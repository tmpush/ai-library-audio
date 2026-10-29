"""Explicit, revision-pinned model downloads. Never called by inference."""
import hashlib
import json
import threading
from pathlib import Path, PurePosixPath
import httpx

MODEL_ID='Qwen/Qwen3-TTS-12Hz-0.6B-Base'


def model_info(client=None):
    if client is None:
        with httpx.Client(timeout=30,follow_redirects=True) as connection:
            return model_info(connection)
    response=client.get(f'https://huggingface.co/api/models/{MODEL_ID}?blobs=true')
    response.raise_for_status()
    data=response.json()
    files=[]
    for entry in data['siblings']:
        name=entry['rfilename']
        path=PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
            raise ValueError('Недопустимый путь в описании модели')
        if name.startswith('.') or name=='README.md':continue
        size=entry.get('size')
        if not isinstance(size,int) or size<0:raise ValueError('Неизвестен размер файла модели')
        files.append({'name':name,'size':size,'sha256':entry.get('lfs',{}).get('sha256')})
    revision=data['sha']
    if len(revision)!=40 or any(c not in '0123456789abcdef' for c in revision):raise ValueError('Некорректная ревизия модели')
    return {'id':MODEL_ID,'revision':revision,'bytes':sum(f['size'] for f in files),'files':files,'license':'Apache-2.0'}


class ModelDownload:
    def __init__(self):
        self.lock=threading.Lock()
        self.state={'status':'idle','downloaded':0,'total':0}
        self.thread=None
        self.cancelled=threading.Event()

    def snapshot(self):
        with self.lock:return dict(self.state)

    def cancel(self):
        self.cancelled.set()
        return self.snapshot()

    def start(self, parent:Path, expected_revision:str, expected_bytes:int, confirmed:bool):
        if not confirmed:raise ValueError('Подтвердите скачивание модели с указанным размером')
        info=model_info()
        if info['revision']!=expected_revision or info['bytes']!=expected_bytes:
            raise ValueError('Состав модели изменился. Обновите размер и подтвердите скачивание заново')
        destination=parent.expanduser().resolve()/('qwen3-tts-0.6b-'+info['revision'][:12])
        with self.lock:
            if self.state['status']=='downloading':raise ValueError('Скачивание уже выполняется')
            manifest=destination/'download-manifest.json'
            if destination.exists():
                if not manifest.is_file() or json.loads(manifest.read_text(encoding='utf-8'))!=info:
                    raise ValueError('Папка уже существует и не относится к этому скачиванию. Выберите другую папку')
            else:
                destination.mkdir(parents=True)
                manifest.write_text(json.dumps(info),encoding='utf-8')
            self.cancelled.clear()
            self.state={'status':'downloading','downloaded':0,'total':info['bytes'],'directory':str(destination)}
        self.thread=threading.Thread(target=self._run,args=(info,destination),daemon=True,name='model-download')
        self.thread.start()
        return self.snapshot()

    def _run(self,info,destination):
        try:
            with httpx.Client(timeout=60,follow_redirects=True) as client:
                for file in info['files']:
                    if self.cancelled.is_set():raise InterruptedError('Скачивание остановлено')
                    target=destination/file['name']
                    target.parent.mkdir(parents=True,exist_ok=True)
                    if target.is_file() and target.stat().st_size==file['size']:
                        existing=hashlib.sha256()
                        with target.open('rb') as stream:
                            for chunk in iter(lambda:stream.read(1024*1024),b''):existing.update(chunk)
                        if file['sha256'] and existing.hexdigest()==file['sha256']:
                            with self.lock:self.state['downloaded']+=file['size']
                            continue
                    temp=target.with_suffix(target.suffix+'.part')
                    digest=hashlib.sha256();size=0
                    url=f"https://huggingface.co/{MODEL_ID}/resolve/{info['revision']}/{file['name']}"
                    with client.stream('GET',url) as response:
                        response.raise_for_status()
                        with temp.open('wb') as stream:
                            for chunk in response.iter_bytes(1024*1024):
                                if self.cancelled.is_set():raise InterruptedError('Скачивание остановлено')
                                size+=len(chunk)
                                if size>file['size']:raise ValueError('Размер скачанного файла превышает ожидаемый')
                                digest.update(chunk);stream.write(chunk)
                                with self.lock:self.state['downloaded']+=len(chunk)
                    if size!=file['size'] or (file['sha256'] and digest.hexdigest()!=file['sha256']):
                        raise ValueError('Проверка целостности модели не прошла')
                    temp.replace(target)
            (destination/'download-manifest.json').write_text(json.dumps(info),encoding='utf-8')
            with self.lock:self.state['status']='done'
        except InterruptedError as exc:
            with self.lock:self.state.update(status='cancelled',error=str(exc))
        except Exception as exc:
            with self.lock:self.state.update(status='failed',error=str(exc))
