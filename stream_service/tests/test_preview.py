"""Live click-to-preview (preview.py): session reuse, cut re-cues, effect and transition previews, keepalive,
stop, validation, and the /dev/preview route's auth (no GPU: fake sessions; the API test starts no session)."""
from __future__ import annotations

import asyncio
import pathlib

import pytest
from aiohttp.test_utils import TestClient, TestServer

from stream_service import preview as P
from stream_service.plugins.base import ParamError
from stream_service.server import make_app

REPO_CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config" / "live"
TOKEN = "cd" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeFx:
    def __init__(self):
        self.puts = []

    async def put(self, body):
        self.puts.append(body)
        return {}


class FakeSession:
    def __init__(self, sid, body):
        self.id, self.body, self.state = sid, body, "holding"
        self.screen, self.gpu = body.get("screen", "tv"), body.get("gpu", 0)
        self.attach = {"url": f"ws://x/media/{sid}?k=k", "target_buffer_ms": 300}
        self.cues, self.renewed, self.went = [], 0, 0
        self.fx = FakeFx()

    async def go(self):
        self.went += 1
        self.state = "live"
        return {}

    async def cue(self, body):
        self.cues.append(body)
        return {}

    def renew(self):
        self.renewed += 1


class FakeMgr:
    def __init__(self):
        self.sessions, self.created, self.deleted = {}, [], []

    def gpu_names(self):
        return ["rtx4060", "p4000"]

    def tv(self, gpu, state="live"):                 # a non-preview (TV) session on a GPU
        s = FakeSession(f"tv-{gpu}", {"screen": "south-screen", "gpu": gpu})
        s.state = state
        self.sessions[s.id] = s

    async def create(self, body, host):
        s = FakeSession(f"s-{len(self.created)}", body)
        self.created.append(body)
        self.sessions[s.id] = s
        return s, True

    async def delete(self, sid):
        self.deleted.append(sid)
        self.sessions[sid].state = "ended"


class _Cls:
    def __init__(self, props):
        self._p = props

    def param_props(self):
        return self._p


class FakePresets:
    class registry:
        effects = {"glow": _Cls({"amount": {}}), "flat": _Cls({})}

    def resolve_transition(self, spec):
        if spec == "nope":
            raise ParamError("unknown_preset", "no such transition")
        return type("T", (), {"params": {"duration_s": 0.02}})()

    def resolve_effect(self, spec):
        return type("E", (), {"effect": spec.get("effect") or "glow"})()


@pytest.fixture
def pv(monkeypatch):
    monkeypatch.setattr(P, "HOLD_S", 0.01)
    return P.Previews(FakeMgr(), FakePresets())


async def test_first_show_creates_a_720p_cut_session_and_goes_live(pv):
    r = await pv.show({"client": "tab-1", "kind": "scene", "spec": "warp-stack-a"}, "h")
    (body,) = pv.mgr.created
    assert body["profile"] == "h264-720p30" and body["anchor"] == {"rgb": [0, 0, 0]}
    assert body["transitions"] == {"in": P.CUT, "out": P.CUT} and body["scene"] == "warp-stack-a"
    assert body["screen"].startswith("dev-preview-") and body["ttl_s"] == P.TTL_S and "auto" not in body
    s = pv.mgr.sessions[r["session"]]
    assert s.went == 1 and r["attach"]["url"] == s.attach["url"]


async def test_next_show_recues_the_same_session_with_a_cut(pv):
    await pv.show({"client": "tab-1", "kind": "scene", "spec": "a"}, "h")
    r = await pv.show({"client": "tab-1", "kind": "scene", "spec": {"source": "b"}}, "h")
    assert len(pv.mgr.created) == 1
    s = pv.mgr.sessions[r["session"]]
    assert s.cues == [{"to": {"source": "b"}, "via": P.CUT, "at": "now"}] and s.renewed == 1


async def test_tabs_get_their_own_sessions_and_an_ended_one_is_replaced(pv):
    a = await pv.show({"client": "tab-1", "kind": "scene", "spec": "a"}, "h")
    b = await pv.show({"client": "tab-2", "kind": "scene", "spec": "a"}, "h")
    assert a["session"] != b["session"]
    pv.mgr.sessions[a["session"]].state = "ended"
    c = await pv.show({"client": "tab-1", "kind": "scene", "spec": "a"}, "h")
    assert c["session"] not in (a["session"], b["session"]) and len(pv.mgr.created) == 3


async def test_effect_runs_at_amount_1_on_the_fx_bus_over_scene_a(pv):
    r = await pv.show({"client": "tab-1", "kind": "effect", "spec": {"effect": "glow"}}, "h")
    assert pv.mgr.created[0]["scene"] == P.SCENE_A and r["fx_id"] == P.FX_ID
    s = pv.mgr.sessions[r["session"]]
    assert s.fx.puts == [{"stack": [{"id": P.FX_ID, "effect": "glow", "params": {"amount": 1.0}}], "fade_s": 0.0}]
    await pv.show({"client": "tab-1", "kind": "effect", "spec": {"effect": "flat"}}, "h")   # no amount param
    assert s.fx.puts[-1]["stack"] == [{"id": P.FX_ID, "effect": "flat"}]
    assert s.cues == []                                       # effect -> effect keeps scene A running
    await pv.show({"client": "tab-1", "kind": "scene", "spec": "a"}, "h")
    assert s.fx.puts[-1] == {"stack": [], "fade_s": 0.0} and s.cues[-1]["to"] == "a"   # the bus is cleared


async def test_an_explicit_amount_is_kept(pv):
    r = await pv.show({"client": "tab-1", "kind": "effect", "spec": {"effect": "glow", "params": {"amount": 0.3}}}, "h")
    assert pv.mgr.sessions[r["session"]].fx.puts[-1]["stack"][0]["params"] == {"amount": 0.3}


async def test_transition_loops_between_two_scenes_until_the_next_click(pv):
    r = await pv.show({"client": "tab-1", "kind": "transition", "spec": "wipe-x"}, "h")
    s = pv.mgr.sessions[r["session"]]
    await asyncio.sleep(0.15)
    assert len(s.cues) >= 2
    assert s.cues[0] == {"to": P.SCENE_B, "via": "wipe-x", "at": "now"}
    assert s.cues[1] == {"to": P.SCENE_A, "via": "wipe-x", "at": "now"}
    await pv.show({"client": "tab-1", "kind": "scene", "spec": "a"}, "h")     # a new click stops the loop
    n = len(s.cues)
    await asyncio.sleep(0.1)
    assert len(s.cues) == n and s.cues[-1]["via"] == P.CUT


async def test_bad_transition_is_a_400_before_anything_starts(pv):
    with pytest.raises(ParamError):
        await pv.show({"client": "tab-1", "kind": "transition", "spec": "nope"}, "h")
    assert pv.mgr.created == []


async def test_keepalive_and_stop(pv):
    assert pv.keepalive({"client": "tab-1"}) == {"session": None}
    r = await pv.show({"client": "tab-1", "kind": "scene", "spec": "a"}, "h")
    assert pv.keepalive({"client": "tab-1"})["session"] == r["session"]
    assert (await pv.stop("tab-1")) == {"ended": r["session"]}
    assert pv.mgr.deleted == [r["session"]] and pv.keepalive({"client": "tab-1"}) == {"session": None}


@pytest.mark.parametrize("body", [{}, {"client": "x"}, {"client": "bad client!"}, {"client": "tab-1", "kind": "nope", "spec": "a"},
                                  {"client": "tab-1", "kind": "scene"}])
async def test_validation(pv, body):
    with pytest.raises(ParamError):
        await pv.show(body, "h")


@pytest.fixture
async def client():
    app = make_app(token=TOKEN, config_dir=REPO_CONFIG)
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_route_needs_no_password(client):
    for method in ("POST", "DELETE"):
        r = await client.request(method, "/dev/preview?client=tab-1", json={"client": "tab-1"})
        assert r.status != 401


async def test_route_validates_and_keepalive_without_a_session(client):
    r = await client.post("/dev/preview", json={"client": "tab-1", "kind": "nope", "spec": "a"}, headers=AUTH)
    assert r.status == 400
    r = await client.post("/dev/preview", json={"client": "tab-1", "keepalive": True}, headers=AUTH)
    assert r.status == 200 and (await r.json()) == {"session": None}
    r = await client.delete("/dev/preview?client=tab-1", headers=AUTH)
    assert r.status == 200 and (await r.json()) == {"ended": None}
    r = await client.get("/dev/preview/x.mp4")          # the old clip route is untouched (open, 404 here)
    assert r.status == 404


# -- ratings ----------------------------------------------------------------------------------------------------------
from stream_service.ratings import Ratings   # noqa: E402


def test_ratings_set_merge_clear_and_persist(tmp_path):
    r = Ratings(tmp_path / "ratings.json")
    assert r.set({"key": "scene/st_x", "stars": 4})["entry"]["stars"] == 4
    e = r.set({"key": "scene/st_x", "tags": ["Great ", "loud", "great"]})["entry"]
    assert e["stars"] == 4 and e["tags"] == ["great", "loud"]
    e = r.set({"key": "scene/st_x", "reject": True})["entry"]
    assert e["reject"] is True and "stars" not in e                       # reject replaces the stars
    e = r.set({"key": "scene/st_x", "stars": 2})["entry"]
    assert e["stars"] == 2 and "reject" not in e
    assert Ratings(tmp_path / "ratings.json").all()["scene/st_x"]["tags"] == ["great", "loud"]   # persisted
    assert r.set({"key": "scene/st_x", "stars": 0, "tags": []})["entry"] is None                # all cleared
    assert r.all() == {}


@pytest.mark.parametrize("body", [{"key": "nope", "stars": 1}, {"key": "scene/x", "stars": 6}, {"key": "scene/x", "stars": True},
                                  {"key": "scene/x", "reject": 1}, {"key": "scene/x", "tags": ["<b>"]}, {"key": "scene/x", "x": 1}])
def test_ratings_validation(tmp_path, body):
    with pytest.raises(ParamError):
        Ratings(tmp_path / "ratings.json").set(body)


async def test_rating_routes(tmp_path):
    app = make_app(token=TOKEN, config_dir=tmp_path)
    async with TestClient(TestServer(app)) as c:
        r = await c.post("/dev/rate", json={"key": "scene/st_x", "stars": 5}, headers=AUTH)
        assert r.status == 200 and (await r.json())["entry"]["stars"] == 5
        r = await c.post("/dev/rate", json={"key": "scene/st_x", "stars": 9}, headers=AUTH)
        assert r.status == 400
        r = await c.get("/dev/ratings")
        assert r.status == 200 and (await r.json())["ratings"]["scene/st_x"]["stars"] == 5


async def test_preview_avoids_a_gpu_with_a_tv_session(pv):
    r = await pv.show({"client": "tab-1", "kind": "scene", "spec": "a"}, "h")
    assert pv.mgr.created[-1]["gpu"] == 1                       # default: GPU 1
    pv.mgr.tv(1)                                                # the south TV goes live on GPU 1
    r2 = await pv.show({"client": "tab-1", "kind": "scene", "spec": "b"}, "h")
    assert pv.mgr.created[-1]["gpu"] == 0 and r2["session"] != r["session"]   # moved to the free GPU
    assert pv.mgr.deleted == [r["session"]]
    pv.mgr.tv(0)                                                # both busy: GPU 1 (the default)
    await pv.show({"client": "tab-2", "kind": "scene", "spec": "a"}, "h")
    assert pv.mgr.created[-1]["gpu"] == 1


async def test_ended_tv_sessions_do_not_count(pv):
    pv.mgr.tv(1, state="ended")
    await pv.show({"client": "tab-1", "kind": "scene", "spec": "a"}, "h")
    assert pv.mgr.created[-1]["gpu"] == 1


def test_ratings_saved_looks(tmp_path):
    r = Ratings(tmp_path / "ratings.json")
    looks = [{"name": " warm ", "params": {"seed": 12, "field": "rings", "glow": 0.4, "invert": False, "tint": "#ff8800"}}]
    e = r.set({"key": "scene/st_x", "looks": looks})["entry"]
    assert e["looks"] == [{"name": "warm", "params": looks[0]["params"]}]
    e = r.set({"key": "scene/st_x", "reject": True})["entry"]
    assert e["looks"][0]["name"] == "warm"                                  # a reject keeps the looks
    assert r.set({"key": "scene/st_x", "reject": False, "looks": []})["entry"] is None


@pytest.mark.parametrize("looks", [{}, [{"name": "a"}], [{"name": "", "params": {}}], [{"name": "<b>", "params": {}}],
                                   [{"name": "a", "params": {"bad key": 1}}], [{"name": "a", "params": {"x": [1]}}],
                                   [{"name": "a", "params": {"x": float("nan")}}], [{"name": "a", "params": {}, "z": 1}],
                                   [{"name": f"l{i}", "params": {}} for i in range(25)]])
def test_ratings_looks_validation(tmp_path, looks):
    with pytest.raises(ParamError):
        Ratings(tmp_path / "ratings.json").set({"key": "scene/x", "looks": looks})
