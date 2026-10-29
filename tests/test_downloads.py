import hashlib
import json
import threading
import httpx
import pytest
from client.engine import models


def test_resume_verifies_existing_files_without_downloading_weights(tmp_path, monkeypatch):
    # Small synthetic payloads; this test never contacts a model host.
    payloads={'model.safetensors':b'fake model', 'config.json':b'{}'}
    manifest={'id':models.MODEL_ID,'revision':'a'*40,'bytes':sum(map(len,payloads.values())),
              'files':[{'name':name,'size':len(body),'sha256':hashlib.sha256(body).hexdigest()} for name,body in payloads.items()],
              'license':'Apache-2.0'}
    monkeypatch.setattr(models,'model_info',lambda:manifest)
    destination=tmp_path/('qwen3-tts-0.6b-'+'a'*12);destination.mkdir()
    (destination/'download-manifest.json').write_text(json.dumps(manifest))
    (destination/'model.safetensors').write_bytes(payloads['model.safetensors'])
    requested=[]
    def response(request):
        name=request.url.path.rsplit('/',1)[-1];requested.append(name)
        return httpx.Response(200,content=payloads[name])
    client=httpx.Client(transport=httpx.MockTransport(response))
    monkeypatch.setattr(models.httpx,'Client',lambda **kwargs:client)
    downloader=models.ModelDownload()
    downloader.start(tmp_path,manifest['revision'],manifest['bytes'],True)
    downloader.thread.join(5)
    assert downloader.snapshot()['status']=='done'
    assert downloader.snapshot()['downloaded']==manifest['bytes']
    assert requested==['config.json']


def test_changed_catalog_requires_new_confirmation(tmp_path,monkeypatch):
    monkeypatch.setattr(models,'model_info',lambda:{'revision':'b'*40,'bytes':100})
    with pytest.raises(ValueError,match='изменился'):
        models.ModelDownload().start(tmp_path,'a'*40,100,True)
    assert not list(tmp_path.iterdir())


def test_download_cancel_does_not_create_complete_file(tmp_path,monkeypatch):
    downloader=models.ModelDownload()
    downloader.cancel()
    downloader._run({'files':[{'name':'model.safetensors'}]},tmp_path)
    assert downloader.snapshot()['status']=='cancelled'
    assert not (tmp_path/'model.safetensors').exists()
