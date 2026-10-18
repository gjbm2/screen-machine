"""note_fluid's host diet (4K60 plan change 4): the uniform arena + CUDA graph path renders exactly what the direct
launches render, and every failure falls back to direct launches.

  SM_GPU_TESTS=1 [SM_TEST_GPU=1] pytest stream_service/tests/test_note_fluid_graph.py
"""
from __future__ import annotations

import os

import numpy as np
import pytest

from stream_service.plugins.sources import note_fluid as nf

GPU = int(os.environ.get("SM_TEST_GPU", "1"))


def test_arena_header_slots_fit():
    slots = [nf.U_N, nf.U_EDT, nf.U_SDT, nf.U_VDEC, nf.U_DDEC, nf.U_KFILL, nf.U_VENT, nf.U_SHADE, nf.U_GAIN]
    assert len(set(slots)) == len(slots)
    assert min(slots) >= 14 and max(slots) < nf.HDR          # 0..13: stir modes, vmax, buoyancy
    assert nf.JACOBI % 2 == 0 and nf.SLOTS % 2 == 0


@pytest.fixture(scope="module")
def ctx():
    pytest.importorskip("cupy")
    from stream_service.gpu import RenderContext
    c = RenderContext(GPU, 640, 360, 60, prealloc_buffers=0)
    with c.device, c.stream:
        yield c


def _make(ctx, preset=None, piano=True):
    from stream_service.tools import fxtest
    s = nf.NoteFluid()
    snd = fxtest.FxSound(ctx.cp, "medley") if piano else None
    s.sound = snd
    s.setup(ctx, nf.NoteFluid.validate(preset or {}))
    return s, snd


def _run(ctx, insts, frames=90, alternate=False):
    """Render the instances in lockstep on the same clock; returns the max abs difference vs the first."""
    cp = ctx.cp
    bufs = [[ctx.new_frame(), ctx.new_frame()] for _ in insts]
    worst, t = 0, 0.0
    for i in range(frames):
        dt = 0.0 if i in (20, 21, 50) else 1.0 / 60              # dt = 0 frames too
        t += dt
        j = (i & 1) if alternate else (i // 17) & 1
        for (s, snd), b in zip(insts, bufs):
            if snd is not None:
                snd.tick(t)
            s.render(t, dt, b[j])
        ref = bufs[0][j].astype(cp.int16)
        for b in bufs[1:]:
            worst = max(worst, int(cp.abs(b[j].astype(cp.int16) - ref).max()))
    return worst, float(bufs[0][j][..., :3].mean())


@pytest.mark.gpu
@pytest.mark.parametrize("preset,piano", [({}, True), ({}, False), ({"render_scale": 1.0, "shading": 0.0}, True)])
def test_graph_matches_direct(ctx, preset, piano):
    direct, graph = _make(ctx, preset, piano), _make(ctx, preset, piano)
    direct[0].graphs = False
    worst, mean = _run(ctx, [direct, graph])
    assert worst == 0
    assert mean > 0.5                                             # not a black frame
    g = graph[0]
    assert g.graphs and g.captures >= nf.SLOTS
    assert g.cuda_calls == 2                                      # graph launch + the slot's event record
    assert direct[0].cuda_calls == 1 + 9 + nf.JACOBI + 1          # copy + 29 launches + record
    for s, _ in (direct, graph):
        s.close()


@pytest.mark.gpu
def test_alternating_output_stays_direct(ctx):
    direct, graph = _make(ctx), _make(ctx)
    direct[0].graphs = False
    worst, _ = _run(ctx, [direct, graph], frames=40, alternate=True)
    assert worst == 0
    assert graph[0].captures == 0 and graph[0].graphs              # never seen twice running: no capture, still on
    for s, _ in (direct, graph):
        s.close()


@pytest.mark.gpu
def test_capture_failure_falls_back(ctx):
    direct, broken = _make(ctx), _make(ctx)
    direct[0].graphs = False

    def boom(slot, out):
        raise RuntimeError("capture unsupported")

    broken[0]._capture = boom
    worst, _ = _run(ctx, [direct, broken], frames=40)
    assert worst == 0
    assert broken[0].graphs is False and broken[0].cuda_calls == direct[0].cuda_calls
    for s, _ in (direct, broken):
        s.close()


@pytest.mark.gpu
def test_launch_failure_falls_back(ctx):
    direct, broken = _make(ctx), _make(ctx)
    direct[0].graphs = False

    class Bad:
        def launch(self, stream=None):
            raise RuntimeError("launch failed")

    broken[0]._capture = lambda slot, out: Bad()
    worst, _ = _run(ctx, [direct, broken], frames=40)
    assert worst == 0
    assert broken[0].graphs is False and not broken[0].graph_cache
    for s, _ in (direct, broken):
        s.close()


@pytest.mark.gpu
def test_no_pinned_arena_falls_back(ctx):
    direct, plain = _make(ctx), _make(ctx)
    direct[0].graphs = False
    plain[0].pin, plain[0].ev, plain[0].graphs = [], [], False   # as if pinned allocation had failed
    worst, _ = _run(ctx, [direct, plain], frames=40)
    assert worst == 0
    assert plain[0].cuda_calls == 1 + 9 + nf.JACOBI              # U.set + 29 launches
    for s, _ in (direct, plain):
        s.close()
