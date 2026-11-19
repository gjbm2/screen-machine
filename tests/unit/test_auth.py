"""Accounts (routes/auth; docs/ACCOUNTS.md): gates by role, spaces in Generated and the jobs feed, sign-in."""
import json

import pytest
from flask import Flask, jsonify

import routes.auth as auth
from routes.auth import core, gates, spaces
from routes.auth.api import auth_api

DESTS = [
    {"id": "_recent", "access": "guest", "has_bucket": True},
    {"id": "north-screen", "access": "guest", "has_bucket": True},
    {"id": "devtest", "has_bucket": True},
]


@pytest.fixture
def on(monkeypatch, tmp_path):
    """Accounts on, with a known destination list, one admin and a fresh spaces DB and session key."""
    monkeypatch.setattr(core, "active", lambda: True)
    monkeypatch.setattr(gates, "active", lambda: True)
    monkeypatch.setattr(core, "client_id", lambda: "cid")
    monkeypatch.setattr(core.smconfig, "destinations", lambda *a: DESTS)
    monkeypatch.setattr(core, "people", lambda: {"boss@x.com": {"role": "admin", "name": "Boss"},
                                                 "kid@x.com": {"role": "member", "name": "Kid"}})
    monkeypatch.setattr(core.smconfig, "secret", lambda *p: tmp_path.joinpath(*p))
    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    monkeypatch.setattr(spaces, "_db", None)
    spaces._cache.clear()
    yield


@pytest.fixture
def app():
    app = Flask(__name__)
    app.register_blueprint(auth_api, url_prefix="/api")
    app.before_request(auth.gate)
    app.after_request(auth.after_request)

    @app.route("/api/<path:rest>", methods=["GET", "POST", "PUT", "DELETE"])
    def anything(rest):
        return jsonify({"ok": True, "space": auth.own_space(mint=False)})
    return app


def client_as(app, email=None, remote="192.168.1.50"):
    c = app.test_client()
    c.environ_base["REMOTE_ADDR"] = remote
    if email:
        c.set_cookie(core.SESSION_COOKIE, core.session_value(email, None))
    return c


def test_needed_roles_follow_the_rules(on):
    assert gates.needed("/api/schedulers/north-screen/pause", "POST") == ["guest"]
    assert gates.needed("/api/schedulers/devtest/pause", "POST") == ["admin"]
    assert "admin" in gates.needed("/api/schedulers/north-screen/schedule", "POST")
    assert gates.needed("/api/schedulers/events/throw", "POST") == []
    assert gates.needed("/api/schedulers/all/status", "GET") == []
    assert gates.needed("/api/schedulers", "GET") == ["admin"]
    assert gates.needed("/api/buckets/north-screen/complete", "GET") == ["guest"]
    assert gates.needed("/api/buckets/devtest/complete", "GET") == ["admin"]
    assert "admin" in gates.needed("/api/buckets/_recent/items", "GET")
    assert sorted(gates.needed("/api/buckets/add_image_to_new_bucket", "POST",
                               {"source_publish_destination": "_recent",
                                "target_publish_destination": "devtest"})) == ["admin", "guest", "guest"]
    assert gates.needed("/api/deployment", "GET") == []


def test_guest_operates_open_screens_only(on, app):
    guest = client_as(app)
    assert guest.post("/api/schedulers/north-screen/pause").status_code == 200
    assert guest.get("/api/buckets/north-screen/complete").status_code == 200
    r = guest.get("/api/buckets/devtest/complete")
    assert r.status_code == 401 and r.get_json()["needs"] == "admin"
    assert guest.post("/api/schedulers/north-screen/schedule", json={}).status_code == 401
    assert guest.post("/api/publish", json={"dest_bucket_id": "devtest", "src_bucket_id": "_recent"}).status_code == 401
    assert guest.post("/api/publish", json={"dest_bucket_id": "north-screen", "src_bucket_id": "_recent"}).status_code == 200
    assert guest.get("/api/jobs/costs").status_code == 401


def test_member_is_refused_admin_calls_with_403_and_admin_passes(on, app):
    assert client_as(app, "kid@x.com").get("/api/schedulers").status_code == 403
    assert client_as(app, "boss@x.com").get("/api/schedulers").status_code == 200
    assert client_as(app, "stranger@x.com").get("/api/schedulers").status_code == 403  # signed in, but a guest


def test_local_processes_pass_every_gate(on, app):
    assert client_as(app, remote="127.0.0.1").get("/api/schedulers").status_code == 200
    # through a proxy on this machine is not local
    c = client_as(app, remote="127.0.0.1")
    assert c.get("/api/schedulers", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 401


def test_accounts_off_changes_nothing(monkeypatch, app):
    monkeypatch.setattr(core, "active", lambda: False)
    monkeypatch.setattr(gates, "active", lambda: False)
    assert client_as(app).get("/api/schedulers").status_code == 200


def test_a_browser_gets_one_space_and_keeps_it(on, app):
    guest = client_as(app)
    r = guest.get("/api/auth/me")
    me = r.get_json()
    assert me == {"accounts": True, "google_client_id": "cid", "signed_in": False, "email": None, "name": None,
                  "role": "guest"}
    cookie = guest.get_cookie(core.SPACE_COOKIE)
    assert cookie and cookie.http_only
    assert guest.get("/api/x").get_json()["space"] == "g:" + cookie.value
    assert client_as(app, "kid@x.com").get("/api/x").get_json()["space"] == "u:kid@x.com"


def test_spaces_decide_what_shows(on, app):
    with app.test_request_context(environ_base={"REMOTE_ADDR": "10.0.0.2"},
                                  headers={"Cookie": f"{core.SPACE_COOKIE}=abcdefghijklmnopqrstuv"}):
        assert core.can_see_space("g:abcdefghijklmnopqrstuv")
        assert not core.can_see_space("g:someoneelse0000000000")
        assert not core.can_see_space(None)  # from before spaces: the admins'
        spaces.note_batch("b1", "g:abcdefghijklmnopqrstuv")
        spaces.note_batch("b2", "u:kid@x.com")
        visible = core.job_visibility()
        assert visible({"source": "generator", "batch_id": "b1"})
        assert not visible({"source": "generator", "batch_id": "b2"})
        assert visible({"source": "scheduler", "target": "north-screen"})
        assert not visible({"source": "scheduler", "target": "devtest"})
        assert not visible({"source": "art-director", "target": None})
    with app.test_request_context(environ_base={"REMOTE_ADDR": "10.0.0.2"}):
        app.preprocess_request()
    admin = client_as(app, "boss@x.com")
    with app.test_request_context(environ_base={"REMOTE_ADDR": "10.0.0.2"},
                                  headers={"Cookie": admin.get_cookie(core.SESSION_COOKIE).key + "="
                                           + admin.get_cookie(core.SESSION_COOKIE).value}):
        assert core.can_see_space(None) and not core.can_see_space("u:kid@x.com")
        assert core.can_see_space("u:kid@x.com", everyone=True)
        assert core.job_visibility()({"source": "generator", "batch_id": "b2"})


def test_sign_in_sets_the_session_and_adopts_the_browser_space(on, app, monkeypatch, tmp_path):
    monkeypatch.setattr("routes.auth.api.verify_google", lambda cred: {"email": "kid@x.com", "name": "K"})
    adopted = []
    monkeypatch.setattr(spaces, "adopt", lambda old, new: adopted.append((old, new)))
    c = client_as(app)
    c.get("/api/auth/me")
    browser = c.get_cookie(core.SPACE_COOKIE).value
    r = c.post("/api/auth/google", json={"credential": "tok"})
    assert r.status_code == 200 and r.get_json()["role"] == "member"
    assert c.get_cookie(core.SESSION_COOKIE)
    assert c.get("/api/auth/me").get_json()["email"] == "kid@x.com"
    import time
    for _ in range(50):
        if adopted:
            break
        time.sleep(0.01)
    assert adopted == [("g:" + browser, "u:kid@x.com")]
    c.post("/api/auth/signout")
    assert c.get("/api/auth/me").get_json()["signed_in"] is False


def test_verify_google_checks_audience(on, monkeypatch):
    from routes.auth import api

    class R:
        status_code = 200

        def __init__(self, claims):
            self._c = claims

        def json(self):
            return self._c
    good = {"aud": "cid", "iss": "https://accounts.google.com", "email": "A@x.com", "email_verified": "true"}
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: R(good))
    assert api.verify_google("t") == {"email": "a@x.com", "name": None}
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: R({**good, "aud": "other"}))
    with pytest.raises(ValueError):
        api.verify_google("t")


def test_adopt_moves_generated_sidecars(on, monkeypatch, tmp_path):
    recent = tmp_path / "_recent"
    recent.mkdir()
    (recent / "a.jpg").write_bytes(b"x")
    (recent / "a.jpg.json").write_text(json.dumps({"space": "g:old"}))
    (recent / "b.jpg").write_bytes(b"x")
    (recent / "b.jpg.json").write_text(json.dumps({"space": "g:other"}))
    monkeypatch.setattr("routes.bucketer.bucket_path", lambda name: tmp_path / name)
    spaces.note_batch("b", "g:old")
    assert spaces.adopt("g:old", "u:me@x.com") == 1
    assert json.loads((recent / "a.jpg.json").read_text())["space"] == "u:me@x.com"
    assert json.loads((recent / "b.jpg.json").read_text())["space"] == "g:other"
    assert spaces.batch_space("b") == "u:me@x.com"
