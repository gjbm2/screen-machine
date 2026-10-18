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
role, else ``lamps.default_role`` for a lamp, as the director decides it. **Order:** members of the selector's
positive ``group:`` terms first, in the group's order (the first such group a fixture is in), then the rest by
``order``, then id.

**Latency** (``resolve_latency``; S5.2): per edge, latency.json ``outputs.<fixture id>.<attr>``, then
``outputs.<the output's latency_key>.<attr>``, then fixtures.yaml's hand-set ms, then 0 ("unset"). A measured edge's
value is its perceived onset ``t10 + (t50 - t10) x (align_pct - 10) / 40`` plus the entry's ``trim_ms``.

**The added file** (README S5.23): config/room/fixtures.added.yaml holds the lights registered on the Lights page, in
fixtures.yaml's schema (its nodes, outputs, types and fixtures). ``Registry`` merges it under the hand file (the hand
file wins on an id clash, with a problem line; one that doesn't read is a problem line and the hand file loads alone)
and reloads on its changes too. Only ``Registrar`` writes it, whole and atomically, after checking the result with the
loader itself; nothing in code ever writes fixtures.yaml. ``GENERIC_TYPES`` are builtin channelled types (RGB, RGBW,
..., a mover) for a DMX light with no OFL file.

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
                                  "tag", "group")
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
            "attrs": list(attrs), "channels": [], "emitters": {}, "wheel": [], "curve": "srgb", "problems": []}


#: A generic type's channel words -> (attr, emitter), as ofl.py would read them (README S5.23). Intensity and emitters
#: are driven (colour_mix; ``uv`` never takes part); a Strobe is a shutter held at 0 ("no strobe" on generic LED
#: lights); Pan and Tilt are not driven yet: they rest at centre (GENERIC_DEFAULTS, 128 of 255; at 0 most movers aim
#: into a corner or the ceiling) until the fixture's ``pin`` says otherwise.
GENERIC_CHANNELS: dict[str, tuple[str, Optional[str]]] = {
    "Dimmer": ("intensity", None), "Red": ("emitter", "red"), "Green": ("emitter", "green"),
    "Blue": ("emitter", "blue"), "White": ("emitter", "white"), "Amber": ("emitter", "amber"),
    "UV": ("emitter", "uv"), "Strobe": ("shutter", None), "Pan": ("pan", None), "Tilt": ("tilt", None)}
#: a generic channel's resting value when nothing drives it (owner, 18 Oct: a mover must not sit aimed at 0, 0)
GENERIC_DEFAULTS: dict[str, int] = {"Pan": 128, "Tilt": 128}
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
}


def _generic(tid: str, names: tuple[str, ...]) -> contract.TypeInfo:
    t = _builtin(tid, "lamp", ())
    chans: list[contract.AttrChannel] = []
    for off, n in enumerate(names):
        attr, em = GENERIC_CHANNELS[n]
        ch: contract.AttrChannel = {"offset": off, "name": n, "attr": attr, "default": GENERIC_DEFAULTS.get(n, 0)}
        if em is not None:
            ch["emitter"] = em
        chans.append(ch)
    kinds = {c["attr"] for c in chans}
    t["channels"], t["footprint"] = chans, len(chans)
    t["attrs"] = (["intensity"] if kinds & {"intensity", "emitter"} else []) + (["colour"] if "emitter" in kinds
                                                                                 else [])
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
            terms.append(Term(key, value, prefix=prefix, neg=neg))
        clauses.append(tuple(terms))
    return Selector(expr, tuple(clauses))


class _Matcher:
    """Matches fixtures against selectors. ``groups`` gives a group's members (None: no such group); ``group_ids``
    lists the groups a prefix term may name."""

    def __init__(self, fixtures: dict, outputs: dict, groups: Callable[[str], Optional[list[str]]],
                 group_ids: Iterable[str], lamps: Optional[dict] = None) -> None:
        self.fixtures = fixtures
        self.outputs = outputs
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


def select(expr: Union[str, Selector], rig: Optional[contract.RigView], *, lamps: Optional[dict] = None) -> list[str]:
    """The fixture ids ``expr`` selects in ``rig``, ordered (module docstring). A string that doesn't parse raises
    ValueError("bad_selector: ...") as ``parse_selector`` does: the caller writes the problem line, and whatever used
    it selects nothing. Pass show.yaml ``lamps`` for ``role:`` to follow placements."""
    if not isinstance(expr, Selector):
        expr = parse_selector(expr)
    if not rig:
        return []
    groups = rig.get("groups") or {}
    return _Matcher(rig.get("fixtures") or {}, rig.get("outputs") or {}, groups.get, groups, lamps).select(expr)


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
                        "curve": curve, "pin": pin, "house": house, "latency": {}}
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

    def groups(self, sec: dict, fixtures: dict, outputs: dict) -> dict[str, list[str]]:
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
            ids = _Matcher(fixtures, outputs, members, specs, self.lamps).select(sel)
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
        groups = self.groups(self._section(raw, "groups"), fixtures, outputs)
        return {"revision": revision, "path": _rel(path), "error": None,
                "problems": list(dict.fromkeys(self.problems)), "outputs": outputs, "nodes": nodes, "types": types,
                "fixtures": fixtures, "groups": groups}


def _load(path: pathlib.Path, types_dir: pathlib.Path, latency: Optional[dict], looks: Optional[dict],
          lamps: Optional[dict], added_path: Optional[pathlib.Path] = None
          ) -> tuple[contract.RigView, list[pathlib.Path], bool, frozenset[str]]:
    """(view, the type files it read, ok, the fixture ids that came from the added file). Not ok: ``error`` set and
    empty maps. ``added_path``: fixtures.added.yaml, merged under the hand file (``merge_added``); None: none."""
    try:
        data = path.read_bytes()
    except OSError as e:
        return _empty_view(path, f"{path.name}: {e.strerror or e}"), [], False, frozenset()
    pre: list[str] = []
    added, added_data = _read_added(added_path, pre) if added_path is not None else (None, b"")
    h = hashlib.sha1(data)
    if added_data:
        h.update(b"\0" + added_data)
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
    if pre:
        view["problems"] = list(dict.fromkeys(pre + view["problems"]))
    return view, b.used, True, frozenset(i for i in ids if i in view["fixtures"])


def load(path: pathlib.Path = contract.FIXTURES_FILE, *, types_dir: pathlib.Path = contract.FIXTURE_TYPES_DIR,
         latency: Optional[dict] = None, patch: Optional[dict] = None, looks: Optional[dict] = None,
         lamps: Optional[dict] = None, added_path: Optional[pathlib.Path] = None) -> contract.RigView:
    """fixtures.yaml -> RigView. Never raises: a bad file gives ``error`` and empty maps (``Registry`` keeps the last
    good one). ``latency`` is latency.json, ``looks`` show.yaml's looks (align_pct, min_visible_ms), ``lamps``
    show.yaml's lamps (role names). ``patch`` (patch.yaml) isn't needed to build the view: drivers read
    ``hue_settings(out, patch)``. ``added_path``: a fixtures.added.yaml to merge under it (None: the hand file
    alone; ``Registry`` merges its sibling)."""
    return _load(pathlib.Path(path), pathlib.Path(types_dir), latency, looks, lamps,
                 pathlib.Path(added_path) if added_path is not None else None)[0]


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
    [name], kind}]: the generic types (GENERIC_TYPES), then the view's own types with DMX channels (an OFL type in
    fixtures.yaml). A type with none (a screen, a Hue lamp, an inert matrix mode) isn't one."""
    def row(t: contract.TypeInfo) -> dict:
        return {"id": t["id"], "label": type_label(t), "source": t["source"], "footprint": t["footprint"],
                "channels": [c["name"] for c in sorted(t["channels"], key=lambda c: c["offset"])], "kind": t["kind"]}
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
    """fixtures.yaml with fixtures.added.yaml merged under it, hot-reloaded (module docstring). ``poll`` on show-brain;
    ``view`` from any thread."""

    def __init__(self, path: pathlib.Path = contract.FIXTURES_FILE, *,
                 types_dir: pathlib.Path = contract.FIXTURE_TYPES_DIR,
                 latency_path: pathlib.Path = contract.LATENCY_FILE,
                 patch_path: pathlib.Path = contract.PATCH_FILE,
                 added_path: Optional[pathlib.Path] = None) -> None:
        self.path = pathlib.Path(path)
        self.added_path = pathlib.Path(added_path) if added_path is not None else added_path_for(self.path)
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
        files = (self.path, self.added_path, self.latency_path, self.patch_path) + self._type_files
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
        view, used, ok, added_ids = _load(self.path, self.types_dir, self.latency, looks, lamps, self.added_path)
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
