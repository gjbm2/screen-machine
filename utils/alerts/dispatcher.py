"""The alert bus: bounded queue, one self-healing daemon worker, dedup /
throttle / storm-brake policy, per-send retry, and the health-gated
healthchecks.io heartbeat ping.

Delivery bookkeeping distinguishes three outcomes ("delivered" | "queued" |
"suppressed") so that nothing is ever silently lost:
- delivered: counted against the storm cap; per-key last_sent stamped
- queued: channel failed; the rendered email waits in a paced retry queue
  (5-min spacing, 5 attempts); last_attempt stamped so cooldowns hold but
  reminders still own redelivery of undelivered criticals
- suppressed: storm brake; per-key state untouched, so the next occurrence
  (or reminder) redelivers with the accumulated count once the window frees

Fail-safe invariants (see docs/ALERTING_PROPOSAL.md §3):
- callers only ever do a non-blocking queue put
- every worker duty is individually wrapped; a poisoned state file or a
  raising channel cannot kill the worker loop
- a dead worker is restarted by the next submit(); a disabled bus (env
  ALERTING_ENABLED=0) drops records and performs no duties
- worker-owned structures (_send_times, _emails_24h, retry/warning queues,
  state) are only ever mutated on the worker thread; stats() reads snapshots
"""

import json
import logging
import os
import queue
import socket
import threading
import time
from collections import deque

import config

from utils.alerts import health as health_mod
from utils.alerts import state as state_mod

_log = logging.getLogger("screen_machine.alerts")

_queue = queue.Queue(maxsize=getattr(config, "ALERT_QUEUE_MAX", 1000))
_worker = None
_worker_lock = threading.Lock()
_state = None
_state_lock = threading.Lock()
_channels = None

_pending_retries = deque(maxlen=20)   # {"subject","body","attempts","next_attempt_at"}
_pending_warnings = {}                # dedup_key -> {"summary","count","last"}
_last_warning_flush = time.time()     # first rollup waits a full batch window
_send_times = deque()                 # DELIVERED-email timestamps (storm window)
_emails_24h = deque()
_storm_active = False
_channel_failures = 0                 # consecutive
_last_email_success = None
_last_ping = 0.0
_last_sentinel = 0.0
_dropped_lock = threading.Lock()
_dropped = 0

RETRY_SPACING_S = 300
RETRY_MAX_ATTEMPTS = 5


def enabled():
    env = os.getenv("ALERTING_ENABLED")
    if env is not None:
        return env not in ("0", "false", "False", "")
    return bool(getattr(config, "ALERTING_ENABLED", True))


def submit(record):
    """Called by alert(). Non-blocking; never raises past its own guard."""
    if not enabled():
        return
    _ensure_worker_alive()
    try:
        _queue.put_nowait(record)
    except queue.Full:
        global _dropped
        with _dropped_lock:
            _dropped += 1


def start():
    if enabled():
        _ensure_worker_alive()


def stats():
    """Read-only snapshot — safe from request threads; never mutates
    worker-owned structures."""
    with _dropped_lock:
        dropped = _dropped
    ch = _get_channels()
    gmail = ch[0] if ch else None
    if gmail is None or not gmail.configured():
        channel_status = "unconfigured"
    elif _channel_failures >= 3:
        channel_status = "failing"
    else:
        channel_status = "ok"
    now = time.time()
    emails_24h = sum(1 for t in tuple(_emails_24h) if now - t <= 86400)
    return {
        "queued": _queue.qsize(),
        "dropped": dropped,
        "emails_24h": emails_24h,
        "pending_retries": len(_pending_retries),
        "channel_consecutive_failures": _channel_failures,
        "email_channel": channel_status,
        "last_email_success": _last_email_success,
        "worker_alive": bool(_worker and _worker.is_alive()),
        "storm_active": _storm_active,
    }


def previous_instance_age_s():
    """Age of the previous instance's last state-file heartbeat, or None."""
    try:
        st = _get_state()
        if st.last_alive:
            return max(0.0, time.time() - float(st.last_alive))
    except Exception:
        pass
    return None


# ── internals ───────────────────────────────────────────────────────────

def _get_state():
    global _state
    with _state_lock:
        if _state is None:
            _state = state_mod.AlertState.load(
                getattr(config, "ALERT_STATE_PATH", "logs/alerts_state.json"))
        return _state


def _get_channels():
    global _channels
    if _channels is None:
        try:
            from utils.alerts.channels import build_channels
            _channels = build_channels()
        except Exception:
            _log.exception("could not build alert channels")
            _channels = []
    return _channels


def _ensure_worker_alive():
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_loop,
                                       name="alert-dispatch", daemon=True)
            _worker.start()


def _safe(fn, *args):
    try:
        fn(*args)
    except Exception:
        try:
            _log.exception("alert worker duty %s failed", fn.__name__)
        except Exception:
            pass


def _worker_loop():
    while True:
        try:
            record = None
            try:
                record = _queue.get(timeout=30.0)
            except queue.Empty:
                pass
            if not enabled():
                continue  # drop record, perform no duties while disabled
            now = time.time()
            if record is not None:
                _safe(_process, record, now)
            _safe(_prune_windows, now)
            _safe(_retry_pending, now)
            _safe(_flush_warnings, now)
            _safe(_critical_reminders, now)
            _safe(_sentinel, now)
            _safe(_heartbeat_ping, now)
            _safe(_flush_state, now)
        except Exception:
            try:
                _log.exception("alert dispatcher iteration failed")
            except Exception:
                pass
            time.sleep(1.0)


def _throttle_cfg(severity):
    cfg = getattr(config, "ALERT_THROTTLE", {})
    return cfg.get(severity, {"min_interval_s": 6 * 3600, "reminder_s": None})


def _process(record, now):
    severity = record["severity"]
    key = record["dedup_key"]
    st = _get_state()
    prev = st.keys.get(key)
    prev_last_seen = float(prev.get("last_seen", 0)) if isinstance(prev, dict) else 0.0
    entry = st.entry(key, severity, now)
    entry.setdefault("last_attempt", 0.0)
    entry["count"] += 1
    entry["count_since_sent"] += 1
    entry["summary"] = record["summary"]

    if severity == "info":
        _log.info("[alert:info] %s: %s", record["category"], record["summary"])
        return

    if severity == "warning":
        w = _pending_warnings.setdefault(
            key, {"summary": record["summary"], "count": 0, "last": now})
        w["count"] += 1
        w["last"] = now
        w["summary"] = record["summary"]
        if len(_pending_warnings) > 200:
            _pending_warnings.pop(next(iter(_pending_warnings)), None)
        return

    cfg = _throttle_cfg(severity)
    min_interval = float(cfg.get("min_interval_s", 6 * 3600))
    reminder_s = cfg.get("reminder_s")
    last_attempt = max(float(entry.get("last_sent") or 0.0),
                       float(entry.get("last_attempt") or 0.0))
    # An incident lapses when no occurrences arrive for a full reminder (or
    # cooldown) window; a lapsed key that fires again is a fresh incident
    incident_gap = float(reminder_s) if reminder_s else min_interval
    new_incident = prev is None or (now - prev_last_seen) > incident_gap

    if severity == "critical":
        # First send is immediate; while the incident keeps recurring the
        # 6-h reminder owns redelivery (NOT a re-send every cooldown)
        should_send = (last_attempt == 0.0) or \
                      (new_incident and now - last_attempt >= min_interval)
    else:
        should_send = now - last_attempt >= min_interval

    if not should_send:
        _log.info("[alert:suppressed] %s (cooldown/ongoing incident)", key)
        return

    suppressed = entry["count_since_sent"] - 1
    subject, body = _render(record, entry, suppressed)
    outcome = _send_email(subject, body, retry_key=key)
    if outcome == "delivered":
        entry["last_sent"] = now
        entry["last_attempt"] = now
        entry["count_since_sent"] = 0
    elif outcome == "queued":
        # rendered body (with counts) is in the retry queue; stamp the
        # attempt so cooldowns hold, but last_sent stays behind so the
        # reminder path keeps owning this undelivered alert
        entry["last_attempt"] = now
        entry["count_since_sent"] = 0
    # "suppressed" (storm): state untouched — the next occurrence or
    # reminder redelivers with the accumulated count once the window frees


def _prune_windows(now):
    while _send_times and now - _send_times[0] > 3600:
        _send_times.popleft()
    while _emails_24h and now - _emails_24h[0] > 86400:
        _emails_24h.popleft()


def _storm_capped(now):
    cap = getattr(config, "ALERT_GLOBAL_MAX_PER_HOUR", 12)
    while _send_times and now - _send_times[0] > 3600:
        _send_times.popleft()
    return len(_send_times) >= cap


def _retry_pending(now):
    if not _pending_retries or _storm_capped(now):
        return
    due = [it for it in list(_pending_retries) if it["next_attempt_at"] <= now]
    for item in due[:3]:
        _pending_retries.remove(item)
        if _do_send(item["subject"], item["body"]):
            _send_times.append(now)
            continue
        item["attempts"] += 1
        if item["attempts"] <= RETRY_MAX_ATTEMPTS:
            item["next_attempt_at"] = now + RETRY_SPACING_S * item["attempts"]
            _pending_retries.append(item)
        else:
            _log.warning("alert email dropped after %d attempts: %s",
                         RETRY_MAX_ATTEMPTS, item["subject"])


def _flush_warnings(now):
    global _last_warning_flush
    if not _pending_warnings:
        return
    flush_s = float(_throttle_cfg("warning").get("batch_flush_s", 3600))
    if now - _last_warning_flush < flush_s:
        return
    lines = [f"- {k}: {w['summary']}  (x{w['count']})"
             for k, w in sorted(_pending_warnings.items())]
    body = ("Warning rollup (batched, max one email per "
            f"{int(flush_s // 60)} min):\n\n" + "\n".join(lines)
            + _footer())
    outcome = _send_email(
        f"{_prefix()}[WARNING] {len(lines)} warning type(s)", body,
        retry_key="warning-rollup")
    if outcome in ("delivered", "queued"):
        _pending_warnings.clear()
        _last_warning_flush = now
    # storm-suppressed: keep the batch; retried next iteration


def _critical_reminders(now):
    st = _get_state()
    reminder_s = _throttle_cfg("critical").get("reminder_s")
    if not reminder_s:
        return
    for key, entry in list(st.keys.items()):
        if entry.get("severity") != "critical":
            continue
        last_sent = float(entry.get("last_sent") or 0.0)
        last_attempt = max(last_sent, float(entry.get("last_attempt") or 0.0))
        if not last_attempt:
            continue  # never attempted — _process owns the first send
        undelivered = last_attempt > last_sent
        if entry.get("count_since_sent", 0) <= 0 and not undelivered:
            continue
        if now - last_attempt < float(reminder_s):
            continue
        last_seen = float(entry.get("last_seen") or 0.0)
        if now - last_seen > float(reminder_s) and not undelivered:
            # incident stopped while suppressed; close it quietly
            _log.info("[alert:incident-closed] %s stopped recurring "
                      "(%d unreported occurrences)", key,
                      entry.get("count_since_sent", 0))
            entry["count_since_sent"] = 0
            st._dirty = True
            continue
        subject = (f"{_prefix()}[CRITICAL] STILL FAILING: {key} — "
                   f"{entry.get('summary', '')[:120]}")
        body = (f"Key: {key}\n"
                f"Occurred {entry['count_since_sent']} time(s) since the last "
                f"email attempt at {_fmt(last_attempt)}; "
                f"{entry['count']} total since {_fmt(entry['first_seen'])}."
                + _footer())
        outcome = _send_email(subject, body, retry_key=f"reminder:{key}")
        if outcome == "delivered":
            entry["last_sent"] = now
        if outcome in ("delivered", "queued", "suppressed"):
            # storm-suppressed reminders also defer a full window rather
            # than re-attempting every 30s against the brake
            entry["last_attempt"] = now
        if outcome in ("delivered", "queued"):
            entry["count_since_sent"] = 0
        st._dirty = True


def _sentinel(now):
    global _last_sentinel
    if now - _last_sentinel < 60:
        return
    _last_sentinel = now
    from utils.alerts import alert
    for dest, age in health_mod.stale_scheduler_ticks(
            getattr(config, "HEARTBEAT_STALE_S", 600)):
        alert("scheduler.wedged",
              f"Scheduler loop for '{dest}' has not ticked for {int(age)}s "
              f"(thread alive, loop stuck)",
              severity="critical", dedup_key=f"sched-wedged:{dest}",
              context={"destination": dest, "age_s": int(age)})


def _heartbeat_ping(now):
    global _last_ping
    url = os.getenv("HEALTHCHECK_PING_URL")
    if not url:
        return
    interval = getattr(config, "HEALTHCHECK_PING_INTERVAL_S", 300)
    if now - _last_ping < interval:
        return
    if not health_mod.is_healthy():
        _log.warning("skipping healthcheck ping: unhealthy (silence -> alarm)")
        _last_ping = now
        return
    _last_ping = now
    try:
        import requests
        requests.get(url, timeout=10)
    except Exception as e:
        # class name only: the exception text can embed the capability URL,
        # which _log_tail would then copy into alert email bodies
        _log.debug("healthcheck ping failed (ignored): %s", type(e).__name__)


def _flush_state(now):
    st = _get_state()
    with _dropped_lock:
        st.dropped = max(st.dropped, _dropped)
    st.save_if_due(now)


# ── delivery ────────────────────────────────────────────────────────────

def _prefix():
    return "[screen-machine]"


def _send_email(subject, body, retry_key=None):
    """Storm-braked send. Returns "delivered" | "queued" | "suppressed".
    Worker thread only. retry_key identifies the alert in the retry queue
    (subjects are not unique across dedup keys)."""
    global _storm_active
    subject = " ".join(str(subject).split())  # header-safe: no CR/LF
    now = time.time()
    if _storm_capped(now):
        if not _storm_active:
            _storm_active = True
            cap = getattr(config, "ALERT_GLOBAL_MAX_PER_HOUR", 12)
            if _do_send(f"{_prefix()}[ERROR] alerting.storm — email cap reached",
                        f"Global cap of {cap} alert emails/hour reached; "
                        "suppressing further emails until the window frees. "
                        "Suppressed alerts are NOT lost — they redeliver with "
                        "accumulated counts. Check logs/alerts_state.json and "
                        "logs/screen_machine.log for what is failing."
                        + _footer()):
                _send_times.append(now)
        return "suppressed"
    _storm_active = False
    if _do_send(subject, body):
        _send_times.append(now)  # only DELIVERED emails consume cap slots
        return "delivered"
    _queue_retry(subject, body, retry_key or subject)
    return "queued"


def _queue_retry(subject, body, retry_key):
    for item in _pending_retries:
        if item["key"] == retry_key:
            return  # already queued; don't stack duplicates
    _pending_retries.append({"key": retry_key, "subject": subject,
                             "body": body, "attempts": 1,
                             "next_attempt_at": time.time() + RETRY_SPACING_S})


def _do_send(subject, body):
    global _channel_failures, _last_email_success
    delivered = False
    for ch in _get_channels():
        try:
            if ch.send(subject, body):
                delivered = True
        except Exception:
            _log.exception("channel %s raised from send()", ch.name)
    if delivered:
        _channel_failures = 0
        _last_email_success = time.time()
        _emails_24h.append(_last_email_success)
        _log.info("[alert:sent] %s", subject)
    else:
        _channel_failures += 1
        _log.warning("[alert:delivery-failed] (%d consecutive) %s",
                     _channel_failures, subject)
    return delivered


def _fmt(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except Exception:
        return str(ts)


def _footer():
    return (f"\n\n--\nscreen-machine on {socket.gethostname()} (WSL)  "
            f"{_fmt(time.time())}")


def _render(record, entry, suppressed):
    sev = record["severity"].upper()
    subject = (f"{_prefix()}[{sev}] {record['category']} — "
               f"{record['summary'][:120]}")
    parts = [
        f"Category: {record['category']}   Severity: {record['severity']}",
        f"Dedup key: {record['dedup_key']}",
        f"First seen: {_fmt(entry['first_seen'])}   "
        f"Total occurrences: {entry['count']}",
    ]
    if suppressed > 0:
        parts.append(f"Suppressed since last email for this key: {suppressed}")
    if record["context"]:
        try:
            parts.append("Context: " + json.dumps(record["context"], default=str)[:1500])
        except Exception:
            pass
    if record["detail"]:
        parts.append("\n" + record["detail"])
    if record["exc_text"]:
        parts.append("\nTraceback:\n" + record["exc_text"])
    tail = _log_tail()
    if tail:
        parts.append("\nRecent log tail (logs/screen_machine.log):\n" + tail)
    return subject, "\n".join(parts) + _footer()


def _log_tail():
    try:
        n = getattr(config, "ALERT_LOG_TAIL_LINES", 30)
        path = os.path.join(str(getattr(config, "ROOT_DIR", ".")),
                            "logs", "screen_machine.log")
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 16384))
            data = f.read().decode("utf-8", errors="replace")
        lines = data.splitlines()[-n:]
        return "\n".join(lines)[-6000:]
    except Exception:
        return ""


# ── test support ────────────────────────────────────────────────────────

def _reset_for_tests(state_path=None, channels=None):
    """Reset module state. Tests only. Pass channels=[] and a throwaway
    state_path on teardown so a leaked worker stays inert."""
    global _state, _channels, _storm_active, _channel_failures
    global _last_email_success, _last_ping, _last_sentinel, _dropped
    global _last_warning_flush
    with _state_lock:
        _state = (state_mod.AlertState.load(state_path)
                  if state_path else None)
    _channels = channels
    _pending_retries.clear()
    _pending_warnings.clear()
    _send_times.clear()
    _emails_24h.clear()
    _storm_active = False
    _channel_failures = 0
    _last_email_success = None
    _last_ping = 0.0
    _last_sentinel = 0.0
    _last_warning_flush = time.time()
    with _dropped_lock:
        _dropped = 0
    while True:
        try:
            _queue.get_nowait()
        except queue.Empty:
            break
