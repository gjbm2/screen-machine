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
            # RunPod's bill per endpoint per hour (routes.jobs.billing), and how much of it went to jobs
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS billing_hours ("
                " endpoint TEXT NOT NULL, hour REAL NOT NULL, usd REAL NOT NULL, attributed_usd REAL NOT NULL,"
                " jobs INTEGER NOT NULL, PRIMARY KEY (endpoint, hour))")

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

    def recent_completed(self, stamps, workflow=None, limit=30):
        """The newest completed jobs that have every timestamp in stamps (for the estimator),
        optionally of one workflow only."""
        sql = ("SELECT data FROM jobs WHERE status = 'completed'"
               + "".join(f" AND json_extract(data, '$.{k}') IS NOT NULL" for k in stamps))
        args = []
        if workflow:
            sql += " AND json_extract(data, '$.workflow') = ?"
            args.append(workflow)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [json.loads(r[0]) for r in rows]

    def first_created(self):
        """When the oldest job kept was created, or None."""
        with self._lock:
            row = self._db.execute("SELECT MIN(created_at) FROM jobs").fetchone()
        return row[0] if row else None

    def created_between(self, start, end):
        """Every job created in [start, end), oldest first (for costing against RunPod's bill)."""
        with self._lock:
            rows = self._db.execute("SELECT data FROM jobs WHERE created_at >= ? AND created_at < ?"
                                    " ORDER BY created_at", (start, end)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def save_billing_hours(self, rows):
        """rows: (endpoint, hour start epoch, usd, attributed usd, jobs), replacing any earlier reading."""
        with self._lock:
            self._db.executemany(
                "INSERT INTO billing_hours (endpoint, hour, usd, attributed_usd, jobs) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(endpoint, hour) DO UPDATE SET usd=excluded.usd,"
                " attributed_usd=excluded.attributed_usd, jobs=excluded.jobs", rows)

    def billing_hours(self, start, end):
        """[(endpoint, hour, usd, attributed_usd, jobs)] for hours starting in [start, end)."""
        with self._lock:
            return self._db.execute("SELECT endpoint, hour, usd, attributed_usd, jobs FROM billing_hours"
                                    " WHERE hour >= ? AND hour < ? ORDER BY hour", (start, end)).fetchall()

    def active(self):
        return self.query(state="active", limit=10_000)

    def prune(self, now=None, retention_s=RETENTION_S):
        """Drop jobs (and billing hours) more than retention_s old; returns how many jobs."""
        cutoff = (now or time.time()) - retention_s
        with self._lock:
            self._db.execute("DELETE FROM billing_hours WHERE hour < ?", (cutoff,))
            return self._db.execute("DELETE FROM jobs WHERE created_at < ?", (cutoff,)).rowcount

    def close(self):
        with self._lock:
            self._db.close()
