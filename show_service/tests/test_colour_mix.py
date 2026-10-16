"""colour_mix (README S5.5, where it is called emitters.py): colour to emitters and a fixture's DMX bytes.

Pure functions on hand-built TypeInfo / FixtureInfo dicts (the shapes of contract.py's Stage-5 block); nothing here
reads config, opens a socket or touches a device. ``-s`` prints the solve cost.
"""

from __future__ import annotations

import math
import random
import time

import numpy as np
import pytest

from show_service import colour_mix as cm
from show_service import contract


# -- helpers ----------------------------------------------------------------------------------------------------------
def ttype(chans: list[tuple], *, footprint: int | None = None, emitters: dict | None = None,
          wheel: list | None = None, curve: str = "srgb", tid: str = "t-test") -> dict:
    """chans: (name, attr, extra) per channel, in offset order; extra may carry emitter, fine, default."""
    out = []
    for off, (name, attr, extra) in enumerate(chans):
        ch = {"offset": off, "name": name, "attr": attr, "default": 0}
        ch.update(extra or {})
        out.append(ch)
    attrs = []
    if any(c["attr"] in ("intensity", "emitter") for c in out):
        attrs.append("intensity")
    if any(c["attr"] in ("emitter", "wheel") for c in out):
        attrs.append("colour")
    return {"id": tid, "source": "ofl", "kind": "lamp", "file": None, "mode": None,
            "footprint": footprint if footprint is not None else len(out), "attrs": attrs, "channels": out,
            "emitters": emitters or {}, "wheel": wheel or [], "curve": curve, "problems": []}


def fixture(**kw) -> dict:
    f = {"id": "f-test", "label": "f", "type": "t-test", "kind": "lamp", "output": None, "address": 1, "light": None,
         "screen": None, "zone": None, "placement": None, "role": None, "tags": [], "order": None, "wb": {},
         "curve": None, "pin": {}, "house": None,
         "latency": {"rise_ms": 0.0, "fall_ms": 0.0, "colour_ms": 0.0, "position_ms": 0.0, "min_pulse_ms": 40.0,
                     "source": {}}}
    f.update(kw)
    return f


def em(name: str, **extra) -> tuple:
    return (name.title(), "emitter", {"emitter": name, **extra})


RGB = [em("red"), em("green"), em("blue")]
DIM = ("Dimmer", "intensity", {})


def xy_of(XYZ) -> tuple[float, float]:
    s = float(np.sum(XYZ))
    return float(XYZ[0]) / s, float(XYZ[1]) / s


def emitter_xyz(names, overrides=None) -> np.ndarray:
    """An independent emitter matrix (3 x k) from EMITTER_DEFAULTS (and overrides)."""
    cols = []
    for n in names:
        d = dict(cm.EMITTER_DEFAULTS[n])
        d.update((overrides or {}).get(n, {}))
        (x, y), Y = d["xy"], d["Y"]
        cols.append([x / y * Y, Y, (1 - x - y) / y * Y])
    return np.array(cols).T


def target_xyz(rgb) -> np.ndarray:
    lin = np.array([cm.srgb_to_linear(c) for c in rgb])
    return np.array([[0.4124564, 0.3575761, 0.1804375],
                     [0.2126729, 0.7151522, 0.0721750],
                     [0.0193339, 0.1191920, 0.9503041]]) @ lin


def in_triangle(p, a, b, c) -> bool:
    def s(p1, p2, p3):
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])
    d1, d2, d3 = s(p, a, b), s(p, b, c), s(p, c, a)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


# -- transfer functions and curves -------------------------------------------------------------------------------------
def test_srgb_round_trip_and_clamps():
    for i in range(256):
        v = i / 255
        assert cm.linear_to_srgb(cm.srgb_to_linear(v)) == pytest.approx(v, abs=1e-12)
    assert cm.srgb_to_linear(-1) == 0.0 and cm.srgb_to_linear(2) == 1.0 and cm.srgb_to_linear(math.nan) == 0.0


def test_curves():
    assert cm.curve_out(0.5, "linear") == 0.5
    assert cm.curve_out(0.5, "srgb") == pytest.approx(cm.linear_to_srgb(0.5))
    assert cm.curve_out(0.5, "gamma:2.2") == pytest.approx(0.5 ** (1 / 2.2))
    assert cm.curve_out(0.5, "gamma:0") == cm.curve_out(0.5, "srgb")         # doesn't read: the neutral srgb
    assert cm.curve_out(0.5, "wobbly") == cm.curve_out(0.5, "srgb")
    assert cm.curve_out(1.7, "linear") == 1.0 and cm.curve_out(-1, "gamma:2") == 0.0


# -- RGB, RGBW --------------------------------------------------------------------------------------------------------
def test_rgb_identity_through_srgb():
    t = ttype(RGB)
    f = fixture()
    for v in range(256):
        c = (v / 255, (255 - v) / 255, 1.0)
        assert list(cm.encode(f, t, 1.0, c)) == [v, 255 - v, 255]
    # no dimmer: the frame's rgb (colour x intensity) goes straight across
    rnd = random.Random(3)
    for _ in range(300):
        c, i = (rnd.random(), rnd.random(), 1.0), rnd.random()
        got = cm.encode(f, t, i, c)
        assert all(abs(g - x * i * 255) <= 0.5 + 1e-6 for g, x in zip(got, c)), (c, i, list(got))
    # with a dimmer: the dimmer carries the intensity, the emitters the colour at full value
    td = ttype([DIM] + RGB)
    assert list(cm.encode(f, td, 0.2, (1.0, 0.6, 0.0))) == [51, 255, 153, 0]
    lv, scale = cm.solve((1.0, 0.6, 0.0), td)
    assert scale == 1.0 and lv["red"] == 1.0 and lv["blue"] == 0.0


def test_rgbw_takes_the_shared_white():
    t = ttype(RGB + [em("white")])
    f = fixture()
    assert list(cm.encode(f, t, 1.0, (1.0, 1.0, 1.0))) == [0, 0, 0, 255]
    lv, _ = cm.solve((1.0, 0.5, 0.5), t)
    w = cm.srgb_to_linear(0.5)
    assert lv["white"] == pytest.approx(w) and lv["red"] == pytest.approx(1 - w)
    assert lv["green"] == pytest.approx(0.0) and lv["blue"] == pytest.approx(0.0)
    rnd = random.Random(5)
    for _ in range(200):
        c = (rnd.random(), rnd.random(), rnd.random())
        lv, scale = cm.solve(c, t)
        assert scale == 1.0 and min(lv.values()) >= 0.0
        lin = [cm.srgb_to_linear(x) for x in c]
        assert lv["white"] == pytest.approx(min(lin))
        assert min(lv["red"], lv["green"], lv["blue"]) == pytest.approx(0.0, abs=1e-12)


def test_uv_never_takes_part():
    t = ttype(RGB + [("UV", "emitter", {"emitter": "uv", "default": 7})])
    assert cm._plan(t).method == "rgb"
    assert list(cm.encode(fixture(), t, 1.0, (1.0, 0.0, 0.0))) == [255, 0, 0, 7]
    assert list(cm.encode(fixture(pin={"UV": 99}), t, 1.0, (1.0, 0.0, 0.0))) == [255, 0, 0, 99]


# -- least squares ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("names", [["red", "green", "blue", "amber"], ["red", "green", "blue", "amber", "white"],
                                   ["red", "green", "blue", "lime", "warm_white"]])
def test_nnls_hits_the_target_chromaticity(names, capsys):
    t = ttype([em(n) for n in names])
    A = emitter_xyz(sorted(names))
    rgb_xy = [cm.EMITTER_DEFAULTS[n]["xy"] for n in ("red", "green", "blue")]
    rnd = random.Random(11)
    cm._SOLVES.clear()
    c0 = time.perf_counter()
    checked = 0
    for _ in range(150):
        c = [rnd.random() for _ in range(3)]
        m = max(c)
        if m < 0.05:
            continue
        c = [x / m for x in c]                                  # a colour at full value, as the engine gives it
        tgt = target_xyz(c)
        assert in_triangle(xy_of(tgt), *rgb_xy)                 # sRGB sits inside the LED primaries: in gamut
        lv, scale = cm.solve(c, t)
        x = np.array([lv[n] for n in sorted(names)])
        assert x.min() >= 0.0 and x.max() == pytest.approx(1.0) and scale > 0
        got = xy_of(A @ x)
        want = xy_of(tgt)
        assert math.dist(got, want) < 0.005, (c, got, want)
        checked += 1
    us = (time.perf_counter() - c0) / checked * 1e6
    with capsys.disabled():
        print(f"\n  colour_mix NNLS {'+'.join(names)}: {us:.0f} us a solve (uncached)")
    assert checked > 100


def test_nnls_uses_the_types_emitters_and_caches():
    over = {"amber": {"xy": [0.60, 0.39], "Y": 0.4}}
    names = ["amber", "blue", "green", "red"]
    t = ttype([em(n) for n in names], emitters=over)
    t_default = ttype([em(n) for n in names])
    c = (1.0, 0.55, 0.1)
    lv, _ = cm.solve(c, t)
    lv0, _ = cm.solve(c, t_default)
    assert lv != lv0
    A = emitter_xyz(names, over)
    assert math.dist(xy_of(A @ np.array([lv[n] for n in names])), xy_of(target_xyz(c))) < 0.005
    n0 = len(cm._SOLVES)
    lv2, _ = cm.solve(c, t)
    assert lv2 == lv and len(cm._SOLVES) == n0                 # the second solve was a cache hit
    # a bad override is ignored (the starting point stays)
    t_bad = ttype([em(n) for n in names], emitters={"amber": {"xy": [2, -1], "Y": "lots"}})
    assert cm.solve(c, t_bad) == cm.solve(c, t_default)


def test_nnls_black_and_no_dimmer_intensity():
    names = ["red", "green", "blue", "amber"]
    t = ttype([em(n) for n in names])
    lv, scale = cm.solve((0.0, 0.0, 0.0), t)
    assert all(v == 0.0 for v in lv.values()) and scale == 1.0
    full = cm.encode(fixture(curve="linear"), t, 1.0, (1.0, 0.6, 0.2))
    half = cm.encode(fixture(curve="linear"), t, 0.5, (1.0, 0.6, 0.2))
    k = cm.srgb_to_linear(0.5)                                   # the intensity in linear light
    for a, b in zip(full, half):
        assert b == pytest.approx(a * k, abs=1.0)


def test_declared_emitters_send_rgb_and_rgbw_through_the_solve():
    """README S5.14: a measured warm-white W (or off-sRGB primaries) can't use white = min(r, g, b): a 6500 K request
    would come out near 3000 K. Any declared emitter sends the type through the least-squares solve."""
    over = {"white": {"xy": [0.437, 0.404], "Y": 1.0}}
    names = ["blue", "green", "red", "white"]
    t = ttype([em("red"), em("green"), em("blue"), em("white")], emitters=over)
    assert cm._plan(t).method == "nnls" and cm._plan(ttype([em(n) for n in names])).method == "rgbw"
    A = emitter_xyz(names, over)
    for c in ((1.0, 1.0, 1.0), (1.0, 0.8, 0.6), (0.3, 0.5, 1.0), (1.0, 0.2, 0.1)):
        lv, _ = cm.solve(c, t)
        got = xy_of(A @ np.array([lv[n] for n in names]))
        assert math.dist(got, xy_of(target_xyz(c))) < 0.005, (c, got)
    lv, _ = cm.solve((1.0, 1.0, 1.0), ttype([em(n) for n in ("red", "green", "blue", "white")]))
    old = xy_of(A @ np.array([lv[n] for n in names]))             # the old path, on the measured W: far off
    assert math.dist(old, xy_of(target_xyz((1.0, 1.0, 1.0)))) > 0.05
    t_rgb = ttype(RGB, emitters={"red": {"xy": [0.69, 0.30]}})
    assert cm._plan(t_rgb).method == "nnls"
    assert cm._plan(ttype(RGB, emitters={"red": {"xy": "junk"}})).method == "rgb"   # unreadable: not declared


@pytest.mark.parametrize("curve,k", [("linear", 1.0), ("gamma:2.2", 2.2)])
def test_chromaticity_through_the_curve(curve, k):
    """The bytes, decoded by a fixture whose response matches the type's curve (light = (DMX/255) ** k), land on the
    colour's chromaticity: the solve is in linear light and the curve encodes it (README S5.14). The same bytes
    read by a fixture that answers differently miss, which is why each type's curve must be measured."""
    names = ["amber", "blue", "green", "red"]
    t = ttype([em(n) for n in ("red", "green", "blue", "amber")], curve=curve)
    A = emitter_xyz(names)
    order = ["red", "green", "blue", "amber"]
    worst = 0.0
    for hexc in ("#ff8040", "#ff3080", "#40a0ff", "#ffd000", "#80ff40"):
        c = tuple(int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5))
        out = cm.encode(fixture(), t, 1.0, c)
        light = {n: (b / 255.0) ** k for n, b in zip(order, out)}
        got = xy_of(A @ np.array([light[n] for n in names]))
        worst = max(worst, math.dist(got, xy_of(target_xyz(c))))
    assert worst < 0.01                                            # byte rounding only
    out = cm.encode(fixture(), ttype([em(n) for n in order]), 1.0, (1.0, 128 / 255, 64 / 255))   # srgb ...
    light = {n: b / 255.0 for n, b in zip(order, out)}                                         # ... on a linear one
    assert math.dist(xy_of(A @ np.array([light[n] for n in names])), xy_of(target_xyz((1.0, 128 / 255, 64 / 255)))) \
        > 0.05


# -- the wheel --------------------------------------------------------------------------------------------------------
WHEEL = [{"dmx": 5, "hex": "#ffffff", "name": "Open"}, {"dmx": 20, "hex": "#ff0000", "name": "Red"},
         {"dmx": 40, "hex": "#00ff00", "name": "Green"}, {"dmx": 60, "hex": "#0000ff", "name": "Blue"},
         {"dmx": 80, "hex": "#ffff00", "name": "Yellow"}]


def test_wheel_nearest_slot_with_thresholds():
    assert cm.wheel_slot((1.0, 0.1, 0.05), WHEEL) == 20
    assert cm.wheel_slot((0.1, 0.2, 1.0), WHEEL) == 60
    assert cm.wheel_slot((1.0, 0.9, 0.1), WHEEL) == 80
    assert cm.wheel_slot((1.0, 0.92, 0.92), WHEEL) == 5         # pale: chroma under WHEEL_WHITE_C -> Open
    sparse = [WHEEL[0], WHEEL[2], WHEEL[4]]                       # Open, green, yellow
    assert cm.wheel_slot((0.0, 0.0, 1.0), sparse) == 5           # blue: no coloured slot within 60 deg -> Open
    no_white = WHEEL[1:]
    assert cm.wheel_slot((1.0, 0.92, 0.92), no_white) in (20, 80)   # no white slot: still the nearest colour
    assert cm.wheel_slot((1, 0, 0), []) == 0


def test_wheel_the_white_slot_competes_on_distance():
    """README S5.14: the nearest slot of all, Open included (S5.5 "the nearest colour slot in OKLab"). Above
    WHEEL_WHITE_C a pale or warm colour used to snap to a saturated gel even with Open much nearer."""
    from show_service import palette as pal
    wheel = WHEEL + [{"dmx": 100, "hex": "#ff8000", "name": "Orange"}]
    warm = pal.white(4000)                                        # C ~0.077: over WHEEL_WHITE_C
    pink = pal.rgb_of("#ffd0e0")                                  # C ~0.057
    assert pal.to_oklch(warm)[1] > cm.WHEEL_WHITE_C and pal.to_oklch(pink)[1] > cm.WHEEL_WHITE_C
    assert cm.wheel_slot(warm, wheel) == 5 and cm.wheel_slot(pink, wheel) == 5     # Open, not yellow or orange
    assert cm.wheel_slot((1.0, 0.5, 0.0), wheel) == 100           # a real orange still takes the orange gel
    assert cm.wheel_slot(warm, wheel[1:]) in (80, 100)            # no white slot: the nearest gel


def test_wheel_type_encodes_slot_and_dimmer():
    t = ttype([DIM, ("Color Wheel", "wheel", {"default": 5})], wheel=WHEEL)
    assert list(cm.encode(fixture(), t, 0.5, (0.1, 1.0, 0.1))) == [128, 40]


# -- white balance, fine channels, pins, defaults ---------------------------------------------------------------------
def test_white_balance_gains():
    t = ttype([DIM] + RGB)
    out = cm.encode(fixture(wb={"red": 0.5, "blue": 3.0}), t, 1.0, (1.0, 1.0, 0.5))
    assert out[1] == round(255 * cm.linear_to_srgb(0.5))
    assert out[2] == 255
    assert out[3] == round(255 * cm.linear_to_srgb(min(1.0, 3.0 * cm.srgb_to_linear(0.5))))


def test_fixture_curve_beats_the_types():
    t = ttype([DIM] + RGB, curve="linear")
    c = (1.0, 0.5, 0.0)
    assert cm.encode(fixture(), t, 1.0, c)[2] == round(255 * cm.srgb_to_linear(0.5))
    assert cm.encode(fixture(curve="gamma:2"), t, 1.0, c)[2] == round(255 * cm.srgb_to_linear(0.5) ** 0.5)


def test_sixteen_bit_fine_channels():
    t = ttype([("Dimmer", "intensity", {"fine": 1}), ("Dimmer fine", "other", {}),
               ("Red", "emitter", {"emitter": "red", "fine": 3}), ("Red fine", "other", {}),
               em("green"), em("blue")])
    out = cm.encode(fixture(curve="linear"), t, 1 / 3, (cm.linear_to_srgb(0.25), 0.0, 1.0))
    assert (out[0] << 8 | out[1]) == round(65535 / 3)
    assert (out[2] << 8 | out[3]) == round(65535 * 0.25)
    assert out[4] == 0 and out[5] == 255


def test_defaults_pins_and_footprint():
    t = ttype([DIM, ("Shutter", "shutter", {"default": 8}), ("Program", "other", {"default": 3}),
               ("Pan", "pan", {"default": 128})] + RGB, footprint=10)
    f = fixture(pin={"Program": 200, "Red": 77, "Nonsense": 5})
    out = cm.encode(f, t, 0.0, (0.0, 1.0, 0.0))
    assert len(out) == 10
    assert out[1] == 8                       # the shutter's Open default: never driven
    assert out[2] == 200                     # a pin holds an undriven channel
    assert out[3] == 128                     # pan: not driven in Stage 5
    assert out[4] == 0                       # a pinned channel that is driven: the driven value wins
    assert out[5] == 255 and out[6] == 0
    assert out[7:] == bytes(3)               # past the channels: zeros
    assert cm.encode(fixture(), ttype([]), 1.0, (1, 1, 1)) == b""
    assert cm.encode(fixture(), {"footprint": 0, "channels": []}, 1.0, (1, 1, 1)) == b""


def test_every_emitter_word_has_a_default():
    assert set(cm.EMITTER_DEFAULTS) == set(contract.EMITTERS)
    for e, d in cm.EMITTER_DEFAULTS.items():
        x, y = d["xy"]
        assert 0 < x < 1 and 0 < y < 1 and x + y < 1 and d["Y"] >= 0


def test_the_colour_cache_follows_the_colour_and_the_fixture():
    t = ttype([DIM] + RGB)
    f = fixture()
    assert list(cm.encode(f, t, 1.0, (1.0, 0.0, 0.0))) == [255, 255, 0, 0]
    assert list(cm.encode(f, t, 1.0, (0.0, 1.0, 0.0))) == [255, 0, 255, 0]
    assert list(cm.encode(f, t, 0.2, (0.0, 1.0, 0.0))) == [51, 0, 255, 0]      # only the dimmer moves
    assert list(cm.encode(f, t, 0.2, (1.0, 0.0, 0.0))) == [51, 255, 0, 0]
    f2 = fixture(wb={"red": 0.0}, pin={"Dimmer": 9})                            # a new registry view: a new dict
    assert list(cm.encode(f2, t, 0.2, (1.0, 0.0, 0.0))) == [51, 0, 0, 0]
    t2 = ttype([DIM] + RGB, curve="linear")                                     # a new type: re-planned
    assert cm.encode(f, t2, 1.0, (cm.linear_to_srgb(0.5), 0.0, 0.0))[1] == 128
