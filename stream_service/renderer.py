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

A run of repeats is a freeze on the glass: after ``MAX_REPEAT_RUN`` repeats in a row the next slot that would repeat
is rendered instead (a probe frame, ``probe_frames``), so at most 3 repeats come in a row.  Each rendered frame
records its host time split into own CPU, run-queue wait and the rest (blocked: GIL, locks, syscalls), and whether
the scene was drawn or held (rate_div), so content fps is known.

GPU-resident encode (4K60 plan change 2; ``encoder = "nvenc"``, set by the session from stream_switches.json): the
slots are NV12 device surfaces instead of pinned host memory, the render thread converts straight into the slot (no
D2H copy) and the writer thread becomes the encode thread (encode.nvenc.GpuEncoder: Encode() on that surface, then
fMP4 as today).  It forces IDRs on the profile's GOP grid (frame ids that are multiples of gop_s * fps, as today),
on the frame at ``hold_pts`` (the exit's last frame) and when a player joins.  If the GPU encoder cannot be created
the session starts on today's ffmpeg path; if it fails mid-session the encode thread copies the surfaces down and
feeds a new ffmpeg pipeline (frame ids continue; the frames NVENC held back are re-sent as the current one) and the
session keeps playing: ``encoder_fallback`` says why (summary, details panel).  Degrade, never refuse.

Degradation controller (4K60 plan change 5, degrade.py; ``degrade_mode`` "on" | "off", set by the session from
stream_switches.json): decided on the render thread once a second, applied at a frame boundary.  Its scene-rate step
is ``FrameCtx.rate_floor`` (layers.py); its fps step (GPU encoder at 60 fps only) is ``step``: frame ids stay on the
profile's grid (``n`` = stream seconds x fps; the page's key times are fid / fps) and advance by ``step`` per output
slot, the pacing deadlines with them; the step changes on a frame id that is a multiple of it, so the GOP's IDR grid
still lands; the encode thread Reconfigure()s NVENC before the first frame of the new rate; each fMP4 sample lasts its
slot's step in ticks, so the timeline stays contiguous (tfdt = id x tick).  The FX trim reaches the session's FX
director through ``on_degrade``.  The repeat rule stays the per-slot catch-up; the scene's own rate divider stays its
own (the floor bounds it from below).
"""
from __future__ import annotations

import collections
import gc
import logging
import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .degrade import DECIDE_S, Controller, Ctx, off_state
from .effects import BUS_CLOSE_S, POOL_RESERVE, EffectLayer, FxUpdate, MasterBus, SlotUpdate, close_with_effects, \
    smoothstep
from .encode.bitstream import StreamConfig
from .encode.encoder import NvencPipeline
from .encode.fmp4 import TIMESCALE, init_segment, media_fragment
from .guard import CudaEvents, GpuTimer, SlowPlugin
from .layers import RATE_HOLD_FRAMES, FrameCtx, Layer, SourceLayer, StillLayer, TransitionLayer
from .profiles import Profile
from .stats import Ring, ThreadMeter, pct_sorted, set_thread_name, split_ms

log = logging.getLogger("stream_service.renderer")

N_SLOTS = 6
MAX_PENDING_WRITES = 5
POOL_BASE = 2        # gpu.RenderContext's preallocated frames (one transition level)
RENDER_BOUND = 0.85   # repeats only when the recent render cost exceeds this fraction of a frame period
RESYNC_S = 0.25
# Paced garbage collection (gc_paced, set by a render worker that turned automatic GC off for its session): collections
# run in the sleep before a frame's deadline, only with this much slack left, never inside a render.  Automatic GC put
# gen2 pauses of 80-160 ms and gen1 of up to 27 ms inside live score renders (7c's fluency audit, 21 Oct).
GC_SLACK_S = (0.002, 0.004, 0.008)      # gen0 / gen1 / gen2: slack needed
GC_EVERY_S = (0.0, 0.5, 20.0)           # each generation at most this often
GC_GEN2_MAX_S = 120.0                   # gen2 runs at the next frame regardless after this long (memory stays bounded)
GC_GEN0_PILE = 10                       # gen0 regardless of slack once its count is this many times its threshold (ff: at
                                        # the degrade floor there is no slack, and garbage would pile up for the gen2)
STARTUP_GRACE_S = 2.0  # at most this long after the first encoded frame, start-up catch-up is not counted late
RECORD_KEEP = 4096
MAX_REPEAT_RUN = 3    # repeats in a row before a slot that would repeat is rendered anyway (a probe frame)
MSG_INIT = 0x01
MSG_FRAME = 0x02


def pace_clock() -> float:
    # CLOCK_MONOTONIC on this WSL host has run ~2 % fast (kernel tick); RAW tracks real time (lab finding)
    return time.clock_gettime(getattr(time, "CLOCK_MONOTONIC_RAW", time.CLOCK_MONOTONIC))


def gpu_encoder_args(p: Profile) -> dict:
    """encode.nvenc.GpuEncoder's settings for a profile (also a render worker's key for an encoder made ahead)."""
    return {"codec": p.codec, "width": p.w, "height": p.h, "fps": p.fps, "bitrate": p.bitrate, "gop_seconds": p.gop_s,
            "colr": p.colr(), "vbv_s": getattr(p, "vbv_s", None), "level": getattr(p, "level", None)}


def slot_action(late: bool, have_last: bool, cost_ema_ms: float, period_ms: float, gpu_behind: Callable[[], bool],
                run: int) -> str:
    """What a frame slot does: "render"; "repeat" (late and render-bound); "backlog" (the GPU is behind); or
    "probe": a slot that would repeat, rendered because ``run`` repeats came in a row already."""
    if not have_last:
        return "render"
    if late and cost_ema_ms > RENDER_BOUND * period_ms:
        why = "repeat"
    elif gpu_behind():
        why = "backlog"       # the GPU is still on earlier frames: queueing more work would only stall longer
    else:
        return "render"
    return "probe" if run >= MAX_REPEAT_RUN else why


def _low_stream(ctx: Any) -> Any:
    from .gpu import LOW_PRIORITY, priority_stream

    return priority_stream(ctx.cp, LOW_PRIORITY)


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
    def on_degrade(self, info: dict) -> None: ...   # the degradation controller stepped (degrade.Controller.state())


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
    dev: Any = None         # GPU encoder: the slot's NV12 device surface (host/pinned are then None)
    surface: Any = None     # ... as the encoder takes it (nvenc.Surface)


@dataclass
class _Rec:
    """Per-frame timing record (ms unless noted)."""
    n: int
    repeat: bool
    slip_ms: float                         # before any resync re-bases the schedule
    host_ms: Optional[float] = None        # rendered frames only (a repeat renders nothing)
    wait_ms: float = 0.0                   # render thread blocked waiting for a free slot / write queue
    t_start: float = 0.0                   # pace_clock() seconds when the slot was handled
    render_ms: Optional[float] = None
    copy_ms: Optional[float] = None
    write_ms: Optional[float] = None
    t_written: Optional[float] = None      # pace_clock() seconds
    encode_ms: Optional[float] = None      # written -> packet out of ffmpeg
    size: Optional[int] = None
    key: Optional[bool] = None
    cpu_ms: Optional[float] = None         # the render thread's own CPU over host_ms (time.thread_time)
    runq_ms: Optional[float] = None        # runnable but not running (schedstat); None if unavailable
    blocked_ms: Optional[float] = None     # host - cpu - runq (>= 0): GIL, locks, syscalls
    div: Optional[int] = None              # the scene's rate_div this frame
    held: Optional[bool] = None            # the scene's last frame was shown again (rate_div), nothing drawn
    probe: bool = False                    # rendered after MAX_REPEAT_RUN repeats in a row
    enc_cpu_ms: Optional[float] = None     # the writer / encode thread's own CPU for this slot (pipe write or Encode)
    enc_runq_ms: Optional[float] = None    # ... and its run-queue wait over the same span (schedstat; 0 if unreadable)


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
        self.gc_paced = False     # collect garbage in the slack before deadlines (worker.py turns automatic GC off)
        self.gc_max_ms = [0.0, 0.0, 0.0]   # the longest paced collection seen, per generation
        self.gc_runs = [0, 0, 0]           # paced collections run, per generation
        self.repeats = 0          # of those, slots filled by re-submitting the previous frame
        self.probe_frames = 0     # slots that would have repeated, rendered after MAX_REPEAT_RUN repeats in a row
        self.content_frames = 0   # rendered frames whose scene was drawn (not held by rate_div)
        self.held_frames = 0      # rendered frames whose scene was held
        self._repeat_run = 0
        self._meter: Optional[ThreadMeter] = None   # the render thread's (opened on it)
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
        self.cpu_ms = Ring(RECORD_KEEP)
        self.runq_ms = Ring(RECORD_KEEP)
        self.blocked_ms = Ring(RECORD_KEEP)
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
        # -- encoder (4K60 plan change 2): "nvenc" | "ffmpeg", set by the session before start() --
        self.encoder = "ffmpeg"
        self.encoder_active: Optional[str] = None
        self.encoder_fallback: Optional[str] = None   # why this session runs on ffmpeg although nvenc was wanted
        self._gpu_enc: Any = None
        self.gpu_encoder_ready: Optional[Callable[[], Any]] = None   # a render worker's encoder made ahead (worker.py)
        self._gop = max(1, round(profile.gop_s * profile.fps))
        self._idr_frames: set[int] = set()            # render thread: frame ids that go out as IDRs (hold_pts)
        self._idr_req = self._idr_seen = 0            # loop thread asks (a player joined); encode thread serves
        self._next_id = 0                             # the next frame id the players expect
        self.forced_idrs = 0                          # IDRs off the grid: hold frames and joining players
        self._fb_host: Any = None                     # mid-session fallback: one pinned frame + a copy stream
        self._fb_stream: Any = None
        self.enc_cpu_ms = Ring(RECORD_KEEP)
        self.backlog_repeats = 0
        self.frozen_exits = 0
        self.slow_trips = 0
        # -- the degradation controller (plan change 5): "on" | "off", set by the session before start() --
        self.degrade_mode = "on"
        self.degrade: Optional[Controller] = None
        self.step = 1                                 # frame ids per output slot (2: the fps step, 60 -> 30)
        self._step_want = 1
        self._steps: dict[int, int] = {}              # frame id -> its slot's step while not 1 (the sample duration)
        self._enc_step = 1                            # encode thread: the step NVENC is configured for
        self._fps_lost: Optional[str] = None          # the GPU encoder fell back: no fps step any more
        self._dg_next = 0.0
        self.fps_steps = 0
        self.reconfigure_ms: Optional[float] = None
        self.reconfigure_error: Optional[str] = None

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
            s = _low_stream(ctx)
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
            s = _low_stream(ctx)
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
            s = _low_stream(ctx)
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
            st = _low_stream(ctx)
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
            gpu_enc = self._make_gpu_encoder() if self.encoder == "nvenc" else None
            self.d_nv12 = None if gpu_enc is not None else cp.empty(self.nv12_bytes, cp.uint8)
            self.slots: list[_Slot] = []
            import numpy as np

            for i in range(N_SLOTS):
                if gpu_enc is not None:                  # NV12 surfaces on the GPU: NVENC reads them there
                    dev = cp.empty(self.nv12_bytes, cp.uint8)
                    self.slots.append(_Slot(i, None, None, cp.cuda.Event(), cp.cuda.Event(),
                                            cp.cuda.Event(block=True), dev=dev, surface=gpu_enc.surface(dev)))
                    continue
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
        self._gpu_enc = gpu_enc
        self.degrade = Controller(self.fps) if self.degrade_mode != "off" else None
        self.pipeline = gpu_enc if gpu_enc is not None else self._ffmpeg_pipeline()
        self.encoder_active = "nvenc" if gpu_enc is not None else "ffmpeg"
        self.started_at = pace_clock()
        self._closer_thread = threading.Thread(target=self._closer_loop, name=f"{self.name}-closer", daemon=True)
        self._closer_thread.start()
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True,
                                               name=f"{self.name}-{'nvenc' if gpu_enc is not None else 'writer'}")
        self._render_thread = threading.Thread(target=self._render_loop, name=f"{self.name}-render", daemon=True)
        self._writer_thread.start()
        self._render_thread.start()

    def _tag(self) -> str:
        """The session in OS thread names (15 bytes with a role prefix)."""
        return self.name[2:] if self.name.startswith("s-") else self.name

    def _ffmpeg_pipeline(self, id_base: int = 0) -> NvencPipeline:
        p = self.profile
        return NvencPipeline(
            p.codec, self.w, self.h, self.fps, gpu=str(self.gpu), bitrate=p.bitrate, gop_seconds=p.gop_s,
            preset=p.preset, extra_args=p.encoder_args(), delay=p.nvenc_delay, name=self._tag(), id_base=id_base,
            clock=pace_clock, on_config=self._on_config, on_frame=self._on_frame, on_exit=self._on_exit)

    def _make_gpu_encoder(self) -> Any:
        """The GPU encoder on this engine's context and render stream (worker thread, device current), or None
        and ``encoder_fallback`` set: the session then starts on the ffmpeg pipe.  ``gpu_encoder_ready`` (a render
        worker's, worker.py): one made ahead on this context's render stream, taken instead of a new one."""
        ready, self.gpu_encoder_ready = self.gpu_encoder_ready, None
        if ready is not None:
            enc = None
            try:
                enc = ready()
                enc.adopt(self._tag(), self._on_config, self._on_frame)
                return enc
            except Exception as e:
                log.warning("[%s] the encoder made ahead is unusable (%s: %s): making one now", self.name,
                            type(e).__name__, e)
                if enc is not None:
                    enc.close()
        try:
            from .encode.nvenc import GpuEncoder

            args = {**gpu_encoder_args(self.profile), "width": self.w, "height": self.h, "fps": self.fps}
            return GpuEncoder(cp=self.ctx.cp, stream=self.ctx.stream, name=self._tag(), clock=pace_clock,
                              on_config=self._on_config, on_frame=self._on_frame, **args)
        except Exception as e:
            self.encoder_fallback = f"NVENC unavailable: {type(e).__name__}: {e}"[:300]
            log.error("[%s] encoder fallback to ffmpeg at start: %s", self.name, self.encoder_fallback)
            return None

    def request_keyframe(self) -> None:
        """A player joined (loop thread): the GPU encoder sends an IDR on its next frame instead of the player
        waiting up to gop_s for one.  The ffmpeg pipe cannot force one; nothing to do before the first packet."""
        if self.frames_encoded > 0:
            self._idr_req += 1

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
    def _gc_in_slack(self, deadline: float, last: list) -> None:
        """Paced GC: the oldest generation that is due and fits the slack before ``deadline`` (gen2 regardless once
        GC_GEN2_MAX_S has passed)."""
        now = pace_clock()
        slack = deadline - now
        for g in (2, 1, 0):
            due = now - last[g] >= GC_EVERY_S[g]
            forced = (g == 2 and now - last[2] >= GC_GEN2_MAX_S) or \
                (g == 0 and gc.get_count()[0] >= GC_GEN0_PILE * max(1, gc.get_threshold()[0]))
            if (due and slack >= GC_SLACK_S[g]) or forced:
                gc.collect(g)
                t = pace_clock()
                self.gc_max_ms[g] = max(self.gc_max_ms[g], (t - now) * 1000.0)
                self.gc_runs[g] += 1
                for k in range(g + 1):
                    last[k] = t
                return

    def _render_loop(self) -> None:
        ctx = self.ctx
        period = 1.0 / self.fps               # the frame-id grid; an output slot is step x this
        from .gpu import GpuOps

        fc = FrameCtx(0, self.fps, ctx.pool, retire=self._retire, ops=GpuOps(ctx), report=self._report,
                      name=self.name)
        set_thread_name(f"render-{self._tag()}")
        self._meter = ThreadMeter()
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
                gc_last = [pace_clock()] * 3
                while not self.stop_evt.is_set():
                    deadline = start + n * period
                    if self.gc_paced:
                        self._gc_in_slack(deadline, gc_last)
                    while True:                       # one sleep to the deadline (again only if woken early)
                        delta = deadline - pace_clock()
                        if delta <= 0:
                            break
                        time.sleep(delta)
                    slip = slip_seen = pace_clock() - deadline   # recorded as seen, before any resync
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
                    self._degrade_boundary(fc, n)
                    step = self.step
                    late = slip > period * step
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
                    act = slot_action(late, self._last_slot is not None, self.render_cost_ema, period * step * 1000,
                                      self._gpu_behind, self._repeat_run)
                    if act == "repeat" or act == "backlog":
                        self.repeats += 1
                        if act == "backlog":
                            self.backlog_repeats += 1
                        self._repeat_run += 1
                        self._submit_repeat(n, slip_seen, step)
                    else:
                        if act == "probe":
                            self.probe_frames += 1
                        self._repeat_run = 0
                        self._render_frame(fc, slip_seen, probe=act == "probe", step=step)
                    n += step
                    self.frames = n
        except Exception as e:
            if not self.stop_evt.is_set():
                detail = f"render error: {type(e).__name__}: {e}"
                log.error("[%s] %s\n%s", self.name, detail, traceback.format_exc())
                self._fail(detail)
        finally:
            if self._meter is not None:
                self._meter.close()
            with self._cv:
                self._pending.append(None)  # writer: stop
                self._cv.notify_all()

    # -- the degradation controller (degrade.py; render thread, at a frame boundary) ----------------------------------
    def _degrade_boundary(self, fc: FrameCtx, n: int) -> None:
        """Once a second, a decision; a pending fps step lands here on a frame id that is a multiple of it."""
        try:
            if self._fps_lost is not None and (self._step_want != 1 or self.step != 1):
                self._step_want = 1                    # the ffmpeg fallback numbers frames itself: one tick each
                ch = self.degrade.drop("fps", pace_clock()) if self.degrade is not None else None
                if ch is not None:
                    self._degrade_changed(ch)
            if self._step_want != self.step and n % self._step_want == 0:
                self._set_step(fc, self._step_want)
            dg = self.degrade
            if dg is None:
                return
            now = pace_clock()
            if now < self._dg_next:
                return
            self._dg_next = now + DECIDE_S
            ch = dg.decide(self._records_since(dg.window_start(now)), self._degrade_ctx(), now,
                           1000.0 * self.step / self.fps)
            if ch is None:
                return
            if ch.step == "scene_rate":
                fc.rate_floor = 2 if ch.on else 1
            elif ch.step == "fps":
                self._step_want = 2 if ch.on else 1
                if n % self._step_want == 0:
                    self._set_step(fc, self._step_want)
            self._degrade_changed(ch)
        except Exception:                          # the controller must never take the stream down
            log.exception("[%s] degradation controller failed; off for this session (its steps stay)", self.name)
            self.degrade = None

    def _set_step(self, fc: FrameCtx, step: int) -> None:
        self.step = fc.step = step
        self.fps_steps += 1
        out = self.fps // step
        if self.ctx is not None:
            self.ctx.fps = out                     # plug-ins that read ctx.fps (iFrameRate, simulation caps)
        if self.timer is not None:
            self.timer.set_fps(out)
        if self.hud is not None:
            self.hud.fps = out
        log.info("[%s] output %d fps from frame %d (frame ids every %d)", self.name, out, fc.n, step)

    def _degrade_changed(self, ch: Any) -> None:
        st = self.degrade_state()
        log.info("[%s] degrade: %s %s (%s): now %s", self.name, ch.step, "on" if ch.on else "off", ch.why or "headroom",
                 st.get("text"))
        if not self.stop_evt.is_set():
            self.post(self.listener.on_degrade, st)

    def _records_since(self, t: float) -> list:
        out = []
        for n in reversed(self.order):          # render thread: the only writer of order / records
            r = self.records.get(n)
            if r is None or r.t_start < t:
                break
            out.append(r)
        return out

    def _degrade_ctx(self) -> Ctx:
        bus = self.bus
        live = [sl for sl in bus.chain.slots if not getattr(sl, "removing", False) and not getattr(sl, "failed", False)]
        fh = fg = 0.0
        for sl in live:
            st = getattr(sl, "gpu_stat", None)
            if st is not None:
                fh += float(st.cpu_ms or 0.0)
                fg += float(st.gpu_bound_ms or 0.0)
        from .hud import _first_source_layer

        lay = _first_source_layer(self.root) if self.root is not None else None   # the scene, as the panel shows it
        own = int(getattr(lay, "rate_div", 1) or 1)
        frozen = self._out_applied or not bus.is_open or bus.level < 1.0
        return Ctx(frozen=frozen, has_fx=bool(live), fx_host_ms=fh, fx_gpu_ms=fg, own_div=own,
                   can_fps=self._gpu_enc is not None and self.fps == 60 and self._fps_lost is None,
                   nvenc_busy=self._nvenc_busy())

    def _nvenc_busy(self) -> Optional[float]:
        """NVENC's own utilisation on this GPU (on the GPU-encode path; its sampler started at the first ask)."""
        if self._gpu_enc is None:
            return None
        s = getattr(self, "_enc_util", None)
        if s is None:
            try:
                from .diag import encoder_util

                s = self._enc_util = encoder_util(self.ctx.device.pci_bus_id)
            except Exception:
                s = self._enc_util = False
        return s.value if s else None

    def degrade_state(self) -> dict:
        """The controller's state (its out_fps is the decided rate: the step lands within a slot), or the off one."""
        dg = self.degrade
        if dg is not None:
            return dg.state()
        st = off_state(self.fps)
        if self.degrade_mode != "off":
            st["mode"] = "stopped" if self.started_at is not None else "on"
        st["out_fps"] = self.fps // self.step
        return st

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
        # the opening is over when the in transition finishes, or when a scene change started during it (a GPU
        # guard's recovery cue, a room join) finishes first: either way the bus opens, else it never would
        if kind == "in" and phase == "done" and not self._out_applied and not self.stop_evt.is_set():
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

    def _render_frame(self, fc: FrameCtx, slip: float, probe: bool = False, step: int = 1) -> None:
        cp = self.ctx.cp
        stream = self.ctx.stream
        tw = pace_clock()
        slot = self._acquire_slot()
        if slot is None:
            return
        t0 = pace_clock()
        meter = self._meter
        m0 = meter.sample() if meter is not None else None
        fc.drawn = fc.held = 0
        fc.div = 1
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
        nv12 = slot.dev if slot.dev is not None else self.d_nv12   # GPU encoder: straight into the slot's surface
        self.ctx.kernels.rgba_to_nv12(self.frame, nv12, self.w, self.h)
        hud = self.hud
        if hud is not None and hud.failed is None and (hud.on or hud.fade > 0.0):
            try:                                # on the encoded copy only: self.frame (the frozen exit) stays clean
                hud.draw(nv12, 0.0 if self.bus.sealed else smoothstep(self.bus.level), fc.n, stream)
            except Exception:
                hud._fail("stopped after an error")
                log.exception("[%s] details panel stopped", self.name)
        slot.ev_rendered.record(stream)
        if timer is not None and self.timer is timer:
            timer.frame_end()
        if slot.dev is None:
            cp.cuda.runtime.memcpyAsync(slot.host.ctypes.data, self.d_nv12.data.ptr, self.nv12_bytes,
                                        cp.cuda.runtime.memcpyDeviceToHost, stream.ptr)
        slot.ev_copied.record(stream)           # GPU encoder: no copy, so this marks the frame done (blocking sync)
        self.root = self.root.collapse(fc)
        if self._out_layer is not None and self._out_layer.done:
            self._idr_frames.add(fc.n)          # the exit's last frame (hold_pts) goes out as an IDR (GPU encoder)
            self._out_layer = None
        host_ms = (pace_clock() - t0) * 1000
        self.rendered += 1
        held = fc.held > 0 and fc.drawn == 0
        rec = _Rec(fc.n, False, slip * 1000, host_ms, wait_ms=(t0 - tw) * 1000, t_start=tw, div=fc.div, held=held,
                   probe=probe)
        if m0 is not None:
            rec.cpu_ms, rec.runq_ms, rec.blocked_ms = split_ms(host_ms, m0, meter.sample())
            self.cpu_ms.add(rec.cpu_ms)
            self.blocked_ms.add(rec.blocked_ms)
            if rec.runq_ms is not None:
                self.runq_ms.add(rec.runq_ms)
        if held:
            self.held_frames += 1
        else:
            self.content_frames += 1
        self._keep(rec)
        self.slip_ms.add(rec.slip_ms)
        self.host_ms.add(host_ms)
        if step != 1:
            self._steps[fc.n] = step            # its fMP4 sample lasts step ticks
        with self._cv:
            slot.frame_id = fc.n
            slot.refs += 1                      # the pending write
            self._set_last(slot)
            self._pending.append((fc.n, slot, True, step))
            self._cv.notify_all()

    def _submit_repeat(self, n: int, slip: float, step: int = 1) -> None:
        tw = pace_clock()
        rec = _Rec(n, True, slip * 1000, t_start=tw)
        self._keep(rec)
        self.slip_ms.add(rec.slip_ms)
        if step != 1:
            self._steps[n] = step
        with self._cv:
            while len(self._pending) >= MAX_PENDING_WRITES and not self.stop_evt.is_set():
                self._cv.wait(0.1)
                self._poll_timer()
            rec.wait_ms = (pace_clock() - tw) * 1000
            slot = self._last_slot
            slot.refs += 1
            self._pending.append((n, slot, False, step))
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
        set_thread_name(f"closer-{self._tag()}")
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
        set_thread_name(f"{'nvenc' if self._gpu_enc is not None else 'writer'}-{self._tag()}")
        self.ctx.device.use()
        meter = ThreadMeter()                   # this thread's schedstat, opened once (degrade: "computer busy")
        try:
            while True:
                with self._cv:
                    while not self._pending:
                        self._cv.wait(0.1)
                    item = self._pending[0]
                if item is None or self.stop_evt.is_set():
                    return
                n, slot, rendered, step = item
                slot.ev_copied.synchronize()
                rec = self.records.get(n)
                if rendered and rec is not None:
                    rec.render_ms = cp.cuda.get_elapsed_time(slot.ev_start, slot.ev_rendered)
                    rec.copy_ms = cp.cuda.get_elapsed_time(slot.ev_rendered, slot.ev_copied)
                    self.render_ms.add(rec.render_ms)
                    cost = max(rec.render_ms, rec.host_ms)
                    self.render_cost_ema += 0.2 * (cost - self.render_cost_ema)
                t0 = pace_clock()
                c0 = time.thread_time()
                q0 = meter.runq_ns()
                try:
                    self._submit(n, slot, step)
                except (BrokenPipeError, ValueError, OSError) as e:
                    if not self.stop_evt.is_set():
                        self._fail(f"encoder input closed: {type(e).__name__}: {e}")
                    return
                t1 = pace_clock()
                enc_cpu = (time.thread_time() - c0) * 1000
                q1 = meter.runq_ns()
                enc_runq = (q1 - q0) / 1e6 if q0 is not None and q1 is not None and q1 >= q0 else 0.0
                self.enc_cpu_ms.add(enc_cpu)
                self.frames_written += 1
                if rec is not None:
                    rec.write_ms = (t1 - t0) * 1000
                    rec.t_written = t1
                    rec.enc_cpu_ms = enc_cpu
                    rec.enc_runq_ms = enc_runq
                    self.write_ms.add(rec.write_ms)
                with self._cv:
                    self._pending.popleft()
                    slot.refs -= 1
                    self._cv.notify_all()
        finally:
            meter.close()
            with self._cv:
                self._cv.notify_all()

    def _submit(self, n: int, slot: _Slot, step: int = 1) -> None:
        """One slot to the encoder (writer thread).  GPU encoder: Encode() on the slot's surface, an IDR where due;
        an error there falls back to ffmpeg for the rest of the session, starting with this frame.  A new step (the
        fps step) Reconfigure()s NVENC first; if it refuses, encoding goes on at the old rate's bits per frame."""
        gpu = self._gpu_enc
        if gpu is not None:
            try:
                if step != self._enc_step:
                    self._enc_step = step
                    try:
                        self.reconfigure_ms = round(gpu.set_fps(self.fps // step), 2)
                    except Exception as e:
                        if self.stop_evt.is_set():
                            return
                        self.reconfigure_error = f"{type(e).__name__}: {e}"[:300]
                        log.error("[%s] NVENC fps Reconfigure failed (encoding goes on): %s", self.name,
                                  self.reconfigure_error)
                gpu.submit(slot.surface, n, self._idr_due(n))
                return
            except Exception as e:
                if self.stop_evt.is_set():
                    return
                self._fall_back(n, slot, f"NVENC failed mid-session: {type(e).__name__}: {e}"[:300])
        self.pipeline.submit(memoryview(self._download(slot) if slot.dev is not None else slot.host))

    def _idr_due(self, n: int) -> bool:
        if n % self._gop == 0:                  # today's grid (NVENC's own GOP restarts at a forced IDR)
            self._idr_frames.discard(n)
            return True
        req = self._idr_req
        if n in self._idr_frames or req != self._idr_seen:
            self._idr_frames.discard(n)
            self._idr_seen = req
            self.forced_idrs += 1
            return True
        return False

    def _download(self, slot: _Slot) -> Any:
        """A GPU-encoder slot's frame into the fallback's pinned buffer (writer thread; the frame is done)."""
        cp = self.ctx.cp
        cp.cuda.runtime.memcpyAsync(self._fb_host.ctypes.data, slot.dev.data.ptr, self.nv12_bytes,
                                    cp.cuda.runtime.memcpyDeviceToHost, self._fb_stream.ptr)
        self._fb_stream.synchronize()
        return self._fb_host

    def _fall_back(self, n: int, slot: _Slot, reason: str) -> None:
        """NVENC failed at frame n (writer thread): the rest of the session goes through a new ffmpeg pipeline fed by
        copies of the device surfaces.  Its frame ids start at the next one the players expect; the frames NVENC
        held back are re-sent as frame n (a freeze of up to 3 frames) so the timeline stays whole.  The players get
        the new init segment (MediaHub.set_config), then its first IDR."""
        log.error("[%s] encoder fallback to ffmpeg at frame %d: %s", self.name, n, reason)
        self.encoder_fallback, self.encoder_active = reason, "ffmpeg"
        self._fps_lost = reason                 # ffmpeg numbers its frames one tick apart: the fps step comes off
        self._steps.clear()
        gpu, self._gpu_enc = self._gpu_enc, None
        try:
            gpu.close()
        except Exception:
            log.exception("[%s] closing the GPU encoder failed", self.name)
        import numpy as np

        cp = self.ctx.cp
        self._fb_pinned = cp.cuda.alloc_pinned_memory(self.nv12_bytes)
        self._fb_host = np.frombuffer(self._fb_pinned, np.uint8, self.nv12_bytes)
        self._fb_stream = cp.cuda.Stream(non_blocking=True)
        base = self._next_id
        self.pipeline = self._ffmpeg_pipeline(id_base=base)
        if n > base:
            frame = memoryview(self._download(slot))
            for _ in range(n - base):
                self.pipeline.submit(frame)

    # ------------------------------------------------------------------------
    # encoder callbacks (reader thread; the encode thread for the GPU encoder)
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
        self._next_id = frame_id + 1
        self.bytes_encoded += len(sample)
        rec = self.records.get(frame_id)
        if rec is not None:
            rec.size, rec.key = len(sample), key
            if rec.t_start:
                self.server_ms.add((t_encoded - rec.t_start) * 1000)
            if rec.t_written is not None:
                rec.encode_ms = (t_encoded - rec.t_written) * 1000
                self.encode_ms.add(rec.encode_ms)
        step = self._steps.pop(frame_id, 1) if self._steps else 1
        frag, _ = media_fragment(frame_id + 1, frame_id * self.frame_dur, self.frame_dur * step, sample, key)
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
                self._fb_host = self._fb_pinned = self._fb_stream = None
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
            # W0.2: rendered frames' host time split, and the scene's own frame rate
            "cpu_ms": self.cpu_ms.summary(), "runq_ms": self.runq_ms.summary(),
            "blocked_ms": self.blocked_ms.summary(),
            "content_fps": self._content_fps(self._recent(2.0), 2.0),
            "content_frames": self.content_frames, "held_frames": self.held_frames,
            "probe_frames": self.probe_frames,
            # change 2: which encoder, why not the wanted one, its thread's CPU per slot, render start -> packet
            "encoder": self.encoder_active or self.encoder, "encoder_fallback": self.encoder_fallback,
            "encode_cpu_ms": self.enc_cpu_ms.summary(), "slot_to_packet_ms": self.server_ms.summary(),
            "forced_idrs": self.forced_idrs,
            # change 5: the degradation controller, the output rate and its NVENC Reconfigures
            "degrade": self.degrade_state(), "out_fps": self.fps // self.step, "fps_steps": self.fps_steps,
            "reconfigure_ms": self.reconfigure_ms, "reconfigure_error": self.reconfigure_error,
            # paced GC (7c, 21 Oct): the longest collection and the number run, per generation; None when not paced
            "gc_ms": {"max": [round(x, 2) for x in self.gc_max_ms], "runs": list(self.gc_runs)} if self.gc_paced else None,
        }

    def guard_stats(self) -> dict:
        t = self.timer
        return {**(t.stats() if t is not None else {"samples": 0, "trips": 0, "backlog": 0}),
                "enabled": t is not None, "slow_trips": self.slow_trips, "backlog_repeats": self.backlog_repeats,
                "frozen_exits": self.frozen_exits}

    def _recent(self, seconds: float) -> list:
        """Records whose slot started in the last ``seconds``, newest first."""
        now = pace_clock()
        recs = []
        for n in reversed(list(self.order)[-(int(self.fps * seconds) + self.fps):]):
            r = self.records.get(n)
            if r is None or now - r.t_start > seconds:
                break
            recs.append(r)
        return recs

    def _content_fps(self, recs: list, seconds: float) -> Optional[float]:
        """Frames per second whose scene was actually drawn (not repeated, not held)."""
        if not recs:
            return None
        span = seconds if self.started_at is None else max(1.0 / self.fps, min(seconds, pace_clock() - self.started_at))
        return round(sum(1 for r in recs if not r.repeat and not r.held) / span, 1)

    def window_stats(self, seconds: float = 2.0) -> dict:
        """Diagnostics over the last ``seconds`` (records whose slot started in the window; loop thread).  Render,
        host and CPU figures are over rendered slots only; repeats are counted as repeats."""
        recs = self._recent(seconds)
        now = pace_clock()
        rendered = [r for r in recs if not r.repeat]
        period_ms = 1000.0 * self.step / self.fps      # an output slot

        def dist(key: str, rows: Optional[list] = None) -> dict:
            vals = sorted(getattr(r, key) for r in (recs if rows is None else rows) if getattr(r, key) is not None)
            rnd = lambda v: None if v is None else round(v, 2)  # noqa: E731
            return {"p50": rnd(pct_sorted(vals, 50)), "p95": rnd(pct_sorted(vals, 95)),
                    "max": rnd(vals[-1] if vals else None)}

        sizes = sum(r.size for r in recs if r.size)
        return {
            "window_s": seconds, "frames": len(recs), "rendered": len(rendered), "budget_ms": round(period_ms, 2),
            "render_ms": dist("render_ms", rendered), "write_ms": dist("write_ms"), "encode_ms": dist("encode_ms"),
            "slip_ms": dist("slip_ms"), "host_ms": dist("host_ms", rendered),
            "cpu_ms": dist("cpu_ms", rendered), "runq_ms": dist("runq_ms", rendered),
            "blocked_ms": dist("blocked_ms", rendered), "enc_cpu_ms": dist("enc_cpu_ms"),
            "enc_runq_ms": dist("enc_runq_ms"),
            "content_fps": self._content_fps(recs, seconds), "probe_frames": sum(1 for r in recs if r.probe),
            "late_pct": round(100.0 * sum(1 for r in recs if r.slip_ms > period_ms) / len(recs), 1) if recs else None,
            "repeats": sum(1 for r in recs if r.repeat),
            "resyncs": sum(1 for t in self.resync_times if now - t <= seconds),
            "encode_queue": len(self._pending) + max(0, self.frames_written - self.frames_encoded),
            "mbps": round(sizes * 8 / 1e6 / seconds, 2),
            "nvenc_delay_frames": getattr(self._gpu_enc, "delay_frames", self.profile.nvenc_delay),   # the active one
            "out_fps": self.fps // self.step,
        }

    def _recent_bitrate(self) -> Optional[float]:
        """Encoded Mbit/s over the last second of frames (at the output rate)."""
        fps = self.fps // self.step
        sizes = []
        for n in list(self.order)[-(fps + 30):]:
            r = self.records.get(n)
            if r is not None and r.size is not None:
                sizes.append(r.size)
        if len(sizes) < fps // 2:
            return None
        return round(sum(sizes[-fps:]) * 8 / 1e6 * fps / min(len(sizes), fps), 2)

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
