"""The show bus (show_service/bus.py) is shared plumbing: real tests against a real Bus on its own thread, bound to
an ephemeral 127.0.0.1 port (never 8703), with aiohttp clients."""

from __future__ import annotations

import asyncio
import json
import math
import socket
import threading
import time

import aiohttp
import numpy as np
import pytest

from show_service import bus as busmod
from show_service import contract as c
from show_service.bus import Bus

WS = aiohttp.WSMsgType
HEALTH = {"t_ms": 1.0, "active_audio": "room-mic",
          "streams": {"room-mic": {"kind": "audio", "state": "streaming", "test": False, "rank": 1,
                                   "line": "room-mic · 48k/1ch · −24 dBFS", "net": {"jitter_ms": float("nan")}},
                      "room-piano": {"kind": "midi", "state": "quiet", "test": False}}}


@pytest.fixture
def bus():
    state = {"body": None}
    b = Bus("127.0.0.1", 0, state_fn=lambda: state["body"], health_fn=lambda: HEALTH)
    b.test_state = state
    b.start()
    assert b.bind_error is None and b.port != 0 and b.port != c.BUS_PORT
    yield b
    b.stop()


def run(coro):
    return asyncio.run(coro)


async def recv(ws, timeout=5.0) -> dict:
    msg = await ws.receive(timeout=timeout)
    assert msg.type == WS.TEXT, (msg.type, msg.data)
    return json.loads(msg.data)


async def hello(session, b, **kw) -> aiohttp.ClientWebSocketResponse:
    ws = await session.ws_connect(b.url, max_msg_size=0)
    await ws.send_json({"type": "hello", "v": 1, "role": "ui", "client_id": "t", **kw})
    return ws


async def collect(ws, stop_type: str, skip=(busmod.SHOW_STATE,), timeout=5.0) -> list[dict]:
    """Messages (minus ``skip``) up to and including the first of ``stop_type``."""
    out, deadline = [], time.monotonic() + timeout
    while True:
        m = await recv(ws, max(0.05, deadline - time.monotonic()))
        if m["type"] in skip:
            continue
        out.append(m)
        if m["type"] == stop_type:
            return out


def env(mtype: str, **data) -> dict:
    return {"type": mtype, "data": data}


# -- handshake -------------------------------------------------------------------------------------------------------
def test_renderer_hello_gets_welcome_first(bus):
    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await s.ws_connect(bus.url)
            # exactly what stream_service/showbus.py sends: v 1, no token, no want
            await ws.send_json({"type": "hello", "v": 1, "role": "renderer", "client_id": "stream_service-1"})
            w = await recv(ws)
            assert w["type"] == "welcome" and w["schema"] == 2 and w["source"] == "show_service"
            assert w["fixtures"] == []                       # none until P4: a v1 client doesn't look for them
            assert set(busmod.TYPES) <= set(w["types"]) and c.MUSIC_NOTE in w["types"]
            assert w["rate_hz"] == 25 and w["stale_after_ms"] > 0 and isinstance(w["t_ms"], float)
            assert "version" in w
            # then show.state frames at 25 Hz (renderers get everything but music.hit)
            m = await recv(ws)
            assert m["type"] == "show.state"
            await ws.close()
    run(go())


def test_token_is_ignored(bus):
    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await hello(s, bus, token="whatever")
            assert (await recv(ws))["type"] == "welcome"
            await ws.close()
    run(go())


def test_not_a_hello_closes_4400(bus):
    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await s.ws_connect(bus.url)
            await ws.send_json({"type": "subscribe"})
            msg = await ws.receive(timeout=5)
            assert msg.type == WS.CLOSE and ws.close_code == 4400
            ws2 = await s.ws_connect(bus.url)
            await ws2.send_str("not json")
            msg = await ws2.receive(timeout=5)
            assert msg.type == WS.CLOSE and ws2.close_code == 4400
    run(go())


def test_latest_on_connect(bus):
    bus.publish(env(c.MUSIC_NOTE, seq=1, pitch=60))
    bus.publish(env(c.MUSIC_NOTES, seq=1, keys=[[60, 90, 1.0]]))
    bus.publish(env(c.MUSIC_HARMONY, chroma12=[0.0] * 12, key="C"))
    bus.publish(env(c.MUSIC_FEATURES, t_ms=1.0, spectrum32=[0.0] * 32, n=1))
    bus.publish(env(c.MUSIC_FEATURES, t_ms=2.0, spectrum32=[0.0] * 32, n=2))
    time.sleep(0.1)

    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await hello(s, bus, want=[c.MUSIC_NOTES, c.MUSIC_FEATURES, c.MUSIC_NOTE])
            assert (await recv(ws))["type"] == "welcome"
            a, b = await recv(ws), await recv(ws)
            assert (a["type"], b["type"]) == (c.MUSIC_NOTES, c.MUSIC_FEATURES)   # harmony not wanted; no music.note
            assert b["data"]["n"] == 2                                            # the latest
            await ws.close()
    run(go())


# -- filter ----------------------------------------------------------------------------------------------------------
def test_want_filter_and_hits_only_to_wanters(bus):
    async def go():
        async with aiohttp.ClientSession() as s:
            plain = await hello(s, bus)
            hits = await hello(s, bus, want=[c.MUSIC_HIT, c.MUSIC_NOTE, c.MUSIC_PEDAL])
            prefix = await hello(s, bus, want=["music"])
            star = await hello(s, bus, want=["music.*"])
            for ws in (plain, hits, prefix, star):
                assert (await recv(ws))["type"] == "welcome"
            await asyncio.sleep(0.1)
            bus.publish(env(c.MUSIC_HIT, band="sub", strength=0.9, t_ms=1.0, stream="room-mic"))
            bus.publish(env(c.MUSIC_NOTE, seq=1, pitch=60))
            bus.publish(env(c.MUSIC_FEATURES, t_ms=1.0, spectrum32=[0.0] * 32))
            bus.publish(env(c.MUSIC_PEDAL, seq=2, pedal="damper", value=0.0))       # end marker
            got = {}
            for name, ws in (("plain", plain), ("hits", hits), ("prefix", prefix), ("star", star)):
                got[name] = [m["type"] for m in await collect(ws, c.MUSIC_PEDAL)]
            assert got["plain"] == [c.MUSIC_NOTE, c.MUSIC_FEATURES, c.MUSIC_PEDAL]
            assert got["hits"] == [c.MUSIC_HIT, c.MUSIC_NOTE, c.MUSIC_PEDAL]
            assert got["prefix"] == got["star"] == [c.MUSIC_NOTE, c.MUSIC_FEATURES, c.MUSIC_PEDAL]
            # a client that wants only music.* gets no show.state
            prefix_types = set()
            bus.publish(env(c.MUSIC_PEDAL, seq=3, pedal="damper", value=0.5))
            for m in await collect(prefix, c.MUSIC_PEDAL, skip=()):
                prefix_types.add(m["type"])
            assert busmod.SHOW_STATE not in prefix_types
            for ws in (plain, hits, prefix, star):
                await ws.close()
    run(go())


def test_want_matching_rules():
    cl = busmod._Client(None, "lights", "x", busmod._parse_want(["music.note", "show"]))
    assert cl.wants("music.note") and cl.wants("show.state")
    assert not cl.wants("music.notes") and not cl.wants("music.hit")
    star = busmod._Client(None, "ui", "x", busmod._parse_want(["*"]))
    assert star.wants("music.features") and not star.wants("music.hit")
    default = busmod._Client(None, "ui", "x", busmod._parse_want([]))      # empty want = the default
    assert default.wants("show.state") and not default.wants("music.hit")
    assert busmod._parse_want("music.hit") == ("music.hit",)
    assert busmod._parse_want({"x": 1}) is None


# -- publish from another thread --------------------------------------------------------------------------------------
def test_stage4_types_and_music_rhythm_only_to_wanters():
    """README S4.5: music.context goes to every client, music.rhythm (10 Hz) only to one that names it."""
    assert c.MUSIC_CONTEXT in busmod.TYPES and c.MUSIC_RHYTHM in busmod.TYPES
    assert set(busmod.WANT_ONLY) == {c.MUSIC_HIT, c.MUSIC_RHYTHM, c.ROOM_WHY}
    renderer = busmod._Client(None, "renderer", "x", busmod._parse_want(None))
    assert renderer.wants(c.MUSIC_CONTEXT) and not renderer.wants(c.MUSIC_RHYTHM)
    for w in (["music"], ["music.*"], ["*"]):
        cl = busmod._Client(None, "ui", "x", busmod._parse_want(w))
        assert cl.wants(c.MUSIC_CONTEXT) and not cl.wants(c.MUSIC_RHYTHM), w
    asked = busmod._Client(None, "console", "x", busmod._parse_want([c.MUSIC_RHYTHM]))
    assert asked.wants(c.MUSIC_RHYTHM) and not asked.wants(c.MUSIC_CONTEXT)


def test_publish_from_another_thread_in_order(bus):
    n = 200

    def producer():
        for i in range(n):
            bus.publish(env(c.MUSIC_NOTE, seq=i, pitch=21 + i % 88))
        bus.publish(env(c.MUSIC_PEDAL, seq=n, pedal="soft", value=1.0))

    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await hello(s, bus, role="lights", want=[c.MUSIC_NOTE, c.MUSIC_PEDAL])
            assert (await recv(ws))["type"] == "welcome"
            await asyncio.sleep(0.05)
            t = threading.Thread(target=producer, name="fake-show-rx")
            t.start()
            got = await collect(ws, c.MUSIC_PEDAL)
            t.join()
            assert [m["data"]["seq"] for m in got[:-1]] == list(range(n))
            await ws.close()
    run(go())
    st = bus.stats()
    assert st["published"][c.MUSIC_NOTE] == n and st["bad"] == 0


def test_publish_never_raises_and_cleans_json(bus):
    bus.publish("not a dict")                     # counted bad, nothing raised
    bus.publish({"data": {}})                     # no type
    bus.publish(env(c.MUSIC_FEATURES, t_ms=float("nan"), spectrum32=np.zeros(32, np.float32), lufs_m=np.float32(-20),
                    peak=float("inf")))

    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await hello(s, bus, want=[c.MUSIC_FEATURES])
            assert (await recv(ws))["type"] == "welcome"
            m = await recv(ws)                     # latest-on-connect
            assert m["data"]["t_ms"] is None and m["data"]["peak"] is None
            assert m["data"]["spectrum32"] == [0.0] * 32 and m["data"]["lufs_m"] == -20.0
            await ws.close()
    run(go())
    assert bus.stats()["bad"] == 2


def test_publish_before_start_and_after_stop():
    b = Bus("127.0.0.1", 0)
    b.publish(env(c.MUSIC_NOTES, seq=1))         # before start: kept for the first drain
    b.start()
    try:
        for _ in range(50):
            if b.latest(c.MUSIC_NOTES):
                break
            time.sleep(0.02)
        assert b.latest(c.MUSIC_NOTES)["data"]["seq"] == 1
    finally:
        b.stop()
    b.publish(env(c.MUSIC_NOTES, seq=2))         # after stop: quietly nothing
    b.stop()                                     # twice is fine


# -- queues ----------------------------------------------------------------------------------------------------------
def _flood(b: Bus, n: int = 1500, size: int = 16000) -> None:
    pad = "x" * size
    for i in range(n):
        b.publish(env(c.MUSIC_FEATURES, i=i, pad=pad))


def test_renderer_that_falls_behind_is_closed_4008(bus):
    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await hello(s, bus, role="renderer", client_id="slow")
            assert (await recv(ws))["type"] == "welcome"
            await asyncio.to_thread(_flood, bus)  # it doesn't read meanwhile: 24 MB > the socket buffers
            for _ in range(200):
                if bus.kicked:
                    break
                await asyncio.sleep(0.05)
            assert bus.kicked == 1
            code = None
            while True:                           # what was in flight, then the close
                msg = await ws.receive(timeout=15)
                if msg.type == WS.CLOSE:
                    code = msg.data
                    break
                if msg.type in (WS.CLOSED, WS.ERROR):
                    break
            assert code == c.CLOSE_BEHIND or ws.close_code == c.CLOSE_BEHIND
    run(go())
    for _ in range(100):
        if not bus.stats()["clients"]:
            break
        time.sleep(0.02)
    assert bus.stats()["clients"] == []


def test_a_burst_after_our_own_stall_does_not_close_a_renderer(bus):
    """show-bus starved for 0.3 s (GIL, say) while show-rx keeps publishing: 500 messages land in one drain."""
    n = 500

    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await hello(s, bus, role="renderer", client_id="keeping-up")
            assert (await recv(ws))["type"] == "welcome"
            bus._loop.call_soon_threadsafe(time.sleep, 0.3)            # stall the bus loop
            await asyncio.sleep(0.05)
            for i in range(n):
                bus.publish(env(c.MUSIC_NOTE, seq=i, pitch=60))
            bus.publish(env(c.MUSIC_PEDAL, seq=n, pedal="damper", value=1.0))
            got = await collect(ws, c.MUSIC_PEDAL, timeout=10)
            assert [m["data"]["seq"] for m in got[:-1]] == list(range(n))
            await ws.close()
    run(go())
    assert bus.kicked == 0


def test_other_roles_drop_the_oldest_and_stay(bus):
    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await hello(s, bus, role="lights", client_id="slow-lights", want=[c.MUSIC_FEATURES, c.MUSIC_PEDAL])
            assert (await recv(ws))["type"] == "welcome"
            await asyncio.to_thread(_flood, bus)
            for _ in range(200):
                st = bus.stats()["clients"]
                if st and st[0]["dropped"]:
                    break
                await asyncio.sleep(0.05)
            st = bus.stats()
            assert st["kicked"] == 0 and st["clients"][0]["dropped"] > 0
            assert st["clients"][0]["queued"] <= c.OTHER_QUEUE
            bus.publish(env(c.MUSIC_PEDAL, seq=1, pedal="damper", value=1.0))
            got = await collect(ws, c.MUSIC_PEDAL, timeout=30)    # still connected; the marker arrives last
            seen = [m["data"]["i"] for m in got if m["type"] == c.MUSIC_FEATURES]
            assert seen == sorted(seen) and seen[-1] == 1499
            await ws.close()
    run(go())


# -- show.state ------------------------------------------------------------------------------------------------------
def test_show_state_is_k3_with_global_on_top(bus):
    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await hello(s, bus, want=["show.state"])
            assert (await recv(ws))["type"] == "welcome"
            silent = await recv(ws)
            assert silent["type"] == "show.state" and "data" not in silent and "fixtures" not in silent
            assert silent["feed"] == "live" and silent["input"] == {"kind": "show", "online": True}
            assert silent["global"]["music"]["spectrum32"] == [0.0] * 32
            assert silent["global"]["music"]["chroma12"] == [0.0] * 12 and silent["global"]["activity"] == 0.0
            assert silent["audio"]["active"] == "room-mic"
            assert silent["audio"]["streams"]["room-mic"]["state"] == "streaming"
            spec = [k / 32 for k in range(32)]
            bus.publish(env(c.MUSIC_FEATURES, t_ms=5.0, stream="room-mic", energy=0.5, spectrum32=spec,
                            bands={"sub": {"level": 0.3, "hits": 1, "presence": 0.2}}))
            bus.publish(env(c.MUSIC_HARMONY, chroma12=[1.0] + [0.0] * 11, key="C", mode="major"))
            for _ in range(50):
                m = await recv(ws)
                if m["global"]["music"]["energy"] == 0.5 and m["global"]["music"]["key"] == "C":
                    break
            g = m["global"]
            assert g["music"]["spectrum32"] == spec and g["music"]["chroma12"][0] == 1.0
            assert g["activity"] == 0.5 and g["music"]["bands"] == {"sub": 0.3} and g["music"]["stream"] == "room-mic"
            assert m["seq"] > silent["seq"] and m["t_ms"] > silent["t_ms"]
            # 25 Hz
            t0 = time.monotonic()
            for _ in range(10):
                await recv(ws)
            assert 0.25 < time.monotonic() - t0 < 1.0
            await ws.close()
    run(go())


def test_show_state_goes_silent_when_features_stop(bus, monkeypatch):
    monkeypatch.setattr(busmod, "MUSIC_STALE_S", 0.1)
    bus.publish(env(c.MUSIC_FEATURES, t_ms=5.0, energy=0.7, spectrum32=[0.5] * 32))
    time.sleep(0.05)
    assert bus.state_frame()["global"]["music"]["energy"] == 0.7
    time.sleep(0.15)
    music = bus.state_frame()["global"]["music"]
    assert music["energy"] == 0.0 and music["spectrum32"] == [0.0] * 32 and music["lufs_m"] == -70.0


# -- HTTP --------------------------------------------------------------------------------------------------------------
def test_healthz_state_and_stubs(bus):
    async def go():
        base = f"http://127.0.0.1:{bus.port}"
        async with aiohttp.ClientSession() as s:
            async with s.get(base + "/healthz") as r:
                assert r.status == 200
                h = await r.json()
            assert h["ok"] is True and h["service"] == "show_service" and h["uptime_s"] >= 0
            assert h["streams"] == {"room-mic": "streaming", "room-piano": "quiet"} and h["clients"] == 0
            async with s.get(base + "/api/v1/state") as r:
                assert r.status == 503 and (await r.json()) == {"error": "starting"}
            bus.test_state["body"] = {"streams": HEALTH["streams"], "x": float("nan")}
            async with s.get(base + "/api/v1/state") as r:
                assert r.status == 200
                body = json.loads(await r.text())        # strict JSON: NaN became null
            assert body["x"] is None and body["streams"]["room-mic"]["net"]["jitter_ms"] is None
            async with s.post(base + "/api/v1/step", json={"dir": "calmer"}) as r:
                assert r.status == 501 and "not built yet" in await r.text()
            async with s.delete(base + "/api/v1/nudge/all") as r:
                assert r.status == 501
    run(go())


def test_bind_error_then_retry():
    holder = socket.socket()
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    port = holder.getsockname()[1]
    b = Bus("127.0.0.1", port)
    try:
        b.start()
        assert b.bind_error                     # start() returned; it keeps retrying
        b.publish(env(c.MUSIC_NOTE, seq=1))     # still fine
        holder.close()
        for _ in range(100):
            if b.bind_error is None:
                break
            time.sleep(0.05)
        assert b.bind_error is None and b.port == port
    finally:
        holder.close()
        b.stop()


def test_dumps():
    assert busmod.dumps({"a": float("nan"), "b": [1.5, float("-inf")], "c": np.arange(3)}) == \
        '{"a":null,"b":[1.5,null],"c":[0,1,2]}'
    assert busmod.dumps({"t": (1, 2)}) == '{"t":[1,2]}'
    assert json.loads(busmod.dumps({"o": object()}))["o"].startswith("<object")
    assert math.isclose(json.loads(busmod.dumps({"x": np.float32(0.25)}))["x"], 0.25)
