"""The room's scene taste from live operator feedback (taste.py; screen-machine-63, 29 Oct): a quick Next is a skip, a
scene cued by hand a like, Keep this look on a picked scene a keep; scores per (plug-in, genre) and per plug-in decay
over 14 days and multiply the scene director's weights.  Also the variant chain: one plug-in runs at most
VARIANT_CHAIN_MAX songs through "a variant for the next song" (sessions.py; fake engine, no GPU)."""
from __future__ import annotations

import asyncio
import json
import math
import time

import pytest

from stream_service import sessions as sessions_mod
from stream_service import taste as T

from .test_cue import AUTO, cue, live
from .test_sessions import client  # noqa: F401 (fixture)

DAY = 86400.0


def test_multiplier_math_and_bounds():
    assert T.multiplier_of(0.0, 0.0) == 1.0
    assert T.multiplier_of(-1.0, -1.0) == pytest.approx(math.exp(-0.35 * 1.5))     # one skip, its own genre: x0.59
    assert T.multiplier_of(0.0, -1.0) == pytest.approx(math.exp(-0.35 * 0.5))      # elsewhere: x0.84
    assert T.multiplier_of(-20.0, -20.0) == T.MULT_MIN and T.multiplier_of(20.0, 20.0) == T.MULT_MAX
    assert T.decayed([2.0, 0.0], 14 * DAY) == pytest.approx(1.0) and T.decayed(None, 0.0) == 0.0
    assert T.context_of({"genre": " House "}) == "house" and T.context_of({}) == "-" and T.context_of(None) == "-"


def test_store_records_decays_dedupes_and_persists(tmp_path):
    path = tmp_path / "taste.json"
    t = T.Taste(path)
    t0 = 1_000_000.0
    assert t.multiplier("polyhedra", "disco", t0) == 1.0
    assert t.record("skip", "polyhedra", "disco", scene="polyhedra-morph", now=t0)
    assert not t.record("skip", "polyhedra", "disco", now=t0 + 2)                   # the other screen's copy: once
    assert not t.record("shrug", "polyhedra", "disco", now=t0 + 30) and not t.record("like", None, now=t0)
    assert t.multiplier("polyhedra", "disco", t0) == pytest.approx(math.exp(-0.35 * 1.5))
    assert t.multiplier("polyhedra", "jazz", t0) == pytest.approx(math.exp(-0.35 * 0.5))
    assert t.multiplier("polyhedra", "disco", t0 + 14 * DAY) == pytest.approx(math.exp(-0.35 * 0.75))   # half-life
    t.record("like", "fractal", "-", now=t0 + 20)
    t.record("keep", "fractal", "-", now=t0 + 40)
    assert t.multiplier("fractal", "-", t0 + 40) == pytest.approx(math.exp(0.35 * 1.5 * 1.5))
    assert t.counts == {"skip": 1, "like": 1, "keep": 1}
    v = t.view(t0 + 40)
    assert v["plugins"]["polyhedra"]["ctx"] == {"disco": -1.0} and v["recent"][0]["signal"] == "keep"
    t.save(now=t0 + 40)
    raw = json.loads(path.read_text())
    assert set(raw["plugins"]) == {"polyhedra", "fractal"} and raw["counts"]["skip"] == 1
    again = T.Taste(path)
    assert again.multiplier("fractal", "-", t0 + 40) == pytest.approx(t.multiplier("fractal", "-", t0 + 40))
    assert again.counts == t.counts and len(again.recent) == 3
    path.write_text("{ not json")
    assert T.Taste(path).multiplier("fractal", "-") == 1.0                         # unreadable: starts empty


async def _settle(s, n=30):
    """Let the cue tasks run, then finish the scene transitions the fake engine started (else the third waits)."""
    for _ in range(n):
        await asyncio.sleep(0.01)
    for tid in list(s.inflight):
        s.on_transition("scene", "started", 0.0, tid, False)
        s.on_transition("scene", "done", 0.1, tid, False)


async def test_skip_like_and_keep_from_the_room(client):  # noqa: F811 (the fixture)
    sid, s, eng = await live(client, auto=AUTO)
    mgr = s.mgr
    assert mgr.taste is not None and mgr.taste.path.parent == T.STATE_DIR           # the tests' own state dir
    s.director.arm(True)
    # a person's Next on a scene the director put up a moment ago: a skip of its plug-in
    r = await cue(client, sid, to="auto")
    assert r.status == 202 and s.scene_cause == "auto"
    first = s._shown
    r = await cue(client, sid, to="auto", source="panel")
    assert r.status == 202
    assert mgr.taste.counts["skip"] == 1 and mgr.taste.recent[-1]["plugin"] == first["plugin"]
    assert mgr.taste_weight(first["plugin"], s.direction_effective) < 1.0
    # a mode's own Next is not a person's
    await cue(client, sid, to="auto", source="mode")
    assert mgr.taste.counts["skip"] == 1
    # a scene on screen past SKIP_S has had its turn
    s._shown["t"] -= T.SKIP_S + 1
    await cue(client, sid, to="auto", source="panel")
    assert mgr.taste.counts["skip"] == 1
    # a scene cued by hand: a like for its plug-in (not a skip of the scene it replaces: that one was not picked)
    r = await cue(client, sid, to="warp-cells", source="panel")
    assert r.status == 202 and mgr.taste.counts["like"] == 1
    assert mgr.taste.recent[-1]["scene"] == "warp-cells"
    plugin = mgr.presets.scenes["warp-cells"]["source"]
    assert mgr.taste_weight(plugin, s.direction_effective) > 1.0
    await cue(client, sid, to="auto", source="panel")                              # Next on a hand-picked scene
    assert mgr.taste.counts["skip"] == 1
    # Keep this look on a picked scene, KEEP_S in all: a keep, once per showing
    now = time.monotonic()
    mgr.room_direction = {"direction": sessions_mod.validate_direction({}), "vibe": None, "section": None,
                          "groove": None, "beat_lock": None, "hold": True, "seq": 1, "t": now, "ensemble": None}
    sh = s._shown
    assert sh["cause"] == "auto"
    sh["t"] = now - 1
    s._taste_tick(now)
    for i in range(1, 130):                                         # 1 s ticks: 129 s held
        mgr.room_direction["t"] = now + i
        s._taste_tick(now + i)
    assert mgr.taste.counts["keep"] == 1 and sh["kept"]
    s._taste_tick(now + 200)
    assert mgr.taste.counts["keep"] == 1
    st = await (await client.get(f"/api/sessions/{sid}")).json()
    assert st["auto"]["taste"]["counts"] == {"skip": 1, "like": 1, "keep": 1}
    v = await (await client.get("/dev/taste")).json()
    assert v["counts"]["keep"] == 1 and v["recent"][0]["signal"] == "keep"


def _variant_family(scenes) -> tuple[str, str]:
    """A plug-in with at least three auto presets (so a variant can follow a variant) and one of its scenes."""
    by: dict = {}
    for n, p in scenes.items():
        if (p.get("tags") or {}).get("auto", True) is not False and p.get("source"):
            by.setdefault(p["source"], []).append(n)
    src = next(s for s, ns in sorted(by.items()) if len(ns) >= 3)
    return src, sorted(by[src])[0]


async def test_variant_chain_caps_songs_on_one_plugin(client):  # noqa: F811 (the fixture)
    sid, s, eng = await live(client, auto=AUTO)
    s.director.arm(True)
    mgr = s.mgr
    src, scene = _variant_family(mgr.presets.scenes)
    s.room_ok = lambda kind, plugin, *a, **k: True                   # the hermetic pool: every plug-in may play
    r = await cue(client, sid, to=scene)                              # by hand: the chain starts here
    await _settle(s, 1)
    assert r.status == 202 and s._variant_chain == {"plugin": src, "songs": 1, "track": mgr.track_n}
    small = {"style": "cut", "seconds": 0.0, "on": "now", "change": "small"}
    mgr.on_room_message({"type": "room.event", "data": {"kind": "track_change", "id": "track_change:41",
                                                        "at_ms": None, "track_id": 41, "transition": small}})
    await _settle(s)
    assert mgr.presets.scenes[s.current_name]["source"] == src and "variant" in s.last_cue["detail"]
    assert s._variant_chain["songs"] == 2
    mgr.on_room_message({"type": "room.event", "data": {"kind": "track_change", "id": "track_change:42",
                                                        "at_ms": None, "track_id": 42, "transition": small}})
    await _settle(s)
    assert "variant" not in s.last_cue["detail"] and f"2 songs on {src} already" in s.last_cue["detail"]
    ch = s._variant_chain
    assert ch["plugin"] != src or ch["songs"] == 3                    # a fresh pick (rarely the same plug-in again)
