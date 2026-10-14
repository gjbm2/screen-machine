"""Media WebSocket fan-out for one session (LIVE_V1 §1.4), ported from the lab's Hub/Client.

Server -> client: one ``config`` text message, ``[0x01][init]``, then ``[0x02][key][frame_id u32 BE][moof+mdat]``
from the next keyframe on, and ``{"type": "eos"}`` when the session ends.  A client that falls 8 frames
behind (frames queued here plus one stuck in the transport) is dropped to the next keyframe.  Small kernel
send buffers keep the backlog in the asyncio transport, where it is visible (lab finding: otherwise the
kernel buffers megabytes of stale frames that drop-to-key cannot see).
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import math
import socket
import time
from typing import Optional

from aiohttp import web

log = logging.getLogger("stream_service.media")

MAX_QUEUE = 8
WRITE_HIGH = 16 << 10
SNDBUF = 256 << 10
NOTSENT_LOWAT = 64 << 10
TCP_NOTSENT_LOWAT = getattr(socket, "TCP_NOTSENT_LOWAT", 25)
STATS_KEYS = ("presented", "dropped", "buffered_ms", "edge_jumps", "stalls")


def _finite_number(v: object) -> Optional[float]:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def tune_socket(transport: Optional[asyncio.Transport]) -> None:
    sock = transport.get_extra_info("socket") if transport is not None else None
    if sock is None:
        return
    for level, opt, val in ((socket.SOL_SOCKET, socket.SO_SNDBUF, SNDBUF),
                            (socket.IPPROTO_TCP, TCP_NOTSENT_LOWAT, NOTSENT_LOWAT)):
        try:
            sock.setsockopt(level, opt, val)
        except OSError:
            pass


class MediaClient:
    _ids = 0

    def __init__(self, ws: web.WebSocketResponse, remote: str, transport: Optional[asyncio.Transport]):
        MediaClient._ids += 1
        self.id = MediaClient._ids
        self.ws = ws
        self.remote = remote
        self.transport = transport
        self.control: collections.deque = collections.deque()
        self.media: collections.deque = collections.deque()
        self.wake = asyncio.Event()
        self.needs_key = True
        self.streaming = False
        self.announced = False
        self.sent = 0
        self.dropped = 0          # frames lost to drop-to-key once streaming
        self.join_skipped = 0     # frames skipped waiting for the first keyframe
        self.connected_at = time.time()
        self.stats: Optional[dict] = None   # last {"type": "stats", ...} from the page

    def transport_backlog(self) -> int:
        try:
            return self.transport.get_write_buffer_size() if self.transport is not None else 0
        except Exception:
            return 0

    def push_control(self, msg) -> None:
        self.control.append(msg)
        self.wake.set()

    def describe(self) -> dict:
        return {"id": self.id, "remote": self.remote, "sent": self.sent, "dropped": self.dropped,
                "join_skipped": self.join_skipped, "stats": self.stats}


class MediaHub:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.clients: dict[int, MediaClient] = {}
        self.config_msg: Optional[str] = None
        self.init_msg: Optional[bytes] = None
        self.ended = False

    # -- producer side (loop thread) ---------------------------------------------
    def set_config(self, config_msg: dict, init_msg: bytes) -> None:
        self.config_msg = json.dumps({**config_msg, "session": self.session_id})
        self.init_msg = init_msg
        for c in self.clients.values():
            self._announce(c)

    def _announce(self, c: MediaClient) -> None:
        if self.config_msg is None or c.announced:
            return
        c.announced = True
        c.push_control(self.config_msg)
        c.push_control(self.init_msg)

    def on_frame(self, frame_id: int, key: bool, msg: bytes) -> None:
        for c in self.clients.values():
            if not c.announced:
                continue
            if c.needs_key and not key:
                if c.streaming:
                    c.dropped += 1
                else:
                    c.join_skipped += 1
                continue
            backlog = len(c.media) + (c.transport_backlog() > WRITE_HIGH)
            if backlog >= MAX_QUEUE:
                c.dropped += len(c.media)
                c.media.clear()
                if not key:  # wait for the next keyframe; an arriving keyframe restarts the stream at once
                    c.needs_key = True
                    c.dropped += 1
                    continue
            c.needs_key = False
            c.streaming = True
            c.media.append(msg)
            c.wake.set()

    def end(self) -> None:
        """Send eos to everyone; the per-client senders then close their sockets."""
        if self.ended:
            return
        self.ended = True
        for c in self.clients.values():
            c.media.clear()
            c.push_control(json.dumps({"type": "eos"}))
            c.push_control(None)  # sentinel: close after eos

    # -- per-client (loop thread) ------------------------------------------------
    async def serve(self, request: web.Request) -> web.WebSocketResponse:
        # compress=False: zlib on ~1 MB keyframes would add tens of ms (lab)
        ws = web.WebSocketResponse(compress=False, heartbeat=None, max_msg_size=1 << 20)
        await ws.prepare(request)
        tune_socket(request.transport)
        c = MediaClient(ws, request.remote or "?", request.transport)
        self.clients[c.id] = c
        log.info("[%s] media client %d connected from %s", self.session_id, c.id, c.remote)
        if self.ended:
            c.push_control(json.dumps({"type": "eos"}))
            c.push_control(None)
        else:
            self._announce(c)
        sender = asyncio.create_task(self._sender(c))
        try:
            async for msg in ws:
                if msg.type.name != "TEXT":
                    continue
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("type") == "stats":
                    # numbers only: these are echoed in every session summary
                    c.stats = {k: _finite_number(data.get(k)) for k in STATS_KEYS}
        finally:
            self.clients.pop(c.id, None)
            sender.cancel()
            log.info("[%s] media client %d disconnected (sent %d, dropped %d)", self.session_id, c.id,
                     c.sent, c.dropped)
        return ws

    async def _sender(self, c: MediaClient) -> None:
        ws = c.ws
        try:
            while not ws.closed:
                await c.wake.wait()
                c.wake.clear()
                while (c.control or c.media) and not ws.closed:
                    if c.control:
                        msg = c.control.popleft()
                        if msg is None:
                            await ws.close(code=1000, message=b"eos")
                            return
                        if isinstance(msg, bytes):
                            await ws.send_bytes(msg)
                        else:
                            await ws.send_str(msg)
                        continue
                    # a frame handed to the transport can no longer be dropped: keep it queued (visible to
                    # on_frame's MAX_QUEUE check) until the transport has drained
                    if c.transport_backlog() > WRITE_HIGH:
                        await asyncio.sleep(0.003)  # aiohttp has no public "drained" event
                        continue
                    await ws.send_bytes(c.media.popleft())
                    c.sent += 1
        except (ConnectionError, asyncio.CancelledError):
            pass
        except Exception:
            # e.g. a RuntimeError from a closing transport: close the socket so the page sees it end at once
            # instead of a silent, open socket (which it would only notice as a 2 s stall)
            log.exception("[%s] media client %d: sender failed", self.session_id, c.id)
            try:
                await ws.close(code=1011, message=b"sender error")
            except Exception:
                pass

    def summary(self) -> list[dict]:
        return [c.describe() for c in self.clients.values()]
