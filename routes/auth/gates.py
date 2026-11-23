"""Which API calls need which role: config/auth/gates.yaml, checked before every /api request (app.py).

A rule is {path: regex, methods: [..] (default all), role: guest|approved|admin|destination, body: [keys]}.
Every rule whose path and method match must pass. `role: destination` takes the role from the destination named by
the regex's `destination` group (its `access` in publish-destinations.json); `body` keys name destinations in the
JSON body, each of which must be usable too. Anything no rule matches is open. A hidden bucket (lock.py) is refused
whatever the role until it is unlocked, even with accounts off."""
import re
import threading

from flask import jsonify, request

import smconfig
from routes.auth import lock
from routes.auth.core import active, destination_access, viewer
from utils.logger import warning

_lock = threading.Lock()
_rules = {"mtime": None, "rules": []}


def rules():
    path = smconfig.shared("auth", "gates.yaml")
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return []
    with _lock:
        if _rules["mtime"] != mtime:
            compiled = []
            try:
                for r in (smconfig.read_data(path) or {}).get("rules") or []:
                    compiled.append({
                        "path": re.compile(r["path"]),
                        "methods": {m.upper() for m in r.get("methods") or []},
                        "role": r.get("role", "admin"),
                        "body": list(r.get("body") or []),
                    })
            except Exception as e:  # keep the last good rules rather than open everything up
                warning(f"[auth] {path}: {e}")
                return _rules["rules"]
            _rules.update(mtime=mtime, rules=compiled)
        return _rules["rules"]


def _matches(path, method, body):
    """(rule, the destination its regex names or None, the destinations named in the body) per matching rule."""
    for r in rules():
        if r["methods"] and method not in r["methods"]:
            continue
        m = r["path"].search(path)
        if not m:
            continue
        named = []
        for key in r["body"]:
            value = (body or {}).get(key) if isinstance(body, dict) else None
            if isinstance(value, str) and value:
                named.append(value)
        yield r, m.groupdict().get("destination"), named


def needed(path, method, body=None):
    """The roles this call needs (one per matching rule and named destination)."""
    out = []
    for r, dest, named in _matches(path, method, body):
        out.append(destination_access(dest) if r["role"] == "destination" else r["role"])
        out += [destination_access(d) for d in named]
    return out


def destinations_named(path, method, body=None):
    """Every destination this call names, by the rules' paths and body keys."""
    return {d for _, dest, named in _matches(path, method, body) for d in ([dest] if dest else []) + named}


def gate():
    """before_request: None to let the call through, else a 401 (not signed in), 403 or 404 (a hidden bucket,
    lock.py: as if it weren't there, signed in or not) response."""
    if request.method == "OPTIONS":
        return None
    if request.path.startswith("/output/"):  # a hidden bucket's files: /output/<bucket>/...
        parts = request.path.split("/")
        if len(parts) > 3 and lock.hidden(parts[2]):
            return jsonify({"error": "not found"}), 404
        return None
    if not request.path.startswith("/api/"):
        return None
    v = viewer()
    if v.local:
        return None
    body = request.get_json(silent=True) if request.is_json else None
    if any(lock.hidden(d) for d in destinations_named(request.path, request.method, body)):
        return jsonify({"error": "not found"}), 404
    if not active():
        return None
    missing = [role for role in needed(request.path, request.method, body) if not v.at_least(role)]
    if not missing:
        return None
    if v.email:
        return jsonify({"error": "not allowed for your account", "needs": missing[0]}), 403
    return jsonify({"error": "sign in to do this", "needs": missing[0]}), 401
