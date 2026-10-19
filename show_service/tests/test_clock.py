"""clock.py: the shared plumbing (consumer helpers, sliding-DFT tempogram, peaks, published line, corrections, hot-path
safety) gets real tests; the tracker gets synthetic-signal "does it work" tests at the spec's checkpoints (§5.3):
lock in ~6 s with < 15 ms phase error, a 120 -> 124 bpm step, half-time without octave flapping, a 16-bar breakdown
held and re-locked within 4 beats, random onsets and rubato kept below conf 0.4. Plus the cost per 10 ms.

Signals are synthesised at 48 kHz and fed as receiver blocks (n0, t0_host_s) with ticks every 10 ms of stream time,
so every timing claim is checked against the true onset times, through what was *published* at least 50 ms before
each beat (as a consumer with a lead would see it).

Every clock here runs as production does: the activation on its show-beat thread (the default). The feeders call
``wait_beat()`` after each block, so show-beat keeps up with an unpaced feed (live, the audio paces it) and the frames
are exactly the inline ones (tests/test_clock_thread.py checks that, and the hand-off itself).

These are the legacy estimator's tests (``Clock(estimator="legacy")``, the rollback since 19 Oct): its thresholds,
counts and internals. The dbn estimator (the default) has its own, in tests/test_beatdbn.py; the estimator-free
tests here (the consumer helpers, snapshot reads, errors) run on the default.
"""

from __future__ import annotations

import math
import threading
import time

import numpy as np
import pytest

from show_service import clock as C
from show_service.contract import AUDIO_GAP, AUDIO_RESUME, AUDIO_SEGMENT, MUSIC_CLOCK

FS = 48000
T0 = 5000.0                     # host time of sample 0


def _have(name: str) -> bool:
    try:
        C.make_activation(name)
        return True
    except Exception:
        return False


HAVE_MADMOM = _have("madmom")
SOURCES = [pytest.param("madmom", marks=pytest.mark.skipif(not HAVE_MADMOM, reason="madmom not installed")), "aubio"]


# -- synthetic audio ---------------------------------------------------------------------------------------------------
def drum(x: np.ndarray, t: float, amp: float = 0.6, kind: str = "kick") -> None:
    i = int(round(t * FS))
    if i >= len(x):
        return
    rng = np.random.default_rng(i)
    if kind == "kick":
        n = int(0.15 * FS)
        m = np.arange(n)
        f = 50 + 80 * np.exp(-m / (0.02 * FS))
        s = amp * np.exp(-m / (0.05 * FS)) * np.sin(2 * np.pi * np.cumsum(f) / FS)
        c = int(0.004 * FS)
        s[:c] += amp * 0.5 * rng.standard_normal(c) * np.exp(-np.arange(c) / (0.001 * FS))
    elif kind == "snare":
        n = int(0.12 * FS)
        m = np.arange(n)
        s = amp * np.exp(-m / (0.03 * FS)) * (0.6 * rng.standard_normal(n) + 0.4 * np.sin(2 * np.pi * 190 * m / FS))
    else:
        n = int(0.04 * FS)
        s = amp * np.exp(-np.arange(n) / (0.008 * FS)) * rng.standard_normal(n)
    e = min(len(x), i + n)
    x[i:e] += s[:e - i]


def click(x: np.ndarray, t: float, amp: float = 0.5) -> None:
    i = int(round(t * FS))
    n = int(0.01 * FS)
    m = np.arange(n)
    e = min(len(x), i + n)
    x[i:e] += (amp * np.exp(-m / (0.002 * FS)) * np.sin(2 * np.pi * 1000 * m / FS))[:e - i]


def pads(x: np.ndarray, t0: float, t1: float, amp: float = 0.25) -> None:
    i0, i1 = int(t0 * FS), int(t1 * FS)
    m = np.arange(i1 - i0) / FS
    env = np.minimum(1, m / 1.0) * np.minimum(1, (t1 - t0 - m) / 1.0)
    s = sum(np.sin(2 * np.pi * f * m + p) for f, p in ((220, 0), (277.2, 1), (329.6, 2), (440, 3)))
    x[i0:i1] += amp * env * s / 4


def tone(x: np.ndarray, t: float, f0: float, amp: float = 0.3, dur: float = 1.2) -> None:
    i = int(round(t * FS))
    n = int(dur * FS)
    m = np.arange(n)
    s = sum(amp / h * np.sin(2 * np.pi * f0 * h * m / FS) for h in (1, 2, 3, 4)) * np.exp(-m / (0.35 * FS))
    a = int(0.003 * FS)
    s[:a] *= np.linspace(0, 1, a)
    e = min(len(x), i + n)
    x[i:e] += s[:e - i]


def finish(x: np.ndarray, seed: int = 3) -> np.ndarray:
    return (x + 0.002 * np.random.default_rng(seed).standard_normal(len(x))).astype(np.float32)


class Run:
    """A clock fed with a whole signal, keeping every published body."""

    def __init__(self, x: np.ndarray, source: str = "auto", block: int = 256, **kw) -> None:
        self.pubs: list[dict] = []
        self.msgs: list[dict] = []

        def pub(m: dict) -> None:
            self.msgs.append(m)
            self.pubs.append(m["data"])

        kw.setdefault("estimator", "legacy")
        self.clk = C.Clock(pub, activation=source, **kw)
        self.cpu_s = 0.0
        self.audio_s = len(x) / FS
        nt = T0
        c0 = time.process_time()
        for n0 in range(0, len(x), block):
            blk = x[n0:n0 + block]
            t0 = T0 + n0 / FS
            self.clk.process("room-mic", n0, t0, blk.reshape(-1, 1), FS, AUDIO_SEGMENT if n0 == 0 else 0)
            self.clk.wait_beat()
            tend = t0 + len(blk) / FS
            while nt <= tend:
                self.clk.tick(nt)
                nt += 0.01
        self.cpu_s = time.process_time() - c0
        self.ts = np.array([p["t_ms"] for p in self.pubs]) / 1000.0

    def at(self, t: float) -> dict:
        """The body a consumer held at host time t."""
        return self.pubs[int(np.searchsorted(self.ts, t, side="right")) - 1]

    def err_ms(self, beats: list[float], lead: float = 0.05) -> list[tuple[float, float, dict]]:
        """(true beat time, phase error ms, body) using what was published >= ``lead`` before each beat."""
        out = []
        for tb in beats:
            if tb - lead < self.ts[0]:
                continue
            p = self.at(tb - lead)
            if p["bpm"] <= 0:
                continue
            b = C.beat_at(p, tb)
            out.append((tb, (b - round(b)) * 60.0 / p["bpm"] * 1000.0, p))
        return out

    def first(self, cond) -> float | None:
        for p in self.pubs:
            if cond(p):
                return p["t_ms"] / 1000.0 - T0
        return None


def kick_loop(beats_t: list[float], dur: float, snare: bool = True) -> np.ndarray:
    x = np.zeros(int(dur * FS))
    for i, t in enumerate(beats_t):
        drum(x, t, 0.6, "kick")
        if snare and i % 2:
            drum(x, t, 0.35, "snare")
    return x


def grid(bpm: float, t0: float, t1: float) -> list[float]:
    return list(np.arange(t0, t1, 60.0 / bpm))


# -- consumer helpers (shared plumbing) --------------------------------------------------------------------------------
def body(**kw) -> dict:
    d = {"bpm": 120.0, "anchor_ms": 10_000.0, "beat_at_anchor": 40, "beats_per_bar": 4, "bar_anchor_beat": 32,
         "phrase_bars": 8, "conf": 0.9, "bar_conf": 0.6, "phrase_conf": 0.1, "state": "locked", "gate": "beat",
         "epoch": 1, "stability": 0.8, "source": "test", "t_ms": 10_000.0}
    d.update(kw)
    return d


def test_beat_at_is_the_link_formula():
    d = body()
    assert C.beat_at(d, 10.0) == 40.0
    assert C.beat_at(d, 10.25) == pytest.approx(40.5)
    assert C.beat_at(d, 9.0) == pytest.approx(38.0)
    assert math.isnan(C.beat_at(body(bpm=0.0), 10.0))


def test_next_beat_respects_lead():
    d = body()
    assert C.next_beat(d, 10.0) == (40, pytest.approx(10.0))       # at or after: beat 40 itself
    assert C.next_beat(d, 10.001) == (41, pytest.approx(10.5))
    k, t = C.next_beat(d, 10.1, lead_s=0.6)           # fire time t_k - 0.6 must be at or after 10.1 -> beat >= 10.7
    assert k == 42 and t == pytest.approx(11.0)
    assert t - 0.6 >= 10.1
    assert C.next_beat(body(bpm=0.0), 10.0) is None


def test_clock_reads_are_snapshot_based_and_gated():
    clk = C.Clock(None, activation="aubio")
    assert clk.next_beat(1.0) is None and math.isnan(clk.beat_at(1.0)) and clk.beats_per_bar == 4
    clk._snapshot = body(gate="dmx")                  # what show-rx would have swapped in
    assert clk.next_beat(10.001) is None              # below the beat gate: no beat-locked pulses
    assert clk.next_beat(10.001, gate="dmx") == pytest.approx(10.5)
    assert clk.next_beat_k(10.001) == (41, pytest.approx(10.5))
    clk._snapshot = body(gate="beat", beats_per_bar=3)
    assert clk.next_beat(10.001) == pytest.approx(10.5) and clk.beats_per_bar == 3
    out = []
    th = threading.Thread(target=lambda: out.append((clk.next_beat(10.2, 0.05), clk.beat_at(10.25))))
    th.start()
    th.join()
    assert out == [(pytest.approx(10.5), pytest.approx(40.5))]
    st = clk.state()
    assert st["clock"] is clk.snapshot() and "cost_ms_per_10ms" in st["stats"]


def test_position_at_bar_and_phrase():
    d = body()                                        # bar anchor 32 = a phrase start; beat 40 = bar 2, beat 0
    p = C.position_at(d, 10.0)
    assert p["bar"] == 2 and p["beat_in_bar"] == pytest.approx(0.0) and p["bar_in_phrase"] == 2 and p["phrase"] == 0
    p = C.position_at(d, 10.0 + 0.5 * 25)              # beat 65 = bar 8 (next phrase), beat 1
    assert p["bar"] == 8 and p["bar_in_phrase"] == 0 and p["phrase"] == 1
    assert p["beat_in_bar"] == pytest.approx(1.0)
    assert C.position_at(body(bpm=0.0), 1.0) is None


# -- tempogram (shared plumbing) ---------------------------------------------------------------------------------------
def brute(x: np.ndarray, n_last: int, N: int, bpm: np.ndarray, fps: float) -> np.ndarray:
    j = np.arange(n_last - N + 1, n_last + 1)
    m = j - j[0]
    w = np.sin(np.pi * m / (2 * N)) ** 2
    xv = np.where(j >= 0, x[np.clip(j, 0, None)], 0.0)
    om = bpm / 60.0 / fps * 2 * np.pi
    X = (xv * w)[None, :] * np.exp(-1j * om[:, None] * j[None, :])
    return np.concatenate(([float(np.sum(xv * w))], X.sum(axis=1)))


@pytest.mark.parametrize("n", [250, 600, 5000])        # filling, full, and past the phasor re-derivation (4096)
def test_sliding_dft_matches_the_windowed_dft(n):
    rng = np.random.default_rng(n)
    x = rng.random(n)
    tg = C.Tempogram(bpm_hi=240.0)
    for i, v in enumerate(x):
        tg.push(float(v), i / 100.0)
    X = tg.spectrum()
    B = brute(x, n - 1, tg.N, tg.bpm, 100.0)
    assert np.allclose(X, B, rtol=1e-8, atol=1e-8)


def test_tempogram_clarity_and_phase_of_a_pulse_train():
    tg = C.Tempogram(bpm_hi=240.0)
    x = np.zeros(900)
    x[np.arange(13, 900, 50)] = 1.0                   # 120 bpm at 100 fps, beats at frame 13 + 50 k
    for i, v in enumerate(x):
        tg.push(float(v), 100.0 + i / 100.0)
    X = tg.spectrum()
    R = np.abs(X[1:]) / X[0].real
    assert tg.clarity(X, 120.0) == pytest.approx(1.0, abs=1e-6)
    assert tg.clarity(X, 60.0) < 0.2                  # sub-harmonics cancel; harmonics (240) don't
    loc = [i for i in range(1, len(R) - 1) if R[i] >= R[i - 1] and R[i] >= R[i + 1]]
    assert 120 in [int(tg.bpm[i]) for i in loc]
    tb = tg.beat_time(X, int(np.argmin(np.abs(tg.bpm - 120.0))))
    q = (tb - 100.13) / 0.5                           # a beat time on the grid
    assert abs(q - round(q)) < 0.01


def test_peaks_find_bumps_with_subframe_timing():
    pk = C._Peaks()
    a = np.zeros(400)
    for c in (50, 150.4, 250.7):                      # triangular bumps, centre in frames
        for i in range(int(c) - 3, int(c) + 5):
            a[i] = max(a[i], 1.0 - abs(i - c) / 2.5)
    found = []
    for i, v in enumerate(a):
        _, p = pk.push(float(v), i / 100.0, 0.05)
        if p:
            found.append(p)
    assert len(found) == 3
    for (t, s, h), c in zip(found, (50, 150.4, 250.7)):
        assert abs(t - c / 100.0) < 0.004 and h > 0.5 and 0.5 < s <= 1.5


def test_peaks_ignore_noise_under_the_floor():
    pk = C._Peaks()
    rng = np.random.default_rng(1)
    n = 0
    for i in range(1000):
        v, p = pk.push(float(0.01 * rng.random()), i / 100.0, 0.03)
        assert v == 0.0                               # nothing reaches the tempogram either
        n += p is not None
    assert n == 0


# -- the whole clock: lock, accuracy, the published line ---------------------------------------------------------------
_cache: dict = {}


def lock_run(source: str, block: int = 256) -> tuple[Run, list[float]]:
    key = ("lock", source, block)
    if key not in _cache:
        beats = grid(120.0, 0.31, 23.8)
        x = np.zeros(int(24 * FS))
        for t in beats:
            click(x, t)
            drum(x, t, 0.5, "kick")
        _cache[key] = (Run(finish(x), source, block=block), [T0 + t for t in beats])
    return _cache[key]


@pytest.mark.parametrize("source", SOURCES)
def test_click_track_locks_fast_and_lands_within_15ms(source):
    r, beats = lock_run(source)
    assert r.clk.act.name == source
    t_lock = r.first(lambda p: p["state"] == "locked")
    assert t_lock is not None and t_lock <= 6.5
    t_gate = r.first(lambda p: p["gate"] == "beat")
    assert t_gate is not None and t_gate <= 9.0
    steady = [(tb, e) for tb, e, p in r.err_ms(beats) if tb - T0 >= 10.0]
    assert len(steady) >= 25
    assert max(abs(e) for _, e in steady) < 15.0
    last = r.pubs[-1]
    assert last["bpm"] == pytest.approx(120.0, abs=0.3)
    assert last["conf"] >= C.BEAT_ON and last["state"] == "locked"
    st = r.clk.stats()
    assert st["seeds"] == 1 and st["errors"] == 0


def test_published_body_shape_and_cadence():
    r, beats = lock_run(SOURCES[0].values[0] if HAVE_MADMOM else "aubio")
    assert all(m["type"] == MUSIC_CLOCK for m in r.msgs)
    assert set(r.pubs[-1]) == set(C.ClockData.__annotations__)
    for k, typ in (("bpm", float), ("anchor_ms", float), ("beat_at_anchor", int), ("bar_anchor_beat", int),
                   ("beats_per_bar", int), ("phrase_bars", int), ("epoch", int), ("state", str), ("gate", str)):
        assert isinstance(r.pubs[-1][k], typ), k
    assert r.pubs[0]["state"] == "searching" and r.pubs[0]["bpm"] == 0.0
    # 10 Hz heartbeat (plus re-anchors): never a gap over 0.1 s of stream time
    assert np.max(np.diff(r.ts)) <= 0.1 + 1e-6
    assert len(r.pubs) >= 10 * 23
    assert r.clk.snapshot() is r.pubs[-1]


def test_published_line_never_jumps_within_an_epoch():
    r, beats = lock_run(SOURCES[0].values[0] if HAVE_MADMOM else "aubio")
    locked = [p for p in r.pubs if p["bpm"] > 0]
    for p, q in zip(locked, locked[1:]):
        t = q["t_ms"] / 1000.0
        d = C.beat_at(q, t) - C.beat_at(p, t)
        assert abs(d) <= 0.5 + 1e-9                   # never more than half a beat, whatever happens
        if p["epoch"] == q["epoch"]:
            assert abs(d) < 0.02, (t - T0, d)         # slewed, not stepped (0.02 beat = 10 ms at 120 bpm)


def test_real_receiver_block_size():
    r, beats = lock_run("aubio", block=128)
    steady = [e for tb, e, p in r.err_ms(beats) if tb - T0 >= 10.0]
    assert steady and max(abs(e) for e in steady) < 15.0


def test_cost_per_10ms_report(capsys):
    lines = []
    for source in ("madmom", "aubio"):
        if source == "madmom" and not HAVE_MADMOM:
            continue
        r, _ = lock_run(source)
        st = r.clk.stats()["cost_ms_per_10ms"]
        mean = r.cpu_s * 1000.0 / (r.audio_s * 100.0)
        lines.append(f"clock[{source}] cpu per 10 ms of audio: mean {mean:.3f} ms, per-block p50 {st['p50']} "
                     f"p95 {st['p95']} max {st['max']}")
        assert mean < (6.0 if source == "madmom" else 1.5)     # generous: the host is rendering 4K
    with capsys.disabled():
        print("\n" + "\n".join(lines))


# -- the spec's checkpoints --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("source", SOURCES)
def test_tempo_step_120_to_124_relocks(source):
    b1 = grid(120.0, 0.3, 22.0)
    b2 = grid(124.0, b1[-1] + 60 / 124.0, 43.8)
    beats = b1 + b2
    r = Run(finish(kick_loop(beats, 44.0)), source)
    t_step = T0 + b2[0]
    errs = r.err_ms([T0 + t for t in beats])
    after = [(tb - t_step, e) for tb, e, p in errs if tb >= t_step]
    # within 16 beats (~8 s) the published phase is back within 15 ms and stays there
    assert max(abs(e) for dt, e in after if dt >= 8.0) < 15.0
    assert r.pubs[-1]["bpm"] == pytest.approx(124.0, abs=0.5)
    st = r.clk.stats()
    assert st["seeds"] == 1 and "cold_searches" not in st     # tracked, not re-searched
    assert r.pubs[-1]["state"] == "locked"


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("snare", [False, True])
def test_half_time_does_not_flap_octave(source, snare):
    beats = grid(120.0, 0.3, 39.8)
    x = np.zeros(int(40 * FS))
    for i, t in enumerate(beats):
        if i % 2 == 0:
            drum(x, t, 0.6, "kick")                   # kick on 1 and 3 only
        if snare and i % 4 == 2:
            drum(x, t, 0.3, "snare")
    r = Run(finish(x), source)
    st = r.clk.stats()
    assert st["seeds"] == 1 and "cold_searches" not in st
    assert st.get("octave_changes", 0) == 0
    bpms = [p["bpm"] for p in r.pubs if p["t_ms"] / 1000.0 - T0 > 10.0]
    assert max(bpms) / min(bpms) < 1.05               # one octave, the whole way
    assert r.pubs[-1]["state"] == "locked"
    kicks = [T0 + t for i, t in enumerate(beats) if i % 2 == 0]
    steady = [e for tb, e, p in r.err_ms(kicks) if tb - T0 > 12.0]
    assert max(abs(e) for e in steady) < 15.0         # whichever octave, the kicks land on beats


def breakdown_signal(silent: bool) -> tuple[np.ndarray, list[float], float]:
    """8 bars of beat, 16 bars of breakdown (pads, or silence), 8 bars of beat on the same grid, at 120 bpm."""
    beats = [0.3 + 0.5 * b for b in range(32 * 4)]
    x = np.zeros(int((0.3 + 32 * 2.0 + 1.0) * FS))
    for b, t in enumerate(beats):
        if not 8 * 4 <= b < 24 * 4:
            drum(x, t, 0.6, "kick")
            if b % 2:
                drum(x, t, 0.35, "snare")
    if not silent:
        pads(x, 0.3 + 16.0 - 0.2, 0.3 + 48.0 + 0.5)
    return finish(x), beats, 0.3 + 48.0


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("silent", [False, True])
def test_breakdown_holds_the_grid_and_relocks_within_4_beats(source, silent):
    x, beats, t_back = breakdown_signal(silent)
    r = Run(x, source)
    st = r.clk.stats()
    assert st["seeds"] == 1 and "cold_searches" not in st     # held, never re-searched
    mid = r.at(T0 + 0.3 + 32.0)
    assert mid["state"] == "holding" and mid["bpm"] == pytest.approx(120.0, abs=0.3)
    assert mid["gate"] == "none"                     # confidence decays, used only for gating
    # the beat count carried through: beat numbers before and after differ by exactly the beats elapsed
    pre = r.err_ms([T0 + beats[30]])[0][2]
    b_pre = round(C.beat_at(pre, T0 + beats[30]))
    after = r.err_ms([T0 + t for t in beats if t >= t_back])
    first_tb, _, p_first = after[0]
    assert round(C.beat_at(p_first, first_tb)) == b_pre + round((first_tb - T0 - beats[30]) / 0.5)
    # within 4 beats of the return: locked again, on the grid, and the epoch never moved
    p4 = r.at(T0 + t_back + 4 * 0.5)
    assert p4["state"] == "locked" and p4["gate"] in ("beat", "dmx")
    assert max(abs(e) for tb, e, p in after) < 15.0
    assert p4["epoch"] == pre["epoch"]
    assert (p4["bar_anchor_beat"] - pre["bar_anchor_beat"]) % 4 == 0      # bar phase kept (a phrase may move)


@pytest.mark.parametrize("source", SOURCES)
def test_random_onsets_keep_conf_low(source):
    rng = np.random.default_rng(7)
    x = np.zeros(int(36 * FS))
    t = 0.2
    while t < 35.7:
        drum(x, t, float(rng.uniform(0.2, 0.7)), str(rng.choice(["kick", "snare", "hat"])))
        t += float(rng.exponential(1 / 3.0))
    r = Run(finish(x), source)
    assert max(p["conf"] for p in r.pubs) < 0.4
    assert all(p["gate"] == "none" for p in r.pubs)


@pytest.mark.parametrize("source", SOURCES)
def test_rubato_keeps_conf_low(source):
    rng = np.random.default_rng(11)
    x = np.zeros(int(40 * FS))
    notes = [196, 220, 247, 262, 294, 330, 349, 392]
    t = 0.3
    while t < 38.5:
        bpm = 80 + 25 * math.sin(2 * math.pi * t / 9.0)
        tone(x, t + float(rng.normal(0, 0.045)), float(rng.choice(notes)), float(rng.uniform(0.15, 0.4)))
        if rng.random() < 0.5:
            tone(x, t + float(rng.normal(0, 0.045)), float(rng.choice(notes)) / 2, 0.2)
        t += 60 / bpm * float(rng.choice([1, 1, 1, 0.5, 2]))
    r = Run(finish(x), source)
    assert max(p["conf"] for p in r.pubs) < 0.4


# -- corrections, humans, no audio, segments, errors -------------------------------------------------------------------
def feed(clk: C.Clock, x: np.ndarray, t_start: float, n_start: int = 0, block: int = 256,
         flags_first: int = 0) -> None:
    nt = t_start
    for i, n0 in enumerate(range(0, len(x), block)):
        blk = x[n0:n0 + block]
        t0 = t_start + n0 / FS
        clk.process("room-mic", n_start + n0, t0, blk.reshape(-1, 1), FS, flags_first if i == 0 else 0)
        clk.wait_beat()
        while nt <= t0 + len(blk) / FS:
            clk.tick(nt)
            nt += 0.01


def loop_clock(source: str = "aubio", secs: float = 14.0) -> tuple[C.Clock, list[dict], list[float]]:
    pubs: list[dict] = []
    clk = C.Clock(lambda m: pubs.append(m["data"]), activation=source, estimator="legacy")
    beats = grid(120.0, 0.3, secs - 0.2)
    feed(clk, finish(kick_loop(beats, secs)), T0, flags_first=AUDIO_SEGMENT)
    assert pubs[-1]["state"] == "locked"
    return clk, pubs, [T0 + t for t in beats]


def test_bar_vote_applies_at_a_bar_line_and_raises_bar_conf():
    clk, pubs, _ = loop_clock()
    now = pubs[-1]["t_ms"] / 1000.0
    one = round(clk.beat_at(now)) + 2                 # "beat one+2 is a one" (as the slow lane would say)
    assert pubs[-1]["bar_conf"] < 0.3                 # no downbeat vote yet: bar confidence stays low
    threading.Thread(target=clk.apply_integer_correction, args=(one, 4, 1.0)).start()   # any thread
    beats = grid(120.0, 14.3, 20.0)
    feed(clk, finish(kick_loop([t - 14.0 for t in beats], 6.0)), T0 + 14.0, n_start=14 * FS)
    d = clk.snapshot()
    assert (d["bar_anchor_beat"] - one) % 4 == 0 and d["beats_per_bar"] == 4
    assert d["bar_conf"] >= 0.6
    assert clk.stats()["bar_votes"] == 1


def test_octave_vote_halves_at_a_beat_and_keeps_the_count():
    clk, pubs, _ = loop_clock()
    clk.apply_integer_correction(None, None, 0.5)
    feed(clk, finish(kick_loop([t - 14.0 for t in grid(120.0, 14.3, 24.0)], 10.0)), T0 + 14.0, n_start=14 * FS)
    d = clk.snapshot()
    assert d["bpm"] == pytest.approx(60.0, abs=0.3)
    assert clk.stats()["octave_votes"] == 1
    # every kick still lands on a whole or half beat of the new, slower grid, and the count never went back
    for t in grid(120.0, 20.3, 23.5):
        b = C.beat_at(d, T0 + t)
        assert abs(2 * b - round(2 * b)) < 0.03
    assert C.beat_at(d, T0 + 23.0) > C.beat_at(pubs[-1], T0 + 14.0)


def test_human_one_half_double():
    clk, pubs, beats = loop_clock()
    t_one = beats[-3]
    clk.one(t_one)
    clk.tick(pubs[-1]["t_ms"] / 1000.0 + 0.1)
    assert clk.snapshot()["bar_anchor_beat"] == round(clk.beat_at(t_one))
    assert clk.snapshot()["bar_conf"] >= 0.8
    clk.double()                                      # 240 bpm is out of range: refused, nothing moves
    feed(clk, finish(kick_loop([t - 14.0 for t in grid(120.0, 14.3, 16.0)], 2.0)), T0 + 14.0, n_start=14 * FS)
    assert clk.snapshot()["bpm"] == pytest.approx(120.0, abs=0.3)
    clk.half()
    feed(clk, finish(kick_loop([t - 16.0 for t in grid(120.0, 16.3, 20.0)], 4.0)), T0 + 16.0, n_start=16 * FS)
    assert clk.snapshot()["bpm"] == pytest.approx(60.0, abs=0.3)
    clk.double()
    feed(clk, finish(kick_loop([t - 20.0 for t in grid(120.0, 20.3, 24.0)], 4.0)), T0 + 20.0, n_start=20 * FS)
    assert clk.snapshot()["bpm"] == pytest.approx(120.0, abs=0.3)
    st = clk.stats()
    assert st["human_octave"] == 3 and st["octave_changes"] == 2 and st["octave_refused"] == 1


def test_tap_tempo_without_audio_is_free():
    pubs: list[dict] = []
    clk = C.Clock(lambda m: pubs.append(m["data"]), activation="aubio", estimator="legacy")
    for i in range(6):
        clk.tap(100.0 + 0.6 * i + (0.01 if i % 2 else -0.01))
    clk.tick(103.1)
    d = clk.snapshot()
    assert d["bpm"] == pytest.approx(100.0, rel=0.03)
    assert d["state"] == "free"
    b = C.beat_at(d, 103.0)                           # the last tap lands on a beat
    assert abs(b - round(b)) < 0.05


def test_no_audio_goes_free_and_coasts_then_resumes():
    clk, pubs, beats = loop_clock()
    before = dict(pubs[-1])
    last = before["t_ms"] / 1000.0
    for i in range(1, 400):                           # 4 s of ticks, no audio
        clk.tick(last + 0.01 * i)
    d = clk.snapshot()
    assert d["state"] == "free" and d["bpm"] == pytest.approx(120.0, abs=0.3)
    assert d["conf"] < before["conf"] and d["gate"] != "beat"
    b = C.beat_at(d, beats[0] + 0.5 * 34)             # still on the grid it learned
    assert abs(b - round(b)) < 0.03
    # audio comes back as a new segment, on the same grid: locked again, same count
    t_back = T0 + 18.0
    feed(clk, finish(kick_loop([t - 18.0 for t in grid(120.0, 18.3, 24.0)], 6.0)), t_back, n_start=0,
         flags_first=AUDIO_SEGMENT)
    d2 = clk.snapshot()
    assert d2["state"] == "locked"
    b2 = C.beat_at(d2, T0 + 23.3)
    assert abs(b2 - round(b2)) < 0.03
    assert round(b2) == round(C.beat_at(before, T0 + 23.3))


def test_gaps_and_segments_dont_break_the_lane():
    clk, pubs, _ = loop_clock()
    x = finish(kick_loop([t - 14.0 for t in grid(120.0, 14.3, 20.0)], 6.0))
    n_start = 14 * FS
    nt = T0 + 14.0
    for i, n0 in enumerate(range(0, len(x), 256)):
        blk = x[n0:n0 + 256].reshape(-1, 1)
        flags = 0
        if 300 <= i < 305:
            blk = np.zeros_like(blk)
            flags = AUDIO_GAP
        elif i == 305:
            flags = AUDIO_RESUME
        t0 = T0 + 14.0 + n0 / FS
        clk.process("room-mic", n_start + n0, t0, blk, FS, flags)
        clk.wait_beat()
        while nt <= t0 + 256 / FS:
            clk.tick(nt)
            nt += 0.01
    d = clk.snapshot()
    assert d["state"] == "locked" and clk.stats()["errors"] == 0
    # a stream at 44.1 kHz (a source switch) keeps the clock: the beat count goes on
    import soxr
    x44 = soxr.resample(finish(kick_loop(grid(120.0, 0.3, 5.8), 6.0)), FS, 44100).astype(np.float32)
    for i, n0 in enumerate(range(0, len(x44), 256)):
        t0 = T0 + 20.0 + n0 / 44100
        clk.process("pc22-loop", n0, t0, x44[n0:n0 + 256].reshape(-1, 1), 44100, AUDIO_SEGMENT if i == 0 else 0)
        clk.wait_beat()
        clk.tick(t0)
    d2 = clk.snapshot()
    assert d2["state"] == "locked" and d2["epoch"] == d["epoch"]
    b = C.beat_at(d2, T0 + 20.0 + 5.3)
    assert abs(b - round(b)) < 0.04


class Boom:
    name = "boom"
    lag_s = 0.0
    floor = 0.0

    def process_hop(self, hop):
        raise RuntimeError("activation fell over")

    def reset(self):
        pass


def test_errors_never_reach_show_rx():
    clk = C.Clock(None, activation=Boom())
    x = finish(np.zeros(FS))
    feed(clk, x, T0, flags_first=AUDIO_SEGMENT)       # must not raise
    assert clk.stats()["errors"] > 0
    clk.process("room-mic", 0, T0, np.zeros((0, 1), np.float32), FS, 0)
    clk.tick(T0 + 2.0)


def test_observe_hit_refines_phase_and_learns_the_lag():
    pubs: list[dict] = []
    clk = C.Clock(lambda m: pubs.append(m["data"]), activation="aubio", estimator="legacy")
    beats = grid(120.0, 0.3, 15.8)
    x = finish(kick_loop(beats, 16.0))
    true_lag_err = 0.008                              # pretend the activation sits 8 ms late on this timbre
    clk.act.lag_s -= true_lag_err
    nt = T0
    hit_i = 0
    for n0 in range(0, len(x), 256):
        t0 = T0 + n0 / FS
        while hit_i < len(beats) and T0 + beats[hit_i] <= t0:   # dsp's sub/bass hits, on time
            clk.observe_hit(T0 + beats[hit_i], "sub", 1.0)
            hit_i += 1
        clk.process("room-mic", n0, t0, x[n0:n0 + 256].reshape(-1, 1), FS, AUDIO_SEGMENT if n0 == 0 else 0)
        clk.wait_beat()
        while nt <= t0 + 256 / FS:
            clk.tick(nt)
            nt += 0.01
    st = clk.stats()
    assert st.get("hit_refined", 0) > 10
    assert st["lag_adj_ms"] == pytest.approx(true_lag_err * 1000.0, abs=3.0)


# -- song changes and the octave from the kicks (review, 16 Oct) -------------------------------------------------------
def drums_part(x: np.ndarray, t0: float, t1: float, bpm: float, *, hats: str = "off") -> list[float]:
    """Kick on every beat, snare on 2 and 4, hats on the off-beat 8ths ("off") or on every 8th ("all"). Returns the
    kick times."""
    T, k, kicks = 60.0 / bpm, 0, []
    while t0 + k * T < t1:
        t = t0 + k * T
        drum(x, t, 0.7, "kick")
        kicks.append(t)
        if k % 2:
            drum(x, t, 0.4, "snare")
        if hats == "all":
            drum(x, t, 0.2, "hat")
        drum(x, t + T / 2, 0.2 if hats == "all" else 0.15, "hat")
        k += 1
    return kicks


def chords_part(x: np.ndarray, t0: float, t1: float, bpm: float) -> None:
    """Soft piano-like chords on every beat, accented on 1, no drums."""
    T, k = 60.0 / bpm, 0
    roots = [220.0, 174.6, 196.0, 261.6]
    while t0 + k * T < t1:
        f = roots[(k // 4) % 4]
        for r in (1.0, 1.26, 1.5):
            tone(x, t0 + k * T, f * r, 0.25 if k % 4 == 0 else 0.15, 0.9)
        k += 1


def relock_s(r: Run, t_change: float, bpm: float) -> float | None:
    """Seconds after t_change from which every published body is locked on bpm (+-2 %) with a gate."""
    ok = [abs(p["bpm"] / bpm - 1.0) < 0.02 and p["state"] == "locked" and p["gate"] in ("dmx", "beat")
          for p in r.pubs]
    for i, p in enumerate(r.pubs):
        t = p["t_ms"] / 1000.0 - T0
        if t >= t_change and all(ok[i:]):
            return t - t_change
    return None


@pytest.mark.skipif(not HAVE_MADMOM, reason="madmom not installed")
@pytest.mark.parametrize("new", ["chords92", "drums96"])
def test_a_new_song_near_3_4_of_the_old_tempo_relocks(new):
    """124 bpm drums, 8 s of silence, then 92 bpm soft chords or 96 bpm drums: the old grid keeps catching hits by
    coincidence (3:4 / 4:3), so only the clearer-tempo trigger can start the cold search. Re-lock within ~10 s."""
    x = np.zeros(int(68 * FS))
    drums_part(x, 0.5, 40.0, 124.0)
    if new == "chords92":
        chords_part(x, 48.0, 68.0, 92.0)
    else:
        drums_part(x, 48.0, 68.0, 96.0)
    r = Run(finish(x), "madmom")
    bpm = 92.0 if new == "chords92" else 96.0
    t = relock_s(r, 48.0, bpm)
    st = r.clk.stats()
    assert t is not None and t <= 10.5, (t, st)
    assert st.get("cold_alt", 0) >= 1 and st["seeds"] == 2 and st["errors"] == 0
    assert r.pubs[-1]["conf"] >= C.BEAT_ON


def hit_run(x: np.ndarray, kicks: list[float], source: str = "madmom") -> Run:
    """A Run whose clock also gets dsp-like low hits (a sub hit 12 ms after each kick, a bass hit at 3 ms), each
    delivered before the block that holds it, as __main__ does (Features first, then the clock)."""
    r = Run.__new__(Run)
    r.pubs, r.msgs = [], []

    def pub(m):
        r.msgs.append(m)
        r.pubs.append(m["data"])

    r.clk = C.Clock(pub, activation=source, estimator="legacy")
    hits = sorted([(T0 + k + 0.003, "bass") for k in kicks] + [(T0 + k + 0.012, "sub") for k in kicks])
    nt, hi = T0, 0
    for n0 in range(0, len(x), 256):
        t0 = T0 + n0 / FS
        t1 = t0 + 256 / FS
        while hi < len(hits) and hits[hi][0] < t1:
            r.clk.observe_hit(hits[hi][0], hits[hi][1], 0.8)
            hi += 1
        r.clk.process("room-mic", n0, t0, x[n0:n0 + 256].reshape(-1, 1), FS, AUDIO_SEGMENT if n0 == 0 else 0)
        r.clk.wait_beat()
        while nt <= t1:
            r.clk.tick(nt)
            nt += 0.01
    r.ts = np.array([p["t_ms"] for p in r.pubs]) / 1000.0
    return r


@pytest.mark.skipif(not HAVE_MADMOM, reason="madmom not installed")
def test_96_bpm_with_8th_note_hats_takes_the_lower_octave():
    """Kick every beat, snare on 2 and 4, hats on every 8th: the activation pulses at 192. With no kick evidence the
    seed cap (180) takes the half; with dsp's kicks, the same."""
    x = np.zeros(int(24 * FS))
    kicks = drums_part(x, 0.3, 24.0, 96.0, hats="all")
    x = finish(x)
    for r in (Run(x, "madmom"), hit_run(x, kicks)):
        t = relock_s(r, 0.0, 96.0)
        assert t is not None and t <= 10.0, (t, r.clk.stats())
        assert r.clk.stats().get("octave_changes", 0) == 0
        on = [e for tb, e, p in r.err_ms([T0 + k for k in kicks]) if tb - T0 > 12.0]
        assert on and max(abs(e) for e in on) < 15.0              # the beats are the kicks, not the off-beats


@pytest.mark.skipif(not HAVE_MADMOM, reason="madmom not installed")
def test_the_kicks_vote_the_octave_on_fast_grids():
    """88 bpm with 8th hats locks at 176 (under the seed cap); dsp's kicks on alternate beats of it take the half,
    on the kicks. A real 174 bpm (a kick on every beat) keeps its tempo with the same rule."""
    x = np.zeros(int(24 * FS))
    kicks = drums_part(x, 0.3, 24.0, 88.0, hats="all")
    x = finish(x)
    assert abs(Run(x, "madmom").pubs[-1]["bpm"] - 176.0) < 2.0    # no kick evidence: the doubled grid stays
    r = hit_run(x, kicks)
    t = relock_s(r, 0.0, 88.0)
    assert t is not None and t <= 10.0, (t, r.clk.stats())
    on = [e for tb, e, p in r.err_ms([T0 + k for k in kicks]) if tb - T0 > 12.0]
    assert on and max(abs(e) for e in on) < 15.0
    y = np.zeros(int(20 * FS))
    kicks = drums_part(y, 0.3, 20.0, 174.0)
    r = hit_run(finish(y), kicks)
    assert relock_s(r, 0.0, 174.0) is not None and r.clk.stats().get("octave_changes", 0) == 0


def test_a_quiet_room_drops_the_gate_and_skips_the_activation():
    from show_service import clock as clock_mod
    c = clock_mod.Clock(None, activation="aubio", estimator="legacy") if hasattr(clock_mod, "AubioActivation") \
        else None
    if c is None:
        pytest.skip("no activation")
    calls = []
    real = c.act.process_hop
    c.act.process_hop = lambda hop: calls.append(1) or real(hop)
    c._gate, c._state = "beat", "locked"
    c.set_level(0.5, 0.0)
    assert not c._quiet
    c.set_level(0.01, 1.0)
    c.set_level(0.01, 3.0)
    assert not c._quiet                                  # under QUIET_AFTER_S
    c.set_level(0.01, 4.1)
    assert c._quiet and c._gate == "none" and c._state == "holding"
    c._lane_fs = clock_mod.FS
    c._hop(np.zeros(clock_mod.HOP, np.float32))
    assert calls == []                                   # madmom (here aubio) isn't run while quiet
    c.set_level(0.03, 5.0)
    assert c._quiet                                      # hysteresis: not back until QUIET_GATE_ON
    c.set_level(0.06, 5.5)
    assert not c._quiet and c.stats()["counts"]["quiet_ends"] == 1 if "counts" in c.stats() else not c._quiet
    c.set_level(None, 6.0)
    assert not c._quiet
