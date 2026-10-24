"""artnet_lan: the wire (ArtDmx, ArtPoll, ArtAddress, ArtPollReply), the beat-phased frame grid, spacing, hits, short
frames and keepalive, the universe, discovery and health, failsafe, the loopback:6454 rule, the virtual node.

Rule (README S5.11): **Art-Net never to the LAN or to 127.0.0.1:6454.** Most tests send into a FakeSock that records
and asserts; the few real-socket tests bind only 127.0.0.1:0 (ephemeral) and send only to sinks the test owns. A
guard makes Nodes' own socket factories raise, and nodes.json is always in tmp_path.
"""

from __future__ import annotations

import json
import math
import select
import socket
import statistics
import struct
import time

import pytest

from show_service import artnet_lan as al
from show_service.artnet_out import MIN_GAP_S, grid_tick

FORBIDDEN_PORTS = {6454, 8700, 8701, 8702, 8703, 6980, 6981, 6982}


# -- guards and fakes ---------------------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _guard(monkeypatch, tmp_path):
    def no_socket(*a, **k):
        raise AssertionError("artnet_lan opened its own socket in a test")
    monkeypatch.setattr(al, "_udp_socket", no_socket)
    monkeypatch.setattr(al, "_bound_socket", no_socket)
    monkeypatch.setattr(al.contract, "NODES_STATE_FILE", tmp_path / "default-nodes.json")


def _check_addr(addr):
    host, port = addr
    assert host.startswith("127."), f"a test tried to send to {addr}"
    assert port not in FORBIDDEN_PORTS, f"a test tried to send to {addr}"


class Sim:
    """A simulated monotonic clock; sleep advances it."""

    def __init__(self, t: float = 100.0) -> None:
        self.t = t

    def mono(self) -> float:
        return self.t

    def sleep(self, d: float) -> None:
        self.t += max(0.0, d)


class FakeSock:
    """Records every sendto (with the sim time); never touches the network. An inbox feeds recvfrom."""

    def __init__(self, sim: Sim | None = None, fail: bool = False) -> None:
        self.sim, self.fail = sim, fail
        self.sent: list[tuple[float, tuple[str, int], bytes]] = []
        self.inbox: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data, addr):
        _check_addr(addr)
        if self.fail:
            raise OSError(101, "Network is unreachable")
        self.sent.append((self.sim.t if self.sim else 0.0, tuple(addr), bytes(data)))
        return len(data)

    def recvfrom(self, n):
        if not self.inbox:
            raise BlockingIOError
        return self.inbox.pop(0)

    def setblocking(self, flag):
        pass

    def ops(self, op: int) -> list[tuple[float, tuple[str, int], bytes]]:
        return [s for s in self.sent if struct.unpack_from("<H", s[2], 8)[0] == op]


class GuardSock:
    """A real UDP socket on 127.0.0.1:0 whose sendto refuses anything but loopback sinks off the forbidden ports."""

    def __init__(self) -> None:
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.bind(("127.0.0.1", 0))

    def sendto(self, data, addr):
        _check_addr(addr)
        return self.s.sendto(data, addr)

    def recvfrom(self, n):
        return self.s.recvfrom(n)

    def setblocking(self, flag):
        self.s.setblocking(flag)

    def getsockname(self):
        return self.s.getsockname()

    def fileno(self):
        return self.s.fileno()

    def close(self):
        self.s.close()


def sink() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    assert s.getsockname()[1] not in FORBIDDEN_PORTS
    return s


def recv_all(s, wait: float = 0.3) -> list[bytes]:
    out = []
    s.settimeout(wait)
    while True:
        try:
            out.append(s.recv(2048))
        except socket.timeout:
            return out
        s.settimeout(0.05)


class FakeClock:
    """clock.Clock's next_beat(t, lead_s, gate): beats at anchor + k x period, gate as set."""
    RANK = {"none": 0, "dmx": 1, "beat": 2}

    def __init__(self, anchor: float, period: float, gate: str = "beat") -> None:
        self.anchor, self.period, self.gate = anchor, period, gate

    def next_beat(self, t_s, lead_s=0.0, *, gate="beat"):
        if self.RANK[self.gate] < self.RANK[gate]:
            return None
        k = math.ceil((t_s + lead_s - self.anchor) / self.period - 1e-9)
        return self.anchor + k * self.period

    def fires(self, t0, t1, lead=0.0):
        k0 = math.ceil((t0 + lead - self.anchor) / self.period)
        out = []
        while True:
            f = self.anchor + k0 * self.period - lead
            if f > t1:
                return out
            out.append(f)
            k0 += 1


class PlainClock:
    """artnet_out's BeatClock: next_beat(t) only."""

    def __init__(self, anchor: float, period: float) -> None:
        self.anchor, self.period = anchor, period

    def next_beat(self, t):
        return self.anchor + math.ceil((t - self.anchor) / self.period - 1e-9) * self.period


# -- a registry by hand (fixtures.py's RigView shape) -------------------------------------------------------------------
def ch(offset, name, attr, default=0, **kw):
    return {"offset": offset, "name": name, "attr": attr, "default": default, **kw}


PAR = {"id": "par", "source": "ofl", "kind": "lamp", "file": None, "mode": None, "footprint": 6,
       "attrs": ["intensity", "colour"], "emitters": {}, "wheel": [], "curve": "srgb", "problems": [],
       "channels": [ch(0, "Dimmer", "intensity"), ch(1, "Red", "emitter", emitter="red"),
                    ch(2, "Green", "emitter", emitter="green"), ch(3, "Blue", "emitter", emitter="blue"),
                    ch(4, "White", "emitter", emitter="white"), ch(5, "Strobe", "shutter", default=255)]}
SPOT = {**PAR, "id": "spot", "footprint": 3,
        "channels": [ch(0, "Dimmer", "intensity"), ch(1, "Colour wheel", "wheel"), ch(2, "Shutter", "shutter", 8)]}
FIVE = {**PAR, "id": "five", "footprint": 5, "channels": [ch(i, f"C{i}", "other") for i in range(5)]}


def fixture(fid, typ, address, output="dmx-a", **kw):
    return {"id": fid, "label": fid, "type": typ, "kind": "lamp", "output": output, "address": address,
            "light": None, "screen": None, "zone": None, "placement": None, "role": None, "tags": [],
            "order": None, "wb": {}, "curve": None, "pin": kw.pop("pin", {}), "house": None,
            "latency": kw.pop("latency", {"rise_ms": 0.0, "fall_ms": 0.0, "colour_ms": 0.0, "position_ms": 0.0,
                                          "min_pulse_ms": 40.0, "source": {"rise": "unset"}}), **kw}


def node_cfg(nid="n1", ip="127.0.0.1", port=40000, **kw):
    return {"id": nid, "match": kw.pop("match", {}), "last_ip": ip, "port": port, "full_512": False,
            "failsafe": None, "virtual": False, **kw}


def out_cfg(oid="dmx-a", node="n1", pa=1, enabled=True, **kw):
    return {"id": oid, "driver": "artnet", "enabled": enabled, "built": True, "node": node, "port_address": pa,
            "latency_key": kw.pop("latency_key", oid), "latency": kw.pop("latency", {}), "hits": kw.pop("hits", None),
            "min_attack_ms": None, "rate_hz": kw.pop("rate_hz", None), "settings": {}, "problems": [], **kw}


def rig_view(*, outputs, nodes, fixtures=(), types=(PAR, SPOT, FIVE)):
    return {"revision": "test", "path": "test", "error": None, "problems": [],
            "outputs": {o["id"]: o for o in outputs}, "nodes": {n["id"]: n for n in nodes},
            "types": {t["id"]: t for t in types}, "fixtures": {f["id"]: f for f in fixtures}, "groups": {}}


def make(sim, sock, tmp_path, *, out=None, nodes=None, fixtures=(), clock=None, settings=None):
    out = out or out_cfg()
    nodes = nodes if nodes is not None else [node_cfg()]
    ns = al.Nodes(settings=settings, state_file=tmp_path / "nodes.json", sock=sock, mono=sim.mono)
    d = al.ArtNetLan(out["id"], clock=clock, settings=settings, nodes=ns, mono=sim.mono, sleep=sim.sleep)
    d.configure(out, rig_view(outputs=[out], nodes=nodes, fixtures=fixtures))
    d.start()
    return d


def frame(active=True, **dmx):
    return {"output": "dmx-a", "seq": 0, "t_send_ms": 0.0, "active": active, "why": None if active else "quiet",
            "fixtures": {fid: {"intensity": 1.0, "colour": [1, 1, 1], "rgb": [1, 1, 1], "dmx": bytes(v),
                               "t_eval_ms": 0.0} for fid, v in dmx.items()}}


ACTIVE = frame(True)


def run(d, sim, until, *, active=True, fr=None, hits=(), t_last=None):
    """The runner's deadline loop on simulated time: plan, (a hit wakes it), wait, set_frame. Returns t_last."""
    hits = sorted(hits)
    t_last = sim.t if t_last is None else t_last
    while sim.t < until:
        t = d.plan(t_last, sim.t, active)
        while hits and hits[0] < t:
            sim.t = max(sim.t, hits.pop(0))
            d.on_hit()
            t = d.plan(t_last, sim.t, active)
        sim.t = max(sim.t, t)
        d.set_frame(fr(sim.t) if callable(fr) else (fr or ACTIVE))
        t_last = t
    return t_last


def parse_dmx(pkt: bytes):
    """An independent ArtDmx reader (Art-Net 4): (seq, port_address, data); asserts the fixed fields."""
    assert pkt[:8] == b"Art-Net\x00"
    assert pkt[8:10] == b"\x00\x50"                 # OpCode 0x5000, low byte first
    assert pkt[10] == 0 and pkt[11] == 14           # ProtVerHi, ProtVerLo
    assert pkt[13] == 0                             # Physical
    (length,) = struct.unpack_from(">H", pkt, 16)
    assert length % 2 == 0 and 2 <= length <= 512 and len(pkt) == 18 + length
    return pkt[12], (pkt[15] & 0x7F) << 8 | pkt[14], pkt[18:]


def dmx_times(sock: FakeSock) -> list[float]:
    return [t for t, _, _ in sock.ops(0x5000)]


# -- the wire -----------------------------------------------------------------------------------------------------------
def test_artpoll_bytes():
    assert al.build_artpoll() == b"Art-Net\x00" + b"\x00\x20" + b"\x00\x0e" + b"\x00\x00"


@pytest.mark.parametrize("mode,cmd", [("hold", 0x08), ("zero", 0x09), ("full", 0x0A), ("scene", 0x0B)])
def test_artaddress_bytes(mode, cmd):
    p = al.build_artaddress(al.AC_FAIL[mode])
    assert len(p) == 107
    assert p[:8] == b"Art-Net\x00" and p[8:10] == b"\x00\x60" and p[10:12] == b"\x00\x0e"
    assert p[12] == 0x7F                            # NetSwitch: no change
    assert p[13] == 1                               # BindIndex: the root device
    assert p[14:32] == bytes(18) and p[32:96] == bytes(64)   # names empty: ignored
    assert p[96:100] == b"\x7f" * 4 and p[100:104] == b"\x7f" * 4   # SwIn, SwOut: no change
    assert p[104] == 0x7F and p[105] == 0xFF        # SubSwitch no change, AcnPriority no change
    assert p[106] == cmd
    assert al.build_artaddress(al.AC_FAIL_RECORD)[106] == 0x0C


def test_artdmx_bytes_port_address_split_and_short_length(tmp_path):
    sim = Sim()
    for pa, lo, hi in [(1, 0x01, 0x00), (0x1234, 0x34, 0x12), (32767, 0xFF, 0x7F)]:
        sock = FakeSock(sim)
        d = make(sim, sock, tmp_path, out=out_cfg(pa=pa), fixtures=[fixture("p", "par", 1)])
        d.set_frame(frame(p=[10, 20, 30, 40, 50, 60]))
        (_, addr, pkt), = sock.sent
        assert addr == ("127.0.0.1", 40000)
        assert pkt[14] == lo and pkt[15] == hi
        seq, got_pa, data = parse_dmx(pkt)
        assert seq == 1 and got_pa == pa and data == bytes([10, 20, 30, 40, 50, 60])
        assert al.decode_artdmx(pkt)["port_address"] == pa
        sim.t += 1.0


def test_seq_runs_1_to_255_and_wraps_to_1_never_0(tmp_path):
    sim, sock = Sim(), FakeSock()
    sock.sim = sim
    d = make(sim, sock, tmp_path, fixtures=[fixture("p", "par", 1)])
    for _ in range(600):
        d.set_frame(ACTIVE)
        sim.t += 0.03
    seqs = [parse_dmx(p)[0] for _, _, p in sock.sent]
    assert 0 not in seqs
    assert seqs[:3] == [1, 2, 3] and seqs[253:258] == [254, 255, 1, 2, 3]
    assert all(b == a % 255 + 1 for a, b in zip(seqs, seqs[1:]))


def make_reply(*, ip="192.168.1.50", short="eDMX1 MAX", long="DMXking eDMX1 MAX", mac="00:1d:c1:12:34:56",
               net=0, sub=0, sw_out=(1, 0, 0, 0), num_ports=1, good_out=(0x80, 0, 0, 0), status3=None, refresh=None,
               length=239) -> bytes:
    """An ArtPollReply built from the Art-Net 4 offsets, independently of the parser."""
    b = bytearray(239)
    b[0:8] = b"Art-Net\x00"
    b[8:10] = (0x2100).to_bytes(2, "little")
    b[10:14] = bytes(int(x) for x in ip.split("."))
    b[14:16] = (0x1936).to_bytes(2, "little")
    b[18], b[19] = net, sub
    b[26:26 + len(short)] = short.encode()
    b[44:44 + len(long)] = long.encode()
    b[172:174] = num_ports.to_bytes(2, "big")
    b[182:186] = bytes(good_out)
    b[190:194] = bytes(sw_out)
    if mac:
        b[201:207] = bytes.fromhex(mac.replace(":", ""))
    if status3 is not None:
        b[217] = status3
    if refresh is not None:
        b[226:228] = refresh.to_bytes(2, "big")
    return bytes(b[:length])


def test_parse_artpollreply_fields():
    r = al.parse_artpollreply(make_reply(net=1, sub=2, sw_out=(3, 4, 0, 0), num_ports=2, good_out=(0x88, 0, 0, 0),
                                         status3=0b1110_0000, refresh=120))
    assert r["ip"] == "192.168.1.50" and r["port"] == 6454
    assert r["short_name"] == "eDMX1 MAX" and r["long_name"] == "DMXking eDMX1 MAX"
    assert r["mac"] == "00:1d:c1:12:34:56"
    assert r["port_addresses"] == [0x123, 0x124]
    assert r["failsafe_supported"] is True and r["failsafe"] == "scene"
    assert r["merging"] is True and r["merge_ltp"] is False
    assert r["refresh_hz"] == 120.0
    for bits, word in [(0b0010_0000, "hold"), (0b0110_0000, "zero"), (0b1010_0000, "full")]:
        assert al.parse_artpollreply(make_reply(status3=bits))["failsafe"] == word
    # Status3's failsafe bits mean nothing unless bit 5 says the node supports it
    r = al.parse_artpollreply(make_reply(status3=0b0100_0000, refresh=0))
    assert r["failsafe"] is None and r["failsafe_supported"] is False and r["refresh_hz"] is None
    assert al.parse_artpollreply(make_reply(refresh=44))["refresh_hz"] == 44.0
    assert al.parse_artpollreply(make_reply(good_out=(0x80, 0, 0, 0)))["merging"] is False
    # an older, shorter reply: what it carries, None past its end
    r = al.parse_artpollreply(make_reply(length=200))
    assert r["short_name"] == "eDMX1 MAX" and r["mac"] is None and r["status3"] is None and r["refresh_hz"] is None


@pytest.mark.parametrize("junk", [b"", b"Art-Net\x00", b"Art-Net\x00\x00\x21" + bytes(50), bytes(239),
                                  make_reply()[:107], b"Art-Net\x00\x00\x50" + make_reply()[10:],
                                  b"Not-Art\x00" + make_reply()[8:], None, 12345])
def test_parse_artpollreply_junk_is_none(junk):
    assert al.parse_artpollreply(junk) is None


def test_norm_mac_and_addresses():
    assert al.norm_mac("00-1D-C1-12-34-56") == al.norm_mac("001dc1123456") == "00:1d:c1:12:34:56"
    assert al.norm_mac("00:1d:c1:12:34") is None and al.norm_mac("zz:1d:c1:12:34:56") is None
    assert al.unicast_ip("192.168.1.50") == "192.168.1.50"
    for bad in ("255.255.255.255", "0.0.0.0", "239.1.2.3", "node.local", "", None):
        assert al.unicast_ip(bad) is None
    assert al.is_room_receiver("127.0.0.1", 6454) and al.is_room_receiver("127.9.9.9", 6454)
    assert al.is_room_receiver("localhost", 6454)
    assert not al.is_room_receiver("127.0.0.1", 6455) and not al.is_room_receiver("192.168.1.50", 6454)


# -- the frame grid -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("lead_ms", [0.0, 37.0])
@pytest.mark.parametrize("bpm", [60, 90, 120, 128, 140, 174, 180])
def test_grid_frames_leave_exactly_at_beat_minus_lead(bpm, lead_ms, tmp_path):
    sim, period = Sim(100.0), 60.0 / bpm
    sock = FakeSock(sim)
    clock = FakeClock(anchor=100.0123, period=period)
    d = make(sim, sock, tmp_path, clock=clock, out=out_cfg(latency={"intensity": {"rise_ms": lead_ms}}),
             fixtures=[fixture("p", "par", 1)])
    run(d, sim, 108.0)
    ts = dmx_times(sock)
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    assert min(gaps) >= MIN_GAP_S - 1e-9                              # Art-Net's 44 Hz, never under
    tick = grid_tick(period)
    m = round(period / tick)
    assert m % 4 == 0 and m / period <= 43.0 + 1e-9                    # 16ths ride the grid, <= 43 Hz
    fires = clock.fires(101.0, 107.0, lead_ms / 1000.0)
    assert len(fires) >= 5
    for f in fires:
        assert min(abs(t - f) for t in ts) < 1e-9, f"no frame at t_beat - L = {f}"
    for f0, f1 in zip(fires, fires[1:]):
        inside = [t for t in ts if f0 - 1e-9 <= t < f1 - 1e-9]
        assert len(inside) == m
        assert max(abs((b - a) - tick) for a, b in zip(inside, inside[1:])) < 1e-9
    st = d.status()
    assert st["extra"]["lead_ms"] == lead_ms and st["extra"]["period_ms"] == pytest.approx(period * 1000, abs=0.1)
    assert st["extra"]["lead_from"] == "fixtures.yaml"                 # hand-set (0 counts as set)


def test_grid_with_artnet_outs_plain_clock_matches(tmp_path):
    sim, period = Sim(100.0), 0.5
    sock = FakeSock(sim)
    d = make(sim, sock, tmp_path, clock=PlainClock(100.0, period), out=out_cfg(latency={"intensity": {"rise_ms": 20}}))
    run(d, sim, 104.0)
    ts = dmx_times(sock)
    for f in FakeClock(100.0, period).fires(101.0, 103.5, 0.02):
        assert min(abs(t - f) for t in ts) < 1e-9


def test_lead_from_latency_json_through_the_registry(tmp_path):
    sim = Sim()
    own = fixture("a", "par", 1, latency={"rise_ms": 90.0, "source": {"rise": "latency.json outputs.a.intensity"}})
    via_output = fixture("b", "par", 7, latency={"rise_ms": 55.0,
                                                 "source": {"rise": "latency.json outputs.dmx-a.intensity"}})
    d = make(sim, FakeSock(sim), tmp_path, fixtures=[own, via_output],
             out=out_cfg(latency={"intensity": {"rise_ms": 10}}))
    st = d.status()["extra"]
    assert st["lead_ms"] == 55.0 and st["lead_from"] == "latency.json outputs.dmx-a.intensity"
    d = make(sim, FakeSock(sim), tmp_path, fixtures=[own])
    assert d.status()["extra"]["lead_ms"] == 0.0 and d.status()["extra"]["lead_from"] == "unset"


def test_rephase_into_the_gap_goes_one_gap_later_never_early(tmp_path):
    sim, period = Sim(100.0), 0.5
    sock = FakeSock(sim)
    clock = FakeClock(anchor=100.0, period=period)
    d = make(sim, sock, tmp_path, clock=clock)
    tick = grid_tick(period)
    beat = 102.0
    t_last = run(d, sim, beat - 1.5 * tick)
    last = sim.t
    assert last == pytest.approx(beat - tick, abs=1e-9)                # the frame before the beat
    clock.anchor -= 0.015                                              # the beat moves into the gap
    new_fire = beat - 0.015
    t = d.plan(t_last, sim.t, True)
    assert t == pytest.approx(last + MIN_GAP_S, abs=1e-12)              # one gap after the last frame
    assert t > new_fire and t - new_fire < MIN_GAP_S                    # late by under a frame, never early
    run(d, sim, 106.0, t_last=t_last)
    ts = dmx_times(sock)
    assert min(b - a for a, b in zip(ts, ts[1:])) >= MIN_GAP_S - 1e-9
    for f in clock.fires(103.0, 105.5):
        assert min(abs(x - f) for x in ts) < 1e-9                       # the grid follows the new phase


def test_rephase_later_lands_exactly_and_a_carried_beat_is_not_taken_twice(tmp_path):
    sim, period = Sim(100.0), 0.5
    sock = FakeSock(sim)
    clock = FakeClock(anchor=100.0, period=period)
    d = make(sim, sock, tmp_path, clock=clock)
    tick = grid_tick(period)
    t_last = run(d, sim, 102.0 - 1.5 * tick)
    clock.anchor += 0.007                                               # the next beat 7 ms later
    t_last = run(d, sim, 102.007, t_last=t_last)
    assert dmx_times(sock)[-1] == pytest.approx(102.007, abs=1e-9)      # exactly on the re-phased beat
    # right after that beat frame the clock nudges the same beat 10 ms later: it was carried, so it isn't taken
    # again (artnet_out's REPHASE_SKIP); the grid just follows the new phase
    clock.anchor += 0.010
    earliest = 102.007 + MIN_GAP_S
    t = d.plan(t_last, sim.t, True)
    assert t != pytest.approx(earliest)
    assert t == pytest.approx(102.517 - 19 * tick, abs=1e-9)
    run(d, sim, 104.0, t_last=t_last)
    for f in clock.fires(102.5, 103.6):
        assert min(abs(x - f) for x in dmx_times(sock)) < 1e-9


def test_free_run_at_43_hz_without_a_beat(tmp_path):
    for clock in (None, FakeClock(100.0, 0.5, gate="none")):
        sim = Sim(100.0)
        sock = FakeSock(sim)
        d = make(sim, sock, tmp_path, clock=clock)
        run(d, sim, 102.0)
        gaps = [b - a for a, b in zip(dmx_times(sock), dmx_times(sock)[1:])]
        assert all(g == pytest.approx(1.0 / 43.0, abs=1e-9) for g in gaps[1:])
    # gate dmx: §5.4's middle confidence keeps DMX on the beat
    sim = Sim(100.0)
    sock = FakeSock(sim)
    d = make(sim, sock, tmp_path, clock=FakeClock(100.0, 0.5, gate="dmx"))
    run(d, sim, 102.0)
    assert min(abs(t - 101.5) for t in dmx_times(sock)) < 1e-9


def test_a_hit_pulls_the_next_frame_forward_never_under_the_gap(tmp_path):
    sim, period = Sim(100.0), 0.5
    sock = FakeSock(sim)
    clock = FakeClock(100.0, period)
    d = make(sim, sock, tmp_path, clock=clock)
    t_last = run(d, sim, 101.1)
    s = sim.t
    # a hit 5 ms after the last frame: the next frame goes one gap after it
    t_last = run(d, sim, s + 0.001, hits=[s + 0.005], t_last=t_last)
    assert dmx_times(sock)[-1] == pytest.approx(s + MIN_GAP_S, abs=1e-12)
    # a hit later than a gap: at once
    s2 = sim.t
    t_last = run(d, sim, s2 + 0.001, hits=[s2 + 0.0235], t_last=t_last)
    assert dmx_times(sock)[-1] == pytest.approx(s2 + 0.0235, abs=1e-12)
    run(d, sim, 104.0, hits=[102.21, 102.215, 102.6, 103.4999], t_last=t_last)
    ts = dmx_times(sock)
    assert min(b - a for a, b in zip(ts, ts[1:])) >= MIN_GAP_S - 1e-9
    for f in (103.0, 103.5):                                            # the grid keeps its phase
        assert min(abs(t - f) for t in ts if t >= f - 1e-9) < MIN_GAP_S
    assert min(abs(t - 103.0) for t in ts) < 1e-9
    # plan_hit, for a runner that plans the hit itself
    last = dmx_times(sock)[-1]
    assert d.plan_hit(last, last + 0.002) == pytest.approx(last + MIN_GAP_S)


def test_a_hit_just_before_a_beat_rides_the_beat_frame(tmp_path):
    """c8's ruling (16 Oct): a pull that would push the next beat frame late waits for the beat instead, so dense
    hits never take the beat frame off t_beat - L."""
    sim, period = Sim(100.0), 0.5
    sock = FakeSock(sim)
    d = make(sim, sock, tmp_path, clock=FakeClock(100.0, period))
    t_last = run(d, sim, 101.2)
    # a hit every 7 ms through the next beat: every frame is pulled, but the beat frame still leaves on the beat
    hits = [101.2 + 0.007 * k for k in range(1, 60)]
    run(d, sim, 101.62, hits=hits, t_last=t_last)
    ts = dmx_times(sock)
    assert min(abs(t - 101.5) for t in ts) < 1e-9
    assert min(b - a for a, b in zip(ts, ts[1:])) >= MIN_GAP_S - 1e-9
    # a hit well before the beat still goes at once
    s = dmx_times(sock)[-1]
    d.on_hit()
    assert d.plan(s, s + 0.030, True) == pytest.approx(s + 0.030)


def test_no_pull_while_idle_or_on_an_output_without_hits(tmp_path):
    sim = Sim(100.0)
    d = make(sim, FakeSock(sim), tmp_path, clock=FakeClock(100.0, 0.5), out=out_cfg(hits=False))
    run(d, sim, 100.5)
    assert d.caps["hits"] is False
    d.on_hit()
    assert d.plan(sim.t, sim.t + 0.001, True) != pytest.approx(sim.t + MIN_GAP_S)
    d = make(sim, FakeSock(sim), tmp_path, clock=FakeClock(100.0, 0.5))
    run(d, sim, 101.0)
    d.on_hit()
    assert d.plan(sim.t, sim.t, False) == pytest.approx(sim.t + 0.1)  # idle: the next look check


def test_set_frame_never_sends_inside_the_gap(tmp_path):
    sim = Sim(100.0)
    sock = FakeSock(sim)
    d = make(sim, sock, tmp_path)
    d.set_frame(ACTIVE)
    d.set_frame(ACTIVE)                                                 # at once: held back
    assert len(sock.sent) == 1 and d.too_soon == 1
    assert d.plan(sim.t, sim.t, True) == pytest.approx(100.0 + MIN_GAP_S)   # and sent as soon as allowed
    sim.t = 100.0 + MIN_GAP_S - 0.0004                                  # 0.4 ms short: waits it out
    d.set_frame(ACTIVE)
    assert len(sock.sent) == 2 and sock.sent[1][0] == pytest.approx(100.0 + MIN_GAP_S)


def test_a_rate_cap_slows_the_grid(tmp_path):
    sim = Sim(100.0)
    sock = FakeSock(sim)
    d = make(sim, sock, tmp_path, clock=FakeClock(100.0, 0.5), out=out_cfg(rate_hz=30))
    run(d, sim, 103.0)
    ts = dmx_times(sock)
    assert min(b - a for a, b in zip(ts, ts[1:])) >= 1 / 30 - 1e-9
    assert min(abs(t - 102.0) for t in ts) < 1e-9
    assert d.caps["rate_hz"] == 30


def run_late(d, sim, until, late_s, *, t_last=None):
    """The runner's loop with every frame leaving ``late_s`` after its plan (sleep overshoot, a GIL hand-over)."""
    t_last = sim.t if t_last is None else t_last
    while sim.t < until:
        t = d.plan(t_last, sim.t, True)
        sim.t = max(sim.t, t) + late_s
        d.set_frame(ACTIVE)
        t_last = t
    return t_last


@pytest.mark.parametrize("late_ms,min_hz", [(0.3, 42.0), (0.8, 36.0)])
def test_a_little_lateness_doesnt_skip_grid_ticks(late_ms, min_hz, tmp_path):
    """README S5.14: at 128 bpm the tick (23.44 ms) is 0.74 ms over the gap, so a frame that left 0.8 ms late used to
    push the next 16th a whole tick on (30.6 Hz). A grid point up to SPACING_WAIT_S inside the gap now goes at the
    gap's end: the rate holds, every beat is still carried, and no gap is ever under 22.7 ms."""
    sim, period = Sim(100.0), 60.0 / 128.0
    sock = FakeSock(sim)
    clock = FakeClock(anchor=100.0123, period=period)
    d = make(sim, sock, tmp_path, clock=clock, fixtures=[fixture("p", "par", 1)])
    run_late(d, sim, 110.0, late_ms / 1000.0)
    ts = [t for t in dmx_times(sock) if t > 101.0]
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    assert min(gaps) >= MIN_GAP_S - 1e-9
    hz = (len(ts) - 1) / (ts[-1] - ts[0])
    assert hz >= min_hz, hz
    for f in clock.fires(101.5, 109.0):                          # every beat carried, never early, late by at most
        late = min(t - f for t in ts if t >= f - 1e-9)           # its own and the frame before's lateness plus the
        assert late <= 2 * late_ms / 1000.0 + al.SPACING_WAIT_S + 1e-6, (f, late)   # tolerance


def test_a_rate_cap_keeps_the_grids_slack(tmp_path):
    """README S5.14: rate_hz 40 at 120 bpm used to tick at exactly the gap (25 ms): any lateness pushed every frame
    two ticks on (20.1 Hz). The cap now keeps GRID_HZ's own slack over its gap."""
    sim = Sim(100.0)
    sock = FakeSock(sim)
    clock = FakeClock(anchor=100.0, period=0.5)
    d = make(sim, sock, tmp_path, clock=clock, out=out_cfg(rate_hz=40))
    run_late(d, sim, 106.0, 0.00005)
    ts = [t for t in dmx_times(sock) if t > 101.0]
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    assert min(gaps) >= 1 / 40 - 1e-9
    hz = (len(ts) - 1) / (ts[-1] - ts[0])
    assert hz > 30.0, hz
    tick = d.status()["extra"]["period_ms"] / 1000.0 / round(d.status()["extra"]["period_ms"] / 1000.0 /
                                                            grid_tick(0.5, d._u.max_hz))
    assert tick >= 1 / 40 + al.GRID_SLACK_S - 1e-9
    for f in clock.fires(101.5, 105.5):
        assert min(abs(t - f) for t in ts) < 0.001


# -- the universe -------------------------------------------------------------------------------------------------------
def test_short_frames_up_to_the_highest_patched_channel_even(tmp_path):
    sim = Sim()
    cases = [([fixture("p", "par", 1)], 6), ([fixture("p", "par", 1), fixture("s", "spot", 10)], 12),
             ([fixture("f", "five", 1)], 6), ([fixture("f", "five", 2)], 6), ([], 2)]
    for fixtures, length in cases:
        sock = FakeSock(sim)
        d = make(sim, sock, tmp_path, fixtures=fixtures)
        d.set_frame(ACTIVE)
        assert len(parse_dmx(sock.sent[-1][2])[2]) == length
        sim.t += 1
    sock = FakeSock(sim)
    d = make(sim, sock, tmp_path, fixtures=[fixture("p", "par", 1)], nodes=[node_cfg(full_512=True)])
    d.set_frame(ACTIVE)
    assert len(parse_dmx(sock.sent[-1][2])[2]) == 512


def test_universe_from_defaults_pins_and_frames(tmp_path):
    sim = Sim()
    sock = FakeSock(sim)
    fx = [fixture("p", "par", 1, pin={"White": 77}), fixture("s", "spot", 10, pin={"Colour wheel": 33}),
          fixture("q", "par", 20)]
    d = make(sim, sock, tmp_path, fixtures=fx)
    d.set_frame(frame(p=[200, 1, 2, 3, 4, 5]))                          # s and q not in the frame
    data = parse_dmx(sock.sent[-1][2])[2]
    assert len(data) == 26
    assert list(data[0:6]) == [200, 1, 2, 3, 4, 5]                      # p: the frame's bytes
    assert list(data[6:9]) == [0, 0, 0]                                 # uncovered
    assert list(data[9:12]) == [0, 33, 8]                               # s: defaults, its pin, the shutter's Open
    assert list(data[19:25]) == [0, 0, 0, 0, 0, 255]                    # q: defaults (strobe open)
    assert d.status()["extra"]["problems"] == []
    # a pin that isn't a channel, and a fixture that runs past 512: problem lines, the rest works
    d = make(sim, FakeSock(sim), tmp_path, fixtures=[fixture("p", "par", 1, pin={"Nope": 1}),
                                                       fixture("x", "par", 510)])
    probs = d.status()["extra"]["problems"]
    assert any("Nope" in p for p in probs) and any("fixtures.x" in p and "left out" in p for p in probs)


def test_keepalive_when_idle_and_change_at_once(tmp_path):
    sim = Sim(100.0)
    sock = FakeSock(sim)
    d = make(sim, sock, tmp_path, fixtures=[fixture("p", "par", 1)], settings={"idle_eval_hz": 10})
    base = [0, 0, 0, 0, 0, 255]

    def fr(t):
        return frame(False, p=[9, 0, 0, 0, 0, 255] if 101.55 <= t < 101.85 else base)
    run(d, sim, 103.05, active=False, fr=fr)
    ts = dmx_times(sock)
    assert ts[0] == pytest.approx(100.1)                                # the first look check
    assert any(abs(t - 101.1) < 1e-6 for t in ts)                       # keepalive after 1 s unchanged
    assert any(abs(t - 101.6) < 1e-6 for t in ts)                       # the change: at the next look check
    assert any(abs(t - 101.9) < 1e-6 for t in ts)                       # and back
    assert any(abs(t - 102.9) < 1e-6 for t in ts)
    assert len(ts) == 5
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    assert max(gaps) <= 1.0 + 1e-6
    assert d.keepalives >= 2
    # active: every planned frame goes, changed or not
    sock.sent.clear()
    run(d, sim, sim.t + 1.0)                                            # no clock: free-run at 43 Hz
    assert len(dmx_times(sock)) >= 43


def test_a_stale_t_last_never_spins_the_idle_runner(tmp_path):
    sim = Sim(100.0)
    d = make(sim, FakeSock(sim), tmp_path)
    for i in range(20):
        t = d.plan(0.0, sim.t, False)                                   # a runner passing an old t_last
        assert t >= sim.t + 0.1 - 1e-9 if i else t >= sim.t
        sim.t = t
        d.set_frame(frame(False))


# -- state, no_config, errors -------------------------------------------------------------------------------------------
def test_status_shape_and_states(tmp_path):
    sim = Sim()
    sock = FakeSock(sim)
    d = make(sim, sock, tmp_path)
    st = d.status()
    assert set(st) == {"id", "driver", "enabled", "state", "detail", "frames", "rate_hz", "late_ms", "errors",
                       "last_frame_age_s", "extra"}
    assert st["state"] == "idle" and st["driver"] == "artnet"
    run(d, sim, sim.t + 1.0)
    st = d.status()
    assert st["state"] == "sending" and st["rate_hz"] == pytest.approx(43.0, abs=0.5)
    assert st["late_ms"]["p95"] == pytest.approx(0.0, abs=1e-6)
    assert st["extra"]["node"]["id"] == "n1"
    for out, words in [(out_cfg(pa=0), "port_address"), (out_cfg(pa=None), "port_address"),
                       (out_cfg(node=None), "no node"), (out_cfg(node="ghost"), "ghost")]:
        s2 = FakeSock(sim)
        d = make(sim, s2, tmp_path, out=out)
        d.set_frame(ACTIVE)
        assert s2.sent == [] and d.status()["state"] == "no_config" and words in d.status()["detail"]
    s3 = FakeSock(sim)
    d = make(sim, s3, tmp_path, out=out_cfg(enabled=False))
    d.set_frame(ACTIVE)
    assert s3.sent == [] and d.status()["state"] == "off"
    d = make(sim, FakeSock(sim), tmp_path, nodes=[node_cfg(ip=None)])
    assert d.status()["state"] == "no_config" and "last_ip" in d.status()["detail"]


def test_send_errors_counted_error_state_and_frames_keep_trying(tmp_path):
    sim = Sim()
    sock = FakeSock(sim, fail=True)
    d = make(sim, sock, tmp_path)
    for _ in range(4):
        d.set_frame(ACTIVE)
        sim.t += 0.05
    st = d.status()
    assert st["state"] == "error" and st["errors"] == 4 and "unreachable" in st["detail"]
    sock.fail = False
    d.set_frame(ACTIVE)
    assert d.status()["state"] == "sending" and len(sock.sent) == 1


def test_stop_sends_nothing_more(tmp_path):
    sim = Sim()
    sock = FakeSock(sim)
    d = make(sim, sock, tmp_path)
    d.set_frame(ACTIVE)
    d.stop()
    sim.t += 1
    d.set_frame(ACTIVE)
    assert len(sock.sent) == 1


# -- discovery and health -----------------------------------------------------------------------------------------------
def test_polls_every_3_s_only_for_used_nodes_and_replies_through_our_socket(tmp_path):
    a, b = sink(), sink()
    ours = GuardSock()
    try:
        sim = Sim(0.0)
        mac = "00:1d:c1:12:34:56"
        ns = al.Nodes(settings={}, state_file=tmp_path / "nodes.json", sock=ours, mono=sim.mono)
        ns.configure({"n1": node_cfg("n1", port=a.getsockname()[1], match={"mac": mac.upper()}),
                      "n2": node_cfg("n2", port=b.getsockname()[1])}, used={"n1"})
        assert ns.status("n1")["detail"] == al.NO_REPLIES or ns.status("n1")["replies"] == 0
        polls = []
        for t in range(0, 10):
            ns.tick(float(t))
            polls += [(t, p) for p in recv_all(a, 0.05)]
        assert [t for t, _ in polls] == [0, 3, 6, 9]
        assert all(p == al.build_artpoll() for _, p in polls)
        assert recv_all(b, 0.05) == []                                  # n2: no enabled output uses it
        assert ns.status("n2")["detail"].startswith("not polled")
        assert ns.status("n1")["detail"] == al.NO_REPLIES
        # the node answers to our source port (as some do through WSL's NAT)
        a.sendto(make_reply(ip="127.0.0.1", mac=mac, status3=0b1110_0000, refresh=44, good_out=(0x88, 0, 0, 0)),
                 ours.getsockname())
        select.select([ours], [], [], 1.0)
        sim.t = 10.0
        ns.tick(10.0)
        st = ns.status("n1")
        assert st["matched"] is True and st["replies"] == 1 and st["mac"] == mac
        assert st["short_name"] == "eDMX1 MAX" and st["failsafe"] == "scene" and st["merging"] is True
        assert st["refresh_hz"] == 44.0 and st["ip_from"] == "config" and st["detail"] is None
        sim.t = 10.5
        assert ns.status("n1")["reply_age_s"] == pytest.approx(0.5)
        sim.t = 25.0
        assert ns.status("n1")["detail"].startswith("silent")
    finally:
        for s in (a, b, ours):
            s.close()


def test_match_by_short_name_and_mismatch_keeps_sending(tmp_path):
    sim = Sim(0.0)
    sock = FakeSock(sim)
    nodes = [node_cfg("n1", match={"short_name": "edmx1 max"})]
    d = make(sim, sock, tmp_path, nodes=nodes)
    ns = d.nodes
    ns.on_reply(make_reply(ip="127.0.0.1", short="eDMX1 MAX"), ("127.0.0.1", 6454))
    assert ns.status("n1")["matched"] is True
    ns.on_reply(make_reply(ip="127.0.0.1", short="Other node", mac="aa:bb:cc:dd:ee:ff"), ("127.0.0.1", 6454))
    st = ns.status("n1")
    assert st["matched"] is False and "frames keep going" in st["detail"] and "Other node" in st["detail"]
    d.set_frame(ACTIVE)
    assert sock.ops(0x5000)[-1][1] == ("127.0.0.1", 40000)            # no stop gate
    ns.on_reply(make_reply(ip="127.0.0.9", short="Stranger"), ("127.0.0.9", 6454))
    assert ns.unmatched == 1
    ns.on_reply(b"rubbish", ("127.0.0.1", 1))
    assert ns.junk == 1


def test_a_moved_node_is_followed_and_kept_in_nodes_json(tmp_path):
    sim = Sim(0.0)
    sock = FakeSock(sim)
    mac = "00:1d:c1:12:34:56"
    cfg = {"n1": node_cfg("n1", port=40001, match={"mac": mac})}
    state = tmp_path / "nodes.json"
    ns = al.Nodes(settings={}, state_file=state, sock=sock, mono=sim.mono)
    ns.configure(cfg, {"n1"})
    ns.on_reply(make_reply(ip="127.0.0.2", mac=mac), ("127.0.0.2", 6454))
    assert ns.dest("n1") == ("127.0.0.2", 40001)
    st = ns.status("n1")
    assert st["ip_from"] == "discovery" and st["detail"].startswith("moved: update last_ip")
    saved = json.loads(state.read_text())
    assert saved["nodes"]["n1"]["ip"] == "127.0.0.2" and saved["nodes"]["n1"]["config_ip"] == "127.0.0.1"
    assert not list(tmp_path.glob(".nodes.json.*"))                     # written atomically, no tmp left
    # a restart reads it back
    ns2 = al.Nodes(settings={}, state_file=state, sock=sock, mono=sim.mono)
    ns2.configure(cfg, {"n1"})
    assert ns2.dest("n1") == ("127.0.0.2", 40001)
    # the owner edits last_ip: config wins
    ns2.configure({"n1": node_cfg("n1", ip="127.0.0.3", port=40001, match={"mac": mac})}, {"n1"})
    assert ns2.dest("n1") == ("127.0.0.3", 40001) and ns2.status("n1")["ip_from"] == "config"
    # the node back at its last_ip: the discovery is forgotten
    ns.on_reply(make_reply(ip="127.0.0.1", mac=mac), ("127.0.0.1", 6454))
    assert ns.dest("n1") == ("127.0.0.1", 40001) and "n1" not in json.loads(state.read_text())["nodes"]


def test_reply_bind_socket_is_drained_too(tmp_path, monkeypatch):
    bound = []

    def bind_local(host, port):
        assert host == "127.0.0.1" and port == 0                       # ephemeral only, never a real port
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((host, port))
        s.setblocking(False)
        bound.append(s)
        return s
    monkeypatch.setattr(al, "_bound_socket", bind_local)
    sim = Sim(0.0)
    ns = al.Nodes(settings={"reply_bind": "127.0.0.1:0"}, state_file=tmp_path / "n.json", sock=FakeSock(sim),
                  mono=sim.mono)
    ns.configure({"n1": node_cfg("n1", port=40002, match={"short_name": "eDMX1 MAX"})}, {"n1"})
    ns.tick(0.0)
    assert len(bound) == 1 and ns.snapshot()["reply_bind"] == "127.0.0.1:0"
    relay = sink()
    try:
        relay.sendto(make_reply(ip="127.0.0.1"), bound[0].getsockname())
        select.select([bound[0]], [], [], 1.0)
        ns.tick(1.0)
        assert ns.status("n1")["matched"] is True
    finally:
        relay.close()
        ns.close()
    assert al.parse_settings({"reply_bind": "0.0.0.0:6454"}).reply_bind is None
    assert al.parse_settings({"reply_bind": "pc22:7000"}).problems


# -- failsafe -----------------------------------------------------------------------------------------------------------
def test_failsafe_artaddress_once_per_node_and_setting(tmp_path):
    sim = Sim(0.0)
    sock = FakeSock(sim)
    ns = al.Nodes(settings={}, state_file=tmp_path / "n.json", sock=sock, mono=sim.mono)
    cfg = {"n1": node_cfg("n1", failsafe="scene"), "n2": node_cfg("n2", port=40003, failsafe="zero")}
    ns.configure(cfg, {"n1"})
    for t in range(6):
        ns.tick(float(t))
    addr = sock.ops(0x6000)
    assert [(a, p[106]) for _, a, p in addr] == [(("127.0.0.1", 40000), 0x0B)]   # n2 isn't used: nothing
    ns.configure({**cfg, "n1": node_cfg("n1", failsafe="zero")}, {"n1"})
    ns.tick(6.0)
    ns.tick(7.0)
    assert [p[106] for _, _, p in sock.ops(0x6000)] == [0x0B, 0x09]
    ns.configure(cfg, set())                                            # no longer used, then used again: again
    ns.configure(cfg, {"n1"})
    ns.tick(8.0)
    assert [p[106] for _, _, p in sock.ops(0x6000)] == [0x0B, 0x09, 0x0B]
    ns.configure({"n1": node_cfg("n1")}, {"n1"})                        # unset: the node's own
    ns.tick(9.0)
    assert len(sock.ops(0x6000)) == 3


def test_record_failsafe_resends_the_universe_then_records(tmp_path):
    sim = Sim(0.0)
    sock = FakeSock(sim)
    d = make(sim, sock, tmp_path, fixtures=[fixture("p", "par", 1)])
    d.set_frame(frame(p=[1, 2, 3, 4, 5, 6]))
    last = d.last_packet()
    sock.sent.clear()
    r = d.nodes.record_failsafe("n1")
    assert r["recorded"] is True and r["why"] is None
    assert [p for _, _, p in sock.sent] == [last, al.build_artaddress(al.AC_FAIL_RECORD)]
    assert d.record_failsafe()["recorded"] is True
    assert d.nodes.record_failsafe("ghost") == {"recorded": False, "why": "unknown node ghost"}
    v = make(sim, FakeSock(sim), tmp_path, nodes=[node_cfg(virtual=True)])
    assert v.nodes.record_failsafe("n1")["recorded"] is False


# -- never room's receiver; the virtual node ----------------------------------------------------------------------------
def test_loopback_6454_is_never_sent_to(tmp_path):
    sim = Sim(0.0)
    sock = FakeSock(sim)
    room = node_cfg("n1", ip="127.0.0.1", port=6454, failsafe="scene")
    d = make(sim, sock, tmp_path, nodes=[room], fixtures=[fixture("p", "par", 1)])
    d.set_frame(frame(p=[9] * 6))
    for t in range(5):
        d.tick(float(t))
    assert sock.sent == []                                              # no frame, poll or ArtAddress
    st = d.nodes.status("n1")
    assert st["ip_from"] == "virtual" and "room_service's own receiver" in st["detail"]
    assert d.nodes.kept("n1", 1) is not None                            # treated as virtual: kept
    assert d.nodes._sendto("n1", b"x", ("127.0.0.1", 6454)) is False and sock.sent == []
    # a reply that would move a LAN node onto loopback:6454 is not followed
    ns = al.Nodes(settings={}, state_file=tmp_path / "n.json", sock=sock, mono=sim.mono)
    ns.configure({"n1": node_cfg("n1", ip="192.0.2.10", port=6454, match={"short_name": "eDMX1 MAX"})}, {"n1"})
    ns.on_reply(make_reply(ip="127.0.0.1"), ("127.0.0.1", 6454))
    assert ns.dest("n1") == ("192.0.2.10", 6454) and "ignored" in ns.status("n1")["detail"]


def test_virtual_node_keeps_frames_and_decodes_them(tmp_path):
    sim = Sim(0.0)
    sock = FakeSock(sim)
    d = make(sim, sock, tmp_path, nodes=[node_cfg(ip=None, virtual=True)], out=out_cfg(pa=7),
             fixtures=[fixture("p", "par", 1)])
    for i in range(3):
        d.set_frame(frame(p=[i, 2, 3, 4, 5, 6]))
        sim.t += 0.05
    for t in range(4):
        d.tick(float(t))
    assert sock.sent == []
    view = d.nodes.dmx_view()["n1"]["7"]
    assert view["seq"] == 3 and view["length"] == 6 and view["channels"] == [2, 2, 3, 4, 5, 6]
    u = d.universe_view()
    assert u["virtual"] is True and u["to"] is None and u["channels"] == [2, 2, 3, 4, 5, 6] and u["port_address"] == 7
    st = d.status()
    assert st["state"] == "sending" and st["frames"] == 3 and st["extra"]["node"]["ip_from"] == "virtual"


# -- the real path, end to end on loopback ------------------------------------------------------------------------------
def test_real_socket_frames_reach_an_ephemeral_sink(tmp_path):
    a = sink()
    ours = GuardSock()
    try:
        sim = Sim(0.0)
        d = make(sim, ours, tmp_path, nodes=[node_cfg(port=a.getsockname()[1])], out=out_cfg(pa=0x0102),
                 fixtures=[fixture("p", "par", 1)])
        d.set_frame(frame(p=[255, 128, 64, 32, 16, 8]))
        (pkt,) = recv_all(a)
        assert parse_dmx(pkt) == (1, 0x0102, bytes([255, 128, 64, 32, 16, 8]))
    finally:
        a.close()
        ours.close()


# -- costs (printed with -s) --------------------------------------------------------------------------------------------
def test_budget_set_frame_a_full_universe(tmp_path):
    sim = Sim(0.0)
    t16 = {**PAR, "id": "p16", "footprint": 16, "channels": [ch(i, f"C{i}", "other", i) for i in range(16)]}
    fx = [fixture(f"f{i}", "p16", 1 + 16 * i) for i in range(32)]
    ns = al.Nodes(settings={}, state_file=tmp_path / "n.json", sock=FakeSock(sim), mono=time.monotonic)
    out = out_cfg()
    d = al.ArtNetLan("dmx-a", nodes=ns, mono=time.monotonic)
    d.configure(out, rig_view(outputs=[out], nodes=[node_cfg(virtual=True)], fixtures=fx, types=(t16,)))
    d.start()
    fr = frame(**{f"f{i}": bytes((i + j) % 256 for j in range(16)) for i in range(32)})
    us = []
    for _ in range(2000):
        d._sent_at = None                                              # measure the build and send, not the gap
        t0 = time.perf_counter()
        d.set_frame(fr)
        us.append((time.perf_counter() - t0) * 1e6)
    us.sort()
    p95 = us[int(0.95 * len(us))]
    print(f"\nartnet_lan set_frame, 32 fixtures x 16 ch (512): p50 {statistics.median(us):.1f} us, "
          f"p95 {p95:.1f} us (budget 100 us)")
    assert p95 < 1000.0
    plan_us = []
    clock = FakeClock(0.0, 0.5)
    d.clock, d._clock_kw = clock, True
    t = 10.0
    for _ in range(2000):
        t0 = time.perf_counter()
        t = d.plan(t, t, True)
        plan_us.append((time.perf_counter() - t0) * 1e6)
    plan_us.sort()
    print(f"artnet_lan plan (beat grid): p95 {plan_us[int(0.95 * len(plan_us))]:.1f} us")


# -- finding boxes on the LAN (GET /api/v1/rig/nodes/find) -------------------------------------------------------------
def _fake_box(reply: bytes):
    """A box on loopback that answers each ArtPoll with ``reply`` (to the poll's source address)."""
    import threading
    box = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    box.bind(("127.0.0.1", 0))
    box.settimeout(0.1)
    stop = threading.Event()
    polls: list[bytes] = []

    def serve():
        while not stop.is_set():
            try:
                data, addr = box.recvfrom(2048)
            except (socket.timeout, OSError):
                continue
            polls.append(data)
            box.sendto(reply, addr)
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return box, stop, t, polls


def _real_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.setblocking(False)
    return s


def test_find_nodes_lists_the_box_that_answers(monkeypatch):
    import ipaddress
    monkeypatch.setattr(al, "_heard", {})
    box, stop, t, polls = _fake_box(make_reply(ip="192.168.1.50"))
    try:
        got = al.find_nodes(subnets=[ipaddress.IPv4Network("127.0.0.0/30")], wait_s=0.3, port=box.getsockname()[1],
                            sock_factory=_real_socket,
                            nodes={"node-1": {"match": {"mac": "00:1d:c1:12:34:56"}, "last_ip": "192.168.1.9"}})
    finally:
        stop.set()
        t.join(1)
        box.close()
    assert polls and polls[0] == al.ARTPOLL
    assert got["searched"] == ["127.0.0.0/30"] and got["polled"] >= 2 and got["words"] is None
    [row] = got["nodes"]
    assert row["ip"] == "192.168.1.50" and row["name"] == "eDMX1 MAX" and row["heard"] == "now"
    assert row["label"] == "eDMX1 MAX (192.168.1.50)" and row["node"] == "node-1"
    assert row["mac"] == "00:1d:c1:12:34:56"


def test_find_nodes_nothing_answers_and_never_polls_rooms_receiver(monkeypatch):
    import ipaddress
    monkeypatch.setattr(al, "_heard", {})
    sent: list[tuple] = []

    class Sock:
        def setsockopt(self, *a):
            pass

        def sendto(self, data, addr):
            sent.append(addr)

        def fileno(self):
            return -1

        def close(self):
            pass
    got = al.find_nodes(subnets=[ipaddress.IPv4Network("127.0.0.0/30")], wait_s=0.2, sock_factory=Sock)
    assert sent == [] and got["nodes"] == []                  # 127.x on udp 6454 is room's receiver: never sent to
    assert got["words"] == al.FIND_NONE


def test_find_nodes_lists_what_nodes_heard(monkeypatch):
    import ipaddress
    monkeypatch.setattr(al, "_heard", {})
    al.remember_reply(al.parse_artpollreply(make_reply(ip="192.168.1.60", short="Box B")), "192.168.1.60",
                      now=time.monotonic() - 30)
    got = al.find_nodes(subnets=[ipaddress.IPv4Network("127.0.0.0/30")], wait_s=0.2, port=9,
                        sock_factory=_real_socket)
    [row] = got["nodes"]
    assert row["name"] == "Box B" and row["heard"] == "earlier" and row["age_s"] >= 29


def test_find_subnets_defaults_and_refusals():
    nets, probs = al.find_subnets(None, ("192.168.1.80", None, "10.0.0.5"), "192.168.1.205")
    assert [str(n) for n in nets] == ["192.168.1.0/24", "10.0.0.0/24"] and probs == []
    nets, probs = al.find_subnets(["8.8.8.0/24", "192.168.0.0/16", "nope", "192.168.4.0/23"])
    assert [str(n) for n in nets] == ["192.168.4.0/23"] and len(probs) == 3
    assert al.find_subnets(None, (), None) == ([], [])


def test_lan_host_reads_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text("OTHER=1\nSM_LAN_HOST=192.168.7.20\n")
    assert al.lan_host({}, f) == "192.168.7.20"
    assert al.lan_host({"SM_LAN_HOST": "10.1.2.3"}, tmp_path / "missing") == "10.1.2.3"
    assert al.lan_host({}, tmp_path / "missing") is None
