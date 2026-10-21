"""Optional room config (room.prod.yaml) settings for light-sensor ingest (LIVE_V1 §2.2b, DCL v3.1 §4.4).

Every key is optional. Ingest is on by default and needs no sensor keys (owner ruling, 14 Oct):
any relay may send readings. `enabled: false` turns the sensor path off, and the mask then comes
from Flask exactly as before. `sensors_file` is still read but no longer gates anything.

```yaml
ingest:
  enabled: true                 # false: no sensor path at all (today's Flask-only pusher)
  sensors_file: ~/.config/screen-machine/room_sensors.json   # {sensor_id: key}, 0600
  mask_source: auto             # every screen's default: auto | sensor | flask
  screens: {north-screen: flask}   # per-screen mask_source
  filter_file: config/light.json   # lightmask FilterParams ("filter" object), reloaded on change
  filter: {stale_after_s: 10}      # FilterParams overrides on top of filter_file (not reloaded)
  lightmask: {root: ., config_py: config.py, intensity_json: routes/data/intensity.json,
              destinations_json: src/data/publish-destinations.json}   # default: Flask's own files
  overrides_poll_s: 2.0         # Flask GET maskstate + brightness-override, per screen on the sensor path
  eval_interval_s: 1.0          # re-evaluate every screen (solar curve, stale ramp, tier changes)
  config_refresh_s: 2.0         # lightmask.ConfigWatcher.refresh(), in the executor
  ack_min_interval_s: 0.2       # lux.ack coalescing (every reading is acked within this)
  max_readings_per_s: 20        # DCL v3.1 §7: readings beyond this in a 1 s window are dropped
  max_msgs_per_s: 50            # any frames beyond this in a 1 s window: close 4029
  hello_timeout_s: 10           # close 4002 without a hello
  idle_timeout_s: 60            # close 4001 when an authenticated relay sends nothing this long
  ws_heartbeat_s: 20            # aiohttp ping/pong (half-open TCP detection)
  flask_fail_after_s: 10        # auto: sensor stale *and* Flask failing this long -> lightmask's stale ramp
  latency_window: 512           # samples kept for the p50/p95 in /api/v1/status
  persist_every_s: 30           # filter state -> <state_dir>/sensors.json (warm start)
mask:                           # the shaper (DCL v3.1 §4.4 puts its constants under mask:)
  snap_transition_ms: 200
  drift_transition_ms: 1000
  drift_interval_s: 3.0
  drift_deadband_brightness: 0.015
  drift_deadband_warm_alpha: null    # null: mask.deadband_warm_alpha
  snap_delta_brightness: 0.05
  max_sets_per_s: 10
  live_max_sets_per_s: 2
  source_change_transition_ms: 2000
```

`mask_source` per screen:

* `auto` (default): the sensor while it is fresh (a reading in the last `stale_after_s`),
  else Flask's `/api/<id>/mask` (the interim fetch); else hold the last mask. If the sensor is
  stale and Flask has failed for `flask_fail_after_s`, lightmask's own stale ramp to the
  solar curve is used instead of holding.
* `sensor`: lightmask is the only writer (DCL v3.1 §4.4). Flask's `/mask` is never fetched
  for the screen; a stale sensor ramps to the solar curve over `stale_ramp_s`.
* `flask`: today's behaviour; the sensor never writes this screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SENSORS_FILE = Path("~/.config/screen-machine/room_sensors.json")
DEFAULT_FILTER_FILE = REPO_ROOT / "config" / "light.json"
MASK_SOURCES = ("auto", "sensor", "flask")


@dataclass
class ShaperConfig:
    snap_transition_ms: int = 200
    drift_transition_ms: int = 1000
    drift_interval_s: float = 3.0
    drift_deadband_brightness: float = 0.015
    drift_deadband_warm_alpha: float | None = None    # None: deadband_warm_alpha
    snap_delta_brightness: float = 0.05
    max_sets_per_s: float = 10.0
    live_max_sets_per_s: float = 2.0
    source_change_transition_ms: int = 2000
    # The existing mask deadbands (mask.deadband_*), copied in by the parser: a snap or a
    # Flask value must move at least this much to be sent.
    deadband_brightness: float = 0.005
    deadband_warm_alpha: float = 0.002
    flask_transition_ms: int = 200                    # mask.transition_ms (the Flask path)

    @property
    def drift_alpha(self) -> float:
        return self.deadband_warm_alpha if self.drift_deadband_warm_alpha is None else self.drift_deadband_warm_alpha


@dataclass
class IngestConfig:
    enabled: bool = True
    sensors_file: Path = field(default_factory=lambda: DEFAULT_SENSORS_FILE.expanduser())
    mask_source: str = "auto"
    screen_sources: dict[str, str] = field(default_factory=dict)
    filter_file: Path | None = DEFAULT_FILTER_FILE
    filter: dict[str, Any] = field(default_factory=dict)
    lightmask_root: Path | None = None
    config_py: Path | None = None
    intensity_json: Path | None = None
    destinations_json: Path | None = None
    overrides_poll_s: float = 2.0
    overrides_timeout_s: float = 2.0
    eval_interval_s: float = 1.0
    config_refresh_s: float = 2.0
    ack_min_interval_s: float = 0.2
    max_readings_per_s: int = 20
    max_msgs_per_s: int = 50
    max_pending_sockets: int = 16
    hello_timeout_s: float = 10.0
    idle_timeout_s: float = 60.0
    ws_heartbeat_s: float = 20.0
    flask_fail_after_s: float = 10.0
    latency_window: int = 512
    persist_every_s: float = 30.0
    shaper: ShaperConfig = field(default_factory=ShaperConfig)

    def source_for(self, screen: str) -> str:
        return self.screen_sources.get(screen, self.mask_source)


_SCALARS = {"overrides_poll_s", "overrides_timeout_s", "eval_interval_s", "config_refresh_s",
            "ack_min_interval_s", "max_readings_per_s", "max_msgs_per_s", "max_pending_sockets",
            "hello_timeout_s", "idle_timeout_s", "ws_heartbeat_s", "flask_fail_after_s",
            "latency_window", "persist_every_s"}


def _path(v: Any, base_dir: Path) -> Path | None:
    if v is None:
        return None
    p = Path(str(v)).expanduser()
    return p if p.is_absolute() else (base_dir / p)


def _source(v: Any, where: str) -> str:
    s = str(v).strip().lower()
    if s not in MASK_SOURCES:
        raise ValueError(f"{where}: mask_source must be one of {', '.join(MASK_SOURCES)}, not {v!r}")
    return s


def ingest_config_from_dict(raw: dict[str, Any] | None, *, base_dir: Path | None = None) -> IngestConfig:
    """Parse the `ingest:` section and the shaper keys of `mask:` from the room config's root dict."""
    raw = raw or {}
    base_dir = base_dir or REPO_ROOT
    ing = raw.get("ingest") or {}
    if not isinstance(ing, dict):
        raise ValueError("ingest: must be a mapping")
    out = IngestConfig()
    out.enabled = bool(ing.get("enabled", True))
    if ing.get("sensors_file") is not None:
        out.sensors_file = _path(ing["sensors_file"], base_dir)
    out.mask_source = _source(ing.get("mask_source", "auto"), "ingest.mask_source")
    for sid, v in (ing.get("screens") or {}).items():
        val = v.get("mask_source") if isinstance(v, dict) else v
        if val is not None:
            out.screen_sources[str(sid)] = _source(val, f"ingest.screens.{sid}")
    if "filter_file" in ing:
        out.filter_file = _path(ing["filter_file"], base_dir)
    out.filter = dict(ing.get("filter") or {})
    lm = ing.get("lightmask") or {}
    out.lightmask_root = _path(lm.get("root"), base_dir)
    out.config_py = _path(lm.get("config_py"), base_dir)
    out.intensity_json = _path(lm.get("intensity_json"), base_dir)
    out.destinations_json = _path(lm.get("destinations_json"), base_dir)
    for k in _SCALARS:
        if k in ing:
            cur = getattr(out, k)
            setattr(out, k, int(ing[k]) if isinstance(cur, int) else float(ing[k]))
    m = raw.get("mask") or {}
    sh = ShaperConfig()
    names = {f.name: f for f in fields(ShaperConfig)}
    for k, v in m.items():
        if k not in names or k in ("deadband_brightness", "deadband_warm_alpha", "flask_transition_ms"):
            continue
        if v is None:
            if k == "drift_deadband_warm_alpha":
                sh.drift_deadband_warm_alpha = None
            continue
        setattr(sh, k, int(v) if k.endswith("_ms") else float(v))
    sh.deadband_brightness = float(m.get("deadband_brightness", sh.deadband_brightness))
    sh.deadband_warm_alpha = float(m.get("deadband_warm_alpha", sh.deadband_warm_alpha))
    sh.flask_transition_ms = int(m.get("transition_ms", sh.flask_transition_ms))
    if sh.max_sets_per_s <= 0 or sh.live_max_sets_per_s <= 0 or sh.drift_interval_s < 0:
        raise ValueError("mask: max_sets_per_s and live_max_sets_per_s must be > 0, drift_interval_s >= 0")
    out.shaper = sh
    return out
