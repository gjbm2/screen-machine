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
  * ``ShutterStrobe`` -> ``shutter`` (never driven in Stage 5: it stays at its default, open);
  * ``Pan``, ``Tilt`` -> ``pan``, ``tilt`` (position: not driven in Stage 5);
  * anything else -> ``other``.
The engine drives ``intensity`` and ``emitter`` channels linearly over their whole range, so a channel is driven only
when every capability on it is that same function (or ``NoFunction``). A channel that mixes functions (a
"Dimmer/Strobe": intensity up to 127, strobe above) or whose intensity runs backwards reads ``other`` with a problem
line: driving it to full would strobe.

**Fine channels.** A mode that carries a channel's ``fineChannelAliases`` gives the coarse channel its ``fine`` offset
(16-bit values, MSB first). Each fine channel is also listed, as ``other``, with its byte of the coarse channel's
default; a second fine (24-bit) stays at its default. ``dmxValueResolution`` (default: the finest the channel has)
scales ``defaultValue`` and every ``dmxRange``.

**Defaults** (what an undriven channel sends), in this order: OFL's ``defaultValue`` (a number at the channel's
resolution, or a ``"50%"`` string); for a shutter, the start of its first ``shutterEffect: "Open"`` capability (a
shutter left at 0 is often dark, §9.2); for a mixed channel with an intensity range, the brightest point of that range
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
            "attrs": [], "channels": [], "emitters": {}, "wheel": [], "curve": "srgb", "problems": []}


class _Mode:
    """One mode's channels, resolved (``parse``'s working state)."""

    def __init__(self, doc: dict, chans: list, problems: list[str]) -> None:
        self.avail = doc.get("availableChannels") if isinstance(doc.get("availableChannels"), dict) else {}
        self.wheels = doc.get("wheels") if isinstance(doc.get("wheels"), dict) else {}
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
    st = _Mode(doc, chans, problems)
    if any(_is_matrix_entry(e, st.known, st.switch) for e in chans):
        if "matrix" in doc or "templateChannels" in doc:
            problems.append(f"mode {info['mode']!r} uses pixel (matrix) channels, which aren't built in Stage 5: this "
                            f"type drives nothing and sends nothing")
            return info, True
    out: list[contract.AttrChannel] = []
    wheel_taken = False
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
                else:
                    problems.append(f"{key}: no whole colour slot on its wheel: not driven")
                    attr = "other"
        ac: contract.AttrChannel = {"offset": off, "name": entry, "attr": attr, "default": _split(value, res)[0]}
        if attr == "emitter":
            ac["emitter"] = extra["emitter"]
        fines = st.avail[key].get("fineChannelAliases") or []
        if attr in ("intensity", "emitter", "wheel", "pan", "tilt") and fines and fines[0] in st.offset:
            ac["fine"] = st.offset[fines[0]]
        out.append(ac)
    info["channels"] = sorted(out, key=lambda c: c["offset"])
    info["footprint"] = len(chans)
    kinds = {c["attr"] for c in out}
    attrs = []
    if kinds & {"intensity", "emitter"}:
        attrs.append("intensity")
    if kinds & {"emitter", "wheel"}:
        attrs.append("colour")
    info["attrs"] = attrs
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
