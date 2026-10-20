"""Fake adapters for scenario tests (ROOM_STATE_MODEL §8): no I/O, no RF, millisecond travel times.

Every knob is an attribute, so a test can change it mid-run (`r.blinds.offline = True`).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .contract import Adapter, Outcome, Reading


def _near(x: Any, y: Any, tolerance: float) -> bool:
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (x, y)):
        return abs(x - y) <= tolerance
    return x == y


def _worst(results: dict[str, Outcome]) -> Outcome:
    rank = {"ok": 0, "unconfirmed": 1, "failed": 2}
    worst = max((o.status for o in results.values()), key=rank.get, default="ok")
    return Outcome(worst, "; ".join(f"{m}: {o.detail}" for m, o in results.items() if o.status != "ok"), results)


class FakeLevel(Adapter):
    """A readable subsystem whose members take `travel_s` to change (blinds, TVs, the mask, the page, Sonos, the plug).

    The device and a naive adapter in one: apply() commands each member's motor, then polls its own readings until each
    member reads stopped at its value, as BlocBlinds does, so a stale reading fools it the way it would fool a real
    adapter that ignores opts["pending"]; the coordinator's proof of arrival has to catch that. A member's value jumps
    to its target when its motor stops (so stale_until_stop and reports_moving=False look the same here).
    Knobs:
      cancel            "keep_moving" (default: a cancelled apply stops waiting and the motor carries on, like Bloc and
                        the screens controller) or "stop_halfway" (aa's original)
      stale_until_stop  reads give the pre-move value with moving=False until the motor stops (the Bloc cache)
      retarget          False: a command sent while a member is moving is ignored
      readback_lag_s    read() shows the previous value this long after a stop (a slow TV readback; apply() already
                        knows, as an adapter that trusts its own ack would)
      reports_moving    False: reads never say moving
      hang_apply, hang_read   the call doesn't return while the flag is set
      cancel_cleanup_s  a cancelled apply takes this long to let go; swallow_cancel: it returns instead of raising
      offline           read() raises, apply() fails every member
      fail              members that never move (no arrival); refuse: members refused on purpose (failed, retry=False)
      persist           False: restart() forgets what was applied (in-memory state, contract rule 10)
    """

    kind = "level"

    def __init__(self, subsystem: str, initial: dict[str, Any], travel_s: float = 0.02, tolerance: float = 0,
                 poll_s: float = 0.01, schemas: dict[str, dict] | None = None, actions: dict[str, dict] | None = None,
                 **knobs: Any) -> None:
        self.subsystem, self.members = subsystem, list(initial)
        self.schemas, self.actions = schemas or {}, actions or {}
        self.initial, self.value = dict(initial), dict(initial)
        self.moving: dict[str, Any] = {}             # member -> where its motor is heading
        self.travel_s, self.tolerance, self.poll_s = travel_s, tolerance, poll_s
        self._change_time_s: float | None = None
        self.cancel, self.stale_until_stop, self.retarget, self.reports_moving = "keep_moving", False, True, True
        self.readback_lag_s, self.cancel_cleanup_s = 0.0, 0.0
        self.hang_apply = self.hang_read = self.swallow_cancel = self.offline = False
        self.fail: set[str] = set()
        self.refuse: set[str] = set()
        self.refuse_detail = "refused"
        self.persist = True
        for k, v in knobs.items():
            if not hasattr(self, k):
                raise TypeError(f"FakeLevel has no knob {k!r}")
            setattr(self, k, v)
        self._motors: dict[str, asyncio.Task] = {}
        self._was: dict[str, tuple[Any, float]] = {}   # member -> (value before its last stop, shown until)
        self.commands: list[dict] = []               # every apply() call, in order
        self.signals: list[tuple] = []

    @property
    def change_time_s(self) -> float:                # follows travel_s unless set
        return self.travel_s * 5 if self._change_time_s is None else self._change_time_s

    @change_time_s.setter
    def change_time_s(self, value: float) -> None:
        self._change_time_s = value

    @property
    def poll_inflight_s(self) -> float:
        return max(0.001, self.travel_s / 4)

    def value_schema(self, member: str) -> dict:
        return self.schemas.get(member, self.schemas.get("*", {}))

    def hand_set(self, member: str, value: Any) -> None:
        """Someone used the remote or the app (an instant move)."""
        self._stop(member)
        self.value[member] = value

    def restart(self) -> None:
        """room_service restarted: what it applied survives only with persist (the mask's hold, the page policy)."""
        if not self.persist:
            for m in list(self.moving):
                self._stop(m)
            self.value = dict(self.initial)

    def _reading(self, m: str, lag: bool = False) -> Reading:
        was = self._was.get(m) if lag else None
        value = was[0] if was and asyncio.get_running_loop().time() < was[1] else self.value[m]
        return Reading(value, moving=m in self.moving and self.reports_moving and not self.stale_until_stop)

    async def read(self, members: list[str]) -> dict[str, Reading]:
        while self.hang_read:
            await asyncio.sleep(0.001)
        if self.offline:
            raise ConnectionError(f"{self.subsystem} unreachable")
        return {m: self._reading(m, lag=True) for m in members}

    def _start(self, m: str, v: Any) -> None:
        """One command to one member's motor."""
        if m in self.fail or (m in self.moving and (not self.retarget or self.moving[m] == v)):
            return                                   # deaf; ignored mid-travel; or already heading there
        self._stop(m)
        self.moving[m] = v
        self._motors[m] = asyncio.get_running_loop().create_task(self._travel(m, v))

    async def _travel(self, m: str, v: Any) -> None:
        await asyncio.sleep(self.travel_s)
        self._was[m] = (self.value[m], asyncio.get_running_loop().time() + self.readback_lag_s)
        self.value[m] = v
        self.moving.pop(m, None)
        self._motors.pop(m, None)

    def _stop(self, m: str, halfway: bool = False) -> None:
        task, v = self._motors.pop(m, None), self.moving.pop(m, None)
        if task:
            task.cancel()
        a = self.value.get(m)
        if halfway and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (a, v)):
            self.value[m] = (a + v) / 2

    async def apply(self, desired: dict[str, Any], opts: dict | None = None) -> Outcome:
        self.commands.append({"desired": dict(desired), "opts": dict(opts or {})})
        while self.hang_apply:
            await asyncio.sleep(0.001)
        if self.offline:
            return _worst({m: Outcome("failed", "unreachable") for m in desired})
        results = {m: Outcome("failed", self.refuse_detail, retry=False) for m in desired if m in self.refuse}
        for m, v in desired.items():
            if m not in results:
                self._start(m, v)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.change_time_s + 0.05   # a little grace for a busy test loop
        try:
            while True:
                for m, v in desired.items():
                    r = self._reading(m)
                    if m not in results and not r.moving and _near(r.value, v, self.tolerance):
                        results[m] = Outcome("ok")
                if len(results) == len(desired) or loop.time() >= deadline:
                    break
                await asyncio.sleep(self.poll_inflight_s)
        except asyncio.CancelledError:               # stops the wait; with keep_moving the motors carry on
            if self.cancel == "stop_halfway":
                for m in desired:
                    self._stop(m, halfway=True)
            if self.cancel_cleanup_s:
                await asyncio.sleep(self.cancel_cleanup_s)
            if self.swallow_cancel:
                return Outcome("unconfirmed", "cancelled")
            raise
        for m in desired:
            results.setdefault(m, Outcome("unconfirmed", "no arrival report"))
        return _worst(results)

    async def signal(self, member: str | None, action: str, args: dict) -> Outcome:
        self.signals.append((member, action, dict(args)))
        return Outcome("ok")


class FakeScreens(FakeLevel):
    """The screens controller: "art", or a live spec {"live": {...}}. An entry is a handshake: `go` comes go_after_s
    after the request and the stream is up entry_s later. "art" before go aborts the entry unseen; after go the entry
    completes and then exits (exit_s), a matched cut either way. A spec change while live is patched in-stream. Reads say
    moving while the controller works. give_up(): the controller's failed phase for the live members: read() gives
    "failed" and a live apply fails with retry=False; sending "art" clears it (as every set_desired does)."""

    def __init__(self, members: list[str], go_after_s: float = 0.02, entry_s: float = 0.02, exit_s: float = 0.02) -> None:
        super().__init__("screens", {m: "art" for m in members}, travel_s=go_after_s + entry_s)
        self.go_after_s, self.entry_s, self.exit_s = go_after_s, entry_s, exit_s
        self.want = dict(self.value)
        self.events: list[tuple[str, str]] = []    # (member, entry | go | abort | live | exit | patch)
        self.refuse_detail = "controller gave up"

    @property
    def change_time_s(self) -> float:
        return (self.go_after_s + self.entry_s + self.exit_s) * 2

    def give_up(self) -> None:
        self.refuse = {m for m, v in self.value.items() if v != "art"}
        for m in self.refuse:
            self.value[m] = "art"                   # on glass it is art again

    def _reading(self, m: str, lag: bool = False) -> Reading:
        return Reading("failed" if m in self.refuse else self.value[m], moving=m in self.moving)

    def _start(self, m: str, v: Any) -> None:
        self.want[m] = v
        self.moving[m] = v
        if m not in self._motors:
            self._motors[m] = asyncio.get_running_loop().create_task(self._drive(m))

    async def _drive(self, m: str) -> None:
        try:
            while self.value[m] != self.want[m]:
                want = self.want[m]
                if want == "art":
                    self.events.append((m, "exit"))
                    await asyncio.sleep(self.exit_s)
                    self.value[m] = "art"
                elif self.value[m] != "art":
                    self.events.append((m, "patch"))
                    self.value[m] = want
                else:
                    self.events.append((m, "entry"))
                    await asyncio.sleep(self.go_after_s)
                    if self.want[m] == "art":
                        self.events.append((m, "abort"))   # before go: the page never left art
                        continue
                    self.events.append((m, "go"))
                    await asyncio.sleep(self.entry_s)
                    self.value[m] = want
                    self.events.append((m, "live"))
        finally:
            self.moving.pop(m, None)
            self._motors.pop(m, None)

    async def apply(self, desired: dict[str, Any], opts: dict | None = None) -> Outcome:
        self.refuse -= {m for m, v in desired.items() if v == "art"}
        return await super().apply(desired, opts)


class FakeEdge(Adapter):
    """A send-only subsystem (the mirror ball on an RF relay): discrete (separate on/off codes) or toggle (one code
    flips it). `physical` is what the device really does. offline: apply() raises; delay_s: a send takes this long."""

    def __init__(self, subsystem: str, members: list[str], kind: str = "edge_discrete", physical: str = "stopped",
                 on: str = "spinning", off: str = "stopped", schema: dict | None = None) -> None:
        self.subsystem, self.members, self.kind = subsystem, members, kind
        self.schema = schema or {}
        self.on, self.off = on, off
        self.physical = {m: physical for m in members}
        self.change_time_s = 0.05
        self.offline, self.delay_s = False, 0.0
        self.sent: list[dict] = []

    def value_schema(self, member: str) -> dict:
        return self.schema

    async def apply(self, desired: dict[str, Any], opts: dict | None = None) -> Outcome:
        if self.offline:
            raise ConnectionError(f"{self.subsystem} unreachable")
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        self.sent.append(dict(desired))
        for m, v in desired.items():
            if self.kind == "edge_toggle":
                self.physical[m] = self.off if self.physical[m] == self.on else self.on
            else:
                self.physical[m] = v
        return _worst({m: Outcome("ok") for m in desired})


class FakeSignals(Adapter):
    """An action-only subsystem (log; later Maestro cues): records every (member, action, args) it sent.
    delay_s: a send takes this long, so a test can kill the process mid-send."""

    kind = "action"

    def __init__(self, subsystem: str = "log", delay_s: float = 0.0) -> None:
        self.subsystem, self.members, self.delay_s = subsystem, [], delay_s
        self.sent: list[tuple] = []

    async def signal(self, member: str | None, action: str, args: dict) -> Outcome:
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        self.sent.append((member, action, dict(args)))
        return Outcome("ok")


class Clock:
    """Wall time plus an offset a test can advance (retry backoff, max_age_s), so real sleeps still count."""

    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return time.time() + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += seconds


async def hard_kill(c) -> None:
    """A SIGKILL mid-run: the state file as it is now, whatever the cancel handlers would write. Devices carry on."""
    snapshot = c.file.path.read_bytes()
    tasks = [t for t in [c._task, *c._rtasks, *c._loops] if t and not t.done()]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    c.file.path.write_bytes(snapshot)
