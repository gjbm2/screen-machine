"""phrasing: how the lights wind up to a lift in the song and land on it (owner, 27 Nov 2026).

"in a build / release moment (e.g. start of chorus, climax, etc.) lights don't seem to follow the pacing / mood /
tension build & release." They could not: the only shaping before a line was keyed to a dance-music riser detector
(it armed a line in 3 of 21 karaoke songs), and what a chorus got instead was a move drawn by chance, a one or two
beat gesture or nothing (27 of 59 arrivals). Across the boundaries where the music lifted, the lights' size rose by
0.017 on average: chance.

A lift is known ahead (the song's brief sizes every section; the art director hears of each about eight seconds
before its line), so it is phrased, the same way every time, sized by how big the lift is:

  wind     over ``wind_s`` before the line the running looks pull in: levels fall by ``wind``, the heads' shapes
           shrink (by 0.6 of it), on a smoothstep, so the room leans in without anything "happening"
  land     on the line everything comes in above its steady level (by ``lift``, where there is headroom) with the
           shapes opened wide (by half of it), and settles over ``settle_s``
  kinds    bump: as above.  snap: the same wind, the last beat before the line dark, then the landing at full.
           swell: no pulling in (a slow song doesn't flinch): the landing only, soft and long.
           stop: the band cuts out and comes back on a hit (a song's moment, utils/song_moments.py): no wind, the
           lights down to ``floor`` for the gap (``dark_ms`` before the line), then the landing.
           out: the song's last light (its ending): down to ``floor`` by the line, over ``fade_ms`` (on ``curve``:
           x ** curve of the way down at x of the fade), and staying there.
           in: back from a song's end: from ``floor`` at the line to the look over ``fade_ms``.
  junction the song junction (director._junction_envs; README S5.17) is out and in: the lamps and the rig read the
           same envelopes, timed as the screens' fade-dark transition.
  moments  a song's stabs and stops (LookData.moments): each its own envelope (a stab is a bump with no wind and a
           short settle), multiplied with the section's (``envelopes``).

``envelope`` is that as two factors for one moment, (level, size), 1.0 = the look as it is. Pure: the look engine
multiplies them into every group's level and every moving head's pattern size (looks.py), so it works under any
scene and any transition. A gesture (heads fly up, a strobe) is still a move's to add, rarely; this is not one.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

SIZE_WIND = 0.6                 # the shapes shrink by this share of the wind
SIZE_LIFT = 0.5                 # ... and open by this share of the lift on the line
SNAP_DARK = 0.04                # a snap's last beat: this level
SWELL_SETTLE = 2.0              # a swell's landing settles over this many times settle_s


#: The envelopes' kinds. OUT is the song's last light (an ending, or the junction to the next song).
OUT = "out"
KINDS = ("bump", "snap", "swell", "stop", OUT, "in")

def _smooth(x: float) -> float:
    x = 0.0 if x < 0.0 else 1.0 if x > 1.0 else x
    return x * x * (3.0 - 2.0 * x)


def envelope(t_s: float, arrival: Optional[Mapping[str, Any]]) -> tuple[float, float]:
    """(level factor, size factor) at ``t_s`` for a look's ``arrival`` ({at_ms, kind, wind, lift, wind_ms,
    settle_ms, beat_ms}; None or a kind it doesn't know: (1, 1))."""
    if not isinstance(arrival, Mapping):
        return 1.0, 1.0
    kind = arrival.get("kind")
    if kind not in KINDS:
        return 1.0, 1.0
    if kind == "in":                                             # back from a song's end: floor -> 1 over fade_ms
        try:
            t0 = float(arrival["at_ms"]) / 1000.0
            fade_s = max(0.0, float(arrival.get("fade_ms") or 0.0) / 1000.0)
            floor = max(0.0, min(1.0, float(arrival.get("floor") or 0.0)))
        except (KeyError, TypeError, ValueError):
            return 1.0, 1.0
        if t_s < t0:
            return floor, 1.0
        if fade_s <= 0.0 or t_s >= t0 + fade_s:
            return 1.0, 1.0
        return floor + (1.0 - floor) * (t_s - t0) / fade_s, 1.0
    if kind == "out":                                            # the song's last light: down by at_ms, and stays
        try:
            line = float(arrival["at_ms"]) / 1000.0
            fade_s = max(0.0, float(arrival.get("fade_ms") or 0.0) / 1000.0)
            floor = max(0.0, min(1.0, float(arrival.get("floor") or 0.0)))
            curve = max(0.2, float(arrival.get("curve") or 1.0))
        except (KeyError, TypeError, ValueError):
            return 1.0, 1.0
        if t_s >= line:
            return floor, 1.0
        if fade_s <= 0.0 or t_s < line - fade_s:
            return 1.0, 1.0
        x = (t_s - (line - fade_s)) / fade_s                     # curve > 1: held up longer, then down, as a
        return 1.0 - (1.0 - floor) * x ** curve, 1.0             # record's fade is heard to the end
    try:
        line = float(arrival["at_ms"]) / 1000.0
        wind, lift = float(arrival.get("wind") or 0.0), float(arrival.get("lift") or 0.0)
        wind_s = max(0.0, float(arrival.get("wind_ms") or 0.0) / 1000.0)
        settle_s = max(0.0, float(arrival.get("settle_ms") or 0.0) / 1000.0)
        beat_s = max(0.0, float(arrival.get("beat_ms") or 0.0) / 1000.0)
    except (KeyError, TypeError, ValueError):
        return 1.0, 1.0
    if kind == "swell":
        settle_s *= SWELL_SETTLE
    if kind == "stop" and t_s < line:
        try:
            dark_s = max(0.0, float(arrival.get("dark_ms") or 0.0) / 1000.0)
            floor = float(arrival.get("floor") if arrival.get("floor") is not None else SNAP_DARK)
        except (TypeError, ValueError):
            return 1.0, 1.0
        return (max(0.0, min(1.0, floor)), 1.0) if t_s >= line - dark_s else (1.0, 1.0)
    if t_s < line:
        if kind == "swell" or wind_s <= 0.0 or t_s < line - wind_s:
            return 1.0, 1.0
        x = _smooth((t_s - (line - wind_s)) / wind_s)
        level, size = 1.0 - wind * x, 1.0 - SIZE_WIND * wind * x
        if kind == "snap" and beat_s > 0.0 and t_s >= line - beat_s:
            level = min(level, SNAP_DARK)
        return max(0.0, level), max(0.0, size)
    if settle_s <= 0.0 or t_s >= line + settle_s:
        return 1.0, 1.0
    y = 1.0 - (t_s - line) / settle_s                            # 1 on the line, 0 once settled
    y = _smooth(y) if kind == "swell" else y * y
    return 1.0 + lift * y, 1.0 + SIZE_LIFT * lift * y


def envelopes(t_s: float, arrival: Optional[Mapping[str, Any]], moments: Any = None) -> tuple[float, float]:
    """The section's arrival and every moment's, multiplied: (level factor, size factor) at ``t_s``."""
    level, size = envelope(t_s, arrival)
    if isinstance(moments, (list, tuple)):
        for m in moments:
            lv, sz = envelope(t_s, m)
            level, size = level * lv, size * sz
    return level, size
