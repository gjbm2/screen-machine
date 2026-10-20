"""SensorService: wiring of the shared `lightmask` package (LIVE_V1 §2.2b, DCL v3.1 §4.4).

No filter or mapping code lives here; it follows lightmask/README.md's room_service block:

* one `lightmask.LuxFilter` per sensor (log-lux snap 0.15 s / drift 2.5 s, the spike guard,
  `held`, `seq`, staleness, warm start from `<state_dir>/sensors.json`);
* `lightmask.ConfigWatcher` over Flask's own files (INTENSITY_CFG from `config.py`,
  `routes/data/intensity.json`, `publish-destinations.json`), refreshed by the caller in an
  executor, so the LightSense editor keeps working;
* `lightmask.mask_for_dest` per screen: sensor -> target_group -> destinations exactly as
  Flask resolves them, then overrides, `adjust`, the sensor curve or the solar curve;
* overrides (`maskon`/`maskoff`/`brightness-override`) read from Flask's existing GET
  endpoints, `GET /api/<id>/maskstate` and `GET /api/<id>/brightness-override` (read-only).

Times: `t` is always room_service's own monotonic receive time (the relay's `mono_ms` is
another host's clock); the solar curve uses wall-clock UTC from `wall()`.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import aiohttp

import lightmask as lm

from .util import describe_exc, read_json

if TYPE_CHECKING:
    from .config import RoomConfig
    from .ingest_config import IngestConfig

log = logging.getLogger("room_service.sensors")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _finite(x: Any) -> bool:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    try:
        return math.isfinite(float(x))
    except OverflowError:
        return False


@dataclass(frozen=True)
class SensorView:
    """What the arbiter needs to know about the sensor that drives a screen."""
    driver: str | None      # the sensor mask_for_dest would use (first in intensity.json order with a value)
    live_seen: bool         # that sensor has had an accepted reading in this process
    fresh: bool             # ... within stale_after_s
    held: bool              # LuxFilter.is_held(t): a calibration capture holds it


class SensorService:
    def __init__(self, cfg: "RoomConfig", icfg: "IngestConfig", *, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], datetime] = utcnow, state_path: Path | None = None) -> None:
        self.cfg = cfg
        self.icfg = icfg
        self.clock = clock
        self.wall = wall
        paths = lm.ConfigPaths(root=icfg.lightmask_root or lm.REPO_ROOT, config_py=icfg.config_py,
                               intensity_json=icfg.intensity_json, destinations_json=icfg.destinations_json)
        self.watcher = lm.ConfigWatcher(paths, min_interval_s=0.0, clock=clock)
        self._fp_watcher = lm.FilterParamsWatcher(icfg.filter_file) if icfg.filter_file else None
        self.params = self._params()
        self.filters: dict[str, lm.LuxFilter] = {}
        self.last_reading: dict[str, float] = {}      # accepted (real) readings in this process
        self.last_raw: dict[str, dict] = {}            # last reading as received, per sensor
        self.rejected: dict[str, dict[str, int]] = {}
        self.overrides: dict[str, lm.Overrides] = {}
        self._ov_attempt: dict[str, float] = {}        # clock time of the last completed poll attempt
        self._ov_error: dict[str, str] = {}
        self.state_path = state_path
        raw = read_json(state_path, {}) if state_path else {}
        self._persisted: dict[str, Any] = dict((raw or {}).get("filters") or {}) if isinstance(raw, dict) else {}
        self._targets_key: Any = None
        self._targets: dict[str, list[str]] = {}
        self._dest_sensors: dict[str, list[str]] = {}
        self.config_error_logged: str | None = None

    # -- config ------------------------------------------------------------------
    def _params(self) -> lm.FilterParams:
        base = asdict(self._fp_watcher.get()) if self._fp_watcher else {}
        return lm.FilterParams.from_mapping({**base, **self.icfg.filter})

    def read_config(self) -> tuple[bool, lm.FilterParams]:
        """Blocking (it may exec config.py): run it in an executor. Returns (the mapping config
        changed, the filter params now on disk); apply the params on the loop (apply_params)."""
        changed = self.watcher.refresh()            # thread-safe; swaps the snapshot atomically
        if self.watcher.last_error and self.watcher.last_error != self.config_error_logged:
            log.warning("lightmask config: %s (keeping the last good snapshot)", self.watcher.last_error)
        self.config_error_logged = self.watcher.last_error
        try:
            params = self._params()
        except (TypeError, ValueError) as e:
            log.warning("lightmask filter params: %s (keeping the current ones)", e)
            params = self.params
        return changed, params

    def apply_params(self, params: lm.FilterParams) -> bool:
        """On the event loop: swap every filter's constants (state carries over)."""
        if params == self.params:
            return False
        self.params = params
        for f in self.filters.values():
            f.set_params(params)
        log.info("lightmask filter params reloaded")
        return True

    def refresh_config(self) -> bool:
        """read_config + apply_params in one call (tests, or no event loop)."""
        changed, params = self.read_config()
        return self.apply_params(params) or changed

    @property
    def config(self) -> lm.LightConfig | None:
        return self.watcher.current

    def _index(self) -> None:
        """sensor -> configured screens it drives, and screen -> its sensors (intensity.json order)."""
        cfg = self.config
        if cfg is self._targets_key:
            return
        self._targets_key = cfg
        self._targets, self._dest_sensors = {}, {}
        if cfg is None:
            return
        for name in cfg.sensor_mappings:
            self._targets[name] = [d for d in lm.resolve_targets(cfg, name) if d in self.cfg.screens]
        for sid in self.cfg.screens:
            self._dest_sensors[sid] = [n for n in cfg.sensor_mappings if sid in self._targets.get(n, [])]

    def targets(self, sensor: str) -> list[str]:
        self._index()
        return list(self._targets.get(sensor, []))

    def sensors_for(self, screen: str) -> list[str]:
        self._index()
        return list(self._dest_sensors.get(screen, []))

    # -- filters -----------------------------------------------------------------
    def filter_for(self, sensor: str) -> lm.LuxFilter:
        f = self.filters.get(sensor)
        if f is None:
            f = lm.LuxFilter.from_state(self._persisted.get(sensor), start_t=self.clock(),
                                        params=self.params, sensor=sensor)
            self.filters[sensor] = f
        return f

    def reset_sequence(self, sensor: str) -> None:
        """A relay said hello: its seq may restart (calib-3 seeds it from the wall clock)."""
        self.filter_for(sensor).reset_sequence()

    def ingest(self, sensor: str, lux: Any, seq: Any, held: bool, t: float) -> lm.SampleResult:
        f = self.filter_for(sensor)
        r = f.sample(lux, t, seq=seq, held=held)
        ok_num = _finite(lux)
        self.last_raw[sensor] = {"lux": lux if ok_num else None,
                                 "seq": seq, "held": held, "accepted": r.accepted, "reason": r.reason}
        if r.accepted:
            self.last_reading[sensor] = t
        else:
            by = self.rejected.setdefault(sensor, {})
            by[r.reason or "?"] = by.get(r.reason or "?", 0) + 1
        return r

    def driver(self, screen: str) -> str | None:
        for name in self.sensors_for(screen):
            f = self.filters.get(name)
            if f is not None and f.lux is not None:
                return name
        return None

    def view(self, screen: str, t: float) -> SensorView:
        name = self.driver(screen)
        if name is None:
            return SensorView(None, False, False, False)
        f = self.filters[name]
        last = self.last_reading.get(name)
        return SensorView(name, last is not None,
                          last is not None and (t - last) <= self.params.stale_after_s, f.is_held(t))

    def mask(self, screen: str, t: float, *, pure: bool = False) -> dict | None:
        """lightmask's mask for `screen` now. `pure`: the sensor branch without the staleness
        blend (the arbiter only asks for that while the driving sensor is fresh)."""
        cfg = self.config
        if cfg is None:
            return None
        sensors = {}
        for name, f in self.filters.items():
            inten = f.intensity(cfg)
            if inten is not None:
                sensors[name] = lm.SensorInput(inten, 0.0 if pure else f.solar_weight(t))
        return lm.mask_for_dest(cfg, screen, self.wall(), self.overrides.get(screen), sensors)

    # -- overrides (Flask GETs, read-only) ----------------------------------------
    def overrides_ready(self, screen: str, t: float) -> bool:
        """An override poll for the screen completed recently (success or failure). Before
        that, the arbiter keeps the Flask source: Flask's /mask already applies overrides."""
        at = self._ov_attempt.get(screen)
        return at is not None and t - at <= 3 * self.icfg.overrides_poll_s + self.icfg.overrides_timeout_s

    async def poll_overrides(self, http: aiohttp.ClientSession, screen: str) -> bool:
        """GET maskstate + brightness-override. True if the overrides changed. A failure keeps
        the last known overrides (none, if never read): the sensor path never needs Flask."""
        base = f"{self.cfg.flask_url}/api/{screen}"
        timeout = aiohttp.ClientTimeout(total=self.icfg.overrides_timeout_s)

        async def get(path: str) -> dict:
            async with http.get(f"{base}/{path}", timeout=timeout) as r:
                if r.status != 200:
                    raise RuntimeError(f"HTTP {r.status}")
                body = await r.json(content_type=None)
                if not isinstance(body, dict):
                    raise RuntimeError("unexpected payload")
                return body
        try:
            ms, bo = await asyncio.gather(get("maskstate"), get("brightness-override"))
            ov = lm.Overrides.from_flask(ms, bo)
        except Exception as e:
            err = describe_exc(e)
            if self._ov_error.get(screen) != err:
                log.warning("%s: override poll failed: %s (keeping the last known overrides)", screen, err)
            self._ov_error[screen] = err
            self._ov_attempt[screen] = self.clock()
            return False
        if screen in self._ov_error:
            log.info("%s: override poll ok again", screen)
            self._ov_error.pop(screen, None)
        self._ov_attempt[screen] = self.clock()
        changed = self.overrides.get(screen, lm.Overrides()) != ov
        if changed:
            log.info("%s: overrides now %s", screen, ov)
        self.overrides[screen] = ov
        return changed

    # -- persistence / status ------------------------------------------------------
    def export_state(self) -> dict:
        out = dict(self._persisted)
        for name, f in self.filters.items():
            if f.lux is not None:
                out[name] = f.export_state()
        return {"v": 1, "filters": out}

    def status(self, t: float) -> dict:
        sensors = {}
        for name in sorted(set(self.filters) | set(self.last_raw)):
            f = self.filters.get(name)
            st = f.state(t) if f else None
            last = self.last_reading.get(name)
            sensors[name] = {
                "lux": None if not st or st.lux is None else round(st.lux, 3),
                "stale": None if not st else st.stale, "held": None if not st else st.held,
                "solar_weight": None if not st else round(st.solar_weight, 3),
                "age_s": None if last is None else round(t - last, 3),
                "samples": f.samples if f else 0, "rejected": dict(self.rejected.get(name, {})),
                "targets": self.targets(name), "last": self.last_raw.get(name)}
        w = self.watcher
        return {"sensors": sensors,
                "config": {"loaded": w.current is not None, "reloads": w.reloads, "last_error": w.last_error,
                           "sources": dict(w.current.sources) if w.current else None},
                "params": {k: getattr(self.params, k) for k in ("snap_ev", "snap_tau_s", "drift_tau_s",
                                                                 "stale_after_s", "stale_ramp_s", "fresh_ramp_s")},
                "overrides": {s: {"mask_enabled": o.mask_enabled, "brightness": o.brightness}
                              for s, o in self.overrides.items()},
                "override_errors": dict(self._ov_error)}
