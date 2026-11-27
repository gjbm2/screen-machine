"""The planners (staging/__init__.py says what one is). Each works only from the room and the lights it is given.

  wall-run   spots chase each other round the walls above head height: every wall but the front (the bars' wall,
             the picture), opened out into one strip. Each spot is carried by whichever light draws it roundest
             there and handed on where two draw it alike; a light not carrying one goes ahead in the dark.
  ring       spots chase each other round a circle on the floor, as many as the circle holds without their pools
             running into each other, carried as wall-run's are.
  glide      two white dots glide level along one wall, a fixed gap apart, stop, and go out (the gun-barrel opening):
             the two heads of the one bar that draws round spots along the longest stretch of that wall. With
             ``end``, once: the dots then swing onto that place and stay (a song's lead-in).
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Sequence

from . import SAMPLES, Key, Light, Plan, Track, carry, geom, lights_on_bars, planner

FLOOR_N = (0.0, 0.0, 1.0)


def _reach(lt: Light, q: Sequence[float]) -> bool:
    got = lt.aim(q, lt.home)
    return got is not None and bool(got[2])


def _score(lt: Light, q: Sequence[float], n: Sequence[float]) -> float:
    if not _reach(lt, q):
        return math.inf
    return geom.spot_score(lt.at, q, n, lt.beam_deg)


@planner("wall-run", label="Spots chasing round the walls",
         defaults={"spots": 2, "height": 0.7, "margin": 0.4, "direction": "cw"},
         words={"direction": ("cw", "ccw")})
def wall_run(room: Mapping[str, Any], lights: list[Light], p: dict) -> Plan:
    """``spots`` spots, evenly spaced, chasing along every wall but the front at ``height`` (a share of the ceiling's
    height), ``margin`` metres short of the front wall at each end; ``direction`` cw or ccw seen from above."""
    size = geom.room_size(room)
    if size is None:
        return Plan({}, problems=["the room has no size (space.yaml room.size): no walls to run along"])
    st = geom.strip(room, height=min(0.95, max(0.1, p["height"])) * size[2], margin=p["margin"],
                    reverse=p["direction"] == "ccw")
    if st is None or st.length <= 0.5:
        return Plan({}, problems=["the walls are too short to run along"])
    n = max(1, min(int(p["spots"]), len(lights)))
    paths = []
    for j in range(n):
        def path(w: float, _j: int = j) -> tuple[tuple, tuple]:
            q, nrm, _wall = st.point(((w + _j / n) % 1.0) * st.length)
            return q, nrm
        paths.append(path)
    mid = st.point(st.length / 2.0)[0]
    tracks, missing = carry(paths, lights, _score, park=mid)
    probs = []
    if missing:
        probs.append(f"no light reaches {missing * 100 // (SAMPLES * n)}% of the run: the spot goes out there")
    walls = " → ".join(w for w, *_ in st.walls)
    return Plan(tracks, problems=probs, note=f"{n} spot(s) along {walls} ({st.length:.1f} m) at {st.height:.1f} m")


@planner("ring", label="Spots chasing round a circle on the floor",
         defaults={"centre": ("dance-floor", "centre"), "fill": 0.8, "spots": 0, "direction": "cw"},
         words={"centre": (), "direction": ("cw", "ccw")})
def ring(room: Mapping[str, Any], lights: list[Light], p: dict) -> Plan:
    """Round the first of ``centre`` that the room has (a region: its middle and size; a point; the room's centre),
    ``fill`` of the way to its nearer edge. ``spots`` 0: as many as fit without their pools touching."""
    from .. import space
    size = geom.room_size(room)
    names = p["centre"] if isinstance(p["centre"], tuple) else (p["centre"],)
    c, half = None, None
    for nm in names:
        r = space.region_of(room, nm) if nm != "centre" else None
        if r is not None:
            c, half = r[1], min(r[2]) / 2.0
            break
        q = space.resolve(room, nm) if nm != "centre" else None
        if q is not None:
            c = (q[0], q[1], 0.0)
            break
        if nm == "centre" and size is not None:
            c = (size[0] / 2.0, size[1] / 2.0, 0.0)
            break
    if c is None:
        return Plan({}, problems=[f"none of {', '.join(names)} is in the room"])
    if half is None:
        half = min(size[0], size[1]) / 4.0 if size is not None else 1.0
    rad = max(0.2, min(1.0, p["fill"]) * half)
    sign = -1.0 if p["direction"] == "cw" else 1.0          # clockwise seen from above
    # the pools' long side round the circle, from the light that draws each place best
    longs = []
    for k in range(12):
        a = 2.0 * math.pi * k / 12
        q = (c[0] + rad * math.cos(a), c[1] + rad * math.sin(a), c[2])
        best = min((geom.footprint(lt.at, q, FLOOR_N, lt.beam_deg)[1] for lt in lights if _reach(lt, q)),
                   default=None)
        if best is not None:
            longs.append(best)
    if not longs:
        return Plan({}, problems=["no light reaches the circle"])
    pool = sum(longs) / len(longs)
    fit = max(1, int(2.0 * math.pi * rad / (1.15 * pool)))
    n = int(p["spots"]) or fit
    n = max(1, min(n, len(lights)))
    paths = []
    for j in range(n):
        def path(w: float, _j: int = j) -> tuple[tuple, tuple]:
            a = sign * 2.0 * math.pi * (w + _j / n)
            return (c[0] + rad * math.cos(a), c[1] + rad * math.sin(a), c[2]), FLOOR_N
        paths.append(path)
    tracks, missing = carry(paths, lights, _score, park=c)
    probs = []
    if n > fit:
        probs.append(f"{n} pools of about {pool:.1f} m on a circle of {rad:.1f} m run into each other "
                     f"({fit} would fit)")
    if missing:
        probs.append("some of the circle is out of every light's reach: the spot goes out there")
    return Plan(tracks, problems=probs,
                note=f"{n} spot(s) round {rad:.1f} m at ({c[0]:.1f}, {c[1]:.1f}); pools about {pool:.1f} m")


@planner("glide", label="Two dots glide along a wall, stop and go out",
         defaults={"wall": "back", "height": 0.6, "gap": 0.0, "glide": 0.6, "hold": 0.15, "end": (),
                   "round": 1.4, "margin": 0.3},
         words={"wall": ("back", "wall-n", "wall-e", "wall-s", "wall-w"), "end": ()})
def glide(room: Mapping[str, Any], lights: list[Light], p: dict) -> Plan:
    """Along ``wall`` (back: the wall facing the front) at ``height`` (a share of the ceiling's height): the two
    heads of the one bar whose spots stay round (long side at most ``round`` x the short) along the longest stretch;
    ``gap`` metres apart (0: a little more than a spot). Of the cycle: ``glide`` gliding, ``hold`` stopped, the rest
    dark, going back. ``end`` (Room map names): once, then onto the first that resolves, and held there."""
    from .. import space
    size = geom.room_size(room)
    if size is None:
        return Plan({}, problems=["the room has no size (space.yaml room.size)"])
    wall = geom.opposite(geom.front_wall(room)) if p["wall"] == "back" else p["wall"]
    st = geom.strip(room, height=min(0.95, max(0.1, p["height"])) * size[2], walls=[wall])
    if st is None:
        return Plan({}, problems=[f"no wall {wall}"])
    nrm = st.point(0.0)[1]
    step = 0.1
    ns = int(st.length / step) + 1
    pts = [st.point(min(st.length, i * step))[0] for i in range(ns)]
    lo_i, hi_i = int(p["margin"] / step), ns - 1 - int(p["margin"] / step)
    bars = lights_on_bars(lights)
    pairs = [sorted(ls, key=lambda x: x.id)[:2] for ls in bars.values() if len(ls) >= 2]
    if not pairs and len(lights) >= 2:
        pairs = [sorted(lights, key=lambda x: x.id)[:2]]
    if not pairs:
        return Plan({}, problems=["it needs two lights that aim"])

    def best_run(pair: list[Light], rnd: float) -> tuple[int, int, float]:
        """(first, last sample, mean spot size) of the longest stretch both draw round, or (0, -1, inf)."""
        best = (0, -1, math.inf)
        run = None
        for i in range(lo_i, hi_i + 2):
            ok = False
            if i <= hi_i:
                ok = True
                for lt in pair:
                    if not _reach(lt, pts[i]):
                        ok = False
                        break
                    mi, ma = geom.footprint(lt.at, pts[i], nrm, lt.beam_deg)
                    if mi <= 0 or ma / mi > rnd:
                        ok = False
                        break
            if ok and run is None:
                run = i
            elif not ok and run is not None:
                a, b = run, i - 1
                mean = sum(geom.footprint(lt.at, pts[k], nrm, lt.beam_deg)[1] for lt in pair
                           for k in range(a, b + 1)) / (2 * (b - a + 1))
                if b - a > best[1] - best[0] or (b - a == best[1] - best[0] and mean < best[2]):
                    best = (a, b, mean)
                run = None
        return best

    pick = None
    for rnd in (p["round"], p["round"] * 1.5, math.inf):
        for pair in pairs:
            a, b, mean = best_run(pair, rnd)
            if b > a and (pick is None or b - a > pick[2] - pick[1] or
                          (b - a == pick[2] - pick[1] and mean < pick[3])):
                pick = (pair, a, b, mean, rnd)
        if pick is not None and (pick[2] - pick[1]) * step >= 2.5 * pick[3]:     # room to glide two dots apart
            break
    if pick is None:
        return Plan({}, problems=[f"no two lights on one bar reach {wall}"])
    pair, a, b, mean, rnd = pick
    probs = []
    if rnd > p["round"]:
        probs.append(f"no stretch of {wall} where both spots stay round: the roundest is used")
    gap = p["gap"] or 1.3 * mean
    s0, s1 = a * step, b * step
    span = s1 - s0
    if span < gap * 1.5:
        probs.append(f"only {span:.1f} m of {wall} to glide along")
        gap = span / 2.0
    # start at the end nearer the pair, gliding away from it
    pa = (sum(lt.at[0] for lt in pair) / 2.0, sum(lt.at[1] for lt in pair) / 2.0)
    e0, e1 = st.point(s0)[0], st.point(s1)[0]
    if math.hypot(e1[0] - pa[0], e1[1] - pa[1]) < math.hypot(e0[0] - pa[0], e0[1] - pa[1]):
        s0, s1 = s1, s0
    sgn = 1.0 if s1 >= s0 else -1.0
    end_q = None
    for nm in (p["end"] if isinstance(p["end"], tuple) else (p["end"],)):
        q = space.resolve(room, nm)
        if q is not None:
            end_q = tuple(float(x) for x in q)
            break
    once = end_q is not None
    g, h = (0.5, 0.15) if once else (min(0.9, p["glide"]), min(0.5, p["hold"]))

    def on_wall(u: float, lead: bool) -> tuple:
        u = min(1.0, max(0.0, u))
        off = gap + u * (span - gap) if lead else u * (span - gap)
        return st.point(s0 + sgn * off)[0]
    tracks: dict[str, Track] = {}
    for idx, lt in enumerate(pair):
        lead = idx == 0
        keys = []
        for k in range(SAMPLES + (1 if once else 0)):
            w = k / SAMPLES
            if w < g:
                keys.append(Key(w, on_wall(w / g, lead), 1.0))
            elif w < g + h:
                keys.append(Key(w, on_wall(1.0, lead), 1.0))
            elif once:
                sw = 0.2
                f = min(1.0, (w - g - h) / sw)
                f = f * f * (3.0 - 2.0 * f)
                q0 = on_wall(1.0, lead)
                keys.append(Key(w, tuple(q0[i] + (end_q[i] - q0[i]) * f for i in range(3)), 1.0))
            elif k == int(math.ceil((g + h) * SAMPLES)):
                keys.append(Key(w, on_wall(1.0, lead), 0.0))   # out where it stopped
            else:
                keys.append(Key(w, on_wall(0.0, lead), 0.0))   # dark, back to the start
        tracks[lt.id] = Track(tuple(keys))
    park = end_q if once else st.point(st.length / 2.0)[0]
    for lt in lights:
        if lt.id not in tracks:
            tracks[lt.id] = Track((Key(0.0, park, 0.0),))
    bar = pair[0].bar or "two lights"
    return Plan(tracks, problems=probs, once=once,
                note=f"{bar}'s {pair[0].id.split('.')[-1]} and {pair[1].id.split('.')[-1]} along {wall}: "
                     f"{abs(s1 - s0):.1f} m, dots about {mean:.1f} m, {gap:.1f} m apart")
