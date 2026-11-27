"""The light in the room, as a number (owner, 27 Nov: reflected light changes how a room reads, so scenes that depend
on low-power lights must bring the total down for them to be seen).

Pure: no I/O but ``load`` (config/room/photometry.yaml). The art director asks three things of it:

``lumens(entry, rgb)``
    What one fixture puts out showing ``rgb`` (0..255, its level already in it), from its type's photometry entry. An
    LED dims in proportion to its channel; a colour wheel passes the colour's own share of the white behind it.

``room_lux(lm, size, reflect)``
    The light those lumens make in a room of that size: direct and bounced, averaged over its surfaces (the
    integrating-sphere estimate lighting designers use: lumens / (area x (1 - mean reflectance))). The same sum the 3D
    room draws (src/pages/Room3D/light.ts RoomLight).

``scene_share(layers)``
    How much of its full level a scene's brightness layers leave on average: a resting scene (``fx: dim``, depth 1)
    none, a pulse about two thirds, a steady scene all of it.

``headroom(...)``
    The level the parts playing WITH a featured light must come down to so the room stays under what that light needs
    (docs/LIGHTS.md §7.5).

What the featured lights need (worked out with the 3D room's model; docs/LIGHTS.md §7.5 has the sums): a laser's
beams in the air read against a wall under about 1 lux in a light haze and 3 to 4 in a medium one; its dots are worth
hundreds of lux each and survive any room light, but not a moving head's pool on the same surface; a derby's blobs are
worth about 90 lux each and read under about 20 lux of room light.
"""
from __future__ import annotations

import math
import pathlib
from typing import Any, Mapping, Optional, Sequence

import yaml

from . import contract

PHOTOMETRY_FILE = contract.REPO_ROOT / "config" / "room" / "photometry.yaml"
FORMS = ("emitters", "wheel", "white", "fixed")
REFLECT_FALLBACK = {"ceiling": 0.7, "wall": 0.5, "floor": 0.2}
SIZE_FALLBACK = (6.0, 6.0, 3.0)
#: lights.room_light's starting points (show.yaml's values win): the least a part is brought down to; the light the
#: screens and anything else leave in the room whatever the lights do (lux); how long a level takes to move (s)
ROOM_LIGHT_FALLBACK = {"min_level": 0.12, "other_lux": 1.0, "glide_s": 2.0, "clear_of_feature": 0.2}
#: the mean of each brightness shape over its cycle (looks.shape: a swell is a short pulse)
FX_MEAN = {"dim": 0.0, "sine": 0.5, "triangle": 0.5, "saw": 0.5, "square": 0.5, "swell": 0.35}
WORD_DEPTH = 0.5                                 # a depth given as a word (swell, energy, ...): about half


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _lin(v: float) -> float:
    x = max(0.0, min(1.0, v / 255.0))
    return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4


def load(path: pathlib.Path = PHOTOMETRY_FILE, problems: Optional[list[str]] = None) -> dict:
    """``{"types": {file or type name: entry}, "reflect": {...}, "size": [x, y, h]}`` from photometry.yaml. A missing
    or bad file is no photometry (every light counts for nothing, so nothing is ever brought down); a bad entry is left
    out and named in ``problems``."""
    probs = problems if problems is not None else []
    out: dict = {"types": {}, "reflect": dict(REFLECT_FALLBACK), "size": list(SIZE_FALLBACK)}
    try:
        raw = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return out
    except Exception as e:
        probs.append(f"photometry.yaml: {type(e).__name__}: {e}: no photometry")
        return out
    if not isinstance(raw, Mapping):
        probs.append("photometry.yaml: not a mapping: no photometry")
        return out
    for name, e in (raw.get("types") or {}).items() if isinstance(raw.get("types"), Mapping) else ():
        ok = isinstance(e, Mapping) and len([k for k in e if k in FORMS]) == 1
        if ok and "emitters" in e:
            em = e["emitters"]
            ok = isinstance(em, Mapping) and all(_num(em.get(c)) is not None and em[c] >= 0 for c in "rgb")
        elif ok:
            k = next(k for k in e if k in FORMS)
            ok = _num(e[k]) is not None and e[k] >= 0
        if not ok:
            probs.append(f"photometry.yaml types.{name}: needs one of emitters {{r, g, b}}, wheel, white or fixed "
                         f"(lumens): left out")
            continue
        out["types"][str(name)] = dict(e)
    room = raw.get("room") if isinstance(raw.get("room"), Mapping) else {}
    out["reflect"] = reflect_of(room.get("reflect"), out["reflect"], probs, "photometry.yaml room.reflect")
    sz = room.get("size")
    if isinstance(sz, list) and len(sz) == 3 and all(_num(x) is not None and x > 0 for x in sz):
        out["size"] = [float(x) for x in sz]
    return out


def reflect_of(raw: Any, base: Mapping[str, float], problems: Optional[list[str]] = None, where: str = "reflect") -> dict:
    """``{ceiling, wall, floor}`` (each 0 .. 0.95) from ``raw`` over ``base``."""
    out = dict(base)
    if raw is None:
        return out
    if not isinstance(raw, Mapping):
        if problems is not None:
            problems.append(f"{where}: not a mapping: the standard room")
        return out
    for k in ("ceiling", "wall", "floor"):
        v = _num(raw.get(k))
        if raw.get(k) is None:
            continue
        if v is None or not 0.0 <= v <= 0.95:
            if problems is not None:
                problems.append(f"{where}.{k}: {raw.get(k)!r} isn't a share 0 .. 0.95: {out[k]}")
            continue
        out[k] = v
    return out


def lumens(entry: Optional[Mapping[str, Any]], rgb: Sequence[float]) -> float:
    """What one fixture of this photometry puts out showing ``rgb`` (0..255, level included)."""
    if not entry or rgb is None or len(rgb) < 3:
        return 0.0
    r, g, b = (max(0.0, min(255.0, float(x))) for x in rgb[:3])
    top = max(r, g, b)
    if top <= 0.0:
        return 0.0
    if "emitters" in entry:
        em = entry["emitters"]
        return (r * em["r"] + g * em["g"] + b * em["b"]) / 255.0
    if "wheel" in entry:
        # the colour's own share of white light (its luminance at full), at the dimmer its level sets
        k = 255.0 / top
        share = 0.2126 * _lin(r * k) + 0.7152 * _lin(g * k) + 0.0722 * _lin(b * k)
        return float(entry["wheel"]) * share * top / 255.0
    if "white" in entry:
        return float(entry["white"]) * top / 255.0
    return float(entry.get("fixed") or 0.0)


def lux_per_lumen(size: Sequence[float], reflect: Mapping[str, float]) -> float:
    """Lux in the room per lumen sent into it: 1 / (surface area x (1 - mean reflectance))."""
    x, y, h = (max(0.1, float(v)) for v in size[:3])
    walls, flat = 2.0 * (x + y) * h, x * y
    area = walls + 2.0 * flat
    rho = (walls * reflect["wall"] + flat * (reflect["ceiling"] + reflect["floor"])) / area
    return 1.0 / (area * (1.0 - min(0.95, rho)))


def room_lux(lm: float, size: Sequence[float], reflect: Mapping[str, float]) -> float:
    return max(0.0, lm) * lux_per_lumen(size, reflect)


def scene_share(layers: Any) -> float:
    """The share of its full level a scene's brightness layers leave on average, 0..1 (each layer multiplies, as in
    the look engine: 1 - depth + depth x the shape's mean)."""
    share = 1.0
    for ln in layers if isinstance(layers, (list, tuple)) else ():
        if not isinstance(ln, Mapping) or ln.get("kind", "intensity") != "intensity":
            continue
        fx = ln.get("fx")
        if fx not in FX_MEAN:
            continue
        d = ln.get("depth", 1.0)
        d = _num(d) if _num(d) is not None else WORD_DEPTH
        d = max(0.0, min(1.0, d))
        share *= 1.0 - d + d * FX_MEAN[fx]
    return share


def headroom(target_lux: float, other_lux: float, feature_lux: float, with_lux: float, min_level: float) -> float:
    """The level (``min_level`` .. 1) for the parts playing with a featured light, so the room stays under
    ``target_lux``: ``other_lux`` is what the screens and anything else leave whatever the lights do, ``feature_lux``
    the featured lights' own, ``with_lux`` what the other playing parts would add at their full level."""
    if with_lux <= 1e-6:
        return 1.0
    room = target_lux - other_lux - feature_lux
    return max(max(0.0, min(1.0, min_level)), min(1.0, room / with_lux))
