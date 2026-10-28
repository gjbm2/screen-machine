"""brightness.screens / brightness.lamps: pushed to the screens and the Hue stream, kept across a restart."""
import asyncio
from types import SimpleNamespace

from room_service.adapters.room import BrightnessAdapter


class Ctl:
    def __init__(self):
        self.levels = []

    def set_level(self, v):
        self.levels.append(v)


def test_apply_pushes_and_persists(tmp_path):
    north, south = Ctl(), Ctl()
    hue = SimpleNamespace(level=1.0, patch=SimpleNamespace(hue={}))
    a = BrightnessAdapter({"north": north, "south": south}, hue, tmp_path)
    a.restore()
    assert north.levels == [] and hue.level == 1.0          # nothing saved: nothing sent
    out = asyncio.run(a.apply({"screens": a.normalize("screens", 60), "lamps": 35}))
    assert out.status == "ok" and north.levels == [0.6] and south.levels == [0.6] and hue.level == 0.35
    assert asyncio.run(a.read(["screens", "lamps"]))["lamps"].value == 35
    b = BrightnessAdapter({"north": Ctl()}, SimpleNamespace(level=1.0), tmp_path)
    b.restore()
    assert b.values == {"screens": 60, "lamps": 35} and b.c["north"].levels == [0.6] and b.hue.level == 0.35


def test_lamps_under_show_streamer_say_so(tmp_path):
    hue = SimpleNamespace(level=1.0, patch=SimpleNamespace(hue={"streamer": "show"}))
    out = asyncio.run(BrightnessAdapter({}, hue, tmp_path).apply({"lamps": 40}))
    assert out.members["lamps"].status == "unconfirmed"


def test_hue_output_scales_brightness():
    from room_service.hue.output import HueOutput
    sent = []
    o = HueOutput.__new__(HueOutput)
    dev = SimpleNamespace(id="hue.ne", extra={"light": "L"})
    o.patch = SimpleNamespace(hue={"gamma": 1.0}, of_kind=lambda k: [dev],
                              decode=lambda dmx: ({"hue.ne": {"rgb": [1.0, 1.0, 1.0]}}, None))
    o.rx = SimpleNamespace(dmx=b"")
    o.channels, o.wanted = {"L": 3}, {"hue.ne"}
    o.streamer = SimpleNamespace(send_colors=sent.append)
    o._last_sent, o._last_send_t, o.frames = None, 0.0, 0
    o.level, o._lv, o._lv_t = 0.5, 0.5, None
    o._send()
    assert sent[-1][0][3] == round(0xFFFF * 0.5)
