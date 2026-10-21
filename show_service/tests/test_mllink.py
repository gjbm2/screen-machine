"""mllink (README S4.5): MLFeed into a real pipe (frames, the receiver's times through soxr, segments, GAP, a full
pipe, a dead reader, the cost), ``beat_vote`` on synthetic beats (each octave, a 3:2 tempo, bar and one; read where
the clock's line holds: a 2 % drifting line, the other phase, split, random and jump-cut windows, and 26's quickstep
window that failed read whole), MLLink's apply rule (agree_runs, cooldown, only what differs, an octave alone,
confirmations) on a fake clock, and MLLink
against ``python -m show_service.ml.worker --fake`` (sys.executable, a tmp models dir): hello, the feed attached, the
view filled, CPU read, a killed worker restarted, disabled by show.yaml, stop as brain.py awaits it.

Only processes the test made are signalled. No ports, no room API, never the real models dir."""
from __future__ import annotations

import asyncio
import inspect
import os
import signal
import sys
import time

import numpy as np
import pytest

from show_service import contract as C
from show_service import mllink as M
from show_service.ml import protocol as P

FS_IN = 48000


def drain(r):
    p, out = P.FrameParser(), []
    while True:
        try:
            data = os.read(r, 1 << 20)
        except BlockingIOError:
            break
        if not data:
            break
        out += list(p.feed(data))
    assert p.bad == 0
    return out


@pytest.fixture
def pipe():
    r, w = os.pipe()
    os.set_blocking(w, False)
    os.set_blocking(r, False)
    yield r, w
    for fd in (r, w):
        try:
            os.close(fd)
        except OSError:
            pass


def tmap(n, base=100.0, fs=FS_IN, skew_ppm=80.0):
    """The receiver's acoustic time of input sample n: a skewed line (a sender clock 80 ppm off)."""
    return base + n / fs * (1.0 + skew_ppm * 1e-6)


def feed_blocks(feed, seconds, *, fs=FS_IN, n_start=0, block=128, stream="room-mic", flags_at=None, f_hz=440.0,
                base=100.0, sink=None):
    """Blocks as the receiver delivers them; ``sink`` = (r, list): the pipe is read as it goes (a worker would)."""
    n = n_start
    end = n_start + int(seconds * fs)
    i = 0
    while n < end:
        i += 1
        if sink is not None and i % 20 == 0:
            sink[1].extend(drain(sink[0]))
        t = (np.arange(n, n + block) / fs).astype(np.float64)
        x = (0.5 * np.sin(2 * np.pi * f_hz * t)).astype(np.float32).reshape(-1, 1)
        fl = flags_at(n) if flags_at else 0
        if fl & C.AUDIO_GAP:
            x[:] = 0.0
        feed.write(stream, n, tmap(n, base, fs), x, fs, fl)
        n += block
    if sink is not None:
        sink[1].extend(drain(sink[0]))
    return n


def test_feed_frames_carry_the_receivers_times_through_soxr(pipe):
    r, w = pipe
    feed = M.MLFeed()
    feed_blocks(feed, 0.5)                                    # nothing attached: returns at once, nothing resampled
    assert feed.blocks == 0 and feed.stats()["frames"] == 0 and feed.stats()["attached"] is False
    feed.attach(w)
    out: list = []
    n = feed_blocks(feed, 3.0, n_start=24000, sink=(r, out))
    assert feed.stats()["drops"] == 0
    assert len(out) >= 55 and out[0].flags & C.ML_FLAG_SEGMENT and all(fr.flags == 0 for fr in out[1:])
    assert [fr.n0 for fr in out] == [800 * i for i in range(len(out))] and {fr.seg for fr in out} == {1}
    for fr in out:                                            # 16 kHz sample m = input sample n_start + 3 m
        assert abs(fr.t0 - tmap(24000 + 3 * fr.n0)) < 1e-4, (fr.n0, fr.t0 - tmap(24000 + 3 * fr.n0))
    # the audio is the sine, delay-compensated: frame k's samples are sin(2π f t) at their own times
    x = np.concatenate([fr.samples() for fr in out[5:15]])
    m0 = out[5].n0
    want = 0.5 * np.sin(2 * np.pi * 440.0 * (24000 + 3 * (m0 + np.arange(x.size))) / FS_IN)
    assert float(np.max(np.abs(x - want))) < 0.02
    # AUDIO_SEGMENT, then another rate: each starts a segment with its own time map
    out = []
    n = feed_blocks(feed, 0.2, n_start=n, flags_at=lambda k: C.AUDIO_SEGMENT if k == n else 0, sink=(r, out))
    feed_blocks(feed, 1.0, fs=44100, n_start=0, base=200.0, sink=(r, out))
    segs = [fr for fr in out if fr.flags & C.ML_FLAG_SEGMENT]
    assert [fr.seg for fr in segs] == [2, 3] and feed.rebuilds == 3
    last = [fr for fr in out if fr.seg == 3]
    for fr in last:
        assert abs(fr.t0 - tmap(fr.n0 * 44100 / 16000, 200.0, 44100)) < 1e-4


def test_feed_gap_blocks_go_through_as_zeros_and_flag_frames(pipe):
    r, w = pipe
    feed = M.MLFeed()
    feed.attach(w)
    gap = (48000, 48000 + 9600)                               # 0.2 s of zero-fill a second in
    out: list = []
    feed_blocks(feed, 2.0, flags_at=lambda k: C.AUDIO_GAP if gap[0] <= k < gap[1] else 0, sink=(r, out))
    flagged = [fr for fr in out if fr.flags & C.ML_FLAG_GAP]
    assert flagged and all(abs(fr.t0 - tmap(3 * fr.n0)) < 1e-4 for fr in out)    # the index stays continuous
    lo, hi = gap[0] / 3, gap[1] / 3
    for fr in out:
        overlaps = fr.n0 < hi + 40 and fr.n0 + 800 > lo - 40   # soxr's filter spreads the edge a few samples
        if fr.flags & C.ML_FLAG_GAP:
            assert overlaps
        elif lo <= fr.n0 and fr.n0 + 800 <= hi:
            raise AssertionError("a frame inside the gap isn't flagged")


def test_feed_full_pipe_drops_counts_and_flags_after_drop(pipe):
    r, w = pipe
    feed = M.MLFeed()
    feed.attach(w)
    feed_blocks(feed, 30.0)                                    # 30 s at 16 kHz is ~1.9 MB: far over a 64 KB pipe
    st = feed.stats()
    assert st["drops"] > 0 and st["frames"] > 0
    out = drain(r)
    feed_blocks(feed, 0.2, n_start=30 * FS_IN)
    after = drain(r)
    assert after and after[0].flags & C.ML_FLAG_AFTER_DROP and after[0].dropped == st["drops"]
    assert after[0].n0 > out[-1].n0 + 800                      # n0 counted the dropped frames


def test_feed_dead_reader_forgets_the_fd():
    r, w = os.pipe()
    os.set_blocking(w, False)
    os.close(r)
    feed = M.MLFeed()
    feed.attach(w)
    old = signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    try:
        feed_blocks(feed, 0.5)
    finally:
        signal.signal(signal.SIGPIPE, old)
        os.close(w)
    st = feed.stats()
    assert st["broken"] == 1 and st["attached"] is False


def test_feed_cost(pipe):
    """README S4.5: <= 40 µs p95 per 128-frame 48 kHz block, frame writes included (printed with -s)."""
    r, w = pipe
    feed = M.MLFeed()
    feed.attach(w)
    x = (0.3 * np.random.default_rng(1).standard_normal((128, 1))).astype(np.float32)
    costs = []
    n = 0
    for i in range(6000):
        t = time.perf_counter_ns()
        feed.write("room-mic", n, tmap(n), x, FS_IN, 0)
        costs.append((time.perf_counter_ns() - t) / 1000.0)
        n += 128
        if i % 40 == 0:
            drain(r)
    p50, p95 = np.percentile(costs[500:], [50, 95])
    print(f"\nMLFeed.write: p50 {p50:.1f} µs, p95 {p95:.1f} µs per 128-frame 48 kHz block (frames included); "
          f"its own stats: {feed.stats()['block_us_p50']} / {feed.stats()['block_us_p95']} µs")
    assert feed.stats()["drops"] == 0
    assert p95 < 200.0                                         # 40 µs is the budget unloaded; the host renders 4K


# -- beat votes --------------------------------------------------------------------------------------------------------
CLOCK = {"bpm": 120.0, "anchor_ms": 100000.0, "beat_at_anchor": 200, "beats_per_bar": 4, "bar_anchor_beat": 200,
         "phrase_bars": 8, "conf": 0.9, "bar_conf": 0.6, "phrase_conf": 0.5, "state": "locked", "gate": "beat",
         "epoch": 3, "stability": 0.9, "source": "madmom", "t_ms": 100000.0}


def t_of(k, clock=CLOCK):
    return clock["anchor_ms"] / 1000.0 + (k - clock["beat_at_anchor"]) * 60.0 / clock["bpm"]


def beats_msg(beats, downbeats=(), bpm=None, bar_len=None, w=(90.0, 120.0)):
    beats = sorted(beats)
    if bpm is None and len(beats) > 1:
        bpm = 60.0 / float(np.median(np.diff(beats)))
    return {"type": "beats", "window_t0": w[0], "window_t1": w[1], "interior": [w[0] + 3, w[1] - 3],
            "stream": "room-mic", "seg": 1, "beats": list(beats), "downbeats": sorted(downbeats), "bpm": bpm,
            "bar_len": bar_len, "model": "beat-this-small0", "cost_ms": 1.0}


def test_beat_vote_octave_one_with_the_one_and_the_bar():
    ks = range(180, 230)
    msg = beats_msg([t_of(k) + 0.01 for k in ks], [t_of(k) + 0.01 for k in ks if k % 4 == 2], bar_len=4)
    v = M.beat_vote(msg, CLOCK, {})
    assert v["octave"] == 1.0 and v["conf"] == 1.0 and v["why"] is None and v["bar_len"] == 4
    assert v["beat_is_one"] == max(k for k in ks if k % 4 == 2) and v["applied"] is False
    v3 = M.beat_vote(beats_msg([t_of(k) for k in ks], [t_of(k) for k in ks if k % 3 == 0], bar_len=3), CLOCK, {})
    assert v3["bar_len"] == 3 and v3["beat_is_one"] % 3 == 0
    v7 = M.beat_vote(beats_msg([t_of(k) for k in ks], [t_of(k) for k in ks if k % 7 == 0], bar_len=7), CLOCK, {})
    assert v7["bar_len"] is None                              # 7 isn't in bar_lens: the clock's bar is used to vote


def test_beat_vote_octaves_and_unrelated_tempi():
    halves = [t_of(180) + i * 0.25 for i in range(100)]       # Beat This! hears twice our tempo
    v = M.beat_vote(beats_msg(halves), CLOCK, {})
    assert v["octave"] == 2.0 and v["conf"] == 1.0 and v["beat_is_one"] is None
    doubles = [t_of(180) + i * 1.0 for i in range(25)]
    v = M.beat_vote(beats_msg(doubles), CLOCK, {})
    assert v["octave"] == 0.5 and v["conf"] == 1.0
    three_two = [t_of(180) + i / 3.0 for i in range(60)]      # 180 against 120 bpm: 3:2, no vote
    v = M.beat_vote(beats_msg(three_two), CLOCK, {})
    assert v["octave"] is None and v["why"] == "the tempi don't relate"
    off = [t_of(k) + 0.2 for k in range(180, 230)]            # right tempo, wrong phase: no agreement
    assert M.beat_vote(beats_msg(off), CLOCK, {})["conf"] == 0.0
    assert M.beat_vote(beats_msg(off), None, {})["why"] == "no grid"
    assert M.beat_vote(beats_msg(off), {**CLOCK, "gate": "none"}, {})["why"] == "no grid"
    assert M.beat_vote(beats_msg(off[:3]), CLOCK, {})["why"] == "too few beats"


def whole_window_conf(msg, clock, match_ms=70.0, tol=0.06):
    """The vote's conf before 21 Oct: every beat of the window against the clock's current line carried back at its
    own bpm (what 26 measured failing on the quickstep), for the regression below."""
    bpm = clock["bpm"]
    r = (60.0 / bpm) / (60.0 / msg["bpm"])
    octave = next(o for o in (2.0, 0.5, 1.0) if abs(r / o - 1.0) <= tol)
    hits = 0
    for b in msg["beats"]:
        x = (clock["beat_at_anchor"] + (b * 1000.0 - clock["anchor_ms"]) * bpm / 60000.0) * octave
        hits += abs(x - round(x)) * 60.0 / bpm / octave <= match_ms / 1000.0
    return hits / len(msg["beats"])


def drifting_x2(drift=0.02, true_bpm=203.0, jitter_s=0.008, phase_s=0.0, seed=4):
    """Beat This! at a true 203 bpm over a 30 s window (its interior's beats, 8 ms jitter); the clock locked at the
    half, its line anchored on a true beat 1.5 s after the window (a vote arrives ~3.7 s after it), its bpm ``drift``
    off: carried back 30 s that line walks 0.6 s off the beats."""
    rng = np.random.default_rng(seed)
    T = 60.0 / true_bpm
    w = (1000.0, 1030.0)
    beats = [w[0] + 3.0 + phase_s + i * T + float(rng.normal(0.0, jitter_s)) for i in range(int(24.0 / T))]
    t_anchor = w[0] + 3.0 + 2 * round((w[1] + 1.5 - w[0] - 3.0) / (2 * T)) * T    # a true beat the half-clock has
    clock = {**CLOCK, "bpm": true_bpm / 2 * (1.0 + drift), "anchor_ms": t_anchor * 1000.0, "beat_at_anchor": 400,
             "bar_anchor_beat": 396}
    return beats_msg(beats, w=w), clock


def test_beat_vote_reads_where_the_clocks_line_holds_a_consistent_x2_reaches_full_agreement():
    """README S4.5 "Beat votes": a consistent x2 window against a half-tempo clock whose line is 2 % off the music's
    tempo (the tracker's local fit, a slew in force) reaches conf ~1 at octave 2; read over the whole window the old
    way it doesn't."""
    for drift in (0.02, -0.02, 0.0):
        msg, clock = drifting_x2(drift)
        v = M.beat_vote(msg, clock, {})
        assert v["octave"] == 2.0 and v["conf"] >= 0.9 and v["why"] is None, (drift, v)
        if drift:
            assert whole_window_conf(msg, clock) < 0.6, drift
    # Beat This! on the other phase (its beats on the half-clock's off-beats' midpoints): no agreement at x2
    msg, clock = drifting_x2(0.02, phase_s=60.0 / 203.0 / 2)
    v = M.beat_vote(msg, clock, {})
    assert v["octave"] == 2.0 and v["conf"] < 0.6
    # only the last local_s count: a window whose newest 10 s moved off the clock's phase disagrees
    msg, clock = drifting_x2(0.0)
    late = [b + (0.12 if b >= msg["interior"][1] - 10.0 else 0.0) for b in msg["beats"]]
    assert M.beat_vote(beats_msg(late, w=(1000.0, 1030.0)), clock, {})["conf"] < 0.6
    assert M.beat_vote(beats_msg(late, w=(1000.0, 1030.0)), clock, {"local_s": 30})["conf"] < 0.9


def test_beat_vote_split_or_garbage_windows_stay_below_min_conf():
    msg, clock = drifting_x2(0.0)
    t0, t1 = msg["interior"]
    split = ([t0 + i * 60.0 / 176.0 for i in range(int(19.0 * 176 / 60))]          # Beat This! splits: 176 bpm,
             + [t0 + 19.0 + i * 0.64 for i in range(int(5.0 / 0.64))])            # then 93.75 (26's reggae)
    v = M.beat_vote(beats_msg(split, w=(1000.0, 1030.0)), clock, {})
    assert v["conf"] < 0.6 or v["octave"] is None, v
    rng = np.random.default_rng(8)
    for _ in range(20):                                       # random onsets: never a vote that passes
        junk = sorted(float(x) for x in rng.uniform(t0, t1, int(rng.integers(20, 90))))
        v = M.beat_vote(beats_msg(junk, w=(1000.0, 1030.0)), clock, {})
        assert v["conf"] < 0.6 or v["octave"] is None, v
    n1, T = int(17.0 * 203 / 60), 60.0 / 203.0                 # a jump cut 7 s from the end: the phase jumps 0.13 s
    seam = [t0 + i * T for i in range(n1)] + [t0 + (n1 + i) * T + 0.13 for i in range(int(7.0 / T))]
    v = M.beat_vote(beats_msg(seam, w=(1000.0, 1030.0)), clock, {})
    assert v["conf"] < 0.6, v


#: 26's live failure (21 Oct, m06-quickstep, the NEEWER mic, replayed; mllink's turn at +25.7 s): Beat This! 200 bpm
#: over the whole window, the clock locked at the half (102.36 published, its line anchored 0.3 s before the vote).
#: Times from the music's start (+1000 s).
QS_BEATS = [0.03, 0.33, 0.61, 0.91, 1.15, 1.49, 1.77, 2.09, 2.35, 2.67, 2.97, 3.27, 3.55, 3.85, 4.13, 4.45, 4.75, 5.05,
            5.33, 5.63, 5.91, 6.21, 6.49, 6.79, 7.09, 7.39, 7.67, 7.97, 8.25, 8.55, 8.85, 9.15, 9.43, 9.73, 10.03,
            10.33, 10.61, 10.93, 11.21, 11.51, 11.79, 12.11, 12.39, 12.69, 12.97, 13.29, 13.57, 13.87, 14.17, 14.47,
            14.77, 15.07, 15.37, 15.65, 15.95, 16.25, 16.53, 16.83, 17.11, 17.43, 17.71, 18.01, 18.29, 18.59, 18.89]
QS_DOWN = [0.33, 0.91, 1.15, 1.49, 1.77, 2.67, 3.27, 3.85, 5.05, 6.21, 7.39, 8.55, 9.73, 10.93, 12.11, 13.29, 14.47,
           15.65, 16.83, 17.43, 18.01, 18.59]
QS_CLOCK = {**CLOCK, "bpm": 102.359486, "anchor_ms": (1000.0 + 25.397926) * 1000.0, "beat_at_anchor": 65,
            "bar_anchor_beat": 51, "bar_conf": 0.1, "conf": 0.9143, "epoch": 2}


def test_beat_vote_26s_quickstep_window_the_old_failure_and_the_fix():
    msg = {**beats_msg([1000.0 + b for b in QS_BEATS], [1000.0 + d for d in QS_DOWN], bpm=200.0, bar_len=4,
                       w=(1000.0 - 8.05, 1000.0 + 21.9499))}
    msg["interior"] = [1000.0 - 5.05, 1000.0 + 18.9499]
    assert whole_window_conf(msg, QS_CLOCK) == pytest.approx(0.154, abs=0.01)   # 26's 0.154: "low agreement"
    v = M.beat_vote(msg, QS_CLOCK, {})
    assert v["octave"] == 2.0 and v["conf"] >= 0.9 and v["beat_is_one"] is None and v["why"] is None


class Link:
    """MLLink with only what _vote and _apply touch: a fake clock and a recording clock_correct."""

    def __init__(self, clock=CLOCK, **cfg):
        self.calls = []
        self.t = 1000.0
        ctx = C.BrainContext(publish=lambda m: None, clock=lambda: self.clock, clock_correct=self._corr)
        self.clock = dict(clock)
        self.link = M.MLLink(ctx, mono=lambda: self.t)
        self.link.configure({"ml": {"beat_votes": cfg}} if cfg else {})

    def _corr(self, **kw):
        self.calls.append(kw)

    def vote(self, msg):
        return self.link._vote(msg)


def one_two_msg(one_res=2):
    ks = range(180, 230)
    return beats_msg([t_of(k) for k in ks], [t_of(k) for k in ks if k % 4 == one_res], bar_len=4)


def test_apply_needs_agree_runs_then_sends_only_what_differs_then_cooldown():
    L = Link()
    a = L.vote(one_two_msg())
    assert a["why"] == "waiting for a second run" and not a["applied"] and L.calls == []
    b = L.vote(one_two_msg())
    assert b["applied"] and L.calls == [{"beat_is_one": b["beat_is_one"]}]       # the bar length agrees: left out
    L.t += 5
    assert L.vote(one_two_msg())["why"] == "waiting for a second run"           # a new run after an apply
    c = L.vote(one_two_msg())
    assert c["why"] == "cooldown" and len(L.calls) == 1
    L.t += 31                                                 # the run held through the cooldown: at once
    assert L.vote(one_two_msg())["applied"] and len(L.calls) == 2


def test_apply_octave_alone_and_low_agreement():
    L = Link(cooldown_s=0)
    halves = beats_msg([t_of(180) + i * 0.25 for i in range(100)], [t_of(180)], bar_len=3)
    L.vote(halves)
    assert L.vote(halves)["applied"] and L.calls == [{"octave": 2.0}]           # never with bar or one
    off = beats_msg([t_of(k) + (0.2 if k >= 214 else 0.0) for k in range(180, 230)])  # the local span off the grid
    v = L.vote(off)
    assert v["why"] == "low agreement" and len(L.calls) == 1


def test_apply_a_new_bar_length_carries_its_one():
    L = Link()
    ks = range(180, 230)
    msg = beats_msg([t_of(k) for k in ks], [t_of(k) for k in ks if k % 3 == 1], bar_len=3)
    for _ in range(3):                                        # the meter needs bar_agree_runs (4) runs, not 2
        v = L.vote(msg)
        assert not v["applied"] and v["why"] == "waiting for 4 runs (a new bar length)"
    v = L.vote(msg)
    assert v["applied"] and L.calls == [{"bar_len": 3, "beat_is_one": v["beat_is_one"]}]


def test_a_new_bar_length_needs_a_one():
    L = Link()
    ks = range(180, 230)
    no_one = beats_msg([t_of(k) for k in ks], [t_of(k) + 0.2 for k in ks if k % 3 == 1], bar_len=3)  # off the grid
    for _ in range(6):
        v = L.vote(no_one)
        assert v["beat_is_one"] is None and v["bar_len"] == 3 and not v["applied"]
    assert v["why"] == "a new bar length needs a one" and L.calls == []


def test_an_agreeing_vote_is_sent_as_a_confirmation_at_most_every_cooldown():
    L = Link()
    agree = one_two_msg(one_res=0)                           # the clock's own bar (anchor 200 = 0 mod 4)
    L.vote(agree)
    v = L.vote(agree)
    assert v["why"] == "the clock agrees" and v["applied"]
    assert L.calls == [{"beat_is_one": v["beat_is_one"], "bar_len": 4, "octave": 1.0}]
    L.vote(agree)
    L.vote(agree)
    assert len(L.calls) == 1                                  # within cooldown_s: nothing more
    assert L.link.confirmed == 1 and L.link.applied == 0


def test_configure_problems_keep_the_starting_points():
    L = Link()
    L.link.configure({"ml": {"beat_votes": {"min_conf": "most", "bar_lens": [1, 4], "agree_runs": 0},
                             "results_stale_s": -1}})
    probs = L.link.snapshot()["problems"]
    assert len(probs) == 4 and L.link.votes_cfg == M.BEAT_VOTES_FALLBACK and L.link.stale_s == 3.0
    L.link.configure({"ml": "yes"})
    assert L.link.snapshot()["problems"] == ["ml: 'yes' is not a section: the starting points are used"]


def test_freshness_follows_the_workers_intervals():
    L = Link()
    L.link.configure({"ml": {"results_stale_s": 3, "stale_periods": 1.5,
                             "tasks": {"ced": {"every_s": 1}, "tags": {"every_s": 1}, "key": {"every_s": 2},
                                       "beats": {"every_s": 10}}}})
    assert L.link.view()["stale_s"] == {"ced": 3.0, "tags": 3.0, "key": 3.0, "beats": 15.0}
    stats = {"type": "stats", "t": 0.0, "cpu_share": 0.3, "stretch": 4.0,
             "tasks": {"ced": {"every_s": 4.0}, "tags": {"every_s": 8.0}, "key": {"every_s": 8.0},
                       "beats": {"every_s": 40.0}}}
    L.link._on_line(stats)                                    # the CPU guard stretched every interval x4
    assert L.link.view()["stale_s"] == {"ced": 6.0, "tags": 12.0, "key": 12.0, "beats": 60.0}
    assert C.ml_stale_s(L.link.view(), "ced", 3.0) == 6.0 and C.ml_stale_s(None, "ced", 3.0) == 3.0
    L.link._on_state("backoff", "restarting in 2 s")          # the neutral view keeps the numbers
    assert L.link.view()["stale_s"]["tags"] == 12.0 and L.link.snapshot()["stale_s"]["tags"] == 12.0


def test_configure_keeps_the_last_good_worker_section_and_never_raises():
    L = Link()
    sent = []
    L.link.sup.configure = lambda ml, worker=None: sent.append(worker)
    L.link.configure({"ml": {"cpu_max": 0.3, "silence_dbfs": -70, "python": "/x/python", "beat_votes": {},
                             "restart": {"stall_s": 30}}})
    assert sent[-1] == {"cpu_max": 0.3, "silence_dbfs": -70}                  # only the keys the worker reads
    L.link.configure({"ml": {"cpu_max": float("nan"), "silence_dbfs": -70, "restart": {"stall_s": "x",
                                                                                      "backoff_s": [0]}}})
    probs = L.link.snapshot()["problems"]
    assert sent[-1] == {"cpu_max": 0.3, "silence_dbfs": -70}                  # the last good one stays
    assert any("can't be sent" in p for p in probs) and any("stall_s" in p for p in probs)
    assert any("backoff_s" in p for p in probs)
    L.link.configure({"ml": {"tasks": {"ced": {"note": "y" * 5000}}}})        # too big for one control frame
    assert any("can't be sent" in p for p in L.link.snapshot()["problems"])


def test_the_owners_0_8_cuts_the_workers_cpu_max_and_gives_it_back():
    L = Link()
    sent = []
    L.link.sup.send_config = lambda worker: sent.append(worker)
    L.link.sup.pid = 4242
    L.link.configure({"ml": {"cpu_max": 0.35, "cpu_floor": 0.1}})
    hist = L.link._cpu_hist

    def run(seconds, service, worker, t0):
        s0 = hist[-1][1] if hist else 0.0
        w0 = hist[-1][3] if hist else 0.0
        for k in range(1, int(seconds / 10) + 1):
            hist.append((t0 + 10 * k, s0 + service * 10 * k, 4242, w0 + worker * 10 * k))
            L.link._cpu_limit(t0 + 10 * k)
        return t0 + seconds
    t = run(60, 0.45, 0.30, 0.0)                              # 0.75 in all: under 0.8, nothing changes
    assert sent == [] and L.link.cpu_limit is None
    t = run(70, 0.60, 0.30, t)                                # 0.90 for 60 s: the worker gets 0.8 - 0.6
    assert L.link.cpu_limit == pytest.approx(0.2, abs=0.03) and sent[-1]["cpu_max"] == pytest.approx(0.2, abs=0.03)
    L.link._build_snapshot()
    assert L.link.snapshot()["cpu_limit"]["cut"] is True
    t = run(70, 0.75, 0.10, t)                                # show_service alone near 0.8: never under the floor
    assert L.link.cpu_limit == pytest.approx(0.1)
    t = run(80, 0.40, 0.10, t)                                # room again: back to ml.cpu_max
    L.link._build_snapshot()
    assert L.link.cpu_limit is None and sent[-1]["cpu_max"] == 0.35 and L.link.snapshot()["cpu_limit"]["cut"] is False


def test_the_beat_process_counts_in_show_services_share(monkeypatch):
    """child_pids (the clock's beat process) adds to cpu.service; a respawned child counts from its first reading,
    one that is down (None) counts nothing, and the share never goes negative."""
    os_pid = M.os.getpid()
    cpu = {os_pid: 100.0, 501: 5.0}
    monkeypatch.setattr(M, "_proc_cpu_s", lambda pid: cpu.get(pid))
    kids = [501]
    ctx = C.BrainContext(publish=lambda m: None)
    link = M.MLLink(ctx, mono=lambda: 0.0, child_pids=lambda: list(kids))
    link._measure_cpu(0.0)
    cpu[os_pid], cpu[501] = 104.0, 7.0                      # 0.4 core own + 0.2 core the beat child over 10 s
    link._measure_cpu(10.0)
    assert link.cpu["service"] == pytest.approx(0.6) and link.cpu["children"] == pytest.approx(0.2)
    kids[:] = [777]                                         # respawned: the new pid counts from its first reading
    cpu[os_pid], cpu[777] = 108.0, 0.5
    link._measure_cpu(20.0)
    assert link.cpu["service"] == pytest.approx(0.4) and link.cpu["children"] == pytest.approx(0.0)
    cpu[os_pid], cpu[777] = 112.0, 1.5
    link._measure_cpu(30.0)
    assert link.cpu["service"] == pytest.approx(0.5) and link.cpu["children"] == pytest.approx(0.1)
    kids[:] = [None]                                        # down
    cpu[os_pid] = 116.0
    link._measure_cpu(40.0)
    assert link.cpu["service"] == pytest.approx(0.4) and link.cpu["children"] == pytest.approx(0.0)
    hist = [h[1] for h in link._cpu_hist]
    assert hist == sorted(hist)                             # cumulative: the 60 s share never sees a drop


# -- MLLink against the fake worker --------------------------------------------------------------------------------------
def ml_cfg(tmp_path, **kw):
    cfg = {"enabled": True, "models_dir": str(tmp_path / "models"), "nice": 19,
           "restart": {"backoff_s": [1, 1], "healthy_s": 600, "stall_s": 5},
           "tasks": {"key": {"window_s": 3}, "beats": {"every_s": 2, "window_s": 6, "interior_margin_s": 1}}}
    cfg.update(kw)
    return cfg


async def until(cond, timeout, step=0.05, during=None):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if during is not None:
            during()
        if cond():
            return True
        await asyncio.sleep(step)
    return cond()


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class Feeder:
    """show-rx's part, on the test's loop: 50 ms blocks of a 120 bpm click + tone in real time, with the receiver's
    times (time.monotonic, as the acoustic map would give)."""

    def __init__(self, feed):
        self.feed, self.n, self.t0 = feed, 0, time.monotonic()

    def __call__(self):
        due = int((time.monotonic() - self.t0) * FS_IN)
        while self.n + 2400 <= due:
            t = np.arange(self.n, self.n + 2400) / FS_IN
            x = 0.2 * np.sin(2 * np.pi * 330 * t) + 0.6 * (np.mod(t, 0.5) < 0.015) * np.sin(2 * np.pi * 60 * t)
            self.feed.write("room-mic", self.n, self.t0 + self.n / FS_IN, x.astype(np.float32).reshape(-1, 1),
                            FS_IN, C.AUDIO_SEGMENT if self.n == 0 else 0)
            self.n += 2400


def test_mllink_runs_the_fake_worker_fills_the_view_restarts_and_stops(tmp_path):
    (tmp_path / "models").mkdir()
    clock = dict(CLOCK, anchor_ms=time.monotonic() * 1000.0, beat_at_anchor=0)
    calls = []

    async def main():
        feed = M.MLFeed()
        ctx = C.BrainContext(publish=lambda m: None, ml_feed=feed, clock=lambda: clock,
                             clock_correct=lambda **kw: calls.append(kw))
        link = M.attach(ctx, log_path=tmp_path / "ml-worker.log", python=sys.executable, extra_args=("--fake",),
                        cpu_every_s=1.0)
        assert link.provides() == {"ml": link.view} and link.view()["state"] == "starting"
        link.configure({"ml": ml_cfg(tmp_path)})
        link.start()
        assert await until(lambda: link.view()["state"] == "running", 15), link.snapshot()
        hello = link.view()["hello"]
        assert set(hello["models"].values()) == {"fake"} and hello["nice"] >= 19
        feeder = Feeder(feed)
        assert await until(lambda: feed.stats()["attached"], 2, during=feeder)

        def tick():
            feeder()
            link.tick(time.monotonic())
        assert await until(lambda: len(link.view()["emb_recent"]) >= 3 and link.view()["key"] is not None
                           and link.view()["beats"] is not None, 20, during=tick), link.snapshot()
        v = link.view()
        assert v["stream"] == "room-mic" and v["ced"]["stream"] == "room-mic" and len(v["ced_recent"]) >= 3
        emb_id, t, emb = v["emb_recent"][-1]
        assert emb.dtype == np.float32 and emb.shape == (C.EMB_DIM,) and abs(t - v["tags"]["t"]) < 1e-9
        assert v["vote"] is not None and set(v["vote"]) == set(C.BeatVote.__annotations__)
        assert all(a is not None and a < 15 for k, a in v["ages"].items() if k != "beats"), v["ages"]
        snap = link.snapshot()
        assert snap["state"] == "running" and snap["bad_lines"] == 0 and snap["feed"]["frames"] > 0
        assert snap["feed"]["drops"] == 0 and snap["votes"] and snap["problems"] == []
        assert await until(lambda: link.snapshot()["cpu"]["worker"] is not None, 4, during=tick), link.snapshot()
        cpu = link.snapshot()["cpu"]
        assert cpu["total"] == pytest.approx(cpu["worker"] + cpu["service"], abs=2e-3)

        first = link.sup.pid
        os.kill(first, signal.SIGKILL)                        # our own child
        assert await until(lambda: link.view()["state"] == "backoff", 5, during=tick), link.snapshot()
        assert link.view()["ced_recent"] == [] and link.view()["hello"] is None    # readers degrade at once
        assert await until(lambda: link.view()["state"] == "running" and link.sup.pid not in (None, first), 15,
                           during=tick), link.snapshot()
        assert await until(lambda: link.view()["ced"] is not None, 6, during=tick)

        link.configure({"ml": ml_cfg(tmp_path, enabled=False)})            # show.yaml hot reload: stop it
        second = link.sup.pid
        assert await until(lambda: link.view()["state"] == "disabled", 10, during=tick)
        assert not alive(second) and not feed.stats()["attached"]
        link.configure({"ml": ml_cfg(tmp_path)})
        assert await until(lambda: link.view()["state"] == "running", 15, during=tick)
        third = link.sup.pid
        got = link.stop()
        assert inspect.isawaitable(got)                       # brain.py awaits it (STOP_AWAIT_S)
        await asyncio.wait_for(got, 6)
        assert link.view()["state"] == "disabled" and not alive(third)
        assert link.sup.last_exit["code"] == 0
    asyncio.run(main())


def test_mllink_missing_venv_degrades(tmp_path):
    async def main():
        ctx = C.BrainContext(publish=lambda m: None, ml_feed=M.MLFeed())
        link = M.attach(ctx, log_path=tmp_path / "w.log", python=str(tmp_path / "no-venv" / "python"))
        link.configure({"ml": ml_cfg(tmp_path)})
        link.start()
        assert await until(lambda: link.view()["state"] == "failed", 5)
        assert link.view()["why"] == "the ML venv is missing" and link.view()["ced"] is None
        link.tick(time.monotonic())
        assert link.snapshot()["next_start_in_s"] is not None
        await asyncio.wait_for(link.stop(), 3)
    asyncio.run(main())
