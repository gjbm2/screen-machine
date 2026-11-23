"""Everyone's own cloud (owner, 23 Nov): each signed-in person who isn't an admin gets a cloud bucket of their own
beside the house's Cloud, to keep the pictures they make. They see only their own; admins see every one.

The clouds are made from the people the deployment knows (private/accounts.yaml and who has signed in) and added to
the destinations as they are read (routes/utils._load_json_once). They are never written to publish-destinations.json,
so no address goes into git. A cloud's id is a hash of its person's e-mail, so it is the same on every read."""
import hashlib
import threading
import time

from routes.auth import core, spaces
from utils.logger import warning

PREFIX = "cloud-"
_TTL = 15.0

_lock = threading.Lock()
_cache = {"at": None, "clouds": []}


def cloud_id(email):
    return PREFIX + hashlib.sha1(email.strip().lower().encode()).hexdigest()[:10]


def _label(email, name):
    first = (name or "").split()
    return f"{first[0] if first else email.split('@')[0]}'s cloud"


def clouds():
    """The people's clouds as destination entries (fresh copies), {"cloud": true, "owner": <email>, ...}."""
    if not core.active():
        return []
    now = time.monotonic()
    with _lock:
        if _cache["at"] is not None and now - _cache["at"] < _TTL:
            return [dict(d) for d in _cache["clouds"]]
    listed = core.people()
    try:
        seen = spaces.seen_people()
    except Exception as e:
        warning(f"[clouds] who has signed in is unreadable: {e}")
        seen = {}
    out = []
    for email in sorted(set(listed) | set(seen)):
        if listed.get(email, {}).get("role") == "admin":
            continue  # an admin's pictures go in the house's clouds
        name = listed.get(email, {}).get("name") or seen.get(email, {}).get("name")
        out.append({"id": cloud_id(email), "name": _label(email, name), "icon": "Cloud", "type": "output_file",
                    "has_bucket": True, "headless": True, "cloud": True, "owner": email,
                    "description": f"{name or email}'s own pictures"})
    with _lock:
        _cache.update(at=now, clouds=out)
    return [dict(d) for d in out]


def owner_of(dest_id):
    """The e-mail of the person whose cloud this is, or None."""
    if not isinstance(dest_id, str) or not dest_id.startswith(PREFIX):
        return None
    return next((d["owner"] for d in clouds() if d["id"] == dest_id), None)
