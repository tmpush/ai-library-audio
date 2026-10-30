"""Real CUDA allocator failure in an isolated process with a 2% memory budget.
Never fills the physical GPU or changes the user's active queue/settings.
"""
import hashlib,json,os,shutil,subprocess,sys,time,sqlite3,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from client.engine.service import Studio
from client.engine.storage import Library
from client.engine.books import Block,prepare_text
from client.engine.export import export_mp3

data=Path(os.environ['APPDATA'])/'app.localbooks.studio'
output=ROOT/'artifacts/vram-recovery';output.mkdir(parents=True,exist_ok=True)
os.environ['BOOKS_LICENSE_URL']='http://127.0.0.1:8791'
os.environ['BOOKS_LICENSE_PUBLIC_KEY']=(Path(os.environ['APPDATA'])/'LocalBooksLicenseServer/public-key.txt').read_text().strip()
os.environ['PATH']=str(ROOT/'build/ffmpeg-runtime')+os.pathsep+os.environ['PATH']

if '--oom' in sys.argv:
    import torch
    torch.cuda.set_per_process_memory_fraction(.02,0)
    job=json.loads((output/'job.json').read_text())['job']
    studio=Studio(output,Library(output))
    try:
        studio.queue.run(job,studio.render,lambda:studio.license().authorize(),studio.verify_model)
    except ValueError as exc:
        assert 'VRAM' in str(exc),str(exc)
        state=studio.queue.state(job)
        assert state['status']=='failed' and state['completed']==1
        assert studio.engine is None and studio.engine_key is None
        result={'cuda_budget_fraction':.02,'total_vram_bytes':torch.cuda.get_device_properties(0).total_memory,
            'error':state['error'],'completed':state['completed'],'total':state['total'],'failed_safely':True}
        print('Real CUDA OOM captured; first chunk retained; worker model released',flush=True)
        torch.cuda.set_per_process_memory_fraction(1.0,0)
        studio.queue.resume(job)
        started=time.perf_counter()
        studio.queue.run(job,studio.render,lambda:studio.license().authorize(),studio.verify_model)
        result['same_process_resume_done']=studio.queue.state(job)['status']=='done'
        result['resume_seconds']=time.perf_counter()-started
        assert result['same_process_resume_done']
        (output/'oom.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    else:raise AssertionError('Expected an actual CUDA OOM')
    raise SystemExit(0)

for name in ['settings.json','license.enc']:shutil.copy2(data/name,output/name)
voice_id='59e2ff66-752a-4ab3-8c57-81ef2e1f4650'
shutil.copytree(data/'voices'/voice_id,output/'voices'/voice_id,dirs_exist_ok=True)
studio=Studio(output,Library(output));studio.license().authorize()
_,voice=studio.voices.load(voice_id)
with sqlite3.connect(data/'render/queue.db') as db:
    original=db.execute('SELECT text,cache_key FROM chunks WHERE job=? ORDER BY position',('2648bc79-2444-490d-ad07-9b71d02318c2',)).fetchall()
first_text,first_key=original[0]
variable_text=next(text for text,key in original if 'противоположна' in text)
corrected=prepare_text([Block(variable_text)])
assert 'икс' in corrected and corrected!=variable_text
spec={**studio.settings,'voice_id':voice_id,'voice_hash':voice['sha256'],'transcript':voice['transcript'],'language':'ru',
      'denoise':True,'auto_stress':False,'title':'Проверка VRAM и произношения','author':'','project_id':'isolated-acceptance','chapters':[],
      'test_nonce':str(uuid.uuid4())}
job=studio.queue.enqueue([first_text,corrected],spec)
first=studio.queue.state(job)['chunks'][0]
target=studio.queue.cache/(first['cache_key']+'.wav')
shutil.copy2(data/'render/cache'/(first_key+'.wav'),target)
digest=hashlib.sha256(target.read_bytes()).hexdigest()
# Mark only the copied, already validated original chunk complete.
with studio.queue.connect() as db:
    db.execute('UPDATE chunks SET done=1,audio_seconds=? WHERE job=? AND position=0',(studio.queue.audio_duration(target),job))
(output/'job.json').write_text(json.dumps({'job':job}),encoding='utf-8')
subprocess.run([sys.executable,'-X','utf8',str(Path(__file__).resolve()),'--oom'],check=True,cwd=ROOT)
studio.queue.resume(job)
started=time.perf_counter();studio.queue.run(job,studio.render,lambda:studio.license().authorize(),studio.verify_model)
assert hashlib.sha256(target.read_bytes()).hexdigest()==digest
assert studio.queue.state(job)['status']=='done'
record={'oom':json.loads((output/'oom.json').read_text(encoding='utf-8')),'resume_done':True,'first_chunk_hash_unchanged':True,
        'resume_seconds':time.perf_counter()-started,'samples':[]}
for index,text in enumerate([corrected]+[(ROOT/'artifacts/full-books'/name).read_text(encoding='utf-8') for name in ['war-and-peace.epub.probe.txt','crime-and-punishment.epub.probe.txt']]):
    wav=output/f'sample-{index+1}.wav';mp3=output/f'sample-{index+1}.mp3'
    started=time.perf_counter()
    if index==0:
        second=studio.queue.state(job)['chunks'][1]
        shutil.copy2(studio.queue.cache/(second['cache_key']+'.wav'),wav)
    else:studio.render(prepare_text([Block(text)],numbers=True),spec,wav)
    if mp3.exists():mp3.unlink()
    export_mp3([wav],mp3,'Проверка '+str(index+1))
    record['samples'].append({'file':str(mp3),'audio_seconds':studio.queue.audio_duration(wav),'elapsed_seconds':time.perf_counter()-started})
    print(json.dumps(record['samples'][-1],ensure_ascii=False),flush=True)
(output/'result.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
(output/'license.enc').unlink(missing_ok=True)
print('VRAM recovery and real Qwen samples: PASS',flush=True)
