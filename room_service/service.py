"""RoomService: wires the components together and owns their lifecycle."""

from __future__ import annotations

import asyncio
import logging
import secrets
from pathlib import Path

import aiohttp
from aiohttp import web

import smconfig

from . import __version__
from .api import build_app
from .adapters import build_adapters
from .adapters.room import screens_follow_a_sensor
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
from .zones import SITE, SITE_NAME, ScopedAdapter, Zone, ZonePlan, ZoneSet, layout_of, read_rooms

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
        remotes to the RM4 Pro devices in the deployment's room/rf/ (the glitterball); show to show_service's Feel
        (False: a placeholder, so tests never reach the real show_service)."""
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
        self.coordinator: Coordinator          # the site zone's (zones.site.coordinator), as before
        self.room_config: RoomConfigFile       # the site zone's modes file
        self.zones: ZoneSet                    # every mode zone (zones.py); only the site until a room has own_modes
        self.adapters: list[Adapter] = []      # the raw shared adapters (zones see ScopedAdapters over them)
        self._zone_watches: "list[asyncio.Task]" = []
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
        for zone in self.zones.all():
            await zone.coordinator.stop()
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
        live_off = None if smconfig.enabled("live") else \
            "live is off for this deployment (deployment.yaml capabilities.live): no screen goes live"
        if live_off:
            for sc in cfg.screens.values():
                sc.live_enabled = False
            log.warning("%s; stream_service is not contacted", live_off)
        cfg.live_off_reason = live_off
        self.events = EventHub(self.status)
        self.pages = PageHub(cfg, on_hello=lambda c: self.screens.welcome(c),
                             on_message=lambda c, t, d: self.screens.page_message(c, t, d),
                             on_closed=lambda c: self.screens.page_closed(c),
                             on_log=self.events.log_event)
        self.stream = StreamClient(cfg.stream_url, cfg.stream_token_file, self.http,
                                   http_timeout_s=cfg.timeouts.http_s, disabled=live_off)
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
        # Virtual DMX devices (the deployment's lights/patch.yaml): Art-Net in, show bus out, Hue streaming; opt-in
        # (dmx.enabled in the room config) and only with the deployment's dmx capability on.
        if cfg.dmx_enabled and not smconfig.enabled("dmx"):
            log.info("dmx is off for this deployment (deployment.yaml capabilities.dmx): no Art-Net, show bus or Hue "
                     "stream")
        elif cfg.dmx_enabled and not Path(cfg.patch_file).exists():
            log.info("dmx: no patch (%s): this site has no DMX patch", cfg.patch_file)
        elif cfg.dmx_enabled:
            try:
                self.dmx = DmxStack(cfg.patch_file, cfg.show_bus_port)
            except Exception as e:
                log.error("dmx disabled: %s", e)
        hue = self.dmx.adapter(show_url=cfg.show_url) if self.dmx is not None else hue_placeholder(cfg.patch_file)
        # Mode zones (zones.py): the rooms.json rooms with "own_modes": true, read once here. None: today's objects.
        rooms_raw = read_rooms(cfg.rooms_file)
        layout = layout_of(rooms_raw)
        built = build_adapters(self.screens.controllers, self.mask, self.events.log_event, cfg.state_dir,
                               cfg.timeouts.policy_ack_s, blinds=self._blinds, blinds_enabled=cfg.blinds_enabled,
                               sonos=None if cfg.sonos_enabled else False,
                               hue=hue, remotes=self._remotes, show=self._show, show_url=cfg.show_url,
                               modes_file=cfg.modes_file, known_screens=cfg.known_screens,
                               sensed=screens_follow_a_sensor(
                                   list(cfg.screens),
                                   cfg.ingest.intensity_json or smconfig.path("intensity.json"),
                                   cfg.destinations_file),
                               zones=layout or None)
        adapters = list({a.subsystem: a for a in built + self._extra_adapters}.values())
        self.adapters = adapters
        if not layout:                         # every deployment until a room opts in: exactly the objects of before
            no_zones = ZonePlan({}, adapters)  # (it only says when rooms.json gains a zone: restart to apply)
            self.room_config = RoomConfigFile(cfg.modes_file, adapters, cfg.state_dir, rooms_path=cfg.rooms_file,
                                              rooms_hook=no_zones.problem)
            self.coordinator = Coordinator(self.room_config.load(), adapters, Path(cfg.state_dir) / "state.json",
                                           on_event=self._on_room_event)
            self.coordinator.config_error = self.room_config.error
            self.zones = ZoneSet(Zone(SITE, None, SITE_NAME, [], self.room_config, self.coordinator),
                                 known_rooms=self._room_ids)
        else:
            self._build_zones(adapters, rooms_raw, layout)
        self.screens.revert_hook = self._revert_screen      # PUT /screens/{id} {for_s} running out
        self.events.start()
        await self.screens.start()
        if self.dmx is not None:
            await self.dmx.start()
        for zone in self.zones.all():
            await zone.coordinator.start()
        self._watch = spawn(self.room_config.watch(self._on_config_file), "modes-file-watch")
        self._zone_watches = [spawn(z.room_config.watch(lambda m, e, z=z: self._on_config_file(m, e, z)),
                                    f"modes-file-watch-{z.room}") for z in self.zones.zones.values()]
        # Members that come and go at runtime (light: the lights registered with show_service on /lights): each such
        # adapter watches its source, and the room config is rebuilt when they change.
        self._member_watches = [spawn(a.watch_members(self._on_members), f"{a.subsystem}-members")
                                for a in adapters if callable(getattr(a, "watch_members", None))]
        # Adapters that keep a device in step on their own (brightness: show_service's lights' level).
        self._member_watches += [spawn(a.keep_in_step(), f"{a.subsystem}-in-step")
                                 for a in adapters if callable(getattr(a, "keep_in_step", None))]
        if not live_off:
            self.stream.start()              # the renderer's events (live off: never contacted)
        self.mask.start()
        await self.snapshotter.start([sid for sid, sc in cfg.screens.items() if sc.live_enabled])
        log.info("room_service %s up (epoch %s); screens: %s", __version__, self.epoch,
                 ", ".join(f"{s}{f' {sc.width}x{sc.height}' if sc.width else ''}{'' if sc.live_enabled else ' (live off)'}"
                           for s, sc in cfg.screens.items()))

    # -- mode zones (zones.py) ------------------------------------------------------------------------------------------
    def _room_ids(self) -> list[str]:
        return [r["id"] for r in self.site.view()]

    def _zone_modes_path(self, room: str) -> Path:
        """An own_modes room's modes file: the deployment's room/modes.<room>.json (smconfig.room_modes_path), or the
        same name beside a modes file the config points elsewhere (a test's, a dev config's)."""
        p = smconfig.room_modes_path(None, room)
        site = Path(self.cfg.modes_file)
        return p if site == smconfig.path("room", "modes.json") else site.with_name(p.name)

    def _build_zones(self, adapters: list[Adapter], rooms_raw: dict, layout: dict) -> None:
        """The site zone and one zone per own_modes room, each with its own modes file, state and Coordinator over
        ScopedAdapters of the shared adapters. Then stale state is put right: the site's entries for targets a zone
        now owns are dropped (prune_outside), and a mode the site was in that is now one zone's moves there
        (migrate_from). Nothing is sent by either."""
        cfg = self.cfg
        plan = ZonePlan(layout, adapters)
        plan.replan(rooms_raw)

        def hook(rooms: dict) -> str | None:
            plan.replan(rooms)
            return plan.problem(rooms)
        site_ads = [ScopedAdapter(a, plan.in_site, SITE_NAME) for a in adapters]
        site_rc = RoomConfigFile(cfg.modes_file, site_ads, cfg.state_dir, rooms_path=cfg.rooms_file,
                                 owners=plan.owners_for(None), rooms_hook=hook)
        site_co = Coordinator(site_rc.load(), site_ads, Path(cfg.state_dir) / "state.json", on_event=self._on_room_event)
        site_co.config_error = site_rc.error
        zones: dict[str, Zone] = {}
        for room, z in layout.items():
            zdir = Path(cfg.state_dir) / "rooms" / room
            ads = [ScopedAdapter(a, plan.in_zone(room), z["name"]) for a in adapters]
            rc = RoomConfigFile(self._zone_modes_path(room), ads, zdir, rooms_path=cfg.rooms_file, scope_room=room,
                                owners=plan.owners_for(room), rooms_hook=hook)
            co = Coordinator(rc.load(), ads, zdir / "state.json", on_event=self._on_room_event,
                             event_type="room_mode.transition", event_extra={"room": room})
            co.config_error = rc.error
            zones[room] = Zone(room, room, z["name"], list(z["screens"]), rc, co, show=z["show"],
                               scope=plan.scopes[room])
        self.room_config, self.coordinator = site_rc, site_co
        self.zones = ZoneSet(Zone(SITE, None, SITE_NAME, [], site_rc, site_co), zones, known_rooms=self._room_ids,
                             plan=plan)
        for zone in self.zones.all():
            zone.coordinator.prune_outside(plan.moved_from(zone.room))
        sm = site_co.s["mode"]
        moved = next((n for n in (sm["target"], sm["current"]) if n and not site_co.cfg.is_concrete(n)), None)
        have = [z for z in zones.values() if moved and z.coordinator.cfg.is_concrete(moved)]
        if len(have) == 1:
            have[0].coordinator.migrate_from(site_co)
        log.info("mode zones: %s", "; ".join(f"{z.name} ({len(z.scope or ())} targets, {z.room_config.path.name})"
                                           for z in zones.values()))

    async def _revert_screen(self, screen: str, value) -> bool:
        """for_s is up: the value from before goes back as a change by hand under the mode's policy (so going back
        to the mode's own value clears the drift). False while a mode switch or another change runs: retried."""
        try:
            await self.zones.for_target(f"screens.{screen}").coordinator.hand_set(f"screens.{screen}", value,
                                                                                    source="for_s")
        except Busy:
            return False
        except Refused as e:
            log.warning("%s: for_s revert refused by the room (%s); setting it directly", screen, e)
            self.screens[screen].set_desired(value, source="for_s")
        return True

    def _on_room_event(self, e: dict) -> None:
        self.events.emit(e["type"], e["data"])
        self.events.mark_dirty()

    def _on_config_file(self, modes: RoomModes, error: str | None, zone: Zone | None = None) -> None:
        """modes.json edited on disk: reload (nothing moves until the next switch), or keep the old one and say why.
        `zone`: a room zone's file (room/modes.<room>.json); None: the site's."""
        co = zone.coordinator if zone is not None else self.coordinator
        name = zone.room_config.path.name if zone is not None else "modes.json"
        co.config_error = error
        if error:
            self.events.log_event("error", error)
        else:
            co.reload(modes, cause=f"{name} edited on disk")
            self.events.log_event("info", f"{name} reloaded (revision {modes.revision})")
        self.events.mark_dirty()

    def _on_members(self, subsystem: str) -> None:
        """An adapter's members changed (a light registered or removed on /lights): the room config is rebuilt from the
        same files, so modes, rooms.json groups and the page see them. Nothing moves until the next switch. With zones,
        each zone's (the first load re-plans the scopes from rooms.json)."""
        for zone in self.zones.all():
            rc, co = zone.room_config, zone.coordinator
            modes = rc.load()
            co.config_error = rc.error
            if rc.error is None:
                co.reload(modes, cause=f"{subsystem} members changed")
        self.events.mark_dirty()

    def swap_adapters(self, added: list, removed: set[str], cause: str) -> None:
        """Devices added or removed at runtime (a remote-controlled device in Settings › Lights): their subsystems join
        or leave the room, and the room config is rebuilt from the same files. Nothing moves until the next switch.
        With zones, every zone gets them (each through its own ScopedAdapter)."""
        self.adapters = [a for a in self.adapters if a.subsystem not in removed] + list(added)
        plan = self.zones.plan
        if plan is not None:
            plan.adapters = self.adapters
        for zone in self.zones.all():
            rc, co = zone.room_config, zone.coordinator
            if plan is None:
                wrapped = list(added)
            else:
                in_scope = plan.in_site if zone.is_site else plan.in_zone(zone.room)
                wrapped = [ScopedAdapter(a, in_scope, zone.name) for a in added]
            rc.adapters = [a for a in rc.adapters if a.subsystem not in removed] + wrapped
            for sub in removed:
                co.adapters.pop(sub, None)
            for a in wrapped:
                co.adapters[a.subsystem] = a
            modes = rc.load()
            co.config_error = rc.error
            if rc.error is None:
                co.reload(modes, cause=cause)
            else:
                self.events.log_event("error", rc.error)
        self.events.mark_dirty()

    def modes_view(self, room: str | None = None) -> dict:
        """GET /api/v1/modes: every requestable mode with its resolved state, nested the way the Room page reads it
        (screens per id, single-member subsystems flat), plus the per-target entries. `room`: an own_modes room's
        modes (its zone); none, or a room without its own modes, the site's. KeyError: a room rooms.json lacks."""
        zone = self.zones.for_room(room)
        co, rc = zone.coordinator, zone.room_config
        cfg = co.cfg
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
                a = co.adapters.get(sub)
                if a is not None and a.members == ["main"]:
                    state[sub] = v
                else:
                    state.setdefault(sub, {})[member] = v
            out[name] = {"label": m.label, "description": m.description, "icon": m.icon, "voice": m.voice,
                         "on_room_page": m.on_room_page,
                         "state": state, "targets": targets,
                         "on_enter": [s.signal for s in m.on_enter], "on_exit": [s.signal for s in m.on_exit]}
        return {"default": cfg.default, "error": rc.error, "revision": cfg.revision, "modes": out}

    async def stop(self) -> None:
        # Live sessions are left running on purpose: a restarted room_service adopts them.
        if self._watch is not None:
            self._watch.cancel()
        for t in self._member_watches + self._zone_watches:
            t.cancel()
        for zone in self.zones.all():
            await zone.coordinator.stop()
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

    def _zone_status(self, room: dict) -> tuple[dict, list[dict]]:
        """With zones: the subsystems merged across zones (a zone's members carry "room"), and status "zones"."""
        subs = {k: dict(v) for k, v in room["subsystems"].items()}
        out = []
        for z in self.zones.zones.values():
            zs = z.coordinator.status()
            for sub, s in zs["subsystems"].items():
                if not s["members"]:
                    continue
                mine = {m: {**x, "room": z.room} for m, x in s["members"].items()}
                have = subs[sub]["members"] if sub in subs and subs[sub]["members"] else {}
                merged = {**have, **mine}
                subs[sub] = {**(subs[sub] if have else s), "members": merged}
                subs[sub]["believed"] = (next(iter(merged.values()))["believed"] if len(merged) == 1
                                         else {m: x["believed"] for m, x in merged.items()})
            md = zs["mode"]
            out.append({"room": z.room, "name": z.name, "screens": list(z.screens), "show": z.show,
                        "mode": {k: md.get(k) for k in ("current", "target", "via", "transition", "last_transition",
                                                        "since_age_s", "default")},
                        "config": {"revision": zs["config"]["revision"], "error": z.room_config.error,
                                   "warnings": zs["config"]["warnings"]},
                        "stored": zs["stored"]})
        return subs, out

    def status(self) -> dict:
        m = self.mask
        room = self.coordinator.status()
        zoned = None if self.zones.single else self._zone_status(room)
        out = {
            "mode": {**room["mode"], "modes_error": self.room_config.error, "rooms_error": self.room_config.rooms_error},
            "screens": self.screens.status(),
            "rooms": self.site.view(),     # rooms.json: [{id, name, screens, lights, blinds, sensors}], file order
            "stream_service": {"reachable": self.stream.reachable, "url": self.cfg.stream_url,
                               "last_error": None if self.stream.reachable else
                               (self.cfg.live_off_reason or self.stream.last_error),   # live off: why
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
        if zoned is not None:                # present only when a room has its own modes
            out["subsystems"], out["zones"] = zoned
        return out
