import hashlib
import wave
import pytest
from client.engine.books import Block, prepare_text
from client.engine.pronunciation import normalize_symbols
from client.engine.service import Studio
from client.engine.storage import Library
from client.engine.tts import is_cuda_oom
from client.engine.books import detect_chapters, import_book


def test_variables_are_spoken_only_in_explicit_context():
    source='Большинство считают, что X, но истина противоположна Х. Переменная Y и значение Z.'
    assert normalize_symbols(source)=='Большинство считают, что икс, но истина противоположна икс. Переменная игрек и значение зет.'
    unchanged='Глава X. X век. Генрих X. X.com. https://x.com/Х. Хабаровск. X-ray. Что Xbox? Что X-ray? Значение X.com.'
    assert normalize_symbols(unchanged)==unchanged
    assert normalize_symbols(source,'en')==source


def test_dictionary_wins_and_source_is_preserved():
    block=Block('Считают, что X. Истина противоположна Х.')
    assert prepare_text([block], dictionary={'X':'экс','Х':'Х'})=='Считают, что экс. Истина противоположна Х.'
    assert block.text=='Считают, что X. Истина противоположна Х.'
    assert prepare_text([block])=='Считают, что икс. Истина противоположна икс.'
    assert prepare_text([Block('к мосту')],dictionary={'к мосту':'к мосту́','мосту́':'wrong'})=='к мосту́'


def test_short_volume_headings_inherit_language():
    blocks=[Block('Русский текст о том, как люди читают книги и обсуждают разные истории из жизни.'),
            Block('ТОМ II','heading'),Block('ЧАСТЬ ТРЕТЬЯ','heading'),Block('Начинается новая история о библиотеке и её читателях, которые приходили каждый вечер.')]
    assert all(c.language=='ru' for c in detect_chapters(blocks))


def test_legacy_epub_numbered_notes_do_not_become_chapters(tmp_path):
    import zipfile
    path=tmp_path/'notes.epub'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('META-INF/container.xml','<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
        z.writestr('book.opf','<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/></spine></package>')
        z.writestr('a.xhtml','<html><body><h1>Глава</h1><p>Основной текст.</p><div id="note123"><h2>1</h2><p>Пояснение.</p></div></body></html>')
    b=import_book(path)
    assert len(b.chapters)==1
    assert all(x.kind=='note' for x in b.blocks[-2:])
    assert 'Пояснение' not in prepare_text(b.blocks,remove_notes=True)


def test_cuda_failure_preserves_queue_and_can_resume(tmp_path,monkeypatch):
    studio=Studio(tmp_path,Library(tmp_path))
    queue=studio.queue
    job=queue.enqueue(['Первый','Второй'],dict(model_revision='r',voice_hash='v',language='ru'))
    def tone(path):
        with wave.open(str(path),'wb') as out:
            out.setparams((1,2,24000,0,'NONE','not compressed'));out.writeframes(b'\0\0'*2400)
    calls=[]
    def fail(text,spec,path):
        calls.append(text)
        if text=='Второй':raise RuntimeError('CUDA out of memory. Tried to allocate 20 MiB')
        tone(path)
    monkeypatch.setattr(studio,'_render',fail)
    studio.engine=object();studio.engine_key=('stale',)
    with pytest.raises(ValueError,match='VRAM'):queue.run(job,studio.render,lambda:None)
    state=queue.state(job)
    assert state['completed']==1 and state['status']=='failed'
    assert 'Продолжить' in state['error'] and studio.engine is None and studio.engine_key is None
    first=queue.cache/(state['chunks'][0]['cache_key']+'.wav')
    digest=hashlib.sha256(first.read_bytes()).hexdigest()
    assert not list(queue.cache.glob('*.tmp.wav'))
    monkeypatch.setattr(studio,'_render',lambda text,spec,path:tone(path))
    queue.resume(job);queue.run(job,studio.render,lambda:None)
    assert queue.state(job)['status']=='done'
    assert hashlib.sha256(first.read_bytes()).hexdigest()==digest
    assert not is_cuda_oom(MemoryError('host memory'))
    assert not is_cuda_oom(RuntimeError('model not found'))
