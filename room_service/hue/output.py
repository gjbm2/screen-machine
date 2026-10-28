"""Hue output: streams the patch's `hue.*` virtual devices to the bridge's Entertainment area (HueStream v2 over
DTLS-PSK, via the pinned `hue-entertainment` package's streamer; r7 §4, §12).

* **Who turns it on:** the room-state `hue` adapter (adapters/hue.py) sets `wanted` to the devices whose target is
  `"dmx"`. With nothing wanted the area is stopped and the lights belong to the house again.
* **Art-Net gating:** wanted devices only start streaming once Art-Net is fresh, so a live mode with no Maestro
  running leaves the lights as they were. After `stop_after_s` without Art-Net (default 10 s) the stream stops and
  the bridge hands the lights back; it restarts when Art-Net returns.
* **Sending:** a fixed 25 Hz latest-value loop (the bridge forwards ~25 Hz over Zigbee, r7 §3), sending only on
  change plus at least once a second; the streamer resends the last frame every 5 s so the area never times out.
* **Brightness:** frames use HueStream's xy+brightness colour space, not RGB. In RGB the bridge takes brightness from
  the colour's luminance, so saturated colours (blue, red) came out dim. Here brightness = max(r, g, b) ** gamma
  (patch.yaml `hue.gamma`, default 0.6; below 1 lifts the mid-levels) and the colour is the xy of the full-level rgb.
* **Level:** `level` (0-1, the room's brightness.lamps target) scales every lamp's brightness; a change eases over
  LEVEL_EASE_S.
* **hue.streamer (patch.yaml, hot-reloaded):** `room` (default) is this loop; `show` hands streaming to show_service
  (SHOW_ENGINE §9.4): this output then never starts the area (and stops it if it was streaming), state `show`.
* **Takeover (r7 §5):** every 2 s while streaming, the area's status and active_streamer are read. If another app has
  stopped or taken the area, the state becomes `taken_over` and nothing is restarted until `wanted` changes (a
  re-request of the mode). We never fight the Hue app.
"""

from __future__ import annotations

import asyncio
import logging
import time

from ..dmx.artnet import ArtNetReceiver
from ..dmx.patch import Patch
from .client import BridgeSecrets, HueClient, HueError, resolve_area
from .stream import GAMMA, xy_bri, xy_streamer

log = logging.getLogger("room_service.hue.output")

RATE_HZ = 25.0
STATUS_EVERY_S = 2.0
LEVEL_EASE_S = 2.0          # a brightness change eases (linearly) over this


class HueOutput:
    def __init__(self, client: HueClient, patch: Patch, receiver: ArtNetReceiver, *, area_id: str | None = None,
                 stop_after_s: float = 10.0) -> None:
        self.client, self.patch, self.rx = client, patch, receiver
        self.area_id = area_id
        self.stop_after_s = stop_after_s
        self.streamer = xy_streamer()                   # hue-entertainment pinned 0.1.2 in the venv
        self.wanted: set[str] = set()
        self.state = "idle"                     # idle | waiting_input | starting | streaming | taken_over | error
        self.detail = ""
        self.channels: dict[str, int] = {}      # light id -> entertainment channel id
        self.our_streamer: str | None = None
        self.frames = 0
        self.level = 1.0                        # wanted brightness 0-1 (BrightnessAdapter)
        self._lv = 1.0                          # what is sent now, eased to level
        self._lv_t: float | None = None
        self._taken_wanted: frozenset[str] | None = None
        self._last_sent: tuple | None = None
        self._last_send_t = 0.0
        self._task: asyncio.Task | None = None
        self._changed = asyncio.Event()

    @property
    def secrets(self) -> BridgeSecrets:
        return self.client.secrets

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="hue-output")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._stop_stream("room_service stopping")

    def set_wanted(self, devices: set[str]) -> None:
        if devices != self.wanted:
            self.wanted = set(devices)
            if self.state == "taken_over" and frozenset(devices) != self._taken_wanted:
                self.state, self.detail = "idle", ""
            self._changed.set()

    def is_streaming(self, device: str) -> bool:
        return self.state == "streaming" and device in self.wanted

    # -- the loop ----------------------------------------------------------------------------------------------
    async def _run(self) -> None:
        next_status = 0.0
        while True:
            try:
                await asyncio.wait_for(self._changed.wait(), 1.0 / RATE_HZ)
            except asyncio.TimeoutError:
                pass
            self._changed.clear()
            try:
                if self.patch.hue.get("streamer", "room") != "room":   # handed to show_service (SHOW_ENGINE §9.4)
                    if self.streamer.is_connected or self.state in ("streaming", "starting"):
                        await self._stop_stream("hue.streamer: show")
                    self.state, self.detail = "show", "show_service streams the lamps"
                    continue
                if self.state == "show":
                    self.state, self.detail = "idle", ""
                age = self.rx.age_ms
                fresh = age is not None and age <= self.stop_after_s * 1000.0
                if not self.wanted or self.state == "taken_over":
                    if self.streamer.is_connected or self.state in ("streaming", "starting"):
                        await self._stop_stream("not wanted")
                    if self.state != "taken_over":
                        self.state = "idle"
                    continue
                if not fresh:
                    if self.state in ("streaming", "starting"):
                        await self._stop_stream(f"no Art-Net for {self.stop_after_s:.0f} s")
                    self.state, self.detail = "waiting_input", "no Art-Net input"
                    continue
                if self.state != "streaming" or not self.streamer.is_connected:
                    await self._start_stream()
                    continue
                self._send()
                now = time.monotonic()
                if now >= next_status:
                    next_status = now + STATUS_EVERY_S
                    await self._check_takeover()
            except asyncio.CancelledError:
                raise
            except Exception as e:                  # keep the loop alive; report and back off
                self.state, self.detail = "error", f"{type(e).__name__}: {e}"
                log.warning("hue output: %s", self.detail)
                await self._stop_stream("error")
                await asyncio.sleep(5.0)

    async def _resolve(self) -> None:
        self.area_id, self.channels = await resolve_area(self.client, self.area_id)

    async def _start_stream(self) -> None:
        self.state, self.detail = "starting", ""
        await self._resolve()
        await self.client.put("entertainment_configuration", self.area_id, {"action": "start"})
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.streamer.connect, self.secrets.host, self.secrets.app_key,
                                   self.secrets.clientkey, self.area_id)
        area = (await self.client.get("entertainment_configuration", self.area_id))[0]
        self.our_streamer = (area.get("active_streamer") or {}).get("rid")
        self.state, self._last_sent = "streaming", None
        log.info("hue output: streaming area %s (%d channels)", self.area_id, len(self.channels))
        self._send()

    async def _stop_stream(self, why: str) -> None:
        was = self.streamer.is_connected
        if was:
            await asyncio.get_running_loop().run_in_executor(None, self.streamer.disconnect)
        if self.area_id and (was or self.state in ("streaming", "starting")):
            try:
                await self.client.put("entertainment_configuration", self.area_id, {"action": "stop"})
            except HueError as e:
                log.warning("hue output: stop failed: %s", e)
            log.info("hue output: stream stopped (%s)", why)
        if self.state in ("streaming", "starting"):
            self.state = "idle"

    async def _check_takeover(self) -> None:
        area = (await self.client.get("entertainment_configuration", self.area_id))[0]
        streamer = (area.get("active_streamer") or {}).get("rid")
        if area.get("status") != "active" or (self.our_streamer and streamer and streamer != self.our_streamer):
            log.warning("hue output: area %s taken over or stopped by another app (status %s)",
                        self.area_id, area.get("status"))
            await asyncio.get_running_loop().run_in_executor(None, self.streamer.disconnect)
            self.state, self.detail = "taken_over", "another app stopped or took the entertainment area"
            self._taken_wanted = frozenset(self.wanted)

    def _ease_level(self, now: float) -> float:
        dt = 0.0 if self._lv_t is None else now - self._lv_t
        self._lv_t = now
        step = dt / LEVEL_EASE_S
        self._lv = min(self.level, self._lv + step) if self._lv < self.level else max(self.level, self._lv - step)
        return self._lv

    def _send(self) -> None:
        fixtures, _ = self.patch.decode(self.rx.dmx)
        gamma = float(self.patch.hue.get("gamma", GAMMA))
        now = time.monotonic()
        lv = self._ease_level(now)
        cmds = []
        for dev in self.patch.of_kind("hue"):
            light = dev.extra.get("light")
            ch = self.channels.get(light) if light else None
            if ch is None or dev.id not in self.wanted:
                continue
            x, y, bri = xy_bri(fixtures[dev.id].get("rgb", [0, 0, 0]), gamma)
            cmds.append((ch, x, y, round(bri * lv)))
        if cmds and (tuple(cmds) != self._last_sent or now - self._last_send_t >= 1.0):
            self.streamer.send_colors(cmds)
            self._last_sent, self._last_send_t = tuple(cmds), now
            self.frames += 1

    def status(self) -> dict:
        dev_of_ch = {self.channels[d.extra["light"]]: d.id for d in self.patch.of_kind("hue")
                     if d.extra.get("light") in self.channels}
        sent = {dev_of_ch.get(ch, f"ch{ch}"): {"x": round(x / 0xFFFF, 4), "y": round(y / 0xFFFF, 4),
                                                "bri": round(b / 0xFFFF, 4)}
                for ch, x, y, b in (self._last_sent or ())}
        return {"state": self.state, "detail": self.detail, "area": self.area_id, "wanted": sorted(self.wanted),
                "channels": self.channels, "frames": self.frames, "dtls": self.streamer.is_connected,
                # the last HueStream frame actually sent (xy + brightness, 0-1), for the outputs monitor
                "sent": sent, "sent_age_ms": round((time.monotonic() - self._last_send_t) * 1000) if sent else None,
                "gamma": float(self.patch.hue.get("gamma", GAMMA)), "level": self.level,
                "level_now": round(self._lv, 3)}
