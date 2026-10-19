"""beatdbn.py and the dbn estimator in clock.py (README S4.15). Shared plumbing, so real tests:
- the port: BeatHMM + madmom's online rule report madmom's own DBNBeatTrackingProcessor(online=True) beats frame for
  frame on a synthetic activation (two settings); a re-seeded forward state beats where its line says; the clock's
  2-net activation is the candidate's (madmom's 2-net mean, bit for bit);
- the clock: locks on test_clock_thread's steady loops within ~5 s at the right tempo and on the beat; inline, the
  show-beat thread and the beat process hand the tracker the same frames (silent and fresh flags included) and so
  publish the same beats; the quiet gate (gate none at once, silent frames, the forward state restarted when it
  ends); no audio (free, gate none, coasting, back); the corrections (half, double, out of range, tap, a run of taps,
  one, the slow lane's bar and octave votes); bar and phrase fields on a 4/4 loop; the body and stats; a fuzz.
Signals and feeders are test_clock's (48 kHz receiver blocks, 10 ms ticks, ``wait_beat`` after each block).
"""

from __future__ import annotations

import math
import os
import signal

import numpy as np
import pytest

from show_service import beatdbn as D
from show_service import beatproc as B
from show_service import clock as C
from show_service.contract import AUDIO_GAP, AUDIO_RESUME, AUDIO_SEGMENT, ClockData
from show_service.tests import test_clock as TC
from show_service.tests import test_clock_thread as TT

FS = TC.FS
T0 = TC.T0
HAVE_MADMOM = TC.HAVE_MADMOM
needs_madmom = pytest.mark.skipif(not HAVE_MADMOM, reason="madmom not installed")
SRC = "madmom" if HAVE_MADMOM else "aubio"


@pytest.fixture
def made():
    """Beat processes a test makes: closed (and their children killed) at teardown, pass or fail."""
    procs: list = []
    yield procs
    for bp in procs:
        pid = bp.pid
        try:
            bp.close(0.5)
        except Exception:                               # noqa: BLE001
            pass
        for p in (pid, bp.pid):
            if p:
                try:
                    os.killpg(p, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass


def synth_activation(n: int = 1500, bpm: float = 123.0, seed: int = 3) -> np.ndarray:
    """A beat activation at 100 fps: peaks on the beats, smaller ones on the off-beats, noise, a 1.2 s breakdown."""
    rng = np.random.default_rng(seed)
    T = 60.0 / bpm
    ph = (np.arange(n) / D.FPS / T) % 1.0
    on = np.minimum(ph, 1.0 - ph) * T
    off = np.abs(ph - 0.5) * T
    a = 0.02 + 0.8 * np.exp(-0.5 * (on / 0.015) ** 2) + 0.2 * np.exp(-0.5 * (off / 0.015) ** 2)
    a += 0.04 * rng.random(n)
    a[600:720] *= 0.1
    return np.clip(a, 0.0, 1.0)


# -- the port --------------------------------------------------------------------------------------------------------
@needs_madmom
@pytest.mark.parametrize("mn,mx,lam", [(45.0, 200.0, 100.0), (55.0, 215.0, 30.0)])
def test_beat_hmm_reports_madmoms_online_beats_frame_for_frame(mn, mx, lam):
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from madmom.features.beats import DBNBeatTrackingProcessor
    acts = synth_activation()
    mm = DBNBeatTrackingProcessor(min_bpm=mn, max_bpm=mx, transition_lambda=lam, fps=100, online=True)
    mm.reset()
    ref: list[float] = []
    for a in acts:
        ref += list(mm.process_online(np.array([a]), reset=False))
    hm = D.BeatHMM(mn, mx, D.FPS, lam, 16)
    p, last, mine = hm.uniform(), 0.0, []
    for i, a in enumerate(acts):
        p, _ = hm.step(p, float(a))
        s = int(np.argmax(p))
        cur = i / D.FPS
        if hm.beat[s] and cur >= last + 60.0 / mx:          # madmom's online rule (its last beat starts at 0)
            last = cur
            mine.append(cur)
    assert len(ref) > 25 and len(mine) == len(ref) and np.allclose(mine, ref, atol=1e-9)
    assert np.median(np.diff(ref)) == pytest.approx(60.0 / 123.0, abs=0.011)


def test_a_seeded_forward_state_beats_where_its_line_says():
    hm = D.BeatHMM(45.0, 200.0, D.FPS, 100.0, 16)
    p = hm.seed(0.5, 0.25)                                  # 120 bpm, a quarter of a beat since the last one
    s = int(np.argmax(p))
    assert hm.sint[s] == 50 and abs(hm.pos[s] - 0.25) <= 0.02 and p.sum() == pytest.approx(1.0)
    assert p[hm.sint < 45].sum() == 0.0 and p[hm.sint > 55].sum() == 0.0      # no mass at another tempo
    flat = 1.0 / 16.0                                       # an activation that says nothing either way
    for i in range(1, 60):
        p, _ = hm.step(p, flat)
        if hm.beat[int(np.argmax(p))]:
            break
    assert i in (37, 38)                                    # the next beat, 0.75 of a beat later
    assert hm.seed(1e6, 0.0).sum() == pytest.approx(1.0)    # (out of range: still a distribution)


@needs_madmom
def test_the_clocks_two_nets_are_the_candidates_activation():
    """madmom_dbn.MultiNetActivation(2) (the nets kept apart, their float32 mean) is what MadmomActivation(nets=2)
    (madmom's own 2-net average) returns: so the tracker sees the activation it was measured on."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from madmom.audio.signal import Signal
        from madmom.features.beats import RNNBeatProcessor
        from madmom.models import BEATS_LSTM
    ours = C.MadmomActivation(nets=2)
    sep = RNNBeatProcessor(post_processor=None, online=True, nn_files=list(BEATS_LSTM[:2]), origin="stream",
                           num_frames=1, fps=100)
    buf = np.zeros(2048, np.float32)
    sep(Signal(buf.copy(), sample_rate=C.FS), reset=True)   # (warm-up, as MadmomActivation's)
    rng = np.random.default_rng(3)
    t = np.arange(300 * C.HOP) / C.FS
    x = (0.5 * np.sin(2 * np.pi * 55 * t) * np.exp(-((t * 2.05) % 1.0) * 12)
         + 0.03 * rng.standard_normal(len(t))).astype(np.float32)
    reset = True
    for i in range(300):
        hop = x[i * C.HOP:(i + 1) * C.HOP]
        a = ours.process_hop(hop)
        buf[:-C.HOP] = buf[C.HOP:]
        buf[-C.HOP:] = hop
        outs = sep(Signal(buf, sample_rate=C.FS), reset=reset)
        reset = False
        v = np.array([float(np.asarray(o).reshape(-1)[-1]) for o in outs], np.float32)
        assert a == max(0.0, float(np.mean(v, dtype=np.float32))), i


def test_settings_are_best2_and_the_conf_weights_load():
    s = D.SETTINGS
    assert (s["nets"], s["lag_ms"], s["conf_cap_n"], s["beat_on"], s["beat_off"], s["dmx_on"], s["dmx_off"]) == \
        (2, 15.0, 2, 0.8, 0.6, 0.6, 0.35)
    assert s["dwell_bars"] == 2 and s["quiet_reset"] and s["act_reset"] and s["tg"]
    assert s["eps"] == 0.0 and s["ch_every_s"] == 0.0 and s["prior_oct"] == 0.0 and s["hits_w"] == 0.0
    assert set(D.CONF_W) == {"2", "4", "8"} and D.CONF_W["2"]["label_ms"] == 70
    assert D.DbnTracker().W is D.CONF_W["2"] and D.DbnTracker({"nets": 3}).W is None      # (hand conf)
    with pytest.raises(KeyError):
        D.settings(bogus=1)
    assert D.load_conf("/nonexistent/beatdbn_conf.json") == {}


# -- the clock: lock ---------------------------------------------------------------------------------------------------
def lock_signal(kind: str) -> tuple[np.ndarray, list[float], float]:
    if kind == "kicks120":                                  # test_clock_thread.loop / test_clock.loop_clock
        beats = TC.grid(120.0, 0.3, 15.8)
        return TC.finish(TC.kick_loop(beats, 16.0)), beats, 120.0
    if kind == "click120":                                  # test_clock_thread's "lock"
        beats = TC.grid(120.0, 0.31, 15.8)
        x = np.zeros(int(16 * FS))
        for t in beats:
            TC.click(x, t)
            TC.drum(x, t, 0.5, "kick")
        return TC.finish(x), beats, 120.0
    x = np.zeros(int(16 * FS))                              # test_clock_proc's coalesced-blocks loop
    beats = TC.drums_part(x, 0.3, 16.0, 124.0)
    return TC.finish(x), beats, 124.0


@pytest.mark.parametrize("kind", ["kicks120", "click120", "drums124"])
def test_locks_on_the_steady_loops_within_5_s_at_the_right_tempo(kind):
    x, beats, bpm = lock_signal(kind)
    r = TC.Run(x, SRC, estimator="dbn")
    t_lock = r.first(lambda p: p["state"] == "locked" and abs(p["bpm"] / bpm - 1.0) < 0.02)
    assert t_lock is not None and t_lock <= 5.0, t_lock
    if SRC == "madmom":
        t_gate = r.first(lambda p: p["gate"] == "beat")
        assert t_gate is not None and t_gate <= 6.0, t_gate
    last = r.pubs[-1]
    assert last["state"] == "locked" and last["bpm"] == pytest.approx(bpm, rel=0.01)
    steady = [e for tb, e, p in r.err_ms([T0 + t for t in beats]) if tb - T0 >= 8.0]
    assert len(steady) >= 12 and max(abs(e) for e in steady) < 30.0     # on the beat (the conf's label is 70 ms)
    st = r.clk.stats()
    assert st["estimator"] == "dbn" and st["errors"] == 0 and st["dbn"]["dbn_beats"] >= 25
    assert st["state"] == last["state"] and st["gate"] == last["gate"]


# -- the clock: the three activation paths give the tracker the same frames -------------------------------------------
def spy_tracker(clk: C.Clock) -> list[tuple]:
    rec: list[tuple] = []
    trk = clk._trk
    orig = trk.frame

    def frame(a, t_raw, *, silent=False, fresh=False):
        rec.append((float(a), float(t_raw), bool(silent), bool(fresh)))
        return orig(a, t_raw, silent=silent, fresh=fresh)

    trk.frame = frame
    return rec


def lines(pubs: list[dict]) -> list[tuple]:
    keys = ("bpm", "anchor_ms", "beat_at_anchor", "beats_per_bar", "bar_anchor_beat", "phrase_bars", "conf",
            "bar_conf", "state", "gate", "epoch")
    out: list[tuple] = []
    for p in pubs:
        k = tuple(p[x] for x in keys)
        if not out or out[-1] != k:
            out.append(k)
    return out


@needs_madmom
def test_inline_the_thread_and_the_process_publish_the_same_beats(made):
    x, _, _ = lock_signal("click120")
    gap = 256 * 1700                                       # a 250 ms hole at 9.07 s (silent frames, then a fresh hop)
    quiet = (int(10.5 * FS), int(15.0 * FS))               # ... and a quiet room (quiet from 13.5 s, 3 s below)
    runs = {}
    for mode in ("inline", "thread", "process"):
        pubs: list[dict] = []
        clk = C.Clock(lambda m: pubs.append(m["data"]), activation="madmom", beat_thread=mode == "thread",
                      beat_proc=mode == "process")
        if mode == "process":
            made.append(clk._bt)
            assert clk.wait_beat_ready(60)
        rec = spy_tracker(clk)
        nt, n0, i = T0, 0, 0
        while n0 < len(x):
            if n0 == gap:
                blk, flags = np.broadcast_to(np.zeros((1, 1), np.float32), (12000, 1)), AUDIO_GAP
            else:
                blk, flags = x[n0:n0 + 256].reshape(-1, 1), (AUDIO_SEGMENT if n0 == 0 else
                                                              (AUDIO_RESUME if n0 == gap + 12000 else 0))
            t0 = T0 + n0 / FS
            clk.set_level(0.01 if quiet[0] <= n0 < quiet[1] else 0.5, t0)
            clk.process("room-mic", n0, t0, blk, FS, flags)
            clk.wait_beat()
            while nt <= t0 + blk.shape[0] / FS:
                clk.tick(nt)
                nt += 0.01
            n0 += blk.shape[0]
            i += 1
        TT.settle(clk, nt)
        st = clk.stats()
        runs[mode] = (rec, lines(pubs), st, pubs)
        clk.stop()
    ri, li, si, pi = runs["inline"]
    assert sum(f[2] for f in ri) > 150 and sum(f[3] for f in ri) >= 3     # silent frames; fresh: start, hole, quiet
    for mode in ("thread", "process"):
        rm, lm, sm, pm = runs[mode]
        assert rm == ri, mode                              # every frame: activation, time, silent, fresh
        assert lm == li, mode                              # the same published lines, labels and bars
        assert sm["dbn"] == si["dbn"] and sm["errors"] == 0, mode
        assert pm[-1]["state"] == pi[-1]["state"] and sm["beat_thread"]["mode"] == mode
    assert si["quiet_starts"] == 1 and si["quiet_ends"] == 1 and si["gap_hops_skipped"] >= 24
    assert any(p["state"] == "locked" and p["gate"] == "beat" for p in pi if p["t_ms"] / 1000.0 - T0 < 9.0)


# -- the clock: quiet, no audio ----------------------------------------------------------------------------------------
def feed(clk: C.Clock, x: np.ndarray, t_start: float, n_start: int = 0, level=None, first_flags: int = 0,
         block: int = 256) -> None:
    """Receiver blocks of ``x`` from host time ``t_start`` (stream index ``n_start``) with 10 ms ticks;
    ``level(t)``: the dsp gate given to set_level before each block (None: never called)."""
    nt = t_start
    for i, n0 in enumerate(range(0, len(x), block)):
        blk = x[n0:n0 + block].reshape(-1, 1)
        t0 = t_start + n0 / FS
        if level is not None:
            clk.set_level(level(t0 - t_start), t0)
        clk.process("room-mic", n_start + n0, t0, blk, FS, first_flags if i == 0 else 0)
        clk.wait_beat()
        while nt <= t0 + blk.shape[0] / FS:
            clk.tick(nt)
            nt += 0.01


def locked_clock(secs: float = 12.0, source: str = SRC) -> tuple[C.Clock, list[dict], list[float]]:
    pubs: list[dict] = []
    clk = C.Clock(lambda m: pubs.append(m["data"]), activation=source)
    beats = TC.grid(120.0, 0.3, secs - 0.2)
    feed(clk, TC.finish(TC.kick_loop(beats, secs)), T0, first_flags=AUDIO_SEGMENT)
    assert pubs[-1]["state"] == "locked", pubs[-1]
    return clk, pubs, [T0 + t for t in beats]


def more(clk: C.Clock, t_from: float, secs: float, **kw) -> None:
    beats = [t - t_from for t in TC.grid(120.0, t_from + 0.3, t_from + secs)]
    feed(clk, TC.finish(TC.kick_loop(beats, secs)), T0 + t_from, n_start=int(t_from * FS), **kw)


def test_a_quiet_room_drops_the_gate_at_once_and_restarts_the_forward_state_when_it_ends():
    pubs: list[dict] = []
    clk = C.Clock(lambda m: pubs.append(m["data"]), activation=SRC)
    calls: list[int] = []
    real = clk.act.process_hop
    clk.act.process_hop = lambda hop: calls.append(1) or real(hop)
    secs = 26.0
    x = TC.finish(TC.kick_loop(TC.grid(120.0, 0.3, secs - 0.2), secs))
    for i in range(int(12.0 * FS), int(17.0 * FS)):       # the room goes quiet for 5 s (the level says so)
        x[i] *= 0.0
    lvl = lambda t: 0.01 if 12.0 <= t < 17.0 else 0.5      # noqa: E731
    seen: dict = {}

    def watch(t0: float) -> None:
        d = clk.snapshot()
        if d is not None and 15.5 <= t0 - T0 < 16.5:
            seen.setdefault("mid", d)
            seen.setdefault("calls", len(calls))
        if d is not None and t0 - T0 >= 16.9:
            seen.setdefault("calls_end", len(calls))

    nt = T0
    for i, n0 in enumerate(range(0, len(x), 256)):
        t0 = T0 + n0 / FS
        clk.set_level(lvl(t0 - T0), t0)
        clk.process("room-mic", n0, t0, x[n0:n0 + 256].reshape(-1, 1), FS, AUDIO_SEGMENT if i == 0 else 0)
        watch(t0)
        while nt <= t0 + 256 / FS:
            clk.tick(nt)
            nt += 0.01
    before = [p for p in pubs if 9.0 <= p["t_ms"] / 1000.0 - T0 < 12.0]
    assert before and all(p["state"] == "locked" for p in before)
    mid = seen["mid"]
    assert mid["gate"] == "none" and mid["state"] == "holding" and mid["bpm"] == pytest.approx(120.0, rel=0.02)
    assert seen["calls_end"] == seen["calls"]              # no activation while quiet (silent frames)
    st = clk.stats()
    assert st["quiet_starts"] == 1 and st["quiet_ends"] == 1 and st["dbn"]["silent_frames"] > 150
    assert st["dbn"]["hmm_resets"] >= 4                    # build, the first hop, the quiet's end, its fresh hop
    assert pubs[-1]["state"] == "locked" and pubs[-1]["bpm"] == pytest.approx(120.0, rel=0.02)
    assert st["errors"] == 0


def test_no_audio_goes_free_with_the_gate_off_coasts_and_comes_back():
    clk, pubs, beats = locked_clock()
    before = dict(pubs[-1])
    last = before["t_ms"] / 1000.0
    for i in range(1, 400):                                 # 4 s of ticks, no audio
        clk.tick(last + 0.01 * i)
    d = clk.snapshot()
    assert d["state"] == "free" and d["gate"] == "none" and d["bpm"] == pytest.approx(before["bpm"], abs=1e-9)
    assert clk.stats()["dbn"]["no_audio"] == 1 and clk.stats()["dbn"]["n_line"] >= 1
    more(clk, 16.0, 6.0, first_flags=AUDIO_SEGMENT)        # audio again, on the same grid
    d2 = clk.snapshot()
    assert d2["state"] == "locked" and d2["bpm"] == pytest.approx(120.0, rel=0.02)
    b = C.beat_at(d2, T0 + 21.3)
    assert abs(b - round(b)) < 0.06 and round(b) > round(C.beat_at(before, T0 + 12.0))     # the count went on


# -- the clock: corrections --------------------------------------------------------------------------------------------
def test_half_and_double_scale_the_line_and_the_dbn_waits_for_the_hold():
    """Half: the line's period x2 at the next beat, the DBN re-seeded there and its disagreement held for
    beatdbn.HOLD_S (a correction is an observation: after the hold the music's own beat may take it back); double
    returns; 240 bpm is refused."""
    clk, pubs, _ = locked_clock()
    e0 = clk.snapshot()["epoch"]
    clk.double()                                            # 240 bpm: out of range, refused, nothing moves
    more(clk, 12.0, 2.0)
    assert clk.snapshot()["bpm"] == pytest.approx(120.0, rel=0.02) and clk.stats()["octave_refused"] == 1
    k_before = C.beat_at(clk.snapshot(), T0 + 14.0)
    clk.half()
    more(clk, 14.0, 4.0)
    d = clk.snapshot()
    assert d["bpm"] == pytest.approx(60.0, rel=0.02) and d["epoch"] > e0 and clk.stats()["dbn"]["held"]
    t_half = next(p["t_ms"] for p in pubs if p["t_ms"] / 1000.0 - T0 >= 14.0 and 0.0 < p["bpm"] < 90.0) / 1000.0
    assert t_half - T0 < 15.0                               # at the next beat
    held = [p for p in pubs if t_half <= p["t_ms"] / 1000.0 < T0 + 18.0]
    assert held and all(p["bpm"] == pytest.approx(60.0, rel=0.03) for p in held)    # no snap back while held
    for t in TC.grid(120.0, 15.3, 17.5):                    # every kick on a whole or half beat of the slow grid
        b = C.beat_at(d, T0 + t)
        assert abs(2 * b - round(2 * b)) < 0.06
    assert C.beat_at(d, T0 + 17.5) > k_before               # the count never went back
    clk.double()
    more(clk, 18.0, 6.0)
    d2 = clk.snapshot()
    assert d2["bpm"] == pytest.approx(120.0, rel=0.02) and d2["state"] == "locked"
    for t in TC.grid(120.0, 22.3, 23.5):
        b = C.beat_at(d2, T0 + t)
        assert abs(b - round(b)) < 0.06
    assert C.beat_at(d2, T0 + 23.0) > C.beat_at(d, T0 + 17.5)
    st = clk.stats()
    assert st["human_octave"] == 3 and st["octave_changes"] == 2 and st["dbn"]["octaves"] == 2
    assert st["errors"] == 0


def test_a_tap_rephases_the_line_and_one_that_agrees_moves_nothing():
    clk, pubs, beats = locked_clock()
    d = clk.snapshot()
    e0 = d["epoch"]
    k = math.floor(C.beat_at(d, T0 + 12.2)) + 1
    t_on = d["anchor_ms"] / 1000.0 + (k - d["beat_at_anchor"]) * 60.0 / d["bpm"]
    clk.tap(t_on + 0.01)                                    # on the line: one more hit
    clk.tick(T0 + 12.3)
    assert clk.snapshot()["epoch"] == e0 and clk.stats()["dbn"]["taps_agree"] == 1
    t_off = t_on + 0.5 + 0.2                                # 0.4 of a beat off the next beat
    clk.tap(t_off)
    clk.tick(T0 + 12.31)
    d2 = clk.snapshot()
    b = C.beat_at(d2, t_off)
    assert abs(b - round(b)) < 1e-6 and d2["epoch"] == e0 + 1 and d2["bpm"] == pytest.approx(d["bpm"])
    assert clk.stats()["dbn"]["taps_phase"] == 1 and clk.stats()["dbn"]["held"]
    more(clk, 12.0, 3.0)                                    # within the hold: the DBN's disagreement waits
    d3 = clk.snapshot()
    assert abs(C.beat_at(d3, t_off) - round(C.beat_at(d3, t_off))) < 0.1
    assert clk.stats()["errors"] == 0


def test_a_run_of_taps_without_audio_starts_a_free_line_at_its_tempo():
    pubs: list[dict] = []
    clk = C.Clock(lambda m: pubs.append(m["data"]), activation="aubio")
    for i in range(6):
        clk.tap(100.0 + 0.6 * i + (0.01 if i % 2 else -0.01))
    clk.tick(103.1)
    d = clk.snapshot()
    assert d["bpm"] == pytest.approx(100.0, rel=0.03) and d["state"] == "free" and d["gate"] == "dmx"
    b = C.beat_at(d, 103.0)                                 # the last tap lands on a beat
    assert abs(b - round(b)) < 0.05
    assert (round(b) - d["bar_anchor_beat"]) % 4 == 1       # the first tap of the run that made the line (taps
    #                                                         1-4: beats -3..0) is a one; 103.0 is beat 2
    assert clk.stats()["dbn"]["taps_tempo"] == 1


def test_one_and_the_slow_lanes_bar_and_octave_votes():
    clk, pubs, beats = locked_clock(14.0)
    d = clk.snapshot()
    assert d["bar_conf"] == pytest.approx(0.1) and d["beats_per_bar"] == 4     # the bars started, no downbeat yet
    t_one = beats[-3]
    clk.one(t_one)
    clk.tick(pubs[-1]["t_ms"] / 1000.0 + 0.1)
    assert clk.snapshot()["bar_anchor_beat"] == round(clk.beat_at(t_one)) and clk.snapshot()["bar_conf"] >= 0.8
    now = clk.snapshot()["t_ms"] / 1000.0
    one = round(clk.beat_at(now)) + 2                       # "beat one+2 is a one", in 3/4
    clk.apply_integer_correction(one, 3, 1.0)
    more(clk, 14.0, 4.0)
    d = clk.snapshot()
    lc = clk.stats()["last_correction"]
    assert lc["result"] == "applied" and d["beats_per_bar"] == 3 and (d["bar_anchor_beat"] - one) % 3 == 0
    assert d["bar_conf"] >= 0.6
    clk.apply_integer_correction(None, None, 0.5)           # the slow lane: the beat is half as fast
    more(clk, 18.0, 6.0)
    d2 = clk.snapshot()
    st = clk.stats()
    assert d2["bpm"] == pytest.approx(60.0, rel=0.02) and st["last_correction"]["result"] == "applied"
    assert st["octave_votes"] == 1 and st["errors"] == 0
    clk2 = C.Clock(None, activation="aubio")                # no line: no correction
    clk2.apply_integer_correction(8, 4, 1.0)
    clk2.tick(T0)
    assert clk2.stats()["corr_no_grid"] == 1


def test_bar_and_phrase_fields_come_from_the_bar_code_on_a_4_4_loop():
    secs = 30.0
    beats = TC.grid(120.0, 0.3, secs - 0.2)
    x = np.zeros(int(secs * FS))
    for i, t in enumerate(beats):
        TC.drum(x, t, 0.8 if i % 4 == 0 else 0.45, "kick")    # 1 accented
        if i % 4 == 0 and (i // 4) % 4 == 0:
            TC.drum(x, t, 0.6, "snare")                     # ... and every 4th bar's 1 more so (a phrase)
        if i % 2:
            TC.drum(x, t, 0.3, "snare")
    r = TC.Run(TC.finish(x), SRC, estimator="dbn")
    d = r.pubs[-1]
    st = r.clk.stats()
    assert st["confirmed"] >= 1 and d["bar_conf"] == pytest.approx(0.1)
    assert d["beats_per_bar"] == 4 and d["phrase_bars"] in (4, 8, 16) and 0.0 < d["phrase_conf"] <= d["bar_conf"]
    assert isinstance(d["bar_anchor_beat"], int)
    early = [p for p in r.pubs if p["state"] == "searching"]
    assert early and all(p["bar_conf"] == 0.0 for p in early)
    pos = C.position_at(d, d["t_ms"] / 1000.0)
    assert pos is not None and 0.0 <= pos["beat_in_bar"] < 4.0


# -- the body, stats, the estimator switch -----------------------------------------------------------------------------
def test_body_stats_and_the_estimator_switch(caplog):
    clk, pubs, _ = locked_clock(8.0)
    d = pubs[-1]
    assert set(d) == set(ClockData.__annotations__)
    for k, typ in (("bpm", float), ("anchor_ms", float), ("beat_at_anchor", int), ("bar_anchor_beat", int),
                   ("beats_per_bar", int), ("phrase_bars", int), ("epoch", int), ("state", str), ("gate", str),
                   ("conf", float), ("stability", float)):
        assert isinstance(d[k], typ), k
    st = clk.stats()
    assert st["estimator"] == "dbn" and st["source"] == ("madmom-dbn2" if SRC == "madmom" else "aubio-dbn")
    assert d["source"] == st["source"] and 0.0 < d["stability"] <= 1.0
    assert {"hmm_resets", "dbn_beats", "lines", "n_line", "conf_w", "held"} <= set(st["dbn"])
    assert clk.next_beat(d["t_ms"] / 1000.0, gate="none") is not None
    assert clk.next_beat_k(d["t_ms"] / 1000.0)[0] > d["beat_at_anchor"] - 2
    legacy = C.Clock(None, activation="aubio", estimator="legacy")
    assert legacy._trk is None and legacy.stats()["estimator"] == "legacy" and legacy.stats()["source"] == "aubio"
    assert "dbn" not in legacy.stats()
    with caplog.at_level("WARNING", logger="show_service.clock"):
        odd = C.Clock(None, activation="aubio", estimator="pll")
    assert odd.stats()["estimator"] == "dbn" and any("estimator" in r.message for r in caplog.records)


# -- a fuzz ------------------------------------------------------------------------------------------------------------
def test_fuzz_the_clock_and_the_tracker_never_raise():
    rng = np.random.default_rng(11)
    clk = C.Clock(None, activation="aubio")
    n, t, fs = 0, T0, FS
    loop = TC.finish(TC.kick_loop(TC.grid(127.0, 0.2, 9.8), 10.0))
    for step in range(1400):
        r = rng.random()
        frames = int(rng.choice([0, 1, 64, 128, 256, 480, 1024, 4096]))
        flags = 0
        if r < 0.03:
            flags, n = AUDIO_SEGMENT, int(rng.integers(0, 10 ** 9))
        elif r < 0.06:
            flags = AUDIO_GAP
        elif r < 0.08:
            flags = AUDIO_RESUME
        elif r < 0.09:
            fs = int(rng.choice([44100, 48000]))
        ch = int(rng.choice([1, 2]))
        if flags & AUDIO_GAP:
            pcm = np.broadcast_to(np.zeros((1, ch), np.float32), (max(1, frames), ch))
        else:
            i0 = int(rng.integers(0, len(loop) - 4096))
            pcm = np.repeat((loop[i0:i0 + frames] * float(rng.choice([0.0, 0.01, 1.0, 3.0]))).reshape(-1, 1),
                            ch, axis=1).astype(np.float32)
        clk.set_level(float(rng.choice([0.0, 0.01, 0.03, 0.5])) if rng.random() < 0.3 else None, t)
        clk.process("room-mic", n, t, pcm, fs, flags)
        n += pcm.shape[0]
        t += pcm.shape[0] / fs + (float(rng.exponential(0.5)) if rng.random() < 0.01 else 0.0)
        u = rng.random()
        if u < 0.02:
            clk.tap(t + float(rng.normal(0, 0.2)))
        elif u < 0.025:
            clk.half()
        elif u < 0.03:
            clk.double()
        elif u < 0.035:
            clk.one(t)
        elif u < 0.045:
            clk.apply_integer_correction(int(rng.integers(-5, 400)), int(rng.choice([2, 3, 4, 7])),
                                         float(rng.choice([0.5, 1.0, 2.0])))
        elif u < 0.05:
            clk.observe_hit(t - 0.01, "sub", 0.8)
        clk.tick(t)
        if rng.random() < 0.005:
            for _ in range(200):                            # 2 s without audio
                t += 0.01
                clk.tick(t)
        d = clk.snapshot()
        assert d is not None and d["state"] in ("searching", "locked", "holding", "free")
        assert d["gate"] in ("beat", "dmx", "none") and 0.0 <= d["conf"] <= 1.0
        assert d["bpm"] == 0.0 or 45.0 * 0.9 <= d["bpm"] <= 200.0 * 1.1
    assert clk.stats()["errors"] == 0, clk.stats()
    trk = D.DbnTracker()                                    # the tracker alone, on anything
    tt = 50.0
    for i in range(3000):
        tt += 0.01
        u = rng.random()
        trk.frame(float(rng.random() ** 3), tt, silent=u < 0.05, fresh=u > 0.99)
        if u < 0.002:
            trk.set_quiet(not trk.quiet, tt)
        elif u < 0.004 and trk.line is not None:
            trk.set_octave(float(rng.choice([0.5, 2.0])), int(trk.beat_at(tt)) + 1)
        elif u < 0.006:
            trk.tap(tt + float(rng.normal(0, 0.1)), [tt - 1.5, tt - 1.0, tt - 0.5, tt] if rng.random() < 0.5 else None)
        elif u < 0.007:
            trk.coast(tt + 1.0)
        if rng.random() < 0.5:
            trk.events.clear()
        assert trk.state in ("searching", "locked", "holding", "free") and 0.0 <= trk.conf <= 1.0
        assert trk.line is None or 60.0 / 200.0 * 0.85 <= trk.line[2] <= 60.0 / 45.0 * 1.15
    assert trk.counts["dbn_beats"] > 0
