"""piano_waterfall's host diet (4K60 plan change 4): one arena upload + one captured graph per frame.

The graph path must render exactly what the direct launches render, including after a capture failure (direct from
then on), when a pinned slot is still in flight (the pageable spare), on dt = 0 frames and across output buffers.

  SM_GPU_TESTS=1 [SM_TEST_GPU=1] pytest stream_service/tests/test_piano_waterfall_graph.py
"""
from __future__ import annotations

import pytest

from .conftest import GPU

pytestmark = pytest.mark.gpu

W, H = 320, 180


@pytest.fixture(scope="module")
def env():
    from stream_service.gpu import RenderContext
    from stream_service.tools.fxtest import FxSound

    ctx = RenderContext(GPU, W, H, 60, prealloc_buffers=0)
    with ctx.device, ctx.stream:
        snd = FxSound(ctx.cp, "medley")
        yield ctx, snd


def _make(ctx, snd, **preset):
    from stream_service.plugins.sources.piano_waterfall import PianoWaterfall

    s = PianoWaterfall()
    s.sound = snd
    s.setup(ctx, PianoWaterfall.validate(preset))
    return s


def _run(ctx, snd, pairs, frames=90, t0=0.0, outs=None):
    """Render every (instance) of ``pairs`` on the same sound; returns the worst |a - b| over all frames."""
    cp = ctx.cp
    outs = outs or [ctx.new_frame() for _ in range(2)]
    a_out, b_out = ctx.new_frame(), ctx.new_frame()
    worst, t = 0, t0
    for i in range(frames):
        dt = 0.0 if i % 17 == 3 else 1 / 60
        t += dt
        snd.tick(t)
        ref, new = pairs
        o = outs[i % len(outs)]
        ref.render(t, dt, a_out)
        new.render(t, dt, o)
        cp.copyto(b_out, o)
        worst = max(worst, int(cp.abs(a_out.astype(cp.int16) - b_out.astype(cp.int16)).max()))
    ctx.stream.synchronize()
    return worst


def test_graph_matches_direct(env):
    ctx, snd = env
    ref, new = _make(ctx, snd), _make(ctx, snd)
    ref._mode = "direct"
    assert _run(ctx, snd, (ref, new)) <= 1
    assert new._mode == "graph" and len(new._graphs) >= 1
    assert new.calls == 4                       # event query, the arena copy, its fence, the graph launch


def test_full_scale_and_pitch_colours(env):
    ctx, snd = env
    ref = _make(ctx, snd, render_scale=1.0, colours="pitch", keyboard=0.0)
    new = _make(ctx, snd, render_scale=1.0, colours="pitch", keyboard=0.0)
    ref._mode = "direct"
    assert _run(ctx, snd, (ref, new), frames=40, t0=5.0) <= 1


def test_graph_cache_is_bounded(env):
    from stream_service.plugins.sources import piano_waterfall as pw

    ctx, snd = env
    ref, new = _make(ctx, snd), _make(ctx, snd)
    ref._mode = "direct"
    outs = [ctx.new_frame() for _ in range(pw._GRAPHS + 3)]
    assert _run(ctx, snd, (ref, new), frames=2 * len(outs), t0=9.0, outs=outs) <= 1
    assert len(new._graphs) == pw._GRAPHS


def test_capture_failure_falls_back(env, caplog):
    ctx, snd = env

    class BrokenStream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def begin_capture(self):
            raise RuntimeError("capture unsupported here")

        def end_capture(self):
            raise RuntimeError("not capturing")

    ref, new = _make(ctx, snd), _make(ctx, snd)
    ref._mode = "direct"
    new._cap = BrokenStream()
    assert _run(ctx, snd, (ref, new), frames=30, t0=12.0) <= 1
    assert new._mode == "direct" and not new._graphs
    assert new.calls == 5                       # query, copy, fence, the two direct launches


def test_slot_in_flight_uses_spare(env):
    ctx, snd = env

    class Busy:
        done = False

        def record(self, stream=None):
            raise AssertionError("a busy slot is never re-fenced")

    ref, new = _make(ctx, snd), _make(ctx, snd)
    ref._mode = "direct"
    new._ev = [Busy() for _ in new._ev]
    assert _run(ctx, snd, (ref, new), frames=20, t0=15.0) <= 1
    assert new._slot == 0 and new.calls == 3    # query, copy from the pageable spare, the graph launch


def test_no_sound_is_quiet_and_safe(env):
    ctx, _snd = env
    from stream_service.plugins.sources.piano_waterfall import PianoWaterfall

    ref, new = PianoWaterfall(), PianoWaterfall()
    for s in (ref, new):
        s.setup(ctx, PianoWaterfall.validate({}))
    ref._mode = "direct"
    cp = ctx.cp
    a, b = ctx.new_frame(), ctx.new_frame()
    for i in range(10):
        ref.render(i / 60, 0.0 if i == 0 else 1 / 60, a)
        new.render(i / 60, 0.0 if i == 0 else 1 / 60, b)
    assert int(cp.abs(a.astype(cp.int16) - b.astype(cp.int16)).max()) <= 1
    new.close()
