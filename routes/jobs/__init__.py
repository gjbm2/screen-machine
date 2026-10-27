"""Generation job tracker (docs/JOBS.md): tracker + store + RunPod progress + HTTP/SSE API."""
import threading

_tracker = None
_tracker_lock = threading.Lock()


def get_tracker():
    """The process-wide tracker, made on first use with the SQLite store (JOBS_DB or
    ~/.local/state/screen-machine/jobs.db)."""
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                from routes.jobs.store import JobStore
                from routes.jobs.tracker import JobTracker
                _tracker = JobTracker(store=JobStore())
    return _tracker


def set_tracker(tracker):
    """Swap the process-wide tracker (tests)."""
    global _tracker
    _tracker = tracker


def startup():
    """At Flask start: prune history past 90 days, fail jobs a restart orphaned (cancelling their RunPod
    jobs in the background). Never raises."""
    from utils.logger import info, error
    try:
        tracker = get_tracker()
        pruned = tracker.store.prune()
        orphaned = tracker.reconcile_on_startup()
        info(f"[jobs] store {tracker.store.path}: pruned {pruned}, orphaned {orphaned}")
    except Exception as e:
        error(f"[jobs] startup failed: {e}")
