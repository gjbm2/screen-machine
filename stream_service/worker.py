"""A render worker: one screen's Engine in a process of its own (4K60 plan change 3; workers.py starts and drives it).

    python -m stream_service.worker --gpu N --cmd-fd A --media-fd B --parent PID [--config-dir D] [--show-bus URL]

Why: both screens' render threads, the encode threads and the web loop shared one GIL (DIAGNOSIS_2026-10-18: render
threads on the GIL futex 18.6-19.4 % of wall, spikes together).  Here a screen's render thread shares its GIL only with
its own encode thread, its prepares and this process's small readers.

The control (today's service, :8702) keeps the sessions, directors, rooms and the media fan-out; it holds stand-ins for
the plug-in objects and sends what the Session would have called on the Engine (EngineProxy, workers.py).  This
process holds the real objects in a handle table (ipc.HandleTable), the Engine and the per-frame copies the render
thread reads: SessionRuntime + Sampler, the session's Sound, a ShowInputs, MusicFeed and scope-feed copy fed by the
control's forwarded bus messages, and the details panel (its text comes from the control).

Threads: the Engine's render / encode / closer threads; one reader for the command socket (inputs and engine commands
inline, in order); one lifecycle thread (bind, start, stop) and PREPARE_THREADS for prepares, so an exit's prepare
never waits behind a slow scene compile; w-enc makes the next session's NVENC encoder ahead (~0.5-0.8 s off its start);
this main thread sends stats at STATS_HZ.  Media (and the encoder config)
go out on their own socket, so an IDR never delays an event.  No asyncio loop, no aiohttp, no plug-in discovery: a
plug-in module is imported when something of it is first prepared (re-imported when its file changed).

PR_SET_PDEATHSIG: this process is killed when the control's main thread goes (the control spawns from it).  SIGINT
is ignored (the control's own shutdown ends sessions and asks workers to exit).  sys.setswitchinterval(0.001).
GPU: CUDA_DEVICE_ORDER=PCI_BUS_ID and Device(--gpu); never CUDA_VISIBLE_DEVICES.
"""
from __future__ import annotations

import argparse
import functools
import gc
import importlib
import logging
import os
import signal
import socket
import sys
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"          # before CUDA initialises (as __main__.py)

log = logging.getLogger("stream_service.worker")

PREPARE_THREADS = 3
STATS_HZ = 2.0
PR_SET_PDEATHSIG = 1
ENC_WAIT_S = 10.0       # a start waits this long for its encoder being made ahead, else makes its own
SLOW_RPC_S = 2.0        # an RPC slower than this is logged


def die_with_parent(parent: int) -> None:
    """SIGKILL when the parent (its spawning thread) goes; if it went already, exit now."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    except Exception:
        pass
    if parent and os.getppid() != parent:
        os._exit(0)


class _ScopeCopy:
    """What sound.Sound reads of scope_feed.ScopeFeed: the newest frame, swapped by reference."""
    latest: Any = None


class _Listener:
    """Only the names: Engine.post(self.listener.on_x, ...) reaches Worker._post, which forwards by name."""

    def on_encoder_config(self, *a): ...
    def on_media(self, *a): ...
    def on_transition(self, *a): ...
    def on_failed(self, *a): ...
    def on_plugin_error(self, *a): ...
    def on_fx(self, *a): ...
    def on_degrade(self, *a): ...


_LISTENER = _Listener()


def _remote_error(e: BaseException) -> dict:
    from .plugins.base import ParamError
    d = {"type": type(e).__name__, "msg": str(e)[:2000]}
    if isinstance(e, ParamError):
        d["param"] = (e.code, e.detail)
    return d


class Worker:
    def __init__(self, gpu: int, cmd_sock: socket.socket, media_sock: socket.socket, *, config_dir: Optional[str],
                 show_bus: Optional[str]):
        from .inputs import ShowInputs
        from .ipc import Channel, HandleTable
        from .music_feed import MusicFeed, ResponseFile

        self.gpu = gpu
        self.t0 = time.monotonic()
        self.cmd = Channel(cmd_sock, "w-cmd", self._on_frame, self._on_close)
        self.media = Channel(media_sock, "w-media")
        self.prep = ThreadPoolExecutor(PREPARE_THREADS, thread_name_prefix="w-prep")
        self.life = ThreadPoolExecutor(1, thread_name_prefix="w-life")
        self.encx = ThreadPoolExecutor(1, thread_name_prefix="w-enc")
        self._enc: Optional[dict] = None             # the NVENC encoder made ahead: {key, stream, fut}
        self._enc_bound: Optional[dict] = None       # ... the one the bound session took
        self._enc_lock = threading.Lock()
        self.table = HandleTable()
        self.slots: "weakref.WeakValueDictionary[str, Any]" = weakref.WeakValueDictionary()
        self.lock = threading.Lock()
        self.sid: Optional[str] = None
        self.engine: Any = None
        self.runtime: Any = None
        self.sampler: Any = None
        self.sound: Any = None
        self.hud_on = False
        self.inputs = ShowInputs(show_bus)
        self.music_feed = MusicFeed(ResponseFile(config_dir) if config_dir else None)
        self.scope = _ScopeCopy()
        self.stop_evt = threading.Event()
        self._mtimes: dict[str, Any] = {}
        self._warm_spec: Optional[dict] = None
        self._warmed = False
        self._warm_on_media = False                  # the warm waits for the session's first packet (session.ready)
        self._import_lock = threading.Lock()
        self._mlock = threading.Lock()
        self.inline = {"submit": self._submit, "sync": self._sync, "hud": self._hud, "hud_text": self._hud_text,
                       "key": self._key, "in": self._input, "gone": self._gone, "warm": self._warm_async,
                       "enc": self._enc_hint, "exit": lambda _d: self.exit("asked")}
        self.rpcs = {"bind": (self.life, self._bind), "prepare": (self.prep, self._prepare),
                     "start": (self.life, self._start), "stop": (self.life, self._stop),
                     "frames": (self.life, self._frames)}

    # -- start-up ---------------------------------------------------------------------------------------------------
    def boot(self) -> dict:
        """Imports and this GPU's CUDA context + core kernels, timed (the hello)."""
        t0 = time.monotonic()
        import cupy  # noqa: F401
        from . import renderer  # noqa: F401
        t1 = time.monotonic()
        from .gpu import RenderContext
        ctx = RenderContext(self.gpu, 64, 64, 30, prealloc_buffers=0)   # context, streams, core kernels (cached)
        with ctx.device:
            ctx.cp.cuda.Device().synchronize()
        t2 = time.monotonic()
        return {"pid": os.getpid(), "gpu": self.gpu, "import_s": round(t1 - t0, 3), "cuda_s": round(t2 - t1, 3),
                "boot_s": round(t2 - self.t0, 3)}

    def run(self) -> None:
        self.cmd.start()
        self.media.start()
        hello = self.boot()
        self.cmd.send(("hello", 0, hello))
        log.info("render worker up on GPU %d in %.2f s (imports %.2f s, CUDA %.2f s)", self.gpu, hello["boot_s"],
                 hello["import_s"], hello["cuda_s"])
        period = 1.0 / STATS_HZ
        n = 0
        while not self.stop_evt.wait(period):
            n += 1
            eng = self.engine
            if eng is not None and eng.started_at is not None:
                try:
                    self.cmd.send(("stats", eng.name, self._snapshot(eng)))
                except Exception:
                    log.exception("stats snapshot failed")
            if n % 2 == 0:
                try:
                    self.music_feed.response.reload_if_changed()
                except Exception:
                    pass

    def exit(self, why: str) -> None:
        if self.stop_evt.is_set():
            return
        self.stop_evt.set()
        log.info("render worker on GPU %d exiting (%s)", self.gpu, why)
        eng = self.engine
        if eng is not None:
            try:
                eng.stop(2.0)
            except Exception:
                log.exception("engine stop at exit failed")
        self.cmd.close(flush_s=0.5)
        self.media.close(flush_s=0.5)
        logging.shutdown()
        os._exit(0)

    def _on_close(self, why: str) -> None:
        threading.Thread(target=self.exit, args=(f"control gone: {why}",), daemon=True).start()

    # -- the command socket (reader thread) -----------------------------------------------------------------------
    def _on_frame(self, kind: int, payload: bytes) -> None:
        from .ipc import loads
        op, rid, data = loads(payload)
        if rid:
            ex, fn = self.rpcs.get(op, (None, None))
            if fn is None:
                self.cmd.send(("reply", rid, False, {"type": "KeyError", "msg": f"unknown op {op!r}"}))
                return
            ex.submit(self._rpc, fn, rid, data)
            return
        fn = self.inline.get(op)
        if fn is None:
            log.warning("unknown message %r", op)
            return
        fn(data)

    def _rpc(self, fn, rid: int, data: Any) -> None:
        t0 = time.monotonic()
        try:
            res = fn(data)
            self.cmd.send(("reply", rid, True, res))
        except BaseException as e:                 # the control logs it (as a prepare failure, a start failure ...)
            log.debug("rpc %s failed", getattr(fn, "__name__", fn), exc_info=True)
            self.cmd.send(("reply", rid, False, _remote_error(e)))
        dt = time.monotonic() - t0
        if dt > SLOW_RPC_S:
            log.info("%s for %s took %.1f s", getattr(fn, "__name__", fn).strip("_"),
                     data.get("sid") if isinstance(data, dict) else "-", dt)

    # -- the engine's callbacks (its threads) -> the control ----------------------------------------------------
    def _post(self, sid: str, fn, *args) -> None:
        name = getattr(fn, "__name__", "")
        if name == "on_media":
            from .ipc import K_MEDIA
            self.media.send_raw(K_MEDIA, args[2])
            if self._warm_on_media:                 # the first packet is out (session.ready): the warm may run now
                self._warm_on_media = False
                self._warm_now()
            return
        if name == "on_encoder_config":           # on the media socket: it must reach the players before frames
            self.media.send(("evt", sid, name, args))
            return
        if name == "on_plugin_error" and args and isinstance(args[0], dict):
            info = dict(args[0])
            info["obj_id"] = self.table.hid_of_id(info.get("obj_id"))
            args = (info,)
        self.cmd.send(("evt", sid, name, args))

    def _frame_hook(self, now: float) -> None:
        """Render thread, once per rendered frame (as Session._frame_hook): the modulation clock, then the sound."""
        self.runtime.begin_frame(now)
        if self.sound is not None:
            self.sound.update(now + self.sampler.lead)

    # -- plug-in classes -----------------------------------------------------------------------------------------
    def _cls(self, ref: tuple) -> type:
        """(module, qualname, name, kind, mtime) -> the class; the module is (re)imported when the control's copy
        is newer than ours."""
        from .plugins.base import iter_plugin_classes, plugin_kind
        module, qualname, name, kind, mtime = ref
        with self._import_lock:
            mod = sys.modules.get(module)
            if mod is None:
                mod = importlib.import_module(module)
                self._mtimes[module] = mtime
            elif mtime is not None and self._mtimes.get(module, mtime) != mtime:
                log.info("plug-in module %s changed: reloading", module)
                mod = importlib.reload(mod)
                self._mtimes[module] = mtime
            else:
                self._mtimes.setdefault(module, mtime)
        if name:
            for cls in iter_plugin_classes(vars(mod).values(), module):
                if cls.name == name and (kind is None or plugin_kind(cls) == kind):
                    return cls
        obj: Any = mod
        for part in qualname.split("."):
            obj = getattr(obj, part)
        return obj

    def _make(self, spec: dict) -> Any:
        from .modulation import Modulation
        cls = self._cls(spec["cls"])
        kind = spec["kind"]
        if kind == "transition":
            return cls(spec["params"])
        img = spec.get("image")
        if img is not None:
            if "rgb" in img:
                return cls(rgb=tuple(img["rgb"]))
            if "shm" in img:
                import numpy as np
                pixels = np.fromfile(img["shm"], dtype=img["dtype"]).reshape(img["shape"])
                try:
                    os.unlink(img["shm"])
                except OSError:
                    pass
                return cls(pixels=pixels)
            return cls(pixels=img["pixels"])
        obj = cls()
        m = spec.get("mod")
        if m is not None:
            obj.modulation = Modulation(self.runtime, m["props"], m["bindings"], key=m["key"])
        return obj

    def _slot(self, s: dict) -> Any:
        """An EffectSlot from the control's description; its effect is taken from the table (given to the engine)."""
        from .effects import EffectSlot
        from .modulation import Modulation
        eff = self.table.give(s["hid"])
        if eff is None:
            raise KeyError(f"effect handle {s['hid']} unknown")
        m = s.get("mod")
        mod = None if m is None else Modulation(self.runtime, m["props"], m["bindings"], key=m["key"])
        slot = EffectSlot(s["slot_id"], eff, name=s["name"], preset=s["preset"], stage=s["stage"], modulation=mod,
                          mix=s["mix"], level=s["level"], target=s["target"], fade_s=s["fade_s"], seq=s["seq"],
                          where=s["where"], label=s["label"])
        self.slots[slot.slot_id] = slot
        return slot

    def _close(self, objs: list) -> None:
        from .effects import close_with_effects
        eng = self.engine
        ctx = getattr(eng, "ctx", None)
        for o in objs:
            try:
                if ctx is not None:
                    with ctx.device:
                        close_with_effects(o)
                else:
                    close_with_effects(o)
            except Exception:
                log.exception("closing %r failed", o)

    # -- RPCs ----------------------------------------------------------------------------------------------------
    def _bind(self, d: dict) -> dict:
        from .modulation import SessionRuntime
        from .inputs import Sampler
        from .renderer import Engine
        from .sound import new_sound
        with self.lock:
            if self.engine is not None:
                raise RuntimeError(f"worker already serves {self.sid}")
            sid = d["sid"]
            self.sampler = Sampler(self.inputs, d["fixture"], d["lead"], d["follow"])
            rt = d["rt"]
            self.runtime = SessionRuntime(self.sampler, speed=rt["speed"], depth=rt["depth"], ease_s=rt["ease_s"])
            self.runtime.feed = self.music_feed
            self._apply_rt(rt)
            self.sound = new_sound(feed=self.scope) if d.get("sound", True) else None
            eng = Engine(profile=d["profile"], gpu=self.gpu, post=functools.partial(self._post, sid),
                         listener=_LISTENER, name=sid)
            eng.sound = self.sound
            eng.frame_hook = self._frame_hook
            eng.bus.runtime = self.runtime
            self.hud_on = bool(d.get("hud"))
            self.engine, self.sid = eng, sid
            self.media.send(("bind", sid, None, None))
        eng.create_context()
        eng.ctx.screen = d.get("screen")
        if d.get("encoder") == "nvenc":            # its encoder: made ahead, else made now beside the prepares
            try:
                ent = self._enc_take(d["profile"])
                eng.ctx.stream = ent["stream"]     # NVENC reads the slots on the stream it was made with
                eng.gpu_encoder_ready = functools.partial(self._enc_wait, ent)
            except Exception:
                log.exception("[%s] no encoder made ahead: the start makes it", sid)
        if self.sound is not None:
            with eng.ctx.device:
                self.sound.alloc(eng.ctx.cp)
        return {"pid": os.getpid()}

    def _prepare(self, d: dict) -> dict:
        eng, sid = self.engine, d["sid"]
        if eng is None or self.sid != sid:
            raise RuntimeError(f"not serving {sid}")
        obj = self._make(d)
        kind = d["kind"]
        if kind == "source":
            eng.prepare(obj, d["params"])
        elif kind == "effect":
            eng.prepare_effect(obj, d["params"])
        else:
            eng.prepare_transition(obj)
        with self.lock:
            stale = self.engine is not eng or self.sid != sid or eng._released
            if not stale:
                self.table.add(obj, hid=d["hid"])
        if stale:
            self._close([obj])
            raise RuntimeError(f"{sid} ended while preparing")
        return {"duration_s": float(obj.duration_s)} if kind == "transition" else {}

    def _start(self, d: dict) -> dict:
        eng = self.engine
        if eng is None or self.sid != d["sid"]:
            raise RuntimeError(f"not serving {d['sid']}")
        anchor = self.table.give(d["anchor"])
        if anchor is None:
            raise RuntimeError("anchor handle unknown")
        eng.encoder = d.get("encoder") or "ffmpeg"
        # the degradation controller runs here; a control that predates it sends no "degrade": none, as before
        eng.degrade_mode = d.get("degrade", "off")
        self._warm_on_media = not self._warmed      # after the first packet, in the anchor hold: well before go / exit
        # GC for the session: what is set up now is frozen out of collection, automatic GC goes off and the render
        # loop collects in the slack before its deadlines (renderer.GC_*); back to normal when the session ends
        gc.collect()
        gc.freeze()
        gc.disable()
        eng.gc_paced = True
        try:
            eng.start(anchor)
        except BaseException:
            eng.gc_paced = False
            gc.unfreeze()
            gc.enable()
            raise
        h = eng.hud
        if h is not None:
            rt = self.runtime
            h.lamp = lambda: (rt.signals or {}).get("beat", 0.0)
            h.on = self.hud_on
        return {"hud": h is not None, "encoder_active": eng.encoder_active, "encoder_fallback": eng.encoder_fallback}

    def _stop(self, d: dict) -> dict:
        eng = self.engine
        if eng is None or (d.get("sid") and d["sid"] != self.sid):
            return {}
        t0 = time.monotonic()
        try:
            eng.stop()
        finally:
            with self.lock:
                owned = self.table.clear()
                self.slots.clear()
            self._close(owned)
            with self.lock:
                self.engine = self.sid = None
                self.runtime = self.sampler = self.sound = None
            ctx = eng.ctx
            with self._enc_lock:
                ent, self._enc_bound = self._enc_bound, None
            unused = eng.gpu_encoder_ready is not None          # ended before its start: its encoder is untouched
            again = eng.encoder == "nvenc" and eng.encoder_fallback is None
            profile = eng.profile
            del eng
            gc.unfreeze()
            gc.collect()
            gc.enable()
            if ctx is not None:
                try:
                    with ctx.device:
                        ctx.cp.get_default_memory_pool().free_all_blocks()
                        ctx.cp.get_default_pinned_memory_pool().free_all_blocks()
                except Exception:
                    log.exception("freeing the memory pools failed")
            if not self.stop_evt.is_set():          # the next session here (most likely this screen's) takes it
                try:
                    if ent is not None and unused:
                        self._enc_put(ent)
                    elif again:
                        self._enc_ahead(profile)
                except Exception:
                    log.exception("making an encoder ahead failed")
        return {"stop_s": round(time.monotonic() - t0, 3)}

    def _frames(self, d: dict) -> list:
        eng = self.engine
        return eng.frame_records(d.get("since", -1)) if eng is not None else []

    # -- inline messages (reader thread, in order) ------------------------------------------------------------
    def _submit(self, d: tuple) -> None:
        from .effects import EffectChain, FxUpdate
        from .renderer import Retire, StartTransition, UpdateParams
        eng = self.engine
        op = d[0]
        if eng is None:
            return
        try:
            if op == "st":                          # (+ the room's scene clock when the control sends one)
                _, kind, tid, src_ref, trans_ref, at_mono, *more = d
                src = self.table.give(src_ref["hid"])
                if src is not None and src_ref.get("chain") is not None:
                    src.fx_chain = EffectChain([self._slot(s) for s in src_ref["chain"]])
                if "make" in trans_ref:
                    trans = self._make(trans_ref["make"])
                else:
                    trans = self.table.give(trans_ref["hid"])
                if src is None or trans is None:
                    log.error("[%s] start_transition %s: unknown handle (source %s, transition %s)", self.sid, kind,
                              src_ref.get("hid"), trans_ref.get("hid"))
                    for o in (src, trans):
                        if o is not None:
                            eng.submit(Retire(o))
                    return
                eng.submit(StartTransition(kind, tid, src, trans, at_mono, more[0] if more else None))
            elif op == "up":
                _, hid, params, bindings = d
                src = self.table.get(hid)
                if src is not None:
                    eng.submit(UpdateParams(src, params, bindings))
            elif op == "retire":
                obj = self.table.get(d[1])
                self.table.drop(d[1])
                if obj is not None:
                    eng.submit(Retire(obj))
            elif op == "fx":
                _, rev, adds, updates, fade_s, master = d
                eng.submit(FxUpdate(rev, add=[self._slot(s) for s in adds], update=list(updates), fade_s=fade_s,
                                    master=master))
        except Exception:
            log.exception("[%s] engine command %s failed", self.sid, op)

    def _apply_rt(self, rt: dict) -> None:
        r = self.runtime
        for k in ("speed_target", "depth_target", "palette", "palette_s", "ease_s", "extra"):
            if k in rt:
                setattr(r, k, rt[k])

    def _sync(self, d: dict) -> None:
        if self.runtime is None or d.get("sid") != self.sid:
            return
        self._apply_rt(d)
        sp = self.sampler
        if "lead" in d:
            sp.lead = d["lead"]
        if "follow" in d:
            sp.follow = d["follow"]
        rd = d.get("room_degrade")
        eng = self.engine
        if rd is not None and eng is not None:      # the room's degrade floor (docs/ENSEMBLE.md §8.9)
            eng.set_room_degrade(*rd)

    def _hud(self, on: bool) -> None:
        self.hud_on = bool(on)
        h = getattr(self.engine, "hud", None)
        if h is not None:
            h.on = self.hud_on

    def _hud_text(self, d: tuple) -> None:
        h = getattr(self.engine, "hud", None)
        if h is not None:
            h.set(*d)

    def _key(self, _d: Any) -> None:
        eng = self.engine
        if eng is not None:
            eng.request_keyframe()

    def _input(self, d: tuple) -> None:
        kind = d[0]
        if kind == "show":
            self.inputs.on_frame(d[1], d[2])
        elif kind == "sound":
            msg, t = d[1], d[2]
            if str(msg.get("type") or "").startswith("music."):
                self.music_feed.on_message(msg, t)
            snd = self.sound
            if snd is not None:
                snd.on_message(msg)
        elif kind == "scope":
            self.scope.latest = d[1]
        elif kind == "room_dir":
            self.music_feed.on_room_direction(d[1], d[2])
            from .gestures import BOOK
            BOOK.on_direction(d[1])                  # the ensemble: which player this screen is (gestures.py)
        elif kind == "gesture":
            from .gestures import BOOK
            BOOK.on_gesture(d[1], d[2])              # room.gesture: the gesture_accents slot reads it
        elif kind == "show_up":
            self.inputs.on_connected(d[1] or {})
        elif kind == "show_down":
            self.inputs.on_disconnected()

    def _gone(self, hids: list) -> None:
        """The control dropped these stand-ins: what is still ours (never shown) goes, through the engine."""
        from .renderer import Retire
        eng = self.engine
        for hid in hids:
            obj = self.table.drop(hid)
            if obj is not None:
                if eng is not None:
                    eng.submit(Retire(obj))
                else:
                    self._close([obj])

    def _warm_async(self, d: dict) -> None:
        """The usual transitions, compiled while nothing else runs here: now if still idle shortly (a warm hint), else
        after the session's first packet (never alongside its prepares or ahead of its session.ready)."""
        self._warm_spec = d
        t = threading.Timer(0.5, self._warm_now)
        t.daemon = True
        t.start()

    def _warm_now(self) -> None:
        with self.lock:
            eng = self.engine
            starting = eng is not None and (eng.started_at is None or self._warm_on_media)   # its first packet first
            if self._warmed or self._warm_spec is None or starting:
                return
            self._warmed = True
        self.prep.submit(self._warm, self._warm_spec)

    def _warm(self, d: dict) -> None:
        """The usual transitions' kernels on this GPU (as SessionManager.warm_gpu), off the start's path."""
        t0 = time.monotonic()
        try:
            from .gpu import RenderContext
            ctx = RenderContext(self.gpu, 1280, 720, 30, prealloc_buffers=0)
            with ctx.device:
                for ref, params in d.get("transitions", ()):
                    try:
                        t = self._cls(ref)(params)
                        t.setup(ctx)
                        t.close()
                    except Exception:
                        log.exception("warming transition %s failed", ref[2])
                ctx.cp.cuda.get_current_stream().synchronize()
                if d.get("live_art"):
                    try:
                        from .plugins.sources import live_art
                        live_art.reserve_pinned(ctx.cp, 3840, 2160)
                    except Exception:
                        log.exception("reserving live_art's pinned buffers failed")
            log.info("render worker GPU %d warm in %.1f s", self.gpu, time.monotonic() - t0)
        except Exception:
            log.exception("warming GPU %d failed", self.gpu)

    # -- the NVENC encoder, made ahead ---------------------------------------------------------------------------
    # CreateEncoder takes ~0.5-0.8 s at 4K.  It is made on the w-enc thread with a render stream of its own, while
    # nothing waits for it: after a session's stop (for the next one here), on a warm hint ("enc"), else at bind,
    # beside the prepares.  Bind gives the session that stream and the start takes the encoder (renderer.Engine.
    # gpu_encoder_ready).  At most one is held; one for another profile is closed.
    @staticmethod
    def _enc_key(profile: Any) -> tuple:
        from .renderer import gpu_encoder_args
        return tuple(sorted(gpu_encoder_args(profile).items()))

    def _enc_new(self, profile: Any, key: tuple) -> dict:
        import cupy as cp
        from .gpu import RENDER_PRIORITY, priority_stream
        with cp.cuda.Device(self.gpu):
            stream = priority_stream(cp, RENDER_PRIORITY)
        return {"key": key, "stream": stream, "fut": self.encx.submit(self._enc_make, profile, stream)}

    def _enc_make(self, profile: Any, stream: Any) -> Any:
        import cupy as cp
        from .encode.nvenc import GpuEncoder
        from .renderer import gpu_encoder_args, pace_clock
        t0 = time.monotonic()
        with cp.cuda.Device(self.gpu):
            stream.synchronize()                   # this thread on the device's context (CreateEncoder takes it)
            enc = GpuEncoder(cp=cp, stream=stream, name="ahead", clock=pace_clock, on_config=lambda _c: None,
                             on_frame=lambda *_a: None, **gpu_encoder_args(profile))
        log.info("render worker GPU %d: %s encoder made ahead in %.2f s", self.gpu, getattr(profile, "name", "?"),
                 time.monotonic() - t0)
        return enc

    def _enc_close(self, ent: dict) -> None:
        """Closed on w-enc once made (an encoder no session took)."""
        def close(f) -> None:
            if f.exception() is None:
                f.result().close()

        def later(f) -> None:
            try:
                self.encx.submit(close, f)
            except RuntimeError:                   # exiting
                pass
        ent["fut"].add_done_callback(later)

    def _enc_put(self, ent: dict) -> None:
        with self._enc_lock:
            old, self._enc = self._enc, ent
        if old is not None and old is not ent:
            self._enc_close(old)

    def _enc_ahead(self, profile: Any) -> None:
        """One for this profile, unless it is held or the bound session has it already."""
        key = self._enc_key(profile)
        with self._enc_lock:
            if any(e is not None and e["key"] == key for e in (self._enc, self._enc_bound)):
                return
            old, self._enc = self._enc, self._enc_new(profile, key)
        if old is not None:
            self._enc_close(old)

    def _enc_take(self, profile: Any) -> dict:
        """Bind (life thread): the one held for this profile, else one made now; the bound session's from here."""
        key = self._enc_key(profile)
        with self._enc_lock:
            old, self._enc = self._enc, None
            ent = old if old is not None and old["key"] == key else self._enc_new(profile, key)
            self._enc_bound = ent
        if old is not None and old is not ent:
            self._enc_close(old)
        return ent

    def _enc_wait(self, ent: dict) -> Any:
        """The start (life thread): the bound session's encoder, once made."""
        try:
            return ent["fut"].result(ENC_WAIT_S)
        except TimeoutError:
            self._enc_close(ent)                   # closed when it is done; the start makes its own
            raise

    def _enc_hint(self, profile: Any) -> None:
        """Reader thread ("enc": a warm hint, or a worker claimed while it spawned)."""
        try:
            self._enc_ahead(profile)
        except Exception:
            log.exception("making an encoder ahead failed")

    # -- stats ---------------------------------------------------------------------------------------------------
    def _snapshot(self, eng: Any) -> dict:
        from .hud import _first_source_layer
        from .renderer import pace_clock
        h = eng.hud
        lay = _first_source_layer(eng.root) if eng.root is not None else None
        slots = {}
        for sid_, sl in list(self.slots.items()):
            mod = sl.modulation
            slots[sid_] = {"level": sl.level, "target": sl.target, "amount_now": sl.amount_now, "failed": sl.failed,
                           "slow": sl.slow, "removing": sl.removing,
                           "last": dict(getattr(mod, "last", {}) or {}) if mod is not None else {},
                           "rows": {k: mod.section_row(k) for k in mod.bindings} if mod is not None else {}}
        mods = {}
        for hid, obj in self.table.items():
            mod = getattr(obj, "modulation", None)
            if mod is None:
                continue
            try:
                mods[hid] = {"last": dict(mod.last), "colour_k": mod.colour_amount(), "describe": mod.describe()}
            except Exception:
                pass
        return {"t": pace_clock(), "frames": eng.frames, "summary": eng.summary(), "window": eng.window_stats(2.0),
                "guard": eng.guard_stats(), "server_ms": eng.server_ms.summary(),
                "encoder_active": eng.encoder_active, "encoder_fallback": eng.encoder_fallback,
                "bus_level": float(eng.bus.level),
                "hud": None if h is None else {"fade": h.fade, "ema_ms": h.ema_ms, "failed": h.failed, "on": h.on},
                "layer": None if lay is None else {"rate_div": getattr(lay, "div_now", lay.rate_div),
                                                   "cost_ema": lay.cost_ema},
                "slots": slots, "mods": mods, "proc": self._proc(eng)}

    def _proc(self, eng: Any) -> dict:
        out: dict = {"pid": os.getpid(), "threads": threading.active_count()}
        try:
            t = os.times()
            out["cpu_s"] = round(t.user + t.system, 2)
            with open("/proc/self/statm") as f:
                out["rss_mb"] = round(int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2**20, 1)
        except (OSError, ValueError):
            pass
        ctx = eng.ctx
        if ctx is not None:
            try:
                with ctx.device:
                    out["pool_mb"] = round(ctx.cp.get_default_memory_pool().total_bytes() / 2**20, 1)
            except Exception:
                pass
        return out


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m stream_service.worker")
    ap.add_argument("--gpu", type=int, required=True)
    ap.add_argument("--cmd-fd", type=int, required=True)
    ap.add_argument("--media-fd", type=int, required=True)
    ap.add_argument("--parent", type=int, default=0)
    ap.add_argument("--config-dir", default=None)
    ap.add_argument("--show-bus", default=None)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)
    die_with_parent(args.parent)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    sys.setswitchinterval(0.001)
    from .gpu import no_thp_stalls
    no_thp_stalls()                         # a 4K anchor read stalled 1.3-3.3 s in kernel compaction without this
    logging.basicConfig(level=args.log_level.upper(), stream=sys.stderr,
                        format=f"%(asctime)s %(levelname)s %(name)s[w{os.getpid()} gpu{args.gpu}]: %(message)s")
    from .stats import set_thread_name
    set_thread_name(f"worker-g{args.gpu}")
    cmd = socket.socket(fileno=args.cmd_fd)
    media = socket.socket(fileno=args.media_fd)
    w = Worker(args.gpu, cmd, media, config_dir=args.config_dir, show_bus=args.show_bus)
    try:
        w.run()
    except Exception:
        log.exception("render worker failed")
        w.exit("error")


if __name__ == "__main__":
    main()
