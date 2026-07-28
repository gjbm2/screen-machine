"""Liveness signals for alerting: scheduler heartbeat ticks, WS-server
supervision status, and the /api/health payload consumed by the Deadman
watchdog on the media server.

routes.scheduler is only ever imported lazily here — routes/scheduler.py
imports this module, so a top-level import would be a cycle.
"""

import logging
import shutil
import time

import config

_log = logging.getLogger("screen_machine.alerts")

_started_at = time.time()
_ticks = {}                 # destination -> last loop-iteration timestamp
_ws_thread = None
_ws_restarts = 0
_last_ws_restart = None
_last_watchdog_probe = None

# A healthy WS server never restarts; a crash-looping one (e.g. port already
# bound) restarts every ~5s, so "restarted recently" is the death signal —
# the supervisor thread itself is immortal and proves nothing
WS_RESTART_UNHEALTHY_S = 120


def tick(destination):
    """Called once per scheduler-loop iteration. One dict write."""
    _ticks[destination] = time.time()


def register_ws_thread(thread):
    global _ws_thread
    _ws_thread = thread


def note_ws_restart():
    global _ws_restarts, _last_ws_restart
    _ws_restarts += 1
    _last_ws_restart = time.time()


def note_watchdog_probe():
    global _last_watchdog_probe
    _last_watchdog_probe = time.time()


def stale_scheduler_ticks(stale_s):
    """[(destination, age_s)] for running-but-not-ticking scheduler loops."""
    out = []
    try:
        from routes.scheduler import running_schedulers
    except Exception:
        return out
    now = time.time()
    for dest, info in list(running_schedulers.items()):
        fut = (info or {}).get("future")
        if fut is None or fut.done():
            continue
        # A loop that has never ticked is measured from module start — real
        # loops tick within seconds of starting
        last = _ticks.get(dest) or _started_at
        age = now - last
        if age > stale_s:
            out.append((dest, age))
    return out


def ws_ok():
    # None = supervision not wired (tests, selftest) — treat as healthy
    if _ws_thread is None:
        return True
    if not _ws_thread.is_alive():
        return False
    if _last_ws_restart and time.time() - _last_ws_restart < WS_RESTART_UNHEALTHY_S:
        return False
    return True


def is_healthy():
    """Gate for the healthchecks.io ping: silence must mean trouble."""
    return ws_ok() and not stale_scheduler_ticks(config.HEARTBEAT_STALE_S)


def build_health():
    from utils.alerts import dispatcher

    now = time.time()
    sched = {}
    try:
        from routes.scheduler import running_schedulers, scheduler_states
        for dest, info in list(running_schedulers.items()):
            fut = (info or {}).get("future")
            last = _ticks.get(dest)
            sched[dest] = {
                "state": scheduler_states.get(dest),
                "future_done": bool(fut.done()) if fut is not None else None,
                "last_tick_age_s": round(now - last, 1) if last else None,
            }
    except Exception as e:
        sched = {"error": str(e)}

    try:
        du = shutil.disk_usage("/")
        disk_pct = round(du.used / du.total * 100, 1)
    except Exception:
        disk_pct = None

    stale = [d for d, _ in stale_scheduler_ticks(config.HEARTBEAT_STALE_S)]
    return {
        "ok": ws_ok() and not stale,
        "started_at": _started_at,
        "uptime_s": round(now - _started_at, 1),
        "scheduler": sched,
        "stale_destinations": stale,
        "ws_server": {"supervised": _ws_thread is not None,
                      "alive": ws_ok(),
                      "restarts": _ws_restarts,
                      "last_restart": _last_ws_restart},
        "alerts": dispatcher.stats(),
        "disk": {"root_pct": disk_pct},
        "last_watchdog_probe": _last_watchdog_probe,
    }
