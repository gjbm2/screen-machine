"""Sign-in API: who am I, sign in with Google, sign out (docs/ACCOUNTS.md)."""
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
