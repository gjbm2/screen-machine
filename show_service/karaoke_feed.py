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

Only while the deployment's ``karaoke`` capability is on (``__main__``). Flask down or a bad reply: None / False
until it answers again (a read older than STALE_S counts as none).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from typing import Any, Callable, Optional

log = logging.getLogger("show.karaoke")

POLL_S = 1.0
STALE_S = 5.0
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


class KaraokeFeed:
    def __init__(self, base_url: str, *, fetch: Callable[[str], Any] = _fetch_json,
                 clock: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic,
                 shape_fn: Optional[Callable[[str], Optional[dict]]] = None) -> None:
        self.url = base_url.rstrip("/") + "/api/karaoke/now"
        self._shape_fn = shape_fn                        # a song id -> its measured shape (library_shape), or None
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
            o = self._fetch(self.url)
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
        self._read_sections(o)
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
            lyr = self._fetch(f"{self.url.rsplit('/now', 1)[0]}/songs/{sid}/lyrics")
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
            plan = songplan.lay_out(secs, shape, dur)
            if self._sections[0] == key:
                self._sections = (key, plan)
            log.info("karaoke: %s: %s", title, " ".join(
                f"{x['kind']}@{x['start_s']:.0f}" + (f"={x['level']:.2f}" if x.get("level") is not None else "")
                + ("*" if x.get("peak") else "") for x in plan) or "no sections (lyrics not synced)")

        if self._shape_fn is None or not secs:
            work()
        else:
            threading.Thread(target=work, name="karaoke-shape", daemon=True).start()

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
        try:
            pos = float(p.get("pos_s") or 0.0)
            if p["state"] == "playing":
                pos += max(0.0, self._clock() - float(p.get("at") or 0.0))
        except (TypeError, ValueError):
            pos = None
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
        try:
            st = o.get("settings") or {}
            shift = (float(s.get("offset_ms") or 0.0) + float(s.get("user_offset_ms") or 0.0)
                     + float(st.get("audio_delay_ms") or 0.0)) / 1000.0
        except (TypeError, ValueError, AttributeError):
            shift = 0.0
        return {"key": f"karaoke:{p['song_id']}:{p.get('qid') or ''}", "state": p["state"], "pos_s": pos,
                "dur_s": dur, "speed": 1.0, "title": (title + (f" — {artist}" if artist else "")) or None,
                "source": "karaoke",
                # the song's catalogue facts (Deezer, routes/karaoke/store.py FACTS): the director's genre while it
                # plays (director._song_genre; owner, 26 Nov: song context "should be doing more work")
                "artist": artist or None, "genre": s.get("genre") or None, "year": s.get("year") or None,
                "lyrics_text": lyr[1], "lyrics_excerpt": lyr[2],
                "sections": [{"pos_s": round(x["start_s"] + shift, 3), "kind": x["kind"], "letter": x["letter"],
                              "level": x.get("level"), "peak": x.get("peak")} for x in secs]}

    def playing(self) -> bool:
        """A song plays, or the speaker-delay calibration runs (routes/karaoke/calibrate.py: it times chirps with the
        mic): the mic's Karaoke want."""
        o = self._fresh()
        if o and isinstance(o.get("calibration"), dict) and o["calibration"].get("state") == "running":
            return True
        np_ = self.now_playing()
        return bool(np_ and np_.get("state") == "playing")

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
