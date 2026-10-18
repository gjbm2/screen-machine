"""ScoreBuilder: live piano notes -> bars of lightly quantised notation.

Times are seconds on the sound clock (sound_view's photon time / 1000, or any monotonic clock).

**With the analysis worker** (analyse.py: PM2S + metre.py, in ~/.venvs/sm-score-ml), which is how it normally runs:
``analysis_request(now)`` hands out the phrase's last WINDOW_S of notes every ANALYSE_S, and ``on_analysis(reply)``
takes back the beats, the metre (beats per bar, where the bar lines fall, simple or compound), the key signature and
each note's hand.  Each reply's metre adds to the phrase's evidence (log-probabilities per bar length and the
compound logit, older replies decaying by EV_DECAY: later windows hear more).  The first bar of a phrase waits for
it (MIN_DECIDE_S of playing and DECIDE_P confident, or MAX_DECIDE_S).  Should the evidence later turn (REWRITE_P
for a different duple/triple or simple/compound, REWRITE_RUN replies running, within REWRITE_S of the phrase's
start), the phrase is written again in the new metre: a ``rewrite`` marker bar, then its bars from the start.  Beats come from a BeatGrid: the analysed beat
times, those under written bars frozen, later ones replaced by each new analysis, brought onto the written beat's
level and phase first (a reply that counts a stretch at twice or half the tempo, or reads the beat half a beat
off, once turned the rest of a phrase to double time; BeatGrid.merge, _same_level).  After a break of 3+ beats the
playing resumes on a beat.  A phrase with nothing metric in it (even onsets, no accents: a test pattern's scale) is
written in 4/4 on an even beat from its onsets.  A bar is written once its end is TRUST_S before the newest
analysed note (the networks look both ways: the newest beats are the weakest).  Notes go to the treble or bass
staff by the hand PM2S gives them, within 3 ledger lines of it; a single line (nothing else sounding) goes by
register instead, keeping its staff within 2 ledger lines (the hand model calls a one-hand scale all left hand).
Tests: tests/test_score.py and tests/test_score_roundtrip.py (scales in, a musically right score out).

**Without it** (no venv, the worker died, or before its first reply when it never comes) the fallback pieces below
write 4/4 with a beat clock of their own:

* **BeatClock**: beat(t) = b0 + (t - t0) / P.  It starts once five chord onsets span 1.5 s: P is the peak of the
  inter-onset intervals folded into 0.45-0.9 s (67-133 bpm, the readable octave), and the first onset is beat 0,
  a downbeat.  Every later chord onset within 0.22 beat of the half-beat grid re-anchors the clock there, pulling
  the phase 35 % of the way to the grid and the period 8 % of the error, so the bar lines follow rubato and drift.
* **Quantising**: onsets and releases snap to the 16th grid.  When the off-beat onsets sit late (their median lies
  at 0.58-0.75 of a beat: swing), anything there snaps to the half beat, so swung eighths read as straight eighths,
  as jazz is written.
* **Bars** (4/4): bar k is committed one beat after it ends (late releases still count).  The hands are split at
  middle C (treble from 60).  Each staff is one voice: the notes starting together make a chord lasting until
  the next onset in that staff (or its own end; a gap under half a beat, or before a next onset within the beat,
  is joined to the note: staccato isn't written as 16ths and rests), longer gaps are rests, and every value is split into notatable pieces
  that don't cross the beat (or, from a beat, the half bar), tied together.  Notes sounding over the bar line are
  tied into the next bar.
* **Phrases**: rests after the last note wait (a later note makes them rest bars).  After SILENCE_S with nothing
  sounding, the last bars are committed, a marker bar (``marker``, no music) ends the phrase, and the clock
  restarts on the next note (a new system on screen).
* Key (for the signature and spelling): the analysis's key signature (PM2S's per-note key, duration-weighted over
  the window; a change needs two replies in a row), else Krumhansl-Kessler profiles against the duration-weighted
  pitch classes of the last ~30 s.  Dynamics: the bar's mean strike velocity, marked when its level changes.
  Pedal: damper crossings of 0.5, at their 16th.

Positions are 16ths throughout: a simple beat is 4 of them, a compound (dotted) beat 6, so a bar is beats x unit.
"""
from __future__ import annotations

import bisect
import collections
import math
from dataclasses import dataclass, field
from typing import Optional

BEATS = 4                          # the fallback's 4/4
GRID = 4                           # 16ths per simple beat
BAR = BEATS * GRID
COMPOUND_UNIT = 6                  # 16ths per dotted beat
CHORD_S = 0.045                    # onsets this close are one chord (for the clock)
P_LO, P_HI = 0.45, 0.9             # the beat period's readable octave (s)
START_ONSETS, START_SPAN_S = 5, 1.5
BUFFER_BEATS = 1.0
SILENCE_S = 4.0
SPLIT = 60                         # treble from middle C
TREBLE_LOW, BASS_HIGH = 53, 67     # a hand's notes past these (3 ledger lines: F3, G4) go to the other staff
MONO_OVERLAP_S = 0.1               # notes sounding together less than this (or a third of the shorter) are legato
LINE_TREBLE_LOW, LINE_BASS_HIGH = 57, 64   # a single line changes staff only past 2 ledger lines (A3, E4)
KEY_TAU_S = 30.0
DYNAMICS = ((0.30, "pp"), (0.42, "p"), (0.54, "mp"), (0.66, "mf"), (0.80, "f"), (2.0, "ff"))
# the analysis
ANALYSE_S = 1.5                    # a request at most this often while notes come
WINDOW_S = 24.0                    # the notes sent (the networks were checked on 24 s windows) ...
MAX_NOTES = 600                    # ... at most the newest this many (dense playing: a shorter window, a quick reply)
MIN_NOTES = 12
MIN_DECIDE_S = 6.0                 # the metre is taken once the phrase has this much playing and ...
DECIDE_P = 0.7                     # ... the evidence is this confident, or
MAX_DECIDE_S = 14.0                # ... anyway after this much
EV_DECAY = 0.75                    # each reply's weight in the evidence, against the next one's
REWRITE_P = 0.85                   # the evidence for another metre that rewrites the phrase ...
REWRITE_RUN = 2                    # ... in this many replies running ...
REWRITE_S = 45.0                   # ... while the phrase is younger than this
HISTORY_S = 60.0                   # the phrase's notes kept (the analysis window, and a rewrite)
TRUST_S = 1.2                      # a bar is written once it ends this long before the newest analysed note
FAST_BEAT_S = 0.28                 # an analysed beat faster than this (215 bpm: a beat on every note of a fast
                                   # run) is written at half the rate; jazz at 180 keeps its quarters
PAUSE_BEATS = 3.0                  # a silence this long inside a phrase (a break): playing resumes on a beat
PHASE_BEATS = 4                    # a reply's phase against ours: over our last this many written beats
LOCAL_BEATS = 8                    # the tempo a merge goes by: our last this many beat intervals
EVEN_IOI, EVEN_VEL = 0.08, 0.05     # metreless: onsets within 8 % of even, velocities within 0.05 of one swell
BREAK_IOI = 2.5                    # metreless: a gap this many times the usual onset interval ends a run
KEY_HOLD = 2                       # replies in a row a new key signature needs

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
    restruck: bool = False                         # its end is the key's next strike (its own release not seen yet)


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
    rewrite: bool = False                          # (a marker) the phrase is written again from its start: drop it
    beats: int = BEATS                             # the metre: beats per bar ...
    unit: int = GRID                               # ... and 16ths per beat (4 simple, 6 compound)
    pos: int = 0                                   # the bar's number in its phrase from 0 (a rewrite restarts it)

    @property
    def units(self) -> int:
        return self.beats * self.unit

    @property
    def meter(self) -> tuple[int, int]:
        """(count, unit) for the time signature: 3/4, 6/8 ..."""
        return (self.beats * 3, 8) if self.unit == COMPOUND_UNIT else (self.beats, 4)


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


class BeatGrid:
    """Beats from the analysis: ``t`` the beat times, beat number j - idx0 at t[j] (beat 0 is a downbeat); linear
    between beats, the end intervals carried on beyond them.  Same interface as BeatClock."""

    running = True

    def __init__(self, times, idx0: int) -> None:
        self.t = [float(x) for x in times]
        self.idx0 = int(idx0)

    def beat(self, t: float) -> float:
        T = self.t
        if t <= T[0]:
            return (t - T[0]) / (T[1] - T[0]) - self.idx0
        if t >= T[-1]:
            return len(T) - 1 + (t - T[-1]) / (T[-1] - T[-2]) - self.idx0
        j = bisect.bisect_right(T, t) - 1
        return j + (t - T[j]) / (T[j + 1] - T[j]) - self.idx0

    def time(self, b: float) -> float:
        T = self.t
        j = b + self.idx0
        if j <= 0:
            return T[0] + j * (T[1] - T[0])
        if j >= len(T) - 1:
            return T[-1] + (j - len(T) + 1) * (T[-1] - T[-2])
        i = int(math.floor(j))
        return T[i] + (j - i) * (T[i + 1] - T[i])

    def merge(self, times, keep_beat: Optional[int] = None) -> None:
        """Newer beats: keep ours up to beat number ``keep_beat`` (under written bars; None: up to where theirs
        start), then theirs from after it, brought onto our beat level and phase: a reply may count the beat at
        twice or half our tempo, or put it half a beat off ours (a scale's eighths read either way); spliced as
        they come, either turned the rest of the phrase to double time."""
        if keep_beat is None:
            ibi0 = self.t[-1] - self.t[-2]
            keep_n = bisect.bisect_left(self.t, float(times[0]) - 0.5 * ibi0) if len(times) else len(self.t)
        else:
            keep_n = keep_beat + self.idx0 + 1
            while len(self.t) < keep_n:        # bars written past our last beat sit on its extrapolation (time()):
                self.t.append(2 * self.t[-1] - self.t[-2])     # those beats are theirs now, or a gap opened
        keep = self.t[:max(2, min(len(self.t), keep_n))]   # these stand as they are: bars may be written on them
        ref = _median_ibi(keep[-(LOCAL_BEATS + 1):])      # the tempo now, not the phrase's
        theirs = sorted(float(x) for x in times)
        new = [x for x in theirs if x > keep[-1]]
        # their phase against ours where both have beats (our last few written ones and their nearest, on the same
        # playing; a circular mean): agreed on and over a quarter beat off, it's a different reading of where the
        # beat falls, not rubato, and the written bars set it, so theirs move
        ph = []
        for b in keep[-PHASE_BEATS:]:
            j = bisect.bisect_left(theirs, b)
            near = min(theirs[max(0, j - 1):j + 1], key=lambda x: abs(x - b), default=None)
            if near is not None and abs(near - b) < 0.6 * ref:
                ph.append(2.0 * math.pi * (near - b) / ref)
        if len(ph) >= 2 and new:
            c, s = sum(math.cos(a) for a in ph), sum(math.sin(a) for a in ph)
            off = math.atan2(s, c) / (2.0 * math.pi)
            if math.hypot(c, s) / len(ph) > 0.5 and abs(off) > 0.25:
                new = [x - off * ref for x in new if x - off * ref > keep[-1]]
        # on our level from our last kept beat: extra beats dropped, missed ones (and rests) filled, out of step
        # ones replaced by ours carried on
        self.t = keep + _same_level([keep[-1]] + new, ref)[1:]

    def extend_before(self, t: float) -> None:
        """Carry the beats back until the first is at or before ``t`` (bar 0 keeps its numbering)."""
        while self.t[0] > t:
            ibi = self.t[1] - self.t[0]
            self.t.insert(0, self.t[0] - ibi)
            self.idx0 += 1


def _median_ibi(t: list) -> float:
    d = sorted(b - a for a, b in zip(t, t[1:]) if b > a)
    return d[len(d) // 2] if d else 0.5


def _same_level(times: list, ref: float) -> list:
    """Beats at one level, ``ref`` apart: the analysis may count a stretch (or all) of a window at twice or half
    the tempo, or in threes against our twos (an even run of eighths reads any way), and a level that changes
    inside the phrase wrote eighths as quarters from there on.  Only a sustained change is undone: two intervals
    in a row of about half a beat lose the beat between them; one of about k beats (k >= 2) gets the missing ones
    evenly; two in a row of about 1.5 have their span divided into ours.  Anything else is rubato and stands
    (real timing displaces single beats, and a pushed beat stretches one by a third); a beat on top of another
    (under 0.38) is dropped."""
    out = [times[0]] if times else []
    i = 1
    while i < len(times):
        x = times[i]
        r = (x - out[-1]) / ref
        r2 = (times[i + 1] - x) / ref if i + 1 < len(times) else None
        if r < 0.38:                           # a beat on top of the last one
            i += 1
            continue
        if r < 0.62 and r2 is not None and 0.38 < r2 < 0.62:
            out.append(times[i + 1])           # double time: the beat between two half-beats goes
            i += 2
            continue
        if 1.35 < r < 1.65 and r2 is not None and 1.35 < r2 < 1.65:
            x = times[i + 1]                   # threes against our twos: their two beats are three of ours
            i += 1
            r = (x - out[-1]) / ref
        k = int(round(r))
        if r2 is None and (k == 0 or abs(r - k) >= 0.3):
            break                              # a lone odd interval at the newest edge: nothing yet says which
        if k >= 2 and abs(r - k) < 0.3:        # missed beats (or a rest): put back evenly
            out += [out[-1] + (x - out[-1]) * j / k for j in range(1, k)]
        out.append(x)
        i += 1
    return out


def _steady_start(t: list, ref: float) -> list:
    """A window's first beats are the networks' weakest (they look both ways): before the first 4 intervals in a
    row within 15 % of ``ref``, they're dropped (BeatGrid.extend_before carries the steady beat back instead)."""
    for i in range(max(0, len(t) - 4)):
        if all(abs((t[j + 1] - t[j]) / ref - 1.0) < 0.15 for j in range(i, i + 4)):
            return t[i:]
    return t


def split_value(pos: int, dur: int, beats: int = BEATS, unit: int = GRID) -> list[int]:
    """Notatable pieces (16ths) for a value starting ``pos`` 16ths into a bar of ``beats`` x ``unit``: pieces never
    cross a beat, except whole-beat values from a beat (a whole bar from its start; in 4-beat bars nothing crosses
    the middle except from the start).  Compound beats split into eighths, quarters and dotted eighths."""
    bar = beats * unit
    out = []
    while dur > 0:
        b, f = divmod(pos % bar, unit)
        if f == 0 and dur >= unit:
            limit = beats - b if b == 0 or beats != 4 else (2 - b % 2 if b < 2 else beats - b)
            k = min(dur // unit, limit)
            while k > 1 and k * unit not in NOTATABLE:
                k -= 1
            d = k * unit
        else:
            room = min(dur, unit - f)
            if unit == COMPOUND_UNIT:
                d = 4 if room >= 4 and f % 2 == 0 else 3 if room >= 3 and f == 0 else 2 if room >= 2 and f % 2 == 0 else 1
            else:
                d = 3 if room >= 3 and f == 0 else 2 if room >= 2 and f % 2 == 0 else 1
        out.append(d)
        pos += d
        dur -= d
    return out


NOTATABLE = (1, 2, 3, 4, 6, 8, 12, 16, 24)


class ScoreBuilder:
    """Feed note_on / note_off / pedal (sound-clock seconds); poll(now) returns the bars committed since.  With the
    analysis worker, pass ``analysis_request(now)``'s requests to it and its replies to ``on_analysis``.  For a
    display: ``position(t)`` (bars from the phrase's start), ``where(t)`` (the bar a note will be written in) and
    ``bar_seconds()``."""

    def __init__(self) -> None:
        self.clock = BeatClock()
        self.grid: Optional[BeatGrid] = None
        self.analysed = False                   # an analysis worker is answering: wait for it, don't use the clock
        self.beats, self.unit = BEATS, GRID     # the metre
        self.metre_conf = 0.0
        self.last_metre: Optional[tuple[int, int]] = None
        self.trusted_t: Optional[float] = None  # notes up to here are behind the analysis's weak edge
        self.hand: dict[int, int] = {}          # note id -> 0 treble / 1 bass, from the analysis
        self.staff_of: dict[int, int] = {}      # note id -> the staff it was first written on (ties stay there)
        self.line_staff: Optional[int] = None   # the staff a single line is on (None: no line running)
        self.history: collections.deque = collections.deque()   # the phrase's notes for the analysis
        self.req_t = -1e9
        self.req_id = 0
        self.key_fifths: Optional[int] = None
        self.key_cand: Optional[int] = None
        self.key_run = 0
        self.ev = {2: 0.0, 3: 0.0, 4: 0.0}      # the phrase's metre evidence (log-probabilities, decaying)
        self.ev_comp = 0.0
        self.flip_run = 0
        self.rewrite_pending = False
        self.phrase_bar_number = 1
        self.notes: list[Note] = []
        self.next_id = 1
        self.next_bar = 0
        self.bar_number = 1                     # printed bar numbers keep counting across phrases
        self.last_chord_t: Optional[float] = None
        self.last_event_t: Optional[float] = None
        self.phrase_t0: Optional[float] = None
        self.pedal_events: list[tuple[float, str]] = []
        self.damper_down = False
        self.swing = False
        self.offbeats: collections.deque = collections.deque(maxlen=48)
        self.pc_weight = [0.0] * 12
        self.pc_t: Optional[float] = None
        self.last_dynamic: Optional[str] = None

    @property
    def timeline(self):
        """What maps time to beats now: the analysis's grid, else the fallback clock (None-like: not running)."""
        return self.grid if self.grid is not None else self.clock

    @property
    def bar_units(self) -> int:
        return self.beats * self.unit

    # -- input ---------------------------------------------------------------------------------------------------
    def note_on(self, pitch: int, vel: float, t: float) -> int:
        if self.phrase_t0 is None:
            self.phrase_t0 = t
            self.phrase_bar_number = self.bar_number
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
        # a key struck again while it's held ends its note there (a piano can't sound one key twice at once): two
        # voices sharing a key get one release for two strikes, and the first note stayed open, tied on and on
        for m in self.notes:
            if m.pitch == int(pitch) and m.t_off is None and m.t_on < t:
                m.t_off, m.restruck = max(t, m.t_on + 0.02), True
                self._key_weight(m)
        n = Note(self.next_id, int(pitch), float(vel), t)
        self.next_id += 1
        self.notes.append(n)
        self.history.append(n)
        while self.history and self.history[0].t_on < t - HISTORY_S:
            self.history.popleft()
        self.last_event_t = t
        return n.id

    def note_off(self, pitch: int, t: float) -> None:
        """Releases the open note of ``pitch`` that began by ``t``.  The bus delivers in clumps (up to ~0.8 s late)
        and live_score passes a frame's onsets before its releases, so a re-struck key's release and new strike
        often come together, strike first: taking the newest open note released the new one before it began (a
        20 ms stub) and left the old one held, tied on bar after bar, and the phrase never ended.  A release for a
        note the next strike already ended (note_on) moves its end back to the release."""
        for n in reversed(self.notes):
            if n.pitch == pitch and n.t_off is None and n.t_on <= t + 1e-3:
                n.t_off = max(t, n.t_on + 0.02)
                self._key_weight(n)
                break
        else:
            for n in reversed(self.notes):
                if n.pitch == pitch and n.restruck and n.t_on <= t + 1e-3 and n.t_off is not None and t < n.t_off:
                    n.t_off, n.restruck = max(t, n.t_on + 0.02), False
                    break
        self.last_event_t = t

    def pedal(self, value: float, t: float) -> None:
        down = value >= 0.5
        if down != self.damper_down:
            self.damper_down = down
            self.pedal_events.append((t, "down" if down else "up"))

    # -- the analysis --------------------------------------------------------------------------------------------
    def use_analysis(self, on: bool) -> None:
        """The scene started the worker (on) or lost it (off: the fallback clock writes from the next phrase)."""
        self.analysed = on

    def analysis_request(self, now: float) -> Optional[dict]:
        """The request to send now (or None): the last WINDOW_S of the phrase's notes, onsets from ``t0``."""
        if now - self.req_t < ANALYSE_S or len(self.history) < MIN_NOTES:
            return None
        if self.trusted_t is not None and self.history and self.trusted_t + TRUST_S >= self.history[-1].t_on - 1e-6:
            return None                         # no note since the last reply's newest
        notes = [n for n in self.history if n.t_on >= now - WINDOW_S][-MAX_NOTES:]
        if len(notes) < MIN_NOTES:
            return None
        self.req_t = now
        self.req_id += 1
        t0 = notes[0].t_on
        return {"rid": self.req_id, "t0": t0, "phrase": self.phrase_t0,
                "notes": [[n.pitch, round(n.t_on - t0, 4),
                           round(max(0.03, (n.t_off if n.t_off is not None else now) - n.t_on), 4),
                           int(max(1, min(127, round(n.vel * 127)))), n.id] for n in notes]}

    def on_analysis(self, rep: dict) -> None:
        """A reply: {"t0", "phrase", "beats": [s from t0], "metre": {"beats", "phase", "compound", "conf"} | None,
        "key_fifths", "hands": {id: 0 treble | 1 bass}, "last_onset"}.  Replies from an earlier phrase are dropped."""
        self.analysed = True
        if rep.get("phrase") != self.phrase_t0 or not rep.get("ok", True):
            return
        t0 = float(rep["t0"])
        beats = [t0 + float(b) for b in rep.get("beats") or []]
        even = self._metreless()
        if even and len(beats) >= 4:
            beats = self._even_beats()
        for k, h in (rep.get("hands") or {}).items():
            self.hand[int(k)] = int(h)
        self._key_vote(rep.get("key_fifths"))
        if rep.get("last_onset") is not None:
            self.trusted_t = t0 + float(rep["last_onset"]) - TRUST_S
        m = rep.get("metre")
        if m is not None:
            probs = m.get("probs") or {str(L): (float(m.get("conf", 0.9)) if L == int(m["beats"]) else 0.05)
                                       for L in self.ev}
            for L in self.ev:
                self.ev[L] = EV_DECAY * self.ev[L] + math.log(max(1e-3, float(probs.get(str(L), 0.0))))
            cp = min(max(float(m.get("compound_p", 0.9 if m.get("compound") else 0.1)), 1e-3), 1.0 - 1e-3)
            self.ev_comp = EV_DECAY * self.ev_comp + math.log(cp / (1.0 - cp))
        if len(beats) < 4:
            return
        top = max(self.ev.values())
        post = {L: math.exp(v - top) for L, v in self.ev.items()}
        tot = sum(post.values())
        L = max(post, key=post.get)
        conf = post[L] / tot
        unit = COMPOUND_UNIT if self.ev_comp > 0 else GRID
        phases = (m or {}).get("phases") or {}
        downbeat = beats[int(phases.get(str(L), (m or {}).get("phase", 0))) % len(beats)] if m else None
        if self.grid is None:
            played = (self.last_event_t or t0) - (self.phrase_t0 or t0)
            if m is None or played < MIN_DECIDE_S or (conf < DECIDE_P and played < MAX_DECIDE_S):
                return
            g = beats if even else _steady_start(_same_level(beats, _median_ibi(beats)), _median_ibi(beats))
            if even:                             # nothing says where the bars go: common time from the first note
                L, unit, downbeat, conf = 4, GRID, self.phrase_t0, 1.0
            if _median_ibi(g) < FAST_BEAT_S and unit == GRID and L in (2, 4) and len(g) >= 6:
                # a beat on every note of a fast run (16ths at 66 bpm read as quarters at 264): half as many
                # beats, the ones the downbeat is among, and the bar counted in them
                j = min(range(len(g)), key=lambda i: abs(g[i] - downbeat)) if downbeat is not None else 0
                g, L = g[j % 2::2], (4 if even else max(2, L // 2))
            self.grid = BeatGrid(g, 0)
            self._set_metre(L, unit, downbeat, conf)
            self._resume_on_beat()
            return
        self.grid.merge(beats, self.next_bar * self.beats)
        self._resume_on_beat()
        family = (L == 3, unit == COMPOUND_UNIT)
        young = self.phrase_t0 is not None and (self.last_event_t or t0) - self.phrase_t0 < REWRITE_S
        if (m is not None and young and family != (self.beats == 3, self.unit == COMPOUND_UNIT) and conf >= REWRITE_P
                and not even):
            self.flip_run += 1
            if self.flip_run >= REWRITE_RUN:
                self._rewrite(beats, L, unit, downbeat, conf)
        else:
            self.flip_run = 0

    def _resume_on_beat(self) -> None:
        """After a silence of PAUSE_BEATS or more inside the phrase, playing resumes on a beat: a pause isn't
        metered, and the beats the analysis puts in it are guesses that differ from reply to reply (spliced, they
        put a scale's restart a 16th early).  The first onset after it moves its nearest beat onto it (the beats
        after it are the analysis's, read from the playing), the beats across the silence spaced evenly; not when
        it's a 16th or more off (an anticipation, an upbeat), and never a beat a written bar sits on."""
        g = self.grid
        frozen = self.next_bar * self.beats + g.idx0        # g.t up to this index is under written bars
        notes = sorted(self.history, key=lambda n: n.t_on)
        until = None                                        # the latest release so far
        for n in notes:
            if until is not None and n.t_on > until:
                j1 = min(range(len(g.t)), key=lambda j: abs(g.t[j] - n.t_on))
                ibi = _median_ibi(g.t[max(0, j1 - 4):j1 + 1])
                gap = (n.t_on - until) / ibi
                f = (n.t_on - g.t[j1]) / ibi
                j0 = max(bisect.bisect_right(g.t, until) - 1, frozen)   # from the last beat it sounded on (or written)
                if gap >= PAUSE_BEATS and 1e-3 < abs(f) < 0.2 and j1 > j0:
                    g.t[j1] = n.t_on
                    a, b = g.t[j0], g.t[j1]
                    g.t[j0 + 1:j1] = [a + (b - a) * i / (j1 - j0) for i in range(1, j1 - j0)]
            off = n.t_off if n.t_off is not None else n.t_on
            until = off if until is None else max(until, off)

    def _runs(self) -> tuple[list, float]:
        """The phrase's chord onsets in runs (a gap of over BREAK_IOI times the usual starts a new one), and the
        usual inter-onset interval."""
        chords: list[Note] = []
        for n in self.history:
            if not chords or n.t_on - chords[-1].t_on > CHORD_S:
                chords.append(n)
        iois = sorted(b.t_on - a.t_on for a, b in zip(chords, chords[1:]))
        med = iois[len(iois) // 2] if iois else 0.0
        runs: list[list[Note]] = []
        for c in chords:
            if runs and c.t_on - runs[-1][-1].t_on <= BREAK_IOI * med:
                runs[-1].append(c)
            else:
                runs.append([c])
        return runs, med

    def _even_beats(self) -> list:
        """A metreless phrase's beat: the usual onset interval folded into the readable octave (as the fallback
        clock does), each run on it from its first note, and a break between runs spaced evenly in whole beats.
        The analysis reads phase slips into an even run (a beat half a beat early, then back), which wrote a
        scale's last bar in 16ths."""
        runs, med = self._runs()
        P = _fold(med)
        out = [runs[0][0].t_on - P]
        for run in runs:
            a, z = run[0].t_on, run[-1].t_on
            if len(out) > 1:                   # the break before this run: whole beats, evenly
                k = max(1, int(round((a - out[-1]) / P)))
                out += [out[-1] + (a - out[-1]) * j / k for j in range(1, k)]
            out += [a + j * P for j in range(int((z - a) / P) + 3)]
        return out

    def _metreless(self) -> bool:
        """Nothing in the playing says where the bar lines go: the phrase's onsets all evenly spaced (between its
        breaks) and no accents (each run's velocities constant, or one smooth swell), as in a test pattern's scale.  The metre
        model then reads 3/4 into the middle of a scale as readily as 4/4; such material is written in common time
        on an even beat."""
        runs, med = self._runs()
        chords = [c for r in runs for c in r]
        if len(chords) < MIN_NOTES or med <= 0:
            return False
        iois = [b.t_on - a.t_on for r in runs for a, b in zip(r, r[1:])]
        if any(abs(x / med - 1.0) > EVEN_IOI for x in iois):
            return False
        for run in runs:                         # each run's velocities against their least-squares line
            n = len(run)
            xs, vs = range(n), [c.vel for c in run]
            mx, mv = (n - 1) / 2, sum(vs) / n
            sxx = sum((x - mx) ** 2 for x in xs)
            slope = sum((x - mx) * (v - mv) for x, v in zip(xs, vs)) / sxx if sxx else 0.0
            if any(abs(v - (mv + slope * (x - mx))) > EVEN_VEL for x, v in zip(xs, vs)):
                return False
        return True

    def _set_metre(self, L: int, unit: int, downbeat: Optional[float], conf: float) -> None:
        """Bar lines on the grid: beat 0 is the downbeat (in step with ``downbeat``) at or before the phrase's first
        note."""
        grid = self.grid
        self.beats, self.unit, self.metre_conf = L, unit, conf
        self.last_metre = (L, unit)
        first = self.phrase_t0 if self.phrase_t0 is not None else grid.t[0]
        grid.extend_before(first - 0.05)
        j_first = max(0, bisect.bisect_right(grid.t, first + 0.07) - 1)
        j_d = min(range(len(grid.t)), key=lambda j: abs(grid.t[j] - downbeat)) if downbeat is not None else j_first
        j0 = j_d - ((j_d - j_first + L - 1) // L) * L
        while j0 < 0:
            grid.extend_before(grid.t[0] - 1e-3)
            j0 += 1
        grid.idx0 = j0
        self.swing = self.swing and unit == GRID
        self.flip_run = 0

    def _rewrite(self, beats: list, L: int, unit: int, downbeat: Optional[float], conf: float) -> None:
        """The evidence turned: the phrase again from its start in the new metre (beats: ours under the old bars
        where the new window doesn't reach)."""
        self.grid.merge(beats)
        self.grid.idx0 = 0
        self._set_metre(L, unit, downbeat, conf)
        start = (self.phrase_t0 or 0.0) - 0.01
        self.notes = [n for n in self.history if n.t_on >= start]
        self.next_bar = 0
        self.staff_of.clear()
        self.line_staff = None
        self.bar_number = self.phrase_bar_number
        self.last_dynamic = None
        self.rewrite_pending = True

    def _key_vote(self, fifths) -> None:
        if fifths is None:
            return
        fifths = int(fifths)
        if self.key_fifths is None:
            self.key_fifths = fifths
        elif fifths == self.key_fifths:
            self.key_cand, self.key_run = None, 0
        elif fifths == self.key_cand:
            self.key_run += 1
            if self.key_run >= KEY_HOLD:
                self.key_fifths, self.key_cand, self.key_run = fifths, None, 0
        else:
            self.key_cand, self.key_run = fifths, 1

    # -- key -----------------------------------------------------------------------------------------------------
    def _key_weight(self, n: Note) -> None:
        if self.pc_t is not None:
            k = math.exp(-max(0.0, n.t_off - self.pc_t) / KEY_TAU_S)
            self.pc_weight = [w * k for w in self.pc_weight]
        self.pc_t = n.t_off
        self.pc_weight[n.pitch % 12] += min(4.0, n.t_off - n.t_on) * (0.5 + n.vel)

    def key(self) -> tuple[int, bool, int]:
        """(tonic pitch class, minor, signature fifths -7..7); with the analysis only the signature is its own (the
        tonic and mode stay Krumhansl's)."""
        w = self.pc_weight
        if sum(w) <= 0:
            return 0, False, self.key_fifths or 0
        best = (-2.0, 0, False)
        for tonic in range(12):
            for minor, prof in ((False, KK_MAJOR), (True, KK_MINOR)):
                x = [w[(tonic + i) % 12] for i in range(12)]
                r = _corr(x, prof)
                if r > best[0]:
                    best = (r, tonic, minor)
        _, tonic, minor = best
        if self.key_fifths is not None:
            return tonic, minor, self.key_fifths
        major_tonic = (tonic + 3) % 12 if minor else tonic
        fifths = (major_tonic * 7) % 12
        if fifths > 6:
            fifths -= 12                      # prefer flats from F# / Gb (6 -> keep 6 sharps), Db = -5 ...
        return tonic, minor, fifths

    # -- output --------------------------------------------------------------------------------------------------
    def bars_ahead(self, t: float) -> Optional[float]:
        """Where time ``t`` falls, in bars after the last written one (None before there's a beat to go by)."""
        tl = self.timeline
        if not tl.running or (self.analysed and self.grid is None):
            return None
        return tl.beat(t) / self.beats - self.next_bar

    def position(self, t: float) -> Optional[float]:
        """Where time ``t`` falls in bars from the phrase's bar 0 (None while bars_ahead is None)."""
        tl = self.timeline
        if not tl.running or (self.analysed and self.grid is None):
            return None
        return tl.beat(t) / self.beats

    def where(self, t: float) -> Optional[tuple[int, float, bool]]:
        """(phrase bar, fraction of it, provisional) for a note starting at ``t``, as _build will assign it; on the
        running clock in 4/4 (provisional) while the analysis hasn't decided the metre; None with no beat yet."""
        provisional = self.analysed and self.grid is None
        tl = self.clock if provisional else self.timeline
        if not tl.running:
            return None
        bu = self.bar_units
        q = self._q(tl.beat(t))
        k, r = divmod(q, bu)
        if k < 0:
            return 0, 0.0, provisional
        return int(k), r / bu, provisional

    def bar_seconds(self) -> Optional[float]:
        """A bar's length (s) at the newest beat, 0.3-6 s (None with no beat yet)."""
        if self.grid is not None and len(self.grid.t) >= 2:
            ibi = self.grid.t[-1] - self.grid.t[-2]
        elif self.clock.running:
            ibi = self.clock.P
        else:
            return None
        return min(6.0, max(0.3, self.beats * ibi))

    def poll(self, now: float) -> list[Bar]:
        out: list[Bar] = []
        if self.rewrite_pending:
            self.rewrite_pending = False
            out.append(Bar(index=0, staves=([], []), t_start=now, t_end=now, empty=True, marker=True, rewrite=True,
                           beats=self.beats, unit=self.unit))
        tl = self.timeline
        waiting = self.analysed and self.grid is None
        if not tl.running or waiting and not self._silent(now):
            return out
        if waiting:                              # the phrase ended before the analysis decided: write it as 4/4
            tl = self.clock
            if not tl.running:
                self._restart()
                return out
        upto = tl.beat(now) - BUFFER_BEATS
        if self.grid is not None and self.trusted_t is not None:
            upto = min(upto, self.grid.beat(self.trusted_t))
        elif self.grid is not None:
            upto = -1.0
        while (self.next_bar + 1) * self.beats <= upto:
            lo_t = tl.time(self.next_bar * self.beats)
            if not any(n.t_off is None or n.t_off > lo_t for n in self.notes):
                break           # trailing rests wait: a later note makes them real, the phrase's end drops them
            out.append(self._build(self.next_bar, now, tl))
            self.next_bar += 1
        if self._silent(now):
            last = max((max(self._q(tl.beat(n.t_off)), self._q(tl.beat(n.t_on)) + 1)   # a note on the last bar line
                        for n in self.notes if n.t_off is not None), default=0)
            while self.next_bar * self.bar_units < last:
                out.append(self._build(self.next_bar, now, tl))
                self.next_bar += 1
            out.append(Bar(index=0, staves=([], []), t_start=now, t_end=now, phrase_end=True, empty=True,
                           marker=True, beats=self.beats, unit=self.unit))
            self._restart()
        return out

    def _silent(self, now: float) -> bool:
        return (self.last_event_t is not None and now - self.last_event_t > SILENCE_S
                and all(n.t_off is not None for n in self.notes))

    def _restart(self) -> None:
        """A new phrase: start the clock on the next onsets; the analysis decides its metre again."""
        self.clock = BeatClock()
        self.grid = None
        self.trusted_t = None
        self.beats, self.unit = BEATS, GRID
        self.notes = []
        self.history.clear()
        self.hand.clear()
        self.staff_of.clear()
        self.line_staff = None
        self.pedal_events = []
        self.next_bar = 0
        self.last_chord_t = None
        self.phrase_t0 = None
        self.req_t = -1e9
        self.offbeats.clear()
        self.swing = False
        self.ev = {2: 0.0, 3: 0.0, 4: 0.0}
        self.ev_comp = 0.0
        self.flip_run = 0
        self.rewrite_pending = False

    def _q(self, b: float) -> int:
        """A beat position -> 16ths, with swing read as straight eighths."""
        if self.swing and self.unit == GRID:
            whole = math.floor(b)
            f = b - whole
            if 0.55 <= f <= 0.8:
                return int(whole * GRID + GRID // 2)
        return int(round(b * self.unit))

    def _staff(self, n: Note, alone: Optional[bool] = None, line: Optional[int] = None) -> int:
        """Treble (0) or bass (1).  A single line (``alone``: nothing else sounds with it) goes by register: it
        stays on ``line`` (the staff the line is on) until it passes 2 ledger lines, and starts split at middle C.
        The hand model calls a one-hand scale all left hand (C5 on the bass staff) and its guesses flicker inside
        a run.  Otherwise the analysed hand, within 3 ledger lines of its staff (no hand: split at middle C).
        Called with the note alone (live_score's note glow): the staff it was written on, else as it would be."""
        if alone is None:
            written = self.staff_of.get(n.id)
            if written is not None:
                return written
            alone, line = self._alone(n, self.notes, self.last_event_t or n.t_on), self.line_staff
        if alone:
            if line == 0 and n.pitch >= LINE_TREBLE_LOW or line == 1 and n.pitch <= LINE_BASS_HIGH:
                return line
            return 0 if n.pitch >= SPLIT else 1
        h = self.hand.get(n.id)
        if h is None:
            return 0 if n.pitch >= SPLIT else 1
        if h == 0 and n.pitch < TREBLE_LOW:
            return 1
        if h == 1 and n.pitch > BASS_HIGH:
            return 0
        return h

    @staticmethod
    def _alone(n: Note, notes: list, now: float) -> bool:
        """No other note sounds with ``n`` beyond a legato overlap."""
        a0, a1 = n.t_on, n.t_off if n.t_off is not None else now
        for m in notes:
            if m is n:
                continue
            b0, b1 = m.t_on, m.t_off if m.t_off is not None else now
            ov = min(a1, b1) - max(a0, b0)
            if ov > max(MONO_OVERLAP_S, min(a1 - a0, b1 - b0) / 3):
                return False
        return True

    def _build(self, k: int, now: float, tl=None) -> Bar:
        tl = tl or self.timeline
        if tl is self.clock and self.grid is None:
            self.beats, self.unit = BEATS, GRID
        B, U = self.beats, self.unit
        bar_u = B * U
        lo, hi = k * bar_u, (k + 1) * bar_u
        segs: tuple[list, list] = ([], [])
        vels = []
        after: list = [None, None]               # per staff: the next onset after this bar (16ths from its start)
        # who sounds with whom, looked up in onset order (a phrase's first bars come in one batch: no all-pairs)
        order = sorted(self.notes, key=lambda m: m.t_on)
        ons = [m.t_on for m in order]
        longest = max(((m.t_off if m.t_off is not None else now) - m.t_on for m in order), default=0.0)

        def alone(n: Note) -> bool:
            end = n.t_off if n.t_off is not None else now
            return self._alone(n, order[bisect.bisect_left(ons, n.t_on - longest):bisect.bisect_left(ons, end)], now)

        for n in self.notes:
            s = self._q(tl.beat(n.t_on))
            e = self._q(tl.beat(n.t_off if n.t_off is not None else now))
            e = max(e, s + 1)
            if s >= hi:
                if s < hi + U:                  # only a next onset within a beat of the bar line matters
                    st = self.staff_of.get(n.id)
                    if st is None:              # not cached: its hand may still change before its own bar
                        st = self._staff(n, alone(n), self.line_staff)
                    if after[st] is None or s - lo < after[st]:
                        after[st] = s - lo
                continue
            if e <= lo:
                continue
            staff = self.staff_of.get(n.id)
            if staff is None:
                one = alone(n)
                staff = self.staff_of[n.id] = self._staff(n, one, self.line_staff)
                self.line_staff = staff if one else None
            segs[staff].append((max(s, lo) - lo, min(e, hi) - lo, n.pitch, n.id, s < lo, e > hi))
            if lo <= s < hi:
                vels.append(n.vel)
        staves = tuple(self._voice(sorted(sg), B, U, after[i]) for i, sg in enumerate(segs))
        bar = Bar(index=self.bar_number, staves=staves, t_start=tl.time(lo / U), t_end=tl.time(hi / U),
                  empty=not any(segs), beats=B, unit=U, pos=k)
        self.bar_number += 1
        if vels:
            m = sum(vels) / len(vels)
            dyn = next(name for edge, name in DYNAMICS if m < edge)
            if dyn != self.last_dynamic:
                bar.dynamic = dyn
                self.last_dynamic = dyn
        for t, what in self.pedal_events:
            p = self._q(tl.beat(t)) - lo
            if 0 <= p < bar_u:
                bar.pedal.append((p, what))
        # forget what can't reach later bars (a short note whose onset rounds up onto the bar line is the next
        # bar's even when its release rounds there too: it was dropped unwritten)
        self.notes = [n for n in self.notes
                      if n.t_off is None or self._q(tl.beat(n.t_off)) > hi or self._q(tl.beat(n.t_on)) >= hi]
        live = {n.id for n in self.notes}
        self.staff_of = {i: s for i, s in self.staff_of.items() if i in live}
        self.pedal_events = [(t, w) for t, w in self.pedal_events if self._q(tl.beat(t)) >= hi]
        return bar

    @staticmethod
    def _voice(segs: list, beats: int = BEATS, unit: int = GRID, after: Optional[int] = None) -> list[Event]:
        """One staff's notes in a bar -> chords and rests covering the bar, split into notatable tied pieces.
        ``after``: the staff's next onset after the bar (16ths from the bar's start; None: none yet), which the
        bar's last note may run on to like any other next onset (not the bar line: a scale's last eighth isn't a
        quarter)."""
        bar = beats * unit
        starts = sorted({s for s, *_ in segs})
        events: list[Event] = []
        pos = 0
        for i, s in enumerate(starts):
            group = [g for g in segs if g[0] == s]
            last = i + 1 == len(starts)
            nxt = bar if last else starts[i + 1]
            end = max(1, min(nxt, max(g[1] for g in group)) - s) + s
            to = after if last else nxt         # the next onset it may run on to (None: nothing follows yet)
            if end < nxt and to is not None and (to - s <= unit or to - end < unit // 2):
                end = nxt                       # short gaps aren't written as rests: the note runs to the next one
            if s > pos:
                events += [Event(p, d) for p, d in _pieces(pos, s - pos, beats, unit)]
            pitches = [g[2] for g in group]
            ids = [g[3] for g in group]
            tie_in = [g[4] for g in group]
            tie_out = [g[5] and g[1] >= bar and end >= bar for g in group]
            pieces = _pieces(s, end - s, beats, unit)
            for j, (p, d) in enumerate(pieces):
                last = j == len(pieces) - 1
                events.append(Event(p, d, pitches, ids, tie_in if j == 0 else [True] * len(pitches),
                                    tie_out if last else [True] * len(pitches)))
            pos = end
        if pos < bar:
            events += [Event(p, d) for p, d in _pieces(pos, bar - pos, beats, unit)]
        return events


def _pieces(pos: int, dur: int, beats: int = BEATS, unit: int = GRID) -> list[tuple[int, int]]:
    out = []
    for d in split_value(pos, dur, beats, unit):
        out.append((pos, d))
        pos += d
    return out


def _corr(x: list[float], y: tuple) -> float:
    mx, my = sum(x) / 12, sum(y) / 12
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    den = math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
    return num / den if den > 0 else 0.0
