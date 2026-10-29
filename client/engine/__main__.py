import argparse
import json
from pathlib import Path
from .books import import_book, prepare_text, chunk_text
from .storage import Library

parser = argparse.ArgumentParser(description="Локальная библиотека книг")
parser.add_argument("book", type=Path)
parser.add_argument("--data", type=Path, default=Path("data"))
args = parser.parse_args()
book = import_book(args.book)
project_id = Library(args.data).save(book)
print(json.dumps({"id": project_id, "title": book.title, "chapters": len(book.chapters),
                  "blocks": len(book.blocks), "chunks": len(chunk_text(prepare_text(book.blocks))),
                  "warnings": book.warnings}, ensure_ascii=False, indent=2))
