"""Per-session render engine: render thread -> NV12 ring -> writer thread -> NVENC (ffmpeg) -> fragments.

  render thread (paced on CLOCK_MONOTONIC_RAW deadlines, like the lab's _gen_loop)
     applies queued commands at the frame boundary, renders the layer tree into an RGBA frame,
     converts RGBA -> NV12 on the GPU, copies it asynchronously into a free pinned host slot
  writer thread
     waits for the slot's copy event, writes the 12 MB (4K) frame into ffmpeg's stdin
  encoder reader thread (encode.encoder.NvencPipeline)
     FLV packet -> fMP4 fragment -> posted to the asyncio loop

A ring of pinned slots lets pipe writes overlap rendering and absorbs encoder hiccups (~80 ms at 4K60).
A frame slot that starts more than one period after its deadline is counted in ``late_frames``.  If the
lateness comes from rendering (the GPU render takes longer than a frame), the previous frame is re-submitted
in that slot (a repeat) so the timeline catches up and never shifts (frame_id stays = stream seconds * fps).
If it comes from the pipe/encoder (the writer is behind), a repeat would cost the same 12 MB write and only
show a duplicate, so real content is rendered late instead and the stream catches up when the writer does.
More than RESYNC_S behind re-bases the schedule (``resyncs``).

Everything that touches the layer tree runs on the render thread; the asyncio side talks to it through a
thread-safe command queue, and the engine reports back through ``post`` (loop.call_soon_threadsafe).

Additive (LIVE_V1 1.1): a StartTransition may carry ``at_mono``, a start time on CLOCK_MONOTONIC (a predicted
beat minus the lead); it waits in a scheduled list until the frame whose time reaches it.  Each frame's time is
mapped from its pace_clock deadline with ``off = time.monotonic() - pace_clock()`` sampled that frame (R15);
``frame_hook(frame_mono)`` then runs once per rendered frame (the session samples its signals there).  Once an
``out`` transition is in the tree, later scene transitions are retired, never applied (R5).  Transitions are
prepared on a worker thread (``prepare_transition``): the render thread never compiles or allocates (R20).

Effects layer (EFFECTS_LAYER_DESIGN B.3): every source, transition and effect is set up AND primed (one
throwaway render into scratch frames) on a worker thread, so plug-ins that compile lazily on their first render
never do so on the render thread.  A scene source carrying ``fx_chain`` is wrapped in an EffectLayer; the
master bus (effects.MasterBus) runs after the whole tree: closed until the ``in`` transition is done, faded out
on exit and sealed on the frame the exit completes.  A plug-in whose render raises is substituted (layers.py,
effects.py) and reported once through ``on_plugin_error``.  Retired objects are closed on a closer thread.

GPU frame-time guard (guard.py): each plug-in call is timed with CUDA events, read a frame (or more) late without
a host sync; a plug-in over budget is tripped through the same substitutions (reported with ``reason: "slow"``).
While the GPU lags the host by ``MAX_GPU_BACKLOG`` rendered frames (1 while a new plug-in is unmeasured) a slot is
filled with a repeat instead of more GPU work (``backlog_repeats``).  An exit that starts while the tree is
tripped or over budget crossfades from a frozen copy of the last rendered frame (``frozen_exits``) instead of
rendering the scene, so it reaches the held still promptly.
"""
from __future__ import annotations

import collections
import logging
import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .effects import BUS_CLOSE_S, POOL_RESERVE, EffectLayer, FxUpdate, MasterBus, SlotUpdate, close_with_effects, \
    smoothstep
from .encode.bitstream import StreamConfig
from .encode.encoder import NvencPipeline
from .encode.fmp4 import TIMESCALE, init_segment, media_fragment
from .guard import CudaEvents, GpuTimer, SlowPlugin
from .layers import RATE_HOLD_FRAMES, FrameCtx, Layer, SourceLayer, StillLayer, TransitionLayer
from .profiles import Profile
from .stats import Ring, pct

log = logging.getLogger("stream_service.renderer")

N_SLOTS = 6
MAX_PENDING_WRITES = 5
POOL_BASE = 2        # gpu.RenderContext's preallocated frames (one transition level)
RENDER_BOUND = 0.85   # repeats only when the recent render cost exceeds this fraction of a frame period
RESYNC_S = 0.25
STARTUP_GRACE_S = 2.0  # at most this long after the first encoded frame, start-up catch-up is not counted late
RECORD_KEEP = 4096
MSG_INIT = 0x01
MSG_FRAME = 0x02


def pace_clock() -> float:
    # CLOCK_MONOTONIC on this WSL host has run ~2 % fast (kernel tick); RAW tracks real time (lab finding)
    return time.clock_gettime(getattr(time, "CLOCK_MONOTONIC_RAW", time.CLOCK_MONOTONIC))


# -- commands (asyncio side -> render thread) ---------------------------------
@dataclass
class StartTransition:
    kind: str               # "in" | "scene" | "out"
    tid: int
    source: Any             # set-up Source for the new b side
    transition: Any         # Transition instance
    at_mono: Optional[float] = None   # start on the first frame at/after this CLOCK_MONOTONIC time (None: now)


@dataclass
class UpdateParams:
    source: Any
    params: dict
    bindings: Optional[dict] = None   # the source's new bindings (None: unchanged)


@dataclass
class Retire:
    obj: Any                # a set-up source that will never be shown (e.g. a replaced pending scene)


@dataclass
class _Stop:
    pass


class EngineListener:
    """Callbacks, always invoked on the asyncio loop thread (through ``post``)."""

    def on_encoder_config(self, cfg: StreamConfig, config_msg: dict, init_msg: bytes) -> None: ...
    def on_media(self, frame_id: int, key: bool, msg: bytes) -> None: ...
    def on_transition(self, kind: str, phase: str, pts: float, tid: int, superseded: bool) -> None: ...
    def on_failed(self, detail: str) -> None: ...
    def on_plugin_error(self, info: dict) -> None: ...
    def on_fx(self, info: dict) -> None: ...   # {"retired": [ids]} | {"bus": "open"|"closed"}


__all__ = ["Engine", "EngineListener", "StartTransition", "UpdateParams", "Retire", "FxUpdate", "SlotUpdate"]


@dataclass
class _Slot:
    index: int
    host: Any               # numpy view over pinned memory
    pinned: Any             # keeps the pinned allocation alive
    ev_start: Any = None
    ev_rendered: Any = None
    ev_copied: Any = None
    refs: int = 0           # pending writes + "last frame" reference
    frame_id: int = -1


@dataclass
class _Rec:
    """Per-frame timing record (ms unless noted)."""
    n: int
    repeat: bool
    slip_ms: float
    host_ms: float = 0.0
    wait_ms: float = 0.0                   # render thread blocked waiting for a free slot / write queue
    t_start: float = 0.0                   # pace_clock() seconds when the slot was handled
    render_ms: Optional[float] = None
    copy_ms: Optional[float] = None
    write_ms: Optional[float] = None
    t_written: Optional[float] = None      # pace_clock() seconds
    encode_ms: Optional[float] = None      # written -> packet out of ffmpeg
    size: Optional[int] = None
    key: Optional[bool] = None


class Engine:
    def __init__(self, *, profile: Profile, gpu: int, post: Callable[..., None], listener: EngineListener,
                 name: str = "session"):
        self.profile = profile
        self.gpu = gpu
        self.post = post
        self.listener = listener
        self.name = name
        self.w, self.h, self.fps = profile.w, profile.h, profile.fps
        if TIMESCALE % self.fps:
            raise ValueError(f"fps {self.fps} must divide {TIMESCALE}")
        self.frame_dur = TIMESCALE // self.fps
        self.nv12_bytes = self.w * self.h * 3 // 2
        self.cmds: "queue.SimpleQueue[Any]" = queue.SimpleQueue()
        self.ctx = None
        self.root: Optional[Layer] = None
        self.stop_evt = threading.Event()
        self._lifecycle = threading.Lock()   # serialises start() / stop() (stop may come from two places)
        self._stopped = False
        self._released = False               # GPU state released: late commands are disposed of in submit()
        self.failed = False
        self.started_at: Optional[float] = None
        # stats (written by the threads, read by the loop: plain counters / deques are fine under the GIL)
        self.frames = 0           # frame slots submitted (rendered + repeated)
        self.rendered = 0
        self.late_frames = 0      # frame slots started more than one period after their deadline
        self.repeats = 0          # of those, slots filled by re-submitting the previous frame
        self.render_cost_ema = 0.0  # ms: max(GPU render, host enqueue) per rendered frame (EMA)
        self.resyncs = 0
        self.frames_written = 0
        self.frames_encoded = 0
        self.bytes_encoded = 0
        self.records: dict[int, _Rec] = {}
        self.order: collections.deque[int] = collections.deque()
        self.render_ms = Ring(RECORD_KEEP)
        self.write_ms = Ring(RECORD_KEEP)
        self.slip_ms = Ring(RECORD_KEEP)
        self.host_ms = Ring(RECORD_KEEP)
        self.encode_ms = Ring(RECORD_KEEP)
        self._pending: collections.deque = collections.deque()   # (n, slot, rendered)
        self._cv = threading.Condition()
        self._retiring: collections.deque = collections.deque()  # (cuda event, obj)
        self._last_slot: Optional[_Slot] = None
        self.pipeline: Optional[NvencPipeline] = None
        self._render_thread: Optional[threading.Thread] = None
        self._writer_thread: Optional[threading.Thread] = None
        self.stream_cfg: Optional[StreamConfig] = None
        self.frame_hook: Optional[Callable[[float], None]] = None   # render thread, once per rendered frame
        self.sound: Any = None                # sound.Sound: handed to every plug-in prepared here (the session sets it)
        self.frame_mono = 0.0                 # the current frame's time on CLOCK_MONOTONIC (s)
        self._scheduled: list[StartTransition] = []
        self._out_applied = False
        self._hook_failed = False
        self.server_ms = Ring(RECORD_KEEP)    # render start -> encoded packet out of ffmpeg
        self.resync_times: collections.deque[float] = collections.deque(maxlen=64)
        # -- effects layer (EFFECTS_LAYER_DESIGN B.3, C.1) --
        self.bus = MasterBus(None)            # the session sets bus.runtime (master bindings)
        self.plugin_errors: dict[tuple[str, str], dict] = {}
        self._reported: set[int] = set()
        self._out_layer: Optional[TransitionLayer] = None
        self._prime_lock = threading.Lock()
        self._scratch: Optional[list] = None
        self._closer_q: "queue.SimpleQueue[Any]" = queue.SimpleQueue()
        self._closer_thread: Optional[threading.Thread] = None
        self.hud = None                       # hud.HudDraw: the live diagnostics panel (made at start)
        # -- GPU frame-time guard (guard.py) --
        self.timer: Optional[GpuTimer] = None
        self._fc: Optional[FrameCtx] = None
        self.backlog_repeats = 0
        self.frozen_exits = 0
        self.slow_trips = 0

    # ------------------------------------------------------------------------
    # setup (called from a worker thread, never the asyncio loop)
    # ------------------------------------------------------------------------
    def create_context(self) -> None:
        from .gpu import RenderContext

        self.ctx = RenderContext(self.gpu, self.w, self.h, self.fps)

    def prepare(self, source: Any, params: dict) -> Any:
        """Set a source up on this engine's GPU and prime it (worker thread).  Returns it, ready for the render
        thread.  Every source, transition and effect is primed: plug-ins that compile lazily on their first
        render do so here, never on the render thread."""
        ctx = self.ctx
        cp = ctx.cp
        if self.sound is not None:
            source.sound = self.sound
        with ctx.device:
            s = cp.cuda.Stream(non_blocking=True)
            with s:
                source.setup(ctx, params)
            s.synchronize()
        self.prime(source, "source")
        if not getattr(source, "internal", False):   # a scene on a struggling GPU holds a frame (layers.py): R20
            self.ensure_pool(POOL_BASE + RATE_HOLD_FRAMES)
        return source

    def prepare_transition(self, transition: Any) -> Any:
        """Set a transition up on this engine's GPU (worker thread): kernels are loaded and buffers allocated
        here, so the render thread only starts it."""
        ctx = self.ctx
        with ctx.device:
            s = ctx.cp.cuda.Stream(non_blocking=True)
            with s:
                transition.setup(ctx)
            s.synchronize()
        self.prime(transition, "transition")
        transition.prepared = True
        return transition

    def prepare_effect(self, effect: Any, params: dict) -> Any:
        """Set an effect up and prime it (worker thread); also reserves the frame pool for effect chains."""
        ctx = self.ctx
        if self.sound is not None:
            effect.sound = self.sound
        with ctx.device:
            s = ctx.cp.cuda.Stream(non_blocking=True)
            with s:
                effect.setup(ctx, params)
            s.synchronize()
        self.prime(effect, "effect")
        self.ensure_pool(POOL_RESERVE + RATE_HOLD_FRAMES)
        return effect

    def prime(self, obj: Any, kind: str) -> None:
        """One throwaway render into scratch frames (worker thread, own stream): kernels a plug-in compiles
        lazily in render() are compiled here, never on the render thread.  Raises what the plug-in raises."""
        ctx = self.ctx
        with self._prime_lock, ctx.device:
            st = ctx.cp.cuda.Stream(non_blocking=True)
            with st:
                if self._scratch is None:
                    self._scratch = [ctx.new_frame() for _ in range(3)]
                    for f in self._scratch:
                        f.fill(0)
                s0, s1, s2 = self._scratch
                if kind == "source":
                    obj.render(0.0, 0.0, s0)
                elif kind == "transition":
                    obj.render(0.5, s0, s1, s2)
                else:
                    obj.render(0.0, 0.0, s0, s1)
            st.synchronize()

    def ensure_pool(self, n: int) -> None:
        ctx = self.ctx
        if ctx is not None and ctx.pool.allocated < n:
            with ctx.device:
                ctx.pool.reserve(n)
                ctx.cp.cuda.Device().synchronize()

    def warm(self, fn: Callable[[Any], None]) -> None:
        """Run fn(ctx) on this engine's device (worker thread), e.g. to load every plug-in's kernels."""
        with self.ctx.device:
            fn(self.ctx)

    def start(self, anchor: Any) -> None:
        """Start encoding the (prepared) anchor.  Raises if the GPU buffers or the encoder cannot start."""
        with self._lifecycle:
            if self._stopped:
                raise RuntimeError("session stopped before it started")
            self._start(anchor)

    def _start(self, anchor: Any) -> None:
        ctx = self.ctx
        cp = ctx.cp
        with ctx.device:
            self.frame = ctx.new_frame()
            self.d_nv12 = cp.empty(self.nv12_bytes, cp.uint8)
            self.slots: list[_Slot] = []
            import numpy as np

            for i in range(N_SLOTS):
                pinned = cp.cuda.alloc_pinned_memory(self.nv12_bytes)
                host = np.frombuffer(pinned, np.uint8, self.nv12_bytes)
                self.slots.append(_Slot(i, host, pinned, cp.cuda.Event(), cp.cuda.Event(),
                                        cp.cuda.Event(block=True)))
            try:                                # the details panel: everything made now, toggling allocates nothing
                from .hud import HudDraw
                self.hud = HudDraw(cp, ctx.kernels.k_hud, self.w, self.h, self.fps)
            except Exception:
                log.exception("[%s] details panel unavailable", self.name)
                self.hud = None
        self.root = SourceLayer(anchor, 0.0)
        p = self.profile
        self.pipeline = NvencPipeline(
            p.codec, self.w, self.h, self.fps, gpu=str(self.gpu), bitrate=p.bitrate, gop_seconds=p.gop_s,
            preset=p.preset, extra_args=p.encoder_args(), delay=p.nvenc_delay, clock=pace_clock,
            on_config=self._on_config, on_frame=self._on_frame, on_exit=self._on_exit)
        self.started_at = pace_clock()
        self._closer_thread = threading.Thread(target=self._closer_loop, name=f"{self.name}-closer", daemon=True)
        self._closer_thread.start()
        self._writer_thread = threading.Thread(target=self._writer_loop, name=f"{self.name}-writer", daemon=True)
        self._render_thread = threading.Thread(target=self._render_loop, name=f"{self.name}-render", daemon=True)
        self._writer_thread.start()
        self._render_thread.start()

    def submit(self, cmd: Any) -> None:
        self.cmds.put(cmd)
        # Put first, then check: _release_gpu sets _released *before* draining the queue, so a command put
        # after that drain is seen here and disposed of (a source prepared while the session ended would
        # otherwise keep its GPU buffers until garbage collection, cached in CuPy's pool).
        if self._released:
            threading.Thread(target=self._dispose_leftovers, name=f"{self.name}-dispose", daemon=True).start()

    @staticmethod
    def _carried(cmd: Any) -> list[Any]:
        """Objects a never-applied command owns (an UpdateParams' source belongs to the layer tree)."""
        if isinstance(cmd, StartTransition):
            return [cmd.source, cmd.transition]
        if isinstance(cmd, Retire):
            return [cmd.obj]
        if isinstance(cmd, FxUpdate):
            return [s.effect for s in cmd.add]
        return []

    def _drain_leftovers(self) -> None:
        while True:
            try:
                cmd = self.cmds.get_nowait()
            except queue.Empty:
                return
            for obj in self._carried(cmd):
                self._close(obj)

    def _dispose_leftovers(self) -> None:
        ctx = self.ctx
        if ctx is None:
            self._drain_leftovers()
            return
        try:
            with ctx.device:
                self._drain_leftovers()
                ctx.cp.get_default_memory_pool().free_all_blocks()
        except Exception:
            log.exception("[%s] disposing of late commands failed", self.name)

    def stop(self, timeout: float = 5.0) -> None:
        """Stop rendering and the encoder (worker thread; blocks up to a few seconds).  Idempotent."""
        self.stop_evt.set()
        with self._lifecycle:
            if self._stopped:
                return
            self._stopped = True
            self._stop(timeout)

    def _stop(self, timeout: float) -> None:
        self.cmds.put(_Stop())
        with self._cv:
            self._cv.notify_all()
        if self._render_thread is not None:
            self._render_thread.join(timeout)
        if self._writer_thread is not None:
            self._writer_thread.join(timeout)
        if self.pipeline is not None:
            self.pipeline.close()
        self._release_gpu()

    # ------------------------------------------------------------------------
    # render thread
    # ------------------------------------------------------------------------
    def _render_loop(self) -> None:
        ctx = self.ctx
        period = 1.0 / self.fps
        from .gpu import GpuOps

        fc = FrameCtx(0, self.fps, ctx.pool, retire=self._retire, ops=GpuOps(ctx), report=self._report)
        ctx.device.use()
        try:
            self.timer = fc.timer = GpuTimer(CudaEvents(ctx.cp, ctx.stream), self.fps, clock=pace_clock)
        except Exception:
            log.exception("[%s] GPU frame-time guard unavailable", self.name)
        self._fc = fc
        try:
            with ctx.stream:
                start = pace_clock() + 0.05
                n = 0
                counting, n_first_enc = False, None   # late-frame accounting (start-up excluded, see below)
                while not self.stop_evt.is_set():
                    deadline = start + n * period
                    while True:
                        delta = deadline - pace_clock()
                        if delta <= 0:
                            break
                        time.sleep(delta - 0.0015 if delta > 0.002 else 0)
                    slip = pace_clock() - deadline
                    if slip > RESYNC_S:
                        # far behind (encoder / pipe slower than real time): re-base instead of repeating forever
                        start += slip
                        slip = 0.0
                        self.resyncs += 1
                        self.resync_times.append(pace_clock())
                    fc.n = n
                    self.frame_mono = deadline + (time.monotonic() - pace_clock())
                    self._poll_timer()                # trips land before this frame's commands (an exit)
                    self._drain_commands(fc)
                    if self.stop_evt.is_set():
                        break
                    self._poll_retired()
                    late = slip > period
                    # Start-up is not counted: NVENC initialisation blocks the first pipe writes for ~0.3 s, and
                    # the catch-up after it (while the anchor is held) would count a few slots late.  Counting
                    # starts at the first on-time slot after the first encoded frame, or STARTUP_GRACE_S after
                    # it at the latest (so a pipeline that never catches up is still reported).
                    if not counting and self.frames_encoded > 0:
                        if n_first_enc is None:
                            n_first_enc = n
                        counting = not late or n - n_first_enc >= STARTUP_GRACE_S * self.fps
                    if late and counting:
                        self.late_frames += 1
                    if late and self._last_slot is not None and \
                            self.render_cost_ema > RENDER_BOUND * period * 1000:
                        self.repeats += 1
                        self._submit_repeat(n, slip)
                    elif self._last_slot is not None and self._gpu_behind():
                        # the GPU is still on earlier frames: queueing more work would only stall longer
                        self.repeats += 1
                        self.backlog_repeats += 1
                        self._submit_repeat(n, slip)
                    else:
                        self._render_frame(fc, slip)
                    n += 1
                    self.frames = n
        except Exception as e:
            if not self.stop_evt.is_set():
                detail = f"render error: {type(e).__name__}: {e}"
                log.error("[%s] %s\n%s", self.name, detail, traceback.format_exc())
                self._fail(detail)
        finally:
            with self._cv:
                self._pending.append(None)  # writer: stop
                self._cv.notify_all()

    def _poll_timer(self) -> None:
        """Read finished GPU timings and trip slow plug-ins (render thread; never blocks)."""
        t = self.timer
        if t is None or self._fc is None:
            return
        try:
            t.poll(self._fc)
        except Exception:                     # the guard must never take the stream down
            log.exception("[%s] GPU frame-time guard failed; disabled for this session", self.name)
            self.timer = self._fc.timer = None

    def _gpu_behind(self) -> bool:
        t = self.timer
        return t is not None and t.pending >= t.backlog_limit()

    def _drain_commands(self, fc: FrameCtx) -> None:
        while self._scheduled and self._scheduled[0].at_mono <= self.frame_mono:
            self._start_transition(fc, self._scheduled.pop(0))
        while True:
            try:
                cmd = self.cmds.get_nowait()
            except queue.Empty:
                return
            if isinstance(cmd, _Stop):
                self.stop_evt.set()
                return
            if isinstance(cmd, StartTransition):
                if cmd.kind == "scene" and cmd.at_mono is not None and cmd.at_mono > self.frame_mono \
                        and not self._out_applied:
                    self._scheduled.append(cmd)       # waits for its beat
                    self._scheduled.sort(key=lambda c: c.at_mono)
                    continue
                if cmd.kind == "out":                 # exit pre-empts every scheduled scene change (R5)
                    for late in self._scheduled:
                        self._retire(late.source)
                        self._retire(late.transition)
                    self._scheduled.clear()
                else:                                 # an immediate change starts anything still waiting first
                    while self._scheduled:
                        self._start_transition(fc, self._scheduled.pop(0))
                self._start_transition(fc, cmd)
            elif isinstance(cmd, UpdateParams):
                cmd.source.update(cmd.params)
                if cmd.bindings is not None and getattr(cmd.source, "modulation", None) is not None:
                    cmd.source.modulation.set_bindings(cmd.bindings)
            elif isinstance(cmd, Retire):
                self._retire(cmd.obj)
            elif isinstance(cmd, FxUpdate):
                if self.bus.sealed:                   # exit completed: the bus never opens again
                    for slot in cmd.add:
                        self._retire(slot.effect)
                else:
                    self.bus.apply(cmd, fc)

    def _start_transition(self, fc: FrameCtx, cmd: StartTransition) -> None:
        if cmd.kind == "scene" and self._out_applied:   # never nest a scene change over the exit (R5)
            self._retire(cmd.source)
            self._retire(cmd.transition)
            return
        frozen = False
        if cmd.kind == "out":
            self._out_applied = True
            frozen = self._freeze_if_slow(fc)
        b: Layer = SourceLayer(cmd.source, fc.t)
        chain = getattr(cmd.source, "fx_chain", None)
        if chain is not None:
            b = EffectLayer(b, chain)
        if not getattr(cmd.transition, "prepared", False):
            cmd.transition.setup(self.ctx)            # v1 callers that did not prepare it (cut, crossfade: cheap)
        self.root = TransitionLayer(self.root, b, cmd.transition, fc.n, kind=cmd.kind, tid=cmd.tid,
                                    notify=self._notify)
        if cmd.kind == "out":
            # the bus fades out within the exit and is sealed on the frame it completes (B.4)
            self._out_layer = self.root
            # (from a frozen frame the bus is already in it: closed at once, never applied twice)
            self.bus.close(0.0 if frozen else min(BUS_CLOSE_S, float(cmd.transition.duration_s)))
            if not self.stop_evt.is_set():
                self.post(self.listener.on_fx, {"bus": "closed"})

    def _tree_slow(self) -> bool:
        """Is anything in the tree (or on the bus) tripped, or the tree over its GPU budget?"""
        for layer in self.root.walk():
            if getattr(layer, "slow", False):
                return True
            chain = getattr(layer, "chain", None)
            if chain is not None and any(getattr(s, "slow", False) for s in chain.slots):
                return True
        if any(getattr(s, "slow", False) and not s.done for s in self.bus.chain.slots):
            return True
        return self.timer is not None and self.timer.over_budget()

    def _freeze_if_slow(self, fc: FrameCtx) -> bool:
        """An exit over a slow / tripped tree: replace the tree by a frozen copy of the last rendered frame, so the
        out transition never renders the scene (it reaches the held still promptly).  -> frozen?"""
        if self.root is None or self.rendered == 0 or getattr(self, "frame", None) is None \
                or isinstance(self.root, StillLayer):
            return False
        try:
            if not self._tree_slow():
                return False
        except Exception:
            log.exception("[%s] exit: checking the tree's GPU cost failed", self.name)
            return False
        buf = fc.pool.acquire()
        fc.ops.copy(self.frame, buf)          # stream-ordered: the last rendered frame, as shown
        old, self.root = self.root, StillLayer(buf, fc.pool.release)
        old.close(fc)                         # unfinished transitions report done (superseded); sources retire
        self.frozen_exits += 1
        log.info("[%s] exit from a frozen frame (the tree was tripped or over its GPU budget)", self.name)
        return True

    def _notify(self, kind: str, phase: str, pts: float, tid: int, superseded: bool) -> None:
        if kind == "in" and phase == "done" and not superseded and not self._out_applied:
            self.bus.open()
            if not self.stop_evt.is_set():
                self.post(self.listener.on_fx, {"bus": "open"})
        if not self.stop_evt.is_set():  # tear-down closes unfinished transitions: not worth reporting
            self.post(self.listener.on_transition, kind, phase, pts, tid, superseded)

    def _report(self, kind: str, obj: Any, exc: BaseException, action: str) -> None:
        """A plug-in failed during render and was substituted (render thread; C.1).  Counted per (kind, name);
        logged and posted once per object."""
        extra: dict = {}
        if kind == "effect":
            name = getattr(obj, "name", "?")
            extra = {"slot": getattr(obj, "slot_id", None), "preset": getattr(obj, "preset", None),
                     "where": getattr(obj, "where", None)}
        else:
            name = getattr(obj, "name", None) or type(obj).__name__
        if isinstance(exc, SlowPlugin):       # the GPU frame-time guard tripped it (guard.py)
            self.slow_trips += 1
            extra.update(reason="slow", gpu_ms=round(exc.ms, 1), budget_ms=round(exc.budget_ms, 1), why=exc.why,
                         profile=self.profile.name)
        detail = f"{type(exc).__name__}: {exc}"[:500]
        now = time.time()
        rec = self.plugin_errors.setdefault((kind, name), {"count": 0, "first_at": now, "action": action})
        rec.update(count=rec["count"] + 1, last_at=now, detail=detail, action=action)
        if id(obj) in self._reported:
            return
        self._reported.add(id(obj))
        if isinstance(exc, SlowPlugin):
            log.error("[%s] %s %s over its GPU budget (%s): %s", self.name, kind, name, action, detail)
        else:
            log.error("[%s] %s %s failed in render (%s): %s\n%s", self.name, kind, name, action, detail,
                      "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-2000:])
        if not self.stop_evt.is_set():
            self.post(self.listener.on_plugin_error,
                      {"kind": kind, "name": name, "phase": "render", "action": action, "detail": detail,
                       "obj_id": id(obj), "count": rec["count"], **extra})

    def _acquire_slot(self) -> Optional[_Slot]:
        with self._cv:
            while not self.stop_evt.is_set():
                if len(self._pending) < MAX_PENDING_WRITES:
                    for s in self.slots:
                        if s.refs == 0:
                            return s
                self._cv.wait(0.1)
                self._poll_timer()            # a stuck plug-in trips while we wait for the GPU
        return None

    def _render_frame(self, fc: FrameCtx, slip: float) -> None:
        cp = self.ctx.cp
        stream = self.ctx.stream
        tw = pace_clock()
        slot = self._acquire_slot()
        if slot is None:
            return
        t0 = pace_clock()
        if self.frame_hook is not None and not self._hook_failed:
            try:
                self.frame_hook(self.frame_mono)
            except Exception:   # modulation must never take the stream down: render unmodulated from here on
                self._hook_failed = True
                log.exception("[%s] frame hook failed; signals frozen for this session", self.name)
        slot.ev_start.record(stream)
        timer = self.timer
        if timer is not None:
            timer.frame_begin()
        if self._out_layer is not None and not self.bus.sealed and self._out_layer.progress(fc) >= 1.0:
            self.bus.seal()                     # the exit's last frame and the hold are the pure target (B.4)
        root = self.root
        if self.bus.sealed and not self.bus.chain.slots:
            root.render(fc, self.frame)
        else:
            try:
                self.bus.render(fc, lambda buf: root.render(fc, buf), self.frame)
            except Exception as e:              # an engine bug in the bus: bypass it for the session (C.1)
                self.bus.seal()
                self._report("bus", self.bus, e, "bypassed")
                root.render(fc, self.frame)
            retired = self.bus.chain.retire_done(fc)
            if retired and not self.stop_evt.is_set():
                self.post(self.listener.on_fx, {"retired": retired})
        self.ctx.kernels.rgba_to_nv12(self.frame, self.d_nv12, self.w, self.h)
        hud = self.hud
        if hud is not None and hud.failed is None and (hud.on or hud.fade > 0.0):
            try:                                # on the encoded copy only: self.frame (the frozen exit) stays clean
                hud.draw(self.d_nv12, 0.0 if self.bus.sealed else smoothstep(self.bus.level), fc.n, stream)
            except Exception:
                hud._fail("stopped after an error")
                log.exception("[%s] details panel stopped", self.name)
        slot.ev_rendered.record(stream)
        if timer is not None and self.timer is timer:
            timer.frame_end()
        cp.cuda.runtime.memcpyAsync(slot.host.ctypes.data, self.d_nv12.data.ptr, self.nv12_bytes,
                                    cp.cuda.runtime.memcpyDeviceToHost, stream.ptr)
        slot.ev_copied.record(stream)
        self.root = self.root.collapse(fc)
        if self._out_layer is not None and self._out_layer.done:
            self._out_layer = None
        host_ms = (pace_clock() - t0) * 1000
        self.rendered += 1
        rec = _Rec(fc.n, False, slip * 1000, host_ms, wait_ms=(t0 - tw) * 1000, t_start=tw)
        self._keep(rec)
        self.slip_ms.add(rec.slip_ms)
        self.host_ms.add(host_ms)
        with self._cv:
            slot.frame_id = fc.n
            slot.refs += 1                      # the pending write
            self._set_last(slot)
            self._pending.append((fc.n, slot, True))
            self._cv.notify_all()

    def _submit_repeat(self, n: int, slip: float) -> None:
        tw = pace_clock()
        rec = _Rec(n, True, slip * 1000, t_start=tw)
        self._keep(rec)
        self.slip_ms.add(rec.slip_ms)
        with self._cv:
            while len(self._pending) >= MAX_PENDING_WRITES and not self.stop_evt.is_set():
                self._cv.wait(0.1)
                self._poll_timer()
            rec.wait_ms = (pace_clock() - tw) * 1000
            slot = self._last_slot
            slot.refs += 1
            self._pending.append((n, slot, False))
            self._cv.notify_all()

    def _set_last(self, slot: _Slot) -> None:
        # called with _cv held: the newest frame stays referenced so a late slot can repeat it
        if self._last_slot is not slot:
            if self._last_slot is not None:
                self._last_slot.refs -= 1
            slot.refs += 1
            self._last_slot = slot

    def _keep(self, rec: _Rec) -> None:
        self.records[rec.n] = rec
        self.order.append(rec.n)
        while len(self.order) > RECORD_KEEP:
            self.records.pop(self.order.popleft(), None)

    # Deferred close: a source dropped from the tree may still be read by work queued on the render stream
    # (the host runs up to a few frames ahead of the GPU), and close() releases its arrays to CuPy's pool.
    # So record an event now and close() once the GPU has passed it.
    def _retire(self, obj: Any) -> None:
        ev = self.ctx.cp.cuda.Event(disable_timing=True)
        ev.record(self.ctx.stream)
        self._retiring.append((ev, obj))

    def _poll_retired(self) -> None:
        while self._retiring and self._retiring[0][0].done:
            _, obj = self._retiring.popleft()
            if self._closer_thread is not None and self._closer_thread.is_alive():
                self._closer_q.put(obj)          # close() off the render thread (lab plug-ins may be slow)
            else:
                self._close(obj)

    def _close(self, obj: Any) -> None:
        try:
            close_with_effects(obj)
        except Exception:
            log.exception("[%s] close() failed for %r", self.name, obj)

    def _closer_loop(self) -> None:
        ctx = self.ctx
        ctx.device.use()
        while True:
            obj = self._closer_q.get()
            if obj is None:
                return
            self._close(obj)

    def _drain_closer(self) -> None:
        """Stop the closer thread and close whatever it had not reached yet (worker thread, at release)."""
        if self._closer_thread is not None:
            self._closer_q.put(None)
            self._closer_thread.join(5.0)
            self._closer_thread = None
        while True:
            try:
                obj = self._closer_q.get_nowait()
            except queue.Empty:
                return
            if obj is not None:
                self._close(obj)

    # ------------------------------------------------------------------------
    # writer thread
    # ------------------------------------------------------------------------
    def _writer_loop(self) -> None:
        cp = self.ctx.cp
        self.ctx.device.use()
        try:
            while True:
                with self._cv:
                    while not self._pending:
                        self._cv.wait(0.1)
                    item = self._pending[0]
                if item is None or self.stop_evt.is_set():
                    return
                n, slot, rendered = item
                slot.ev_copied.synchronize()
                rec = self.records.get(n)
                if rendered and rec is not None:
                    rec.render_ms = cp.cuda.get_elapsed_time(slot.ev_start, slot.ev_rendered)
                    rec.copy_ms = cp.cuda.get_elapsed_time(slot.ev_rendered, slot.ev_copied)
                    self.render_ms.add(rec.render_ms)
                    cost = max(rec.render_ms, rec.host_ms)
                    self.render_cost_ema += 0.2 * (cost - self.render_cost_ema)
                t0 = pace_clock()
                try:
                    self.pipeline.submit(memoryview(slot.host))
                except (BrokenPipeError, ValueError, OSError) as e:
                    if not self.stop_evt.is_set():
                        self._fail(f"encoder input closed: {type(e).__name__}: {e}")
                    return
                t1 = pace_clock()
                self.frames_written += 1
                if rec is not None:
                    rec.write_ms = (t1 - t0) * 1000
                    rec.t_written = t1
                    self.write_ms.add(rec.write_ms)
                with self._cv:
                    self._pending.popleft()
                    slot.refs -= 1
                    self._cv.notify_all()
        finally:
            with self._cv:
                self._cv.notify_all()

    # ------------------------------------------------------------------------
    # encoder callbacks (reader thread)
    # ------------------------------------------------------------------------
    def _on_config(self, scfg: StreamConfig) -> None:
        self.stream_cfg = scfg
        init = init_segment(scfg, colr=self.profile.colr())
        config_msg = {"type": "config", "codec": self.profile.codec, "codec_string": scfg.codec_string,
                      "width": self.w, "height": self.h, "fps": self.fps, "timescale": TIMESCALE,
                      "frame_duration": self.frame_dur}
        self.post(self.listener.on_encoder_config, scfg, config_msg, bytes([MSG_INIT]) + init)

    def _on_frame(self, frame_id: int, key: bool, sample: bytes, t_encoded: float) -> None:
        self.frames_encoded += 1
        self.bytes_encoded += len(sample)
        rec = self.records.get(frame_id)
        if rec is not None:
            rec.size, rec.key = len(sample), key
            if rec.t_start:
                self.server_ms.add((t_encoded - rec.t_start) * 1000)
            if rec.t_written is not None:
                rec.encode_ms = (t_encoded - rec.t_written) * 1000
                self.encode_ms.add(rec.encode_ms)
        frag, _ = media_fragment(frame_id + 1, frame_id * self.frame_dur, self.frame_dur, sample, key)
        msg = bytes([MSG_FRAME, 1 if key else 0]) + frame_id.to_bytes(4, "big") + frag
        self.post(self.listener.on_media, frame_id, key, msg)

    def _on_exit(self, rc: int, tail: str) -> None:
        if not self.stop_evt.is_set():
            self._fail(f"encoder exited rc={rc}: {tail[-600:]}")

    def _fail(self, detail: str) -> None:
        if self.failed:
            return
        self.failed = True
        self.stop_evt.set()
        with self._cv:
            self._cv.notify_all()
        self.post(self.listener.on_failed, detail)

    # ------------------------------------------------------------------------
    def _release_gpu(self) -> None:
        """Drop GPU references and give the cached memory back to the driver (GPU 0 is shared)."""
        ctx = self.ctx
        if ctx is None:
            self._released = True
            self._drain_leftovers()
            return
        try:
            with ctx.device:
                ctx.stream.synchronize()
                if self.root is not None:
                    fc = FrameCtx(self.frames, self.fps, ctx.pool, retire=self._close)
                    self.root.close(fc)
                self.root = None
                for cmd in self._scheduled:
                    self._close(cmd.source)
                    self._close(cmd.transition)
                self._scheduled.clear()
                self.bus.chain.close_now()
                self._out_layer = None
                while self._retiring:
                    self._close(self._retiring.popleft()[1])
                self._drain_closer()
                self._scratch = None
                self.timer = None
                if self._fc is not None:
                    self._fc.timer = None
                # commands that never reached the render thread may carry set-up sources; from here on
                # submit() disposes of late ones itself (see submit)
                self._released = True
                self._drain_leftovers()
                self.frame = None
                self.d_nv12 = None
                self.slots = []
                self._last_slot = None
                ctx.pool.free.clear()
                ctx.cp.get_default_memory_pool().free_all_blocks()
                ctx.cp.get_default_pinned_memory_pool().free_all_blocks()
        except Exception:
            log.exception("[%s] GPU release failed", self.name)
        finally:
            if not self._released:  # failed part-way: still dispose of queued sources, now and later
                self._released = True
                self._drain_leftovers()

    # ------------------------------------------------------------------------
    def summary(self) -> dict:
        backlog = len(self._pending)
        in_encoder = max(0, self.frames_written - self.frames_encoded)
        return {
            "frames": self.frames, "rendered": self.rendered, "late_frames": self.late_frames,
            "repeats": self.repeats,
            "resyncs": self.resyncs, "frames_encoded": self.frames_encoded,
            "render_ms": self.render_ms.summary(), "write_ms": self.write_ms.summary(),
            "slip_ms": self.slip_ms.summary(), "host_ms": self.host_ms.summary(),
            "encode_ms": self.encode_ms.summary(),
            "encode_queue": backlog + in_encoder,
            "bitrate_mbps": self._recent_bitrate(),
            "gpu_guard": self.guard_stats(),
        }

    def guard_stats(self) -> dict:
        t = self.timer
        return {**(t.stats() if t is not None else {"samples": 0, "trips": 0, "backlog": 0}),
                "enabled": t is not None, "slow_trips": self.slow_trips, "backlog_repeats": self.backlog_repeats,
                "frozen_exits": self.frozen_exits}

    def window_stats(self, seconds: float = 2.0) -> dict:
        """Diagnostics over the last ``seconds`` (records whose slot started in the window; loop thread)."""
        now = pace_clock()
        recs = []
        for n in reversed(list(self.order)[-(int(self.fps * seconds) + self.fps):]):
            r = self.records.get(n)
            if r is None or now - r.t_start > seconds:
                break
            recs.append(r)
        period_ms = 1000.0 / self.fps

        def dist(key: str) -> dict:
            vals = [getattr(r, key) for r in recs if getattr(r, key) is not None]
            rnd = lambda v: None if v is None else round(v, 2)  # noqa: E731
            return {"p50": rnd(pct(vals, 50)), "p95": rnd(pct(vals, 95)), "max": rnd(max(vals) if vals else None)}

        sizes = sum(r.size for r in recs if r.size)
        return {
            "window_s": seconds, "frames": len(recs), "budget_ms": round(period_ms, 2),
            "render_ms": dist("render_ms"), "write_ms": dist("write_ms"), "encode_ms": dist("encode_ms"),
            "slip_ms": dist("slip_ms"), "host_ms": dist("host_ms"),
            "late_pct": round(100.0 * sum(1 for r in recs if r.slip_ms > period_ms) / len(recs), 1) if recs else None,
            "repeats": sum(1 for r in recs if r.repeat),
            "resyncs": sum(1 for t in self.resync_times if now - t <= seconds),
            "encode_queue": len(self._pending) + max(0, self.frames_written - self.frames_encoded),
            "mbps": round(sizes * 8 / 1e6 / seconds, 2),
            "nvenc_delay_frames": self.profile.nvenc_delay,
        }

    def _recent_bitrate(self) -> Optional[float]:
        """Encoded Mbit/s over the last second of frames."""
        sizes = []
        for n in list(self.order)[-(self.fps + 30):]:
            r = self.records.get(n)
            if r is not None and r.size is not None:
                sizes.append(r.size)
        if len(sizes) < self.fps // 2:
            return None
        return round(sum(sizes[-self.fps:]) * 8 / 1e6 * self.fps / min(len(sizes), self.fps), 2)

    def frame_records(self, since: int = -1, limit: int = RECORD_KEEP) -> list[dict]:
        out = []
        for n in list(self.order):
            if n <= since:
                continue
            r = self.records.get(n)
            if r is not None:
                out.append({k: v for k, v in r.__dict__.items()})
            if len(out) >= limit:
                break
        return out
