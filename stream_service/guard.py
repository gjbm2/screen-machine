"""GPU frame-time guard: every plug-in call is timed on the GPU, and a plug-in that stays over budget is tripped.

The layer tree brackets each plug-in render (a scene source, a transition, each effect) with two CUDA events on
the render stream (``FrameCtx.timer``: :meth:`GpuTimer.begin` / :meth:`GpuTimer.end`); the engine closes each
rendered frame with one more event.  At the start of the next frame (and while the render thread waits for a slot)
:meth:`GpuTimer.poll` reads the events of frames the GPU has finished, oldest first, with ``cudaEventQuery`` only:
it never blocks the host.  A frame still running on the GPU is looked into: the plug-in whose start event has
passed but whose end has not is "in progress", and how long it has been seen so is a lower bound on its cost.

No gates (owner, 18 Oct 2026: "rip all your gates out"; graceful degradation): a slow plug-in keeps playing.
A scene over its frame time drops its frame rate (layers.py ``rate_div``), the engine repeats frames while the GPU
is behind (below), and the cost is reported (``stats()``: the worst call, how many samples were over the budget,
the details panel and the log).  The one stop (:class:`TripMonitor`) is a hang:
  * ``stuck``: a call still running on the GPU ``HANG_MS`` (3 s) after it was seen to start;
  * ``frame``: a call that took ``HANG_MS`` or more (read after it finished).
A stop calls the owner's ``trip(fc, SlowPlugin)``, which substitutes through the failure paths the layers already
have (layers.py, effects.py): a scene shows black and the session cues a recovery, a transition finishes as a cut,
an effect is dropped.  Internal plug-ins (the anchor / exit stills) are never timed.

The engine also uses the timer as back-pressure: while the GPU is ``MAX_GPU_BACKLOG`` rendered frames behind the
host (1 while a newly shown plug-in has not been measured yet), it repeats the previous frame instead of queueing
more GPU work, so a pathological plug-in costs at most a frame or two of queued work, not the whole slot ring.

GPU-agnostic: the events come from a backend (:class:`CudaEvents` on the GPU; the tests use a fake one with a fake
clock).  Everything runs on the render thread.
"""
from __future__ import annotations

import collections
import logging
import math
import time
from typing import Any, Callable, Optional

log = logging.getLogger("stream_service.guard")

TRIP_BUDGET_X = 1.5          # the budget reported against: 1.5 x the frame period (nothing stops on it)
HANG_MS = 3000.0             # a call on the GPU this long is a hang, not a slow frame: the one stop
GPU_BOUND_MARGIN_MS = 1.0    # a span this much longer than the host's submit of the same call is real GPU work
MAX_GPU_BACKLOG = 2          # rendered frames the GPU may lag the host before the engine repeats instead
TOTAL_EMA = 0.3
WHY_TEXT = {"frame": "a single frame", "stuck": "still running"}


def trip_budget_ms(fps: float) -> float:
    return TRIP_BUDGET_X * 1000.0 / fps


class SlowPlugin(RuntimeError):
    """A plug-in tripped the GPU frame-time guard (the 'exception' its owner's failure path reports)."""

    def __init__(self, kind: str, name: str, ms: float, budget_ms: float, why: str):
        self.kind, self.name, self.ms, self.budget_ms, self.why = kind, name, float(ms), float(budget_ms), why
        super().__init__(f"{name} took {ms:.0f} ms of GPU time per frame ({WHY_TEXT.get(why, why)}; "
                         f"budget {budget_ms:.0f} ms)")


class Stat:
    """Per call-site timing state, kept on the owner (``owner.gpu_stat``)."""
    __slots__ = ("samples", "last_ms", "n_over", "sum_over", "tripped", "probation", "cpu_ms", "gpu_bound_ms")

    def __init__(self) -> None:
        self.samples = 0
        self.last_ms = 0.0              # the GPU span (event to event): includes the host's stalls between launches
        self.cpu_ms = 0.0               # that call's own CPU on the host (thread time: no lock or run-queue waits)
        self.gpu_bound_ms = 0.0         # the span when it outlasted the host's submit (real GPU work), else 0
        self.n_over = 0
        self.sum_over = 0.0
        self.tripped = False
        self.probation = True


class TripMonitor:
    def __init__(self, fps: float):
        self.period_ms = 1000.0 / fps
        self.budget_ms = trip_budget_ms(fps)
        self.frame_ms = HANG_MS

    def feed(self, st: Stat, ms: float, scale: float = 1.0) -> Optional[str]:
        """One measured sample -> "frame" for a hang, else None (over the budget is counted, never stopped).
        ``scale``: the call site's frames per render."""
        st.samples += 1
        st.last_ms = ms
        if st.tripped:
            return None
        if ms >= self.frame_ms:
            return "frame"
        if ms > self.budget_ms * scale:
            st.n_over += 1
            st.sum_over += ms
        return None

    def stuck(self, st: Stat, running_ms: float) -> bool:
        return not st.tripped and running_ms >= self.frame_ms


class CudaEvents:
    """The GPU backend: timing events recorded on the render stream; ``done`` is a non-blocking query."""

    def __init__(self, cp: Any, stream: Any):
        self.cp, self.stream = cp, stream

    def new(self) -> Any:
        return self.cp.cuda.Event()

    def record(self, ev: Any) -> None:
        ev.record(self.stream)

    def done(self, ev: Any) -> bool:
        return ev.done

    def elapsed_ms(self, a: Any, b: Any) -> float:
        return self.cp.cuda.get_elapsed_time(a, b)


class _Rec:
    __slots__ = ("owner", "kind", "obj", "ev0", "ev1", "seen0", "cpu0", "wall0", "cpu_ms", "wall_ms")

    def __init__(self, owner: Any, kind: str, obj: Any, ev0: Any):
        self.owner, self.kind, self.obj, self.ev0 = owner, kind, obj, ev0
        self.ev1: Any = None
        self.seen0: Optional[float] = None
        self.cpu0, self.wall0 = time.thread_time(), time.perf_counter()
        self.cpu_ms = self.wall_ms = 0.0


class _Frame:
    __slots__ = ("recs", "end", "i", "total")

    def __init__(self) -> None:
        self.recs: list[_Rec] = []
        self.end: Any = None
        self.i = 0
        self.total = 0.0


def plugin_name(kind: str, owner: Any, obj: Any) -> str:
    if kind == "effect":
        return str(getattr(owner, "name", None) or getattr(obj, "name", None) or type(obj).__name__)
    return str(getattr(obj, "name", None) or type(obj).__name__)


class GpuTimer:
    def __init__(self, backend: Any, fps: float, clock: Callable[[], float] = time.monotonic):
        self.be = backend
        self.mon = TripMonitor(fps)
        self.clock = clock
        self.fifo: collections.deque[_Frame] = collections.deque()
        self.cur: Optional[_Frame] = None
        self._free: list = []
        self.probation = 0              # call sites shown but not measured yet
        self.samples = 0
        self.trips = 0
        self.total_ema: Optional[float] = None
        self.last_trip: Optional[dict] = None
        self.over = 0                   # samples over the budget (reported; nothing stops on them)
        self.worst: Optional[dict] = None   # the slowest call so far {kind, name, ms}

    def set_fps(self, fps: float) -> None:
        """The output frame rate changed (the degradation controller's fps step): budgets follow the new period."""
        self.mon.period_ms = 1000.0 / fps
        self.mon.budget_ms = trip_budget_ms(fps)

    # -- recording (render thread, inside a frame) ----------------------------------
    def _ev(self) -> Any:
        return self._free.pop() if self._free else self.be.new()

    def frame_begin(self) -> None:
        if self.cur is not None:        # a frame that raised half way: its records are dropped
            self._drop(self.cur)
        self.cur = _Frame()

    def begin(self, owner: Any, kind: str, obj: Any) -> Optional[_Rec]:
        fr = self.cur
        if fr is None or getattr(obj, "internal", False):
            return None
        st = getattr(owner, "gpu_stat", None)
        if st is None:
            st = owner.gpu_stat = Stat()
            self.probation += 1
        if st.tripped:
            return None
        ev = self._ev()
        self.be.record(ev)
        rec = _Rec(owner, kind, obj, ev)
        fr.recs.append(rec)
        return rec

    def end(self, rec: Optional[_Rec]) -> None:
        if rec is None:
            return
        ev = self._ev()
        self.be.record(ev)
        rec.ev1 = ev
        rec.cpu_ms = (time.thread_time() - rec.cpu0) * 1000.0      # host: own CPU, and wall for the submit
        rec.wall_ms = (time.perf_counter() - rec.wall0) * 1000.0

    def frame_end(self) -> None:
        fr, self.cur = self.cur, None
        if fr is None:
            return
        fr.recs = [r for r in fr.recs if r.ev1 is not None]
        fr.end = self._ev()
        self.be.record(fr.end)
        self.fifo.append(fr)

    # -- reading (render thread, between frames) --------------------------------------
    @property
    def pending(self) -> int:
        """Rendered frames the GPU has not finished (as of the last poll)."""
        return len(self.fifo)

    def backlog_limit(self) -> int:
        return 1 if self.probation > 0 else MAX_GPU_BACKLOG

    def poll(self, fc: Any) -> None:
        """Read every finished frame's timings; trip what is over budget.  Never blocks."""
        be = self.be
        while self.fifo:
            fr = self.fifo[0]
            if be.done(fr.end):
                while fr.i < len(fr.recs):
                    self._sample(fc, fr, fr.recs[fr.i])
                    fr.i += 1
                self.total_ema = fr.total if self.total_ema is None else \
                    self.total_ema + TOTAL_EMA * (fr.total - self.total_ema)
                self.fifo.popleft()
                self._free.append(fr.end)
                continue
            now = self.clock()
            while fr.i < len(fr.recs):            # the frame is still on the GPU: what has finished, what runs
                rec = fr.recs[fr.i]
                if be.done(rec.ev1):
                    self._sample(fc, fr, rec)
                    fr.i += 1
                    continue
                if be.done(rec.ev0):
                    if rec.seen0 is None:
                        rec.seen0 = now
                    else:
                        running = (now - rec.seen0) * 1000.0
                        st = rec.owner.gpu_stat
                        if self.mon.stuck(st, running):
                            self._trip(fc, rec, running, "stuck")
                break
            return

    def _sample(self, fc: Any, fr: _Frame, rec: _Rec) -> None:
        ms = float(self.be.elapsed_ms(rec.ev0, rec.ev1))
        self._free.append(rec.ev0)
        self._free.append(rec.ev1)
        rec.ev0 = rec.ev1 = None
        fr.total += ms
        self.samples += 1
        st = rec.owner.gpu_stat
        if st.probation:
            st.probation = False
            self.probation -= 1
        st.cpu_ms = rec.cpu_ms
        st.gpu_bound_ms = ms if ms > rec.wall_ms + GPU_BOUND_MARGIN_MS else 0.0   # else the GPU waited on the host
        n_over = st.n_over
        why = self.mon.feed(st, ms, getattr(rec.owner, "rate_div", 1))
        if st.n_over > n_over:
            self.over += 1
        if self.worst is None or ms > self.worst["ms"]:
            self.worst = {"kind": rec.kind, "name": plugin_name(rec.kind, rec.owner, rec.obj), "ms": round(ms, 1)}
        if why is not None:
            self._trip(fc, rec, ms, why)

    def _trip(self, fc: Any, rec: _Rec, ms: float, why: str) -> None:
        st = rec.owner.gpu_stat
        if st.tripped:
            return
        st.tripped = True
        name = plugin_name(rec.kind, rec.owner, rec.obj)
        self.trips += 1
        self.last_trip = {"kind": rec.kind, "name": name, "ms": round(ms, 1), "why": why}
        exc = SlowPlugin(rec.kind, name, ms, self.mon.budget_ms * getattr(rec.owner, "rate_div", 1), why)
        log.warning("GPU guard: %s %s stopped (a hang): %s", rec.kind, name, exc)
        try:
            rec.owner.trip(fc, exc)
        except Exception:              # the guard must never take a frame down
            log.exception("GPU guard: substituting %s %s failed", rec.kind, name)

    def _drop(self, fr: _Frame) -> None:
        for rec in fr.recs:              # never measured: no longer on probation either
            st = rec.owner.gpu_stat
            if st.probation:
                st.probation = False
                self.probation -= 1
        fr.recs = []

    # -- reporting ---------------------------------------------------------------------
    def over_budget(self) -> bool:
        """The GPU is behind / stuck, or the tree's GPU spans are over a frame period while the GPU really lags (a
        frame still on it).  Spans alone overstate: they include the host's own stalls (lock waits) between launches,
        while the GPU sits idle (docs/perf/DIAGNOSIS_2026-10-18.md §3)."""
        if len(self.fifo) >= MAX_GPU_BACKLOG:
            return True
        if self.total_ema is not None and self.total_ema > self.mon.period_ms and self.fifo:
            return True
        if self.fifo:
            fr = self.fifo[0]
            if fr.i < len(fr.recs) and fr.recs[fr.i].seen0 is not None:
                return (self.clock() - fr.recs[fr.i].seen0) * 1000.0 > self.mon.period_ms
        return False

    def stats(self) -> dict:
        return {"samples": self.samples, "trips": self.trips, "backlog": len(self.fifo),
                "frame_gpu_ms": None if self.total_ema is None else round(self.total_ema, 2),
                "budget_ms": round(self.mon.budget_ms, 2), "last_trip": self.last_trip, "over_budget": self.over,
                "worst": self.worst}
