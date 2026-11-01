"""Practice: is the pianist working at a piece rather than playing it through? (owner, 1 Nov: "it's a piano practice
session, so lots of stopping and starting. the a.d. appears to be assuming it's VERY choppy music and changing scenes
every few seconds.") A 0..1 read from the voice piano's note-ons alone, for the art director (the stops are rests
inside one piece: no fades to dark, slow scene changes) and Auto.

**What practice looks like** (the owner's practice, 1 Nov, against the 711 pieces of the MIDI library played
straight): stops, mostly short (1-3 s between note-ons, against 0.1-0.5 s within the playing) and now and then a long
one (5-40 s: looking at the music); runs of 5-25 s; restarts that replay a passage played again and again in the last
few minutes; and the playing breaks off where it is, on a note or two. A performance plays through: its stops are
few, mostly a cadence or a fermata (a chord, held), and what follows one is mostly new. A recital has one long rest
per piece, after its last chord, and the next piece is new.

**Evidence**, from the note-ons of the last WINDOW_S (from the newest gap of ``rest_max_s`` or more: before that is
another sitting). A *stop* is a gap of at least ``stop_s`` that is also ``stop_med`` times the median gap between
onsets and ``stop_rel`` times their ``pace_q`` quantile over the ``pace_s`` before it (gaps over ``chord_s`` and under
``play_gap_s``; a slow piece's 1.5 s beats are its pace, not stops). Each stop counts, judged once:

- its length: ``stop_floor`` at ``stop_s``, rising to 1 at ``stop_full_s`` and on to ``stop_max``;
- times what it broke off on: 1 after one or two notes, ``chord3`` after a chord of three, ``chord4`` after more;
- times 1 + its replays: how often its next ``replay_k`` notes (at least ``replay_hits`` of them, in any order: two
  hands never strike in the same order twice, and a slip is allowed) were played together in the ``replay_back_s``
  before it, at most ``replay_max`` (a stop is judged once those notes are in; until then it counts no replays).

E is the window's evidence per WINDOW_S (counted over at least ``min_span_s``: a sitting's first minute and a half
counts as that much), and raw = clamp((E - e_lo) / (e_hi - e_lo)), decided only with ``min_play_s`` of playing in the
window (the sum of gaps under ``play_gap_s``); with less, the last raw holds.

**Smoothing.** The value follows raw, rising with ``rise_tau_s`` and falling with ``fall_tau_s`` (slow: one long run
through the piece mid-practice is still practice). Raw is decided only while the piano is being played (the newest
note under ``play_gap_s`` old): a rest in progress has not been followed by playing yet, and a long pause never lets
the window slide past the stops before it. With no notes for ``hold_s`` the value falls in a straight line to 0 at
``forget_s``, and the next note starts a fresh sitting.

**Calibration** (1 Nov, fed at 1 Hz and 2 Hz): the owner's 9 min of practice reads 0.7 at 85 s from a cold start
and stays 0.76-1.0 after; synthetic practice cut from library pieces (attempts of 4-20 s, rests of 3-40 s, half the
restarts replaying) reads 0.7 at a median 90 s; of the 711 pieces played straight, 21 pass 0.35 at some point (the
stop-start ones: Einaudi, Tempest I, Chopin's second Scherzo, Asturias, Der Dichter spricht) and 32 pass 0.2; the
Gymnopedies, Gnossiennes, Moonlight I and the Goldberg Aria stay at 0; recitals of pieces that read low stay under
0.32 (20-40 s between pieces). ``evidence`` keys: stops, replays, evidence (E), play_s, span_s, raw, value, silent_s.

**Cost.** ``update`` runs on show-rx every SNAPSHOT_S. It takes only the strikes newer than the last call (a walk back
from the end), keeps the window's notes and stops itself, and judges each stop once (one slide over the notes of
``replay_back_s``). Well under 1 ms per call with 1000 notes in the window (tests/test_practice.py measures it).
"""
from __future__ import annotations

import math
from typing import Any, Optional

WINDOW_S = 180.0         # how far back update() needs strikes (s)

FALLBACK: dict = {       # starting points: calibrated on the owner's practice (1 Nov) and the MIDI library
    "window_s": WINDOW_S,
    "stop_s": 1.0,       # a stop: a gap between note-ons at least this long ...
    "stop_med": 4.0,     # ... and this many times the median gap between onsets ...
    "stop_rel": 1.5,     # ... and this many times their pace_q quantile ...
    "pace_q": 0.9,
    "pace_s": 60.0,      # ... over this long before it (with fewer than pace_n gaps, stop_s alone)
    "pace_n": 4,
    "chord_s": 0.05,     # note-ons this close are one onset (a chord)
    "stop_floor": 0.1,   # a stop_s stop counts this much, ...
    "stop_full_s": 8.0,  # ... 1 at this length ...
    "stop_max": 2.0,     # ... and longer ones on up to this
    "chord3": 0.6,       # a stop after a chord of three notes counts this much of that ...
    "chord4": 0.3,       # ... and after four or more this much (a cadence, held)
    "replay_k": 10,      # a stop's next this many notes ...
    "replay_hits": 8,    # ... this many of them played together before is a replay ...
    "replay_back_s": 170.0,  # ... in this long before the stop
    "replay_max": 2,     # a stop counts 1 + its replays, at most this many
    "e_lo": 4.0,         # evidence per window_s: at or under this, no practice ...
    "e_hi": 8.0,         # ... at or over this, all practice
    "min_span_s": 90.0,  # evidence counted over at least this much time
    "min_play_s": 20.0,  # playing needed before deciding (else the last raw holds)
    "play_gap_s": 2.5,   # gaps under this are playing; the newest note this old: a rest in progress
    "rest_max_s": 120.0,  # a gap this long is a new sitting (what came before is left out)
    "rise_tau_s": 20.0,
    "fall_tau_s": 150.0,
    "hold_s": 120.0,     # no notes this long: the value starts falling ...
    "forget_s": 300.0,   # ... to 0 here (a new sitting starts fresh)
    "max_dt_s": 5.0,     # one update moves the value at most this much time's worth
}
_INTS = ("pace_n", "replay_k", "replay_hits", "replay_max")


def _num(x: Any) -> Optional[float]:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _get(e: Any, i: int) -> Any:
    try:
        return e[i]
    except (TypeError, IndexError, KeyError):
        return None


class PracticeRead:
    """The smoothed practice read (0..1, ``value``) of one voice stream's note-ons, with ``evidence`` (small numbers
    for the log and the card). One caller thread (show-rx); readers on other threads take ``value`` and ``evidence``,
    which are replaced, never mutated."""

    def __init__(self, cfg: Optional[dict] = None) -> None:
        c = dict(FALLBACK)
        for k, v in (cfg.items() if isinstance(cfg, dict) else ()):
            f = _num(v) if k in FALLBACK else None
            if f is not None and f >= 0:
                c[k] = f
        for k in _INTS:
            c[k] = max(1, int(c[k]))
        c["replay_k"] = max(3, c["replay_k"])
        c["replay_hits"] = min(c["replay_hits"], c["replay_k"])
        c["stop_full_s"] = max(c["stop_full_s"], c["stop_s"] + 0.1)
        c["e_hi"] = max(c["e_hi"], c["e_lo"] + 0.1)
        c["forget_s"] = max(c["forget_s"], c["hold_s"] + 1.0)
        c["window_s"] = max(c["window_s"], c["replay_back_s"], c["pace_s"])
        self.cfg = c
        self.evidence: dict = {"value": 0.0}
        self._fresh()
        self._t: Optional[float] = None            # the last update's now_s
        self._seen: float = -math.inf              # the newest strike taken ...
        self._seen_n = 0                           # ... and how many with exactly that time

    def _fresh(self) -> None:
        """A new sitting: no notes, no stops, no read."""
        self.value: float = 0.0
        self.raw: Optional[float] = None
        self._held: Optional[float] = None         # the value when the silence passed hold_s
        self._ts: list[float] = []                 # the window's note-on times ...
        self._ps: list[int] = []                   # ... and pitches
        self._base = 0                             # notes dropped from the front (a note's number - _base = index)
        self._first = 0                            # the sitting's first note (number)
        self._play = 0.0                           # sum of gaps under play_gap_s, from the sitting's first note on
        self._stops: list[list] = []               # [time, note number, weight, replays (None: not judged yet)]

    # -- the read ----------------------------------------------------------------------------------------------------
    def update(self, strikes: Any, now_s: float) -> float:
        """Feed the window's note-ons (NoteTap.raw_since entries, oldest first) at ``now_s`` (monotonic s); returns and
        keeps the smoothed value. Odd input (a non-list, short or non-numeric entries, NaN, entries out of order) is
        skipped, never raised."""
        try:
            return self._update(strikes, now_s)
        except Exception:                          # a sense never takes show-rx down
            return self.value

    def _update(self, strikes: Any, now_s: float) -> float:
        now = _num(now_s)
        if now is None:
            return self.value
        c = self.cfg
        if self._t is not None and now < self._t - 1.0:   # the clock went back: start again
            self._fresh()
            self._seen, self._seen_n = -math.inf, 0
        dt = 0.0 if self._t is None else min(max(0.0, now - self._t), c["max_dt_s"])
        self._t = now
        for t, p in self._new(strikes, now):
            self._add(t, p)
        self._trim(now - c["window_s"])
        ts = self._ts
        age = now - ts[-1] if ts else None
        if age is None or age >= c["forget_s"]:
            if self.value or ts or self.raw is not None:
                self._fresh()
            self.evidence = {"value": 0.0, "silent_s": None if age is None else round(age, 1)}
            return self.value
        self._judge()
        if age < c["play_gap_s"]:
            self._decide(now)
        if age >= c["hold_s"]:
            if self._held is None:
                self._held = self.value
            self.value = self._held * max(0.0, (c["forget_s"] - age) / (c["forget_s"] - c["hold_s"]))
        else:
            self._held = None
            if self.raw is not None and dt > 0:
                tau = c["rise_tau_s"] if self.raw > self.value else c["fall_tau_s"]
                self.value += (self.raw - self.value) * (1.0 - math.exp(-dt / max(tau, 1e-3)))
        self.value = min(1.0, max(0.0, self.value))
        ev = dict(self.evidence)
        ev["value"] = round(self.value, 3)
        ev["silent_s"] = round(age, 1)
        self.evidence = ev
        return self.value

    # -- the window --------------------------------------------------------------------------------------------------
    def _new(self, strikes: Any, now: float) -> list[tuple[float, int]]:
        """The strikes not taken yet (newer than the newest taken, or at its time past the ones taken), up to now + 1 s,
        oldest first. Only the new tail is walked."""
        if not isinstance(strikes, (list, tuple)) or not strikes:
            return []
        seen, hi = self._seen, now + 1.0
        i = len(strikes)
        while i > 0:
            t = _num(_get(strikes[i - 1], 0))
            if t is not None and t < seen:
                break
            i -= 1
        out: list[tuple[float, int]] = []
        skip = self._seen_n                        # entries at exactly ``seen`` that were taken already
        for e in strikes[i:]:
            t, p = _num(_get(e, 0)), _num(_get(e, 3))
            if t is None or p is None or t < self._seen or t > hi:
                continue
            if t == seen and skip > 0:
                skip -= 1
                continue
            if t == self._seen:
                self._seen_n += 1
            else:
                self._seen, self._seen_n = t, 1
            out.append((t, int(p)))
        return out

    def _add(self, t: float, p: int) -> None:
        """One note-on: the play sum, a new sitting, or a stop before it."""
        c = self.cfg
        ts, ps = self._ts, self._ps
        g = t - ts[-1] if ts else 0.0
        if g >= c["rest_max_s"]:                   # another sitting (the value holds, or has decayed meanwhile)
            self._base += len(ts)
            self._first = self._base
            ts.clear()
            ps.clear()
            self._play, self._stops = 0.0, []
        elif g < c["play_gap_s"]:
            self._play += g
        n = self._base + len(ts)                    # this note's number
        ts.append(t)
        ps.append(p)
        if len(ts) < 2 or g < c["stop_s"]:
            return
        last = len(ts) - 2                          # the note before the gap
        chord, pg, lo = c["chord_s"], c["play_gap_s"], t - c["pace_s"]
        gaps = []
        j = last
        while j > 0 and ts[j - 1] >= lo:
            d = ts[j] - ts[j - 1]
            if chord < d < pg:
                gaps.append(d)
            j -= 1
        if len(gaps) >= c["pace_n"]:
            gaps.sort()
            m = len(gaps)
            if g < c["stop_med"] * gaps[m // 2] or g < c["stop_rel"] * gaps[min(m - 1, int(c["pace_q"] * m))]:
                return
        w = c["stop_floor"] + (1.0 - c["stop_floor"]) * (g - c["stop_s"]) / (c["stop_full_s"] - c["stop_s"])
        w = min(c["stop_max"], w)
        size = 1
        j = last
        while j > 0 and ts[last] - ts[j - 1] <= chord:
            size += 1
            j -= 1
        if size >= 4:
            w *= c["chord4"]
        elif size == 3:
            w *= c["chord3"]
        self._stops.append([t, n, w, None])

    def _trim(self, lo: float) -> None:
        """Drop notes and stops older than ``lo`` (keeping the play sum honest)."""
        ts = self._ts
        k = 0
        pg = self.cfg["play_gap_s"]
        while k < len(ts) - 1 and ts[k] < lo:
            g = ts[k + 1] - ts[k]
            if g < pg:
                self._play -= g
            k += 1
        if k:
            del ts[:k]
            del self._ps[:k]
            self._base += k
            self._play = max(0.0, self._play)
        self._first = max(self._first, self._base)
        st = self._stops
        k = 0
        while k < len(st) and st[k][0] < lo:
            k += 1
        if k:
            del st[:k]

    def _judge(self) -> None:
        """Each stop's replays, once its next replay_k notes are in."""
        c = self.cfg
        k, need, back = c["replay_k"], c["replay_hits"], c["replay_back_s"]
        ts, ps, base = self._ts, self._ps, self._base
        for s in self._stops:
            if s[3] is not None:
                continue
            i = s[1] - base
            if i + k > len(ts):
                continue
            if i < 0:                              # its notes left the window before they were all in
                s[3] = 0
                continue
            b = i
            lo = ts[i] - back
            while b > 0 and ts[b - 1] >= lo:
                b -= 1
            s[3] = _occurrences(ps, b, i, k, need, c["replay_max"])

    def _decide(self, now: float) -> None:
        """Raw from the window's stops (the piano is being played)."""
        c = self.cfg
        e = 0.0
        n = reps = 0
        for _t, _n, w, r in self._stops:
            e += w * (1 + (r or 0))
            n += 1
            reps += bool(r)
        t0 = self._ts[self._first - self._base] if self._ts else now
        per = e * c["window_s"] / max(now - t0, c["min_span_s"])
        ev = {"stops": n, "replays": reps, "evidence": round(per, 2), "play_s": round(self._play, 1),
              "span_s": round(min(now - t0, c["window_s"]), 1)}
        if self._play >= c["min_play_s"]:
            self.raw = min(1.0, max(0.0, (per - c["e_lo"]) / (c["e_hi"] - c["e_lo"])))
        ev["raw"] = None if self.raw is None else round(self.raw, 3)
        self.evidence = ev


def _occurrences(ps: list, b: int, i: int, k: int, need: int, cap: int) -> int:
    """How many times (up to ``cap``) at least ``need`` of ps[i:i+k] (as a multiset) were among k consecutive notes of
    ps[b:i]; a found place is counted once (the slide skips k notes past it)."""
    if i - b < need:
        return 0
    head: dict[int, int] = {}
    for p in ps[i:i + k]:
        head[p] = head.get(p, 0) + 1
    win: dict[int, int] = {}
    ov = found = 0
    after = b
    for j in range(b, i):
        p = ps[j]
        m = win.get(p, 0) + 1
        win[p] = m
        if m <= head.get(p, 0):
            ov += 1
        if j - b >= k:                             # the note leaving the window
            q = ps[j - k]
            m = win[q]
            if m <= head.get(q, 0):
                ov -= 1
            win[q] = m - 1
        if ov >= need and j >= after:
            found += 1
            if found >= cap:
                break
            after = j + k
    return found
