"""Download the approved Windows CUDA wheel from its official index, with verified ranges/hash."""
import concurrent.futures
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urldefrag,unquote
import httpx
from bs4 import BeautifulSoup

name='torch-2.9.1+cu128-cp311-cp311-win_amd64.whl'
root=Path('build/tts-wheels');root.mkdir(parents=True,exist_ok=True)
with httpx.Client(timeout=30,follow_redirects=True) as client:
    response=client.get('https://download.pytorch.org/whl/cu128/torch/');response.raise_for_status()
    link=next(a['href'] for a in BeautifulSoup(response.text,'html.parser').find_all('a') if unquote(a.get('href','').split('#')[0]).endswith('/'+name))
    url,fragment=urldefrag(link)
    url=url.replace('https://download-r2.pytorch.org/', 'https://download.pytorch.org/')
    if not re.fullmatch('sha256=[0-9a-f]{64}',fragment):raise ValueError('Official SHA-256 is missing')
    expected=fragment.split('=')[1]
    response=client.head(url);response.raise_for_status();size=int(response.headers['content-length'])
parts=root/(name+'.parts');parts.mkdir(exist_ok=True)
existing=root/name
if existing.is_file() and existing.stat().st_size==size:
    digest=hashlib.sha256()
    with existing.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    if digest.hexdigest()==expected:
        print(json.dumps({'status':'verified','sha256':expected,'path':str(existing)}),flush=True)
        raise SystemExit(0)
chunk_size=16*1024*1024
progress=0;lock=threading.Lock();local=threading.local()
def download(index):
    global progress
    start=index*chunk_size;end=min(size,start+chunk_size)-1
    path=parts/str(index)
    if not path.is_file() or path.stat().st_size!=end-start+1:
        if not hasattr(local,'client'):local.client=httpx.Client(timeout=60,follow_redirects=True)
        for attempt in range(4):
            try:
                temporary=path.with_suffix('.part')
                with local.client.stream('GET',url+f'?segment={index}',headers={'Range':f'bytes={start}-{end}'}) as response:
                    if response.status_code!=206 or response.headers.get('content-range')!=f'bytes {start}-{end}/{size}':
                        raise ValueError('Server did not return the requested byte range')
                    written=0
                    with temporary.open('wb') as output:
                        for block in response.iter_bytes(1024*1024):
                            written+=len(block)
                            if written>end-start+1:raise ValueError('Oversized range')
                            output.write(block)
                if written!=end-start+1:raise ValueError('Incomplete range')
                temporary.replace(path)
                break
            except (httpx.HTTPError,ValueError):
                if attempt==3:raise
                time.sleep(1+attempt)
    with lock:
        progress+=end-start+1
        print(json.dumps({'downloaded':progress,'total':size}),flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as workers:
    list(workers.map(download,range((size+chunk_size-1)//chunk_size)))
digest=hashlib.sha256()
temporary=root/(name+'.part')
with temporary.open('wb') as output:
    for index in range((size+chunk_size-1)//chunk_size):
        with (parts/str(index)).open('rb') as source:
            for block in iter(lambda:source.read(1024*1024),b''):
                digest.update(block);output.write(block)
if digest.hexdigest()!=expected:raise ValueError('Final SHA-256 mismatch; wheel was not accepted')
temporary.replace(root/name)
(root/'torch-source.json').write_text(json.dumps({'url':url,'sha256':expected,'bytes':size}),encoding='utf-8')
print(json.dumps({'status':'verified','sha256':expected,'path':str(root/name)}),flush=True)
