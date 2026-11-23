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

ROLES = ("guest", "approved", "admin")  # rising; a role passes every gate needing it or less
SESSION_COOKIE = "sm_session"
SPACE_COOKIE = "sm_space"
COOKIE_DAYS = 365
_SPACE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_LOCAL = ("127.0.0.1", "::1")


def rank(role):
    return ROLES.index(role) if role in ROLES else len(ROLES)  # an unknown role is above admin: nobody


# ---------------------------------------------------------------- the deployment's sign-in config

# Everything site-specific about sign-in is deployment config, kept out of git: deployments/<id>/private/accounts.yaml
#   google_client_id: "<the deployment's Google OAuth web client>"
#   people: [{email, role: admin|approved, name}]
# The code names no site, account or address.
_config_lock = threading.Lock()
_config = {"mtime": None, "client_id": "", "by_email": {}}


def _accounts_config():
    """The private accounts file, parsed; re-read when it changes. Missing or unreadable: no client ID, nobody listed."""
    path = smconfig.path("private", "accounts.yaml")
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {"client_id": "", "by_email": {}}
    with _config_lock:
        if _config["mtime"] != mtime:
            client, by_email = "", {}
            try:
                data = smconfig.read_data(path) or {}
                client = str(data.get("google_client_id") or "").strip()
                for p in data.get("people") or []:
                    email = str((p or {}).get("email") or "").strip().lower()
                    if email:
                        by_email[email] = {"role": _role(p.get("role")), "name": p.get("name")}
            except Exception as e:
                warning(f"[auth] {path}: {e}")
            _config.update(mtime=mtime, client_id=client, by_email=by_email)
        return {"client_id": _config["client_id"], "by_email": _config["by_email"]}


def _role(value):
    value = "approved" if value == "member" else value  # the first name for approved
    return value if value in ROLES else "guest"


def client_id():
    return _accounts_config()["client_id"]


def save_people(by_email):
    """Rewrites the people list of the private accounts file (the Settings › People page), keeping its client ID and
    its leading comment lines. Atomic. by_email: {email: {role, name}}; a guest is simply not listed."""
    path = smconfig.path("private", "accounts.yaml")
    try:
        text = path.read_text()
    except OSError:
        text = ""
    header = "".join(line + "\n" for line in text.splitlines() if line.startswith("#"))
    rows = [{"email": e, "role": p["role"], **({"name": p["name"]} if p.get("name") else {})}
            for e, p in sorted(by_email.items()) if p.get("role") in ("approved", "admin")]
    body = smconfig._yaml().safe_dump({"google_client_id": client_id(), "people": rows}, sort_keys=False,
                                      default_flow_style=None, width=120)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(header + body)
    os.replace(tmp, path)


def people():
    """{email: {role, name}} of the people the deployment lists."""
    return _accounts_config()["by_email"]


def active():
    """Sign-in and roles are enforced only with the capability on and a Google client ID to sign in with."""
    try:
        return smconfig.enabled("accounts") and bool(client_id())
    except Exception as e:  # a broken manifest must not lock everyone out
        warning(f"[auth] deployment unreadable, accounts off: {e}")
        return False


# ---------------------------------------------------------------- session cookie

def _signer(salt="sm-session"):
    key_path = smconfig.secret("session.key")
    if not key_path.exists():
        key_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    return URLSafeTimedSerializer(key_path.read_text().strip(), salt=salt)


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
    """Behind the https proxy cookies are Secure; plain-http callers (e.g. screens' display pages) can't keep those."""
    return request.headers.get("X-Forwarded-Proto") == "https" or request.is_secure


def set_cookie(response, name, value, days=COOKIE_DAYS, seconds=None):
    response.set_cookie(name, value, max_age=seconds if seconds is not None else days * 86400, httponly=True, samesite="Lax", secure=https(), path="/")


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
    """The least role that may see and use a destination: its `access` in publish-destinations.json, else admin. A
    person's own cloud (clouds.py) is theirs and the admins'."""
    from routes.auth import clouds
    owner = clouds.owner_of(dest_id)
    if owner:
        from flask import has_request_context
        return "guest" if has_request_context() and viewer().email == owner else "admin"
    try:
        for d in smconfig.destinations():
            if d.get("id") == dest_id:
                return d.get("access") if d.get("access") in ROLES else "admin"
    except Exception as e:
        warning(f"[auth] publish-destinations unreadable: {e}")
    return "admin"


def private_ok():
    """Whether this caller may see the deployment's private catalogue (private/catalogue.json: its own workflows and
    the routing text that goes with them): approved people and admins. Work with no request behind it (schedules,
    generation threads) and processes on this machine may."""
    from flask import has_request_context
    if not has_request_context() or not active():
        return True
    return viewer().at_least("approved")


def can_use_destination(dest_id):
    from routes.auth import lock
    if lock.hidden(dest_id):
        return False
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
