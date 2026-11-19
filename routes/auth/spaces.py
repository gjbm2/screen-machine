"""Which space each generator batch belongs to (SQLite at ~/.local/state/screen-machine/accounts.db), so the jobs feed
can show a viewer only their own; and moving a browser's images into an account when it signs in."""
import json
import os
import sqlite3
import threading
import time

import smconfig
from utils.logger import info, warning

_lock = threading.Lock()
_db = None
_cache = {}  # batch_id -> space (batches are written once)


def _conn():
    global _db
    if _db is None:
        path = os.environ.get("ACCOUNTS_DB") or str(smconfig.state("accounts.db"))
        if path != ":memory:":
            os.makedirs(os.path.dirname(path), exist_ok=True)
        _db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        _db.execute("PRAGMA journal_mode=WAL")
        _db.execute("CREATE TABLE IF NOT EXISTS batch_spaces ("
                    " batch_id TEXT PRIMARY KEY, space TEXT NOT NULL, created_at REAL NOT NULL)")
        _db.execute("CREATE INDEX IF NOT EXISTS batch_spaces_space ON batch_spaces(space)")
    return _db


def note_batch(batch_id, space):
    if not batch_id or not space:
        return
    with _lock:
        _conn().execute("INSERT OR IGNORE INTO batch_spaces (batch_id, space, created_at) VALUES (?, ?, ?)",
                        (str(batch_id), space, time.time()))
        _cache.pop(str(batch_id), None)


def batch_space(batch_id):
    if not batch_id:
        return None
    key = str(batch_id)
    if key in _cache:
        return _cache[key]
    with _lock:
        row = _conn().execute("SELECT space FROM batch_spaces WHERE batch_id = ?", (key,)).fetchone()
    space = row[0] if row else None
    if space:
        _cache[key] = space
    return space


def adopt(old, new):
    """A browser signed in: its anonymous space's batches and Generated images become the account's."""
    if not old or not new or old == new:
        return 0
    with _lock:
        _conn().execute("UPDATE batch_spaces SET space = ? WHERE space = ?", (new, old))
        _cache.clear()
    from routes.bucketer import bucket_path
    from routes.utils import sidecar_path
    moved = 0
    for image in bucket_path("_recent").iterdir():
        if image.suffix.lower() == ".json" or not image.is_file():
            continue
        sidecar = sidecar_path(image)
        try:
            meta = json.loads(sidecar.read_text("utf-8"))
        except Exception:
            continue
        if isinstance(meta, dict) and meta.get("space") == old:
            meta["space"] = new
            try:
                sidecar.write_text(json.dumps(meta, indent=2), "utf-8")
                moved += 1
            except OSError as e:
                warning(f"[auth] could not move {image.name} to {new}: {e}")
    info(f"[auth] {old} -> {new}: {moved} Generated images")
    return moved
