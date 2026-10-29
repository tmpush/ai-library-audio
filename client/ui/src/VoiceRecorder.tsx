import {useEffect,useRef,useState} from 'react';

export const REFERENCE_TEXT='Сегодня я читаю небольшую историю своим обычным голосом. За окном тихо шумят деревья, а на столе лежит раскрытая книга. Я делаю короткую паузу и продолжаю рассказ спокойно и ясно. Впереди новый день, интересная встреча и дорога к старому маяку.';

export async function recordingToWav(recording:Blob){
 const decoded=await new OfflineAudioContext(1,1,24000).decodeAudioData(await recording.arrayBuffer());
 const frames=Math.min(decoded.length,24000*30),duration=frames/24000;
 if(duration<10)throw Error('Запись короче 10 секунд. Прочитайте весь текст спокойно и запишите ещё раз.');
 const buffer=new ArrayBuffer(44+frames*2),view=new DataView(buffer);
 const text=(offset:number,value:string)=>{for(let i=0;i<value.length;i++)view.setUint8(offset+i,value.charCodeAt(i))};
 text(0,'RIFF');view.setUint32(4,36+frames*2,true);text(8,'WAVE');text(12,'fmt ');
 view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);
 view.setUint32(24,24000,true);view.setUint32(28,48000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);
 text(36,'data');view.setUint32(40,frames*2,true);
 const channels=Array.from({length:decoded.numberOfChannels},(_,i)=>decoded.getChannelData(i));
 let energy=0;
 for(let i=0;i<frames;i++){
  const value=Math.max(-1,Math.min(1,channels.reduce((sum,ch)=>sum+ch[i],0)/channels.length));
  energy+=value*value;view.setInt16(44+i*2,Math.round(value*(value<0?32768:32767)),true);
 }
 if(Math.sqrt(energy/frames)<.003)throw Error('Голос почти не слышен. Проверьте микрофон и запишите ещё раз.');
 return {file:new File([buffer],'my-voice.wav',{type:'audio/wav'}),duration};
}

type Capture={recorder:MediaRecorder;stream:MediaStream;timer:number;aborted:boolean};
export function VoiceRecorder({save}:{save:(file:File,name:string,transcript:string)=>Promise<void>}){
 const [phase,setPhase]=useState<'idle'|'permission'|'recording'|'processing'|'saving'>('idle');
 const [seconds,setSeconds]=useState(0),[name,setName]=useState('Мой голос'),[error,setError]=useState(''),[saved,setSaved]=useState(false);
 const [preview,setPreview]=useState<{file:File;url:string;duration:number}|null>(null);
 const active=useRef<Capture|null>(null),epoch=useRef(0),previewUrl=useRef('');
 const dispose=(capture:Capture)=>{window.clearInterval(capture.timer);capture.stream.getTracks().forEach(track=>track.stop());if(active.current===capture)active.current=null};
 const stop=()=>{const capture=active.current;if(capture){window.clearInterval(capture.timer);if(capture.recorder.state!=='inactive')capture.recorder.stop();capture.stream.getTracks().forEach(track=>track.stop())}};
 const cancel=()=>{epoch.current++;const capture=active.current;if(capture){capture.aborted=true;stop();dispose(capture)}setPhase('idle');setSeconds(0)};
 useEffect(()=>{
  const hidden=()=>{if(document.hidden)stop()};document.addEventListener('visibilitychange',hidden);
  return()=>{epoch.current++;document.removeEventListener('visibilitychange',hidden);const capture=active.current;if(capture){capture.aborted=true;stop();dispose(capture)}if(previewUrl.current)URL.revokeObjectURL(previewUrl.current)};
 },[]);
 async function start(){
  const attempt=++epoch.current;setError('');setSaved(false);setPhase('permission');let stream:MediaStream|undefined;
  try{
   if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined')throw Error('Запись с микрофона недоступна в этой среде. Можно загрузить готовый WAV ниже.');
   stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:false,noiseSuppression:false,autoGainControl:false},video:false});
   if(epoch.current!==attempt){stream.getTracks().forEach(track=>track.stop());return}
   const mime=['audio/webm;codecs=opus','audio/webm','audio/mp4'].find(type=>MediaRecorder.isTypeSupported(type));
   const recorder=new MediaRecorder(stream,mime?{mimeType:mime}:undefined),parts:Blob[]=[];
   const capture:Capture={recorder,stream,timer:0,aborted:false};active.current=capture;
   recorder.ondataavailable=e=>{if(e.data.size)parts.push(e.data)};
   recorder.onerror=()=>{capture.aborted=true;dispose(capture);if(epoch.current===attempt){setError('Запись прервалась. Проверьте микрофон и повторите.');setPhase('idle')}};
   recorder.onstop=()=>{dispose(capture);if(capture.aborted||epoch.current!==attempt)return;setPhase('processing');void recordingToWav(new Blob(parts,{type:recorder.mimeType})).then(result=>{
    if(epoch.current!==attempt)return;if(previewUrl.current)URL.revokeObjectURL(previewUrl.current);
    const url=URL.createObjectURL(result.file);previewUrl.current=url;setPreview({...result,url});setSeconds(result.duration);setPhase('idle');
   }).catch(err=>{if(epoch.current===attempt){setError(String(err.message||err));setPhase('idle')}})};
   recorder.start(250);const started=performance.now();setSeconds(0);setPhase('recording');setPreview(null);
   if(previewUrl.current){URL.revokeObjectURL(previewUrl.current);previewUrl.current=''}
   capture.timer=window.setInterval(()=>{const elapsed=(performance.now()-started)/1000;setSeconds(Math.min(30,elapsed));if(elapsed>=30)stop()},100);
  }catch(err){stream?.getTracks().forEach(track=>track.stop());const capture=active.current;if(capture&&capture.stream===stream)dispose(capture);if(epoch.current!==attempt)return;
   const e=err as Error;setError(e.name==='NotAllowedError'?'Доступ к микрофону не разрешён. Разрешите его для приложения и повторите запись.':e.name==='NotFoundError'?'Микрофон не найден. Подключите его и повторите.':e.name==='NotReadableError'?'Не удалось открыть микрофон. Возможно, он занят другой программой.':e.message);setPhase('idle');
  }
 }
 async function saveRecording(){if(!preview||!name.trim())return;setError('');setPhase('saving');try{await save(preview.file,name.trim(),REFERENCE_TEXT);setSaved(true)}catch(err){setError(String((err as Error).message||err))}finally{setPhase('idle')}}
 return <details className="voice-recorder" onToggle={e=>{if(!e.currentTarget.open&&phase!=='saving')cancel()}}><summary>Записать мой голос с микрофона</summary>
  <p>Прочитайте весь текст обычным голосом за 15–25 секунд. Записывайте в тихом месте, без музыки, примерно в 15–20 см от микрофона.</p>
  <blockquote className="recording-text">{REFERENCE_TEXT}</blockquote>
  <div className="recording-actions"><button type="button" onClick={()=>void start()} disabled={phase!=='idle'}>{preview?'Записать заново':'Начать запись'}</button>
   {phase==='recording'&&<button type="button" onClick={stop}>Остановить запись</button>}{phase==='permission'&&<button type="button" onClick={cancel}>Отменить запрос</button>}
   <span role="status">{phase==='permission'?'Ожидаем разрешения на микрофон…':phase==='processing'?'Подготавливаем запись…':phase==='saving'?'Сохраняем голос…':phase==='recording'?`● Запись: ${seconds.toFixed(1)} / 30 сек`:preview?`Записано ${preview.duration.toFixed(1)} сек`:'Микрофон выключен'}</span>
  </div>
  <small>Минимум 10 секунд. На 30-й секунде запись остановится. Если не успели дочитать, запишите заново. При закрытии студии микрофон выключается. Запись хранится на этом компьютере.</small>
  {preview&&<div className="recording-result"><audio controls src={preview.url} aria-label="Моя запись"/><label>Имя записанного голоса<input value={name} onChange={e=>{setName(e.target.value);setSaved(false)}} disabled={phase==='saving'} maxLength={100}/></label><button type="button" disabled={phase!=='idle'||!name.trim()||saved} onClick={()=>void saveRecording()}>Сохранить мой голос</button><small>Сохраняйте только если прочитали весь текст без пропусков: он используется как расшифровка образца.</small></div>}
  {error&&<p role="alert" className="error">{error}</p>}{saved&&<p role="status">Голос сохранён и выбран для озвучки.</p>}
 </details>;
}
