import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from .errors import AppError


class Database:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS searches (
                    id TEXT PRIMARY KEY, image_hash TEXT NOT NULL,
                    parameters TEXT NOT NULL, source TEXT NOT NULL,
                    result TEXT NOT NULL, created_at TEXT NOT NULL,
                    image_bytes BLOB, mime TEXT
                );
                CREATE TABLE IF NOT EXISTS url_images (
                    url TEXT PRIMARY KEY, image_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage (
                    period TEXT NOT NULL, bucket TEXT NOT NULL,
                    searches INTEGER NOT NULL DEFAULT 0,
                    requests INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (period, bucket)
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                    outcome TEXT NOT NULL DEFAULT 'reserved'
                );
                CREATE TABLE IF NOT EXISTS lease (
                    id INTEGER PRIMARY KEY CHECK (id=1),
                    owner TEXT NOT NULL, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY, search_id TEXT,
                    error TEXT, status INTEGER
                );
            """)

    @contextmanager
    def connect(self, write=False):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA secure_delete=ON")
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def search_lock(self):
        # Shared across threads AND Python processes, without holding a SQLite
        # transaction open during the network request. Crashes expire in 10 min.
        owner = uuid.uuid4().hex
        with self.connect(write=True) as conn:
            row = conn.execute("SELECT * FROM lease WHERE id=1").fetchone()
            if row and row["expires"] > time.time():
                raise AppError("busy", "Another search is running. Wait for it to finish; no new request was sent.", 409)
            conn.execute("INSERT OR REPLACE INTO lease VALUES (1, ?, ?)", (owner, time.time() + 600))
        try:
            yield
        finally:
            with self.connect(write=True) as conn:
                conn.execute("DELETE FROM lease WHERE owner=?", (owner,))

    def get(self, search_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM searches WHERE id=?", (search_id,)).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "image_hash": row["image_hash"],
                "parameters": json.loads(row["parameters"]), "source": json.loads(row["source"]),
                "result": json.loads(row["result"]), "created_at": row["created_at"],
                "image_bytes": row["image_bytes"], "mime": row["mime"]}

    def put(self, entry, image_bytes=None, mime=None, alias=None):
        with self.connect(write=True) as conn:
            conn.execute("""INSERT OR REPLACE INTO searches
                (id, image_hash, parameters, source, result, created_at, image_bytes, mime)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (
                entry["id"], entry["image_hash"], json.dumps(entry["parameters"]),
                json.dumps(entry["source"]), json.dumps(entry["result"]), entry["created_at"], image_bytes, mime))
            if alias:
                conn.execute("INSERT OR REPLACE INTO url_images VALUES (?, ?)", (alias, entry["image_hash"]))

    def url_hash(self, url):
        with self.connect() as conn:
            row = conn.execute("SELECT image_hash FROM url_images WHERE url=?", (url,)).fetchone()
            return row[0] if row else None

    def set_url_hash(self, url, image_hash):
        with self.connect(write=True) as conn:
            conn.execute("INSERT OR REPLACE INTO url_images VALUES (?, ?)", (url, image_hash))

    def update_source(self, search_id, source, alias=None):
        with self.connect(write=True) as conn:
            conn.execute("UPDATE searches SET source=? WHERE id=?", (json.dumps(source), search_id))
            if alias:
                conn.execute("INSERT OR REPLACE INTO url_images VALUES (?, (SELECT image_hash FROM searches WHERE id=?))", (alias, search_id))

    def history(self):
        with self.connect() as conn:
            rows = conn.execute("""SELECT id, source, parameters, created_at,
                json_array_length(json_extract(result, '$.results')) AS count
                FROM searches ORDER BY created_at DESC""").fetchall()
        return [{**dict(row), "source": json.loads(row["source"]),
                 "parameters": json.loads(row["parameters"])} for row in rows]

    def operation(self, operation_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
        return dict(row) if row else None

    def finish_operation(self, operation_id, search_id=None, error=None):
        with self.connect(write=True) as conn:
            conn.execute("INSERT OR REPLACE INTO operations VALUES (?, ?, ?, ?)", (
                operation_id, search_id, json.dumps(error.as_dict()) if error else None,
                error.status if error else None))

    def clear_history(self):
        with self.search_lock(), self.connect(write=True) as conn:
            for table in ("searches", "url_images", "operations"):
                conn.execute(f"DELETE FROM {table}")  # fixed identifiers only
        # usage and credential-free attempt records deliberately survive.
