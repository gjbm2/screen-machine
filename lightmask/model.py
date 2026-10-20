"""lightmask.model: the light -> mask mapping, ported from Flask.

This is a faithful port of today's production logic:

* ``routes/display_utils.py``  ``compute_mask`` and its helpers
  (``_solar_index``, ``_kelvin_to_*``, ``_lin_to_srgb``, ``_rgb_to_hex``,
  ``_REF_WHITE``),
* ``routes/lightsensor.py``    ``IntensityMapper.get_intensity`` (the
  piecewise-linear lux -> intensity curve only; see "Smoothing" below),
* ``routes/display.py``        the ``GET /api/<id>/mask`` override order
  (brightness override, then maskoff, then ``adjust``),
* ``routes/utils.py``          ``get_destinations_for_group`` and where
  ``_load_json_once`` finds ``publish-destinations.json``.

Everything here is a pure function of its arguments, apart from the file
reads in ``load_config`` / ``ConfigWatcher`` (and their state lives on the
watcher instance, never in module globals). Nothing imports Flask or
``routes.*``.

Smoothing is deliberately NOT ported. Flask's ``get_intensity`` applies a
5-sample moving average over ``lux_history`` and an alpha=0.2 EMA that
advances on every *read* (from every caller, shared across screens). Here
``intensity_for`` is the bare curve; time-based smoothing is
``lightmask.filter.LuxFilter``.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time as _time
import types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Sequence

from astral import LocationInfo
from astral.sun import elevation
from astral.sun import noon as _astral_noon

REPO_ROOT = Path(__file__).resolve().parent.parent

# ``source`` values. "sensor", "astral" and "manual_override" are the strings
# Flask already returns. Flask returns no ``source`` for maskoff or for
# ``adjust: false``; lightmask names them so callers can tell them apart.
SOURCE_SENSOR = "sensor"
SOURCE_SOLAR = "astral"          # Flask's name for the solar curve (DCL calls it "solar")
SOURCE_OVERRIDE = "manual_override"
SOURCE_MASKOFF = "maskoff"
SOURCE_FIXED = "fixed"           # intensity_cfg.adjust is false or missing
SOURCE_BLEND = "blend"           # sensor <-> solar ramp in progress (stale sensor)

NEUTRAL_HEX = "#FFFFFF"


# ───────── colour helpers (verbatim from routes/display_utils.py) ─────────

def _kelvin_to_linear(k):
    """Kelvin -> linear-RGB (0-1) using Planckian locus fit."""
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
    clamp = lambda x: max(0.0, min(1.0, x))  # noqa: E731
    return tuple(map(clamp, (r, g, b)))


def _lin_to_srgb(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * pow(c, 1 / 2.4) - 0.055


def _kelvin_to_srgb(k):
    return tuple(_lin_to_srgb(c) for c in _kelvin_to_linear(k))


def _rgb_to_hex(rgb):
    r, g, b = (round(x * 255) for x in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


# Panel reference white (Warm 2 ~ D65). A constant, computed once.
_REF_WHITE = _kelvin_to_srgb(6500.0)


def _colour(cfg: Mapping, level: float):
    """Warm tint for a 0..1 level (sensor intensity or solar index).

    Same expressions, in the same order, as both branches of compute_mask.
    Returns (warm_hex, warm_alpha_unrounded, kelvin_now).
    """
    mired_night = 1e6 / cfg["warmest_temp_K"]
    mired_day = 1e6 / cfg["coolest_temp_K"]
    mired_now = mired_day + (1 - level) ** cfg["beta_colour"] * (mired_night - mired_day)
    kelvin_now = 1e6 / mired_now
    target_rgb = _kelvin_to_srgb(kelvin_now)
    gain = tuple(min(1.0, t / w) for t, w in zip(target_rgb, _REF_WHITE))
    warm_alpha_max = cfg.get("warm_alpha_max", 0.07)
    warm_alpha = warm_alpha_max * (1 - level) ** cfg["beta_colour"]
    return _rgb_to_hex(gain), warm_alpha, kelvin_now


# ───────── solar curve (verbatim maths from routes/display_utils.py) ──────

def _observer(lat, lon):
    return LocationInfo(latitude=lat, longitude=lon).observer


def _solar_index(dt, lat, lon, cfg):
    """Day-light index in [0, 1] and today's solar noon.

    Identical to Flask's ``_solar_index`` except that it calls
    ``astral.sun.noon`` instead of ``astral.sun.sun(...)["noon"]``. The value
    is the same (``sun()`` calls ``noon()`` with the same date and UTC
    tzinfo); ``sun()`` also computes dawn/sunrise/sunset/dusk, which are
    unused and raise ValueError at latitudes where the sun never sets or
    rises (Flask would 500 there; lightmask does not).
    """
    obs = _observer(lat, lon)
    solar_noon = _astral_noon(obs, dt.date())
    elev_max = elevation(obs, solar_noon)
    elev_now = elevation(obs, dt)

    lin = (elev_now - cfg["dusk_offset_deg"]) / max(1e-6, elev_max - cfg["dusk_offset_deg"])
    lin = max(0.0, min(1.0, lin))

    seasonality = cfg.get("seasonality_factor", 0.0)
    if seasonality > 0:
        raw_ratio = (elev_now - cfg["dusk_offset_deg"]) / max(1e-6, 90 - cfg["dusk_offset_deg"])
        raw_ratio = max(0.0, min(1.0, raw_ratio))
        lin = (1 - seasonality) * lin + seasonality * raw_ratio

    idx = math.sin(lin * math.pi / 2)
    if cfg["smoothstep"]:
        idx = idx * idx * (3 - 2 * idx)
    return idx, solar_noon


def _solar_core(cfg: Mapping, lat, lon, now: datetime) -> dict:
    """Solar branch of compute_mask: everything after "Get solar index"."""
    idx, solar_noon = _solar_index(now, lat, lon, cfg)
    idx_base = idx

    bias = cfg.get("east_west_bias", 0.0)
    hours_from_noon = (now - solar_noon).total_seconds() / 3600.0
    if hours_from_noon < 0:
        power = 1.0 - 0.8 * (abs(bias) ** 0.5) * (1 if bias > 0 else -1)
    else:
        power = 1.0 + 0.8 * (abs(bias) ** 0.5) * (1 if bias > 0 else -1)
    idx = idx ** power

    stretch = cfg.get("day_stretch", 0.0)
    if stretch == 0:
        gamma_str = 1.0
    else:
        gamma_str = 10 ** (-stretch)
    idx = idx ** gamma_str
    idx = max(0.0, min(1.0, idx))

    brightness = cfg["night_floor"] + (1 - cfg["night_floor"]) * idx
    warm_hex, warm_alpha, kelvin_now = _colour(cfg, idx)
    return {
        "brightness": round(brightness, 3),
        "warm_hex": warm_hex,
        "warm_alpha": round(warm_alpha, 3),
        "_debug": {
            "idx_base": round(idx_base, 3),
            "idx_skewed": round(idx, 3),
            "kelvin_now": round(kelvin_now),
            "brightness": round(brightness, 3),
            "warm_alpha": round(warm_alpha, 3),
            "bias": bias,
            "power": round(power, 3),
            "gamma_str": round(gamma_str, 3),
            "stretch": stretch,
            "solar_noon": solar_noon,
            "hours_from_noon": round(hours_from_noon, 2),
        },
    }


def _sensor_core(cfg: Mapping, intensity) -> dict:
    """Sensor branch of compute_mask. Uses the *base* INTENSITY_CFG.

    Flask merges a screen's intensity_cfg only on the solar path, so the
    sensor path's colour constants never see per-screen keys. Brightness is
    the curve intensity, unrounded, exactly as Flask returns it.
    """
    warm_hex, warm_alpha, kelvin_now = _colour(cfg, intensity)
    return {
        "brightness": intensity,
        "warm_hex": warm_hex,
        "warm_alpha": round(warm_alpha, 3),
        "kelvin_now": round(kelvin_now),
    }


def merged_cfg(base: Mapping, intensity_cfg: Mapping | None) -> dict:
    """Per-screen intensity_cfg merged over INTENSITY_CFG, as compute_mask does.

    Only keys already in the base are taken; a key whose base value is a bool
    is cast with bool(), everything else with float(). ``adjust``, ``lat`` and
    ``lon`` are not base keys and are ignored here.
    """
    cfg = dict(base)
    for key, value in (intensity_cfg or {}).items():
        if key in cfg:
            if isinstance(cfg[key], bool):
                cfg[key] = bool(value)
            else:
                cfg[key] = float(value)
    return cfg


def _as_utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _flask_timestamp(now: datetime) -> str:
    return now.isoformat(timespec="seconds").replace("+00:00", "Z")


# ───────── lux -> intensity curve (routes/lightsensor.py, minus smoothing) ─

def curve_intensity(points: Sequence[Sequence[float]], lux: float):
    """Piecewise-linear lux -> intensity over (lux, intensity) points sorted by lux.

    Same search as ``IntensityMapper.get_intensity``: the first segment with
    x0 <= lux <= x1 wins; below the first point -> first intensity; above the
    last -> last intensity. Differences, only where Flask raises: an empty
    curve returns None (Flask: IndexError) and a zero-width segment is
    skipped (Flask: ZeroDivisionError).
    """
    if not points:
        return None
    for i in range(len(points) - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]
        if x0 <= lux <= x1:
            if x1 == x0:
                continue
            return y0 + (y1 - y0) * (lux - x0) / (x1 - x0)
    if lux <= points[0][0]:
        return points[0][1]
    return points[-1][1]


# ───────── config snapshot ────────────────────────────────────────────────

def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value):
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


@dataclass(frozen=True)
class SensorMapping:
    name: str
    target_group: str | None
    points: tuple          # ((lux, intensity), ...) sorted by lux (stable, like Flask)


@dataclass(frozen=True)
class LightConfig:
    """Immutable snapshot of the three config inputs. Treat it as read-only."""

    intensity_cfg: Mapping[str, Any]                 # config.INTENSITY_CFG
    sensor_mappings: Mapping[str, SensorMapping]     # routes/data/intensity.json, file order
    destinations: tuple                              # publish-destinations.json entries
    sources: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def from_data(cls, intensity_cfg: Mapping, intensity_mapping: Mapping | None,
                  destinations: Iterable[Mapping], sources: Mapping[str, str] | None = None) -> "LightConfig":
        mappings: dict[str, SensorMapping] = {}
        for name, m in ((intensity_mapping or {}).get("sensor_mappings") or {}).items():
            pts = sorted(((p["lux"], p["intensity"]) for p in (m.get("lux_to_intensity") or [])),
                         key=lambda p: p[0])
            mappings[name] = SensorMapping(name=name, target_group=m.get("target_group"), points=tuple(pts))
        return cls(intensity_cfg=_freeze(dict(intensity_cfg)),
                   sensor_mappings=MappingProxyType(mappings),
                   destinations=tuple(_freeze(dict(d)) for d in destinations),
                   sources=MappingProxyType(dict(sources or {})))

    def destination(self, dest_id: str) -> Mapping | None:
        """Flask builds {d["id"]: d}, so the last entry with an id wins."""
        found = None
        for d in self.destinations:
            if d["id"] == dest_id:
                found = d
        return found

    def destination_dict(self, dest_id: str) -> dict | None:
        d = self.destination(dest_id)
        return None if d is None else _thaw(d)


# ───────── config loading ─────────────────────────────────────────────────

# Where Flask's findfile("publish-destinations.json") looks, relative to the
# process cwd (Flask runs from the repo root) and to routes/ (SCRIPT_DIR).
# The first existing file wins; today that is src/data/publish-destinations.json.
_DEST_SEARCH = (
    ".", "src/data", "routes/overlays", "routes/data",
    "routes/data", "routes/workflows", "routes/sysprompts",
    "routes/src/workflows", "routes/src/data/workflows",
    ".", "routes",
)
DESTINATIONS_FILENAME = "publish-destinations.json"


def find_destinations_file(root: Path | str = REPO_ROOT) -> Path | None:
    """Mirror of routes.utils.findfile("publish-destinations.json") with cwd=root."""
    root = Path(root)
    for rel in _DEST_SEARCH:
        p = root / rel / DESTINATIONS_FILENAME
        if p.exists():
            return p.resolve()
    home = Path(os.path.expanduser("~")) / DESTINATIONS_FILENAME
    if home.exists():
        return home.resolve()
    return None


@dataclass(frozen=True)
class ConfigPaths:
    root: Path = REPO_ROOT
    config_py: Path | None = None          # default <root>/config.py
    intensity_json: Path | None = None     # default <root>/routes/data/intensity.json
    destinations_json: Path | None = None  # default: Flask's findfile search from <root>

    def resolve(self) -> tuple[Path, Path, Path | None]:
        root = Path(self.root)
        cfg = Path(self.config_py) if self.config_py else root / "config.py"
        inten = Path(self.intensity_json) if self.intensity_json else root / "routes" / "data" / "intensity.json"
        dests = Path(self.destinations_json) if self.destinations_json else find_destinations_file(root)
        return cfg, inten, dests


def read_intensity_cfg(config_py: Path | str) -> dict:
    """Execute config.py in an isolated namespace and return INTENSITY_CFG.

    compute_mask does ``spec_from_file_location("config", "config.py")`` +
    ``exec_module`` on every call. This executes the same source the same way
    (a fresh module named "config", never put in sys.modules) but compiles it
    by hand, so no __pycache__ file is written next to config.py. Only
    INTENSITY_CFG is read from the result.

    TODO(config/light.json): DCL §2.6 / REFACTOR_PLAN P4b plan to move these
    constants to config/light.json (reloaded on mtime) and drop the exec of
    config.py. When that lands, read them here and keep this as a fallback.
    """
    path = Path(config_py)
    source = path.read_bytes()
    module = types.ModuleType("config")
    module.__file__ = str(path)
    # dont_inherit=True: compile() otherwise applies *this* module's
    # ``from __future__ import annotations`` to config.py, which exec_module
    # (Flask's way) never does.
    code = compile(source, str(path), "exec", dont_inherit=True)
    exec(code, module.__dict__)  # noqa: S102 (repo-owned file)
    cfg = getattr(module, "INTENSITY_CFG")
    if not isinstance(cfg, Mapping):
        raise TypeError(f"{path}: INTENSITY_CFG is not a mapping")
    return dict(cfg)


def read_intensity_mapping(path: Path | str) -> dict | None:
    """routes/data/intensity.json. A missing file means no sensor mappings
    (IntensityMapper leaves mapping_data None, so every screen is solar)."""
    p = Path(path)
    if not p.exists():
        return None
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_destinations(path: Path | str | None) -> list:
    if path is None:
        raise FileNotFoundError(f"Could not locate file: {DESTINATIONS_FILENAME}")
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def load_config(paths: ConfigPaths | None = None) -> LightConfig:
    """Read INTENSITY_CFG, intensity.json and publish-destinations.json once."""
    cfg_py, inten, dests = (paths or ConfigPaths()).resolve()
    return LightConfig.from_data(
        read_intensity_cfg(cfg_py),
        read_intensity_mapping(inten),
        read_destinations(dests),
        sources={"config_py": str(cfg_py), "intensity_json": str(inten), "destinations_json": str(dests)},
    )


def _file_sig(path: Path | None):
    if path is None:
        return None
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return ("missing",)
    return (st.st_mtime_ns, st.st_size)


class ConfigWatcher:
    """A LightConfig that reloads whichever input file's mtime (or size) changed.

    * ``current``: the last good snapshot; never touches the disk.
    * ``refresh()``: stats the three files and re-reads the changed ones. Run
      it in an executor from asyncio code (it may exec config.py).
    * ``get()``: ``refresh()`` at most every ``min_interval_s``, then ``current``.

    A file that fails to parse (for example while the LightSense editor is
    writing it) keeps the previous good snapshot and sets ``last_error``.
    Flask differs here: a bad intensity.json disables the sensor path and a
    bad publish-destinations.json makes the mask route 500.
    """

    def __init__(self, paths: ConfigPaths | None = None, *, min_interval_s: float = 1.0,
                 clock: Callable[[], float] = _time.monotonic) -> None:
        self.paths = paths or ConfigPaths()
        self.min_interval_s = min_interval_s
        self._clock = clock
        self._lock = threading.Lock()
        self._sigs: dict[str, Any] = {}
        self._parts: dict[str, Any] = {}
        self._current: LightConfig | None = None
        self._last_check: float | None = None
        self.last_error: str | None = None
        self.reloads = 0

    @property
    def current(self) -> LightConfig | None:
        return self._current

    def get(self) -> LightConfig:
        now = self._clock()
        if self._current is None or self._last_check is None or now - self._last_check >= self.min_interval_s:
            self.refresh()
        if self._current is None:
            raise RuntimeError(f"lightmask config unavailable: {self.last_error}")
        return self._current

    def refresh(self) -> bool:
        """Reload changed files. Returns True when a new snapshot was built."""
        with self._lock:
            self._last_check = self._clock()
            cfg_py, inten, dests = self.paths.resolve()
            files = {"config_py": cfg_py, "intensity_json": inten, "destinations_json": dests}
            sigs = {k: (str(p), _file_sig(p)) for k, p in files.items()}
            if self._current is not None and sigs == self._sigs:
                return False
            readers = {"config_py": read_intensity_cfg, "intensity_json": read_intensity_mapping,
                       "destinations_json": read_destinations}
            parts = dict(self._parts)
            try:
                for key, path in files.items():
                    if key not in parts or sigs[key] != self._sigs.get(key):
                        parts[key] = readers[key](path)
                snap = LightConfig.from_data(parts["config_py"], parts["intensity_json"],
                                             parts["destinations_json"],
                                             sources={k: str(v) for k, v in files.items()})
            except Exception as e:  # keep the last good snapshot
                self.last_error = f"{type(e).__name__}: {e}"
                return False
            self._parts, self._sigs, self._current = parts, sigs, snap
            self.last_error = None
            self.reloads += 1
            return True


# ───────── targets ───────────────────────────────────────────────────────

def destinations_for_group(destinations: Iterable[Mapping], scope: str) -> list[str]:
    """routes.utils.get_destinations_for_group over a destinations list."""
    destinations = list(destinations)
    if scope == "global":
        return [d["id"] for d in destinations if d.get("has_bucket", False)]
    for d in destinations:
        if d["id"] == scope and d.get("has_bucket", False):
            return [scope]
    return [d["id"] for d in destinations
            if scope in d.get("groups", []) and d.get("has_bucket", False)]


def resolve_targets(config: LightConfig, sensor: str) -> list[str]:
    """Destination ids driven by ``sensor`` (its target_group, resolved like Flask).

    Unknown sensor or empty target_group -> []. A destination in the list may
    still have ``adjust`` off (it then gets the neutral mask).
    """
    m = config.sensor_mappings.get(sensor)
    if m is None or not m.target_group:
        return []
    return destinations_for_group(config.destinations, m.target_group)


def sensors_for_dest(config: LightConfig, dest_id: str) -> list[str]:
    """Sensors whose targets include dest_id, in intensity.json order
    (compute_mask uses the first one that has a reading)."""
    return [name for name in config.sensor_mappings if dest_id in resolve_targets(config, name)]


def intensity_for(config: LightConfig, sensor: str, lux: float):
    """Curve intensity for ``sensor`` at ``lux`` (no smoothing); None if unmapped."""
    m = config.sensor_mappings.get(sensor)
    if m is None:
        return None
    return curve_intensity(m.points, lux)


# ───────── overrides ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Overrides:
    """Flask's per-screen manual state (routes/display.py).

    mask_enabled: False after POST /api/<id>/maskoff (True after maskon; the default).
    brightness:   set by POST /api/<id>/brightness-override; wins over maskoff.
    """

    mask_enabled: bool = True
    brightness: float | None = None

    @classmethod
    def coerce(cls, value: "Overrides | Mapping | None") -> "Overrides":
        if value is None:
            return cls()
        if isinstance(value, Overrides):
            return value
        if isinstance(value, Mapping):
            enabled = value.get("mask_enabled", value.get("enabled", True))
            if value.get("maskoff"):
                enabled = False
            b = value.get("brightness", value.get("brightness_override"))
            return cls(mask_enabled=bool(enabled), brightness=None if b is None else float(b))
        raise TypeError(f"overrides must be Overrides, a mapping or None, not {type(value).__name__}")

    @classmethod
    def from_flask(cls, maskstate: Mapping | None = None, brightness_override: Mapping | None = None) -> "Overrides":
        """From the bodies of GET /api/<id>/maskstate and GET /api/<id>/brightness-override."""
        enabled = True if maskstate is None else bool(maskstate.get("enabled", True))
        b = None
        if brightness_override is not None and brightness_override.get("override") is not None:
            b = float(brightness_override["override"])
        return cls(mask_enabled=enabled, brightness=b)


# ───────── the mapping ───────────────────────────────────────────────────

def _neutral(brightness, source):
    return {"brightness": brightness, "warm_hex": NEUTRAL_HEX, "warm_alpha": 0.0, "source": source}


def _dest_latlon(base: Mapping, icfg: Mapping):
    # Flask's mask route does float(intensity_cfg["lat"/"lon"]) (KeyError -> 500
    # if missing). lightmask falls back to INTENSITY_CFG default_lat/default_lon.
    if "lat" in icfg and "lon" in icfg:
        return float(icfg["lat"]), float(icfg["lon"])
    return float(base.get("default_lat", 51.5074)), float(base.get("default_lon", -0.1278))


def mask_for(dest_cfg: Mapping, intensity: float | None, now: datetime | None = None,
             overrides: Overrides | Mapping | None = None, *, base_cfg: Mapping) -> dict:
    """Final mask for one destination, as GET /api/<id>/mask computes it.

    dest_cfg   the destination's publish-destinations.json entry
    intensity  curve intensity from that screen's sensor, or None for the solar branch
    now        datetime (naive = UTC; converted to UTC)
    overrides  Overrides / mapping / None
    base_cfg   INTENSITY_CFG (``LightConfig.intensity_cfg``)

    Returns {brightness, warm_hex, warm_alpha, source}. Values (and rounding)
    match Flask: the solar branch rounds brightness and warm_alpha to 3 dp; the
    sensor branch rounds warm_alpha only and returns the intensity unrounded.

    The caller decides whether ``intensity`` applies to this destination (see
    ``resolve_targets`` / ``mask_for_dest``); mask_for does not check groups.
    """
    ov = Overrides.coerce(overrides)
    if ov.brightness is not None:
        return _neutral(float(ov.brightness), SOURCE_OVERRIDE)
    if not ov.mask_enabled:
        return _neutral(1.0, SOURCE_MASKOFF)
    icfg = dest_cfg.get("intensity_cfg") or {}
    if not icfg.get("adjust", False):
        return _neutral(1.0, SOURCE_FIXED)
    if intensity is not None:
        core = _sensor_core(base_cfg, intensity)
        return {"brightness": core["brightness"], "warm_hex": core["warm_hex"],
                "warm_alpha": core["warm_alpha"], "source": SOURCE_SENSOR}
    cfg = merged_cfg(base_cfg, icfg)
    lat, lon = _dest_latlon(base_cfg, icfg)
    core = _solar_core(cfg, lat, lon, _as_utc(now))
    return {"brightness": core["brightness"], "warm_hex": core["warm_hex"],
            "warm_alpha": core["warm_alpha"], "source": SOURCE_SOLAR}


def solar_mask(config: LightConfig, dest_id: str, now: datetime | None = None) -> dict | None:
    """The solar-curve mask for a destination, ignoring sensors and overrides
    (what a stale sensor ramps to). None for an unknown destination."""
    dest = config.destination(dest_id)
    if dest is None:
        return None
    return mask_for(dest, None, now, None, base_cfg=config.intensity_cfg)


def _hex_rgb(h: str):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def blend_masks(sensor_mask: Mapping, solar_mask: Mapping, solar_weight: float) -> dict:
    """Linear ramp between two masks; solar_weight 0 -> sensor, 1 -> solar.

    Brightness and warm_alpha are blended linearly, warm_hex per sRGB channel.
    (New behaviour: Flask has no staleness ramp.)
    """
    w = max(0.0, min(1.0, float(solar_weight)))
    if w <= 0.0:
        return dict(sensor_mask)
    if w >= 1.0:
        return dict(solar_mask)
    a, b = sensor_mask, solar_mask
    ra, ga, ba = _hex_rgb(a["warm_hex"])
    rb, gb, bb = _hex_rgb(b["warm_hex"])
    lerp = lambda x, y: x + (y - x) * w  # noqa: E731
    return {
        "brightness": round(lerp(float(a["brightness"]), float(b["brightness"])), 4),
        "warm_hex": "#{:02X}{:02X}{:02X}".format(round(lerp(ra, rb)), round(lerp(ga, gb)), round(lerp(ba, bb))),
        "warm_alpha": round(lerp(float(a["warm_alpha"]), float(b["warm_alpha"])), 4),
        "source": SOURCE_BLEND,
        "solar_weight": round(w, 3),
    }


@dataclass(frozen=True)
class SensorInput:
    """One sensor's contribution to mask_for_dest.

    intensity     curve intensity of the filtered lux (None = no reading yet)
    solar_weight  LuxFilter.solar_weight(t): 0 = fresh, 1 = fully stale
    """

    intensity: float | None
    solar_weight: float = 0.0


def mask_for_dest(config: LightConfig, dest_id: str, now: datetime | None = None,
                  overrides: Overrides | Mapping | None = None,
                  sensors: Mapping[str, "SensorInput | float | None"] | None = None) -> dict | None:
    """compute_mask's whole decision for one destination id.

    ``sensors`` maps sensor name -> SensorInput (or a bare intensity). As in
    compute_mask, the first sensor in intensity.json order whose target group
    contains the destination and that has a reading drives it; with none, the
    solar curve does. A stale sensor (solar_weight > 0) is blended towards the
    solar mask. Returns None for an unknown destination (Flask: 404).
    """
    dest = config.destination(dest_id)
    if dest is None:
        return None
    base = config.intensity_cfg
    ov = Overrides.coerce(overrides)
    icfg = dest.get("intensity_cfg") or {}
    if ov.brightness is not None or not ov.mask_enabled or not icfg.get("adjust", False):
        return mask_for(dest, None, now, ov, base_cfg=base)

    chosen = None
    for name in config.sensor_mappings:
        raw = (sensors or {}).get(name)
        if raw is None:
            continue
        inp = raw if isinstance(raw, SensorInput) else SensorInput(intensity=float(raw))
        if inp.intensity is None:
            continue
        if dest_id in resolve_targets(config, name):
            chosen = (name, inp)
            break
    if chosen is None:
        return mask_for(dest, None, now, ov, base_cfg=base)

    name, inp = chosen
    m_sensor = mask_for(dest, inp.intensity, now, ov, base_cfg=base)
    m_sensor["sensor"] = name
    w = max(0.0, min(1.0, float(inp.solar_weight)))
    if w <= 0.0:
        return m_sensor
    m_solar = mask_for(dest, None, now, ov, base_cfg=base)
    if w >= 1.0:
        return m_solar
    out = blend_masks(m_sensor, m_solar, w)
    out["sensor"] = name
    return out


# ───────── drop-in for Flask's compute_mask (full payload incl. _debug) ───

SensorReader = Callable[[str], "tuple[float | None, float] | None"]


def flask_compute_mask(config: LightConfig, lat, lon, cfg=None, now_utc=None, time=None,
                       screen_cfg: Mapping | None = None, *,
                       sensor_intensity: SensorReader | None = None) -> dict:
    """Same signature and payload as routes.display_utils.compute_mask.

    ``sensor_intensity(sensor_name)`` returns ``(intensity, latest_lux)`` or
    None when the sensor has no reading. It is called lazily, in the same
    order and under the same conditions as Flask calls
    ``intensity_mapper.get_intensity``, so Flask can keep its EMA bit-for-bit
    by passing a reader built on lux_history + intensity_mapper, or pass a
    LuxFilter-backed reader to switch smoothing. ``cfg`` is ignored (as in
    Flask). Times are used as given (Flask always passes aware UTC).
    """
    base = config.intensity_cfg
    now = time or now_utc or datetime.now(timezone.utc)
    stamp = _flask_timestamp(now)

    if screen_cfg:
        dests = {d["id"]: d for d in config.destinations}
        dest = dests.get(screen_cfg.get("id"))
        if dest:
            intensity_cfg = dest.get("intensity_cfg", {})
            if not intensity_cfg.get("adjust", False):
                return {
                    "brightness": 1.0, "warm_hex": NEUTRAL_HEX, "warm_alpha": 0.0,
                    "blend": "multiply", "timestamp": stamp,
                    "_debug": {
                        "idx_base": 1.0, "idx_skewed": 1.0, "kelvin_now": base["coolest_temp_K"],
                        "brightness": 1.0, "warm_alpha": 0.0, "bias": 0.0, "power": 1.0,
                        "gamma_str": 1.0, "stretch": 0.0,
                        "elev": elevation(_observer(lat, lon), now),
                        "solar_noon": None, "noon_elev": None, "hours_from_noon": 0.0,
                    },
                }
            for sensor_name, mapping in config.sensor_mappings.items():
                if not mapping.target_group:
                    continue
                if dest["id"] not in destinations_for_group(config.destinations, mapping.target_group):
                    continue
                reading = sensor_intensity(sensor_name) if sensor_intensity else None
                if reading is None:
                    continue
                target_intensity, latest_lux = reading
                if target_intensity is None:
                    continue
                core = _sensor_core(base, target_intensity)
                return {
                    "brightness": core["brightness"], "warm_hex": core["warm_hex"],
                    "warm_alpha": core["warm_alpha"], "blend": "multiply", "timestamp": stamp,
                    "source": SOURCE_SENSOR,
                    "sensor": {"name": sensor_name, "lux": latest_lux},
                    "_debug": {
                        "idx_base": target_intensity, "idx_skewed": target_intensity,
                        "kelvin_now": core["kelvin_now"], "brightness": target_intensity,
                        "warm_alpha": core["warm_alpha"], "bias": 0.0, "power": 1.0,
                        "gamma_str": 1.0, "stretch": 0.0,
                        "elev": elevation(_observer(lat, lon), now),
                        "solar_noon": None, "noon_elev": None, "hours_from_noon": 0.0,
                        "sensor_name": sensor_name, "lux": latest_lux,
                    },
                }
            base = merged_cfg(base, intensity_cfg)
            if "lat" in intensity_cfg and "lon" in intensity_cfg:
                lat = float(intensity_cfg["lat"])
                lon = float(intensity_cfg["lon"])

    core = _solar_core(base, lat, lon, now)
    dbg = core["_debug"]
    return {
        "brightness": core["brightness"], "warm_hex": core["warm_hex"], "warm_alpha": core["warm_alpha"],
        "blend": "multiply", "timestamp": stamp, "source": SOURCE_SOLAR,
        "_debug": {
            "idx_base": dbg["idx_base"], "idx_skewed": dbg["idx_skewed"], "kelvin_now": dbg["kelvin_now"],
            "brightness": dbg["brightness"], "warm_alpha": dbg["warm_alpha"], "bias": dbg["bias"],
            "power": dbg["power"], "gamma_str": dbg["gamma_str"], "stretch": dbg["stretch"],
            "elev": elevation(_observer(lat, lon), now),
            "solar_noon": dbg["solar_noon"],
            "noon_elev": elevation(_observer(lat, lon), dbg["solar_noon"]),
            "hours_from_noon": dbg["hours_from_noon"],
        },
    }


def flask_mask_route(config: LightConfig, dest_id: str, now: datetime | None = None,
                     overrides: Overrides | Mapping | None = None, *,
                     sensor_intensity: SensorReader | None = None) -> dict | None:
    """Body of GET /api/<dest_id>/mask (routes/display.py ``mask``), minus jsonify.

    None for an unknown destination (Flask aborts 404). Kept for golden
    parity and for a later Flask switch; room_service uses mask_for_dest.
    """
    dest = config.destination(dest_id)
    if dest is None:
        return None
    now = now or datetime.now(timezone.utc)
    ov = Overrides.coerce(overrides)
    if ov.brightness is not None:
        return {"brightness": ov.brightness, "warm_hex": NEUTRAL_HEX, "warm_alpha": 0.0,
                "source": SOURCE_OVERRIDE, "timestamp": _flask_timestamp(now)}
    icfg = dest.get("intensity_cfg", {})
    if not ov.mask_enabled or not icfg.get("adjust", False):
        return {"brightness": 1.0, "warm_hex": NEUTRAL_HEX, "warm_alpha": 0.0,
                "timestamp": _flask_timestamp(now)}
    lat = float(icfg["lat"])
    lon = float(icfg["lon"])
    payload = flask_compute_mask(config, lat, lon, screen_cfg={**_thaw(icfg), "id": dest_id},
                                 now_utc=now, sensor_intensity=sensor_intensity)
    payload.pop("_debug", None)
    return payload
