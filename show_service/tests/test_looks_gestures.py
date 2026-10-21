"""looks: the room ensemble on the rig's lights (docs/ENSEMBLE.md §7.2, §11.2): gestures, roles, staggers and canon.
Deterministic: a fake clock body (120 bpm, beat 0 at 10.0 s, 4/4), chosen send times, and fake palette_fn / look_fn
whose LookData carries ``ensemble`` and ``gestures`` as the director sends them; the real fixture_encode and room
geometry where movers are involved. Nothing is read from config/ or written anywhere.

The rig: one DMX RGB par (an artnet-caps output: hits, rise 0) playing as ``lamps:left`` and one sm-hue lamp (a
hue-caps output: no hits, min attack 80 ms, min period 160 ms, rise 99 ms) as ``lamps:right``.
"""

from __future__ import annotations

import math

import pytest

from show_service import contract, fixtures, palette
from show_service import looks as lk
from show_service import space as sp
from show_service.looks import LookEngine
from show_service.tests.test_looks import (DMX_CAPS, HUE_CAPS, HUE_T, SnapClock, feats, fx, lat, layer, look, out,
                                           rigview)
from show_service.tests.test_looks_motion import Cat, mover, pose, room, scene

RGB = fixtures.BUILTIN_TYPES["generic-rgb"]
MOVER = fixtures.BUILTIN_TYPES["generic-mover-full"]          # zoom 10..40 deg
MOVER_NZ = fixtures.BUILTIN_TYPES["generic-mover"]            # pan and tilt only
DMX = {**DMX_CAPS, "min_period_ms": 0.0}
HUE = {**HUE_CAPS, "min_period_ms": 160.0}
BEAT = 0.5
HUE_RISE = 0.099
FRAME = 1.0 / 43.0
FAST = {"pan": 5000.0, "tilt": 5000.0}
LEVEL = 0.5
T = 11.0
#: show.yaml ensemble.gestures.shapes' starting points (docs/ENSEMBLE.md §10)
SHAPES = {
    "accent": {"attack_ms": 0, "hold_ms": 40, "decay_ms": 160, "depth": 0.25, "colour_depth": 0.0, "kick": 0.0},
    "downbeat": {"attack_ms": 0, "hold_ms": 60, "decay_ms": 220, "depth": 0.35, "colour_depth": 0.2, "kick": 0.3},
    "hit": {"attack_ms": 0, "hold_ms": 80, "decay_ms": 400, "depth": 1.0, "colour_depth": 0.6, "kick": 1.0},
    "section": {"attack_ms": 80, "hold_ms": 120, "decay_ms": 900, "depth": 0.5, "colour_depth": 0.6, "kick": 0.6},
    "chord": {"attack_ms": 0, "hold_ms": 60, "decay_ms": 350, "depth": 0.5, "colour_depth": 0.5, "kick": 0.3},
}
ACCOMPANY = {"level": 0.7, "depth": 0.6, "motion": 0.5, "pulse": 0.6, "fx_density": 0.5, "gestures": 0.5}


# -- LookData's ensemble and gestures, as the director sends them (contract.EnsembleData, GestureData) -----------------
def player(members, *, side="left", stagger=0.0, canon=0.0, role="even", scale=None) -> dict:
    sc = {"level": 1.0, "depth": 1.0, "motion": 1.0, "pulse": 1.0, "fx_density": 1.0, "gestures": 1.0}
    sc.update(scale or {})
    return {"role": role, "scale": sc, "stagger_beats": stagger, "stagger_s": stagger * BEAT, "palette_role": None,
            "energy_bias": 0.0, "register": None, "clock_offset_beats": canon, "team": 0 if side == "left" else 1,
            "kind": "lights", "side": side, "x": -1.0 if side == "left" else 1.0, "label": f"Lamps, {side}",
            "screen": None, "members": list(members)}


def nxt(players: dict, at_ms: float) -> dict:
    return {"at_ms": at_ms, "form": "spotlight", "kind": "solo_and_accompany", "label": "One side leads", "line": None,
            "players": players, "why": "test"}


def ensemble(players: dict, *, at_ms=0.0, next_=None, rev=1, glide_ms=0.0) -> dict:
    return {"id": 1, "rev": rev, "form": "echo-across", "kind": "echo", "label": "Echo across the room",
            "line": "Playing together: echo, left to right", "params": {}, "order": list(players),
            "players": players, "screens": {}, "since_ms": 0.0, "at_ms": at_ms, "glide_ms": glide_ms, "next": next_,
            "source": "pick", "why": "test"}


def part(pid: str, at_s: float, strength: float = 1.0, *, predicted: bool = True, colour=None, gen: int = 0) -> dict:
    return {"player": pid, "at_ms": at_s * 1000.0, "strength": strength, "gen": gen, "predicted": predicted,
            "colour": colour}


def gesture(gid: str, kind: str, *parts, seq: int = 1, dur_ms: float = 0.0) -> dict:
    return {"id": gid, "seq": seq, "t_ms": 0.0, "kind": kind, "source": "look", "form": "echo-across",
            "strength": 1.0, "shape": dict(SHAPES[kind]), "dur_ms": dur_ms, "register": None, "pitches": [],
            "beat": None, "parts": list(parts), "why": "test"}


LR = {"lamps:left": player(["par"]), "lamps:right": player(["hue"], side="right")}


# -- the engine -------------------------------------------------------------------------------------------------------
def engine(rig: dict, box: dict, *, looks=None, scenes=None, lights=None, space=None, features=False) -> LookEngine:
    box.setdefault("pal", {"*": (LEVEL, LEVEL, LEVEL)})
    box.setdefault("look", look())
    box.setdefault("now", 10.0)
    box.setdefault("stale", False)
    e = LookEngine(palette_fn=lambda t: box["pal"], look_fn=lambda: box["look"],
                   clock=SnapClock(anchor=10.0, period=BEAT),
                   features_fn=(lambda: feats(0.0 if box["stale"] else box["now"])) if features else None,
                   mono=lambda: box["now"], light_scenes=Cat(scenes), space_mod=sp if space is not None else False)
    assert e.configure(looks if looks is not None else {"layers": []}, rig, lights=lights, space=space) == []
    return e


def two(box: dict, **kw) -> LookEngine:
    rig = rigview([fx("par", RGB, "dmx", address=1, latency=lat(0.0)),
                   fx("hue", HUE_T, "hue", latency=lat(HUE_RISE * 1000.0))],
                  [RGB, HUE_T], [out("dmx", "artnet"), out("hue", "hue")])
    return engine(rig, box, **kw)


def pars(box: dict, **kw) -> LookEngine:
    """Two pars on one DMX output: ``l`` (lamps:left) and ``r`` (lamps:right)."""
    rig = rigview([fx("l", RGB, "dmx", address=1), fx("r", RGB, "dmx", address=4)], [RGB], [out("dmx", "artnet")])
    return engine(rig, box, **kw)


def frame(e: LookEngine, box: dict, oid: str, t: float, caps: dict) -> dict:
    box["now"] = t
    return e.evaluate(oid, t, caps)


def inten(fr: dict, fid: str) -> float:
    return fr["fixtures"][fid]["intensity"]


def hue_at(e: LookEngine, box: dict, ti: float) -> float:
    """The Hue lamp's intensity on the frame that lands at photon time ``ti`` (sent its rise earlier)."""
    return inten(frame(e, box, "hue", ti - HUE_RISE, HUE), "hue")


# -- DMX: a predicted step ---------------------------------------------------------------------------------------------
def test_dmx_a_predicted_hit_steps_up_on_its_line_holds_min_pulse_then_decays_back_to_the_level():
    box: dict = {}
    e = two(box)
    s = 0.3
    box["look"] = look(ensemble=ensemble(LR), gestures=[gesture("g:hit:1", "hit", part("lamps:left", T, s))])
    hold, dec = 0.080, 0.400                                  # max(hold_ms, the par's min_pulse 40 ms); decay_ms
    assert inten(frame(e, box, "dmx", T - 0.01, DMX), "par") == pytest.approx(LEVEL)   # a step: nothing before it
    assert inten(frame(e, box, "dmx", T, DMX), "par") == pytest.approx(LEVEL + 1.0 * s)
    assert inten(frame(e, box, "dmx", T + hold - 0.005, DMX), "par") == pytest.approx(LEVEL + s)
    assert inten(frame(e, box, "dmx", T + hold + dec, DMX), "par") == pytest.approx(LEVEL + s * math.exp(-1.0))
    assert inten(frame(e, box, "dmx", T + hold + 8 * dec + 0.01, DMX), "par") == pytest.approx(LEVEL)
    for ti in (T, T + 0.05, T + 0.3):
        assert hue_at(e, box, ti) == pytest.approx(LEVEL)      # lamps:right: never touched
    st = e.stats()["gestures"]
    assert (st["taken"], st["shown"], st["by_kind"], st["parts"], st["unknown"]) == (1, 1, {"hit": 1}, 1, 0)


# -- Hue: the rise limit, the reactive swell, the min period -----------------------------------------------------------
def test_hue_a_predicted_part_rises_over_the_min_attack_to_peak_on_its_line_within_the_rise_limit():
    box: dict = {}
    e = two(box)
    s, depth, a = 0.4, SHAPES["downbeat"]["depth"], 0.080
    box["look"] = look(ensemble=ensemble(LR), gestures=[gesture("g:beat:1:2", "downbeat", part("lamps:right", T, s))])
    dt = 0.02
    got = [(T + k * dt, hue_at(e, box, T + k * dt)) for k in range(-20, 10)]
    for ti, v in got:
        if ti < T - a - 1e-6:
            assert v == pytest.approx(LEVEL)
    vals = dict((round(ti - T, 3), v) for ti, v in got)
    assert vals[-0.04] == pytest.approx(LEVEL + depth * s * 0.5, abs=1e-6)    # half way up the smoothstep
    assert vals[0.0] == pytest.approx(LEVEL + depth * s, abs=1e-6)            # peaks on its line (photon time)
    for (_t0, v0), (_t1, v1) in zip(got, got[1:]):
        assert v1 - v0 <= contract.SLEW_PEAK * dt / a + 1e-9                  # never steeper than the rise limit


def test_hue_a_reactive_part_swells_over_at_least_300_ms():
    box: dict = {}
    e = two(box)
    s, depth = 0.6, SHAPES["chord"]["depth"]
    box["look"] = look(ensemble=ensemble(LR),
                       gestures=[gesture("g:piano:1", "chord", part("lamps:right", T, s, predicted=False))])
    assert hue_at(e, box, T - 0.05) == pytest.approx(LEVEL)
    assert hue_at(e, box, T) == pytest.approx(LEVEL)                          # a swell starts at its time
    assert hue_at(e, box, T + 0.15) == pytest.approx(LEVEL + depth * s * 0.5, abs=1e-6)
    assert hue_at(e, box, T + 0.30) == pytest.approx(LEVEL + depth * s, abs=1e-6)


def test_on_hue_two_parts_closer_than_its_min_period_make_one_flash_but_dmx_shows_both():
    def run(gestures):
        box: dict = {}
        e = two(box)
        box["look"] = look(ensemble=ensemble(LR), gestures=gestures)
        return e, [hue_at(e, box, T - 0.2 + k * 0.02) for k in range(40)]
    e2, both = run([gesture("g:a", "downbeat", part("lamps:right", T, 0.3)),
                    gesture("g:b", "downbeat", part("lamps:right", T + 0.1, 0.4))])
    _e1, one = run([gesture("g:a", "downbeat", part("lamps:right", T, 0.4))])
    assert both == pytest.approx(one)                                         # merged into the first, the larger
    assert e2.stats()["gestures"]["merged"] == 1
    box: dict = {}
    e = two(box)
    box["look"] = look(ensemble=ensemble(LR), gestures=[gesture("g:a", "downbeat", part("lamps:left", T, 0.3)),
                                                        gesture("g:b", "downbeat", part("lamps:left", T + 0.1, 0.4))])
    frame(e, box, "dmx", T, DMX)
    assert inten(frame(e, box, "dmx", T + 0.1, DMX), "par") == pytest.approx(LEVEL + 0.35 * 0.4)   # the second
    assert e.stats()["gestures"]["merged"] == 0


# -- routing, once, malformed ------------------------------------------------------------------------------------------
def test_a_part_lights_only_its_players_members_and_an_unknown_player_is_counted_never_raised():
    box: dict = {}
    e = two(box)
    players = {**LR, "screen.south": {**player(["screen.south"]), "kind": "screen"},
               "device:mirror_ball": {**player([]), "kind": "device"}}
    box["look"] = look(ensemble=ensemble(players), gestures=[gesture(
        "g:1", "hit", part("lamps:right", T, 0.3), part("nobody", T), part("screen.south", T),
        part("device:mirror_ball", T))])
    for t in (T - 0.1, T, T + 0.05):
        assert inten(frame(e, box, "dmx", t, DMX), "par") == pytest.approx(LEVEL)   # the par is lamps:left
    assert hue_at(e, box, T) == pytest.approx(LEVEL + 0.3)
    st = e.stats()["gestures"]
    assert st["unknown"] == 1 and st["parts"] == 1                           # a screen or a device: nothing here
    box2: dict = {}
    e2 = two(box2)                                                            # no ensemble: a fixture id is itself
    box2["look"] = look(gestures=[gesture("g:2", "hit", part("par", T, 0.2))])
    assert inten(frame(e2, box2, "dmx", T, DMX), "par") == pytest.approx(LEVEL + 0.2)


def test_each_id_and_seq_is_taken_once_a_resend_replaces_it_and_an_empty_one_calls_it_off():
    box: dict = {}
    e = two(box)
    g1 = gesture("g:hit:7", "hit", part("lamps:left", T, 0.3), seq=5)
    box["look"] = look(ensemble=ensemble(LR), gestures=[g1])
    frame(e, box, "dmx", 10.5, DMX)
    box["look"] = look(ensemble=ensemble(LR), gestures=[dict(g1)])          # a new list, the same (id, seq)
    frame(e, box, "hue", 10.5, HUE)
    frame(e, box, "dmx", 10.52, DMX)
    st = e.stats()["gestures"]
    assert (st["taken"], st["replaced"], st["parts"]) == (1, 0, 1)
    assert inten(frame(e, box, "dmx", T, DMX), "par") == pytest.approx(LEVEL + 0.3)   # one flash
    g2 = gesture("g:hit:7", "hit", part("lamps:left", T + 1.0, 0.3), seq=9)          # a moved line
    box["look"] = look(ensemble=ensemble(LR), gestures=[g2])
    assert inten(frame(e, box, "dmx", T + 0.3, DMX), "par") == pytest.approx(LEVEL)  # the old one is gone
    box["look"] = look(ensemble=ensemble(LR), gestures=[g1, g2])           # an older copy changes nothing
    frame(e, box, "dmx", T + 0.32, DMX)
    assert [p.at_s for p in e._gring] == [pytest.approx(T + 1.0)]
    assert e.stats()["gestures"]["replaced"] == 1
    box["look"] = look(ensemble=ensemble(LR), gestures=[gesture("g:hit:7", "hit", seq=10)])   # called off
    assert inten(frame(e, box, "dmx", T + 1.0, DMX), "par") == pytest.approx(LEVEL)
    st = e.stats()["gestures"]
    assert (st["removed"], st["parts"]) == (1, 0)


def test_take_gesture_returns_false_for_a_body_that_doesnt_read_and_skips_an_unknown_kind():
    box: dict = {}
    e = two(box)
    box["look"] = look(ensemble=ensemble(LR))
    good = gesture("g:ok", "accent", part("lamps:left", T))
    assert e.take_gesture(good) is True
    assert e.take_gesture(good) is True                                       # again: nothing new
    bad = [None, "nope", {"id": 3, "parts": []}, {"id": "g:x", "parts": "no"},
           {**good, "id": "g:y", "shape": {"depth": 1.0}},
           {**good, "id": "g:z", "parts": [{"player": "lamps:left"}]},
           {**good, "id": "g:v", "shape": {**SHAPES["accent"], "kick": "lots"}}]
    for b in bad:
        assert e.take_gesture(b) is False
    assert e.take_gesture({**good, "id": "g:w", "kind": "wobble"}) is False   # skipped, never raised
    st = e.stats()["gestures"]
    assert (st["taken"], st["malformed"], st["skipped"], st["parts"]) == (1, len(bad), 1, 1)
    assert set(st) == {"taken", "replaced", "removed", "shown", "merged", "unknown", "skipped", "malformed",
                       "by_kind", "parts"}


# -- the colour pop ----------------------------------------------------------------------------------------------------
def test_a_colour_pop_moves_toward_the_parts_palette_role_at_full_value_by_colour_depth_x_w():
    box: dict = {}
    e = two(box)
    roles = {"base": {"hex": "#202020"}, "accent": {"hex": "#00ff00"}, "highlight": {"hex": "#800000"}}
    pal = {"name": "x", "source": "vibe", "roles": roles, "emphasis": "base"}
    box["look"] = look(palette=pal, ensemble=ensemble(LR), gestures=[
        gesture("g:look:L2", "section", part("lamps:left", T, 0.5, colour="highlight"))])
    cd = SHAPES["section"]["colour_depth"]

    def want(x):
        c = palette.mix((1.0, 1.0, 1.0), (1.0, 0.0, 0.0), x)                 # #800000 at full value: red
        return [v / max(c) for v in c]
    assert frame(e, box, "dmx", T - 0.2, DMX)["fixtures"]["par"]["colour"] == [1.0, 1.0, 1.0]
    assert frame(e, box, "dmx", T - 0.04, DMX)["fixtures"]["par"]["colour"] == pytest.approx(want(cd * 0.5 * 0.5))
    fr = frame(e, box, "dmx", T, DMX)
    assert fr["fixtures"]["par"]["colour"] == pytest.approx(want(cd * 0.5))
    assert fr["fixtures"]["par"]["intensity"] == pytest.approx(LEVEL + 0.5 * 0.5)


# -- roles -------------------------------------------------------------------------------------------------------------
LAYERS = {"layers": [layer(id="b", needs="none"), layer(id="r", phase="bar", period_bars=1, needs="none")]}


def test_accompany_takes_a_beat_layers_depth_x_pulse_and_a_bar_layers_x_motion():
    box: dict = {}
    e = pars(box, looks=LAYERS)
    players = {"lamps:left": player(["l"], role="accompany", scale=ACCOMPANY),
               "lamps:right": player(["r"], side="right")}
    box["look"] = look(ensemble=ensemble(players))
    f = frame(e, box, "dmx", 10.3, DMX)              # beat 0.6: the beat square low, the bar's high
    assert inten(f, "l") == pytest.approx(LEVEL * (1.0 - 0.6)) and inten(f, "r") == pytest.approx(0.0)
    f = frame(e, box, "dmx", 11.1, DMX)              # beat 2.2: the beat square high, the bar's low
    assert inten(f, "l") == pytest.approx(LEVEL * (1.0 - 0.5)) and inten(f, "r") == pytest.approx(0.0)


def test_next_takes_effect_at_its_line_and_the_role_glides_over_glide_ms():
    box: dict = {}
    e = pars(box, looks={"layers": [layer(id="b", needs="none")]})
    even = {"lamps:left": player(["l"]), "lamps:right": player(["r"], side="right")}
    acc = {**even, "lamps:left": player(["l"], role="accompany", scale=ACCOMPANY)}
    box["look"] = look(ensemble=ensemble(even, next_=nxt(acc, 12_000.0), glide_ms=1000.0, rev=2))
    assert inten(frame(e, box, "dmx", 11.8, DMX), "l") == pytest.approx(0.0)     # beat 3.6, low: still even
    frame(e, box, "dmx", 12.0, DMX)                                               # its line: the glide starts
    for t, pulse in ((12.3, 0.88), (12.8, 0.68), (13.3, 0.6)):                   # beats 4.6, 5.6, 6.6: low
        f = frame(e, box, "dmx", t, DMX)
        assert inten(f, "l") == pytest.approx(LEVEL * (1.0 - pulse)) and inten(f, "r") == pytest.approx(0.0)
    box["look"] = look(ensemble=ensemble(acc, at_ms=12_000.0, glide_ms=1000.0, rev=3))   # folded in: no jump
    assert inten(frame(e, box, "dmx", 13.8, DMX), "l") == pytest.approx(LEVEL * 0.4)


def test_motion_scales_a_position_patterns_size():
    rig = rigview([mover("a", 1, setup={"max_speed": FAST}), mover("b", 40, setup={"max_speed": FAST})], [MOVER],
                  [out("dmx", "artnet")])
    sc = {"m": scene({"id": "c", "kind": "position", "mode": "circle", "size_deg": 20, "phase": "bar",
                      "period_bars": 1})}
    box: dict = {"look": look(light_scene="m")}
    e = engine(rig, box, scenes=sc, lights={"motion": {"accel_s": 0}})
    players = {"m:left": player(["a"], role="accompany", scale=ACCOMPANY), "m:right": player(["b"], side="right")}
    box["look"] = look(light_scene="m", ensemble=ensemble(players))
    pans = {"a": [], "b": []}
    for k in range(120):
        f = frame(e, box, "dmx", 10.0 + k * FRAME, DMX)
        for fid in pans:
            pans[fid].append(f["fixtures"][fid]["attrs"]["pan"])
    span = {fid: max(v[20:]) - min(v[20:]) for fid, v in pans.items()}
    assert span["b"] == pytest.approx(40.0, rel=0.02) and span["a"] == pytest.approx(20.0, rel=0.02)


# -- staggers ----------------------------------------------------------------------------------------------------------
LIGHTS = {"groups": {"lamps": {"label": "Lamps", "select": "kind:lamp"}}}
SQ = {"id": "d", "fx": "square", "phase": "beat", "depth": 1.0, "needs": "none", "attack_ms": 0, "decay_ms": 250}


def progs(name: str, kind: str, at_ms: float, **params) -> dict:
    return {"lamps": {"group": "lamps", "label": "Lamps", "scene": name, "effects": [], "at_ms": at_ms,
                      "transition": {"name": kind, "kind": kind, "params": params, "at_ms": at_ms}}}


def _staggered(kind: str, ens_at_ms: float, next_at_ms, **params) -> tuple[LookEngine, dict]:
    players = {"lamps:left": player(["l"]), "lamps:right": player(["r"], side="right", stagger=4)}
    box: dict = {"look": look(light_programs=progs("on", "cut", 10_000.0), ensemble=ensemble(players,
                                                                                          at_ms=10_000.0))}
    e = pars(box, scenes={"on": scene(), "off": scene(SQ)}, lights=LIGHTS)
    frame(e, box, "dmx", 10.0, DMX)
    box["look"] = look(light_programs=progs("off", kind, 11_000.0, **params), ensemble=ensemble(
        players, at_ms=ens_at_ms, next_=nxt(players, next_at_ms) if next_at_ms is not None else None, rev=2))
    return e, box


def test_a_crossfade_at_the_look_change_lands_its_stagger_later_on_the_right():
    e, box = _staggered("crossfade", 10_000.0, 11_000.0, seconds=1.0)
    f = frame(e, box, "dmx", 11.3, DMX)                        # beat 2.6, the square's low half: 30% in on the left
    assert inten(f, "l") == pytest.approx(LEVEL * 0.7) and inten(f, "r") == pytest.approx(LEVEL)
    f = frame(e, box, "dmx", 13.3, DMX)                        # 4 beats = 2 s later: 30% in on the right
    assert inten(f, "l") == pytest.approx(0.0) and inten(f, "r") == pytest.approx(LEVEL * 0.7)
    e, box = _staggered("crossfade", 11_000.0, None, seconds=1.0)   # folded in already: ensemble.at_ms matches
    f = frame(e, box, "dmx", 13.3, DMX)
    assert inten(f, "r") == pytest.approx(LEVEL * 0.7)


def test_a_sweep_and_a_transition_away_from_the_look_change_are_not_staggered():
    for kind, ens_at, nx, params in (("sweep", 10_000.0, 11_000.0, {"beats": 2}),
                                     ("crossfade", 10_000.0, None, {"seconds": 1.0})):
        e, box = _staggered(kind, ens_at, nx, **params)
        f = frame(e, box, "dmx", 11.3, DMX)
        assert inten(f, "l") == pytest.approx(LEVEL * 0.7) and inten(f, "r") == pytest.approx(LEVEL * 0.7), kind


def test_a_cut_lands_late_on_the_staggered_side():
    e, box = _staggered("cut", 10_000.0, 11_000.0)
    f = frame(e, box, "dmx", 11.3, DMX)
    assert inten(f, "l") == pytest.approx(0.0) and inten(f, "r") == pytest.approx(LEVEL)
    f = frame(e, box, "dmx", 13.3, DMX)
    assert inten(f, "r") == pytest.approx(0.0)


# -- canon -------------------------------------------------------------------------------------------------------------
def test_canon_runs_a_lights_wave_its_clock_offset_beats_late():
    box: dict = {}
    e = pars(box, looks={"layers": [layer(id="w", fx="saw", phase="bar", period_bars=1, needs="none")]})
    players = {"lamps:left": player(["l"]), "lamps:right": player(["r"], side="right", canon=2.0)}
    box["look"] = look(ensemble=ensemble(players))
    got = [frame(e, box, "dmx", 11.0 + k * 0.05, DMX) for k in range(61)]
    for k in range(20, 61):                                   # 2 beats = 1 s = 20 frames
        assert inten(got[k], "r") == pytest.approx(inten(got[k - 20], "l"), abs=1e-9)
    assert any(abs(inten(f, "r") - inten(f, "l")) > 0.2 for f in got)    # half a bar apart: visibly different


# -- the kick ----------------------------------------------------------------------------------------------------------
def _beam_height_over(view_pose: dict, pan: float, tilt: float, xy: tuple) -> tuple[float, float]:
    """Where a beam passes over the vertical through ``xy``: (its height there, its distance from that line)."""
    o, d = view_pose["at"], sp.beam_dir(view_pose, pan, tilt)
    n2 = d[0] * d[0] + d[1] * d[1]
    s = ((xy[0] - o[0]) * d[0] + (xy[1] - o[1]) * d[1]) / n2
    return o[2] + s * d[2], math.hypot(o[0] + s * d[0] - xy[0], o[1] + s * d[1] - xy[1])


@pytest.mark.parametrize("speed", [5000.0, 20.0])
def test_a_calibrated_movers_kick_lifts_its_aim_point_by_at_most_kick_m_x_kick_within_its_speed(speed):
    p = pose([3.0, 4.0, 2.9], [math.pi, 0.0, 0.0])                     # hung: the pan axis down
    view = room(points={"spot": {"label": "Spot", "at": [3.0, 6.0, 0.0]}}, mv={"at": [3, 4, 2.9], "pose": p})
    rig = rigview([mover("mv", setup={"max_speed": {"pan": speed, "tilt": speed}})], [MOVER],
                  [out("dmx", "artnet")])
    sc = {"m": scene({"id": "a", "kind": "position", "mode": "aim", "centre": "spot"})}
    box: dict = {"look": look(light_scene="m")}
    e = engine(rig, box, scenes=sc, space=view, lights={"motion": {"accel_s": 0}})
    pv = view["appliances"]["mv"]["pose"]
    n = int(10.0 / FRAME)
    for k in range(n):                                                   # it settles on the spot first
        frame(e, box, "dmx", 10.0 + k * FRAME, DMX)
    line = 10.0 + n * FRAME + 0.5
    box["look"] = look(light_scene="m", ensemble=ensemble({"movers:all": player(["mv"])}),
                       gestures=[gesture("g:hit:1", "hit", part("movers:all", line))])
    prev, hs = None, []
    for k in range(n + 1, n + 1 + int(4.0 / FRAME)):
        a = frame(e, box, "dmx", 10.0 + k * FRAME, DMX)["fixtures"]["mv"]["attrs"]
        h, off = _beam_height_over(pv, a["pan"], a["tilt"], (3.0, 6.0))
        assert off < 1e-4                                                # it only ever lifts, straight up
        assert -1e-6 <= h <= lk.KICK_M * SHAPES["hit"]["kick"] + 1e-6
        hs.append(h)
        if prev is not None:
            assert abs(a["tilt"] - prev[1]) <= speed * FRAME + 1e-6 and abs(a["pan"] - prev[0]) <= speed * FRAME + 1e-6
        prev = (a["pan"], a["tilt"])
    assert hs[0] == pytest.approx(0.0, abs=1e-6) and hs[-1] == pytest.approx(0.0, abs=1e-6)   # and back down
    if speed > 1000.0:
        assert max(hs) == pytest.approx(lk.KICK_M, abs=1e-6)           # full height on its line
    else:
        assert 0.0 < max(hs) < lk.KICK_M                                 # slew-limited: degrade, never refused


def test_an_uncalibrated_mover_with_zoom_widens_and_one_without_does_nothing():
    rig = rigview([mover("z", 1, setup={"max_speed": FAST}), fx("nz", MOVER_NZ, "dmx", address=40, latency=lat())],
                  [MOVER, MOVER_NZ], [out("dmx", "artnet")])
    box: dict = {}
    e = engine(rig, box)
    box["look"] = look(ensemble=ensemble({"movers:all": player(["z", "nz"])}),
                       gestures=[gesture("g:hit:1", "hit", part("movers:all", T, 0.5))])
    f0 = frame(e, box, "dmx", T - 0.1, DMX)
    z0 = f0["fixtures"]["z"]["attrs"]["zoom"]
    f1 = frame(e, box, "dmx", T, DMX)
    assert f1["fixtures"]["z"]["attrs"]["zoom"] == pytest.approx(z0 + (40.0 - z0) * 0.5)   # toward its widest
    a0, a1 = f0["fixtures"]["nz"]["attrs"], f1["fixtures"]["nz"]["attrs"]
    assert a1 == a0 and "zoom" not in a1                                  # no zoom, no pose: no guessed direction
    assert f1["fixtures"]["nz"]["intensity"] == pytest.approx(LEVEL + 0.5)                 # it still flashes
    z0a, z1a = f0["fixtures"]["z"]["attrs"], f1["fixtures"]["z"]["attrs"]
    assert {k: v for k, v in z1a.items() if k != "zoom"} == {k: v for k, v in z0a.items() if k != "zoom"}  # no shutter


# -- steady ------------------------------------------------------------------------------------------------------------
def _one(look_kw: dict, *, stale: bool = False, follow=None, at: float = T) -> float:
    box: dict = {}
    e = two(box, features=True)
    box["stale"] = stale
    if follow:
        e.set_follow(follow)
    box["look"] = look(ensemble=ensemble(LR), gestures=[
        gesture("g:hit:1", "hit", part("lamps:left", T, 0.3)),
        gesture("g:beat:1:9", "accent", part("lamps:left", T + 4.0, 0.4))], **look_kw)   # after the hit's 8 decays
    return inten(frame(e, box, "dmx", at, DMX), "par")


def test_no_gesture_under_house_low_a_held_frame_or_on_a_light_not_following():
    assert _one({}) == pytest.approx(LEVEL + 0.3)
    assert _one({"house": True}) == pytest.approx(LEVEL)
    assert _one({"low": True}) == pytest.approx(LEVEL)
    assert _one({}, stale=True) == pytest.approx(LEVEL)                  # stale features with a vibe: held
    assert _one({}, follow={"par": "off"}) == 0.0


def test_over_the_pre_drop_dip_a_hit_shows_and_an_accent_doesnt():
    assert _one({"dip": True}) == pytest.approx(LEVEL + 0.3)
    assert _one({"dip": True}, at=T + 4.0) == pytest.approx(LEVEL)
    assert _one({}, at=T + 4.0) == pytest.approx(LEVEL + 0.25 * 0.4)


# -- parity and degrade ------------------------------------------------------------------------------------------------
def test_without_an_ensemble_or_gestures_frames_are_byte_identical():
    """No ensemble; an all-even ensemble with no staggers; gestures nobody here plays: every frame equal, bytes too."""
    def run(extra: dict) -> list:
        rig = rigview([fx("par", RGB, "dmx", address=1), mover("mv", 10, setup={"max_speed": FAST}),
                       fx("hue", HUE_T, "dmx", latency=lat(99.0))], [RGB, MOVER, HUE_T], [out("dmx", "artnet")])
        sc = {"m": scene({"id": "c", "kind": "position", "mode": "circle", "size_deg": 15, "phase": "bar",
                          "period_bars": 1},
                         {"id": "z", "kind": "beam", "zoom": {"fx": "sine", "phase": "beat", "between": [12, 38]}},
                         layer(id="b", needs="none"), layer(id="r", fx="sine", phase="bar", period_bars=1,
                                                            needs="none", spread=1.0)),
              "n": scene(SQ)}
        box: dict = {"look": look(light_programs=progs("m", "cut", 10_000.0), **extra(10_000.0))}
        e = engine(rig, box, scenes=sc, lights=LIGHTS)
        out_ = [frame(e, box, "dmx", 10.0 + k * FRAME, DMX)["fixtures"] for k in range(60)]
        box["look"] = look(light_programs=progs("n", "crossfade", 11_500.0, seconds=1.0), **extra(11_500.0))
        out_ += [frame(e, box, "dmx", 10.0 + k * FRAME, DMX)["fixtures"] for k in range(60, 160)]
        return out_
    even = {"lamps:all": player(["par", "mv", "hue"])}
    base = run(lambda at: {})
    assert run(lambda at: {"ensemble": ensemble(even, next_=nxt(even, at))}) == base
    assert run(lambda at: {"ensemble": ensemble(even), "gestures": [
        gesture("g:x", "hit", part("nobody", 11.0)), gesture("g:y", "hit", part("screen.north", 11.0))]}) == base


def test_without_contract_gesture_weight_there_are_no_gestures_and_nothing_fails(monkeypatch):
    monkeypatch.delattr(contract, "gesture_weight")
    box: dict = {}
    e = two(box)
    box["look"] = look(ensemble=ensemble(LR), gestures=[gesture("g:hit:1", "hit", part("lamps:left", T, 0.3))])
    assert inten(frame(e, box, "dmx", T, DMX), "par") == pytest.approx(LEVEL)
    assert e.errors == {}
