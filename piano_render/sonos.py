"""Play a rendered test-piano file, or a stream rendered as it plays, on a Sonos zone through room_service, and watch
it (session.py --output sonos:<zone>).

    out = SonosOutput("drawing-room", emit=print)
    t_audio0 = out.start(render_flac, lead_in_ms=750, title="Piano: Nocturne",
                         url="http://<SM_LAN_HOST>:8700/show/api/v1/piano/audio/<key>.flac")
    t_audio0 = out.start(streamer, lead_in_ms=750, title="Piano: Nocturne",      # a stream (Streams, below)
                         url="http://<SM_LAN_HOST>:8700/show")
    out.wait(); out.session.done / out.session.error
    out.stop()                  # pauses the Sonos only while it still plays our file; settle() waits for a late one

Device control goes through room_service (screen-machine-ff's; AV_PROGRAMME §11): the Sonos state is read from
``GET <ROOM_URL>/api/v1/status`` and playback is asked for with ``PUT <ROOM_URL>/api/v1/room/targets``. The player
itself is only *read* here: UPnP GetTransportInfo + GetPositionInfo on the zone's ip (config/room/sonos.json) at
WATCH_HZ, plain HTTP SOAP to UPNP_PORT (Sonos's fixed control port).

start():
  1. The room's state: the zone's volume and now_playing (state, track_id), and the volume the room stored to put back
     (status ``stored``: the "_restore" slot first, then any other); whether it is PLAYING also from UPnP (fresher).
  2. The volume: the one asked (0..100), else the current one if > 0, else the stored put-back value; else 0 and
     ``{"ev": "warning", "why": "Sonos volume is 0"}`` (never an invented level). A volume nobody knows (no reading,
     nothing stored) is left out of the PUT (the Sonos keeps its own), with a warning.
  3. The pad (silence before MIDI time 0): ``lead_in_ms`` (show_sender's --pad-s); while room_service doesn't take
     ``fade_s`` (below) at least ramp_s + FADE_PAD_EXTRA_S, so the fade-in its hand-set applies to every play
     (sonos.json "ramp_s", else RAMP_S_DEFAULT 4 s, from volume 0) covers silence only.
  4. The padded copy: ``<cache>/sonos/<pkey>.flac`` = pad of digital silence, then the render, same rate, channels and
     sample format (FLAC at compression level 0), pkey = md5("<key>:pad<ms>"): made once (blockwise, in a thread at
     nice 19), reused (and touched), the newest PADDED_KEEP kept. The URL's last segment "<key>.flac" becomes
     "<pkey>.flac" (show_service serves both: api.py ``/api/v1/piano/audio/``). A URL that doesn't name the render is
     played as it is, with no pad.
  5. ``PUT {"values": {"sonos.<zone>.playback": {"uri", "title", "fade_s": 0, "volume"}}, "source": "test piano"}``.
     Feature detection: room_service's Sonos adapter rejects keys it doesn't take with a 400 naming them ("unknown
     keys [...]"); OPTIONAL_KEYS it names are dropped for the rest of this process (UNSUPPORTED) and the PUT is sent
     again (with the fade pad when fade_s went). Any other refusal is an error.
  6. ``t_audio0 = t_put + start_ms/1000 + pad`` (+ ramp_s when fade_s is unsupported and the Sonos was playing: the
     hand-set fades that out first). t_put: just before the PUT that played. start_ms: config/room/latency.json
     ``outputs["sonos:<zone>"].start_ms`` (screen-machine-10's file, read only), else START_MS_DEFAULT (500: PUT ->
     file t=0 at the room mic, 0.499 and 0.498 s on 21 Oct, from PAUSED_PLAYBACK).
The watch (a thread per play, WATCH_HZ): our URI must be PLAYING within START_TIMEOUT_S (other URIs before it are the
old music fading out), else "Sonos didn't start". Then STOPPED near the file's end (the last RelTime, or the clock,
within END_SLACK_S of it): played; STOPPED earlier: "Sonos stopped"; PAUSED_PLAYBACK: "Sonos paused"; another TrackURI:
"Sonos is playing something else". RelTime is whole seconds: each second edge brackets the file's start between two
polls; the brackets of the last EDGES_KEPT edges are intersected (a jump or an empty intersection starts again), and
once MIN_EDGES agree, a start that differs from t_audio0 by more than REBASE_S is sent as ``{"ev": "rebase",
"t_audio0", "was"}`` (show_sender shifts its MIDI clock). A transport that can't be read for UNREADABLE_S: the end is
timed by the clock (a warning says so).
stop(): if the Sonos still plays our URI, PUT playback "paused" through room_service, once per play (room_service fades
it out over ramp_s first, and a second pause mid-fade would put back the faded volume); never otherwise. A stop during
a start that has PUT but isn't heard yet watches for our URI (until it is due plus START_GRACE_S) and pauses it then.

Streams (start's first argument not a path: piano_render.stream.Streamer, a callable f(position), or an iterable of
(n, ch) float blocks; owner 22 Oct: render on demand, nothing kept):
  * Mp3Stream: digital silence for the pad, then the source's blocks, as f32le into ffmpeg (libmp3lame, MP3_BITRATE
    CBR; ~4.5% of a core, plus ~0.3 s and 1.5 CPU-s to start), written as it is made to
    ``<streams dir>/<id>.mp3`` (``$PIANO_STREAM_DIR``, else ~/.local/state/screen-machine/piano/streams; id = 32 hex);
    ``<id>.done`` once it ends (ffmpeg has written everything). Both are deleted STREAM_KEEP_S (10 min) after the
    end (a detached sleeper, plus a sweep of old ones at every new stream). Nothing else is written.
  * Pacing: a source with blocks(position) / f(position) is given the stream's position(): the wall clock since its
    first block, in frames. That is an early estimate of the Sonos's own position (it starts playing later), so the
    source's ahead_s (make the Streamer with ahead_s=STREAM_AHEAD_S, 4 s) is the least the Sonos's buffer gets; a
    smaller one is logged. A plain iterable is paced here, STREAM_AHEAD_S ahead of the same clock. position() raises
    pc22.Halted after a stop.
  * show_service serves the growing file at ``GET /api/v1/piano/stream/<id>.mp3`` (api.py), following it to
    ``<id>.done``; the Sonos fetches it through room_service's :8700/show door.
  * The URI is ``x-rincon-mp3radio://<host:port>/show/api/v1/piano/stream/<id>.mp3`` (STREAM_SCHEME "radio": an
    internet-radio stream to the Sonos, of no set length, never seeked, so no Range or Content-Length is needed; Sonos
    stopped taking plain http: for radio streams in 6.4.2, see SoCo's play_uri). "http" plays it as http:// instead
    (a track of unknown length) and is there to try if a Sonos ever refuses the radio form. The PUT always carries a
    title (STREAM_TITLE at least): a radio URI needs one.
  * The PUT goes once STREAM_PUT_AFTER_S of audio is in the file (or the stream has ended), so the Sonos's first
    read finds its buffer's worth. t_audio0 = t_put + stream_start_ms/1000 + pad + MP3_DELAY_FRAMES/sr (+ the
    fade-out, as for files). stream_start_ms: SonosOutput's (STREAM_START_MS_DEFAULT, a guess): piano_render/
    calibrate.py measures the true delay after it with the room mic and moves the MIDI clock by that, so it is
    not kept here.
  * The watch: as for files, with these differences. Our URI is matched on TrackURI, mp3radio:// and http:// alike,
    else on GetMediaInfo's CurrentURI (read-only). RelTime edges say when MIDI time 0 left the Sonos's position
    counter (done "rel_start_ms", against t_audio0); the room hears it STREAM_HEARD_AFTER_REL_MS later. With
    STREAM_REBASE, once the edges pin it within STREAM_REL_WIDTH_S, the play emits ``{"ev": "heard", "t"}`` (and
    again if it moves by STREAM_REL_MOVE_S): calibrate.py's sink_emit moves the MIDI clock there unless its mic has
    measured this play. Checked against the mic on 26 Oct (timing clicks, 3 plays): the start estimate alone was off
    by -230..-10 ms from one play to the next (heard 834-1050 ms after it), RelTime + 66 ms within +-31 ms. The end is known once the stream has ended: STOPPED near it is "played"; still PLAYING our URI
    STREAM_END_SLACK_S after it, the play is over (done "by": "stream") and the Sonos is paused (once), so it never
    sits on, or re-fetches, a finished stream. A stream that failed (the source raised, ffmpeg died) ends there and
    the play's error says so. done also has "seen_ms" (PUT -> our URI PLAYING) for calibration.
  * stop(): as for files, and the stream stops (its render ends; the file is closed and marked done).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import unquote, urlsplit

log = logging.getLogger("piano_render.sonos")

REPO = Path(__file__).resolve().parent.parent
ROOM_URL = "http://127.0.0.1:8700"                 # room_service on this box (loopback is an allowed Host)
SONOS_FILE = REPO / "config" / "room" / "sonos.json"
LATENCY_FILE = REPO / "config" / "room" / "latency.json"
SOURCE = "test piano"                              # the PUT's source (room_service's log)
UPNP_PORT = 1400
AVT = ("/MediaRenderer/AVTransport/Control", "urn:schemas-upnp-org:service:AVTransport:1")
START_MS_DEFAULT = 500.0
RAMP_S_DEFAULT = 4.0                               # room_service's Sonos adapter when sonos.json has no ramp_s
FADE_PAD_EXTRA_S = 0.5
OPTIONAL_KEYS = ("fade_s", "title")                # playback keys an older room_service doesn't take
UNSUPPORTED: set = set()                           # of OPTIONAL_KEYS, refused by room_service in this process
RESTORE_SLOT = "_restore"                          # room_service's "put back afterwards" slot
PLAYING = ("PLAYING", "TRANSITIONING")
WATCH_HZ = 5.0
START_TIMEOUT_S = 30.0                             # a hand-set may wait 15 s for a running switch, then fade 4 + 4 s
START_GRACE_S = 5.0
END_SLACK_S = 2.5
REBASE_S = 0.7
MIN_EDGES = 2
EDGES_KEPT = 8
UNREADABLE_S = 5.0
HTTP_TIMEOUT_S = 10.0
UPNP_TIMEOUT_S = 2.0
PADDED_DIR = "sonos"
PADDED_KEEP = 12                                   # ~35 MB each for a typical piece
PAD_BLOCK_S = 5.0
KEY_RE = re.compile(r"^[0-9a-f]{32}$")
# -- streams (module docstring, Streams) --
STREAM_DIR_ENV = "PIANO_STREAM_DIR"                 # show_service's api.py reads the same
STREAM_DIR = Path("~/.local/state/screen-machine/piano/streams")
STREAM_ROUTE = "/api/v1/piano/stream/{id}.mp3"      # show_service's, under room's /show door
RADIO_SCHEME = "x-rincon-mp3radio"
STREAM_SCHEME = "radio"                             # "radio" (x-rincon-mp3radio://) or "http"
STREAM_TITLE = "Piano"
STREAM_KEEP_S = 600.0                               # a stream's files go this long after it ends
STREAM_AHEAD_S = 4.0                                # the render runs this far ahead of the wall clock from its start
STREAM_PUT_AFTER_S = 2.0                            # audio in the file before the PUT
STREAM_READY_TIMEOUT_S = 20.0
STREAM_START_MS_DEFAULT = 1500.0                    # PUT -> stream t=0 heard: a guess until the room mic measures it
STREAM_START_GRACE_S = 10.0
STREAM_END_SLACK_S = 4.0
STREAM_REBASE = True                                # a radio stream's RelTime edges place the heard start ("heard")
STREAM_HEARD_AFTER_REL_MS = 66.0                    # the room hears MIDI time 0 this long after RelTime puts it (26 Oct)
STREAM_REL_WIDTH_S = 0.08                           # ... once the edges' bracket is this narrow
STREAM_REL_MOVE_S = 0.025                           # ... and again when the estimate moves by more than this
STREAM_RATE = 48000
MP3_BITRATE = "320k"
MP3_DELAY_FRAMES = 1105                             # LAME's 576 + the decoder's 529 (ffmpeg 6.1, measured 23 Oct)
FFMPEG = "ffmpeg"
FFMPEG_EXIT_S = 10.0


class SonosError(Exception):
    """Plain words for the Room page: why the Sonos can't play this."""


def _read_json(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return None
    return float(v)


def start_ms_for(zone: str, latency_file: Path = LATENCY_FILE) -> float:
    """latency.json ``outputs["sonos:<zone>"].start_ms`` (0..20000), else START_MS_DEFAULT."""
    ent = (_read_json(latency_file).get("outputs") or {}).get(f"sonos:{zone}")
    v = _num(ent.get("start_ms")) if isinstance(ent, dict) else None
    return v if v is not None and 0.0 <= v <= 20000.0 else START_MS_DEFAULT


def rel_s(text: Any) -> Optional[int]:
    """UPnP RelTime "H:MM:SS" -> whole seconds (None for NOT_IMPLEMENTED or anything else)."""
    m = re.match(r"^\s*(\d+):(\d{1,2}):(\d{1,2})(?:\.\d+)?\s*$", str(text or ""))
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) if m else None


def _norm_uri(u: Any) -> str:
    s = unquote(str(u or "")).strip()
    if s.lower().startswith(RADIO_SCHEME + "://"):
        s = "http://" + s[len(RADIO_SCHEME) + 3:]       # a radio stream is fetched over plain http
    return s


def same_uri(a: Any, b: Any) -> bool:
    """The same URI, unquoted; x-rincon-mp3radio:// and http:// of the same place count as the same."""
    return bool(a) and bool(b) and _norm_uri(a) == _norm_uri(b)


def streams_dir() -> Path:
    raw = os.environ.get(STREAM_DIR_ENV)
    return Path(raw).expanduser() if raw and raw.strip() else STREAM_DIR.expanduser()


def stream_url(url: str, sid: str, scheme: str = STREAM_SCHEME) -> str:
    """The URI the Sonos plays a stream from: <scheme>://<host:port><door>/api/v1/piano/stream/<sid>.mp3, from the
    url show_sender gives (its .../api/v1/piano/audio/<key>.flac, or just the door, http://<host>:8700/show)."""
    u = urlsplit(str(url or ""))
    if not u.netloc:
        raise SonosError(f"“{url}” isn't an address the Sonos can fetch from")
    i = u.path.find("/api/v1/")
    door = (u.path[:i] if i >= 0 else u.path).rstrip("/")
    head = RADIO_SCHEME if scheme == "radio" else (u.scheme or "http")
    return f"{head}://{u.netloc}{door}{STREAM_ROUTE.format(id=sid)}"


def is_file_source(source: Any) -> bool:
    return isinstance(source, (str, os.PathLike))


STREAM_FILE_RE = re.compile(r"^\.?([0-9a-f]{32})\.(mp3|done|done\.tmp)$")


def sweep_streams(folder: Path, keep_s: float = STREAM_KEEP_S, now: Optional[float] = None) -> list[str]:
    """Delete the streams that ended more than keep_s ago (their .done says when), and those nobody has written to
    for keep_s and that never ended (a writer that died: a live one adds to its file every few ms). Returns the ids
    removed."""
    now = time.time() if now is None else now
    gone: list[str] = []
    try:
        names = list(folder.iterdir())
    except OSError:
        return gone
    for f in names:
        m = STREAM_FILE_RE.match(f.name)
        if not m:
            continue
        sid, kind = m.groups()
        try:
            if now - f.stat().st_mtime <= keep_s:
                continue
        except OSError:
            continue                                     # gone already (its .done took it)
        if kind == "mp3" and (folder / f"{sid}.done").exists():
            continue                                     # ended: its .done's time decides
        for g in ([f, folder / f"{sid}.mp3"] if kind == "done" else [f]):
            try:
                g.unlink()
            except OSError:
                pass
        if sid not in gone:
            gone.append(sid)
    return gone


def _reap_later(paths: list, delay_s: float) -> None:
    """Delete ``paths`` delay_s from now, from a detached sleeper (it outlives this process; nothing is waited on)."""
    try:
        subprocess.run(["sh", "-c", '(sleep "$0"; rm -f -- "$@") </dev/null >/dev/null 2>&1 &',
                        str(max(0, int(round(delay_s)))), *[str(p) for p in paths]],
                       start_new_session=True, timeout=5, check=False, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("sonos: couldn't schedule the stream's deletion (the next stream's sweep will): %s", e)


def _block_array(b: Any):
    import numpy as np
    a = np.asarray(getattr(b, "data", b), dtype=np.float32)
    if a.ndim == 1:
        a = a[:, None]
    if a.ndim != 2 or a.shape[1] < 1:
        raise ValueError(f"a block of shape {a.shape}: want (frames, channels)")
    if a.shape[1] == 1:
        a = np.repeat(a, 2, axis=1)
    return a[:, :2]


class Mp3Stream:
    """One live stream for the Sonos (module docstring, Streams): the pad, then ``source``'s blocks -> ffmpeg ->
    ``path`` as it is made; ``done_path`` once it has ended. start(); wait_ready(s); position(); stop(); ``ended`` (an
    Event), ``frames`` (written, the pad included), ``duration_s`` (frames / sr), ``error`` (plain words or None)."""

    def __init__(self, source: Any, *, pad_s: float = 0.0, sr: Optional[int] = None, folder: Optional[Path] = None,
                 ahead_s: float = STREAM_AHEAD_S, keep_s: float = STREAM_KEEP_S, ffmpeg: str = FFMPEG,
                 bitrate: str = MP3_BITRATE, clock: Callable[[], float] = time.monotonic) -> None:
        from .pc22 import Halted
        self._Halted = Halted
        self.source = source
        self.id = uuid.uuid4().hex
        self.dir = Path(folder) if folder is not None else streams_dir()
        self.path = self.dir / f"{self.id}.mp3"
        self.done_path = self.dir / f"{self.id}.done"
        self.sr = int(sr or getattr(source, "sr", None) or STREAM_RATE)
        self.ahead_s = float(ahead_s)
        self.paced = callable(getattr(source, "blocks", None)) or callable(source)
        own = getattr(source, "ahead_s", None)
        if self.paced and isinstance(own, (int, float)) and own < self.ahead_s:
            log.info("sonos: the stream's render runs %.1f s ahead (%.1f s wanted): the Sonos's buffer gets that plus "
                     "its own start delay", own, self.ahead_s)
        self.pad_frames = max(0, int(round(float(pad_s) * self.sr)))
        self.keep_s, self.ffmpeg, self.bitrate, self.clock = float(keep_s), ffmpeg, bitrate, clock
        self.bytes_per_s = int(str(bitrate).rstrip("kK")) * 1000 / 8.0 if str(bitrate)[:-1].isdigit() else 40000.0
        self.halt = threading.Event()
        self.ended = threading.Event()
        self.frames = 0
        self.error: Optional[str] = None
        self.t_first: Optional[float] = None
        self.t_start = 0.0
        self.proc: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None
        self.stderr_tail: deque = deque(maxlen=10)
        self.stats: dict = {}

    @property
    def duration_s(self) -> float:
        return self.frames / self.sr

    def position(self) -> float:
        """The source's pacing position in frames: the wall clock since its first block (0 until then: loading
        and first-call warm-ups don't count as late; module docstring); pc22.Halted after stop()."""
        if self.halt.is_set():
            raise self._Halted()
        t = self.t_first
        return 0.0 if t is None else (self.clock() - t) * self.sr

    def start(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        sweep_streams(self.dir, self.keep_s)
        cmd = [self.ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-probesize", "32",
               "-analyzeduration", "0", "-f", "f32le", "-ar", str(self.sr), "-ac", "2", "-i", "pipe:0",
               "-map", "0:a", "-c:a", "libmp3lame", "-b:a", str(self.bitrate), "-write_xing", "0",
               "-id3v2_version", "0", "-flush_packets", "1", "-threads", "1", "-f", "mp3", "-y", str(self.path)]
        self.t_start = self.clock()
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE, bufsize=0, start_new_session=True)
        except OSError as e:
            raise SonosError(f"the MP3 encoder (ffmpeg) can't start: {e}") from None
        threading.Thread(target=self._read_err, name="mp3-err", daemon=True).start()
        self.thread = threading.Thread(target=self._run, name="mp3-stream", daemon=True)
        self.thread.start()

    def _read_err(self) -> None:
        for raw in iter(self.proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self.stderr_tail.append(line)
                log.debug("ffmpeg: %s", line)

    def _write(self, a) -> None:
        import numpy as np
        mv = memoryview(np.ascontiguousarray(a, dtype="<f4").tobytes())
        fd = self.proc.stdin.fileno()
        while mv:
            mv = mv[os.write(fd, mv):]
        self.frames += len(a)

    def _iter(self):
        src = self.source
        if callable(getattr(src, "blocks", None)):
            return iter(src.blocks(position=self.position))
        if callable(src):
            return iter(src(self.position))
        return iter(src)

    def _run(self) -> None:
        import numpy as np
        it = None
        c0 = time.process_time()
        try:
            left = self.pad_frames
            while left > 0 and not self.halt.is_set():
                n = min(left, self.sr)
                self._write(np.zeros((n, 2), np.float32))
                left -= n
            it = self._iter()
            for b in it:
                if self.halt.is_set():
                    break
                if self.t_first is None:
                    self.t_first = self.clock()
                a = _block_array(b)
                if not self.paced:                       # a plain iterable: paced here, like a Streamer
                    while not self.halt.is_set():
                        pos = self.position()
                        due = self.frames - self.pad_frames - (pos + self.ahead_s * self.sr)
                        if due <= 0:
                            break
                        self.halt.wait(min(0.05, max(0.002, due / self.sr)))
                    if self.halt.is_set():
                        break
                self._write(a)
        except self._Halted:
            pass
        except OSError as e:                             # the encoder went away
            if not self.halt.is_set():
                self.error = f"the MP3 encoder stopped ({e})"
        except Exception as e:                           # noqa: BLE001  the source broke: the stream ends there
            if not self.halt.is_set():
                self.error = f"{type(e).__name__}: {e}"
                log.warning("sonos: the stream %s failed: %s", self.id, self.error)
        finally:
            close = getattr(it, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:                        # noqa: BLE001
                    pass
            self._close_encoder()
            self.stats.update(frames=self.frames, audio_s=round(self.duration_s, 3),
                              wall_s=round(self.clock() - self.t_start, 3),
                              writer_cpu_s=round(time.process_time() - c0, 3))
            self._mark_done()
            self.ended.set()

    def _close_encoder(self) -> None:
        p = self.proc
        try:
            p.stdin.close()
        except OSError:
            pass
        try:
            p.wait(timeout=FFMPEG_EXIT_S)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        if p.returncode != 0 and self.error is None:
            self.error = "the MP3 encoder failed (rc %s): %s" % (p.returncode,
                                                                " | ".join(list(self.stderr_tail)[-2:]) or "no output")

    def _mark_done(self) -> None:
        doc = {"id": self.id, "frames": self.frames, "duration_s": round(self.duration_s, 3), "error": self.error,
               "t": round(time.time(), 3)}
        tmp = self.dir / f".{self.id}.done.tmp"
        try:
            tmp.write_text(json.dumps(doc), encoding="utf-8")
            os.replace(tmp, self.done_path)
        except OSError as e:
            log.warning("sonos: couldn't mark the stream %s done: %s", self.id, e)
        _reap_later([self.path, self.done_path], self.keep_s)

    def audio_in_file_s(self) -> float:
        try:
            return self.path.stat().st_size / self.bytes_per_s
        except OSError:
            return 0.0

    def wait_ready(self, seconds: float, timeout: float) -> bool:
        """True once ``seconds`` of audio are in the file, or the stream has ended with some; False on timeout."""
        end = self.clock() + timeout
        while self.clock() < end:
            if self.audio_in_file_s() >= seconds or (self.ended.is_set() and self.audio_in_file_s() > 0):
                return True
            if self.ended.is_set():
                return False
            time.sleep(0.02)
        return False

    def stop(self, timeout: float = 1.0) -> None:
        """Ends the stream (the render stops, the file is closed and marked done); waits at most ``timeout``."""
        self.halt.set()
        th = self.thread
        if th is not None and th is not threading.current_thread():
            th.join(timeout)


def unknown_keys(detail: str) -> set:
    """The OPTIONAL_KEYS a 400's detail names as unknown ("playback has unknown keys ['fade_s', 'title']; it takes")."""
    m = re.search(r"unknown keys?\s*\[([^\]]*)\]", detail or "")
    named = set(re.findall(r"[A-Za-z_]+", m.group(1))) if m else set()
    return named & set(OPTIONAL_KEYS)


def padded_name(key: str, pad_s: float) -> str:
    return hashlib.md5(f"{key}:pad{int(round(pad_s * 1000))}".encode()).hexdigest()


def _lower_this_thread() -> None:
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
    except (OSError, AttributeError):
        pass


def padded_copy(src: "str | os.PathLike", pad_s: float, *, keep: int = PADDED_KEEP) -> Path:
    """<src dir>/sonos/<md5(key:pad<ms>)>.flac: pad_s of digital silence, then ``src`` (module docstring, start 4)."""
    import numpy as np
    import soundfile as sf
    src = Path(src)
    d = src.parent / PADDED_DIR
    out = d / f"{padded_name(src.stem, pad_s)}.flac"
    try:
        if out.stat().st_size > 0:
            os.utime(out)
            return out
    except OSError:
        pass
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{out.stem}.{os.getpid()}.{threading.get_ident()}.tmp"
    err: list = []

    def work() -> None:
        _lower_this_thread()
        try:
            with sf.SoundFile(str(src)) as f:
                sr, ch = f.samplerate, f.channels
                with sf.SoundFile(str(tmp), "w", samplerate=sr, channels=ch, subtype=f.subtype, format="FLAC",
                                  compression_level=0.0) as g:
                    left = int(round(pad_s * sr))
                    block = max(1, int(PAD_BLOCK_S * sr))
                    while left > 0:
                        n = min(block, left)
                        g.write(np.zeros((n, ch), dtype=np.int32))
                        left -= n
                    for b in f.blocks(blocksize=block, dtype="int32", always_2d=True):
                        g.write(b)
            os.replace(tmp, out)
        except BaseException as e:                       # noqa: BLE001  said by the caller
            err.append(e)
            try:
                tmp.unlink()
            except OSError:
                pass

    th = threading.Thread(target=work, name="sonos-pad", daemon=True)
    th.start()
    th.join()
    if err:
        raise SonosError(f"couldn't make the padded copy of {src.name}: {err[0]}")
    _prune(d, keep, out)
    return out


def _prune(d: Path, keep: int, spare: Path) -> None:
    try:
        files = sorted((p for p in d.glob("*.flac") if p != spare), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for p in files[max(0, keep - 1):]:
        try:
            p.unlink()
        except OSError:
            pass


class _Play:
    """One start(): what was played and how it went."""

    def __init__(self, url: str, pad_s: float, file_s: float, t_put: float, t_audio0: float, appear_by: float) -> None:
        self.url, self.pad_s, self.file_s, self.t_put, self.t_audio0 = url, pad_s, file_s, t_put, t_audio0
        self.appear_by = appear_by                       # when our URI should be playing at the latest (stop's watch)
        self.seen = False
        self.paused = False                              # we asked room_service to pause it (once: its fade-out
        self.halt = threading.Event()                    # still plays our file, and a second pause would restore the
                                                         # volume it had faded to)
        self.exited = threading.Event()
        self.done: Optional[dict] = None
        self.error: Optional[str] = None
        self.rebased = 0
        self.stream: Optional[Mp3Stream] = None          # a stream's (module docstring, Streams)
        self.track_uri: Optional[str] = None             # a stream's TrackURI when the Sonos reports another one
        self.t_seen: Optional[float] = None              # our URI first seen PLAYING
        self.rel_est: Optional[float] = None             # MIDI time 0 from the RelTime edges (a stream: recorded)
        self.heard_sent: Optional[float] = None          # the last "heard" this play emitted (a stream)


class SonosOutput:
    """PC22Output's shape for session.py: start(path, lead_in_ms, url, title, volume) -> t_audio0; wait(); stop();
    ``session`` (the last play: ``done``, ``error``); ``pad_s`` (the pad the last start used). ``emit`` gets the
    protocol's "warning" and "rebase" lines."""

    def __init__(self, zone: str, *, emit: Optional[Callable[[dict], Any]] = None, room_url: str = ROOM_URL,
                 sonos_file: Path = SONOS_FILE, latency_file: Path = LATENCY_FILE, upnp_port: int = UPNP_PORT,
                 clock: Callable[[], float] = time.monotonic, stream_scheme: str = STREAM_SCHEME,
                 stream_dir: Optional[Path] = None, stream_start_ms: float = STREAM_START_MS_DEFAULT,
                 stream_ahead_s: float = STREAM_AHEAD_S, put_after_s: float = STREAM_PUT_AFTER_S) -> None:
        cfg = _read_json(sonos_file)
        z = (cfg.get("zones") or {}).get(zone)
        if not isinstance(z, dict) or not isinstance(z.get("ip"), str) or not z["ip"].strip():
            raise SonosError(f"there's no Sonos zone “{zone}” in {Path(sonos_file).name}")
        self.zone, self.ip = zone, z["ip"].strip()
        self.room_name = z.get("room") if isinstance(z.get("room"), str) else zone
        ramp = _num(cfg.get("ramp_s"))
        self.ramp_s = ramp if ramp is not None and ramp >= 0 else RAMP_S_DEFAULT
        self.emit = emit or (lambda msg: None)
        self.room_url, self.latency_file, self.upnp_port = room_url.rstrip("/"), Path(latency_file), int(upnp_port)
        self.clock = clock
        self.target = f"sonos.{zone}.playback"
        self.pad_s = 0.0
        self._lock = threading.Lock()
        self._p: Optional[_Play] = None
        self._epoch = 0                                  # bumped by every stop(): a start in flight stops before its PUT
        self._pending: Optional[threading.Thread] = None
        if stream_scheme not in ("radio", "http"):
            raise ValueError(f"stream_scheme {stream_scheme!r}: radio or http")
        self.stream_scheme, self.stream_dir, self.stream_start_ms = stream_scheme, stream_dir, float(stream_start_ms)
        self.stream_ahead_s, self.put_after_s = float(stream_ahead_s), float(put_after_s)

    # -- what the last start learnt (PC22Output's names) --
    @property
    def session(self) -> Optional[_Play]:
        return self._p

    @property
    def done(self) -> Optional[dict]:
        return self._p.done if self._p else None

    @property
    def t_audio0(self) -> Optional[float]:
        return self._p.t_audio0 if self._p else None

    # -- HTTP --
    def _http(self, method: str, path: str, body: Optional[dict] = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.room_url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        except (urllib.error.URLError, OSError) as e:
            raise SonosError(f"room_service isn't answering ({getattr(e, 'reason', e)})") from None
        try:
            doc = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            doc = {}
        return status, doc if isinstance(doc, dict) else {}

    def upnp(self, action: str) -> dict:
        """One read-only AVTransport query (GetTransportInfo | GetPositionInfo | GetMediaInfo) -> its fields."""
        if action not in ("GetTransportInfo", "GetPositionInfo", "GetMediaInfo"):
            raise ValueError(action)
        path, urn = AVT
        env = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
               f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><u:{action} xmlns:u="{urn}">'
               f'<InstanceID>0</InstanceID></u:{action}></s:Body></s:Envelope>')
        req = urllib.request.Request(f"http://{self.ip}:{self.upnp_port}{path}", data=env.encode(), method="POST",
                                     headers={"Content-Type": 'text/xml; charset="utf-8"',
                                              "SOAPACTION": f'"{urn}#{action}"'})
        with urllib.request.urlopen(req, timeout=UPNP_TIMEOUT_S) as r:
            root = ET.fromstring(r.read())
        resp = root.find(".//{%s}%sResponse" % (urn, action))
        if resp is None:
            raise ValueError(f"no {action}Response")
        return {c.tag.split("}")[-1]: (c.text or "") for c in resp}

    def transport(self) -> Optional[tuple[str, str, Optional[int], float, float]]:
        """(state, TrackURI, RelTime s, t before, t after) from UPnP, or None when it can't be read."""
        try:
            state = self.upnp("GetTransportInfo").get("CurrentTransportState", "")
            t1 = self.clock()
            pi = self.upnp("GetPositionInfo")
            t2 = self.clock()
        except Exception as e:                           # noqa: BLE001  unreadable now
            log.debug("sonos %s: UPnP: %s", self.zone, e)
            return None
        return state, pi.get("TrackURI", ""), rel_s(pi.get("RelTime")), t1, t2

    def room_state(self) -> dict:
        """The zone as room_service believes it (start 1): volume, state, track, put_back."""
        status, doc = self._http("GET", "/api/v1/status")
        if status != 200:
            raise SonosError(f"room_service answered {status} for its status")
        sub = (doc.get("subsystems") or {}).get("sonos")
        if not isinstance(sub, dict):
            raise SonosError("room_service has no Sonos")
        believed = sub.get("believed") or {}
        now = believed.get(f"{self.zone}.now_playing")
        now = now if isinstance(now, dict) else {}
        stored = doc.get("stored") if isinstance(doc.get("stored"), dict) else {}
        put_back = None
        for slot in sorted(stored, key=lambda s: s != RESTORE_SLOT):
            e = (stored.get(slot) or {}).get(f"sonos.{self.zone}.volume")
            v = _num(e.get("value")) if isinstance(e, dict) else None
            if v is not None and 0 <= v <= 100:
                put_back = int(round(v))
                break
        vol = _num(believed.get(f"{self.zone}.volume"))
        return {"volume": None if vol is None else int(round(vol)), "state": str(now.get("state") or ""),
                "track": now.get("track_id"), "put_back": put_back, "available": sub.get("available") is not False}

    def choose_volume(self, asked: Any, state: dict) -> Optional[int]:
        """start 2: asked, else the current if > 0, else the stored put-back; else 0 (warning). None: unknown."""
        v = _num(asked)
        if v is not None:
            if not 0 <= v <= 100:
                raise SonosError(f"volume {asked!r} isn't 0..100")
            return int(round(v))
        cur = state.get("volume")
        if cur is not None and cur > 0:
            return cur
        if state.get("put_back") is not None and state["put_back"] > 0:
            return state["put_back"]
        if cur is None:
            self.emit({"ev": "warning", "why": "Sonos volume isn't known: it plays at its own"})
            return None
        self.emit({"ev": "warning", "why": "Sonos volume is 0"})
        return 0

    def _playing_now(self, state: dict) -> bool:
        t = self.transport()
        return (t is not None and t[0] in PLAYING) or state.get("state", "").upper() in PLAYING

    # -- playing --
    def start(self, path, lead_in_ms: float = 0.0, url: Optional[str] = None, title: Optional[str] = None,
              volume: Any = None) -> float:
        """Plays ``path`` (a render) on the zone from ``url``, or a stream (``path`` not a path: module docstring,
        Streams; url: show_sender's, or the door's base); returns t_audio0 (module docstring). SonosError."""
        if not url:
            raise SonosError("no URL to play")
        if not is_file_source(path):
            return self._start_stream(path, lead_in_ms, url, title, volume)
        with self._lock:
            epoch = self._epoch
            old = self._p
        if old is not None:
            old.halt.set()
        state = self.room_state()
        if not state["available"]:
            raise SonosError(f"room_service can't reach the {self.room_name} Sonos now")
        vol = self.choose_volume(volume, state)
        was_playing = self._playing_now(state)
        src = Path(path)
        file_s = self._duration(src)
        while True:
            fade = "fade_s" not in UNSUPPORTED
            pad = max(0.0, float(lead_in_ms) / 1000.0)
            if not fade:
                pad = max(pad, self.ramp_s + FADE_PAD_EXTRA_S)
            play_url, pad = self._padded_url(src, url, pad)
            value: dict = {"uri": play_url}
            if title and "title" not in UNSUPPORTED:
                value["title"] = str(title)
            if fade:
                value["fade_s"] = 0
            if vol is not None:
                value["volume"] = vol
            with self._lock:
                if epoch != self._epoch:
                    raise SonosError("stopped before it played")
            t_put = self.clock()
            status, doc = self._http("PUT", "/api/v1/room/targets", {"values": {self.target: value}, "source": SOURCE})
            if status in (200, 202):
                break
            detail = str(doc.get("detail") or doc.get("error") or "")
            drop = unknown_keys(detail) & set(value) if status == 400 else set()
            if not drop:
                raise SonosError(f"room_service refused the play ({status}: {detail or 'no detail'})")
            UNSUPPORTED.update(drop)
            log.info("sonos %s: room_service doesn't take %s yet: playing without", self.zone, sorted(drop))
        fade_out = self.ramp_s if (not fade and was_playing) else 0.0
        t_audio0 = t_put + start_ms_for(self.zone, self.latency_file) / 1000.0 + pad + fade_out
        appear_by = t_put + fade_out + START_GRACE_S
        p = _Play(play_url, pad, pad + file_s, t_put, t_audio0, appear_by)
        with self._lock:
            self._p, self.pad_s = p, pad
        threading.Thread(target=self._watch, args=(p,), name="sonos-watch", daemon=True).start()
        log.info("sonos %s: %s at volume %s, pad %.2f s%s; t_audio0 in %+.0f ms", self.zone, play_url,
                 vol, pad, f", after a {fade_out:.0f} s fade-out" if fade_out else "",
                 (t_audio0 - self.clock()) * 1000)
        return t_audio0

    def _start_stream(self, source: Any, lead_in_ms: float, url: str, title: Optional[str], volume: Any) -> float:
        with self._lock:
            epoch = self._epoch
            old = self._p
        if old is not None:
            old.halt.set()
            if old.stream is not None:
                old.stream.stop(0.0)
        state = self.room_state()
        if not state["available"]:
            raise SonosError(f"room_service can't reach the {self.room_name} Sonos now")
        vol = self.choose_volume(volume, state)
        was_playing = self._playing_now(state)
        pad = max(0.0, float(lead_in_ms) / 1000.0)
        st = Mp3Stream(source, pad_s=pad, folder=self.stream_dir, ahead_s=self.stream_ahead_s, clock=self.clock)
        play_url = stream_url(url, st.id, self.stream_scheme)
        st.start()
        try:
            if not st.wait_ready(self.put_after_s, STREAM_READY_TIMEOUT_S):
                raise SonosError(f"the piano stream didn't start: {st.error}" if st.error else
                                 f"the piano stream made no sound in {STREAM_READY_TIMEOUT_S:.0f} s")
            while True:
                fade = "fade_s" not in UNSUPPORTED
                value: dict = {"uri": play_url}
                if "title" not in UNSUPPORTED:
                    value["title"] = str(title or STREAM_TITLE)      # a radio URI needs one
                if fade:
                    value["fade_s"] = 0
                if vol is not None:
                    value["volume"] = vol
                with self._lock:
                    if epoch != self._epoch:
                        raise SonosError("stopped before it played")
                t_put = self.clock()
                status, doc = self._http("PUT", "/api/v1/room/targets",
                                         {"values": {self.target: value}, "source": SOURCE})
                if status in (200, 202):
                    break
                detail = str(doc.get("detail") or doc.get("error") or "")
                drop = unknown_keys(detail) & set(value) if status == 400 else set()
                if not drop:
                    raise SonosError(f"room_service refused the play ({status}: {detail or 'no detail'})")
                UNSUPPORTED.update(drop)
                log.info("sonos %s: room_service doesn't take %s yet: playing without", self.zone, sorted(drop))
        except BaseException:
            st.stop(0.5)
            raise
        if not fade:
            self.emit({"ev": "warning", "why": "room_service fades the Sonos in: the start of the piece is quiet"})
        if "title" in UNSUPPORTED:
            self.emit({"ev": "warning", "why": "room_service sends no title: the Sonos may not play the stream"})
        fade_out = self.ramp_s if (not fade and was_playing) else 0.0
        t_audio0 = t_put + self.stream_start_ms / 1000.0 + pad + MP3_DELAY_FRAMES / st.sr + fade_out
        p = _Play(play_url, pad, 0.0, t_put, t_audio0, t_put + fade_out + STREAM_START_GRACE_S)
        p.stream = st
        with self._lock:
            self._p, self.pad_s = p, pad
        threading.Thread(target=self._watch, args=(p,), name="sonos-watch", daemon=True).start()
        log.info("sonos %s: stream %s at volume %s, pad %.2f s, %.1f s in the file at the PUT; t_audio0 in %+.0f ms",
                 self.zone, play_url, vol, pad, st.audio_in_file_s(), (t_audio0 - self.clock()) * 1000)
        return t_audio0

    @staticmethod
    def _duration(src: Path) -> float:
        try:
            import soundfile as sf
            i = sf.info(str(src))
            return float(i.frames) / float(i.samplerate)
        except Exception:                                # noqa: BLE001  the watch then has no file end to go by
            return 0.0

    @staticmethod
    def _padded_url(src: Path, url: str, pad: float) -> tuple[str, float]:
        """The padded copy's URL and the pad (start 4); the URL as it is and no pad when it doesn't name ``src``."""
        head, sep, last = url.rpartition("/")
        if pad <= 0 or not sep or last != f"{src.stem}.flac" or not KEY_RE.match(src.stem):
            if pad > 0:
                log.warning("sonos: %s doesn't name %s: played as it is, with no pad", url, src.name)
            return url, 0.0
        return f"{head}/{padded_copy(src, pad).name}", pad

    def _file_s(self, p: _Play) -> Optional[float]:
        """The played file's length (pad included); a stream's once it has ended, None before."""
        st = p.stream
        if st is None:
            return p.file_s
        return st.duration_s if st.ended.is_set() else None

    def _ours(self, uri: str, p: _Play) -> bool:
        """``uri`` (a TrackURI) is our play's; for a stream also when GetMediaInfo's CurrentURI is (a radio stream's
        TrackURI may differ), remembered for the next polls."""
        if same_uri(uri, p.url) or (p.track_uri is not None and same_uri(uri, p.track_uri)):
            return True
        if p.stream is None:
            return False
        try:
            cur = self.upnp("GetMediaInfo").get("CurrentURI", "")
        except Exception:                                # noqa: BLE001  unreadable now: not ours by this poll
            return False
        if same_uri(cur, p.url) and uri:
            p.track_uri = uri
        return same_uri(cur, p.url)

    def _finished(self, p: _Play, by: str, last_rel: Optional[int]) -> None:
        """The end: p.done, or p.error when the stream failed."""
        st = p.stream
        if st is not None and st.error:
            p.error = f"the piano stream failed: {st.error}"
            return
        p.done = {"by": by, "rel_s": last_rel, "rebased": p.rebased}
        if st is not None:
            p.done.update(stream_s=round(st.duration_s, 3),
                          seen_ms=None if p.t_seen is None else round((p.t_seen - p.t_put) * 1000),
                          rel_start_ms=None if p.rel_est is None else round((p.rel_est - p.t_audio0) * 1000))

    def _watch(self, p: _Play) -> None:
        period = 1.0 / WATCH_HZ
        last_ok = self.clock()
        prev: Optional[tuple[int, float]] = None          # (RelTime s, t before the last poll, which read it)
        edges: list[tuple[float, float]] = []             # brackets of the file's start, (lo, hi)
        last_rel: Optional[int] = None
        warned = False
        stream = p.stream is not None
        try:
            while not p.halt.wait(period):
                now = self.clock()
                file_s = self._file_s(p)
                t = self.transport()
                if t is None:
                    if now - last_ok >= UNREADABLE_S:
                        if not warned:
                            warned = True
                            self.emit({"ev": "warning", "why": "Sonos isn't answering: its end is timed by the clock"})
                        if file_s is not None and now >= p.t_audio0 - p.pad_s + file_s + END_SLACK_S:
                            self._finished(p, "clock", last_rel)
                            return
                    continue
                last_ok = now
                state, uri, rel, t1, t2 = t
                ours = self._ours(uri, p)
                if not p.seen:
                    if ours and state in PLAYING:
                        p.seen, p.t_seen = True, now
                    elif now > p.t_put + START_TIMEOUT_S:
                        p.error = "Sonos didn't start"
                        return
                    else:
                        continue
                if not ours:
                    p.error = "Sonos is playing something else"
                    return
                if state in PLAYING:
                    if rel is not None:
                        if prev is not None and rel == prev[0] + 1:     # the second ticked between the two polls
                            edges = self._edge(p, edges, (prev[1] - rel, t2 - rel))
                        elif prev is not None and rel != prev[0]:
                            edges = []                    # a seek, or a missed second: start the brackets again
                        prev, last_rel = (rel, t1), rel
                    if stream and file_s is not None and now >= p.t_audio0 - p.pad_s + file_s + STREAM_END_SLACK_S:
                        self._finished(p, "stream", last_rel)            # over: never left on a finished stream
                        self._pause(p)
                        return
                    continue
                if file_s is None:
                    near_end = False                      # a stream still being made
                elif stream:
                    near_end = now >= p.t_audio0 - p.pad_s + file_s - END_SLACK_S
                else:
                    end = p.t_audio0 - p.pad_s + file_s
                    near_end = (file_s > 0 and ((last_rel is not None and last_rel >= file_s - END_SLACK_S)
                                                or now >= end - END_SLACK_S))
                if state == "STOPPED" and near_end:
                    self._finished(p, "sonos", last_rel)
                    return
                if state == "STOPPED" and stream and p.stream.error:
                    p.error = f"the piano stream failed: {p.stream.error}"     # it played what there was
                    return
                p.error = "Sonos paused" if state == "PAUSED_PLAYBACK" else "Sonos stopped"
                return
        finally:
            p.exited.set()

    def _edge(self, p: _Play, edges: list, bracket: tuple[float, float]) -> list:
        """One second edge: the file started within ``bracket``; intersect it with the last ones, rebase if needed."""
        edges = (edges + [bracket])[-EDGES_KEPT:]
        lo, hi = max(e[0] for e in edges), min(e[1] for e in edges)
        if lo > hi:                                      # they disagree (a stall or a seek): start again from this one
            edges, (lo, hi) = [bracket], bracket
        if len(edges) >= MIN_EDGES:
            est = (lo + hi) / 2.0 + p.pad_s              # MIDI time 0: the file's start plus the pad
            if p.stream is not None:
                p.rel_est = est + MP3_DELAY_FRAMES / p.stream.sr
                heard = p.rel_est + STREAM_HEARD_AFTER_REL_MS / 1000.0
                if STREAM_REBASE and hi - lo <= STREAM_REL_WIDTH_S and (
                        p.heard_sent is None or abs(heard - p.heard_sent) > STREAM_REL_MOVE_S):
                    p.heard_sent = heard
                    self.emit({"ev": "heard", "t": heard, "width_ms": round((hi - lo) * 1000)})
                return edges
            if abs(est - p.t_audio0) > REBASE_S:
                was, p.t_audio0 = p.t_audio0, est
                p.rebased += 1
                log.info("sonos %s: its position puts MIDI time 0 %+.0f ms from the estimate: rebased", self.zone,
                         (est - was) * 1000)
                self.emit({"ev": "rebase", "t_audio0": est, "was": was})
        return edges

    def wait(self, timeout: Optional[float] = None) -> bool:
        p = self._p
        return True if p is None else p.exited.wait(timeout)

    # -- stopping --
    def ours_playing(self, url: str, p: Optional[_Play] = None) -> bool:
        """The Sonos plays ``url`` now (UPnP, else room_service's now_playing); False when neither can tell. With the
        play ``p`` a stream's other TrackURI counts too (_ours)."""
        t = self.transport()
        if t is not None:
            return t[0] in PLAYING and (same_uri(t[1], url) or (p is not None and p.stream is not None
                                                               and self._ours(t[1], p)))
        try:
            st = self.room_state()
        except SonosError:
            return False
        return st["state"].upper() in PLAYING and same_uri(st["track"], url)

    def _pause(self, p: _Play) -> None:
        with self._lock:
            if p.paused:
                return
            p.paused = True
        try:
            status, doc = self._http("PUT", "/api/v1/room/targets", {"values": {self.target: "paused"},
                                                                     "source": SOURCE})
            if status not in (200, 202):
                log.warning("sonos %s: room_service refused the pause (%s: %s)", self.zone, status, doc.get("detail"))
            else:
                log.info("sonos %s: paused (it was still playing our file)", self.zone)
        except SonosError as e:
            log.warning("sonos %s: couldn't pause: %s", self.zone, e)

    def stop(self, timeout: float = 1.0) -> None:
        """Our file off the Sonos, if it still plays it (module docstring); a stream ends too; safe any time."""
        with self._lock:
            self._epoch += 1
            p = self._p
        if p is None:
            return
        p.halt.set()
        try:
            if p.exited.is_set() and (p.done is not None or p.error is not None):
                return                                   # it ended, or left our file, by itself: nothing of ours plays
            if p.paused:
                return
            if self.ours_playing(p.url, p):
                self._pause(p)
            elif not p.seen and self.clock() < p.appear_by:
                th = threading.Thread(target=self._pause_when_heard, args=(p,), name="sonos-late-pause", daemon=True)
                self._pending = th
                th.start()
        finally:
            if p.stream is not None:
                p.stream.stop(timeout)

    def _pause_when_heard(self, p: _Play) -> None:
        while self.clock() < p.appear_by:
            t = self.transport()
            if t is not None and t[0] in PLAYING and (same_uri(t[1], p.url) or (p.stream is not None
                                                                               and self._ours(t[1], p))):
                self._pause(p)
                return
            time.sleep(1.0 / WATCH_HZ)

    def settle(self, timeout: float = 2.0) -> None:
        """At exit: wait (at most ``timeout``) for a pause that waits for our file to be heard."""
        th = self._pending
        if th is not None:
            th.join(timeout)

    def close(self) -> None:
        self.stop()
