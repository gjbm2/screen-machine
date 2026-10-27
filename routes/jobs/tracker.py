"""The generation job tracker (docs/JOBS.md): the one record of every job's state, with pub/sub for
live pages and SQLite for history. Thread-safe; generator threads, the overlay WS loop and Flask
request threads all call it."""
import threading
import time
import uuid
from collections import deque

from utils.logger import info, warning, error

from routes.jobs import runpod_api
from routes.jobs.progress import ComfyProgress
from routes.jobs.store import JobStore

ACTIVE = ("preparing", "queued", "running", "publishing")
TERMINAL = ("completed", "failed", "cancelled")
SOURCES = ("generator", "scheduler", "alexa", "api")
RESTART_ERROR = "Server restarted while this job was running"

FIELDS = {
    "id": None, "batch_id": None, "batch_index": None, "placeholder_id": None, "source": "api",
    "kind": "image", "target": None, "target_name": None, "prompt": "", "refined_prompt": None,
    "workflow": None, "refiner": None, "status": "preparing", "message": None, "stages": None,
    "stage_index": None, "percent": None, "error": None, "runpod_job_id": None, "endpoint_id": None,
    "created_at": None, "submitted_at": None, "started_at": None, "finished_at": None,
    "updated_at": None, "duration_s": None, "cost_gbp": None, "seed": None, "result_url": None,
    "published_path": None, "cancel_requested": False,
}
# Fields a caller may set through update(); the rest are kept by the tracker itself
_SETTABLE = set(FIELDS) - {"id", "created_at", "updated_at", "finished_at", "duration_s",
                           "cancel_requested"}

SNAPSHOT_FINISHED_S = 60     # finished jobs a (re)connecting stream still gets
MEMORY_FINISHED_S = 600      # finished jobs kept in memory; older ones are read from the store


def new_job_id():
    return f"job_{uuid.uuid4().hex[:12]}"


def is_active(job):
    return job.get("status") in ACTIVE


class Subscription:
    """A bounded queue of job events for one listener. The publisher never blocks: when the
    listener falls behind, the oldest events are dropped (the next snapshot repairs it)."""

    def __init__(self, tracker, target=None, maxlen=256):
        self.target = target
        self.dropped = 0
        self._tracker = tracker
        self._queue = deque(maxlen=maxlen)
        self._cond = threading.Condition()
        self.closed = False

    def _offer(self, job):
        if self.target and job.get("target") != self.target:
            return
        with self._cond:
            if len(self._queue) == self._queue.maxlen:
                self.dropped += 1
            self._queue.append(job)
            self._cond.notify()

    def get(self, timeout=None):
        """The next job event, or None after timeout (or once closed)."""
        with self._cond:
            if not self._queue and not self.closed:
                self._cond.wait(timeout)
            return self._queue.popleft() if self._queue else None

    def close(self):
        self._tracker._unsubscribe(self)
        with self._cond:
            self.closed = True
            self._cond.notify_all()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class JobTracker:
    def __init__(self, store=None, progress_interval=0.25, persist_interval=5.0,
                 cancel_remote=None):
        self.store = store if store is not None else JobStore(":memory:")
        self.progress_interval = progress_interval
        self.persist_interval = persist_interval
        # RunPod cancel (endpoint_id, runpod_job_id) -> bool; swapped out by tests
        self.cancel_remote = cancel_remote or (lambda ep, rid: runpod_api.cancel(ep, rid, timeout=10))
        self._lock = threading.RLock()
        self._jobs = {}
        self._progress = {}
        self._by_runpod = {}
        self._subs = set()
        self._last_publish = {}
        self._last_persist = {}
        self._timers = {}

    # ---------------------------------------------------------------- create / read

    def create(self, prompt="", source="api", **fields):
        now = time.time()
        job = dict(FIELDS)
        job.update({k: v for k, v in fields.items() if k in _SETTABLE})
        job.update(id=new_job_id(), prompt=prompt or "", created_at=now, updated_at=now,
                   source=source if source in SOURCES else "api")
        job["stages"] = list(job["stages"] or [])
        with self._lock:
            self._prune_memory(now)
            self._jobs[job["id"]] = job
            self._commit(job, persist=True)
            return dict(job)

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                return dict(job)
        return self.store.get(job_id)

    def list(self, state="all", limit=50, before=None, target=None, source=None, q=None):
        """Newest first, from the store with in-memory (fresher) copies laid over it."""
        limit = max(1, min(int(limit or 50), 500))
        rows = self.store.query(state, limit, before, target, source, q)
        with self._lock:
            jobs = [dict(self._jobs[r["id"]]) if r["id"] in self._jobs else r for r in rows]
        if state == "active":
            jobs = [j for j in jobs if is_active(j)]
        elif state == "finished":
            jobs = [j for j in jobs if not is_active(j)]
        jobs = jobs[:limit]
        next_before = jobs[-1]["created_at"] if len(jobs) == limit else None
        return jobs, next_before

    def snapshot(self, target=None, now=None):
        """Active jobs plus those finished in the last minute (what a (re)connecting stream gets)."""
        now = now or time.time()
        with self._lock:
            jobs = [dict(j) for j in self._jobs.values()
                    if (not target or j.get("target") == target)
                    and (is_active(j) or (j.get("finished_at") or 0) >= now - SNAPSHOT_FINISHED_S)]
        return sorted(jobs, key=lambda j: j["created_at"], reverse=True)

    def active_jobs(self):
        with self._lock:
            return [dict(j) for j in self._jobs.values() if is_active(j)]

    def is_cancelled(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return bool(job and (job["cancel_requested"] or job["status"] == "cancelled"))

    # ---------------------------------------------------------------- changes

    def update(self, job_id, **fields):
        """Change a job and publish it at once. A finished job never changes again (the first
        terminal state wins), so a late update from a generator thread can't revive it."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job["status"] in TERMINAL:
                return dict(job)
            changes = {k: v for k, v in fields.items() if k in _SETTABLE and job.get(k) != v}
            if not changes:
                return dict(job)
            status = changes.get("status")
            if status and status not in ACTIVE + TERMINAL:
                raise ValueError(f"Unknown job status {status!r}")
            now = time.time()
            job.update(changes)
            if status == "queued" and not job["submitted_at"]:
                job["submitted_at"] = now
            if status == "running" and not job["started_at"]:
                job["started_at"] = now
            if status in TERMINAL:
                job["finished_at"] = now
                job["duration_s"] = round(now - job["created_at"], 2)
                if status == "completed":
                    job["percent"] = 100
                self._end(job_id)
            if changes.get("runpod_job_id"):
                self._by_runpod[changes["runpod_job_id"]] = job_id
            job["updated_at"] = now
            self._commit(job, persist=True)
            return dict(job)

    def progress(self, job_id, **fields):
        """Step progress (percent / stage_index / message). Published at most ~4 times a second per
        job; the latest value always goes out (a trailing publish catches up)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job["status"] in TERMINAL:
                return
            changes = {k: v for k, v in fields.items()
                       if k in ("percent", "stage_index", "message") and job.get(k) != v}
            if not changes:
                return
            job.update(changes)
            job["updated_at"] = time.time()
            self._publish_throttled(job_id)

    def set_stages(self, job_id, processing_stages):
        """The workflow's processing stages (workflows.json), for weighting comfy progress."""
        with self._lock:
            tracker = ComfyProgress(processing_stages)
            self._progress[job_id] = tracker
        return self.update(job_id, stages=tracker.stage_names)

    def on_worker_frame(self, runpod_job_id, comfy):
        """A frame from the RunPod worker's ComfyUI relay ({job_id, comfy}). Called on the overlay
        WS loop, so it only folds the frame in and returns. True when it belonged to a job."""
        with self._lock:
            job_id = self._by_runpod.get(runpod_job_id)
            job = self._jobs.get(job_id) if job_id else None
            if not job or job["status"] in TERMINAL:
                return bool(job)
            if job["status"] == "queued":
                self.update(job_id, status="running", message="Running")
            tracker = self._progress.get(job_id)
            if tracker is None:
                tracker = self._progress[job_id] = ComfyProgress()
            changes = tracker.feed(comfy)
            if changes:
                self.progress(job_id, **changes)
            return True

    def complete(self, job_id, **fields):
        return self.update(job_id, status="completed", message=None, **fields)

    def fail(self, job_id, error_text, **fields):
        return self.update(job_id, status="failed", error=str(error_text or "Failed"),
                           message=None, **fields)

    def cancel(self, job_id):
        """Cancel one job: it ends as cancelled at once, its generator thread stops at its next check,
        and its RunPod job (if submitted) is cancelled in the background. Returns the job."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return self.get(job_id)
            if job["status"] in TERMINAL:
                return dict(job)
            job["cancel_requested"] = True
            remote = (job["endpoint_id"], job["runpod_job_id"])
            done = self.update(job_id, status="cancelled", message=None)
        if all(remote):
            self._cancel_remote_async([remote])
        return done

    def cancel_all(self):
        """Cancel every active job; returns how many."""
        ids = [j["id"] for j in self.active_jobs()]
        for job_id in ids:
            self.cancel(job_id)
        return len(ids)

    # ---------------------------------------------------------------- pub/sub

    def subscribe(self, target=None, maxlen=256):
        sub = Subscription(self, target, maxlen)
        with self._lock:
            self._subs.add(sub)
        return sub

    def _unsubscribe(self, sub):
        with self._lock:
            self._subs.discard(sub)

    # ---------------------------------------------------------------- startup

    def reconcile_on_startup(self, cancel_in_background=True):
        """Jobs the store still has as active were orphaned by a restart: mark them failed and cancel
        their RunPod jobs (in the background; never blocks start-up). Returns how many."""
        orphans = self.store.active()
        now = time.time()
        remote = []
        for job in orphans:
            job.update(status="failed", error=RESTART_ERROR, message=None, finished_at=now,
                       updated_at=now, duration_s=round(now - job["created_at"], 2))
            self._persist(job)
            if job.get("endpoint_id") and job.get("runpod_job_id"):
                remote.append((job["endpoint_id"], job["runpod_job_id"]))
        if orphans:
            warning(f"[jobs] {len(orphans)} job(s) were left running by a restart; marked failed")
        if remote:
            if cancel_in_background:
                self._cancel_remote_async(remote)
            else:
                self._cancel_remote(remote)
        return len(orphans)

    # ---------------------------------------------------------------- internals

    def _commit(self, job, persist):
        """Publish (and maybe persist) the job's current state. Called under the lock, so subscribers
        and the store see changes in order."""
        timer = self._timers.pop(job["id"], None)
        if timer:
            timer.cancel()
        self._last_publish[job["id"]] = time.monotonic()
        snapshot = dict(job)
        snapshot["stages"] = list(job["stages"] or [])
        for sub in list(self._subs):
            sub._offer(snapshot)
        if persist:
            self._last_persist[job["id"]] = time.monotonic()
            self._persist(snapshot)

    def _publish_throttled(self, job_id):
        now = time.monotonic()
        wait = self.progress_interval - (now - self._last_publish.get(job_id, 0))
        if wait <= 0:
            persist = now - self._last_persist.get(job_id, 0) >= self.persist_interval
            self._commit(self._jobs[job_id], persist=persist)
        elif job_id not in self._timers:
            timer = threading.Timer(wait, self._flush, args=(job_id,))
            timer.daemon = True
            self._timers[job_id] = timer
            timer.start()

    def _flush(self, job_id):
        with self._lock:
            if self._timers.pop(job_id, None) is None:
                return
            job = self._jobs.get(job_id)
            if job and job["status"] not in TERMINAL:
                persist = time.monotonic() - self._last_persist.get(job_id, 0) >= self.persist_interval
                self._commit(job, persist=persist)

    def _persist(self, job):
        try:
            self.store.upsert(job)
        except Exception as e:
            error(f"[jobs] could not save job {job.get('id')}: {e}")

    def _end(self, job_id):
        self._progress.pop(job_id, None)
        self._last_persist.pop(job_id, None)

    def _prune_memory(self, now):
        cutoff = now - MEMORY_FINISHED_S
        for job_id, job in list(self._jobs.items()):
            if job["status"] in TERMINAL and (job["finished_at"] or 0) < cutoff:
                del self._jobs[job_id]
                self._last_publish.pop(job_id, None)
                if job.get("runpod_job_id"):
                    self._by_runpod.pop(job["runpod_job_id"], None)

    def _cancel_remote(self, pairs):
        for endpoint_id, runpod_job_id in pairs:
            ok = False
            try:
                ok = self.cancel_remote(endpoint_id, runpod_job_id)
            except Exception as e:
                warning(f"[jobs] RunPod cancel of {runpod_job_id} raised: {e}")
            info(f"[jobs] RunPod cancel {runpod_job_id} on {endpoint_id}: {'ok' if ok else 'failed'}")

    def _cancel_remote_async(self, pairs):
        threading.Thread(target=self._cancel_remote, args=(list(pairs),), daemon=True,
                         name="jobs-runpod-cancel").start()
