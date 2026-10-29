import json
import math
import struct
import time
import wave
from pathlib import Path
from fastapi.testclient import TestClient
from client.engine.api import create_app


class Allowed:
    def authorize(self, **kwargs): return {'valid': True}


def wav(path, seconds):
    with wave.open(str(path), 'wb') as out:
        out.setparams((1,2,16000,0,'NONE','not compressed'))
        out.writeframes(b''.join(struct.pack('<h',int(5000*math.sin(i*.1))) for i in range(seconds*16000)))


def test_connected_pipeline_with_injected_test_synth(tmp_path):
    app=create_app(tmp_path/'library','t'*32)
    studio=app.state.studio
    studio.license_client=Allowed()
    studio.settings={'model_directory':'test-only','model_revision':'test-only','device':'cpu'}
    studio.verify_model=lambda spec:None
    def test_synth(text,spec,path): wav(path,1)
    studio.render=test_synth
    sample=Path(__file__).resolve().parents[1]/'samples/night-keeper.fb2'
    reference=tmp_path/'voice.wav';wav(reference,15)
    with TestClient(app) as api:
        api.headers['Authorization']='Bearer '+'t'*32
        imported=api.post('/import',files={'file':(sample.name,sample.read_bytes())}).json()
        voice=api.post('/voices',data={'name':'Test'},files={'file':('voice.wav',reference.read_bytes())}).json()
        assert voice['duration']==15
        response=api.post('/projects/'+imported['id']+'/render',json={'voice_id':voice['id']})
        assert response.status_code==200,response.text
        identifier=response.json()['id']
        deadline=time.monotonic()+10
        while studio.queue.state(identifier)['status'] not in {'done','failed'} and time.monotonic()<deadline:
            time.sleep(.05)
        assert studio.queue.state(identifier)['status']=='done'
        jobs=api.get('/jobs').json()
        assert jobs[0]['completed']==jobs[0]['total']
        audio=api.get('/jobs/'+identifier+'/audio/0')
        assert audio.status_code==200 and audio.content[:4]==b'RIFF'
        assert api.get('/jobs/'+identifier+'/audio/999').status_code==404
        result=api.post('/jobs/'+identifier+'/export',json={'directory':str(tmp_path/'output')})
        assert result.status_code==200,result.text
        assert Path(result.json()['files'][-1]).exists()
        assert api.get('/projects').json()[0]['render']['status']=='done'
        assert api.get('/jobs/'+identifier+'/chunks').json()[0]['done']
        assert api.delete('/projects/'+imported['id']).status_code==200
        assert studio.queue.summaries()==[]
        assert not list(studio.queue.cache.glob('*.wav'))
        assert api.get('/projects').json()==[]
        # User exports and source files survive project deletion.
        assert sample.is_file() and Path(result.json()['files'][-1]).is_file()


def test_synthesis_blocked_without_license_but_import_available(tmp_path):
    api=TestClient(create_app(tmp_path,'t'*32))
    api.headers['Authorization']='Bearer '+'t'*32
    assert api.get('/projects').status_code==200
    result=api.post('/projects/nonexistent/render',json={'voice_id':'missing'})
    assert result.status_code==400
    assert 'лицензий' in result.json()['detail']


def test_download_staging_does_not_change_model_identity(tmp_path):
    from client.engine.service import Studio
    (tmp_path/'config.json').write_text('{}')
    weights=tmp_path/'model.safetensors'
    weights.write_bytes(b'verified-weights')
    identity=Studio.fingerprint(tmp_path)
    (tmp_path/'model.safetensors.part').write_bytes(b'incomplete-prefix')
    ranges=tmp_path/'model.safetensors.ranges'
    ranges.mkdir()
    (ranges/'0').write_bytes(b'downloaded-range')
    assert Studio.fingerprint(tmp_path)==identity
    (ranges/'0').write_bytes(b'retried-range')
    assert Studio.fingerprint(tmp_path)==identity
    weights.write_bytes(b'replaced-weights')
    assert Studio.fingerprint(tmp_path)!=identity
