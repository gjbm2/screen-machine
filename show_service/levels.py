"""The live input meter: ``WS /v1/levels`` (owner, 16 Oct: a moving level, near real time; built by 05).

The receive path only stores: :meth:`push` (show-rx, per audio packet of the active stream) folds the packet's peak
into a slot. A frame goes out as soon as 1/MAX_HZ has passed since the last one (so at most ~60 a second, one per
display refresh, as c8 asked to spare room_service's pass-through), carrying the loudest packet since the last frame:

    {"s": "pc22-mic", "c": 15566, "pk": -23.4, "n": 6, "t": 8063918580729833, "a": 1234.567891}

``pk``: the peak in dBFS over the ``n`` packets folded in (-120 floor). ``t``: the newest packet's first-sample
capture time on the SENDER's clock in ns (the heartbeats' capture map; null until it has a fit), so a client on the
sender's own clock (PC22: QPC = Python's perf_counter_ns) measures capture -> client exactly. ``a``: show_service's
arrival time of that packet (time.monotonic). Pages reach it through room_service's pass-through at /show/v1/levels.
Nothing is sent while no client is connected. A client may send ``{"ping": x}`` (any number of its own clock); the
reply ``{"pong": x, "srv": <time.monotonic()>}`` lets it align clocks and show how far behind the room each sample
reaches it (``a`` is on the same clock as ``srv``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
import time
from typing import Any, Optional

from aiohttp import WSMsgType, web

log = logging.getLogger("show_service.levels")

PATH = "/v1/levels"
MAX_HZ = 60.0
QUEUE_MAX = 64
FLOOR_DBFS = -120.0


class LevelFeed:
    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._clients: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._slot: Optional[list] = None                # [stream, counter, peak, packets, t_cap_ns, arrival]
        self._last_flush = 0.0
        self._scheduled = False
        self.sent = 0
        self.dropped = 0

    # -- show-rx: store only ----------------------------------------------------------------------------------------
    def push(self, stream: str, counter: int, peak: float, t_cap_ns: Optional[float], arrival_s: float) -> None:
        if not self._clients or self._loop is None:
            return
        with self._lock:
            s = self._slot
            if s is None or s[0] != stream:
                self._slot = [stream, counter, peak, 1, t_cap_ns, arrival_s]
            else:
                s[1], s[2], s[3], s[4], s[5] = counter, max(s[2], peak), s[3] + 1, t_cap_ns, arrival_s
            if self._scheduled:
                return
            self._scheduled = True
        wait = self._last_flush + 1.0 / MAX_HZ - time.monotonic()
        try:
            if wait <= 0:
                self._loop.call_soon_threadsafe(self._flush)
            else:
                self._loop.call_soon_threadsafe(self._loop.call_later, wait, self._flush)
        except RuntimeError:                              # the loop closed (shutting down)
            pass

    # -- bus loop ---------------------------------------------------------------------------------------------------
    def _flush(self) -> None:
        with self._lock:
            s, self._slot, self._scheduled = self._slot, None, False
        self._last_flush = time.monotonic()
        if s is None:
            return
        stream, counter, peak, n, t, a = s
        frame = json.dumps({"s": stream, "c": counter, "n": n,
                            "pk": round(20.0 * math.log10(peak), 1) if peak > 1e-6 else FLOOR_DBFS,
                            "t": int(t) if t is not None and math.isfinite(t) else None, "a": round(a, 6)},
                           separators=(",", ":"))
        for q in self._clients:
            if q.full():
                self.dropped += 1
            else:
                q.put_nowait(frame)

    def routes(self, app: web.Application) -> None:
        app.router.add_get(PATH, self._ws)

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        self._loop = asyncio.get_running_loop()
        ws = web.WebSocketResponse(heartbeat=10.0)
        await ws.prepare(request)
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._clients.add(q)

        async def reader() -> None:                       # pings (clock alignment); notice the close
            async for msg in ws:
                if msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
                if msg.type == WSMsgType.TEXT:
                    try:
                        x = json.loads(msg.data).get("ping")
                    except (ValueError, AttributeError):
                        continue
                    if isinstance(x, (int, float)) and not q.full():
                        q.put_nowait(json.dumps({"pong": x, "srv": time.monotonic()}))

        rd = asyncio.ensure_future(reader())
        try:
            while not ws.closed and not rd.done():
                get = asyncio.ensure_future(q.get())
                done, _ = await asyncio.wait({get, rd}, return_when=asyncio.FIRST_COMPLETED)
                if get not in done:
                    get.cancel()
                    break
                await ws.send_str(get.result())
                self.sent += 1
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._clients.discard(q)
            rd.cancel()
            await ws.close()
        return ws

    def snapshot(self) -> dict[str, Any]:
        return {"clients": len(self._clients), "sent": self.sent, "dropped": self.dropped}
