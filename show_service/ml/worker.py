"""The ML worker: ``python -m show_service.ml.worker --models D --parent PID --nice N [--fake]`` (README S4.3, S4.4).

A child of show_service (mllink spawns it from ~/.venvs/sm-show-ml in its own session). It reads 16 kHz mono audio
frames on stdin and writes one JSON line per result on stdout: hello, ced, tags, key, beats, stats, error
(contract's ``Ml*`` shapes, exactly). It never sees our clock: times are the acoustic host seconds the frames carry.

**Start.** Niceness to ``--nice`` (before any thread, so every thread inherits it); fd 1 dup'ed for the protocol and
pointed at stderr (a library's stray print can't corrupt a line); ``PR_SET_PDEATHSIG`` = SIGTERM, and exit when
``getppid()`` isn't ``--parent``; the stdin reader; up to 0.5 s for the config frame (show_service sends it right after
the spawn); the models (lazy imports); ``hello``. Then the two compute lanes start.

**Threads.**
- ``ml-reader``: stdin frames -> the ring (I/O only). EOF or ``{"cmd": "stop"}``: exit 0 within 1 s.
- ``ml-fast``: ced -> tags -> key on the fast lane's grid (``ml.fast_lane.every_s``), the cycle's tasks sharing
  ``ml.fast_lane.budget_ms``: a task whose p95 cost doesn't fit what is left is skipped (``skipped``).
- ``ml-beats``: beats on its own grid (``tasks.beats.every_s``).
- ``ml-watchdog``: sets a run's onnxruntime ``RunOptions.terminate`` at its ``budget_ms``; the result is dropped
  (``over_budget``) and the task's next turn is skipped (no retry at full cost). Three over-budget runs in a row
  double the task's every_s (up to x8); ten good runs halve it.
- the main thread: the CPU guard (each second), ``stats`` (every 10 s), and the lane watchdog: a lane with no cycle
  in max(3 x its period, its period + its budget + 5 s) is wedged in a call that never returns, and the worker exits
  70 so show_service restarts it (the reader and the stats would otherwise keep it looking alive).

**A grid, not a queue.** A lane wakes on its grid (every_s x stretch); a task runs when its own interval (every_s x
its doubling x stretch) has passed since its last turn, it has new audio, and its whole window is in the segment. A
cycle that overruns starts again at the next grid point after it: never back to back, never catching up.

**The CPU guard.** The worker's CPU (every thread) over wall time: above ``ml.cpu_max`` over 30 s multiplies every
interval by 1.5 (``stretch``, up to 4); under 0.7 x cpu_max over 60 s divides it by 1.5 (down to 1). At most one step
per window.

**Silence.** A window under ``ml.silence_dbfs`` (RMS, dBFS) runs no model: ced reports ``silence_p`` 1, the rest 0,
``skipped: "silence"``; tags, key and beats skip (counted as ``silent``).

**The ring** keeps the current segment's last ``max(window) + 2 s``. A frame with ML_FLAG_SEGMENT, another stream or
seg, ``n0`` going backwards, or a hole over 1 s starts a new segment; a hole up to 1 s is zero-filled and counted
(``holes``). A beats window never contains a hole or a GAP frame (it waits for clean audio).

``--bench FILE`` (not production) decodes FILE with ffmpeg and prints each task's cost per call, niced like the worker.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import copy
import ctypes
import heapq
import json
import math
import os
import pathlib
import signal
import sys
import threading
import time
from typing import Any, Callable, Optional

import numpy as np

from show_service import contract as C
from show_service.ml import models as M
from show_service.ml import protocol as P

FS = C.ML_FS

# The starting points (README S4.9; the same numbers as show.yaml's ``ml`` section, marked there): used until the
# config frame arrives, and for any key it leaves out or gets wrong.
DEFAULTS: dict = {
    "enabled": True,
    "nice": 19,                                   # owner: the host renders 4K (two compute threads: the lanes below)
    "cpu_max": 0.35,                              # starting point
    "silence_dbfs": -70,                          # starting point
    "results_stale_s": 3,                         # starting point (mllink's)
    "fast_lane": {"every_s": 1, "budget_ms": 250},            # §6 / starting point
    "tasks": {
        "ced": {"every_s": 1, "window_s": 2.0, "model": "ced-mini", "budget_ms": 100},
        "tags": {"every_s": 1, "window_s": 2.1, "model": "discogs-effnet", "budget_ms": 80,
                 "heads": {"genre": "genre_discogs400", "moods": "mtg_jamendo_moodtheme",
                           "instruments": "mtg_jamendo_instrument", "dance": "danceability",
                           "voice_p": "voice_instrumental", "engagement": "engagement_regression",
                           "bright": "timbre", "acoustic": "mood_acoustic"}},     # arousal (deam): off, show.yaml
        "key": {"every_s": 2, "window_s": 20, "profile": "bgate", "budget_ms": 60},
        "beats": {"every_s": 10, "window_s": 30, "interior_margin_s": 3, "model": "beat-this-small0",
                  "budget_ms": 5500},
    },
    "ced_groups": {
        "music": ["Music"],
        "speech": ["Speech", "Male speech, man speaking", "Female speech, woman speaking",
                   "Child speech, kid speaking", "Narration, monologue"],
        "conversation": ["Conversation"],
        "singing": ["Singing", "Choir"],
        "crowd": ["Crowd", "Chatter", "Hubbub, speech noise, speech babble"],
        "applause": ["Applause", "Clapping", "Cheering"],
        "silence": ["Silence"],
    },
}
FAST_ORDER = ("ced", "tags", "key")               # README S4.3: the fast lane's order
STATS_EVERY_S = 10.0
HOLE_FILL_MAX_S = 1.0
RING_MARGIN_S = 2.0
OVER_ROW_DOUBLE, GOOD_ROW_HALVE, FACTOR_MAX = 3, 10, 8
STRETCH_UP, STRETCH_MAX, UP_WINDOW_S, DOWN_WINDOW_S, DOWN_SHARE = 1.5, 4.0, 30.0, 60.0, 0.7
CONFIG_WAIT_S = 0.5
COST_KEEP = 64
ERROR_MAX = 300
LANE_STALL_PERIODS, LANE_STALL_SLACK_S = 3.0, 5.0  # a lane with no cycle in max(3 x its period, its period + its
                                                  # budget + 5 s) is wedged (a native call that never returns): exit
LANE_STALL_EXIT = 70                              # ...non-zero, so show_service restarts the worker (README S4.3)


def log(msg: str) -> None:
    try:
        sys.stderr.write(f"{time.strftime('%H:%M:%S')} ml-worker[{os.getpid()}] {msg}\n")
        sys.stderr.flush()
    except Exception:                                  # noqa: BLE001  the log is best effort
        pass


# -- config ------------------------------------------------------------------------------------------------------------
def merge_config(base: dict, over: Any, path: str = "", problems: Optional[list] = None) -> dict:
    """``over`` laid over ``base``: a key of the wrong type keeps base's value (and says so in ``problems``); dicts
    merge; ``heads`` and ``ced_groups`` are taken whole (they are maps, not settings)."""
    problems = problems if problems is not None else []
    out = copy.deepcopy(base)
    if not isinstance(over, dict):
        return out
    for k, v in over.items():
        p = f"{path}.{k}" if path else k
        if k not in base:
            out[k] = copy.deepcopy(v)               # e.g. python, models_dir: not the worker's, kept as given
            continue
        b = base[k]
        if k in ("heads", "ced_groups"):
            if isinstance(v, dict):
                out[k] = copy.deepcopy(v)
            else:
                problems.append(f"{p}: not a map")
        elif isinstance(b, dict):
            out[k] = merge_config(b, v, p, problems)
        elif isinstance(b, bool):
            if isinstance(v, bool):
                out[k] = v
            else:
                problems.append(f"{p}: not true/false")
        elif isinstance(b, (int, float)):
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and (v > 0 or b <= 0):
                out[k] = v
            else:
                problems.append(f"{p}: {v!r} is not a usable number")
        elif isinstance(b, str):
            if isinstance(v, str) and v:
                out[k] = v
            else:
                problems.append(f"{p}: not a name")
        else:
            out[k] = copy.deepcopy(v)
    return out


# -- the ring ----------------------------------------------------------------------------------------------------------
class Window:
    """One copy out of the ring: samples [n_start, n_end) of segment ``gen``."""
    __slots__ = ("pcm", "n_start", "n_end", "gen", "stream", "seg", "t_start", "t_end", "clean")

    def __init__(self, pcm, n_start, n_end, gen, stream, seg, t_start, t_end, clean):
        self.pcm, self.n_start, self.n_end, self.gen = pcm, n_start, n_end, gen
        self.stream, self.seg, self.t_start, self.t_end, self.clean = stream, seg, t_start, t_end, clean


class Ring:
    """The current segment's recent 16 kHz audio and its time map (sample i of a frame sounds at t0 + i / 16000)."""

    def __init__(self, capacity_s: float = 32.0) -> None:
        self._lock = threading.Lock()
        self.cap = int(capacity_s * FS)
        self.buf = np.zeros(self.cap, dtype=np.float32)
        self.gen = 0                    # +1 per ring segment (the worker's own count)
        self.stream: Optional[str] = None
        self.seg: Optional[int] = None
        self.n_end = 0                  # the next sample's index in the segment
        self.n_base = 0                 # the segment's first index held (n_end - held)
        self.anchors_n: list[int] = []
        self.anchors_t: list[float] = []
        self.bad_end = 0                # samples before this include a hole or a GAP frame
        self.frames = self.bytes = self.gaps = self.holes = self.drops_seen = self.segments = 0
        self.arrived = time.monotonic()

    def resize(self, capacity_s: float) -> None:
        with self._lock:
            cap = int(capacity_s * FS)
            if cap == self.cap:
                return
            keep = min(cap, self.n_end - self.n_base)
            data = self._read(self.n_end - keep, self.n_end) if keep else np.zeros(0, np.float32)
            self.cap, self.buf = cap, np.zeros(cap, dtype=np.float32)
            self.n_base = self.n_end - keep
            self._write(self.n_base, data)

    def _new_segment(self, stream: str, seg: int) -> None:
        self.gen += 1
        self.segments += 1
        self.stream, self.seg = stream, seg
        self.n_end = self.n_base = 0
        self.anchors_n, self.anchors_t = [], []
        self.bad_end = 0

    def _write(self, n: int, x: np.ndarray) -> None:
        if len(x) > self.cap:
            x, n = x[-self.cap:], n + len(x) - self.cap
        i = n % self.cap
        k = min(len(x), self.cap - i)
        self.buf[i:i + k] = x[:k]
        if k < len(x):
            self.buf[:len(x) - k] = x[k:]

    def _read(self, a: int, b: int) -> np.ndarray:
        i, n = a % self.cap, b - a
        if i + n <= self.cap:
            return self.buf[i:i + n].copy()
        return np.concatenate([self.buf[i:], self.buf[:n - (self.cap - i)]])

    def push(self, fr: P.Frame) -> None:
        x = fr.samples()
        with self._lock:
            self.frames += 1
            self.bytes += C.ML_HEADER_SIZE + len(fr.payload)
            self.drops_seen = fr.dropped
            self.arrived = time.monotonic()
            if (fr.flags & C.ML_FLAG_SEGMENT or fr.stream != self.stream or fr.seg != self.seg
                    or fr.n0 < self.n_end or fr.n0 - self.n_end > HOLE_FILL_MAX_S * FS):
                self._new_segment(fr.stream, fr.seg)
                self.n_end = self.n_base = fr.n0
            elif fr.n0 > self.n_end:                  # a short hole: zero-fill it
                self.holes += 1
                gap = fr.n0 - self.n_end
                self._write(self.n_end, np.zeros(gap, dtype=np.float32))
                self.n_end = fr.n0
                self.bad_end = fr.n0
            if fr.flags & C.ML_FLAG_GAP:
                self.gaps += 1
                self.bad_end = fr.n0 + len(x)
            self._write(self.n_end, x)
            self.n_end += len(x)
            self.n_base = max(self.n_base, self.n_end - self.cap)
            self.anchors_n.append(fr.n0)
            self.anchors_t.append(fr.t0)
            if len(self.anchors_n) > 4096:
                cut = bisect.bisect_left(self.anchors_n, self.n_base) - 1
                if cut > 0:
                    del self.anchors_n[:cut], self.anchors_t[:cut]

    def _time(self, n: int) -> float:
        i = bisect.bisect_right(self.anchors_n, n) - 1
        i = max(0, i)
        return self.anchors_t[i] + (n - self.anchors_n[i]) / FS

    def window(self, seconds: float) -> Optional[Window]:
        """The newest ``seconds`` of the segment, or None when it doesn't hold that much yet."""
        need = int(round(seconds * FS))
        with self._lock:
            if not self.anchors_n or self.n_end - self.n_base < need:
                return None
            a, b = self.n_end - need, self.n_end
            return Window(self._read(a, b), a, b, self.gen, self.stream or "", self.seg or 0, self._time(a),
                          self._time(b - 1), self.bad_end <= a)

    def key(self) -> tuple:
        """Changes whenever new audio arrives (a task with the same key since its last run has nothing new)."""
        with self._lock:
            return (self.gen, self.n_end)

    def held_s(self) -> float:
        with self._lock:
            return (self.n_end - self.n_base) / FS


# -- scheduling (pure; a fake clock in tests) --------------------------------------------------------------------------
class TaskState:
    def __init__(self, name: str) -> None:
        self.name = name
        self.every_s = 1.0
        self.window_s = 1.0
        self.budget_ms = 100.0
        self.factor = 1
        self.over_row = self.good_row = 0
        self.runs = self.skipped = self.over_budget = self.errors = self.silent = 0
        self.costs: collections.deque = collections.deque(maxlen=COST_KEEP)
        self.last_turn: Optional[float] = None
        self.last_key: Optional[tuple] = None
        self.skip_next = False                    # an overrun skips the task's next turn (no retry at full cost)

    def configure(self, tcfg: dict) -> None:
        self.every_s = float(tcfg.get("every_s", self.every_s))
        self.window_s = float(tcfg.get("window_s", self.window_s))
        self.budget_ms = float(tcfg.get("budget_ms", self.budget_ms))

    def interval(self, stretch: float) -> float:
        return self.every_s * self.factor * stretch

    def due(self, g: float, stretch: float) -> bool:
        return self.last_turn is None or g - self.last_turn >= self.interval(stretch) - 1e-6

    def p95(self) -> float:
        if not self.costs:
            return 0.0
        return float(np.percentile(np.fromiter(self.costs, float), 95))

    def ran(self, cost_ms: float, over: bool) -> None:
        if over:
            self.over_budget += 1
            self.over_row += 1
            self.good_row = 0
            self.skip_next = True
            if self.over_row >= OVER_ROW_DOUBLE:
                self.factor = min(FACTOR_MAX, self.factor * 2)
                self.over_row = 0
        else:
            self.runs += 1
            self.good_row += 1
            self.over_row = 0
            if self.good_row >= GOOD_ROW_HALVE and self.factor > 1:
                self.factor //= 2
                self.good_row = 0
        self.costs.append(cost_ms)

    def stats(self, stretch: float) -> dict:
        c = np.fromiter(self.costs, float) if self.costs else None
        return {"runs": self.runs, "skipped": self.skipped, "over_budget": self.over_budget, "errors": self.errors,
                "silent": self.silent,
                "cost_ms_p50": round(float(np.percentile(c, 50)), 2) if c is not None else None,
                "cost_ms_p95": round(float(np.percentile(c, 95)), 2) if c is not None else None,
                "every_s": round(self.interval(stretch), 3), "factor": self.factor}


class Grid:
    """A lane's wake-ups: g0 + k x period; after a cycle ends at ``now``, the next is the first point after it."""

    def __init__(self, start: float) -> None:
        self.next_t = start

    def after(self, now: float, period: float) -> float:
        if period <= 0:
            return now
        if self.next_t <= now:
            k = math.floor((now - self.next_t) / period) + 1
            self.next_t += k * period
        return self.next_t


class CpuGuard:
    """``stretch`` from the worker's CPU share (README S4.3, "The CPU guard"). ``sample`` once a second."""

    def __init__(self, cpu_max: float, clock_t0: float) -> None:
        self.cpu_max = cpu_max
        self.stretch = 1.0
        self.samples: collections.deque = collections.deque()
        self.last_change = clock_t0

    def share(self, now: float, window: float) -> Optional[float]:
        if not self.samples:
            return None
        t1, c1 = self.samples[-1]
        old = [(t, c) for t, c in self.samples if t <= now - window + 1e-9]
        if not old:
            return None
        t0, c0 = old[-1]
        return (c1 - c0) / (t1 - t0) if t1 > t0 else None

    def sample(self, now: float, cpu_s: float) -> float:
        self.samples.append((now, cpu_s))
        while self.samples and self.samples[0][0] < now - DOWN_WINDOW_S - 2:
            self.samples.popleft()
        if now - self.last_change >= UP_WINDOW_S:
            s = self.share(now, UP_WINDOW_S)
            if s is not None and s > self.cpu_max and self.stretch < STRETCH_MAX:
                self.stretch = min(STRETCH_MAX, self.stretch * STRETCH_UP)
                self.last_change = now
                return self.stretch
        if now - self.last_change >= DOWN_WINDOW_S:
            s = self.share(now, DOWN_WINDOW_S)
            if s is not None and s < DOWN_SHARE * self.cpu_max and self.stretch > 1.0:
                self.stretch = max(1.0, self.stretch / STRETCH_UP)
                if self.stretch < 1.0001:
                    self.stretch = 1.0
                self.last_change = now
        return self.stretch


def plan_fast(states: list[TaskState], g: float, stretch: float, elapsed_ms: float, budget_ms: float,
              ready: Callable[[TaskState], bool]) -> Optional[TaskState]:
    """The next task to run in this fast-lane cycle, or None. Tasks not due are passed over; a due task whose p95
    doesn't fit what is left of ``budget_ms`` is skipped (counted, and its turn taken)."""
    for st in states:
        if st.last_turn == g or not st.due(g, stretch) or not ready(st):
            continue
        if st.skip_next:                             # it overran last time: this turn is skipped, not retried
            st.skip_next = False
            st.skipped += 1
            st.last_turn = g
            continue
        if elapsed_ms > 0 and st.p95() > budget_ms - elapsed_ms:
            st.skipped += 1                          # (the cycle's first task always runs: its own budget caps it)
            st.last_turn = g
            continue
        return st
    return None


# -- the watchdog ------------------------------------------------------------------------------------------------------
class Watchdog(threading.Thread):
    """Terminates a Budget at its deadline. One thread; it only waits."""

    def __init__(self) -> None:
        super().__init__(name="ml-watchdog", daemon=True)
        self._cv = threading.Condition()
        self._heap: list = []
        self._seq = 0
        self.fired = 0

    def arm(self, budget: M.Budget) -> None:
        with self._cv:
            self._seq += 1
            heapq.heappush(self._heap, (budget.deadline, self._seq, budget))
            self._cv.notify()

    def disarm(self, budget: M.Budget) -> None:
        with self._cv:
            self._heap = [e for e in self._heap if e[2] is not budget]
            heapq.heapify(self._heap)

    def run(self) -> None:
        while True:
            with self._cv:
                while not self._heap:
                    self._cv.wait()
                deadline, _, budget = self._heap[0]
                wait = deadline - time.monotonic()
                if wait > 0:
                    self._cv.wait(wait)
                    continue
                heapq.heappop(self._heap)
            budget.terminate()
            self.fired += 1


# -- output ------------------------------------------------------------------------------------------------------------
class LineWriter:
    """Whole lines to the protocol fd, one at a time. A failed write (EPIPE: show_service is gone) exits."""

    def __init__(self, fd: int, on_dead: Callable[[], None]) -> None:
        self.fd, self.on_dead = fd, on_dead
        self.lock = threading.Lock()
        self.lines = 0

    def write(self, obj: dict) -> None:
        data = P.encode_line(obj)
        with self.lock:
            try:
                view = memoryview(data)
                while view:
                    n = os.write(self.fd, view)
                    view = view[n:]
                self.lines += 1
            except OSError:
                self.on_dead()


# -- the worker --------------------------------------------------------------------------------------------------------
class Worker:
    def __init__(self, models_dir: pathlib.Path, *, fake: bool, nice: int, out: Callable[[dict], None],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.models_dir = pathlib.Path(models_dir).expanduser()
        self.fake, self.nice, self.out, self.clock = fake, nice, out, clock
        self.problems: list[str] = []
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg_version = 0
        self.cfg_event = threading.Event()
        self._cfg_lock = threading.Lock()
        self.ring = Ring(self._ring_s(self.cfg))
        self.stop = threading.Event()
        self.unknown_cmds = 0
        self.errors = 0
        self.last_error: Optional[str] = None
        self.states = {t: TaskState(t) for t in C.ML_TASKS}
        self._apply_task_cfg(self.cfg)
        self.guard = CpuGuard(float(self.cfg["cpu_max"]), clock())
        self.watchdog = Watchdog()
        self.backends: Optional[M.Backends] = None
        self._backends_lock = threading.Lock()
        self._reload_lock = threading.Lock()
        self._loaded_version = -1
        self.parser = P.FrameParser()
        self.stats_every = STATS_EVERY_S
        self.lanes: dict[str, dict] = {}          # lane -> {"beat": its last cycle mark, "period", "budget_s"}
        self._reloading = False                   # a model reload in a lane: the lane watchdog waits for it
        self.exit_code = 0

    # config
    @staticmethod
    def _ring_s(cfg: dict) -> float:
        return max(float(t.get("window_s", 0)) for t in cfg["tasks"].values()) + RING_MARGIN_S + 1.0

    def _apply_task_cfg(self, cfg: dict) -> None:
        for name, st in self.states.items():
            st.configure(cfg["tasks"].get(name, {}))

    def configure(self, ml: Any) -> None:
        problems: list[str] = []
        cfg = merge_config(DEFAULTS, ml, "ml", problems)
        with self._cfg_lock:
            self.cfg = cfg
            self.cfg_version += 1
            self.problems = problems
        for p in problems:
            log(f"config: {p} (the starting point is used)")
        self._apply_task_cfg(cfg)
        self.guard.cpu_max = float(cfg["cpu_max"])
        self.ring.resize(self._ring_s(cfg))
        self.cfg_event.set()

    # input
    def on_frame(self, fr: P.Frame) -> None:
        if fr.is_audio:
            self.ring.push(fr)
            return
        cmd = (fr.ctrl or {}).get("cmd")
        if cmd == C.ML_CMD_CONFIG:
            self.configure((fr.ctrl or {}).get("ml"))
        elif cmd == C.ML_CMD_STOP:
            log("stop")
            self.stop.set()
        else:
            self.unknown_cmds += 1

    def reader(self, fd: int) -> None:
        try:
            while not self.stop.is_set():
                data = os.read(fd, 65536)
                if not data:
                    log("stdin closed")
                    break
                for fr in self.parser.feed(data):
                    self.on_frame(fr)
        except OSError as e:
            log(f"stdin: {e}")
        finally:
            self.stop.set()

    # models
    def load_models(self) -> M.Backends:
        with self._cfg_lock:
            cfg, version = self.cfg, self.cfg_version
        b = M.load(cfg, self.models_dir, fake=self.fake, log=log)
        with self._backends_lock:
            self.backends, self._loaded_version = b, version
        return b

    def maybe_reload(self) -> None:
        """On a config change (either lane, between cycles): reload the models when the set changed."""
        with self._reload_lock:
            with self._cfg_lock:
                cfg, version = self.cfg, self.cfg_version
            b = self.backends
            if b is None or version == self._loaded_version:
                return
            if M.model_set(cfg) != b.key_sig:
                log("the models in the config changed: reloading")
                self._reloading = True
                try:
                    self.load_models()
                finally:
                    self._reloading = False
                    for info in self.lanes.values():
                        info["beat"] = self.clock()
                return
            ced = b.tasks.get("ced")
            if hasattr(ced, "set_groups"):
                ced.set_groups(cfg.get("ced_groups") or {}, log)
            self._loaded_version = version

    def hello(self) -> dict:
        b = self.backends or M.Backends()
        return {"type": C.ML_HELLO, "v": C.ML_PROTOCOL_V, "pid": os.getpid(), "t": self.clock(),
                "models": dict(b.models), "tasks": {t: b.tasks.get(t) is not None for t in C.ML_TASKS},
                "versions": M.versions(), "threads": {"compute": 2, "intra_op": 1},
                "nice": os.getpriority(os.PRIO_PROCESS, 0)}

    # one task
    def _run_task(self, st: TaskState, b: M.Backends) -> Optional[float]:
        """Run ``st`` once on the newest window; returns its cost in ms (None: it didn't run)."""
        runner = b.tasks.get(st.name)
        cfg = self.cfg
        key = self.ring.key()                         # before the copy: a frame arriving now counts as new
        w = self.ring.window(max(st.window_s, float(getattr(runner, "need_s", 0.0) or 0.0)))
        if w is None:
            return None
        st.last_key = key
        if st.name == "beats" and not w.clean:
            return None
        dbfs = M.level_dbfs(w.pcm[-int(st.window_s * FS):])
        if dbfs < float(cfg["silence_dbfs"]):
            st.silent += 1
            if st.name == "ced":
                line = {"type": C.ML_CED, "t": w.t_end, "window_s": st.window_s, "stream": w.stream, "seg": w.seg}
                line.update({f"{g}_p": 0.0 for g in C.CED_GROUPS})
                line.update({"silence_p": 1.0, "skipped": "silence", "cost_ms": 0.0})
                self.out(line)
            return None
        budget = M.Budget(st.budget_ms, self.clock)
        self.watchdog.arm(budget)
        over = False
        res = None
        try:
            res = runner.run(w.pcm, budget, n_start=w.n_start, n_end=w.n_end, gen=w.gen)
            if budget.elapsed_ms() > st.budget_ms:
                over = True
        except M.Overrun:
            over = True
        except Exception as e:                        # noqa: BLE001  a task failing never stops the worker
            st.errors += 1
            self.errors += 1
            self.last_error = f"{st.name}: {type(e).__name__}: {e}"[:ERROR_MAX]
            log(self.last_error)
            self.out({"type": C.ML_ERROR, "t": self.clock(), "task": st.name, "error": self.last_error})
            return budget.elapsed_ms()
        finally:
            self.watchdog.disarm(budget)
        cost = budget.elapsed_ms()
        st.ran(cost, over)
        if over or res is None:
            return cost
        self.out(self._line(st, w, res, cost))
        return cost

    def _line(self, st: TaskState, w: Window, res: dict, cost: float) -> dict:
        base = {"stream": w.stream, "seg": w.seg}
        if st.name == "ced":
            return {"type": C.ML_CED, "t": w.t_end, "window_s": st.window_s, **base, **res, "cost_ms": cost}
        if st.name == "tags":
            emb = res.pop("emb_vec")
            line = {"type": C.ML_TAGS, "t": w.t_end, "window_s": st.window_s, **base,
                    "genre_parents": res["genre_parents"], "styles_top3": res["styles_top3"],
                    "moods_top5": res["moods_top5"], "instruments_top5": res["instruments_top5"]}
            for f in M.SCALAR_FIELDS:
                line[f] = res.get(f)
            line.update({"emb_id": res["emb_id"], "emb": P.encode_emb(emb), "emb_dim": res["emb_dim"],
                         "cost_ms": cost})
            return line
        if st.name == "key":
            return {"type": C.ML_KEY, "t": w.t_end, "window_s": st.window_s, **base, **res, "cost_ms": cost}
        # beats
        margin = float(self.cfg["tasks"]["beats"].get("interior_margin_s", 3))
        t0 = w.t_start
        t1 = w.t_end
        i0, i1 = t0 + margin, t1 - margin
        beats = [t0 + float(b) for b in res["beats_rel"]]
        downs = [t0 + float(d) for d in res["downbeats_rel"]]
        bi = [b for b in beats if i0 <= b <= i1]
        di = [d for d in downs if i0 <= d <= i1]
        bpm = None
        if len(bi) >= 4:
            ibi = float(np.median(np.diff(bi)))
            bpm = 60.0 / ibi if ibi > 0 else None
        bar_len = None
        if len(di) >= 2:
            arr = np.asarray(beats)
            counts = [int(np.sum((arr >= a - 1e-6) & (arr < b - 1e-6))) for a, b in zip(di[:-1], di[1:])]
            counts = [c for c in counts if c > 0]
            if counts:
                bar_len = int(round(float(np.median(counts))))
        return {"type": C.ML_BEATS, "window_t0": t0, "window_t1": t1, "interior": [i0, i1], **base, "beats": bi,
                "downbeats": di, "bpm": bpm, "bar_len": bar_len,
                "model": str(self.cfg["tasks"]["beats"].get("model")), "cost_ms": cost}

    def _ready(self, st: TaskState) -> bool:
        b = self.backends
        if b is None or b.tasks.get(st.name) is None:
            return False
        return st.last_key != self.ring.key()       # nothing new: skip without counting

    # lanes
    def lane(self, names: tuple, lane_cfg: Callable[[], tuple]) -> None:
        """One compute thread: wake on the grid, run what is due in order, then wait for the next grid point. Each
        cycle marks ``lanes[name]`` at its start and end (the lane watchdog, ``wedged_lane``)."""
        name = "+".join(names)
        grid = Grid(self.clock())
        info = self.lanes[name] = {"beat": self.clock(), "period": 1.0, "budget_s": 0.0}
        while not self.stop.is_set():
            g = grid.next_t
            wait = g - self.clock()
            if wait > 0 and self.stop.wait(wait):
                return
            every_s, budget_ms = lane_cfg()
            self.maybe_reload()
            b = self.backends
            start = self.clock()
            states = [self.states[n] for n in names]
            info.update(beat=start, period=every_s * self.guard.stretch,
                        budget_s=max([budget_ms] + [st.budget_ms for st in states]) / 1000.0)
            while b is not None and not self.stop.is_set():
                st = plan_fast(states, g, self.guard.stretch, (self.clock() - start) * 1000.0, budget_ms,
                               self._ready)
                if st is None:
                    break
                st.last_turn = g
                self._run_task(st, b)
            info["beat"] = self.clock()
            grid.after(self.clock(), every_s * self.guard.stretch)

    def wedged_lane(self, now: float) -> Optional[str]:
        """A lane that has made no cycle in max(LANE_STALL_PERIODS x its period, its period + its budget +
        LANE_STALL_SLACK_S): stuck in a call that never returns (essentia can't be stopped; a lock). The reader and
        the stats carry on without it, so show_service's stall rule alone wouldn't see it."""
        if self._reloading:
            return None
        for name, info in list(self.lanes.items()):
            limit = max(LANE_STALL_PERIODS * info["period"], info["period"] + info["budget_s"] + LANE_STALL_SLACK_S)
            if now - info["beat"] > limit:
                return f"{name} has made no cycle for {now - info['beat']:.0f} s (limit {limit:.0f} s)"
        return None

    def _fast_cfg(self) -> tuple:
        fl = self.cfg["fast_lane"]
        return float(fl["every_s"]), float(fl["budget_ms"])

    def _beats_cfg(self) -> tuple:
        bt = self.cfg["tasks"]["beats"]
        return float(bt["every_s"]), float(bt["budget_ms"])         # one task: the watchdog caps it

    # stats
    def stats(self, cpu_share: float) -> dict:
        r = self.ring
        try:
            with open("/proc/self/statm") as f:
                rss_mb = int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
        except OSError:
            rss_mb = 0.0
        return {"type": C.ML_STATS, "t": self.clock(), "cpu_share": cpu_share, "cpu_s": time.process_time(),
                "rss_mb": rss_mb, "frames": r.frames, "bytes": r.bytes, "gaps": r.gaps, "holes": r.holes,
                "drops_seen": r.drops_seen, "ring_s": r.held_s(), "stretch": self.guard.stretch,
                "tasks": {t: s.stats(self.guard.stretch) for t, s in self.states.items()},
                "errors": self.errors, "last_error": self.last_error}

    def main_loop(self) -> None:
        last_stats_t, last_stats_cpu = self.clock(), time.process_time()
        while not self.stop.wait(1.0):
            now, cpu = self.clock(), time.process_time()
            wedged = self.wedged_lane(now)
            if wedged is not None:                    # exit non-zero: show_service's supervisor restarts us
                log(f"lane {wedged}: wedged, exiting")
                self.exit_code = LANE_STALL_EXIT
                self.stop.set()
                return
            self.guard.sample(now, cpu)
            if now - last_stats_t >= self.stats_every - 1e-3:
                share = (cpu - last_stats_cpu) / (now - last_stats_t)
                self.out(self.stats(share))
                last_stats_t, last_stats_cpu = now, cpu


# -- process plumbing --------------------------------------------------------------------------------------------------
def _set_nice(target: int) -> int:
    try:
        cur = os.getpriority(os.PRIO_PROCESS, 0)
        if target > cur:
            os.setpriority(os.PRIO_PROCESS, 0, min(19, target))
    except OSError as e:
        log(f"nice: {e}")
    return os.getpriority(os.PRIO_PROCESS, 0)


def _pdeathsig() -> None:
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGTERM, 0, 0, 0)         # PR_SET_PDEATHSIG
    except OSError as e:
        log(f"prctl: {e}")


def _bench(models_dir: pathlib.Path, path: str, reps: int, offset_s: float) -> int:
    import subprocess
    dur = max(40.0, reps + 32.0)
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", str(offset_s), "-t", str(dur), "-i", path,
                          "-f", "f32le", "-ac", "1", "-ar", str(FS), "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype="<f4").copy()
    cfg = copy.deepcopy(DEFAULTS)
    t0 = time.perf_counter()
    b = M.load(cfg, models_dir, log=log)
    print(json.dumps({"load_s": round(time.perf_counter() - t0, 2), "models": b.models,
                      "tasks": {k: v is not None for k, v in b.tasks.items()}}))
    for name in C.ML_TASKS:
        runner = b.tasks.get(name)
        if runner is None:
            continue
        tc = cfg["tasks"][name]
        win = int(math.ceil(max(float(tc["window_s"]), float(getattr(runner, "need_s", 0.0) or 0.0)) * FS))
        hop = int(float(tc["every_s"]) * FS)
        costs, last = [], None
        n = max(3, min(reps, (len(x) - win) // hop))
        for i in range(n):
            a = i * hop
            budget = M.Budget(1e9)
            t = time.perf_counter()
            last = runner.run(x[a:a + win], budget, n_start=a, n_end=a + win, gen=1)
            costs.append((time.perf_counter() - t) * 1000)
        c = np.asarray(costs[1:] or costs)
        if isinstance(last, dict):
            last = {k: (v if not isinstance(v, np.ndarray) else f"<{v.shape}>") for k, v in last.items()}
        print(json.dumps({"task": name, "runs": len(costs), "first_ms": round(costs[0], 2),
                          "p50_ms": round(float(np.percentile(c, 50)), 2),
                          "p95_ms": round(float(np.percentile(c, 95)), 2), "last": P.clean(last, 3)}, default=str))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m show_service.ml.worker", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default=str(C.MODELS_DIR))
    ap.add_argument("--parent", type=int, default=0, help="show_service's pid: exit when it isn't our parent")
    ap.add_argument("--nice", type=int, default=DEFAULTS["nice"])
    ap.add_argument("--fake", action="store_true", help="tests only: cheap numpy results, no model files")
    ap.add_argument("--stats-every", type=float, default=STATS_EVERY_S, help=argparse.SUPPRESS)   # tests only
    ap.add_argument("--bench", metavar="AUDIO", help="not production: each task's cost per call on AUDIO")
    ap.add_argument("--bench-reps", type=int, default=12)
    ap.add_argument("--bench-offset", type=float, default=30.0)
    a = ap.parse_args(argv)

    nice = _set_nice(a.nice)                          # 1. before any thread: every thread inherits it
    if a.bench:
        return _bench(pathlib.Path(a.models).expanduser(), a.bench, a.bench_reps, a.bench_offset)

    proto_fd = os.dup(1)                              # 2. the protocol keeps the real stdout...
    os.dup2(2, 1)                                     # ...and fd 1 becomes stderr
    sys.stdout = sys.stderr
    _pdeathsig()                                      # 3. die with show_service
    if a.parent and os.getppid() != a.parent:
        log(f"parent {a.parent} is gone (ppid {os.getppid()}): exiting")
        return 3

    def dead() -> None:
        os._exit(0)

    writer = LineWriter(proto_fd, dead)
    w = Worker(pathlib.Path(a.models), fake=a.fake, nice=nice, out=writer.write)
    w.stats_every = max(0.5, a.stats_every)
    signal.signal(signal.SIGTERM, lambda *_: w.stop.set())
    signal.signal(signal.SIGINT, lambda *_: w.stop.set())

    rd = threading.Thread(target=w.reader, args=(0,), name="ml-reader", daemon=True)   # 4. the stdin reader
    rd.start()
    w.cfg_event.wait(CONFIG_WAIT_S)
    t0 = time.monotonic()
    b = w.load_models()                               # 5. the models
    log(f"models loaded in {time.monotonic() - t0:.2f} s: {b.models}")
    w.maybe_reload()
    writer.write(w.hello())                           # 6. hello (also when stdin already closed: S4.11's check)
    if w.stop.is_set():
        return _exit(writer)
    w.watchdog.start()
    threading.Thread(target=w.lane, args=(FAST_ORDER, w._fast_cfg), name="ml-fast", daemon=True).start()
    threading.Thread(target=w.lane, args=(("beats",), w._beats_cfg), name="ml-beats", daemon=True).start()
    w.main_loop()
    return _exit(writer, w.exit_code)


def _exit(writer: LineWriter, code: int = 0) -> int:
    """Exit at once (0, or the lane watchdog's code): no torn line (the writer's lock, waited for at most 1 s: a
    wedged lane may hold it), no wait for a model run in progress."""
    writer.lock.acquire(timeout=1.0)
    try:
        sys.stderr.flush()
    except Exception:                                  # noqa: BLE001
        pass
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
