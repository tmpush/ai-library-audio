import concurrent.futures,hashlib,json,time,threading
from pathlib import Path
import httpx
root=Path('models/chatterbox-multilingual-v3');root.mkdir(parents=True,exist_ok=True)
revision='5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18'
with httpx.Client(timeout=30) as c:
    meta=c.get(f'https://huggingface.co/api/models/ResembleAI/chatterbox/revision/{revision}?blobs=true');meta.raise_for_status();meta=meta.json()
chunk=16*1024*1024
for item in meta['siblings']:
    name=item['rfilename']
    if name in ['ve.pt','grapheme_mtl_merged_expanded_v1.json','Cangjie5_TC.json','README.md']:
        dest=root/name
        if not dest.exists():
            with httpx.Client(timeout=60,follow_redirects=True) as c:
                r=c.get(f'https://huggingface.co/ResembleAI/chatterbox/resolve/{revision}/{name}?download=true');r.raise_for_status()
                if len(r.content)!=item['size']:raise ValueError('Incorrect small file size')
                dest.write_bytes(r.content)
        if 'lfs' in item:
            if hashlib.sha256(dest.read_bytes()).hexdigest()!=item['lfs']['sha256']:raise ValueError('Small file checksum mismatch')
        elif hashlib.sha1(b'blob '+str(dest.stat().st_size).encode()+b'\0'+dest.read_bytes()).hexdigest()!=item['blobId']:
            raise ValueError('Small file Git hash mismatch')
        continue
    if name not in ['s3gen.pt','t3_mtl23ls_v3.safetensors']:continue
    size=item['size'];expected=item['lfs']['sha256'];dest=root/name
    if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest()==expected:continue
    parts=root/(name+'.ranges');parts.mkdir(exist_ok=True)
    completed=[0];lock=threading.Lock()
    def download(i):
        a=i*chunk;b=min(size,a+chunk)-1;p=parts/str(i)
        if not p.exists() or p.stat().st_size!=b-a+1:
            for attempt in range(4):
                try:
                    with httpx.Client(timeout=30,follow_redirects=True) as c:
                        with c.stream('GET',f'https://huggingface.co/ResembleAI/chatterbox/resolve/{revision}/{name}?download=true&segment={i}&request={time.time_ns()}',headers={'Range':f'bytes={a}-{b}'}) as r:
                            if r.status_code!=206 or r.headers.get('content-range')!=f'bytes {a}-{b}/{size}':raise ValueError('Invalid range response')
                            tmp=p.with_suffix('.part');n=0
                            with tmp.open('wb') as f:
                                for block in r.iter_bytes(1024*1024):
                                    n+=len(block)
                                    if n>b-a+1:raise ValueError('Oversized response')
                                    f.write(block)
                            if n!=b-a+1:raise ValueError('Truncated response')
                            tmp.replace(p)
                    break
                except (httpx.HTTPError,ValueError):
                    if attempt==3:raise
                    time.sleep(attempt+1)
        with lock:
            completed[0]+=b-a+1
            print(json.dumps({'file':name,'downloaded':completed[0],'total':size}),flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:list(pool.map(download,range((size+chunk-1)//chunk)))
    h=hashlib.sha256();tmp=dest.with_suffix(dest.suffix+'.part')
    with tmp.open('wb') as out:
        for i in range((size+chunk-1)//chunk):
            with (parts/str(i)).open('rb') as src:
                for b in iter(lambda:src.read(1024*1024),b''):h.update(b);out.write(b)
    if h.hexdigest()!=expected:raise ValueError('SHA256 mismatch')
    tmp.replace(dest);print(json.dumps({'verified':name,'sha256':expected}),flush=True)
(root/'download-manifest.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
