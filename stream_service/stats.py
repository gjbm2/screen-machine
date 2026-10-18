"""Small statistics helpers."""
from __future__ import annotations

import collections
import ctypes
import math
import os
import sys
import time
from typing import Callable, Iterable, Optional

SUMMARY_CACHE_S = 1.0     # Ring.summary() is recomputed at most this often (W1.1: big rings, one sort)


def pct(values: Iterable[float], p: float) -> Optional[float]:
    return pct_sorted(sorted(values), p)


def pct_sorted(s: list, p: float) -> Optional[float]:
    """pct() of an already sorted list."""
    if not s:
        return None
    k = (len(s) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


class Ring:
    """The last ``n`` samples, for percentiles.  summary() sorts once and is cached for ``cache_s``."""

    def __init__(self, n: int, cache_s: float = SUMMARY_CACHE_S, clock: Callable[[], float] = time.monotonic):
        self.values: collections.deque[float] = collections.deque(maxlen=n)
        self.cache_s = cache_s
        self.clock = clock
        self._cached: Optional[dict] = None
        self._cached_at = 0.0

    def add(self, v: float) -> None:
        self.values.append(v)

    def summary(self) -> dict:
        now = self.clock()
        c = self._cached
        if c is not None and now - self._cached_at < self.cache_s:
            return dict(c)
        s = sorted(self.values)
        r = lambda v: None if v is None else round(v, 3)  # noqa: E731
        c = {"p50": r(pct_sorted(s, 50)), "p95": r(pct_sorted(s, 95)), "max": r(s[-1] if s else None), "n": len(s)}
        self._cached, self._cached_at = c, now
        return dict(c)


# -- per-thread measurement (W0.1, W0.2) ---------------------------------------------
PR_SET_NAME = 15
_libc = None


def set_thread_name(name: str) -> None:
    """Name the calling thread for top -H / ps -L (Linux prctl, 15 bytes).  Never raises."""
    global _libc
    if not sys.platform.startswith("linux"):
        return
    try:
        if _libc is None:
            _libc = ctypes.CDLL(None, use_errno=True)
        _libc.prctl(PR_SET_NAME, ctypes.c_char_p(name.encode(errors="replace")[:15]), 0, 0, 0)
    except Exception:
        pass


class ThreadMeter:
    """The calling thread's CPU time (time.thread_time) and run-queue wait (the 2nd field of a kept-open
    /proc/thread-self/schedstat, ns).  Make it on the thread it measures.  runq is None where unavailable."""

    def __init__(self, path: str = "/proc/thread-self/schedstat"):
        try:
            self.fd: Optional[int] = os.open(path, os.O_RDONLY)
        except OSError:
            self.fd = None

    def runq_ns(self) -> Optional[int]:
        fd = self.fd
        if fd is None:
            return None
        try:
            return int(os.pread(fd, 64, 0).split()[1])
        except (OSError, ValueError, IndexError):
            return None

    def sample(self) -> tuple:
        """(thread CPU seconds, run-queue wait ns or None)."""
        return time.thread_time(), self.runq_ns()

    def close(self) -> None:
        fd, self.fd = self.fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def split_ms(host_ms: float, s0: tuple, s1: tuple) -> tuple:
    """(cpu_ms, runq_ms or None, blocked_ms) of a host span from two ThreadMeter samples.  blocked is what is
    left (GIL, locks, syscalls, page faults), >= 0; without runq it includes the run-queue wait."""
    cpu = (s1[0] - s0[0]) * 1000.0
    runq = None if s0[1] is None or s1[1] is None else (s1[1] - s0[1]) / 1e6
    return cpu, runq, max(0.0, host_ms - cpu - (runq or 0.0))
