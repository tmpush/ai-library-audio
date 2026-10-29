"""Local Chatterbox V3 comparison against the saved unmarked Qwen phrases."""
import argparse,json,os,random,sys,time,wave,hashlib,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
parser=argparse.ArgumentParser();parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--model',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ['PATH']=str(ROOT/'build/ffmpeg-runtime')+os.pathsep+os.environ['PATH']
from client.engine.export import encode,ffmpeg_path,join_wavs
from client.engine.denoise import reduce_noise
import numpy as np
import torch
import soundfile as sf
from chatterbox.mtl_tts import ChatterboxMultilingualTTS
manifest=json.loads(args.manifest.read_text(encoding='utf-8'));args.output.mkdir(parents=True,exist_ok=True)
if (args.output/'comparison.json').exists():raise ValueError('Comparison already exists')
torch.set_num_threads(6)
load_start=time.monotonic();model=ChatterboxMultilingualTTS.from_local(str(args.model),device='cuda',t3_model='v3')
model.prepare_conditionals(manifest['reference'],exaggeration=0.5)
print(json.dumps({'state':'loaded','seconds':time.monotonic()-load_start}),flush=True)
rows=[];parts=[]
for phrase in manifest['phrases']:
    n=phrase['phrase'];seed=phrase['seed'];random.seed(seed);np.random.seed(seed);torch.manual_seed(seed);torch.cuda.manual_seed_all(seed)
    start=time.monotonic();torch.cuda.reset_peak_memory_stats()
    audio=model.generate(phrase['text'],language_id='ru',exaggeration=0.5,cfg_weight=0.5,temperature=0.8)
    torch.cuda.synchronize();elapsed=time.monotonic()-start
    raw=args.output/f'{n:02d}-raw.wav';clean=args.output/f'{n:02d}-Chatterbox.wav'
    sf.write(str(raw),audio.squeeze().detach().cpu().numpy(),model.sr,subtype='PCM_16')
    reduce_noise(raw,clean);parts.append(clean)
    mp3=clean.with_suffix('.mp3');encode([ffmpeg_path(),'-v','error','-nostdin','-n','-i',str(clean),'-c:a','libmp3lame','-b:a','128k',str(mp3)])
    row={'phrase':n,'text':phrase['text'],'seed':seed,'file':str(mp3),'audio_seconds':audio.shape[-1]/model.sr,'synthesis_seconds':elapsed,'peak_cuda_bytes':torch.cuda.max_memory_allocated()}
    rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
silence=args.output/'pause.wav'
with wave.open(str(silence),'wb') as w:w.setparams((1,2,model.sr,0,'NONE','not compressed'));w.writeframes(b'\0\0'*model.sr)
combined=[]
for p in parts:
    if combined:combined.append(silence)
    combined.append(p)
merged=args.output/'Chatterbox.wav';join_wavs(combined,merged)
mp3=args.output/'Chatterbox V3 — без пометок.mp3';encode([ffmpeg_path(),'-v','error','-nostdin','-n','-i',str(merged),'-c:a','libmp3lame','-b:a','128k',str(mp3)])
encode([ffmpeg_path(),'-v','error','-i',str(mp3),'-f','null','-'])
shutil.copy2(Path(manifest['qwen_comparison'])/'A — без пометок.mp3',args.output/'Qwen — без пометок.mp3')
paired=[]
for phrase,chatter in zip(manifest['phrases'],parts):
    qwen=Path(phrase['file']).with_suffix('.wav')
    if paired:paired.append(silence)
    paired.extend([qwen,silence,chatter])
joined=args.output/'Попарное сравнение.wav';join_wavs(paired,joined)
encode([ffmpeg_path(),'-v','error','-nostdin','-n','-i',str(joined),'-c:a','libmp3lame','-b:a','128k',str(args.output/'Попарно — Qwen затем Chatterbox.mp3')])
# One explicit acute mark, kept separate from the unmarked four-phrase baseline.
seed=manifest['phrases'][0]['seed'];random.seed(seed);np.random.seed(seed);torch.manual_seed(seed);torch.cuda.manual_seed_all(seed)
marked=manifest['phrases'][0]['text'].replace('мосту.', 'мосту́.')
audio=model.generate(marked,language_id='ru',exaggeration=0.5,cfg_weight=0.5,temperature=0.8)
raw=args.output/'мосту-marked-raw.wav';clean=args.output/'мосту-marked.wav'
sf.write(str(raw),audio.squeeze().detach().cpu().numpy(),model.sr,subtype='PCM_16');reduce_noise(raw,clean)
encode([ffmpeg_path(),'-v','error','-nostdin','-n','-i',str(clean),'-c:a','libmp3lame','-b:a','128k',str(args.output/'Chatterbox — точечное ударение мосту.mp3')])
report={'engine':'Chatterbox Multilingual V3','model_revision':manifest['model_revision'],'voice_name':manifest['voice_name'],'voice_hash':manifest['voice_hash'],'reference_sha256':hashlib.sha256(Path(manifest['reference']).read_bytes()).hexdigest(),'settings':{'exaggeration':0.5,'cfg_weight':0.5,'temperature':0.8,'language':'ru','denoise':True},'phrases':rows,'output':str(mp3),'total_seconds':time.monotonic()-load_start}
(args.output/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
