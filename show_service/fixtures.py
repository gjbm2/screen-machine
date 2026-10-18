"""fixtures: the rig's registry, config/room/fixtures.yaml resolved (SHOW_ENGINE §9.2; README "Stage 5", S5.2, S5.4).

Pure: no threads, no network. ``Registry.poll`` runs on show-brain (every RELOAD_S); ``Registry.view`` gives any thread
the current ``contract.RigView``, swapped by reference and never mutated after the swap.

**Load rules** (S5.2, as vibes.py's):
  * a YAML error, an unreadable file or a file that isn't a mapping keeps the last good registry, with ``error`` set
    (``load`` alone returns empty maps with the error);
  * a value it can't use drops that thing, or that key to its unset value, with a page-words line in ``problems``
    (``"fixtures.spot.ball1: address 510 + footprint 4 runs past 512: left out"``);
  * an unknown key is ignored with a problem line, except under ``outputs.<id>``, where every key the schema doesn't
    name goes to the output's ``settings`` for its driver (``streamer``, room's switch, is the exception: a problem);
  * ``Registry.poll`` reloads on any mtime or size change of fixtures.yaml, a type file it read, latency.json or
    patch.yaml (debounced: the same change seen on two polls in a row, so a half-written file is never loaded), and
    at once when show.yaml's ``looks.align_pct`` / ``looks.min_visible_ms`` or ``lamps`` change.

**What is left out, and what is kept.** A type that can't be read (no file, no such mode), a fixture with no usable
type, a fixture on an artnet output with no ``address`` (or one running past channel 512), and a fixture on a hue
output with no ``light`` are left out. A fixture naming an unknown output is kept, unrouted. Overlapping fixtures in a
universe are both kept (the later address wins on the shared channels). A matrix type is kept but inert (ofl.py).
An output naming an unknown node, or Port-Address 0 (room_service's virtual universe), keeps that key unset, so it
can't start (``no_config``).

**Selectors** (LX-style; ``parse_selector``, ``select``): ``|`` separates OR clauses, spaces AND terms, ``!`` negates a
term; a term is ``key:value`` (``SELECTOR_KEYS``), a bare word is ``tag:<word>``, ``*`` is every fixture, and a value
ending in ``*`` is a prefix. ``role:`` matches a fixture's own role, else (given show.yaml ``lamps``) its placement's
role, else ``lamps.default_role`` for a lamp, as the director decides it. ``cap:<attr>`` (docs/LIGHTS.md §2.7) holds
when the fixture's type can show that ATTRS word (``cap:pan``, ``cap:gobo``); ``side:<word>`` and
``height:<low|mid|high>`` are the room's (space.py's ``spatial_fn``, passed as ``spatial``): without it they hold for
no fixture (an unplaced light is on no side). **Order:** members of the selector's positive ``group:`` terms first, in
the group's order (the first such group a fixture is in), then the rest by ``order``, then id.

**Latency** (``resolve_latency``; S5.2): per edge, latency.json ``outputs.<fixture id>.<attr>``, then
``outputs.<the output's latency_key>.<attr>``, then fixtures.yaml's hand-set ms, then 0 ("unset"). A measured edge's
value is its perceived onset ``t10 + (t50 - t10) x (align_pct - 10) / 40`` plus the entry's ``trim_ms``.

**The added file** (README S5.23): config/room/fixtures.added.yaml holds the lights registered on the Lights page, in
fixtures.yaml's schema (its nodes, outputs, types and fixtures). ``Registry`` merges it under the hand file (the hand
file wins on an id clash, with a problem line; one that doesn't read is a problem line and the hand file loads alone)
and reloads on its changes too. Only ``Registrar`` writes it, whole and atomically, after checking the result with the
loader itself; nothing in code ever writes fixtures.yaml. ``GENERIC_TYPES`` are builtin channelled types (RGB, RGBW,
..., the movers) for a DMX light with no OFL file: ``GENERIC_RANGES`` gives their channels' ranges (starting points
for a typical head; the wheels re-divide by the light's setup ``slots.count``).

**The setup file** (docs/LIGHTS.md §2.6): config/room/fixtures.setup.yaml holds each light's settings from the Lights
page (home, soft limits, measured speeds, its range when the type doesn't say, its default beam, slot names, named
positions, its strobe cap), keyed by fixture id, for any fixture (hand-file ones too). Only ``SetupStore`` writes it,
whole and atomically; ``Registry`` merges each entry into ``FixtureInfo.setup`` (``{}`` when none) and reloads on its
changes. A key it can't use is dropped with a problem line; an id that isn't a light is kept in the file, with a
problem line; a file that doesn't read keeps the last good settings.

Config names live in config: nothing here branches on a fixture, output, node, group, zone, screen or role name. A
driver kind (``contract.DRIVERS``) and a builtin type (``BUILTIN_TYPES``) are fixed vocabulary words.
"""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import logging
import math
import os
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Union

from . import contract
from . import ofl
from .ofl import load_ofl  # noqa: F401  (README S5.4 names it fixtures.load_ofl)

log = logging.getLogger("show_service.fixtures")

ID_RE = r"^[a-z0-9][a-z0-9_.-]{0,63}$"     # fixture, output, node, type, group ids and tags (S5.2)
_ID = re.compile(ID_RE)
VERSION = 1
ALIGN_PCT = 25.0            # starting point (§5.5; show.yaml looks.align_pct): aim at the perceived onset, ~t20-t30
MIN_VISIBLE_MS = 40.0       # starting point (show.yaml looks.min_visible_ms): a pulse's visible width
UNIVERSE = 512              # DMX channels in a universe
PORT_ADDRESS_MAX = 32767    # Art-Net's 15-bit Port-Address
ARTNET_PORT = 6454          # Art-Net's UDP port. On a loopback address it is room_service's receiver: never a node
STREAMER_DEFAULT = "room"   # patch.yaml hue.streamer unset: room_service streams (room_service/hue/output.py)
HUE_KEYS: tuple[str, ...] = ("bridge", "area", "gamma", "stop_after_s")   # read from patch.yaml's hue block when unset
UNIVERSE_DRIVERS: tuple[str, ...] = ("artnet",)    # driver kinds whose fixtures sit in a DMX universe (need address)
LIGHT_DRIVERS: tuple[str, ...] = ("hue",)          # driver kinds whose fixtures are a bridge's lights (need light)
SELECTOR_KEYS: tuple[str, ...] = ("id", "kind", "type", "output", "driver", "zone", "placement", "role", "screen",
                                  "tag", "group", "cap", "side", "height")
SPATIAL_KEYS: tuple[str, ...] = ("side", "height")   # answered by ``select``'s ``spatial`` (space.spatial_fn)
HEIGHT_WORDS: tuple[str, ...] = ("low", "mid", "high")   # height:<word>: the room's thirds, floor to ceiling
TOP_KEYS: tuple[str, ...] = ("version", "outputs", "nodes", "types", "fixtures", "groups")
OUTPUT_KEYS: tuple[str, ...] = ("driver", "enabled", "node", "port_address", "latency_key", "latency", "hits",
                                "min_attack_ms", "rate_hz")
NODE_KEYS: tuple[str, ...] = ("match", "last_ip", "port", "full_512", "failsafe", "virtual")
TYPE_KEYS: tuple[str, ...] = ("source", "file", "mode", "emitters", "curve")
FIXTURE_KEYS: tuple[str, ...] = ("type", "label", "output", "address", "light", "screen", "zone", "placement", "role",
                                 "tags", "order", "wb", "curve", "pin", "house")
GROUP_KEYS: tuple[str, ...] = ("select", "order")
_MAC = re.compile(r"^[0-9a-f]{2}([:-][0-9a-f]{2}){5}$")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _builtin(tid: str, kind: str, attrs: tuple[str, ...]) -> contract.TypeInfo:
    return {"id": tid, "source": "builtin", "kind": kind, "file": None, "mode": None, "footprint": 0,
            "attrs": list(attrs), "channels": [], "emitters": {}, "wheel": [], "curve": "srgb", "problems": [],
            "wheels": {}, "physical": ofl.no_physical()}


#: A generic type's channel words -> (attr, emitter), as ofl.py would read them (README S5.23; docs/LIGHTS.md §2.5).
#: Intensity and emitters are driven by colour_mix (``uv`` never takes part); the movers' channels by fixture_encode
#: through GENERIC_RANGES. A Strobe is a shutter held at 0 ("no strobe" on generic LED lights: no ranges, never
#: driven). A "<name> fine" channel is the LSB of <name> (16-bit, MSB first).
GENERIC_CHANNELS: dict[str, tuple[str, Optional[str]]] = {
    "Dimmer": ("intensity", None), "Red": ("emitter", "red"), "Green": ("emitter", "green"),
    "Blue": ("emitter", "blue"), "White": ("emitter", "white"), "Amber": ("emitter", "amber"),
    "UV": ("emitter", "uv"), "Strobe": ("shutter", None), "Pan": ("pan", None), "Tilt": ("tilt", None),
    "Pan fine": ("other", None), "Tilt fine": ("other", None), "Pan/Tilt speed": ("pt_speed", None),
    "Shutter": ("shutter", None), "Colour wheel": ("wheel", None), "Gobo": ("gobo", None),
    "Gobo rotation": ("gobo_spin", None), "Prism": ("prism", None), "Zoom": ("zoom", None),
    "Focus": ("focus", None)}
#: a generic channel's resting value when nothing drives it (owner, 18 Oct: a mover must not sit aimed at 0, 0; the
#: shutter open)
GENERIC_DEFAULTS: dict[str, int] = {"Pan": 128, "Tilt": 128, "Shutter": 8}    # centre, centre, open
#: The generic channelled types (README S5.23): a DMX light with no Open Fixture Library file, registered on the
#: Lights page. id -> (label, its channels in DMX order).
GENERIC_TYPES: dict[str, tuple[str, tuple[str, ...]]] = {
    "generic-dimmer": ("Dimmer only (1 channel)", ("Dimmer",)),
    "generic-rgb": ("RGB light (3 channels)", ("Red", "Green", "Blue")),
    "generic-rgbw": ("RGBW light (4 channels)", ("Red", "Green", "Blue", "White")),
    "generic-rgba": ("RGBA light (4 channels)", ("Red", "Green", "Blue", "Amber")),
    "generic-rgbwa": ("RGBWA light (5 channels)", ("Red", "Green", "Blue", "White", "Amber")),
    "generic-rgbwauv": ("RGBWA + UV light (6 channels)", ("Red", "Green", "Blue", "White", "Amber", "UV")),
    "generic-drgb": ("Dimmer + RGB light (4 channels)", ("Dimmer", "Red", "Green", "Blue")),
    "generic-drgbw": ("Dimmer + RGBW light (5 channels)", ("Dimmer", "Red", "Green", "Blue", "White")),
    "generic-drgbs": ("Dimmer + RGB + strobe light (5 channels)", ("Dimmer", "Red", "Green", "Blue", "Strobe")),
    "generic-mover": ("Moving head: pan, tilt, dimmer, RGB (6 channels)", ("Pan", "Tilt", "Dimmer", "Red", "Green",
                                                                          "Blue")),
    "generic-mover-full": ("Moving head: pan, tilt (fine), speed, dimmer, shutter, colour wheel, gobo, gobo spin, "
                           "prism, zoom, focus (13 channels)",
                           ("Pan", "Pan fine", "Tilt", "Tilt fine", "Pan/Tilt speed", "Dimmer", "Shutter",
                            "Colour wheel", "Gobo", "Gobo rotation", "Prism", "Zoom", "Focus")),
}
#: The generic channels' ranges (contract.CapRange, 8-bit; a channel with its fine is scaled to 16 bit). Starting
#: points: a common budget mover's layout; check the light's manual. An OFL file is the real answer, and the light's
#: setup ``range`` / ``slots`` override. "slots:<n>" is a wheel of n equal slots (GENERIC_WHEELS), re-divided by setup
#: ``slots.count``.
GENERIC_RANGES: dict[str, Any] = {
    "Pan": [{"dmx": [0, 255], "cap": "Pan", "attr": "pan", "lo": -270, "hi": 270}],          # 540° (setup range wins)
    "Tilt": [{"dmx": [0, 255], "cap": "Tilt", "attr": "tilt", "lo": -135, "hi": 135}],       # 270°
    "Pan/Tilt speed": [{"dmx": [0, 255], "cap": "PanTiltSpeed", "attr": "pt_speed", "lo": 1.0, "hi": 0.0}],  # 0 = fast
    "Shutter": [{"dmx": [0, 7], "cap": "ShutterStrobe", "attr": "shutter", "mode": "closed", "lo": 0, "hi": 0},
                {"dmx": [8, 15], "cap": "ShutterStrobe", "attr": "shutter", "mode": "open", "lo": 0, "hi": 0},
                {"dmx": [16, 131], "cap": "ShutterStrobe", "attr": "shutter", "mode": "strobe", "lo": 1, "hi": 20},
                {"dmx": [132, 255], "cap": "ShutterStrobe", "attr": "shutter", "mode": "open", "lo": 0, "hi": 0}],
    "Colour wheel": "slots:8",    # 8 equal slots over 0..127 (slot 1 Open), then colour_spin: 128..191 CW, 192..255 CCW
    "Gobo": "slots:8",            # 8 equal slots over 0..63 (slot 1 Open), shake 64..127 per slot, gobo_scroll above
    "Gobo rotation": [{"dmx": [0, 3], "cap": "WheelSlotRotation", "attr": "gobo_spin", "lo": 0, "hi": 0},
                      {"dmx": [4, 127], "cap": "WheelSlotRotation", "attr": "gobo_spin", "lo": 0.01, "hi": 1.0},
                      {"dmx": [128, 131], "cap": "WheelSlotRotation", "attr": "gobo_spin", "lo": 0, "hi": 0},
                      {"dmx": [132, 255], "cap": "WheelSlotRotation", "attr": "gobo_spin", "lo": -0.01, "hi": -1.0}],
    "Prism": [{"dmx": [0, 127], "cap": "WheelSlot", "attr": "prism", "lo": 0, "hi": 0, "slot": 0},
              {"dmx": [128, 255], "cap": "Prism", "attr": "prism", "lo": 1, "hi": 1, "slot": 1}],
    "Zoom": [{"dmx": [0, 255], "cap": "Zoom", "attr": "zoom", "lo": ofl.ZOOM_UNKNOWN_DEG[0],
              "hi": ofl.ZOOM_UNKNOWN_DEG[1]}],                                             # ZOOM_UNKNOWN_DEG
    "Focus": [{"dmx": [0, 255], "cap": "Focus", "attr": "focus", "lo": 0.0, "hi": 1.0}],
}
#: The generic wheels: channel word -> (the setup ``slots.count`` key, the wheel's attr, its slots' span on the
#: channel, the slot type, the rest of the channel: (attr, cap, [lo, hi], speed lo, speed hi) per range; "shake" is
#: one WheelShake range per slot over that span).
GENERIC_WHEELS: dict[str, dict[str, Any]] = {
    "Colour wheel": {"count": "colour", "attr": "colour_slot", "kind": "colour", "span": (0, 127), "slot_type": "Color",
                     "rest": [("colour_spin", "WheelRotation", (128, 191), 0.01, 1.0),
                              ("colour_spin", "WheelRotation", (192, 255), -0.01, -1.0)]},
    "Gobo": {"count": "gobo", "attr": "gobo", "kind": "gobo", "span": (0, 63), "slot_type": "Gobo",
             "shake": (64, 127),
             "rest": [("gobo_scroll", "WheelRotation", (128, 191), 0.01, 1.0),
                      ("gobo_scroll", "WheelRotation", (192, 255), -0.01, -1.0)]},
}
GENERIC_SLOTS = 8                   # starting point: a generic wheel's slots until the light's setup says (slots.count)
SLOT_COUNT_MAX = 32                 # the most slots setup ``slots.count`` may divide a generic wheel into


def _split_span(lo: int, hi: int, n: int) -> list[tuple[int, int]]:
    """``n`` equal DMX spans over [lo, hi] (inclusive), in order."""
    w = hi - lo + 1
    return [(lo + (k * w) // n, lo + ((k + 1) * w) // n - 1) for k in range(n)]


def _wheel_ranges(word: str, n: int) -> tuple[list[dict], dict[str, Any]]:
    """A generic wheel channel's ranges for ``n`` slots (slot 1 Open), and its TypeInfo.wheels entry."""
    g = GENERIC_WHEELS[word]
    out: list[dict] = []
    spans = _split_span(g["span"][0], g["span"][1], n)
    slot_word = "Pattern" if g["kind"] == "gobo" else "Colour"
    slots = []
    for k, (a, b) in enumerate(spans, start=1):
        name = "Open" if k == 1 else f"{slot_word} {k}"
        out.append({"dmx": [a, b], "cap": "WheelSlot", "attr": g["attr"], "lo": k, "hi": k, "slot": k, "word": name})
        slots.append({"slot": k, "type": "Open" if k == 1 else g["slot_type"], "name": name,
                      **({"hex": "#ffffff"} if k == 1 else {})})
    if g.get("shake"):
        for k, (a, b) in enumerate(_split_span(g["shake"][0], g["shake"][1], n), start=1):
            out.append({"dmx": [a, b], "cap": "WheelShake", "attr": g["attr"] + "_shake", "lo": 0.01, "hi": 1.0,
                        "slot": k})
    for attr, cap, (a, b), lo, hi in g["rest"]:
        out.append({"dmx": [a, b], "cap": cap, "attr": attr, "lo": lo, "hi": hi})
    return out, {"kind": g["kind"], "attr": g["attr"], "slots": slots}


def _generic(tid: str, names: tuple[str, ...], counts: Optional[dict[str, int]] = None) -> contract.TypeInfo:
    """A generic type's TypeInfo; ``counts``: setup ``slots.count`` ({"gobo": n, "colour": n}) for its wheels."""
    t = _builtin(tid, "lamp", ())
    counts = counts or {}
    at = {n: off for off, n in enumerate(names)}
    chans: list[contract.AttrChannel] = []
    served: set[str] = set()
    for off, n in enumerate(names):
        attr, em = GENERIC_CHANNELS[n]
        ch: contract.AttrChannel = {"offset": off, "name": n, "attr": attr, "default": GENERIC_DEFAULTS.get(n, 0)}
        if em is not None:
            ch["emitter"] = em
        fine = at.get(f"{n} fine")
        if fine is not None:
            ch["fine"] = fine
        spec = GENERIC_RANGES.get(n)
        if isinstance(spec, str):
            ranges, wheel = _wheel_ranges(n, max(1, min(SLOT_COUNT_MAX, int(counts.get(
                GENERIC_WHEELS[n]["count"]) or GENERIC_SLOTS))))
            t["wheels"][n] = wheel
        else:
            ranges = copy.deepcopy(spec) if spec else None
        if ranges is not None:
            if fine is not None:                                     # 16-bit: 0..255 -> 0..65535 (x 257)
                for r in ranges:
                    r["dmx"] = [r["dmx"][0] * 257, r["dmx"][1] * 257 + (256 if r["dmx"][1] < 255 else 0)]
                ch["res"] = 2
            ch["ranges"] = ranges
            served |= {r["attr"] for r in ranges}
        chans.append(ch)
    kinds = {c["attr"] for c in chans}
    attrs = set()
    if kinds & {"intensity", "emitter"}:
        attrs.add("intensity")
    if kinds & {"emitter", "wheel"}:
        attrs.add("colour")
    t["channels"], t["footprint"] = chans, len(chans)
    t["attrs"] = [a for a in contract.ATTRS if a in attrs | served]
    return t


_GENERIC_CACHE: dict[tuple, contract.TypeInfo] = {}


def generic_type(tid: str, counts: Optional[dict[str, int]] = None) -> Optional[contract.TypeInfo]:
    """A generic type re-divided for a light's setup ``slots.count`` (fixture_encode's per-light plan); the builtin
    one when no count changes it; None for a type that isn't generic. Cached (one object per (type, counts))."""
    if tid not in GENERIC_TYPES:
        return None
    names = GENERIC_TYPES[tid][1]
    used = {g["count"] for w, g in GENERIC_WHEELS.items() if w in names}
    key_counts = tuple(sorted((k, int(v)) for k, v in (counts or {}).items() if k in used and _int(v) is not None
                              and int(v) != GENERIC_SLOTS))
    if not key_counts:
        return BUILTIN_TYPES[tid]
    key = (tid, key_counts)
    t = _GENERIC_CACHE.get(key)
    if t is None:
        if len(_GENERIC_CACHE) > 256:
            _GENERIC_CACHE.clear()
        t = _GENERIC_CACHE[key] = _generic(tid, names, dict(key_counts))
    return t


#: S5.2's builtin types. A fixtures.yaml type with ``source: builtin`` names one of these by its id.
BUILTIN_TYPES: dict[str, contract.TypeInfo] = {
    "sm-screen": _builtin("sm-screen", "screen", ("colour",)),           # a Frame TV: showbus (P4); never dimmed
    "sm-hue": _builtin("sm-hue", "lamp", ("intensity", "colour")),      # a Hue colour lamp: the hue driver's xy + bri
    **{tid: _generic(tid, chans) for tid, (_, chans) in GENERIC_TYPES.items()},
}
#: Page words for the builtin types (GET /api/v1/rig/types, the rig's fixture rows).
TYPE_LABELS: dict[str, str] = {"sm-screen": "Frame TV (a screen)", "sm-hue": "Hue colour lamp",
                               **{tid: label for tid, (label, _) in GENERIC_TYPES.items()}}


# -- value readers -----------------------------------------------------------------------------------------------------
def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def _int(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and math.isfinite(v) and v == int(v):
        return int(v)
    return None


def _is_id(v: Any) -> bool:
    return isinstance(v, str) and bool(_ID.match(v))


def parse_curve(v: Any) -> Optional[str]:
    """``srgb`` | ``linear`` | ``gamma:<g>`` (g > 0), canonical; None: not a curve."""
    if not isinstance(v, str):
        return None
    s = v.strip().lower()
    if s in contract.CURVES:
        return s
    if s.startswith("gamma:"):
        g = s[len("gamma:"):].strip()
        try:
            gv = float(g)
        except ValueError:
            return None
        return f"gamma:{gv:g}" if math.isfinite(gv) and gv > 0 else None
    return None


def _yaml(data: bytes) -> Any:
    """yaml.safe_load, through libyaml's CSafeLoader when it is there (the same result, ~10x faster: a reload runs
    on show-brain)."""
    import yaml
    return yaml.load(data, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))   # noqa: S506 (a safe loader)


def _rel(path: pathlib.Path) -> str:
    try:
        return str(pathlib.Path(path).resolve().relative_to(contract.REPO_ROOT))
    except (ValueError, OSError):
        return str(path)


# -- selectors ---------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Term:
    key: str                    # SELECTOR_KEYS
    value: str                  # without the trailing "*" of a prefix
    prefix: bool = False
    neg: bool = False
    every: bool = False         # "*": every fixture

    def __str__(self) -> str:
        body = "*" if self.every else f"{self.key}:{self.value}{'*' if self.prefix else ''}"
        return ("!" if self.neg else "") + body


@dataclass(frozen=True)
class Selector:
    """A parsed selector: OR of AND clauses (``parse_selector``). Hashable; ``str()`` gives it back canonical."""
    expr: str
    clauses: tuple[tuple[Term, ...], ...]

    def __str__(self) -> str:
        return " | ".join(" ".join(str(t) for t in c) for c in self.clauses)

    def group_terms(self) -> list[Term]:
        """The positive ``group:`` terms, in the order written (they order the result)."""
        return [t for c in self.clauses for t in c if t.key == "group" and not t.neg and not t.every]


def parse_selector(expr: str) -> Selector:
    """``expr`` -> Selector. ValueError("bad_selector: <why>") when it doesn't parse (README S5.2)."""
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("bad_selector: empty")
    clauses = []
    for raw in expr.split("|"):
        words = raw.split()
        if not words:
            raise ValueError(f"bad_selector: an empty clause in {expr!r}")
        terms = []
        for w in words:
            neg = w.startswith("!")
            body = w[1:] if neg else w
            if not body or body.startswith("!"):
                raise ValueError(f"bad_selector: {w!r} negates nothing")
            if body == "*":
                terms.append(Term("id", "", neg=neg, every=True))
                continue
            if ":" in body:
                key, value = body.split(":", 1)
                if key not in SELECTOR_KEYS:
                    raise ValueError(f"bad_selector: {key!r} isn't a key ({', '.join(SELECTOR_KEYS)})")
            else:
                key, value = "tag", body
            prefix = value.endswith("*")
            if prefix:
                value = value[:-1]
            if "*" in value:
                raise ValueError(f"bad_selector: {w!r}: '*' only ends a prefix")
            if not value and not prefix:
                raise ValueError(f"bad_selector: {w!r} has no value")
            if key == "cap" and not prefix and value not in contract.ATTRS:
                raise ValueError(f"bad_selector: {w!r}: {value!r} isn't something a light can do "
                                 f"({', '.join(contract.ATTRS)})")
            if key == "height" and not prefix and value not in HEIGHT_WORDS:
                raise ValueError(f"bad_selector: {w!r}: height is {', '.join(HEIGHT_WORDS)}")
            terms.append(Term(key, value, prefix=prefix, neg=neg))
        clauses.append(tuple(terms))
    return Selector(expr, tuple(clauses))


class _Matcher:
    """Matches fixtures against selectors. ``groups`` gives a group's members (None: no such group); ``group_ids``
    lists the groups a prefix term may name."""

    def __init__(self, fixtures: dict, outputs: dict, groups: Callable[[str], Optional[list[str]]],
                 group_ids: Iterable[str], lamps: Optional[dict] = None, *, types: Optional[dict] = None,
                 spatial: Optional[Callable[[str, str, str], bool]] = None) -> None:
        self.fixtures = fixtures
        self.outputs = outputs
        self.types = types if isinstance(types, dict) else {}
        self.spatial = spatial
        self.groups = groups
        self.group_ids = sorted(group_ids)
        lamps = lamps if isinstance(lamps, dict) else {}
        pr = lamps.get("placement_roles")
        self.placement_roles = pr if isinstance(pr, dict) else {}
        dr = lamps.get("default_role")
        self.default_role = dr if isinstance(dr, str) else None

    def role(self, f: dict) -> Optional[str]:
        """The fixture's own role, else its placement's (show.yaml lamps), else the default role, for a lamp."""
        if f.get("role"):
            return f["role"]
        if f.get("kind") != "lamp":
            return None
        r = self.placement_roles.get(f.get("placement")) if f.get("placement") else None
        return r if isinstance(r, str) else self.default_role

    def _groups_named(self, t: Term) -> list[str]:
        if t.prefix:
            return [g for g in self.group_ids if g.startswith(t.value)]
        return [t.value]

    def _values(self, fid: str, f: dict, key: str) -> list[str]:
        if key == "id":
            return [fid]
        if key == "tag":
            return list(f.get("tags") or [])
        if key == "role":
            r = self.role(f)
            return [r] if r else []
        if key == "driver":
            o = self.outputs.get(f.get("output")) if f.get("output") else None
            return [o["driver"]] if o else []
        v = f.get(key)
        return [v] if isinstance(v, str) else []

    def term(self, t: Term, fid: str, f: dict) -> bool:
        if t.every:
            hit = True
        elif t.key == "group":
            hit = any(fid in (self.groups(g) or ()) for g in self._groups_named(t))
        elif t.key == "cap":
            ty = self.types.get(f.get("type"))
            attrs = (ty.get("attrs") if isinstance(ty, dict) else None) or ()
            hit = any(a.startswith(t.value) for a in attrs) if t.prefix else t.value in attrs
        elif t.key in SPATIAL_KEYS:
            hit = False
            if self.spatial is not None:
                try:
                    hit = bool(self.spatial(fid, t.key, t.value))
                except Exception:                                # noqa: BLE001  a bad answer: on no side
                    log.exception("fixtures: spatial(%s, %s, %s) failed", fid, t.key, t.value)
        else:
            vals = self._values(fid, f, t.key)
            hit = any(v.startswith(t.value) for v in vals) if t.prefix else t.value in vals
        return hit != t.neg

    def matches(self, sel: Selector, fid: str, f: dict) -> bool:
        return any(all(self.term(t, fid, f) for t in c) for c in sel.clauses)

    def select(self, sel: Selector) -> list[str]:
        ids = [fid for fid, f in self.fixtures.items() if self.matches(sel, fid, f)]
        ranked: list[list[str]] = []
        for t in sel.group_terms():
            for g in self._groups_named(t):
                ranked.append(list(self.groups(g) or ()))

        def key(fid: str) -> tuple:
            for gi, members in enumerate(ranked):
                if fid in members:
                    return (0, gi, members.index(fid), 0.0, "")
            o = self.fixtures[fid].get("order")
            return (1, 0 if o is not None else 1, 0, o if o is not None else 0.0, fid)
        return sorted(ids, key=key)


def select(expr: Union[str, Selector], rig: Optional[contract.RigView], *, lamps: Optional[dict] = None,
           spatial: Optional[Callable[[str, str, str], bool]] = None) -> list[str]:
    """The fixture ids ``expr`` selects in ``rig``, ordered (module docstring). A string that doesn't parse raises
    ValueError("bad_selector: ...") as ``parse_selector`` does: the caller writes the problem line, and whatever used
    it selects nothing. Pass show.yaml ``lamps`` for ``role:`` to follow placements, and space.py's ``spatial_fn(view)``
    for ``side:`` and ``height:`` (``spatial(fixture id, key, value) -> bool``; without it they hold for none)."""
    if not isinstance(expr, Selector):
        expr = parse_selector(expr)
    if not rig:
        return []
    groups = rig.get("groups") or {}
    return _Matcher(rig.get("fixtures") or {}, rig.get("outputs") or {}, groups.get, groups, lamps,
                    types=rig.get("types") or {}, spatial=spatial).select(expr)


# -- latency -----------------------------------------------------------------------------------------------------------
def _onset(entry: Any, edge: Optional[str], align_pct: float) -> Optional[float]:
    """A latency.json outputs entry's perceived onset for ``edge`` (rise | fall; None: rise, else the entry's own
    t10/t50) in ms, trim included; None when it has none."""
    if not isinstance(entry, dict):
        return None
    if edge is None:
        e = entry.get("rise") if isinstance(entry.get("rise"), dict) else entry
    else:
        e = entry.get(edge)
    if not isinstance(e, dict):
        return None
    t10, t50 = _num(e.get("t10")), _num(e.get("t50"))
    if t50 is None:
        return None
    v = t50 if t10 is None else t10 + (t50 - t10) * (align_pct - 10.0) / 40.0
    return round(v + (_num(entry.get("trim_ms")) or 0.0), 3)


def resolve_latency(fixture_id: str, out: Optional[contract.OutputConfig], latency_doc: Optional[dict], *,
                    align_pct: float = ALIGN_PCT, min_visible_ms: float = MIN_VISIBLE_MS) -> contract.LatencyView:
    """L(fixture, kind) in ms (README S5.2): per lamp, then per output, then hand-set, then 0 ("unset")."""
    outs = latency_doc.get("outputs") if isinstance(latency_doc, dict) else None
    outs = outs if isinstance(outs, dict) else {}
    keys = [fixture_id] + ([out["latency_key"]] if out and out.get("latency_key") else [])
    hand = (out or {}).get("latency") or {}
    hand_i = hand.get("intensity") if isinstance(hand.get("intensity"), dict) else {}

    def measured(kind: str, edge: Optional[str]) -> Optional[tuple[float, str]]:
        for k in keys:
            d = outs.get(k)
            if not isinstance(d, dict):
                continue
            for attr in contract.LATENCY_ATTRS[kind]:
                v = _onset(d.get(attr), edge, align_pct)
                if v is not None:
                    return v, f"latency.json outputs.{k}.{attr}"
        return None

    def handset(v: Any) -> Optional[tuple[float, str]]:
        n = _num(v)
        return (n, "fixtures.yaml") if n is not None else None

    unset = (0.0, "unset")
    rise = measured("intensity", "rise") or handset(hand_i.get("rise_ms")) or unset
    fall = measured("intensity", "fall") or handset(hand_i.get("fall_ms")) or unset
    colour = measured("colour", None) or handset(hand.get("colour_ms"))
    if colour is None:
        colour = (rise[0], f"{rise[1]} (the intensity rise)") if rise[1] != "unset" else unset
    position = measured("position", None) or handset(hand.get("position_ms")) or unset
    return {"rise_ms": rise[0], "fall_ms": fall[0], "colour_ms": colour[0], "position_ms": position[0],
            "min_pulse_ms": round(max(min_visible_ms, rise[0] - fall[0] + min_visible_ms), 3),
            "source": {"rise": rise[1], "fall": fall[1], "colour": colour[1], "position": position[1]}}


# -- hue ---------------------------------------------------------------------------------------------------------------
def _hue_ok(key: str, v: Any) -> bool:
    if v is None:
        return False
    if key in ("bridge", "area"):
        return isinstance(v, str) and bool(v.strip())
    n = _num(v)
    return n is not None and (n > 0 if key == "gamma" else n >= 0)


def hue_settings(out: Optional[contract.OutputConfig], patch: Optional[dict]) -> dict:
    """{bridge, area, gamma, stop_after_s, streamer, host}: the output's own settings (fixtures.yaml) over patch.yaml's
    hue block (68's, the one truth until P5). ``streamer`` is always patch.yaml's (default "room"). ``host`` is
    patch.yaml's ``hue.host``, only while the bridge is patch.yaml's own (room loads the pairing with
    ``BridgeSecrets.load(hue.bridge, hue.host)``: the host wins over the pairing file's address), else None (the
    pairing file's). Unusable values fall through to patch.yaml's, then None."""
    hue = patch.get("hue") if isinstance(patch, dict) else None
    hue = hue if isinstance(hue, dict) else {}
    own = (out or {}).get("settings") or {}
    res: dict[str, Any] = {}
    for k in HUE_KEYS:
        v = own.get(k)
        res[k] = v if _hue_ok(k, v) else (hue.get(k) if _hue_ok(k, hue.get(k)) else None)
    s = hue.get("streamer", STREAMER_DEFAULT)
    res["streamer"] = s if isinstance(s, str) and s.strip() else STREAMER_DEFAULT
    pb = hue.get("bridge") if _hue_ok("bridge", hue.get("bridge")) else None
    h = hue.get("host")
    res["host"] = h.strip() if isinstance(h, str) and h.strip() and res["bridge"] == pb else None
    return res


# -- the loader --------------------------------------------------------------------------------------------------------
def _empty_view(path: pathlib.Path, error: Optional[str], revision: str = "") -> contract.RigView:
    return {"revision": revision, "path": _rel(path), "error": error, "problems": [], "outputs": {}, "nodes": {},
            "types": {}, "fixtures": {}, "groups": {}}


def _looks_numbers(looks: Optional[dict], problems: list[str]) -> tuple[float, float]:
    looks = looks if isinstance(looks, dict) else {}
    a = looks.get("align_pct", ALIGN_PCT)
    an = _num(a)
    if an is None or not 0.0 <= an <= 100.0:
        problems.append(f"looks.align_pct: {a!r} isn't 0..100: using {ALIGN_PCT:g} (starting point) for latencies")
        an = ALIGN_PCT
    m = looks.get("min_visible_ms", MIN_VISIBLE_MS)
    mn = _num(m)
    if mn is None or mn < 0:
        problems.append(f"looks.min_visible_ms: {m!r} isn't a number of ms: using {MIN_VISIBLE_MS:g} (starting point)")
        mn = MIN_VISIBLE_MS
    return an, mn


class _Builder:
    """One load of fixtures.yaml (``_load``). Collects problems and the type files it read."""

    def __init__(self, types_dir: pathlib.Path, latency: Optional[dict], looks: Optional[dict],
                 lamps: Optional[dict]) -> None:
        self.types_dir = pathlib.Path(types_dir)
        self.latency = latency
        self.lamps = lamps if isinstance(lamps, dict) else None
        self.problems: list[str] = []
        self.used: list[pathlib.Path] = []
        self.align_pct, self.min_visible_ms = _looks_numbers(looks, self.problems)

    # -- helpers -------------------------------------------------------------------------------------------------------
    def _section(self, raw: dict, name: str) -> dict:
        v = raw.get(name)
        if v is None:
            return {}
        if not isinstance(v, dict):
            self.problems.append(f"{name}: not a mapping: ignored")
            return {}
        return v

    def _entry(self, where: str, eid: Any, v: Any, keys: Optional[tuple[str, ...]]) -> Optional[dict]:
        if not _is_id(eid):
            self.problems.append(f"{where}: {eid!r} isn't an id ({ID_RE}): left out")
            return None
        if v is None:
            v = {}
        if not isinstance(v, dict):
            self.problems.append(f"{where}: not a mapping: left out")
            return None
        if keys is not None:
            for k in v:
                if k not in keys:
                    self.problems.append(f"{where}.{k}: unknown key: ignored")
        return v

    def _bool(self, where: str, d: dict, key: str, default: bool, problems: Optional[list[str]] = None) -> bool:
        v = d.get(key, default)
        if isinstance(v, bool):
            return v
        (problems if problems is not None else self.problems).append(
            f"{where}.{key}: {v!r} isn't true or false: {str(default).lower()}")
        return default

    def _opt_str(self, where: str, d: dict, key: str) -> Optional[str]:
        v = d.get(key)
        if v is None:
            return None
        if isinstance(v, str) and v.strip():
            return v
        self.problems.append(f"{where}.{key}: {v!r} isn't a name: unset")
        return None

    # -- sections ------------------------------------------------------------------------------------------------------
    def nodes(self, sec: dict) -> dict[str, contract.NodeConfig]:
        out: dict[str, contract.NodeConfig] = {}
        for nid, n in sec.items():
            where = f"nodes.{nid}"
            n = self._entry(where, nid, n, NODE_KEYS)
            if n is None:
                continue
            match: dict[str, str] = {}
            m = n.get("match")
            if m is not None:
                mac = m.get("mac") if isinstance(m, dict) else None
                sn = m.get("short_name") if isinstance(m, dict) else None
                if isinstance(m, dict) and len(m) == 1 and isinstance(mac, str) and _MAC.match(mac.strip().lower()):
                    match = {"mac": mac.strip().lower().replace("-", ":")}
                elif isinstance(m, dict) and len(m) == 1 and isinstance(sn, str) and sn.strip():
                    match = {"short_name": sn}
                else:
                    self.problems.append(f"{where}.match: {m!r} isn't {{mac: aa:bb:cc:dd:ee:ff}} or {{short_name: ...}}"
                                         f": unset (any reply at last_ip counts)")
            last_ip = None
            ip = n.get("last_ip")
            if ip is not None:
                try:
                    last_ip = str(ipaddress.IPv4Address(str(ip).strip()))
                except ValueError:
                    self.problems.append(f"{where}.last_ip: {ip!r} isn't an IPv4 address: unset (nothing is sent)")
            port = _int(n.get("port", ARTNET_PORT))
            if port is None or not 1 <= port <= 65535:
                self.problems.append(f"{where}.port: {n.get('port')!r} isn't a UDP port: {ARTNET_PORT}")
                port = ARTNET_PORT
            fs = n.get("failsafe")
            if fs is not None and fs not in contract.FAILSAFE_MODES:
                self.problems.append(f"{where}.failsafe: {fs!r} isn't one of {', '.join(contract.FAILSAFE_MODES)}: "
                                     f"unset (the node's own)")
                fs = None
            virtual = self._bool(where, n, "virtual", False)
            if last_ip and ipaddress.IPv4Address(last_ip).is_loopback and port == ARTNET_PORT and not virtual:
                self.problems.append(f"{where}: {last_ip}:{port} is room_service's own Art-Net receiver: never sent "
                                     f"to (treated as virtual)")
                virtual = True
            out[nid] = {"id": nid, "match": match, "last_ip": last_ip, "port": port,
                        "full_512": self._bool(where, n, "full_512", False), "failsafe": fs, "virtual": virtual}
        return out

    def _hand_latency(self, v: Any, where: str, probs: list[str]) -> dict[str, Any]:
        if v is None:
            return {}
        if not isinstance(v, dict):
            probs.append(f"{where}.latency: not a mapping: unset")
            return {}
        out: dict[str, Any] = {}

        def ms(path: str, x: Any) -> Optional[float]:
            if x is None:
                return None
            n = _num(x)
            if n is None or n < 0:
                probs.append(f"{where}.latency.{path}: {x!r} isn't a number of ms: unset")
                return None
            return n
        for k, x in v.items():
            if k == "intensity":
                if x is None:
                    continue
                if not isinstance(x, dict):
                    probs.append(f"{where}.latency.intensity: not a mapping of rise_ms, fall_ms: unset")
                    continue
                edges = {}
                for e, y in x.items():
                    if e not in ("rise_ms", "fall_ms"):
                        probs.append(f"{where}.latency.intensity.{e}: unknown key: ignored")
                        continue
                    n = ms(f"intensity.{e}", y)
                    if n is not None:
                        edges[e] = n
                if edges:
                    out["intensity"] = edges
            elif k in ("colour_ms", "position_ms"):
                n = ms(k, x)
                if n is not None:
                    out[k] = n
            else:
                probs.append(f"{where}.latency.{k}: unknown key: ignored")
        return out

    def outputs(self, sec: dict, nodes: dict) -> dict[str, contract.OutputConfig]:
        out: dict[str, contract.OutputConfig] = {}
        for oid, o in sec.items():
            where = f"outputs.{oid}"
            o = self._entry(where, oid, o, None)
            if o is None:
                continue
            drv = o.get("driver")
            if drv not in contract.DRIVERS:
                self.problems.append(f"{where}.driver: {drv!r} isn't one of {', '.join(contract.DRIVERS)}: left out")
                continue
            probs: list[str] = []
            enabled = self._bool(where, o, "enabled", False, probs)
            node = o.get("node")
            if node is not None and node not in nodes:
                probs.append(f"{where}.node: {node!r} isn't in nodes: unset (the output can't start)")
                node = None
            pa = o.get("port_address")
            if pa is not None:
                pai = _int(pa)
                if pai == 0:
                    probs.append(f"{where}.port_address: 0 is room_service's virtual universe: refused (the output "
                                 f"can't start)")
                    pa = None
                elif pai is None or not 1 <= pai <= PORT_ADDRESS_MAX:
                    probs.append(f"{where}.port_address: {pa!r} isn't 1..{PORT_ADDRESS_MAX}: unset (the output "
                                 f"can't start)")
                    pa = None
                else:
                    pa = pai
            if drv in UNIVERSE_DRIVERS:
                missing = [k for k in ("node", "port_address") if k not in o]
                if missing:
                    probs.append(f"{where}: an {drv} output needs {' and '.join(missing)}: it can't start until set")
            lk = o.get("latency_key", oid)
            if not isinstance(lk, str) or not lk.strip():
                probs.append(f"{where}.latency_key: {lk!r} isn't a key: {oid}")
                lk = oid
            hits = o.get("hits")
            if hits is not None and not isinstance(hits, bool):
                probs.append(f"{where}.hits: {hits!r} isn't true or false: the driver's own")
                hits = None
            mam = o.get("min_attack_ms")
            if mam is not None and (_num(mam) is None or _num(mam) < 0):
                probs.append(f"{where}.min_attack_ms: {mam!r} isn't a number of ms: the driver's own")
                mam = None
            rate = o.get("rate_hz")
            if rate is not None and (_num(rate) is None or _num(rate) <= 0):
                probs.append(f"{where}.rate_hz: {rate!r} isn't a rate: the driver's own")
                rate = None
            settings = {k: v for k, v in o.items() if k not in OUTPUT_KEYS}
            if "streamer" in settings:
                probs.append(f"{where}.streamer: room_service's switch, read from patch.yaml's hue block only: "
                             f"ignored")
                del settings["streamer"]
            if drv in LIGHT_DRIVERS:
                for k in HUE_KEYS:
                    if k in settings and settings[k] is not None and not _hue_ok(k, settings[k]):
                        probs.append(f"{where}.{k}: {settings[k]!r} isn't usable: patch.yaml's is used")
            hand = self._hand_latency(o.get("latency"), where, probs)
            self.problems.extend(probs)
            out[oid] = {"id": oid, "driver": drv, "enabled": enabled, "built": drv in contract.DRIVERS_BUILT,
                        "node": node, "port_address": pa, "latency_key": lk, "latency": hand,
                        "hits": hits, "min_attack_ms": _num(mam) if mam is not None else None,
                        "rate_hz": _num(rate) if rate is not None else None, "settings": settings, "problems": probs}
        return out

    def _type_file(self, where: str, file: str) -> Optional[pathlib.Path]:
        base = self.types_dir.resolve()
        p = (self.types_dir / file).resolve()
        try:
            p.relative_to(base)
        except ValueError:
            self.problems.append(f"{where}.file: {file!r} is outside {_rel(self.types_dir)}: left out")
            return None
        return p

    def _emitters(self, where: str, v: Any, info: contract.TypeInfo) -> dict[str, dict[str, Any]]:
        if v is None:
            return {}
        if not isinstance(v, dict):
            self.problems.append(f"{where}.emitters: not a mapping: ignored (starting points)")
            return {}
        has = {c.get("emitter") for c in info["channels"] if c.get("attr") == "emitter"}
        out = {}
        for e, d in v.items():
            if e not in contract.EMITTERS:
                self.problems.append(f"{where}.emitters.{e}: not an emitter ({', '.join(contract.EMITTERS)}): ignored")
                continue
            if e not in has:
                self.problems.append(f"{where}.emitters.{e}: this type has no {e} channel: ignored")
                continue
            xy = d.get("xy") if isinstance(d, dict) else None
            y = _num(d.get("Y")) if isinstance(d, dict) else None
            ok_xy = (isinstance(xy, list) and len(xy) == 2 and all(_num(c) is not None for c in xy)
                     and all(0.0 < float(c) < 1.0 for c in xy) and float(xy[0]) + float(xy[1]) <= 1.0)
            if not ok_xy or y is None or y <= 0:
                self.problems.append(f"{where}.emitters.{e}: needs xy: [x, y] (CIE 1931, 0..1) and Y > 0: ignored "
                                     f"(starting point)")
                continue
            out[e] = {"xy": [float(xy[0]), float(xy[1])], "Y": y, "source": "type"}
        return out

    def types(self, sec: dict) -> dict[str, contract.TypeInfo]:
        out: dict[str, contract.TypeInfo] = {}
        for tid, t in sec.items():
            where = f"types.{tid}"
            t = self._entry(where, tid, t, TYPE_KEYS)
            if t is None:
                continue
            src = t.get("source")
            if src == "builtin":
                if tid not in BUILTIN_TYPES:
                    self.problems.append(f"{where}: {tid!r} isn't a builtin type ({', '.join(BUILTIN_TYPES)}): "
                                         f"left out")
                    continue
                for k in ("file", "mode"):
                    if k in t:
                        self.problems.append(f"{where}.{k}: a builtin type has no {k}: ignored")
                info = copy.deepcopy(BUILTIN_TYPES[tid])
            elif src == "ofl":
                file, mode = t.get("file"), t.get("mode")
                if isinstance(mode, (int, float)) and not isinstance(mode, bool):
                    mode = f"{mode:g}" if isinstance(mode, float) else str(mode)
                if not isinstance(file, str) or not file.strip():
                    self.problems.append(f"{where}.file: required for an ofl type (ofl/<maker>/<model>.json): left out")
                    continue
                if not isinstance(mode, str) or not mode.strip():
                    self.problems.append(f"{where}.mode: required for an ofl type (tools/show/add_fixture.py lists "
                                         f"the file's modes): left out")
                    continue
                path = self._type_file(where, file)
                if path is None:
                    continue
                self.used.append(path)
                info, usable = ofl.load(path, mode, type_id=tid, file=file)
                self.problems.extend(f"{where}: {p}" for p in info["problems"])
                if not usable:
                    self.problems.append(f"{where}: can't be used: left out, with its fixtures")
                    continue
            else:
                self.problems.append(f"{where}.source: {src!r} isn't builtin or ofl: left out")
                continue
            info["emitters"] = self._emitters(where, t.get("emitters"), info)
            if "curve" in t:
                c = parse_curve(t.get("curve"))
                if c is None:
                    self.problems.append(f"{where}.curve: {t.get('curve')!r} isn't srgb, linear or gamma:<g>: srgb")
                else:
                    info["curve"] = c
            out[tid] = info
        return out

    def fixtures(self, sec: dict, types: dict, outputs: dict) -> dict[str, contract.FixtureInfo]:
        out: dict[str, contract.FixtureInfo] = {}
        roles = (self.lamps or {}).get("roles")
        roles = roles if isinstance(roles, dict) else None
        for fid, f in sec.items():
            where = f"fixtures.{fid}"
            f = self._entry(where, fid, f, FIXTURE_KEYS)
            if f is None:
                continue
            tid = f.get("type")
            if tid is None:
                self.problems.append(f"{where}.type: required: left out")
                continue
            if tid not in types:
                self.problems.append(f"{where}.type: {tid!r} isn't a usable type: left out")
                continue
            t = types[tid]
            label = f.get("label", fid)
            if not isinstance(label, str) or not label.strip():
                self.problems.append(f"{where}.label: {label!r} isn't text: {fid}")
                label = fid
            oid = f.get("output")
            if oid is not None and oid not in outputs:
                self.problems.append(f"{where}.output: {oid!r} isn't an output: unrouted")
                oid = None
            drv = outputs[oid]["driver"] if oid else None
            address = None
            if f.get("address") is not None:
                address = _int(f.get("address"))
                if address is None or not 1 <= address <= UNIVERSE:
                    self.problems.append(f"{where}.address: {f.get('address')!r} isn't 1..{UNIVERSE}: unset")
                    address = None
            light = f.get("light")
            if light is not None and not (isinstance(light, str) and light.strip()):
                self.problems.append(f"{where}.light: {light!r} isn't a light id: unset")
                light = None
            if drv in UNIVERSE_DRIVERS and address is None:
                self.problems.append(f"{where}: on an {drv} output it needs address (1..{UNIVERSE}): left out")
                continue
            if drv in LIGHT_DRIVERS and light is None:
                self.problems.append(f"{where}: on a {drv} output it needs light (the bridge's light id): left out")
                continue
            role = self._opt_str(where, f, "role")
            if role is not None and roles is not None and role not in roles:
                self.problems.append(f"{where}.role: {role!r} isn't a show.yaml lamps.roles name: unset (from "
                                     f"placement)")
                role = None
            tags: list[str] = []
            tv = f.get("tags")
            if tv is not None and not isinstance(tv, list):
                self.problems.append(f"{where}.tags: not a list: none")
            for tg in tv if isinstance(tv, list) else []:
                if _is_id(tg):
                    if tg not in tags:
                        tags.append(tg)
                else:
                    self.problems.append(f"{where}.tags: {tg!r} isn't a tag ({ID_RE}): dropped")
            order = f.get("order")
            if order is not None and _num(order) is None:
                self.problems.append(f"{where}.order: {order!r} isn't a number: unset")
                order = None
            wb: dict[str, float] = {}
            wv = f.get("wb")
            if wv is not None and not isinstance(wv, dict):
                self.problems.append(f"{where}.wb: not a mapping of emitter -> gain: 1.0 each")
            for e, g in (wv.items() if isinstance(wv, dict) else ()):
                gn = _num(g)
                if e not in contract.EMITTERS or gn is None or gn < 0:
                    self.problems.append(f"{where}.wb.{e}: {g!r} isn't an emitter gain (>= 0): 1.0")
                else:
                    wb[e] = gn
            curve = None
            if f.get("curve") is not None:
                curve = parse_curve(f.get("curve"))
                if curve is None:
                    self.problems.append(f"{where}.curve: {f.get('curve')!r} isn't srgb, linear or gamma:<g>: the "
                                         f"type's")
            pin: dict[str, int] = {}
            pv = f.get("pin")
            names = {c["name"] for c in t["channels"]}
            if pv is not None and not isinstance(pv, dict):
                self.problems.append(f"{where}.pin: not a mapping of channel name -> 0..255: none")
            for ch, val in (pv.items() if isinstance(pv, dict) else ()):
                vi = _int(val)
                if ch not in names:
                    self.problems.append(f"{where}.pin.{ch}: type {tid} has no channel {ch!r}: ignored")
                elif vi is None or not 0 <= vi <= 255:
                    self.problems.append(f"{where}.pin.{ch}: {val!r} isn't 0..255: ignored")
                else:
                    pin[ch] = vi
            house = None
            hv = f.get("house")
            if hv is not None:
                hx = hv.get("hex") if isinstance(hv, dict) else None
                hi = _num(hv.get("intensity", 1.0)) if isinstance(hv, dict) else None
                if isinstance(hx, str) and _HEX.match(hx) and hi is not None and 0.0 <= hi <= 1.0:
                    house = {"intensity": hi, "hex": hx.lower()}
                else:
                    self.problems.append(f"{where}.house: needs hex: \"#rrggbb\" and intensity 0..1: unset (dark)")
            out[fid] = {"id": fid, "label": label, "type": tid, "kind": t["kind"], "output": oid, "address": address,
                        "light": light, "screen": self._opt_str(where, f, "screen"),
                        "zone": self._opt_str(where, f, "zone"), "placement": self._opt_str(where, f, "placement"),
                        "role": role, "tags": tags, "order": _num(order) if order is not None else None, "wb": wb,
                        "curve": curve, "pin": pin, "house": house, "latency": {}, "setup": {}}
        self._shared_lights(out, outputs)
        return out

    def _shared_lights(self, fixtures: dict, outputs: dict) -> None:
        seen: dict[tuple[str, str], str] = {}
        for fid, f in fixtures.items():
            o = outputs.get(f["output"]) if f["output"] else None
            if not o or o["driver"] not in LIGHT_DRIVERS or not f["light"]:
                continue
            k = (f["output"], f["light"])
            if k in seen:
                self.problems.append(f"fixtures.{fid}.light: also fixtures.{seen[k]}'s light on output "
                                     f"{f['output']}: both kept")
            else:
                seen[k] = fid

    def universes(self, fixtures: dict, types: dict, outputs: dict) -> None:
        """Footprints past 512 leave the fixture out; overlaps in a universe are problem lines (both kept)."""
        owner: dict[tuple, str] = {}
        for oid, o in outputs.items():
            if o["driver"] in UNIVERSE_DRIVERS and o["node"] and o["port_address"]:
                k = (o["node"], o["port_address"])
                if k in owner:
                    self.problems.append(f"outputs.{oid}: node {k[0]} Port-Address {k[1]} is also outputs.{owner[k]}'s "
                                         f"universe: their fixtures share it")
                else:
                    owner[k] = oid
        spans: dict[tuple, list[tuple[int, int, str]]] = {}
        for fid in list(fixtures):
            f = fixtures[fid]
            o = outputs.get(f["output"]) if f["output"] else None
            if not o or o["driver"] not in UNIVERSE_DRIVERS:
                continue
            fp, a = types[f["type"]]["footprint"], f["address"]
            if fp <= 0:
                self.problems.append(f"fixtures.{fid}: type {f['type']} has no DMX channels: it sends nothing")
                continue
            if a + fp - 1 > UNIVERSE:
                self.problems.append(f"fixtures.{fid}: address {a} + footprint {fp} runs past {UNIVERSE}: left out")
                del fixtures[fid]
                continue
            key = (o["node"], o["port_address"]) if o["node"] and o["port_address"] else ("", f["output"])
            spans.setdefault(key, []).append((a, a + fp - 1, fid))
        for items in spans.values():
            items.sort()
            for i, (a, e, fid) in enumerate(items):
                for a2, e2, fid2 in items[i + 1:]:
                    if a2 > e:
                        break
                    self.problems.append(f"fixtures.{fid2}: channels {a2}-{e2} overlap fixtures.{fid} ({a}-{e}): both "
                                         f"kept; the later address wins on the shared channels")

    def groups(self, sec: dict, fixtures: dict, outputs: dict, types: Optional[dict] = None) -> dict[str, list[str]]:
        specs: dict[str, tuple[Selector, list[str]]] = {}
        for gid, g in sec.items():
            where = f"groups.{gid}"
            g = self._entry(where, gid, g, GROUP_KEYS)
            if g is None:
                continue
            expr = g.get("select")
            if not isinstance(expr, str):
                self.problems.append(f"{where}.select: required (a selector, e.g. \"kind:lamp\"): left out")
                continue
            try:
                sel = parse_selector(expr)
            except ValueError as e:
                self.problems.append(f"{where}.select: {e}: left out")
                continue
            order: list[str] = []
            ov = g.get("order")
            if ov is not None and not isinstance(ov, list):
                self.problems.append(f"{where}.order: not a list of fixture ids: ignored")
            for x in ov if isinstance(ov, list) else []:
                if x in fixtures:
                    if x not in order:
                        order.append(x)
                else:
                    self.problems.append(f"{where}.order: {x!r} isn't a fixture: skipped")
            specs[gid] = (sel, order)
        resolved: dict[str, list[str]] = {}
        resolving: list[str] = []

        def members(gid: str) -> Optional[list[str]]:
            if gid in resolved:
                return resolved[gid]
            if gid not in specs:
                return None
            if gid in resolving:
                self.problems.append(f"groups.{resolving[0]}: refers to itself through group:{gid}: that term "
                                     f"selects nothing")
                return None
            resolving.append(gid)
            sel, order = specs[gid]
            ids = _Matcher(fixtures, outputs, members, specs, self.lamps, types=types).select(sel)
            have = set(ids)
            resolving.pop()
            resolved[gid] = [x for x in order if x in have] + [x for x in ids if x not in order]
            return resolved[gid]
        for gid in specs:
            members(gid)
        return {gid: resolved.get(gid, []) for gid in specs}

    # -- the whole file ------------------------------------------------------------------------------------------------
    def build(self, raw: dict, path: pathlib.Path, revision: str) -> contract.RigView:
        version = raw.get("version")
        if version != VERSION:
            self.problems.append(f"version: {version!r} (this reader knows {VERSION})")
        for k in raw:
            if k not in TOP_KEYS:
                self.problems.append(f"{k}: unknown key: ignored")
        nodes = self.nodes(self._section(raw, "nodes"))
        outputs = self.outputs(self._section(raw, "outputs"), nodes)
        types = self.types(self._section(raw, "types"))
        fixtures = self.fixtures(self._section(raw, "fixtures"), types, outputs)
        self.universes(fixtures, types, outputs)
        for fid, f in fixtures.items():
            f["latency"] = resolve_latency(fid, outputs.get(f["output"]) if f["output"] else None, self.latency,
                                           align_pct=self.align_pct, min_visible_ms=self.min_visible_ms)
        groups = self.groups(self._section(raw, "groups"), fixtures, outputs, types)
        return {"revision": revision, "path": _rel(path), "error": None,
                "problems": list(dict.fromkeys(self.problems)), "outputs": outputs, "nodes": nodes, "types": types,
                "fixtures": fixtures, "groups": groups}


def _load(path: pathlib.Path, types_dir: pathlib.Path, latency: Optional[dict], looks: Optional[dict],
          lamps: Optional[dict], added_path: Optional[pathlib.Path] = None,
          setups: Optional[tuple[dict, bytes]] = None
          ) -> tuple[contract.RigView, list[pathlib.Path], bool, frozenset[str]]:
    """(view, the type files it read, ok, the fixture ids that came from the added file). Not ok: ``error`` set and
    empty maps. ``added_path``: fixtures.added.yaml, merged under the hand file (``merge_added``); None: none.
    ``setups``: (fixtures.setup.yaml's ``fixtures`` mapping, its bytes) to merge into each FixtureInfo.setup."""
    try:
        data = path.read_bytes()
    except OSError as e:
        return _empty_view(path, f"{path.name}: {e.strerror or e}"), [], False, frozenset()
    pre: list[str] = []
    added, added_data = _read_added(added_path, pre) if added_path is not None else (None, b"")
    h = hashlib.sha1(data)
    if added_data:
        h.update(b"\0" + added_data)
    if setups is not None and setups[1]:
        h.update(b"\0setup\0" + setups[1])
    revision = h.hexdigest()[:12]
    try:
        raw = _yaml(data)
        if not isinstance(raw, dict):
            raise ValueError("the file is not a mapping")
    except Exception as e:
        return _empty_view(path, f"{path.name}: {type(e).__name__}: {e}", revision), [], False, frozenset()
    ids: frozenset[str] = frozenset()
    if added is not None:
        raw, ids = merge_added(raw, added, pre)
    b = _Builder(types_dir, latency, looks, lamps)
    try:
        view = b.build(raw, path, revision)
    except Exception as e:                      # a bug here must not take the rig down: the last good view stays
        log.exception("fixtures: building the registry failed")
        return _empty_view(path, f"{path.name}: {type(e).__name__}: {e}", revision), b.used, False, frozenset()
    if setups is not None:
        attach_setup(view, setups[0], pre)
    if pre:
        view["problems"] = list(dict.fromkeys(pre + view["problems"]))
    return view, b.used, True, frozenset(i for i in ids if i in view["fixtures"])


def load(path: pathlib.Path = contract.FIXTURES_FILE, *, types_dir: pathlib.Path = contract.FIXTURE_TYPES_DIR,
         latency: Optional[dict] = None, patch: Optional[dict] = None, looks: Optional[dict] = None,
         lamps: Optional[dict] = None, added_path: Optional[pathlib.Path] = None,
         setup_path: Optional[pathlib.Path] = None) -> contract.RigView:
    """fixtures.yaml -> RigView. Never raises: a bad file gives ``error`` and empty maps (``Registry`` keeps the last
    good one). ``latency`` is latency.json, ``looks`` show.yaml's looks (align_pct, min_visible_ms), ``lamps``
    show.yaml's lamps (role names). ``patch`` (patch.yaml) isn't needed to build the view: drivers read
    ``hue_settings(out, patch)``. ``added_path``: a fixtures.added.yaml to merge under it (None: the hand file
    alone; ``Registry`` merges its sibling). ``setup_path``: a fixtures.setup.yaml to merge (None: every setup {})."""
    setups = None
    if setup_path is not None:
        sp: list[str] = []
        got, data = read_setup(pathlib.Path(setup_path), sp)
        setups = (got or {}, data)
    view = _load(pathlib.Path(path), pathlib.Path(types_dir), latency, looks, lamps,
                 pathlib.Path(added_path) if added_path is not None else None, setups)[0]
    if setup_path is not None and sp and view["error"] is None:
        view["problems"] = list(dict.fromkeys(sp + view["problems"]))
    return view


def summary(view: Optional[contract.RigView]) -> dict:
    """The registry block of the Rig snapshot (README S5.4): {revision, path, error, problems, counts}."""
    v = view or _empty_view(contract.FIXTURES_FILE, "not loaded yet")
    return {"revision": v["revision"], "path": v["path"], "error": v["error"], "problems": list(v["problems"]),
            "counts": {k: len(v[k]) for k in ("outputs", "nodes", "types", "fixtures", "groups")}}


# -- the added file (the Lights page's; README S5.23) ------------------------------------------------------------------
ADDED_NAME = "fixtures.added.yaml"
ADDED_SECTIONS: tuple[str, ...] = ("nodes", "outputs", "types", "fixtures")   # what it holds, in the order written
ADDED_HEADER = """\
# config/room/fixtures.added.yaml: the lights registered on the Lights page (/lights). WRITTEN BY show_service's rig
# API (POST, PUT and DELETE /show/api/v1/rig/fixtures; show_service/README.md S5.23), whole, on every change: don't
# edit it by hand. show_service merges it under config/room/fixtures.yaml (the hand file; the same schema, README
# S5.2), which wins on an id clash (a problem line), and re-reads it when it changes. Removing a light here removes the
# node and output made for it once nothing else uses them.
"""


def added_path_for(path: pathlib.Path) -> pathlib.Path:
    """The added file next to a fixtures.yaml (``Registry``'s and ``Registrar``'s default)."""
    return pathlib.Path(path).parent / ADDED_NAME


def _read_added(path: pathlib.Path, problems: list[str]) -> tuple[Optional[dict], bytes]:
    """(its mapping, its bytes). Missing: (None, b""). One that doesn't read: a problem line and None, so the hand
    file loads alone (a machine-written file is never half there: it is replaced whole)."""
    lost = "the lights added on the Lights page are left out"
    try:
        data = pathlib.Path(path).read_bytes()
    except FileNotFoundError:
        return None, b""
    except OSError as e:
        problems.append(f"{ADDED_NAME}: {e.strerror or e}: {lost}")
        return None, b""
    try:
        raw = _yaml(data)
    except Exception as e:
        problems.append(f"{ADDED_NAME}: {type(e).__name__}: {e}: {lost}")
        return None, data
    if raw is None:
        return {}, data
    if not isinstance(raw, dict):
        problems.append(f"{ADDED_NAME}: not a mapping: {lost}")
        return None, data
    return raw, data


def merge_added(hand: dict, added: dict, problems: list[str]) -> tuple[dict, frozenset[str]]:
    """The hand file's mapping with the added file's nodes, outputs, types and fixtures merged in (a new dict, neither
    input changed): the hand file wins on an id clash, with a problem line. Also the fixture ids taken from the added
    file."""
    out = dict(hand)
    v = added.get("version", VERSION)
    if v != VERSION:
        problems.append(f"{ADDED_NAME}: version: {v!r} (this reader knows {VERSION})")
    for k in added:
        if k != "version" and k not in ADDED_SECTIONS:
            problems.append(f"{ADDED_NAME}: {k}: not a section it holds ({', '.join(ADDED_SECTIONS)}): ignored")
    ids: set[str] = set()
    for sec in ADDED_SECTIONS:
        a = added.get(sec)
        if a is None:
            continue
        if not isinstance(a, dict):
            problems.append(f"{ADDED_NAME}: {sec}: not a mapping: ignored")
            continue
        h = hand.get(sec)
        if h is not None and not isinstance(h, dict):
            continue                                     # the hand file's own problem line says so (_Builder)
        merged = dict(h or {})
        for eid, e in a.items():
            if eid in merged:
                problems.append(f"{ADDED_NAME}: {sec}.{eid}: also in fixtures.yaml: the hand file's is used")
                continue
            merged[eid] = e
            if sec == "fixtures":
                ids.add(eid)
        out[sec] = merged
    return out, frozenset(ids)


def type_label(t: Any) -> Optional[str]:
    """A type in page words: a builtin's TYPE_LABELS, else "<id> (<mode>, <n> channels)"."""
    if not isinstance(t, dict):
        return None
    tid = t.get("id")
    if t.get("source") == "builtin" and tid in TYPE_LABELS:
        return TYPE_LABELS[tid]
    fp = t.get("footprint") or 0
    mode = f"{t['mode']}, " if t.get("mode") else ""
    return f"{tid} ({mode}{fp} channel{'' if fp == 1 else 's'})"


def type_rows(view: Optional[contract.RigView]) -> list[dict]:
    """GET /api/v1/rig/types: every type a DMX light can be registered with, [{id, label, source, footprint, channels:
    [name], kind, caps}]: the generic types (GENERIC_TYPES), then the view's own types with DMX channels (an OFL type in
    fixtures.yaml). A type with none (a screen, a Hue lamp, an inert matrix mode) isn't one."""
    def row(t: contract.TypeInfo) -> dict:
        return {"id": t["id"], "label": type_label(t), "source": t["source"], "footprint": t["footprint"],
                "channels": [c["name"] for c in sorted(t["channels"], key=lambda c: c["offset"])], "kind": t["kind"],
                "caps": caps_of(t)}
    rows = [row(BUILTIN_TYPES[tid]) for tid in GENERIC_TYPES]
    for tid, t in ((view or {}).get("types") or {}).items():
        if tid not in GENERIC_TYPES and isinstance(t, dict) and t.get("footprint") and t.get("kind") == "lamp":
            rows.append(row(t))
    return rows


def role_of(f: dict, lamps: Optional[dict] = None) -> Optional[str]:
    """A fixture's role as the director decides it (selectors' ``role:``): its own, else its placement's (show.yaml
    ``lamps``), else ``lamps.default_role`` for a lamp."""
    return _Matcher({}, {}, lambda g: None, (), lamps).role(f)


# -- the hot-reloaded registry -----------------------------------------------------------------------------------------
class Registry:
    """fixtures.yaml with fixtures.added.yaml merged under it and fixtures.setup.yaml merged into each fixture's
    ``setup``, hot-reloaded (module docstring). ``poll`` on show-brain; ``view`` from any thread."""

    def __init__(self, path: pathlib.Path = contract.FIXTURES_FILE, *,
                 types_dir: pathlib.Path = contract.FIXTURE_TYPES_DIR,
                 latency_path: pathlib.Path = contract.LATENCY_FILE,
                 patch_path: pathlib.Path = contract.PATCH_FILE,
                 added_path: Optional[pathlib.Path] = None,
                 setup_path: Optional[pathlib.Path] = None) -> None:
        self.path = pathlib.Path(path)
        self.added_path = pathlib.Path(added_path) if added_path is not None else added_path_for(self.path)
        self.setup_path = pathlib.Path(setup_path) if setup_path is not None else setup_path_for(self.path)
        self._setup_good: dict[str, Any] = {}       # the last fixtures.setup.yaml that read (a broken one keeps it)
        self.types_dir = pathlib.Path(types_dir)
        self.latency_path = pathlib.Path(latency_path)
        self.patch_path = pathlib.Path(patch_path)
        self.generation = 0                         # +1 per new view (good or the last good with a new error)
        self.patch: Optional[dict] = None           # patch.yaml as last read (hue_settings' input), None: unreadable
        self.latency: Optional[dict] = None
        self.added_ids: frozenset[str] = frozenset()   # the view's fixtures from the added file (the page edits them)
        self._view: Optional[contract.RigView] = None
        self._good: Optional[contract.RigView] = None
        self._type_files: tuple[pathlib.Path, ...] = ()
        self._sig: Optional[tuple] = None
        self._seen: Optional[tuple] = None          # a changed signature seen once, not yet applied (debounce)

    @staticmethod
    def _stat(p: pathlib.Path) -> Optional[tuple]:
        try:
            st = p.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _signature(self, looks: Optional[dict], lamps: Optional[dict]) -> tuple:
        lk = looks if isinstance(looks, dict) else {}
        inputs = json.dumps({"align_pct": lk.get("align_pct"), "min_visible_ms": lk.get("min_visible_ms"),
                             "lamps": lamps}, sort_keys=True, default=str)
        files = (self.path, self.added_path, self.setup_path, self.latency_path, self.patch_path) + self._type_files
        return tuple((str(p), self._stat(p)) for p in files) + (inputs,)

    def _read_side(self, problems: list[str]) -> None:
        try:
            self.latency = json.loads(self.latency_path.read_text(encoding="utf-8"))
            if not isinstance(self.latency, dict):
                raise ValueError("not a JSON object")
        except FileNotFoundError:
            self.latency = None
        except Exception as e:
            self.latency = None
            problems.append(f"{self.latency_path.name}: {type(e).__name__}: {e}: every latency is unset or hand-set")
        try:
            self.patch = _yaml(self.patch_path.read_bytes())
            if not isinstance(self.patch, dict):
                raise ValueError("not a mapping")
        except FileNotFoundError:
            self.patch = None
        except Exception as e:
            self.patch = None
            problems.append(f"{self.patch_path.name}: {type(e).__name__}: {e}: hue settings come from fixtures.yaml "
                            f"alone")

    def poll(self, looks: Optional[dict] = None, force: bool = False, lamps: Optional[dict] = None) -> bool:
        """Reload when an input changed (module docstring). True when ``view()`` now returns a new view.

        **Debounced** (README S5.14): a change to a file (its mtime or size) is applied only when the next poll
        (RELOAD_S later, about a second) sees the same files, so a file caught half-way through an in-place save
        (which can still be valid YAML: an output on and its fixtures cut off) is never loaded; the last good view
        stays meanwhile. The first load, ``force``, and a change of show.yaml's inputs alone apply at once."""
        sig = self._signature(looks, lamps)
        if not force and sig == self._sig:
            self._seen = None
            return False
        if not force and self._sig is not None and sig[:-1] != self._sig[:-1] and sig != self._seen:
            self._seen = sig                        # a file changed: wait for one more poll to see it settled
            return False
        self._seen = None
        side: list[str] = []
        self._read_side(side)
        got, sdata = read_setup(self.setup_path, side)
        if got is None:
            side.append(f"{SETUP_NAME}: the lights' last good settings stay in use")
        else:
            self._setup_good = got
        view, used, ok, added_ids = _load(self.path, self.types_dir, self.latency, looks, lamps, self.added_path,
                                          (self._setup_good, sdata))
        if ok:
            view["problems"] = list(dict.fromkeys(side + view["problems"]))
            self._type_files = tuple(dict.fromkeys(used))
            self._good = view
            self.added_ids = added_ids
            new = view
            log.info("fixtures: loaded %d fixtures, %d outputs (%s), %d problem(s)", len(view["fixtures"]),
                     len(view["outputs"]), view["revision"], len(view["problems"]))
        else:
            log.warning("fixtures: %s", view["error"])
            if self._good is not None:
                new = dict(self._good)
                new["error"] = f"{view['error']} (the last good registry stays in use)"
            else:
                new = view
            if self._view is not None and new.get("error") == self._view.get("error") \
                    and new.get("revision") == self._view.get("revision"):
                self._sig = self._signature(looks, lamps)
                return False
        self._sig = self._signature(looks, lamps)
        self._view = new                                       # swapped by reference; never mutated after this
        self.generation += 1
        return True

    def view(self) -> Optional[contract.RigView]:
        return self._view

    def hue_settings(self, out: Optional[contract.OutputConfig]) -> dict:
        """``hue_settings`` over the patch.yaml this registry last read."""
        return hue_settings(out, self.patch)


# -- writing the added file (the rig API, on show-brain; README S5.23) -------------------------------------------------
ADDED_ID_PREFIX = "dmx."            # a registered light's id: dmx.<its label, slugged>, made unique
NODE_ID_PREFIX = "node-"            # a node made for an address: node-<its IP, dashed>
OUTPUT_ID_PREFIX = "dmx-"           # an output made for a universe on a node: dmx-<the IP, dashed>-u<universe>
LABEL_MAX = 80                      # characters in a light's name
WORD_MAX = 64                       # characters in a placement, role or zone word
SPEC_KEYS: tuple[str, ...] = ("label", "type", "node_ip", "universe", "address", "placement", "role", "zone")
OPTIONAL_KEYS: tuple[str, ...] = ("placement", "role", "zone")
_SLUG = re.compile(r"[^a-z0-9]+")


class Refused(ValueError):
    """A registration that can't be made as asked: nothing is written. ``code`` is the API's error word (400; 404
    ``unknown_fixture``; 409 ``in_hand_file``), ``detail`` page words, ``extra`` more fields (``choices``,
    ``problems``)."""

    def __init__(self, code: str, detail: str, **extra: Any) -> None:
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.extra = code, detail, extra


def write_added(path: pathlib.Path, doc: dict) -> None:
    """The added file, whole and atomically (a tmp file in the same directory, fsync, os.replace): ADDED_HEADER, then
    ``version`` and ADDED_SECTIONS in order. Raises OSError."""
    import yaml
    body = {"version": VERSION, **{s: dict(doc.get(s) or {}) for s in ADDED_SECTIONS}}
    text = ADDED_HEADER + yaml.safe_dump(body, sort_keys=False, default_flow_style=False, allow_unicode=True,
                                         width=120)
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _whole(v: Any) -> Optional[int]:
    """A whole number, or one written as digits (a form field); None otherwise."""
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return _int(v)


def _node_ip(v: Any) -> str:
    """A node's address: an IPv4 unicast literal, normalised (never this machine: loopback is room_service's own
    Art-Net receiver). Refused otherwise."""
    try:
        a = ipaddress.IPv4Address(v.strip()) if isinstance(v, str) else None
    except ValueError:
        a = None
    if a is None or a.is_unspecified or a.is_multicast or a.is_reserved or a.is_loopback:
        raise Refused("bad_request", f"node_ip: {v!r} isn't the node's IPv4 address on the LAN (like 192.168.1.50)")
    return str(a)


def _same_ip(v: Any, ip: str) -> bool:
    try:
        return str(ipaddress.IPv4Address(str(v).strip())) == ip
    except ValueError:
        return False


class Registrar:
    """The rig API's writer (README S5.23). Each call reads the hand file and the added file, checks the result with the
    loader itself (``_Builder``: a light it would leave out is refused, with its problem lines), and only then writes
    the added file whole (``write_added``). Never writes fixtures.yaml. Only on show-brain: one write at a time.

    A light names ``node_ip`` and ``universe``: the artnet output for that node's universe is found (the hand file's
    first) or made in the added file, on, with its node (``last_ip``, no match: any reply there counts). An output or
    node the added file made goes again once nothing uses it; a generic type is declared there as it is first used."""

    def __init__(self, path: pathlib.Path = contract.FIXTURES_FILE, *, added_path: Optional[pathlib.Path] = None,
                 types_dir: pathlib.Path = contract.FIXTURE_TYPES_DIR, looks: Optional[dict] = None,
                 lamps: Optional[dict] = None) -> None:
        self.path = pathlib.Path(path)
        self.added_path = pathlib.Path(added_path) if added_path is not None else added_path_for(self.path)
        self.types_dir = pathlib.Path(types_dir)
        self.looks = looks if isinstance(looks, dict) else None
        self.lamps = lamps if isinstance(lamps, dict) else None

    # -- the calls -----------------------------------------------------------------------------------------------------
    def add(self, body: Any) -> tuple[str, list[str], contract.RigView]:
        """POST /api/v1/rig/fixtures: (the new light's id, its problem lines, the view as the loader now builds it)."""
        hand, added = self._read()
        spec, probs = self._spec(body, self._build(hand, added), partial=False)
        new = copy.deepcopy(added)
        fid = self._free(ADDED_ID_PREFIX + (_SLUG.sub("-", spec["label"].lower()).strip("-")[:48] or "light"),
                         dict(self._items(hand, new, "fixtures")))
        oid = self._place(hand, new, spec["node_ip"], spec["universe"], probs)
        entry: dict[str, Any] = {"label": spec["label"], "type": spec["type"], "output": oid,
                                 "address": spec["address"]}
        entry.update({k: spec[k] for k in OPTIONAL_KEYS if spec.get(k) is not None})
        new["fixtures"][fid] = entry
        self._declare_type(hand, new, spec["type"])
        view, mine = self._check(hand, new, fid, oid)
        self._write(new)
        return fid, list(dict.fromkeys(probs + mine)), view

    def update(self, fid: str, body: Any) -> tuple[str, list[str], contract.RigView]:
        """PUT /api/v1/rig/fixtures/{id}: the same keys, each optional; null clears placement, role or zone. A new
        node_ip or universe moves the light to that output (found or made)."""
        hand, added = self._read()
        self._editable(fid, hand, added)
        spec, probs = self._spec(body, self._build(hand, added), partial=True)
        new = copy.deepcopy(added)
        entry = dict(new["fixtures"][fid]) if isinstance(new["fixtures"][fid], dict) else {}
        o = dict(self._items(hand, new, "outputs")).get(entry.get("output"))
        n = dict(self._items(hand, new, "nodes")).get(o.get("node")) if isinstance(o, dict) else None
        ip = spec.get("node_ip") or (n.get("last_ip") if isinstance(n, dict) else None)
        universe = spec.get("universe") or (_whole(o.get("port_address")) if isinstance(o, dict) else None)
        if ip is None or universe is None:
            raise Refused("bad_request", "Say node_ip and universe: this light has no node or universe yet.")
        oid = self._place(hand, new, _node_ip(ip), universe, probs)
        entry["output"] = oid
        for k in ("label", "type", "address"):
            if k in spec:
                entry[k] = spec[k]
        for k in OPTIONAL_KEYS:
            if k in spec:
                if spec[k] is None:
                    entry.pop(k, None)
                else:
                    entry[k] = spec[k]
        new["fixtures"][fid] = entry
        self._declare_type(hand, new, entry.get("type"))
        self._prune(hand, new)
        view, mine = self._check(hand, new, fid, oid)
        self._write(new)
        return fid, list(dict.fromkeys(probs + mine)), view

    def remove(self, fid: str) -> dict:
        """DELETE /api/v1/rig/fixtures/{id}: ``{removed: id, outputs: [...], nodes: [...], types: [...]}`` (what went
        with it: the added file's entries nothing uses any more)."""
        hand, added = self._read()
        self._editable(fid, hand, added)
        new = copy.deepcopy(added)
        del new["fixtures"][fid]
        gone = self._prune(hand, new)
        self._write(new)
        return {"removed": fid, **gone}

    # -- reading and checking ------------------------------------------------------------------------------------------
    def _read(self) -> tuple[dict, dict]:
        try:
            hand = _yaml(self.path.read_bytes())
        except FileNotFoundError:
            hand = {}
        except Exception as e:
            raise Refused("fixtures_unreadable", f"{self.path.name} doesn't read ({type(e).__name__}: {e}): fix it "
                                                 f"first, then try again")
        hand = {} if hand is None else hand
        if not isinstance(hand, dict):
            raise Refused("fixtures_unreadable", f"{self.path.name} isn't a mapping: fix it first, then try again")
        probs: list[str] = []
        added, _ = _read_added(self.added_path, probs)
        if probs:
            raise Refused("added_unreadable", f"{probs[0]}: fix or remove it first, then try again")
        added = copy.deepcopy(added) if added is not None else {}
        for s in ADDED_SECTIONS:
            if not isinstance(added.get(s), dict):
                added[s] = {}
        return hand, added

    def _build(self, hand: dict, added: dict) -> contract.RigView:
        pre: list[str] = []
        raw, _ = merge_added(hand, added, pre)
        view = _Builder(self.types_dir, None, self.looks, self.lamps).build(raw, self.path, "candidate")
        view["problems"] = list(dict.fromkeys(pre + view["problems"]))
        return view

    @staticmethod
    def _items(hand: dict, added: dict, sec: str) -> list[tuple[str, Any]]:
        """A section's entries as the loader merges them: the hand file's first, then the added file's new ids."""
        h = hand.get(sec) if isinstance(hand.get(sec), dict) else {}
        a = added.get(sec) if isinstance(added.get(sec), dict) else {}
        return list(h.items()) + [(k, v) for k, v in a.items() if k not in h]

    def _editable(self, fid: str, hand: dict, added: dict) -> None:
        if fid in (hand.get("fixtures") if isinstance(hand.get("fixtures"), dict) else {}):
            raise Refused("in_hand_file", f"{fid} is written by hand in config/room/fixtures.yaml: change it there")
        if fid not in added["fixtures"]:
            raise Refused("unknown_fixture", f"There's no light {fid!r} added on the Lights page.",
                          choices=sorted(added["fixtures"]))

    def _spec(self, body: Any, view: contract.RigView, *, partial: bool) -> tuple[dict, list[str]]:
        if not isinstance(body, dict):
            raise Refused("bad_request", "The body is {label, type, node_ip, universe, address, placement?, role?, "
                                         "zone?}.")
        probs = [f"{k}: not something a light takes here: ignored" for k in body if k not in SPEC_KEYS]
        out: dict[str, Any] = {}

        def asked(k: str) -> bool:
            return k in body or not partial
        if asked("label"):
            v = body.get("label")
            if not isinstance(v, str) or not v.strip() or len(v.strip()) > LABEL_MAX:
                raise Refused("bad_request", f"label: give the light a name (1 to {LABEL_MAX} characters)")
            out["label"] = v.strip()
        if asked("type"):
            rows = type_rows(view)
            if body.get("type") not in {r["id"] for r in rows}:
                raise Refused("unknown_type", f"type: {body.get('type')!r} isn't a light type here: pick one from "
                                              f"the list", choices=rows)
            out["type"] = body["type"]
        if asked("node_ip"):
            out["node_ip"] = _node_ip(body.get("node_ip"))
        if asked("universe"):
            u = _whole(body.get("universe"))
            if u is None or not 1 <= u <= PORT_ADDRESS_MAX:
                raise Refused("bad_request", f"universe: {body.get('universe')!r} isn't 1..{PORT_ADDRESS_MAX} (the "
                                             f"node's Art-Net universe; 0 is room_service's own)")
            out["universe"] = u
        if asked("address"):
            a = _whole(body.get("address"))
            if a is None or not 1 <= a <= UNIVERSE:
                raise Refused("bad_request", f"address: {body.get('address')!r} isn't a DMX start channel "
                                             f"(1..{UNIVERSE})")
            out["address"] = a
        for k in OPTIONAL_KEYS:
            if k in body:
                v = body.get(k)
                if v is None or (isinstance(v, str) and not v.strip()):
                    out[k] = None
                elif isinstance(v, str) and len(v.strip()) <= WORD_MAX:
                    out[k] = v.strip()
                else:
                    raise Refused("bad_request", f"{k}: {v!r} isn't a word (up to {WORD_MAX} characters)")
        return out, probs

    @staticmethod
    def _free(base: str, taken: dict) -> str:
        """``base`` cut to an id, or with -2, -3, ... until it isn't taken."""
        base = base[:58].rstrip("-.") or "light"
        if base not in taken:
            return base
        n = 2
        while f"{base}-{n}" in taken:
            n += 1
        return f"{base}-{n}"

    def _place(self, hand: dict, new: dict, ip: str, universe: int, probs: list[str]) -> str:
        """The artnet output for universe ``universe`` on the node at ``ip``: one already there (the hand file's
        first), else a node (if none has that address) and an output made in ``new``, on."""
        nodes = self._items(hand, new, "nodes")
        nid = next((k for k, n in nodes if isinstance(n, dict) and _same_ip(n.get("last_ip"), ip)), None)
        if nid is None:
            nid = self._free(NODE_ID_PREFIX + ip.replace(".", "-"), dict(nodes))
            new["nodes"][nid] = {"last_ip": ip}
        outs = self._items(hand, new, "outputs")
        oid = next((k for k, o in outs if isinstance(o, dict) and o.get("driver") in UNIVERSE_DRIVERS
                    and o.get("node") == nid and _whole(o.get("port_address")) == universe), None)
        if oid is None:
            oid = self._free(f"{OUTPUT_ID_PREFIX}{ip.replace('.', '-')}-u{universe}", dict(outs))
            new["outputs"][oid] = {"driver": UNIVERSE_DRIVERS[0], "enabled": True, "node": nid,
                                   "port_address": universe}
        elif dict(outs)[oid].get("enabled") is not True:
            if oid in (hand.get("outputs") if isinstance(hand.get("outputs"), dict) else {}):
                probs.append(f"outputs.{oid}: switched off in config/room/fixtures.yaml (enabled: false): switch it "
                             f"on there and this light sends")
            else:
                new["outputs"][oid] = {**new["outputs"][oid], "enabled": True}
        return oid

    def _declare_type(self, hand: dict, new: dict, tid: Any) -> None:
        if tid in GENERIC_TYPES and tid not in dict(self._items(hand, new, "types")):
            new["types"][tid] = {"source": "builtin"}

    def _prune(self, hand: dict, new: dict) -> dict[str, list[str]]:
        """The added file's outputs, nodes and types nothing uses any more, removed from ``new``."""
        gone: dict[str, list[str]] = {"outputs": [], "nodes": [], "types": []}
        fixtures = [f for _, f in self._items(hand, new, "fixtures") if isinstance(f, dict)]
        used = {f.get("output") for f in fixtures}
        for oid in [k for k in new["outputs"] if k not in used]:
            del new["outputs"][oid]
            gone["outputs"].append(oid)
        used = {o.get("node") for _, o in self._items(hand, new, "outputs") if isinstance(o, dict)}
        for nid in [k for k in new["nodes"] if k not in used]:
            del new["nodes"][nid]
            gone["nodes"].append(nid)
        used = {f.get("type") for f in fixtures}
        for tid in [k for k in new["types"] if k not in used]:
            del new["types"][tid]
            gone["types"].append(tid)
        return gone

    def _check(self, hand: dict, new: dict, fid: str, oid: str) -> tuple[contract.RigView, list[str]]:
        """Build the result with the loader; a light it leaves out (or doesn't route to ``oid``, or with no DMX
        channels) is refused with its problem lines."""
        view = self._build(hand, new)
        nid = (view["outputs"].get(oid) or {}).get("node")
        names = [f"fixtures.{fid}", f"outputs.{oid}"] + ([f"nodes.{nid}"] if nid else [])
        pat = re.compile("|".join(re.escape(n) for n in names) + r"(?![a-z0-9_-])")
        mine = [p for p in view["problems"] if pat.search(p) or p.startswith(ADDED_NAME)]
        f = view["fixtures"].get(fid)
        if f is None or f["output"] != oid:
            raise Refused("not_added", "That light can't be added as asked: " + ("; ".join(mine) or
                                                                                  "the registry leaves it out"),
                          problems=mine)
        if not (view["types"].get(f["type"]) or {}).get("footprint"):
            raise Refused("not_dmx", f"type {f['type']} has no DMX channels here", problems=mine)
        return view, mine

    def _write(self, new: dict) -> None:
        try:
            write_added(self.added_path, new)
        except OSError as e:
            raise Refused("not_saved", f"{ADDED_NAME} couldn't be written ({e.strerror or e}): nothing changed")


# -- the setup file (the Lights page's per-light settings; docs/LIGHTS.md §2.6) ----------------------------------------
SETUP_NAME = "fixtures.setup.yaml"
SETUP_KEYS: tuple[str, ...] = ("home", "limits", "max_speed", "range", "beam", "slots", "positions", "strobe_hz_max",
                               "jog_flip")
SETUP_AXES: tuple[str, ...] = ("pan", "tilt")
SETUP_BEAM: tuple[str, ...] = ("zoom", "focus", "iris", "frost")
SETUP_SLOT_WHEELS: tuple[str, ...] = ("gobo", "gobo2", "colour", "prism")   # slots.<wheel>.<n>: its name
SETUP_COUNTS: tuple[str, ...] = ("gobo", "colour")    # slots.count.<wheel>: a generic wheel's slots
#: how deep a patch merges into each key (a null at that depth removes the entry): positions.<name> and
#: slots.<wheel>.<n> are replaced whole; the rest key by key (home.pan, beam.zoom, ...)
_SETUP_MERGE_DEPTH: dict[str, int] = {"home": 1, "limits": 1, "max_speed": 1, "range": 1, "beam": 1, "jog_flip": 1,
                                      "positions": 1, "slots": 2}
SETUP_HEADER = """\
# config/room/fixtures.setup.yaml: each light's settings from the Lights page (Move it and set it up: where it rests,
# its limits, its normal beam, its pattern and colour names, its named positions; docs/LIGHTS.md §2.6). WRITTEN BY
# show_service's rig API (POST /show/api/v1/rig/fixtures/{id}/setup and .../positions; fixtures.SetupStore), whole, on
# every change: don't edit it by hand. Keyed by fixture id, for any light (config/room/fixtures.yaml's too).
# show_service re-reads it when it changes. Angles are degrees from the middle of the light's pan or tilt range.
"""


def setup_path_for(path: pathlib.Path) -> pathlib.Path:
    """The setup file next to a fixtures.yaml (``Registry``'s default)."""
    return pathlib.Path(path).parent / SETUP_NAME


def slug_id(text: Any, fallback: str = "place") -> str:
    """Page words -> an id (ID_RE): lower case, runs of anything else to "-" ("Over the sofa" -> "over-the-sofa"),
    the names positions and room points are kept under."""
    s = _SLUG.sub("-", str(text or "").strip().lower()).strip("-.")[:64].rstrip("-.")
    return s if _is_id(s) else fallback


def _finite(v: Any, what: str, *, lo: Optional[float] = None, hi: Optional[float] = None, above: bool = False
            ) -> float:
    n = _num(v)
    if n is None:
        raise ValueError(f"{what}: {v!r} isn't a number")
    if (above and lo is not None and not n > lo) or (not above and lo is not None and n < lo) \
            or (hi is not None and n > hi):
        rng = f"above {lo:g}" if above else f"{lo:g} to {hi:g}" if hi is not None else f"at least {lo:g}"
        raise ValueError(f"{what}: {v!r} isn't {rng}")
    return n


def _axes(v: Any, what: str, check: Callable[[Any, str], Any]) -> dict:
    if not isinstance(v, dict):
        raise ValueError(f"{what}: not a mapping of pan and tilt")
    out = {}
    for k, x in v.items():
        if k not in SETUP_AXES:
            raise ValueError(f"{what}.{k}: only pan and tilt")
        if x is not None:
            out[k] = check(x, f"{what}.{k}")
    return out


def _limit_pair(v: Any, what: str) -> list[float]:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ValueError(f"{what}: [one end, the other end] in degrees")
    a, b = _finite(v[0], what), _finite(v[1], what)
    if not a < b:
        raise ValueError(f"{what}: {list(v)!r}: the first end must be below the second")
    return [a, b]


def _slot_key(k: Any, what: str) -> str:
    n = _whole(k)
    if n is None or n < 0:
        raise ValueError(f"{what}: {k!r} isn't a slot number")
    return str(n)


def _name(v: Any, what: str) -> str:
    if not isinstance(v, str) or not v.strip() or len(v.strip()) > LABEL_MAX:
        raise ValueError(f"{what}: a name of 1 to {LABEL_MAX} characters")
    return v.strip()


def _slots(v: Any, what: str) -> dict:
    if not isinstance(v, dict):
        raise ValueError(f"{what}: not a mapping")
    out: dict[str, Any] = {}
    for w, m in v.items():
        if m is None:
            continue
        if w == "count":
            if not isinstance(m, dict):
                raise ValueError(f"{what}.count: a mapping of wheel -> slots")
            cnt = {}
            for k, n in m.items():
                if k not in SETUP_COUNTS:
                    raise ValueError(f"{what}.count.{k}: only {', '.join(SETUP_COUNTS)}")
                if n is None:
                    continue
                c = _whole(n)
                if c is None or not 1 <= c <= SLOT_COUNT_MAX:
                    raise ValueError(f"{what}.count.{k}: {n!r} isn't 1 to {SLOT_COUNT_MAX}")
                cnt[k] = c
            if cnt:
                out["count"] = cnt
            continue
        if w not in SETUP_SLOT_WHEELS:
            raise ValueError(f"{what}.{w}: not a wheel ({', '.join(SETUP_SLOT_WHEELS)}, count)")
        if not isinstance(m, dict):
            raise ValueError(f"{what}.{w}: a mapping of slot number -> name")
        names: dict[str, Any] = {}
        for k, x in m.items():
            key = _slot_key(k, f"{what}.{w}")
            if x is None:
                continue
            if w == "colour":
                d = {"name": x} if isinstance(x, str) else x
                if not isinstance(d, dict) or not set(d) <= {"name", "hex"} or not d:
                    raise ValueError(f"{what}.colour.{key}: {{name?, hex?}}")
                e: dict[str, str] = {}
                if d.get("name") is not None:
                    e["name"] = _name(d["name"], f"{what}.colour.{key}.name")
                if d.get("hex") is not None:
                    if not isinstance(d["hex"], str) or not _HEX.match(d["hex"]):
                        raise ValueError(f"{what}.colour.{key}.hex: {d['hex']!r} isn't #rrggbb")
                    e["hex"] = d["hex"].lower()
                if e:
                    names[key] = e
            else:
                names[key] = _name(x, f"{what}.{w}.{key}")
        if names:
            out[w] = names
    return out


def _positions(v: Any, what: str) -> dict:
    if not isinstance(v, dict):
        raise ValueError(f"{what}: not a mapping of name -> {{pan, tilt}}")
    out = {}
    for k, x in v.items():
        if not _is_id(k):
            raise ValueError(f"{what}: {k!r} isn't a name the room can use ({ID_RE}; fixtures.slug_id makes one)")
        if x is None:
            continue
        if not isinstance(x, dict) or not {"pan", "tilt"} <= set(x) or not set(x) <= {"pan", "tilt", "label"}:
            raise ValueError(f"{what}.{k}: {{pan, tilt, label?}}")
        e: dict[str, Any] = {"pan": _finite(x["pan"], f"{what}.{k}.pan"),
                             "tilt": _finite(x["tilt"], f"{what}.{k}.tilt")}
        if x.get("label") is not None:
            e["label"] = _name(x["label"], f"{what}.{k}.label")
        out[k] = e
    return out


def _beam(v: Any, what: str) -> dict:
    if not isinstance(v, dict):
        raise ValueError(f"{what}: not a mapping of {', '.join(SETUP_BEAM)}")
    out = {}
    for k, x in v.items():
        if k not in SETUP_BEAM:
            raise ValueError(f"{what}.{k}: only {', '.join(SETUP_BEAM)}")
        if x is not None:
            out[k] = _finite(x, f"{what}.{k}", lo=0.0, above=True) if k == "zoom" else \
                _finite(x, f"{what}.{k}", lo=0.0, hi=1.0)
    return out


def _flip(v: Any, what: str) -> bool:
    if not isinstance(v, bool):
        raise ValueError(f"{what}: {v!r} isn't true or false")
    return v


_SETUP_CHECK: dict[str, Callable[[Any, str], Any]] = {
    "home": lambda v, w: _axes(v, w, _finite),
    "limits": lambda v, w: _axes(v, w, _limit_pair),
    "max_speed": lambda v, w: _axes(v, w, lambda x, ww: _finite(x, ww, lo=0.0, above=True)),
    "range": lambda v, w: _axes(v, w, lambda x, ww: _finite(x, ww, lo=0.0, above=True)),
    "beam": _beam,
    "slots": _slots,
    "positions": _positions,
    "strobe_hz_max": lambda v, w: _finite(v, w, lo=0.0, above=True),
    "jog_flip": lambda v, w: _axes(v, w, _flip),
}


def clean_setup(entry: Any) -> tuple[contract.FixtureSetup, list[str]]:
    """A setup entry -> (the part that can be used, normalised; a page-words line per key that can't). Empty
    mappings drop out (an unset key is absent, never a default)."""
    if not isinstance(entry, dict):
        return {}, ["not a mapping of settings"]
    out: dict[str, Any] = {}
    probs: list[str] = []
    for k, v in entry.items():
        if k not in SETUP_KEYS:
            probs.append(f"{k}: not a setting a light has ({', '.join(SETUP_KEYS)})")
            continue
        if v is None:
            continue
        try:
            got = _SETUP_CHECK[k](v, k)
        except ValueError as e:
            probs.append(str(e))
            continue
        if got != {}:
            out[k] = got
    return out, probs                                                  # type: ignore[return-value]


def read_setup(path: pathlib.Path, problems: list[str]) -> tuple[Optional[dict[str, Any]], bytes]:
    """(its ``fixtures`` mapping as written, its bytes). Missing: ({}, b""). One that doesn't read: a problem line and
    None (``Registry`` keeps the last good settings)."""
    try:
        data = pathlib.Path(path).read_bytes()
    except FileNotFoundError:
        return {}, b""
    except OSError as e:
        problems.append(f"{SETUP_NAME}: {e.strerror or e}")
        return None, b""
    try:
        raw = _yaml(data)
    except Exception as e:
        problems.append(f"{SETUP_NAME}: {type(e).__name__}: {e}")
        return None, data
    if raw is None:
        return {}, data
    fx = raw.get("fixtures") if isinstance(raw, dict) else None
    if not isinstance(raw, dict) or not isinstance(fx if fx is not None else {}, dict):
        problems.append(f"{SETUP_NAME}: not a mapping with fixtures: {{<id>: settings}}")
        return None, data
    if raw.get("version", VERSION) != VERSION:
        problems.append(f"{SETUP_NAME}: version: {raw.get('version')!r} (this reader knows {VERSION})")
    return dict(fx or {}), data


def attach_setup(view: contract.RigView, setups: dict[str, Any], problems: list[str]) -> None:
    """Each fixture's ``setup`` from the setup file's entry ({} when none), what can't be used dropped with a problem
    line; an id that isn't a light is a problem line (kept in the file)."""
    fixtures = view.get("fixtures") or {}
    for fid, entry in sorted(setups.items(), key=lambda kv: str(kv[0])):
        if fid not in fixtures:
            problems.append(f"{SETUP_NAME}: setup for {fid}: no such light (kept)")
            continue
        clean, probs = clean_setup(entry)
        problems.extend(f"{SETUP_NAME}: {fid}.{p}: ignored" for p in probs)
        fixtures[fid]["setup"] = clean


def merge_setup(cur: Any, patch: dict) -> dict:
    """``cur`` with ``patch`` merged in (a new dict): a key set to null removes it; dict keys merge to their
    _SETUP_MERGE_DEPTH (positions per name, slots per wheel and slot, the rest per axis or beam word), a null there
    removing that entry; emptied mappings drop out."""
    def merge(a: dict, b: dict, depth: int) -> dict:
        out = copy.deepcopy(a)
        for k, v in b.items():
            if v is None:
                out.pop(k, None)
            elif depth > 1 and isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = merge(out[k], v, depth - 1)
            else:
                out[k] = copy.deepcopy(v)
            if out.get(k) == {}:
                out.pop(k, None)
        return out
    out = copy.deepcopy(cur) if isinstance(cur, dict) else {}
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict) and k in _SETUP_MERGE_DEPTH:
            out[k] = merge(out[k], v, _SETUP_MERGE_DEPTH[k])
        else:
            out[k] = copy.deepcopy(v)
        if out.get(k) == {}:
            out.pop(k, None)
    return out


def write_setup(path: pathlib.Path, fixtures: dict) -> None:
    """The setup file, whole and atomically (as ``write_added``): SETUP_HEADER, ``version``, ``fixtures`` by id."""
    import yaml
    body = {"version": VERSION, "fixtures": {k: fixtures[k] for k in sorted(fixtures)}}
    text = SETUP_HEADER + yaml.safe_dump(body, sort_keys=False, default_flow_style=False, allow_unicode=True,
                                         width=120)
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


class SetupStore:
    """The setup file's reader and its only writer (the rig API, on show-brain: one write at a time). ``patch``
    merges, checks the whole result (``clean_setup``: any problem refuses it, nothing written) and writes the file
    whole; the Registry picks it up (``Rig.reload_now`` after the write)."""

    def __init__(self, path: pathlib.Path = contract.FIXTURES_SETUP_FILE) -> None:
        self.path = pathlib.Path(path)

    def _raw(self) -> dict[str, Any]:
        probs: list[str] = []
        got, _ = read_setup(self.path, probs)
        if got is None:
            raise Refused("setup_unreadable", f"{probs[0] if probs else SETUP_NAME}: fix or remove it first, then try "
                                              f"again")
        return got

    def read(self) -> dict[str, contract.FixtureSetup]:
        """Every light's settings, as the Registry uses them ({} for a missing file). Refused when it doesn't read."""
        return {str(k): clean_setup(v)[0] for k, v in self._raw().items()}

    def get(self, fid: str) -> contract.FixtureSetup:
        return self.read().get(fid, {})

    def patch(self, fid: str, patch: Any) -> contract.FixtureSetup:
        """Merge ``patch`` into ``fid``'s settings (``merge_setup``) and write: the light's settings now. Refused
        ``bad_request`` (an id that isn't one, a body that isn't a mapping, a value that can't be used: ``problems``
        lists each) or ``setup_unreadable`` / ``not_saved``; nothing is written then."""
        if not _is_id(fid):
            raise Refused("bad_request", f"{fid!r} isn't a light's id")
        if not isinstance(patch, dict):
            raise Refused("bad_request", f"The settings are a mapping of {', '.join(SETUP_KEYS)}.")
        raw = self._raw()
        merged = merge_setup(raw.get(fid), patch)
        clean, probs = clean_setup(merged)
        if probs:
            raise Refused("bad_request", f"{fid}: {probs[0]}", problems=probs)
        new = dict(raw)
        if clean:
            new[fid] = clean
        else:
            new.pop(fid, None)
        self._write(new)
        return clean

    def remove(self, fid: str) -> bool:
        """Forget a light's settings (when it is removed from the page): True when there were some."""
        raw = self._raw()
        if fid not in raw:
            return False
        self._write({k: v for k, v in raw.items() if k != fid})
        return True

    def _write(self, fixtures: dict) -> None:
        try:
            write_setup(self.path, fixtures)
        except OSError as e:
            raise Refused("not_saved", f"{SETUP_NAME} couldn't be written ({e.strerror or e}): nothing changed")


def caps_of(t: Any, setup: Optional[dict] = None) -> Optional[contract.FixtureCaps]:
    """fixture_encode.caps for a type (and a light's setup), imported lazily: None when it can't be (the page then
    shows no controls) or the type isn't one."""
    if not isinstance(t, dict):
        return None
    try:
        from . import fixture_encode
    except Exception:                                    # noqa: BLE001  degrade: no capability rows
        return None
    try:
        return fixture_encode.caps(t, setup if isinstance(setup, dict) else None)
    except Exception:                                    # noqa: BLE001
        log.exception("fixtures: caps for type %s failed", t.get("id"))
        return None
