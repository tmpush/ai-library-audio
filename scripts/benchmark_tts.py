"""Developer benchmark of a real local model. Does not change application license checks."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import socket
import sys
import time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from client.engine.books import chunk_text,import_book,prepare_text,spoken_blocks
from client.engine.tts import QwenEngine
from client.engine.export import join_wavs
from client.engine.voices import inspect_voice

parser=argparse.ArgumentParser()
parser.add_argument('--model',type=Path,required=True)
parser.add_argument('--reference',type=Path,required=True)
parser.add_argument('--transcript',type=Path,required=True)
parser.add_argument('--device',choices=['cpu','cuda:0'],required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--text-file',type=Path)
parser.add_argument('--book',type=Path)
parser.add_argument('--threads',type=int,default=8)
parser.add_argument('--backend',choices=['standard','cuda_graphs'],default='standard')
parser.add_argument('--timeout-seconds',type=float,default=600,help='Maximum generation time per chunk; timeout fails the check')
parser.add_argument('--reference-label',default='provided local WAV; voice similarity not evaluated')
args=parser.parse_args()
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
network_attempts=[]
def refuse_network(sock,address):
    network_attempts.append(str(address))
    raise RuntimeError('Network access is forbidden during this local TTS benchmark')
socket.socket.connect=refuse_network
import numpy as np
import soundfile as sf
import torch
torch.set_num_threads(args.threads)
torch.manual_seed(42)
if args.device.startswith('cuda') and not torch.cuda.is_available():raise SystemExit('CUDA is unavailable')
voice=inspect_voice(args.reference)
if args.book:
    book=import_book(args.book);chapter=book.chapters[0]
    text=prepare_text(spoken_blocks(book,chapter.start,chapter.end),numbers=True,language='ru')
else:
    text=args.text_file.read_text(encoding='utf-8-sig').strip() if args.text_file else 'Вечером Анна открыла окно библиотеки. На другом берегу зажглись двенадцать фонарей.'
if args.output.exists() and any(args.output.iterdir()):raise SystemExit('Choose a new output directory')
args.output.mkdir(parents=True,exist_ok=True)
(args.output/'input.txt').write_text(text,encoding='utf-8')
def sync():
    if args.device.startswith('cuda'):torch.cuda.synchronize()
sync();start=time.perf_counter()
engine=QwenEngine(args.model,args.device,backend=args.backend)
from transformers import StoppingCriteria
class Progress(StoppingCriteria):
    def __init__(self):
        self.started=self.last=time.perf_counter()
    def __call__(self,input_ids,scores,**kwargs):
        now=time.perf_counter()
        if now-self.started>args.timeout_seconds:
            raise TimeoutError('The diagnostic generation exceeded its time limit; no truncated audio is accepted')
        if now-self.last>=30:
            print(json.dumps({'stage':'generating','tokens':input_ids.shape[1],'seconds':round(now-self.started,2)}),flush=True)
            self.last=now
        return False
generate=engine.model.model.talker.generate
def measured_generate(*positional,**keyword):
    keyword['stopping_criteria']=[Progress()]
    return generate(*positional,**keyword)
engine.model.model.talker.generate=measured_generate
sync();load_seconds=time.perf_counter()-start
print(json.dumps({'stage':'loaded','seconds':load_seconds,'device':args.device}),flush=True)
if args.device.startswith('cuda'):torch.cuda.reset_peak_memory_stats()
transcript=args.transcript.read_text(encoding='utf-8-sig').strip()
sync();start=time.perf_counter()
engine.prepare_voice(args.reference,transcript)
sync();voice_seconds=time.perf_counter()-start
print(json.dumps({'stage':'voice_prepared','seconds':voice_seconds}),flush=True)
parts=chunk_text(text)
rows=[];paths=[]
for index,part in enumerate(parts):
    path=args.output/f'{index:04d}.wav'
    sync();start=time.perf_counter()
    engine.synthesize(part,'ru',args.reference,transcript,path)
    sync();seconds=time.perf_counter()-start
    audio,rate=sf.read(path)
    if not len(audio) or not np.isfinite(audio).all() or np.max(np.abs(audio))<.0001:raise RuntimeError('Empty, silent or non-finite audio')
    duration=len(audio)/rate
    row={'index':index,'characters':len(part),'seconds':round(seconds,3),'audio_seconds':round(duration,3),'rtf':round(seconds/duration,3),'sample_rate':rate}
    rows.append(row);paths.append(path)
    print(json.dumps(row),flush=True)
join_wavs(paths,args.output/'complete.wav')
report={'device':args.device,'backend':args.backend,'gpu':torch.cuda.get_device_name(0) if args.device.startswith('cuda') else None,
        'platform':platform.platform(),'python':platform.python_version(),
        'threads':torch.get_num_threads(),'torch':torch.__version__,'qwen_tts':importlib.metadata.version('qwen-tts'),
        'non_streaming_mode':False,
        'chunk_timeout_seconds':args.timeout_seconds,
        'transformers':importlib.metadata.version('transformers'),
        'model_directory':str(args.model.resolve()),'reference_kind':args.reference_label,
        'reference_sha256':hashlib.sha256(args.reference.read_bytes()).hexdigest(),
        'reference_seconds':voice['duration'],'characters':len(text),'load_seconds':round(load_seconds,3),
        'voice_preparation_seconds':round(voice_seconds,3),
        'network_attempts':network_attempts,
        'synthesis_seconds':round(sum(row['seconds'] for row in rows),3),
        'audio_seconds':round(sum(row['audio_seconds'] for row in rows),3),'chunks':rows,
        'peak_vram_mib':round(torch.cuda.max_memory_allocated()/1024**2) if args.device.startswith('cuda') else None}
report['rtf']=round(report['synthesis_seconds']/report['audio_seconds'],3)
(args.output/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True),flush=True)
