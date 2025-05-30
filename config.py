"""
Configuration settings for the Screen Machine application.
This file centralizes all configuration constants and settings.
"""

import os
from pathlib import Path

# Application paths
ROOT_DIR = Path(__file__).parent
STATIC_FOLDER = 'build'
OUTPUT_DIR = 'output'

# Server settings
HOST = "0.0.0.0"
PORT = 5000
DEBUG = True
WS_PORT = 8765

# API settings
API_PREFIX = '/api'
LOG_LIMIT = 100  # Default number of logs to return

# File settings
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'webm'}
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size 

# Scheduler settings
SCHEDULER_TICK_INTERVAL = 2.0  # Seconds between scheduler trigger checks
SCHEDULER_TICK_BUFFER = 0.5    # Additional buffer time for scheduler operations 
MAX_EVENT_HISTORY = 100
# Path to the exported variables registry
VARS_REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "scheduler", "_vars.json")

"""
Global constants for the dimming / warming driver.

We now treat the darkest, warmest state as the *anchor point* and compute
everything upward from that.

Each constant carries:
    • A practical tuning range.
    • What effect moving the knob has on the picture.

"""

INTENSITY_CFG: dict[str, float | bool] = {
    # ───────────────────────────────── overlay “floor” ─────────────────────────
    #
    # overlay_alpha_night  ∈  0.60 … 0.95     default: 0.90
    #   Opacity of the RGBA mask at **deepest night** (solar index = 0).
    #   • Lower values (0.60)   → darker minimum, preserves some specular detail
    #   • Higher values (0.95)  → almost opaque, useful where total blackout is
    #                             needed but NEVER set to 1.0 (art would vanish)
    #
    "overlay_alpha_night": 0.90,

    # warmest_temp_K  ∈  1800 … 3000          default: 2200
    #   Desired white-point at night.  1800 K ≈ candle; 2200 K ≈ tungsten bulb.
    #   Too low can look orange on the Frame Pro; calibrate with the lights off.
    "warmest_temp_K": 2700,

    # coolest_temp_K  ∈  5500 … 7500          default: 6500
    #   Neutral daytime white.  6500 K ≈ D-65; Frame Pro’s “Warm 2” already sits
    #   there, so keep the default unless you *deliberately* want daylight blue.
    "coolest_temp_K": 6500,

    # ─────────────────────────────── curve shapes ─────────────────────────────
    #
    # gamma_opacity  ∈  1.0 … 2.5             default: 1.60
    #   Steepness of the dimming curve.
    #   • 1.0 gives a gentle slope (screen stays bright longer).
    #   • 2.5 drops quickly after sunset – good in museums.
    #
    "gamma_opacity": 1.60,

    # beta_colour  ∈  0.5 … 3.0              default: 0.80
    #   Power-law in *mired* space that decides **when** the image begins to warm.
    #   <1   → warms early in the evening;  >1   → stays neutral until late dusk.
    "beta_colour": 1.00,

    # smoothstep  ∈ {True, False}            default: True
    #   Apply a cubic easing to avoid a visible kink at dawn/dusk.
    "smoothstep": True,

    # ────────────────────────────── solar geometry ────────────────────────────
    #
    # dusk_offset_deg  ∈  −12 … 0            default: −6
    #   Elevation (in degrees) where the 0-point of the solar index lies.
    #   −6 ° = civil dusk;  −12 ° = nautical dusk.
    #
    "dusk_offset_deg": -4,

    # elev_range_deg  ∈  60 … 110            default: 96
    #   Range mapped onto 0 → 1.  96 ° =  (-6 ° … +90 °).
    #   Larger flattens mid-day; smaller exaggerates noon.
    "elev_range_deg": 65,

    # ───────────────────────── frontend convenience ───────────────────────────
    #
    # fetch_period_s  ∈  5 … 120             default: 60
    #   How often the browser *polls* when you do not have a real-time sensor.
    #
    "fetch_period_s": 60,
}