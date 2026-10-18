"""Show-bus inputs: per-fixture signals, smoothing, liveness and the beat follower (LIVE_V1 1.1 §1.9).

The renderer does no DSP of its own (owner ruling, AV_PROGRAMME A.1.6): no section, mood or genre detection.
Signals are the bus's raw latest values plus one-pole smoothing; the one exception is the beat follower, a
metronome follower used only to compensate the render->photon latency.

Bindable signals are scalars 0-1 (``BIND_SIGNALS``; ``rgb`` is not bindable in v1):
  raw       level, pulse, pan, tilt, zoom ((x + 1) / 2 for pan and tilt), activity (bus global)
  smoothed  activity_slow (tau 2 s), level_slow (tau 1 s)
  follower  beat: 1 at each predicted beat, decaying (unlocked or off: the raw pulse-edge envelope, which
            lands ``lead`` late); beat_phase 0-1 (held when unlocked); beat_confidence
Derived from Maestro's channels for the per-item mappings (binding.py; owner, 15 Oct 2026):
  movers    pan_slow, tilt_slow (tau 0.5 s; a jump over 0.25 in one frame, a "teleport" at a section change,
            eases over 2 s instead), zoom_slow (tau 1 s; n/a until ZOOM moves), motion (how fast PAN/TILT
            sweep, tau 2 s; n/a after 30 s without movement)
  colour    sat (saturation of lit frames, tau 3 s), warmth (0.5 + 0.5 sat cos(hue - 30 deg)); n/a after 30 s
            unlit.  Not bindable, for the palette fit: pal_hue, pal_spread (weighted circular mean / spread of
            the lit hue, tau 6 s)
  section   a trigger: 1 at each stable GOBO change, decaying (tau 1.5 s); ``gobo`` (the stable GOBO value)
            seeds section-mode bindings
  music     energy, energy_slow, drive, punch, tempo: music_direction.py's features, merged in by the session
            (modulation.py); while show_service's features arrive, energy follows theirs closely (music_feed.py)
  piano     note_on, chord_change (triggers), note_rate, velocity, register, spread, pedal, tension: piano.py, from
            the piano's notes (Session.sound), merged in by the session too
An n/a signal is None; a binding drops it and renormalises the rest.
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

from .piano import PIANO_LEVELS, PIANO_SIGNALS, PIANO_TRIGGERS

TRIGGERS = ("pulse", "beat", "section", "downbeat") + PIANO_TRIGGERS
MUSIC_SIGNALS = ("energy", "energy_slow", "drive", "punch", "tempo")   # music_direction.py's (the session merges)
CLOCK_SIGNALS = ("bar_phase", "phrase_phase", "downbeat")   # show_service's music.clock only (music_feed.py)
FEED_SIGNALS = ("bass", "mids", "highs", "brightness", "density", "groove", "arousal")   # show_service's, smoothed
PHASES = ("beat_phase", "bar_phase", "phrase_phase")
DERIVED_SIGNALS = ("pan_slow", "tilt_slow", "zoom_slow", "motion", "sat", "warmth", "section")
BIND_SIGNALS = ("level", "pulse", "pan", "tilt", "zoom", "activity", "activity_slow", "level_slow",
                "beat", "beat_phase", "beat_confidence") + DERIVED_SIGNALS + MUSIC_SIGNALS + PIANO_SIGNALS \
    + CLOCK_SIGNALS + FEED_SIGNALS
NA_SIGNALS = ("zoom_slow", "motion", "sat", "warmth") + MUSIC_SIGNALS + PIANO_LEVELS + CLOCK_SIGNALS \
    + FEED_SIGNALS   # may be None (n/a)
POSITIONAL = ("pan", "tilt", "zoom", "pan_slow", "tilt_slow", "zoom_slow")   # DMX positions: never re-centred
PIVOT_SIGNALS = ("energy", "energy_slow", "drive", "punch", "tempo", "activity", "activity_slow") + FEED_SIGNALS \
    + ("motion",)              # the adaptive pivot's defaults (music_feed.Response.pivot_signals; modulation.Pivots)
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
TAU_PAN_S = 0.5
TELEPORT = 0.25            # a pan/tilt step this big in one frame is a jump, not movement
TELEPORT_TAU_S, TELEPORT_HOLD_S = 2.0, 2.0
TAU_ZOOM_S = 1.0
TAU_MOTION_S = 2.0
MOTION_FULL = 1.5          # pan/tilt sweep speed (0-1 units per second) that reads as motion 1
MOVED = 0.02               # a pan/tilt/zoom change this big counts as movement
MOTION_NA_S, ZOOM_NA_S, COLOUR_NA_S = 30.0, 300.0, 30.0
TAU_SAT_S, TAU_HUE_S = 3.0, 6.0
LIT_V = 0.05
SECTION_DECAY_S = 1.5
DEFAULT_PHOTON_MS = 178.7  # presented -> photon when latency.json is absent (south, 13 Oct)
DEFAULT_FIXTURES = {"north-screen": "screen.north", "south-screen": "screen.south", "devtest": "screen.north"}


def signal_kind(s: str) -> str:
    return "trigger" if s in TRIGGERS else "phase" if s in PHASES else "level"


def signal_catalog() -> dict:
    """The catalog's ``signals`` block: {name: {kind, idle, na?}}."""
    return {s: {"kind": signal_kind(s), "idle": 0.0 if s in TRIGGERS else None if s in PHASES else 0.5,
                **({"na": True} if s in NA_SIGNALS else {}), **({"from": "music"} if s in MUSIC_SIGNALS else {}),
                **({"from": "clock"} if s in CLOCK_SIGNALS else {}), **({"from": "show"} if s in FEED_SIGNALS else {}),
                **({"from": "piano"} if s in PIANO_SIGNALS else {})}
            for s in BIND_SIGNALS}


def idle_value(signal: str) -> float:
    return 0.0 if signal in TRIGGERS else 0.5


def pivotable(signal: str) -> bool:
    """A level the adaptive pivot may centre on its own norm (modulation.Pivots): never a trigger, a phase or a DMX
    position."""
    return signal in BIND_SIGNALS and signal_kind(signal) == "level" and signal not in POSITIONAL


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
    * :meth:`edge` takes edges from an onset detector instead (the beat ladder's onset follower, music_feed.py:
      show_service's strong onsets at their acoustic time), skipping the Schmitt stage, with an optional period
      ``hint`` (the show clock's, octave-folded, while its tempo is clear).  With a hint P is the hint's, trimmed by
      the grid's own residuals (an alpha-beta loop: each hit moves the trim by pull^2 / (2 - pull) of its residual
      per beat, bounded to +-``trim``: at the bound it is ``pinned``), so 25 ms-quantised, jittery edges never bias
      the period the grid is extrapolated on, and a hint a little off the music still converges on its phase.  A hint
      arriving on a grid of another period (fitted without one, or another octave) drops the hit history: the lock is
      earned again on the hint's grid; on a grid of its period, the anchor moves by the recent edges' median residual
      on the hint's grid (the trim starts from a fitted phase).  A re-fit counts edges in hint beats (1 to
      ``refit_fold_hint`` apart) and sets the trim to its fitted period.  The grid, refractory, lock and timeout are
      the same.
    """

    def __init__(self, *, rise: float = 0.6, fall: float = 0.3, floor: float = 0.1, peak_tau_s: float = 4.0,
                 window: int = 8, min_ms: float = 270.0, max_ms: float = 2000.0, tolerance: float = 0.12,
                 lock_at: float = 0.7, unlock_below: float = 0.6, min_edges: int = 4, pull: float = 0.25,
                 refit_edges: int = 7, refit_fold: int = 2, refit_tolerance: float = 0.08,
                 refit_floor_ms: float = 30.0, refit_fold_hint: int = 4, trim: float = 0.05):
        self.rise, self.fall, self.floor, self.peak_tau = rise, fall, floor, peak_tau_s
        self.window = window
        self.min_s, self.max_s = min_ms / 1000.0, max_ms / 1000.0
        self.tolerance, self.lock_at, self.unlock_below = tolerance, lock_at, unlock_below
        self.min_edges, self.pull = min_edges, pull
        self.refit_edges, self.refit_fold = refit_edges, refit_fold
        self.refit_tolerance, self.refit_floor_s = refit_tolerance, refit_floor_ms / 1000.0
        self.refit_fold_hint, self.trim = refit_fold_hint, trim
        self.trim_gain = pull * pull / (2.0 - pull)  # the alpha-beta loop's beta for alpha = pull (Benedict-Bordner)
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
        self.hint: Optional[float] = None         # edge(): the period to hold to (the show clock's beat), or None
        self.trim_c = 0.0                         # edge(): the hint's trim (relative, within +-trim)
        self.strength = 0.0                       # edge(): the last accepted edge's strength (the inputs API)

    @property
    def confidence(self) -> float:
        return sum(self.hits) / self.window

    @property
    def pinned(self) -> bool:
        """edge(): the trim is at its bound (the hint is further off the music than ``trim``: the grid drifts)."""
        return self.hint is not None and abs(self.trim_c) >= self.trim - 1e-9

    def timeout(self) -> float:
        return max(2.5 * self.period, 1.5) if self.period else 1.5

    def locked(self, now: float) -> bool:
        return self.is_locked and self.last_edge is not None and now - self.last_edge <= self.timeout()

    def feed(self, t: float, value: float) -> bool:
        """One input sample at time t (s).  Returns True if it was an accepted beat edge."""
        dt = 0.0 if self.t_prev is None else min(0.1, max(0.0, t - self.t_prev))
        self.t_prev = t
        self._expire(t)
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

    def edge(self, t: float, strength: float = 1.0, hint: Optional[float] = None) -> bool:
        """One edge from an onset detector at time t (s), its strength 0-1: no Schmitt stage.  ``hint``: the period
        (s) to hold to (the show clock's beat; None or out of 270-2000 ms: the follower's own).  Returns True if it
        was accepted (not inside the refractory period, nor out of order)."""
        h = hint if hint is not None and self.min_s <= hint <= self.max_s else None
        self._expire(t)
        if h is not None and self.hint is None:
            self.trim_c = 0.0                         # a new stretch on the clock's period: trimmed afresh ...
            if self.anchor is not None and self.period is not None and abs(self.period - h) <= self.tolerance * h:
                # ... from a phase fitted to the recent edges on its grid (the median residual within +-P/4), so the
                # anchor's drift on the old period never pumps the trim
                rs = [r for e in self.recent
                      for r in (e - self.anchor - round((e - self.anchor) / h) * h,) if abs(r) <= 0.25 * h]
                if rs:
                    self.anchor += statistics.median(rs)
        if h is not None and self.period is not None and abs(self.period - h) > self.tolerance * h:
            # a grid on another period (fitted with no hint, or another octave): its phase is not the hint's beat
            self.is_locked, self.locked_since = False, None
            self.hits.clear()
            self.recent.clear()
            self.intervals.clear()
            self.period = h
        self.hint = h
        if self.last_edge is not None:
            refractory = 0.35 * self.period if self.is_locked and self.period else 0.150
            if t - self.last_edge < refractory:
                self.rejected += 1
                return False
        if self.period is None and self.hint is not None:
            self.period = self.hint                   # the first interval folds on the clock's beat
        self.t_prev, self.strength = t, strength
        self._edge(t)
        return True

    def _expire(self, t: float) -> None:
        if (self.is_locked or self.hits) and self.last_edge is not None and t - self.last_edge > self.timeout():
            # timed out (no edge for max(2.5 P, 1.5 s)): unlocked, and the hit history is stale
            self.is_locked, self.locked_since = False, None
            self.hits.clear()
            self.recent.clear()

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
        hit, pred, k = False, None, 1
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
            if hit and self.hint is not None:         # edge(): the residual trims the hint (alpha-beta, bounded)
                c = self.trim_c + self.trim_gain * (t - pred) / (k * self.hint)
                self.trim_c = -self.trim if c < -self.trim else self.trim if c > self.trim else c
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
        if self.hint is not None:                     # edge(): the clock's period, trimmed by the grid's residuals
            self.period = self.hint * (1.0 + self.trim_c)

    def _refit(self) -> bool:
        """The latest edge missed the grid: if the last ``refit_edges`` edges are evenly spaced (1 to
        ``refit_fold`` beats apart; with a hint, 1 to ``refit_fold_hint`` hint beats and within ``tolerance`` of
        it), re-fit the grid to them (intervals, period, anchor, hit history).  -> whether it did."""
        e = list(self.recent)
        if len(e) < self.refit_edges:
            return False
        d = [b - a for a, b in zip(e, e[1:])]
        h = self.hint
        base = min(d) if h is None else h
        if base <= 0:
            return False
        ks = [round(x / base) for x in d]
        if min(ks) < 1 or max(ks) > (self.refit_fold if h is None else self.refit_fold_hint):
            return False
        p = (e[-1] - e[0]) / sum(ks)
        tol = max(self.refit_tolerance * p, self.refit_floor_s)
        if not self.min_s <= p <= self.max_s or any(abs(x - k * p) > tol for x, k in zip(d, ks)):
            return False
        if h is not None and abs(p - h) > self.tolerance * h:
            return False
        if h is not None:                             # the fitted period, as the hint's trim (bounded)
            self.trim_c = max(-self.trim, min(self.trim, p / h - 1.0))
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
    ext: dict = field(default_factory=dict)         # derived signals (pan_slow ... section_at): see the docstring

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
        self.ext: dict = {}                   # derived signals' state (_derive)
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
                    if attr == "effect":
                        self.ext["section_at"] = t        # GOBO steps on Maestro's section changes
        self._derive(t, dt, raw)
        f = self.follower
        f.feed(t, raw.get(self.edge_from, 0.0))
        self.snapshot = Snapshot(
            t, arrival, raw, self.slow, fresh_input, f.anchor, f.period, f.confidence, f.last_edge, f.is_locked,
            f.timeout(), dict(self.stable),
            {"edges_accepted": f.accepted, "edges_rejected": f.rejected, "refits": f.refits,
             "locked_since": f.locked_since, "pulse_peak": round(f.peak, 4)},
            dict(self.ext))

    def _derive(self, t: float, dt: float, raw: dict) -> None:
        """pan_slow, tilt_slow, zoom_slow, motion, sat, the lit hue's mean and spread (loop thread)."""
        x = self.ext
        pan, tilt, zoom = _clamp01(0.5 + 0.5 * raw["pan"]), _clamp01(0.5 + 0.5 * raw["tilt"]), raw["zoom"]
        if "pan" not in x:
            x.update(pan=pan, tilt=tilt, pan_slow=pan, tilt_slow=tilt, zoom_slow=zoom, motion=0.0, ref=(pan, tilt),
                     zoom_ref=zoom, moved_t=None, zoom_t=None, tele_until=-1e9, lit_t=None, sat=None,
                     hx=0.0, hy=0.0, hw=0.0)
        else:
            step = math.hypot(pan - x["pan"], tilt - x["tilt"])
            if step > TELEPORT:
                x["tele_until"] = t + TELEPORT_HOLD_S
            if dt > 0:
                a = 1.0 - math.exp(-dt / (TELEPORT_TAU_S if t < x["tele_until"] else TAU_PAN_S))
                x["pan_slow"] += (pan - x["pan_slow"]) * a
                x["tilt_slow"] += (tilt - x["tilt_slow"]) * a
                speed = 0.0 if step > TELEPORT else step / dt
                x["motion"] += (min(1.0, speed / MOTION_FULL) - x["motion"]) * (1.0 - math.exp(-dt / TAU_MOTION_S))
                x["zoom_slow"] += (zoom - x["zoom_slow"]) * (1.0 - math.exp(-dt / TAU_ZOOM_S))
            if math.hypot(pan - x["ref"][0], tilt - x["ref"][1]) > MOVED:
                x["ref"], x["moved_t"] = (pan, tilt), t
            if abs(zoom - x["zoom_ref"]) > MOVED:
                x["zoom_ref"], x["zoom_t"] = zoom, t
            x["pan"], x["tilt"] = pan, tilt
        r, g, b = raw["rgb"]
        v = max(r, g, b)
        if v > LIT_V:
            s = (v - min(r, g, b)) / v
            h = _hue(r, g, b, v)
            wt = s * v
            hx, hy = wt * math.cos(2 * math.pi * h), wt * math.sin(2 * math.pi * h)
            if x["sat"] is None or x["lit_t"] is None:
                x.update(sat=s, hx=hx, hy=hy, hw=wt)
            elif dt > 0:
                a_s, a_h = 1.0 - math.exp(-dt / TAU_SAT_S), 1.0 - math.exp(-dt / TAU_HUE_S)
                x["sat"] += (s - x["sat"]) * a_s
                x["hx"] += (hx - x["hx"]) * a_h
                x["hy"] += (hy - x["hy"]) * a_h
                x["hw"] += (wt - x["hw"]) * a_h
            x["lit_t"] = t


def _hue(r: float, g: float, b: float, v: float) -> float:
    """HSV hue 0-1 (0 red, 1/3 green, 2/3 blue) of a lit colour (v = max(r, g, b) > 0)."""
    c = v - min(r, g, b)
    if c <= 1e-9:
        return 0.0
    if v == r:
        h = ((g - b) / c) % 6.0
    elif v == g:
        h = (b - r) / c + 2.0
    else:
        h = (r - g) / c + 4.0
    return (h / 6.0) % 1.0


def colour_stats(x: dict) -> Optional[tuple[float, float, float]]:
    """(pal_hue 0-1, pal_spread turns 0.02-0.25, sat) from FixtureSignals.ext, or None before a lit frame."""
    if x.get("sat") is None or not x.get("hw"):
        return None
    hue = (math.atan2(x["hy"], x["hx"]) / (2 * math.pi)) % 1.0
    rr = min(1.0, math.hypot(x["hx"], x["hy"]) / x["hw"])
    spread = math.sqrt(-2.0 * math.log(max(rr, 1e-6))) / (2 * math.pi)
    return hue, min(0.25, max(0.02, spread)), x["sat"]


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
        self.taps: list = []             # f(kind, *args): the render workers' copies (workers.py), fed from here

    def _tap(self, *msg) -> None:
        for f in self.taps:
            try:
                f(*msg)
            except Exception:
                log.exception("show bus tap failed")

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
        if self.taps:
            self._tap("show_up", welcome)

    def on_disconnected(self) -> None:
        self.connected = False
        if self.taps:
            self._tap("show_down", None)

    def on_frame(self, frame: dict, arrival: Optional[float] = None) -> None:
        arrival = time.monotonic() if arrival is None else arrival
        if self.taps:
            self._tap("show", frame, arrival)
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
        x = snap.ext
        if x:
            live["pan_slow"], live["tilt_slow"] = x["pan_slow"], x["tilt_slow"]
            live["zoom_slow"] = x["zoom_slow"] if x["zoom_t"] is not None and now - x["zoom_t"] <= ZOOM_NA_S else None
            live["motion"] = x["motion"] if x["moved_t"] is not None and now - x["moved_t"] <= MOTION_NA_S else None
            cs = colour_stats(x) if x["lit_t"] is not None and now - x["lit_t"] <= COLOUR_NA_S else None
            live["sat"] = None if cs is None else cs[2]
            live["warmth"] = None if cs is None else _clamp01(0.5 + 0.5 * cs[2] * math.cos(2 * math.pi * (cs[0] - 0.08)))
            sa = x.get("section_at")
            live["section"] = math.exp(-(now - sa) / SECTION_DECAY_S) if sa is not None and now >= sa else 0.0
        out = {k: (None if v is None else idle_value(k) + w * (v - idle_value(k))) for k, v in live.items()}
        if x:
            cs = colour_stats(x) if x["lit_t"] is not None and now - x["lit_t"] <= COLOUR_NA_S else None
            out["colour"] = None if cs is None else {"hue": cs[0], "spread": cs[1], "sat": cs[2]}
        out["gobo"] = snap.stable.get("effect")
        out["beat_phase"] = phase
        out["bpm"] = 60.0 / snap.period if locked and snap.period and self.follow == "extrapolate" else None
        out["beat_locked"] = locked
        return out, phase

    def sample(self, now: float) -> dict:
        """Render thread, once per frame: -> {signal: value} (idle values with no bus data)."""
        snap = self.snapshot()
        self._liveness(now, snap)
        if snap is None:
            return {s: idle_value(s) for s in BIND_SIGNALS
                    if s != "beat_phase" and s not in CLOCK_SIGNALS and s not in FEED_SIGNALS} | {
                "beat_phase": self.held_phase, "bpm": None, "beat_locked": False}
        out, self.held_phase = self._values(now, snap, self.w, self.held_phase)
        return out

    def peek(self, now: float) -> dict:
        """The API's view (loop thread): like sample(), without advancing any state."""
        snap = self.snapshot()
        if snap is None:
            return {s: idle_value(s) for s in BIND_SIGNALS
                    if s != "beat_phase" and s not in CLOCK_SIGNALS and s not in FEED_SIGNALS} | {
                "beat_phase": self.held_phase, "bpm": None, "beat_locked": False}
        return self._values(now, snap, self.w, self.held_phase)[0]
