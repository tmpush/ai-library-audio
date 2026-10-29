from pathlib import Path
import pytest
from client.engine.books import import_book, validate_chapters

@pytest.mark.parametrize("filename,title", [("night-keeper.fb2", "Ночной смотритель"), ("lighthouse.epub", "Письма с маяка"), ("forest-route.pdf", "Маршрут через лес")])
def test_generated_samples(filename, title):
    book = import_book(Path(__file__).resolve().parents[1] / "samples" / filename)
    assert book.title == title
    assert len(book.chapters) == 2
    validate_chapters(book.chapters, len(book.blocks))
    assert all(block.text.strip() for block in book.blocks)
