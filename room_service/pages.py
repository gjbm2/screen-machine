"""Display page control sockets: `WS /v1/screen/<id>?role=kiosk|viewer|lab` (LIVE_V1 §2).

* Envelope `{"v": 1, "type", "data"}`; unknown types and fields are ignored.
* Per-screen registry, pub/sub, any number of pages. `kiosk` and `lab` clients are
  *glass* (what is on the TV). One glass client per screen is the one the handshakes
  drive (screens.py: its own device, the TV, keeps it); a second glass client is warned
  about. Every other page (standby glass, `viewer`) is a *subscriber*: it is shown what
  the screen shows (the running live session, else art) and drives nothing; its acks
  only let the room offer it the session again after it cut to art by itself (screens.py).
* A page's *device* is its browser's User-Agent with the version numbers taken out: the
  remote address can't tell pages apart here (every socket arrives through the host's
  port proxy), and a browser update must not make the TV a stranger.
* Liveness: `hb` when a socket has sent nothing for 2 s; a socket with no
  inbound frame for 6 s is closed (the page reconnects).
* Message-level heartbeat only: aiohttp's ping heartbeat is off.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import itertools
import json
import logging
import re
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from .util import REDACT, read_json, spawn, utc_iso

if TYPE_CHECKING:
    from .config import RoomConfig

log = logging.getLogger("room_service.pages")

ROLES = ("kiosk", "viewer", "lab")
GLASS_ROLES = ("kiosk", "lab")
# Inbound types a page may send (DCL §2.3 "scoped by role").
PAGE_TYPES = ("hello", "ack", "kiosk.state", "hb")
MAX_MSGS_PER_S = 50              # per client; there is no limit on the number of pages per screen (owner, 15 Oct)

CLOSE_UNKNOWN_SCREEN = 4404
CLOSE_BAD_VERSION = 4400
CLOSE_BEHIND = 4008       # the page reconnects at once on 4008 (fresh welcome): only for "behind"
CLOSE_RATE = 4029         # inbound flood: the page reconnects with its normal jittered backoff
CLOSE_DEAD = 4001
CLOSE_NO_HELLO = 4002
CLOSE_BAD_KEY = 4401      # kiosk/lab without the screen's ctlkey (LIVE_V1 §3 machine channels)
CLOSE_REPLACED = 4009     # replaced by a newer socket of the same page load (don't reconnect it)
HB_TEXT = json.dumps({"v": 1, "type": "hb", "data": {}})

_ids = itertools.count(1)
_VERSION = re.compile(r"\d+(?:[._]\d+)*")


def device_of(user_agent: str | None) -> str:
    """A short id for the browser a page runs in (not a secret, not an identity): its User-Agent without version
    numbers, hashed. "" when it sent none."""
    ua = _VERSION.sub("#", (user_agent or "").strip())
    return hashlib.sha1(ua.encode()).hexdigest()[:12] if ua else ""


class PageGone(Exception):
    pass


class PageClient:
    """One page socket. Outbound messages go through a bounded outbox (LIVE_V1 §2.2a.1):

    * `mask.set` and `policy` are latest-value only: a newer one replaces an unsent older
      one (policy messages always carry the full Policy, so nothing is lost).
    * Everything else (`welcome`, `freeze`, `unfreeze`, `live.*`) is never dropped and keeps
      its order.
    * A client with more than `behind_bytes` unsent, or whose oldest unsent message is older
      than `behind_s`, is disconnected (close 4008: the page reconnects at once and gets a
      fresh, authoritative `welcome`).
    """

    LATEST_ONLY = ("mask.set", "policy")

    def __init__(self, screen: str, role: str, ws: web.WebSocketResponse, remote: str | None, *,
                 behind_bytes: int = 256 * 1024, behind_s: float = 5.0, user_agent: str | None = None) -> None:
        self.cid = f"c{next(_ids)}"
        self.screen = screen
        self.role = role
        self.ws = ws
        self.remote = remote
        self.device = device_of(user_agent)
        self.connected_at = time.monotonic()
        self.last_recv = time.monotonic()
        self.last_sent = time.monotonic()
        self.hello: dict | None = None
        self.page_load_id: str | None = None
        self.authenticated: bool | None = None    # True: valid ctlkey; False: none configured
        self.url_key: str | None = None           # ?ctlkey= until hello is checked; then cleared
        self.standby = False                      # glass, but not the driven one (screens.py)
        self.welcomed = False                     # its welcome is queued: broadcasts may follow it
        # A subscriber that cut to art by itself is offered the session again (screens.py _rejoin_later).
        self.rejoin_sid: str | None = None
        self.rejoins = 0
        self.rejoin_timer: asyncio.TimerHandle | None = None
        self.closed = False
        self.behind_bytes = behind_bytes
        self.behind_s = behind_s
        self._out: deque[tuple[str, str, float]] = deque()   # (type, text, queued_at)
        self._out_bytes = 0
        self._sending_since: float | None = None             # queued_at of the message in flight
        self._wake = asyncio.Event()

    @property
    def glass(self) -> bool:
        return self.role in GLASS_ROLES

    def send(self, type_: str, data: dict | None = None) -> None:
        """Queue one message. Raises PageGone if the socket is closed (or just got dropped)."""
        if self.closed:
            raise PageGone(self.cid)
        text = json.dumps({"v": 1, "type": type_, "data": data or {}}, default=str)
        if type_ in self.LATEST_ONLY:
            for i, (t, old, _ts) in enumerate(self._out):
                if t == type_:
                    del self._out[i]
                    self._out_bytes -= len(old)
                    break
        self._out.append((type_, text, time.monotonic()))
        self._out_bytes += len(text)
        if self._out_bytes > self.behind_bytes:
            self.drop_behind(f"{self._out_bytes} bytes unsent")
            raise PageGone(self.cid)
        self._wake.set()

    def behind_age(self, now: float | None = None) -> float:
        """Age of the oldest message not yet written to the socket (0 when caught up)."""
        now = time.monotonic() if now is None else now
        oldest = [t for t in (self._sending_since, self._out[0][2] if self._out else None) if t is not None]
        return now - min(oldest) if oldest else 0.0

    def drop_behind(self, why: str) -> None:
        if self.closed:
            return
        log.warning("%s %s: client behind (%s); disconnecting", self.screen, self.cid, why)
        self.closed = True
        self._out.clear()
        self._out_bytes = 0
        spawn(self.ws.close(code=CLOSE_BEHIND, message=b"behind"), "page-close")

    async def writer(self, hb_idle_s: float) -> None:
        """Drains the outbox; emits `hb` whenever nothing was sent for hb_idle_s."""
        while not self.closed:
            if not self._out:
                self._wake.clear()
                idle_for = time.monotonic() - self.last_sent
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=max(0.0, hb_idle_s - idle_for))
                    continue
                except asyncio.TimeoutError:
                    if self._out:
                        continue
                    type_, text, queued = "hb", HB_TEXT, time.monotonic()
            else:
                type_, text, queued = self._out.popleft()
                self._out_bytes -= len(text)
            if self.ws.closed:
                return
            self._sending_since = queued
            try:
                await self.ws.send_str(text)
                self.last_sent = time.monotonic()
            except (ConnectionError, RuntimeError):
                return
            finally:
                self._sending_since = None

    def info(self) -> dict:
        return {"cid": self.cid, "role": self.role, "remote": self.remote, "device": self.device,
                "page_load_id": self.page_load_id,
                "authenticated": self.authenticated, "standby": self.standby,
                "build": (self.hello or {}).get("build"),
                "caps": (self.hello or {}).get("caps"),
                "flags": (self.hello or {}).get("flags")}


# Callbacks the service supplies.
HelloCb = Callable[[PageClient], dict]                 # returns the welcome data
MessageCb = Callable[[PageClient, str, dict], None]
ClosedCb = Callable[[PageClient], None]


class PageHub:
    def __init__(self, cfg: "RoomConfig", *, on_hello: HelloCb, on_message: MessageCb,
                 on_closed: ClosedCb, on_log: Callable[[str, str], None] | None = None) -> None:
        self.cfg = cfg
        self._on_hello = on_hello
        self._on_message = on_message
        self._on_closed = on_closed
        self._on_log = on_log or (lambda level, msg: None)
        self.clients: dict[str, list[PageClient]] = {sid: [] for sid in cfg.screens}
        self._keys: dict[str, str] = {}
        self._keys_mtime: float | None = None
        self._warned_open: set[str] = set()
        self._load_keys()
        self.allowed_origins = {o.rstrip("/").lower() for o in [*cfg.web_origins, *cfg.kiosk_origins]}
        base = urlsplit(cfg.art_page_base)
        self.allowed_origins.add(f"{base.scheme}://{base.netloc}".lower())

    # -- per-screen control keys (LIVE_V1 §3 "Machine channels always need keys") ---
    def _load_keys(self) -> None:
        """Owner ruling, 14 Oct: no keys in the AV stack, so page keys are never loaded or required."""
        self._keys, self._keys_mtime = {}, None
        return
        path = Path(self.cfg.page_keys_file).expanduser()
        try:
            st = path.stat()
        except OSError:
            self._keys, self._keys_mtime = {}, None
            return
        if st.st_mtime == self._keys_mtime:
            return
        self._keys_mtime = st.st_mtime
        if st.st_mode & 0o077:
            log.warning("page keys file %s is readable by group/others; it should be 0600", path)
        raw = read_json(path, {})
        keys = {str(k): str(v) for k, v in (raw.items() if isinstance(raw, dict) else [])
                if isinstance(v, str) and len(v) >= 16}
        for v in keys.values():
            REDACT.add(v)
        self._keys = keys
        log.info("page control keys loaded for: %s", ", ".join(sorted(keys)) or "no screens")

    def key_required(self, screen: str) -> bool:
        self._load_keys()
        return screen in self._keys

    def _check_key(self, client: PageClient, presented: str | None) -> bool:
        """Glass clients only. True if allowed (a valid key, or none configured for the screen)."""
        want = self._keys.get(client.screen) if self.key_required(client.screen) else None
        if want is None:
            client.authenticated = False
            if client.screen not in self._warned_open:
                self._warned_open.add(client.screen)
                log.warning("%s: no control key configured; accepting unauthenticated %s clients "
                            "(add one to %s to require it)", client.screen, client.role, self.cfg.page_keys_file)
            return True
        ok = bool(presented) and hmac.compare_digest(str(presented).encode(), want.encode())
        client.authenticated = ok
        return ok

    # -- registry queries --------------------------------------------------
    def glass_count(self, screen: str) -> int:
        return sum(1 for c in self.clients.get(screen, []) if c.glass and c.hello and not c.closed)

    def any_connected(self, screen: str) -> bool:
        return any(c.hello and not c.closed for c in self.clients.get(screen, []))

    def hello_clients(self, screen: str) -> list[PageClient]:
        return [c for c in self.clients.get(screen, []) if c.hello and not c.closed]

    def broadcast(self, screen: str, type_: str, data: dict) -> int:
        """Send to every client of a screen (glass and viewer). Returns the count sent."""
        n = 0
        for c in self.hello_clients(screen):
            try:
                c.send(type_, data)
                n += 1
            except PageGone:
                pass
        return n

    async def close_all(self) -> None:
        for cl in self.clients.values():
            for c in list(cl):
                c.closed = True
                await c.ws.close(code=1001, message=b"room_service stopping")

    # -- the WS endpoint ---------------------------------------------------
    async def handle(self, request: web.Request) -> web.StreamResponse:
        screen = request.match_info["screen"]
        role = request.query.get("role", "viewer")
        origin = request.headers.get("Origin")
        if origin is not None and origin.rstrip("/").lower() not in self.allowed_origins:
            # A browser page from anywhere else (DCL v3 §7): the kiosk page's own origin must be
            # in web_origins / kiosk_origins (or be art_page_base's). No Origin = not a browser.
            log.warning("%s: control socket from origin %r refused", screen, origin)
            raise web.HTTPForbidden(text=json.dumps({"error": "origin_not_allowed", "detail": origin}),
                                    content_type="application/json")
        if screen not in self.cfg.screens:
            # Accept the upgrade and close with 4404: a browser reports a refused upgrade as
            # 1006, which the page can't tell from a network failure (DCL v3 §3.7).
            ws = web.WebSocketResponse(heartbeat=None, timeout=2.0)
            await ws.prepare(request)
            await ws.close(code=CLOSE_UNKNOWN_SCREEN, message=b"unknown screen")
            return ws
        if role not in ROLES:
            raise web.HTTPBadRequest(text=json.dumps({"error": "bad_role", "detail": role}),
                                     content_type="application/json")
        t = self.cfg.timeouts
        # timeout = how long close() waits for the peer's close frame (a dead peer never sends one).
        ws = web.WebSocketResponse(heartbeat=None, autoping=True, max_msg_size=64 * 1024, timeout=2.0)
        await ws.prepare(request)
        client = PageClient(screen, role, ws, request.remote,
                            behind_bytes=t.page_behind_bytes, behind_s=t.page_behind_s,
                            user_agent=request.headers.get("User-Agent"))
        client.url_key = request.query.get("ctlkey")
        self.clients.setdefault(screen, []).append(client)
        writer = spawn(client.writer(t.page_hb_idle_s), f"page-writer-{client.cid}")
        watchdog = spawn(self._watchdog(client), f"page-watchdog-{client.cid}")
        log.info("%s: %s socket %s connected from %s", screen, role, client.cid, request.remote)
        window_start, window_n = time.monotonic(), 0
        try:
            async for msg in ws:
                client.last_recv = time.monotonic()
                if msg.type != WSMsgType.TEXT:
                    continue
                now = time.monotonic()
                if now - window_start >= 1.0:
                    window_start, window_n = now, 0
                window_n += 1
                if window_n > MAX_MSGS_PER_S:
                    log.warning("%s %s: over %d msgs/s; closing", screen, client.cid, MAX_MSGS_PER_S)
                    await ws.close(code=CLOSE_RATE, message=b"rate")
                    break
                try:
                    env = json.loads(msg.data)
                except ValueError:
                    continue
                if not isinstance(env, dict):
                    continue
                if env.get("v") != 1:
                    log.warning("%s %s: unsupported envelope v=%r; closing", screen, client.cid, env.get("v"))
                    await ws.close(code=CLOSE_BAD_VERSION, message=b"unsupported version")
                    break
                mtype = env.get("type")
                data = env.get("data") if isinstance(env.get("data"), dict) else {}
                if mtype not in PAGE_TYPES or mtype == "hb":
                    continue
                if mtype == "hello":
                    try:
                        self._hello(client, data)
                    except Exception:
                        log.exception("%s: handling hello failed", screen)
                    continue
                if client.hello is None:
                    continue           # nothing but hello counts before hello
                if not client.glass and mtype != "ack":
                    continue           # viewers are subscribers: only their acks are read (a rejoin offer)
                try:
                    self._on_message(client, mtype, data)
                except Exception:
                    log.exception("%s: handling %s failed", screen, mtype)
        finally:
            client.closed = True
            watchdog.cancel()
            writer.cancel()
            try:
                self.clients[screen].remove(client)
            except ValueError:
                pass
            log.info("%s: %s socket %s closed (code %s)", screen, role, client.cid, ws.close_code)
            if client.hello is not None:
                try:
                    self._on_closed(client)
                except Exception:
                    log.exception("%s: close callback failed", screen)
        return ws

    def _hello(self, client: PageClient, data: dict) -> None:
        if client.hello is not None:
            return                      # one hello per socket
        hrole = data.get("role")
        if hrole and hrole != client.role:
            log.warning("%s %s: hello role %r differs from URL role %r; using the URL's",
                        client.screen, client.cid, hrole, client.role)
        if data.get("screen") and data.get("screen") != client.screen:
            log.warning("%s %s: hello names screen %r", client.screen, client.cid, data.get("screen"))
        if client.glass and not self._check_key(client, client.url_key or data.get("key")):
            log.warning("%s %s: %s client from %s without the screen's control key; closing",
                        client.screen, client.cid, client.role, client.remote)
            self._on_log("warning", f"{client.screen}: rejected a {client.role} client without its key")
            client.closed = True
            spawn(client.ws.close(code=CLOSE_BAD_KEY, message=b"key required"), "page-close")
            return
        client.url_key = None
        client.hello = {k: v for k, v in data.items() if k != "key"}      # never keep the key
        client.hello["at"] = utc_iso()
        client.page_load_id = str(data.get("page_load_id") or "") or None
        # Which glass client is driven (and the duplicate_glass warning) is the screen
        # controller's decision: ScreenController.on_glass_hello.
        welcome = self._on_hello(client)
        client.send("welcome", welcome)
        client.welcomed = True          # subscriber broadcasts (live.*) only ever follow the welcome

    async def _watchdog(self, client: PageClient) -> None:
        t = self.cfg.timeouts
        while not client.closed:
            await asyncio.sleep(min(0.5, t.page_dead_s / 4))
            now = time.monotonic()
            if client.hello is None and now - client.connected_at > t.page_hello_s:
                log.warning("%s %s: no hello within %.0f s; closing", client.screen, client.cid, t.page_hello_s)
                await client.ws.close(code=CLOSE_NO_HELLO, message=b"hello required")
                return
            if now - client.last_recv > t.page_dead_s:
                log.warning("%s %s: no inbound frame for %.1f s; closing dead socket",
                            client.screen, client.cid, now - client.last_recv)
                client.closed = True
                await client.ws.close(code=CLOSE_DEAD, message=b"dead")
                return
            if client.behind_age(now) > client.behind_s:
                client.drop_behind(f"oldest unsent message {client.behind_age(now):.1f} s old")
                return
