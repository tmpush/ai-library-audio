import json
import wave
import pytest
from fastapi.testclient import TestClient
from client.engine.api import create_app
from client.engine.books import Block, prepare_text
from client.engine.queue import RenderQueue


def wav(path):
    with wave.open(str(path),'wb') as out:
        out.setparams((1,2,24000,0,'NONE','not compressed'))
        out.writeframes(b'\0\0'*2400)


@pytest.fixture
def setup(tmp_path):
    app=create_app(tmp_path,'t'*32)
    studio=app.state.studio
    class Allowed:
        def authorize(self,**kwargs): pass
    studio.license_client=Allowed()
    spec={'model_revision':'test','voice_hash':'test','voice_id':'same-voice','language':'ru',
          'title':'Test','author':'Test','project_id':'test','chapters':[{'start':0,'end':2,'language':'ru','title':'One'}]}
    parent=studio.queue.enqueue(['К мосту.','Соседний фрагмент.'],spec)
    studio.queue.run(parent,lambda text,spec,path:wav(path),lambda:None)
    api=TestClient(app)
    api.headers['Authorization']='Bearer '+'t'*32
    return studio,parent,api


def test_take_roundtrip_isolated_persistent_and_reversible(setup,monkeypatch,tmp_path):
    studio,parent,api=setup
    original=studio.queue.state(parent)['chunks']
    hashes=[(studio.queue.cache/(c['cache_key']+'.wav')).read_bytes() for c in original]
    response=api.post(f'/jobs/{parent}/takes',json={'position':0,'text':'К мосту́.','count':3})
    assert response.status_code==200,response.text
    takes=response.json()['takes']
    assert len(takes)==4 and takes[0]['original']
    assert len({t['cache_key'] for t in takes})==4
    assert studio.queue.state(parent)['chunks']==original
    for take in takes[1:]:
        spec=json.loads(studio.queue.state(take['id'])['spec'])
        assert spec['voice_id']=='same-voice' and spec['auto_stress'] is False
        studio.queue.run(take['id'],lambda text,spec,path:wav(path),lambda:None)
    chosen=takes[1]
    route=f'/jobs/{parent}/choose-take'
    body={'position':0,'take':chosen['id'],'expected_key':original[0]['cache_key']}
    assert api.post(route,json=body).status_code==200
    changed=RenderQueue(studio.queue.root).state(parent)['chunks']
    assert changed[1]==original[1]
    assert changed[0]['cache_key']==chosen['cache_key']
    assert changed[0]['text']=='К мосту́.'
    assert api.post(route,json=body).status_code==400  # stale editor cannot replace silently
    # Export reads the chosen audio, without touching any previous export.
    previous=tmp_path/'old.mp3';previous.write_bytes(b'previous export')
    seen=[]
    def export(paths,destination,*args):
        seen.extend(paths);return destination
    monkeypatch.setattr('client.engine.service.export_mp3',export)
    studio.export_single_mp3(parent,str(tmp_path/'new.mp3'))
    assert seen[0].stem==chosen['cache_key'] and seen[1].stem==original[1]['cache_key']
    assert previous.read_bytes()==b'previous export'
    assert api.post(route,json={'position':0,'take':takes[0]['id'],'expected_key':chosen['cache_key']}).status_code==200
    assert studio.queue.state(parent)['chunks']==original
    assert hashes==[(studio.queue.cache/(c['cache_key']+'.wav')).read_bytes() for c in original]
    assert all(j['preview'] for j in studio.jobs() if j['repair_parent'])


def test_reject_incomplete_wrong_position_and_missing_audio(setup):
    studio,parent,api=setup
    state=studio.create_takes(parent,0,'К мосту.',1)
    take=state['takes'][1]
    key=state['chunks'][0]['cache_key']
    with pytest.raises(ValueError):studio.apply_take(parent,0,take['id'],key)
    studio.queue.run(take['id'],lambda text,spec,path:wav(path),lambda:None)
    with pytest.raises(ValueError):studio.apply_take(parent,1,take['id'],key)
    (studio.queue.cache/(take['cache_key']+'.wav')).unlink()
    with pytest.raises(ValueError):studio.apply_take(parent,0,take['id'],key)
    assert api.post(f'/jobs/{parent}/takes',json={'position':99,'text':'text'}).status_code==400
    assert api.post(f'/jobs/{parent}/takes',json={'position':0,'text':'  '}).status_code==400


def test_rules_atomic_persistent_exact_non_cascading(setup):
    studio,parent,api=setup
    rules={'к мосту':'к мосту́','мосту́':'INVALID'}
    assert api.post('/pronunciation',json={'rules':rules}).status_code==200
    assert api.get('/pronunciation').json()==rules
    assert api.post('/pronunciation/preview',json={'text':'к мосту'}).json()['text']=='к мосту́'
    assert json.loads((studio.root/'pronunciation.json').read_text(encoding='utf-8'))==rules
    assert prepare_text([Block('К мосту, к мосту, к мостуик.')],dictionary=rules)=='К мосту, к мосту́, к мостуик.'
    assert api.post('/pronunciation',json={'rules':{'':'bad'}}).status_code==400
    assert api.get('/pronunciation').json()==rules


def test_global_rules_merge_book_precedence(setup,monkeypatch):
    from client.engine.books import Book, Chapter
    studio,parent,api=setup
    book=Book('Test','Author','hash',[Block('к мосту')],[Chapter('Глава',0,1,'ru')],[])
    project=studio.library.save(book)
    studio.settings={'model_directory':'test','model_revision':'test','device':'cpu'}
    monkeypatch.setattr(studio.voices,'load',lambda id:(None,{'sha256':'test','transcript':'test'}))
    studio.save_pronunciation_rules({'к мосту':'к мосту́'})
    default=studio.enqueue(project,'voice',{})
    assert studio.queue.state(default)['chunks'][0]['text']=='к мосту́'
    override=studio.enqueue(project,'voice',{'dictionary':{'к мосту':'к мосту'}})
    assert studio.queue.state(override)['chunks'][0]['text']=='к мосту'
    assert studio.library.load(project).blocks[0].text=='к мосту'
