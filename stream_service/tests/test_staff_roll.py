"""staff_roll: does it crash, does it draw notes.  A steady phrase (a scale over bass chords, then a held chord) on
the test piano, the room's transcription in process (RoomScore, the beat clock: no analysis worker, no room
process), small frames; plus no sound, the night look at full scale, and the page-time map (shared plumbing: its
parking and re-anchoring are what keep x exact).

  SM_GPU_TESTS=1 [SM_TEST_GPU=1] pytest stream_service/tests/test_staff_roll.py
"""
from __future__ import annotations

import os

import numpy as np
import pytest

GPU = int(os.environ.get("SM_TEST_GPU", "1"))
W, H = 960, 540


def _performance() -> list:
    """Quarter notes at 120 bpm for 9 s: a C major scale up and down in the right hand, bass chords on the beats;
    a held C major chord at the end, then silence."""
    from stream_service.piano_replay import NOTE_OFF, NOTE_ON
    ev = []

    def note(t, p, d, v):
        ev.append((t, NOTE_ON, p, v))
        ev.append((t + d, NOTE_OFF, p, 40))

    scale = [60, 62, 64, 65, 67, 69, 71, 72, 74, 72, 71, 69, 67, 65, 64, 62]
    for i in range(18):
        t = 0.2 + 0.5 * i
        note(t, scale[i % len(scale)] + (1 if i == 9 else 0), 0.45, 80)       # one C# (a sign)
        if i % 2 == 0:
            for p in (36, 43, 48):
                note(t, p, 0.95, 70)
    for p in (48, 60, 64, 67):
        note(9.3, p, 1.6, 90)
    return sorted(ev, key=lambda e: e[0])


@pytest.fixture(scope="module")
def ctx():
    pytest.importorskip("cupy")
    from stream_service.gpu import RenderContext
    c = RenderContext(GPU, W, H, 60, prealloc_buffers=0)
    with c.device, c.stream:
        yield c


@pytest.fixture(autouse=True)
def local_room(monkeypatch):
    """The room's transcription in this process (never a room process hearing the live bus)."""
    from stream_service.score import room as R
    monkeypatch.setattr(R.RemoteRoom, "join",
                        classmethod(lambda cls, key, member, music_bus=None: R.RoomScore.join(key, member,
                                                                                              analysis=False)))


def _sound(ctx):
    from stream_service.piano_replay import TestPiano
    from stream_service.tools.fxtest import FxSound
    snd = FxSound(ctx.cp, "chords")
    snd.piano = TestPiano(_performance(), loop=False, start_ms=0.0)
    return snd


def _make(ctx, snd, **preset):
    from stream_service.plugins.sources.staff_roll import StaffRoll
    s = StaffRoll()
    s.sound = snd
    s.setup(ctx, StaffRoll.validate(preset))
    return s


def _run(ctx, snd, s, t0=0.0, frames=30, step=1.0 / 15, each=None):
    out = [ctx.new_frame() for _ in range(2)]
    t = t0
    for i in range(frames):
        dt = 0.0 if i % 23 == 7 else step
        t += dt
        if snd is not None:
            snd.tick(t)
        s.render(t, dt, out[i & 1])
        if each is not None:
            each(i, t)
    ctx.stream.synchronize()
    return out[(frames - 1) & 1]


def test_draws_notes_and_settles(ctx):
    snd = _sound(ctx)
    s = _make(ctx, snd)
    first = {}

    def watch(i, t):
        if "born" not in first and s.notes:
            first["born"] = t                              # the frame the first note is drawn on
        if "on" not in first and snd.frame.onsets:
            first["on"] = t
    try:
        out = _run(ctx, snd, s, frames=200, each=watch)    # 13.3 s: the phrase, then its end
        img = ctx.cp.asnumpy(out)[..., :3].astype(np.float32)
        assert img.mean() > 3.0
        assert first["born"] == first["on"]                # born on the frame its onset arrives
        st = s.stats
        assert st["births"] >= 45 and st["settled"] >= 20 and st["barlines"] >= 2
        assert s.n_recs > 50
        ink = (img.mean(axis=2) < 90).sum()                # dark ink on the paper
        assert ink > 2000
        sharps = [n for n in s.notes if n.pitch == 73 and n.acc in (1, 2)]
        assert sharps                                       # the F# carries its sign
        bass = [n for n in s.notes if n.pitch < 50]
        assert bass and all(n.staff == 1 for n in bass)
        assert any(n.stem is not None for n in s.notes) and any(n.head != 0 for n in s.notes)   # values written
    finally:
        s.close()


def test_no_sound_is_alive(ctx):
    s = _make(ctx, None)
    try:
        out = _run(ctx, None, s, frames=6)
        assert float(out[..., :3].mean()) > 3.0
        assert s.notes == []
    finally:
        s.close()


def test_night_full_scale_and_half_scale(ctx):
    for preset in ({"look": "night", "render_scale": 1.0, "systems": 3}, {"look": "paper", "render_scale": 0.5}):
        snd = _sound(ctx)                                  # (its own clock from 0)
        s = _make(ctx, snd, **preset)
        try:
            out = _run(ctx, snd, s, frames=40)
            assert float(out[..., :3].mean()) > 3.0
            assert s.stats["births"] > 5
        finally:
            s.close()


def test_time_map_parks_and_anchors():
    from stream_service.plugins.sources.staff_roll import TimeMap
    m = TimeMap(100.0)
    m.anchor(100.0, 100.0)
    for i in range(1, 61):                                 # playing: page time runs with sound time
        m.advance(100.0 + i / 60, 1.0, 1 / 60)
    assert abs(m.at(101.0) - 1.0) < 1e-6
    for i in range(61, 361):                               # silence: it parks
        m.advance(100.0 + i / 60, 0.0, 1 / 60)
    u_park = m.at(106.0)
    assert u_park - m.at(105.0) < 1e-3 and m.rate == 0.0
    m.anchor(105.5, 106.0)                                 # an onset heard late: full speed from its time
    assert abs(m.at(106.0) - (m.at(105.5) + 0.5)) < 1e-6
    assert abs(m.at(100.5) - 0.5) < 1e-6                   # history kept: earlier notes keep their x


def test_cold_first_frame_720p(ctx):
    """As a render worker runs it: set up and primed on a stream of its own before the session's first frame (the
    Sound not updated yet: t_ms 0), then the first real frame on the render stream at photon time on a host up for
    months, with the piano already playing.  26cdc526's first frame took 22.5 s there (a page-time map running from
    t 0 to the photon clock, a million lines made in a loop); the guard reads such a span as GPU time."""
    from stream_service.gpu import RenderContext
    from stream_service.piano_replay import TestPiano
    from stream_service.tools.fxtest import FxSound
    cp = ctx.cp
    c720 = RenderContext(GPU, 1280, 720, 30, prealloc_buffers=0)
    with c720.device:
        snd = FxSound(cp, "chords")                         # its frame is the view's, t_ms 0, as sound.Sound's is
        T0 = 8.9e6                                          # photon seconds: CLOCK_MONOTONIC after ~103 days up
        snd.piano = TestPiano(_performance(), loop=True, start_ms=(T0 - 3.0) * 1000.0)
        from stream_service.plugins.sources.staff_roll import StaffRoll
        s = StaffRoll()
        s.sound = snd
        prime = cp.cuda.Stream(non_blocking=True)
        try:
            with prime:                                     # renderer.prepare: setup and one throwaway render
                s.setup(c720, StaffRoll.validate({"look": "night"}))
                s.render(0.0, 0.0, c720.new_frame())
            prime.synchronize()
            out = [c720.new_frame() for _ in range(2)]
            spans = []
            with c720.stream:
                for i in range(24):
                    snd.tick(T0 + i / 30.0)
                    e0, e1 = cp.cuda.Event(), cp.cuda.Event()
                    e0.record()
                    s.render(i / 30.0, 1.0 / 30, out[i & 1])
                    e1.record()
                    e1.synchronize()
                    spans.append(cp.cuda.get_elapsed_time(e0, e1))
            assert s.stats["births"] > 5                    # the piano was heard from the first real frame
            assert len(s.systems) <= s.nsys + 1 and s.gen <= 1
            assert spans[0] < 100.0, spans                  # (the bug: seconds)
            assert max(spans) < 100.0, spans
        finally:
            s.close()
