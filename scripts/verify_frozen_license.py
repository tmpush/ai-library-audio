"""Windows/macOS integration: a real frozen engine, temporary HTTP server and native keyring.
No model, real user license or book content is used. No secrets are printed.
"""
import base64
import argparse
import json
import hashlib
import os
import queue
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
import httpx
import keyring
import uvicorn
from argon2 import PasswordHasher
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from client.engine.hardware import machine_id
from server.api import create_app
from server.licenses import LicenseStore

parser=argparse.ArgumentParser()
parser.add_argument('--executable',type=Path,default=root/'build/sidecar'/('books-engine.exe' if os.name=='nt' else 'books-engine'))
parser.add_argument('--with-tts',action='store_true',help='Run a real GPU chapter with the existing model and synthetic reference')
args=parser.parse_args()
executable=args.executable.resolve()
tts_report=None
account=machine_id()
previous=keyring.get_password('local-books-order',account)
created_key=None
process=None
client=None
with tempfile.TemporaryDirectory(prefix='lbo-frozen-license-') as temporary:
    directory=Path(temporary)
    signing=Ed25519PrivateKey.generate()
    store=LicenseStore(directory/'server.db',signing)
    issued=store.issue()
    app=create_app(store,PasswordHasher().hash(secrets.token_urlsafe(32)),secrets.token_urlsafe(48))
    sock=socket.socket();sock.bind(('127.0.0.1',0))
    url=f'http://127.0.0.1:{sock.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    deadline=time.monotonic()+10
    while not server.started and time.monotonic()<deadline:time.sleep(.05)
    assert server.started,'Temporary license server did not start'
    token=secrets.token_urlsafe(48)
    environment={**os.environ,'BOOKS_LOCAL_TOKEN':token,'BOOKS_DATA':str(directory/'client'),
                 'BOOKS_LICENSE_URL':url,'BOOKS_LICENSE_PUBLIC_KEY':base64.b64encode(signing.public_key().public_bytes_raw()).decode()}
    if args.with_tts and os.name=='nt':
        # Prove that the packaged engine/export do not use Python, Node or FFmpeg from PATH.
        environment['PATH']=os.pathsep.join([str(Path(os.environ['SystemRoot'])/'System32'),
            str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0'),os.environ['SystemRoot']])
        environment.pop('PYTHONPATH',None)
        environment.pop('PYTHONHOME',None)
    errors=[]
    def launch():
        global process,client
        process=subprocess.Popen([str(executable)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                 text=True,encoding='utf-8',env=environment,creationflags=0x08000000 if os.name=='nt' else 0)
        lines=queue.Queue()
        def stdout():
            for line in process.stdout:lines.put(line)
        def stderr():
            with (root/'artifacts/frozen-engine-stderr.log').open('a',encoding='utf-8') as log:
                for line in process.stderr:
                    errors.append(line);log.write(line);log.flush()
        threading.Thread(target=stdout,daemon=True).start();threading.Thread(target=stderr,daemon=True).start()
        deadline=time.monotonic()+90
        while True:
            line=lines.get(timeout=max(.1,deadline-time.monotonic()))
            try:ready=json.loads(line)
            except ValueError:continue
            if isinstance(ready,dict) and ready.get('type')=='ready':break
            if time.monotonic()>=deadline:raise TimeoutError('Engine readiness timeout')
        print('Frozen engine ready',flush=True)
        client=httpx.Client(base_url=f"http://127.0.0.1:{ready['port']}",headers={'Authorization':'Bearer '+token},timeout=30)
    def stop():
        global process,client
        if client:client.close();client=None
        if process:
            process.stdin.write('shutdown\n');process.stdin.flush();process.wait(timeout=20)
            assert process.returncode==0,''.join(errors)
            process=None
    try:
        launch()
        response=client.post('/license/activate',json={'key':issued['key']})
        assert response.status_code==200,response.text
        created_key=keyring.get_password('local-books-order',account)
        assert created_key and client.get('/license').json()['valid']
        encrypted=(directory/'client/license.enc').read_bytes()
        assert issued['key'].encode() not in encrypted
        stop();launch()
        assert client.get('/license').json()['valid'],'License was not restored after restart'
        assert len(store.activations(issued['id']))==1
        if args.with_tts:
            print('Checking packaged TTS runtime and local model',flush=True)
            runtime=client.get('/runtime').json()
            assert all(runtime.values()),runtime
            response=client.post('/settings/model',json={'directory':str(root/'models/qwen3-tts-0.6b-5d83992436ea'),'device':'cuda:0','backend':'cuda_graphs'},timeout=120)
            response.raise_for_status()
            sample=root/'samples/night-keeper.fb2'
            response=client.post('/import',files={'file':(sample.name,sample.read_bytes())})
            response.raise_for_status();project=response.json()['id']
            response=client.post('/voices',data={'name':'Synthetic technical reference','transcript':(root/'data/tts-check/reference.txt').read_text(encoding='utf-8-sig')},files={'file':('reference.wav',(root/'data/tts-check/reference.wav').read_bytes())})
            response.raise_for_status();voice=response.json()['id']
            response=client.post(f'/projects/{project}/render',json={'voice_id':voice,'chapter':0,'numbers':True})
            response.raise_for_status();job=response.json()['id']
            print('Rendering the control chapter on CUDA',flush=True)
            deadline=time.monotonic()+180
            while time.monotonic()<deadline:
                state=next(j for j in client.get('/jobs').json() if j['id']==job)
                if state['status'] in {'done','error','failed','paused'}:break
                time.sleep(1)
            assert state['status']=='done',state
            output=root/'artifacts/frozen-gpu-export'
            response=client.post(f'/jobs/{job}/export',json={'directory':str(output)},timeout=120)
            response.raise_for_status()
            audio=client.get(f'/jobs/{job}/audio/0');audio.raise_for_status()
            (root/'artifacts/frozen-gpu-first.wav').write_bytes(audio.content)
            tts_report={'runtime':runtime,'job':state,'export':response.json(),'reference':'synthetic Windows SAPI; human voice similarity not evaluated'}
            print('Frozen CUDA synthesis and FFmpeg export passed',flush=True)
        store.update(issued['id'],revoke=True)
        stop();launch()
        deadline=time.monotonic()+15
        status=client.get('/license').json()
        while status['valid'] and time.monotonic()<deadline:
            time.sleep(.2);status=client.get('/license').json()
        assert not status['valid'],'Revoked license survived startup verification'
        assert client.post('/projects/missing/render',json={'voice_id':'missing'}).status_code==400
        assert client.get('/projects').status_code==200
        stop()
        report={'checks':['frozen activation over HTTP','native keyring + encrypted file','license restored after restart',
                          'same machine activation remains idempotent','revocation enforced after restart','render denied while library remains accessible'],
                'model_used':args.with_tts,'temporary_license':True,'tts':tts_report,
                'executable_sha256':hashlib.sha256(executable.read_bytes()).hexdigest()}
        (root/('artifacts/frozen-full-check.json' if args.with_tts else 'artifacts/frozen-license-check.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))
    finally:
        if client:client.close()
        if process and process.poll() is None:process.kill();process.wait()
        server.should_exit=True;thread.join(timeout=10);store.engine.dispose()
        # Preserve an existing product credential; remove only the credential created by this test.
        if previous is None and created_key and keyring.get_password('local-books-order',account)==created_key:
            keyring.delete_password('local-books-order',account)
