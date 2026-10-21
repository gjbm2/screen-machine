"""harmony_spiral: the keys' places, the host build and the tunnel's stamps (CPU), and a does-it-crash render with the
test piano (GPU, tiny frames).

  pytest stream_service/tests/test_harmony_spiral.py                      # the CPU tests
  SM_GPU_TESTS=1 [SM_TEST_GPU=1] pytest stream_service/tests/test_harmony_spiral.py
"""
from __future__ import annotations

import numpy as np
import pytest

from stream_service.plugins.sources import harmony_spiral as hs

from .conftest import GPU


def _host_only(layout: str = "fifths") -> hs.HarmonySpiral:
    """A scene with the host-side state of setup and no GPU (for _build)."""
    s = hs.HarmonySpiral.__new__(hs.HarmonySpiral)
    s.w, s.h, s.sc, s.R = 1920, 1080, 1.0, hs.R_FRAC * 1080
    s.layout = layout
    s._places()
    s.kick = s.drift = 0.0
    return s


def _table(notes: dict) -> np.ndarray:
    """A notes table: {pitch: (level, vel, age_on)}; every other key silent and never struck."""
    n = np.zeros((128, 4), np.float32)
    n[:, 2] = np.inf
    n[:, 3] = np.inf
    for p, (lv, vel, age) in notes.items():
        n[p] = (lv, vel, age, -1.0)
    return n


@pytest.mark.parametrize("layout", hs.LAYOUTS)
def test_key_places(layout):
    spoke, turn, q0, q1 = hs.key_places(layout)
    keys = np.arange(hs.LOW, hs.HIGH + 1)
    assert len(set(zip(spoke.tolist(), turn.tolist()))) == hs.N_KEYS          # one place per key
    cs = spoke[keys % 12 == 0]
    assert np.all(cs == 0)                                                     # every C on the top spoke
    assert turn.argmin() == 0 and turn.argmax() == hs.N_KEYS - 1               # A0 innermost, C8 outermost
    assert np.all(np.diff(turn[keys % 12 == 0]) == 1.0)                        # each octave one turn out
    if layout == "fifths":
        assert spoke[67 - hs.LOW] == 1 and spoke[65 - hs.LOW] == 11            # G clockwise of C, F anticlockwise
    else:
        assert np.allclose(turn, keys / 12.0)                                  # chromatic: exactly pitch


def test_build_caps_threads_and_orders_the_outline():
    s = _host_only()
    p = hs.HarmonySpiral.validate({})
    hb = np.zeros(hs.N_AR, np.float32)
    # a C major triad, held
    s._build(hb, _table({60: (0.5, 0.6, 1.0), 64: (0.5, 0.6, 1.0), 67: (0.5, 0.6, 1.0)}), p, 0.0)
    assert (s.n_nodes, s.n_threads, s.n_poly) == (3, 3, 3)
    raw = hb[hs.O_RN:hs.O_RN + 3 * hs.REC_R].reshape(3, hs.REC_R)
    ang = np.degrees(np.arctan2(raw[:, 0] - 960.0, -(raw[:, 1] - 540.0))) % 360.0
    assert np.allclose(sorted(ang), [0.0, 30.0, 120.0], atol=0.01)            # C, G, E around the circle
    assert np.all(np.diff(ang) > 0)                                            # outline order: around the centre
    # twenty sounding keys: twelve threaded, every sounding key a node
    many = {21 + 4 * i: (0.1 + 0.03 * i, 0.5, 2.5) for i in range(20)}
    s._build(hb, _table(many), p, 0.0)
    assert s.n_threads == hs.NS and s.n_nodes == 20
    # silence, and a table full of junk, build nothing and never raise
    s._build(hb, _table({}), p, 0.0)
    assert (s.n_nodes, s.n_threads) == (0, 0)
    junk = np.full((128, 4), np.nan, np.float32)
    s._build(hb, junk, p, 0.0)
    assert s.n_nodes == 0


def test_tunnel_stamps_once_per_chord_and_keeps_up_with_a_run():
    s = _host_only()
    s.stamped_on, s.pending = -1e9, None
    # a chord struck at 10.0 s, watched at 60 frames/s: one stamp, once it has bloomed
    got = [(t, s._stamp(t - 10.0, t)) for t in np.arange(10.0, 13.0, 1 / 60)]
    stamps = [t for t, inj in got if inj == 1.0]
    assert len(stamps) == 1 and 10.0 + hs.STAMP_AGE <= stamps[0] < 10.0 + hs.STAMP_AGE + 0.02
    assert all(inj == hs.TRACE for t, inj in got if t != stamps[0])
    assert s._stamp(np.inf, 14.0) == hs.TRACE                                 # nothing in view
    # a run, a note every 0.1 s: never 0.15 s old, still stamped every STAMP_GAP or so
    stamps = []
    for t in np.arange(20.0, 22.0, 1 / 60):
        newest = (t - 20.0) % 0.1
        if s._stamp(newest, t) == 1.0:
            stamps.append(t)
    gaps = np.diff(stamps)
    assert len(stamps) >= 4 and gaps.min() >= hs.STAMP_GAP - 1e-6 and gaps.max() < hs.STAMP_GAP + 0.15


def test_struck_this_instant_blooms_from_the_next_frame():
    s = _host_only()
    p = hs.HarmonySpiral.validate({})
    hb = np.zeros(hs.N_AR, np.float32)
    s._build(hb, _table({60: (0.6, 0.7, 0.0)}), p, 0.0)
    assert s.n_nodes == 0
    s._build(hb, _table({60: (0.6, 0.7, 0.03)}), p, 0.0)
    assert s.n_nodes == 1


# -- GPU: a few frames with the test piano ------------------------------------------------------------------------
W, H = 320, 180


@pytest.fixture(scope="module")
def env():
    pytest.importorskip("cupy")
    from stream_service.gpu import RenderContext
    from stream_service.tools.fxtest import FxSound

    ctx = RenderContext(GPU, W, H, 60, prealloc_buffers=0)
    with ctx.device, ctx.stream:
        yield ctx, FxSound(ctx.cp, "chords")


def _make(ctx, snd, **preset):
    s = hs.HarmonySpiral()
    s.sound = snd
    s.setup(ctx, hs.HarmonySpiral.validate(preset))
    return s


@pytest.mark.gpu
@pytest.mark.parametrize("history", hs.HISTORIES)
def test_renders_notes_graph_matches_direct(env, history):
    ctx, snd = env
    cp = ctx.cp
    ref, new = _make(ctx, snd, history=history), _make(ctx, snd, history=history)
    ref._mode = "direct"
    a, outs = ctx.new_frame(), [ctx.new_frame() for _ in range(2)]
    worst, lit, t = 0, 0.0, 4.0 * hs.HISTORIES.index(history)   # the shared piano's clock only goes forward
    for i in range(60):
        dt = 0.0 if i in (0, 40) else 1 / 60
        t += dt
        snd.tick(t)
        o = outs[i % 2]
        ref.render(t, dt, a)
        new.render(t, dt, o)
        worst = max(worst, int(cp.abs(a.astype(cp.int16) - o.astype(cp.int16)).max()))
        lit = max(lit, float(o[..., :3].mean()))
    ctx.stream.synchronize()
    assert worst <= 1
    assert new._mode == "graph" and new.calls == 4          # event query, the arena copy, its fence, the graph
    assert new.n_nodes > 0 and new.n_threads > 0          # the chords sound by now
    assert lit > 2.0                                       # not black
    new.close()
    ref.close()


@pytest.mark.gpu
def test_quiet_and_the_other_looks(env):
    ctx, snd = env
    cp = ctx.cp
    looks = [{}, {"colours": "accent", "palette": "ember", "twist": -0.5},
             {"layout": "chromatic", "colours": "register", "history": "flat"},
             {"render_scale": 1.0, "linger": 0.0, "names": 0.0, "fill": 0.0, "threads": 0.0, "depth": 0.0}]
    for look in looks:
        s = _make(ctx, None, **look)                       # no sound at all: the guide and the dust
        o = ctx.new_frame()
        for i in range(5):
            s.render(i / 60, 0.0 if i == 0 else 1 / 60, o)
        assert float(o[..., :3].mean()) > 1.0              # never black
        s.sound = snd
        for i in range(20):
            snd.tick(20.0 + i / 60)
            s.render(1.0 + i / 60, 1 / 60, o)
        ctx.stream.synchronize()
        assert int(o[..., :3].max()) > 0
        s.close()
