"""show-beat as a process (README S4.6; ``Clock(beat_proc=True)``): madmom's beat activation in a child of
show_service, so a madmom frame neither holds show-rx nor takes the GIL from it. Both ends of the link live here. At
import this needs only the stdlib, numpy and ``contract``; madmom is imported in the child only (through
``clock.MadmomActivation``), and ``ml.supervisor`` (for its restart and environment constants) only by the parent.

The wire: two pipes, no JSON, no shared memory (100 hops a second of 1.8 KB is nothing; a pipe gives the child a
blocking read for free and EOF when show_service dies).
- Parent to child (its stdin): HOP = ``HOP_HDR`` (32 B: magic ``SMBH``, version, flags, n = 441, epoch u32, seq u32,
  t_raw f64, t_put f64) + 441 little-endian float32 = 1796 B, one ``os.write`` each. That is under PIPE_BUF, so it is
  atomic even non-blocking: a full pipe refuses the whole hop (``BlockingIOError``), never half of it. Flag
  ``HOP_RESET``: reset the activation before this hop. A reset never travels alone: it rides the next hop.
- Child to parent (its stdout): REC = 40 B (magic ``SMBR``, version, flags, pad, epoch, seq, a f32, act_us u32, t_raw
  and t_put echoed), one ``os.write`` each. Flags: ``REC_RESET_DONE`` (the activation was reset before this hop),
  ``REC_ERROR`` (it raised: ``a`` is NaN), ``REC_HELLO`` (the first record: seq = the child's pid, a = nets, act_us =
  its start-up in ms).
- No control frames: config is argv, and stop is EOF on stdin. A bad magic or header is counted (``junk``) and the
  reader resyncs on the next magic.

The child (``python -m show_service.beatproc --parent PID --activation madmom|fake --nets N --nice N
[--fake-delay-ms D]``), as ml/worker.py: the nice first; fd 1 kept for the protocol and pointed at stderr (so a stray
print can't corrupt it); PR_SET_PDEATHSIG (SIGTERM when the thread that spawned it dies); exit 3 if its parent is
not ``--parent``; build the activation (madmom 2 nets: about 1.1-1.5 s); HELLO; then one thread, one loop: read
stdin, for each whole hop reset if asked (or the epoch moved), ``process_hop`` timed, one record back. EOF or EPIPE
is a clean exit 0. It never looks ahead or skips: overload is the parent's in-flight cap's job. ``FakeActivation``
(mean |hop|, an optional sleep) is for tests.

The parent, ``BeatProc``: the transport the Clock drives from show-rx, with the same face as clock._BeatThread
(``out``, ``cost``, ``done``, ``errors``, ``send_hop``, ``send_reset``, ``poll``, ``ready``, ``alive``, ``inflight``,
``wait``, ``stop``), so the Clock's hop and drain paths are one.
- **show-beat-sup** (a daemon thread) spawns, reads the HELLO itself (show-rx holds no fd until then, so the child
  writes nothing else yet), then publishes ``(generation, write fd, read fd, pid)`` in one attribute store. It
  watches the child (exit, a kill asked for by show-rx, a 30 s HELLO timeout, stop) every 0.1 s, reads its CPU from
  /proc every 10 s, and on exit publishes ``(generation + 1, None, None, None)``. Restarts back off as the ML worker
  does (ml.supervisor DEFAULT_RESTART: 2/5/15/60/300 s, a 600 s run resets the count) and never give up; the whole
  loop is in try/except (if this thread ever ended, PR_SET_PDEATHSIG would take the child, and the Clock would run
  inline: degrade, never refuse). Spawning (17-31 ms in Popen under GIL load) never happens on show-rx.
- **One closer.** Only the supervisor closes fds, and those of generation g only once show-rx has adopted a later one
  (its ``_gen`` is the ack), or at ``close()``; so an fd number can never be reused under a show-rx write. show-rx
  never closes anything: EPIPE, EOF or a stall marks the generation dead.
- show-rx's methods never block and take no lock: ``poll()`` (adopt a newer generation; read records only while
  hops are in flight, else no syscall), ``send_hop()`` (0 sent, 1 pipe full, 2 dead), ``send_reset()`` (rides the
  next hop), ``request_kill()`` (a stall: marks the generation dead, the supervisor kills it).
- ``close()`` (after show-rx has stopped, as Show.stop does): EOF to the child (/dev/null over our write end, so the
  fd number stays ours until the end), 1 s, SIGTERM its process group, 1 s, SIGKILL, reaped (no zombie), every fd
  closed.
"""

from __future__ import annotations

import argparse
import collections
import ctypes
import fcntl
import logging
import math
import os
import pathlib
import select
import signal
import struct
import subprocess
import sys
import threading
import time
from typing import Optional, Sequence

import numpy as np

from .contract import REPO_ROOT

log = logging.getLogger("show_service.beatproc")

# -- the wire ----------------------------------------------------------------------------------------------------------
HOP = 441                          # clock.HOP (a test checks they agree; clock imports this module, not the reverse)
VERSION = 1
HOP_MAGIC, REC_MAGIC = b"SMBH", b"SMBR"
HOP_HDR = struct.Struct("<4sBBHIIdd")          # magic, version, flags, n, epoch, seq, t_raw, t_put: 32 B
HOP_BYTES = HOP_HDR.size + 4 * HOP             # 1796 B, one atomic write
REC = struct.Struct("<4sBBHIIfIdd")            # magic, version, flags, pad, epoch, seq, a, act_us, t_raw, t_put: 40 B
HOP_RESET = 1
REC_RESET_DONE, REC_ERROR, REC_HELLO = 1, 2, 4
U32 = 0xFFFFFFFF
READ_BYTES = REC.size * 256                    # the parent's read size, a whole number of records
CHILD_READ = 65536
assert HOP_BYTES <= select.PIPE_BUF and REC.size <= select.PIPE_BUF   # both writes atomic

# -- the parent's knobs ------------------------------------------------------------------------------------------------
MODULE = "show_service.beatproc"
PIPE_BYTES = 128 << 10             # our pipe to the child: 72 hops, more than clock.BEAT_Q_MAX can put in flight
HELLO_S = 30.0                     # no HELLO this long after a spawn: kill it, back off, try again
STOP_WAIT_S = 1.0                  # close(): EOF, wait 1 s, SIGTERM the group, 1 s, SIGKILL
WATCH_S = 0.1                      # the supervisor's wake-up
CPU_EVERY_S = 10.0                 # the child's CPU share from /proc, this often
LOG_MAX = 1 << 20                  # the child's stderr log moves to .1 past 1 MB (checked before each spawn)
MIN_BACKOFF_S = 0.05               # tests use short backoffs; nothing spins
_CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


# -- frames ------------------------------------------------------------------------------------------------------------
def pack_hop(buf: bytearray, epoch: int, seq: int, hop: np.ndarray, t_raw: float, t_put: float,
             flags: int = 0) -> None:
    """One hop into ``buf`` (HOP_BYTES long), in place: the header, then the 441 samples as float32."""
    HOP_HDR.pack_into(buf, 0, HOP_MAGIC, VERSION, flags, HOP, epoch & U32, seq & U32, t_raw, t_put)
    np.frombuffer(buf, "<f4", HOP, HOP_HDR.size)[:] = hop


def pack_rec(epoch: int, seq: int, a: float, act_us: int, t_raw: float, t_put: float, flags: int = 0) -> bytes:
    return REC.pack(REC_MAGIC, VERSION, flags, 0, epoch & U32, seq & U32, a, max(0, min(U32, int(act_us))),
                    t_raw, t_put)


def _hop_ok(data, o: int) -> bool:
    return data[o + 4] == VERSION and HOP_HDR.unpack_from(data, o)[3] == HOP


def _rec_ok(data, o: int) -> bool:
    return data[o + 4] == VERSION


def split(data, magic: bytes, size: int, ok) -> tuple[list[int], int, int]:
    """The whole frames at the front of ``data`` (bytes): (their offsets, bytes used, junk bytes skipped). A frame
    starts with ``magic`` and passes ``ok(data, offset)``; anything else is junk, skipped to the next magic. A frame
    cut short is left for the next read (``used`` stops before it), as are the last 3 bytes of junk (a magic may be
    starting there)."""
    offs: list[int] = []
    o, n, junk = 0, len(data), 0
    while n - o >= size:
        if data[o:o + 4] == magic and ok(data, o):
            offs.append(o)
            o += size
            continue
        j = data.find(magic, o + 1)
        if j < 0:
            k = max(o, n - 3)
            junk += k - o
            o = k
            break
        junk += j - o
        o = j
    return offs, o, junk


def split_hops(data) -> tuple[list[int], int, int]:
    return split(data, HOP_MAGIC, HOP_BYTES, _hop_ok)


def split_recs(data) -> tuple[list[int], int, int]:
    return split(data, REC_MAGIC, REC.size, _rec_ok)


# -- activations -------------------------------------------------------------------------------------------------------
class FakeActivation:
    """Tests only: mean |hop| (the same value in the parent and in the child: float32 in, float32 out), after an
    optional sleep (a slow child)."""
    name = "fake"
    lag_s = 0.0
    floor = 0.0
    nets = 0

    def __init__(self, delay_ms: float = 0.0) -> None:
        self.delay_s = max(0.0, float(delay_ms)) / 1000.0
        self.resets = 0
        self.hops = 0

    def process_hop(self, hop: np.ndarray) -> float:
        if self.delay_s:
            time.sleep(self.delay_s)
        self.hops += 1
        return float(np.mean(np.abs(hop)))

    def reset(self) -> None:
        self.resets += 1


def make_child_activation(name: str, nets: int, fake_delay_ms: float = 0.0):
    if name == "fake":
        return FakeActivation(fake_delay_ms)
    if name == "madmom":
        from .clock import MadmomActivation
        return MadmomActivation(nets=nets)
    raise ValueError(f"no activation {name!r} in the beat process (madmom or fake)")


# -- the child ---------------------------------------------------------------------------------------------------------
def _log(msg: str) -> None:
    try:
        sys.stderr.write(f"{time.strftime('%H:%M:%S')} beat-worker[{os.getpid()}]: {msg}\n")
        sys.stderr.flush()
    except Exception:                                   # noqa: BLE001  logging never stops the child
        pass


def _set_nice(target: int) -> int:
    try:
        cur = os.getpriority(os.PRIO_PROCESS, 0)
        if target > cur:
            os.setpriority(os.PRIO_PROCESS, 0, min(19, target))
    except OSError as e:
        _log(f"nice: {e}")
    return os.getpriority(os.PRIO_PROCESS, 0)


def _pdeathsig() -> None:
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGTERM, 0, 0, 0)          # PR_SET_PDEATHSIG
    except OSError as e:
        _log(f"prctl: {e}")


def serve(act, in_fd: int, out_fd: int) -> int:
    """The child's loop: hops in, records out, until EOF (0) or a broken pipe (0). One thread, no look-ahead."""
    rem = b""
    cur_epoch = None
    errors = 0
    err_logged = -1e9
    junk = 0
    perf = time.perf_counter
    while True:
        try:
            data = os.read(in_fd, CHILD_READ)
        except OSError as e:
            _log(f"stdin: {e}: exiting")
            return 0
        if not data:
            return 0                                    # EOF: show_service closed its end (stop), or died
        if rem:
            data = rem + data
        offs, used, bad = split_hops(data)
        if bad:
            junk += bad
            _log(f"{bad} junk bytes skipped ({junk} so far)")
        for o in offs:
            _m, _v, fl, _n, ep, seq, t_raw, t_put = HOP_HDR.unpack_from(data, o)
            rf = 0
            if fl & HOP_RESET or ep != cur_epoch:
                cur_epoch = ep
                try:
                    act.reset()
                except Exception as e:                  # noqa: BLE001
                    _log(f"reset: {type(e).__name__}: {e}")
                rf = REC_RESET_DONE
            c0 = perf()
            try:
                a = float(act.process_hop(np.frombuffer(data, "<f4", HOP, o + HOP_HDR.size)))
            except Exception as e:                      # noqa: BLE001  counted and reported, never fatal
                a = math.nan
                rf |= REC_ERROR
                errors += 1
                if time.monotonic() - err_logged > 10.0:
                    err_logged = time.monotonic()
                    _log(f"activation error ({errors} so far): {type(e).__name__}: {e}")
            us = (perf() - c0) * 1e6
            try:
                os.write(out_fd, pack_rec(ep, seq, a, us, t_raw, t_put, rf))
            except OSError:                             # EPIPE: show_service has gone
                return 0
        rem = data[used:] if used < len(data) else b""


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog=f"python -m {MODULE}", description="show_service's beat activation process")
    ap.add_argument("--parent", type=int, default=0, help="show_service's pid: exit when it isn't our parent")
    ap.add_argument("--activation", default="madmom", choices=("madmom", "fake"))
    ap.add_argument("--nets", type=int, default=2)
    ap.add_argument("--nice", type=int, default=0)
    ap.add_argument("--fake-delay-ms", type=float, default=0.0, help=argparse.SUPPRESS)    # tests only
    a = ap.parse_args(argv)

    nice = _set_nice(a.nice)                            # 1. first
    proto_fd = os.dup(1)                                # 2. the protocol keeps the real stdout ...
    os.dup2(2, 1)                                       # ... and fd 1 becomes stderr
    sys.stdout = sys.stderr
    _pdeathsig()                                        # 3. die with show_service's spawning thread
    if a.parent and os.getppid() != a.parent:
        _log(f"parent {a.parent} is gone (ppid {os.getppid()}): exiting")
        return 3
    t0 = time.perf_counter()
    act = make_child_activation(a.activation, a.nets, a.fake_delay_ms)    # 4. madmom: about 1.1-1.5 s
    ms = (time.perf_counter() - t0) * 1000.0
    nets = float(getattr(act, "nets", 0))
    _log(f"{a.activation} ({nets:g} nets) ready in {ms:.0f} ms, nice {nice}")
    try:
        os.write(proto_fd, pack_rec(0, os.getpid(), nets, int(ms), 0.0, 0.0, REC_HELLO))   # 5. hello
    except OSError:
        return 0
    code = serve(act, 0, proto_fd)                      # 6. until EOF
    try:
        sys.stderr.flush()
    except Exception:                                   # noqa: BLE001
        pass
    os._exit(code)                                      # no teardown of madmom's objects: exit at once


# -- the parent --------------------------------------------------------------------------------------------------------
def _proc_cpu_s(pid: int) -> Optional[float]:
    """utime + stime of ``pid`` (every thread), in seconds; None when it can't be read."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            raw = f.read()
        fields = raw[raw.rindex(b")") + 2:].split()
        return (int(fields[11]) + int(fields[12])) / float(_CLK_TCK)
    except (OSError, ValueError, IndexError):
        return None


def _close(fd: Optional[int]) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


class BeatProc:
    """The beat process's parent end (see the module docstring). Built on any thread (it spawns from its own); the
    ``send_*``/``poll``/``request_kill`` methods and the ``out`` deque are show-rx's alone. Class attributes are the
    defaults (tests may patch them); the keyword arguments override them per instance."""
    respawns = True                      # a lost child comes back (clock._BeatThread: False)
    backoff_s: Optional[Sequence[float]] = None     # None: ml.supervisor DEFAULT_RESTART's
    healthy_s: Optional[float] = None
    hello_s: float = HELLO_S
    extra_args: Sequence[str] = ()
    python: Optional[str] = None         # None: sys.executable

    def __init__(self, activation: str = "madmom", nets: int = 2, *, nice: int = 0,
                 log_path: "Optional[os.PathLike | str]" = None, python: Optional[str] = None,
                 parent_pid: Optional[int] = None, backoff_s: Optional[Sequence[float]] = None,
                 healthy_s: Optional[float] = None, hello_s: Optional[float] = None,
                 extra_args: Optional[Sequence[str]] = None, cwd: "os.PathLike | str" = REPO_ROOT) -> None:
        from .ml import supervisor as S                 # stdlib + numpy + contract: its restart and env constants
        self.activation, self.nets, self.nice = str(activation), int(nets), int(nice)
        self.log_path = pathlib.Path(log_path) if log_path is not None else None
        self.python = python or type(self).python or sys.executable
        self.parent_pid = parent_pid or os.getpid()
        steps = backoff_s if backoff_s is not None else (self.backoff_s if self.backoff_s is not None
                                                         else S.DEFAULT_RESTART["backoff_s"])
        self._steps = [max(MIN_BACKOFF_S, float(s)) for s in steps] or [300.0]
        self._healthy = float(healthy_s if healthy_s is not None else (self.healthy_s if self.healthy_s is not None
                                                                       else S.DEFAULT_RESTART["healthy_s"]))
        self._hello_s = float(hello_s if hello_s is not None else self.hello_s)
        self._extra = tuple(extra_args if extra_args is not None else self.extra_args)
        self._cwd = str(cwd)
        self._env = dict(os.environ, PYTHONUNBUFFERED="1", CUDA_VISIBLE_DEVICES="", MALLOC_ARENA_MAX=S.MALLOC_ARENAS)
        self._env.update({k: "1" for k in S.THREAD_ENV})
        # show-rx's (the Clock's transport face; written by show-rx only)
        self.out: collections.deque = collections.deque()
        self.cost: collections.deque = collections.deque(maxlen=2000)     # ms per process_hop, the child's
        self.done = 0                                   # records read (every generation)
        self.errors = 0                                 # activation errors the child reported
        self.reset_done = 0                             # records that say the activation was reset first
        self.junk = 0                                   # bytes skipped resyncing (never expected)
        self.sent = self.recv = 0                       # hops sent / records read, this generation
        self.last_progress = time.perf_counter()        # the last record read, or the send that began a run in flight
        self._gen = 0                                   # the generation show-rx uses: its ack to the supervisor
        self._wfd: Optional[int] = None
        self._rfd: Optional[int] = None
        self._dead = -1                                 # a generation show-rx found dead (EOF, EPIPE) or had killed
        self._kill = -1                                 # a generation show-rx wants killed (a stall)
        self._reset_next = False
        self._rem = b""
        self._buf = bytearray(HOP_BYTES)
        self._pcm = np.frombuffer(self._buf, "<f4", HOP, HOP_HDR.size)
        # the supervisor's (read anywhere, written by show-beat-sup only)
        self._link: tuple = (0, None, None, None)      # (generation, write fd, read fd, pid): ONE store
        self._pub = 0                                   # the last generation published
        self._mine: list[tuple[int, int, int]] = []     # (generation, wfd, rfd) not yet closed
        self.state = "starting"
        self.why: Optional[str] = None
        self.pid: Optional[int] = None                  # the live child's pid (from the spawn, before its hello)
        self.hello: Optional[dict] = None
        self.attempts = self.restarts = self.failures_in_row = self.sup_errors = self.log_errors = 0
        self.last_exit: Optional[dict] = None
        self.cpu: Optional[float] = None                # the child's share of a core over the last CPU_EVERY_S
        self.next_start_at: Optional[float] = None
        self._proc: Optional[subprocess.Popen] = None
        self._closed = False
        self._close_lock = threading.Lock()             # close() from two threads: each fd is closed once
        self._stop = threading.Event()
        self._sup = threading.Thread(target=self._supervise, name="show-beat-sup", daemon=True)
        self._sup.start()

    # -- show-rx: the transport ----------------------------------------------------------------------------------------
    @property
    def ready(self) -> bool:
        """Hops may go to the child: a generation with fds is adopted (its HELLO came) and it is not dead."""
        return self._wfd is not None and self._dead != self._gen and not self._closed

    @property
    def alive(self) -> bool:
        """The supervisor runs (so a child is, or will be again): False after ``close()``."""
        return not self._closed and self._sup.is_alive()

    @property
    def inflight(self) -> int:
        return self.sent - self.recv

    @property
    def gen(self) -> int:
        """The generation show-rx has adopted: a new one means the child it was talking to has gone."""
        return self._gen

    def poll(self) -> None:
        """show-rx, never blocking: adopt a newer generation; read whatever records have come, only while hops are in
        flight (else no syscall at all). Each record appends ``("reset", epoch)`` first if the child reset there,
        then ``("a", epoch, seq, a | None, t_raw, t_put)``."""
        link = self._link
        if link[0] != self._gen:
            self._adopt(link)
        if self.sent == self.recv or self._rfd is None or self._dead == self._gen or self._closed:
            return
        try:
            data = os.read(self._rfd, READ_BYTES)
        except BlockingIOError:
            return
        except OSError:
            self._dead = self._gen
            return
        if not data:
            self._dead = self._gen                      # EOF: the child has gone
            return
        self._take(data)

    def _adopt(self, link: tuple) -> None:
        gen, wfd, rfd, _pid = link
        self._wfd, self._rfd = wfd, rfd
        self.sent = self.recv = 0
        self._rem = b""
        self._reset_next = False
        self.last_progress = time.perf_counter()
        self._gen = gen                                 # last: the ack (older generations' fds may close now)

    def _take(self, data: bytes) -> None:
        if self._rem:
            data = self._rem + data
        offs, used, junk = split_recs(data)
        self.junk += junk
        out, cost = self.out, self.cost
        for o in offs:
            _m, _v, fl, _p, ep, seq, a, us, t_raw, t_put = REC.unpack_from(data, o)
            if fl & REC_HELLO:
                continue
            if fl & REC_RESET_DONE:
                out.append(("reset", ep))
                self.reset_done += 1
            if fl & REC_ERROR:
                self.errors += 1
                out.append(("a", ep, seq, None, t_raw, t_put))
            else:
                out.append(("a", ep, seq, a, t_raw, t_put))
            cost.append(us / 1000.0)
            self.done += 1
            self.recv += 1
        if offs:
            self.last_progress = time.perf_counter()
        self._rem = data[used:] if used < len(data) else b""

    def send_hop(self, epoch: int, seq: int, hop: np.ndarray, t_raw: float, t_put: float) -> int:
        """show-rx, never blocking: 0 sent, 1 the pipe is full (dropped: the caller splices), 2 no child (dead, not
        ready, closed)."""
        w = self._wfd
        if w is None or self._dead == self._gen or self._closed:
            return 2
        HOP_HDR.pack_into(self._buf, 0, HOP_MAGIC, VERSION, HOP_RESET if self._reset_next else 0, HOP,
                          epoch & U32, seq & U32, t_raw, t_put)
        self._pcm[:] = hop
        try:
            n = os.write(w, self._buf)
        except BlockingIOError:
            return 1
        except OSError:                                 # EPIPE: the child has gone
            self._dead = self._gen
            return 2
        if n != HOP_BYTES:                              # never (atomic under PIPE_BUF): the stream would be torn
            self._dead = self._gen
            self.request_kill()
            return 2
        if self.sent == self.recv:
            self.last_progress = time.perf_counter()
        self.sent += 1
        self._reset_next = False
        return 0

    def send_reset(self, epoch: int) -> None:
        """The activation resets before the next hop that is sent (the flag rides it; ``epoch`` is that hop's)."""
        self._reset_next = True

    def request_kill(self) -> None:
        """show-rx: this generation is wedged (a stall). It is dead from here; the supervisor kills it and respawns."""
        self._dead = self._gen
        self._kill = self._gen

    def wait(self, dt: float) -> None:
        """Tests and offline runs only (blocks up to ``dt``): read, then wait for the next record."""
        self.poll()
        r = self._rfd
        if self.sent > self.recv and r is not None and self._dead != self._gen:
            try:
                select.select([r], [], [], dt)
            except (OSError, ValueError):
                time.sleep(dt)
        else:
            time.sleep(dt)

    def wait_ready(self, timeout: float = 30.0) -> bool:
        """Tests and offline runs only: until a child has said hello (and show-rx's side has adopted it)."""
        end = time.monotonic() + timeout
        while True:
            self.poll()
            if self.ready:
                return True
            if not self.alive or time.monotonic() > end:
                return False
            time.sleep(0.005)

    def snapshot(self) -> dict:
        now = time.monotonic()
        return {"pid": self.pid, "state": self.state, "why": self.why, "restarts": self.restarts,
                "failures_in_row": self.failures_in_row, "last_exit": self.last_exit, "cpu": self.cpu,
                "attempts": self.attempts, "hello": self.hello, "junk": self.junk, "sup_errors": self.sup_errors,
                "log_errors": self.log_errors,
                "next_start_in_s": (round(max(0.0, self.next_start_at - now), 1)
                                    if self.next_start_at is not None else None)}

    def stop(self, timeout: float = STOP_WAIT_S) -> None:
        self.close(timeout)

    def close(self, timeout: float = STOP_WAIT_S) -> None:
        """Stop the supervisor and the child (EOF, ``timeout``, SIGTERM the group, ``timeout``, SIGKILL; reaped) and
        close every fd. Any thread, idempotent; call it once show-rx no longer sends (Show.stop's order): from here
        ``send_hop`` returns 2 and ``poll`` reads nothing."""
        self._closed = True
        self._stop.set()
        if self._sup is not threading.current_thread():
            self._sup.join(2.0 * max(0.0, float(timeout)) + 3.0)
        if self._sup.is_alive():                        # never expected: kill what it made, close what it left
            p = self._proc
            if p is not None:
                self._signal(p, signal.SIGKILL)
            log.warning("beat process: its supervisor did not stop in time")
            return
        self._close_all()

    # -- show-beat-sup -------------------------------------------------------------------------------------------------
    def _supervise(self) -> None:
        while not self._stop.is_set():
            try:
                ran = self._one_run()
                if self._stop.is_set():
                    break
                if ran is not None and ran >= self._healthy:
                    self.failures_in_row = 0
                self.failures_in_row += 1
                self.restarts += 1
                self._backoff("failed" if ran is None else "backoff")
            except Exception as e:                      # noqa: BLE001  never give up: this run ends, then the backoff
                self.sup_errors += 1
                self.why = f"supervisor error: {type(e).__name__}: {e}"[:200]
                log.exception("beat process supervisor: %s", self.why)
                try:
                    if self._proc is not None:
                        self._end(self._proc, "kill", self.why)
                    self._dead_link()
                    self.failures_in_row += 1
                    self._backoff("failed")
                except Exception:                       # noqa: BLE001
                    log.exception("beat process supervisor: recovering")
                    self._stop.wait(1.0)
        try:
            if self._proc is not None:
                self._end(self._proc, "stop", "stopped")
        except Exception:                               # noqa: BLE001
            log.exception("beat process supervisor: stop")
        self.state, self.why = "stopped", None
        self.next_start_at = None
        if self._closed:
            self._close_all()

    def _one_run(self) -> Optional[float]:
        """Spawn, wait for HELLO, publish, watch until it exits (or is killed, or we stop), reap. Returns how long it
        ran (None: it never started)."""
        self._rotate_log()
        r_in, w_in = os.pipe()
        try:
            r_out, w_out = os.pipe()
        except OSError:
            _close(r_in)
            _close(w_in)
            raise
        os.set_blocking(w_in, False)
        os.set_blocking(r_out, False)
        try:
            fcntl.fcntl(w_in, fcntl.F_SETPIPE_SZ, PIPE_BYTES)
        except (OSError, AttributeError):               # a smaller pipe-max-size: the default 64 KB (36 hops) stays
            pass
        cmd = [self.python, "-m", MODULE, "--parent", str(self.parent_pid), "--activation", self.activation,
               "--nets", str(self.nets), "--nice", str(self.nice), *self._extra]
        self.attempts += 1
        self.state, self.why = "starting", None
        logf = None
        if self.log_path is not None:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                logf = open(self.log_path, "ab")
                logf.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} beat-worker start (attempt {self.attempts}, "
                           f"{self.failures_in_row} failures in a row) ---\n".encode())
                logf.flush()
            except OSError as e:                        # degrade: the child's stderr is ours, and it still runs
                self.log_errors += 1
                log.warning("beat process log %s: %s (its stderr goes to ours)", self.log_path, e)
                if logf is not None:
                    logf.close()
                logf = None
        try:
            proc = subprocess.Popen(cmd, stdin=r_in, stdout=w_out, stderr=logf, cwd=self._cwd, env=self._env,
                                    start_new_session=True, close_fds=True)
        except OSError as e:
            for fd in (r_in, w_in, r_out, w_out):
                _close(fd)
            self.last_exit = {"code": None, "why": f"spawn: {e}"[:200], "at": round(time.monotonic(), 3),
                              "pid": None}
            self.state, self.why = "failed", f"spawn: {e}"[:200]
            return None
        finally:
            if logf is not None:
                logf.close()
        _close(r_in)
        _close(w_out)
        self._proc, self.pid = proc, proc.pid
        started = time.monotonic()
        how, why = self._wait_hello(proc, r_out)
        if how is not None:                             # no hello: show-rx never saw these fds, so they close now
            _close(w_in)                                # (EOF first: a child past its start-up exits by itself)
            self._end(proc, how, why)
            _close(r_out)
            return time.monotonic() - started
        self._pub += 1
        gen = self._pub
        self._mine.append((gen, w_in, r_out))
        self._link = (gen, w_in, r_out, proc.pid)      # one store: show-rx adopts it at its next poll
        self.state, self.why = "running", None
        how, why = self._watch(proc, gen)
        self._end(proc, how, why, w_in if how == "stop" else None)
        self._dead_link()
        return time.monotonic() - started

    def _wait_hello(self, proc: subprocess.Popen, r_out: int) -> tuple[Optional[str], Optional[str]]:
        """(None, None) once HELLO is read here, else (how to end it, why)."""
        end = time.monotonic() + self._hello_s
        acc = b""
        while True:
            if self._stop.is_set():
                return "stop", "stopped"
            if proc.poll() is not None:
                return "reap", "exited before hello"
            left = end - time.monotonic()
            if left <= 0:
                return "kill", f"no hello in {self._hello_s:g} s"
            try:
                ready, _, _ = select.select([r_out], [], [], min(WATCH_S, left))
            except (OSError, ValueError):
                ready = []
                time.sleep(min(WATCH_S, left))
            if not ready:
                continue
            try:
                data = os.read(r_out, REC.size - len(acc))
            except BlockingIOError:
                continue
            if not data:
                return "reap", "exited before hello"
            acc += data
            if len(acc) < REC.size:
                continue
            m, v, fl, _p, _ep, seq, a, us, _t, _u = REC.unpack(acc)
            if m != REC_MAGIC or v != VERSION or not fl & REC_HELLO:
                self.junk += len(acc)
                return "kill", "bad hello"
            self.hello = {"pid": seq, "nets": int(a), "startup_ms": us}
            return None, None

    def _watch(self, proc: subprocess.Popen, gen: int) -> tuple[str, str]:
        cpu_t, cpu = time.monotonic(), _proc_cpu_s(proc.pid)
        while True:
            code = proc.poll()
            if code is not None:
                return "reap", f"exited ({code})"
            if self._stop.is_set():
                return "stop", "stopped"
            if self._kill == gen:
                return "kill", "stalled (no record for clock.BEAT_STALL_S with hops in flight)"
            now = time.monotonic()
            if now - cpu_t >= CPU_EVERY_S:
                c = _proc_cpu_s(proc.pid)
                if c is not None and cpu is not None and now > cpu_t:
                    self.cpu = round(max(0.0, (c - cpu) / (now - cpu_t)), 3)
                cpu_t, cpu = now, c
            self._close_acked()
            self._stop.wait(WATCH_S)

    def _end(self, proc: subprocess.Popen, how: str, why: Optional[str], wfd: Optional[int] = None) -> None:
        """Make sure ``proc`` is gone and reaped. ``stop``: EOF first (/dev/null over our write end ``wfd``: the pipe
        closes, the fd number stays ours), then STOP_WAIT_S, SIGTERM, STOP_WAIT_S, SIGKILL. ``kill``: SIGKILL now.
        ``reap``: it has exited."""
        code = proc.poll()
        if code is None and how == "stop":
            if wfd is not None:
                try:
                    dn = os.open(os.devnull, os.O_WRONLY)
                    os.dup2(dn, wfd)
                    os.close(dn)
                except OSError:
                    pass
            code = self._wait(proc, STOP_WAIT_S)
            if code is None:
                self._signal(proc, signal.SIGTERM)
                code = self._wait(proc, STOP_WAIT_S)
        if code is None and how != "reap":
            self._signal(proc, signal.SIGKILL)
        if code is None:
            code = self._wait(proc, 5.0)
        if code is None:                                # never expected (SIGKILL'd): a last try, then let it go
            self._signal(proc, signal.SIGKILL)
            code = self._wait(proc, 5.0)
        tail = self.log_tail(1) if code not in (0, None) and how == "reap" else []
        self.last_exit = {"code": code, "why": (f"{why}: {tail[-1][:200]}" if tail else why),
                          "at": round(time.monotonic(), 3), "pid": proc.pid}
        self._proc, self.pid = None, None

    @staticmethod
    def _wait(proc: subprocess.Popen, timeout: float) -> Optional[int]:
        try:
            return proc.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    @staticmethod
    def _signal(proc: subprocess.Popen, sig: int) -> None:
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, sig)                    # its own session: the group is its pid
        except (ProcessLookupError, PermissionError):
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                pass

    def _dead_link(self) -> None:
        """The child is gone: a generation without fds (show-rx adopts it, which lets the old fds close)."""
        if self._link[1] is not None:
            self._pub += 1
            self._link = (self._pub, None, None, None)

    def _close_acked(self) -> None:
        if not self._mine:
            return
        g_rx = self._gen
        keep = []
        for g, w, r in self._mine:
            if g < g_rx:
                _close(w)
                _close(r)
            else:
                keep.append((g, w, r))
        self._mine = keep

    def _close_all(self) -> None:
        with self._close_lock:
            self._dead_link()
            mine, self._mine = self._mine, []
            for _g, w, r in mine:
                _close(w)
                _close(r)

    def _backoff(self, state: str) -> None:
        delay = self._steps[max(0, min(self.failures_in_row, len(self._steps)) - 1)]
        self.next_start_at = time.monotonic() + delay
        self.state = state
        if state == "backoff":
            self.why = f"restarting in {delay:g} s"
        while not self._stop.is_set():
            left = self.next_start_at - time.monotonic()
            if left <= 0:
                break
            self._close_acked()
            self._stop.wait(min(WATCH_S, left))
        self.next_start_at = None

    def _rotate_log(self) -> None:
        p = self.log_path
        if p is None:
            return
        try:
            if p.stat().st_size > LOG_MAX:
                os.replace(p, p.with_name(p.name + ".1"))
        except OSError:
            pass

    def log_tail(self, n: int = 5) -> list[str]:
        if self.log_path is None:
            return []
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 4096))
                return [ln.decode("utf-8", "replace") for ln in f.read().splitlines()[-n:]]
        except OSError:
            return []


if __name__ == "__main__":
    sys.exit(main())
