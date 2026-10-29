"""A/B check with the owner's existing voice and normal license/model checks."""
import argparse
import json
import os
import random
import sys
import time
import wave
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from client.engine.service import Studio
from client.engine.storage import Library
from client.engine.export import join_wavs,encode,ffmpeg_path

parser=argparse.ArgumentParser()
parser.add_argument('--voice-id',required=True)
parser.add_argument('--output-dir',type=Path)
args=parser.parse_args()
data=Path(os.environ['APPDATA'])/'app.localbooks.studio'
os.environ['BOOKS_LICENSE_URL']='http://127.0.0.1:8791'
os.environ['BOOKS_LICENSE_PUBLIC_KEY']=(Path(os.environ['APPDATA'])/'LocalBooksLicenseServer/public-key.txt').read_text().strip()
studio=Studio(data,Library(data))
studio.license().authorize()
reference,voice=studio.voices.load(args.voice_id)
spec={**studio.settings,'voice_id':args.voice_id,'voice_hash':voice['sha256'],'transcript':voice['transcript'],'language':'ru','denoise':True,'auto_stress':False}
studio.verify_model(spec)
if any(j['status'] in {'running','queued'} for j in studio.queue.summaries()):raise RuntimeError('Finish or pause existing jobs first')
output=args.output_dir or Path.home()/'Documents/Аудиокниги/Ночной смотритель/Сравнение ударений'
output.mkdir(parents=True,exist_ok=True)
if (output/'comparison.json').exists():raise RuntimeError('Comparison already exists')
phrases=[
 ('Каждый отражался в воде, и отражения медленно плыли к мосту.','Каждый отражался в воде, и отражения медленно плыли к мосту́.'),
 ('На столе лежала тетрадь смотрителя.','На столе лежала тетрадь смотри́теля.'),
 ('Иногда читатель приходит после дождя.','Иногда чита́тель приходит после дождя.'),
 ('Анна принесла три тома.','Анна принесла три тома́.'),
]
import torch
import numpy as np
records=[]
studio.start()
try:
    # Capture CUDA graph once before resetting the same RNG seed for each pair.
    studio.render('Проверка звучания.',spec,output/'warmup.wav')
    for n,pair in enumerate(phrases,1):
        for label,text in zip(('A','B'),pair):
            studio.license().authorize()
            random.seed(1700+n);np.random.seed(1700+n);torch.manual_seed(1700+n);torch.cuda.manual_seed_all(1700+n)
            path=output/f'{n:02d}-{label}.wav'
            start=time.monotonic();studio.render(text,spec,path)
            with wave.open(str(path)) as wav:duration=wav.getnframes()/wav.getframerate()
            mp3=path.with_suffix('.mp3')
            encode([ffmpeg_path(),'-v','error','-nostdin','-n','-i',str(path),'-c:a','libmp3lame','-b:a','128k',str(mp3)])
            records.append(dict(phrase=n,variant=label,text=text,file=str(mp3),seconds=duration,compute_seconds=time.monotonic()-start,seed=1700+n))
            print(json.dumps(records[-1],ensure_ascii=False),flush=True)
    silence=output/'pause.wav'
    with wave.open(str(silence),'wb') as wav:
        wav.setparams((1,2,24000,0,'NONE','not compressed'));wav.writeframes(b'\0\0'*24000)
    for label,title in [('A','A — без пометок'),('B','B — точечные ударения')]:
        parts=[]
        for n in range(1,len(phrases)+1):
            if parts:parts.append(silence)
            parts.append(output/f'{n:02d}-{label}.wav')
        merged=output/f'{label}.wav';join_wavs(parts,merged)
        encode([ffmpeg_path(),'-v','error','-nostdin','-n','-i',str(merged),'-c:a','libmp3lame','-b:a','128k',str(output/(title+'.mp3'))])
    (output/'comparison.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'Порядок фраз.txt').write_text('A — без пометок. B — одно точечное ударение. Порядок фраз одинаковый.\n\n'+'\n\n'.join(f'{i}. A: {a}\n   B: {b}' for i,(a,b) in enumerate(phrases,1)),encoding='utf-8')
finally:studio.close()
