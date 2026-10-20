"""room_service inside the Screen Machine app process (owner ruling, 14 Oct 22:25: one back end).

app.py calls start_in_background() once at start-up, the same way it starts the legacy WS server.
room_service then runs on a daemon thread with its OWN asyncio loop, aiohttp server and ClientSession,
and serves every one of its HTTP + WebSocket routes on ROOM_HOST:ROOM_PORT (0.0.0.0:8700), the port
the separate service used. The display pages, the Room page and the Windows LAN port-forward keep
working unchanged. The public :5000 door has no room routes. The GPU renderer (stream_service, :8702)
stays a separate worker.

Environment (all optional):
  ROOM_IN_APP=1          0/false/off: don't start it
  ROOM_HOST=0.0.0.0
  ROOM_PORT=8700
  ROOM_CONFIG=config/room/room.prod.yaml   (relative to the repo root)
  ROOM_LOG=logs/room_service.log           (INFO and up; warnings also go to stderr)

Port busy, or the state dir's room.lock held (the separate `scripts/ctl room` is still running): one
warning, then a quiet retry every 10 s, so stopping the separate service is the whole hand-over.
Any other failure (an import, the config, the loop dying) is logged and retried with backoff.
Nothing here ever raises into Flask. room_service never imports Flask, and this module only starts
and stops it.

Standalone, the same code path Flask uses (e.g. a smoke check on a spare port):
  python room_in_app.py --port 8799 --config /tmp/room.yaml [--state-dir DIR] [--no-snapshots]
"""

from __future__ import annotations

import asyncio
import atexit
import errno
import logging
import os
import socket
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = "config/room/room.prod.yaml"
DEFAULT_LOG = "logs/room_service.log"
RETRY_BUSY_S = 10.0
BACKOFF_S = (2.0, 5.0, 15.0, 30.0, 60.0)
HEALTHY_AFTER_S = 60.0        # a run this long resets the backoff

log = logging.getLogger("room_service.in_app")


class _Waiting(Exception):
    """Another room_service has the port or the state dir: wait and retry, no alarm."""


def _truthy(v: str | None, default: bool = True) -> bool:
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")


def _repo_path(p: str | os.PathLike) -> Path:
    p = Path(p).expanduser()
    return p if p.is_absolute() else REPO_ROOT / p


def setup_logging(log_file: str | os.PathLike | None = None, *, console_level: int = logging.WARNING) -> None:
    """room_service's loggers get their own handlers (Flask configures no root logging): a rotating file
    at INFO and stderr (the Flask screen session) at WARNING. Secrets are redacted as in the service."""
    root = logging.getLogger("room_service")
    if getattr(root, "_sm_in_app", False):
        return
    root._sm_in_app = True                          # type: ignore[attr-defined]
    root.setLevel(logging.INFO)
    root.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        from room_service.util import RedactFilter
        redact: logging.Filter | None = RedactFilter()
    except Exception:                               # room_service unimportable: the runner says so itself
        redact = None
    handlers: list[logging.Handler] = []
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(console_level)
    handlers.append(console)
    if log_file:
        try:
            path = _repo_path(log_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=3)
            fh.setLevel(logging.INFO)
            handlers.append(fh)
        except OSError as e:
            console.setLevel(logging.INFO)
            print(f"room_in_app: can't open {log_file} ({e}); room_service logs to stderr", file=sys.stderr)
    for h in handlers:
        h.setFormatter(fmt)
        if redact is not None:
            h.addFilter(redact)
        root.addHandler(h)


def _bind(host: str, port: int) -> socket.socket:
    """Bind the listening socket before anything of room_service starts, so a busy port costs nothing
    (no instance lock, no DMX ports, no headless Chrome). SO_REUSEADDR as aiohttp uses it; on Linux it
    never lets two listeners share a port."""
    fam = socket.AF_INET6 if ":" in host else socket.AF_INET
    s = socket.socket(fam, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        s.listen(128)
        s.setblocking(False)
    except OSError as e:
        s.close()
        if e.errno in (errno.EADDRINUSE, errno.EACCES):
            raise _Waiting(f"port {port} is in use ({e.strerror})") from None
        raise
    return s


class RoomRunner:
    """One supervised room_service on its own thread and event loop."""

    def __init__(self, *, host: str = "0.0.0.0", port: int = 8700, config: str | os.PathLike = DEFAULT_CONFIG,
                 state_dir: str | os.PathLike | None = None, snapshots: bool | None = None,
                 retry_busy_s: float = RETRY_BUSY_S, backoff_s: tuple[float, ...] = BACKOFF_S,
                 on_crash: Callable[[str, BaseException | None], None] | None = None) -> None:
        self.host, self.port = host, int(port)
        self.config = _repo_path(config)
        self.state_dir = Path(state_dir).expanduser() if state_dir else None
        self.snapshots = snapshots                  # None: as the config says
        self.retry_busy_s = retry_busy_s
        self.backoff_s = backoff_s
        self.on_crash = on_crash
        self.state = "idle"                         # idle | starting | running | waiting | backoff | stopped
        self.detail = ""
        self.starts = 0
        self.serving = threading.Event()            # set while room_service is up and listening
        self.thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_serving: asyncio.Event | None = None

    # -- control (any thread) ----------------------------------------------------
    def start(self) -> threading.Thread:
        if self.thread is None or not self.thread.is_alive():
            self._stop.clear()
            self.thread = threading.Thread(target=self._main, name="room-service", daemon=True)
            self.thread.start()
        return self.thread

    def stop(self, timeout: float = 10.0) -> None:
        """Stop cleanly: aiohttp's graceful shutdown, then RoomService.stop() (live sessions are left
        running on purpose; the next start adopts them). Safe to call twice and from atexit."""
        self._stop.set()
        loop, ev = self._loop, self._stop_serving
        if loop is not None and ev is not None:
            try:
                loop.call_soon_threadsafe(ev.set)
            except RuntimeError:                    # that loop has already closed
                pass
        t = self.thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout)

    def wait_serving(self, timeout: float) -> bool:
        return self.serving.wait(timeout)

    # -- the thread ----------------------------------------------------------------
    def _main(self) -> None:
        failures = 0
        warned = None
        while not self._stop.is_set():
            began = time.monotonic()
            try:
                self.state, self.detail = "starting", ""
                asyncio.run(self._serve())
                if self._stop.is_set():
                    break
                raise RuntimeError("room_service's event loop ended by itself")
            except _Waiting as e:
                self.state, self.detail = "waiting", str(e)
                if warned != str(e):
                    log.warning("room_service is not running in the app yet: %s. Another room_service "
                                "(scripts/ctl room) probably has it; retrying every %.0f s", e, self.retry_busy_s)
                    warned = str(e)
                self._stop.wait(self.retry_busy_s)
                continue
            except BaseException as e:              # never let anything out of this thread
                if self._stop.is_set():
                    break
                was_up = self.serving.is_set()
                ran = time.monotonic() - began
                if ran >= HEALTHY_AFTER_S:
                    failures = 0
                delay = self.backoff_s[min(failures, len(self.backoff_s) - 1)]
                failures += 1
                self.state, self.detail = "backoff", f"{type(e).__name__}: {e}"
                log.error("room_service %s after %.0f s (%s); restarting in %.0f s",
                          "died" if was_up else "failed to start", ran, self.detail, delay,
                          exc_info=(type(e), e, e.__traceback__))
                if self.on_crash is not None:
                    try:
                        self.on_crash(f"room_service (in the app) {'died' if was_up else 'failed to start'}; "
                                      f"restarting in {delay:.0f}s", e if isinstance(e, Exception) else None)
                    except Exception:
                        pass
                self._stop.wait(delay)
            finally:
                self.serving.clear()
                self._loop = self._stop_serving = None
            warned = None if self.state != "waiting" else warned
        self.state = "stopped"

    async def _serve(self) -> None:
        sock: socket.socket | None = _bind(self.host, self.port)
        svc = runner = None
        try:
            # Imported here, inside the thread: a missing dependency is this thread's problem, not Flask's.
            from aiohttp import web
            from room_service.api import SafeAccessLogger
            from room_service.config import load_config
            from room_service.service import RoomService
            from room_service.util import AlreadyRunning

            cfg = load_config(self.config)
            if self.state_dir is not None:
                cfg.state_dir = self.state_dir
            if self.snapshots is not None:
                cfg.snapshot.enabled = self.snapshots
            svc = RoomService(cfg)
            runner = web.AppRunner(svc.app, access_log_class=SafeAccessLogger,
                                   access_log=logging.getLogger("room_service.access"), shutdown_timeout=3.0)
            try:
                await runner.setup()                # on_startup: RoomService.start() (instance lock first)
            except AlreadyRunning as e:
                runner = None
                await _abandon(svc)
                raise _Waiting(str(e)) from None
            except BaseException:
                runner = None
                await _abandon(svc)
                raise
            site = web.SockSite(runner, sock)
            await site.start()
            sock = None                             # the site owns it now
            self._loop = asyncio.get_running_loop()
            self._stop_serving = asyncio.Event()
            self.starts += 1
            self.state, self.detail = "running", f"http://{self.host}:{self.port}"
            self.serving.set()
            log.warning("room_service is serving on http://%s:%d inside the app process (config %s)",
                        self.host, self.port, self.config)
            if self._stop.is_set():                 # stop() came before the event existed
                self._stop_serving.set()
            await self._stop_serving.wait()
            log.info("room_service (in the app) stopping")
        finally:
            if sock is not None:
                sock.close()
            if runner is not None:
                try:
                    await runner.cleanup()          # on_shutdown + on_cleanup: RoomService.stop()
                except BaseException as e:
                    log.error("room_service cleanup failed: %s", e)
                    await _abandon(svc)
            if svc is not None:
                _release_lock(svc)


def _release_lock(svc: object) -> None:
    """The instance lock is an flock on a file this process opened: if a failed run kept it, every later
    try in this process would refuse itself. release() is a no-op once released."""
    lock = getattr(svc, "_lock", None)
    if lock is not None:
        try:
            lock.release()
        except Exception:
            pass


async def _abandon(svc: object) -> None:
    """A start-up (or cleanup) that failed half way: stop what got started, in RoomService's own order,
    each part best-effort, then the lock. Tasks left over die with this loop (asyncio.run cancels them)."""
    def part(name: str, method: str) -> Callable[[], object] | None:
        obj = getattr(svc, name, None)
        fn = getattr(obj, method, None) if obj is not None else None
        return fn if callable(fn) else None

    for name, method in (("coordinator", "stop"), ("screens", "stop"), ("dmx", "stop"), ("pages", "close_all"),
                         ("events", "stop"), ("mask", "stop"), ("stream", "stop"), ("snapshotter", "close"),
                         ("http", "close")):
        fn = part(name, method)
        if fn is None:
            continue
        try:
            r = fn()
            if asyncio.iscoroutine(r):
                await asyncio.wait_for(r, 5.0)
        except BaseException:
            pass
    _release_lock(svc)


_runner: RoomRunner | None = None


def start_in_background(*, on_crash: Callable[[str, BaseException | None], None] | None = None) -> RoomRunner | None:
    """Flask's entry point. Reads the ROOM_* environment, starts the supervised thread, registers a clean
    stop at exit, and returns at once. Never raises; None when ROOM_IN_APP is off or setup failed."""
    global _runner
    try:
        if not _truthy(os.environ.get("ROOM_IN_APP"), True):
            print("room_in_app: ROOM_IN_APP is off; room_service is not started in the app", file=sys.stderr)
            return None
        if _runner is not None:
            return _runner
        setup_logging(os.environ.get("ROOM_LOG", DEFAULT_LOG))
        _runner = RoomRunner(host=os.environ.get("ROOM_HOST", "0.0.0.0"),
                             port=int(os.environ.get("ROOM_PORT", "8700")),
                             config=os.environ.get("ROOM_CONFIG", DEFAULT_CONFIG),
                             on_crash=on_crash)
        _runner.start()
        atexit.register(_runner.stop, 8.0)
        return _runner
    except Exception as e:
        print(f"room_in_app: could not start room_service in the app: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def runner() -> RoomRunner | None:
    return _runner


def run_forever(*, port: int = 8700, config: str | os.PathLike = DEFAULT_CONFIG, host: str = "0.0.0.0",
                state_dir: str | os.PathLike | None = None, snapshots: bool | None = None,
                log_file: str | None = None) -> int:
    """Run the in-app runner in the foreground, exactly as Flask runs it, until Ctrl-C or SIGTERM."""
    import signal
    setup_logging(log_file, console_level=logging.INFO)
    r = RoomRunner(host=host, port=port, config=config, state_dir=state_dir, snapshots=snapshots)
    done = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: done.set())
    r.start()
    while not done.wait(0.5):
        if not r.thread or not r.thread.is_alive():
            break
    r.stop()
    return 0


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="room_service exactly as the app runs it (its own thread and loop)")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=int(os.environ.get("ROOM_PORT", "8700")))
    p.add_argument("--config", default=os.environ.get("ROOM_CONFIG", DEFAULT_CONFIG))
    p.add_argument("--state-dir")
    p.add_argument("--no-snapshots", action="store_true")
    p.add_argument("--log-file")
    a = p.parse_args()
    raise SystemExit(run_forever(port=a.port, config=a.config, host=a.host, state_dir=a.state_dir,
                                 snapshots=False if a.no_snapshots else None, log_file=a.log_file))
