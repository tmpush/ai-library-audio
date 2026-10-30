import json
import argparse
import os
import platform
import io
import queue
import secrets
import subprocess
import tempfile
import threading
import time
from pathlib import Path
import httpx

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--executable',type=Path,default=root/'build/sidecar'/('books-engine.exe' if os.name=='nt' else 'books-engine'))
args=parser.parse_args()
executable=args.executable.resolve()
token=secrets.token_urlsafe(32)
with tempfile.TemporaryDirectory(prefix='lbo-sidecar-check-') as directory:
    start=time.perf_counter()
    process=subprocess.Popen([str(executable)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,encoding='utf-8',env={**os.environ,'BOOKS_LOCAL_TOKEN':token,'BOOKS_DATA':directory},creationflags=0x08000000 if os.name=='nt' else 0)
    lines=queue.Queue()
    errors=[]
    def read():
        for line in process.stdout:lines.put(line)
    def stderr():
        for line in process.stderr:errors.append(line)
    threading.Thread(target=read,daemon=True).start()
    threading.Thread(target=stderr,daemon=True).start()
    try:
        deadline=time.monotonic()+90
        while True:
            line=lines.get(timeout=max(.1,deadline-time.monotonic()))
            try:ready=json.loads(line)
            except ValueError:continue
            if isinstance(ready,dict) and ready.get('type')=='ready':break
            if time.monotonic()>=deadline:raise TimeoutError('Engine readiness timeout')
        startup=time.perf_counter()-start
        assert ready['type']=='ready'
        client=httpx.Client(base_url=f"http://127.0.0.1:{ready['port']}",headers={'Authorization':'Bearer '+token})
        assert client.get('/projects').status_code==200
        assert httpx.get(str(client.base_url)+'/projects').status_code==401
        sample=root/'samples/night-keeper.fb2'
        response=client.post('/import',files={'file':(sample.name,sample.read_bytes())})
        assert response.status_code==200,response.text
        project=response.json()['id']
        result=client.post(f'/projects/{project}/prepare',json={'chapter':0,'numbers':True})
        assert 'двенадцать' in result.json()['text'],result.text
        from PIL import Image
        picture=io.BytesIO();Image.new('RGB',(64,96),'green').save(picture,format='PNG')
        result=client.post(f'/projects/{project}/cover',files={'file':('cover.png',picture.getvalue())})
        assert result.status_code==200,result.text
        assert client.get(f'/projects/{project}/cover').headers['content-type']=='image/jpeg'
        for filename in ['lighthouse.epub','forest-route.pdf']:
            sample=root/'samples'/filename
            result=client.post('/import',files={'file':(filename,sample.read_bytes())})
            assert result.status_code==200,result.text
            assert len(result.json()['book']['chapters'])==2,result.text
        # An English FB2 checks that langdetect's model data is included in the frozen build.
        english=b'<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><body><section><title><p>Chapter One</p></title><p>The librarian opened the door and welcomed visitors who wanted to read a book. Every evening the library was filled with people from the town.</p></section></body></FictionBook>'
        result=client.post('/import',files={'file':('english.fb2',english)})
        assert result.json()['book']['chapters'][0]['language']=='en',result.text
        client.close()
        process.stdin.write('shutdown\n');process.stdin.flush()
        process.wait(timeout=15)
        assert process.returncode==0,errors
        report={'platform':platform.platform(),'executable_bytes':executable.stat().st_size,'startup_seconds':round(startup,3),'checks':['ready handshake','authenticated projects','unauthenticated denied','FB2/EPUB/PDF import','Russian normalization','cover conversion','English language detection data','graceful shutdown'],'exit_code':process.returncode}
        (root/'artifacts').mkdir(exist_ok=True)
        (root/'artifacts/sidecar-check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))
    except Exception:
        print(''.join(errors))
        raise
    finally:
        if process.poll() is None:process.kill();process.wait()
