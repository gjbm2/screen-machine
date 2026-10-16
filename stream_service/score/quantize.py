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
times, those under written bars frozen, later ones replaced by each new analysis.  A bar is written once its end is
TRUST_S before the newest analysed note (the networks look both ways: the newest beats are the weakest).  Notes go
to the treble or bass staff by the hand PM2S gives them (far out of range: the other staff).

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
        start), then theirs from after it."""
        if keep_beat is None:
            ibi0 = self.t[-1] - self.t[-2]
            keep_n = bisect.bisect_left(self.t, float(times[0]) - 0.5 * ibi0) if len(times) else len(self.t)
        else:
            keep_n = keep_beat + self.idx0 + 1
        keep = self.t[:max(2, min(len(self.t), keep_n))]
        ibi = keep[-1] - keep[-2]
        new = [float(x) for x in times if x > keep[-1] + 0.5 * ibi]
        out = keep + new
        clean = out[:2]
        for x in out[2:]:                      # a gap of whole beats (a missed beat) is filled evenly
            gap = x - clean[-1]
            last = clean[-1] - clean[-2]
            k = int(round(gap / last)) if last > 0 else 1
            if k >= 2 and abs(gap / k - last) < 0.25 * last:
                clean += [clean[-1] + gap * i / k for i in range(1, k)]
            if gap > 0.3 * last:
                clean.append(x)
        self.t = clean

    def extend_before(self, t: float) -> None:
        """Carry the beats back until the first is at or before ``t`` (bar 0 keeps its numbering)."""
        while self.t[0] > t:
            ibi = self.t[1] - self.t[0]
            self.t.insert(0, self.t[0] - ibi)
            self.idx0 += 1


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
    analysis worker, pass ``analysis_request(now)``'s requests to it and its replies to ``on_analysis``."""

    def __init__(self) -> None:
        self.clock = BeatClock()
        self.grid: Optional[BeatGrid] = None
        self.analysed = False                   # an analysis worker is answering: wait for it, don't use the clock
        self.beats, self.unit = BEATS, GRID     # the metre
        self.metre_conf = 0.0
        self.last_metre: Optional[tuple[int, int]] = None
        self.trusted_t: Optional[float] = None  # notes up to here are behind the analysis's weak edge
        self.hand: dict[int, int] = {}          # note id -> 0 treble / 1 bass, from the analysis
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
        n = Note(self.next_id, int(pitch), float(vel), t)
        self.next_id += 1
        self.notes.append(n)
        self.history.append(n)
        while self.history and self.history[0].t_on < t - HISTORY_S:
            self.history.popleft()
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
            self.grid = BeatGrid(beats, 0)
            self._set_metre(L, unit, downbeat, conf)
            return
        self.grid.merge(beats, self.next_bar * self.beats)
        family = (L == 3, unit == COMPOUND_UNIT)
        young = self.phrase_t0 is not None and (self.last_event_t or t0) - self.phrase_t0 < REWRITE_S
        if m is not None and young and family != (self.beats == 3, self.unit == COMPOUND_UNIT) and conf >= REWRITE_P:
            self.flip_run += 1
            if self.flip_run >= REWRITE_RUN:
                self._rewrite(beats, L, unit, downbeat, conf)
        else:
            self.flip_run = 0

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
            last = max((self._q(tl.beat(n.t_off)) for n in self.notes if n.t_off is not None), default=0)
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

    def _staff(self, n: Note) -> int:
        h = self.hand.get(n.id)
        if h is None:
            return 0 if n.pitch >= SPLIT else 1
        if h == 0 and n.pitch < 48:
            return 1
        if h == 1 and n.pitch > 72:
            return 0
        return h

    def _build(self, k: int, now: float, tl=None) -> Bar:
        tl = tl or self.timeline
        if tl is self.clock and self.grid is None:
            self.beats, self.unit = BEATS, GRID
        B, U = self.beats, self.unit
        bar_u = B * U
        lo, hi = k * bar_u, (k + 1) * bar_u
        segs: tuple[list, list] = ([], [])
        vels = []
        for n in self.notes:
            s = self._q(tl.beat(n.t_on))
            e = self._q(tl.beat(n.t_off if n.t_off is not None else now))
            e = max(e, s + 1)
            if e <= lo or s >= hi:
                continue
            segs[self._staff(n)].append((max(s, lo) - lo, min(e, hi) - lo, n.pitch, n.id, s < lo, e > hi))
            if lo <= s < hi:
                vels.append(n.vel)
        staves = tuple(self._voice(sorted(sg), B, U) for sg in segs)
        bar = Bar(index=self.bar_number, staves=staves, t_start=tl.time(lo / U), t_end=tl.time(hi / U),
                  empty=not any(segs), beats=B, unit=U)
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
        # forget what can't reach later bars
        self.notes = [n for n in self.notes if n.t_off is None or self._q(tl.beat(n.t_off)) > hi]
        self.pedal_events = [(t, w) for t, w in self.pedal_events if self._q(tl.beat(t)) >= hi]
        return bar

    @staticmethod
    def _voice(segs: list, beats: int = BEATS, unit: int = GRID) -> list[Event]:
        """One staff's notes in a bar -> chords and rests covering the bar, split into notatable tied pieces."""
        bar = beats * unit
        starts = sorted({s for s, *_ in segs})
        events: list[Event] = []
        pos = 0
        for i, s in enumerate(starts):
            group = [g for g in segs if g[0] == s]
            nxt = starts[i + 1] if i + 1 < len(starts) else bar
            end = max(1, min(nxt, max(g[1] for g in group)) - s) + s
            if end < nxt and (nxt - s <= unit or nxt - end < unit // 2):
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
