"""
Multiply-blend mask driver  —  sRGB space
(front end uses `mix-blend-mode: multiply`)

Returns payload:
    {
      "brightness": 0.42,        # black overlay opacity
      "warm_hex":   "#F4D1AE",   # multiply colour
      "warm_alpha": 0.035,       # opacity for multiply layer
      "blend":      "multiply",  # constant so front end knows mode
      "timestamp":  "2025-05-30T20:57:00Z"
    }
"""
from __future__ import annotations
from datetime import datetime, timezone
import math, importlib
from typing import Mapping

from astral import LocationInfo
from astral.sun import elevation, sun
import config


# ───────── helpers ────────────────────────────────────────────────────

def _solar_index(dt, lat, lon, cfg):
    """
    Day-light index ∈ [0, 1]  (single value)

    • 0   at civil dusk  (elevation = dusk_offset_deg, default -6°)
    • 1   at today's solar-noon elevation
    • Follows  sin(elevation)   ⇒ brightness tracks real-world irradiance

    Existing knobs still work:
        dusk_offset_deg   - where the curve hits 0
        elev_range_deg    - implicit in max-elevation scaling
        smoothstep        - optional cubic easing
        seasonality_factor - 0.0 = fully normalized (same curve all year)
                           1.0 = raw elevation ratio (varies with season)
    """
    loc = LocationInfo(latitude=lat, longitude=lon)

    # max elevation today (accounts for season & latitude)
    sun_today  = sun(loc.observer, date=dt.date())
    elev_max   = elevation(loc.observer, sun_today["noon"])        # °

    # current elevation
    elev_now   = elevation(loc.observer, dt)                       # °

    # linear fraction 0-1  (same shift & scale as before)
    lin = (elev_now - cfg["dusk_offset_deg"]) / max(
        1e-6, elev_max - cfg["dusk_offset_deg"]
    )
    lin = max(0.0, min(1.0, lin))   # clamp

    # Get seasonality factor (0.0 = normalized, 1.0 = raw)
    seasonality = cfg.get("seasonality_factor", 0.0)
    
    if seasonality > 0:
        # Mix between normalized and raw elevation ratio
        # At seasonality=0: use normalized curve (current behavior)
        # At seasonality=1: use raw elevation ratio
        raw_ratio = (elev_now - cfg["dusk_offset_deg"]) / max(1e-6, 90 - cfg["dusk_offset_deg"])
        raw_ratio = max(0.0, min(1.0, raw_ratio))
        lin = (1 - seasonality) * lin + seasonality * raw_ratio

    # irradiance-weighted fraction using sine curve
    # (sin 0° = 0  →  sin maxElev = 1)
    idx = math.sin(lin * math.pi / 2)      # pi/2 puts lin=1 => sin=1

    # optional easing
    if cfg["smoothstep"]:
        idx = idx * idx * (3 - 2 * idx)

    return idx, sun_today["noon"]            # return noon for bias calc


def _kelvin_to_linear(k):
    """Kelvin → linear-RGB (0-1) using Planckian locus fit."""
    t = k / 100.0
    if t <= 66:
        r = 1.0
        g = (99.4708 * math.log(t) - 161.1196) / 255
    else:
        r = (329.6987 * (t - 60) ** -0.133205) / 255
        g = (288.12217 * (t - 60) ** -0.075515) / 255
    if t <= 19:
        b = 0.0
    elif t >= 66:
        b = 1.0
    else:
        b = (138.51773 * math.log(t - 10) - 305.04479) / 255
    clamp = lambda x: max(0.0, min(1.0, x))
    return tuple(map(clamp, (r, g, b)))


def _lin_to_srgb(c):                # gamma-encode
    return 12.92*c if c <= 0.0031308 else 1.055*pow(c, 1/2.4) - 0.055


def _kelvin_to_srgb(k):
    return tuple(_lin_to_srgb(c) for c in _kelvin_to_linear(k))


def _rgb_to_hex(rgb):
    r, g, b = (round(x * 255) for x in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


# Panel reference white (Warm 2 ≈ D65)
_REF_WHITE = _kelvin_to_srgb(6500.0)


# ───────── public API ─────────────────────────────────────────────────

def compute_mask(
    lat: float | None,
    lon: float | None,
    cfg: Mapping | None = None,
    now_utc: datetime | None = None,
    time: datetime | None = None,
    screen_cfg: Mapping | None = None,  # Per-screen overrides from publish-destinations.json
) -> dict[str, str | float]:
    # Force fresh load of config
    import importlib.util
    from routes.utils import _load_json_once
    
    spec = importlib.util.spec_from_file_location("config", "config.py")
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)
    
    # If screen_cfg provided, load its config
    if screen_cfg:
        dests = {dest["id"]: dest for dest in _load_json_once("destination", "publish-destinations.json")}
        dest = dests.get(screen_cfg.get("id"))
        if dest:
            screen_cfg = dest.get("intensity_cfg", {})
            if screen_cfg.get("adjust", False):
                lat = float(screen_cfg["lat"])
                lon = float(screen_cfg["lon"])
    
    # Use provided time or current time
    now = time or now_utc or datetime.utcnow().replace(tzinfo=timezone.utc)
    
    # Get solar index
    idx, solar_noon = _solar_index(now, lat, lon, config.INTENSITY_CFG)
    
    # ── NEW  orientation bias  ───────────────────────────────────────
    bias = config.INTENSITY_CFG.get("east_west_bias", 0.0)          # Range -1 to 1: -1 = west-facing, +1 = east-facing
    hours_from_noon = (now - solar_noon).total_seconds() / 3600.0
    
    # Apply skewing transformation using a smooth, continuous function
    # For east-facing (bias > 0): morning curve is convex (rises faster), afternoon is concave (falls faster)
    # For west-facing (bias < 0): opposite effect
    if hours_from_noon < 0:  # Morning
        # East-facing screens get more light in morning
        # Map bias [-1,1] to power [0.2, 1.8] using a smooth curve
        # At bias=0: power=1.0 (no effect)
        # At bias=±1: power=0.2 or 1.8 (maximum effect)
        power = 1.0 - 0.8 * (abs(bias) ** 0.5) * (1 if bias > 0 else -1)  # Square root gives more gradual effect
    else:  # Afternoon  
        # East-facing screens get less light in afternoon
        power = 1.0 + 0.8 * (abs(bias) ** 0.5) * (1 if bias > 0 else -1)  # Square root gives more gradual effect
    
    idx = idx ** power
    idx = max(0.0, min(1.0, idx))    # clamp back

    # ── brightness  ──────────────────────────────────────────────────
    brightness = config.INTENSITY_CFG["night_floor"] + (1 - config.INTENSITY_CFG["night_floor"]) * idx

    # ── colour temperature  ──────────────────────────────────────────
    mired_night = 1e6 / config.INTENSITY_CFG["warmest_temp_K"]
    mired_day   = 1e6 / config.INTENSITY_CFG["coolest_temp_K"]
    mired_now   = mired_day + (1 - idx) ** config.INTENSITY_CFG["beta_colour"] * (mired_night - mired_day)
    kelvin_now  = 1e6 / mired_now
    target_rgb  = _kelvin_to_srgb(kelvin_now)

    gain = tuple(min(1.0, t / w) for t, w in zip(target_rgb, _REF_WHITE))

    warm_alpha_max = config.INTENSITY_CFG.get("warm_alpha_max", 0.07)
    warm_alpha = warm_alpha_max * (1 - idx) ** config.INTENSITY_CFG["beta_colour"]
    warm_hex   = _rgb_to_hex(gain)

    return {
        "brightness": round(brightness, 3),
        "warm_hex":   warm_hex,
        "warm_alpha": round(warm_alpha, 3),
        "blend":      "multiply",
        "timestamp":  now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "_debug": {
            "idx_base":   round(idx, 3),
            "idx_skewed": round(idx, 3),
            "kelvin_now": round(kelvin_now),
            "brightness": round(brightness, 3),
            "warm_alpha": round(warm_alpha, 3),
            "bias":       bias,
            "power":      round(power, 3),
            "elev":       elevation(LocationInfo(latitude=lat, longitude=lon).observer, now),
            "solar_noon": solar_noon,
            "noon_elev":  elevation(LocationInfo(latitude=lat, longitude=lon).observer, solar_noon),
            "hours_from_noon": round(hours_from_noon, 2),
        },
    }


def test_mask_hourly(lat: float = 51.5074, lon: float = -0.1278, dest_id: str = None):
    """
    Display a table showing mask brightness by hour of day for today.
    Default coordinates are London, UK.
    
    Usage from console:
        from routes.display_utils import test_mask_hourly
        test_mask_hourly()  # Uses London
        test_mask_hourly(40.7128, -74.0060)  # NYC
        test_mask_hourly(dest_id="north-screen")  # Use specific screen config
    """
    from datetime import timedelta
    import importlib.util
    from routes.utils import _load_json_once
    
    # Force fresh load of config
    spec = importlib.util.spec_from_file_location("config", "config.py")
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)
    
    # If dest_id provided, load its config
    screen_cfg = None
    if dest_id:
        dests = {dest["id"]: dest for dest in _load_json_once("destination", "publish-destinations.json")}
        dest = dests.get(dest_id)
        if not dest:
            print(f"Warning: Destination '{dest_id}' not found")
        else:
            screen_cfg = dest.get("intensity_cfg", {})
            if not screen_cfg.get("adjust", False):
                print(f"Warning: Destination '{dest_id}' has intensity adjustment disabled")
            else:
                lat = float(screen_cfg["lat"])
                lon = float(screen_cfg["lon"])
                print(f"\nUsing configuration for {dest_id}:")
                print(f"  • Location: {lat}°N, {lon}°E")
                print(f"  • East-west bias: {screen_cfg.get('east_west_bias', 0.0)}")
                print(f"  • Seasonality: {screen_cfg.get('seasonality_factor', 1.0)}")
    
    # Start of today in UTC
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    
    # Get solar noon info from first mask call
    first_mask = compute_mask(lat, lon, screen_cfg=screen_cfg, time=today)
    solar_noon = first_mask['_debug']['solar_noon']
    noon_elev = first_mask['_debug']['noon_elev']
    
    print(f"\nSolar noon: {solar_noon} | Elevation: {noon_elev:.2f}")
    
    # Display config values
    print("\nConfiguration:")
    print(f"  • Night floor: {config.INTENSITY_CFG['night_floor']:.2f}")
    print(f"  • Gamma brightness: {config.INTENSITY_CFG['gamma_brightness']:.2f}")
    print(f"  • Beta colour: {config.INTENSITY_CFG['beta_colour']:.2f}")
    print(f"  • Warmest temp: {config.INTENSITY_CFG['warmest_temp_K']}K")
    print(f"  • Coolest temp: {config.INTENSITY_CFG['coolest_temp_K']}K")
    print(f"  • Warm alpha max: {config.INTENSITY_CFG.get('warm_alpha_max', 0.07):.2f}")
    print(f"  • Dusk offset: {config.INTENSITY_CFG['dusk_offset_deg']}°")
    print(f"  • Smoothstep: {config.INTENSITY_CFG['smoothstep']}")
    
    # Header
    print(f"\nMask brightness by hour for {today.strftime('%Y-%m-%d')} UTC at lat={lat}, lon={lon}")
    print("-" * 120)
    print(f"{'Time':>6} | {'Brightness':>10} | {'Idx Base':>9} | {'Idx Skew':>9} | {'Power':>6} | {'Solar Elev°':>11} | {'Kelvin':>6} | {'Warm Alpha':>10} | {'Hours from Noon':>15}")
    print("-" * 120)
    
    day_brightness = []
    night_brightness = []
    
    for hour in range(24):
        test_time = today.replace(hour=hour, tzinfo=timezone.utc)
        mask = compute_mask(lat, lon, screen_cfg=screen_cfg, time=test_time)
        
        print(f"{hour:02d}:00 | {mask['brightness']:>10.3f} | {mask['_debug']['idx_base']:>9.3f} | {mask['_debug']['idx_skewed']:>9.3f} | {mask['_debug']['power']:>6.3f} | {mask['_debug']['elev']:>11.1f} | {mask['_debug']['kelvin_now']:>6} | {mask['warm_alpha']:>10.3f} | {mask['_debug']['hours_from_noon']:>15.2f}")
        
        # Collect for averages using values from mask
        if mask['_debug']['elev'] > 0:
            day_brightness.append(mask['brightness'])
        if mask['_debug']['elev'] <= -6:
            night_brightness.append(mask['brightness'])
    
    print("-" * 120)
    
    # Add summary
    if day_brightness:
        avg_day = sum(day_brightness) / len(day_brightness)
        print(f"\nAverage daytime brightness: {avg_day:.3f}")
    if night_brightness:
        avg_night = sum(night_brightness) / len(night_brightness)
        print(f"Average nighttime brightness: {avg_night:.3f}")


def test_mask_throughout_year(lat: float = 51.5074, lon: float = -0.1278):
    """
    Test mask behavior at key times throughout the year.
    Verifies that brightness is high during day and low at night.
    """
    # Test dates: solstices, equinoxes
    test_dates = [
        (datetime(2025, 3, 20), "Spring Equinox"),
        (datetime(2025, 6, 21), "Summer Solstice"),
        (datetime(2025, 9, 23), "Autumn Equinox"),
        (datetime(2025, 12, 21), "Winter Solstice"),
    ]
    
    print(f"\nMask behavior throughout year at lat={lat}, lon={lon}")
    print("-" * 80)
    print(f"{'Date':>20} | {'Noon Bright':>11} | {'Night Bright':>12} | {'Noon OK':>7} | {'Night OK':>8}")
    print("-" * 80)
    
    all_pass = True
    
    for date, name in test_dates:
        # Test at noon and midnight
        noon = date.replace(hour=12, tzinfo=timezone.utc)
        midnight = date.replace(hour=0, tzinfo=timezone.utc)
        
        noon_mask = compute_mask(lat, lon, time=noon)
        midnight_mask = compute_mask(lat, lon, time=midnight)
        
        noon_ok = noon_mask['brightness'] > 0.9
        night_ok = midnight_mask['brightness'] < 0.1
        
        print(f"{name:>20} | {noon_mask['brightness']:>11.3f} | {midnight_mask['brightness']:>12.3f} | {'✓' if noon_ok else '✗':>7} | {'✓' if night_ok else '✗':>8}")
        
        if not (noon_ok and night_ok):
            all_pass = False
    
    print("-" * 80)
    print(f"\nOverall test: {'PASS ✓' if all_pass else 'FAIL ✗'}")


def test_mask_at_time(time_str: str, lat: float = 51.5074, lon: float = -0.1278):
    """
    Test mask at a specific time.
    
    Usage:
        test_mask_at_time("2025-06-01 08:47:26")  # Your example time
        test_mask_at_time("2025-06-01 14:00:00")  # Afternoon
        test_mask_at_time("2025-06-01 23:00:00")  # Night
    """
    from datetime import datetime
    
    # Parse the time string
    test_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
    test_time = test_time.replace(tzinfo=timezone.utc)
    
    mask = compute_mask(lat, lon, time=test_time)
    elev = elevation(LocationInfo(latitude=lat, longitude=lon).observer, test_time)
    
    print(f"\nMask at {time_str} UTC (lat={lat}, lon={lon})")
    print("-" * 50)
    print(f"Solar elevation:  {elev:.1f}°")
    print(f"Solar index:      {mask['_debug']['idx_skewed']:.3f}")
    print(f"Brightness:       {mask['brightness']:.3f}")
    print(f"Color temp:       {mask['_debug']['kelvin_now']}K")
    print(f"Warm hex:         {mask['warm_hex']}")
    print(f"Warm alpha:       {mask['warm_alpha']:.3f}")
    print("-" * 50)
    
    # Interpretation
    if mask['brightness'] > 0.9:
        print("Status: Daytime (minimal dimming)")
    elif mask['brightness'] < 0.1:
        print("Status: Nighttime (maximum dimming)")
    else:
        print("Status: Twilight/transition period")


if __name__ == "__main__":
    # Run tests when module is executed directly
    print("Testing mask logic...")
    test_mask_hourly()
    print("\n" + "="*70 + "\n")
    test_mask_throughout_year()
