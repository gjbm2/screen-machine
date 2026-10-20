"""MaskArbiter: exactly one writer of `mask.set` per screen (DCL v3.1 §4.4, LIVE_V1 §2.2b).

It *is* the room's mask object: a `MaskPusher` subclass, so the screens, the coordinator's
`sensor_mask` subsystem and the status code keep calling the same methods. The Flask path
(the interim `GET /api/<id>/mask` fetch, `mask.py`) becomes one source, the sensor path
(`SensorService`, i.e. lightmask) the other, and each screen has one *tier* at a time:

| tier | writer | Flask `/mask` fetched? |
|---|---|---|
| `sensor` | lightmask, from the screen's fresh sensor | **no** (each fetch advances Flask's shared EMA) |
| `flask` | Flask's `/mask` (today's path) | yes |
| `sensor-stale` | lightmask's stale ramp to the solar curve (auto: only while Flask is failing) | yes (to notice it is back) |
| `hold` | nobody: the page keeps its last mask | no |

`ingest.mask_source` per screen (ingest_config.py): `auto` = sensor (fresh) > flask > hold,
`sensor` = lightmask only (DCL), `flask` = today. **Without a sensor keys file every screen is
`flask` and every call goes straight to MaskPusher's own code, so behaviour is exactly as
before.** A screen's first sensor-driven push marks it *sourced*; from then on its masks
carry `source` and go through its MaskShaper (mask_shaper.py), Flask values included.

Holds: a frozen screen (cut in progress), `sensor_mask: suspended`, and a sensor whose relay
says `held: true` (a calibration capture) push nothing; the shaper keeps the latest value
and it goes out when the hold ends (held: the next reading re-offers it).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import lightmask as lm

from .ingest import IngestHub, SensorKeys
from .mask import MaskPusher, MaskValue
from .mask_shaper import LatencyStats, MaskShaper, Shaped
from .sensors import SensorService, utcnow
from .util import FileWriter, describe_exc, spawn

if TYPE_CHECKING:
    import aiohttp

    from .config import RoomConfig
    from .pages import PageHub

log = logging.getLogger("room_service.mask")

FLASK, SENSOR, STALE, HOLD = "flask", "sensor", "sensor-stale", "hold"
FETCHING = (FLASK, STALE)
SENSOR_TIERS = (SENSOR, STALE)
NEG_INF = -math.inf


class StampedDict(dict):
    """MaskPusher.latest that also records when each screen's value was fetched."""

    def __init__(self, clock: Callable[[], float]) -> None:
        super().__init__()
        self.stamp: dict[str, float] = {}
        self._clock = clock

    def __setitem__(self, key: str, value: Any) -> None:
        super().__setitem__(key, value)
        self.stamp[key] = self._clock()


def clean_mask(m: dict) -> dict | None:
    """Clamp a lightmask mask to what the page accepts (LIVE_V1 MaskSet); None if unusable."""
    try:
        b = float(m["brightness"])
        a = float(m.get("warm_alpha") or 0.0)
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(b) and math.isfinite(a)):
        return None
    out = dict(m)
    out["brightness"] = min(1.0, max(0.0, b))
    out["warm_alpha"] = min(1.0, max(0.0, a))
    out["warm_hex"] = str(m.get("warm_hex") or "#FFFFFF").upper()
    return out


class MaskArbiter(MaskPusher):
    def __init__(self, cfg: "RoomConfig", http: "aiohttp.ClientSession | None", pages: "PageHub", *,
                 is_frozen: Callable[[str], bool],
                 on_push: Callable[[str, dict], None] | None = None,
                 on_change: Callable[[], None] | None = None,
                 is_live: Callable[[str], bool] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], datetime] = utcnow) -> None:
        super().__init__(cfg, http, pages, is_frozen=is_frozen, on_push=on_push, on_change=on_change)
        self.icfg = cfg.ingest
        self._clock = clock
        self.latest = StampedDict(clock)
        self.state_path = Path(cfg.state_dir) / "sensors.json"
        self.sensors = SensorService(cfg, self.icfg, clock=clock, wall=wall, state_path=self.state_path)
        self.keys = SensorKeys(self.icfg.sensors_file)
        self.hub = IngestHub(self)
        self._is_live = is_live or (lambda s: False)
        self.shapers: dict[str, MaskShaper] = {sid: MaskShaper(self.icfg.shaper) for sid in cfg.screens}
        self.tier: dict[str, str] = {}
        self.pushed_source: dict[str, str | None] = {}
        self._sourced: set[str] = set()
        self._source_change: set[str] = set()
        self._flask_since: dict[str, float] = {}
        self._flask_failed: dict[str, bool] = {}
        self.latency = LatencyStats(self.icfg.latency_window)
        self.latency_by_screen = {sid: LatencyStats(self.icfg.latency_window) for sid in cfg.screens}
        self.counts: Counter[str] = Counter()
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._sensor_tasks: list[asyncio.Task] = []
        self._ov_wake = asyncio.Event()
        self._config_wake = asyncio.Event()
        self._ov_started: dict[str, float] = {}
        self._ov_inflight: set[str] = set()
        self._files = FileWriter("sensors")
        self._persisted: dict | None = None
        self._persist_at = NEG_INF
        self._eval_errors: set[str] = set()

    # -- state -------------------------------------------------------------------
    @property
    def ingest_on(self) -> bool:
        return self.icfg.enabled and self.mcfg.enabled

    @property
    def armed(self) -> bool:
        """Sensor path possible: ingest on and at least one valid sensor key."""
        return self.ingest_on   # owner ruling, 14 Oct: no sensor keys needed

    def disabled_reason(self) -> str | None:
        if not self.icfg.enabled:
            return "ingest disabled in the room config (ingest.enabled: false)"
        if not self.mcfg.enabled:
            return "ingest disabled: mask push is off in the room config (mask.enabled: false)"
        return None

    def _legacy(self, screen: str) -> bool:
        """Today's code path, verbatim: no sensor keys and never sensor-driven."""
        return not self.armed and screen not in self._sourced

    def _rate(self, screen: str) -> float:
        sh = self.icfg.shaper
        try:
            live = bool(self._is_live(screen))
        except Exception:
            live = False
        return sh.live_max_sets_per_s if live else sh.max_sets_per_s

    # -- tiers ---------------------------------------------------------------------
    def _flask_failing(self, screen: str, t: float) -> bool:
        if not self._flask_failed.get(screen):
            return False
        ref = max(self.latest.stamp.get(screen, NEG_INF), self._flask_since.get(screen, NEG_INF))
        return t - ref >= self.icfg.flask_fail_after_s

    def _select(self, screen: str, t: float) -> str:
        if not self.armed:
            return FLASK
        mode = self.icfg.source_for(screen)
        if mode == "flask":
            return FLASK
        v = self.sensors.view(screen, t)
        if mode == "sensor":
            if not v.live_seen:
                return HOLD
            if not self.sensors.overrides_ready(screen, t):
                self._ov_wake.set()
                return HOLD
            return SENSOR
        # auto
        if v.fresh:
            if self.sensors.overrides_ready(screen, t):
                return SENSOR
            self._ov_wake.set()           # read the overrides first; Flask's /mask applies them meanwhile
            return FLASK
        if v.live_seen and self._flask_failing(screen, t):
            return STALE
        return FLASK

    def _update_tier(self, screen: str, t: float) -> str:
        new = self._select(screen, t)
        old = self.tier.get(screen, FLASK)
        self.tier[screen] = new
        if new == old:
            return new
        if new in FETCHING and old not in FETCHING:
            self._flask_since[screen] = t       # a value fetched before this is stale
            self._flask_failed.pop(screen, None)
            self.trigger()                      # fetch now rather than at the backstop
        sh = self.shapers.get(screen)
        if sh is not None:
            sh.cancel()
        if screen in self.pushed:
            self._source_change.add(screen)
        self.counts["tier_changes"] += 1
        drv = self.sensors.driver(screen)
        log.info("%s: mask source %s -> %s%s", screen, old, new, f" (sensor {drv})" if drv and new in SENSOR_TIERS else "")
        self._on_change()
        return new

    # -- MaskPusher hooks (the Flask source) -----------------------------------------
    def _targets(self) -> list[str]:
        base = super()._targets()
        if not self.armed and not self._sourced:
            return base
        t = self._clock()
        return [s for s in base if self._update_tier(s, t) in FETCHING]

    async def _fetch_one(self, screen: str) -> None:
        before = self.latest.stamp.get(screen)
        await super()._fetch_one(screen)
        self._flask_failed[screen] = self.latest.stamp.get(screen) == before

    def _maybe_push(self, screen: str) -> bool:
        if self._legacy(screen):
            return super()._maybe_push(screen)
        t = self._clock()
        tier = self._update_tier(screen, t)
        if tier in SENSOR_TIERS:
            self.evaluate(screen, t)            # a resume (unfrozen / follow) on the sensor path
            return False
        if tier != FLASK:
            return False
        if screen not in self._sourced:
            return super()._maybe_push(screen)
        sc = self.cfg.screen(screen)
        v = self.latest.get(screen)
        if not sc or not sc.mask_push or v is None:
            return False
        if self.latest.stamp.get(screen, NEG_INF) < self._flask_since.get(screen, NEG_INF):
            return False                        # fetched before the sensor took over: too old
        return self._offer(screen, {**v.as_dict(), "source": "flask"}, t, flask=True)

    def on_unfrozen(self, screen: str) -> None:
        super().on_unfrozen(screen)
        if not self._legacy(screen):
            self._flush(screen)

    def set_suspended(self, suspended: bool) -> None:
        was = self.suspended
        super().set_suspended(suspended)
        if was and not suspended and (self.armed or self._sourced):
            for sid in self.cfg.screens:
                if not self._legacy(sid):
                    self.evaluate(sid)
                    self._flush(sid)

    def welcome_mask(self, screen: str) -> dict | None:
        d = super().welcome_mask(screen)
        if self._legacy(screen):
            return d
        if d is not None and self.pushed_source.get(screen):
            d["source"] = self.pushed_source[screen]
        try:
            # After the welcome is queued (it is sent right after this returns): a first push,
            # or one that was held back while nobody was connected.
            asyncio.get_running_loop().call_soon(self._after_welcome, screen)
        except RuntimeError:
            pass
        return d

    def _after_welcome(self, screen: str) -> None:
        self.evaluate(screen)
        self._flush(screen)

    def status(self, screen: str) -> dict:
        d = super().status(screen)
        if self._legacy(screen):
            return d                            # the as-built shape (LIVE_V1 §3)
        t = self._clock()
        v = self.sensors.view(screen, t)
        d.update({"source": self.pushed_source.get(screen), "tier": self.tier.get(screen, FLASK),
                  "mask_source": self.icfg.source_for(screen), "sensor": v.driver, "sensor_fresh": v.fresh,
                  "held": v.held, "latency_ms": self.latency_by_screen[screen].status()})
        return d

    # -- the sensor source -----------------------------------------------------------
    def on_reading(self, sensor: str, lux: Any, seq: Any, held: bool, t: float) -> lm.SampleResult:
        """One ingest.lux reading (t = its receive time on room_service's monotonic clock)."""
        r = self.sensors.ingest(sensor, lux, seq, held, t)
        if not r.accepted:
            self.counts["readings_rejected"] += 1
            return r
        self.counts["readings"] += 1
        if self.sensors.config is None:
            self._config_wake.set()
            return r
        hint = "snap" if r.mode in ("snap", "init") else None
        for screen in self.sensors.targets(sensor):
            self.evaluate(screen, t, arrival=t, hint=hint)
        return r

    def on_lux_frame(self, data: dict) -> None:
        """The kiosk's relay posts to Flask's WS, and room_service hears every reading there:
        feed it to the sensor path so the masks follow the light within a frame or two instead
        of Flask's 1 s fetch and double smoothing. Flask relays each reading twice (the raw
        frame, then its own broadcast with `target_group`), so only the broadcast counts. A
        sensor that is connected straight to /v1/ingest/lux ignores this copy."""
        if "target_group" not in data or not self.ingest_on:
            return
        sensor = data.get("sensor_name")
        if not isinstance(sensor, str) or not sensor or sensor in self.hub.conns:
            return
        self.counts["flask_readings"] += 1
        self.on_reading(sensor, data.get("lux"), None, False, self._clock())

    def sensor_connected(self, sensor: str) -> None:
        if self.sensors.config is None:
            self._config_wake.set()

    def sensor_disconnected(self, sensor: str) -> None:
        self._on_change()

    def evaluate(self, screen: str, t: float | None = None, *, arrival: float | None = None,
                 hint: str | None = None) -> bool:
        """Compute and offer the sensor path's mask for one screen. True if a mask.set went out."""
        sc = self.cfg.screen(screen)
        if not sc or not sc.mask_push or self._legacy(screen):
            return False
        t = self._clock() if t is None else t
        tier = self._update_tier(screen, t)
        if tier not in SENSOR_TIERS:
            return False
        if self.sensors.view(screen, t).held:
            self.shapers[screen].cancel()       # calibration capture: keep the last mask
            self.counts["held_skips"] += 1
            return False
        pure = tier == SENSOR and self.icfg.source_for(screen) == "auto"
        try:
            mask = self.sensors.mask(screen, t, pure=pure)
        except Exception as e:                  # never let a config oddity kill the ingest path
            if screen not in self._eval_errors:
                log.warning("%s: lightmask evaluation failed: %s", screen, describe_exc(e))
                self._eval_errors.add(screen)
            return False
        self._eval_errors.discard(screen)
        mask = clean_mask(mask) if mask else None
        if mask is None:
            return False
        return self._offer(screen, mask, t, arrival=arrival, hint=hint)

    def _evaluate_all(self) -> None:
        t = self._clock()
        for sid, sc in self.cfg.screens.items():
            if sc.mask_push:
                self.evaluate(sid, t)

    # -- shaping and sending -------------------------------------------------------------
    def _can_send(self, screen: str) -> bool:
        return not self.suspended and not self._is_frozen(screen) and self.pages.any_connected(screen)

    def _offer(self, screen: str, mask: dict, t: float, *, arrival: float | None = None,
               hint: str | None = None, flask: bool = False) -> bool:
        sh = self.shapers[screen]
        if sh.last_sent is None and screen in self.pushed:
            # What the page shows now (a legacy Flask push): measure the change against it.
            sh.seed({**self.pushed[screen].as_dict(), "source": self.pushed_source.get(screen) or "flask"})
        out = sh.offer(mask, t, hint="source" if screen in self._source_change else hint,
                       arrival=arrival, max_rate_hz=self._rate(screen), flask=flask)
        sent = self._send(screen, out, t, deferred=False) if out is not None else False
        self._schedule(screen, t)
        return sent

    def _send(self, screen: str, s: Shaped, t: float, *, deferred: bool) -> bool:
        sh = self.shapers[screen]
        if self.suspended or self._is_frozen(screen):
            sh.defer(s)                         # hold: the page keeps what it shows
            self.counts["held_frozen"] += 1
            return False
        m = s.mask
        rev = self.rev.get(screen, 0) + 1
        data = {"rev": rev, "brightness": round(m["brightness"], 4), "warm_hex": m["warm_hex"],
                "warm_alpha": round(m["warm_alpha"], 4), "transition_ms": s.transition_ms,
                "source": m.get("source")}
        if self.pages.broadcast(screen, "mask.set", data) == 0:
            sh.defer(s)                         # nobody to tell: it goes after the next welcome
            return False
        now = self._clock()
        self.rev[screen] = rev
        value = MaskValue(brightness=data["brightness"], warm_hex=data["warm_hex"], warm_alpha=data["warm_alpha"])
        self.pushed[screen] = value
        self.pushed_source[screen] = m.get("source")
        sh.sent(s, t)
        self._source_change.discard(screen)
        if m.get("source") != "flask":
            self._sourced.add(screen)
        if s.arrival is not None:
            ms = (now - s.arrival) * 1000.0
            self.latency.add(ms, deferred)
            self.latency_by_screen[screen].add(ms, deferred)
        self.counts[f"sent_{s.kind}"] += 1
        if deferred:
            self.counts["sent_deferred"] += 1
        self._on_push(screen, value.as_dict())
        self._on_change()
        return True

    def _schedule(self, screen: str, t: float) -> None:
        h = self._timers.pop(screen, None)
        if h is not None:
            h.cancel()
        if not self._can_send(screen):
            return                              # unfreeze / follow / the next welcome flush it
        w = self.shapers[screen].wait_s(t, self._rate(screen))
        if w is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return                              # unit tests drive _flush by hand
        self._timers[screen] = loop.call_later(w, self._flush, screen)

    def _flush(self, screen: str) -> None:
        self._timers.pop(screen, None)
        sh = self.shapers.get(screen)
        if sh is None or sh.pending is None or not self._can_send(screen):
            return
        t = self._clock()
        tier = self._update_tier(screen, t)       # (a tier change cancels the pending value)
        if sh.pending is None:
            return
        if (tier == FLASK) != (sh.pending.mask.get("source") == "flask") or tier == HOLD:
            sh.cancel()
            return
        if tier in SENSOR_TIERS and self.sensors.view(screen, t).held:
            sh.cancel()
            return
        out = sh.due(t, self._rate(screen))
        if out is not None:
            self._send(screen, out, t, deferred=True)
        self._schedule(screen, t)

    # -- lifecycle -------------------------------------------------------------------------
    def start(self) -> None:
        super().start()
        if self._sensor_tasks or not self.ingest_on:
            return
        self.keys.refresh()
        self._sensor_tasks = [spawn(self._eval_loop(), "mask-eval"),
                              spawn(self._config_loop(), "lightmask-config"),
                              spawn(self._overrides_loop(), "mask-overrides")]

    async def stop(self) -> None:
        for task in self._sensor_tasks:
            task.cancel()
        await asyncio.gather(*self._sensor_tasks, return_exceptions=True)
        self._sensor_tasks = []
        for h in self._timers.values():
            h.cancel()
        self._timers.clear()
        await self.hub.close_all()
        self._maybe_persist(force=True)
        await self._files.close()
        await super().stop()

    async def _eval_loop(self) -> None:
        """The solar curve, the stale ramp and the tier changes move without readings."""
        while True:
            await asyncio.sleep(self.icfg.eval_interval_s)
            was = self.armed
            self.keys.refresh()
            if self.armed != was:
                log.info("sensor ingest %s", "armed (sensor keys present)" if self.armed else "off (no sensor keys)")
                self._config_wake.set()
                self._on_change()
            if self.armed or self._sourced:
                self._evaluate_all()
                self._maybe_persist()

    async def _config_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            if self.armed:
                try:
                    changed, params = await loop.run_in_executor(None, self.sensors.read_config)
                    changed = self.sensors.apply_params(params) or changed
                except Exception as e:
                    log.warning("lightmask config refresh failed: %s", describe_exc(e))
                    changed = False
                if changed:
                    self._evaluate_all()
            try:
                await asyncio.wait_for(self._config_wake.wait(), self.icfg.config_refresh_s)
            except asyncio.TimeoutError:
                pass
            self._config_wake.clear()

    def _override_candidates(self, t: float) -> list[str]:
        out = []
        for sid, sc in self.cfg.screens.items():
            if not sc.mask_push or self.icfg.source_for(sid) == "flask":
                continue
            v = self.sensors.view(sid, t)
            if v.fresh or (v.live_seen and self.tier.get(sid) in SENSOR_TIERS):
                out.append(sid)
        return out

    async def _overrides_loop(self) -> None:
        """Flask's maskstate + brightness-override, every overrides_poll_s per screen on the
        sensor path (2 GETs per 2 s = 1/s per screen; never while Flask is the source)."""
        poll = max(0.1, self.icfg.overrides_poll_s)
        while True:
            try:
                await asyncio.wait_for(self._ov_wake.wait(), poll)
            except asyncio.TimeoutError:
                pass
            self._ov_wake.clear()
            if not self.armed or self.http is None:
                continue
            t = self._clock()
            due = [s for s in self._override_candidates(t)
                   if s not in self._ov_inflight and t - self._ov_started.get(s, NEG_INF) >= 0.9 * poll]
            if not due:
                continue
            for s in due:
                self._ov_started[s] = t
                self._ov_inflight.add(s)
            try:
                results = await asyncio.gather(*(self.sensors.poll_overrides(self.http, s) for s in due),
                                               return_exceptions=True)
            finally:
                self._ov_inflight.difference_update(due)
            for s, changed in zip(due, results):
                self.evaluate(s, hint="snap" if changed is True else None)

    def _maybe_persist(self, force: bool = False) -> None:
        state = self.sensors.export_state()
        if not state["filters"] or state == self._persisted:
            return
        t = self._clock()
        if not force and t - self._persist_at < self.icfg.persist_every_s:
            return
        self._persisted, self._persist_at = state, t
        self._files.write_json(self.state_path, state)

    # -- status ------------------------------------------------------------------------------
    def overview(self) -> dict:
        """The top-level `mask` section of /api/v1/status."""
        t = self._clock()
        return {
            "ingest": {"on": self.ingest_on, "armed": self.armed, "disabled_reason": self.disabled_reason(),
                       "sensors_file": str(self.keys.path), "sensors_with_keys": sorted(self.keys.keys),
                       **self.hub.status()},
            "latency_ms": self.latency.status(),
            "tiers": {sid: self.tier.get(sid, FLASK) for sid, sc in self.cfg.screens.items() if sc.mask_push},
            "mask_source": {sid: self.icfg.source_for(sid) for sid in self.cfg.screens},
            "counts": dict(self.counts),
            **self.sensors.status(t),
        }
