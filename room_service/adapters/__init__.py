"""Device adapters for the room coordinator (room_service.roomstate, contract v1.0, ROOM_STATE_MODEL §5)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import smconfig

from ..roomstate.contract import Adapter, Placeholder

log = logging.getLogger("room_service.adapters")


def _hub_blinds(blinds_mod) -> Adapter:
    """The Bloc hub blinds from the deployment's room/rf/blinds.json; the placeholder when the site has none (no file)
    or the file is unusable (said in the log), so a missing optional file never stops the room."""
    try:
        return blinds_mod.from_config()
    except FileNotFoundError as e:
        log.info("no hub blinds (%s): placeholder", e.filename or e)
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.error("hub blinds not loaded (%s: %s): placeholder", type(e).__name__, e)
    return blinds_mod.placeholder_from_config()


def build_adapters(controllers: dict, mask: Any, log_sink: Callable[[str, str], None], state_dir: str | Path,
                   policy_ack_s: float = 10.0, blinds: Adapter | None = None, blinds_enabled: bool = True,
                   hue: Adapter | None = None, remotes: list[Adapter] | None = None,
                   sonos: Adapter | bool | None = None, autopilot: Adapter | None = None,
                   show: Adapter | bool | None = None, lights: Adapter | bool | None = None,
                   show_url: str | None = None, modes_file: str | Path | None = None,
                   known_screens: dict[str, str] | None = None, sensed: bool | None = None,
                   zones: dict[str, dict] | None = None) -> list[Adapter]:
    """Every subsystem the room config can name. `blinds` defaults to the hub blinds from the deployment's
    room/rf/blinds.json (dry_run until writes are enabled there), or a placeholder with blinds_enabled=False, the
    deployment's blinds capability off, or no blinds.json; pass one in for tests.
    `hue` is the patch's hue.* devices (room_service.dmx.DmxStack.adapter(), or a placeholder).
    `remotes` default to the RM4 Pro devices, one file each in the deployment's room/rf/: the glitterball (mirror_ball =
    power, mirror_ball_motor = speed and direction), the pinspots (pinspot: on, colour, level, pulse, flash); pass [] to
    keep the placeholder (tests). With the rf capability off they are placeholders (broadlink_rf).
    `sonos` defaults to the zones in the deployment's room/sonos.json (SoCo); False keeps the placeholder (tests: no
    LAN), as does the deployment's sonos capability off.
    `show_url`: show_service's address for the Feel and the lights (room_service's cfg.show_url; default the
    deployment's show port). With the deployment's show capability off, both are placeholders.
    `autopilot` (Auto on/off) defaults to one kept in state_dir, seeded from `modes_file` (the room's cfg.modes_file;
    default the deployment's room/modes.json).
    `show` (the Feel: show_service's vibe, over loopback) defaults to ShowAdapter, its catalog cached in state_dir;
    False keeps a placeholder (tests: never reach the real show_service).
    `lights` (the DMX lights: light.<fixture id> for show_service's registered non-Hue lamps) defaults to LightsAdapter,
    its list of lights cached in state_dir, when `show` is the default; with a show passed in or False (tests), or
    lights=False, an empty placeholder, so a mode or rooms.json naming "light" still loads. brightness.lamps dims those
    lights too (through show_service's rig level) besides the Hue stream.
    `known_screens`: every screen the site has ({id: name}, config.known_screens), so screens/page/diag name an
    art-only screen as nothing rather than failing (Adapter.absent). `sensed`: whether a light sensor drives a live
    screen here (adapters.room.screens_follow_a_sensor; None: unknown), for sensor_mask's words.
    `zones`: the rooms with their own modes ({room: {"screens", "show"}}, room_service/zones.py): the zone-only members
    sensor_mask.<room>, brightness.<room>.screens and, for a room in the show, show.<room>.mic / .ai. None or {}: none,
    every adapter exactly as without zones."""
    from . import blinds as blinds_mod
    from . import broadlink_rf
    from . import sonos as sonos_mod
    from .autopilot import AutopilotAdapter
    from .devices import placeholders
    from .lights import LightsAdapter
    from .room import BrightnessAdapter, DiagAdapter, LogAdapter, PageAdapter, SensorMaskAdapter
    from .screens import ScreensAdapter
    from .show import ShowAdapter
    show_url = show_url or smconfig.url("show")
    if not smconfig.enabled("show"):
        if show is None or show is True:
            log.info("the deployment's show capability is off: the Feel and the DMX lights are placeholders")
            show = False
        if lights is None or lights is True:
            lights = False
    if lights is None:                       # the real show_service only when show is the real one too
        lights = show is None or show is True
    if lights is True:
        lights = LightsAdapter(url=show_url, cache_file=Path(state_dir) / "lights_rig.json")
    elif lights is False:
        lights = Placeholder("light", [])
    pushed = [bool(getattr(getattr(c, "cfg", None), "mask_push", False)) for c in controllers.values()]
    zones = zones or {}
    zone_screens = {r: list(z.get("screens") or []) for r, z in zones.items()}
    zone_kw = {"zones": zone_screens} if zones else {}
    mask_adapter = SensorMaskAdapter(mask, state_dir, sensed=sensed, pushed=any(pushed) if pushed else None, **zone_kw)
    page_adapter = PageAdapter(controllers, state_dir, policy_ack_s, known=known_screens)
    diag_adapter = DiagAdapter(controllers, state_dir, known=known_screens)
    brightness = BrightnessAdapter(controllers, getattr(hue, "output", None), state_dir,   # hue: the room's stream
                                   rig=lights if isinstance(lights, LightsAdapter) else None, **zone_kw)
    mask_adapter.restore()
    page_adapter.restore()
    diag_adapter.restore()
    brightness.restore()
    if remotes is None:
        remotes = broadlink_rf.from_config(state_dir=state_dir)
    if sonos is None and not smconfig.enabled("sonos"):
        log.info("the deployment's sonos capability is off: Sonos is a placeholder")
        sonos = False
    if sonos is None:
        sonos = sonos_mod.from_config()
    extra = [*remotes, *([sonos] if isinstance(sonos, Adapter) else [])]
    taken = {a.subsystem for a in extra}
    if show is None or show is True:
        in_show = [r for r, z in zones.items() if z.get("show", True)]
        show = ShowAdapter(url=show_url, cache_file=Path(state_dir) / "show_catalog.json",
                           **({"zones": in_show, "claims_file": Path(state_dir) / "show_claims.json"} if in_show else {}))
    elif show is False:
        show = Placeholder("show", ["main"])
    return [ScreensAdapter(controllers, known_screens), page_adapter, diag_adapter, mask_adapter, brightness, LogAdapter(log_sink),
            blinds or (_hub_blinds(blinds_mod) if blinds_enabled and smconfig.enabled("blinds")
                       else blinds_mod.placeholder_from_config()),
            *[p for p in placeholders() if p.subsystem not in taken], *extra,
            *([hue] if hue is not None else []), lights, show, autopilot or AutopilotAdapter(state_dir, modes_file=modes_file)]
