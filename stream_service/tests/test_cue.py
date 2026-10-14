"""cue, auto, direction, palette and inputs on the session API, with the fake engine (LIVE_V1 1.1; no GPU)."""
from __future__ import annotations

import asyncio

import pytest

from stream_service.plugins.sources.image import ImageSource
from stream_service.renderer import Retire, StartTransition, UpdateParams
from stream_service.server import MANAGER

from .test_sessions import AUTH, TOKEN, FakeEngine, body, client, create, make_ready  # noqa: F401 (fixture)

AUTO = {"enabled": True, "dwell_s": [6, 12], "seed": 3}


async def live(client, **kw):
    res, s = await create(client, **kw)
    await make_ready(s)
    r = await client.post(f"/api/sessions/{res['session']}/go", json={}, headers=AUTH)
    assert r.status == 200
    go = await r.json()
    assert go["cue_id"] and go["duration_s"] == 1.0                     # additive on go (R3)
    s.on_transition("in", "started", 1.0, s.in_tid, False)
    s.on_transition("in", "done", 2.0, s.in_tid, False)
    assert s.state == "live"
    return res["session"], s, FakeEngine.instances[-1]


async def cue(client, sid, **b):
    return await client.post(f"/api/sessions/{sid}/cue", json=b, headers=AUTH)


async def test_cue_state_rules_and_bad_requests(client):
    res, s = await create(client, auto=AUTO)
    sid = res["session"]
    await make_ready(s)
    r = await cue(client, sid, to="warp-cells")
    assert r.status == 409 and "use go" in (await r.json())["detail"]             # holding: use go (R4)
    await client.post(f"/api/sessions/{sid}/go", json={}, headers=AUTH)
    assert (await cue(client, sid, to="warp-cells")).status == 202               # entering: accepted
    for b, code in (({"to": {"still": {"rgb": [0, 0, 0]}}}, "bad_to"), ({}, "bad_to"),
                    ({"to": "warp-cells", "at": "bar"}, "bad_at"), ({"to": "nope"}, "unknown_preset"),
                    ({"to": "warp-cells", "extra": 1}, "bad_request")):
        r = await cue(client, sid, **b)
        assert r.status == 400 and (await r.json())["error"] == code, b
    s.on_transition("in", "done", 2.0, s.in_tid, False)
    r = await client.post(f"/api/sessions/{sid}/exit", json={"target": {"rgb": [1, 2, 3]}}, headers=AUTH)
    ex = await r.json()
    assert r.status == 202 and ex["cue_id"] and ex["duration_s"] == 1.0          # additive on exit (R3)
    assert (await cue(client, sid, to="warp-cells")).status == 409               # exiting


async def test_cue_response_events_and_transition_fields(client):
    events = await client.ws_connect(f"/api/events?token={TOKEN}")
    sid, s, eng = await live(client)
    r = await cue(client, sid, to="warp-cells", via="melt-2s", at="beat")
    res = await r.json()
    assert r.status == 202 and set(res) >= {"session", "state", "cue_id", "at_pts", "duration_s"}
    assert res["duration_s"] == 2.0
    cmd = eng.cmds[-1]
    assert isinstance(cmd, StartTransition) and cmd.kind == "scene" and cmd.at_mono is None   # no beat: now
    s.on_transition("scene", "started", 3.0, cmd.tid, False)
    seen = []
    while True:
        m = await events.receive_json(timeout=2)
        if m.get("session") == sid:
            seen.append(m)
        if m["type"] == "session.transition" and m.get("cue_id") == res["cue_id"]:
            break
    cue_ev = next(e for e in seen if e["type"] == "session.cue")
    assert cue_ev["phase"] == "scheduled" and cue_ev["fallback"] == "no_beat" and cue_ev["at"] == "now"
    tr = seen[-1]
    assert tr["cause"] == "manual" and tr["scene"] == "warp-cells" and tr["transition"] == "melt-2s"
    go_ev = next(e for e in seen if e["type"] == "session.transition" and e["kind"] == "in")
    assert go_ev["cause"] == "go"
    await events.close()


async def test_idempotency_replay_and_conflict(client):
    sid, s, eng = await live(client)
    a = await (await cue(client, sid, to="warp-cells", idempotency_key="k1")).json()
    n = len(eng.cmds)
    b = await (await cue(client, sid, to="warp-cells", idempotency_key="k1")).json()
    assert a == b and len(eng.cmds) == n                                         # replay: same body, no new cue
    r = await cue(client, sid, to="warp-smoke", idempotency_key="k1")
    assert r.status == 409 and (await r.json())["error"] == "idempotency_conflict"


async def test_idempotency_key_with_a_big_body(client):
    # A body over bodies.FAST_PATH_MIN comes back from parse_body with each png_b64 value as PngData (bytes);
    # the cue's canonical form must not choke on it (a bad cue is a 400, never a 500).
    sid, s, eng = await live(client)
    to = {"scene": "warp-cells", "params": {"png_b64": "A" * 300_000}}
    r = await cue(client, sid, to=to, idempotency_key="big")
    assert r.status == 400, await r.text()


async def test_two_transitions_at_most_then_one_pending_slot(client):
    events = await client.ws_connect(f"/api/events?token={TOKEN}")
    sid, s, eng = await live(client)
    await cue(client, sid, to="warp-cells")
    await cue(client, sid, to="warp-smoke")
    assert len(s.inflight) == 2
    n = len(eng.cmds)
    await cue(client, sid, to="warp-liquid")                                     # waits in the slot
    assert s.pending["scene"] == "warp-liquid" and len(eng.cmds) == n
    await cue(client, sid, to="fractal-zoom")                                    # replaces it
    assert s.pending["scene"] == "fractal-zoom"
    assert isinstance(eng.cmds[-1], Retire)
    first = next(t for t in s.inflight)
    s.on_transition("scene", "done", 5.0, first, False)                          # a slot frees: it starts
    assert s.pending is None and eng.cmds[-1].kind == "scene" and s.current_name == "fractal-zoom"
    phases = []
    while True:
        m = await events.receive_json(timeout=2)
        if m["type"] == "session.cue":
            phases.append((m["scene"], m["phase"], m.get("reason")))
        if m["type"] == "session.cue" and m["scene"] == "fractal-zoom" and m["phase"] == "scheduled" \
                and not m.get("pending"):
            break
    assert ("warp-liquid", "cancelled", "replaced") in phases
    await events.close()


async def test_exit_cancels_pending_and_disarms_the_director(client):
    sid, s, eng = await live(client, auto=AUTO)
    s.tick(asyncio.get_running_loop().time())
    assert s.director.armed
    for scene in ("warp-cells", "warp-smoke", "warp-liquid"):
        await cue(client, sid, to=scene)
    assert s.pending is not None
    r = await client.post(f"/api/sessions/{sid}/exit", json={"target": {"rgb": [5, 5, 5]}}, headers=AUTH)
    assert r.status == 202
    assert s.pending is None and s.exited and not s.director.armed
    assert eng.cmds[-1].kind == "out"
    s.tick(asyncio.get_running_loop().time())
    assert not s.director.armed                                                  # never re-armed after exit
    assert isinstance(eng.started_with, ImageSource) and eng.started_with.modulation is None   # R1: the anchor
    assert eng.cmds[-1].source.modulation is None                                # ... and the exit target
    assert all(c.source.modulation is not None for c in eng.cmds
               if isinstance(c, StartTransition) and c.kind == "scene")          # scene sources are modulated


async def test_auto_cue_needs_an_auto_block(client):
    sid, s, eng = await live(client)
    r = await cue(client, sid, to="auto")
    assert r.status == 409 and (await r.json())["error"] == "auto_unavailable"
    r = await client.patch(f"/api/sessions/{sid}", json={"auto": {"enabled": False}}, headers=AUTH)
    assert r.status == 200
    r = await cue(client, sid, to="auto")                                        # a one-shot pick when disabled
    res = await r.json()
    assert r.status == 202
    cmd = eng.cmds[-1]
    assert cmd.kind == "scene" and s.cue_info[cmd.tid]["cause"] == "auto" and s.cue_info[cmd.tid]["reason"] == "next"
    assert s.current_name in s.mgr.presets.scenes and s.mgr.presets.scenes[s.current_name].get("tags")


async def test_look_patch_in_every_non_terminal_state_and_summary(client):
    res, s = await create(client, auto=AUTO, direction={"energy": 0.8, "pace": 1.0, "intensity": 0.5},
                          palette="ember", fixture="screen.south", beat_follow="off")
    sid = res["session"]
    assert s.runtime.speed == 2.0 and s.runtime.depth == 0.5                     # applied at once on create
    summ = await (await client.get(f"/api/sessions/{sid}", headers=AUTH)).json()
    assert summ["direction"]["energy"] == 0.8 and summ["palette"] == "ember" and summ["fixture"] == "screen.south"
    assert summ["auto"]["seed"] == 3 and summ["masters"]["speed"] == 2.0 and summ["beat_follow"] == "off"
    assert summ["current_scene"] == "fractal-zoom" and "lead_ms" in summ
    # starting: direction-only PATCH is fine; scene PATCH keeps today's rules
    r = await client.patch(f"/api/sessions/{sid}", json={"direction": {"pace": 0.0}, "palette": "ice"}, headers=AUTH)
    assert r.status == 200 and s.runtime.speed_target == 0.5 and s.palette == "ice"
    assert s.direction["energy"] == 0.5                                          # whole object: neutral default
    assert (await client.patch(f"/api/sessions/{sid}", json={"scene": "black"}, headers=AUTH)).status == 409
    for bad in ({"palette": "sepia"}, {"auto": {"paused": True}}, {"direction": {"energy": "x"}},
                {"beat_follow": "maybe"}):
        assert (await client.patch(f"/api/sessions/{sid}", json=bad, headers=AUTH)).status == 400, bad


async def test_patch_params_bindings_and_null_restore(client):
    sid, s, eng = await live(client)          # scene fractal-zoom: exposure bound to beat in the preset
    assert "exposure" in s.scene_spec.bindings
    r = await client.patch(f"/api/sessions/{sid}", json={"params": {"exposure": 1.5}}, headers=AUTH)
    assert r.status == 200 and "exposure" not in s.scene_spec.bindings
    cmd = eng.cmds[-1]
    assert isinstance(cmd, UpdateParams) and cmd.params["exposure"] == 1.5 and "exposure" not in cmd.bindings
    r = await client.patch(f"/api/sessions/{sid}", json={"params": {"exposure": None}}, headers=AUTH)
    assert r.status == 200 and s.scene_spec.bindings["exposure"]["bind"] == "beat"
    r = await client.patch(f"/api/sessions/{sid}", json={"params": {"zoom_rate": {"bind": "pulse", "range": [0, 1]}}},
                           headers=AUTH)
    assert r.status == 200 and eng.cmds[-1].bindings["zoom_rate"]["rest"] == 0.0


async def test_inputs_and_diag_without_a_bus(client):
    sid, s, eng = await live(client)
    r = await client.get(f"/api/sessions/{sid}/inputs", headers=AUTH)
    i = await r.json()
    assert r.status == 200 and i["online"] is False and i["stale"] == "link" and i["bus"]["configured"] is False
    assert i["signals"]["beat"] == 0.0 and i["signals"]["level"] == 0.5 and i["derived"]["bpm"] is None
    assert {b["param"] for b in i["bindings"]} >= {"exposure"} and i["masters"]["speed"] == 1.0
    assert i["lead"]["total_ms"] > 0 and i["lead"]["photon_source"]
    r = await client.get("/api/diag", headers=AUTH)
    d = await r.json()
    assert r.status == 200 and d["host"]["cpus"] >= 1 and d["verdict"]["level"] in ("ok", "warn", "bad")
    assert d["sessions"][0]["session"] == sid
    assert (await client.get("/api/diag")).status == 200   # no password


async def test_catalog_v11_blocks(client):
    cat = await (await client.get("/api/catalog", headers=AUTH)).json()
    assert cat["api"] == {"contract": "LIVE_V1", "rev": "1.1",
                          "features": ["cue", "auto", "direction", "palette", "inputs", "bindings", "effects",
                                       "fx_bus", "fx_auto", "plugin_errors", "gpu_guard"]}
    assert set(cat["vocab"]) == {"moods", "genre", "transition_style", "palette_family"}
    assert cat["palettes"]["ember"]["swatch"][0].startswith("#") and cat["signals"]["activity_slow"]["idle"] == 0.5
    assert cat["errors"] == []
    assert cat["presets"]["scenes"]["warp-kaleido"]["tags"]["energy"] == [0.4, 1.0]
    assert "tags" not in cat["presets"]["scenes"]["black"] and "tags" not in cat["presets"]["transitions"]["cut"]
    names = {t["name"] for t in cat["transitions"]}
    assert {"melt", "burn", "zoom", "luma", "wipe", "glitch", "pixelate"} <= names
