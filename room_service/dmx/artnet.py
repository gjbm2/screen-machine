"""Art-Net receiver: ArtDmx on UDP 6454 for one Port-Address (the universe Maestro sends its virtual devices on).

Only ArtDmx (OpCode 0x5000) is read; everything else is counted and ignored. Maestro is set up with this host's IP
by hand ("Define its IP address", r3 §1), so no ArtPollReply is needed. Under WSL's NAT nothing on the LAN reaches
this socket directly: the Windows side relays UDP 6454 to WSL (docs/PORTS.md).

Each accepted packet is stamped with CLOCK_MONOTONIC on arrival (LIVE_V1 §1.9 `t_ms`) and handed to the listeners.
Packets for other universes are counted per Port-Address, so a universe off-by-one (Maestro's "Offset Universes")
shows up in status instead of as silence.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from collections import Counter, deque
from typing import Callable

log = logging.getLogger("room_service.dmx.artnet")

ARTNET_ID = b"Art-Net\x00"
OP_DMX = 0x5000
HEADER = 18


def parse_artdmx(pkt: bytes) -> tuple[int, int, bytes] | None:
    """(port_address, sequence, data) for an ArtDmx packet, else None."""
    if len(pkt) < HEADER or not pkt.startswith(ARTNET_ID):
        return None
    (op,) = struct.unpack_from("<H", pkt, 8)
    if op != OP_DMX:
        return None
    seq, _phys, sub_uni, net = pkt[12], pkt[13], pkt[14], pkt[15]
    (length,) = struct.unpack_from(">H", pkt, 16)
    data = pkt[HEADER:HEADER + min(length, 512)]
    return (net & 0x7F) << 8 | sub_uni, seq, data


def build_artdmx(port_address: int, data: bytes, seq: int = 0) -> bytes:
    """An ArtDmx packet (test sender, room_service.dmx.send)."""
    data = bytes(data[:512])
    if len(data) % 2:
        data += b"\x00"                        # the spec wants an even length
    return (ARTNET_ID + struct.pack("<H", OP_DMX) + bytes([0, 14, seq & 0xFF, 0, port_address & 0xFF,
                                                           (port_address >> 8) & 0x7F])
            + struct.pack(">H", len(data)) + data)


class ArtNetReceiver(asyncio.DatagramProtocol):
    """Keeps the latest 512 channels of one universe and calls listeners(t_ms) on every accepted packet."""

    def __init__(self, universe: int, stale_after_ms: int = 1500) -> None:
        self.universe = universe
        self.stale_after_ms = stale_after_ms
        self.dmx = bytearray(512)
        self.listeners: list[Callable[[float], None]] = []
        self.transport: asyncio.DatagramTransport | None = None
        self.bind: str | None = None
        self.bind_error: str | None = None
        self.packets = 0
        self.other = Counter()                  # packets for other Port-Addresses
        self.ignored = 0                        # not ArtDmx (ArtPoll, ArtSync, junk)
        self.last_t: float | None = None        # monotonic seconds of the last accepted packet
        self.last_from: str | None = None
        self.last_seq: int | None = None
        self.source = "artnet"                  # "artnet", "fake" (fake_artnet.py) or "inject" (the API's test path)
        self.fake = None                        # the FakeArtNet, which registers itself
        self._recent: deque[float] = deque(maxlen=400)

    async def start(self, host: str, port: int) -> None:
        loop = asyncio.get_running_loop()
        self.bind = f"{host}:{port}"
        try:
            await loop.create_datagram_endpoint(lambda: self, local_addr=(host, port), reuse_port=False)
            log.info("Art-Net: listening on udp %s, universe %d", self.bind, self.universe)
        except OSError as e:
            self.bind_error = str(e)
            log.error("Art-Net: can't bind udp %s: %s (no Art-Net input)", self.bind, e)

    def connection_made(self, transport) -> None:
        self.transport = transport

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None

    def datagram_received(self, data: bytes, addr) -> None:
        parsed = parse_artdmx(data)
        if parsed is None:
            self.ignored += 1
            return
        pa, seq, payload = parsed
        if pa != self.universe:
            self.other[pa] += 1
            return
        source, fake = "artnet", self.fake
        if fake is not None and fake.running:
            if addr is not None and fake.addr == tuple(addr[:2]):
                source = "fake"
            else:
                fake.yield_to(addr[0] if addr else "?")   # real Art-Net wins
        self.last_from = addr[0] if addr else None
        self.last_seq = seq
        self.accept(payload, source)

    def accept(self, payload: bytes, source: str) -> None:
        now = time.monotonic()
        self.dmx[:len(payload)] = payload
        self.packets += 1
        self.last_t = now
        self.source = source
        self._recent.append(now)
        for fn in list(self.listeners):
            try:
                fn(now * 1000.0)
            except Exception:                   # a listener's bug must not stop the input
                log.exception("Art-Net listener failed")

    def inject(self, channels: dict[int, int]) -> None:
        """Set channels (1-based) as if a packet had arrived: the API's test path, never used by Maestro."""
        buf = bytearray(self.dmx)
        for ch, v in channels.items():
            buf[ch - 1] = max(0, min(255, int(v)))
        self.accept(bytes(buf), "inject")

    @property
    def age_ms(self) -> float | None:
        return None if self.last_t is None else (time.monotonic() - self.last_t) * 1000.0

    @property
    def online(self) -> bool:
        age = self.age_ms
        return age is not None and age <= self.stale_after_ms

    @property
    def pps(self) -> float:
        now = time.monotonic()
        n = sum(1 for t in self._recent if now - t <= 1.0)
        return float(n)

    def status(self) -> dict:
        age = self.age_ms
        return {"bind": self.bind, "bind_error": self.bind_error, "universe": self.universe,
                "online": self.online, "age_ms": None if age is None else round(age, 1), "pps": self.pps,
                "packets": self.packets, "last_from": self.last_from, "last_seq": self.last_seq,
                "source": self.source if self.packets else None,
                "other_universes": {str(k): v for k, v in self.other.most_common(8)}, "ignored": self.ignored}
