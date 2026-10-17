"""The beat clock as effects see it (owner, 17 Oct: an effect's natural pacing follows the bpm).

:class:`BeatClock` is show_service's music clock at the frame's photon time (else the lighting desk's locked
follower), handed to an effect each frame as ``Effect.beat_clock`` (None while no clock is sure).  Two uses:

* **Tempo lock** (effects.EffectSlot, every effect): a param whose schema says ``x-tempo: k`` (``k`` = its value
  for one cycle per second: 1 for Hz / turns/s, 360 for deg/s, 60 for per-minute; ``"period"`` for seconds per
  cycle) runs at one cycle per musical division while the clock is sure: 1/4, 1/2, 1, 2 beats, then 1, 2, 4 ... 32
  bars, the one nearest the param's own (mapped) pace, with hysteresis, so the music moves a pace a whole step
  (half / double time) or not at all.  A pace no division fits (too slow, too fast, or off the param's range)
  keeps its value.  ``locks`` (param -> beats per cycle) says what is locked this frame.
* **Phase lock** (opt-in, :func:`advance`): an effect that integrates its own phase (in cycles) is pulled onto the
  grid, so its steps and peaks land on the beat; a cycle of whole bars starts on a bar line.  The pull is
  slew-limited (never backwards, at most double speed), so nothing jumps when the clock comes, goes or re-anchors.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

BEAT_DIVISIONS = (0.25, 0.5, 1.0, 2.0)     # 1/16, 1/8, 1/4 and 1/2 notes in 4/4
BAR_MULTIPLES = (1, 2, 4, 8, 16, 32)       # whole bars
HYSTERESIS = 0.25                          # octaves past the half-way point before the division changes
REACH = 0.6                                # octaves beyond the end divisions still snapped
ON_DIVISION = 0.03                         # octaves: a rate this close to a division is on it
PULL_TAU_S = 0.5                           # phase lock: the error's time constant
MAX_BEHIND, MAX_AHEAD = 0.8, 1.0           # phase lock: the pull's limits, as shares of the rate (never backwards)


@dataclass(frozen=True)
class BeatClock:
    beats: float                 # the beat number at the frame's photon time (continuous)
    bpm: float
    beats_per_bar: int = 4
    bar_beat: float = 0.0        # the beat number of a downbeat
    source: str = "show"         # "show": show_service's clock | "desk": the lighting desk's follower (no bars)
    locks: dict = field(default_factory=dict)    # param -> beats per cycle, tempo-locked this frame (EffectSlot)

    @property
    def bps(self) -> float:
        return self.bpm / 60.0

    @property
    def bar_phase(self) -> float:
        return ((self.beats - self.bar_beat) / self.beats_per_bar) % 1.0

    def divisions(self) -> tuple:
        bpb = max(1, int(self.beats_per_bar))
        return tuple(sorted(set(BEAT_DIVISIONS) | {float(bpb * m) for m in BAR_MULTIPLES}))

    def snap(self, cps: float, current: Optional[float] = None) -> Optional[float]:
        """Beats per cycle for a pace of ``cps`` cycles/s: the nearest division (log scale), keeping ``current``
        until the pace is HYSTERESIS octaves past the half-way point; None when no division is within REACH."""
        if not (cps > 0.0 and math.isfinite(cps)) or self.bpm <= 0.0:
            return None
        x = math.log2(self.bps / cps)
        divs = self.divisions()
        if x < math.log2(divs[0]) - REACH or x > math.log2(divs[-1]) + REACH:
            return None
        if current is not None and current in divs and abs(x - math.log2(current)) < 0.5 + HYSTERESIS:
            return current
        return min(divs, key=lambda d: abs(x - math.log2(d)))

    def division_of(self, cps: float) -> Optional[float]:
        """Beats per cycle when ``cps`` is one cycle per a division (within ON_DIVISION octaves), else None."""
        if not (cps > 0.0 and math.isfinite(cps)) or self.bpm <= 0.0:
            return None
        x = math.log2(self.bps / cps)
        for d in self.divisions():
            if abs(x - math.log2(d)) < ON_DIVISION:
                return d
        return None


def tempo_unit(prop: dict) -> Any:
    """A param's ``x-tempo`` (a positive number or "period"), or None when it has none."""
    k = prop.get("x-tempo")
    if k == "period":
        return k
    if isinstance(k, bool) or not isinstance(k, (int, float)) or not k > 0:
        return None
    return float(k)


def cycles_per_s(value: float, unit: Any) -> Optional[float]:
    """|value| as cycles per second (None for a stopped pace or a zero period)."""
    v = abs(float(value))
    if unit == "period":
        return 1.0 / v if v > 0.0 else None
    return v / unit if v > 0.0 else None


def value_for(cps: float, unit: Any, sign: float = 1.0) -> float:
    """cycles per second -> the param's value (the sign of a rate kept)."""
    if unit == "period":
        return 1.0 / cps
    return math.copysign(cps * unit, sign)


def advance(phase: float, rate: float, dt: float, clock: Optional[BeatClock], param: str) -> float:
    """``phase`` (cycles, unbounded) advanced by ``rate`` (cycles/s, kicks included) over ``dt``, and pulled
    toward the grid while ``clock`` is sure and the pace param ``param`` (``x-tempo``) is tempo-locked
    (``clock.locks``: n beats per cycle): phase 0 of each cycle on a beat (on a bar line when n is whole bars).
    Slew-limited: the pull never runs the phase backwards nor faster than double ``rate``.  Read the clock with
    ``getattr(self, "beat_clock", None)``."""
    step = rate * dt
    n = clock.locks.get(param) if clock is not None else None
    if clock is None or n is None or n <= 0.0 or dt <= 0.0 or rate == 0.0:
        return phase + step
    target = (clock.beats - clock.bar_beat) / n
    if rate < 0.0:
        target = -target
    err = (target - (phase + step) + 0.5) % 1.0 - 0.5     # the frame shows the stepped phase at the clock's time
    pull = err * (1.0 - math.exp(-dt / PULL_TAU_S))
    lim = abs(step)
    s = 1.0 if rate > 0.0 else -1.0
    pull = s * max(-MAX_BEHIND * lim, min(MAX_AHEAD * lim, s * pull))
    return phase + step + pull


def paced(prop: dict, k: Any = 1.0) -> dict:
    """``prop`` marked as a pace (``x-tempo``): ``k`` is its value for one cycle per second, or "period"."""
    return {**prop, "x-tempo": k}
