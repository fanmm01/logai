"""Small transactional document store for resumable browser jobs and tournaments."""
import json
import sqlite3
import threading
from contextlib import contextmanager


class Store:
    def __init__(self, path):
        self.path, self.lock = str(path), threading.RLock()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, body TEXT, PRIMARY KEY(kind,id))')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, kind, value):
        with self.lock, self.connect() as db:
            db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)',
                       (kind, value['id'], json.dumps(value, ensure_ascii=False)))

    def get(self, kind, identifier):
        with self.connect() as db:
            row = db.execute('SELECT body FROM records WHERE kind=? AND id=?', (kind, identifier)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, kind):
        with self.connect() as db:
            rows = db.execute('SELECT body FROM records WHERE kind=? ORDER BY rowid DESC', (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]
