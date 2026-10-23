"""VBAN receiver: the UDP socket, SUBSCRIBE, the stream registry, gaps, segments, health and dispatch (docs/
SHOW_ENGINE.md §3.1, §3.3, §3.5; show_service/README.md "receiver.py" is the full contract).

Threads. ``show-rx`` owns everything here: it waits for a datagram for at most TICK_S (10 ms; 1 ms while a hole is
held; less while a held block's wait ends), drains what else is queued (up to RX_BATCH_MAX) into
:meth:`Receiver.handle_batch`, runs held-hole deadlines between wakes, and :meth:`Receiver.tick` every TICK_S.
``show-dns`` resolves each configured sender's host every 30 s (5 s while one fails; the last good address is kept)
into a dict that show-rx reads by reference: DNS never blocks show-rx. Callbacks (on_audio, on_midi, on_tick) run on
show-rx; an exception in one is counted and logged (rate-limited), never fatal. :meth:`health` and
:meth:`set_offsets` are for any thread.

Socket. ``cfg.vban_bind`` holds two UDP sockets in one SO_REUSEPORT group (Linux): A (audio, heartbeats, everything
else; bound first, so reuseport index 0) and B (our MIDI), with a classic BPF reuseport program (_MIDI_CBPF) that
sends TEXT packets whose JSON has ``seq"`` at byte 36 (vban.pack_midi_events / midi_state_obj: ``{"v":1,"seq"...``)
to B. A plain bind without SO_REUSEPORT is tried first and closed, so a port someone holds fails as before and a
second instance can't join. If B, the program or SO_REUSEPORT fails, A is bound alone, exactly as before (health
socket.text.split false + the error). Both sockets parse and dispatch any packet, so a misroute costs latency only;
audio on B (an overlapping process swapped the slots) closes both and binds them again in order (at most every 5 s;
socket.text.misrouted / rebinds). SO_RCVBUF is asked for 4 MB and read back (Linux caps it at net.core.rmem_max;
never SO_RCVBUFFORCE). A failed bind shows in health and is retried every 2 s. Every 1 s (and at once when an address
first resolves or changes, or the sender list changes) it sends ``vban.subscribe_packet(sender.streams, counter)``
from A to each sender's resolved address: the sender streams to the source of the latest one (NAT-safe).

Wakes (show-rx): select on both; B (when readable) is drained first and its MIDI dispatched at once, then A's batch;
B is polled again (non-blocking, about 3 us when empty) before each on_audio call once it has gone TEXT_POLL_S
(0.5 ms) unchecked, so a note waits at most one on_audio call (+ 0.5 ms). With one socket and user stamps the loop
is the old one (settimeout + recvfrom_into).

Stamps. Every datagram has two times. ``arrival``: the kernel's receive time (SO_TIMESTAMPNS, CLOCK_REALTIME, moved to
time.monotonic with the realtime - monotonic offset sampled once per wake; one outside [wake - 5 s, wake + 1 ms] is a
clock step: the wake is used and ts_bad counted; 50 datagrams in a row without one go back to user stamps), else
``clock()`` right after its recv ("user", as before; also whenever ``clock`` is not time.monotonic, i.e. tests). Every
timing model uses it: SampleClock / CaptureMap observe, hole hold and expiry, idle segments, meter bins, loss, the
off grace, RemoteTimeMap.observe and on_midi's ``arrival_s``. ``seen``: the wake (the user stamp in user mode), for
liveness only: stream states (stale / down / quiet / heartbeat freshness), the active stream, host answers and the
live meter's ``a``; so a backlog never reads as a stale stream (no flapping). SO_RXQ_OVFL gives the kernel's own drop
count. Health: socket.stamps, queue_ms (wake - the oldest kernel arrival per batch, ~2 s), kernel_drops, ts_bad;
per MIDI stream net.rx_lag_ms (dispatch - kernel arrival, ~10 s).

Blocks (contract item 1-3). Within one wake (handle_batch, and the hold expiries), contiguous real audio of the
active stream goes out as ONE on_audio call of up to COALESCE_MAX_FRAMES (4096) frames: n0 its first sample, t0 =
clock.to_host(n0) taken when its first packet was delivered (exactly the per-packet value), sample i at t0 + i/fs,
flags those of its first packet. A run ends at the end of the wake, a hole, a SEGMENT / RESUME / GAP flag, a stream,
rate, channel or n0 change, the size cap, or a clock self-step (later blocks then take the re-anchored map). AUDIO_GAP
(zero-fill, a read-only broadcast view of one cached zero row: consumers never write into pcm) and AUDIO_RESUME
blocks always go alone. ``coalesce_min`` > 1 holds a short run of real audio (fewer than that many packets) open
until it has them or its first packet has waited ``coalesce_min x frames / fs + 1 ms``; MIDI and control are never
held, and tick() always flushes first. :meth:`handle` (tests) stays one call per packet. Observation, metering, the
live meter and loss stay per packet. Health socket: blocks, block_packets (mean packets per real block, ~2 s),
block_frames_max (~2 s), coalesce, coalesce_min. Rollback switches (Receiver kwargs, each on by default; off is the
receiver before it): ``coalesce`` (one call per packet), ``kernel_stamps`` (user stamps), ``split_midi`` (one
socket).

Senders (Stage 3, README S3.10 "the receiver seam"). The list starts as ``cfg.senders``; :meth:`set_senders` (any
thread; sources.py, from config/room/show.yaml) replaces it at the next tick, and show-dns resolves the new hosts at
once. A removed sender gets no more SUBSCRIBEs; its streams stay in the registry and go down by the usual rules. A
sender's ``hosts`` (else ``(host,)``) is an ordered fallback list: every host is resolved; a host "answers" when a
packet arrives from its resolved address and port; while some host has answered within ANSWER_FRESH_S, SUBSCRIBE
goes to the first such host in list order only, otherwise to every resolved host. :meth:`set_priority` (any thread)
ranks those streams before ``cfg.stream_priority`` (which stays); the usual switch-up rule (3 s) applies.

Registry. One entry per stream name, made on its first valid packet, subscribed or not; ranked by
contract.stream_rank(name, cfg.stream_priority) (``--stream-priority`` names first, unknown names last), and
``test-*`` / contract.TEST_STREAMS are marked test. Kind: AUDIO makes it "audio", TEXT MIDI makes it "midi" (a
heartbeat alone leaves "unknown"). A heartbeat (TEXT named after its audio stream) updates
that stream and is not dispatched. SUBSCRIBE packets and junk are counted and dropped.

Audio (per stream; the receiver's own index ``n`` per segment, from 0; ``fill_max`` = packets in 250 ms):
  * counter k ahead of the next expected: k == 0 delivered; 1..fill_max a hole: later packets are held until it
    fills or ``hold = clamp(3 x clock.jitter_ms, 4, 40) ms`` passes after the hole was seen, then the hole goes out
    as one zero-fill block flagged AUDIO_GAP and the next real block carries AUDIO_RESUME; behind (a duplicate, or
    late for a hole already zero-filled) dropped and counted ``dup`` / ``late``; further either way a new segment.
  * new segment (``SampleClock.segment``, n = 0, the first block flagged AUDIO_SEGMENT, held packets flushed first):
    "counter", "format" (sr/channels/fmt/frames; fs passed), "sess" (heartbeat sess changed, keep_skew), "idle"
    (no packet for >= 1 s, or the heartbeat state left idle/no_device), "sender-step" (keep_skew; below).
    "counter"/"idle" keep the skew when the heartbeat sess is unchanged. A clock self-step ("step") re-syncs the
    CaptureMap and n continues.
  * ``clock.observe(n_first, arrival, frames)`` once per real packet at arrival (late ones included, duplicates
    and zero-fill never). A block's ``t0_host_s = clock.to_host(n0)`` at delivery; only the active stream's blocks
    are delivered, and the first after a switch carries AUDIO_SEGMENT.
  * heartbeat -> CaptureMap: ``n_rx = n_base + signed(counter_delta(c_base, frame)) x frames`` (dropped if < 0),
    ``ok = capture.observe(n_rx, t_cap_ns, seg)``. A "sender-step" segment starts at the next packet when ``xruns``
    rose within a sess, or when the sender's ``n - n_rx`` moved off its baseline (re-taken at the first heartbeat of
    each segment). ``ok`` False alone (the capture line moved, the samples didn't) is a ``capture_splices``: the
    CaptureMap starts over and the next real block carries AUDIO_RESUME, with no segment (18 Oct).
  * metering on every real packet (500 ms peak/RMS, clips at |x| >= 0.999, a 10 s floor of 100 ms RMS bins).

The mic on demand (README S5.16; mic.py decides). :meth:`Receiver.set_off_streams` (any thread) names the streams to
keep off: each sender's SUBSCRIBE lists them under ``off`` (device closed, off heartbeats) and the rest under
``subscribe`` (possibly empty), sent at once on a change. Their audio is dropped on arrival, before the registry's
meters, ``on_level`` or delivery, so nothing of it reaches DSP, levels, rings or the listener; packets within
OFF_GRACE_S of going off are expected stragglers, later ones mean the sender ignores the off (both counted in health).
Privacy at start: a configured stream that could be a mic (not MIDI, not a test stream, not an on-demand sender's)
is held off until a decision covers it, so nothing captures the room before the mic decision exists (the decision
is made before the first SUBSCRIBE; a sender added later waits for the next one).

MIDI. The stream's RemoteTimeMap observes the newest ``t_ns`` of a batch (or a state's ``t_ns``) at arrival; it is
attached to the best-ranked audio stream from the same source IP whose CaptureMap is ready (detached when that one
goes down), then ``on_midi(stream, msg, arrival_s, tmap.to_host)``; duplicates pass through (midi.py dedupes by
seq). ``set_offsets`` sets each stream's acoustic_offset_ms (missing = 0) on its SampleClock or RemoteTimeMap.

States (re-evaluated every 0.1 s): off (kept off here, or a heartbeat < 2 s old says "off" and no audio came after
it), no_device (a heartbeat < 2 s old says so), down (no data packet for > 5 s, or ever), stale (> 0.5 s), quiet
(audio: RMS under -60 dBFS for > 5 s; MIDI: no note or pedal for 30 s), else streaming. ``active_audio()``: the
best-ranked audio stream that is streaming or quiet; away from the current one at once when it goes off, else only
after it has been stale/down/no_device for 1 s; up only to one that has been streaming for 3 s; None only when no
audio stream is live. MIDI is never a fallback. Health (README "Health") is rebuilt at 2 Hz.
"""

from __future__ import annotations

import logging
import math
import select
import socket
import struct
import sys
import threading
import time
from collections import deque
from typing import Any, Callable, Mapping, Optional

import numpy as np

from . import contract, vban
from .contract import AUDIO_GAP, AUDIO_RESUME, AUDIO_SEGMENT, TICK_S, Config, OnAudio, OnMidi, OnTick
from .timeline import CaptureMap, RemoteTimeMap, SampleClock

log = logging.getLogger("show_service.receiver")

RCVBUF_WANT = 4 << 20
RECV_BUF = 2048
# Each wake drains up to this many datagrams that are already queued (non-blocking), then handles the batch's MIDI and
# control packets before its audio, so a note never waits behind a backlog of audio work: at most one batch (about
# 85 ms of audio at 128 frames). The batch's own sizes are in health (socket.batch_max, socket.backlogs).
RX_BATCH_MAX = 32
TEXT_BATCH_MAX = 64                              # the MIDI socket's drain per wake / poll
TEXT_POLL_S = 0.0005                             # B is polled before an on_audio call once it's this long unchecked
RCVBUF_TEXT = 1 << 20
COALESCE_MAX_FRAMES = 4096                       # one on_audio block's most frames (32 x 128: ~85 ms at 48 kHz)
STATS_WINDOW_S = 2.0                             # block_packets, block_frames_max, queue_ms
MIDI_LAG_WINDOW_S = 10.0                         # net.rx_lag_ms
KTS_BACK_S, KTS_AHEAD_S = 5.0, 0.001             # a kernel stamp outside [wake - 5 s, wake + 1 ms]: a clock step
KTS_MISS_MAX = 50                                # datagrams in a row without a kernel stamp: back to user stamps
REBIND_EVERY_S = 5.0                             # the misroute self-heal's most frequent rebind
BIND_RETRY_S = 2.0
SUBSCRIBE_EVERY_S = vban.SUBSCRIBE_EVERY_S       # 1 s
DNS_EVERY_S = 30.0
DNS_RETRY_S = 5.0
HOLD_MIN_MS, HOLD_MAX_MS, HOLD_JITTERS = 4.0, 40.0, 3.0
FILL_MAX_S = 0.25                                # a hole up to this long is held/zero-filled; more is a segment
IDLE_S = 1.0
STATES_EVERY_S = 0.1
HEALTH_EVERY_S = 0.5
STALE_S = 0.5
DOWN_S = 5.0
HB_FRESH_S = 2.0
QUIET_POWER = 1e-6                               # -60 dBFS mean square
QUIET_AUDIO_S = 5.0
QUIET_MIDI_S = 30.0
SWITCH_AWAY_S = 1.0
SWITCH_UP_S = 3.0
LOSS_WINDOW_S = 10.0
METER_BIN_S = 0.1
METER_S = 0.5
TRACE_BINS = 10            # the card's input trace: the last 10 closed METER_BIN_S bins (1 s at 10 Hz), with their seq
FLOOR_BINS = 100                                 # 10 s of 100 ms bins
CLIP_LEVEL = 0.999
DBFS_FLOOR = -120.0
MIDI_TEXT_GAP_MAX = 1000                         # TEXT counter steps up to this count as lost packets
ERROR_LOG_EVERY_S = 10.0
ANSWER_FRESH_S = 3.0                             # a host that sent a packet this recently gets the SUBSCRIBEs alone
OFF_GRACE_S = 1.0                                # audio of a stream this long after it went off: the sender ignores it
LIVE = ("streaming", "quiet")
BAD = ("stale", "down", "no_device")
_HALF = 1 << 31
_MOD = 1 << 32
_PEDAL_CCS = frozenset(contract.PEDAL_CC)

# Linux socket options CPython 3.12 doesn't export (asm-generic/socket.h, x86_64 / aarch64): SO_TIMESTAMPNS (_OLD; the
# cmsg type is the same number, a native struct timespec of CLOCK_REALTIME), SO_RXQ_OVFL (a u32 cmsg: the socket's
# receive-queue drops since bind, present once there are any), SO_ATTACH_REUSEPORT_CBPF (struct sock_fprog).
_LINUX = sys.platform.startswith("linux")
SO_TIMESTAMPNS = getattr(socket, "SO_TIMESTAMPNS", 35 if _LINUX else None)
SO_RXQ_OVFL = getattr(socket, "SO_RXQ_OVFL", 40 if _LINUX else None)
SO_ATTACH_REUSEPORT_CBPF = getattr(socket, "SO_ATTACH_REUSEPORT_CBPF", 51 if _LINUX else None)
_TIMESPEC = struct.Struct("@ll")                 # struct timespec {time_t tv_sec; long tv_nsec} ('qq' on x86_64)
_U32 = struct.Struct("@I")
ANC_SPACE = (socket.CMSG_SPACE(_TIMESPEC.size) + socket.CMSG_SPACE(4)) if hasattr(socket, "CMSG_SPACE") else 0
_CAN_SPLIT = _LINUX and SO_ATTACH_REUSEPORT_CBPF is not None and hasattr(socket, "SO_REUSEPORT")
# The reuseport program (classic BPF on the datagram, offsets from the UDP payload; returns the socket index): VBAN TEXT
# (byte 4 & 0xE0 == 0x40) whose bytes 36..39 are 'seq"' (our MIDI events and states: {"v":1,"seq"...}) -> 1 (B), all
# else -> 0 (A): audio, heartbeats (``"sess"``), SUBSCRIBE echoes, junk, short packets (a load past the end returns 0).
_MIDI_CBPF = ((0x30, 0, 0, 4),            # ldb [4]            the format byte
              (0x54, 0, 0, 0xE0),         # and #0xE0          its sub-protocol
              (0x15, 0, 3, 0x40),         # jeq #TEXT          else ret 0
              (0x20, 0, 0, 36),           # ld  [36]           4 bytes, big-endian
              (0x15, 0, 1, 0x73657122),   # jeq 'seq"'         else ret 0
              (0x06, 0, 0, 1),            # ret 1              the MIDI socket
              (0x06, 0, 0, 0))            # ret 0              the audio socket


def _attach_cbpf(sock: socket.socket, prog: tuple = _MIDI_CBPF) -> None:
    """Attach a classic BPF reuseport program to ``sock``'s SO_REUSEPORT group (raises OSError on refusal)."""
    import ctypes
    raw = b"".join(struct.pack("HBBI", *ins) for ins in prog)
    buf = ctypes.create_string_buffer(raw, len(raw))
    sock.setsockopt(socket.SOL_SOCKET, SO_ATTACH_REUSEPORT_CBPF, struct.pack("HP", len(prog), ctypes.addressof(buf)))


def _udp(rcvbuf: int, reuse: bool = False) -> socket.socket:
    """An unbound UDP socket, SO_RCVBUF asked for (best effort), SO_REUSEPORT when ``reuse`` (raises OSError)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if reuse:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        except OSError:
            pass
    except OSError:
        s.close()
        raise
    return s


def _bind_pair(host: str, port: int) -> tuple[Optional[tuple[socket.socket, socket.socket]], Optional[str]]:
    """A (bound first: reuseport index 0) and B on (host, port) with _MIDI_CBPF attached: ((a, b), None), else
    (None, why) with both closed."""
    a = b = None
    try:
        a = _udp(RCVBUF_WANT, reuse=True)
        a.bind((host, port))
        b = _udp(RCVBUF_TEXT, reuse=True)
        b.bind((host, a.getsockname()[1]))
        _attach_cbpf(a)
        b.setblocking(False)
        return (a, b), None
    except (OSError, ValueError, TypeError, struct.error, AttributeError) as e:
        for s in (b, a):
            if s is not None:
                s.close()
        return None, f"{type(e).__name__}: {e}"


def _probe(host: str, port: int) -> int:
    """Bind (host, port) without SO_REUSEPORT and close it: raises OSError when someone holds the port (as a plain bind
    would); returns the port (port 0: the free one the kernel picked)."""
    p = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        p.bind((host, port))
        return int(p.getsockname()[1])
    finally:
        p.close()


def _wake_off() -> tuple[float, int]:
    """(now on time.monotonic in s, CLOCK_REALTIME - CLOCK_MONOTONIC in ns), read once per wake."""
    m1 = time.monotonic_ns()
    r = time.clock_gettime_ns(time.CLOCK_REALTIME)
    m2 = time.monotonic_ns()
    return m2 / 1e9, r - ((m1 + m2) >> 1)


def kernel_arrival(anc: Any, wake_s: float, off_ns: int) -> tuple[float, int, Optional[int]]:
    """One datagram's arrival from its recvmsg ancillary data: (arrival_s, status, drops). The SO_TIMESTAMPNS stamp
    (CLOCK_REALTIME) minus ``off_ns`` (realtime - monotonic at the wake), on time.monotonic. status 0: that stamp; 1:
    a stamp outside [wake_s - 5 s, wake_s + 1 ms] (a clock step between the stamp and the wake): ``wake_s``; 2: no
    stamp: ``wake_s``. ``drops``: the SO_RXQ_OVFL count when present, else None."""
    arr: Optional[float] = None
    drops: Optional[int] = None
    for level, kind, data in anc:
        if level != socket.SOL_SOCKET:
            continue
        if kind == SO_TIMESTAMPNS and len(data) >= _TIMESPEC.size:
            sec, nsec = _TIMESPEC.unpack_from(data)
            arr = (sec * 1_000_000_000 + nsec - off_ns) / 1e9
        elif kind == SO_RXQ_OVFL and len(data) >= _U32.size:
            drops = _U32.unpack_from(data)[0]
    if arr is None:
        return wake_s, 2, drops
    if not wake_s - KTS_BACK_S <= arr <= wake_s + KTS_AHEAD_S:
        return wake_s, 1, drops
    return arr, 0, drops


def _pct(vals: list) -> Optional[dict]:
    """{p50, p95, max} of ``vals`` (nearest rank), None when empty."""
    if not vals:
        return None
    v = sorted(vals)
    n = len(v)
    return {"p50": _r(v[(n - 1) // 2], 2), "p95": _r(v[min(n - 1, max(0, math.ceil(0.95 * n) - 1))], 2),
            "max": _r(v[-1], 2)}


def is_test_stream(name: str) -> bool:
    return name in contract.TEST_STREAMS or name.startswith("test-")


def sender_hosts(s: contract.Sender) -> tuple[str, ...]:
    """The sender's ordered host list: ``hosts`` when given, else ``(host,)``."""
    return tuple(s.hosts) if s.hosts else (s.host,)


def _signed(d: int) -> int:
    return d - _MOD if d >= _HALF else d


def _dbfs(power: float) -> float:
    return DBFS_FLOOR if power <= 1e-12 else max(DBFS_FLOOR, 10.0 * math.log10(power))


def _peak_dbfs(peak: float) -> float:
    return round(DBFS_FLOOR if peak <= 1e-6 else max(DBFS_FLOOR, 20.0 * math.log10(peak)), 1)


def _r(x: Any, nd: int = 3) -> Any:
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), nd)


class _Stream:
    """One stream's registry entry (show-rx only)."""

    def __init__(self, name: str, addr: tuple[str, int], now: float, priority: tuple[str, ...] = ()) -> None:
        self.name = name
        self.kind = "unknown"
        self.rank = contract.stream_rank(name, priority)
        self.test = is_test_stream(name)
        self.addr = addr
        self.first_s = now
        self.last_s: Optional[float] = None              # any packet (heartbeats too); seen (liveness)
        self.first_data_s: Optional[float] = None
        self.last_data_s: Optional[float] = None         # audio / MIDI packets only; seen (liveness)
        self.last_arr: Optional[float] = None            # ... the same packet's arrival (timing: idle, loss)
        self.packets = 0
        self.bytes = 0
        self.state = "down"
        self.state_since = now
        # audio
        self.sr = self.channels = self.fmt = self.frames = 0
        self.clock: Optional[SampleClock] = None
        self.capture: Optional[CaptureMap] = None
        self.seg_id = 0
        self.seg_t = now
        self.seg_sess: Optional[str] = None              # heartbeat sess when the segment began
        self.segments = 0
        self.base_c = 0                                  # counter of the next packet to deliver ...
        self.base_n = 0                                  # ... and its first sample's index
        self.fill_max = 1
        self.pending: dict[int, tuple[vban.AudioPacket, float]] = {}
        self.flags = 0                                   # SEGMENT / RESUME for the next block delivered
        self.want_seg: Optional[tuple[str, bool]] = None
        self.late = self.dup = self.gaps = self.lost = 0
        self.filled: deque[int] = deque(maxlen=512)      # counters zero-filled lately (late vs dup)
        self.loss: deque[list] = deque()                 # [second, received, lost]
        # heartbeat
        self.hb: Optional[vban.Heartbeat] = None
        self.hb_s: Optional[float] = None                # seen (freshness)
        self.hb_arr: Optional[float] = None              # arrival (ordering against audio and segments)
        self.hb_idle = False
        self.ndiff: Optional[int] = None
        self.ndiff_seg = -1
        self.sender_steps = 0
        self.step_causes = {"xrun": 0, "capture": 0, "ndiff": 0}   # which heartbeat check found each sender step
        self.capture_splices = 0                         # capture-only steps: a splice (AUDIO_RESUME), not a segment
        # meter
        self.t_loud: Optional[float] = None
        self.clips = 0
        self.bin_t = now
        self.bin_peak = 0.0
        self.bin_ss = 0.0
        self.bin_n = 0
        self.bins: deque[tuple[float, float, int]] = deque(maxlen=FLOOR_BINS)   # (peak, sumsq, samples)
        self.bin_seq = 0                       # closed bins so far: the trace's seq (the newest bin's number)
        # midi
        self.tmap: Optional[RemoteTimeMap] = None
        self.attached: Optional[str] = None
        self.text_c: Optional[int] = None
        self.t_activity: Optional[float] = None
        self.lag: Optional[deque] = None                 # (seen, dispatch - arrival in ms) of each socket MIDI packet
        # the mic on demand: audio packets dropped while this stream was off (and those past OFF_GRACE_S)
        self.off_dropped = 0
        self.off_ignored = 0

    def count_loss(self, now: float, received: int, lost: int) -> None:
        sec = int(now)
        if self.loss and self.loss[-1][0] == sec:
            self.loss[-1][1] += received
            self.loss[-1][2] += lost
        else:
            self.loss.append([sec, received, lost])
            while self.loss and self.loss[0][0] <= sec - LOSS_WINDOW_S:
                self.loss.popleft()

    def loss_pct(self, now: float) -> float:
        rx = lost = 0
        for sec, r, l in self.loss:
            if sec > now - LOSS_WINDOW_S - 1:
                rx += r
                lost += l
        return 100.0 * lost / (rx + lost) if rx + lost else 0.0

    def hold_s(self) -> float:
        j = self.clock.jitter_ms if self.clock is not None else 0.0
        return min(HOLD_MAX_MS, max(HOLD_MIN_MS, HOLD_JITTERS * j)) / 1000.0


class _Run:
    """The open coalesced block (show-rx; one, reused): contiguous blocks of one stream for one on_audio call."""

    __slots__ = ("st", "n0", "t0", "fs", "ch", "flags", "n_end", "frames", "packets", "deadline", "parts")

    def __init__(self) -> None:
        self.st: Optional[_Stream] = None
        self.n0 = self.n_end = self.frames = self.packets = self.fs = self.ch = self.flags = 0
        self.t0 = self.deadline = 0.0
        self.parts: list[np.ndarray] = []


class _SockStat:
    """One socket's stamp state and counters (show-rx)."""

    __slots__ = ("kts", "ovfl", "ts_bad", "miss", "drops", "drops_base", "rx")

    def __init__(self) -> None:
        self.kts = False                                 # SO_TIMESTAMPNS on and trusted
        self.ovfl = False                                # SO_RXQ_OVFL on
        self.ts_bad = 0                                  # datagrams whose stamp was missing or out of range
        self.miss = 0                                    # datagrams in a row without a stamp
        self.drops = 0                                   # SO_RXQ_OVFL of this socket
        self.drops_base = 0                              # ... of the sockets before it (rebinds)
        self.rx = 0

    def closed(self) -> None:
        self.drops_base += self.drops
        self.drops = 0

    def kernel_drops(self) -> Optional[int]:
        return self.drops_base + self.drops if self.ovfl or self.drops_base else None


class Receiver:
    """The VBAN receiver (module docstring; README "receiver.py")."""

    def __init__(self, cfg: Config, *, on_audio: OnAudio, on_midi: OnMidi, on_tick: OnTick,
                 clock: Callable[[], float] = time.monotonic, sock: Optional[socket.socket] = None,
                 on_level: Optional[Callable[[str, int, float, Optional[float], float], None]] = None,
                 text_sock: Optional[socket.socket] = None, coalesce: bool = True, coalesce_min: int = 1,
                 kernel_stamps: bool = True, split_midi: bool = True) -> None:
        """``sock`` / ``text_sock``: sockets to use instead of binding (tests; text_sock only with sock, already in
        one reuseport group with the program attached). ``coalesce``: False gives one on_audio call per packet, as
        before (coalesce_min is then unused). ``coalesce_min``: hold a run of real audio until it has this many
        packets or its first one waited coalesce_min x frames / fs + 1 ms (1: coalesce what is queued, never wait).
        ``kernel_stamps`` / ``split_midi``: False keeps user stamps / one socket (the old receiver). Each is a
        rollback switch: off is exactly the receiver before it."""
        self.cfg = cfg
        self._on_audio, self._on_midi, self._on_tick = on_audio, on_midi, on_tick
        self._on_level = on_level                        # levels.LevelFeed.push: each live packet's peak, at arrival
        self._level_watch: frozenset = frozenset()       # streams a levels client names (?stream=): pushed too
        self._clock = clock
        self._kts_want = bool(kernel_stamps)
        self._split_want = bool(split_midi)
        self._coal_on = bool(coalesce)
        self._coal_min = max(1, int(coalesce_min))
        self._sock = sock
        self._tsock = text_sock if sock is not None else None
        self._own_sock = sock is None
        self._sock_error: Optional[str] = None
        self._rcvbuf: Optional[int] = None
        self._bound: Optional[str] = None
        self._sa, self._sb = _SockStat(), _SockStat()   # A's and B's stamps and counters
        self._t_rcvbuf: Optional[int] = None
        self._t_bound: Optional[str] = None
        self._split_error: Optional[str] = None         # why B isn't there (split false)
        self._t_error: Optional[str] = None             # B's latest receive error
        self._misrouted = self._rebinds = 0
        self._rebind_due = False
        self._rebind_next = -math.inf
        self._text_later: list[tuple] = []               # B's non-MIDI packets polled mid-audio: the next wake's
        self._t_text = -math.inf                         # when B was last looked at (time.monotonic)
        self._tbuf = bytearray(RECV_BUF)
        self._tmv = memoryview(self._tbuf)
        self._from_sock = False                          # dispatching what show-rx's wake read (net.rx_lag_ms)
        self._seen = 0.0                                 # the datagram being handled: its seen time (liveness)
        self._qms: deque[tuple[float, float]] = deque(maxlen=2048)   # (wake, ms) per A batch: kernel queue delay
        # coalesced blocks (show-rx): the open run, and the stats health reads
        self._coal = False                               # inside a wake: _emit coalesces
        self._run = _Run()
        self._run_open = False
        self._zrows: dict[int, np.ndarray] = {}          # channels -> a read-only (1, ch) zero row (GAP pcm)
        self._blocks = 0
        self._win_calls = self._win_pkts = self._win_fmax = 0
        self._win_hist: deque[tuple[float, int, int, int]] = deque(maxlen=16)
        self._rx = self._junk = self._sub_rx = self._bytes = 0
        self._batch_max = self._backlogs = 0             # show-rx's drained batches: the largest, how many were full
        self._errors = {"audio": 0, "midi": 0, "tick": 0, "handle": 0}
        self._err_logged = -math.inf
        self._streams: dict[str, _Stream] = {}
        self._active: Optional[str] = None
        self._offsets: dict[str, float] = {}
        self._offsets_new: Optional[dict[str, float]] = None
        # senders: show-rx's list; set_senders (any thread) puts the next one in _senders_want (applied at a tick)
        self._senders: tuple[contract.Sender, ...] = tuple(cfg.senders)
        self._senders_want: Optional[tuple[contract.Sender, ...]] = None
        self._senders_gen = 0
        # priority: set_priority's names first, then cfg.stream_priority (show-rx applies it at a tick)
        self._priority_base: tuple[str, ...] = tuple(getattr(cfg, "stream_priority", ()) or ())
        self._priority: tuple[str, ...] = self._priority_base
        self._priority_want: Optional[tuple[str, ...]] = None
        self._priority_src: Optional[tuple[str, ...]] = None
        self._choice: tuple[str, ...] = ()               # set_choice: the Room page's Mic pick, after the test streams
        self._choice_want: Optional[tuple[str, ...]] = None
        self._device_hints: dict[str, str] = {}          # set_device_hints: {stream: device} in each SUBSCRIBE
        # the mic on demand (README S5.16): set_off_streams (any thread) puts (off, covered) in _off_want; show-rx
        # applies it at a tick. _off (in effect: decided + held) is swapped whole and read per packet on show-rx.
        self._off_want: Optional[tuple[frozenset[str], frozenset[str]]] = None
        self._off_decided: frozenset[str] = frozenset()
        self._covered: Optional[frozenset[str]] = None   # the streams a decision has covered; None: no decision yet
        self._held: frozenset[str] = frozenset()
        self._off: frozenset[str] = frozenset()
        self._off_since: dict[str, float] = {}
        self._off_dropped = self._off_ignored = 0
        self._ignored_logged = -math.inf
        # show-dns writes these two dicts, keyed (sender name, host), swapped by reference; show-rx reads them
        self._resolved: dict[tuple[str, str], tuple[str, int]] = {}
        self._resolve_err: dict[tuple[str, str], Optional[str]] = {}
        self._dns_wake = threading.Event()
        self._dns_gen = 0
        # host fallback (show-rx): resolved (ip, port) -> the (sender, host) keys it belongs to; when each last answered
        self._addr_index: dict[tuple[str, int], tuple[tuple[str, str], ...]] = {}
        self._index_key: Any = None
        self._answered: dict[tuple[str, str], float] = {}
        self._using: dict[str, Optional[str]] = {}
        self._sub_gen = -1
        self._sub_counter = 0
        self._sub_next = -math.inf
        self._sub_last: dict[str, float] = {}
        self._send_err: dict[str, Optional[str]] = {}
        self._states_next = -math.inf
        self._health_next = -math.inf
        self._health: dict = {}
        self._holding = 0                                # streams with held packets
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        if sock is not None:
            self._adopt(sock)
            if self._tsock is not None:
                self._adopt_text(self._tsock)
        self._held = self._off = self._hold_now()        # privacy at start: no decision yet
        self._build_health(0.0)

    # -- lifecycle --------------------------------------------------------------------------------------------------
    def start(self) -> None:
        """Bind ``cfg.vban_bind`` (unless a socket was given) and start "show-rx" and "show-dns"."""
        if self._threads:
            return
        self._stop.clear()
        if self._sock is None:
            self._bind()
        self._threads = [threading.Thread(target=self._run_rx, name="show-rx", daemon=True),
                         threading.Thread(target=self._run_dns, name="show-dns", daemon=True)]
        for t in self._threads:
            t.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._dns_wake.set()
        for t in self._threads:
            t.join(timeout)
        self._threads = []
        if self._own_sock:
            self._close_socks()

    def _close_socks(self) -> None:
        for s in (self._tsock, self._sock):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        self._sa.closed()
        self._sb.closed()
        self._sock = self._tsock = None

    def _enable_kts(self, sock: Any, ss: _SockStat) -> None:
        """SO_TIMESTAMPNS (+ SO_RXQ_OVFL) on a real socket when ``clock`` is time.monotonic; otherwise (fake clocks,
        wrapped sockets, other platforms, a refusal) user stamps, as before."""
        ss.kts = ss.ovfl = False
        if (not self._kts_want or SO_TIMESTAMPNS is None or self._clock is not time.monotonic
                or not isinstance(sock, socket.socket) or not hasattr(sock, "recvmsg_into")):
            return
        try:
            sock.setsockopt(socket.SOL_SOCKET, SO_TIMESTAMPNS, 1)
            ss.kts = True
            ss.miss = 0
        except OSError:
            return
        try:
            sock.setsockopt(socket.SOL_SOCKET, SO_RXQ_OVFL, 1)
            ss.ovfl = True
        except (OSError, TypeError):
            pass

    def _adopt(self, sock: socket.socket) -> None:
        try:
            self._rcvbuf = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            host, port = sock.getsockname()[:2]
            self._bound = f"{host}:{port}"
        except OSError as e:
            self._sock_error = str(e)
        self._enable_kts(sock, self._sa)

    def _adopt_text(self, sock: socket.socket) -> None:
        try:
            sock.setblocking(False)                      # (polled between audio blocks: must never block)
            self._t_rcvbuf = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            host, port = sock.getsockname()[:2]
            self._t_bound = f"{host}:{port}"
        except OSError as e:
            self._t_error = str(e)
        self._enable_kts(sock, self._sb)

    def _bind(self) -> None:
        host, port = str(self.cfg.vban_bind[0]), int(self.cfg.vban_bind[1])
        if self._split_want and _CAN_SPLIT:
            try:
                port = _probe(host, port)                # a port someone holds fails here, exactly as a plain bind
            except OSError as e:
                self._bind_failed(e)
                return
            pair, why = _bind_pair(host, port)
            if pair is not None:
                self._sock, self._tsock = pair
                self._sock_error = self._split_error = None
                self._adopt(pair[0])
                self._adopt_text(pair[1])
                log.info("VBAN on %s, SO_RCVBUF %s; MIDI on its own socket (reuseport, SO_RCVBUF %s); stamps %s",
                         self._bound, self._rcvbuf, self._t_rcvbuf, "kernel" if self._sa.kts else "user")
                return
            if self._split_error != why:
                log.warning("VBAN: no MIDI socket (%s): one socket, as before", why)
            self._split_error = why
        elif self._split_want:
            self._split_error = "not supported here (Linux SO_REUSEPORT + classic BPF)"
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RCVBUF_WANT)
            except OSError:
                pass
            s.bind((host, port))
        except OSError as e:
            s.close()
            self._bind_failed(e)
            return
        self._sock = s
        self._sock_error = None
        self._adopt(s)
        log.info("VBAN on %s, SO_RCVBUF %s; stamps %s", self._bound, self._rcvbuf, "kernel" if self._sa.kts else "user")

    def _bind_failed(self, e: OSError) -> None:
        if self._sock_error != str(e):
            log.warning("VBAN bind %s:%s failed: %s (retrying every %.0f s)", *self.cfg.vban_bind, e, BIND_RETRY_S)
        self._sock_error = str(e)

    def _maybe_rebind(self, now: float) -> None:
        """Audio came on B (another process joined the reuseport group and the slots swapped when one left): close
        both and bind them again in order (own sockets only; at most every REBIND_EVERY_S; counted)."""
        self._rebind_due = False
        if not self._own_sock or now < self._rebind_next:
            return
        self._rebind_next = now + REBIND_EVERY_S
        self._rebinds += 1
        log.warning("VBAN: audio on the MIDI socket (misrouted %d): binding both sockets again", self._misrouted)
        self._flush()
        self._close_socks()
        self._bind()

    def _run_rx(self) -> None:
        buf = bytearray(RECV_BUF)
        mv = memoryview(buf)
        next_tick = self._clock()
        next_bind = next_tick
        try:
            while not self._stop.is_set():
                now = self._clock()
                sock = self._sock
                if sock is None:
                    if self._own_sock and now >= next_bind:
                        self._bind()
                        next_bind = now + BIND_RETRY_S
                    if self._sock is None:
                        self._stop.wait(max(0.0, min(TICK_S, next_tick - now)))
                else:
                    timeout = min(TICK_S, next_tick - now)
                    if self._holding:
                        timeout = min(timeout, 0.001)
                    if self._run_open:                   # a held run (coalesce_min) ends its wait on time
                        timeout = min(timeout, self._run.deadline - now)
                    try:
                        if self._tsock is not None or self._sa.kts:
                            ok = self._wake_select(sock, buf, mv, timeout)
                        else:
                            ok = self._wake_user(sock, buf, mv, timeout)
                    except Exception:                    # noqa: BLE001 - never let a wake stop show-rx
                        self._error("handle")
                        self._stop.wait(TICK_S)
                        ok = True
                    if not ok:
                        break
                now = self._clock()
                if self._run_open:
                    self._settle(now)
                if self._holding:
                    self._expire_coalesced(now, force=False)
                if now >= next_tick:
                    try:
                        self.tick(now)
                    except Exception:                    # noqa: BLE001
                        self._error("tick")
                    next_tick += TICK_S
                    if next_tick <= now:
                        next_tick = now + TICK_S
                if self._rebind_due:
                    self._maybe_rebind(now)
        finally:
            self._flush()

    def _wake_user(self, sock: Any, buf: bytearray, mv: memoryview, timeout: float) -> bool:
        """One socket, user stamps (the old loop): wait on recvfrom_into, stamp each datagram with clock() after its
        recv, drain what else is queued. False: stop."""
        try:
            sock.settimeout(max(0.0005, timeout))
            n, addr = sock.recvfrom_into(buf)
        except (socket.timeout, BlockingIOError, InterruptedError):
            n = 0
        except OSError as e:                             # e.g. closed under us at stop
            if self._stop.is_set():
                return False
            self._sock_error = str(e)
            self._stop.wait(TICK_S)
            n = 0
        if n:
            arrival = self._clock()
            if self._sock_error is not None:             # a transient error (ICMP, ENOBUFS) has passed
                self._sock_error = None
            batch = [(bytes(mv[:n]), addr, arrival)]
            try:                                         # whatever else is already queued, without waiting
                sock.settimeout(0.0)
                while len(batch) < RX_BATCH_MAX:
                    n, addr = sock.recvfrom_into(buf)
                    if not n:
                        break
                    batch.append((bytes(mv[:n]), addr, self._clock()))
            except (socket.timeout, BlockingIOError, InterruptedError, OSError):
                pass
            self._sa.rx += len(batch)
            self._from_sock = True
            try:
                self.handle_batch(batch)
            finally:
                self._from_sock = False
        return True

    def _wake_select(self, sock: socket.socket, buf: bytearray, mv: memoryview, timeout: float) -> bool:
        """A (+ B): select on both, drain B first and dispatch its MIDI at once, then A's batch, each datagram with
        its kernel arrival (or user stamps) and the wake as its seen time. False: stop."""
        ts = self._tsock
        try:
            if sock.gettimeout() != 0.0:
                sock.setblocking(False)
            ready = select.select((sock, ts) if ts is not None else (sock,), (), (), max(0.0005, timeout))[0]
        except (OSError, ValueError) as e:               # e.g. closed under us at stop
            if self._stop.is_set():
                return False
            self._sock_error = str(e)
            self._stop.wait(TICK_S)
            return True
        self._t_text = time.monotonic()                  # (select just looked at B)
        if not ready and not self._text_later:
            return True
        self._from_sock = True
        try:
            if ts is not None and (ts in ready or self._text_later):
                self._text_wake(ts)
            sa = self._sa
            kts = sa.kts
            items, err = self._recv_batch(sock, buf, mv, kts, RX_BATCH_MAX)
            if not items:
                if err is not None and not isinstance(err, (BlockingIOError, InterruptedError, socket.timeout)):
                    if self._stop.is_set():
                        return False
                    self._sock_error = str(err)
                    self._stop.wait(TICK_S)
                return True
            if self._sock_error is not None:             # a transient error (ICMP, ENOBUFS) has passed
                self._sock_error = None
            sa.rx += len(items)
            if kts:
                wake, oldest = self._kconvert(items, sa)
                if oldest <= wake:
                    self._qms.append((wake, (wake - oldest) * 1000.0))
                self.handle_batch(items, seen_s=wake)
            else:
                self.handle_batch(items)
        finally:
            self._from_sock = False
        return True

    def _recv_batch(self, sock: Any, buf: bytearray, mv: memoryview, kts: bool, cap: int) -> tuple[list, Any]:
        """Up to ``cap`` queued datagrams, non-blocking: [(bytes, addr, ancillary list | user stamp)], the error that
        ended it (BlockingIOError when the queue is empty)."""
        items: list = []
        err: Any = None
        try:
            if kts:
                bufs = [buf]
                recv = sock.recvmsg_into
                while len(items) < cap:
                    n, anc, _fl, addr = recv(bufs, ANC_SPACE)
                    if not n:
                        break
                    items.append((bytes(mv[:n]), addr, anc))
            else:
                clock = self._clock
                while len(items) < cap:
                    n, addr = sock.recvfrom_into(buf)
                    if not n:
                        break
                    items.append((bytes(mv[:n]), addr, clock()))
        except OSError as e:                             # (BlockingIOError: the queue is empty)
            err = e
        return items, err

    def _kconvert(self, items: list, ss: _SockStat) -> tuple[float, float]:
        """In place: each item's ancillary data -> its kernel arrival (kernel_arrival). Returns (wake, the oldest good
        arrival or inf). KTS_MISS_MAX datagrams in a row without a stamp put this socket on user stamps."""
        wake, off = _wake_off()
        oldest = math.inf
        for i in range(len(items)):
            data, addr, anc = items[i]
            arr, status, drops = kernel_arrival(anc, wake, off)
            if status:
                ss.ts_bad += 1
                if status == 2:
                    ss.miss += 1
                else:
                    ss.miss = 0
            else:
                ss.miss = 0
                if arr < oldest:
                    oldest = arr
            if drops is not None:
                ss.drops = drops
            items[i] = (data, addr, arr)
        if ss.miss >= KTS_MISS_MAX and ss.kts:
            ss.kts = False
            log.warning("VBAN: %d datagrams in a row without a kernel stamp: user stamps from now on", ss.miss)
        return wake, oldest

    # -- the MIDI socket (show-rx) ------------------------------------------------------------------------------------
    def _text_wake(self, ts: socket.socket) -> None:
        """Every wake, before A: what was deferred from the polls, then B's queue."""
        if self._text_later:
            later, self._text_later = self._text_later, []
            self._dispatch_later(later)
        self._text_read(ts, mid=False)

    def _poll_text(self) -> None:
        """Before each on_audio call (show-rx): B's queue, its MIDI dispatched now (about 3 us when empty), unless B
        was looked at less than TEXT_POLL_S ago: a note waits at most one on_audio call (+ 0.5 ms)."""
        ts = self._tsock
        if ts is None:
            return
        now = time.monotonic()
        if now - self._t_text < TEXT_POLL_S:
            return
        self._t_text = now
        seen, coal = self._seen, self._coal
        try:
            self._text_read(ts, mid=True)
        finally:
            self._seen, self._coal = seen, coal

    def _text_read(self, ts: socket.socket, mid: bool) -> None:
        """Drain B and dispatch its MIDI. A non-MIDI packet on B is counted as misrouted (audio asks for a rebind) and
        handled like one from A: now between wakes, at the next wake when polled mid-audio (``mid``)."""
        sb = self._sb
        items, err = self._recv_batch(ts, self._tbuf, self._tmv, sb.kts, TEXT_BATCH_MAX)
        if items:
            was, self._from_sock = self._from_sock, True     # (a poll from tick's flush too: net.rx_lag_ms)
            try:
                self._text_items(items, mid)
            finally:
                self._from_sock = was
        elif err is not None and not isinstance(err, (BlockingIOError, InterruptedError, socket.timeout)):
            self._t_error = str(err)

    def _text_items(self, items: list, mid: bool) -> None:
        sb = self._sb
        self._t_error = None
        sb.rx += len(items)
        seen: Optional[float] = None
        if sb.kts:
            seen, _ = self._kconvert(items, sb)
        later = []
        for data, addr, arr in items:
            try:
                pkt = self._parse(data)
                if pkt is None:
                    continue
                o = pkt.obj if isinstance(pkt, vban.TextPacket) else None
                if o is None or "subscribe" in o or not ("ev" in o or isinstance(o.get("state"), dict)):
                    self._misrouted += 1
                    if o is None:                        # audio: the reuseport slots are swapped
                        self._rebind_due = True
                    later.append((pkt, addr, arr, len(data), arr if seen is None else seen))
                    continue
                self._seen = arr if seen is None else seen
                self._dispatch(pkt, addr, arr, len(data))
            except Exception:                            # noqa: BLE001 - never let one datagram stop show-rx
                self._error("handle")
        if later:
            if mid:
                self._text_later.extend(later)
            else:
                self._dispatch_later(later)

    def _dispatch_later(self, later: list) -> None:
        """B's non-MIDI packets, in arrival order, as one coalesced wake (control first, as handle_batch does)."""
        was = self._coal
        self._coal = self._coal_on
        try:
            for audio in (False, True):
                for pkt, addr, arr, n, seen in later:
                    if isinstance(pkt, vban.AudioPacket) is audio:
                        self._seen = seen
                        try:
                            self._dispatch(pkt, addr, arr, n)
                        except Exception:                # noqa: BLE001
                            self._error("handle")
        finally:
            self._coal = was
        if not was:
            self._flush()

    def _run_dns(self) -> None:
        while not self._stop.is_set():
            self._dns_wake.clear()                       # a set_senders during the pass wakes the next one at once
            self._resolve_all()
            ok = all(self._resolve_err.get(k) is None for k in self._host_keys(self._sender_list()))
            self._dns_wake.wait(DNS_EVERY_S if ok else DNS_RETRY_S)

    def _sender_list(self) -> tuple[contract.Sender, ...]:
        """The newest list: set_senders' (not yet applied by show-rx) else show-rx's (any thread)."""
        want = self._senders_want
        return want if want is not None else self._senders

    @staticmethod
    def _host_keys(senders: tuple[contract.Sender, ...]) -> list[tuple[str, str]]:
        return [(s.name, h) for s in senders for h in sender_hosts(s)]

    def _resolve_all(self) -> None:
        """Resolve every sender's every host (show-dns; blocking). Swaps the dicts show-rx reads. A host that stops
        resolving keeps its last good address; hosts no longer listed are dropped."""
        old_r, old_e = self._resolved, self._resolve_err
        resolved: dict[tuple[str, str], tuple[str, int]] = {}
        errs: dict[tuple[str, str], Optional[str]] = {}
        changed = False
        for s in self._sender_list():
            for h in sender_hosts(s):
                key = (s.name, h)
                if key in errs:                          # a host listed twice
                    continue
                try:
                    info = socket.getaddrinfo(h, s.port, socket.AF_INET, socket.SOCK_DGRAM)
                    addr = (str(info[0][4][0]), int(info[0][4][1]))
                    changed |= old_r.get(key) != addr
                    resolved[key], errs[key] = addr, None
                except (OSError, UnicodeError, IndexError) as e:
                    if old_e.get(key) != str(e):
                        log.warning("sender %s: can't resolve %s: %s (keeping %s)", s.name, h, e, old_r.get(key))
                    errs[key] = str(e)
                    if key in old_r:
                        resolved[key] = old_r[key]
        changed |= resolved.keys() != old_r.keys()
        self._resolved, self._resolve_err = resolved, errs
        if changed:
            self._dns_gen += 1                           # show-rx subscribes at once

    def _error(self, what: str) -> None:
        self._errors[what] += 1
        now = time.monotonic()
        if now - self._err_logged >= ERROR_LOG_EVERY_S:
            self._err_logged = now
            log.exception("receiver: %s raised (errors so far %s)", what, self._errors)

    # -- any thread -------------------------------------------------------------------------------------------------
    def health(self) -> dict:
        """The latest 2 Hz health snapshot (README "Health"); safe from any thread."""
        return self._health

    def set_offsets(self, offsets: dict[str, float]) -> None:
        """acoustic_offset_ms per stream (latency.json ``sources``); applied at the next tick."""
        self._offsets_new = {str(k): float(v) for k, v in dict(offsets).items()}

    def set_senders(self, senders: tuple[contract.Sender, ...]) -> None:
        """The whole sender list (sources.py; any thread). Applied at the next tick; show-dns resolves new hosts at
        once. A removed sender gets no more SUBSCRIBEs; its streams go down by the usual rules."""
        self._senders_want = tuple(senders)
        self._dns_wake.set()

    def set_priority(self, names: tuple[str, ...]) -> None:
        """Streams ranked before ``cfg.stream_priority`` (sources.py: a test stream while it plays; any thread).
        Applied at the next tick; the usual switch-up rule (SWITCH_UP_S) applies."""
        self._priority_want = tuple(str(n) for n in names)

    def set_choice(self, stream: Optional[str]) -> None:
        """The Room page's Mic pick (inputs.py; any thread): ranked after set_priority's test streams and before
        ``cfg.stream_priority``; None for automatic. Applied at the next tick; the switch-up rule applies."""
        self._choice_want = (str(stream),) if stream else ()

    def set_device_hints(self, hints: Mapping[str, str]) -> None:
        """{stream: capture device} sent in each SUBSCRIBE to the sender of that stream (inputs.py; any thread). A
        change is sent at once."""
        hints = {str(k): str(v) for k, v in dict(hints).items() if v}
        if hints != self._device_hints:
            self._device_hints = hints
            self._sub_next = -math.inf

    def set_off_streams(self, names: Any, covered: Any = None) -> None:
        """The mic on demand (mic.py; any thread; README S5.16): ``names`` go in each SUBSCRIBE's ``off`` (device
        closed, off heartbeats) instead of ``subscribe``, and their audio is dropped on arrival. ``covered``: every
        stream this decision considered (default: every stream of the senders now); a configured stream that could
        be a mic and isn't covered stays held off until a decision covers it. Applied at the next tick, which sends
        the SUBSCRIBEs at once."""
        if covered is None:
            covered = [st for s in self._sender_list() for st in s.streams]
        self._off_want = (frozenset(str(n) for n in names), frozenset(str(n) for n in covered))

    def off_streams(self) -> frozenset[str]:
        """The streams kept off now: the decision's and the held ones (any thread)."""
        return self._off

    def senders(self) -> tuple[contract.Sender, ...]:
        """The sender list show-rx uses now (any thread)."""
        return self._senders

    # -- show-rx ----------------------------------------------------------------------------------------------------
    def active_audio(self) -> Optional[str]:
        return self._active

    def handle_batch(self, batch: list[tuple[bytes, tuple[str, int], float]], seen_s: Optional[float] = None) -> None:
        """Datagrams drained together (show-rx): MIDI and control packets first, in arrival order, then the audio in
        arrival order, so a note is never held behind the batch's audio work. Each keeps its own arrival time; its
        seen time (liveness) is ``seen_s`` (the wake, with kernel stamps) else its arrival. The batch's contiguous
        audio goes out coalesced (module docstring "Blocks")."""
        k = len(batch)
        if k > self._batch_max:
            self._batch_max = k
        if k >= RX_BATCH_MAX:
            self._backlogs += 1
        was = self._coal
        self._coal = self._coal_on
        try:
            audio = []
            for buf, addr, arrival in batch:
                try:
                    pkt = self._parse(buf)
                    if pkt is None:
                        continue
                    if isinstance(pkt, vban.AudioPacket):
                        audio.append((pkt, addr, arrival, len(buf)))
                    else:
                        if self._tsock is not None and ("ev" in pkt.obj or isinstance(pkt.obj.get("state"), dict)):
                            self._misrouted += 1                 # MIDI on A: not our key order (slower, not wrong)
                        self._seen = arrival if seen_s is None else seen_s
                        self._dispatch(pkt, addr, arrival, len(buf))
                except Exception:                        # noqa: BLE001 - never let one datagram stop show-rx
                    self._error("handle")
            for pkt, addr, arrival, n in audio:
                try:
                    self._seen = arrival if seen_s is None else seen_s
                    self._dispatch(pkt, addr, arrival, n)
                except Exception:                        # noqa: BLE001
                    self._error("handle")
        finally:
            self._coal = was
        if not was and self._run_open:
            self._settle(seen_s if seen_s is not None else max((b[2] for b in batch), default=self._seen))

    def handle(self, buf: bytes | bytearray | memoryview, addr: tuple[str, int], arrival_s: float,
               seen_s: Optional[float] = None) -> None:
        """One datagram (show-rx; tests call it directly with fake times): one on_audio call per packet."""
        pkt = self._parse(buf)
        if pkt is not None:
            self._seen = arrival_s if seen_s is None else seen_s
            self._dispatch(pkt, addr, arrival_s, len(buf))

    def _parse(self, buf: bytes | bytearray | memoryview):
        self._rx += 1
        self._bytes += len(buf)
        pkt = vban.parse(buf)
        if pkt is None:
            self._junk += 1
        return pkt

    def _dispatch(self, pkt: Any, addr: tuple[str, int], arrival_s: float, nbytes: int) -> None:
        keys = self._addr_index.get((addr[0], addr[1])) if self._addr_index else None
        if keys:                                         # a configured host answered (the host fallback)
            for k in keys:
                self._answered[k] = self._seen
        if isinstance(pkt, vban.AudioPacket):
            self._audio(pkt, addr, arrival_s, nbytes)
            return
        o = pkt.obj
        if "subscribe" in o:                             # vban.classify's order, parsing once
            if vban.is_subscribe(pkt):
                self._sub_rx += 1
            else:
                self._junk += 1
            return
        if "ev" in o:
            msg: Any = vban.parse_midi_events(o)
        elif isinstance(o.get("state"), dict):
            msg = vban.parse_midi_state(o)
        else:
            hb = vban.parse_heartbeat(o)
            if hb is None:
                self._junk += 1
                return
            self._heartbeat(self._stream(pkt.stream, addr, self._seen), hb, arrival_s)
            return
        if msg is None:
            self._junk += 1
            return
        self._midi(self._stream(pkt.stream, addr, self._seen), pkt, msg, arrival_s, nbytes)

    def tick(self, now_s: float) -> None:
        """SUBSCRIBE when due, held-hole deadlines, states/active (10 Hz), health (2 Hz), then on_tick (show-rx). A run
        held open (coalesce_min) goes out first: every block delivered so far reaches the consumers before on_tick."""
        if self._run_open:
            self._flush()
        if self._offsets_new is not None:
            self._offsets, self._offsets_new = self._offsets_new, None
            for st in self._streams.values():
                self._apply_offset(st)
        want = self._senders_want
        if want is not None and want is not self._senders:
            self._apply_senders(want)
            if self._refresh_off(now_s):                 # a new sender's mic is held until a decision covers it
                self._update_states(now_s)
        ow = self._off_want
        if ow is not None:
            self._off_want = None
            self._off_decided, self._covered = ow
            if self._refresh_off(now_s):
                self._states_next = now_s + STATES_EVERY_S
                self._update_states(now_s)               # an active stream that went off is left at once
        cw = self._choice_want
        if cw is not None:
            self._choice_want = None
            if cw != self._choice:
                self._choice = cw
                self._apply_priority(self._priority_src if self._priority_src is not None else ())
        pw = self._priority_want
        if pw is not None and pw is not self._priority_src:
            self._apply_priority(pw)
        if self._index_key != (self._dns_gen, self._senders_gen):
            self._rebuild_index()
        if self._senders and (now_s >= self._sub_next or self._sub_gen != self._dns_gen):
            self._subscribe(now_s)
        if self._holding:
            self._expire_coalesced(now_s, force=True)
        if now_s >= self._states_next:
            self._states_next = now_s + STATES_EVERY_S
            self._refresh_off(now_s)                     # (a held name may have turned out to be MIDI)
            self._update_states(now_s)
        if now_s >= self._health_next:
            self._health_next = now_s + HEALTH_EVERY_S
            self._build_health(now_s)
        try:
            self._on_tick(now_s)
        except Exception:                                # noqa: BLE001
            self._error("tick")

    # -- the sender list and priority (show-rx) ----------------------------------------------------------------------
    def _apply_senders(self, senders: tuple[contract.Sender, ...]) -> None:
        old = {s.name for s in self._senders}
        self._senders = senders
        self._senders_gen += 1
        names = {s.name for s in senders}
        for gone in old - names:
            for d in (self._sub_last, self._send_err, self._using):
                d.pop(gone, None)
        keep = set(self._host_keys(senders))
        self._answered = {k: v for k, v in self._answered.items() if k in keep}
        self._sub_next = -math.inf                       # subscribe the (already resolved) ones at once
        log.info("senders: %s", ", ".join(f"{s.name}={'|'.join(sender_hosts(s))}:{s.port}/{','.join(s.streams)}"
                                          for s in senders) or "none")

    def _apply_priority(self, names: tuple[str, ...]) -> None:
        self._priority_src = names
        prio = tuple(dict.fromkeys(names + self._choice + self._priority_base))
        if prio == self._priority:
            return
        self._priority = prio
        for st in self._streams.values():
            st.rank = contract.stream_rank(st.name, prio)
        log.info("stream priority: %s", ",".join(prio) or "none")

    def _rebuild_index(self) -> None:
        self._index_key = (self._dns_gen, self._senders_gen)
        idx: dict[tuple[str, int], list[tuple[str, str]]] = {}
        resolved = self._resolved
        for key in self._host_keys(self._senders):
            addr = resolved.get(key)
            if addr is not None:
                idx.setdefault(addr, []).append(key)
        self._addr_index = {a: tuple(ks) for a, ks in idx.items()}

    # -- the mic on demand (show-rx) ----------------------------------------------------------------------------------
    def _hold_now(self) -> frozenset[str]:
        """Configured streams that could be a mic (not MIDI by name or by kind, not a test stream, not an on-demand
        sender's) that no decision has covered yet: held off (privacy at start)."""
        cov = self._covered
        out = set()
        for s in self._senders:
            if s.on_demand:
                continue
            for name in s.streams:
                if (cov is not None and name in cov) or name in contract.MIDI_STREAMS or is_test_stream(name):
                    continue
                st = self._streams.get(name)
                if st is not None and st.kind == "midi":
                    continue
                out.add(name)
        return frozenset(out)

    def _refresh_off(self, now: float) -> bool:
        """Recompute the off set in effect (the decision's plus the held); True when it changed (the SUBSCRIBEs then
        go at once)."""
        held = self._hold_now()
        off = self._off_decided | held
        self._held = held
        if off == self._off:
            return False
        for n in off - self._off:
            self._off_since[n] = now
        for n in self._off - off:
            self._off_since.pop(n, None)
            st = self._streams.get(n)
            if st is not None:                           # back on: "quiet" needs 5 s of its new sound, not the old
                st.first_data_s = st.t_loud = None
        self._off = off
        self._sub_next = -math.inf
        log.info("mic: off %s%s", ",".join(sorted(off)) or "none",
                 f" (held until a mic decision covers them: {','.join(sorted(held))})" if held else "")
        return True

    def _drop_off(self, pkt: vban.AudioPacket, addr: tuple[str, int], arrival: float) -> None:
        """An audio packet of a stream kept off: counted and dropped (no meter, level, delivery or segment). The grace
        runs on its arrival: a backlog's stragglers are still stragglers."""
        st = self._stream(pkt.stream, addr, self._seen)
        st.off_dropped += 1
        self._off_dropped += 1
        since = self._off_since.get(pkt.stream)
        if since is not None and arrival - since > OFF_GRACE_S:
            st.off_ignored += 1
            self._off_ignored += 1
            if arrival - self._ignored_logged >= ERROR_LOG_EVERY_S:
                self._ignored_logged = arrival
                log.warning("mic: %s still sends audio %.1f s after it went off (dropped; %d so far)", pkt.stream,
                            arrival - since, self._off_ignored)

    # -- SUBSCRIBE --------------------------------------------------------------------------------------------------
    def _subscribe(self, now: float) -> None:
        self._sub_next = now + SUBSCRIBE_EVERY_S
        self._sub_gen = self._dns_gen
        sock, resolved = self._sock, self._resolved
        if sock is None:
            return
        hints, off = self._device_hints, self._off
        for s in self._senders:
            devices = {st: hints[st] for st in s.streams if st in hints} or None
            sub = [st for st in s.streams if st not in off]
            offl = [st for st in s.streams if st in off]
            cands = [(h, resolved[(s.name, h)]) for h in sender_hosts(s) if (s.name, h) in resolved]
            if not cands:
                continue
            using = None
            for h, a in cands:
                t = self._answered.get((s.name, h))
                if t is not None and now - t <= ANSWER_FRESH_S:
                    using = (h, a)
                    break
            self._using[s.name] = using[0] if using is not None else None
            err = None
            for _h, addr in ([using] if using is not None else cands):
                try:
                    sock.sendto(vban.subscribe_packet(sub, self._sub_counter, devices=devices, off=offl), addr)
                except OSError as e:
                    err = str(e)
                self._sub_counter = (self._sub_counter + 1) % _MOD
            self._sub_last[s.name] = now
            self._send_err[s.name] = err

    # -- registry ---------------------------------------------------------------------------------------------------
    def _stream(self, name: str, addr: tuple[str, int], now: float) -> _Stream:
        st = self._streams.get(name)
        if st is None:
            st = self._streams[name] = _Stream(name, (str(addr[0]), int(addr[1])), now, self._priority)
            log.info("stream %s from %s:%s%s", name, addr[0], addr[1],
                     "" if name in contract.KNOWN_STREAMS else " (unknown name: lowest priority)")
        elif st.addr[0] != addr[0] or st.addr[1] != addr[1]:
            st.addr = (str(addr[0]), int(addr[1]))
        st.last_s = now
        return st

    def _apply_offset(self, st: _Stream) -> None:
        off = float(self._offsets.get(st.name, 0.0))
        if st.clock is not None:
            st.clock.acoustic_offset_ms = off
        if st.tmap is not None:
            st.tmap.acoustic_offset_ms = off

    # -- audio ------------------------------------------------------------------------------------------------------
    def _audio(self, pkt: vban.AudioPacket, addr: tuple[str, int], arrival: float, size: int) -> None:
        if pkt.stream in self._off:                      # the mic is off for it: nothing of it goes anywhere
            self._drop_off(pkt, addr, arrival)
            return
        seen = self._seen
        st = self._stream(pkt.stream, addr, seen)
        if st.kind == "midi":
            self._junk += 1
            return
        prev_data = st.last_arr
        st.kind = "audio"
        st.packets += 1
        st.bytes += size
        st.last_data_s = seen
        st.last_arr = arrival
        if st.first_data_s is None:
            st.first_data_s = seen
        self._meter(st, pkt.pcm, arrival)
        if self._on_level is not None and st.clock is not None and (st.name == self._active
                                                                    or st.name in self._level_watch):
            self._level(st, pkt, seen, st.name == self._active)
        if self._active is None:                         # nothing live: take this one now, not at the next 0.1 s
            self._update_states(seen)
        if st.clock is None:
            st.clock = SampleClock(pkt.sr, float(self._offsets.get(st.name, 0.0)))
            st.capture = CaptureMap(pkt.sr)
            self._new_format(st, pkt)
            self._begin(st, pkt, arrival)
        elif (pkt.sr, pkt.channels, pkt.fmt, pkt.frames) != (st.sr, st.channels, st.fmt, st.frames):
            self._segment(st, pkt, arrival, "format", False)
        elif st.want_seg is not None:
            self._segment(st, pkt, arrival, *st.want_seg)
        elif prev_data is not None and arrival - prev_data >= IDLE_S:
            self._segment(st, pkt, arrival, "idle", self._same_sess(st))
        else:
            k = vban.counter_delta(st.base_c, pkt.counter)
            if k == 0:
                self._observe(st, st.base_n, arrival)
                self._deliver_real(st, pkt, arrival)
                self._drain(st)
            elif k < _HALF:
                if k <= st.fill_max:
                    if pkt.counter in st.pending:
                        st.dup += 1
                    else:
                        self._observe(st, st.base_n + k * st.frames, arrival)
                        if not st.pending:
                            self._holding += 1
                        st.pending[pkt.counter] = (pkt, arrival)
                else:
                    self._segment(st, pkt, arrival, "counter", self._same_sess(st))
            else:
                behind = _MOD - k
                if behind <= 2 * st.fill_max:
                    if pkt.counter in st.filled:
                        st.late += 1
                        n = st.base_n - behind * st.frames
                        if n >= 0:
                            self._observe(st, n, arrival)
                    else:
                        st.dup += 1
                else:
                    self._segment(st, pkt, arrival, "counter", self._same_sess(st))
        if st.pending:
            self._expire(st, arrival)

    def _same_sess(self, st: _Stream) -> bool:
        return st.hb is not None and st.seg_sess == st.hb.sess

    def _new_format(self, st: _Stream, pkt: vban.AudioPacket) -> None:
        st.sr, st.channels, st.fmt, st.frames = pkt.sr, pkt.channels, pkt.fmt, pkt.frames
        st.fill_max = max(1, int(FILL_MAX_S * pkt.sr / pkt.frames))

    def _begin(self, st: _Stream, pkt: vban.AudioPacket, arrival: float) -> None:
        """Start the clock's current segment with ``pkt`` as its first packet (n = 0)."""
        assert st.clock is not None and st.capture is not None
        st.seg_id = st.clock.seg
        st.seg_t = arrival
        st.seg_sess = st.hb.sess if st.hb is not None else None
        st.segments += 1
        st.want_seg = None
        st.base_c, st.base_n = pkt.counter, 0
        st.filled.clear()
        st.flags = AUDIO_SEGMENT
        st.ndiff = None
        st.capture.reset(st.clock.seg, fs=st.sr)
        self._observe(st, 0, arrival)
        self._deliver_real(st, pkt, arrival)

    def _segment(self, st: _Stream, pkt: vban.AudioPacket, arrival: float, reason: str, keep: bool) -> None:
        assert st.clock is not None
        if st.pending:
            self._expire(st, math.inf)                   # what was held goes out on the old segment's map
        if reason == "format":
            self._new_format(st, pkt)
            st.clock.segment(reason, keep_skew=False, fs=pkt.sr)
        else:
            st.clock.segment(reason, keep_skew=keep)
        self._begin(st, pkt, arrival)

    def _observe(self, st: _Stream, n_first: int, arrival: float) -> None:
        clock = st.clock
        assert clock is not None and st.capture is not None
        clock.observe(n_first, arrival, st.frames)
        if clock.seg != st.seg_id:                       # it re-anchored itself ("step"): n continues
            if self._run_open and self._run.st is st:    # the open run keeps the old map's t0; later blocks the new
                self._flush()
            st.seg_id = clock.seg
            st.segments += 1
            st.capture.reset(clock.seg)
            st.ndiff = None

    def _deliver_real(self, st: _Stream, pkt: vban.AudioPacket, arrival: float) -> None:
        flags, st.flags = st.flags, 0
        n0 = st.base_n
        st.base_c = (st.base_c + 1) % _MOD
        st.base_n += st.frames
        st.count_loss(st.last_arr or 0.0, 1, 0)
        if st.name == self._active:
            self._emit(st, n0, pkt.pcm, flags, arrival)

    def _drain(self, st: _Stream) -> None:
        """Deliver held packets that are now next in line."""
        while st.pending:
            item = st.pending.pop(st.base_c, None)
            if item is None:
                return
            self._deliver_real(st, item[0], item[1])
            if not st.pending:
                self._holding -= 1

    def _expire(self, st: _Stream, now: float) -> None:
        """Zero-fill holes whose hold has passed, delivering what was held behind them."""
        while st.pending:
            seen = min(a for _, a in st.pending.values())
            if now < seen + st.hold_s():
                return
            c_next = min(st.pending, key=lambda c: vban.counter_delta(st.base_c, c))
            missing = vban.counter_delta(st.base_c, c_next)
            for i in range(missing):
                st.filled.append((st.base_c + i) % _MOD)
            n0 = st.base_n
            flags = AUDIO_GAP | (st.flags & AUDIO_SEGMENT)
            st.flags = (st.flags & ~AUDIO_SEGMENT) | AUDIO_RESUME
            st.base_c = c_next
            st.base_n += missing * st.frames
            st.gaps += 1
            st.lost += missing
            st.count_loss(seen, 0, missing)
            if st.name == self._active:                  # zeros for free: a read-only view of one cached row
                self._emit(st, n0, np.broadcast_to(self._zrow(st.channels), (missing * st.frames, st.channels)),
                           flags)
            self._drain(st)

    def _zrow(self, ch: int) -> np.ndarray:
        z = self._zrows.get(ch)
        if z is None:
            z = self._zrows[ch] = np.zeros((1, ch), np.float32)
            z.flags.writeable = False
        return z

    def _expire_all(self, now: float) -> None:
        for st in list(self._streams.values()):
            if st.pending:
                self._expire(st, now)

    def _expire_coalesced(self, now: float, force: bool) -> None:
        """_expire_all as one wake: each hole's GAP and RESUME alone, the held packets behind them as one run;
        ``force`` (tick) flushes it, else the coalesce_min rule (_settle)."""
        was = self._coal
        self._coal = self._coal_on
        try:
            self._expire_all(now)
        finally:
            self._coal = was
        if not was and self._run_open:
            if force:
                self._flush()
            else:
                self._settle(now)

    # -- blocks out (show-rx) ----------------------------------------------------------------------------------------
    def _emit(self, st: _Stream, n0: int, pcm: np.ndarray, flags: int, arrival: float = 0.0) -> None:
        """One block of the active stream: straight to on_audio outside a wake (handle(): per packet), else onto the
        open run (module docstring "Blocks"). ``arrival``: its packet's (the run's wait starts at the first's)."""
        assert st.clock is not None
        r = self._run
        if self._run_open:
            if (self._coal and not flags and r.st is st and n0 == r.n_end and r.fs == st.sr
                    and r.ch == pcm.shape[1] and r.frames + pcm.shape[0] <= COALESCE_MAX_FRAMES):
                r.parts.append(pcm)                      # (a GAP / RESUME run was flushed when it opened)
                r.n_end += pcm.shape[0]
                r.frames += pcm.shape[0]
                r.packets += 1
                return
            self._flush()
        t0 = st.clock.to_host(n0)                        # now: exactly the per-packet value for this block
        real = not flags & AUDIO_GAP
        if not self._coal:
            self._poll_text()
            self._call(st.name, n0, t0, pcm, st.sr, flags, 1 if real else 0)
            return
        r.st, r.n0, r.t0, r.fs, r.ch, r.flags = st, n0, t0, st.sr, pcm.shape[1], flags
        r.n_end = n0 + pcm.shape[0]
        r.frames = pcm.shape[0]
        r.packets = 1 if real else 0
        r.deadline = arrival + self._coal_min * pcm.shape[0] / st.sr + 0.001
        r.parts.append(pcm)
        self._run_open = True
        if flags & (AUDIO_GAP | AUDIO_RESUME):           # alone: dsp and clock size their suppression by its frames
            self._flush()

    def _flush(self) -> None:
        """The open run as one on_audio call (B polled first: a note waits at most one call)."""
        if not self._run_open:
            return
        self._run_open = False
        r = self._run
        self._poll_text()
        parts = r.parts
        pcm = parts[0] if len(parts) == 1 else np.concatenate(parts)
        parts.clear()
        st, r.st = r.st, None
        assert st is not None
        self._call(st.name, r.n0, r.t0, pcm, r.fs, r.flags, r.packets)

    def _settle(self, now: float) -> None:
        """End of a wake: the open run goes out unless coalesce_min holds it (fewer packets, still within its wait)."""
        if self._run_open and (self._run.packets >= self._coal_min or now >= self._run.deadline):
            self._flush()

    def _call(self, name: str, n0: int, t0: float, pcm: np.ndarray, fs: int, flags: int, packets: int) -> None:
        self._blocks += 1
        if packets:
            self._win_calls += 1
            self._win_pkts += packets
            if pcm.shape[0] > self._win_fmax:
                self._win_fmax = pcm.shape[0]
        try:
            self._on_audio(name, n0, t0, pcm, fs, flags)
        except Exception:                                # noqa: BLE001
            self._error("audio")

    def set_level_watch(self, names: Any) -> None:
        """The streams a levels client names (``?stream=``; levels.LevelFeed.on_watch, any thread): their packets go
        to the feed too, marked not active. The active stream, and what listens to it, is unchanged."""
        self._level_watch = frozenset(str(n) for n in (names or ()))

    def _level(self, st: _Stream, pkt: vban.AudioPacket, arrival: float, active: bool = True) -> None:
        """The live meter (levels.py): this packet's peak and its capture time on the sender's clock, at arrival."""
        try:
            pcm = pkt.pcm
            peak = max(float(pcm.max()), -float(pcm.min())) if pcm.size else 0.0
            t = None
            if st.capture is not None and st.capture.ready:
                t = st.capture.to_remote_ns(st.base_n + _signed(vban.counter_delta(st.base_c, pkt.counter)) * st.frames)
            if active:
                self._on_level(st.name, pkt.counter, peak, t, arrival, pcm=pcm, sr=st.sr)  # type: ignore[misc]
            else:                                        # a watched stream that isn't the active one
                self._on_level(st.name, pkt.counter, peak, t, arrival, pcm=pcm, sr=st.sr,  # type: ignore[misc]
                               active=False)
        except Exception:                                # noqa: BLE001
            self._error("audio")

    def _meter(self, st: _Stream, pcm: np.ndarray, arrival: float) -> None:
        peak = max(float(pcm.max()), -float(pcm.min()))
        ss = float(np.vdot(pcm, pcm))
        if peak >= CLIP_LEVEL:
            st.clips += int(np.count_nonzero(np.abs(pcm) >= CLIP_LEVEL))
        if ss >= QUIET_POWER * pcm.size:
            st.t_loud = self._seen                       # (the quiet state: liveness; the bins run on arrival)
        if arrival - st.bin_t >= METER_BIN_S:
            if st.bin_n:
                st.bins.append((st.bin_peak, st.bin_ss, st.bin_n))
                st.bin_seq += 1
            st.bin_t, st.bin_peak, st.bin_ss, st.bin_n = arrival, 0.0, 0.0, 0
        st.bin_peak = max(st.bin_peak, peak)
        st.bin_ss += ss
        st.bin_n += pcm.size

    # -- heartbeats -------------------------------------------------------------------------------------------------
    def _heartbeat(self, st: _Stream, hb: vban.Heartbeat, arrival: float) -> None:
        prev, prev_arr = st.hb, st.hb_arr
        st.hb, st.hb_s, st.hb_arr = hb, self._seen, arrival
        if prev is not None and hb.sess != prev.sess:
            if st.clock is not None and (prev_arr is None or st.seg_t <= prev_arr):
                st.want_seg = ("sess", True)             # (a counter/idle segment since the last heartbeat covers it)
            st.ndiff = None
            st.hb_idle = hb.state in ("idle", "no_device", "off")
            return                                       # its (frame, n) belongs to the new sess: pair from the next
        if hb.state in ("idle", "no_device", "off"):
            st.hb_idle = True
        elif st.hb_idle:
            st.hb_idle = False
            if st.clock is not None and st.want_seg is None:
                st.want_seg = ("idle", True)
        if st.clock is None or st.capture is None or st.want_seg is not None:
            return
        step = hard = prev is not None and hb.xruns > prev.xruns
        if step:
            st.step_causes["xrun"] += 1
        if hb.frame is not None and hb.n is not None and hb.t_cap_ns is not None:
            n_rx = st.base_n + _signed(vban.counter_delta(st.base_c, hb.frame)) * st.frames
            if n_rx >= 0:
                seg = st.clock.seg
                if not st.capture.observe(n_rx, hb.t_cap_ns, seg=seg):
                    step = True
                    st.step_causes["capture"] += 1
                ndiff = hb.n - n_rx
                if st.ndiff is None or st.ndiff_seg != seg:
                    st.ndiff, st.ndiff_seg = ndiff, seg
                elif ndiff != st.ndiff:
                    step = hard = True
                    st.step_causes["ndiff"] += 1
        if step and not hard:
            # Only the capture line moved (no xrun, the sender's n still matches ours): the samples run on and their
            # host times come from arrivals (SampleClock, which steps by itself if time was really lost), so only
            # the CaptureMap (MIDI's audio map) starts over, which observe() just did. The audio gets a splice mark
            # (AUDIO_RESUME: dsp and the clock skip a packet's hits and peaks) instead of a segment, which reset the
            # DSP and madmom: on a loaded host this check fired every ~40 s (232 in 2.7 h, 18 Oct).
            st.flags |= AUDIO_RESUME
            st.capture_splices += 1
        elif step:
            st.want_seg = ("sender-step", True)
            st.sender_steps += 1

    # -- MIDI -------------------------------------------------------------------------------------------------------
    def _midi(self, st: _Stream, pkt: vban.TextPacket, msg: Any, arrival: float, size: int) -> None:
        if st.kind == "audio":
            self._junk += 1
            return
        if st.tmap is None:
            st.tmap = RemoteTimeMap(float(self._offsets.get(st.name, 0.0)))
        seen = self._seen
        st.kind = "midi"
        st.packets += 1
        st.bytes += size
        st.last_data_s = seen
        st.last_arr = arrival
        if st.first_data_s is None:
            st.first_data_s = seen
        if st.text_c is None:
            st.count_loss(arrival, 1, 0)
            st.text_c = pkt.counter
        else:
            d = vban.counter_delta(st.text_c, pkt.counter)
            if d == 0 or d >= _HALF:
                st.dup += 1                              # (passed through: midi.py dedupes by seq)
            else:
                lost = d - 1 if d <= MIDI_TEXT_GAP_MAX else 0
                st.lost += lost
                st.gaps += 1 if lost else 0
                st.count_loss(arrival, 1, lost)
                st.text_c = pkt.counter
        if isinstance(msg, vban.MidiBatch):
            t_ns = max((e[0] for e in msg.ev), default=None)
            if any((e[1] & 0xE0) == 0x80 or ((e[1] & 0xF0) == 0xB0 and e[2] in _PEDAL_CCS) for e in msg.ev):
                st.t_activity = seen
        else:
            t_ns = msg.t_ns
        if t_ns is not None:
            st.tmap.observe(t_ns, arrival)
        self._attach(st)
        if self._from_sock:                              # net.rx_lag_ms: dispatch - kernel arrival
            if st.lag is None:
                st.lag = deque(maxlen=2048)
            st.lag.append((seen, (self._clock() - arrival) * 1000.0))
        try:
            self._on_midi(st.name, msg, arrival, st.tmap.to_host)
        except Exception:                                # noqa: BLE001
            self._error("midi")

    def _attach(self, st: _Stream) -> None:
        """Put the MIDI stream on the best audio stream's map from the same source IP (§3.5)."""
        tmap = st.tmap
        if tmap is None:
            return
        best: Optional[_Stream] = None
        for a in self._streams.values():
            if (a.kind == "audio" and a.addr[0] == st.addr[0] and a.state not in ("down", "off")
                    and a.capture is not None and a.capture.ready
                    and (best is None or (a.rank, a.name) < (best.rank, best.name))):
                best = a
        if best is not None:
            if tmap.audio is not best.clock or tmap.capture is not best.capture:
                tmap.attach_audio(best.clock, best.capture)   # type: ignore[arg-type]
            st.attached = best.name
        elif st.attached is not None:
            a = self._streams.get(st.attached)
            if a is None or a.state in ("down", "off") or a.addr[0] != st.addr[0]:
                tmap.detach_audio()
                st.attached = None

    # -- states and the active stream -------------------------------------------------------------------------------
    def _state_of(self, st: _Stream, now: float) -> str:
        if st.name in self._off:
            return "off"
        hb, hb_s = st.hb, st.hb_s
        if hb is not None and hb_s is not None and now - hb_s < HB_FRESH_S:
            if hb.state == "no_device":
                return "no_device"
            if hb.state == "off" and (st.last_arr is None or (st.hb_arr or 0.0) >= st.last_arr):
                return "off"                             # (audio after that heartbeat: it is capturing again)
        last = st.last_data_s if st.kind != "unknown" else st.last_s
        if last is None or now - last > DOWN_S:
            return "down"
        if now - last > STALE_S:
            return "stale"
        since = st.first_data_s if st.first_data_s is not None else st.first_s
        if st.kind == "audio":
            if now - max(since, st.t_loud if st.t_loud is not None else -math.inf) > QUIET_AUDIO_S:
                return "quiet"
        elif st.kind == "midi":
            if now - max(since, st.t_activity if st.t_activity is not None else -math.inf) > QUIET_MIDI_S:
                return "quiet"
        return "streaming"

    def _update_states(self, now: float) -> None:
        for st in self._streams.values():
            s = self._state_of(st, now)
            if s != st.state:
                st.state, st.state_since = s, now
        for st in self._streams.values():
            if st.kind == "midi":
                self._attach(st)
        self._choose_active(now)

    def _choose_active(self, now: float) -> None:
        audio = [st for st in self._streams.values() if st.kind == "audio"]
        live = sorted((st for st in audio if st.state in LIVE), key=lambda s: (s.rank, s.name))
        cur = self._streams.get(self._active) if self._active is not None else None
        new = cur
        if cur is None:
            new = live[0] if live else None
        elif cur.state == "off":                         # switched off on purpose: no waiting
            new = live[0] if live else None
        elif cur.state in BAD:
            if now - cur.state_since >= SWITCH_AWAY_S:
                new = live[0] if live else None
        else:
            for st in live:
                if (st.rank, st.name) >= (cur.rank, cur.name):
                    break
                if st.state == "streaming" and now - st.state_since >= SWITCH_UP_S:
                    new = st
                    break
        if new is not cur:
            self._active = new.name if new is not None else None
            if new is not None:
                new.flags |= AUDIO_SEGMENT
            log.info("active audio: %s -> %s", cur.name if cur else None, self._active)

    # -- health -----------------------------------------------------------------------------------------------------
    def _build_health(self, now: float) -> None:
        senders = {}
        resolved, rerr = self._resolved, self._resolve_err
        for s in self._senders:
            hosts = sender_hosts(s)
            using = self._using.get(s.name)
            main = using or hosts[0]
            addr = resolved.get((s.name, main))
            last = self._sub_last.get(s.name)
            rows = []
            for h in hosts:
                a = resolved.get((s.name, h))
                t = self._answered.get((s.name, h))
                rows.append({"host": h, "addr": f"{a[0]}:{a[1]}" if a else None, "resolve_error": rerr.get((s.name, h)),
                             "answered_age_ms": _r((now - t) * 1000.0, 1) if t is not None else None})
            senders[s.name] = {"host": main, "port": s.port, "addr": f"{addr[0]}:{addr[1]}" if addr else None,
                               "resolve_error": rerr.get((s.name, main)), "send_error": self._send_err.get(s.name),
                               "streams": list(s.streams), "last_subscribe_ms": _r(last * 1000.0) if last else None,
                               "hosts": rows, "using": using, "on_demand": bool(s.on_demand)}
        # blocks: this window's counts, then the last STATS_WINDOW_S of windows
        self._win_hist.append((now, self._win_calls, self._win_pkts, self._win_fmax))
        self._win_calls = self._win_pkts = self._win_fmax = 0
        calls = pkts = fmax = 0
        for t, c, p, f in self._win_hist:
            if t > now - STATS_WINDOW_S:
                calls += c
                pkts += p
                fmax = max(fmax, f)
        sa, sb = self._sa, self._sb
        split = self._tsock is not None
        self._health = {
            "t_ms": round(now * 1000.0, 3), "active_audio": self._active,
            "socket": {"bind": self._bound or "{}:{}".format(*self.cfg.vban_bind), "error": self._sock_error,
                       "rx": self._rx, "junk": self._junk, "subscribe_rx": self._sub_rx, "bytes": self._bytes,
                       "rcvbuf": self._rcvbuf, "batch_max": self._batch_max, "backlogs": self._backlogs,
                       "blocks": self._blocks, "block_packets": _r(pkts / calls, 2) if calls else None,
                       "block_frames_max": fmax, "coalesce": self._coal_on, "coalesce_min": self._coal_min,
                       "stamps": "kernel" if sa.kts else "user",
                       "queue_ms": _pct([q for t, q in self._qms if t > now - STATS_WINDOW_S]),
                       "kernel_drops": sa.kernel_drops(), "ts_bad": sa.ts_bad,
                       "text": {"bind": self._t_bound if split else None, "rx": sb.rx,
                                "rcvbuf": self._t_rcvbuf if split else None, "split": split,
                                "error": self._split_error if not split else self._t_error,
                                "misrouted": self._misrouted, "rebinds": self._rebinds,
                                "stamps": ("kernel" if sb.kts else "user") if split else None,
                                "ts_bad": sb.ts_bad, "kernel_drops": sb.kernel_drops()}},
            "errors": dict(self._errors),
            "senders": senders,
            "priority": list(self._priority), "choice": list(self._choice), "device_hints": dict(self._device_hints),
            "off": sorted(self._off), "off_held": sorted(self._held), "off_decided": self._covered is not None,
            "off_dropped": self._off_dropped, "off_ignored": self._off_ignored,
            "streams": {st.name: self._stream_health(st, now) for st in self._streams.values()}}

    def _stream_health(self, st: _Stream, now: float) -> dict:
        last = st.last_data_s if st.last_data_s is not None else st.last_s
        age_ms = _r((now - last) * 1000.0, 1) if last is not None else None
        loss = st.loss_pct(now)
        out: dict[str, Any] = {"kind": st.kind, "rank": st.rank, "test": st.test, "addr": f"{st.addr[0]}:{st.addr[1]}",
                               "state": st.state, "format": None, "audio": None, "sender": None, "clock": None,
                               "off_dropped": st.off_dropped, "off_ignored": st.off_ignored}
        if st.kind == "audio" and st.clock is not None:
            clock = st.clock
            out["format"] = {"sr": st.sr, "channels": st.channels, "fmt": vban.FORMAT_NAMES.get(st.fmt, str(st.fmt)),
                             "frames": st.frames}
            out["net"] = {"packets": st.packets, "loss_pct": round(loss, 2), "late": st.late, "dup": st.dup,
                          "gaps": st.gaps, "lost": st.lost, "jitter_ms": _r(clock.jitter_ms),
                          "hold_ms": _r(st.hold_s() * 1000.0), "drift_ppm": _r(clock.skew_ppm),
                          "skew_alarm": clock.skew_alarm, "age_ms": age_ms, "segments": st.segments,
                          "sender_steps": st.sender_steps, "step_causes": dict(st.step_causes),
                          "capture_splices": st.capture_splices}
            out["audio"] = self._audio_health(st, now)
            out["clock"] = {**clock.state(), "capture": st.capture.state() if st.capture is not None else None}
            out["line"] = "{} · {:g}k/{}ch · {} dBFS · {:.1f} % loss · {:.0f} ms jitter".format(
                st.name, st.sr / 1000.0, st.channels, f"{out['audio']['rms_dbfs']:.0f}".replace("-", "−"), loss,
                clock.jitter_ms)
        else:
            rc = st.tmap.remote if st.tmap is not None else None
            out["net"] = {"packets": st.packets, "loss_pct": round(loss, 2), "late": st.late, "dup": st.dup,
                          "gaps": st.gaps, "lost": st.lost, "jitter_ms": _r(rc.jitter_ms) if rc else None,
                          "hold_ms": None, "drift_ppm": _r(rc.skew_ppm) if rc else None,
                          "skew_alarm": rc.skew_alarm if rc else False, "age_ms": age_ms,
                          "segments": rc.seg + 1 if rc else 0,
                          "rx_lag_ms": _pct([v for t, v in st.lag if t > now - MIDI_LAG_WINDOW_S]) if st.lag else None}
            if st.tmap is not None:
                out["clock"] = {**st.tmap.state(), "attached": st.attached}
            out["line"] = "{} · {} · {:.1f} % loss · {:.0f} ms jitter".format(
                st.name, "MIDI" if st.kind == "midi" else "?", loss, rc.jitter_ms if rc else 0.0)
        if st.state != "streaming":
            out["line"] += f" · {st.state}"
        if st.hb is not None and st.hb_s is not None:
            hb = st.hb
            out["sender"] = {"sess": hb.sess, "dev": hb.dev, "state": hb.state, "xruns": hb.xruns,
                             "in_lat_ms": hb.in_lat_ms, "age_ms": _r((now - st.hb_s) * 1000.0, 1),
                             "devices": list(hb.devices) if hb.devices is not None else None}
        return out

    def _audio_health(self, st: _Stream, now: float) -> dict:
        n_bins = max(1, int(round(METER_S / METER_BIN_S)))
        recent = list(st.bins)[-(n_bins - 1):] if n_bins > 1 else []
        if st.bin_n:
            recent.append((st.bin_peak, st.bin_ss, st.bin_n))
        peak = max((p for p, _, _ in recent), default=0.0)
        ss, n = sum(s for _, s, _ in recent), sum(c for _, _, c in recent)
        rms_db = _dbfs(ss / n) if n else DBFS_FLOOR
        floor = min((_dbfs(s / c) for _, s, c in st.bins if c), default=rms_db)
        last = list(st.bins)[-TRACE_BINS:]
        return {"peak_dbfs": _peak_dbfs(peak), "rms_dbfs": round(rms_db, 1), "clips": st.clips, "silent": rms_db < -60.0,
                "floor_dbfs": round(floor, 1),
                "trace": {"seq": st.bin_seq, "hz": round(1.0 / METER_BIN_S, 3),
                          "peak_dbfs": [_peak_dbfs(p) for p, _, _ in last],
                          "rms_dbfs": [round(_dbfs(ss_ / c), 1) if c else DBFS_FLOOR for _, ss_, c in last]}}
