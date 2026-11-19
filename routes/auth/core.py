"""Who is calling, and what they may see (routes/auth/__init__.py has the overview)."""
import os
import re
import secrets
import threading
from dataclasses import dataclass

from flask import request
from itsdangerous import BadSignature, URLSafeTimedSerializer

import smconfig
from utils.logger import warning

ROLES = ("guest", "member", "admin")  # rising; a role passes every gate needing it or less
SESSION_COOKIE = "sm_session"
SPACE_COOKIE = "sm_space"
COOKIE_DAYS = 365
_SPACE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_LOCAL = ("127.0.0.1", "::1")


def rank(role):
    return ROLES.index(role) if role in ROLES else len(ROLES)  # an unknown role is above admin: nobody


def settings():
    return smconfig.deployment().section("accounts")


def client_id():
    return str(settings().get("google_client_id") or "").strip()


def active():
    """Sign-in and roles are enforced only with the capability on and a Google client ID to sign in with."""
    try:
        return smconfig.enabled("accounts") and bool(client_id())
    except Exception as e:  # a broken manifest must not lock everyone out
        warning(f"[auth] deployment unreadable, accounts off: {e}")
        return False


# ---------------------------------------------------------------- people (private/accounts.yaml)

_people_lock = threading.Lock()
_people = {"mtime": None, "by_email": {}}


def people():
    """{email: {role, name}} from deployments/<id>/private/accounts.yaml (`people: [{email, role, name}]`),
    re-read when it changes. Missing file: nobody is listed."""
    path = smconfig.path("private", "accounts.yaml")
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {}
    with _people_lock:
        if _people["mtime"] != mtime:
            by_email = {}
            try:
                for p in (smconfig.read_data(path) or {}).get("people") or []:
                    email = str((p or {}).get("email") or "").strip().lower()
                    if email:
                        by_email[email] = {"role": p.get("role") if p.get("role") in ROLES else "guest",
                                           "name": p.get("name")}
            except Exception as e:
                warning(f"[auth] {path}: {e}")
            _people.update(mtime=mtime, by_email=by_email)
        return _people["by_email"]


# ---------------------------------------------------------------- session cookie

def _signer():
    key_path = smconfig.secret("session.key")
    if not key_path.exists():
        key_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    return URLSafeTimedSerializer(key_path.read_text().strip(), salt="sm-session")


def session_value(email, name):
    return _signer().dumps({"email": email.lower(), "name": name})


def _session():
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        data = _signer().loads(raw, max_age=COOKIE_DAYS * 86400)
    except BadSignature:
        return None
    return data if isinstance(data, dict) and data.get("email") else None


def https():
    """Behind Caddy (the https name) cookies are Secure; the http doors (TVs, the IP address) can't keep those."""
    return request.headers.get("X-Forwarded-Proto") == "https" or request.is_secure


def set_cookie(response, name, value, days=COOKIE_DAYS):
    response.set_cookie(name, value, max_age=days * 86400, httponly=True, samesite="Lax", secure=https(), path="/")


# ---------------------------------------------------------------- the viewer

@dataclass(frozen=True)
class Viewer:
    role: str
    email: str = None
    name: str = None
    local: bool = False

    def at_least(self, role):
        return self.local or rank(self.role) >= rank(role)

    @property
    def admin(self):
        return self.at_least("admin")


def viewer():
    """Who is calling this request (kept in the request's environ, which is never shared between requests)."""
    if "sm.viewer" in request.environ:
        return request.environ["sm.viewer"]
    local = request.remote_addr in _LOCAL and "X-Forwarded-For" not in request.headers
    s = _session()
    email = s["email"] if s else None
    person = people().get(email, {}) if email else {}
    role = person.get("role", "guest")
    if not active():
        role = "admin"  # accounts off: everyone may do everything, as before
    v = Viewer(role=role, email=email, name=person.get("name") or (s or {}).get("name"), local=local)
    request.environ["sm.viewer"] = v
    return v


def own_space(mint=True):
    """The caller's generator space: "u:<email>" when signed in, else "g:<browser key>" (made, and set as a cookie
    on the response, the first time it is needed). None when there is none and mint is False."""
    v = viewer()
    if v.email:
        return "u:" + v.email
    raw = request.cookies.get(SPACE_COOKIE) or request.environ.get("sm.new_space")
    if raw and _SPACE_RE.match(raw):
        return "g:" + raw
    if not mint:
        return None
    request.environ["sm.new_space"] = secrets.token_urlsafe(16)
    return "g:" + request.environ["sm.new_space"]


def after_request(response):
    """Sets the browser's space cookie when own_space() made one this request."""
    new = request.environ.get("sm.new_space")
    if new and not request.cookies.get(SPACE_COOKIE):
        set_cookie(response, SPACE_COOKIE, new, days=COOKIE_DAYS * 2)
    return response


# ---------------------------------------------------------------- what they may see

def destination_access(dest_id):
    """The least role that may see and use a destination: its `access` in publish-destinations.json, else admin."""
    try:
        for d in smconfig.destinations():
            if d.get("id") == dest_id:
                return d.get("access") if d.get("access") in ROLES else "admin"
    except Exception as e:
        warning(f"[auth] publish-destinations unreadable: {e}")
    return "admin"


def can_use_destination(dest_id):
    return not active() or viewer().at_least(destination_access(dest_id))


def can_see_space(space, everyone=False):
    """Whether a generated image stamped with `space` shows in the caller's Generated tab. Images from before spaces
    (no stamp) belong to the admins (owner, 19 Nov)."""
    if not active():
        return True
    v = viewer()
    if v.admin and (everyone or not space):
        return True
    mine = own_space(mint=False)
    return bool(space) and space == mine


def job_visibility(everyone=True):
    """For the jobs feed: a test of whether the caller sees a job, settled now (a stream outlives its request). An
    admin sees every job (unless everyone is False); anyone else sees their own generator jobs and the house's
    jobs for destinations they may use."""
    if not active():
        return lambda job: True
    v = viewer()
    if v.admin and everyone:
        return lambda job: True
    mine = own_space(mint=False)

    def visible(job):
        if job.get("source") == "generator":
            from routes.auth.spaces import batch_space
            return bool(mine) and batch_space(job.get("batch_id")) == mine
        target = job.get("target")
        return bool(target) and v.at_least(destination_access(target))
    return visible


def can_see_job(job, everyone=True):
    return job_visibility(everyone)(job)
