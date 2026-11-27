"""karaoke.api: /api/karaoke/* (docs/KARAOKE.md). Only while the deployment's ``karaoke`` capability is on.

  GET    /karaoke/state                    the library, the queue, the player and the player page's last report
  GET    /karaoke/now                      what the display page needs (polled every second), + join {url, room_karaoke}
  GET    /karaoke/search?q=                LRCLIB songs matching the text: [{artist, title, duration, synced}]
  POST   /karaoke/requests                 {q} | {url} | {artist, title, duration}: a song into the library
                                           (+ queue: true also puts it at the end of the queue; + singers)
  POST   /karaoke/songs/<id>/retry         a failed song again
  POST   /karaoke/songs/<id>/refine          {note?}: lyrics off: the careful pass over them (in the background, after
                                           songs being prepared) | {undo: true}: the first pass's lyrics back
                                           | {cancel: true}: not waiting any more
  PATCH  /karaoke/songs/<id>               {user_offset_ms}: nudge this song's lyrics
  DELETE /karaoke/songs/<id>               forget it (files too)
  GET    /karaoke/songs/<id>/lyrics        {kind, lines: [{start, end, text}], ...}
  GET    /karaoke/songs/<id>/<stem>.m4a    instrumental | vocals
  POST   /karaoke/queue                    {op: add, song_id, index?, singers?} | {op: remove, qid} | {op: move, qid, index}
                                           | {op: singers, qid, singers} | {op: clear}: entries; the same song can be
                                           queued more than once. singers: who sing it, [names] (optional; the names
                                           of the last 12 h are /state's singers)
  POST   /karaoke/player                   {action: play [qid | song_id]|pause|resume|restart|stop|next|prev|seek [pos_s]}
                                           (play song_id: a new entry right after the current one, played now)
  POST   /karaoke/settings                 {guide 0..1, autoplay, audio_delay_ms, highlight words|dot|both, child_mode,
                                            screen (a screen's id, null: the deployment's own), output screen|none}
"""
from __future__ import annotations

import json
import re
from typing import Any

from flask import Blueprint, jsonify, request, send_file

import smconfig
from utils.logger import error

from . import sources as src
from .store import FACTS, Store, song_dir, song_key

karaoke_api = Blueprint("karaoke_api", __name__)
_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def _err(code: int, message: str):
    return jsonify({"error": message}), code


def _body() -> dict[str, Any]:
    b = request.get_json(silent=True)
    return b if isinstance(b, dict) else {}


@karaoke_api.before_request
def _only_when_on():
    if not smconfig.enabled("karaoke"):
        return _err(404, "Karaoke is off on this installation (capability karaoke).")
    return None


@karaoke_api.get("/karaoke/state")
def state():
    from . import sonos_driver
    d = sonos_driver()
    return jsonify({**store().snapshot(), "sonos_error": d.error if d is not None else None})


@karaoke_api.get("/karaoke/now")
def now():
    from . import room_in_karaoke
    from .join import join_url
    # join: the panel's address for a phone and whether the room is in a karaoke mode (the screens' QR code at rest)
    return jsonify({**store().now_playing(), "join": {"url": join_url(), "room_karaoke": room_in_karaoke()}})


@karaoke_api.get("/karaoke/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": []})
    try:
        rows = src.catalog_search(q)
    except Exception as e:  # noqa: BLE001
        return _err(502, f"The song search didn't answer: {e}")
    for r in rows:
        dup = store().find_existing(key=song_key(r["artist"], r["title"]))
        r["have"] = dup["id"] if dup else None
    return jsonify({"results": rows})


@karaoke_api.get("/karaoke/artist")
def artist():
    """An artist's songs, most popular first (sources.artist_top), each marked if the library has it."""
    name = (request.args.get("name") or "").strip()
    if not name:
        return _err(400, "Say which artist: ?name=")
    try:
        o = src.artist_top(name)
    except Exception as e:  # noqa: BLE001
        return _err(502, f"The song search didn't answer: {e}")
    for r in o["results"]:
        dup = store().find_existing(key=song_key(r["artist"], r["title"]))
        r["have"] = dup["id"] if dup else None
    return jsonify(o)


@karaoke_api.get("/karaoke/playlist")
def playlist():
    """A Spotify playlist or album, read (sources.spotify_list), each song marked if the library has it."""
    url = (request.args.get("url") or "").strip()
    try:
        o = src.spotify_list(url)
    except src.SourceError as e:
        return _err(400, str(e))
    for t in o["tracks"]:
        dup = store().find_existing(key=song_key(t["artist"], t["title"]))
        t["have"] = dup["id"] if dup else None
    return jsonify(o)


@karaoke_api.post("/karaoke/import")
def import_list():
    """Every song of a Spotify playlist or album into the library to be prepared (owner, 26 Nov), skipping the ones
    it has; with {queue: true}, each into the queue too (at the end, in the list's order)."""
    b = _body()
    try:
        o = src.spotify_list(str(b.get("url") or ""))
    except src.SourceError as e:
        return _err(400, str(e))
    st, added, had = store(), 0, 0
    for t in o["tracks"]:
        dup = st.find_existing(key=song_key(t["artist"], t["title"]))
        song = dup or st.add_song({"kind": "lrclib", "artist": t["artist"], "title": t["title"],
                                   "duration": t["duration"]})
        if dup:
            had += 1
        else:
            added += 1
        if b.get("queue"):
            st.queue_add(song["id"])
    return jsonify({"name": o["name"], "added": added, "had": had, "total": len(o["tracks"])})


@karaoke_api.post("/karaoke/requests")
def add_request():
    b = _body()
    st = store()
    if b.get("url"):
        url = str(b["url"]).strip()
        if not src.is_url(url):
            return _err(400, "That isn't a link.")
        kind = src.classify_url(url)
        req = {"kind": kind, "url": url}
        vid = _youtube_id(url) if kind == "youtube" else None
        dup = st.find_existing(video_id=vid) if vid else None
    elif b.get("title"):
        req = {"kind": "lrclib", "artist": str(b.get("artist") or ""), "title": str(b["title"]),
               "duration": b.get("duration"),
               **{k: b[k] for k in FACTS if b.get(k) is not None}}         # the search's facts: genre, year, rank...
        dup = st.find_existing(key=song_key(req["artist"], req["title"]))
    elif b.get("q"):
        q = str(b["q"]).strip()
        if src.is_url(q):
            return _err(400, "Send a link as {url}.")
        req, dup = {"kind": "text", "q": q}, None
    else:
        return _err(400, "Say which song: {q}, {url} or {artist, title, duration}.")
    song = dup or st.add_song(req)
    if b.get("queue"):                                   # true or "end": the end; "next": up next
        st.queue_add(song["id"], next=b.get("queue") == "next", singers=_singers(b))
    return jsonify({"song_id": song["id"], "existing": bool(dup)})


def _youtube_id(url: str) -> str | None:
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


@karaoke_api.post("/karaoke/songs/<sid>/retry")
def retry(sid: str):
    """Prepare the song again from nothing: a failed one, or one stuck preparing (its running step ends)."""
    from . import song_worker
    w = song_worker()
    if w is not None:
        return jsonify({"ok": True}) if w.restart(sid) else _err(404, "No such song.")
    s = store().update_song(sid, status="queued", error=None, detail="")
    return jsonify(s) if s else _err(404, "No such song.")


@karaoke_api.post("/karaoke/songs/<sid>/refine")
def refine(sid: str):
    """Refer a song whose lyrics were off for the careful pass (worker.refine, tools/karaoke/refine.py: every voice
    out, heard, the whole lyrics timed at once): song.refine says how it is going. Or undo it, or cancel the wait."""
    import time as _time
    from . import song_worker
    b, st = _body(), store()
    with st.lock:
        song = st.s["songs"].get(sid)
        r = dict((song or {}).get("refine") or {})
    if song is None:
        return _err(404, "No such song.")
    if b.get("undo"):
        w = song_worker()
        if w is None or not w.undo_refine(sid):
            return _err(409, "There's nothing to undo.")
        return jsonify({"ok": True})
    if b.get("cancel"):
        if r.get("state") != "waiting":
            return _err(409, "It isn't waiting.")
        st.update_song(sid, refine={**r, "state": "cancelled", "detail": ""})
        return jsonify({"ok": True})
    if song.get("status") != "ready":
        return _err(409, "The song isn't ready yet.")
    if r.get("state") in ("waiting", "working"):
        return jsonify({"ok": True, "refine": r})
    r = {"state": "waiting", "detail": "", "error": None, "asked_at": _time.time(),
         "note": str(b.get("note") or "")[:300] or None, "times": int(r.get("times") or 0) + 1}
    st.update_song(sid, refine=r)
    return jsonify({"ok": True, "refine": r})


@karaoke_api.patch("/karaoke/songs/<sid>")
def patch_song(sid: str):
    b = _body()
    if "user_offset_ms" not in b:
        return _err(400, "Send {user_offset_ms}.")
    ms = int(max(-10000, min(10000, int(b["user_offset_ms"]))))
    if store().update_song(sid, user_offset_ms=ms) is None:
        return _err(404, "No such song.")
    return jsonify({"id": sid, "user_offset_ms": ms})


@karaoke_api.delete("/karaoke/songs/<sid>")
def delete_song(sid: str):
    return jsonify({"ok": True}) if store().remove_song(sid) else _err(404, "No such song.")


@karaoke_api.get("/karaoke/songs/<sid>/lyrics")
def song_lyrics(sid: str):
    with store().lock:
        song = store().s["songs"].get(sid)
        lyr = dict(song.get("lyrics") or {}) if song else None
    if song is None:
        return _err(404, "No such song.")
    doc = lyr or {"kind": "none", "lines": []}
    if store().s["settings"].get("child_mode"):        # child mode: swear words as dashes, and when to mute them
        from .profanity import censor
        sung = None
        try:                                            # what Whisper heard: [{w, t0, t1}] (or {"words": [...]})
            raw = json.loads((song_dir(sid) / "sung.json").read_text())
            sung = raw.get("words") if isinstance(raw, dict) else raw
        except (OSError, ValueError):
            pass
        try:
            doc = censor(doc, sung if isinstance(sung, list) else None)
        except Exception as e:  # noqa: BLE001 - never lose the lyrics to child mode: at least the words masked
            error(f"karaoke child mode ({sid}): {e}")
            doc = censor(doc, None)
    return jsonify(doc)


@karaoke_api.get("/karaoke/songs/<sid>/<stem>.m4a")
def song_audio(sid: str, stem: str):
    if stem not in ("instrumental", "vocals") or not re.fullmatch(r"k[0-9a-f]{10}", sid):
        return _err(404, "No such file.")
    f = song_dir(sid) / f"{stem}.m4a"
    if not f.is_file():
        return _err(404, "Not ready yet.")
    return send_file(f, mimetype="audio/mp4", conditional=True, max_age=3600)


def _singers(b: dict) -> Any:
    """The body's singers: [names], or the first version's one {singer}."""
    return b.get("singers") if b.get("singers") is not None else b.get("singer")


@karaoke_api.post("/karaoke/queue")
def queue():
    b = _body()
    op, st = str(b.get("op") or ""), store()
    idx = b.get("index")
    try:
        if op == "add":
            st.queue_add(str(b.get("song_id")), None if idx is None else int(idx), next=bool(b.get("next")),
                         singers=_singers(b))
        elif op in ("singers", "singer"):
            st.queue_singers(str(b.get("qid")), _singers(b))
        elif op == "remove":
            st.queue_remove(str(b.get("qid")))
        elif op == "move":
            st.queue_move(str(b.get("qid")), int(idx))
        elif op == "clear":
            st.queue_clear()
        else:
            return _err(400, "op is add, remove, move, singers or clear.")
    except KeyError:
        return _err(404, "No such song or queue entry.")
    except (TypeError, ValueError):
        return _err(400, "index is a number.")
    return jsonify({"queue": st.snapshot()["queue"]})


@karaoke_api.post("/karaoke/player")
def player():
    b = _body()
    action = str(b.get("action") or "")
    try:
        if action == "play":
            p = store().play(b.get("song_id"), b.get("qid"))
        elif action in ("pause", "resume", "restart", "stop", "next", "prev", "seek"):
            p = store().control(action, b.get("pos_s"))
        else:
            return _err(400, "action is play, pause, resume, restart, stop, next, prev or seek.")
    except KeyError:
        return _err(404, "No such song.")
    except LookupError as e:
        return _err(409, str(e))
    return jsonify({"player": p})


_calibrator = None


@karaoke_api.post("/karaoke/calibrate")
def calibrate():
    """Measure the speaker delay with the room mic (calibrate.py): starts a run; its progress is in /state."""
    global _calibrator
    if str(store().s["settings"].get("output") or "").startswith("sonos:"):
        return _err(409, "A Sonos room keeps its own timing (the Music page's: its start delay in latency.json and "
                         "the Sonos's own position as it plays): there's nothing to calibrate here.")
    if _calibrator is None:
        from .calibrate import Calibrator
        _calibrator = Calibrator(store(), smconfig.url("show"))
    return jsonify({"calibration": _calibrator.start()})


@karaoke_api.get("/karaoke/outputs")
def outputs():
    """Where the karaoke sound can play: the karaoke screen's own page, nowhere, or a Sonos room of the deployment
    (the Music page's Play on Sonos rooms: room/sonos.json, labelled as there)."""
    from .sonos_out import sonos_zones
    rows = [{"id": "screen", "label": "From the screen's own page"}, {"id": "none", "label": "No sound (screens only)"}]
    rows += [{"id": f"sonos:{z}", "label": f"Sonos ({room})"} for z, room in sonos_zones().items()]
    return jsonify({"outputs": rows})


@karaoke_api.post("/karaoke/settings")
def settings():
    b = _body()
    o = b.get("output")
    if isinstance(o, str) and o.startswith("sonos:"):
        from .sonos_out import sonos_zones
        if o[len("sonos:"):] not in sonos_zones():
            return _err(400, "There's no such Sonos room here.")
    try:
        return jsonify({"settings": store().settings(**{k: b[k] for k in ("guide", "autoplay", "audio_delay_ms", "highlight", "child_mode", "screen", "output") if k in b})})
    except (TypeError, ValueError):
        return _err(400, "guide is 0..1, autoplay true/false, audio_delay_ms a number, highlight words | dot | both, "
                         "screen a screen's id (or null), output screen | none | sonos:<zone>.")
