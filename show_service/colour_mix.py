"""colour_mix: a colour to a fixture's emitters, and a fixture's DMX bytes (docs/SHOW_ENGINE.md §9.2; README "Stage 5",
S5.5, where this module is called emitters.py). Pure: no threads, no network, no I/O. B13's.

Every ``Rgb`` that comes in is gamma-encoded sRGB 0..1 **at full value** (the largest channel 1, or black), exactly as
the look engine's ``FixtureFrame.colour``; ``intensity`` is the frame's intensity (the palette's value scale:
``rgb = colour x intensity``, as palette.scale_level and room's Hue path read it).

**Colour to emitters** (§9.2), in linear light (sRGB decoded), by the set of emitters the type's channels carry
(``contract.EMITTERS``; ``uv`` never takes part, it stays at its default or pin):
  * ``red green blue`` only: straight across;
  * ``red green blue white``: white = min(r, g, b), subtracted from each;
  * any other set (amber, lime, cyan, the whites, a partial set): non-negative least squares on the emitters' CIE XYZ
    (from each emitter's ``xy`` and relative luminance ``Y``) against the colour's XYZ, with a tiny ridge so the answer
    is unique and moves smoothly as the colour moves (no metameric jumps between emitter triples), then scaled so the
    largest emitter is 1 with the chromaticity kept (the colour at full value, as RGB straight across gives it);
  * a colour wheel: the nearest slot in OKLab, white (Open) slots included, with two thresholds that force the white
    slot: a colour paler than ``WHEEL_WHITE_C`` (OKLCh chroma), and one whose nearest slot is a coloured one more
    than ``WHEEL_MAX_HUE_DEG`` away in hue, when the wheel has a white slot (a wrong hue is worse than white).
A type that declares any emitter's ``xy`` or ``Y`` (fixtures.yaml ``types.<id>.emitters``: measured) always goes
through the least-squares solve, RGB and RGBW sets included, so a warm-white W or off-sRGB primaries still land on
the colour's chromaticity. The solve and the encode assume the type's ``curve`` matches the fixture's response:
``srgb`` only fits a fixture that dims like sRGB; measure each type and set ``linear`` or ``gamma:<g>``.
Then the fixture's ``wb`` gains (per emitter, linear light, clipped at 1), then the curve (the fixture's, else the
type's, else ``srgb``): ``srgb`` sends sRGB-encoded values, so RGB values reach DMX exactly as the palette has them;
``linear`` sends linear light; ``gamma:<g>`` sends light ** (1 / g) for a fixture whose response is DMX ** g.

**Intensity.** A type with an intensity channel gets it there (16-bit when it has a fine channel; the value as is, a
console fader's) and its emitters at full colour. A type without one gets the emitters x intensity before the curve:
RGB and RGBW from ``colour x intensity`` itself (so an RGB par shows the frame's ``rgb`` exactly, through ``srgb``),
the least-squares sets x the intensity in linear light.

**The bytes.** Start from every channel's ``default`` (fixtures.py / ofl.py give a shutter its Open value, a fine
channel its byte of the coarse default), apply the fixture's ``pin`` by channel name, then the driven channels. The
result is always ``footprint`` bytes. Shutter, pan, tilt and other channels are never driven in Stage 5.

**Costs.** A type's channel plan and a fixture's default bytes are cached by identity (a RigView is swapped whole,
never mutated). Least-squares solves are cached on the colour quantised to ``SOLVE_QUANTUM`` (a fixture's colour moves
slowly); RGB and RGBW need no cache.

Config names live in config: nothing here branches on a fixture, type, output or channel name.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Mapping
from typing import Any, Optional, Sequence

import numpy as np
from scipy.optimize import nnls

from . import contract
from .palette import to_oklab

Rgb = tuple[float, float, float]

#: Emitter -> {"xy": CIE 1931 chromaticity, "Y": relative luminance at full drive}. Starting points (typical LED
#: datasheet values for each colour of a multi-chip LED fixture); measure. A type's ``emitters`` in fixtures.yaml (or
#: the TypeInfo's ``emitters``) overrides them. ``Y`` only matters relative to the type's other emitters (the NNLS
#: solve); red, green and blue carry sRGB's own luminance shares so a full R+G+B reads as a white.
EMITTER_DEFAULTS: dict[str, dict[str, Any]] = {
    "red": {"xy": [0.700, 0.299], "Y": 0.2126},         # ~625 nm
    "green": {"xy": [0.170, 0.700], "Y": 0.7152},       # ~525 nm
    "blue": {"xy": [0.136, 0.040], "Y": 0.0722},        # ~465 nm
    "white": {"xy": [0.3127, 0.3290], "Y": 1.0},        # cool white, ~6500 K
    "warm_white": {"xy": [0.4369, 0.4041], "Y": 0.9},   # ~3000 K
    "cold_white": {"xy": [0.3135, 0.3236], "Y": 1.0},   # ~6500 K
    "amber": {"xy": [0.575, 0.424], "Y": 0.25},         # ~590 nm
    "lime": {"xy": [0.410, 0.560], "Y": 0.6},           # phosphor-converted lime
    "cyan": {"xy": [0.040, 0.560], "Y": 0.3},           # ~500 nm
    "magenta": {"xy": [0.385, 0.160], "Y": 0.15},
    "yellow": {"xy": [0.540, 0.455], "Y": 0.5},         # ~585 nm
    "indigo": {"xy": [0.165, 0.012], "Y": 0.03},        # ~430 nm
    "uv": {"xy": [0.170, 0.005], "Y": 0.0},             # never takes part (pinned to its default)
}
PINNED_EMITTERS: frozenset[str] = frozenset({"uv"})     # README S5.5: uv never takes part
RGB_SET: frozenset[str] = frozenset({"red", "green", "blue"})
RGBW_SET: frozenset[str] = frozenset({"red", "green", "blue", "white"})
SOLVE_QUANTUM = 0.005           # README S5.5: least-squares solves cached on the colour quantised to this
SOLVE_CACHE_MAX = 4096          # entries; cleared whole when full (the cache refills from the colours in use)
RIDGE = 1e-4                    # NNLS ridge, relative to the emitter matrix's scale: unique, smooth answers; the
                                # chromaticity error it costs is ~1e-4 xy (README: within 0.005)
WHEEL_WHITE_C = 0.05            # starting point: a colour below this OKLCh chroma takes the wheel's white (Open) slot
WHEEL_MAX_HUE_DEG = 60.0        # starting point: a coloured slot further than this in hue loses to the white slot
CURVE_DEFAULT = "srgb"          # neutral: the palette's values go straight across

# linear sRGB (D65) -> CIE XYZ
_SRGB_TO_XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                         [0.2126729, 0.7151522, 0.0721750],
                         [0.0193339, 0.1191920, 0.9503041]])


# -- transfer functions -----------------------------------------------------------------------------------------------
def _c01(x: float) -> float:
    """Clamp to 0..1; NaN reads 0."""
    return 0.0 if not x > 0.0 else 1.0 if x > 1.0 else x


def srgb_to_linear(c: float) -> float:
    """sRGB transfer, decode (0..1 -> linear light 0..1)."""
    c = _c01(float(c))
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_to_srgb(c: float) -> float:
    """sRGB transfer, encode (linear light 0..1 -> 0..1)."""
    c = _c01(float(c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1.0 / 2.4) - 0.055


@functools.lru_cache(maxsize=64)
def _curve(curve: Optional[str]) -> tuple[str, float]:
    """A curve word -> (kind, exponent). Anything that doesn't read is the neutral srgb (fixtures.py reports it)."""
    s = str(curve or CURVE_DEFAULT).strip().lower()
    if s in contract.CURVES:
        return s, 1.0
    if s.startswith("gamma:"):
        try:
            g = float(s.split(":", 1)[1])
        except ValueError:
            g = math.nan
        if math.isfinite(g) and g > 0.0:
            return "gamma", 1.0 / g
    return CURVE_DEFAULT, 1.0


def curve_out(v: float, curve: str) -> float:
    """Linear light 0..1 -> a DMX fraction 0..1 through ``srgb`` | ``linear`` | ``gamma:<g>``."""
    kind, e = _curve(curve)
    v = _c01(float(v))
    if kind == "linear":
        return v
    if kind == "gamma":
        return v ** e
    return linear_to_srgb(v)


# -- a type's channel plan (cached by identity) -----------------------------------------------------------------------
class _Plan:
    __slots__ = ("type", "footprint", "intensity", "emitters", "wheel", "names", "method", "key", "A", "defaults")

    def __init__(self, t: Mapping[str, Any]) -> None:
        self.type = t
        fp = t.get("footprint")
        self.footprint = int(fp) if isinstance(fp, int) and not isinstance(fp, bool) and fp > 0 else 0
        self.intensity: list[tuple[int, Optional[int]]] = []
        self.emitters: list[tuple[int, Optional[int], str]] = []
        self.wheel: list[int] = []
        defaults = bytearray(self.footprint)
        for ch in t.get("channels") or ():
            if not isinstance(ch, Mapping):
                continue
            off = ch.get("offset")
            if not isinstance(off, int) or isinstance(off, bool) or not 0 <= off < self.footprint:
                continue
            d = ch.get("default")
            defaults[off] = max(0, min(255, int(d))) if isinstance(d, (int, float)) and not isinstance(d, bool) \
                and math.isfinite(d) else 0
            fine = ch.get("fine")
            fine = fine if isinstance(fine, int) and not isinstance(fine, bool) and 0 <= fine < self.footprint \
                and fine != off else None
            attr = ch.get("attr")
            if attr == "intensity":
                self.intensity.append((off, fine))
            elif attr == "emitter" and ch.get("emitter") in contract.EMITTERS \
                    and ch.get("emitter") not in PINNED_EMITTERS:
                self.emitters.append((off, fine, str(ch["emitter"])))
            elif attr == "wheel":
                self.wheel.append(off)
        self.defaults = bytes(defaults)
        self.names: tuple[str, ...] = tuple(sorted({e for _, _, e in self.emitters}))
        ns = frozenset(self.names)
        self.method = None if not ns else "rgb" if ns == RGB_SET else "rgbw" if ns == RGBW_SET else "nnls"
        if self.method in ("rgb", "rgbw") and _declared(t, self.names):
            # measured emitters (a warm-white W, primaries away from sRGB's): straight across or min(r, g, b) would
            # miss the chromaticity by their difference, so the solve uses them (README S5.14)
            self.method = "nnls"
        self.A: Optional[np.ndarray] = None
        self.key: tuple = ()
        if self.method == "nnls":
            chroma = _emitter_chroma(t, self.names)
            cols = []
            for e in self.names:
                (x, y), Y = chroma[e]
                cols.append((x / y * Y, Y, (1.0 - x - y) / y * Y))
            A = np.array(cols, dtype=float).T                    # 3 x k
            scale = float(np.max(np.linalg.norm(A, axis=0))) or 1.0
            k = len(self.names)
            self.A = np.vstack([A / scale, math.sqrt(RIDGE) * np.eye(k)])    # (3 + k) x k: the ridge rows
            self.key = tuple((e, *chroma[e][0], chroma[e][1]) for e in self.names)


def _override(o: Any) -> tuple[Optional[list], Optional[float]]:
    """A type's ``emitters`` entry -> (xy, Y), each None where it doesn't read."""
    if not isinstance(o, Mapping):
        return None, None
    oxy, oY = o.get("xy"), o.get("Y")
    xy = oxy if isinstance(oxy, (list, tuple)) and len(oxy) == 2 and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in oxy) \
        and 0.0 < oxy[0] < 1.0 and 0.0 < oxy[1] < 1.0 and oxy[0] + oxy[1] < 1.0 else None
    Y = float(oY) if isinstance(oY, (int, float)) and not isinstance(oY, bool) and math.isfinite(oY) and oY > 0.0 \
        else None
    return xy, Y


def _declared(t: Mapping[str, Any], names: Sequence[str]) -> bool:
    """Does the type declare (in fixtures.yaml's ``emitters``) a usable xy or Y for any of its emitters?"""
    own = t.get("emitters") if isinstance(t.get("emitters"), Mapping) else {}
    return any(_override(own.get(e)) != (None, None) for e in names)


def _emitter_chroma(t: Mapping[str, Any], names: Sequence[str]) -> dict[str, tuple[tuple[float, float], float]]:
    """Each emitter's (xy, Y): the type's ``emitters`` where they read, else EMITTER_DEFAULTS."""
    own = t.get("emitters") if isinstance(t.get("emitters"), Mapping) else {}
    out = {}
    for e in names:
        d = EMITTER_DEFAULTS.get(e, EMITTER_DEFAULTS["white"])
        xy, Y = d["xy"], d["Y"]
        oxy, oY = _override(own.get(e))
        if oxy is not None:
            xy = oxy
        if oY is not None:
            Y = oY
        out[e] = ((float(xy[0]), float(xy[1])), float(Y))
    return out


_PLANS: dict[int, _Plan] = {}


def _plan(t: Mapping[str, Any]) -> _Plan:
    p = _PLANS.get(id(t))
    if p is None or p.type is not t:
        if len(_PLANS) > 256:
            _PLANS.clear()
        p = _Plan(t)
        _PLANS[id(t)] = p
    return p


# -- the solve --------------------------------------------------------------------------------------------------------
_SOLVES: dict[tuple, tuple[tuple[float, ...], float]] = {}


def _nnls(p: _Plan, lin: Sequence[float]) -> tuple[tuple[float, ...], float]:
    """Normalised emitter levels (largest 1) for a linear-light colour, and the factor the raw solve was scaled by."""
    q = 1.0 / SOLVE_QUANTUM
    key = (p.key, round(lin[0] * q), round(lin[1] * q), round(lin[2] * q))
    hit = _SOLVES.get(key)
    if hit is not None:
        return hit
    target = _SRGB_TO_XYZ @ np.array([key[1], key[2], key[3]], dtype=float) / q
    k = len(p.names)
    x, _ = nnls(p.A, np.concatenate([target, np.zeros(k)]))   # only the direction matters: normalised below
    peak = float(x.max()) if k else 0.0
    if not peak > 1e-12:
        out: tuple[tuple[float, ...], float] = (tuple(0.0 for _ in range(k)), 1.0)
    else:
        out = (tuple(float(v) / peak for v in x), 1.0 / peak)
    if len(_SOLVES) >= SOLVE_CACHE_MAX:
        _SOLVES.clear()
    _SOLVES[key] = out
    return out


def _levels(p: _Plan, colour: Sequence[float], wb: Optional[Mapping[str, Any]]) -> tuple[dict[str, float], float]:
    lin = (srgb_to_linear(colour[0]), srgb_to_linear(colour[1]), srgb_to_linear(colour[2]))
    scale = 1.0
    if p.method == "rgb":
        lv = {"red": lin[0], "green": lin[1], "blue": lin[2]}
    elif p.method == "rgbw":
        w = min(lin)
        lv = {"red": lin[0] - w, "green": lin[1] - w, "blue": lin[2] - w, "white": w}
    elif p.method == "nnls":
        xs, scale = _nnls(p, lin)
        lv = dict(zip(p.names, xs))
    else:
        return {}, 1.0
    if wb:
        for e, v in lv.items():
            g = wb.get(e)
            if isinstance(g, (int, float)) and not isinstance(g, bool) and math.isfinite(g) and g >= 0.0:
                lv[e] = v * g
    return {e: _c01(v) for e, v in lv.items()}, scale


def solve(colour: Sequence[float], t: Mapping[str, Any], wb: Optional[Mapping[str, Any]] = None
          ) -> tuple[dict[str, float], float]:
    """``colour`` (sRGB 0..1 at full value) -> ({emitter: 0..1 linear light, wb applied}, scale): ``scale`` is the
    factor the raw least-squares solve was multiplied by to put its largest emitter at 1 (1.0 for RGB and RGBW, which
    need none). A type without emitters gives ({}, 1.0)."""
    return _levels(_plan(t), colour, wb)


# -- the wheel --------------------------------------------------------------------------------------------------------
def _lch(lab: Sequence[float]) -> tuple[float, float]:
    C = math.hypot(lab[1], lab[2])
    return C, (math.degrees(math.atan2(lab[2], lab[1])) % 360.0) if C > 1e-9 else 0.0


@functools.lru_cache(maxsize=128)
def _wheel_labs(hexes: tuple[str, ...]) -> tuple[Optional[tuple[float, float, float]], ...]:
    out = []
    for h in hexes:
        try:
            s = str(h).strip().lstrip("#")
            out.append(to_oklab((int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0, int(s[4:6], 16) / 255.0))
                       if len(s) == 6 else None)
        except ValueError:
            out.append(None)
    return tuple(out)


def wheel_slot(colour: Sequence[float], slots: Sequence[Mapping[str, Any]]) -> int:
    """The DMX value of the wheel slot nearest ``colour`` in OKLab, white (Open) slots included; the white slot also
    takes pale colours (chroma under WHEEL_WHITE_C) and colours whose nearest slot is a coloured one more than
    WHEEL_MAX_HUE_DEG away in hue. 0 for an empty wheel."""
    good = [s for s in slots or () if isinstance(s, Mapping) and isinstance(s.get("dmx"), int)]
    if not good:
        return 0
    labs = _wheel_labs(tuple(str(s.get("hex") or "") for s in good))
    lab = to_oklab((_c01(colour[0]), _c01(colour[1]), _c01(colour[2])))
    c, h = _lch(lab)
    whites, colours = [], []
    for s, sl in zip(good, labs):
        if sl is None:
            continue
        sc, sh = _lch(sl)
        d = math.dist(lab, sl)
        (whites if sc < WHEEL_WHITE_C else colours).append((d, sh, int(s["dmx"])))
    if not whites and not colours:
        return int(good[0]["dmx"])
    if c < WHEEL_WHITE_C and whites:
        return min(whites)[2]
    best = min(whites + colours)                             # the nearest slot of all, whites included (S5.5)
    if whites and best in colours:
        dh = abs((best[1] - h + 180.0) % 360.0 - 180.0)
        if dh > WHEEL_MAX_HUE_DEG:
            return min(whites)[2]
    return best[2]


# -- the bytes --------------------------------------------------------------------------------------------------------
_TEMPLATES: dict[int, tuple[Any, Any, bytes]] = {}


def _template(f: Mapping[str, Any], t: Mapping[str, Any], p: _Plan) -> bytes:
    """The type's defaults with the fixture's pins applied (cached by the fixture's and the type's identity)."""
    hit = _TEMPLATES.get(id(f))
    if hit is not None and hit[0] is f and hit[1] is t:
        return hit[2]
    buf = bytearray(p.defaults)
    pins = f.get("pin") if isinstance(f.get("pin"), dict) else {}
    if pins:
        for ch in t.get("channels") or ():
            if isinstance(ch, Mapping) and ch.get("name") in pins:
                off, v = ch.get("offset"), pins[ch["name"]]
                if isinstance(off, int) and 0 <= off < p.footprint and isinstance(v, (int, float)) \
                        and not isinstance(v, bool) and math.isfinite(v):
                    buf[off] = max(0, min(255, int(round(v))))
    out = bytes(buf)
    if len(_TEMPLATES) > 1024:
        _TEMPLATES.clear()
    _TEMPLATES[id(f)] = (f, t, out)
    return out


def _put(buf: bytearray, off: int, fine: Optional[int], v: float) -> None:
    v = _c01(v)
    if fine is None:
        buf[off] = int(round(v * 255.0))
    else:
        w = int(round(v * 65535.0))
        buf[off] = w >> 8
        buf[fine] = w & 0xFF


_COLOURS: dict[int, tuple] = {}


def _colour_part(f: Mapping[str, Any], t: Mapping[str, Any], p: _Plan, col: Rgb) -> tuple[bytes, dict[str, float]]:
    """The bytes that depend only on the colour (the template, then the emitters at full colour when a dimmer carries
    the intensity, and the wheel), with the emitter levels at full colour; cached per fixture on the exact colour (a
    fixture's colour holds between the director's ticks)."""
    hit = _COLOURS.get(id(f))
    if hit is not None and hit[0] is f and hit[1] is t and hit[2] == col:
        return hit[3], hit[4]
    buf = bytearray(_template(f, t, p))
    lv: dict[str, float] = {}
    if p.emitters:
        wb = f.get("wb")
        lv, _ = _levels(p, col, wb if isinstance(wb, dict) and wb else None)
        if p.intensity:                                          # full colour; the dimmer carries the intensity
            curve = f.get("curve") or t.get("curve") or CURVE_DEFAULT
            for off, fine, e in p.emitters:
                _put(buf, off, fine, curve_out(lv.get(e, 0.0), curve))
    if p.wheel:
        v = wheel_slot(col, t.get("wheel") or ())
        for off in p.wheel:
            buf[off] = max(0, min(255, v))
    out = (bytes(buf), lv)
    if len(_COLOURS) > 1024:
        _COLOURS.clear()
    _COLOURS[id(f)] = (f, t, col, out[0], lv)
    return out


def encode(f: Mapping[str, Any], t: Mapping[str, Any], intensity: float, colour: Sequence[float]) -> bytes:
    """One fixture's ``footprint`` bytes for ``intensity`` (0..1) and ``colour`` (sRGB at full value)."""
    p = _plan(t)
    if not p.footprint:
        return b""
    i = _c01(float(intensity))
    col = (_c01(float(colour[0])), _c01(float(colour[1])), _c01(float(colour[2])))
    base, lv = _colour_part(f, t, p, col)
    if not p.intensity and not p.emitters:
        return base
    buf = bytearray(base)
    for off, fine in p.intensity:
        _put(buf, off, fine, i)
    if p.emitters and not p.intensity:                           # no dimmer: the emitters carry the intensity
        if p.method in ("rgb", "rgbw"):
            wb = f.get("wb")
            lv, _ = _levels(p, (col[0] * i, col[1] * i, col[2] * i), wb if isinstance(wb, dict) and wb else None)
        else:
            k = srgb_to_linear(i)                                # the intensity in linear light
            lv = {e: v * k for e, v in lv.items()}
        curve = f.get("curve") or t.get("curve") or CURVE_DEFAULT
        for off, fine, e in p.emitters:
            _put(buf, off, fine, curve_out(lv.get(e, 0.0), curve))
    return bytes(buf)
