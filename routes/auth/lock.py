"""Hidden buckets (owner, 23 Nov): a destination with `"locked": true` in publish-destinations.json (Private) is left
out of every list and refused to every call until an admin unlocks this browser with the deployment's PIN. Then it
shows for 30 minutes, or until they lock it again.

- The PIN is the first one an admin types; its hash is kept in deployments/<id>/private/lock.yaml (gitignored).
  Delete that file to choose another.
- The unlock is a signed cookie (sm_unlock) naming the account and when it runs out.
- Processes on this machine always see the hidden buckets; so does work with no request behind it (schedules)."""
import os
import threading
import time

from flask import has_request_context, request
from itsdangerous import BadSignature
from werkzeug.security import check_password_hash, generate_password_hash

import smconfig
from routes.auth import core
from utils.logger import warning

COOKIE = "sm_unlock"
MINUTES = 30
MIN_PIN = 4
_SALT = "sm-unlock"
_TTL = 5.0  # the locked list is read at most this often (it is asked for every /output file)

_lock = threading.Lock()
_ids = {"at": None, "ids": frozenset()}


def locked_ids():
    """Ids of the destinations kept hidden until unlocked."""
    now = time.monotonic()
    with _lock:
        if _ids["at"] is not None and now - _ids["at"] < _TTL:
            return _ids["ids"]
    try:
        ids = frozenset(d["id"] for d in smconfig.destinations() if d.get("locked"))
    except Exception as e:
        warning(f"[lock] publish-destinations unreadable: {e}")
        ids = _ids["ids"]  # keep the last good list rather than show them
    with _lock:
        _ids.update(at=now, ids=ids)
    return ids


def until():
    """When this browser's unlock runs out (epoch seconds), or None while locked. Only an admin's counts."""
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        data = core._signer(_SALT).loads(raw, max_age=MINUTES * 60)
    except BadSignature:
        return None
    v = core.viewer()
    if not isinstance(data, dict) or data.get("email") != v.email or not v.admin:
        return None
    t = float(data.get("until") or 0)
    return t if t > time.time() else None


def unlocked():
    if not has_request_context():
        return True
    return core.viewer().local or until() is not None


def hidden(dest_id):
    """Whether this caller may not see or use the destination: it is locked and they haven't unlocked it."""
    return bool(dest_id) and dest_id in locked_ids() and not unlocked()


# ---------------------------------------------------------------- the PIN

def _pin_file():
    return smconfig.path("private", "lock.yaml")


def _pin_hash():
    try:
        return str((smconfig.read_data(_pin_file()) or {}).get("pin_hash") or "")
    except (OSError, ValueError):
        return ""


def pin_set():
    return bool(_pin_hash())


def check_pin(pin):
    """Whether the PIN is right. With none chosen yet, this one becomes the PIN."""
    h = _pin_hash()
    if h:
        return check_password_hash(h, pin)
    path = _pin_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text("# The PIN that unlocks the hidden buckets (routes/auth/lock.py). Delete this file to choose another.\n"
                   + smconfig._yaml().safe_dump({"pin_hash": generate_password_hash(pin)}))
    os.replace(tmp, path)
    return True


def set_unlock(response, email, until):
    """Unlocks this browser until `until` (epoch seconds, MINUTES from now)."""
    core.set_cookie(response, COOKIE, core._signer(_SALT).dumps({"email": email, "until": until}), seconds=MINUTES * 60)


def clear_unlock(response):
    response.delete_cookie(COOKIE, path="/")
