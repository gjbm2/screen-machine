"""ring.Rings is shared plumbing (README "Stage 3 > S3.12"): writes on show-rx, reads from any thread. Real tests:
resampling and timing, segments, gaps, a lapped read (deterministic and threaded), eviction, and the cost per block
(``-s`` prints it)."""

from __future__ import annotations

import threading
import time

import numpy as np

from show_service import contract as c
from show_service.ring import Rings

FS_IN = 48000
BLOCK = 128


def feed(r: Rings, stream: str, seconds: float, *, t0: float, n0: int = 0, fs: int = FS_IN, ch: int = 2,
         freq: float = 440.0, amp: float = 0.5, flags0: int = c.AUDIO_SEGMENT, gap_blocks=()) -> int:
    """Blocks of a sine (stereo), from input sample n0 at host time t0; returns the next n0."""
    n_total = int(seconds * fs)
    n = 0
    first = True
    while n < n_total:
        k = min(BLOCK, n_total - n)
        i = n0 + n
        t = np.arange(i, i + k) / fs
        x = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
        flags = flags0 if first else 0
        b = n // BLOCK
        if b in gap_blocks:
            x[:] = 0.0
            flags |= c.AUDIO_GAP
        pcm = np.repeat(x[:, None], ch, axis=1) if ch > 1 else x[:, None]
        r.write(stream, i, t0 + (i - n0) / fs, np.ascontiguousarray(pcm), fs, flags)
        n += k
        first = False
    return n0 + n_total


def peak_hz(pcm: np.ndarray, fs: int) -> float:
    x = pcm.astype(np.float64)
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.argmax(spec) * fs / len(x))


def test_resamples_to_16k_mono_and_keeps_time():
    r = Rings(seconds=10.0)
    feed(r, "room-mic", 3.0, t0=100.0)
    clip = r.latest("room-mic", 2.0)
    assert clip is not None and clip.fs == c.RING_FS and clip.pcm.dtype == np.int16
    assert abs(len(clip.pcm) - 2 * c.RING_FS) <= 2
    assert abs(peak_hz(clip.pcm, clip.fs) - 440.0) < 2.0
    assert 0.45 * 32767 < np.abs(clip.pcm[1000:]).max() < 0.55 * 32767
    # the newest sample ends at the last input's time (soxr's output is delay-compensated, the tail not yet out)
    end = clip.t0_s + len(clip.pcm) / clip.fs
    assert 102.95 < end <= 103.0 + 1e-6
    # a window by time: the sample at a known time is the sine's phase there
    w = r.clip("room-mic", 101.0, 101.5)
    assert abs(w.t0_s - 101.0) < 1e-3 and abs(len(w.pcm) - 8000) <= 1
    want = 0.5 * np.sin(2 * np.pi * 440.0 * (w.t0_s - 100.0 + np.arange(200) / c.RING_FS)) * 32767
    assert np.max(np.abs(w.pcm[:200] - want)) < 0.03 * 32767
    assert w.gaps == []
    assert r.latest(None, 1.0).stream == "room-mic"        # None = the stream written last


def test_segments_are_time_linear_with_unheld_spans_zeroed():
    r = Rings(seconds=30.0)
    feed(r, "room-mic", 2.0, t0=10.0)
    feed(r, "room-mic", 2.0, t0=15.0, n0=0, freq=880.0)   # a new segment 3 s after the first one ended
    clip = r.clip("room-mic", 10.0, 17.0)
    assert abs(clip.t0_s - 10.0) < 1e-3
    fs = clip.fs
    assert 7 * fs - 800 <= len(clip.pcm) <= 7 * fs       # the resampler still holds the newest ~30 ms
    hole = clip.pcm[int(2.1 * fs):int(4.9 * fs)]
    assert not hole.any()                                  # nothing held there: zeros
    assert any(abs(a - 12.0) < 0.05 and abs(b - 15.0) < 0.05 for a, b in clip.gaps), clip.gaps
    assert abs(peak_hz(clip.pcm[int(5.2 * fs):int(6.9 * fs)], fs) - 880.0) < 3.0
    assert abs(peak_hz(clip.pcm[int(0.2 * fs):int(1.9 * fs)], fs) - 440.0) < 3.0
    # latest() of a short window stays inside the newest segment
    last = r.latest("room-mic", 1.0)
    assert last.gaps == [] and abs(last.t0_s - 16.0) < 0.05


def test_a_jump_in_n0_starts_a_segment():
    r = Rings(seconds=10.0)
    nxt = feed(r, "s", 1.0, t0=0.0)
    feed(r, "s", 1.0, t0=2.0, n0=nxt + FS_IN, flags0=0)   # n0 skipped a second without AUDIO_SEGMENT
    clip = r.clip("s", 0.0, 3.0)
    assert any(abs(a - 1.0) < 0.05 and abs(b - 2.0) < 0.05 for a, b in clip.gaps), clip.gaps


def test_audio_gap_blocks_are_recorded():
    r = Rings(seconds=10.0)
    gap = set(range(100, 120))                             # 20 blocks = 53 ms, starting 0.267 s in
    feed(r, "s", 1.0, t0=50.0, gap_blocks=gap)
    clip = r.latest("s", 5.0)
    assert len(clip.gaps) == 1
    a, b = clip.gaps[0]
    assert abs(a - (50.0 + 100 * BLOCK / FS_IN)) < 2e-3 and abs(b - (50.0 + 120 * BLOCK / FS_IN)) < 2e-3
    assert r.info()["s"]["gaps"] == 1


def test_wraps_and_holds_only_the_ring():
    r = Rings(seconds=1.0)
    feed(r, "s", 2.5, t0=0.0)
    info = r.info()["s"]
    assert abs(info["held_s"] - 1.0) < 1e-3
    clip = r.clip("s", 0.0, 10.0)
    assert abs(clip.t0_s - (info["t_last_s"] - 1.0)) < 1e-3 and len(clip.pcm) == c.RING_FS
    assert abs(peak_hz(clip.pcm, clip.fs) - 440.0) < 3.0


def test_lapped_read_is_cut_deterministic():
    r = Rings(seconds=1.0)
    feed(r, "s", 2.0, t0=0.0)
    ring = r._rings["s"]
    full = r.clip("s", 0.0, 10.0)
    assert len(full.pcm) == c.RING_FS and ring.lapped_reads == 0
    ring.w_pending = ring.w + 1600                         # as if a 0.1 s write were landing while we copy
    cut = r.clip("s", 0.0, 10.0)
    assert ring.lapped_reads == 1
    assert abs(len(cut.pcm) - (c.RING_FS - 1600)) <= 1
    assert abs(cut.t0_s - (full.t0_s + 0.1)) < 1e-3
    ring.w_pending = ring.w
    assert r.latest("s", 0.5) is not None                  # recent reads are untouched


def test_lapped_reads_never_return_torn_data_threaded():
    """A 16 kHz counter (no resampling, exact values) written fast into a tiny ring while another thread reads the
    whole ring: every clip must be consecutive."""
    r = Rings(seconds=0.1, max_streams=1)                  # 1600 samples
    stop = threading.Event()
    blk = 256

    def writer():
        i = 0
        while not stop.is_set():
            v = ((np.arange(i, i + blk) % 30000) / 32767.0).astype(np.float32)[:, None]
            r.write("t", i, i / 16000.0, v, 16000, c.AUDIO_SEGMENT if i == 0 else 0)
            i += blk

    th = threading.Thread(target=writer, daemon=True)
    th.start()
    try:
        bad = reads = 0
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            clip = r.clip("t", -1.0, 1e9)
            if clip is None or len(clip.pcm) < 2:
                continue
            reads += 1
            d = np.diff(clip.pcm.astype(np.int64)) % 30000
            if np.any(d != 1):
                bad += 1
    finally:
        stop.set()
        th.join(2)
    print(f"\nthreaded: {reads} reads, {r._rings['t'].lapped_reads} lapped and cut, {bad} torn")
    assert reads > 50 and bad == 0


def test_over_max_streams_drops_the_one_written_longest_ago():
    r = Rings(seconds=2.0, max_streams=2)
    feed(r, "a", 0.5, t0=0.0)
    feed(r, "b", 0.5, t0=1.0)
    feed(r, "a", 0.5, t0=2.0)                              # a is newer now
    feed(r, "c", 0.5, t0=3.0)
    assert set(r.streams()) == {"a", "c"}
    assert r.latest("b") is None and r.latest("a") is not None and r.latest("c") is not None


def test_not_armed_records_nothing_and_rearming_forgets_what_was_held():
    r = Rings(seconds=20.0)
    n = feed(r, "room-mic", 3.0, t0=100.0)
    r.armed = False                                    # the listener: no clip could go
    writes = r.info()["room-mic"]["writes"]
    n = feed(r, "room-mic", 2.0, t0=103.0, n0=n, flags0=0)
    assert r.info()["room-mic"]["writes"] == writes       # skipped on show-rx: nothing resampled
    r.armed = True
    feed(r, "room-mic", 1.0, t0=105.0, n0=n, flags0=0)
    info = r.info()["room-mic"]
    assert abs(info["held_s"] - 1.0) < 0.05 and abs(info["t_first_s"] - 105.0) < 0.01   # only the new second
    clip = r.latest("room-mic", 10.0)
    assert clip.t0_s >= 105.0 - 1e-3 and clip.gaps == []      # a clip never spans the pause


def test_nothing_held():
    r = Rings()
    assert r.latest() is None and r.latest("x") is None and r.clip("x", 0, 1) is None and r.info() == {}
    feed(r, "s", 0.2, t0=5.0)
    assert r.clip("s", 100.0, 200.0) is None and r.clip("s", 1.0, 0.5) is None


def test_mono_16k_input_is_copied_without_resampling():
    r = Rings(seconds=2.0)
    x = (np.arange(1600) / 32767.0).astype(np.float32)
    r.write("s", 0, 7.0, x, 16000, c.AUDIO_SEGMENT)
    clip = r.latest("s", 1.0)
    assert np.array_equal(clip.pcm, np.arange(1600, dtype=np.int16)) and clip.t0_s == 7.0


def test_cost_per_block():
    """README S3.3: Rings.write <= 60 us a 128-frame block (p95, under the 4K render). Here: p50 asserted, p95
    printed."""
    r = Rings()
    blk = (0.1 * np.random.default_rng(1).standard_normal((BLOCK, 2))).astype(np.float32)
    for i in range(400):                                   # warm up
        r.write("room-mic", i * BLOCK, i * BLOCK / FS_IN, blk, FS_IN, c.AUDIO_SEGMENT if i == 0 else 0)
    ts = []
    for i in range(400, 6400):
        t = time.perf_counter()
        r.write("room-mic", i * BLOCK, i * BLOCK / FS_IN, blk, FS_IN, 0)
        ts.append(time.perf_counter() - t)
    p50, p95 = np.percentile(ts, 50) * 1e6, np.percentile(ts, 95) * 1e6
    print(f"\nRings.write 128 frames 48k stereo: p50 {p50:.1f} us, p95 {p95:.1f} us, max {max(ts) * 1e6:.0f} us")
    assert p50 < 60.0
