"""space: the room in metres, where every appliance is, and the geometry every appliance reads (docs/LIGHTS.md §3,
§4.3). The space builder's (18 Oct).

**The frame** (§3.1): metres, right-handed, **x east, y north, z up**, origin the room's south-west floor corner; the
room is the box [0, X] × [0, Y] × [0, Z] once its size is known. ``front`` is the compass word of the wall people face
(unset: north, the frame's own convention, not a room fact); ``right`` = front × up; left/right are always as seen
facing the front; cw/ccw are as seen from above (the floor plan: N -> E -> S -> W). The floor (z = 0) is always there;
the ceiling and the four walls come from the room's size. A direction ("upwards", "turning clockwise") or a place ("the
piano") therefore means the same thing to a screen, a lamp and a mover.

**Files** (``Store``, one per process: ``shared()``): config/room/space.yaml (hand) with config/room/space.added.yaml
(written only by the rig API: the Room map, Move it's teach points and solved poses) merged under it. Keys:
``room {size, front}``, ``appliances.<fixture or room device id> {at, kind, label, mount, plane {normal, up, size},
teach, pose}``, ``points.<name> {label, at, device}``, ``regions.<name> {label, surface, centre, size}``,
``surfaces.<name> {label, origin, normal, up, size}``. The hand file wins on a clash (a problem line): per key for an
appliance (so a hand-placed mover can still be taught on the page), whole entries for points, regions and surfaces.

**Load rules** (fixtures.py's): ``poll`` re-reads on any mtime or size change of either file; a file that doesn't read
keeps that file's last good contents (``error`` says why); a value it can't use drops that key or entry with a
page-words problem line; an unknown key is a problem line and ignored. ``view()`` is the merged ``SpaceView``, swapped
by reference, never mutated after the swap; subscribers (weak, light_scenes.Catalogue's rule) hear about a change on
the polling thread.

**Writers** (show-brain only, one at a time): each validates, checks the result with the loader itself (a write that
would add a problem line is refused), writes space.added.yaml whole and atomically, then polls. ``Refused`` (code
``bad_request``, ``in_hand_file``, ``not_found`` or ``added_unreadable``) means nothing was written. Nothing in code
ever writes space.yaml.

**Geometry** (pure Python on the hot path: ``aim``, ``beam_dir``, ``flow_*``, ``surface_*``, ``lateral`` run per fixture
per frame; derived numbers are cached per view): flow directions and their 0..1 coordinates (§3.5), each screen's view
of the flow in its own plane (``screen_flow``), the side/height words for fixtures.select (``spatial_fn``), and ray
hits on the room's surfaces. **Movers** (§3.6): ``solve_pose`` fits a mover's mounting to 3+ taught places by least
squares (NumPy and SciPy, imported only there); ``aim`` is the inverse kinematics from then on, so any calibrated mover
can point at any room point. Nothing refuses: an unreachable point gets the nearest the light can do, flagged.

Nothing here branches on an appliance, point, region, scene or fixture name: every name lives in the YAML.
"""

from __future__ import annotations

import copy
import datetime
import functools
import hashlib
import json
import logging
import math
import os
import pathlib
import re
import threading
import weakref
from typing import Any, Callable, Mapping, Optional, Sequence

from . import contract

log = logging.getLogger("show_service.space")

Vec3 = tuple[float, float, float]

VERSION = 1
ADDED_NAME = "space.added.yaml"
HAND_NAME = "space.yaml"
ID_RE = r"^[a-z0-9][a-z0-9_.-]{0,63}$"     # fixtures.py's: appliance ids, place, region and surface names
_ID = re.compile(ID_RE)
_WORD = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")    # an appliance's kind ("screen", "mover")
_DEVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")   # a room device a place belongs to ("mirror_ball")
TOP_KEYS: tuple[str, ...] = ("version", "room", "appliances", "points", "regions", "surfaces")
SECTIONS: tuple[str, ...] = ("room", "appliances", "points", "regions", "surfaces")   # the added file, in this order
ROOM_KEYS: tuple[str, ...] = ("size", "front")
PLACE_KEYS: tuple[str, ...] = ("at", "kind", "label", "mount", "plane")   # an appliance's place (editable on the map)
APPLIANCE_KEYS: tuple[str, ...] = PLACE_KEYS + ("teach", "pose")
POINT_KEYS: tuple[str, ...] = ("label", "at", "device", "from")
REGION_KEYS: tuple[str, ...] = ("label", "surface", "centre", "size")
SURFACE_KEYS: tuple[str, ...] = ("label", "origin", "normal", "up", "size")
PLANE_KEYS: tuple[str, ...] = ("normal", "up", "size")
POSE_KEYS: tuple[str, ...] = ("at", "rot", "tilt_offset_deg", "pan_sign", "tilt_sign", "rms_deg", "worst_deg",
                              "samples", "solved_at")
SAMPLE_KEYS: tuple[str, ...] = ("pan", "tilt", "point", "at", "t")
FRONTS: dict[str, Vec3] = {"north": (0.0, 1.0, 0.0), "east": (1.0, 0.0, 0.0), "south": (0.0, -1.0, 0.0),
                           "west": (-1.0, 0.0, 0.0)}
FRONT_ALIASES: dict[str, str] = {"n": "north", "e": "east", "s": "south", "w": "west"}
DEFAULT_FRONT = "north"                     # the frame's convention (§3.1), not a room fact
ROOM_SURFACES: tuple[str, ...] = ("floor", "ceiling", "wall-n", "wall-e", "wall-s", "wall-w")
SURFACE_LABELS: dict[str, str] = {"floor": "Floor", "ceiling": "Ceiling", "wall-n": "North wall",
                                  "wall-e": "East wall", "wall-s": "South wall", "wall-w": "West wall"}
SIDES: dict[str, str] = {"n": "north", "e": "east", "s": "south", "w": "west"}   # side:<n|e|s|w> as compass words
HEIGHTS: tuple[str, ...] = ("low", "mid", "high")   # height:<word>: the room's height in thirds
LABEL_MAX = 80                              # characters in a place's or region's name (fixtures.LABEL_MAX)
TEACH_MAX = 16                              # taught places kept per mover: past this the oldest goes first
UP: Vec3 = (0.0, 0.0, 1.0)
ZERO: Vec3 = (0.0, 0.0, 0.0)
EPS = 1e-9
EDGE_M = 1e-6                               # a ray landing this close outside a bounded surface still hits it
TAU = 2.0 * math.pi
#: show.yaml ``lights.calibration``'s starting points (docs/LIGHTS.md §12; that block wins key by key).
CALIBRATION: dict[str, Any] = {"fit_deg": (1.5, 4.0, 8.0), "min_spread_deg": 20.0, "pos_sigma_m": 0.3}
ANGLE_SIGMA_DEG = 0.5       # starting point (``lights.calibration.angle_sigma_deg`` wins): how closely a person centres
                            # a beam on a place by eye; weighs the taught angles against the map's position in the solve
PRIOR_SIGMA_M = 2.0         # §3.6: with no map position, the room's centre (at ceiling height when hung) at this sigma
OFFLINE_SHARE = 0.25        # starting point: with no map position, the taught directions must also leave the plane of
                            # the widest pair by min_spread_deg x this (places in a line leave where it hangs open)
GUESS_RISE_M = 1.0          # with no room and no map position: the solve starts this far above (floor: below) the
                            # taught places' centre (a first guess only; the fit moves it)
DEFAULT_SPEED: tuple[float, float] = (180.0, 120.0)   # lights.motion.default_max_speed_deg_s's starting point: how
                            # aim weighs pan against tilt travel when the caller gives no speed
SOLVE_SCOUT_NFEV = 20       # every start (per pan sign: the Kabsch fit + hung/standing × 8 headings) runs this many
                            # least_squares evaluations first...
SOLVE_STARTS = 4            # ...then the ones with the lowest cost so far run on to convergence
SOLVE_MAX_NFEV = 200        # least_squares evaluations for each of those (a bound on the solve's time)


class Refused(ValueError):
    """A space write that can't be made as asked: nothing is written. ``code`` is the API's error word (400
    ``bad_request``; 404 ``not_found``; 409 ``in_hand_file``, ``added_unreadable``), ``detail`` page words, ``extra``
    more fields (fixtures.Refused's shape)."""

    def __init__(self, code: str, detail: str, **extra: Any) -> None:
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.extra = code, detail, extra


# -- small vector maths (pure Python: the hot path) -------------------------------------------------------------------
def _v3(p: Sequence[float]) -> Vec3:
    return (float(p[0]), float(p[1]), float(p[2]))


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a: Vec3, k: float) -> Vec3:
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _len(a: Vec3) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _unit(a: Vec3) -> Vec3:
    n = _len(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > EPS else ZERO


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _angle_deg(a: Vec3, b: Vec3) -> float:
    """The angle between two directions (any length), degrees; 0 when either is zero."""
    na, nb = _len(a), _len(b)
    if na <= EPS or nb <= EPS:
        return 0.0
    c = _dot(a, b) / (na * nb)
    return math.degrees(math.acos(1.0 if c > 1.0 else -1.0 if c < -1.0 else c))


@functools.lru_cache(maxsize=512)
def _rotm(rv: tuple[float, float, float]) -> tuple[Vec3, Vec3, Vec3]:
    """The rotation matrix (rows) of a rotation vector (Rodrigues). Cached: a pose's ``rot`` never changes."""
    x, y, z = rv
    th = math.sqrt(x * x + y * y + z * z)
    if th < 1e-12:
        return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    kx, ky, kz = x / th, y / th, z / th
    c, s = math.cos(th), math.sin(th)
    C = 1.0 - c
    return ((c + kx * kx * C, kx * ky * C - kz * s, kx * kz * C + ky * s),
            (ky * kx * C + kz * s, c + ky * ky * C, ky * kz * C - kx * s),
            (kz * kx * C - ky * s, kz * ky * C + kx * s, c + kz * kz * C))


def _bf(theta_deg: float, phi_deg: float) -> Vec3:
    """The beam in the fixture's own frame (§3.6): z is the pan axis; φ from it, θ round it."""
    th, ph = math.radians(theta_deg), math.radians(phi_deg)
    sp = math.sin(ph)
    return (sp * math.cos(th), sp * math.sin(th), math.cos(ph))


# -- parsing (shared by the loader and the writers) ------------------------------------------------------------------
def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _vec(v: Any, n: int) -> Optional[list[float]]:
    if not isinstance(v, (list, tuple)) or len(v) != n:
        return None
    out = [_num(x) for x in v]
    return None if any(x is None for x in out) else out      # type: ignore[return-value]


def _sizes(v: Any, n: int) -> Optional[list[float]]:
    out = _vec(v, n)
    return out if out is not None and all(x > 0 for x in out) else None


def _label(v: Any) -> Optional[str]:
    if not isinstance(v, str):
        return None
    s = " ".join(v.split())
    return s[:LABEL_MAX] if s else None


def humanise(name: str) -> str:
    """Page words for a name with no label: "mirror-ball" -> "Mirror ball"."""
    s = re.sub(r"[-_.]+", " ", name).strip()
    return (s[:1].upper() + s[1:]) if s else name


def _front_word(v: Any) -> Optional[str]:
    if not isinstance(v, str):
        return None
    w = v.strip().lower()
    w = FRONT_ALIASES.get(w, w)
    return w if w in FRONTS else None


def _plane(raw: Any, where: str, probs: list[str]) -> Optional[dict]:
    """{normal, up, size?} -> {normal: unit, up: unit and square to the normal, size: [w, h] | None}."""
    if not isinstance(raw, dict):
        probs.append(f"{where}: needs normal, up and size: left unset")
        return None
    for k in raw:
        if k not in PLANE_KEYS:
            probs.append(f"{where}: {k!r} isn't a plane key ({', '.join(PLANE_KEYS)}): ignored")
    n, up = _vec(raw.get("normal"), 3), _vec(raw.get("up", [0, 0, 1]), 3)
    if n is None or _len(_v3(n)) <= EPS or up is None:
        probs.append(f"{where}: normal and up need three numbers each (normal not zero): left unset")
        return None
    nn = _unit(_v3(n))
    u = _v3(up)
    uo = _unit(_sub(u, _mul(nn, _dot(u, nn))))           # up, made square to the normal
    if uo == ZERO:
        probs.append(f"{where}: up runs along the normal: left unset")
        return None
    size = None
    if raw.get("size") is not None:
        size = _sizes(raw.get("size"), 2)
        if size is None:
            probs.append(f"{where}.size: two lengths in metres ([w, h]): left unset")
    return {"normal": [round(x, 6) for x in nn], "up": [round(x, 6) for x in uo], "size": size}


def _sample(raw: Any, where: str, probs: list[str]) -> Optional[dict]:
    if not isinstance(raw, dict):
        probs.append(f"{where}: isn't a taught place ({', '.join(SAMPLE_KEYS)}): dropped")
        return None
    pan, tilt, at = _num(raw.get("pan")), _num(raw.get("tilt")), _vec(raw.get("at"), 3)
    if pan is None or tilt is None or at is None:
        probs.append(f"{where}: needs pan, tilt (degrees) and at ([x, y, z] m): dropped")
        return None
    point = raw.get("point")
    point = point if isinstance(point, str) and point else None
    t = raw.get("t")
    return {"pan": pan, "tilt": tilt, "point": point, "at": at, "t": t if isinstance(t, str) else ""}


def _pose(raw: Any, where: str, probs: list[str]) -> Optional[dict]:
    if not isinstance(raw, dict):
        probs.append(f"{where}: isn't a solved pose: left unset (Point it at… again)")
        return None
    at, rot = _vec(raw.get("at"), 3), _vec(raw.get("rot"), 3)
    off, rms, worst = _num(raw.get("tilt_offset_deg")), _num(raw.get("rms_deg")), _num(raw.get("worst_deg"))
    ps, ts, n = raw.get("pan_sign"), raw.get("tilt_sign"), raw.get("samples")
    if (at is None or rot is None or off is None or rms is None or worst is None or rms < 0 or worst < 0
            or ps not in (1, -1) or ts not in (1, -1) or isinstance(ps, bool) or isinstance(ts, bool)
            or not isinstance(n, int) or isinstance(n, bool) or n < 0):
        probs.append(f"{where}: isn't a solved pose ({', '.join(POSE_KEYS)}): left unset (Point it at… again)")
        return None
    s = raw.get("solved_at")
    return {"at": at, "rot": rot, "tilt_offset_deg": off, "pan_sign": int(ps), "tilt_sign": int(ts), "rms_deg": rms,
            "worst_deg": worst, "samples": n, "solved_at": s if isinstance(s, str) else ""}


def _unknown(raw: dict, keys: tuple[str, ...], where: str, probs: list[str]) -> None:
    for k in raw:
        if k not in keys:
            probs.append(f"{where}: {k!r} isn't a key here ({', '.join(keys)}): ignored")


def _place_value(k: str, v: Any, where: str, probs: list[str]) -> Any:
    """One appliance place key, checked; None (with a problem line) when it can't be used."""
    if k == "at":
        out = _vec(v, 3)
        if out is None:
            probs.append(f"{where}: three numbers in metres ([x, y, z]): left unset")
        return out
    if k == "kind":
        if isinstance(v, str) and _WORD.match(v):
            return v
        probs.append(f"{where}: {v!r} isn't a word: left unset")
        return None
    if k == "label":
        out = _label(v)
        if out is None:
            probs.append(f"{where}: isn't a name: left unset")
        return out
    if k == "mount":
        if v in contract.MOUNTS:
            return v
        probs.append(f"{where}: {v!r} isn't one of {', '.join(contract.MOUNTS)}: left unset")
        return None
    return _plane(v, where, probs)                               # "plane"


def _appliance(raw: Any, where: str, probs: list[str]) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        probs.append(f"{where}: isn't a mapping: left out")
        return {}
    _unknown(raw, APPLIANCE_KEYS, where, probs)
    out: dict[str, Any] = {}
    for k in PLACE_KEYS:
        if raw.get(k) is not None:
            v = _place_value(k, raw[k], f"{where}.{k}", probs)
            if v is not None:
                out[k] = v
    if raw.get("teach") is not None:
        if isinstance(raw["teach"], list):
            got = [s for i, r in enumerate(raw["teach"])
                   if (s := _sample(r, f"{where}.teach[{i + 1}]", probs)) is not None]
            if len(got) > TEACH_MAX:
                probs.append(f"{where}.teach: {len(got)} taught places: the last {TEACH_MAX} are kept")
            out["teach"] = got[-TEACH_MAX:]
        else:
            probs.append(f"{where}.teach: isn't a list of taught places: dropped")
    if raw.get("pose") is not None:
        p = _pose(raw["pose"], f"{where}.pose", probs)
        if p is not None:
            out["pose"] = p
    return out


def _point(raw: Any, where: str, probs: list[str]) -> Optional[dict]:
    if not isinstance(raw, dict):
        probs.append(f"{where}: needs at ([x, y, z] m): left out")
        return None
    _unknown(raw, POINT_KEYS, where, probs)
    at = _vec(raw.get("at"), 3)
    if at is None:
        probs.append(f"{where}.at: three numbers in metres ([x, y, z]): left out")
        return None
    out: dict[str, Any] = {"at": at}
    if raw.get("label") is not None:
        lab = _label(raw["label"])
        if lab is None:
            probs.append(f"{where}.label: isn't a name: its own name is used")
        else:
            out["label"] = lab
    dev = raw.get("device")
    if dev is not None:
        if isinstance(dev, str) and _DEVICE.match(dev):
            out["device"] = dev
        else:
            probs.append(f"{where}.device: {dev!r} isn't a room device name: left unset")
    src = raw.get("from")
    if isinstance(src, str) and (src == "page" or _ID.match(src)):
        out["from"] = src
    return out


def _region(raw: Any, where: str, probs: list[str]) -> Optional[dict]:
    if not isinstance(raw, dict):
        probs.append(f"{where}: needs surface, centre and size: left out")
        return None
    _unknown(raw, REGION_KEYS, where, probs)
    surf, centre, size = raw.get("surface"), _vec(raw.get("centre"), 3), _sizes(raw.get("size"), 2)
    if not isinstance(surf, str) or centre is None or size is None:
        probs.append(f"{where}: needs surface (a name), centre ([x, y, z] m) and size ([w, d] m): left out")
        return None
    out: dict[str, Any] = {"surface": surf, "centre": centre, "size": size}
    lab = _label(raw.get("label"))
    if lab is not None:
        out["label"] = lab
    return out


def _surface(name: str, raw: Any, where: str, probs: list[str]) -> Optional[dict]:
    if name in ROOM_SURFACES:
        probs.append(f"{where}: {name} comes from the room's size: left out (use another name)")
        return None
    if not isinstance(raw, dict):
        probs.append(f"{where}: needs origin, normal, up and size: left out")
        return None
    _unknown(raw, SURFACE_KEYS, where, probs)
    origin = _vec(raw.get("origin"), 3)
    pl = _plane({k: raw[k] for k in PLANE_KEYS if k in raw}, where, probs)
    if origin is None or pl is None:
        if origin is None:
            probs.append(f"{where}.origin: three numbers in metres ([x, y, z]): left out")
        return None
    out: dict[str, Any] = {"origin": origin, **pl}
    lab = _label(raw.get("label"))
    if lab is not None:
        out["label"] = lab
    return out


def _room(raw: Any, where: str, probs: list[str]) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        probs.append(f"{where}: isn't a mapping: ignored")
        return {}
    _unknown(raw, ROOM_KEYS, where, probs)
    out: dict[str, Any] = {}
    if raw.get("size") is not None:
        size = _sizes(raw["size"], 3)
        if size is None:
            probs.append(f"{where}.size: three lengths in metres (east-west, north-south, floor to ceiling): left "
                         f"unset")
        else:
            out["size"] = size
    if raw.get("front") is not None:
        f = _front_word(raw["front"])
        if f is None:
            probs.append(f"{where}.front: {raw['front']!r} isn't north, east, south or west (n, e, s, w): "
                         f"{DEFAULT_FRONT} is used")
        else:
            out["front"] = f
    return out


def parse_doc(doc: Any, fname: str, probs: list[str]) -> dict[str, dict]:
    """A space file's mapping -> its sections, every value checked and normalised (bad ones dropped with a problem
    line). Pure."""
    out: dict[str, dict] = {"room": {}, "appliances": {}, "points": {}, "regions": {}, "surfaces": {}}
    if doc is None:
        return out
    if not isinstance(doc, dict):
        probs.append(f"{fname}: isn't a mapping: ignored")
        return out
    _unknown(doc, TOP_KEYS, fname, probs)
    out["room"] = _room(doc.get("room"), f"{fname}: room", probs)
    parts: tuple[tuple[str, Callable[..., Any]], ...] = (
        ("appliances", lambda n, r, w: _appliance(r, w, probs) or None),
        ("points", lambda n, r, w: _point(r, w, probs)),
        ("regions", lambda n, r, w: _region(r, w, probs)),
        ("surfaces", lambda n, r, w: _surface(n, r, w, probs)))
    for sec, parse in parts:
        raw = doc.get(sec)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            probs.append(f"{fname}: {sec} isn't a mapping: ignored")
            continue
        for name, entry in raw.items():
            if not isinstance(name, str) or not _ID.match(name):
                probs.append(f"{fname}: {sec}: {name!r} isn't a name ({ID_RE}): left out")
                continue
            got = parse(name, entry, f"{fname}: {sec}.{name}")
            if got:
                out[sec][name] = got
    return out


# -- the view (merge and build; pure) --------------------------------------------------------------------------------
def _room_surfaces(size: Optional[list[float]]) -> dict[str, dict]:
    """floor (always: z = 0 is the frame's floor), then the ceiling and walls once the size is known (§3.1)."""
    def s(name: str, origin: Vec3, normal: Vec3, u: Vec3, v: Vec3, wh: Optional[list[float]]) -> dict:
        return {"name": name, "label": SURFACE_LABELS[name], "origin": list(origin), "normal": list(normal),
                "u": list(u), "v": list(v), "size": wh}
    if size is None:
        return {"floor": s("floor", ZERO, UP, (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), None)}
    X, Y, Z = size
    e, n, w, so, up, dn = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, -1.0, 0.0), UP, (0.0, 0.0, -1.0)
    return {"floor": s("floor", ZERO, up, e, n, [X, Y]),
            "ceiling": s("ceiling", (0.0, 0.0, Z), dn, e, n, [X, Y]),
            "wall-n": s("wall-n", (0.0, Y, 0.0), so, e, up, [X, Z]),      # facing it from inside: west -> east
            "wall-e": s("wall-e", (X, Y, 0.0), w, so, up, [Y, Z]),        # north -> south
            "wall-s": s("wall-s", (X, 0.0, 0.0), n, w, up, [X, Z]),       # east -> west
            "wall-w": s("wall-w", (0.0, 0.0, 0.0), e, n, up, [Y, Z])}     # south -> north


def _merge(hand: dict, added: dict, probs: list[str]) -> tuple[dict, dict[str, set]]:
    """The two files' sections merged (the hand file wins: §3.2). Returns (sections, appliance id -> the place keys
    the hand file sets)."""
    out: dict[str, dict] = {"room": dict(hand["room"]), "appliances": {}, "points": {}, "regions": {}, "surfaces": {}}
    for k, v in added["room"].items():
        if k in out["room"]:
            if out["room"][k] != v:
                probs.append(f"{ADDED_NAME}: room.{k} is set in {HAND_NAME} too: {HAND_NAME}'s is used")
        else:
            out["room"][k] = v
    for sec in ("points", "regions", "surfaces"):
        out[sec] = {n: {**e, "_src": "hand"} for n, e in hand[sec].items()}
        for n, e in added[sec].items():
            if n in out[sec]:
                probs.append(f"{ADDED_NAME}: {sec}.{n} is in {HAND_NAME} too: {HAND_NAME}'s is used")
            else:
                out[sec][n] = {**e, "_src": "added"}
    hand_keys: dict[str, set] = {}
    for aid in list(hand["appliances"]) + [a for a in added["appliances"] if a not in hand["appliances"]]:
        h, a = hand["appliances"].get(aid, {}), added["appliances"].get(aid, {})
        e = dict(a)
        for k, v in h.items():
            if k in a and a[k] != v:
                probs.append(f"{ADDED_NAME}: appliances.{aid}.{k} is set in {HAND_NAME} too: {HAND_NAME}'s is used")
            e[k] = v
        out["appliances"][aid] = e
        hand_keys[aid] = set(h)
    return out, hand_keys


def _box(points: list[Sequence[float]]) -> Optional[list[list[float]]]:
    if not points:
        return None
    return [[min(p[i] for p in points) for i in range(3)], [max(p[i] for p in points) for i in range(3)]]


def build_view(hand: Any, added: Any = None, *, error: Optional[str] = None,
               problems: Optional[list[str]] = None) -> contract.SpaceView:
    """The SpaceView of a hand file's mapping and an added file's (either None: empty). Pure; ``problems`` are lines
    to put first (the loader's own)."""
    probs: list[str] = list(problems or [])
    hp = parse_doc(hand, HAND_NAME, probs)
    ap = parse_doc(added, ADDED_NAME, probs)
    m, hand_keys = _merge(hp, ap, probs)
    size = m["room"].get("size")
    front = m["room"].get("front", DEFAULT_FRONT)
    fv = FRONTS[front]
    room = {"size": size, "front": front, "centre": [size[0] / 2, size[1] / 2, size[2] / 2] if size else None,
            "right": [round(x, 6) for x in _cross(fv, UP)], "front_vec": list(fv)}
    surfaces: dict[str, dict] = _room_surfaces(size)
    for name, e in m["surfaces"].items():
        n, upv = _v3(e["normal"]), _v3(e["up"])
        surfaces[name] = {"name": name, "label": e.get("label") or humanise(name), "origin": e["origin"],
                          "normal": e["normal"], "u": [round(x, 6) for x in _cross(upv, n)], "v": e["up"],
                          "size": e.get("size")}
    points = {}
    for name, e in m["points"].items():
        p = {"label": e.get("label") or humanise(name), "at": e["at"],
             "from": "hand" if e["_src"] == "hand" else e.get("from", "page")}
        if e.get("device"):
            p["device"] = e["device"]
        points[name] = p
    regions = {}
    for name, e in m["regions"].items():
        if e["surface"] not in surfaces:
            probs.append(f"regions.{name}: there's no surface {e['surface']!r} (set the room's size first, or add it "
                         f"under surfaces): left out")
            continue
        regions[name] = {"label": e.get("label") or humanise(name), "surface": e["surface"], "centre": e["centre"],
                         "size": e["size"]}
    appliances = {}
    for aid, e in m["appliances"].items():
        a: dict[str, Any] = {"at": e.get("at")}
        for k in ("kind", "label", "mount", "plane"):
            if k in e:
                a[k] = e[k]
        a["teach"] = list(e.get("teach") or [])
        a["pose"] = e.get("pose")
        a["editable"] = not (hand_keys.get(aid, set()) & set(PLACE_KEYS))
        appliances[aid] = a
    if size:
        extent: Optional[list[list[float]]] = [[0.0, 0.0, 0.0], [float(x) for x in size]]
    else:
        placed = [p["at"] for p in points.values()] + [r["centre"] for r in regions.values()]
        for a in appliances.values():
            placed += [x for x in (a["at"], (a["pose"] or {}).get("at")) if x]
        extent = _box(placed)
    body = {"room": room, "surfaces": surfaces, "points": points, "regions": regions, "appliances": appliances,
            "extent": extent}
    rev = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]
    return {"revision": rev, "error": error, "problems": list(dict.fromkeys(probs)),   # type: ignore[typeddict-item]
            **body}


def empty_view() -> contract.SpaceView:
    """No room, nothing placed (before the first poll)."""
    v = build_view(None, None)
    v["revision"] = ""
    return v


# -- the store ---------------------------------------------------------------------------------------------------------
ADDED_HEADER = """\
# config/room/space.added.yaml: the room as set on the Lights page (the Room map, and Move it's "Point it at…"). WRITTEN
# BY show_service's rig API (/show/api/v1/rig/space/..., .../teach; docs/LIGHTS.md §3, §8.1), whole, on every change:
# don't edit it by hand. show_service merges it under config/room/space.yaml (the hand file, the same keys), which wins
# on a clash (a problem line), and re-reads both when they change. appliances.<id>.teach are a mover's taught places
# and .pose its solved mounting (space.solve_pose).
"""


def added_path_for(path: pathlib.Path) -> pathlib.Path:
    """The added file next to a space.yaml (``Store``'s default)."""
    return pathlib.Path(path).parent / ADDED_NAME


def write_added(path: pathlib.Path, doc: dict) -> None:
    """The added file, whole and atomically (a tmp file in the same directory, fsync, os.replace): ADDED_HEADER, then
    ``version`` and SECTIONS in order. Raises OSError."""
    import yaml
    body = {"version": VERSION, **{s: dict(doc.get(s) or {}) for s in SECTIONS}}
    text = ADDED_HEADER + yaml.safe_dump(body, sort_keys=False, default_flow_style=None, allow_unicode=True, width=120)
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


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _check_id(name: Any, what: str) -> str:
    if not isinstance(name, str) or not _ID.match(name):
        raise Refused("bad_request", f"{name!r} isn't a {what} name (lower-case letters, digits, - _ .)")
    return name


def _one(parse: Callable[[list[str]], Any], what: str) -> Any:
    """Run one checker; its first problem line becomes the refusal."""
    probs: list[str] = []
    out = parse(probs)
    if out is None:
        raise Refused("bad_request", probs[0] if probs else f"{what} can't be used")
    return out


class Store:
    """config/room/space.yaml + space.added.yaml (module docstring). ``poll`` and the writers from show-brain (one at a
    time: an RLock); ``view`` from any thread (swapped by reference)."""

    def __init__(self, path: pathlib.Path = contract.SPACE_FILE, *,
                 added_path: Optional[pathlib.Path] = None) -> None:
        self.path = pathlib.Path(path)
        if added_path is not None:
            self.added_path = pathlib.Path(added_path)
        elif self.path == contract.SPACE_FILE:
            self.added_path = contract.SPACE_ADDED_FILE
        else:
            self.added_path = added_path_for(self.path)
        self._view: contract.SpaceView = empty_view()
        self._sig: Any = ()                          # () = never polled
        self._docs: dict[str, Any] = {"hand": None, "added": None}   # each file's last good mapping
        self._lock = threading.RLock()
        self._subs: list = []

    # -- reading -------------------------------------------------------------------------------------------------------
    def view(self) -> contract.SpaceView:
        return self._view

    @property
    def revision(self) -> str:
        return self._view["revision"]

    def subscribe(self, fn: Callable[["Store"], None]) -> None:
        """``fn(store)`` after each change, on the polling thread. A bound method is held weakly (an engine that is
        gone drops out); a failing subscriber is logged, never raised."""
        ref = weakref.WeakMethod(fn) if hasattr(fn, "__self__") else (lambda f=fn: f)
        with self._lock:
            self._subs.append(ref)

    def poll(self, force: bool = False) -> bool:
        """Re-read both files when either changed (mtime or size). True when the view in use changed."""
        with self._lock:
            changed = self._poll(force)
            subs = list(self._subs) if changed else []
        for ref in subs:
            fn = ref()
            if fn is None:
                continue
            try:
                fn(self)
            except Exception:                            # a subscriber's bug never stops the poller
                log.exception("space: a subscriber failed")
        if changed:
            with self._lock:
                self._subs = [r for r in self._subs if r() is not None]
        return changed

    @staticmethod
    def _stat(p: pathlib.Path) -> Optional[tuple]:
        try:
            st = p.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    @staticmethod
    def _read(p: pathlib.Path) -> tuple[Any, Optional[str]]:
        """(mapping, None), or (None, why) when it doesn't read. Missing: ({}, None)."""
        import yaml
        try:
            data = p.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}, None
        except (OSError, UnicodeDecodeError) as e:
            return None, f"{p.name}: {getattr(e, 'strerror', None) or e}"
        try:
            doc = yaml.safe_load(data)
        except Exception as e:
            return None, f"{p.name}: {type(e).__name__}: {e}"
        doc = {} if doc is None else doc
        if not isinstance(doc, dict):
            return None, f"{p.name}: the top level isn't a mapping"
        return doc, None

    def _poll(self, force: bool) -> bool:
        sig = (self._stat(self.path), self._stat(self.added_path))
        if not force and sig == self._sig:
            return False
        self._sig = sig
        errors = []
        for key, p in (("hand", self.path), ("added", self.added_path)):
            doc, err = self._read(p)
            if err is None:
                self._docs[key] = doc
            else:
                errors.append(err + " (keeping its last good contents)")
                log.warning("space: %s", errors[-1])
        error = "; ".join(errors) or None
        view = build_view(self._docs["hand"], self._docs["added"], error=error)
        changed = view["revision"] != self._view["revision"]
        self._view = view
        if changed:
            log.info("space: loaded %s + %s (revision %s; %d places, %d appliances, room %s)", self.path.name,
                     self.added_path.name, view["revision"], len(view["points"]), len(view["appliances"]),
                     view["room"]["size"] or "size unset")
        return changed

    # -- writing -------------------------------------------------------------------------------------------------------
    def _hand_now(self) -> dict[str, dict]:
        """The hand file's sections as they read now (the last good when it doesn't read)."""
        doc, err = self._read(self.path)
        return parse_doc(self._docs["hand"] if err else doc, HAND_NAME, [])

    def _added_now(self) -> dict:
        doc, err = self._read(self.added_path)
        if err is not None:
            raise Refused("added_unreadable", f"{err}: fix or remove it first, then try again")
        doc = copy.deepcopy(doc)
        for s in SECTIONS:
            if not isinstance(doc.get(s), dict):
                doc[s] = {}
        return doc

    def _write(self, mutate: Callable[[dict[str, dict], dict], Any]) -> Any:
        """mutate(hand sections, the added mapping to change) under the lock; refuses a result the loader would
        complain about; then writes and polls."""
        with self._lock:
            hand = self._hand_now()
            added = self._added_now()
            new = copy.deepcopy(added)
            out = mutate(hand, new)
            before: list[str] = []
            after: list[str] = []
            parse_doc(added, ADDED_NAME, before)
            parse_doc(new, ADDED_NAME, after)
            fresh = [p for p in after if p not in before]
            if fresh:
                raise Refused("bad_request", fresh[0], problems=fresh)
            write_added(self.added_path, new)
            self.poll(force=True)
            return out

    def set_room(self, size: Any = None, front: Any = None) -> contract.SpaceView:
        """The Room map's "How big is the room?" and "Which wall do people face?". None leaves that as it is; ``size
        []`` clears it. front: north|east|south|west (or n|e|s|w)."""
        val: dict[str, Any] = {}
        if size is not None:
            val["size"] = None if size == [] else _one(
                lambda p: _sizes(size, 3) or p.append("The room's size is three lengths in metres (east-west, "
                                                       "north-south, floor to ceiling)."), "size")
        if front is not None:
            val["front"] = _one(lambda p: _front_word(front) or p.append(f"{front!r} isn't n, e, s or w."), "front")

        def mutate(hand: dict, new: dict) -> None:
            clash = [k for k, v in val.items() if k in hand["room"]]
            if clash:
                raise Refused("in_hand_file", f"The room's {' and '.join(clash)} is set in {HAND_NAME}: change it "
                                              f"there")
            room = dict(new["room"] or {})
            for k, v in val.items():
                if v is None:
                    room.pop(k, None)
                else:
                    room[k] = v
            new["room"] = room
        self._write(mutate)
        return self._view

    def set_appliance(self, aid: str, *, at: Any = ..., mount: Any = ..., plane: Any = ..., kind: Any = ...,
                      label: Any = ...) -> dict:
        """Place an appliance (a fixture id, or a room device): a key left out is unchanged, a key given as None
        clears it. Its taught places and pose stay. Returns its entry in the new view."""
        aid = _check_id(aid, "light or device")
        given = {k: v for k, v in (("at", at), ("mount", mount), ("plane", plane), ("kind", kind), ("label", label))
                 if v is not ...}
        val = {k: (None if v is None else _one(lambda p, k=k, v=v: _place_value(k, v, f"{aid} {k}", p), k))
               for k, v in given.items()}

        def mutate(hand: dict, new: dict) -> None:
            clash = [k for k in val if k in hand["appliances"].get(aid, {})]
            if clash:
                raise Refused("in_hand_file", f"{aid}'s {' and '.join(clash)} is set in {HAND_NAME}: change it there")
            e = dict(new["appliances"].get(aid) or {})
            for k, v in val.items():
                if v is None:
                    e.pop(k, None)
                else:
                    e[k] = v
            if e:
                new["appliances"][aid] = e
            else:
                new["appliances"].pop(aid, None)
        self._write(mutate)
        return copy.deepcopy(self._view["appliances"].get(aid)) or {"at": None, "teach": [], "pose": None,
                                                                     "editable": True}

    def remove_appliance(self, aid: str) -> None:
        """Forget an appliance's entry on the page: its place, taught places and pose (the hand file's stays)."""
        aid = _check_id(aid, "light or device")

        def mutate(hand: dict, new: dict) -> None:
            new["appliances"].pop(aid, None)
        if self._in_added("appliances", aid) is not None:
            self._write(mutate)

    def set_point(self, name: str, at: Any, label: Optional[str] = None, source: str = "page", *,
                  device: Any = ...) -> dict:
        """A named place lights can aim at ("The piano", "Mirror ball"). ``source`` is "page" or the fixture whose
        saved position made it (§3.6). ``device``: the room device it belongs to (left out: unchanged)."""
        name = _check_id(name, "place")
        pos = _one(lambda p: _vec(at, 3) or p.append("A place needs three numbers in metres ([x, y, z])."), "at")
        lab = None
        if label is not None:
            lab = _one(lambda p: _label(label) or p.append("That isn't a name."), "label")
        if not isinstance(source, str) or not (source == "page" or _ID.match(source)):
            raise Refused("bad_request", f"{source!r} isn't page or a light's id")
        if device is not ... and device is not None and not (isinstance(device, str) and _DEVICE.match(device)):
            raise Refused("bad_request", f"{device!r} isn't a room device name")

        def mutate(hand: dict, new: dict) -> None:
            if name in hand["points"]:
                raise Refused("in_hand_file", f"{name} is set in {HAND_NAME}: change it there")
            old = dict(new["points"].get(name) or {})
            e: dict[str, Any] = {"label": lab or old.get("label") or humanise(name), "at": pos}
            dev = old.get("device") if device is ... else device
            if dev:
                e["device"] = dev
            e["from"] = source
            new["points"][name] = e
        self._write(mutate)
        return copy.deepcopy(self._view["points"].get(name)) or {}

    def remove_point(self, name: str) -> None:
        self._remove("points", _check_id(name, "place"))

    def set_region(self, name: str, surface: str, centre: Any, size: Any, label: Optional[str] = None) -> dict:
        """A named area on a surface (a dance floor): its centre (room m) and size along the surface (m)."""
        name = _check_id(name, "area")
        c = _one(lambda p: _vec(centre, 3) or p.append("An area's centre is three numbers in metres."), "centre")
        wh = _one(lambda p: _sizes(size, 2) or p.append("An area's size is two lengths in metres."), "size")
        if not isinstance(surface, str) or surface not in self._view["surfaces"]:
            raise Refused("bad_request", f"There's no surface {surface!r} (set the room's size first)",
                          choices=sorted(self._view["surfaces"]))
        lab = None
        if label is not None:
            lab = _one(lambda p: _label(label) or p.append("That isn't a name."), "label")

        def mutate(hand: dict, new: dict) -> None:
            if name in hand["regions"]:
                raise Refused("in_hand_file", f"{name} is set in {HAND_NAME}: change it there")
            old = dict(new["regions"].get(name) or {})
            new["regions"][name] = {"label": lab or old.get("label") or humanise(name), "surface": surface,
                                    "centre": c, "size": wh}
        self._write(mutate)
        return copy.deepcopy(self._view["regions"].get(name)) or {}

    def remove_region(self, name: str) -> None:
        self._remove("regions", _check_id(name, "area"))

    def _remove(self, sec: str, name: str) -> None:
        def mutate(hand: dict, new: dict) -> None:
            if name in hand[sec]:
                raise Refused("in_hand_file", f"{name} is set in {HAND_NAME}: change it there")
            new[sec].pop(name, None)
        if name in self._hand_now()[sec] or self._in_added(sec, name) is not None:
            self._write(mutate)

    def _in_added(self, sec: str, name: str) -> Any:
        """The added file's raw entry (None: not there, or the file doesn't read: the write then says why)."""
        doc, err = self._read(self.added_path)
        if err is not None:
            return {}
        part = (doc or {}).get(sec)
        return part.get(name) if isinstance(part, dict) else None

    def add_sample(self, aid: str, s: Mapping[str, Any]) -> list[dict]:
        """Store one taught place for a mover (Move it's "Point it at…"); ``t`` defaults to now. The last TEACH_MAX
        are kept. Returns its taught places."""
        aid = _check_id(aid, "light")
        sample = _one(lambda p: _sample(dict(s) if isinstance(s, Mapping) else s, "That taught place", p), "sample")
        sample["t"] = sample["t"] or _now_iso()

        def mutate(hand: dict, new: dict) -> None:
            if "teach" in hand["appliances"].get(aid, {}):
                raise Refused("in_hand_file", f"{aid}'s taught places are set in {HAND_NAME}: change them there")
            e = dict(new["appliances"].get(aid) or {})
            e["teach"] = (list(e.get("teach") or []) + [sample])[-TEACH_MAX:]
            new["appliances"][aid] = e
        self._write(mutate)
        return copy.deepcopy(self._view["appliances"].get(aid, {}).get("teach") or [])

    def remove_sample(self, aid: str, index: int) -> list[dict]:
        """Drop one taught place (0-based, as listed)."""
        aid = _check_id(aid, "light")

        def mutate(hand: dict, new: dict) -> None:
            e = dict(new["appliances"].get(aid) or {})
            teach = list(e.get("teach") or [])
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(teach):
                raise Refused("not_found", f"There's no taught place {index!r} (it has {len(teach)})")
            del teach[index]
            if teach:
                e["teach"] = teach
            else:
                e.pop("teach", None)
            if e:
                new["appliances"][aid] = e
            else:
                new["appliances"].pop(aid, None)
        self._write(mutate)
        return copy.deepcopy(self._view["appliances"].get(aid, {}).get("teach") or [])

    def clear_samples(self, aid: str) -> None:
        self._drop_key(_check_id(aid, "light"), "teach")

    def set_pose(self, aid: str, pose: Optional[Mapping[str, Any]]) -> None:
        """Store (or with None, forget) a mover's solved pose."""
        aid = _check_id(aid, "light")
        if pose is None:
            self._drop_key(aid, "pose")
            return
        p = _one(lambda pr: _pose(dict(pose), "That pose", pr), "pose")

        def mutate(hand: dict, new: dict) -> None:
            e = dict(new["appliances"].get(aid) or {})
            e["pose"] = p
            new["appliances"][aid] = e
        self._write(mutate)

    def _drop_key(self, aid: str, key: str) -> None:
        def mutate(hand: dict, new: dict) -> None:
            e = dict(new["appliances"].get(aid) or {})
            e.pop(key, None)
            if e:
                new["appliances"][aid] = e
            else:
                new["appliances"].pop(aid, None)
        cur = self._in_added("appliances", aid)
        if isinstance(cur, dict) and (key in cur or not cur):
            self._write(mutate)


_SHARED: Optional[Store] = None
_SHARED_LOCK = threading.Lock()


def shared() -> Store:
    """The process's one Store of config/room/space.yaml (+ space.added.yaml): the director, the rig's look engine and
    the API read the same room. Created (and loaded once) on first use; polled by its readers."""
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = Store()
            try:
                _SHARED.poll()
            except Exception:                            # never stops a caller: the view stays empty
                log.exception("space: first load failed")
        return _SHARED


# -- lookups ---------------------------------------------------------------------------------------------------------
def position(view: Mapping[str, Any], aid: str) -> Optional[Vec3]:
    """An appliance's position: where the map puts it, else its solved pose's."""
    a = (view.get("appliances") or {}).get(aid)
    if not a:
        return None
    at = a.get("at") or (a.get("pose") or {}).get("at")
    return _v3(at) if at else None


def _surface_centre(s: Mapping[str, Any]) -> Vec3:
    o = _v3(s["origin"])
    if not s.get("size"):
        return o
    w, h = s["size"]
    return _add(o, _add(_mul(_v3(s["u"]), w / 2), _mul(_v3(s["v"]), h / 2)))


def resolve(view: Mapping[str, Any], name: str) -> Optional[Vec3]:
    """A space name's room position: points, then regions (centre), appliances (at, else the pose's), surfaces
    (centre; an unbounded one's origin). None: no such name."""
    if not isinstance(name, str):
        return None
    p = (view.get("points") or {}).get(name)
    if p:
        return _v3(p["at"])
    r = (view.get("regions") or {}).get(name)
    if r:
        return _v3(r["centre"])
    a = position(view, name)
    if a is not None:
        return a
    s = (view.get("surfaces") or {}).get(name)
    return _surface_centre(s) if s else None


def region_of(view: Mapping[str, Any], name: str) -> Optional[tuple[dict, Vec3, list[float]]]:
    """(its surface, its centre, its [w, d] along the surface) for a region name."""
    r = (view.get("regions") or {}).get(name)
    if not r:
        return None
    s = (view.get("surfaces") or {}).get(r["surface"])
    return (s, _v3(r["centre"]), list(r["size"])) if s else None


def surface_point(s: Mapping[str, Any], u: float, v: float) -> Vec3:
    """origin + u·û + v·v̂ (m)."""
    o, su, sv = s["origin"], s["u"], s["v"]
    return (o[0] + u * su[0] + v * sv[0], o[1] + u * su[1] + v * sv[1], o[2] + u * su[2] + v * sv[2])


def surface_uv(s: Mapping[str, Any], p: Sequence[float]) -> tuple[float, float]:
    """A room point's (u, v) on a surface (projected along its normal)."""
    o = s["origin"]
    d = (p[0] - o[0], p[1] - o[1], p[2] - o[2])
    return _dot(d, s["u"]), _dot(d, s["v"])                       # type: ignore[arg-type]


def _inside(s: Mapping[str, Any], p: Sequence[float], tol: float) -> bool:
    if not s.get("size"):
        return True
    u, v = surface_uv(s, p)
    w, h = s["size"]
    return -tol <= u <= w + tol and -tol <= v <= h + tol


def nearest_surface(view: Mapping[str, Any], p: Sequence[float]) -> Optional[dict]:
    """The surface nearest a room point: among those whose bounds hold its projection, else any."""
    best, best_in = None, None
    for s in (view.get("surfaces") or {}).values():
        d = abs(_dot(_sub(_v3(p), _v3(s["origin"])), _v3(s["normal"])))
        if best is None or d < best[0]:
            best = (d, s)
        if _inside(s, p, 0.05) and (best_in is None or d < best_in[0]):
            best_in = (d, s)
    pick = best_in or best
    return pick[1] if pick else None


def surface_of(view: Mapping[str, Any], name: str) -> Optional[dict]:
    """A surface by name; a region's surface; else the surface nearest a place (point, appliance)."""
    surfaces = view.get("surfaces") or {}
    if name in surfaces:
        return surfaces[name]
    r = region_of(view, name)
    if r:
        return r[0]
    p = resolve(view, name)
    return nearest_surface(view, p) if p is not None else None


def targets(view: Mapping[str, Any]) -> list[dict]:
    """Every aimable name with its page words (GET /api/v1/rig/space's ``targets``): places, areas, placed
    appliances, then surfaces. ``label`` is None for an appliance with none (the page uses the light's name)."""
    out = [{"name": n, "label": p["label"], "kind": "point", "at": p["at"]} for n, p in view["points"].items()]
    out += [{"name": n, "label": r["label"], "kind": "region", "at": r["centre"]} for n, r in view["regions"].items()]
    for n in view["appliances"]:
        at = position(view, n)
        if at is not None:
            out.append({"name": n, "label": view["appliances"][n].get("label"), "kind": "appliance", "at": list(at)})
    out += [{"name": n, "label": s["label"], "kind": "surface", "at": list(_surface_centre(s))}
            for n, s in view["surfaces"].items()]
    return out


# -- flow geometry (§3.5) ----------------------------------------------------------------------------------------------
class _Geom:
    """A view's derived numbers for the flow maths (cached per view object)."""
    __slots__ = ("view", "front", "right", "axis", "lo", "hi", "corners", "fcorners", "half_r", "height", "_far")

    def __init__(self, view: Mapping[str, Any]) -> None:
        self.view = view
        room = view.get("room") or {}
        self.front: Vec3 = FRONTS.get(room.get("front") or DEFAULT_FRONT, FRONTS[DEFAULT_FRONT])
        self.right: Vec3 = _unit(_cross(self.front, UP))
        ext = view.get("extent")
        self.lo: Optional[Vec3] = _v3(ext[0]) if ext else None
        self.hi: Optional[Vec3] = _v3(ext[1]) if ext else None
        c = room.get("centre")
        self.axis: Optional[Vec3] = _v3(c) if c else (_mul(_add(self.lo, self.hi), 0.5) if ext else None)
        self.corners: list[Vec3] = []
        if self.lo is not None and self.hi is not None:
            lo, hi = self.lo, self.hi
            self.corners = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
            self.half_r = sum(abs(self.right[i]) * (hi[i] - lo[i]) / 2 for i in range(3))
            self.height = (lo[2], hi[2])
        else:
            self.half_r = 0.0
            self.height = None
        self.fcorners = [self.fold(c)[0] for c in self.corners]
        self._far: dict[tuple, float] = {}

    def far(self, c: Vec3, mirror: bool) -> float:
        """The largest distance from c to a corner of the extent (folded when mirrored); memoised per centre."""
        key = (c, mirror)
        d = self._far.get(key)
        if d is None:
            d = max((_len(_sub(q, c)) for q in (self.fcorners if mirror else self.corners)), default=0.0)
            if len(self._far) > 64:                       # a flow's centre changes rarely: a small memo is plenty
                self._far.clear()
            self._far[key] = d
        return d

    def lateral(self, p: Vec3) -> float:
        return _dot(_sub(p, self.axis), self.right) if self.axis is not None else 0.0

    def fold(self, p: Vec3) -> tuple[Vec3, bool]:
        """Mirror symmetry: a point left of the front axis, reflected onto the right half (and whether it was)."""
        lat = self.lateral(p)
        return (_sub(p, _mul(self.right, 2 * lat)), True) if lat < 0 else (p, False)


_GEOM: dict[int, _Geom] = {}
_GEOM_LOCK = threading.Lock()
_GEOM_MAX = 8


def _geom(view: Mapping[str, Any]) -> _Geom:
    g = _GEOM.get(id(view))
    if g is not None and g.view is view:
        return g
    g = _Geom(view)
    with _GEOM_LOCK:
        _GEOM[id(view)] = g                              # holds the view, so its id can't be reused while cached
        while len(_GEOM) > _GEOM_MAX:
            del _GEOM[next(iter(_GEOM))]
    return g


def lateral(view: Mapping[str, Any], p: Sequence[float]) -> float:
    """Signed distance (m) right of the front axis through the room's centre (else the extent's); 0 without either."""
    return _geom(view).lateral(_v3(p))


def _flow_centre(g: _Geom, centre: Optional[Sequence[float]]) -> Optional[Vec3]:
    return _v3(centre) if centre is not None else g.axis


def _vector(g: _Geom, d: str, c: Optional[Vec3], p: Vec3) -> Vec3:
    if d == "up":
        return UP
    if d == "down":
        return (0.0, 0.0, -1.0)
    if d == "right":
        return g.right
    if d == "left":
        return _mul(g.right, -1.0)
    if c is None:
        return ZERO
    if d in ("cw", "ccw"):
        h = (p[0] - c[0], p[1] - c[1], 0.0)
        v = _unit(_cross(h, UP))                          # north of c -> east: clockwise from above
        return v if d == "cw" else _mul(v, -1.0)
    if d in ("outward", "inward"):
        v = _unit(_sub(p, c))
        return v if d == "outward" else _mul(v, -1.0)
    if d in ("towards", "away"):
        v = _unit(_sub(c, p))
        return v if d == "towards" else _mul(v, -1.0)
    return ZERO


def flow_vector(view: Mapping[str, Any], dir: str, centre: Optional[Sequence[float]], p: Sequence[float], *,
                symmetry: str = "none") -> Vec3:
    """The flow's direction at room point ``p`` (unit, or zero: still, an unknown word, or at its centre). ``centre``
    is the flow's ``at`` resolved (None: the room's centre). ``symmetry`` "mirror": the left half of the room mirrors
    the right across the front axis."""
    g = _geom(view)
    p3 = _v3(p)
    c = _flow_centre(g, centre)
    if symmetry == "mirror" and g.axis is not None:
        pf, flipped = g.fold(p3)
        cf = g.fold(c)[0] if c is not None else None
        v = _vector(g, dir, cf, pf)
        return _sub(v, _mul(g.right, 2 * _dot(v, g.right))) if flipped else v
    return _vector(g, dir, c, p3)


def flow_coord(view: Mapping[str, Any], dir: str, centre: Optional[Sequence[float]], p: Sequence[float], *,
               symmetry: str = "none") -> float:
    """Where ``p`` sits along the flow, 0..1 (0 = where the wave starts; §3.5's table). 0 for still, an unknown word,
    or nothing placed. ``symmetry`` "mirror" folds the room onto its right half, so both halves fire together."""
    g = _geom(view)
    if g.lo is None or g.hi is None or dir not in contract.FLOW_DIRS or dir == "still":
        return 0.0
    p3 = _v3(p)
    c = _flow_centre(g, centre)
    corners = g.corners
    if symmetry == "mirror" and g.axis is not None:
        p3 = g.fold(p3)[0]
        c = g.fold(c)[0] if c is not None else None
        corners = g.fcorners
    if dir in ("up", "down"):
        lo, hi = g.lo[2], g.hi[2]
        k = 0.5 if hi - lo <= EPS else _clamp01((p3[2] - lo) / (hi - lo))
        return k if dir == "up" else 1.0 - k
    if c is None:
        return 0.0
    d = _sub(p3, c)
    if dir in ("right", "left"):
        h = g.half_r
        k = 0.5 if h <= EPS else _clamp01((_dot(d, g.right) + h) / (2 * h))
        return k if dir == "right" else 1.0 - k
    if dir in ("cw", "ccw"):
        x, y = _dot(d, g.right), _dot(d, g.front)
        if math.hypot(x, y) <= EPS:
            return 0.0
        k = (math.atan2(x, y) % TAU) / TAU                 # the front = 0, a quarter turn clockwise = 0.25
        return k if dir == "cw" else (1.0 - k) % 1.0
    far = g.far(c, corners is g.fcorners)
    if far <= EPS:
        return 0.0
    k = _clamp01(_len(d) / far)
    return k if dir in ("outward", "away") else 1.0 - k


def screen_flow(view: Mapping[str, Any], screen_id: str, dir: str, centre: Optional[Sequence[float]], *,
                symmetry: str = "none") -> Optional[contract.ScreenFlow]:
    """The flow as a screen sees it (§3.5): in its own plane, x right and y up as a viewer facing it sees them. A
    screen with no plane takes the nearest wall's (up = the room's up). None: it isn't placed."""
    s = position(view, screen_id)
    if s is None:
        return None
    a = view["appliances"][screen_id]
    pl = a.get("plane")
    if pl:
        n, up = _v3(pl["normal"]), _v3(pl["up"])
    else:
        walls = {k: v for k, v in (view.get("surfaces") or {}).items() if abs(v["normal"][2]) < 0.5}
        w = nearest_surface({"surfaces": walls}, s) if walls else None
        if w is None:
            return None
        n, up = _v3(w["normal"]), UP
    rs = _cross(up, n)                                    # its right, as a viewer facing it sees
    d = flow_vector(view, dir, centre, s, symmetry=symmetry)
    x, y, depth = _dot(d, rs), _dot(d, up), _dot(d, n)
    inplane = math.hypot(x, y)
    return {"fixture": screen_id, "x": round(x, 4), "y": round(y, 4),
            "angle_deg": round(math.degrees(math.atan2(y, x)), 2) if inplane > 1e-6 else 0.0,
            "inplane": round(min(1.0, inplane), 4), "depth": round(depth, 4),
            "radial": 1 if dir in ("outward", "away") else -1 if dir in ("inward", "towards") else 0,
            "spin": 1 if dir == "cw" else -1 if dir == "ccw" else 0,
            "phase": round(flow_coord(view, dir, centre, s, symmetry=symmetry), 4)}


def spatial_fn(view: Mapping[str, Any]) -> Callable[[str, str, str], bool]:
    """fixtures.select's ``spatial(fid, key, value)``: ``side:<n|e|s|w|north|…|front|back|left|right>`` (which half of
    the room, split through its centre) and ``height:<low|mid|high>`` (the room's height in thirds; the extent's
    without a size). An unplaced fixture is on no side and at no height."""
    g = _geom(view)

    def spatial(fid: str, key: str, value: str) -> bool:
        p = position(view, fid)
        if p is None or not isinstance(value, str):
            return False
        w = value.strip().lower()
        if key == "side":
            if g.axis is None:
                return False
            d = _sub(p, g.axis)
            w = SIDES.get(w, w)
            if w in FRONTS:
                return _dot(d, FRONTS[w]) > EPS
            if w in ("front", "back"):
                f = _dot(d, g.front)
                return f > EPS if w == "front" else f < -EPS
            if w in ("left", "right"):
                r = _dot(d, g.right)
                return r > EPS if w == "right" else r < -EPS
            return False
        if key == "height" and w in HEIGHTS and g.height is not None:
            lo, hi = g.height
            if hi - lo <= EPS:
                return False
            third = min(2, int(3 * (p[2] - lo) / (hi - lo))) if p[2] > lo else 0
            return HEIGHTS[third] == w
        return False
    return spatial


# -- movers: the forward model, aim and ray hits (§3.6) ----------------------------------------------------------------
def beam_dir(pose: Mapping[str, Any], pan: float, tilt: float) -> Vec3:
    """The room direction (unit) of a mover's beam at (pan, tilt), centred degrees."""
    R = _rotm(tuple(pose["rot"]))                         # type: ignore[arg-type]
    b = _bf(pose["pan_sign"] * pan, pose["tilt_sign"] * tilt + pose["tilt_offset_deg"])
    return (_dot(R[0], b), _dot(R[1], b), _dot(R[2], b))


def _window(rng: Sequence[float], lim: Optional[Sequence[float]]) -> tuple[float, float]:
    """The range ∩ the owner's soft limits (limits outside the range, or not two numbers: the range)."""
    lo, hi = float(min(rng)), float(max(rng))
    if lim is None:
        return lo, hi
    ends = _vec(lim, 2)
    if ends is not None:
        a, b = max(lo, min(ends)), min(hi, max(ends))
        if a <= b:
            return a, b
    return lo, hi


def aim(pose: Mapping[str, Any], q: Sequence[float], *, current: Sequence[float], pan_range: Sequence[float],
        tilt_range: Sequence[float], limits: Optional[Mapping[str, Any]] = None,
        speed: Optional[Sequence[float]] = None) -> contract.Aim:
    """The pan and tilt that put a calibrated mover's beam on room point ``q`` (inverse kinematics). Of every solution
    inside its range and soft limits (both beam branches, each pan a turn either way), the one it reaches soonest from
    ``current`` (max(|Δpan|/v_pan, |Δtilt|/v_tilt), ``speed`` = (v_pan, v_tilt) deg/s): no flips while the current
    branch still reaches. None reachable: the nearest it can point, ``reachable`` False with the angle off (never
    refused)."""
    P = pose["at"]
    v = (q[0] - P[0], q[1] - P[1], q[2] - P[2])
    cp, ct = (float(current[0]), float(current[1])) if current is not None else (0.0, 0.0)
    lim = limits or {}
    plo, phi_ = _window(pan_range, lim.get("pan"))
    tlo, thi = _window(tilt_range, lim.get("tilt"))
    vp, vt = (float(speed[0]), float(speed[1])) if speed else DEFAULT_SPEED
    vp, vt = max(vp, EPS), max(vt, EPS)
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if n <= EPS:                                          # aimed at itself: any way will do
        return {"pan": min(phi_, max(plo, cp)), "tilt": min(thi, max(tlo, ct)), "reachable": True, "error_deg": 0.0}
    R = _rotm(tuple(pose["rot"]))                         # type: ignore[arg-type]
    bx = (R[0][0] * v[0] + R[1][0] * v[1] + R[2][0] * v[2]) / n      # Rᵀ·v̂: the target in the fixture's frame
    by = (R[0][1] * v[0] + R[1][1] * v[1] + R[2][1] * v[2]) / n
    bz = (R[0][2] * v[0] + R[1][2] * v[1] + R[2][2] * v[2]) / n
    phi_t = math.degrees(math.acos(1.0 if bz > 1.0 else -1.0 if bz < -1.0 else bz))
    th_t = math.degrees(math.atan2(by, bx))
    ps, ts, off = pose["pan_sign"], pose["tilt_sign"], pose["tilt_offset_deg"]
    branches = ((ps * th_t, ts * (phi_t - off)), (ps * (th_t + 180.0), ts * (-phi_t - off)))   # (pan, tilt) at turn 0
    best: Optional[tuple[float, float, float, float]] = None
    for pan0, tilt0 in branches:
        for k in range(math.ceil((plo - 1e-9 - pan0) / 360.0), math.floor((phi_ + 1e-9 - pan0) / 360.0) + 1):
            pan = pan0 + 360.0 * k
            dp = abs(pan - cp)
            for m in range(math.ceil((tlo - 1e-9 - tilt0) / 360.0), math.floor((thi + 1e-9 - tilt0) / 360.0) + 1):
                tilt = tilt0 + 360.0 * m
                dt = abs(tilt - ct)
                cost = dp / vp if dp / vp > dt / vt else dt / vt
                if best is None or cost < best[0] - 1e-12 or (cost <= best[0] + 1e-12 and dp + dt < best[1]):
                    best = (cost, dp + dt, pan, tilt)
    if best is not None:
        return {"pan": min(phi_, max(plo, best[2])), "tilt": min(thi, max(tlo, best[3])), "reachable": True,
                "error_deg": 0.0}
    # none inside: the nearest it can point (each solution, a turn either way, clamped into the window), then the
    # soonest
    vu = (v[0] / n, v[1] / n, v[2] / n)
    pick: Optional[tuple[float, float, float, float]] = None
    seen = set()
    for pan, tilt in ((p0 + 360.0 * k, t0 + 360.0 * m) for p0, t0 in branches for k in (-2, -1, 0, 1, 2)
                      for m in (-1, 0, 1)):
        c = (min(phi_, max(plo, pan)), min(thi, max(tlo, tilt)))
        key = (round(c[0], 6), round(c[1], 6))
        if key in seen:
            continue
        seen.add(key)
        err = _angle_deg(beam_dir(pose, c[0], c[1]), vu)
        cost = max(abs(c[0] - cp) / vp, abs(c[1] - ct) / vt)
        if pick is None or err < pick[0] - 1e-9 or (abs(err - pick[0]) <= 1e-9 and cost < pick[1]):
            pick = (err, cost, c[0], c[1])
    assert pick is not None
    return {"pan": pick[2], "tilt": pick[3], "reachable": False, "error_deg": round(pick[0], 3)}


def ray_hit(view: Mapping[str, Any], origin: Sequence[float], d: Sequence[float]) -> Optional[tuple[Vec3, str]]:
    """Where a ray from ``origin`` along ``d`` first lands on a surface (bounded ones within their size; the room
    box's first on a tie, then the named ones): (point, surface name), or None."""
    o, dv = _v3(origin), _v3(d)
    best: Optional[tuple[float, Vec3, str]] = None
    for name, s in (view.get("surfaces") or {}).items():
        n = s["normal"]
        den = dv[0] * n[0] + dv[1] * n[1] + dv[2] * n[2]
        if abs(den) <= EPS:
            continue
        so = s["origin"]
        t = ((so[0] - o[0]) * n[0] + (so[1] - o[1]) * n[1] + (so[2] - o[2]) * n[2]) / den
        if t <= 1e-6 or (best is not None and t >= best[0] - 1e-12):
            continue
        hit = (o[0] + t * dv[0], o[1] + t * dv[1], o[2] + t * dv[2])
        if _inside(s, hit, EDGE_M):
            best = (t, hit, name)
    return (best[1], best[2]) if best else None


# -- movers: the teach-and-solve (§3.6) --------------------------------------------------------------------------------
def _cal(cfg: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """show.yaml lights.calibration over CALIBRATION's starting points (a bad value: the starting point)."""
    out = dict(CALIBRATION, angle_sigma_deg=ANGLE_SIGMA_DEG)
    c = cfg if isinstance(cfg, Mapping) else {}
    fit = c.get("fit_deg")
    if isinstance(fit, (list, tuple)) and len(fit) == 3 and all(_num(x) is not None and x > 0 for x in fit):
        out["fit_deg"] = tuple(sorted(float(x) for x in fit))
    for k in ("min_spread_deg", "pos_sigma_m", "angle_sigma_deg"):
        x = _num(c.get(k))
        if x is not None and x > 0:
            out[k] = x
    return out


def _fmt(x: float) -> str:
    return f"{x:g}"


def fit_words(level: str, cal: Mapping[str, Any], n: int = 0) -> str:
    """The page words for a fit level (§3.6's table; the thresholds from config)."""
    fit = cal["fit_deg"]
    return {"need_more": f"Point it at 3 places so it learns where it hangs ({n} so far).",
            "spread": "Point it at places further apart.",
            "good": f"Spot on (within {_fmt(fit[0])}°).",
            "ok": f"Good (within {_fmt(fit[1])}°).",
            "rough": "Rough: point it at one more place.",
            "bad": "It doesn't fit: check the places on the map, and where the light hangs."}[level]


def _spread(dirs: list[Vec3]) -> tuple[float, float]:
    """(the widest angle between two taught directions, how far the rest leave the plane of the most square pair),
    degrees; from the commanded directions (signs +1, no offset: angles barely depend on them)."""
    wide, best, plane = 0.0, -1.0, ZERO
    for i in range(len(dirs)):
        for j in range(i + 1, len(dirs)):
            wide = max(wide, _angle_deg(dirs[i], dirs[j]))
            c = _cross(dirs[i], dirs[j])
            if _len(c) > best:
                best, plane = _len(c), c
    nrm = _unit(plane)
    if nrm == ZERO:
        return wide, 0.0
    off = max(math.degrees(math.asin(min(1.0, abs(_dot(b, nrm))))) for b in dirs)
    return wide, off


def solve_pose(samples: Sequence[Mapping[str, Any]], *, at_hint: Optional[Sequence[float]], mount: Optional[str],
               pan_range: Sequence[float], tilt_range: Sequence[float], cfg: Optional[Mapping[str, Any]] = None,
               view: Optional[Mapping[str, Any]] = None) -> contract.PoseFit:
    """A mover's pose from its taught places (§3.6): least squares over its position, rotation and tilt offset, from
    multi-starts (pan sign × (the Kabsch rotation from the start position, hung or standing × 8 headings), each
    scouted briefly and the SOLVE_STARTS best run to convergence; a tilt sign of -1 is the same pose as the heading
    + 180° with the offset negated, so the fit reports +1; a pan sign read the other way is the offset ± 180°, so it
    reports the one with |offset| <= 90°). ``at_hint`` (the map's placement) pulls the position at
    ``pos_sigma_m``; without it, the room's centre (at ceiling height when hung) at PRIOR_SIGMA_M, from ``view`` (else
    the shared store's, when one is running). ``cfg`` is show.yaml lights.calibration. ``pan_range``/``tilt_range``
    are the fixture's (the model has no range; aim keeps to them). A rough or bad fit still carries its pose (degrade:
    the page says so); need_more has none. A fit whose position is loosely tied down (``pos_m``, from the fit's
    Jacobian at angle_sigma_deg, over ``pos_sigma_m``) reads rough however small its rms. Pure, ~0.1-0.4 s: call it
    off show-brain."""
    cal = _cal(cfg)
    good = [s for s in (_sample(dict(x), "", []) if isinstance(x, Mapping) else None for x in samples) if s]
    n = len(good)
    dirs = [_bf(s["pan"], s["tilt"]) for s in good]
    wide, offline = _spread(dirs)
    out: contract.PoseFit = {"pose": None, "rms_deg": None, "worst_deg": None, "worst_index": None,
                             "spread_deg": round(wide, 2), "samples": n, "level": "need_more",
                             "words": fit_words("need_more", cal, n), "pos_m": None}
    if n < 3:
        return out
    if wide < cal["min_spread_deg"] or (at_hint is None and offline < cal["min_spread_deg"] * OFFLINE_SHARE):
        out["words"] = fit_words("spread", cal)
        return out

    import numpy as np
    from scipy.optimize import least_squares
    from scipy.spatial.transform import Rotation

    pan = np.radians([s["pan"] for s in good])
    tilt = np.radians([s["tilt"] for s in good])
    Q = np.array([s["at"] for s in good], dtype=float)
    sig_a = math.radians(cal["angle_sigma_deg"])
    prior: Optional[np.ndarray] = None
    sig_p = cal["pos_sigma_m"]
    if at_hint is not None:
        prior = np.array(_v3(at_hint))
    else:
        v = view
        if v is None and _SHARED is not None:
            v = _SHARED.view()
        size = ((v or {}).get("room") or {}).get("size")
        if size:
            prior = np.array([size[0] / 2, size[1] / 2, size[2] if mount in ("ceiling", "truss") else size[2] / 2])
            sig_p = PRIOR_SIGMA_M
    if prior is not None:
        P0 = prior
    else:
        P0 = Q.mean(axis=0) + np.array([0.0, 0.0, -GUESS_RISE_M if mount == "floor" else GUESS_RISE_M])

    def rotm(rv: np.ndarray) -> np.ndarray:
        th = float(np.linalg.norm(rv))
        if th < 1e-12:
            return np.eye(3)
        k = rv / th
        K = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
        return np.eye(3) + math.sin(th) * K + (1.0 - math.cos(th)) * (K @ K)

    def beams(x: np.ndarray, ps: int) -> tuple[np.ndarray, np.ndarray]:
        th, ph = ps * pan, tilt + x[6]
        sp = np.sin(ph)
        d = np.stack([sp * np.cos(th), sp * np.sin(th), np.cos(ph)], axis=1) @ rotm(x[3:6]).T
        t = Q - x[:3]
        t = t / np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-9)
        return d, t

    def fun(x: np.ndarray, ps: int) -> np.ndarray:
        d, t = beams(x, ps)
        r = ((d - t) / sig_a).ravel()
        return np.concatenate([r, (x[:3] - prior) / sig_p]) if prior is not None else r

    def skew(a: np.ndarray) -> np.ndarray:            # rows of vectors -> their cross-product matrices
        S = np.zeros(a.shape[:-1] + (3, 3))
        S[..., 0, 1], S[..., 0, 2], S[..., 1, 2] = -a[..., 2], a[..., 1], -a[..., 0]
        S[..., 1, 0], S[..., 2, 0], S[..., 2, 1] = a[..., 2], -a[..., 1], a[..., 0]
        return S

    def jac(x: np.ndarray, ps: int) -> np.ndarray:
        """fun's Jacobian, analytic: d/dP of the unit direction to each place, d/d rot through SO(3)'s left
        Jacobian (R(rv + δ) ≈ Exp(J_l δ)·R(rv)), d/d offset of the beam."""
        th, ph = ps * pan, tilt + x[6]
        sp, cp, ct, st = np.sin(ph), np.cos(ph), np.cos(th), np.sin(th)
        R = rotm(x[3:6])
        A = np.stack([sp * ct, sp * st, cp], axis=1) @ R.T
        dB = np.stack([cp * ct, cp * st, -sp], axis=1) @ R.T
        w = Q - x[:3]
        wn = np.maximum(np.linalg.norm(w, axis=1), 1e-9)
        t = w / wn[:, None]
        dP = (np.eye(3)[None] - t[:, :, None] * t[:, None, :]) / wn[:, None, None]
        rv = x[3:6]
        a = float(np.linalg.norm(rv))
        F = skew(rv)
        if a < 1e-8:
            Jl = np.eye(3) + F / 2 + F @ F / 6
        else:
            Jl = np.eye(3) + (1 - math.cos(a)) / a ** 2 * F + (a - math.sin(a)) / a ** 3 * (F @ F)
        blk = np.concatenate([dP, -skew(A) @ Jl, dB[:, :, None]], axis=2) / sig_a
        J = blk.reshape(3 * n, 7)
        if prior is not None:
            J = np.vstack([J, np.hstack([np.eye(3) / sig_p, np.zeros((3, 4))])])
        return J

    m = 3 * n + (3 if prior is not None else 0)
    t0 = Q - P0
    t0 = t0 / np.maximum(np.linalg.norm(t0, axis=1, keepdims=True), 1e-9)
    starts: list[tuple[float, int, np.ndarray]] = []
    for ps in (1, -1):
        B = np.stack([np.sin(tilt) * np.cos(ps * pan), np.sin(tilt) * np.sin(ps * pan), np.cos(tilt)], axis=1)
        U, _, Vt = np.linalg.svd(B.T @ t0)               # Kabsch: the rotation taking the beams onto the places
        R0 = Vt.T @ np.diag([1.0, 1.0, float(np.sign(np.linalg.det(Vt.T @ U.T)) or 1.0)]) @ U.T
        rots = [Rotation.from_matrix(R0).as_rotvec()]
        for hung in (True, False):                       # and the grid: hung or standing × 8 headings
            base = Rotation.from_euler("x", math.pi) if hung else Rotation.identity()
            rots += [(Rotation.from_euler("z", k * math.pi / 4) * base).as_rotvec() for k in range(8)]
        for r0 in rots:
            x0 = np.concatenate([P0, r0, [0.0]])
            try:                                         # a short scouting run from every start...
                r = least_squares(fun, x0, jac=jac, args=(ps,), method="lm" if m >= 7 else "trf",
                                  max_nfev=SOLVE_SCOUT_NFEV)
            except Exception:                            # a start that fails is skipped
                continue
            starts.append((float(r.cost), ps, r.x))
    starts.sort(key=lambda s: s[0])
    best = None
    for _, ps, x0 in starts[:SOLVE_STARTS]:              # ...then the most promising ones, refined
        try:
            r = least_squares(fun, x0, jac=jac, args=(ps,), method="lm" if m >= 7 else "trf",
                              max_nfev=SOLVE_MAX_NFEV)
        except Exception:
            continue
        if best is None or r.cost < best[0].cost:
            best = (r, ps)
    if best is None:
        out.update(level="bad", words=fit_words("bad", cal))
        return out
    r, ps = best
    d, t = beams(r.x, ps)
    chord = np.linalg.norm(d - t, axis=1)
    err = np.degrees(2.0 * np.arcsin(np.clip(chord / 2.0, 0.0, 1.0)))
    rms, worst, wi = float(np.sqrt(np.mean(err ** 2))), float(err.max()), int(err.argmax())
    # how far the position may be off, taught angles good to angle_sigma_deg: places that leave it loosely tied down
    # (a weak spread, or 3 places and no map position) read rough however small the rms
    J = jac(r.x, ps)
    cov = np.linalg.pinv(J.T @ J)
    pos_m = float(math.sqrt(max(0.0, float(np.linalg.eigvalsh(cov[:3, :3]).max()))))
    if np.linalg.matrix_rank(J.T @ J) < 7:
        pos_m = math.inf
    Rm = rotm(r.x[3:6])
    off = (math.degrees(float(r.x[6])) + 180.0) % 360.0 - 180.0
    if abs(off) > 90.0:          # the same pose read the other way round: pan sign flipped, offset - 180°, R -> -R·M
        ps, off, Rm = -ps, (off - 180.0 + 180.0) % 360.0 - 180.0, -Rm @ np.diag([1.0, -1.0, 1.0])
    rot = Rotation.from_matrix(Rm).as_rotvec()            # canonical: |rot| <= π
    fit = cal["fit_deg"]
    level = "good" if rms <= fit[0] else "ok" if rms <= fit[1] else "rough" if rms <= fit[2] else "bad"
    if pos_m > cal["pos_sigma_m"] + 1e-6 and level in ("good", "ok"):
        level = "rough"
    pose: contract.MoverPose = {"at": [round(float(x), 4) for x in r.x[:3]], "rot": [round(float(x), 6) for x in rot],
                                "tilt_offset_deg": round(off, 4), "pan_sign": ps, "tilt_sign": 1,
                                "rms_deg": round(rms, 3), "worst_deg": round(worst, 3), "samples": n,
                                "solved_at": _now_iso()}
    out.update(pose=pose, rms_deg=round(rms, 3), worst_deg=round(worst, 3), worst_index=wi, level=level,
               words=fit_words(level, cal), pos_m=round(pos_m, 3) if math.isfinite(pos_m) else None)
    return out
