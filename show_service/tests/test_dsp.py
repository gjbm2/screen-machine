"""dsp.Features on synthetic signals (README "dsp.py" tests; SHOW_ENGINE §4, §6.3, §11.1).

Does-it-work level: shapes, a sine lands in its spectrum32 band at 48 and 44.1 kHz, kicks hit sub/bass within
15 ms, clicks give onsets within a hop, silence reads zero and runs no slower than noise (no denormals), noise is
noisy, the spectrum keeps its shape, any block size, the flags, rate/channel changes, the gate, the cadence, no
per-block buffer allocations, and the CPU cost (printed; run with -s to see it).
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

FS = 48000
T0 = 1000.0


# -- signals ----------------------------------------------------------------------------------------------------------
def coloured(n: int, slope: float, rms: float, seed: int = 1, fs: int = FS) -> np.ndarray:
    """Noise with power ~ 1/f**slope (0 white, 1 pink, 2 red), scaled to ``rms``."""
    rng = np.random.default_rng(seed)
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.maximum(np.fft.rfftfreq(n, 1.0 / fs), 20.0)                # shaped from 20 Hz up
    spec /= f ** (slope / 2.0)
    x = np.fft.irfft(spec, n)
    return x * (rms / np.std(x))


def sine(n: int, hz: float, amp: float, fs: int = FS) -> np.ndarray:
    return amp * np.sin(2.0 * np.pi * hz * np.arange(n) / fs)


def kicks(n: int, *, period_s: float = 0.5, first_s: float = 1.0, amp: float = 0.6, fs: int = FS):
    """A kick drum: a sine sweeping 110 -> 50 Hz (tau 10 ms), decaying tau 120 ms. Returns (x, onset samples)."""
    length = int(0.3 * fs)
    t = np.arange(length) / fs
    hz = 50.0 + 60.0 * np.exp(-t / 0.010)
    k = amp * np.sin(2.0 * np.pi * np.cumsum(hz) / fs) * np.exp(-t / 0.12)
    x = np.zeros(n)
    onsets = list(range(int(first_s * fs), n - length, int(period_s * fs)))
    for s in onsets:
        x[s:s + length] += k
    return x, onsets


def feed(f: dsp.Features, x: np.ndarray, *, fs: int = FS, block: int = 128, t0: float = T0, stream: str = "room-mic",
         flags_for=None, channels: int = 1, tick: bool = True, on_block=None) -> None:
    """Deliver ``x`` in blocks (float32, (frames, channels)), ticking every 10 ms of audio time like the receiver."""
    x32 = np.asarray(x, dtype=np.float32)
    next_tick = t0
    for i, s in enumerate(range(0, x32.size, block)):
        blk = x32[s:s + block]
        pcm = np.repeat(blk[:, None], channels, axis=1) if channels > 1 else blk.reshape(-1, 1)
        flags = flags_for(i) if flags_for else 0
        if on_block:
            on_block(i, t0 + s / fs, blk.size)
        f.process(stream, s, t0 + s / fs, pcm, fs, flags)
        now = t0 + (s + blk.size) / fs
        while tick and next_tick <= now:
            f.tick(next_tick)
            next_tick += c.TICK_S


def collect():
    msgs: list[dict] = []
    return msgs, dsp.Features(publish=msgs.append)


def of_type(msgs, typ):
    return [m["data"] for m in msgs if m["type"] == typ]


# -- shapes -----------------------------------------------------------------------------------------------------------
def test_shapes_are_exactly_the_contract():
    msgs, f = collect()
    x, _ = kicks(4 * FS)
    feed(f, x + coloured(x.size, 1, 0.05))
    feats = of_type(msgs, c.MUSIC_FEATURES)
    hits = of_type(msgs, c.MUSIC_HIT)
    assert feats and hits
    assert all(set(m) == {"type", "data"} for m in msgs)
    d = f.latest()
    assert d is feats[-1]
    assert set(d) == set(c.FeaturesData.__annotations__)
    assert d["stream"] == "room-mic"
    assert list(d["bands"]) == list(c.BAND_NAMES)
    for band in d["bands"].values():
        assert set(band) == set(c.BandData.__annotations__)
        assert isinstance(band["hits"], int) and 0.0 <= band["level"] <= 1.0 and 0.0 <= band["presence"] <= 1.0
    assert len(d["spectrum32"]) == c.SPEC_BANDS and all(0.0 <= v <= 1.0 for v in d["spectrum32"])
    for k in ("onset", "brightness", "noisiness", "gate", "energy", "punch", "drive"):
        assert 0.0 <= d[k] <= 1.0, k
    assert -70.0 <= d["lufs_m"] <= 0.0 and -70.0 <= d["lufs_s"] <= 0.0
    assert -120.0 <= d["rms_dbfs"] <= d["peak_dbfs"] <= 0.0
    for h in hits:
        assert set(h) == set(c.HitData.__annotations__) and h["band"] in c.BAND_NAMES and 0.0 <= h["strength"] <= 1.0
    json.dumps(msgs, allow_nan=False)                 # strict JSON, plain Python types
    assert all(type(v) is float for k, v in d.items() if k not in ("bands", "spectrum32", "stream"))
    assert f.chroma12() is None
    st = f.stats()
    for k in ("blocks", "hops", "hop_ms_p50", "hop_ms_p95", "block_ms_p95", "gaps"):
        assert k in st
    assert st["hops"] == x.size // 256 and st["gaps"] == 0 and st["publishes"] == len(feats)


# -- spectrum32 -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("k", [3, 5, 9, 14, 19, 24, 28, 31])
def test_sine_lights_its_spectrum32_band_at_both_rates(k):
    hz = c.SPEC_CENTRES_HZ[k]
    peaks = {}
    for fs in (48000, 44100):
        msgs, f = collect()
        feed(f, sine(3 * fs, hz, 0.3, fs), fs=fs)
        spec = f.latest()["spectrum32"]
        peaks[fs] = int(np.argmax(spec))
        assert peaks[fs] == k, (fs, hz, spec)
    assert peaks[48000] == peaks[44100]


def test_lowest_bands_share_bins_but_stay_in_place():
    """Bands 0-2 are 7-10 Hz wide and a 4096 FFT's bins 11 Hz apart: an empty band takes the nearest bin (spec)."""
    for k in (0, 1, 2):
        msgs, f = collect()
        feed(f, sine(3 * FS, c.SPEC_CENTRES_HZ[k], 0.3))
        spec = np.array(f.latest()["spectrum32"])
        assert abs(int(np.argmax(spec)) - k) <= 1
        assert spec[6:].max() < spec.max() - 0.3       # the rest of the spectrum stays well down


def test_spectrum32_keeps_the_shape():
    """One shared ref, no per-band normalisation. Summed bin power per log band: pink noise reads flat, red noise
    (falling 6 dB/oct) reads bass over air, and a tone added to pink lifts its band only."""
    n = 6 * FS
    msgs, f = collect()
    feed(f, coloured(n, 1, 0.1))
    pink = np.array(f.latest()["spectrum32"])
    mid = pink[6:29]
    assert mid.max() - mid.min() < 0.12, pink                       # flat within ~7 dB over 66 Hz-11 kHz
    msgs, f = collect()
    f.gate_ref_lufs = -50.0                                           # K-weighting hears little of red noise
    feed(f, coloured(n, 2, 0.1))
    red = np.array(f.latest()["spectrum32"])
    assert f.latest()["gate"] > 0.99
    # 1/f^2 power is 1/f per log band: 3 dB an octave, about 21 dB from 76 Hz to 9.8 kHz (0.35 of the window)
    assert red[2:6].mean() > red[26:30].mean() + 0.25, red
    assert np.all(np.diff(red[8:31]) < 0.03)                         # falling (within noise)
    msgs, f = collect()
    feed(f, coloured(n, 1, 0.1) + sine(n, c.SPEC_CENTRES_HZ[14], 0.3))
    tone = np.array(f.latest()["spectrum32"])
    assert int(np.argmax(tone)) == 14
    others = np.r_[tone[6:12], tone[17:29]]
    assert others.max() < tone[14] - 0.3


# -- hits -------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("block", [128, 96, 1000])
def test_kicks_hit_sub_and_bass_within_15_ms(block):
    n = 10 * FS
    x, onsets = kicks(n)
    x = x + coloured(n, 1, 0.003)
    msgs: list[dict] = []
    current = {}

    def on_block(i, t0, frames):
        current["t0"], current["t1"] = t0, t0 + frames / FS

    def publish(m):
        if m["type"] == c.MUSIC_HIT:                  # emitted while its own block is being processed
            t = m["data"]["t_ms"] / 1000.0
            assert current["t0"] - 1e-9 <= t < current["t1"] + 1e-9
        msgs.append(m)

    f = dsp.Features(publish=publish)
    feed(f, x, block=block, on_block=on_block)
    hits = of_type(msgs, c.MUSIC_HIT)
    both = 0
    for s in onsets:
        t_on = (T0 + s / FS) * 1000.0
        near = [h for h in hits if 0.0 <= h["t_ms"] - t_on < 60.0]
        low = sorted(h["t_ms"] - t_on for h in near if h["band"] in ("sub", "bass"))
        assert low and low[0] < 15.0, (s, near)
        sub = [h["t_ms"] - t_on for h in near if h["band"] == "sub"]
        bass = [h["t_ms"] - t_on for h in near if h["band"] == "bass"]
        assert bass and min(bass) < 15.0, (s, near)
        both += bool(sub and min(sub) < 20.0)                       # sub when the pitch has fallen into it
    assert both >= 0.9 * len(onsets)
    stray = [h for h in hits if h["band"] in ("sub", "bass")
             and min(abs(h["t_ms"] - (T0 + s / FS) * 1000.0 - 30.0) for s in onsets) > 60.0]
    assert len(stray) <= 1, stray                                     # (almost) nothing between the kicks
    d = f.latest()
    assert d["punch"] > 0.5 and d["drive"] > 0.4, d                  # 2 low hits/s, strong
    assert sum(fe["bands"]["sub"]["hits"] for fe in of_type(msgs, c.MUSIC_FEATURES)) == f.stats()["hits"]["sub"]


def test_any_block_size_gives_the_same_hits():
    n = 3 * FS
    x, onsets = kicks(n, first_s=0.6)
    x = x + coloured(n, 1, 0.003)
    times = {}
    for block in (7, 128, 333, 5000):
        msgs, f = collect()
        feed(f, x, block=block)
        times[block] = [(h["band"], h["t_ms"]) for h in of_type(msgs, c.MUSIC_HIT) if h["band"] in ("sub", "bass")]
        assert f.stats()["hops"] == n // 256
    ref = times[128]
    for block, got in times.items():
        assert len(got) == len(ref), block
        for (b1, t1), (b2, t2) in zip(sorted(got), sorted(ref)):
            assert b1 == b2 and abs(t1 - t2) < 1.0, (block, b1, t1, t2)
    msgs, f = collect()
    feed(f, x[:FS // 4], block=1)                     # one sample at a time: slow, but it works
    assert f.stats()["hops"] == (FS // 4) // 256


# -- onsets -----------------------------------------------------------------------------------------------------------
def test_click_track_onsets_land_on_the_clicks():
    n = 3 * FS
    x = coloured(n, 1, 0.002)
    clicks = list(range(int(0.6 * FS) + 37, n - FS // 10, FS // 4))   # off the hop grid on purpose
    rng = np.random.default_rng(3)
    for s in clicks:
        x[s:s + 48] += rng.standard_normal(48) * 0.3                   # a 1 ms broadband click
    msgs, f = collect()
    f.gate_ref_lufs = -45.0                                            # clicks over a quiet room: gate open
    feed(f, x)
    k = f._olog_i
    order = np.arange(k - min(k, f._olog_t.size), k) % f._olog_t.size
    t_hop, flux = f._olog_t[order], f._olog_v[order]
    base = float(np.median(flux))
    hop_s = 256 / FS
    for s in clicks:
        tc = T0 + s / FS
        if tc < t_hop[0] + 3 * hop_s:
            continue
        j0 = int(np.searchsorted(t_hop, tc))                          # the hop whose onset time follows the click
        win = slice(max(0, j0 - 3), j0 + 4)
        jp = win.start + int(np.argmax(flux[win]))
        assert abs(jp - j0) <= 1, (s, jp, j0)                         # +-1 hop (5.3 ms)
        assert flux[jp] > 5 * base + 1.0
    feats = of_type(msgs, c.MUSIC_FEATURES)
    tf = np.array([d["t_ms"] / 1000 for d in feats])
    on = np.array([d["onset"] for d in feats])
    after, between = [], []
    for s in clicks[2:]:
        tc = T0 + s / FS
        j = int(np.searchsorted(tf, tc))
        after.append(on[j:j + 2].max())                               # the two publishes after the click
        between += list(on[(tf > tc + 0.1) & (tf < tc + 0.2)])
    assert np.median(after) > 0.8 and np.median(between) < 0.3, (after, between)


# -- silence, noise, gate ---------------------------------------------------------------------------------------------
def _cpu(f, x, **kw):
    t = time.thread_time()
    feed(f, x, **kw)
    return time.thread_time() - t


def test_silence_reads_zero_and_is_not_slow():
    n = 8 * FS
    msgs, f = collect()
    t_silence = _cpu(f, np.zeros(n))
    d = f.latest()
    assert not of_type(msgs, c.MUSIC_HIT)
    assert d["energy"] == d["gate"] == d["onset"] == d["punch"] == d["drive"] == 0.0
    assert max(d["spectrum32"]) == 0.0 and all(b["level"] == 0.0 for b in d["bands"].values())
    assert d["lufs_m"] == d["lufs_s"] == -70.0 and d["peak_dbfs"] == d["rms_dbfs"] == -120.0
    assert d["brightness"] == d["noisiness"] == 0.0
    for state in (f._zi_band, f._zi_fast, f._slow):                    # dithered: never denormal
        tiny = state[(state != 0.0) & (np.abs(state) < 1e-290)]
        assert tiny.size == 0
    msgs2, g = collect()
    t_noise = _cpu(g, coloured(n, 0, 0.1))
    assert t_silence < 1.5 * t_noise + 0.05, (t_silence, t_noise)

    # loud, then a long silence: everything decays to (exactly) zero, nothing goes denormal
    msgs, f = collect()
    feed(f, np.r_[coloured(3 * FS, 1, 0.1), np.zeros(20 * FS)])
    d = f.latest()
    assert max(d["spectrum32"]) == 0.0 and d["energy"] == 0.0
    sm = f._spec_sm
    assert np.all((sm == 0.0) | (sm > 1e-300))                       # never denormal


def test_white_noise_is_noisy_and_bright_a_sine_is_not():
    n = 3 * FS
    msgs, f = collect()
    feed(f, coloured(n, 0, 0.1))
    white = f.latest()
    msgs, f = collect()
    feed(f, sine(n, 1000.0, 0.3))
    tone = f.latest()
    msgs, f = collect()
    feed(f, sine(n, 100.0, 0.3))
    low = f.latest()
    msgs, f = collect()
    feed(f, coloured(n, 1, 0.1))
    pink = f.latest()
    assert white["noisiness"] > 0.7 and white["brightness"] > 0.8, white
    assert tone["noisiness"] < 0.1 and low["noisiness"] < 0.1
    assert low["brightness"] < 0.3 < tone["brightness"] < white["brightness"]
    assert tone["noisiness"] < pink["noisiness"] < white["noisiness"]


def test_the_gate_holds_quiet_music_down():
    n = 5 * FS
    x = coloured(n, 1, 10 ** (-62 / 20))                              # about -60 LUFS: background
    msgs, f = collect()
    feed(f, x)
    d = f.latest()
    assert d["lufs_s"] < -50.0 and d["gate"] == 0.0 and d["energy"] == 0.0 and max(d["spectrum32"]) == 0.0
    msgs, f = collect()
    f.gate_ref_lufs = -60.0                                           # a mode that expects it this quiet
    feed(f, x)
    d = f.latest()
    assert d["gate"] > 0.9 and max(d["spectrum32"]) > 0.3 and max(b["level"] for b in d["bands"].values()) > 0.3


# -- flags, rate, channels, streams -----------------------------------------------------------------------------------
def test_gap_and_resume_never_fire():
    n = 6 * FS
    x = sine(n, 110.0, 0.3)
    gap_blocks = {i for i in range(200, n // 128) if i % 40 < 4}      # 4 lost packets (10.7 ms) in every 40

    def flagged(resume: bool):
        def flags_for(i):
            if i in gap_blocks:
                return c.AUDIO_GAP
            return c.AUDIO_RESUME if resume and (i - 1) in gap_blocks else 0
        return flags_for

    msgs, f = collect()
    feed(f, x, flags_for=flagged(True))
    assert f.stats()["gaps"] == len(gap_blocks)
    assert not [h for h in of_type(msgs, c.MUSIC_HIT) if h["t_ms"] > (T0 + 0.6) * 1000], of_type(msgs, c.MUSIC_HIT)
    late = [(T0 + i * 128 / FS) for i in range(200, n // 128) if (i - 1) in gap_blocks and i not in gap_blocks]
    feats = of_type(msgs, c.MUSIC_FEATURES)
    assert all(d["onset"] < 0.5 for d in feats if any(0 <= d["t_ms"] / 1000 - t < 0.03 for t in late))
    msgs, f = collect()
    feed(f, x, flags_for=flagged(False))                               # the same splices unflagged do fire
    assert of_type(msgs, c.MUSIC_HIT)


def test_segment_resets_and_keeps_going():
    n = 4 * FS
    x, _ = kicks(n)
    msgs, f = collect()
    feed(f, x[:2 * FS + FS // 2])
    assert f._hops_rs == (2 * FS + FS // 2) // 256
    feed(f, x[2 * FS + FS // 2:], t0=T0 + 2.5, flags_for=lambda i: c.AUDIO_SEGMENT if i == 0 else 0)
    assert f._hops_rs == (FS + FS // 2) // 256                       # counted from the reset
    assert f.latest()["t_ms"] > (T0 + 3.9) * 1000
    assert [h for h in of_type(msgs, c.MUSIC_HIT) if h["t_ms"] > (T0 + 2.9) * 1000]


def test_rate_and_channel_changes_rebuild():
    msgs, f = collect()
    feed(f, sine(2 * FS, 1000.0, 0.3))
    k48 = int(np.argmax(f.latest()["spectrum32"]))
    fs2 = 44100
    feed(f, sine(2 * fs2, 1000.0, 0.3, fs2), fs=fs2, channels=2, t0=T0 + 2.0,
         flags_for=lambda i: c.AUDIO_SEGMENT if i == 0 else 0)
    st = f.stats()
    assert st["fs"] == fs2 and st["channels"] == 2 and f._T.fs == fs2
    d = f.latest()
    assert int(np.argmax(d["spectrum32"])) == k48
    assert -20.0 < d["lufs_m"] < -5.0                                   # two channels of a -13 dBFS sine
    assert abs(d["rms_dbfs"] - 20 * math.log10(0.3 / math.sqrt(2))) < 0.5


def test_a_new_stream_clears_the_history():
    msgs, f = collect()
    feed(f, coloured(3 * FS, 1, 0.1))
    assert f._q.c_s > 0
    feed(f, coloured(FS // 2, 1, 0.1, seed=2), stream="pc22-loop", t0=T0 + 3.0)
    assert f.latest()["stream"] == "pc22-loop"
    assert f._q.c_s <= 3


def test_tick_cadence_25_hz_only_with_fresh_audio():
    msgs, f = collect()
    feed(f, coloured(4 * FS, 1, 0.1))
    feats = of_type(msgs, c.MUSIC_FEATURES)
    assert 99 <= len(feats) <= 103
    ts = [d["t_ms"] for d in feats]
    assert all(b > a for a, b in zip(ts, ts[1:]))
    assert 39.0 < float(np.mean(np.diff(ts))) < 41.0
    f.tick(T0 + 4.5)                                                  # the audio since the last publish
    before = len(of_type(msgs, c.MUSIC_FEATURES))
    assert before == len(feats) + 1
    last = of_type(msgs, c.MUSIC_FEATURES)[-1]
    assert last["gate"] > 0.5 and sum(last["spectrum32"]) > 1.0
    for k in range(300):                                              # 3 s without audio: the fade, then nothing
        f.tick(T0 + 4.51 + k * 0.01)
    fade = of_type(msgs, c.MUSIC_FEATURES)[before:]
    # from the latest hop (seen at ~T0 + 4.0): 25 Hz up to 1.5 s, then one all-zero frame (ticks resume at 4.51)
    assert 22 <= len(fade) <= 28 and f.stats()["fades"] == len(fade)
    assert all(d["gate"] == 0 and d["energy"] == 0 and d["onset"] == 0 and d["punch"] == 0 for d in fade)
    assert all(d["bands"][b]["level"] == 0 and d["bands"][b]["hits"] == 0 for d in fade for b in d["bands"])
    sums = [sum(d["spectrum32"]) for d in fade]
    assert sums[0] < sum(last["spectrum32"]) and all(b <= a for a, b in zip(sums, sums[1:]))
    assert sums[-2] < 0.02 * sum(last["spectrum32"])                   # tau 300 ms over ~1.4 s
    assert sums[-1] == 0 and all(d["presence"] == 0 for d in fade[-1]["bands"].values())
    ts = [d["t_ms"] for d in fade]
    assert ts[0] > last["t_ms"] and all(b > a for a, b in zip(ts, ts[1:]))
    assert 1450 <= ts[-1] - last["t_ms"] <= 1600 and 480 <= fade[0]["t_ms"] - last["t_ms"] <= 560
    json.dumps(fade, allow_nan=False)
    assert set(fade[0]) == set(last) and fade[0]["stream"] == last["stream"]
    n = len(msgs)
    feed(f, coloured(FS, 1, 0.1, seed=4), t0=T0 + 8.0)                 # audio again: real frames
    again = of_type(msgs[n:], c.MUSIC_FEATURES)
    assert len(again) >= 20 and again[-1]["gate"] > 0.5


# -- allocation and cost ----------------------------------------------------------------------------------------------
def test_no_buffer_allocations_per_block():
    f = dsp.Features()
    x = (sine(4 * FS, 220.0, 0.2) + coloured(4 * FS, 1, 0.01)).astype(np.float32).reshape(-1, 1)
    for s in range(0, 2 * FS, 128):
        f.process("room-mic", s, T0 + s / FS, x[s:s + 128], FS, 0)
    blocks = [x[s:s + 128] for s in range(2 * FS, 4 * FS, 128)]
    tracemalloc.start()
    try:
        base, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        for j, blk in enumerate(blocks):
            s = 2 * FS + 128 * j
            f.process("room-mic", s, T0 + s / FS, blk, FS, 0)
        cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak - base < 8192, (base, cur, peak)                      # views and scalars only, no sample buffers
    assert cur - base < 4096


def test_cpu_per_10_ms_of_audio(capsys):
    """Kicks over pink noise, 128-frame blocks, ticks every 10 ms: the whole path, publishing included."""
    n = 10 * FS
    x, _ = kicks(n)
    x = x + coloured(n, 1, 0.01)
    msgs, f = collect()
    cpu = _cpu(f, x)
    per_10ms_us = cpu / (n / FS) * 1e4
    st = f.stats()
    with capsys.disabled():
        print(f"\n[dsp] CPU {per_10ms_us:.0f} us per 10 ms of audio ({per_10ms_us / 100:.1f} % of a core); "
              f"hop p50 {st['hop_ms_p50']} ms p95 {st['hop_ms_p95']} ms; block p95 {st['block_ms_p95']} ms; "
              f"publish p95 {st['tick_ms_p95']} ms; kernel {st['kernel']}")
    assert per_10ms_us < 3000                       # a pathology guard only (shared, loaded host)
    assert st["hop_ms_p95"] < 2.0


def test_fuzz_never_raises_and_stays_finite():
    """Random block sizes, flags, rates, channel counts and a NaN time: no exception, every output finite."""
    rng = np.random.default_rng(11)
    msgs, f = collect()
    t = T0
    fs, ch = FS, 1
    for i in range(400):
        if rng.random() < 0.02:
            fs, ch = int(rng.choice([44100, 48000])), int(rng.integers(1, 5))
        n = int(rng.choice([1, 7, 96, 128, 128, 128, 500, 1500, 3000]))
        pcm = (rng.standard_normal((n, ch)) * rng.choice([0.0, 1e-6, 0.05, 0.9])).astype(np.float32)
        flags = int(rng.choice([0, 0, 0, 0, c.AUDIO_GAP, c.AUDIO_RESUME, c.AUDIO_SEGMENT]))
        t0 = math.nan if rng.random() < 0.01 else t
        f.process("room-mic" if rng.random() < 0.97 else "pc22-loop", 0, t0, pcm, fs, flags)
        t += n / fs
        f.tick(t)
    f.process("room-mic", 0, t, np.zeros(0, dtype=np.float32), FS, 0)               # empty: ignored
    f.process("room-mic", 0, t, (np.ones(256) * 1000).astype(np.int16), FS, 0)      # ints: converted
    feats = of_type(msgs, c.MUSIC_FEATURES)
    assert feats
    json.dumps(msgs, allow_nan=False)
    for d in feats:
        assert all(0.0 <= v <= 1.0 for v in d["spectrum32"])
    assert f.stats()["blocks"] >= 400
