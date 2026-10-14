"""Show-bus inputs: per-fixture signals, smoothing, liveness and the beat follower (LIVE_V1 1.1 §1.9).

The renderer does no DSP of its own (owner ruling, AV_PROGRAMME A.1.6): no section, mood or genre detection.
Signals are the bus's raw latest values plus one-pole smoothing; the one exception is the beat follower, a
metronome follower used only to compensate the render->photon latency.

Bindable signals are scalars 0-1 (``BIND_SIGNALS``; ``rgb`` is not bindable in v1):
  raw       level, pulse, pan, tilt, zoom ((x + 1) / 2 for pan and tilt), activity (bus global)
  smoothed  activity_slow (tau 2 s), level_slow (tau 1 s)
  follower  beat: 1 at each predicted beat, decaying (unlocked or off: the raw pulse-edge envelope, which
            lands ``lead`` late); beat_phase 0-1 (held when unlocked); beat_confidence
When the bus is down or the input stale, each signal eases to its idle value (triggers 0, levels 0.5,
beat_phase held): hold 250 ms, then 2 s; recovery needs 1 s of fresh frames, then a 1 s ease back (R16).

Clocks (R15): bus ``t_ms`` is CLOCK_MONOTONIC (time.monotonic()) ms, stamped by the publisher; the follower
uses only differences of it.  The render thread maps its pace_clock deadlines into that clock with
``off = time.monotonic() - pace_clock()`` sampled each frame (renderer.py), never comparing raw clocks.

Threads: bus frames arrive on the asyncio loop (``ShowInputs.on_frame``).  Each :class:`FixtureSignals`
updates its smoothing and follower there and publishes an immutable :class:`Snapshot` with one reference
assignment (atomic under the GIL).  The render thread and the API only read snapshots; per-session state
(liveness, the held phase) is advanced by the render thread alone (:meth:`Sampler.sample`).
"""
from __future__ import annotations

import collections
import json
import logging
import math
import pathlib
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("stream_service.inputs")

TRIGGERS = ("pulse", "beat")
BIND_SIGNALS = ("level", "pulse", "pan", "tilt", "zoom", "activity", "activity_slow", "level_slow",
                "beat", "beat_phase", "beat_confidence")
FOLLOW_ATTRS = ("effect", "variant")      # discrete attributes whose changes the director can follow
BEAT_FOLLOW = ("extrapolate", "off")

TAU_ACTIVITY_S = 2.0
TAU_LEVEL_S = 1.0
TAU_RGB_S = 1.5
BEAT_DECAY_S = 0.18        # the beat envelope: exp(-t / BEAT_DECAY_S) after each (predicted) beat
LINK_TIMEOUT_S = 0.5       # no frame for this long: the link is down
STALE_HOLD_S = 0.25        # stale: hold the last values this long ...
STALE_EASE_S = 2.0         # ... then ease to idle over this long
RECOVER_AFTER_S = 1.0      # recovery: this long of fresh frames ...
RECOVER_EASE_S = 1.0       # ... then ease back over this long
FOLLOW_STABLE_S = 0.3      # a follow_dmx attribute counts once it has held a new value this long
DEFAULT_PHOTON_MS = 178.7  # presented -> photon when latency.json is absent (south, 13 Oct)
DEFAULT_FIXTURES = {"north-screen": "screen.north", "south-screen": "screen.south", "devtest": "screen.north"}


def signal_catalog() -> dict:
    """The catalog's ``signals`` block: {name: {kind, idle}}."""
    return {s: {"kind": "trigger" if s in TRIGGERS else "phase" if s == "beat_phase" else "level",
                "idle": 0.0 if s in TRIGGERS else None if s == "beat_phase" else 0.5} for s in BIND_SIGNALS}


def idle_value(signal: str) -> float:
    return 0.0 if signal in TRIGGERS else 0.5


def default_fixture(screen: str) -> str:
    return DEFAULT_FIXTURES.get(screen, "screen.north")


def load_photon(path: Optional[pathlib.Path], screen: str) -> tuple[float, str]:
    """presented -> photon ms for a screen from config/room/latency.json -> (ms, source).

    ``north-screen`` (or the fixture ``screen.north``) reads ``screens.north``; a screen with no entry uses
    another's value, flagged."""
    try:
        data = json.loads(pathlib.Path(path).read_text()) if path else {}
        screens = data.get("screens") or {}
        key = screen.split(".")[-1] if "." in screen else screen.split("-")[0]
        name = key if key in screens else next(iter(screens), None)
        ms = float(screens[name]["effective"]["presented_to_photon_ms"])
        if not 0 < ms < 2000:
            raise ValueError(f"presented_to_photon_ms {ms} out of range")
        return ms, f"latency.json:{name}" + ("" if name == key else f" (no {key} measurement)")
    except (OSError, ValueError, KeyError, TypeError) as e:
        if path and pathlib.Path(path).exists():
            log.warning("latency file %s unusable (%s); presented->photon %.0f ms", path, e, DEFAULT_PHOTON_MS)
        return DEFAULT_PHOTON_MS, "default"


def _num(v: Any, default: float = 0.0) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return default
    return float(v)


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


class BeatFollower:
    """A metronome follower, not a beat tracker (LIVE_V1 1.1 §1.9, R18).

    * Edges: a Schmitt trigger on the input normalised by its recent peak (decaying over ``peak_tau_s``, never
      below ``floor``): an edge when it rises to >= ``rise`` after having been <= ``fall``.  An edge inside the
      refractory period after the last accepted one (0.35 P when locked, 150 ms unlocked) is rejected.  It is
      timed half a sample interval before the sample that crossed.
    * Period P = the median of the last 8 accepted intervals within 270-2000 ms, refined to the mean of the
      intervals within ±12 % of that median (bus frames arrive every ~25 ms, so single intervals are quantised;
      their mean is not).  Once P exists, an interval I is folded: k = round(I / P) <= 4, accepted as I / k
      when |I - kP| <= 0.12 P (a missed pulse doesn't corrupt P).  An interval across a timeout gap is not used.
    * Confidence = the share of the last 8 edges that landed within ±12 % of P of the grid ``anchor + kP``.  A
      hit pulls the anchor a quarter of the way towards the edge; a miss leaves the grid alone while locked
      and re-anchors on the edge while unlocked.
    * Re-fit (a tempo step or a phase jump): when an edge misses the grid but the last ``refit_edges`` (7)
      edges are evenly spaced among themselves (each interval within max(8 % of P', 30 ms) of 1 or 2 times
      their period P', P' within 270-2000 ms), the grid is re-fitted to them at once: P from their intervals
      (which replace the history), the anchor fitted to all 7 (least squares), and the hit history becomes those
      7 edges, all hits on the new grid (7/8: locked afresh, ``locked_since`` restarts).  It is the lock rule
      applied to a grid fitted to the edges themselves, so a step re-locks on about its 7th beat instead of its
      ~16th (4 misses to unlock, 5 new intervals to flip the median, then 6 of 8 hits).  Random edges lock no
      more often than before (7 in a row on one grid, at the tighter tolerance).
    * Locks at confidence >= 0.7 with >= 4 accepted edges; unlocks below 0.6, or when no edge has been
      accepted for max(2.5 P, 1.5 s).  That timeout also clears the hit history (P and the grid are kept), so
      edges that come back after a breakdown must earn the lock again (6 hits) rather than inherit it.
    """

    def __init__(self, *, rise: float = 0.6, fall: float = 0.3, floor: float = 0.1, peak_tau_s: float = 4.0,
                 window: int = 8, min_ms: float = 270.0, max_ms: float = 2000.0, tolerance: float = 0.12,
                 lock_at: float = 0.7, unlock_below: float = 0.6, min_edges: int = 4, pull: float = 0.25,
                 refit_edges: int = 7, refit_fold: int = 2, refit_tolerance: float = 0.08,
                 refit_floor_ms: float = 30.0):
        self.rise, self.fall, self.floor, self.peak_tau = rise, fall, floor, peak_tau_s
        self.window = window
        self.min_s, self.max_s = min_ms / 1000.0, max_ms / 1000.0
        self.tolerance, self.lock_at, self.unlock_below = tolerance, lock_at, unlock_below
        self.min_edges, self.pull = min_edges, pull
        self.refit_edges, self.refit_fold = refit_edges, refit_fold
        self.refit_tolerance, self.refit_floor_s = refit_tolerance, refit_floor_ms / 1000.0
        self.intervals: collections.deque[float] = collections.deque(maxlen=window)
        self.hits: collections.deque[bool] = collections.deque(maxlen=window)
        self.recent: collections.deque[float] = collections.deque(maxlen=refit_edges)   # edge times, for a re-fit
        self.armed = True
        self.peak = 0.0
        self.t_prev: Optional[float] = None
        self.last_edge: Optional[float] = None
        self.anchor: Optional[float] = None
        self.period: Optional[float] = None
        self.is_locked = False
        self.accepted = 0
        self.rejected = 0
        self.refits = 0
        self.locked_since: Optional[float] = None

    @property
    def confidence(self) -> float:
        return sum(self.hits) / self.window

    def timeout(self) -> float:
        return max(2.5 * self.period, 1.5) if self.period else 1.5

    def locked(self, now: float) -> bool:
        return self.is_locked and self.last_edge is not None and now - self.last_edge <= self.timeout()

    def feed(self, t: float, value: float) -> bool:
        """One input sample at time t (s).  Returns True if it was an accepted beat edge."""
        dt = 0.0 if self.t_prev is None else min(0.1, max(0.0, t - self.t_prev))
        self.t_prev = t
        if (self.is_locked or self.hits) and self.last_edge is not None and t - self.last_edge > self.timeout():
            # timed out (no edge for max(2.5 P, 1.5 s)): unlocked, and the hit history is stale
            self.is_locked, self.locked_since = False, None
            self.hits.clear()
            self.recent.clear()
        self.peak = max(value, self.peak * math.exp(-dt / self.peak_tau))
        x = value / max(self.peak, self.floor)
        if x <= self.fall:
            self.armed = True
            return False
        if not self.armed or x < self.rise:
            return False
        self.armed = False
        te = t - 0.5 * dt
        if self.last_edge is not None:
            refractory = 0.35 * self.period if self.is_locked and self.period else 0.150
            if te - self.last_edge < refractory:
                self.rejected += 1
                return False
        self._edge(te)
        return True

    def _fold(self, interval: float) -> Optional[float]:
        if self.period is None:
            return interval if self.min_s <= interval <= self.max_s else None
        k = round(interval / self.period)
        if 1 <= k <= 4 and abs(interval - k * self.period) <= self.tolerance * self.period:
            folded = interval / k
            return folded if self.min_s <= folded <= self.max_s else None
        return interval if self.min_s <= interval <= self.max_s and not self.is_locked else None

    def _edge(self, t: float) -> None:
        self.accepted += 1
        hit, pred = False, None
        if self.period is not None and self.anchor is not None:
            k = max(1, round((t - self.anchor) / self.period))
            pred = self.anchor + k * self.period
            hit = abs(t - pred) <= self.tolerance * self.period
        gap = self.last_edge is None or t - self.last_edge > self.timeout()
        if not gap:                                   # an interval across a timeout gap means nothing
            iv = self._fold(t - self.last_edge)
            if iv is not None:
                self.intervals.append(iv)
        self.last_edge = t
        self.hits.append(hit)
        if gap:
            self.recent.clear()
        self.recent.append(t)
        if not (pred is not None and not hit and self._refit()):
            self._update_period()
            if hit:
                self.anchor = pred + self.pull * (t - pred)
            elif not self.is_locked:
                self.anchor = t
        conf = self.confidence
        if not self.is_locked and conf >= self.lock_at and self.accepted >= self.min_edges and self.period:
            self.is_locked, self.locked_since = True, t
        elif self.is_locked and conf < self.unlock_below:
            self.is_locked, self.locked_since = False, None

    def _update_period(self) -> None:
        if self.intervals:
            med = statistics.median(self.intervals)
            inliers = [i for i in self.intervals if abs(i - med) <= self.tolerance * med]
            self.period = sum(inliers) / len(inliers) if inliers else med

    def _refit(self) -> bool:
        """The latest edge missed the grid: if the last ``refit_edges`` edges are evenly spaced (1 to
        ``refit_fold`` beats apart), re-fit the grid to them (intervals, period, anchor, hit history).
        -> whether it did."""
        e = list(self.recent)
        if len(e) < self.refit_edges:
            return False
        d = [b - a for a, b in zip(e, e[1:])]
        base = min(d)
        if base <= 0:
            return False
        ks = [round(x / base) for x in d]
        if max(ks) > self.refit_fold:
            return False
        p = (e[-1] - e[0]) / sum(ks)
        tol = max(self.refit_tolerance * p, self.refit_floor_s)
        if not self.min_s <= p <= self.max_s or any(abs(x - k * p) > tol for x, k in zip(d, ks)):
            return False
        self.intervals.clear()
        self.intervals.extend(x / k for x, k in zip(d, ks))
        self._update_period()
        beat = [0]
        for k in ks:
            beat.append(beat[-1] + k)
        offset = sum(x - b * self.period for x, b in zip(e, beat)) / len(e)    # least squares, P fixed
        self.anchor = offset + beat[-1] * self.period
        self.hits.clear()
        self.hits.extend([True] * len(e))
        self.is_locked = False                        # a new grid: the caller locks it afresh (locked_since = now)
        self.refits += 1
        return True


@dataclass(frozen=True)
class Snapshot:
    """One fixture's state after a bus frame (immutable: published by reference)."""
    t: float                      # the frame's bus time (s, CLOCK_MONOTONIC)
    arrival: float                # when it arrived (s, time.monotonic())
    raw: dict                     # rgb [r, g, b], level, pulse, pan, tilt, zoom, effect, variant, activity
    slow: dict                    # activity_slow, level_slow, rgb_slow [r, g, b]
    fresh_input: bool             # input.online and feed == live
    anchor: Optional[float]
    period: Optional[float]
    confidence: float
    last_edge: Optional[float]
    is_locked: bool
    timeout: float
    stable: dict = field(default_factory=dict)      # follow_dmx attr -> its stable value (>= 300 ms)
    follower: dict = field(default_factory=dict)    # counters for /inputs

    def locked(self, now: float) -> bool:
        return self.is_locked and self.last_edge is not None and now - self.last_edge <= self.timeout


class FixtureSignals:
    """Smoothing, follow_dmx debouncing and the beat follower for one fixture (loop thread)."""

    def __init__(self, name: str, *, edge_from: str = "pulse"):
        self.name = name
        self.edge_from = edge_from
        self.follower = BeatFollower()
        self.t: Optional[float] = None
        self.slow: dict = {}
        self.stable: dict = {}                # attr -> stable value
        self.candidate: dict = {}             # attr -> (value, since)
        self.snapshot: Optional[Snapshot] = None

    def update(self, t: float, arrival: float, fx: dict, activity: Any, fresh_input: bool = True) -> None:
        rgb = fx.get("rgb")
        rgb = [_clamp01(_num(c)) for c in rgb] if isinstance(rgb, list) and len(rgb) == 3 else [0.0, 0.0, 0.0]
        raw = {"rgb": rgb, "level": _clamp01(_num(fx.get("level"))), "pulse": _clamp01(_num(fx.get("pulse"))),
               "pan": _num(fx.get("pan")), "tilt": _num(fx.get("tilt")), "zoom": _clamp01(_num(fx.get("zoom"))),
               "effect": fx.get("effect"), "variant": fx.get("variant"), "activity": _clamp01(_num(activity))}
        dt = 0.0 if self.t is None else min(1.0, max(0.0, t - self.t))
        self.t = t
        if not self.slow:
            self.slow = {"activity_slow": raw["activity"], "level_slow": raw["level"], "rgb_slow": list(rgb)}
        else:
            aa, al, ar = (1.0 - math.exp(-dt / tau) for tau in (TAU_ACTIVITY_S, TAU_LEVEL_S, TAU_RGB_S))
            s = self.slow
            self.slow = {"activity_slow": s["activity_slow"] + (raw["activity"] - s["activity_slow"]) * aa,
                         "level_slow": s["level_slow"] + (raw["level"] - s["level_slow"]) * al,
                         "rgb_slow": [a + (b - a) * ar for a, b in zip(s["rgb_slow"], rgb)]}
        if fresh_input:                       # follow_dmx compares fresh frames only
            for attr in FOLLOW_ATTRS:
                v = raw[attr]
                if attr not in self.stable:
                    self.stable[attr] = v
                    self.candidate[attr] = (v, t)
                    continue
                cv, since = self.candidate[attr]
                if v != cv:
                    self.candidate[attr] = (v, t)
                elif v != self.stable[attr] and t - since >= FOLLOW_STABLE_S:
                    self.stable[attr] = v
        f = self.follower
        f.feed(t, raw.get(self.edge_from, 0.0))
        self.snapshot = Snapshot(
            t, arrival, raw, self.slow, fresh_input, f.anchor, f.period, f.confidence, f.last_edge, f.is_locked,
            f.timeout(), dict(self.stable),
            {"edges_accepted": f.accepted, "edges_rejected": f.rejected, "refits": f.refits,
             "locked_since": f.locked_since, "pulse_peak": round(f.peak, 4)})


class ShowInputs:
    """The show bus as the renderer sees it: connection state and one FixtureSignals per fixture."""

    def __init__(self, url: Optional[str] = None):
        self.url = url
        self.connected = False
        self.welcome: Optional[dict] = None
        self.fixtures: dict[str, FixtureSignals] = {}
        self.frames = 0
        self.last_arrival: Optional[float] = None
        self.last_seq: Optional[int] = None

    @property
    def configured(self) -> bool:
        return self.url is not None

    def fixture(self, name: str) -> FixtureSignals:
        fs = self.fixtures.get(name)
        if fs is None:
            fs = self.fixtures[name] = FixtureSignals(name)
        return fs

    def on_connected(self, welcome: dict) -> None:
        self.connected = True
        self.welcome = welcome

    def on_disconnected(self) -> None:
        self.connected = False

    def on_frame(self, frame: dict, arrival: Optional[float] = None) -> None:
        arrival = time.monotonic() if arrival is None else arrival
        t = _num(frame.get("t_ms"), arrival * 1000.0) / 1000.0
        if abs(t - arrival) > 5.0:       # not CLOCK_MONOTONIC on this host after all: use the arrival time
            t = arrival
        fixtures = frame.get("fixtures")
        if not isinstance(fixtures, dict):
            return
        inp = frame.get("input") or {}
        feed = frame.get("feed", frame.get("look"))   # K3 as amended renames the top-level `look` to `feed`
        fresh = inp.get("online", True) is not False and feed in (None, "live")
        activity = (frame.get("global") or {}).get("activity")
        for name, fx in fixtures.items():
            if isinstance(fx, dict):
                self.fixture(str(name)).update(t, arrival, fx, activity, fresh)
        self.frames += 1
        self.last_arrival = arrival
        self.last_seq = frame.get("seq")

    def status(self) -> dict:
        age = None if self.last_arrival is None else round((time.monotonic() - self.last_arrival) * 1000, 1)
        return {"configured": self.configured, "connected": self.connected, "frames": self.frames,
                "age_ms": age, "fixtures": sorted(self.fixtures),
                "source": (self.welcome or {}).get("source")}


class Sampler:
    """A session's view of its fixture.  :meth:`sample` (render thread only) advances the liveness weight and
    the held phase; :meth:`peek` (the API) computes the same values without changing anything.

    ``beat`` and ``beat_phase`` are predicted at ``now + lead`` when the follower is locked, so steady beats
    land on glass on time; everything else is the latest value (it lands ``lead`` late)."""

    def __init__(self, inputs: Optional[ShowInputs], fixture: str, lead_s: float, follow: str = "extrapolate"):
        self.inputs = inputs
        self.fixture_name = fixture
        self.lead = lead_s                # seconds; written by the loop (1 Hz, slewed), read here
        self.follow = follow
        self.held_phase = 0.0
        self.w = 0.0                      # liveness weight: 0 idle .. 1 live values
        self.stale: Optional[str] = "link"
        self._fresh_for = 0.0
        self._stale_for = 0.0
        self._t: Optional[float] = None

    def snapshot(self) -> Optional[Snapshot]:
        if self.inputs is None:
            return None
        fs = self.inputs.fixtures.get(self.fixture_name)
        return None if fs is None else fs.snapshot

    def freshness(self, now: float, snap: Optional[Snapshot]) -> Optional[str]:
        """None when fresh, else "link" (no frame for 500 ms / socket closed) or "input" (input not live)."""
        if snap is None or self.inputs is None or not self.inputs.connected or now - snap.arrival > LINK_TIMEOUT_S:
            return "link"
        return None if snap.fresh_input else "input"

    def beat_grid(self, now: float) -> Optional[tuple[float, float]]:
        """(anchor, period) when the follower is locked and following, else None."""
        snap = self.snapshot()
        if snap is None or self.follow != "extrapolate" or not snap.locked(now):
            return None
        return snap.anchor, snap.period

    def _liveness(self, now: float, snap: Optional[Snapshot]) -> None:
        dt = 0.0 if self._t is None else min(0.5, max(0.0, now - self._t))
        self._t = now
        self.stale = self.freshness(now, snap)
        if self.stale is None:
            self._stale_for = 0.0
            self._fresh_for += dt
            if self._fresh_for >= RECOVER_AFTER_S:
                self.w = min(1.0, self.w + dt / RECOVER_EASE_S)
        else:
            self._fresh_for = 0.0
            self._stale_for += dt
            if self._stale_for > STALE_HOLD_S:
                self.w = max(0.0, self.w - dt / STALE_EASE_S)

    def _values(self, now: float, snap: Snapshot, w: float, held_phase: float) -> tuple[dict, float]:
        raw, slow = snap.raw, snap.slow
        live = {"level": raw["level"], "pulse": raw["pulse"], "pan": _clamp01(0.5 + 0.5 * raw["pan"]),
                "tilt": _clamp01(0.5 + 0.5 * raw["tilt"]), "zoom": raw["zoom"], "activity": raw["activity"],
                "activity_slow": slow["activity_slow"], "level_slow": slow["level_slow"],
                "beat_confidence": snap.confidence}
        locked = snap.locked(now)
        phase = held_phase
        if self.follow == "extrapolate" and locked:
            t = now + self.lead
            k = math.floor((t - snap.anchor) / snap.period)
            beat = math.exp(-(t - (snap.anchor + k * snap.period)) / BEAT_DECAY_S)
            phase = (t - snap.anchor) / snap.period - k
        else:  # unlocked or off: the raw edge envelope, unpredicted (it lands on glass `lead` late)
            beat = math.exp(-(now - snap.last_edge) / BEAT_DECAY_S) if snap.last_edge and now >= snap.last_edge \
                else 0.0
        live["beat"] = beat
        out = {k: idle_value(k) + w * (v - idle_value(k)) for k, v in live.items()}
        out["beat_phase"] = phase
        out["bpm"] = 60.0 / snap.period if locked and snap.period and self.follow == "extrapolate" else None
        out["beat_locked"] = locked
        return out, phase

    def sample(self, now: float) -> dict:
        """Render thread, once per frame: -> {signal: value} (idle values with no bus data)."""
        snap = self.snapshot()
        self._liveness(now, snap)
        if snap is None:
            return {s: idle_value(s) for s in BIND_SIGNALS if s != "beat_phase"} | {
                "beat_phase": self.held_phase, "bpm": None, "beat_locked": False}
        out, self.held_phase = self._values(now, snap, self.w, self.held_phase)
        return out

    def peek(self, now: float) -> dict:
        """The API's view (loop thread): like sample(), without advancing any state."""
        snap = self.snapshot()
        if snap is None:
            return {s: idle_value(s) for s in BIND_SIGNALS if s != "beat_phase"} | {
                "beat_phase": self.held_phase, "bpm": None, "beat_locked": False}
        return self._values(now, snap, self.w, self.held_phase)[0]
