"""Sign-in API (docs/ACCOUNTS.md): who am I, sign in with Google, sign out; the people and their roles (Settings ›
People, admins only by config/auth/gates.yaml); the deployment's private catalogue for those allowed it."""
import threading

import requests
from flask import Blueprint, jsonify, request

from routes.auth import core, spaces
from utils.logger import info, warning

auth_api = Blueprint("auth_api", __name__)

GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"


def _me():
    v = core.viewer()
    return {
        "accounts": core.active(),
        "google_client_id": core.client_id() or None,
        "signed_in": bool(v.email),
        "email": v.email,
        "name": v.name,
        "role": v.role,
    }


@auth_api.route("/auth/me", methods=["GET"])
def me():
    core.own_space()  # a browser gets its space cookie on its first visit
    v = core.viewer()
    if v.email:
        spaces.note_person(v.email, v.name)
    return jsonify(_me())


def verify_google(credential):
    """The Google ID token's {email, name}, checked by Google (tokeninfo: signature and expiry) and here (audience,
    issuer, verified email). Raises ValueError."""
    r = requests.get(TOKENINFO, params={"id_token": credential}, timeout=10)
    if r.status_code != 200:
        raise ValueError("Google did not accept the sign-in")
    claims = r.json()
    if claims.get("aud") != core.client_id():
        raise ValueError("the sign-in was for another app")
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise ValueError("the sign-in did not come from Google")
    if str(claims.get("email_verified")).lower() != "true" or not claims.get("email"):
        raise ValueError("the Google account has no verified e-mail")
    return {"email": claims["email"].lower(), "name": claims.get("name") or claims.get("given_name")}


@auth_api.route("/auth/google", methods=["POST"])
def google_sign_in():
    if not core.client_id():
        return jsonify({"error": "sign-in is not set up"}), 503
    credential = (request.get_json(silent=True) or {}).get("credential")
    if not credential:
        return jsonify({"error": "no credential"}), 400
    try:
        who = verify_google(credential)
    except (ValueError, requests.RequestException) as e:
        warning(f"[auth] sign-in refused: {e}")
        return jsonify({"error": str(e)}), 401
    browser_space = core.own_space(mint=False)
    account_space = "u:" + who["email"]
    if browser_space and browser_space.startswith("g:"):
        # what this browser made before signing in becomes the account's
        threading.Thread(target=spaces.adopt, args=(browser_space, account_space), daemon=True).start()
    role = core.people().get(who["email"], {}).get("role", "guest")
    spaces.note_person(who["email"], who["name"])
    info(f"[auth] signed in: {who['email']} ({role})")
    response = jsonify({"accounts": core.active(), "google_client_id": core.client_id(), "signed_in": True,
                        "email": who["email"], "name": core.people().get(who["email"], {}).get("name") or who["name"],
                        "role": role if core.active() else "admin"})
    core.set_cookie(response, core.SESSION_COOKIE, core.session_value(who["email"], who["name"]))
    return response


@auth_api.route("/auth/signout", methods=["POST"])
def sign_out():
    response = jsonify({"signed_in": False})
    response.delete_cookie(core.SESSION_COOKIE, path="/")
    return response


# ---------------------------------------------------------------- people (admins: config/auth/gates.yaml)

@auth_api.route("/auth/people", methods=["GET"])
def list_people():
    """Everyone listed or seen: {email, name, role, first_seen, last_seen}, admins first, then approved, then by name."""
    listed, seen = core.people(), spaces.seen_people()
    rows = []
    for email in set(listed) | set(seen):
        p, s = listed.get(email, {}), seen.get(email, {})
        rows.append({"email": email, "name": p.get("name") or s.get("name"), "role": p.get("role", "guest"),
                     "first_seen": s.get("first_seen"), "last_seen": s.get("last_seen")})
    rows.sort(key=lambda r: (-core.ROLES.index(r["role"]), (r["name"] or r["email"]).lower()))
    return jsonify({"people": rows, "roles": list(core.ROLES), "me": core.viewer().email})


@auth_api.route("/auth/people", methods=["PUT"])
def set_person():
    """{email, role}: gives someone a role (guest takes them off the list). The last admin can't be removed, so the
    deployment never locks itself out."""
    body = request.get_json(silent=True) or {}
    email = str(body.get("email") or "").strip().lower()
    role = body.get("role")
    if "@" not in email or role not in core.ROLES:
        return jsonify({"error": "need an e-mail address and a role"}), 400
    people = {e: dict(p) for e, p in core.people().items()}
    admins = [e for e, p in people.items() if p["role"] == "admin"]
    if role != "admin" and admins == [email]:
        return jsonify({"error": "there must be at least one admin"}), 409
    name = body.get("name") or people.get(email, {}).get("name") or spaces.seen_people().get(email, {}).get("name")
    people[email] = {"role": role, "name": name}
    core.save_people(people)
    info(f"[auth] {core.viewer().email or 'this machine'} set {email} to {role}")
    return list_people()


# ---------------------------------------------------------------- the private catalogue

@auth_api.route("/workflows/private", methods=["GET"])
def private_workflows():
    """The deployment's private catalogue entries (private/catalogue.json), merged by the generator page over the
    shared catalogue. Only for approved people and admins; nobody else's page ever carries them."""
    import smconfig
    if not core.private_ok():
        return jsonify([])
    path = smconfig.path("private", "catalogue.json")
    try:
        return jsonify(smconfig.read_data(path) or [])
    except OSError:
        return jsonify([])
