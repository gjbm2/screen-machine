"""The karaoke song's lyrics, word by word, at photon time: what the karaoke effects draw (listens "lyrics").

Owner, 25 Nov: "karaoke effects ... which take the words at the relevant time as input params ... should only play
when there is a lyric stream available (in the same way as the midi effects / scenes only play when there is a midi
stream)".  docs/KARAOKE.md: Flask's karaoke player (routes/karaoke/store.py) owns the clock: a song, a state, and
``pos_s`` at server time ``at``; each line of ``lyrics.json`` carries its words' ``t0``/``t1``.

* :class:`LyricFeed` (one per process, :func:`shared`): a thread (``stream-lyrics``) reads GET /api/karaoke/now once a
  second and, when the song changes, GET /api/karaoke/songs/<id>/lyrics, and publishes an immutable
  :class:`Snapshot` by reference (the render thread never waits on Flask).  Only while the deployment has the
  ``karaoke`` capability; otherwise :func:`shared` is None and nothing polls.
* :class:`LyricView` (one per session's Sound): ``update(photon_s)`` on the render thread, once per frame -> the
  :class:`LyricFrame` plug-ins read as ``self.sound.lyrics``.  The lyric clock is the display page's
  (src/pages/DisplayPage/karaoke/KaraokeLayer.tsx): the song position at the frame's photon time, minus the
  speaker's delay (settings.audio_delay_ms), minus the song's offset_ms + user_offset_ms.  It runs on its own and
  slews toward the server's (a 1 Hz read never makes text jump); more than SNAP_S off (a seek, a restart): it snaps.

Words are known AHEAD (unlike the piano's notes), so an effect may land a word exactly on its ``t0`` or move before it.
"""
from __future__ import annotations

import bisect
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger("stream_service.lyrics")

POLL_S = 1.0
STALE_S = 5.0
TIMEOUT_S = 2.0
SNAP_S = 0.5            # the server's lyric time this far from ours: jump (a seek, a restart), else slew
SLEW_PER_S = 2.0        # the share of the error taken out per second (at 60 fps ~3% a frame)
LINE_LEAD_S = 0.6       # a line is "current" this long before its first word ...
LINE_HOLD_S = 1.2       # ... and stays until this long after its last (or until the next line's lead)
GAP_KEEP_S = 4.0        # a gap between lines shorter than this keeps ``active`` at 1
FADE_OUT_S = 1.5        # a longer gap: ``active`` falls over this after the last word ...
FADE_IN_S = 1.0         # ... and rises over this before the next line's first word
PICS_POLL_S = 5.0       # a song's pictures are read again this often while Flask is still finding them


@dataclass(frozen=True)
class Word:
    text: str
    t0: float
    t1: float
    line: int           # its line's index
    i: int              # its index within the line


@dataclass(frozen=True)
class Line:
    i: int
    text: str
    start: float        # its first word's t0
    end: float          # its last word's t1
    words: tuple[Word, ...]
    chorus: bool        # the same words are sung more than once in the song (the chorus, a hook)
    repeat: bool        # ... and this is not the first time


@dataclass(frozen=True)
class Song:
    key: str
    title: str
    lines: tuple[Line, ...]
    starts: tuple[float, ...] = field(default=(), repr=False)
    words: tuple[Word, ...] = field(default=(), repr=False)      # every word in order
    word_t0: tuple[float, ...] = field(default=(), repr=False)

    def line_at(self, t: float) -> int:
        """The current line's index at lyric time t, or -1 (before the first line's lead / in a long gap after
        a line's hold)."""
        k = bisect.bisect_right(self.starts, t + LINE_LEAD_S) - 1
        if k < 0:
            return -1
        return k if t <= self.lines[k].end + LINE_HOLD_S else -1

    def word_at(self, t: float) -> int:
        """The index (in ``words``) of the last word started by t, or -1."""
        return bisect.bisect_right(self.word_t0, t) - 1

    def active(self, t: float) -> float:
        """0..1: 1 while words are sung or a short gap is between them; down over FADE_OUT_S after the last word
        of a line followed by a long gap (or the song's end), up over FADE_IN_S before the next line."""
        if not self.lines:
            return 0.0
        k = bisect.bisect_right(self.starts, t) - 1
        if k < 0:
            nxt = self.lines[0].start
            return _c01(1.0 - (nxt - t) / FADE_IN_S)
        ln = self.lines[k]
        if t <= ln.end:
            return 1.0
        nxt = self.lines[k + 1].start if k + 1 < len(self.lines) else None
        if nxt is not None and nxt - ln.end <= GAP_KEEP_S:
            return 1.0
        out = _c01(1.0 - (t - ln.end) / FADE_OUT_S)
        inn = _c01(1.0 - (nxt - t) / FADE_IN_S) if nxt is not None else 0.0
        return max(out, inn)


def _c01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _norm(text: str) -> str:
    return " ".join("".join(c for c in w.lower() if c.isalnum() or c == "'") for w in text.split())


def build_song(key: str, title: str, lyr: Any) -> Optional[Song]:
    """lyrics.json ({kind, lines: [{start, end, text, words: [{t0, t1}]}]}) -> a Song, or None (not synced, empty).
    A line without word times spreads its words evenly over start..end."""
    if not isinstance(lyr, dict) or lyr.get("kind") != "synced":
        return None
    raw = [ln for ln in (lyr.get("lines") or []) if isinstance(ln, dict) and str(ln.get("text") or "").strip()]
    counts: dict[str, int] = {}
    for ln in raw:
        n = _norm(str(ln["text"]))
        counts[n] = counts.get(n, 0) + 1
    seen: set[str] = set()
    lines: list[Line] = []
    for ln in raw:
        text = " ".join(str(ln["text"]).split())
        toks = text.split(" ")
        try:
            s = float(ln.get("start"))
            e = float(ln.get("end") if ln.get("end") is not None else s + 0.4 * len(toks))
        except (TypeError, ValueError):
            continue
        times = ln.get("words")
        ws: list[tuple[float, float]] = []
        if isinstance(times, list) and len(times) == len(toks):
            try:
                ws = [(float(w["t0"]), float(w["t1"])) for w in times]
            except (TypeError, ValueError, KeyError):
                ws = []
        if not ws:
            step = max(0.05, (e - s) / max(1, len(toks)))
            ws = [(s + j * step, s + (j + 1) * step) for j in range(len(toks))]
        i = len(lines)
        n = _norm(text)
        words = tuple(Word(tok, t0, max(t0 + 0.05, t1), i, j) for j, (tok, (t0, t1)) in enumerate(zip(toks, ws)))
        lines.append(Line(i, text, words[0].t0, max(w.t1 for w in words), words, counts.get(n, 0) > 1, n in seen))
        seen.add(n)
    if not lines:
        return None
    lines.sort(key=lambda x: x.start)
    lines = [Line(k, x.text, x.start, x.end, tuple(Word(w.text, w.t0, w.t1, k, w.i) for w in x.words), x.chorus,
                  x.repeat) for k, x in enumerate(lines)]
    words = tuple(w for x in lines for w in x.words)
    return Song(key, title, tuple(lines), tuple(x.start for x in lines), words, tuple(w.t0 for w in words))


@dataclass(frozen=True)
class Picture:
    """One of the song's artist pictures (routes/karaoke/pictures.py): a local JPEG, vetted (or the catalogue's own).
    ``face``: (cx, cy, h) of the main faces' box as fractions of the picture (None: unknown)."""
    id: str
    path: str
    w: int
    h: int
    kind: str                # portrait | live | group | cover | other
    quality: float
    face: Optional[tuple]
    era: Optional[str]
    moods: tuple = ()
    appeal: float = 0.5      # how engaging on a big screen (the vetting's score, 0..1)


@dataclass(frozen=True)
class Clip:
    """A short silent clip of the artist live (routes/karaoke/pictures.py fetch_clips): a local MP4, its frames vetted."""
    id: str
    path: str
    dur: float
    appeal: float = 0.5
    crop: Optional[tuple] = None   # (w, h, x, y): the picture inside baked-in black bars


@dataclass(frozen=True)
class PictureSet:
    """The playing song's pictures, best first (the song's era first), and its video clips (most engaging first).
    ``key`` changes when either list does."""
    key: str
    song: str
    era: Optional[str]
    pictures: tuple
    clips: tuple = ()


def _face(faces: Any) -> Optional[tuple]:
    """The box round every main face (the largest few), as (cx, cy, h); None without faces."""
    boxes = []
    for f in faces or []:
        try:
            x, y, w, h = (float(f[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            continue
        if w > 0 and h > 0:
            boxes.append((x, y, x + w, y + h))
    if not boxes:
        return None
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    return (0.5 * (x0 + x1), 0.5 * (y0 + y1), max(0.02, y1 - y0))


def picture_set(sid: str, o: Any) -> Optional[PictureSet]:
    """GET /api/karaoke/songs/<sid>/pictures -> a PictureSet (None: nothing usable)."""
    if not isinstance(o, dict):
        return None
    out = []
    for p in o.get("pictures") or []:
        try:
            out.append(Picture(str(p["id"]), str(p["path"]), int(p["w"]), int(p["h"]), str(p.get("kind") or "other"),
                               float(p.get("quality") if p.get("quality") is not None else 0.7), _face(p.get("faces")),
                               p.get("era") if isinstance(p.get("era"), str) else None,
                               tuple(m for m in p.get("moods") or [] if isinstance(m, str)),
                               float(p.get("appeal") if p.get("appeal") is not None else 0.5)))
        except (KeyError, TypeError, ValueError):
            continue
    clips = []
    for c in o.get("clips") or []:
        try:
            cr = c.get("crop")
            clips.append(Clip(str(c["id"]), str(c["path"]), float(c.get("dur") or 30.0),
                              float(c.get("appeal") if c.get("appeal") is not None else 0.5),
                              tuple(int(v) for v in cr) if isinstance(cr, list) and len(cr) == 4 else None))
        except (KeyError, TypeError, ValueError):
            continue
    if not out and not clips:
        return None
    return PictureSet(f"{sid}:{len(out)}:{out[0].id if out else ''}:{len(clips)}", sid,
                      o.get("era") if isinstance(o.get("era"), str) else None, tuple(out), tuple(clips))


@dataclass(frozen=True)
class Snapshot:
    """What the feed read last (immutable, swapped by reference)."""
    song: Optional[Song]
    state: str               # playing | paused | stopped
    pos_s: float             # the song position at server time ``at``
    at: float
    shift_s: float           # speaker delay + the song's offsets: lyric time = song position - shift_s
    read_mono: float
    pictures: Optional[PictureSet] = None    # the song's artist pictures (the photo scenes; listens "pictures")


class LyricFeed:
    """Flask's karaoke player, read once a second on its own thread."""

    def __init__(self, base_url: str, *, fetch: Optional[Callable[[str], Any]] = None,
                 mono: Callable[[], float] = time.monotonic) -> None:
        self.base = base_url.rstrip("/")
        self._fetch = fetch or _fetch_json
        self._mono = mono
        self.snap: Optional[Snapshot] = None
        self._songs: dict[str, Optional[Song]] = {}      # song id + child mode -> built song (None: no synced lyrics)
        self._failing = False
        self._unloaded: set[str] = set()                 # songs whose lyrics failed to load (logged once each)
        self._pics: dict[str, tuple] = {}                # song id -> (PictureSet | None, read at mono, done)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def poll_once(self) -> None:
        try:
            o = self._fetch(self.base + "/api/karaoke/now")
            if not isinstance(o, dict):
                raise ValueError("not an object")
        except Exception as e:                           # noqa: BLE001 - Flask restarting: next time
            if not self._failing:
                log.info("lyrics: no reply from Flask (%s): no lyrics until it answers", type(e).__name__)
            self._failing = True
            return
        self._failing = False
        p, s = o.get("player") or {}, o.get("song") or {}
        sid = p.get("song_id") if isinstance(p, dict) else None
        state = p.get("state") if isinstance(p, dict) and p.get("state") in ("playing", "paused") and sid else "stopped"
        song = None
        if state != "stopped" and isinstance(s, dict) and s.get("id") == sid:
            child = bool((o.get("settings") or {}).get("child_mode"))
            song = self._song(str(sid), s, child)
        try:
            delay = float((o.get("settings") or {}).get("audio_delay_ms") or 0.0) / 1000.0
            shift = delay + (float(s.get("offset_ms") or 0.0) + float(s.get("user_offset_ms") or 0.0)) / 1000.0
            pos, at = float(p.get("pos_s") or 0.0), float(p.get("at") or time.time())
        except (TypeError, ValueError, AttributeError):
            shift, pos, at = 0.0, 0.0, time.time()
        pics = self._pictures(str(sid)) if state != "stopped" and sid else None
        self.snap = Snapshot(song, state, pos, at, shift, self._mono(), pics)

    def _pictures(self, sid: str) -> Optional[PictureSet]:
        """The song's pictures: read on a new song, then every PICS_POLL_S while Flask is still fetching them."""
        have = self._pics.get(sid)
        now = self._mono()
        if have is not None and (have[2] or now - have[1] < PICS_POLL_S):
            return have[0]
        try:
            o = self._fetch(f"{self.base}/api/karaoke/songs/{urllib.parse.quote(sid)}/pictures")
        except Exception:                                # noqa: BLE001 - an older Flask, or restarting: later
            o = None
        ps = picture_set(sid, o)
        done = isinstance(o, dict) and o.get("state") in ("ready", "none")    # Flask has finished this song's artists
        if len(self._pics) > 64:
            self._pics.clear()
        if ps is not None and (have is None or have[0] is None or have[0].key != ps.key):
            log.info("pictures: %s: %d", sid, len(ps.pictures))
        self._pics[sid] = (ps, now, done)
        return ps

    def pictures(self) -> Optional[PictureSet]:
        """The playing (or paused) song's pictures, if fresh."""
        sn = self.fresh()
        return sn.pictures if sn is not None and sn.state != "stopped" else None

    def _song(self, sid: str, s: dict, child: bool = False) -> Optional[Song]:
        """The song's lyrics as Flask serves them NOW.  Child mode (settings.child_mode; owner, 26 Nov): Flask masks
        each swear word as dashes (same tokens), so the copy is keyed by song AND child mode, fetched again when it
        switches, and the Song's key changes with it (the effects drop what they drew and start again on the masked
        words).  The raw words are never read from anywhere else."""
        key = f"{sid}:child" if child else sid
        if key in self._songs:
            return self._songs[key]
        try:
            lyr = self._fetch(f"{self.base}/api/karaoke/songs/{urllib.parse.quote(sid)}/lyrics")
        except Exception as e:                           # noqa: BLE001 - try again next read (logged once a song)
            if key not in self._unloaded:
                self._unloaded.add(key)
                log.info("lyrics: %s's lyrics didn't load (%s); trying again each second", key, type(e).__name__)
            return None
        title = " — ".join(x for x in (str(s.get("title") or "").strip(), str(s.get("artist") or "").strip()) if x)
        song = build_song(key, title, lyr)
        if len(self._songs) > 64:
            self._songs.clear()
        self._songs[key] = song
        log.info("lyrics: %s: %s", title or sid, f"{len(song.lines)} lines" if song else "no synced lyrics")
        return song

    def fresh(self) -> Optional[Snapshot]:
        sn = self.snap
        return sn if sn is not None and self._mono() - sn.read_mono <= STALE_S else None

    def present(self) -> bool:
        """A song with synced lyrics is playing (the effects' gate: the fx director's heard "lyrics")."""
        sn = self.fresh()
        return bool(sn and sn.song and sn.state == "playing")

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="stream-lyrics", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(POLL_S)


def _fetch_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as r:   # noqa: S310 - loopback, our own Flask
        return json.loads(r.read().decode("utf-8"))


_SHARED: Optional[LyricFeed] = None
_SHARED_DONE = False
_SHARED_LOCK = threading.Lock()


def shared() -> Optional[LyricFeed]:
    """The process's feed, started on first use; None where the deployment has no karaoke (or SM_LYRICS=0)."""
    global _SHARED, _SHARED_DONE
    if _SHARED_DONE:
        return _SHARED
    with _SHARED_LOCK:
        if not _SHARED_DONE:
            _SHARED_DONE = True
            try:
                import smconfig
                if os.environ.get("SM_LYRICS", "1") != "0" and smconfig.enabled("karaoke") is True:
                    _SHARED = LyricFeed(smconfig.url("flask"))
                    _SHARED.start()
            except Exception:                            # noqa: BLE001
                log.exception("lyrics: no feed (the karaoke effects stay off)")
                _SHARED = None
    return _SHARED


APPEAL_GOOD = 0.6       # a picture this engaging counts towards the photo scenes' share (owner, 26 Nov: "not just
                        # because they're pics but because they're engaging photos to look at")


def good_pictures(ps: Optional["PictureSet"]) -> int:
    return sum(1 for p in ps.pictures if p.appeal >= APPEAL_GOOD) if ps is not None else 0


def clips_playing() -> int:
    """How many vetted video clips of the artist the playing karaoke song has (the song_video scenes' gate)."""
    f = shared()
    sn = f.fresh() if f is not None else None
    return len(sn.pictures.clips) if sn is not None and sn.state == "playing" and sn.pictures else 0


def pictures_playing() -> int:
    """How many ENGAGING artist pictures the playing karaoke song has (the scene director's gate and share for the
    photo scenes)."""
    f = shared()
    sn = f.fresh() if f is not None else None
    return good_pictures(sn.pictures) if sn is not None and sn.state == "playing" else 0


# -- per session, on the render thread -----------------------------------------------------------------------------
@dataclass
class LyricFrame:
    """One render frame of lyrics (``self.sound.lyrics``).  Read-only for plug-ins.

    present  a song with synced lyrics is playing (False: draw nothing, pass the frame through)
    t        lyric time (s, song time minus the speaker's delay and the song's offsets) at this frame's photon time
    song     the Song (every line and word, known ahead), or None
    line     the current line's index in song.lines, or -1 (before the first, in a long gap)
    word     the index in song.words of the last word started by t, or -1
    active   0..1, eased: 1 while words are sung (short gaps included); fades in long gaps, at the end and on pause.
             Multiply your amount by it.
    """
    present: bool = False
    t: float = 0.0
    song: Optional[Song] = None
    line: int = -1
    word: int = -1
    active: float = 0.0

    @property
    def current(self) -> Optional[Line]:
        return self.song.lines[self.line] if self.song is not None and self.line >= 0 else None

    def next_line(self) -> Optional[Line]:
        """The line after the current one (or the first coming line when there is no current one)."""
        s = self.song
        if s is None:
            return None
        k = self.line + 1 if self.line >= 0 else bisect.bisect_right(s.starts, self.t)
        return s.lines[k] if 0 <= k < len(s.lines) else None


class LyricView:
    """One session's lyric clock: ``update(photon_s)`` once per frame (monotonic s of the frame's photon time)."""

    def __init__(self, feed: Optional[LyricFeed] = None) -> None:
        self.feed = feed
        self.frame = LyricFrame()
        self._t: Optional[float] = None
        self._key: Optional[str] = None
        self._last: Optional[float] = None
        self._active = 0.0

    def update(self, photon_s: float) -> LyricFrame:
        sn = self.feed.fresh() if self.feed is not None else None
        if sn is None or sn.song is None:
            return self._idle(photon_s)
        wall = time.time() + (photon_s - time.monotonic())
        target = sn.pos_s + (max(0.0, wall - sn.at) if sn.state == "playing" else 0.0) - sn.shift_s
        return self.step(sn.song, target, photon_s, sn.state == "playing")

    def step(self, song: Song, target: float, now_s: float, playing: bool = True) -> LyricFrame:
        """Advance the clock to now_s toward the server's lyric time ``target`` (also the harness's entry)."""
        dt = 0.0 if self._last is None else max(0.0, min(0.25, now_s - self._last))
        self._last = now_s
        if self._t is None or self._key != song.key or abs(target - self._t - (dt if playing else 0.0)) > SNAP_S:
            self._t = target
        else:
            if playing:
                self._t += dt
            self._t += (target - self._t) * min(1.0, SLEW_PER_S * dt)
        self._key = song.key
        t = self._t
        want = song.active(t) if playing else 0.0
        self._active += (want - self._active) * min(1.0, 6.0 * dt) if dt > 0 else 0.0
        f = self.frame = LyricFrame(playing, t, song, song.line_at(t), song.word_at(t), self._active)
        return f

    def _idle(self, now_s: float) -> LyricFrame:
        dt = 0.0 if self._last is None else max(0.0, min(0.25, now_s - self._last))
        self._last = now_s
        self._active *= max(0.0, 1.0 - 6.0 * dt)
        f = self.frame
        if f.song is not None and self._active > 0.01:     # fade the last words out rather than cut them
            self.frame = LyricFrame(False, f.t, f.song, f.line, f.word, self._active)
        else:
            self._t, self._key = None, None
            self.frame = LyricFrame()
        return self.frame


# -- the harness's song ----------------------------------------------------------------------------------------------
DEMO_LINES = (
    ("Turn it up, turn it up, we are golden", 1.0),
    ("Sing it out until the morning comes", 4.6),
    ("Turn it up, turn it up, we are golden", 8.0),
    ("Every voice in the room is a candle", 11.4),
    ("Lights are low and the night is young", 15.0),
    ("Hold the note and let it carry home", 19.0),
)


def demo_song() -> Song:
    """A song for fxtest (stream_service/tools/fxtest.py): six lines, the chorus twice, steady word times."""
    lines = []
    for text, start in DEMO_LINES:
        toks = text.split()
        step = 0.42
        lines.append({"start": start, "end": start + step * len(toks), "text": text,
                      "words": [{"t0": start + j * step, "t1": start + j * step + step * 0.85}
                                for j in range(len(toks))]})
    song = build_song("demo", "Demo", {"kind": "synced", "lines": lines})
    assert song is not None
    return song


__all__ = ["LyricFeed", "LyricView", "LyricFrame", "Song", "Line", "Word", "Snapshot", "build_song", "shared",
           "demo_song"]
