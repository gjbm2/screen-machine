"""The karaoke player's song, for the art director (docs/KARAOKE.md; owner, 25 Nov: "song endings should trigger the
a.d. to switch effects ... we have good metadata that should be fed to the a.d. ... all implemented for midi files").

Flask's karaoke player (routes/karaoke/store.py) owns the clock: a song, a state, and ``pos_s`` at server time ``at``
(both processes on this machine share time.time()). This reads its GET /api/karaoke/now once a second on its own
thread (``show-karaoke``; the brain never waits on Flask) and offers:

* ``now_playing()``: the Music page player's shape ({key, state, pos_s, dur_s, speed, title}), so structure follows the
  song's end exactly as a piece's (armed ahead, track_end at the end, track_change into the next song, via "player")
  and the director names the song. ``__main__`` chains it after the Music page's player: a piece it plays wins.
  The position is the server's clock extrapolated to this moment, not the last read's.
* ``playing()``: a song plays (or the speaker-delay calibration runs): the mic's "Karaoke" want (mic.Mic.add_want),
  so the room is heard while one sings.

* ``clock_override(now_s, body)``: while a song plays and has its beat grid (``beats.json``: tools/karaoke/beats.py,
  Beat This! on the backing track), the show's music.clock is that grid played along the player's position as the
  room hears it (clock.Clock.set_override): locked from the first beat, no octave flips, bars from the file's
  downbeats, phrases counted from the song's sections. No grid, paused, or a long beatless stretch: None (the room
  mic's own clock).
* ``energy_ahead(lookahead_s)``: the song's measured shape (utils/song_shape.py) where the room will be in
  ``lookahead_s``: its intensity 0..1, known ahead, for the director.

Positions: ``pos_s`` is the song position the ROOM hears now (the player's position less the delay to the room,
settings.audio_delay_ms), so the song's end lands as heard; sections are at their audio time (the lyrics' time plus
the song's lyric offsets), snapped to the file's nearest downbeat within half a bar.

Any known song, not only karaoke (owner, 28 Nov: "the same logic (precisely -- same codepaths ...) available for a.d.
processing of sonos tracks ... it shouldn't be specific to karaoke mode"): Flask's GET /api/songs/now is the song the
room plays, the karaoke player's or a Sonos zone's track when it is a ready library song (routes/karaoke/room_song.py),
with ``source`` "karaoke" or "sonos:<zone>". Everything above follows either; only ``playing()`` (the mic's want: the
room is heard while one SINGS) is karaoke's alone. A Sonos position resolves to 1 s, read every 2 s: the song's start
is narrowed across the reads (each says it lies in a 1 s window), and the file grid's phase is pulled onto the room
mic's beats while the mic's clock is locked at the same tempo.

Only while the deployment's ``karaoke`` capability is on (``__main__``). Flask down or a bad reply: None / False
until it answers again (a read older than STALE_S counts as none).
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
import urllib.request
from typing import Any, Callable, Optional

log = logging.getLogger("show.karaoke")

GRID_RETRY_S = 10.0       # a song without beats.json yet (being analysed): look again this often
GRID_LOOK_S = 0.4         # the line is anchored on the beat nearest the room's now + this (consumers read ahead)
GRID_EDGE_S = 2.0         # the grid is published from this long before the song's first beat
GRID_GAP_BEATS = 2.0      # no file beat within this many beats of now (a beatless stretch): the mic's clock
GRID_SEEK_S = 0.5         # the room's position moved this far from where the line had it: a new grid (epoch + 1)
GRID_PHRASE_BARS = 8


class FileGrid:
    """A song's beats.json: beats and downbeats in song seconds, and the beats in a bar."""

    def __init__(self, d: dict) -> None:
        import numpy as np
        self.beats = np.asarray([float(x) for x in d.get("beats") or []], dtype=float)
        if len(self.beats) < 8:
            raise ValueError("too few beats")
        downs = np.asarray([float(x) for x in d.get("downbeats") or []], dtype=float)
        self.meter = int(d.get("meter") or 4) if 2 <= int(d.get("meter") or 4) <= 7 else 4
        self.down_idx = np.unique(np.clip(np.searchsorted(self.beats, downs - 0.035), 0, len(self.beats) - 1)) \
            if len(downs) else np.arange(0, len(self.beats), self.meter)
        self.period = float(np.median(np.diff(self.beats)))
        self.source = f"file:{d.get('model') or 'beats'}"

    def nearest(self, t: float) -> int:
        import numpy as np
        i = int(np.searchsorted(self.beats, t))
        if i <= 0:
            return 0
        if i >= len(self.beats):
            return len(self.beats) - 1
        return i if self.beats[i] - t < t - self.beats[i - 1] else i - 1

    def local_period(self, k: int) -> float:
        import numpy as np
        lo, hi = max(0, k - 3), min(len(self.beats), k + 5)
        d = np.diff(self.beats[lo:hi])
        d = d[(d > 0.5 * self.period) & (d < 1.5 * self.period)]
        return float(np.median(d)) if len(d) else self.period

    def frac_index(self, t: float) -> float:
        """The beat count (float) at song time ``t`` along the file's beats."""
        import numpy as np
        b = self.beats
        if t <= b[0]:
            return (t - b[0]) / self.period
        if t >= b[-1]:
            return len(b) - 1 + (t - b[-1]) / self.period
        i = int(np.searchsorted(b, t)) - 1
        return i + (t - b[i]) / max(1e-6, b[i + 1] - b[i])

    def downbeat_le(self, k: int) -> Optional[int]:
        import numpy as np
        j = int(np.searchsorted(self.down_idx, k, side="right")) - 1
        return int(self.down_idx[j]) if j >= 0 else None

    def snap_downbeat(self, t: float) -> float:
        """``t`` moved to the nearest downbeat when one is within half a bar; else ``t``."""
        if not len(self.down_idx):
            return t
        import numpy as np
        dt = self.beats[self.down_idx]
        j = int(np.argmin(np.abs(dt - t)))
        return float(dt[j]) if abs(dt[j] - t) <= 0.5 * self.meter * self.period else t


def library_grid(song_id: str) -> Optional[FileGrid]:
    """A karaoke library song's beat grid (its beats.json), or None until it has been analysed."""
    import smconfig
    p = smconfig.state("karaoke", "songs", song_id, "beats.json")
    try:
        return FileGrid(json.loads(p.read_text()))
    except (OSError, ValueError, TypeError):
        return None

POLL_S = 1.0
STALE_S = 5.0
SONOS_RES_S = 1.0          # a Sonos position resolves to this (whole seconds)
SONOS_SLACK_S = 0.25       # ... and its read time can be this far off either side
SONOS_LOOSE_S = 0.3        # narrowed less than this: the grid runs at gate "dmx" until the mic's beats confirm it
PHASE_ALPHA = 0.05         # the file grid's phase moves this share of the way onto the mic's beats each music.clock
PHASE_TEMPO_TOL = 0.05     # ... only while the mic's tempo is within this of the file's (or half of it)
PHASE_MAX_S = 0.6          # ... and never more than this from where the position puts it


def is_song(np_: Any) -> bool:
    """A known library song playing (this feed's now_playing, whatever its source): its brief, grid, sections and
    shape exist. The Music page's own piece is not one."""
    if not isinstance(np_, dict) or not np_.get("key"):
        return False
    src = str(np_.get("source") or "")
    return src == "karaoke" or src.startswith("sonos")


def is_karaoke(np_: Any) -> bool:
    """A karaoke song: someone sings (the mic's want, singing isn't talk)."""
    return isinstance(np_, dict) and np_.get("source") == "karaoke"
TIMEOUT_S = 2.0


def _fetch_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as r:   # noqa: S310 - loopback, our own Flask
        return json.loads(r.read().decode("utf-8"))


def library_shape(song_id: str) -> Optional[dict]:
    """A karaoke library song's measured shape (utils/song_shape.py: from its backing and lead vocal), kept under
    the show's state; None when its audio isn't there."""
    import smconfig
    from utils import song_shape
    return song_shape.load_or_measure(smconfig.state("karaoke", "songs", song_id),
                                      smconfig.state("show", "shapes", f"{song_id}.json"))


def library_song(song_id: str) -> Optional[dict]:
    """A karaoke library song's record (title, artist, year, genre, audio_s), or None."""
    import smconfig
    try:
        s = (json.loads(smconfig.state("karaoke", "state.json").read_text()).get("songs") or {}).get(song_id)
    except (OSError, ValueError):
        return None
    return s if isinstance(s, dict) else None


def library_moments(song_id: str) -> Optional[dict]:
    """A karaoke library song's moments (utils/song_moments.py: its stabs, stops, drum sections and how it ends),
    kept beside the song as moments.json: read, or measured (a few seconds: never on a playing song's path) and
    kept. None when its audio isn't there."""
    import smconfig
    from utils import song_moments
    d = smconfig.state("karaoke", "songs", song_id)
    try:
        m = json.loads((d / "moments.json").read_text())
        if isinstance(m, dict) and m.get("version") == song_moments.VERSION:
            return m
    except (OSError, ValueError):
        pass
    return song_moments.for_song(d) if d.is_dir() else None


def library_brief(song_id: str, shape: Optional[dict] = None, ai: bool = False) -> Optional[dict]:
    """A karaoke library song's brief (brief.py: its idiom, palette and section map), kept beside the song. The saved
    one; else, with ``ai`` off, one made here from the facts alone (instant, no network), and with it on the AI's
    (a call of up to a minute: never on the path a playing song waits on). None when the song isn't in the library."""
    import smconfig
    from . import brief
    s = library_song(song_id)
    d = smconfig.state("karaoke", "songs", song_id)
    if s is None or not d.is_dir():
        return None
    kw: dict = {}
    if ai:
        try:
            import yaml
            from . import idiom
            vocab = yaml.safe_load((idiom.PATH.parent / "vocab.yaml").read_text()) or {}
            vibes = yaml.safe_load((idiom.PATH.parents[1] / "room" / "vibes.yaml").read_text()) or {}
            kw = {"feel_palettes": ((vibes.get("vibes") or {}).get("singalong") or {}).get("palettes"),
                  "moods": vocab.get("moods"), "genres": vocab.get("genre")}
        except Exception:                                # noqa: BLE001 - the idioms' own palettes, no mood words
            kw = {}
    return brief.make(d, s, shape=shape, ai=ai, log=log.info, **kw)


class KaraokeFeed:
    def __init__(self, base_url: str, *, fetch: Callable[[str], Any] = _fetch_json,
                 clock: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic,
                 shape_fn: Optional[Callable[[str], Optional[dict]]] = None,
                 grid_fn: Optional[Callable[[str], Optional[FileGrid]]] = None,
                 brief_fn: Optional[Callable[..., Optional[dict]]] = None,
                 moments_fn: Optional[Callable[[str], Optional[dict]]] = None) -> None:
        self.base = base_url.rstrip("/")
        self.url = self.base + "/api/songs/now"           # the room's song: karaoke's, else a Sonos zone's library track
        self.url_old = self.base + "/api/karaoke/now"     # a Flask from before /api/songs/now: karaoke only
        self._sonos_iv: Optional[dict] = None             # a Sonos song's start, narrowed: {sid, lo, hi, at}
        self._ph_off = 0.0                               # the file grid pulled onto the mic's beats (s)
        self._brief_fn = brief_fn                        # (song id, shape, ai) -> its brief (library_brief), or None
        self._brief: tuple[Optional[str], Optional[dict]] = (None, None)                   # (song, its brief)
        self._brief_asked: set[str] = set()              # songs whose AI brief was asked for (once a run)
        self._shape_fn = shape_fn                        # a song id -> its measured shape (library_shape), or None
        self._moments_fn = moments_fn                    # a song id -> its moments (library_moments), or None
        self._moments: tuple[Optional[str], Optional[dict]] = (None, None)                 # (song, its moments)
        self._grid_fn = grid_fn                          # a song id -> its beat grid (library_grid), or None
        self._grid: tuple[Optional[str], Optional[FileGrid], float] = (None, None, -1e18)   # (song, grid, tried at)
        self._shape: tuple[Optional[str], Optional[dict]] = (None, None)                   # (song, its shape)
        self._ov: Optional[dict] = None                  # the file grid's line while it drives music.clock
        self._ov_epoch = 0
        self._fetch = fetch
        self._clock = clock
        self._mono = mono
        self._last: Optional[dict] = None
        self._read_at = -1e18
        self._failing = False
        self._wakes: list[Callable[[], None]] = []
        self._sections: tuple[Optional[str], list] = (None, [])   # (song key, lyric_sections) for structure's plan
        self._lyrics: tuple[Optional[str], str, str] = (None, "", "")   # (song key, all the words, an excerpt)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- reading ------------------------------------------------------------------------------------------------------
    def poll_once(self) -> None:
        try:
            try:
                o = self._fetch(self.url)
            except Exception:                            # noqa: BLE001 - an older Flask: the karaoke player's alone
                o = self._fetch(self.url_old)
            if not isinstance(o, dict):
                raise ValueError("not an object")
        except Exception as e:                           # noqa: BLE001 - Flask restarting: next time
            if not self._failing:
                log.info("karaoke: no reply from Flask (%s): no song until it answers", type(e).__name__)
            self._failing = True
            return
        if self._failing:
            log.info("karaoke: Flask answers again")
        self._failing = False
        was = self.playing()
        self._last, self._read_at = o, self._mono()
        self._sonos_read(o)
        self._read_sections(o)
        self._read_grid(o)
        nxt = o.get("up_next")                           # the song after this one: its AI brief, before it starts
        if isinstance(nxt, dict) and nxt.get("id"):
            self.brief_ahead(str(nxt["id"]))
        if self.playing() != was:
            for fn in list(self._wakes):
                try:
                    fn()
                except Exception:                        # noqa: BLE001
                    pass

    def _read_sections(self, o: dict) -> None:
        """Once a song (this thread): its verse / chorus / breaks from its lyrics (lyric_sections.py; owner, 26 Nov:
        "verse / chorus etc. triggers ... to the a.d."), for structure's lyric plan.  A failed read tries again on
        the next poll."""
        p, s = o.get("player") or {}, o.get("song") or {}
        sid = p.get("song_id") if isinstance(p, dict) else None
        child = bool((o.get("settings") or {}).get("child_mode"))
        key = f"{sid}:child" if child else sid           # child mode's lyrics are masked: read again on a switch
        rev = s.get("lyrics_rev") if isinstance(s, dict) else None
        if rev:                                          # its lyrics were re-timed (the careful pass, while it plays):
            key = f"{key}:r{rev}"                        # read again, so the sections move with them
        if not sid or key == self._sections[0] or not isinstance(s, dict):
            return
        try:
            from .lyric_sections import sections_from_lyrics
            lyr = self._fetch(f"{self.base}/api/karaoke/songs/{sid}/lyrics")
            dur = s.get("audio_s") or s.get("duration")
            secs = sections_from_lyrics(lyr, float(dur) if dur else None)
            from .song_character import excerpt
            lines = [str(x.get("text") or "") for x in (lyr.get("lines") or []) if isinstance(x, dict)] \
                if isinstance(lyr, dict) else []
        except Exception as e:                           # noqa: BLE001 - next poll
            log.info("karaoke: %s's sections not read (%s)", sid, type(e).__name__)
            return
        self._sections = (key, [])                        # held back until the song's shape has been tried
        self._plan_song(key, str(sid), secs, float(dur) if dur else None, str(s.get("title") or sid))
        # the words too, for the song's character (song_character.py) and the treatment's AI (an excerpt)
        self._lyrics = (key, " ".join(lines), excerpt(lines))

    def _read_grid(self, o: dict) -> None:
        """The playing song's beat grid, once (again every GRID_RETRY_S while it has none: it is being analysed)."""
        if self._grid_fn is None:
            return
        p = o.get("player") or {}
        sid = p.get("song_id") if isinstance(p, dict) else None
        if not sid:
            return
        g = self._grid
        if g[0] == sid and (g[1] is not None or self._mono() - g[2] < GRID_RETRY_S):
            return
        try:
            grid = self._grid_fn(str(sid))
        except Exception as e:                           # noqa: BLE001 - the mic's clock for this song
            log.info("karaoke: %s's beat grid not read (%s)", sid, type(e).__name__)
            grid = None
        if grid is not None and g[1] is None:
            log.info("karaoke: %s: the beat grid from the file (%d beats, %.1f bpm, %d/bar)", sid, len(grid.beats),
                     60.0 / grid.period, grid.meter)
        self._grid = (str(sid), grid, self._mono())

    def _plan_song(self, key: str, sid: str, secs: list, dur: Optional[float], title: str) -> None:
        """The song laid out (songplan.lay_out): its sections with the level its measured shape gives each and its
        climax marked. The shape is read (measured the first time: a few seconds, then kept) off this thread, and the
        sections are handed to structure only once it has been tried, so the plan never changes under it."""
        from . import songplan

        def work() -> None:
            shape = None
            if self._shape_fn is not None and secs:
                try:
                    shape = self._shape_fn(sid)
                except Exception as e:                   # noqa: BLE001 - no shape: the sections alone
                    log.info("karaoke: %s's shape not read (%s)", title, type(e).__name__)
            if self._moments_fn is not None:             # its stabs, stops and ending (utils/song_moments.py)
                try:
                    mo = self._moments_fn(sid)
                    self._moments = (sid, mo if isinstance(mo, dict) else None)
                    if isinstance(mo, dict):
                        log.info("karaoke: %s: moments %s", title, " ".join(
                            f"{m['kind']}{'/' + m['style'] if m.get('style') else ''}@{m['t']:.1f}"
                            for m in mo.get("moments") or ()) or "none")
                except Exception as e:                   # noqa: BLE001 - no moments: the sections alone
                    log.info("karaoke: %s's moments not read (%s: %s)", title, type(e).__name__, e)
            b = None
            if self._brief_fn is not None:               # the song's brief (brief.py): its own map and idiom
                try:
                    b = self._brief_fn(sid, shape, False)
                except Exception as e:                   # noqa: BLE001 - no brief: the lyric sections
                    log.info("karaoke: %s's brief not read (%s: %s)", title, type(e).__name__, e)
            plan = None
            if b is not None and len(b.get("sections") or ()) >= 2:
                try:
                    from . import brief as briefmod, idiom
                    plan = briefmod.plan(b, idiom.load().get("idioms"), shape, dur)
                except Exception as e:                   # noqa: BLE001
                    log.info("karaoke: %s's brief not laid out (%s: %s)", title, type(e).__name__, e)
                    b = None
            if plan is None:
                b, plan = None, songplan.lay_out(secs, shape, dur)
            if self._sections[0] == key:
                self._sections = (key, plan)
                self._shape = (sid, shape)
                self._brief = (sid, b)
            if b is not None:
                log.info("karaoke: %s: brief by %s: %s, %s. %s", title, b.get("by"), b.get("idiom"), b.get("palette"),
                         b.get("tone") or "")
            if b is None or b.get("by") != "ai":
                self.brief_ahead(sid)                    # the AI's, for the next time it is sung
            log.info("karaoke: %s: %s", title, " ".join(
                f"{x['kind']}@{x['start_s']:.0f}" + (f"={x['level']:.2f}" if x.get("level") is not None else "")
                + ("*" if x.get("peak") else "") for x in plan) or "no sections (lyrics not synced)")

        if self._shape_fn is None or not secs:
            work()
        else:
            threading.Thread(target=work, name="karaoke-shape", daemon=True).start()

    def brief_ahead(self, sid: Optional[str]) -> None:
        """Ask for a song's AI brief off every thread that matters, once a run: the song that is up next (so it is
        there before its first note), or one playing on a local brief (for the next time)."""
        if not sid or self._brief_fn is None or sid in self._brief_asked:
            return
        self._brief_asked.add(sid)

        def work() -> None:
            try:
                self._brief_fn(sid, self._shape_fn(sid) if self._shape_fn is not None else None, True)
            except Exception as e:                       # noqa: BLE001 - the local brief stands
                log.info("karaoke: %s's AI brief not made (%s: %s)", sid, type(e).__name__, e)
        threading.Thread(target=work, name="karaoke-brief", daemon=True).start()

    @staticmethod
    def _source(o: Optional[dict]) -> Optional[str]:
        if not o:
            return None
        src = o.get("source")
        return str(src) if src else ("karaoke" if (o.get("player") or {}).get("song_id") else None)

    def _sonos_read(self, o: dict) -> None:
        """A Sonos read: its song's start (wall clock) lies in [at - pos - 1 s, at - pos] (a whole-second position),
        give or take the read's own lag; each new read narrows it, a jump (a seek, a pause) starts again."""
        src = self._source(o) or ""
        p = o.get("player") or {}
        if not src.startswith("sonos") or p.get("state") != "playing" or not p.get("song_id"):
            self._sonos_iv = None
            return
        try:
            at, pos = float(p["at"]), float(p.get("pos_s") or 0.0)
        except (KeyError, TypeError, ValueError):
            return
        iv = self._sonos_iv
        if iv is not None and iv["sid"] == p["song_id"] and iv["at"] == at:
            return                                       # the same read again (Flask reads Sonos every 2 s)
        lo, hi = at - pos - SONOS_RES_S - SONOS_SLACK_S, at - pos + SONOS_SLACK_S
        if iv is not None and iv["sid"] == p["song_id"] and max(lo, iv["lo"]) <= min(hi, iv["hi"]):
            lo, hi = max(lo, iv["lo"]), min(hi, iv["hi"])
        else:
            self._ph_off = 0.0
        self._sonos_iv = {"sid": p["song_id"], "lo": lo, "hi": hi, "at": at}

    def _player_pos(self, o: dict) -> Optional[float]:
        """The player's position now (before the delay to the room): karaoke's exact clock; a Sonos song's from its
        narrowed start."""
        p = o.get("player") or {}
        iv = self._sonos_iv
        if p.get("state") == "playing" and iv is not None and iv["sid"] == p.get("song_id") and \
                (self._source(o) or "").startswith("sonos"):
            return self._clock() - 0.5 * (iv["lo"] + iv["hi"])
        try:
            pos = float(p.get("pos_s") or 0.0)
            if p.get("state") == "playing":
                pos += max(0.0, self._clock() - float(p.get("at") or 0.0))
        except (TypeError, ValueError):
            return None
        return pos

    def _fresh(self) -> Optional[dict]:
        o = self._last
        return o if o is not None and self._mono() - self._read_at <= STALE_S else None

    def now_playing(self) -> Optional[dict]:
        """The song the karaoke player is on: {key (song + queue entry), state, pos_s, dur_s, speed, title}; None
        while it is stopped or Flask doesn't answer."""
        o = self._fresh()
        p = o.get("player") if o else None
        s = o.get("song") if o else None
        if not isinstance(p, dict) or not isinstance(s, dict) or p.get("state") not in ("playing", "paused"):
            return None
        if not p.get("song_id"):
            return None
        pos = self._player_pos(o)
        src = self._source(o) or "karaoke"
        dur = s.get("audio_s") or s.get("duration")
        try:
            dur = float(dur) if dur else None
        except (TypeError, ValueError):
            dur = None
        title = str(s.get("title") or "").strip()
        artist = str(s.get("artist") or "").strip()
        # Its sections (lyric_sections.py), each at the player position where the ROOM hears it begin: the lyrics'
        # time + the song's lyric offsets + the delay to the room (settings.audio_delay_ms), as the display page.
        k = self._sections[0]
        secs = self._sections[1] if k is not None and k.split(":")[0] == p["song_id"] else []
        lk = self._lyrics[0]
        lyr = self._lyrics if lk is not None and lk.split(":")[0] == p["song_id"] else (None, "", "")
        lyr_shift, delay = self._shifts(o)
        grid = self._grid[1] if self._grid[0] == p["song_id"] else None

        def sec_at(x: dict) -> float:
            t = float(x["start_s"]) + (0.0 if x.get("split") else lyr_shift)   # a split is the shape's: audio time
            return grid.snap_downbeat(t) if grid is not None else t
        if pos is not None:
            pos -= delay                                 # where the room is in the song (its end lands as heard)
        kind = "karaoke" if src == "karaoke" else src.split(":")[0]
        return {"key": f"{kind}:{p['song_id']}:{p.get('qid') or ''}", "state": p["state"], "pos_s": pos,
                "dur_s": dur, "speed": 1.0, "title": (title + (f" — {artist}" if artist else "")) or None,
                "source": src,
                # the song's catalogue facts (Deezer, routes/karaoke/store.py FACTS): the director's genre while it
                # plays (director._song_genre; owner, 26 Nov: song context "should be doing more work")
                "artist": artist or None, "genre": s.get("genre") or None, "year": s.get("year") or None,
                "lyrics_text": lyr[1], "lyrics_excerpt": lyr[2],
                # its brief (brief.py; None: the lyric sections as they were): the idiom, tone and palette, and on
                # each section its size, how the music arrives there and whether it is the last chorus
                "brief": self._brief_view(p["song_id"]),
                # its moments (utils/song_moments.py), in the song's own audio time as pos_s: what the music does that
                # the lights should hit (show_service/moments.py chooses and performs them)
                "moments": (self._moments[1] or {}).get("moments") if self._moments[0] == p["song_id"] else None,
                "moments_beat_s": (self._moments[1] or {}).get("beat_s") if self._moments[0] == p["song_id"] else None,
                "sections": [{"pos_s": round(sec_at(x), 3), "kind": x["kind"], "letter": x["letter"],
                              "level": x.get("level"), "peak": x.get("peak"),
                              **{k: x[k] for k in ("size", "arrive", "brief_kind", "final") if k in x}}
                             for x in secs]}

    def _brief_view(self, sid: Any) -> Optional[dict]:
        if self._brief[0] != sid or self._brief[1] is None:
            return None
        from . import brief
        return brief.view(self._brief[1])

    @staticmethod
    def _shifts(o: dict) -> tuple[float, float]:
        """(the song's lyric offsets, the delay to the room) in s: the lyrics' own time + the first is the audio's;
        the player's position less the second is what the room hears."""
        s, st = o.get("song") or {}, o.get("settings") or {}
        try:
            lyr = (float(s.get("offset_ms") or 0.0) + float(s.get("user_offset_ms") or 0.0)) / 1000.0
        except (TypeError, ValueError, AttributeError):
            lyr = 0.0
        try:
            delay = float(st.get("audio_delay_ms") or 0.0) / 1000.0
        except (TypeError, ValueError, AttributeError):
            delay = 0.0
        return lyr, delay

    def heard_pos(self, now_mono: Optional[float] = None) -> Optional[tuple[str, float]]:
        """(song id, the song position the room hears at ``now_mono``) while a song PLAYS; else None."""
        o = self._fresh()
        p = o.get("player") if o else None
        if not isinstance(p, dict) or p.get("state") != "playing" or not p.get("song_id"):
            return None
        pos = self._player_pos(o)
        if pos is None:
            return None
        pos -= self._shifts(o)[1]
        if now_mono is not None:
            pos += now_mono - self._mono()
        return str(p["song_id"]), pos

    def energy_ahead(self, lookahead_s: float = 0.0) -> Optional[float]:
        """The playing song's measured intensity (utils/song_shape.py, 0..1, the song's own range) at the position
        the room will hear in ``lookahead_s``; None without a song playing or its shape."""
        h = self.heard_pos()
        if h is None or self._shape[0] != h[0] or not self._shape[1]:
            return None
        from utils import song_shape
        return song_shape.at(self._shape[1], h[1] + lookahead_s)

    def clock_override(self, now_s: float, body: Optional[dict]) -> Optional[dict]:
        """clock.Clock.set_override (show-rx, every music.clock): the file's beat grid along the room's position, as
        a music.clock body; None (the mic's clock) without one. Cheap: a few searches in the song's beats."""
        h = self.heard_pos(now_s)
        g = self._grid
        if h is None or g[1] is None or g[0] != h[0]:
            self._ov = None
            return None
        grid, t = g[1], h[1]
        b = grid.beats
        if t < b[0] - GRID_EDGE_S or t > b[-1] + grid.period:
            self._ov = None
            return None
        k = grid.nearest(t + GRID_LOOK_S)
        per = grid.local_period(k)
        if abs(b[grid.nearest(t)] - t) > GRID_GAP_BEATS * per and b[0] <= t <= b[-1]:
            self._ov = None                              # a stretch with no beat in it (a cappella, a break)
            return None
        ov = self._ov
        if ov is None or ov["sid"] != h[0] or abs(t - (ov["t"] + (now_s - ov["now"]))) > GRID_SEEK_S:
            prevs = []                                   # the count never goes back at a consumer's now
            if body and float(body.get("bpm") or 0.0) > 0.0:
                from .clock import beat_at
                prevs.append(beat_at(body, now_s))       # type: ignore[arg-type]
            if ov is not None and ov.get("t") is not None:
                prevs.append(ov["base"] + ov["k_at"] + (now_s - ov["now"]) / max(ov["per"], 1e-3))
            prevs = [x for x in prevs if math.isfinite(x)]
            base = int(math.ceil(max(prevs) - grid.frac_index(t))) + 1 if prevs else 0
            self._ov_epoch = max(self._ov_epoch, int((body or {}).get("epoch") or 0)) + 1
            ov = self._ov = {"sid": h[0], "base": base, "epoch": self._ov_epoch}
            log.info("karaoke: music.clock from the file's beats (song %s at %.1f s, epoch %d)", h[0], t,
                     self._ov_epoch)
        ov["t"], ov["now"], ov["k_at"], ov["per"] = t, now_s, grid.frac_index(t), per
        base = ov["base"]
        bpb = grid.meter
        cd = grid.downbeat_le(k)
        if cd is None:
            cd = k - (k % bpb)
        bar_anchor = cd
        sd = self._phrase_downbeat(h[0], float(b[k]), grid)
        conf_ph = 0.3
        if sd is not None and sd <= cd:
            bars = int(round((cd - sd) / bpb))
            bar_anchor = cd - (bars % GRID_PHRASE_BARS) * bpb
            conf_ph = 0.6
        anchor_s = now_s + float(b[k]) - t
        gate, conf = "beat", 0.95
        if (self._source(self._fresh()) or "").startswith("sonos"):
            anchor_s, gate, conf = self._onto_mic(anchor_s, per, now_s, body)
        return {"bpm": round(60.0 / per, 3), "anchor_ms": anchor_s * 1000.0,
                "beat_at_anchor": base + k, "beats_per_bar": bpb, "bar_anchor_beat": base + bar_anchor,
                "phrase_bars": GRID_PHRASE_BARS, "conf": conf, "bar_conf": 0.9 if gate == "beat" else 0.6,
                "phrase_conf": conf_ph, "state": "locked", "gate": gate, "epoch": ov["epoch"], "stability": 1.0,
                "source": grid.source if gate == "beat" else f"{grid.source}+sonos", "t_ms": now_s * 1000.0}

    def _onto_mic(self, anchor_s: float, per: float, now_s: float, body: Optional[dict]) -> tuple[float, str, float]:
        """A Sonos song's grid: its position is only good to a fraction of a second, so its beats are pulled onto
        the room mic's while the mic's clock is locked at the file's tempo (or half of it); until then, and while
        its start is still loose, it runs at gate "dmx" (lamps only at the middle depth: README §5.4)."""
        iv = self._sonos_iv
        loose = iv is None or iv["hi"] - iv["lo"] > SONOS_LOOSE_S
        mic_ok = False
        if body and body.get("gate") == "beat" and float(body.get("bpm") or 0.0) > 0.0:
            r = float(body["bpm"]) * per / 60.0          # the mic's tempo over the file's
            for octave in (1.0, 0.5):                 # a half-time mic beat is on a file beat; a double-time one may not be
                if abs(r / octave - 1.0) <= PHASE_TEMPO_TOL:
                    from .clock import beat_at
                    mp = 60.0 / float(body["bpm"])
                    f_mic = beat_at(body, now_s)          # type: ignore[arg-type]
                    # the mic's nearest beat to now, in seconds, against the file's
                    t_mic = now_s - ((f_mic % 1.0) * mp)
                    d = ((t_mic - (anchor_s + self._ph_off)) / per) % 1.0
                    d = (d - 1.0 if d > 0.5 else d) * per     # within half a beat either way
                    self._ph_off += PHASE_ALPHA * d
                    self._ph_off = max(-PHASE_MAX_S, min(PHASE_MAX_S, self._ph_off))
                    mic_ok = True
                    break
        gate = "beat" if (mic_ok or not loose) else "dmx"
        return anchor_s + self._ph_off, gate, 0.95 if gate == "beat" else 0.6

    def _phrase_downbeat(self, sid: str, t: float, grid: FileGrid) -> Optional[int]:
        """The beat index of the downbeat nearest the start of the section the song is in at ``t`` (audio time)."""
        k, plan = self._sections
        if k is None or k.split(":")[0] != sid or not plan:
            return None
        o = self._fresh()
        lyr = self._shifts(o)[0] if o else 0.0
        start = None
        for x in plan:
            st = float(x["start_s"]) + (0.0 if x.get("split") else lyr)
            if st <= t + 0.5 * grid.period:
                start = st
        if start is None:
            return None
        j = grid.nearest(grid.snap_downbeat(start))
        return j

    def playing(self) -> bool:
        """A song plays, or the speaker-delay calibration runs (routes/karaoke/calibrate.py: it times chirps with the
        mic): the mic's Karaoke want."""
        o = self._fresh()
        if o and isinstance(o.get("calibration"), dict) and o["calibration"].get("state") == "running":
            return True
        np_ = self.now_playing()
        return bool(np_ and np_.get("state") == "playing" and is_karaoke(np_))

    def add_on_change(self, fn: Callable[[], None]) -> None:
        """``fn()`` when a song starts or stops playing (the mic's wake)."""
        self._wakes.append(fn)

    # -- thread -------------------------------------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="show-karaoke", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(POLL_S)


def chain_now_playing(first: Optional[Callable[[], Optional[dict]]],
                      then: Callable[[], Optional[dict]]) -> Callable[[], Optional[dict]]:
    """ctx.now_playing: the Music page's piece while it has one, else the karaoke song."""
    def now_playing() -> Optional[dict]:
        if first is not None:
            try:
                got = first()
            except Exception:                            # noqa: BLE001
                got = None
            if got:
                return got
        return then()
    return now_playing
