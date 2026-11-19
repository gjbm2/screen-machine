"""Which API calls need which role: config/auth/gates.yaml, checked before every /api request (app.py).

A rule is {path: regex, methods: [..] (default all), role: guest|approved|admin|destination, body: [keys]}.
Every rule whose path and method match must pass. `role: destination` takes the role from the destination named by
the regex's `destination` group (its `access` in publish-destinations.json); `body` keys name destinations in the
JSON body, each of which must be usable too. Anything no rule matches is open."""
import re
import threading

from flask import jsonify, request

import smconfig
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


def needed(path, method, body=None):
    """The roles this call needs (one per matching rule and named destination)."""
    out = []
    for r in rules():
        if r["methods"] and method not in r["methods"]:
            continue
        m = r["path"].search(path)
        if not m:
            continue
        if r["role"] == "destination":
            out.append(destination_access(m.groupdict().get("destination")))
        else:
            out.append(r["role"])
        for key in r["body"]:
            value = (body or {}).get(key) if isinstance(body, dict) else None
            if isinstance(value, str) and value:
                out.append(destination_access(value))
    return out


def gate():
    """before_request: None to let the call through, else a 401 (not signed in) or 403 response."""
    if not request.path.startswith("/api/") or request.method == "OPTIONS" or not active():
        return None
    v = viewer()
    if v.local:
        return None
    body = request.get_json(silent=True) if request.is_json else None
    missing = [role for role in needed(request.path, request.method, body) if not v.at_least(role)]
    if not missing:
        return None
    if v.email:
        return jsonify({"error": "not allowed for your account", "needs": missing[0]}), 403
    return jsonify({"error": "sign in to do this", "needs": missing[0]}), 401
