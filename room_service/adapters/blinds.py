"""Bloc iQ Smart Hub (a Dooya/Motion "Connector" bridge) over its local UDP protocol.

Protocol, as implemented by the `motionblinds` library and checked against the hub on 13 Oct:
  - JSON over UDP unicast to <hub>:32100; the hub replies to the sender's port, so it works
    from WSL2 behind NAT. Its multicast pushes (238.0.0.18:32101) do NOT reach WSL.
  - GetDeviceList and ReadDevice need no key. ReadDevice answers from the hub's cache:
    no radio traffic, nothing moves.
  - WriteDevice needs AccessToken = AES-128-ECB(key, token).hex().upper(), where the token
    comes from GetDeviceList and the 16-char key from the Bloc app (Settings > About,
    tap the icon 5 times).
Position convention: 0 = fully open (raised), 100 = fully closed (lowered).
"""

from __future__ import annotations

import asyncio
import datetime
import json
import time
from dataclasses import dataclass
from typing import Any

from Cryptodome.Cipher import AES

from ..roomstate.contract import Adapter, Outcome, Reading
from ..roomstate.schema import range_schema

PORT = 32100
GATEWAY_TYPES = {"02000001", "02000002"}

OP_CLOSE, OP_OPEN, OP_STOP, OP_QUERY = 0, 1, 2, 5
OPERATION = {0: "closing", 1: "opening", 2: "stopped", 5: "query", 7: "jog_up", 8: "jog_down"}
WIRELESS = {0: "uni", 1: "bi", 2: "bi_limits", 3: "wifi", 4: "virtual_pct", 5: "other"}


class HubError(Exception):
    pass


class _Reply(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.future: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

    def datagram_received(self, data: bytes, addr) -> None:
        if not self.future.done():
            self.future.set_result(data)

    def error_received(self, exc: Exception) -> None:
        if not self.future.done():
            self.future.set_exception(exc)


@dataclass
class BlindReading:
    mac: str
    position: int | None     # 0 raised .. 100 lowered
    operation: str           # stopped | closing | opening | ...
    wireless: str            # "bi" means the motor reports its position
    battery_v: float | None
    rssi: int | None
    raw: dict


class BlocHub:
    """One hub. Requests are serialised: the hub forwards each write as a 433 MHz frame."""

    def __init__(self, ip: str, key: str | None = None, timeout_s: float = 3.0, attempts: int = 3,
                 write_gap_s: float = 0.25) -> None:
        self.ip, self.key = ip, key
        self.timeout_s, self.attempts, self.write_gap_s = timeout_s, attempts, write_gap_s
        self.mac: str | None = None
        self.device_type: str | None = None
        self.children: dict[str, str] = {}   # mac -> deviceType
        self._token: str | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _msg_id() -> str:
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S%f")[:-3]

    async def _rpc_once(self, msg: dict) -> dict:
        loop = asyncio.get_running_loop()
        transport, proto = await loop.create_datagram_endpoint(_Reply, remote_addr=(self.ip, PORT))
        try:
            transport.sendto(json.dumps({**msg, "msgID": self._msg_id()}).encode())
            return json.loads(await asyncio.wait_for(proto.future, self.timeout_s))
        finally:
            transport.close()

    async def _rpc(self, msg: dict) -> dict:
        last: Exception | None = None
        for _ in range(self.attempts):
            try:
                return await self._rpc_once(msg)
            except (asyncio.TimeoutError, OSError, ValueError) as e:
                last = e
        raise HubError(f"hub {self.ip} did not answer {msg['msgType']} after {self.attempts} tries: {last!r}")

    async def device_list(self) -> dict:
        async with self._lock:
            resp = await self._rpc({"msgType": "GetDeviceList"})
        self.mac, self.device_type, self._token = resp["mac"], resp["deviceType"], resp.get("token")
        self.children = {d["mac"]: d["deviceType"] for d in resp.get("data", [])
                         if d["deviceType"] not in GATEWAY_TYPES}
        return resp

    def _access_token(self) -> str:
        if not self.key:
            raise HubError("no hub key: see ~/.config/screen-machine/rf/bloc_hub.json")
        if not self._token:
            raise HubError("no hub token yet: call device_list() first")
        return AES.new(self.key.encode(), AES.MODE_ECB).encrypt(self._token.encode()).hex().upper()

    async def read(self, mac: str) -> BlindReading:
        """The hub's cached state for one blind. No radio traffic."""
        if not self.children:
            await self.device_list()
        async with self._lock:
            resp = await self._rpc({"msgType": "ReadDevice", "mac": mac,
                                    "deviceType": self.children.get(mac, "10000000"),
                                    "AccessToken": "0" * 32})
        d = resp.get("data") or {}
        if resp.get("msgType") != "ReadDeviceAck" or not d:
            raise HubError(f"ReadDevice {mac}: unexpected reply {resp}")
        return BlindReading(
            mac=mac,
            position=d.get("currentPosition"),
            operation=OPERATION.get(d.get("operation"), str(d.get("operation"))),
            wireless=WIRELESS.get(d.get("wirelessMode"), str(d.get("wirelessMode"))),
            battery_v=d["batteryLevel"] / 100 if "batteryLevel" in d else None,
            rssi=d.get("RSSI"),
            raw=d,
        )

    async def write(self, mac: str, data: dict) -> dict:
        """Send a command to one blind. This transmits on 433 MHz."""
        if not self._token:
            await self.device_list()
        async with self._lock:
            resp = await self._rpc({"msgType": "WriteDevice", "mac": mac,
                                    "deviceType": self.children.get(mac, "10000000"),
                                    "AccessToken": self._access_token(), "data": data})
            if resp.get("actionResult") is not None:
                # Usually a stale token or a wrong key. Refresh the token once and retry.
                fresh = await self._rpc({"msgType": "GetDeviceList"})
                self._token = fresh.get("token")
                resp = await self._rpc({"msgType": "WriteDevice", "mac": mac,
                                        "deviceType": self.children.get(mac, "10000000"),
                                        "AccessToken": self._access_token(), "data": data})
            await asyncio.sleep(self.write_gap_s)
        if resp.get("msgType") != "WriteDeviceAck" or resp.get("actionResult") is not None:
            raise HubError(f"WriteDevice {mac} {data}: rejected: {resp}")
        return resp


class BlocBlinds(Adapter):
    """The hub's blinds as one level subsystem (contract v1.0, ROOM_STATE_MODEL §5.4): members are channels, values
    are positions 0..100 (0 raised, 100 lowered; normalize() maps "raised"/"lowered" to 0/100).

    0 and 100 use the motor's own open/close, which stop on its limits; anything else uses targetPosition.
    Reads are the hub's cache (no RF). The cache shows a moving blind as stopped at its OLD position until the motor
    stops, so arrival is proven, not assumed (R1): a blind counts as arrived when it reads stopped within tolerance of
    its target, and, when opts["pending"] says it is being re-targeted while an earlier command may still be running,
    a reading equal to that `from` or an `earlier` value only counts once `earlier_until` has passed. A blind seen
    stopped somewhere else after the command gets it re-sent once. A cancel sends no STOP.
    Writes are off (dry_run) until writes_enabled, from config/room/rf/blinds.json after the owner-present test; the
    coordinator then sends nothing, and apply() itself refuses (retry=False) if it is called anyway.
    """

    subsystem = "blinds"
    subsystem_label = "Blinds"
    kind = "level"
    tolerance = 3
    poll_s = 15.0                   # idle polling for changes made by hand (cache reads: no RF)
    read_timeout_s = 5.0
    ALIASES = {"raised": 0, "lowered": 100}

    def __init__(self, hub: BlocHub, macs: dict[str, str], change_time_s: float = 75.0,
                 move_poll_s: float = 0.5, margin_s: float = 15.0, writes_enabled: bool = False) -> None:
        self.hub, self.macs = hub, macs
        self.members = list(macs)
        self.change_time_s, self.margin_s = change_time_s, margin_s
        self.poll_inflight_s = move_poll_s          # 2 Hz while a command is in flight
        self.dry_run = not writes_enabled

    def value_schema(self, member: str) -> dict:
        return range_schema(0, 100, self.label(member), unit="%", step=1, default=0,
                            presets={"raised": 0, "lowered": 100}, ends=("Raised", "Lowered"))

    def label(self, member: str) -> str:
        """"Drawing room blind ch02": the room from rooms.json, the channel as the hub names it."""
        from .devices import room_of
        room = room_of("blinds", member)
        return f"{room} blind {member}" if room else f"Blind {member}"

    def words(self, member: str, value: Any) -> str:
        return {0: "Raised", 100: "Lowered"}.get(value, f"{value}% lowered") if isinstance(value, int) else str(value)

    def normalize(self, member: str, value: Any) -> int:
        """Canonical value at load (pure, offline): "raised"/"lowered" become 0/100, so reads compare equal."""
        return self.position(value)

    @classmethod
    def position(cls, value: Any) -> int:
        v = cls.ALIASES.get(value, value)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100:
            raise ValueError(f"blind position must be 0..100 or raised/lowered, got {value!r}")
        return round(v)

    async def read(self, members: list[str]) -> dict[str, Reading]:
        out = {}
        for m in members:
            r = await self.hub.read(self.macs[m])
            out[m] = Reading(r.position, moving=r.operation in ("opening", "closing"))
        return out

    async def read_detail(self, members: list[str]) -> dict[str, BlindReading]:
        return {m: await self.hub.read(self.macs[m]) for m in members}

    async def stop(self, members: list[str]) -> Outcome:
        results = {}
        for m in members:
            try:
                await self.hub.write(self.macs[m], {"operation": OP_STOP})
                results[m] = Outcome("ok", "stop sent")
            except HubError as e:
                results[m] = Outcome("failed", str(e))
        return self._overall(results)

    def _near(self, a: int | None, b: int | None) -> bool:
        return a is not None and b is not None and abs(a - b) <= self.tolerance

    def _proof(self, position: int, target: int, pending: dict | None) -> bool:
        """A stopped reading at the target proves arrival unless it could be the stale cache of a blind still finishing
        an earlier command: equal to the pending `from` or an `earlier` value before `earlier_until` (R1)."""
        if not self._near(position, target):
            return False
        if not pending or time.time() >= pending.get("earlier_until", 0):
            return True
        return not any(self._near(target, x) for x in [pending.get("from"), *pending.get("earlier", [])])

    async def _command(self, m: str, pos: int) -> None:
        cmd = {0: {"operation": OP_OPEN}, 100: {"operation": OP_CLOSE}}.get(pos, {"targetPosition": pos})
        await self.hub.write(self.macs[m], cmd)

    async def apply(self, desired: dict[str, Any], opts: dict | None = None) -> Outcome:
        """Sends each member its move, then polls the hub's cache until each one is proven at its target.

        ok = proven at the target; unconfirmed = sent, no proof within change_time_s + margin; failed = not sent (hub
        unreachable, key rejected; a bad value or writes not enabled with retry=False). Cancelling stops the wait,
        not the blinds.
        """
        pending = (opts or {}).get("pending", {})
        results: dict[str, Outcome] = {}
        if self.dry_run:
            return self._overall({m: Outcome("failed", "blind writes not enabled (dry run)", retry=False) for m in desired})
        targets: dict[str, int] = {}
        start: dict[str, int | None] = {}            # the cache before the command: "elsewhere" means neither this
        for m, v in desired.items():                 # nor the target
            try:
                pos = self.position(v)
            except ValueError as e:
                results[m] = Outcome("failed", str(e), retry=False)
                continue
            try:
                start[m] = (await self.hub.read(self.macs[m])).position
            except (KeyError, HubError):
                start[m] = None                      # unknown: any stopped reading off target gets the one re-send
            try:
                await self._command(m, pos)
                targets[m] = pos
            except (KeyError, HubError) as e:
                results[m] = Outcome("failed", str(e))
        t0 = time.monotonic()
        deadline = t0 + self.change_time_s + self.margin_s
        last: dict[str, int | None] = {}
        resent: set[str] = set()
        while len(results) < len(desired) and time.monotonic() < deadline:
            await asyncio.sleep(self.poll_inflight_s)
            for m, pos in targets.items():
                if m in results:
                    continue
                try:
                    r = await self.hub.read(self.macs[m])
                except HubError:
                    continue
                last[m] = r.position
                if r.operation != "stopped" or r.position is None:
                    continue
                if self._proof(r.position, pos, pending.get(m)):
                    results[m] = Outcome("ok", f"at {r.position} after {time.monotonic() - t0:.1f} s")
                elif not self._near(r.position, pos) and not self._near(r.position, start[m]) and m not in resent:
                    resent.add(m)                    # stopped somewhere else after the command: re-send once
                    start[m] = r.position
                    try:
                        await self._command(m, pos)
                    except HubError:
                        pass
        for m, pos in targets.items():
            if m not in results:
                results[m] = Outcome("unconfirmed", f"no arrival at {pos} within {time.monotonic() - t0:.0f} s (last {last.get(m)})")
        return self._overall(results)

    @staticmethod
    def _overall(results: dict[str, Outcome]) -> Outcome:
        rank = {"ok": 0, "unconfirmed": 1, "failed": 2}
        worst = max((o.status for o in results.values()), key=rank.get, default="ok")
        detail = "; ".join(f"{m}: {o.detail}" for m, o in results.items() if o.status != "ok")
        return Outcome(worst, detail, results, retry=all(o.retry for o in results.values()))


# --- construction from the repo's device config ----------------------------------------------------------------------

def from_config(path: str | None = None, key_path: str | None = None) -> "BlocBlinds":
    """BlocBlinds for the 13 hub blinds in config/room/rf/blinds.json; the hub key from ~/.config/screen-machine/rf/.
    Writes stay off (dry_run) unless blinds.json says "writes_enabled": true (after the owner-present test, Q11)."""
    import json
    import stat
    from pathlib import Path
    cfg_path = Path(path) if path else Path(__file__).resolve().parents[2] / "config" / "room" / "rf" / "blinds.json"
    cfg = json.loads(cfg_path.read_text())
    kp = Path(key_path) if key_path else Path.home() / ".config" / "screen-machine" / "rf" / "bloc_hub.json"
    key = None
    if kp.exists() and not stat.S_IMODE(kp.stat().st_mode) & 0o077:
        key = (json.loads(kp.read_text()).get("key") or "").strip() or None
    travel = [t for ch in cfg.get("travel_time_s", {}).get("per_channel", {}).values() for t in ch.values()]
    kwargs = {"change_time_s": max(travel)} if travel else {}
    return BlocBlinds(BlocHub(cfg["hub"]["ip"], key), {n: b["mac"] for n, b in cfg["blinds"].items()},
                      writes_enabled=bool(cfg.get("writes_enabled", False)), **kwargs)


def placeholder_from_config(path: str | None = None):
    """The same 13 members as from_config(), with nothing behind them (room config `blinds: {enabled: false}`)."""
    import json
    from pathlib import Path
    from ..roomstate.contract import Placeholder
    cfg_path = Path(path) if path else Path(__file__).resolve().parents[2] / "config" / "room" / "rf" / "blinds.json"
    try:
        members = list(json.loads(cfg_path.read_text())["blinds"])
    except (OSError, ValueError, KeyError):
        members = [f"ch{n:02d}" for n in range(2, 15)]
    p = Placeholder("blinds", members)
    p.subsystem_label = BlocBlinds.subsystem_label
    return p
