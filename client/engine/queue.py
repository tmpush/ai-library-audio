"""Persistent single-worker queue. Production caller must supply license checks."""
import hashlib
import json
import sqlite3
import time
import uuid
import wave
from pathlib import Path


class RenderQueue:
    def __init__(self, directory: Path):
        self.root = directory
        self.cache = directory / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.path = directory / "queue.db"
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, spec TEXT NOT NULL, status TEXT NOT NULL, error TEXT);
              CREATE TABLE IF NOT EXISTS chunks(job TEXT NOT NULL, position INTEGER NOT NULL, text TEXT NOT NULL,
                cache_key TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, seconds REAL, audio_seconds REAL,
                PRIMARY KEY(job,position));
            ''')

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def enqueue(self, texts: list[str], spec: dict):
        # spec must include immutable model revision, voice content hash and all synthesis settings.
        if not texts or not all(t.strip() for t in texts):
            raise ValueError("Нет текста для синтеза")
        if not all(spec.get(k) for k in ("model_revision", "voice_hash", "language")):
            raise ValueError("Не указаны модель, отпечаток голоса или язык")
        identifier = str(uuid.uuid4())
        canonical = json.dumps(spec, sort_keys=True, ensure_ascii=False)
        with self.connect() as db:
            db.execute("INSERT INTO jobs VALUES (?,?,?,NULL)", (identifier, canonical, "queued"))
            for i, text in enumerate(texts):
                chunk_spec = self.chunk_spec(spec, i)
                key = hashlib.sha256((json.dumps(chunk_spec, sort_keys=True, ensure_ascii=False) + "\0" + text).encode()).hexdigest()
                db.execute("INSERT INTO chunks(job,position,text,cache_key) VALUES (?,?,?,?)", (identifier, i, text, key))
        return identifier

    @staticmethod
    def chunk_spec(spec, position):
        result = dict(spec)
        for chapter in spec.get('chapters', []):
            if chapter['start'] <= position < chapter['end']:
                result['language'] = chapter['language']
                break
        return result

    def status(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT status FROM jobs WHERE id=?', (identifier,)).fetchone()
        if row is None: raise KeyError(identifier)
        return row[0]

    def state(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError(identifier)
            chunks = [dict(r) for r in db.execute("SELECT * FROM chunks WHERE job=? ORDER BY position", (identifier,))]
        return {**dict(row), "chunks": chunks, "completed": sum(c["done"] for c in chunks), "total": len(chunks)}

    def list(self):
        with self.connect() as db:
            identifiers = [r[0] for r in db.execute("SELECT id FROM jobs ORDER BY rowid DESC")]
        return [self.state(identifier) for identifier in identifiers]

    def summaries(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('''
              SELECT j.id,j.spec,j.status,j.error,COUNT(c.position) AS total,
                COALESCE(SUM(c.done),0) AS completed,COALESCE(SUM(c.seconds),0) AS compute_seconds,
                COALESCE(SUM(c.audio_seconds),0) AS audio_seconds,
                COALESCE(SUM(CASE WHEN c.seconds>0 THEN c.audio_seconds ELSE 0 END),0) AS measured_audio_seconds
              FROM jobs j LEFT JOIN chunks c ON c.job=j.id GROUP BY j.id ORDER BY j.rowid DESC
            ''')]

    def delete(self, identifier):
        # The owning Studio serializes this against its worker, including a paused in-flight chunk.
        with self.connect() as db:
            row=db.execute('SELECT status FROM jobs WHERE id=?',(identifier,)).fetchone()
            if row is None:raise KeyError(identifier)
            if row[0]=='running':raise ValueError('Сначала приостановите озвучку')
            keys={r[0] for r in db.execute('SELECT cache_key FROM chunks WHERE job=?',(identifier,))}
            db.execute('DELETE FROM chunks WHERE job=?',(identifier,))
            db.execute('DELETE FROM jobs WHERE id=?',(identifier,))
            unused=[key for key in keys if not db.execute('SELECT 1 FROM chunks WHERE cache_key=? LIMIT 1',(key,)).fetchone()]
        for key in unused:
            (self.cache/(key+'.wav')).unlink(missing_ok=True)

    def pause(self, identifier):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='paused' WHERE id=? AND status IN ('queued','running')", (identifier,))

    def resume(self, identifier):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='queued',error=NULL WHERE id=? AND status IN ('paused','failed')", (identifier,))

    def recover(self):
        # Call only after acquiring exclusive ownership of the worker process.
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='paused' WHERE status='running'")

    @staticmethod
    def audio_duration(path):
        try:
            with wave.open(str(path), "rb") as wav:
                if wav.getnframes() == 0:
                    return 0
                expected = wav.getnframes() * wav.getnchannels() * wav.getsampwidth()
                if len(wav.readframes(wav.getnframes())) != expected:
                    return 0
                return wav.getnframes() / wav.getframerate()
        except (OSError, wave.Error, EOFError):
            return 0

    def run(self, identifier, synthesize, authorize, preflight=None):
        """synthesize(text,spec,path); authorize() raises on an invalid license."""
        with self.connect() as db:
            changed = db.execute("UPDATE jobs SET status='running',error=NULL WHERE id=? AND status='queued'", (identifier,)).rowcount
        if not changed:
            return
        try:
            state = self.state(identifier)
            spec = json.loads(state["spec"])
            authorize()
            if preflight: preflight(spec)
            for chunk in state["chunks"]:
                if self.status(identifier) != "running":
                    return
                authorize()
                output = self.cache / (chunk["cache_key"] + ".wav")
                duration = self.audio_duration(output)
                # Preserve measured work when resuming this job; another job's cache hit costs zero.
                elapsed = (chunk['seconds'] or 0.0) if chunk['done'] else 0.0
                if not duration:
                    temp = self.cache / (chunk["cache_key"] + "." + identifier + ".tmp.wav")
                    start = time.perf_counter()
                    try:
                        chunk_spec = self.chunk_spec(spec, chunk['position'])
                        synthesize(chunk["text"], chunk_spec, temp)
                        elapsed = time.perf_counter() - start
                        duration = self.audio_duration(temp)
                        if not duration:
                            raise ValueError("Модель вернула пустой или повреждённый WAV")
                        temp.replace(output)
                    finally:
                        temp.unlink(missing_ok=True)
                with self.connect() as db:
                    db.execute("UPDATE chunks SET done=1,seconds=?,audio_seconds=? WHERE job=? AND position=?", (elapsed, duration, identifier, chunk["position"]))
            with self.connect() as db:
                db.execute("UPDATE jobs SET status='done' WHERE id=? AND status='running'", (identifier,))
        except Exception as exc:
            with self.connect() as db:
                db.execute("UPDATE jobs SET status='failed',error=? WHERE id=?", (str(exc), identifier))
            raise
