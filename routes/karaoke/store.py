"""karaoke.store: the song library, the singing queue and the player, in one JSON file (docs/KARAOKE.md).

State lives in ``~/.local/state/screen-machine/karaoke/state.json`` and each song's files in ``.../karaoke/songs/<id>/``
(smconfig.state). One lock guards everything; every change is written straight back (atomic replace).

A song moves through ``status``: queued -> fetching -> lyrics -> separating -> ready, or failed (``error`` says why).
The library is every song added (``songs``). The queue is the running order: entries ``{qid, song_id}``; the same song
can be in it more than once, and removing an entry leaves the library alone. Like a hi-fi's queue, entries stay after
they play: the player points at its entry (``player.qid``); next and previous move along the queue, skipping songs
that aren't ready yet.

The player is the one clock, here on the server (like a hi-fi or a Sonos): a song, a state, and the position ``pos_s``
it had at server time ``at``; while playing, the position now is ``pos_s + (now - at)``. Play, pause, next and the end
of a song (``tick``: the next ready song, or stop) all happen here, whether no display page is open or ten. Display
pages only follow it; nothing waits on them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

import smconfig

STATUSES = ("queued", "fetching", "lyrics", "separating", "ready", "failed")
WORKING = ("queued", "fetching", "lyrics", "separating")
GUIDE_LEVELS = (0.0, 0.25, 1.0)          # the lead vocal under the backing track: off, quiet, full

STATUS_WORDS = {"queued": "waiting its turn", "fetching": "getting the song", "lyrics": "finding the lyrics",
                "separating": "removing the lead vocal", "failed": "it didn't work"}

DEFAULT_SETTINGS = {"guide": 0.0, "autoplay": True, "audio_delay_ms": 0, "highlight": "words", "child_mode": False,
                    "screen": None, "output": "screen"}
#: Where the karaoke sound plays (owner, 26 Nov: "specify which screen we're outputting to ... and living room sonos
#: audio"): "screen", the karaoke screen's own page (its browser: speakers, HDMI, Bluetooth); "none", the screens only.
#: "sonos:<zone>", a Sonos room playing it itself (sonos_out.py; the api checks the zone is the deployment's).
OUTPUTS = ("screen", "none")
SONOS_OUTPUT = "sonos:"
#: How the words show as they're sung (owner, 25 Nov): yellow as they're sung, a bouncing dot, or both.
HIGHLIGHTS = ("words", "dot", "both")


def root() -> Path:
    return smconfig.state("karaoke")


def song_dir(song_id: str) -> Path:
    return root() / "songs" / song_id


def norm(s: str | None) -> str:
    """'Beyoncé – Halo (Official Video)' -> 'beyonce halo official video': for matching, never shown."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def song_key(artist: str | None, title: str | None) -> str:
    return f"{norm(artist)}|{norm(title)}"


def _same_key(a: str, b: str) -> bool:
    """Two artist|title keys name one song: the same title, the artist's words in any order ("Williams, Pharrell" is
    Pharrell Williams)."""
    (aa, at), (ba, bt) = a.split("|", 1), b.split("|", 1)
    return at == bt and sorted(aa.split()) == sorted(ba.split())


def new_qid() -> str:
    return "q" + os.urandom(5).hex()


def new_id(seed: str) -> str:
    return "k" + hashlib.sha1(f"{seed}|{time.time_ns()}".encode()).hexdigest()[:10]


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or (root() / "state.json")
        self.lock = threading.RLock()
        self.calibration: dict | None = None             # calibrate.py's run (memory only): /now and the panel show it
        self._autostart: str | None = None               # a song added to an idle player: it plays when ready (qid)
        self.changed = threading.Condition(self.lock)
        self.s: dict[str, Any] = {"songs": {}, "queue": [], "player": self._idle_player(0), "settings": dict(DEFAULT_SETTINGS)}
        self._load()

    # ---- persistence
    def _load(self) -> None:
        try:
            d = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        self.s["songs"] = d.get("songs") or {}
        q = d.get("queue") or []
        self.s["queue"] = [e if isinstance(e, dict) else {"qid": new_qid(), "song_id": e} for e in q]   # old: bare ids
        self.s["queue"] = [e for e in self.s["queue"] if e.get("song_id") in self.s["songs"]]
        self.s["settings"] = {**DEFAULT_SETTINGS, **(d.get("settings") or {})}
        p = d.get("player") or {}
        # The clock is wall time, so a restart carries on where the song is now (a hi-fi doesn't stop when the
        # control app reconnects). A song that is gone: stopped.
        if (p.get("song_id") in self.s["songs"] and p.get("state") in ("playing", "paused")
                and any(e["qid"] == p.get("qid") for e in self.s["queue"])):
            self.s["player"] = {**self._idle_player(0), **p, "rev": int(p.get("rev") or 0) + 1}
        else:
            self.s["player"] = self._idle_player(int(p.get("rev") or 0) + 1)
        for song in self.s["songs"].values():          # work cut off by a restart starts again from the top
            if song.get("status") in WORKING:
                song["status"], song["detail"] = "queued", "restarted"

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        body = self.s
        tmp.write_text(json.dumps(body, indent=1))
        os.replace(tmp, self.path)

    def commit(self) -> None:
        """Save and wake anyone waiting on a change (the worker). Call with the lock held."""
        self._save()
        self.changed.notify_all()

    # ---- songs
    def merge_into(self, sid: str, keep: str) -> None:
        """``sid`` turned out to be ``keep`` (already in the library): its queue entries become ``keep``'s."""
        with self.lock:
            for e in self.s["queue"]:
                if e["song_id"] == sid:
                    e["song_id"] = keep
        self.remove_song(sid)

    def find_existing(self, *, video_id: str | None = None, key: str | None = None,
                      exclude: str | None = None) -> dict | None:
        with self.lock:
            for song in self.s["songs"].values():
                if song.get("status") == "failed" or song["id"] == exclude:
                    continue
                if video_id and song.get("video_id") == video_id:
                    return song
                if key and key != "|" and _same_key(song_key(song.get("artist"), song.get("title")), key):
                    return song
        return None

    def add_song(self, request: dict) -> dict:
        """A new song from a request: {kind: text|lrclib|youtube|spotify|url, ...what the request said}."""
        with self.lock:
            sid = new_id(json.dumps(request, sort_keys=True))
            song = {"id": sid, "status": "queued", "detail": "", "error": None, "request": request,
                    "title": request.get("title") or request.get("q") or request.get("url") or "?",
                    "artist": request.get("artist") or "", "duration": request.get("duration"),
                    "video_id": None, "source_url": None, "lyrics": None, "offset_ms": 0, "user_offset_ms": 0,
                    "created_at": time.time(), "ready_at": None}
            for k in FACTS:                              # what the search already knew (catalog_search's row)
                if request.get(k) is not None:
                    song[k] = request[k]
            self.s["songs"][sid] = song
            self.commit()
            return song

    def update_song(self, sid: str, **fields: Any) -> dict | None:
        with self.lock:
            song = self.s["songs"].get(sid)
            if song is None:
                return None
            song.update(fields)
            self.commit()
            return dict(song)

    def remove_song(self, sid: str) -> bool:
        with self.lock:
            if sid not in self.s["songs"]:
                return False
            del self.s["songs"][sid]
            self.s["queue"] = [e for e in self.s["queue"] if e["song_id"] != sid]
            if self.s["player"].get("song_id") == sid:
                self._set_player("stopped", None)
            self.commit()
        d = song_dir(sid)
        if d.is_dir():
            for f in d.iterdir():
                f.unlink(missing_ok=True)
            d.rmdir()
        return True

    def next_job(self) -> dict | None:
        """The oldest song waiting to be processed: queued-for-singing songs first, then the rest."""
        with self.lock:
            waiting = [s for s in self.s["songs"].values() if s.get("status") == "queued"]
            if not waiting:
                return None
            order: dict[str, int] = {}
            for i, e in enumerate(self.s["queue"]):
                order.setdefault(e["song_id"], i)
            waiting.sort(key=lambda s: (order.get(s["id"], 10_000), s.get("created_at") or 0))
            return dict(waiting[0])

    # ---- the queue
    def queue_add(self, sid: str, index: int | None = None, next: bool = False) -> dict:
        """A new entry for song ``sid`` at ``index`` (default: the end); ``next``: up next, right after the entry the
        player is on (playing, paused or stopped there: Play carries on after it), else at the top."""
        with self.lock:
            if sid not in self.s["songs"]:
                raise KeyError(sid)
            e = {"qid": new_qid(), "song_id": sid}
            q = self.s["queue"]
            p = self.s["player"]
            # Nothing playing and nothing waiting: the song added plays (as soon as it is ready). Owner, 25 Nov: "without
            # anything playing ... shouldn't it just have started playing as the only item in the queue?"
            idle = p["state"] == "stopped" and not self._upcoming(p.get("qid"))
            if next:
                i = self._index(p.get("qid"))
                index = 0 if i is None else i + 1
            q.insert(len(q) if index is None else max(0, min(len(q), int(index))), e)
            if idle:
                self._autostart = e["qid"]
                self._start_if_ready()
            self.commit()
            return e

    def _upcoming(self, qid: str | None) -> list[dict]:
        i = self._index(qid)
        return self.s["queue"][0 if i is None else i + 1:]

    def _start_if_ready(self) -> None:
        """The song added to an idle player (queue_add), once it is ready, unless anything else happened meanwhile
        (lock held)."""
        q = self._autostart
        if not q:
            return
        entry = next((e for e in self.s["queue"] if e["qid"] == q), None)
        song = self.s["songs"].get(entry["song_id"]) if entry else None
        if entry is None or song is None or song.get("status") == "failed" or self.s["player"]["state"] != "stopped":
            self._autostart = None
        elif self._ready(entry):
            self._autostart = None
            self._set_player("playing", entry, -self.lead_in(entry))
            self.commit()

    def queue_remove(self, qid: str) -> None:
        with self.lock:
            if self.s["player"].get("qid") == qid:          # taking out the one playing: on to the next
                nxt = self.next_ready(qid)
                if self.s["player"]["state"] == "playing" and nxt:
                    self._set_player("playing", nxt, -self.lead_in(nxt))
                else:
                    self._set_player("stopped", None)
            self.s["queue"] = [e for e in self.s["queue"] if e["qid"] != qid]
            self.commit()

    def queue_move(self, qid: str, index: int) -> None:
        with self.lock:
            q = self.s["queue"]
            i = next((k for k, e in enumerate(q) if e["qid"] == qid), None)
            if i is None:
                raise KeyError(qid)
            e = q.pop(i)
            q.insert(max(0, min(len(q), int(index))), e)
            self.commit()

    def queue_clear(self) -> None:
        """Clear the queue (owner, 25 Nov: "a 'clear all' on queue"): all of it, except the song playing (or paused)
        now, so whoever is singing isn't cut off."""
        with self.lock:
            p = self.s["player"]
            keep = self.current_entry() if p["state"] in ("playing", "paused") else None
            self.s["queue"] = [keep] if keep else []
            if not keep:
                self._set_player("stopped", None)
            self._autostart = None
            self.commit()

    # ---- the player
    @staticmethod
    def _idle_player(rev: int, song_id: str | None = None) -> dict:
        return {"rev": rev, "state": "stopped", "song_id": song_id, "qid": None, "pos_s": 0.0, "at": time.time()}

    def _set_player(self, state: str, entry: dict | None, pos_s: float = 0.0) -> dict:
        p = {"rev": self.s["player"]["rev"] + 1, "state": state if entry else "stopped",
             "song_id": entry["song_id"] if entry else None, "qid": entry["qid"] if entry else None,
             "pos_s": max(-LEAD_IN_S, float(pos_s)), "at": time.time()}
        self.s["player"] = p
        return p

    def lead_in(self, entry: dict | None) -> float:
        """Silence before the song's music, so that its title is up for at least LEAD_IN_S before its first line
        (owner, 25 Nov: "ensure a min. 8s between title of song being shown and first lyric (by injecting silence if
        needed)": singers change over). The player starts the song at -lead_in; its sound starts at 0."""
        song = self.s["songs"].get((entry or {}).get("song_id") or "") or {}
        return song_lead_in(song)

    def position(self, now: float | None = None) -> float:
        """Where the song is now, by the server's clock."""
        p = self.s["player"]
        if p["state"] == "playing":
            return p["pos_s"] + max(0.0, (now or time.time()) - p["at"])
        return p["pos_s"]

    def _index(self, qid: str | None) -> int | None:
        return next((i for i, e in enumerate(self.s["queue"]) if e["qid"] == qid), None) if qid else None

    def _ready(self, e: dict) -> bool:
        return (self.s["songs"].get(e["song_id"]) or {}).get("status") == "ready"

    def next_ready(self, after_qid: str | None = None) -> dict | None:
        """The first ready entry after ``after_qid`` (from the top when None)."""
        i = self._index(after_qid)
        start = 0 if i is None else i + 1
        return next((e for e in self.s["queue"][start:] if self._ready(e)), None)

    def prev_ready(self, before_qid: str | None) -> dict | None:
        i = self._index(before_qid)
        if i is None:
            return None
        return next((e for e in reversed(self.s["queue"][:i]) if self._ready(e)), None)

    def current_entry(self) -> dict | None:
        i = self._index(self.s["player"].get("qid"))
        return self.s["queue"][i] if i is not None else None

    def play(self, sid: str | None = None, qid: str | None = None) -> dict:
        """Play a queue entry (``qid``: moved up to right after the current entry, which joins the songs already sung;
        the rest keep their order); or a library song now (``sid``: a new entry right after the current one);
        or, with neither, carry on: resume a pause, else the next ready entry. From the start."""
        with self.lock:
            self._autostart = None
            p = self.s["player"]
            if qid:
                entry = next((e for e in self.s["queue"] if e["qid"] == qid), None)
                if entry is None:
                    raise KeyError(qid)
                # Playing an entry further down (or one already sung) moves it up to right after the current one,
                # which goes into the songs already sung; nothing else moves (owner, 25 Nov: "not zap others").
                q = self.s["queue"]
                if entry["qid"] != p.get("qid") and self.s["songs"].get(entry["song_id"], {}).get("status") == "ready":
                    q.remove(entry)
                    ci = self._index(p.get("qid"))
                    q.insert(0 if ci is None else ci + 1, entry)
            elif sid:
                if sid not in self.s["songs"]:
                    raise KeyError(sid)
                i = self._index(p.get("qid"))
                entry = {"qid": new_qid(), "song_id": sid}
                self.s["queue"].insert(len(self.s["queue"]) if i is None else i + 1, entry)
            else:
                if p["state"] == "playing" and self.current_entry():
                    return p                                 # already playing: nothing to do
                if p["state"] == "paused" and self.current_entry():
                    return self.control("resume")
                entry = self.next_ready(p.get("qid"))
                if entry is None:
                    raise LookupError("Nothing in the queue is ready to play.")
            song = self.s["songs"][entry["song_id"]]
            if song.get("status") != "ready":
                self.commit()
                raise LookupError(f"“{song.get('title')}” isn't ready yet: {STATUS_WORDS.get(song.get('status'), song.get('status'))}.")
            p = self._set_player("playing", entry, -self.lead_in(entry))
            self.commit()
            return p

    def control(self, action: str, pos_s: float | None = None, by: str = "you") -> dict:
        """``by``: who stops it: "you" (the panel) or "end" (the queue ran out: tick); live_view.py reads it."""
        with self.lock:
            self._autostart = None                       # the person took over
            p, cur = self.s["player"], self.current_entry()
            if action == "pause" and p["state"] == "playing":
                p = self._set_player("paused", cur, self.position())
            elif action == "resume" and p["state"] == "paused" and cur:
                p = self._set_player("playing", cur, p["pos_s"])
            elif action == "restart" and cur:
                p = self._set_player("playing", cur, -self.lead_in(cur))
            elif action == "seek" and cur and pos_s is not None:
                dur = (self.s["songs"].get(cur["song_id"]) or {}).get("audio_s") or float("inf")
                p = self._set_player(p["state"] if p["state"] != "stopped" else "paused", cur, min(float(pos_s), dur))
            elif action == "stop":
                p = self._set_player("stopped", None)
                p["qid"] = cur["qid"] if cur else None       # Play carries on from here
                p["stop"] = by
            elif action == "next":
                nxt = self.next_ready(p.get("qid"))
                p = self._set_player("playing", nxt, -self.lead_in(nxt)) if nxt else self._set_player("stopped", None)
            elif action == "prev":
                prv = self.prev_ready(p.get("qid"))
                if cur and (self.position() > 3.0 or prv is None):
                    p = self._set_player("playing", cur, -self.lead_in(cur))   # like any player: back to the start first
                elif prv:
                    p = self._set_player("playing", prv, -self.lead_in(prv))
                else:
                    return p
            else:
                return p
            self.commit()
            return p

    def tick(self, now: float | None = None) -> None:
        """The end of a song: the next ready entry (autoplay), else stop; and a song added to an idle player starts
        once it is ready (queue_add). Called every half second."""
        with self.lock:
            self._start_if_ready()
            p = self.s["player"]
            if p["state"] != "playing":
                return
            dur = (self.s["songs"].get(p["song_id"] or "") or {}).get("audio_s")
            if dur and self.position(now) >= dur:
                if self.s["settings"].get("autoplay") and self.next_ready(p.get("qid")):
                    self.control("next")
                else:
                    self.control("stop", by="end")

    def settings(self, **kw: Any) -> dict:
        with self.lock:
            st = self.s["settings"]
            if "guide" in kw:
                st["guide"] = min(1.0, max(0.0, float(kw["guide"])))
            if "autoplay" in kw:
                st["autoplay"] = bool(kw["autoplay"])
            if "audio_delay_ms" in kw:
                st["audio_delay_ms"] = int(min(5000, max(-2000, int(kw["audio_delay_ms"]))))
            if "screen" in kw:                           # the karaoke screen; None: the deployment's own choice
                v = kw["screen"]
                if v is not None and not (isinstance(v, str) and 0 < len(v) <= 64 and v.replace("-", "").replace("_", "").isalnum()):
                    raise ValueError("screen")
                st["screen"] = v
            if "output" in kw:
                o = kw["output"]
                if not (o in OUTPUTS or (isinstance(o, str) and o.startswith(SONOS_OUTPUT) and len(o) > len(SONOS_OUTPUT))):
                    raise ValueError("output")
                st["output"] = kw["output"]
            if "child_mode" in kw:
                st["child_mode"] = bool(kw["child_mode"])
            if "highlight" in kw:
                if kw["highlight"] not in HIGHLIGHTS:
                    raise ValueError("highlight")
                st["highlight"] = kw["highlight"]
            self.commit()
            return dict(st)

    def anchor(self, rev: int, pos_s: float, at: float) -> None:
        """The song is at ``pos_s`` at server time ``at`` (sonos_out.py: what the room hears), without a new rev (no
        output window re-places anything), and only while the player is still on ``rev``, playing."""
        with self.lock:
            p = self.s["player"]
            if p.get("rev") == rev and p.get("state") == "playing":
                p["pos_s"], p["at"] = round(float(pos_s), 4), float(at)
                self.commit()

    # ---- the speaker-delay calibration (calibrate.py)
    def set_calibration(self, c: dict | None) -> None:
        with self.lock:
            self.calibration = dict(c) if c else None

    def pause_for_calibration(self) -> None:
        """A song playing would drown the chirps: pause it (Play carries on)."""
        with self.lock:
            if self.s["player"]["state"] == "playing":
                self.control("pause")

    # ---- views
    def snapshot(self) -> dict:
        with self.lock:
            songs = sorted(self.s["songs"].values(), key=lambda s: -(s.get("created_at") or 0))
            return {"songs": [public_song(s) for s in songs], "queue": [dict(e) for e in self.s["queue"]],
                    "player": {**self.s["player"], "position_s": round(self.position(), 2)},
                    "settings": dict(self.s["settings"]), "calibration": self.calibration, "now": time.time()}

    def now_playing(self) -> dict:
        """What a display page needs, small enough to poll every second."""
        with self.lock:
            p = dict(self.s["player"])
            song = self.s["songs"].get(p.get("song_id") or "")
            nxt = self.next_ready(p.get("qid"))
            nsong = self.s["songs"].get(nxt["song_id"]) if nxt else None
            child = bool(self.s["settings"].get("child_mode"))
            return {"player": p, "settings": dict(self.s["settings"]),
                    "song": (child_song(public_song(song)) if child else public_song(song)) if song else None,
                    "up_next": {"id": nsong["id"], "title": nsong.get("title"), "artist": nsong.get("artist")} if nsong else None,
                    "calibration": self.calibration if (self.calibration or {}).get("state") == "running" else None,
                    "now": time.time()}


#: A song's catalogue facts (sources.song_facts / catalog_search): for the library's tags and its sorting. ``rank`` is
#: Deezer's popularity (higher: more played), the cheap proxy for "most popular first".
FACTS = ("genre", "year", "rank", "art", "album")


LEAD_IN_S = 8.0


def song_lead_in(s: dict) -> float:
    """``Store.lead_in`` for a song: LEAD_IN_S less how far into the song its first timed line is (0 without one)."""
    lines = (s.get("lyrics") or {}).get("lines") or []
    starts = [ln["start"] for ln in lines if isinstance(ln, dict) and isinstance(ln.get("start"), (int, float))]
    if not starts:
        return 0.0
    return round(max(0.0, LEAD_IN_S - min(starts)), 2)


def child_song(ps: dict) -> dict:
    """A public song with its title and artist's swear words as dashes (child mode: profanity.py)."""
    from . import profanity
    return {**ps, **{k: profanity.mask_text(ps.get(k)) for k in ("title", "artist", "album") if ps.get(k)}}


def song_tags(s: dict) -> list[str]:
    """The library's tags for a song: its genre and its decade ("Pop", "1980s")."""
    tags = []
    if s.get("genre"):
        tags.append(str(s["genre"]))
    if isinstance(s.get("year"), int) and s["year"] > 1900:
        tags.append(f"{s['year'] // 10 * 10}s")
    return tags


def public_song(s: dict) -> dict:
    lyr = s.get("lyrics") or {}
    return {k: s.get(k) for k in ("id", "status", "detail", "error", "title", "artist", "duration", "audio_s", "video_id",
                                  "source_url", "offset_ms", "user_offset_ms", "created_at", "ready_at") + FACTS} | {
        "lyrics": {"kind": lyr.get("kind"), "lines": len(lyr.get("lines") or [])} if lyr else None,
        "request_kind": (s.get("request") or {}).get("kind"), "tags": song_tags(s), "writers": s.get("writers"),
        "lead_in_s": song_lead_in(s)}
