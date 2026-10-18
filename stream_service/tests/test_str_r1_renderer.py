"""STR-R1 renderer core (docs/perf/PROGRAMME.md W0.1, W0.2, W1.1, W1.3, W1.7, W1.9): cached single-sort ring
summaries, window_stats over rendered slots, the probe frame after 3 repeats, the per-frame CPU / run-queue /
blocked split and its cost, thread names, stream priorities (NumPy fakes, no GPU)."""
from __future__ import annotations

import logging
import sys
import threading
import time

import numpy as np
import pytest

from stream_service import gpu as gpu_mod
from stream_service import stats as stats_mod
from stream_service.layers import FrameCtx, SourceLayer
from stream_service.profiles import PROFILES
from stream_service.renderer import MAX_REPEAT_RUN, Engine, EngineListener, _Rec, pace_clock, slot_action
from stream_service.stats import Ring, ThreadMeter, pct, set_thread_name, split_ms


# -- W1.1: one sort per call, cached 1 s -------------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_ring_summary_sorts_once_and_caches(monkeypatch):
    vals = [float((i * 37) % 101) for i in range(4096)]
    want = {"p50": round(pct(vals, 50), 3), "p95": round(pct(vals, 95), 3), "max": 100.0, "n": 4096}
    sorts = []
    real = sorted
    monkeypatch.setattr(stats_mod, "sorted", lambda v, **kw: sorts.append(1) or real(v, **kw), raising=False)
    clk = Clock()
    ring = Ring(4096, clock=clk)
    for v in vals:
        ring.add(v)
    s = ring.summary()
    assert len(sorts) == 1
    assert s == want
    ring.add(1000.0)
    clk.t += 0.5
    assert ring.summary() == s and len(sorts) == 1            # cached: no sort
    s["p50"] = -1                                             # callers get a copy
    assert ring.summary()["p50"] != -1
    clk.t += 0.6
    assert ring.summary()["max"] == 1000.0 and len(sorts) == 2


def test_ring_summary_empty():
    assert Ring(8).summary() == {"p50": None, "p95": None, "max": None, "n": 0}


# -- W0.1: window_stats over rendered rows; host_ms None on repeats ----------------------------------------------
def make_engine():
    return Engine(profile=PROFILES["h264-720p30"], gpu=0, post=lambda fn, *a: None, listener=EngineListener())


def test_window_stats_counts_rendered_rows_only():
    eng = make_engine()
    now = pace_clock()
    for n in range(30):
        if n % 3 == 2:
            rec = _Rec(n, True, 40.0, t_start=now - 0.5 + n * 0.01)
            assert rec.host_ms is None
        else:
            rec = _Rec(n, False, 1.0, 20.0, t_start=now - 0.5 + n * 0.01, render_ms=5.0, cpu_ms=12.0, runq_ms=1.0,
                       blocked_ms=7.0, div=1, held=n % 2 == 1)
        eng._keep(rec)
    ws = eng.window_stats(2.0)
    assert ws["frames"] == 30 and ws["rendered"] == 20 and ws["repeats"] == 10
    assert ws["host_ms"] == {"p50": 20.0, "p95": 20.0, "max": 20.0}       # repeats never read as 0
    assert ws["render_ms"]["p50"] == 5.0 and ws["cpu_ms"]["p50"] == 12.0 and ws["blocked_ms"]["max"] == 7.0
    assert ws["slip_ms"]["max"] == 40.0                                    # slip: every slot
    drawn = sum(1 for n in range(30) if n % 3 != 2 and n % 2 == 0)
    assert ws["content_fps"] == round(drawn / 2.0, 1)


def test_summary_carries_the_split_and_content_fps():
    eng = make_engine()
    s = eng.summary()
    for k in ("cpu_ms", "runq_ms", "blocked_ms"):
        assert set(s[k]) == {"p50", "p95", "max", "n"}
    assert s["content_fps"] is None and s["probe_frames"] == 0 and s["content_frames"] == 0
    for k in ("frames", "rendered", "repeats", "render_ms", "host_ms", "slip_ms", "gpu_guard", "bitrate_mbps"):
        assert k in s                                                     # shape unchanged


# -- W1.7: a probe frame after 3 repeats ------------------------------------------------------------------------
def run_slots(slots, *, cost=40.0, period=33.3, behind=False):
    run, acts = 0, []
    for late in slots:
        a = slot_action(late, True, cost, period, lambda: behind, run)
        run = run + 1 if a in ("repeat", "backlog") else 0
        acts.append(a)
    return acts


def test_probe_frame_after_three_repeats():
    acts = run_slots([True] * 12)
    assert acts == (["repeat"] * MAX_REPEAT_RUN + ["probe"]) * 3
    assert run_slots([False] * 8, behind=True) == (["backlog"] * 3 + ["probe"]) * 2
    longest = cur = 0
    for a in run_slots([True] * 200):
        cur = cur + 1 if a != "probe" and a != "render" else 0
        longest = max(longest, cur)
    assert longest == MAX_REPEAT_RUN


def test_slot_action_keeps_the_repeat_rule():
    calls = []

    def behind():
        calls.append(1)
        return False

    assert slot_action(True, False, 99.0, 33.3, behind, 0) == "render"          # nothing to repeat yet
    assert slot_action(True, True, 99.0, 33.3, behind, 0) == "repeat" and not calls   # backlog not consulted
    assert slot_action(True, True, 10.0, 33.3, behind, 0) == "render" and calls      # late, not render-bound
    assert slot_action(False, True, 99.0, 33.3, lambda: True, 2) == "backlog"
    assert slot_action(False, True, 99.0, 33.3, lambda: True, 3) == "probe"


# -- W0.2: cpu / runq / blocked ---------------------------------------------------------------------------------
def test_split_blocked_never_negative():
    cpu, runq, blocked = split_ms(10.0, (1.000, 5_000_000), (1.006, 6_000_000))
    assert cpu == pytest.approx(6.0) and runq == pytest.approx(1.0) and blocked == pytest.approx(3.0)
    assert split_ms(5.0, (1.0, 0), (1.008, 1_000_000))[2] == 0.0              # clock granularity: clamped
    cpu, runq, blocked = split_ms(10.0, (1.0, None), (1.004, None))
    assert runq is None and blocked == pytest.approx(6.0)


def test_thread_meter_reads_a_kept_open_fd(tmp_path):
    f = tmp_path / "schedstat"
    f.write_text("123 4567 8\n")
    m = ThreadMeter(str(f))
    assert m.runq_ns() == 4567
    f.write_text("123 9999 9\n")
    assert m.runq_ns() == 9999                                                 # re-read per call
    m.close()
    assert m.runq_ns() is None
    assert ThreadMeter(str(tmp_path / "missing")).sample()[1] is None
    f.write_text("garbage")
    assert ThreadMeter(str(f)).runq_ns() is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="schedstat")
def test_thread_meter_measures_its_own_thread():
    out = {}

    def work():
        m = ThreadMeter()
        s0 = m.sample()
        x = 0
        t_end = time.perf_counter() + 0.02
        while time.perf_counter() < t_end:
            x += 1
        s1 = m.sample()
        out["split"] = split_ms(20.0, s0, s1)
        m.close()

    t = threading.Thread(target=work)
    t.start()
    t.join()
    cpu, runq, blocked = out["split"]
    assert cpu > 5.0 and blocked >= 0.0
    if runq is not None:
        assert runq >= 0.0


def test_self_meter_cost_per_frame():
    """What _render_frame adds per frame (W0.2): two samples, the split, three ring adds, the scene counters."""
    m = ThreadMeter()
    rings = [Ring(4096) for _ in range(3)]
    fc = FrameCtx(0, 30, None)
    rec = _Rec(0, False, 0.0, 10.0)
    best = None
    for _ in range(5):
        k = 2000
        t0 = time.perf_counter()
        for _ in range(k):
            fc.drawn = fc.held = 0
            fc.div = 1
            s0 = m.sample()
            held = fc.held > 0 and fc.drawn == 0
            rec.cpu_ms, rec.runq_ms, rec.blocked_ms = split_ms(10.0, s0, m.sample())
            rings[0].add(rec.cpu_ms)
            rings[1].add(rec.blocked_ms)
            if rec.runq_ms is not None:
                rings[2].add(rec.runq_ms)
            rec.held = held
        per = (time.perf_counter() - t0) / k * 1000.0
        best = per if best is None else min(best, per)
    m.close()
    print(f"W0.2 self-meter: {best * 1000:.2f} us per frame")
    assert best <= 0.05


# -- layers: held / drawn per frame, the rate log names the session ---------------------------------------------
class Pool:
    def acquire(self):
        return np.zeros((1, 1, 4), np.float64)

    def release(self, buf):
        pass


class Const:
    name = "const"

    def render(self, t_local, dt, out):
        out[...] = 1.0


def test_source_layer_reports_drawn_and_held():
    layer = SourceLayer(Const(), 0.0)
    layer.rate_div = 2
    fc = FrameCtx(0, 30, Pool())
    out = np.zeros((1, 1, 4))
    seen = []
    for n in range(4):
        fc.n, fc.drawn, fc.held, fc.div = n, 0, 0, 1
        layer.render(fc, out)
        seen.append((fc.drawn, fc.held, fc.div))
    assert seen == [(1, 0, 2), (0, 1, 2), (1, 0, 2), (0, 1, 2)]


def test_rate_log_names_the_session(caplog):
    layer = SourceLayer(Const(), 0.0)
    layer.cost_ema = 50.0
    with caplog.at_level(logging.INFO, logger="stream_service.layers"):
        layer._set_rate(2, FrameCtx(0, 30, Pool(), name="s-1a2b3c4d"))
    assert "[s-1a2b3c4d] scene const" in caplog.text


# -- W0.1 thread names ------------------------------------------------------------------------------------------
def test_thread_names_never_raise(monkeypatch):
    out = {}

    def work():
        set_thread_name("render-1a2b3c4d-and-much-longer-than-15")
        set_thread_name("ünïcode-név")
        try:
            out["comm"] = open("/proc/thread-self/comm").read().strip()
        except OSError:
            out["comm"] = None
        set_thread_name("render-1a2b3c4d")
        try:
            out["comm2"] = open("/proc/thread-self/comm").read().strip()
        except OSError:
            out["comm2"] = None

    t = threading.Thread(target=work)
    t.start()
    t.join()
    if sys.platform.startswith("linux") and out["comm2"] is not None:
        assert out["comm2"] == "render-1a2b3c4d"
    monkeypatch.setattr(stats_mod, "_libc", object())                      # no prctl: still no raise
    set_thread_name("x")
    monkeypatch.setattr(stats_mod.sys, "platform", "win32")
    set_thread_name("x")


def test_engine_tag_fits_thread_names():
    eng = Engine(profile=PROFILES["h264-720p30"], gpu=0, post=lambda fn, *a: None, listener=EngineListener(),
                 name="s-1a2b3c4d")
    for role in ("render", "writer", "closer", "encerr"):
        assert len(f"{role}-{eng._tag()}".encode()) <= 15


# -- W1.9 stream priorities --------------------------------------------------------------------------------------
class FakeCuda:
    def __init__(self, ok=True):
        self.ok, self.made = ok, []
        outer = self

        class Stream:
            def __init__(self, non_blocking=False, priority=None):
                if priority is not None and not outer.ok:
                    raise TypeError("priority")
                self.priority, self.ptr = priority, 7
                outer.made.append(self)

        class runtime:
            @staticmethod
            def streamGetPriority(ptr):
                return -5

        self.Stream, self.runtime = Stream, runtime


class FakeCp:
    def __init__(self, ok=True):
        self.cuda = FakeCuda(ok)


def test_priority_stream_and_fallback():
    cp = FakeCp()
    s = gpu_mod.priority_stream(cp, gpu_mod.RENDER_PRIORITY)
    assert s.priority == -5 and gpu_mod.stream_priority(cp, s) == -5
    assert gpu_mod.priority_stream(cp, gpu_mod.LOW_PRIORITY).priority == 0
    cp = FakeCp(ok=False)
    assert gpu_mod.priority_stream(cp, -5).priority is None                  # degrade: a plain stream
