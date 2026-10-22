"""The core builder's minimal realism stage (EQ + width). Skipped once the realism builder's module replaces it."""
import numpy as np
import pytest

from piano_render import realism

SR = 48000

if not str(getattr(realism, "VERSION", "")).startswith("minimal"):
    pytest.skip("realism.py is no longer the minimal version", allow_module_level=True)


def _noise(secs=2.0, seed=1):
    return np.random.default_rng(seed).standard_normal((int(secs * SR), 2)).astype(np.float32) * 0.1


def _band_db(x, lo, hi):
    X = np.abs(np.fft.rfft(x.astype(np.float64)))
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return 10 * np.log10(np.mean(X[(f >= lo) & (f < hi)] ** 2))


def test_minimal_realism_eq_and_width():
    x = _noise()
    inst = {"eq": [], "width": 1.0}
    assert np.array_equal(realism.apply(x, SR, None, inst, None), x)
    y = realism.apply(x, SR, None, {"eq": [{"type": "highshelf", "f": 3000, "gain_db": 6, "q": 0.707}]}, None)
    assert y.shape == x.shape and y.dtype == np.float32
    assert _band_db(y[:, 0], 10000, 20000) - _band_db(x[:, 0], 10000, 20000) == pytest.approx(6.0, abs=0.3)
    assert _band_db(y[:, 0], 50, 300) - _band_db(x[:, 0], 50, 300) == pytest.approx(0.0, abs=0.3)
    y = realism.apply(x, SR, None, {"eq": [{"type": "highpass", "f": 200, "q": 0.707}]}, None)
    assert _band_db(y[:, 0], 20, 50) - _band_db(x[:, 0], 20, 50) < -20
    y = realism.apply(x, SR, None, {"eq": [{"type": "peak", "f": 1000, "gain_db": -6, "q": 2}]}, None)
    assert _band_db(y[:, 0], 950, 1050) - _band_db(x[:, 0], 950, 1050) == pytest.approx(-6.0, abs=0.5)
    mono = realism.apply(x, SR, None, {"width": 0.0}, None)
    assert np.allclose(mono[:, 0], mono[:, 1])
    assert np.allclose(mono[:, 0], 0.5 * (x[:, 0] + x[:, 1]), atol=1e-6)
    with pytest.raises(ValueError):
        realism.apply(x, SR, None, {"eq": [{"type": "bogus", "f": 100}]}, None)
