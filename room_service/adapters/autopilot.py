"""autopilot.main: Auto on/off (docs/SHOW_ENGINE.md §8.9) as a room target, so a mode or the page can turn it on.

The value lives here, in <state_dir>/autopilot.json (contract rule 10), so it holds with show_service down; show_brain
reads it. With no state file yet it starts from modes.json's top-level autopilot.on (default off).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..roomstate.contract import Adapter, Outcome, Reading
from ..roomstate.schema import enum_schema
from .room import _load, _save

MODES_FILE = Path(__file__).resolve().parents[2] / "config" / "room" / "modes.json"


class AutopilotAdapter(Adapter):
    subsystem = "autopilot"
    subsystem_label = "Auto"
    category = "setting"
    kind = "level"
    members = ["main"]
    poll_s = 5.0
    VALUES = ("on", "off")

    def __init__(self, state_dir: str | Path, modes_file: str | Path | None = None) -> None:
        self.path = Path(state_dir) / "autopilot.json"
        self.modes_file = Path(modes_file) if modes_file else MODES_FILE

    def _seed(self) -> str:
        try:
            on = ((yaml.safe_load(self.modes_file.read_text()) or {}).get("autopilot") or {}).get("on")
        except (OSError, yaml.YAMLError, AttributeError):
            on = None
        return "on" if on is True else "off"

    def value(self) -> str:
        v = _load(self.path).get("main")
        return v if v in self.VALUES else self._seed()

    def label(self, member: str) -> str:
        return "Auto"

    def value_schema(self, member: str) -> dict:
        return enum_schema(list(self.VALUES), "Auto", {"on": "On", "off": "Off"}, default="off")

    def normalize(self, member: str, value: Any) -> Any:
        if value is True or value is False:
            return "on" if value else "off"
        if value not in self.VALUES:
            raise ValueError(f"autopilot is 'on' or 'off', got {value!r}")
        return value

    async def read(self, members: list[str]) -> dict[str, Reading]:
        return {m: Reading(self.value()) for m in members}

    async def apply(self, desired: dict[str, Any], opts: dict | None = None) -> Outcome:
        v = desired.get("main")
        if v not in self.VALUES:
            return Outcome("failed", f"{v!r} is not 'on' or 'off'", retry=False)
        _save(self.path, {"main": v})
        return Outcome("ok", "", {"main": Outcome("ok")})
