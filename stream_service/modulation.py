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

The cut invariant (R1): only scene sources get a Modulation.  The ``image`` source (the anchor and exit
targets) never does, so it always renders at identity and the frame at ``hold_pts`` equals the still.

Threads: the loop thread only replaces whole values on :class:`SessionRuntime` (targets, palette): single
assignments, atomic under the GIL.  Bindings change through the engine's command queue (UpdateParams.bindings).
"""
from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np

import logging

from . import binding, music_feed, palettes, piano
from .inputs import BIND_SIGNALS, MUSIC_SIGNALS, NA_SIGNALS, TRIGGERS, Sampler, idle_value
from .plugins.base import ParamError, is_bindable

log = logging.getLogger("stream_service.modulation")
AVAIL_TAU_S = 2.0
MAESTRO_EASE_S = 3.0


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
        if self.feed is not None:               # the room's sound and beat clock (music_feed.py) over the desk's
            wf = self.feed.liveness(now)
            lead = self.sampler.lead if self.sampler is not None else 0.0
            music_feed.blend(sig, self.feed.snapshot(), wf,
                             self.clock_sig.sample(self.feed.clock(now), now + lead))
        ex = self.extra
        wm = ex.get("music_w")
        wm = w if wm is None else wm
        for k in MUSIC_SIGNALS:                 # the music features, eased to idle with their source's liveness
            v = ex.get(k)
            sig[k] = None if v is None else 0.5 + wm * (v - 0.5)
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
        self.now, self.dt = now, dt
        self.signals = sig

    def masters(self) -> dict:
        return {"speed": round(self.speed, 4), "depth": round(self.depth, 4),
                "speed_target": round(self.speed_target, 4), "depth_target": round(self.depth_target, 4)}


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
                    bd.y = old.y                  # a swap eases from what was showing
                bound[k] = bd
            except Exception as e:
                log.warning("%s: mapping on %s dropped: %s", self.key or "?", k, e)
        self._bound = bound
        self.colour_mix = mix

    def scale_dt(self, dt: float) -> float:
        """The speed master as the scene clock."""
        return dt * self.runtime.speed

    def palette_rows(self) -> Optional[np.ndarray]:
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
        depth = rt.depth
        out = dict(values)
        last = {}
        for key, bd in self._bound.items():
            try:
                v = bd.step(sig, rt.avail, values.get(key), depth, rt.epoch, rt.now, rt.dt)
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
        return [binding.describe(k, b, self.last.get(k)) for k, b in self.bindings.items()]


_ROWS: dict = {}


def _own_rows(name: Any) -> Optional[np.ndarray]:
    """A plug-in palette name's cosine rows (plugins/_lib/palettes.py), cached; None for anything else."""
    if not isinstance(name, str):
        return None
    if name not in _ROWS:
        from .plugins._lib.palettes import PALETTES
        _ROWS[name] = np.array(PALETTES[name], np.float64) if name in PALETTES else None
    return _ROWS[name]
