"""The room geometry planners share: how big and how stretched a beam's spot is where it lands, the walls opened out
into one strip round the room's corners, and which wall is which. Pure: no engine, no I/O. Metres and degrees.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

Vec3 = tuple[float, float, float]

#: the room's walls in order round the room, clockwise seen from above (x east, y north), each between two corners
WALL_LOOP = ("wall-e", "wall-s", "wall-w", "wall-n")
FRONT_WALL = {"east": "wall-e", "south": "wall-s", "west": "wall-w", "north": "wall-n"}
ROUND_WEIGHT = 0.5         # a spot's score: its long side x stretch^this (a round spot beats a small streak)
GRAZE_COS = 0.08           # a beam this close to parallel with a surface (about 85 degrees) draws no spot on it
MIN_THROW_M = 1.5          # a spot nearer its light than this is the wall beside it, not a spot in the room


def room_size(room: Mapping[str, Any]) -> Optional[tuple[float, float, float]]:
    s = (room.get("room") or {}).get("size")
    if isinstance(s, (list, tuple)) and len(s) == 3 and all(isinstance(x, (int, float)) and x > 0 for x in s):
        return float(s[0]), float(s[1]), float(s[2])
    return None


def front_wall(room: Mapping[str, Any]) -> str:
    return FRONT_WALL.get((room.get("room") or {}).get("front") or "", "wall-n")


def opposite(wall: str) -> str:
    i = WALL_LOOP.index(wall)
    return WALL_LOOP[(i + 2) % 4]


def _corners(X: float, Y: float) -> dict[str, tuple[tuple[float, float], tuple[float, float], tuple[float, float]]]:
    """Each wall: (its first corner, its last corner, its normal into the room), clockwise from above."""
    ne, se, sw, nw = (X, Y), (X, 0.0), (0.0, 0.0), (0.0, Y)
    return {"wall-e": (ne, se, (-1.0, 0.0)), "wall-s": (se, sw, (0.0, 1.0)),
            "wall-w": (sw, nw, (1.0, 0.0)), "wall-n": (nw, ne, (0.0, -1.0))}


def footprint(at: Sequence[float], q: Sequence[float], normal: Sequence[float], beam_deg: float
              ) -> tuple[float, float]:
    """A beam's spot where it lands at ``q`` on a surface with ``normal``: (its short side, its long side) in metres.
    The short side is the beam's width at that throw; the long side that over the cosine of the angle it lands at."""
    d = (q[0] - at[0], q[1] - at[1], q[2] - at[2])
    L = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
    if L <= 1e-9:
        return 0.0, 0.0
    cosi = abs(d[0] * normal[0] + d[1] * normal[1] + d[2] * normal[2]) / L
    minor = 2.0 * L * math.tan(math.radians(max(0.1, beam_deg)) / 2.0)
    return minor, minor / max(cosi, GRAZE_COS)


def spot_score(at: Sequence[float], q: Sequence[float], normal: Sequence[float], beam_deg: float) -> float:
    """How well a light draws a spot at ``q``: smaller is better. Its long side, made worse by how stretched it is
    (ROUND_WEIGHT): a round 1.5 m spot beats a 0.5 x 2 m streak. Inf when the beam would only graze the surface, or
    lands on it from behind."""
    d = (q[0] - at[0], q[1] - at[1], q[2] - at[2])
    L = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
    if L < MIN_THROW_M:
        return math.inf
    facing = -(d[0] * normal[0] + d[1] * normal[1] + d[2] * normal[2]) / L
    if facing < GRAZE_COS:
        return math.inf
    minor, major = footprint(at, q, normal, beam_deg)
    return major * (major / minor) ** ROUND_WEIGHT if minor > 0 else math.inf


@dataclass(frozen=True)
class Strip:
    """Walls opened out into one strip: s metres along it from its start, at one height."""
    walls: tuple[tuple[str, tuple[float, float], tuple[float, float], tuple[float, float]], ...]
    height: float
    length: float

    def point(self, s: float) -> tuple[Vec3, Vec3, str]:
        """(the room point s metres along, the wall's normal into the room, the wall's name); clamped to the strip."""
        s = min(max(s, 0.0), self.length)
        for name, a, b, n in self.walls:
            seg = math.hypot(b[0] - a[0], b[1] - a[1])
            if s <= seg or name == self.walls[-1][0]:
                f = s / seg if seg > 0 else 0.0
                f = min(max(f, 0.0), 1.0)
                return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, self.height), (n[0], n[1], 0.0), name
            s -= seg
        a = self.walls[-1][2]
        return (a[0], a[1], self.height), (self.walls[-1][3][0], self.walls[-1][3][1], 0.0), self.walls[-1][0]


def strip(room: Mapping[str, Any], *, height: float, walls: Optional[Sequence[str]] = None, margin: float = 0.0,
          reverse: bool = False) -> Optional[Strip]:
    """The walls ``walls`` (default: every wall but the front, the three in order round the room from the front's
    end) as one strip at ``height`` metres, ``margin`` metres short of the front wall at each end. None without a
    room size."""
    size = room_size(room)
    if size is None:
        return None
    X, Y, _Z = size
    cs = _corners(X, Y)
    if walls is None:
        f = WALL_LOOP.index(front_wall(room))
        walls = [WALL_LOOP[(f + i) % 4] for i in (1, 2, 3)]
    segs = []
    for w in walls:
        if w not in cs:
            return None
        a, b, n = cs[w]
        segs.append([w, a, b, n])
    if reverse:
        segs = [[w, b, a, n] for w, a, b, n in reversed(segs)]
    front = front_wall(room)
    fa, fb, _n = cs[front]
    for seg, end in ((segs[0], 1), (segs[-1], 2)):          # each end shortened by the margin where it meets the front
        a, b = seg[1], seg[2]
        corner = a if end == 1 else b
        if margin > 0 and (corner == fa or corner == fb):
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            m = min(margin, 0.45 * L)
            ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
            if end == 1:
                seg[1] = (a[0] + ux * m, a[1] + uy * m)
            else:
                seg[2] = (b[0] - ux * m, b[1] - uy * m)
    out = tuple((w, tuple(a), tuple(b), n) for w, a, b, n in segs)
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for _w, a, b, _n in out)
    return Strip(out, height, length)                      # type: ignore[arg-type]


def surface_normal(room: Mapping[str, Any], name: str) -> Optional[Vec3]:
    s = (room.get("surfaces") or {}).get(name)
    n = s.get("normal") if isinstance(s, Mapping) else None
    return (float(n[0]), float(n[1]), float(n[2])) if isinstance(n, (list, tuple)) and len(n) == 3 else None
