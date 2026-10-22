#!/usr/bin/env python3
"""Streaming-latency lab server.

  synthetic NV12 frames --> NVENC (ffmpeg) --> per-frame fMP4 fragments
  --> WebSocket --> browser (MSE <video> | WebCodecs canvas | WebCodecs->MediaStream <video>)
  <-- per-frame timing reports (receive, ready, presented, expected-on-glass)

Every timestamp is on the server's monotonic epoch clock.  Clients (static/sync.js, beelink/fb_probe.py)
fit a skew-aware mapping to it over the same WebSocket (offset = a + b*t over the low-RTT envelope of
their pings) and convert BEFORE reporting: host clocks drift apart, and until 2026-10-11 this WSL host's
CLOCK_MONOTONIC ran ~2 % fast (chrony vs timesyncd, docs/KIOSK_INFRASTRUCTURE_NOTES.md), so a single offset
went stale by ~20 ms per second.  The server does no offset arithmetic; it only drops
reports made against another server instance ('epoch', a random id per server start, sent in every
'config' and 'pong').  Every stage of the pipeline can then be attributed:

  capture -> rendered -> written(pipe) -> encoded -> sent -> received
          -> ready (appended / decoded) -> presented -> expected on glass

Optional flow control: a page that sends {"type": "ack", "fid": N} as it handles frames gets at most
CREDIT_FRAMES unacknowledged frames in flight (the rest wait, and are dropped to the next keyframe, here).

Run (WSL, project venv):
  ../../.venv/bin/python server.py --codec h264 --size 1920x1080 --fps 60
Then open http://<host>:8081/ on the display box.  See README.md.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import math
import pathlib
import re
import socket
import struct
import sys
import threading
import time
import traceback
from typing import Optional

from aiohttp import WSMsgType, web

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lab.bitstream import StreamConfig  # noqa: E402
from lab.encoder import ENCODERS, MonotonicEpochClock, NvencPipeline  # noqa: E402
from lab.fmp4 import TIMESCALE, init_segment, media_fragment  # noqa: E402
from lab.source import PatternSource, barcode_geometry  # noqa: E402

clock = MonotonicEpochClock()
EPOCH = clock.id       # changes on every server start: clients drop clock samples taken against another one

MSG_INIT = 1
MSG_FRAME = 2
# type, flags(bit0=key), frame_id, t_capture, t_encoded, t_send, moof_len   (little endian, 34 bytes)
FRAME_HDR = struct.Struct("<BBIdddI")
MAX_QUEUE = 8          # frames of backlog per client (queued here + one stuck in the transport) before dropping to the next keyframe
WRITE_HIGH = 16 << 10  # bytes in the asyncio transport above which no further media frame is handed over
SNDBUF = 256 << 10     # small kernel send buffer + NOTSENT_LOWAT keep unsent bytes in the transport, where they are visible
NOTSENT_LOWAT = 64 << 10
TCP_NOTSENT_LOWAT = getattr(socket, "TCP_NOTSENT_LOWAT", 25)
CREDIT_FRAMES = 4      # with acks: frames in flight to a page before the server stops sending
RECORD_KEEP = 4000     # per-frame server records retained
ROWS_KEEP = 20000      # report rows retained per session (hub level, so reconnects do not lose them)
DEPARTED_KEEP_S = 600  # closed connections are kept this long for their counters
REPORT_GRACE_S = 3.0   # pages report a never-presented frame 2 s after it arrived (+ 250 ms flush, + its latency)
PLAUSIBLE_MS = (1000, 60000)  # report timestamps outside [capture - 1 s, capture + 60 s] are rejected as bad
SNAP_PERIOD_S = 0.1    # counter snapshots for windowed /api/state deltas (the matrix takes exact ones)
SKEW_WARN_PPM = 100

FLAG_KEY, FLAG_EST, FLAG_GAP, FLAG_OVERDRAWN, FLAG_NEVER = 1, 8, 16, 32, 64
NOT_PRESENTED = FLAG_OVERDRAWN | FLAG_NEVER

STAGES = ["render", "pipe", "encode", "queue", "net", "client", "present", "total", "total_presented",
          "fb", "expected_minus_fb",  # fb*: from beelink/fb_probe.py (X framebuffer read-back)
          "sched"]  # source thread lateness vs its schedule (diagnostic, not part of total)
COUNTERS = ("sent", "dropped", "join_skipped", "barcode_ok", "barcode_bad", "barcode_race",
            "items_bad", "items_stale")


def pct(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def parse_size(s: str) -> tuple[int, int]:
    w, h = s.lower().split("x")
    return int(w), int(h)


def num(v) -> Optional[float]:
    """A finite number from a report, else None (reports may carry null for any timestamp)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def pace_clock() -> float:
    # CLOCK_MONOTONIC on this WSL host runs ~1.9 % fast (kernel tick); RAW tracks real time to < 100 ppm
    return time.clock_gettime(getattr(time, "CLOCK_MONOTONIC_RAW", time.CLOCK_MONOTONIC))


class ConfigError(ValueError):
    pass


class RunAborted(Exception):
    pass


def validate_cfg(cfg: dict) -> None:
    """Reject a session config BEFORE the running session is torn down for it."""
    for k in ("codec", "size", "fps"):
        if cfg.get(k) in (None, ""):
            raise ConfigError(f"session config needs {k!r}")
    if cfg["codec"] not in ENCODERS:
        raise ConfigError(f"codec must be one of {list(ENCODERS)}, not {cfg['codec']!r}")
    try:
        w, h = parse_size(str(cfg["size"]))
        fps = int(cfg["fps"])
    except (TypeError, ValueError):
        raise ConfigError(f"bad size/fps {cfg['size']!r}@{cfg['fps']!r} (want e.g. 1920x1080 and 30)") from None
    if w <= 0 or h <= 0 or w % 2 or h % 2:
        raise ConfigError(f"size {w}x{h}: width and height must be positive and even (NV12)")
    if fps <= 0 or TIMESCALE % fps:
        raise ConfigError(f"fps {fps} must divide {TIMESCALE}")
    if cfg.get("source", "pattern") not in ("pattern", "cuda"):
        raise ConfigError(f"source must be pattern or cuda, not {cfg['source']!r}")
    # every value Session() converts: a bad one must fail here, not after the running session was stopped
    for k, cast, lo, hi in (("gop", float, 1e-3, None), ("complexity", int, 0, 3), ("gpu", int, -1, None),
                            ("render_gpu", int, 0, None), ("vbv_frames", float, 1e-3, None), ("slices", int, 1, None)):
        v = cfg.get(k)
        if v is None:
            continue
        try:
            x = cast(v)
        except (TypeError, ValueError):
            raise ConfigError(f"{k} must be {'a number' if cast is float else 'an integer'}, not {v!r}") from None
        if not math.isfinite(x) or x < lo or (hi is not None and x > hi):
            raise ConfigError(f"{k} {v!r} out of range")
    if not re.fullmatch(r"\d+(\.\d+)?[kKmMgG]?", str(cfg.get("bitrate", "8M"))):
        raise ConfigError(f"bitrate {cfg.get('bitrate')!r}: want e.g. 8M, 8000k or 8000000")
    if cfg.get("intra_refresh"):
        raise ConfigError("intra_refresh was removed: it cannot coexist with drop-to-keyframe (a viewer that "
                         "joins, re-hellos or overflows waits for an IDR that never comes)")


# ---------------------------------------------------------------------------
class Session:
    """One encoder configuration: source thread + NVENC pipeline."""

    _gen = 0

    def __init__(self, hub: "Hub", cfg: dict):
        Session._gen += 1
        self.gen = Session._gen
        self.hub = hub
        self.cfg = cfg
        self.codec = cfg["codec"]
        self.w, self.h = parse_size(cfg["size"])
        self.fps = int(cfg["fps"])
        self.dur = TIMESCALE // self.fps
        if TIMESCALE % self.fps:
            raise ValueError(f"fps {self.fps} must divide {TIMESCALE}")
        self.records: dict[int, dict] = {}
        self.records_order: collections.deque[int] = collections.deque()
        self.stream_cfg: Optional[StreamConfig] = None
        self.init: Optional[bytes] = None
        self.announced: Optional[bytes] = None  # the init segment last sent to the viewers
        self.started_at = clock()
        self.late_frames = 0
        self.exit_info: Optional[str] = None
        self.fb: dict[int, float] = {}  # frame id -> first seen in the X framebuffer (server clock)
        self.stop_evt = threading.Event()  # deliberate stop
        self.failed = threading.Event()    # encoder gone: the source thread must stop feeding it
        if cfg.get("source", "pattern") == "cuda":
            from lab.gpu_source import CudaSource  # needs CuPy (see README)
            self.source = CudaSource(self.w, self.h, self.fps, int(cfg.get("complexity", 2)),
                                     device=int(cfg.get("render_gpu", 0)))
        else:
            self.source = PatternSource(self.w, self.h, self.fps, int(cfg.get("complexity", 2)))
        self.pipeline = NvencPipeline(
            self.codec, self.w, self.h, self.fps,
            gpu=str(cfg.get("gpu", "0")), bitrate=str(cfg.get("bitrate", "8M")),
            gop_seconds=float(cfg.get("gop", 1.0)), preset=str(cfg.get("preset", "p1")),
            profile=cfg.get("profile"),
            vbv_frames=cfg.get("vbv_frames"), fix_reorder=not cfg.get("no_reorder_fix", False),
            slices=cfg.get("slices"),
            clock=clock, on_config=self._on_config, on_frame=self._on_frame, on_exit=self._on_exit)
        self.thread = threading.Thread(target=self._gen_loop, name="source", daemon=True)
        self.thread.start()

    # -- generator thread --------------------------------------------------
    def _gen_loop(self) -> None:
        # Paced on CLOCK_MONOTONIC_RAW, not clock(): on this host CLOCK_MONOTONIC runs ~2 % fast, which
        # would make a nominal 30 fps source really emit ~30.5 fps into a 30 Hz panel.
        period = 1.0 / self.fps
        start = pace_clock() + 0.05
        n = 0
        try:
            while not (self.stop_evt.is_set() or self.failed.is_set()):
                target = start + n * period
                while True:
                    delta = target - pace_clock()
                    if delta <= 0:
                        break
                    time.sleep(delta - 0.0015 if delta > 0.002 else 0)
                slip = pace_clock() - target
                if slip > period:
                    self.late_frames += 1  # cannot keep real time (source+pipe too slow)
                t_capture = clock()
                frame = self.source.render(n, t_capture)
                t_rendered = clock()
                rec = {"t_capture": t_capture, "t_rendered": t_rendered, "slip": slip * 1000}
                self.records[n] = rec
                self.records_order.append(n)
                while len(self.records_order) > RECORD_KEEP:
                    self.records.pop(self.records_order.popleft(), None)
                rec["t_written"] = self.pipeline.submit(frame)
                n += 1
        except (BrokenPipeError, ValueError, OSError) as e:
            if not self.stop_evt.is_set() and self.exit_info is None:  # keep the encoder's own reason
                self.exit_info = f"source stopped: {type(e).__name__}: {e}"

    # -- encoder reader thread callbacks -----------------------------------
    def _on_config(self, scfg: StreamConfig) -> None:
        self.stream_cfg = scfg
        self.init = init_segment(scfg)
        self.hub.loop.call_soon_threadsafe(self.hub.on_session_config, self)

    def _on_frame(self, frame_id: int, key: bool, sample: bytes, t_encoded: float) -> None:
        rec = self.records.get(frame_id)
        if rec is None:
            return
        rec["t_encoded"] = t_encoded
        rec["size"] = len(sample)
        rec["key"] = key
        frag, moof_len = media_fragment(frame_id + 1, frame_id * self.dur, self.dur, sample, key)
        self.hub.loop.call_soon_threadsafe(self.hub.on_frame, self, frame_id, key,
                                           rec["t_capture"], t_encoded, frag, moof_len)

    def _on_exit(self, rc: int, tail: str) -> None:
        if not self.stop_evt.is_set():
            self.exit_info = f"encoder exited rc={rc}: {tail}"
            self.failed.set()
            print(f"[session {self.gen}] {self.exit_info}", flush=True)

    def stop(self) -> None:
        self.stop_evt.set()
        self.pipeline.close()
        self.thread.join(timeout=2)

    def config_message(self) -> dict:
        s = self.stream_cfg
        return {
            "type": "config", "session": self.gen, "epoch": EPOCH, "codec": self.codec,
            "codecString": s.codec_string if s else None,
            "description": s.record.hex() if s else None,
            "width": self.w, "height": self.h, "fps": self.fps, "timescale": TIMESCALE,
            "frameDuration": self.dur, "barcode": barcode_geometry(self.w, self.h),
            "notes": s.notes if s else [], "settings": self.cfg,
        }


# ---------------------------------------------------------------------------
class Client:
    _ids = 0

    def __init__(self, ws: web.WebSocketResponse, remote: str, transport: Optional[asyncio.Transport] = None):
        Client._ids += 1
        self.id = Client._ids
        self.ws = ws
        self.transport = transport
        self.remote = remote
        self.role = "viewer"
        self.hello = False
        self.mode = "?"
        self.info: dict = {}
        self.params: dict = {}      # client params as last reported by the page (mode, target, draw, hw)
        self.clock: dict = {}       # sync quality as last reported {rtt, ppm, resid, n}
        self.skew_warned = False
        self.control: collections.deque = collections.deque()
        self.media: collections.deque = collections.deque()
        self.wake = asyncio.Event()
        self.session_gen = 0
        self.needs_key = True
        self.streaming = False      # has had a frame queued in the current session (key waits after this are drops)
        self.credit = False         # page sends acks -> bound frames in flight
        self.unacked: collections.deque[int] = collections.deque()
        self.msg_errors = 0
        self.closed_at: Optional[float] = None
        # lifetime counters of this connection (per-run figures are deltas of snapshots, see Hub.snapshot)
        self.sent = 0
        self.dropped = 0            # frames lost to drop-to-key (overflow, then key wait) once streaming
        self.join_skipped = 0       # frames skipped waiting for the first keyframe after (re)joining
        self.barcode_ok = 0
        self.barcode_bad = 0
        self.barcode_race = 0
        self.items_bad = 0          # malformed report items
        self.items_stale = 0        # items for another server epoch / session, or before the page had synced
        self.sent_times: dict[int, float] = {}
        self.events: collections.deque = collections.deque(maxlen=200)
        self.barcode_cost: collections.deque = collections.deque(maxlen=500)

    def push_control(self, msg) -> None:
        self.control.append(msg)
        self.wake.set()

    def counters(self) -> dict:
        return {k: getattr(self, k) for k in COUNTERS}

    def transport_backlog(self) -> int:
        try:
            return self.transport.get_write_buffer_size() if self.transport is not None else 0
        except Exception:
            return 0

    def credit_exhausted(self) -> bool:
        return self.credit and len(self.unacked) >= CREDIT_FRAMES

    def on_ack(self, fid) -> None:
        if isinstance(fid, bool) or not isinstance(fid, int):
            return
        self.credit = True
        while self.unacked and self.unacked[0] <= fid:
            self.unacked.popleft()
        self.wake.set()


def tune_socket(transport: Optional[asyncio.Transport]) -> None:
    """Small SO_SNDBUF + TCP_NOTSENT_LOWAT: otherwise the kernel takes megabytes (tcp_wmem max 4 MB here),
    the sender never blocks, and seconds of stale frames hide where drop-to-key cannot see them."""
    sock = transport.get_extra_info("socket") if transport is not None else None
    if sock is None:
        return
    for level, opt, val in ((socket.SOL_SOCKET, socket.SO_SNDBUF, SNDBUF),
                            (socket.IPPROTO_TCP, TCP_NOTSENT_LOWAT, NOTSENT_LOWAT)):
        try:
            sock.setsockopt(level, opt, val)
        except OSError:
            pass


class Hub:
    def __init__(self, args):
        self.args = args
        self.loop: asyncio.AbstractEventLoop = None  # set on startup
        self.clients: dict[int, Client] = {}
        self.departed: dict[int, Client] = {}  # closed connections, kept for their counters
        self.session: Optional[Session] = None
        self.cfg: dict = {}  # last requested session config; POST /api/session merges into it
        self.session_lock = asyncio.Lock()
        self.rows: dict[int, collections.deque] = {}  # session gen -> report rows of every connection
        self.mse: dict[int, collections.deque] = {}   # session gen -> (t, cid, remote, mode, ahead, rate, stalls)
        self.snaps: collections.deque = collections.deque(maxlen=1500)  # counter snapshots, every SNAP_PERIOD_S
        self.default_mode = args.mode
        self.log_path = HERE / "runs" / time.strftime("%Y%m%d-%H%M%S")
        self.log_path.mkdir(parents=True, exist_ok=True)
        self.frame_log = open(self.log_path / "frames.jsonl", "a", buffering=1)
        self.clock_log = open(self.log_path / "clock.jsonl", "a", buffering=1)  # per-report sync quality
        self.clock_ref = (clock(), pace_clock())  # for the server's own CLOCK_MONOTONIC vs RAW rate
        self.clock_warned = False

    # -- session management -------------------------------------------------
    async def start_session(self, cfg: Optional[dict] = None, *, update: Optional[dict] = None) -> Session:
        """Replace the running session with `cfg` (or the last requested config) updated by `update`.

        Serialised: overlapping calls (API, matrix) used to each tear down and build a pipeline, orphaning
        one ffmpeg/NVENC session forever.  The merge happens under the lock, so a partial update never sees
        an empty config mid-restart, and the config is validated before the old session is stopped."""
        async with self.session_lock:
            new = {**(self.cfg if cfg is None else cfg), **(update or {})}
            validate_cfg(new)
            prev, self.cfg = self.cfg, new
            old, self.session = self.session, None
            if old:
                await self.loop.run_in_executor(None, old.stop)
            try:
                sess = await self.loop.run_in_executor(None, Session, self, new)
            except Exception as e:
                # validated, yet failed (no CuPy for source=cuda, ffmpeg missing...): keep the last good config
                # (later partial updates merge into it) and put the previous stream back
                self.cfg = old.cfg if old else prev
                print(f"[session] {type(e).__name__}: {e}; config not applied", flush=True)
                if old:
                    try:
                        self._install(await self.loop.run_in_executor(None, Session, self, old.cfg), "restored")
                    except Exception as e2:
                        print(f"[session] could not restore the previous config: {type(e2).__name__}: {e2}", flush=True)
                raise
            self._install(sess, "started")
            return sess

    def _install(self, sess: Session, what: str) -> None:
        self.session = sess
        for gen in [g for g in (*self.rows, *self.mse) if g < sess.gen - 1]:  # keep current + previous
            self.rows.pop(gen, None)
            self.mse.pop(gen, None)
        print(f"[session {sess.gen}] {what} {sess.cfg}", flush=True)
        if sess.init is not None:  # the encoder's config arrived before the assignment above: announce it now
            self.on_session_config(sess)

    def on_session_config(self, sess: Session) -> None:
        if sess is not self.session or sess.announced is sess.init:
            return
        sess.announced = sess.init
        for line in (sess.stream_cfg.notes if sess.stream_cfg else []):
            print(f"[session {sess.gen}] {line}", flush=True)
        for c in self.clients.values():
            self._send_config(c)

    def _send_config(self, c: Client) -> None:
        sess = self.session
        if not sess or not sess.init:
            return
        if c.role == "probe":
            c.push_control(json.dumps(sess.config_message()))
            return
        if c.role != "viewer":
            return
        c.media.clear()
        c.unacked.clear()
        c.needs_key = True
        c.streaming = False
        if c.session_gen != sess.gen:
            c.sent_times.clear()  # frame ids restart at 0 in every session
        c.session_gen = sess.gen
        c.push_control(json.dumps(sess.config_message()))
        c.push_control(bytes([MSG_INIT]) + sess.init)

    def viewers(self) -> list[Client]:
        return [c for c in self.clients.values() if c.role == "viewer" and c.hello]

    def on_frame(self, sess: Session, frame_id: int, key: bool, t_capture: float,
                 t_encoded: float, frag: bytes, moof_len: int) -> None:
        if sess is not self.session:
            return
        for c in self.clients.values():
            if c.role != "viewer" or c.session_gen != sess.gen:
                continue
            if c.needs_key and not key:
                if c.streaming:
                    c.dropped += 1
                else:
                    c.join_skipped += 1
                continue
            # backlog = frames waiting here + the one the transport is still busy with (the sender only
            # hands a frame over once the transport has drained, see sender())
            backlog = len(c.media) + (c.transport_backlog() > WRITE_HIGH)
            if backlog >= MAX_QUEUE:
                c.dropped += len(c.media)
                c.events.append({"t": clock(), "event": "server-drop-to-key", "frame": frame_id,
                                 "queued": len(c.media), "transport_bytes": c.transport_backlog(),
                                 "unacked": len(c.unacked) if c.credit else None})
                c.media.clear()
                if not key:  # wait for the next keyframe; an arriving keyframe restarts the stream at once
                    c.needs_key = True
                    c.dropped += 1
                    continue
            c.needs_key = False
            c.streaming = True
            c.media.append((frame_id, key, t_capture, t_encoded, frag, moof_len))
            c.wake.set()

    # -- per-client sender ----------------------------------------------------
    async def sender(self, c: Client) -> None:
        try:
            await self._send_loop(c)
        except ConnectionError:  # peer gone mid-send (aiohttp: "Cannot write to closing transport"); ws_handler cleans up
            pass

    async def _send_loop(self, c: Client) -> None:
        ws = c.ws
        while not ws.closed:
            await c.wake.wait()
            c.wake.clear()
            while (c.control or c.media) and not ws.closed:
                if c.control:
                    msg = c.control.popleft()
                    if isinstance(msg, tuple):  # ("pong", id, c0, t_rx): stamp the reply as it leaves
                        t_tx = clock()
                        # NTP-style: s = middle of the server's hold time, so time spent queued here behind
                        # media shows up as extra RTT (and is filtered by the client) instead of as offset
                        msg = json.dumps({"type": "pong", "id": msg[1], "c0": msg[2], "s": (msg[3] + t_tx) / 2,
                                          "rx": msg[3], "tx": t_tx, "epoch": EPOCH})
                    if isinstance(msg, bytes):
                        await ws.send_bytes(msg)
                    else:
                        await ws.send_str(msg)
                    continue
                # A frame handed to the transport can no longer be dropped: keep it in c.media (visible to
                # on_frame's MAX_QUEUE check) until the transport has drained and, with acks, the page keeps up.
                if c.transport_backlog() > WRITE_HIGH or c.credit_exhausted():
                    await asyncio.sleep(0.003)  # aiohttp has no public "drained" event
                    continue
                frame_id, key, t_capture, t_encoded, frag, moof_len = c.media.popleft()
                t_send = clock()
                hdr = FRAME_HDR.pack(MSG_FRAME, 1 if key else 0, frame_id, t_capture, t_encoded, t_send, moof_len)
                await ws.send_bytes(hdr + frag)
                c.sent += 1
                c.sent_times[frame_id] = t_send
                if c.credit:
                    c.unacked.append(frame_id)
                if len(c.sent_times) > RECORD_KEEP:
                    for k in list(c.sent_times)[: len(c.sent_times) - RECORD_KEEP]:
                        del c.sent_times[k]

    # -- reports from clients -------------------------------------------------
    def _note_clock(self, c: Client, clk, session) -> None:
        if not isinstance(clk, dict):
            return
        c.clock = {k: clk.get(k) for k in ("rtt", "ppm", "resid", "n")}
        self.clock_log.write(json.dumps({"t": clock(), "cid": c.id, "role": c.role, "session": session,
                                         **c.clock}, separators=(",", ":")) + "\n")
        ppm, n = num(clk.get("ppm")), num(clk.get("n"))
        if ppm is not None and abs(ppm) > SKEW_WARN_PPM and not c.skew_warned and (n or 0) >= 5:
            c.skew_warned = True
            print(f"[client {c.id} {c.mode}] clock skew {ppm:+.0f} ppm vs the server clock (> {SKEW_WARN_PPM}): "
                  f"timestamps are drift-corrected by the page; fit resid {clk.get('resid')} ms, "
                  f"rtt {clk.get('rtt')} ms", flush=True)

    def ingest(self, c: Client, msg: dict) -> None:
        # events and barcodes carry no clock arithmetic: always keep them (the page clears them once sent)
        for ev in msg.get("events") or []:
            if not isinstance(ev, dict):
                continue
            ev["t_server"] = clock()
            c.events.append(ev)
            print(f"[client {c.id} {c.mode}] event {json.dumps(ev)[:400]}", flush=True)
        for bc in msg.get("barcodes") or []:
            if not isinstance(bc, dict):
                continue
            read, frame = num(bc.get("read")), num(bc.get("frame"))
            c.barcode_cost.append(num(bc.get("cost")) or 0)
            if bc.get("ok"):
                c.barcode_ok += 1
            elif read is not None and frame is not None and abs(read - frame) == 1:
                # drawImage(video) grabs the element's *current* frame, which may already have advanced
                # past the frame the rVFC callback describes: a timing race, not a mis-identification.
                c.barcode_race += 1
            else:
                c.barcode_bad += 1
                c.events.append({"event": "barcode-mismatch", "t_server": clock(), **bc})
        if isinstance(msg.get("params"), dict):
            c.params = msg["params"]
        sess = self.session
        same_session = sess is not None and msg.get("session") == sess.gen
        mse = msg.get("mse")
        if same_session and isinstance(mse, dict):
            self.mse.setdefault(sess.gen, collections.deque(maxlen=ROWS_KEEP)).append(
                (clock(), c.id, c.remote, c.mode, num(mse.get("ahead")), num(mse.get("rate")), num(mse.get("stalls"))))
        items = msg.get("items") or []
        if msg.get("epoch") != EPOCH:  # converted with another server instance's clock
            c.items_stale += len(items)
            return
        clk = msg.get("clock")
        self._note_clock(c, clk, msg.get("session"))
        if not same_session or not isinstance(clk, dict) or not num(clk.get("n")):
            c.items_stale += len(items)
            return
        rows = self.rows.setdefault(sess.gen, collections.deque(maxlen=ROWS_KEEP))
        for item in items:
            try:
                row = self._row(c, sess, item)
            except (TypeError, ValueError, IndexError):
                c.items_bad += 1
                continue
            if row is None:
                continue
            rows.append(row)
            self.frame_log.write(json.dumps(row, separators=(",", ":")) + "\n")

    def _row(self, c: Client, sess: Session, item) -> Optional[dict]:
        """One report item -> stage row.  Item timestamps are already on the server clock; any may be null,
        and a stage is computed only when both of its ends exist."""
        fid, t_recv, t_ready, t_present, t_expected, flags = item
        fid, flags = int(fid), int(flags or 0)
        rec = sess.records.get(fid)
        t_send = c.sent_times.get(fid)
        if not rec or t_send is None or "t_encoded" not in rec:
            return None  # not (or no longer) known as sent to this connection
        t_recv, t_ready, t_present, t_expected = (num(v) for v in (t_recv, t_ready, t_present, t_expected))
        t_cap = rec["t_capture"]
        for v in (t_recv, t_ready, t_present, t_expected):
            if v is not None and not t_cap - PLAUSIBLE_MS[0] <= v <= t_cap + PLAUSIBLE_MS[1]:
                raise ValueError(f"timestamp {v} implausible for capture {t_cap}")  # unconverted / broken clock
        presented = t_expected is not None and not flags & NOT_PRESENTED
        row = {"cid": c.id, "remote": c.remote, "mode": c.mode, "session": sess.gen, "frame": fid,
               "key": rec.get("key"), "size": rec.get("size"), "flags": flags, "presented": presented}
        for name, a, b in (("render", rec.get("t_rendered"), t_cap),
                           ("pipe", rec.get("t_written"), rec.get("t_rendered")),
                           ("encode", rec["t_encoded"], rec.get("t_written")),
                           ("queue", t_send, rec["t_encoded"]),
                           ("net", t_recv, t_send),
                           ("client", t_ready, t_recv),
                           ("present", t_expected if presented else None, t_ready),
                           ("total", t_expected if presented else None, t_cap),
                           ("total_presented", t_present if presented else None, t_cap)):
            if a is not None and b is not None:
                row[name] = a - b
        if "slip" in rec:
            row["sched"] = rec["slip"]
        row["t"] = t_cap
        return row

    def ingest_fb(self, c: Client, msg: dict) -> None:
        c.info["probe_stats"] = msg.get("stats")
        items = msg.get("items") or []
        if msg.get("epoch") != EPOCH:
            c.items_stale += len(items)
            return
        self._note_clock(c, msg.get("clock"), msg.get("session"))
        sess = self.session
        if not sess or msg.get("session") != sess.gen:
            c.items_stale += len(items)
            return
        for item in items:
            try:
                fid, t_fb = int(item[0]), num(item[1])  # t_fb: already on the server clock
            except (TypeError, ValueError, IndexError):
                c.items_bad += 1
                continue
            rec = sess.records.get(fid)
            if t_fb is not None and rec is not None:
                if not rec["t_capture"] - PLAUSIBLE_MS[0] <= t_fb <= rec["t_capture"] + PLAUSIBLE_MS[1]:
                    c.items_bad += 1
                    continue
                sess.fb.setdefault(fid, t_fb)
        if len(sess.fb) > RECORD_KEEP:
            for k in sorted(sess.fb)[: len(sess.fb) - RECORD_KEEP]:
                del sess.fb[k]

    # -- counters + statistics ---------------------------------------------------
    def snapshot(self) -> dict:
        sess = self.session
        return {"t": clock(), "gen": sess.gen if sess else None, "late": sess.late_frames if sess else 0,
                "c": {c.id: c.counters() for c in (*self.clients.values(), *self.departed.values())}}

    def _snap_at(self, t: float) -> Optional[dict]:
        """The periodic snapshot nearest to t (None if t predates them all)."""
        best = None
        for s in reversed(self.snaps):
            if best is None or abs(s["t"] - t) < abs(best["t"] - t):
                best = s
            if s["t"] <= t:
                break
        return best if best is not None and best["t"] - t < 1000 * SNAP_PERIOD_S else None

    def server_clock_ppm(self) -> Optional[float]:
        """Rate of clock() (CLOCK_MONOTONIC) against CLOCK_MONOTONIC_RAW since start."""
        c0, r0 = self.clock_ref
        dr = (pace_clock() - r0) * 1000
        return None if dr < 5000 else ((clock() - c0) / dr - 1) * 1e6

    def clock_check(self) -> None:
        if self.clock_warned or pace_clock() - self.clock_ref[1] < 30:
            return
        ppm = self.server_clock_ppm()
        if ppm is not None and abs(ppm) > 1000:
            self.clock_warned = True
            print(f"[server] note: CLOCK_MONOTONIC runs {ppm:+.0f} ppm vs CLOCK_MONOTONIC_RAW here (WSL kernel "
                  f"tick). Remote pages see this as clock skew and correct it (sync.js fit); the source is paced "
                  f"on RAW.", flush=True)

    def stats(self, seconds: float = 10.0, since: Optional[float] = None, until: Optional[float] = None,
              base: Optional[dict] = None, top: Optional[dict] = None, sess: Optional[Session] = None) -> dict:
        """Per-viewer statistics over frames CAPTURED in [since, until).

        Without `since`: the last `seconds`, ending REPORT_GRACE_S ago (never-presented frames are only
        reported 2 s after they arrived).  Counters are deltas between the snapshots `base` and `top`
        (default: the periodic snapshots nearest the window ends).  Rows of every connection of the
        session are kept, so a viewer that reconnected is aggregated per (remote, mode)."""
        now = clock()
        if since is None:
            hi = now - REPORT_GRACE_S * 1000
            lo = hi - seconds * 1000
        else:
            lo, hi = since, (until if until is not None else now)
        base = base if base is not None else self._snap_at(lo)
        top = top if top is not None else (self._snap_at(hi) if hi < now - 300 else self.snapshot())
        sess = sess or self.session
        gen = sess.gen if sess else None
        late = None
        if sess:  # source frames that slipped > 1 period, counted in the window only (not the start-up burst)
            late = (top["late"] if top and top["gen"] == gen else sess.late_frames) - \
                (base["late"] if base and base["gen"] == gen else 0)
        recs = [r for r in list(sess.records.values()) if lo <= r["t_capture"] < hi] if sess else []
        slips = [r["slip"] for r in recs if "slip" in r]
        out = {"server_time": now, "epoch": EPOCH, "window": [lo, hi],
               "server_clock_ppm_vs_raw": self.server_clock_ppm(),
               "session": sess.config_message() if sess else None,
               "late_source_frames": late, "source_slips_over_2ms": sum(1 for s in slips if s > 2),
               "encoder_exit": sess.exit_info if sess else None,
               "fb_frames": len(sess.fb) if sess else 0,
               "fb_sample": sorted(sess.fb)[-5:] if sess else [], "clients": []}
        known = {**self.departed, **self.clients}
        zero = dict.fromkeys(COUNTERS, 0)

        def delta(cid: int) -> dict:
            t = top["c"].get(cid) if top else None
            if t is None:
                c = known.get(cid)
                t = c.counters() if c is not None and not top else zero
            b = (base["c"].get(cid) if base else None) or zero
            return {k: t[k] - b[k] for k in COUNTERS}

        rows = [r for r in (self.rows.get(gen) or ()) if lo <= r["t"] < hi]
        groups: dict[tuple, dict] = {}
        for r in rows:
            g = groups.setdefault((r["remote"], r["mode"]), {"cids": set(), "rows": []})
            g["rows"].append(r)
            g["cids"].add(r["cid"])
        for c in known.values():  # viewers with nothing reported in the window still get a row
            if c.role == "viewer" and c.session_gen == gen and gen is not None and \
                    (c.id in self.clients or delta(c.id)["sent"] > 0):
                groups.setdefault((c.remote, c.mode), {"cids": set(), "rows": []})["cids"].add(c.id)
        mse_rows = [m for m in (self.mse.get(gen) or ()) if lo <= m[0] < hi]
        fb = sess.fb if sess else {}
        produced = len(recs)
        for (remote, mode), g in sorted(groups.items(), key=lambda kv: max(kv[1]["cids"])):
            cids = sorted(g["cids"])
            latest = known.get(cids[-1])
            grows = g["rows"]
            stage_vals: dict[str, list] = {s: [r[s] for r in grows if s in r] for s in STAGES}
            for r in grows:
                t_fb = fb.get(r["frame"])
                if t_fb is not None:
                    stage_vals["fb"].append(t_fb - r["t"])
                    if "total" in r:
                        stage_vals["expected_minus_fb"].append(r["t"] + r["total"] - t_fb)
            stage = {s: {"n": len(v), "p50": pct(v, 50), "p95": pct(v, 95), "p99": pct(v, 99),
                         "max": max(v), "min": min(v)} for s, v in stage_vals.items() if v}
            cnt = {k: 0 for k in COUNTERS}
            for cid in cids:
                for k, v in delta(cid).items():
                    cnt[k] += v
            presented = sum(1 for r in grows if r["presented"])
            overdrawn = sum(1 for r in grows if r["flags"] & FLAG_OVERDRAWN)
            never = sum(1 for r in grows if r["flags"] & FLAG_NEVER)
            gm = [m for m in mse_rows if m[2] == remote and m[3] == mode]
            events = sorted((e for cid in cids if cid in known for e in known[cid].events),
                            key=lambda e: e.get("t_server") or e.get("t") or 0)
            clk = latest.clock if latest else {}
            out["clients"].append({
                "id": cids[-1], "ids": cids, "connections": len(cids), "reconnects": len(cids) - 1,
                "live": any(cid in self.clients for cid in cids),
                "remote": remote, "role": "viewer", "mode": mode,
                "info": latest.info if latest else {}, "params": latest.params if latest else {},
                "clock": clk, "rtt_ms": num(clk.get("rtt")), "offset_ms": None,  # offsets are applied by the page now
                "sent": cnt["sent"], "server_dropped": cnt["dropped"], "join_skipped": cnt["join_skipped"],
                "produced": produced, "reported": len(grows), "presented": presented,
                "not_presented": len(grows) - presented, "overdrawn": overdrawn, "never_presented": never,
                "gap_before": sum(1 for r in grows if r["presented"] and r["flags"] & FLAG_GAP),
                "unreported": max(0, cnt["sent"] - len(grows)),
                # survivorship: share of the frames the source produced in the window that reached glass
                "presented_ratio": presented / produced if produced else None,
                "not_presented_rate": 1 - presented / produced if produced else None,
                "barcode_ok": cnt["barcode_ok"], "barcode_bad": cnt["barcode_bad"],
                "barcode_race": cnt["barcode_race"],
                "barcode_cost_ms_p95": pct(list(latest.barcode_cost), 95) if latest else None,
                "items_bad": cnt["items_bad"], "items_stale": cnt["items_stale"],
                "mse": self._mse_summary(gm),
                "stages": stage, "recent_events": events[-10:],
            })
        for c in self.clients.values():  # probes etc.: connection-level info only
            if c.role == "viewer":
                continue
            out["clients"].append({
                "id": c.id, "remote": c.remote, "role": c.role, "mode": c.mode, "info": c.info,
                "clock": c.clock, "rtt_ms": num(c.clock.get("rtt")), "offset_ms": None,
                "items_bad": c.items_bad, "items_stale": c.items_stale,
                "sent": 0, "server_dropped": 0, "reported": 0, "presented": 0, "stages": {},
                "barcode_ok": 0, "barcode_bad": 0, "recent_events": list(c.events)[-10:],
            })
        return out

    @staticmethod
    def _mse_summary(samples: list) -> Optional[dict]:
        if not samples:
            return None
        stalls = 0
        last: dict[int, float] = {}
        for _, cid, _, _, _, _, s in samples:  # 'stalls' is a per-player running count: sum the increases
            if s is not None:
                if cid in last and s > last[cid]:
                    stalls += s - last[cid]
                last[cid] = s
        ahead = [m[4] for m in samples if m[4] is not None]
        rate = [m[5] for m in samples if m[5] is not None]
        return {"n": len(samples), "ahead_p50": pct(ahead, 50), "ahead_p95": pct(ahead, 95),
                "rate_p50": pct(rate, 50), "stalls": stalls}

    def client_closed(self, c: Client) -> None:
        self.clients.pop(c.id, None)
        c.closed_at = clock()
        self.departed[c.id] = c
        cutoff = c.closed_at - DEPARTED_KEEP_S * 1000
        for cid in [cid for cid, d in self.departed.items() if d.closed_at < cutoff]:
            del self.departed[cid]
        while len(self.departed) > 200:
            del self.departed[next(iter(self.departed))]


# ---------------------------------------------------------------------------
async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    hub: Hub = request.app["hub"]
    # compress=False: Chrome offers permessage-deflate; zlib on ~1 MB keyframes would add tens of ms.
    ws = web.WebSocketResponse(compress=False, heartbeat=None, max_msg_size=8 << 20)
    await ws.prepare(request)
    transport = request.transport
    tune_socket(transport)
    c = Client(ws, request.remote or "?", transport)
    hub.clients[c.id] = c
    sender = asyncio.create_task(hub.sender(c))
    print(f"[client {c.id}] connected from {c.remote}", flush=True)
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            t_rx = clock()
            try:
                data = json.loads(msg.data)
                kind = data.get("type")
                if kind == "ping":
                    c.push_control(("pong", data.get("id"), data.get("c0"), t_rx))
                elif kind == "ack":
                    c.on_ack(data.get("fid"))
                elif kind == "hello":
                    c.role = data.get("role", "viewer")
                    c.mode = data.get("mode", "?")
                    c.info = data.get("info", {})
                    c.hello = True
                    print(f"[client {c.id}] hello role={c.role} mode={c.mode} info={json.dumps(c.info)[:600]}",
                          flush=True)
                    if c.role in ("viewer", "probe"):
                        hub._send_config(c)
                elif kind == "report":
                    hub.ingest(c, data)
                elif kind == "fb":
                    hub.ingest_fb(c, data)
                elif kind == "info":
                    c.info.update(data.get("info", {}))
                    print(f"[client {c.id}] info {json.dumps(data.get('info'))[:800]}", flush=True)
            except Exception as e:  # one malformed message must not drop the viewer's socket
                c.msg_errors += 1
                if c.msg_errors <= 5 or c.msg_errors % 100 == 0:
                    print(f"[client {c.id}] bad message #{c.msg_errors}: {type(e).__name__}: {e} "
                          f"{str(msg.data)[:200]}", flush=True)
    finally:
        hub.client_closed(c)
        sender.cancel()
        print(f"[client {c.id}] disconnected", flush=True)
    return ws


async def api_state(request: web.Request) -> web.Response:
    hub: Hub = request.app["hub"]
    seconds = float(request.query.get("seconds", 10))
    return web.json_response(hub.stats(seconds))


async def api_session(request: web.Request) -> web.Response:
    """Merge the body into the last requested config and restart the session (400 on a bad config)."""
    hub: Hub = request.app["hub"]
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("body must be a JSON object")
    except ValueError as e:
        return web.json_response({"ok": False, "error": f"bad request: {e}"}, status=400)
    try:
        sess = await hub.start_session(update=body)
    except ValueError as e:
        return web.json_response({"ok": False, "error": str(e), "cfg": hub.cfg}, status=400)
    except Exception as e:
        traceback.print_exc()
        return web.json_response({"ok": False, "error": f"{type(e).__name__}: {e}", "cfg": hub.cfg}, status=500)
    return web.json_response({"ok": True, "session": sess.gen, "cfg": sess.cfg})


async def api_mode(request: web.Request) -> web.Response:
    """Tell viewers to switch client mode (mse | webcodecs | wctrack) and other client params."""
    hub: Hub = request.app["hub"]
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("body must be a JSON object")
    except ValueError as e:
        return web.json_response({"ok": False, "error": f"bad request: {e}"}, status=400)
    for c in hub.clients.values():
        if c.role == "viewer":
            c.push_control(json.dumps({"type": "set-params", "params": body}))
    return web.json_response({"ok": True, "clients": len(hub.clients)})


async def index(request: web.Request) -> web.StreamResponse:
    raise web.HTTPFound("/client.html" + (f"?{request.query_string}" if request.query_string else ""))


@web.middleware
async def no_cache(request, handler):
    resp = await handler(request)
    resp.headers["Cache-Control"] = "no-store"
    return resp


async def snapshot_loop(hub: Hub) -> None:
    while True:
        hub.snaps.append(hub.snapshot())
        await asyncio.sleep(SNAP_PERIOD_S)


def fmt(v, spec: str = ".1f", none: str = "-") -> str:
    return none if v is None else format(v, spec)


async def console_loop(hub: Hub) -> None:
    while True:
        await asyncio.sleep(5)
        hub.clock_check()
        st = hub.stats(5)
        for c in st["clients"]:
            if c["role"] != "viewer":
                continue
            s = c["stages"]

            def f(k, p="p50"):
                v = s.get(k, {}).get(p)
                return "   -  " if v is None else f"{v:6.1f}"
            clk = c.get("clock") or {}
            print(f"[stats c{c['id']} {c['mode']:<9}] total p50 {f('total')} p95 {f('total','p95')} | "
                  f"enc {f('encode')} pipe {f('pipe')} queue {f('queue')} net {f('net')} client {f('client')} "
                  f"present {f('present')} | presented {c['presented']}/{c['produced']} "
                  f"({fmt(c['presented_ratio'], '.0%')}) overdrawn {c['overdrawn']} never {c['never_presented']} "
                  f"drop {c['server_dropped']} | rtt {fmt(num(clk.get('rtt')))} ppm {fmt(num(clk.get('ppm')), '+.0f')} "
                  f"| barcode ok/bad {c['barcode_ok']}/{c['barcode_bad']}"
                  + (f" | reconnects {c['reconnects']}" if c["reconnects"] else ""), flush=True)


async def wait_for_viewer(hub: Hub, timeout: float) -> bool:
    t_end = time.monotonic() + timeout
    while not hub.viewers():
        if time.monotonic() > t_end:
            return False
        await asyncio.sleep(0.5)
    return True


async def matrix_runner(hub: Hub, path: pathlib.Path) -> None:
    spec = json.loads(path.read_text())
    warmup, dwell = float(spec.get("warmup", 6)), float(spec.get("dwell", 30))
    viewer_wait = float(spec.get("viewer_wait", 60))  # per run, once the first viewer has been seen
    results: list[dict] = []
    print(f"[matrix] {len(spec['runs'])} runs, warmup {warmup}s dwell {dwell}s; waiting for a viewer...", flush=True)
    try:
        await wait_for_viewer(hub, math.inf)
        for i, run in enumerate(spec["runs"]):
            cfg = {**spec.get("base", {}), **run}
            client_params = cfg.pop("client", {})
            entry: dict = {"run": cfg, "client": client_params}
            print(f"[matrix] run {i + 1}/{len(spec['runs'])}: {cfg} client={client_params}", flush=True)
            try:
                validate_cfg(cfg)  # before touching the viewers or the running session
                if not await wait_for_viewer(hub, viewer_wait):
                    raise RunAborted(f"no viewer connected for {viewer_wait:.0f} s")
                if client_params:
                    for c in hub.clients.values():
                        if c.role == "viewer":
                            c.push_control(json.dumps({"type": "set-params", "params": client_params}))
                sess = await hub.start_session(cfg)
                await asyncio.sleep(warmup)
                t0, base = clock(), hub.snapshot()
                await asyncio.sleep(dwell)
                t1, top = clock(), hub.snapshot()
                # never-presented frames are reported 2 s after arrival: wait for them before reading the
                # window [t0, t1), and before the next session wipes the page's pending records
                await asyncio.sleep(REPORT_GRACE_S)
                st = hub.stats(since=t0, until=t1, base=base, top=top, sess=sess)
                entry["stats"] = st
                notes = []
                if hub.session is not sess:
                    notes.append("session replaced during the run")
                if st["encoder_exit"]:
                    notes.append(f"encoder: {st['encoder_exit'][:200]}")
                viewers = [c for c in st["clients"] if c["role"] == "viewer"]
                if not any(c["reported"] for c in viewers):
                    notes.append("no viewer data in the dwell window")
                for v in viewers:  # a page that ignored set-params (stale cached client.js...) mislabels the run
                    got = {**(v.get("params") or {}), "mode": v["mode"]}
                    off = {k: got[k] for k in client_params if got.get(k) is not None and got[k] != client_params[k]}
                    if off:
                        notes.append(f"viewer {v['id']} reports {off}, not as requested")
                if notes:
                    entry["warning"] = "; ".join(notes)
                    print(f"[matrix] run {i + 1}: {entry['warning']}", flush=True)
            except asyncio.CancelledError:
                raise
            except (ConfigError, RunAborted) as e:
                entry["error"] = f"{'bad config' if isinstance(e, ConfigError) else 'aborted'}: {e}"
                print(f"[matrix] run {i + 1} SKIPPED: {entry['error']}", flush=True)
            except Exception as e:  # one bad run must not end the matrix
                entry["error"] = f"{type(e).__name__}: {e}"
                print(f"[matrix] run {i + 1} FAILED: {entry['error']}\n{traceback.format_exc()}", flush=True)
            results.append(entry)
            (hub.log_path / "matrix.json").write_text(json.dumps(results, indent=1))
    finally:
        (hub.log_path / "matrix.json").write_text(json.dumps(results, indent=1))
        write_matrix_markdown(results, hub.log_path / "matrix.md")
        print(f"[matrix] done ({len(results)}/{len(spec['runs'])} runs) -> {hub.log_path / 'matrix.json'}", flush=True)


def _log_task_exit(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        e = task.exception()
        print(f"[{task.get_name()}] crashed: {''.join(traceback.format_exception(e))}", flush=True)


# client params worth showing per mode in matrix.md (MSE latency is set by its buffer target)
MODE_PARAMS = {"mse": ("target",), "webcodecs": ("draw", "hw"), "wctrack": ("hw",)}


def _client_label(mode: Optional[str], params: dict, requested: dict) -> str:
    mode = mode or params.get("mode") or requested.get("mode") or "?"
    keys = list(MODE_PARAMS.get(mode, ())) + [k for k in requested if k != "mode" and k not in MODE_PARAMS.get(mode, ())]
    parts = []
    for k in keys:
        v = params.get(k, requested.get(k))
        if v is not None:
            parts.append(f"{k}={v}")
    return " ".join([mode, *parts])


def _cell(s: str) -> str:
    """One table cell: encoder-exit notes carry ffmpeg's multi-line stderr."""
    return " ".join(str(s).split()).replace("|", "/")


def write_matrix_markdown(results: list[dict], out: pathlib.Path) -> None:
    head = ["run", "codec", "size@fps", "bitrate", "client", "total p50", "p95", "p99", "encode", "net", "client",
            "present", "presented/produced", "not presented (overdrawn/never)", "drops", "mse ahead/rate/stalls",
            "conns", "sync rtt/ppm", "notes"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for i, r in enumerate(results, 1):
        cfg, req = r.get("run", {}), r.get("client") or {}
        lead = [str(i), str(cfg.get("codec", "?")), f"{cfg.get('size', '?')}@{cfg.get('fps', '?')}",
                str(cfg.get("bitrate", ""))]
        viewers = [c for c in (r.get("stats") or {}).get("clients", []) if c["role"] == "viewer"]
        if "error" in r or not viewers:
            note = r.get("error") or r.get("warning") or "no viewer"
            lines.append("| " + " | ".join(lead + [_client_label(None, {}, req)] + [""] * (len(head) - 6)
                                            + [f"**{_cell(note)}**"]) + " |")
            continue
        for c in viewers:
            s = c["stages"]
            g = lambda k, p="p50": "" if k not in s else f"{s[k][p]:.1f}"  # noqa: E731
            m, clk = c.get("mse"), c.get("clock") or {}
            notes = [r["warning"]] if r.get("warning") else []
            if c.get("join_skipped"):
                notes.append(f"join-skipped {c['join_skipped']}")
            if c.get("items_bad"):
                notes.append(f"bad items {c['items_bad']}")
            lines.append("| " + " | ".join(lead + [
                _client_label(c["mode"], c.get("params") or {}, req),
                g("total"), g("total", "p95"), g("total", "p99"), g("encode"), g("net"), g("client"), g("present"),
                f"{c['presented']}/{c['produced']} ({fmt(c['presented_ratio'], '.0%')})",
                f"{c['not_presented']} ({c['overdrawn']}/{c['never_presented']})",
                str(c["server_dropped"]),
                "" if not m else f"{fmt(m['ahead_p50'])}/{fmt(m['rate_p50'], '.2f')}/{fmt(m['stalls'], '.0f')}",
                str(c["connections"]),
                f"{fmt(num(clk.get('rtt')))}/{fmt(num(clk.get('ppm')), '+.0f')}",
                _cell("; ".join(notes))]) + " |")
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--codec", default="h264", choices=["h264", "hevc", "av1"])
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--bitrate", default="8M")
    ap.add_argument("--gop", type=float, default=1.0, help="seconds between IDR frames")
    ap.add_argument("--gpu", default="0", help="CUDA device index for NVENC (0=RTX 4060, 1=P4000 here)")
    ap.add_argument("--complexity", type=int, default=2, choices=[0, 1, 2, 3])
    ap.add_argument("--source", default="pattern", choices=["pattern", "cuda"],
                    help="pattern = CPU test pattern; cuda = GPU-rendered fractal (CuPy)")
    ap.add_argument("--render-gpu", type=int, default=0, help="CUDA device for --source cuda")
    ap.add_argument("--profile", default=None, help="e.g. high / main (h264)")
    ap.add_argument("--vbv-frames", type=float, default=None, help="VBV buffer in frames (default: NVENC ull default)")
    ap.add_argument("--no-reorder-fix", action="store_true", help="do NOT rewrite the H.264 SPS (A/B test)")
    ap.add_argument("--slices", type=int, default=None, help="encoder slices (helps only software decode)")
    ap.add_argument("--mode", default="mse", help="default client mode if the URL does not set one")
    ap.add_argument("--matrix", default=None, help="JSON file describing a sequence of runs")
    args = ap.parse_args()

    hub = Hub(args)
    app = web.Application(middlewares=[no_cache])
    app["hub"] = hub
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/state", api_state)
    app.router.add_post("/api/session", api_session)
    app.router.add_post("/api/client", api_mode)
    app.router.add_static("/", HERE / "static", show_index=False)

    async def on_startup(app):
        hub.loop = asyncio.get_running_loop()
        cfg = {"codec": args.codec, "size": args.size, "fps": args.fps, "bitrate": args.bitrate,
               "gop": args.gop, "gpu": args.gpu, "complexity": args.complexity, "profile": args.profile,
               "source": args.source, "render_gpu": args.render_gpu, "vbv_frames": args.vbv_frames,
               "no_reorder_fix": args.no_reorder_fix, "slices": args.slices}
        tasks = {"console": console_loop(hub), "snapshots": snapshot_loop(hub)}
        if args.matrix:
            tasks["matrix"] = matrix_runner(hub, pathlib.Path(args.matrix))
        else:
            await hub.start_session(cfg)
        for name, coro in tasks.items():
            app[name] = asyncio.create_task(coro, name=name)
            app[name].add_done_callback(_log_task_exit)
        print(f"[server] epoch {EPOCH}, logging to {hub.log_path}", flush=True)

    async def on_cleanup(app):
        for name in ("matrix", "console", "snapshots"):
            if name in app:
                app[name].cancel()
        if hub.session:
            hub.session.stop()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    web.run_app(app, host=args.host, port=args.port, print=lambda s: print(s, flush=True))


if __name__ == "__main__":
    main()
