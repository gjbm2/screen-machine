"""The GLSL runtime's fast path (plugins/_lib/glsl/arena.py; 4K60 plan change 4): one graph per frame must give
the same pixels as the direct path, keep every fallback working, and never let a pinned slot be rewritten while
the GPU may still read it.

CPU tests always run.  GPU tests: SM_GPU_TESTS=1 (GPU 1 by default, or the only visible one), e.g.
  flock <gpu1.lock> env CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=1 SM_GPU_TESTS=1 nice -n 15 \\
      ~/.venvs/sm-stream/bin/python -m pytest stream_service/tests/test_glsl_runtime_fast.py
"""
from __future__ import annotations

import importlib
import os
import types

import numpy as np
import pytest

from stream_service.plugins._lib.glsl import runtime as R


# ------------------------------------------------------------------------------------------- CPU

def _sound(rng, nb):
    return types.SimpleNamespace(present=True, frame=types.SimpleNamespace(spectrum=list(rng.uniform(-0.1, 1.1, nb))))


def test_fast_rows_are_the_same_floats_as_show_textures():
    base = R.ShowTextures.__new__(R.ShowTextures)
    base.n = 512
    fast = R.FastRows(512)
    rng = np.random.default_rng(3)
    for i in range(600):
        t, e, b = float(rng.uniform(0, 5000)), float(rng.uniform(0, 1)), float(rng.uniform(0, 1))
        if i % 3 == 0:                                   # numpy scalars from the easer promote differently
            e, b = np.float64(e), np.float32(b)
        snd = None if i % 2 == 0 else _sound(rng, 32 if i % 4 == 1 else 24)
        want, got = base.rows(t, e, b, snd), fast.rows(t, e, b, snd)
        for x, y in zip(want[:2], got[:2]):
            assert x.dtype == y.dtype and np.array_equal(x, y)


def test_rows_into_matches_to_rgba():
    rng = np.random.default_rng(4)
    rows = [rng.uniform(0, 1, 512).astype(np.float32), rng.uniform(0, 1, 512)]
    view = np.zeros((2, 512, 4), np.uint8)
    view[..., 3] = 255
    R._rows_into(view, rows)
    assert np.array_equal(view, R._to_rgba(rows))


def _fake_program(uniforms):
    dt = R.uniform_dtype(uniforms)
    p = R.Program.__new__(R.Program)
    p.dtype, p.u = dt, np.zeros((), dt)
    p.names = set(dt.names)
    p.types = {u.name: u.type for u in uniforms}
    return p


def test_param_map_writes_what_apply_params_writes():
    from stream_service.plugins._lib.glsl import translate as T
    uni = [T.Uniform("speed", "float"), T.Uniform("count", "int"), T.Uniform("on", "bool"),
           T.Uniform("tint", "vec3"), T.Uniform("tint4", "vec4"), T.Uniform("pos", "vec2")]
    props = {"speed": {"x-glsl": {"u": "speed"}}, "count": {"x-glsl": {"u": "count"}},
             "on": {"x-glsl": {"u": "on"}}, "tint": {"x-glsl": {"u": "tint", "color": 3}},
             "tint4": {"x-glsl": {"u": "tint4", "color": 4, "alpha": 0.5}},
             "px": {"x-glsl": {"u": "pos", "c": 0}}, "py": {"x-glsl": {"u": "pos", "c": 1}},
             "missing": {"x-glsl": {"u": "nope"}}, "plain": {}}
    a, b = _fake_program(uni), _fake_program(uni)
    pm = R.ParamMap(b, props)
    for vals in ({"speed": 1.5, "count": 2.6, "on": True, "tint": "#ff8000", "tint4": "#102030", "px": 0.25,
                  "py": -1.0, "missing": 3, "plain": 4},
                 {"speed": np.float64(0.1), "count": 7, "on": 0.0, "tint": "#000000"}):
        R.apply_params(a, props, vals)
        pm.apply(b, vals)
        assert a.u.tobytes() == b.u.tobytes()


def test_isf_passes_launch_count():
    p = types.SimpleNamespace(tr=types.SimpleNamespace(meta={"passes": [{"TARGET": "a"}, {}, {"TARGET": "b"}]}))
    assert R.IsfPasses(p, None, 8, 8).launches() == 4          # the last pass with a TARGET also draws the output
    p.tr.meta = {"passes": [{}]}
    assert R.IsfPasses(p, None, 8, 8).launches() == 1


# ------------------------------------------------------------------------------------------- GPU

gpu = pytest.mark.gpu
P = "stream_service.plugins."
SCENE = P + "sources.st_modified_star_nest_2"            # Shadertoy, music channel, palette post
ISF_SCENE = P + "sources.isf_color_history"              # ISF multi-pass generator
EFFECT = P + "effects.isf_cv_optical_feedback_tunnel"    # ISF 3 passes + persistent targets, scale 0.45 (upmix)
EFFECT_FULL = P + "effects.isf_waveform_displace"        # full size: mix (amount < 1) and direct (amount 1)
TRANSITION = P + "transitions.glt_angular"


def _cls(mod):
    m = importlib.import_module(mod)
    return [v for v in vars(m).values() if isinstance(v, type) and v.__module__ == m.__name__
            and getattr(v, "GLSL", "")][0]


@pytest.fixture(scope="module")
def ctx():
    from stream_service.gpu import RenderContext
    dev = 0 if os.environ.get("CUDA_VISIBLE_DEVICES") else int(os.environ.get("SM_TEST_GPU", "1"))
    c = RenderContext(dev, 320, 180, 30, prealloc_buffers=0)
    with c.device, c.stream:
        yield c


def _pair(ctx, cls, preset=None, sound=None):
    kind = "transition" if hasattr(cls, "pure_endpoints") else ("effect" if hasattr(cls, "stage") else "source")
    out = []
    for fast in (False, True):
        if kind == "transition":
            x = cls(cls.validate(preset or {}))
            x.setup(ctx)
        else:
            x = cls()
            if sound is not None:
                x.sound = sound
            x.setup(ctx, cls.validate(preset or {}))
        x._fast_off = not fast
        out.append(x)
    return kind, out


def _ab(ctx, cls, n=16, preset=None, sound=None, each=None):
    cp = ctx.cp
    rng = np.random.default_rng(0)
    h, w = ctx.height, ctx.width
    A = cp.asarray(rng.integers(0, 255, (h, w, 4), dtype=np.uint8))
    B = cp.asarray(rng.integers(0, 255, (h, w, 4), dtype=np.uint8))
    kind, (d, f) = _pair(ctx, cls, preset, sound)
    outs = [ctx.new_frame(), ctx.new_frame()]
    worst = 0
    for i in range(n):
        if each is not None:
            each(i, f)
        if sound is not None:
            sound.tick(1.0 + i / 30)
        for x, o in zip((d, f), outs):
            o.fill(9)
            dt = 0.0 if i == 0 else 1 / 30
            if kind == "source":
                x.render(i / 30, dt, o)
            elif kind == "effect":
                x.render(i / 30, dt, A, o)
            else:
                x.render(min(1.0, i / (n - 2)), A, B, o)
        worst = max(worst, int(cp.abs(outs[0].astype(cp.int16) - outs[1].astype(cp.int16)).max()))
    assert f._fast is not None, "the fast path did not engage"
    return worst, f, outs[1]


@gpu
@pytest.mark.parametrize("mod,preset", [(SCENE, None), (ISF_SCENE, None), (EFFECT, None),
                                        (EFFECT_FULL, {"amount": 0.5}), (EFFECT_FULL, {"amount": 1.0}),
                                        (TRANSITION, None)])
def test_fast_path_matches_direct(ctx, mod, preset):
    from stream_service.tools.fxtest import PIANO, FxSound
    cls = _cls(mod)
    snd = FxSound(ctx.cp, PIANO) if getattr(cls, "listens", None) else None
    worst, f, last = _ab(ctx, cls, preset=preset, sound=snd)
    assert worst == 0
    plan = f._fast.plan
    if plan is None:                                      # a transition: one launch, no graph, no events
        assert "poll" not in f._watch.__dict__ and f._watch.pending is None
    else:
        assert plan.n_graph_runs > 0 and plan.n_direct_runs == 0
    assert float(last[..., :3].mean()) > 1.0              # not a trivially black match


@gpu
def test_notes_texture_from_the_gpu_matches(ctx):
    """The notes kernel (glsl_notes_min) writes what cupy.minimum(notes_gpu, cap) writes, and host notes and
    'no notes' land as the direct path leaves them."""
    cp = ctx.cp
    cls = _cls(SCENE)
    cls = type("NotesScene", (cls,), {"channels": {0: "notes", 1: "music"}, "name": "notes_scene"})
    gpu_notes = cp.asarray(np.random.default_rng(1).uniform(0, 3e4, (128, 4)).astype(np.float32))
    gpu_notes[5, 2] = np.inf
    frame = types.SimpleNamespace(spectrum=None, notes=np.random.default_rng(2).uniform(0, 2e4, (128, 4)))
    snd = types.SimpleNamespace(present=False, notes_gpu=gpu_notes, frame=frame)
    _, (d, f) = _pair(ctx, cls, sound=snd)
    o = ctx.new_frame()
    for mode in ("gpu", "host", "none", "gpu"):
        snd.notes_gpu = gpu_notes if mode == "gpu" else None
        snd.frame = frame if mode == "host" else None
        for x in (d, f):
            x.render(0.0, 1 / 30, o)
        ctx.stream.synchronize()
        off = f._fast.tex.off["notes"]
        got = f._fast.plan.dev[off: off + 128 * 4 * 4].view(cp.float32).reshape(1, 128, 4)
        assert cp.array_equal(got, d.tex.notes), mode


@gpu
def test_capture_failure_degrades_to_direct_launches(ctx, monkeypatch):
    from stream_service.plugins._lib.glsl import arena
    monkeypatch.setattr(arena._Driver, "last_node", lambda self, ptr: (_ for _ in ()).throw(RuntimeError("no")))
    worst, f, _ = _ab(ctx, _cls(EFFECT), n=8)
    assert worst == 0
    plan = f._fast.plan
    assert not plan.graphs_ok and plan.n_graph_runs == 0 and plan.n_direct_runs == 8


@gpu
def test_any_fast_path_error_falls_back_to_the_direct_path(ctx):
    """An error inside the fast frame: logged once, the frame is drawn by the direct path, which stays."""
    def boom(i, f):
        if i == 3 and f._fast is not None:
            f._fast.plan.run = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    cls = _cls(SCENE)
    cp = ctx.cp
    kind, (d, f) = _pair(ctx, cls)
    outs = [ctx.new_frame(), ctx.new_frame()]
    for i in range(8):
        boom(i, f)
        for x, o in zip((d, f), outs):
            x.render(i / 30, 0.0 if i == 0 else 1 / 30, o)
        assert cp.array_equal(outs[0], outs[1])
    assert f._fast is None and f._fast_off and "poll" not in f._watch.__dict__


@gpu
def test_structural_changes_capture_once_each_and_reuse(ctx):
    """Render-scale changes (the _Watch shrink) re-capture; returning to a seen shape reuses its graph; pixels
    still match the direct path throughout."""
    cp = ctx.cp
    _, (d, f) = _pair(ctx, _cls(SCENE))
    outs = [ctx.new_frame(), ctx.new_frame()]
    for i, s in enumerate([0.5, 0.5, 0.3, 0.3, 0.5, 0.5, 0.3, 0.3]):
        for x, o in zip((d, f), outs):
            x.scale_cap = s
            x.render(i / 30, 1 / 30, o)
        assert cp.array_equal(outs[0], outs[1])
    assert f.scale == pytest.approx(0.3) and f._fast.plan.n_captures == 2


@gpu
@pytest.mark.parametrize("zero_copy", [True, False])
def test_pinned_slots_are_not_rewritten_while_queued(ctx, zero_copy):
    """Far more frames than slots queued behind a slow GPU: every frame's arena arrives intact (the fence makes
    the host wait instead of overwriting a slot the GPU has not read).  Also without a zero-copy address (the
    upload is then a cudaMemcpy outside the graph)."""
    from stream_service.plugins._lib.glsl.arena import FramePlan
    cp = ctx.cp
    src = r"""
    extern "C" __global__ void spin_then_copy(const float* a, float* out, const int k, const long long cycles) {
        long long t0 = clock64(); while (clock64() - t0 < cycles) {}
        out[k] = a[0];
    }"""
    fn = cp.RawModule(code=src).get_function("spin_then_copy")
    plan = FramePlan(cp, 16, slots=3, name="t")
    if not zero_copy:
        plan._host_dev = None
    out = cp.zeros(64, cp.float32)
    v = plan.view(plan.static(16), np.float32, (4,))
    for k in range(40):
        plan.begin()
        v[0] = float(k)
        plan.node(fn, (1, 1, 1), (1, 1, 1), (np.uint64(plan.dev_ptr), np.uint64(out.data.ptr), np.int32(k),
                                              np.int64(2_000_000)))
        plan.run()
    ctx.stream.synchronize()
    assert np.array_equal(out.get()[:40], np.arange(40, dtype=np.float32))
    assert plan.n_stalls > 0 and plan.n_graph_runs == 40 and plan.n_captures == 1


@gpu
def test_stream_switch_orders_after_the_previous_frame(ctx):
    from stream_service.plugins._lib.glsl.arena import FramePlan
    cp = ctx.cp
    src = r"""
    extern "C" __global__ void slow_add(float* out, const float v, const long long cycles) {
        long long t0 = clock64(); while (clock64() - t0 < cycles) {}
        out[0] = out[0] * 2.f + v;
    }"""
    fn = cp.RawModule(code=src).get_function("slow_add")
    plan = FramePlan(cp, 0, name="t")
    out = cp.zeros(1, cp.float32)
    streams = [cp.cuda.Stream(non_blocking=True) for _ in range(2)]
    want = 0.0
    for k in range(10):
        plan.begin()
        plan.node(fn, (1, 1, 1), (1, 1, 1), (np.uint64(out.data.ptr), np.float32(k), np.int64(3_000_000)))
        plan.run(streams[k % 2])
        want = want * 2 + k
    cp.cuda.Device().synchronize()
    assert float(out.get()[0]) == want


@gpu
def test_a_shape_that_changes_every_frame_stops_capturing(ctx):
    """An ISF size expression animated every frame would capture a graph per frame: after THRASH captures the
    plan launches directly instead (same results)."""
    from stream_service.plugins._lib.glsl.arena import THRASH, FramePlan
    cp = ctx.cp
    fn = cp.RawModule(code=r"""
    extern "C" __global__ void put(float* out, const float v) { out[blockIdx.x] = v; }""").get_function("put")
    plan = FramePlan(cp, 0, name="t")
    out = cp.zeros(64, cp.float32)
    for k in range(40):
        plan.begin()
        plan.node(fn, (1 + k % 40, 1, 1), (1, 1, 1), (np.uint64(out.data.ptr), np.float32(k)))
        plan.run()
    ctx.stream.synchronize()
    assert plan.n_captures == THRASH[0] and not plan.graphs_ok and plan.n_direct_runs == 40 - THRASH[0]
    assert float(out.get()[0]) == 39.0
