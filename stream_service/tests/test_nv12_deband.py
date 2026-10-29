"""RGBA -> NV12 debanding and dither (gpu.py rgba_to_nv12; owner, 29 Oct: banding near black).  GPU: SM_GPU_TESTS=1."""
import numpy as np
import pytest

pytestmark = pytest.mark.gpu

W, H = 1920, 1080


def _nv12(rgb: np.ndarray, deband: float = 1.0) -> np.ndarray:
    import cupy as cp
    from stream_service.gpu import CoreKernels
    k = CoreKernels()
    frame = np.full((H, W, 4), 255, np.uint8)
    frame[..., :3] = rgb
    src = cp.asarray(frame)
    out = cp.zeros(W * H * 3 // 2, cp.uint8)
    k.rgba_to_nv12(src, out, W, H, 1.0, deband)
    return cp.asnumpy(out[: W * H]).reshape(H, W).astype(np.float64)


def _ideal_y(v: np.ndarray) -> np.ndarray:
    return 16.0 + 219.0 / 255.0 * v


def test_flat_keeps_its_level():
    """A flat colour: only the two codes either side of its exact level, averaging to it."""
    for v in (0, 7, 18, 128, 200, 255):
        y, ideal = _nv12(np.full((H, W, 3), v, np.uint8)), _ideal_y(np.float64(v))
        assert y.min() >= np.floor(ideal) and y.max() <= np.ceil(ideal) and abs(y.mean() - ideal) < 0.05


def test_dark_ramp_tracks_the_true_gradient():
    """A near-black ramp, 8-bit quantised: the debanded, dithered Y's local average follows the unquantised ramp."""
    true = np.linspace(5.0, 15.0, W)[None, :].repeat(H, 0)
    rgb = np.floor(true + 0.5).astype(np.uint8)[..., None].repeat(3, 2)
    box = lambda a: np.convolve(a.mean(0), np.ones(16) / 16, mode="valid")
    ideal = box(_ideal_y(true))[200:-200]
    err_on = np.abs(box(_nv12(rgb, 1.0)) [200:-200] - ideal)
    err_off = np.abs(box(_nv12(rgb, 0.0))[200:-200] - ideal)
    assert err_on.mean() < 0.08 and err_on.mean() < 0.4 * err_off.mean()


def test_edges_are_kept():
    rgb = np.zeros((H, W, 3), np.uint8)
    rgb[:, W // 2:] = 200
    y, hi = _nv12(rgb), _ideal_y(200.0)
    assert np.all(y[:, : W // 2] == 16) and np.all((y[:, W // 2:] >= np.floor(hi)) & (y[:, W // 2:] <= np.ceil(hi)))
