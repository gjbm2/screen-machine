"""levels.py: the opt-in ``?wave=N`` waveform on the /v1/levels meter (stream_service's scope feed, 18 Oct)."""
from __future__ import annotations

import asyncio
import base64
import json

import numpy as np
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from show_service import levels as lv
from show_service.levels import LevelFeed


def _sine(n: int, amp: float = 0.5, hz: float = 440.0, sr: int = 48000) -> np.ndarray:
    return (amp * np.sin(2 * np.pi * hz * np.arange(n) / sr)).astype(np.float32)


def _w(frame: dict) -> list[int]:
    """A frame's waveform: "wb" (base64 little-endian int16) as ints."""
    return np.frombuffer(base64.b64decode(frame["wb"]), "<i2").astype(int).tolist()


def _feed_with(qs: dict) -> tuple[LevelFeed, asyncio.AbstractEventLoop]:
    """A LevelFeed with fake clients: {queue: wave N or None}."""
    f = LevelFeed()
    loop = asyncio.new_event_loop()
    f._loop = loop
    for q, n in qs.items():
        f._clients.add(q)
        if n is not None:
            f._wave[q] = n
    return f, loop


def test_wave_param_clamps():
    assert LevelFeed.wave_param(None) is None
    assert LevelFeed.wave_param("abc") is None
    assert LevelFeed.wave_param("128") == 128
    assert LevelFeed.wave_param("1") == lv.WAVE_MIN
    assert LevelFeed.wave_param("-5") == lv.WAVE_MIN
    assert LevelFeed.wave_param("999999") == lv.WAVE_MAX


def test_flush_wave_only_for_who_asks_and_plain_frames_unchanged():
    plain, w128, w128b, w64 = (asyncio.Queue(maxsize=8) for _ in range(4))
    f, loop = _feed_with({plain: None, w128: 128, w128b: 128, w64: 64})
    try:
        x = _sine(4096, amp=2.0)                       # clipped: over full scale
        f.push("room-mic", 1, 0.9, None, 12.5, pcm=x, sr=48000)
        f._flush()
        a = json.loads(plain.get_nowait())
        b = json.loads(w128.get_nowait())
        c = json.loads(w128b.get_nowait())
        d = json.loads(w64.get_nowait())
        assert "wb" not in a and "f" in a
        wb = _w(b)
        assert len(wb) == 128 and all(-32767 <= v <= 32767 for v in wb)
        assert b == c
        assert len(_w(d)) == 64 and _w(d) == wb[-64:]   # the newest samples, oldest first
        assert max(wb) == 32767 and min(wb) == -32767  # clipped
        want = np.clip(np.rint(x[-128:] * 32767.0), -32767, 32767).astype(int).tolist()
        assert wb == want
        # without the wave clients, the plain frame is byte-identical to the wave-free path
        b.pop("wb")
        assert b == a
    finally:
        loop.close()


def test_plain_frame_bytes_match_without_any_wave_client():
    q1 = asyncio.Queue(maxsize=8)
    q2, q3 = asyncio.Queue(maxsize=8), asyncio.Queue(maxsize=8)
    x = _sine(4096)
    f1, l1 = _feed_with({q1: None})
    f2, l2 = _feed_with({q2: None, q3: 256})
    try:
        for f in (f1, f2):
            f.push("room-mic", 7, 0.5, None, 3.25, pcm=x, sr=48000)
            f._flush()
        assert q1.get_nowait() == q2.get_nowait()
    finally:
        l1.close()
        l2.close()


def test_wave_pads_zeros_before_the_ring_fills():
    q = asyncio.Queue(maxsize=8)
    f, loop = _feed_with({q: 128})
    try:
        f.push("room-mic", 1, 0.5, None, 1.0, pcm=np.full(32, 0.5, np.float32), sr=48000)
        f._flush()
        w = _w(json.loads(q.get_nowait()))
        assert len(w) == 128 and w[:96] == [0] * 96 and w[96:] == [16384] * 32
    finally:
        loop.close()


def test_ws_query_wave_end_to_end():
    async def go():
        f = LevelFeed()
        app = web.Application()
        f.routes(app)
        async with TestClient(TestServer(app)) as cl:
            plain = await cl.ws_connect(lv.PATH)
            waved = await cl.ws_connect(lv.PATH + "?wave=128")
            big = await cl.ws_connect(lv.PATH + "?wave=100000")
            for _ in range(50):
                if len(f._clients) == 3:
                    break
                await asyncio.sleep(0.01)
            assert len(f._clients) == 3 and sorted(f._wave.values()) == [128, lv.WAVE_MAX]
            f.push("room-mic", 1, 0.5, None, 1.0, pcm=_sine(4096), sr=48000)
            a = json.loads((await plain.receive(timeout=2)).data)
            b = json.loads((await waved.receive(timeout=2)).data)
            c = json.loads((await big.receive(timeout=2)).data)
            assert "wb" not in a and len(_w(b)) == 128 and len(_w(c)) == lv.WAVE_MAX
            await waved.close()
            for _ in range(50):
                if len(f._wave) == 1:
                    break
                await asyncio.sleep(0.01)
            assert len(f._wave) == 1
            await plain.close()
            await big.close()

    asyncio.run(go())


def test_a_named_stream_has_its_own_frames_and_the_default_keeps_the_active():
    """?stream=<name> (1c's calibration, 23 Oct): a client naming a stream gets only that stream's frames (peak,
    spectrum, wave); the default clients keep the active stream's; a not-active push never reaches them."""
    plain, named = asyncio.Queue(maxsize=8), asyncio.Queue(maxsize=8)
    f, loop = _feed_with({plain: None, named: 128})
    told: list = []
    f.on_watch = told.append
    try:
        f._key[named] = "room-mic"
        f._rewatch()
        assert told == [frozenset({"room-mic"})] and f.snapshot()["watched"] == ["room-mic"]
        f.push("pc22-mic", 5, 0.5, None, 2.0, pcm=_sine(4096, amp=0.5), sr=48000)               # the active one
        f.push("room-mic", 9, 0.1, None, 2.001, pcm=_sine(4096, amp=0.1), sr=48000, active=False)
        f._flush()
        a, b = json.loads(plain.get_nowait()), json.loads(named.get_nowait())
        assert a["s"] == "pc22-mic" and a["c"] == 5 and "wb" not in a
        assert b["s"] == "room-mic" and b["c"] == 9 and len(_w(b)) == 128 and max(_w(b)) <= 0.11 * 32767
        assert plain.empty() and named.empty()
        f.push("room-mic", 10, 0.1, None, 2.01, pcm=_sine(128, amp=0.1), sr=48000, active=False)
        f._flush()
        assert plain.empty() and json.loads(named.get_nowait())["c"] == 10
        del f._key[named]
        f._rewatch()
        assert told[-1] == frozenset() and set(f._taps) == {None}
    finally:
        loop.close()


def test_stream_param():
    assert LevelFeed.stream_param(None) is None and LevelFeed.stream_param("") is None
    assert LevelFeed.stream_param(" room-mic ") == "room-mic" and LevelFeed.stream_param("x" * 17) is None
