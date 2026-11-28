"""The generation API's generations (routes/api_v1): one row per POST /api/v1/generations in accounts.db, the key that
made it, what was asked, and once it ends what came out. A running one is also held here in memory with its job ids
(the job tracker has its progress); one the server lost by restarting reads as failed."""
import hashlib
import json
import threading
import time
import uuid

from routes.auth import spaces
from utils.logger import error, info

RESTARTED = "the server restarted while this was being made"
LIVE = ("queued", "running")
_ready = {"db": None}     # the connection the tables were made on (a new one, in tests, makes them again)
_running = {}        # generation id -> {"key_id", "job_ids": [...], "done": Event, "cancel": bool}
_running_lock = threading.Lock()


def _conn():
    db = spaces._conn()
    if _ready["db"] is not db:
        db.execute("CREATE TABLE IF NOT EXISTS api_generations ("
                   " id TEXT PRIMARY KEY, key_id TEXT NOT NULL, created_at REAL NOT NULL, n INTEGER NOT NULL,"
                   " status TEXT NOT NULL, request TEXT NOT NULL, request_hash TEXT, idempotency_key TEXT,"
                   " result TEXT, error TEXT, finished_at REAL, job_ids TEXT)")
        db.execute("CREATE INDEX IF NOT EXISTS api_generations_key ON api_generations(key_id, created_at)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS api_generations_idem ON api_generations(key_id, idempotency_key)"
                   " WHERE idempotency_key IS NOT NULL")
        _ready["db"] = db
    return db


def request_hash(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def _row(r, names):
    d = dict(zip(names, r))
    for k in ("request", "result", "job_ids"):
        d[k] = json.loads(d[k]) if d.get(k) else None
    if d["status"] in LIVE and d["id"] not in _running:
        d.update(status="failed", error=d.get("error") or RESTARTED)
    return d


def _select(where, args):
    with spaces._lock:
        cur = _conn().execute(f"SELECT * FROM api_generations {where}", args)
        names = [c[0] for c in cur.description]
        return [_row(r, names) for r in cur.fetchall()]


def get(gen_id, key_id):
    rows = _select("WHERE id=? AND key_id=?", (gen_id, key_id))
    return rows[0] if rows else None


def by_idempotency(key_id, idem):
    rows = _select("WHERE key_id=? AND idempotency_key=?", (key_id, idem))
    return rows[0] if rows else None


def listing(key_id, limit=20, before=None):
    if before:
        return _select("WHERE key_id=? AND created_at<? ORDER BY created_at DESC LIMIT ?", (key_id, before, limit))
    return _select("WHERE key_id=? ORDER BY created_at DESC LIMIT ?", (key_id, limit))


def made_since(key_id, since):
    """(pictures asked for since then, the oldest of those requests' times)."""
    with spaces._lock:
        r = _conn().execute("SELECT COALESCE(SUM(n), 0), MIN(created_at) FROM api_generations"
                            " WHERE key_id=? AND created_at>=?", (key_id, since)).fetchone()
    return int(r[0]), r[1]


def running_count(key_id):
    with _running_lock:
        return sum(1 for run in _running.values() if run["key_id"] == key_id)


def new_id():
    return "gen_" + uuid.uuid4().hex[:20]


def create(gen_id, key_id, n, request, idem=None, req_hash=None):
    with _running_lock:
        _running[gen_id] = {"key_id": key_id, "job_ids": [], "done": threading.Event(), "cancel": False}
    with spaces._lock:
        _conn().execute("INSERT INTO api_generations (id, key_id, created_at, n, status, request, request_hash,"
                        " idempotency_key) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)",
                        (gen_id, key_id, time.time(), n, json.dumps(request), req_hash, idem))
    return _running[gen_id]


def job_ids(gen_id):
    with _running_lock:
        run = _running.get(gen_id)
        return list(run["job_ids"]) if run else None


def finish(gen_id, status, result=None, err=None):
    with _running_lock:
        run = _running.get(gen_id) or {}
    if run.get("cancel") and status == "failed":
        status = "cancelled"
    with spaces._lock:
        _conn().execute("UPDATE api_generations SET status=?, result=?, error=?, finished_at=?, job_ids=? WHERE id=?",
                        (status, json.dumps(result) if result is not None else None, err, time.time(),
                         json.dumps(run.get("job_ids") or []), gen_id))
    with _running_lock:
        _running.pop(gen_id, None)
    if run.get("done"):
        run["done"].set()


def wait(gen_id, seconds):
    with _running_lock:
        run = _running.get(gen_id)
    if run and seconds > 0:
        run["done"].wait(seconds)


def cancel(gen_id):
    """Asks the generation's jobs to stop. False if it is not running."""
    from routes.jobs import get_tracker
    with _running_lock:
        run = _running.get(gen_id)
        if not run:
            return False
        run["cancel"] = True
        ids = list(run["job_ids"])
    for job_id in ids:
        get_tracker().cancel(job_id)
    return True


def start(gen_id, run, work):
    """Runs work() (which makes the pictures and returns process_generate_image_request's answer) in a thread, and
    files how it ended."""
    def go():
        try:
            result = work()
            finish(gen_id, "succeeded", result=result)
            info(f"[api] {gen_id} done: {len((result or {}).get('images') or [])} made")
        except Exception as e:
            error(f"[api] {gen_id} failed: {e}")
            finish(gen_id, "failed", err=str(e) or "the generation failed")
    threading.Thread(target=go, name=f"api-{gen_id}", daemon=True).start()
