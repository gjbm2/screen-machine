"""Persistent per-dedup-key alert state.

One JSON file (config.ALERT_STATE_PATH), written only by the dispatcher
worker via atomic tmp+rename. Persistence exists so a restart mid-incident
does not replay the whole email history; it deliberately still allows one
fresh notification after restart (a restart is itself signal).
"""

import json
import logging
import os
import tempfile
import time

MAX_KEYS = 500

_log = logging.getLogger("screen_machine.alerts")


class AlertState:
    def __init__(self, path):
        self.path = path
        self.keys = {}
        self.dropped = 0
        self.last_alive = None
        self._dirty = False
        self._last_save = 0.0

    @classmethod
    def load(cls, path):
        st = cls(path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                keys = data.get("keys")
                st.keys = keys if isinstance(keys, dict) else {}
                st.dropped = int(data.get("dropped", 0))
                st.last_alive = data.get("last_alive")
        except FileNotFoundError:
            pass
        except Exception as e:
            _log.warning("alert state file unreadable (%s); starting fresh", e)
        return st

    def entry(self, key, severity, now):
        e = self.keys.get(key)
        if e is None or not isinstance(e, dict):
            e = {"first_seen": now, "last_seen": now, "count": 0,
                 "count_since_sent": 0, "last_sent": 0.0, "last_attempt": 0.0,
                 "severity": severity, "summary": ""}
            self.keys[key] = e
            self._evict(now)
        e["last_seen"] = now
        e["severity"] = severity
        self._dirty = True
        return e

    def note_dropped(self, n=1):
        self.dropped += n
        self._dirty = True

    def _evict(self, now):
        if len(self.keys) <= MAX_KEYS:
            return
        oldest = sorted(self.keys, key=lambda k: self.keys[k].get("last_seen", 0))
        for k in oldest[:len(self.keys) - MAX_KEYS]:
            self.keys.pop(k, None)

    def save_if_due(self, now, min_interval_s=30.0):
        # last_alive doubles as the restart-detection heartbeat, so a save is
        # due on the interval even when nothing else changed
        if now - self._last_save < min_interval_s:
            return
        self.last_alive = now
        self.save(now)

    def save(self, now=None):
        try:
            payload = {"keys": self.keys, "dropped": self.dropped,
                       "last_alive": self.last_alive or (now or time.time())}
            d = os.path.dirname(self.path) or "."
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".alerts_state.", dir=d)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                os.replace(tmp, self.path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            self._dirty = False
            self._last_save = now or time.time()
        except Exception as e:
            _log.warning("could not save alert state: %s", e)
