"""Timeline: where a stream's samples, and a sender's clock, sit on our CLOCK_MONOTONIC (docs/SHOW_ENGINE.md §3.3,
§3.5). Pure arithmetic: no I/O, no threads, no numpy needed. "A sender's clock" is the one monotonic clock its process
stamps heartbeats and MIDI with (vban.py, "Clocks").

There is no playout buffer, so nothing waits for audio: each packet's samples are stamped by **sample index** and the
index is mapped to host time along the **lower envelope** of arrival times (Moon, Skelly & Towsley, "Estimation and
removal of clock skew from network delay measurements", 1999). Network jitter delays what we know; it never moves
when things happened.

  x_i = the sender's own clock in seconds since the segment began (n/fs for samples, t_ns/1e9 for a remote clock)
  d_i = (arrival_i - arrival_0) - x_i                     # transit time plus a constant: only late, never early
  1. running minimum of d per WINDOW_S (2 s) bucket of x;
  2. over the last FIT_S (60 s) of closed buckets, Moon's line: below every bucket minimum and closest to them in
     sum, i.e. the lower convex hull's edge under the points' mean x (robust to every delayed packet); the open
     bucket's minimum so far only lowers the intercept;
  3. slope = **skew** = host seconds per sender second - 1 (+ = the sender's clock runs slow against ours), clamped
     to +-SKEW_MAX_PPM (300). Until MIN_FIT_SPAN_S of data is in, the slope is the prior (0, or the old segment's
     with ``keep_skew``);
  4. to_host(x) = arrival_0 + a + (1 + skew) * x + acoustic_offset_ms / 1000.

What a mapped time means. Audio (SampleClock): when the sample reached the ADC, plus the sender's input buffering
beyond one packet (``in_lat - frames/fs``: a packet leaves at the capture callback, ``in_lat`` after its first
sample's ADC time; 0 when the sender's input latency is one packet), plus the smallest one-way transport delay (a
constant we can't see without a round trip). A remote clock (RemoteClock, MIDI stamped as sent): the stamp plus the
smallest transport delay. RemoteTimeMap bridges the two, so MIDI always lands on the audio map's constant. Then the
source's ``acoustic_offset_ms`` from config/room/latency.json is added (``sources.<stream>``; signed, + = the sound
reaches the listener later than the stream's timestamps). The click-track calibration (§5.5) measures end to end, so
it folds those constants into the offset. Mapped times are acoustic times: the beat clock anchors on them.

Segments. A map is valid only within one segment of continuous sample index. The receiver starts a new one
(:meth:`SampleClock.segment`) on a new sender ``sess``, a counter discontinuity, a frame-size change, an idle or
device switch, or a sender step (CaptureMap). The clocks also re-anchor themselves (reason ``"step"``, keeping the
skew) when:
  * an observation lands more than STEP_EARLY_S under the line (samples skipped, or the sender's clock jumped ahead);
  * the sender's clock goes back more than STEP_BACK_S (a reboot);
  * observations stay more than STEP_LATE_S over the line for STEP_PERSIST_S of sender time (it lost time and kept
    counting: a stalled capture). Points that late never feed the envelope;
  * the backstop for smaller losses: when a bucket closes, the minima of the last two closed buckets and the new
    bucket's first point all sit more than max(BACKSTOP_MIN_S (5 ms), 3 x jitter) over the line. A sender that lost
    capture time under STEP_LATE_S re-anchors 4-6 s later (two to three windows) instead of mapping everything early
    by the loss until the old buckets age out. A lasting rise in the smallest network delay looks the same and
    re-anchors too, which is right: the map's constant is the smallest delay. Points that come back early pull the
    line down at once.
A powerline stall that delivers everything late in a burst is none of these: a few seconds of late packets, then the
envelope again. A sender with heartbeats is caught sooner, by its CaptureMap (two pairs off the line, 0.5-1 s).

Alarm (``skew_alarm``, the watchdog on both clocks, §3.3): set by a fit beyond +-SKEW_MAX_PPM over at least
ALARM_MIN_SPAN_S of data, or by STEPS_ALARM (2) self-steps within STEPS_WINDOW_S (60 s). It latches across steps,
with the raw slope, and only a within-limit fit over at least ALARM_MIN_SPAN_S clears it, so a gross host-clock fault
(the old WSL 2 %) holds the alarm instead of flapping with every re-anchor. A skew beyond the clamp that runs over
the line (+) re-anchors through the backstop each time its drift passes the tolerance, which bounds the map's error.

Also kept per clock: RFC 3550's interarrival jitter (``jitter_ms``; the receiver's gap hold is clamp(3 x jitter, 4,
40 ms)) and how late the last packet was against the envelope (``late_ms``).

Ownership: one thread owns each clock (show_rt's receive thread). Nothing here locks; :meth:`state` returns a plain
dict to publish elsewhere.

Classes:
  LowerEnvelope   the estimator on (x, d) points
  SampleClock     a stream's sample index n -> host seconds (and back)
  RemoteClock     a sender's clock ns -> host seconds, from packets stamped on it (MIDI)
  CaptureMap      a sender's ns <-> one audio stream's sample index, from heartbeat (n, t_cap_ns) pairs; says when
                  the sender stepped (lost or skipped capture time while counting)
  RemoteTimeMap   MIDI t_ns -> host seconds: through the audio stream's map when it has one (notes and sound on
                  one timeline), else through its own RemoteClock plus the bridge measured while it had one
"""

from __future__ import annotations

import math
from collections import deque
from typing import Optional

WINDOW_S = 2.0
FIT_S = 60.0
MIN_FIT_SPAN_S = 4.0
ALARM_MIN_SPAN_S = 20.0
SKEW_MAX_PPM = 300.0
STEP_EARLY_S = 0.1
STEP_LATE_S = 0.5
STEP_PERSIST_S = 10.0
STEP_BACK_S = 1.0
BACKSTOP_MIN_S = 0.005                       # buckets this far (or 3 x jitter) over the line, three in a row: step
BACKSTOP_JITTERS = 3.0
STEPS_ALARM = 2                              # this many self-steps ...
STEPS_WINDOW_S = 60.0                        # ... within this much host time set the alarm
CAPTURE_TOL_MIN_MS = 2.0                     # a heartbeat pair further off the line than max(this, 3 x jitter) ...
CAPTURE_TOL_JITTERS = 3.0
CAPTURE_STEP_PAIRS = 2                       # ... this many times in a row: the sender stepped
BRIDGE_TAU_S = 10.0                          # RemoteTimeMap: EMA of (audio path - remote path)
JITTER_GAIN = 1.0 / 16.0                    # RFC 3550 §6.4.1
_EPS = 1e-7                                  # 0.1 us: below the line by less than this is "on" it

NAN = math.nan


class LowerEnvelope:
    """Moon's lower-envelope line through (x, d) points: x = sender seconds, d = transit + constant (seconds)."""

    def __init__(self, *, window_s: float = WINDOW_S, fit_s: float = FIT_S, min_fit_span_s: float = MIN_FIT_SPAN_S,
                 skew_max_ppm: float = SKEW_MAX_PPM, alarm_min_span_s: float = ALARM_MIN_SPAN_S,
                 prior_skew: float = 0.0) -> None:
        self.window_s, self.fit_s, self.min_fit_span_s = window_s, fit_s, min_fit_span_s
        self.skew_max = skew_max_ppm * 1e-6
        self.alarm_min_span_s = alarm_min_span_s
        self.reset(prior_skew)

    def reset(self, prior_skew: float = 0.0, *, alarm: bool = False, b_raw: float = NAN) -> None:
        """Forget every point. ``alarm`` and ``b_raw`` carry a latched alarm over (a step)."""
        self.prior = max(-self.skew_max, min(self.skew_max, prior_skew))
        self.pts: deque[tuple[float, float]] = deque()   # closed buckets' minima (x, d), oldest first
        self.cur: Optional[list] = None                  # [bucket, x, d] of the open bucket's minimum
        self.a = NAN                                     # the line: d = a + b * x
        self.b = self.prior
        self.b_raw = b_raw                               # the fitted slope before the clamp
        self.alarm = alarm
        self.fitted = False                              # True once the slope comes from the data
        self.closed = False                              # True when the last add() closed a bucket
        self.span_s = 0.0
        self.count = 0
        self.jitter = 0.0
        self.late = 0.0
        self._last_d: Optional[float] = None

    @property
    def ready(self) -> bool:
        return not math.isnan(self.a)

    def value(self, x: float) -> float:
        """The envelope's d at x (NaN before the first point)."""
        return self.a + self.b * x

    def points(self) -> list[tuple[float, float]]:
        out = list(self.pts)
        if self.cur is not None:
            out.append((self.cur[1], self.cur[2]))
        return out

    def add(self, x: float, d: float) -> float:
        """Take one point; returns its residual over the line after the update (>= 0 up to rounding)."""
        self.count += 1
        if self._last_d is not None:                     # RFC 3550 jitter: J += (|D| - J) / 16
            self.jitter += (abs(d - self._last_d) - self.jitter) * JITTER_GAIN
        self._last_d = d
        k = math.floor(x / self.window_s)
        refit = False
        self.closed = False
        if self.cur is None:
            self.cur = [k, x, d]
            refit = True
        elif k > self.cur[0]:                            # the open bucket closes
            self.pts.append((self.cur[1], self.cur[2]))
            while self.pts and self.pts[0][0] < x - self.fit_s:
                self.pts.popleft()
            self.cur = [k, x, d]
            refit = self.closed = True
        elif d < self.cur[2]:                            # a new minimum in the open bucket (or a late straggler)
            self.cur[1], self.cur[2] = x, d
            refit = not self.ready or d < self.value(x) - _EPS
        if refit:
            self._fit()
        self.late = d - self.value(x)
        return self.late

    def _fit(self) -> None:
        pts = self.points()
        if not pts:
            return
        self.span_s = pts[-1][0] - pts[0][0]
        b = self.prior
        closed = self.pts                                # the slope only from whole buckets: an open bucket's
        if len(closed) >= 2 and closed[-1][0] - closed[0][0] >= self.min_fit_span_s:   # minimum is biased high
            raw = _hull_slope(list(closed))
            if raw is not None:
                self.b_raw = raw
                self.fitted = True
                if self.span_s >= self.alarm_min_span_s:  # a short span neither sets nor clears it (latched)
                    self.alarm = abs(raw) > self.skew_max
                b = max(-self.skew_max, min(self.skew_max, raw))
        self.b = b
        self.a = min(d - b * x for x, d in pts)          # the lowest line of slope b under every point

    def raised(self, tol: float, buckets: int = 3) -> bool:
        """True when the last ``buckets - 1`` closed buckets' minima and the open bucket's minimum so far all sit
        more than ``tol`` over the line (the backstop for lost time under STEP_LATE_S; the caller checks it when a
        bucket has just closed, so the open one holds its first point)."""
        if not self.ready or self.cur is None or len(self.pts) < buckets - 1:
            return False
        recent = list(self.pts)[-(buckets - 1):] + [(self.cur[1], self.cur[2])]
        return all(d - self.value(x) > tol for x, d in recent)


def _hull_slope(pts: list[tuple[float, float]]) -> float | None:
    """Slope of the lower convex hull's edge under the points' mean x (Moon et al.'s LP solution)."""
    ps = sorted(pts)
    hull: list[tuple[float, float]] = []
    for p in ps:
        if hull and p[0] == hull[-1][0]:                 # same x: keep the lower (sorted: the first)
            continue
        while len(hull) >= 2:
            (ox, oy), (ax, ay) = hull[-2], hull[-1]
            if (ax - ox) * (p[1] - oy) - (ay - oy) * (p[0] - ox) <= 0.0:
                hull.pop()
            else:
                break
        hull.append(p)
    if len(hull) < 2:
        return None
    xm = sum(p[0] for p in ps) / len(ps)
    for (x0, y0), (x1, y1) in zip(hull, hull[1:]):
        if xm <= x1:
            return (y1 - y0) / (x1 - x0)
    (x0, y0), (x1, y1) = hull[-2], hull[-1]
    return (y1 - y0) / (x1 - x0)


class _Clock:
    """Shared machinery: a LowerEnvelope over (sender seconds, transit) with segments, steps and an offset."""

    def __init__(self, acoustic_offset_ms: float = 0.0, **env_kw) -> None:
        self.acoustic_offset_ms = float(acoustic_offset_ms)   # hot-reloadable: set the attribute
        self.env = LowerEnvelope(**env_kw)
        self.seg = 0
        self.reason = "start"
        self.steps = 0
        self._step_at: deque[float] = deque()            # host arrival of each self-step in the last STEPS_WINDOW_S
        self._now = NAN                                  # the latest arrival seen
        self._t0 = NAN                                   # host arrival of the segment's first point
        self._late_since: Optional[float] = None
        self._last_x: Optional[float] = None

    # -- segments -------------------------------------------------------------------------------------------------
    def _segment(self, reason: str, keep_skew: bool) -> int:
        prior = self.env.b if keep_skew else 0.0
        self.env.reset(prior)
        self.seg += 1
        self.reason = reason
        self._t0 = NAN
        self._late_since = None
        self._last_x = None
        self._reset_origin()
        return self.seg

    def _reset_origin(self) -> None:
        raise NotImplementedError

    def _step(self, arrival_s: float) -> bool:
        alarm, b_raw = self.env.alarm, self.env.b_raw    # latched across the re-anchor (module docstring)
        self._segment("step", keep_skew=True)
        self.steps += 1
        self._step_at.append(arrival_s)
        self._trim_steps(arrival_s)
        self.env.alarm = alarm or len(self._step_at) >= STEPS_ALARM
        self.env.b_raw = b_raw
        return False

    def _trim_steps(self, now_s: float) -> None:
        while self._step_at and self._step_at[0] < now_s - STEPS_WINDOW_S:
            self._step_at.popleft()

    def _add(self, x: float, arrival_s: float) -> bool:
        """Feed one point (x in sender seconds since the segment origin); False if it started a new segment."""
        self._now = arrival_s if math.isnan(self._now) else max(self._now, arrival_s)
        if math.isnan(self._t0):
            self._t0 = arrival_s
        d = (arrival_s - self._t0) - x
        env = self.env
        if env.ready:
            if self._last_x is not None and x < self._last_x - STEP_BACK_S:
                return self._step(arrival_s)             # the sender's clock went back (a reboot)
            r = d - env.value(x)
            if r < -STEP_EARLY_S:
                return self._step(arrival_s)             # samples skipped, or its clock jumped ahead
            if r > STEP_LATE_S:
                if self._late_since is None:
                    self._late_since = x
                elif x - self._late_since >= STEP_PERSIST_S:
                    return self._step(arrival_s)         # it lost time and kept counting
                env.late = r                             # too late to say anything about the envelope
                self._last_x = max(self._last_x if self._last_x is not None else x, x)
                return True
            self._late_since = None
        env.add(x, d)
        self._last_x = x if self._last_x is None else max(self._last_x, x)
        if env.closed and env.raised(max(BACKSTOP_MIN_S, BACKSTOP_JITTERS * env.jitter)):
            return self._step(arrival_s)                 # it lost a little time and kept counting
        return True

    # -- reading --------------------------------------------------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self.env.ready

    @property
    def skew_ppm(self) -> float:
        """Host seconds per sender second - 1, in ppm (clamped to +-300)."""
        return self.env.b * 1e6

    @property
    def skew_alarm(self) -> bool:
        return self.env.alarm

    @property
    def steps_last_min(self) -> int:
        """Self-steps within STEPS_WINDOW_S of the latest arrival."""
        if not math.isnan(self._now):
            self._trim_steps(self._now)
        return len(self._step_at)

    @property
    def jitter_ms(self) -> float:
        return self.env.jitter * 1000.0

    @property
    def late_ms(self) -> float:
        return self.env.late * 1000.0 if self.env.ready else NAN

    def _host(self, x: float, offset: bool) -> float:
        env = self.env
        t = self._t0 + env.a + (1.0 + env.b) * x
        return t + self.acoustic_offset_ms / 1000.0 if offset else t

    def _x(self, t_host: float, offset: bool) -> float:
        env = self.env
        if offset:
            t_host -= self.acoustic_offset_ms / 1000.0
        return (t_host - self._t0 - env.a) / (1.0 + env.b)

    def state(self) -> dict:
        env = self.env
        return {"segment": self.seg, "reason": self.reason, "ready": env.ready, "fitted": env.fitted,
                "skew_ppm": round(env.b * 1e6, 3),
                "skew_raw_ppm": None if math.isnan(env.b_raw) else round(env.b_raw * 1e6, 3),
                "skew_alarm": env.alarm, "span_s": round(env.span_s, 3), "points": len(env.pts) + (env.cur is not None),
                "observations": env.count, "jitter_ms": round(self.jitter_ms, 3),
                "late_ms": None if not env.ready else round(self.late_ms, 3), "steps": self.steps,
                "steps_last_min": self.steps_last_min, "acoustic_offset_ms": self.acoustic_offset_ms}


class SampleClock(_Clock):
    """A stream's sample index -> host CLOCK_MONOTONIC seconds (acoustic), and back.

    ``observe(n_first, arrival_s, frames)`` once per audio packet, in arrival order: ``n_first`` is the index of the
    packet's first sample in the receiver's running count for this segment, ``arrival_s`` the host monotonic time it
    arrived, ``frames`` its length. A packet leaves the sender only once its last sample is in, so its arrival bounds
    the capture of sample ``n_first + frames``; pass ``frames`` (0 folds the packet's duration into the constant).
    It leaves at the capture callback, ``in_lat`` after its first sample's ADC time, so the mapped time is the ADC
    time + (in_lat - frames/fs) + the smallest transport delay (module docstring).
    """

    def __init__(self, fs: int = 48000, acoustic_offset_ms: float = 0.0, **env_kw) -> None:
        super().__init__(acoustic_offset_ms, **env_kw)
        self.fs = int(fs)
        self._n0: Optional[int] = None

    def _reset_origin(self) -> None:
        self._n0 = None

    def segment(self, reason: str = "", *, keep_skew: bool = False, fs: int | None = None) -> int:
        """Start a new segment (forget the map); returns its id. ``keep_skew`` keeps the slope as the new prior
        (same device: a sender restart); ``fs`` changes the nominal rate."""
        if fs is not None:
            self.fs = int(fs)
        return self._segment(reason or "segment", keep_skew)

    def observe(self, n_first: int, arrival_s: float, frames: int = 0) -> bool:
        """Feed one packet. False if it re-anchored the clock (a step: see the module docstring)."""
        n_end = int(n_first) + int(frames)
        if self._n0 is None:
            self._n0 = n_end
        ok = self._add((n_end - self._n0) / self.fs, float(arrival_s))
        if not ok:                                       # re-anchored on this very packet
            self._n0 = n_end
            self._add(0.0, float(arrival_s))
        return ok

    def to_host(self, n: float, offset: bool = True) -> float:
        """Host monotonic seconds of sample ``n`` (acoustic time with ``offset``); NaN before any packet."""
        if self._n0 is None:
            return NAN
        return self._host((n - self._n0) / self.fs, offset)

    def to_n(self, t_host: float, offset: bool = True) -> float:
        """The (fractional) sample index at host time ``t_host``; NaN before any packet."""
        if self._n0 is None:
            return NAN
        return self._n0 + self._x(t_host, offset) * self.fs

    def state(self) -> dict:
        return {**super().state(), "fs": self.fs, "n0": self._n0}


class RemoteClock(_Clock):
    """A sender's clock (ns, vban.py "Clocks") -> host seconds, by the same lower envelope.

    Feed it ``observe(t_remote_ns, arrival_s)`` for packets the sender stamped *as it sent them*: a MIDI batch's
    newest event (sent within 2 ms of it) and a MIDI state's ``t_ns``. Don't mix in heartbeat ``t_cap_ns`` (an ADC
    time, older than the send by a buffer): the envelope keeps whichever kind has the smaller constant.
    """

    def __init__(self, acoustic_offset_ms: float = 0.0, **env_kw) -> None:
        super().__init__(acoustic_offset_ms, **env_kw)
        self._r0: Optional[int] = None

    def _reset_origin(self) -> None:
        self._r0 = None

    def segment(self, reason: str = "", *, keep_skew: bool = False) -> int:
        """Start over (the sender rebooted or its clock stepped); returns the new segment id."""
        return self._segment(reason or "segment", keep_skew)

    def observe(self, t_remote_ns: int, arrival_s: float) -> bool:
        t = int(t_remote_ns)
        if self._r0 is None:
            self._r0 = t
        ok = self._add((t - self._r0) / 1e9, float(arrival_s))
        if not ok:
            self._r0 = t
            self._add(0.0, float(arrival_s))
        return ok

    def to_host(self, t_remote_ns: int, offset: bool = True) -> float:
        if self._r0 is None:
            return NAN
        return self._host((int(t_remote_ns) - self._r0) / 1e9, offset)

    def to_remote_ns(self, t_host: float, offset: bool = True) -> float:
        if self._r0 is None:
            return NAN
        return self._r0 + self._x(t_host, offset) * 1e9


class CaptureMap:
    """A sender's clock <-> one audio stream's sample index, from its heartbeats' (n, t_cap_ns) pairs.

    ``n`` must be in the receiver's index space for the stream (the SampleClock's): the receiver converts the
    heartbeat's ``frame`` (a packet counter) to its own index for that packet. Both ends are on the sender's box, so
    the only noise is capture-callback timing, and a lower envelope again takes the earliest. Tied to one segment of
    the stream's SampleClock: a pair from another segment resets it.

    Sender steps. The receiver's index advances with every packet it gets or zero-fills, so a sender that loses
    capture time but keeps counting (an input overflow it doesn't count over, vban.py "Sender rules") shows here,
    and only here, as pairs whose ``t_cap_ns`` lies off the line. A pair more than :attr:`tolerance_ms`
    (max(CAPTURE_TOL_MIN_MS, 3 x jitter)) off never feeds the line; CAPTURE_STEP_PAIRS such pairs in a row make
    :meth:`observe` return False: the receiver then starts a ``"sender-step"`` segment (keep_skew=True), and the map
    starts over from that pair. One pair off alone is a late callback, and is dropped.
    """

    def __init__(self, fs: int = 48000, **env_kw) -> None:
        env_kw.setdefault("window_s", 4.0)               # 8 heartbeats per bucket
        self.fs = int(fs)
        self.env = LowerEnvelope(**env_kw)
        self.seg: Optional[int] = None
        self.off = 0                                     # pairs off the line in a row
        self.last_residual_ms = NAN
        self._n0: Optional[int] = None
        self._t0: Optional[int] = None

    def reset(self, seg: Optional[int] = None, fs: Optional[int] = None) -> None:
        if fs is not None:
            self.fs = int(fs)
        self.env.reset(0.0)
        self.seg, self._n0, self._t0 = seg, None, None
        self.off = 0

    @property
    def ready(self) -> bool:
        return self.env.ready

    @property
    def tolerance_ms(self) -> float:
        """How far off the line a pair may lie and still be on it: max(2 ms, 3 x the pairs' RFC 3550 jitter)."""
        return max(CAPTURE_TOL_MIN_MS, CAPTURE_TOL_JITTERS * self.env.jitter * 1000.0)

    def residual_ms(self, n: int, t_cap_ns: int) -> float:
        """How far ``t_cap_ns`` lies off the line at index ``n``, in ms: + = later than the line says (capture time
        lost while the index kept counting), - = earlier (samples skipped). NaN before any pair."""
        if self._n0 is None or not self.env.ready:
            return NAN
        x = (int(n) - self._n0) / self.fs
        return ((int(t_cap_ns) - self._t0) / 1e9 - x - self.env.value(x)) * 1000.0

    def observe(self, n: int, t_cap_ns: int, seg: Optional[int] = None) -> bool:
        """One heartbeat pair; ``seg`` = the stream SampleClock's segment the index belongs to. False when the
        sender stepped (CAPTURE_STEP_PAIRS pairs in a row off the line): start a "sender-step" segment."""
        if seg != self.seg:
            self.reset(seg)
        n, t = int(n), int(t_cap_ns)
        if self._n0 is not None and self.env.ready:
            r = self.residual_ms(n, t)
            self.last_residual_ms = r
            if abs(r) > self.tolerance_ms:
                self.off += 1
                if self.off < CAPTURE_STEP_PAIRS:
                    return True                          # one late callback, or the first of a step: held back
                self.reset(seg)                          # the line is wrong now: start over from this pair
                self._add(n, t)
                return False
            self.off = 0
        self._add(n, t)
        return True

    def _add(self, n: int, t: int) -> None:
        if self._n0 is None:
            self._n0, self._t0 = n, t
        x = (n - self._n0) / self.fs
        self.env.add(x, (t - self._t0) / 1e9 - x)       # remote seconds - sample seconds: the envelope's d

    def to_n(self, t_remote_ns: int) -> float:
        """The stream's sample index captured at sender time ``t_remote_ns`` (NaN before any pair)."""
        if self._n0 is None or not self.env.ready:
            return NAN
        env = self.env
        return self._n0 + ((int(t_remote_ns) - self._t0) / 1e9 - env.a) / (1.0 + env.b) * self.fs

    def to_remote_ns(self, n: float) -> float:
        if self._n0 is None or not self.env.ready:
            return NAN
        x = (n - self._n0) / self.fs
        return self._t0 + (self.env.a + (1.0 + self.env.b) * x) * 1e9

    def state(self) -> dict:
        r = self.last_residual_ms
        return {"segment": self.seg, "ready": self.ready, "pairs": self.env.count,
                "skew_ppm": round(self.env.b * 1e6, 3), "jitter_ms": round(self.env.jitter * 1000.0, 3),
                "residual_ms": None if math.isnan(r) else round(r, 3), "off": self.off}


class RemoteTimeMap:
    """MIDI ``t_ns`` (the sender's clock, vban.py "Clocks") -> host acoustic seconds.

    Through the audio stream when one from the same sender is attached and both its SampleClock and CaptureMap are
    ready for the same segment: t_ns -> sample index -> host, so notes and sound share one map (§3.5). Otherwise
    through its own RemoteClock, fed by :meth:`observe`, plus ``bridge_s``.

    The two raw maps differ by a constant: the audio map's time is the ADC time + (in_lat - frames/fs) + the
    smallest transport delay, the remote map's is the stamp + the smallest transport delay (module docstring). With
    a 20 ms input latency that is 17.3 ms. So while ``via`` is "audio" and the RemoteClock is ready, every
    observation updates ``bridge_s`` = an EMA (tau BRIDGE_TAU_S, 10 s, of host time) of (audio path - remote path)
    at that ``t_ns``, and the remote path adds it: when ``via`` flips (an audio stall, a self-step until the next
    heartbeat, the audio stream going down) notes don't jump. ``bridge_s`` is 0 until it has been measured (MIDI
    with no audio ever: the remote constant). Either way this map's own ``acoustic_offset_ms`` (the MIDI source's,
    from latency.json) is applied, not the audio stream's.
    """

    def __init__(self, acoustic_offset_ms: float = 0.0, **env_kw) -> None:
        self.acoustic_offset_ms = float(acoustic_offset_ms)
        self.remote = RemoteClock(0.0, **env_kw)
        self.audio: Optional[SampleClock] = None
        self.capture: Optional[CaptureMap] = None
        self.bridge_s = 0.0
        self._bridge_at: Optional[float] = None          # host arrival of the last bridge update (None: never)

    def attach_audio(self, audio: SampleClock, capture: CaptureMap) -> None:
        self.audio, self.capture = audio, capture

    def detach_audio(self) -> None:
        self.audio = self.capture = None

    def observe(self, t_remote_ns: int, arrival_s: float) -> bool:
        """Feed the RemoteClock one packet stamped as sent; False if that re-anchored it. Updates the bridge."""
        ok = self.remote.observe(t_remote_ns, arrival_s)
        if self.via == "audio" and self.remote.ready:
            diff = self._audio_path(t_remote_ns) - self.remote.to_host(t_remote_ns, offset=False)
            if math.isfinite(diff):
                if self._bridge_at is None:
                    self.bridge_s = diff
                else:
                    dt = max(0.0, float(arrival_s) - self._bridge_at)
                    self.bridge_s += (diff - self.bridge_s) * (1.0 - math.exp(-dt / BRIDGE_TAU_S))
                self._bridge_at = float(arrival_s)
        return ok

    @property
    def via(self) -> Optional[str]:
        """"audio", "remote" or None (nothing to map with yet)."""
        a, c = self.audio, self.capture
        if a is not None and c is not None and a.ready and c.ready and c.seg == a.seg:
            return "audio"
        return "remote" if self.remote.ready else None

    def _audio_path(self, t_remote_ns: int) -> float:
        return self.audio.to_host(self.capture.to_n(t_remote_ns), offset=False)   # type: ignore[union-attr]

    def to_host(self, t_remote_ns: int, offset: bool = True) -> float:
        via = self.via
        if via == "audio":
            t = self._audio_path(t_remote_ns)
        elif via == "remote":
            t = self.remote.to_host(t_remote_ns, offset=False) + self.bridge_s
        else:
            return NAN
        return t + self.acoustic_offset_ms / 1000.0 if offset else t

    def state(self) -> dict:
        return {"via": self.via, "acoustic_offset_ms": self.acoustic_offset_ms,
                "bridge_ms": None if self._bridge_at is None else round(self.bridge_s * 1000.0, 3),
                "remote": self.remote.state(), "capture": None if self.capture is None else self.capture.state()}
