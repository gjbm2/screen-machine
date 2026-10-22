"""piano_render/session.py: the protocol with a stubbed render (a fake child) and a stubbed PC22Output; the render
child itself (stubbed render, then a real tiny FluidSynth render at SCHED_IDLE); the gain, through the real
PC22Output with a fake player that records what it was sent (nothing reaches a speaker).

    chrt -i 0 nice -n 19 ~/.venvs/sm-piano/bin/python -m pytest -q piano_render/tests/test_session.py
"""
from __future__ import annotations

import json
import os
import queue
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

from piano_render import session  # noqa: E402

FAKE_RENDER = r'''
import json, os, sys, time
mode = os.environ.get("FAKE_RENDER_MODE", "ok")
def emit(o):
    print(json.dumps(o), flush=True)
for i in range(3):
    emit({"ev": "progress", "stage": "rendering", "step": "engine", "done_s": i, "total_s": 3, "frac": i / 3})
    time.sleep(0.05)
if mode == "error":
    emit({"ev": "error", "why": "no sfizz here"})
    sys.exit(1)
if mode == "die":
    sys.exit(4)
if mode == "slow":
    time.sleep(30)
emit({"ev": "rendered", "path": "/r.flac", "duration_s": 0.3, "onset_ms": 2.0, "cached": False, "render_s": 0.15})
'''


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

    def next(self, ev: str, timeout: float = 5.0) -> dict:
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


class FakeOutput:
    """PC22Output's interface: start() -> t_audio0 (file sample 0, after lead_in_ms), wait(), stop(), done."""

    def __init__(self, dur: float = 0.3, start_s: float = 0.05, fail: "str | None" = None) -> None:
        self.dur, self.start_s, self.fail = dur, start_s, fail
        self.calls: list[tuple] = []
        self._stop = threading.Event()
        self._exited = threading.Event()
        self._exited.set()
        self.done = None
        self.session = SimpleNamespace(error=None)

    def start(self, path, **kw) -> float:
        self.calls.append(("start", path, kw))
        if self.fail:
            raise RuntimeError(self.fail)
        self._stop.clear()
        self._exited.clear()
        time.sleep(self.start_s)
        self.t0 = time.monotonic() + 0.02 + kw.get("lead_in_ms", 0.0) / 1000.0
        threading.Thread(target=self._run, daemon=True).start()
        return self.t0

    def _run(self) -> None:
        stopped = self._stop.wait(max(0.0, self.t0 + self.dur - time.monotonic()))
        self.done = {"frames": 14400, "underruns": 0, "stopped": stopped}
        self._exited.set()

    def stop(self, timeout: float = 1.0) -> None:
        self.calls.append(("stop",))
        self._stop.set()
        self._exited.wait(timeout)

    def wait(self, timeout=None) -> bool:
        return self._exited.wait(timeout)


@pytest.fixture
def run(tmp_path, monkeypatch):
    """Start a Session in a thread: returns (sink, send(cmd), close(), result list, output)."""
    script = tmp_path / "fake_render.py"
    script.write_text(FAKE_RENDER)
    made = []

    def make(mode: str = "ok", pad_s: float = 0.4, **out_kw):
        monkeypatch.setenv("FAKE_RENDER_MODE", mode)
        sink = Sink()
        out = FakeOutput(**out_kw)
        s = session.Session("/x.mid", "grand", pad_s, proto=session.Proto(sink), output_factory=lambda: out,
                            render_argv=[sys.executable, str(script)])
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
            th.join(5.0)
        made.append(close)
        return SimpleNamespace(sink=sink, send=send, close=close, rc=rc, out=out, s=s, th=th)

    yield make
    for c in made:
        c()


def test_render_play_played_eof(run):
    r = run()
    prog = r.sink.next("progress")
    assert prog["stage"] == "rendering" and set(prog) >= {"done_s", "total_s", "frac"}
    rendered = r.sink.next("rendered")
    assert rendered["path"] == "/r.flac" and rendered["duration_s"] == 0.3 and rendered["onset_ms"] == 2.0
    t = time.monotonic()
    r.send("play")
    st = r.sink.next("started")
    assert r.out.calls[0] == ("start", "/r.flac", {"lead_in_ms": 400.0})        # the pad, as the player's lead-in
    assert st["t_audio0"] == r.out.t0 and st["pad_s"] == 0.4 and st["t_audio0"] - t > 0.4
    played = r.sink.next("played")
    assert played["frames"] == 14400 and time.monotonic() >= r.out.t0 + 0.3
    r.send("play")                                                               # a loop: play again
    assert r.sink.next("started")["t_audio0"] > played.get("t", 0)
    r.sink.next("played")
    r.close()
    assert r.rc == [0] and ("stop",) in r.out.calls                             # EOF: stopped, exit 0


def test_stop_is_silent_at_once_and_no_played_follows(run):
    r = run(dur=10.0)
    r.sink.next("rendered")
    r.send("play")
    r.sink.next("started")
    t = time.monotonic()
    r.send("stop")
    r.sink.next("stopped")
    assert time.monotonic() - t < 0.05 and r.out.calls[-1] == ("stop",)
    assert not any(m["ev"] == "played" for m in r.sink.drain(0.3))
    r.close()
    assert r.rc == [0]


def test_eof_while_playing_stops_and_exits_0(run):
    r = run(dur=10.0)
    r.sink.next("rendered")
    r.send("play")
    r.sink.next("started")
    r.close()
    assert r.rc == [0] and r.out.calls[-1] == ("stop",) and r.out._stop.is_set()


def test_play_before_rendered_waits_for_it(run):
    r = run()
    r.send("play")
    r.sink.next("rendered")
    r.sink.next("started")
    r.close()
    assert r.rc == [0]


def test_stop_during_the_render_kills_it_and_play_renders_again(run):
    r = run("slow")
    r.sink.next("progress")
    time.sleep(0.2)
    proc = r.s.render_proc
    r.send("stop")
    r.sink.next("stopped")
    assert proc.wait(timeout=2) is not None                                     # killed, not left rendering
    assert not any(m["ev"] == "error" for m in r.sink.drain(0.3))              # its exit is no error
    r.send("play")
    r.sink.next("progress")                                                      # rendering again
    assert r.s.render_proc is not proc
    r.close()
    assert r.rc == [0] and r.s.render_proc.poll() is not None


@pytest.mark.parametrize("mode, why", [("error", "no sfizz here"), ("die", "exit code 4")])
def test_a_render_failure_is_an_error_and_exit_1(run, mode, why):
    r = run(mode)
    err = r.sink.next("error")
    r.th.join(5.0)
    assert why in err["why"] and r.rc == [1]


def test_a_playback_failure_is_an_error_and_exit_1(run):
    r = run(fail="no WASAPI output")
    r.sink.next("rendered")
    r.send("play")
    err = r.sink.next("error")
    r.th.join(5.0)
    assert err["why"] == "couldn't play on PC22: no WASAPI output" and r.rc == [1]


def test_render_child_progress_result_and_errors():
    sink = Sink()
    states = [{"stage": "load", "done_s": 0, "total_s": 10}] + \
        [{"stage": "engine", "done_s": d, "total_s": 10} for d in (0, 1, 2)] + \
        [{"stage": "realism", "done_s": 5, "total_s": 10}, {"stage": "write", "done_s": 0, "total_s": 10}]

    def fake_render(midi, inst, progress=None):
        for s in states:
            progress(s)
        return SimpleNamespace(path="/c.flac", duration_s=12.5, onset_ms=2.0, cached=True)

    assert session.render_child("/p.mid", "grand", session.Proto(sink), idle=False, render_fn=fake_render) == 0
    got = sink.drain(0)
    prog = [m for m in got if m["ev"] == "progress"]
    assert [m["step"] for m in prog] == ["load", "engine", "realism", "write"]   # same-step bursts thinned
    assert [m["frac"] for m in prog] == sorted(m["frac"] for m in prog) and prog[2]["frac"] == 0.875
    assert got[-1]["ev"] == "rendered" and got[-1]["path"] == "/c.flac" and got[-1]["cached"] is True

    def broken(midi, inst, progress=None):
        raise KeyError("unknown instrument 'grand'")
    sink = Sink()
    assert session.render_child("/p.mid", "grand", session.Proto(sink), idle=False, render_fn=broken) == 1
    assert sink.drain(0)[-1] == {"ev": "error", "why": "couldn't render p.mid on grand: unknown instrument 'grand'"}


def _child(midi, inst, env=None):
    return subprocess.run([sys.executable, "-m", "piano_render.session", "--render-child", "--midi", str(midi),
                           "--instrument", inst], cwd=str(ROOT), capture_output=True, text=True, timeout=120,
                          env=env)


def test_the_real_render_child_runs_idle_and_caches(gm_registry, cache, midi):
    m = midi([(0.0, "on", 60, 90), (0.4, "off", 60, 40), (0.5, "on", 64, 90), (0.9, "off", 64, 40)])
    p = _child(m, "gm")
    lines = [json.loads(x) for x in p.stdout.splitlines()]
    assert p.returncode == 0, p.stderr[-2000:]
    done = lines[-1]
    assert done["ev"] == "rendered" and done["sched"] == "idle" and done["nice"] == 19 and not done["cached"]
    assert Path(done["path"]).exists() and done["duration_s"] >= 0.9
    assert all(x["ev"] == "progress" and x["stage"] == "rendering" for x in lines[:-1])
    again = json.loads(_child(m, "gm").stdout.splitlines()[-1])
    assert again["cached"] is True and again["path"] == done["path"]


def test_main_refuses_a_bad_gain_and_reports_a_missing_midi(tmp_path):
    env = dict(os.environ, PIANO_SESSION_GAIN_DB="quiet")
    p = subprocess.run([sys.executable, "-m", "piano_render.session", "--midi", "x.mid", "--instrument", "grand-c5"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=60, env=env, input="")
    assert p.returncode == 1 and "is not a number of dB" in json.loads(p.stdout)["why"]
    env = {k: v for k, v in os.environ.items() if k != session.GAIN_ENV}
    p = subprocess.Popen([sys.executable, "-m", "piano_render.session", "--midi", str(tmp_path / "nope.mid"),
                          "--instrument", "grand-c5"], cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, env=env)
    try:
        p.stdin.write("play\n")
        p.stdin.flush()                                                          # stdin stays open: no EOF
        msg = json.loads(p.stdout.readline())
        assert p.wait(timeout=30) == 1
    finally:
        if p.poll() is None:
            p.kill()
    assert msg["ev"] == "error" and "file not found" in msg["why"]


# -- the gain ----------------------------------------------------------------------------------------------------
pc22 = pytest.importorskip("piano_render.pc22")
FAKE_PLAYER = r'''#!{python}
import json, os, sys
import numpy as np
hdr = json.loads(sys.stdin.buffer.readline())
print("ready " + json.dumps({{"device": "fake", "index": 0, "rate": hdr["rate"], "device_rate": 48000,
      "channels": 2, "format": hdr["format"], "latency_ms": 20.0, "pid": os.getpid()}}), flush=True)
need, data = hdr["frames"] * 2 * 4, b""
while len(data) < need:
    b = sys.stdin.buffer.read1(65536)
    if not b:
        break
    data += b
a = np.frombuffer(data[:need - need % 8], "<f4")
with open({out!r}, "w") as f:
    json.dump({{"max": float(np.abs(a).max()), "n": int(a.size), "lead_in_ms": hdr["lead_in_ms"]}}, f)
print("started " + json.dumps({{"dac_in_ms": 5.0, "first_frame": -int(round(hdr["lead_in_ms"] / 1000 * hdr["rate"]))}}),
      flush=True)
print("done " + json.dumps({{"frames": hdr["frames"], "underruns": 0, "stopped": False}}), flush=True)
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
def test_the_gain_reaches_the_pcm_the_real_pc22_output_sends(tmp_path, monkeypatch):
    sf = pytest.importorskip("soundfile")
    monkeypatch.setattr(pc22, "to_pcm", pc22.to_pcm)
    wav = tmp_path / "tone.wav"
    t = np.arange(24000) / 48000.0
    x = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    sf.write(str(wav), np.stack([x, x], 1), 48000, subtype="FLOAT")
    rec = tmp_path / "rec.json"
    fake = tmp_path / "fake_py"
    fake.write_text(FAKE_PLAYER.format(python=sys.executable, out=str(rec)))
    fake.chmod(0o755)
    session.apply_gain(pc22, -120.0)
    out = pc22.PC22Output(py=str(fake))
    t_call = time.monotonic()
    t0 = out.start(str(wav), lead_in_ms=250.0)
    assert out.wait(10.0)
    got = json.loads(rec.read_text())
    assert got["n"] == 2 * 24000 and got["lead_in_ms"] == 250.0
    assert got["max"] == pytest.approx(0.5e-6, rel=1e-3)                         # -120 dB on what was sent
    assert 0.25 < t0 - t_call < 5.0                                              # the pad is inside t_audio0
