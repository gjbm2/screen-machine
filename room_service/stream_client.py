"""stream_service client: REST (LIVE_V1 §1.2) and the events WebSocket (§1.3).

* No auth (owner, 14 Oct): a token in `stream_service.token_file` is still sent if the file exists
  (an older renderer wants it), and nothing is sent without one.
* `reachable` means the events socket is up: the handshakes need its events.
* The events URL carries `?token=`; it is never logged (describe_exc strips queries).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, Awaitable, Callable

import aiohttp

from .util import REDACT, describe_exc, read_token, spawn

log = logging.getLogger("room_service.stream")


class StreamError(Exception):
    def __init__(self, code: str, detail: str = "", status: int | None = None) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.status = status


class StreamUnreachable(StreamError):
    pass


EventCb = Callable[[dict], None]
ConnCb = Callable[[bool], Awaitable[None] | None]


class StreamClient:
    def __init__(self, url: str, token_file, http: aiohttp.ClientSession, *,
                 http_timeout_s: float = 10.0) -> None:
        self.url = url.rstrip("/")
        self.token_file = token_file
        self.http = http
        self.http_timeout_s = http_timeout_s
        self._token: str | None = None
        self._on_event: list[EventCb] = []
        self._on_conn: list[ConnCb] = []
        self.events_connected = False
        self.down_at: float | None = None      # monotonic time the events socket last went down (a renderer restart)
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None
        self._catalog: dict | None = None
        self._catalog_at = 0.0
        self.connects = 0

    # -- wiring ------------------------------------------------------------
    def on_event(self, cb: EventCb) -> None:
        self._on_event.append(cb)

    def on_connection(self, cb: ConnCb) -> None:
        self._on_conn.append(cb)

    @property
    def reachable(self) -> bool:
        return self.events_connected

    def went_away_within(self, s: float) -> bool:
        """The renderer's events socket dropped in the last `s` seconds (or is down now): a restart for a deploy."""
        return not self.events_connected or (self.down_at is not None and time.monotonic() - self.down_at <= s)

    def _get_token(self, refresh: bool = False) -> str | None:
        if self._token is None or refresh:
            tok = read_token(self.token_file)
            if tok:
                REDACT.add(tok)
            self._token = tok
        return self._token

    # -- REST --------------------------------------------------------------
    async def _req(self, method: str, path: str, body: Any = None, *,
                   timeout: float | None = None, ok: tuple[int, ...] = (200, 201, 202, 204)) -> Any:
        for attempt in (0, 1):
            tok = self._get_token(refresh=attempt == 1)
            headers = {"Authorization": f"Bearer {tok}"} if tok else {}
            try:
                async with self.http.request(
                        method, self.url + path, json=body, headers=headers,
                        timeout=aiohttp.ClientTimeout(total=timeout or self.http_timeout_s)) as r:
                    text = await r.text()
                    if r.status == 401 and attempt == 0 and tok:
                        continue           # token may have been rotated: re-read once
                    if r.status not in ok:
                        try:
                            err = json.loads(text)
                            code, detail = str(err.get("error", r.status)), str(err.get("detail", ""))
                        except ValueError:
                            code, detail = f"http_{r.status}", text[:200]
                        raise StreamError(code, REDACT(detail), r.status)
                    if not text:
                        return None
                    try:
                        return json.loads(text)
                    except ValueError:
                        return None
            except StreamError:
                raise
            except asyncio.TimeoutError:       # (also aiohttp's ServerTimeoutError): up but not answering in time
                t = timeout or self.http_timeout_s
                self.last_error = f"timed out after {t:g} s"
                raise StreamUnreachable("timeout", self.last_error) from None
            except (aiohttp.ClientError, OSError) as e:
                self.last_error = describe_exc(e)
                raise StreamUnreachable("unreachable", self.last_error) from None
        raise StreamError("unauthorized", "stream_service rejected the token", 401)

    async def catalog(self, max_age_s: float = 30.0) -> dict:
        if self._catalog is not None and time.monotonic() - self._catalog_at < max_age_s:
            return self._catalog
        cat = await self._req("GET", "/api/catalog")
        self._catalog, self._catalog_at = cat or {}, time.monotonic()
        return self._catalog

    def cached_catalog(self) -> dict | None:
        return self._catalog

    async def list_sessions(self) -> list[dict]:
        res = await self._req("GET", "/api/sessions")
        if isinstance(res, dict):
            res = res.get("sessions", [])
        return [s for s in (res or []) if isinstance(s, dict)]

    async def get_session(self, sid: str) -> dict | None:
        try:
            return await self._req("GET", f"/api/sessions/{sid}")
        except StreamError as e:
            if e.status == 404:
                return None
            raise

    async def create_session(self, body: dict, timeout: float | None = None) -> dict:
        return await self._req("POST", "/api/sessions", body, timeout=timeout or max(self.http_timeout_s, 30.0))

    async def warm(self, screen: str, gpu: int | None = None, timeout: float = 2.0) -> Any:
        """POST /api/workers/warm: start this screen's render worker ahead of its next session (a hint)."""
        body = {"screen": screen, **({"gpu": gpu} if gpu is not None else {})}
        return await self._req("POST", "/api/workers/warm", body, timeout=timeout)

    async def go(self, sid: str, timeout: float | None = None) -> None:
        await self._req("POST", f"/api/sessions/{sid}/go", {}, timeout=timeout)

    async def exit(self, sid: str, target: dict, transition: Any = None, timeout: float | None = None) -> None:
        body: dict[str, Any] = {"target": target}
        if transition is not None:
            body["transition"] = transition
        await self._req("POST", f"/api/sessions/{sid}/exit", body,
                        timeout=timeout or max(self.http_timeout_s, 30.0))

    async def put_fx(self, sid: str, body: dict) -> Any:
        return await self._req("PUT", f"/api/sessions/{sid}/fx", body)

    async def cue(self, sid: str, body: dict) -> Any:
        """POST /api/sessions/{sid}/cue: e.g. {to: "auto", via?} = the director's own weighted next pick."""
        return await self._req("POST", f"/api/sessions/{sid}/cue", body)

    async def patch(self, sid: str, body: dict) -> Any:
        return await self._req("PATCH", f"/api/sessions/{sid}", body)

    async def renew(self, sid: str) -> None:
        await self._req("POST", f"/api/sessions/{sid}/renew", {})

    async def delete(self, sid: str) -> bool:
        """DELETE a session. True if it existed. Never raises for 404."""
        try:
            await self._req("DELETE", f"/api/sessions/{sid}", ok=(200, 202, 204))
            return True
        except StreamError as e:
            if e.status == 404:
                return False
            raise

    async def state(self) -> dict:
        return await self._req("GET", "/api/state") or {}

    # -- events WS ---------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            self._task = spawn(self._events_loop(), "stream-events")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _set_connected(self, up: bool) -> None:
        if up == self.events_connected:
            return
        self.events_connected = up
        if not up:
            self.down_at = time.monotonic()
        for cb in self._on_conn:
            try:
                res = cb(up)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                log.exception("stream connection callback failed")

    async def _events_loop(self) -> None:
        n = 0
        ws_base = "ws" + self.url[4:] if self.url.startswith("http") else self.url
        while True:
            tok = self._get_token(refresh=True)
            try:
                async with self.http.ws_connect(f"{ws_base}/api/events", params={"token": tok} if tok else None,
                                                heartbeat=10.0, max_msg_size=4 * 1024 * 1024) as ws:
                    n = 0
                    self.connects += 1
                    log.info("stream_service events connected (%s)", self.url)
                    await self._set_connected(True)
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                evt = json.loads(msg.data)
                            except ValueError:
                                continue
                            if isinstance(evt, dict):
                                for cb in self._on_event:
                                    try:
                                        cb(evt)
                                    except Exception:
                                        log.exception("stream event handler failed")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = describe_exc(e)
                if n == 0:
                    log.warning("stream_service events: %s", self.last_error)
            await self._set_connected(False)
            delay = random.uniform(0, min(5.0, 0.5 * 2 ** n))     # local service: recover quickly
            n = min(n + 1, 6)
            await asyncio.sleep(max(0.2, delay))
