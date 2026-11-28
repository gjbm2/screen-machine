"""The generation API (routes/api_v1; docs/API.md) and its keys (routes/auth/keys.py): keys made, used, rolled and
revoked; the gates' `role: key` rules; generations run, read, limited, replayed and cancelled. The generator itself
is a stand-in (process_generate_image_request), so nothing reaches RunPod."""
import base64
import io
import threading
import time

import pytest
from flask import Flask
from PIL import Image

import routes.auth as auth
import routes.api_v1 as api
from routes.api_v1 import inputs, runs
from routes.auth import core, gates, keys, spaces
from routes.auth.api import auth_api

DESTS = [
    {"id": "_recent", "access": "guest"},
    {"id": "north-screen", "name": "North", "access": "guest"},
    {"id": "devtest", "name": "Devtest"},
]
WORKFLOWS = [
    {"id": "krea2-turbo.json", "name": "Photographic", "input": ["text"], "default": True,
     "params": [{"id": "width", "type": "number", "min": 256, "max": 4096}, {"id": "seed", "type": "number"},
                {"id": "height", "type": "select", "options": ["864", "1536"]}]},   # bare options, as the catalogue has
    {"id": "qwen21-edit.json", "name": "Adapt", "input": ["image", "text"],
     "params": [{"id": "shape", "type": "select", "options": [{"value": "auto"}, {"value": "square"}]},
                {"id": "skip-upscaling", "type": "bool", "hidden": True}]},
    {"id": "old.json", "name": "Old", "input": ["text"], "hidden": True},
]
REFINERS = [{"id": "none", "name": "No refiner"}, {"id": "enrich", "name": "Enrich"}, {"id": "film", "hidden": True}]


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Accounts on, one admin, a fresh accounts DB, the API on, a stand-in catalogue and generator."""
    monkeypatch.setattr(core, "active", lambda: True)
    monkeypatch.setattr(gates, "active", lambda: True)
    monkeypatch.setattr(core, "client_id", lambda: "cid")
    monkeypatch.setattr(core.smconfig, "destinations", lambda *a: DESTS)
    monkeypatch.setattr(core, "people", lambda: {"boss@x.com": {"role": "admin", "name": "Boss"}})
    monkeypatch.setattr(core.smconfig, "secret", lambda *p: tmp_path.joinpath(*p))
    monkeypatch.setattr(core.smconfig, "enabled", lambda c: True)
    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    monkeypatch.setattr(spaces, "_db", None)
    spaces._cache.clear()
    keys._hits.clear()
    keys._used.clear()
    monkeypatch.setattr(api, "_workflows", lambda: [w for w in WORKFLOWS if not w.get("hidden")])
    monkeypatch.setattr(api, "_refiners", lambda: [r for r in REFINERS if not r.get("hidden")])
    recent = tmp_path / "_recent"
    recent.mkdir()
    import routes.bucketer
    monkeypatch.setattr(routes.bucketer, "bucket_path", lambda name: tmp_path / name)
    calls = []
    gate = {"release": threading.Event(), "fail": None}
    gate["release"].set()

    def fake_generate(data, uploaded_images=None, job_source="generator", job_ids=None):
        calls.append({"data": data, "images": uploaded_images, "source": job_source})
        gate["release"].wait(10)
        if gate["fail"]:
            raise ValueError(gate["fail"])
        out = []
        for i in range(data["params"]["batch"]):
            name = f"{data['batch_id']}-{i}.jpg"
            Image.new("RGB", (8, 8)).save(recent / name)
            out.append({"recent_file": name, "seed": 42 + i, "final_prompt": "refined " + data["prompt"],
                        "workflow": data["workflow"] or "krea2-turbo.json", "full_workflow": {"big": 1}})
        return {"images": out}
    import routes.generate_utils
    monkeypatch.setattr(routes.generate_utils, "process_generate_image_request", fake_generate)
    yield {"calls": calls, "gate": gate, "recent": recent}
    gate["release"].set()


@pytest.fixture
def app():
    app = Flask(__name__)
    app.register_blueprint(auth_api, url_prefix="/api")
    app.register_blueprint(api.api_v1, url_prefix="/api")
    app.before_request(auth.gate)
    app.after_request(auth.after_request)

    @app.route("/api/buckets/<path:rest>")
    def other(rest):
        return {"ok": True}

    @app.route("/api/v1/probe")
    def probe():
        return {"private": core.private_ok()}
    return app


def admin(app):
    c = app.test_client()
    c.environ_base["REMOTE_ADDR"] = "192.168.1.50"
    c.set_cookie(core.SESSION_COOKIE, core.session_value("boss@x.com", "Boss"))
    return c


def caller(app, secret=None, remote="203.0.113.9"):
    c = app.test_client()
    c.environ_base["REMOTE_ADDR"] = remote
    if secret:
        c.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {secret}"
    return c


def make_key(app, **fields):
    r = admin(app).post("/api/auth/keys", json={"name": "Agent", **fields})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["key"], r.get_json()["secret"]


def b64_png():
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------- keys

def test_key_is_shown_once_and_only_its_hash_is_kept(env, app):
    key, secret = make_key(app)
    assert secret.startswith(f"smk_{key['id']}_") and key["hint"].endswith(secret[-4:])
    listed = admin(app).get("/api/auth/keys").get_json()
    assert listed["keys"][0]["id"] == key["id"]
    assert secret not in str(listed) and "hash" not in listed["keys"][0]
    stored = spaces._conn().execute("SELECT hash FROM api_keys").fetchone()[0]
    assert secret.split("_", 2)[2] not in stored and len(stored) == 64
    assert key["space"] == "u:boss@x.com" and key["scopes"] == ["generate"]


def test_only_admins_manage_keys(env, app):
    guest = caller(app)
    assert guest.get("/api/auth/keys").status_code == 401
    _, secret = make_key(app)
    r = caller(app, secret).get("/api/auth/keys")
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "wrong_api"


def test_key_works_on_v1_only_and_a_bad_one_never(env, app):
    _, secret = make_key(app)
    assert caller(app, secret).get("/api/v1/me").status_code == 200
    assert caller(app, secret).get("/api/buckets/north-screen/complete").status_code == 403
    for bad in ("smk_aaaaaaaaaaaa_" + "x" * 43, secret[:-1] + ("A" if secret[-1] != "A" else "B"), "smk_nope"):
        r = caller(app, bad).get("/api/v1/me")
        assert r.status_code == 401 and r.get_json()["error"]["code"] == "invalid_key"
    r = caller(app).get("/api/v1/me")
    assert r.status_code == 401 and r.get_json()["error"]["code"] == "key_required"
    assert "Bearer" in r.headers["WWW-Authenticate"]


def test_x_api_key_header_and_other_bearer_tokens(env, app):
    _, secret = make_key(app)
    c = app.test_client()
    c.environ_base.update(REMOTE_ADDR="203.0.113.9", HTTP_X_API_KEY=secret)
    assert c.get("/api/v1/me").status_code == 200
    # the admin API's own bearer token is not an API key
    c = app.test_client()
    c.environ_base.update(REMOTE_ADDR="203.0.113.9", HTTP_AUTHORIZATION="Bearer admintoken")
    assert c.get("/api/buckets/north-screen/complete").status_code == 200


def test_index_and_spec_are_open(env, app):
    assert caller(app).get("/api/v1").status_code == 200
    spec = caller(app).get("/api/v1/openapi.json").get_json()
    assert spec["openapi"].startswith("3.1") and "/generations" in spec["paths"]
    assert spec["servers"][0]["url"].endswith("/api/v1")


def test_revoke_is_immediate_and_final(env, app):
    key, secret = make_key(app)
    assert admin(app).delete(f"/api/auth/keys/{key['id']}").get_json()["key"]["state"] == "revoked"
    r = caller(app, secret).get("/api/v1/me")
    assert r.status_code == 401 and r.get_json()["error"]["code"] == "key_revoked"
    assert admin(app).post(f"/api/auth/keys/{key['id']}/roll", json={}).status_code == 400


def test_expired_key_is_refused(env, app):
    key, secret = make_key(app)
    keys._write(key["id"], {"expires_at": time.time() - 1})
    assert caller(app, secret).get("/api/v1/me").get_json()["error"]["code"] == "key_expired"
    _, forever = make_key(app, expires_days=None)
    assert caller(app, forever).get("/api/v1/me").get_json()["expires_at"] is None


def test_roll_with_and_without_grace(env, app):
    key, old = make_key(app)
    r = admin(app).post(f"/api/auth/keys/{key['id']}/roll", json={"grace_hours": 24}).get_json()
    new = r["secret"]
    assert new != old and new.startswith(f"smk_{key['id']}_")
    assert caller(app, old).get("/api/v1/me").status_code == 200
    assert caller(app, new).get("/api/v1/me").status_code == 200
    newer = admin(app).post(f"/api/auth/keys/{key['id']}/roll", json={"grace_hours": 0}).get_json()["secret"]
    assert caller(app, new).get("/api/v1/me").status_code == 401
    assert caller(app, old).get("/api/v1/me").status_code == 401
    assert caller(app, newer).get("/api/v1/me").status_code == 200


def test_bad_key_fields_are_refused(env, app):
    assert admin(app).post("/api/auth/keys", json={"name": ""}).status_code == 400
    assert admin(app).post("/api/auth/keys", json={"name": "a", "scopes": ["root"]}).status_code == 400
    assert admin(app).post("/api/auth/keys", json={"name": "a", "per_day": -1}).status_code == 400


def test_last_use_is_noted(env, app):
    key, secret = make_key(app)
    c = caller(app, secret)
    c.environ_base["HTTP_X_FORWARDED_FOR"] = "198.51.100.7, 10.0.0.1"
    c.get("/api/v1/me")
    row = keys.get(key["id"])
    assert row["last_used_ip"] == "198.51.100.7" and row["uses"] == 1 and row["last_used_at"]


def test_rate_limit(env, app, monkeypatch):
    monkeypatch.setattr(keys, "REQUESTS_PER_MIN", 3)
    _, secret = make_key(app)
    c = caller(app, secret)
    assert [c.get("/api/v1/me").status_code for _ in range(4)] == [200, 200, 200, 429]
    r = c.get("/api/v1/me")
    assert r.get_json()["error"]["code"] == "rate_limited" and int(r.headers["Retry-After"]) >= 1


# ---------------------------------------------------------------- the catalogue

def test_workflows_and_refiners(env, app):
    _, secret = make_key(app)
    wf = caller(app, secret).get("/api/v1/workflows").get_json()["data"]
    assert [w["id"] for w in wf] == ["krea2-turbo", "qwen21-edit"]
    assert wf[1]["images"] == 1 and [p["id"] for p in wf[1]["params"]] == ["shape"]
    assert wf[0]["params"][2]["options"] == [{"value": "864", "label": "864"}, {"value": "1536", "label": "1536"}]
    ok = caller(app, secret).post("/api/v1/generations", json={"prompt": "x", "workflow": "krea2-turbo",
                                                               "params": {"height": 864}, "wait": 5})
    assert ok.get_json()["status"] == "succeeded", ok.get_json()
    bad = caller(app, secret).post("/api/v1/generations", json={"prompt": "x", "workflow": "krea2-turbo",
                                                                "params": {"height": 5}})
    assert bad.get_json()["error"]["code"] == "bad_params"
    assert [r["id"] for r in caller(app, secret).get("/api/v1/refiners").get_json()["data"]] == ["none", "enrich"]


def test_links_are_https_for_a_name(env, app):
    """Waitress drops the proxy's X-Forwarded-Proto: a name is the https proxy's, an address is as asked."""
    def server(host):
        r = app.test_client().get("/api/v1/openapi.json", base_url=f"http://{host}")
        return r.get_json()["servers"][0]["url"]
    assert server("screen.example.com") == "https://screen.example.com/api/v1"
    assert server("192.168.1.205:8080") == "http://192.168.1.205:8080/api/v1"
    assert server("127.0.0.1:5000") == "http://127.0.0.1:5000/api/v1"
    assert server("localhost:5000") == "http://localhost:5000/api/v1"


def test_private_catalogue_follows_the_key(env, app):
    _, plain = make_key(app)
    _, private = make_key(app, private=True)
    assert caller(app, plain).get("/api/v1/probe").get_json() == {"private": False}
    assert caller(app, private).get("/api/v1/probe").get_json() == {"private": True}


def test_destinations_need_publish(env, app):
    _, plain = make_key(app)
    assert caller(app, plain).get("/api/v1/destinations").get_json()["data"] == []
    _, pub = make_key(app, scopes=["publish"], destinations=["north-screen"])
    assert caller(app, pub).get("/api/v1/destinations").get_json()["data"] == [{"id": "north-screen", "name": "North"}]


# ---------------------------------------------------------------- generations

def test_generation_runs_and_lands_in_the_makers_space(env, app):
    _, secret = make_key(app)
    c = caller(app, secret)
    r = c.post("/api/v1/generations", json={"prompt": "a fox", "n": 2, "params": {"seed": 7}, "wait": 5})
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "succeeded", body
    assert len(body["outputs"]) == 2 and body["outputs"][0]["prompt"] == "refined a fox"
    assert body["outputs"][0]["url"].endswith(f"/output/_recent/{body['id']}-0.jpg")
    call = env["calls"][0]
    assert call["source"] == "api" and call["data"]["space"] == "u:boss@x.com"
    assert call["data"]["params"] == {"seed": 7, "batch": 2} and call["data"]["workflow"] is None
    assert spaces.batch_space(body["id"]) == "u:boss@x.com"
    # the stored result is trimmed
    assert "full_workflow" not in str(runs.get(body["id"], body and keys.all_keys()[0]["id"])["result"])
    # its file, with the key
    f = c.get(f"/api/v1/generations/{body['id']}/outputs/1")
    assert f.status_code == 200 and f.mimetype == "image/jpeg"
    assert c.get(f"/api/v1/generations/{body['id']}/outputs/2").status_code == 404
    listed = c.get("/api/v1/generations").get_json()["data"]
    assert [g["id"] for g in listed] == [body["id"]]


def test_generation_is_async_and_long_polls(env, app):
    env["gate"]["release"].clear()
    _, secret = make_key(app)
    c = caller(app, secret)
    r = c.post("/api/v1/generations", json={"prompt": "slow"})
    assert r.status_code == 202 and r.get_json()["status"] == "queued"
    gen = r.get_json()["id"]
    assert r.headers["Location"].endswith(f"/api/v1/generations/{gen}")
    assert c.get(f"/api/v1/generations/{gen}").get_json()["status"] == "queued"
    threading.Timer(0.3, env["gate"]["release"].set).start()
    assert c.get(f"/api/v1/generations/{gen}?wait=5").get_json()["status"] == "succeeded"


def test_generation_failure_is_reported(env, app):
    env["gate"]["fail"] = "RunPod said no"
    _, secret = make_key(app)
    body = caller(app, secret).post("/api/v1/generations", json={"prompt": "x", "wait": 5}).get_json()
    assert body["status"] == "failed" and body["error"]["message"] == "RunPod said no"


def test_other_keys_cannot_see_a_generation(env, app):
    _, a = make_key(app)
    _, b = make_key(app)
    gen = caller(app, a).post("/api/v1/generations", json={"prompt": "x", "wait": 5}).get_json()["id"]
    assert caller(app, b).get(f"/api/v1/generations/{gen}").status_code == 404
    assert caller(app, b).get(f"/api/v1/generations/{gen}/outputs/0").status_code == 404
    assert caller(app, b).get("/api/v1/generations").get_json()["data"] == []


def test_requests_are_checked(env, app):
    _, secret = make_key(app)
    c = caller(app, secret)

    def code(body):
        return c.post("/api/v1/generations", json=body).get_json()["error"]["code"]
    assert code({"prompt": "x", "colour": "red"}) == "bad_request"
    assert code({"prompt": "x", "workflow": "nope"}) == "unknown_workflow"
    assert code({"prompt": "x", "workflow": "old"}) == "unknown_workflow"           # hidden
    assert code({"prompt": "x", "refiner": "film"}) == "unknown_refiner"            # hidden
    assert code({"prompt": "x", "n": 9}) == "bad_request"
    assert code({"prompt": "x", "workflow": "krea2-turbo", "params": {"shape": "square"}}) == "bad_params"
    assert code({"prompt": "x", "workflow": "krea2-turbo", "params": {"width": 10}}) == "bad_params"
    assert code({"prompt": "x", "workflow": "qwen21-edit", "params": {"shape": "round"}}) == "bad_params"
    assert code({"prompt": "x", "workflow": "qwen21-edit"}) == "images_required"
    assert code({}) == "bad_request"
    assert code({"prompt": "x", "images": [{"data": "not base64!"}]}) == "bad_image"
    assert code({"prompt": "x", "publish_to": "nowhere"}) == "scope_missing"      # the gate, before the screen
    assert c.post("/api/v1/generations", data="x").get_json()["error"]["code"] == "bad_request"
    assert env["calls"] == []


def test_images_by_data_and_by_own_output(env, app):
    _, secret = make_key(app)
    c = caller(app, secret)
    first = c.post("/api/v1/generations", json={"prompt": "base", "wait": 5}).get_json()
    r = c.post("/api/v1/generations", json={
        "prompt": "make it blue", "workflow": "qwen21-edit", "params": {"shape": "square"}, "wait": 5,
        "images": [{"data": "data:image/png;base64," + b64_png()}]})
    assert r.get_json()["status"] == "succeeded", r.get_json()
    assert env["calls"][-1]["data"]["workflow"] == "qwen21-edit.json" and len(env["calls"][-1]["images"]) == 1
    r = c.post("/api/v1/generations", json={"prompt": "again", "workflow": "qwen21-edit", "wait": 5,
                                            "images": [{"generation": first["id"], "output": 0}]})
    assert r.get_json()["status"] == "succeeded"
    _, other = make_key(app)
    r = caller(app, other).post("/api/v1/generations", json={"prompt": "steal", "images": [{"generation": first["id"]}]})
    assert r.get_json()["error"]["code"] == "bad_image"


def test_image_urls_must_be_public(env, app):
    for url in ("http://127.0.0.1/x.jpg", "http://192.168.1.205:8080/x.jpg", "http://localhost/x.jpg",
                "http://[::1]/x.jpg", "file:///etc/passwd", "http://169.254.169.254/latest"):
        with pytest.raises(inputs.Refused) as e:
            inputs.fetch(url)
        assert e.value.code in ("image_not_public", "bad_image", "image_unreachable"), url


def test_publish_needs_scope_and_the_screen(env, app):
    _, plain = make_key(app)
    r = caller(app, plain).post("/api/v1/generations", json={"prompt": "x", "publish_to": "north-screen"})
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "scope_missing"
    _, pub = make_key(app, scopes=["publish"], destinations=["north-screen"])
    r = caller(app, pub).post("/api/v1/generations", json={"prompt": "x", "publish_to": "devtest"})
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "destination_not_allowed"
    r = caller(app, pub).post("/api/v1/generations", json={"prompt": "x", "publish_to": "north-screen", "wait": 5})
    assert r.get_json()["status"] == "succeeded"
    assert env["calls"][-1]["data"]["params"]["publish_destination"] == "north-screen"


def test_limits(env, app):
    env["gate"]["release"].clear()
    _, secret = make_key(app, concurrent=1, per_day=3)
    c = caller(app, secret)
    assert c.post("/api/v1/generations", json={"prompt": "a"}).status_code == 202
    r = c.post("/api/v1/generations", json={"prompt": "b"})
    assert r.status_code == 429 and r.get_json()["error"]["code"] == "too_many_running" and r.headers["Retry-After"]
    env["gate"]["release"].set()
    time.sleep(0.3)
    r = c.post("/api/v1/generations", json={"prompt": "c", "n": 3})
    assert r.status_code == 429 and r.get_json()["error"]["code"] == "quota_exceeded"
    assert c.post("/api/v1/generations", json={"prompt": "d", "n": 2, "wait": 5}).status_code == 200
    me = c.get("/api/v1/me").get_json()
    assert me["usage"]["last_24h"] == 3 and me["per_day"] == 3


def test_idempotency(env, app):
    _, secret = make_key(app)
    c = caller(app, secret)
    h = {"Idempotency-Key": "abc"}
    first = c.post("/api/v1/generations", json={"prompt": "x", "wait": 5}, headers=h)
    again = c.post("/api/v1/generations", json={"prompt": "x"}, headers=h)
    assert again.get_json()["id"] == first.get_json()["id"] and again.headers["Idempotent-Replayed"] == "true"
    assert len(env["calls"]) == 1
    r = c.post("/api/v1/generations", json={"prompt": "y"}, headers=h)
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "idempotency_conflict"


def test_cancel(env, app, monkeypatch):
    env["gate"]["release"].clear()
    _, secret = make_key(app)
    c = caller(app, secret)
    gen = c.post("/api/v1/generations", json={"prompt": "x"}).get_json()["id"]
    cancelled = []

    class Tracker:
        def cancel(self, job_id):
            cancelled.append(job_id)
            env["gate"]["fail"] = "Cancelled"
            env["gate"]["release"].set()

        def get(self, job_id):
            return None
    import routes.jobs
    monkeypatch.setattr(routes.jobs, "get_tracker", lambda: Tracker())
    runs._running[gen]["job_ids"].append("job_1")
    body = c.delete(f"/api/v1/generations/{gen}").get_json()
    assert cancelled == ["job_1"] and body["status"] == "cancelled"


def test_restart_lost_generation_reads_as_failed(env, app):
    env["gate"]["release"].clear()
    _, secret = make_key(app)
    c = caller(app, secret)
    gen = c.post("/api/v1/generations", json={"prompt": "x"}).get_json()["id"]
    runs._running.pop(gen)
    body = c.get(f"/api/v1/generations/{gen}").get_json()
    assert body["status"] == "failed" and body["error"]["message"] == runs.RESTARTED


def test_api_off(env, app, monkeypatch):
    _, secret = make_key(app)
    monkeypatch.setattr(api.smconfig, "enabled", lambda c: c != "api")
    r = caller(app, secret).get("/api/v1/me")
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "api_off"


def test_key_gates_hold_with_accounts_off(env, app, monkeypatch):
    monkeypatch.setattr(core, "active", lambda: False)
    monkeypatch.setattr(gates, "active", lambda: False)
    assert caller(app).get("/api/v1/me").status_code == 401
    _, secret = make_key(app)
    assert caller(app, secret).get("/api/v1/me").status_code == 200


def test_local_process_without_a_key_passes(env, app):
    local = caller(app, remote="127.0.0.1")
    assert local.get("/api/v1/me").status_code == 200
    assert local.post("/api/v1/generations", json={"prompt": "x", "wait": 5}).get_json()["status"] == "succeeded"
