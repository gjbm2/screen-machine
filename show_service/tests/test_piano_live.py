"""piano_live.py: HEAD says it's here; listeners of one instrument share one synth process; on_midi's notes reach its
stdin with their sound time; it stops LINGER_S after the last listener leaves.  piano_render.live is faked."""
from __future__ import annotations

import asyncio
import json
import pathlib
import stat
import sys
import time
from types import SimpleNamespace

from show_service import piano_live

FAKE = '''#!{py}
import pathlib, sys, threading, time
out = pathlib.Path({log!r})
def read():
    for line in sys.stdin:
        with out.open("a") as f:
            f.write(line)
    sys.exit(0)
threading.Thread(target=read, daemon=True).start()
sys.stderr.write('{{"ready": true, "load_s": 0.1}}\\n')
sys.stderr.write('{{"clock": {{"t0": %r, "delay_s": 0.35, "sr": 48000}}}}\\n' % time.monotonic()); sys.stderr.flush()
while True:
    sys.stdout.buffer.write(b"\\x00" * 4096); sys.stdout.buffer.flush()   # 1024 frames of silence
    time.sleep(1024 / 48000)
'''


def test_live_stream(tmp_path: pathlib.Path, monkeypatch) -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    log = tmp_path / "stdin.log"
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE.format(py=sys.executable, log=str(log)))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(piano_live, "PYTHON", fake)
    monkeypatch.setattr(piano_live, "LINGER_S", 0.5)
    insts = tmp_path / "instruments.json"
    insts.write_text(json.dumps({"default": "concert-d", "instruments": {"concert-d": {}, "fazioli-sf2": {},
                                                                         "windows-gs": {}}}))
    monkeypatch.setattr(piano_live, "INSTRUMENTS", insts)
    live = piano_live.PianoLive()

    async def go():
        app = web.Application()
        live.routes(app)
        async with TestClient(TestServer(app, host="127.0.0.1", port=0)) as cl:
            r = await cl.head("/api/v1/piano/live.mp3")
            assert r.status == 200 and r.headers["Content-Type"] == "audio/mpeg" and not live.synths
            r1 = await cl.get("/api/v1/piano/live.mp3", params={"instrument": "windows-gs"})
            assert r1.status == 200 and r1.headers["X-Piano-Instrument"] == "concert-d"     # can't stream: default
            r2 = await cl.get("/api/v1/piano/live.mp3")
            a, b = await r1.content.read(3000), await r2.content.read(3000)
            assert len(a) > 0 and len(b) > 0 and len(live.synths) == 1                             # one shared synth
            msg = SimpleNamespace(ev=[(1_000, 0x90, 60, 80), (2_000, 0x80, 60, 0), (3_000, 0xE0, 0, 64)])
            live.on_midi("piano", msg, 5.0, lambda t_ns: 100.0 + t_ns * 1e-9)
            for _ in range(50):
                if log.exists() and len(log.read_text().splitlines()) >= 2:
                    break
                await asyncio.sleep(0.05)
            got = [json.loads(x) for x in log.read_text().splitlines()]
            assert [(g["s"], g["a"]) for g in got] == [(0x90, 60), (0x80, 60)] and abs(got[0]["t"] - 100.000001) < 1e-6
            r1.close()
            r2.close()
            t0 = time.monotonic()
            while live.synths and time.monotonic() - t0 < 6:
                await asyncio.sleep(0.1)
            assert not live.synths and live._feeds == ()                                       # stopped after linger

    asyncio.run(go())


def test_only_the_chosen_device_hears(tmp_path: pathlib.Path, monkeypatch) -> None:
    """Owner, 23 Oct: 'I clicked play on PC22, and the sound came out of ipad'. The piano's output is one choice; a
    device's stream gets the notes only while it's that device (a panic when it moves away), an older page's always;
    the chosen device's sound is warmed before it connects; devices are listed (and kept) by name."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    log = tmp_path / "stdin.log"
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE.format(py=sys.executable, log=str(log)))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(piano_live, "PYTHON", fake)
    monkeypatch.setattr(piano_live, "GATE_S", 0.05)
    insts = tmp_path / "instruments.json"
    insts.write_text(json.dumps({"default": "concert-d", "instruments": {"concert-d": {}}}))
    monkeypatch.setattr(piano_live, "INSTRUMENTS", insts)
    out = ["pc22"]
    live = piano_live.PianoLive(output_fn=lambda: out[0], devices_file=tmp_path / "devices.json")
    note = SimpleNamespace(ev=[(1_000, 0x90, 60, 80)])

    def lines() -> list:
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

    async def settle(n: int = 6) -> None:
        for _ in range(n):
            await asyncio.sleep(0.05)

    async def go():
        app = web.Application()
        live.routes(app)
        async with TestClient(TestServer(app, host="127.0.0.1", port=0)) as cl:
            r = await cl.post("/api/v1/piano/devices", json={"id": "ipad-1", "name": "Greg's iPad"})
            assert r.status == 200
            d = await r.json()
            assert d["devices"][0]["id"] == "device:ipad-1" and d["devices"][0]["label"] == "Greg's iPad"
            assert d["hears"] is None and not live.synths
            out[0] = "device:ipad-1"                                   # chosen: warmed before it connects
            await settle()
            assert list(live.synths) == ["concert-d"] and live._feeds
            r1 = await cl.get("/api/v1/piano/live.mp3", params={"device": "ipad-1", "name": "Greg's iPad"})
            await r1.content.read(1000)
            live.on_midi("piano", note, 5.0, lambda t_ns: 100.0)
            await settle()
            assert [g.get("a") for g in lines()] == [60]
            out[0] = "pc22"                                            # PC22 chosen: the iPad hears nothing
            await settle()
            assert live._feeds == () and lines()[-1] == {"panic": True}
            n = len(lines())
            live.on_midi("piano", note, 5.0, lambda t_ns: 100.0)
            await settle()
            assert len(lines()) == n
            r2 = await cl.get("/api/v1/piano/live.mp3")                # an older page: hears as before
            await r2.content.read(1000)
            assert live._feeds
            r2.close()
            r1.close()
            await settle()
            assert live.device_outputs()[0]["label"] == "Greg's iPad"
        again = piano_live.PianoLive(devices_file=tmp_path / "devices.json")
        assert again.device_outputs()[0]["id"] == "device:ipad-1"     # kept across a restart

    asyncio.run(go())


def test_the_websocket_stream_is_stamped_pcm_with_the_piece_map(tmp_path: pathlib.Path, monkeypatch) -> None:
    """live.ws: hello, then s16le chunks each stamped with the sound time of its first frame (t0 + frame/SR - delay)
    and the host clock, and the piece's map (pos_fn) every MAP_S; a ping is answered."""
    import struct
    from aiohttp import WSMsgType, web
    from aiohttp.test_utils import TestClient, TestServer
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE.format(py=sys.executable, log=str(tmp_path / "stdin.log")))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(piano_live, "PYTHON", fake)
    monkeypatch.setattr(piano_live, "MAP_S", 0.1)
    insts = tmp_path / "instruments.json"
    insts.write_text(json.dumps({"default": "concert-d", "instruments": {"concert-d": {}}}))
    monkeypatch.setattr(piano_live, "INSTRUMENTS", insts)
    live = piano_live.PianoLive(devices_file=tmp_path / "devices.json",
                                pos_fn=lambda: {"piece": "lib:x", "pos_s": 12.0, "at": time.monotonic(),
                                                "speed": 1.0, "state": "playing"})

    async def go():
        app = web.Application()
        live.routes(app)
        async with TestClient(TestServer(app, host="127.0.0.1", port=0)) as cl:
            ws = await cl.ws_connect("/api/v1/piano/live.ws", params={"device": "ipad-1", "name": "iPad"})
            hello = await ws.receive_json(timeout=5)
            assert hello["hello"]["sr"] == 48000 and hello["hello"]["format"] == "s16le"
            stamps, maps, pong = [], [], None
            await ws.send_json({"ping": 42})
            t_end = time.monotonic() + 5
            while time.monotonic() < t_end and (len(stamps) < 5 or not maps or pong is None):
                m = await ws.receive(timeout=5)
                if m.type == WSMsgType.BINARY:
                    t, host_now = struct.unpack("<dd", m.data[:16])
                    assert len(m.data) - 16 == piano_live.PCM_BYTES and host_now <= time.monotonic()
                    stamps.append(t)
                elif m.type == WSMsgType.TEXT:
                    d = json.loads(m.data)
                    if "map" in d:
                        maps.append(d["map"])
                    if "pong" in d:
                        pong = d["pong"]
            steps = [round(b - a, 5) for a, b in zip(stamps, stamps[1:])]
            assert set(steps) == {round(piano_live.PCM_FRAMES / 48000, 5)}       # one chunk's length apart
            assert maps[0]["piece"] == "lib:x" and pong == 42
            assert live.devices_view()["devices"][0]["streaming"] is True
            await ws.close()

    asyncio.run(go())


def test_the_websocket_keeps_talking_while_nothing_plays_and_logs_who_listens(tmp_path: pathlib.Path, monkeypatch,
                                                                              caplog) -> None:
    """Owner, 24 Oct ('play on this device' must not get broken): with no piece, the socket still carries the clock
    every MAP_S (the page's stall check closes a silent socket and opens it again), and who joins and leaves is
    logged (the evidence when a device goes quiet)."""
    import logging
    from aiohttp import WSMsgType, web
    from aiohttp.test_utils import TestClient, TestServer
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE.format(py=sys.executable, log=str(tmp_path / "stdin.log")))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(piano_live, "PYTHON", fake)
    monkeypatch.setattr(piano_live, "MAP_S", 0.1)
    insts = tmp_path / "instruments.json"
    insts.write_text(json.dumps({"default": "concert-d", "instruments": {"concert-d": {}}}))
    monkeypatch.setattr(piano_live, "INSTRUMENTS", insts)
    live = piano_live.PianoLive(output_fn=lambda: "device:ipad-1", devices_file=tmp_path / "devices.json",
                                pos_fn=lambda: None)
    caplog.set_level(logging.INFO, logger="show_service.piano_live")

    async def go():
        app = web.Application()
        live.routes(app)
        async with TestClient(TestServer(app, host="127.0.0.1", port=0)) as cl:
            ws = await cl.ws_connect("/api/v1/piano/live.ws", params={"device": "ipad-1", "name": "iPad"})
            await ws.receive_json(timeout=5)                           # hello
            clocks = 0
            t_end = time.monotonic() + 5
            while time.monotonic() < t_end and clocks < 3:
                m = await ws.receive(timeout=5)
                if m.type == WSMsgType.TEXT:
                    d = json.loads(m.data)
                    assert "map" not in d
                    clocks += set(d) == {"host_now"}
            assert clocks >= 3
            await ws.close()
            for _ in range(40):
                if "left concert-d" in caplog.text:
                    break
                await asyncio.sleep(0.05)

    asyncio.run(go())
    assert "ipad-1 listening to concert-d (output device:ipad-1)" in caplog.text
    assert "ipad-1 left concert-d after" in caplog.text


def test_a_device_can_be_forgotten(tmp_path: pathlib.Path) -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    live = piano_live.PianoLive(devices_file=tmp_path / "devices.json")
    live.register("ipad-1", "iPad")

    async def go():
        app = web.Application()
        live.routes(app)
        async with TestClient(TestServer(app, host="127.0.0.1", port=0)) as cl:
            r = await cl.delete("/api/v1/piano/devices/device:ipad-1")
            assert r.status == 200 and (await r.json())["devices"] == []
            assert (await cl.delete("/api/v1/piano/devices/ipad-1")).status == 404
        assert piano_live.PianoLive(devices_file=tmp_path / "devices.json").device_outputs() == []

    asyncio.run(go())


def test_a_warm_sound_nobody_hears_stays_off(tmp_path: pathlib.Path, monkeypatch) -> None:
    """The output set to a device nobody listens on: its warm sound stops after WARM_S and stays off (it was
    restarted every WARM_S, forever); the device naming itself, or the output chosen again, warms it again."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE.format(py=sys.executable, log=str(tmp_path / "stdin.log")))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(piano_live, "PYTHON", fake)
    monkeypatch.setattr(piano_live, "GATE_S", 0.05)
    monkeypatch.setattr(piano_live, "WARM_S", 0.5)
    insts = tmp_path / "instruments.json"
    insts.write_text(json.dumps({"default": "concert-d", "instruments": {"concert-d": {}}}))
    monkeypatch.setattr(piano_live, "INSTRUMENTS", insts)
    out = ["device:ipad-1"]
    live = piano_live.PianoLive(output_fn=lambda: out[0], devices_file=tmp_path / "devices.json")
    live.register("ipad-1", "iPad")

    async def until(cond, limit: float = 6.0) -> bool:
        t0 = time.monotonic()
        while not cond() and time.monotonic() - t0 < limit:
            await asyncio.sleep(0.05)
        return cond()

    async def go():
        app = web.Application()
        live.routes(app)
        async with TestClient(TestServer(app, host="127.0.0.1", port=0)) as cl:
            await cl.get("/api/v1/piano/devices")                      # the page's poll starts the gate
            assert await until(lambda: "concert-d" in live.synths)     # warmed for the chosen device
            assert await until(lambda: not live.synths)                # nobody came: stopped
            await asyncio.sleep(1.0)
            await cl.get("/api/v1/piano/devices")                      # the page still polling: not a listener
            await asyncio.sleep(0.5)
            assert not live.synths                                     # and not started again
            await cl.post("/api/v1/piano/devices", json={"id": "ipad-1", "name": "iPad"})
            assert await until(lambda: "concert-d" in live.synths)     # the device named itself: warm again
            assert await until(lambda: not live.synths)
            out[0] = "pc22"
            await asyncio.sleep(0.3)
            out[0] = "device:ipad-1"                                   # chosen again: warm again
            assert await until(lambda: "concert-d" in live.synths)
            await live._stop(live.synths["concert-d"])

    asyncio.run(go())
