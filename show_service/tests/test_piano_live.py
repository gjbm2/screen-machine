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
