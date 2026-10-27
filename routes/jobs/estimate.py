"""How long a job's phases are expected to take, learned from the job history (docs/JOBS.md).

Only the predictable phases are learned:
  prepare_s  = submitted_at - created_at      (prompt refining, workflow loading, uploading)
  generate_s = publishing_at - generating_at  (sampling on a warm GPU: steady per workflow)
  publish_s  = finished_at - publishing_at
"Getting ready" (submitted_at -> generating_at: GPU queue, cold boot, model load) varies wildly and is
deliberately never learned, so a cold start can't skew the estimate. A job that never got a ComfyUI
progress frame (no generating_at) is not a sample.

Each phase is the median over the last 30 completed jobs of the same workflow ("workflow", needs at
least 3 samples), else of any workflow ("all"), else fixed defaults ("default"). Clients turn it into a
smoothly moving progress ring (src/shared/jobs/estimate.ts). Results are cached per workflow for 60 s,
so a job update never costs a history scan."""
import statistics
import threading
import time

PHASES = (  # name, start timestamp, end timestamp
    ("prepare_s", "created_at", "submitted_at"),
    ("generate_s", "generating_at", "publishing_at"),
    ("publish_s", "publishing_at", "finished_at"),
)
# Every timestamp a job needs to be a sample (the store query filters on these too)
SAMPLE_STAMPS = ("created_at", "submitted_at", "generating_at", "publishing_at", "finished_at")
DEFAULTS = {"prepare_s": 4.0, "generate_s": 30.0, "publish_s": 3.0}
WINDOW = 30            # most recent completed jobs looked at
MIN_SAMPLES = 3        # samples needed before a basis is trusted
MAX_PHASE_S = 30 * 60  # a phase longer than this (or negative) is an outlier and ignored
CACHE_S = 60.0


def phase_durations(job):
    """{phase: seconds} for a job that can teach the estimate, else None. It needs every timestamp in
    SAMPLE_STAMPS and a sane generate_s; a prepare or publish phase that is negative or over
    MAX_PHASE_S comes back as None (ignored, not the whole job)."""
    if any(not isinstance(job.get(k), (int, float)) for k in SAMPLE_STAMPS):
        return None
    out = {}
    for name, start, end in PHASES:
        d = job[end] - job[start]
        out[name] = d if 0 <= d <= MAX_PHASE_S else None
    return out if out["generate_s"] is not None else None


def summarise(jobs, basis):
    """The estimate dict from completed jobs (newest first), or None with fewer than MIN_SAMPLES."""
    samples = [d for d in (phase_durations(j) for j in jobs) if d is not None][:WINDOW]
    if len(samples) < MIN_SAMPLES:
        return None
    est = {}
    for name, _, _ in PHASES:
        values = [s[name] for s in samples if s[name] is not None]
        est[name] = round(statistics.median(values), 1) if values else DEFAULTS[name]
    est.update(samples=len(samples), basis=basis)
    return est


def default_estimate():
    return dict(DEFAULTS, samples=0, basis="default")


class Estimator:
    """Per-workflow phase estimates from a JobStore, cached for cache_s seconds."""

    def __init__(self, store, cache_s=CACHE_S, clock=time.monotonic):
        self.store = store
        self.cache_s = cache_s
        self.clock = clock
        self._lock = threading.Lock()
        self._cache = {}      # workflow (None = any) -> (expires, estimate)
        self.scans = 0        # history queries made (tests)

    def estimate(self, workflow=None):
        """A fresh copy of the estimate for this workflow (None = workflow not known yet). Never
        raises: a store error gives the defaults."""
        key = workflow or None
        now = self.clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return dict(hit[1])
        try:
            est = self._compute(key)
        except Exception:
            est = default_estimate()
        with self._lock:
            self._cache[key] = (now + self.cache_s, est)
        return dict(est)

    def _compute(self, workflow):
        if workflow:
            self.scans += 1
            est = summarise(self.store.recent_completed(SAMPLE_STAMPS, workflow=workflow,
                                                        limit=WINDOW), "workflow")
            if est:
                return est
            return self.estimate(None)
        self.scans += 1
        return (summarise(self.store.recent_completed(SAMPLE_STAMPS, limit=WINDOW), "all")
                or default_estimate())

    def clear(self):
        with self._lock:
            self._cache.clear()
