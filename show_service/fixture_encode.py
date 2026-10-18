"""fixture_encode: a fixture's attributes to its DMX bytes, and what each fixture can do (docs/LIGHTS.md §2.3, §2.4).
Pure: no threads, no network, no I/O. The encode builder's.

**Attributes** are contract.ATTRS words in their normalised units (pan/tilt centred degrees, zoom beam degrees, spins
-1..1 with + clockwise, slots 1-based, shutter ``{mode, hz}``). Every coarse channel of a type lists its ``ranges``
(contract.CapRange: ofl.parse, fixtures.GENERIC_RANGES); this module turns the attrs a frame asks for into a value
inside one range per channel.

**encode(f, t, intensity, colour, attrs)**: ``attrs`` None, or holding only ``intensity`` and ``colour``, is exactly
colour_mix.encode's bytes (the frame's intensity and colour always come from the arguments). Otherwise, from those
bytes (template, pins, emitters, dimmer, the colour wheel's nearest slot):
  1. per channel, the attrs it serves are tried in ATTR_PRIORITY; the first whose value is active (spin != 0,
     shake > 0, prism in, a slot set, shutter not open) **and** has a matching range on the channel wins;
  2. then the continuous attrs (CONTINUOUS: pan, tilt, zoom, focus, iris, frost, cct, pt_speed);
  3. then, on a channel that serves one attr only and isn't pinned, that attr's explicit resting value (a spin of 0
     the stop range, a shutter asked open the start of its first open range, prism 0 its out slot);
  4. a channel none of them claims keeps colour_mix's byte (its default, or its pin).
Values: continuous attrs are linear inside the range that holds them, clamped to the nearest end of all of them (pan
and tilt to the fixture's own range; the owner's soft limits are the engine's, §5.5); slots go to the middle of their
range; a shake picks the range for the gobo's slot (else the wheel-wide one) and its speed; a signed spin picks a
range of its sign; a shutter the range of its mode (pulse, random, ramp and burst fall back to strobe) with ``hz``
interpolated, "open" and "closed" at the start of theirs. A 16-bit value goes to (coarse, fine) MSB first; a mode
without the fine channel gets the coarse byte only. ``macro`` ({channel name: range word}) sends that range's start
(the page's "More"; last, so it wins on its channel).

**Per-light setup** (FixtureInfo.setup): ``range`` rescales pan or tilt when the type doesn't give it in degrees
(``physical`` None: a percent angle, a generic type); ``slots.count`` re-divides a generic wheel
(fixtures.generic_type); colour ``hex`` names make the colour wheel's nearest slot use those colours.

**caps(t, setup)** is contract.FixtureCaps: everything the Lights page and the engine read about a fixture, built from
ranges and wheels alone (never a type or channel name). **neutral(caps, setup)** is every attr at rest: home, the
default beam (setup ``beam``, else zoom mid-range, focus 0.5, iris 1, frost 0), slots unset, spins 0, shutter open.

**Costs.** A type's plan is cached by identity (as colour_mix's), a light's own (setup-adjusted) plan by the
fixture's identity; a frame with only movement changes does no colour solve (colour_mix's per-fixture cache).

Config names live in config: nothing here branches on a fixture, type, channel or output name.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Optional, Sequence

from . import colour_mix, contract

ATTR_PRIORITY: tuple[str, ...] = ("shutter", "gobo_shake", "gobo2_shake", "gobo_spin", "gobo2_spin", "gobo_scroll",
                                  "colour_spin", "prism_spin", "prism", "gobo", "gobo2", "colour_slot", "pan_spin",
                                  "tilt_spin")
CONTINUOUS: tuple[str, ...] = ("pan", "tilt", "zoom", "focus", "iris", "frost", "cct", "pt_speed")
SPINS: tuple[str, ...] = ("colour_spin", "gobo_spin", "gobo2_spin", "gobo_scroll", "prism_spin", "pan_spin",
                          "tilt_spin")
SHAKES: tuple[str, ...] = ("gobo_shake", "gobo2_shake")
SLOT_ATTRS: tuple[str, ...] = ("colour_slot", "gobo", "gobo2")
STROBE_LIKE: tuple[str, ...] = ("pulse", "random", "ramp", "burst")   # no range of their own: strobe
NEUTRAL_BEAM: dict[str, float] = {"focus": 0.5, "iris": 1.0, "frost": 0.0}   # §2.1 "when nothing sets it"
NEUTRAL_PT_SPEED = 1.0              # the fixture's fastest: the engine smooths (show.yaml lights.motion.pt_speed wins)
_COLOUR_ATTRS = frozenset({"intensity", "colour", "colour_slot"})   # what colour_mix already sends
_RESTING = frozenset(SPINS) | {"shutter", "prism"}                 # attrs with an explicit resting value (step 3)


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def _round(x: float) -> int:
    return int(math.floor(x + 0.5))


def _span(r: Mapping[str, Any]) -> tuple[float, float]:
    lo, hi = float(r["lo"]), float(r["hi"])
    return (lo, hi) if lo <= hi else (hi, lo)


def to_dmx(r: Mapping[str, Any], value: float) -> int:
    """``value`` (the range's attr unit) -> a full-resolution DMX value inside ``r``: linear between its ends, clamped
    to them; a stepped range (lo == hi) gives the middle of its DMX span."""
    d0, d1 = int(r["dmx"][0]), int(r["dmx"][1])
    lo, hi = float(r["lo"]), float(r["hi"])
    if hi == lo:
        return (d0 + d1) // 2
    x = (float(value) - lo) / (hi - lo)
    x = 0.0 if not x > 0.0 else 1.0 if x > 1.0 else x
    return _round(d0 + x * (d1 - d0))


# -- a type's plan (cached by identity) --------------------------------------------------------------------------------
class _Chan:
    __slots__ = ("off", "fine", "res", "name", "ranges", "by_attr", "only", "prio", "cont", "keys", "memo", "fast")

    def __init__(self, ch: Mapping[str, Any], footprint: int, scale: Mapping[str, float]) -> None:
        self.off = int(ch["offset"])
        fine = ch.get("fine")
        self.fine = fine if isinstance(fine, int) and not isinstance(fine, bool) and 0 <= fine < footprint \
            and fine != self.off else None
        res = ch.get("res")
        self.res = int(res) if isinstance(res, int) and not isinstance(res, bool) and 1 <= res <= 4 else 1
        self.name = str(ch.get("name"))
        top = 256 ** self.res - 1
        self.ranges: list[dict] = []
        for r in ch.get("ranges") or ():
            if not isinstance(r, Mapping) or not isinstance(r.get("attr"), str):
                continue
            dmx = r.get("dmx")
            lo, hi = _num(r.get("lo")), _num(r.get("hi"))
            if not (isinstance(dmx, (list, tuple)) and len(dmx) == 2 and all(isinstance(x, int) for x in dmx)
                    and 0 <= dmx[0] <= dmx[1] <= top) or lo is None or hi is None:
                continue
            k = scale.get(r["attr"], 1.0)
            self.ranges.append({**r, "dmx": [int(dmx[0]), int(dmx[1])], "lo": lo * k, "hi": hi * k})
        self.by_attr: dict[str, list[dict]] = {}
        for r in self.ranges:
            self.by_attr.setdefault(r["attr"], []).append(r)
        real = {a for a in self.by_attr if a != "macro"}
        self.only: Optional[str] = next(iter(real)) if len(real) == 1 else None
        self.prio = tuple(a for a in ATTR_PRIORITY if a in self.by_attr)    # encode's steps 1 and 2, this channel's
        self.cont = tuple(a for a in CONTINUOUS if a in self.by_attr)
        # a channel with no continuous attr is memoised on the values it reads (they rarely change between frames)
        shake_slots = tuple(a[:-len("_shake")] for a in self.prio if a in SHAKES)
        self.keys: Optional[tuple[str, ...]] = None if self.cont else tuple(dict.fromkeys(self.prio + shake_slots))
        self.memo: dict[tuple, Optional[int]] = {}
        # one continuous attr over one sloped range (pan, tilt, zoom, focus, speed): (attr, dmx lo, dmx hi, lo, hi)
        self.fast: Optional[tuple[str, int, int, float, float]] = None
        if not self.prio and len(self.cont) == 1 and len(self.by_attr[self.cont[0]]) == 1:
            r = self.by_attr[self.cont[0]][0]
            if r["lo"] != r["hi"]:
                self.fast = (self.cont[0], r["dmx"][0], r["dmx"][1], r["lo"], r["hi"])


class EncodePlan:
    """One type's channels with ranges, ready for ``encode`` (``plan``)."""
    __slots__ = ("type", "footprint", "chans", "by_name", "attrs", "enc", "scale", "driven")

    def __init__(self, t: Mapping[str, Any], scale: Optional[Mapping[str, float]] = None) -> None:
        self.type = t
        fp = t.get("footprint")
        self.footprint = int(fp) if isinstance(fp, int) and not isinstance(fp, bool) and fp > 0 else 0
        self.scale = dict(scale or {})
        self.chans: list[_Chan] = []
        for ch in t.get("channels") or ():
            if isinstance(ch, Mapping) and ch.get("ranges") and isinstance(ch.get("offset"), int) \
                    and 0 <= ch["offset"] < self.footprint:
                c = _Chan(ch, self.footprint, self.scale)
                if c.ranges:
                    self.chans.append(c)
        self.by_name = {c.name: c for c in self.chans}
        #: the channels encode's steps 1-3 can claim (an intensity, emitter or macro-only channel never is)
        self.driven = [c for c in self.chans if c.prio or c.cont or c.only in _RESTING]
        self.attrs = frozenset(a for c in self.chans for a in c.by_attr)
        #: the engine's _Slot.enc: a range beyond what colour_mix sends (intensity, colour, the wheel's nearest slot)
        self.enc = bool(self.attrs - _COLOUR_ATTRS)


_PLANS: dict[int, EncodePlan] = {}


def plan(t: Mapping[str, Any]) -> EncodePlan:
    """The type's plan, cached by identity (a RigView is swapped whole, never mutated)."""
    p = _PLANS.get(id(t))
    if p is None or p.type is not t:
        if len(_PLANS) > 256:
            _PLANS.clear()
        p = EncodePlan(t)
        _PLANS[id(t)] = p
    return p


def enc(t: Mapping[str, Any]) -> bool:
    """Does this type have anything for ``encode``'s attrs to drive (pan, tilt, gobo, shutter, ...)?"""
    return plan(t).enc


def _setup_key(t: Mapping[str, Any], setup: Optional[Mapping[str, Any]]) -> tuple:
    """What of a light's setup changes its plan: generic wheel counts, and a pan/tilt range the type doesn't give."""
    if not setup:
        return ()
    slots = setup.get("slots") if isinstance(setup.get("slots"), Mapping) else {}
    counts = slots.get("count") if isinstance(slots.get("count"), Mapping) else {}
    rng = setup.get("range") if isinstance(setup.get("range"), Mapping) else {}
    phys = t.get("physical") if isinstance(t.get("physical"), Mapping) else {}
    r = tuple((a, float(rng[a])) for a in ("pan", "tilt") if _num(rng.get(a)) is not None and rng[a] > 0
              and phys.get(f"{a}_deg") is None)
    c = tuple(sorted((str(k), int(v)) for k, v in counts.items() if _num(v) is not None)) \
        if t.get("source") == "builtin" else ()
    return (c, r) if c or r else ()


_SETUP_PLANS: dict[tuple, tuple[Mapping[str, Any], Mapping[str, Any], EncodePlan]] = {}


def _effective(t: Mapping[str, Any], setup: Optional[Mapping[str, Any]]) -> tuple[Mapping[str, Any], EncodePlan]:
    """(the type as this light has it, its plan): ``plan(t)`` unless its setup re-divides a generic wheel
    (fixtures.generic_type) or gives the pan/tilt range the type doesn't."""
    key = _setup_key(t, setup)
    if not key:
        return t, plan(t)
    ck = (id(t),) + key
    hit = _SETUP_PLANS.get(ck)
    if hit is not None and hit[0] is t:
        return hit[1], hit[2]
    counts, rng = key
    te: Mapping[str, Any] = t
    if counts:
        try:
            from . import fixtures
            te = fixtures.generic_type(str(t.get("id")), dict(counts)) or t
        except Exception:                                    # noqa: BLE001  degrade: the type's own wheels
            te = t
    scale: dict[str, float] = {}
    for a, deg in rng:
        span = [r for c in plan(te).chans for r in c.by_attr.get(a, ())]
        if span:
            width = max(max(_span(r)) for r in span) - min(min(_span(r)) for r in span)
            if width > 0:
                scale[a] = deg / width
    p = EncodePlan(te, scale) if scale else plan(te)
    if len(_SETUP_PLANS) > 512:
        _SETUP_PLANS.clear()
    _SETUP_PLANS[ck] = (t, te, p)
    return te, p


_FIXTURE_PLANS: dict[int, tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], EncodePlan,
                               Optional[list]]] = {}


def _fixture(f: Mapping[str, Any], t: Mapping[str, Any]) -> tuple[EncodePlan, Optional[list]]:
    """(the light's plan, its colour-wheel slots with setup colours or None), cached by the fixture's identity."""
    hit = _FIXTURE_PLANS.get(id(f))
    if hit is not None and hit[0] is f and hit[1] is t:
        return hit[3], hit[4]
    setup = f.get("setup") if isinstance(f.get("setup"), Mapping) else None
    te, p = _effective(t, setup)
    wheel = _setup_wheel(te, p, setup)
    if len(_FIXTURE_PLANS) > 1024:
        _FIXTURE_PLANS.clear()
    _FIXTURE_PLANS[id(f)] = (f, t, te, p, wheel)
    return p, wheel


def _colour_wheel(te: Mapping[str, Any]) -> Optional[dict]:
    for w in (te.get("wheels") or {}).values():
        if isinstance(w, Mapping) and w.get("attr") == "colour_slot":
            return dict(w)
    return None


def _setup_wheel(te: Mapping[str, Any], p: EncodePlan, setup: Optional[Mapping[str, Any]]) -> Optional[list]:
    """The colour wheel's slots for the nearest-colour pick when the light's setup names any slot's colour:
    [{dmx: full resolution, hex}]; None otherwise (colour_mix's own pick stands)."""
    own = ((setup or {}).get("slots") or {}).get("colour") if isinstance((setup or {}).get("slots"), Mapping) else None
    if not isinstance(own, Mapping) or not any(isinstance(v, Mapping) and v.get("hex") for v in own.values()):
        return None
    w = _colour_wheel(te)
    hexes = {int(s["slot"]): s.get("hex") for s in (w or {}).get("slots") or () if isinstance(s, Mapping)}
    for k, v in own.items():
        if isinstance(v, Mapping) and v.get("hex") and str(k).isdigit():
            hexes[int(k)] = v["hex"]
    out = []
    for c in p.chans:
        for r in c.by_attr.get("colour_slot", ()):
            hx = hexes.get(int(r.get("slot") or 0))
            if hx:
                out.append({"dmx": to_dmx(r, r["lo"]), "hex": hx, "chan": c})
    return out or None


# -- choosing a range --------------------------------------------------------------------------------------------------
def _shutter(v: Any) -> tuple[Optional[str], Optional[float]]:
    if isinstance(v, str):
        return (v if v in contract.SHUTTER_MODES else None), None
    if isinstance(v, Mapping):
        m = v.get("mode")
        return (m if m in contract.SHUTTER_MODES else None), _num(v.get("hz"))
    return None, None


def _active(a: str, v: Any) -> bool:
    if v is None:
        return False
    if a == "shutter":
        m, _ = _shutter(v)
        return m is not None and m != "open"
    if a in SPINS:
        n = _num(v)
        return n is not None and n != 0.0
    if a in SHAKES:
        n = _num(v)
        return n is not None and n > 0.0
    if a == "prism":
        return v is True or ((_num(v) or 0.0) > 0.0)
    return _num(v) is not None                                  # a slot


def _holding(rs: Sequence[dict], v: float) -> Optional[dict]:
    """The first range whose [lo, hi] holds ``v`` (a sloped one before a stepped one); else the nearest end's."""
    best, dist = None, math.inf
    for r in rs:
        lo, hi = _span(r)
        if lo < hi and lo <= v <= hi:
            return r
    for r in rs:
        lo, hi = _span(r)
        if lo <= v <= hi:
            return r
        d = min(abs(v - lo), abs(v - hi))
        if d < dist:
            best, dist = r, d
    return best


def _continuous(rs: Sequence[dict], v: float) -> Optional[int]:
    r = _holding(rs, v)
    return to_dmx(r, v) if r is not None else None


def _slot(rs: Sequence[dict], n: int) -> Optional[int]:
    for r in rs:
        if r.get("slot") == n:
            return (r["dmx"][0] + r["dmx"][1]) // 2
    return None


def _spin(rs: Sequence[dict], v: float) -> Optional[int]:
    signed = [r for r in rs if (min(r["lo"], r["hi"]) >= 0.0 if v > 0 else max(r["lo"], r["hi"]) <= 0.0)
              and not (r["lo"] == 0.0 and r["hi"] == 0.0)]
    return _continuous(signed, v) if signed else None


def _stop(rs: Sequence[dict]) -> Optional[int]:
    """A spin's stop: the middle of a range at 0, else the point 0 inside a sloped one."""
    for r in rs:
        if r["lo"] == 0.0 and r["hi"] == 0.0:
            return (r["dmx"][0] + r["dmx"][1]) // 2
    for r in rs:
        lo, hi = _span(r)
        if lo <= 0.0 <= hi:
            return to_dmx(r, 0.0)
    return None


def _shutter_value(rs: Sequence[dict], mode: str, hz: Optional[float]) -> Optional[int]:
    mine = [r for r in rs if r.get("mode") == mode]
    if not mine and mode in STROBE_LIKE:
        mine = [r for r in rs if r.get("mode") == "strobe"]
    if not mine:
        return None
    if mode in ("open", "closed"):
        return int(mine[0]["dmx"][0])
    if hz is None:
        r = mine[0]
        return (r["dmx"][0] + r["dmx"][1]) // 2
    return _continuous(mine, hz)


def _shake(c: _Chan, a: str, v: float, attrs: Mapping[str, Any]) -> Optional[int]:
    rs = c.by_attr[a]
    n = _num(attrs.get(a[:-len("_shake")]))
    if n is not None:
        mine = [r for r in rs if r.get("slot") == int(n)]
        if not mine:
            mine = [r for r in rs if "slot" not in r]
    else:
        mine = [r for r in rs if "slot" not in r]
    return _continuous(mine, min(1.0, v)) if mine else None


def _prism(c: _Chan, v: Any) -> Optional[int]:
    n = 1 if v is True else max(1, _round(_num(v) or 1.0))
    rs = c.by_attr.get("prism", [])
    got = _slot(rs, n)
    if got is None:
        lower = sorted((r for r in rs if 1 <= (r.get("slot") or 0) <= n), key=lambda r: -(r.get("slot") or 0))
        upper = sorted((r for r in rs if (r.get("slot") or 0) >= 1), key=lambda r: r.get("slot") or 0)
        pick = (lower or upper or [None])[0]
        got = (pick["dmx"][0] + pick["dmx"][1]) // 2 if pick is not None else None
    if got is None and c.by_attr.get("prism_spin"):                 # in, not turning
        got = _stop(c.by_attr["prism_spin"])
        if got is None:
            got = _continuous(c.by_attr["prism_spin"], 0.0)
    return got


def _value(c: _Chan, a: str, v: Any, attrs: Mapping[str, Any]) -> Optional[int]:
    rs = c.by_attr[a]
    if a == "shutter":
        mode, hz = _shutter(v)
        return _shutter_value(rs, mode, hz) if mode else None
    if a in SHAKES:
        return _shake(c, a, float(_num(v) or 0.0), attrs)
    if a in SPINS:
        return _spin(rs, max(-1.0, min(1.0, float(_num(v) or 0.0))))
    if a == "prism":
        return _prism(c, v)
    n = _num(v)
    return _slot(rs, int(n)) if n is not None else None


def _neutral_value(c: _Chan, a: str, v: Any) -> Optional[int]:
    """A dedicated channel's explicit resting value (encode step 3)."""
    rs = c.by_attr[a]
    if a == "shutter":
        mode, _ = _shutter(v)
        return _shutter_value(rs, "open", None) if mode == "open" else None
    if a in SPINS and _num(v) == 0.0:
        return _stop(rs)
    if a == "prism" and (v is False or _num(v) == 0.0):
        return _slot(rs, 0)
    return None


def _choose(c: _Chan, attrs: Mapping[str, Any], pinned: bool = False) -> Optional[int]:
    for a in c.prio:
        v = attrs.get(a)
        if v is not None and _active(a, v):
            d = _value(c, a, v, attrs)
            if d is not None:
                return d
    for a in c.cont:
        n = _num(attrs.get(a))
        if n is not None:
            d = _continuous(c.by_attr[a], n)
            if d is not None:
                return d
    if c.only is not None and not pinned and attrs.get(c.only) is not None:
        return _neutral_value(c, c.only, attrs[c.only])               # a pin is the owner's resting value: kept
    return None


def _write(buf: bytearray, c: _Chan, value: int) -> None:
    """A full-resolution value: its coarse byte, and its fine byte when the mode carries it (MSB first)."""
    res = c.res
    value = 0 if value < 0 else min(int(value), 256 ** res - 1)
    buf[c.off] = (value >> (8 * (res - 1))) & 0xFF
    if c.fine is not None and res >= 2:
        buf[c.fine] = (value >> (8 * (res - 2))) & 0xFF


_SCALARS = (type(None), int, float, str, bool)


def _key(v: Any) -> Any:
    """A hashable stand-in for an attr value (the memo's key)."""
    if type(v) in _SCALARS:
        return v
    if isinstance(v, Mapping):
        return tuple(sorted((str(k), _key(x)) for k, x in v.items()))
    if isinstance(v, (list, tuple)):
        return tuple(_key(x) for x in v)
    return v


def _choose_memo(c: _Chan, attrs: Mapping[str, Any], pinned: bool) -> Optional[int]:
    fast = c.fast
    if fast is not None:                                         # the hot path: pan and tilt every frame
        v = attrs.get(fast[0])
        if type(v) is float or type(v) is int:                   # (a bool's type is bool)
            if not math.isfinite(v):
                return None
            x = (v - fast[3]) / (fast[4] - fast[3])
            x = 0.0 if x < 0.0 else 1.0 if x > 1.0 else x
            return int(math.floor(fast[1] + x * (fast[2] - fast[1]) + 0.5))
        return None if v is None else _choose(c, attrs, pinned)
    if c.keys is None:
        return _choose(c, attrs, pinned)
    keys = c.keys
    if len(keys) == 1:
        k: tuple = (pinned, _key(attrs.get(keys[0])))
    else:
        k = (pinned,) + tuple([_key(attrs.get(a)) for a in keys])
    try:
        return c.memo[k]
    except KeyError:
        pass
    except TypeError:                                            # an unhashable value: no memo
        return _choose(c, attrs, pinned)
    d = _choose(c, attrs, pinned)
    if len(c.memo) > 64:
        c.memo.clear()
    c.memo[k] = d
    return d


def _macro(buf: bytearray, p: EncodePlan, m: Any) -> None:
    if not isinstance(m, Mapping):
        return
    for name, word in m.items():
        c = p.by_name.get(name)
        if c is None:
            continue
        if isinstance(word, str):
            w = word.strip().lower()
            r = next((r for r in c.ranges if str(r.get("word") or "").strip().lower() == w), None)
            if r is not None:
                _write(buf, c, int(r["dmx"][0]))
        elif isinstance(word, int) and not isinstance(word, bool) and 0 <= word <= 255:
            buf[c.off] = word                                   # a raw coarse value


# -- the bytes ---------------------------------------------------------------------------------------------------------
def encode(f: Mapping[str, Any], t: Mapping[str, Any], intensity: float, colour: Sequence[float],
           attrs: Optional[Mapping[str, Any]] = None) -> bytes:
    """One fixture's ``footprint`` bytes for ``intensity``, ``colour`` (sRGB at full value) and ``attrs`` (module
    docstring). Never raises on a bad value: it is skipped."""
    base = colour_mix.encode(f, t, intensity, colour)
    if not attrs or not base or all(k in ("intensity", "colour") for k in attrs):
        return base
    p, wheel = _fixture(f, t)
    if not p.driven and not attrs.get("macro"):
        return base
    buf = bytearray(base)
    pins = f.get("pin") or {}
    for c in p.driven:
        try:
            d = _choose_memo(c, attrs, c.name in pins)
        except (TypeError, ValueError, KeyError):                # a value of the wrong shape: skipped
            d = None
        if d is not None:
            _write(buf, c, d)
        elif wheel and "colour_slot" in c.by_attr and attrs.get("colour_slot") is None:
            col = (colour_mix._c01(float(colour[0])), colour_mix._c01(float(colour[1])),
                   colour_mix._c01(float(colour[2])))
            mine = [s for s in wheel if s["chan"] is c]
            if mine:
                _write(buf, c, colour_mix.wheel_slot(col, mine))
    if attrs.get("macro"):
        _macro(buf, p, attrs.get("macro"))
    return bytes(buf)


# -- capabilities ------------------------------------------------------------------------------------------------------
def _setup_names(setup: Optional[Mapping[str, Any]], wheel: str) -> dict[int, Any]:
    slots = (setup or {}).get("slots") if isinstance((setup or {}).get("slots"), Mapping) else {}
    own = slots.get(wheel) if isinstance(slots.get(wheel), Mapping) else {}
    return {int(k): v for k, v in own.items() if str(k).isdigit()}


def caps(t: Mapping[str, Any], setup: Optional[Mapping[str, Any]] = None) -> contract.FixtureCaps:
    """What a fixture of type ``t`` with ``setup`` can show (contract.FixtureCaps)."""
    te, p = _effective(t, setup)
    setup = setup or {}
    by = {a: [r for c in p.chans for r in c.by_attr.get(a, ())] for a in p.attrs}
    chan_of = {a: next((c for c in p.chans if a in c.by_attr), None) for a in p.attrs}
    attrs = [a for a in contract.ATTRS if a in set(te.get("attrs") or ()) | p.attrs]
    limits = setup.get("limits") if isinstance(setup.get("limits"), Mapping) else {}

    def axis(a: str) -> Optional[dict]:
        rs = by.get(a)
        if not rs:
            return None
        lim = limits.get(a)
        return {"min": min(min(_span(r)) for r in rs), "max": max(max(_span(r)) for r in rs),
                "fine": bool(chan_of[a] is not None and chan_of[a].fine is not None),
                "limits": [float(lim[0]), float(lim[1])] if isinstance(lim, (list, tuple)) and len(lim) == 2 else None}
    zoom = None
    if by.get("zoom"):
        phys = te.get("physical") if isinstance(te.get("physical"), Mapping) else {}
        zoom = {"min": min(min(_span(r)) for r in by["zoom"]), "max": max(max(_span(r)) for r in by["zoom"]),
                "known": phys.get("zoom_deg") is not None}
    emitters = any(isinstance(c, Mapping) and c.get("attr") == "emitter" and c.get("emitter") != "uv"
                   for c in te.get("channels") or ())
    wheels = {w.get("attr"): w for w in (te.get("wheels") or {}).values() if isinstance(w, Mapping)}

    def slot_rows(attr: str, setup_wheel: str) -> list[dict]:
        rs = by.get(attr) or []
        have = {r.get("slot") for r in rs}
        names = _setup_names(setup, setup_wheel)
        w = wheels.get(attr) or {}
        rows = []
        for s in w.get("slots") or ():
            n = s.get("slot")
            if n not in have:
                continue
            own = names.get(n)
            row = {"slot": n, "name": own if isinstance(own, str) else s.get("name") or f"Slot {n}"}
            if attr == "colour_slot":
                o = own if isinstance(own, Mapping) else {}
                row = {"slot": n, "name": o.get("name") or s.get("name") or f"Colour {n}",
                       "hex": o.get("hex") or s.get("hex")}
            else:
                row.update(shake=any(r.get("slot") in (n, None) for r in by.get(f"{attr}_shake", ())),
                           open=s.get("type") == "Open")
            rows.append(row)
        if not rows:                                            # ranges with no wheel listed (a hand-built type)
            for n in sorted(x for x in have if isinstance(x, int) and x >= 1):
                own = names.get(n)
                if attr == "colour_slot":
                    o = own if isinstance(own, Mapping) else {}
                    rows.append({"slot": n, "name": o.get("name") or f"Colour {n}", "hex": o.get("hex")})
                else:
                    rows.append({"slot": n, "name": own if isinstance(own, str) else f"Slot {n}",
                                 "shake": any(r.get("slot") in (n, None) for r in by.get(f"{attr}_shake", ())),
                                 "open": False})
        return rows
    prism_rows = []
    pw = wheels.get("prism")
    pnames = _setup_names(setup, "prism")
    pslots = sorted({r.get("slot") for r in by.get("prism", []) + by.get("prism_spin", [])
                     if isinstance(r.get("slot"), int) and r.get("slot") >= 1})
    listed = [s for s in (pw or {}).get("slots") or () if s.get("type") == "Prism"]
    for k in pslots:
        s = listed[k - 1] if k - 1 < len(listed) else {}
        own = pnames.get(k)
        prism_rows.append({"slot": k, "name": own if isinstance(own, str) else s.get("name") or "Prism",
                           "facets": s.get("facets")})
    sh = by.get("shutter") or []
    modes = [m for m in contract.SHUTTER_MODES if any(r.get("mode") == m for r in sh)]
    strobes = [r for r in sh if r.get("mode") in ("strobe",) + STROBE_LIKE and max(r["lo"], r["hi"]) > 0]
    strobe_hz = [min(min(_span(r)) for r in strobes), max(max(_span(r)) for r in strobes)] if strobes else None
    cap_hz = _num(setup.get("strobe_hz_max"))
    if strobe_hz is not None and cap_hz is not None and cap_hz > 0:
        strobe_hz = [min(strobe_hz[0], cap_hz), min(strobe_hz[1], cap_hz)]
    macros = []
    for c in p.chans:
        ms = c.by_attr.get("macro")
        if ms:
            macros.append({"channel": c.name, "name": c.name,
                           "choices": [{"word": str(r.get("word") or f"{r['dmx'][0]}-{r['dmx'][1]}"),
                                        "dmx": int(r["dmx"][0])} for r in ms]})
    cct = by.get("cct")
    return {"attrs": attrs, "pan": axis("pan"), "tilt": axis("tilt"), "pt_speed": "pt_speed" in p.attrs,
            "zoom": zoom, "focus": "focus" in p.attrs, "iris": "iris" in p.attrs, "frost": "frost" in p.attrs,
            "colour": "mix" if emitters else "wheel" if "colour_slot" in p.attrs else "none",
            "colour_slots": slot_rows("colour_slot", "colour"), "gobo_slots": slot_rows("gobo", "gobo"),
            "gobo2_slots": slot_rows("gobo2", "gobo2"), "prism_slots": prism_rows,
            "spins": [a for a in SPINS if a in p.attrs],
            "cct": {"min_k": min(min(_span(r)) for r in cct), "max_k": max(max(_span(r)) for r in cct)} if cct
            else None,
            "shutter": modes, "strobe_hz": strobe_hz, "macros": macros,
            "moves": "pan" in p.attrs and "tilt" in p.attrs}


def neutral(caps: Mapping[str, Any], setup: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Every attr this fixture has at its resting value (docs/LIGHTS.md §2.1 "when nothing sets it"): pan/tilt at
    setup ``home`` (else 0, clamped to the range), the beam at setup ``beam`` (else zoom mid-range, focus 0.5, iris 1,
    frost 0), slots unset (the frame's colour picks the colour wheel's), spins and shakes 0, prism out, shutter open,
    pt_speed the fastest. Intensity, colour and macro are the frame's, never here."""
    setup = setup or {}
    attrs = set(caps.get("attrs") or ())
    home = setup.get("home") if isinstance(setup.get("home"), Mapping) else {}
    beam = setup.get("beam") if isinstance(setup.get("beam"), Mapping) else {}
    out: dict[str, Any] = {}
    for a in ("pan", "tilt"):
        ax = caps.get(a)
        if a in attrs and isinstance(ax, Mapping):
            v = _num(home.get(a))
            v = 0.0 if v is None else v
            out[a] = max(float(ax["min"]), min(float(ax["max"]), v))
    if "zoom" in attrs and isinstance(caps.get("zoom"), Mapping):
        z = caps["zoom"]
        v = _num(beam.get("zoom"))
        out["zoom"] = max(float(z["min"]), min(float(z["max"]), v)) if v is not None else \
            (float(z["min"]) + float(z["max"])) / 2.0
    for a, d in NEUTRAL_BEAM.items():
        if a in attrs:
            v = _num(beam.get(a))
            out[a] = d if v is None else max(0.0, min(1.0, v))
    for a in SLOT_ATTRS + ("cct",):
        if a in attrs:
            out[a] = None
    for a in SPINS + SHAKES:
        if a in attrs:
            out[a] = 0.0
    if "prism" in attrs:
        out["prism"] = 0
    if "shutter" in attrs:
        out["shutter"] = {"mode": "open", "hz": None}
    if "pt_speed" in attrs:
        out["pt_speed"] = NEUTRAL_PT_SPEED
    return out
