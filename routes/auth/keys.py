"""API keys (owner, 28 Nov): other apps and agents use the generation API (/api/v1, routes/api_v1; docs/API.md) with a
key an admin makes on Settings › Apps & API keys.

- A key reads `smk_<id>_<secret>`. Only a SHA-256 of the secret is kept (accounts.db, next to the spaces); the whole
  key is shown once, when it is made or rolled. The id is not secret: it names the key in lists, logs and its jobs.
- It is sent as `Authorization: Bearer <key>` (or `X-API-Key`), never in a URL, and works on /api/v1 only.
- Revoking is at once and for good (the row stays, for the record). Rolling gives the key a new secret; the old one
  keeps working for the grace asked (none, or up to a week), so an app can be moved over without a gap.
- What a key may do: `generate` (make pictures, which land in its maker's Generated) and `publish` (also put them on
  the screens in its `destinations`); `private` lets it use the deployment's private catalogue. Its limits: pictures
  in any 24 hours and generations at once (0 = no limit). It may expire.
- The gates are config/auth/gates.yaml's `role: key` rules (gates.py); a key's request is a guest (approved with
  `private`) whose space is its maker's.
"""
import hashlib
import hmac
import json
import re
import secrets
import threading
import time

from routes.auth import spaces
from utils.logger import info, warning

PREFIX = "smk"
SCOPES = ("generate", "publish")
MAX_GRACE_H = 7 * 24
_TOKEN_RE = re.compile(r"^smk_([a-z0-9]{12})_([A-Za-z0-9_-]{32,64})$")
_USE_WRITE_S = 30          # last-used is written at most this often per key
_used = {}                 # key id -> when last-used was last written
_ready = {"db": None}     # the connection the tables were made on (a new one, in tests, makes them again)

FIELDS = ("id", "name", "last4", "created_at", "created_by", "space", "scopes", "destinations", "private",
          "per_day", "concurrent", "expires_at", "revoked_at", "revoked_by", "rolled_at", "prev_until",
          "last_used_at", "last_used_ip", "uses")


def _conn():
    db = spaces._conn()
    if _ready["db"] is not db:
        db.execute("CREATE TABLE IF NOT EXISTS api_keys ("
                   " id TEXT PRIMARY KEY, name TEXT NOT NULL, hash TEXT NOT NULL, last4 TEXT NOT NULL,"
                   " prev_hash TEXT, prev_until REAL, created_at REAL NOT NULL, created_by TEXT, space TEXT,"
                   " scopes TEXT NOT NULL, destinations TEXT NOT NULL, private INTEGER NOT NULL DEFAULT 0,"
                   " per_day INTEGER NOT NULL DEFAULT 0, concurrent INTEGER NOT NULL DEFAULT 0, expires_at REAL,"
                   " revoked_at REAL, revoked_by TEXT, rolled_at REAL, last_used_at REAL, last_used_ip TEXT,"
                   " uses INTEGER NOT NULL DEFAULT 0)")
        _ready["db"] = db
    return db


def _hash(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def _row(r, names):
    d = dict(zip(names, r))
    d["scopes"] = json.loads(d.get("scopes") or "[]")
    d["destinations"] = json.loads(d.get("destinations") or "[]")
    d["private"] = bool(d.get("private"))
    return d


def _select(where="", args=()):
    with spaces._lock:
        cur = _conn().execute(f"SELECT * FROM api_keys {where}", args)
        names = [c[0] for c in cur.description]
        return [_row(r, names) for r in cur.fetchall()]


def public(key):
    """What lists and the key itself may see: never a hash."""
    if not key:
        return None
    out = {k: key.get(k) for k in FIELDS}
    out["hint"] = f"{PREFIX}_{key['id']}_…{key['last4']}"
    out["state"] = state(key)
    return out


def state(key, now=None):
    now = now or time.time()
    if key.get("revoked_at"):
        return "revoked"
    if key.get("expires_at") and key["expires_at"] <= now:
        return "expired"
    return "active"


def _clean(fields):
    """The settable fields of a key, checked. Raises ValueError with what is wrong."""
    out = {}
    if "name" in fields:
        name = str(fields.get("name") or "").strip()
        if not name or len(name) > 80:
            raise ValueError("a key needs a name (up to 80 characters)")
        out["name"] = name
    if "scopes" in fields:
        scopes = fields.get("scopes") or []
        if not isinstance(scopes, list) or any(s not in SCOPES for s in scopes):
            raise ValueError(f"scopes are some of {', '.join(SCOPES)}")
        out["scopes"] = [s for s in SCOPES if s in scopes or (s == "generate" and "publish" in scopes)]
    if "destinations" in fields:
        dests = fields.get("destinations") or []
        if not isinstance(dests, list) or not all(isinstance(d, str) and d for d in dests):
            raise ValueError("destinations is a list of screen ids")
        out["destinations"] = sorted(set(dests))
    if "private" in fields:
        out["private"] = bool(fields.get("private"))
    for name in ("per_day", "concurrent"):
        if name in fields:
            try:
                value = int(fields.get(name) or 0)
            except (TypeError, ValueError):
                raise ValueError(f"{name} is a whole number (0 = no limit)") from None
            if value < 0 or value > 100000:
                raise ValueError(f"{name} is between 0 (no limit) and 100000")
            out[name] = value
    if "expires_at" in fields:
        value = fields.get("expires_at")
        out["expires_at"] = float(value) if value not in (None, "", 0) else None
    return out


def _write(key_id, fields):
    cols = {k: (json.dumps(v) if k in ("scopes", "destinations") else int(v) if k == "private" else v)
            for k, v in fields.items()}
    with spaces._lock:
        _conn().execute(f"UPDATE api_keys SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", (*cols.values(), key_id))


def create(name, created_by=None, space=None, scopes=("generate",), destinations=(), private=False, per_day=100,
           concurrent=3, expires_at=None):
    """A new key: (its row, the whole key, shown only now). Raises ValueError for a bad field."""
    fields = _clean({"name": name, "scopes": list(scopes), "destinations": list(destinations), "private": private,
                     "per_day": per_day, "concurrent": concurrent, "expires_at": expires_at})
    key_id = "".join(secrets.choice("abcdefghijkmnpqrstuvwxyz23456789") for _ in range(12))
    secret = secrets.token_urlsafe(32)
    with spaces._lock:
        _conn().execute(
            "INSERT INTO api_keys (id, name, hash, last4, created_at, created_by, space, scopes, destinations, private,"
            " per_day, concurrent, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key_id, fields["name"], _hash(secret), secret[-4:], time.time(), created_by, space,
             json.dumps(fields["scopes"]), json.dumps(fields["destinations"]), int(fields["private"]),
             fields["per_day"], fields["concurrent"], fields["expires_at"]))
    info(f"[keys] {created_by or 'this machine'} made API key {key_id} ({fields['name']})")
    return get(key_id), f"{PREFIX}_{key_id}_{secret}"


def get(key_id):
    rows = _select("WHERE id=?", (key_id,))
    return rows[0] if rows else None


def all_keys():
    return sorted(_select(), key=lambda k: (k.get("revoked_at") is not None, -k["created_at"]))


def update(key_id, fields):
    """Changes a live key's name, scopes, screens or limits; returns it (None if there is none)."""
    key = get(key_id)
    if not key:
        return None
    if key.get("revoked_at"):
        raise ValueError("that key is revoked")
    allowed = {k: v for k, v in fields.items() if k in ("name", "scopes", "destinations", "private", "per_day",
                                                         "concurrent", "expires_at")}
    if allowed:
        _write(key_id, _clean(allowed))
    return get(key_id)


def roll(key_id, grace_hours=0):
    """A new secret for the key: (its row, the whole new key). The old secret keeps working for grace_hours."""
    key = get(key_id)
    if not key:
        return None, None
    if key.get("revoked_at"):
        raise ValueError("that key is revoked")
    grace = max(0.0, min(float(grace_hours or 0), MAX_GRACE_H))
    secret = secrets.token_urlsafe(32)
    now = time.time()
    with spaces._lock:
        old = _conn().execute("SELECT hash FROM api_keys WHERE id=?", (key_id,)).fetchone()[0]
        _conn().execute("UPDATE api_keys SET hash=?, last4=?, prev_hash=?, prev_until=?, rolled_at=? WHERE id=?",
                        (_hash(secret), secret[-4:], old if grace else None, now + grace * 3600 if grace else None,
                         now, key_id))
    info(f"[keys] API key {key_id} rolled (old secret works for {grace:g} h)")
    return get(key_id), f"{PREFIX}_{key_id}_{secret}"


def revoke(key_id, by=None):
    key = get(key_id)
    if not key:
        return None
    if not key.get("revoked_at"):
        with spaces._lock:
            _conn().execute("UPDATE api_keys SET revoked_at=?, revoked_by=?, prev_hash=NULL, prev_until=NULL"
                            " WHERE id=?", (time.time(), by, key_id))
        info(f"[keys] API key {key_id} ({key['name']}) revoked by {by or 'this machine'}")
    return get(key_id)


def check(token):
    """The key a presented token is, as (key, None), or (None, why): invalid_key, key_revoked or key_expired. Only a
    token whose secret matches is told it is revoked or expired."""
    m = _TOKEN_RE.match(token or "")
    if not m:
        return None, "invalid_key"
    key_id, secret = m.groups()
    with spaces._lock:
        cur = _conn().execute("SELECT * FROM api_keys WHERE id=?", (key_id,))
        names = [c[0] for c in cur.description]
        r = cur.fetchone()
    if not r:
        return None, "invalid_key"
    row = dict(zip(names, r))
    given = _hash(secret)
    now = time.time()
    ok = hmac.compare_digest(given, row["hash"])
    if not ok and row.get("prev_hash") and (row.get("prev_until") or 0) > now:
        ok = hmac.compare_digest(given, row["prev_hash"])
    if not ok:
        return None, "invalid_key"
    key = _row(r, names)
    why = {"revoked": "key_revoked", "expired": "key_expired"}.get(state(key, now))
    return (None, why) if why else (key, None)


def note_use(key, ip):
    """Last used (when and from where) and the count, written at most every _USE_WRITE_S per key."""
    now = time.time()
    if now - _used.get(key["id"], 0) < _USE_WRITE_S:
        return
    _used[key["id"]] = now
    try:
        with spaces._lock:
            _conn().execute("UPDATE api_keys SET last_used_at=?, last_used_ip=?, uses=uses+1 WHERE id=?",
                            (now, ip, key["id"]))
    except Exception as e:
        warning(f"[keys] could not note the use of {key['id']}: {e}")


# How many requests a key may make a minute, across the API (polling included); fixed, generous: it stops a runaway
# loop, not an app.
REQUESTS_PER_MIN = 120
_hits = {}
_hits_lock = threading.Lock()


def hit(key_id, now=None):
    """Counts one request; (allowed, remaining, seconds until the window frees one)."""
    now = now or time.time()
    with _hits_lock:
        window = [t for t in _hits.get(key_id, []) if t > now - 60]
        if len(window) >= REQUESTS_PER_MIN:
            _hits[key_id] = window
            return False, 0, max(1, int(window[0] + 60 - now) + 1)
        window.append(now)
        _hits[key_id] = window
        return True, REQUESTS_PER_MIN - len(window), 0
