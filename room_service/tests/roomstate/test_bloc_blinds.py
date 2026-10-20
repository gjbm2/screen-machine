"""BlocBlinds (contract v1.0) against a fake hub that behaves like the real one's cache. No network.

Run from experiments/rf-spike:  .venv/bin/python -m pytest -q rfspike/tests
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from pathlib import Path

from room_service.adapters.blinds import BlindReading, BlocBlinds, HubError
from room_service.roomstate.coordinator import Coordinator
from room_service.roomstate.fakes import FakeEdge, FakeLevel, FakeSignals
from room_service.roomstate.model import RoomConfig

MACS = {"ch02": "m02", "ch03": "m03", "ch14": "m14"}


class FakeHub:
    """The hub's cache: a write starts a move after `lag` reads; the blind travels `step` per read, then stops.
    stale: the cache shows the old position, 'stopped', until the motor stops (as measured on 13 Oct).
    retarget=False: a write while a blind is moving is ignored."""

    def __init__(self, positions: dict[str, int], lag: int = 2, step: int = 40, stale: bool = False,
                 retarget: bool = True) -> None:
        self.pos = dict(positions)
        self.op = {m: "stopped" for m in positions}
        self.target: dict[str, int] = {}
        self.wait: dict[str, int] = {}
        self.lag, self.step, self.stale, self.retarget = lag, step, stale, retarget
        self.shown = dict(positions)                   # what the stale cache last showed
        self.deaf: set[str] = set()                    # weak RSSI: the command never reaches the motor
        self.down = False
        self.writes: list[tuple[str, dict]] = []

    async def write(self, mac: str, data: dict) -> dict:
        if self.down:
            raise HubError("hub did not answer WriteDevice")
        self.writes.append((mac, data))
        if mac in self.deaf or (mac in self.target and not self.retarget):
            return {"msgType": "WriteDeviceAck"}
        if "targetPosition" in data:
            self.target[mac] = data["targetPosition"]
        elif data.get("operation") in (0, 1):
            self.target[mac] = 100 if data["operation"] == 0 else 0
        elif data.get("operation") == 2:
            self.target.pop(mac, None)
            self.op[mac] = "stopped"
            return {"msgType": "WriteDeviceAck"}
        self.wait[mac] = self.lag
        return {"msgType": "WriteDeviceAck"}

    async def read(self, mac: str) -> BlindReading:
        if self.down:
            raise HubError("hub did not answer ReadDevice")
        if mac in self.target:
            if self.wait.get(mac, 0) > 0:
                self.wait[mac] -= 1                     # cache still shows the old state
            else:
                t, p = self.target[mac], self.pos[mac]
                self.pos[mac] = min(t, p + self.step) if t > p else max(t, p - self.step)
                self.op[mac] = "closing" if t > p else "opening"
                if self.pos[mac] == t:
                    self.op[mac] = "stopped"
                    del self.target[mac]
        if self.stale and mac in self.target:
            return BlindReading(mac, self.shown.get(mac), "stopped", "bi", 11.0, -75, {})
        self.shown[mac] = self.pos[mac]
        return BlindReading(mac, self.pos[mac], self.op[mac], "bi", 11.0, -75, {})


def blinds(hub: FakeHub, change_time_s: float = 0.5, writes_enabled: bool = True) -> BlocBlinds:
    return BlocBlinds(hub, MACS, change_time_s=change_time_s, move_poll_s=0.001, margin_s=0.1,
                      writes_enabled=writes_enabled)


def test_lowering_reports_ok_only_after_the_cache_shows_arrival():
    hub = FakeHub({"m02": 0, "m03": 80, "m14": 100}, lag=3)
    out = asyncio.run(blinds(hub).apply({"ch02": "lowered", "ch03": 100}))
    assert out.status == "ok", out
    assert hub.pos["m02"] == 100 and hub.pos["m03"] == 100
    assert hub.writes == [("m02", {"operation": 0}), ("m03", {"operation": 0})]   # motor limits for 0/100


def test_intermediate_positions_use_target_position():
    hub = FakeHub({"m02": 100, "m03": 100, "m14": 100})
    out = asyncio.run(blinds(hub).apply({"ch03": 80, "ch02": 0}))
    assert out.status == "ok"
    assert ("m03", {"targetPosition": 80}) in hub.writes and ("m02", {"operation": 1}) in hub.writes
    assert hub.pos == {"m02": 0, "m03": 80, "m14": 100}


def test_a_deaf_blind_is_unconfirmed_and_the_others_ok():
    hub = FakeHub({"m02": 0, "m03": 80, "m14": 100})
    hub.deaf = {"m03"}
    out = asyncio.run(blinds(hub, change_time_s=0.05).apply({"ch02": 100, "ch03": 100}))
    assert out.status == "unconfirmed" and out.retry
    assert out.members["ch02"].status == "ok"
    assert out.members["ch03"].status == "unconfirmed" and "last 80" in out.members["ch03"].detail


def test_an_unreachable_hub_fails_every_member():
    hub = FakeHub({"m02": 0, "m03": 80, "m14": 100})
    hub.down = True
    out = asyncio.run(blinds(hub).apply({"ch02": 100, "ch03": 100}))
    assert out.status == "failed" and set(out.members) == {"ch02", "ch03"} and out.retry


def test_bad_values_fail_without_sending():
    hub = FakeHub({"m02": 0, "m03": 80, "m14": 100})
    out = asyncio.run(blinds(hub).apply({"ch02": 120, "ch03": "half"}))
    assert out.status == "failed" and hub.writes == []
    assert not out.retry and not out.members["ch02"].retry                # v1.0: retrying a bad value is pointless


def test_read_reports_moving():
    hub = FakeHub({"m02": 0, "m03": 80, "m14": 100}, lag=0, step=10)

    async def body():
        a = blinds(hub)
        await hub.write("m02", {"operation": 0})
        r = await a.read(["ch02", "ch14"])
        assert r["ch02"].moving and r["ch02"].value == 10
        assert not r["ch14"].moving and r["ch14"].value == 100
    asyncio.run(body())


# --- contract v1.0 ----------------------------------------------------------------------------------------------------

def test_v1_contract_attributes_and_normalize():
    a = blinds(FakeHub({"m02": 0, "m03": 80, "m14": 100}), writes_enabled=False)
    assert a.dry_run and (a.read_timeout_s, a.tolerance, a.poll_inflight_s) == (5.0, 3, 0.001)
    assert BlocBlinds(None, MACS).change_time_s == 75.0 and BlocBlinds(None, MACS).dry_run   # writes off by default
    assert (a.normalize("ch02", "lowered"), a.normalize("ch02", "raised"), a.normalize("ch02", 42.4)) == (100, 0, 42)


def test_dry_run_sends_nothing_and_refuses_on_purpose():
    hub = FakeHub({"m02": 0, "m03": 80, "m14": 100})
    out = asyncio.run(blinds(hub, writes_enabled=False).apply({"ch02": 100}))
    assert out.status == "failed" and not out.retry and hub.writes == []


def test_r1_return_to_start_is_not_proof_until_the_earlier_command_could_have_finished():
    """The stale cache: ch02 was told 100 and is still travelling (the cache shows 0, stopped). Now it is sent back
    to 0. Without the hint the cache's 0 looks like arrival at once; with opts.pending it isn't proof until
    earlier_until."""
    async def body():
        hub = FakeHub({"m02": 0, "m03": 80, "m14": 100}, stale=True, lag=1000)   # the earlier move never shows
        hub.target["m02"], hub.wait["m02"] = 100, 1000
        a = blinds(hub)
        t0 = time.monotonic()
        out = await a.apply({"ch02": 0})                                         # a naive caller: fooled at once
        assert out.status == "ok" and time.monotonic() - t0 < 0.05
        hub.target["m02"], hub.wait["m02"] = 100, 1000
        pending = {"ch02": {"from": 0, "earlier": [100], "earlier_until": time.time() + 0.2}}
        t0 = time.monotonic()
        out = await a.apply({"ch02": 0}, {"pending": pending})
        assert out.status == "ok" and time.monotonic() - t0 >= 0.2
    asyncio.run(body())


def test_r1_blind_seen_stopped_elsewhere_is_re_sent_once():
    """A hub that can't re-target mid-travel: the command back to 0 is lost, the blind finishes its earlier move to 100
    and the cache then shows it there. That is 'stopped somewhere else after the command': re-sent once, and it gets
    back to 0."""
    async def body():
        hub = FakeHub({"m02": 0, "m03": 80, "m14": 100}, stale=True, retarget=False, lag=3)
        hub.target["m02"], hub.wait["m02"] = 100, 3                              # travelling down, cache shows 0
        pending = {"ch02": {"from": 0, "earlier": [100], "earlier_until": time.time() + 0.05}}
        out = await blinds(hub).apply({"ch02": 0}, {"pending": pending})
        assert out.status == "ok" and hub.pos["m02"] == 0
        assert hub.writes == [("m02", {"operation": 1}), ("m02", {"operation": 1})]
    asyncio.run(body())


def test_the_coordinator_restores_real_adapter_positions(tmp_path):
    """The coordinator driving BlocBlinds over the fake hub: store, lower, retrieve."""
    raw = json.loads((Path(__file__).parents[2] / "roomstate" / "example_room.json").read_text())
    raw = copy.deepcopy(raw)
    for t in raw["transitions"]:
        for s in t["steps"]:
            if isinstance(s, dict) and "delay" in s:
                s["delay"] = 0.001
    hub = FakeHub({"m02": 0, "m03": 80, "m14": 100})
    screens = ["north-screen", "south-screen", "devtest"]
    adapters = [blinds(hub),
                FakeLevel("tv", {"north-screen": "standby", "south-screen": "art_mode"}, travel_s=0.001),
                FakeLevel("screens", {s: "art" for s in screens}, travel_s=0.001),
                FakeLevel("sensor_mask", {"main": "follow"}, travel_s=0.001),
                FakeLevel("page", {f"{s}.timed_reload": True for s in screens}, travel_s=0.001),
                FakeLevel("sonos", {"lounge.volume": 25, "lounge.playback": "paused"}, travel_s=0.001),
                FakeLevel("plug", {"dmx_power": "off"}, travel_s=0.001),
                FakeEdge("mirror_ball", ["main"]), FakeSignals("log")]
    cfg = RoomConfig(raw, adapters)

    async def body():
        c = Coordinator(cfg, adapters, tmp_path / "state.json")
        await c.start(reconcile=False)
        await c.wait_idle()
        for mode in ("disco", "normal"):
            await c.request_mode(mode)
            await c.wait_idle()
            assert c.s["last_transition"]["status"] == "ok", c.s["last_transition"]
            if mode == "disco":
                assert hub.pos == {"m02": 100, "m03": 100, "m14": 100}
        assert hub.pos == {"m02": 0, "m03": 80, "m14": 100}
        assert c.s["stored"] == {}
    asyncio.run(body())


def test_the_coordinator_skips_dry_run_blinds(tmp_path):
    """Writes off: the blinds are read for real, their rows are 'skipped (dry run)', and the guarded entry step
    aborts (R8) unless the slice config allows it."""
    raw = json.loads((Path(__file__).parents[2] / "roomstate" / "example_room.json").read_text())
    raw["transitions"][0]["steps"][1]["on_fail"]["allow_skipped"] = True
    raw["transitions"][0]["steps"][3]["delay"] = 0.001
    hub = FakeHub({"m02": 0, "m03": 80, "m14": 100})
    adapters = [blinds(hub, writes_enabled=False), FakeSignals("log")]
    cfg = RoomConfig(raw, adapters)

    async def body():
        c = Coordinator(cfg, adapters, tmp_path / "state.json")
        await c.start(reconcile=False)
        await c.wait_idle()
        await c.request_mode("disco")
        await c.wait_idle()
        tr = c.s["last_transition"]
        rows = {r["subsystem"]: r for r in tr["steps"]}
        assert (rows["blinds.ch02"]["status"], rows["blinds.ch02"]["detail"]) == ("skipped", "dry run")
        assert tr["status"] == "partial" and "guard not enforced: blinds.ch02, blinds.ch03 skipped" in tr["detail"]
        assert hub.writes == [] and c.s["stored"]["pre_live"]["blinds.ch03"]["value"] == 80
    asyncio.run(body())
