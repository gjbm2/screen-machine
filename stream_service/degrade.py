"""The degradation controller (4K60 plan change 5; docs/perf/PLAN.md, DIAGNOSIS_2026-10-18 §8 "The ladder").

One per session, where its Engine runs (renderer.py: the render worker for ``workers: proc`` screens, the service
otherwise), on the render thread at a frame boundary.  Every DECIDE_S it reads the last second of the engine's frame
records and decides with arithmetic only.  Per output slot (means, ms) it knows:
  * host: the render thread's wall time per rendered slot (own CPU + run-queue wait + blocked: GIL, locks, syscalls);
  * gpu:  the frame's GPU span where it outlasted the host's submit by GPU_MARGIN_MS (real GPU work; a shorter span is
          the GPU waiting on the host);
  * enc:  the encode thread's time per slot (Encode() / the pipe write) plus the render thread's wait for a free slot;
          of the encode thread's time, how much it sat runnable in the run queue (schedstat: the host's CPUs, not
          the encoder);
and the outcome: repeats and late slots.  Each stage's time as a share of the output period is its load.

A second is overloaded when both its halves are: a stage at DOWN_LOAD or more, or repeats (REPEAT_SHARE) or late slots
(LATE_SHARE).  Then one step down, the first of these that is not on yet and can relieve the busiest stage (owner,
21 Oct: "degrade, never refuse": the ladder is the scene's rate and the output fps only; the effects are never trimmed):
  scene_rate  new scene content every 2nd output slot, the last frame shown in between (layers.py: a floor under the
              scene's own rate_div): the host or the GPU, and the scene not already that slow by itself;
  fps         output 60 -> 30 fps: NVENC Reconfigure, frame ids stay on the 60 Hz grid, each fMP4 sample lasts two ticks
              (renderer.py): any stage, on the GPU-encode path at 60 fps only (the ffmpeg path skips it).
The why is the busiest stage, except that an encode stage with at least ENC_RUNQ_SHARE of the encode thread's time
in the run queue, or with NVENC itself under NVENC_BUSY (NVML, read once a second off the render thread), is the host's ("computer busy", 21 Oct: NVENC was 29-46% busy while the box ran at load 40-53 on
20 vCPUs); the step taken is the same.  None left: it stays where it is (``floor``).  Down at most once a second: the window after a change starts at the
change.  Up: the last step on comes off once its projection (the load without it, from the same numbers) has stayed
under UP_LOAD for its UP_WAIT_S (fps 30 s) and as long since the last change.  A step that has to come back on within
SETTLE_S of coming off doubles its wait (up to WAIT_MAX_X); one that stays off that long starts again from 1x.  Frozen
(no change, windows restart) across the entry (until the master bus is fully open) and the exit.  It never stops a
stream and never refuses: every step keeps the stream playing.

One decision per room (docs/ENSEMBLE.md §8.9; owner 22 Oct: the screens drifted apart): each screen keeps its own
measurement and its own steps (``steps``), and takes a room floor (``set_room``: the steps its room's other screens
took by their own measurement, from the session).  The steps in effect (``effective``) are its own and the room's:
a step one screen takes lifts the room, a step can't come off below the room's floor, and the room comes back up
only once every screen's own measurement lets it.  A step the room already has is never its own next step down.
Never a refusal: the floor only adds steps that keep the stream playing.  No room: an empty floor, as before.

``state()`` is what /api/sessions (``degrade``), the session.degrade event and the details panel show.  With a room
floor it also carries ``own`` and ``room`` (the steps behind it), and the words say whose ("60 -> 30 fps: in step
with south-screen (GPU busy)").
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

STEPS = ("scene_rate", "fps")
WHY = {"host": "computer busy", "gpu": "GPU busy", "enc": "encoder busy", "fallback": "encoder fell back"}
DECIDE_S = 1.0          # decisions per second, each over the last second
DOWN_LOAD = 0.95        # a stage this busy in both halves of a second: one step down
UP_LOAD = 0.7           # a step comes off only if the projection without it is under this share of the period
REPEAT_SHARE = 0.05     # ... or this share of the slots repeated (at least 2)
LATE_SHARE = 0.25       # ... or started more than a period late
MIN_SLOTS = 3           # a half with fewer slots says nothing
UP_WAIT_S = {"scene_rate": 10.0, "fps": 30.0}
SETTLE_S = 60.0
WAIT_MAX_X = 8
GPU_MARGIN_MS = 1.0     # as guard.GPU_BOUND_MARGIN_MS: a span this much over the host's submit is real GPU work
ENC_RUNQ_SHARE = 0.5    # the encode stage busiest with this share of its thread's time runnable, not running: "host"
NVENC_BUSY = 0.85       # ... or with NVENC itself (NVML) under this share busy: "host" too
SCENE_FLOOR = 2
EPS = 1e-6


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class Window:
    """A stretch of slots: per-slot means (ms) of each stage, and the outcome."""
    slots: int = 0
    rendered: int = 0
    host_ms: float = 0.0
    cpu_ms: float = 0.0
    runq_ms: float = 0.0
    blocked_ms: float = 0.0
    gpu_ms: float = 0.0
    enc_ms: float = 0.0
    enc_runq_ms: float = 0.0                # the encode thread's run-queue wait inside enc_ms
    wait_ms: float = 0.0
    drawn_host_ms: Optional[float] = None   # slots whose scene was drawn (not held by a rate divider)
    drawn_gpu_ms: Optional[float] = None
    repeats: int = 0
    late: int = 0

    def load(self, period_ms: float) -> dict[str, float]:
        p = max(EPS, period_ms)
        return {"host": self.host_ms / p, "gpu": self.gpu_ms / p, "enc": (self.enc_ms + self.wait_ms) / p}

    @property
    def enc_runq(self) -> float:
        """The share of the encode thread's time spent waiting for a CPU (0..1)."""
        return min(1.0, self.enc_runq_ms / self.enc_ms) if self.enc_ms > EPS else 0.0


def window(recs: Iterable[Any], period_ms: float) -> Window:
    """renderer._Rec rows -> a Window.  Host and GPU are over rendered slots (a repeat renders nothing), the encoder
    over every slot it has taken; the GPU span of a frame still on the GPU is not known yet and is left out."""
    w = Window()
    host = cpu = runq = blk = gpu = enc = erq = wait = dh = dg = 0.0
    n_gpu = n_enc = nd = ndg = 0
    for r in recs:
        w.slots += 1
        if r.slip_ms > period_ms:
            w.late += 1
        wait += r.wait_ms or 0.0
        if r.write_ms is not None:
            enc += r.write_ms
            erq += r.enc_runq_ms or 0.0
            n_enc += 1
        if r.repeat:
            w.repeats += 1
            continue
        w.rendered += 1
        h = r.host_ms or 0.0
        host += h
        cpu += r.cpu_ms or 0.0
        runq += r.runq_ms or 0.0
        blk += r.blocked_ms or 0.0
        g = None
        if r.render_ms is not None:
            g = r.render_ms if r.render_ms > h + GPU_MARGIN_MS else 0.0
            gpu += g
            n_gpu += 1
        if not r.held:
            dh += h
            nd += 1
            if g is not None:
                dg += g
                ndg += 1
    k = max(1, w.rendered)
    w.host_ms, w.cpu_ms, w.runq_ms, w.blocked_ms = host / k, cpu / k, runq / k, blk / k
    w.gpu_ms = gpu / n_gpu if n_gpu else 0.0
    w.enc_ms = enc / n_enc if n_enc else 0.0
    w.enc_runq_ms = erq / n_enc if n_enc else 0.0
    w.wait_ms = wait / max(1, w.slots)
    w.drawn_host_ms = dh / nd if nd else None
    w.drawn_gpu_ms = dg / ndg if ndg else None
    return w


@dataclass
class Ctx:
    """What the engine adds to the records at a decision."""
    frozen: bool = False        # the entry (master bus not fully open yet) or the exit
    has_fx: bool = False        # the bus carries effects (not on their way out); reported only: no step trims them
    fx_host_ms: float = 0.0     # their cost per rendered frame now (the guard's samples: own CPU, real GPU)
    fx_gpu_ms: float = 0.0
    own_div: int = 1            # the scene's own rate_div (layers.py)
    can_fps: bool = False       # the GPU-encode path at 60 fps
    nvenc_busy: Optional[float] = None   # NVENC's own utilisation on this GPU, 0..1 (diag.EncoderUtil)


@dataclass
class Change:
    step: str
    on: bool
    why: Optional[str]          # the busy stage (host | gpu | enc | fallback); None for a step up


def text(steps: Iterable[str], why: Optional[str], grid_fps: int) -> str:
    """Plain words for the panel: "60 -> 30 fps: computer busy", "scene 30 fps: GPU busy"."""
    steps = list(steps)
    if not steps:
        return f"full rate, {grid_fps} fps"
    out_fps = grid_fps // 2 if "fps" in steps else grid_fps
    words = {"fps": f"{grid_fps} -> {out_fps} fps", "scene_rate": f"scene {out_fps // SCENE_FLOOR} fps"}
    s = " + ".join(words[x] for x in reversed(steps))
    return f"{s}: {WHY.get(why, why)}" if why else s


class Controller:
    def __init__(self, grid_fps: int, wall: Callable[[], float] = time.time):
        self.grid_fps = int(grid_fps)
        self.wall = wall
        self.steps: list[str] = []
        self.why: Optional[str] = None             # the busy stage behind the steps on
        self.changed_at: Optional[float] = None    # pace clock
        self.since: Optional[float] = None         # wall clock of the last change
        self.window_from: Optional[float] = None   # decisions read records from here on (a change, an unfreeze)
        self.ok_since: Optional[float] = None      # the last step's projection under UP_LOAD since
        self.mult = {s: 1 for s in STEPS}
        self.off_at: dict[str, float] = {}         # when a step last came off
        self.frozen = True
        self.floor = False                         # overloaded with no step left
        self.load: Optional[dict] = None
        self.downs = self.ups = self.failed_ups = 0
        self.room: tuple = ()                      # the room's floor: steps its other screens took (set_room)
        self.room_why: Optional[str] = None        # ... in words ("in step with south-screen (GPU busy)")

    # -- the room (one decision per room) -------------------------------------------------------------------------
    def effective(self) -> list:
        """The steps in effect: its own, then the room's that it doesn't have (in the ladder's order)."""
        if not self.room:
            return self.steps
        return self.steps + [s for s in STEPS if s in self.room and s not in self.steps]

    def set_room(self, steps: Iterable[str], why: Optional[str], now: float) -> bool:
        """The room's floor (the steps the room's other screens took for themselves).  True when it changed what is
        in effect (or its words): the engine applies it; a change restarts the windows, as its own steps do."""
        room = tuple(s for s in STEPS if s in tuple(steps or ()))
        why = why if room else None
        if room == self.room and why == self.room_why:
            return False
        before = list(self.effective())
        self.room, self.room_why = room, why
        if self.effective() != before:
            self._changed(now)
        return True

    # -- when ----------------------------------------------------------------------------------------------------
    def window_start(self, now: float) -> float:
        w = now - DECIDE_S
        return w if self.window_from is None else max(w, self.window_from)

    # -- what ----------------------------------------------------------------------------------------------------
    def decide(self, recs: list, ctx: Ctx, now: float, period_ms: float) -> Optional[Change]:
        """``recs``: the frame records since window_start(now); ``period_ms``: the output period now."""
        if ctx.frozen:
            self.frozen, self.window_from, self.ok_since = True, now, None
            return None
        if self.frozen:                           # just opened: a whole second from here before anything
            self.frozen, self.window_from, self.ok_since = False, now, None
            return None
        if self.window_from is not None and now - self.window_from < DECIDE_S - EPS:
            return None
        mid = now - DECIDE_S / 2
        a = window([r for r in recs if r.t_start < mid], period_ms)
        b = window([r for r in recs if r.t_start >= mid], period_ms)
        whole = window(recs, period_ms)
        ld = whole.load(period_ms)
        self.load = {**{k: round(v, 3) for k, v in ld.items()}, "enc_runq": round(whole.enc_runq, 3),
                     "nvenc": None if ctx.nvenc_busy is None else round(ctx.nvenc_busy, 2)}
        sa, sb = self._over(a, period_ms), self._over(b, period_ms)
        if sa and sb:                             # held for the whole second
            self.ok_since = None
            stage = max(ld, key=ld.get)
            step = self._next_down(stage, ctx)
            if step is None:
                self.floor = True
                return None
            starved = whole.enc_runq >= ENC_RUNQ_SHARE or (ctx.nvenc_busy is not None
                                                          and ctx.nvenc_busy < NVENC_BUSY)
            why = "host" if stage == "enc" and starved else stage   # the encode thread starved, not NVENC
            return self._on(step, why, ctx, now)
        self.floor = False
        if sa or sb or not self.steps:
            self.ok_since = None
            return None
        last = self.steps[-1]
        if self.project(last, whole, ctx, period_ms) >= UP_LOAD:
            self.ok_since = None
            return None
        if self.ok_since is None:
            self.ok_since = now - DECIDE_S        # this second counts
        wait = UP_WAIT_S[last] * self.mult[last]
        if now - self.ok_since >= wait - EPS and now - (self.changed_at or -1e9) >= wait - EPS:
            return self._off(last, now)
        return None

    def drop(self, step: str, now: float, why: str = "fallback") -> Optional[Change]:
        """A step that can no longer apply (the GPU encoder fell back to ffmpeg: no fps step there) comes off now."""
        if step not in self.steps:
            return None
        self.steps.remove(step)
        if not self.steps:
            self.why = None
        self._changed(now)
        return Change(step, False, why)

    @staticmethod
    def _over(w: Window, period_ms: float) -> Optional[str]:
        if w.slots < MIN_SLOTS:
            return None
        ld = w.load(period_ms)
        stage = max(ld, key=ld.get)
        if ld[stage] >= DOWN_LOAD or w.repeats >= max(2.0, REPEAT_SHARE * w.slots) or w.late >= LATE_SHARE * w.slots:
            return stage
        return None

    def _next_down(self, stage: str, ctx: Ctx) -> Optional[str]:
        for step in STEPS:
            if step in self.steps or step in self.room:     # (the room's floor has it on already)
                continue
            if step == "scene_rate" and stage in ("host", "gpu") and ctx.own_div < SCENE_FLOOR:
                return step
            if step == "fps" and ctx.can_fps:
                return step
        return None

    def project(self, step: str, w: Window, ctx: Ctx, period_ms: float) -> float:
        """The load (the busiest stage's share of the period) with ``step`` off again."""
        ld = w.load(period_ms)
        p = max(EPS, period_ms)
        if step == "fps":                         # the same work per slot in half the period
            return 2.0 * max(ld.values())
        if step == "scene_rate":
            if ctx.own_div >= SCENE_FLOOR or w.drawn_host_ms is None:
                return max(ld.values())           # the scene is that slow by itself: the floor changes nothing
            return max(max(w.host_ms, w.drawn_host_ms) / p, max(w.gpu_ms, w.drawn_gpu_ms or 0.0) / p, ld["enc"])
        return max(ld.values())

    def _on(self, step: str, stage: str, ctx: Ctx, now: float) -> Change:
        self.steps.append(step)
        self.why = stage
        off = self.off_at.pop(step, None)
        if off is not None:
            if now - off < SETTLE_S:              # it came off too soon: wait longer next time
                self.mult[step] = min(WAIT_MAX_X, self.mult[step] * 2)
                self.failed_ups += 1
            else:
                self.mult[step] = 1
        self.downs += 1
        self._changed(now)
        return Change(step, True, stage)

    def _off(self, step: str, now: float) -> Change:
        self.steps.pop()
        self.off_at[step] = now
        if not self.steps:
            self.why = None
        self.ups += 1
        self._changed(now)
        return Change(step, False, None)

    def _changed(self, now: float) -> None:
        self.changed_at = self.window_from = now
        self.ok_since = None
        self.since = self.wall()

    # -- reporting -----------------------------------------------------------------------------------------------
    @property
    def out_fps(self) -> int:
        return self.grid_fps // 2 if "fps" in self.effective() else self.grid_fps

    def state(self) -> dict:
        eff = self.effective()
        why = self.why if self.steps else self.room_why if eff else None     # its own words, else the room's
        st = {"mode": "on", "level": len(eff), "step": eff[-1] if eff else None,
              "steps": list(eff), "why": WHY.get(why, why) if why else None,
              "since": None if self.since is None else _iso(self.since),
              "text": text(eff, why, self.grid_fps),
              "out_fps": self.out_fps, "scene_div": SCENE_FLOOR if "scene_rate" in eff else 1,
              "fx_trim": False, "floor": self.floor, "frozen": self.frozen, "load": self.load,
              "up_wait_s": {s: UP_WAIT_S[s] * self.mult[s] for s in STEPS},
              "downs": self.downs, "ups": self.ups, "failed_ups": self.failed_ups}
        if self.room:
            st["own"], st["room"] = list(self.steps), list(self.room)
        return st


def off_state(grid_fps: int) -> dict:
    """``degrade: off`` (stream_switches.json): no controller."""
    return {"mode": "off", "level": 0, "step": None, "steps": [], "why": None, "since": None,
            "text": text((), None, grid_fps), "out_fps": grid_fps, "scene_div": 1, "fx_trim": False, "floor": False,
            "frozen": False, "load": None}
