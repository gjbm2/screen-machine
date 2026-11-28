"""karaoke.pace: the karaoke tools give way to what plays live (owner, 27 Nov: "when we're doing the song extraction
alongside rendering... throttle this so rendering doesn't splutter"; "if we're critically pending a song prep in
order to keep the queue going, we may want to upweight that task. otherwise, real time stuff should probably win";
"'timing carefully' prep jobs should definitely be downweighted -- those are 'when we have time later'").

WSL shares the GPU between processes by time-slicing, with no priorities, so removing a vocal (torch, ~30 s flat out)
takes the GPU time the renderer's frames and the browser playing the song need. Each tool (all torch) imports
tools/karaoke/gpu_pace.py, which after every ~0.1 s of its work waits for the GPU to finish it and then sleeps, so
that it has only ``share`` of the time. The worker sets the share while the tool runs, through a small file
(SM_PACE_FILE), from the policy below; re-read every 0.5 s by the tool.

  nothing live (no stream session live, no song playing)  1.0  flat out
  the song is needed soon (its turn within URGENT_S)      1.0  it keeps the queue going
  a song being prepared while something plays live        PREP_SHARE
  the careful pass while something plays live             CAREFUL_SHARE  (it is "when we have time")

Measured 27 Nov (the 4K vj stream live, the vocal taken out of a 3:30 song): flat out it took 66 s and the renderer's
frames were late in a median 52 % of each second (worst 98 %, 73 repeated frames); paced at 0.4 it took 130 s and a
median 0 % were late (worst 7 %, 6 repeats). Stopping the process from outside instead (SIGSTOP bursts) stalled the
whole GPU while the tool loaded its model, so the tools pace themselves.
The tools also run at nice NICE (CAREFUL_NICE for the careful pass), so their CPU threads come after the renderer's.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.request
from typing import Any, Callable

import smconfig

PREP_SHARE = 0.4
CAREFUL_SHARE = 0.1
URGENT_S = 240.0          # a song whose turn comes within this is prepared flat out (paced, a song takes ~2-3 min)
NICE = 10
CAREFUL_NICE = 19
LIVE_CACHE_S = 3.0

_live = (0.0, False)
_live_lock = threading.Lock()


def screens_live() -> bool:
    """Any stream session live now (the renderer's GET /api/diag, through room_service); read at most every 3 s."""
    global _live
    with _live_lock:
        at, v = _live
        if time.time() - at < LIVE_CACHE_S:
            return v
    try:
        with urllib.request.urlopen(smconfig.url("room") + "/renderer/api/diag", timeout=1.5) as r:
            d = json.loads(r.read())
        v = any(s.get("state") == "live" for s in d.get("sessions") or [])
    except Exception:
        v = False
    with _live_lock:
        _live = (time.time(), v)
    return v


def flat_out() -> float:
    return 1.0


#: The share for the tool the worker runs next (set by the worker per job: Worker.prep_share / careful_share).
current: Callable[[], float] = flat_out
current_nice: int = NICE


class Paced:
    """A share file for one tool run: env() for its Popen, and a thread writing ``share()`` into it each second
    while ``p`` runs."""

    def __init__(self, share: Callable[[], float]) -> None:
        self.share = share
        d = smconfig.state("karaoke", "pace")
        d.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=d, suffix=".share")
        os.close(fd)
        self.path = name
        self.shares: list[float] = []
        self._write()

    def _write(self) -> None:
        try:
            v = max(0.05, min(1.0, float(self.share())))
        except Exception:
            v = 1.0
        self.shares.append(v)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            f.write(f"{v:.2f}")
        os.replace(tmp, self.path)

    def env(self, base: dict | None = None) -> dict:
        return {**(base or os.environ), "SM_PACE_FILE": self.path}

    def follow(self, p: subprocess.Popen) -> "Paced":
        def run() -> None:
            try:
                while p.poll() is None:
                    time.sleep(1.0)
                    self._write()
            finally:
                try:
                    os.unlink(self.path)
                except OSError:
                    pass
        threading.Thread(target=run, name=f"karaoke-pace-{p.pid}", daemon=True).start()
        return self


def popen(cmd: list[str], **kw: Any) -> subprocess.Popen:
    """Popen for a karaoke tool: niced, with the current share (kw: stdout, stderr, text, env, cwd)."""
    pc = Paced(current)
    p = subprocess.Popen(["nice", "-n", str(current_nice), *cmd], env=pc.env(kw.pop("env", None)), **kw)
    pc.follow(p)
    return p


def run(cmd: list[str], *, timeout: float, **kw: Any) -> subprocess.CompletedProcess:
    """subprocess.run(cmd, capture_output=True, timeout=...) for a karaoke tool, niced and paced."""
    p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        raise
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)
