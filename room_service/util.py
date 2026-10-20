"""Small shared helpers: atomic files, tokens, secret redaction, a mailbox."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import secrets
import tempfile
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("room_service")


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Write `data` to `path` atomically (tmp file in the same dir + fsync + rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_bytes(path, json_bytes(obj))


def json_bytes(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n").encode()


class FileWriter:
    """Atomic file writes off the event loop (LIVE_V1 §2.2a.6: no blocking work on the loop).

    `write(path, data)` queues the latest content for `path` (an older unwritten one is
    replaced) and returns at once; one background task writes queued files in a worker
    thread, one at a time, so writes to the same path keep their order. `flush()` waits
    until everything queued so far is on disk; `flush_sync()` writes what is left from
    the calling thread (for the final write at shutdown).
    """

    def __init__(self, name: str = "files") -> None:
        self.name = name
        self._pending: dict[Path, tuple[bytes, int]] = {}
        self._wake: asyncio.Event | None = None
        self._idle: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self.errors = 0

    def write(self, path: Path, data: bytes, mode: int = 0o600) -> None:
        self._pending.pop(Path(path), None)            # re-insert: keeps dict order = queue order
        self._pending[Path(path)] = (data, mode)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.flush_sync()                          # no loop (tests, shutdown): write now
            return
        if self._wake is None:
            self._wake, self._idle = asyncio.Event(), asyncio.Event()
        self._idle.clear()
        self._wake.set()
        if self._task is None or self._task.done():
            self._task = spawn(self._run(), f"filewriter-{self.name}")

    def write_json(self, path: Path, obj: Any) -> None:
        self.write(path, json_bytes(obj))

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._pending:
                path = next(iter(self._pending))
                data, mode = self._pending.pop(path)
                try:
                    await loop.run_in_executor(None, atomic_write_bytes, path, data, mode)
                except OSError as e:
                    self.errors += 1
                    log.error("could not write %s: %s", path, describe_exc(e))
            if not self._pending:
                self._idle.set()

    async def flush(self) -> None:
        if self._idle is not None and self._task is not None and not self._task.done():
            await self._idle.wait()
        self.flush_sync()

    def flush_sync(self) -> None:
        while self._pending:
            path = next(iter(self._pending))
            data, mode = self._pending.pop(path)
            try:
                atomic_write_bytes(path, data, mode)
            except OSError as e:
                self.errors += 1
                log.error("could not write %s: %s", path, describe_exc(e))

    async def close(self) -> None:
        await self.flush()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "rb") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        log.warning("could not read %s (%s); ignoring it", path, type(e).__name__)
        return default


def load_or_create_token(path: Path) -> str:
    """Return the bearer token in `path`, creating a fresh 64-hex token (0600, O_EXCL) if absent."""
    path = Path(path).expanduser()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        token = secrets.token_hex(32)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass  # lost a race with another starter; read theirs below
        else:
            with os.fdopen(fd, "w") as f:
                f.write(token + "\n")
            log.info("created token file %s (mode 0600)", path)
            return token
    st = path.stat()
    if st.st_mode & 0o077:
        log.warning("token file %s is readable by group/others; tightening to 0600", path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path.read_text().strip()


class AlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    """Exclusive flock on <state_dir>/room.lock for the life of the instance (DCL v3 §4.7.1).

    Two instances on one state dir would share (and fight over) the persisted sessions,
    including the record of which stream sessions are ours, so the second one refuses to
    start: a dev run alongside production must pass its own --state-dir.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise AlreadyRunning(f"another room_service holds {self.path}; stop it, or give this "
                                 "one its own --state-dir") from None
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            import fcntl
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


def read_token(path: Path) -> str | None:
    """Read someone else's token file (e.g. stream_service's). None if missing/unreadable."""
    try:
        tok = Path(path).expanduser().read_text().strip()
        return tok or None
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Secret redaction for logs (tokens must never reach a log line or the UI)
# ---------------------------------------------------------------------------

class Redactor:
    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def add(self, secret: str | None) -> None:
        if secret and len(secret) >= 8:
            self._secrets.add(secret)

    def __call__(self, text: str) -> str:
        for s in self._secrets:
            if s in text:
                text = text.replace(s, "***")
        return text


REDACT = Redactor()


class RedactFilter(logging.Filter):
    """Formats the record's message once and strips any known secret from it."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - malformed log call
            return True
        red = REDACT(msg)
        if red != msg or record.args:
            record.msg, record.args = red, None
        if record.exc_info and record.exc_text is None and record.exc_info[1] is not None:
            # Exception text (e.g. aiohttp URLs with ?token=) goes through the redactor too.
            import traceback
            record.exc_text = REDACT("".join(traceback.format_exception(*record.exc_info)).rstrip())
            record.exc_info = None
        return True


def describe_exc(e: BaseException) -> str:
    """A short, secret-free description of an exception (no URLs with query strings)."""
    text = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
    # aiohttp's handshake errors embed the full request URL; cut query strings.
    out = []
    for part in text.split():
        if "?" in part and ("://" in part or part.startswith("/")):
            part = part.split("?", 1)[0] + "?…"
        out.append(part)
    return REDACT(" ".join(out))[:300]


def viewport_of(state: dict | None) -> tuple[float, float] | None:
    """A page's reported viewport {w, h} (kiosk.state), when it is a usable shape."""
    vp = (state or {}).get("viewport")
    if not isinstance(vp, dict):
        return None
    w, h = vp.get("w"), vp.get("h")
    ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) and 64 <= v <= 16384 for v in (w, h))
    return (float(w), float(h)) if ok else None


_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def media_size(path: Path) -> tuple[int, int] | None:
    """(width, height) of a PNG/JPEG (from its header) or a video (ffprobe, 3 s cap); None if unknown.
    Blocking: call it in an executor."""
    try:
        with open(path, "rb") as f:
            head = f.read(1 << 20)
    except OSError:
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
        w, h = int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
        return (w, h) if w > 0 and h > 0 else None
    if head[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(head):
            if head[i] != 0xFF:
                i += 1
                continue
            m = head[i + 1]
            if m in _JPEG_SOF:
                h, w = int.from_bytes(head[i + 5:i + 7], "big"), int.from_bytes(head[i + 7:i + 9], "big")
                return (w, h) if w > 0 and h > 0 else None
            if m in (0xD8, 0x01, 0xFF) or 0xD0 <= m <= 0xD7:
                i += 1 if m == 0xFF else 2
                continue
            i += 2 + int.from_bytes(head[i + 2:i + 4], "big")
        return None
    import subprocess
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        w, h = (int(v) for v in out.splitlines()[0].split("x")[:2])
        return (w, h) if w > 0 and h > 0 else None
    except Exception:
        return None


def fit_pixels(w: float, h: float, budget: float, *, max_side: int = 4096, min_side: int = 64) -> dict[str, int]:
    """(w, h)'s shape at `budget` pixels, up or down: even sides within [min_side, max_side], never over budget."""
    k = math.sqrt(budget / (w * h))
    k = min(k, max_side / max(w, h))
    fw, fh = int(w * k + 1e-6), int(h * k + 1e-6)
    fw, fh = min(max_side, max(min_side, fw - fw % 2)), min(max_side, max(min_side, fh - fh % 2))
    while fw * fh > budget and fw > min_side and fh > min_side:
        fw, fh = fw - 2, fh - 2
    return {"width": fw, "height": fh}


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def utc_iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---------------------------------------------------------------------------
# Mailbox: an ordered, bounded buffer of inbound items with sequence numbers.
#
# Handshake code takes `mark()` *before* it sends a request, then scans forward
# with `next(since)`. Nothing that arrives in between (an event racing the HTTP
# response, say) is lost.
# ---------------------------------------------------------------------------

class Mailbox:
    def __init__(self, maxlen: int = 1000) -> None:
        self._items: deque[tuple[int, dict]] = deque(maxlen=maxlen)
        self._seq = 0
        self._waiters: list[asyncio.Future] = []

    def mark(self) -> int:
        return self._seq

    def put(self, item: dict) -> None:
        self._seq += 1
        self._items.append((self._seq, item))
        waiters, self._waiters = self._waiters, []
        for w in waiters:
            if not w.done():
                w.set_result(None)

    def _first_after(self, since: int) -> tuple[int, dict] | None:
        for seq, item in self._items:
            if seq > since:
                return seq, item
        return None

    async def next(self, since: int, timeout: float) -> tuple[int, dict] | None:
        """The first item with seq > since, waiting up to `timeout` s. None on timeout."""
        found = self._first_after(since)
        if found is not None:
            return found
        if timeout <= 0:
            return None
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        try:
            await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            if fut in self._waiters:
                self._waiters.remove(fut)
        return self._first_after(since)


class Notifier:
    """Wake-everyone primitive for 'something changed' (cheaper than Condition)."""

    def __init__(self) -> None:
        self._fut: asyncio.Future | None = None

    def notify(self) -> None:
        if self._fut is not None and not self._fut.done():
            self._fut.set_result(None)
        self._fut = None

    async def wait(self, timeout: float | None = None) -> bool:
        if self._fut is None or self._fut.done():
            self._fut = asyncio.get_running_loop().create_future()
        try:
            await asyncio.wait_for(asyncio.shield(self._fut), timeout)
            return True
        except asyncio.TimeoutError:
            return False


async def wait_until(pred: Callable[[], bool], notifier: Notifier, timeout: float) -> bool:
    """Wait until pred() is true, re-checking whenever `notifier` fires."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not pred():
        remaining = deadline - loop.time()
        if remaining <= 0:
            return False
        await notifier.wait(min(remaining, 1.0))
    return True


def spawn(coro, name: str) -> asyncio.Task:
    """create_task that logs (instead of silently swallowing) an unexpected crash."""
    task = asyncio.get_running_loop().create_task(coro, name=name)

    def _done(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            log.error("task %s crashed: %s", name, describe_exc(exc), exc_info=exc)

    task.add_done_callback(_done)
    return task
