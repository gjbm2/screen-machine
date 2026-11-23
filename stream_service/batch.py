"""Batch GPU jobs: work that wants a GPU but is not a live session (the first: easing a screen clip onto its still).

Owner, 23 Nov: the renderer is the one place that decides GPU use ("we don't want multiple sessions competing for a
resource that becomes scarce during live mode").  So such work is asked for here and runs when, and only where, the
renderer says:

  * only on the GPUs the deployment lists (``--batch-gpus`` in its services/sm-stream.env).  A GPU that also drives a
    desktop is left off the list.  No list: there is no GPU batch work here, and a caller does it its own way;
  * one job at a time per GPU, first come first served, each in its own process (batch_run.py), never in a render
    process;
  * live wins: a job never starts on a GPU that has a session, and a session that takes the GPU mid-job has the
    job's process killed at once and the job queued again from its start (jobs are seconds long, so nothing is
    paused or moved).  A job must therefore be restartable: it writes its result as a temp file and renames it.

  POST   /api/batch {"kind", "args"}  -> 202 the job          (503 no_batch_gpu: nothing listed here)
  GET    /api/batch                   -> {"gpus", "jobs"}
  GET    /api/batch/<id>              -> the job              state: queued | running | done | failed | cancelled
  DELETE /api/batch/<id>              -> the job, cancelled

``kind`` is a module of stream_service/batch_jobs with ``run(args, progress)``; ``args`` go to it untouched.  A
finished job is kept KEEP_S; a restart of the service forgets them all.  No auth, as the rest of the control API.
"""
from __future__ import annotations

import asyncio
import collections
import importlib.util
import itertools
import json
import logging
import os
import signal
import sys
import time
from typing import Callable, Optional, Sequence

log = logging.getLogger("stream_service.batch")

TICK_S = 0.2            # how soon a job gives its GPU up to a session, and a freed GPU is used
KEEP_S = 600.0          # a finished job stays readable this long
ERR_TAIL = 2000         # of a failed job's stderr
QUEUED, RUNNING, DONE, FAILED, CANCELLED = "queued", "running", "done", "failed", "cancelled"


def known_kind(kind: object) -> bool:
    if not isinstance(kind, str) or not kind.isidentifier() or kind.startswith("_"):
        return False
    try:
        return importlib.util.find_spec(f"stream_service.batch_jobs.{kind}") is not None
    except (ImportError, ValueError):
        return False


class Job:
    def __init__(self, jid: str, kind: str, args: dict):
        self.id, self.kind, self.args = jid, kind, args
        self.state = QUEUED
        self.progress = 0.0
        self.gpu: Optional[int] = None
        self.waiting: Optional[str] = None      # why it is not running yet, in plain words
        self.restarts = 0                       # times a session took its GPU
        self.error: Optional[str] = None
        self.created = time.time()
        self.started: Optional[float] = None
        self.finished: Optional[float] = None
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.stopping: Optional[str] = None     # why its process is being killed: "live" | "cancel"
        self.err = collections.deque(maxlen=40)

    def describe(self) -> dict:
        return {"id": self.id, "kind": self.kind, "state": self.state, "progress": round(self.progress, 4),
                "gpu": self.gpu, "waiting": self.waiting, "restarts": self.restarts, "error": self.error,
                "created": self.created, "started": self.started, "finished": self.finished}


class Batch:
    """``gpus``: where jobs may run.  ``busy(gpu)``: why that GPU is not free for a job (a live session), or None."""

    def __init__(self, gpus: Sequence[int], busy: Callable[[int], Optional[str]], *, python: str = sys.executable,
                 keep_s: float = KEEP_S, tick_s: float = TICK_S):
        self.gpus = list(dict.fromkeys(int(g) for g in gpus))
        self.busy, self.python, self.keep_s, self.tick_s = busy, python, keep_s, tick_s
        self.jobs: dict[str, Job] = {}
        self._ids = itertools.count(1)
        self._tasks: set = set()

    # ---------------------------------------------------------------- the API's side

    def submit(self, kind: str, args: dict) -> Job:
        job = Job(f"b{next(self._ids)}-{os.urandom(3).hex()}", kind, args)
        self.jobs[job.id] = job
        log.info("batch %s (%s) queued", job.id, kind)
        return job

    def cancel(self, jid: str) -> Optional[Job]:
        job = self.jobs.get(jid)
        if job is None:
            return None
        if job.state == QUEUED:
            self._finish(job, CANCELLED)
        elif job.state == RUNNING:
            self._kill(job, "cancel")
        return job

    def describe(self) -> dict:
        return {"gpus": self.gpus, "jobs": [j.describe() for j in self.jobs.values()]}

    # ---------------------------------------------------------------- the loop

    async def run(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("batch tick")
            await asyncio.sleep(self.tick_s)

    def tick(self) -> None:
        now = time.time()
        for jid in [j.id for j in self.jobs.values() if j.finished and now - j.finished > self.keep_s]:
            del self.jobs[jid]
        why = {g: self._busy(g) for g in self.gpus}
        running = {j.gpu: j for j in self.jobs.values() if j.state == RUNNING}
        for gpu, job in running.items():
            if why.get(gpu) and not job.stopping:       # live wins
                log.info("batch %s gives GPU %d up: %s", job.id, gpu, why[gpu])
                self._kill(job, "live")
        queued = [j for j in self.jobs.values() if j.state == QUEUED]
        free = [g for g in self.gpus if g not in running and not why[g]]
        for job, gpu in zip(queued, free):
            self._start(job, gpu)
        held = "; ".join(dict.fromkeys(w for w in why.values() if w)) or None
        for job in queued[len(free):]:
            job.waiting = held if not free and held else "another job is running"

    def _busy(self, gpu: int) -> Optional[str]:
        try:
            return self.busy(gpu)
        except Exception as e:                      # can't tell: don't start anything on it
            return f"GPU {gpu} unknown ({e})"

    def _start(self, job: Job, gpu: int) -> None:
        job.state, job.gpu, job.waiting, job.progress, job.stopping = RUNNING, gpu, None, 0.0, None
        job.started = time.time()
        job.err.clear()
        task = asyncio.ensure_future(self._run(job, gpu))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job: Job, gpu: int) -> None:
        env = dict(os.environ, CUDA_DEVICE_ORDER="PCI_BUS_ID", CUDA_VISIBLE_DEVICES=str(gpu), PYTHONUNBUFFERED="1")
        try:
            # its own process group, so a kill takes anything it started; batch_run dies with this process
            proc = job.proc = await asyncio.create_subprocess_exec(
                self.python, "-m", "stream_service.batch_run", job.kind, json.dumps(job.args), str(os.getpid()),
                env=env, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, start_new_session=True)
        except Exception as e:
            self._finish(job, FAILED, f"could not start: {e}")
            return
        if job.stopping:                            # asked to stop while it was being started
            self._signal(proc)
        log.info("batch %s (%s) running on GPU %d, pid %d", job.id, job.kind, gpu, proc.pid)

        async def out() -> None:
            async for line in proc.stdout:
                word, _, value = line.decode("utf-8", "replace").strip().partition(" ")
                if word == "progress":
                    try:
                        job.progress = min(1.0, max(job.progress, float(value)))
                    except ValueError:
                        pass

        async def err() -> None:
            async for line in proc.stderr:
                job.err.append(line.decode("utf-8", "replace").rstrip())

        await asyncio.gather(out(), err(), return_exceptions=True)
        code = await proc.wait()
        job.proc = None
        if job.stopping == "live":                  # a session took the GPU: again, from the start, when it is free
            job.state, job.gpu, job.progress, job.stopping, job.started = QUEUED, None, 0.0, None, None
            job.restarts += 1
        elif job.stopping == "cancel":
            self._finish(job, CANCELLED)
        elif code == 0:
            job.progress = 1.0
            self._finish(job, DONE)
        else:
            self._finish(job, FAILED, ("\n".join(job.err) or f"exit {code}")[-ERR_TAIL:])

    def _kill(self, job: Job, why: str) -> None:
        job.stopping = why
        if job.proc is not None:
            self._signal(job.proc)

    @staticmethod
    def _signal(proc: asyncio.subprocess.Process) -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def _finish(self, job: Job, state: str, error: Optional[str] = None) -> None:
        job.state, job.error, job.finished, job.waiting = state, error, time.time(), None
        took = f" in {job.finished - job.started:.1f} s" if job.started else ""
        (log.warning if state == FAILED else log.info)("batch %s (%s) %s%s%s", job.id, job.kind, state, took,
                                                       f": {error[-300:]}" if error else "")

    async def close(self) -> None:
        for job in self.jobs.values():
            if job.state == RUNNING:
                self._kill(job, "cancel")
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=5.0)
