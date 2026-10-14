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

The cut invariant (R1): only scene sources get a Modulation.  The ``image`` source (the anchor and exit
targets) never does, so it always renders at identity and the frame at ``hold_pts`` equals the still.

Threads: the loop thread only replaces whole values on :class:`SessionRuntime` (targets, palette): single
assignments, atomic under the GIL.  Bindings change through the engine's command queue (UpdateParams.bindings).
"""
from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np

from . import palettes
from .inputs import BIND_SIGNALS, TRIGGERS, Sampler
from .plugins.base import ParamError, is_bindable


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
            if "bind" not in value:
                raise ParamError("bad_param", f"{key}: an object value must be a binding {{\"bind\": ...}}")
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
        self.signals = self.sampler.sample(now) if self.sampler is not None else {}

    def masters(self) -> dict:
        return {"speed": round(self.speed, 4), "depth": round(self.depth, 4),
                "speed_target": round(self.speed_target, 4), "depth_target": round(self.depth_target, 4)}


class Modulation:
    """One scene source's bindings, clock and palette hand-off (render thread)."""

    def __init__(self, runtime: SessionRuntime, props: dict[str, dict], bindings: Optional[dict] = None):
        self.runtime = runtime
        self.props = props
        self.bindings = dict(bindings or {})
        self.last: dict[str, float] = {}          # the last resolved bound values (read by the inputs API)

    def set_bindings(self, bindings: dict) -> None:
        self.bindings = dict(bindings)

    def scale_dt(self, dt: float) -> float:
        """The speed master as the scene clock."""
        return dt * self.runtime.speed

    def palette_rows(self) -> Optional[np.ndarray]:
        return self.runtime.palette_rows

    def apply(self, values: dict) -> dict:
        if not self.bindings:
            return values
        sig = self.runtime.signals
        depth = self.runtime.depth
        out = dict(values)
        last = {}
        for key, b in self.bindings.items():
            prop = self.props.get(key)
            if prop is None:
                continue
            s = min(1.0, max(0.0, float(sig.get(b["bind"], 0.0) or 0.0)))
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
        return [{"param": k, **b, **({"value": round(self.last[k], 5)} if k in self.last else {})}
                for k, b in self.bindings.items()]
