import contextlib
import json
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS turns (
                  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, input_hash TEXT NOT NULL,
                  image_path TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                  transcription TEXT, answer TEXT, citations TEXT NOT NULL DEFAULT '[]',
                  error TEXT, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS turns_conversation ON turns(conversation_id,created);
                CREATE TABLE IF NOT EXISTS documents (
                  id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL,
                  path TEXT NOT NULL, reading_path TEXT NOT NULL, origin TEXT,
                  status TEXT NOT NULL DEFAULT 'queued', cloud_status TEXT NOT NULL DEFAULT 'waiting',
                  cloud_id TEXT, error TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS fragments (
                  id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                  label TEXT NOT NULL, text TEXT NOT NULL, embedding BLOB);
                CREATE INDEX IF NOT EXISTS fragment_document ON fragments(document_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS fragment_search USING fts5(text,content='fragments',content_rowid='id');
                CREATE TRIGGER IF NOT EXISTS fragment_add AFTER INSERT ON fragments BEGIN
                  INSERT INTO fragment_search(rowid,text) VALUES(new.id,new.text); END;
                CREATE TRIGGER IF NOT EXISTS fragment_del AFTER DELETE ON fragments BEGIN
                  INSERT INTO fragment_search(fragment_search,rowid,text) VALUES('delete',old.id,old.text); END;
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL, payload TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                  due REAL NOT NULL DEFAULT 0, lease REAL NOT NULL DEFAULT 0, error TEXT);
                CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status,kind,due);
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(turns)")}
            for name, definition in (("page_capture", "INTEGER NOT NULL DEFAULT 0"),
                                     ("page_context", "TEXT")):
                if name not in columns:
                    db.execute(f"ALTER TABLE turns ADD COLUMN {name} {definition}")

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, job_id, kind, target, payload=None, db=None):
        args = (job_id, kind, target, json.dumps(payload or {}, ensure_ascii=False))
        sql = "INSERT OR IGNORE INTO jobs(id,kind,target,payload) VALUES(?,?,?,?)"
        if db is not None:
            db.execute(sql, args)
        else:
            with self.connect() as db:
                db.execute(sql, args)

    def claim(self, kinds: tuple[str, ...]):
        now = time.time()
        marks = ",".join("?" for _ in kinds)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                f"SELECT * FROM jobs WHERE kind IN ({marks}) AND "
                "((status='queued' AND due<=?) OR (status='processing' AND lease<?)) "
                "ORDER BY due,rowid LIMIT 1",
                (*kinds, now, now),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE jobs SET status='processing',lease=?,attempts=attempts+1 WHERE id=?",
                (now + 180, row["id"]),
            )
            job = dict(row)
            job["payload"] = json.loads(job["payload"])
            job["attempts"] += 1
            return job

    def renew(self, job_id):
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET lease=? WHERE id=? AND status='processing'",
                (time.time() + 180, job_id),
            )

    def finish(self, job_id):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='complete',lease=0,error=NULL WHERE id=?", (job_id,))

    def defer(self, job, message, delay, terminal=False, configured=True):
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status=?,lease=0,due=?,error=?,attempts=attempts-? WHERE id=?",
                (
                    "error" if terminal else "queued",
                    time.time() + delay,
                    message,
                    0 if configured else 1,
                    job["id"],
                ),
            )
            if job["kind"] == "turn":
                db.execute(
                    "UPDATE turns SET status=?,error=?,updated=? WHERE id=?",
                    (
                        "error" if terminal else "queued",
                        message,
                        time.time(),
                        job["target"],
                    ),
                )
            if job["kind"] in ("index", "publish"):
                column = "status" if job["kind"] == "index" else "cloud_status"
                db.execute(
                    f"UPDATE documents SET {column}=?,error=? WHERE id=?",
                    (
                        "error" if terminal else "waiting",
                        message,
                        job["target"],
                    ),
                )

    def turn(self, turn_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item.pop("image_path")
        item.pop("input_hash")
        item.pop("page_context", None)
        item["citations"] = json.loads(item["citations"])
        return item

    def history(self, conversation_id, before, limit=6):
        with self.connect() as db:
            rows = db.execute(
                "SELECT transcription,answer FROM turns WHERE conversation_id=? "
                "AND status='complete' AND created<? ORDER BY created DESC LIMIT ?",
                (conversation_id, before, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def documents(self):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,title,kind,origin,status,cloud_status,cloud_id,error,created FROM documents ORDER BY created DESC"
                )
            ]

    def state(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_state(self, key, value, db=None):
        sql = "INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        if db is not None:
            db.execute(sql, (key, str(value)))
        else:
            with self.connect() as db:
                db.execute(sql, (key, str(value)))
