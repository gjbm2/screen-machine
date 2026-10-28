"""A light worked by its RF remote through the RM4 Pro (broadlink_rf.py): the pinspots, config/room/rf/pinspot.json.
Two pinspots answer the same remote, so they are one device and always do the same.

Nothing reads the light back. The room keeps its own record of what the light is doing (persisted in the state dir,
so it survives a restart) and reaches any wanted state by pressing the remote's buttons in order, each spaced gap_s:
power, colour, brightness steps, then the pulse and flash toggles. One subsystem, so a mode's whole look is planned at
once. Members:
  main    on | off      the "on" and "off" buttons (or one "toggle")
  colour  the file's "colours", each its own button ("white" and the six others)
  level   1..steps      "up" and "down" move one step; an unknown level goes via the floor first (down x steps)
  pulse   on | off      one toggle button
  flash   on | off      one toggle button (strobe)
Settings asked for while it's off are kept and sent when it next comes on (it doesn't take the remote while off);
the room reads them as the light's settings meanwhile.

Reset (action "reset", also on the Lights setup page and the Room page): for a light that lost power at the wall,
which comes back on white at full with no pulse or flash: on, white, down x steps, off. The room then knows exactly
what it is doing. A pulse or flash the room never sent counts as off.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from ..roomstate.contract import Adapter, Outcome, Placeholder, Reading
from ..roomstate.schema import enum_schema, range_schema
from .broadlink_rf import Remote, RFError

log = logging.getLogger("room_service.rf_light")

MEMBERS = ("main", "colour", "level", "pulse", "flash")
TOGGLES = ("pulse", "flash")
ON_OFF = ("on", "off")
WHITE = "white"


def placeholder(raw: dict) -> Placeholder:
    """The light without python-broadlink: its members and values from the file, nothing sent."""
    steps = int(raw.get("steps", 16))
    p = Placeholder(str(raw.get("subsystem") or "pinspot"), list(MEMBERS),
                    {"main": list(ON_OFF), "colour": list(raw.get("colours") or {WHITE: "White"}),
                     "level": list(range(1, steps + 1)), "pulse": list(ON_OFF), "flash": list(ON_OFF)})
    p.subsystem_label = raw.get("label") or None
    return p


def plan(now: dict, desired: dict, kept: dict, steps: int, codes: set[str]) -> tuple[list[tuple[str, int, dict]], dict]:
    """The presses that take the light from `now` (the record: a None value is unknown) to `desired` (some members),
    with `kept` (settings asked for while it was off). Returns ([(button, times, the record's change once sent)], the
    settings still kept afterwards). Pure: tested on its own."""
    target = desired.get("main", now.get("main"))
    want = {**kept, **{m: v for m, v in desired.items() if m != "main"}}
    presses: list[tuple[str, int, dict]] = []
    absolute = {"on", "off"} <= codes
    if target != "on":
        if target == "off" and now.get("main") != "off":
            presses.append(("off" if absolute else "toggle", 1, {"main": "off"}))
        # it doesn't take the remote while off: keep what differs for the next time it comes on
        return presses, {m: v for m, v in want.items() if v != now.get(m)}
    if now.get("main") != "on":                  # off, or unknown (a toggle then assumes off)
        presses.append(("on" if absolute else "toggle", 1, {"main": "on"}))
    colour = want.get("colour")
    if colour is not None and colour != now.get("colour"):
        presses.append((str(colour), 1, {"colour": colour}))
    level = want.get("level")
    if level is not None:
        level = max(1, min(steps, int(level)))
        cur = now.get("level")
        if cur is None:                          # unknown: from the floor up
            presses.append(("down", steps, {"level": 1}))
            cur = 1
        if level > cur:
            presses.append(("up", level - cur, {"level": level}))
        elif level < cur:
            presses.append(("down", cur - level, {"level": level}))
    for t in TOGGLES:
        v = want.get(t)
        if v is not None and v != (now.get(t) or "off"):   # never sent: off, as after power at the wall
            presses.append((t, 1, {t: v}))
    return presses, {}


class RFLightAdapter(Adapter):
    """The light's state (contract v1.0), a level kind whose read() is the room's own record of what was sent."""

    kind = "level"
    poll_s = 5.0
    read_timeout_s = 1.0
    poll_all = True                              # reads are free

    def __init__(self, remote: Remote, state_dir: str | Path | None = None) -> None:
        raw = remote.file.raw
        self.remote, self.file = remote, remote.file
        self.subsystem = str(raw.get("subsystem") or "pinspot")
        self.members = list(MEMBERS)
        self._label = str(raw.get("label") or self.subsystem)
        self.subsystem_label = self._label
        self.state_path = Path(state_dir) / f"rf-light-{remote.id}.json" if state_dir else None
        self.now: dict[str, Any] = {m: None for m in MEMBERS}
        self.kept: dict[str, Any] = {}
        if self.state_path and self.state_path.exists():
            try:
                saved = json.loads(self.state_path.read_text())
                self.now.update({m: saved.get(m) for m in MEMBERS})
                self.kept = {m: v for m, v in (saved.get("kept") or {}).items() if m in MEMBERS}
            except (OSError, ValueError) as e:
                log.warning("%s not read (%s); the light starts unknown", self.state_path, e)

    # Live from the file (a code learned or a colour renamed while running counts at once).
    @property
    def steps(self) -> int:
        return max(1, int(self.file.fresh().get("steps", 16)))

    @property
    def gap_s(self) -> float:
        return float(self.file.fresh().get("gap_s", 0.3))

    @property
    def colours(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in (self.file.fresh().get("colours") or {}).items()}

    @property
    def change_time_s(self) -> float:            # the longest plan: power, colour, the floor and back up, two toggles
        return 8.0 + self.gap_s * (2 * self.steps + 6)

    @property
    def dry_run(self) -> bool:
        codes = self.file.codes
        return not ({"on", "off"} <= codes.keys() or "toggle" in codes)

    @property
    def actions(self) -> dict[str, dict]:
        return {"reset": {"label": "Reset (after it lost power at the wall)", "schema": {"type": "object"}},
                "press": {"label": "Press a button on the remote",
                          "schema": {"type": "object", "properties": {"button": {"enum": sorted(self.file.codes)}}}}}

    def value_schema(self, member: str) -> dict:
        if member == "main":
            s = enum_schema(list(ON_OFF), self._label, {"on": "On", "off": "Off"}, default="off")
            s["x-ui"]["switch"] = {"on": "on", "off": "off"}
            return s
        if member == "colour":
            c = self.colours or {WHITE: "White"}
            return enum_schema(list(c), f"{self._label} colour", c, default=WHITE if WHITE in c else next(iter(c)))
        if member == "level":
            return range_schema(1, self.steps, f"{self._label} brightness", default=self.steps, ends=("Dim", "Full"))
        if member in TOGGLES:
            s = enum_schema(list(ON_OFF), f"{self._label} {member}", {"on": "On", "off": "Off"}, default="off")
            s["x-ui"]["switch"] = {"on": "on", "off": "off"}
            return s
        return {}

    def label(self, member: str) -> str:
        return self._label if member == "main" else str(self.value_schema(member).get("x-ui", {}).get("label", member))

    def state(self) -> dict:
        """What the light is doing (None: unknown) and what it will do when it next comes on."""
        return {"now": dict(self.now), "kept": dict(self.kept)}

    def _save(self) -> None:
        if not self.state_path:
            return
        try:
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({**self.now, "kept": self.kept}))
            tmp.replace(self.state_path)
        except OSError as e:
            log.warning("%s not saved: %s", self.state_path, e)

    async def read(self, members: list[str]) -> dict[str, Reading]:
        return {m: Reading(self.now.get(m) if m == "main" else self.kept.get(m, self.now.get(m))) for m in members}

    async def _run(self, presses: list[tuple[str, int, dict]]) -> None:
        """Send each press in order, the record following each one sent (a failure part way leaves the truth)."""
        first = True
        for button, times, change in presses:
            packet = self.file.code(button)
            for _ in range(times):
                if not first:
                    await asyncio.sleep(self.gap_s)
                first = False
                await self.remote.rm.send(packet)
            self.now.update(change)
            self._save()

    async def apply(self, desired: dict[str, Any], opts: dict) -> Outcome:
        if self.dry_run:
            return Outcome("failed", f"no on/off codes learned yet ({self.file.path.name})", retry=False)
        bad = [m for m in desired if m not in MEMBERS]
        if bad:
            return Outcome("failed", f"no member {', '.join(bad)}", retry=False)
        async with self.remote.lock:
            presses, kept = plan(self.now, desired, self.kept, self.steps, set(self.file.codes))
            missing = sorted({b for b, _, _ in presses if b not in self.file.codes})
            if missing:
                return Outcome("failed", f"not learned yet: {', '.join(missing)}", retry=False)
            try:
                await self._run(presses)
            except RFError as e:
                return Outcome("failed", str(e))
            self.kept = kept
            self._save()
        words = ", ".join(f"{b} x{n}" if n > 1 else b for b, n, _ in presses) or "nothing to send"
        log.info("%s: %s -> %s", self._label, desired, words)
        return Outcome("ok", words)

    async def reset(self) -> Outcome:
        codes = self.file.codes
        presses: list[tuple[str, int, dict]] = []
        if "on" in codes:                        # a toggle-only remote: it is already on after power at the wall
            presses.append(("on", 1, {"main": "on"}))
        if WHITE in codes:
            presses.append((WHITE, 1, {"colour": WHITE}))
        presses.append(("down", self.steps, {"level": 1}))
        presses.append(("off" if "off" in codes else "toggle", 1, {"main": "off"}))
        missing = sorted({b for b, _, _ in presses if b not in codes})
        if missing:
            return Outcome("failed", f"not learned yet: {', '.join(missing)}", retry=False)
        async with self.remote.lock:
            try:
                await self._run(presses)
            except RFError as e:
                return Outcome("failed", str(e))
            self.now.update({"colour": WHITE if WHITE in codes else self.now.get("colour"), "pulse": "off",
                             "flash": "off"})
            self.kept = {}
            self._save()
        log.info("%s: reset (%s)", self._label, ", ".join(b for b, _, _ in presses))
        return Outcome("ok", "reset: dimmed to the floor and off")

    def note(self, button: str) -> None:
        """A button pressed by hand (the setup page's Test): the record follows, as the light would."""
        n = self.now
        if button in ON_OFF:
            n["main"] = button
        elif button == "toggle" and n["main"] is not None:
            n["main"] = "off" if n["main"] == "on" else "on"
        elif n["main"] != "off":                 # it doesn't take the remote while off
            if button in self.colours:
                n["colour"] = button
            elif button in ("up", "down") and n["level"] is not None:
                n["level"] = max(1, min(self.steps, n["level"] + (1 if button == "up" else -1)))
            elif button in TOGGLES:
                n[button] = "off" if n[button] == "on" else "on"
        self._save()

    async def signal(self, member: str | None, action: str, args: dict) -> Outcome:
        if action == "reset":
            return await self.reset()
        if action != "press":
            return Outcome("failed", f"{self.subsystem} has no action {action!r}")
        button, times = str((args or {}).get("button", "")), max(1, int((args or {}).get("times", 1)))
        if button not in self.file.codes:
            return Outcome("failed", f"no code learned for {button!r}")
        async with self.remote.lock:
            try:
                for i in range(times):
                    if i:
                        await asyncio.sleep(self.gap_s)
                    await self._run([(button, 1, {})])
                    self.note(button)
            except RFError as e:
                return Outcome("failed", str(e))
        return Outcome("ok", f"pressed {button}" + (f" x{times}" if times > 1 else ""))
