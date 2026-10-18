"""Shared plumbing for the render workers (4K60 plan change 3; workers.py is the control's side, worker.py the worker's).

  Channel      one end of a Unix stream socket: framed messages out through a sender thread (callers never block on
               the socket), in through a reader thread that hands each frame to ``on_frame(kind, payload)``.  A frame
               is ``[u32 length][u8 kind][payload]``: K_MSG a pickled object, K_MEDIA a media message as the engine
               makes it (``[0x02][key][frame_id u32 BE][moof+mdat]``), never pickled.  ``send`` is never dropped;
               ``send_lossy`` (forwarded bus inputs: latest values) drops the oldest when the peer falls behind.
  HandleTable  handle id <-> object.  Held strongly while the table owns the object (it must close it), weakly once it
               is given away (to the engine, or the control's own references), so the table never keeps a retired
               plug-in alive.
  Lifecycle    a worker's states: spawning -> idle -> bound -> lingering -> exit (pure: no processes, no clock of its
               own), and when an idle or lingering worker is due to exit.

Same host, same user, our own code on both ends: pickle is the format.  Nothing here gates anything.
"""
from __future__ import annotations

import collections
import logging
import pickle
import socket
import struct
import threading
import weakref
from typing import Any, Callable, Optional

log = logging.getLogger("stream_service.ipc")

HDR = struct.Struct("<IB")
K_MSG = 1
K_MEDIA = 2
MAX_FRAME = 256 << 20          # a frame larger than this is a broken stream
SOCK_BUF = 4 << 20
LOSSY_KEEP = 512               # forwarded inputs queued for a slow peer before the oldest go


def pack(kind: int, payload: bytes) -> bytes:
    return HDR.pack(len(payload), kind) + payload


def dumps(obj: Any) -> bytes:
    return pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)


def loads(data: Any) -> Any:
    return pickle.loads(data)


def tune(sock: socket.socket) -> None:
    for opt in (socket.SO_SNDBUF, socket.SO_RCVBUF):
        try:
            sock.setsockopt(socket.SOL_SOCKET, opt, SOCK_BUF)
        except OSError:
            pass


class Decoder:
    """Frames out of a byte stream, fed in arbitrary pieces (the reader's buffer handling, testable alone)."""

    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        self.buf += data
        out = []
        while len(self.buf) >= HDR.size:
            n, kind = HDR.unpack_from(self.buf)
            if n > MAX_FRAME:
                raise ValueError(f"frame of {n} bytes")
            if len(self.buf) < HDR.size + n:
                break
            out.append((kind, bytes(self.buf[HDR.size:HDR.size + n])))
            del self.buf[:HDR.size + n]
        return out


def _recv_exact(sock: socket.socket, n: int) -> Optional[bytearray]:
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        k = sock.recv_into(view[got:], n - got)
        if k == 0:
            return None
        got += k
    return buf


class Channel:
    def __init__(self, sock: socket.socket, name: str, on_frame: Optional[Callable[[int, bytes], None]] = None,
                 on_close: Optional[Callable[[str], None]] = None, lossy_keep: int = LOSSY_KEEP):
        self.sock = sock
        self.name = name
        self.on_frame = on_frame
        self.on_close = on_close
        sock.setblocking(True)
        tune(sock)
        self._q: collections.deque = collections.deque()
        self._lossy: collections.deque = collections.deque(maxlen=lossy_keep)
        self._cv = threading.Condition()
        self.closed = False
        self.dropped = 0
        self.sent = 0
        self.received = 0
        self.queued_bytes = 0
        self._closing = False
        self._tx = threading.Thread(target=self._send_loop, name=f"{name}-tx", daemon=True)
        self._rx = threading.Thread(target=self._recv_loop, name=f"{name}-rx", daemon=True) if on_frame else None

    def start(self) -> "Channel":
        self._tx.start()
        if self._rx is not None:
            self._rx.start()
        return self

    # -- out --------------------------------------------------------------------------------------------------------
    def send(self, obj: Any) -> None:
        self.push(pack(K_MSG, dumps(obj)))

    def send_raw(self, kind: int, payload: bytes) -> None:
        self.push(pack(kind, payload))

    def send_lossy(self, obj: Any) -> None:
        self.push(pack(K_MSG, dumps(obj)), lossy=True)

    def push(self, frame: bytes, lossy: bool = False) -> None:
        """A packed frame (``pack``); ``lossy``: the oldest such frame goes when LOSSY_KEEP are waiting."""
        with self._cv:
            if self.closed:
                return
            if lossy:
                if len(self._lossy) == self._lossy.maxlen:
                    self.dropped += 1
                self._lossy.append(frame)
            else:
                self._q.append(frame)
                self.queued_bytes += len(frame)
            self._cv.notify()

    def backlog(self) -> int:
        return len(self._q) + len(self._lossy)

    def _send_loop(self) -> None:
        try:
            while True:
                with self._cv:
                    while not self._q and not self._lossy and not self.closed:
                        self._cv.wait()
                    if self._q:
                        frame = self._q.popleft()
                        self.queued_bytes -= len(frame)
                    elif self._lossy:
                        frame = self._lossy.popleft()
                    else:
                        return                       # closed and drained
                self.sock.sendall(frame)
                self.sent += 1
        except OSError as e:
            self._closed(f"send: {type(e).__name__}: {e}")

    # -- in ---------------------------------------------------------------------------------------------------------
    def _recv_loop(self) -> None:
        why = "eof"
        try:
            while True:
                hdr = _recv_exact(self.sock, HDR.size)
                if hdr is None:
                    break
                n, kind = HDR.unpack(hdr)
                if n > MAX_FRAME:
                    why = f"frame of {n} bytes"
                    break
                payload = _recv_exact(self.sock, n) if n else bytearray()
                if payload is None:
                    break
                self.received += 1
                try:
                    self.on_frame(kind, bytes(payload))
                except Exception:
                    log.exception("[%s] handling a frame failed", self.name)
        except OSError as e:
            why = f"recv: {type(e).__name__}: {e}"
        self._closed(why)

    def _closed(self, why: str) -> None:
        with self._cv:
            first = not self.closed
            self.closed = True
            self._q.clear()
            self._lossy.clear()
            self._cv.notify_all()
        if first and self.on_close is not None and not self._closing:
            try:
                self.on_close(why)
            except Exception:
                log.exception("[%s] on_close failed", self.name)

    def close(self, flush_s: float = 0.0) -> None:
        """Stop sending (after ``flush_s`` for the queue to drain) and close the socket."""
        self._closing = True
        if flush_s > 0 and self._tx.is_alive():
            with self._cv:
                self.closed = True               # no new frames; the sender drains what is queued, then returns
                self._cv.notify_all()
            self._tx.join(flush_s)
        self._closed("closed")
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# -- the handle table ---------------------------------------------------------------------------------------------
class HandleTable:
    """hid -> object.  ``add`` owns the object (a strong reference: :meth:`clear` hands it back to be closed) unless
    ``owned=False``; :meth:`give` passes ownership on (the engine took it) and keeps only a weak reference.  Lookups by
    identity (:meth:`hid_of`) and by ``id()`` (:meth:`hid_of_id`, for the engine's error reports) check the object is
    still the same one, so a reused id never maps to a dead entry.  Thread-safe."""

    def __init__(self, first: int = 1) -> None:
        self._next = first
        self._strong: dict[int, Any] = {}
        self._weak: dict[int, Callable[[], Any]] = {}
        self._ids: dict[int, int] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _ref(obj: Any) -> Callable[[], Any]:
        try:
            return weakref.ref(obj)
        except TypeError:                         # not weak-referenceable: held for good (rare: no __weakref__)
            return lambda: obj

    def add(self, obj: Any, hid: Optional[int] = None, owned: bool = True) -> int:
        with self._lock:
            if hid is None:
                hid = self._next
                self._next += 1
            else:
                self._next = max(self._next, hid + 1)
            self._strong.pop(hid, None)
            self._weak.pop(hid, None)
            if owned:
                self._strong[hid] = obj
            else:
                self._weak[hid] = self._ref(obj)
            self._ids[id(obj)] = hid
            if len(self._ids) > 64 and len(self._ids) > 4 * (len(self._strong) + len(self._weak)):
                self._prune()
            return hid

    def _prune(self) -> None:
        live = {}
        for oid, hid in self._ids.items():
            obj = self._get(hid)
            if obj is not None and id(obj) == oid:
                live[oid] = hid
        self._ids = live

    def _get(self, hid: Any) -> Any:
        obj = self._strong.get(hid)
        if obj is not None:
            return obj
        r = self._weak.get(hid)
        if r is None:
            return None
        obj = r()
        if obj is None:
            self._weak.pop(hid, None)
        return obj

    def get(self, hid: Any) -> Any:
        with self._lock:
            return self._get(hid)

    def hid_of(self, obj: Any) -> Optional[int]:
        if obj is None:
            return None
        with self._lock:
            hid = self._ids.get(id(obj))
            return hid if hid is not None and self._get(hid) is obj else None

    def hid_of_id(self, oid: Any) -> Optional[int]:
        with self._lock:
            hid = self._ids.get(oid)
            if hid is None:
                return None
            obj = self._get(hid)
            return hid if obj is not None and id(obj) == oid else None

    def owns(self, hid: Any) -> bool:
        with self._lock:
            return hid in self._strong

    def give(self, hid: Any) -> Any:
        """Ownership passes on: the object, now held weakly (None if unknown or gone)."""
        with self._lock:
            obj = self._strong.pop(hid, None)
            if obj is not None:
                self._weak[hid] = self._ref(obj)
                return obj
            return self._get(hid)

    def drop(self, hid: Any) -> Any:
        """Forget hid: -> the object if the table still owned it (the caller closes it), else None."""
        with self._lock:
            obj = self._strong.pop(hid, None)
            self._weak.pop(hid, None)
            return obj

    def items(self) -> list[tuple[int, Any]]:
        with self._lock:
            out = [(h, o) for h, o in self._strong.items()]
            for h in list(self._weak):
                o = self._get(h)
                if o is not None:
                    out.append((h, o))
            return out

    def clear(self) -> list[Any]:
        """Forget everything: -> the objects the table still owned (to be closed)."""
        with self._lock:
            owned = list(self._strong.values())
            self._strong.clear()
            self._weak.clear()
            self._ids.clear()
            return owned

    def __len__(self) -> int:
        with self._lock:
            return len(self._strong) + sum(1 for r in self._weak.values() if r() is not None)


# -- a worker's lifecycle -----------------------------------------------------------------------------------------
SPAWNING, IDLE, BOUND, LINGERING, EXIT = "spawning", "idle", "bound", "lingering", "exit"
STATES = (SPAWNING, IDLE, BOUND, LINGERING, EXIT)
_MOVES = {
    SPAWNING: {IDLE, BOUND, EXIT},        # BOUND: claimed while it starts (the session waits for it to be ready)
    IDLE: {BOUND, EXIT},
    BOUND: {LINGERING, EXIT},
    LINGERING: {BOUND, EXIT},
    EXIT: set(),
}


class Lifecycle:
    """spawning -> idle -> bound -> lingering (``linger_s``) -> exit.  A worker claimed while spawning is bound at
    once (``ready`` says whether it has said hello).  An idle worker (spawned ahead by a warm hint) and a lingering one
    exit ``linger_s`` after they last went idle; a new session on their GPU before then takes them.  Pure: callers
    pass the time."""

    def __init__(self, linger_s: float, now: float) -> None:
        self.linger_s = linger_s
        self.state = SPAWNING
        self.ready = False
        self.since = now
        self.spawned_at = now
        self.ready_at: Optional[float] = None
        self.binds = 0
        self.why: Optional[str] = None

    def move(self, state: str, now: float, why: Optional[str] = None) -> None:
        if state == self.state:
            return
        if state not in _MOVES[self.state]:
            raise ValueError(f"worker {self.state} -> {state}")
        self.state, self.since = state, now
        if state == BOUND:
            self.binds += 1
        if state == EXIT:
            self.why = why

    def hello(self, now: float) -> None:
        """The worker is up (its CUDA context made)."""
        self.ready, self.ready_at = True, now
        if self.state == SPAWNING:
            self.move(IDLE, now)

    def claim(self, now: float) -> bool:
        """A session takes this worker; False if it can't (bound or gone)."""
        if self.state in (SPAWNING, IDLE, LINGERING):
            self.move(BOUND, now)
            return True
        return False

    def release(self, now: float) -> None:
        """Its session ended: it lingers (released before its hello: still spawning, idle once it says hello)."""
        if self.state != BOUND:
            return
        if self.ready:
            self.move(LINGERING, now)
        else:
            self.state, self.since = SPAWNING, now

    def free(self) -> bool:
        return self.state in (SPAWNING, IDLE, LINGERING)

    def due(self, now: float) -> bool:
        """Time to exit: idle or lingering for linger_s."""
        return self.state in (IDLE, LINGERING) and now - self.since >= self.linger_s

    def left_s(self, now: float) -> Optional[float]:
        return max(0.0, self.linger_s - (now - self.since)) if self.state in (IDLE, LINGERING) else None

    def gone(self, now: float, why: str) -> None:
        if self.state != EXIT:
            self.move(EXIT, now, why)
