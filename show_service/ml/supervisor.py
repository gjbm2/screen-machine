"""The show_service side of the ML worker: spawn it, feed it, read it, restart it (README S4.3).

Two pieces, stdlib + numpy + ``contract`` + ``ml.protocol`` only (no other show_service module), so mllink can wrap
them or follow them:

``FrameFeeder`` (the writer, any one thread; no locks, no waits):
- 16 kHz mono float32 in, ``ML_FRAME_SAMPLES`` (800) frames out, each one non-blocking ``os.write`` of one
  preallocated buffer (header by ``struct.pack_into``): at most ``ML_PIPE_ATOMIC`` bytes, so all-or-nothing;
- a full pipe (``BlockingIOError``) drops the frame, counts it, and flags the next written frame
  ``ML_FLAG_AFTER_DROP``; ``n0`` still advances, so the worker sees the jump;
- a dead worker (``BrokenPipeError``, any ``OSError``) forgets the fd and counts ``broken``;
- a new attach (any thread; a generation and the fd in one store, so none is ever lost), another stream, or
  ``segment()`` starts a segment (``seg`` + 1, ``n0`` 0, ``ML_FLAG_SEGMENT``); ``gap=True`` audio (zero-fill) flags
  every frame it reaches ``ML_FLAG_GAP``. ``busy`` is odd while a write may hold an fd. It never retries or blocks.

``WorkerSupervisor`` (asyncio, on one loop):
- spawns ``[ml.python, -m, ML_WORKER_MODULE, --models, ml.models_dir, --parent, <our pid>, --nice, ml.nice]`` with
  ``cwd=REPO_ROOT``, ``start_new_session=True``, stdin = the read end of our ``os.pipe()`` (1 MB, PIPE_BYTES; the write
  end stays here, O_NONBLOCK), stdout an asyncio pipe (``limit=ML_LINE_MAX``), stderr = ``log_path`` appended after a
  "--- ml-worker start ---" line (the last run's traceback survives the next start), and the environment plus
  ``PYTHONUNBUFFERED=1``, ``CUDA_VISIBLE_DEVICES=``, every ``*_NUM_THREADS=1`` and ``MALLOC_ARENA_MAX=2``;
- sends the config frame (WORKER_KEYS of the ``ml`` section, or what mllink passes) right after the spawn, and again
  on every ``configure`` that keeps the process (a change to python, models_dir or nice restarts it; ``enabled: false``
  stops it: state ``disabled``). A frame that can't be made (too big, a NaN, a value JSON can't carry) is counted
  and not sent; settings it can't use take their starting points (``resolve_checked``);
- reads lines: each is JSON-decoded and checked against contract's shapes (``protocol.check_line``); anything else is
  counted (``bad_lines``) and skipped; good ones go to ``on_line``. The feeder is attached only after ``hello``;
- restarts when the worker exits, or sends nothing for ``restart.stall_s`` while it is fed (before ``hello``: for
  ``stall_s`` after the spawn); the worker exits by itself (70) when one of its lanes is wedged. The stop sequence:
  detach the feeder and wait out a write under way (FEED_RELEASE_S; past it /dev/null goes over our fd, closed once
  that write ends), ``{"cmd": "stop"}``, close our pipe end, 2 s, SIGTERM its process group, 2 s, SIGKILL. Only the
  process it made is ever signalled. An exit with a code over 0 keeps its last log line in ``last_exit["why"]``;
- backoff: ``restart.backoff_s[n - 1]`` (n = failures in a row; the last value for ever after; none under
  MIN_BACKOFF_S); a run of ``restart.healthy_s`` or more resets n first. It never gives up: a python that doesn't
  exist is state ``failed`` ("the ML venv is missing"), and a fault in the supervisor itself ends that run (state
  ``failed``) and is retried on the same backoff; ``stop()`` still detaches the feed and closes the pipe.
"""
from __future__ import annotations

import asyncio
import collections
import fcntl
import itertools
import json
import logging
import math
import os
import pathlib
import signal
import time
from typing import Any, Callable, Optional

import numpy as np

from show_service import contract as C
from show_service.ml import protocol as P

log = logging.getLogger("show_service.ml.supervisor")

STOP_WAIT_S = 2.0                     # README S4.3: stop, wait 2 s, SIGTERM, wait 2 s, SIGKILL
FEED_RELEASE_S = 0.5                  # after attach(None), at most this long for a write already under way on the
                                      # feeding thread (FrameFeeder.busy) to finish before our pipe end is closed; past
                                      # it, /dev/null is put over the fd and it is closed once that write is done
STOP_MAX_S = FEED_RELEASE_S + 3 * STOP_WAIT_S   # the whole stop sequence at worst (brain.STOP_AWAIT_S covers it)
DEFAULT_RESTART = {"backoff_s": [2, 5, 15, 60, 300], "healthy_s": 600, "stall_s": 30}   # S4.9 starting points
MIN_BACKOFF_S = 1.0                   # starting point: no wait between attempts is shorter (a 0 would spin)
PIPE_BYTES = 1 << 20                  # our pipe to the worker: 1 MB (Linux pipe-max-size's default), about 16 s of
                                      # 16 kHz frames, so a starved nice-19 reader drops nothing (frames stay atomic)
THREAD_ENV = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")
MALLOC_ARENAS = "2"                   # glibc malloc arenas for the worker (its two lanes): with one per thread, the
                                      # beats lane's arena kept a Beat This! run's peak (about 570 MB in all against
                                      # about 300 MB, the same cost per run; measured 16 Oct)
#: the show.yaml ``ml`` keys the worker reads: its config frame carries these and nothing else (the process keys,
#: restart, beat_votes and freshness are show_service's own; README S4.3)
WORKER_KEYS = ("cpu_max", "silence_dbfs", "fast_lane", "tasks", "ced_groups")


class FrameFeeder:
    """16 kHz mono float32 -> worker frames (see the module docstring)."""

    def __init__(self, frame_samples: int = C.ML_FRAME_SAMPLES, clock: Callable[[], float] = time.monotonic) -> None:
        if not 1 <= frame_samples <= C.ML_FRAME_MAX_SAMPLES:
            raise ValueError("frame_samples out of range")
        self.n = frame_samples
        self.clock = clock
        self._buf = bytearray(C.ML_HEADER_SIZE + 4 * frame_samples)
        self._pcm = np.frombuffer(self._buf, dtype="<f4", offset=C.ML_HEADER_SIZE)
        self._fd: Optional[int] = None
        self._gens = itertools.count(1)
        self._target: tuple = (0, None)     # (generation, fd): attach() replaces it in one store, never lost
        self._gen_seen = 0                  # the generation write() last took
        self._broken_gen = -1               # the generation whose fd broke (attached reads False until a new one)
        self.busy = 0                       # odd while write() may be using an fd (WorkerSupervisor waits it out)
        self._stream: Optional[str] = None
        self._stream_b = b"\0" * 16
        self._seg = 0
        self._n0 = 0                        # the current frame's first sample in the segment
        self._fill = 0
        self._t0 = 0.0                      # the current frame's first sample's time
        self._flags = 0
        self._new_segment = True
        self.frames = self.bytes = self.drops = self.broken = self.segments = 0
        self.last_write: Optional[float] = None

    def attach(self, fd: Optional[int]) -> None:
        """The worker's stdin write end (O_NONBLOCK), or None. Any thread; taken at the next write; a new attach
        (even of the same fd number) starts a segment."""
        self._target = (next(self._gens), fd)

    def segment(self) -> None:
        """The next frame starts a segment (another rate, contract.AUDIO_SEGMENT, ...)."""
        self._new_segment = True

    @property
    def attached(self) -> bool:
        g, fd = self._target
        return fd is not None and g != self._broken_gen

    def write(self, stream: str, t0: float, pcm: Any, gap: bool = False) -> None:
        """``pcm`` (16 kHz mono) whose first sample sounds at ``t0`` (host s). Returns at once while no fd."""
        self.busy += 1                      # odd: an fd may be in use (one writer thread: no lock needed)
        try:
            self._write(stream, t0, pcm, gap)
        finally:
            self.busy += 1

    def _write(self, stream: str, t0: float, pcm: Any, gap: bool) -> None:
        g, fd = self._target
        if g != self._gen_seen:
            self._gen_seen = g
            self._fd = fd
            self._new_segment = True
        if self._fd is None:
            return
        if stream != self._stream:
            self._stream = stream
            self._stream_b = (stream or "").encode("ascii", "replace")[:16].ljust(16, b"\0")
            self._new_segment = True
        x = np.asarray(pcm, dtype=np.float32).reshape(-1)
        i = 0
        while i < len(x):
            if self._new_segment:
                self._new_segment = False
                self._seg += 1
                self.segments += 1
                self._n0, self._fill, self._flags = 0, 0, C.ML_FLAG_SEGMENT
            if self._fill == 0:
                self._t0 = t0 + i / C.ML_FS
            k = min(self.n - self._fill, len(x) - i)
            self._pcm[self._fill:self._fill + k] = x[i:i + k]
            self._fill += k
            i += k
            if gap:
                self._flags |= C.ML_FLAG_GAP
            if self._fill == self.n:
                self._send()
                if self._fd is None:
                    return

    def _send(self) -> None:
        P.pack_audio_into(self._buf, self._stream_b, self._seg, self._n0, self._t0, self.drops, self._flags, self.n)
        try:
            os.write(self._fd, self._buf)
            self.frames += 1
            self.bytes += len(self._buf)
            self.last_write = self.clock()
            self._flags = 0
        except BlockingIOError:
            self.drops += 1
            self._flags = (self._flags & C.ML_FLAG_SEGMENT) | C.ML_FLAG_AFTER_DROP
            if self._flags & C.ML_FLAG_SEGMENT:     # a dropped segment start: the next frame starts it again
                self._new_segment = True
        except OSError:
            self.broken += 1
            self._fd = None
            self._broken_gen = self._gen_seen
        self._n0 += self.n
        self._fill = 0

    def stats(self) -> dict:
        return {"frames": self.frames, "bytes": self.bytes, "drops": self.drops, "broken": self.broken,
                "segments": self.segments, "attached": self.attached}


def _pos(v: Any) -> Optional[float]:
    """A finite number > 0, else None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    x = float(v)
    return x if math.isfinite(x) and x > 0 else None


def resolve_checked(ml: Any) -> tuple[dict, list[str]]:
    """The keys the supervisor itself reads, over the starting points, and a problem line per value it couldn't use
    (never raises: an owner-edited, hot-reloaded file can't stop the worker for good). ``restart``: stall_s and
    healthy_s finite and > 0; backoff_s a non-empty list of finite numbers, each at least MIN_BACKOFF_S."""
    probs: list[str] = []
    ml = ml if isinstance(ml, dict) else {}
    r = {"backoff_s": [float(x) for x in DEFAULT_RESTART["backoff_s"]], "healthy_s": float(DEFAULT_RESTART["healthy_s"]),
         "stall_s": float(DEFAULT_RESTART["stall_s"])}
    given = ml.get("restart")
    if isinstance(given, dict):
        for k in ("stall_s", "healthy_s"):
            if k in given:
                v = _pos(given[k])
                if v is None:
                    probs.append(f"ml.restart.{k}: {given[k]!r} is not a number over 0: {r[k]:g} used")
                else:
                    r[k] = v
        if "backoff_s" in given:
            raw = given["backoff_s"]
            vals = [_pos(x) if not (isinstance(x, (int, float)) and not isinstance(x, bool) and x == 0) else 0.0
                    for x in raw] if isinstance(raw, list) else []
            if not vals or any(v is None for v in vals):
                probs.append(f"ml.restart.backoff_s: {raw!r} is not a list of seconds: {r['backoff_s']} used")
            else:
                if any(v < MIN_BACKOFF_S for v in vals):
                    probs.append(f"ml.restart.backoff_s: waits under {MIN_BACKOFF_S:g} s raised to it")
                r["backoff_s"] = [max(MIN_BACKOFF_S, v) for v in vals]
    elif given is not None:
        probs.append(f"ml.restart: {given!r} is not a section: the starting points are used")
    enabled = ml.get("enabled", True)
    if not isinstance(enabled, bool):
        probs.append(f"ml.enabled: {enabled!r} is not true/false: true used")
        enabled = True
    nice = ml.get("nice", 19)
    if isinstance(nice, bool) or not isinstance(nice, int) or not 0 <= nice <= 19:
        probs.append(f"ml.nice: {nice!r} is not a whole number 0..19: 19 used")
        nice = 19
    paths = {}
    for k, dflt in (("python", C.ML_PYTHON), ("models_dir", C.MODELS_DIR)):
        v = ml.get(k)
        if v is not None and not (isinstance(v, str) and v):
            probs.append(f"ml.{k}: {v!r} is not a path: {dflt} used")
            v = None
        paths[k] = str(pathlib.Path(str(v or dflt)).expanduser())
    return {"enabled": enabled, "python": paths["python"], "models_dir": paths["models_dir"], "nice": nice,
            "restart": r}, probs


def resolve(ml: Optional[dict]) -> dict:
    """The keys the supervisor itself reads, over the starting points (``resolve_checked`` without its problems)."""
    return resolve_checked(ml)[0]


def worker_section(ml: Any) -> dict:
    """The part of show.yaml ``ml`` that goes to the worker as its config frame: WORKER_KEYS only."""
    return {k: ml[k] for k in WORKER_KEYS if k in ml} if isinstance(ml, dict) else {}


def check_frame(worker: dict) -> Optional[str]:
    """None when ``worker`` makes a config frame (JSON with no NaN or inf, at most ML_CTRL_MAX_BYTES), else why not."""
    try:
        P.config_frame(worker)
        return None
    except Exception as e:                            # noqa: BLE001  ProtocolError, a NaN, a YAML date...
        return f"{type(e).__name__}: {e}"[:200]


class WorkerSupervisor:
    """Spawn, feed, read and restart the worker (see the module docstring). ``start()`` and ``stop()`` on the loop."""

    def __init__(self, ml: Optional[dict] = None, *, log_path: Optional[pathlib.Path] = None,
                 on_line: Optional[Callable[[dict], None]] = None,
                 on_state: Optional[Callable[[str, Optional[str]], None]] = None,
                 extra_args: tuple = (), parent_pid: Optional[int] = None, cwd: pathlib.Path = C.REPO_ROOT,
                 clock: Callable[[], float] = time.monotonic, feeder: Optional[FrameFeeder] = None) -> None:
        self.ml = dict(ml or {})
        self.cfg = resolve(self.ml)
        self.worker = worker_section(self.ml)        # what the config frame carries
        self.log_path = pathlib.Path(log_path or C.ML_LOG)
        self.on_line, self.on_state = on_line, on_state
        self.extra_args, self.cwd, self.clock = tuple(extra_args), cwd, clock
        self.parent_pid = parent_pid or os.getpid()
        self.feeder = feeder if feeder is not None else FrameFeeder(clock=clock)   # mllink passes MLFeed's
        self.state, self.why = "starting", None
        self.since = clock()
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.pid: Optional[int] = None
        self._wfd: Optional[int] = None
        self._task: Optional[asyncio.Task] = None
        self._wake = asyncio.Event()
        self._stopping = False
        self._restart_now = False
        self.restarts = 0
        self.failures_in_row = 0
        self.next_start_at: Optional[float] = None
        self.last_exit: Optional[dict] = None
        self.hello: Optional[dict] = None
        self.last_stats: Optional[dict] = None
        self.lines: collections.Counter = collections.Counter()
        self.bad_lines = 0
        self.last_bad: Optional[str] = None
        self.last_line_t: Optional[float] = None
        self.started_t: Optional[float] = None
        self.attempts = 0
        self.config_errors = 0                       # config frames that couldn't be made (never sent)
        self.last_config_error: Optional[str] = None
        self.run_errors = 0                          # supervisor faults caught by the loop (each ends one run)
        self.pipe_bytes: Optional[int] = None
        self.feed_late = 0                           # stops that put /dev/null over a pipe still being written

    # -- state ---------------------------------------------------------------------------------------------------------
    def _set(self, state: str, why: Optional[str] = None) -> None:
        if (state, why) != (self.state, self.why):
            self.state, self.why, self.since = state, why, self.clock()
            if self.on_state:
                try:
                    self.on_state(state, why)
                except Exception:                     # noqa: BLE001  a callback never stops the supervisor
                    pass

    def snapshot(self) -> dict:
        now = self.clock()
        return {"state": self.state, "why": self.why, "pid": self.pid, "since_s": round(now - self.since, 1),
                "restarts": self.restarts, "failures_in_row": self.failures_in_row,
                "next_start_in_s": (round(max(0.0, self.next_start_at - now), 1)
                                    if self.next_start_at is not None and self.state in ("backoff", "failed")
                                    else None),
                "last_exit": self.last_exit, "log_tail": self.log_tail(), "hello": self.hello,
                "stats": self.last_stats, "feed": self.feeder.stats(), "lines": dict(self.lines),
                "bad_lines": self.bad_lines, "config_errors": self.config_errors, "run_errors": self.run_errors}

    def log_tail(self, n: int = 5) -> list[str]:
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 4096))
                return [ln.decode("utf-8", "replace") for ln in f.read().splitlines()[-n:]]
        except OSError:
            return []

    # -- control -------------------------------------------------------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            self._stopping = False
            self._task = asyncio.get_running_loop().create_task(self._run(), name="ml-supervisor")

    def configure(self, ml: Optional[dict], worker: Optional[dict] = None) -> None:
        """A new ``ml`` section: a config frame (``worker``: what it carries; default ``worker_section(ml)``), a restart
        (python, models_dir, nice), or a stop (enabled false). Never raises."""
        old = self.cfg
        self.ml = dict(ml or {})
        self.cfg = resolve(self.ml)
        self.worker = dict(worker) if worker is not None else worker_section(self.ml)
        if any(old[k] != self.cfg[k] for k in ("python", "models_dir", "nice")) and self.proc is not None:
            self._restart_now = True
        else:
            self._send_config()
        self._wake.set()

    def send_config(self, worker: dict) -> None:
        """Only a new config frame (the process keys unchanged): mllink's CPU share of the owner's 0.8."""
        self.worker = dict(worker)
        self._send_config()

    async def stop(self) -> None:
        """The stop sequence; the feed is detached and our pipe end closed even when the loop had failed."""
        self._stopping = True
        self._wake.set()
        t, self._task = self._task, None
        if t is not None:
            try:
                await t
            except asyncio.CancelledError:
                raise
            except Exception:                         # noqa: BLE001  a failed loop still stops cleanly below
                log.exception("ML supervisor: its loop had failed")
        if self.proc is not None or self._wfd is not None:
            try:
                await self._finish("stopped")
            except asyncio.CancelledError:
                raise
            except Exception:                         # noqa: BLE001
                log.exception("ML supervisor: stop")
        self.feeder.attach(None)

    # -- the loop ------------------------------------------------------------------------------------------------------
    async def _run(self) -> None:
        while not self._stopping:
            try:
                await self._step()
            except asyncio.CancelledError:
                raise
            except Exception as e:                    # noqa: BLE001  never give up: this run ends, then the backoff
                self.run_errors += 1
                why = f"supervisor error: {type(e).__name__}: {e}"[:200]
                log.exception("ML supervisor: %s", why)
                try:
                    if self.proc is not None or self._wfd is not None:
                        await self._finish(why)
                    self.failures_in_row += 1
                    await self._backoff("failed", why)
                except asyncio.CancelledError:
                    raise
                except Exception:                     # noqa: BLE001
                    log.exception("ML supervisor: recovering")
                    await asyncio.sleep(MIN_BACKOFF_S)
        self._set("disabled", "stopped")

    async def _step(self) -> None:
        if not self.cfg["enabled"]:
            self._set("disabled", "ML is off (show.yaml ml.enabled)")
            await self._sleep(None)
            return
        if not os.path.exists(self.cfg["python"]):
            self.failures_in_row += 1
            await self._backoff("failed", "the ML venv is missing")
            return
        ran_for = await self._one_run()
        if self._stopping:
            return
        if ran_for is not None and ran_for >= float(self.cfg["restart"]["healthy_s"]):
            self.failures_in_row = 0
        if self._restart_now or not self.cfg["enabled"]:
            self._restart_now = False
            return                                      # a config change: at once, not a failure
        self.failures_in_row += 1
        await self._backoff("backoff", None)

    async def _backoff(self, state: str, why: Optional[str]) -> None:
        """Wait ``backoff_s[n - 1]``; only a stop, a restart-worthy change (python, models_dir, nice) or ``enabled``
        going false cuts it short (an ordinary config change doesn't)."""
        steps = list(self.cfg["restart"]["backoff_s"]) or [300]
        delay = max(MIN_BACKOFF_S, float(steps[max(0, min(self.failures_in_row, len(steps)) - 1)]))
        self.next_start_at = self.clock() + delay
        self._set(state, why or f"restarting in {delay:g} s")
        while not self._stopping and not self._restart_now and self.cfg["enabled"]:
            left = self.next_start_at - self.clock()
            if left <= 0:
                break
            await self._sleep(left)
        self._restart_now = False
        self.next_start_at = None

    async def _sleep(self, delay: Optional[float]) -> None:
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), delay)
        except asyncio.TimeoutError:
            pass

    async def _one_run(self) -> Optional[float]:
        """Spawn, read until it exits or stalls, then make sure it is gone. Returns how long it ran."""
        r, w = os.pipe()
        os.set_blocking(w, False)
        try:
            self.pipe_bytes = fcntl.fcntl(w, fcntl.F_SETPIPE_SZ, PIPE_BYTES)
        except (OSError, AttributeError):             # a smaller pipe-max-size: the default 64 KB stays
            self.pipe_bytes = None
        env = dict(os.environ, PYTHONUNBUFFERED="1", CUDA_VISIBLE_DEVICES="", MALLOC_ARENA_MAX=MALLOC_ARENAS)
        env.update({k: "1" for k in THREAD_ENV})
        cmd = [self.cfg["python"], "-m", C.ML_WORKER_MODULE, "--models", self.cfg["models_dir"], "--parent",
               str(self.parent_pid), "--nice", str(self.cfg["nice"]), *self.extra_args]
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.hello = None
        self.lines.clear()
        self.attempts += 1
        try:
            # appended (mllink rotates it in place past 1 MB), so the last run's traceback is still there after the
            # next start; each run begins with a header line
            with open(self.log_path, "ab") as logf:
                logf.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} ml-worker start (attempt {self.attempts}, "
                           f"{self.failures_in_row} failures in a row) ---\n".encode())
                logf.flush()
                self.proc = await asyncio.create_subprocess_exec(
                    *cmd, stdin=r, stdout=asyncio.subprocess.PIPE, stderr=logf, cwd=str(self.cwd), env=env,
                    start_new_session=True, limit=C.ML_LINE_MAX)
        except OSError as e:
            os.close(r)
            os.close(w)
            self.last_exit = {"code": None, "why": f"spawn: {e}", "at": self.clock()}
            return None
        os.close(r)
        self._wfd = w
        self.pid = self.proc.pid
        self.started_t = self.last_line_t = self.clock()
        self._set("starting", "loading models")
        self._send_config()
        why = await self._read_until_done()
        await self._finish(why)
        return self.clock() - self.started_t

    def _send_config(self) -> None:
        """The config frame, when it can be made (else counted, not sent: the worker keeps its last config)."""
        if self._wfd is None:
            return
        try:
            frame = P.config_frame(self.worker)
        except Exception as e:                        # noqa: BLE001  mllink checks first; this is the last guard
            self.config_errors += 1
            self.last_config_error = f"{type(e).__name__}: {e}"[:200]
            log.warning("ML supervisor: the config frame can't be made (%s): not sent", self.last_config_error)
            return
        self._send_ctrl(frame)

    def _send_ctrl(self, frame: bytes) -> None:
        if self._wfd is None:
            return
        try:
            os.write(self._wfd, frame)
        except BlockingIOError:
            pass                                       # a full pipe: the next configure() sends it again
        except OSError:
            pass

    async def _read_until_done(self) -> str:
        assert self.proc is not None and self.proc.stdout is not None
        stall_s = float(self.cfg["restart"]["stall_s"])
        read: Optional[asyncio.Future] = None
        try:
            while True:
                if self._stopping:
                    return "stopped"
                if self._restart_now:
                    return "config changed"
                if not self.cfg["enabled"]:
                    return "disabled"
                now = self.clock()
                fed = self.feeder.last_write is not None and self.feeder.last_write > (self.last_line_t or 0)
                if (self.hello is None or fed) and now - (self.last_line_t or now) > stall_s:
                    return f"no line for {stall_s:g} s"
                if read is None:
                    read = asyncio.ensure_future(self.proc.stdout.readline())
                wake = asyncio.ensure_future(self._wake.wait())
                done, _ = await asyncio.wait({read, wake}, timeout=min(1.0, stall_s / 2),
                                             return_when=asyncio.FIRST_COMPLETED)
                if wake in done:
                    self._wake.clear()
                else:
                    wake.cancel()
                if read not in done:
                    continue
                fut, read = read, None
                try:
                    line = fut.result()
                except (ValueError, asyncio.LimitOverrunError):   # a line over ML_LINE_MAX
                    self.bad_lines += 1
                    self.last_bad = "line too long"
                    continue
                if not line:
                    return "exited"
                self._on_line(line)
        finally:
            if read is not None and not read.done():
                read.cancel()

    def _on_line(self, line: bytes) -> None:
        self.last_line_t = self.clock()
        try:
            obj = json.loads(line)
        except ValueError:
            obj = None
        why = P.check_line(obj)
        if why is not None:
            self.bad_lines += 1
            self.last_bad = why
            return
        kind = obj["type"]
        self.lines[kind] += 1
        if kind == C.ML_HELLO:
            self.hello = obj
            self._set("running", None)
            self.feeder.attach(self._wfd)
        elif kind == C.ML_STATS:
            self.last_stats = obj
        if self.on_line:
            try:
                self.on_line(obj)
            except Exception:                         # noqa: BLE001  a callback never stops the reader
                pass

    async def _feed_released(self) -> bool:
        """After ``feeder.attach(None)``: True once no write that may hold the old fd is under way (FrameFeeder.busy:
        odd while one is), waiting at most FEED_RELEASE_S. A write that starts after the attach takes no fd."""
        b = self.feeder.busy
        if b % 2 == 0:
            return True
        end = time.monotonic() + FEED_RELEASE_S
        while self.feeder.busy == b and time.monotonic() < end:
            await asyncio.sleep(0.002)
        return self.feeder.busy != b

    def _close_later(self, w: int) -> None:
        """A write still holds ``w`` after FEED_RELEASE_S: put /dev/null over it (the pipe's write end closes, so the
        worker sees EOF; that write lands in /dev/null), and close the fd number once the write is over, so it can
        never be reused under it."""
        self.feed_late += 1
        try:
            dn = os.open(os.devnull, os.O_WRONLY)
            os.dup2(dn, w)
            os.close(dn)
        except OSError:
            pass
        b = self.feeder.busy
        loop = asyncio.get_running_loop()

        def check() -> None:
            if self.feeder.busy != b:
                try:
                    os.close(w)
                except OSError:
                    pass
            else:
                loop.call_later(0.05, check)
        loop.call_later(0.05, check)

    async def _finish(self, why: str) -> None:
        """The stop sequence (README S4.3); only our own Popen is signalled."""
        self.feeder.attach(None)
        proc, w = self.proc, self._wfd
        self._wfd = None
        if w is not None:
            if await self._feed_released():
                try:
                    os.write(w, P.stop_frame())
                except OSError:
                    pass
                os.close(w)
            else:
                self._close_later(w)
        code = await self._wait(proc, STOP_WAIT_S)
        if code is None:
            self._signal(proc, signal.SIGTERM)
            code = await self._wait(proc, STOP_WAIT_S)
        if code is None:
            self._signal(proc, signal.SIGKILL)
            code = await self._wait(proc, STOP_WAIT_S)
        reason = why
        if code is not None and code > 0:              # its own failure: its last log line says why
            tail = self.log_tail(1)
            if tail:
                reason = f"{why} (code {code}): {tail[-1][:200]}"
        self.last_exit = {"code": code, "why": reason, "at": round(self.clock(), 3),
                          "pid": proc.pid if proc else None}
        self.restarts += 0 if self._stopping else 1
        self.proc, self.pid = None, None

    @staticmethod
    async def _wait(proc: Optional[asyncio.subprocess.Process], timeout: float) -> Optional[int]:
        if proc is None:
            return None
        try:
            return await asyncio.wait_for(proc.wait(), timeout)
        except asyncio.TimeoutError:
            return None

    @staticmethod
    def _signal(proc: Optional[asyncio.subprocess.Process], sig: int) -> None:
        if proc is None or proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, sig)                  # its own session: the group is its pid
        except (ProcessLookupError, PermissionError):
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                pass
