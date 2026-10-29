"""Explicit local GPU smoke test; uses an isolated queue, never changes the user's book."""
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from client.engine.service import Studio
from client.engine.storage import Library
from client.engine.export import export_mp3

data=Path(os.environ['APPDATA'])/'app.localbooks.studio'
output=ROOT/'artifacts'/('repair-smoke-'+time.strftime('%Y%m%d-%H%M%S'))
output.mkdir(parents=True)
os.environ['BOOKS_LICENSE_URL']='http://127.0.0.1:8791'
os.environ['BOOKS_LICENSE_PUBLIC_KEY']=(Path(os.environ['APPDATA'])/'LocalBooksLicenseServer/public-key.txt').read_text().strip()
os.environ['PATH']=str(ROOT/'build/ffmpeg-runtime')+os.pathsep+os.environ['PATH']
for name in ['settings.json','license.enc']:
    shutil.copy2(data/name,output/name)
voice_id='47d3ae05-be84-4461-9159-e6310bd4ddd5'
shutil.copytree(data/'voices'/voice_id,output/'voices'/voice_id)
studio=Studio(output,Library(output))
studio.license().authorize()
_,voice=studio.voices.load(voice_id)
spec={**studio.settings,'voice_id':voice_id,'voice_hash':voice['sha256'],'transcript':voice['transcript'],
      'language':'ru','denoise':True,'auto_stress':False,'title':'Проверка дублей','author':'',
      'project_id':'isolated-test','chapters':[{'title':'Проба','start':0,'end':2,'language':'ru'}]}
source=Path.home()/'Documents/Аудиокниги/Ночной смотритель/Сравнение — Мой голос 2'
texts=['Каждый отражался в воде, и отражения медленно плыли к мосту.','На столе лежала тетрадь смотрителя.']
parent=studio.queue.enqueue(texts,spec)
for i,chunk in enumerate(studio.queue.state(parent)['chunks']):
    dest=studio.queue.cache/(chunk['cache_key']+'.wav')
    shutil.copy2(source/f'{i+1:02d}-A.wav',dest)
studio.queue.run(parent,studio.render,lambda:studio.license().authorize(),studio.verify_model)
original=studio.queue.state(parent)['chunks']
result=studio.create_takes(parent,0,texts[0],3)
records=[]
for i,take in enumerate(result['takes'][1:],1):
    start=time.monotonic()
    studio.queue.run(take['id'],studio.render,lambda:studio.license().authorize(),studio.verify_model)
    chunk=studio.queue.state(take['id'])['chunks'][0]
    path=studio.queue.cache/(chunk['cache_key']+'.wav')
    mp3=output/f'take-{i}.mp3'
    export_mp3([path],mp3,'Ночной смотритель — дубль '+str(i))
    record={'take':i,'seconds':chunk['audio_seconds'],'elapsed':time.monotonic()-start,
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'file':str(mp3)}
    records.append(record)
    print(json.dumps(record,ensure_ascii=False),flush=True)
chosen=result['takes'][1]
studio.apply_take(parent,0,chosen['id'],original[0]['cache_key'])
assert studio.queue.state(parent)['chunks'][1]==original[1]
studio.export_single_mp3(parent,str(output/'replaced.mp3'))
studio.apply_take(parent,0,result['takes'][0]['id'],chosen['cache_key'])
assert studio.queue.state(parent)['chunks']==original
assert len({r['sha256'] for r in records})==3
(output/'result.json').write_text(json.dumps({'voice_id':voice_id,'takes':records,'replace_and_restore':True},ensure_ascii=False,indent=2),encoding='utf-8')
# No activation secrets are retained in the test artifact.
(output/'license.enc').unlink()
print('VERIFIED '+str(output),flush=True)
