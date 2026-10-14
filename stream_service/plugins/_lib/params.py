"""Parameter helpers (``basic=True`` marks a headline param the simple Room page shows; 1-3 per plug-in): every parameter declares its *tier* and, when DMX/director may drive it, how.

* ``x-tier``: ``"realtime"`` (may change every frame from DMX: must never jump or flicker), ``"regional"``
  (art-director level, changes every ~10-30 s and eases: density, symmetry, style, palette), ``"structural"``
  (set by the preset only: seeds, mode enums whose change is a discontinuity), ``"quality"`` (render_scale).
* ``x-bindable``: ``"level"`` (value set from a 0..1 signal, smoothed), ``"rate"`` (a speed the plug-in
  integrates, so position never jumps), ``"trigger"`` (expects a fast 0..1 envelope, e.g. the beat).

Every scene exposes the STANDARD realtime macro interface (:func:`standard_scene_params`) so the binding layer
can drive any scene the same way: energy <- Maestro activity, pulse <- beat, speed <- director pace,
intensity <- master, drift_speed <- colour pace.
"""
from __future__ import annotations

from typing import Optional

from stream_service.plugins._lib.palettes import PALETTES


def num(lo: float, hi: float, default: float, label: str, *, step: float = 0.01, unit: Optional[str] = None,
        tier: str = "regional", bind: Optional[str] = None, integer: bool = False, basic: bool = False) -> dict:
    ui = {"label": label, "step": 1 if integer else step, "widget": "slider"}
    if basic:
        ui["basic"] = True
    if unit:
        ui["unit"] = unit
    d = {"type": "integer" if integer else "number", "minimum": lo, "maximum": hi, "default": default,
         "x-ui": ui, "x-tier": tier}
    if bind:
        d["x-bindable"] = bind
    return d


def enum(values, default, label: str, *, tier: str = "structural", basic: bool = False) -> dict:
    ui = {"label": label, "widget": "select"}
    if basic:
        ui["basic"] = True
    return {"type": "string", "enum": list(values), "default": default, "x-ui": ui, "x-tier": tier}


def flag(default: bool, label: str, *, tier: str = "regional") -> dict:
    return {"type": "boolean", "default": default, "x-ui": {"label": label}, "x-tier": tier}


def palette_params(default: str = "neon", period: float = 1.0, drift: float = 0.03) -> dict:
    return {
        "palette": enum(PALETTES, default, "Palette", tier="regional", basic=True),
        "palette_period": num(0.05, 10.0, period, "Palette period", tier="regional"),
        "drift_speed": num(-2.0, 2.0, drift, "Colour drift", unit="/s", tier="realtime", bind="rate"),
    }


def standard_scene_params(*, palette: Optional[str] = "neon", render_scale: float = 0.5,
                          period: float = 1.0, drift: float = 0.03) -> dict:
    """The macro interface every scene exposes (plus its own specific params)."""
    d = {
        "energy": num(0.0, 1.0, 0.5, "Energy", tier="realtime", bind="level"),
        "pulse": num(0.0, 1.0, 0.0, "Pulse (beat envelope)", tier="realtime", bind="trigger"),
        "speed": num(0.0, 4.0, 1.0, "Speed", unit="×", tier="realtime", bind="rate"),
        "intensity": num(0.0, 2.0, 1.0, "Intensity", tier="realtime", bind="level"),
        "render_scale": num(0.25, 1.0, render_scale, "Render scale", step=0.05, tier="quality"),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }
    if palette:
        d.update(palette_params(palette, period, drift))
    return d


def standard_effect_params() -> dict:
    return {
        "amount": num(0.0, 1.0, 0.5, "Amount", tier="realtime", bind="level", basic=True),
        "pulse": num(0.0, 1.0, 0.0, "Pulse (beat envelope)", tier="realtime", bind="trigger"),
    }
