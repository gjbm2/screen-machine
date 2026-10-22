"""One picture by design (docs/ENSEMBLE.md §8.7, §8.9; rooms.py, layers.py, degrade.py): a room's screens share the
scene clock (the room's moment for the cut), the seeds and the degrade ladder; canon's clock offset runs a screen a
round behind.  Fake engine and numpy frames, no GPU."""
from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np
import pytest

from stream_service import ensemble, rooms
from stream_service.degrade import Controller, Ctx
from stream_service.layers import FrameCtx, SourceLayer
from stream_service.modulation import SessionRuntime
from stream_service.music_feed import MusicFeed
from stream_service.renderer import Engine, EngineListener, StartTransition
from stream_service.profiles import PROFILES
from stream_service.server import MANAGER

from .test_beat_ladder import clock, play, runtime
from .test_cue import AUTO, cue, live
from .test_degrade import Pool, Run
from .test_ensemble import auto_cut, ens, part, say
from .test_fx_api import client  # noqa: F401 (fixture)
from .test_rooms import room_pair, scene_cmds, settle

FPS = 60


# -- seeds --------------------------------------------------------------------------------------------------------------
def test_fix_seeds_draws_a_random_seed_once_and_leaves_set_ones():
    rnd = {"seed": {"type": "integer", "minimum": -1, "maximum": 2**31 - 1, "default": -1}, "x": {"type": "number"}}
    p = {"seed": -1, "x": 0.5}
    got = rooms.fix_seeds(rnd, p, 12345)
    assert got == {"seed": 12345, "x": 0.5} and p["seed"] == -1                 # a copy: the preset's stays
    assert rooms.fix_seeds(rnd, {"seed": 7}, 1) == {"seed": 7}                  # a set seed is the preset's
    fixed = {"seed": {"type": "integer", "minimum": 0, "maximum": 99, "default": 1}}
    q = {"seed": 3}
    assert rooms.fix_seeds(fixed, q, 5) is q and rooms.fix_seeds({}, q, 5) is q  # no "random": the same dict
    assert rooms.fix_seeds({"seed": {**rnd["seed"], "maximum": 9}}, {"seed": -1}, 23) == {"seed": 3}   # in range


async def test_a_room_transitions_random_seed_is_the_leaders_on_every_screen(client):
    (sa, a, ea), (sb, b, eb) = await room_pair(client)
    await cue(client, sa, to="auto", via="burn-2s")                             # burn: seed -1 = random
    await settle(a, b)
    ta, tb = scene_cmds(ea)[-1].transition, scene_cmds(eb)[-1].transition
    assert ta.params["seed"] >= 0 and tb.params["seed"] == ta.params["seed"]
    assert tb.frozen_spec.params["seed"] == ta.params["seed"]
    sc, c, ec = await live(client, screen="other")                               # not in a room: the plug-in's own
    t = await c._transition_ready(c.mgr.presets.resolve_transition("burn-2s"))
    assert t.params["seed"] == -1


# -- the room's scene clock (sessions) ------------------------------------------------------------------------------------
async def test_a_room_cut_runs_every_screens_scene_clock_from_the_rooms_moment(client):
    (sa, a, ea), (sb, b, eb) = await room_pair(client)
    a._update_lead = b._update_lead = lambda now: None                          # (the manager's ticker would)
    a.sampler.lead, b.sampler.lead = 0.12, 0.31                                 # different render -> photon leads
    await auto_cut(a, b)
    ca, cb = scene_cmds(ea)[-1], scene_cmds(eb)[-1]
    assert ca.clock is not None and cb.clock is not None
    assert ca.clock + 0.12 == pytest.approx(cb.clock + 0.31) == pytest.approx(a.scene_t0) == b.scene_t0
    say(client.app[MANAGER], ens(north=part(stagger_s=0.5), south=part(stagger_s=1.5)))
    await auto_cut(a, b)
    ca, cb = scene_cmds(ea)[-1], scene_cmds(eb)[-1]                             # staggered cuts, one clock: the
    assert ca.clock + 0.12 == pytest.approx(cb.clock + 0.31) == pytest.approx(b.scene_t0)   # room's moment,
    assert ca.at_mono - ca.clock == pytest.approx(0.5, abs=0.05)                # before any stagger
    assert cb.at_mono + 0.31 - b.scene_t0 == pytest.approx(1.5 + 0.31 - 0.12, abs=0.1)
    await cue(client, sb, to="warp-cells")                                      # a person's cue: its own screen,
    await settle(a, b)                                                          # stream time as before
    assert scene_cmds(eb)[-1].clock is None and b.scene_t0 is None


async def test_no_room_no_clock_and_a_joiner_takes_the_leaders(client):
    sc, c, ec = await live(client, screen="other", auto=AUTO)
    await c.cue({"to": "auto"}, cause="auto", reason="max_dwell", detail="test")
    await settle(c)
    assert scene_cmds(ec)[-1].clock is None and [x.clock for x in ec.cmds if isinstance(x, StartTransition)
                                                 and x.kind == "in"] == [None]
    (sa, a, ea), (sb, b, eb) = await room_pair(client)
    ins = [x for e in (ea, eb) for x in e.cmds if isinstance(x, StartTransition) and x.kind == "in"]
    assert ins[0].clock + a.sampler.lead == pytest.approx(ins[1].clock + b.sampler.lead)   # go: the leader's scene
    await auto_cut(a, b)
    b.current_name, b._room_synced = "something-else", None                    # behind: it catches up once
    b._follow_tick(time.monotonic(), a)
    await settle(a, b)
    assert scene_cmds(eb)[-1].clock + b.sampler.lead == pytest.approx(a.scene_t0)


# -- the room's scene clock (layers) ------------------------------------------------------------------------------------
class Src:
    """A scene that records the scene time and step it was rendered at."""
    name = "rec"

    def __init__(self, rt=None):
        self.seen: list = []
        self.modulation = None if rt is None else SimpleNamespace(scale_dt=lambda dt: dt * rt.speed, runtime=rt)

    def render(self, t, dt, out):
        self.seen.append((t, dt))


def rt(off=0.0, ok=True, speed=1.0):
    return SimpleNamespace(clock_off=off, clock_off_ok=ok, speed=speed)


def run(layer, t_first, lead, frames, *, phase=0.0, n0=0, jump_at=None, jump=0.0, each=None):
    """Render ``frames`` frames at FPS from render time t_first + phase (CLOCK_MONOTONIC s); a resync after frame
    ``jump_at`` moves the frames ``jump`` s later (their ids go on).  -> [(photon time, scene time)]."""
    fc = FrameCtx(n0, FPS, Pool())
    out = np.zeros((2, 2, 4), np.uint8)
    got = []
    for k in range(frames):
        fc.n = n0 + k
        fc.mono = t_first + phase + k / FPS + (jump if jump_at is not None and k > jump_at else 0.0)
        if each is not None:
            each(k)
        layer.render(fc, out)
        got.append((fc.mono + lead, layer.source.seen[-1][0]))
    return got


def test_the_same_scene_time_at_the_same_photon_time_on_every_screen():
    p0 = 1000.0                                                                  # the room's moment (photon)
    la, lb = 0.12, 0.31
    a, b = SourceLayer(Src(rt()), 0.0, p0 - la), SourceLayer(Src(rt()), 0.0, p0 - lb)
    ga = run(a, p0 - la, la, 120, phase=0.004)                                  # cuts at once, its own frame grid
    gb = run(b, p0 - lb + 0.2, lb, 120, phase=0.011, n0=777, jump_at=40, jump=0.3)   # 0.2 s later, then a resync
    for photon, t in ga + gb:
        assert t == pytest.approx(photon - p0, abs=1e-9)                        # one function of photon time
    assert gb[0][1] == pytest.approx(0.211, abs=1e-9)                           # it joined where the room was
    assert all(y[1] >= x[1] for x, y in zip(gb, gb[1:]))                       # never backwards


def test_without_a_room_clock_it_is_todays_stream_time():
    s = Src(rt(speed=0.5))
    lay = SourceLayer(s, 1.0)
    fc = FrameCtx(60, FPS, Pool())
    fc.mono = 5000.0                                                             # (ignored without t0_mono)
    out = np.zeros((2, 2, 4), np.uint8)
    for n in (60, 61, 63):
        fc.n = n
        lay.render(fc, out)
    assert [round(t, 9) for t, _ in s.seen] == [0.0, round(0.5 / FPS, 9), round(1.5 / FPS, 9)]
    assert s.seen[0][1] == 0.0


def test_canon_runs_the_scene_a_round_behind_and_holds_its_first_frame_till_then():
    p0, lead = 2000.0, 0.2
    r = rt(off=1.0)
    lay = SourceLayer(Src(r), 0.0, p0 - lead)
    got = run(lay, p0 - lead + 0.5, lead, 120)                                  # cut 0.5 s after the room's moment
    for photon, t in got:
        assert t == pytest.approx(max(0.0, photon - p0 - 1.0), abs=1e-9)        # held at 0 until a round later


def test_an_offset_glides_forward_and_a_steep_rise_waits_for_the_next_cut():
    p0, lead = 3000.0, 0.0
    r = rt(off=1.0)
    lay = SourceLayer(Src(r), 0.0, p0)

    def shrink(k):                                                               # 1 s -> 0 over 1 s: the clock hurries
        r.clock_off = max(0.0, 1.0 - max(0, k - 60) / FPS)
    got = run(lay, p0 + 1.0, lead, 200, each=shrink)
    assert got[-1][1] == pytest.approx(got[-1][0] - p0, abs=1e-9) and lay.off == 0.0
    assert all(y[1] >= x[1] for x, y in zip(got, got[1:]))
    r2 = rt(off=0.0)
    lay2 = SourceLayer(Src(r2), 0.0, p0)

    def rise(k):                                                                 # 0 -> 1 s at once: a jump back
        r2.clock_off = 1.0 if k >= 30 else 0.0
    got2 = run(lay2, p0, lead, 90, each=rise)
    assert lay2.off == 0.0 and lay2.off_held                                     # kept until the next cut
    assert all(y[1] >= x[1] for x, y in zip(got2, got2[1:]))
    assert SourceLayer(Src(r2), 0.0, p0)._offset(p0, r2) == 1.0                 # (the next cut's layer takes it)
    r3 = rt(off=0.0, ok=False)                                                   # a glide the runtime says can't be
    lay3 = SourceLayer(Src(r3), 0.0, p0)                                         # followed: held too

    def slow(k):
        r3.clock_off = min(1.0, k * 0.001)
    run(lay3, p0, lead, 30, each=slow)
    assert lay3.off == 0.0 and lay3.off_held


# -- canon's offset: the levers, the runtime's beat signals ---------------------------------------------------------------
def test_clock_levers_glide_and_say_when_a_scene_clock_can_follow():
    canon = part(stagger_beats=2.0, stagger_s=1.1, clock_offset_beats=2.0)
    lv = ensemble.levers(canon)
    assert lv["clock_beats"] == 2.0 and lv["clock_s"] == pytest.approx(1.1)
    assert ensemble.clock_offset_s(lv, 0.5) == 1.0 and ensemble.clock_offset_s(lv, None) == pytest.approx(1.1)
    assert ensemble.clock_offset_s(None, 0.5) == 0.0 and ensemble.levers(part()) is ensemble.NEUTRAL
    assert ensemble.levers(part(clock_offset_beats=2.0))["clock_s"] == 0.0     # no stagger to say its seconds
    assert ensemble.clean({"depth": 1.0})["clock_beats"] == 0.0                 # an older control: unison
    g = ensemble.LeverGlide()
    w = ensemble.wire(ens(south=canon, glide_ms=1000.0), "south-screen", 0.5)
    assert w["beat_s"] == 0.5
    g.at(w, 0.0)
    assert not ensemble.clock_glide_ok(g, 0.5)                                  # +1 s over 1 s: too steep
    assert g.at(w, 500.0)["clock_beats"] == pytest.approx(1.0)                 # (the signals glide all the same)
    g2 = ensemble.LeverGlide()
    g2.at(ensemble.wire(ens(south=canon, glide_ms=4000.0), "south-screen"), 0.0)
    assert ensemble.clock_glide_ok(g2, 0.5)                                     # over 4 s: the scene slows, follows
    g2.at(None, 10_000.0)
    assert ensemble.clock_glide_ok(g2, 0.5)                                     # back to unison: it hurries


def test_the_runtime_reads_beat_signals_a_round_behind():
    f = MusicFeed()
    t0 = 100.0
    sure = lambda th: clock(t0, gate="beat", state="locked", conf=0.9)          # noqa: E731 (120 bpm)
    t = play(f, t0, 3.0, clk=sure)
    r0, r1 = runtime(f), runtime(f)
    canon = part(stagger_beats=2.0, stagger_s=1.0, clock_offset_beats=2.0)
    r1.extra = {"ensemble": ensemble.wire(ens(north=canon, glide_ms=0.0), "north-screen", 0.5)}
    for r in (r0, r1):
        r.begin_frame(t - 0.1)                                                  # (n/a phases fade in first)
        r.begin_frame(t)
    assert r0.clock_off == 0.0 and r1.clock_off == pytest.approx(1.0)
    s0, s1 = r0.signals, r1.signals
    assert s1["beat_phase"] == pytest.approx(s0["beat_phase"])                  # whole beats: the same phase
    assert s1["bar_phase"] == pytest.approx((s0["bar_phase"] - 0.5) % 1.0)     # half a bar (2 beats) behind
    r2 = runtime(f)
    r2.extra = {"ensemble": ensemble.wire(ens(north=canon, glide_ms=0.0), "north-screen", None)}
    r2.begin_frame(t)
    assert r2.clock_off == pytest.approx(1.0) and not r2.clock_off_ok          # no sure beat: its stagger_s; a jump


# -- one degrade decision per room ----------------------------------------------------------------------------------------
def test_the_room_floor_adds_steps_holds_them_and_never_shows_outside_a_room():
    r = Run()
    plain = r.c.state()
    assert "own" not in plain and "room" not in plain and r.c.effective() == []
    assert r.c.set_room(("fps",), "in step with south-screen (GPU busy)", r.t)
    st = r.c.state()
    assert st["steps"] == ["fps"] and st["own"] == [] and st["room"] == ["fps"] and st["out_fps"] == 30
    assert st["text"] == "60 -> 30 fps: in step with south-screen (GPU busy)" and r.out_fps == 30
    assert not r.c.set_room(["fps"], "in step with south-screen (GPU busy)", r.t)   # unchanged: nothing to do
    for _ in range(3):
        r.tick(10.0)                                                            # quiet at 30 fps
    ch = r.tick(80.0) and r.tick(80.0)                                          # overloaded: its own next step
    assert r.c.steps == ["scene_rate"] and r.c.effective() == ["scene_rate", "fps"]
    assert ch is None or ch.step == "scene_rate"
    for _ in range(40):                                                          # its own comes off when it may ...
        r.tick(1.0)
    assert r.c.steps == [] and r.c.effective() == ["fps"]                       # ... never below the room's floor
    assert r.c.set_room((), None, r.t) and r.c.effective() == [] and "room" not in r.c.state()


def test_a_screen_never_drops_alone_the_floor_follows_its_peers_own_steps():
    a = SimpleNamespace(screen="north-screen", degrade={"steps": ["fps"], "why": "GPU busy"})
    b = SimpleNamespace(screen="south-screen", degrade={"steps": ["fps"], "own": [], "room": ["fps"]})
    assert rooms.degrade_floor([a]) == (("fps",), "in step with north-screen (GPU busy)")
    assert rooms.degrade_floor([b]) == ((), None)                                # a floor is never passed back on
    c = SimpleNamespace(screen="devtest", degrade={"steps": ["scene_rate", "fps"], "own": ["scene_rate"],
                                                   "room": ["fps"], "why": "computer busy"})
    assert rooms.degrade_floor([a, c]) == (("scene_rate", "fps"), "in step with north-screen (GPU busy)")
    assert rooms.degrade_floor([]) == ((), None) and rooms.degrade_floor([SimpleNamespace(screen="x",
                                                                                         degrade=None)]) == ((), None)


async def test_sessions_hand_the_rooms_floor_to_their_engines(client):
    (sa, a, ea), (sb, b, eb) = await room_pair(client)
    got: dict = {"a": [], "b": []}
    ea.set_room_degrade = lambda s, w: got["a"].append((s, w))
    eb.set_room_degrade = lambda s, w: got["b"].append((s, w))
    a.tick(time.monotonic()), b.tick(time.monotonic())
    assert got == {"a": [], "b": []}                                             # nobody degraded: nothing sent
    a.on_degrade({"level": 1, "steps": ["fps"], "why": "GPU busy", "out_fps": 15})
    a.tick(time.monotonic()), b.tick(time.monotonic())
    assert got["b"] == [(["fps"], "in step with north-screen (GPU busy)")] and got["a"] == []
    b.tick(time.monotonic())
    assert len(got["b"]) == 1                                                    # once per change
    a.on_degrade({"level": 0, "steps": [], "why": None, "out_fps": 30})
    b.tick(time.monotonic())
    assert got["b"][-1] == ([], None)
    sc, c, ec = await live(client, screen="other")                               # not in a room: never sent
    ec.set_room_degrade = lambda s, w: got.setdefault("c", []).append((s, w))
    c.tick(time.monotonic())
    assert "c" not in got


class Capture(EngineListener):
    def __init__(self):
        self.degrade: list = []

    def on_degrade(self, info):
        self.degrade.append(info)


def test_the_engine_applies_the_rooms_floor_at_a_boundary_once_open():
    lis = Capture()
    e = Engine(profile=PROFILES["hevc-2160p60"], gpu=0, post=lambda fn, *a: fn(*a), listener=lis, name="s-t")
    e.degrade = Controller(60)
    e.ctx = SimpleNamespace(fps=60)
    e._gpu_enc = object()                                                        # the GPU-encode path at 60 fps
    e._dg_next = 1e18                                                            # (no own decisions here)
    fc = FrameCtx(0, 60, Pool())
    e.set_room_degrade(["scene_rate", "fps", "bogus"], "in step with north-screen (GPU busy)")
    e._degrade_boundary(fc, 8)
    assert fc.rate_floor == 1 and e.step == 1 and not lis.degrade                # frozen (the entry): not yet
    e.degrade.frozen = False
    e._degrade_boundary(fc, 9)
    assert fc.rate_floor == 2 and e._step_want == 2 and e.step == 1             # the fps step waits for an even id
    assert lis.degrade[-1]["steps"] == ["scene_rate", "fps"] and lis.degrade[-1]["out_fps"] == 30
    e._degrade_boundary(fc, 10)
    assert e.step == 2 and fc.step == 2 and len(lis.degrade) == 1
    e._fps_lost = "NVENC failed"                                                 # it can't take fps any more
    e._degrade_boundary(fc, 12)
    assert e.step == 1 and e.degrade.effective() == ["scene_rate"] and lis.degrade[-1]["out_fps"] == 60
    e.set_room_degrade([], None)
    e._degrade_boundary(fc, 13)
    assert fc.rate_floor == 1 and e.degrade.effective() == [] and lis.degrade[-1]["level"] == 0
