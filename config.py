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

These settings can be overridden on a per-screen basis in publish-destinations.json
by adding an intensity_cfg object to any screen entry. For example:

    {
      "id": "north-screen",
      "intensity_cfg": {
        "lat": 51.5074,
        "lon": -0.1278,
        "east_west_bias": 0.5,
        "adjust": true
      }
    }

Only parameters that exist in this base config can be overridden.
"""

INTENSITY_CFG = {
    # ────────────── BRIGHTNESS CURVE ──────────────

    "night_floor": 0.03,
    # Minimum brightness at night. This controls the maximum opacity
    # of the black overlay used to darken the image.
    # 
    # Meaning: when solar_index = 0 (deepest night), the image will be
    # dimmed to 3% of its original brightness.
    # 
    # Range: 0.02–0.40 typical.
    # - Lower = darker screen at night (may crush blacks or make artwork illegible).
    # - Higher = retains more luminance at night (less restful, but preserves detail).
    # - 0.03 = very dark at night, good for bedrooms.
    # - 0.10 = moderate dimming, good for living rooms.
    # - 0.20 = subtle dimming, good for work areas.

    "gamma_brightness": 1.0,
    # Exponential steepness of the brightness dimming curve:
    # brightness = night_floor × (1 - solar_index)^gamma_brightness
    #
    # Controls *when* during the day dimming becomes perceptible.
    #
    # Range: 1.2–2.5 typical.
    # - Lower = gentle slope, screen starts dimming early in the afternoon.
    # - Higher = screen stays bright longer, then dims sharply after sunset.
    # - 1.0 = linear dimming (may feel too sudden).
    # - 1.5 = gradual transition (good default).
    # - 2.0 = stays bright until near sunset, then dims quickly.

    # ────────────── COLOUR TEMPERATURE CURVE ──────────────

    "warmest_temp_K": 2200,
    # The target white-point in Kelvin at night.
    #
    # Meaning: the system will gradually shift the white-point from
    # 6500K (neutral daylight) to 2200K (warm tungsten bulb) as solar_index falls.
    #
    # Range: 1800–2600 K.
    # - Lower = more amber/orange warmth at night (like a candle).
    # - Higher = more neutral whites even at night.
    # - 1800K = very warm, candle-like (may be too orange).
    # - 2200K = warm tungsten bulb (good default).
    # - 2600K = slightly warm, like a halogen bulb.

    "coolest_temp_K": 6500,
    # The daytime white-point of your display in Kelvin.
    #
    # Should match the panel's native white in your chosen picture mode.
    # Samsung "Movie" mode with Warm 2 usually lands near 6500K (D65).
    #
    # Range: 5500–7500 K.
    # - Lower = slightly warm even during day.
    # - Higher = cooler, more blue-tinted during day.
    # - 5500K = warm daylight.
    # - 6500K = standard D65 (good default).
    # - 7500K = cool daylight.

    "beta_colour": 1.2,
    # Curve steepness for warming effect (colour temp shift).
    #
    # This controls how quickly the screen warms as it gets darker.
    # Operates in mired space (1e6 / K) to match human perception.
    #
    # Range: 0.8–2.0 typical.
    # - Lower = warmth starts earlier in the afternoon.
    # - Higher = warmth stays subtle until near twilight, then ramps up.
    # - 0.8 = gradual warming throughout day.
    # - 1.2 = moderate transition (good default).
    # - 2.0 = stays cool until near sunset, then warms quickly.

    "warm_alpha_max": 0.10,
    # Maximum opacity of the warmth overlay at night.
    #
    # Used as a multiplier on the colour-blend layer:
    # warm_alpha = warm_alpha_max × (1 - solar_index)^beta_colour
    #
    # Range: 0.03–0.15 typical.
    # - Lower = more subtle hue shift.
    # - Higher = stronger colour cast (risk of orange/sepia at night).
    # - 0.03 = very subtle warmth.
    # - 0.07 = moderate warmth (good default).
    # - 0.15 = strong warmth (may be too orange).

    # ────────────── SOLAR GEOMETRY ──────────────

    "dusk_offset_deg": -6,
    # The elevation (in degrees above horizon) at which the solar index hits 0.
    #
    # -6° = civil twilight (sun just below the horizon).
    # This sets the "night" anchor for both brightness and warmth curves.
    #
    # Range: -4 to -12 degrees.
    # - -4° = astronomical twilight (starts warm/dim earlier).
    # - -6° = civil twilight (good default).
    # - -8° = nautical twilight (starts warm/dim later).
    # - -12° = deep night (may be too late for some locations).

    "elev_range_deg": 96,
    # Total angular elevation range mapped to solar_index [0–1].
    #
    # Example: -6° to +90° = 96°, so:
    #  - 90° = solar_index = 1 (sun overhead)
    #  - -6° = solar_index = 0 (dusk)
    #
    # Range: 90–100 degrees typical.
    # - Lower = faster transitions at dawn/dusk.
    # - Higher = more gradual transitions.
    # - 90° = standard range (good default).
    # - 96° = extended range (more gradual).
    # - 100° = very gradual transitions.

    # ────────────── EASING FUNCTION ──────────────

    "smoothstep": False,
    # Apply cubic smoothing (Hermite interpolation) to solar_index
    #
    # Smoothstep formula: x²(3−2x)
    # - Makes transitions into and out of twilight feel gradual, not abrupt.
    # - False = linear fade (may feel too sudden).
    # - True = smoother transitions (good for most cases).

    # ────────────── EAST-WEST ADJUSTMENT ──────────────

    "east_west_bias": 0.0,
    # Adjusts the timing of brightness/warmth based on screen orientation.
    #
    # Range: -1.0 to +1.0.
    # - +1.0 = east-facing: bright mornings, dim afternoons.
    # - 0.0 = neutral (good default).
    # - -1.0 = west-facing: dim mornings, bright afternoons.
    #
    # The effect is subtle and gradual, using a power curve to avoid
    # sudden changes. The bias affects both brightness and warmth timing.

    "seasonality_factor": 0.1,
    # Controls how much the solar curve varies with seasons.
    #
    # Range: 0.0 to 1.0.
    # - 0.0 = normalized curve (same behavior year-round).
    # - 1.0 = raw elevation ratio (varies with seasons).
    #
    # At 0.0: The system uses a normalized curve that maintains consistent
    #         brightness/warmth timing throughout the year, regardless of
    #         seasonal changes in daylight hours.
    #
    # At 1.0: The system follows the raw solar elevation, which means:
    #         - Summer: longer bright periods, later dimming
    #         - Winter: shorter bright periods, earlier dimming
    #
    # Values in between mix these behaviors. 1.0 is recommended for
    # most locations to maintain natural seasonal variation.

    "default_lat": 51.5074,  # London
    "default_lon": -0.1278,  # London
}