"""The overlay note effects under the room ensemble's register window (docs/ENSEMBLE.md §8.6; sound.register): does
it crash, and are notes outside the window drawn dimmer, never hidden.  Small frames, a few dozen in all.

  SM_GPU_TESTS=1 [SM_TEST_GPU=1] pytest stream_service/tests/test_note_register.py
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.gpu

GPU = int(os.environ.get("SM_TEST_GPU", "1"))
W, H = 480, 270
EFFECTS = ("note_ripples", "note_stream", "note_highway_3d")


@pytest.fixture(scope="module")
def ctx():
    pytest.importorskip("cupy")
    from stream_service.gpu import RenderContext
    c = RenderContext(GPU, W, H, 60, prealloc_buffers=0)
    with c.device, c.stream:
        yield c


def _effect(ctx, name, snd):
    import importlib
    mod = importlib.import_module(f"stream_service.plugins.effects.{name}")
    cls = next(c for c in vars(mod).values() if isinstance(c, type) and getattr(c, "name", None) == name)
    e = cls()
    e.sound = snd
    e.setup(ctx, cls.validate({"amount": 1.0}))
    return e


def _run(ctx, name, register, frames=40):
    from stream_service.tools.fxtest import FxSound
    snd = FxSound(ctx.cp, "sweep")
    snd.register = register
    e = _effect(ctx, name, snd)
    cp = ctx.cp
    src = cp.zeros((H, W, 4), cp.uint8)
    src[..., 3] = 255
    outs = [ctx.new_frame(), ctx.new_frame()]
    total, t = 0.0, 0.0
    for i in range(frames):
        dt = 1.0 / 15
        t += dt
        snd.tick(t)
        e.render(t, dt, src, outs[i & 1])
        total += float(outs[i & 1][..., :3].astype(cp.float32).mean())
    ctx.stream.synchronize()
    e.close()
    return total


@pytest.mark.parametrize("name", EFFECTS)
def test_outside_the_window_is_dimmer_never_hidden(ctx, name):
    full = _run(ctx, name, None)
    nothing = _run(ctx, name, (0.0, 0.0, 0.25))       # every note outside the window
    assert full > 0.0
    assert 0.0 < nothing < full
