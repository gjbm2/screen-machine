"""dsp.Features fed the receiver's coalesced blocks (contract.OnAudio: up to receiver.COALESCE_MAX_FRAMES contiguous
frames a call) gives the numbers it gives packet by packet.

Shared plumbing, so real tests: the same 12 s of music (kicks, pink noise, hats, a chord) as 128-frame packets and as
1024- and 4096-frame blocks: the same hits in the same order (t_ms within the 1 us rounding, strength equal), the
same onset log, onsets, hops and medium frames (low_db exact: it read the block end's envelopes before); other hops
and 44.1 kHz too; a coalesced AUDIO_RESUME block suppresses only its first packet; the noise floor's bins are exactly
100 ms at any block size; read-only gap blocks; the ring's margin; and the cost per packet falls with the block
(printed; run with -s to see it; asserted only as a pathology guard on a shared, loaded host).
"""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from show_service import contract as c
from show_service import dsp

FS = 48000
T0 = 1000.0


# -- the signal -------------------------------------------------------------------------------------------------------
def music(n: int, fs: int = FS) -> np.ndarray:
    """Kicks, pink noise, hats and a held chord. The kick and hat periods are off every grid (hop, 100 ms frame,
    4 s rate window), so no onset sits exactly on the onset-rate window's edge, where 1e-13 s of time rounding
    decides a tie."""
    rng = np.random.default_rng(1)
    spec = np.fft.rfft(rng.standard_normal(n))
    spec /= np.maximum(np.fft.rfftfreq(n, 1.0 / fs), 20.0) ** 0.5
    x = np.fft.irfft(spec, n)
    x *= 0.01 / np.std(x)
    t = np.arange(int(0.3 * fs)) / fs
    kick = 0.6 * np.sin(2 * np.pi * np.cumsum(50 + 60 * np.exp(-t / 0.01)) / fs) * np.exp(-t / 0.12)
    for s in np.arange(0.5137, n / fs - 0.4, 0.5171):
        i = int(s * fs)
        x[i:i + kick.size] += kick[:n - i]
    hl = int(0.05 * fs)
    hat = np.diff(np.r_[0.0, rng.standard_normal(hl)]) * 0.15 * np.exp(-np.arange(hl) / fs / 0.012)
    for s in np.arange(0.2611, n / fs - 0.1, 0.2731):
        i = int(s * fs)
        x[i:i + hl] += hat[:n - i]
    tt = np.arange(n) / fs
    for m in (48, 60, 64, 67):
        x += 0.04 * np.sin(2 * np.pi * 440 * 2 ** ((m - 69) / 12) * tt)
    return x


def run(x: np.ndarray, block: int, *, fs: int = FS, hop: int = dsp.HOP, tick: bool = False, batch: bool = True):
    """Feed x in blocks (no ticks by default: the rolling percentiles then stay put, so strengths compare exactly).
    Returns (features, hits, medium frames), a frame being the medium lane's numbers right after it ran."""
    msgs: list[dict] = []
    f = dsp.Features(publish=msgs.append, hop=hop, batch=batch)
    frames: list[tuple] = []
    medium = f._medium

    def recording(*a):
        medium(*a)
        frames.append((f._m_t, f._m_low, f._m_hdb, f._m_perc, f._m_harm, f._m_pulse, f._m_rate,
                       f._cn.copy(), f._cavg.copy(), f._mfcc.copy()))

    f._medium = recording
    x32 = np.asarray(x, dtype=np.float32)
    next_tick = T0
    for s in range(0, x32.size, block):
        blk = x32[s:s + block].reshape(-1, 1)
        f.process("room-mic", s, T0 + s / fs, blk, fs, 0)
        now = T0 + (s + blk.shape[0]) / fs
        while tick and next_tick <= now:
            f.tick(next_tick)
            next_tick += c.TICK_S
    return f, [m["data"] for m in msgs if m["type"] == c.MUSIC_HIT], frames


def assert_same(ref, got, label):
    fr, hr, mr = ref
    fg, hg, mg = got
    assert [h["band"] for h in hg] == [h["band"] for h in hr], label           # the same hits, in the same order
    for a, b in zip(hr, hg):
        assert abs(a["t_ms"] - b["t_ms"]) <= 0.001 + 1e-9, (label, a, b)      # the 1 us rounding of t0 + i / fs
        assert a["strength"] == b["strength"], (label, a, b)
    assert np.allclose(fg._slow, fr._slow, rtol=1e-12, atol=0.0), label
    assert np.allclose(fg._zi_band, fr._zi_band, rtol=1e-12, atol=1e-300), label
    assert np.allclose(fg._olog_v, fr._olog_v, rtol=1e-9, atol=1e-9), label
    assert np.allclose(fg._olog_t, fr._olog_t, rtol=0.0, atol=1e-9), label
    assert fg._onsets_total == fr._onsets_total and fg._hops == fr._hops and fg._olog_i == fr._olog_i, label
    assert len(mg) == len(mr), label
    for a, b in zip(mr, mg):
        assert abs(a[0] - b[0]) < 1e-9, (label, a[0], b[0])
        assert abs(a[1] - b[1]) < 1e-9, (label, a[0], a[1], b[1])            # low_db: was up to 13 dB off at 4096
        assert np.allclose(a[2:7], b[2:7], rtol=1e-9, atol=1e-9), (label, a[0], a[2:7], b[2:7])
        for u, v in zip(a[7:], b[7:]):
            assert np.allclose(u, v, rtol=1e-9, atol=1e-12), (label, a[0])


# -- the same numbers -------------------------------------------------------------------------------------------------
def test_coalesced_blocks_give_the_packets_numbers():
    x = music(12 * FS)
    ref = run(x, 128)
    assert len(ref[1]) > 50 and len(ref[2]) == 120 and ref[0]._onsets_total > 20          # the signal does things
    assert {h["band"] for h in ref[1]} >= {"sub", "bass", "high_mid", "air"}
    for block in (1024, 4096):
        assert_same(ref, run(x, block), block)


def test_the_batch_switch_off_gives_the_same_numbers():
    """batch=False (the rollback switch): coalesced blocks run packet-sized pieces and hop by hop, today's way, with
    the same numbers (low_db included: the slow at each piece's end is kept either way), and stats say so."""
    x = music(5 * FS)
    ref = run(x, 128)
    for block in (128, 1024, 4096):
        got = run(x, block, batch=False)
        assert_same(ref, got, ("off", block))
        st = got[0].stats()
        assert st["batch"] is False and st["batch_fallbacks"] == 0
    assert dsp.Features().stats()["batch"] is True


@pytest.mark.parametrize("fs,hop,block", [(44100, 256, 4096), (48000, 128, 4096), (48000, 1024, 4096),
                                          (48000, 256, 4096 + 128), (48000, 256, 768)])
def test_other_rates_hops_and_sizes_give_the_packets_numbers(fs, hop, block):
    """44.1 kHz; a hop of 128 (32 hops a block, two groups, SuperFlux lag 4) and of 1024 (lag 1); a block over
    CHUNK (split); 6 packets (3 hops: the smallest multi-hop block)."""
    x = music(5 * fs, fs)
    assert_same(run(x, 128, fs=fs, hop=hop), run(x, block, fs=fs, hop=hop), (fs, hop, block))


def test_odd_blocks_keep_todays_tail_pieces():
    """A block that isn't whole packets: its whole pieces in one pass, the tail piece alone, as before (the pieces
    start at the block's start, so hits can differ from 128-frame packets by a piece: test_dsp's any-block-size)."""
    x = music(3 * FS)
    for block in (333, 1000, 5000):
        f, hits, frames = run(x, block)
        assert f._hops == x.size // 256 and len(frames) == 29          # hop 562 ends at 143872: 4800 .. 139200
        ref = run(x, 128)[1]
        assert len(hits) == len(ref)
        for a, b in zip(sorted((h["band"], h["t_ms"]) for h in hits), sorted((h["band"], h["t_ms"]) for h in ref)):
            assert a[0] == b[0] and abs(a[1] - b[1]) < 1.0, (block, a, b)


def test_ticks_between_blocks_still_publish_and_hits_keep_their_order():
    """With ticks (one per 10 ms of audio, so several a 4096 block apart): the same hits in the same order; the
    features keep coming at most 50 Hz."""
    x = music(6 * FS)
    msgs: list[dict] = []
    f = dsp.Features(publish=msgs.append)
    x32 = x.astype(np.float32)
    for s in range(0, x32.size, 4096):
        blk = x32[s:s + 4096].reshape(-1, 1)
        f.process("room-mic", s, T0 + s / FS, blk, FS, 0)
        f.tick(T0 + (s + blk.shape[0]) / FS)
    hits = [m["data"] for m in msgs if m["type"] == c.MUSIC_HIT]
    ref = run(x, 128, tick=True)[1]
    assert [h["band"] for h in hits] == [h["band"] for h in ref]
    assert all(abs(a["t_ms"] - b["t_ms"]) <= 0.001 + 1e-9 for a, b in zip(hits, ref))
    feats = [m["data"] for m in msgs if m["type"] == c.MUSIC_FEATURES]
    assert len(feats) == len(range(0, x32.size, 4096))          # one publish per block: 11.7 Hz here, never more
    ts = [d["t_ms"] for d in feats]
    assert all(b > a for a, b in zip(ts, ts[1:]))


def test_a_batched_pass_that_raises_is_redone_the_old_way():
    """Degrade, never refuse: a batched hit pass that raises (here after it ran: its filters and detectors moved)
    is redone piece by piece from the state before it, and a multi-hop pass hop by hop; the numbers are the same
    and stats count each fallback."""
    x = music(4 * FS)
    ref = run(x, 128)
    msgs: list[dict] = []
    f = dsp.Features(publish=msgs.append)
    calls = {"hit": 0, "flux": 0}
    hit_block, hops_flux = f._hit_block, f._hops_flux

    def bad_hit_block(*a):
        calls["hit"] += 1
        hit_block(*a)
        if calls["hit"] % 3 == 0:
            raise RuntimeError("after the work")

    def bad_hops_flux(*a):
        calls["flux"] += 1
        if calls["flux"] % 4 == 0:
            raise FloatingPointError("before the work")
        return hops_flux(*a)

    f._hit_block, f._hops_flux = bad_hit_block, bad_hops_flux
    frames: list[tuple] = []
    medium = f._medium

    def recording(*a):
        medium(*a)
        frames.append((f._m_t, f._m_low, f._m_hdb, f._m_perc, f._m_harm, f._m_pulse, f._m_rate,
                       f._cn.copy(), f._cavg.copy(), f._mfcc.copy()))

    f._medium = recording
    x32 = x.astype(np.float32)
    for s in range(0, x32.size, 4096):
        f.process("room-mic", s, T0 + s / FS, x32[s:s + 4096].reshape(-1, 1), FS, 0)
    nblk = len(range(0, x32.size, 4096))
    assert f.stats()["batch_fallbacks"] == calls["hit"] // 3 + calls["flux"] // 4 > 10
    assert_same(ref, (f, [m["data"] for m in msgs if m["type"] == c.MUSIC_HIT], frames), "fallbacks")
    assert calls["hit"] == nblk


# -- flags and bins ---------------------------------------------------------------------------------------------------
def test_a_coalesced_resume_block_suppresses_only_its_first_packet():
    """32 packets after a hole as one AUDIO_RESUME block: hits are held for a packet and a hop (384 samples), not
    for the whole block (4352 before), so a kick 500 samples in still fires."""
    n = 2 * FS
    bed = music(n)[:n] * 0.0 + np.random.default_rng(4).standard_normal(n) * 0.003
    t = np.arange(int(0.3 * FS)) / FS
    kick = 0.6 * np.sin(2 * np.pi * np.cumsum(50 + 60 * np.exp(-t / 0.01)) / FS) * np.exp(-t / 0.12)
    msgs: list[dict] = []
    f = dsp.Features(publish=msgs.append)
    s = 0
    for s in range(0, n, 128):                                  # a settled detector
        f.process("room-mic", s, T0 + s / FS, bed[s:s + 128].astype(np.float32).reshape(-1, 1), FS, 0)
    s = n
    f.process("room-mic", s, T0 + s / FS, np.broadcast_to(np.zeros((1280, 1), np.float32), (1280, 1)), FS,
              c.AUDIO_GAP)                                      # a 10-packet hole: a read-only view of zeros
    s += 1280
    blk = np.random.default_rng(5).standard_normal(4096) * 0.003
    blk[500:500 + kick.size] += kick[:4096 - 500]
    k0 = len(msgs)
    f.process("room-mic", s, T0 + s / FS, blk.astype(np.float32).reshape(-1, 1), FS, c.AUDIO_RESUME)
    hits = [m["data"] for m in msgs[k0:] if m["type"] == c.MUSIC_HIT]
    t_blk = (T0 + s / FS) * 1000.0
    low = [h["t_ms"] - t_blk for h in hits if h["band"] in ("sub", "bass")]
    assert low and min(low) >= 384 / FS * 1000.0 - 1e-6, hits
    assert min(low) < (500 / FS + 0.015) * 1000.0, hits         # the kick, within 15 ms of its onset
    assert f.stats()["gaps"] == 1


@pytest.mark.parametrize("fs", [48000, 44100])
def test_floor_bins_are_exactly_100_ms_at_any_block_size(fs):
    """Levels alternating -20 / -40 dBFS every 100 ms: each bin reads its own level whatever the blocks (a bin that
    closed at a block boundary past 100 ms took in its neighbour's)."""
    bin_n = int(round(0.1 * fs))
    nb = 40
    rng = np.random.default_rng(6)
    lv = np.where(np.arange(nb) % 2 == 0, -20.0, -40.0) + rng.uniform(-1, 1, nb)
    x = np.concatenate([rng.standard_normal(bin_n) * 10 ** (v / 20) for v in lv]).astype(np.float32)
    want = [10.0 * math.log10(float(np.mean(x[k * bin_n:(k + 1) * bin_n].astype(np.float64) ** 2)))
            for k in range(nb)]
    for block in (7, 128, 4096):
        f = dsp.Features()
        for s in range(0, x.size, block):
            f.process("room-mic", s, T0 + s / fs, x[s:s + block].reshape(-1, 1), fs, 0)
        got = list(f._floor_vals)
        assert len(got) == nb and f._fl_n == 0, (block, len(got), f._fl_n)
        assert np.allclose(got, want, atol=1e-3), (block, np.max(np.abs(np.array(got) - want)))


def test_gap_blocks_may_be_read_only_and_are_skipped():
    f = dsp.Features()
    x = music(FS // 2).astype(np.float32).reshape(-1, 1)
    f.process("room-mic", 0, T0, x, FS, 0)
    hops = f.stats()["hops"]
    zeros = np.broadcast_to(np.zeros((1, 1), np.float32), (12000, 1))   # 250 ms of gap fill, read-only
    assert not zeros.flags.writeable
    f.process("room-mic", x.shape[0], T0 + 0.5, zeros, FS, c.AUDIO_GAP)
    assert f.stats()["gaps"] == 1 and f.stats()["hops"] == hops and f._n == x.shape[0]


def test_the_ring_holds_a_chunk_behind_every_read():
    for fs in (44100, 48000):
        T = dsp._Tables(fs)
        assert dsp.RING_N >= max(T.n_raw, dsp.SPEC_N, dsp.FAST_N) + dsp.CHUNK + dsp.HOP_MAX, fs
    with pytest.raises(ValueError):
        dsp.Features(hop=2048)


# -- cost -------------------------------------------------------------------------------------------------------------
def test_cost_per_packet_falls_with_the_block(capsys):
    """Features.process CPU per 128-frame packet at 1, 8 and 32 packets a block (min of 3 interleaved runs of 1.5 s
    of music). Printed; asserted only against a pathology (batching undone) on a shared, loaded host."""
    x = music(int(1.5 * FS) + 4096).astype(np.float32).reshape(-1, 1)
    n = int(1.5 * FS) // 4096 * 4096
    best = {}
    for _ in range(3):
        for k in (1, 8, 32):
            B = 128 * k
            f = dsp.Features()
            t = time.thread_time()
            for s in range(0, n, B):
                f.process("room-mic", s, T0 + s / FS, x[s:s + B], FS, 0)
            us = (time.thread_time() - t) / (n / 128) * 1e6
            best[k] = min(best.get(k, math.inf), us)
    with capsys.disabled():
        print(f"\n[dsp blocks] us per packet: 1 {best[1]:.0f}, 8 {best[8]:.0f} ({best[8] / best[1]:.0%}), "
              f"32 {best[32]:.0f} ({best[32] / best[1]:.0%})")
    assert best[32] < 0.75 * best[1], best
