use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use std::{
    io::Cursor,
    path::PathBuf,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

pub const SCRIPT: &str = "Сегодня я читаю своим голосом. В библиотеке тихо. За окном шумят деревья. Я рассказываю эту историю спокойно и ясно.";
pub fn microphones() -> Vec<String> {
    cpal::default_host()
        .input_devices()
        .map(|items| items.filter_map(|d| d.name().ok()).collect())
        .unwrap_or_default()
}
pub struct Recording {
    stream: Option<cpal::Stream>,
    samples: Arc<Mutex<Vec<f32>>>,
    pub error: Arc<Mutex<Option<String>>>,
    rate: u32,
    channels: usize,
    pub started: Instant,
}
impl Recording {
    pub fn start(name: &str) -> Result<Self, String> {
        let host = cpal::default_host();
        let device = if name.is_empty() {
            host.default_input_device()
        } else {
            host.input_devices()
                .ok()
                .and_then(|mut d| d.find(|d| d.name().ok().as_deref() == Some(name)))
        }
        .ok_or("Микрофон не найден. Подключите его и проверьте разрешения системы")?;
        let supported = device.default_input_config().map_err(|e| e.to_string())?;
        let rate = supported.sample_rate().0;
        let channels = supported.channels() as usize;
        let limit = rate as usize * channels * 12;
        let samples = Arc::new(Mutex::new(Vec::with_capacity(limit)));
        let error = Arc::new(Mutex::new(None));
        let errors = error.clone();
        let on_error = move |e: cpal::StreamError| {
            *errors.lock().unwrap() = Some(format!("Ошибка микрофона: {e}"));
        };
        let buffer = samples.clone();
        let config = supported.config();
        let stream = match supported.sample_format() {
            cpal::SampleFormat::F32 => device.build_input_stream(
                &config,
                move |data: &[f32], _| {
                    let mut s = buffer.lock().unwrap();
                    let n = data.len().min(limit.saturating_sub(s.len()));
                    s.extend_from_slice(&data[..n]);
                },
                on_error,
                None,
            ),
            cpal::SampleFormat::I16 => device.build_input_stream(
                &config,
                move |data: &[i16], _| {
                    let mut s = buffer.lock().unwrap();
                    let n = data.len().min(limit.saturating_sub(s.len()));
                    s.extend(data[..n].iter().map(|v| *v as f32 / 32768.0));
                },
                on_error,
                None,
            ),
            cpal::SampleFormat::U16 => device.build_input_stream(
                &config,
                move |data: &[u16], _| {
                    let mut s = buffer.lock().unwrap();
                    let n = data.len().min(limit.saturating_sub(s.len()));
                    s.extend(data[..n].iter().map(|v| (*v as f32 - 32768.0) / 32768.0));
                },
                on_error,
                None,
            ),
            _ => {
                return Err(
                    "Этот формат микрофона не поддерживается. Выберите другой аудиовход".into(),
                )
            }
        }
        .map_err(|e| format!("Не удалось включить микрофон: {e}"))?;
        stream.play().map_err(|e| e.to_string())?;
        Ok(Self {
            stream: Some(stream),
            samples,
            error,
            rate,
            channels,
            started: Instant::now(),
        })
    }
    pub fn level(&self) -> f32 {
        let samples = self.samples.lock().unwrap();
        let n = samples.len().min(2048);
        if n == 0 {
            return 0.0;
        }
        (samples[samples.len() - n..]
            .iter()
            .map(|s| s * s)
            .sum::<f32>()
            / n as f32)
            .sqrt()
    }
    pub fn finish(mut self) -> Result<Vec<u8>, String> {
        self.stream.take();
        if let Some(error) = self.error.lock().unwrap().clone() {
            return Err(error);
        }
        let samples = self.samples.lock().unwrap();
        let mono: Vec<f32> = samples
            .chunks_exact(self.channels)
            .map(|frame| frame.iter().sum::<f32>() / self.channels as f32)
            .collect();
        encode_voice(&mono, self.rate)
    }
}
pub fn encode_voice(mono: &[f32], rate: u32) -> Result<Vec<u8>, String> {
    if rate == 0 || mono.len() < rate as usize * 10 {
        return Err("Запись короче 10 секунд. Прочитайте весь текст ещё раз".into());
    }
    let length = ((mono.len() as u64 * 24000 / rate as u64) as usize).min(720000);
    let mut cursor = Cursor::new(Vec::new());
    {
        let mut wav = hound::WavWriter::new(
            &mut cursor,
            hound::WavSpec {
                channels: 1,
                sample_rate: 24000,
                bits_per_sample: 16,
                sample_format: hound::SampleFormat::Int,
            },
        )
        .map_err(|e| e.to_string())?;
        for i in 0..length {
            let at = i as f64 * rate as f64 / 24000.0;
            let lo = at as usize;
            let f = (at - lo as f64) as f32;
            let sample = mono[lo] * (1.0 - f) + mono[(lo + 1).min(mono.len() - 1)] * f;
            wav.write_sample((sample.clamp(-1.0, 1.0) * 32767.0) as i16)
                .map_err(|e| e.to_string())?;
        }
        wav.finalize().map_err(|e| e.to_string())?;
    }
    Ok(cursor.into_inner())
}
// A single seekable source spans the generated fragments without loading a book into RAM.
#[derive(Clone)]
enum AudioInput { File(PathBuf), Memory(Arc<[u8]>) }
impl AudioInput {
    fn open(&self) -> Result<Box<dyn rodio::Source + Send>, String> {
        match self {
            Self::File(path) => Ok(Box::new(rodio::Decoder::try_from(std::fs::File::open(path).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?)),
            Self::Memory(bytes) => Ok(Box::new(rodio::Decoder::try_from(Cursor::new(bytes.clone())).map_err(|e| e.to_string())?)),
        }
    }
}
#[derive(Clone)]
struct AudioTrack { input: AudioInput, start: f64, duration: f64 }
struct BookSource {
    tracks: Vec<AudioTrack>,
    index: usize,
    decoder: Box<dyn rodio::Source + Send>,
    samples: u64,
    offset: f64,
    position: Arc<std::sync::atomic::AtomicU64>,
    error: Arc<Mutex<Option<String>>>,
}
fn locate_track(tracks: &[AudioTrack], seconds: f64) -> usize {
    tracks.partition_point(|t| t.start <= seconds).saturating_sub(1).min(tracks.len().saturating_sub(1))
}
impl BookSource {
    fn new(tracks: Vec<AudioTrack>, seconds: f64, position: Arc<std::sync::atomic::AtomicU64>, error: Arc<Mutex<Option<String>>>) -> Result<Self,String> {
        let index=locate_track(&tracks,seconds);
        let mut decoder=tracks[index].input.open()?;
        let offset=(seconds-tracks[index].start).max(0.0).min(tracks[index].duration);
        if offset>0.0 { decoder.try_seek(Duration::from_secs_f64(offset)).map_err(|e| e.to_string())?; }
        position.store((seconds*1000.0) as u64,std::sync::atomic::Ordering::Relaxed);
        Ok(Self {tracks,index,decoder,samples:0,offset,position,error})
    }
}
impl Iterator for BookSource {
    type Item=f32;
    fn next(&mut self)->Option<f32> {
        loop {
            if let Some(value)=self.decoder.next() {
                self.samples+=1;
                let seconds=self.tracks[self.index].start+self.offset+self.samples as f64/self.decoder.sample_rate() as f64/self.decoder.channels() as f64;
                self.position.store((seconds*1000.0) as u64,std::sync::atomic::Ordering::Relaxed);
                return Some(value);
            }
            if self.index+1>=self.tracks.len() {return None;}
            self.index+=1; self.samples=0; self.offset=0.0;
            match self.tracks[self.index].input.open() {
                Ok(decoder)=>self.decoder=decoder,
                Err(e)=>{*self.error.lock().unwrap()=Some(e);return None;}
            }
        }
    }
}
impl rodio::Source for BookSource {
    fn current_span_len(&self)->Option<usize> { self.decoder.current_span_len() }
    fn channels(&self)->u16 {self.decoder.channels()}
    fn sample_rate(&self)->u32 {self.decoder.sample_rate()}
    fn total_duration(&self)->Option<Duration> {self.tracks.last().map(|t|Duration::from_secs_f64(t.start+t.duration))}
}
pub struct Player {
    stream: rodio::OutputStream,
    pub sink: rodio::Sink,
    tracks: Vec<AudioTrack>,
    position: Arc<std::sync::atomic::AtomicU64>,
    error: Arc<Mutex<Option<String>>>,
    pub label: String,
    pub volume: f32,
    pub speed: f32,
    pub muted: bool,
    pub scrub: Option<f64>,
}
impl Player {
    pub fn files(files: Vec<PathBuf>, label: String) -> Result<Self, String> {
        Self::inputs(files.into_iter().map(AudioInput::File).collect(),label)
    }
    pub fn bytes(bytes: Vec<u8>) -> Result<Self, String> {
        Self::inputs(vec![AudioInput::Memory(bytes.into())],"Запись вашего голоса".into())
    }
    fn inputs(inputs: Vec<AudioInput>,label:String)->Result<Self,String> {
        if inputs.is_empty(){return Err("В записи пока нет звука".into());}
        let mut tracks=Vec::new(); let mut start=0.0; let mut format=None;
        for input in inputs {
            let decoder=input.open()?;
            let spec=(decoder.channels(),decoder.sample_rate());
            if format.is_some_and(|f|f!=spec){return Err("Фрагменты записи имеют разные аудиоформаты. Сохраните запись одним MP3.".into());}
            format=Some(spec);
            let duration=decoder.total_duration().ok_or("Не удалось определить длительность записи")?.as_secs_f64();
            tracks.push(AudioTrack{input,start,duration}); start+=duration;
        }
        let stream=rodio::OutputStreamBuilder::open_default_stream().map_err(|e|format!("Устройство воспроизведения недоступно: {e}"))?;
        let sink=rodio::Sink::connect_new(stream.mixer());
        let position=Arc::new(std::sync::atomic::AtomicU64::new(0));
        let error=Arc::new(Mutex::new(None));
        sink.append(BookSource::new(tracks.clone(),0.0,position.clone(),error.clone())?);
        Ok(Self{stream,sink,tracks,position,error,label,volume:1.0,speed:1.0,muted:false,scrub:None})
    }
    pub fn duration(&self)->f64 {self.tracks.last().map(|t|t.start+t.duration).unwrap_or(0.0)}
    pub fn position(&self)->f64 {if self.finished(){self.duration()}else{(self.position.load(std::sync::atomic::Ordering::Relaxed) as f64/1000.0).min(self.duration())}}
    pub fn seek(&mut self,seconds:f64)->Result<(),String> {
        let seconds=seconds.clamp(0.0,self.duration());
        let position=Arc::new(std::sync::atomic::AtomicU64::new(0));
        let source=BookSource::new(self.tracks.clone(),seconds,position.clone(),self.error.clone())?;
        let paused=self.sink.is_paused();
        self.sink.stop();
        self.position=position;
        self.sink=rodio::Sink::connect_new(self.stream.mixer());
        self.sink.set_volume(if self.muted{0.0}else{self.volume}); self.sink.set_speed(self.speed);
        if paused{self.sink.pause();}
        self.sink.append(source);
        Ok(())
    }
    pub fn toggle(&mut self)->Result<(),String> {
        if self.finished(){self.seek(0.0)?;self.sink.play();}
        else if self.sink.is_paused(){self.sink.play();}else{self.sink.pause();}
        Ok(())
    }
    pub fn tick(&mut self)->Result<(),String> {
        self.sink.set_volume(if self.muted{0.0}else{self.volume});self.sink.set_speed(self.speed);
        if let Some(e)=self.error.lock().unwrap().take(){return Err(e);} Ok(())
    }
    pub fn finished(&self)->bool {self.sink.empty()}
}

#[cfg(test)]
mod tests {
    fn test_audio(value: i16) -> super::AudioInput {
        let mut cursor=std::io::Cursor::new(Vec::new());
        let spec=hound::WavSpec{channels:1,sample_rate:24000,bits_per_sample:16,sample_format:hound::SampleFormat::Int};
        {let mut wav=hound::WavWriter::new(&mut cursor,spec).unwrap();for _ in 0..24000{wav.write_sample(value).unwrap();}wav.finalize().unwrap();}
        super::AudioInput::Memory(cursor.into_inner().into())
    }
    #[test]
    fn book_source_seeks_across_fragments_and_streams_to_end() {
        let tracks=vec![super::AudioTrack{input:test_audio(3000),start:0.0,duration:1.0},super::AudioTrack{input:test_audio(15000),start:1.0,duration:1.0}];
        assert_eq!(super::locate_track(&tracks,0.999),0);
        assert_eq!(super::locate_track(&tracks,1.0),1);
        let pos=std::sync::Arc::new(std::sync::atomic::AtomicU64::new(0));
        let error=std::sync::Arc::new(std::sync::Mutex::new(None));
        let mut source=super::BookSource::new(tracks.clone(),0.5,pos.clone(),error.clone()).unwrap();
        assert!(source.next().unwrap()<0.2);
        for _ in 1..12000 {source.next().unwrap();}
        assert!(source.next().unwrap()>0.4);
        assert_eq!(source.count(),23999);
        assert_eq!(pos.load(std::sync::atomic::Ordering::Relaxed),2000);
        let mut seeked=super::BookSource::new(tracks.clone(),1.25,pos.clone(),error.clone()).unwrap();
        assert!(seeked.next().unwrap()>0.4);
        assert_eq!(seeked.count(),17999);
        let restarted=super::BookSource::new(tracks,0.0,pos,error.clone()).unwrap();
        assert_eq!(restarted.count(),48000);
        assert!(error.lock().unwrap().is_none());
    }
    #[test]
    fn recording_converts_to_voice_library_contract() {
        let samples: Vec<f32> = (0..48000 * 12)
            .map(|i| (i as f32 * 0.02).sin() * 0.2)
            .collect();
        let bytes = super::encode_voice(&samples, 48000).unwrap();
        let wav = hound::WavReader::new(std::io::Cursor::new(bytes)).unwrap();
        assert_eq!(wav.spec().sample_rate, 24000);
        assert_eq!(wav.spec().channels, 1);
        assert_eq!(wav.duration(), 24000 * 12);
        assert_eq!(wav.spec().bits_per_sample, 16);
        assert!(super::encode_voice(&samples[..100], 48000).is_err());
    }
}
