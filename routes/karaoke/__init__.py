"""Karaoke (docs/KARAOKE.md): ask for a song by name or link, it is downloaded, its lead vocal removed and its lyrics
fetched; every display page plays the backing track with the lyrics, following the server's clock. Capability ``karaoke``."""
from __future__ import annotations

import threading
import time

import smconfig
from utils.logger import error

from .api import karaoke_api, store
from . import pictures  # noqa: F401 - its routes join karaoke_api; its thread starts in start()
from . import room_song  # noqa: F401 - GET /api/songs/now joins karaoke_api; its Sonos watch starts in start()
from .store import song_dir
from .worker import Worker, audio_seconds

_worker: Worker | None = None
_sonos = None


def song_worker():
    """The song worker, once started; else None."""
    return _worker


def sonos_driver():
    """The Sonos driver (sonos_out.py), where the deployment has Sonos and it has started; else None."""
    return _sonos
WRITERS_RETRY_S = 900


_live = None


def room_in_karaoke() -> bool:
    """The room is in a karaoke mode (live_view.LiveView.in_karaoke_mode; False before it has looked)."""
    return bool(_live and _live.in_karaoke_mode())


def start() -> None:
    """Start the worker (app.py, outside tests). A no-op while the capability is off."""
    global _worker
    if not smconfig.enabled("karaoke") or _worker is not None:
        return
    st = store()
    with st.lock:                                   # songs made before the clock knew their length
        missing = [sid for sid, s in st.s["songs"].items() if s.get("status") == "ready" and not s.get("audio_s")]
    for sid in missing:
        st.update_song(sid, audio_s=audio_seconds(song_dir(sid) / "instrumental.m4a"))
    _worker = Worker(st)
    _worker.start()
    threading.Thread(target=_clock, args=(st,), name="karaoke-clock", daemon=True).start()
    threading.Thread(target=_facts, args=(st,), name="karaoke-facts", daemon=True).start()
    pictures.start()                                # the artists' pictures for the photo scenes
    room_song.start(st)                             # a Sonos track is prepared as a karaoke song is
    from .live_view import LiveView                 # the karaoke screen goes live with a song, back to art on Stop
    global _live
    _live = LiveView(st)
    _live.start()
    from routes.sound import calibrator             # a speaker being measured: no song over the chirps
    calibrator().before.append(lambda _output: st.pause_for_calibration())
    if smconfig.enabled("sonos"):                       # karaoke on a Sonos room (sonos_out.py)
        global _sonos
        from .sonos_out import KaraokeSonos
        _sonos = KaraokeSonos(st)
        _sonos.start()


def _facts(st) -> None:
    """Songs without their catalogue facts (genre, year, popularity, cover: the library's tags and sorting) get them,
    one at a time; a song the catalogues don't know is marked and not asked about again. Then who wrote each song
    (MusicBrainz, for the intro card), retried after WRITERS_RETRY_S when MusicBrainz doesn't answer."""
    from . import sources as src
    failed: dict[str, float] = {}
    while True:
        with st.lock:
            need = [(sid, s.get("artist") or "", s.get("title") or "") for sid, s in st.s["songs"].items()
                    if s.get("status") == "ready" and not s.get("writers_at")
                    and time.time() - failed.get(sid, 0) > WRITERS_RETRY_S]
        for sid, a, t in need:
            try:
                st.update_song(sid, writers=src.song_writers(a, t), writers_at=time.time())
            except Exception as e:  # noqa: BLE001 - MusicBrainz busy or down: later
                failed[sid] = time.time()
                error(f"karaoke writers ({t}): {e}")
        with st.lock:
            todo = [(sid, s.get("artist") or "", s.get("title") or "") for sid, s in st.s["songs"].items()
                    if not s.get("facts_at") and s.get("status") in ("ready", "lyrics", "separating")]
        for sid, a, t in todo:
            try:
                f = src.song_facts(a, t)
                with st.lock:
                    cur = st.s["songs"].get(sid) or {}
                    fields = {k: v for k, v in f.items() if v is not None and cur.get(k) is None}
                st.update_song(sid, facts_at=time.time(), **fields)
            except Exception as e:  # noqa: BLE001
                error(f"karaoke facts: {e}")
        time.sleep(20)


def _clock(st) -> None:
    """The end of each song, on the server's clock (Store.tick), twice a second."""
    while True:
        try:
            st.tick()
        except Exception as e:  # noqa: BLE001
            error(f"karaoke clock: {e}")
        time.sleep(0.5)


__all__ = ["karaoke_api", "start", "store"]
