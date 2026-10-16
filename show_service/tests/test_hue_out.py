"""hue_out: the Hue Entertainment driver (README S5.6), with a fake bridge and a fake DTLS streamer only.

Never the real bridge, never an Entertainment stream, never the real pairing files. Every HueOut here gets a fake
client, a fake streamer, and a patch.yaml in tmp_path; room's BridgeSecrets.load (the one hue_out uses) reads a secrets
dir in tmp_path (room_service.hue.client.SECRETS_DIR is pointed there). The module's default client and streamer
factories raise, and any socket connect or sendto to a non-loopback address raises (the guard).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import statistics
import threading
import time
from types import SimpleNamespace

import pytest
import yaml

from room_service.hue import client as room_client
from room_service.hue import pair as room_pair
from room_service.hue import stream as room_stream
from show_service import contract
from show_service import hue_out as ho

REAL_STREAMER_FACTORY = ho.default_streamer_factory    # never connected here: only its class is looked at

BRIDGE = "0011aabbccddeeff"
AREA = "11111111-2222-3333-4444-555555555555"
OTHER_AREA = "99999999-2222-3333-4444-555555555555"
OUR_RID = "auth-ours"
TEST_HOST = "198.51.100.7"          # TEST-NET-2 (documentation range): only ever handed to the fake streamer
PATCH_HOST = "198.51.100.9"         # TEST-NET-2: patch.yaml's hue.host in a tmp patch (room's rule: it wins)
LIGHTS = {"lamp.a": "light-a", "lamp.b": "light-b", "lamp.c": "light-c", "lamp.d": "light-d"}
CHANNEL = {"light-a": 0, "light-b": 1, "light-c": 2}       # light-d isn't in the area
FAST = {"takeover_check_s": 0.1, "start_wait_s": 0.3, "release_settle_s": 0.05, "release_timeout_s": 1.0,
        "error_backoff_s": 0.2}
RGB = (1.0, 0.5, 0.25)


# -- the guard ------------------------------------------------------------------------------------------------------------
def _refuse_remote(addr) -> None:
    if isinstance(addr, tuple) and addr and isinstance(addr[0], str):
        if not (addr[0].startswith("127.") or addr[0] in ("::1", "localhost")):
            raise AssertionError(f"network to {addr!r} from a hue test")


@pytest.fixture(autouse=True)
def guard(monkeypatch, tmp_path):
    def refuse(*_a, **_k):
        raise AssertionError("a default factory was called: hue tests use fakes only")
    monkeypatch.setattr(ho, "default_client_factory", refuse)
    monkeypatch.setattr(ho, "default_streamer_factory", refuse)
    for mod in (room_client, room_pair):                # room's BridgeSecrets.load reads tmp only, never the real files
        monkeypatch.setattr(mod, "SECRETS_DIR", tmp_path / "hue")
    monkeypatch.setattr(contract, "HUE_SECRETS_DIR", tmp_path / "hue")
    real_connect, real_connect_ex, real_sendto = socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto

    def connect(self, addr):
        _refuse_remote(addr)
        return real_connect(self, addr)

    def connect_ex(self, addr):
        _refuse_remote(addr)
        return real_connect_ex(self, addr)

    def sendto(self, data, *args):
        _refuse_remote(args[-1])
        return real_sendto(self, data, *args)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket.socket, "sendto", sendto)
    monkeypatch.setattr(ho, "CONTROL_WAKE_S", 0.02)
    monkeypatch.setattr(ho, "RELEASE_POLL_S", 0.02)
    yield


# -- fakes ----------------------------------------------------------------------------------------------------------------
class FakeClient:
    """aiohttp-free CLIP v2: the area's status and active_streamer are the test's to set. ``inventory`` is room's own
    HueClient.inventory over these GETs, so room's resolve_area runs unchanged."""

    inventory = room_client.HueClient.inventory

    def __init__(self, events: list) -> None:
        self.events = events
        self.calls: list[tuple] = []
        self.area = {"id": AREA, "status": "inactive", "active_streamer": None,
                     "channels": [{"channel_id": ch, "members": [{"service": {"rid": f"ent-{li}"}}]}
                                  for li, ch in CHANNEL.items()]}
        self.extra_areas: list[dict] = []
        self.lights = [{"id": li, "owner": {"rid": f"dev-{li}"}} for li in LIGHTS.values()]
        self.ents = [{"id": f"ent-{li}", "owner": {"rid": f"dev-{li}"}} for li in LIGHTS.values()]
        self.fail: Exception | None = None       # every call raises it
        self.fail_put: Exception | None = None   # PUTs raise it
        self.stop_lag = 0                        # area GETs that still read active after a stop
        self.stop_noop = False                   # a stop never deactivates the area
        self.on_start = None                     # called after a start (a test can take the area then)
        self._lag = 0

    async def get(self, rtype, rid=None):
        await asyncio.sleep(0)
        self.calls.append(("GET", rtype, rid))
        if self.fail:
            raise self.fail
        if rtype == "light":
            return [dict(x) for x in self.lights]
        if rtype == "entertainment":
            return [dict(x) for x in self.ents]
        if rtype == "entertainment_configuration":
            areas = [self.area] + self.extra_areas
            got = [dict(a) for a in areas if rid is None or a["id"] == rid]
            if rid == self.area["id"] and self._lag > 0:
                self._lag -= 1
                got[0]["status"] = "active"
            return got
        return []

    async def put(self, rtype, rid, body):
        await asyncio.sleep(0)
        self.calls.append(("PUT", rtype, rid, body.get("action")))
        self.events.append(("put", body.get("action")))
        if self.fail:
            raise self.fail
        if self.fail_put:
            raise self.fail_put
        if body.get("action") == "start":
            self.area.update(status="active", active_streamer={"rid": OUR_RID, "rtype": "auth_v1"})
            if self.on_start is not None:
                self.on_start()
        elif body.get("action") == "stop" and not self.stop_noop:
            self.area.update(status="inactive", active_streamer=None)
            self._lag = self.stop_lag
        return {"data": []}

    def puts(self, action=None):
        return [c for c in self.calls if c[0] == "PUT" and (action is None or c[3] == action)]

    def gets(self, rtype):
        return [c for c in self.calls if c[0] == "GET" and c[1] == rtype]


class FakeStreamer:
    """Records connect and every send_colors; never opens a socket."""

    def __init__(self, events: list) -> None:
        self.events = events
        self.is_connected = False
        self.connects: list[tuple] = []
        self.sent: list[list] = []
        self.disconnects = 0
        self.fail_connect: Exception | None = None
        self.during_connect = None               # called inside connect (the handshake)

    def connect(self, host, app_key, clientkey, area_id):
        self.connects.append((host, app_key, clientkey, area_id))
        self.events.append(("connect", area_id))
        if self.during_connect is not None:
            self.during_connect()
        if self.fail_connect:
            raise self.fail_connect
        self.is_connected = True

    def disconnect(self):
        self.is_connected = False
        self.disconnects += 1
        self.events.append(("disconnect", None))

    def send_colors(self, cmds):
        self.sent.append(list(cmds))


def out_cfg(enabled=True, **kw) -> dict:
    o = {"id": "hue", "driver": "hue", "enabled": enabled, "built": True, "node": None, "port_address": None,
         "latency_key": "hue", "latency": {}, "hits": None, "min_attack_ms": None, "rate_hz": None, "settings": {},
         "problems": []}
    o.update(kw)
    return o


def rig_view() -> dict:
    fx = {}
    for fid, light in LIGHTS.items():
        fx[fid] = {"id": fid, "label": fid, "type": "lamp-type", "kind": "lamp", "output": "hue", "light": light}
    fx["screen.x"] = {"id": "screen.x", "label": "x", "type": "screen-type", "kind": "screen", "output": "hue",
                      "light": "light-x"}                     # the screens rule: never a lamp of the stream
    fx["lamp.elsewhere"] = {"id": "lamp.elsewhere", "label": "e", "type": "lamp-type", "kind": "lamp",
                            "output": "other", "light": "light-e"}
    return {"revision": "test", "path": "test", "error": None, "problems": [], "outputs": {}, "nodes": {},
            "types": {}, "fixtures": fx, "groups": {}}


def write_secrets(d, bridge=BRIDGE, name=None, host=TEST_HOST) -> None:
    d.mkdir(exist_ok=True)
    rec = {"host": host, "bridge_id": bridge, "name": "test bridge", "model": "fake",
           "app_key": "test-app-key", "clientkey": "00112233445566778899aabbccddeeff"}
    if host is None:
        del rec["host"]
    (d / f"{name or bridge}.json").write_text(json.dumps(rec))


class H:
    """One HueOut on fakes, run on its own asyncio loop (show-brain's stand-in)."""

    def __init__(self, tmp_path, *, enabled=True, streamer="show", secrets=True, rig_hue=None, out=None,
                 **patch_kw) -> None:
        self.patch = tmp_path / "patch.yaml"
        self._bump = 0
        self.write_patch(streamer, **patch_kw)
        self.secrets = tmp_path / "hue"
        if secrets:
            write_secrets(self.secrets)
        self.events: list = []
        self.client, self.streamer = FakeClient(self.events), FakeStreamer(self.events)
        self.ctx = SimpleNamespace(loop=None, settings={"rig": {"hue": dict(FAST, **(rig_hue or {}))}})
        self.made = 0
        self.d = ho.HueOut("hue", ctx=self.ctx, client_factory=self._client,
                           streamer_factory=lambda: self.streamer, patch_path=self.patch)
        self.d.configure(out or out_cfg(enabled), rig_view())
        self.active = True
        self.feeding = True
        self.rgb = {fid: list(RGB) for fid in LIGHTS}

    def _client(self, secrets):
        self.made += 1
        self.secrets_seen = secrets
        return self.client

    def write_patch(self, streamer="show", **hue) -> None:
        h = {"bridge": BRIDGE, "area": AREA, "gamma": 0.6, "stop_after_s": 0.4}
        h.update(hue)
        if streamer is not None:
            h["streamer"] = streamer
        self.patch.write_text(yaml.safe_dump({"hue": h}))
        self._bump += 1                                 # a visible mtime change, even within one clock tick
        st = os.stat(self.patch)
        os.utime(self.patch, ns=(st.st_atime_ns, st.st_mtime_ns + self._bump * 1_000_000_000))

    def frame(self, active=None, rgb=None, t_ms=None) -> dict:
        rgb = rgb or self.rgb
        return {"output": "hue", "seq": 0, "t_send_ms": time.monotonic() * 1000.0 if t_ms is None else t_ms,
                "active": self.active if active is None else active, "why": None,
                "fixtures": {fid: {"intensity": max(c), "colour": c, "rgb": list(c), "t_eval_ms": 0.0}
                             for fid, c in rgb.items()}}

    async def _feeder(self) -> None:
        while True:
            if self.feeding:
                self.d.set_frame(self.frame())
            await asyncio.sleep(0.02)

    def run(self, scenario, timeout: float = 15.0):
        async def main():
            self.ctx.loop = asyncio.get_running_loop()
            self.d.start()
            feeder = asyncio.create_task(self._feeder())
            try:
                return await asyncio.wait_for(scenario(), timeout)
            finally:
                feeder.cancel()
                t = self.d.stop()
                if t is not None:
                    await t
        return asyncio.run(main())

    async def streaming(self, lights=("lamp.a", "lamp.b")) -> None:
        self.d.request(list(lights), True)
        await until(lambda: self.d.hue_status()["state"] == "streaming", what="streaming")


async def until(pred, timeout: float = 3.0, what: str = "") -> None:
    t0 = time.monotonic()
    while not pred():
        if time.monotonic() - t0 > timeout:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.005)


def cmds_for(lights_rgb: dict, gamma=0.6) -> list:
    return [(CHANNEL[LIGHTS[fid]], *ho.xy_bri(rgb, gamma)) for fid, rgb in sorted(lights_rgb.items())]


# -- the state machine -------------------------------------------------------------------------------------------------------
def test_off_by_default_never_touches_the_bridge(tmp_path):
    h = H(tmp_path, enabled=False)
    assert h.d.hue_status()["state"] == "off"
    assert "enabled is false" in h.d.hue_status()["detail"]

    async def go():
        h.d.request(["lamp.a"], True)
        await asyncio.sleep(0.3)
        assert h.d.hue_status()["state"] == "off"
    h.run(go)
    assert h.client.calls == [] and h.made == 0 and h.streamer.connects == []


def test_no_factories_stays_off_and_loads_nothing(tmp_path):
    """The defaults are lazy: a HueOut built with none, off, never calls them (the guard would raise)."""
    write_secrets(tmp_path / "hue")
    d = ho.HueOut("hue", ctx=SimpleNamespace(loop=None, settings={}), patch_path=tmp_path / "missing.yaml")
    d.configure(out_cfg(False), rig_view())

    async def go():
        d.ctx.loop = asyncio.get_running_loop()
        d.start()
        d.request(["lamp.a"], True)
        await asyncio.sleep(0.2)
        assert d.hue_status()["state"] == "off"
        t = d.stop()
        if t is not None:
            await t
    asyncio.run(go())


@pytest.mark.parametrize("word", ["room", None])
def test_waiting_room_until_patch_says_show(tmp_path, word):
    h = H(tmp_path, streamer=word)

    async def go():
        h.d.request(["lamp.a"], True)
        await until(lambda: h.d.hue_status()["state"] == "waiting_room", what="waiting_room")
        await asyncio.sleep(0.2)
        assert h.d.hue_status()["state"] == "waiting_room"
        assert contract.hue_answer(h.d.hue_status())["state"] == "idle"      # room streams: we follow nothing
    h.run(go)
    assert h.client.calls == [] and h.streamer.connects == []


def test_flip_back_to_room_stops_within_a_reload_and_forgets_the_set(tmp_path):
    """68's contract: on the flip back room sends no release (its HueOutput takes over); we stop at once, and what
    room asked is forgotten, so a later flip to show never resumes a stale set (room re-sends it then)."""
    h = H(tmp_path)
    calls = []
    h.ctx.persist = lambda: calls.append(1)

    async def go():
        await h.streaming()
        n = len(calls)
        h.write_patch("room")
        await until(lambda: h.d.hue_status()["state"] == "waiting_room", timeout=1.0, what="waiting_room")
        assert h.client.puts("stop") and not h.streamer.is_connected
        assert h.d.hue_status()["streaming"] is False
        assert h.d.persist_state()["asked"] == [] and h.d.hue_status()["wanted"] == [] and len(calls) > n
        h.write_patch("show")
        await until(lambda: h.d.hue_status()["state"] == "idle", what="idle")
        await asyncio.sleep(0.15)
        assert len(h.client.puts("start")) == 1                 # nothing resumed
        h.d.request(["lamp.a"], True)                           # room's reconcile re-applies its "dmx" members
        await until(lambda: h.d.hue_status()["state"] == "streaming", what="streaming again")
    h.run(go)


def test_a_flip_back_to_room_during_the_handshake_gives_the_area_back_before_a_frame(tmp_path):
    """README S5.12: patch.yaml is read again right after the DTLS handshake. Flipped back to room meanwhile: the area
    is stopped at once and never a frame is sent (room's 2 s patch watch may start its own stream next, and the shared
    app key makes the two streamers invisible to each other's takeover checks)."""
    h = H(tmp_path)
    states: list = []
    real_set = h.d._set
    h.d._set = lambda st, detail: (states.append(st), real_set(st, detail))
    h.streamer.during_connect = lambda: h.write_patch("room")

    async def go():
        h.d.request(["lamp.a"], True)
        await until(lambda: h.d.hue_status()["state"] == "waiting_room", what="waiting_room")
        await asyncio.sleep(0.1)
    h.run(go)
    assert "streaming" not in states and h.streamer.sent == [] and h.d.hue_status()["frames"] == 0
    assert len(h.client.puts("start")) == 1 and len(h.client.puts("stop")) == 1
    assert h.events.index(("connect", AREA)) < h.events.index(("disconnect", None)) < h.events.index(("put", "stop"))


def test_the_start_wait_reads_a_fresh_switch(tmp_path):
    """While waiting for an active area to go inactive, a flip back to room ends the wait: nothing starts."""
    h = H(tmp_path, rig_hue={"start_wait_s": 2.0})
    h.client.area.update(status="active", active_streamer={"rid": "room-still-stopping"})

    async def go():
        h.d.request(["lamp.a"], True)
        await until(lambda: h.d.hue_status()["state"] == "starting", what="starting")
        h.write_patch("room")
        h.client.area.update(status="inactive", active_streamer=None)
        await until(lambda: h.d.hue_status()["state"] == "waiting_room", timeout=1.0, what="waiting_room")
        await asyncio.sleep(0.1)
    h.run(go)
    assert h.client.puts() == [] and h.streamer.connects == []


def test_switched_off_stops_the_frames_at_once_then_the_area(tmp_path):
    """68's contract: show_service MUST stop streaming when fixtures.yaml outputs.hue.enabled goes false."""
    h = H(tmp_path)

    async def go():
        await h.streaming(("lamp.a",))
        h.feeding = False
        h.d.configure(out_cfg(False), rig_view())               # what the Rig's reconcile hands the driver
        n = len(h.streamer.sent)
        h.d.set_frame(h.frame())
        assert len(h.streamer.sent) == n                        # not one more frame
        await until(lambda: h.d.hue_status()["state"] == "off", timeout=1.0, what="off")
        assert len(h.client.puts("stop")) == 1 and not h.streamer.is_connected
        assert contract.hue_answer(h.d.hue_status())["state"] == "idle"
    h.run(go)


def test_a_request_reads_a_fresh_switch(tmp_path):
    """room flips hue.streamer to show and POSTs the set at once: the request sees the switch even before the
    control task does, so the answer is armed, not a stale "room streams"."""
    h = H(tmp_path, streamer="room")

    async def go():
        await until(lambda: h.d.hue_status()["state"] == "waiting_room", what="waiting_room")
        h.write_patch("show")
        st = h.d.request(["lamp.a"], True)                      # no await in between
        assert st["state"] == "idle" and st["room_state"] == "armed" and st["words"] == "Ready: waiting for the music"
        await until(lambda: h.d.hue_status()["state"] == "streaming", what="streaming")
        assert h.d.hue_status()["words"] == "Following the music"
    h.run(go)


def test_idle_without_a_wanted_lamp(tmp_path):
    h = H(tmp_path)

    async def go():
        await until(lambda: h.d.hue_status()["state"] == "idle", what="idle")
        await asyncio.sleep(0.1)
    h.run(go)
    assert h.client.calls == []


def test_no_pairing_file_is_no_config(tmp_path):
    h = H(tmp_path, secrets=False)

    async def go():
        h.d.request(["lamp.a"], True)
        await until(lambda: h.d.hue_status()["state"] == "no_config", what="no_config")
        assert "isn't paired" in h.d.hue_status()["detail"] and BRIDGE in h.d.hue_status()["detail"]
        assert contract.hue_answer(h.d.hue_status())["state"] == "idle"
    h.run(go)
    assert h.made == 0 and h.client.calls == []


def test_starting_then_streaming(tmp_path):
    h = H(tmp_path)

    async def go():
        await h.streaming(("lamp.a", "lamp.b"))
        st = h.d.hue_status()
        assert st["wanted"] == ["lamp.a", "lamp.b"] and st["dtls"] and st["streaming"] and st["area"] == AREA
        assert st["channels"] == CHANNEL and st["enabled"] and st["output"] == "hue"
    h.run(go)
    assert len(h.client.puts("start")) == 1
    assert h.streamer.connects == [(TEST_HOST, "test-app-key", "00112233445566778899aabbccddeeff", AREA)]
    assert isinstance(h.secrets_seen, room_client.BridgeSecrets)  # room's pairing, read by room's own loader
    assert h.secrets_seen.host == TEST_HOST                      # the pairing file's (this patch.yaml names no host)
    assert h.events.index(("put", "start")) < h.events.index(("connect", AREA))


def test_no_start_until_frames_are_active(tmp_path):
    h = H(tmp_path)
    h.active = False

    async def go():
        h.d.request(["lamp.a"], True)
        await until(lambda: h.d.hue_status()["state"] == "waiting_input", what="waiting_input")
        assert h.d.hue_status()["detail"] == "no look to show yet"
        ans = contract.hue_answer(h.d.hue_status())
        assert ans["state"] == "armed" and ans["words"] == "Ready: waiting for the music"   # following, no sound yet
        assert ans["detail"] == "no look to show yet" and ans["wanted"] == ["lamp.a"]
        await asyncio.sleep(0.2)
    h.run(go)
    assert h.client.puts() == []


def test_waiting_input_after_stop_after_s_and_back(tmp_path):
    h = H(tmp_path)                                     # patch stop_after_s 0.4

    async def go():
        await h.streaming()
        h.active = False
        t0 = time.monotonic()
        await until(lambda: h.d.hue_status()["state"] == "waiting_input", what="waiting_input")
        assert time.monotonic() - t0 >= 0.35
        assert len(h.client.puts("stop")) == 1 and not h.streamer.is_connected
        h.active = True
        await until(lambda: h.d.hue_status()["state"] == "streaming", what="streaming again")
        assert len(h.client.puts("start")) == 2
    h.run(go)


def test_area_already_active_is_waited_for(tmp_path):
    h = H(tmp_path)
    h.client.area.update(status="active", active_streamer={"rid": "someone-else"})

    async def go():
        h.d.request(["lamp.a"], True)
        await until(lambda: h.d.hue_status()["state"] == "starting", what="starting")
        await asyncio.sleep(0.1)
        assert h.client.puts() == []                    # waiting, not fighting
        h.client.area.update(status="inactive", active_streamer=None)
        await until(lambda: h.d.hue_status()["state"] == "streaming", what="streaming")
    h.run(go)
    assert len(h.client.puts("start")) == 1


def test_area_still_active_after_start_wait_is_taken_over(tmp_path):
    h = H(tmp_path)
    h.client.area.update(status="active", active_streamer={"rid": "someone-else"})

    async def go():
        h.d.request(["lamp.a"], True)
        await until(lambda: h.d.hue_status()["state"] == "taken_over", what="taken_over")
        await asyncio.sleep(0.3)
        assert h.d.hue_status()["state"] == "taken_over"
    h.run(go)
    assert h.client.puts() == [] and h.streamer.connects == []


@pytest.mark.parametrize("how", ["stopped", "another_streamer"])
def test_takeover_while_streaming(tmp_path, how):
    h = H(tmp_path)

    async def go():
        await h.streaming(("lamp.a", "lamp.b"))
        if how == "stopped":
            h.client.area.update(status="inactive", active_streamer=None)
        else:
            h.client.area.update(active_streamer={"rid": "someone-else"})
        await until(lambda: h.d.hue_status()["state"] == "taken_over", what="taken_over")
        assert not h.streamer.is_connected and h.client.puts("stop") == []      # never fight
        assert contract.hue_answer(h.d.hue_status())["state"] == "taken_over"
        h.client.area.update(status="inactive", active_streamer=None)
        await asyncio.sleep(0.3)
        h.d.request(["lamp.a", "lamp.b"], True)                                 # the same set: no restart
        await asyncio.sleep(0.2)
        assert h.d.hue_status()["state"] == "taken_over" and len(h.client.puts("start")) == 1
        h.d.request(["lamp.a"], True)                                           # room re-applies: a new set
        await until(lambda: h.d.hue_status()["state"] == "streaming", what="restart")
        assert len(h.client.puts("start")) == 2
    h.run(go)


def test_error_then_backoff_then_streaming(tmp_path):
    h = H(tmp_path, rig_hue={"error_backoff_s": 0.3})
    h.client.fail = ho.HueError(500, "boom")

    async def go():
        h.d.request(["lamp.a"], True)
        await until(lambda: h.d.hue_status()["state"] == "error", what="error")
        assert "boom" in h.d.hue_status()["detail"]
        await asyncio.sleep(0.15)
        assert len(h.client.gets("light")) == 1        # no retry inside the backoff
        h.client.fail = None
        await until(lambda: h.d.hue_status()["state"] == "streaming", what="streaming after the backoff")
        assert h.d.status()["errors"] >= 1
    h.run(go)


def test_a_failed_handshake_stops_the_area_it_started(tmp_path):
    h = H(tmp_path)
    h.streamer.fail_connect = OSError("handshake timed out")

    async def go():
        h.d.request(["lamp.a"], True)
        await until(lambda: h.d.hue_status()["state"] == "error", what="error")
        await until(lambda: len(h.client.puts("stop")) == 1, what="the area stopped")
    h.run(go)


@pytest.mark.parametrize("case", ["no_area", "no_channel"])
def test_config_mismatch_is_no_config(tmp_path, case):
    h = H(tmp_path, area="not-on-the-bridge" if case == "no_area" else AREA)
    if case == "no_area":                                     # two areas, none configured or named: room's rule fails
        h.client.extra_areas = [{"id": OTHER_AREA, "status": "inactive", "channels": []}]

    async def go():
        h.d.request(["lamp.d"] if case == "no_channel" else ["lamp.a"], True)
        await until(lambda: h.d.hue_status()["state"] == "no_config", what="no_config")
        want = "no entertainment area" if case == "no_area" else "has a channel"
        assert want in h.d.hue_status()["detail"]
    h.run(go)
    assert h.client.puts() == []


def test_a_configured_area_the_bridge_lacks_falls_back_as_room_does(tmp_path):
    """room's resolve_area, unchanged: patch.yaml's area, else the one named "Screen Machine", else the only one. Both
    streamers pick the same area; the detail says so."""
    h = H(tmp_path, area=OTHER_AREA)

    async def go():
        await h.streaming(("lamp.a",))
        assert h.d.hue_status()["area"] == AREA and OTHER_AREA in h.d.hue_status()["detail"]
    h.run(go)


def test_takeover_is_captured_right_after_our_start(tmp_path):
    """68's contract: active_streamer is noted right after our own action:start (room's output.py capture). Another
    app taking the area during the handshake is taken_over at once, never fought (no stop)."""
    h = H(tmp_path)
    h.streamer.during_connect = lambda: h.client.area.update(active_streamer={"rid": "someone-else"})

    async def go():
        h.d.request(["lamp.a"], True)
        await until(lambda: h.d.hue_status()["state"] == "taken_over", what="taken_over")
        assert h.d._our_streamer == OUR_RID and h.client.puts("stop") == []
        assert not h.streamer.is_connected
        assert contract.hue_answer(h.d.hue_status())["words"] == "Taken over by another app"
    h.run(go)
    assert h.d.hue_status()["frames"] == 0


@pytest.mark.parametrize("bridge_ended_it", [True, False])
def test_a_dropped_dtls_session_restarts(tmp_path, bridge_ended_it):
    h = H(tmp_path)

    async def go():
        await h.streaming()
        if bridge_ended_it:
            h.client.area.update(status="inactive", active_streamer=None)
        h.streamer.is_connected = False                 # the sender thread's send failed
        await until(lambda: len(h.streamer.connects) == 2 and h.d.hue_status()["state"] == "streaming",
                    what="restarted")
        assert len(h.client.puts("stop")) == (0 if bridge_ended_it else 1)
    h.run(go)


# -- frames -------------------------------------------------------------------------------------------------------------------
def test_frames_every_tick_unchanged_and_only_wanted_lamps_with_channels(tmp_path):
    h = H(tmp_path)

    async def go():
        await h.streaming(("lamp.a", "lamp.b", "lamp.d"))      # lamp.d: no channel in the area
        n0 = len(h.streamer.sent)
        await asyncio.sleep(0.3)
        got = h.streamer.sent[n0:]
        assert len(got) >= 8                                    # unchanged colours still go out every frame
        want = cmds_for({"lamp.a": RGB, "lamp.b": RGB})
        assert all(sorted(c) == want for c in got)              # lamp.c isn't wanted; lamp.d has no channel
    h.run(go)


def test_nothing_is_sent_unless_streaming(tmp_path):
    h = H(tmp_path)

    async def go():
        await asyncio.sleep(0.2)                                # idle: nobody asked
        assert h.streamer.sent == []
    h.run(go)


def test_inactive_frames_hold_the_last_active_colours(tmp_path):
    h = H(tmp_path)

    async def go():
        await h.streaming(("lamp.a",))
        h.active = False
        h.rgb = {fid: [0.0, 0.0, 0.0] for fid in LIGHTS}        # the house look (dark) on inactive frames
        n0 = len(h.streamer.sent)
        await asyncio.sleep(0.15)
        got = h.streamer.sent[n0:]
        assert got and all(c == cmds_for({"lamp.a": RGB}) for c in got)
    h.run(go)


def test_gamma_comes_from_patch(tmp_path):
    h = H(tmp_path, gamma=1.0)

    async def go():
        await h.streaming(("lamp.a",))
        await asyncio.sleep(0.1)
        assert h.streamer.sent[-1] == cmds_for({"lamp.a": RGB}, gamma=1.0)
    h.run(go)


@pytest.mark.parametrize("attack_ms,expect", [(None, 80.0), (40.0, 80.0), (200.0, 200.0)])
def test_rises_are_never_faster_than_min_attack(tmp_path, attack_ms, expect):
    h = H(tmp_path, out=out_cfg(True, min_attack_ms=attack_ms))
    assert h.d.caps["min_attack_ms"] == expect and h.d.caps["hits"] is False

    async def go():
        await h.streaming(("lamp.a",))
        h.feeding = False
        t0 = time.monotonic() * 1000.0 + 10_000.0
        h.d.set_frame(h.frame(rgb={"lamp.a": [0.0, 0.0, 0.0]}, t_ms=t0))
        levels = []
        for k in range(1, 30):
            h.d.set_frame(h.frame(rgb={"lamp.a": [1.0, 1.0, 1.0]}, t_ms=t0 + 20.0 * k))
            levels.append(h.streamer.sent[-1][0][3])
        want = [round(min(1.0, contract.SLEW_PEAK * 20.0 * k / expect) ** 0.6 * 0xFFFF) for k in range(1, 30)]
        assert levels == want
        h.d.set_frame(h.frame(rgb={"lamp.a": [0.0, 0.0, 0.0]}, t_ms=t0 + 20.0 * 31))
        assert h.streamer.sent[-1][0][3] == 0                  # a fall is never slowed
    h.run(go)


def test_sent_values_are_xy_bri_through_pulses_and_colour_changes(tmp_path):
    """The xy cache (a pulse only scales the colour) never changes what goes out: exactly xy_bri(rgb, gamma)."""
    import random
    rnd = random.Random(7)
    h = H(tmp_path)

    async def go():
        await h.streaming(("lamp.a", "lamp.b"))
        h.feeding = False
        t_ms = time.monotonic() * 1000.0 + 10_000.0
        base = {"lamp.a": [1.0, 0.3, 0.1], "lamp.b": [0.2, 0.4, 1.0]}
        for k in range(400):
            if k % 50 == 0:                                     # a new colour now and then
                base = {fid: [rnd.random() for _ in range(3)] for fid in base}
            pulse = 0.3 + 0.7 * (0.5 + 0.5 * __import__("math").cos(k / 3.0))
            rgb = {fid: [c * pulse for c in col] for fid, col in base.items()}
            t_ms += 400.0                                       # far apart: the rise limit stays out of it
            h.d.set_frame(h.frame(rgb=rgb, t_ms=t_ms))
            assert sorted(h.streamer.sent[-1]) == cmds_for({fid: ho._rgb(v) for fid, v in rgb.items()}), k
    h.run(go)


def test_plan_is_a_fixed_50hz_grid_while_streaming(tmp_path):
    h = H(tmp_path)
    d = h.d
    assert d.caps["rate_hz"] == 50.0 and d.caps["grid"] == "fixed" and d.caps["keepalive_s"] is None
    d._state = "streaming"                                    # white box: the grid alone
    assert d.plan(0.0, 100.0, True) == 100.0
    d.set_frame(h.frame())
    assert d.plan(100.0, 100.003, True) == pytest.approx(100.02)
    d.set_frame(h.frame())
    assert d.plan(100.02, 100.021, True) == pytest.approx(100.04)
    d.set_frame(h.frame())
    assert d.plan(100.04, 100.3, True) == 100.3              # over a period behind: re-anchored, no burst
    d.set_frame(h.frame())
    d._state = "idle"
    assert d.plan(100.3, 100.31, True) == pytest.approx(100.4)    # not streaming: 0.1 s, activity only


def test_runs_at_50hz_in_real_time(tmp_path):
    h = H(tmp_path)
    stop = threading.Event()

    def runner():
        t_last = 0.0
        while not stop.is_set():
            t = h.d.plan(t_last, time.monotonic(), True)
            dt = t - time.monotonic()
            if dt > 0:
                time.sleep(dt)
            h.d.set_frame(h.frame())
            t_last = time.monotonic()

    async def go():
        h.feeding = False
        th = threading.Thread(target=runner, daemon=True)
        th.start()
        try:
            await h.streaming(("lamp.a",))
            n0 = len(h.streamer.sent)
            await asyncio.sleep(1.5)
            n = len(h.streamer.sent) - n0
            st = h.d.status()
        finally:
            stop.set()
            th.join(1.0)
        print(f"\n  hue frames in 1.5 s: {n}; status rate {st['rate_hz']} Hz; late p50/p95 {st['late_ms']} ms")
        assert 55 <= n <= 80
        assert 35.0 <= st["rate_hz"] <= 55.0
    h.run(go)


# -- release ------------------------------------------------------------------------------------------------------------------
def test_release_with_nothing_streaming_is_at_once(tmp_path):
    h = H(tmp_path)

    async def go():
        r = await h.d.release(None)
        assert r["waited_ms"] < 50.0                            # at once (a measurement: a little above 0)
        assert r == {"released": True, "state": "idle", "why": None, "still_following": [],
                     "waited_ms": r["waited_ms"], "via": "show"}
    h.run(go)
    assert h.client.calls == []


@pytest.mark.parametrize("lights", [None, ["lamp.a", "lamp.b"]])
def test_release_all_stops_waits_for_inactive_and_settles(tmp_path, lights):
    h = H(tmp_path)
    h.client.stop_lag = 3

    async def go():
        await h.streaming(("lamp.a", "lamp.b"))
        n_get = len(h.client.gets("entertainment_configuration"))
        r = await h.d.release(lights)
        assert r["released"] is True and r["why"] is None and r["via"] == "show"
        assert r["waited_ms"] >= 50.0                           # release_settle_s 0.05 after the area went inactive
        assert len(h.client.puts("stop")) == 1 and not h.streamer.is_connected
        assert len(h.client.gets("entertainment_configuration")) - n_get >= 4   # the polls through the lag
        assert h.d.hue_status()["wanted"] == []
        await until(lambda: h.d.hue_status()["state"] == "idle", what="idle")
    h.run(go)


def test_release_one_of_two_is_a_shared_area(tmp_path):
    h = H(tmp_path)

    async def go():
        await h.streaming(("lamp.a", "lamp.b"))
        r = await h.d.release(["lamp.a"])
        assert r["released"] is False and r["why"] == "shared_area" and r["still_following"] == ["lamp.b"]
        await asyncio.sleep(0.1)
        assert h.d.hue_status()["state"] == "streaming" and h.client.puts("stop") == []
    h.run(go)


@pytest.mark.parametrize("order", ["stopped_first", "release_first"])
def test_release_right_after_room_empties_the_set_waits_for_inactive_and_settles(tmp_path, order):
    """room's adapter: POST /hue/stream with the new set, then POST /hue/release. The control task may stop the area
    before the release arrives, or after: either way released only once the area reads inactive + the settle."""
    h = H(tmp_path, rig_hue={"release_settle_s": 0.2})
    h.client.stop_lag = 2

    async def go():
        await h.streaming(("lamp.a", "lamp.b"))
        st = h.d.request([], False)
        assert contract.hue_answer(st)["state"] == "idle"
        if order == "stopped_first":
            await until(lambda: len(h.client.puts("stop")) == 1, what="the area stopped")
        n_get = len(h.client.gets("entertainment_configuration"))
        r = await h.d.release(["lamp.a", "lamp.b"])
        assert r["released"] is True and r["why"] is None and r["state"] == "idle"
        assert r["waited_ms"] >= 190.0                          # the settle after it read inactive
        assert len(h.client.gets("entertainment_configuration")) - n_get >= 3    # read through the lag
        assert len(h.client.puts("stop")) == 1
    h.run(go)


def test_release_after_an_old_stop_is_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(ho, "OLD_STOP_S", 0.1)
    h = H(tmp_path, rig_hue={"release_settle_s": 0.5})

    async def go():
        await h.streaming(("lamp.a",))
        h.d.request([], False)
        await until(lambda: h.d.hue_status()["state"] == "idle" and h.client.puts("stop"), what="stopped")
        await asyncio.sleep(0.6)                                # the music stopped a while ago
        r = await h.d.release(["lamp.a"])
        assert r["released"] is True and r["waited_ms"] < 300.0
    h.run(go)


def test_release_while_another_app_holds_the_area_is_not_released(tmp_path):
    h = H(tmp_path)

    async def go():
        await h.streaming(("lamp.a",))
        h.client.area.update(active_streamer={"rid": "someone-else"})
        await until(lambda: h.d.hue_status()["state"] == "taken_over", what="taken_over")
        r = await h.d.release(["lamp.a"])
        assert r["released"] is False and r["why"] == "taken_over" and r["state"] in contract.HUE_ROOM_STATES
        assert h.client.puts("stop") == []                      # never fight
    h.run(go)


def test_release_with_the_bridge_unreachable(tmp_path):
    h = H(tmp_path)

    async def go():
        await h.streaming(("lamp.a",))
        h.client.fail_put = ho.HueError(0, "ClientConnectorError: no route")
        r = await h.d.release(None)
        assert r["released"] is False and r["why"] == "bridge_unreachable"
        h.client.fail_put = None                                # the stop is retried after the backoff
        await until(lambda: h.d.hue_status()["state"] == "idle", what="idle")
        assert h.client.area["status"] == "inactive"
    h.run(go)


def test_release_timeout(tmp_path):
    h = H(tmp_path, rig_hue={"release_timeout_s": 0.3})
    h.client.stop_noop = True                                   # the area never reads inactive

    async def go():
        await h.streaming(("lamp.a",))
        t0 = time.monotonic()
        r = await h.d.release(None)
        assert r["released"] is False and r["why"] == "timeout"
        assert 0.25 <= time.monotonic() - t0 <= 1.0
    h.run(go)


def test_release_never_waits_past_the_cap(tmp_path):
    """Room's adapter waits 20 s: a release never waits longer than contract.HUE_RELEASE_MAX_S (+2 s in the API)."""
    h = H(tmp_path, rig_hue={"release_timeout_s": 600})
    assert h.d._release_timeout_s() == contract.HUE_RELEASE_MAX_S <= 18.0


def test_release_while_waiting_to_start_never_starts(tmp_path):
    """An area already active at our start was never ours: a release while we wait for it reads it, and answers
    taken_over (room then checks the bridge itself), never released. Nothing starts."""
    h = H(tmp_path)
    h.client.area.update(status="active", active_streamer={"rid": "someone-else"})

    async def go():
        h.d.request(["lamp.a"], True)
        await until(lambda: h.d.hue_status()["state"] == "starting" and h.d.hue_status()["area"] == AREA,
                    what="waiting for the area")            # (resolved: the start wait)
        r = await h.d.release(None)
        assert r["released"] is False and r["why"] == "taken_over" and r["waited_ms"] < 200.0
        h.client.area.update(status="inactive", active_streamer=None)
        await asyncio.sleep(0.3)
        r = await h.d.release(None)                             # the other app let go: writable
        assert r["released"] is True and r["why"] is None
    h.run(go)
    assert h.client.puts() == []


@pytest.mark.parametrize("how", ["taken_while_streaming", "active_at_start"])
def test_release_in_rooms_order_while_another_app_holds_the_area_is_not_released(tmp_path, how):
    """room's adapter: POST /hue/stream with the new set, then POST /hue/release. The new set clears our own
    taken_over state, so the release reads the area itself: active under another app is taken_over, never a false
    released (room would skip its own bridge check and write into a streaming area)."""
    h = H(tmp_path)

    async def go():
        if how == "taken_while_streaming":
            await h.streaming(("lamp.a",))
            h.client.area.update(active_streamer={"rid": "someone-else"})
        else:                                                   # already active: start_wait_s, then taken_over
            h.client.area.update(status="active", active_streamer={"rid": "someone-else"})
            h.d.request(["lamp.a"], True)
        await until(lambda: h.d.hue_status()["state"] == "taken_over", what="taken_over")
        st = h.d.request([], False)                             # room's order: the new set first ...
        assert contract.hue_answer(st)["state"] == "idle"
        n_get = len(h.client.gets("entertainment_configuration"))
        r = await h.d.release(["lamp.a"])                       # ... then the release
        assert r["released"] is False and r["why"] == "taken_over", r
        assert len(h.client.gets("entertainment_configuration")) > n_get        # read, not assumed
        h.client.area.update(status="inactive", active_streamer=None)            # the other app lets go
        r = await h.d.release(["lamp.a"])
        assert r["released"] is True and r["why"] is None
    h.run(go)
    assert h.client.puts("stop") == []                          # never fight


# -- request --------------------------------------------------------------------------------------------------------------------
def test_request_lists_unknown_ids_and_on_false_empties(tmp_path):
    h = H(tmp_path)

    async def go():
        st = h.d.request(["lamp.a", "nope", "screen.x", "lamp.elsewhere", "lamp.a"], True)
        assert st["wanted"] == ["lamp.a"] and st["unknown"] == ["nope", "screen.x", "lamp.elsewhere"]
        assert st["asked"] == ["lamp.a", "nope", "screen.x", "lamp.elsewhere"]          # unfiltered: room's lights
        assert contract.hue_answer(st)["lights"] == st["asked"]
        await until(lambda: h.d.hue_status()["state"] == "streaming", what="streaming")
        st = h.d.request(["lamp.a"], False)
        assert st["wanted"] == []
        await until(lambda: h.d.hue_status()["state"] == "idle", what="idle")
        assert len(h.client.puts("stop")) == 1
        with pytest.raises(ValueError, match="bad_request"):
            h.d.request("lamp.a", True)                        # type: ignore[arg-type]
        with pytest.raises(ValueError, match="bad_request"):
            h.d.request([1, 2], True)                          # type: ignore[list-item]
    h.run(go)


# -- stop -----------------------------------------------------------------------------------------------------------------------
def test_stop_on_show_brain_gives_the_area_back(tmp_path):
    h = H(tmp_path)

    async def go():
        await h.streaming(("lamp.a",))
        t = h.d.stop()
        assert t is not None
        await t
        assert len(h.client.puts("stop")) == 1 and not h.streamer.is_connected
        assert h.d.hue_status()["state"] == "off"
    h.run(go)


def test_stop_from_another_thread_waits_for_the_area(tmp_path):
    h = H(tmp_path)

    async def go():
        await h.streaming(("lamp.a",))
        assert await asyncio.to_thread(h.d.stop, 2.0) is None
        assert len(h.client.puts("stop")) == 1 and not h.streamer.is_connected
    h.run(go)


# -- room's code, imported (68's contract), the pairing, the area -----------------------------------------------------------------
def test_room_service_hue_code_is_imported_not_copied():
    from room_service.hue import colour as room_colour
    assert ho.xy_bri is room_stream.xy_bri and ho.xy_streamer is room_stream.xy_streamer
    assert ho.GAMMA is room_stream.GAMMA and ho.COLOR_SPACE_BYTE is room_stream.COLOR_SPACE_BYTE
    assert ho.BridgeSecrets is room_client.BridgeSecrets and ho.HueClient is room_client.HueClient
    assert ho.HueError is room_client.HueError and ho.resolve_area is room_client.resolve_area
    assert ho.rgb_to_xy is room_colour.rgb_to_xy and issubclass(ho.HueConfigError, room_client.HueError)


def test_importing_hue_out_loads_nothing_else_of_room_service():
    """Only the dependency-free modules 68 named (stdlib, aiohttp, hue-entertainment), in a fresh interpreter."""
    import subprocess
    import sys
    code = ("import sys; import show_service.hue_out; "
            "print(sorted(m for m in sys.modules if m.startswith('room_service')))")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    got = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=60,
                         env={**os.environ, "CUDA_VISIBLE_DEVICES": ""})
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == str(["room_service", "room_service.hue", "room_service.hue.client",
                                      "room_service.hue.colour", "room_service.hue.pair", "room_service.hue.stream"])


def test_the_default_streamer_is_rooms_xy_streamer():
    """No connect: only the class and the HueStream message builder (xy colour space)."""
    pytest.importorskip("hue_entertainment")
    from hue_entertainment import HueDtlsStreamer
    s = REAL_STREAMER_FACTORY()
    assert isinstance(s, HueDtlsStreamer) and type(s).__name__ == "XyStreamer" and not s.is_connected
    s.connect = None                                            # never a handshake here
    s._area_uuid_bytes = AREA.encode("ascii")
    msg = s._build_huestream_message([(0, *ho.xy_bri((1.0, 0.2, 0.1)))])
    assert len(msg) == 16 + 36 + 7 and msg[ho.COLOR_SPACE_BYTE] == 1


@pytest.mark.parametrize("case", ["by_id", "only_file", "two_files_none_named", "unreadable", "host_in_patch"])
def test_the_pairing_is_rooms_by_the_bridge_id_in_patch_yaml(tmp_path, case):
    """room's BridgeSecrets.load(<patch.yaml hue.bridge>, <patch.yaml hue.host>), read-only, from the (tmp) secrets
    dir: patch.yaml's host wins over the file's address, as in room_service/dmx/__init__.py."""
    other = "aaaaaaaaaaaaaaaa"
    extra = {"host": PATCH_HOST} if case == "host_in_patch" else {}
    h = H(tmp_path, secrets=False, bridge=BRIDGE if case != "two_files_none_named" else "ffffffffffffffff", **extra)
    if case == "unreadable":
        write_secrets(h.secrets, host=None)                     # no host: room's loader raises
    else:
        write_secrets(h.secrets, bridge=other, host="198.51.100.8")
        if case != "only_file":
            write_secrets(h.secrets)

    async def go():
        h.d.request(["lamp.a"], True)
        if case in ("two_files_none_named", "unreadable"):
            await until(lambda: h.d.hue_status()["state"] == "no_config", what="no_config")
            want = "isn't paired" if case == "two_files_none_named" else "doesn't read"
            assert want in h.d.hue_status()["detail"] and "test-app-key" not in h.d.hue_status()["detail"]
        else:
            await until(lambda: h.d.hue_status()["state"] == "streaming", what="streaming")
    h.run(go)
    if case == "by_id":
        assert h.secrets_seen.bridge_id == BRIDGE and h.secrets_seen.host == TEST_HOST
    elif case == "host_in_patch":
        assert h.secrets_seen.bridge_id == BRIDGE and h.secrets_seen.host == PATCH_HOST
        assert h.streamer.connects[0][0] == PATCH_HOST
    elif case == "only_file":
        assert h.secrets_seen.bridge_id == other                # room's rule: the only file
    else:
        assert h.made == 0 and h.client.calls == []


def test_a_new_bridge_address_in_patch_yaml_restarts_the_stream_there(tmp_path):
    """room follows patch.yaml's hue.host (at its next start); we follow it at the reload: stop, then stream there."""
    h = H(tmp_path)

    async def go():
        await h.streaming(("lamp.a",))
        h.write_patch("show", host=PATCH_HOST)
        await until(lambda: len(h.streamer.connects) == 2 and h.d.hue_status()["state"] == "streaming",
                    what="restarted")
        assert h.streamer.connects[0][0] == TEST_HOST and h.streamer.connects[1][0] == PATCH_HOST
        assert len(h.client.puts("stop")) == 1
    h.run(go)


def test_resolve_area_is_rooms():
    c = FakeClient([])
    assert asyncio.run(ho.resolve_area(c, AREA)) == (AREA, CHANNEL)
    assert asyncio.run(ho.resolve_area(c, None)) == (AREA, CHANNEL)          # the only area
    assert asyncio.run(ho.resolve_area(c, OTHER_AREA)) == (AREA, CHANNEL)    # room's fallback: the only area
    c.extra_areas = [{"id": OTHER_AREA, "status": "inactive", "channels": []}]
    with pytest.raises(ho.HueError):
        asyncio.run(ho.resolve_area(c, None))                                # two, none chosen, none named
    assert asyncio.run(ho.resolve_area(c, OTHER_AREA)) == (OTHER_AREA, {})
    c.extra_areas[0]["metadata"] = {"name": "Screen Machine"}
    assert asyncio.run(ho.resolve_area(c, None))[0] == OTHER_AREA           # room's name rule


def test_room_states_map_every_driver_state():
    """contract.hue_room_state: exactly idle | armed | streaming | taken_over for the routes (68's contract)."""
    for st in contract.OUTPUT_STATES:
        for wanted in ([], ["lamp.a"]):
            rs = contract.hue_room_state({"state": st, "wanted": wanted})
            assert rs in contract.HUE_ROOM_STATES
            if st == "taken_over":
                assert rs == "taken_over"
            elif not wanted or st in ("off", "waiting_room", "not_built", "no_config"):
                assert rs == "idle"
            elif st == "streaming":
                assert rs == "streaming"
            else:
                assert rs == "armed", st                # waiting_input, starting, idle (a step away), error ...
    ans = contract.hue_answer({"state": "streaming", "wanted": ["lamp.a"], "asked": ["lamp.a", "x"], "detail": None,
                               "unknown": ["x"]}, "b00t")
    assert ans == {"state": "streaming", "lights": ["lamp.a", "x"], "boot_id": "b00t", "words": "Following the music",
                   "detail": None, "wanted": ["lamp.a"], "unknown": ["x"]}         # lights: what room asked, unfiltered
    assert contract.hue_answer(None) == {"state": "idle", "lights": [], "boot_id": None, "words": "Not following",
                                         "detail": None, "wanted": [], "unknown": []}


def test_settings_merge_and_rig_settings(tmp_path):
    h = H(tmp_path, rig_hue={"takeover_check_s": "junk"})
    assert h.d._rs["takeover_check_s"] == ho.RIG_HUE_DEFAULTS["takeover_check_s"]   # a bad number: the default
    assert h.d._rs["start_wait_s"] == FAST["start_wait_s"]
    s = ho.hue_settings({"settings": {"gamma": 0.8}}, {"hue": {"gamma": 0.6, "bridge": BRIDGE, "streamer": "show"}})
    assert s["gamma"] == 0.8 and s["bridge"] == BRIDGE and s["streamer"] == "show"
    s = ho.hue_settings({"settings": {"streamer": "show"}}, {"hue": {}})
    assert s["streamer"] in (None, "room")                       # the switch is only ever patch.yaml's
    assert ho.hue_settings({}, {"hue": {"bridge": BRIDGE, "host": PATCH_HOST}})["host"] == PATCH_HOST   # room's rule
    assert ho.hue_settings({"settings": {"bridge": "another"}}, {"hue": {"bridge": BRIDGE, "host": PATCH_HOST}}
                           )["host"] is None                     # another bridge: its pairing file's address


# -- shapes and costs -------------------------------------------------------------------------------------------------------------
def test_status_shapes(tmp_path):
    h = H(tmp_path)

    async def go():
        await h.streaming(("lamp.a",))
        await asyncio.sleep(0.1)
        st, hs = h.d.status(), h.d.hue_status()
        assert set(st) == set(contract.OutputStatus.__annotations__)
        assert set(hs) == set(contract.HueStatus.__annotations__)
        assert set(contract.hue_answer(hs)) == set(contract.HueAnswer.__annotations__)
        assert hs["room_state"] == "streaming" and hs["words"] == "Following the music"
        assert st["state"] in contract.OUTPUT_STATES and st["extra"]["state"] == "streaming"
        assert st["frames"] > 0 and st["last_frame_age_s"] is not None and st["driver"] == "hue"
        json.dumps(st)
        r = await h.d.release(["lamp.a"])
        assert set(r) == set(contract.HueRelease.__annotations__)
    h.run(go)


def test_set_frame_budget(tmp_path):
    """p95 of set_frame for 4 lamps through the real xy streamer's encoder (never connected: no socket, no thread;
    send_colors only builds the message and queues it)."""
    pytest.importorskip("hue_entertainment")
    h = H(tmp_path)
    s = REAL_STREAMER_FACTORY()                                 # room's xy streamer
    s.connect = None
    s._area_uuid_bytes = AREA.encode("ascii")
    s._connected = True                                         # the queue only: nothing consumes or sends it
    d = h.d
    d._streamer, d._streaming = s, True
    d._channels = {"light-a": 0, "light-b": 1, "light-c": 2, "light-d": 3}
    d._asked = list(LIGHTS)
    d._recompute_wanted()
    assert len(d._send) == 4
    t_ms = time.monotonic() * 1000.0
    out = []
    for what, colour in (("a beat pulse", lambda v: [v, 0.5 * v, 0.25 * v]),             # the colour only scales
                         ("the colour moving every frame", lambda v: [v, 0.5 * v, 0.25])):
        times = []
        for k in range(3000):
            v = 0.2 + 0.8 * (k % 50) / 49.0
            f = h.frame(rgb={fid: colour(v) for fid in LIGHTS}, t_ms=t_ms)
            t_ms += 20.0
            t0 = time.perf_counter()
            d.set_frame(f)
            times.append((time.perf_counter() - t0) * 1e6)
        times.sort()
        p50, p95 = statistics.median(times), times[int(0.95 * len(times))]
        out.append(f"{what}: p50 {p50:.1f} us, p95 {p95:.1f} us")
        assert p95 < 1000.0
    s._connected = False
    print("\n  hue set_frame, 4 lamps (budget p95 100 us): " + "; ".join(out))


# -- README S5.14: the review fixes --------------------------------------------------------------------------------------------
def test_caps_ask_for_no_fast_oscillator_and_no_vibe_idle(tmp_path):
    h = H(tmp_path, out=out_cfg(True, min_attack_ms=200.0))
    assert h.d.caps["min_period_ms"] == 400.0 and h.d.caps["idle_without_vibe"] is False
    assert ho.HueOut.caps["min_period_ms"] == 2 * ho.MIN_ATTACK_MS


@pytest.mark.parametrize("depth", [0.7, 0.9])
def test_sent_brightness_matches_the_virtual_maestros_hue_channels(tmp_path, depth):
    """Parity on what set_frame actually sends (README S5.14): the look engine's frames (show.yaml's pulse layer)
    through the driver, against artnet_out's hue channels for the same clock, features and depth. Disco's full
    accent depth (0.9) peaks at 18.75 x d per second; a driver limit of 1/attack clipped it by up to 0.11."""
    import math
    from show_service import artnet_out as ao
    from show_service.looks import LookEngine
    from show_service.tests.test_looks import HUE_T, SnapClock, feats, out as lout, rigview, show_looks
    lay = ao.load_layout(ao.PATCH_FILE)                           # read only: the hue devices and their channels
    dev = next(d for d in lay.devices if d.kind == "hue")
    idx = {c.field: c.index for c in dev.channels}
    colour = (1.0, 0.6, 0.3)
    clk = SnapClock(anchor=10.0, period=60.0 / 128.0)
    box = {"f": feats(10.0)}
    vm = ao.VirtualMaestro(layout=lay, clock=clk, features_fn=lambda: box["f"], room_dmx_url=None,
                           palette_fn=lambda t: {dev.id: colour})
    e = LookEngine(clock=clk, features_fn=lambda: box["f"], palette_fn=lambda t: {"lamp.a": colour})
    rig = rigview([{"id": "lamp.a", "label": "a", "type": "sm-hue", "kind": "lamp", "output": "hue",
                    "address": None, "light": "light-a", "screen": None, "zone": None, "placement": None,
                    "role": None, "tags": [], "order": None, "wb": {}, "curve": None, "pin": {}, "house": None,
                    "latency": {"rise_ms": 0.0, "fall_ms": 0.0, "colour_ms": 0.0, "position_ms": 0.0,
                                "min_pulse_ms": 40.0, "source": {}}}], [HUE_T], [lout("hue", "hue")])
    assert e.configure(show_looks(), rig) == []
    for x in (vm, e):
        x.set_look(hue_depth=depth, beat_lock=True)
    h = H(tmp_path)
    d = h.d
    fake = FakeStreamer([])
    fake.is_connected = True
    d._streamer, d._streaming = fake, True                       # white box: streaming, no bridge, no thread
    d._channels = dict(CHANNEL)
    d._asked = ["lamp.a"]
    d._recompute_wanted()
    gamma = d._gamma
    worst, clipped_before = 0.0, 0.0
    prev_t, prev_lvl = None, None
    t = 10.0
    while t < 16.0:
        t = vm.plan(t)
        box["f"] = feats(t - 0.012, energy=0.5)
        assert vm.compose(t)
        data = vm.channels()
        want = max(data[idx["r"]], data[idx["g"]], data[idx["b"]]) / 255.0
        fr = e.evaluate("hue", t, d.caps)
        d.set_frame(fr)
        (ch, _x, _y, bri), = fake.sent[-1]
        got = (bri / 0xFFFF) ** (1.0 / gamma)
        worst = max(worst, abs(got - want))
        lvl = fr["fixtures"]["lamp.a"]["intensity"]
        if prev_t is not None:                                    # what the old 1/attack limit would have sent
            clipped_before = max(clipped_before, lvl - min(lvl, prev_lvl + (t - prev_t) / 0.080))
        prev_t, prev_lvl = t, lvl
    assert worst <= 1.0 / 255 + 1e-6, worst
    if depth == 0.9:
        assert clipped_before > 0.02                              # the case the shared constant fixes


def test_what_room_asked_comes_back_after_a_restart(tmp_path):
    """README S5.14: room's adapter POSTs /hue/stream only on a mode apply. A restart (or the output switched off
    and on) takes back the persisted set and streams again with no request, but only while patch.yaml says show."""
    h = H(tmp_path)
    saved = {}

    async def first():
        await h.streaming(("lamp.a", "lamp.b"))
        saved.update(h.d.persist_state())
    h.run(first)
    assert saved["asked"] == ["lamp.a", "lamp.b"]
    h2 = H(tmp_path)                                              # a new HueOut: show_service restarted
    assert h2.d.hue_status()["wanted"] == []

    async def second():
        assert h2.d.restore_state(saved) is True
        await until(lambda: h2.d.hue_status()["state"] == "streaming", what="streaming again")
        assert h2.d.hue_status()["wanted"] == ["lamp.a", "lamp.b"]
    h2.run(second)
    h3 = H(tmp_path, streamer="room")                             # rolled back: room streams, nothing taken back
    assert h3.d.restore_state(saved) is False and h3.d._asked == []
    assert h3.d.restore_state({"asked": "junk"}) is False and h3.d.restore_state(None) is False
    calls = []
    h4 = H(tmp_path)
    h4.ctx.persist = lambda: calls.append(1)
    h4.d.request(["lamp.a"], True)
    assert calls and h4.d.persist_state()["asked"] == ["lamp.a"]
