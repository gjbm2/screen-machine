"""clock.py's show-beat thread (README S4.6) and the slow lane's integer corrections (§5.3). Both are shared plumbing,
so they get real tests:
- the hand-off: activations computed on show-beat, frames run on show-rx in order with the inline times, and the
  threaded clock identical to the inline one on the existing loops;
- back-pressure (a stalled activation never stalls show-rx; drops, a reset before the splice, recovery), segment
  resets (stale results dropped), activation errors, stop(), a dropped Clock, a dead show-beat (inline again);
- corrections: checked, gated (no grid, stale, superseded), switched at the bar line under the new hypothesis
  (bar length and one, octave 2 and 0.5), never a phase or a tempo, expired rather than applied off a bar line;
- the measurement (``-s`` prints it): show-rx's block cost and latency, inline vs threaded, paced in real time on
  this host (with the 4K render running). ``SHOW_CLOCK_MEASURE_S`` sets the seconds per mode (default 6).
The hand-off tests run on the default (dbn) estimator; those that compare the legacy estimator's internals
(``_line``, its counts, its corrections' bookkeeping) build ``estimator="legacy"`` (directly or through
test_clock's Run and loop_clock). tests/test_beatdbn.py has the dbn ones.
"""

from __future__ import annotations

import gc
import math
import os
import threading
import time

import numpy as np
import pytest

from show_service import clock as C
from show_service.contract import AUDIO_SEGMENT
from show_service.tests import test_clock as TC

FS = TC.FS
T0 = TC.T0


class Recorder:
    """A cheap activation that logs what is done with it and on which thread. ``gate`` (an Event) holds every
    process_hop until it is set (a stalled show-beat)."""
    name = "rec"
    lag_s = 0.0
    floor = 0.0

    def __init__(self, gate: threading.Event | None = None) -> None:
        self.log: list[tuple] = []
        self.gate = gate
        self.n = 0

    def process_hop(self, hop: np.ndarray) -> float:
        if self.gate is not None:
            self.gate.wait(20.0)
        self.n += 1
        self.log.append(("hop", threading.current_thread().name, float(hop[0])))
        return 0.1 * (self.n % 7)

    def reset(self) -> None:
        self.log.append(("reset", threading.current_thread().name))


def feed(clk: C.Clock, x: np.ndarray, t_start: float = T0, n_start: int = 0, block: int = 256,
         first_flags: int = AUDIO_SEGMENT, wait: bool = True) -> list[float]:
    """Receiver blocks plus 10 ms ticks; returns each process() call's wall time (ms)."""
    ms = []
    nt = t_start
    for i, n0 in enumerate(range(0, len(x), block)):
        blk = x[n0:n0 + block].reshape(-1, 1)
        t0 = t_start + n0 / FS
        c0 = time.perf_counter()
        clk.process("room-mic", n_start + n0, t0, blk, FS, first_flags if i == 0 else 0)
        ms.append((time.perf_counter() - c0) * 1000.0)
        if wait:
            clk.wait_beat()
        while nt <= t0 + blk.shape[0] / FS:
            clk.tick(nt)
            nt += 0.01
    return ms


def settle(clk: C.Clock, t: float) -> None:
    """Let show-beat finish and show-rx drain it."""
    assert clk.wait_beat(10.0)
    clk.tick(t)


def loop(secs: float) -> np.ndarray:
    return TC.finish(TC.kick_loop(TC.grid(120.0, 0.3, secs - 0.2), secs))


def spy_frames(clk: C.Clock) -> list[tuple]:
    """Record every frame show-rx runs: (a, t_raw, thread name)."""
    rec: list[tuple] = []
    orig = clk._frame

    def frame(a, t_raw):
        rec.append((a, t_raw, threading.current_thread().name))
        return orig(a, t_raw)                           # (the dbn flags ride the clock, set just before)

    clk._frame = frame
    return rec


# -- the hand-off ------------------------------------------------------------------------------------------------------
def test_activation_runs_on_show_beat_and_frames_come_back_in_order_with_the_inline_times():
    x = loop(3.0)
    runs = {}
    for threaded in (False, True):
        act = Recorder()
        clk = C.Clock(None, activation=act, beat_thread=threaded)
        rec = spy_frames(clk)
        feed(clk, x)
        settle(clk, T0 + 3.0)
        runs[threaded] = (clk, act, rec)
    (ci, ai, ri), (ct, at, rt) = runs[False], runs[True]
    assert len(rt) == len(ri) > 250
    assert [(a, t) for a, t, _ in rt] == [(a, t) for a, t, _ in ri]       # same frames, same times, same order
    assert all(t2 > t1 for (_, t1, _), (_, t2, _) in zip(rt, rt[1:]))
    assert {n for _, _, n in rt} == {threading.current_thread().name}     # clock state stays on "show-rx"
    assert {e[1] for e in at.log} == {"show-beat"}                       # the activation never leaves show-beat
    assert {e[1] for e in ai.log} == {threading.current_thread().name}
    st = ct.stats()
    bt = st["beat_thread"]
    assert bt["on"] and bt["queued"] == 0 and bt["drops"] == 0 and bt["act_ms_p95"] > 0.0
    assert st.get("beat_order", 0) == 0 and st.get("beat_stale", 0) == 0 and st["errors"] == 0
    assert st["frames"] == ci.stats()["frames"]
    assert not ci.stats()["beat_thread"]["on"]
    ct.stop()


def _line(clk: C.Clock) -> tuple:
    return (clk._pub_k, clk._pub_t, clk._pub_bpm, clk._bpb, clk._bar_anchor, clk._phrase_bars, clk._epoch,
            clk._state, clk._gate, round(clk._conf, 9))


@pytest.mark.parametrize("source", TC.SOURCES)
@pytest.mark.parametrize("signal", ["lock", "breakdown"])
def test_the_threaded_clock_tracks_exactly_like_the_inline_one(source, signal):
    if signal == "lock":
        beats = TC.grid(120.0, 0.31, 15.8)
        x = np.zeros(int(16 * FS))
        for t in beats:
            TC.click(x, t)
            TC.drum(x, t, 0.5, "kick")
        x = TC.finish(x)
    else:
        x, _, _ = TC.breakdown_signal(silent=False)
    out = {}
    for threaded in (False, True):
        r = TC.Run(x, source, beat_thread=threaded)
        settle(r.clk, T0 + len(x) / FS)
        st = r.clk.stats()
        counts = {k: v for k, v in st.items() if isinstance(v, int) and k not in ("published", "epoch")}
        out[threaded] = (counts, _line(r.clk), r)
        r.clk.stop()
    assert out[True][0] == out[False][0]              # every count: frames, peaks, seeds, hits, misses, relocks ...
    assert out[True][1] == out[False][1]              # the same published line, bar and phrase, state, gate, conf
    assert out[True][2].pubs[-1]["state"] == "locked"


# -- back-pressure, resets, errors, stop -------------------------------------------------------------------------------
def test_a_stalled_activation_never_stalls_show_rx_and_the_clock_recovers():
    gate = threading.Event()
    act = Recorder(gate)
    clk = C.Clock(None, activation=act, beat_thread=True)
    x = loop(5.0)
    ms = feed(clk, x[:2 * FS], wait=False)             # show-beat is stuck on its first hop
    st = clk.stats()["beat_thread"]
    assert st["queued"] == C.BEAT_Q_MAX and clk._bt_put == C.BEAT_Q_MAX    # bounded: 0.5 s in flight ...
    hops = int(2 * FS * 44100 / 48000) // C.HOP
    assert hops - C.BEAT_Q_MAX - 2 <= st["drops"] <= hops - C.BEAT_Q_MAX     # ... the rest dropped
    assert max(ms) < 50.0                               # show-rx never waited for it
    gate.set()
    settle(clk, T0 + 2.0)
    assert clk.stats()["beat_thread"]["queued"] == 0
    feed(clk, x[2 * FS:], t_start=T0 + 2.0, n_start=2 * FS, first_flags=0)
    settle(clk, T0 + 5.0)
    kinds = [e[0] for e in act.log]
    # init + the first segment reset, the 50 hops in flight, then a reset before the first hop after the drops
    assert kinds[:2] == ["reset", "reset"] and kinds[2:2 + C.BEAT_Q_MAX] == ["hop"] * C.BEAT_Q_MAX
    assert kinds[2 + C.BEAT_Q_MAX] == "reset" and set(kinds[3 + C.BEAT_Q_MAX:]) == {"hop"}
    st = clk.stats()
    assert st["frames"] == kinds.count("hop") and st["errors"] == 0 and st["beat_thread"]["queued"] == 0
    clk.stop()


def test_a_new_segment_drops_the_old_segments_results_and_resets_first():
    gate = threading.Event()
    act = Recorder(gate)
    clk = C.Clock(None, activation=act, beat_thread=True)
    rec = spy_frames(clk)
    x = loop(2.0)
    feed(clk, x[:int(0.3 * FS)], wait=False)           # ~29 hops of segment 1 wait behind the stalled one
    put1 = clk._bt_put
    assert 25 <= put1 <= 30
    feed(clk, x[FS:FS + int(0.15 * FS)], t_start=T0 + 1.0, n_start=7 * FS, first_flags=AUDIO_SEGMENT, wait=False)
    assert clk.stats()["beat_thread"]["drops"] == 0
    gate.set()
    settle(clk, T0 + 2.0)
    st = clk.stats()
    assert st["beat_stale"] == put1                     # segment 1's results came back after the reset: dropped
    assert rec and all(t >= T0 + 1.0 for _, t, _ in rec)     # only segment 2's frames ran
    kinds = [e[0] for e in act.log]
    assert kinds[2:2 + put1] == ["hop"] * put1 and kinds[2 + put1] == "reset"
    assert st["errors"] == 0
    clk.stop()


def test_activation_errors_on_show_beat_are_counted_and_never_kill_it():
    clk = C.Clock(None, activation=TC.Boom(), beat_thread=True)
    feed(clk, TC.finish(np.zeros(FS)))
    settle(clk, T0 + 1.0)
    st = clk.stats()
    assert st["errors"] >= 90 and st["beat_thread"]["act_errors"] == st["errors"]
    assert st["beat_thread"]["on"] and st["beat_thread"]["queued"] == 0
    clk.stop()


def test_stop_joins_is_idempotent_and_audio_after_it_runs_inline():
    act = Recorder()
    clk = C.Clock(None, activation=act, beat_thread=True)
    x = loop(2.0)
    feed(clk, x[:FS])
    th = clk._bt.thread
    threading.Thread(target=clk.stop).start()           # any thread
    clk.stop(2.0)
    clk.stop()
    assert not th.is_alive()
    n_before = clk.stats()["frames"]
    feed(clk, x[FS:], t_start=T0 + 1.0, n_start=FS, first_flags=0)
    st = clk.stats()
    assert st["beat_inline_after_stop"] == 1 and st["frames"] > n_before + 80
    assert not st["beat_thread"]["on"] and st["errors"] == 0
    assert act.log[-1][1] == threading.current_thread().name


def test_a_dropped_clock_ends_its_show_beat():
    clk = C.Clock(None, activation=Recorder(), beat_thread=True)
    th = clk._bt.thread
    del clk
    gc.collect()
    th.join(2.0)
    assert not th.is_alive()


def test_a_dead_show_beat_falls_back_to_inline():
    act = Recorder()
    clk = C.Clock(None, activation=act, beat_thread=True)
    clk._bt.q.put(None)                                 # show-beat exits without stop(): as if it had died
    clk._bt.thread.join(2.0)
    feed(clk, loop(2.0), wait=False)
    st = clk.stats()
    assert st["beat_thread_died"] == 1 and not st["beat_thread"]["on"]
    assert st["frames"] > 100 and st["errors"] == 0     # the clock carries on, inline


# -- integer corrections -----------------------------------------------------------------------------------------------
def locked() -> tuple[C.Clock, list[dict]]:
    clk, pubs, _ = TC.loop_clock()
    return clk, pubs


def more(clk: C.Clock, t_from: float, secs: float) -> None:
    beats = [t - t_from for t in TC.grid(120.0, t_from + 0.3, t_from + secs)]
    feed(clk, TC.finish(TC.kick_loop(beats, secs)), t_start=T0 + t_from, n_start=int(t_from * FS), first_flags=0)


def test_corrections_must_be_whole_numbers_in_range():
    clk, pubs = locked()
    now = pubs[-1]["t_ms"] / 1000.0
    k = round(clk.beat_at(now))
    for args in ((k + 0.5, None, 1.0), (None, 0, 1.0), (None, 17, 1.0), (None, 1, 1.0), (None, 4.5, 1.0),
                 (None, None, 3.0), (None, None, 1.5), (None, None, "x"), (True, 4, 1.0), (math.nan, 4, 1.0),
                 (None, None, None)):
        clk.apply_integer_correction(*args)
    clk.tick(now + 0.01)
    st = clk.stats()
    assert st["corr_bad"] == 10 and st["corr_accepted"] == 1    # (None, None, None) is octave 1: a no-op vote
    clk.apply_integer_correction(np.int64(k - 4), np.int32(4), np.float64(1.0))     # numpy ints are fine
    clk.tick(now + 0.02)
    st = clk.stats()
    assert st["last_correction"]["result"] == "accepted" and st["corr_replaced"] == 1
    more(clk, 14.0, 3.0)
    d = clk.snapshot()
    assert d["beats_per_bar"] == 4 and (d["bar_anchor_beat"] - (k - 4)) % 4 == 0


def test_no_grid_no_correction():
    clk = C.Clock(None, activation="aubio", estimator="legacy")
    clk.apply_integer_correction(8, 4, 1.0)
    clk.tick(T0)
    assert clk.stats()["corr_no_grid"] == 1 and clk._pending is None
    clk.stop()


def test_bar_length_and_one_switch_at_the_new_bar_line_and_never_move_the_phase():
    clk, pubs = locked()
    before = dict(pubs[-1])
    now = before["t_ms"] / 1000.0
    one = round(clk.beat_at(now)) - 5                  # a downbeat from the window's interior (the past)
    th = threading.Thread(target=clk.apply_integer_correction, args=(one, 3))    # any thread
    th.start()
    th.join()
    more(clk, 14.0, 4.0)
    d = clk.snapshot()
    lc = clk.stats()["last_correction"]
    assert lc["result"] == "applied" and d["beats_per_bar"] == 3
    K = lc["at_beat"]
    assert (K - one) % 3 == 0 and K > round(C.beat_at(before, now))      # at the next bar line of the new bars
    assert (d["bar_anchor_beat"] - one) % 3 == 0
    assert d["bar_conf"] == pytest.approx(0.6)
    # integer only: the beat grid (tempo and phase) is where tracking had it, whatever the bars did
    for t in (now + 1.0, now + 3.0):
        assert abs(C.beat_at(d, t) - C.beat_at(before, t)) < 0.03
    assert d["epoch"] == before["epoch"]
    # the switch beat is where the published bars changed: no body before K's time says 3
    tk = d["anchor_ms"] / 1000.0 + (K - d["beat_at_anchor"]) * 60.0 / d["bpm"]
    assert all(p["beats_per_bar"] == 4 for p in clk_pubs_until(pubs, tk - 0.3))
    clk.stop()


def clk_pubs_until(pubs: list[dict], t: float) -> list[dict]:
    return [p for p in pubs if p["t_ms"] / 1000.0 <= t]


def test_octave_two_and_a_half_switch_at_bar_lines_keep_the_count_and_mark_a_break():
    clk, pubs = locked()
    seen: list[tuple] = []                              # (upcoming beat, bar anchor, factor) at each octave switch
    orig = clk._apply_octave

    def spy(f, kick=None):
        seen.append((clk._k_next + clk._off, clk._bar_anchor, f))
        return orig(f, kick=kick)

    clk._apply_octave = spy
    clk.apply_integer_correction(None, None, 0.5)
    more(clk, 14.0, 4.0)
    d = clk.snapshot()
    lc = clk.stats()["last_correction"]
    assert d["bpm"] == pytest.approx(60.0, abs=0.3) and lc["result"] == "applied"
    K1 = lc["at_beat"]
    assert seen[0][0] == K1 and seen[0][2] == 0.5
    assert (K1 - seen[0][1]) % 8 == 0                   # a bar of the new (2x longer) beats starts there
    assert clk._break_k == K1
    # a vote about a beat before that break is another grid's count; one far ahead is not from the window
    clk.apply_integer_correction(K1 - 4, 4, 1.0)
    clk.tick(T0 + 18.01)
    assert clk.stats()["corr_stale"] == 1
    k_now = clk._k_next + clk._off
    clk.apply_integer_correction(k_now + 9, 4, 1.0)
    clk.tick(T0 + 18.02)
    assert clk.stats()["corr_stale"] == 2
    anchor = clk.snapshot()["bar_anchor_beat"]
    clk.apply_integer_correction(None, None, 2)
    more(clk, 18.0, 4.0)
    d2 = clk.snapshot()
    lc = clk.stats()["last_correction"]
    assert d2["bpm"] == pytest.approx(120.0, abs=0.3) and lc["result"] == "applied"
    K2 = lc["at_beat"]
    assert len(seen) == 2 and seen[1][0] == K2 and seen[1][2] == 2.0
    assert ((K2 - seen[1][1]) * 2) % 4 == 0 and ((K2 - anchor) * 2) % 4 == 0
    assert C.beat_at(d2, T0 + 21.5) > C.beat_at(d, T0 + 18.0)     # the count went on
    assert clk.stats()["octave_votes"] == 2
    for t in TC.grid(120.0, 20.3, 21.8):                # on the kicks again, a beat each
        b = C.beat_at(d2, T0 + t)
        assert abs(b - round(b)) < 0.03
    clk.stop()


def test_an_octave_out_of_range_is_refused_and_nothing_moves():
    clk, pubs = locked()
    clk.apply_integer_correction(None, None, 2.0)       # 240 bpm
    more(clk, 14.0, 3.0)
    st = clk.stats()
    d = clk.snapshot()
    assert st["corr_refused"] == 1 and st["octave_refused"] == 1 and d["bpm"] == pytest.approx(120.0, abs=0.3)
    assert d["beats_per_bar"] == 4 and (d["bar_anchor_beat"] - pubs[-1]["bar_anchor_beat"]) % 4 == 0
    clk.stop()


def test_a_waiting_correction_dies_with_its_grid_or_to_a_human():
    clk, pubs = locked()
    now = pubs[-1]["t_ms"] / 1000.0
    k = round(clk.beat_at(now))
    clk.apply_integer_correction(k - 2, 3, 1.0)
    clk.one(now)                                       # "now is the one": the human wins
    clk.tick(now + 0.01)
    assert clk.stats()["corr_superseded"] == 1 and clk._pending is None
    clk.apply_integer_correction(k - 2, 3, 1.0)
    for i in range(5):                                 # a tap tempo at 100 bpm: a new grid, a numbering break
        clk.tap(now + 0.6 * i)
    clk.tick(now + 0.02)
    st = clk.stats()
    assert st["corr_accepted"] == 2 and st["corr_stale"] == 1 and clk._pending is None
    assert clk.snapshot()["beats_per_bar"] == 4
    clk.stop()


def test_a_correction_that_misses_its_bar_line_expires_instead_of_applying_off_it():
    clk, pubs = locked()
    now = pubs[-1]["t_ms"] / 1000.0
    settle(clk, now)                                   # every frame drained: no beat is judged below
    K = clk._k_next + clk._off
    anchor, bpb = clk._bar_anchor, clk._bpb
    clk.apply_integer_correction(K + 1, 4, 1.0)
    clk.tick(now + 0.01)
    assert clk._pending is not None
    for _ in range(2 * 4 + 1):                         # the upcoming beat never reaches K + 1 + 4n (a count jump)
        clk._apply_pending(clk._k_next - 1)
    st = clk.stats()
    assert st["corr_expired"] == 1 and clk._pending is None
    assert (clk._bar_anchor, clk._bpb) == (anchor, bpb)
    clk.stop()


def test_the_slow_lane_holds_the_clocks_own_octave_check():
    clk, pubs = locked()
    clk.apply_integer_correction(None, 4, 1.0)
    clk.tick(pubs[-1]["t_ms"] / 1000.0 + 0.01)
    assert clk._oct_lock_t >= clk._t_known + C.SLOW_OCT_HOLD_S - 1e-6
    clk.stop()


# -- the measurement ---------------------------------------------------------------------------------------------------
def _paced(beat_thread: bool, x: np.ndarray, block: int = 128) -> dict:
    """Feed ``x`` in real time, as the receiver does (a block when its last sample has arrived; ticks every 10 ms):
    each block's show-rx cost (process + its ticks) and its latency from arrival to done."""
    clk = C.Clock(None, activation="madmom", beat_thread=beat_thread)
    cost, lat = [], []
    start = time.perf_counter() + 0.05
    nt = T0
    for i, n0 in enumerate(range(0, len(x) - block + 1, block)):
        due = start + (n0 + block) / FS
        w = due - time.perf_counter()
        if w > 0:
            time.sleep(w)
        c0 = time.perf_counter()
        t0 = T0 + n0 / FS
        clk.process("room-mic", n0, t0, x[n0:n0 + block].reshape(-1, 1), FS, AUDIO_SEGMENT if i == 0 else 0)
        while nt <= t0 + block / FS:
            clk.tick(nt)
            nt += 0.01
        c1 = time.perf_counter()
        if n0 >= FS:                                    # past the first second (warm-up)
            cost.append((c1 - c0) * 1000.0)
            lat.append((c1 - due) * 1000.0)
    st = clk.stats()
    clk.stop()
    c, la = np.array(cost), np.array(lat)
    return {"cost": {q: float(np.percentile(c, q)) for q in (50, 95, 99)} | {"max": float(c.max())},
            "lat": {q: float(np.percentile(la, q)) for q in (50, 95, 99)} | {"max": float(la.max())},
            "stats": st, "blocks": len(cost)}


@pytest.mark.skipif(not TC.HAVE_MADMOM, reason="madmom not installed")
def test_show_rx_block_cost_inline_vs_thread(capsys):
    secs = float(os.environ.get("SHOW_CLOCK_MEASURE_S", "6"))
    x = np.zeros(int(secs * FS))
    TC.drums_part(x, 0.3, secs, 124.0)
    x = TC.finish(x)
    res = {bt: _paced(bt, x) for bt in (False, True)}
    lines = []
    for bt, r in res.items():
        c, la = r["cost"], r["lat"]
        b = r["stats"]["beat_thread"]
        lines.append(f"show-rx block (128 @ 48 kHz, clock only, {r['blocks']} blocks, {secs:.0f} s paced) "
                     f"{'thread' if bt else 'inline'}: cost p50 {c[50]:.3f} p95 {c[95]:.3f} p99 {c[99]:.3f} "
                     f"max {c['max']:.2f} ms | arrival->done p95 {la[95]:.3f} p99 {la[99]:.3f} max {la['max']:.2f} ms"
                     + (f" | show-beat act p50 {b['act_ms_p50']} p95 {b['act_ms_p95']} ms, lag p95 "
                        f"{b['lag_ms_p95']} ms, drops {b['drops']}" if bt else ""))
    with capsys.disabled():
        print("\n" + "\n".join(lines))
    thr, inl = res[True], res[False]
    assert thr["stats"]["beat_thread"]["drops"] == 0 and thr["stats"]["errors"] == 0
    assert thr["cost"][95] < inl["cost"][95]            # a madmom frame no longer rides on show-rx's block
