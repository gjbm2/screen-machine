"""HueOutput's fades (owner, 12 Nov: the TVs fade on a mode switch, the lamps cut): the stream starts on each lamp's
own state and eases to the music, and a lamp leaving eases to its next state before the area stops. No bridge: a fake
CLIP client, a fake streamer and a fake clock."""

from room_service.dmx.artnet import ArtNetReceiver
from room_service.dmx.patch import DEFAULT_PATCH, load_patch
from room_service.hue import output as hue_output
from room_service.hue.output import HueOutput, state_xyb


class Clock:
    t = 100.0

    def monotonic(self):
        return self.t


class FakeStreamer:
    def __init__(self):
        self.is_connected, self.sent = False, []

    def connect(self, *a):
        self.is_connected = True

    def send_colors(self, cmds):
        self.sent.append(list(cmds))

    def disconnect(self):
        self.is_connected = False


class FakeClient:
    secrets = type("Secrets", (), {"host": "bridge", "app_key": "k", "clientkey": "c"})()

    def __init__(self, lights):
        self.lights, self.puts = lights, []

    async def get(self, rtype, rid=None):
        if rtype == "light":
            return list(self.lights.values())
        return [{"status": "active", "active_streamer": {"rid": "us"}}]

    async def put(self, rtype, rid, body):
        self.puts.append((rtype, body))


def make(monkeypatch, house):
    clock = Clock()
    monkeypatch.setattr(hue_output, "time", clock)
    patch = load_patch(DEFAULT_PATCH)
    patch.hue = {**patch.hue, "fade_s": 3.0}
    rx = ArtNetReceiver(0)
    rx.inject({33: 255, 34: 255})                                # hue.ne: full red
    ne = patch.devices["hue.ne"].extra["light"]
    out = HueOutput(FakeClient({ne: {"id": ne, **house}}), patch, rx, area_id="area")
    out.streamer = FakeStreamer()

    async def resolve():
        out.channels = {ne: 0}
    out._resolve = resolve
    return clock, out


def bri(out):
    return out.streamer.sent[-1][0][3]


async def test_the_stream_starts_on_the_lamps_own_state_and_eases_in(monkeypatch):
    clock, out = make(monkeypatch, {"on": {"on": False}, "dimming": {"brightness": 50.0},
                                    "color": {"xy": {"x": 0.45, "y": 0.41}}})
    out.wanted = {"hue.ne"}
    await out._start_stream()
    assert bri(out) == 0                                         # off before: the first frame is dark
    clock.t += 1.5
    out._send()
    assert 0 < bri(out) < 0xFFFF
    clock.t += 1.5
    out._send()
    assert bri(out) == 0xFFFF and not out._fades                 # the music, full red


async def test_a_lamp_leaving_eases_to_its_next_state_before_the_stop(monkeypatch):
    clock, out = make(monkeypatch, {"on": {"on": True}, "dimming": {"brightness": 40.0},
                                    "color": {"xy": {"x": 0.3, "y": 0.3}}})
    out.wanted = {"hue.ne"}
    await out._start_stream()
    clock.t += 5
    out._send()
    nxt = {"on": True, "bri": 40, "xy": [0.3, 0.3]}
    out.set_wanted(set(), fade_to={"hue.ne": nxt})
    assert out.fading_out({"hue.ne"})
    clock.t += 1.5
    out._send()
    assert out.fading_out() and out.streamer.sent[-1][0][3] != state_xyb(nxt)[2]
    clock.t += 1.5
    out._send()
    assert tuple(out.streamer.sent[-1][0][1:]) == state_xyb(nxt) and not out.fading_out()


def test_state_xyb_reads_records_and_canonical_values():
    assert state_xyb("off", (100, 200, 300)) == (100, 200, 0)
    assert state_xyb({"on": True, "bri": 100, "xy": [0.5, 0.25]}) == (round(0.5 * 0xFFFF), round(0.25 * 0xFFFF), 0xFFFF)
    warm = state_xyb({"on": True, "bri": 50, "mirek": 400})
    assert warm[0] > warm[1] and warm[2] == round(0xFFFF / 2)
    rec = {"id": "x", "on": {"on": True}, "dimming": {"brightness": 100.0}, "color_temperature": {"mirek": 400}}
    assert state_xyb(rec)[:2] == warm[:2]
