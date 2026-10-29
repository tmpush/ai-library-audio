"""Local owner-authorized comparison using the existing activated license.

Uses the normal Studio queue and license checks; original voice and exports stay intact.
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from client.engine.books import Book, Block, Chapter
from client.engine.storage import Library
from client.engine.service import Studio
from client.engine.export import join_wavs, encode, ffmpeg_path

data = Path(os.environ['APPDATA'])/'app.localbooks.studio'
os.environ['BOOKS_LICENSE_URL'] = 'http://127.0.0.1:8791'
os.environ['BOOKS_LICENSE_PUBLIC_KEY'] = (Path(os.environ['APPDATA'])/'LocalBooksLicenseServer/public-key.txt').read_text().strip()
library = Library(data)
studio = Studio(data,library)
voice_id = '25de4410-f1ca-4a64-93d3-e11328c33c0e'
options = dict(numbers=True,remove_notes=False,remove_page_furniture=False,dictionary={})
book_id = next(p['id'] for p in library.list() if p['title']=='Ночной смотритель')
sample = 'В 2026 году смотритель нашёл 12 книг. На часах было 09:05. Цена книги — 1 250,50 ₽. Он прочитал 21% текста и прошёл 2 км. Температура опустилась до -5 градусов.'
test_id = next((p['id'] for p in library.list() if p['title']=='Проверка чисел'),None)
test_id = library.save(Book('Проверка чисел','Книжная мастерская',hashlib.sha256(sample.encode()).hexdigest(),
    [Block(sample)], [Chapter('Проверка чисел',0,1)]),test_id)
destination = Path.home()/'Documents/Аудиокниги/Ночной смотритель'
report=[]
studio.start()
try:
    for identifier,filename in [(book_id,'Ночной смотритель — очистка и числа.mp3'),(test_id,'Проверка чисел — с очисткой.mp3')]:
        output=destination/filename
        if output.exists():raise ValueError('Comparison already exists: '+str(output))
        job=studio.enqueue(identifier,voice_id,options,denoise=True)
        print(json.dumps({'job':job,'state':'queued'},ensure_ascii=False),flush=True)
        deadline=time.monotonic()+600
        while studio.queue.status(job) not in {'done','failed'}:
            if time.monotonic()>deadline:raise TimeoutError(job)
            time.sleep(1)
        state=studio.queue.state(job)
        if state['status']!='done':raise RuntimeError(state['error'])
        merged=ROOT/'artifacts'/f'{job}.wav'
        join_wavs([studio.queue.cache/(c['cache_key']+'.wav') for c in state['chunks']],merged)
        encode([ffmpeg_path(),'-v','error','-nostdin','-n','-i',str(merged),'-c:a','libmp3lame','-b:a','128k',str(output)])
        encode([ffmpeg_path(),'-v','error','-i',str(output),'-f','null','-'])
        result={'job':job,'output':str(output),'audio_seconds':sum(c['audio_seconds'] for c in state['chunks']),
                'compute_seconds':sum(c['seconds'] for c in state['chunks']),'spoken_text':[c['text'] for c in state['chunks']],
                'denoise':True,'voice_id':voice_id}
        report.append(result)
        (ROOT/'artifacts/speech-quality-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False),flush=True)
finally:
    studio.close()
