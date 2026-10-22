"""Bindings, derived masters and the session palette: per-frame scene modulation (LIVE_V1 1.1 §1.9-§1.11).

A numeric parameter whose schema has ``x-bindable: true`` can take a binding instead of a value, in a preset's
params or in PATCH params (R19):

  {"bind": "<signal>", "range": [lo, hi], "rest"?: r}
      value = rest + depth * (lo + (hi - lo) * signal - rest)

``rest`` defaults to ``lo`` for trigger signals (pulse, beat) and to the range midpoint otherwise; ``depth`` is
``direction.intensity``.  :func:`split_bindings` validates bindings and returns the plain params (a binding's
plain value is its rest) plus the bindings, so validated params stay plain numbers everywhere.  Bound values
are written every frame and bypass the PATCH easer: smoothness comes only from the ``*_slow`` signals.

Derived masters (R13), eased over ``direction_ease_s`` after create:
  speed  0.5x .. 2x, exponential in direction.pace, applied as the scene clock: a scene source gets
         ``dt * speed`` (never transitions, whose durations stay in seconds);
  depth  direction.intensity: scales every binding's swing about its rest.
There is no luminance master (R2).  The session ``palette`` (R14) is crossfaded here, on the render thread, and
handed as cosine rows to sources that mark their palette param ``x-palette: true``.

Bindings v2 (binding.py; owner, 15 Oct 2026): a param may instead follow a blend of signals, in live / section /
cue mode, pivoting on its plain value (the preset's: the base); the per-item mappings (mappings.py) supply them.
The runtime merges the session's music features (``extra``, set by the loop each tick) into the bus signals, fades
n/a inputs in and out (``avail``, tau 2 s), keeps the section epoch, and eases Maestro's colour into cosine rows
(``maestro_rows``) for the colour form: a palette-driven scene (``session_palette()``) gets its palette blended
toward the room's colour by the binding's mix, and a GLSL scene's ``palette_mix`` is raised to that amount.
While show_service's features arrive (music_feed.Response): ``energy`` is theirs, fast; ``beat`` comes from the
beat ladder (music_feed.BeatLadder: one source at the frame's photon time, every pulse x "music present"); and
section mappings follow the live value by ``follow``.

The adaptive pivot (:class:`Pivots`; owner, 17 Oct): a binding pivots on its base at the input's idle 0.5, but drive
(max ~0.45), tempo and motion never reach 0.5, so rate mappings ran scenes BELOW their authored base all song.  Each
level listed in Response.pivot_signals now centres on its own norm: the running median of its values while music
plays (sampled once a second while the bindings' energy is at least pivot_present_at and the level is fully live;
silence, n/a and fades hold the ring, so a quiet room never drags the norm to 0; over pivot_window_s; it applies once
there are pivot_min_s of them, before that the fixed 0.5).  It re-centres the signal the bindings read,
x' = clamp01(x - w (median - 0.5)) with w the signal's liveness (x = 0.5 + w (v - 0.5), so x' = 0.5 + w (v - median)):
at its norm a binding gives its base, and with the bus down (w 0) the signal is idle and the binding gives its base as
before.  The offset is eased (tau pivot_ease_s) in when a median first applies, across a median's moves and out when
the pivot is switched off: never a step.  Re-centring rather than
moving each binding's x0 to the median: one O(listed signals) pass per frame here instead of a per-binding blend
re-evaluation, it reaches every binding that reads the signals (v1, v2 live / section / cue, kicks' ``by``, effects
and the master bus) the same way, and x0 at the median would break the idle invariant whenever the bus goes (signals
ease to 0.5, x0 stays at the median) unless every binding re-evaluated its x0 with the liveness each frame.  The
cost: the clamp saturates an input more than 0.5 from its norm (rare for the low-range levels that need it).  Never
triggers, phases or DMX positions (inputs.pivotable).

The room ensemble (ensemble.py; docs/ENSEMBLE.md §8.3, §8.5): the session puts this screen's part in ``extra``
(``ensemble``: its levers now and next, glided here at photon time, ``lever``; None = unison, today's path): depth x
the binding depth, motion x the scene clock, pulse x the bindings' beat, energy_bias + their energy (after the
pivots, so the pivot never re-centres it away), pal_shift reads the session palette that far along.

The cut invariant (R1): only scene sources get a Modulation.  The ``image`` source (the anchor and exit
targets) never does, so it always renders at identity and the frame at ``hold_pts`` equals the still.

Threads: the loop thread only replaces whole values on :class:`SessionRuntime` (targets, palette): single
assignments, atomic under the GIL.  Bindings change through the engine's command queue (UpdateParams.bindings).
"""
from __future__ import annotations

import bisect
import collections
import math
from typing import Any, Optional

import numpy as np

import logging

from . import binding, ensemble, music_feed, palettes, piano
from .inputs import BIND_SIGNALS, FEED_SIGNALS, MUSIC_SIGNALS, NA_SIGNALS, TRIGGERS, Sampler, idle_value, pivotable
from .piano import PIANO_LEVELS
from .plugins.base import ParamError, is_bindable

log = logging.getLogger("stream_service.modulation")
AVAIL_TAU_S = 2.0
MAESTRO_EASE_S = 3.0
PIVOT_SAMPLE_S = 1.0       # the adaptive pivot samples each listed level once a second ...
PIVOT_LIVE = 0.99          # ... while its source is this live (and, n/a-able, fully available): else held


def _finite(v: Any, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ParamError("bad_param", f"{what} must be a finite number")
    return float(v)


def _clamp(prop: dict, v: float) -> float:
    lo, hi = prop.get("minimum"), prop.get("maximum")
    if lo is not None and v < lo:
        v = float(lo)
    if hi is not None and v > hi:
        v = float(hi)
    return v


def speed_for(pace: float) -> float:
    return 2.0 ** (2.0 * pace - 1.0)


def parse_binding(name: str, prop: dict, spec: dict) -> dict:
    """Validate one binding against its parameter's schema; returns a normalised copy (with ``rest``)."""
    if not prop.get("x-bindable") or prop.get("type") not in ("number", "integer"):   # True or a hint like "level"
        raise ParamError("bad_param", f"{name} is not bindable")
    extra = set(spec) - {"bind", "range", "rest"}
    if extra:
        raise ParamError("bad_param", f"{name}: unknown binding keys {sorted(extra)} (v1: bind, range, rest)")
    sig = spec.get("bind")
    if sig not in BIND_SIGNALS:
        raise ParamError("bad_param", f"{name}: unknown signal {sig!r} (known: {list(BIND_SIGNALS)})")
    rng = spec.get("range", [prop.get("minimum", 0.0), prop.get("maximum", 1.0)])
    if not (isinstance(rng, (list, tuple)) and len(rng) == 2):
        raise ParamError("bad_param", f"{name}.range must be [lo, hi]")
    lo, hi = (_clamp(prop, _finite(x, f"{name}.range")) for x in rng)
    rest = spec.get("rest")
    rest = (lo if sig in TRIGGERS else (lo + hi) / 2) if rest is None else _clamp(prop, _finite(rest, f"{name}.rest"))
    return {"bind": sig, "range": [lo, hi], "rest": rest}


def split_bindings(props: dict[str, dict], params: Optional[dict], base_bindings: Optional[dict] = None,
                   restore: Optional[tuple[dict, dict]] = None) -> tuple[dict, dict]:
    """params (values, bindings or null) -> (plain params for validate_params, the bindings after this update).

    A value replaces a binding on that parameter; a binding replaces a value (its plain value is its rest).
    ``null`` restores what ``restore = (plain, bindings)`` (the preset's, R13) holds for that parameter, or
    its default."""
    if params is None:
        return {}, dict(base_bindings or {})
    if not isinstance(params, dict):
        raise ParamError("bad_param", "params must be an object")
    plain: dict = {}
    bindings = dict(base_bindings or {})
    for key, value in params.items():
        prop = props.get(key)
        if prop is None:
            raise ParamError("unknown_param", f"unknown parameter {key!r} (known: {sorted(props)})")
        if value is None:
            r_plain, r_bind = restore or ({}, {})
            bindings.pop(key, None)
            if key in r_bind:
                bindings[key] = r_bind[key]
            plain[key] = r_plain.get(key, prop["default"])
        elif isinstance(value, dict):
            if binding.is_v2(value):                 # a v2 mapping: the plain value stays (it is the base)
                bindings[key] = binding.parse_v2(key, prop, value)
                continue
            if set(value) == {"base"}:               # move a mapped param's centre, keeping its mapping
                plain[key] = value["base"]
                continue
            if "bind" not in value:
                raise ParamError("bad_param", f"{key}: an object value must be a binding {{\"bind\": ...}}, "
                                              f"a v2 mapping {{\"in\": ...}} or {{\"base\": v}}")
            b = parse_binding(key, prop, value)
            bindings[key] = b
            plain[key] = b["rest"]
        else:
            bindings.pop(key, None)
            plain[key] = value
    return plain, bindings


def _pivot_source(k: str) -> str:
    """Whose liveness eases a signal to idle in begin_frame (Pivots weighs its offset by it)."""
    return "music" if k in MUSIC_SIGNALS else "feed" if k in FEED_SIGNALS else \
        "act" if k in ("activity", "activity_slow") else "piano" if k in PIANO_LEVELS else "desk"


class Pivots:
    """The adaptive pivot (the module docstring): each listed level's running median over a bounded ring (sorted
    insert / remove at 1 Hz), and the re-centring of the signals the bindings read (O(listed) per frame).  Per session
    (each session's own signal stream); the render thread samples, eases and re-centres, the loop thread re-centres
    too (sessions.Session._cue_signals, for cue mappings) and reads ``view`` (the inputs API): ``off`` and ``view`` are
    replaced whole when their keys change, an ``off`` entry's eased offset is a float set in place, ``w`` holds plain
    floats."""

    def __init__(self) -> None:
        self._cfg: Any = None
        self.need = 1                             # samples before a median applies (pivot_min_s)
        self.ease_s = 3.0                         # the offsets' tau (pivot_ease_s)
        self.present_at = 0.0                     # samples only while the bindings' energy is this (pivot_present_at)
        self.rings: dict[str, tuple[collections.deque, list]] = {}   # signal -> (samples in order, sorted)
        self.src: dict[str, str] = {}             # signal -> its liveness source (_pivot_source)
        self.w: dict[str, float] = {"music": 0.0, "feed": 0.0, "act": 0.0, "piano": 1.0, "desk": 0.0}
        self.tgt: dict[str, float] = {}           # applied: signal -> median - 0.5 (at the 1 Hz sample)
        self.off: dict[str, list] = {}            # signal -> [source, offset eased to tgt (0 once not applied)]
        self.view: dict = {}                      # signal -> {median, applied, samples}
        self._t: Optional[float] = None           # the last sample
        self._tf: Optional[float] = None          # the last frame

    @property
    def active(self) -> bool:
        return bool(self.rings or self.off)

    def configure(self, r: Any) -> None:
        """music_feed.Response's pivot_*: rebuilt only when they change (hot reload; the samples are kept)."""
        self.ease_s, self.present_at = r.pivot_ease_s, r.pivot_present_at
        key = (r.pivot_on, r.pivot_window_s, r.pivot_min_s, r.pivot_signals)
        if key == self._cfg:
            return
        self._cfg = key
        n = max(1, int(r.pivot_window_s / PIVOT_SAMPLE_S))
        self.need = min(n, max(1, math.ceil(r.pivot_min_s / PIVOT_SAMPLE_S)))
        rings = {}
        for k in (sorted(k for k in r.pivot_signals if pivotable(k)) if r.pivot_on else ()):
            old = self.rings.get(k)
            vals = list(old[0])[-n:] if old is not None else []
            rings[k] = (collections.deque(vals, maxlen=n), sorted(vals))
        self.rings, self.src = rings, {k: _pivot_source(k) for k in rings}
        self._publish()

    def step(self, sig: dict, avail: dict, now: float, present: bool = True) -> None:
        """Render thread, once per frame (while ``active``): a sample of each listed level once a second (only while
        the music is ``present`` and the level fully live: silence, n/a or fading holds the ring), the offsets eased
        toward their medians (out, once not applied), then the re-centring."""
        if self._t is None or now - self._t >= PIVOT_SAMPLE_S or now < self._t:
            self._t = now
            w = self.w
            for k, (ring, srt) in (self.rings.items() if present else ()):
                v = sig.get(k)
                if v is None or w[self.src[k]] < PIVOT_LIVE or avail.get(k, 1.0) < PIVOT_LIVE:
                    continue
                if len(ring) == ring.maxlen:      # the oldest leaves the sorted copy too
                    del srt[bisect.bisect_left(srt, ring[0])]
                ring.append(v)
                bisect.insort(srt, v)
            self._publish()
        dt = 0.0 if self._tf is None else min(0.5, max(0.0, now - self._tf))
        self._tf = now
        a = 1.0 if self.ease_s <= 0.0 else 1.0 - math.exp(-dt / self.ease_s)
        tgt, gone = self.tgt, None
        for k, e in self.off.items():
            e[1] += (tgt.get(k, 0.0) - e[1]) * a
            if k not in tgt and abs(e[1]) < 1e-4:
                gone = (gone or []) + [k]
        if gone:
            self.off = {k: e for k, e in self.off.items() if k not in gone}
        self.apply(sig)

    def apply(self, sig: dict) -> None:
        """Re-centre the applied signals in ``sig`` in place: x' = clamp01(x - w (median - 0.5)), eased."""
        w = self.w
        for k, (src, off) in self.off.items():
            v = sig.get(k)
            if v is not None:
                v -= w[src] * off
                sig[k] = 0.0 if v < 0.0 else 1.0 if v > 1.0 else v

    def shift(self, k: str) -> float:
        """What apply subtracts from signal ``k`` now (the inputs API)."""
        e = self.off.get(k)
        return 0.0 if e is None else self.w[e[0]] * e[1]

    def _publish(self) -> None:
        tgt, view = {}, {}
        for k, (ring, srt) in self.rings.items():
            n = len(srt)
            med = None if n == 0 else srt[n // 2] if n % 2 else 0.5 * (srt[n // 2 - 1] + srt[n // 2])
            applied = med is not None and n >= self.need
            if applied:
                tgt[k] = med - 0.5
            view[k] = {"median": None if med is None else round(med, 4), "applied": applied,
                       "samples": n}
        self.tgt, self.view = tgt, view
        if any(k not in self.off for k in tgt):  # a newly applied level eases in from 0
            self.off = {**self.off, **{k: [self.src[k], 0.0] for k in tgt if k not in self.off}}


class SessionRuntime:
    """Per-session modulation state shared by the session's scene sources."""

    def __init__(self, sampler: Optional[Sampler], *, speed: float = 1.0, depth: float = 1.0,
                 ease_s: float = 8.0):
        self.sampler = sampler
        self.ease_s = ease_s
        # written by the loop thread (whole values), read by the render thread
        self.speed_target = speed
        self.depth_target = depth
        self.palette: Optional[str] = None
        self.palette_s = 4.0
        # render thread state
        self.speed = speed
        self.depth = depth
        self.signals: dict = {}
        self.palette_rows: Optional[np.ndarray] = None    # the eased session palette (4 x 3), or None
        self._pal_from: Optional[np.ndarray] = None
        self._pal_name: Optional[str] = None
        self._pal_t = 0.0
        self._t: Optional[float] = None
        # bindings v2: written by the loop (whole dict), merged here
        self.extra: dict = {}                     # music features {energy, drive, punch, tempo, epoch, music_w}
        self.feed = None                          # show_service's music (music_feed.MusicFeed), set by the session
        self.clock_sig = music_feed.ClockSignals()
        self.now = 0.0
        self.dt = 0.0
        self.avail: dict[str, float] = {}         # n/a-able signals' eased availability 0-1
        self._lastv: dict[str, float] = {}
        self.epoch: tuple = (0, 0)                # (music section/band epoch, liveness crossings)
        self._live_hi = False
        self.maestro_rows: Optional[np.ndarray] = None    # Maestro's colour as cosine rows (eased), or None
        self.colour_avail = 0.0
        # section mappings following the live value (binding.Bound.step; music_feed.Response), set each frame
        self.follow = 0.0
        self.response: Any = music_feed.Response()
        self.beat_source: Optional[str] = None    # the ladder's rung, else blend's ("clock" | "onset" | None)
        self.ladder = music_feed.BeatLadder()     # the bindings' beat from one source at photon time
        self.pivots = Pivots()                    # the adaptive pivot (per session)
        self.lever: Optional[dict] = None         # this screen's ensemble levers this frame (ensemble.py); None: unison
        self._lever_glide = ensemble.LeverGlide()

    def begin_frame(self, now: float) -> None:
        """Render thread, once per rendered frame (``now``: the frame's time on CLOCK_MONOTONIC)."""
        dt = 0.0 if self._t is None else min(0.5, max(0.0, now - self._t))
        self._t = now
        a = 1.0 - math.exp(-dt / max(0.05, self.ease_s / 4.0)) if dt > 0 else 0.0   # ~98 % in ease_s
        self.speed += (self.speed_target - self.speed) * a
        self.depth += (self.depth_target - self.depth) * a
        name = self.palette
        if name != self._pal_name:              # a new session palette: crossfade from what is showing
            self._pal_name = name
            if name is None:
                self.palette_rows = None
            else:
                self._pal_from = self.palette_rows
                self._pal_t = 0.0
                self.palette_rows = np.array(palettes.cosine(name), np.float64)
        if name is not None and self._pal_from is not None:
            self._pal_t += dt
            u = min(1.0, self._pal_t / max(1e-3, self.palette_s))
            u = u * u * (3 - 2 * u)
            target = np.array(palettes.cosine(name), np.float64)
            self.palette_rows = self._pal_from + (target - self._pal_from) * u
            if u >= 1.0:
                self._pal_from = None
        sig = self.sampler.sample(now) if self.sampler is not None else {}
        w = self.sampler.w if self.sampler is not None else 0.0
        wf = 0.0
        ex = self.extra
        fast, follow, src, wa = None, 0.0, None, w
        feed = self.feed
        if feed is not None:                    # the room's sound and beat clock (music_feed.py) over the desk's
            wf = feed.liveness(now)
            lead = self.sampler.lead if self.sampler is not None else 0.0
            r = feed.response.cfg
            snap, clk = feed.snapshot(), feed.clock(now)
            csig = self.clock_sig.sample(clk, now + lead)
            src = music_feed.blend(sig, snap, wf, csig, feed.levels(now), feed.onset_pulse(now))
            if r.ladder_on and wf > 0.0:        # the bindings' beat: one source at photon time
                desk = bool(sig.get("beat_locked")) and getattr(self.sampler, "follow", None) == "extrapolate"
                src = self.ladder.resolve(sig, feed, clk, csig, self.clock_sig, wf, now, now + lead, desk)
            else:                               # the feed gone: the desk's (or nothing), as blend left it
                self.ladder.active = False
            follow = r.follow * wf if wf > 0.0 else 0.0
            self.response = r
            if ex.get("music_w") is not None:   # the session's music is show_service's: its energy, at its rate
                fast = feed.energy_now(now)
            if snap is not None and wf > 0.0:   # activity: the desk's, eased to the feed's (blend)
                wa = wf + (1.0 - wf) * w
        self.follow, self.beat_source = follow, src
        wm = ex.get("music_w")
        wm = w if wm is None else wm
        music_feed.music_signals(sig, ex, wm, fast, wf)   # the music features, eased to idle with their liveness
        for k in piano.PIANO_LEVELS:            # the piano's: n/a when it is silent or not connected
            sig[k] = ex.get(k)
        sig.update(piano.envelopes(ex, now))    # note_on / chord_change, sharp at the frame rate
        av = 1.0 - math.exp(-dt / AVAIL_TAU_S) if dt > 0 else 0.0
        for k in NA_SIGNALS:                    # n/a inputs fade out (holding their last value) and back in
            v = sig.get(k)
            if v is not None:
                self._lastv[k] = v
            a = self.avail.get(k, 0.0)
            a += ((1.0 if v is not None else 0.0) - a) * av
            self.avail[k] = a
            sig[k] = self._lastv.get(k) if a > 1e-3 else None
        pv = self.pivots                        # the adaptive pivot: each listed level centred on its own norm
        pv.configure(self.response)
        if pv.active:
            pw = pv.w
            pw["music"], pw["feed"], pw["act"], pw["desk"] = wm, wf, wa, w
            e = sig.get("energy")               # music present: the bindings' energy (n/a: liveness alone)
            pv.step(sig, self.avail, now, e is None or e >= pv.present_at)
        hi = max(w, wf) >= 0.5
        live_ep = self.epoch[1] + (1 if hi != self._live_hi else 0)
        self._live_hi = hi
        self.epoch = (int(ex.get("epoch") or 0), live_ep)
        col = sig.get("colour")
        ac = 1.0 - math.exp(-dt / MAESTRO_EASE_S) if dt > 0 else 0.0
        if col is not None:
            target = np.array(binding.maestro_rows(col["hue"], col["spread"], col["sat"]), np.float64)
            self.maestro_rows = target if self.maestro_rows is None else \
                self.maestro_rows + (target - self.maestro_rows) * ac
        self.colour_avail += ((w if col is not None else 0.0) - self.colour_avail) * ac
        try:                                    # the room ensemble's levers at photon time (never breaks a frame)
            lv = self._lever_glide.at(ex.get("ensemble"),
                                      (now + (self.sampler.lead if self.sampler is not None else 0.0)) * 1000.0)
        except Exception:
            lv = None
        self.lever = lv
        if lv is not None:
            ensemble.apply_signals(sig, lv)
        self.now, self.dt = now, dt
        self.signals = sig

    def masters(self) -> dict:
        return {"speed": round(self.speed, 4), "depth": round(self.depth, 4),
                "speed_target": round(self.speed_target, 4), "depth_target": round(self.depth_target, 4)}

    def inputs_view(self, out: dict, now: float) -> None:
        """The inputs API (loop thread, sessions.Session.inputs; ``out`` in place), off the render thread's last
        frame: while the beat ladder runs (the ladder on and the feed live), ``derived.beat_source`` is its rung and
        ``derived.beat`` its beat; ``ladder`` (rung, pulses_per_min, presence, the tempo-clear test and the onset
        follower) and ``pivot`` (each listed level's median, whether it applies, the shift in effect and the value the
        bindings read)."""
        feed, lad, pv = self.feed, self.ladder, self.pivots
        r = feed.response.cfg if feed is not None else self.response
        running = feed is not None and r.ladder_on and lad.active and self._t is not None
        if running:
            d = out.setdefault("derived", {})
            d["beat_source"], d["beat"] = lad.rung, round(lad.beat, 4)
        s = feed.snapshot() if feed is not None else None
        g = s.grid if s is not None else None
        out["ladder"] = {
            "on": bool(r.ladder_on), "running": running, "rung": lad.rung if running else None,
            "pulses_per_min": lad.pulses_per_min if running else None, "presence": round(lad.presence, 3),
            "tempo_clear": feed.tempo_clear(now) if feed is not None else False,
            "clear_bpm": None if s is None or s.clear_bpm is None else round(s.clear_bpm, 2),
            "onset_follower": None if g is None else {
                "locked": g.locked(now), "period_ms": None if g.period is None else round(g.period * 1000.0, 1),
                "confidence": round(g.confidence, 3), "edges": g.accepted, "refits": g.refits,
                "held_to_clock": g.hint is not None, "trim_pinned": g.pinned,
                "last_edge_age_ms": None if g.last_edge is None else round((now - g.last_edge) * 1000.0, 1)}}
        sig = self.signals
        out["pivot"] = {"on": bool(r.pivot_on), "window_s": r.pivot_window_s, "min_s": r.pivot_min_s,
                        "present_at": r.pivot_present_at, "ease_s": r.pivot_ease_s,
                        "signals": {k: {**v, "shift": round(pv.shift(k), 4),
                                        "value": None if sig.get(k) is None else round(sig[k], 4)}
                                    for k, v in pv.view.items()}}


class Modulation:
    """One scene source's bindings, clock and palette hand-off (render thread)."""

    def __init__(self, runtime: SessionRuntime, props: dict[str, dict], bindings: Optional[dict] = None,
                 key: str = ""):
        self.runtime = runtime
        self.props = props
        self.key = key                            # "scene/<plug-in>": seeds section (gobo) inputs per item
        self.last: dict[str, float] = {}          # the last resolved bound values (read by the inputs API)
        self.colour_mix = 0.0                     # the colour form's mix on this item's palette
        self._own_palette: Any = None
        self._colour_k = 0.0
        self.set_bindings(bindings or {})

    def set_bindings(self, bindings: dict) -> None:
        """Render thread (UpdateParams / SlotUpdate) or before the source is shown.  v2 mappings are compiled
        here; one that does not compile is dropped (logged) and the param keeps its plain value."""
        self.bindings = dict(bindings)
        bound, mix = {}, 0.0
        for k, b in self.bindings.items():
            if not binding.is_v2(b) or not b.get("on", True) or k not in self.props:
                continue
            if "colour" in b:
                mix = float(b.get("mix", 0.0))
                continue
            try:
                old = self._bound.get(k) if hasattr(self, "_bound") else None
                bd = binding.Bound(k, self.props[k], b, self.key)
                if old is not None and old.y is not None:
                    bd.y, bd.yl = old.y, old.yl   # a swap eases from what was showing
                bound[k] = bd
            except Exception as e:
                log.warning("%s: mapping on %s dropped: %s", self.key or "?", k, e)
        self._bound = bound
        self.colour_mix = mix

    def scale_dt(self, dt: float) -> float:
        """The speed master as the scene clock (x the ensemble's motion on this screen)."""
        lv = getattr(self.runtime, "lever", None)
        return dt * self.runtime.speed if lv is None else dt * self.runtime.speed * lv["motion"]

    def palette_rows(self) -> Optional[np.ndarray]:
        """The session palette for this item (the colour form's blend included), read ``pal_shift`` along when the
        ensemble leans this screen to a palette role (t -> t + shift: d += c x shift)."""
        rows = self._palette_rows()
        lv = getattr(self.runtime, "lever", None)
        if rows is None or lv is None or not lv["pal_shift"]:
            return rows
        r = np.array(rows, np.float64)
        r[3] = r[3] + r[2] * lv["pal_shift"]
        return r

    def _palette_rows(self) -> Optional[np.ndarray]:
        rt = self.runtime
        rows = rt.palette_rows
        k = self._colour_k
        if k <= 0.0 or rt.maestro_rows is None:
            return rows
        base = rows
        if base is None:
            base = _own_rows(self._own_palette)
        if base is None:
            return rt.maestro_rows if k >= 0.5 else None
        return base + (rt.maestro_rows - base) * k

    def colour_amount(self) -> float:
        return self._colour_k

    def apply(self, values: dict) -> dict:
        if not self.bindings:
            return values
        rt = self.runtime
        sig = rt.signals
        lv = getattr(rt, "lever", None)
        depth = rt.depth if lv is None else rt.depth * lv["depth"]
        out = dict(values)
        last = {}
        for key, bd in self._bound.items():
            try:
                v = bd.step(sig, rt.avail, values.get(key), depth, rt.epoch, rt.now, rt.dt, rt.follow, rt.response)
            except Exception as e:                # a mapping never takes a frame down: drop it, keep the value
                log.warning("%s: mapping on %s failed (%s): dropped", self.key or "?", key, e)
                self._bound = {k: b for k, b in self._bound.items() if k != key}
                break
            out[key] = v
            last[key] = v
        if self.colour_mix > 0.0:
            self._own_palette = values.get("palette")
            self._colour_k = self.colour_mix * rt.colour_avail
            if "palette_mix" in self.props and isinstance(out.get("palette_mix"), (int, float)):
                out["palette_mix"] = max(out["palette_mix"], self._colour_k)
        for key, b in self.bindings.items():
            prop = self.props.get(key)
            if prop is None or "bind" not in b:
                continue
            s = sig.get(b["bind"])
            s = idle_value(b["bind"]) if s is None else min(1.0, max(0.0, float(s)))   # n/a: idle
            lo, hi = b["range"]
            rest = b["rest"]
            v = _clamp(prop, rest + depth * (lo + (hi - lo) * s - rest))
            if prop.get("type") == "integer":
                v = int(round(v))
            out[key] = v
            last[key] = v
        self.last = last
        return out

    def describe(self) -> list[dict]:
        """The bindings with their last values (and ``section_row``)."""
        return [{**binding.describe(k, b, self.last.get(k)), **self.section_row(k)} for k, b in self.bindings.items()]

    def section_row(self, key: str) -> dict:
        """A section binding's latch (``target``), whether it follows the live value now (``follows``: the last
        frame's, read off the render thread's state) and the live part's ease when it does (``follow_tau_s``: s, or
        [up, down]); {} for any other."""
        bd = getattr(self, "_bound", {}).get(key)
        if bd is None or bd.mode != "section":
            return {}
        t = bd.target
        up, down = bd.follow_taus(self.runtime.response)
        return {"target": round(t, 5) if isinstance(t, float) else t, "follows": bd.yl is not None,
                "follow_tau_s": round(up, 3) if up == down else [round(up, 3), round(down, 3)]}


_ROWS: dict = {}


def _own_rows(name: Any) -> Optional[np.ndarray]:
    """A plug-in palette name's cosine rows (plugins/_lib/palettes.py), cached; None for anything else."""
    if not isinstance(name, str):
        return None
    if name not in _ROWS:
        from .plugins._lib.palettes import PALETTES
        _ROWS[name] = np.array(PALETTES[name], np.float64) if name in PALETTES else None
    return _ROWS[name]
