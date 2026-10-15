"""--music-bus: a sound-only show-bus subscription (show_service, :8703).  Its music.* and show.state reach the
sound sink and never a ShowInputs; a refused connection is quiet and retried (no GPU)."""
from __future__ import annotations

import asyncio
import json
import socket

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from stream_service import showbus


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_music_bus_feeds_only_the_sound_sink():
    async def bus(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        hello = json.loads((await ws.receive()).data)
        assert hello["type"] == "hello" and "token" not in hello
        await ws.send_json({"type": "welcome", "schema": 2, "source": "show_service"})
        await ws.send_json({"type": "music.note", "data": {"pitch": 60, "vel": 0.8, "on": True}})
        await ws.send_json({"type": "show.state", "global": {"music": {"spectrum32": [0.1] * 32}}})
        await ws.send_json({"type": "room.direction", "data": {}})     # not for the sound views
        await asyncio.sleep(5)
        return ws

    app = web.Application()
    app.router.add_get("/v1/show", bus)
    async with TestServer(app) as srv:
        got: list[dict] = []
        task = asyncio.create_task(showbus.run(str(srv.make_url("/v1/show")).replace("http", "ws"), None,
                                               sound_sink=got.append, name="music bus"))
        for _ in range(100):
            if len(got) >= 2:
                break
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert [m["type"] for m in got] == ["music.note", "show.state"]


@pytest.mark.asyncio
async def test_refused_music_bus_is_quiet_and_retries():
    got: list[dict] = []
    task = asyncio.create_task(showbus.run(f"ws://127.0.0.1:{free_port()}/v1/show", None,
                                           sound_sink=got.append, name="music bus"))
    await asyncio.sleep(0.8)
    assert not task.done()                     # still retrying, nothing raised
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert got == []
