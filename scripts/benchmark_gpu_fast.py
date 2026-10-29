"""Same-process baseline/CUDA-graph comparison with existing local weights only."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
parser=argparse.ArgumentParser()
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--skip-baseline',action='store_true')
parser.add_argument('--book',action='store_true')
parser.add_argument('--trim-cache',action='store_true')
args=parser.parse_args()
if args.output.exists():raise SystemExit('Choose a new output directory')
args.output.mkdir(parents=True)
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
attempts=[]
def deny_network(sock,address):
    attempts.append(str(address));raise RuntimeError('Network prohibited during benchmark')
socket.socket.connect=deny_network
import torch
import soundfile as sf
import numpy as np
from client.engine.tts import QwenEngine
from client.engine.books import import_book,prepare_text,spoken_blocks,chunk_text
torch.set_num_threads(6)
model=Path('models/qwen3-tts-0.6b-5d83992436ea')
reference=Path('data/tts-check/reference.wav')
transcript=Path('data/tts-check/reference.txt').read_text(encoding='utf-8-sig')
text='Вечером Анна открыла окно библиотеки. На другом берегу зажглись двенадцать фонарей.'
if args.book:
    book=import_book(Path('samples/night-keeper.fb2'));chapter=book.chapters[0]
    text=prepare_text(spoken_blocks(book,chapter.start,chapter.end),numbers=True,language='ru')
(args.output/'input.txt').write_text(text,encoding='utf-8')
parts=chunk_text(text)
rows=[]
def timed(name,call):
    torch.cuda.synchronize();started=time.perf_counter()
    result=call()
    torch.cuda.synchronize();seconds=time.perf_counter()-started
    print(json.dumps({'stage':name,'seconds':round(seconds,3)}),flush=True)
    return result,seconds
engine,load=timed('load',lambda:QwenEngine(model,'cuda:0'))
prompt,voice_time=timed('voice',lambda:engine.prepare_voice(reference,transcript))
def render(label,generate):
    for i,part in enumerate(parts):
        if args.trim_cache:torch.cuda.empty_cache()
        free,total=torch.cuda.mem_get_info()
        print(json.dumps({'stage':'memory','free_mib':round(free/1024**2),'reserved_mib':round(torch.cuda.memory_reserved()/1024**2)}),flush=True)
        torch.manual_seed(42+i)
        (waves,rate),elapsed=timed(label,lambda:generate(part))
        wave=waves[0]
        if not len(wave) or not np.isfinite(wave).all() or np.max(np.abs(wave))<.0001:raise ValueError('Invalid audio')
        sf.write(args.output/f'{label}-{i}.wav',wave,rate,subtype='PCM_16')
        duration=len(wave)/rate
        row={'backend':label,'index':i,'characters':len(part),'seconds':round(elapsed,3),'audio_seconds':round(duration,3),'rtf':round(elapsed/duration,3)}
        rows.append(row);print(json.dumps(row),flush=True)
        (args.output/'partial.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
if not args.skip_baseline:
    render('baseline',lambda part:engine.model.generate_voice_clone(text=part,language='Russian',voice_clone_prompt=prompt,max_new_tokens=512))
from faster_qwen3_tts import FasterQwen3TTS
from faster_qwen3_tts.predictor_graph import PredictorGraph
from faster_qwen3_tts.talker_graph import TalkerGraph
import faster_qwen3_tts.generate as generation
original_generate=generation.fast_generate
def checked_generate(*a,**kw):
    predictor=kw['predictor_graph'];run=predictor.run;started=time.perf_counter()
    def bounded_run(*positional,**keyword):
        if time.perf_counter()-started>180:raise TimeoutError('Diagnostic generation exceeded 180 seconds')
        return run(*positional,**keyword)
    predictor.run=bounded_run
    try:codes,timing=original_generate(*a,**kw)
    finally:predictor.run=run
    count=0 if codes is None else len(codes)
    if count>=kw['max_new_tokens'] or kw['talker_input_embeds'].shape[1]+count>=kw['talker_graph'].max_seq_len:
        raise RuntimeError('Generation reached its limit; incomplete audio rejected')
    print(json.dumps({'stage':'decode','timing':timing}),flush=True)
    return codes,timing
generation.fast_generate=checked_generate
talker=engine.model.model.talker
config=engine.model.model.config.talker_config
fast=FasterQwen3TTS(engine.model,
    PredictorGraph(talker.code_predictor,talker.code_predictor.model.config,config.hidden_size,device='cuda:0',dtype=torch.bfloat16),
    TalkerGraph(talker.model,config,device='cuda:0',dtype=torch.bfloat16,max_seq_len=1024),
    device='cuda:0',dtype=torch.bfloat16,max_seq_len=1024)
_,warmup=timed('graph_capture',lambda:fast.warmup(prefill_len=100))
if args.trim_cache:torch.cuda.empty_cache()
torch.cuda.reset_peak_memory_stats()
for label in ['graphs_first','graphs_warm']:
    render(label,lambda part:fast.generate_voice_clone(text=part,language='Russian',voice_clone_prompt=prompt,max_new_tokens=512,append_silence=False))
report={'rows':rows,'load_seconds':load,'voice_seconds':voice_time,'graph_capture_seconds':warmup,
    'peak_allocated_mib':round(torch.cuda.max_memory_allocated()/1024**2),'peak_reserved_mib':round(torch.cuda.max_memory_reserved()/1024**2),
    'network_attempts':attempts,'torch':torch.__version__,'trim_cache':args.trim_cache,'faster_qwen3_tts':'0.3.2','reference_kind':'Synthetic Windows SAPI, not a human similarity test'}
(args.output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report),flush=True)
