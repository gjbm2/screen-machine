"""Room events WebSocket hub (LIVE_V1 §3, `WS /api/v1/events`).

* `status` (the full status) goes to a client on connect and to everyone on every
  change, coalesced to at most 4 per second.
* All other events go out immediately, in order.
* Each client has its own bounded queue and writer, so one slow browser can't
  stall the service; a client that falls 500 messages behind is dropped.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from aiohttp import web

from .util import REDACT, spawn

log = logging.getLogger("room_service.events")

STATUS_MIN_INTERVAL_S = 0.25   # 4 per second
MAX_CLIENTS = 64               # the events WS is open on the LAN (LIVE_V1 §3): bound the fan-out


class _Client:
    def __init__(self, ws: web.WebSocketResponse) -> None:
        self.ws = ws
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=500)
        self.task: asyncio.Task | None = None

    def offer(self, text: str) -> bool:
        try:
            self.queue.put_nowait(text)
            return True
        except asyncio.QueueFull:
            return False

    async def writer(self) -> None:
        while True:
            text = await self.queue.get()
            if text is None or self.ws.closed:
                return
            try:
                await self.ws.send_str(text)
            except (ConnectionError, RuntimeError):
                return


class EventHub:
    def __init__(self, status_fn: Callable[[], dict]) -> None:
        self._status_fn = status_fn
        self._clients: set[_Client] = set()
        self._dirty = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.status_sent = 0          # for tests / diagnostics

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            self._task = spawn(self._flusher(), "events-status-flusher")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        for c in list(self._clients):
            c.offer(None)
            await c.ws.close()
        self._clients.clear()

    # -- publishing --------------------------------------------------------
    def mark_dirty(self) -> None:
        self._dirty.set()

    def emit(self, type_: str, data: Any) -> None:
        if not self._clients:
            return
        text = REDACT(json.dumps({"type": type_, "data": data}, default=str))
        for c in list(self._clients):
            if not c.offer(text):
                log.warning("events client fell behind; dropping it")
                self._drop(c)

    def log_event(self, level: str, msg: str) -> None:
        self.emit("log", {"level": level, "msg": REDACT(msg)})

    def _drop(self, c: _Client) -> None:
        self._clients.discard(c)
        if c.task:
            c.task.cancel()
        spawn(c.ws.close(), "events-drop")

    async def _flusher(self) -> None:
        while True:
            await self._dirty.wait()
            self._dirty.clear()
            if self._clients:
                try:
                    self.emit("status", self._status_fn())
                    self.status_sent += 1
                except Exception:
                    log.exception("building status failed")
            # Rate limit: changes made during the sleep coalesce into one status.
            await asyncio.sleep(STATUS_MIN_INTERVAL_S)

    # -- the WS endpoint ---------------------------------------------------
    async def handle(self, request: web.Request) -> web.StreamResponse:
        if len(self._clients) >= MAX_CLIENTS:
            return web.json_response({"error": "too_many_clients", "detail": f"at most {MAX_CLIENTS}"},
                                     status=503)
        ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=64 * 1024)
        await ws.prepare(request)
        c = _Client(ws)
        c.offer(REDACT(json.dumps({"type": "status", "data": self._status_fn()}, default=str)))
        self._clients.add(c)
        c.task = spawn(c.writer(), "events-writer")
        try:
            async for _msg in ws:   # inbound messages are ignored
                pass
        finally:
            self._clients.discard(c)
            c.offer(None)
            if c.task:
                c.task.cancel()
        return ws
