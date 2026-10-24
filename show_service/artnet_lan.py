"""artnet_lan: show_service's own Art-Net sender to LAN nodes (Stage 5, P5; docs/SHOW_ENGINE.md §5.4, §9.3;
README S5.7). Off by default: nothing is built until fixtures.yaml enables an ``artnet`` output.

One ``ArtNetLan`` per enabled ``artnet`` output (one universe on one node), run by outputs.py's runner thread
``show-out-<id>`` (``plan`` and ``set_frame``) and ticked on show-brain at 1 Hz (``configure``, ``tick``). One ``Nodes``
per Rig (``shared["artnet.nodes"]``) owns the socket, the node table, discovery, health and failsafe.

**The wire** (one copy in show_service: artnet_out's ``build_artdmx`` and constants, §9.3):
  ArtDmx        ``Art-Net\\0``, OpCode 0x5000 LE, ProtVer 14, Sequence 1..255 per universe (never 0: "off"),
                Physical 0, SubUni, Net, Length BE (even, 2..512), data. Port-Address >= 1 (0 is room_service's own
                universe). Unicast only (WSL can't broadcast; no ArtSync with one universe per node).
  ArtPoll       OpCode 0x2000, ProtVer 14, Flags 0 (reply only when polled), DiagPriority 0: 14 bytes, unicast.
  ArtPollReply  OpCode 0x2100, Art-Net 4 offsets (``parse_artpollreply``): IP, port, names, ports, GoodOutputA (merge
                bits), MAC, Status3 (failsafe), RefreshRate.
  ArtAddress    OpCode 0x6000, every field "no change" (0x7f, empty names, AcnPriority 255) except ``Command``:
                AcFailHold 0x08, AcFailZero 0x09, AcFailFull 0x0a, AcFailScene 0x0b, AcFailRecord 0x0c.

**The frame grid** (``plan``, §5.4), while the look is active and the clock predicts beats (gate ``dmx`` or better:
§5.4's middle confidence keeps DMX beat-locked):
  * ``L`` = the output's intensity rise latency (latency.json under its ``latency_key``, else hand-set, else 0);
  * the next beat fire time ``f = t_k - L`` (``clock.next_beat(t, lead_s=L)``), the tick ``grid_tick(P)`` (P / m, m the
    largest multiple of 4 keeping <= 43 Hz), grid points ``f - j x tick``: a frame leaves exactly at ``t_beat - L`` and
    on every 16th. The clock is read at every plan, so a re-anchor re-phases the grid at the next frame;
  * never closer than ``MIN_GAP_S`` (22.7 ms) to when the last frame actually left (``set_frame`` stamps it). A beat
    re-phased into that gap goes at last + gap: late by under a frame, never early. A grid point at most
    SPACING_WAIT_S inside the gap (the last frame left a little late) goes at last + gap too, rather than a whole tick
    on (README S5.14). A beat the last frame already carried isn't taken again when the clock nudges it (artnet_out's
    REPHASE_SKIP). An output's ``rate_hz`` keeps the grid's own slack (GRID_SLACK_S) over its gap;
  * no beat (gate ``none``, or no clock): free-run at 43 Hz;
  * a hit (``on_hit``, or the runner's own ``plan_hit``) pulls the next frame forward to ``max(now, last + gap)``,
    unless that would push the next beat frame late (the beat is less than a gap after it): then the flash rides the
    beat frame, at most one gap later (c8's integration ruling, 16 Oct);
  * idle (an inactive look): plan every ``1 / idle_eval_hz``; ``set_frame`` sends only when the universe changed or
    ``keepalive_s`` (1 s) passed since the last send.

**The universe** (``set_frame``): built in place in a preallocated packet: each fixture's type defaults and the
fixture's pins (compiled at ``configure``), then each fixture's ``FixtureFrame.dmx`` at ``address - 1`` (by address:
on an overlap the later address wins). Short frames: up to the highest patched channel, rounded up to even (>= 2),
unless the node's ``full_512``. Channels no fixture covers are 0.

**Nodes** (show-brain): a unicast ArtPoll every ``poll_s`` (3 s) to each node an enabled output uses; replies drained
from our own socket (WSL NAT carries some nodes' replies back) and from ``reply_bind`` when set (ca's relay, README
S5.7). Matched by MAC, else short name. A matching reply from another address is used and kept in nodes.json (state
dir, atomic; "moved: update last_ip"); code never writes fixtures.yaml. A reply at the node's address that doesn't
match: ``matched: false``, frames keep going (no stop gates). A node with ``failsafe`` gets one ArtAddress per
setting when an enabled output first uses it; ``record_failsafe`` resends the node's current universes, then
AcFailRecord.

**Never to room's receiver.** A loopback address on udp 6454 is never sent to: a node configured there is treated as
virtual (a problem line), and ``Nodes.send`` refuses it again at the socket. **The virtual node** (``virtual: true``)
keeps its frames in memory (decoded by ``Nodes.dmx_view``, served by the rig's frame route) and sends nothing.

Threads: ``plan``/``set_frame``/``on_hit`` run on the output's runner (``on_hit`` from any thread); ``configure``,
``tick``, ``record_failsafe`` on show-brain; ``status``, ``universe_view``, ``dmx_view`` from any thread. Shared state is
swapped by reference; nothing here locks on the frame path.
"""

from __future__ import annotations

import inspect
import ipaddress
import json
import logging
import math
import os
import pathlib
import socket
import struct
import threading
import time
import dataclasses
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

from . import contract
from .artnet_out import (ARTNET_ID, GRID_HZ, HEADER, MAX_HZ, MIN_GAP_S, OP_DMX, PORT, PROT_VER, REPHASE_SKIP,
                         UNIVERSE_SIZE, build_artdmx, grid_tick, next_seq)

log = logging.getLogger("show_service.artnet_lan")

# -- the wire (Art-Net 4) -----------------------------------------------------------------------------------------------
OP_POLL = 0x2000
OP_POLL_REPLY = 0x2100
OP_ADDRESS = 0x6000
#: ArtAddress Command per contract.FAILSAFE_MODES (Art-Net 4: AcFailHold .. AcFailScene), and AcFailRecord.
AC_FAIL: dict[str, int] = {"hold": 0x08, "zero": 0x09, "full": 0x0A, "scene": 0x0B}
AC_FAIL_RECORD = 0x0C
if set(AC_FAIL) != set(contract.FAILSAFE_MODES):          # the contract's vocabulary and the wire must agree
    raise ImportError("artnet_lan.AC_FAIL doesn't cover contract.FAILSAFE_MODES")
STATUS3_FAILSAFE = ("hold", "zero", "full", "scene")      # Status3 bits 7-6: 00 hold, 01 zero, 10 full, 11 scene
REPLY_MIN = 108                                           # through LongName: shorter is not an ArtPollReply
NO_CHANGE = 0x7F                                          # ArtAddress NetSwitch / SwIn / SwOut / SubSwitch
ACN_NO_CHANGE = 0xFF                                      # ArtAddress AcnPriority
BIND_ROOT = 1                                             # ArtAddress BindIndex: 1 = the root device

# -- show.yaml rig.artnet (fallbacks; the same numbers as show.yaml, with their §) --------------------------------------
POLL_S = 3.0                    # §9.3: a unicast ArtPoll every 3 s to each node an enabled output uses
KEEPALIVE_S = 1.0               # §9.3: an unchanged universe goes out again at least this often
IDLE_EVAL_HZ = 10.0             # starting point: how often an idle output's look is checked for a change
SILENT_AFTER_S = 10.0           # starting point: no ArtPollReply this long reads "silent" (health only)

# -- timing and housekeeping --------------------------------------------------------------------------------------------
GATE = "dmx"                    # §5.4: beat-locked on DMX from the clock's middle confidence up
SPACING_WAIT_S = 0.001          # a frame due inside the gap by no more than this waits it out (set_frame <= 1 ms)
SEND_LOG_S = 30.0               # a failing send is logged once, then at most this often
DRAIN_MAX = 64                  # replies read per socket per tick
RATE_WINDOW_S = 5.0             # OutputStatus.rate_hz is measured over this
ERROR_RUN = 3                   # this many failed sends in a row: state "error" (frames keep being tried)
RING = 256
SHARED_KEY = "artnet.nodes"     # outputs.py's shared dict: one Nodes per Rig
NO_REPLIES = ("no replies yet: nodes answer on PC22's udp 6454, which ca's relay forwards to room_service "
              "(README S5.7)")

CAPS: contract.DriverCaps = {"hits": True, "min_attack_ms": 0.0, "grid": "beat", "rate_hz": MAX_HZ,
                             "keepalive_s": KEEPALIVE_S, "min_period_ms": 0.0,
                             # nothing room-side decides which DMX fixtures follow: no vibe is the house look (§8.6)
                             "idle_without_vibe": True}
#: The grid's slack under the gap: GRID_HZ keeps a tick at least this much longer than Art-Net's MIN_GAP_S (0.53 ms),
#: and an output's own ``rate_hz`` keeps the same margin over its gap (README S5.14).
GRID_SLACK_S = 1.0 / GRID_HZ - MIN_GAP_S


# -- packets ------------------------------------------------------------------------------------------------------------
def build_artpoll() -> bytes:
    """ArtPoll: ``Art-Net\\0``, OpCode 0x2000 LE, ProtVer 14, Flags 0, DiagPriority 0 (14 bytes)."""
    return ARTNET_ID + OP_POLL.to_bytes(2, "little") + bytes([0, PROT_VER, 0, 0])


ARTPOLL = build_artpoll()


def build_artaddress(cmd: int) -> bytes:
    """ArtAddress with ``Command`` = cmd and every other field "no change" (107 bytes): NetSwitch 0x7f, BindIndex 1,
    ShortName and LongName empty (ignored), SwIn and SwOut 0x7f each, SubSwitch 0x7f, AcnPriority 255."""
    if not 0 <= cmd <= 0xFF:
        raise ValueError("cmd must be 0..255")
    return (ARTNET_ID + OP_ADDRESS.to_bytes(2, "little") + bytes([0, PROT_VER, NO_CHANGE, BIND_ROOT])
            + bytes(18) + bytes(64) + bytes([NO_CHANGE] * 4) + bytes([NO_CHANGE] * 4)
            + bytes([NO_CHANGE, ACN_NO_CHANGE, cmd]))


def decode_artdmx(data: bytes | bytearray | memoryview) -> Optional[dict]:
    """An ArtDmx packet -> ``{port_address, seq, physical, length, data}``; None for anything else."""
    b = bytes(data)
    if len(b) < HEADER + 2 or b[:8] != ARTNET_ID or struct.unpack_from("<H", b, 8)[0] != OP_DMX:
        return None
    (length,) = struct.unpack_from(">H", b, 16)
    if length > UNIVERSE_SIZE or len(b) < HEADER + length:
        return None
    return {"port_address": (b[15] & 0x7F) << 8 | b[14], "seq": b[12], "physical": b[13], "length": length,
            "data": b[HEADER:HEADER + length]}


def _cstr(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()


def norm_mac(s: Any) -> Optional[str]:
    """``"AA-BB-CC-DD-EE-FF"`` / ``"aabbccddeeff"`` / ``"aa:bb:.."`` -> ``"aa:bb:cc:dd:ee:ff"``; None if it isn't one."""
    if not isinstance(s, str):
        return None
    h = "".join(c for c in s.lower() if c in "0123456789abcdef")
    if len(h) != 12 or len(h) != sum(c not in ":-. " for c in s):
        return None
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def parse_artpollreply(data: bytes | bytearray | memoryview) -> Optional[dict]:
    """An ArtPollReply (Art-Net 4 offsets) -> a dict; None for anything else (junk, another OpCode, too short). Fields
    past the end of a short (older) reply are None."""
    try:
        b = bytes(data)
    except (TypeError, ValueError):
        return None
    if len(b) < REPLY_MIN or b[:8] != ARTNET_ID or struct.unpack_from("<H", b, 8)[0] != OP_POLL_REPLY:
        return None
    n = len(b)

    def has(off: int, size: int = 1) -> bool:
        return n >= off + size

    net, sub = b[18], b[19]
    num_ports = (b[172] << 8 | b[173]) if has(172, 2) else None
    ports = max(1, min(4, num_ports or 0))
    good_out = list(b[182:186]) if has(182, 4) else None
    sw_out = list(b[190:194]) if has(190, 4) else None
    mac = None
    if has(201, 6) and any(b[201:207]):
        mac = ":".join(f"{x:02x}" for x in b[201:207])
    status3 = b[217] if has(217) else None
    refresh = (b[226] << 8 | b[227]) if has(226, 2) else None
    return {
        "ip": ".".join(str(x) for x in b[10:14]),
        "port": struct.unpack_from("<H", b, 14)[0],
        "version": b[16] << 8 | b[17],
        "net": net, "sub": sub,
        "oem": b[20] << 8 | b[21],
        "status1": b[23],
        "esta": b[25] << 8 | b[24],
        "short_name": _cstr(b[26:44]),
        "long_name": _cstr(b[44:108]),
        "node_report": _cstr(b[108:172]) if has(108, 64) else None,
        "num_ports": num_ports,
        "port_types": list(b[174:178]) if has(174, 4) else None,
        "good_input": list(b[178:182]) if has(178, 4) else None,
        "good_output": good_out,
        "sw_in": list(b[186:190]) if has(186, 4) else None,
        "sw_out": sw_out,
        "port_addresses": ([(net & 0x7F) << 8 | (sub & 0x0F) << 4 | (s & 0x0F) for s in sw_out[:ports]]
                           if sw_out is not None else None),
        "style": b[200] if has(200) else None,
        "mac": mac,
        "bind_ip": ".".join(str(x) for x in b[207:211]) if has(207, 4) else None,
        "bind_index": b[211] if has(211) else None,
        "status2": b[212] if has(212) else None,
        "good_output_b": list(b[213:217]) if has(213, 4) else None,
        "status3": status3,
        # Status3 bit 5: the node supports a programmable failsafe; then bits 7-6 say which one it uses
        "failsafe_supported": bool(status3 & 0x20) if status3 is not None else None,
        "failsafe": STATUS3_FAILSAFE[status3 >> 6] if status3 is not None and status3 & 0x20 else None,
        "merging": any(x & 0x08 for x in good_out) if good_out is not None else None,   # GoodOutputA bit 3
        "merge_ltp": any(x & 0x02 for x in good_out) if good_out is not None else None,  # bit 1: LTP (else HTP)
        "refresh_hz": float(refresh) if refresh else None,   # Art-Net 4 RefreshRate; 0: not given (0..44: DMX's 44 Hz)
    }


# -- addresses ----------------------------------------------------------------------------------------------------------
def unicast_ip(s: Any) -> Optional[str]:
    """An IPv4 unicast literal, normalised; None for anything else (no DNS: never a lookup on show-brain)."""
    try:
        a = ipaddress.IPv4Address(str(s).strip())
    except (ipaddress.AddressValueError, ValueError):
        return None
    if a.is_unspecified or a.is_multicast or a.is_reserved:
        return None
    return str(a)


def is_room_receiver(ip: Any, port: Any) -> bool:
    """A loopback address on udp 6454: room_service's own receiver (the virtual Maestro's universe). Never sent to."""
    if port != PORT:
        return False
    try:
        return ipaddress.ip_address(str(ip).strip()).is_loopback
    except ValueError:
        return str(ip).strip().lower() == "localhost"


def _udp_socket() -> socket.socket:
    """Our one UDP socket: bound 0.0.0.0:0 (an ephemeral port), non-blocking. Tests replace this factory."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 0))
    s.setblocking(False)
    return s


def _bound_socket(host: str, port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((host, port))
    s.setblocking(False)
    return s


# -- settings (show.yaml rig.artnet) ------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Settings:
    poll_s: float = POLL_S
    keepalive_s: float = KEEPALIVE_S
    idle_eval_hz: float = IDLE_EVAL_HZ
    silent_after_s: float = SILENT_AFTER_S
    reply_bind: Optional[tuple[str, int]] = None
    problems: tuple[str, ...] = ()


def parse_settings(raw: Any) -> Settings:
    """show.yaml ``rig.artnet`` -> Settings. A value it can't use keeps the fallback, with a problem line."""
    raw = raw if isinstance(raw, Mapping) else {}
    vals: dict[str, Any] = {}
    problems: list[str] = []
    for key, default in (("poll_s", POLL_S), ("keepalive_s", KEEPALIVE_S), ("idle_eval_hz", IDLE_EVAL_HZ),
                         ("silent_after_s", SILENT_AFTER_S)):
        v = raw.get(key)
        if v is None:
            vals[key] = default
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0:
            vals[key] = float(v)
        else:
            vals[key] = default
            problems.append(f"rig.artnet.{key}: {v!r} isn't a positive number: using {default:g}")
    bind = None
    rb = raw.get("reply_bind")
    if rb not in (None, ""):
        host, sep, port = str(rb).strip().rpartition(":")
        try:
            p = int(port)
        except ValueError:
            p = -1
        h = host or "0.0.0.0"
        try:
            ipaddress.IPv4Address(h)
            ok_host = True
        except ValueError:
            ok_host = False
        if not sep or not 0 <= p <= 65535 or not ok_host:
            problems.append(f"rig.artnet.reply_bind: {rb!r} isn't host:port (an IPv4 address): not bound")
        elif p == PORT:
            problems.append(f"rig.artnet.reply_bind: {rb!r}: udp {PORT} is room_service's receiver: not bound")
        else:
            bind = (h, p)
    return Settings(reply_bind=bind, problems=tuple(problems), **vals)


# -- nodes ------------------------------------------------------------------------------------------------------------
@dataclass
class _NodeState:
    replies: int = 0
    reply_at: Optional[float] = None
    reply_ip: Optional[str] = None
    matched: Optional[bool] = None
    last: Optional[dict] = None
    mismatch: Optional[str] = None
    polls: int = 0
    next_poll: float = -math.inf
    sent: int = 0
    kept: int = 0
    send_errors: int = 0
    refused: int = 0
    last_error: Optional[str] = None
    failsafe_sent: Optional[str] = None


@dataclass(frozen=True)
class _Dest:
    ip: Optional[str]
    port: int
    virtual: bool
    ip_from: str                    # config | discovery | virtual | none


class Nodes:
    """The Art-Net node table, one per Rig (``shared["artnet.nodes"]``). Module docstring."""

    def __init__(self, *, settings: Any = None, state_file: Optional[pathlib.Path] = None, sock: Any = None,
                 mono: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        self.settings = parse_settings(settings)
        self.state_file = pathlib.Path(state_file) if state_file is not None else contract.NODES_STATE_FILE
        self._sock = sock
        self._own_sock = sock is None
        if sock is not None and hasattr(sock, "setblocking"):
            sock.setblocking(False)
        self._sock_lock = threading.Lock()
        self._reply_sock: Any = None
        self._reply_bound: Optional[tuple[str, int]] = None
        self._reply_error: Optional[str] = None
        self._reply_retry = -math.inf
        self._mono = mono
        self._wall = wall
        self._cfg: dict[str, dict] = {}
        self._used: frozenset[str] = frozenset()
        self._dests: dict[str, _Dest] = {}              # swapped by reference (runners read it)
        self._problems: dict[str, tuple[str, ...]] = {}
        self._st: dict[str, _NodeState] = {}
        self._found: dict[str, dict] = self._load_state()   # nodes.json: {node: {ip, config_ip, mac, short_name, at}}
        self._kept: dict[str, dict[int, tuple[bytes, float]]] = {}   # virtual: node -> port address -> (packet, t)
        self._claims: dict[str, tuple[str, Callable[[], Optional[bytes]]]] = {}   # output -> (node, last_packet)
        self.junk = 0                                   # non-reply or unparseable packets drained
        self.unmatched = 0                              # replies no node claimed
        self.last_unmatched: Optional[dict] = None
        self.drain_errors = 0
        self._log_next = 0.0

    # -- show-brain -----------------------------------------------------------------------------------------------------
    def set_settings(self, settings: Any) -> None:
        self.settings = parse_settings(settings)

    def configure(self, nodes: Mapping[str, Mapping[str, Any]], used: set[str] | frozenset[str]) -> None:
        """The registry's nodes, and which of them an enabled output uses (only those are polled)."""
        cfg = {str(k): dict(v) for k, v in (nodes or {}).items() if isinstance(v, Mapping)}
        used = frozenset(str(u) for u in used if str(u) in cfg)
        for nid in set(self._st) - used:                # no longer used: its failsafe goes again at the next first use
            st = self._st[nid]
            st.failsafe_sent, st.next_poll = None, -math.inf
        self._cfg, self._used = cfg, used
        self._rebuild()

    def claim(self, output_id: str, node_id: str, packet_fn: Callable[[], Optional[bytes]]) -> None:
        """An enabled output sends to ``node_id``; ``packet_fn`` gives its last packet (record_failsafe)."""
        self._claims = {**self._claims, output_id: (node_id, packet_fn)}

    def unclaim(self, output_id: str) -> None:
        """The output stopped (or was switched off). The socket stays open: an idle UDP socket costs nothing, and a
        reload that reconfigures outputs one by one must not change our source port under replies in flight."""
        if output_id in self._claims:
            self._claims = {k: v for k, v in self._claims.items() if k != output_id}

    def close(self) -> None:
        """Close the sockets we opened (a test's own socket is left to the test)."""
        with self._sock_lock:
            if self._own_sock and self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
            if self._reply_sock is not None:
                try:
                    self._reply_sock.close()
                except OSError:
                    pass
                self._reply_sock, self._reply_bound = None, None

    def tick(self, now_s: float) -> None:
        """1 Hz on show-brain: failsafe ArtAddress and ArtPolls that are due (used nodes only), then drain replies.
        Several artnet drivers may tick it; each poll goes out only when it is due."""
        self._ensure_reply_sock(now_s)
        cfg, dests = self._cfg, self._dests
        for nid in sorted(self._used):
            n, d = cfg.get(nid), dests.get(nid)
            if n is None or d is None:
                continue
            st = self._state(nid)
            mode = n.get("failsafe") if n.get("failsafe") in AC_FAIL else None
            if d.virtual:
                st.failsafe_sent = mode                 # nothing to program on a virtual node
                continue
            if d.ip is None:
                continue
            if mode is not None and st.failsafe_sent != mode:
                if self._sendto(nid, build_artaddress(AC_FAIL[mode]), (d.ip, d.port)):
                    st.failsafe_sent = mode
                    log.info("Art-Net: node %s failsafe set to %s (ArtAddress to %s)", nid, mode, d.ip)
            elif mode is None:
                st.failsafe_sent = None
            if now_s >= st.next_poll:
                st.next_poll = now_s + self.settings.poll_s
                if self._sendto(nid, ARTPOLL, (d.ip, d.port)):
                    st.polls += 1
        self._drain(self._sock)
        self._drain(self._reply_sock)

    def on_reply(self, data: bytes, addr: tuple[str, int]) -> None:
        """One packet that may be an ArtPollReply (our socket, the reply_bind socket, or any other source)."""
        r = parse_artpollreply(data)
        if r is None:
            self.junk += 1
            return
        ip = unicast_ip(r["ip"]) or unicast_ip(addr[0] if addr else None) or r["ip"]
        now = self._mono()
        remember_reply(r, ip)                           # for GET /api/v1/rig/nodes/find
        hit = False
        for nid, n in self._cfg.items():
            d = self._dests.get(nid)
            if d is None or d.virtual:
                continue
            m = _match(n.get("match") or {}, r)
            at_its_ip = d.ip is not None and ip == d.ip
            if m is True or (m is None and at_its_ip):
                self._record(nid, r, ip, now, matched=True)
                if m is True:
                    self._found_at(nid, n, ip, r)
                hit = True
            elif m is False and at_its_ip:
                self._record(nid, r, ip, now, matched=False)
                hit = True
        if not hit:
            self.unmatched += 1
            self.last_unmatched = {"ip": ip, "short_name": r["short_name"], "mac": r["mac"]}

    def record_failsafe(self, node_id: str, frames: Optional[list[bytes]] = None) -> dict:
        """Send the node's current universes once more (``frames``, else every claiming output's last packet), then
        ArtAddress AcFailRecord, so the node stores what shows now as its failsafe scene. Nothing records on its own."""
        n = self._cfg.get(node_id)
        if n is None:
            return {"recorded": False, "why": f"unknown node {node_id}"}
        d = self._dests.get(node_id)
        if d is None or d.virtual:
            return {"recorded": False, "why": "a virtual node: nothing to record on"}
        if d.ip is None:
            return {"recorded": False, "why": "no address: set last_ip in fixtures.yaml"}
        if frames is None:
            frames = []
            for nid, fn in self._claims.values():
                if nid == node_id:
                    try:
                        p = fn()
                    except Exception:
                        p = None
                    if p:
                        frames.append(p)
        for p in frames:
            self._sendto(node_id, p, (d.ip, d.port))
        ok = self._sendto(node_id, build_artaddress(AC_FAIL_RECORD), (d.ip, d.port))
        log.info("Art-Net: node %s: failsafe scene %s (%d universes resent)", node_id,
                 "recorded" if ok else "NOT recorded", len(frames))
        return {"recorded": ok, "why": None if ok else f"send failed: {self._state(node_id).last_error}",
                "frames": len(frames)}

    # -- any thread -----------------------------------------------------------------------------------------------------
    def dest(self, node_id: str) -> Optional[tuple[str, int]]:
        """Where frames go now; None: virtual, unknown, or no address."""
        d = self._dests.get(node_id)
        return None if d is None or d.virtual or d.ip is None else (d.ip, d.port)

    def is_virtual(self, node_id: str) -> bool:
        d = self._dests.get(node_id)
        return bool(d is not None and d.virtual)

    def send(self, node_id: str, packet: bytes | bytearray | memoryview) -> bool:
        """A runner's frame: kept (a virtual node) or sent unicast. False: not sent (unknown node, no address, a
        refused or failed send). Never to a loopback address on 6454."""
        d = self._dests.get(node_id)
        if d is None:
            return False
        if d.virtual:
            b = bytes(packet)
            pa = (b[15] & 0x7F) << 8 | b[14] if len(b) > 15 else 0
            kept = self._kept.get(node_id)
            if kept is None:
                kept = self._kept.setdefault(node_id, {})
            kept[pa] = (b, self._mono())
            self._state(node_id).kept += 1
            return True
        if d.ip is None:
            return False
        return self._sendto(node_id, packet, (d.ip, d.port))

    def status(self, node_id: str) -> contract.NodeStatus:
        n = self._cfg.get(node_id)
        d = self._dests.get(node_id)
        st = self._st.get(node_id) or _NodeState()
        last = st.last or {}
        now = self._mono()
        age = None if st.reply_at is None else round(now - st.reply_at, 1)
        found = self._found_for(node_id, n) if n is not None else None
        if n is None:
            detail: Optional[str] = f"unknown node {node_id}: not in fixtures.yaml nodes"
        elif self._problems.get(node_id):
            detail = self._problems[node_id][0]
        elif found is not None:
            detail = f"moved: update last_ip ({n.get('last_ip') or 'unset'} -> {found['ip']}; nodes.json)"
        elif st.matched is False and st.mismatch:
            detail = st.mismatch
        elif d is not None and d.virtual:
            detail = "virtual: frames kept in memory, never sent (previz)"
        elif d is None or d.ip is None:
            detail = "no address: set last_ip in fixtures.yaml"
        elif node_id not in self._used:
            detail = "not polled: no enabled output uses it"
        elif st.replies == 0:
            detail = NO_REPLIES
        elif age is not None and age > self.settings.silent_after_s:
            detail = f"silent: no ArtPollReply for {age:.0f} s (frames keep going)"
        else:
            detail = None
        return {"id": node_id, "ip": d.ip if d is not None and not d.virtual else None,
                "ip_from": d.ip_from if d is not None else "none", "matched": st.matched, "replies": st.replies,
                "reply_age_s": age, "short_name": last.get("short_name"), "long_name": last.get("long_name"),
                "mac": last.get("mac"), "refresh_hz": last.get("refresh_hz"), "failsafe": last.get("failsafe"),
                "merging": last.get("merging"), "detail": detail}

    def snapshot(self) -> dict:
        """Every configured node's NodeStatus, plus the socket side (for the rig's snapshot)."""
        return {"nodes": {nid: self.status(nid) for nid in sorted(self._cfg)}, "used": sorted(self._used),
                "reply_bind": (f"{self._reply_bound[0]}:{self._reply_bound[1]}" if self._reply_bound else None),
                "reply_bind_error": self._reply_error, "problems": list(self.settings.problems),
                "junk": self.junk, "unmatched": self.unmatched, "last_unmatched": self.last_unmatched,
                "drain_errors": self.drain_errors}

    def dmx_view(self) -> dict:
        """The virtual nodes' universes, decoded from our own packets (previz): ``{node: {port_address: {seq, length,
        channels, age_s}}}``."""
        now = self._mono()
        out: dict[str, dict] = {}
        for nid, kept in dict(self._kept).items():
            u: dict[str, Any] = {}
            for pa, (pkt, t) in dict(kept).items():
                p = decode_artdmx(pkt)
                if p is not None:
                    u[str(pa)] = {"seq": p["seq"], "length": p["length"], "channels": list(p["data"]),
                                  "age_s": round(now - t, 3)}
            out[nid] = u
        return out

    def kept(self, node_id: str, port_address: int) -> Optional[bytes]:
        """A virtual node's last packet for a universe."""
        e = (self._kept.get(node_id) or {}).get(port_address)
        return e[0] if e else None

    # -- internals ------------------------------------------------------------------------------------------------------
    def _state(self, nid: str) -> _NodeState:
        st = self._st.get(nid)
        if st is None:
            st = self._st.setdefault(nid, _NodeState())
        return st

    def _found_for(self, nid: str, n: Mapping[str, Any]) -> Optional[dict]:
        """nodes.json's address for the node, while fixtures.yaml's last_ip is still the one it was found against."""
        f = self._found.get(nid)
        if not isinstance(f, dict) or f.get("config_ip") != (n.get("last_ip") or None):
            return None
        return f if unicast_ip(f.get("ip")) else None

    def _rebuild(self) -> None:
        dests: dict[str, _Dest] = {}
        problems: dict[str, tuple[str, ...]] = {}
        for nid, n in self._cfg.items():
            pr: list[str] = []
            port = n.get("port", PORT)
            if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
                pr.append(f"nodes.{nid}: port {port!r} isn't 1..65535: using {PORT}")
                port = PORT
            if n.get("virtual") is True:
                dests[nid] = _Dest(None, port, True, "virtual")
                continue
            raw_ip = n.get("last_ip")
            ip = unicast_ip(raw_ip) if raw_ip not in (None, "") else None
            if raw_ip not in (None, "") and ip is None:
                pr.append(f"nodes.{nid}: last_ip {raw_ip!r} isn't a unicast IPv4 address: nothing sent")
            if ip is not None and is_room_receiver(ip, port):
                pr.append(f"nodes.{nid}: {ip}:{port} is room_service's own receiver: treated as virtual "
                          "(nothing sent)")
                dests[nid] = _Dest(None, port, True, "virtual")
                problems[nid] = tuple(pr)
                continue
            f = self._found_for(nid, n)
            if f is not None and not is_room_receiver(f["ip"], port):
                dests[nid] = _Dest(f["ip"], port, False, "discovery")
            elif ip is not None:
                dests[nid] = _Dest(ip, port, False, "config")
            else:
                dests[nid] = _Dest(None, port, False, "none")
            if pr:
                problems[nid] = tuple(pr)
        self._dests, self._problems = dests, problems

    def _sendto(self, nid: str, packet: bytes | bytearray | memoryview, addr: tuple[str, int]) -> bool:
        st = self._state(nid)
        if is_room_receiver(addr[0], addr[1]):             # the rule, again at the socket
            st.refused += 1
            return False
        try:
            s = self._socket()
            s.sendto(packet, addr)
            st.sent += 1
            return True
        except OSError as e:                                # no route, buffer full, interface down: carry on
            st.send_errors += 1
            st.last_error = f"{type(e).__name__}: {e}"
            now = time.monotonic()
            if now >= self._log_next:
                self._log_next = now + SEND_LOG_S
                log.warning("Art-Net: send to node %s (%s:%d) failed: %s (logged at most every %.0f s)", nid,
                            addr[0], addr[1], st.last_error, SEND_LOG_S)
            return False

    def _socket(self) -> Any:
        s = self._sock
        if s is not None:
            return s
        with self._sock_lock:
            if self._sock is None:
                self._sock = _udp_socket()
                self._own_sock = True
            return self._sock

    def _ensure_reply_sock(self, now_s: float) -> None:
        want = self.settings.reply_bind
        if want == self._reply_bound and (want is None or self._reply_sock is not None):
            return
        if self._reply_sock is not None:
            try:
                self._reply_sock.close()
            except OSError:
                pass
            self._reply_sock, self._reply_bound = None, None
        if want is None:
            self._reply_error = None
            return
        if now_s < self._reply_retry:
            return
        try:
            self._reply_sock = _bound_socket(*want)
            self._reply_bound, self._reply_error = want, None
            log.info("Art-Net: ArtPollReplies also read on udp %s:%d (rig.artnet.reply_bind)", *want)
        except OSError as e:
            self._reply_error = f"{type(e).__name__}: {e}"
            self._reply_retry = now_s + SEND_LOG_S
            log.warning("Art-Net: reply_bind %s:%d not bound: %s (retried in %.0f s)", want[0], want[1], e,
                        SEND_LOG_S)

    def _drain(self, s: Any) -> None:
        if s is None:
            return
        for _ in range(DRAIN_MAX):
            try:
                data, addr = s.recvfrom(2048)
            except (BlockingIOError, InterruptedError):
                return
            except ConnectionRefusedError:                  # an ICMP error queued by an earlier send
                continue
            except OSError:
                self.drain_errors += 1
                return
            if len(data) >= 10 and data[:8] == ARTNET_ID and data[8:10] == OP_POLL_REPLY.to_bytes(2, "little"):
                self.on_reply(data, addr)
            else:
                self.junk += 1

    def _record(self, nid: str, r: dict, ip: str, now: float, *, matched: bool) -> None:
        st = self._state(nid)
        st.replies += 1
        st.reply_at, st.reply_ip, st.last, st.matched = now, ip, r, matched
        if matched:
            st.mismatch = None
        else:
            want = self._cfg.get(nid, {}).get("match") or {}
            st.mismatch = (f"the node at {ip} answered as {r['short_name']!r} ({r['mac'] or 'no MAC'}), not "
                           f"{_match_words(want)}: frames keep going")

    def _found_at(self, nid: str, n: Mapping[str, Any], ip: str, r: dict) -> None:
        """A matching reply: at last_ip, forget any discovery; elsewhere, use that address (nodes.json)."""
        cfg_ip = unicast_ip(n.get("last_ip")) if n.get("last_ip") else None
        port = n.get("port", PORT) if isinstance(n.get("port"), int) else PORT
        if ip == cfg_ip:
            if nid in self._found:
                self._found = {k: v for k, v in self._found.items() if k != nid}
                self._save_state()
                self._rebuild()
                log.info("Art-Net: node %s answers at its last_ip %s again", nid, ip)
            return
        if unicast_ip(ip) is None or is_room_receiver(ip, port):
            self._problems = {**self._problems, nid: (f"nodes.{nid}: a matching reply says {ip}, which can't be "
                                                      "sent to: ignored",)}
            return
        cur = self._found.get(nid) or {}
        if cur.get("ip") == ip and cur.get("config_ip") == (n.get("last_ip") or None):
            return
        self._found = {**self._found, nid: {"ip": ip, "config_ip": n.get("last_ip") or None, "mac": r.get("mac"),
                                            "short_name": r.get("short_name"), "at": round(self._wall(), 3)}}
        self._save_state()
        self._rebuild()
        log.warning("Art-Net: node %s answered from %s, not its last_ip %s: sending there (moved: update last_ip "
                    "in fixtures.yaml)", nid, ip, n.get("last_ip"))

    def _load_state(self) -> dict[str, dict]:
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            log.warning("Art-Net: %s not read (%s): starting from fixtures.yaml's addresses", self.state_file, e)
            return {}
        nodes = raw.get("nodes") if isinstance(raw, dict) else None
        return {str(k): dict(v) for k, v in (nodes or {}).items() if isinstance(v, dict)}

    def _save_state(self) -> None:
        """nodes.json, atomically (tmp in the same directory, fsync, os.replace). A failure keeps it in memory."""
        path = self.state_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "nodes": self._found}, f, indent=1, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError as e:
            log.warning("Art-Net: %s not written (%s): the discovered address is kept in memory", path, e)


def _match(crit: Mapping[str, Any], r: Mapping[str, Any]) -> Optional[bool]:
    """A node's ``match`` against a reply: by MAC, else short name (case-insensitive); None when it names neither."""
    mac = norm_mac(crit.get("mac")) if crit.get("mac") else None
    if mac is not None:
        return r.get("mac") == mac
    sn = crit.get("short_name")
    if isinstance(sn, str) and sn.strip():
        return str(r.get("short_name") or "").strip().casefold() == sn.strip().casefold()
    return None


def _match_words(crit: Mapping[str, Any]) -> str:
    if crit.get("mac"):
        return f"MAC {crit['mac']}"
    if crit.get("short_name"):
        return f"short name {crit['short_name']!r}"
    return "anything"


# -- the driver ---------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class _Universe:
    """What configure compiles for the runner, swapped by reference."""
    port_address: Optional[int]
    node_id: Optional[str]
    length: int
    base: bytes                                      # 512: defaults, then pins
    spans: tuple[tuple[str, int, int], ...]          # (fixture id, 0-based offset, footprint), by address
    lead_s: float
    lead_from: str
    max_hz: float
    gap_s: float
    keepalive_s: float
    idle_dt: float
    why: Optional[str]                               # no_config: why nothing can be sent
    problems: tuple[str, ...] = field(default=())


def _clamp_byte(v: Any) -> int:
    try:
        x = int(round(float(v)))
    except (TypeError, ValueError):
        return 0
    return 0 if x < 0 else 255 if x > 255 else x


def _num(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return float(v)
    return None


def output_lead(out: Mapping[str, Any], fixtures: list[Mapping[str, Any]]) -> tuple[float, str]:
    """The output's intensity rise latency in ms and where it came from: latency.json under its ``latency_key`` (as
    fixtures.py resolved it for a fixture that has no measurement of its own), else the hand-set
    ``latency.intensity.rise_ms``, else 0 ("unset")."""
    key = out.get("latency_key") or out.get("id")
    prefix = f"latency.json outputs.{key}."
    for f in fixtures:
        lv = f.get("latency") or {}
        src = (lv.get("source") or {}).get("rise")
        v = _num(lv.get("rise_ms"))
        if isinstance(src, str) and src.startswith(prefix) and v is not None:
            return max(0.0, v), src
    hand = _num(((out.get("latency") or {}).get("intensity") or {}).get("rise_ms"))
    if hand is not None and hand >= 0:
        return hand, "fixtures.yaml"
    return 0.0, "unset"


class ArtNetLan:
    """contract.Driver, kind ``artnet``: one universe on one node. Module docstring."""

    kind = "artnet"
    caps: contract.DriverCaps = CAPS

    def __init__(self, output_id: str, *, ctx: Any = None, clock: Any = None, settings: Any = None,
                 shared: Optional[dict] = None, mono: Callable[[], float] = time.monotonic,
                 nodes: Optional[Nodes] = None, sleep: Callable[[float], None] = time.sleep, **kw: Any) -> None:
        self.output_id = output_id
        self.ctx = ctx
        self.clock = clock
        self._mono = mono
        self._sleep = sleep
        self.settings = parse_settings(settings)
        if nodes is None:
            shared = shared if shared is not None else {}
            nodes = shared.get(SHARED_KEY)
            if nodes is None:
                nodes = Nodes(settings=settings, mono=mono, state_file=kw.get("state_file"), sock=kw.get("sock"))
                shared[SHARED_KEY] = nodes
        self.nodes: Nodes = nodes
        self.caps = dict(CAPS)                          # type: ignore[assignment]
        self._clock_kw = self._clock_takes_lead(clock)
        self._u: Optional[_Universe] = None
        self._enabled = False
        self._running = False
        # the runner's own (show-out-<id>)
        self._buf = bytearray(build_artdmx(1, bytes(UNIVERSE_SIZE), 1))
        self._data = memoryview(self._buf)[HEADER:]
        self._buf_pa = 1
        self.seq = 0
        self._sent_at: Optional[float] = None           # when the last frame actually left
        self._planned: Optional[float] = None
        self._eval_at: Optional[float] = None           # the last set_frame call (idle plans count from it)
        self._upcoming: Optional[float] = None          # the beat fire time the planned frame may carry
        self._last_fire: Optional[float] = None         # the last beat fire time a frame carried
        self._period: Optional[float] = None
        self._hit = False                               # any thread: pull the next frame forward
        self._asap = False                              # a frame was held back by the spacing rule
        self._last_data: Optional[bytes] = None
        self._last_packet: Optional[bytes] = None
        # stats (written by the runner, read anywhere)
        self.frames = 0
        self.keepalives = 0
        self.skipped = 0                                # idle evaluations with nothing new to send
        self.too_soon = 0
        self.send_errors = 0
        self._err_run = 0
        self.last_send_error: Optional[str] = None
        self.callback_errors = 0
        self._log_next = 0.0
        self._late = [0.0] * RING
        self._late_n = 0
        self._sends = [-math.inf] * RING
        self._sends_n = 0

    @staticmethod
    def _clock_takes_lead(clock: Any) -> bool:
        """clock.Clock.next_beat(t, lead_s=, gate=); artnet_out's BeatClock is next_beat(t) only."""
        fn = getattr(clock, "next_beat", None)
        if fn is None:
            return False
        try:
            ps = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            return False
        return "lead_s" in ps and "gate" in ps

    # -- show-brain -----------------------------------------------------------------------------------------------------
    def set_settings(self, settings: Any) -> None:
        """show.yaml rig.artnet changed (hot reload): this driver's and the shared Nodes'."""
        self.settings = parse_settings(settings)
        self.nodes.set_settings(settings)
        u = self._u
        if u is not None:
            self._u = dataclasses.replace(u, keepalive_s=self.settings.keepalive_s,
                                          idle_dt=1.0 / self.settings.idle_eval_hz)
            self.caps = {**self.caps, "keepalive_s": self.settings.keepalive_s}   # type: ignore[assignment]

    def configure(self, out: Mapping[str, Any], rig: Mapping[str, Any]) -> None:
        """Compile the universe for the runner (swapped by reference) and hand the registry's nodes to Nodes."""
        out = out or {}
        rig = rig or {}
        nodes = rig.get("nodes") or {}
        used = {o.get("node") for o in (rig.get("outputs") or {}).values()
                if isinstance(o, Mapping) and o.get("driver") == self.kind and o.get("enabled") is True
                and o.get("node")}
        self.nodes.configure(nodes, used)
        self._enabled = out.get("enabled") is True
        problems: list[str] = []
        why: Optional[str] = None
        pa = out.get("port_address")
        if not isinstance(pa, int) or isinstance(pa, bool) or not 1 <= pa < 1 << 15:
            why = f"port_address {pa!r}: needs 1..32767 (0 is room_service's own universe)"
            pa = None
        node_id = out.get("node") or None
        node = nodes.get(node_id) if node_id else None
        if node_id is None:
            why = why or f"no node: set outputs.{self.output_id}.node in fixtures.yaml"
        elif not isinstance(node, Mapping):
            why = why or f"node {node_id} isn't in fixtures.yaml nodes"
        types = rig.get("types") or {}
        mine = sorted((f for f in (rig.get("fixtures") or {}).values()
                       if isinstance(f, Mapping) and f.get("output") == self.output_id),
                      key=lambda f: (f.get("address") or 0, str(f.get("id"))))
        base = bytearray(UNIVERSE_SIZE)
        spans: list[tuple[str, int, int]] = []
        hi = 0
        for f in mine:
            fid = str(f.get("id"))
            t = types.get(f.get("type")) or {}
            fp = int(t.get("footprint") or 0)
            if fp <= 0:
                continue
            a = f.get("address")
            if not isinstance(a, int) or isinstance(a, bool) or a < 1 or a + fp - 1 > UNIVERSE_SIZE:
                problems.append(f"fixtures.{fid}: address {a!r} + footprint {fp} doesn't fit 1..512: left out")
                continue
            off = a - 1
            names: dict[str, int] = {}
            for ch in t.get("channels") or []:
                o = ch.get("offset")
                if isinstance(o, int) and 0 <= o < fp:
                    base[off + o] = _clamp_byte(ch.get("default", 0))
                    names.setdefault(str(ch.get("name")), o)
            for name, v in (f.get("pin") or {}).items():
                if name in names:
                    base[off + names[name]] = _clamp_byte(v)
                else:
                    problems.append(f"fixtures.{fid}: pin {name!r} isn't a channel of type {f.get('type')}")
            spans.append((fid, off, fp))
            hi = max(hi, off + fp)
        full = bool(isinstance(node, Mapping) and node.get("full_512") is True)
        length = UNIVERSE_SIZE if full else max(2, hi + (hi & 1))
        lead_ms, lead_from = output_lead(out, list(mine))
        rate = _num(out.get("rate_hz"))
        # a rate cap keeps the grid's own slack over its gap, so a frame that leaves a little late doesn't push the
        # next grid point (README S5.14: rate_hz 40 at 120 bpm would otherwise tick at exactly the gap)
        max_hz = min(GRID_HZ, 1.0 / (1.0 / rate + GRID_SLACK_S)) if rate is not None and rate > 0 else GRID_HZ
        gap = max(MIN_GAP_S, 1.0 / rate) if rate is not None and rate > 0 else MIN_GAP_S
        caps = dict(CAPS)
        if out.get("hits") is False:
            caps["hits"] = False                         # it can only turn hits off
        mam = _num(out.get("min_attack_ms"))
        if mam is not None and mam > caps["min_attack_ms"]:
            caps["min_attack_ms"] = mam                  # it can only raise the floor
        caps["rate_hz"] = min(MAX_HZ, rate) if rate is not None and rate > 0 else MAX_HZ
        caps["keepalive_s"] = self.settings.keepalive_s
        self.caps = caps                                 # type: ignore[assignment]
        self._u = _Universe(port_address=pa, node_id=node_id, length=length, base=bytes(base), spans=tuple(spans),
                            lead_s=lead_ms / 1000.0, lead_from=lead_from, max_hz=max_hz, gap_s=gap,
                            keepalive_s=self.settings.keepalive_s, idle_dt=1.0 / self.settings.idle_eval_hz,
                            why=why, problems=tuple(problems))
        if self._enabled and node_id is not None:
            self.nodes.claim(self.output_id, node_id, self.last_packet)
        else:
            self.nodes.unclaim(self.output_id)

    def start(self) -> None:
        self._running = True

    def stop(self, timeout: float = 2.0) -> None:
        """Nothing more is sent; each node's own failsafe takes over after its timeout."""
        self._running = False
        self.nodes.unclaim(self.output_id)

    def tick(self, now_s: float) -> None:
        self.nodes.tick(now_s)

    def record_failsafe(self) -> dict:
        """AcFailRecord on this output's node (Nodes.record_failsafe with every universe that node carries)."""
        u = self._u
        if u is None or u.node_id is None:
            return {"recorded": False, "why": "no node"}
        return self.nodes.record_failsafe(u.node_id)

    # -- any thread -----------------------------------------------------------------------------------------------------
    def on_hit(self, d: Any = None) -> None:
        """A music.hit for this output: the next ``plan`` (while active) answers max(now, last + gap)."""
        if self.caps.get("hits"):
            self._hit = True

    # -- the runner (show-out-<id>) -------------------------------------------------------------------------------------
    def plan(self, t_last_s: float, now_s: float, active: bool) -> float:
        """The next send time (monotonic s). Module docstring, "The frame grid"."""
        u = self._u
        gap = u.gap_s if u is not None else MIN_GAP_S
        sent = self._sent_at
        ref = t_last_s if sent is None else max(t_last_s, sent)
        earliest = now_s if sent is None else max(now_s, sent + gap)
        pull = self._hit or self._asap
        self._hit = self._asap = False
        self._upcoming = None
        sendable = u is not None and u.why is None and self._enabled and self._running
        if not sendable or not active:
            # idle: the next look check, anchored on the last evaluation (a stale t_last never spins the runner)
            idle_dt = u.idle_dt if u is not None else 1.0 / IDLE_EVAL_HZ
            base = t_last_s if self._eval_at is None else max(t_last_s, self._eval_at)
            t = base + idle_dt
            if sendable and sent is not None:
                t = min(t, sent + u.keepalive_s)        # type: ignore[union-attr]
            t = max(t, earliest)
        elif pull:                                       # a hit (or a frame the gap held back): as soon as allowed,
            g = self._grid(ref, now_s, earliest, u)      # unless that would push the next beat frame late: then the
            fire = self._upcoming                        # flash rides the beat frame (c8's integration ruling, 16 Oct:
            if fire is not None and fire - earliest < u.gap_s:   # the beat frame leaves at t_beat - L, §5.4)
                t = g
            else:
                t = earliest
                self._upcoming = None
        else:
            t = self._grid(ref, now_s, earliest, u)      # type: ignore[arg-type]
        self._planned = t
        return t

    def plan_hit(self, t_last_s: float, now_s: float) -> float:
        """For a runner woken by a hit: max(now, last + gap), never under the gap from the last frame's send."""
        self._hit = True
        return self.plan(t_last_s, now_s, True)

    def _fire(self, after: float, lead: float) -> Optional[float]:
        """The first beat fire time (t_k - lead) at or after ``after``; None without a beat (gate under dmx)."""
        c = self.clock
        if c is None:
            return None
        try:
            tk = c.next_beat(after, lead_s=lead, gate=GATE) if self._clock_kw else c.next_beat(after + lead)
        except Exception:
            self.callback_errors += 1
            return None
        if tk is None:
            return None
        try:
            tk = float(tk)
        except (TypeError, ValueError):
            return None
        return tk - lead if math.isfinite(tk) else None

    def _grid(self, ref: float, now: float, earliest: float, u: _Universe) -> float:
        after = max(ref, now - u.gap_s)                  # a beat more than a gap gone is not chased
        if self._last_fire is not None and self._period:
            after = max(after, self._last_fire + REPHASE_SKIP * self._period)   # carried already: never twice
        fire = self._fire(after + 1e-6, u.lead_s)
        if fire is None:                                 # no beat: free-run
            return max(earliest, ref + 1.0 / u.max_hz)
        fire2 = self._fire(fire + u.gap_s, u.lead_s)
        if fire2 is not None and fire2 > fire:
            self._period = fire2 - fire
        self._upcoming = fire
        if fire <= earliest:                             # inside the gap: one gap later, never early
            return earliest
        tick = grid_tick(self._period, u.max_hz) if self._period else u.gap_s
        # the first grid point at or after earliest - SPACING_WAIT_S: one a little inside the gap (the last frame
        # left a little late) goes at the gap's end instead of a whole tick on (README S5.14); set_frame's wait of
        # up to SPACING_WAIT_S keeps the gap exact
        lo = earliest - SPACING_WAIT_S
        return max(earliest, fire - math.floor((fire - lo) / tick + 1e-9) * tick)

    def set_frame(self, frame: Mapping[str, Any]) -> None:
        """Build the universe in place and send it (a virtual node keeps it). Idle: only on a change or keepalive."""
        now = self._eval_at = self._mono()
        u = self._u
        if not self._running or not self._enabled or u is None or u.why is not None or u.node_id is None:
            return
        sent = self._sent_at
        if sent is not None:
            short = sent + u.gap_s - now
            if short > 1e-9:
                if short > SPACING_WAIT_S:              # never under the gap: the next plan sends it at once
                    self.too_soon += 1
                    self._asap = True
                    return
                self._sleep(short)
                now = self._mono()
        if self._buf_pa != u.port_address:
            pa = int(u.port_address or 0)
            self._buf[14], self._buf[15] = pa & 0xFF, (pa >> 8) & 0x7F
            self._buf_pa = pa
        n = u.length
        data = self._data
        data[:n] = u.base[:n]
        fx = frame.get("fixtures") or {}
        for fid, off, fp in u.spans:
            f = fx.get(fid)
            dmx = f.get("dmx") if f is not None else None
            if dmx is None:
                continue
            if not isinstance(dmx, (bytes, bytearray, memoryview)):
                dmx = bytes(_clamp_byte(v) for v in dmx)
            k = min(fp, len(dmx), n - off)
            if k > 0:
                data[off:off + k] = dmx[:k]
        cur = bytes(data[:n])
        active = bool(frame.get("active"))
        keepalive = False
        if not active and sent is not None and cur == self._last_data:
            if now - sent < u.keepalive_s - 1e-6:
                self.skipped += 1
                return
            keepalive = True
        self._buf[16], self._buf[17] = n >> 8, n & 0xFF
        self.seq = next_seq(self.seq)
        self._buf[12] = self.seq
        pkt = memoryview(self._buf)[:HEADER + n]
        ok = self.nodes.send(u.node_id, pkt)
        t_sent = self._mono()
        self._sent_at = t_sent
        if self._upcoming is not None and self._planned is not None and self._planned >= self._upcoming - 1e-9:
            self._last_fire = self._upcoming             # this frame carried that beat
        if self._planned is not None:
            self._late[self._late_n % RING] = (t_sent - self._planned) * 1000.0
            self._late_n += 1
        if ok:
            self.frames += 1
            self.keepalives += keepalive
            self._err_run = 0
            self._last_data = cur
            self._last_packet = bytes(pkt)
            self._sends[self._sends_n % RING] = t_sent
            self._sends_n += 1
        else:
            self.send_errors += 1
            self._err_run += 1
            st = self.nodes._st.get(u.node_id)
            self.last_send_error = (st.last_error if st is not None and st.last_error else
                                    "not sent: " + (self.nodes.status(u.node_id).get("detail") or "no address"))
            if t_sent >= self._log_next:
                self._log_next = t_sent + SEND_LOG_S
                log.warning("Art-Net out %s: frame not sent (%s; %d so far, logged at most every %.0f s)",
                            self.output_id, self.last_send_error, self.send_errors, SEND_LOG_S)

    # -- any thread -----------------------------------------------------------------------------------------------------
    def last_packet(self) -> Optional[bytes]:
        """The last packet sent (or kept), as sent."""
        return self._last_packet

    def universe_view(self) -> Optional[dict]:
        """For GET /api/v1/rig/frame's ``universe``: ``{port_address, length, channels, to, virtual, seq, age_s}``."""
        u = self._u
        if u is None:
            return None
        data = self._last_data
        d = self.nodes.dest(u.node_id) if u.node_id else None
        sent = self._sent_at
        return {"port_address": u.port_address, "length": u.length, "channels": list(data) if data else [],
                "to": f"{d[0]}:{d[1]}" if d else None,
                "virtual": bool(u.node_id and self.nodes.is_virtual(u.node_id)), "seq": self.seq,
                "age_s": None if sent is None else round(self._mono() - sent, 3)}

    def status(self) -> contract.OutputStatus:
        u = self._u
        now = self._mono()
        n = min(self._late_n, RING)
        late = sorted(self._late[:n]) if n else []
        sends = [t for t in self._sends if t > now - RATE_WINDOW_S]
        rate = 0.0
        if len(sends) >= 2:
            span = max(sends) - min(sends)
            rate = (len(sends) - 1) / span if span > 0 else 0.0
        node = self.nodes.status(u.node_id) if u is not None and u.node_id else None
        sent = self._sent_at
        if not self._enabled:
            state, detail = "off", f"outputs.{self.output_id}.enabled is false in config/room/fixtures.yaml"
        elif u is None:
            state, detail = "no_config", "not configured yet"
        elif u.why is not None:
            state, detail = "no_config", u.why
        elif node is not None and node["ip"] is None and node["ip_from"] != "virtual":
            state, detail = "no_config", node["detail"]
        elif self._err_run >= ERROR_RUN:
            state, detail = "error", self.last_send_error
        elif self._running and sent is not None and now - sent <= u.keepalive_s + 1.5:
            state, detail = "sending", (node or {}).get("detail")
        else:
            state, detail = "idle", (node or {}).get("detail")
        return {"id": self.output_id, "driver": self.kind, "enabled": self._enabled, "state": state,
                "detail": detail, "frames": self.frames, "rate_hz": round(rate, 1),
                "late_ms": {"p50": round(late[len(late) // 2], 2) if late else None,
                            "p95": round(late[min(len(late) - 1, int(0.95 * len(late)))], 2) if late else None},
                "errors": self.send_errors, "last_frame_age_s": None if sent is None else round(now - sent, 3),
                "extra": {"node": node, "port_address": u.port_address if u else None,
                          "length": u.length if u else None, "seq": self.seq,
                          "lead_ms": round(u.lead_s * 1000.0, 1) if u else None,
                          "lead_from": u.lead_from if u else None,
                          "period_ms": round(self._period * 1000.0, 1) if self._period else None,
                          "keepalives": self.keepalives, "skipped": self.skipped, "too_soon": self.too_soon,
                          "last_send_error": self.last_send_error, "callback_errors": self.callback_errors,
                          "problems": list(u.problems) if u else [],
                          "caps": dict(self.caps)}}


# -- finding boxes on the LAN (GET /api/v1/rig/nodes/find: the Lights page's "Boxes found") ---------------------------
# WSL's NAT can't broadcast onto the LAN (module docstring), so finding is a unicast ArtPoll to every address of each
# LAN /24 (14 bytes each: about 3.5 kB for a /24), then the replies on a socket of its own for FIND_WAIT_S. A box that
# answers only on PC22's udp 6454 reaches room_service's receiver through ca's relay, not us: it's heard once
# rig.artnet.reply_bind is set (bound here for the search when no Nodes holds it, else heard through Nodes.on_reply).
FIND_WAIT_S = 1.5               # starting point: Art-Net 4 nodes answer an ArtPoll within 3 s, most within a few ms
FIND_MAX_WAIT_S = 5.0
FIND_PREFIX_MIN = 22            # at most a /22 (1022 addresses) per search
FIND_MAX_SUBNETS = 4
HEARD_KEEP_S = 600.0            # a reply Nodes heard in the last 10 minutes is listed too ("heard earlier")
HEARD_MAX = 64
ENV_FILE = contract.REPO_ROOT / ".env"     # SM_LAN_HOST: PC22's LAN address (as tools/show_sender reads it)
FIND_NONE = "No Art-Net box answered. Type the box's address instead."

_heard: dict[str, dict] = {}    # ip -> {reply, at (monotonic)}: every ArtPollReply Nodes parsed (any socket)
_heard_lock = threading.Lock()


def remember_reply(r: Mapping[str, Any], ip: str, now: Optional[float] = None) -> None:
    """Keep one parsed ArtPollReply for ``find_nodes`` (bounded; any thread)."""
    at = time.monotonic() if now is None else now
    with _heard_lock:
        _heard[ip] = {"reply": dict(r), "at": at}
        if len(_heard) > HEARD_MAX:
            for k in sorted(_heard, key=lambda k: _heard[k]["at"])[:len(_heard) - HEARD_MAX]:
                del _heard[k]


def lan_host(env: Mapping[str, str] | None = None, env_file: pathlib.Path = ENV_FILE) -> Optional[str]:
    """SM_LAN_HOST from the repo's .env, else the environment; None when neither gives an IPv4 address."""
    got = None
    try:
        for line in env_file.read_text().splitlines():
            if line.startswith("SM_LAN_HOST="):
                got = line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    if not got:
        got = (os.environ if env is None else env).get("SM_LAN_HOST")
    return unicast_ip(got) if got else None


def parse_subnet(s: Any) -> tuple[Optional[ipaddress.IPv4Network], Optional[str]]:
    """``192.168.1.0/24`` (or a bare address: its /24) -> (network, None); else (None, plain words). LAN ranges only."""
    raw = str(s).strip()
    try:
        net = ipaddress.IPv4Network(raw if "/" in raw else f"{raw}/24", strict=False)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError):
        return None, f"{raw!r} isn't a LAN range like 192.168.1.0/24"
    if not net.is_private or net.is_loopback or net.is_link_local or net.is_multicast:
        return None, f"{net} isn't a home network range: not searched"
    if net.prefixlen < FIND_PREFIX_MIN:
        return None, f"{net} is too big to search (at most /{FIND_PREFIX_MIN})"
    return net, None


def find_subnets(asked: Optional[list[str]] = None, near: tuple[Any, ...] = (),
                 lan: Optional[str] = None) -> tuple[list[ipaddress.IPv4Network], list[str]]:
    """What to search: the ranges asked for, else the /24 of PC22's LAN address and of every configured node's
    address. (networks, problems)."""
    nets: list[ipaddress.IPv4Network] = []
    problems: list[str] = []
    for s in (asked or []) if asked else [x for x in (lan, *near) if unicast_ip(x)]:
        net, why = parse_subnet(s)
        if why:
            if asked:
                problems.append(why)
            continue
        if net not in nets and not any(net.subnet_of(n) for n in nets):
            nets.append(net)
    if len(nets) > FIND_MAX_SUBNETS:
        problems.append(f"only the first {FIND_MAX_SUBNETS} ranges were searched")
        nets = nets[:FIND_MAX_SUBNETS]
    return nets, problems


def _found_row(r: Mapping[str, Any], ip: str, heard: str, age_s: float,
               nodes: Mapping[str, Mapping[str, Any]]) -> dict:
    node = None
    for nid, n in nodes.items():
        m = _match(n.get("match") or {}, r)
        if m is True or (m is None and unicast_ip(n.get("last_ip")) == ip):
            node = nid
            break
    name = r.get("short_name") or r.get("long_name") or ip
    return {"ip": ip, "name": name, "short_name": r.get("short_name"), "long_name": r.get("long_name"),
            "mac": r.get("mac"), "port": r.get("port") or PORT, "num_ports": r.get("num_ports"),
            "port_addresses": r.get("port_addresses"), "oem": r.get("oem"), "esta": r.get("esta"),
            "node_report": r.get("node_report"), "node": node, "heard": heard, "age_s": round(age_s, 1),
            "label": f"{name} ({ip})"}


def find_nodes(*, subnets: list[ipaddress.IPv4Network], wait_s: float = FIND_WAIT_S,
               skip: tuple[str, ...] = (), reply_bind: Optional[tuple[str, int]] = None,
               nodes: Mapping[str, Mapping[str, Any]] | None = None,
               sock_factory: Callable[[], Any] = _udp_socket, port: int = PORT,
               mono: Callable[[], float] = time.monotonic, problems: tuple[str, ...] = ()) -> dict:
    """ArtPoll every address of ``subnets`` (unicast, plus each range's broadcast where the route allows it), read the
    replies for ``wait_s``, and list the boxes that answered (with those Nodes heard in the last HEARD_KEEP_S).
    Blocking: run it off the show-bus loop. Never sends to room's receiver."""
    import select

    wait_s = max(0.2, min(FIND_MAX_WAIT_S, float(wait_s)))
    nodes = nodes or {}
    probs = list(problems)
    t0 = mono()
    socks: list[Any] = []
    polled = 0
    replies: dict[str, dict] = {}
    try:
        s = sock_factory()
        socks.append(s)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except (OSError, AttributeError):
            pass
        if reply_bind is not None:
            try:
                socks.append(_bound_socket(*reply_bind))
            except OSError:
                pass                                    # Nodes holds it: its replies come through remember_reply
        skipped = {unicast_ip(x) for x in skip}
        targets = [str(h) for net in subnets for h in net.hosts() if str(h) not in skipped]
        targets += [str(net.broadcast_address) for net in subnets]
        errors = 0
        for ip in targets:
            if is_room_receiver(ip, port):
                continue
            for _ in range(3):
                try:
                    s.sendto(ARTPOLL, (ip, port))
                    polled += 1
                    break
                except (BlockingIOError, InterruptedError):
                    time.sleep(0.002)                   # the send buffer is full: let it drain
                except OSError:
                    errors += 1                         # no route, a broadcast WSL refuses: carry on
                    break
        if errors and errors >= len(targets):
            probs.append("no ArtPoll could be sent (no route to the LAN?)")
        deadline = mono() + wait_s
        while socks and (polled or len(socks) > 1):     # nothing sent and no reply_bind: nothing to wait for
            left = deadline - mono()
            if left <= 0:
                break
            try:
                ready, _, _ = select.select(socks, [], [], left)
            except (OSError, ValueError):
                break
            for rs in ready:
                for _ in range(DRAIN_MAX):
                    try:
                        data, addr = rs.recvfrom(2048)
                    except (BlockingIOError, InterruptedError, ConnectionRefusedError):
                        break
                    except OSError:
                        break
                    r = parse_artpollreply(data)
                    if r is None:
                        continue
                    ip = unicast_ip(r["ip"]) or unicast_ip(addr[0] if addr else None) or str(r["ip"])
                    replies[ip] = r
                    remember_reply(r, ip)
    except OSError as e:
        probs.append(f"the search couldn't run: {type(e).__name__}: {e}")
    finally:
        for x in socks:
            try:
                x.close()
            except OSError:
                pass
    now = mono()
    rows = [_found_row(r, ip, "now", 0.0, nodes) for ip, r in replies.items()]
    with _heard_lock:
        heard = dict(_heard)
    for ip, h in heard.items():
        age = now - h["at"]
        if ip in replies or age > HEARD_KEEP_S:
            continue
        rows.append(_found_row(h["reply"], ip, "now" if h["at"] >= t0 else "earlier", max(0.0, age), nodes))
    rows.sort(key=lambda x: (x["heard"] != "now", tuple(int(p) for p in x["ip"].split(".")) if
                             unicast_ip(x["ip"]) else (999,)))
    return {"nodes": rows, "searched": [str(n) for n in subnets], "polled": polled, "wait_s": wait_s,
            "took_s": round(mono() - t0, 2), "problems": probs,
            "words": None if rows else (FIND_NONE if subnets else (probs[0] + ". Type the box's address instead.")
                                        if probs else "No LAN range to search (SM_LAN_HOST isn't in .env). Type the "
                                        "box's address instead.")}
