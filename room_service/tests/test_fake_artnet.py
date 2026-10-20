"""The fake Art-Net (dmx/fake_artnet.py): real ArtDmx over UDP into the receiver, decoded through the patch."""

import asyncio
import socket

import pytest

from room_service.dmx.artnet import ArtNetReceiver, build_artdmx
from room_service.dmx.fake_artnet import FakeArtNet
from room_service.dmx.patch import DEFAULT_PATCH, load_patch


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def rig():
    patch = load_patch(DEFAULT_PATCH)
    patch.artnet.bind_host, patch.artnet.bind_port = "127.0.0.1", free_udp_port()
    rx = ArtNetReceiver(patch.artnet.universe, patch.artnet.stale_after_ms)
    await rx.start(patch.artnet.bind_host, patch.artnet.bind_port)
    fake = FakeArtNet(lambda: patch, rx)
    yield patch, rx, fake
    fake.stop()
    rx.close()


async def wait_for(cond, timeout=2.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("timed out")
        await asyncio.sleep(0.01)


async def test_packets_drive_every_device(rig):
    patch, rx, fake = rig
    await fake.start("techno", 120)
    await wait_for(lambda: rx.packets >= 10)
    assert rx.source == "fake" and rx.online and not rx.other
    fixtures, glob = patch.decode(rx.dmx)
    assert "activity" in glob
    for dev in patch.devices.values():
        assert max(fixtures[dev.id]["rgb"]) > 0, dev.id          # every screen and lamp has a colour
    assert fake.status()["song"]["song"] == "techno"


async def test_lamps_follow_their_screen_and_pulse(rig):
    patch, rx, fake = rig
    await fake.start("techno", 120)
    t = fake.song.beat_time(8)                                   # on a kick: pulse high
    on = fake.universe(patch, t + 0.01)
    off = fake.universe(patch, t + 0.45)                         # just before the next beat: pulse decayed
    ne = patch.devices["hue.ne"]
    north_on = fake.song.state(t + 0.01)["fixtures"]["screen.north"]
    assert north_on["pulse"] > 0.5
    assert sum(on[ne.address + i] for i in (1, 2, 3)) > sum(off[ne.address + i] for i in (1, 2, 3))
    assert on[ne.address] == 255                                 # MASTER DIMMER static, as on Maestro


async def test_controls_and_restart_keep_the_tempo(rig):
    _, _, fake = rig
    await fake.start("pop", 124)
    assert fake.control({"bpm": 140}) == {"bpm": 140.0}
    with pytest.raises(ValueError):
        fake.control({"bpm": 900})
    fake.stop()
    assert fake.status()["song"]["bpm"] == 140.0 and not fake.running
    with pytest.raises(ValueError):
        fake.control({"bpm": 100})


async def test_real_artnet_stops_the_fake(rig):
    patch, rx, fake = rig
    await fake.start()
    await wait_for(lambda: rx.packets >= 3)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as maestro:
        maestro.sendto(build_artdmx(patch.artnet.universe, bytes(512), 1), (patch.artnet.bind_host, patch.artnet.bind_port))
        await wait_for(lambda: not fake.running)
    assert "real Art-Net" in fake.stopped and rx.source == "artnet"
    with pytest.raises(RuntimeError):                            # and it won't start over live Art-Net
        await fake.start()
