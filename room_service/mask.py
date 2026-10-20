"""Mask pusher (AV_PROGRAMME §3.2, §3.4).

1. Listens on Flask's WS (:8765) as a *pure receiver*. Never sends a data frame:
   the first frame a client sends makes it a broadcaster to every screen. Only
   protocol control frames are ever written (pong replies to Flask's own pings,
   which its 30 s ping watchdog needs, and the close handshake). The client
   heartbeat is off.
2. Any JSON frame with a top-level "lux" key is a trigger. Triggers coalesce, and
   a backstop fires every `backstop_s` anyway.
3. GETs `/api/<screen>/mask` from Flask for screens with mask_push and a connected
   page, keeping all of Flask's curve/override logic: **at most once per second per
   screen** (`min_fetch_interval_s`, LIVE_V1 §2.2b), because each GET advances Flask's
   shared intensity EMA and faster polling would speed up the production TVs' masks
   too. `max_requests_per_s` caps the total on top of that.
4. A value that moved beyond the deadbands is pushed as `mask.set` to every page
   of that screen, unless the screen is frozen (a cut is in progress) or the mode
   has `sensor_mask: suspended`, in which case the last pushed value is held.
5. A failed fetch keeps the last mask (never resets to brightness 0).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

import aiohttp

from .util import describe_exc, spawn

if TYPE_CHECKING:
    from .config import RoomConfig
    from .pages import PageHub

log = logging.getLogger("room_service.mask")


@dataclass
class MaskValue:
    brightness: float
    warm_hex: str
    warm_alpha: float

    def as_dict(self) -> dict:
        return {"brightness": self.brightness, "warm_hex": self.warm_hex, "warm_alpha": self.warm_alpha}


def parse_mask(payload: dict) -> MaskValue | None:
    try:
        b = float(payload["brightness"])
        return MaskValue(brightness=min(1.0, max(0.0, b)),
                         warm_hex=str(payload.get("warm_hex") or "#FFFFFF").upper(),
                         warm_alpha=max(0.0, float(payload.get("warm_alpha") or 0.0)))
    except (KeyError, TypeError, ValueError):
        return None


class MaskPusher:
    def __init__(self, cfg: "RoomConfig", http: aiohttp.ClientSession, pages: "PageHub", *,
                 is_frozen: Callable[[str], bool],
                 on_push: Callable[[str, dict], None] | None = None,
                 on_change: Callable[[], None] | None = None) -> None:
        self.cfg = cfg
        self.mcfg = cfg.mask
        self.http = http
        self.pages = pages
        self._is_frozen = is_frozen
        self._on_push = on_push or (lambda screen, data: None)
        self._on_change = on_change or (lambda: None)
        self._trigger = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self.latest: dict[str, MaskValue] = {}     # last fetched from Flask
        self.pushed: dict[str, MaskValue] = {}     # last pushed (what pages show)
        self.rev: dict[str, int] = {}
        self.suspended = False
        self.ws_connected = False
        self.ws_frames = 0
        self.lux_frames = 0
        self.requests = 0
        self.last_fetch_error: str | None = None
        self._last_fetch: dict[str, float] = {}     # loop time of each screen's last GET

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if not self.mcfg.enabled or self._tasks:
            return
        self._tasks = [spawn(self._ws_loop(), "mask-flask-ws"), spawn(self._fetch_loop(), "mask-fetch")]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    # -- control -----------------------------------------------------------
    def trigger(self) -> None:
        self._trigger.set()

    def on_lux_frame(self, data: dict) -> None:
        """A lux frame seen on Flask's WS (MaskArbiter feeds it to the sensor path)."""

    def set_suspended(self, suspended: bool) -> None:
        """sensor_mask: suspended holds the last pushed mask; follow resumes pushing."""
        if suspended == self.suspended:
            return
        self.suspended = suspended
        log.info("sensor mask %s", "suspended (holding the last value)" if suspended else "following")
        self._on_change()
        if not suspended:
            for screen in list(self.latest):
                self._maybe_push(screen)
            self.trigger()

    def on_unfrozen(self, screen: str) -> None:
        """A cut finished: catch the page up with anything held during the freeze."""
        self._maybe_push(screen)
        self.trigger()

    def welcome_mask(self, screen: str) -> dict | None:
        sc = self.cfg.screen(screen)
        if not sc or not sc.mask_push:
            return None
        v = self.pushed.get(screen)
        if v is None:
            self.trigger()            # a first page: fetch now rather than at the backstop
            return None
        return {"rev": self.rev.get(screen, 0), **v.as_dict(), "transition_ms": self.mcfg.transition_ms}

    def status(self, screen: str) -> dict:
        v = self.pushed.get(screen) or self.latest.get(screen)
        d = v.as_dict() if v else {"brightness": None, "warm_hex": None, "warm_alpha": None}
        return {**d, "suspended": self.suspended}

    # -- push logic --------------------------------------------------------
    def _moved(self, old: MaskValue | None, new: MaskValue) -> bool:
        if old is None:
            return True
        return (abs(new.brightness - old.brightness) > self.mcfg.deadband_brightness
                or abs(new.warm_alpha - old.warm_alpha) > self.mcfg.deadband_warm_alpha
                or new.warm_hex != old.warm_hex)

    def _maybe_push(self, screen: str) -> bool:
        sc = self.cfg.screen(screen)
        new = self.latest.get(screen)
        if not sc or not sc.mask_push or new is None:
            return False
        if self.suspended or self._is_frozen(screen):
            return False             # hold: the last pushed value stays on the page
        if not self._moved(self.pushed.get(screen), new):
            return False
        rev = self.rev.get(screen, 0) + 1
        data = {"rev": rev, **new.as_dict(), "transition_ms": self.mcfg.transition_ms}
        if self.pages.broadcast(screen, "mask.set", data) == 0:
            return False             # nobody to tell; push again when a page is back
        self.rev[screen] = rev
        self.pushed[screen] = new
        self._on_push(screen, new.as_dict())
        self._on_change()
        return True

    # -- Flask HTTP --------------------------------------------------------
    def _targets(self) -> list[str]:
        return [sid for sid, sc in self.cfg.screens.items()
                if sc.mask_push and self.pages.any_connected(sid)]

    async def _fetch_one(self, screen: str) -> None:
        self.requests += 1
        url = f"{self.cfg.flask_url}/api/{screen}/mask"
        try:
            async with self.http.get(url, timeout=aiohttp.ClientTimeout(total=self.mcfg.fetch_timeout_s)) as r:
                if r.status != 200:
                    raise RuntimeError(f"HTTP {r.status}")
                v = parse_mask(await r.json(content_type=None))
                if v is None:
                    raise RuntimeError("unexpected mask payload")
        except Exception as e:  # keep the last mask; never reset to 0
            err = f"{screen}: {describe_exc(e)}"
            if err != self.last_fetch_error:
                log.warning("mask fetch failed: %s (keeping the last mask)", err)
            self.last_fetch_error = err
            return
        self.last_fetch_error = None
        self.latest[screen] = v
        self._maybe_push(screen)

    async def _fetch_loop(self) -> None:
        """Fetch on lux triggers and on the backstop, at most once per `min_fetch_interval_s`
        per screen (LIVE_V1 §2.2b "Interim": every GET advances Flask's shared, read-driven
        intensity EMA, so polling faster would also speed up the production TVs' masks).

        A trigger that arrives before a screen's interval has passed is remembered and served
        as soon as the interval allows, so a light change is never more than one interval late.
        """
        loop = asyncio.get_running_loop()
        interval = max(0.05, float(self.mcfg.min_fetch_interval_s))
        backstop = max(interval, float(self.mcfg.backstop_s))
        wanted: set[str] = set()          # triggered but not fetched yet (interval not passed)
        while True:
            targets = self._targets()
            now = loop.time()
            # Sleep until the next fetch falls due: a wanted screen's interval, or a backstop.
            due_at = [self._last_fetch.get(s, -1e9) + interval for s in wanted & set(targets)]
            due_at += [self._last_fetch.get(s, -1e9) + backstop for s in targets]
            timeout = max(0.0, min(due_at) - now) if due_at else backstop
            try:
                await asyncio.wait_for(self._trigger.wait(), timeout=timeout)
                triggered = True
            except asyncio.TimeoutError:
                triggered = False
            self._trigger.clear()
            targets = self._targets()
            if triggered:
                wanted |= set(targets)
            wanted &= set(targets)
            now = loop.time()
            due = [s for s in targets
                   if now - self._last_fetch.get(s, -1e9) >= (interval if s in wanted else backstop)]
            if not due:
                continue
            for s in due:
                self._last_fetch[s] = now
                wanted.discard(s)
            await asyncio.gather(*(self._fetch_one(s) for s in due))
            # Global budget on top of the per-screen interval: never hammer Flask.
            min_gap = len(due) / max(0.1, self.mcfg.max_requests_per_s)
            await asyncio.sleep(max(0.0, min_gap - (loop.time() - now)))

    # -- Flask WS (receive only) ------------------------------------------
    async def _ws_loop(self) -> None:
        backoff = 1.0
        while True:
            try:
                # heartbeat=None: we never send pings. autoping=True only answers Flask's
                # pings with pong control frames (its watchdog closes silent sockets).
                async with self.http.ws_connect(self.cfg.flask_ws, heartbeat=None, autoping=True,
                                                autoclose=True, max_msg_size=8 * 1024 * 1024) as ws:
                    self.ws_connected = True
                    backoff = 1.0
                    log.info("listening for lux frames on %s", self.cfg.flask_ws)
                    self._on_change()
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self.ws_frames += 1
                            if '"lux"' not in msg.data:
                                continue   # cheap pre-filter: overlays etc.
                            try:
                                data = json.loads(msg.data)
                            except ValueError:
                                continue
                            if isinstance(data, dict) and "lux" in data:
                                self.lux_frames += 1
                                try:
                                    self.on_lux_frame(data)
                                except Exception as e:
                                    log.warning("lux frame: %s", describe_exc(e))
                                self.trigger()
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Flask WS %s: %s", self.cfg.flask_ws, describe_exc(e))
            if self.ws_connected:
                self.ws_connected = False
                self._on_change()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
