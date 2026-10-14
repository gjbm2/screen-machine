"""Events WebSocket fan-out (LIVE_V1 §1.3).  The server only sends; client messages are ignored."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

from aiohttp import web

log = logging.getLogger("stream_service.events")

MAX_BACKLOG = 2000


class EventHub:
    def __init__(self) -> None:
        self.queues: set[asyncio.Queue] = set()
        self.history: list[dict] = []   # the last 200 events (kept for debugging from a REPL)

    def emit(self, event: dict) -> None:
        log.info("event %s", json.dumps(event, separators=(",", ":")))
        self.history.append(event)
        del self.history[:-200]
        data = json.dumps(event)
        for q in list(self.queues):
            if q.qsize() >= MAX_BACKLOG:  # a stuck consumer: drop it rather than grow without bound
                self.queues.discard(q)
                q.put_nowait(None)
                continue
            q.put_nowait(data)

    def close(self) -> None:
        """Service shutdown: close every events socket (otherwise shutdown waits for them to time out)."""
        for q in list(self.queues):
            q.put_nowait(None)
        self.queues.clear()

    async def serve(self, request: web.Request, snapshot: Callable[[], list[dict]]) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=None)
        # Take the snapshot and subscribe in the same synchronous step, *before* the handshake awaits:
        # otherwise an event emitted while prepare() runs (e.g. session.ready for a session that was just
        # created) would be in neither the snapshot nor the queue, and a (re)connecting room_service would
        # never see it.
        q: asyncio.Queue = asyncio.Queue()
        for ev in snapshot():  # current state of every session, so a (re)connecting consumer can adopt them
            q.put_nowait(json.dumps(ev))
        self.queues.add(q)
        try:
            await ws.prepare(request)
        except BaseException:
            self.queues.discard(q)
            raise

        async def sender() -> None:
            try:
                while True:
                    data = await q.get()
                    if data is None:
                        await ws.close()
                        return
                    await ws.send_str(data)
            except (ConnectionError, asyncio.CancelledError):
                pass
            except Exception:  # never leave a dead sender behind a socket that looks open
                log.exception("events sender failed")
                await ws.close()

        task = asyncio.create_task(sender())
        try:
            async for _ in ws:  # ignore anything the client sends
                pass
        finally:
            self.queues.discard(q)
            task.cancel()
        return ws
