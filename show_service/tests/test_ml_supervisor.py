"""The show_service side of the ML worker (README S4.3): FrameFeeder into a real pipe (shapes, segments, times, GAP,
drops and AFTER_DROP, a dead reader), and WorkerSupervisor against ``python -m show_service.ml.worker --fake`` (under
sys.executable, a tmp models dir) and against a stand-in that prints junk and stalls: hello, the feed attached,
results, a killed worker restarted on the backoff, the stall restart, disabled, a missing venv, stop.

Only processes the test made are signalled. No ports, no real room API, never the real models dir."""
from __future__ import annotations

import asyncio
import os
import signal
import stat
import sys
import time

import numpy as np
import pytest

from show_service import contract as C
from show_service.ml import protocol as P
from show_service.ml import supervisor as S

FS = C.ML_FS


# -- FrameFeeder -------------------------------------------------------------------------------------------------------
def drain(r):
    p, out = P.FrameParser(), []
    while True:
        try:
            data = os.read(r, 65536)
        except BlockingIOError:
            break
        if not data:
            break
        out += list(p.feed(data))
    assert p.bad == 0
    return out


@pytest.fixture
def pipe():
    r, w = os.pipe()
    os.set_blocking(w, False)
    os.set_blocking(r, False)
    yield r, w
    for fd in (r, w):
        try:
            os.close(fd)
        except OSError:
            pass


def test_feeder_frames_segments_and_times(pipe):
    r, w = pipe
    f = S.FrameFeeder()
    f.write("room", 10.0, np.ones(500, np.float32))     # no fd yet: returns at once
    assert f.frames == 0
    f.attach(w)
    blocks = [(100.0 + k * 0.0300003, np.full(480, k, np.float32)) for k in range(10)]   # 4800 samples, 30 ms blocks
    for t0, x in blocks:
        f.write("room", t0, x)
    out = drain(r)
    assert [fr.n0 for fr in out] == [0, 800, 1600, 2400, 3200, 4000]
    assert out[0].flags == C.ML_FLAG_SEGMENT and all(fr.flags == 0 for fr in out[1:])
    assert {fr.seg for fr in out} == {1} and {fr.stream for fr in out} == {"room"}
    for fr in out:                                   # t0 = the block time of its first sample (+ i / fs within it)
        k, i = divmod(fr.n0, 480)
        assert fr.t0 == pytest.approx(blocks[k][0] + i / FS, abs=1e-4)
        assert fr.samples()[0] == np.float32(k)
    f.write("other", 200.0, np.zeros(800, np.float32))   # another stream: a new segment
    f.segment()
    f.write("other", 200.05, np.zeros(800, np.float32))
    out = drain(r)
    assert [(fr.seg, fr.n0, fr.flags, fr.stream) for fr in out] == [
        (2, 0, C.ML_FLAG_SEGMENT, "other"), (3, 0, C.ML_FLAG_SEGMENT, "other")]
    assert f.stats()["segments"] == 3 and f.stats()["attached"]


def test_feeder_gap_flags(pipe):
    r, w = pipe
    f = S.FrameFeeder()
    f.attach(w)
    f.write("s", 0.0, np.ones(1000, np.float32))
    f.write("s", 1000 / FS, np.zeros(1000, np.float32), gap=True)
    f.write("s", 2000 / FS, np.ones(1200, np.float32))
    out = drain(r)
    assert [bool(fr.flags & C.ML_FLAG_GAP) for fr in out] == [False, True, True, False]


def test_feeder_full_pipe_drops_and_flags(pipe):
    r, w = pipe
    f = S.FrameFeeder()
    f.attach(w)
    x = np.zeros(800, np.float32)
    n = 0
    while f.drops < 3:
        f.write("s", n * 0.05, x)
        n += 1
        assert n < 10000
    written = f.frames
    out = drain(r)
    assert len(out) == written                     # every written frame whole: no torn frame
    f.write("s", n * 0.05, x)                      # room again
    nxt = drain(r)
    assert len(nxt) == 1
    fr = nxt[0]
    assert fr.flags & C.ML_FLAG_AFTER_DROP and fr.dropped == 3
    assert fr.n0 == 800 * n                        # n0 counted the dropped frames
    f.write("s", (n + 1) * 0.05, x)
    assert drain(r)[0].flags == 0


def test_feeder_dead_reader_forgets_the_fd(pipe):
    r, w = pipe
    f = S.FrameFeeder()
    f.attach(w)
    os.close(r)
    f.write("s", 0.0, np.zeros(1600, np.float32))
    assert f.broken == 1 and not f.attached
    f.write("s", 0.1, np.zeros(1600, np.float32))
    assert f.broken == 1 and f.frames == 0


def test_feeder_new_fd_starts_a_segment(pipe):
    r, w = pipe
    f = S.FrameFeeder()
    f.attach(w)
    f.write("s", 0.0, np.zeros(1600, np.float32))
    r2, w2 = os.pipe()
    os.set_blocking(w2, False)
    os.set_blocking(r2, False)
    try:
        f.attach(w2)
        f.write("s", 0.1, np.zeros(800, np.float32))
        out = drain(r2)
        assert out[0].flags == C.ML_FLAG_SEGMENT and out[0].n0 == 0 and out[0].seg == 2
    finally:
        os.close(r2)
        os.close(w2)


def test_feeder_cost():
    """Budget (mllink's MLFeed: <= 40 us p95 per 128-frame 48 kHz block); here per 43-sample 16 kHz block."""
    r, w = os.pipe()
    os.set_blocking(w, False)
    os.set_blocking(r, False)
    try:
        f = S.FrameFeeder()
        f.attach(w)
        x = np.zeros(43, np.float32)
        costs = []
        for i in range(3000):
            t = time.perf_counter()
            f.write("s", i * 43 / FS, x)
            costs.append((time.perf_counter() - t) * 1e6)
            if i % 50 == 0:
                drain(r)
        p95 = float(np.percentile(costs, 95))
        print(f"\nFrameFeeder.write: p50 {np.percentile(costs, 50):.1f} us, p95 {p95:.1f} us per 43-sample block")
        assert p95 < 200
    finally:
        os.close(r)
        os.close(w)


# -- WorkerSupervisor --------------------------------------------------------------------------------------------------
def ml_cfg(tmp_path, **kw):
    cfg = {"enabled": True, "python": sys.executable, "models_dir": str(tmp_path / "models"), "nice": 19,
           "restart": {"backoff_s": [1, 1], "healthy_s": 600, "stall_s": 3},
           "tasks": {"key": {"window_s": 3}, "beats": {"every_s": 2, "window_s": 4, "interior_margin_s": 1}}}
    cfg.update(kw)
    return cfg


async def until(cond, timeout, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(step)
    return cond()


def pcm(seconds, t0=0.0):
    t = np.arange(int(seconds * FS)) / FS
    return (0.3 * np.sin(2 * np.pi * 220 * (t + t0)) + 0.4 * (np.mod(t + t0, 0.5) < 0.02)).astype(np.float32)


async def feed_for(sup, seconds, block=0.1):
    t0 = time.monotonic()
    k = 0
    while time.monotonic() - t0 < seconds:
        sup.feeder.write("room", t0 + k * block, pcm(block, k * block))
        k += 1
        await asyncio.sleep(block)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_supervisor_fake_worker_runs_restarts_and_stops(tmp_path):
    (tmp_path / "models").mkdir()
    lines = []

    async def main():
        sup = S.WorkerSupervisor(ml_cfg(tmp_path), log_path=tmp_path / "ml-worker.log", on_line=lines.append,
                                 extra_args=("--fake",))
        sup.start()
        assert await until(lambda: sup.state == "running", 15), sup.snapshot()
        assert sup.hello["models"] and set(sup.hello["models"].values()) == {"fake"}
        assert await until(lambda: sup.feeder.attached, 2)
        await feed_for(sup, 4.5)
        assert await until(lambda: {"ced", "tags", "key"} <= set(sup.lines), 5), sup.snapshot()
        key = next(x for x in lines if x["type"] == "key")
        assert key["window_s"] == 3.0                    # the config frame reached the worker
        snap = sup.snapshot()
        assert snap["bad_lines"] == 0 and snap["feed"]["frames"] > 0 and snap["pid"] == sup.pid
        assert any("models loaded" in ln for ln in snap["log_tail"]) or snap["log_tail"]

        first = sup.pid
        os.kill(first, signal.SIGKILL)                   # our own child
        assert await until(lambda: sup.state == "backoff", 5), sup.snapshot()
        assert sup.failures_in_row == 1 and sup.restarts == 1 and not sup.feeder.attached
        assert sup.snapshot()["next_start_in_s"] is not None
        assert await until(lambda: sup.state == "running" and sup.pid not in (None, first), 15), sup.snapshot()
        assert sup.last_exit["code"] == -signal.SIGKILL and sup.last_exit["why"] == "exited"

        second = sup.pid
        await sup.stop()
        assert sup.state == "disabled" and sup.pid is None and not alive(second)
        assert sup.last_exit["code"] == 0                # it took the stop frame / EOF
    asyncio.run(main())


def test_supervisor_config_restart_disable_enable(tmp_path):
    (tmp_path / "models").mkdir()

    async def main():
        sup = S.WorkerSupervisor(ml_cfg(tmp_path), log_path=tmp_path / "w.log", extra_args=("--fake",))
        sup.start()
        assert await until(lambda: sup.state == "running", 15)
        first = sup.pid
        sup.configure(ml_cfg(tmp_path, nice=18))         # nice changed: a restart, not a failure
        assert await until(lambda: sup.state == "running" and sup.pid not in (None, first), 15), sup.snapshot()
        assert sup.failures_in_row == 0 and sup.hello["nice"] >= 18
        second = sup.pid
        sup.configure(ml_cfg(tmp_path, enabled=False))
        assert await until(lambda: sup.state == "disabled", 10)
        assert sup.pid is None and not alive(second)
        sup.configure(ml_cfg(tmp_path))
        assert await until(lambda: sup.state == "running", 15)
        await sup.stop()
    asyncio.run(main())


def test_supervisor_missing_venv_is_failed_and_retried(tmp_path):
    async def main():
        sup = S.WorkerSupervisor(ml_cfg(tmp_path, python=str(tmp_path / "nope" / "python")),
                                 log_path=tmp_path / "w.log")
        sup.start()
        assert await until(lambda: sup.state == "failed", 5)
        assert sup.why == "the ML venv is missing" and sup.snapshot()["next_start_in_s"] is not None
        assert await until(lambda: sup.failures_in_row >= 2, 5)     # tried again on the backoff, for ever
        await sup.stop()
    asyncio.run(main())


STAND_IN = """#!/bin/sh
# a stand-in "python": junk, one valid hello, then silence (it ignores stdin and the stop frame)
echo 'not json'
echo '{"type": "ced"}'
echo '{"type": "mystery", "x": 1}'
echo '{"type":"hello","v":1,"pid":0,"t":0.0,"models":{},"tasks":{"ced":false,"tags":false,"key":false,"beats":false},"versions":{},"threads":{"compute":2,"intra_op":1},"nice":19}'
exec sleep 60
"""


def test_supervisor_counts_bad_lines_and_restarts_a_stall(tmp_path):
    py = tmp_path / "fakepython"
    py.write_text(STAND_IN)
    py.chmod(py.stat().st_mode | stat.S_IXUSR)

    async def main():
        sup = S.WorkerSupervisor(ml_cfg(tmp_path, python=str(py), restart={"backoff_s": [1], "healthy_s": 600,
                                                                          "stall_s": 1.5}),
                                 log_path=tmp_path / "w.log")
        sup.start()
        assert await until(lambda: sup.state == "running", 10), sup.snapshot()
        assert sup.bad_lines == 3 and sup.lines["hello"] == 1
        first = sup.pid
        t0 = time.monotonic()
        await feed_for(sup, 1.0)                          # fed, and nothing comes back...
        assert await until(lambda: sup.restarts >= 1, 10), sup.snapshot()
        assert sup.last_exit["why"].startswith("no line for") and not alive(first)
        assert time.monotonic() - t0 < 1.5 + 2 * S.STOP_WAIT_S + 3      # stop frame ignored: SIGTERM got it
        assert sup.last_exit["code"] == -signal.SIGTERM
        assert await until(lambda: sup.state == "running" and sup.pid != first, 10)
        await sup.stop()
    asyncio.run(main())


def test_supervisor_backoff_steps_on_a_fake_clock(tmp_path):
    clock = [1000.0]
    sup = S.WorkerSupervisor(ml_cfg(tmp_path, restart={"backoff_s": [2, 5, 15, 60, 300], "healthy_s": 600,
                                                       "stall_s": 30}), clock=lambda: clock[0])
    waited = []

    async def fake_sleep(delay):
        waited.append(delay)
        clock[0] += delay

    sup._sleep = fake_sleep

    async def main():
        for n in range(1, 8):
            sup.failures_in_row = n
            waited.clear()
            await sup._backoff("backoff", None)
            yield_ = sum(waited)
            assert yield_ == pytest.approx([2, 5, 15, 60, 300, 300, 300][n - 1])
        assert sup.why == "restarting in 300 s" and sup.next_start_at is None
    asyncio.run(main())
    assert S.resolve({})["restart"] == S.DEFAULT_RESTART
    assert S.resolve({"python": "~/x"})["python"] == os.path.expanduser("~/x")


# -- review fixes: hand-off, guards, logs (README S4.4a "Review fixes") -------------------------------------------------
def test_feeder_attach_is_never_lost_and_a_reattach_starts_a_segment(pipe):
    r, w = pipe
    f = S.FrameFeeder()
    f.attach(w)
    f.attach(None)
    f.attach(w)                                       # three attaches between two writes: the last one stands
    assert f.attached
    f.write("s", 0.0, np.zeros(800, np.float32))
    f.attach(w)                                       # the same fd number again (a new worker): a new segment
    f.write("s", 0.05, np.zeros(800, np.float32))
    out = drain(r)
    assert [(fr.seg, fr.flags & C.ML_FLAG_SEGMENT) for fr in out] == [(1, C.ML_FLAG_SEGMENT), (2, C.ML_FLAG_SEGMENT)]
    f.attach(None)
    f.write("s", 0.1, np.zeros(800, np.float32))
    assert drain(r) == [] and not f.attached


def test_feeder_busy_is_odd_only_inside_a_write(pipe):
    r, w = pipe
    seen = []

    class Spy(S.FrameFeeder):
        def _send(self):
            seen.append(self.busy % 2)
            super()._send()
    f = Spy()
    f.attach(w)
    f.write("s", 0.0, np.zeros(1600, np.float32))
    assert seen == [1, 1] and f.busy % 2 == 0 and f.busy == 2


def test_finish_waits_out_a_write_under_way_or_puts_dev_null_over_the_fd():
    async def main():
        sup = S.WorkerSupervisor({})
        sup.feeder.busy = 1                            # show-rx is inside a write...
        asyncio.get_running_loop().call_later(0.05, setattr, sup.feeder, "busy", 2)   # ...that ends 50 ms later
        assert await sup._feed_released() is True
        r, w = os.pipe()
        sup.feeder.busy = 3                            # a write that doesn't end in FEED_RELEASE_S
        t0 = time.monotonic()
        assert await sup._feed_released() is False
        assert S.FEED_RELEASE_S - 0.01 <= time.monotonic() - t0 < S.FEED_RELEASE_S + 0.5
        sup._close_later(w)
        assert os.read(r, 10) == b""                   # the pipe's write end is closed: the worker sees EOF...
        os.write(w, b"x" * 10)                         # ...and that write lands in /dev/null, never elsewhere
        sup.feeder.busy = 4                            # the write ends: the fd number is closed only now
        await asyncio.sleep(0.15)
        with pytest.raises(OSError):
            os.fstat(w)
        os.close(r)
        assert sup.feed_late == 1
    asyncio.run(main())


def test_resolve_checked_never_raises_and_floors_the_backoff():
    cfg, probs = S.resolve_checked({"restart": {"stall_s": "soon", "healthy_s": float("nan"), "backoff_s": [0, 3]},
                                    "nice": "low", "enabled": "yes", "python": 7})
    assert cfg["restart"]["stall_s"] == 30 and cfg["restart"]["healthy_s"] == 600
    assert cfg["restart"]["backoff_s"] == [S.MIN_BACKOFF_S, 3.0] and cfg["nice"] == 19 and cfg["enabled"] is True
    assert len(probs) == 6 and all(p.startswith("ml.") for p in probs)
    assert S.resolve_checked({"restart": {"backoff_s": []}})[0]["restart"]["backoff_s"] == [2, 5, 15, 60, 300]
    assert S.resolve_checked({"restart": "fast"})[1]
    assert S.check_frame({"tasks": {"x": "y" * 5000}}) is not None             # over ML_CTRL_MAX_BYTES
    assert S.check_frame({"cpu_max": float("nan")}) is not None
    import datetime
    assert S.check_frame({"cpu_max": datetime.date(2026, 10, 16)}) is not None   # a YAML date
    assert S.check_frame(S.worker_section({"cpu_max": 0.3, "python": "/x", "restart": {}, "beat_votes": {}})) is None
    assert set(S.worker_section({"cpu_max": 0.3, "python": "/x", "restart": {}, "tasks": {}})) == {"cpu_max", "tasks"}


def test_supervisor_survives_settings_it_cant_send_and_stops_cleanly(tmp_path):
    (tmp_path / "models").mkdir()

    async def main():
        bad = ml_cfg(tmp_path, cpu_max=float("nan"), restart={"backoff_s": [0], "healthy_s": 600, "stall_s": "x"})
        bad["tasks"] = {**bad["tasks"], "ced": {"note": "y" * 5000}}            # a 5 KB frame
        sup = S.WorkerSupervisor(bad, log_path=tmp_path / "w.log", extra_args=("--fake",))
        sup.start()
        assert await until(lambda: sup.state == "running", 15), sup.snapshot()
        assert sup.config_errors == 1 and sup.cfg["restart"]["backoff_s"] == [S.MIN_BACKOFF_S]
        try:
            want = int(open("/proc/sys/fs/pipe-max-size").read())
        except OSError:
            want = 0
        if want >= S.PIPE_BYTES:
            assert sup.pipe_bytes == S.PIPE_BYTES                              # about 16 s of frames of slack
        sup.configure({**bad, "cpu_max": float("inf")})                         # a hot reload: counted, not raised
        assert sup.config_errors == 2 and sup.state == "running"
        pid = sup.pid
        await sup.stop()
        assert sup.state == "disabled" and not alive(pid) and not sup.feeder.attached
    asyncio.run(main())


def test_a_fault_in_the_supervisor_ends_the_run_and_is_retried(tmp_path):
    (tmp_path / "models").mkdir()

    async def main():
        sup = S.WorkerSupervisor(ml_cfg(tmp_path), log_path=tmp_path / "w.log", extra_args=("--fake",))
        real = sup._read_until_done
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                await asyncio.sleep(0.5)
                raise RuntimeError("boom")
            return await real()
        sup._read_until_done = flaky
        sup.start()
        assert await until(lambda: sup.run_errors == 1 and "boom" in ((sup.last_exit or {}).get("why") or ""), 15), \
            sup.snapshot()
        first = sup.last_exit["pid"]
        assert not alive(first) and sup.state == "failed"                     # that run's worker is gone
        assert await until(lambda: sup.state == "running", 15), sup.snapshot()   # never gives up
        pid = sup.pid
        await sup.stop()
        assert not alive(pid) and sup._task is None
    asyncio.run(main())


CRASHER = """#!/bin/sh
echo 'Traceback (most recent call last):' >&2
echo "ModuleNotFoundError: No module named 'essentia'" >&2
exit 1
"""


def test_a_crash_keeps_its_reason_and_the_last_runs_log(tmp_path):
    py = tmp_path / "crashpython"
    py.write_text(CRASHER)
    py.chmod(py.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "w.log"

    async def main():
        sup = S.WorkerSupervisor(ml_cfg(tmp_path, python=str(py)), log_path=log)
        sup.start()
        assert await until(lambda: sup.attempts >= 2 and sup.state == "backoff", 15), sup.snapshot()
        assert sup.last_exit["code"] == 1 and "No module named 'essentia'" in sup.last_exit["why"]
        await sup.stop()
    asyncio.run(main())
    text = log.read_text()
    assert text.count("ml-worker start (attempt") >= 2                         # appended, never truncated
    assert text.count("No module named 'essentia'") >= 2


def test_the_brain_waits_out_the_whole_stop_sequence():
    from show_service import brain
    assert brain.STOP_AWAIT_S >= S.STOP_MAX_S + 0.5
    assert brain.STOP_TIMEOUT_S >= brain.STOP_AWAIT_S + 2.0
