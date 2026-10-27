"""SQLite history for generation jobs (docs/JOBS.md). One row per job; the whole job is kept as JSON,
with the columns the list API filters on alongside it."""
import json
import os
import sqlite3
import threading
import time

RETENTION_S = 90 * 24 * 3600
ACTIVE = ("preparing", "queued", "running", "publishing")


def default_path():
    return os.environ.get("JOBS_DB") or os.path.expanduser("~/.local/state/screen-machine/jobs.db")


class JobStore:
    def __init__(self, path=None):
        self.path = path or default_path()
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS jobs ("
                " id TEXT PRIMARY KEY, created_at REAL NOT NULL, status TEXT NOT NULL,"
                " target TEXT, source TEXT, prompt TEXT, data TEXT NOT NULL)")
            self._db.execute("CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created_at)")
            self._db.execute("CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status)")

    def upsert(self, job):
        row = (job["id"], job["created_at"], job["status"], job.get("target"), job.get("source"),
               job.get("prompt") or "", json.dumps(job, default=str))
        with self._lock:
            self._db.execute(
                "INSERT INTO jobs (id, created_at, status, target, source, prompt, data)"
                " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET"
                " status=excluded.status, target=excluded.target, source=excluded.source,"
                " prompt=excluded.prompt, data=excluded.data", row)

    def get(self, job_id):
        with self._lock:
            row = self._db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def query(self, state="all", limit=50, before=None, target=None, source=None, q=None):
        where, args = [], []
        marks = ",".join("?" * len(ACTIVE))
        if state == "active":
            where.append(f"status IN ({marks})")
            args += ACTIVE
        elif state == "finished":
            where.append(f"status NOT IN ({marks})")
            args += ACTIVE
        if before is not None:
            where.append("created_at < ?")
            args.append(before)
        if target:
            where.append("target = ?")
            args.append(target)
        if source:
            where.append("source = ?")
            args.append(source)
        if q:
            where.append("prompt LIKE ? ESCAPE '\\'")
            args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        sql = "SELECT data FROM jobs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [json.loads(r[0]) for r in rows]

    def active(self):
        return self.query(state="active", limit=10_000)

    def prune(self, now=None, retention_s=RETENTION_S):
        """Drop jobs created more than retention_s ago; returns how many."""
        cutoff = (now or time.time()) - retention_s
        with self._lock:
            return self._db.execute("DELETE FROM jobs WHERE created_at < ?", (cutoff,)).rowcount

    def close(self):
        with self._lock:
            self._db.close()
