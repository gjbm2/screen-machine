"""score/room.py: one transcription per room, shared by its screens' live score scenes."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from stream_service.score import room as R
from stream_service.score.room import RoomScore


def frames(n_notes: int = 24, ioi: float = 0.3, fps: float = 30.0, tail_s: float = 6.0):
    """A steady run of notes as SoundView frames: (t, frame) with onsets/releases due in it."""
    t, k, out = 0.0, 0, []
    end = n_notes * ioi + tail_s
    while t <= end:
        f = SimpleNamespace(onsets=[], releases=[], pedals=[0.0], t_ms=t * 1000.0)
        for i in range(n_notes):
            on, off = i * ioi, i * ioi + ioi * 0.9
            if t - 1 / fps < on <= t:
                f.onsets.append((60 + i % 8, 0.6, t - on))
            if t - 1 / fps < off <= t:
                f.releases.append((60 + i % 8, 0.4, t - off))
        out.append((t, f))
        t += 1 / fps
        k += 1
    return out


def test_two_screens_get_the_same_bars_from_one_feed():
    a, b = object(), object()
    room = RoomScore.join("t-same", a, analysis=False)
    assert RoomScore.join("t-same", b, analysis=False) is room
    fed = {"a": 0, "b": 0}
    got = {"a": [], "b": []}
    for t, f in frames():
        for name, m in (("a", a), ("b", b)):
            with room.lock:
                if room.leads(m):
                    room.feed(f, t)
                    fed[name] += 1
                got[name] += room.take(m)
    assert fed["a"] > 0 and fed["b"] == 0                     # one screen feeds; the notes go in once
    assert got["a"] and got["a"] == got["b"]                  # both draw the same bars
    written = [p for bar in got["a"] for st in bar.staves for e in st for p, ti in zip(e.pitches, e.tie_in) if not ti]
    assert len(written) == 24                                 # every note once, not twice
    room.leave(a)
    room.leave(b)
    assert "t-same" not in RoomScore._rooms


def test_the_next_screen_leads_when_the_leader_goes_quiet(monkeypatch):
    a, b = object(), object()
    room = RoomScore.join("t-lead", a, analysis=False)
    RoomScore.join("t-lead", b, analysis=False)
    clock = [100.0]
    monkeypatch.setattr(R.time, "monotonic", lambda: clock[0])
    assert room.leads(a) and not room.leads(b)
    clock[0] += R.LEAD_STALE_S + 0.05                         # a stops rendering (another scene, or not shown yet)
    assert room.leads(b) and not room.leads(a)
    room.leave(b)                                             # the leader leaves: the next one to tick leads
    assert room.leads(a)
    room.leave(a)


def test_a_screen_joining_mid_phrase_gets_the_phrase_so_far():
    a = object()
    room = RoomScore.join("t-join", a, analysis=False)
    fs = frames()
    half = len(fs) // 2 + 60
    for t, f in fs[:half]:
        with room.lock:
            room.leads(a) and room.feed(f, t)
    late = object()
    RoomScore.join("t-join", late, analysis=False)
    for t, f in fs[half:]:
        with room.lock:
            room.leads(a) and room.feed(f, t)
    a_bars, late_bars = room.take(a), room.take(late)
    assert late_bars and [x for x in a_bars if not x.marker][:1] == [x for x in late_bars if not x.marker][:1]
    room.leave(a)
    room.leave(late)


def test_two_render_threads_at_once():
    a, b = object(), object()
    room = RoomScore.join("t-threads", a, analysis=False)
    RoomScore.join("t-threads", b, analysis=False)
    fs = frames(n_notes=40, ioi=0.2)
    got: dict = {"a": [], "b": []}
    errors: list = []

    def run(name, m):
        try:
            for t, f in fs:
                with room.lock:
                    if room.leads(m):
                        room.feed(f, t)
                    got[name] += room.take(m)
                    room.builder.position(t), room.builder.key()
                time.sleep(0)
        except Exception as e:                                # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=run, args=("a", a)), threading.Thread(target=run, args=("b", b))]
    for th in ts:
        th.start()
    for th in ts:
        th.join()
    assert not errors
    for name in ("a", "b"):
        with room.lock:
            got[name] += room.take(a if name == "a" else b)
    assert got["a"] == got["b"] and got["a"]
    room.leave(a)
    room.leave(b)


def test_the_last_screen_out_closes_the_analysis():
    closed = []
    a, b = object(), object()
    room = RoomScore.join("t-close", a, analysis=False)
    RoomScore.join("t-close", b, analysis=False)
    room.analyser = SimpleNamespace(close=lambda: closed.append(1), ok=True)
    room.leave(a)
    assert not closed
    room.leave(b)
    assert closed == [1] and "t-close" not in RoomScore._rooms
