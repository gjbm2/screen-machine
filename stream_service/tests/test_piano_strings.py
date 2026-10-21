"""piano_strings: does it crash.  Frames with notes and every pedal (the damper, the soft pedal, the sostenuto), a
fast run into the undamped top keys, no sound at all, a geometry change mid-run, full render scale; and the
captured graph renders what the direct launches do.  Small frames, about 120 in all (the P4000 may be streaming).

  SM_GPU_TESTS=1 [SM_TEST_GPU=1] pytest stream_service/tests/test_piano_strings.py
"""
from __future__ import annotations

import os

import numpy as np
import pytest

pytestmark = pytest.mark.gpu

GPU = int(os.environ.get("SM_TEST_GPU", "1"))
W, H = 480, 270


def _performance() -> list:
    """A pedalled bass chord, a phrase under the soft pedal, a sostenuto chord under a fast run up to the undamped
    keys (and a pitch below the piano's range), then key-up with the dampers down."""
    from stream_service.piano_replay import CC, NOTE_OFF, NOTE_ON
    ev = []

    def note(t, p, d, v):
        ev.append((t, NOTE_ON, p, v))
        ev.append((t + d, NOTE_OFF, p, 40))

    ev.append((0.0, CC, 64, 127))
    for p in (21, 33, 45, 52, 61, 108):
        note(0.05, p, 1.2, 100)
    ev.append((1.3, CC, 64, 0))
    ev.append((1.4, CC, 67, 127))
    for i, p in enumerate((60, 64, 67, 72)):
        note(1.5 + 0.12 * i, p, 0.3, 70)
    ev.append((2.1, CC, 67, 0))
    for p in (40, 47, 52):
        note(2.2, p, 1.5, 80)
    ev.append((2.3, CC, 66, 127))
    for i in range(36):
        note(2.5 + 0.035 * i, 72 + i, 0.05, 90 + 10 * (i % 3))
    note(3.0, 15, 0.2, 90)
    ev.append((3.9, CC, 66, 0))
    return sorted(ev, key=lambda e: e[0])


@pytest.fixture(scope="module")
def ctx():
    pytest.importorskip("cupy")
    from stream_service.gpu import RenderContext
    c = RenderContext(GPU, W, H, 60, prealloc_buffers=0)
    with c.device, c.stream:
        yield c


def _sound(ctx):
    from stream_service.piano_replay import TestPiano
    from stream_service.tools.fxtest import FxSound
    snd = FxSound(ctx.cp, "chords")
    snd.piano = TestPiano(_performance(), loop=True, start_ms=0.0)
    return snd


def _make(ctx, snd, **preset):
    from stream_service.plugins.sources.piano_strings import PianoStrings
    s = PianoStrings()
    s.sound = snd
    s.setup(ctx, PianoStrings.validate(preset))
    return s


def _run(ctx, snd, scenes, t0=0.0, frames=30, outs=None, each=None, step=1.0 / 15):
    """Render ``scenes`` in lockstep on one clock (``step`` s apart, some dt = 0 frames); returns the worst
    |a - b| between the first and the others, and the first's last frame."""
    cp = ctx.cp
    outs = outs or [[ctx.new_frame() for _ in range(2)] for _ in scenes]
    worst, t = 0, t0
    for i in range(frames):
        dt = 0.0 if i % 23 == 7 else step
        t += dt
        if snd is not None:
            snd.tick(t)
        for s, o in zip(scenes, outs):
            s.render(t, dt, o[i & 1])
        ref = outs[0][i & 1].astype(cp.int16)
        for o in outs[1:]:
            worst = max(worst, int(cp.abs(o[i & 1].astype(cp.int16) - ref).max()))
        if each is not None:
            each(i, t)
    ctx.stream.synchronize()
    return worst, outs[0][(frames - 1) & 1]


def test_notes_and_pedals(ctx):
    snd = _sound(ctx)
    s = _make(ctx, snd)
    seen = {"sym": 0.0, "lift": 0.0, "flash": 0.0}

    def watch(i, t):
        from stream_service.plugins.sources.piano_strings import _RING
        S = s._views[(s._slot - 1) % _RING][2]
        seen["sym"] = max(seen["sym"], float(S[:, 0].max()))
        seen["lift"] = max(seen["lift"], float(S[:, 1].max()))
        seen["flash"] = max(seen["flash"], float(S[:, 4].max()))

    _, out = _run(ctx, snd, [s], frames=64, each=watch)          # 4.3 s of the performance
    img = ctx.cp.asnumpy(out)[..., :3].astype(np.float32)
    assert img.mean() > 3.0                                  # never black
    assert seen["sym"] > 0.0 and seen["lift"] > 0.5 and seen["flash"] > 0.1
    assert s._mode == "graph" and s.calls == 4               # event query, the arena copy, its fence, the graph


def test_graph_matches_direct(ctx):
    snd = _sound(ctx)
    ref, new = _make(ctx, snd, colours="pitch", palette="rainbow"), _make(ctx, snd, colours="pitch", palette="rainbow")
    ref._mode = "direct"
    worst, _ = _run(ctx, snd, [ref, new], frames=12)
    assert worst <= 1
    assert new._mode == "graph" and len(new._graphs) >= 1


def test_no_sound_is_alive(ctx):
    s = _make(ctx, None, colours="register")
    cp = ctx.cp
    a, b = ctx.new_frame(), ctx.new_frame()
    s.render(0.0, 0.0, a)
    for i in range(8):
        s.render((i + 1) * 0.25, 0.25, b)
    ctx.stream.synchronize()
    assert float(a[..., :3].mean()) > 3.0
    assert int(cp.abs(a.astype(cp.int16) - b.astype(cp.int16)).max()) > 0     # the light breathes


def test_geometry_change_and_full_scale(ctx):
    snd = _sound(ctx)
    s = _make(ctx, snd, render_scale=1.0, colours="hands")
    _run(ctx, snd, [s], frames=6)
    geo = s._geo
    s.update(s.validate({"overstrung": 1.0, "perspective": 0.0, "colours": "hands", "render_scale": 1.0}))
    _, out = _run(ctx, snd, [s], t0=0.5, frames=6)
    assert s._geo != geo
    assert float(out[..., :3].mean()) > 3.0
