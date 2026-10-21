"""beatdbn.py and the dbn estimator in clock.py (README S4.15). Shared plumbing, so real tests:
- the port: BeatHMM + madmom's online rule report madmom's own DBNBeatTrackingProcessor(online=True) beats frame for
  frame on a synthetic activation (two settings); a re-seeded forward state beats where its line says; the clock's
  2-net activation is the candidate's (madmom's 2-net mean, bit for bit);
- the clock: locks on test_clock_thread's steady loops within ~5 s at the right tempo and on the beat; inline, the
  show-beat thread and the beat process hand the tracker the same frames (silent and fresh flags included) and so
  publish the same beats; the quiet gate (gate none at once, silent frames, the forward state restarted when it
  ends); no audio (free, gate none, coasting, back); the corrections (half, double, out of range, tap, a run of taps,
  one, the slow lane's bar and octave votes); the octave map (a minute at x2 over a DBN at its own octave, ended by
  the quiet room's new grid, the cold grid and a contrary vote); the slow lane's octave evidence (the activation's
  ratio at the beats an octave adds or drops; a x2 onto nothing refused, an unheard one too; a halving the audio
  can't confirm refused at 126 bpm, applied at 184 or with a wider prior; one it confirms applied at 120); bar and
  phrase fields on a 4/4 loop; the body and stats; a fuzz;
- the published line (README S4.15 "Publishing"), on synthetic activations fed frame by frame (``act_clock``): the
  count only goes forward across a re-phase and a tempo jump; next_beat moves about half as much as the tracker's own
  line under a jittered DBN beat train; the downbeat stays put across a re-lock and a tempo jump; the epoch moves only
  on a real tempo jump, and once for a song after a quiet room; a slew is judged again at a beat the DBN missed.
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
    ln0, n0 = clk._trk.line, len(pubs)
    for i in range(1, 400):                                 # 4 s of ticks, no audio
        clk.tick(last + 0.01 * i)
    d = clk.snapshot()
    # the tracker's line coasts unchanged; the published one stays on it (a slew in force is judged again at each
    # coasted beat, as legacy's: it doesn't run on for the whole coast), its count never going back
    assert d["state"] == "free" and d["gate"] == "none" and clk._trk.line == ln0
    assert d["bpm"] == pytest.approx(60.0 / ln0[2], rel=1e-3) and d["epoch"] == before["epoch"]
    for (_, p), (t, q) in zip([(0.0, before)] + [(x["t_ms"] / 1000.0, x) for x in pubs[n0:]],
                              [(x["t_ms"] / 1000.0, x) for x in pubs[n0:]]):
        assert C.beat_at(q, t) >= C.beat_at(p, t) - 1e-9, t
    assert clk.stats()["dbn"]["no_audio"] == 1 and clk.stats()["dbn"]["n_line"] >= 1
    more(clk, 16.0, 6.0, first_flags=AUDIO_SEGMENT)        # audio again, on the same grid
    d2 = clk.snapshot()
    assert d2["state"] == "locked" and d2["bpm"] == pytest.approx(120.0, rel=0.02)
    b = C.beat_at(d2, T0 + 21.3)
    assert abs(b - round(b)) < 0.06 and round(b) > round(C.beat_at(before, T0 + 12.0))     # the count went on


# -- the clock: corrections --------------------------------------------------------------------------------------------
def test_half_and_double_map_the_line_and_the_dbn_tracks_on_underneath():
    """Half: the octave map (factor 0.5) from the next beat; the published line runs at 60 while the DBN tracks on at
    its own 120 (no hold, nothing re-seeded) and stays mapped; double is the contrary correction: factor 1, the
    tracker's own line again; a double that would publish 240 bpm is refused."""
    clk, pubs, _ = locked_clock()
    e0 = clk.snapshot()["epoch"]
    clk.double()                                            # 240 bpm: out of range, refused, nothing moves
    more(clk, 12.0, 2.0)
    assert clk.snapshot()["bpm"] == pytest.approx(120.0, rel=0.02) and clk.stats()["octave_refused"] == 1
    k_before = C.beat_at(clk.snapshot(), T0 + 14.0)
    clk.half()
    more(clk, 14.0, 10.0)
    d = clk.snapshot()
    st = clk.stats()
    assert d["bpm"] == pytest.approx(60.0, rel=0.02) and d["epoch"] > e0 and not st["dbn"]["held"]
    assert st["oct_factor"] == 0.5 and st["oct_age_s"] > 8.0 and st["bpm"] == pytest.approx(120.0, rel=0.02)
    assert d["gate"] == "beat" and d["state"] == "locked"   # the tracker's own conf and gate
    t_half = next(p["t_ms"] for p in pubs if p["t_ms"] / 1000.0 - T0 >= 14.0 and 0.0 < p["bpm"] < 90.0) / 1000.0
    assert t_half - T0 < 15.0                               # at the next beat
    held = [p for p in pubs if t_half <= p["t_ms"] / 1000.0 < T0 + 24.0]
    assert held and all(p["bpm"] == pytest.approx(60.0, rel=0.03) for p in held)    # never back (the old hold: 6 s)
    for t in TC.grid(120.0, 15.3, 23.5):                    # every kick on a whole or half beat of the slow grid
        b = C.beat_at(d, T0 + t)
        assert abs(2 * b - round(2 * b)) < 0.06
    assert C.beat_at(d, T0 + 23.5) > k_before               # the count never went back
    clk.double()
    more(clk, 24.0, 6.0)
    d2 = clk.snapshot()
    assert d2["bpm"] == pytest.approx(120.0, rel=0.02) and d2["state"] == "locked"
    for t in TC.grid(120.0, 28.3, 29.5):
        b = C.beat_at(d2, T0 + t)
        assert abs(b - round(b)) < 0.06
    assert C.beat_at(d2, T0 + 29.0) > C.beat_at(d, T0 + 23.5)
    st = clk.stats()
    assert st["human_octave"] == 3 and st["octave_changes"] == 2 and st["oct_factor"] == 1.0
    assert st["oct_maps_applied"] == 1 and st["oct_maps_cleared"] == 1 and st["oct_clear_contrary"] == 1
    assert st["oct_age_s"] is None and st["dbn"].get("holds", 0) == 0 and st["errors"] == 0


def test_an_octave_map_holds_for_a_minute_ends_on_a_cold_grid_and_a_contrary_vote():
    """README S4.15 "The octave map": a 100 bpm DBN loop; the slow lane's x2 at 12 s publishes 200 bpm from the next
    bar line to 80 s with no flip back (the DBN tracks on at 100 underneath), the count only forward, one epoch, the
    downbeat kept; 4 s of silence, then the same loop: the first gate after it is a new grid on the tracker's own 100
    (the map ends); a second x2 at 95 s, then the contrary x0.5 at 106 s: factor 1 again."""
    lag = C.AubioActivation.lag_s
    a = act_train([(100.0, 0.3, 80.0), (100.0, 84.21, 125.0)], 125.0, lag, seed=31)
    t = (np.arange(len(a)) + 1) / D.FPS
    sil = (t >= 80.0) & (t < 84.0)
    a[sil] = 0.0
    seen: dict = {}

    def note(key: str):
        return lambda c, tt: seen.setdefault(key, (tt, dict(c.stats()), c._trk.line))

    at = {12.0: lambda c, tt: c.apply_integer_correction(None, None, 2.0), 79.9: note("mapped"), 93.0: note("cold"),
          95.0: lambda c, tt: c.apply_integer_correction(None, None, 2.0), 105.9: note("again"),
          106.0: lambda c, tt: c.apply_integer_correction(None, None, 0.5)}
    clk, pubs = act_clock(a, at, level=np.where(sil, 0.0, 0.4))
    t_sw = next(tt for tt, d, _ in pubs if tt >= 12.0 and d["bpm"] > 150.0)
    assert t_sw < 15.0
    mapped = [(tt, d, ln) for tt, d, ln in pubs if t_sw <= tt < 80.0]
    assert mapped[-1][0] - t_sw >= 60.0
    assert all(d["bpm"] == pytest.approx(200.0, rel=0.03) for _, d, _ in mapped)      # steady, never back to 100
    assert sum(abs(60.0 / ln[2] - 100.0) < 3.0 for _, _, ln in mapped) >= 0.9 * len(mapped)  # the DBN at its own
    assert len({d["epoch"] for _, d, _ in mapped}) == 1
    assert len({d["bar_anchor_beat"] % 4 for _, d, _ in mapped}) == 1 and mapped[-1][1]["beats_per_bar"] == 4
    assert all(d["gate"] == "beat" for tt, d, _ in mapped if tt >= t_sw + 4.0)
    for (_, p, _), (tt, q, _) in zip(pubs, pubs[1:]):
        if p["bpm"]:
            for dt in (0.0, 0.05, 0.1):
                assert C.beat_at(q, tt + dt) >= C.beat_at(p, tt + dt) - 1e-9, (tt, dt)
    res = []                                                # the 100 bpm beats and the ones between them, each
    for tt, d, _ in mapped:                                 # read on the body in force at the time: all on it
        if tt >= t_sw + 2.0:
            res += [C.beat_at(d, b + h) for b in act_beats(100.0, tt, tt + 0.6) for h in (0.0, 0.3)]
    res = np.array([x - round(x) for x in res])
    assert len(res) > 400 and np.percentile(np.abs(res - np.median(res)), 95) < 0.05 and abs(np.median(res)) < 0.1
    ts, st, _ = seen["mapped"]
    assert st["oct_factor"] == 2.0 and st["oct_age_s"] > 60.0 and st["oct_maps_applied"] == 1
    assert st["corr_applied"] == 1 and st["dbn"].get("holds", 0) == 0
    _, st, _ = seen["cold"]                                 # the gate after the quiet room: the tracker's own octave
    # (here the DBN's restart line jumped first, ungated: that new grid ended the map; else the cold grid does)
    assert st["oct_factor"] == 1.0 and st["pub_cold_grids"] == 1
    assert st.get("oct_clear_cold", 0) + st.get("oct_clear_jump", 0) == 1 and st["oct_maps_cleared"] == 1
    cold = [d for tt, d, _ in pubs if 90.0 <= tt < 95.0]
    assert cold and all(d["bpm"] == pytest.approx(100.0, rel=0.03) for d in cold)
    _, st, _ = seen["again"]
    assert st["oct_factor"] == 2.0 and st["oct_maps_applied"] == 2
    st = clk.stats()
    assert st["oct_factor"] == 1.0 and st["oct_clear_contrary"] == 1 and st["oct_maps_cleared"] == 2
    assert pubs[-1][1]["bpm"] == pytest.approx(100.0, rel=0.03) and st["errors"] == 0


def test_an_octave_map_ends_at_the_cold_grid_after_a_quiet_room():
    """_dbn_cold_grid with a map in force (the room went quiet and the line came back gated, unjumped): the map ends
    there, a new grid (one epoch) on the tracker's own line."""
    lag = C.AubioActivation.lag_s

    def quiet_passed(c: C.Clock, tt: float) -> None:        # as _frame_dbn marks it after a quiet room
        c._pub_cold, c._pub_real = True, False

    clk, pubs = act_clock(act_train([(100.0, 0.3, 30.0)], 30.0, lag, seed=31),
                          {12.0: lambda c, tt: c.apply_integer_correction(None, None, 2.0), 22.0: quiet_passed})
    before = [d for tt, d, _ in pubs if 20.0 <= tt < 22.0]
    after = [d for tt, d, _ in pubs if 23.0 <= tt]
    assert all(d["bpm"] == pytest.approx(200.0, rel=0.03) for d in before)
    assert after and all(d["bpm"] == pytest.approx(100.0, rel=0.03) for d in after)
    assert {d["epoch"] for d in after} == {before[-1]["epoch"] + 1}
    st = clk.stats()
    assert st["oct_factor"] == 1.0 and st["oct_clear_cold"] == 1 and st["pub_cold_grids"] == 1
    for (_, p, _), (tt, q, _) in zip(pubs, pubs[1:]):
        if p["bpm"]:
            assert C.beat_at(q, tt) >= C.beat_at(p, tt) - 1e-9, tt


def act_beats(bpm: float, t0: float, t1: float) -> list[float]:
    """act_train's beats (from 0.3 s) between t0 and t1, as the tracker times them (its own lag taken out)."""
    T = 60.0 / bpm
    return [0.3 + i * T for i in range(int((t1 - 0.3) / T) + 1) if t0 <= 0.3 + i * T < t1]


def test_an_octave_back_switches_past_a_bar_line_that_is_an_in_between_beat():
    """Under a x2 map the music moves half a tracker beat (30 s): the tracker's beats are now the in-between beats of
    the bars (every bar line an in-between beat). The slow lane's x0.5 (40 s) then switches at the first beat of both
    grids after its bar line (it once waited for a bar line that never came, and expired), the switch beat starting
    the bar; and a human half (56 s) at the next bar the same way (it once waited for ever)."""
    lag = C.AubioActivation.lag_s
    seen: dict = {}

    def parity(c: C.Clock, tt: float) -> None:          # the tracker's beat against the bar anchor, published count
        seen["par"] = (c._om_floor(c._trk.line[0]) + c._off - c._bar_anchor) % 2
        seen["f"] = c._om_f

    at = {10.0: lambda c, tt: c.one(tt), 12.0: lambda c, tt: c.apply_integer_correction(None, None, 2.0),
          39.9: parity, 56.0: lambda c, tt: c.half()}
    at[40.0] = lambda c, tt: c.apply_integer_correction(None, None, 0.5)
    clk, pubs = act_clock(act_train([(100.0, 0.3, 30.0), (100.0, 30.6, 80.0)], 80.0, lag, seed=31), at)
    assert seen == {"par": 1, "f": 2.0}                 # (the case: every bar line an in-between beat)
    t_back = next(tt for tt, d, _ in pubs if tt >= 40.0 and d["bpm"] < 150.0)
    t_half = next(tt for tt, d, _ in pubs if tt >= 56.0 and d["bpm"] < 75.0)
    assert t_back < 40.0 + 2 * 8 * 0.3 and t_half < 56.0 + 2 * 4 * 0.6     # within its wait, never expired
    st = clk.stats()
    assert st["corr_applied"] == 2 and st.get("corr_expired", 0) == 0 and clk._pending_oct is None
    assert st["oct_factor"] == 0.5 and st["oct_clear_contrary"] == 1 and st["errors"] == 0
    d = next(d for tt, d, _ in pubs if t_back + 1.0 <= tt)      # at 100, every beat (the downbeat too) on the music's
    beats = [30.6 + i * 0.6 for i in range(80) if t_back + 1.0 <= 30.6 + i * 0.6 < t_back + 4.0]
    assert len(beats) == 5 and all(abs(x - round(x)) < 0.06 for x in (C.beat_at(d, b) for b in beats))
    for (_, p, _), (tt, q, _) in zip(pubs, pubs[1:]):
        if p["bpm"]:
            for dt in (0.0, 0.05, 0.1):
                assert C.beat_at(q, tt + dt) >= C.beat_at(p, tt + dt) - 1e-9, (tt, dt)


def test_the_dbn_coming_to_the_mapped_octave_itself_is_joined_not_a_new_grid():
    """A x2 map over a DBN at 100 (published 200); at 40 s the music becomes a 200 loop on the same grid and the DBN
    comes to 200 itself (hopping back once on the way). The out-of-range mapped tempo (400) is judged as a jump,
    confirmed, then 'joined': the line slews on as the tracker's own, no new grid, never back to 100 (the range clause
    once ended the map on the DBN's first line at 200, then published its hop back to 100: three epochs)."""
    lag = C.AubioActivation.lag_s
    a = act_train([(100.0, 0.3, 40.0), (200.0, 40.3, 90.0)], 90.0, lag, seed=31)
    clk, pubs = act_clock(a, {12.0: lambda c, tt: c.apply_integer_correction(None, None, 2.0)})
    t_sw = next(tt for tt, d, _ in pubs if tt >= 12.0 and d["bpm"] > 150.0)
    after = [(tt, d, ln) for tt, d, ln in pubs if tt >= t_sw + 1.0]
    assert len({d["epoch"] for _, d, _ in after}) == 1
    assert all(d["bpm"] > 150.0 for _, d, _ in after)
    assert after[-1][1]["bpm"] == pytest.approx(200.0, rel=0.03)
    assert 60.0 / after[-1][2][2] == pytest.approx(200.0, rel=0.03)              # the tracker's own line at 200
    st = clk.stats()
    assert st["oct_factor"] == 1.0 and st["oct_clear_joined"] == 1 and st.get("oct_clear_range", 0) == 0
    assert st["oct_maps_cleared"] == 1 and st["errors"] == 0


# -- the slow lane's octave evidence (README S4.15 "Octave evidence") --------------------------------------------------
def fed_tracker(a: np.ndarray, silent_from: float = math.inf) -> D.DbnTracker:
    """A DbnTracker fed ``a`` frame by frame (frame i sounding at (i + 1) / FPS, as act_train's); silent from
    ``silent_from`` s (no activation: a quiet room)."""
    trk = D.DbnTracker()
    for i, v in enumerate(a):
        t = (i + 1) / D.FPS
        trk.frame(float(v), t, silent=t >= silent_from)
    return trk


def test_octave_evidence_reads_the_beats_an_octave_adds_or_drops():
    """DbnTracker.octave_evidence on act_train's 100 bpm beats (beat k at 0.3 + 0.6 k s once the tracker's lag is
    out): x2 reads the beats midway against the beats (nothing there, act_train's 0.2 off-beats, as strong as the
    beats); x0.5 reads every other beat against the parity it keeps (weak beats dropped, strong ones dropped, all
    alike); nothing heard, a quiet stretch, or no octave says nothing."""
    lag = D.SETTINGS["lag_ms"] / 1000.0
    k40 = 0.3 + 0.6 * 40                                    # a strong beat (even), 6 s before the end

    def ev(f: float, t_ref: float = k40, silent_from: float = math.inf, **kw) -> dict | None:
        return fed_tracker(act_train([(100.0, 0.3, 30.0)], 30.0, lag, seed=7, **kw), silent_from).octave_evidence(
            t_ref, 0.6, f)

    assert ev(2.0, off=0.0)["ratio"] < 0.08
    e = ev(2.0)
    assert 0.2 < e["ratio"] < 0.32 and e["n"] == D.OCT_EV_N and e["floor"] < 0.06
    assert ev(2.0, off=0.8)["ratio"] == pytest.approx(1.0, abs=0.1)
    assert ev(0.5, weak=0.15)["ratio"] < 0.2                # the weak beats dropped: the audio confirms a halving
    assert ev(0.5, t_ref=k40 + 0.6, weak=0.15)["ratio"] > 4.0   # ... onto the weak ones: the strong ones dropped
    assert ev(0.5)["ratio"] == pytest.approx(1.0, abs=0.1)  # every beat alike: the audio can't tell
    assert ev(2.0, silent_from=26.0) is None                # the last 4 s quiet: too few points heard
    assert ev(1.0) is None and D.DbnTracker().octave_evidence(1.0, 0.5, 2.0) is None
    # the period the gate reads it on (and the prior's tempo): the median over the line's last 16 beats, steady under
    # 25 ms of beat-to-beat jitter
    trk = fed_tracker(act_train([(100.0, 0.3, 30.0)], 30.0, lag, seed=7, jitter_s=0.025))
    assert trk.period() == pytest.approx(0.6, rel=0.02) and D.DbnTracker().period() is None


def test_a_slow_lane_x2_needs_peaks_midway():
    """A x2 onto beats midway that carry nothing (a 100 bpm loop with no off-beats) is refused on the audio and
    nothing moves; one the tracker can't judge (too little activation) is refused too (the octave-map tests above
    apply x2s on act_train's 0.2 off-beats)."""
    lag = C.AubioActivation.lag_s
    x2 = {12.0: lambda c, tt: c.apply_integer_correction(None, None, 2.0)}
    clk, pubs = act_clock(act_train([(100.0, 0.3, 24.0)], 24.0, lag, seed=31, off=0.0), x2)
    st = clk.stats()
    assert st["corr_octave_refused_audio"] == 1 and st["last_correction"]["result"] == "octave_refused_audio"
    assert st["oct_evidence"]["result"] == "audio" and st["oct_evidence"]["ratio"] < 0.1
    assert st["oct_factor"] == 1.0 and st.get("oct_maps_applied", 0) == 0 and st["errors"] == 0
    assert all(d["bpm"] == pytest.approx(100.0, rel=0.03) for tt, d, _ in pubs if tt >= 10.0)

    def deaf(c: C.Clock, tt: float) -> None:
        c._trk.octave_evidence = lambda *a, **kw: None
        c.apply_integer_correction(None, None, 2.0)

    clk, pubs = act_clock(act_train([(100.0, 0.3, 24.0)], 24.0, lag, seed=31), {12.0: deaf})
    st = clk.stats()
    ev = st["oct_evidence"]
    assert st["corr_octave_refused_unheard"] == 1 and st["last_correction"]["result"] == "octave_refused_unheard"
    assert (ev["octave"], ev["ratio"], ev["n"], ev["result"]) == (2.0, None, 0, "unheard")
    assert ev["bpm"] == pytest.approx(100.0, rel=0.03) and pubs[-1][1]["bpm"] == pytest.approx(100.0, rel=0.03)


def test_a_halving_the_audio_cant_confirm_needs_a_plausible_tempo():
    """26's room-mic pop song in miniature: every beat of a 126 bpm loop alike (the beats a halving drops carry peaks
    as strong: the audio can't tell) and the half, 63, more than 0.8 octave under the prior's 120: refused, nothing
    moves. The call's own prior_oct (mllink sends show.yaml's) widens it: applied. At 184 (26's reggae, locked at the
    double) the half, 92, is plausible: applied."""
    lag = C.AubioActivation.lag_s

    def x05(**kw):
        return {12.0: lambda c, tt: c.apply_integer_correction(None, None, 0.5, **kw)}

    clk, pubs = act_clock(act_train([(126.0, 0.3, 24.0)], 24.0, lag, seed=31), x05())
    st = clk.stats()
    assert st["corr_octave_refused_prior"] == 1 and st["oct_factor"] == 1.0 and st["errors"] == 0
    assert st["oct_evidence"]["ratio"] > C.OCT_DROP_MAX and st["oct_evidence"]["bpm"] == pytest.approx(126, rel=0.03)
    assert all(d["bpm"] == pytest.approx(126.0, rel=0.03) for tt, d, _ in pubs if tt >= 10.0)
    clk, pubs = act_clock(act_train([(126.0, 0.3, 24.0)], 24.0, lag, seed=31), x05(prior_oct=1.2))
    assert clk.stats()["corr_applied"] == 1 and pubs[-1][1]["bpm"] == pytest.approx(63.0, rel=0.03)
    clk, pubs = act_clock(act_train([(184.0, 0.3, 24.0)], 24.0, lag, seed=31), x05())
    st = clk.stats()
    assert st["corr_applied"] == 1 and st["oct_factor"] == 0.5 and st["oct_evidence"]["result"] == "ok"
    assert st["oct_evidence"]["ratio"] > C.OCT_DROP_MAX and pubs[-1][1]["bpm"] == pytest.approx(92.0, rel=0.03)


def test_a_halving_the_audio_confirms_applies_whatever_the_tempo():
    """Every other beat of a 120 bpm loop weak (x0.3: the DBN still locks at 120; at x0.15 it takes the strong ones
    at 60 itself) and the bars' one on a strong beat: a x0.5 drops the weak ones, so the audio confirms it, and the
    half (60, under the prior's 69) applies, on the strong beats."""
    lag = C.AubioActivation.lag_s
    at = {10.45: lambda c, tt: c.one(10.3),                 # beat 20 (strong: act_train's even beats)
          12.0: lambda c, tt: c.apply_integer_correction(None, None, 0.5)}
    clk, pubs = act_clock(act_train([(120.0, 0.3, 24.0)], 24.0, lag, seed=31, weak=0.3), at)
    st = clk.stats()
    assert st["corr_applied"] == 1 and st["oct_factor"] == 0.5 and st["errors"] == 0
    assert st["oct_evidence"]["result"] == "ok" and st["oct_evidence"]["ratio"] < C.OCT_DROP_MAX
    d = pubs[-1][1]
    assert d["bpm"] == pytest.approx(60.0, rel=0.03)
    for i in range(40, 44):                                 # the strong beats are its beats, the weak ones between
        b = C.beat_at(d, 0.3 + 0.5 * i) - (0.5 if i % 2 else 0.0)
        assert abs((b + 0.5) % 1.0 - 0.5) < 0.06, i


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
    bpm_trk = 60.0 / clk._trk.line[2]                       # (the published bpm also carries its slew, if any)
    clk.tap(t_off)
    clk.tick(T0 + 12.31)
    d2 = clk.snapshot()
    b = C.beat_at(d2, t_off)
    assert abs(b - round(b)) < 1e-6 and d2["epoch"] == e0 + 1 and d2["bpm"] == pytest.approx(bpm_trk)
    assert d2["bpm"] == pytest.approx(d["bpm"], rel=0.01)
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
    # the slow lane: "the beat is half as fast". Every beat of the loop is a kick (the beats a halving drops are as
    # strong as the ones it keeps: the audio can't tell) and 60 bpm is no plausible tempo: refused, nothing moves
    # (26's room-mic pop song, 21 Oct: Beat This! heard its 125 at 61-65)
    clk.apply_integer_correction(None, None, 0.5)
    more(clk, 18.0, 6.0)
    d2 = clk.snapshot()
    st = clk.stats()
    assert d2["bpm"] == pytest.approx(120.0, rel=0.02) and st["last_correction"]["result"] == "octave_refused_prior"
    ev = st["oct_evidence"]
    assert st["corr_octave_refused_prior"] == 1 and st.get("octave_votes", 0) == 0 and st["oct_factor"] == 1.0
    assert ev["octave"] == 0.5 and ev["result"] == "prior" and ev["ratio"] > C.OCT_DROP_MAX and ev["n"] >= 8
    assert d2["beats_per_bar"] == 3 and d2["epoch"] == d["epoch"] and st["errors"] == 0
    clk2 = C.Clock(None, activation="aubio")                # no line: no correction
    clk2.apply_integer_correction(8, 4, 1.0)
    clk2.tick(T0)
    assert clk2.stats()["corr_no_grid"] == 1


def test_a_persons_half_outranks_the_slow_lanes_octave_votes_until_a_new_song():
    """A person's half/double stands against the slow lane's octave votes (they are dropped whole, counted "human")
    for HUMAN_OCT_HOLD_S or until a cold grid; octave-1 votes (a one, a bar length) still apply."""
    clk, pubs, beats = locked_clock(14.0)
    clk.half()
    more(clk, 14.0, 6.0)
    assert clk.snapshot()["bpm"] == pytest.approx(60.0, rel=0.03)
    clk.apply_integer_correction(None, None, 2.0)           # the slow lane disagrees: the person's half stands
    more(clk, 20.0, 6.0)
    st = clk.stats()
    assert clk.snapshot()["bpm"] == pytest.approx(60.0, rel=0.03)
    assert st["last_correction"]["result"] == "human" and st["corr_human"] == 1
    clk._human_oct_t -= C.HUMAN_OCT_HOLD_S                 # ten minutes on: the slow lane may set it again
    clk.apply_integer_correction(None, None, 2.0)
    more(clk, 26.0, 6.0)
    assert clk.snapshot()["bpm"] == pytest.approx(120.0, rel=0.03)
    assert clk.stats()["last_correction"]["result"] == "applied"


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


# -- the published line (README S4.15 "Publishing") --------------------------------------------------------------------
def act_train(segs: list[tuple[float, float, float]], t_end: float, lag: float, *, jitter_s: float = 0.0,
              seed: int = 5, noise_until: float = 0.0, off: float = 0.2, weak: float = 1.0) -> np.ndarray:
    """A beat activation at 100 fps, frame i sounding at (i + 1) / FPS: a 15 ms peak on every beat of ``segs``
    ((bpm, from, to) each), ``lag`` late as the activation's own lag, plus N(0, ``jitter_s``), every other beat of a
    segment (its 2nd, 4th ...) x ``weak``; a smaller one (``off``) on each off-beat; noise; random onsets with no
    pulse before ``noise_until``."""
    rng = np.random.default_rng(seed)
    n = int(round(t_end * D.FPS))
    t = (np.arange(n) + 1) / D.FPS
    a = 0.02 + 0.04 * rng.random(n)
    beats: list[float] = []
    level: list[float] = []
    for bpm, t0, t1 in segs:
        seg = list(np.arange(t0, t1, 60.0 / bpm))
        beats += seg
        level += [0.8 * (weak if i % 2 else 1.0) for i in range(len(seg))]
    for b, lv in zip(beats, level):
        bj = b + lag + (float(rng.normal(0.0, jitter_s)) if jitter_s else 0.0)
        m = np.abs(t - bj) < 0.08
        a[m] += lv * np.exp(-0.5 * ((t[m] - bj) / 0.015) ** 2)
    for b0, b1 in zip(beats, beats[1:]):
        if b1 - b0 < 1.0 and off > 0.0:
            m = np.abs(t - (0.5 * (b0 + b1) + lag)) < 0.08
            a[m] += off * np.exp(-0.5 * ((t[m] - 0.5 * (b0 + b1) - lag) / 0.015) ** 2)
    if noise_until > 0.0:
        m = t < noise_until
        a[m] = 0.02 + 0.5 * rng.random(int(m.sum())) ** 4
    return np.clip(a, 0.0, 1.0)


def act_clock(a: np.ndarray, at: dict | None = None,
              level: np.ndarray | None = None) -> tuple[C.Clock, list[tuple[float, dict, tuple]]]:
    """A dbn Clock fed ``a`` one activation frame at a time (``Clock._frame``: the dbn path from the activation on),
    published on every change and ticked every 10 ms as show-rx does; ``at``: {time: fn(clock, t)} actions; ``level``:
    dsp's gate per frame (``Clock.set_level``: a quiet room's frames are silent, the first after it fresh, as
    ``_hop``/``_inline`` make them). Returns the clock and every body with its time and the tracker's own line then."""
    pubs: list[tuple[float, dict, tuple]] = []
    holder: list[C.Clock] = []

    def pub(m: dict) -> None:
        d = m["data"]
        pubs.append((d["t_ms"] / 1000.0, d, holder[0]._trk.line))

    clk = C.Clock(pub, activation="aubio")
    holder.append(clk)
    todo = dict(at or {})
    for i, v in enumerate(a):
        t = (i + 1) / D.FPS
        for ta in sorted(x for x in todo if x <= t):
            todo.pop(ta)(clk, t)
        if level is not None:
            clk.set_level(float(level[i]), t)
            if clk._quiet:
                clk._f_silent, v = True, 0.0
            elif clk._act_fresh:
                clk._f_fresh, clk._act_fresh = True, False
        if clk._frame(float(v), t):
            clk._emit(t)
        clk.tick(t)
    return clk, pubs


_SYN: dict = {}


def synth_run() -> tuple[C.Clock, list[tuple[float, dict, tuple]]]:
    """Room noise (random onsets) to 6 s, then 120 bpm; at 18.2 s the beat moves 0.4 of a beat later (the tracker
    re-phases); at 26 s 150 bpm (a tempo jump). A human "one" at 14.0 s (bar_conf 0.8)."""
    if "run" not in _SYN:
        lag = C.AubioActivation.lag_s
        a = act_train([(120.0, 6.3, 18.0), (120.0, 18.2, 26.0), (150.0, 26.0, 36.0)], 36.0, lag, noise_until=6.0)
        _SYN["run"] = act_clock(a, {14.0: lambda c, t: c.one(t)})
    return _SYN["run"]


def epochs(pubs, t0: float, t1: float) -> set:
    return {d["epoch"] for t, d, _ in pubs if t0 <= t < t1}


def test_the_published_count_only_goes_forward_across_a_rephase_and_a_tempo_jump():
    clk, pubs = synth_run()
    st = clk.stats()
    assert st["dbn"]["rephases"] >= 2 and st["pub_far_waits"] >= 1 and st["errors"] == 0
    t_gated = next(t for t, d, _ in pubs if d["gate"] == "beat")
    assert t_gated < 12.0
    worst_slew = 0.0
    for (_, p, _), (t, q, _) in zip(pubs, pubs[1:]):
        if not p["bpm"]:
            continue
        for dt in (0.0, 0.05, 0.1):                     # at the switch, and a consumer that reads it later
            d = C.beat_at(q, t + dt) - C.beat_at(p, t + dt)
            assert d >= -1e-9, (t, dt, d)               # never back
            assert d < 1.0 or q["epoch"] != p["epoch"], (t, dt, d)   # a whole beat only with a new epoch
        if t > t_gated and p["epoch"] == q["epoch"] and p["gate"] == q["gate"] == "beat":
            worst_slew = max(worst_slew, C.beat_at(q, t) - C.beat_at(p, t))
    assert worst_slew < 0.03, worst_slew                # slewed, not stepped (15 ms at 120 bpm)
    e = max(epochs(pubs, 11.0, 12.0))
    assert epochs(pubs, 12.0, 26.0) == {e}              # the re-phase at 18.2 s: no epoch
    assert e + 1 <= max(epochs(pubs, 26.0, 36.0)) <= e + 2     # the tempo jump: a new grid
    t_end, last, _ = pubs[-1]
    assert last["bpm"] == pytest.approx(150.0, rel=0.03) and last["gate"] == "beat"


def line_next(line: tuple, q: float) -> tuple[int, float]:
    k0, t0, T = line
    k = math.ceil(k0 + (q - t0) / T - 1e-9)
    return k, t0 + (k - k0) * T


def body_line(d: dict) -> tuple:
    return d["beat_at_anchor"], d["anchor_ms"] / 1000.0, 60.0 / d["bpm"]


def jitter_ms(seq: list[tuple[float, tuple]], lead: float) -> list[float]:
    """|change| of each line's next beat after t + lead, between consecutive lines that differ and name the same
    beat (clock.next_beat at the moment of the switch)."""
    out = []
    for (_, a), (t, b) in zip(seq, seq[1:]):
        if a == b:
            continue
        ka, ta = line_next(a, t + lead)
        kb, tb = line_next(b, t + lead)
        if ka == kb:
            out.append(abs(tb - ta) * 1000.0)
    return out


def crossings(seq: list[tuple[float, tuple]], t_end: float) -> list[float]:
    """The integer-beat crossings of the line in force (each line from its time to the next one's), a crossing within
    0.35 of a period after the last one being the same beat re-phased (run_stack's flashes, artnet_out's rule)."""
    out: list[float] = []
    for i, (t, ln) in enumerate(seq):
        t1 = seq[i + 1][0] if i + 1 < len(seq) else t_end
        k, tk = line_next(ln, t)
        while tk < t1:
            if not out or tk > out[-1] + 0.35 * ln[2]:
                out.append(tk)
            k += 1
            tk = ln[1] + (k - ln[0]) * ln[2]
    return out


def spacing_ms(fl: list[float], T: float) -> np.ndarray:
    """|t[k+1] - 2 t[k] + t[k-1]| (ms) over consecutive fired beats about a period apart: how steady they are."""
    f = np.asarray(fl)
    iv = np.diff(f)
    ok = (np.abs(iv[1:] / T - 1.0) < 0.3) & (np.abs(iv[:-1] / T - 1.0) < 0.3)
    return np.abs(np.diff(iv))[ok] * 1000.0


def test_next_beat_is_smooth_under_a_jittered_dbn_beat_train():
    """The DBN's beats 12 ms rms off a steady 120 bpm: the tracker's own line (re-anchored on every DBN beat, what
    was published before) jitters with them; the published line's next beat moves by about half as much at Hue's
    +99 ms and the screens' +300 ms, the beats it fires are as much steadier, and they land where the raw line's do."""
    lag = C.AubioActivation.lag_s
    clk, pubs = act_clock(act_train([(120.0, 0.3, 40.0)], 40.0, lag, jitter_s=0.012, seed=9))
    gated = [(t, d, ln) for t, d, ln in pubs if t >= 10.0 and d["gate"] == "beat"]
    assert len(gated) > 250 and gated[-1][0] > 39.0
    pub = [(t, body_line(d)) for t, d, _ in gated]
    raw = [(t, ln) for t, _, ln in gated]
    for lead in (0.099, 0.300):
        jp, jr = np.percentile(jitter_ms(pub, lead), 95), np.percentile(jitter_ms(raw, lead), 95)
        assert jp <= 0.65 * jr and jp < 10.0, (lead, jp, jr)
    fp, fr = crossings(pub, 40.0), crossings(raw, 40.0)
    sp, sr = np.percentile(spacing_ms(fp, 0.5), 95), np.percentile(spacing_ms(fr, 0.5), 95)
    assert sp <= 0.85 * sr and sp < 10.0, (sp, sr)      # the fired beats' spacing: steadier
    err = {k: np.array([tk - (round((tk - 0.3) / 0.5) * 0.5 + 0.3) for tk in f if tk > 12.0]) * 1000.0
           for k, f in (("pub", fp), ("raw", fr))}
    med = {k: float(np.median(v)) for k, v in err.items()}
    assert abs(med["pub"] - med["raw"]) < 3.0, med      # on the beat where the raw line is (the DBN's own offset)
    assert np.percentile(np.abs(err["pub"] - med["pub"]), 95) < 25.0
    assert clk.stats()["errors"] == 0


def test_the_downbeat_stays_put_across_a_relock_and_a_tempo_jump():
    clk, pubs = synth_run()
    barred = [(t, d) for t, d, _ in pubs if d["bar_conf"] > 0.0]
    assert barred and barred[0][0] < 12.0
    t_one = 14.0
    after_one = [(t, d) for t, d in barred if t > t_one + 0.1]
    assert len({d["bar_anchor_beat"] % 4 for _, d in after_one}) == 1      # the re-phase and the jump: kept
    assert len({d["bar_anchor_beat"] % 4 for t, d in barred if t < t_one}) == 1
    assert all(d["bar_conf"] >= 0.8 for t, d in after_one if t < 26.0)    # a re-lock keeps bar_conf
    assert after_one[-1][1]["bar_conf"] == pytest.approx(C.DBN_NEW_BAR_CONF)   # a new grid: a guess again
    assert all(d["beats_per_bar"] == 4 for _, d in barred)


def test_the_epoch_moves_only_on_a_real_tempo_jump():
    """Random onsets (never gated), then 120 bpm, a 4 % step to 125, a 25 % jump to 156: one epoch for the jump,
    none for the noise's wandering line, the music's first grid after it or the small step."""
    lag = C.AubioActivation.lag_s
    a = act_train([(120.0, 8.3, 20.0), (125.0, 20.0, 30.0), (156.0, 30.0, 40.0)], 40.0, lag, noise_until=8.0,
                  seed=12)
    clk, pubs = act_clock(a)
    assert not any(d["gate"] != "none" for t, d, _ in pubs if t < 8.0)    # the noise is never gated
    assert epochs(pubs, 0.0, 8.0) <= {0, 1}
    t_gated = next(t for t, d, _ in pubs if d["gate"] == "beat")
    assert t_gated < 16.0
    e = max(epochs(pubs, t_gated, t_gated + 0.01))
    assert epochs(pubs, t_gated, 30.0) == {e}          # the 4 % step at 20 s is a slew
    assert e + 1 <= max(epochs(pubs, 30.0, 40.0)) <= e + 2
    last = pubs[-1][1]
    assert last["bpm"] == pytest.approx(156.0, rel=0.03)
    assert clk.stats()["pub_slews"] > 50


def test_a_song_after_a_quiet_room_is_one_new_grid_even_at_a_near_tempo():
    """120 bpm (a human one at 14 s), 4 s of silence (dsp's gate 0: a quiet room 3 s in), then 118 bpm on another
    phase, within the 8 % that would make it a tempo jump: one new epoch, at the first gate after the quiet; the
    count never goes back; the bars keep their anchor, bar_conf a guess again (_dbn_cold_grid)."""
    lag = C.AubioActivation.lag_s
    a = act_train([(120.0, 0.3, 30.0), (118.0, 34.21, 50.0)], 50.0, lag, seed=23)
    t = (np.arange(len(a)) + 1) / D.FPS
    sil = (t >= 30.0) & (t < 34.0)
    a[sil] = 0.0
    clk, pubs = act_clock(a, {14.0: lambda c, t: c.one(t)}, level=np.where(sil, 0.0, 0.4))
    st = clk.stats()
    assert st["quiet_starts"] == 1 and st.get("pub_cold_grids") == 1 and st["errors"] == 0
    assert all(d["gate"] == "none" for t, d, _ in pubs if 33.1 <= t < 34.0)
    t_up = next(t for t, d, _ in pubs if t >= 34.0 and d["gate"] != "none")
    e = max(epochs(pubs, 12.0, 30.0))
    assert epochs(pubs, 12.0, t_up) == {e}
    assert epochs(pubs, t_up, 50.0) == {e + 1}
    for (_, p, _), (t, q, _) in zip(pubs, pubs[1:]):
        if p["bpm"]:
            assert C.beat_at(q, t) >= C.beat_at(p, t) - 1e-9, t
    before = next(d for t, d, _ in reversed(pubs) if t < 30.0)
    first = next(d for t, d, _ in pubs if t >= t_up)
    assert before["bar_conf"] >= 0.8 and first["bar_conf"] == pytest.approx(C.DBN_NEW_BAR_CONF)
    assert first["bar_anchor_beat"] % 4 == before["bar_anchor_beat"] % 4
    assert pubs[-1][1]["bpm"] == pytest.approx(118.0, rel=0.03)


def test_a_slew_is_judged_again_at_a_beat_the_dbn_missed():
    """The tracker's line moves only on a DBN beat. A beat of it that passes with no DBN beat (a miss) judges the
    published slew again (_dbn_missed, as legacy after every beat), so a slew set for one beat does not run on until
    the next DBN beat; the count stays continuous; a far tracker tempo is left to the tracker's own line updates."""
    lag = C.AubioActivation.lag_s
    clk, _ = act_clock(act_train([(120.0, 0.3, 12.0)], 12.0, lag))
    trk = clk._trk
    assert clk.snapshot()["gate"] == "beat"
    k0, t0, T = trk.line
    u = trk.t_last
    pp = clk._pub_beat(u)                               # a slew in force: 10 % fast, through the count at u
    clk._set_pub(math.floor(pp), u - (pp - math.floor(pp)) * T / 1.1, 60.0 / T * 1.1)
    old = (clk._pub_k, clk._pub_t, clk._pub_bpm)
    u2 = u + 1.4 * T                                    # a beat and a bit on with no DBN beat

    def err(x: float) -> float:                         # published - tracker's line, in beats, at the nearest beat
        e = clk._pub_beat(x) - (k0 + (x - t0) / T)
        return e - round(e)

    w = (u2, u2 + C.DBN_PIVOT_S)
    b_old, e_old = [clk._pub_beat(x) for x in w], err(u2 + 2 * T)
    trk.events.append((trk._k_judged + 1, "miss", 0.0))
    clk._dbn_missed(u2)
    trk.events.clear()
    assert clk.stats().get("pub_resyncs") == 1
    assert 60.0 / T * 0.9 - 1e-9 <= clk._pub_bpm < 60.0 / T      # ahead of the tracker's line: it slows, <= 10 %
    b_new = [clk._pub_beat(x) for x in w]
    assert b_new[0] >= b_old[0] - 1e-9 and b_new[1] == pytest.approx(b_old[1], abs=1e-9)   # never back
    assert e_old > 0.3 and abs(err(u2 + 2 * T)) < 0.25 * e_old    # closed, where the old slew ran on 0.34 ahead
    pub = (clk._pub_k, clk._pub_t, clk._pub_bpm)
    assert pub != old
    trk._line = (k0, t0, T / 1.2)                       # the tracker 20 % faster: a jump, not a slew to re-judge
    far0 = clk._pub_far
    trk.events.append((trk._k_judged + 1, "miss", 0.0))
    clk._dbn_missed(u2 + T)
    trk.events.clear()
    assert (clk._pub_k, clk._pub_t, clk._pub_bpm) == pub and clk._pub_far == far0
    assert clk.stats().get("pub_resyncs") == 1


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
        elif u < 0.006:
            trk.tap(tt + float(rng.normal(0, 0.1)), [tt - 1.5, tt - 1.0, tt - 0.5, tt] if rng.random() < 0.5 else None)
        elif u < 0.007:
            trk.coast(tt + 1.0)
        if rng.random() < 0.5:
            trk.events.clear()
        assert trk.state in ("searching", "locked", "holding", "free") and 0.0 <= trk.conf <= 1.0
        assert trk.line is None or 60.0 / 200.0 * 0.85 <= trk.line[2] <= 60.0 / 45.0 * 1.15
    assert trk.counts["dbn_beats"] > 0
