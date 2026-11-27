"""Staging: the room-geometry layer under the light scenes (docs/LIGHTS.md §5.10).

Owner, 27 Nov: the scene ideas "all ... require algebraic logic as part of the scene for implementation in the relevant
room geometry (which is contingent and shouldn't be hard coded)". A scene says what it wants in a few words; a planner
works out how this room and these lights can do it.

A scene's position layer names a planner::

    - {id: run, kind: position, mode: planned, plan: wall-run, with: {spots: 2}, phase: bar, period_bars: [8, 4]}

A planner (``planners.py``) is a pure function of
  * the room: space.build_view's view (its size, which wall is the front, its surfaces, regions, points, bars),
  * the layer's lights: where each one is, how it aims (inverse kinematics through its pose, within its range and soft
    limits), its beam angle, its speed and which bar it is on,
  * the motion stage's timing (motion.py),
and returns a :class:`Plan`: for each light a :class:`Track` (where it points and how bright it is at each moment of
one cycle of the layer), the shortest cycle in which every light can follow its track (``min_period_s``), and a line
saying what it made of the room. The look engine (looks.py ``mode: planned``) plays each track through its own aiming
(keep-out regions too) and its motion stage like any pattern, and multiplies the light's brightness by the track's.
A planner never sends anything and never runs per frame: the engine plans once per layer, space and rig.

A planner that can't fit the room plans nothing and says why (``Plan.problems``): the layer then leaves those lights
where the layers below it put them, as an unresolved target does.

Nothing here names a site fact: every length comes from the room, every angle from the light.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from .. import motion

Vec3 = tuple[float, float, float]
#: (pan, tilt, reachable) for a room point, nearest ``current`` (the light's own degrees)
AimFn = Callable[[Sequence[float], Sequence[float]], Optional[tuple[float, float, bool]]]

SAMPLES = 96              # keys per cycle a planner draws (a 4-bar cycle at 120 bpm: one every 83 ms)
SPEED_SHARE = 0.95        # a lit track may use this share of a light's top speed (looks.py SPEED_FIT)
HOLD_KEYS = 1             # a light that hands over stays where it was this many keys, dark, before it moves
JUMP_M = 1.0              # a spot that moves further than this between two keys has jumped (it is a new spot)
FLIP_DEG = 30.0           # a lit light whose aim would turn further than this between two keys hands the spot on


def _dist(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


@dataclass(frozen=True)
class Light:
    """One light a planner may use."""
    id: str
    at: Vec3                                     # where it is in the room
    beam_deg: float                              # its beam's full angle
    aim: AimFn                                   # room point -> its pan and tilt
    speed: tuple[float, float] = (180.0, 120.0)  # pan, tilt deg/s
    bar: Optional[str] = None                    # the bar it is part of (space.yaml bars), if any
    home: tuple[float, float] = (0.0, 0.0)


@dataclass(frozen=True)
class Key:
    """A track's place at one moment: ``w`` (0..1 of the cycle), the room point aimed at, the light's level 0..1."""
    w: float
    at: Vec3
    level: float


@dataclass(frozen=True)
class Track:
    """Where one light points and how bright it is over a cycle: keys in order of ``w``. Between two lit keys the aim
    moves in a straight line in the room; into or out of a dark key it is already at the next key's place (a dark
    light goes ahead to where it lights next). The level is a straight line between keys."""
    keys: tuple[Key, ...]

    def sample(self, w: float, loop: bool = True) -> tuple[Vec3, float]:
        ks = self.keys
        if len(ks) == 1:
            return ks[0].at, ks[0].level
        if loop:
            w -= math.floor(w)
        elif w >= ks[-1].w:
            return ks[-1].at, ks[-1].level
        elif w <= ks[0].w:
            return ks[0].at, ks[0].level
        lo, hi = 0, len(ks)                       # the last key at or before w (binary search)
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if ks[mid].w <= w:
                lo = mid
            else:
                hi = mid
        a = ks[lo]
        if a.w > w:                               # before the first key: the stretch from the last one, round the end
            a = ks[-1]
            b, span, f0 = ks[0], ks[0].w + 1.0 - a.w, w + 1.0 - a.w
        elif lo + 1 < len(ks):
            b = ks[lo + 1]
            span, f0 = b.w - a.w, w - a.w
        else:
            if not loop:
                return a.at, a.level
            b = ks[0]
            span, f0 = b.w + 1.0 - a.w, w - a.w
        f = f0 / span if span > 1e-12 else 0.0
        level = a.level + (b.level - a.level) * f
        if a.level > 0.0 and b.level > 0.0:
            at = (a.at[0] + (b.at[0] - a.at[0]) * f, a.at[1] + (b.at[1] - a.at[1]) * f,
                  a.at[2] + (b.at[2] - a.at[2]) * f)
        else:
            at = b.at
        return at, level


@dataclass
class Plan:
    tracks: dict[str, Track]
    min_period_s: float = 0.0                    # the shortest cycle every light can follow its track in
    problems: list[str] = field(default_factory=list)
    note: str = ""                               # what the planner made of the room (stats, the library)
    once: bool = False                           # the track has an end (it is played once from the scene's start)


@dataclass(frozen=True)
class Planner:
    name: str
    fn: Callable[..., Plan]
    defaults: Mapping[str, Any]                  # its ``with`` keys and their defaults
    words: Mapping[str, tuple[str, ...]]         # a key whose value is one of these words
    label: str


PLANNERS: dict[str, Planner] = {}


def planner(name: str, *, label: str, defaults: Mapping[str, Any],
            words: Optional[Mapping[str, tuple[str, ...]]] = None) -> Callable:
    def reg(fn: Callable[..., Plan]) -> Callable[..., Plan]:
        PLANNERS[name] = Planner(name, fn, dict(defaults), dict(words or {}), label)
        return fn
    return reg


def parse(name: Any, raw: Any) -> tuple[Optional[dict], list[str]]:
    """A layer's ``plan`` and ``with``, checked: (its settings over the planner's defaults, problems). None when the
    planner doesn't exist."""
    _load()
    pl = PLANNERS.get(name) if isinstance(name, str) else None
    if pl is None:
        return None, [f"plan {name!r} isn't one of {', '.join(sorted(PLANNERS))}"]
    probs: list[str] = []
    out = dict(pl.defaults)
    if raw is None:
        return out, probs
    if not isinstance(raw, Mapping):
        return out, [f"with {raw!r} isn't a mapping: the planner's own settings are used"]
    for k, v in raw.items():
        if k not in pl.defaults:
            probs.append(f"with.{k}: not a setting of {name} ({', '.join(sorted(pl.defaults))}): ignored")
            continue
        d = pl.defaults[k]
        if k in pl.words:
            vs = v if isinstance(v, (list, tuple)) else None
            ok = (all(isinstance(x, str) for x in vs) and bool(vs)) if vs is not None else isinstance(v, str)
            if not ok:
                probs.append(f"with.{k}: {v!r} isn't a word or a list of names: {d!r} is used")
                continue
            if vs is None and pl.words[k] and v not in pl.words[k]:
                probs.append(f"with.{k}: {v!r} isn't one of {', '.join(pl.words[k])}: {d!r} is used")
                continue
            out[k] = tuple(vs) if vs is not None else v
        elif isinstance(d, bool):
            if not isinstance(v, bool):
                probs.append(f"with.{k}: {v!r} isn't true or false: {d!r} is used")
                continue
            out[k] = v
        elif isinstance(d, (int, float)) or d is None:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                probs.append(f"with.{k}: {v!r} isn't a number >= 0: {d!r} is used")
                continue
            out[k] = int(v) if isinstance(d, int) and not isinstance(d, bool) and float(v).is_integer() else float(v)
        else:
            out[k] = v
    return out, probs


def make(name: str, params: Mapping[str, Any], room: Mapping[str, Any], lights: Sequence[Light], *,
         ramp_s: float = 0.0, jerk_s: float = 0.0) -> Plan:
    """Run a planner. Never raises: a planner that fails plans nothing and says so."""
    _load()
    pl = PLANNERS.get(name)
    if pl is None:
        return Plan({}, problems=[f"no planner {name!r}"])
    if not lights:
        return Plan({}, problems=["no light here can be aimed at a room point (none has a pose)"])
    try:
        plan = pl.fn(room, list(lights), dict(params))
    except Exception as e:                                   # a planner's bug never fails the frame
        return Plan({}, problems=[f"{name} failed: {type(e).__name__}: {e}"])
    if plan.tracks and plan.min_period_s <= 0.0:
        plan.min_period_s = min_period_s(plan, {lt.id: lt for lt in lights}, ramp_s, jerk_s)
    return plan


# -- the tools every planner shares ---------------------------------------------------------------------------------
def carry(spots: Sequence[Callable[[float], tuple[Vec3, Vec3]]], lights: Sequence[Light],
          score: Callable[[Light, Vec3, Vec3], float], *, n: int = SAMPLES, keep: float = 0.8,
          park: Optional[Vec3] = None) -> tuple[dict[str, Track], int]:
    """Spots that travel, each carried at every moment by the light that draws it best (``score``: smaller is better,
    inf: it can't): the light carrying a spot keeps it until another would draw it at under ``keep`` of its score
    (no flicker between two lights that draw it alike), and hands it over where they are alike. A light that isn't
    carrying anything is dark, holds where it was for HOLD_KEYS, then goes ahead to where it lights next. ``spots``:
    for each spot, w (0..1) -> (room point, its surface's normal). (tracks, how many keys a spot had no light.)"""
    who: dict[int, Optional[str]] = {j: None for j in range(len(spots))}
    carried: dict[str, list[Optional[tuple[Vec3, int]]]] = {lt.id: [None] * n for lt in lights}
    by_id = {lt.id: lt for lt in lights}
    ang: dict[str, tuple[float, float]] = {lt.id: lt.home for lt in lights}   # where each light last pointed, lit
    last_q: dict[int, Optional[Vec3]] = {j: None for j in range(len(spots))}
    had: dict[str, Optional[int]] = {}                       # the spot each light carried at the last key
    free_at: dict[str, int] = {}                             # when each light last let a spot go
    missing = 0
    for lap in (0, 1):                                       # twice round, so the start knows how the end left it
        for k in range(n):
            w = k / n
            busy: set[str] = set()
            carrying: set[str] = set()
            for j, path in enumerate(spots):
                q, nrm = path(w)
                prev = last_q[j]
                last_q[j] = q
                cur = who[j]
                if cur is not None and prev is not None and _dist(prev, q) > JUMP_M:
                    busy.add(cur)                            # the spot jumped (a run's end to its start): another
                    who[j] = cur = None                      # light takes it, the one that had it goes out
                ranked = []
                for lt in lights:
                    if lt.id in busy or had.get(lt.id) not in (None, j):   # it just had another spot: out first
                        continue
                    sc = score(lt, q, nrm)
                    if not math.isfinite(sc):
                        continue
                    if lt.id == cur:                         # carrying it: only while its aim follows smoothly
                        got = lt.aim(q, ang[lt.id])
                        if got is None or max(abs(got[0] - ang[lt.id][0]), abs(got[1] - ang[lt.id][1])) > FLIP_DEG:
                            continue
                    ranked.append((sc, lt.id))
                ranked.sort()
                if not ranked:
                    who[j] = None
                    if lap:
                        missing += 1
                    continue
                # the lights that draw it nearly as well as the best: the one carrying it keeps it; else the one
                # that has been free longest (it has had time to get there in the dark)
                near = [(free_at.get(i, -1), s, i) for s, i in ranked if s * keep <= ranked[0][0]]
                pick = cur if cur is not None and any(i == cur for _f, _s, i in near) else min(near)[2]
                got = by_id[pick].aim(q, ang[pick])
                if got is not None:
                    ang[pick] = (got[0], got[1])
                who[j] = pick
                busy.add(pick)
                carrying.add(pick)
                had[pick] = j
                if lap:
                    carried[pick][k] = (q, j)
            tick = lap * n + k
            for lt in lights:                               # who is free now, and since when
                if lt.id not in carrying:
                    if had.get(lt.id) is not None:
                        free_at[lt.id] = tick
                    had[lt.id] = None
    tracks: dict[str, Track] = {}
    for lt in lights:
        row = carried[lt.id]
        lit = [k for k in range(n) if row[k] is not None]
        if not lit:
            if park is not None:
                tracks[lt.id] = Track((Key(0.0, park, 0.0),))
            continue
        keys = []
        for k in range(n):
            if row[k] is not None:
                keys.append(Key(k / n, row[k][0], 1.0))
                continue
            back = next(((k - d) % n for d in range(1, HOLD_KEYS + 1) if row[(k - d) % n] is not None), None)
            if back is not None:                             # just handed over: hold, dark
                keys.append(Key(k / n, row[back][0], 0.0))
                continue
            nxt = next((k + d) % n for d in range(1, n + 1) if row[(k + d) % n] is not None)
            keys.append(Key(k / n, row[nxt][0], 0.0))       # dark: already where it lights next
        tracks[lt.id] = Track(tuple(keys))
    return tracks, missing


def min_period_s(plan: Plan, lights: Mapping[str, Light], ramp_s: float, jerk_s: float) -> float:
    """The shortest cycle in which every light follows its track truly: while lit, no faster than SPEED_SHARE of its
    top speed; while dark, time enough to get from where it went dark to where it lights next (motion.travel_s,
    from rest to rest)."""
    worst = 0.0
    for fid, tr in plan.tracks.items():
        lt = lights.get(fid)
        if lt is None or len(tr.keys) < 2:
            continue
        cur: Sequence[float] = lt.home
        angs: list = []
        for _lap in ((0,) if plan.once else (0, 1)):         # a loop twice round: its start follows on from its end
            angs = []
            for k in tr.keys:
                got = lt.aim(k.at, cur)
                if got is None:
                    angs = []
                    break
                cur = (got[0], got[1])
                angs.append(cur)
        if not angs:
            continue
        ks = tr.keys
        m = len(ks)
        vp, vt = lt.speed[0] * SPEED_SHARE, lt.speed[1] * SPEED_SHARE
        ends = m if not plan.once else m - 1
        for i in range(ends):
            a, b = ks[i], ks[(i + 1) % m]
            dw = (b.w - a.w) % 1.0 or (1.0 if m == 1 else 0.0)
            if dw <= 1e-9:
                continue
            dp, dq = abs(angs[(i + 1) % m][0] - angs[i][0]), abs(angs[(i + 1) % m][1] - angs[i][1])
            if a.level > 0.0 and b.level > 0.0:
                need = max(dp / vp if vp > 0 else 0.0, dq / vt if vt > 0 else 0.0) / dw
                worst = max(worst, need)
        # each dark stretch: from where it went dark to where it lights again
        i = 0
        while i < m:
            if ks[i].level > 0.0 and ks[(i + 1) % m].level <= 0.0:
                j = (i + 1) % m
                steps = 0
                while ks[j].level <= 0.0 and steps < m:
                    j = (j + 1) % m
                    steps += 1
                if steps < m and (not plan.once or j > i):
                    dw = (ks[j].w - ks[i].w) % 1.0
                    d = motion.travel_s(angs[j][0] - angs[i][0], angs[j][1] - angs[i][1], lt.speed, ramp_s, jerk_s)
                    if dw > 1e-9 and d > 0.0:
                        worst = max(worst, d / dw)
            i += 1
    return worst


def lights_on_bars(lights: Sequence[Light]) -> dict[Optional[str], list[Light]]:
    out: dict[Optional[str], list[Light]] = {}
    for lt in lights:
        out.setdefault(lt.bar, []).append(lt)
    return out


_LOADED = False


def _load() -> None:
    global _LOADED
    if not _LOADED:
        _LOADED = True
        from . import planners  # noqa: F401  (registers them)
