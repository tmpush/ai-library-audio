use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use std::{
    collections::VecDeque,
    io::Cursor,
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Instant,
};

pub const SCRIPT: &str = "Сегодня я читаю небольшую историю своим обычным голосом. За окном тихо шумят деревья, а на столе лежит раскрытая книга. Я делаю короткую паузу и продолжаю рассказ спокойно и ясно. Впереди новый день, интересная встреча и дорога к старому маяку.";
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
        let limit = rate as usize * channels * 30;
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
pub struct Player {
    _stream: rodio::OutputStream,
    pub sink: rodio::Sink,
    remaining: VecDeque<PathBuf>,
    pub label: String,
}
impl Player {
    pub fn files(files: Vec<PathBuf>, label: String) -> Result<Self, String> {
        let stream = rodio::OutputStreamBuilder::open_default_stream()
            .map_err(|e| format!("Устройство воспроизведения недоступно: {e}"))?;
        let sink = rodio::Sink::connect_new(stream.mixer());
        let mut player = Self {
            _stream: stream,
            sink,
            remaining: files.into(),
            label,
        };
        player.tick()?;
        Ok(player)
    }
    pub fn bytes(bytes: Vec<u8>) -> Result<Self, String> {
        let stream =
            rodio::OutputStreamBuilder::open_default_stream().map_err(|e| e.to_string())?;
        let sink = rodio::Sink::connect_new(stream.mixer());
        sink.append(rodio::Decoder::try_from(Cursor::new(bytes)).map_err(|e| e.to_string())?);
        Ok(Self {
            _stream: stream,
            sink,
            remaining: VecDeque::new(),
            label: "Запись вашего голоса".into(),
        })
    }
    pub fn tick(&mut self) -> Result<(), String> {
        if self.sink.len() < 2 {
            if let Some(path) = self.remaining.pop_front() {
                self.sink.append(
                    rodio::Decoder::try_from(std::fs::File::open(path).map_err(|e| e.to_string())?)
                        .map_err(|e| e.to_string())?,
                );
            }
        }
        Ok(())
    }
    pub fn finished(&self) -> bool {
        self.sink.empty() && self.remaining.is_empty()
    }
}

#[cfg(test)]
mod tests {
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
