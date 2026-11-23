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
                                                 "kid@x.com": {"role": "approved", "name": "Kid"}})
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

    @app.route("/output/<path:rest>")
    def output(rest):
        return "file"
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


def test_approved_is_refused_admin_calls_with_403_and_admin_passes(on, app):
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
                  "role": "guest", "can_unlock": False, "pin_set": False, "unlocked_until": None}
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
    assert r.status_code == 200 and r.get_json()["role"] == "approved"
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


def test_people_page_sets_roles_and_keeps_an_admin(on, app, monkeypatch, tmp_path):
    accounts = tmp_path / "accounts.yaml"
    accounts.write_text("# header kept\ngoogle_client_id: cid\npeople:\n  - {email: boss@x.com, role: admin}\n")
    monkeypatch.setattr(core.smconfig, "path", lambda *p: tmp_path / p[-1])
    monkeypatch.setattr(core, "people", lambda: core._accounts_config()["by_email"])  # the file, not the fixture's
    monkeypatch.setattr(core, "client_id", lambda: "cid")
    spaces.note_person("kid@x.com", "Kid")
    admin = client_as(app, "boss@x.com")
    rows = admin.get("/api/auth/people").get_json()["people"]
    assert [(r["email"], r["role"]) for r in rows] == [("boss@x.com", "admin"), ("kid@x.com", "guest")]
    r = admin.put("/api/auth/people", json={"email": "kid@x.com", "role": "approved"})
    assert r.status_code == 200
    text = accounts.read_text()
    assert text.startswith("# header kept\n") and "google_client_id: cid" in text and "kid@x.com" in text
    assert core._accounts_config()["by_email"]["kid@x.com"]["role"] == "approved"
    assert admin.put("/api/auth/people", json={"email": "boss@x.com", "role": "guest"}).status_code == 409
    assert client_as(app, "kid@x.com").get("/api/auth/people").status_code == 403


def test_private_catalogue_only_for_approved_and_admins(on, app):
    with app.test_request_context(environ_base={"REMOTE_ADDR": "10.0.0.2"}):
        assert not core.private_ok()
    for email, ok in (("kid@x.com", True), ("boss@x.com", True), ("stranger@x.com", False)):
        c = client_as(app, email)
        with app.test_request_context(environ_base={"REMOTE_ADDR": "10.0.0.2"},
                                      headers={"Cookie": f"{core.SESSION_COOKIE}={c.get_cookie(core.SESSION_COOKIE).value}"}):
            assert core.private_ok() is ok
    # no request behind it: schedules, generation threads (pytest-flask keeps a request context pushed, so pretend)
    import flask
    with pytest.MonkeyPatch.context() as m:
        m.setattr(flask, "has_request_context", lambda: False)
        assert core.private_ok()


def test_catalogue_and_private_templates_follow_the_caller(on, app, monkeypatch, tmp_path):
    from routes import utils
    (tmp_path / "workflows.json").write_text('[{"id": "a.json"}]')
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "catalogue.json").write_text('[{"id": "p.json", "summary": "s"}]')
    (tmp_path / "private" / "t.txt.j2").write_text("PRIVATE")
    (tmp_path / "main.txt.j2").write_text('shared{% include "private/t.txt.j2" ignore missing %}')
    monkeypatch.setattr(utils, "findfile", lambda name: str(tmp_path / name))
    monkeypatch.setattr(utils.smconfig, "path", lambda *p: tmp_path.joinpath(*p))
    monkeypatch.setattr(utils, "find_template_file", lambda name: str(tmp_path / name))
    monkeypatch.setattr(utils, "SEARCH_PATHS", [str(tmp_path)])
    utils._json_cache.clear()
    with app.test_request_context(environ_base={"REMOTE_ADDR": "10.0.0.2"}):
        assert [w["id"] for w in utils._load_json_once("workflow", "workflows.json")] == ["a.json"]
        assert utils.dict_substitute("main.txt.j2") == "shared"
    import flask
    monkeypatch.setattr(flask, "has_request_context", lambda: False)  # work with no request behind it
    assert [w["id"] for w in utils._load_json_once("workflow", "workflows.json")] == ["a.json", "p.json"]
    assert utils.dict_substitute("main.txt.j2") == "sharedPRIVATE"


@pytest.fixture
def hidden(on, monkeypatch, tmp_path):
    """`on`, plus a hidden bucket (lock.py) and a fresh PIN file."""
    from routes.auth import clouds, lock
    monkeypatch.setattr(core.smconfig, "destinations",
                        lambda *a: DESTS + [{"id": "private", "has_bucket": True, "locked": True}])
    monkeypatch.setattr(lock, "_pin_file", lambda: tmp_path / "private" / "lock.yaml")
    monkeypatch.setattr(lock, "_ids", {"at": None, "ids": frozenset()})
    monkeypatch.setattr(clouds, "_cache", {"at": None, "clouds": []})
    return lock


def test_hidden_bucket_needs_an_admin_and_the_pin(hidden, app, monkeypatch):
    lock = hidden
    boss, kid = client_as(app, "boss@x.com"), client_as(app, "kid@x.com")
    for c in (boss, kid, client_as(app)):
        assert c.get("/api/buckets/private/complete").status_code == 404
        assert c.get("/output/private/a.jpg").status_code == 404
        assert c.post("/api/buckets/add_image_to_new_bucket", json={"source_publish_destination": "_recent",
                      "target_publish_destination": "private"}).status_code == 404
    assert client_as(app, remote="127.0.0.1").get("/api/buckets/private/complete").status_code == 200
    me = boss.get("/api/auth/me").get_json()
    assert me["can_unlock"] and not me["pin_set"] and me["unlocked_until"] is None
    assert kid.post("/api/auth/unlock", json={"pin": "1234"}).status_code == 403
    assert boss.post("/api/auth/unlock", json={"pin": "12"}).status_code == 400
    r = boss.post("/api/auth/unlock", json={"pin": "1234"})       # the first PIN becomes the PIN
    assert r.status_code == 200 and r.get_json()["unlocked_until"] > 0 and lock.pin_set()
    assert boss.get("/api/buckets/private/complete").status_code == 200
    assert boss.get("/output/private/a.jpg").status_code == 200 and boss.get("/output/cloud/a.jpg").status_code == 200
    assert kid.get("/api/buckets/private/complete").status_code == 404   # one browser's unlock only
    boss.post("/api/auth/lock")
    assert boss.get("/api/buckets/private/complete").status_code == 404
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert boss.post("/api/auth/unlock", json={"pin": "9999"}).status_code == 401
    assert boss.post("/api/auth/unlock", json={"pin": "1234"}).status_code == 200
    # 30 minutes on, it is hidden again
    import time as _time
    real = _time.time
    monkeypatch.setattr(_time, "time", lambda: real() + lock.MINUTES * 60 + 1)
    assert boss.get("/api/buckets/private/complete").status_code == 404


def test_hidden_bucket_stays_hidden_with_accounts_off(hidden, app, monkeypatch):
    monkeypatch.setattr(core, "active", lambda: False)
    monkeypatch.setattr(gates, "active", lambda: False)
    c = client_as(app)
    assert c.get("/api/buckets/private/complete").status_code == 404
    assert c.post("/api/auth/unlock", json={"pin": "4321"}).status_code == 200   # everyone is an admin
    assert c.get("/api/buckets/private/complete").status_code == 200


def test_each_person_has_their_own_cloud(hidden, app):
    from routes.auth import clouds
    kid_cloud = clouds.cloud_id("kid@x.com")
    assert [d["id"] for d in clouds.clouds()] == [kid_cloud]          # admins have none of their own
    assert clouds.clouds()[0]["name"] == "Kid's cloud" and clouds.owner_of(kid_cloud) == "kid@x.com"
    assert client_as(app, "kid@x.com").get(f"/api/buckets/{kid_cloud}/complete").status_code == 200
    assert client_as(app, "boss@x.com").get(f"/api/buckets/{kid_cloud}/complete").status_code == 200
    assert client_as(app, "stranger@x.com").get(f"/api/buckets/{kid_cloud}/complete").status_code == 403
    assert client_as(app).get(f"/api/buckets/{kid_cloud}/complete").status_code == 401
