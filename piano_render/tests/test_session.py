"""piano_render/session.py: the protocol with a stubbed render (a fake child) and a stubbed PC22Output; the render
child itself (stubbed render, then real tiny FluidSynth renders at nice 19); the progress estimate; background
renders (a session that goes away leaves its render to finish, the bound on them, the hand-over to a later press);
the gain, through the real PC22Output with a fake player that records what it was sent (nothing reaches a speaker).

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
import json, os, sys, threading, time
mode = os.environ.get("FAKE_RENDER_MODE", "ok")
mark = os.environ.get("FAKE_MARK")
eof = threading.Event()
def watch():
    try:
        while os.read(0, 4096):
            pass
    except OSError:
        pass
    eof.set()
threading.Thread(target=watch, daemon=True).start()
def emit(o):
    try:
        print(json.dumps(o), flush=True)
    except (OSError, ValueError):
        pass
for i in range(3):
    emit({"ev": "progress", "stage": "rendering", "step": "engine", "done_s": i, "total_s": 3, "frac": i / 3})
    time.sleep(0.05)
if mode == "error":
    emit({"ev": "error", "why": "no sfizz here"})
    sys.exit(1)
if mode == "die":
    sys.exit(4)
if mode == "slow":
    time.sleep(float(os.environ.get("FAKE_SLOW_S", "1.5")))
if mark:
    with open(mark, "w") as f:
        json.dump({"finished": True, "eof_seen": eof.is_set()}, f)
emit({"ev": "rendered", "path": "/r.flac", "duration_s": 0.3, "onset_ms": 2.0, "cached": False, "render_s": 0.15})
try:
    sys.stdout.close()
except OSError:
    pass
'''
IDLE_PARENT = os.sched_getscheduler(0) == os.SCHED_IDLE      # children inherit it and can't leave it (RLIMIT_NICE 0)


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

    def make(mode: str = "ok", pad_s: float = 0.4, env: "dict | None" = None, **out_kw):
        monkeypatch.setenv("FAKE_RENDER_MODE", mode)
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
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
        made.append((close, s))
        return SimpleNamespace(sink=sink, send=send, close=close, rc=rc, out=out, s=s, th=th)

    yield make
    for c, s in made:
        c()
        p = s.render_proc
        if p is not None and p.poll() is None:                                   # a fake still "rendering"
            p.kill()
            p.wait(5)


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


def test_stop_during_the_render_keeps_it_and_the_next_play_plays_it(run):
    r = run("slow", env={"FAKE_SLOW_S": "1.0"})
    r.sink.next("progress")
    proc = r.s.render_proc
    r.send("stop")
    r.sink.next("stopped")
    time.sleep(0.2)
    assert proc.poll() is None                                                   # not killed: still rendering
    r.send("play")
    r.sink.next("rendered")                                                      # the same render, then played
    r.sink.next("started")
    assert r.s.render_proc is proc and not any(m["ev"] == "error" for m in r.sink.seen)
    r.close()
    assert r.rc == [0]


def test_eof_during_the_render_leaves_it_to_finish(run, tmp_path):
    mark = tmp_path / "mark.json"
    r = run("slow", env={"FAKE_SLOW_S": "1.0", "FAKE_MARK": str(mark)})
    r.sink.next("progress")
    proc = r.s.render_proc
    t = time.monotonic()
    r.close()
    assert r.rc == [0] and time.monotonic() - t < 0.5                            # the session is gone at once...
    assert proc.poll() is None                                                   # ...and its render goes on
    assert proc.wait(timeout=5) == 0
    assert json.loads(mark.read_text()) == {"finished": True, "eof_seen": True}  # told by EOF, then finished


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


# -- the render child (in this process, render stubbed) -----------------------------------------------------------
def test_render_child_progress_result_and_errors():
    sink = Sink()
    states = [{"stage": "load", "done_s": 0, "total_s": 10}] + \
        [{"stage": "engine", "done_s": d, "total_s": 10} for d in (0, 1, 2)] + \
        [{"stage": "realism", "done_s": 5, "total_s": 10}, {"stage": "write", "done_s": 0, "total_s": 10}]

    def fake_render(midi, inst, progress=None):
        for s in states:
            progress(s)
        return SimpleNamespace(path="/c.flac", duration_s=12.5, onset_ms=2.0, cached=True)

    assert session.render_child("/p.mid", "grand", session.Proto(sink), lower=False, render_fn=fake_render) == 0
    got = sink.drain(0)
    prog = [m for m in got if m["ev"] == "progress"]
    assert [m["step"] for m in prog] == ["load", "engine", "realism", "write"]   # same-step bursts thinned
    fr = [m["frac"] for m in prog]
    est = session.Estimate(session.DEFAULT_RATES[None])
    assert fr == sorted(fr) and 0.0 <= fr[0] < fr[-1] < 1.0
    assert fr[2] == est.frac({"stage": "realism", "done_s": 5, "total_s": 10})
    assert got[-1]["ev"] == "rendered" and got[-1]["path"] == "/c.flac" and got[-1]["cached"] is True

    def broken(midi, inst, progress=None):
        raise KeyError("unknown instrument 'grand'")
    sink = Sink()
    assert session.render_child("/p.mid", "grand", session.Proto(sink), lower=False, render_fn=broken) == 1
    assert sink.drain(0)[-1] == {"ev": "error", "why": "couldn't render p.mid on grand: unknown instrument 'grand'"}


def _sleeper(*marks: str) -> subprocess.Popen:
    """A process that sleeps, with ``marks`` in its command line (what the cmdline checks look for)."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", *marks])


def test_render_child_waiting_on_another_render_passes_its_progress_on(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    holder = _sleeper("piano_render")
    try:
        (cache / "k1.progress.json").write_text(json.dumps(
            {"stage": "engine", "done_s": 30.0, "total_s": 60.0, "pid": holder.pid, "updated": time.time()}))
        listed = []

        def plan(midi, inst):
            return SimpleNamespace(key="k1", dir=cache, inst={"engine": "sfizz"})

        def waits(midi, inst, progress=None):                                    # render.py waiting on the lock
            listed.append(session.running(cache))
            time.sleep(1.2)
            return SimpleNamespace(path="/c.flac", duration_s=60.0, onset_ms=1.0, cached=True)

        sink = Sink()
        assert session.render_child("/p.mid", "grand", session.Proto(sink), lower=False, render_fn=waits,
                                    plan_fn=plan) == 0
        got = sink.drain(0)
        shared = [m for m in got if m["ev"] == "progress"]
        assert shared and all(m.get("shared") and m["step"] == "engine" for m in shared)
        assert 0.0 < shared[0]["frac"] < 1.0 and len(shared) >= 2                # repeated while it waits
        assert got[-1]["ev"] == "rendered" and got[-1]["cached"] is True
        assert [(e["pid"], e["key"], e["background"]) for e in listed[0]] == [(os.getpid(), "k1", False)]
        assert session.running(cache) == []                                      # listed while it ran, only
    finally:
        holder.kill()
        holder.wait()


def test_the_lock_holder_counts_while_it_runs(tmp_path):
    other = _sleeper()                                                           # e.g. the prerender script ("python -")
    ours = _sleeper("piano_render")
    try:
        assert _until(lambda: session._proc(other.pid) and session._proc(ours.pid), 5.0)

        def holder(pid, updated):
            (tmp_path / "k.progress.json").write_text(json.dumps({"stage": "engine", "pid": pid, "updated": updated}))
            return session.holder_progress(tmp_path, "k")
        assert holder(other.pid, time.time())["pid"] == other.pid                 # its heartbeat is fresh
        assert holder(other.pid, time.time() - 60) is None                        # stale, and not piano_render
        assert holder(ours.pid, time.time() - 60)["pid"] == ours.pid              # piano_render: slow heartbeat ok
        other.kill()
        other.wait()
        assert holder(other.pid, time.time()) is None                             # a killed render's file
        assert session.holder_progress(tmp_path, "nothing") is None
    finally:
        for p in (other, ours):
            p.kill()
            p.wait()


@pytest.mark.parametrize("case, taken", [("starved", True), ("busy", False), ("prerender", False)])
def test_a_press_takes_over_a_starved_background_render(tmp_path, monkeypatch, case, taken):
    cache = tmp_path / "cache"
    cache.mkdir()
    holder = _sleeper("--render-child", "piano_render")
    try:
        assert _until(lambda: session._proc(holder.pid), 5.0)
        (cache / "k2.progress.json").write_text(json.dumps(
            {"stage": "engine", "done_s": 10.0, "total_s": 100.0, "pid": holder.pid, "updated": time.time()}))
        if case != "prerender":                                                  # a prerender isn't listed
            _entry(cache, holder, "k2", True, 1.0)
        share = (0.05, 0.9) if case != "busy" else (0.9, 0.05)                   # (on a CPU, waiting to run) a second
        monkeypatch.setattr(session, "_schedstat", lambda pid: tuple(x * time.monotonic() for x in share))
        monkeypatch.setattr(session, "_policy", lambda pid: os.SCHED_IDLE)
        monkeypatch.setattr(session, "TAKEOVER_WINDOW_S", 0.3)

        def plan(midi, inst):
            return SimpleNamespace(key="k2", dir=cache, inst={"engine": "sfizz"})

        def waits(midi, inst, progress=None):
            time.sleep(1.5)
            return SimpleNamespace(path="/c.flac", duration_s=100.0, onset_ms=1.0, cached=False)

        sink = Sink()
        assert session.render_child("/p.mid", "grand", session.Proto(sink), lower=False, render_fn=waits,
                                    plan_fn=plan) == 0
        assert (holder.poll() is not None) is taken
        assert any(m.get("shared") for m in sink.drain(0) if m["ev"] == "progress")
    finally:
        holder.kill()
        holder.wait()


# -- the estimate ----------------------------------------------------------------------------------------------------
def test_the_estimate_weights_each_stage_by_its_expected_time():
    rates = {"load": 10.0, "engine": 0.5, "realism": 0.1, "master": 0.3, "write": 0.1}
    est = session.Estimate(rates)
    assert est.frac({"stage": "load", "done_s": 0, "total_s": 0}) == 0.0          # no length yet
    assert est.frac({"stage": "load", "done_s": 0, "total_s": 100}, in_stage_s=5.0) == pytest.approx(5 / 110, abs=1e-4)
    assert est.frac({"stage": "load", "done_s": 0, "total_s": 100}, in_stage_s=60.0) == pytest.approx(9 / 110, abs=1e-4)
    assert est.frac({"stage": "engine", "done_s": 50, "total_s": 100}) == pytest.approx(35 / 110, abs=1e-4)
    assert est.frac({"stage": "master", "done_s": 0, "total_s": 100}, in_stage_s=15.0) == pytest.approx(85 / 110,
                                                                                                      abs=1e-4)
    assert est.frac({"stage": "master", "done_s": 0, "total_s": 100}, in_stage_s=99.0) == pytest.approx(97 / 110,
                                                                                                      abs=1e-4)
    assert est.frac({"stage": "start"}) == 0.0


def test_the_estimate_learns_from_the_instruments_own_renders(tmp_path):
    def sidecar(name, inst, load, eng, real, mast, wr, audio=100.0):
        (tmp_path / f"{name}.json").write_text(json.dumps({
            "instrument": inst, "duration_s": audio,
            "stats": {"engine": {"load_s": load, "audio_s": audio}, "engine_s": eng, "realism_s": real,
                      "master_s": mast, "write_s": wr}}))
    sidecar("a", "grand", 14.0, 30.0, 2.0, 4.0, 0.5)
    sidecar("b", "grand", 16.0, 20.0, 2.0, 2.0, 0.5)
    sidecar("c", "grand", 15.0, 25.0, 2.0, 3.0, 0.5)
    sidecar("d", "fazioli", 0.1, 3.0, 2.0, 3.0, 0.5)
    (tmp_path / "e.progress.json").write_text(json.dumps({"stage": "engine", "pid": 1}))
    got = session.history_rates(tmp_path, "grand")
    assert got == pytest.approx({"load": 15.0, "engine": 0.25, "realism": 0.02, "master": 0.03, "write": 0.005})
    assert session.history_rates(tmp_path, "nothing") is None
    assert session.Estimate(instrument="grand", engine="sfizz", cache=tmp_path).rates == pytest.approx(got)
    assert session.Estimate(instrument="new", engine="fluidsynth", cache=tmp_path).rates == \
        session.DEFAULT_RATES["fluidsynth"]


# -- the bound on background renders ---------------------------------------------------------------------------------
def _entry(cache: Path, p: "subprocess.Popen | None", key: str, background: bool, since: float,
           start: "int | None" = None, pid: "int | None" = None) -> dict:
    pid = pid if pid is not None else p.pid
    if start is None:
        got = session._proc(pid)
        start = got[1] if got else 0
    doc = {"pid": pid, "start": start, "key": key, "midi": f"/{key}.mid", "instrument": "grand",
           "background": background, "t": since, "since": since if background else None}
    d = cache / session.RUNNING_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pid}.json").write_text(json.dumps(doc))
    return doc


def test_at_most_two_background_renders_the_oldest_unwanted_killed_first(tmp_path):
    cache = tmp_path / "cache"
    ps = {n: _sleeper("--render-child") for n in "ABCDW"}
    impostor = _sleeper("--render-child")
    try:
        assert _until(lambda: all(session._proc(p.pid) for p in [*ps.values(), impostor]), 5.0)
        _entry(cache, ps["A"], "ka", True, 1.0)                                  # the oldest, but a press wants it
        _entry(cache, ps["W"], "ka", False, 1.5)                                 # ...this waited-on render child
        _entry(cache, ps["B"], "kb", True, 2.0)
        _entry(cache, ps["C"], "kc", True, 3.0)
        _entry(cache, None, "kd", True, 0.5, pid=999_999_999, start=1)            # gone: dropped, not counted
        _entry(cache, impostor, "ke", True, 0.1, start=1)                         # a reused pid: never killed
        (cache / "kb.progress.json").write_text(json.dumps({"stage": "engine", "pid": ps["B"].pid}))
        (cache / f"kb.flac.tmp{ps['B'].pid}-1").write_text("partial")
        _entry(cache, None, "kme", True, 4.0, pid=os.getpid())                   # the new one (this process)
        killed = session.enforce_bound(cache, 2, os.getpid())
        assert killed == [ps["B"].pid, ps["C"].pid]                              # unwanted ones, oldest first
        assert ps["B"].wait(5) and ps["C"].wait(5)
        assert ps["A"].poll() is None and ps["W"].poll() is None and impostor.poll() is None
        left = sorted((e["key"], e["background"]) for e in session.running(cache))
        assert left == [("ka", False), ("ka", True), ("kme", True)]
        assert not (cache / "kb.progress.json").exists() and not list(cache.glob("kb.*.tmp*"))
        assert session.enforce_bound(cache, 2, os.getpid()) == []                 # within the bound now
    finally:
        for p in [*ps.values(), impostor]:
            p.kill()
            p.wait()


# -- the real render child and session (tiny FluidSynth renders) ---------------------------------------------------
def _child(midi, inst, env=None):
    """The render child as a session runs it (its stdin a pipe kept open): its protocol lines, its exit code."""
    import tempfile
    with tempfile.TemporaryFile("w+") as errf:
        p = subprocess.Popen([sys.executable, "-m", "piano_render.session", "--render-child", "--midi", str(midi),
                              "--instrument", inst], cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=errf, text=True, env=env)
        try:
            out = p.stdout.read()                                                # until it exits
            rc = p.wait(timeout=120)
        finally:
            if p.poll() is None:
                p.kill()
            p.stdin.close()
        errf.seek(0)
        err = errf.read()
    return SimpleNamespace(lines=[json.loads(x) for x in out.splitlines()], rc=rc, stderr=err)


def test_the_real_render_child_runs_at_nice_19_and_caches(gm_registry, cache, midi):
    m = midi([(0.0, "on", 60, 90), (0.4, "off", 60, 40), (0.5, "on", 64, 90), (0.9, "off", 64, 40)])
    c = _child(m, "gm")
    assert c.rc == 0, c.stderr[-2000:]
    done = c.lines[-1]
    assert done["ev"] == "rendered" and done["nice"] == 19 and not done["cached"]
    assert done["sched"] == ("idle" if IDLE_PARENT else "other")                 # someone waits: not SCHED_IDLE
    assert Path(done["path"]).exists() and done["duration_s"] >= 0.9
    assert all(x["ev"] == "progress" and x["stage"] == "rendering" for x in c.lines[:-1])
    again = _child(m, "gm").lines[-1]
    assert again["cached"] is True and again["path"] == done["path"]
    assert session.running(cache) == []


def _long_piece(midi, seconds: float):
    """Chords every 0.25 s: on TimGM6mb about 4.5 s of rendering for 60 s, 9 s for 140 s (22 Oct, nice 19)."""
    ev = []
    t = 0.0
    while t < seconds:
        for k in (48, 55, 60):
            ev += [(t, "on", k, 80), (t + 0.45, "off", k, 40)]
        t += 0.25
    return midi(ev, name="long.mid")


class _Sess:
    """A real session process (no play is ever sent: nothing reaches PC22)."""

    def __init__(self, m, inst, env):
        self.p = subprocess.Popen([sys.executable, "-m", "piano_render.session", "--midi", str(m), "--instrument",
                                   inst], cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, env=env)
        self.q: "queue.Queue[dict]" = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            self.q.put(json.loads(line))

    def next(self, pred, timeout=60.0) -> dict:
        end = time.monotonic() + timeout
        while True:
            msg = self.q.get(timeout=max(0.01, end - time.monotonic()))
            if pred(msg):
                return msg

    def close(self) -> int:
        self.p.stdin.close()
        return self.p.wait(timeout=10)


def _background(cache) -> list[dict]:
    return [e for e in session.running(cache) if e.get("background")]


def _until(fn, timeout: float):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()


def test_a_session_that_goes_away_leaves_its_render_to_finish_in_the_background(gm_registry, cache, midi, tmp_path):
    from piano_render import render
    m = _long_piece(midi, 60.0)
    bglog = tmp_path / "bg.log"
    env = dict(os.environ, PIANO_SESSION_BG_LOG=str(bglog), PIANO_SESSION_BG_IDLE_AFTER_S="0.3")
    env.pop(session.GAIN_ENV, None)
    s = _Sess(m, "gm", env)
    s.next(lambda x: x["ev"] == "progress")
    t = time.monotonic()
    assert s.close() == 0 and time.monotonic() - t < 3.0                         # the session goes at once
    bg = _until(lambda: _background(cache), 5.0)
    assert len(bg) == 1, session.running(cache)
    pid = bg[0]["pid"]
    if not IDLE_PARENT:                                                           # nobody waits: down to SCHED_IDLE
        assert _until(lambda: os.sched_getscheduler(pid) == os.SCHED_IDLE, 5.0)
    hit = _until(lambda: render.cached(m, "gm", registry=gm_registry), 90.0)
    assert hit is not None and Path(hit.path).exists()
    assert _until(lambda: not session.same_process(pid, bg[0]["start"]), 10.0)
    assert session.running(cache) == []
    text = bglog.read_text()
    assert "goes on rendering in the background" in text and "rendered long.mid on gm in the background" in text


def test_a_later_press_waits_for_the_background_render_and_keeps_it_going(gm_registry, cache, midi, tmp_path):
    m = _long_piece(midi, 140.0)
    env = dict(os.environ, PIANO_SESSION_BG_LOG=str(tmp_path / "bg.log"), PIANO_SESSION_BG_IDLE_AFTER_S="1.5")
    env.pop(session.GAIN_ENV, None)
    first = _Sess(m, "gm", env)
    first.next(lambda x: x["ev"] == "progress")
    assert first.close() == 0
    orphan = _until(lambda: _background(cache), 5.0)[0]
    second = _Sess(m, "gm", env)                                                 # a press soon after
    shared = second.next(lambda x: x["ev"] == "progress" and x.get("shared"))
    assert 0.0 <= shared["frac"] < 1.0
    waiter = next(e for e in session.running(cache) if not e["background"])
    time.sleep(2.0)                                                               # past BG_IDLE_AFTER_S: still wanted
    if not IDLE_PARENT:
        assert os.sched_getscheduler(orphan["pid"]) != os.SCHED_IDLE
    assert second.close() == 0                                                    # gone again: its child, a waiter,
    assert _until(lambda: not session.same_process(waiter["pid"], waiter["start"]), 5.0)   # leaves it to the orphan
    assert [e["pid"] for e in _background(cache)] == [orphan["pid"]]
    third = _Sess(m, "gm", env)
    done = third.next(lambda x: x["ev"] == "rendered", timeout=90.0)
    assert done["cached"] is True                                                 # the background render's result
    assert third.close() == 0
    assert _until(lambda: session.running(cache) == [], 10.0)


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
