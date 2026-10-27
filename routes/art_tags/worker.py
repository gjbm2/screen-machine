"""Art tags: the background queue (docs/ART_TAGS.md). One low-priority daemon thread tags one file at a time; tagging
never holds up generation or display (owner, 27 Oct 2026). New saves go to the front; the sweep (60 s after start,
then every sweep_every_s) queues every bucket file that has no result yet, which is also the historic backfill.

Failures: a timeout, 429, 5xx or dropped connection backs off 30 s .. 30 min with the item kept; out of credit (or a
config problem such as no key) pauses an hour with one alert; a refusal, an unreadable picture or an unusable answer
costs an attempt, and after max_attempts the file is marked failed and left alone. Over the day's budget
(llm.yaml budgets.tag) waits for local midnight."""
from __future__ import annotations

import collections
import datetime as _dt
import json
import os
import re
import threading
import time
from typing import Any, Callable, Optional

from routes.art_tags import lexicon, sidecar

FIRST_SWEEP_S = 60.0
BACKOFF_MIN_S, BACKOFF_MAX_S = 30.0, 1800.0
PAUSE_S = 3600.0
COVERAGE_TTL_S = 60.0
PRIORITY_BUCKETS = ("north-screen", "south-screen", "devtest")
_STAMP_RX = re.compile(r"^\d{8}-\d{6}")


def _log(level: str, msg: str) -> None:
    try:
        from utils import logger
        getattr(logger, level)(f"[art_tags] {msg}")
    except Exception:                                 # noqa: BLE001
        pass


def _next_midnight(now: float) -> float:
    d = _dt.datetime.fromtimestamp(now).date() + _dt.timedelta(days=1)
    return _dt.datetime.combine(d, _dt.time()).timestamp()


def _iso(t: Optional[float]) -> Optional[str]:
    if not t:
        return None
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seq_files(meta: dict) -> list[str]:
    out = []
    for e in meta.get("sequence") or []:
        f = e.get("file") if isinstance(e, dict) else e
        if isinstance(f, str) and f and f not in out:
            out.append(f)
    return out


def _skip_buckets() -> set:
    try:
        return set(lexicon.settings().get("skip_buckets") or [])
    except Exception:                                 # noqa: BLE001
        return {"_recent"}


def scan(skip: Optional[set] = None) -> list[dict]:
    """Every tagging candidate, in sweep order: north-screen, south-screen, devtest, then the other buckets
    alphabetically; within a bucket favourites first, then newest first. One dict per media file named in a bucket's
    sequence that exists: {bucket, file, fav, status}."""
    base = sidecar.output_base()
    skip = _skip_buckets() if skip is None else skip
    try:
        names = [p.name for p in base.iterdir() if p.is_dir() and (p / "bucket.json").is_file()]
    except OSError:
        return []
    names = [n for n in names if n not in skip and not n.startswith(".")]
    names.sort(key=lambda n: (PRIORITY_BUCKETS.index(n) if n in PRIORITY_BUCKETS else len(PRIORITY_BUCKETS), n))
    out: list[dict] = []
    for b in names:
        try:
            meta = json.loads((base / b / "bucket.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        favs = set(meta.get("favorites") or [])
        rows = []
        for f in _seq_files(meta):
            p = base / b / f
            if os.path.splitext(f)[1].lower() not in sidecar.MEDIA_EXT or "/" in f or not p.is_file():
                continue
            m = _STAMP_RX.match(f)
            if m:
                stamp = m.group(0)
            else:                                     # tmpXXXX.jpg and other old names: their file time
                try:
                    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(p.stat().st_mtime))
                except OSError:
                    stamp = ""
            try:
                st = sidecar.status_of(sidecar.read(p))
            except (OSError, ValueError):
                st = None
            rows.append({"bucket": b, "file": f, "fav": f in favs, "status": st, "_stamp": stamp})
        rows.sort(key=lambda r: r["_stamp"], reverse=True)
        rows.sort(key=lambda r: not r["fav"])         # stable: favourites first, each part newest first
        for r in rows:
            r.pop("_stamp")
        out += rows
    return out


class TagWorker:
    def __init__(self, tag_fn: Optional[Callable[..., dict]] = None, clock: Callable[[], float] = time.time,
                 alert_fn: Optional[Callable[..., Any]] = None, budget_fn: Optional[Callable[[str], bool]] = None):
        if tag_fn is None:
            from routes.art_tags.tagger import tag_file as tag_fn
        self.tag_fn = tag_fn
        self.clock = clock
        self.alert_fn = alert_fn
        self.budget_fn = budget_fn
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self._stop = threading.Event()
        self.q: collections.deque = collections.deque()
        self.queued: set = set()
        self.retag: set = set()
        self.attempts: dict = {}
        self.current: Optional[dict] = None
        self.done_session = 0
        self.failed_session = 0
        self.backoff_s = 0.0
        self.not_before = 0.0
        self.paused_until = 0.0
        self.pause_reason: Optional[str] = None
        self.last_error: Optional[str] = None
        self.thread: Optional[threading.Thread] = None
        self._next_sweep = 0.0
        self._coverage: tuple = (0.0, None)

    # -- the queue ------------------------------------------------------------------------------------------------
    def enqueue(self, bucket: str, filename: str, front: bool = False, retag: bool = False) -> bool:
        """Queue a file (new to the queue: True). Never blocks on I/O, never raises."""
        try:
            if not bucket or not filename or bucket in _skip_buckets():
                return False
            key = (bucket, filename)
            with self.lock:
                if retag:
                    self.retag.add(key)
                    self.attempts.pop(key, None)
                if key in self.queued:
                    if front and self.q and self.q[0] != key:
                        self.q.remove(key)
                        self.q.appendleft(key)
                    return False
                self.queued.add(key)
                (self.q.appendleft if front else self.q.append)(key)
            self.wake.set()
            return True
        except Exception as e:                        # noqa: BLE001
            _log("warning", f"enqueue {bucket}/{filename} failed: {e}")
            return False

    def sweep(self) -> int:
        """Queue every bucket file without a result (done or failed). -> how many were new to the queue."""
        n = 0
        for r in scan():
            if r["status"] not in ("done", "failed") and self.enqueue(r["bucket"], r["file"]):
                n += 1
        if n:
            _log("info", f"sweep queued {n}")
        return n

    def _pop(self) -> Optional[tuple]:
        with self.lock:
            if not self.q:
                return None
            key = self.q.popleft()
            self.queued.discard(key)
            return key

    def _requeue_front(self, key: tuple) -> None:
        with self.lock:
            if key not in self.queued:
                self.queued.add(key)
                self.q.appendleft(key)

    def _pause(self, reason: str, until: float, kind: str, detail: str = "") -> None:
        first = self.paused_until <= self.clock()
        self.paused_until, self.pause_reason = until, reason
        _log("warning", f"paused until {_iso(until)}: {reason}")
        if first and kind in ("quota", "config"):     # one alert per pause
            try:
                alert = self.alert_fn
                if alert is None:
                    from utils.alerts import alert
                alert("art_tags.quota" if kind == "quota" else "art_tags.error",
                      f"Art tagging paused for an hour: {reason}", severity="critical" if kind == "quota" else "error",
                      context={"detail": detail[:500]})
            except Exception:                         # noqa: BLE001
                pass

    def _over_budget(self, task: str) -> bool:
        try:
            if self.budget_fn is not None:
                return bool(self.budget_fn(task))
            import utils.llm as llm
            return llm.over_budget(task)
        except Exception:                             # noqa: BLE001
            return False

    # -- one step -------------------------------------------------------------------------------------------------
    def step(self) -> str:
        """Tag at most one file. -> paused | waiting | budget | idle | skipped | done | retry | failed | attempt."""
        now = self.clock()
        if self.paused_until > now:
            return "paused"
        if self.paused_until:
            self.paused_until, self.pause_reason = 0.0, None
        if self.not_before > now:
            return "waiting"
        cfg = lexicon.settings()
        task = cfg.get("task") or "tag"
        if not self.q:
            return "idle"
        if self._over_budget(task):
            self._pause("the day's tagging budget is spent (llm.yaml budgets.tag)", _next_midnight(now), "budget")
            return "budget"
        key = self._pop()
        if key is None:
            return "idle"
        bucket, filename = key
        path = sidecar.media_path(bucket, filename)
        if bucket in set(cfg.get("skip_buckets") or []) or not path.is_file():
            self.retag.discard(key)
            self.attempts.pop(key, None)
            return "skipped"
        if key not in self.retag:
            try:
                st = sidecar.status_of(sidecar.read(path))
            except (OSError, ValueError):
                st = None
            if st in ("done", "failed"):
                return "skipped"
        self.current = {"bucket": bucket, "filename": filename, "since": _iso(now)}
        try:
            res = self.tag_fn(bucket, filename)
        except Exception as e:                        # noqa: BLE001 - tag_fn never raises; a fake one might
            res = {"ok": False, "error": f"{type(e).__name__}: {e}", "kind": "invalid"}
        finally:
            self.current = None
        if res.get("ok"):
            try:
                sidecar.update(path, lambda d: sidecar.apply_ai(d, res["ai"], res.get("model"), res.get("cost_usd"),
                                                                lexicon.load()["version"]))
            except (OSError, ValueError) as e:        # the file went, or its sidecar isn't JSON: leave it alone
                _log("warning", f"{bucket}/{filename}: result not written ({e})")
                self.retag.discard(key)
                return "skipped"
            self.retag.discard(key)
            self.attempts.pop(key, None)
            self.backoff_s = 0.0
            self.done_session += 1
            self.last_error = None
            return "done"
        err, kind = str(res.get("error") or "failed"), res.get("kind") or "invalid"
        self.last_error = f"{bucket}/{filename}: {err}"[:300]
        if kind == "gone":
            self.retag.discard(key)
            return "skipped"
        if kind == "transient":
            self.backoff_s = min(BACKOFF_MAX_S, max(BACKOFF_MIN_S, self.backoff_s * 2))
            self.not_before = now + self.backoff_s
            self._requeue_front(key)
            _log("warning", f"{bucket}/{filename}: {err}; next try in {self.backoff_s:.0f} s")
            return "retry"
        if kind in ("quota", "config"):
            self._requeue_front(key)
            self._pause(("out of credit" if kind == "quota" else "the tag model can't be called") + f" ({err[:120]})",
                        now + PAUSE_S, kind, err)
            return "paused"
        n = self.attempts.get(key, 0) + 1
        max_attempts = int(cfg.get("max_attempts") or 3)
        if n < max_attempts:
            self.attempts[key] = n
            with self.lock:                           # behind the rest: a bad file mustn't block the queue
                if key not in self.queued:
                    self.queued.add(key)
                    self.q.append(key)
            _log("warning", f"{bucket}/{filename}: attempt {n} failed: {err}")
            return "attempt"
        self.attempts.pop(key, None)
        self.retag.discard(key)
        try:
            sidecar.update(path, lambda d: sidecar.apply_failure(d, err, n))
        except (OSError, ValueError) as e:
            _log("warning", f"{bucket}/{filename}: failure not written ({e})")
        self.failed_session += 1
        _log("warning", f"{bucket}/{filename}: failed after {n} attempts: {err}")
        return "failed"

    # -- the thread -----------------------------------------------------------------------------------------------
    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self._next_sweep = self.clock() + FIRST_SWEEP_S
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="art-tags")
        self.thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Let the thread finish its current file and end (tests; Flask never stops it)."""
        self._stop.set()
        self.wake.set()
        if self.thread is not None:
            self.thread.join(timeout)

    def _run(self) -> None:
        try:                                          # this thread only: the host serves live 4K streams
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
        except Exception as e:                        # noqa: BLE001
            _log("warning", f"could not lower the thread's priority: {e}")
        _log("info", "worker started")
        while not self._stop.is_set():
            try:
                now = self.clock()
                if now >= self._next_sweep:
                    try:
                        every = float(lexicon.settings().get("sweep_every_s") or 900)
                    except Exception:                 # noqa: BLE001
                        every = 900.0
                    self._next_sweep = now + every
                    self.sweep()
                r = self.step()
                if r in ("done", "attempt", "failed"):
                    self._stop.wait(float(lexicon.settings().get("min_interval_s") or 1.0))
                    continue
                if r == "skipped":
                    continue
                wait = min(60.0, max(0.5, self._next_sweep - self.clock()))
                if r == "paused":
                    wait = min(wait, max(0.5, self.paused_until - self.clock()))
                elif r == "waiting" or r == "retry":
                    wait = min(wait, max(0.5, self.not_before - self.clock()))
                self.wake.wait(wait)
                self.wake.clear()
            except Exception as e:                    # noqa: BLE001 - the loop never dies
                _log("error", f"worker loop: {type(e).__name__}: {e}")
                self._stop.wait(30)

    # -- status ---------------------------------------------------------------------------------------------------
    def coverage(self) -> dict:
        at, cov = self._coverage
        if cov is not None and self.clock() - at < COVERAGE_TTL_S:
            return cov
        cov = {}
        for r in scan():
            c = cov.setdefault(r["bucket"], {"total": 0, "tagged": 0, "failed": 0})
            c["total"] += 1
            c["tagged"] += r["status"] == "done"
            c["failed"] += r["status"] == "failed"
        self._coverage = (self.clock(), cov)
        return cov

    def snapshot(self) -> dict:
        """Contract §2's status."""
        spend, model = None, None
        try:
            import utils.llm as llm
            task = lexicon.settings().get("task") or "tag"
            spend = round(float((llm.status().get("spend_today_usd") or {}).get(task) or 0.0), 4)
            model = ((llm.task_settings(task).get("models") or [{}])[0]).get("model")
        except Exception:                             # noqa: BLE001
            pass
        now = self.clock()
        with self.lock:
            queued = len(self.q)
        return {"running": bool(self.thread and self.thread.is_alive()), "queued": queued, "current": self.current,
                "done_session": self.done_session, "failed_session": self.failed_session,
                "paused_until": _iso(self.paused_until) if self.paused_until > now else None,
                "pause_reason": self.pause_reason if self.paused_until > now else None,
                "waiting_until": _iso(self.not_before) if self.not_before > now else None,
                "last_error": self.last_error, "spend_today_usd": spend, "model": model,
                "coverage": self.coverage()}
