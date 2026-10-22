"""The render tree: SourceLayer and TransitionLayer (GPU-agnostic; the buffers come from the frame context).

* ``SourceLayer(source, t0)`` renders a source at local time ``t - t0``.  A scene source with a session
  modulation runs on the scene clock instead: its ``dt`` is scaled by the speed master and ``t_local`` is
  integrated from that (transitions always run in stream seconds).
* ``SourceLayer(source, t0, t0_mono)``: a room's scene (docs/ENSEMBLE.md §8.9, one picture by design): its time runs
  from the room's moment for the cut, ``t0_mono`` (CLOCK_MONOTONIC s on this screen's render clock: the room's photon
  time minus this screen's lead), read from the frame's own time (``FrameCtx.mono``) instead of its frame id, so every
  screen in the room draws the same scene time at the same photon time: a screen that cuts later (a stagger, a slow
  prepare) joins the scene where the room is, a resync never sets it back, and the screens' frame grids don't matter.
  Canon's clock offset (SessionRuntime.clock_off, ensemble.py) runs it that far behind (a round later); before the
  room's moment (the offset past the stagger) it holds its first frame.  It follows a change of the offset only while
  the change keeps the clock running forward at a sane rate (ensemble.CLOCK_SLOW / CLOCK_FAST and the runtime's
  ``clock_off_ok``); a steeper one waits for the next cut.  Without ``t0_mono`` (no room, a person's cue): as above.
* ``TransitionLayer(a, b, transition, n_start)`` renders ``a`` and ``b`` into pooled buffers and blends them.
  Transitions nest: a transition started while another runs takes the running ``TransitionLayer`` as its
  ``a``.  When a transition reaches progress 1 the tree collapses it to its ``b`` (after that frame), and
  everything in ``a`` is closed.
* Events: each transition reports ``started`` on its first rendered frame and ``done`` on the first frame
  that shows ``b`` fully (progress 1), with that frame's pts.  A transition discarded before it finished
  (because an enclosing one completed first) reports ``done`` with ``superseded=True`` at the discarding
  frame, so every ``started`` gets exactly one ``done``.

Timeline: frame ``n`` is at ``t = n / fps`` stream seconds (deterministic; equals the video's mediaTime).

Failure isolation (EFFECTS_LAYER_DESIGN C.1): a plug-in whose ``render`` raises never takes the session down.
A source is replaced by solid black, a transition by a crossfade (for the rest of its duration), an effect by
a passthrough (effects.py).  Each substitution is reported once through ``FrameCtx.report`` (or collected in
``FrameCtx.errors`` when there is no reporter, as in the unit tests).

GPU frame-time guard (guard.py): with ``FrameCtx.timer`` set, every plug-in call (a source, a transition, each
effect) is bracketed by GPU timing events; a plug-in that stays over budget is tripped through ``trip(fc, exc)``:
a source goes black like a failing one (the session cues a recovery), a transition finishes as a cut, an effect
is dropped.  ``StillLayer`` shows a frozen frame (an exit that must not render a slow scene).

Scene frame rate under GPU load (owner, 15 Oct 2026): a scene source whose measured GPU cost per render is over
``RATE_TARGET`` of a frame period renders only every 2nd, 3rd, ... up to 8th frame (``rate_div``) and shows its last frame
in between, so a struggling GPU drops the scene's frame rate (e.g. 15 fps in a 30 fps stream) instead of falling
behind or tripping; transitions, effects and the stream itself still run every frame.  The guard's budget for the
scene scales with ``rate_div``: it trips only a scene that cannot keep up even at ``RATE_MAX_DIV``.  The rate comes
back up once the cost has stayed low enough for ``RATE_UP_S``.

The degradation controller (degrade.py, plan change 5) bounds it from below: ``FrameCtx.rate_floor`` (2 = new content
every 2nd slot) is a floor under every scene's own ``rate_div``; the scene's own logic is unchanged.  ``FrameCtx.step``
is the controller's fps step: frame ids stay on the profile's grid (``t = n / fps``) and advance by ``step`` per output
slot, so a rate divider counts output slots (``div * step`` ids) and the scene's budget is the output period.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional, Protocol

from .easing import ease
from .ensemble import CLOCK_FAST, CLOCK_SLOW

log = logging.getLogger("stream_service.layers")

RATE_TARGET = 0.7      # a scene may cost this share of a frame period per output frame
RATE_MAX_DIV = 8       # render at most this many frames apart (3.75 fps in a 30 fps stream): degrade, never refuse
RATE_UP_S = 3.0        # stream seconds the cost must stay low (with a 20% margin) before the rate comes back up
RATE_EMA = 0.3
RATE_HOLD_FRAMES = 2   # pooled frames held at once: a transition's outgoing and incoming scenes (the engine reserves)

# notify(kind, phase, pts, tid, superseded)
Notify = Callable[[str, str, float, int, bool], None]


class Pool(Protocol):
    def acquire(self) -> Any: ...
    def release(self, buf: Any) -> None: ...


class FrameOps:
    """Frame primitives the layer tree needs for substitutions and effect mixing.  These defaults work on NumPy
    arrays (unit tests); the engine passes ``gpu.GpuOps`` (CuPy copies and the core kernels)."""

    def copy(self, src: Any, out: Any) -> None:
        out[...] = src

    def fill(self, out: Any, rgb: tuple[int, int, int]) -> None:
        out[..., :3] = rgb
        out[..., 3] = 255

    def crossfade(self, a: Any, b: Any, out: Any, p: float) -> None:
        out[...] = a + (b - a) * p


@dataclass
class FrameCtx:
    n: int          # frame id
    fps: int
    pool: Pool
    retire: Callable[[Any], None] = lambda obj: obj.close()   # how to dispose of a closed source/transition
    ops: FrameOps = field(default_factory=FrameOps)
    # report(kind, obj, exc, action): a plug-in failed and was substituted; None -> appended to ``errors``
    report: Optional[Callable[[str, Any, BaseException, str], None]] = None
    errors: list = field(default_factory=list)
    timer: Any = None       # guard.GpuTimer (engine) or None: begin(owner, kind, obj) -> token, end(token)
    name: str = ""          # the session (logs)
    # this frame's scene sources (the engine resets them per frame): rendered, held (rate_div) and the largest div
    drawn: int = 0
    held: int = 0
    div: int = 1
    step: int = 1           # frame ids per output slot (2: the degradation controller's fps step, 60 -> 30)
    rate_floor: int = 1     # the degradation controller's floor under every scene's rate_div (2: every 2nd slot)
    mono: Optional[float] = None   # this frame's time on CLOCK_MONOTONIC (s; the engine's frame_mono): room clocks

    @property
    def t(self) -> float:
        return self.n / self.fps

    def failed(self, kind: str, obj: Any, exc: BaseException, action: str) -> None:
        if self.report is not None:
            try:
                self.report(kind, obj, exc, action)
                return
            except Exception:   # reporting must never fail a frame
                pass
        self.errors.append((kind, obj, exc, action))


class Layer:
    def render(self, fc: FrameCtx, out: Any) -> None:
        raise NotImplementedError

    def collapse(self, fc: FrameCtx) -> "Layer":
        return self

    def close(self, fc: FrameCtx) -> None:
        raise NotImplementedError

    def walk(self) -> Iterator["Layer"]:
        yield self

    def describe(self) -> Any:
        raise NotImplementedError


class SourceLayer(Layer):
    def __init__(self, source: Any, t0: float, t0_mono: Optional[float] = None):
        self.source = source
        self.t0 = t0
        self.t0_mono = t0_mono                      # the room's scene clock (module doc); None: stream time
        self.off: Optional[float] = None            # ... its clock offset in effect (s), from its first frame
        self.off_held = False                       # an offset change it could not glide: kept until the next cut
        self._off_t: Optional[float] = None
        self.last_t: Optional[float] = None
        self.scene_t = 0.0
        self.failed = False
        self.slow = False
        # -- the scene's frame rate under GPU load: render every rate_div-th frame, hold the last in between --
        self.rate_div = 1
        self.hold: Any = None
        self.n_rendered: Optional[int] = None
        self.cost_ema: Optional[float] = None
        self._prev_ms: Optional[float] = None       # the sample before: one spike alone never demotes
        self._samples_seen = 0
        self._low_since: Optional[float] = None
        self.div_now = 1                            # what it renders at: rate_div or the controller's floor

    def _adapt_rate(self, fc: FrameCtx) -> None:
        """Pick rate_div from this scene's real cost (gpu_stat, read a frame or two late): the larger of its render
        call's own CPU on the host and its GPU span when that outlasted the host's submit (guard: gpu_bound_ms).  A
        span no longer than the submit is the GPU waiting on the host's lock and run-queue stalls, which a slower
        scene rate does not fix (docs/perf/DIAGNOSIS_2026-10-18.md §3: 513 rate changes in 25 min)."""
        st = getattr(self, "gpu_stat", None)
        if st is None or st.samples == self._samples_seen:
            return
        self._samples_seen = st.samples
        cost = max(float(getattr(st, "cpu_ms", 0.0) or 0.0), float(getattr(st, "gpu_bound_ms", 0.0) or 0.0))
        ms, self._prev_ms = (cost if self._prev_ms is None else min(cost, self._prev_ms)), cost
        # up at once on a costly render (two in a row: a lone spike is a busy host, not the scene), down smoothly
        self.cost_ema = ms if self.cost_ema is None else max(ms, self.cost_ema + RATE_EMA * (ms - self.cost_ema))
        per = RATE_TARGET * 1000.0 * max(1, getattr(fc, "step", 1)) / fc.fps   # of an output slot
        want = min(RATE_MAX_DIV, max(1, math.ceil(self.cost_ema / per)))
        if want > self.rate_div:
            self._set_rate(want, fc)
        elif max(1, math.ceil(self.cost_ema / (0.8 * per))) < self.rate_div:
            if self._low_since is None:
                self._low_since = fc.t
            elif fc.t - self._low_since >= RATE_UP_S:
                self._set_rate(max(1, math.ceil(self.cost_ema / (0.8 * per))), fc)
        else:
            self._low_since = None

    def _set_rate(self, div: int, fc: FrameCtx) -> None:
        log.info("[%s] scene %s: %.1f ms per render (own CPU or real GPU): renders every %s (%.1f fps)",
                 getattr(fc, "name", "") or "-", getattr(self.source, "name", type(self.source).__name__),
                 self.cost_ema, "frame" if div == 1 else f"{div} frames",
                 fc.fps / max(1, getattr(fc, "step", 1)) / div)
        self.rate_div, self._low_since = div, None

    def render(self, fc: FrameCtx, out: Any) -> None:
        if not self.failed:
            self._adapt_rate(fc)
        div = self.rate_div
        floor = getattr(fc, "rate_floor", 1)
        if floor > div and not getattr(self.source, "internal", False):
            div = floor                                     # the degradation controller's floor (degrade.py)
        self.div_now = div
        if not self.failed and div > 1 and self.hold is not None and self.n_rendered is not None \
                and 0 <= fc.n - self.n_rendered < div * max(1, getattr(fc, "step", 1)):
            fc.ops.copy(self.hold, out)                     # between renders: the last frame again (dt accrues)
            fc.held += 1
            fc.div = max(fc.div, div)
            return
        mod = getattr(self.source, "modulation", None)
        mono = getattr(fc, "mono", None)
        if self.t0_mono is not None and mono is not None:
            t_local, dt = self._room_clock(mono, mod)
        else:
            t_local = fc.t - self.t0
            dt = 0.0 if self.last_t is None else max(0.0, t_local - self.last_t)
            self.last_t = t_local
            if mod is not None:
                dt = mod.scale_dt(dt)
                self.scene_t += dt
                t_local = self.scene_t
        if self.failed:
            fc.ops.fill(out, (0, 0, 0))
            return
        if div > 1 and self.hold is None:
            self.hold = fc.pool.acquire()
        elif div == 1 and self.hold is not None:
            fc.pool.release(self.hold)
            self.hold = None
        dst = out if self.hold is None else self.hold
        tok = fc.timer.begin(self, "source", self.source) if fc.timer is not None else None
        try:
            self.source.render(t_local, dt, dst)
        except Exception as e:      # C.1: a failing scene shows black; the session cues a recovery
            self.failed = True
            fc.ops.fill(out, (0, 0, 0))
            fc.failed("source", self.source, e, "solid")
            return
        finally:
            if tok is not None:
                fc.timer.end(tok)
        self.n_rendered = fc.n
        fc.drawn += 1
        fc.div = max(fc.div, div)
        if dst is not out:
            fc.ops.copy(dst, out)

    def _room_clock(self, mono: float, mod: Any) -> tuple[float, float]:
        """(t_local, dt) on the room's scene clock at this frame (``mono``): the time since the room's moment, back by
        the clock offset, never backwards; a scene source's on its scene clock (x the speed master and motion), from
        where the room is when it joins late."""
        raw = mono - self.t0_mono - self._offset(mono, getattr(mod, "runtime", None))
        if self.last_t is None:                     # its first frame: the room's scene time so far, not 0
            lead_in, dt = max(0.0, raw), 0.0
            self.last_t = lead_in
        else:
            lead_in, dt = 0.0, max(0.0, raw - self.last_t)
            self.last_t = max(self.last_t, raw)
        if mod is None:
            return self.last_t, dt
        if lead_in > 0.0:
            self.scene_t += mod.scale_dt(lead_in)
        dt = mod.scale_dt(dt)
        self.scene_t += dt
        return self.scene_t, dt

    def _offset(self, mono: float, rt: Any) -> float:
        """The clock offset this frame (s): the runtime's (canon; 0 in unison), taken whole on the first frame, then
        followed while each step keeps the clock running forward at a sane rate and the runtime says its glide can be
        followed; else held (to the next cut: a new layer takes the offset then)."""
        try:
            want = float(getattr(rt, "clock_off", 0.0) or 0.0) if rt is not None else 0.0
            ok = bool(getattr(rt, "clock_off_ok", True)) if rt is not None else True
        except (TypeError, ValueError):
            want, ok = 0.0, True
        if self.off is None:
            self.off, self._off_t = want, mono
            return want
        adv = max(0.0, mono - (self._off_t if self._off_t is not None else mono))
        self._off_t = mono
        d = want - self.off
        if d != 0.0 and not self.off_held:
            if ok and -CLOCK_FAST * adv - 1e-9 <= d <= CLOCK_SLOW * adv + 1e-9:
                self.off = want
            else:
                self.off_held = True
        return self.off

    def trip(self, fc: FrameCtx, exc: BaseException) -> None:
        """Over its GPU budget (guard.py): black from the next frame, like a failing scene."""
        if self.failed:
            return
        self.failed = self.slow = True
        fc.failed("source", self.source, exc, "solid")

    def close(self, fc: FrameCtx) -> None:
        if self.hold is not None:
            fc.pool.release(self.hold)
            self.hold = None
        fc.retire(self.source)

    def describe(self) -> Any:
        return getattr(self.source, "name", type(self.source).__name__)


class TransitionLayer(Layer):
    _EPS = 1e-9

    def __init__(self, a: Layer, b: Layer, transition: Any, n_start: int, *, kind: str, tid: int,
                 notify: Notify):
        self.a, self.b = a, b
        self.transition = transition
        self.n_start = n_start
        self.kind, self.tid = kind, tid
        self.notify = notify
        self.started = False
        self.done = False
        self.done_pts: Optional[float] = None
        self.failed = False
        self.slow = False           # tripped by the GPU guard: finished as a cut

    def progress(self, fc: FrameCtx) -> float:
        if self.slow:
            return 1.0
        frames = float(self.transition.duration_s) * fc.fps
        if frames <= 0:
            return 1.0
        p = (fc.n - self.n_start) / frames
        if p <= 0.0:
            return 0.0
        return 1.0 if p >= 1.0 - self._EPS else p   # tolerance for durations that are not whole frames

    def render(self, fc: FrameCtx, out: Any) -> None:
        p = self.progress(fc)
        if not self.started:
            self.started = True
            self.notify(self.kind, "started", fc.t, self.tid, False)
        if p >= 1.0:
            self.b.render(fc, out)
            if not self.done:
                self.done = True
                self.done_pts = fc.t
                self.notify(self.kind, "done", fc.t, self.tid, False)
            return
        need_a, need_b = self.needs(p)
        if need_a and not need_b:
            self.a.render(fc, out)
            return
        if need_b and not need_a:
            self.b.render(fc, out)
            return
        buf_a, buf_b = fc.pool.acquire(), fc.pool.acquire()
        try:
            self.a.render(fc, buf_a)
            self.b.render(fc, buf_b)
            pe = ease(self.transition.easing, p)
            if self.failed:
                fc.ops.crossfade(buf_a, buf_b, out, pe)
            else:
                tok = fc.timer.begin(self, "transition", self.transition) if fc.timer is not None else None
                try:
                    self.transition.render(pe, buf_a, buf_b, out)
                except Exception as e:     # C.1: a failing transition becomes a crossfade
                    self.failed = True
                    fc.ops.crossfade(buf_a, buf_b, out, pe)
                    fc.failed("transition", self.transition, e, "crossfade")
                finally:
                    if tok is not None:
                        fc.timer.end(tok)
        finally:
            # all GPU work is on one stream, so a buffer reused later this frame is stream-ordered
            fc.pool.release(buf_a)
            fc.pool.release(buf_b)

    def trip(self, fc: FrameCtx, exc: BaseException) -> None:
        """Over its GPU budget (guard.py): the next frame shows b fully (the transition finishes as a cut)."""
        if self.slow:
            return
        self.slow = True            # (already done: nothing to cut, but the plug-in is reported slow)
        fc.failed("transition", self.transition, exc, "cut")

    def needs(self, p: float) -> tuple[bool, bool]:
        if self.failed:
            return (True, p > 0.0)
        fn = getattr(self.transition, "needs", None)
        if fn is not None:
            return fn(p)
        if getattr(self.transition, "pure_endpoints", True) and p <= 0.0:
            return True, False
        return True, True

    def collapse(self, fc: FrameCtx) -> Layer:
        if self.done:
            self.a.close(fc)
            fc.retire(self.transition)
            return self.b.collapse(fc)
        self.a = self.a.collapse(fc)
        self.b = self.b.collapse(fc)
        return self

    def close(self, fc: FrameCtx) -> None:
        if not self.started:
            self.started = True
            self.notify(self.kind, "started", fc.t, self.tid, False)
        if not self.done:
            self.done = True
            self.notify(self.kind, "done", fc.t, self.tid, True)
        self.a.close(fc)
        self.b.close(fc)
        fc.retire(self.transition)

    def walk(self) -> Iterator[Layer]:
        yield self
        yield from self.a.walk()
        yield from self.b.walk()

    def describe(self) -> Any:
        return {"transition": getattr(self.transition, "name", "?"), "kind": self.kind, "tid": self.tid,
                "a": self.a.describe(), "b": self.b.describe()}


class StillLayer(Layer):
    """A frozen frame (a pooled buffer, released on close): what an exit crossfades from instead of rendering a
    scene that is over its GPU budget (renderer.py)."""

    def __init__(self, buf: Any, release: Callable[[Any], None]):
        self.buf = buf
        self.release = release

    def render(self, fc: FrameCtx, out: Any) -> None:
        if self.buf is None:
            fc.ops.fill(out, (0, 0, 0))
        else:
            fc.ops.copy(self.buf, out)

    def close(self, fc: FrameCtx) -> None:
        buf, self.buf = self.buf, None
        if buf is not None:
            self.release(buf)

    def describe(self) -> Any:
        return "frozen frame"
