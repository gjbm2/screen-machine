"""piano_render/session.py: the protocol (load, rendered, play, started, played, stop, EOF) with real Streamers on
TimGM6mb (FluidSynth) and fake sinks that pace and read them like pc22.py and sonos.py do; the arm; the calibrator's
wiring (a fake calibrator, the real StreamReference); the kept MP3 copy (real ffmpeg, into tmp); the load estimate;
main's refusals; the gain, through the real PC22Output with a fake player that records what it was sent. Nothing
reaches a speaker or a Sonos.

    chrt -i 0 nice -n 19 ~/.venvs/sm-piano/bin/python -m pytest -q piano_render/tests/test_session.py
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from piano_render import session, stream  # noqa: E402
from piano_render.pc22 import Halted  # noqa: E402

SR = 48000
NOTES = [(0.0, "on", 60, 90), (0.25, "off", 60, 64), (0.3, "on", 64, 80), (0.6, "off", 64, 64)]


class Sink:
    """Proto's stream: every line parsed into a queue."""

    def __init__(self) -> None:
        self.q: "queue.Queue[dict]" = queue.Queue()
        self.buf = ""
        self.seen: list[dict] = []

    def write(self, s: str) -> None:
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.q.put(json.loads(line))

    def flush(self) -> None:
        pass

    def next(self, ev: str, timeout: float = 15.0) -> dict:
        end = time.monotonic() + timeout
        while True:
            msg = self.q.get(timeout=max(0.01, end - time.monotonic()))
            self.seen.append(msg)
            if msg["ev"] == ev:
                return msg

    def drain(self, wait: float = 0.2) -> list[dict]:
        time.sleep(wait)
        out = []
        while not self.q.empty():
            out.append(self.q.get())
        self.seen += out
        return out


class FakeSink:
    """PC22Output's shape for streams: start(source, lead_in_ms) iterates source.blocks(position) on a thread, the
    way pc22.py does (position: 0 until it has started, then the wall clock from t_audio0; Halted after stop), and
    returns t_audio0 once the source's ahead_s is in; wait(); stop(); done; session.error / stream_error; ready."""

    def __init__(self, fail: "str | None" = None, start_s: float = 0.02) -> None:
        self.fail, self.start_s = fail, start_s
        self.calls: list[tuple] = []
        self.got: list[np.ndarray] = []
        self.plays: list[list[np.ndarray]] = []
        self._halt = threading.Event()
        self._exited = threading.Event()
        self._exited.set()
        self.done = None
        self.session = SimpleNamespace(error=None, stream_error=None)
        self.ready = {"device": "Fake speakers"}
        self.t0: "float | None" = None

    def position(self) -> float:
        if self._halt.is_set():
            raise Halted()
        return 0.0 if self.t0 is None else (time.monotonic() - self.t0) * SR

    def start(self, source, **kw) -> float:
        self.calls.append(("start", source, kw))
        if self.fail:
            raise RuntimeError(self.fail)
        self._halt.set()                                                 # a start while playing stops it first
        self._exited.wait(1.0)
        self._halt = threading.Event()
        self._exited.clear()
        self.t0, self.done = None, None
        self.session = SimpleNamespace(error=None, stream_error=None)
        got: list[np.ndarray] = []
        self.plays.append(got)
        need = int(getattr(source, "ahead_s", 0.3) * SR)
        have = threading.Event()
        lead = kw.get("lead_in_ms", 0.0) / 1000.0

        def run() -> None:
            n = 0
            try:
                for b in source.blocks(self.position):
                    got.append(np.array(b.data))
                    n += len(b.data)
                    if n >= need:
                        have.set()
            except Halted:
                pass
            except Exception as e:                                   # noqa: BLE001  as pc22.py: plays out
                self.session.stream_error = f"{type(e).__name__}: {e}"
            have.set()
            if self.t0 is not None:
                self._halt.wait(max(0.0, self.t0 + n / SR - time.monotonic()))
            self.done = {"frames": n, "underruns": 0, "stopped": self._halt.is_set()}
            self._exited.set()

        threading.Thread(target=run, daemon=True).start()
        while not have.wait(0.01):
            if self._halt.is_set():
                raise RuntimeError("stopped before 'started'")
        if self._halt.is_set():
            raise RuntimeError("stopped before 'started'")
        time.sleep(self.start_s)
        self.t0 = time.monotonic() + 0.02 + lead
        return self.t0

    def stop(self, timeout: float = 1.0) -> None:
        self.calls.append(("stop",))
        self._halt.set()
        self._exited.wait(timeout)

    def wait(self, timeout=None) -> bool:
        return self._exited.wait(timeout)


class FakeSonos(FakeSink):
    """SonosOutput's shape: start(source, lead_in_ms, url, title, volume) -> t_audio0; pad_s; stream_start_ms;
    settle(); error words after the play (``error``)."""

    def __init__(self, error: "str | None" = None, **kw) -> None:
        super().__init__(**kw)
        self.error_after = error
        self.pad_s = 0.0
        self.stream_start_ms = 1500.0
        self.settled = False

    def start(self, source, **kw) -> float:
        self.pad_s = kw.get("lead_in_ms", 0.0) / 1000.0
        t0 = super().start(source, **kw) + self.stream_start_ms / 1000.0
        self.t0 = t0
        if self.error_after:
            self.session = SimpleNamespace(error=self.error_after, stream_error=None)
            self._halt.set()
        return t0

    def wait(self, timeout=None) -> bool:
        ok = super().wait(timeout)
        if ok and self.error_after:
            self.done = None                                             # it left our stream: an error, no "done"
        return ok

    def settle(self, timeout: float = 2.0) -> None:
        self.settled = True


class FakeCal:
    """Calibrator's shape: begin(sink_t0, ref) -> t_audio0 (the prior: +50 ms), end(), close(), key, lat_path."""

    def __init__(self, delay: float = 0.05) -> None:
        self.delay, self.key, self.lat_path = delay, "pc22:default", None
        self.begun: list[tuple] = []
        self.ended = 0
        self.closed = False

    def begin(self, sink_t0, ref) -> float:
        self.begun.append((self.key, sink_t0, ref))
        return sink_t0 + self.delay

    def end(self) -> None:
        self.ended += 1

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def piece(gm_registry, midi):
    return SimpleNamespace(reg=gm_registry, mid=midi(NOTES, name="piece.mid"))


@pytest.fixture
def run(piece, tmp_path, monkeypatch):
    """Start a Session in a thread on a real Streamer (TimGM6mb): returns its parts."""
    made = []
    monkeypatch.setenv("PIANO_SESSION_LOADS_FILE", str(tmp_path / "loads.json"))

    def make(pad_s: float = 0.2, output: str = "pc22", fake=None, cal=None, keep=None, arm: bool = False,
             midi_path=None, ahead_s: float = 0.3, factory=None):
        sink = Sink()
        out = fake if fake is not None else FakeSink()
        mp = str(midi_path or piece.mid)
        factory = factory or (lambda: stream.Streamer(mp, "gm", registry=piece.reg, ahead_s=ahead_s))
        s = session.Session(mp, "gm", pad_s, proto=session.Proto(sink), output_factory=lambda: out,
                            output=output, streamer_factory=factory, calibrator=cal, keep_factory=keep, arm=arm,
                            ahead_s=ahead_s, loads_file=tmp_path / "loads.json")
        r, w = os.pipe()
        cmds, wf = os.fdopen(r, "r"), os.fdopen(w, "w")
        rc: list[int] = []
        th = threading.Thread(target=lambda: rc.append(s.run(cmds)), daemon=True)
        th.start()

        def send(cmd: str) -> None:
            wf.write(cmd + "\n")
            wf.flush()

        def close() -> None:
            if not wf.closed:
                wf.close()
            th.join(10.0)
        made.append(close)
        return SimpleNamespace(sink=sink, send=send, close=close, rc=rc, out=out, s=s, th=th)

    yield make
    for c in made:
        c()


def test_load_play_played_then_again_from_the_start(run):
    r = run()
    ready = r.sink.next("rendered")
    assert ready["streaming"] is True and ready["path"] is None and ready["cached"] is False
    assert ready["duration_s"] == pytest.approx(0.6, abs=0.01) and ready["load_s"] > 0 and ready["ahead_s"] == 0.3
    assert not [m for m in r.sink.seen if m["ev"] == "progress" and m.get("stage") != "loading"]
    t = time.monotonic()
    r.send("play")
    st = r.sink.next("started")
    (_, src, kw) = r.out.calls[-1]
    assert kw == {"lead_in_ms": 200.0} and src.ahead_s == 0.3 and src.sr == SR      # the pad: the player's lead-in
    assert st["t_audio0"] == r.out.t0 == st["sink_t0"] and st["delay_ms"] == 0.0 and st["t_audio0"] - t > 0.2
    played = r.sink.next("played")
    assert played["complete"] and played["late_blocks"] == 0 and played["frames"] > 0.6 * SR
    first = np.concatenate(r.out.plays[0])
    assert first.shape[1] == 2 and np.abs(first).max() > 0.01
    r.send("play")                                                                     # a loop: from frame 0 again
    r.sink.next("started")
    r.sink.next("played")
    second = np.concatenate(r.out.plays[1])
    n = min(len(first), len(second))
    assert abs(len(first) - len(second)) < SR // 10
    err = float(np.sqrt(np.mean((first[:n] - second[:n]) ** 2)) / np.sqrt(np.mean(first[:n] ** 2)))
    assert err < 0.05                                                                  # the same piece (FluidSynth's
    r.close()                                                                          # first render differs a hair)
    assert r.rc == [0] and ("stop",) in r.out.calls


def test_stop_is_silent_at_once_and_no_played_follows(run, midi):
    long = midi([(0.0, "on", 60, 90), (8.0, "off", 60, 64)], name="long.mid")
    r = run(midi_path=long)
    r.sink.next("rendered")
    r.send("play")
    r.sink.next("started")
    t = time.monotonic()
    r.send("stop")
    r.sink.next("stopped")
    assert time.monotonic() - t < 0.5 and r.out.calls[-1] == ("stop",)
    assert not any(m["ev"] == "played" for m in r.sink.drain(0.3))
    r.send("play")                                                                     # and again after a stop
    r.sink.next("started")
    r.close()
    assert r.rc == [0]


def test_eof_while_playing_stops_and_exits_0(run, midi):
    r = run(midi_path=midi([(0.0, "on", 60, 90), (8.0, "off", 60, 64)], name="long.mid"))
    r.sink.next("rendered")
    r.send("play")
    r.sink.next("started")
    r.close()
    assert r.rc == [0] and r.out.calls[-1] == ("stop",) and r.out._halt.is_set()
    assert r.s.streamer is None                                                        # freed once the render let go


def test_play_before_rendered_waits_for_it_and_progress_comes_first(run, piece):
    def slow():
        time.sleep(0.8)
        return stream.Streamer(str(piece.mid), "gm", registry=piece.reg, ahead_s=0.3)
    r = run(factory=slow)
    r.send("play")
    prog = r.sink.next("progress")
    assert prog["stage"] == "loading" and prog["step"] == "load" and 0 < prog["frac"] <= 0.95
    r.sink.next("rendered")
    r.sink.next("started")
    fr = [m["frac"] for m in r.sink.seen if m["ev"] == "progress"]
    assert fr == sorted(fr)
    r.close()
    assert r.rc == [0]


def test_a_load_failure_is_an_error_and_exit_1(run, tmp_path):
    r = run(midi_path=tmp_path / "nope.mid")
    err = r.sink.next("error")
    r.th.join(5.0)
    assert "couldn't load gm for nope.mid: file not found" in err["why"] and r.rc == [1]


def test_a_playback_failure_is_an_error_and_exit_1(run):
    r = run(fake=FakeSink(fail="no WASAPI output"))
    r.sink.next("rendered")
    r.send("play")
    err = r.sink.next("error")
    r.th.join(5.0)
    assert err["why"] == "couldn't play on PC22: no WASAPI output" and r.rc == [1]


def test_a_render_that_fails_while_it_plays_says_so(run, piece):
    class Broken:
        def __init__(self):
            self.st = stream.Streamer(str(piece.mid), "gm", registry=piece.reg, ahead_s=0.3)
            self.perf, self.stats = self.st.perf, {}

        def open(self):
            return self.st.open()

        def rewind(self):
            return {}

        def blocks(self, position):
            yield next(self.st.blocks(position))
            raise RuntimeError("std::bad_alloc")

        def close(self):
            self.st.close()
    r = run(factory=Broken)
    r.sink.next("rendered")
    r.send("play")
    err = r.sink.next("error")
    r.th.join(5.0)
    assert "failed while it played" in err["why"] and "bad_alloc" in err["why"] and r.rc == [1]


def test_the_arm_starts_the_player_while_loading_and_the_play_uses_it(run, piece, tmp_path):
    session.record_load_s("gm", session.ARM_LEAD_S + 0.2, tmp_path / "loads.json")    # expected: the arm in 0.2 s
    loaded = threading.Event()

    def slow():
        time.sleep(1.5)
        loaded.set()
        return stream.Streamer(str(piece.mid), "gm", registry=piece.reg, ahead_s=0.3)
    r = run(factory=slow, arm=True)
    t = time.monotonic()
    while not r.out.calls and time.monotonic() - t < 5:
        time.sleep(0.01)
    assert r.out.calls and r.out.calls[0][0] == "start" and r.out.calls[0][1].armed
    assert not loaded.is_set()                                                         # while the load still runs
    r.sink.next("rendered")
    time.sleep(0.2)
    assert r.out.plays[0] == []                                                        # silent until "play"
    r.send("play")
    st = r.sink.next("started")
    assert st["armed"] is True and len([c for c in r.out.calls if c[0] == "start"]) == 1
    r.sink.next("played")
    r.close()
    assert r.rc == [0]


def test_after_a_play_the_next_passes_player_is_armed_and_a_stop_drops_it(run, tmp_path):
    session.record_load_s("gm", 0.5, tmp_path / "loads.json")
    r = run(arm=True)
    r.sink.next("rendered")
    r.send("play")
    assert r.sink.next("started")["armed"] is True
    r.sink.next("played")
    time.sleep(session.REARM_S + 0.3)
    r.send("play")                                                                     # a loop's next pass
    assert r.sink.next("started")["armed"] is True
    r.sink.next("played")
    r.send("stop")                                                                     # within REARM_S: dropped
    r.sink.next("stopped")
    time.sleep(session.REARM_S + 0.3)
    starts = [c for c in r.out.calls if c[0] == "start"]
    assert len(starts) == 2 and all(c[1].armed for c in starts)
    r.close()
    assert r.rc == [0]


def test_a_stop_before_the_play_drops_the_arm_and_the_play_starts_its_own(run, tmp_path):
    session.record_load_s("gm", 0.5, tmp_path / "loads.json")                         # the arm starts at once
    r = run(arm=True)
    r.sink.next("rendered")
    t = time.monotonic()
    while not r.out.calls and time.monotonic() - t < 5:
        time.sleep(0.01)
    r.send("stop")
    r.sink.next("stopped")
    r.send("play")
    st = r.sink.next("started")
    starts = [c for c in r.out.calls if c[0] == "start"]
    assert "armed" not in st and len(starts) == 2 and starts[0][1].armed and not starts[1][1].armed
    r.close()
    assert r.rc == [0]


def test_the_calibrator_gets_every_block_and_moves_t_audio0(run):
    cal = FakeCal(0.05)
    r = run(cal=cal)
    r.sink.next("rendered")
    r.send("play")
    st = r.sink.next("started")
    assert st["t_audio0"] == pytest.approx(st["sink_t0"] + 0.05) and st["delay_ms"] == 50.0
    (key, t0, ref), = cal.begun
    assert key == "pc22:Fake speakers" and t0 == st["sink_t0"]
    r.sink.next("played")
    assert ref.end_frame() > 0 and cal.ended >= 1                                     # the envelope of what was sent
    r.close()
    assert cal.closed


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_a_kept_copy_only_when_asked_and_only_of_a_whole_play(run, tmp_path, midi):
    d = tmp_path / "kept"
    r = run(keep=lambda: session.KeepCopy(d, "piece - gm"))
    r.sink.next("rendered")
    r.send("play")
    r.sink.next("started")
    r.sink.next("played")
    t = time.monotonic()
    while not list(d.glob("*.mp3")) and time.monotonic() - t < 10:
        time.sleep(0.05)
    (mp3,) = d.glob("*.mp3")
    assert mp3.name.endswith(" piece - gm.mp3") and mp3.stat().st_size > 1000
    r.close()
    q = run(keep=lambda: session.KeepCopy(d, "long - gm"),
            midi_path=midi([(0.0, "on", 60, 90), (8.0, "off", 60, 64)], name="long.mid"))
    q.sink.next("rendered")
    q.send("play")
    q.sink.next("started")
    q.send("stop")
    q.sink.next("stopped")
    time.sleep(1.0)
    q.close()
    assert [p.name for p in d.iterdir()] == [mp3.name]                                # a stopped play leaves nothing


# -- Play on Sonos (a stubbed SonosOutput; piano_render/tests/test_sonos.py tests the real one against fakes) -------
def test_play_sonos_streams_with_its_url_title_and_volume_and_play_repeats_it(run):
    out = FakeSonos()
    r = run(output="sonos:drawing-room", fake=out, pad_s=0.75)
    r.sink.next("rendered")
    r.send('play-sonos {"url": "http://h:8700/show", "title": "Piano: X", "volume": 30}')
    st = r.sink.next("started")
    (_, src, kw) = out.calls[-1]
    assert isinstance(src, session.PlaySource)
    assert kw == {"lead_in_ms": 0.0, "url": "http://h:8700/show", "title": "Piano: X", "volume": 30}
    assert st["pad_s"] == 0.0 and st["t_audio0"] == st["sink_t0"]       # its own start delay covers the 0.75 s
    r.sink.next("played")
    r.send("play")                                                        # a loop: the last play-sonos again
    r.sink.next("started")
    assert out.calls[-1][2]["url"] == "http://h:8700/show"
    r.sink.next("played")
    r.close()
    assert r.rc == [0] and ("stop",) in out.calls and out.settled         # EOF: stop, then settle


def test_the_sonos_pad_is_what_its_start_delay_leaves(run):
    out = FakeSonos()
    out.stream_start_ms = 200.0
    r = run(output="sonos:drawing-room", fake=out, pad_s=0.75)
    r.sink.next("rendered")
    r.send('play-sonos {"url": "http://h:8700/show/api/v1/piano/audio/k.flac"}')       # an older show_sender's url
    st = r.sink.next("started")
    want = 0.75 - 0.2 - 1105 / SR
    assert out.calls[-1][2]["lead_in_ms"] == pytest.approx(want * 1000) and st["pad_s"] == pytest.approx(want, abs=1e-3)
    r.close()


@pytest.mark.parametrize("output, cmd, why", [
    ("sonos:drawing-room", "play", "play-sonos"),
    ("sonos:drawing-room", "play-sonos {}", "takes"),
    ("sonos:drawing-room", "play-sonos not json", "takes"),
    ("sonos:drawing-room", 'play-sonos {"url": "http://h/show", "zone": "kitchen"}', "kitchen"),
    ("pc22", 'play-sonos {"url": "http://h/show"}', "--output sonos"),
])
def test_a_bad_sonos_command_is_an_error_and_exit_1(run, output, cmd, why):
    r = run(output=output, fake=FakeSonos())
    r.sink.next("rendered")
    r.send(cmd)
    err = r.sink.next("error")
    r.th.join(5.0)
    assert why in err["why"] and r.rc == [1] and not [c for c in r.out.calls if c[0] == "start"]


def test_sonos_leaving_our_stream_says_so_in_its_own_words(run):
    r = run(output="sonos:drawing-room", fake=FakeSonos(error="Sonos paused"))
    r.sink.next("rendered")
    r.send('play-sonos {"url": "http://h/show"}')
    r.sink.next("started")
    err = r.sink.next("error")
    r.th.join(5.0)
    assert err["why"] == "Sonos paused" and r.rc == [1]
    q = run(output="sonos:drawing-room", fake=FakeSonos(fail="room_service isn't answering (refused)"))
    q.sink.next("rendered")
    q.send('play-sonos {"url": "http://h/show"}')
    assert q.sink.next("error")["why"] == "couldn't play on Sonos: room_service isn't answering (refused)"


# -- the load estimate ------------------------------------------------------------------------------------------------
def test_the_expected_load_learns_from_this_instruments_own_loads(tmp_path):
    p = tmp_path / "loads.json"
    assert session.expected_load_s("x", "sfizz", p) == session.DEFAULT_LOAD_S["sfizz"]
    assert session.expected_load_s("x", "fluidsynth", p) == session.DEFAULT_LOAD_S["fluidsynth"]
    for v in (1.0, 2.0, 9.0):
        session.record_load_s("x", v, p)
    assert session.expected_load_s("x", "sfizz", p) == 2.0
    for v in range(20):
        session.record_load_s("x", 3.0, p)
    assert len(json.loads(p.read_text())["x"]["load_s"]) == session.LOADS_KEPT


# -- main -------------------------------------------------------------------------------------------------------------
def _main(args, env, stdin=""):
    return subprocess.run([sys.executable, "-m", "piano_render.session", *args], cwd=str(ROOT), capture_output=True,
                          text=True, timeout=120, env=env, input=stdin)


def test_main_on_sonos_refuses_the_gain_and_an_unknown_zone():
    base = ["--midi", "x.mid", "--instrument", "grand-c5"]
    env = dict(os.environ, PIANO_SESSION_GAIN_DB="-120")
    p = _main(base + ["--output", "sonos:drawing-room"], env)
    assert p.returncode == 1 and "never the Sonos" in json.loads(p.stdout)["why"]
    env = {k: v for k, v in os.environ.items() if k != session.GAIN_ENV}
    env.update(PIANO_CALIBRATE="0", PIANO_SESSION_SONOS_STREAM="1")
    p = _main(base + ["--output", "sonos:no-such-zone"], env)
    assert p.returncode == 1 and "no Sonos zone" in json.loads(p.stdout.splitlines()[0])["why"]
    assert "old cached-file path" not in p.stderr
    p = _main(base + ["--output", "sonos:"], env)
    assert p.returncode == 2 and "pc22 or sonos:<zone>" in p.stderr
    env["PIANO_SESSION_SONOS_STREAM"] = "0"                         # show_service without the stream route yet
    p = _main(base + ["--output", "sonos:no-such-zone"], env)
    assert p.returncode == 1 and "no Sonos zone" in json.loads(p.stdout.splitlines()[0])["why"]
    assert "old cached-file path" in p.stderr                       # the old session_files.py said so


def test_the_stream_route_probe_tells_shows_own_404_from_a_missing_route():
    import http.server

    class H(http.server.BaseHTTPRequestHandler):
        kind = "json"

        def do_HEAD(self):
            self.send_response(404)
            self.send_header("Content-Type", "application/json" if H.kind == "json" else "text/plain")
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    try:
        assert session.stream_route_live(url)
        H.kind = "text"
        assert not session.stream_route_live(url)
    finally:
        srv.shutdown()
    assert not session.stream_route_live("http://127.0.0.1:9", timeout=0.5)    # nobody there
    a = session.build_parser().parse_args(["--midi", "m.mid", "--instrument", "x", "--pad-s", "0.2", "--output",
                                           "sonos:z"])
    argv = session.files_argv(a)
    assert argv[1:3] == ["-m", "piano_render.session_files"] and argv[argv.index("--pad-s") + 1] == "0.750"


def test_the_calibration_mic_from_show_service_reaches_the_calibrator(monkeypatch):
    """--calibration-mic (show_sender passes show_service's word): the Calibrator watches that stream; "" = no mic
    hears the output (no mic check); absent: calibrate's default. The old file path has no calibration."""
    from piano_render import calibrate
    base = ["--midi", "m.mid", "--instrument", "x", "--output", "sonos:drawing-room"]
    assert session.build_parser().parse_args(base).calibration_mic is None
    assert session.build_parser().parse_args(base + ["--calibration-mic", "room-mic"]).calibration_mic == "room-mic"
    a = session.build_parser().parse_args(base + ["--calibration-mic", ""])
    assert a.calibration_mic == "" and "--calibration-mic" not in session.files_argv(a)
    with pytest.raises(SystemExit):
        session.build_parser().parse_args(base + ["--calibration-mic", "no such mic"])
    monkeypatch.delenv(session.CALIBRATE_ENV, raising=False)
    monkeypatch.delenv(calibrate.MIC_ENV, raising=False)
    proto = session.Proto(Sink())
    cals = [session._calibrator("sonos:drawing-room", proto, None, "room-mic"),
            session._calibrator("sonos:drawing-room", proto, None, ""),
            session._calibrator("pc22", proto, None, None)]
    try:
        assert [c.mic for c in cals] == ["room-mic", None, calibrate.MIC_FOR["pc22"]]
        assert cals[1].no_mic_why == "show_service names no mic that hears sonos:drawing-room"
    finally:
        for c in cals:
            c.close()
    assert session._calibrator("pc22", proto, -120.0, "pc22-mic") is None        # a silent check: off


def test_main_refuses_a_bad_gain_and_reports_a_missing_midi(tmp_path):
    env = dict(os.environ, PIANO_SESSION_GAIN_DB="quiet")
    p = _main(["--midi", "x.mid", "--instrument", "grand-c5"], env)
    assert p.returncode == 1 and "is not a number of dB" in json.loads(p.stdout)["why"]
    env = {k: v for k, v in os.environ.items() if k != session.GAIN_ENV}
    env.update(PIANO_CALIBRATE="0", PIANO_SESSION_ARM="0", PIANO_SESSION_LOADS_FILE=str(tmp_path / "loads.json"))
    p = subprocess.Popen([sys.executable, "-m", "piano_render.session", "--midi", str(tmp_path / "nope.mid"),
                          "--instrument", "grand-c5"], cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, env=env)
    try:
        p.stdin.write("play\n")
        p.stdin.flush()                                                          # stdin stays open: no EOF
        msgs = []
        while True:
            line = p.stdout.readline()
            if not line:
                break
            msgs.append(json.loads(line))
            if msgs[-1]["ev"] == "error":
                break
        assert p.wait(timeout=60) == 1
    finally:
        if p.poll() is None:
            p.kill()
    assert msgs[-1]["ev"] == "error" and "file not found" in msgs[-1]["why"]


# -- the gain ----------------------------------------------------------------------------------------------------
pc22 = pytest.importorskip("piano_render.pc22")
FAKE_PLAYER = r'''#!{python}
import json, os, sys
import numpy as np
hdr = json.loads(sys.stdin.buffer.readline())
print("ready " + json.dumps({{"device": "fake", "index": 0, "rate": hdr["rate"], "device_rate": 48000,
      "channels": 2, "format": hdr["format"], "latency_ms": 20.0, "pid": os.getpid()}}), flush=True)
need = hdr["frames"] * 2 * 4 if hdr["frames"] is not None else None
pre = int(hdr["prebuffer_ms"] / 1000 * hdr["rate"]) * 8
data, started = b"", False
def start():
    print("started " + json.dumps({{"dac_in_ms": 5.0,
          "first_frame": -int(round(hdr["lead_in_ms"] / 1000 * hdr["rate"]))}}), flush=True)
while need is None or len(data) < need:
    b = sys.stdin.buffer.read1(65536)
    if not b:
        break
    data += b
    if not started and len(data) >= (pre if need is None else min(pre, need)):
        started = True
        start()
if not started:
    start()
a = np.frombuffer(data[:len(data) - len(data) % 8], "<f4")
with open({out!r}, "w") as f:
    json.dump({{"max": float(np.abs(a).max()) if a.size else 0.0, "n": int(a.size), "lead_in_ms": hdr["lead_in_ms"],
               "frames": hdr["frames"]}}, f)
print("done " + json.dumps({{"frames": int(a.size // 2), "underruns": 0, "stopped": False}}), flush=True)
'''


def test_apply_gain_scales_and_fails_closed(monkeypatch):
    monkeypatch.setattr(pc22, "to_pcm", pc22.to_pcm)
    assert session.apply_gain(pc22, None) is None
    g = session.apply_gain(pc22, -120.0)
    out = np.frombuffer(pc22.to_pcm(np.full((4, 2), 0.5, np.float32), "f32le"), "<f4")
    assert g == pytest.approx(1e-6) and np.allclose(out, 0.5e-6)
    session.apply_gain(pc22, -6.0)                                               # again: from the unscaled one
    out = np.frombuffer(pc22.to_pcm(np.full((4, 2), 0.5, np.float32), "f32le"), "<f4")
    assert np.allclose(out, 0.5 * 10 ** (-6 / 20))
    monkeypatch.setattr(pc22, "_Session", type("S", (), {"_write": lambda self: None}))
    with pytest.raises(RuntimeError, match="not playing"):
        session.apply_gain(pc22, -120.0)


@pytest.mark.skipif(not pc22.available(), reason="needs WSL interop (wslpath; the player itself is a fake)")
def test_the_gain_reaches_the_pcm_a_streamed_play_sends_the_real_pc22_output(run, tmp_path, monkeypatch):
    monkeypatch.setattr(pc22, "to_pcm", pc22.to_pcm)
    rec = tmp_path / "rec.json"
    fake = tmp_path / "fake_py"
    fake.write_text(FAKE_PLAYER.format(python=sys.executable, out=str(rec)))
    fake.chmod(0o755)
    session.apply_gain(pc22, -120.0)
    r = run(fake=pc22.PC22Output(py=str(fake)), pad_s=0.25)
    r.sink.next("rendered")
    t_call = time.monotonic()
    r.send("play")
    st = r.sink.next("started")
    played = r.sink.next("played")
    got = json.loads(rec.read_text())
    assert got["frames"] is None and got["lead_in_ms"] == 250.0                  # a stream: no length
    assert got["n"] == 2 * played["frames"] and got["n"] > 2 * 0.6 * SR
    assert 0 < got["max"] < 2e-6                                                 # -120 dB on what was sent
    assert 0.2 < st["t_audio0"] - t_call < 5.0                                   # the pad is inside t_audio0
    r.close()
