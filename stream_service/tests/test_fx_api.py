"""The master FX bus API (GET / PUT /fx), scene-attached effects, direction.fx / auto.fx and the catalog
(EFFECTS_LAYER_DESIGN D; fake engine, no GPU)."""
from __future__ import annotations

import asyncio

import pytest

from stream_service.effects import EffectChain, FxUpdate
from stream_service.renderer import Retire, StartTransition

from aiohttp.test_utils import TestClient, TestServer

from stream_service import sessions as sessions_mod
from stream_service.server import make_app

from .fxkit import fx_config, fx_registry
from .test_cue import AUTO, cue, live
from .test_sessions import AUTH, TOKEN, FakeEngine, create, make_ready  # noqa: F401


@pytest.fixture
async def client(monkeypatch, tmp_path):
    """The app on the test effects (tests/fxkit.py) and a config dir with their presets: the engine and API are
    under test, not the plug-in files."""
    FakeEngine.instances.clear()
    monkeypatch.setattr(sessions_mod, "Engine", FakeEngine)
    app = make_app(config_dir=fx_config(tmp_path), max_sessions=3, registry=fx_registry())
    async with TestClient(TestServer(app)) as c:
        yield c


async def put(client, sid, **body):
    return await client.put(f"/api/sessions/{sid}/fx", json=body, headers=AUTH)


async def get(client, sid):
    r = await client.get(f"/api/sessions/{sid}/fx", headers=AUTH)
    assert r.status == 200
    return await r.json()


def fx_updates(eng):
    return [c for c in eng.cmds if isinstance(c, FxUpdate)]


async def events_until(ws, pred, timeout=2.0):
    seen = []
    while True:
        m = await ws.receive_json(timeout=timeout)
        seen.append(m)
        if pred(m):
            return seen


async def test_put_get_shapes_ids_and_order(client):
    sid, s, eng = await live(client)
    r = await put(client, sid, stack=["hue-drift-slow", {"effect": "shake"}, {"id": "mine", "preset": "chroma-soft",
                                                                             "level": 0.5}], fade_s=1.5)
    assert r.status == 200, await r.text()
    res = await r.json()
    assert res["rev"] == 1 and res["fade_s"] == 1.5 and res["session"] == sid
    ids = [x["id"] for x in res["stack"]]
    assert ids == ["mine", "fx-1", "fx-2"]                     # stage order: glitch, colour, motion
    stages = [x["stage"] for x in res["stack"]]
    assert stages == ["glitch", "colour", "motion"]
    e = res["stack"][0]
    assert set(e) >= {"id", "effect", "preset", "label", "stage", "params", "bindings", "level", "bypass", "origin",
                      "state", "level_now", "amount_now", "cost_ms"}
    assert e["effect"] == "chroma" and e["preset"] == "chroma-soft" and e["level"] == 0.5 and e["origin"] == "manual"
    assert e["bindings"]["pulse"]["bind"] == "beat" and "pulse" not in e["params"]   # default pulse <- beat
    assert e["state"] == "fading_in"
    cmd = fx_updates(eng)[-1]
    assert cmd.rev == 1 and len(cmd.add) == 3 and cmd.fade_s == 1.5 and cmd.update == []
    assert all(sl.level == 0.0 for sl in cmd.add) and sorted(sl.target for sl in cmd.add) == [0.5, 1.0, 1.0]
    assert (await get(client, sid))["stack"] == res["stack"]
    assert s.summary()["fx"]["rev"] == 1 and len(s.summary()["fx"]["stack"]) == 3


async def test_diff_by_id_and_by_plugin_update_remove_bypass(client):
    sid, s, eng = await live(client)
    res = await (await put(client, sid, stack=["hue-drift-slow", {"effect": "shake"}])).json()
    slots = {x["id"]: x for x in res["stack"]}
    e_hue = fx_updates(eng)[-1].add[0].slot_id if fx_updates(eng)[-1].add[0].name == "hue_drift" else \
        fx_updates(eng)[-1].add[1].slot_id
    n_add = len(fx_updates(eng))
    # by plug-in (no id): shake matched, params eased; hue drift gone (fades out); grade added
    r = await put(client, sid, stack=[{"effect": "shake", "params": {"max_shift": 0.03}}, "grade-warm",
                                      {"id": "fx-1", "preset": "hue-drift-slow", "bypass": True}], if_rev=1)
    res2 = await r.json()
    assert r.status == 200 and res2["rev"] == 2
    cmd = fx_updates(eng)[-1]
    assert len(fx_updates(eng)) == n_add + 1
    assert [sl.name for sl in cmd.add] == ["grade"]
    upd = {u.id: u for u in cmd.update}
    hue_u = upd[e_hue]
    assert hue_u.target == 0.0 and not hue_u.remove and hue_u.params is None      # bypass keeps the slot
    shake_u = next(u for u in cmd.update if u.params is not None)
    assert shake_u.params["max_shift"] == 0.03 and shake_u.target == 1.0
    st = {x["id"]: x for x in res2["stack"]}
    assert st["fx-1"]["bypass"] is True and slots["fx-2"]["effect"] == "shake" and st["fx-2"]["params"]["max_shift"] == 0.03
    # remove everything: every slot fades out
    r = await put(client, sid, stack=[], fade_s=0)
    cmd = fx_updates(eng)[-1]
    assert (await r.json())["stack"] == [] and all(u.remove for u in cmd.update) and len(cmd.update) == 3
    # the engine reports the retirements
    s.on_fx({"retired": [u.id for u in cmd.update]})
    assert s.fx.retiring == {}


async def test_if_rev_conflict_and_validation_errors(client):
    sid, s, eng = await live(client)
    assert (await put(client, sid, stack=["grade-warm"])).status == 200
    r = await put(client, sid, stack=[], if_rev=0)
    assert r.status == 409 and (await r.json())["error"] == "fx_conflict"
    cases = [
        ({"stack": ["grade-warm"] * 7}, "too_many_effects"),
        ({"stack": ["nope"]}, "unknown_preset"),
        ({"stack": [{"effect": "nope"}]}, "unknown_effect"),
        ({"stack": [{"effect": "passthrough"}]}, "unknown_effect"),                     # internal
        ({"stack": [{"effect": "chroma", "params": {"bogus": 1}}]}, "unknown_param"),
        ({"stack": [{"effect": "chroma", "params": {"spread": {"bind": "beat"}}}]}, "bad_param"),   # not bindable
        ({"stack": [{"effect": "chroma", "params": {"amount": "x"}}]}, "bad_param"),
        ({"stack": [{"effect": "chroma", "params": {"amount": 0.2}, "bindings": {"amount": {"bind": "beat"}}}]},
         "bad_spec"),
        ({"stack": [{"effect": "chroma", "colour": 1}]}, "bad_spec"),
        ({"stack": [{"id": "BAD ID", "effect": "chroma"}]}, "bad_request"),
        ({"stack": [{"id": "a", "effect": "chroma"}, {"id": "a", "effect": "shake"}]}, "bad_request"),
        ({"stack": "grade-warm"}, "bad_request"),
        ({"stack": [], "master": {"gain": 1}}, "bad_request"),
        ({"stack": [], "bogus": 1}, "bad_request"),
    ]
    rev = (await get(client, sid))["rev"]
    for body, code in cases:
        r = await put(client, sid, **body)
        assert r.status == 400 and (await r.json())["error"] == code, (body, await r.text())
    assert (await get(client, sid))["rev"] == rev                          # nothing changed


async def test_bindings_master_and_inputs_fx_bindings(client):
    sid, s, eng = await live(client)
    r = await put(client, sid, stack=[{"effect": "shake", "params": {"amount": {"bind": "activity_slow",
                                                                                 "range": [0.2, 0.8]}}}],
                  master={"amount": 1.0, "pulse": {"bind": "level_slow", "range": [0.5, 1.0]}})
    res = await r.json()
    assert r.status == 200
    b = res["stack"][0]["bindings"]
    assert b["amount"] == {"bind": "activity_slow", "range": [0.2, 0.8], "rest": 0.5} and b["pulse"]["bind"] == "beat"
    assert res["master"]["pulse"]["bind"] == "level_slow" and res["master"]["amount"] == 1.0
    cmd = fx_updates(eng)[-1]
    assert cmd.master["pulse"]["rest"] == 0.75
    inp = await (await client.get(f"/api/sessions/{sid}/inputs", headers=AUTH)).json()
    assert {(x["slot"], x["param"]) for x in inp["fx_bindings"]} == {("fx-1", "amount"), ("fx-1", "pulse")}


async def test_put_in_holding_is_stored_and_409_after_exit(client):
    res, s = await create(client)
    sid = res["session"]
    await make_ready(s)
    assert (await put(client, sid, stack=["grade-warm"])).status == 200        # holding: stored
    assert s.fx.open is False
    await client.post(f"/api/sessions/{sid}/go", json={}, headers=AUTH)
    s.on_transition("in", "done", 2.0, s.in_tid, False)
    s.on_fx({"bus": "open"})
    assert s.fx.open is True
    epoch = s.fx.epoch
    r = await client.post(f"/api/sessions/{sid}/exit", json={"target": {"rgb": [1, 2, 3]}}, headers=AUTH)
    assert r.status == 202 and s.fx.epoch == epoch + 1
    r = await put(client, sid, stack=[])
    assert r.status == 409 and (await r.json())["error"] == "bad_state"


async def test_exit_during_prepare_retires_the_new_slots(client):
    sid, s, eng = await live(client)
    eng.prepare_delay = 0.3
    task = asyncio.create_task(put(client, sid, stack=["grade-warm"]))
    await asyncio.sleep(0.1)
    eng.prepare_delay = 0.0
    r = await client.post(f"/api/sessions/{sid}/exit", json={"target": {"rgb": [1, 2, 3]}}, headers=AUTH)
    assert r.status == 202
    r = await task
    assert r.status == 409
    assert not fx_updates(eng)
    retired = [c.obj for c in eng.cmds if isinstance(c, Retire)]
    assert any(getattr(o, "name", None) == "grade" for o in retired)


async def test_session_fx_events_manual_bus_and_error(client):
    events = await client.ws_connect(f"/api/events?token={TOKEN}")
    sid, s, eng = await live(client)
    await put(client, sid, stack=["grade-warm", "shake-beat"])
    seen = await events_until(events, lambda m: m["type"] == "session.fx")
    ev = seen[-1]
    assert ev["cause"] == "manual" and ev["rev"] == 1 and ev["added"] == ["fx-1", "fx-2"] and len(ev["stack"]) == 2
    assert set(ev["stack"][0]) == {"id", "effect", "preset", "label", "stage", "level", "bypass", "origin"}
    s.on_fx({"bus": "open"})
    ev = (await events_until(events, lambda m: m["type"] == "session.fx"))[-1]
    assert ev["cause"] == "bus" and ev["open"] is True
    # a bus slot failing in render: dropped from the stack, session.fx cause error + session.plugin_error
    eslot = fx_updates(eng)[0].add[0]
    s.on_plugin_error({"kind": "effect", "name": eslot.name, "phase": "render", "action": "removed",
                       "detail": "CUDADriverError: boom", "obj_id": id(eslot), "slot": eslot.slot_id,
                       "preset": eslot.preset, "where": "bus", "count": 1})
    seen = await events_until(events, lambda m: m["type"] == "session.plugin_error")
    fx_ev = next(m for m in seen if m["type"] == "session.fx")
    assert fx_ev["cause"] == "error" and fx_ev["removed"] == ["fx-1"] and fx_ev["rev"] == 2
    pe = seen[-1]
    assert pe["kind"] == "effect" and pe["slot"] == "fx-1" and pe["action"] == "removed" and pe["count"] == 1
    assert [x["id"] for x in (await get(client, sid))["stack"]] == ["fx-2"]
    assert (await get(client, sid))["errors"][0]["slot"] == "fx-1"
    assert fx_updates(eng)[-1].update[0].remove is True
    diag = await (await client.get("/api/diag", headers=AUTH)).json()
    assert diag["plugin_errors"][0]["name"] == "grade" and diag["plugin_errors"][0]["count"] == 1
    assert s.summary()["plugin_errors"] == 1
    await events.close()


async def test_prepare_failure_is_rejected_not_fatal(client, monkeypatch):
    monkeypatch.setattr(FakeEngine, "fail_effects", {"shake"}, raising=False)
    sid, s, eng = await live(client)
    r = await put(client, sid, stack=["grade-warm", "shake-beat"])
    res = await r.json()
    assert r.status == 200 and [x["effect"] for x in res["stack"]] == ["grade"]
    assert res["rejected"] == [{"index": 1, "effect": "shake", "error": "prepare_failed",
                                "detail": "RuntimeError: shake exploded in setup"}]


async def test_cue_with_scene_effects_carries_a_chain(client):
    sid, s, eng = await live(client)
    r = await cue(client, sid, to={"preset": "warp-cells", "effects": ["grade-warm", {"effect": "chroma"}]})
    assert r.status == 202, await r.text()
    cmd = next(c for c in reversed(eng.cmds) if isinstance(c, StartTransition))
    chain = cmd.source.fx_chain
    assert isinstance(chain, EffectChain) and [sl.name for sl in chain.slots] == ["chroma", "grade"]   # stage order
    assert all(sl.where == "scene" and sl.level == 1.0 for sl in chain.slots)
    assert chain.slots[0].modulation.bindings["pulse"]["bind"] == "beat"                   # default binding
    assert s.summary()["current_scene_effects"] == [{"effect": "grade", "preset": "grade-warm", "stage": "colour"},
                                                    {"effect": "chroma", "preset": None, "stage": "glitch"}]
    r = await cue(client, sid, to={"preset": "warp-cells", "effects": ["grade-warm"] * 5})
    assert r.status == 400 and (await r.json())["error"] == "too_many_effects"
    r = await cue(client, sid, to={"source": "warp_stack", "effects": [{"effect": "nope"}]})
    assert r.status == 400 and (await r.json())["error"] == "unknown_effect"


async def test_scene_effects_on_create_and_patch_and_empty_list(client):
    res, s = await create(client, scene={"preset": "warp-cells", "effects": ["shake-beat"]})
    assert [sl.name for sl in s.pending_scene.fx_chain.slots] == ["shake"]
    await make_ready(s)
    sid = res["session"]
    await client.post(f"/api/sessions/{sid}/go", json={}, headers=AUTH)
    s.on_transition("in", "done", 2.0, s.in_tid, False)
    r = await client.patch(f"/api/sessions/{sid}", json={"scene": {"preset": "warp-smoke", "effects": []}},
                           headers=AUTH)
    assert r.status == 200
    cmd = next(c for c in reversed(FakeEngine.instances[-1].cmds) if isinstance(c, StartTransition))
    assert getattr(cmd.source, "fx_chain", None) is None


async def test_scene_effect_prepare_failure_is_prepare_failed(client, monkeypatch):
    monkeypatch.setattr(FakeEngine, "fail_effects", {"chroma"}, raising=False)
    sid, s, eng = await live(client)
    r = await cue(client, sid, to={"preset": "warp-cells", "effects": ["chroma-soft"]})
    assert r.status == 500 and (await r.json())["error"] == "prepare_failed"


async def test_direction_fx_and_auto_fx(client):
    sid, s, eng = await live(client, auto=AUTO)
    assert s.summary()["direction"]["fx"] == {"density": 0.5, "rate": 0.5, "stages": None, "moods": None,
                                              "follow": None}
    assert s.summary()["auto"]["fx"]["enabled"] is False                       # off by default
    r = await client.patch(f"/api/sessions/{sid}", json={"direction": {"energy": 0.8, "fx": {"density": 1.0,
                                                                                              "stages": ["colour"],
                                                                                              "follow": 0.3}},
                                                         "auto": {"fx": {"enabled": True, "max": 3}}}, headers=AUTH)
    assert r.status == 200, await r.text()
    sm = await r.json()
    assert sm["direction"]["fx"] == {"density": 1.0, "rate": 0.5, "stages": ["colour"], "moods": None, "follow": 0.3}
    assert sm["auto"]["fx"]["enabled"] is True and sm["auto"]["fx"]["max"] == 3 and sm["auto"]["enabled"] is True
    assert s.fx_director.enabled and s.fx_director.fxd["density"] == 1.0 and s.fx_director.follow == 0.3
    for body in ({"direction": {"fx": {"stages": ["nope"]}}}, {"direction": {"fx": {"density": "x"}}},
                 {"direction": {"fx": {"bogus": 1}}}, {"direction": {"fx": {"follow": "x"}}},
                 {"auto": {"fx": {"max": 9}}}, {"auto": {"fx": {"enabled": 1}}}):
        r = await client.patch(f"/api/sessions/{sid}", json=body, headers=AUTH)
        assert r.status == 400 and (await r.json())["error"] == "bad_request", body
    # auto fx without scene auto
    r = await client.patch(f"/api/sessions/{sid}", json={"auto": {"enabled": False, "fx": {"enabled": True}}},
                           headers=AUTH)
    sm = await r.json()
    assert sm["auto"]["enabled"] is False and sm["auto"]["fx"]["enabled"] is True and sm["auto"]["fx"]["max"] == 3


async def test_fx_director_moves_through_the_session(client):
    sid, s, eng = await live(client, auto={**AUTO, "fx": {"enabled": True, "max": 2}},
                             direction={"fx": {"density": 1.0, "rate": 1.0}})
    s.on_fx({"bus": "open"})
    now = asyncio.get_running_loop().time()
    s.tick(now)
    assert s.fx_director.armed
    s.fx_director.next_at = 0.0                                                 # due now
    s.tick(now)
    await asyncio.sleep(0.05)
    while s._fx_task is not None:
        await asyncio.sleep(0.01)
    res = await get(client, sid)
    assert len(res["stack"]) == 1 and res["stack"][0]["origin"] == "auto"
    assert res["auto"]["last"]["op"] == "add" and res["auto"]["armed"] is True
    # a manual change to the auto slot makes it manual
    slot_id = res["stack"][0]["id"]
    r = await put(client, sid, stack=[{"id": slot_id, "preset": res["stack"][0]["preset"], "level": 0.4}])
    assert (await r.json())["stack"][0]["origin"] == "manual"
    # exit disarms for good
    await client.post(f"/api/sessions/{sid}/exit", json={"target": {"rgb": [0, 0, 0]}}, headers=AUTH)
    s.tick(now + 1)
    assert not s.fx_director.armed


async def test_catalog_effects_stages_and_presets(client):
    cat = await (await client.get("/api/catalog", headers=AUTH)).json()
    assert cat["stages"][0] == "warp" and cat["stages"][-1] == "overlay"
    names = {e["name"]: e for e in cat["effects"]}
    assert {"chroma", "shake", "hue_drift", "grade", "flash"} <= set(names)
    ch = names["chroma"]
    assert ch["stage"] == "glitch" and ch["origin"] == "core" and ch["schema"]["properties"]["amount"]["x-bindable"] \
        == "level" and ch["amount_mode"] == "native" and ch["license_class"] == "permissive"
    assert cat["presets"]["effects"]["grade-warm"]["effect"] == "grade"
    assert cat["presets"]["effects"]["grade-warm"]["stage"] == "colour"
    assert all("origin" in s for s in cat["sources"]) and all("tags" in t for t in cat["transitions"])
    assert cat["broken"] == [] and cat["plugins"]["counts"]["effects"] == 6      # incl. the internal passthrough


async def test_next_effect_adds_then_swaps_and_never_touches_manual(client):
    """The owner's Next for effects: a change now, whatever the schedule (or auto.fx) says."""
    sid, s, eng = await live(client, auto={**AUTO, "fx": {"enabled": False}})
    s.on_fx({"bus": "open"})
    r = await client.post(f"/api/sessions/{sid}/fx/next", headers=AUTH)
    first = await r.json()
    assert r.status == 200 and first["op"] == "add" and first["preset"]
    res = await get(client, sid)
    assert [x["origin"] for x in res["stack"]] == ["auto"]
    r = await client.post(f"/api/sessions/{sid}/fx/next", headers=AUTH)
    second = await r.json()
    assert r.status == 200 and second["op"] == "swap" and second["removed"] == res["stack"][0]["id"]
    # a hand-placed effect stays put: only the auto one changes
    res = await get(client, sid)
    manual = {"preset": second["preset"], "level": 0.5}
    r = await put(client, sid, stack=[*({"id": x["id"], "preset": x["preset"], "level": x["level"]} for x in res["stack"]), manual])
    r = await client.post(f"/api/sessions/{sid}/fx/next", headers=AUTH)
    res = await get(client, sid)
    assert r.status == 200 and sum(1 for x in res["stack"] if x["origin"] == "manual") >= 1


async def test_room_events_and_the_clock_reach_the_fx_director(client):
    sid, s, eng = await live(client, auto={**AUTO, "fx": {"enabled": True, "max": 2}})
    seen = []
    s.fx_director.on_event = lambda kind, now: seen.append(kind)
    assert s.fx_director.clock == s._fx_clock and s._fx_clock(0.0) is None       # no sure clock in tests
    s.mgr._room_event({"kind": "breakdown", "at_ms": 1000})                       # FX only: passes the filter
    s.mgr._room_event({"kind": "drop", "at_ms": 2000})
    s.mgr._room_event({"kind": "look_change", "at_ms": 3000})                     # neither cuts nor moves FX
    assert seen == ["breakdown", "drop"]
