"""ScoreBuilder: live piano notes -> bars of lightly quantised notation.

Times are seconds on the sound clock (sound_view's photon time / 1000, or any monotonic clock).  The pieces:

* **BeatClock**: beat(t) = b0 + (t - t0) / P.  It starts once five chord onsets span 1.5 s: P is the peak of the
  inter-onset intervals folded into 0.45-0.9 s (67-133 bpm, the readable octave), and the first onset is beat 0,
  a downbeat.  Every later chord onset within 0.22 beat of the half-beat grid re-anchors the clock there, pulling
  the phase 35 % of the way to the grid and the period 8 % of the error, so the bar lines follow rubato and drift.
* **Quantising**: onsets and releases snap to the 16th grid.  When the off-beat onsets sit late (their median lies
  at 0.58-0.75 of a beat: swing), anything there snaps to the half beat, so swung eighths read as straight eighths,
  as jazz is written.
* **Bars** (4/4): bar k is committed one beat after it ends (late releases still count).  The hands are split at
  middle C (treble from 60).  Each staff is one voice: the notes starting together make a chord lasting until
  the next onset in that staff (or its own end), gaps are rests, and every value is split into notatable pieces
  that don't cross the beat (or, from a beat, the half bar), tied together.  Notes sounding over the bar line are
  tied into the next bar.
* **Phrases**: rests after the last note wait (a later note makes them rest bars).  After SILENCE_S with nothing
  sounding, the last bars are committed, a marker bar (``marker``, no music) ends the phrase, and the clock
  restarts on the next note (a new system on screen).
* Key (for the signature and spelling): Krumhansl-Kessler profiles against the duration-weighted pitch classes of
  the last ~30 s.  Dynamics: the bar's mean strike velocity, marked when its level changes.  Pedal: damper
  crossings of 0.5, at their 16th.
"""
from __future__ import annotations

import collections
import math
from dataclasses import dataclass, field
from typing import Optional

BEATS = 4                          # 4/4
GRID = 4                           # 16ths per beat
BAR = BEATS * GRID
CHORD_S = 0.045                    # onsets this close are one chord (for the clock)
P_LO, P_HI = 0.45, 0.9             # the beat period's readable octave (s)
START_ONSETS, START_SPAN_S = 5, 1.5
BUFFER_BEATS = 1.0
SILENCE_S = 4.0
SPLIT = 60                         # treble from middle C
KEY_TAU_S = 30.0
DYNAMICS = ((0.30, "pp"), (0.42, "p"), (0.54, "mp"), (0.66, "mf"), (0.80, "f"), (2.0, "ff"))

# Krumhansl-Kessler key profiles (major, minor), tonic first
KK_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
KK_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


@dataclass
class Note:
    id: int
    pitch: int
    vel: float
    t_on: float
    t_off: Optional[float] = None


@dataclass
class Event:
    """One chord (or a rest: no pitches) in one staff of one bar; positions in 16ths from the bar's start."""
    start: int
    dur: int
    pitches: list = field(default_factory=list)
    ids: list = field(default_factory=list)
    tie_in: list = field(default_factory=list)     # per pitch: continues a note from before this event
    tie_out: list = field(default_factory=list)    # per pitch: continues after it


@dataclass
class Bar:
    index: int
    staves: tuple                                  # (treble events, bass events)
    t_start: float
    t_end: float
    dynamic: Optional[str] = None
    pedal: list = field(default_factory=list)      # [(16th, "down" | "up")]
    phrase_end: bool = False
    empty: bool = False
    marker: bool = False                           # no music: the phrase ended here (start a new system)


def _fold(p: float) -> float:
    while p < P_LO:
        p *= 2.0
    while p >= P_HI:
        p /= 2.0
    return p


def estimate_period(onsets: list[float]) -> float:
    """The beat period: the densest folded inter-onset interval (a Gaussian-kernel peak on a log scale)."""
    iois = [b - a for a, b in zip(onsets, onsets[1:]) if b - a > 0.08]
    if not iois:
        return 0.6
    folded = [math.log2(_fold(x)) for x in iois]
    lo, hi = math.log2(P_LO), math.log2(P_HI)
    best, best_s = folded[0], -1.0
    for i in range(61):
        c = lo + (hi - lo) * i / 60
        s = sum(math.exp(-((f - c) / 0.04) ** 2) for f in folded)
        if s > best_s:
            best, best_s = c, s
    return 2.0 ** best


class BeatClock:
    def __init__(self) -> None:
        self.P: Optional[float] = None
        self.t0 = 0.0
        self.b0 = 0.0
        self.onsets: collections.deque = collections.deque(maxlen=32)

    @property
    def running(self) -> bool:
        return self.P is not None

    def beat(self, t: float) -> float:
        return self.b0 + (t - self.t0) / self.P

    def time(self, b: float) -> float:
        return self.t0 + (b - self.b0) * self.P

    def feed(self, t: float) -> None:
        """A chord onset."""
        self.onsets.append(t)
        if self.P is None:
            on = list(self.onsets)
            if len(on) >= START_ONSETS and on[-1] - on[0] >= START_SPAN_S:
                self.P, self.t0, self.b0 = estimate_period(on), on[0], 0.0
            return
        b = self.beat(t)
        g = round(b * 2.0) / 2.0
        e = b - g
        if abs(e) < 0.22:
            self.b0, self.t0 = b - 0.35 * e, t
            self.P = min(P_HI * 1.15, max(P_LO * 0.85, self.P * (1.0 + 0.08 * e)))


def split_value(pos: int, dur: int) -> list[int]:
    """Notatable pieces (16ths) for a value starting ``pos`` 16ths into a 4/4 bar: pieces never cross a beat,
    except whole-beat values that start on a beat and stay inside their half bar (a whole note fills the bar)."""
    out = []
    while dur > 0:
        if pos % BAR == 0 and dur >= BAR:
            d = BAR
        elif pos % GRID == 0 and dur >= GRID:
            half_end = (pos // 8 + 1) * 8
            room = min(dur, half_end - pos) if pos % 8 else min(dur, 8 + (8 if pos % 16 == 0 and dur >= 12 else 0))
            d = 12 if room >= 12 and pos % 16 == 0 else 8 if room >= 8 else 4
        else:
            room = min(dur, GRID - pos % GRID)
            d = 3 if room >= 3 and pos % GRID == 0 else 2 if room >= 2 and pos % 2 == 0 else 1
        out.append(d)
        pos += d
        dur -= d
    return out


class ScoreBuilder:
    """Feed note_on / note_off / pedal (sound-clock seconds); poll(now) returns the bars committed since."""

    def __init__(self) -> None:
        self.clock = BeatClock()
        self.notes: list[Note] = []
        self.next_id = 1
        self.next_bar = 0
        self.bar_number = 1                     # printed bar numbers keep counting across phrases
        self.last_chord_t: Optional[float] = None
        self.last_event_t: Optional[float] = None
        self.pedal_events: list[tuple[float, str]] = []
        self.damper_down = False
        self.swing = False
        self.offbeats: collections.deque = collections.deque(maxlen=48)
        self.pc_weight = [0.0] * 12
        self.pc_t: Optional[float] = None
        self.last_dynamic: Optional[str] = None
        self.phrase_bar0 = 0                    # the clock bar where the current phrase starts

    # -- input ---------------------------------------------------------------------------------------------------
    def note_on(self, pitch: int, vel: float, t: float) -> int:
        if self.last_chord_t is None or t - self.last_chord_t > CHORD_S:
            self.clock.feed(t)
            self.last_chord_t = t
            if self.clock.running:
                f = self.clock.beat(t) % 1.0
                if 0.2 < f < 0.85:
                    self.offbeats.append(f)
                    if len(self.offbeats) >= 12:
                        m = sorted(self.offbeats)[len(self.offbeats) // 2]
                        self.swing = 0.58 <= m <= 0.75
        n = Note(self.next_id, int(pitch), float(vel), t)
        self.next_id += 1
        self.notes.append(n)
        self.last_event_t = t
        return n.id

    def note_off(self, pitch: int, t: float) -> None:
        for n in reversed(self.notes):
            if n.pitch == pitch and n.t_off is None:
                n.t_off = max(t, n.t_on + 0.02)
                self._key_weight(n)
                break
        self.last_event_t = t

    def pedal(self, value: float, t: float) -> None:
        down = value >= 0.5
        if down != self.damper_down:
            self.damper_down = down
            self.pedal_events.append((t, "down" if down else "up"))

    # -- key -----------------------------------------------------------------------------------------------------
    def _key_weight(self, n: Note) -> None:
        if self.pc_t is not None:
            k = math.exp(-max(0.0, n.t_off - self.pc_t) / KEY_TAU_S)
            self.pc_weight = [w * k for w in self.pc_weight]
        self.pc_t = n.t_off
        self.pc_weight[n.pitch % 12] += min(4.0, n.t_off - n.t_on) * (0.5 + n.vel)

    def key(self) -> tuple[int, bool, int]:
        """(tonic pitch class, minor, signature fifths -7..7)."""
        w = self.pc_weight
        if sum(w) <= 0:
            return 0, False, 0
        best = (-2.0, 0, False)
        for tonic in range(12):
            for minor, prof in ((False, KK_MAJOR), (True, KK_MINOR)):
                x = [w[(tonic + i) % 12] for i in range(12)]
                r = _corr(x, prof)
                if r > best[0]:
                    best = (r, tonic, minor)
        _, tonic, minor = best
        major_tonic = (tonic + 3) % 12 if minor else tonic
        fifths = (major_tonic * 7) % 12
        if fifths > 6:
            fifths -= 12                      # prefer flats from F# / Gb (6 -> keep 6 sharps), Db = -5 ...
        return tonic, minor, fifths

    # -- output --------------------------------------------------------------------------------------------------
    def poll(self, now: float) -> list[Bar]:
        out: list[Bar] = []
        if not self.clock.running:
            return out
        silent = (self.last_event_t is not None and now - self.last_event_t > SILENCE_S
                  and all(n.t_off is not None for n in self.notes))
        upto = self.clock.beat(now) - BUFFER_BEATS
        while (self.next_bar + 1) * BEATS <= upto:
            lo_t = self.clock.time(self.next_bar * BEATS)
            if not any(n.t_off is None or n.t_off > lo_t for n in self.notes):
                break           # trailing rests wait: a later note makes them real, the phrase's end drops them
            out.append(self._build(self.next_bar, now))
            self.next_bar += 1
        if silent:
            last = max((self._q(self.clock.beat(n.t_off)) for n in self.notes if n.t_off is not None), default=0)
            while self.next_bar * BAR < last:
                out.append(self._build(self.next_bar, now))
                self.next_bar += 1
            out.append(Bar(index=0, staves=([], []), t_start=now, t_end=now, phrase_end=True, empty=True,
                           marker=True))
            self._restart()
        return out

    def _restart(self) -> None:
        """A new phrase: keep the period as a prior, start the clock on the next onsets."""
        self.clock = BeatClock()
        self.notes = []
        self.pedal_events = []
        self.next_bar = 0
        self.last_chord_t = None
        self.offbeats.clear()
        self.swing = False

    def _q(self, b: float) -> int:
        """A beat position -> 16ths, with swing read as straight eighths."""
        if self.swing:
            whole = math.floor(b)
            f = b - whole
            if 0.55 <= f <= 0.8:
                return int(whole * GRID + GRID // 2)
        return int(round(b * GRID))

    def _build(self, k: int, now: float) -> Bar:
        lo, hi = k * BAR, (k + 1) * BAR
        segs: tuple[list, list] = ([], [])
        vels = []
        for n in self.notes:
            s = self._q(self.clock.beat(n.t_on))
            e = self._q(self.clock.beat(n.t_off if n.t_off is not None else now))
            e = max(e, s + 1)
            if e <= lo or s >= hi:
                continue
            staff = 0 if n.pitch >= SPLIT else 1
            segs[staff].append((max(s, lo) - lo, min(e, hi) - lo, n.pitch, n.id, s < lo, e > hi))
            if lo <= s < hi:
                vels.append(n.vel)
        staves = tuple(self._voice(sorted(sg)) for sg in segs)
        bar = Bar(index=self.bar_number, staves=staves, t_start=self.clock.time(lo / GRID),
                  t_end=self.clock.time(hi / GRID), empty=not any(segs))
        self.bar_number += 1
        if vels:
            m = sum(vels) / len(vels)
            dyn = next(name for edge, name in DYNAMICS if m < edge)
            if dyn != self.last_dynamic:
                bar.dynamic = dyn
                self.last_dynamic = dyn
        for t, what in self.pedal_events:
            p = self._q(self.clock.beat(t)) - lo
            if 0 <= p < BAR:
                bar.pedal.append((p, what))
        # forget what can't reach later bars
        self.notes = [n for n in self.notes if n.t_off is None or self._q(self.clock.beat(n.t_off)) > hi]
        self.pedal_events = [(t, w) for t, w in self.pedal_events if self._q(self.clock.beat(t)) >= hi]
        return bar

    @staticmethod
    def _voice(segs: list) -> list[Event]:
        """One staff's notes in a bar -> chords and rests covering the bar, split into notatable tied pieces."""
        starts = sorted({s for s, *_ in segs})
        events: list[Event] = []
        pos = 0
        for i, s in enumerate(starts):
            group = [g for g in segs if g[0] == s]
            nxt = starts[i + 1] if i + 1 < len(starts) else BAR
            end = max(1, min(nxt, max(g[1] for g in group)) - s) + s
            if s > pos:
                events += [Event(p, d) for p, d in _pieces(pos, s - pos)]
            pitches = [g[2] for g in group]
            ids = [g[3] for g in group]
            tie_in = [g[4] for g in group]
            tie_out = [g[5] and g[1] >= BAR and end >= BAR for g in group]
            pieces = _pieces(s, end - s)
            for j, (p, d) in enumerate(pieces):
                last = j == len(pieces) - 1
                events.append(Event(p, d, pitches, ids, tie_in if j == 0 else [True] * len(pitches),
                                    tie_out if last else [True] * len(pitches)))
            pos = end
        if pos < BAR:
            events += [Event(p, d) for p, d in _pieces(pos, BAR - pos)]
        return events


def _pieces(pos: int, dur: int) -> list[tuple[int, int]]:
    out = []
    for d in split_value(pos, dur):
        out.append((pos, d))
        pos += d
    return out


def _corr(x: list[float], y: tuple) -> float:
    mx, my = sum(x) / 12, sum(y) / 12
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    den = math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
    return num / den if den > 0 else 0.0
