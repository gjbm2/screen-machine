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
sys.stderr.write('{{"ready": true, "load_s": 0.1}}\\n'); sys.stderr.flush()
while True:
    sys.stdout.buffer.write(b"\\xff\\xfb" + b"x" * 1000); sys.stdout.buffer.flush()
    time.sleep(0.02)
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
            assert a[:2] == b"\xff\xfb" and len(b) > 0 and len(live.synths) == 1               # one shared synth
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
