"""score/room_proc.py and room.RemoteRoom: the room's transcription in its own process, for proc workers (one render
process per screen).  The view a worker keeps must read exactly as the builder does, and MIDI played onto the music
bus must reach every worker as the same, musically right bars."""
from __future__ import annotations

import asyncio
import os
import pickle
import socket
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

from stream_service.score import room as R
from stream_service.score.quantize import ScoreBuilder
from stream_service.score.room import BuilderView, RemoteRoom, view_of
from stream_service.tests.test_score import _waltz
from stream_service.tests.test_score_roundtrip import FIFTHS, assert_musical, assert_on_the_stave, read, scale

REPO = R.pathlib.Path(__file__).resolve().parents[2]


def _same_reads(b: ScoreBuilder, v: BuilderView, ts: list[float]) -> None:
    assert (v.beats, v.unit, v.next_bar, v.bar_units) == (b.beats, b.unit, b.next_bar, b.bar_units)
    assert v.key() == b.key() and v.bar_seconds() == b.bar_seconds()
    for t in ts:
        assert v.position(t) == b.position(t) and v.where(t) == b.where(t) and v.bars_ahead(t) == b.bars_ahead(t)
    assert [n.id for n in v.notes] == [n.id for n in b.notes]
    assert [v._staff(n) for n in v.notes] == [b._staff(n) for n in b.notes]


@pytest.mark.parametrize("analysed", [False, True], ids=["beat-clock", "analysis"])
def test_the_view_reads_as_the_builder_does(analysed):
    """At every point of a piece, a view given view_of(builder) (through a pickle, as on the socket) answers the
    scene's reads (position, where, key, bar_seconds, staves) exactly as the builder."""
    b, v = ScoreBuilder(), BuilderView()
    if analysed:
        b.use_analysis(True)
        ns = [(int(p), float(t), float(d), float(vel)) for p, t, d, vel in _waltz(bars=10)]
    else:
        ev, _ = scale(60, 1, 0.5, 100)
        ns = [(p, t, 0.27, 70.0) for t, st, p, vel in ev if vel == 70]
    t_end, step, k, checks, gridded = ns[-1][1] + 6.0, 1 / 30, 0, 0, False
    offs: list = []
    t = 0.0
    while t <= t_end:
        while k < len(ns) and ns[k][1] <= t:
            p, on, d, vel = ns[k]
            b.note_on(p, vel / 127.0, on)
            offs.append((on + d, p))
            k += 1
        for off in sorted(o for o in offs if o[0] <= t):
            b.note_off(off[1], off[0])
            offs.remove(off)
        if analysed and b.grid is None and t > 4.0:
            req = b.analysis_request(t)
            if req is not None:
                beats = [round(x - req["t0"], 4) for x in np.arange(0.3, 0.3 + 40 * 0.5, 0.5)]
                b.on_analysis({"ok": True, "t0": req["t0"], "phrase": req["phrase"], "beats": beats,
                               "metre": {"beats": 3, "phase": 0, "compound": False, "conf": 0.9}, "key_fifths": 0,
                               "hands": {}, "last_onset": req["notes"][-1][1]})
        b.poll(t)
        v.apply(pickle.loads(pickle.dumps(view_of(b))))
        _same_reads(b, v, [t, t - 0.37, t + 0.5] + [n.t_on for n in b.notes])
        checks += 1
        gridded = gridded or b.grid is not None
        t += step
    assert checks > 300 and gridded == analysed          # (the phrase's end clears it)


def test_a_glow_keeps_its_note_through_the_view():
    """A view keeps a note's object while the room sends it, so a glow holding it sees its release."""
    b, v = ScoreBuilder(), BuilderView()
    b.note_on(60, 0.6, 1.0)
    v.apply(view_of(b))
    held = v.notes[0]
    assert held.t_off is None
    b.note_off(60, 1.4)
    v.apply(view_of(b))
    assert v.notes[0] is held and held.t_off == 1.4


# -- end to end: the music bus -> the room's process -> two workers -------------------------------------------------
class FakeMusicBus:
    """show_service's music bus as the room's process sees it (hello -> welcome, then music.* messages): plays
    MIDI events in real time on host monotonic ms, as the test piano does."""

    def __init__(self, events: list) -> None:
        from aiohttp import web
        from stream_service.piano_replay import TestPiano
        self.piano = TestPiano(events)
        self.port = _free_port()
        self.started = threading.Event()
        self.loop = asyncio.new_event_loop()
        self.web = web

        async def ws(request):
            sock = web.WebSocketResponse()
            await sock.prepare(request)
            msg = await sock.receive()
            assert '"hello"' in msg.data
            await sock.send_json({"type": "welcome", "v": 1, "source": "test"})
            while not sock.closed:
                for m in self.piano.poll(time.monotonic() * 1000.0):
                    await sock.send_json(m)
                await asyncio.sleep(0.005)
            return sock

        app = web.Application()
        app.router.add_get("/v1/show", ws)
        self.runner = web.AppRunner(app)
        threading.Thread(target=self._serve, daemon=True).start()
        assert self.started.wait(10)

    def _serve(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.runner.setup())
        self.loop.run_until_complete(self.web.TCPSite(self.runner, "127.0.0.1", self.port).start())
        self.started.set()
        self.loop.run_forever()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/v1/show"

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _room_proc(key: str, bus: str, *extra: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "stream_service.score.room_proc", "--key", key, "--music-bus", bus,
                             *extra], cwd=str(REPO), env=dict(os.environ), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def _wait(cond, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def runtime_dir(monkeypatch):
    """A short one: a unix socket's path is at most 107 bytes (pytest's tmp_path is too long)."""
    import shutil
    import tempfile
    d = tempfile.mkdtemp(prefix="smr", dir="/tmp")
    monkeypatch.setenv("XDG_RUNTIME_DIR", d)
    yield R.pathlib.Path(d)
    shutil.rmtree(d, ignore_errors=True)


def test_two_workers_get_the_same_right_bars_from_the_room_process(runtime_dir):
    """A scale played onto the music bus reaches two workers through one room process as the same bars, every note
    written once and spelled in the key, as the in-process room writes it; the process leaves when they do."""
    ev, played = scale(60, 1, 0.5, 100)
    bus = FakeMusicBus(ev)
    proc = _room_proc("t-e2e", bus.url, "--linger", "1", "--no-analysis")
    try:
        assert _wait(lambda: R.try_connect(R.sock_path("t-e2e")) is not None, 15), "the room's process never listened"
        a, b = RemoteRoom.join("t-e2e", "a", bus.url), RemoteRoom.join("t-e2e", "b", bus.url)
        got: dict = {"a": [], "b": []}
        end = time.monotonic() + ev[-1][0] + 9.0
        while time.monotonic() < end:
            now = time.monotonic()
            for name, room in (("a", a), ("b", b)):
                bars, _rev = room.tick(name, None, now)
                for bar in bars:
                    if bar.rewrite:
                        while got[name] and not got[name][-1][0].marker:
                            got[name].pop()
                    else:
                        got[name].append((bar, room.builder.key()[2]))
                room.builder.position(now), room.builder.bar_seconds()       # the scene's reads, between messages
            time.sleep(1 / 30)
        assert a.connects == b.connects == 1
        assert got["a"] and pickle.dumps([x[0] for x in got["a"]]) == pickle.dumps([x[0] for x in got["b"]])
        notes = read(got["a"])
        assert_musical(notes, played, FIFTHS[0], 2, True)
        assert_on_the_stave(got["a"], played, notes)
        a.leave("a")
        b.leave("b")
        assert proc.wait(timeout=10) == 0, "the room's process didn't leave after its screens"
        assert not R.sock_path("t-e2e").exists()
    finally:
        if proc.poll() is None:
            proc.kill()
        bus.close()


def test_the_first_worker_starts_the_room_process_once(runtime_dir, monkeypatch):
    """Two workers joining at once with nothing listening: one room process is started (under the lock), both join
    it, and a worker that loses it gets a phrase end, then the new one."""
    monkeypatch.setenv("SM_MUSIC_BUS", f"ws://127.0.0.1:{_free_port()}/v1/show")       # no bus: it waits for one
    monkeypatch.setattr(R, "SPAWN_ARGS", ("--no-analysis", "--linger", "2"))
    rooms = [RemoteRoom.join("t-spawn", i) for i in range(2)]
    pid_file = R.sock_path("t-spawn").with_suffix(".pid")
    try:
        assert _wait(lambda: all(r.connects == 1 for r in rooms), 20), "a worker never joined"
        pid = int(pid_file.read_text())
        out = subprocess.run(["pgrep", "-f", "stream_service.score.room_proc --key t-spawn"], capture_output=True,
                             text=True).stdout.split()
        assert out == [str(pid)], f"started {len(out)} room processes"
        os.kill(pid, 15)                                       # it goes away: the workers start another and rejoin
        assert _wait(lambda: all(r.connects == 2 for r in rooms), 30), "the workers never rejoined"
        bars, _ = rooms[0].tick(0, None, time.monotonic())
        assert bars and bars[0].marker and not bars[0].rewrite
        pid = int(pid_file.read_text())
    finally:
        for i, r in enumerate(rooms):
            r.leave(i)
        if pid_file.exists():
            os.kill(int(pid_file.read_text()), 15)


def test_screens_on_new_code_dont_join_a_room_process_on_old_code(runtime_dir, monkeypatch):
    """After a restart with new transcription code, the old room process (in its own session) may still be lingering:
    the screens start a new one on their own code's socket, and the old one is left to linger out."""
    monkeypatch.setenv("SM_MUSIC_BUS", f"ws://127.0.0.1:{_free_port()}/v1/show")
    monkeypatch.setattr(R, "SPAWN_ARGS", ("--no-analysis", "--linger", "2"))
    old_sock = str(R.sock_path("t-stamp")).replace(R.STAMP, "0ld0ld00")
    old = _room_proc("t-stamp", os.environ["SM_MUSIC_BUS"], "--sock", old_sock, "--linger", "3", "--no-analysis")
    room = None
    try:
        assert _wait(lambda: R.try_connect(R.pathlib.Path(old_sock)) is not None, 15)
        room = RemoteRoom.join("t-stamp", "a")
        assert _wait(lambda: room.connects == 1, 20)
        new_pid = int(R.sock_path("t-stamp").with_suffix(".pid").read_text())
        assert new_pid != old.pid
        assert old.wait(timeout=15) == 0, "the old room process never lingered out"
    finally:
        if room is not None:
            room.leave("a")
        if old.poll() is None:
            old.kill()
        pid_file = R.sock_path("t-stamp").with_suffix(".pid")
        if pid_file.exists():
            os.kill(int(pid_file.read_text()), 15)
