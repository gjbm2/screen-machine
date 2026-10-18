"""One transcription per room, shared by its screens' live score scenes (owner, 18 Oct: both screens transcribe the
same MIDI; each ran its own ScoreBuilder and PM2S worker, about 1.3 CPU cores apiece, for the same notes).

A :class:`RoomScore` holds the room's one ScoreBuilder and one analysis worker.  Each frame, the member that leads
feeds its SoundView frame's notes in, sends the analysis requests and takes the replies, and polls the bars, which go
to every member's queue.  Leading goes to whoever is rendering: a member that ticks while the leader hasn't fed for
LEAD_STALE_S takes over (the leader switched scene, or a member was prepared but isn't on screen yet).  A member that
joins mid-phrase is handed the phrase's bars so far.  The last to leave closes the worker.

The screens render on their own threads: every use of the builder goes through ``lock``, held only for CPU work,
never across a GPU wait (one screen's wait would stall the other's render thread): a scene feeds and takes its
bars in one short hold, and reads the builder through :class:`Shared`, which takes the lock for each call.  Each
scene keeps its own engraver and drawing (sizes and layouts can differ).

Rooms are keyed by the music they hear; show_service hears one room today, so the scenes use one key.

In a proc worker (one render process per screen: SM_STREAM_WORKER) the screens can't share a builder in memory, so
the room's transcription runs in a process of its own (score/room_proc.py: the music bus into a SoundView into a
RoomScore, as a scene feeds it) and :class:`RemoteRoom` stands in for RoomScore: the same ``tick`` and ``leave``, and
a :class:`BuilderView` for the scene's reads.  The room's process sends its bars and, whenever it changes, the
builder state a scene reads each frame (:func:`view_of`); the view runs ScoreBuilder's own position, where, key and
_staff on it, so nothing per frame crosses the socket.  A reader thread queues what comes and ``tick`` applies it on
the render thread, which never waits.  The first worker to need the room's process starts it (under a lock, in its
own session); it exits LINGER_S after its last client leaves.
"""
from __future__ import annotations

import collections
import fcntl
import logging
import os
import pathlib
import pickle
import socket
import struct
import subprocess
import sys
import threading
import time
from typing import Any, Optional

from .quantize import Bar, BeatGrid, Note, ScoreBuilder

log = logging.getLogger("stream_service.score")

LEAD_STALE_S = 0.25


class Shared:
    """The room's builder for one scene: each method call under the room's lock, attributes read as they are."""

    def __init__(self, builder: ScoreBuilder, lock) -> None:
        object.__setattr__(self, "_b", builder)
        object.__setattr__(self, "_lock", lock)

    def __getattr__(self, name: str) -> Any:
        v = getattr(self._b, name)
        if not callable(v):
            return v
        lock = self._lock

        def call(*a, **k):
            with lock:
                return v(*a, **k)
        return call

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("the room's builder is fed by RoomScore.feed")


class RoomScore:
    _rooms: dict[str, "RoomScore"] = {}
    _rooms_lock = threading.Lock()

    def __init__(self, key: str, analysis: bool = True) -> None:
        self.key = key
        self.lock = threading.RLock()
        self.builder = ScoreBuilder()
        self.analyser = None
        self.members: list = []
        self.queues: dict[int, collections.deque] = {}
        self.phrase_bars: list = []            # the bars since the last phrase end, for a member joining mid-phrase
        self.leader = None
        self.fed_at = 0.0                      # monotonic s of the leader's last feed
        self.rev = 0                           # analysis replies taken (a member re-lays its glows on a change)
        if analysis:
            try:
                from .client import Analyser
                if Analyser.available():
                    self.analyser = Analyser()
                    self.builder.use_analysis(self.analyser.ok)
                else:
                    log.warning("live_score: no analysis venv or PM2S weights; writing 4/4 on the beat clock")
            except Exception as e:
                log.warning("live_score: no analysis (%s); writing 4/4 on the beat clock", e)

    # -- membership --------------------------------------------------------------------------------------------
    @classmethod
    def join(cls, key: str, member: Any, analysis: bool = True) -> "RoomScore":
        with cls._rooms_lock:
            room = cls._rooms.get(key)
            if room is None:
                room = cls._rooms[key] = RoomScore(key, analysis)
            with room.lock:
                room.members.append(member)
                room.queues[id(member)] = collections.deque(room.phrase_bars)
            return room

    def leave(self, member: Any) -> None:
        with RoomScore._rooms_lock:
            with self.lock:
                if member in self.members:
                    self.members.remove(member)
                self.queues.pop(id(member), None)
                if self.leader is member:
                    self.leader = None
                last = not self.members
            if last:
                if RoomScore._rooms.get(self.key) is self:
                    del RoomScore._rooms[self.key]
                if self.analyser is not None:
                    self.analyser.close()
                    self.analyser = None

    # -- each frame (under ``lock``) ---------------------------------------------------------------------------
    def leads(self, member: Any) -> bool:
        """Whether ``member`` feeds this frame: the leader, or anyone once the leader has gone quiet."""
        now = time.monotonic()
        if self.leader is not member and (self.leader is None or now - self.fed_at > LEAD_STALE_S):
            self.leader = member
        if self.leader is member:
            self.fed_at = now
            return True
        return False

    def feed(self, frame: Any, t: float) -> None:
        """The leader's frame: its notes in, the analysis out and back, the new bars to every member's queue."""
        b = self.builder
        for p, vel, age in frame.onsets:
            b.note_on(p, float(vel), t - age)
        for p, _vo, age in frame.releases:
            b.note_off(p, t - age)
        b.pedal(float(frame.pedals[0]), t)
        an = self.analyser
        if an is not None:
            if an.ok:
                req = b.analysis_request(t)
                if req is not None:
                    an.submit("a", req)
                for _key, meta, _blob in an.results():
                    b.on_analysis(meta)
                    self.rev += 1
            elif b.analysed:
                b.use_analysis(False)                         # the worker died: the beat clock from the next phrase
        for bar in b.poll(t):
            if bar.marker and not bar.rewrite:
                self.phrase_bars = []
            else:
                self.phrase_bars.append(bar)
            for q in self.queues.values():
                q.append(bar)

    def tick(self, member: Any, frame: Any, t: float) -> tuple[list, int]:
        """A scene's frame: it feeds if it leads, then takes its bars -> (bars, analysis replies so far).  One short
        hold of the lock, CPU only."""
        with self.lock:
            if self.leads(member):
                self.feed(frame, t)
            return self.take(member), self.rev

    def take(self, member: Any) -> list:
        """The bars written since ``member`` last took them."""
        q = self.queues.get(id(member))
        if not q:
            return []
        out = list(q)
        q.clear()
        return out


# -- the room in its own process (proc workers) -----------------------------------------------------------------------
LINGER_S = 30.0                        # the room's process exits this long after its last client leaves
SPAWN_WAIT_S = 10.0                    # a worker waits this long for the process it started to listen
RETRY_S = (0.5, 5.0)                   # reconnects back off from 0.5 s, doubling to 5 s
INBOX_MAX = 256                        # a scene that isn't ticking (prepared, not shown): its inbox is folded to one
MUSIC_BUS = "ws://127.0.0.1:8703/v1/show"
SPAWN_ARGS: tuple = ()                 # more arguments for the room's process (tests: --no-analysis)
LOG_FILE = pathlib.Path.home() / ".local" / "state" / "screen-machine" / "logs" / "score_room.log"
_LEN = struct.Struct(">I")


def sock_path(key: str) -> pathlib.Path:
    """The room's process listens here ($XDG_RUNTIME_DIR/screen-machine, 0700)."""
    return pathlib.Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "screen-machine" / f"score-room-{key}.sock"


def pack(obj: Any) -> bytes:
    """One message on the room's socket: a 4-byte length, then the pickle (one user's processes on one host)."""
    b = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    return _LEN.pack(len(b)) + b


def _recv(sock: socket.socket, n: int) -> Optional[bytes]:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def unpack_from(sock: socket.socket) -> Optional[Any]:
    """The next message, or None at the end of the stream."""
    head = _recv(sock, _LEN.size)
    if head is None:
        return None
    body = _recv(sock, _LEN.unpack(head)[0])
    return None if body is None else pickle.loads(body)


def try_connect(path: pathlib.Path) -> Optional[socket.socket]:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(str(path))
        return s
    except OSError:
        s.close()
        return None


def view_of(b: ScoreBuilder) -> dict:
    """What a scene reads of the builder each frame (live_score: position, where, key, bar_seconds, _staff, beats,
    unit, next_bar, notes), as plain data for BuilderView.apply."""
    notes = b.notes
    ids = {n.id for n in notes}
    return {"clock": (b.clock.P, b.clock.t0, b.clock.b0),
            "grid": (list(b.grid.t), b.grid.idx0) if b.grid is not None else None,
            "analysed": b.analysed, "beats": b.beats, "unit": b.unit, "next_bar": b.next_bar, "swing": b.swing,
            "pc_weight": list(b.pc_weight), "key_fifths": b.key_fifths,
            "hand": {i: h for i, h in b.hand.items() if i in ids},
            "staff_of": {i: st for i, st in b.staff_of.items() if i in ids},
            "line_staff": b.line_staff, "last_event_t": b.last_event_t,
            "notes": [(n.id, n.pitch, n.vel, n.t_on, n.t_off, n.restruck) for n in notes]}


class BuilderView(ScoreBuilder):
    """A worker's replica of the room's builder: ScoreBuilder's own reads on the state the room's process sends.  A
    note keeps its object while it's sent, so a glow holding it sees its release."""

    def __init__(self) -> None:
        super().__init__()
        self._by_id: dict[int, Note] = {}
        self._grid_src = None

    def apply(self, v: dict) -> None:
        self.clock.P, self.clock.t0, self.clock.b0 = v["clock"]
        g = v["grid"]
        if g != self._grid_src:
            self.grid = None if g is None else BeatGrid(g[0], g[1])
            self._grid_src = g
        self.analysed, self.beats, self.unit = v["analysed"], v["beats"], v["unit"]
        self.next_bar, self.swing = v["next_bar"], v["swing"]
        self.pc_weight, self.key_fifths = list(v["pc_weight"]), v["key_fifths"]
        self.hand, self.staff_of = dict(v["hand"]), dict(v["staff_of"])
        self.line_staff, self.last_event_t = v["line_staff"], v["last_event_t"]
        by_id, notes = {}, []
        for i, pitch, vel, t_on, t_off, restruck in v["notes"]:
            n = self._by_id.get(i)
            if n is None:
                n = Note(i, pitch, vel, t_on, t_off, restruck)
            else:
                n.t_on, n.t_off, n.restruck = t_on, t_off, restruck
            by_id[i] = n
            notes.append(n)
        self._by_id, self.notes = by_id, notes


def _marker() -> Bar:
    """A phrase end for the scene: a new room process (the old one went away) starts its music afresh."""
    return Bar(index=0, staves=([], []), t_start=0.0, t_end=0.0, empty=True, marker=True)


class RemoteRoom:
    """The room's transcription in its own process, as a scene in a proc worker sees it: RoomScore's ``tick`` and
    ``leave``, and ``builder`` (a BuilderView) for its reads.  Starts the room's process if none is listening; with
    none (it can't start, or it went away) the scene gets no bars and degrades as it does with no notes."""

    def __init__(self, key: str, music_bus: Optional[str] = None) -> None:
        self.key = key
        self.music_bus = music_bus or os.environ.get("SM_MUSIC_BUS") or MUSIC_BUS
        self.builder = BuilderView()
        self.rev = 0
        self.connects = 0
        self._inbox: collections.deque = collections.deque()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sock: Optional[socket.socket] = None
        self._thread = threading.Thread(target=self._run, name=f"score-room-{key}", daemon=True)
        self._thread.start()

    @classmethod
    def join(cls, key: str, member: Any, music_bus: Optional[str] = None) -> "RemoteRoom":
        return cls(key, music_bus)

    # -- the render thread ---------------------------------------------------------------------------------------
    def tick(self, member: Any, frame: Any, t: float) -> tuple[list, int]:
        """The bars since the last tick and the analysis replies so far; the builder view brought up to date.  The
        frame is unused: the room's process hears the bus itself."""
        with self._lock:
            msgs = list(self._inbox)
            self._inbox.clear()
        bars: list = []
        for m in msgs:
            if m.get("view") is not None:
                self.builder.apply(m["view"])
            bars.extend(m.get("bars") or ())
            self.rev = int(m.get("rev", self.rev))
        return bars, self.rev

    def leave(self, member: Any) -> None:
        self._stop.set()
        s = self._sock
        if s is not None:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    # -- the reader thread ---------------------------------------------------------------------------------------
    def _put(self, msg: dict) -> None:
        with self._lock:
            self._inbox.append(msg)
            if len(self._inbox) > INBOX_MAX:                  # nobody is ticking: one message, the bars and the latest
                ms = list(self._inbox)                        # view and rev
                view = next((m["view"] for m in reversed(ms) if m.get("view") is not None), None)
                self._inbox.clear()
                self._inbox.append({"bars": [b for m in ms for b in (m.get("bars") or ())], "rev": ms[-1].get("rev"),
                                    "view": view})

    def _run(self) -> None:
        delay = RETRY_S[0]
        while not self._stop.is_set():
            s = self._connect()
            if s is None:
                self._stop.wait(delay)
                delay = min(RETRY_S[1], delay * 2)
                continue
            delay = RETRY_S[0]
            if self.connects:
                self._put({"bars": [_marker()], "rev": self.rev, "view": None})
            self.connects += 1
            self._sock = s
            try:
                while not self._stop.is_set():
                    msg = unpack_from(s)
                    if msg is None:
                        break
                    self._put(msg)
            except (OSError, pickle.UnpicklingError, EOFError) as e:
                if not self._stop.is_set():
                    log.warning("live_score: the room's score process: %s", e)
            finally:
                self._sock = None
                s.close()
            if not self._stop.is_set():
                log.warning("live_score: the room's score process went away; reconnecting")

    def _connect(self) -> Optional[socket.socket]:
        path = sock_path(self.key)
        s = try_connect(path)
        if s is not None:
            return s
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with open(path.with_suffix(".lock"), "a+") as lk:
                fcntl.flock(lk, fcntl.LOCK_EX)                # one worker starts it; the others wait, then connect
                s = try_connect(path)
                if s is not None:
                    return s
                self._spawn()
                end = time.monotonic() + SPAWN_WAIT_S
                while time.monotonic() < end and not self._stop.is_set():
                    s = try_connect(path)
                    if s is not None:
                        return s
                    time.sleep(0.05)
        except OSError as e:
            log.warning("live_score: can't start the room's score process (%s)", e)
        return None

    def _spawn(self) -> None:
        from .client import REPO
        env = {k: v for k, v in os.environ.items() if k != "SM_STREAM_WORKER"}
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "ab") as out:
            p = subprocess.Popen([sys.executable, "-m", "stream_service.score.room_proc", "--key", self.key,
                                  "--music-bus", self.music_bus, *SPAWN_ARGS], cwd=str(REPO), env=env, stdin=subprocess.DEVNULL,
                                 stdout=out, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
        threading.Thread(target=p.wait, name="score-room-reap", daemon=True).start()     # no zombie when it exits
        log.info("live_score: started the room's score process (pid %d, key %s)", p.pid, self.key)

