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
"""
from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Any, Optional

from .quantize import ScoreBuilder

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
