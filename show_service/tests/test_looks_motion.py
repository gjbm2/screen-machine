"""looks: movers, room space and flow (docs/LIGHTS.md §5; README S5.26). Deterministic: a fake clock body and chosen
send times, the real fixture_encode (fixtures.py's generic movers) and the real room geometry, built in memory
(space.build_view). Nothing is read from config/ or written anywhere.

A **simulated fixture** (``follow``) stands in for a real head: it follows the commanded pan/tilt ``position_ms``
later, never faster than its own top speed, so timing tests check where the beam physically is.
"""

from __future__ import annotations

import math

import pytest

from show_service import colour_mix, fixtures, space as sp
from show_service import looks as lk
from show_service.looks import LookEngine
from show_service.tests.test_looks import DMX_CAPS, HUE_CAPS, HUE_T, SnapClock, fx, look, out, rigview

MOVER = fixtures.BUILTIN_TYPES["generic-mover-full"]
RGB = fixtures.BUILTIN_TYPES["generic-rgb"]
FRAME = 1.0 / 43.0                     # an Art-Net frame on the grid (~23 ms)
FAST = {"pan": 5000.0, "tilt": 5000.0}  # a light this fast never shows the slew in a pattern's numbers


# -- helpers -----------------------------------------------------------------------------------------------------------
def lat(rise: float = 0.0, pos: float = 0.0, fall: float = 0.0) -> dict:
    return {"rise_ms": rise, "fall_ms": fall, "colour_ms": rise, "position_ms": pos, "min_pulse_ms": 40.0,
            "source": {}}


def mover(fid: str, addr: int = 1, setup: dict | None = None, latency: dict | None = None, output: str = "dmx",
          **kw) -> dict:
    return fx(fid, MOVER, output, address=addr, setup=setup or {}, latency=latency or lat(), **kw)


class Cat:
    """A light_scenes.Catalogue's shape: scenes and effects by name, a revision, poll and subscribe."""

    def __init__(self, scenes: dict | None = None, effects: dict | None = None) -> None:
        self.scenes, self.effects, self.revision, self.subs = scenes or {}, effects or {}, "r1", []

    def poll(self) -> bool:
        return False

    def subscribe(self, fn) -> None:
        self.subs.append(fn)


def scene(*layers, hits=None, **kw) -> dict:
    return {"label": "x", "layers": list(layers), "hits": hits, "caps": None, "tags": {}, **kw}


def engine(rig: dict, box: dict, scenes: dict | None = None, effects: dict | None = None, lights: dict | None = None,
           space: dict | None = None) -> LookEngine:
    box.setdefault("look", look())
    box.setdefault("now", 10.0)
    e = LookEngine(palette_fn=lambda t: box.get("pal", {"*": (1.0, 1.0, 1.0)}), look_fn=lambda: box["look"],
                   clock=box.setdefault("clk", SnapClock(anchor=10.0, period=0.5)), mono=lambda: box["now"],
                   light_scenes=Cat(scenes, effects), space_mod=sp if space is not None else False)
    box["probs"] = e.configure({"layers": []}, rig, lights=lights, space=space)
    return e


def run(e: LookEngine, box: dict, t0: float, t1: float, oid: str = "dmx", caps: dict = DMX_CAPS,
        step: float = FRAME) -> list[tuple[float, dict]]:
    got = []
    for i in range(int(round((t1 - t0) / step)) + 1):
        t = t0 + i * step
        box["now"] = t
        got.append((t, e.evaluate(oid, t, caps)))
    return got


def at(fr: dict, fid: str) -> dict:
    return fr["fixtures"][fid]["attrs"]


def follow(frames: list[tuple[float, dict]], fid: str, pos_s: float, vmax: float, axis: str = "pan",
           dt: float = 0.0005) -> list[tuple[float, float]]:
    """The simulated head: the command sent at t applies from t + pos_s; the head follows it at <= vmax."""
    cmds = [(t + pos_s, at(fr, fid)[axis]) for t, fr in frames]
    x = cmds[0][1]
    out_ = []
    i, cur = 0, cmds[0][1]
    t = cmds[0][0]
    while t <= cmds[-1][0]:
        while i + 1 < len(cmds) and cmds[i + 1][0] <= t:
            i += 1
            cur = cmds[i][1]
        x += max(-vmax * dt, min(vmax * dt, cur - x))
        out_.append((t, x))
        t += dt
    return out_


def room(points: dict | None = None, **appliances) -> dict:
    v = sp.build_view({"version": 1, "room": {"size": [6.0, 8.0, 3.0], "front": "north"},
                       "appliances": appliances, "points": points or {}})
    assert v["problems"] == [], v["problems"]
    return v


def pose(at_: list, rot: list, **kw) -> dict:
    return {"at": at_, "rot": rot, "tilt_offset_deg": kw.get("off", 0.0), "pan_sign": kw.get("ps", 1),
            "tilt_sign": kw.get("ts", 1), "rms_deg": 0.1, "worst_deg": 0.2, "samples": 4, "solved_at": "x"}


# -- capabilities (§5.2) -----------------------------------------------------------------------------------------------
def test_a_mover_scene_leaves_lamps_and_pars_exactly_as_the_colour_path():
    rig = rigview([mover("mv", 1), fx("par", RGB, "dmx", address=20), fx("hue", HUE_T, "dmx")],
                  [MOVER, RGB, HUE_T], [out("dmx", "artnet")])
    sc = scene({"id": "c", "kind": "position", "mode": "circle", "size_deg": 15},
               {"id": "b", "kind": "beam", "zoom": "narrow"}, {"id": "g", "kind": "gobo", "slot": 3})
    box = {"look": look(light_scene="m"), "pal": {"*": (1.0, 0.4, 0.1)}}
    e = engine(rig, box, {"m": sc})
    assert box["probs"] == []
    assert e._program.skipped == {"m/c": 2, "m/b": 2, "m/g": 2}            # the par and the lamp: skipped silently
    fr = e.evaluate("dmx", 10.1, DMX_CAPS)
    par = fr["fixtures"]["par"]
    assert "attrs" not in par and "attrs" not in fr["fixtures"]["hue"]
    assert par["dmx"] == colour_mix.encode(rig["fixtures"]["par"], RGB, par["intensity"], par["colour"])
    a = at(fr, "mv")
    assert a["zoom"] == 10.0 and a["gobo"] == 3 and a["shutter"] == {"mode": "open", "hz": None}
    assert len(fr["fixtures"]["mv"]["dmx"]) == MOVER["footprint"]


# -- position patterns (§5.4) ------------------------------------------------------------------------------------------
def test_an_uncalibrated_circle_runs_round_home_in_its_own_degrees():
    rig = rigview([mover("mv", setup={"home": {"pan": 10.0, "tilt": -20.0}, "max_speed": FAST})], [MOVER],
                  [out("dmx", "artnet")])
    sc = scene({"id": "c", "kind": "position", "mode": "circle", "size_deg": 15, "phase": "bar", "period_bars": 1,
                "rotate": "cw"})
    box = {"look": look(light_scene="m")}
    e = engine(rig, box, {"m": sc}, lights={"motion": {"accel_s": 0}})
    frames = run(e, box, 10.0, 12.0)
    for t, fr in frames[5:]:
        w = (t - 10.0) / 2.0                                              # one bar = 2 s at 120 bpm
        a = at(fr, "mv")
        assert a["pan"] == pytest.approx(10.0 + 15.0 * math.cos(2 * math.pi * w), abs=1e-6)
        assert a["tilt"] == pytest.approx(-20.0 - 15.0 * math.sin(2 * math.pi * w), abs=1e-6)   # cw: -sin


def test_a_calibrated_circle_lands_on_the_ceiling_round_its_centre():
    """A floor-standing mover off to one side draws a 1 m circle round the middle of the ceiling: every beam hit is
    within 2 cm of the circle."""
    p = pose([0.5, 0.5, 0.2], [0.0, 0.0, 0.0])                           # standing: the pan axis up
    view = room(points={"spot": {"label": "Spot", "at": [3.0, 5.0, 3.0]}},
                **{"mv": {"at": [0.5, 0.5, 0.2], "pose": p}})
    rig = rigview([mover("mv", setup={"max_speed": FAST})], [MOVER], [out("dmx", "artnet")])
    sc = scene({"id": "c", "kind": "position", "mode": "circle", "on": "ceiling", "centre": "spot", "size": 1.0,
                "phase": "bar", "period_bars": 2})
    box = {"look": look(light_scene="m")}
    e = engine(rig, box, {"m": sc}, space=view, lights={"motion": {"accel_s": 0}})
    pose_v = view["appliances"]["mv"]["pose"]
    frames = run(e, box, 10.0, 14.0)
    for _t, fr in frames[10:]:
        a = at(fr, "mv")
        hit, surf = sp.ray_hit(view, pose_v["at"], sp.beam_dir(pose_v, a["pan"], a["tilt"]))
        assert surf == "ceiling"
        assert math.hypot(hit[0] - 3.0, hit[1] - 5.0) == pytest.approx(1.0, abs=0.02)
    assert e.stats()["motion"]["mv"]["unreachable"] == []


def test_aim_takes_its_own_position_first_then_the_room_map_then_words():
    p = pose([3.0, 4.0, 2.9], [math.pi, 0.0, 0.0])                       # hung: the pan axis down
    view = room(points={"piano": {"label": "The piano", "at": [1.0, 7.0, 1.0]}}, mv={"at": [3, 4, 2.9], "pose": p})
    setup = {"max_speed": FAST, "positions": {"desk": {"pan": 33.0, "tilt": 44.0}}}
    rig = rigview([mover("mv", setup=setup), mover("mv2", 40, setup={"max_speed": FAST})], [MOVER],
                  [out("dmx", "artnet")])
    scenes = {n: scene({"id": "a", "kind": "position", "mode": "aim", "centre": c})
              for n, c in (("desk", ["desk", "piano"]), ("piano", ["nowhere", "piano"]), ("down", "down"),
                           ("lost", ["nowhere"]))}
    box = {"look": look(light_scene="desk")}
    e = engine(rig, box, scenes, space=view, lights={"motion": {"accel_s": 0}})
    a = at(run(e, box, 10.0, 10.2)[-1][1], "mv")
    assert (a["pan"], a["tilt"]) == (33.0, 44.0)                          # its own saved position, uncalibrated or not
    box["look"] = look(light_scene="piano")
    fr = run(e, box, 10.3, 10.5)[-1][1]
    d = sp.beam_dir(p, at(fr, "mv")["pan"], at(fr, "mv")["tilt"])
    want = [1.0 - 3.0, 7.0 - 4.0, 1.0 - 2.9]
    n = math.sqrt(sum(x * x for x in want))
    assert sum(a_ * b / n for a_, b in zip(d, want)) == pytest.approx(1.0, abs=1e-6)   # aimed at the piano
    assert at(fr, "mv2")["pan"] == 0.0                                    # uncalibrated mv2: no piano, stays home
    assert "piano/a" in e.stats()["motion"]["mv2"]["unresolved"]
    box["look"] = look(light_scene="down")
    a = at(run(e, box, 10.6, 10.8)[-1][1], "mv")
    assert sp.beam_dir(p, a["pan"], a["tilt"])[2] == pytest.approx(-1.0, abs=1e-6)     # "down": straight below
    box["look"] = look(light_scene="lost")
    run(e, box, 10.9, 11.0)
    assert e.stats()["motion"]["mv"]["unresolved"] == ["lost/a"]


def test_fan_and_mirror_spread_the_group():
    fs = [mover(f"mv{i}", 1 + 20 * i, setup={"max_speed": FAST}) for i in range(4)]
    rig = rigview(fs, [MOVER], [out("dmx", "artnet")])
    sc = scene({"id": "f", "kind": "position", "mode": "fan", "size_deg": 30, "fan": 1.0},
               {"id": "8", "kind": "position", "mode": "figure8", "size_deg": 10, "spread_mode": "mirror",
                "depth": 0.0})
    box = {"look": look(light_scene="m")}
    e = engine(rig, box, {"m": sc}, lights={"motion": {"accel_s": 0}})
    fr = run(e, box, 10.0, 10.3)[-1][1]
    assert [round(at(fr, f"mv{i}")["pan"], 6) for i in range(4)] == [-30.0, -10.0, 10.0, 30.0]


# -- the motion stage (§5.5) -------------------------------------------------------------------------------------------
def test_a_180_degree_jump_never_outruns_the_light():
    setup = {"max_speed": {"pan": 180.0, "tilt": 120.0},
             "positions": {"a": {"pan": -90.0, "tilt": -60.0}, "b": {"pan": 90.0, "tilt": 60.0}}}
    rig = rigview([mover("mv", setup=setup)], [MOVER], [out("dmx", "artnet")])
    scenes = {n: scene({"id": "a", "kind": "position", "mode": "aim", "centre": n}) for n in ("a", "b")}
    box = {"look": look(light_scene="a")}
    e = engine(rig, box, scenes)
    run(e, box, 10.0, 12.0)
    box["look"] = look(light_scene="b")
    frames = run(e, box, 12.0, 14.0)
    prev = None
    for t, fr in frames:
        a = at(fr, "mv")
        if prev is not None:
            assert abs(a["pan"] - prev[0]) <= 180.0 * FRAME + 1e-9
            assert abs(a["tilt"] - prev[1]) <= 120.0 * FRAME + 1e-9
        prev = (a["pan"], a["tilt"])
    assert prev == (90.0, 60.0)
    arrive = next(t for t, fr in frames if at(fr, "mv")["pan"] == 90.0)
    assert arrive - 12.0 == pytest.approx(lk.travel_s(180.0, 180.0, 180.0 / lk.DEFAULT_ACCEL_S), abs=2 * FRAME)


def test_soft_limits_clamp_and_say_so():
    setup = {"max_speed": FAST, "limits": {"pan": [-30.0, 30.0]}, "positions": {"far": {"pan": 80.0, "tilt": 0.0}}}
    rig = rigview([mover("mv", setup=setup)], [MOVER], [out("dmx", "artnet")])
    box = {"look": look(light_scene="far")}
    e = engine(rig, box, {"far": scene({"id": "a", "kind": "position", "mode": "aim", "centre": "far"})},
               lights={"motion": {"accel_s": 0}})
    assert at(run(e, box, 10.0, 10.3)[-1][1], "mv")["pan"] == 30.0
    assert e.stats()["motion"]["mv"]["clamped"] == ["limits"]


def _bounce(bpm: float, pos_ms: float = 60.0) -> tuple[LookEngine, dict]:
    setup = {"max_speed": {"pan": 180.0, "tilt": 180.0},
             "positions": {"a": {"pan": -20.0, "tilt": 0.0}, "b": {"pan": 20.0, "tilt": 0.0}}}
    rig = rigview([mover("mv", setup=setup, latency=lat(pos=pos_ms))], [MOVER], [out("dmx", "artnet")])
    sc = scene({"id": "hop", "kind": "position", "mode": "bounce", "points": ["a", "b"], "hop": "beat"})
    box = {"look": look(light_scene="m"), "clk": SnapClock(anchor=10.0, period=60.0 / bpm)}
    return engine(rig, box, {"m": sc}), box


def test_a_bounce_is_pre_rolled_so_the_beam_lands_on_the_beat():
    """40° hops at 120 bpm, 180°/s: the simulated head reaches each point within one frame of its beat."""
    e, box = _bounce(120.0)
    frames = run(e, box, 10.0, 16.0)
    head = follow(frames, "mv", 0.060, 180.0)
    for k in range(3, 11):                                               # beat k at 10 + k/2: point a (k even), b
        line = 10.0 + 0.5 * k
        want = -20.0 if k % 2 == 0 else 20.0
        arrive = next(t for t, x in head if t > line - 0.45 and abs(x - want) < 0.5)
        assert arrive == pytest.approx(line, abs=FRAME), k
    assert e.stats()["motion"]["mv"]["slowed"] == {}


def test_a_hop_too_fast_for_the_light_doubles_its_period():
    e, box = _bounce(240.0)                                              # 40° needs 0.34 s; 0.8 x 0.25 s won't do
    frames = run(e, box, 10.0, 14.0)
    assert e.stats()["motion"]["mv"]["slowed"] == {"m/hop": "hop period x2"}
    head = follow(frames, "mv", 0.060, 180.0)
    for k in range(4, 14, 2):                                            # every second beat now
        line = 10.0 + 0.25 * k
        want = -20.0 if (k // 2) % 2 == 0 else 20.0
        arrive = next(t for t, x in head if t > line - 0.45 and abs(x - want) < 0.5)
        assert arrive == pytest.approx(line, abs=FRAME), k


@pytest.mark.parametrize("need, doubled", [(400.0, False), (600.0, True)])
def test_a_pattern_too_fast_shrinks_then_slows_never_faster_than_the_light(need, doubled):
    size = need * 2.0 / (2.0 * math.pi)                                  # peak speed 2πR/T over one 2 s bar
    setup = {"max_speed": {"pan": 180.0, "tilt": 180.0}}
    rig = rigview([mover("mv", setup=setup)], [MOVER], [out("dmx", "artnet")])
    sc = scene({"id": "c", "kind": "position", "mode": "circle", "size_deg": size, "phase": "bar",
                "period_bars": 1})
    box = {"look": look(light_scene="m")}
    e = engine(rig, box, {"m": sc})
    frames = run(e, box, 10.0, 18.0)
    prev = None
    for _t, fr in frames:
        a = at(fr, "mv")
        if prev is not None:
            assert abs(a["pan"] - prev[0]) <= 180.0 * FRAME * 1.0 + 1e-9
            assert abs(a["tilt"] - prev[1]) <= 180.0 * FRAME * 1.0 + 1e-9
        prev = (a["pan"], a["tilt"])
    slowed = e.stats()["motion"]["mv"]["slowed"]["m/c"]
    share = float(slowed.split("x")[1].split(",")[0])
    assert share >= lk.DEFAULT_MIN_SIZE_SHARE
    assert slowed.endswith("period x2" if doubled else "period x1")
    span = max(at(fr, "mv")["pan"] for _t, fr in frames[200:]) - min(at(fr, "mv")["pan"] for _t, fr in frames[200:])
    assert span == pytest.approx(2.0 * size * share, rel=0.02)           # it still draws the (smaller) circle


# -- transitions and dark moves (§5.5, §5.6) ---------------------------------------------------------------------------
def _two_place_rig(**setup_kw):
    setup = {"max_speed": {"pan": 180.0, "tilt": 180.0},
             "positions": {"a": {"pan": -60.0, "tilt": 0.0}, "b": {"pan": 60.0, "tilt": 0.0}}, **setup_kw}
    rig = rigview([mover("mv", setup=setup)], [MOVER], [out("dmx", "artnet")])
    scenes = {n: scene({"id": "a", "kind": "position", "mode": "aim", "centre": n}) for n in ("a", "b")}
    return rig, scenes


def program(scene_name: str, kind: str, at_ms: float, **params) -> dict:
    return {"movers": {"group": "movers", "label": "Moving lights", "scene": scene_name,
                       "transition": {"name": kind, "kind": kind, "params": params, "at_ms": at_ms},
                       "effects": [], "at_ms": at_ms}}


LIGHTS = {"groups": {"movers": {"label": "Moving lights", "select": "kind:lamp", "dark_level": 0}}}


def test_a_big_jump_in_a_dark_move_glide_travels_dark_then_lights_after_it_settles():
    rig, scenes = _two_place_rig()
    box = {"look": look(light_programs=program("a", "crossfade", 10_000.0))}
    e = engine(rig, box, scenes, lights=LIGHTS)
    run(e, box, 10.0, 11.0)
    box["look"] = look(light_programs=program("b", "crossfade", 11_500.0, seconds=1.0, dark_move=True))
    frames = run(e, box, 11.02, 14.0)
    prev = None
    moved = []
    for t, fr in frames:
        a, i = at(fr, "mv"), fr["fixtures"]["mv"]["intensity"]
        if prev is not None and abs(a["pan"] - prev) > 0.5:
            assert i == 0.0, t                                            # never lit while it moves
            moved.append(t)
        prev = a["pan"]
    assert moved and min(moved) >= 11.5 - FRAME                           # it starts with the crossfade at at_ms
    arrive = moved[-1]
    lit = next(t for t, fr in frames if t > arrive and fr["fixtures"]["mv"]["intensity"] > 0.0)
    assert lit - arrive == pytest.approx(lk.DEFAULT_SETTLE_MS / 1000.0, abs=1.5 * FRAME)


def test_a_dark_move_cut_lights_at_its_line():
    rig, scenes = _two_place_rig()
    box = {"look": look(light_programs=program("a", "cut", 10_000.0))}
    e = engine(rig, box, scenes, lights=LIGHTS)
    run(e, box, 10.0, 11.0)
    box["look"] = look(light_programs=program("b", "dark_move", 12_500.0))
    frames = run(e, box, 11.02, 13.5)
    dark = [t for t, fr in frames if fr["fixtures"]["mv"]["intensity"] == 0.0]
    assert dark and min(dark) < 12.5 - 0.5                                # it went dark well before the line
    lit = next(t for t, fr in frames if t > min(dark) and fr["fixtures"]["mv"]["intensity"] > 0.0)
    assert lit == pytest.approx(12.5, abs=FRAME)
    assert at(frames[-1][1], "mv")["pan"] == 60.0


def test_a_cut_is_pre_rolled_to_arrive_on_its_line():
    rig, scenes = _two_place_rig()
    box = {"look": look(light_programs=program("a", "cut", 10_000.0))}
    e = engine(rig, box, scenes, lights=LIGHTS)
    run(e, box, 10.0, 11.0)
    box["look"] = look(light_programs=program("b", "cut", 12_000.0))
    frames = run(e, box, 11.02, 13.0)
    arrive = next(t for t, fr in frames if at(fr, "mv")["pan"] == 60.0)
    assert arrive == pytest.approx(12.0, abs=FRAME)
    assert all(fr["fixtures"]["mv"]["intensity"] > 0 for _t, fr in frames)   # a plain cut never goes dark


def test_fade_dark_goes_to_the_groups_dark_level_and_back_moving_while_dark():
    rig, scenes = _two_place_rig()
    box = {"look": look(light_programs=program("a", "cut", 10_000.0))}
    e = engine(rig, box, scenes, lights=LIGHTS)
    run(e, box, 10.0, 11.0)
    box["look"] = look(light_programs=program("b", "fade_dark", 12_000.0, out_s=1.0, in_s=1.0,
                                              in_at_ms=13_000.0))
    frames = dict(run(e, box, 11.0, 15.0, step=0.05))

    def i(t):
        return frames[min(frames, key=lambda k: abs(k - t))]["fixtures"]["mv"]["intensity"]

    def pan(t):
        return at(frames[min(frames, key=lambda k: abs(k - t))], "mv")["pan"]
    assert i(11.0) == pytest.approx(1.0) and i(11.5) == pytest.approx(0.5, abs=0.06)
    assert i(12.2) == 0.0 and i(12.9) == 0.0 and i(13.5) == pytest.approx(0.5, abs=0.06) and i(14.2) == 1.0
    assert pan(11.9) == -60.0 and pan(12.9) == 60.0                       # it moved while dark


def test_a_sweep_starts_each_lights_crossfade_at_its_place_along_the_flow():
    view = room(**{"w": {"at": [1.0, 4.0, 2.0]}, "e": {"at": [5.0, 4.0, 2.0]}})
    rig = rigview([fx("w", HUE_T, "dmx"), fx("e", HUE_T, "dmx")], [HUE_T], [out("dmx", "artnet")])
    scenes = {"on": scene(), "off": scene({"id": "d", "fx": "square", "phase": "beat", "depth": 1.0,
                                           "needs": "none", "attack_ms": 0, "decay_ms": 250})}
    flow = {"id": 1, "dir": "right", "at": None, "at_xyz": None, "speed": 0.5, "spread": 1.0, "symmetry": "none"}
    lights = {"groups": {"lamps": {"label": "Lamps", "select": "kind:lamp"}}}

    def prog(name, at_ms, **params):
        return {"lamps": {"group": "lamps", "scene": name, "flow": flow, "effects": [], "at_ms": at_ms,
                          "transition": {"name": "sweep", "kind": "sweep", "params": params, "at_ms": at_ms}}}
    box = {"look": look(light_programs=prog("on", 10_000.0))}
    e = engine(rig, box, scenes, lights=lights, space=view)
    e.evaluate("dmx", 10.0, DMX_CAPS)
    box["look"] = look(light_programs=prog("off", 11_000.0, beats=1, wave_beats=4))   # 4 beats = 2 s across
    pf = e._outs["dmx"].pfades["lamps"]
    fr = e.evaluate("dmx", 10.3, DMX_CAPS)                               # the square's low half: depth shows
    cw, ce = 1.0 / 6.0, 5.0 / 6.0                                         # flow_coord right of x = 1 and x = 5
    assert pf.x(11.0 + cw * 2.0 + 0.25, cw) == pytest.approx(0.5)
    assert pf.x(11.0 + ce * 2.0 + 0.25, ce) == pytest.approx(0.5)
    assert fr["fixtures"]["w"]["intensity"] == 1.0
    fr = e.evaluate("dmx", 12.3, DMX_CAPS)                               # west done (the low half), east not begun
    assert fr["fixtures"]["w"]["intensity"] == pytest.approx(0.0) and fr["fixtures"]["e"]["intensity"] == 1.0


# -- programs per group (§5.6) -----------------------------------------------------------------------------------------
def test_each_group_runs_its_own_program_and_crossfades_alone():
    rig = rigview([mover("mv", setup={"max_speed": FAST, "positions": {"a": {"pan": 50.0, "tilt": 0.0}}}),
                   fx("l1", HUE_T, "dmx"), fx("l2", HUE_T, "dmx")], [MOVER, HUE_T], [out("dmx", "artnet")])
    sq = {"id": "sq", "fx": "square", "phase": "beat", "depth": 1.0, "needs": "none", "attack_ms": 0,
          "decay_ms": 250}
    scenes = {"aim": scene({"id": "p", "kind": "position", "mode": "aim", "centre": "a"}), "pulse": scene(sq),
              "still": scene()}
    lights = {"groups": {"movers": {"label": "Movers", "select": f"type:{MOVER['id']}"},
                         "lamps": {"label": "Lamps", "select": "kind:lamp"}}}

    def progs(lamp_scene, kind="cut", at_ms=10_000.0, **params):
        return {"movers": {"group": "movers", "scene": "aim", "effects": [], "at_ms": 10_000.0,
                           "transition": {"name": "cut", "kind": "cut", "params": {}, "at_ms": 10_000.0}},
                "lamps": {"group": "lamps", "scene": lamp_scene, "effects": [], "at_ms": at_ms,
                          "transition": {"name": kind, "kind": kind, "params": params, "at_ms": at_ms}}}
    box = {"look": look(light_programs=progs("pulse"))}
    e = engine(rig, box, scenes, lights=lights)
    assert e.stats()["program"]["groups"] == {"movers": ["mv"], "lamps": ["l1", "l2"], "*": []}
    frames = dict(run(e, box, 10.0, 10.6, step=0.05))
    assert frames[10.3]["fixtures"]["l1"]["intensity"] == 0.0             # the lamps pulse...
    assert frames[10.3]["fixtures"]["mv"]["intensity"] == 1.0             # ...the mover doesn't, it aims
    assert at(frames[10.3], "mv")["pan"] == 50.0
    box["look"] = look(light_programs=progs("still", "crossfade", 11_000.0, seconds=2.0))
    assert e.evaluate("dmx", 10.8, DMX_CAPS)["fixtures"]["l1"]["intensity"] == 0.0   # before at_ms: still pulsing
    fr = e.evaluate("dmx", 12.3, DMX_CAPS)                               # 65% through, the square's low half
    assert fr["fixtures"]["l1"]["intensity"] == pytest.approx(0.65)
    assert at(fr, "mv")["pan"] == 50.0
    assert e.stats()["per_output"]["dmx"]["programs"] == {"movers": "aim", "lamps": "still"}
    box["look"] = look(light_scene="pulse")                              # no programs: the light_scene path, all
    fr = e.evaluate("dmx", 13.3, DMX_CAPS)
    assert fr["fixtures"]["l1"]["intensity"] == 0.0 and fr["fixtures"]["mv"]["intensity"] == 0.0


def test_effects_ride_over_the_scene_by_their_envelope():
    rig = rigview([mover("mv", setup={"max_speed": FAST, "positions": {"a": {"pan": 0.0, "tilt": 0.0},
                                                                       "b": {"pan": 40.0, "tilt": 0.0}}}),
                   fx("l1", HUE_T, "dmx")], [MOVER, HUE_T], [out("dmx", "artnet")])
    scenes = {"aim": scene({"id": "p", "kind": "position", "mode": "aim", "centre": "a"})}
    effects = {"there": {"label": "Over there", "on": ["drop"], "fade": {"in_beats": 2, "out_beats": 2},
                         "layers": [{"id": "p", "kind": "position", "mode": "aim", "centre": "b"},
                                    {"id": "dim", "fx": "square", "phase": "beat", "depth": 1.0, "needs": "none",
                                     "attack_ms": 0, "decay_ms": 250}]}}
    lights = {"groups": {"all": {"label": "All", "select": "kind:lamp"}}, "motion": {"accel_s": 0}}

    def progs(runs):
        return {"all": {"group": "all", "scene": "aim", "effects": runs, "at_ms": 10_000.0,
                        "transition": {"name": "cut", "kind": "cut", "params": {}, "at_ms": 10_000.0}}}
    run1 = {"id": "fx:1", "name": "there", "label": "Over there", "at_ms": 11_000.0, "until_ms": None,
            "strength": 1.0, "why": "the drop"}
    box = {"look": look(light_programs=progs([run1]))}
    e = engine(rig, box, scenes, effects, lights=lights)
    assert e.stats()["program"]["effects"] == {"all": ["there"]}
    f = dict(run(e, box, 10.0, 12.6, step=0.05))
    assert at(f[10.5], "mv")["pan"] == 0.0
    assert at(f[11.5], "mv")["pan"] == pytest.approx(20.0, abs=0.5)        # half way up its 2-beat fade in
    assert at(f[12.1], "mv")["pan"] == pytest.approx(40.0, abs=0.5)
    assert f[12.3]["fixtures"]["l1"]["intensity"] == 0.0                  # the square, at full envelope
    box["look"] = look(light_programs=progs([]))                         # it left the list: fades out over 2 beats
    f = dict(run(e, box, 12.6, 14.8, step=0.05))
    assert at(f[13.1], "mv")["pan"] == pytest.approx(20.0, abs=1.0)
    assert at(f[13.8], "mv")["pan"] == 0.0 and e._outs["dmx"].fx["all"] == {}   # and forgotten after


# -- flow order (§5.2) -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("symmetry", ["none", "mirror"])
def test_a_wave_travels_the_room_along_the_flow(symmetry):
    view = room(**{"w": {"at": [1.0, 4.0, 2.0]}, "e": {"at": [5.0, 4.0, 2.0]}})
    rig = rigview([fx("w", HUE_T, "dmx"), fx("e", HUE_T, "dmx")], [HUE_T], [out("dmx", "artnet")])
    sc = scene({"id": "wave", "fx": "saw", "phase": "bar", "period_bars": 1, "depth": 1.0, "needs": "none",
                "order": "flow", "spread": 1.0})
    flow = {"id": 3, "dir": "right", "at": None, "at_xyz": None, "speed": 0.5, "spread": 1.0, "symmetry": symmetry}
    box = {"look": look(light_scene="m", flow=flow)}
    e = engine(rig, box, {"m": sc}, space=view)
    for t in (10.1, 10.37, 11.2, 11.65):
        fr = e.evaluate("dmx", t, DMX_CAPS)
        w, east = fr["fixtures"]["w"]["intensity"], fr["fixtures"]["e"]["intensity"]
        if symmetry == "mirror":
            assert w == pytest.approx(east)
        else:                                                            # the west lamp leads by 4/6 of a 2 s bar
            assert w == pytest.approx(lk.shape("saw", (t - 10.0) / 2.0 - 1.0 / 6.0))
            assert east == pytest.approx(lk.shape("saw", (t - 10.0) / 2.0 - 5.0 / 6.0))


def test_without_positions_a_wave_follows_group_order_and_reverses_with_the_flow():
    rig = rigview([fx(f"l{i}", HUE_T, "dmx") for i in range(4)], [HUE_T], [out("dmx", "artnet")])
    sc = scene({"id": "wave", "fx": "saw", "phase": "bar", "period_bars": 1, "depth": 1.0, "needs": "none",
                "order": "flow", "spread": 1.0})
    for d, lags in (("right", [0, 0.25, 0.5, 0.75]), ("left", [0.75, 0.5, 0.25, 0]), ("still", [0, 0, 0, 0])):
        box = {"look": look(light_scene="m", flow={"id": 1, "dir": d, "speed": 0.5, "spread": 1.0})}
        e = engine(rig, box, {"m": sc})
        fr = e.evaluate("dmx", 10.3, DMX_CAPS)
        for i, lag in enumerate(lags):
            assert fr["fixtures"][f"l{i}"]["intensity"] == pytest.approx(lk.shape("saw", 0.15 - lag)), (d, i)


def test_flow_wave_is_the_shared_phase():
    clk = SnapClock(anchor=10.0, period=0.5).body
    flow = {"period_beats": 4.0, "anchor_beat": 2.0, "spread": 0.5, "period_s": 2.0, "at_ms": 11_000.0}
    assert lk.flow_wave(flow, clk, 11.5, 0.0) == pytest.approx(0.25)    # beat 3: one beat past the anchor of 4
    assert lk.flow_wave(flow, clk, 11.5, 0.5) == pytest.approx(0.0)
    assert lk.flow_wave(flow, None, 11.5, 0.0) == pytest.approx(0.25)   # no grid: period_s from at_ms
    assert lk.flow_wave(None, clk, 11.5, 0.0) == 0.0


# -- beam, gobo, prism, colour (§5.2) ----------------------------------------------------------------------------------
def test_beam_gobo_prism_and_colour_layers():
    rig = rigview([mover("mv", setup={"slots": {"gobo": {"4": "Stars"}}})], [MOVER], [out("dmx", "artnet")])
    sc = scene({"id": "b", "kind": "beam", "zoom": "wide", "focus": 0.2},
               {"id": "g", "kind": "gobo", "slot": "star", "spin": "flow", "spin_scale": 0.5},
               {"id": "p", "kind": "prism", "in": "accents"},
               {"id": "c", "kind": "colour", "role": "accent", "depth": 1.0},
               {"id": "w", "kind": "colour_wheel", "mode": "step", "step_on": "bar"},
               {"id": "z", "kind": "beam", "zoom": {"fx": "triangle", "phase": "bar", "between": [10, 40]}})
    roles = {"base": {"hex": "#ff0000"}, "accent": {"hex": "#0000ff"}, "highlight": {"hex": "#ffffff"}}
    box = {"look": look(light_scene="m", flow={"id": 1, "dir": "ccw", "speed": 0.8}, palette={"roles": roles})}
    e = engine(rig, box, {"m": sc})
    assert box["probs"] == [] and e._program.scene_problems == ()
    fr = e.evaluate("dmx", 10.0, DMX_CAPS)
    a = at(fr, "mv")
    assert a["gobo"] == 4 and a["gobo_spin"] == pytest.approx(-0.4)       # ccw at speed 0.8, x 0.5
    assert a["prism"] == 1 and a["focus"] == pytest.approx(0.2)           # accents style "beat": the prism is in
    assert a["zoom"] == pytest.approx(40.0)                               # the triangle's top at the bar line
    assert fr["fixtures"]["mv"]["colour"] == pytest.approx([0.0, 0.0, 1.0], abs=1e-6)
    assert a["colour_slot"] == 1                                          # bar 0
    assert at(e.evaluate("dmx", 12.0, DMX_CAPS), "mv")["colour_slot"] == 2   # the next bar: the next slot
    box["look"] = look(light_scene="m", accents={"style": "none"})
    assert at(e.evaluate("dmx", 12.1, DMX_CAPS), "mv")["prism"] == 0


def test_a_shutter_layer_belongs_in_an_effect():
    rig = rigview([mover("mv", setup={"strobe_hz_max": 5.0})], [MOVER], [out("dmx", "artnet")])
    lights = {"groups": {"all": {"label": "All", "select": "kind:lamp"}}}
    strobe = {"id": "s", "kind": "shutter", "mode": "strobe", "hz": [6, 10]}
    box = {"look": look(light_programs={"all": {"group": "all", "scene": "bad", "at_ms": 0.0, "effects": [
        {"id": "fx:1", "name": "burst", "at_ms": 10_000.0, "until_ms": 12_000.0, "strength": 1.0}],
        "transition": {"name": "cut", "kind": "cut", "params": {}, "at_ms": 0.0}}})}
    effects = {"burst": {"label": "Strobe burst", "on": ["drop"], "layers": [strobe]}}
    e = engine(rig, box, {"bad": scene(strobe)}, effects, lights=lights)
    assert any("a shutter layer belongs in an effect" in p for p in e._program.scene_problems)
    assert at(e.evaluate("dmx", 10.5, DMX_CAPS), "mv")["shutter"] == {"mode": "strobe", "hz": 5.0}   # its own cap
    assert at(e.evaluate("dmx", 12.5, DMX_CAPS), "mv")["shutter"] == {"mode": "open", "hz": None}


# -- the hold (§5.7) ---------------------------------------------------------------------------------------------------
def test_the_hold_overrides_everything_and_hands_back_through_a_glide():
    setup = {"max_speed": {"pan": 180.0, "tilt": 120.0}, "limits": {"pan": [-30.0, 30.0]},
             "positions": {"a": {"pan": -20.0, "tilt": 10.0}}}
    rig = rigview([mover("mv", setup=setup)], [MOVER], [out("dmx", "artnet")])
    sq = {"id": "sq", "fx": "square", "phase": "beat", "depth": 1.0, "needs": "none", "attack_ms": 0,
          "decay_ms": 250}
    box = {"look": look(light_scene="m")}
    e = engine(rig, box, {"m": scene({"id": "p", "kind": "position", "mode": "aim", "centre": "a"}, sq)})
    run(e, box, 10.0, 11.0)
    got = e.hold("mv", {"pan": 45.0, "zoom": 30.0, "wobble": 1, "gobo": "x"}, idle_s=5.0, smooth_ms=80.0)
    assert got["held"] is True and got["target"]["pan"] == 30.0           # its soft limit
    assert sorted(got["unknown"]) == ["gobo", "wobble"]
    assert got["target"]["intensity"] == 1.0 and got["target"]["shutter"] == {"mode": "open"}
    e.identify("mv", 5.0)
    e.set_follow({"mv": "off"})
    frames = run(e, box, 11.02, 12.0)
    for _t, fr in frames:
        assert fr["busy"] is True and fr["fixtures"]["mv"]["intensity"] == 1.0   # no square, no identify, no follow
    a = at(frames[-1][1], "mv")
    assert a["pan"] == pytest.approx(30.0, abs=0.01) and a["zoom"] == pytest.approx(30.0, abs=0.01)
    assert e.holds()["mv"]["now"]["pan"] == pytest.approx(30.0, abs=0.01)
    assert e.stats()["motion"]["mv"]["held"] is True
    box["now"] = 12.0
    assert e.hold("mv", {"pan": 45.0}, idle_s=5.0, free=True)["target"]["pan"] == 45.0   # free: the full range
    a = at(run(e, box, 12.02, 13.0)[-1][1], "mv")
    assert a["pan"] == pytest.approx(45.0, abs=0.01)
    e.set_follow({})
    e.identify("mv", 0)
    assert e.release("mv") is True and e.release("mv") is False
    frames = run(e, box, 13.02, 14.0)
    prev = 45.0
    for _t, fr in frames:                                                # back to its program: a glide, not a jump
        p = at(fr, "mv")["pan"]
        assert abs(p - prev) <= 180.0 * FRAME + 1e-9
        prev = p
    assert prev == -20.0 and frames[-1][1]["busy"] is False
    box["now"] = 14.0
    e.hold("mv", {"pan": 0.0}, idle_s=0.5)
    assert run(e, box, 14.02, 14.4)[-1][1]["busy"] is True
    assert run(e, box, 14.42, 14.8)[-1][1]["busy"] is False               # idle: handed back
    assert e.holds() == {}


def test_a_hold_reaches_a_light_with_no_movement_too():
    rig = rigview([fx("hue", HUE_T, "hue")], [HUE_T], [out("hue", "hue")])
    box = {"look": look()}
    e = engine(rig, box)
    got = e.hold("hue", {"intensity": 0.25, "colour": "#ff0000", "pan": 10}, smooth_ms=0)
    assert got["held"] and got["unknown"] == ["pan"]
    fr = e.evaluate("hue", 10.0, HUE_CAPS)
    assert fr["fixtures"]["hue"]["rgb"] == pytest.approx([0.25, 0.0, 0.0]) and "attrs" not in fr["fixtures"]["hue"]
    assert e.hold("ghost", {})["held"] is False
    with pytest.raises(ValueError, match="^bad_request"):
        e.hold("hue", "up")


# -- inactive frames (§5.1) --------------------------------------------------------------------------------------------
def test_an_inactive_mover_glides_home_busy_until_it_lands():
    setup = {"max_speed": {"pan": 180.0, "tilt": 120.0}, "home": {"pan": 0.0, "tilt": 0.0},
             "positions": {"a": {"pan": 90.0, "tilt": 0.0}}}
    rig = rigview([mover("mv", setup=setup)], [MOVER], [out("dmx", "artnet")])
    box = {"look": look(light_scene="m")}
    e = engine(rig, box, {"m": scene({"id": "p", "kind": "position", "mode": "aim", "centre": "a"})})
    caps = {**DMX_CAPS, "idle_without_vibe": True}
    assert at(run(e, box, 10.0, 11.5, caps=caps)[-1][1], "mv")["pan"] == 90.0
    box["pal"] = None                                                    # no vibe: inactive on an Art-Net output
    frames = run(e, box, 11.52, 13.0, caps=caps)
    assert frames[0][1]["active"] is False and frames[0][1]["busy"] is True
    prev = 90.0
    for _t, fr in frames:
        p = at(fr, "mv")["pan"]
        assert abs(p - prev) <= 180.0 * FRAME + 1e-9
        prev = p
    landed = next(i for i, (_t, fr) in enumerate(frames) if at(fr, "mv")["pan"] == 0.0)
    assert all(fr["busy"] for _t, fr in frames[:landed - 1]) and frames[-1][1]["busy"] is False


# -- configuration (§5.2, §12) -----------------------------------------------------------------------------------------
def test_bad_kinds_and_values_are_left_out_with_problem_lines():
    rig = rigview([mover("mv")], [MOVER], [out("dmx", "artnet")])
    sc = scene({"id": "a", "kind": "laser"}, {"id": "b", "kind": "position", "mode": "spiral"},
               {"id": "c", "kind": "position", "mode": "bounce"}, {"id": "d", "kind": "beam"},
               {"id": "e", "kind": "gobo", "slot": True}, {"id": "f", "kind": "colour", "role": "pink"},
               {"id": "g", "kind": "cct", "k": "warm"}, {"id": "h", "kind": "beam", "zoom": "huge"},
               {"id": "i", "kind": "position", "mode": "aim", "order": "sideways"},
               {"id": "j", "kind": "beam", "zoom": {"fx": "sine", "between": [1]}},
               {"id": "ok", "kind": "beam", "zoom": 20, "colour": 1})
    box = {"look": look(light_scene="m")}
    e = engine(rig, box, {"m": sc}, lights={"motion": {"accel_s": -1, "default_max_speed_deg_s": 5},
                                           "jog": {"idle_s": 0}})
    text = "\n".join(e._program.scene_problems)
    for needle in ("kind 'laser'", "mode 'spiral'", "a bounce needs points", "no zoom, focus, iris or frost",
                   "slot True", "role", "k 'warm'", "'huge'", "order 'sideways'", "between [1]",
                   "light_scenes.m.layers.ok.colour: not a layer key"):
        assert needle in text, needle
    assert [L.id for L in e._program.scenes["m"].layers] == ["m/ok"]
    probs = "\n".join(box["probs"])
    assert "lights.motion.accel_s" in probs and "default_max_speed_deg_s" in probs and "lights.jog.idle_s" in probs
    m = e._program.motion
    assert (m.accel_s, m.max_speed, m.jog_idle_s) == (lk.DEFAULT_ACCEL_S, lk.DEFAULT_MAX_SPEED_DEG_S,
                                                      lk.DEFAULT_JOG_IDLE_S)


def test_the_real_catalogue_compiles_against_a_rig_of_movers_and_lamps():
    from show_service import light_scenes
    cat = light_scenes.Catalogue()
    cat.poll()
    rig = rigview([mover(f"mv{i}", 1 + 20 * i) for i in range(2)] + [fx(f"hue.{i}", HUE_T, "hue") for i in range(4)],
                  [MOVER, HUE_T], [out("dmx", "artnet"), out("hue", "hue")])
    e = LookEngine(light_scenes=cat, space_mod=False)
    assert e.configure({"layers": []}, rig) == []
    assert e._program.scene_problems == (), e._program.scene_problems
    for name, sc in e._program.scenes.items():
        for L in sc.layers:                                              # a mover layer reaches the movers only
            if L.kind == "position":
                assert set(L.members) == {"mv0", "mv1"}, (name, L.id)
    box = {"look": look()}
    e.look_fn = lambda: box["look"]
    e.palette_fn = lambda t: {"*": (1.0, 1.0, 1.0)}
    e._snap_fn = SnapClock(anchor=10.0, period=0.5).snapshot
    for name in cat.scenes:                                              # every scene runs a frame without a crash
        box["look"] = look(light_scene=name, palette={"roles": {"base": {"hex": "#ff0000"},
                                                                 "accent": {"hex": "#00ff00"},
                                                                 "highlight": {"hex": "#0000ff"}}})
        e.evaluate("dmx", 10.0 + len(name) * 0.01, DMX_CAPS)
        e.evaluate("hue", 10.0 + len(name) * 0.01, HUE_CAPS)


def test_live_view_shows_where_each_beam_lands():
    p = pose([3.0, 4.0, 2.9], [math.pi, 0.0, 0.0])
    view = room(mv={"at": [3.0, 4.0, 2.9], "pose": p}, hue={"at": [1.0, 1.0, 1.0]})
    rig = rigview([mover("mv", setup={"slots": {"gobo": {"2": "Stars"}}}), fx("hue", HUE_T, "dmx")], [MOVER, HUE_T],
                  [out("dmx", "artnet")])
    box = {"look": look(light_scene="m", light_programs={}, flow={"id": 1, "dir": "up"})}
    e = engine(rig, box, {"m": scene({"id": "g", "kind": "gobo", "slot": 2})}, space=view)
    fr = e.evaluate("dmx", 10.0, DMX_CAPS)
    lv = e.live_view({"dmx": fr})
    mv = lv["fixtures"]["mv"]
    assert mv["calibrated"] is True and mv["surface"] == "floor" and mv["hit"] == [3.0, 4.0, 0.0]
    assert mv["gobo"] == "Stars" and mv["prism"] is False and mv["held"] is False
    assert lv["fixtures"]["hue"]["at"] == [1.0, 1.0, 1.0] and lv["fixtures"]["hue"]["dir"] is None
    assert lv["flow"]["dir"] == "up"
