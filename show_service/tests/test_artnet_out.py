"""artnet_out: the ArtDmx wire, the patch layout, the beat-phased frame grid and the pulse edges, the yield rule.

Everything that sends goes to an ephemeral UDP socket on 127.0.0.1 that the test owns: never port 6454, never
room_service. The yield check is driven through an injected poll function: no HTTP to :8700.
"""

from __future__ import annotations

import math
import socket
import statistics
import struct
import threading
import time

import pytest

from show_service import artnet_out as ao
from show_service.contract import BAND_NAMES

PATCH = ao.PATCH_FILE           # config/room/patch.yaml, read only

SCREEN_FIELDS = ["r", "g", "b", "pulse", "global.activity", "pan", "tilt", "zoom", "effect", "variant"]
HUE_FIELDS = ["dimmer", "r", "g", "b"]


# -- helpers ----------------------------------------------------------------------------------------------------------
def parse(pkt: bytes) -> tuple[int, int, int, bytes]:
    """An independent ArtDmx reader (Art-Net 4 spec): (opcode, seq, port_address, data); asserts the fixed fields."""
    assert pkt[:8] == b"Art-Net\x00"
    (op,) = struct.unpack_from("<H", pkt, 8)
    assert pkt[10] == 0 and pkt[11] == 14            # ProtVerHi, ProtVerLo
    seq, phys, sub_uni, net = pkt[12], pkt[13], pkt[14], pkt[15]
    assert phys == 0
    (length,) = struct.unpack_from(">H", pkt, 16)
    assert length % 2 == 0 and 2 <= length <= 512 and len(pkt) == 18 + length
    return op, seq, (net & 0x7F) << 8 | sub_uni, pkt[18:]


class FakeClock:
    """Beats at anchor + k * period (from the beat clock's point of view: acoustic host seconds)."""

    def __init__(self, anchor: float, period: float, beats_per_bar: int = 4) -> None:
        self.anchor, self.period, self.beats_per_bar = anchor, period, beats_per_bar
        self.locked = True

    def next_beat(self, t: float):
        if not self.locked:
            return None
        k = math.ceil((t - self.anchor) / self.period - 1e-12)
        return self.anchor + k * self.period

    def beats(self, t0: float, t1: float) -> list[float]:
        out, b = [], self.next_beat(t0)
        while b is not None and b <= t1:
            out.append(b)
            b = self.next_beat(b + 1e-6)
        return out


def feats(t_s: float, *, energy=0.5, brightness=0.5, bass=0.5, punch=0.5, hits=0) -> dict:
    return {"t_ms": t_s * 1000.0, "stream": "test",
            "bands": {n: {"level": bass if n == "bass" else 0.0, "hits": hits, "presence": 0.0} for n in BAND_NAMES},
            "onset": 0.0, "lufs_m": -20.0, "lufs_s": -20.0, "peak_dbfs": -6.0, "rms_dbfs": -20.0,
            "brightness": brightness, "noisiness": 0.1, "gate": 1.0, "energy": energy, "punch": punch, "drive": 0.0,
            "spectrum32": [0.0] * 32}


def layout():
    return ao.load_layout(PATCH)


def ch(lay: ao.Layout, dev: str, field: str) -> int:
    """0-based universe index of a device's field."""
    d = next(x for x in lay.devices if x.id == dev)
    return next(c.index for c in d.channels if c.field == field)


def run(vm: ao.VirtualMaestro, t0: float, t1: float) -> list[tuple[float, bytes]]:
    """Drive the loop's steps with fake times: [(send time, 512 channels)]."""
    out, t = [], t0
    while True:
        t = vm.plan(t)
        if t > t1:
            return out
        if vm.compose(t):
            out.append((t, vm.channels()))


def edges(frames, idx: int) -> list[float]:
    """Send times of the frames where channel idx rises from 0."""
    out, prev = [], 0
    for t, data in frames:
        if data[idx] > 0 and prev == 0:
            out.append(t)
        prev = data[idx]
    return out


@pytest.fixture
def rx():
    """The test's own receiver: 127.0.0.1 on an ephemeral port (never 6454)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(0.05)
    assert s.getsockname()[1] != ao.PORT
    yield s
    s.close()


def collect(sock: socket.socket, seconds: float) -> list[tuple[float, bytes]]:
    out, end = [], time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            pkt = sock.recv(2048)
        except socket.timeout:
            continue
        out.append((time.monotonic(), pkt))
    return out


# -- the wire ---------------------------------------------------------------------------------------------------------
def test_artdmx_header_and_data():
    data = bytes(range(256)) * 2
    pkt = ao.build_artdmx(0, data, 7)
    op, seq, pa, out = parse(pkt)
    assert (op, seq, pa) == (0x5000, 7, 0)
    assert pkt[8:10] == b"\x00\x50"                  # OpCode little-endian
    assert pkt[16:18] == b"\x02\x00"                 # Length big-endian: 512
    assert out == data and len(pkt) == 530


def test_port_address_split():
    for pa, sub_uni, net in ((0, 0, 0), (1, 1, 0), (0x1234, 0x34, 0x12), (0x7FFF, 0xFF, 0x7F), (0x100, 0, 1)):
        pkt = ao.build_artdmx(pa, bytes(512), 1)
        assert (pkt[14], pkt[15]) == (sub_uni, net)
        assert parse(pkt)[2] == pa
        f = ao.ArtDmxFrame(pa)
        assert (f.buf[14], f.buf[15]) == (sub_uni, net)
    for bad in (-1, 1 << 15):
        with pytest.raises(ValueError):
            ao.build_artdmx(bad, b"\x00\x00", 1)
        with pytest.raises(ValueError):
            ao.ArtDmxFrame(bad)


def test_length_is_even_and_at_most_512():
    assert parse(ao.build_artdmx(0, b"\x01\x02\x03\x04\x05", 1))[3] == b"\x01\x02\x03\x04\x05\x00"
    assert parse(ao.build_artdmx(0, b"", 1))[3] == b"\x00\x00"
    assert len(parse(ao.build_artdmx(0, bytes(600), 1))[3]) == 512


def test_sequence_is_1_to_255_and_wraps_to_1():
    assert [ao.next_seq(s) for s in (0, 1, 254, 255)] == [1, 2, 255, 1]
    with pytest.raises(ValueError):
        ao.build_artdmx(0, b"\x00\x00", 0)
    f = ao.ArtDmxFrame(0)
    seqs = [f.advance()[12] for _ in range(600)]
    assert 0 not in seqs
    assert seqs[:3] == [1, 2, 3] and seqs[254:257] == [255, 1, 2]
    assert all(b == a % 255 + 1 for a, b in zip(seqs, seqs[1:]))


# -- the layout -------------------------------------------------------------------------------------------------------
def test_layout_matches_patch_yaml():
    lay = layout()
    assert lay.universe == 0
    by_id = {d.id: d for d in lay.devices}
    for dev, addr in (("screen.north", 1), ("screen.south", 17)):
        d = by_id[dev]
        assert d.kind == "screen" and d.address == addr
        assert [c.field for c in d.channels] == SCREEN_FIELDS
        assert [c.index for c in d.channels] == list(range(addr - 1, addr - 1 + 10))
        assert [c.attr for c in d.channels] == ["RED", "GREEN", "BLUE", "DIMMER", "ACTIVITY", "PAN", "TILT", "ZOOM",
                                                "GOBO", "PRISM"]
    for dev, addr in (("hue.ne", 33), ("hue.nw", 37), ("hue.se", 41), ("hue.sw", 45)):
        d = by_id[dev]
        assert d.kind == "hue" and d.address == addr
        assert [c.field for c in d.channels] == HUE_FIELDS
        assert d.channels[0].attr == "MASTER DIMMER" and d.channels[0].default == 255


def test_universe_bytes_follow_the_profiles():
    lay = layout()
    pal = {"north-screen": (1, 0, 0), "south-screen": (0, 0, 1), "hue.ne": (0, 1, 0), "*": (1, 1, 1)}
    vm = ao.VirtualMaestro(layout=lay, palette_fn=lambda t: pal, hue_depth=0.0, idle_after_s=None,
                           features_fn=lambda: feats(10.0, energy=0.5, brightness=1.0, bass=0.0))
    for _ in range(3):
        vm.section_change()
    assert vm.compose(10.0)
    d = vm.channels()
    # screen.north at 1: R G B DIMMER ACTIVITY PAN TILT ZOOM GOBO PRISM
    assert list(d[0:10]) == [255, 0, 0, 0, 128, 255, 0, 0, 3, 0]
    assert list(d[16:26]) == [0, 0, 255, 0, 128, 255, 0, 0, 3, 0]
    # hue: MASTER DIMMER 255, then RGB: by device id, then its screen
    assert list(d[32:36]) == [255, 0, 255, 0]        # hue.ne: its own colour
    assert list(d[36:40]) == [255, 255, 0, 0]        # hue.nw: north-screen's
    assert list(d[40:44]) == [255, 0, 0, 255]        # hue.se: south-screen's
    assert list(d[44:48]) == [255, 0, 0, 255]
    patched = set(range(0, 10)) | set(range(16, 26)) | set(range(32, 48))
    assert all(d[i] == 0 for i in range(512) if i not in patched)
    # what goes on the wire is the same universe at Port-Address 0
    op, _, pa, data = parse(bytes(vm.frame.advance()))
    assert (op, pa, data) == (0x5000, 0, d)


def test_layout_follows_a_reordered_profile_and_universe():
    raw = {"artnet": {"universe": 0x123},
           "profiles": {"s": {"channels": [{"field": "pan", "range": [-1, 1]}, {"field": "pulse"},
                                           {"field": "effect", "raw": True}, {"field": "spare", "default": 7}]},
                        "h": {"channels": [{"field": "b"}, {"field": "dimmer", "default": 255}]}},
           "devices": {"screen.x": {"profile": "s", "address": 100}, "hue.y": {"profile": "h", "address": 511},
                       "fog.z": {"kind": "other", "profile": "h", "address": 200}}}
    lay = ao.parse_layout(raw)
    assert lay.universe == 0x123 and [d.id for d in lay.devices] == ["screen.x", "fog.z", "hue.y"]
    vm = ao.VirtualMaestro(layout=lay, palette_fn=lambda t: (0, 0, 1), hue_depth=0.0,
                           features_fn=lambda: feats(5.0, brightness=0.75), idle_after_s=None)
    assert vm.compose(5.0)
    d = vm.channels()
    assert list(d[99:103]) == [191, 0, 0, 7]         # pan 0.5 on -1..1, no pulse, GOBO 0, an unknown field's default
    assert list(d[510:512]) == [255, 255]            # hue.y: blue, dimmer
    assert list(d[199:201]) == [0, 255]              # other: defaults only
    assert (vm.frame.buf[14], vm.frame.buf[15]) == (0x23, 0x01)


def test_layout_errors():
    base = {"profiles": {"p": {"channels": [{"field": "r"}, {"field": "g"}]}}}
    with pytest.raises(ao.LayoutError):
        ao.parse_layout({**base, "devices": {"hue.a": {"profile": "p", "address": 512}}})     # doesn't fit
    with pytest.raises(ao.LayoutError):
        ao.parse_layout({**base, "devices": {"hue.a": {"profile": "nope", "address": 1}}})
    with pytest.raises(ao.LayoutError):
        ao.parse_layout({"artnet": {"universe": 1 << 15}})
    with pytest.raises(ao.LayoutError):
        ao.parse_layout([])


def test_patch_reload_keeps_the_last_good(tmp_path):
    p = tmp_path / "patch.yaml"
    p.write_text(PATCH.read_text())
    vm = ao.VirtualMaestro(patch_path=p)
    vm._check_patch(force=True)
    assert vm.layout.universe == 0 and vm.patch_error is None
    p.write_text(PATCH.read_text().replace("universe: 0 ", "universe: 1 "))
    vm._patch_mtime = None                            # as if the mtime moved
    vm._check_patch()
    assert vm.layout.universe == 1 and (vm.frame.buf[14], vm.frame.buf[15]) == (1, 0)
    p.write_text("profiles: [unclosed")
    vm._patch_mtime = None
    vm._check_patch()
    assert vm.layout.universe == 1 and vm.patch_error


# -- the frame grid and the pulse ---------------------------------------------------------------------------------------
def test_grid_tick():
    assert ao.grid_tick(0.5) == pytest.approx(0.025)             # 120 bpm -> 40 Hz
    assert 1 / ao.grid_tick(60 / 128) == pytest.approx(42.67, abs=0.01)
    assert ao.grid_tick(1.0) == pytest.approx(1 / 40)            # 60 bpm: 40 frames a beat (<= 43 Hz)
    for p in (0.27, 0.35, 0.4652, 0.5, 0.6, 1.0, 1.4, 2.0):
        tick = ao.grid_tick(p)
        m = round(p / tick)
        assert m % 4 == 0 and abs(m * tick - p) < 1e-9
        assert tick >= ao.MIN_GAP_S + 0.0005                     # slack for a frame that leaves a little late
    assert ao.grid_tick(0.05) == pytest.approx(0.025)            # faster than 4 frames a beat: what fits


@pytest.mark.parametrize("bpm", [60, 90, 120, 128, 174])
def test_pulse_edge_leaves_exactly_on_each_beat(bpm):
    lay = layout()
    clk = FakeClock(anchor=100.013, period=60.0 / bpm)
    vm = ao.VirtualMaestro(layout=lay, clock=clk, features_fn=lambda: feats(0.0, punch=1.0), idle_after_s=None)
    frames = run(vm, 99.0, 108.0)
    times = [t for t, _ in frames]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert min(gaps) >= ao.MIN_GAP_S - 1e-9                       # never over 44 Hz
    beats = clk.beats(times[0] + clk.period, times[-1])
    tick = ao.grid_tick(clk.period)
    steady = [g for t, g in zip(times[1:], gaps) if t > beats[0]]
    assert max(abs(g - tick) for g in steady) < 1e-9              # on the beat-phased grid
    pulse = ch(lay, "screen.north", "pulse")
    e = edges(frames, pulse)
    e = [t for t in e if t >= beats[0] - 1e-9]
    assert len(e) == len(beats)
    for t_edge, b in zip(e, beats):
        assert abs(t_edge - b) < 1e-9                             # the edge frame leaves at the beat
    # one edge per beat for the follower: the pulse is back at <= 0.3 of its peak well before the next beat
    peak = max(d[pulse] for _, d in frames)
    assert peak == round(255 * 0.70)                              # punch 1 -> amp 0.70
    for b in beats[:-1]:
        late = [d[pulse] for t, d in frames if b + 0.5 * clk.period <= t < b + clk.period - 1e-6]
        assert late and max(late) <= 0.3 * peak
    # both screens carry the same edge
    south = ch(lay, "screen.south", "pulse")
    assert edges(frames, south) == edges(frames, pulse)


def test_edge_is_never_early_and_at_most_one_frame_late_when_the_clock_rephases():
    lay = layout()
    clk = FakeClock(anchor=50.0, period=0.5)
    vm = ao.VirtualMaestro(layout=lay, clock=clk, idle_after_s=None)
    pulse = ch(lay, "screen.north", "pulse")
    frames, t = [], 49.0
    shifts = {52.2: +0.007, 54.1: -0.015, 56.3: +0.011, 58.05: +0.240}     # slews and one big jump
    beats_seen = []
    while t < 61.0:
        for at, d in list(shifts.items()):
            if t >= at:
                clk.anchor += d
                del shifts[at]
        t_next = vm.plan(t)
        if vm._upcoming is not None:
            beats_seen.append(vm._upcoming)
        vm.compose(t_next)
        frames.append((t_next, vm.channels()))
        t = t_next
    times = [x for x, _ in frames]
    assert min(b - a for a, b in zip(times, times[1:])) >= ao.MIN_GAP_S - 1e-9
    for t_edge in edges(frames, pulse):
        b = max(x for x in beats_seen if x <= t_edge + 1e-9)     # the beat this edge carries
        assert 0.0 <= t_edge - b < ao.MIN_GAP_S                   # never early, within one frame
    # no double edge: edges stay at least the follower's refractory (0.35 P) apart
    e = edges(frames, pulse)
    assert min(b - a for a, b in zip(e, e[1:])) >= 0.35 * 0.5
    assert len(e) >= 20


def test_no_lock_no_pulse_and_free_run_at_44hz():
    lay = layout()
    clk = FakeClock(anchor=0.0, period=0.5)
    clk.locked = False
    vm = ao.VirtualMaestro(layout=lay, clock=clk, idle_after_s=None)
    frames = run(vm, 10.0, 12.0)
    assert all(d[ch(lay, "screen.north", "pulse")] == 0 for _, d in frames)
    gaps = [b[0] - a[0] for a, b in zip(frames, frames[1:])]
    assert all(abs(g - 1 / 44) < 1e-9 for g in gaps)


def test_gobo_steps_on_sections_and_prism_flips_per_bar():
    lay = layout()
    clk = FakeClock(anchor=20.0, period=0.5)
    vm = ao.VirtualMaestro(layout=lay, clock=clk, idle_after_s=None)
    gobo, prism = ch(lay, "screen.north", "effect"), ch(lay, "screen.north", "variant")
    frames = run(vm, 19.9, 22.2)                                  # beats 20.0 .. 22.0: 5 edges
    assert {d[gobo] for _, d in frames} == {0}
    at = {round(t, 3): d[prism] for t, d in frames}
    assert at[20.0] == 0 and at[21.5] == 0 and at[22.0] == 1     # beats 1-4 bar 0, beat 5 bar 1
    for _ in range(257):
        vm.section_change()
    vm.compose(22.3)
    assert vm.channels()[gobo] == 257 % 256


def test_hue_is_scheduled_l_hue_ahead_of_the_screens():
    lay = layout()
    T = 30.0
    vm = ao.VirtualMaestro(layout=lay, palette_fn=lambda t: (1, 0, 0) if t < T else (0, 0, 1), hue_depth=0.0,
                           hue_latency_ms=100.0, idle_after_s=None)
    frames = run(vm, 29.0, 31.0)
    hue_b, scr_b = ch(lay, "hue.ne", "b"), ch(lay, "screen.north", "b")
    t_hue = next(t for t, d in frames if d[hue_b] == 255)
    t_scr = next(t for t, d in frames if d[scr_b] == 255)
    assert T - 0.1 <= t_hue < T - 0.1 + ao.MIN_GAP_S
    assert T <= t_scr < T + ao.MIN_GAP_S
    vm.set_hue_latency_ms(0.0)
    assert vm.status()["hue_latency_ms"] == 0.0


def test_hue_swells_softly_on_the_beat():
    lay = layout()
    clk = FakeClock(anchor=40.0, period=0.5)
    vm = ao.VirtualMaestro(layout=lay, clock=clk, palette_fn=lambda t: (1, 1, 1), idle_after_s=None)
    frames = run(vm, 39.5, 42.0)
    r = ch(lay, "hue.ne", "r")
    on_beat = [d[r] for t, d in frames if any(abs(t - b) < 1e-9 for b in (40.5, 41.0, 41.5))]
    mid = [d[r] for t, d in frames if any(abs(t - b) < 0.02 for b in (40.75, 41.25))]
    assert on_beat and min(on_beat) == 255
    assert mid and max(mid) < 200 and min(mid) >= round(255 * 0.3)
    pre = [d[r] for t, d in frames if 40.95 <= t < 41.0]          # the attack starts >= 80 ms ahead: rising
    assert pre == sorted(pre)
    assert all(d[ch(lay, "hue.ne", "dimmer")] == 255 for _, d in frames)


def test_idle_without_fresh_features():
    lay = layout()
    box = {"f": None}
    vm = ao.VirtualMaestro(layout=lay, features_fn=lambda: box["f"])
    assert not vm.compose(10.0)
    box["f"] = feats(1.0)                                         # 9 s old
    assert not vm.compose(10.0)
    box["f"] = feats(9.99)
    assert vm.compose(10.0) and vm.active


def test_a_failing_callback_never_stops_the_frame():
    lay = layout()

    def boom(*_):
        raise RuntimeError("bug")

    class BadClock:
        def next_beat(self, t):
            raise RuntimeError("bug")

    vm = ao.VirtualMaestro(layout=lay, palette_fn=boom, features_fn=boom, clock=BadClock(), idle_after_s=None)
    t = vm.plan(5.0)
    assert vm.compose(t)
    assert vm.callback_errors >= 3
    assert vm.channels()[0] == round(255 * ao.DEFAULT_RGB[0])


def test_stand_ins_move_slowly():
    lay = layout()
    box = {"f": feats(0.0, brightness=0.0, bass=0.0, hits=0)}
    vm = ao.VirtualMaestro(layout=lay, features_fn=lambda: box["f"], idle_after_s=None)
    pan, zoom = ch(lay, "screen.north", "pan"), ch(lay, "screen.north", "zoom")
    assert vm.compose(0.0) and vm.channels()[pan] == 0            # seeded dark: PAN -1
    frames, t, prev = [], 0.0, 0
    while t < 6.0:
        t = vm.plan(t)
        box["f"] = feats(round(t / 0.04) * 0.04, brightness=1.0, bass=1.0, hits=1)   # 25 Hz snapshots, 6 hits each
        vm.compose(t)
        d = vm.channels()
        assert abs(d[pan] - prev) <= 0.25 * 255 / 2               # never an inputs.py "teleport" in one frame
        prev = d[pan]
        frames.append((t, d))
    assert frames[-1][1][pan] > 240                               # brightness 1 -> PAN +1, after a few tau
    assert frames[-1][1][zoom] == 255                             # 150 hits/s >= 8/s


# -- the yield rule ---------------------------------------------------------------------------------------------------
def test_foreign_sender():
    ours = frozenset({"127.0.0.1", "172.20.0.5"})
    st = lambda frm, age, online=True: {"enabled": True, "input": {"last_from": frm, "age_ms": age,
                                                                   "online": online}}
    assert ao.foreign_sender(st("192.168.1.50", 30.0), ours) == "192.168.1.50"
    assert ao.foreign_sender(st("192.168.1.50", 5000.0, False), ours) is None     # gone quiet
    assert ao.foreign_sender(st("127.0.0.1", 10.0), ours) is None                 # us (or room's own test show)
    assert ao.foreign_sender(st("172.20.0.5", 10.0), ours) is None
    assert ao.foreign_sender(st(None, None, False), ours) is None
    assert ao.foreign_sender({"enabled": False}, ours) is None
    assert ao.foreign_sender({"enabled": True, "input": {"last_from": "10.0.0.9", "online": True}}, ours) == "10.0.0.9"
    assert ao.foreign_sender("junk", ours) is None


def test_check_foreign_pauses_and_resumes():
    box = {"st": {"enabled": True, "input": {"last_from": "192.168.1.50", "age_ms": 20.0}}}
    vm = ao.VirtualMaestro(layout=layout(), poll_fn=lambda: box["st"])
    vm.check_foreign()
    assert vm.yielded_to == "192.168.1.50" and vm.yields == 1
    vm.check_foreign()
    assert vm.yields == 1
    box["st"] = None

    def down():
        raise OSError("refused")

    vm.poll_fn = down
    vm.check_foreign()
    assert vm.yielded_to == "192.168.1.50" and vm.poll_errors == 1          # a failed poll changes nothing
    vm.poll_fn = lambda: {"enabled": True, "input": {"last_from": "192.168.1.50", "age_ms": 4000.0}}
    vm.check_foreign()
    assert vm.yielded_to is None


# -- config ---------------------------------------------------------------------------------------------------------
def test_off_by_default():
    assert ao.config_from_env({}).target is None
    assert ao.config_from_env({"SHOW_ARTNET_TARGET": "off"}).target is None
    cfg = ao.config_from_env({"SHOW_ARTNET_TARGET": "127.0.0.1:6454", "SHOW_HUE_LATENCY_MS": "85",
                              "SHOW_ROOM_DMX_URL": ""})
    assert cfg.target == ("127.0.0.1", 6454) and cfg.hue_latency_ms == 85.0 and cfg.room_dmx_url is None
    assert ao.parse_target("lamp-node") == ("lamp-node", 6454)
    vm = ao.VirtualMaestro()
    vm.start()                                                    # no target: nothing starts, nothing is sent
    assert vm._threads == [] and vm.status()["state"] == "off"
    vm.stop()


# -- end to end over a real (ephemeral) UDP socket ------------------------------------------------------------------------
def test_sends_on_the_beat_grid_over_udp(rx):
    lay = layout()
    clk = FakeClock(anchor=time.monotonic() + 0.2, period=0.3)
    vm = ao.VirtualMaestro(target=rx.getsockname(), layout=lay, clock=clk,
                           features_fn=lambda: feats(time.monotonic()), room_dmx_url=None)
    vm.start()
    try:
        got = collect(rx, 1.6)
    finally:
        vm.stop()
    assert len(got) >= 30 and len(got) <= 1.6 * 44 + 2
    seqs, pulse = [], ch(lay, "screen.north", "pulse")
    for _, pkt in got:
        op, seq, pa, data = parse(pkt)
        assert op == 0x5000 and pa == 0 and len(data) == 512
        seqs.append(seq)
    assert seqs[0] == 1 and all(b == a % 255 + 1 for a, b in zip(seqs, seqs[1:]))
    arrivals = [(t, parse(p)[3]) for t, p in got]
    e = edges(arrivals, pulse)
    assert len(e) >= 4
    lateness = []
    for t_edge in e:
        b = max(x for x in clk.beats(t_edge - 1.0, t_edge + 0.002))
        lateness.append(t_edge - b)
    assert min(lateness) >= -0.002                                # never before the beat
    assert statistics.median(lateness) < ao.MIN_GAP_S             # within one frame (wall clock, niced, under load)
    assert max(lateness) < 3 * ao.MIN_GAP_S
    st = vm.status()
    assert st["packets"] >= len(got) and st["beats"] >= 4 and st["send_errors"] == 0


def test_yields_over_udp_then_resumes(rx):
    box = {"st": {"enabled": True, "input": {"last_from": "192.168.1.50", "age_ms": 15.0}}}
    vm = ao.VirtualMaestro(target=rx.getsockname(), layout=layout(), idle_after_s=None,
                           poll_fn=lambda: box["st"], poll_s=0.05)
    vm.start()
    try:
        deadline = time.monotonic() + 2.0
        while vm.yielded_to is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert vm.status()["state"] == "yielded"
        collect(rx, 0.1)                                          # drain anything already in flight
        assert collect(rx, 0.3) == []
        box["st"] = {"enabled": True, "input": {"last_from": "127.0.0.1", "age_ms": 15.0}}
        assert len(collect(rx, 0.5)) >= 5
        assert vm.status()["state"] == "sending"
    finally:
        vm.stop()
    assert not any(t.is_alive() for t in threading.enumerate() if t.name.startswith("show-artnet"))


# -- robustness, timing from the actual send, the clock's bar, palette and the quiet room ------------------------------
def test_nan_inputs_send_defaults_and_never_stick():
    lay = layout()
    dev = next(d for d in lay.devices if d.id == "screen.north")
    data = bytearray(512)
    ao.encode_into(data, dev, {"pulse": math.nan, "global.activity": math.inf, "effect": math.nan,
                               "rgb": (math.nan, 0.5, 1.0)})
    for c in dev.channels:
        if c.field in ("pulse", "global.activity", "effect", "r"):
            assert data[c.index] == c.default, c.field
    box = {"f": feats(0.0, energy=math.nan, brightness=math.nan, bass=math.nan, punch=math.nan)}
    clk = FakeClock(anchor=0.5, period=0.5)
    vm = ao.VirtualMaestro(layout=lay, clock=clk, features_fn=lambda: box["f"], idle_after_s=None)
    frames = run(vm, 0.0, 2.0)                                    # never raises
    assert frames and vm.callback_errors == 0
    assert all(math.isfinite(x) for x in (vm._pan, vm._tilt, vm._energy, vm._pulse_amp))
    box["f"] = feats(0.0, brightness=1.0)
    run(vm, 2.0, 6.0)
    assert vm.channels()[ch(lay, "screen.north", "pan")] > 128   # the smoother moved on: no NaN stuck in it


class _Stopper:
    """Stands in for the loop's stop Event: wait() returns at once; stops after ``n`` waits."""

    def __init__(self, n: int) -> None:
        self.n, self.flag = n, False

    def is_set(self) -> bool:
        return self.flag

    def wait(self, _timeout: float) -> bool:
        self.n -= 1
        self.flag = self.n <= 0
        return self.flag

    def set(self) -> None:
        self.flag = True


def test_a_failing_frame_is_counted_and_logged_once_per_30s(caplog):
    vm = ao.VirtualMaestro(layout=layout(), idle_after_s=None)

    def boom(t):
        raise RuntimeError("compose bug")

    vm.compose = boom
    vm._stop = _Stopper(6)
    with caplog.at_level("ERROR", logger="show_service.artnet_out"):
        vm._run()                                                 # returns once the stopper says so
    assert vm.frame_errors == 6 and vm.status()["frame_errors"] == 6
    assert sum("frame failed" in r.getMessage() for r in caplog.records) == 1


class _SimTime:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def sleep(self, d: float) -> None:
        self.t += max(0.0, d)


class _LateSock:
    """Each send leaves ``late[i]`` after it was due (the scheduler); records when, stops the loop after ``n``."""

    def __init__(self, sim: _SimTime, vm: ao.VirtualMaestro, late: list[float], n: int) -> None:
        self.sim, self.vm, self.late, self.n = sim, vm, late, n
        self.sent: list[float] = []

    def send(self, buf) -> int:
        self.sim.t += self.late[len(self.sent) % len(self.late)]
        self.sent.append(self.sim.t)
        if len(self.sent) >= self.n:
            self.vm._stop.set()
        return len(buf)


@pytest.mark.parametrize("beat", [False, True])
def test_frames_keep_the_minimum_gap_from_when_they_actually_left(beat):
    sim = _SimTime(100.0)
    period = 0.4652                                               # tick 23.26 ms: the grid closest to 44 Hz
    clk = FakeClock(anchor=100.5, period=period) if beat else None
    vm = ao.VirtualMaestro(layout=layout(), clock=clk, idle_after_s=None, time_fn=sim)
    vm._sleep = sim.sleep
    late = [0.0003, 0.0003, 0.0003, 0.004] if beat else [0.0, 0.004]   # mostly on time, now and then 4 ms late
    sock = _LateSock(sim, vm, late, 400)
    vm._sock = sock
    vm._run()
    gaps = [b - a for a, b in zip(sock.sent, sock.sent[1:])]
    assert min(gaps) >= ao.MIN_GAP_S - 1e-9                       # never over 44 Hz, as sent
    if beat:
        beats = clk.beats(100.5, sock.sent[-1] - 0.05)
        for b in beats:                                           # every edge leaves with its beat (+ its lateness)
            assert any(0.0 <= t - b <= 0.0041 for t in sock.sent), b
        tick = ao.grid_tick(period)
        for i, g in enumerate(gaps):                              # 0.3 ms late fits the grid's slack: no skip;
            if late[i % 4] == late[(i + 1) % 4] == 0.0003:        # 4 ms late costs at most one grid point
                assert g == pytest.approx(tick, abs=1e-6)
            assert g <= 2 * tick + 0.0041


def test_foreign_sender_by_rate_and_while_yielded():
    ours = frozenset({"127.0.0.1"})

    def st(frm="127.0.0.1", age=10.0, pps=42.0, source="artnet"):
        return {"enabled": True, "input": {"last_from": frm, "age_ms": age, "pps": pps, "source": source}}

    assert ao.foreign_sender(st(pps=85.0), ours, own_hz=41.0) == "127.0.0.1 (+44 pps)"   # a relay from localhost
    assert ao.foreign_sender(st(pps=46.0), ours, own_hz=41.0) is None                    # just us (jitter)
    assert ao.foreign_sender(st(pps=85.0), ours) is None                                 # not sending: no rate test
    assert ao.foreign_sender(st(pps=85.0, age=4000.0), ours, own_hz=41.0) is None        # stale
    assert ao.foreign_sender(st(pps=44.0), ours, yielded=True) == "127.0.0.1"            # we're quiet: not us
    assert ao.foreign_sender(st(source="fake"), ours, yielded=True) is None              # room's own test show
    assert ao.foreign_sender(st(age=3000.0), ours, yielded=True) is None
    assert ao.foreign_sender(st(frm="192.168.1.50"), ours) == "192.168.1.50"            # the address rule stays


def test_check_foreign_yields_to_a_second_sender_by_rate_and_resumes():
    box = {"st": {"enabled": True, "input": {"last_from": "127.0.0.1", "age_ms": 12.0, "pps": 86.0,
                                             "source": "artnet"}}}
    vm = ao.VirtualMaestro(layout=layout(), poll_fn=lambda: box["st"])
    vm.check_foreign()
    assert vm.yielded_to is None                                  # we aren't sending: nothing to compare
    vm.active, vm._interval_s, vm._sent_t = True, 1 / 42.0, time.monotonic()
    vm.check_foreign()
    assert vm.yielded_to == "127.0.0.1 (+44 pps)" and vm.yields == 1
    box["st"] = {"enabled": True, "input": {"last_from": "127.0.0.1", "age_ms": 9.0, "pps": 44.0,
                                            "source": "artnet"}}
    vm.check_foreign()
    assert vm.yielded_to is not None                              # the other sender alone, at its own rate
    box["st"]["input"]["age_ms"] = 5000.0
    vm.check_foreign()
    assert vm.yielded_to is None and vm.yields == 1


class SnapClock(FakeClock):
    """A FakeClock that also publishes a music.clock body, with its own beat numbering and bar anchor."""

    def __init__(self, anchor: float, period: float, k0: int, bar_anchor: int) -> None:
        super().__init__(anchor, period)
        self.k0, self.bar_anchor = k0, bar_anchor

    def snapshot(self):
        return {"bpm": 60.0 / self.period, "anchor_ms": self.anchor * 1000.0, "beat_at_anchor": self.k0,
                "beats_per_bar": 4, "bar_anchor_beat": self.bar_anchor, "gate": "beat"}


def test_prism_follows_the_clocks_bar_numbering():
    lay = layout()
    clk = SnapClock(anchor=20.0, period=0.5, k0=100, bar_anchor=102)   # beat 102 (t = 21.0) starts a bar
    vm = ao.VirtualMaestro(layout=lay, clock=clk, idle_after_s=None)
    prism = ch(lay, "screen.north", "variant")
    at = {round(t, 3): d[prism] for t, d in run(vm, 19.9, 23.2)}
    assert at[20.0] == 1 and at[20.5] == 1                       # beats 100, 101: the bar before 102's
    assert at[21.0] == 0 and at[21.25] == 0 and at[22.5] == 0    # beats 102-105: held between edges
    assert at[23.0] == 1                                          # beat 106: the next bar
    vm2 = ao.VirtualMaestro(layout=lay, clock=SnapClock(20.0, 0.5, 100, 102), idle_after_s=None)
    at2 = {round(t, 3): d[prism] for t, d in run(vm2, 21.9, 23.2)}   # starts mid-bar: same parity, not our count
    assert at2[22.0] == 0 and at2[23.0] == 1


def test_palette_from_env_and_constructor():
    assert ao.config_from_env({}).palette is None
    assert ao.config_from_env({"SHOW_PALETTE": "Cool"}).palette == ao.PALETTES["cool"]
    assert ao.config_from_env({"SHOW_PALETTE": "0.2, 0.4, 0.6"}).palette == pytest.approx((0.2, 0.4, 0.6))
    assert ao.config_from_env({"SHOW_PALETTE": "255,0,128"}).palette == pytest.approx((1.0, 0.0, 128 / 255))
    assert ao.config_from_env({"SHOW_PALETTE": "mauve-ish"}).palette is None             # logged, default
    with pytest.raises(ValueError):
        ao.parse_palette("1,2")
    lay = layout()
    vm = ao.VirtualMaestro(layout=lay, palette=(0.0, 0.0, 1.0), idle_after_s=None, hue_depth=0.0)
    assert vm.compose(1.0)
    d = vm.channels()
    assert (d[ch(lay, "screen.north", "r")], d[ch(lay, "screen.north", "b")]) == (0, 255)
    assert (d[ch(lay, "hue.ne", "r")], d[ch(lay, "hue.ne", "b")]) == (0, 255)
    assert vm.status()["rgb"] == [0.0, 0.0, 1.0]


def test_a_quiet_room_idles_and_sound_resumes():
    lay = layout()
    box = {"gate": 0.0}

    def f():
        d = feats(box["t"])
        d["gate"], d["energy"] = box["gate"], 0.0
        return d

    vm = ao.VirtualMaestro(layout=lay, features_fn=f)
    box["t"] = 0.0
    sent = []
    for i in range(int(14 / 0.025)):                              # 14 s of a silent (but streaming) room
        box["t"] = t = i * 0.025
        sent.append((t, vm.compose(t)))
    assert all(ok for t, ok in sent if t < 9.9) and not any(ok for t, ok in sent if t > 10.1)
    assert vm.idle_reason == "quiet" and not vm.active
    box["gate"] = 0.03                                            # under QUIET_GATE_ON: still quiet
    assert not vm.compose(14.0)
    box["gate"] = 0.4                                             # music
    box["t"] = 14.1
    assert vm.compose(14.1) and vm.active and vm.idle_reason is None
    vm2 = ao.VirtualMaestro(layout=lay, features_fn=f, quiet_after_s=None)          # the quiet rule off
    box["gate"] = 0.0
    for i in range(30):
        box["t"] = i * 0.5
        assert vm2.compose(i * 0.5)
