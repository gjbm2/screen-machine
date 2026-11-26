#!/usr/bin/env python3
"""A virtual Art-Net node on the Windows side, with a GigBAR view (docs/GIGBAR_RIG.md §7).

It is a node in its own right, on its own host (Windows), listening on UDP 6454 like any Ethernet-DMX box:
  * ArtPoll (0x2000) -> an ArtPollReply (Art-Net 4 offsets: its IP on the interface the poll came in on, one DMX
    output port on its universe, a locally administered MAC), sent back to the poller;
  * ArtDmx (0x5000) for its Port-Address -> its DMX output buffer (other universes are counted, not taken);
  * ArtAddress (0x6000) -> its failsafe setting (AcFailHold / Zero / Full / Scene: what the output does when Art-Net
    stops), as a real node does.
Its DMX side runs as a node's does: the output port refreshes continuously at --refresh Hz (DMX512 is never "sent on
change"), whatever rate Art-Net arrives at; after --loss-s without Art-Net the failsafe applies (hold by default).
That DMX output is what the view reads: GET /dmx is a Server-Sent Events stream, one event per DMX refresh, carrying
the 512 slots. Nothing here makes up DMX: with no Art-Net arriving, the output holds (or zeroes) and says so.

    py -3.12 virtual_node.py                         # UDP 0.0.0.0:6454, universe (Port-Address) 1, view on :8705
    py -3.12 virtual_node.py --port 6455             # PC22: Windows' 6454 belongs to the Art-Net relay
    py -3.12 virtual_node.py --reply-to-6454         # answer polls on the poller's 6454 (some nodes do): tests the
                                                     # "no replies" path of a controller behind NAT

The views: http://127.0.0.1:8705/ (the board, gigbar.html) and /3d (gigbar3d.html, three.js), beside this file. GET /status is the node's state as JSON.
Standard library only (Windows Python 3.12).
"""

from __future__ import annotations

import argparse
import base64
import collections
import hashlib
import http.server
import json
import logging
import pathlib
import socket
import socketserver
import struct
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
ARTNET_ID = b"Art-Net\x00"
OP_POLL, OP_POLL_REPLY, OP_DMX, OP_ADDRESS = 0x2000, 0x2100, 0x5000, 0x6000
PROT_VER = 14
ESTA_PROTOTYPE = 0x7FF0             # ESTA's range for prototypes and experiments
AC_FAIL = {0x08: "hold", 0x09: "zero", 0x0A: "full", 0x0B: "scene"}
VERSION = "1.0"
#: the views' scripts, served from beside this file (three.js vendored: tools/virtual_node/vendor/three, MIT)
STATIC = {"/gigbar_model.js": "gigbar_model.js", "/vendor/three/three.module.js": "vendor/three/three.module.js",
          "/vendor/three/three.core.js": "vendor/three/three.core.js"}

log = logging.getLogger("virtual-node")


# -- the wire -------------------------------------------------------------------------------------------------------------
def parse(pkt: bytes) -> tuple[int, bytes] | None:
    """(OpCode, packet) for an Art-Net packet, else None."""
    if len(pkt) < 10 or not pkt.startswith(ARTNET_ID):
        return None
    return struct.unpack_from("<H", pkt, 8)[0], pkt


def parse_dmx(pkt: bytes) -> tuple[int, int, bytes] | None:
    """(port_address, sequence, data) of an ArtDmx packet."""
    if len(pkt) < 18:
        return None
    seq, sub_uni, net = pkt[12], pkt[14], pkt[15]
    (length,) = struct.unpack_from(">H", pkt, 16)
    return (net & 0x7F) << 8 | sub_uni, seq, pkt[18:18 + min(length, 512)]


def build_poll_reply(*, ip: str, port_address: int, mac: bytes, short: str, long: str, report: str,
                     outputting: bool, merging: bool, refresh_hz: int, failsafe: str) -> bytes:
    """An ArtPollReply (Art-Net 4, 239 bytes): one DMX512 output port on ``port_address``."""
    b = bytearray(239)
    b[0:8] = ARTNET_ID
    struct.pack_into("<H", b, 8, OP_POLL_REPLY)
    b[10:14] = socket.inet_aton(ip)
    struct.pack_into("<H", b, 14, 6454)                       # always 0x1936
    b[16], b[17] = 0, 1                                       # firmware version
    b[18], b[19] = (port_address >> 8) & 0x7F, (port_address >> 4) & 0x0F   # NetSwitch, SubSwitch
    b[23] = 0xE0                                              # Status1: indicators normal, addresses set by network
    struct.pack_into("<H", b, 24, ESTA_PROTOTYPE)
    b[26:26 + 17] = short.encode("ascii", "replace")[:17].ljust(17, b"\0")
    b[44:44 + 63] = long.encode("ascii", "replace")[:63].ljust(63, b"\0")
    b[108:108 + 63] = report.encode("ascii", "replace")[:63].ljust(63, b"\0")
    struct.pack_into(">H", b, 172, 1)                         # NumPorts
    b[174] = 0x80                                             # PortTypes[0]: can output DMX512 from Art-Net
    b[178] = 0x08                                             # GoodInput[0]: input disabled
    b[182] = (0x80 if outputting else 0) | (0x08 if merging else 0)   # GoodOutputA[0]: data, merging
    b[190] = port_address & 0x0F                              # SwOut[0]
    b[200] = 0x00                                             # Style: StNode
    b[201:207] = mac
    b[207:211] = socket.inet_aton(ip)                         # BindIp
    b[211] = 1                                                # BindIndex
    b[212] = 0x08                                             # Status2: 15-bit Port-Address
    b[213] = 0xC0                                             # GoodOutputB[0]: RDM off, continuous output
    b[217] = {"hold": 0x00, "zero": 0x40, "full": 0x80, "scene": 0xC0}.get(failsafe, 0)   # Status3: failsafe
    struct.pack_into(">H", b, 226, refresh_hz)                # RefreshRate
    return bytes(b)


def local_ip_towards(addr: str) -> str:
    """This host's address on the interface that reaches ``addr`` (the address a reply must carry)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((addr, 6454))
        return s.getsockname()[0]
    except OSError:
        return "0.0.0.0"
    finally:
        s.close()


# -- the node ---------------------------------------------------------------------------------------------------------------
class Node:
    """Art-Net in, one DMX output port out. The UDP thread writes; the DMX refresh thread and the HTTP threads read."""

    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.port_address = a.universe
        self.mac = bytes([0x02, 0x53, 0x4D]) + hashlib.sha1(socket.gethostname().encode()).digest()[:3]
        self.failsafe = a.failsafe
        self.dmx_in = bytearray(512)                # the last Art-Net frame's slots
        self.dmx_out = bytearray(512)               # what the DMX port outputs now
        self.lock = threading.Lock()
        self.t0 = time.monotonic()
        self.last_dmx_t: float | None = None
        self.last_len = 0
        self.packets = 0
        self.polls = 0
        self.replies = 0
        self.addresses = 0
        self.other = collections.Counter()           # ArtDmx for other Port-Addresses
        self.ignored = 0
        self.seq_jumps = 0
        self.sources: dict[str, dict] = {}           # ip -> {packets, last_t, seq}
        self.recent: collections.deque[float] = collections.deque(maxlen=400)
        self.frame = 0                               # DMX refresh count
        self.out_t = time.monotonic()
        self.lost = False
        self.sock: socket.socket | None = None
        self.cond = threading.Condition()

    # -- UDP ---------------------------------------------------------------------------------------------------------------
    def serve_udp(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((self.a.bind, self.a.port))
        self.sock = s
        log.info("Art-Net node on udp %s:%d, universe (Port-Address) %d, failsafe %s", self.a.bind, self.a.port,
                 self.port_address, self.failsafe)
        while True:
            try:
                data, addr = s.recvfrom(2048)
            except ConnectionResetError:             # Windows: an ICMP port-unreachable from a past send
                continue
            except OSError as e:
                log.warning("udp: %s", e)
                time.sleep(0.2)
                continue
            p = parse(data)
            if p is None:
                self.ignored += 1
                continue
            op, pkt = p
            if op == OP_DMX:
                self.on_dmx(pkt, addr)
            elif op == OP_POLL:
                self.on_poll(addr)
            elif op == OP_ADDRESS:
                self.on_address(pkt)
            else:
                self.ignored += 1

    def on_dmx(self, pkt: bytes, addr: tuple[str, int]) -> None:
        d = parse_dmx(pkt)
        if d is None:
            self.ignored += 1
            return
        pa, seq, payload = d
        if pa != self.port_address:
            self.other[pa] += 1
            return
        now = time.monotonic()
        src = self.sources.setdefault(addr[0], {"packets": 0, "last_t": now, "seq": None, "port": addr[1]})
        if seq and src["seq"] and seq != src["seq"] % 255 + 1:
            self.seq_jumps += 1                      # a lost or reordered packet (sequence 0 = off)
        src.update(packets=src["packets"] + 1, last_t=now, seq=seq, port=addr[1])
        with self.lock:
            self.dmx_in[:len(payload)] = payload     # a short frame leaves the slots above it as they were
            self.last_len = len(payload)
            self.last_dmx_t = now
            self.packets += 1
            self.recent.append(now)

    def on_poll(self, addr: tuple[str, int]) -> None:
        self.polls += 1
        ip = local_ip_towards(addr[0])
        age = self.age_s()
        rep = build_poll_reply(ip=ip, port_address=self.port_address, mac=self.mac, short=self.a.short_name,
                               long=self.a.long_name, report=f"#0001 [{self.polls:04d}] {self.status_word()}",
                               outputting=age is not None and age < self.a.loss_s, merging=self.merging(),
                               refresh_hz=self.a.refresh, failsafe=self.failsafe)
        dest = (addr[0], 6454) if self.a.reply_to_6454 else addr
        try:
            assert self.sock is not None
            self.sock.sendto(rep, dest)
            self.replies += 1
        except OSError as e:
            log.warning("ArtPollReply to %s:%d failed: %s", dest[0], dest[1], e)

    def on_address(self, pkt: bytes) -> None:
        self.addresses += 1
        cmd = pkt[106] if len(pkt) > 106 else None
        if cmd in AC_FAIL:
            self.failsafe = AC_FAIL[cmd]
            log.info("ArtAddress: failsafe %s", self.failsafe)

    # -- the DMX port ----------------------------------------------------------------------------------------------------
    def refresh_loop(self) -> None:
        period = 1.0 / self.a.refresh
        nxt = time.monotonic()
        while True:
            nxt += period
            now = time.monotonic()
            with self.lock:
                age = None if self.last_dmx_t is None else now - self.last_dmx_t
                lost = age is None or age > self.a.loss_s
                if not lost:
                    self.dmx_out[:] = self.dmx_in
                elif self.failsafe == "zero":
                    self.dmx_out[:] = bytes(512)
                elif self.failsafe == "full":
                    self.dmx_out[:] = b"\xff" * 512
                # hold (and scene: no recorded scene here) keep the last output
                self.lost = lost
                self.frame += 1
                self.out_t = now
            with self.cond:
                self.cond.notify_all()
            time.sleep(max(0.0, nxt - time.monotonic()))
            if time.monotonic() - nxt > 1.0:
                nxt = time.monotonic()              # the host stalled: don't burst to catch up

    # -- views -------------------------------------------------------------------------------------------------------------
    def age_s(self) -> float | None:
        return None if self.last_dmx_t is None else time.monotonic() - self.last_dmx_t

    def merging(self) -> bool:
        now = time.monotonic()
        return sum(1 for s in self.sources.values() if now - s["last_t"] < 10) > 1

    def status_word(self) -> str:
        age = self.age_s()
        if age is None:
            return "no Art-Net yet"
        return "receiving" if age < self.a.loss_s else f"no Art-Net for {age:.0f} s ({self.failsafe})"

    def status(self) -> dict:
        now = time.monotonic()
        age = self.age_s()
        return {"node": {"short_name": self.a.short_name, "long_name": self.a.long_name, "version": VERSION,
                         "udp": f"{self.a.bind}:{self.a.port}", "universe": self.port_address,
                         "mac": ":".join(f"{x:02x}" for x in self.mac), "failsafe": self.failsafe,
                         "refresh_hz": self.a.refresh, "loss_s": self.a.loss_s,
                         "reply_to_6454": self.a.reply_to_6454, "up_s": round(now - self.t0, 1)},
                "artnet": {"packets": self.packets, "pps": sum(1 for t in self.recent if now - t <= 1.0),
                           "age_s": None if age is None else round(age, 3), "length": self.last_len,
                           "word": self.status_word(), "merging": self.merging(), "seq_jumps": self.seq_jumps,
                           "sources": {ip: {"packets": s["packets"], "age_s": round(now - s["last_t"], 2),
                                            "seq": s["seq"], "port": s["port"]} for ip, s in self.sources.items()},
                           "other_universes": dict(self.other.most_common(8)), "ignored": self.ignored,
                           "polls": self.polls, "replies": self.replies, "artaddress": self.addresses},
                "dmx": {"frame": self.frame, "lost": self.lost}}

    def dmx_event(self) -> str:
        with self.lock:
            data = bytes(self.dmx_out)
            frame, lost = self.frame, self.lost
        age = self.age_s()
        return json.dumps({"f": frame, "lost": lost, "age_s": None if age is None else round(age, 3),
                           "d": base64.b64encode(data).decode()})


# -- HTTP: the view, /status, /dmx (SSE) -------------------------------------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    node: Node
    server_version = "sm-virtual-node/" + VERSION

    def log_message(self, fmt: str, *args) -> None:       # quiet: the SSE stream would flood the log
        pass

    def do_GET(self) -> None:                             # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/gigbar", "/gigbar.html"):
            return self.file(HERE / "gigbar.html", "text/html; charset=utf-8")
        if path in ("/3d", "/gigbar3d.html"):
            return self.file(HERE / "gigbar3d.html", "text/html; charset=utf-8")
        if path in STATIC:
            return self.file(HERE / STATIC[path], "text/javascript; charset=utf-8")
        if path == "/status":
            return self.json(self.node.status())
        if path == "/dmx":
            return self.sse()
        self.send_error(404)

    def file(self, p: pathlib.Path, ctype: str) -> None:
        try:
            body = p.read_bytes()
        except OSError:
            return self.send_error(404, f"{p.name} isn't beside virtual_node.py")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last = -1
        try:
            while True:
                with self.node.cond:
                    self.node.cond.wait(timeout=1.0)
                if self.node.frame == last:
                    continue
                last = self.node.frame
                self.wfile.write(b"data: " + self.node.dmx_event().encode() + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A virtual Art-Net node with a GigBAR view")
    ap.add_argument("--bind", default="0.0.0.0", help="UDP address to listen on (default all interfaces)")
    ap.add_argument("--port", type=int, default=6454, help="UDP port (6454, the Art-Net port)")
    ap.add_argument("--universe", type=int, default=1, help="the output port's 15-bit Port-Address (default 1)")
    ap.add_argument("--http", default="127.0.0.1:8705", help="the view's address (default 127.0.0.1:8705)")
    ap.add_argument("--refresh", type=int, default=40, help="DMX output refresh, Hz (default 40)")
    ap.add_argument("--loss-s", type=float, default=4.0, help="Art-Net silence before the failsafe (default 4 s)")
    ap.add_argument("--failsafe", choices=("hold", "zero", "full", "scene"), default="hold")
    ap.add_argument("--reply-to-6454", action="store_true", help="send ArtPollReply to the poller's port 6454")
    ap.add_argument("--short-name", default="SM Virtual Node")
    ap.add_argument("--long-name", default="screen-machine virtual Art-Net node (GigBAR view)")
    ap.add_argument("--log", default=None, help="a log file (default: stderr)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        filename=a.log, encoding="utf-8" if a.log else None)
    if not 1 <= a.universe < 1 << 15:
        ap.error("--universe must be 1..32767")
    node = Node(a)
    threading.Thread(target=node.serve_udp, name="artnet", daemon=True).start()
    threading.Thread(target=node.refresh_loop, name="dmx-out", daemon=True).start()
    host, _, port = a.http.rpartition(":")
    Handler.node = node
    httpd = Server((host or "127.0.0.1", int(port)), Handler)
    log.info("view on http://%s:%s/", host or "127.0.0.1", port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
