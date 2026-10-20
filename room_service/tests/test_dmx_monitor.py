"""The outputs monitor's taps: WS /api/v1/dmx/live (the show bus in-app, also with show_bus_port 0), the Hue frame
actually sent in HueOutput.status(), and the raw universe bytes in DmxStack.status()."""

import json

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from room_service.dmx import DmxStack
from room_service.dmx.artnet import ArtNetReceiver
from room_service.dmx.bus import ShowBus
from room_service.dmx.patch import DEFAULT_PATCH, load_patch


async def test_live_ws_monitor_gets_frames_with_no_bus_port():
    patch = load_patch(DEFAULT_PATCH)
    rx = ArtNetReceiver(patch.artnet.universe)
    bus = ShowBus(patch, rx, port=0)
    await bus.start()                                            # port 0: binds nothing
    assert bus.status()["url"] is None and bus._runner is None
    app = web.Application()
    app.router.add_get("/api/v1/dmx/live", bus.handle)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/api/v1/dmx/live")
        await ws.send_str(json.dumps({"type": "hello", "v": 1, "role": "monitor", "client_id": "t"}))
        welcome = json.loads((await ws.receive()).data)
        assert welcome["type"] == "welcome"
        rx.inject({1: 255, 33: 255, 34: 255})                    # screen.north red; hue.ne dimmer + red
        frame = json.loads((await ws.receive(timeout=2)).data)
        while frame["fixtures"].get("hue.ne", {}).get("rgb", [0])[0] == 0:
            frame = json.loads((await ws.receive(timeout=2)).data)
        assert frame["type"] == "show.state" and frame["fixtures"]["hue.ne"]["rgb"][0] == 1.0
        assert [c.role for c in bus.clients] == ["monitor"]
        await ws.close()
    await bus.stop()


async def test_hue_status_reports_the_frame_actually_sent():
    from room_service.hue.output import HueOutput
    patch = load_patch(DEFAULT_PATCH)
    out = HueOutput(None, patch, ArtNetReceiver(0))
    ne = patch.devices["hue.ne"].extra["light"]
    out.channels = {ne: 0}
    out._last_sent, out._last_send_t = ((0, 0xFFFF // 2, 0x4000, 0xFFFF),), __import__("time").monotonic()
    st = out.status()
    assert st["sent"] == {"hue.ne": {"x": 0.5, "y": 0.25, "bri": 1.0}}
    assert st["sent_age_ms"] is not None and st["gamma"] == float(patch.hue.get("gamma", 0.6))


async def test_stack_status_has_raw_channel_bytes():
    stack = DmxStack(DEFAULT_PATCH, show_bus_port=0)
    assert stack.status()["channels"] == []                      # nothing received yet
    stack.rx.inject({33: 255, 48: 7})
    ch = stack.status()["channels"]
    end = max(d.end for d in stack.patch.devices.values())
    assert len(ch) == end and ch[32] == 255 and ch[47] == 7
