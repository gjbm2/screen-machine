"""
Pure functions – no Flask, no file-IO.

ambient_rgba(lat, lon) ->  {"hex": "#RRGGBB", "alpha": 0.73, "timestamp": …}
"""
from __future__ import annotations
from datetime import datetime, timezone
import math
from typing import Mapping
import importlib

from astral import LocationInfo
from astral.sun import elevation
import config

# ───────────────────────────── solar index ─────────────────────────────
def _solar_elevation(dt: datetime, lat: float, lon: float) -> float:
    """Solar elevation (degrees) using Astral's SPA."""
    # Create a location info object (name and timezone are not used for elevation calculation)
    loc = LocationInfo(latitude=lat, longitude=lon)
    # Use the elevation function from astral.sun
    return elevation(loc.observer, dt)

def _ambient_index(
    dt: datetime,
    lat: float,
    lon: float,
    cfg: Mapping = None,
) -> float:
    """
    0 (night) … 1 (full noon).  Optionally smoothed.
    """
    # Reload config each time for testing
    importlib.reload(config)
    cfg = cfg or config.INTENSITY_CFG
    
    elev = _solar_elevation(dt, lat, lon)
    raw = (elev - cfg["dusk_offset_deg"]) / cfg["elev_range_deg"]
    idx = max(0.0, min(1.0, raw))
    if cfg["smoothstep"]:
        idx = idx * idx * (3 - 2 * idx)
    return idx, elev, raw  # Return intermediate values for debugging

# ───────────────────────────── colour science ─────────────────────────
def _kelvin_to_hex(k: float) -> str:
    """
    Fast Kelvin → HEX (T. Helland approximation, ≤10 % error).
    """
    t = k / 100.0
    if t <= 66:
        r = 255
        g = 99.47 * math.log(t) - 161.12
    else:
        r = 329.7 * (t - 60) ** -0.1332
        g = 288.12 * (t - 60) ** -0.0755
    b = 0 if t <= 19 else \
        255 if t >= 66 else \
        138.52 * math.log(t - 10) - 305.04

    clamp = lambda x: max(0, min(255, int(x)))
    return f"#{clamp(r):02X}{clamp(g):02X}{clamp(b):02X}"

def _mix_kelvin(idx: float, cfg: Mapping = None) -> float:
    """
    Linear interpolation in *mired* space for perceptual uniformity.
    """
    # Reload config each time for testing
    importlib.reload(config)
    cfg = cfg or config.INTENSITY_CFG
    
    mired_warm = 1e6 / cfg["warmest_temp_K"]
    mired_cool = 1e6 / cfg["coolest_temp_K"]
    beta = cfg["beta_colour"]
    mired_now = mired_warm + (idx ** beta) * (mired_cool - mired_warm)
    return 1e6 / mired_now

# ───────────────────────────── public helper ──────────────────────────
def ambient_rgba(
    lat: float,
    lon: float,
    cfg: Mapping = None,
    now_utc: datetime | None = None,
) -> dict[str, str | float]:
    """
    Front-end payload.  Scales from the *night-floor* upward.

    α(idx) = overlay_alpha_night · (1 − idx)^γ
    """
    # Reload config each time for testing
    importlib.reload(config)
    cfg = cfg or config.INTENSITY_CFG
    
    now_utc = now_utc or datetime.utcnow().replace(tzinfo=timezone.utc)

    idx, elev, raw = _ambient_index(now_utc, lat, lon, cfg)
    alpha = cfg["overlay_alpha_night"] * (1.0 - idx) ** cfg["gamma_opacity"]
    kelvin = _mix_kelvin(idx, cfg)
    hex_colour = _kelvin_to_hex(kelvin)

    return {
        "hex": hex_colour,
        "alpha": round(alpha, 3),
        "timestamp": now_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "_debug": {  # Add debug info
            "solar_elevation": round(elev, 1),
            "raw_index": round(raw, 3),
            "smooth_index": round(idx, 3),
            "kelvin": round(kelvin),
            "alpha_raw": round(alpha, 3)
        }
    }
