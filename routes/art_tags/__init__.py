"""Art tags (docs/ART_TAGS.md): every picture saved to a bucket gets a few tags from an AI pass (config/art_tags.yaml),
kept in its sidecar; a low-priority queue does it so tagging never holds up generation or display (owner, 27 Oct)."""
import threading

_worker = None
_worker_lock = threading.Lock()


def get_worker():
    """The process-wide queue, made on first use. Its thread starts only in startup() (Flask's __main__), so a test or
    a tool that imports the bucket code never tags anything."""
    global _worker
    if _worker is None:
        with _worker_lock:
            if _worker is None:
                from routes.art_tags.worker import TagWorker
                _worker = TagWorker()
    return _worker


def set_worker(worker):
    """Swap the process-wide worker (tests)."""
    global _worker
    _worker = worker


def enqueue(bucket, filename, front=False, retag=False):
    """Queue one bucket file for tagging (not when the deployment has art_tags off). Never blocks, never raises."""
    try:
        import smconfig
        if not smconfig.enabled("art_tags"):
            return False
        return get_worker().enqueue(bucket, filename, front=front, retag=retag)
    except Exception as e:                            # noqa: BLE001
        try:
            from utils.logger import warning
            warning(f"[art_tags] enqueue {bucket}/{filename} failed: {e}")
        except Exception:                             # noqa: BLE001
            pass
        return False


def startup():
    """At Flask start: the worker thread (first sweep 60 s later, the historic backfill included), and the art
    director's thread, which has new pictures made for a screen whose library is thin for the music in Ambient
    (owner, 28 Oct; routes/art_tags/director.py). Each starts only when the deployment has it on: the tagging queue
    needs "art_tags", the art director "art_director" and "generation". Never raises."""
    from utils.logger import info, error
    try:
        import smconfig
        tags_on = smconfig.enabled("art_tags")
        director_on = smconfig.enabled("art_director") and smconfig.enabled("generation")
    except Exception as e:                            # noqa: BLE001
        error(f"[art_tags] startup: no deployment ({e}); neither the tagging queue nor the art director started")
        return
    if not tags_on:
        info("[art_tags] tagging queue not started: art_tags is off in this deployment")
    else:
        try:
            get_worker().start()
            info("[art_tags] tagging queue started")
        except Exception as e:                        # noqa: BLE001
            error(f"[art_tags] startup failed: {e}")
    if not director_on:
        info("[art_tags] art director not started: art_director (or generation) is off in this deployment")
        return
    try:
        from routes.art_tags.director import get_director
        get_director().start()
        info("[art_tags] art director started")
    except Exception as e:                            # noqa: BLE001
        error(f"[art_tags] art director startup failed: {e}")
