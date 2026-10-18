"""Render workers, the control's side (4K60 plan change 3): one render+encode process per live screen, on demand.

stream_switches.json ``workers``: ``inproc`` (default: the Engine in this process, exactly as before) or ``proc`` (the
screen's sessions render in a worker process, worker.py).  Read at session create, so a flip applies at the screen's
next session.  This process stays the control: API, sessions, directors, rooms and the media fan-out on :8702.

  WorkerPool   the workers, per GPU.  Spawned only from the loop thread (the main thread: PR_SET_PDEATHSIG in the
               worker fires when the spawning thread exits), in the control's process group, so ``scripts/ctl stop
               stream`` (TERM, then KILL to the group) and any death of the control take them too.  Lifecycle
               (ipc.Lifecycle): spawning -> idle -> bound -> lingering LINGER_S -> exit.  A session takes an idle or
               lingering worker on its GPU, else spawns one at once (Session.__init__, before the anchor decode and
               the scene pick).  ``hint`` (POST /api/workers/warm) spawns one ahead.  The pool forwards the control's
               one subscription each to the show bus, the music bus and the levels feed to every ready worker (raw
               messages; latest values: a lagging worker loses the oldest), and ticks the bound proxies at 10 Hz.
  EngineProxy  the Engine as a Session uses it.  Plug-in objects the Session makes here are stand-ins: each prepared
               one gets a handle (ipc.HandleTable) and its real twin lives in the worker.  Commands go out in order on
               the command socket; prepares, start and stop are calls that block the calling executor thread (as the
               Engine's own do).  The worker's events come back on the loop; its stats (1-2 Hz) feed summary(),
               window_stats(), guard_stats(), the panel's numbers and the stand-ins' render-side fields (an effect
               slot's level, a scene's mapping values), so a summary never touches the worker.  The control keeps its
               own copy of the session's signals ticking at 10 Hz (Session._frame_hook) for the directors, cues and
               the inputs API; the worker renders with its copy, synced from the control's targets at 10 Hz.
               The details panel's lines are built here as before and pushed to the worker's HudDraw.

Degrade, never refuse: a worker that cannot be spawned, is not up in READY_S, or fails to bind -> that session runs in
this process (``worker`` in its summary says so, with the reason; a session.warning too).  A worker that dies mid-
session fails that session (session.error), which is room's existing fallback path.  No auth: a Unix socket pair.
"""
from __future__ import annotations

import asyncio
import collections
import functools
import itertools
import logging
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Optional

from .ipc import BOUND, EXIT, IDLE, LINGERING, SPAWNING, Channel, HandleTable, K_MEDIA, K_MSG, Lifecycle, dumps, \
    loads, pack

log = logging.getLogger("stream_service.workers")

REPO = pathlib.Path(__file__).resolve().parent.parent
LINGER_S = 30.0
READY_S = 20.0          # spawn -> hello, else the session runs in this process
CALL_S = 30.0           # a prepare (a cold NVRTC compile can take seconds)
STOP_S = 10.0
EXIT_KILL_S = 5.0       # asked to exit, still there after this: killed
SHM_DIR = pathlib.Path("/dev/shm/sm-stream")
TICK_S = 0.1



MUSIC_BUS_URL: Optional[str] = None   # the control's first --music-bus (server.make_app), passed to workers' children

class WorkerError(RuntimeError):
    pass


_REMOTE: dict[str, type] = {}


def remote_error(d: dict) -> BaseException:
    """The worker's exception, re-raised here under its own name (the session's logs and events name it)."""
    from .plugins.base import ParamError
    if d.get("param"):
        return ParamError(*d["param"])
    name = str(d.get("type") or "WorkerError")
    cls = _REMOTE.get(name)
    if cls is None:
        cls = _REMOTE[name] = type(name, (WorkerError,), {})
    return cls(d.get("msg") or name)


def cls_ref(cls: type, registry: Any = None) -> tuple:
    """(module, qualname, name, kind, file mtime): how the worker finds (and re-imports) a plug-in class."""
    from .plugins.base import plugin_kind
    mt = None
    if registry is not None:
        try:
            mt = (registry.module_info(cls) or {}).get("mtime")
        except Exception:
            mt = None
    return cls.__module__, cls.__qualname__, getattr(cls, "name", None), plugin_kind(cls), mt


def _mod_spec(mod: Any) -> Optional[dict]:
    if mod is None:
        return None
    return {"props": mod.props, "bindings": dict(mod.bindings), "key": mod.key}


# -- one worker process ---------------------------------------------------------------------------------------------
class WorkerConn:
    _n = itertools.count(1)

    def __init__(self, pool: "WorkerPool", gpu: int):
        self.pool = pool
        self.gpu = gpu
        self.n = next(self._n)
        self.life = Lifecycle(pool.linger_s, time.monotonic())
        self.ready = threading.Event()
        self.hello: Optional[dict] = None
        self.proxy: Optional["EngineProxy"] = None
        self.dead: Optional[str] = None
        self.media_sid: Optional[str] = None
        self.proc: Optional[subprocess.Popen] = None
        self.cmd: Optional[Channel] = None
        self.media: Optional[Channel] = None
        self._rid = itertools.count(1)
        self._calls: dict[int, list] = {}
        self._lock = threading.Lock()
        self.exit_asked: Optional[float] = None
        self.stats: Optional[dict] = None
        self.spawn_s: Optional[float] = None

    @property
    def pid(self) -> Optional[int]:
        return self.proc.pid if self.proc is not None else None

    def spawn(self, config_dir: Optional[str], show_bus: Optional[str]) -> None:
        """Loop (main) thread only: the worker's PR_SET_PDEATHSIG follows the thread that forks it."""
        if threading.current_thread() is not threading.main_thread():
            log.warning("render worker spawned off the main thread: it may outlive the control")
        t0 = time.monotonic()
        a_cmd, b_cmd = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        a_med, b_med = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        args = [sys.executable, "-m", "stream_service.worker", "--gpu", str(self.gpu), "--cmd-fd",
                str(b_cmd.fileno()), "--media-fd", str(b_med.fileno()), "--parent", str(os.getpid()),
                "--log-level", logging.getLevelName(logging.getLogger("stream_service").getEffectiveLevel())]
        if config_dir:
            args += ["--config-dir", str(config_dir)]
        if show_bus:
            args += ["--show-bus", show_bus]
        env = {**os.environ, "CUDA_DEVICE_ORDER": "PCI_BUS_ID", "PYTHONUNBUFFERED": "1",
               "SM_STREAM_WORKER": "1"}          # live-score uses the room process (score/room.py RemoteRoom; b0)
        if MUSIC_BUS_URL:
            env["SM_MUSIC_BUS"] = MUSIC_BUS_URL
        try:
            self.proc = subprocess.Popen(args, pass_fds=(b_cmd.fileno(), b_med.fileno()), cwd=str(REPO), env=env,
                                         stdin=subprocess.DEVNULL, close_fds=True)
        finally:
            b_cmd.close()
            b_med.close()
        self.cmd = Channel(a_cmd, f"wk{self.n}-cmd", self._on_cmd, self._on_close).start()
        self.media = Channel(a_med, f"wk{self.n}-media", self._on_media, self._on_close).start()
        threading.Thread(target=self._reap, name=f"wk{self.n}-reap", daemon=True).start()
        self.spawn_s = round(time.monotonic() - t0, 4)
        log.info("render worker %d spawning on GPU %d (pid %d)", self.n, self.gpu, self.proc.pid)

    # -- calls (any thread but the readers) --------------------------------------------------------------------
    def send(self, msg: Any) -> None:
        if self.cmd is not None and not self.dead:
            self.cmd.send(msg)

    def call(self, op: str, data: Any, timeout: float = CALL_S) -> Any:
        if self.dead or self.cmd is None:
            raise WorkerError(f"render worker gone ({self.dead or 'not started'})")
        rid = next(self._rid)
        slot = [threading.Event(), None, None]
        with self._lock:
            self._calls[rid] = slot
        self.cmd.send((op, rid, data))
        if not slot[0].wait(timeout):
            with self._lock:
                self._calls.pop(rid, None)
            raise WorkerError(f"{op}: no answer from the render worker in {timeout:g} s")
        ok, value = slot[1], slot[2]
        if ok:
            return value
        raise value if isinstance(value, BaseException) else remote_error(value)

    def _fail_calls(self, why: str) -> None:
        with self._lock:
            calls, self._calls = self._calls, {}
        for slot in calls.values():
            slot[1], slot[2] = False, WorkerError(f"render worker gone ({why})")
            slot[0].set()

    # -- the readers (their own threads) ---------------------------------------------------------------------
    def _on_cmd(self, kind: int, payload: bytes) -> None:
        msg = loads(payload)
        tag = msg[0]
        if tag == "reply":
            _, rid, ok, value = msg
            with self._lock:
                slot = self._calls.pop(rid, None)
            if slot is not None:
                slot[1], slot[2] = ok, value
                slot[0].set()
        elif tag == "evt":
            self._evt(msg[1], msg[2], msg[3])
        elif tag == "stats":
            px = self.proxy
            self.stats = msg[2]
            if px is not None and px.name == msg[1]:
                self.pool.post(px._on_stats, msg[2])
        elif tag == "hello":
            self.hello = msg[2]
            self.ready.set()
            self.pool.post(self.pool._hello, self)

    def _evt(self, sid: str, name: str, args: tuple) -> None:
        px = self.proxy
        if px is None or px.name != sid:
            return
        self.pool.post(px._event, name, args)

    def _on_media(self, kind: int, payload: bytes) -> None:
        if kind == K_MEDIA:
            px = self.proxy
            if px is None or px.name != self.media_sid or len(payload) < 6:
                return
            self.pool.post(px._media, int.from_bytes(payload[2:6], "big"), payload[1] == 1, payload)
            return
        msg = loads(payload)
        if msg[0] == "bind":
            self.media_sid = msg[1]
        elif msg[0] == "evt":
            self._evt(msg[1], msg[2], msg[3])

    def _on_close(self, why: str) -> None:
        self._gone(f"socket closed: {why}")

    def _reap(self) -> None:
        rc = self.proc.wait()
        self.pool.procs.discard(self.proc)
        self._gone(f"exited rc={rc}")

    def _gone(self, why: str) -> None:
        with self._lock:
            if self.dead:
                return
            self.dead = why
        self.ready.set()                    # a session waiting for it gives up now
        self._fail_calls(why)
        self.pool.post(self.pool._gone, self, why)
        for ch in (self.cmd, self.media):
            if ch is not None:
                threading.Thread(target=ch.close, daemon=True).start()

    # -- ending -------------------------------------------------------------------------------------------------
    def ask_exit(self, why: str) -> None:
        if self.exit_asked is None:
            self.exit_asked = time.monotonic()
            log.info("render worker %d (pid %s) exits: %s", self.n, self.pid, why)
            self.send(("exit", 0, None))

    def kill(self, why: str) -> None:
        if self.exit_asked is None:
            self.exit_asked = time.monotonic()      # never handed to another session meanwhile
        p = self.proc
        if p is not None and p.poll() is None:
            log.warning("render worker %d (pid %d) killed: %s", self.n, p.pid, why)
            try:
                p.kill()
            except OSError:
                pass

    def describe(self, now: float) -> dict:
        st = self.stats or {}
        proc = st.get("proc") or {}
        h = self.hello or {}
        return {"n": self.n, "pid": self.pid, "gpu": self.gpu, "state": self.life.state,
                "session": self.proxy.name if self.proxy is not None else None,
                "linger_left_s": None if self.life.left_s(now) is None else round(self.life.left_s(now), 1),
                "boot_s": h.get("boot_s"), "ready_s": None if self.life.ready_at is None
                else round(self.life.ready_at - self.life.spawned_at, 3),
                "binds": self.life.binds, "cpu_s": proc.get("cpu_s"), "rss_mb": proc.get("rss_mb"),
                "pool_mb": proc.get("pool_mb"), "threads": proc.get("threads"), "dead": self.dead,
                "dropped_inputs": self.cmd.dropped if self.cmd is not None else 0}


# -- the pool -------------------------------------------------------------------------------------------------------
class WorkerPool:
    def __init__(self, mgr: Any, *, config_dir: Any = None, show_bus: Optional[str] = None,
                 linger_s: float = LINGER_S):
        self.mgr = mgr
        self.config_dir = str(config_dir) if config_dir is not None else None
        self.show_bus = show_bus
        self.linger_s = linger_s
        self.conns: list[WorkerConn] = []
        self.procs: set = set()                     # every worker process until it is reaped (close() waits on them)
        self.watched: set = set()
        self.last_gpu: dict[str, int] = {}
        self.spawned = 0
        self.fallbacks = 0
        self._room_dir: Optional[tuple] = None
        self._show_up: Optional[dict] = None
        self._closing = False
        self.cold: collections.deque = collections.deque(maxlen=64)   # (spawn -> hello s, spawn -> session ready s)

    # -- threads -> loop -----------------------------------------------------------------------------------------
    def post(self, fn: Callable, *args) -> None:
        self.mgr.post(fn, *args)

    # -- sessions (loop thread) ------------------------------------------------------------------------------
    def engine(self, *, profile: Any, gpu: int, post: Callable, listener: Any, name: str) -> "EngineProxy":
        self.last_gpu[getattr(listener, "screen", "")] = gpu
        return EngineProxy(profile=profile, gpu=gpu, post=post, listener=listener, name=name, pool=self)

    def acquire(self, gpu: int, proxy: "EngineProxy") -> Optional[WorkerConn]:
        now = time.monotonic()
        free = [c for c in self.conns if c.gpu == gpu and not c.dead and c.exit_asked is None and c.life.free()]
        free.sort(key=lambda c: (not c.ready.is_set(), c.life.state != LINGERING))   # ready first, warm first
        for c in free:
            if c.life.claim(now):
                c.proxy = proxy
                return c
        c = self._spawn(gpu)
        if c is not None:
            c.life.claim(now)
            c.proxy = proxy
        return c

    def _spawn(self, gpu: int) -> Optional[WorkerConn]:
        c = WorkerConn(self, gpu)
        try:
            c.spawn(self.config_dir, self.show_bus)
        except Exception as e:
            log.error("render worker on GPU %d could not be spawned: %s: %s", gpu, type(e).__name__, e)
            return None
        self.conns.append(c)
        self.procs.add(c.proc)
        self.spawned += 1
        return c

    def release(self, conn: WorkerConn, proxy: "EngineProxy") -> None:
        """Any thread: its session is done with it (it lingers)."""
        self.post(self._release, conn, proxy)

    def _release(self, conn: WorkerConn, proxy: "EngineProxy") -> None:
        if conn.proxy is not proxy:
            return
        conn.proxy = None
        conn.media_sid = None
        if not conn.dead:
            conn.life.release(time.monotonic())

    def hint(self, screen: str, gpu: Optional[int] = None) -> dict:
        """POST /api/workers/warm: a worker for this screen's next session, spawned now (additive; room may send it at
        the start of an entry, so the spawn overlaps the freeze and the snapshot)."""
        sw = getattr(self.mgr, "switches", None)
        mode = sw.workers(screen) if sw is not None else "inproc"
        if mode != "proc":
            return {"screen": screen, "workers": mode, "spawned": False}
        if gpu is None:
            gpu = self.last_gpu.get(screen)
        if gpu is None:
            return {"screen": screen, "workers": mode, "spawned": False, "why": "no GPU known for this screen: pass gpu"}
        have = [c for c in self.conns if c.gpu == gpu and not c.dead and c.exit_asked is None
                and c.life.state in (SPAWNING, IDLE, LINGERING) and c.proxy is None]
        if have:
            c = have[0]
            if c.life.state in (IDLE, LINGERING):
                c.life.since = time.monotonic()          # wanted again: its linger starts over
            return {"screen": screen, "gpu": gpu, "workers": mode, "spawned": False, "worker": c.n, "state": c.life.state}
        c = self._spawn(gpu)
        return {"screen": screen, "gpu": gpu, "workers": mode, "spawned": c is not None,
                **({"worker": c.n} if c is not None else {"why": "spawn failed"})}

    def _hello(self, conn: WorkerConn) -> None:
        now = time.monotonic()
        conn.life.hello(now)
        h = conn.hello or {}
        log.info("render worker %d ready on GPU %d: %.2f s from spawn (boot %.2f s: imports %.2f, CUDA %.2f)",
                 conn.n, conn.gpu, now - conn.life.spawned_at, h.get("boot_s", 0), h.get("import_s", 0),
                 h.get("cuda_s", 0))
        if self._show_up is not None:
            conn.cmd.send_lossy(("in", 0, ("show_up", self._show_up)))
        if self._room_dir is not None:
            conn.cmd.send_lossy(("in", 0, ("room_dir",) + self._room_dir))
        conn.send(("warm", 0, self._warm_spec()))

    def _warm_spec(self) -> dict:
        """What SessionManager.warm_plugins sets up, as the worker's class refs (it runs it off the start's path)."""
        mgr = self.mgr
        out = []
        try:
            names = sorted(set(mgr.WARM_TRANSITIONS) | mgr.presets.file_plugins("transition"))
            for name in names:
                cls = mgr.registry.transitions.get(name)
                if cls is not None:
                    try:
                        out.append((cls_ref(cls, mgr.registry), cls.validate({})))
                    except Exception:
                        pass
        except Exception:
            log.exception("render worker warm list failed")
        return {"transitions": out, "live_art": True}

    def _gone(self, conn: WorkerConn, why: str) -> None:
        now = time.monotonic()
        asked = conn.exit_asked is not None
        conn.life.gone(now, why)
        if conn in self.conns:
            self.conns.remove(conn)
        px = conn.proxy
        conn.proxy = None
        (log.info if asked or self._closing else log.warning)(
            "render worker %d (pid %s) gone: %s", conn.n, conn.pid, why)
        if px is not None:
            px._worker_gone(why)

    # -- housekeeping (a task on the loop) -------------------------------------------------------------------
    async def run(self) -> None:
        n = 0
        while True:
            await asyncio.sleep(TICK_S)
            n += 1
            now = time.monotonic()
            for px in list(self.watched):
                try:
                    px.tick(now)
                except Exception:
                    log.exception("[%s] worker tick failed", px.name)
            if n % 10:
                continue
            for c in list(self.conns):
                if c.dead:
                    continue
                if c.exit_asked is not None:
                    if now - c.exit_asked > EXIT_KILL_S:
                        c.kill("did not exit when asked")
                elif c.life.due(now):
                    c.ask_exit(f"{c.life.state} {self.linger_s:g} s")
                elif not c.ready.is_set() and now - c.life.spawned_at > READY_S * 2:
                    c.kill(f"no hello in {READY_S * 2:g} s")

    async def close(self) -> None:
        """Service shutdown (after the sessions ended): every worker exits, else is killed."""
        self._closing = True
        for c in list(self.conns):
            c.ask_exit("service shutdown")
        t_end = time.monotonic() + EXIT_KILL_S
        while time.monotonic() < t_end and any(p.poll() is None for p in list(self.procs)):
            await asyncio.sleep(0.05)
        for p in list(self.procs):
            if p.poll() is None:
                log.warning("render worker pid %d killed: service shutdown", p.pid)
                try:
                    p.kill()
                except OSError:
                    pass
        try:
            for p in SHM_DIR.glob(f"{os.getpid()}-*"):
                p.unlink()
        except OSError:
            pass

    def describe(self) -> dict:
        now = time.monotonic()
        cold = list(self.cold)
        return {"linger_s": self.linger_s, "spawned": self.spawned, "fallbacks": self.fallbacks,
                "workers": [c.describe(now) for c in self.conns],
                "cold_starts": [{"ready_s": a, "session_ready_s": b} for a, b in cold[-8:]]}

    # -- inputs: the control's one subscription each, forwarded raw (loop thread) ---------------------------
    def _forward(self, payload: tuple) -> None:
        conns = [c for c in self.conns if c.ready.is_set() and not c.dead and c.cmd is not None]
        if not conns:
            return
        frame = pack(K_MSG, dumps(("in", 0, payload)))
        for c in conns:
            c.cmd.push(frame, lossy=True)

    def on_show(self, kind: str, *args) -> None:
        """ShowInputs tap: ("show", frame, arrival) | ("show_up", welcome) | ("show_down", None)."""
        if kind == "show_up":
            self._show_up = args[0] or {}
        elif kind == "show_down":
            self._show_up = None
        self._forward((kind, *args))

    def on_sound(self, msg: dict, t: float) -> None:
        self._forward(("sound", msg, t))

    def on_room_direction(self, data: dict, t: float) -> None:
        self._room_dir = (data, t)
        self._forward(("room_dir", data, t))

    def on_scope(self, frame: Any) -> None:
        self._forward(("scope", frame))


# -- the stand-ins the Session reads --------------------------------------------------------------------------------
class _Ctx:
    """What the Session reads of a RenderContext: it exists (prepares go ahead), ``screen`` (sent at bind)."""

    def __init__(self, screen: Optional[str]):
        self.screen = screen


class _Bus:
    def __init__(self) -> None:
        self.runtime: Any = None
        self.level = 0.0


class _Ring:
    def __init__(self) -> None:
        self.cached: dict = {"p50": None, "p95": None, "max": None, "n": 0}

    def summary(self) -> dict:
        return dict(self.cached)


class _Hud:
    """The details panel as the Session drives it: ``on`` and ``set`` go to the worker's HudDraw, the rest is its
    last stats.  ``lamp`` stays here unused: the worker's lamp is its own runtime's beat."""

    def __init__(self, proxy: "EngineProxy"):
        self._px = proxy
        self._on = False
        self.lamp: Any = None
        self.failed: Optional[str] = None
        self.ema_ms: Optional[float] = None
        self.remote_fade = 0.0

    @property
    def on(self) -> bool:
        return self._on

    @on.setter
    def on(self, v: bool) -> None:
        v = bool(v)
        if v != self._on:
            self._on = v
            self._px._send(("hud", 0, v))

    @property
    def fade(self) -> float:
        return 1.0 if self._on else self.remote_fade

    def set(self, grid: bytes, tone: bytes) -> None:
        self._px._send(("hud_text", 0, (grid, tone)))


_SLOT_FIELDS = ("level", "target", "amount_now", "failed", "slow", "removing")
_SUMMARY0 = {"frames": 0, "rendered": 0, "late_frames": 0, "repeats": 0, "resyncs": 0, "frames_encoded": 0,
             "encode_queue": 0, "bitrate_mbps": None}
_RING0 = {"p50": None, "p95": None, "max": None, "n": 0}


class EngineProxy:
    """Engine's interface as sessions.Session (and fx_state, hud, server) use it, over a render worker."""
    remote = True

    def __init__(self, *, profile: Any, gpu: int, post: Callable, listener: Any, name: str, pool: WorkerPool):
        from .layers import SourceLayer
        self.profile, self.gpu, self.post, self.listener, self.name = profile, gpu, post, listener, name
        self.pool = pool
        self.fps = profile.fps
        self.encoder = "ffmpeg"
        self.degrade_mode = "on"                    # the degradation controller runs in the worker (degrade.py)
        self.sound: Any = None
        self.frame_hook: Any = None
        self.bus = _Bus()
        self.hud: Optional[_Hud] = None
        self.ctx: Optional[_Ctx] = None
        self.root = SourceLayer(None, 0.0)          # the scene layer's rate_div / cost_ema for the panel
        self.server_ms = _Ring()
        self.encoder_active: Optional[str] = None
        self.encoder_fallback: Optional[str] = None
        self.table = HandleTable()                  # hid -> the stand-in (weak: the Session holds them)
        self.slot_shadows: dict[str, Any] = {}      # engine slot id -> its stand-in (weakref)
        self._gone_q: collections.deque = collections.deque()
        self._stats: Optional[dict] = None
        self._fbase: Optional[tuple[int, float]] = None
        self._synced: dict = {}
        self._local: Any = None
        self._stopped = False
        self._started = False
        self._lock = threading.Lock()
        self.t_created = time.monotonic()
        self.t_ready: Optional[float] = None
        self.conn = pool.acquire(gpu, self)
        self.worker: dict = {"mode": "proc", "n": None if self.conn is None else self.conn.n}

    # -- plumbing --------------------------------------------------------------------------------------------
    def _send(self, msg: Any) -> None:
        c = self.conn
        if c is not None and not self._stopped and self._local is None:
            c.send(msg)

    def _call(self, op: str, data: Any, timeout: float = CALL_S) -> Any:
        c = self.conn
        if c is None:
            raise WorkerError("no render worker")
        if self._stopped:
            raise WorkerError("session stopped")
        return c.call(op, data, timeout)

    def _hid(self, obj: Any) -> Optional[int]:
        return self.table.hid_of(obj)

    def _track(self, obj: Any) -> int:
        import weakref
        hid = self.table.add(obj, owned=False)
        q = self._gone_q
        try:                                         # the stand-in dropped here: its twin goes there too
            weakref.finalize(obj, q.append, hid)
        except TypeError:
            pass
        return hid

    # -- falling back to this process (degrade, never refuse) ----------------------------------------------
    def _fallback(self, why: str) -> Any:
        from . import sessions                      # its Engine (the tests' fake when they swap it)
        log.warning("[%s] render worker unavailable (%s): this session renders in the service process", self.name, why)
        self.pool.fallbacks += 1
        local = sessions.Engine(profile=self.profile, gpu=self.gpu, post=self.post, listener=self.listener,
                                name=self.name)
        local.encoder = self.encoder
        local.degrade_mode = self.degrade_mode
        local.sound = self.sound
        local.frame_hook = self.frame_hook
        if getattr(local, "bus", None) is not None:
            local.bus.runtime = self.bus.runtime
        local.worker = {"mode": "inproc", "fallback": why[:300]}
        self._local = local
        c, self.conn = self.conn, None
        if c is not None:
            c.kill(f"session {self.name} fell back: {why}")
            self.pool.release(c, self)
        s = self.listener
        s.engine = local
        self.post(functools.partial(s.emit, "session.warning",
                                    detail=f"render worker unavailable ({why[:160]}): running in the service process"))
        return local

    # -- set-up (executor threads, as the Engine's) --------------------------------------------------------
    def create_context(self) -> None:
        if self._local is not None:
            return self._local.create_context()
        try:
            c = self.conn
            if c is None:
                raise WorkerError("spawn failed")
            if not c.ready.wait(READY_S):
                raise WorkerError(f"not up in {READY_S:g} s")
            if c.dead:
                raise WorkerError(c.dead)
            if self._stopped:
                raise WorkerError("session stopped")
            s = self.listener
            rt, sp = s.runtime, s.sampler
            c.call("bind", {"sid": self.name, "profile": self.profile, "screen": s.screen,
                            "fixture": sp.fixture_name, "lead": sp.lead, "follow": sp.follow,
                            "sound": s.sound is not None, "hud": bool(getattr(s, "hud_wanted", False)),
                            "rt": {"speed": rt.speed, "depth": rt.depth, "speed_target": rt.speed_target,
                                   "depth_target": rt.depth_target, "ease_s": rt.ease_s, "palette": rt.palette,
                                   "palette_s": rt.palette_s, "extra": dict(rt.extra or {})}})
            if self._stopped:                        # ended while it bound: undo (bind and stop run in order there)
                c.call("stop", {"sid": self.name}, STOP_S)
                raise WorkerError("session stopped")
            self.ctx = _Ctx(s.screen)
            self.worker = {"mode": "proc", "n": c.n, "pid": c.pid}
        except WorkerError as e:
            if self._stopped:
                raise
            return self._fallback(str(e)).create_context()
        except Exception as e:
            if self._stopped:
                raise
            return self._fallback(f"bind failed: {type(e).__name__}: {e}").create_context()

    def _image(self, src: Any) -> dict:
        if getattr(src, "_rgb", None) is not None:
            return {"rgb": list(src._rgb)}
        px = src._pixels
        try:
            SHM_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = SHM_DIR / f"{os.getpid()}-{self.name}-{id(src):x}.rgb"
            px.tofile(path)
            return {"shm": str(path), "shape": tuple(px.shape), "dtype": str(px.dtype)}
        except OSError:
            return {"pixels": px}

    def _prepare(self, obj: Any, spec: dict) -> Any:
        hid = self._track(obj)
        spec.update(hid=hid, sid=self.name)
        img = spec.get("image") or {}
        try:
            self._call("prepare", spec)
        except BaseException:
            self.table.drop(hid)
            raise
        finally:
            if "shm" in img:
                try:
                    os.unlink(img["shm"])
                except OSError:
                    pass
        return obj

    def prepare(self, source: Any, params: dict) -> Any:
        if self._local is not None:
            return self._local.prepare(source, params)
        reg = self.listener.mgr.registry
        spec = {"kind": "source", "cls": cls_ref(type(source), reg), "params": params,
                "mod": _mod_spec(getattr(source, "modulation", None))}
        image = getattr(source, "name", None) == "image" and hasattr(source, "_pixels")   # the anchor / an exit
        if image:                                    # target (not isinstance: a rescan may have reloaded its module)
            spec["image"] = self._image(source)
        self._prepare(source, spec)
        if image:
            source._pixels = None                    # the host copy is no longer needed here
        return source

    def prepare_transition(self, transition: Any) -> Any:
        if self._local is not None:
            return self._local.prepare_transition(transition)
        spec = {"kind": "transition", "cls": cls_ref(type(transition), self.listener.mgr.registry),
                "params": transition.params}
        return self._prepare(transition, spec)

    def prepare_effect(self, effect: Any, params: dict) -> Any:
        if self._local is not None:
            return self._local.prepare_effect(effect, params)
        spec = {"kind": "effect", "cls": cls_ref(type(effect), self.listener.mgr.registry), "params": params}
        return self._prepare(effect, spec)

    def start(self, anchor: Any) -> None:
        if self._local is not None:
            return self._local.start(anchor)
        res = self._call("start", {"sid": self.name, "anchor": self._hid(anchor), "encoder": self.encoder,
                                   "degrade": self.degrade_mode})
        self.encoder_active, self.encoder_fallback = res.get("encoder_active"), res.get("encoder_fallback")
        if res.get("hud"):
            h = _Hud(self)
            h._on = bool(getattr(self.listener, "hud_wanted", False))
            self.hud = h
        self._started = True
        self.pool.post(self.pool.watched.add, self)

    def stop(self, timeout: float = 5.0) -> None:
        if self._local is not None:
            return self._local.stop(timeout)
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        self.pool.post(self.pool.watched.discard, self)
        c = self.conn
        if c is None:
            return
        if c.dead:
            self.pool.release(c, self)
            return
        try:
            if c.ready.is_set():                     # a no-op there unless it serves this session
                c.call("stop", {"sid": self.name}, STOP_S)
        except Exception as e:
            c.kill(f"stop failed: {e}")
        self.pool.release(c, self)

    # -- commands (loop thread) ----------------------------------------------------------------------------
    def submit(self, cmd: Any) -> None:
        if self._local is not None:
            return self._local.submit(cmd)
        from .effects import FxUpdate
        from .renderer import Retire, StartTransition, UpdateParams
        if self._stopped or self.conn is None:
            return
        if isinstance(cmd, StartTransition):
            src = self._ref(cmd.source)
            trans = self._trans_ref(cmd.transition)
            if src is None or trans is None:
                log.error("[%s] %s transition with an object the worker does not have: skipped", self.name, cmd.kind)
                return
            msg = ("st", cmd.kind, cmd.tid, src, trans, cmd.at_mono)
        elif isinstance(cmd, UpdateParams):
            hid = self._hid(cmd.source)
            if hid is None:
                return
            msg = ("up", hid, cmd.params, cmd.bindings)
        elif isinstance(cmd, Retire):
            hid = self._hid(cmd.obj)
            if hid is None:
                return
            self.table.drop(hid)
            msg = ("retire", hid)
        elif isinstance(cmd, FxUpdate):
            adds = [self._slot(s) for s in cmd.add]
            if any(a is None for a in adds):
                log.error("[%s] FX update %d with an effect the worker does not have: skipped", self.name, cmd.rev)
                return
            msg = ("fx", cmd.rev, adds, list(cmd.update), cmd.fade_s, cmd.master)
        else:
            log.error("[%s] unknown engine command %r", self.name, cmd)
            return
        self._send(("submit", 0, msg))

    def _ref(self, src: Any) -> Optional[dict]:
        hid = self._hid(src)
        if hid is None:
            return None
        out: dict = {"hid": hid}
        chain = getattr(src, "fx_chain", None)
        if chain is not None:
            slots = [self._slot(s) for s in chain.slots]
            if any(s is None for s in slots):
                return None
            out["chain"] = slots
        return out

    def _trans_ref(self, t: Any) -> Optional[dict]:
        hid = self._hid(t)
        if hid is not None:
            return {"hid": hid}
        try:                                         # never prepared (no context then): the render thread sets it up
            return {"make": {"kind": "transition", "cls": cls_ref(type(t), self.listener.mgr.registry),
                             "params": t.params}}
        except Exception:
            return None

    def _slot(self, s: Any) -> Optional[dict]:
        import weakref
        hid = self._hid(s.effect)
        if hid is None:
            return None
        self.slot_shadows[s.slot_id] = weakref.ref(s)
        return {"slot_id": s.slot_id, "hid": hid, "name": s.name, "preset": s.preset, "stage": s.stage,
                "mix": s.mix, "level": s.level, "target": s.target, "fade_s": s.fade_s, "seq": s.seq,
                "where": s.where, "label": s.label, "mod": _mod_spec(s.modulation)}

    def request_keyframe(self) -> None:
        if self._local is not None:
            return self._local.request_keyframe()
        if self._started:
            self._send(("key", 0, None))

    # -- the worker's side, back on the loop ---------------------------------------------------------------
    def _event(self, name: str, args: tuple) -> None:
        if self._local is not None:
            return
        if name == "on_plugin_error" and args and isinstance(args[0], dict):
            info = dict(args[0])
            obj = self.table.get(info.get("obj_id"))
            info["obj_id"] = id(obj) if obj is not None else None
            args = (info,)
        fn = getattr(self.listener, name, None)
        if fn is not None:
            fn(*args)

    def _media(self, frame_id: int, key: bool, msg: bytes) -> None:
        if self._local is not None:
            return
        if key and self.t_ready is None:
            self.t_ready = time.monotonic()
            c = self.conn
            if c is not None and c.life.ready_at is not None and c.life.binds == 1:
                self.pool.cold.append((round(c.life.ready_at - c.life.spawned_at, 3),
                                       round(self.t_ready - self.t_created, 3)))
        self.listener.on_media(frame_id, key, msg)

    def _worker_gone(self, why: str) -> None:
        """Loop thread: the worker died under a started session -> the session fails (room's fallback).  Before the
        start, the call in flight fails instead (or the bind falls back to this process)."""
        if self._stopped or self._local is not None or not self._started:
            return
        self.listener.on_failed(f"render worker exited: {why}")

    def _on_stats(self, st: dict) -> None:
        if self._local is not None:
            return
        self._stats = st
        self._fbase = (int(st.get("frames") or 0), float(st.get("t") or 0.0))
        self.encoder_active = st.get("encoder_active") or self.encoder_active
        self.encoder_fallback = st.get("encoder_fallback")
        self.bus.level = float(st.get("bus_level") or 0.0)
        self.server_ms.cached = st.get("server_ms") or dict(_RING0)
        h, hs = self.hud, st.get("hud")
        if h is not None and hs:
            h.remote_fade, h.ema_ms, h.failed = float(hs.get("fade") or 0.0), hs.get("ema_ms"), hs.get("failed")
        lay = st.get("layer") or {}
        self.root.rate_div = self.root.div_now = int(lay.get("rate_div") or 1)   # (the worker sends what it renders at)
        self.root.cost_ema = lay.get("cost_ema")
        for sid, vals in (st.get("slots") or {}).items():
            r = self.slot_shadows.get(sid)
            sl = r() if r is not None else None
            if sl is None:
                self.slot_shadows.pop(sid, None)
                continue
            for k in _SLOT_FIELDS:
                if k in vals:
                    setattr(sl, k, vals[k])
            mod = sl.modulation
            if mod is not None:
                mod.last = vals.get("last") or {}
                rows = vals.get("rows") or {}
                mod.section_row = lambda k, rows=rows: dict(rows.get(k) or {})
        for hid, vals in (st.get("mods") or {}).items():
            obj = self.table.get(hid)
            mod = getattr(obj, "modulation", None)
            if mod is None:
                continue
            mod.last = vals.get("last") or {}
            mod._colour_k = float(vals.get("colour_k") or 0.0)
            d = vals.get("describe") or []
            mod.describe = lambda d=d: list(d)

    def tick(self, now: float) -> None:
        """Loop thread, 10 Hz while started: this process's copy of the session's signals (directors, cues, the
        inputs API), then the targets the worker's copy eases to; stand-ins dropped here go there too."""
        s = self.listener
        if self._stopped or self._local is not None or s.state in ("ended", "failed"):
            return
        s._frame_hook(now)
        rt, sp = s.runtime, s.sampler
        cur = {"speed_target": rt.speed_target, "depth_target": rt.depth_target, "palette": rt.palette,
               "palette_s": rt.palette_s, "ease_s": rt.ease_s, "extra": rt.extra, "lead": sp.lead,
               "follow": sp.follow}
        diff = {k: v for k, v in cur.items() if self._synced.get(k, self) != v}
        if diff:
            self._synced.update(diff)
            self._send(("sync", 0, {"sid": self.name, **diff}))
        if self._gone_q:
            hids = []
            while self._gone_q:
                hids.append(self._gone_q.popleft())
            self._send(("gone", 0, hids))

    # -- reporting (loop thread, from the last stats) ------------------------------------------------------
    @property
    def frames(self) -> int:
        if self._local is not None:
            return self._local.frames
        b = self._fbase
        if b is None:
            return 0
        if self._stopped:
            return b[0]
        from .renderer import pace_clock
        return b[0] + int(max(0.0, pace_clock() - b[1]) * self.fps)

    def summary(self) -> dict:
        if self._local is not None:
            return self._local.summary()
        st = self._stats or {}
        e = dict(st.get("summary") or {})
        for k, v in _SUMMARY0.items():
            e.setdefault(k, v)
        for k in ("render_ms", "write_ms", "slip_ms", "host_ms", "encode_ms"):
            e.setdefault(k, dict(_RING0))
        e.setdefault("encoder", self.encoder_active or self.encoder)
        e.setdefault("encoder_fallback", self.encoder_fallback)
        return e

    def window_stats(self, seconds: float = 2.0) -> dict:
        if self._local is not None:
            return self._local.window_stats(seconds)
        return dict((self._stats or {}).get("window") or {})

    def guard_stats(self) -> dict:
        if self._local is not None:
            return self._local.guard_stats()
        return dict((self._stats or {}).get("guard") or {"samples": 0, "trips": 0, "backlog": 0, "enabled": False})

    def frame_records(self, since: int = -1, limit: int = 4096) -> list[dict]:
        if self._local is not None:
            return self._local.frame_records(since, limit)
        try:
            return self._call("frames", {"since": since}, 3.0)[:limit]
        except Exception as e:
            log.warning("[%s] frame records from the worker failed: %s", self.name, e)
            return []
