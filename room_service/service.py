"""RoomService: wires the components together and owns their lifecycle."""

from __future__ import annotations

import asyncio
import logging
import secrets
from pathlib import Path

import aiohttp
from aiohttp import web

from . import __version__
from .api import build_app
from .adapters import build_adapters
from .config import RoomConfig
from .dmx import DmxStack, hue_placeholder
from .events import EventHub
from .mask import MaskPusher
from .mask_source import close_ingest, make_mask_source, mask_overview
from .pages import PageHub
from .room_config import RoomConfigFile
from .roomstate.contract import Adapter
from .roomstate.coordinator import Busy, Coordinator, Refused
from .roomstate.model import RoomConfig as RoomModes
from .screens import ScreensManager
from .site import SiteRooms
from .snapshot import PlaywrightSnapshotter, SnapshotError, Snapshotter
from .stream_client import StreamClient
from .util import REDACT, InstanceLock, load_or_create_token, spawn, utc_iso

log = logging.getLogger("room_service")


class DisabledSnapshotter:
    """snapshot.enabled: false -> every entry/exit fails at the snapshot step (cleanly)."""

    async def start(self, screens: list[str]) -> None:
        return None

    async def ensure_ready(self, screen: str) -> None:
        raise SnapshotError("snapshotter disabled in config")

    async def snapshot(self, screen: str, state: dict) -> bytes:
        raise SnapshotError("snapshotter disabled in config")

    def prefetch(self, screen: str, state: dict) -> None:
        return None

    async def close(self) -> None:
        return None

    def status(self) -> dict:
        return {"enabled": False}


class RoomService:
    def __init__(self, cfg: RoomConfig, *, snapshotter: Snapshotter | None = None, token: str | None = None,
                 extra_adapters: list[Adapter] | None = None, blinds: Adapter | None = None,
                 remotes: list[Adapter] | None = None, show: Adapter | bool | None = None) -> None:
        """extra_adapters replace built ones with the same subsystem (tests); blinds defaults to the Bloc hub;
        remotes to the RM4 Pro devices in config/room/rf/ (the glitterball); show to show_service's Feel (False: a
        placeholder, so tests never reach the real show_service)."""
        self.cfg = cfg
        self.epoch = secrets.token_hex(4)
        self.started_at = utc_iso()
        self.token = token
        self._snapshotter_override = snapshotter
        self._extra_adapters = extra_adapters or []
        self._blinds = blinds
        self._remotes = remotes
        self._show = show
        self._watch: "asyncio.Task | None" = None
        self._member_watches: "list[asyncio.Task]" = []    # adapters whose members come and go (light)
        self.http: aiohttp.ClientSession | None = None
        self._lock: InstanceLock | None = None
        # Built in start(): they need a running loop.
        self.events: EventHub
        self.pages: PageHub
        self.stream: StreamClient
        self.mask: MaskPusher
        self.snapshotter: Snapshotter
        self.screens: ScreensManager
        self.coordinator: Coordinator
        self.room_config: RoomConfigFile
        self.dmx: DmxStack | None = None
        self.site = SiteRooms(cfg.rooms_file)        # status "rooms": the Room page's sections
        self.app = build_app(self)
        self.app.on_startup.append(self._on_startup)
        self.app.on_shutdown.append(self._on_shutdown)
        self.app.on_cleanup.append(self._on_cleanup)

    # -- lifecycle -------------------------------------------------------------
    async def _on_startup(self, app: web.Application) -> None:
        await self.start()

    async def _on_shutdown(self, app: web.Application) -> None:
        # Stop the coordinator and the screen state machines *before* the page sockets close,
        # so an in-flight handshake is left as it is (persisted: its open freeze and session)
        # for the next process to release/adopt, rather than aborting on "page gone".
        await self.coordinator.stop()
        await self.screens.stop()
        if self.dmx is not None:
            await self.dmx.stop()              # stops a Hue stream (the bridge hands the lights back) and the bus
        # Then close long-lived sockets so aiohttp's graceful shutdown doesn't wait on them.
        await self.pages.close_all()
        await close_ingest(self.mask)
        await self.events.stop()

    async def _on_cleanup(self, app: web.Application) -> None:
        await self.stop()

    async def start(self) -> None:
        cfg = self.cfg
        Path(cfg.state_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = InstanceLock(Path(cfg.state_dir) / "room.lock")
        self._lock.acquire()                 # before any persisted state is read
        if self.token is None:
            self.token = load_or_create_token(cfg.token_file)
        REDACT.add(self.token)
        self.http = aiohttp.ClientSession()
        self.events = EventHub(self.status)
        self.pages = PageHub(cfg, on_hello=lambda c: self.screens.welcome(c),
                             on_message=lambda c, t, d: self.screens.page_message(c, t, d),
                             on_closed=lambda c: self.screens.page_closed(c),
                             on_log=self.events.log_event)
        self.stream = StreamClient(cfg.stream_url, cfg.stream_token_file, self.http,
                                   http_timeout_s=cfg.timeouts.http_s)
        # MaskArbiter (Flask + sensor ingest, LIVE_V1 §2.2b) unless ingest is off; without a
        # sensor keys file it behaves exactly like MaskPusher.
        self.mask = make_mask_source(cfg, self.http, self.pages,
                                     is_frozen=lambda s: self.screens.is_frozen(s),
                                     on_push=lambda s, d: self.events.emit("screen.mask", {"screen": s, **d}),
                                     on_change=self.events.mark_dirty,
                                     is_live=lambda s: getattr(self.screens.get(s), "layer", None) == "live")
        if self._snapshotter_override is not None:
            self.snapshotter = self._snapshotter_override
        elif cfg.snapshot.enabled:
            self.snapshotter = PlaywrightSnapshotter(cfg)
        else:
            self.snapshotter = DisabledSnapshotter()
        self.screens = ScreensManager(cfg, epoch=self.epoch, pages=self.pages, stream=self.stream,
                                      snapshotter=self.snapshotter, mask=self.mask, events=self.events,
                                      http=self.http)
        # The room state model (ROOM_STATE_MODEL): adapters, modes.json, one state file.
        # Virtual DMX devices (config/room/patch.yaml): Art-Net in, show bus out, Hue streaming; opt-in (dmx.enabled).
        if cfg.dmx_enabled:
            try:
                self.dmx = DmxStack(cfg.patch_file, cfg.show_bus_port)
            except Exception as e:
                log.error("dmx disabled: %s", e)
        hue = self.dmx.adapter() if self.dmx is not None else hue_placeholder(cfg.patch_file)
        built = build_adapters(self.screens.controllers, self.mask, self.events.log_event, cfg.state_dir,
                               cfg.timeouts.policy_ack_s, blinds=self._blinds, blinds_enabled=cfg.blinds_enabled,
                               sonos=None if cfg.sonos_enabled else False,
                               hue=hue, remotes=self._remotes, show=self._show)
        adapters = list({a.subsystem: a for a in built + self._extra_adapters}.values())
        self.room_config = RoomConfigFile(cfg.modes_file, adapters, cfg.state_dir, rooms_path=cfg.rooms_file)
        self.coordinator = Coordinator(self.room_config.load(), adapters, Path(cfg.state_dir) / "state.json",
                                       on_event=self._on_room_event)
        self.coordinator.config_error = self.room_config.error
        self.screens.revert_hook = self._revert_screen      # PUT /screens/{id} {for_s} running out
        self.events.start()
        await self.screens.start()
        if self.dmx is not None:
            await self.dmx.start()
        await self.coordinator.start()
        self._watch = spawn(self.room_config.watch(self._on_config_file), "modes-file-watch")
        # Members that come and go at runtime (light: the lights registered with show_service on /lights): each such
        # adapter watches its source, and the room config is rebuilt when they change.
        self._member_watches = [spawn(a.watch_members(self._on_members), f"{a.subsystem}-members")
                                for a in adapters if callable(getattr(a, "watch_members", None))]
        self.stream.start()
        self.mask.start()
        await self.snapshotter.start([sid for sid, sc in cfg.screens.items() if sc.live_enabled])
        log.info("room_service %s up (epoch %s); screens: %s", __version__, self.epoch,
                 ", ".join(f"{s}{f' {sc.width}x{sc.height}' if sc.width else ''}{'' if sc.live_enabled else ' (live off)'}"
                           for s, sc in cfg.screens.items()))

    async def _revert_screen(self, screen: str, value) -> bool:
        """for_s is up: the value from before goes back as a change by hand under the mode's policy (so going back
        to the mode's own value clears the drift). False while a mode switch or another change runs: retried."""
        try:
            await self.coordinator.hand_set(f"screens.{screen}", value, source="for_s")
        except Busy:
            return False
        except Refused as e:
            log.warning("%s: for_s revert refused by the room (%s); setting it directly", screen, e)
            self.screens[screen].set_desired(value, source="for_s")
        return True

    def _on_room_event(self, e: dict) -> None:
        self.events.emit(e["type"], e["data"])
        self.events.mark_dirty()

    def _on_config_file(self, modes: RoomModes, error: str | None) -> None:
        """modes.json edited on disk: reload (nothing moves until the next switch), or keep the old one and say why."""
        self.coordinator.config_error = error
        if error:
            self.events.log_event("error", error)
        else:
            self.coordinator.reload(modes, cause="modes.json edited on disk")
            self.events.log_event("info", f"modes.json reloaded (revision {modes.revision})")
        self.events.mark_dirty()

    def _on_members(self, subsystem: str) -> None:
        """An adapter's members changed (a light registered or removed on /lights): the room config is rebuilt from the
        same files, so modes, rooms.json groups and the page see them. Nothing moves until the next switch."""
        modes = self.room_config.load()
        self.coordinator.config_error = self.room_config.error
        if self.room_config.error is None:
            self.coordinator.reload(modes, cause=f"{subsystem} members changed")
        self.events.mark_dirty()

    def modes_view(self) -> dict:
        """GET /api/v1/modes: every requestable mode with its resolved state, nested the way the Room page reads it
        (screens per id, single-member subsystems flat), plus the per-target entries."""
        cfg = self.coordinator.cfg
        out = {}
        for name, m in cfg.modes.items():
            if m.abstract:
                continue
            state: dict = {}
            targets = {}
            for t, e in m.state.items():
                sub, member = t.split(".", 1)
                v = e.set if e.sets else {"retrieve": e.retrieve, "else": e.else_} if e.retrieve else {"store": e.store}
                targets[t] = {"verb": e.verb(), "value": v if e.sets else None, "restore": e.restore, "policy": e.policy}
                if (slot := e.retrieve or (None if e.restore else e.store)):
                    targets[t]["slot"] = slot     # an old-style store/retrieve pair only
                a = self.coordinator.adapters.get(sub)
                if a is not None and a.members == ["main"]:
                    state[sub] = v
                else:
                    state.setdefault(sub, {})[member] = v
            out[name] = {"label": m.label, "description": m.description, "icon": m.icon, "voice": m.voice,
                         "on_room_page": m.on_room_page,
                         "state": state, "targets": targets,
                         "on_enter": [s.signal for s in m.on_enter], "on_exit": [s.signal for s in m.on_exit]}
        return {"default": cfg.default, "error": self.room_config.error, "revision": cfg.revision, "modes": out}

    async def stop(self) -> None:
        # Live sessions are left running on purpose: a restarted room_service adopts them.
        if self._watch is not None:
            self._watch.cancel()
        for t in self._member_watches:
            t.cancel()
        await self.coordinator.stop()
        await self.mask.stop()
        await self.stream.stop()
        await self.screens.stop()
        await self.snapshotter.close()
        if self.http is not None:
            await self.http.close()
        if self._lock is not None:
            self._lock.release()
        log.info("room_service stopped")

    # -- status ------------------------------------------------------------------
    def dmx_summary(self) -> dict:
        """Compact: the full patch and values are GET /api/v1/dmx."""
        if self.dmx is None:
            return {"enabled": False}
        i, h = self.dmx.rx, self.dmx.hue
        return {"enabled": True, "devices": sorted(self.dmx.patch.devices), "patch_error": self.dmx.patch_error,
                "artnet": {"online": i.online, "pps": i.pps, "bind_error": i.bind_error},
                "bus_clients": len(self.dmx.bus.clients) if self.dmx.bus else 0,
                "hue": h.state if h is not None else "unavailable"}

    def status(self) -> dict:
        m = self.mask
        room = self.coordinator.status()
        return {
            "mode": {**room["mode"], "modes_error": self.room_config.error, "rooms_error": self.room_config.rooms_error},
            "screens": self.screens.status(),
            "rooms": self.site.view(),     # rooms.json: [{id, name, screens, lights, blinds, sensors}], file order
            "stream_service": {"reachable": self.stream.reachable, "url": self.cfg.stream_url,
                               "last_error": None if self.stream.reachable else self.stream.last_error,
                               "owned_sessions": sorted(self.screens.owned),
                               "foreign_sessions": self.screens.foreign},
            "subsystems": room["subsystems"],
            "stored": room["stored"],
            "config": room["config"],
            "flask": {"ws_connected": m.ws_connected, "lux_frames": m.lux_frames,
                      "mask_requests": m.requests, "last_fetch_error": m.last_fetch_error},
            **mask_overview(m),            # "mask": ingest, sources, latency p50/p95 (MaskArbiter)
            "snapshotter": self.snapshotter.status(),
            "dmx": self.dmx_summary(),
            "service": {"version": __version__, "epoch": self.epoch, "started_at": self.started_at},
        }
