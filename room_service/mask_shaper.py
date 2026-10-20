"""MaskShaper: one per screen, between the MaskArbiter and the page outbox (DCL v3.1 §4.4, §5.5).

It decides *when* a mask goes out and with which `transition_ms`, so the kiosk's compositor is
idle in steady light:

* **snap** (a filter snap or re-init, |Δbrightness| >= `snap_delta_brightness`, a change of
  lightmask `source`, or an override change): sent at once with `snap_transition_ms` (200),
  provided it moved beyond the base deadbands (`mask.deadband_*`).
* **source** (the arbiter switched between sensor and Flask): sent at once with
  `source_change_transition_ms` (2000).
* **drift** (anything else beyond `drift_deadband_brightness` 0.015 or the warm-alpha
  deadband): at most once per `drift_interval_s` (3 s), counted from the last send of any
  kind, with `drift_transition_ms` (1000). Below the deadbands nothing is sent.
* **flask** (the interim Flask source after a sensor has driven the screen): today's rule,
  the base deadbands or any `warm_hex` change, `mask.transition_ms`.
* Never more than `max_sets_per_s` (10) per screen, or `live_max_sets_per_s` (2) while the
  screen is live (LIVE_V1 §2.2 rule 11). A send held back by these limits is kept, latest
  value wins, and goes when `due()` allows; the caller arms a timer from `wait_s()`.

Pure and clock-free: every call takes the time `t`. Deltas are always measured against the
last mask *sent*, so slow creep accumulates and eventually crosses the drift deadband.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Mapping

from .ingest_config import ShaperConfig

EPS = 1e-9          # 0.505 - 0.5 is 0.00499999... in binary (lightmask.exceeds_deadband does the same)
_RANK = {"drift": 0, "flask": 1, "snap": 2, "source": 3}


@dataclass
class Shaped:
    mask: dict                # brightness, warm_hex, warm_alpha, source (+ sensor, solar_weight)
    kind: str                 # snap | drift | source | flask
    transition_ms: int
    arrival: float | None     # receive time of the reading behind this value (latency stats)
    due_at: float = 0.0


def _num(m: Mapping, k: str) -> float:
    return float(m.get(k) or 0.0)


class MaskShaper:
    def __init__(self, cfg: ShaperConfig) -> None:
        self.cfg = cfg
        self.last_sent: dict | None = None
        self.last_t: float | None = None
        self.pending: Shaped | None = None

    # -- classification ----------------------------------------------------------
    def _base_moved(self, new: Mapping) -> bool:
        p = self.last_sent
        return (abs(_num(new, "brightness") - _num(p, "brightness")) + EPS >= self.cfg.deadband_brightness
                or abs(_num(new, "warm_alpha") - _num(p, "warm_alpha")) + EPS >= self.cfg.deadband_warm_alpha)

    def classify(self, mask: Mapping, *, hint: str | None = None, flask: bool = False) -> str | None:
        """The kind of send `mask` needs against the last sent mask, or None (nothing to send)."""
        p = self.last_sent
        if p is None:
            return "snap"
        if hint == "source":
            return "source"
        if flask:
            if (self._base_moved(mask) or mask.get("warm_hex") != p.get("warm_hex")
                    or mask.get("source") != p.get("source")):
                return "flask"
            return None
        if mask.get("source") != p.get("source"):
            return "snap"
        db = abs(_num(mask, "brightness") - _num(p, "brightness"))
        if db + EPS >= self.cfg.snap_delta_brightness:
            return "snap"
        if hint == "snap" and self._base_moved(mask):
            return "snap"
        if (db + EPS >= self.cfg.drift_deadband_brightness
                or abs(_num(mask, "warm_alpha") - _num(p, "warm_alpha")) + EPS >= self.cfg.drift_alpha):
            return "drift"
        return None

    def _transition(self, kind: str) -> int:
        c = self.cfg
        return {"snap": c.snap_transition_ms, "drift": c.drift_transition_ms,
                "source": c.source_change_transition_ms, "flask": c.flask_transition_ms}[kind]

    def _earliest(self, kind: str, max_rate_hz: float) -> float:
        if self.last_t is None:
            return float("-inf")
        gap = 1.0 / max_rate_hz
        if kind == "drift":
            gap = max(gap, self.cfg.drift_interval_s)
        return self.last_t + gap

    # -- the API -----------------------------------------------------------------
    def offer(self, mask: Mapping, t: float, *, hint: str | None = None, arrival: float | None = None,
              max_rate_hz: float | None = None, flask: bool = False) -> Shaped | None:
        """A new candidate mask. Returns what to send now, or None (nothing, or held back)."""
        rate = max_rate_hz or self.cfg.max_sets_per_s
        kind = self.classify(mask, hint=hint, flask=flask)
        if kind is None:
            self.pending = None            # back within the deadbands of what the page shows
            return None
        if self.pending is not None and _RANK[self.pending.kind] > _RANK[kind]:
            kind = self.pending.kind       # a held-back snap stays a snap; the newest value wins
        s = Shaped(dict(mask), kind, self._transition(kind), arrival)
        s.due_at = self._earliest(kind, rate)
        if t + EPS >= s.due_at:
            self.pending = None
            return s
        self.pending = s
        return None

    def due(self, t: float, max_rate_hz: float | None = None) -> Shaped | None:
        """The held-back mask, if its time has come."""
        s = self.pending
        if s is None:
            return None
        s.due_at = max(s.due_at, self._earliest(s.kind, max_rate_hz or self.cfg.max_sets_per_s))
        if t + EPS < s.due_at:
            return None
        self.pending = None
        return s

    def wait_s(self, t: float, max_rate_hz: float | None = None) -> float | None:
        if self.pending is None:
            return None
        due = max(self.pending.due_at, self._earliest(self.pending.kind, max_rate_hz or self.cfg.max_sets_per_s))
        return max(0.0, due - t)

    def defer(self, s: Shaped) -> None:
        """Could not be sent (frozen, suspended, no page): keep it as the pending value."""
        if self.pending is None or _RANK[s.kind] >= _RANK[self.pending.kind]:
            self.pending = s
        else:
            self.pending = Shaped(s.mask, self.pending.kind, self.pending.transition_ms, s.arrival,
                                  self.pending.due_at)

    def sent(self, s: Shaped, t: float) -> None:
        self.last_sent = dict(s.mask)
        self.last_t = t

    def seed(self, mask: Mapping) -> None:
        """What the page shows when the shaper starts tracking (e.g. a legacy Flask push)."""
        self.last_sent = dict(mask)

    def cancel(self) -> None:
        self.pending = None


class LatencyStats:
    """Rolling reading-arrival -> mask.set-enqueue times (ms), for /api/v1/status."""

    def __init__(self, window: int = 512) -> None:
        self.immediate: deque[float] = deque(maxlen=max(8, window))   # sent while handling the reading
        self.deferred: deque[float] = deque(maxlen=max(8, window))    # sent later by the shaper
        self.count = 0

    def add(self, ms: float, deferred: bool = False) -> None:
        (self.deferred if deferred else self.immediate).append(ms)
        self.count += 1

    @staticmethod
    def summary(values) -> dict:
        v = sorted(values)
        if not v:
            return {"p50": None, "p95": None, "max": None, "n": 0}

        def pct(q: float) -> float:                     # nearest rank
            return round(v[min(len(v) - 1, max(0, math.ceil(q * len(v)) - 1))], 3)
        return {"p50": pct(0.50), "p95": pct(0.95), "max": round(v[-1], 3), "n": len(v)}

    def status(self) -> dict:
        """p50/p95 (top level) are the immediate sends: the server's own processing time."""
        imm = self.summary(self.immediate)
        return {"p50": imm["p50"], "p95": imm["p95"], "immediate": imm,
                "deferred": self.summary(self.deferred), "total": self.count}
