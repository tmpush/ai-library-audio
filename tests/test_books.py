from pathlib import Path
import zipfile

import pytest
from client.engine.books import (Block, Book, Chapter, import_book, prepare_text,
                                 chunk_text, split_chapter, merge_chapters, validate_chapters)
from client.engine.storage import Library


def test_fb2_text_and_notes(tmp_path):
    path = tmp_path / "book.fb2"
    path.write_text('''<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info><book-title>Книга</book-title><author><first-name>Анна</first-name><last-name>Мир</last-name></author></title-info></description><body><section><title><p>Глава 1</p></title><p>Это <emphasis>важный</emphasis> текст.</p></section><section><title><p>Глава 2</p></title><p>Продолжение.</p></section></body><body name="notes"><section><p>Примечание.</p></section></body></FictionBook>''', encoding="utf-8")
    book = import_book(path)
    assert book.title == "Книга"
    assert "важный текст" in book.blocks[1].text
    assert len(book.chapters) == 2
    assert "Примечание" not in prepare_text(book.blocks, remove_notes=True)
    assert "Примечание" in prepare_text(book.blocks)
    original = book.to_dict()
    prepare_text(book.blocks, numbers=True)
    assert original == book.to_dict()


def test_epub_reads_spine_order_and_inline_spacing(tmp_path):
    path = tmp_path / "book.epub"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        z.writestr("OPS/book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Порядок</dc:title></metadata><manifest><item id="a" href="a.xhtml"/><item id="b" href="b.xhtml"/></manifest><spine><itemref idref="b"/><itemref idref="a"/></spine></package>')
        z.writestr("OPS/a.xhtml", '<html><body><h1>Вторая</h1><p>Конец.</p></body></html>')
        z.writestr("OPS/b.xhtml", '<html><body><h1>Первая</h1><p>Слово <b>жирное</b>, рядом.</p></body></html>')
    book = import_book(path)
    assert [c.title for c in book.chapters] == ["Первая", "Вторая"]
    assert book.blocks[1].text == "Слово жирное, рядом."


def test_xml_entities_rejected(tmp_path):
    path = tmp_path / "bad.fb2"
    path.write_text('<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///etc/passwd">]><foo>&x;</foo>')
    with pytest.raises(Exception):
        import_book(path)


def test_split_merge_and_persistence(tmp_path):
    book = Book("Тест", "", "abc", [Block(str(n)) for n in range(5)], [Chapter("Глава", 0, 5)])
    split_chapter(book, 0, 2, "Вторая")
    validate_chapters(book.chapters, 5)
    lib = Library(tmp_path)
    project = lib.save(book)
    restored = Library(tmp_path).load(project)
    assert restored.to_dict() == book.to_dict()
    merge_chapters(restored, 0)
    assert restored.chapters[0].end == 5
    assert restored.blocks == book.blocks
    with pytest.raises(ValueError):
        split_chapter(restored, 0, 5, "Нельзя")
    lib.delete(project)
    assert lib.list() == []


@pytest.mark.parametrize("text", ["Слово. " * 100, "длинноеслово" * 100, "Абзац\n\n" * 100])
def test_chunks_preserve_content_and_limit(text):
    parts = chunk_text(text, 80)
    assert all(0 < len(p) <= 80 for p in parts)
    assert "".join("".join(parts).split()) == "".join(text.split())


def test_number_and_dictionary():
    text = prepare_text([Block("У Анны 12 книг. Дата 12.10.2026, дробь 1/2.")], numbers=True, dictionary={"Анны": "А́нны"})
    assert "двенадцать книг" in text
    assert "двенадцатое октября две тысячи двадцать шестого года" in text and "одна вторая" in text
    assert "А́нны" in text


def test_invalid_boundaries():
    with pytest.raises(ValueError):
        validate_chapters([Chapter("A", 0, 2), Chapter("B", 3, 4)], 4)


def test_epub_keeps_div_text_around_paragraphs_and_line_breaks(tmp_path):
    path=tmp_path/'divs.epub'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('META-INF/container.xml','<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
        z.writestr('book.opf','<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/></spine></package>')
        z.writestr('a.xhtml','<html><body><div>До <b>начала</b><p>Одна<br/>строка</p>После <span>конца.</span></div><aside epub:type="footnote"><p>Сноска.</p></aside></body></html>')
    book=import_book(path)
    assert [b.text for b in book.blocks]==['До начала','Одна строка','После конца.','Сноска.']
    assert book.blocks[-1].kind=='note'
    assert 'Сноска' not in prepare_text(book.blocks,remove_notes=True)


def test_numbers_use_chapter_language_and_keep_dates_and_pauses():
    assert prepare_text([Block('There are 12.')],numbers=True,language='en')=='There are twelve.'
    text=prepare_text([Block('Их 12, а было 5. Дата 12.10.2026; дробь 1/2; цена 1,25; [[pause:500]]')],numbers=True)
    assert text=='Их двенадцать, а было пять. Дата двенадцатое октября две тысячи двадцать шестого года; дробь одна вторая; цена одна целая двадцать пять сотых; [[pause:500]]'
    with pytest.raises(ValueError,match='не поддерживаются'):
        prepare_text([Block('12')],numbers=True,language='ja')


def test_pdf_without_text_and_encrypted_pdf_report_clear_errors(tmp_path):
    from pypdf import PdfWriter
    path=tmp_path/'blank.pdf'
    writer=PdfWriter();writer.add_blank_page(width=100,height=100)
    writer.write(path)
    with pytest.raises(ValueError,match='OCR'):import_book(path)
    writer.encrypt('test-password');writer.write(path)
    with pytest.raises(ValueError,match='паролем'):import_book(path)


def test_epub_duplicate_paths_are_rejected(tmp_path):
    path=tmp_path/'duplicate.epub'
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr('a','one')
        with pytest.warns(UserWarning):archive.writestr('a','two')
    with pytest.raises(ValueError,match='повторяющиеся'):import_book(path)
