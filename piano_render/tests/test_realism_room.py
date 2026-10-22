"""The room (dsp/room.py): convolution modes against scipy, IR normalisation, the synthetic IR's decay, the wet
level, and the fallbacks."""

import numpy as np
import pytest
import soundfile as sf
from scipy.signal import butter, fftconvolve, sosfilt

from piano_render.dsp import room as RM

SR = 48000


def test_true_stereo_wet_level_ignores_input_correlation(tmp_path):
    p = _write_ir(tmp_path / "ir4.wav", 4, secs=0.8)
    room = {"id": "t", "ir": str(p), "wet_db": -12}
    rng = np.random.default_rng(9)
    for corr in (False, True):
        a = 0.1 * rng.standard_normal(3 * SR)
        b = a if corr else 0.1 * rng.standard_normal(3 * SR)
        x = np.stack([a, b], axis=1).astype(np.float32)
        y, _ = RM.wet(x, room, SR)
        seg = slice(SR, 2 * SR)
        rel = 10 * np.log10(np.mean(y[seg].astype(np.float64) ** 2) / np.mean(x[seg].astype(np.float64) ** 2))
        assert rel == pytest.approx(-12, abs=1.0)


@pytest.mark.parametrize("extra", [300, 5000])
def test_in_place_overlap_add_across_blocks(extra):
    """Several blocks, including a last block shorter than the IR (the carried spill-over)."""
    rng = np.random.default_rng(2)
    L = 1500
    h = (rng.standard_normal((L, 4)) * np.exp(-np.arange(L) / 400)[:, None]).astype(np.float32)
    _, blk = RM._block_plan(L)
    n = 2 * blk + extra
    x = rng.standard_normal((n, 2)).astype(np.float32)
    buf = np.zeros((n + L - 1 + 7, 2), np.float32)
    buf[:n] = x
    assert RM.convolve_into(buf, n, h, "true", gain=0.5) == n + L - 1
    ref_l = fftconvolve(x[:, 0], h[:, 0]) + fftconvolve(x[:, 1], h[:, 2])
    ref_r = fftconvolve(x[:, 0], h[:, 1]) + fftconvolve(x[:, 1], h[:, 3])
    want = np.stack([ref_l, ref_r], axis=1) * 0.5
    want[:n] += x
    assert np.allclose(buf[:n + L - 1], want, atol=5e-4)
    assert not buf[n + L - 1:].any()


def test_convolve_matches_scipy_in_every_mode():
    rng = np.random.default_rng(0)
    x = rng.standard_normal((9000, 2)).astype(np.float32)
    h4 = (rng.standard_normal((700, 4)) * np.exp(-np.arange(700) / 150)[:, None]).astype(np.float32)
    y = RM.convolve(x, h4, "true")
    assert y.shape == (9000 + 700 - 1, 2)
    ref_l = fftconvolve(x[:, 0], h4[:, 0]) + fftconvolve(x[:, 1], h4[:, 2])
    ref_r = fftconvolve(x[:, 0], h4[:, 1]) + fftconvolve(x[:, 1], h4[:, 3])
    assert np.allclose(y[:, 0], ref_l, atol=2e-4) and np.allclose(y[:, 1], ref_r, atol=2e-4)
    y = RM.convolve(x, h4[:, :2], "mono-in")
    m = 0.5 * (x[:, 0] + x[:, 1])
    assert np.allclose(y[:, 0], fftconvolve(m, h4[:, 0]), atol=2e-4)
    assert np.allclose(y[:, 1], fftconvolve(m, h4[:, 1]), atol=2e-4)
    y = RM.convolve(x, h4[:, :1], "per-channel")
    assert np.allclose(y[:, 1], fftconvolve(x[:, 1], h4[:, 0]), atol=2e-4)


def _write_ir(path, ch, sr=SR, secs=0.6, seed=1):
    rng = np.random.default_rng(seed)
    n = int(secs * sr)
    h = rng.standard_normal((n, ch)) * np.exp(-np.arange(n) / (0.1 * sr))[:, None] * 0.3
    sf.write(str(path), h.astype(np.float32), sr, subtype="FLOAT")
    return path


@pytest.mark.parametrize("ch,mode", [(4, "true"), (2, "mono-in"), (1, "per-channel")])
def test_ir_files_are_normalised(tmp_path, ch, mode):
    p = _write_ir(tmp_path / f"ir{ch}.wav", ch)
    h, m, src = RM.load_ir({"ir": str(p), "wet_db": -16}, SR)
    assert m == mode and src == "file"
    e = np.sum(h.astype(np.float64) ** 2, axis=0)
    total = (e.sum() / 2) if mode == "true" else e.mean()
    assert total == pytest.approx(1.0, rel=1e-3)


def test_ir_at_another_rate_is_resampled(tmp_path):
    p = _write_ir(tmp_path / "ir441.wav", 2, sr=44100, secs=0.5)
    h48, _, _ = RM.load_ir({"ir": str(p), "wet_db": -16}, SR)
    assert h48.shape[0] == pytest.approx(0.5 * SR, rel=0.05)


def _t30(h, sr, lo, hi):
    """T60 from the -5..-35 dB slope of the Schroeder curve in a band."""
    sos = butter(4, [lo, hi], btype="bandpass", fs=sr, output="sos")
    y = sosfilt(sos, h.astype(np.float64))
    edc = np.cumsum((y ** 2)[::-1])[::-1]
    edc_db = 10 * np.log10(edc / edc[0] + 1e-30)
    i5, i35 = np.argmax(edc_db < -5), np.argmax(edc_db < -35)
    return 2.0 * (i35 - i5) / sr


def test_synthetic_ir_decay_predelay_and_width():
    h = RM.synthetic_ir(SR, rt60_s=1.0, predelay_ms=12, width=1.0)
    assert h.shape[1] == 2 and np.isfinite(h).all()
    first = int(np.flatnonzero(np.abs(h).max(axis=1) > 0)[0])
    assert first >= int(0.012 * SR) - 1
    t_mid = _t30(h[:, 0], SR, 700, 1400)
    t_hi = _t30(h[:, 0], SR, 6000, 10000)
    assert t_mid == pytest.approx(1.0, rel=0.15)
    assert t_hi < 0.75 * t_mid                                   # HF dies first
    c = np.corrcoef(h[:, 0], h[:, 1])[0, 1]
    assert abs(c) < 0.2                                          # left and right decorrelated
    mono = RM.synthetic_ir(SR, rt60_s=1.0, predelay_ms=12, width=0.0)
    assert np.allclose(mono[:, 0], mono[:, 1])
    again = RM.synthetic_ir(SR, rt60_s=1.0, predelay_ms=12, width=1.0)
    assert np.array_equal(h, again)                              # repeatable (the render cache relies on it)


def test_wet_level_and_fallbacks(tmp_path):
    rng = np.random.default_rng(5)
    mono = 0.1 * rng.standard_normal(4 * SR)
    x = np.stack([mono, mono], axis=1).astype(np.float32)           # a mono-in IR hears the mono mix
    room = {"id": "small", "ir": None, "synth": {"rt60_s": 0.6, "predelay_ms": 10, "width": 1.0}, "wet_db": -16}
    y, st = RM.wet(x, room, SR)
    assert st["ir_source"] == "synth" and y.shape[0] > x.shape[0]
    seg = slice(SR, 3 * SR)                                          # unit-energy IR: white noise comes out at wet_db
    rel = 10 * np.log10(np.mean(y[seg].astype(np.float64) ** 2) / np.mean(x[seg].astype(np.float64) ** 2))
    assert rel == pytest.approx(-16, abs=1.0)
    # an IR file that isn't installed falls back to the synth settings; neither -> no wet
    y2, st2 = RM.wet(x, dict(room, ir=str(tmp_path / "missing.wav")), SR)
    assert st2["ir_source"] == "synth"
    y3, st3 = RM.wet(x, dict(room, ir=str(tmp_path / "missing.wav"), synth=None), SR)
    assert y3 is None and "skipped" in st3
    y4, st4 = RM.wet(x, dict(room, wet_db=None), SR)
    assert y4 is None
    # the wet starts no earlier than the pre-delay
    imp = np.zeros((SR, 2), np.float32)
    imp[1000] = 1.0
    yi, _ = RM.wet(imp, room, SR)
    assert np.abs(yi[:1000 + int(0.01 * SR) - 2]).max() < 1e-6       # FFT round-off only (-120 dB)
