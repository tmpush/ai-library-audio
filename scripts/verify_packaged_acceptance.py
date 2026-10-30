"""Exercise the installed engine and prepare selectable pronunciation takes.
Run only with the GUI closed. Uses the owner's normal license and library.
"""
import json,os,queue,secrets,subprocess,sys,threading,time
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from client.engine.books import Block,prepare_text
data=Path(os.environ['APPDATA'])/'app.localbooks.studio'
installed=Path(os.environ['LOCALAPPDATA'])/'Programs/LocalBooksStudio'
output=ROOT/'artifacts/packaged-acceptance';output.mkdir(parents=True,exist_ok=True)
token=secrets.token_urlsafe(48)
env={**os.environ,'BOOKS_DATA':str(data),'BOOKS_LOCAL_TOKEN':token,'BOOKS_LICENSE_URL':'http://127.0.0.1:8791',
     'BOOKS_LICENSE_PUBLIC_KEY':(Path(os.environ['APPDATA'])/'LocalBooksLicenseServer/public-key.txt').read_text().strip()}
log=(output/'engine.log').open('w',encoding='utf-8')
p=subprocess.Popen([str(installed/'engine/books-engine.exe')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,
                   text=True,encoding='utf-8',env=env,creationflags=0x08000000)
lines=queue.Queue()
def read():
    for line in p.stdout:lines.put(line)
threading.Thread(target=read,daemon=True).start()
client=None
try:
    deadline=time.monotonic()+120
    while True:
        line=lines.get(timeout=max(.1,deadline-time.monotonic()))
        try:ready=json.loads(line)
        except ValueError:continue
        if ready.get('type')=='ready':break
    client=httpx.Client(base_url=f"http://127.0.0.1:{ready['port']}",headers={'Authorization':'Bearer '+token},timeout=120,trust_env=False)
    def get(path):
        r=client.get(path);r.raise_for_status();return r.json()
    def post(path,body):
        r=client.post(path,json=body);r.raise_for_status();return r.json()
    assert get('/license')['valid']
    probe=post('/pronunciation/preview',{'text':'Большинство считают, что X, но истина противоположна Х.'})
    assert probe['text']=='Большинство считают, что икс, но истина противоположна икс.'
    imported=[]
    projects=get('/projects')
    for filename,title in [('war-and-peace.epub','Война и мир'),('crime-and-punishment.epub','Преступление и наказание')]:
        existing=next((b for b in projects if b['title']==title),None)
        if existing is None:
            path=ROOT/'data/acceptance-books'/filename
            r=client.post('/import',files={'file':(filename,path.read_bytes())});r.raise_for_status();existing=r.json()
        book=get('/projects/'+existing['id'])
        imported.append({'id':existing['id'],'title':book['title'],'chapters':len(book['chapters']),'blocks':len(book['blocks'])})
        print(json.dumps(imported[-1],ensure_ascii=False),flush=True)
    parent='2648bc79-2444-490d-ad07-9b71d02318c2'
    repair=get('/jobs/'+parent+'/repair')
    position=next(i for i,c in enumerate(repair['chunks']) if 'противоположна' in c['text'])
    original=repair['chunks'][position]['cache_key']
    text=prepare_text([Block(repair['chunks'][position]['text'])])
    assert 'икс' in text
    before={j['id'] for j in get('/jobs')}
    post('/jobs/'+parent+'/takes',{'position':position,'text':text,'count':3})
    jobs=[j for j in get('/jobs') if j['id'] not in before and j.get('repair_parent')==parent and j['status']!='done']
    assert len(jobs)==3
    wanted={j['id'] for j in jobs};deadline=time.monotonic()+1200
    while time.monotonic()<deadline:
        states=[j for j in get('/jobs') if j['id'] in wanted]
        if any(j['status'] in ['failed','paused'] for j in states):raise RuntimeError(str(states))
        if all(j['status']=='done' for j in states):break
        time.sleep(4)
    else:raise TimeoutError('Takes remain saved in the queue')
    for index,j in enumerate(states,1):
        destination=output/f'pronunciation-take-{index}.mp3'
        if destination.exists():destination=output/f'pronunciation-take-{index}-{time.strftime("%H%M%S")}.mp3'
        post('/jobs/'+j['id']+'/export-mp3',{'path':str(destination)})
        j['file']=str(destination)
    assert get('/jobs/'+parent+'/repair')['chunks'][position]['cache_key']==original
    result={'installed_engine':True,'license_active':True,'symbol_preview':True,'imported':imported,'repair_position':position,
            'takes':states,'original_unchanged':True}
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Packaged acceptance: PASS',flush=True)
finally:
    if client:client.close()
    if p.poll() is None:
        p.stdin.write('shutdown\n');p.stdin.flush()
        try:p.wait(timeout=25)
        except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=10)
    log.close()
    subprocess.Popen([str(installed/'local-books-order.exe')],cwd=installed)
