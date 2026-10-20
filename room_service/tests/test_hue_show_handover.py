"""Hue hand-over to show_service (SHOW_ENGINE §9.4): the `hue.streamer` switch, the show alias, the release handshake
before a stored state is written, show_service unreachable -> unconfirmed, and room mode unchanged. No bridge: a fake
CLIP client and a fake show_service on a local port."""

import asyncio
import copy

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from room_service.adapters.hue import HueAdapter
from room_service.dmx.artnet import ArtNetReceiver
from room_service.dmx.patch import DEFAULT_PATCH, load_patch


class FakeClient:
    """The CLIP v2 calls the adapter makes: GET light, PUT light/device."""

    def __init__(self, patch):
        self.lights = {d.extra["light"]: {"id": d.extra["light"], "on": {"on": True}, "dimming": {"brightness": 22.0},
                                          "color": {"xy": {"x": 0.482, "y": 0.409}}, "mode": "normal",
                                          "owner": {"rid": f"dev-{d.id}"}}
                       for d in patch.of_kind("hue")}
        self.puts: list[tuple[str, str, dict]] = []

    async def get(self, rtype, rid=None):
        assert rtype == "light"
        return [copy.deepcopy(li) for li in self.lights.values()]

    async def put(self, rtype, rid, body):
        self.puts.append((rtype, rid, body))
        if rtype == "light":
            li = self.lights[rid]
            if "on" in body:
                li["on"] = dict(body["on"])
            if "dimming" in body:
                li["dimming"] = {"brightness": body["dimming"]["brightness"]}
            if "color" in body:
                li["color"] = {"xy": dict(body["color"]["xy"])}

    async def close(self):
        pass


class FakeOutput:
    def __init__(self, client):
        self.client, self.wanted, self.state = client, set(), "idle"

    def set_wanted(self, devices):
        self.wanted = set(devices)
        self.state = "waiting_input" if devices else "idle"

    def is_streaming(self, device):
        return False


@pytest.fixture
async def show_service():
    """A fake show_service: records calls; keeps its set and boot_id like the real one."""
    calls = []
    st = {"lights": [], "boot": "boot-1"}

    async def stream(request):
        body = await request.json()
        calls.append(("stream", body))
        st["lights"] = list(body["lights"])
        return web.json_response({"state": "armed" if body["on"] else "idle", "boot_id": st["boot"]})

    async def release(request):
        body = await request.json()
        calls.append(("release", body))
        return web.json_response({"released": True, "state": "idle"})

    async def status(request):
        calls.append(("status", None))
        state = "idle" if st.get("idle_anyway") or not st["lights"] else "armed"
        return web.json_response({"state": state, "lights": st["lights"], "boot_id": st["boot"]})

    app = web.Application()
    app.router.add_post("/api/v1/hue/stream", stream)
    app.router.add_post("/api/v1/hue/release", release)
    app.router.add_get("/api/v1/hue/status", status)
    server = TestServer(app)
    await server.start_server()
    yield f"http://127.0.0.1:{server.port}", calls, st
    await server.close()


def make(streamer="room", show_url=None):
    patch = load_patch(DEFAULT_PATCH)
    patch.hue = {**patch.hue, "streamer": streamer, "writes_enabled": True}
    if show_url:
        patch.hue["show_url"] = show_url
    client = FakeClient(patch)
    out = FakeOutput(client)
    return patch, client, out, HueAdapter(out, patch, client=client)


async def test_show_is_an_alias_of_dmx():
    _, _, _, a = make()
    assert a.normalize("ne", "show") == "dmx"


async def test_show_mode_asks_show_service_and_never_touches_room_streamer(show_service):
    url, calls, _ = show_service
    _, client, out, a = make("show", url)
    res = await a.apply({"ne": "dmx", "nw": "dmx"}, {})
    assert res.status == "ok", res
    assert calls[0] == ("stream", {"lights": ["hue.ne", "hue.nw"], "on": True})
    assert out.wanted == set()                                   # room's HueOutput never asked to stream
    readings = await a.read(["ne", "nw", "se"])
    assert readings["ne"].value == "dmx" and readings["nw"].value == "dmx"
    assert readings["se"].value == {"on": True, "bri": 22, "xy": [0.482, 0.409]}
    await a.close()


async def test_leaving_the_show_releases_before_writing_the_state(show_service):
    url, calls, _ = show_service
    _, client, out, a = make("show", url)
    await a.apply({"ne": "dmx"}, {})
    calls.clear()
    stored = {"on": True, "bri": 40, "xy": [0.3, 0.3]}
    res = await a.apply({"ne": stored}, {"over_s": 0})
    assert res.status == "ok", res
    assert [c[0] for c in calls[:2]] == ["stream", "release"]
    assert calls[0][1] == {"lights": [], "on": False} and calls[1][1] == {"lights": ["hue.ne"]}
    light_puts = [p for p in client.puts if p[0] == "light"]
    assert light_puts and light_puts[-1][2]["dimming"]["brightness"] == 40.0
    assert (await a.read(["ne"]))["ne"].value == stored
    await a.close()


async def test_show_service_unreachable_is_unconfirmed_not_failed():
    _, _, _, a = make("show", "http://127.0.0.1:9")            # nothing listens on port 9
    res = await a.apply({"ne": "dmx"}, {})
    assert res.status == "unconfirmed" and res.members["ne"].retry
    await a.close()


async def test_release_unconfirmed_while_lamp_still_streams(show_service):
    url, calls, _ = show_service
    _, client, _, a = make("show", url)
    await a.apply({"ne": "dmx"}, {})
    a.patch.hue["show_url"] = "http://127.0.0.1:9"             # show_service gone mid-show
    client.lights[a.devices["ne"].extra["light"]]["mode"] = "streaming"
    res = await a.apply({"ne": "off"}, {"over_s": 0})
    assert res.status == "unconfirmed"
    assert not [p for p in client.puts if p[0] == "light"]      # nothing written to a streaming lamp
    await a.close()


async def test_room_mode_unchanged():
    _, _, out, a = make("room")
    res = await a.apply({"ne": "dmx"}, {})
    assert out.wanted == {"hue.ne"}
    assert res.members["ne"].status in ("ok", "unconfirmed")
    await a.close()


async def test_hue_output_yields_the_area_under_show():
    from room_service.hue.output import HueOutput
    patch = load_patch(DEFAULT_PATCH)
    patch.hue = {**patch.hue, "streamer": "show"}
    rx = ArtNetReceiver(0)
    rx.inject({33: 255})                                         # fresh Art-Net
    out = HueOutput(FakeClient(patch), patch, rx)

    async def must_not_start():
        raise AssertionError("room's HueOutput started the area under hue.streamer: show")
    out._start_stream = must_not_start
    out.set_wanted({"hue.ne"})
    out.start()
    await asyncio.sleep(0.2)
    assert out.state == "show"
    patch.hue["streamer"] = "room"                               # rollback: flip back
    out._start_stream = lambda: asyncio.sleep(0)
    await asyncio.sleep(0.2)
    assert out.state != "show"
    await out.stop()


async def test_first_contact_adopts_show_services_set(show_service):
    url, calls, st = show_service
    st["lights"] = ["hue.ne", "hue.se"]                         # persisted across a room_service restart
    _, _, _, a = make("show", url)
    r = await a.read(["ne", "se", "nw"])
    assert {m for m, x in r.items() if x.value == "dmx"} == {"ne", "se"}
    assert [c[0] for c in calls] == ["status"]                  # adopted: no blink-inducing re-POST
    await a.close()


async def test_show_service_restart_gets_our_set_again(show_service):
    url, calls, st = show_service
    _, _, _, a = make("show", url)
    await a.apply({"ne": "dmx"}, {})
    await a.read(["ne"])
    st["boot"], st["lights"] = "boot-2", []                     # it restarted and lost the set
    calls.clear()
    await a.read(["ne"])
    assert ("stream", {"lights": ["hue.ne"], "on": True}) in calls
    await a.close()


async def test_mode_change_while_down_wins_over_restored_set(show_service):
    url, calls, st = show_service
    _, _, _, a = make("show", url)
    await a.apply({"ne": "dmx", "nw": "dmx"}, {})
    a.patch.hue["show_url"] = "http://127.0.0.1:9"             # show_service goes down...
    await a.apply({"nw": "dmx", "ne": "dmx", "se": "dmx"}, {})  # ...and the mode changes meanwhile
    assert a._show_pending
    a.patch.hue["show_url"] = url                               # back up with its persisted (older) set
    calls.clear()
    await a.read(["ne"])
    assert ("stream", {"lights": ["hue.ne", "hue.nw", "hue.se"], "on": True}) in calls
    assert st["lights"] == ["hue.ne", "hue.nw", "hue.se"]
    await a.close()


async def test_release_refused_by_takeover_is_failed_not_retried(show_service):
    url, calls, st = show_service
    _, client, _, a = make("show", url)
    await a.apply({"ne": "dmx"}, {})

    async def refuse(request):
        return web.json_response({"released": False, "why": "taken_over", "state": "taken_over"})
    async def stream(request):
        return web.json_response({"state": "taken_over"})
    app = web.Application()
    app.router.add_post("/api/v1/hue/release", refuse)
    app.router.add_post("/api/v1/hue/stream", stream)
    server = TestServer(app)
    await server.start_server()
    a.patch.hue["show_url"] = f"http://127.0.0.1:{server.port}"
    res = await a.apply({"ne": "off"}, {"over_s": 0})
    assert res.status == "failed" and not res.members["ne"].retry
    assert not [p for p in client.puts if p[0] == "light"]
    await server.close()
    await a.close()


async def test_idle_with_matching_set_is_not_re_sent(show_service):
    url, calls, st = show_service
    _, _, _, a = make("show", url)
    await a.apply({"ne": "dmx"}, {})
    await a.read(["ne"])
    calls.clear()
    st["idle_anyway"] = True                                    # their output is off: state idle, set kept
    await a.read(["ne"])
    assert [c[0] for c in calls] == ["status"]
    await a.close()
