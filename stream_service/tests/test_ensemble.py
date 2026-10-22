"""The room ensemble, the renderer's half (ensemble.py, rooms.py, sessions.py; docs/ENSEMBLE.md §8): this screen's part
(current vs next), staggered scene changes, roles, contrast, the register and the panel line.  Fake engine, no GPU."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import numpy as np
import pytest

from stream_service import ensemble, rooms
from stream_service.modulation import Modulation, SessionRuntime
from stream_service.server import MANAGER

from .test_fx_api import client  # noqa: F401 (fixture)
from .test_rooms import room_pair, scene_cmds, settle

NOW_MS = 1_000_000.0


def part(role="even", stagger_beats=0.0, stagger_s=0.0, **kw):
    scale = {"level": 1.0, "depth": 1.0, "motion": 1.0, "pulse": 1.0, "fx_density": 1.0, "gestures": 1.0,
             **kw.pop("scale", {})}
    return {"role": role, "scale": scale, "stagger_beats": stagger_beats, "stagger_s": stagger_s,
            "palette_role": None, "energy_bias": 0.0, "register": None, "clock_offset_beats": 0.0, "team": 0,
            "kind": "screen", "side": "left", "x": -1.0, "label": "A Frame", "screen": None, "members": [], **kw}


def ens(north=None, south=None, nxt=None, line="Playing together: a round, right follows left by 2 beats",
        params=None, glide_ms=1000.0):
    return {"id": 1, "rev": 1, "form": "a-round", "kind": "canon", "label": "A round", "line": line,
            "params": params or {}, "order": ["screen.north", "screen.south"],
            "players": {"screen.north": north or part(), "screen.south": south or part()},
            "screens": {"north-screen": "screen.north", "south-screen": "screen.south"},
            "since_ms": 0.0, "at_ms": 0.0, "glide_ms": glide_ms, "next": nxt, "source": "pick", "why": ""}


def nxt(at_ms, north=None, south=None):
    return {"at_ms": at_ms, "form": "spotlight", "kind": "solo_and_accompany", "label": "Spotlight", "line": None,
            "players": {"screen.north": north or part(), "screen.south": south or part()}, "why": ""}


# -- pure helpers ----------------------------------------------------------------------------------------------------
def test_my_part_is_next_from_its_at_on():
    e = ens(south=part(stagger_s=1.0), nxt=nxt(NOW_MS + 500.0, south=part("lead", stagger_s=2.0)))
    assert ensemble.my_part(e, "south-screen", NOW_MS)["role"] == "even"
    assert ensemble.my_part(e, "south-screen", NOW_MS + 500.0)["role"] == "lead"
    assert ensemble.my_part(e, "devtest", NOW_MS) is None and ensemble.my_part(None, "south-screen", NOW_MS) is None
    e2 = ens(nxt={**nxt(NOW_MS), "players": {}})                       # next doesn't name it: the current part
    assert ensemble.my_part(e2, "north-screen", NOW_MS + 1.0)["role"] == "even"


def test_stagger_s_beats_on_a_sure_clock_else_seconds():
    e = ens(south=part(stagger_beats=2.0, stagger_s=0.9), nxt=nxt(NOW_MS + 1000.0, south=part(stagger_beats=4.0)))
    assert ensemble.stagger_s(e, "south-screen", 0.5, NOW_MS) == pytest.approx(1.0)
    assert ensemble.stagger_s(e, "south-screen", None, NOW_MS) == pytest.approx(0.9)
    assert ensemble.stagger_s(e, "south-screen", 0.5, NOW_MS + 1000.0) == pytest.approx(2.0)   # next's
    assert ensemble.stagger_s(e, "north-screen", 0.5, NOW_MS) == 0.0
    assert ensemble.stagger_s(None, "south-screen", 0.5, NOW_MS) == 0.0
    assert rooms.stagger_s(e, "south-screen", 0.5, NOW_MS / 1000.0) == pytest.approx(1.0)       # monotonic s
    bad = ens(south=part(stagger_beats=-3.0, stagger_s=float("nan")))
    assert ensemble.stagger_s(bad, "south-screen", 0.5, NOW_MS) == 0.0 == ensemble.stagger_s(bad, "south-screen",
                                                                                               None, NOW_MS)


def test_fx_trim_then_fx_step():
    want = ["a", "b", "c"]
    assert ensemble.fx_trim(want, None) is want and ensemble.fx_trim(want, 1.0) is want
    assert ensemble.fx_trim(want, 0.5) == ["a", "b"] and ensemble.fx_trim(["a"], 0.5) == ["a"]
    assert ensemble.fx_trim(want, 0.0) == [] and ensemble.fx_trim([], 0.5) == []
    have = [("s1", "a"), ("s2", "b"), ("s3", "c")]
    assert rooms.fx_step(have, ensemble.fx_trim(want, 0.5)) == (None, "s3")     # the mirror drops the third
    assert rooms.fx_step(have, want) == (None, None)


def test_fx_source_follows_the_lead_screen():
    def m(screen, own=()):
        return SimpleNamespace(screen=screen, own=set(own))
    a, b, c = m("north-screen"), m("south-screen"), m("devtest")
    ms = [a, b, c]
    assert rooms.fx_source(a, a, ms) is None and rooms.fx_source(b, a, ms) is a           # today: the leader's
    assert rooms.fx_source(a, a, ms, ["south-screen"]) is b and rooms.fx_source(b, a, ms, ["south-screen"]) is None
    assert rooms.fx_source(c, a, ms, ["south-screen"]) is b
    b.own = {"fx"}                                                                     # it set its own: not the source
    assert rooms.fx_source(c, a, ms, ["south-screen"]) is a
    a.own = {"fx"}
    assert rooms.fx_source(c, a, ms, ["south-screen"]) is None                         # (c is the first head)


def test_levers_wire_and_neutral():
    assert ensemble.levers(part()) is ensemble.NEUTRAL and ensemble.levers(None) is ensemble.NEUTRAL
    acc = part("accompany", scale={"depth": 0.6, "motion": 0.5, "pulse": 0.6, "fx_density": 0.5},
               palette_role="highlight", energy_bias=0.075, register=[0.0, 0.5])
    lv = ensemble.levers(acc, {"outside": 0.3})
    assert lv["depth"] == 0.6 and lv["motion"] == 0.5 and lv["pulse"] == 0.6 and lv["fx_density"] == 0.5
    assert lv["energy_bias"] == 0.075 and lv["pal_shift"] == pytest.approx(2 / 3) and lv["register"] == (0.0, 0.5, 0.3)
    assert ensemble.levers(part(register=[0.5, 1.0]))["register"] == (0.5, 1.0, ensemble.OUTSIDE_DEFAULT)
    assert ensemble.wire(ens(), "north-screen") is None                                 # unison: nothing to carry
    assert ensemble.wire(ens(south=acc), "north-screen") is None
    w = ensemble.wire(ens(south=acc), "south-screen")
    assert w["cur"]["depth"] == 0.6 and w["next"] is None and w["glide_ms"] == 1000.0


def test_lever_glide_from_next_at_and_across_the_fold():
    acc = part("accompany", scale={"depth": 0.6, "motion": 0.5})
    g = ensemble.LeverGlide()
    w0 = ensemble.wire(ens(nxt=nxt(NOW_MS + 1000.0, south=acc)), "south-screen")
    assert g.at(w0, NOW_MS) is None                                                   # neutral before next.at
    assert (g.at(w0, NOW_MS + 1000.0) or ensemble.NEUTRAL)["depth"] == 1.0            # the glide starts at next.at
    assert g.at(w0, NOW_MS + 1500.0)["depth"] == pytest.approx(0.8)                   # halfway (smoothstep 0.5)
    w1 = ensemble.wire(ens(south=acc), "south-screen")                                # show folds next in
    assert g.at(w1, NOW_MS + 1500.0)["depth"] == pytest.approx(0.8)                   # no jump
    assert g.at(w1, NOW_MS + 2000.0)["depth"] == pytest.approx(0.6)
    assert g.at(None, NOW_MS + 3000.0)["depth"] == pytest.approx(0.6)                 # the ensemble gone: back,
    assert g.at(None, NOW_MS + 3500.0)["depth"] == pytest.approx(0.8)                 # over the last glide
    assert g.at(None, NOW_MS + 4000.0) is None                                        # rested at neutral


def test_palette_shift_goes_the_short_way_round():
    g = ensemble.LeverGlide()
    hi = ensemble.wire(ens(south=part(palette_role="highlight")), "south-screen")
    g.at(hi, NOW_MS)
    assert g.at(hi, NOW_MS + 1000.0)["pal_shift"] == pytest.approx(2 / 3)
    acc = ensemble.wire(ens(south=part(palette_role="base", scale={"depth": 0.9})), "south-screen")
    g.at(acc, NOW_MS + 2000.0)
    mid = g.at(acc, NOW_MS + 2500.0)["pal_shift"]
    assert mid == pytest.approx(5 / 6)                                               # 2/3 -> 1 (= 0), not via 1/3


def test_signals_direction_and_note_weights():
    sig = {"beat": 0.8, "energy": 0.95, "energy_slow": None}
    lv = {**ensemble.NEUTRAL, "pulse": 0.5, "energy_bias": 0.1}
    ensemble.apply_signals(sig, lv)
    assert sig == {"beat": 0.4, "energy": 1.0, "energy_slow": None}
    eff = {"energy": 0.5, "fx": {"density": 0.8, "rate": 0.5}}
    assert ensemble.direction(eff, None) is eff and ensemble.direction(eff, ensemble.NEUTRAL) is eff
    d = ensemble.direction(eff, {**ensemble.NEUTRAL, "fx_density": 0.5, "energy_bias": -0.075})
    assert d["energy"] == 0.425 and d["fx"]["density"] == 0.4 and eff["fx"]["density"] == 0.8
    assert ensemble.note_weights(None, np.arange(128)) is None
    w = ensemble.note_weights((0.0, 0.5, 0.25), np.array([21, 60, 64, 65, 108]))
    assert list(w) == [1.0, 1.0, 1.0, 0.25, 0.25]                                     # 64.5 = the middle of A0..C8


def test_segment_orders_the_screens_by_x():
    e = ens(north=part(x=0.71), south=part(x=-0.71))                       # south on the left, north on the right
    assert ensemble.segment(e, "south-screen") == (0, 2) and ensemble.segment(e, "north-screen") == (1, 2)
    assert ensemble.segment(e, "devtest") is None and ensemble.segment(None, "north-screen") is None
    assert ensemble.segment({**e, "screens": {"north-screen": "screen.north"}}, "north-screen") is None   # alone
    tie = ens()                                                             # both at x -1: show's (x, id) order
    assert ensemble.segment(tie, "north-screen") == (0, 2) and ensemble.segment(tie, "south-screen") == (1, 2)
    assert ensemble.segment(ens(north=part(x=None)), "south-screen") is None   # no x: never placed, so it is alone
    assert ensemble.clean_segment((1, 2)) == (1, 2) and ensemble.clean_segment([0, 3]) == (0, 3)
    for bad in (None, (2, 2), (-1, 2), (0, 1), (True, 2), (0.5, 2), "ab", (0, 2, 3)):
        assert ensemble.clean_segment(bad) is None
    rt = SessionRuntime(None)
    rt.extra = {"segment": (1, 2)}
    rt.begin_frame(0.0)
    assert rt.segment == (1, 2)
    rt.extra = {"segment": "bad"}
    rt.begin_frame(1.0)
    assert rt.segment is None


def test_hud_line():
    e = ens(south=part("lead"))
    assert ensemble.hud_line(e, None, NOW_MS) == "Together: a round, right follows left by 2 beats · lead south-screen"
    assert ensemble.hud_line(e, "Playing together: echo, left to right", NOW_MS).startswith("Together: echo, left")
    assert ensemble.hud_line(ens(line=None), None, NOW_MS) is None and ensemble.hud_line(None) is None


def test_runtime_levers_scale_depth_clock_beat_and_palette():
    rt = SessionRuntime(None)
    m = Modulation(rt, {}, {})
    rt.palette = "ember"
    rt.begin_frame(0.0)
    assert rt.lever is None and m.scale_dt(0.1) == pytest.approx(0.1)               # unison: today's numbers
    base = np.array(m.palette_rows())
    acc = part("accompany", scale={"depth": 0.6, "motion": 0.5, "pulse": 0.6}, palette_role="accent")
    rt.extra = {"ensemble": ensemble.wire(ens(north=acc, glide_ms=0.0), "north-screen")}
    rt.begin_frame(1.0)
    assert rt.lever["motion"] == 0.5 and m.scale_dt(0.1) == pytest.approx(0.05)
    rows = np.array(m.palette_rows())
    assert np.allclose(rows[:3], base[:3]) and np.allclose(rows[3], base[3] + base[2] / 3.0)
    rt.extra = {"ensemble": {"cur": {"depth": "bad"}}}                                # never breaks a frame
    rt.begin_frame(2.0)
    rt.extra = {}
    rt.begin_frame(3.0)
    assert rt.lever is None


# -- sessions: staggered cuts, unison is today's path -----------------------------------------------------------------
def say(mgr, e):
    mgr.on_room_message({"type": "room.direction", "data": {"ensemble": e}})


async def auto_cut(a, b, reason="max_dwell"):
    t0 = time.monotonic()
    await a.cue({"to": "auto"}, cause="auto", reason=reason, detail="test")
    await settle(a, b)
    return t0


async def test_unison_is_todays_path(client):
    (sa, a, ea), (sb, b, eb) = await room_pair(client)
    await auto_cut(a, b)
    assert scene_cmds(ea)[-1].at_mono is None and scene_cmds(eb)[-1].at_mono is None
    say(client.app[MANAGER], ens())                                                  # every stagger 0
    await auto_cut(a, b)
    assert scene_cmds(ea)[-1].at_mono is None and scene_cmds(eb)[-1].at_mono is None
    assert b.current_name == a.current_name


async def test_followers_cut_at_their_own_stagger(client):
    (sa, a, ea), (sb, b, eb) = await room_pair(client)
    mgr = client.app[MANAGER]
    say(mgr, ens(north=part(stagger_s=0.5), south=part(stagger_s=1.5)))
    t0 = await auto_cut(a, b)
    ta, tb = scene_cmds(ea)[-1].at_mono, scene_cmds(eb)[-1].at_mono
    assert ta is not None and tb is not None
    assert 0.3 < ta - t0 < 0.9 and 1.3 < tb - t0 < 1.9                                # no grid: its stagger_s
    assert b.current_name == a.current_name
    for reason in ("track", "next", "plugin_error"):                                 # these cut together
        await auto_cut(a, b, reason)
        assert scene_cmds(ea)[-1].at_mono is None and scene_cmds(eb)[-1].at_mono is None, reason
    await a.cue({"to": "auto"}, cause="auto", reason="room_event", detail="test", stagger=False)   # a sweep's
    await settle(a, b)
    assert scene_cmds(eb)[-1].at_mono is None


async def test_segment_reaches_the_runtime_without_a_part(client):
    (sa, a, ea), (sb, b, eb) = await room_pair(client)
    say(client.app[MANAGER], ens(north=part(x=0.71), south=part(x=-0.71)))      # unison: no levers
    now = asyncio.get_running_loop().time()
    a.tick(now), b.tick(now)
    assert a.runtime.extra["segment"] == (1, 2) and b.runtime.extra["segment"] == (0, 2)
    assert "ensemble" not in a.runtime.extra and "ensemble" not in b.runtime.extra


async def test_lead_screen_is_the_fx_source_and_levers_reach_the_runtime(client):
    (sa, a, ea), (sb, b, eb) = await room_pair(client)
    mgr = client.app[MANAGER]
    assert b.fx_source() is a and a.fx_source() is None
    acc = part("accompany", scale={"depth": 0.6, "fx_density": 0.5}, energy_bias=-0.075)
    say(mgr, ens(north=acc, south=part("lead")))
    assert a.fx_source() is b and b.fx_source() is None
    now = asyncio.get_running_loop().time()
    a.tick(now), b.tick(now + 2.0)
    assert a.runtime.extra["ensemble"]["cur"]["depth"] == 0.6 and "ensemble" not in b.runtime.extra
    a.tick(time.monotonic() + 5.0)                                                   # past the glide
    assert a._fx_mirror(["x", "y", "z"]) == ["x", "y"] and b._fx_mirror(["x"]) == ["x"]
    fd = a.fx_director
    assert fd.direction["fx"]["density"] == pytest.approx(round(a.direction_effective["fx"]["density"] * 0.5, 3))
    assert a.director.direction.get("energy") == a.direction_effective.get("energy")   # the room's picks: unbiased
    assert fd.direction["energy"] == pytest.approx(max(0.0, a.direction_effective["energy"] - 0.075), abs=1e-3)


def test_segment_counts_only_live_screens():
    """7c, 22 Oct: with only south live, south must not get (0, 2) and leave the tape's new notes on the dark north."""
    ens = {"screens": {"south-screen": "screen.south", "north-screen": "screen.north"},
           "players": {"screen.south": {"x": 0.2}, "screen.north": {"x": 0.8}}}
    assert ensemble.segment(ens, "south-screen") == (0, 2)
    assert ensemble.segment(ens, "south-screen", {"south-screen"}) is None
    assert ensemble.segment(ens, "north-screen", {"south-screen", "north-screen"}) == (1, 2)


def test_live_screens_hold_a_brief_gap():
    from types import SimpleNamespace

    from stream_service import sessions as S
    mgr = S.SessionManager.__new__(S.SessionManager)
    mgr._screen_seen = {}
    a = SimpleNamespace(screen="south-screen", state="live", exited=False)
    b = SimpleNamespace(screen="north-screen", state="live", exited=False)
    mgr.sessions = {"a": a, "b": b}
    assert mgr.live_screens(100.0) == {"south-screen", "north-screen"}
    b.state = "ended"                                       # north drops out (a fallback)
    assert mgr.live_screens(100.0 + S.SEGMENT_HOLD_S - 1) == {"south-screen", "north-screen"}
    assert mgr.live_screens(100.0 + S.SEGMENT_HOLD_S + 1) == {"south-screen"}
