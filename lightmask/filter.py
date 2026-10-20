"""lightmask.filter: time-based lux filter (DCL §2.6 "Filter", "Staleness and warm start").

One ``LuxFilter`` per sensor. It advances only when a sample is ingested, using
the sample timestamps the caller passes (room_service's own monotonic receive
time; the relay's ``mono_ms`` is another host's clock). Reading it never
changes it, unlike Flask's alpha=0.2 EMA, which advances on every read.

Behaviour
---------
* Works in log-lux: ``ev = log2(lux + log_offset_lux)``. The offset (default
  1 lux) keeps 0 lux finite and stops sub-lux sensor noise in a dark room
  counting as whole EVs.
* Snap: a reading more than ``snap_ev`` (0.75 EV) from the state, on the same
  side, for ``snap_confirm`` (2) consecutive samples moves the state with
  tau ``snap_tau_s`` (0.15 s). The first far sample is not applied, so a
  single-sample spike never reaches the output. Once snapping, it keeps the
  snap tau until within ``snap_exit_ev`` (0.1 EV; set it equal to snap_ev
  for the literal DCL rule), so a step settles in well under a second
  instead of finishing its last 0.75 EV on the drift tau.
* Drift: otherwise tau ``drift_tau_s`` (2.5 s), optionally
  ``drift_tau_up_s`` when brightening.
* Updates are exponential in real time: ``ev += (x - ev) * (1 - exp(-dt/tau))``
  with dt = time since the previous sample, so 1.4 Hz and 10 Hz streams give
  the same response in seconds; a slow rate is never a fault.
* Held (``held=True``, a latency-calibration capture): the sample keeps the
  link alive but never moves the output, and the solar-ramp weight freezes
  too. The first ``held=False`` sample resumes from the frozen state with dt =
  one sample interval, so the hold itself causes no jump. ``is_held(t)`` is
  what callers gate mask pushes on: it turns false once the link goes stale,
  so a relay that dies mid-capture does not pin the mask forever. A hold that
  starts after a stale gap still re-initialises on release (below).
* Sequence: a ``seq`` that is not greater than the last accepted one is
  rejected (backwards or duplicate). Call ``reset_sequence()`` when a relay
  opens a new connection (a restarted relay may count from 0 again). A seq
  that is not an integer (or an integral float) is rejected as invalid_seq.
* Readings must be real numbers (bool and str are rejected), are clamped to
  [lux_min, lux_max] (0 to 200 klx), and NaN/inf are rejected. A non-finite
  ``t`` is rejected as invalid_time (it would otherwise poison the clock).
* Staleness: no accepted sample for ``stale_after_s`` (10 s) -> stale.
  ``solar_weight(t)`` then ramps 0 -> 1 over ``stale_ramp_s`` (30 s); when
  samples return it ramps back to 0 over ``fresh_ramp_s`` (5 s). The first
  sample after a stale gap re-initialises the state at that reading (the old
  state is too old to filter from; the 5 s ramp hides the change).
* Warm start: ``LuxFilter(..., warm_lux=x, start_t=t)`` (or ``from_state``)
  starts at a persisted value with solar_weight 0, as if a sample had arrived
  at ``start_t``: the last value is used until the first fresh sample, and
  if none comes within 10 s the normal stale ramp starts. Without a warm
  value the filter starts cold: no output, solar_weight 1.

``intensity_for(config, sensor, lux)`` maps a filtered lux through that
sensor's intensity.json curve. ``exceeds_deadband`` and ``MaskGate`` apply
the deadband and the 20 Hz per-screen limit to the final masks.
"""
from __future__ import annotations

import json
import math
import numbers
import os
import threading
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Mapping

from .model import REPO_ROOT, LightConfig, intensity_for  # noqa: F401  (re-exported)

__all__ = [
    "FilterParams", "LuxFilter", "SampleResult", "FilterState", "intensity_for",
    "exceeds_deadband", "MaskGate", "load_filter_params", "FilterParamsWatcher",
]


@dataclass(frozen=True)
class FilterParams:
    # snap / drift (DCL §2.6 item 2; D27 item 11 defaults)
    snap_ev: float = 0.75
    snap_confirm: int = 2
    snap_tau_s: float = 0.15
    snap_exit_ev: float = 0.1
    drift_tau_s: float = 2.5
    drift_tau_up_s: float | None = None
    log_offset_lux: float = 1.0
    # ingest validation (LIVE_V1 §2.2b)
    lux_min: float = 0.0
    lux_max: float = 200_000.0
    # staleness (DCL §2.6 item 3)
    stale_after_s: float = 10.0
    stale_ramp_s: float = 30.0
    fresh_ramp_s: float = 5.0
    # output gating (DCL §2.6 item 2)
    deadband_brightness: float = 0.005
    deadband_warm_alpha: float = 0.002
    max_rate_hz: float = 20.0
    transition_ms: int = 150

    def __post_init__(self) -> None:
        for name in ("snap_tau_s", "drift_tau_s", "log_offset_lux", "stale_after_s",
                     "stale_ramp_s", "fresh_ramp_s", "max_rate_hz"):
            if not (getattr(self, name) > 0):
                raise ValueError(f"FilterParams.{name} must be > 0")
        if self.drift_tau_up_s is not None and not self.drift_tau_up_s > 0:
            raise ValueError("FilterParams.drift_tau_up_s must be > 0 or None")
        if self.snap_confirm < 1:
            raise ValueError("FilterParams.snap_confirm must be >= 1")
        if not (0 <= self.snap_exit_ev <= self.snap_ev):
            raise ValueError("FilterParams.snap_exit_ev must be in [0, snap_ev]")
        if not (self.lux_min <= self.lux_max):
            raise ValueError("FilterParams.lux_min must be <= lux_max")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "FilterParams":
        """Known keys only; unknown keys are ignored (forward compatible)."""
        if not data:
            return cls()
        known = {f.name: f for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for k, v in data.items():
            if k not in known:
                continue
            if k == "snap_confirm":
                kwargs[k] = int(v)
            elif k == "transition_ms":
                kwargs[k] = int(v)
            elif k == "drift_tau_up_s" and v is None:
                kwargs[k] = None
            else:
                kwargs[k] = float(v)
        return cls(**kwargs)


def load_filter_params(path: Path | str | None = None) -> FilterParams:
    """FilterParams from the ``"filter"`` object of config/light.json.

    config/light.json does not exist yet (DCL §2.6 plans it); a missing file
    or missing key gives the defaults above.
    """
    p = Path(path) if path else REPO_ROOT / "config" / "light.json"
    if not p.exists():
        return FilterParams()
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return FilterParams.from_mapping((data or {}).get("filter"))


class FilterParamsWatcher:
    """load_filter_params, re-read when the file's mtime/size changes.
    A bad file keeps the previous params and sets ``last_error``."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else REPO_ROOT / "config" / "light.json"
        self._sig: Any = object()
        self._params = FilterParams()
        self._lock = threading.Lock()
        self.last_error: str | None = None

    def get(self) -> FilterParams:
        with self._lock:
            try:
                st = os.stat(self.path)
                sig: Any = (st.st_mtime_ns, st.st_size)
            except FileNotFoundError:
                sig = None
            if sig != self._sig:
                try:
                    self._params = load_filter_params(self.path) if sig is not None else FilterParams()
                    self.last_error = None
                except Exception as e:
                    self.last_error = f"{type(e).__name__}: {e}"
                self._sig = sig
            return self._params


@dataclass(frozen=True)
class SampleResult:
    accepted: bool
    reason: str | None = None     # when rejected: "seq_backwards", "seq_duplicate", "invalid_seq",
    #                               "invalid_lux", "invalid_time"
    lux: float | None = None      # filtered lux after this sample
    moved: bool = False           # the filtered state changed
    mode: str | None = None       # "init", "drift", "snap", "pending", "held"


@dataclass(frozen=True)
class FilterState:
    lux: float | None
    ev: float | None
    stale: bool
    solar_weight: float
    held: bool                    # is_held(t): holding and not stale
    age_s: float | None           # seconds since the last accepted sample


def _is_number(x) -> bool:
    return isinstance(x, numbers.Real) and not isinstance(x, bool)


def _as_seq(x) -> int | None:
    """An integer sequence number, or None when ``x`` is not one."""
    if isinstance(x, bool):
        return None
    if isinstance(x, numbers.Integral):
        return int(x)
    if isinstance(x, numbers.Real):
        f = float(x)
        if math.isfinite(f) and f.is_integer():
            return int(f)
    return None


class LuxFilter:
    def __init__(self, sensor: str, params: FilterParams | None = None, *,
                 warm_lux: float | None = None, start_t: float | None = None) -> None:
        self.sensor = sensor
        self.params = params or FilterParams()
        self._ev: float | None = None
        self._last_t: float | None = None       # last accepted sample (incl. held)
        self._last_seq: int | None = None
        self._pending = 0
        self._pending_sign = 0
        self._snapping = False
        self._snap_sign = 0
        self._held = False
        self._reinit = False                    # a hold began after a stale gap
        # solar weight is piecewise linear: value _w at _w_t, moving by _w_dir
        self._w = 1.0
        self._w_t: float | None = None
        self._w_dir = 0
        self.samples = 0
        self.rejected = 0
        if warm_lux is not None:
            if start_t is None:
                raise ValueError("warm start needs start_t (the filter's clock)")
            lux = self._clamp(float(warm_lux))
            if lux is not None:
                self._ev = self._to_ev(lux)
                self._last_t = float(start_t)
                self._w, self._w_t, self._w_dir = 0.0, float(start_t), 0

    # ── persistence ─────────────────────────────────────────────────────
    def export_state(self) -> dict:
        """Small JSON-able dict to persist (for example on each mask push)."""
        return {"v": 1, "sensor": self.sensor, "lux": self.lux}

    @classmethod
    def from_state(cls, state: Mapping | None, start_t: float, params: FilterParams | None = None,
                   sensor: str | None = None) -> "LuxFilter":
        st = state if isinstance(state, Mapping) else {}
        name = sensor or st.get("sensor") or "sensor"
        lux = st.get("lux")
        # a corrupt persisted value means a cold start, never a crash at startup
        if not _is_number(lux) or not math.isfinite(float(lux)):
            return cls(name, params)
        return cls(name, params, warm_lux=float(lux), start_t=start_t)

    # ── helpers ─────────────────────────────────────────────────────────
    def _to_ev(self, lux: float) -> float:
        return math.log2(lux + self.params.log_offset_lux)

    def _from_ev(self, ev: float) -> float:
        return max(0.0, 2.0 ** ev - self.params.log_offset_lux)

    def _clamp(self, lux: float) -> float | None:
        if not math.isfinite(lux):
            return None
        return min(self.params.lux_max, max(self.params.lux_min, lux))

    def set_params(self, params: FilterParams) -> None:
        """Swap constants (config reload). The state carries over at the same
        lux: a changed ``log_offset_lux`` or clamp re-expresses it, so a reload
        never shifts the output."""
        old, lux = self.params, self.lux
        self.params = params
        if lux is not None and (old.log_offset_lux != params.log_offset_lux
                                or not params.lux_min <= lux <= params.lux_max):
            self._ev = self._to_ev(self._clamp(lux))

    def reset_sequence(self) -> None:
        """Forget the last seq (call on a new relay connection / hello)."""
        self._last_seq = None

    # ── outputs (never mutate) ──────────────────────────────────────────
    @property
    def lux(self) -> float | None:
        return None if self._ev is None else self._from_ev(self._ev)

    @property
    def ev(self) -> float | None:
        return self._ev

    @property
    def held(self) -> bool:
        """The last accepted sample had held=true (latched; gate on is_held(t))."""
        return self._held

    def is_held(self, t: float) -> bool:
        """True while a calibration capture holds this sensor and the link is
        alive. Callers keep the last mask for the sensor's targets while this
        is true. It turns false after stale_after_s without samples, so a relay
        that dies mid-capture lets the screens follow the stale ramp."""
        return self._held and not self.is_stale(t)

    def is_stale(self, t: float) -> bool:
        return self._last_t is None or (t - self._last_t) > self.params.stale_after_s

    def _w_at(self, t: float) -> float:
        if self._w_dir == 0 or self._w_t is None:
            return self._w
        dt = max(0.0, t - self._w_t)
        if self._w_dir < 0:
            return max(0.0, self._w - dt / self.params.fresh_ramp_s)
        return min(1.0, self._w + dt / self.params.stale_ramp_s)

    def solar_weight(self, t: float) -> float:
        """0 = use the sensor, 1 = use the solar curve; ramps in between."""
        if self._last_t is None:
            return 1.0
        t_stale = self._last_t + self.params.stale_after_s
        if t > t_stale:
            return min(1.0, self._w_at(t_stale) + (t - t_stale) / self.params.stale_ramp_s)
        return self._w_at(t)

    def state(self, t: float) -> FilterState:
        return FilterState(lux=self.lux, ev=self._ev, stale=self.is_stale(t),
                           solar_weight=self.solar_weight(t), held=self.is_held(t),
                           age_s=None if self._last_t is None else max(0.0, t - self._last_t))

    def intensity(self, config: LightConfig) -> float | None:
        """Curve intensity of the filtered lux for this sensor (None if no state/mapping)."""
        lux = self.lux
        return None if lux is None else intensity_for(config, self.sensor, lux)

    # ── ingest ──────────────────────────────────────────────────────────
    def sample(self, lux: float, t: float, *, seq: int | None = None, held: bool = False) -> SampleResult:
        """Ingest one reading taken/received at monotonic time ``t`` (seconds)."""
        p = self.params
        if not _is_number(t) or not math.isfinite(float(t)):
            self.rejected += 1
            return SampleResult(False, "invalid_time", self.lux)
        if seq is not None:
            seq = _as_seq(seq)
            if seq is None:
                self.rejected += 1
                return SampleResult(False, "invalid_seq", self.lux)
            if self._last_seq is not None and seq <= self._last_seq:
                self.rejected += 1
                return SampleResult(False, "seq_duplicate" if seq == self._last_seq else "seq_backwards", self.lux)
        v = None
        if _is_number(lux):
            try:
                v = self._clamp(float(lux))
            except OverflowError:       # an int too large for a float
                v = None
        if v is None:
            self.rejected += 1
            return SampleResult(False, "invalid_lux", self.lux)
        if seq is not None:
            self._last_seq = seq
        t = float(t)
        prev_t = self._last_t
        if prev_t is not None and t < prev_t:
            t = prev_t                     # clock went backwards: treat as dt = 0
        gap = None if prev_t is None else t - prev_t
        w_now = self.solar_weight(t)       # includes any stale ramp up to now
        self._last_t = t
        self.samples += 1

        if held:
            # liveness only: output and solar weight freeze until held=False
            if gap is not None and gap > p.stale_after_s:
                self._reinit = True        # the frozen state is too old to resume from
            self._held = True
            self._pending, self._pending_sign, self._snapping = 0, 0, False
            self._w, self._w_t, self._w_dir = w_now, t, 0
            return SampleResult(True, None, self.lux, False, "held")

        self._held = False
        self._w, self._w_t = w_now, t
        self._w_dir = -1 if w_now > 0 else 0

        x = self._to_ev(v)
        if self._ev is None or self._reinit or (gap is not None and gap > p.stale_after_s):
            self._ev = x
            self._reinit = False
            self._pending, self._pending_sign, self._snapping = 0, 0, False
            return SampleResult(True, None, self.lux, True, "init")

        d = x - self._ev
        sign = 1 if d > 0 else (-1 if d < 0 else 0)
        tau: float | None
        if abs(d) > p.snap_ev:
            if self._snapping and sign == self._snap_sign:
                tau, mode = p.snap_tau_s, "snap"
            else:
                if self._pending_sign == sign:
                    self._pending += 1
                else:
                    self._pending, self._pending_sign = 1, sign
                if self._pending >= p.snap_confirm:
                    self._snapping, self._snap_sign = True, sign
                    self._pending, self._pending_sign = 0, 0
                    tau, mode = p.snap_tau_s, "snap"
                else:
                    self._snapping = False
                    tau, mode = None, "pending"
        else:
            self._pending, self._pending_sign = 0, 0
            if self._snapping and abs(d) > p.snap_exit_ev:
                tau, mode = p.snap_tau_s, "snap"
            else:
                self._snapping = False
                tau = p.drift_tau_up_s if (d > 0 and p.drift_tau_up_s) else p.drift_tau_s
                mode = "drift"

        moved = False
        if tau is not None and gap is not None and gap > 0 and d != 0:
            self._ev += d * (1.0 - math.exp(-gap / tau))
            moved = True
        return SampleResult(True, None, self.lux, moved, mode)


# ───────── output gating ──────────────────────────────────────────────────

def exceeds_deadband(prev: Mapping | None, new: Mapping, params: FilterParams | None = None) -> bool:
    """True when ``new`` should be sent: nothing sent yet, the source changed,
    |Δbrightness| >= deadband_brightness or |Δwarm_alpha| >= deadband_warm_alpha.
    Compare against the last value *sent*, so slow creep still gets through."""
    p = params or FilterParams()
    if prev is None:
        return True
    if prev.get("source") != new.get("source"):
        return True
    eps = 1e-9   # 0.505 - 0.5 is 0.00499999... in binary
    if abs(float(new["brightness"]) - float(prev["brightness"])) + eps >= p.deadband_brightness:
        return True
    if abs(float(new["warm_alpha"]) - float(prev["warm_alpha"])) + eps >= p.deadband_warm_alpha:
        return True
    return False


class MaskGate:
    """Per-screen send gate: deadband against the last sent mask, and at most
    ``max_rate_hz`` sends. ``offer`` returns the mask to send now or None; a
    mask held back only by the rate limit is kept, and ``due(t)`` returns it
    once the interval has passed (call it from a timer)."""

    def __init__(self, params: FilterParams | None = None) -> None:
        self.params = params or FilterParams()
        self.last_sent: dict | None = None
        self._last_t: float | None = None
        self._pending: dict | None = None

    def _interval(self) -> float:
        return 1.0 / self.params.max_rate_hz

    def offer(self, mask: Mapping, t: float, *, force: bool = False) -> dict | None:
        if not force and not exceeds_deadband(self.last_sent, mask, self.params):
            self._pending = None
            return None
        if not force and self._last_t is not None and t - self._last_t < self._interval():
            self._pending = dict(mask)
            return None
        return self._send(mask, t)

    def due(self, t: float) -> dict | None:
        if self._pending is None or (self._last_t is not None and t - self._last_t < self._interval()):
            return None
        m, self._pending = self._pending, None
        return self._send(m, t)

    def wait_s(self, t: float) -> float | None:
        """Seconds until a held-back mask may go, or None if nothing is waiting."""
        if self._pending is None:
            return None
        return 0.0 if self._last_t is None else max(0.0, self._interval() - (t - self._last_t))

    def _send(self, mask: Mapping, t: float) -> dict:
        self.last_sent = dict(mask)
        self._last_t = t
        self._pending = None
        return dict(mask)
