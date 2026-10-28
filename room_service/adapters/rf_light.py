"""A light worked by its RF remote through the RM4 Pro (broadlink_rf.py): a pinspot (device type
config/room/rf/types/pinspot.json, added in Settings › Light setup). Several pinspots on one remote are one device and
always do the same.

Nothing reads the light back. The room keeps its own record of what the light is doing (persisted in the state dir,
so it survives a restart) and reaches any wanted state by pressing the remote's buttons in order, each spaced gap_s.
One subsystem, so a mode's whole look is planned at once. Members, from the device's file:
  main    on | off      "on" and "off" buttons, or one "toggle" (the pinspot's remote has one power button)
  colour  its "colours", each its own button. "fade" (the remote's colour cycle) is one of them: the room can't know
          what it shows, so it's a colour of its own. Pressing the colour it already shows switches it off (owner,
          28 Oct), so the plan never presses the colour it has
  level   0..steps      "up" and "down" move one step; 0 is black (the pinspot: 8 steps). With "up_hold" and
                        "down_hold" learned (the remote's + and − held down), all the way up or down is one hold,
                        streamed for hold_s, like holding the remote's button (owner, 28 Oct: "the real utility here is
                        dim to black and undim from black")
  <toggle> on | off     each of its "toggles" (the pinspot: flash, the strobe), one button each
Changing colour goes through black (owner, 28 Oct: "reducing intensity of blue down to black, then changing to white,
then increasing intensity up to 16"): down to 0, the colour, then up to the level wanted (else the level it had, else
full). An unknown level goes down by every step first. Settings asked for while it's off are kept and sent when it
next comes on (it doesn't take the remote while off); the room reads them as the light's settings meanwhile.

Reset (action "reset", on the light setup page and the Room page): for a light that lost power at the wall, which comes
back on, white, at full, with no flash: white, down by every step, then power off. The room then knows exactly what it
is doing. A toggle the room never sent counts as off.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from ..roomstate.contract import Adapter, Outcome, Placeholder, Reading
from ..roomstate.schema import enum_schema, range_schema
from .broadlink_rf import Remote, RFError, clean_rf, max_frames, rf_pulses

log = logging.getLogger("room_service.rf_light")

BASE = ("main", "colour", "level")
FADE_BYTES = 960                                 # one held press: as many copies as fit (14, ~0.95 s unbroken); the
                                                 # RM4 Pro took 954 bytes and timed out on 1204 (28 Oct)
ON_OFF = ("on", "off")
WHITE = "white"


def toggles_of(raw: dict) -> dict[str, str]:
    return {str(k): str(v) for k, v in (raw.get("toggles") or {}).items()}


def placeholder(raw: dict) -> Placeholder:
    """The light without python-broadlink: its members and values from the file, nothing sent."""
    steps = int(raw.get("steps", 16))
    toggles = toggles_of(raw)
    p = Placeholder(str(raw.get("subsystem") or "pinspot"), [*BASE, *toggles],
                    {"main": list(ON_OFF), "colour": list(raw.get("colours") or {WHITE: "White"}),
                     "level": list(range(0, steps + 1)), **{t: list(ON_OFF) for t in toggles}})
    p.subsystem_label = raw.get("label") or None
    return p


def plan(now: dict, desired: dict, kept: dict, steps: int, codes: set[str], toggles: list[str]
         ) -> tuple[list[tuple[str, int, dict]], dict]:
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
    hold = {"up_hold", "down_hold"} <= codes    # the remote's hold: all the way in one go

    def to_black(cur: int | None) -> list[tuple[str, int, dict]]:
        if cur == 0:
            return []
        return [("down_hold", 1, {"level": 0})] if hold else [("down", steps if cur is None else cur, {"level": 0})]

    cur = now.get("level")
    level = want.get("level")
    if level is not None:
        level = max(0, min(steps, int(level)))
    colour = want.get("colour")
    if colour is not None and colour != now.get("colour"):
        # through black: down to 0, the colour, then back up to the level wanted, else the one it had, else full
        back = level if level is not None else (cur if cur else steps)
        presses += to_black(cur)
        presses.append((str(colour), 1, {"colour": colour}))
        cur, level = 0, back
    if level is not None and level != cur:
        if cur is None or level == 0:            # unknown, or black wanted: down all the way first
            presses += to_black(cur)
            cur = 0
        if level == steps and hold and level > cur:
            presses.append(("up_hold", 1, {"level": steps}))
        elif level > cur:
            presses.append(("up", level - cur, {"level": level}))
        elif level < cur:
            presses.append(("down", cur - level, {"level": level}))
    for t in toggles:
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
        self.toggles = list(toggles_of(raw))
        self.members = [*BASE, *self.toggles]
        self._label = str(raw.get("label") or self.subsystem)
        self.subsystem_label = self._label
        self.state_path = Path(state_dir) / f"rf-light-{remote.id}.json" if state_dir else None
        self.now: dict[str, Any] = {m: None for m in self.members}
        self.kept: dict[str, Any] = {}
        if self.state_path and self.state_path.exists():
            try:
                saved = json.loads(self.state_path.read_text())
                self.now.update({m: saved.get(m) for m in self.members})
                if isinstance(self.now["level"], int) and self.now["level"] > self.steps:   # the file has fewer steps now
                    self.now["level"] = None
                self.kept = {m: v for m, v in (saved.get("kept") or {}).items() if m in self.members}
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
    def hold_s(self) -> float:
        return float(self.file.fresh().get("hold_s", 3.5))

    @property
    def change_time_s(self) -> float:            # the longest plan: power, down to black, a colour, up, the toggles
        return 8.0 + 2 * self.hold_s + self.gap_s * (2 * self.steps + 4 + len(self.toggles))

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
        if member == "main" or member in self.toggles:
            label = self._label if member == "main" else f"{self._label} {toggles_of(self.file.fresh())[member].lower()}"
            s = enum_schema(list(ON_OFF), label, {"on": "On", "off": "Off"}, default="off")
            s["x-ui"]["switch"] = {"on": "on", "off": "off"}
            return s
        if member == "colour":
            c = self.colours or {WHITE: "White"}
            return enum_schema(list(c), f"{self._label} colour", c, default=WHITE if WHITE in c else next(iter(c)))
        if member == "level":
            return range_schema(0, self.steps, f"{self._label} brightness", default=self.steps, ends=("Black", "Full"))
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
        held = set(self.file.held)
        for button, times, change in presses:
            if button in held:
                if not first:
                    await asyncio.sleep(self.gap_s)
                first = False
                await self._hold(button)
                self.now.update(change)
                self._save()
                continue
            packet = self.file.code(button)
            for _ in range(times):
                if not first:
                    await asyncio.sleep(self.gap_s)
                first = False
                await self.remote.rm.send(packet)
            self.now.update(change)
            self._save()

    async def _hold(self, button: str) -> None:
        """The remote's button held down: its code sent over and over without a break, the way the remote does it
        (owner, 28 Oct: the light steps about ten times a second while the code keeps coming). One packet holds as
        many copies as the RM4 Pro takes (FADE_BYTES: ~0.95 s, unbroken) at the learned frequency. hold_s 0: that one
        packet. Longer: more packets, each sent as the last ends (the RM4 leaves ~0.2 s between them)."""
        raw = self.file.raw_code(button)
        one = clean_rf(raw, max_frames(raw, FADE_BYTES), gap_ms=0)
        air = sum(rf_pulses(one)) / 1e6 or 0.5
        n = 1 if self.hold_s <= air else round(self.hold_s / air)
        loop = asyncio.get_running_loop()
        at = t0 = loop.time()
        sent = []
        for _ in range(n):
            wait = at - 0.03 - loop.time()       # a little early: as small a gap as it allows
            if wait > 0:
                await asyncio.sleep(wait)
            await self.remote.rm.send(one)
            sent.append(f"{len(one)} B at {loop.time() - t0:.2f} s")
            at += air
        log.info("%s: held %s (%s): %s; %.2f s of air", self._label, button,
                 "one press" if n == 1 else f"{self.hold_s:g} s", ", ".join(sent), n * air)

    async def apply(self, desired: dict[str, Any], opts: dict) -> Outcome:
        if self.dry_run:
            return Outcome("failed", f"its power button isn't learned yet ({self.file.path.name})", retry=False)
        bad = [m for m in desired if m not in self.members]
        if bad:
            return Outcome("failed", f"no member {', '.join(bad)}", retry=False)
        async with self.remote.lock:
            presses, kept = plan(self.now, desired, self.kept, self.steps, set(self.file.codes), self.toggles)
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
        """After power at the wall it is on, white, at full: white, down to black, power off."""
        codes = self.file.codes
        presses: list[tuple[str, int, dict]] = [("on", 1, {"main": "on"})] if "on" in codes else []
        if WHITE in codes:
            presses.append((WHITE, 1, {"colour": WHITE}))
        presses.append(("down", self.steps, {"level": 0}))
        presses.append(("off" if "off" in codes else "toggle", 1, {"main": "off"}))
        missing = sorted({b for b, _, _ in presses if b not in codes})
        if missing:
            return Outcome("failed", f"not learned yet: {', '.join(missing)}", retry=False)
        async with self.remote.lock:
            try:
                await self._run(presses)
            except RFError as e:
                return Outcome("failed", str(e))
            self.now.update({"colour": WHITE if WHITE in codes else self.now.get("colour"),
                             **{t: "off" for t in self.toggles}})
            self.kept = {}
            self._save()
        log.info("%s: reset (%s)", self._label, ", ".join(b for b, _, _ in presses))
        return Outcome("ok", "reset: down to black and off")

    def note(self, button: str) -> None:
        """A button pressed by hand (the setup page's Test): the record follows, as the light would."""
        n = self.now
        if button in ON_OFF:
            n["main"] = button
        elif button == "toggle":
            n["main"] = None if n["main"] is None else "off" if n["main"] == "on" else "on"
        elif button in self.colours:             # its own colour again switches it off; another (or off) turns it on
            if n["main"] == "on" and n["colour"] == button:
                n["main"] = "off"
            else:
                n["main"], n["colour"] = "on", button
        elif n["main"] != "off":                 # it doesn't take the remote while off
            if button in ("up", "down") and n["level"] is not None:
                n["level"] = max(0, min(self.steps, n["level"] + (1 if button == "up" else -1)))
            elif button in ("up_hold", "down_hold"):     # the remote's hold, recorded: all the way
                n["level"] = self.steps if button == "up_hold" else 0
            elif button in self.toggles:
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
