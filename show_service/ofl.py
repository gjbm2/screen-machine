"""ofl: an Open Fixture Library fixture file -> one mode's attribute map (README "Stage 5", S5.2 "OFL -> channels").

The Open Fixture Library (https://open-fixture-library.org, MIT; fixture schema 12.x) describes a fixture once
(``availableChannels``, ``wheels``, ``templateChannels``/``matrix``) and lists its DMX modes (``modes[].channels``:
channel names, fine and switching aliases, ``null`` gaps). ``parse`` turns one mode into what the look engine and the
encoder need: a ``contract.TypeInfo`` with one ``contract.AttrChannel`` per channel, the type's ``attrs``, its colour
wheel and its footprint. Pure: no threads, no network, never raises on a bad file (``problems`` inside).
tools/show/add_fixture.py downloads the files; fixtures.py calls ``load`` for every ``source: ofl`` type.

**A channel's attribute** comes from its first capability that isn't ``NoFunction`` (a "0-9: nothing" range ahead of
the real function is common):
  * ``Intensity`` -> ``intensity``;
  * ``ColorIntensity`` -> ``emitter``, named from its ``color`` in lower snake case (``Warm White`` -> ``warm_white``)
    when that is a ``contract.EMITTERS`` word; any other colour -> ``other``;
  * ``WheelSlot`` on a wheel whose slots carry ``colors`` -> ``wheel`` (the type's ``wheel`` lists each colour slot's
    DMX value, the middle of its range; ``Open`` counts as white). Only a type's first colour wheel is driven;
  * ``ShutterStrobe`` -> ``shutter``;
  * ``Pan``, ``Tilt`` -> ``pan``, ``tilt``;
  * anything else -> the ATTRS word of its ``ranges`` (below: ``gobo``, ``zoom``, ``pt_speed``, ...), else ``other``.
colour_mix drives ``intensity`` and ``emitter`` channels linearly over their whole range, so a channel is driven that
way only when every capability on it is that same function (or ``NoFunction``). A channel that mixes functions (a
"Dimmer/Strobe": intensity up to 127, strobe above) or whose intensity runs backwards reads ``other`` with a problem
line: driving it to full would strobe (fixture_encode still uses its strobe range when an effect asks for one).

**Ranges** (docs/LIGHTS.md §2.2): every coarse channel also lists ``ranges``, one ``contract.CapRange`` per capability
(``NoFunction`` gaps and split wheel positions left out), each mapped to the ATTRS word it serves with the value at
each end in its normalised unit: Pan/Tilt in degrees centred on the middle of the channel's angles (0..540 -> -270..
+270; a percent angle uses the file's ``physical.focus`` panMax/tiltMax, else UNKNOWN_PAN_DEG / UNKNOWN_TILT_DEG with a
problem line, and setup ``range`` wins on the page); rotation speeds signed shares of the fastest (fast CW +1, slow CW
+0.01, stop 0; Hz and rpm divided by the channel's largest); speeds 0..1 (fast 1, slow 0.01); a PanTiltSpeed given as
a duration 1 - its share; strobes in Hz (keywords through STROBE_HZ_UNKNOWN, a problem line); beam angles in degrees
(keywords and % across the lens's ``degreesMinMax``, else ZOOM_UNKNOWN_DEG, a problem line); focus near 0 .. far 1;
iris and frost 0..1; colour temperature in kelvin (warm/CTO and cold/CTB at CCT_WARM_K / CCT_COLD_K, a problem line).
Wheels: the colour wheel (as above) sets ``colour_slot`` and its rotation ``colour_spin``; the first wheel with Gobo
slots ``gobo`` (shake ``gobo_shake``, rotation ``gobo_scroll``, the image's own rotation ``gobo_spin``), the second
``gobo2``; a wheel of Prism slots ``prism`` (Open 0, then 1..n). Anything the engine never drives (effects, presets,
maintenance, fog, blades, ...) is ``macro``: the page's "More" picks its range by ``word``. TypeInfo gains ``wheels``
(the mode's wheels, their kind and slots) and ``physical`` (lens and pan/tilt degrees, None when the file doesn't
say), and ``attrs`` lists every ATTRS word a range serves.

**Fine channels.** A mode that carries a channel's ``fineChannelAliases`` gives the coarse channel its ``fine`` offset
(16-bit values, MSB first). Each fine channel is also listed, as ``other``, with its byte of the coarse channel's
default; a second fine (24-bit) stays at its default. ``dmxValueResolution`` (default: the finest the channel has)
scales ``defaultValue`` and every ``dmxRange``.

**Defaults** (what an undriven channel sends), in this order: OFL's ``defaultValue`` (a number at the channel's
resolution, or a ``"50%"`` string); for a shutter, the start of its first ``shutterEffect: "Open"`` capability (a
shutter left at 0 is often dark, §9.2); for a Pan or Tilt channel, its centre (the owner's 18 Oct ruling: a mover never
rests aimed at 0, 0; the fine byte too); for a mixed channel with an intensity range, the brightest point of that range
(so the fixture still lights while the channel is held); otherwise 0. A fixture's ``pin`` (fixtures.yaml) overrides
any of them.

**Switching channels** (a mode channel named by a capability's ``switchChannels``) are ``other``, holding the default of
the channel they switch to at their controlling channel's default value.

**Matrix fixtures** (``matrix``, ``templateChannels``) aren't built: a mode that uses pixel channels reads a problem
line and the type is inert (footprint 0, nothing driven, nothing sent). A matrix fixture's plain modes work.

Config names live in config: nothing here branches on a fixture, maker or mode name.
"""

from __future__ import annotations

import json
import math
import pathlib
import re
from typing import Any, Optional

from . import contract

OFL_URL = "https://open-fixture-library.org"        # + /<maker>/<model>.json: the fixture file (add_fixture.py)
SCHEMA_MAJOR = 12                                   # the fixture schema this reader follows (12.x)
MAX_RES_BYTES = 4                                   # 8..32 bit: OFL's dmxValueResolution goes to 24bit
_RES_BYTES = {"8bit": 1, "16bit": 2, "24bit": 3}
_ATTR_BY_CAP = {"Intensity": "intensity", "ColorIntensity": "emitter", "WheelSlot": "wheel",
                "ShutterStrobe": "shutter", "Pan": "pan", "Tilt": "tilt"}
_BRIGHTNESS_WORDS = {"off": 0.0, "dark": 0.01, "bright": 1.0}   # OFL's brightness keywords (schema: Brightness)
_OPEN = "Open"                                      # ShutterStrobe.shutterEffect / a wheel slot's type
_WHITE = "#ffffff"

# Ranges (docs/LIGHTS.md §2.2). Code starting points, each said on the type's problem lines when used: the Lights page
# (setup ``range``) or the fixture's OFL file is the real answer.
UNKNOWN_PAN_DEG = 540.0                             # starting point: a pan range the file doesn't give in degrees
UNKNOWN_TILT_DEG = 270.0                            # starting point: the same for tilt
ZOOM_UNKNOWN_DEG = (10.0, 40.0)                     # starting point: beam angle narrow..wide with no lens degrees
STROBE_HZ_UNKNOWN = (1.0, 20.0)                     # starting point: a strobe speed slow..fast that isn't in Hz
CCT_WARM_K = 2700.0                                 # starting point: OFL's "warm" / "CTO"
CCT_COLD_K = 6500.0                                 # starting point: OFL's "cold" / "CTB"
SLOW = 0.01                                         # OFL's "slow" speed (and "slow CW" rotation): a share of fastest
_PERCENT_WORDS = {"off": 0.0, "low": 0.01, "high": 1.0, "closed": 0.0, "open": 1.0}   # OFL percent keywords
_SHUTTER_MODE = {"Open": "open", "Closed": "closed", "Strobe": "strobe", "Pulse": "pulse", "RampUp": "ramp",
                 "RampDown": "ramp", "RampUpDown": "ramp", "Lightning": "random", "Spikes": "random",
                 "Burst": "burst"}
_ROTATION_WORDS = {"fast cw": 1.0, "slow cw": SLOW, "stop": 0.0, "slow ccw": -SLOW, "fast ccw": -1.0}
_SPEED_WORDS = {"fast": 1.0, "slow": SLOW, "stop": 0.0}
_DURATION_WORDS = {"instant": 0.0, "short": 0.1, "long": 1.0}   # starting points: OFL's duration keywords as a share
_GOBO_SLOTS = ("Gobo", "AnimationGoboStart", "AnimationGoboEnd")
_ENTITY = re.compile(r"^([-+]?\d+(?:\.\d+)?)\s*(deg|%|hz|bpm|rpm|ms|s|m|k)?$")   # an OFL entity: number + unit


# -- small readers -----------------------------------------------------------------------------------------------------
def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def _resolution(ch: dict) -> int:
    """Bytes per value: dmxValueResolution, else 1 + the channel's fine aliases (OFL: the finest it has)."""
    r = _RES_BYTES.get(ch.get("dmxValueResolution"))
    if r:
        return r
    fines = ch.get("fineChannelAliases")
    return max(1, min(MAX_RES_BYTES, 1 + (len(fines) if isinstance(fines, list) else 0)))


def _top(res: int) -> int:
    return 256 ** res - 1


def _dmx_value(v: Any, res: int) -> Optional[int]:
    """A DMX value at ``res`` bytes: a number, or a percentage string ("50%") of the full range. None: unreadable."""
    n = _num(v)
    if n is None and isinstance(v, str) and v.strip().endswith("%"):
        try:
            n = float(v.strip()[:-1]) / 100.0 * _top(res)
        except ValueError:
            n = None
    return None if n is None or not math.isfinite(n) else int(round(n))


def _split(value: int, res: int) -> list[int]:
    """A value at ``res`` bytes -> its bytes, MSB first."""
    value = max(0, min(_top(res), int(value)))
    return [(value >> (8 * (res - 1 - k))) & 0xFF for k in range(res)]


def _byte(value: int, res: int, k: int) -> int:
    """Byte ``k`` (0 = coarse) of a value at ``res`` bytes; 0 past its resolution (an 8bit channel's fine)."""
    return _split(value, res)[k] if k < res else 0


def _brightness(v: Any, fallback: float) -> float:
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _BRIGHTNESS_WORDS:
            return _BRIGHTNESS_WORDS[s]
        if s.endswith("%"):
            try:
                return float(s[:-1]) / 100.0
            except ValueError:
                return fallback
    return fallback


def _caps(ch: dict, res: int) -> list[dict]:
    """The channel's capabilities, each with a usable dmxRange (a lone ``capability`` spans the whole channel)."""
    if isinstance(ch.get("capability"), dict):
        return [{**ch["capability"], "dmxRange": [0, _top(res)]}]
    out = []
    for c in ch.get("capabilities") or []:
        r = c.get("dmxRange") if isinstance(c, dict) else None
        if (isinstance(r, list) and len(r) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in r)
                and 0 <= r[0] <= r[1] <= _top(res)):
            out.append(c)
    return out


def _real(caps: list[dict]) -> list[dict]:
    return [c for c in caps if c.get("type") != "NoFunction"]


def _emitter(colour: Any) -> Optional[str]:
    if not isinstance(colour, str):
        return None
    e = colour.strip().lower().replace(" ", "_").replace("-", "_")
    return e if e in contract.EMITTERS else None


def _hex_mean(colours: list) -> Optional[str]:
    rgb = []
    for c in colours:
        if isinstance(c, str) and len(c) == 7 and c[0] == "#":
            try:
                rgb.append(tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)))
            except ValueError:
                continue
    if not rgb:
        return None
    return "#" + "".join(f"{round(sum(p[i] for p in rgb) / len(rgb)):02x}" for i in range(3))


def _slot_number(c: dict) -> Optional[int]:
    """A WheelSlot capability's whole slot (1-based), or None for a split or a range between slots."""
    n = c.get("slotNumber")
    if n is None and c.get("slotNumberStart") is not None and c.get("slotNumberStart") == c.get("slotNumberEnd"):
        n = c.get("slotNumberStart")
    n = _num(n)
    return int(n) if n is not None and n == int(n) and n >= 1 else None


def _entity(v: Any) -> tuple[Optional[float], str]:
    """An OFL entity -> (number, unit: "deg" | "%" | "hz" | "bpm" | "rpm" | "s" | "ms" | "m" | "k" | "" for a bare
    number), or (None, the keyword, lower case; "" when there is none)."""
    if isinstance(v, bool):
        return None, ""
    if isinstance(v, (int, float)):
        return (float(v), "") if math.isfinite(v) else (None, "")
    if not isinstance(v, str):
        return None, ""
    s = v.strip().lower()
    m = _ENTITY.match(s)
    return (float(m.group(1)), m.group(2) or "") if m else (None, s)


def _pair(c: dict, prop: str) -> Optional[tuple[Any, Any]]:
    """A capability's (start, end) of ``prop`` (``<prop>Start``/``<prop>End``, else ``<prop>`` for both); None when
    it has neither."""
    if f"{prop}Start" in c or f"{prop}End" in c:
        return c.get(f"{prop}Start"), c.get(f"{prop}End", c.get(f"{prop}Start"))
    if prop in c:
        return c.get(prop), c.get(prop)
    return None


def _wheel_of(c: dict, key: str) -> str:
    """The wheel a wheel capability turns: its ``wheel`` (the first of a list), else the channel's own key (OFL)."""
    w = c.get("wheel")
    if isinstance(w, list):
        w = w[0] if w else None
    return w if isinstance(w, str) else key


def _wheel_kind(slots: Any) -> str:
    """colour (a slot carries colours, as the colour-wheel rule above) | gobo | prism | other."""
    slots = [s for s in slots or () if isinstance(s, dict)]
    if any(s.get("colors") for s in slots):
        return "colour"
    if any(s.get("type") in _GOBO_SLOTS for s in slots):
        return "gobo"
    if any(s.get("type") == "Prism" for s in slots):
        return "prism"
    return "other"


def _slot_name(slot: dict, n: int) -> str:
    """A wheel slot in page words: its OFL name, else by its type ("Open", "Pattern 3", "Prism 3", "Frost")."""
    name = slot.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    t = slot.get("type")
    if t in _GOBO_SLOTS:
        return f"Pattern {n}"
    if t == "Prism":
        f = _num(slot.get("facets"))
        return f"Prism {int(f)}" if f else "Prism"
    if t == "Color":
        return _hex_mean(slot.get("colors") or []) or f"Colour {n}"
    return str(t) if isinstance(t, str) and t else f"Slot {n}"


def wheel_view(wheels: dict, name: str, kind: str, attr: Optional[str]) -> dict[str, Any]:
    """TypeInfo.wheels[name]: {kind, attr, slots: [{slot, type, name, hex?, facets?}]} (1-based slots)."""
    out = []
    for n, s in enumerate((wheels.get(name) or {}).get("slots") or [], start=1):
        if not isinstance(s, dict):
            continue
        row: dict[str, Any] = {"slot": n, "type": s.get("type") if isinstance(s.get("type"), str) else "Unknown",
                               "name": _slot_name(s, n)}
        if s.get("type") == _OPEN:
            row["hex"] = _WHITE
        elif s.get("colors"):
            hx = _hex_mean(s.get("colors") or [])
            if hx:
                row["hex"] = hx
        if _num(s.get("facets")):
            row["facets"] = int(_num(s.get("facets")))
        out.append(row)
    return {"kind": kind, "attr": attr, "slots": out}


# -- the file ----------------------------------------------------------------------------------------------------------
def read(path: pathlib.Path) -> tuple[Optional[dict], Optional[str]]:
    """(the fixture JSON, None) or (None, why)."""
    try:
        doc = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "file not found"
    except OSError as e:
        return None, f"unreadable: {e.strerror or e}"
    except ValueError as e:
        return None, f"not JSON: {e}"
    if not isinstance(doc, dict) or not isinstance(doc.get("modes"), list):
        return None, "not an Open Fixture Library fixture (no modes list)"
    return doc, None


def _is_matrix_entry(entry: Any, known: dict, switch: dict) -> bool:
    return entry is not None and (not isinstance(entry, str) or (entry not in known and entry not in switch))


def modes(doc: dict) -> list[dict]:
    """Every mode: {name, shortName, channels (its footprint), matrix (uses pixel channels: inert here)}."""
    known, switch = _index(doc)
    out = []
    for m in doc.get("modes") or []:
        if not isinstance(m, dict):
            continue
        chans = m.get("channels") if isinstance(m.get("channels"), list) else []
        out.append({"name": m.get("name"), "shortName": m.get("shortName"), "channels": len(chans),
                    "matrix": any(_is_matrix_entry(e, known, switch) for e in chans)})
    return out


def find_mode(doc: dict, mode: str) -> Optional[dict]:
    """A mode by its name or shortName, exactly; else the one whose name or shortName matches ignoring case."""
    ms = [m for m in doc.get("modes") or [] if isinstance(m, dict)]
    for m in ms:
        if mode in (m.get("name"), m.get("shortName")):
            return m
    low = str(mode).strip().lower()
    hits = [m for m in ms if low in (str(m.get("name") or "").lower(), str(m.get("shortName") or "").lower())]
    return hits[0] if len(hits) == 1 else None


def _index(doc: dict) -> tuple[dict[str, tuple[str, int]], dict[str, list[tuple[str, dict]]]]:
    """name -> (channel key, 0 coarse | k-th fine alias); switching alias -> [(controlling channel key, capability)]."""
    avail = doc.get("availableChannels") if isinstance(doc.get("availableChannels"), dict) else {}
    known: dict[str, tuple[str, int]] = {}
    switch: dict[str, list[tuple[str, dict]]] = {}
    for key, ch in avail.items():
        if not isinstance(ch, dict):
            continue
        known[key] = (key, 0)
        for k, alias in enumerate(ch.get("fineChannelAliases") or [], start=1):
            if isinstance(alias, str):
                known.setdefault(alias, (key, k))
        for c in ch.get("capabilities") or []:
            for alias in ((c.get("switchChannels") or {}) if isinstance(c, dict) else {}):
                switch.setdefault(alias, []).append((key, c))
    return known, switch


def _empty(type_id: str, file: Optional[str], mode: Optional[str]) -> contract.TypeInfo:
    return {"id": type_id, "source": "ofl", "kind": "lamp", "file": file, "mode": mode, "footprint": 0,
            "attrs": [], "channels": [], "emitters": {}, "wheel": [], "curve": "srgb", "problems": [],
            "wheels": {}, "physical": no_physical()}


def no_physical() -> dict[str, Any]:
    """TypeInfo.physical when nothing is known (a builtin type, an unread file)."""
    return {"lens_deg": None, "pan_deg": None, "tilt_deg": None, "zoom_deg": None}


class _Mode:
    """One mode's channels, resolved (``parse``'s working state)."""

    def __init__(self, doc: dict, chans: list, problems: list[str], mode_physical: Any = None) -> None:
        self.avail = doc.get("availableChannels") if isinstance(doc.get("availableChannels"), dict) else {}
        self.wheels = doc.get("wheels") if isinstance(doc.get("wheels"), dict) else {}
        phys = doc.get("physical") if isinstance(doc.get("physical"), dict) else {}
        self.physical = {**phys, **(mode_physical if isinstance(mode_physical, dict) else {})}   # a mode's own wins
        lens = self.physical.get("lens") if isinstance(self.physical.get("lens"), dict) else {}
        dm = lens.get("degreesMinMax")
        self.lens_deg: Optional[tuple[float, float]] = (
            (float(min(dm)), float(max(dm))) if isinstance(dm, list) and len(dm) == 2
            and all(_num(x) is not None for x in dm) and max(dm) > 0 else None)
        self.zoom_guess = False                             # a beam angle came from ZOOM_UNKNOWN_DEG
        self.wheel_attr: dict[str, Optional[str]] = {}      # assign_wheels: wheel -> the ATTRS word its slots set
        self.wheel_kinds: dict[str, str] = {}
        self.known, self.switch = _index(doc)
        self.chans = chans
        self.problems = problems
        self.offset = {}                                  # name -> its first offset in this mode
        for i, e in enumerate(chans):
            if isinstance(e, str):
                self.offset.setdefault(e, i)
        self._defaults: dict[str, tuple[int, int, Optional[str]]] = {}   # key -> (value, res, why)

    # -- classification ---------------------------------------------------------------------------------------------
    def classify(self, key: str) -> tuple[str, dict]:
        """(attr, extra) for a coarse channel: extra may hold emitter, wheel_name, light_open (a default) and why."""
        ch = self.avail[key]
        res = _resolution(ch)
        caps = _caps(ch, res)
        real = _real(caps)
        if not caps:
            return "other", {"why": "no capabilities"}
        first = (real or caps)[0]
        attr = _ATTR_BY_CAP.get(first.get("type"), "other")
        if attr == "intensity":
            mixed = [c for c in real if c.get("type") != "Intensity"]
            backwards = any(_brightness(c.get("brightnessStart", c.get("brightness")), 0.0)
                            > _brightness(c.get("brightnessEnd", c.get("brightness")), 1.0) for c in real
                            if c.get("type") == "Intensity")
            if mixed or backwards:
                return "other", {"light_open": self._brightest(caps), "why": (
                    "mixes intensity with other functions" if mixed else "its intensity runs backwards")}
            return "intensity", {}
        if attr == "emitter":
            e = _emitter(first.get("color"))
            if e is None:
                return "other", {}
            if any(c.get("type") != "ColorIntensity" or _emitter(c.get("color")) != e for c in real):
                return "other", {"why": f"mixes its {e} intensity with other functions"}
            return "emitter", {"emitter": e}
        if attr == "wheel":
            name = first.get("wheel") if isinstance(first.get("wheel"), str) else key
            wheel = self.wheels.get(name)
            slots = wheel.get("slots") if isinstance(wheel, dict) else None
            if not isinstance(slots, list) or not any(isinstance(s, dict) and s.get("colors") for s in slots):
                return "other", {}
            return "wheel", {"wheel_name": name}
        return attr, {}

    @staticmethod
    def _brightest(caps: list[dict]) -> Optional[int]:
        best, at = -1.0, None
        for c in caps:
            if c.get("type") != "Intensity":
                continue
            lo, hi = c["dmxRange"]
            bs = _brightness(c.get("brightnessStart", c.get("brightness")), 0.0)
            be = _brightness(c.get("brightnessEnd", c.get("brightness")), 1.0)
            b, v = (be, hi) if be >= bs else (bs, lo)
            if b > best:
                best, at = b, v
        return at

    # -- defaults ----------------------------------------------------------------------------------------------------
    def default(self, key: str, depth: int = 0) -> tuple[int, int, Optional[str]]:
        """(the channel's default at full resolution, its resolution, a problem or None). Cached per channel."""
        if key in self._defaults:
            return self._defaults[key]
        ch = self.avail[key]
        res = _resolution(ch)
        why = None
        value = None
        if "defaultValue" in ch:
            value = _dmx_value(ch.get("defaultValue"), res)
            if value is None:
                why = f"defaultValue {ch.get('defaultValue')!r} isn't a DMX value: ignored"
            elif not 0 <= value <= _top(res):
                why = f"defaultValue {ch.get('defaultValue')!r} is out of range: clamped"
                value = max(0, min(_top(res), value))
        if value is None:
            attr, extra = self.classify(key)
            if attr == "shutter":
                opens = [c for c in _caps(ch, res) if c.get("type") == "ShutterStrobe"
                         and c.get("shutterEffect") == _OPEN]
                value = opens[0]["dmxRange"][0] if opens else None
            elif attr in ("pan", "tilt"):                     # its centre, never aimed at 0, 0 (owner, 18 Oct)
                spans = [c["dmxRange"] for c in _caps(ch, res) if c.get("type") in ("Pan", "Tilt")]
                if spans:
                    value = int(math.floor((min(r[0] for r in spans) + max(r[1] for r in spans)) / 2.0 + 0.5))
            elif extra.get("light_open") is not None:
                value = extra["light_open"]
        out = (value if value is not None else 0, res, why)
        self._defaults[key] = out
        return out

    def switch_default(self, alias: str) -> int:
        """A switching channel's default: the channel it switches to at its controller's default, coarse byte."""
        users = self.switch.get(alias) or []
        in_mode = [u for u in users if u[0] in self.offset] or users
        if not in_mode:
            return 0
        ctl = in_mode[0][0]
        value, res, _ = self.default(ctl)
        ch = self.avail[ctl]
        for c in _caps(ch, res):
            lo, hi = c["dmxRange"]
            if lo <= value <= hi:
                target = (c.get("switchChannels") or {}).get(alias)
                if isinstance(target, str) and target in self.known:
                    tkey, k = self.known[target]
                    tval, tres, _ = self.default(tkey)
                    return _byte(tval, tres, k)
                break
        return 0

    def wheel_slots(self, key: str, wheel_name: str) -> list[dict]:
        """The colour slots of the wheel this channel turns: [{dmx (coarse, the middle of its range), hex, name}]."""
        ch = self.avail[key]
        res = _resolution(ch)
        slots = (self.wheels.get(wheel_name) or {}).get("slots") or []
        out, seen = [], set()
        for c in _caps(ch, res):
            if c.get("type") != "WheelSlot" or (c.get("wheel") or key) != wheel_name:
                continue
            n = _slot_number(c)
            if n is None or n in seen or n > len(slots) or not isinstance(slots[n - 1], dict):
                continue
            seen.add(n)
            slot = slots[n - 1]
            if slot.get("type") == _OPEN:
                hexv, name = _WHITE, slot.get("name") or _OPEN
            else:
                hexv = _hex_mean(slot.get("colors") or [])
                if hexv is None:
                    continue
                name = slot.get("name") or hexv
            lo, hi = c["dmxRange"]
            out.append({"dmx": _split((lo + hi) // 2, res)[0], "hex": hexv, "name": str(name)})
        return sorted(out, key=lambda s: s["dmx"])

    # -- ranges (docs/LIGHTS.md §2.2) ----------------------------------------------------------------------------------
    def wheels_in_mode(self) -> list[str]:
        """The wheels this mode's channels turn, in channel order (a wheel the file defines)."""
        out: list[str] = []
        for e in self.chans:
            if not isinstance(e, str) or e not in self.known or self.known[e][1] != 0:
                continue
            key = self.known[e][0]
            ch = self.avail[key]
            for c in _caps(ch, _resolution(ch)):
                if c.get("type") in ("WheelSlot", "WheelShake", "WheelSlotRotation", "WheelRotation"):
                    w = _wheel_of(c, key)
                    if w in self.wheels and w not in out:
                        out.append(w)
        return out

    def assign_wheels(self, colour_wheel: Optional[str]) -> dict[str, dict]:
        """TypeInfo.wheels: each wheel of the mode with its kind and the ATTRS word its slots set (the driven colour
        wheel colour_slot; the first gobo wheel gobo, the second gobo2; the first prism wheel prism; else None)."""
        out: dict[str, dict] = {}
        gobos, prism = 0, False
        for w in self.wheels_in_mode():
            kind = _wheel_kind((self.wheels.get(w) or {}).get("slots"))
            attr = None
            if w == colour_wheel:
                attr = "colour_slot"
            elif kind == "gobo" and gobos < 2:
                attr = ("gobo", "gobo2")[gobos]
                gobos += 1
            elif kind == "prism" and not prism:
                attr, prism = "prism", True
            out[w] = wheel_view(self.wheels, w, kind, attr)
        self.wheel_attr = {w: v["attr"] for w, v in out.items()}
        self.wheel_kinds = {w: v["kind"] for w, v in out.items()}
        return out

    def _gobo_attr(self, wheel: str) -> Optional[str]:
        """gobo | gobo2 for a gobo wheel; a rotation that names no wheel of its own: the first gobo wheel's."""
        a = self.wheel_attr.get(wheel)
        if a in ("gobo", "gobo2"):
            return a
        if wheel not in self.wheel_attr:
            return next((x for x in self.wheel_attr.values() if x == "gobo"), None)
        return None

    def _one(self, key: str, c: dict) -> Optional[dict]:
        """One capability -> a raw range: {dmx, cap, attr, kind, lo, hi (entities or numbers), slot?, mode?, word?}.
        ``kind`` says how _finish reads lo/hi. None: left out (a split wheel position)."""
        t = c.get("type")
        r: dict[str, Any] = {"dmx": [int(c["dmxRange"][0]), int(c["dmxRange"][1])], "cap": str(t)}
        word = next((c.get(k) for k in ("comment", "effectName", "effectPreset", "shutterEffect")
                     if isinstance(c.get(k), str) and c.get(k).strip()), None)
        if word:
            r["word"] = word.strip()

        def ranged(attr: str, kind: str, prop: Optional[str], default: tuple = (None, None)) -> dict:
            p = _pair(c, prop) if prop else None
            r.update(attr=attr, kind=kind, lo=p[0] if p else default[0], hi=p[1] if p else default[1])
            return r

        def fixed(attr: str, v: float, **extra: Any) -> dict:
            r.update(attr=attr, kind="fixed", lo=float(v), hi=float(v), **extra)
            return r
        if t == "Intensity":
            return ranged("intensity", "pct", "brightness", ("0%", "100%"))
        if t == "ColorIntensity":
            if isinstance(c.get("color"), str):
                r.setdefault("word", c["color"])
            return ranged("colour", "pct", "brightness", ("0%", "100%"))
        if t == "ColorTemperature":
            return ranged("cct", "k", "colorTemperature") if _pair(c, "colorTemperature") else fixed("macro", 0.0)
        if t in ("ShutterStrobe", "StrobeSpeed"):
            eff = c.get("shutterEffect") if t == "ShutterStrobe" else "Strobe"
            mode = _SHUTTER_MODE.get(eff, "strobe")
            if mode == "strobe" and c.get("randomTiming") is True:
                mode = "random"
            r["mode"] = mode
            return ranged("shutter", "hz", "speed", (0.0, 0.0))
        if t in ("Pan", "Tilt"):
            return ranged(t.lower(), "angle", "angle", ("0%", "100%"))
        if t in ("PanContinuous", "TiltContinuous"):
            return ranged(t[:-len("Continuous")].lower() + "_spin", "rot", "speed", (0.0, 0.0))
        if t == "PanTiltSpeed":
            if _pair(c, "speed"):
                return ranged("pt_speed", "speed", "speed")
            return ranged("pt_speed", "dur", "duration", (0.0, 0.0))
        if t in ("BeamAngle", "Zoom"):
            return ranged("zoom", "beam", "angle", ("narrow", "wide"))
        if t == "Focus":
            return ranged("focus", "dist", "distance", ("near", "far"))
        if t == "Iris":
            return ranged("iris", "pct", "openPercent", ("0%", "100%"))
        if t == "Frost":
            return ranged("frost", "pct", "frostIntensity", ("0%", "100%"))
        if t == "Prism":
            if _pair(c, "speed"):
                r["slot"] = 1
                return ranged("prism_spin", "rot", "speed")
            return fixed("prism", 1.0, slot=1)
        if t == "PrismRotation":
            return ranged("prism_spin", "rot", "speed") if _pair(c, "speed") else fixed("macro", 0.0)
        if t in ("WheelSlot", "WheelShake", "WheelSlotRotation", "WheelRotation"):
            return self._wheel_range(key, c, r, ranged, fixed)
        return fixed("macro", 0.0)

    def _wheel_range(self, key: str, c: dict, r: dict, ranged: Any, fixed: Any) -> Optional[dict]:
        t = c.get("type")
        wheel = _wheel_of(c, key)
        wattr = self.wheel_attr.get(wheel)
        slots = (self.wheels.get(wheel) or {}).get("slots") or []
        if t == "WheelSlot":
            n = _slot_number(c)
            if n is None:
                return None                                       # a split, or between slots: skipped (as today)
            slot = slots[n - 1] if n <= len(slots) and isinstance(slots[n - 1], dict) else None
            if slot is None:
                return fixed("macro", 0.0) if wattr is None else None
            r.setdefault("word", _slot_name(slot, n))
            st = slot.get("type")
            if st == "Frost":
                return fixed("frost", 1.0, slot=n)
            if st == "Iris" and _num(_entity(slot.get("openPercent"))[0]) is not None:
                return fixed("iris", _entity(slot.get("openPercent"))[0] / 100.0, slot=n)
            if wattr == "prism" or (st == "Prism" and wattr is None):
                k = 0 if st == _OPEN else sum(1 for s in slots[:n] if isinstance(s, dict) and s.get("type") == "Prism")
                return fixed("prism", float(k), slot=k)
            if wattr in ("colour_slot", "gobo", "gobo2"):
                return fixed(wattr, float(n), slot=n)
            return fixed("macro", 0.0)
        if t == "WheelShake":
            a = self._gobo_attr(wheel) if self.wheel_kinds.get(wheel, "gobo") == "gobo" else None
            if a is None:
                return fixed("macro", 0.0)
            n = _slot_number(c)
            if n is not None:
                r["slot"] = n
            return ranged(f"{a}_shake", "speed", "shakeSpeed", (1.0, 1.0))
        if not _pair(c, "speed"):
            return fixed("macro", 0.0)                            # an indexed angle: the page's "More"
        if t == "WheelSlotRotation":
            a = self._gobo_attr(wheel)
            return ranged(f"{a}_spin", "rot", "speed") if a else fixed("macro", 0.0)
        if wattr == "colour_slot":                                # WheelRotation: the colour wheel turning
            return ranged("colour_spin", "rot", "speed")
        if wattr == "gobo":                                       # the first gobo wheel scrolling (no gobo2_scroll)
            return ranged("gobo_scroll", "rot", "speed")
        return fixed("macro", 0.0)

    def cap_ranges(self, key: str, pan_tilt: dict[str, Optional[float]]) -> list[contract.CapRange]:
        """The channel's ranges, normalised (module docstring). ``pan_tilt`` collects the pan/tilt degrees the file
        gives (TypeInfo.physical)."""
        ch = self.avail[key]
        raw = []
        for c in _real(_caps(ch, _resolution(ch))):
            r = self._one(key, c)
            if r is not None:
                raw.append(r)
        return self._finish(key, raw, pan_tilt)

    def _finish(self, key: str, raw: list[dict], pan_tilt: dict[str, Optional[float]]) -> list[contract.CapRange]:
        out: list[contract.CapRange] = []
        by_attr: dict[str, list[dict]] = {}
        for r in raw:
            by_attr.setdefault(r["attr"], []).append(r)
        for attr, rs in by_attr.items():
            if attr in ("pan", "tilt"):
                vals = self._angles(key, attr, rs, pan_tilt)
            else:
                vals = [self._values(key, r, rs) for r in rs]
            for r, (lo, hi) in zip(rs, vals):
                cr: dict[str, Any] = {"dmx": r["dmx"], "cap": r["cap"], "attr": attr, "lo": round(lo, 6),
                                      "hi": round(hi, 6)}
                for k in ("slot", "mode", "word"):
                    if k in r:
                        cr[k] = r[k]
                out.append(cr)                                    # type: ignore[arg-type]
        return sorted(out, key=lambda x: (x["dmx"][0], x["dmx"][1]))

    def _angles(self, key: str, attr: str, rs: list[dict], pan_tilt: dict) -> list[tuple[float, float]]:
        """Pan/Tilt degrees, centred on the middle of the channel's angles."""
        raw = [(_entity(r["lo"]), _entity(r["hi"])) for r in rs]
        in_deg = all(u == "deg" for pair in raw for n, u in pair if n is not None) and all(
            n is not None for pair in raw for n, _ in pair)
        total = None
        if not in_deg:
            focus = self.physical.get("focus") if isinstance(self.physical.get("focus"), dict) else {}
            total = _num(focus.get(f"{attr}Max"))
            if total is None or total <= 0:
                total = UNKNOWN_PAN_DEG if attr == "pan" else UNKNOWN_TILT_DEG
                self.problems.append(f"{key}: {attr} range not in the file: {total:g}° assumed (set it on the Lights "
                                     f"page)")
                pan_tilt.setdefault(attr, None)
            else:
                pan_tilt.setdefault(attr, float(total))

        def deg(e: tuple[Optional[float], str], end: float) -> float:
            n, u = e
            if n is None:
                return end * (total or 0.0)
            return n if u == "deg" or (u == "" and in_deg) else n / 100.0 * (total or 0.0)
        vals = [(deg(a, 0.0), deg(b, 1.0)) for a, b in raw]
        lo_all = min(min(v) for v in vals)
        hi_all = max(max(v) for v in vals)
        if in_deg:
            pan_tilt.setdefault(attr, float(hi_all - lo_all))
        c = (lo_all + hi_all) / 2.0
        return [(a - c, b - c) for a, b in vals]

    def _values(self, key: str, r: dict, rs: list[dict]) -> tuple[float, float]:
        """A raw range's (lo, hi) in its attr's unit; ``rs``: every range of that attr on the channel (scales)."""
        kind = r["kind"]
        if kind == "fixed":
            return r["lo"], r["hi"]
        ends = (_entity(r["lo"]), _entity(r["hi"]))
        if kind == "pct":
            return tuple(self._pct(e, d) for e, d in zip(ends, (0.0, 1.0)))     # type: ignore[return-value]
        if kind in ("rot", "speed", "dur"):
            scale = self._scale(rs, ("hz", "rpm", "bpm") if kind != "dur" else ("s", "ms"))
            v = tuple(self._speed(e, kind, scale) for e in ends)
            return (1.0 - v[0], 1.0 - v[1]) if kind == "dur" else v            # type: ignore[return-value]
        if kind == "hz":
            return tuple(self._hz(key, e) for e in ends)                       # type: ignore[return-value]
        if kind == "beam":
            return tuple(self._beam(key, e) for e in ends)                     # type: ignore[return-value]
        if kind == "dist":
            ms = [n for x in rs for e in (_entity(x["lo"]), _entity(x["hi"])) for n in [e[0]] if n is not None
                  and e[1] == "m"]
            return tuple(self._dist(e, ms) for e in ends)                      # type: ignore[return-value]
        if kind == "k":
            return tuple(self._kelvin(key, e) for e in ends)                   # type: ignore[return-value]
        return 0.0, 0.0

    @staticmethod
    def _pct(e: tuple[Optional[float], str], default: float) -> float:
        n, u = e
        if n is not None:
            return n / 100.0 if u == "%" else n
        if u in _PERCENT_WORDS:
            return _PERCENT_WORDS[u]
        if u in _BRIGHTNESS_WORDS:
            return _BRIGHTNESS_WORDS[u]
        return default

    @staticmethod
    def _scale(rs: list[dict], units: tuple[str, ...]) -> float:
        """The largest |value| among the ranges' ends in ``units`` (rpm and bpm in Hz; ms in s): what 1 is."""
        best = 0.0
        for x in rs:
            for e in (_entity(x["lo"]), _entity(x["hi"])):
                n, u = e
                if n is not None and u in units:
                    v = n / 60.0 if u in ("rpm", "bpm") else n / 1000.0 if u == "ms" else n
                    best = max(best, abs(v))
        return best

    @staticmethod
    def _speed(e: tuple[Optional[float], str], kind: str, scale: float) -> float:
        n, u = e
        if n is None:
            words = _ROTATION_WORDS if kind == "rot" else _DURATION_WORDS if kind == "dur" else _SPEED_WORDS
            return words.get(u, 0.0)
        if u == "%":
            return n / 100.0
        if u in ("hz", "rpm", "bpm", "s", "ms") and scale > 0:
            v = n / 60.0 if u in ("rpm", "bpm") else n / 1000.0 if u == "ms" else n
            return v / scale
        return n

    def _hz(self, key: str, e: tuple[Optional[float], str]) -> float:
        n, u = e
        if n is not None and u in ("hz", "bpm", ""):
            return n / 60.0 if u == "bpm" else n
        share = n / 100.0 if n is not None and u == "%" else _SPEED_WORDS.get(u)
        if share is None:
            return 0.0
        lo, hi = STROBE_HZ_UNKNOWN
        self.problems.append(f"{key}: strobe speed not in Hz in the file: {lo:g}-{hi:g} Hz assumed")
        return 0.0 if share <= 0.0 else lo + share * (hi - lo)

    def _beam(self, key: str, e: tuple[Optional[float], str]) -> float:
        n, u = e
        if n is not None and u in ("deg", ""):
            return n
        lens = self.lens_deg
        if lens is None:
            lens = ZOOM_UNKNOWN_DEG
            self.zoom_guess = True
            self.problems.append(f"{key}: beam angle not in the file: {lens[0]:g}-{lens[1]:g}° assumed")
        if n is not None and u == "%":
            return lens[0] + n / 100.0 * (lens[1] - lens[0])
        return lens[1] if u == "wide" else lens[0]

    @staticmethod
    def _dist(e: tuple[Optional[float], str], metres: list[float]) -> float:
        n, u = e
        if n is None:
            return 1.0 if u == "far" else 0.0
        if u == "%":
            return n / 100.0
        if u == "m" and metres and max(metres) > min(metres):
            return (n - min(metres)) / (max(metres) - min(metres))
        return 0.5 if u == "m" else n

    def _kelvin(self, key: str, e: tuple[Optional[float], str]) -> float:
        n, u = e
        if n is not None and u in ("k", ""):
            return n
        self.problems.append(f"{key}: colour temperature not in kelvin in the file: warm {CCT_WARM_K:g} K, cold "
                             f"{CCT_COLD_K:g} K assumed")
        if n is not None and u == "%":
            return CCT_WARM_K + max(0.0, min(1.0, n / 100.0)) * (CCT_COLD_K - CCT_WARM_K)
        return CCT_COLD_K if u in ("cold", "ctb") else CCT_WARM_K if u in ("warm", "cto") else (
            (CCT_WARM_K + CCT_COLD_K) / 2.0)


#: The channel words a channel that read ``other`` in Stage 5 takes from its first range (contract.CHANNEL_ATTRS).
_UPGRADE = frozenset(contract.CHANNEL_ATTRS) - {"intensity", "emitter", "wheel", "other"}


def parse(doc: Any, mode: str, *, type_id: str = "ofl", file: Optional[str] = None
          ) -> tuple[contract.TypeInfo, bool]:
    """One mode of an OFL fixture -> (TypeInfo, usable). Not usable: the mode (or the file) can't be read at all, and
    the type is left out. A matrix mode is usable but inert (footprint 0, problem line). Never raises."""
    info = _empty(type_id, file, mode)
    problems: list[str] = info["problems"]
    if not isinstance(doc, dict) or not isinstance(doc.get("modes"), list):
        problems.append("not an Open Fixture Library fixture (no modes list)")
        return info, False
    m = find_mode(doc, mode)
    if m is None:
        names = ", ".join(f"{x['name']!r}" + (f" ({x['shortName']})" if x.get("shortName") else "")
                          for x in modes(doc))
        problems.append(f"mode {mode!r} isn't in the file (its modes: {names or 'none'})")
        return info, False
    info["mode"] = m.get("name") if isinstance(m.get("name"), str) else mode
    chans = m.get("channels")
    if not isinstance(chans, list):
        problems.append(f"mode {info['mode']!r} has no channel list")
        return info, False
    st = _Mode(doc, chans, problems, m.get("physical"))
    if any(_is_matrix_entry(e, st.known, st.switch) for e in chans):
        if "matrix" in doc or "templateChannels" in doc:
            problems.append(f"mode {info['mode']!r} uses pixel (matrix) channels, which aren't built in Stage 5: this "
                            f"type drives nothing and sends nothing")
            return info, True
    out: list[contract.AttrChannel] = []
    wheel_taken = False
    colour_wheel: Optional[str] = None
    coarse: list[tuple[contract.AttrChannel, str, dict, int]] = []      # (channel, key, classify's extra, res)
    for off, entry in enumerate(chans):
        if entry is None:
            continue                                                  # a gap: 0, no channel
        if not isinstance(entry, str):
            problems.append(f"channel {off + 1}: {entry!r} isn't a channel name: sends 0")
            continue
        if entry in st.switch and entry not in st.known:
            out.append({"offset": off, "name": entry, "attr": "other", "default": st.switch_default(entry)})
            continue
        if entry not in st.known:
            problems.append(f"channel {off + 1}: {entry!r} isn't defined in availableChannels: sends 0")
            continue
        key, k = st.known[entry]
        value, res, why = st.default(key)
        if why:
            problems.append(f"{key}: {why}")
        if k > 0:                                                     # a fine alias: its byte of the coarse default
            out.append({"offset": off, "name": entry, "attr": "other", "default": _byte(value, res, k)})
            continue
        attr, extra = st.classify(key)
        if extra.get("why") and attr == "other" and "light_open" in extra:
            problems.append(f"{key}: {extra['why']}: not driven, held at {_split(value, res)[0]} (pin it to change)")
        elif extra.get("why") and attr == "other":
            problems.append(f"{key}: {extra['why']}: not driven")
        if attr == "wheel":
            if wheel_taken:
                problems.append(f"{key}: a second colour wheel: not driven")
                attr = "other"
            else:
                slots = st.wheel_slots(key, extra["wheel_name"])
                if slots:
                    info["wheel"] = slots
                    wheel_taken = True
                    colour_wheel = extra["wheel_name"]
                else:
                    problems.append(f"{key}: no whole colour slot on its wheel: not driven")
                    attr = "other"
        ac: contract.AttrChannel = {"offset": off, "name": entry, "attr": attr, "default": _split(value, res)[0]}
        if attr == "emitter":
            ac["emitter"] = extra["emitter"]
        fines = st.avail[key].get("fineChannelAliases") or []
        if fines and fines[0] in st.offset:
            ac["fine"] = st.offset[fines[0]]
        out.append(ac)
        coarse.append((ac, key, extra, res))
    info["wheels"] = st.assign_wheels(colour_wheel)
    pan_tilt: dict[str, Optional[float]] = {}
    served: set[str] = set()
    for ac, key, extra, res in coarse:
        ranges = st.cap_ranges(key, pan_tilt)
        ac["ranges"] = ranges
        if res > 1:
            ac["res"] = res
        if ac["attr"] == "other" and not extra.get("why") and ranges and ranges[0]["attr"] in _UPGRADE:
            ac["attr"] = ranges[0]["attr"]                            # a function Stage 5 didn't know: its word
        served |= {r["attr"] for r in ranges if r["attr"] not in ("intensity", "colour")}   # those two: as before
    info["channels"] = sorted(out, key=lambda c: c["offset"])
    info["footprint"] = len(chans)
    zooms = [v for c in out for r in c.get("ranges") or () if r["attr"] == "zoom" for v in (r["lo"], r["hi"])]
    info["physical"] = {"lens_deg": list(st.lens_deg) if st.lens_deg else None, "pan_deg": pan_tilt.get("pan"),
                        "tilt_deg": pan_tilt.get("tilt"),
                        "zoom_deg": [min(zooms), max(zooms)] if zooms and not st.zoom_guess else None}
    kinds = {c["attr"] for c in out}
    attrs = set()
    if kinds & {"intensity", "emitter"}:
        attrs.add("intensity")
    if kinds & {"emitter", "wheel"}:
        attrs.add("colour")
    info["attrs"] = [a for a in contract.ATTRS if a in attrs | served]
    info["problems"] = list(dict.fromkeys(problems))                  # a coarse and its fine say it once
    return info, True


def load(path: pathlib.Path, mode: str, *, type_id: Optional[str] = None, file: Optional[str] = None
         ) -> tuple[contract.TypeInfo, bool]:
    """``parse`` on a file: (TypeInfo, usable). An unreadable file is not usable (problem inside)."""
    path = pathlib.Path(path)
    tid = type_id or path.stem
    doc, why = read(path)
    if doc is None:
        info = _empty(tid, file, mode)
        info["problems"].append(f"{file or path.name}: {why}")
        return info, False
    return parse(doc, mode, type_id=tid, file=file)


def load_ofl(path: pathlib.Path, mode: str, *, type_id: Optional[str] = None,
             file: Optional[str] = None) -> contract.TypeInfo:
    """README S5.4's ``fixtures.load_ofl``: the TypeInfo for one mode of an OFL file; problems inside, never raises."""
    return load(path, mode, type_id=type_id, file=file)[0]
