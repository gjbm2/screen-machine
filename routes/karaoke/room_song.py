"""The song playing in the room, whoever plays it (owner, 28 Nov: "all the logic just built for karaoke: we should have
the same logic (precisely -- same codepaths ...) available for a.d. processing of sonos tracks ... it shouldn't be
specific to karaoke mode"; "I mean the entire karaoke a.d. / processing / song narrative etc machinery").

The karaoke machinery works on library songs (store.py): each prepared by the worker (worker.py: the recording, the
lead vocal taken out, the lyrics timed to it, then its beats, shape and brief). Two things here make it source-free:

* GET /api/songs/now: /karaoke/now's body (player, song, settings ...) plus ``source``, for the show's song feed
  (show_service/karaoke_feed.py), so the same grid, sections, shape and brief drive the art director:
    "karaoke"        the karaoke player has a song (playing or paused): its own body;
    "sonos:<zone>"   else a Sonos zone plays a track that is a ready library song: the player is that song at
                     Sonos's own position (read at ``at``), settings.audio_delay_ms the Sonos output's delay;
    None             else the karaoke player's body (stopped); ``rest`` true when the room is in a karaoke mode
                     with no song chosen: the show holds a gentle pattern then, whatever the mic hears (the quiet
                     background music between songs is not a song to perform; owner, 28 Nov: "feels like we're playing
                     entire music scenes etc. during pre-state ... it should just be in a gentle holding pattern").
* watch(): every WATCH_S, each Sonos zone's track (room_service's now_playing reading); one not in the library is
  added to it, ``request.origin`` "sonos", and the worker prepares it exactly as a requested song (it is not queued
  for singing). The worker paces it as it does every song (karaoke/pace.py).
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from typing import Any, Callable, Optional

import smconfig
from utils.logger import info

from .store import Store, public_song, song_key

WATCH_S = 5.0
SONOS_STATES = ("playing", "transitioning")
MIN_TRACK_S = 60.0               # shorter (a jingle, an advert, a stream with no length): not prepared


def _secs(hms: Any) -> Optional[float]:
    """Sonos's "0:03:12" -> 192.0; "" or "NOT_IMPLEMENTED" -> None."""
    m = re.fullmatch(r"(\d+):(\d{1,2}):(\d{1,2})", str(hms or "").strip())
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) if m else None


def sonos_tracks(status: dict) -> list[dict]:
    """room_service's GET /api/v1/status -> each Sonos zone's track: {zone, state, title, artist, duration_s,
    position_s, age_s}."""
    members = (((status or {}).get("subsystems") or {}).get("sonos") or {}).get("members") or {}
    out = []
    for name, m in members.items():
        if not name.endswith(".now_playing") or not isinstance(m, dict) or not isinstance(m.get("believed"), dict):
            continue
        b = m["believed"]
        if not b.get("title"):
            continue
        out.append({"zone": name[: -len(".now_playing")], "state": str(b.get("state") or "").lower(),
                    "title": b.get("title"), "artist": b.get("artist") or "", "duration_s": _secs(b.get("duration")),
                    "position_s": _secs(b.get("position")), "age_s": float(m.get("age_s") or 0.0)})
    return out


def room_status() -> dict:
    try:
        with urllib.request.urlopen(smconfig.url("room") + "/api/v1/status", timeout=2.0) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def sonos_delay_ms(zone: str) -> float:
    """The Sonos output's delay (smsound.latency), 0 when not known."""
    try:
        from smsound import latency
        v = latency.effective_ms(f"sonos:{zone}")
        return float(v) if isinstance(v, (int, float)) else 0.0
    except Exception:
        return 0.0


def song_now(store: Store, tracks: list[dict], now: Optional[float] = None, karaoke_room: bool = False) -> dict:
    """GET /api/songs/now's body (module docstring). ``karaoke_room``: the room is in a karaoke mode."""
    now = time.time() if now is None else now
    body = store.now_playing()
    p = body.get("player") or {}
    if p.get("state") in ("playing", "paused") and p.get("song_id"):
        return {**body, "source": "karaoke", "rest": False}
    for t in tracks:
        if t["state"] not in SONOS_STATES or t["position_s"] is None:
            continue
        song = store.find_existing(key=song_key(t["artist"], t["title"]))
        if not song or song.get("status") != "ready":
            continue
        at = now - t["age_s"]                            # when Sonos said where it was
        rev = int(abs(hash((song["id"], t["zone"]))) % 1_000_000)
        settings = {**(body.get("settings") or {}), "audio_delay_ms": sonos_delay_ms(t["zone"])}
        return {**body, "source": f"sonos:{t['zone']}", "settings": settings, "song": public_song(song),
                "player": {"rev": rev, "state": "playing", "song_id": song["id"], "qid": None,
                           "pos_s": float(t["position_s"]), "at": at},
                "up_next": None, "singers": [], "queued_after": 0, "background": [], "rest": False}
    return {**body, "source": None, "rest": bool(karaoke_room)}


def prepare_track(store: Store, t: dict) -> Optional[dict]:
    """A Sonos track the library lacks: added as a song (the worker prepares it). The song, or None (not one to
    prepare, or already there)."""
    if (t.get("duration_s") or 0) < MIN_TRACK_S or not t.get("title"):
        return None
    if store.find_existing(key=song_key(t["artist"], t["title"])):
        return None
    song = store.add_song({"kind": "lrclib", "artist": t["artist"], "title": t["title"],
                           "duration": t["duration_s"], "origin": "sonos"})
    info(f"songs: {t['artist']} – {t['title']} plays on Sonos ({t['zone']}): prepared for the show as {song['id']}")
    return song


class Watch:
    def __init__(self, store: Store, read: Callable[[], dict] = room_status) -> None:
        self.store = store
        self.read = read
        self.tracks: list[dict] = []
        self.at = 0.0
        self._stop = threading.Event()

    def poll_once(self) -> None:
        tracks = sonos_tracks(self.read())
        self.tracks, self.at = tracks, time.time()
        for t in tracks:
            if t["state"] in SONOS_STATES:
                prepare_track(self.store, t)
                with self.store.lock:
                    self.store.changed.notify_all()     # the worker wakes for it

    def current(self) -> list[dict]:
        """The last read's tracks, their age counted on to now (positions are extrapolated by song_now's ``at``)."""
        d = time.time() - self.at
        return [{**t, "age_s": t["age_s"] + d} for t in self.tracks]

    def start(self) -> "Watch":
        def run() -> None:
            while not self._stop.wait(WATCH_S):
                try:
                    self.poll_once()
                except Exception as e:  # noqa: BLE001
                    info(f"songs: Sonos watch: {e}")
        threading.Thread(target=run, name="songs-sonos-watch", daemon=True).start()
        return self


_watch: Optional[Watch] = None


def start(store: Store) -> Optional[Watch]:
    """Only where the deployment has Sonos (room/sonos.json)."""
    global _watch
    if _watch is None and smconfig.path("room", "sonos.json").exists():
        _watch = Watch(store).start()
    return _watch


def tracks_now() -> list[dict]:
    return _watch.current() if _watch else []


# GET /api/songs/now joins the karaoke routes (karaoke_api, url prefix /api)
from flask import jsonify  # noqa: E402

from .api import karaoke_api, store as _store  # noqa: E402


@karaoke_api.get("/songs/now")
def songs_now():
    from . import room_in_karaoke
    return jsonify(song_now(_store(), tracks_now(), karaoke_room=room_in_karaoke()))
