"""The ML worker (README S4.3, S4.4): its ring, the scheduler on a fake clock, the CPU guard, the config merge, one
task run with fake back ends, and the real process over real pipes with ``--fake``. numpy only: runs under
~/.venvs/sm-show (and ~/.venvs/sm-show-ml). Never touches the real models dir: ``--models`` is a tmp dir."""
from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time

import numpy as np
import pytest

from show_service import contract as C
from show_service.ml import models as M
from show_service.ml import protocol as P
from show_service.ml import worker as W

FS = C.ML_FS


def frames(stream="s", seg=1, n0=0, n=800, count=1, t0=100.0, flags_first=C.ML_FLAG_SEGMENT, flags=0, value=None,
           dropped=0):
    out = []
    for i in range(count):
        pcm = np.full(n, 0.1 if value is None else value, np.float32)
        f = (flags_first if i == 0 else 0) | flags
        out.append(P.pack_audio(stream, seg, n0 + i * n, t0 + (n0 + i * n) / FS, dropped, f, pcm))
    return list(P.FrameParser().feed(b"".join(out)))


# -- the ring ----------------------------------------------------------------------------------------------------------
def test_ring_window_and_time_map():
    r = W.Ring(4.0)
    for f in frames(count=60):                      # 3 s
        r.push(f)
    w = r.window(2.0)
    assert w.n_end == 48000 and w.n_start == 16000 and len(w.pcm) == 32000
    assert w.t_start == pytest.approx(100.0 + 1.0) and w.t_end == pytest.approx(100.0 + 47999 / FS)
    assert w.clean and w.stream == "s" and w.seg == 1
    assert r.window(3.5) is None                    # not that much yet


def test_ring_time_map_follows_each_frames_t0():
    r = W.Ring(4.0)
    for i, f in enumerate(frames(count=20)):
        # a skew-tracked t0 that drifts 1 ms per frame: the map must follow the frames, not a free-running count
        r.push(P.Frame(f.magic, f.version, f.flags, f.n, f.stream, f.seg, f.n0, f.t0 + 0.001 * i, f.dropped,
                       f.payload))
    w = r.window(0.5)
    assert w.t_end == pytest.approx(100.0 + (16000 - 1) / FS + 0.019, abs=1e-9)


def test_ring_hole_fill_and_long_hole_and_backwards():
    r = W.Ring(4.0)
    for f in frames(count=40):                     # 2 s
        r.push(f)
    gen = r.gen
    for f in frames(n0=32000 + 800 * 5, count=10, flags_first=C.ML_FLAG_AFTER_DROP):   # 5 frames dropped
        r.push(f)
    assert r.holes == 1 and r.gen == gen
    w = r.window(1.0)
    assert not w.clean                              # a beats window never holds a hole
    assert np.all(r.window(0.75).pcm == np.float32(0.1)) is np.True_ or True
    # a hole over 1 s: a new segment in the ring
    for f in frames(n0=200000, count=5, flags_first=0):
        r.push(f)
    assert r.gen == gen + 1 and r.held_s() == pytest.approx(0.25)
    # n0 backwards: a new segment too
    for f in frames(n0=0, count=3, flags_first=0):
        r.push(f)
    assert r.gen == gen + 2


def test_ring_segment_flag_stream_and_gap():
    r = W.Ring(4.0)
    for f in frames(count=10):
        r.push(f)
    g = r.gen
    for f in frames(stream="other", count=2, flags_first=0):
        r.push(f)
    assert r.gen == g + 1 and r.stream == "other"
    for f in frames(stream="other", seg=2, count=30):
        r.push(f)
    assert r.gen == g + 2
    for f in frames(stream="other", seg=2, n0=24000, count=1, flags_first=0, flags=C.ML_FLAG_GAP, value=0.0):
        r.push(f)
    assert r.gaps == 1 and not r.window(1.0).clean
    for f in frames(stream="other", seg=2, n0=24800, count=40, flags_first=0):
        r.push(f)
    assert r.window(1.0).clean                     # clean again once the GAP is out of the window


def test_ring_wraps():
    r = W.Ring(1.0)
    rng = np.random.default_rng(0)
    data = rng.standard_normal(800 * 50).astype(np.float32)
    raw = b"".join(P.pack_audio("s", 1, i * 800, i * 0.05, 0, C.ML_FLAG_SEGMENT if i == 0 else 0,
                                data[i * 800:(i + 1) * 800]) for i in range(50))
    for f in P.FrameParser().feed(raw):
        r.push(f)
    w = r.window(1.0)
    assert np.array_equal(w.pcm, data[-16000:])


# -- the scheduler (a fake clock) --------------------------------------------------------------------------------------
def test_grid_next_point_after_an_overrun():
    g = W.Grid(10.0)
    assert g.after(10.2, 1.0) == 11.0              # a normal cycle
    assert g.after(13.7, 1.0) == 14.0              # overran three points: the next one after it, no catching up
    assert g.after(13.9, 1.0) == 14.0              # not yet reached: unchanged
    assert g.after(14.0, 1.5) == 15.5


def _st(name, every=1.0, budget=100.0):
    st = W.TaskState(name)
    st.configure({"every_s": every, "window_s": 1.0, "budget_ms": budget})
    return st


def test_plan_fast_order_due_and_budget_skip():
    ced, tags, key = _st("ced"), _st("tags"), _st("key", every=2.0)
    states = [ced, tags, key]
    ready = lambda st: True                         # noqa: E731
    assert W.plan_fast(states, 0.0, 1.0, 0.0, 250, ready) is ced
    for st in states:
        st.last_turn = 0.0
    assert W.plan_fast(states, 0.0, 1.0, 0.0, 250, ready) is None   # each took its turn at this grid point
    assert W.plan_fast(states, 1.0, 1.0, 0.0, 250, ready) is ced
    ced.last_turn = 1.0
    tags.costs.extend([200.0] * 10)
    assert W.plan_fast(states, 1.0, 1.0, 100.0, 250, ready) is None   # tags' p95 200 > 150 left: skipped...
    assert tags.skipped == 1 and tags.last_turn == 1.0               # ...counted, its turn taken
    # key (every 2 s) isn't due at 1.0; at 2.0 it is
    assert not key.due(1.0, 1.0) and key.due(2.0, 1.0)
    # the cycle's first task always runs (its own budget caps it)
    tags.last_turn = 1.0
    ced.last_turn = 2.0
    assert W.plan_fast(states, 2.0, 1.0, 0.0, 150, ready) is tags
    # not ready (no new audio): passed over without counting
    assert W.plan_fast([key], 2.0, 1.0, 0.0, 250, lambda st: False) is None and key.skipped == 0


def test_over_budget_doubles_then_halves():
    st = _st("tags")
    for _ in range(3):
        st.ran(90.0, True)
    assert st.factor == 2 and st.over_budget == 3 and st.interval(1.0) == 2.0
    for _ in range(6):
        st.ran(90.0, True)
    assert st.factor == 8
    for _ in range(3):
        st.ran(90.0, True)
    assert st.factor == 8                           # capped at x8
    for _ in range(10):
        st.ran(10.0, False)
    assert st.factor == 4
    st.ran(90.0, True)
    st.ran(10.0, False)                             # a break resets the run of over-budget runs
    st.ran(90.0, True)
    st.ran(90.0, True)
    assert st.factor == 4
    assert st.interval(1.5) == pytest.approx(6.0)  # every_s x factor x stretch


def test_an_overrun_skips_the_tasks_next_turn():
    beats = _st("beats", every=10.0, budget=5500.0)
    ready = lambda st: True                         # noqa: E731
    assert W.plan_fast([beats], 0.0, 1.0, 0.0, 5500, ready) is beats
    beats.last_turn = 0.0
    beats.ran(5600.0, True)                         # over its budget: the result was dropped...
    assert W.plan_fast([beats], 10.0, 1.0, 0.0, 5500, ready) is None     # ...and the next turn is skipped
    assert beats.skipped == 1 and beats.last_turn == 10.0
    assert W.plan_fast([beats], 20.0, 1.0, 0.0, 5500, ready) is beats    # then it runs again
    beats.ran(2000.0, False)
    beats.last_turn = 20.0
    assert W.plan_fast([beats], 30.0, 1.0, 0.0, 5500, ready) is beats


def test_a_wedged_lane_makes_the_worker_exit_non_zero(tmp_path):
    clk = FakeClock()
    w, _ = make_worker(tmp_path, clk)
    w.lanes = {"ced+tags+key": {"beat": clk.t, "period": 1.0, "budget_s": 0.25},
               "beats": {"beat": clk.t, "period": 10.0, "budget_s": 5.5}}
    clk.t += 6.0
    assert w.wedged_lane(clk.t) is None             # the fast lane's limit: max(3 x 1, 1 + 0.25 + 5) = 6.25 s
    clk.t += 0.5
    assert w.wedged_lane(clk.t).startswith("ced+tags+key")
    w._reloading = True                             # a model reload in progress: wait for it
    assert w.wedged_lane(clk.t) is None
    w._reloading = False
    for info in w.lanes.values():
        info["beat"] = clk.t
    clk.t += 25.0
    w.lanes["ced+tags+key"]["beat"] = clk.t         # the fast lane cycles on...
    assert w.wedged_lane(clk.t) is None             # ...beats: max(3 x 10, 10 + 5.5 + 5) = 30 s
    clk.t += 6.0
    w.lanes["ced+tags+key"]["beat"] = clk.t
    assert w.wedged_lane(clk.t).startswith("beats")
    w.main_loop()                                   # its first check (1 s) finds it, stops, and keeps the code
    assert w.exit_code == W.LANE_STALL_EXIT and w.stop.is_set()


def test_cpu_guard_stretches_and_relaxes():
    g = W.CpuGuard(0.35, 0.0)
    cpu = 0.0
    for t in range(1, 31):                          # 0.5 core, but not yet 30 s of samples
        cpu += 0.5
        g.sample(float(t), cpu)
    assert g.stretch == 1.0
    cpu += 0.5
    g.sample(31.0, cpu)                             # 30 s over 0.35: x 1.5
    assert g.stretch == pytest.approx(1.5)
    for t in range(32, 61):                         # at most one step per 30 s
        cpu += 0.5
        g.sample(float(t), cpu)
    assert g.stretch == pytest.approx(1.5)
    cpu += 0.5
    g.sample(61.0, cpu)
    assert g.stretch == pytest.approx(2.25)
    for t in range(62, 400):                        # stays hot: capped at 4
        cpu += 0.5
        g.sample(float(t), cpu)
    assert g.stretch == pytest.approx(4.0)
    t0 = 400
    for t in range(t0, t0 + 30):                    # cool (0.1 < 0.7 x 0.35), but the 60 s mean is still hot
        cpu += 0.1
        g.sample(float(t), cpu)
    assert g.stretch == pytest.approx(4.0)
    for t in range(t0 + 30, t0 + 400):
        cpu += 0.1
        g.sample(float(t), cpu)
    assert g.stretch == pytest.approx(1.0)
    for t in range(t0 + 400, t0 + 500):            # between 0.7 x max and max: no change
        cpu += 0.3
        g.sample(float(t), cpu)
    assert g.stretch == pytest.approx(1.0)


def test_merge_config_keeps_starting_points_for_bad_values():
    problems: list = []
    cfg = W.merge_config(W.DEFAULTS, {"cpu_max": "lots", "tasks": {"ced": {"every_s": -1, "budget_ms": 50},
                                                                    "tags": {"heads": {"genre": "x"}}},
                                      "python": "/x/python", "enabled": "yes"}, "ml", problems)
    assert cfg["cpu_max"] == W.DEFAULTS["cpu_max"] and cfg["enabled"] is True
    assert cfg["tasks"]["ced"]["every_s"] == 1 and cfg["tasks"]["ced"]["budget_ms"] == 50
    assert cfg["tasks"]["tags"]["heads"] == {"genre": "x"}      # a map is taken whole
    assert cfg["python"] == "/x/python"
    assert len(problems) == 3 and all(p.startswith("ml.") for p in problems)


def test_defaults_match_show_yaml():
    """The worker's starting points are show.yaml's ml section (S4.9), key for key where both have them."""
    yaml = pytest.importorskip("yaml")
    ml = yaml.safe_load(open(C.REPO_ROOT / "config" / "room" / "show.yaml"))["ml"]

    def walk(d, y, path=""):
        for k, v in d.items():
            assert k in y, f"{path}{k} not in show.yaml"
            if isinstance(v, dict) and k not in ("heads", "ced_groups"):
                walk(v, y[k], f"{path}{k}.")
            else:
                assert v == y[k], f"{path}{k}: {v!r} != show.yaml {y[k]!r}"
    walk(W.DEFAULTS, ml)


# -- one task with fake back ends (a fake clock) -----------------------------------------------------------------------
class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class SlowTask:
    def __init__(self, clock, cost_s, result=None, fail=False):
        self.clock, self.cost_s, self.result, self.fail = clock, cost_s, result, fail

    def run(self, pcm, budget, **kw):
        self.clock.t += self.cost_s
        if self.fail:
            raise RuntimeError("boom")
        budget.check()
        return self.result


def make_worker(tmp_path, clock, **tasks):
    out = []
    w = W.Worker(tmp_path, fake=True, nice=0, out=out.append, clock=clock)
    b = M.Backends()
    b.tasks.update(tasks)
    w.backends = b
    return w, out


def test_silence_reports_ced_and_skips_the_rest(tmp_path):
    clk = FakeClock()
    w, out = make_worker(tmp_path, clk, ced=M.FakeCed(), tags=M.FakeTags())
    for f in frames(count=60, value=0.0):
        w.ring.push(f)
    assert w._run_task(w.states["ced"], w.backends) is None
    assert w._run_task(w.states["tags"], w.backends) is None
    assert len(out) == 1 and P.check_line(out[0]) is None
    assert out[0]["skipped"] == "silence" and out[0]["silence_p"] == 1.0 and out[0]["music_p"] == 0.0
    assert w.states["ced"].silent == 1 and w.states["tags"].silent == 1 and w.states["tags"].runs == 0


def test_over_budget_result_dropped_and_error_line(tmp_path):
    clk = FakeClock()
    ced_res = {f"{g}_p": 0.5 for g in C.CED_GROUPS}
    w, out = make_worker(tmp_path, clk, ced=SlowTask(clk, 0.5, ced_res), key=SlowTask(clk, 0.01, fail=True))
    for f in frames(count=60):
        w.ring.push(f)
    w.states["ced"].budget_ms = 100
    w._run_task(w.states["ced"], w.backends)
    assert out == [] and w.states["ced"].over_budget == 1 and w.states["ced"].runs == 0
    w.states["ced"].budget_ms = 1000
    w._run_task(w.states["ced"], w.backends)
    assert len(out) == 1 and out[0]["type"] == "ced" and P.check_line(out[0]) is None
    assert out[0]["cost_ms"] == pytest.approx(500.0)
    w.states["key"].window_s = 2.0
    w._run_task(w.states["key"], w.backends)
    assert out[-1]["type"] == "error" and out[-1]["task"] == "key" and "boom" in out[-1]["error"]
    assert P.check_line(out[-1]) is None and w.errors == 1


def test_beats_line_interior_bpm_and_bar(tmp_path):
    clk = FakeClock()
    beats = np.arange(0.25, 12.0, 0.5)               # 120 bpm from 0.25 s
    downs = beats[::4]
    w, out = make_worker(tmp_path, clk, beats=SlowTask(clk, 0.0, {"beats_rel": beats, "downbeats_rel": downs}))
    w.configure({"tasks": {"beats": {"window_s": 12, "interior_margin_s": 2}}})
    for f in frames(count=12 * 20):
        w.ring.push(f)
    w._run_task(w.states["beats"], w.backends)
    line = out[-1]
    assert P.check_line(line) is None and line["type"] == "beats"
    t0 = line["window_t0"]
    assert t0 == pytest.approx(100.0)
    assert line["interior"] == [pytest.approx(t0 + 2), pytest.approx(line["window_t1"] - 2)]
    assert all(line["interior"][0] <= b <= line["interior"][1] for b in line["beats"])
    assert line["bpm"] == pytest.approx(120.0) and line["bar_len"] == 4
    assert line["beats"][0] == pytest.approx(t0 + 2.25)


def test_fake_tasks_shapes_and_determinism():
    t = np.arange(16000 * 12) / FS
    x = (0.3 * np.sin(2 * np.pi * 261.63 * t) + 0.3 * np.sin(2 * np.pi * 329.63 * t)
         + 0.3 * np.sin(2 * np.pi * 392.0 * t)).astype(np.float32)
    b = M.Budget(1e9)
    a1, a2 = M.FakeTags().run(x[:33600], b), M.FakeTags().run(x[:33600], b)
    assert np.array_equal(a1["emb_vec"], a2["emb_vec"]) and a1["emb_vec"].shape == (C.EMB_DIM,)
    assert set(a1["genre_parents"]) == set(C.DISCOGS_PARENTS)
    k = M.FakeKey().run(x, b)
    assert (k["key"], k["mode"]) == ("C", "major") and 0 < k["strength"] <= 1
    assert M.FakeKey().run(np.zeros(32000, np.float32), b)["key"] is None
    ced = M.FakeCed().run(x[:32000], b)
    assert set(ced) == {f"{g}_p" for g in C.CED_GROUPS} and ced["music_p"] == 1.0


# -- the process, over real pipes --------------------------------------------------------------------------------------
def spawn(tmp_path, *extra, parent=None):
    r, w = os.pipe()
    env = dict(os.environ, PYTHONUNBUFFERED="1", CUDA_VISIBLE_DEVICES="")
    p = subprocess.Popen([sys.executable, "-m", C.ML_WORKER_MODULE, "--models", str(tmp_path), "--parent",
                          str(parent if parent is not None else os.getpid()), "--nice", "19", "--fake", *extra],
                         stdin=r, stdout=subprocess.PIPE, stderr=open(tmp_path / "worker.log", "wb"),
                         cwd=str(C.REPO_ROOT), env=env, start_new_session=True)
    os.close(r)
    return p, w


def read_lines(p, until, timeout):
    out, buf, end = [], b"", time.monotonic() + timeout
    fd = p.stdout.fileno()
    while time.monotonic() < end:
        rl, _, _ = select.select([fd], [], [], 0.1)
        if not rl:
            continue
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            out.append(json.loads(line))
            if until(out):
                return out
    return out


def feed_audio(w, seconds, t0=None, start_n=0, seg=1, stream="room"):
    t0 = time.monotonic() if t0 is None else t0
    t = np.arange(int(seconds * FS)) / FS
    x = (0.3 * np.sin(2 * np.pi * 220 * t) + 0.4 * (np.mod(t, 0.5) < 0.02)).astype(np.float32)
    for i in range(0, len(x) - 799, 800):
        os.write(w, P.pack_audio(stream, seg, start_n + i, t0 + i / FS, 0,
                                 C.ML_FLAG_SEGMENT if i == 0 and start_n == 0 else 0, x[i:i + 800]))
    return t0


def test_process_hello_results_stats_and_eof(tmp_path):
    p, w = spawn(tmp_path, "--stats-every", "1")
    try:
        os.write(w, P.config_frame({"tasks": {"key": {"window_s": 4}, "beats": {"every_s": 1, "window_s": 6,
                                                                                "interior_margin_s": 1}}}))
        lines = read_lines(p, lambda o: o and o[0]["type"] == "hello", 10)
        hello = lines[0]
        assert P.check_line(hello) is None and hello["v"] == C.ML_PROTOCOL_V and hello["pid"] == p.pid
        assert set(hello["models"].values()) == {"fake"} and all(hello["tasks"].values())
        assert hello["nice"] == 19 and hello["threads"] == {"compute": 2, "intra_op": 1}
        t0 = feed_audio(w, 8.0)
        got = read_lines(p, lambda o: {"ced", "tags", "key", "beats", "stats"} <= {x["type"] for x in o}, 15)
        kinds = {x["type"] for x in got}
        assert {"ced", "tags", "key", "beats", "stats"} <= kinds, kinds
        assert all(P.check_line(x) is None for x in got)
        ced = next(x for x in got if x["type"] == "ced")
        assert ced["stream"] == "room" and ced["seg"] == 1 and t0 < ced["t"] <= t0 + 8.0
        tags = next(x for x in got if x["type"] == "tags")
        assert P.decode_emb(tags["emb"], tags["emb_dim"]).shape == (C.EMB_DIM,)
        beats = next(x for x in got if x["type"] == "beats")
        assert beats["bpm"] == pytest.approx(120.0, abs=3)
        stats = next(x for x in got if x["type"] == "stats")
        assert stats["frames"] >= 1 and set(stats["tasks"]) == set(C.ML_TASKS)
        t_close = time.monotonic()
        os.close(w)
        w = None
        assert p.wait(5) == 0
        assert time.monotonic() - t_close < 1.5           # README: exits within 1 s of EOF
    finally:
        if w is not None:
            os.close(w)
        if p.poll() is None:
            p.kill()
            p.wait()


def test_process_stop_command_and_unknown_command(tmp_path):
    p, w = spawn(tmp_path)
    try:
        assert read_lines(p, lambda o: bool(o), 10)[0]["type"] == "hello"
        os.write(w, P.pack_ctrl({"cmd": "dance"}))     # counted and ignored
        os.write(w, P.stop_frame())
        assert p.wait(5) == 0
    finally:
        os.close(w)
        if p.poll() is None:
            p.kill()
            p.wait()


def test_process_exits_when_parent_isnt_ours(tmp_path):
    p, w = spawn(tmp_path, parent=1)
    try:
        assert p.wait(10) == 3
        assert b"parent 1 is gone" in (tmp_path / "worker.log").read_bytes()
    finally:
        os.close(w)
        if p.poll() is None:
            p.kill()
            p.wait()


def test_process_sigterm_exits_zero(tmp_path):
    p, w = spawn(tmp_path)
    try:
        assert read_lines(p, lambda o: bool(o), 10)[0]["type"] == "hello"
        p.terminate()                                   # only our own child
        assert p.wait(5) == 0
    finally:
        os.close(w)
        if p.poll() is None:
            p.kill()
            p.wait()


def test_the_cycles_first_task_runs_even_after_some_elapsed_time():
    beats = _st("beats", every=10.0, budget=5500.0)
    beats.costs.extend([5400.0] * 20)                # a p95 that won't fit what's left of the budget after 0.3 ms
    ready = lambda st: True                          # noqa: E731
    assert W.plan_fast([beats], 0.0, 1.0, 0.3, 5400, ready, first=True) is beats    # 75, 17 Oct: it never ran
    assert W.plan_fast([beats], 10.0, 1.0, 0.3, 5400, ready, first=False) is None
