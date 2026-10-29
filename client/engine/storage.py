import json
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime, timezone

from .books import Book, Block, Chapter, validate_chapters


class Library:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "library.db"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, title TEXT NOT NULL, book TEXT NOT NULL, updated TEXT NOT NULL)")

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def save(self, book: Book, project_id: str | None = None):
        validate_chapters(book.chapters, len(book.blocks))
        project_id = project_id or str(uuid.uuid4())
        with self.connect() as db:
            db.execute("INSERT INTO projects VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title, book=excluded.book, updated=excluded.updated",
                       (project_id, book.title, json.dumps(book.to_dict(), ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
        return project_id

    def list(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,title,updated FROM projects ORDER BY updated DESC")]

    def load(self, project_id: str):
        with self.connect() as db:
            row = db.execute("SELECT book FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(project_id)
        value = json.loads(row[0])
        value["blocks"] = [Block(**block) for block in value["blocks"]]
        value["chapters"] = [Chapter(**chapter) for chapter in value["chapters"]]
        return Book(**value)

    def delete(self, project_id: str):
        with self.connect() as db:
            db.execute("DELETE FROM projects WHERE id=?", (project_id,))
