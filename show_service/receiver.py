"""VBAN receiver: the UDP socket, SUBSCRIBE, the stream registry, gaps, segments, health and dispatch (docs/
SHOW_ENGINE.md §3.1, §3.3, §3.5; show_service/README.md "receiver.py" is the full contract).

Threads. ``show-rx`` owns everything here: ``recvfrom_into`` with a timeout of at most TICK_S (10 ms, shorter while a
hole is held), ``clock()`` stamped right after each datagram, :meth:`Receiver.handle` per datagram, held-hole
deadlines between datagrams, and :meth:`Receiver.tick` every TICK_S. ``show-dns`` resolves each configured sender's
host every 30 s (5 s while one fails; the last good address is kept) into a dict that show-rx reads by reference:
DNS never blocks show-rx. Callbacks (on_audio, on_midi, on_tick) run on show-rx; an exception in one is counted
and logged (rate-limited), never fatal. :meth:`health` and :meth:`set_offsets` are for any thread.

Socket. One UDP socket on ``cfg.vban_bind``, SO_RCVBUF asked for 4 MB and read back (Linux caps it at
net.core.rmem_max; never SO_RCVBUFFORCE). A failed bind shows in health and is retried every 2 s. Every 1 s (and
at once when an address first resolves or changes, or the sender list changes) it sends
``vban.subscribe_packet(sender.streams, counter)`` from that socket to each sender's resolved address: the sender
streams to the source of the latest one (NAT-safe).

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
    ``ok = capture.observe(n_rx, t_cap_ns, seg)``. A "sender-step" segment starts at the next packet when ``ok`` is
    False, when ``xruns`` rose within a sess, or when the sender's ``n - n_rx`` moved off its baseline (re-taken at
    the first heartbeat of each segment).
  * metering on every real packet (500 ms peak/RMS, clips at |x| >= 0.999, a 10 s floor of 100 ms RMS bins).

MIDI. The stream's RemoteTimeMap observes the newest ``t_ns`` of a batch (or a state's ``t_ns``) at arrival; it is
attached to the best-ranked audio stream from the same source IP whose CaptureMap is ready (detached when that one
goes down), then ``on_midi(stream, msg, arrival_s, tmap.to_host)``; duplicates pass through (midi.py dedupes by
seq). ``set_offsets`` sets each stream's acoustic_offset_ms (missing = 0) on its SampleClock or RemoteTimeMap.

States (re-evaluated every 0.1 s): no_device (a heartbeat < 2 s old says so), down (no data packet for > 5 s, or
ever), stale (> 0.5 s), quiet (audio: RMS under -60 dBFS for > 5 s; MIDI: no note or pedal for 30 s), else
streaming. ``active_audio()``: the best-ranked audio stream that is streaming or quiet; away from the current one
only after it has been stale/down/no_device for 1 s; up only to one that has been streaming for 3 s; None only when
no audio stream is live. MIDI is never a fallback. Health (README "Health") is rebuilt at 2 Hz.
"""

from __future__ import annotations

import logging
import math
import socket
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
LIVE = ("streaming", "quiet")
BAD = ("stale", "down", "no_device")
_HALF = 1 << 31
_MOD = 1 << 32
_PEDAL_CCS = frozenset(contract.PEDAL_CC)


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
        self.last_s: Optional[float] = None              # any packet (heartbeats too)
        self.first_data_s: Optional[float] = None
        self.last_data_s: Optional[float] = None         # audio / MIDI packets only
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
        self.hb_s: Optional[float] = None
        self.hb_idle = False
        self.ndiff: Optional[int] = None
        self.ndiff_seg = -1
        self.sender_steps = 0
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


class Receiver:
    """The VBAN receiver (module docstring; README "receiver.py")."""

    def __init__(self, cfg: Config, *, on_audio: OnAudio, on_midi: OnMidi, on_tick: OnTick,
                 clock: Callable[[], float] = time.monotonic, sock: Optional[socket.socket] = None,
                 on_level: Optional[Callable[[str, int, float, Optional[float], float], None]] = None) -> None:
        self.cfg = cfg
        self._on_audio, self._on_midi, self._on_tick = on_audio, on_midi, on_tick
        self._on_level = on_level                        # levels.LevelFeed.push: each live packet's peak, at arrival
        self._clock = clock
        self._sock = sock
        self._own_sock = sock is None
        self._sock_error: Optional[str] = None
        self._rcvbuf: Optional[int] = None
        self._bound: Optional[str] = None
        self._rx = self._junk = self._sub_rx = self._bytes = 0
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
        if self._own_sock and self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _adopt(self, sock: socket.socket) -> None:
        try:
            self._rcvbuf = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            host, port = sock.getsockname()[:2]
            self._bound = f"{host}:{port}"
        except OSError as e:
            self._sock_error = str(e)

    def _bind(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RCVBUF_WANT)
            except OSError:
                pass
            s.bind(tuple(self.cfg.vban_bind))
        except OSError as e:
            s.close()
            if self._sock_error != str(e):
                log.warning("VBAN bind %s:%s failed: %s (retrying every %.0f s)", *self.cfg.vban_bind, e,
                            BIND_RETRY_S)
            self._sock_error = str(e)
            return
        self._sock = s
        self._sock_error = None
        self._adopt(s)
        log.info("VBAN on %s, SO_RCVBUF %s", self._bound, self._rcvbuf)

    def _run_rx(self) -> None:
        buf = bytearray(RECV_BUF)
        mv = memoryview(buf)
        next_tick = self._clock()
        next_bind = next_tick
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
                try:
                    sock.settimeout(max(0.0005, timeout))
                    n, addr = sock.recvfrom_into(buf)
                except (socket.timeout, BlockingIOError, InterruptedError):
                    n = 0
                except OSError as e:                     # e.g. closed under us at stop
                    if self._stop.is_set():
                        break
                    self._sock_error = str(e)
                    self._stop.wait(TICK_S)
                    n = 0
                if n:
                    arrival = self._clock()
                    if self._sock_error is not None:     # a transient error (ICMP, ENOBUFS) has passed
                        self._sock_error = None
                    try:
                        self.handle(mv[:n], addr, arrival)
                    except Exception:                    # noqa: BLE001 - never let one datagram stop show-rx
                        self._error("handle")
            now = self._clock()
            if self._holding:
                self._expire_all(now)
            if now >= next_tick:
                try:
                    self.tick(now)
                except Exception:                        # noqa: BLE001
                    self._error("tick")
                next_tick += TICK_S
                if next_tick <= now:
                    next_tick = now + TICK_S

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

    def senders(self) -> tuple[contract.Sender, ...]:
        """The sender list show-rx uses now (any thread)."""
        return self._senders

    # -- show-rx ----------------------------------------------------------------------------------------------------
    def active_audio(self) -> Optional[str]:
        return self._active

    def handle(self, buf: bytes | bytearray | memoryview, addr: tuple[str, int], arrival_s: float) -> None:
        """One datagram (show-rx; tests call it directly with fake times)."""
        self._rx += 1
        self._bytes += len(buf)
        pkt = vban.parse(buf)
        if pkt is None:
            self._junk += 1
            return
        keys = self._addr_index.get((addr[0], addr[1])) if self._addr_index else None
        if keys:                                         # a configured host answered (the host fallback)
            for k in keys:
                self._answered[k] = arrival_s
        if isinstance(pkt, vban.AudioPacket):
            self._audio(pkt, addr, arrival_s, len(buf))
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
            self._heartbeat(self._stream(pkt.stream, addr, arrival_s), hb, arrival_s)
            return
        if msg is None:
            self._junk += 1
            return
        self._midi(self._stream(pkt.stream, addr, arrival_s), pkt, msg, arrival_s, len(buf))

    def tick(self, now_s: float) -> None:
        """SUBSCRIBE when due, held-hole deadlines, states/active (10 Hz), health (2 Hz), then on_tick (show-rx)."""
        if self._offsets_new is not None:
            self._offsets, self._offsets_new = self._offsets_new, None
            for st in self._streams.values():
                self._apply_offset(st)
        want = self._senders_want
        if want is not None and want is not self._senders:
            self._apply_senders(want)
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
            self._expire_all(now_s)
        if now_s >= self._states_next:
            self._states_next = now_s + STATES_EVERY_S
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

    # -- SUBSCRIBE --------------------------------------------------------------------------------------------------
    def _subscribe(self, now: float) -> None:
        self._sub_next = now + SUBSCRIBE_EVERY_S
        self._sub_gen = self._dns_gen
        sock, resolved = self._sock, self._resolved
        if sock is None:
            return
        hints = self._device_hints
        for s in self._senders:
            devices = {st: hints[st] for st in s.streams if st in hints} or None
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
                    sock.sendto(vban.subscribe_packet(s.streams, self._sub_counter, devices=devices), addr)
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
        st = self._stream(pkt.stream, addr, arrival)
        if st.kind == "midi":
            self._junk += 1
            return
        prev_data = st.last_data_s
        st.kind = "audio"
        st.packets += 1
        st.bytes += size
        st.last_data_s = arrival
        if st.first_data_s is None:
            st.first_data_s = arrival
        self._meter(st, pkt.pcm, arrival)
        if self._on_level is not None and st.clock is not None and st.name == self._active:
            self._level(st, pkt, arrival)
        if self._active is None:                         # nothing live: take this one now, not at the next 0.1 s
            self._update_states(arrival)
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
                self._deliver_real(st, pkt)
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
        self._deliver_real(st, pkt)

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
            st.seg_id = clock.seg
            st.segments += 1
            st.capture.reset(clock.seg)
            st.ndiff = None

    def _deliver_real(self, st: _Stream, pkt: vban.AudioPacket) -> None:
        flags, st.flags = st.flags, 0
        n0 = st.base_n
        st.base_c = (st.base_c + 1) % _MOD
        st.base_n += st.frames
        st.count_loss(st.last_data_s or 0.0, 1, 0)
        if st.name == self._active:
            self._emit(st, n0, pkt.pcm, flags)

    def _drain(self, st: _Stream) -> None:
        """Deliver held packets that are now next in line."""
        while st.pending:
            item = st.pending.pop(st.base_c, None)
            if item is None:
                return
            self._deliver_real(st, item[0])
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
            if st.name == self._active:
                self._emit(st, n0, np.zeros((missing * st.frames, st.channels), np.float32), flags)
            self._drain(st)

    def _expire_all(self, now: float) -> None:
        for st in list(self._streams.values()):
            if st.pending:
                self._expire(st, now)

    def _emit(self, st: _Stream, n0: int, pcm: np.ndarray, flags: int) -> None:
        assert st.clock is not None
        try:
            self._on_audio(st.name, n0, st.clock.to_host(n0), pcm, st.sr, flags)
        except Exception:                                # noqa: BLE001
            self._error("audio")

    def _level(self, st: _Stream, pkt: vban.AudioPacket, arrival: float) -> None:
        """The live meter (levels.py): this packet's peak and its capture time on the sender's clock, at arrival."""
        try:
            pcm = pkt.pcm
            peak = max(float(pcm.max()), -float(pcm.min())) if pcm.size else 0.0
            t = None
            if st.capture is not None and st.capture.ready:
                t = st.capture.to_remote_ns(st.base_n + _signed(vban.counter_delta(st.base_c, pkt.counter)) * st.frames)
            self._on_level(st.name, pkt.counter, peak, t, arrival)  # type: ignore[misc]
        except Exception:                                # noqa: BLE001
            self._error("audio")

    def _meter(self, st: _Stream, pcm: np.ndarray, arrival: float) -> None:
        peak = max(float(pcm.max()), -float(pcm.min()))
        ss = float(np.vdot(pcm, pcm))
        if peak >= CLIP_LEVEL:
            st.clips += int(np.count_nonzero(np.abs(pcm) >= CLIP_LEVEL))
        if ss >= QUIET_POWER * pcm.size:
            st.t_loud = arrival
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
        prev, prev_s = st.hb, st.hb_s
        st.hb, st.hb_s = hb, arrival
        if prev is not None and hb.sess != prev.sess:
            if st.clock is not None and (prev_s is None or st.seg_t <= prev_s):
                st.want_seg = ("sess", True)             # (a counter/idle segment since the last heartbeat covers it)
            st.ndiff = None
            st.hb_idle = hb.state in ("idle", "no_device")
            return                                       # its (frame, n) belongs to the new sess: pair from the next
        if hb.state in ("idle", "no_device"):
            st.hb_idle = True
        elif st.hb_idle:
            st.hb_idle = False
            if st.clock is not None and st.want_seg is None:
                st.want_seg = ("idle", True)
        if st.clock is None or st.capture is None or st.want_seg is not None:
            return
        step = prev is not None and hb.xruns > prev.xruns
        if hb.frame is not None and hb.n is not None and hb.t_cap_ns is not None:
            n_rx = st.base_n + _signed(vban.counter_delta(st.base_c, hb.frame)) * st.frames
            if n_rx >= 0:
                seg = st.clock.seg
                if not st.capture.observe(n_rx, hb.t_cap_ns, seg=seg):
                    step = True
                ndiff = hb.n - n_rx
                if st.ndiff is None or st.ndiff_seg != seg:
                    st.ndiff, st.ndiff_seg = ndiff, seg
                elif ndiff != st.ndiff:
                    step = True
        if step:
            st.want_seg = ("sender-step", True)
            st.sender_steps += 1

    # -- MIDI -------------------------------------------------------------------------------------------------------
    def _midi(self, st: _Stream, pkt: vban.TextPacket, msg: Any, arrival: float, size: int) -> None:
        if st.kind == "audio":
            self._junk += 1
            return
        if st.tmap is None:
            st.tmap = RemoteTimeMap(float(self._offsets.get(st.name, 0.0)))
        st.kind = "midi"
        st.packets += 1
        st.bytes += size
        st.last_data_s = arrival
        if st.first_data_s is None:
            st.first_data_s = arrival
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
                st.t_activity = arrival
        else:
            t_ns = msg.t_ns
        if t_ns is not None:
            st.tmap.observe(t_ns, arrival)
        self._attach(st)
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
            if (a.kind == "audio" and a.addr[0] == st.addr[0] and a.state != "down" and a.capture is not None
                    and a.capture.ready and (best is None or (a.rank, a.name) < (best.rank, best.name))):
                best = a
        if best is not None:
            if tmap.audio is not best.clock or tmap.capture is not best.capture:
                tmap.attach_audio(best.clock, best.capture)   # type: ignore[arg-type]
            st.attached = best.name
        elif st.attached is not None:
            a = self._streams.get(st.attached)
            if a is None or a.state == "down" or a.addr[0] != st.addr[0]:
                tmap.detach_audio()
                st.attached = None

    # -- states and the active stream -------------------------------------------------------------------------------
    def _state_of(self, st: _Stream, now: float) -> str:
        if st.hb is not None and st.hb_s is not None and now - st.hb_s < HB_FRESH_S and st.hb.state == "no_device":
            return "no_device"
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
        self._health = {
            "t_ms": round(now * 1000.0, 3), "active_audio": self._active,
            "socket": {"bind": self._bound or "{}:{}".format(*self.cfg.vban_bind), "error": self._sock_error,
                       "rx": self._rx, "junk": self._junk, "subscribe_rx": self._sub_rx, "bytes": self._bytes,
                       "rcvbuf": self._rcvbuf},
            "errors": dict(self._errors),
            "senders": senders,
            "priority": list(self._priority), "choice": list(self._choice), "device_hints": dict(self._device_hints),
            "streams": {st.name: self._stream_health(st, now) for st in self._streams.values()}}

    def _stream_health(self, st: _Stream, now: float) -> dict:
        last = st.last_data_s if st.last_data_s is not None else st.last_s
        age_ms = _r((now - last) * 1000.0, 1) if last is not None else None
        loss = st.loss_pct(now)
        out: dict[str, Any] = {"kind": st.kind, "rank": st.rank, "test": st.test, "addr": f"{st.addr[0]}:{st.addr[1]}",
                               "state": st.state, "format": None, "audio": None, "sender": None, "clock": None}
        if st.kind == "audio" and st.clock is not None:
            clock = st.clock
            out["format"] = {"sr": st.sr, "channels": st.channels, "fmt": vban.FORMAT_NAMES.get(st.fmt, str(st.fmt)),
                             "frames": st.frames}
            out["net"] = {"packets": st.packets, "loss_pct": round(loss, 2), "late": st.late, "dup": st.dup,
                          "gaps": st.gaps, "lost": st.lost, "jitter_ms": _r(clock.jitter_ms),
                          "hold_ms": _r(st.hold_s() * 1000.0), "drift_ppm": _r(clock.skew_ppm),
                          "skew_alarm": clock.skew_alarm, "age_ms": age_ms, "segments": st.segments,
                          "sender_steps": st.sender_steps}
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
                          "segments": rc.seg + 1 if rc else 0}
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
