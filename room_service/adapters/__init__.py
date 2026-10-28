"""Device adapters for the room coordinator (room_service.roomstate, contract v1.0, ROOM_STATE_MODEL §5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..roomstate.contract import Adapter, Placeholder


def build_adapters(controllers: dict, mask: Any, log_sink: Callable[[str, str], None], state_dir: str | Path,
                   policy_ack_s: float = 10.0, blinds: Adapter | None = None, blinds_enabled: bool = True,
                   hue: Adapter | None = None, remotes: list[Adapter] | None = None,
                   sonos: Adapter | bool | None = None, autopilot: Adapter | None = None,
                   show: Adapter | bool | None = None, lights: Adapter | bool | None = None) -> list[Adapter]:
    """Every subsystem the room config can name. `blinds` defaults to the hub blinds from config/room/rf/blinds.json
    (dry_run until writes are enabled there), or a placeholder with blinds_enabled=False; pass one in for tests.
    `hue` is the patch's hue.* devices (room_service.dmx.DmxStack.adapter(), or a placeholder).
    `remotes` default to the RM4 Pro devices, one file each in config/room/rf/: the glitterball (mirror_ball = power,
    mirror_ball_motor = speed and direction), the pinspots (pinspot: on, colour, level, pulse, flash); pass [] to keep
    the placeholder (tests).
    `sonos` defaults to the zones in config/room/sonos.json (SoCo); False keeps the placeholder (tests: no LAN).
    `autopilot` (Auto on/off) defaults to one kept in state_dir, seeded from modes.json.
    `show` (the Feel: show_service's vibe, over loopback) defaults to ShowAdapter, its catalog cached in state_dir;
    False keeps a placeholder (tests: never reach the real show_service).
    `lights` (the DMX lights: light.<fixture id> for show_service's registered non-Hue lamps) defaults to LightsAdapter,
    its list of lights cached in state_dir, when `show` is the default; with a show passed in or False (tests), or
    lights=False, an empty placeholder, so a mode or rooms.json naming "light" still loads."""
    from . import blinds as blinds_mod
    from . import broadlink_rf
    from . import sonos as sonos_mod
    from .autopilot import AutopilotAdapter
    from .devices import placeholders
    from .lights import LightsAdapter
    from .room import BrightnessAdapter, DiagAdapter, LogAdapter, PageAdapter, SensorMaskAdapter
    from .screens import ScreensAdapter
    from .show import ShowAdapter
    if lights is None:                       # the real show_service only when show is the real one too
        lights = show is None or show is True
    if lights is True:
        lights = LightsAdapter(cache_file=Path(state_dir) / "lights_rig.json")
    elif lights is False:
        lights = Placeholder("light", [])
    mask_adapter, page_adapter = SensorMaskAdapter(mask, state_dir), PageAdapter(controllers, state_dir, policy_ack_s)
    diag_adapter = DiagAdapter(controllers, state_dir)
    brightness = BrightnessAdapter(controllers, getattr(hue, "output", None), state_dir)   # hue: the room's stream
    mask_adapter.restore()
    page_adapter.restore()
    diag_adapter.restore()
    brightness.restore()
    if remotes is None:
        remotes = broadlink_rf.from_config(state_dir=state_dir)
    if sonos is None:
        sonos = sonos_mod.from_config()
    extra = [*remotes, *([sonos] if isinstance(sonos, Adapter) else [])]
    taken = {a.subsystem for a in extra}
    if show is None or show is True:
        show = ShowAdapter(cache_file=Path(state_dir) / "show_catalog.json")
    elif show is False:
        show = Placeholder("show", ["main"])
    return [ScreensAdapter(controllers), page_adapter, diag_adapter, mask_adapter, brightness, LogAdapter(log_sink),
            blinds or (blinds_mod.from_config() if blinds_enabled else blinds_mod.placeholder_from_config()),
            *[p for p in placeholders() if p.subsystem not in taken], *extra,
            *([hue] if hue is not None else []), lights, show, autopilot or AutopilotAdapter(state_dir)]
