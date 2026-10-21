"""The show bus and API: ``ws://127.0.0.1:8703/v1/show``, ``/healthz``, ``/api/v1/state`` (docs/SHOW_ENGINE.md §11).

The wire is the K3 bus that stream_service/showbus.py reads (room_service/dmx/bus.py serves the same handshake):

```
client -> {"type":"hello","v":1|2,"role":"renderer","client_id":"…","want"?:["music.note","music.hit",…]}
server -> {"type":"welcome","schema":2,"source":"show_service","version":"0.1.0","types":[…],"rate_hz":25,
           "stale_after_ms":500,"fixtures":[],"t_ms":…}      (fixtures: none until P4; room's welcome lists its own)
server -> the latest music.notes, music.harmony, music.features and music.clock it holds (a reconnect heals at once)
server -> {"type":"music.note","data":{…}}  …  exactly as published (contract.Envelope; the TypedDicts are the shapes)
server -> {"v":1,"type":"show.state","seq":…,"t_ms":…,"feed":"live","input":{"kind":"show","online":true},
           "audio":{"active":"room-mic","streams":{"room-mic":{"kind","state","test","line"}}},
           "global":{"activity":0.4,"music":{"chroma12":[12],"spectrum32":[32],"energy",…}}}   25 Hz, no fixtures
```

* **No auth** (CLAUDE.md rule 5): ``hello.token`` is ignored. It binds 127.0.0.1; the LAN reaches it only through
  room_service's ``:8700/show/*`` pass-through.
* **Hello** within 5 s (HELLO_TIMEOUT_S), else close 4400. The welcome is always the first message.
* **Filter:** no ``want`` (or an empty one): every type except WANT_ONLY (``music.hit``; Stage 4's
  ``music.rhythm``). With ``want``: an entry matches a type exactly, or as a prefix (``"music"``, ``"music."``,
  ``"music.*"``, ``"*"``); a WANT_ONLY type only by its exact name.
* **Queues:** role ``renderer``: 64 (RENDERER_QUEUE) then close 4008 (CLOSE_BEHIND; it reconnects and heals). Other
  roles: 256 (OTHER_QUEUE), dropping the oldest when full (counted per client).
* **show.state** at 25 Hz in K3 shape with ``global`` at the top level, for v1 clients and sound_view's fallback.
  ``feed`` is ``live`` while show_service runs, whatever the audio does (§11.1); audio health is under ``audio``.
  ``global.music`` comes from the latest music.features and music.harmony; either older than MUSIC_STALE_S reads as
  silence (zeros), so a stopped source never freezes a spectrum on the screens. ``chroma12`` (with ``key`` and
  ``mode``) is music.harmony's: the piano's while it plays. §11.1 wants the *audio* chroma here; that comes from
  dsp.Features.chroma12() once the medium lane exists (it returns None today, so with audio only there is no
  music.harmony and chroma12 reads zeros). sound_view reads it only as a fallback.
* **No fixtures** in show.state until P4 (§11.1's K3 shape otherwise): the welcome says ``"fixtures": []``, as room's
  welcome lists its own, so a v1 client sees there are none rather than looking for them.
* **publish(msg)** from any thread: appends to an inbox and wakes the loop with ``call_soon_threadsafe`` (at most one
  wake-up pending), never blocks, never raises. ``json.dumps`` happens once per message, on ``show-bus``. NaN and
  numpy values are cleaned before sending (strict JSON for browsers). The drain yields every 16 messages, so a
  burst after a stall on our side never closes a renderer that is keeping up.
* **Routes:** ``GET /v1/show`` (WS), ``GET /healthz``, ``GET /api/v1/state`` (``state_fn()``, or 503 while it is
  None), then ``routes(app)`` (api.py's §11.2 routes and ``/v1/events``; a ``routes`` that raises is logged and the
  stubs stay), then a 501 stub with a plain message for every STUBS pair nobody registered.

Threads: one ``show-bus`` thread owns the asyncio loop and every client; ``publish`` and ``stats`` are safe from any
thread; ``state_fn`` and ``health_fn`` are called on ``show-bus`` and must only read snapshots.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import math
import threading
import time
from typing import Any, Callable, Optional

from aiohttp import WSMsgType, web

from . import __version__
from .contract import (BUS_HOST, BUS_PATH, BUS_PORT, BUS_SCHEMA, CLOSE_BEHIND, HELLO_TIMEOUT_S,
                       LATEST_ON_CONNECT_BRAIN, MUSIC_CLOCK, MUSIC_FEATURES, MUSIC_HARMONY, MUSIC_HIT, MUSIC_INSIGHT,
                       MUSIC_NOTE, MUSIC_NOTES, MUSIC_PEDAL, OTHER_QUEUE, RENDERER_QUEUE, ROOM_DIRECTION, ROOM_EVENT,
                       ROOM_LOOK, SPEC_BANDS, WANT_ONLY as _WANT_ONLY_P0)
from .contract import MUSIC_CONTEXT, MUSIC_RHYTHM, WANT_ONLY_ENSEMBLE, WANT_ONLY_STAGE4, WANT_ONLY_STAGE5

log = logging.getLogger("show_service.bus")

SHOW_STATE = "show.state"
STATE_HZ = 25.0
STALE_AFTER_MS = 500                 # welcome: a client may call the feed stale after this long without a frame
MUSIC_STALE_S = 1.0                  # global.music reads silent when features/harmony are older than this
TYPES: tuple[str, ...] = (SHOW_STATE, MUSIC_NOTE, MUSIC_PEDAL, MUSIC_NOTES, MUSIC_HARMONY, MUSIC_FEATURES, MUSIC_HIT,
                          MUSIC_CLOCK, ROOM_DIRECTION, ROOM_LOOK, ROOM_EVENT, MUSIC_INSIGHT,  # these 4: the brain's
                          MUSIC_CONTEXT, MUSIC_RHYTHM)                                        # Stage 4
#: only to clients whose hello ``want`` names them exactly: music.hit (lights) and Stage 4's music.rhythm (10 Hz,
#: about 1 KB each), so the renderer's traffic grows by music.context's 1 Hz only (README S4.5)
WANT_ONLY: tuple[str, ...] = _WANT_ONLY_P0 + WANT_ONLY_STAGE4 + WANT_ONLY_STAGE5 + WANT_ONLY_ENSEMBLE
LATEST_ON_CONNECT: tuple[str, ...] = (MUSIC_NOTES, MUSIC_HARMONY, MUSIC_FEATURES, MUSIC_CLOCK) + LATEST_ON_CONNECT_BRAIN
INBOX_MAX = 4096                     # publish() backlog if show-bus stalls; the oldest go first
DRAIN_YIELD = 16                     # the drain lets the senders run after this many messages
BIND_RETRY_S = 2.0
CLOSE_TIMEOUT_S = 2.0
MAX_IN_MSG = 64 << 10

# §11.2 routes: 501 with a plain message, only where ``routes`` (api.py) registered nothing (it replaces them all).
STUBS: tuple[tuple[str, str], ...] = (
    ("GET", "/v1/events"), ("POST", "/api/v1/step"), ("PUT", "/api/v1/palette"), ("POST", "/api/v1/action"),
    ("DELETE", "/api/v1/nudge/{id}"), ("PUT", "/api/v1/vibe"), ("POST", "/api/v1/describe"),
    ("PUT", "/api/v1/listening"), ("POST", "/api/v1/hue/release"), ("PUT", "/api/v1/autopilot"),
    ("POST", "/api/v1/autopilot/undo"), ("POST", "/api/v1/autopilot/resume"))

_FEATURE_KEYS = ("energy", "gate", "onset", "brightness", "noisiness", "lufs_m", "lufs_s", "punch", "drive")


# -- JSON (strict: browsers reject NaN) -------------------------------------------------------------------------------
def _default(o: Any) -> Any:
    if hasattr(o, "tolist"):          # numpy arrays and scalars
        return o.tolist()
    raise TypeError(f"not JSON: {type(o).__name__}")


def _clean(o: Any, depth: int = 0) -> Any:
    if depth > 32:
        return None
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if o is None or isinstance(o, (bool, int, str)):
        return o
    if isinstance(o, dict):
        return {str(k): _clean(v, depth + 1) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v, depth + 1) for v in o]
    if hasattr(o, "tolist"):
        return _clean(o.tolist(), depth + 1)
    return str(o)


def dumps(obj: Any) -> Optional[str]:
    """Compact strict JSON; non-finite floats become null, numpy becomes lists. None if it can't be done."""
    try:
        return json.dumps(obj, separators=(",", ":"), allow_nan=False, default=_default)
    except (TypeError, ValueError, RecursionError):
        try:
            return json.dumps(_clean(obj), separators=(",", ":"), allow_nan=False)
        except Exception:
            return None


def _num(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _parse_want(want: Any) -> Optional[tuple[str, ...]]:
    if isinstance(want, str):
        want = [want]
    if not isinstance(want, (list, tuple)):
        return None
    out = tuple(w[:64] for w in want[:64] if isinstance(w, str) and w)
    return out or None


# -- one client -------------------------------------------------------------------------------------------------------
class _Client:
    __slots__ = ("ws", "role", "client_id", "want", "limit", "queue", "wake", "dropped", "sent", "closing", "task",
                 "since", "_match")

    def __init__(self, ws: web.WebSocketResponse, role: str, client_id: str, want: Optional[tuple[str, ...]]) -> None:
        self.ws, self.role, self.client_id, self.want = ws, role, client_id, want
        self.limit = RENDERER_QUEUE if role == "renderer" else OTHER_QUEUE
        self.queue: collections.deque[str] = collections.deque()
        self.wake = asyncio.Event()
        self.dropped = self.sent = 0
        self.closing = False
        self.task: Optional[asyncio.Task] = None
        self.since = time.monotonic()
        self._match: dict[str, bool] = {}

    def wants(self, mtype: str) -> bool:
        hit = self._match.get(mtype)
        if hit is None:
            if self.want is None:
                hit = mtype not in WANT_ONLY
            else:
                hit = mtype in self.want or (mtype not in WANT_ONLY and any(_prefix(w, mtype) for w in self.want))
            self._match[mtype] = hit
        return hit

    def offer(self, text: str) -> bool:
        """Queue one message. False: a renderer fell behind (the caller closes it 4008)."""
        if self.closing:
            return True
        if len(self.queue) >= self.limit:
            if self.role == "renderer":
                return False
            self.queue.popleft()
            self.dropped += 1
        self.queue.append(text)
        self.wake.set()
        return True


def _prefix(w: str, mtype: str) -> bool:
    if w == "*":
        return True
    if w.endswith("*"):
        w = w[:-1]
    if w.endswith("."):
        return mtype.startswith(w)
    return mtype.startswith(w + ".")


# -- the bus ----------------------------------------------------------------------------------------------------------
class Bus:
    """The show bus and the show API on one aiohttp app, on its own thread (``show-bus``)."""

    def __init__(self, host: str = BUS_HOST, port: int = BUS_PORT, *, state_fn: Callable[[], Optional[dict]] | None = None,
                 health_fn: Callable[[], dict] | None = None,
                 routes: Callable[[web.Application], None] | None = None) -> None:
        """``state_fn``: the body of /api/v1/state (None: 503 "starting"). ``health_fn``: Receiver.health, for
        show.state's ``audio`` and /healthz's ``streams``. Both run on show-bus and must only read snapshots.
        ``routes(app)``: called once on show-bus at startup to add more routes (api.py); if it raises, the error is
        logged (``routes_error``) and the stubs stay. ``port`` 0 binds an ephemeral port (tests); ``.port`` is the
        bound one after start()."""
        self.host, self.port = host, int(port)
        self.state_fn, self.health_fn = state_fn, health_fn
        self.routes = routes
        self.routes_error: Optional[str] = None
        self.bind_error: Optional[str] = None
        self.t0 = time.monotonic()
        self._inbox: collections.deque = collections.deque(maxlen=INBOX_MAX)
        self._drain_pending = False         # True while a _wake is scheduled and hasn't run yet
        self._drainer: Optional[asyncio.Task] = None
        self._stopping = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._runner: Optional[web.AppRunner] = None
        self._tasks: list[asyncio.Task] = []
        self._closing: set[asyncio.Future] = set()               # 4008 closes in flight (kept referenced)
        self._clients: set[_Client] = set()
        self._latest: dict[str, tuple[dict, str, float]] = {}     # type -> (msg, json, monotonic arrival)
        self._published: dict[str, int] = {}
        self._seq = 0
        self.state_frames = self.kicked = self.bad = self.inbox_dropped = 0

    # -- lifecycle (any thread) ----------------------------------------------------------------------------------
    def start(self) -> None:
        """Start ``show-bus``; returns once the first bind attempt is done (``bind_error`` set if it failed: it
        keeps retrying every 2 s, and publish() keeps working)."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="show-bus", daemon=True)
        self._thread.start()
        self._ready.wait(10.0)

    def stop(self, timeout: float = 2.0) -> None:
        self._stopping = True
        loop, thread = self._loop, self._thread
        if loop is None or thread is None or not thread.is_alive():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._shutdown(), loop).result(timeout)
        except Exception as e:  # timeout or a closed loop: stop it anyway
            log.warning("show bus: shutdown incomplete: %s", e or type(e).__name__)
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            pass
        thread.join(timeout)

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}{BUS_PATH}"

    # -- publish (any thread; the hot thread's only call) -------------------------------------------------------
    def publish(self, msg: dict) -> None:
        """Queue ``msg`` ({"type", "data"}) for every client that wants it. Never blocks, never raises; the caller
        must not mutate ``msg`` afterwards."""
        try:
            inbox = self._inbox
            if len(inbox) >= INBOX_MAX:
                self.inbox_dropped += 1
            inbox.append(msg)
            if self._drain_pending:
                return
            loop = self._loop
            if loop is None or self._stopping:
                return
            self._drain_pending = True       # only _wake clears it; an append after that schedules another
            loop.call_soon_threadsafe(self._wake)
        except Exception:                    # a closed loop: stay quiet (the pending flag stays set)
            pass

    def stats(self) -> dict:
        clients = []
        for c in list(self._clients):
            clients.append({"role": c.role, "client_id": c.client_id, "want": list(c.want) if c.want else None,
                            "queued": len(c.queue), "dropped": c.dropped, "sent": c.sent,
                            "age_s": round(time.monotonic() - c.since, 1)})
        return {"url": self.url, "bind_error": self.bind_error, "routes_error": self.routes_error, "clients": clients,
                "published": dict(self._published),
                "state_frames": self.state_frames, "kicked": self.kicked, "bad": self.bad,
                "inbox_dropped": self.inbox_dropped}

    def latest(self, mtype: str) -> Optional[dict]:
        """The latest published message of ``mtype`` (the envelope as published), or None."""
        hit = self._latest.get(mtype)
        return hit[0] if hit else None

    # -- the loop (show-bus) -------------------------------------------------------------------------------------
    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._startup())
        except Exception:
            log.exception("show bus: startup failed")
        finally:
            self._ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                for t in asyncio.all_tasks(loop):
                    t.cancel()
                loop.run_until_complete(asyncio.sleep(0))
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    async def _startup(self) -> None:
        app = web.Application(client_max_size=MAX_IN_MSG)
        app.router.add_get(BUS_PATH, self._ws)
        app.router.add_get("/healthz", self._healthz)
        app.router.add_get("/api/v1/state", self._state)
        if self.routes is not None:
            try:
                self.routes(app)
            except Exception as e:
                self.routes_error = f"{type(e).__name__}: {e}"
                log.exception("show bus: the API routes failed to mount; the 501 stubs stay")
        taken = {(r.method, r.resource.canonical) for r in app.router.routes() if r.resource is not None}
        for method, path in STUBS:
            if (method, path) not in taken:
                app.router.add_route(method, path, self._stub)
        self._runner = web.AppRunner(app, access_log=None, shutdown_timeout=1.0)
        await self._runner.setup()
        if not await self._bind():
            self._tasks.append(asyncio.create_task(self._bind_retry(), name="show-bus-bind"))
        self._tasks.append(asyncio.create_task(self._state_loop(), name="show-bus-state"))
        if self._inbox:                      # published before the loop existed
            self._drain_pending = True
            self._loop.call_soon(self._wake)

    async def _bind(self) -> bool:
        try:
            await web.TCPSite(self._runner, self.host, self.port).start()
        except OSError as e:
            if self.bind_error != str(e):
                log.error("show bus: can't bind %s:%d (retrying every %.0f s): %s", self.host, self.port,
                          BIND_RETRY_S, e)
            self.bind_error = str(e)
            return False
        addrs = self._runner.addresses
        if addrs and isinstance(addrs[0], tuple):
            self.port = int(addrs[0][1])
        self.bind_error = None
        log.info("show bus: %s (api http://%s:%d/api/v1/state)", self.url, self.host, self.port)
        return True

    async def _bind_retry(self) -> None:
        while True:
            await asyncio.sleep(BIND_RETRY_S)
            if await self._bind():
                return

    async def _shutdown(self) -> None:
        for t in self._tasks + ([self._drainer] if self._drainer is not None else []):
            t.cancel()
        clients = list(self._clients)
        for c in clients:
            c.closing = True
            if c.task is not None:
                c.task.cancel()
        if clients:
            await asyncio.gather(*(self._close(c, 1001, b"show_service stopping") for c in clients),
                                 return_exceptions=True)
        if self._runner is not None:
            await self._runner.cleanup()

    async def _close(self, c: _Client, code: int, message: bytes) -> None:
        try:
            await asyncio.wait_for(c.ws.close(code=code, message=message), CLOSE_TIMEOUT_S)
        except Exception:                    # a client that never reads: the transport is closed under it
            pass

    # -- fan-out (show-bus) --------------------------------------------------------------------------------------
    def _wake(self) -> None:
        self._drain_pending = False
        if self._drainer is None or self._drainer.done():
            self._drainer = asyncio.ensure_future(self._drain())

    async def _drain(self) -> None:
        """Empty the inbox, yielding every DRAIN_YIELD messages so the senders run between them: a burst (show-bus
        starved of the GIL for a moment) then never fills a renderer's queue; only a client that doesn't read does."""
        inbox, n = self._inbox, 0
        while inbox:
            try:
                msg = inbox.popleft()
            except IndexError:
                break
            mtype = msg.get("type") if isinstance(msg, dict) else None
            text = dumps(msg) if isinstance(mtype, str) and mtype else None
            if text is None:
                self.bad += 1
                continue
            self._published[mtype] = self._published.get(mtype, 0) + 1
            self._latest[mtype] = (msg, text, time.monotonic())
            self._fanout(mtype, text)
            n += 1
            if n % DRAIN_YIELD == 0:
                await asyncio.sleep(0)

    def _fanout(self, mtype: str, text: str) -> None:
        for c in list(self._clients):
            if c.wants(mtype) and not c.offer(text):
                self._kick(c)

    def _kick(self, c: _Client) -> None:
        c.closing = True
        c.queue.clear()
        self.kicked += 1
        if c.task is not None:
            c.task.cancel()
        log.warning("show bus: %s (%s) fell %d behind: closing %d", c.client_id, c.role, c.limit, CLOSE_BEHIND)
        fut = asyncio.ensure_future(self._close(c, CLOSE_BEHIND, b"too slow: renderer queue full"))
        self._closing.add(fut)
        fut.add_done_callback(self._closing.discard)

    async def _sender(self, c: _Client) -> None:
        try:
            while not c.closing:
                while not c.queue:
                    c.wake.clear()
                    await c.wake.wait()
                    if c.closing:
                        return
                await c.ws.send_str(c.queue.popleft())
                c.sent += 1
        except Exception:                    # the connection went: the handler's read loop ends and cleans up
            pass

    # -- show.state (show-bus) -----------------------------------------------------------------------------------
    async def _state_loop(self) -> None:
        loop = asyncio.get_running_loop()
        period = 1.0 / STATE_HZ
        due = loop.time()
        while True:
            due += period
            delay = due - loop.time()
            if delay < -period:              # fell behind (a stall): don't burst
                due, delay = loop.time(), 0.0
            await asyncio.sleep(max(0.0, delay))
            try:
                if any(c.wants(SHOW_STATE) for c in self._clients):
                    text = dumps(self.state_frame())
                    if text is not None:
                        self.state_frames += 1
                        self._fanout(SHOW_STATE, text)
            except Exception:
                log.exception("show bus: show.state failed")
                await asyncio.sleep(1.0)

    def _fresh(self, mtype: str, now: float) -> Optional[dict]:
        hit = self._latest.get(mtype)
        if hit is None or now - hit[2] > MUSIC_STALE_S:
            return None
        data = hit[0].get("data")
        return data if isinstance(data, dict) else None

    def _health(self) -> dict:
        if self.health_fn is None:
            return {}
        try:
            h = self.health_fn()
        except Exception:
            return {}
        return h if isinstance(h, dict) else {}

    def audio_summary(self) -> dict:
        h = self._health()
        streams = {}
        for name, s in (h.get("streams") or {}).items():
            if isinstance(s, dict):
                streams[str(name)] = {"kind": s.get("kind"), "state": s.get("state"), "test": bool(s.get("test")),
                                      "line": s.get("line")}
        return {"active": h.get("active_audio"), "streams": streams}

    def state_frame(self) -> dict:
        """One K3 show.state frame (``global`` at the top level, no fixtures)."""
        now = time.monotonic()
        self._seq += 1
        f = self._fresh(MUSIC_FEATURES, now)
        h = self._fresh(MUSIC_HARMONY, now)
        music: dict[str, Any] = {k: _num(f.get(k)) if f else 0.0 for k in _FEATURE_KEYS}
        if not f:
            music["lufs_m"] = music["lufs_s"] = -70.0
        spec = f.get("spectrum32") if f else None
        music["spectrum32"] = ([_num(v) for v in spec] if isinstance(spec, (list, tuple)) and len(spec) == SPEC_BANDS
                               else [0.0] * SPEC_BANDS)
        bands = f.get("bands") if f else None
        music["bands"] = ({str(k): _num(v.get("level")) for k, v in bands.items() if isinstance(v, dict)}
                          if isinstance(bands, dict) else {})
        chroma = h.get("chroma12") if h else None
        music["chroma12"] = ([_num(v) for v in chroma] if isinstance(chroma, (list, tuple)) and len(chroma) == 12
                             else [0.0] * 12)
        music["key"] = h.get("key") if h else None
        music["mode"] = h.get("mode") if h else None
        music["stream"] = f.get("stream") if f else None
        music["t_ms"] = _num(f.get("t_ms"), None) if f else None
        return {"v": 1, "type": SHOW_STATE, "seq": self._seq, "t_ms": round(now * 1000.0, 2), "feed": "live",
                "input": {"kind": "show", "online": True}, "audio": self.audio_summary(),
                "global": {"activity": music["energy"], "music": music}}

    # -- handlers (show-bus) -------------------------------------------------------------------------------------
    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=10.0, max_msg_size=MAX_IN_MSG, timeout=CLOSE_TIMEOUT_S)
        await ws.prepare(request)
        hello: Any = None
        try:
            msg = await ws.receive(timeout=HELLO_TIMEOUT_S)
            if msg.type == WSMsgType.TEXT:
                hello = json.loads(msg.data)
        except (asyncio.TimeoutError, ValueError, TypeError):
            hello = None
        if not isinstance(hello, dict) or hello.get("type") != "hello":
            await ws.close(code=4400, message=b"hello expected")
            return ws
        role = str(hello.get("role") or "ui")[:32]
        c = _Client(ws, role, str(hello.get("client_id") or "?")[:64], _parse_want(hello.get("want")))
        welcome = {"type": "welcome", "schema": BUS_SCHEMA, "source": "show_service", "version": __version__,
                   "types": list(TYPES), "rate_hz": STATE_HZ, "stale_after_ms": STALE_AFTER_MS, "fixtures": [],
                   "t_ms": round(time.monotonic() * 1000.0, 2)}
        c.queue.append(dumps(welcome))
        for mtype in LATEST_ON_CONNECT:      # the same loop step as joining: nothing is missed or doubled
            hit = self._latest.get(mtype)
            if hit is not None and c.wants(mtype):
                c.queue.append(hit[1])
        self._clients.add(c)
        c.task = asyncio.create_task(self._sender(c), name=f"show-bus-send-{c.client_id}")
        log.info("show bus: %s (%s) connected%s", c.client_id, role, f", want {list(c.want)}" if c.want else "")
        try:
            async for _ in ws:               # clients only listen
                pass
        finally:
            self._clients.discard(c)
            c.closing = True
            c.wake.set()
            if c.task is not None:
                c.task.cancel()
            log.info("show bus: %s disconnected (close %s)", c.client_id, ws.close_code)
        return ws

    async def _healthz(self, request: web.Request) -> web.Response:
        h = self._health()
        streams = {str(k): v.get("state") for k, v in (h.get("streams") or {}).items() if isinstance(v, dict)}
        body = {"ok": True, "service": "show_service", "version": __version__,
                "uptime_s": round(time.monotonic() - self.t0, 1), "clients": len(self._clients),
                "active_audio": h.get("active_audio"), "streams": streams}
        return web.Response(text=dumps(body) or "{}", content_type="application/json")

    async def _state(self, request: web.Request) -> web.Response:
        try:
            body = self.state_fn() if self.state_fn is not None else None
        except Exception as e:
            log.exception("show bus: state_fn failed")
            return web.Response(status=500, text=dumps({"error": f"{type(e).__name__}: {e}"}) or "{}",
                                content_type="application/json")
        if body is None:
            return web.Response(status=503, text='{"error":"starting"}', content_type="application/json")
        return web.Response(text=dumps(body) or "{}", content_type="application/json")

    async def _stub(self, request: web.Request) -> web.Response:
        return web.Response(status=501, text=f"not built yet: {request.method} {request.path} "
                                             "(docs/SHOW_ENGINE.md §11.2)\n")
