"""Which API calls need which role: config/auth/gates.yaml, checked before every /api request (app.py).

A rule is {path: regex, methods: [..] (default all), role: guest|approved|admin|destination, body: [keys]}.
Every rule whose path and method match must pass. `role: destination` takes the role from the destination named by
the regex's `destination` group (its `access` in publish-destinations.json); `body` keys name destinations in the
JSON body, each of which must be usable too. Anything no rule matches is open. A hidden bucket (lock.py) is refused
whatever the role until it is unlocked, even with accounts off.

`role: key` (the generation API, /api/v1; keys.py) needs an API key, with accounts on or off, and `scope` one of its
scopes; a destination its `body` names needs the key's `publish` scope and to be one of its screens. A key works on
/api/v1 only, and a key that is wrong, revoked or expired is refused wherever it is sent. Processes on this machine
without a key pass, as everywhere."""
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
                        "scope": r.get("scope"),
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
        if r["role"] == "key":
            continue
        out.append(destination_access(dest) if r["role"] == "destination" else r["role"])
        out += [destination_access(d) for d in named]
    return out


def destinations_named(path, method, body=None):
    """Every destination this call names, by the rules' paths and body keys."""
    return {d for _, dest, named in _matches(path, method, body) for d in ([dest] if dest else []) + named}


def api_error(status, code, message, headers=None, **extra):
    """The generation API's error: {"error": {"code", "message", ...}} (docs/API.md)."""
    response = jsonify({"error": {"code": code, "message": message, **extra}})
    response.status_code = status
    for k, v in (headers or {}).items():
        response.headers[k] = v
    return response


KEY_REFUSED = {
    "invalid_key": "that API key is not one this server knows",
    "key_revoked": "that API key has been revoked",
    "key_expired": "that API key has expired",
}


def _key_gate(v, path, method, body):
    """The `role: key` rules: None, or the refusal."""
    for r, _, named in _matches(path, method, body):
        if r["role"] != "key":
            continue
        if not v.key:
            return api_error(401, "key_required", "this needs an API key: Authorization: Bearer smk_…",
                             {"WWW-Authenticate": 'Bearer realm="api"'})
        if r["scope"] and r["scope"] not in v.key["scopes"]:
            return api_error(403, "scope_missing", f"this key may not {r['scope']}", needs=r["scope"])
        for d in named:
            if "publish" not in v.key["scopes"]:
                return api_error(403, "scope_missing", "this key may not put pictures on screens", needs="publish")
            if d not in v.key["destinations"]:
                return api_error(403, "destination_not_allowed", f"this key may not publish to {d}", destination=d)
    return None


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
    v1 = request.path == "/api/v1" or request.path.startswith("/api/v1/")
    if v.key_error:
        return api_error(401, v.key_error, KEY_REFUSED[v.key_error], {"WWW-Authenticate": 'Bearer error="invalid_token"'})
    if v.key and not v1:
        return api_error(403, "wrong_api", "an API key works on /api/v1 only")
    if v.local:
        return None
    body = request.get_json(silent=True) if request.is_json else None
    if any(lock.hidden(d) for d in destinations_named(request.path, request.method, body)):
        if v1:
            return api_error(404, "not_found", "no such destination")
        return jsonify({"error": "not found"}), 404
    refused = _key_gate(v, request.path, request.method, body)
    if refused is not None:
        return refused
    if not active():
        return None
    missing = [role for role in needed(request.path, request.method, body) if not v.at_least(role)]
    if not missing:
        return None
    if v.email:
        return jsonify({"error": "not allowed for your account", "needs": missing[0]}), 403
    return jsonify({"error": "sign in to do this", "needs": missing[0]}), 401
