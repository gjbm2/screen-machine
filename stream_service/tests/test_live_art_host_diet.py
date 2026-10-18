"""live_art's host diet (plan change 4): the fused kernels match the old multi-launch sequence bit for bit, and a frame
costs one CUDA call (three when new art arrives).  GPU, opt-in:

  SM_GPU_TESTS=1 [SM_TEST_GPU=1] pytest stream_service/tests/test_live_art_host_diet.py
"""
from __future__ import annotations

import importlib
from collections import deque

import numpy as np
import pytest

from stream_service.plugins.sources import live_art as la

from .conftest import GPU


def _ctx(w: int, h: int):
    from stream_service.gpu import RenderContext
    return RenderContext(GPU, w, h, 60, prealloc_buffers=0)


def _rand(cp, rng, shape):
    return cp.asarray(rng.integers(0, 256, shape, dtype=np.uint8))


@pytest.mark.gpu
@pytest.mark.parametrize("freeze", [0, 1])
def test_gpu_arrive_equals_freeze_expand_mix(freeze):
    from stream_service.gpu import raw_module
    from stream_service.plugins._lib.cuda import launch_2d
    w, h = 67, 41
    ctx = _ctx(w, h)
    cp = ctx.cp
    mod = raw_module(la._SRC)
    k_mix, k_arrive = mod.get_function("live_art_mix"), mod.get_function("live_art_arrive")
    rng = np.random.default_rng(3)
    iw, ih = np.int32(w), np.int32(h)
    with ctx.device, ctx.stream:
        prev, cur = _rand(cp, rng, (h, w, 4)), _rand(cp, rng, (h, w, 4))
        rgb = _rand(cp, rng, (h, w, 3))
        for kf, k, gain in ((0.37, 0.0, 1.0), (0.81, 0.13, 0.9), (1.0, 1.0, 1.0), (0.5, 0.6, 1.15)):
            # the old path: freeze in place (mid-fade), expand into cur, then the mix
            p0, c0, o0 = prev.copy(), cur.copy(), ctx.new_frame()
            if freeze:
                launch_2d(k_mix, w, h, (p0, c0, p0, iw, ih, np.float32(kf), np.float32(1.0)))
            ctx.kernels.rgb_to_rgba(rgb, c0)
            launch_2d(k_mix, w, h, (p0, c0, o0, iw, ih, np.float32(k), np.float32(gain)))
            # the new path: one launch
            p1, c1, o1 = prev.copy(), cur.copy(), ctx.new_frame()
            launch_2d(k_arrive, w, h, (p1, c1, rgb, o1, iw, ih, np.int32(freeze), np.float32(kf),
                                       np.float32(k), np.float32(gain)))
            assert cp.array_equal(p0, p1) and cp.array_equal(c0, c1) and cp.array_equal(o0, o1), (kf, k, gain)


@pytest.mark.gpu
@pytest.mark.parametrize("size", [(64, 36), (33, 35), (1, 1)])   # tails of 0, 12 and 4 bytes
def test_gpu_copy_kernel_and_odd_out_fallback(size):
    w, h = size
    ctx = _ctx(w, h)
    cp = ctx.cp
    src = la.LiveArtSource()
    with ctx.device, ctx.stream:
        src.setup(ctx, la.LiveArtSource.validate({}))
        src.cur[...] = _rand(cp, np.random.default_rng(5), (h, w, 4))
        out = ctx.new_frame()
        out.fill(7)
        src.render(0.0, 1 / 60, out)                   # settled, gain 1: the copy kernel
        assert cp.array_equal(out, src.cur)
        big = cp.zeros((h, w + 1, 4), cp.uint8)        # a non-contiguous out: cp.copyto (degrade, never refuse)
        view = big[:, 1:]
        src.render(0.0, 1 / 60, view)
        assert cp.array_equal(view, src.cur) and int(big[:, 0].max()) == 0
    src.close()


class _Count:
    def __init__(self, f, n, key):
        self.f, self.n, self.key = f, n, key

    def __call__(self, *a, **kw):
        self.n[self.key] = self.n.get(self.key, 0) + 1
        return self.f(*a, **kw)


@pytest.mark.gpu
def test_gpu_one_call_per_frame_three_on_arrival(monkeypatch):
    w, h = 64, 36
    ctx = _ctx(w, h)
    cp = ctx.cp
    n: dict = {}
    src = la.LiveArtSource()
    with ctx.device, ctx.stream:
        src.setup(ctx, la.LiveArtSource.validate({"crossfade_s": 0.5}))
        nb = w * h * 3
        bufs = [la._HostBuf(np.frombuffer(m, np.uint8, nb).reshape(h, w, 3),
                            cp.cuda.Event(block=False, disable_timing=True), m)
                for m in (la._pinned_new(cp, nb) for _ in range(la.N_BUFFERS))]
        src.feed = feed = la._ArtFeed("http://127.0.0.1:9/art", "http://127.0.0.1:9/art.png", w, h, bufs)
        for key in ("k", "k_arrive", "k_copy"):
            setattr(src, key, _Count(getattr(src, key), n, key))
        real = cp.cuda.runtime.memcpyAsync
        monkeypatch.setattr(cp.cuda.runtime, "memcpyAsync", _Count(real, n, "memcpy"))
        monkeypatch.setattr(cp, "copyto", _Count(cp.copyto, n, "copyto"))
        out = ctx.new_frame()

        def frame(dt=1 / 60):
            n.clear()
            src.render(0.0, dt, out)
            ctx.stream.synchronize()
            return dict(n)

        assert frame() == {"k_copy": 1}                         # settled
        white = np.full((h, w, 3), 255, np.uint8)
        assert feed._stage(white, "still")
        assert frame() == {"memcpy": 1, "k_arrive": 1}          # + its event record: 3 calls
        assert frame() == {"k": 1}                              # fading
        assert feed._stage(white * 0, "video", 0.4)             # mid-fade: frozen inside the same launch
        assert frame(0.0) == {"memcpy": 1, "k_arrive": 1}       # dt = 0 safe
        for _ in range(30):
            frame()
        assert frame() == {"k_copy": 1} and float(out[..., :3].mean()) == 0.0
        src.feed = None
    la._pinned_give_back(bufs)
    src.close()


def test_pinned_free_list_survives_a_hot_reload():
    """The plug-in rescan importlib.reloads a changed file: the reserved pinned blocks must not be orphaned."""
    free, pending = la._PINNED_FREE, la._PINNED_PENDING
    marker = object()
    free.setdefault(-1, deque()).append(marker)
    try:
        mod = importlib.reload(la)
        assert mod._PINNED_FREE is free and mod._PINNED_PENDING is pending and marker in mod._PINNED_FREE[-1]
    finally:
        free.pop(-1, None)
