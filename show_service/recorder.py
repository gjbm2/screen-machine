"""Piano recordings (the Room page's Record, 22 Oct): the brain component ``recorder``.

- **The tap** (show-rx; ``__main__.on_midi`` calls :meth:`Recorder.on_midi` right after NoteTap, under its own guard
  "recorder"). While nothing records it returns at once. While a take runs it keeps only the streams of show.yaml
  ``situations.midi.streams`` (unset: every MIDI stream; test streams never), and appends ``(stream, msg, arrival,
  host offset)`` to the take's deque: O(1), no I/O, no lock. The host offset is ``to_host(t) - t`` for the packet's
  first stamp (its arrival when the map isn't ready), so the brain can place the packet's events without calling
  ``to_host`` again.
- **The take** (show-brain: drained every tick and on every view, so the deque stays short). Per stream it applies
  midi.Piano's wire rule (vban.midi_seq_verdict: duplicates dropped, a gap healed from ``prev``, oldest first, what is
  still missing counted as ``lost``). Event times: the sender's own ``t_ns`` deltas within one wire session (the
  kiosk's stamps: network jitter never enters) on the host offset of the session's first packet; a new wire session
  (the piano box re-plugged) is placed by its own first packet, i.e. stitched with ``to_host``. It keeps only the
  channels of ``situations.midi.channels`` (unset: all): note on/off (note-off velocities kept; note-on velocity 0 is
  a note-off) and CC 64/66/67 raw (a half-pedalled damper stays proportional). Events stamped before Record (a first
  batch's ``prev``) are left out, and so is a note-off whose note-on is. A state packet only closes a note the take
  holds that the state says is up (at the state's time); it never opens one. A session change closes what the old
  session held at its last time. Stop closes every held note and pedal at Stop (or the last event, if later).
- **The file**: SMF type 0 written with mido, PPQ 960 at 120 bpm (1920 ticks a second, 0.52 ms), a track-name meta
  (the title) and the tempo meta first, every event's channel kept. midi.py's hot-repeat cap is applied as it is
  written (a re-strike of a key within 0.25 s is at most the previous, capped, velocity + 32: the adsilent's v1
  reports absurd velocities on fast repeats); the take keeps the raw ones. Leading silence is trimmed to 0.5 s
  before the first event; end-of-track is 1 s after the last. A take with no note is not saved.
- **Storage**: ``<folder>/<id>.mid`` + ``<id>.json`` (``{v, id, title, recorded_at, duration_s, notes, events,
  host_t0, wall_start, stream, streams, sender, channels, sessions, lost, ppq, bpm}``; ``host_t0`` is the host
  CLOCK_MONOTONIC second of the file's 0, ``wall_start`` its epoch second, so the mic's audio can be lined up later).
  The folder is ``RECORDINGS_DIR`` unless show.yaml ``tests.piano.recordings`` names one (~ expanded; a relative path
  is under the repo). Ids are sortable and url-safe: ``YYYYMMDD-HHMMSS-xxxx`` (local time of Record, 4 hex). Every
  write is a temporary file in the same folder, flushed, fsynced and renamed; Stop's encode and writes, a rename and a
  delete run in a worker thread (``run_in_executor``) when called on the brain loop, so show-brain never blocks.
  Rename changes the title in the JSON and the track name in the .mid (spliced, no re-encode); ids never change.
- Nothing persists across a restart but the files; a take running at shutdown is saved by :meth:`stop`.

The API (api.py; the page reaches it through room_service's /show pass-through): ``GET /api/v1/piano/recordings`` ->
:meth:`view`, ``POST /api/v1/piano/record {"on"}`` -> :meth:`record`, ``PATCH /api/v1/piano/recordings/{id}
{"title"?, "composer"?, "genre"?}`` -> :meth:`edit`, ``DELETE`` -> :meth:`delete`, ``GET .../{id}.mid`` -> :meth:`file_of`. Playback is the
test piano's: ``POST /api/v1/test {"source": "piano", "piece": "rec:<id>"}`` (sources.py reads this folder through
:func:`recordings_folder` and :func:`read_meta`).
"""
from __future__ import annotations

import asyncio
import collections
import datetime
import io
import json
import logging
import math
import os
import pathlib
import re
import secrets
import time
from typing import Any, Callable, Optional

from . import contract, vban

log = logging.getLogger("show.recorder")

RECORDINGS_DIR = pathlib.Path.home() / ".local" / "share" / "screen-machine" / "piano-recordings"
PPQ = 960
TEMPO_US = 500_000                     # 120 bpm
TICKS_PER_S = PPQ * 1_000_000.0 / TEMPO_US
LEAD_IN_S = 0.5                        # leading silence kept before the first event
TAIL_S = 1.0                           # end-of-track after the last event
HOT_REPEAT_S = 0.25                    # midi.py's hot-note cap (Piano.hot_repeat_s / hot_jump)
HOT_JUMP = 32
PEDAL_CC = (64, 66, 67)
ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")
TITLE_MAX = 120
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
#: the test source that plays MIDI (its show.yaml ``tests.<source>`` block names the folder); no name in code
MIDI_SOURCE = next(s for s, p in contract.TEST_SOURCE_PLAYERS.items() if p["kind"] == "midi")


def _is_test(stream: str) -> bool:
    return stream in contract.TEST_STREAMS or stream.startswith("test-")


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# -- config (pure) ------------------------------------------------------------------------------------------------------
def recordings_folder(settings: Any, default: pathlib.Path = RECORDINGS_DIR) -> pathlib.Path:
    """show.yaml ``tests.piano.recordings`` (~ expanded; relative: under the repo), else ``default``."""
    tests = settings.get("tests") if isinstance(settings, dict) else None
    piano = tests.get(MIDI_SOURCE) if isinstance(tests, dict) else None
    raw = piano.get("recordings") if isinstance(piano, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return pathlib.Path(default)
    p = pathlib.Path(raw.strip()).expanduser()
    return p if p.is_absolute() else contract.REPO_ROOT / p


def midi_section(settings: Any) -> tuple[Optional[frozenset], Optional[frozenset]]:
    """(streams, channels) from show.yaml ``situations.midi``: the live streams listed (test ones dropped; None = every
    non-test stream) and the channels 1-16 (None = all)."""
    sit = settings.get("situations") if isinstance(settings, dict) else None
    sec = sit.get("midi") if isinstance(sit, dict) else None
    sec = sec if isinstance(sec, dict) else {}
    streams = sec.get("streams")
    st = None
    if isinstance(streams, list) and streams:
        st = frozenset(str(s) for s in streams if isinstance(s, str) and s and not _is_test(s))
    ch = sec.get("channels")
    chans = None
    if isinstance(ch, list):
        got = frozenset(int(c) for c in ch if isinstance(c, int) and not isinstance(c, bool) and 1 <= c <= 16)
        chans = got or None
    return st, chans


def sender_of(settings: Any, stream: str) -> Optional[str]:
    """The show.yaml sender whose ``streams`` name ``stream`` (None if none does)."""
    senders = settings.get("senders") if isinstance(settings, dict) else None
    if not isinstance(senders, dict):
        return None
    for name, spec in senders.items():
        streams = spec.get("streams") if isinstance(spec, dict) else None
        if isinstance(streams, str):
            streams = [streams]
        if isinstance(streams, list) and stream in streams:
            return str(name)
    return None


def take_title(wall_s: float) -> str:
    """"Take - Thu 22 Oct 14:05" (local time)."""
    dt = datetime.datetime.fromtimestamp(wall_s)
    return f"Take - {dt:%a} {dt.day} {dt:%b} {dt:%H:%M}"


def clean_title(title: Any) -> str:
    """A title as the page sent it, tidied (spaces collapsed); ValueError("bad_title") if nothing is left or it is
    longer than TITLE_MAX."""
    if not isinstance(title, str):
        raise ValueError("bad_title: a title is text")
    t = " ".join(_CTRL.sub(" ", title).split())
    if not t or len(t) > TITLE_MAX:
        raise ValueError(f"bad_title: a title has 1-{TITLE_MAX} characters")
    return t


def file_name(title: str) -> str:
    """An ASCII file name for a download ("Take - Thu 22 Oct 14_05.mid")."""
    safe = re.sub(r"[^A-Za-z0-9 ._()\-]", "_", title).strip(" ._") or "recording"
    return safe[:100] + ".mid"


# -- the file (pure) ----------------------------------------------------------------------------------------------------
def _latin1(text: str) -> str:
    return text.encode("latin-1", "replace").decode("latin-1")


def capped(events: list) -> list:
    """midi.py's hot-repeat cap over (t, order, status, d1, d2) events in time order: a note-on within HOT_REPEAT_S of
    the same key's previous one is at most its (capped) velocity + HOT_JUMP. Returns new tuples."""
    last: dict[int, tuple[float, int]] = {}
    out = []
    for e in events:
        t, o, status, d1, d2 = e
        if status & 0xF0 == 0x90 and d2 > 0:
            prev = last.get(d1)
            if prev is not None and 0.0 <= t - prev[0] < HOT_REPEAT_S and d2 > prev[1] + HOT_JUMP:
                d2 = prev[1] + HOT_JUMP
            last[d1] = (t, d2)
            e = (t, o, status, d1, d2)
        out.append(e)
    return out


def encode_smf(events: list, title: str, t0: float, t_end: float) -> bytes:
    """SMF type 0 bytes (mido): track name, tempo, then ``events`` (t, order, status, d1, d2) in time order with
    times relative to ``t0`` (host seconds), end-of-track at ``t_end``."""
    import mido                                          # pinned in show_service/requirements.txt

    mid = mido.MidiFile(type=0, ticks_per_beat=PPQ)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tr.append(mido.MetaMessage("track_name", name=_latin1(title), time=0))
    tr.append(mido.MetaMessage("set_tempo", tempo=TEMPO_US, time=0))
    prev = 0
    for t, _o, status, d1, d2 in events:
        tick = max(prev, int(round((t - t0) * TICKS_PER_S)))
        kind, ch = status & 0xF0, status & 0x0F
        if kind == 0x90:
            msg = mido.Message("note_on", channel=ch, note=d1, velocity=d2, time=tick - prev)
        elif kind == 0x80:
            msg = mido.Message("note_off", channel=ch, note=d1, velocity=d2, time=tick - prev)
        else:
            msg = mido.Message("control_change", channel=ch, control=d1, value=d2, time=tick - prev)
        tr.append(msg)
        prev = tick
    end = max(prev, int(round((t_end - t0) * TICKS_PER_S)))
    tr.append(mido.MetaMessage("end_of_track", time=end - prev))
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def _varlen(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def retitle_smf(data: bytes, title: str) -> Optional[bytes]:
    """``data`` (as encode_smf wrote it: one track whose first event is the track-name meta at delta 0) with the
    track name replaced; None if it isn't laid out that way (the file is left as it is)."""
    if len(data) < 26 or data[:4] != b"MThd" or data[14:18] != b"MTrk" or data[22:25] != b"\x00\xff\x03":
        return None
    i, n = 25, 0
    while i < len(data):                                 # the name's variable-length size
        b = data[i]
        n = (n << 7) | (b & 0x7F)
        i += 1
        if not b & 0x80:
            break
    else:
        return None
    end = i + n
    if end > len(data):
        return None
    name = _latin1(title).encode("latin-1")
    body = b"\x00\xff\x03" + _varlen(len(name)) + name + data[end:]
    return data[:18] + len(body).to_bytes(4, "big") + body


def _atomic_write(path: pathlib.Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_meta(folder: pathlib.Path, rid: str) -> Optional[dict]:
    """A recording's JSON (None: no such id, or its .mid is gone). Any thread."""
    if not isinstance(rid, str) or not ID_RE.match(rid):
        return None
    try:
        meta = json.loads((pathlib.Path(folder) / f"{rid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or not (pathlib.Path(folder) / f"{rid}.mid").is_file():
        return None
    return meta


def item(meta: dict) -> dict:
    """The contract's row: {id, title, composer, genre, recorded_at, duration_s, notes} (composer and genre: what the
    player set, else null)."""
    return {"id": meta.get("id"), "title": meta.get("title"), "composer": meta.get("composer"),
            "genre": meta.get("genre"), "recorded_at": meta.get("recorded_at"),
            "duration_s": meta.get("duration_s"), "notes": meta.get("notes")}


def clean_detail(value: Any, what: str) -> Optional[str]:
    """A composer or genre as the page sent it: tidied text, or None (empty or null clears it). ValueError past
    TITLE_MAX."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"bad_{what}: a {what} is text")
    t = " ".join(_CTRL.sub(" ", value).split())
    if len(t) > TITLE_MAX:
        raise ValueError(f"bad_{what}: a {what} has at most {TITLE_MAX} characters")
    return t or None


# -- the take -----------------------------------------------------------------------------------------------------------
class _Wire:
    """One stream's wire session inside a take."""
    __slots__ = ("seq", "sess", "off", "last_s")

    def __init__(self, sess: Optional[str], off: float) -> None:
        self.seq: Optional[int] = None
        self.sess = sess
        self.off = off                                   # host s - sender s, from the session's first packet
        self.last_s = -math.inf                          # its newest event or state time (host s)


class _Take:
    def __init__(self, t0_host: float, wall0: float, accept: Optional[frozenset],
                 channels: Optional[frozenset]) -> None:
        self.q: collections.deque = collections.deque()  # show-rx appends, show-brain pops
        self.t0_host, self.wall0 = t0_host, wall0
        self.accept, self.channels = accept, channels
        self.events: list[tuple[float, int, int, int, int]] = []
        self.held: dict[tuple[str, int, int], float] = {}     # (stream, channel 0-15, pitch) -> its note-on time
        self.pedals: dict[tuple[str, int, int], int] = {}     # (stream, channel 0-15, cc) -> raw value
        self.wires: dict[str, _Wire] = {}
        self.per_stream: dict[str, int] = {}
        self.n = 0
        self.notes = self.lost = self.healed = self.dupes = self.sessions = self.closed = self.early = 0


class _Job:
    """A finished take, ready to write (built on show-brain, written anywhere)."""

    def __init__(self, rid: str, title: str, meta: dict, events: list, t0: float, t_end: float,
                 folder: pathlib.Path) -> None:
        self.rid, self.title, self.meta, self.events = rid, title, meta, events
        self.t0, self.t_end, self.folder = t0, t_end, folder


class Recorder:
    """Component ``recorder`` (tick 1 Hz) and the show-rx tap (module docstring)."""

    name = "recorder"
    tick_hz = contract.SLOW_TICK_HZ

    def __init__(self, ctx: Optional[contract.BrainContext] = None, *, folder: Optional[pathlib.Path] = None,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        """``folder``: the default folder (show.yaml ``tests.piano.recordings`` still wins); tests pass a tmp one."""
        self.ctx = ctx
        self.clock, self.wall = clock, wall
        self._default_folder = pathlib.Path(folder) if folder is not None else RECORDINGS_DIR
        self.folder = self._default_folder
        self.settings: dict = {}
        self._streams: Optional[frozenset] = None
        self._channels: Optional[frozenset] = None
        self._take: Optional[_Take] = None               # read by show-rx (one attribute), swapped on show-brain
        self._list: Optional[tuple[Any, list[dict]]] = None   # (folder stamp, rows newest first)
        self._last: Optional[dict] = None
        self._errors: list[str] = []
        self._saving = 0
        self._snap: dict = {}
        self._rebuild()

    # -- show-rx ---------------------------------------------------------------------------------------------------
    def on_midi(self, stream: str, msg: Any, arrival_s: float, to_host: Callable[[int], float]) -> None:
        """contract.OnMidi (show-rx): nothing unless a take runs; then one append."""
        take = self._take
        if take is None:
            return
        acc = take.accept
        if acc is None:
            if _is_test(stream):
                return
        elif stream not in acc:
            return
        ev = getattr(msg, "ev", None)
        t_ref = ev[0][0] if ev else getattr(msg, "t_ns", None)
        if t_ref is None:
            return
        t = to_host(t_ref)
        if not _finite(t):
            t = arrival_s
        take.q.append((stream, msg, t - t_ref * 1e-9))

    # -- Component -------------------------------------------------------------------------------------------------
    def provides(self) -> dict[str, Any]:
        return {}

    def configure(self, settings: dict) -> None:
        self.settings = settings if isinstance(settings, dict) else {}
        self._streams, self._channels = midi_section(self.settings)
        folder = recordings_folder(self.settings, self._default_folder)
        if folder != self.folder:
            self.folder, self._list = folder, None
        self._rebuild()

    def restore(self, saved: dict) -> None:
        """Nothing persists but the files."""

    def start(self) -> None:
        self._rebuild()

    def tick(self, now_s: float) -> None:
        take = self._take
        if take is not None:
            self._drain(take)
        self._rebuild()

    def snapshot(self) -> dict:
        return self._snap

    def persist(self) -> Optional[dict]:
        return None

    def stop(self) -> None:
        """Shutdown: a take still running is saved here (inline)."""
        take, self._take = self._take, None
        if take is not None:
            try:
                job = self._finish(take, self.clock())
                if job is not None:
                    self._write(job)
                    log.info("recording %s saved at shutdown (%d notes)", job.rid, job.meta["notes"])
            except Exception:                            # noqa: BLE001
                log.exception("the take running at shutdown couldn't be saved")
        self._rebuild()

    # -- API (show-brain) ------------------------------------------------------------------------------------------
    def view(self) -> dict:
        """GET /api/v1/piano/recordings."""
        take = self._take
        if take is not None:
            self._drain(take)
            self._rebuild()
        return {"recording": self._state(take), "recordings": self.recordings()}

    def record(self, on: bool) -> Any:
        """POST /api/v1/piano/record. On: a new take (already recording: the view as it is). Off: the take ends and is
        saved; the view gains ``last`` (the new row, or None for a take with no note, or nothing to stop). On the
        brain loop the save runs in a worker thread and this returns an awaitable."""
        if on:
            if self._take is None:
                self._take = _Take(self.clock(), self.wall(), self._streams, self._channels)
                log.info("recording: started")
            self._rebuild()
            return self.view()
        take, self._take = self._take, None
        if take is None:
            return {**self.view(), "last": None}
        job = self._finish(take, self.clock())
        if job is None:
            log.info("recording: stopped, no notes: nothing saved")
            self._rebuild()
            return {**self.view(), "last": None}
        return self._off_thread(self._save_view, job)

    def recordings(self) -> list[dict]:
        """Every saved recording, newest first (contract rows)."""
        return [item(m) for m in self._metas()]

    def rename(self, rid: str, title: Any) -> Any:
        """PATCH /api/v1/piano/recordings/{id} {"title"} -> the row. LookupError("unknown_recording"), ValueError
        ("bad_title")."""
        return self.edit(rid, {"title": title})

    def edit(self, rid: str, changes: dict) -> Any:
        """PATCH /api/v1/piano/recordings/{id} {"title"?, "composer"?, "genre"?} -> the row (owner, 23 Oct: name a
        take and say whose piece it is, so its score prints them).  A named null or "" clears composer or genre.
        LookupError("unknown_recording"), ValueError("bad_title" ...)."""
        ch: dict = {}
        if "title" in changes:
            ch["title"] = clean_title(changes["title"])
        for k in ("composer", "genre"):
            if k in changes:
                ch[k] = clean_detail(changes[k], k)
        if read_meta(self.folder, rid) is None:
            raise LookupError("unknown_recording")
        return self._off_thread(self._edit_files, self.folder, rid, ch)

    def delete(self, rid: str) -> Any:
        """DELETE /api/v1/piano/recordings/{id} -> {"deleted": id}. LookupError("unknown_recording")."""
        if read_meta(self.folder, rid) is None:
            raise LookupError("unknown_recording")
        return self._off_thread(self._delete_files, self.folder, rid)

    def file_of(self, rid: str) -> tuple[pathlib.Path, str]:
        """(the .mid path, its title) for a download. LookupError("unknown_recording")."""
        meta = read_meta(self.folder, rid)
        if meta is None:
            raise LookupError("unknown_recording")
        return self.folder / f"{rid}.mid", str(meta.get("title") or rid)

    # -- the take (show-brain) -------------------------------------------------------------------------------------
    def _state(self, take: Optional[_Take]) -> dict:
        if take is None:
            return {"state": "off", "since_s": None, "notes": 0, "duration_s": 0.0}
        age = round(max(0.0, self.clock() - take.t0_host), 3)
        return {"state": "recording", "since_s": age, "notes": take.notes, "duration_s": age}

    def _drain(self, take: _Take) -> None:
        q = take.q
        while True:
            try:
                stream, msg, off = q.popleft()
            except IndexError:
                return
            try:
                self._feed(take, stream, msg, off)
            except Exception:                            # noqa: BLE001  one bad packet never ends a take
                log.debug("recording: packet skipped", exc_info=True)

    def _feed(self, take: _Take, stream: str, msg: Any, off: float) -> None:
        is_state = isinstance(msg, vban.MidiState)
        w = take.wires.get(stream)
        verdict = vban.midi_seq_verdict(msg.seq, msg.sess, w.seq if w else None, w.sess if w else None,
                                        state=is_state)
        if verdict == "old":
            take.dupes += 1
            return
        if verdict == "restart":
            if w is not None:                            # the piano's sender restarted inside the take
                self._close(take, stream, w.last_s if math.isfinite(w.last_s) else take.t0_host)
            w = _Wire(msg.sess, off)
            take.wires[stream] = w
            take.sessions += 1
        last = w.seq
        if is_state:
            self._state_close(take, stream, w, msg)
            w.seq = msg.seq if last is None else max(last, msg.seq)
            return
        if last is None:                                 # a session's first batch: its prev heals what it covers
            for _s, ev in sorted(msg.prev, key=lambda p: p[0]):
                self._events(take, stream, w, ev)
        elif msg.seq > last + 1:
            healed = 0
            for s, ev in sorted(msg.prev, key=lambda p: p[0]):
                if last < s < msg.seq:
                    self._events(take, stream, w, ev)
                    healed += 1
            take.healed += healed
            take.lost += msg.seq - last - 1 - healed
        self._events(take, stream, w, msg.ev)
        w.seq = msg.seq

    def _add(self, take: _Take, t: float, status: int, d1: int, d2: int) -> None:
        take.n += 1
        take.events.append((t, take.n, status, d1, d2))

    def _events(self, take: _Take, stream: str, w: _Wire, events: list) -> None:
        chans = take.channels
        for t_ns, status, d1, d2 in events:
            t = t_ns * 1e-9 + w.off
            if t > w.last_s:
                w.last_s = t
            ch = status & 0x0F
            if chans is not None and ch + 1 not in chans:
                continue
            kind = status & 0xF0
            if t < take.t0_host:                         # before Record (a first batch's prev)
                take.early += 1
                continue
            if kind == 0x90 and d2 > 0:
                self._add(take, t, 0x90 | ch, d1, d2)
                take.held[(stream, ch, d1)] = t
                take.notes += 1
                take.per_stream[stream] = take.per_stream.get(stream, 0) + 1
            elif kind in (0x80, 0x90):
                if take.held.pop((stream, ch, d1), None) is not None:
                    self._add(take, t, 0x80 | ch, d1, d2 if kind == 0x80 else 0)
            elif kind == 0xB0 and d1 in PEDAL_CC:
                self._add(take, t, 0xB0 | ch, d1, d2)
                take.pedals[(stream, ch, d1)] = d2

    def _state_close(self, take: _Take, stream: str, w: _Wire, st: Any) -> None:
        """Close the notes this stream holds that the state says are up (states never open a note)."""
        t = st.t_ns * 1e-9 + w.off
        if t > w.last_s:
            w.last_s = t
        keys = st.keys
        for key, t_on in list(take.held.items()):
            s, ch, p = key
            if s != stream or not vban.PIANO_LOW <= p < vban.PIANO_LOW + vban.PIANO_KEYS:
                continue
            if keys[p - vban.PIANO_LOW] == 0 and t_on < t:
                del take.held[key]
                self._add(take, max(t, take.t0_host), 0x80 | ch, p, 0)
                take.closed += 1

    def _close(self, take: _Take, stream: Optional[str], t: float) -> None:
        """Note-offs (velocity 0) for every held note and CC 0 for every pedal down (of ``stream``, or all) at t."""
        for key in sorted(k for k in take.held if stream is None or k[0] == stream):
            del take.held[key]
            self._add(take, t, 0x80 | key[1], key[2], 0)
            take.closed += 1
        for key in sorted(k for k, v in take.pedals.items() if (stream is None or k[0] == stream) and v > 0):
            take.pedals[key] = 0
            self._add(take, t, 0xB0 | key[1], key[2], 0)

    def _finish(self, take: _Take, t_stop: float) -> Optional[_Job]:
        """Drain, close what is held at Stop, and lay out the file (None: no note, nothing to save)."""
        self._drain(take)
        last = max((e[0] for e in take.events), default=t_stop)
        self._close(take, None, max(t_stop, last))
        if take.notes <= 0 or not take.events:
            return None
        events = sorted(take.events, key=lambda e: (e[0], e[1]))
        t0 = events[0][0] - LEAD_IN_S
        t_end = events[-1][0] + TAIL_S
        stream = max(take.per_stream, key=lambda s: take.per_stream[s]) if take.per_stream else None
        rid = self._new_id(take.wall0)
        title = take_title(take.wall0)
        wall_start = take.wall0 + (t0 - take.t0_host)
        recorded_at = datetime.datetime.fromtimestamp(take.wall0).astimezone().isoformat(timespec="seconds")
        meta = {"v": 1, "id": rid, "title": title, "recorded_at": recorded_at,
                "duration_s": round(t_end - t0, 3), "notes": take.notes, "events": len(events),
                "host_t0": round(t0, 6), "wall_start": round(wall_start, 6), "stream": stream,
                "streams": sorted(take.per_stream), "sender": sender_of(self.settings, stream) if stream else None,
                "channels": sorted(take.channels) if take.channels is not None else None,
                "sessions": take.sessions, "lost": take.lost, "healed": take.healed, "closed": take.closed,
                "ppq": PPQ, "bpm": 60_000_000 / TEMPO_US}
        log.info("recording: stopped: %s, %d notes, %.1f s (%d lost, %d healed, %d closed)", rid, take.notes,
                 meta["duration_s"], take.lost, take.healed, take.closed)
        return _Job(rid, title, meta, events, t0, t_end, self.folder)

    def _new_id(self, wall0: float) -> str:
        stem = datetime.datetime.fromtimestamp(wall0).strftime("%Y%m%d-%H%M%S")
        for _ in range(64):
            rid = f"{stem}-{secrets.token_hex(2)}"
            if not (self.folder / f"{rid}.json").exists() and not (self.folder / f"{rid}.mid").exists():
                return rid
        return f"{stem}-{secrets.token_hex(2)}"

    # -- files (any thread) ----------------------------------------------------------------------------------------
    @staticmethod
    def _write(job: _Job) -> None:
        data = encode_smf(capped(job.events), job.title, job.t0, job.t_end)
        job.folder.mkdir(parents=True, exist_ok=True)
        _atomic_write(job.folder / f"{job.rid}.mid", data)
        _atomic_write(job.folder / f"{job.rid}.json",
                      (json.dumps(job.meta, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))

    def _save_view(self, job: _Job) -> dict:
        """Write the take, then the view with ``last`` (runs in a worker thread on the brain loop: no brain state is
        touched but the list cache and the counters, each one assignment)."""
        try:
            self._write(job)
            last: Optional[dict] = item(job.meta)
            err = None
        except Exception as e:                           # noqa: BLE001  the page says why
            log.exception("recording %s couldn't be saved", job.rid)
            last, err = None, f"The take couldn't be saved ({type(e).__name__}: {e})."
        self._list = None
        self._last = last
        self._errors = [err] if err else []
        return {"recording": self._state(self._take), "recordings": self.recordings(), "last": last,
                **({"error": err} if err else {})}

    def _edit_files(self, folder: pathlib.Path, rid: str, ch: dict) -> dict:
        meta = read_meta(folder, rid)
        if meta is None:
            raise LookupError("unknown_recording")
        title = ch.get("title")
        if title is not None and title != meta.get("title"):
            mid = folder / f"{rid}.mid"
            try:
                data = retitle_smf(mid.read_bytes(), title)
                if data is not None:
                    _atomic_write(mid, data)
            except OSError:
                log.warning("recording %s: the track name in the .mid wasn't changed", rid, exc_info=True)
        for k, v in ch.items():
            meta[k] = v
        _atomic_write(folder / f"{rid}.json", (json.dumps(meta, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
        self._list = None
        return item(meta)

    def _delete_files(self, folder: pathlib.Path, rid: str) -> dict:
        for ext in (".json", ".mid"):                    # the JSON first: a half-deleted take is never listed
            try:
                (folder / f"{rid}{ext}").unlink()
            except FileNotFoundError:
                pass
        self._list = None
        log.info("recording %s deleted", rid)
        return {"deleted": rid}

    def _off_thread(self, fn: Callable, *args: Any) -> Any:
        """``fn(*args)`` in a worker thread when called on a running loop (an awaitable comes back), else inline."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                return fn(*args)
            finally:
                self._rebuild()

        async def run() -> Any:
            self._saving += 1
            try:
                return await loop.run_in_executor(None, fn, *args)
            finally:
                self._saving -= 1
                self._rebuild()
        return run()

    def _metas(self) -> list[dict]:
        folder = self.folder
        try:
            st = folder.stat()
            stamp: Any = (str(folder), st.st_mtime_ns)
        except OSError:
            return []
        cached = self._list
        if cached is not None and cached[0] == stamp:
            return cached[1]
        rows = []
        try:
            names = [p.name for p in folder.iterdir() if p.suffix == ".json" and not p.name.startswith(".")]
        except OSError:
            names = []
        for name in names:
            meta = read_meta(folder, name[:-5])
            if meta is not None and meta.get("id") == name[:-5]:
                rows.append(meta)
        rows.sort(key=lambda m: (str(m.get("id") or "")), reverse=True)
        self._list = (stamp, rows)
        return rows

    def _rebuild(self) -> None:
        take = self._take
        self._snap = {"recording": self._state(take), "folder": str(self.folder),
                      "streams": sorted(self._streams) if self._streams is not None else None,
                      "channels": sorted(self._channels) if self._channels is not None else None,
                      "saving": self._saving, "last": self._last, "errors": list(self._errors),
                      "take": None if take is None else {
                          "events": len(take.events), "held": len(take.held), "queued": len(take.q),
                          "lost": take.lost, "healed": take.healed, "dupes": take.dupes, "sessions": take.sessions,
                          "early": take.early}}


def attach(ctx: contract.BrainContext, **kw: Any) -> Recorder:
    """The brain's ``recorder``. ``__main__`` also feeds it from on_midi (its show-rx tap)."""
    return Recorder(ctx, **kw)
