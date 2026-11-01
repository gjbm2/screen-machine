"""practice.py: the practice read from the voice piano's note-ons (shared plumbing for the director, cues and Auto: real
tests). Synthetic note streams only, made here from a seeded random walk; no files, no sockets."""

from __future__ import annotations

import math
import random
import time

from show_service import practice
from show_service.practice import FALLBACK, WINDOW_S, PracticeRead


def piece(seed: int, seconds: float, *, ioi: float = 0.25, cadence_s: float = 30.0) -> list[tuple[float, int]]:
    """A through-composed line: (t, pitch) note-ons from 0, a random walk at ``ioi`` with a bass note on every fourth,
    a short breath every 8 s, and a held four-note cadence chord (then 2.5 s) every ``cadence_s``."""
    rng = random.Random(seed)
    out: list[tuple[float, int]] = []
    t, p, k = 0.0, 64, 0
    next_breath, next_cadence = 8.0, cadence_s
    while t < seconds:
        p = min(88, max(52, p + rng.choice((-4, -3, -2, -1, 1, 2, 3, 5))))
        out.append((t, p))
        if k % 4 == 0:
            out.append((t + 0.01, p - 24 + rng.randrange(-2, 3)))
        k += 1
        t += ioi * rng.uniform(0.8, 1.25)
        if t >= next_cadence:
            for d, q in enumerate((48, 55, 60, 64)):
                out.append((t + 0.005 * d, q + rng.randrange(0, 3)))
            t += 2.5
            next_cadence += cadence_s
            next_breath = t + 8.0
        elif t >= next_breath:
            t += 0.6
            next_breath += 8.0
    return out


def practice_session(seed: int, seconds: float) -> list[tuple[float, int]]:
    """Working at the first 20 s of a piece: attempts of 4-15 s that break off on a note, rests of 2-12 s, about half
    of them going back to the passage's start and the rest to the last attempt's last few seconds."""
    rng = random.Random(seed)
    src = piece(seed, 40.0, cadence_s=999.0)
    out: list[tuple[float, int]] = []
    t, start = 0.0, 0.0
    while t < seconds:
        length = rng.uniform(4.0, 15.0)
        part = [(a - start, q) for a, q in src if start <= a < start + length]
        for a, q in part:
            out.append((t + a, q))
        t = out[-1][0] + rng.uniform(2.0, 12.0)
        start = 0.0 if rng.random() < 0.5 else max(0.0, min(start + length - rng.uniform(2.0, 5.0), 20.0))
    return out


def run(notes: list[tuple[float, int]], *, step: float = 1.0, tail: float = 0.0, t0: float = 5000.0,
        r: PracticeRead | None = None) -> tuple[list[tuple[float, float]], PracticeRead]:
    """Feed ``notes`` as the caller does (the strikes of the last WINDOW_S, every ``step``); (t, value) per call."""
    r = r or PracticeRead()
    strikes = [(t0 + a, "voice", 1, q, 80) for a, q in notes]
    out = []
    end = (notes[-1][0] if notes else 0.0) + tail
    a = b = 0
    t = 0.0
    while t <= end:
        now = t0 + t
        while b < len(strikes) and strikes[b][0] <= now:
            b += 1
        while a < b and strikes[a][0] < now - WINDOW_S:
            a += 1
        out.append((t, r.update(strikes[a:b], now)))
        t += step
    return out, r


def first_at(series: list[tuple[float, float]], x: float) -> float | None:
    return next((t for t, v in series if v >= x), None)


# -- the read ---------------------------------------------------------------------------------------------------------
def test_practice_reads_high():
    for seed in (1, 2, 3):
        series, r = run(practice_session(seed, 420.0))
        t7 = first_at(series, 0.7)
        assert t7 is not None and t7 <= 240, (seed, t7, r.evidence)
        assert min(v for t, v in series if t >= t7) >= 0.6, (seed, r.evidence)
        assert r.evidence["stops"] >= 5 and r.evidence["replays"] >= 2


def test_straight_playing_reads_low():
    for seed in (4, 5, 6):
        series, r = run(piece(seed, 360.0))
        assert max(v for _, v in series) <= 0.2, (seed, r.evidence)


def test_recital_reads_low():
    notes: list[tuple[float, int]] = []
    t = 0.0
    for seed, gap in ((7, 25.0), (8, 40.0), (9, 0.0)):
        notes += [(t + a, q) for a, q in piece(seed, 150.0)]
        t = notes[-1][0] + gap
    series, r = run(notes)
    assert max(v for _, v in series) <= 0.3, r.evidence


def test_slow_piece_beats_are_not_stops():
    """1.4 s between onsets throughout (a slow piece): its pace, not stops."""
    rng = random.Random(10)
    notes, t = [], 0.0
    for _ in range(220):
        notes.append((t, 60 + rng.randrange(-7, 8)))
        t += 1.4 * rng.uniform(0.9, 1.1)
    series, r = run(notes)
    assert max(v for _, v in series) <= 0.2, r.evidence


def test_holds_through_a_rest_then_decays_and_forgets():
    notes = practice_session(11, 300.0)
    series, r = run(notes, tail=FALLBACK["forget_s"] + 30.0)
    last = notes[-1][0]
    at = dict(series)
    v_end = at[math.floor(last)]
    assert v_end >= 0.6
    # a rest under hold_s changes nothing
    assert abs(at[math.floor(last) + 60] - at[math.floor(last) + 2]) < 0.05
    # past hold_s it falls in a straight line, to 0 at forget_s
    mid = at[math.floor(last + (FALLBACK["hold_s"] + FALLBACK["forget_s"]) / 2)]
    assert 0.1 < mid < v_end
    assert at[math.floor(last + FALLBACK["forget_s"]) + 5] == 0.0
    assert r.raw is None and r.evidence["value"] == 0.0


def test_new_sitting_starts_fresh():
    """After forget_s of silence the stops before it are gone: straight playing then reads low again."""
    notes = practice_session(12, 300.0)
    later = [(notes[-1][0] + FALLBACK["forget_s"] + 20.0 + a, q) for a, q in piece(13, 200.0)]
    series, r = run(notes + later)
    tail = [v for t, v in series if t >= later[0][0]]
    assert max(tail) <= 0.2
    assert all(s[0] >= 5000.0 + later[0][0] for s in r._stops)


def test_value_is_held_between_calls_and_kept():
    series, r = run(practice_session(14, 200.0))
    assert r.value == series[-1][1]
    assert set(r.evidence) >= {"value", "stops", "replays", "evidence", "play_s", "raw", "silent_s"}
    assert all(isinstance(v, (int, float, type(None))) for v in r.evidence.values())


def test_cfg_overrides_and_bad_cfg():
    r = PracticeRead({"e_lo": 1.0, "rise_tau_s": "5", "nope": 3, "e_hi": "x", "replay_k": -2})
    assert r.cfg["e_lo"] == 1.0 and r.cfg["rise_tau_s"] == 5.0 and "nope" not in r.cfg
    assert r.cfg["e_hi"] == FALLBACK["e_hi"] and r.cfg["replay_k"] == FALLBACK["replay_k"]
    assert PracticeRead("not a dict").cfg == PracticeRead().cfg


# -- odd input --------------------------------------------------------------------------------------------------------
def test_odd_input_never_raises():
    r = PracticeRead()
    odd = [None, "notes", 7, {}, [None], [()], [("x", "s", 1, 60, 80)], [(1.0,)], [(float("nan"), "s", 1, 60, 80)],
           [(float("inf"), "s", 1, 60, 80)], [(10.0, "s", 1, None, 80)], [{"t": 1}], [(10.0, "s", 1, "60", 80)],
           [(12.0, "s", 1, 60, 80), (11.0, "s", 1, 62, 80), (13.0, "s", 1, 64, 80)]]
    for i, s in enumerate(odd):
        v = r.update(s, 10.0 + i)
        assert 0.0 <= v <= 1.0
    for now in (float("nan"), None, "x", float("inf")):
        assert 0.0 <= r.update([], now) <= 1.0                      # type: ignore[arg-type]
    assert 0.0 <= r.update([(5.0, "s", 1, 60, 80)], 1.0) <= 1.0     # the clock went back: starts again
    assert 0.0 <= r.update([(1e12, "s", 1, 60, 80)], 2.0) <= 1.0    # a note from the future waits


def test_same_strikes_twice_count_once_and_same_time_batch_is_whole():
    r = PracticeRead()
    batch = [(100.0, "s", 1, 60, 80), (100.0, "s", 1, 64, 80)]
    r.update(batch[:1], 100.0)                                      # half a batch at one arrival time
    r.update(batch, 100.5)                                          # the rest of it, same time
    r.update(batch, 101.0)                                          # nothing new
    assert r._ps == [60, 64]


# -- cost ---------------------------------------------------------------------------------------------------------
def test_cost_with_1000_notes_in_the_window():
    """The caller passes the whole window every call; a call takes only what is new. Over 1000 notes in WINDOW_S (with
    stops and replays): the median call and the 99th centile (calls that judge a stop) stay well under budget."""
    notes = practice_session(15, 700.0)
    notes = [(a * 0.4, q) for a, q in notes]                        # 2.5 times as dense
    t0 = 1000.0
    strikes = [(t0 + a, "voice", 1, q, 80) for a, q in notes]
    r = PracticeRead()
    times: list[float] = []
    a = b = 0
    t = 0.0
    n_max = 0
    while t <= notes[-1][0]:
        now = t0 + t
        while b < len(strikes) and strikes[b][0] <= now:
            b += 1
        while a < b and strikes[a][0] < now - WINDOW_S:
            a += 1
        win = strikes[a:b]
        n_max = max(n_max, len(win))
        s = time.perf_counter()
        r.update(win, now)
        times.append(time.perf_counter() - s)
        t += 0.5
    times.sort()
    assert n_max >= 1000
    assert times[len(times) // 2] < 0.0005, times[len(times) // 2]
    assert times[int(len(times) * 0.99)] < 0.003, times[-5:]


def test_window_constant_matches_fallback():
    assert practice.WINDOW_S == FALLBACK["window_s"] >= FALLBACK["replay_back_s"]
