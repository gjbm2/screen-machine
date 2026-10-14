"""Layer tree: rendering, transition events and pts, nesting, collapse (NumPy fakes, no GPU)."""
from __future__ import annotations

import numpy as np
import pytest

from stream_service.layers import FrameCtx, SourceLayer, TransitionLayer

FPS = 10


class Pool:
    def __init__(self):
        self.out = 0

    def acquire(self):
        self.out += 1
        return np.zeros((1, 1, 4), np.float64)

    def release(self, buf):
        self.out -= 1


class Const:
    """A fake source that fills its frame with a constant and records its (t_local, dt) calls."""

    def __init__(self, value):
        self.value = value
        self.calls = []
        self.closed = False
        self.name = f"const{value}"

    def render(self, t_local, dt, out):
        self.calls.append((round(t_local, 6), round(dt, 6)))
        out[...] = self.value

    def close(self):
        self.closed = True


class Mix:
    name = "mix"

    def __init__(self, duration_s, easing="linear"):
        self.duration_s = duration_s
        self.easing = easing
        self.closed = False

    def render(self, p, a, b, out):
        out[...] = a + (b - a) * p

    def close(self):
        self.closed = True


class Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, kind, phase, pts, tid, superseded):
        self.events.append((kind, phase, round(pts, 6), tid, superseded))


def run(root, n0, n1, pool=None):
    pool = pool or Pool()
    fc = FrameCtx(n0, FPS, pool)
    out = np.zeros((1, 1, 4))
    values = {}
    for n in range(n0, n1):
        fc.n = n
        root.render(fc, out)
        values[n] = float(out[0, 0, 0])
        root = root.collapse(fc)
    assert pool.out == 0
    return root, values


def test_source_layer_local_time_and_dt():
    s = Const(1.0)
    root, _ = run(SourceLayer(s, t0=0.5), 5, 9)
    assert s.calls == [(0.0, 0.0), (0.1, 0.1), (0.2, 0.1), (0.3, 0.1)]


def test_transition_progress_events_and_collapse():
    rec = Recorder()
    a, b = Const(0.0), Const(10.0)
    root = TransitionLayer(SourceLayer(a, 0.0), SourceLayer(b, 1.0), Mix(1.0), 10, kind="in", tid=1, notify=rec)
    root, vals = run(root, 10, 25)
    assert vals[10] == 0.0                       # progress 0: pure a (b not rendered)
    assert vals[15] == pytest.approx(5.0)        # half way, linear
    assert vals[20] == 10.0 and vals[24] == 10.0
    assert rec.events == [("in", "started", 1.0, 1, False), ("in", "done", 2.0, 1, False)]
    assert isinstance(root, SourceLayer) and root.source is b   # collapsed to b
    assert a.closed and not b.closed
    assert b.calls[0] == (0.1, 0.0)              # b first rendered one frame into the transition


def test_zero_duration_is_done_on_its_first_frame():
    rec = Recorder()
    root = TransitionLayer(SourceLayer(Const(0.0), 0), SourceLayer(Const(7.0), 3.0), Mix(0.0), 30, kind="out",
                           tid=2, notify=rec)
    root, vals = run(root, 30, 32)
    assert vals[30] == 7.0
    assert rec.events == [("out", "started", 3.0, 2, False), ("out", "done", 3.0, 2, False)]


def test_nested_transition_uses_running_one_as_a():
    rec = Recorder()
    anchor, scene, target = Const(0.0), Const(10.0), Const(20.0)
    t_in = TransitionLayer(SourceLayer(anchor, 0), SourceLayer(scene, 0), Mix(2.0), 0, kind="in", tid=1,
                           notify=rec)
    root, vals = run(t_in, 0, 10)                # in is half done at n=10
    assert vals[9] == pytest.approx(4.5)
    t_out = TransitionLayer(root, SourceLayer(target, 1.0), Mix(1.0), 10, kind="out", tid=2, notify=rec)
    root, vals = run(t_out, 10, 25)
    # n=15: a = in at progress 0.75 (7.5), out at 0.5 -> 13.75
    assert vals[15] == pytest.approx(13.75)
    assert vals[20] == 20.0
    # the in transition was discarded before it finished: done with superseded=True at the out's done frame
    assert ("in", "done", 2.0, 1, True) in rec.events
    assert ("out", "done", 2.0, 2, False) in rec.events
    assert isinstance(root, SourceLayer) and root.source is target
    assert anchor.closed and scene.closed and not target.closed


def test_inner_transition_completing_first_collapses_inside():
    rec = Recorder()
    a, b, c = Const(0.0), Const(10.0), Const(20.0)
    inner = TransitionLayer(SourceLayer(a, 0), SourceLayer(b, 0), Mix(0.5), 0, kind="in", tid=1, notify=rec)
    outer = TransitionLayer(inner, SourceLayer(c, 0), Mix(2.0), 0, kind="scene", tid=2, notify=rec)
    root, vals = run(outer, 0, 8)
    assert rec.events[:3] == [("scene", "started", 0.0, 2, False), ("in", "started", 0.0, 1, False),
                              ("in", "done", 0.5, 1, False)]
    assert isinstance(root, TransitionLayer) and isinstance(root.a, SourceLayer) and root.a.source is b
    assert a.closed


def test_close_reports_unfinished_transitions():
    rec = Recorder()
    t = TransitionLayer(SourceLayer(Const(0), 0), SourceLayer(Const(1), 0), Mix(5.0), 0, kind="scene", tid=7,
                        notify=rec)
    fc = FrameCtx(3, FPS, Pool())
    t.close(fc)
    assert rec.events == [("scene", "started", 0.3, 7, False), ("scene", "done", 0.3, 7, True)]


def test_progress_hits_exactly_one_on_the_frame_grid():
    # 0.1 s at 30 fps is 3.0000000000000004 frames: done must still land on frame +3
    class P:
        duration_s = 0.1
    rec = Recorder()
    t = TransitionLayer(SourceLayer(Const(0), 0), SourceLayer(Const(1), 0), P(), 0, kind="in", tid=1, notify=rec)
    fc = FrameCtx(3, 30, Pool())
    assert t.progress(fc) == 1.0
