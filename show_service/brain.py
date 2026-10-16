"""brain.py: the show-brain thread (README "Stage 3", S3.3 and S3.4). One thread, one asyncio loop, every Stage-3
component on it.

```
[show-brain]  Brain.start(): ctx.loop, ctx.persist = dirty; state.json -> comp.restore(); show.yaml -> ctx.settings
              -> comp.configure(); comp.start(); then
              ticks      one fixed-rate loop per rate (director 10 Hz; room/situations/autopilot/sources/listener 1 Hz),
                         components in add order, each call guarded and timed twice: wall (perf_counter, which
                         includes waiting for the GIL behind show-rx: the lag signal) and CPU (thread_time: the
                         component's own work, what the S3.3 budgets apply to)
              house      every 0.25 s: state.json when due (at most once per PERSIST_MIN_S, after a change), show.yaml
                         every RELOAD_S (mtime or size changed: valid -> swap ctx.settings and configure() every
                         component; invalid -> keep the last good, show the error), the loop's lag
              tasks      the components' own (RoomLink's socket, listener calls, art fetches, child-process polls)
```

* **Threads.** Everything a component owns lives on show-brain. Other threads use ``call()`` (show-bus: api.py),
  ``dirty()`` and ``snapshot()``, and the components' own any-thread readers (``palette_fn``, ``view`` ...).
* **Degrade, never refuse.** A component that raises in restore/configure/start/tick/persist/stop is counted and
  logged (at most every 30 s per call site), and everything else carries on. A missing or bad show.yaml at start
  gives ``{}`` (every component's neutral values) and an error in ``snapshot()["settings"]``; a bad edit later keeps
  the last good settings. A torn state.json is moved aside by state_store and the brain starts cold.
* **Persistence** is state_store.StateStore: ``ctx.persist`` = ``dirty()``; written on show-brain at most once a
  second after a change, and once more at stop.
"""

from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import hashlib
import inspect
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from . import contract
from .state_store import StateStore

log = logging.getLogger("show_service.brain")

HOUSE_S = 0.25                      # housekeeping: state.json when due, the show.yaml poll, the loop-lag probe
GUARD_LOG_S = 30.0
START_TIMEOUT_S = 10.0
LAG_SAMPLES = 600
TICK_SAMPLES = 600
VIEW_EVERY_S = 1.0                  # tick stats (p95) recomputed this often for snapshot()
SHUTDOWN_WAIT_S = 1.0               # cancelled tasks get this long to finish (RoomLink closes its session)


class _Guard:
    """Counts a call site's exceptions; logs the first and then at most one every GUARD_LOG_S."""
    __slots__ = ("name", "errors", "_next", "last")

    def __init__(self, name: str) -> None:
        self.name, self.errors, self._next, self.last = name, 0, 0.0, None

    def failed(self, e: BaseException) -> None:
        self.errors += 1
        self.last = f"{type(e).__name__}: {e}"
        now = time.monotonic()
        if now >= self._next:
            self._next = now + GUARD_LOG_S
            log.error("%s failed (%d so far; logged at most every %.0f s)", self.name, self.errors, GUARD_LOG_S,
                      exc_info=(type(e), e, e.__traceback__))


class _TickStats:
    __slots__ = ("hz", "ms", "cpu_ms", "late", "guard", "ticks")

    def __init__(self, name: str, hz: float) -> None:
        self.hz = float(hz or 0.0)
        self.ms: collections.deque = collections.deque(maxlen=TICK_SAMPLES)       # wall: includes GIL waits
        self.cpu_ms: collections.deque = collections.deque(maxlen=TICK_SAMPLES)   # show-brain's own CPU time
        self.late = 0
        self.ticks = 0
        self.guard = _Guard(f"{name}.tick")


def _p95(values: Any) -> Optional[float]:
    v = sorted(values)
    if not v:
        return None
    return round(v[min(len(v) - 1, int(0.95 * len(v)))], 3)


def parse_settings(raw: bytes) -> dict:
    """show.yaml's bytes -> a dict. Empty -> {}. Raises ValueError when it isn't YAML or isn't a mapping."""
    import yaml
    try:
        data = yaml.safe_load(raw.decode("utf-8")) if raw.strip() else {}
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        raise ValueError(f"isn't valid YAML ({str(e).splitlines()[0][:120]})") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"isn't a mapping (it is a {type(data).__name__})")
    return data


class Brain:
    """README S3.4. ``add()`` every component (main thread), then ``start()``; ``stop()`` at the end."""

    def __init__(self, ctx: contract.BrainContext, *, show_file: Path = contract.SHOW_FILE,
                 state_file: Path = contract.STATE_FILE, wall: Callable[[], float] = time.time,
                 mono: Callable[[], float] = time.monotonic, reload_s: float = contract.RELOAD_S) -> None:
        self.ctx = ctx
        self.show_file = Path(show_file)
        self.store = StateStore(Path(state_file), wall=wall, mono=mono)
        self.wall, self.mono = wall, mono
        self.reload_s = float(reload_s)
        self.components: dict[str, Any] = {}
        self.modules: dict[str, str] = {}                 # __main__ fills it (import status of the Stage-3 modules)
        self.running = False                              # api.py answers 503 "starting" until this is True
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._tasks: list[asyncio.Task] = []
        self._stats: dict[str, _TickStats] = {}
        self._g = {k: _Guard(k) for k in ("restore", "configure", "start", "persist", "stop", "settings")}
        self._settings_sig: Any = None
        self._settings_next = 0.0
        self.settings_error: Optional[str] = None
        self.settings_revision: Optional[str] = None
        self._lag: collections.deque = collections.deque(maxlen=LAG_SAMPLES)
        self._tick_view: dict = {}
        self._lag_p95: Optional[float] = None
        self._view_next = 0.0
        self.started_at: Optional[float] = None

    # -- building (main thread) ------------------------------------------------------------------------------------
    def add(self, comp: Any) -> None:
        """Before start(): register ``comp`` and put its ``provides()`` into ctx (``on_mode_change`` is appended)."""
        if self._thread is not None:
            raise RuntimeError("Brain.add after start()")
        name = getattr(comp, "name", None)
        if not isinstance(name, str) or not name:
            raise ValueError(f"a component needs a name: {comp!r}")
        if name in self.components:
            raise ValueError(f"duplicate component name {name!r}")
        self.components[name] = comp
        self._stats[name] = _TickStats(name, getattr(comp, "tick_hz", 0.0))
        try:
            provided = comp.provides() or {}
        except Exception as e:
            log.exception("%s.provides() failed: its ctx slots stay empty", name)
            self.modules.setdefault(name, f"failed: provides: {type(e).__name__}: {e}")
            provided = {}
        for k, v in provided.items():
            if k == "on_mode_change":
                self.ctx.on_mode_change.append(v)
            elif hasattr(self.ctx, k):
                setattr(self.ctx, k, v)
            else:
                log.warning("%s provides %r, which BrainContext has no slot for (ignored)", name, k)

    # -- lifecycle ---------------------------------------------------------------------------------------------------
    def start(self) -> None:
        """Start "show-brain" and return once its loop runs and every component has started."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="show-brain", daemon=True)
        self._thread.start()
        if not self._ready.wait(START_TIMEOUT_S):
            log.error("show-brain didn't finish starting within %.0f s (it carries on starting)", START_TIMEOUT_S)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            try:
                loop.run_until_complete(self._startup())
            except Exception:
                log.exception("show-brain start-up failed; the brain carries on with what started")
                self.running = True
            finally:
                self._ready.set()
            loop.run_forever()
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.wait(pending, timeout=SHUTDOWN_WAIT_S))
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor(timeout=SHUTDOWN_WAIT_S))
            except Exception:
                log.exception("show-brain loop shutdown")
            finally:
                loop.close()

    async def _startup(self) -> None:
        ctx = self.ctx
        ctx.loop = self._loop
        ctx.persist = self.dirty
        saved = self.store.load()
        if self.store.load_error:
            log.error("state: %s", self.store.load_error)
        elif saved:
            log.info("state: restored %s from %s", ", ".join(sorted(saved)), self.store.path)
        for name, comp in self.components.items():
            self._guarded("restore", name, comp.restore, saved.get(name) or {})
        settings = self._read_settings(first=True)
        ctx.settings = settings if settings is not None else {}
        self._configure_all()
        for name, comp in self.components.items():
            self._guarded("start", name, comp.start)
        loop = asyncio.get_running_loop()
        rates: dict[float, list[str]] = {}
        for name, comp in self.components.items():
            hz = float(getattr(comp, "tick_hz", 0.0) or 0.0)
            if hz > 0:
                rates.setdefault(hz, []).append(name)
        for hz, names in sorted(rates.items(), reverse=True):
            self._tasks.append(loop.create_task(self._tick_loop(hz, names), name=f"show-brain-tick-{hz:g}hz"))
        self._tasks.append(loop.create_task(self._housekeeping(), name="show-brain-house"))
        self.started_at = self.mono()
        self.running = True
        log.info("show-brain up: %s; settings %s%s", ", ".join(f"{n} {self._stats[n].hz:g} Hz"
                                                             for n in self.components) or "no components",
                 self.show_file, f" ({self.settings_error})" if self.settings_error else "")

    def stop(self, timeout: float = 5.0) -> None:
        """comp.stop() in reverse add order (on show-brain), a final state.json, then the loop and the thread."""
        loop, th = self._loop, self._thread
        if th is None:
            return
        if loop is not None and loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
                fut.result(timeout)
            except concurrent.futures.TimeoutError:
                log.error("show-brain shutdown took over %.0f s", timeout)
            except Exception:
                log.exception("show-brain shutdown")
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        th.join(timeout)
        self._thread = None

    async def _shutdown(self) -> None:
        self.running = False
        for t in self._tasks:
            t.cancel()
        self._tasks = []
        for name, comp in reversed(list(self.components.items())):
            self._guarded("stop", name, comp.stop)
        self.store.flush(self._collect, force=True)
        await asyncio.sleep(0.05)                          # cancelled component tasks run their cleanup

    # -- any thread --------------------------------------------------------------------------------------------------
    async def call(self, fn: Callable, *args: Any, timeout: float = contract.BRAIN_CALL_TIMEOUT_S) -> Any:
        """From another loop (show-bus): ``fn(*args)`` on show-brain, awaited there if it returns an awaitable.
        Raises asyncio.TimeoutError (the call is cancelled), RuntimeError when the brain isn't running, or fn's own
        exception (api.py turns ValueError/LookupError into page words)."""
        loop = self._loop
        if loop is None or loop.is_closed() or not self.running:
            raise RuntimeError("brain_not_running")
        try:
            here = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        if here is loop:                                   # already on show-brain
            return await asyncio.wait_for(self._invoke(fn, args), timeout)
        cf = asyncio.run_coroutine_threadsafe(self._invoke(fn, args), loop)
        try:
            return await asyncio.wait_for(asyncio.wrap_future(cf), timeout)
        except asyncio.TimeoutError:
            cf.cancel()
            raise

    @staticmethod
    async def _invoke(fn: Callable, args: tuple) -> Any:
        v = fn(*args)
        if inspect.isawaitable(v):
            v = await v
        return v

    def dirty(self) -> None:
        """``ctx.persist`` (any thread): state.json within PERSIST_MIN_S."""
        self.store.dirty()

    def snapshot(self) -> dict:
        """Any thread: ``{"running", "components": {name: comp.snapshot()}, "ticks": {name: {"hz", "p95_ms" (= wall),
        "wall_p95_ms", "cpu_p95_ms", "late", "errors", "last_error"}}, "loop_lag_ms_p95", "settings": {"path", "revision", "error"}, "state_file":
        StateStore.status(), "modules": {name: "ok" | "missing: …" | "failed: …"}, "errors": {site: n}}``."""
        comps = {}
        for name, comp in list(self.components.items()):
            try:
                s = comp.snapshot()
            except Exception as e:
                s = {"error": f"snapshot failed: {type(e).__name__}: {e}"}
            comps[name] = s if isinstance(s, dict) else {}
        return {"running": self.running, "components": comps, "ticks": self._tick_view,
                "loop_lag_ms_p95": self._lag_p95,
                "settings": {"path": str(self.show_file), "revision": self.settings_revision,
                             "error": self.settings_error},
                "state_file": self.store.status(), "modules": dict(self.modules),
                "errors": {k: g.errors for k, g in self._g.items() if g.errors}}

    # -- show-brain ----------------------------------------------------------------------------------------------------
    def _guarded(self, site: str, name: str, fn: Callable, *args: Any) -> Any:
        try:
            return fn(*args)
        except Exception as e:
            self._g[site].failed(e)
            log.debug("%s.%s failed", name, site)
            return None

    def _collect(self) -> dict:
        out = {}
        for name, comp in self.components.items():
            try:
                part = comp.persist()
            except Exception as e:
                self._g["persist"].failed(e)
                continue
            if part is not None:
                out[name] = part
        return out

    def _configure_all(self) -> None:
        settings = self.ctx.settings
        for name, comp in self.components.items():
            self._guarded("configure", name, comp.configure, settings)

    def _read_settings(self, first: bool = False) -> Optional[dict]:
        """show.yaml when it changed (or ``first``): the parsed dict, or None when unchanged or bad (the error is
        kept; at start a bad file gives None, which the caller turns into {})."""
        try:
            st = self.show_file.stat()
            sig: Any = (st.st_mtime_ns, st.st_size)
        except OSError as e:
            sig = ("missing", getattr(e, "errno", None))
        if not first and sig == self._settings_sig:
            return None
        self._settings_sig = sig
        if sig[0] == "missing":
            self.settings_error = f"{self.show_file} not found: neutral settings (every value unset)" if first \
                else f"{self.show_file} not found: keeping the last good settings"
            log.error("settings: %s", self.settings_error)
            return None
        try:
            raw = self.show_file.read_bytes()
            data = parse_settings(raw)
        except (OSError, ValueError) as e:
            what = e.strerror if isinstance(e, OSError) and e.strerror else str(e)
            self.settings_error = (f"{self.show_file.name} {what}: "
                                   + ("neutral settings (every value unset)" if first else "keeping the last good"))
            log.error("settings: %s", self.settings_error)
            return None
        self.settings_error = None
        self.settings_revision = hashlib.sha1(raw).hexdigest()[:12]
        if not first:
            log.info("settings: %s changed (revision %s): reconfiguring", self.show_file.name, self.settings_revision)
        return data

    async def _tick_loop(self, hz: float, names: list[str]) -> None:
        period = 1.0 / hz
        comps = [(n, self.components[n], self._stats[n]) for n in names]
        nxt = self.mono()
        while True:
            now = self.mono()
            if now < nxt:
                await asyncio.sleep(nxt - now)
                now = self.mono()
            late = now - nxt > period * 0.5
            for name, comp, st in comps:
                t0, c0 = time.perf_counter(), time.thread_time()
                try:
                    comp.tick(now)
                except Exception as e:
                    st.guard.failed(e)
                st.cpu_ms.append((time.thread_time() - c0) * 1000.0)
                st.ms.append((time.perf_counter() - t0) * 1000.0)
                st.ticks += 1
                if late:
                    st.late += 1
            nxt += period
            behind = self.mono()
            if nxt <= behind:                              # fell a whole period behind: skip ahead, no burst
                nxt = behind + period
            await asyncio.sleep(0)                         # always yield, so a slow tick can't starve the API

    async def _housekeeping(self) -> None:
        while True:
            t_sleep = self.mono()
            await asyncio.sleep(HOUSE_S)
            self._lag.append(max(0.0, (self.mono() - t_sleep - HOUSE_S) * 1000.0))
            now = self.mono()
            try:
                self.store.flush(self._collect)
            except Exception as e:                         # (flush never raises; belt and braces)
                self._g["persist"].failed(e)
            if now >= self._settings_next:
                self._settings_next = now + self.reload_s
                try:
                    new = self._read_settings()
                    if new is not None:
                        self.ctx.settings = new
                        self._configure_all()
                except Exception as e:
                    self._g["settings"].failed(e)
            if now >= self._view_next:
                self._view_next = now + VIEW_EVERY_S
                self._tick_view = {n: {"hz": s.hz, "p95_ms": _p95(s.ms), "wall_p95_ms": _p95(s.ms),
                                       "cpu_p95_ms": _p95(s.cpu_ms), "late": s.late, "errors": s.guard.errors,
                                       "last_error": s.guard.last, "ticks": s.ticks}
                                   for n, s in self._stats.items()}
                self._lag_p95 = _p95(self._lag)
