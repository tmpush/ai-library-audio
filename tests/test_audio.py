import math
import struct
import wave
from pathlib import Path
import pytest
from client.engine.voices import inspect_voice, VoiceLibrary
from client.engine.queue import RenderQueue


def tone(path, seconds=1, silent=False):
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1,2,16000,0,"NONE","not compressed"))
        wav.writeframes(b"".join(struct.pack('<h', 0 if silent else int(5000*math.sin(i*.1))) for i in range(16000*seconds)))

def test_voice_validation_and_library(tmp_path):
    sample=tmp_path/'voice.wav'
    tone(sample,15)
    assert inspect_voice(sample)['duration']==15
    library=VoiceLibrary(tmp_path/'voices')
    voice=library.add(sample,'Тест','Образец')
    assert library.load(voice['id'])[0].exists()
    tone(sample,15,True)
    with pytest.raises(ValueError): inspect_voice(sample)
    tone(sample,3)
    with pytest.raises(ValueError): inspect_voice(sample)

def test_queue_resume_cache_and_authorization(tmp_path):
    queue=RenderQueue(tmp_path)
    spec=dict(model_revision='test-rev',voice_hash='test-voice',language='ru')
    identifier=queue.enqueue(['Первый','Второй','Третий'],spec)
    calls=[]
    def render(text,spec,path):
        calls.append(text)
        if text=='Второй': raise RuntimeError('simulated crash')
        tone(path)
    with pytest.raises(RuntimeError): queue.run(identifier,render,lambda:None)
    assert queue.state(identifier)['completed']==1
    queue=RenderQueue(tmp_path)
    queue.resume(identifier)
    def success(text,spec,path):
        calls.append(text);tone(path)
    queue.run(identifier,success,lambda:None)
    assert queue.state(identifier)['status']=='done'
    assert calls.count('Первый')==1
    second=queue.enqueue(['Первый'],spec)
    queue.run(second,lambda *args:pytest.fail('Cache should be reused'),lambda:None)
    third=queue.enqueue(['Секрет'],spec)
    def denied(): raise ValueError('License expired')
    with pytest.raises(ValueError): queue.run(third,lambda *args:pytest.fail('Unauthorized synth'),denied)
    assert queue.state(third)['completed']==0

def test_pause_resume(tmp_path):
    queue=RenderQueue(tmp_path)
    job=queue.enqueue(['Раз','Два'],dict(model_revision='test',voice_hash='voice',language='ru'))
    def render(text,spec,path):
        tone(path);queue.pause(job)
    queue.run(job,render,lambda:None)
    assert queue.state(job)['status']=='paused'
    assert queue.state(job)['completed']==1
    queue.resume(job)
    queue.run(job,lambda text,spec,path:tone(path),lambda:None)
    assert queue.state(job)['status']=='done'


def test_identical_text_in_different_languages_is_not_shared(tmp_path):
    queue=RenderQueue(tmp_path)
    spec=dict(model_revision='rev',voice_hash='voice',language='ru',chapters=[
        dict(start=0,end=1,language='ru'),dict(start=1,end=2,language='en')])
    job=queue.enqueue(['Test','Test'],spec)
    languages=[]
    def render(text,spec,path):
        languages.append(spec['language']);tone(path)
    queue.run(job,render,lambda:None)
    assert languages==['ru','en']
    assert len({c['cache_key'] for c in queue.state(job)['chunks']})==2


def test_long_chapter_resume_and_corrupt_cache(tmp_path):
    from client.engine.books import chunk_text
    text=' '.join(f'Смотритель открыл дверь и увидел письмо номер {i}. На улице шёл дождь.' for i in range(200))
    assert 10000<len(text)<15000
    texts=chunk_text(text)
    queue=RenderQueue(tmp_path)
    job=queue.enqueue(texts,dict(model_revision='rev',voice_hash='voice',language='ru'))
    calls=[]
    def render(text,spec,path):
        calls.append(text);tone(path)
        if len(calls)==3:queue.pause(job)
    queue.run(job,render,lambda:None)
    first=queue.state(job)
    assert first['status']=='paused' and first['completed']==3
    saved_seconds=first['chunks'][0]['seconds']
    damaged=queue.cache/(first['chunks'][1]['cache_key']+'.wav')
    damaged.write_bytes(b'broken')
    queue=RenderQueue(tmp_path)
    queue.recover();queue.resume(job)
    queue.run(job,lambda text,spec,path:tone(path),lambda:None)
    final=queue.state(job)
    assert final['status']=='done' and final['completed']==len(texts)
    assert final['chunks'][0]['seconds']==saved_seconds
    assert queue.audio_duration(damaged)==1


def test_preflight_runs_even_for_cached_audio(tmp_path):
    queue=RenderQueue(tmp_path)
    spec=dict(model_revision='rev',voice_hash='voice',language='ru')
    job=queue.enqueue(['Текст'],spec)
    queue.run(job,lambda text,spec,path:tone(path),lambda:None)
    second=queue.enqueue(['Текст'],spec)
    def changed(spec):raise ValueError('model changed')
    with pytest.raises(ValueError,match='model changed'):
        queue.run(second,lambda *args:pytest.fail('must not render'),lambda:None,changed)
    assert queue.state(second)['status']=='failed'


def test_deleting_job_keeps_cache_referenced_by_other_jobs(tmp_path):
    queue=RenderQueue(tmp_path)
    spec=dict(model_revision='rev',voice_hash='voice',language='ru')
    first=queue.enqueue(['Общий текст'],spec)
    second=queue.enqueue(['Общий текст'],spec)
    queue.run(first,lambda text,spec,path:tone(path),lambda:None)
    queue.delete(first)
    assert len(list(queue.cache.glob('*.wav')))==1
    queue.run(second,lambda *args:pytest.fail('cache was deleted'),lambda:None)
    queue.delete(second)
    assert not list(queue.cache.glob('*.wav'))


def test_invalid_wav_and_zero_sample_rate_are_rejected(tmp_path):
    sample=tmp_path/'bad.wav';sample.write_bytes(b'')
    with pytest.raises(ValueError,match='WAV'):inspect_voice(sample)
    tone(sample,15)
    data=bytearray(sample.read_bytes());data[24:28]=b'\0'*4;sample.write_bytes(data)
    with pytest.raises(ValueError,match='WAV'):inspect_voice(sample)
