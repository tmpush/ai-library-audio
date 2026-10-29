"""Developer-only real TTS queue check. Run start, then resume in a NEW process."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
parser=argparse.ArgumentParser()
parser.add_argument('phase',choices=['start','resume'])
args=parser.parse_args()
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
network_attempts=[]
def deny_network(sock,address):
    network_attempts.append(str(address));raise RuntimeError('Offline benchmark')
socket.socket.connect=deny_network
import torch
from num2words import num2words
from client.engine.books import chunk_text
from client.engine.queue import RenderQueue
from client.engine.tts import QwenEngine
from client.engine.export import join_wavs,export_book

root=Path('artifacts/fast-queue-long')
checkpoint=root/'checkpoint.json'
reference=Path('data/tts-check/reference.wav')
transcript=Path('data/tts-check/reference.txt').read_text(encoding='utf-8-sig').strip()
model=Path('models/qwen3-tts-0.6b-5d83992436ea')
torch.manual_seed(42)
if args.phase=='start':
    if root.exists():raise SystemExit('Test directory already exists; preserve previous results')
    root.mkdir(parents=True)
    scenes=[
        'Вечером Анна открыла окно библиотеки. На другом берегу зажглись фонари, и их отражения медленно плыли к мосту.',
        'Утром смотритель принёс новую тетрадь. На её обложке была нарисована лодка, похожая на ту, что стояла у старой пристани.',
        'После дождя дорожка к дому покрылась листьями. Анна услышала шаги на лестнице и отложила книгу, чтобы открыть дверь.',
        'В тихом читальном зале часы пробили полдень. Посетитель попросил карту реки и долго рассматривал узкую дорогу к маяку.',
    ]
    paragraphs=[]
    for i in range(1,41):
        number=num2words(i,lang='ru')
        paragraphs.append(f'В тетради появилась запись номер {number}. {scenes[(i-1)%len(scenes)]} Анна сверила дату с календарём и аккуратно сложила лист. На конверте она написала: письмо номер {number}. Затем положила его в ящик у окна.')
    text='\n\n'.join(paragraphs)
    assert 10000<=len(text)<=15000,len(text)
    (root/'input.txt').write_text(text,encoding='utf-8')
    parts=chunk_text(text)
    queue=RenderQueue(root/'queue')
    spec={'model_revision':'5d83992436eae1d760afd27aff78a71d676296fc','voice_hash':hashlib.sha256(reference.read_bytes()).hexdigest(),
          'language':'ru','backend':'cuda_graphs','engine_revision':'cuda-graphs-0.3.2-v1'}
    job=queue.enqueue(parts,spec)
    saved={'job':job,'characters':len(text),'chunks':len(parts),'start_pid':os.getpid()}
else:
    saved=json.loads(checkpoint.read_text(encoding='utf-8'))
    assert saved['start_pid']!=os.getpid()
    queue=RenderQueue(root/'queue');job=saved['job']
    assert queue.state(job)['completed']==3
    queue.recover();queue.resume(job)

started=time.perf_counter()
engine=QwenEngine(model,'cuda:0',backend='cuda_graphs')
load=time.perf_counter()-started
calls=[]
def render(text,spec,path):
    engine.synthesize(text,'ru',reference,transcript,path)
    calls.append(text)
    print(json.dumps({'phase':args.phase,'generated':len(calls),'characters':len(text)}),flush=True)
    if args.phase=='start' and len(calls)==3:queue.pause(job)

# This is an isolated developer fixture, never the application's license service.
queue.run(job,render,lambda:None)
state=queue.state(job)
files=[queue.cache/(c['cache_key']+'.wav') for c in state['chunks'] if c['done']]
if args.phase=='start':
    assert state['status']=='paused' and state['completed']==3
    saved['first_hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    saved['first_load_seconds']=load
    checkpoint.write_text(json.dumps(saved,indent=2),encoding='utf-8')
else:
    assert state['status']=='done' and len(calls)==state['total']-3
    for name,digest in saved['first_hashes'].items():
        assert hashlib.sha256((queue.cache/name).read_bytes()).hexdigest()==digest
    join_wavs(files,root/'complete.wav')
    outputs=export_book([('Контрольная глава',files)],root/'export','Проверка длинной озвучки','Тестовый текст')
    compute=sum(c['seconds'] for c in state['chunks']);audio=sum(c['audio_seconds'] for c in state['chunks'])
    report={**saved,'status':state['status'],'resume_pid':os.getpid(),'resume_load_seconds':load,
        'generated_after_restart':len(calls),'preserved_chunks':3,'compute_seconds':round(compute,3),
        'audio_seconds':round(audio,3),'rtf':round(compute/audio,4),'network_attempts':network_attempts,
        'exports':[str(p) for p in outputs],'reference_kind':'Synthetic Windows SAPI; listening quality not evaluated'}
    (root/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)
print(json.dumps({'phase':args.phase,'status':state['status'],'completed':state['completed'],'total':state['total']}),flush=True)
