import pytest
from client.engine.books import Block, Book, Chapter, prepare_text, spoken_blocks, split_chapter, merge_chapters, chunk_text, detect_chapters
from client.engine.storage import Library
from client.engine.models import ModelDownload, model_info
import httpx


def test_manual_text_survives_boundaries_and_restart(tmp_path):
    book=Book('Книга','','hash',[Block('Начало'),Block('Анна приехала.'),Block('Конец')],[Chapter('Глава',0,3)])
    book.spoken_overrides['1']='А́нна [[pause:500]] приехала.'
    split_chapter(book,0,1,'Вторая')
    merge_chapters(book,0)
    library=Library(tmp_path)
    restored=library.load(library.save(book))
    assert restored.blocks[1].text=='Анна приехала.'
    spoken=prepare_text(spoken_blocks(restored,0,3))
    assert '[[pause:500]]' in chunk_text(spoken)
    assert 'А́нна' in spoken
    assert 'Анна' in restored.blocks[1].text


def test_furniture_is_optional_and_original_is_preserved():
    blocks=[Block('Название','header'),Block('Текст'),Block('12','page_number')]
    assert prepare_text(blocks)=='Название\n\nТекст\n\n12'
    assert prepare_text(blocks,remove_page_furniture=True)=='Текст'
    assert blocks[0].text=='Название'


def test_pause_bounds():
    assert chunk_text('До [[pause:1000]] после')==['До','[[pause:1000]]','после']
    with pytest.raises(ValueError):chunk_text('[[pause:9999999]]')


def test_language_per_chapter():
    blocks=[Block('Chapter one','heading'),Block('The old library stood near the river. Every evening the librarian opened the door and welcomed visitors who wanted to read a book.'),Block('Глава вторая','heading'),Block('Старая библиотека стояла на берегу реки. Каждый вечер библиотекарь открывал двери и встречал читателей, которые приходили за новой книгой.')]
    assert [c.language for c in detect_chapters(blocks)]==['en','ru']


def test_model_download_requires_explicit_confirmation(tmp_path):
    with pytest.raises(ValueError,match='Подтвердите'):
        ModelDownload().start(tmp_path,'a'*40,123,False)
    assert not list(tmp_path.iterdir())


def test_model_manifest_validation():
    def get(req):return httpx.Response(200,json={'sha':'a'*40,'siblings':[{'rfilename':'../outside','size':5}]})
    with httpx.Client(transport=httpx.MockTransport(get)) as client:
        with pytest.raises(ValueError,match='путь'):model_info(client)


def test_model_replacement_invalidates_saved_revision(tmp_path):
    from client.engine.service import Studio
    model=tmp_path/'model';model.mkdir()
    (model/'config.json').write_text('{}')
    (model/'model.safetensors').write_bytes(b'test checkpoint')
    studio=Studio(tmp_path,Library(tmp_path))
    settings=studio.configure(str(model),'cpu')
    studio.verify_model(settings)
    (model/'model.safetensors').write_bytes(b'different checkpoint')
    with pytest.raises(ValueError,match='изменились'):studio.verify_model(settings)
