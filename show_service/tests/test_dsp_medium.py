"""dsp's medium lane on synthetic signals (README "Stage 4" S4.6; SHOW_ENGINE §4).

Does-it-work level: music.rhythm's shape and 10 Hz cadence at 48 and 44.1 kHz; a held C major chord's chroma peaks
at C, E and G and harmony finds C major from it; drums are percussive and pads are not; a click track has a pulse
and noise has none; with a fake locked clock, swung hats read swing ~2 and straight ones ~1, off-beat 16ths are
syncopated and on-beat hits are not; chord changes raise harm_change (time-based and beat-synchronous); silence
reads zero and chroma12() goes None (and stale); low_db; a reset; no buffer allocations per block; a bad clock
never raises; and the cost, before nothing else (printed; run with -s to see it).
"""

from __future__ import annotations

import json
import math
import time
import tracemalloc

import numpy as np
import pytest

from show_service import contract as c
from show_service import dsp
from show_service.harmony import Harmony

FS = 48000
T0 = 1000.0
BPM = 120.0
SPB = 60.0 / BPM
FIRST_BEAT_S = 0.5                         # beat 0 sounds at T0 + 0.5 s


# -- signals ----------------------------------------------------------------------------------------------------------
def noise(n: int, rms: float, seed: int = 1, fs: int = FS, slope: float = 1.0) -> np.ndarray:
    """Noise with power ~ 1/f**slope from 20 Hz, scaled to ``rms``."""
    rng = np.random.default_rng(seed)
    spec = np.fft.rfft(rng.standard_normal(n))
    spec /= np.maximum(np.fft.rfftfreq(n, 1.0 / fs), 20.0) ** (slope / 2.0)
    x = np.fft.irfft(spec, n)
    return x * (rms / np.std(x))


def tone(n: int, midi: float, amp: float, fs: int = FS, partials: int = 4, attack_s: float = 0.05) -> np.ndarray:
    """A sustained note with 1/k partials and a short attack."""
    t = np.arange(n) / fs
    hz = 440.0 * 2.0 ** ((midi - 69) / 12.0)
    x = sum(amp / k * np.sin(2.0 * np.pi * hz * k * t) for k in range(1, partials + 1))
    return x * np.minimum(1.0, t / attack_s)


def chord(n: int, midis, amp: float = 0.1, fs: int = FS) -> np.ndarray:
    return sum(tone(n, m, amp, fs) for m in midis)


def burst(n: int, tau_s: float, amp: float, seed: int, fs: int = FS) -> np.ndarray:
    """A high-passed noise burst decaying with tau: a hat (short tau) or a snare (longer)."""
    z = np.diff(np.r_[0.0, np.random.default_rng(seed).standard_normal(n)])
    return amp * z * np.exp(-np.arange(n) / fs / tau_s)


def kick(fs: int = FS) -> np.ndarray:
    """A sine sweeping 110 -> 50 Hz (tau 10 ms), decaying tau 120 ms, faded out over its last 20 ms (a cut-off
    tail is a click, and an onset)."""
    t = np.arange(int(0.4 * fs)) / fs
    hz = 50.0 + 60.0 * np.exp(-t / 0.010)
    fade = np.minimum(1.0, (t[-1] - t) / 0.02)
    return 0.5 * np.sin(2.0 * np.pi * np.cumsum(hz) / fs) * np.exp(-t / 0.12) * fade


def place(x: np.ndarray, sound: np.ndarray, t_s: float, fs: int = FS) -> None:
    s = int(round(t_s * fs))
    if 0 <= s < x.size:
        e = min(x.size, s + sound.size)
        x[s:e] += sound[:e - s]


def beat_times(n: int, fs: int = FS):
    k = 0
    while FIRST_BEAT_S + (k + 1) * SPB < n / fs - 0.3:
        yield k, FIRST_BEAT_S + k * SPB
        k += 1


def drum_loop(n: int, *, offbeat: float = 0.5, hats: bool = True, fs: int = FS) -> np.ndarray:
    """Kick on 1 and 3, snare on 2 and 4, hats on every beat and at ``offbeat`` into it (0.5 straight, 2/3 a 2:1
    swing), at 120 bpm from FIRST_BEAT_S."""
    x = np.zeros(n)
    k_s, sn, hat = kick(fs), burst(int(0.2 * fs), 0.05, 0.3, 3, fs), burst(int(0.06 * fs), 0.012, 0.15, 4, fs)
    for k, tb in beat_times(n, fs):
        place(x, k_s if k % 2 == 0 else sn, tb, fs)
        if hats:
            place(x, hat, tb, fs)
            place(x, hat, tb + offbeat * SPB, fs)
    return x


def hats_at(n: int, phases, fs: int = FS) -> np.ndarray:
    """Hats only, at these phases of every beat."""
    x = np.zeros(n)
    hat = burst(int(0.06 * fs), 0.012, 0.2, 4, fs)
    for _, tb in beat_times(n, fs):
        for ph in phases:
            place(x, hat, tb + ph * SPB, fs)
    return x


def clicks(n: int, period_s: float = 0.5, fs: int = FS) -> np.ndarray:
    x = np.zeros(n)
    for s in range(fs // 2, n - 100, int(period_s * fs)):
        x[s:s + 48] = 0.8 * np.hanning(48)
    return x


def progression(n: int, every_s: float = 2.0, fs: int = FS) -> np.ndarray:
    """C, Am, F, G: a new chord every ``every_s``."""
    chords = ([60, 64, 67], [57, 60, 64], [53, 57, 60], [55, 59, 62])
    x = np.zeros(n)
    step = int(every_s * fs)
    for i, s in enumerate(range(0, n, step)):
        seg = chord(min(step, n - s), chords[i % 4], fs=fs)
        x[s:s + seg.size] = seg
    return x


def clock(gate: str = "beat", epoch: int = 1, bpb: int = 4) -> c.ClockData:
    """A locked 120 bpm grid with beat 0 at T0 + FIRST_BEAT_S (and the bar there)."""
    return {"bpm": BPM, "anchor_ms": (T0 + FIRST_BEAT_S) * 1000.0, "beat_at_anchor": 0, "beats_per_bar": bpb,
            "bar_anchor_beat": 0, "phrase_bars": 4, "conf": 0.9, "bar_conf": 0.9, "phrase_conf": 0.8,
            "state": "locked", "gate": gate, "epoch": epoch, "stability": 0.9, "source": "madmom", "t_ms": 0.0}


# -- the harness ------------------------------------------------------------------------------------------------------
def feed(f: dsp.Features, x: np.ndarray, *, fs: int = FS, block: int = 128, t0: float = T0, stream: str = "room-mic",
         flags_for=None, on_tick=None) -> None:
    """Deliver ``x`` in blocks, ticking every TICK_S of audio time like the receiver."""
    x32 = np.asarray(x, dtype=np.float32)
    next_tick = t0
    for i, s in enumerate(range(0, x32.size, block)):
        blk = x32[s:s + block]
        f.process(stream, s, t0 + s / fs, blk.reshape(-1, 1), fs, flags_for(i) if flags_for else 0)
        now = t0 + (s + blk.size) / fs
        while next_tick <= now:
            f.tick(next_tick)
            if on_tick is not None:
                on_tick(next_tick)
            next_tick += c.TICK_S


def run(x: np.ndarray, *, fs: int = FS, ck=None, **kw):
    msgs: list[dict] = []
    f = dsp.Features(publish=msgs.append)
    if ck is not None:
        f.clock_fn = lambda: ck
    feed(f, x, fs=fs, **kw)
    return f, [m["data"] for m in msgs if m["type"] == c.MUSIC_RHYTHM], msgs


def tail(frames, key, s: float = 3.0):
    """``key`` over the last ``s`` seconds of frames."""
    return [d[key] for d in frames[-int(s * c.RHYTHM_HZ):]]


# -- shape and cadence ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("fs", [48000, 44100])
def test_rhythm_shape_and_10_hz(fs):
    n = 6 * fs
    f, frames, msgs = run(drum_loop(n, fs=fs) + chord(n, [60, 64, 67], fs=fs), fs=fs)
    assert 58 <= len(frames) <= 61                                  # one per 100 ms of stream time
    ts = [d["t_ms"] for d in frames]
    assert all(b > a for a, b in zip(ts, ts[1:]))
    assert 99.0 < float(np.mean(np.diff(ts))) < 101.0 and max(np.diff(ts)) < 107.0
    d = f.rhythm()
    assert d is frames[-1]
    assert set(d) == set(c.RhythmData.__annotations__)
    assert d["stream"] == "room-mic"
    assert len(d["chroma12"]) == len(d["chroma12_avg"]) == 12 and len(d["mfcc13"]) == 13
    assert max(d["chroma12"]) == 1.0 and all(0.0 <= v <= 1.0 for v in d["chroma12"] + d["chroma12_avg"])
    for k in ("percussive_ratio", "harm_change", "density", "pulse_clarity", "gate"):
        assert 0.0 <= d[k] <= 1.0, k
    assert d["swing"] is None and d["syncopation"] is None              # no clock
    assert -120.0 <= d["harmonic_db"] <= 0.0 and -120.0 <= d["low_db"] <= 6.0 and d["onset_rate"] >= 0.0
    json.dumps(msgs, allow_nan=False)
    assert all(type(v) is float for k, v in d.items() if k not in ("stream", "chroma12", "chroma12_avg", "mfcc13",
                                                                     "swing", "syncopation"))
    assert all(type(v) is float for v in d["chroma12"] + d["mfcc13"])
    st = f.stats()
    assert st["medium_frames"] == len(frames) == st["rhythm_publishes"]
    assert st["medium_ms_p50"] is not None and st["medium_ms_p95"] >= st["medium_ms_p50"]
    assert st["publishes"] == len([m for m in msgs if m["type"] == c.MUSIC_FEATURES])   # features only
    assert st["onsets"] > 0
    ch = f.chroma12()
    assert isinstance(ch, np.ndarray) and ch.shape == (12,) and float(ch.max()) == 1.0


def test_one_publish_per_frame_and_none_without_audio():
    msgs: list[dict] = []
    f = dsp.Features(publish=msgs.append)
    x = chord(2 * FS, [60, 64, 67]).astype(np.float32).reshape(-1, 1)
    for s in range(0, x.shape[0], 128):                     # process only: frames are counted, nothing published
        f.process("room-mic", s, T0 + s / FS, x[s:s + 128], FS, 0)
    assert f.stats()["medium_frames"] == 20 and f.rhythm() is None and f.chroma12() is None
    f.tick(T0 + 2.0)
    f.tick(T0 + 2.001)                                      # nothing new: no second publish
    rh = [m for m in msgs if m["type"] == c.MUSIC_RHYTHM]
    assert len(rh) == 1 and f.rhythm() is rh[0]["data"]     # the latest frame only (at most 10 Hz)
    assert f.chroma12() is not None
    for k in range(60):                                     # no audio: no more frames, and the chroma goes stale
        f.tick(T0 + 2.01 + 0.01 * k)
    assert len([m for m in msgs if m["type"] == c.MUSIC_RHYTHM]) == 1
    assert f.chroma12() is None and f.rhythm() is rh[0]["data"]


# -- chroma and harmony -----------------------------------------------------------------------------------------------
def test_c_major_chord_chroma_and_harmony_finds_the_key():
    n = 12 * FS
    pubs: list[dict] = []
    h = Harmony(pubs.append)
    holder: dict = {}

    def on_tick(now):
        h.tick(now, None, holder["f"].chroma12())

    msgs: list[dict] = []
    f = holder["f"] = dsp.Features(publish=msgs.append)
    # the root doubled in the bass, as music voices it: a bare triad with real overtones (E's 3rd partial is B,
    # G's is D) scores E minor and C major alike in Krumhansl-Kessler, and harmony then names no key
    feed(f, chord(n, [48, 60, 64, 67]), on_tick=on_tick)
    d = f.rhythm()
    top3 = set(int(k) for k in np.argsort(d["chroma12"])[-3:])
    assert top3 == {0, 4, 7}, d["chroma12"]                          # C, E, G
    assert d["chroma12"][0] == 1.0                                   # the doubled root
    assert max(d["chroma12"][k] for k in range(12) if k not in (0, 4, 7)) < 0.6 * min(d["chroma12"][k]
                                                                                    for k in (0, 4, 7))
    assert set(int(k) for k in np.argsort(d["chroma12_avg"])[-3:]) == {0, 4, 7}
    assert d["harmonic_db"] > -30.0 and d["harm_change"] < 0.05
    last = h.latest
    assert last is not None and last["source"] == "audio"
    assert (last["key"], last["mode"]) == ("C", "major"), last
    assert np.allclose(last["chroma12"], np.round(f.chroma12(), 4), atol=1e-4)


def test_chroma_is_tuned_to_a440_at_44k1():
    n = 4 * 44100
    for midi in (57, 62, 69, 76):                                     # A3, D4, A4, E5: their pitch class wins
        f, frames, _ = run(tone(n, midi, 0.2, fs=44100, partials=1), fs=44100)
        assert int(np.argmax(frames[-1]["chroma12"])) == midi % 12


# -- percussive vs sustained, pulse ----------------------------------------------------------------------------------
def test_drums_are_percussive_pads_are_not():
    n = 10 * FS
    _, drums, _ = run(drum_loop(n))
    _, pads, _ = run(chord(n, [48, 55, 60, 64, 67], amp=0.06))
    assert min(tail(drums, "percussive_ratio")) > 0.7
    assert max(tail(pads, "percussive_ratio")) < 0.3
    assert drums[-1]["onset_rate"] > 2.0 and pads[-1]["onset_rate"] == 0.0
    assert pads[-1]["pulse_clarity"] == 0.0                         # a steady sound's flux is no pulse


def test_click_track_has_a_pulse_noise_has_none():
    n = 12 * FS
    _, cl, _ = run(clicks(n))
    _, wn, _ = run(noise(n, 0.1, slope=1.0))
    assert min(tail(cl, "pulse_clarity")) > 0.8
    assert max(tail(wn, "pulse_clarity")) < 0.3
    assert 1.8 <= cl[-1]["onset_rate"] <= 2.2                       # 2 clicks a second
    assert wn[-1]["onset_rate"] < 1.0                               # noise: (almost) no onsets


def test_density_follows_the_onset_rate_and_the_gate():
    """Sparse (2 a second), dense (8), sparse again: once the percentiles have seen both, dense reads high and the
    second sparse stretch low (alone, a steady rate is stretched toward 1: that is the normalisation)."""
    n = 8 * FS
    x = np.r_[hats_at(n, (0.0,)), hats_at(n, (0.0, 0.25, 0.5, 0.75)), hats_at(n, (0.0,))]
    _, frames, _ = run(x)
    dense = frames[150:160]                                         # 15-16 s
    sparse = frames[-10:]                                           # 23-24 s
    assert dense[-1]["onset_rate"] > 2.5 * sparse[-1]["onset_rate"]
    assert np.mean([d["density"] for d in dense]) > np.mean([d["density"] for d in sparse]) + 0.3
    _, quiet, _ = run(x * 1e-3)                                     # 60 dB down: the loudness gate holds it at 0
    assert max(d["density"] for d in quiet) == 0.0 and all(d["gate"] == 0.0 for d in quiet[-20:])


# -- with a clock: swing, syncopation ---------------------------------------------------------------------------------
def test_swing_needs_a_lock_and_reads_the_offbeat():
    n = 12 * FS
    _, swung, _ = run(drum_loop(n, offbeat=2.0 / 3.0), ck=clock())
    _, straight, _ = run(drum_loop(n, offbeat=0.5), ck=clock())
    _, dmx, _ = run(drum_loop(n, offbeat=2.0 / 3.0), ck=clock(gate="dmx"))
    _, unlocked, _ = run(drum_loop(n, offbeat=2.0 / 3.0), ck=clock(gate="none"))
    _, none, _ = run(drum_loop(n, offbeat=2.0 / 3.0))
    sw = tail(swung, "swing")
    st = tail(straight, "swing")
    assert all(v is not None and 1.8 <= v <= 2.2 for v in sw), sw
    assert all(v is not None and 0.95 <= v <= 1.15 for v in st), st
    assert all(v is not None and 1.8 <= v <= 2.2 for v in tail(dmx, "swing"))
    assert all(d["swing"] is None and d["syncopation"] is None for d in unlocked + none)


def test_syncopation_offbeat_16ths_vs_on_the_beat():
    n = 12 * FS
    _, off, _ = run(hats_at(n, (0.25, 0.75)), ck=clock())          # only the "e" and "a": all syncopated
    _, on, _ = run(hats_at(n, (0.0,)), ck=clock())                 # only the beats: none
    _, eighths, _ = run(hats_at(n, (0.0, 0.5)), ck=clock())        # 8ths under equal beats: not syncopated
    _, push, _ = run(hats_at(n, (0.5,)), ck=clock())               # 8ths with nothing on the beats: syncopated
    assert min(tail(off, "syncopation")) > 0.9
    assert max(tail(on, "syncopation")) < 0.1
    assert max(tail(eighths, "syncopation")) < 0.1
    assert min(tail(push, "syncopation")) > 0.9
    _, three, _ = run(hats_at(n, (0.25, 0.75)), ck=clock(bpb=3))   # 3/4: still a 16th grid
    assert min(tail(three, "syncopation")) > 0.9


def test_sync_tables():
    weak, nxt = dsp._sync_tables(16)
    assert list(weak) == [1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 14, 15]
    assert list(nxt) == [2, 4, 4, 6, 8, 8, 10, 12, 12, 14, 0, 0]


# -- the chord change -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("locked", [False, True])
def test_chord_changes_raise_harm_change(locked):
    n = 14 * FS
    ck = clock() if locked else None
    f, moving, _ = run(progression(n), ck=ck)
    _, steady, _ = run(chord(n, [60, 64, 67]), ck=ck)
    a, b = np.mean(tail(moving, "harm_change", 6.0)), np.mean(tail(steady, "harm_change", 6.0))
    assert a > 0.05 and b < 0.02 and a > 4 * b, (a, b)
    assert (f._bs_i > 0) == locked                                 # the beat-synchronous path ran only when locked


def test_a_clock_epoch_restarts_the_beat_sync():
    ck = clock()
    msgs: list[dict] = []
    f = dsp.Features(publish=msgs.append)
    f.clock_fn = lambda: ck
    feed(f, progression(8 * FS))
    assert f._bs_i > 0
    ck = clock(epoch=2)
    feed(f, progression(FS // 5), t0=T0 + 8.0)
    assert f._bs_epoch == 2 and f._bs_i <= 1


# -- silence, levels, reset -------------------------------------------------------------------------------------------
def test_silence_reads_zero_and_has_no_chroma():
    f, frames, _ = run(np.zeros(5 * FS), ck=clock())
    d = frames[-1]
    assert d["chroma12"] == [0.0] * 12 and d["chroma12_avg"] == [0.0] * 12
    assert d["harmonic_db"] == -120.0 and d["low_db"] == -120.0
    assert d["percussive_ratio"] == d["harm_change"] == d["pulse_clarity"] == d["onset_rate"] == d["density"] == 0.0
    assert d["syncopation"] == 0.0 and d["swing"] is None             # locked, but nothing to hear
    assert f.chroma12() is None
    # loud, then silence: the chroma goes away within the 0.9 s median and the frames keep coming
    f, frames, _ = run(np.r_[chord(3 * FS, [60, 64, 67]), np.zeros(3 * FS)])
    assert frames[25]["chroma12"] != [0.0] * 12 and frames[-1]["chroma12"] == [0.0] * 12 and f.chroma12() is None


def test_low_db_reads_the_sub_and_bass_level():
    f, frames, _ = run(tone(4 * FS, 43, 0.5, partials=1))          # G2, 98 Hz, half scale: -6 dB re a full sine
    assert -7.5 <= frames[-1]["low_db"] <= -4.5, frames[-1]["low_db"]
    f, frames, _ = run(tone(4 * FS, 93, 0.5, partials=1))          # A6, 1760 Hz: nothing down there
    assert frames[-1]["low_db"] < -40.0
    mf = frames[-1]["mfcc13"]
    assert all(math.isfinite(v) for v in mf) and any(abs(v) > 1.0 for v in mf)


def test_segment_resets_the_medium_lane():
    msgs: list[dict] = []
    f = dsp.Features(publish=msgs.append)
    feed(f, drum_loop(6 * FS))
    assert f._on_i > 0 and f._med_n > 0
    feed(f, np.zeros(FS // 5), t0=T0 + 6.0, flags_for=lambda i: c.AUDIO_SEGMENT if i == 0 else 0)   # 1 frame
    assert f._on_i == 0 and f._med_n == 1 and float(np.abs(f._m_hist).max()) < 1e-20   # one silent frame
    feed(f, drum_loop(6 * FS), t0=T0 + 6.2)
    d = f.rhythm()
    assert d["onset_rate"] > 2.0 and d["percussive_ratio"] > 0.7


# -- robustness, allocation, cost -------------------------------------------------------------------------------------
def test_a_bad_clock_never_raises():
    def boom():
        raise RuntimeError("clock down")

    bodies = [boom, lambda: None, lambda: {"gate": "beat"}, lambda: {**clock(), "bpm": 0.0},
              lambda: {**clock(), "bpm": float("nan")}, lambda: {**clock(), "beats_per_bar": None},
              lambda: {**clock(), "anchor_ms": float("inf")}, lambda: {**clock(), "epoch": "x"}]
    for fn in bodies:
        msgs: list[dict] = []
        f = dsp.Features(publish=msgs.append)
        f.clock_fn = fn
        feed(f, drum_loop(3 * FS))
        frames = [m["data"] for m in msgs if m["type"] == c.MUSIC_RHYTHM]
        assert len(frames) == 29                       # hop 562 ends at 143872: frames at 4800 .. 139200
        json.dumps(frames, allow_nan=False)


def test_no_buffer_allocations_per_block_with_a_locked_clock():
    f = dsp.Features()
    ck = clock()
    f.clock_fn = lambda: ck
    x = (drum_loop(6 * FS) + chord(6 * FS, [60, 64, 67])).astype(np.float32).reshape(-1, 1)
    for s in range(0, 4 * FS, 128):
        f.process("room-mic", s, T0 + s / FS, x[s:s + 128], FS, 0)
    blocks = [x[s:s + 128] for s in range(4 * FS, 6 * FS, 128)]
    frames0 = f.stats()["medium_frames"]
    tracemalloc.start()
    try:
        base, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        for j, blk in enumerate(blocks):
            s = 4 * FS + 128 * j
            f.process("room-mic", s, T0 + s / FS, blk, FS, 0)
        cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert f.stats()["medium_frames"] - frames0 == 20
    assert peak - base < 8192, (base, cur, peak)                     # small arrays and scalars, no sample buffers
    assert cur - base < 4096


def test_cpu_of_the_medium_lane(capsys):
    """Drums and a chord, a locked clock, 128-frame blocks, ticks every 10 ms. Prints the medium frame's cost and
    the hop's (which leaves the medium frame out); asserts only against pathologies (a shared, loaded host)."""
    n = 20 * FS
    x = drum_loop(n) + chord(n, [60, 64, 67])
    msgs: list[dict] = []
    f = dsp.Features(publish=msgs.append)
    ck = clock()
    f.clock_fn = lambda: ck
    t = time.thread_time()
    feed(f, x)
    cpu = time.thread_time() - t
    st = f.stats()
    with capsys.disabled():
        print(f"\n[dsp medium] whole path {cpu / (n / FS) * 100:.2f} % of a core; medium frame p50 "
              f"{st['medium_ms_p50']} ms p95 {st['medium_ms_p95']} ms ({st['medium_frames']} frames, "
              f"{st['medium_ms_p50'] * 10 / 1000:.2%} of a core at 10 Hz); hop p50 {st['hop_ms_p50']} ms "
              f"p95 {st['hop_ms_p95']} ms; block p95 {st['block_ms_p95']} ms; median kernel {dsp.MEDIAN_KERNEL}")
    assert st["medium_ms_p95"] < 3.0 and st["hop_ms_p95"] < 2.0


def test_fuzz_never_raises_and_stays_finite():
    rng = np.random.default_rng(5)
    msgs: list[dict] = []
    f = dsp.Features(publish=msgs.append)
    ck = clock()
    f.clock_fn = lambda: ck if rng.random() < 0.7 else None
    t = T0
    fs = FS
    for _ in range(600):
        if rng.random() < 0.02:
            fs = int(rng.choice([44100, 48000, 32000, 16000]))
        n = int(rng.choice([1, 7, 96, 128, 128, 500, 1500, 3000]))
        pcm = (rng.standard_normal(n) * rng.choice([0.0, 1e-6, 0.05, 0.9])).astype(np.float32)
        flags = int(rng.choice([0, 0, 0, 0, c.AUDIO_GAP, c.AUDIO_RESUME, c.AUDIO_SEGMENT]))
        f.process("room-mic", 0, t, pcm, fs, flags)
        t += n / fs
        f.tick(t)
    frames = [m["data"] for m in msgs if m["type"] == c.MUSIC_RHYTHM]
    assert frames
    json.dumps(frames, allow_nan=False)
