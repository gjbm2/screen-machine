"""Session state machine, events and teardown with a fake render engine (no GPU, no encoder).

The fake records the commands the API side submits; the tests play the engine's part by calling the
session's callbacks (on_media, on_transition, on_failed) on the loop, as Engine.post would.
"""
from __future__ import annotations

import asyncio
import base64
import json
import pathlib
import threading

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from stream_service import server as server_mod
from stream_service import sessions as sessions_mod
from stream_service.renderer import Engine, Retire, StartTransition, UpdateParams
from stream_service.server import MANAGER, make_app

REPO_CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config" / "live"
TOKEN = "cd" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeEngine:
    instances: list["FakeEngine"] = []

    def __init__(self, *, profile, gpu, post, listener, name="session"):
        self.profile, self.gpu, self.post, self.listener, self.name = profile, gpu, post, listener, name
        self.cmds: list = []
        self.started_with = None
        self.stopped = 0
        self.prepare_delay = 0.0
        FakeEngine.instances.append(self)

    def create_context(self):
        pass

    def prepare(self, source, params):
        if self.prepare_delay:
            threading.Event().wait(self.prepare_delay)
        source.prepared_params = dict(params)
        return source

    def prepare_effect(self, effect, params):
        if self.prepare_delay:
            threading.Event().wait(self.prepare_delay)
        fail = getattr(type(self), "fail_effects", set())
        if getattr(effect, "name", None) in fail:
            raise RuntimeError(f"{effect.name} exploded in setup")
        effect.prepared_params = dict(params)
        effect.ctx = None
        from stream_service.plugins.base import ParamEaser
        effect.easer = ParamEaser(effect.param_props(), params, 0.3)
        return effect

    def start(self, anchor):
        self.started_with = anchor

    def submit(self, cmd):
        self.cmds.append(cmd)

    def stop(self, timeout=5.0):
        self.stopped += 1

    def summary(self):
        ring = {"p50": None, "p95": None, "max": None, "n": 0}
        return {"frames": 0, "rendered": 0, "late_frames": 0, "repeats": 0, "resyncs": 0, "frames_encoded": 0,
                "render_ms": ring, "write_ms": ring, "slip_ms": ring, "host_ms": ring, "encode_ms": ring,
                "encode_queue": 0, "bitrate_mbps": None}

    def frame_records(self, since=-1):
        return []


@pytest.fixture
async def client(monkeypatch):
    FakeEngine.instances.clear()
    monkeypatch.setattr(sessions_mod, "Engine", FakeEngine)
    app = make_app(config_dir=REPO_CONFIG, max_sessions=3)
    async with TestClient(TestServer(app)) as c:
        yield c


def body(**kw):
    b = {"screen": "devtest", "gpu": 0, "profile": "h264-720p30", "anchor": {"rgb": [10, 20, 30]},
         "scene": "fractal-zoom", "transitions": {"in": "crossfade-1s", "out": "crossfade-1s"}}
    b.update(kw)
    return b


async def create(client, **kw):
    r = await client.post("/api/sessions", json=body(**kw), headers=AUTH)
    assert r.status == 201, await r.text()
    res = await r.json()
    return res, client.app[MANAGER].sessions[res["session"]]


async def make_ready(s):
    s.on_media(0, True, b"\x02\x01\x00\x00\x00\x00frag")
    await asyncio.sleep(0)


async def test_lifecycle_states_events_and_hold(client):
    events = await client.ws_connect(f"/api/events?token={TOKEN}")
    res, s = await create(client)
    sid = res["session"]
    assert res["state"] == "starting" and s.state == "starting"
    assert (await client.post(f"/api/sessions/{sid}/go", json={}, headers=AUTH)).status == 409   # not holding
    await make_ready(s)
    r = await client.post(f"/api/sessions/{sid}/exit", json={"target": {"rgb": [1, 2, 3]}}, headers=AUTH)
    assert r.status == 409                                                       # no exit before go
    assert s.state == "holding"

    r = await client.post(f"/api/sessions/{sid}/go", json={}, headers=AUTH)
    assert r.status == 200 and (await r.json())["state"] == "entering"
    eng = FakeEngine.instances[-1]
    go_cmd = eng.cmds[-1]
    assert isinstance(go_cmd, StartTransition) and go_cmd.kind == "in"
    assert (await client.post(f"/api/sessions/{sid}/go", json={}, headers=AUTH)).status == 409
    s.on_transition("in", "started", 2.0, go_cmd.tid, False)
    s.on_transition("in", "done", 3.0, go_cmd.tid, False)
    assert s.state == "live"

    r = await client.post(f"/api/sessions/{sid}/exit", json={"target": {"rgb": [1, 2, 3]}}, headers=AUTH)
    assert r.status == 202 and (await r.json())["state"] == "exiting"
    out = eng.cmds[-1]
    assert isinstance(out, StartTransition) and out.kind == "out"
    s.on_transition("out", "started", 5.0, out.tid, False)
    s.on_transition("out", "done", 6.0, out.tid, False)
    assert s.state == "held" and s.hold_pts == 6.0

    # exit again from held (additive): a new out, a new hold_pts
    r = await client.post(f"/api/sessions/{sid}/exit", json={"target": {"rgb": [9, 9, 9]}, "transition": "cut"},
                          headers=AUTH)
    assert r.status == 202
    out2 = eng.cmds[-1]
    assert s.hold_pts is None and s.state == "exiting"
    s.on_transition("out", "started", 7.0, out2.tid, False)
    s.on_transition("out", "done", 7.0, out2.tid, False)
    assert s.state == "held" and s.hold_pts == 7.0

    assert (await client.delete(f"/api/sessions/{sid}", headers=AUTH)).status == 204
    assert s.state == "ended" and eng.stopped == 1
    assert (await client.delete(f"/api/sessions/{sid}", headers=AUTH)).status == 204   # idempotent
    assert (await client.post(f"/api/sessions/{sid}/go", json={}, headers=AUTH)).status == 409

    seen = []
    while True:
        msg = await events.receive_json(timeout=2)
        if msg["session"] == sid:
            seen.append(msg)
        if msg["type"] == "session.ended":
            break
    kinds = [(e["type"], e.get("state") or e.get("phase") or e.get("reason") or e.get("hold_pts")) for e in seen]
    assert kinds[:3] == [("session.state", "starting"), ("session.state", "holding"), ("session.ready", None)]
    assert ("session.held", 6.0) in kinds and ("session.held", 7.0) in kinds
    assert kinds[-2:] == [("session.state", "ended"), ("session.ended", "deleted")]
    assert all(e["screen"] == "devtest" for e in seen)
    await events.close()


async def test_superseded_out_does_not_hold(client):
    res, s = await create(client)
    await make_ready(s)
    await s.go()
    s.on_transition("in", "done", 1.0, s.in_tid, False)
    await s.exit({"target": {"rgb": [1, 1, 1]}})
    first = s.exit_tid
    await s.exit({"target": {"rgb": [2, 2, 2]}, "transition": "cut"})   # re-exit while exiting
    s.on_transition("out", "done", 2.0, s.exit_tid, False)              # the cut completes first ...
    s.on_transition("out", "done", 2.0, first, True)                    # ... discarding the first out
    assert s.state == "held" and s.hold_pts == 2.0
    # a late, non-superseded done of the replaced out transition must not move hold_pts either
    s.on_transition("out", "done", 3.0, first, False)
    assert s.hold_pts == 2.0


async def test_patch_in_holding_rebuilds_the_pending_scene(client):
    """In holding the scene is not visible: new params must apply from its first frame, not ease after go."""
    res, s = await create(client)
    await make_ready(s)
    eng = FakeEngine.instances[-1]
    old = s.pending_scene
    r = await client.patch(f"/api/sessions/{res['session']}", json={"params": {"preset": "rabbit"}},
                           headers=AUTH)
    assert r.status == 200 and (await r.json())["scene_params"]["preset"] == "rabbit"
    assert s.pending_scene is not old and s.pending_scene.prepared_params["preset"] == "rabbit"
    assert isinstance(eng.cmds[-1], Retire) and eng.cmds[-1].obj is old
    assert not any(isinstance(c, UpdateParams) for c in eng.cmds)
    await s.go()
    assert eng.cmds[-1].source.prepared_params["preset"] == "rabbit"
    # live: params are an eased update of the visible scene, not a rebuild
    s.on_transition("in", "done", 1.0, s.in_tid, False)
    r = await client.patch(f"/api/sessions/{res['session']}", json={"params": {"zoom_rate": 0.5}}, headers=AUTH)
    assert r.status == 200
    assert isinstance(eng.cmds[-1], UpdateParams) and eng.cmds[-1].source is s.current_scene
    assert eng.cmds[-1].params["zoom_rate"] == 0.5 and eng.cmds[-1].params["preset"] == "rabbit"


async def test_go_during_a_holding_patch_starts_the_rebuilt_scene(client):
    """A go that arrives while a holding PATCH is still preparing waits for it: one in cue, new params."""
    res, s = await create(client)
    await make_ready(s)
    eng = FakeEngine.instances[-1]
    eng.prepare_delay = 0.3
    patch = asyncio.create_task(client.patch(f"/api/sessions/{res['session']}",
                                             json={"params": {"preset": "rabbit"}}, headers=AUTH))
    await asyncio.sleep(0.05)                     # the PATCH is now preparing the rebuilt scene
    await s.go()
    assert (await patch).status == 200
    starts = [(c.kind, c.source.prepared_params["preset"]) for c in eng.cmds if isinstance(c, StartTransition)]
    assert starts == [("in", "rabbit")]


async def test_overlapping_holding_patches_apply_one_on_another(client):
    """A holding PATCH that waited for the lock builds on the one before it: neither is lost after its 200."""
    res, s = await create(client)
    await make_ready(s)
    eng = FakeEngine.instances[-1]
    eng.prepare_delay = 0.3
    url = f"/api/sessions/{res['session']}"
    original = s.pending_scene
    p1 = asyncio.create_task(client.patch(url, json={"params": {"preset": "rabbit"}}, headers=AUTH))
    await asyncio.sleep(0.05)                     # PATCH 1 holds the session lock, preparing
    p2 = asyncio.create_task(client.patch(url, json={"params": {"zoom_rate": 0.5}}, headers=AUTH))
    r1, r2 = await p1, await p2
    assert r1.status == 200 and r2.status == 200, (await r1.text(), await r2.text())
    assert s.state == "holding"
    pp = s.pending_scene.prepared_params
    assert pp["preset"] == "rabbit" and pp["zoom_rate"] == 0.5
    assert s.scene_spec.params["preset"] == "rabbit" and s.scene_spec.params["zoom_rate"] == 0.5
    final = (await r2.json())["scene_params"]
    assert final["preset"] == "rabbit" and final["zoom_rate"] == 0.5
    retired = [c.obj for c in eng.cmds if isinstance(c, Retire)]
    assert len(retired) == 2 and retired[0] is original and s.pending_scene not in retired
    assert not any(isinstance(c, UpdateParams) for c in eng.cmds)


async def test_params_patch_while_go_holds_the_lock_eases_the_visible_scene(client):
    """A params PATCH that waited behind go is an entering edit once go has run: 200 + UpdateParams, not 409."""
    res, s = await create(client)
    await make_ready(s)
    eng = FakeEngine.instances[-1]
    eng.ctx = object()                            # go sets its transition up on a worker thread (R20) ...

    def prepare_transition(t):
        threading.Event().wait(0.2)               # ... slowly (GPU set-up), holding the session lock meanwhile
        return t
    eng.prepare_transition = prepare_transition
    sid = res["session"]
    go = asyncio.create_task(client.post(f"/api/sessions/{sid}/go", json={}, headers=AUTH))
    await asyncio.sleep(0.05)
    assert s.state == "holding" and s.lock.locked()
    r = await client.patch(f"/api/sessions/{sid}", json={"params": {"zoom_rate": 0.5}}, headers=AUTH)
    assert (await go).status == 200
    assert r.status == 200, await r.text()
    assert s.state == "entering" and s.scene_spec.params["zoom_rate"] == 0.5
    upd = eng.cmds[-1]
    assert isinstance(upd, UpdateParams) and upd.source is s.current_scene and upd.params["zoom_rate"] == 0.5
    assert [c.kind for c in eng.cmds if isinstance(c, StartTransition)] == ["in"]
    assert not any(isinstance(c, Retire) for c in eng.cmds)   # no scene prepared only to be thrown away


async def test_params_patch_queued_behind_a_holding_patch_and_go(client):
    """PATCH 1 (holding) -> go -> PATCH 2 queued on the lock: one in cue with PATCH 1, then PATCH 2 eases it."""
    res, s = await create(client)
    await make_ready(s)
    eng = FakeEngine.instances[-1]
    eng.prepare_delay = 0.3
    url = f"/api/sessions/{res['session']}"
    p1 = asyncio.create_task(client.patch(url, json={"params": {"preset": "rabbit"}}, headers=AUTH))
    await asyncio.sleep(0.05)                     # PATCH 1 holds the session lock, preparing
    go = asyncio.create_task(s.go())              # go queues on the lock first ...
    await asyncio.sleep(0.01)
    p2 = asyncio.create_task(client.patch(url, json={"params": {"zoom_rate": 0.5}}, headers=AUTH))  # ... PATCH 2 next
    r1 = await p1
    await go
    r2 = await p2
    assert r1.status == 200 and r2.status == 200, (await r1.text(), await r2.text())
    assert s.state == "entering"
    starts = [(c.kind, c.source.prepared_params["preset"]) for c in eng.cmds if isinstance(c, StartTransition)]
    assert starts == [("in", "rabbit")]
    upd = eng.cmds[-1]
    assert isinstance(upd, UpdateParams) and upd.source is s.current_scene
    assert upd.params["preset"] == "rabbit" and upd.params["zoom_rate"] == 0.5
    assert s.scene_spec.params["preset"] == "rabbit" and s.scene_spec.params["zoom_rate"] == 0.5
    assert sum(isinstance(c, Retire) for c in eng.cmds) == 1   # only the original pending scene


async def test_scene_change_while_live_uses_in_transition_by_default(client):
    res, s = await create(client)
    await make_ready(s)
    await s.go()
    eng = FakeEngine.instances[-1]
    r = await client.patch(f"/api/sessions/{res['session']}", json={"scene": "black"}, headers=AUTH)
    assert r.status == 200 and (await r.json())["scene"] == "black"
    cmd = eng.cmds[-1]
    assert isinstance(cmd, StartTransition) and cmd.kind == "scene"
    assert cmd.transition.duration_s == 1.0   # the session's in: crossfade-1s
    assert (await client.patch(f"/api/sessions/{res['session']}", json={"bogus": 1}, headers=AUTH)).status == 400


async def test_failure_moves_to_failed_and_ends_media(client):
    res, s = await create(client)
    await make_ready(s)
    ws = await client.ws_connect(res["attach"]["url"].split(str(client.port), 1)[1])
    s.on_failed("encoder exited rc=1")
    assert s.state == "failed" and s.error == "encoder exited rc=1"
    assert (await ws.receive_json(timeout=2)) == {"type": "eos"}
    summary = await (await client.get(f"/api/sessions/{res['session']}", headers=AUTH)).json()
    assert summary["state"] == "failed" and summary["error"] == "encoder exited rc=1"
    assert (await client.post(f"/api/sessions/{res['session']}/go", json={}, headers=AUTH)).status == 409


async def test_ttl_reaper_ends_sessions(client):
    res, s = await create(client)
    s.expires_at = 0.0
    for _ in range(40):
        if s.state == "ended":
            break
        await asyncio.sleep(0.1)
    assert s.state == "ended" and s.end_reason == "ttl"


async def test_concurrent_idempotent_creates_and_session_limit(client):
    # three concurrent creates for the same key -> one session
    rs = await asyncio.gather(*(client.post("/api/sessions", json=body(idempotency_key="k-1"), headers=AUTH)
                                for _ in range(3)))
    ids = {(await r.json())["session"] for r in rs}
    assert all(r.status == 201 for r in rs) and len(ids) == 1
    # max_sessions=3: two more distinct sessions fit, created concurrently (no double counting while starting)
    rs = await asyncio.gather(*(client.post("/api/sessions", json=body(idempotency_key=f"k-{i}"), headers=AUTH)
                                for i in (2, 3)))
    assert [r.status for r in rs] == [201, 201]
    r = await client.post("/api/sessions", json=body(), headers=AUTH)
    assert r.status == 503 and (await r.json())["error"] == "too_many_sessions"


async def test_events_snapshot_and_no_event_lost_during_handshake(client, monkeypatch):
    """An event emitted while the events socket's handshake is in progress must reach that socket."""
    res, s = await create(client)
    await make_ready(s)
    hub = client.app[server_mod.EVENTS]
    orig = web.WebSocketResponse.prepare

    async def prepare_and_emit(self, request):
        hub.emit({"type": "session.transition", "session": "s-x", "screen": "devtest", "kind": "in",
                  "phase": "started", "pts": 1.0})
        return await orig(self, request)

    monkeypatch.setattr(web.WebSocketResponse, "prepare", prepare_and_emit)
    ws = await client.ws_connect(f"/api/events?token={TOKEN}")
    first = await ws.receive_json(timeout=2)
    assert first == {"type": "session.state", "session": res["session"], "screen": "devtest", "state": "holding"}
    second = await ws.receive_json(timeout=2)
    assert second["type"] == "session.transition" and second["session"] == "s-x"
    await ws.close()


async def test_api_errors_are_json(client, monkeypatch):
    r = await client.get("/api/nope", headers=AUTH)
    assert r.status == 404 and (await r.json())["error"] == "not_found"
    r = await client.put("/api/sessions", headers=AUTH)
    assert r.status == 405 and (await r.json())["error"] == "method_not_allowed" and "Allow" in r.headers
    r = await client.get("/api/events", headers=AUTH)              # not a WebSocket upgrade
    assert r.status == 400
    r = await client.get(f"/api/events?token={TOKEN}")             # token but not an upgrade
    assert r.status == 400 and (await r.json())["error"] == "bad_request"


async def test_body_over_limit_is_json_413(monkeypatch):
    monkeypatch.setattr(server_mod, "MAX_BODY", 1024)
    app = make_app(config_dir=REPO_CONFIG)
    async with TestClient(TestServer(app)) as c:
        r = await c.post("/api/sessions", data=b"x" * 4096, headers={**AUTH, "Content-Type": "application/json"})
        assert r.status == 413 and (await r.json())["error"] == "too_large"


async def test_bad_png_is_400_before_a_session_exists(client):
    r = await client.post("/api/sessions", json=body(anchor={"png_b64": base64.b64encode(
        b"\x89PNG\r\n\x1a\n" + b"junk" * 10).decode()}), headers=AUTH)
    assert r.status == 400 and (await r.json())["error"] == "bad_image"
    assert client.app[MANAGER].sessions == {}


async def test_media_client_stats_are_sanitised(client):
    res, s = await create(client)
    ws = await client.ws_connect(res["attach"]["url"].split(str(client.port), 1)[1])
    await ws.send_str(json.dumps({"type": "stats", "presented": 10, "dropped": {"x": [1] * 1000},
                                  "buffered_ms": 250.5, "edge_jumps": True, "stalls": float("nan"),
                                  "extra": "ignored"}))
    for _ in range(20):
        st = s.media.summary()
        if st and st[0]["stats"]:
            break
        await asyncio.sleep(0.05)
    assert st[0]["stats"] == {"presented": 10, "dropped": None, "buffered_ms": 250.5, "edge_jumps": None,
                              "stalls": None}
    await ws.close()


# -- engine-level, no GPU ----------------------------------------------------------
class Closable:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


def test_commands_submitted_after_release_are_disposed_of():
    from stream_service.profiles import PROFILES

    eng = Engine(profile=PROFILES["h264-720p30"], gpu=0, post=lambda *a: None, listener=None, name="t")
    queued = Closable()
    eng.submit(Retire(queued))           # queued before stop: the release drains it
    eng.stop()
    assert queued.closed == 1
    late_src, late_tr, late = Closable(), Closable(), Closable()
    eng.submit(StartTransition("out", 1, late_src, late_tr))   # e.g. an exit target prepared as the session ended
    eng.submit(Retire(late))
    for _ in range(50):
        if late.closed and late_src.closed:
            break
        threading.Event().wait(0.02)
    assert (late_src.closed, late_tr.closed, late.closed) == (1, 1, 1)
    owned = Closable()
    eng.submit(UpdateParams(owned, {}))  # an UpdateParams' source belongs to the tree: never closed from here
    threading.Event().wait(0.1)
    assert owned.closed == 0




async def test_scene_auto_lets_the_director_pick_the_first_scene(client):
    """Owner, 15 Oct: modes carry tags / direction, the director picks the first scene too."""
    res, s = await create(client, scene="auto", auto={"enabled": True, "pool": ["warp-cells"]})
    assert s.scene_spec.name == "warp-cells"
    res, s = await create(client, screen="other", scene={"auto": True}, auto={"enabled": True, "pool": {"tags": {"energy": [0.9, 1.0]}}})
    assert s.scene_spec.name in s.mgr.presets.scenes
    r = await client.post("/api/sessions", json=body(screen="third", scene="auto", auto={"enabled": True, "pool": ["nope"]}), headers=AUTH)
    assert r.status == 201                           # an empty auto pool falls back to the whole vetted pool
