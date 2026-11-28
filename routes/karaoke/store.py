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
from smsound import eq as EQ
from smsound import latency as L
from smsound import outputs as O

STATUSES = ("queued", "fetching", "lyrics", "separating", "ready", "failed")
WORKING = ("queued", "fetching", "lyrics", "separating")
GUIDE_LEVELS = (0.0, 0.25, 1.0)          # the lead vocal under the backing track: off, quiet, full

STATUS_WORDS = {"queued": "waiting its turn", "fetching": "getting the song", "lyrics": "finding the lyrics",
                "separating": "removing the lead vocal", "failed": "it didn't work"}

DEFAULT_SETTINGS = {"guide": 0.0, "autoplay": True, "highlight": "words", "child_mode": False, "muted": False,
                    "screen": None, "output": None}
#: Where the karaoke sound plays (owner, 26 Nov: "specify which screen we're outputting to ... and living room sonos
#: audio"): one of the app's sound outputs (smsound.outputs; owner, 27 Nov: "generalise this ... in a consistent
#: standard way"), of the kinds karaoke can play: ``screen:<id>`` (that screen's own page: its browser, speakers, HDMI,
#: Bluetooth), ``device:<id>`` (a browser playing it itself: the Karaoke page's This device, the same player as a
#: screen's page; owner, 27 Nov: "'this device' is always a meaningful notion in any device that can host a browser"),
#: ``sonos:<zone>`` (a Sonos playing it itself: sonos_out.py), ``none``. None: the karaoke screen's own
#: page (``output()``). Its delay is the output's, in smsound.latency (the one store); /now carries it as
#: settings.audio_delay_ms for the display page and the lyric feeds.
KINDS = ("screen", "device", "sonos", "none")
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


#: Who sings: a queue entry's optional ``singers``, one or more names (owner, 27 Nov: "note who is singing which song
#: ... you don't want to have to type out everyone's name repeatedly"; "needs to be poss. to add more than 1 singer").
#: The names used in the last SESSION_S are the panel's suggestions.
SESSION_S = 12 * 3600
SINGER_MAX = 30
SINGERS_MAX = 8


def clean_singer(name: Any) -> str | None:
    """A singer's name as typed, tidied (spaces, length); None for none."""
    n = " ".join(str(name or "").split())[:SINGER_MAX].strip()
    return n or None


def clean_singers(names: Any) -> list[str]:
    """One name or a list of them, tidied, each once (any case), at most SINGERS_MAX."""
    raw = names if isinstance(names, (list, tuple)) else [names]
    out: list[str] = []
    for x in raw:
        n = clean_singer(x)
        if n and n.lower() not in (o.lower() for o in out):
            out.append(n)
    return out[:SINGERS_MAX]


def new_id(seed: str) -> str:
    return "k" + hashlib.sha1(f"{seed}|{time.time_ns()}".encode()).hexdigest()[:10]


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or (root() / "state.json")
        self.lock = threading.RLock()
        self.calibration: dict | None = None             # calibrate.py's run (memory only): /now and the panel show it
        # Two things wait for a song to be ready (kept across a restart):
        self._auto = False                               # nothing is on: the first song to come that is ready plays
        self._cut: str | None = None                     # Play now on one still being prepared (qid): it plays when
                                                         # ready, whatever is on
        self.changed = threading.Condition(self.lock)
        self.s: dict[str, Any] = {"songs": {}, "queue": [], "player": self._idle_player(0), "settings": dict(DEFAULT_SETTINGS),
                                 "sung": 0,                      # how many of the queue, from its top, are sung
                                 "singers": {}}                  # name -> when last given a song (SESSION_S)
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
        kept = [e.get("song_id") in self.s["songs"] for e in self.s["queue"]]      # a song that is gone: its entries too
        full = self.s["queue"]
        self.s["queue"] = [e for e, k in zip(full, kept) if k]
        for e in self.s["queue"]:                       # the first version: one name
            if "singer" in e:
                one = e.pop("singer")
                if one and not e.get("singers"):
                    e["singers"] = [one]
        self.s["settings"] = {**DEFAULT_SETTINGS, **(d.get("settings") or {})}
        self._migrate_output()
        self.s["singers"] = {k: float(v) for k, v in (d.get("singers") or {}).items() if isinstance(v, (int, float))}
        if d.get("last_song") in self.s["songs"]:
            self.s["last_song"] = d["last_song"]
        p = d.get("player") or {}
        rev = int(p.get("rev") or 0) + 1
        q = self.s["queue"]
        on = p.get("state") in ("playing", "paused")
        if "sung" in d:                                  # how many are sung, less the ones whose song is gone
            n = max(0, min(len(full), int(d.get("sung") or 0)))
            sung = sum(kept[:n])
            cur = full[n] if on and n < len(full) and kept[n] else None
        else:
            # A file from before "sung" was kept: the player's entry said where it was. Everything above it is sung;
            # so is it, once stopped (Stop, the queue's end), unless a restart cut it off ("boot": still to come).
            i = next((k for k, e in enumerate(q) if e["qid"] == p.get("qid")), None)
            sung = 0 if i is None else i if (on or p.get("stop") == "boot") else i + 1
            cur = q[i] if on and i is not None else None
        self.s["sung"] = sung
        song = self.s["songs"].get(cur["song_id"]) if cur else None
        # The clock is wall time, so a restart carries on where the song is now (a hi-fi doesn't stop when the
        # control app reconnects).
        if cur and song:
            self.s["player"] = {**self._idle_player(0), **p, "rev": rev, "qid": cur["qid"], "song_id": cur["song_id"]}
            dur = song.get("audio_s")
            pos = float(p.get("pos_s") or 0) + max(0.0, time.time() - float(p.get("at") or 0))
            if p.get("state") == "playing" and dur and pos >= dur:
                # It "ended" while the machine was off (owner, 27 Nov: a queued song showed, didn't play, and Play did
                # nothing): stopped, and it is still to come, at the top: Play plays it.
                self.s["player"] = {**self._idle_player(rev, cur["song_id"]), "stop": "boot"}
        else:
            self.s["player"] = {**self._idle_player(rev), **({"stop": p["stop"]} if p.get("stop") and not on else {})}
        a_ = d.get("autostart")
        self._cut = d.get("play_now") or (a_ if d.get("autostart_cut") and isinstance(a_, str) else None)
        if not any(e["qid"] == self._cut for e in q):
            self._cut = None
        # (before "sung": the entry that was waiting to start, by its qid)
        self._auto = a_ is True or (isinstance(a_, str) and not d.get("autostart_cut") and any(e["qid"] == a_ for e in q))
        self._settle()
        for song in self.s["songs"].values():          # work cut off by a restart starts again from the top
            if song.get("status") in WORKING:
                song["status"], song["detail"] = "queued", "restarted"
            if (song.get("refine") or {}).get("state") == "working":    # the careful pass too: it waits its turn
                song["refine"] = {**song["refine"], "state": "waiting", "detail": "restarted"}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        body = {**self.s, "autostart": self._auto, "play_now": self._cut}
        tmp.write_text(json.dumps(body, indent=1))
        os.replace(tmp, self.path)

    def commit(self) -> None:
        """Save and wake anyone waiting on a change (the worker). Call with the lock held."""
        self._settle()
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
            for qid in [e["qid"] for e in self.s["queue"] if e["song_id"] == sid]:
                self._take_out(qid)                      # each as if taken out of the queue by hand
            del self.s["songs"][sid]
            if self.s.get("last_song") == sid:
                self.s.pop("last_song", None)
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
    def next_refine(self) -> dict | None:
        """The oldest ready song referred for the careful pass over its lyrics (refine.state "waiting")."""
        with self.lock:
            waiting = [s for s in self.s["songs"].values()
                       if s.get("status") == "ready" and (s.get("refine") or {}).get("state") == "waiting"]
            if not waiting:
                return None
            return dict(min(waiting, key=lambda s: (s.get("refine") or {}).get("asked_at") or 0))

    def _singers(self, names: Any) -> list[str]:
        """``names`` tidied, each spelt as it was last time (Clare, not clare), and remembered as used now. Lock held."""
        known = self.s.setdefault("singers", {})
        out = []
        for n in clean_singers(names):
            n = next((k for k in known if k.lower() == n.lower()), n)
            known[n] = time.time()
            out.append(n)
        return out

    def singers(self) -> list[str]:
        """The names given songs in the last SESSION_S, most recent first: the panel's suggestions."""
        cut = time.time() - SESSION_S
        with self.lock:
            known = self.s.get("singers") or {}
            return [k for k, t in sorted(known.items(), key=lambda kv: -kv[1]) if t >= cut]

    def roster(self, add: Any = None, remove: Any = None) -> list[str]:
        """Tonight's names before anyone sings (owner, 27 Nov: "a way to add singers' names prior to their starting
        to sing"): ``add`` names join them, ``remove`` names leave. -> singers()."""
        with self.lock:
            self._singers(add)
            gone = {n.lower() for n in clean_singers(remove)}
            known = self.s.setdefault("singers", {})
            for k in [k for k in known if k.lower() in gone]:
                del known[k]
            self.commit()
        return self.singers()

    def new_session(self) -> None:
        """A new session (owner, 27 Nov): tonight's names are forgotten and the songs already sung leave the queue;
        the song playing (or paused) and the ones to come stay."""
        with self.lock:
            self.s["queue"] = self.s["queue"][self.s["sung"]:]
            self.s["sung"] = 0
            self.s["singers"] = {}
            self.commit()

    def queue_singers(self, qid: str, singers: Any) -> dict:
        """Who sings a queue entry: a name or a list ([] or None: nobody said)."""
        with self.lock:
            e = next((e for e in self.s["queue"] if e["qid"] == qid), None)
            if e is None:
                raise KeyError(qid)
            ns = self._singers(singers)
            if ns:
                e["singers"] = ns
            else:
                e.pop("singers", None)
            self.commit()
            return dict(e)

    # The queue is one list in three parts, in order (owner, 28 Nov: "the whole queue logic ... should be rethought
    # properly"):
    #
    #     queue[:sung]      the songs already sung, oldest first
    #     queue[sung]       the song that is on, while one plays or is paused (else there is none)
    #     the rest          the songs to come, in the order they will play
    #
    # ``sung`` is the only thing that says where the player is, and only the few functions below change it or move an
    # entry across a part's edge: _start (a song goes on), _halt (the one on is over), _take_out, and the moves. So a
    # song to come is never among the sung until it has been on, and a sung one never comes back by accident. The
    # player's ``qid`` is only its name for the pages: the song on; stopped, the last one sung (_settle).
    def waiting(self) -> bool:
        """The player will start a song by itself when one is ready (see _start_if_ready)."""
        return self._auto or self._cut is not None

    def _on(self) -> bool:
        """A song is on (playing or paused)."""
        return self.s["player"]["state"] in ("playing", "paused")

    def _top(self) -> int:
        """Where the songs to come start."""
        return self.s["sung"] + (1 if self._on() else 0)

    def to_come(self) -> list[dict]:
        return self.s["queue"][self._top():]

    def _first_ready(self) -> dict | None:
        """The first song to come that is ready to play (one still being prepared, or failed, keeps its place)."""
        return next((e for e in self.to_come() if self._ready(e)), None)

    def _settle(self) -> None:
        """What must always be true, put right before every save: ``sung`` within the queue, the song on is
        queue[sung], and the player's qid names it (stopped: the last one sung, or after a restart cut a song off,
        that song, still to come). Lock held."""
        q, p = self.s["queue"], self.s["player"]
        self.s["sung"] = n = max(0, min(len(q), int(self.s.get("sung") or 0)))
        if self._on():
            cur = q[n] if n < len(q) else None
            if cur is None or cur["song_id"] not in self.s["songs"]:
                self._set_player("stopped", None)        # the song on is gone: nothing is on
                p = self.s["player"]
            else:
                p["qid"], p["song_id"] = cur["qid"], cur["song_id"]
        if not self._on():
            cut_off = p.get("stop") == "boot" and n < len(q)
            p["qid"] = q[n]["qid"] if cut_off else q[n - 1]["qid"] if n else None
        if self._cut and not any(e["qid"] == self._cut for e in q):
            self._cut = None

    def _start(self, entry: dict, gap: bool = True, pos_s: float | None = None) -> dict:
        """``entry`` goes on, from its start: the one that was on joins the songs sung, and that one entry moves to
        the top (owner, 28 Nov: "'play this' is ONLY meant to move THAT ONE SONG to top of queue not to change anything
        else"). Lock held."""
        q = self.s["queue"]
        at = self._start_at(entry, gap) if pos_s is None else pos_s      # before anything changes: it reads the player
        if self._on() and q[self.s["sung"]]["qid"] != entry["qid"]:
            self.s["sung"] += 1
        i = self._index(entry["qid"])
        q.pop(i)
        if i < self.s["sung"]:                           # one already sung, on again
            self.s["sung"] -= 1
        q.insert(self.s["sung"], entry)
        return self._set_player("playing", entry, at)

    def _halt(self, why: str | None) -> dict:
        """Nothing is on: the song that was joins the songs sung. ``why``: "you" (Stop), "end" (it played out, or was
        skipped with nothing ready after it), "mode" (the room left karaoke). Lock held."""
        if self._on():
            self.s["sung"] += 1
        p = self._set_player("stopped", None)
        if why:
            p["stop"] = why
        return p

    def _to_top(self, entry: dict) -> None:
        """``entry`` to the top of the songs to come; nothing else moves. Lock held."""
        q = self.s["queue"]
        i = self._index(entry["qid"])
        q.pop(i)
        if i < self.s["sung"]:
            self.s["sung"] -= 1
        q.insert(self._top(), entry)

    def _take_out(self, qid: str) -> None:
        """One entry leaves the queue; nothing else changes part. The one playing: on to the next ready one. Lock
        held."""
        q = self.s["queue"]
        i = self._index(qid)
        if i is None:
            return
        n = self.s["sung"]
        if self._on() and i == n:
            nxt = self._first_ready() if self.s["player"]["state"] == "playing" else None
            if nxt:
                self._start(nxt, pos_s=-self.lead_in(nxt))       # (the one taken out is now the last "sung": index n)
                q.pop(n)
                self.s["sung"] -= 1
            else:
                was = self.s["player"]["state"]
                self._set_player("stopped", None)["stop"] = "gone"
                q.pop(n)
                # it was playing and nothing after it is ready: what is still being prepared plays when it is
                self._auto = was == "playing" and bool(self.s["settings"].get("autoplay"))
        else:
            q.pop(i)
            if i < n:
                self.s["sung"] -= 1
        if self._cut == qid:
            self._cut = None

    def queue_add(self, sid: str, index: int | None = None, next: bool = False, singers: Any = None) -> dict:
        """A new entry for song ``sid`` among the songs to come: at the end, or ``next`` (the top), or at ``index`` of
        the whole queue (never above the top: up there are the songs sung). ``singers``: who sing it (a name or a
        list; optional). Added while nothing is on and nothing is waiting, it plays (as soon as it is ready)."""
        with self.lock:
            if sid not in self.s["songs"]:
                raise KeyError(sid)
            e = {"qid": new_qid(), "song_id": sid}
            ns = self._singers(singers)
            if ns:
                e["singers"] = ns
            q = self.s["queue"]
            top = self._top()
            # Nothing playing and nothing waiting: the song added plays (as soon as it is ready). Owner, 25 Nov: "without
            # anything playing ... shouldn't it just have started playing as the only item in the queue?"
            if not self._on() and not q[top:]:
                self._auto = True
            q.insert(top if next else len(q) if index is None else max(top, min(len(q), int(index))), e)
            self._start_if_ready()
            self.commit()
            return e

    def _start_if_ready(self) -> None:
        """The two things that wait for a song to be ready (lock held): Play now on one still being prepared (it goes
        on the moment it is ready, whatever is on); and, while nothing is on and the player is waiting (a song added
        to an idle player; the queue ran out with songs still being prepared), the first song to come that is
        ready."""
        if self._cut:
            entry = next((e for e in self.s["queue"] if e["qid"] == self._cut), None)
            song = self.s["songs"].get(entry["song_id"]) if entry else None
            if song is None or song.get("status") == "failed":
                self._cut = None
            elif song.get("status") == "ready":
                self._cut = None
                self._auto = False
                self._start(entry)
                self.commit()
        if self._auto:
            if self._on():
                self._auto = False
            else:
                nxt = self._first_ready()
                if nxt:
                    self._auto = False
                    self._start(nxt, pos_s=-self.lead_in(nxt))
                    self.commit()

    def queue_remove(self, qid: str) -> None:
        """Take one entry out. Only it goes: the songs sung stay sung, the songs to come keep their order. The one
        playing: on to the next ready one, else nothing is on."""
        with self.lock:
            self._take_out(qid)
            self.commit()

    def queue_move(self, qid: str, index: int) -> None:
        """Move one entry to ``index`` of the whole queue. A song to come stays to come (above the top are the songs
        sung: it goes to the top instead); a sung one dropped among the songs to come is to come again; the one that
        is on doesn't move."""
        with self.lock:
            q = self.s["queue"]
            i = self._index(qid)
            if i is None:
                raise KeyError(qid)
            n = self.s["sung"]
            if self._on() and i == n:
                return
            e = q.pop(i)
            t = max(0, min(len(q), int(index)))
            if i < n:
                if t < n:                                # among the songs sung still
                    q.insert(t, e)
                    self.commit()
                    return
                self.s["sung"] -= 1                      # to come again
            q.insert(max(t, self._top()), e)
            self._start_if_ready()                       # (the player waiting, and this one ready: as an add)
            self.commit()

    def queue_clear(self) -> None:
        """Clear the queue (owner, 25 Nov: "a 'clear all' on queue"): all of it, sung and to come, except the song
        playing (or paused) now, so whoever is singing isn't cut off."""
        with self.lock:
            keep = self.current_entry()
            self.s["queue"] = [keep] if keep else []
            self.s["sung"] = 0
            if not keep:
                self._set_player("stopped", None)
            self._auto = False
            self.commit()

    # ---- the player
    @staticmethod
    def _idle_player(rev: int, song_id: str | None = None) -> dict:
        return {"rev": rev, "state": "stopped", "song_id": song_id, "qid": None, "pos_s": 0.0, "at": time.time()}

    def _set_player(self, state: str, entry: dict | None, pos_s: float = 0.0) -> dict:
        p = {"rev": self.s["player"]["rev"] + 1, "state": state if entry else "stopped",
             "song_id": entry["song_id"] if entry else None, "qid": entry["qid"] if entry else None,
             "pos_s": max(-max(LEAD_IN_S, CHANGE_GAP_S), float(pos_s)), "at": time.time()}
        self.s["player"] = p
        if entry and state == "playing":
            self.s["last_song"] = entry["song_id"]      # the background music starts with it
        return p

    def _start_at(self, entry: dict | None, gap: bool = True) -> float:
        """Where a song starts: -its lead-in, and at least CHANGE_GAP_S back while another is sounding (``gap``: a
        person's change; a song that ended by itself has nothing to fade). Lock held."""
        lead = self.lead_in(entry)
        p = self.s["player"]
        if gap and p["state"] == "playing" and self.position() > 0.5:
            lead = max(lead, CHANGE_GAP_S)
        return -lead

    def background(self) -> list[str]:
        """Songs for the music while nothing plays (owner, 28 Nov: "play (with lyrics) the one just done, and then pick
        at random from recent songs list until next a song is chosen"): the one just sung first, then the songs sung
        recently (this queue's, newest first). The display page plays the first, then picks among the rest at random.
        With none sung yet: the ready library, shuffled by the day."""
        with self.lock:
            songs = self.s["songs"]
            ready = {sid for sid, s in songs.items() if s.get("status") == "ready"}
            last = self.s.get("last_song")
            done = self.s["queue"][: self._top()]         # the songs sung, and the one on
            recent: list[str] = []
            for e in reversed(done):
                sid = e["song_id"]
                if sid in ready and sid != last and sid not in recent:
                    recent.append(sid)
        if len(recent) < 2:                             # too few to pick among: the library joins in
            day = time.strftime("%Y-%m-%d")
            rest = sorted(ready - {last} - set(recent), key=lambda sid: hashlib.sha1(f"{day}|{sid}".encode()).hexdigest())
            recent += rest
        first = [last] if last in ready else []
        return (first + recent)[:BACKGROUND_N]

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

    def needed_in(self, sid: str, now: float | None = None) -> float | None:
        """Seconds until the song's next turn in the queue comes (what is left of the one playing, then each ready
        song before it); 0 when nothing plays and it is next; None when it has no turn to come."""
        with self.lock:
            t = 0.0
            cur = self.current_entry()
            if cur:
                dur = (self.s["songs"].get(cur["song_id"]) or {}).get("audio_s") or 0.0
                t = max(0.0, dur - self.position(now))
            for e in self.to_come():
                if e["song_id"] == sid:
                    return t
                if self._ready(e):
                    t += (self.s["songs"].get(e["song_id"]) or {}).get("audio_s") or 0.0
            return None

    def current_entry(self) -> dict | None:
        """The entry that is on (playing or paused)."""
        n = self.s["sung"]
        return self.s["queue"][n] if self._on() and n < len(self.s["queue"]) else None

    def play(self, sid: str | None = None, qid: str | None = None) -> dict:
        """Play a queue entry now (``qid``: that one entry moves to the top and goes on; the one that was on joins
        the songs sung; nothing else moves); or a library song now (``sid``: a new entry, the same); or, with neither,
        carry on: resume a pause, else the first song to come that is ready, else (nothing to come) the last one sung
        again. From the start. A song still being prepared (owner, 28 Nov: the same options on every song) moves to
        the top of the songs to come and goes on the moment it is ready, unless a person plays, stops or skips
        meanwhile."""
        with self.lock:
            p = self.s["player"]
            q = self.s["queue"]
            if qid:
                entry = next((e for e in q if e["qid"] == qid), None)
                if entry is None:
                    raise KeyError(qid)
            elif sid:
                if sid not in self.s["songs"]:
                    raise KeyError(sid)
                entry = None
            else:
                if p["state"] == "playing":
                    return p                                 # already playing: nothing to do
                if p["state"] == "paused":
                    return self.control("resume")
                entry = self._first_ready()
                if entry is None:
                    waiting = next((e for e in self.to_come() if self._status(e) not in ("ready", "failed")), None)
                    if waiting:                              # it plays when it is ready
                        self._auto = True
                        self.commit()
                        raise LookupError(self._not_ready(waiting))
                    n = self.s["sung"]
                    entry = q[n - 1] if n and self._ready(q[n - 1]) else None    # nothing to come: the last one, again
                if entry is None:
                    raise LookupError("Nothing in the queue is ready to play.")
            status = self.s["songs"][sid]["status"] if entry is None else self._status(entry)
            if status == "failed":
                raise LookupError(self._not_ready(entry or {"song_id": sid}))
            self._auto, self._cut = False, None              # the person took over
            if entry is None:                                # a library song: a new entry at the top
                entry = {"qid": new_qid(), "song_id": sid}
                q.insert(self._top(), entry)
            if status != "ready":
                if entry is not self.current_entry():
                    self._to_top(entry)
                self._cut = entry["qid"]
                self.commit()
                return self.s["player"]
            p = self._start(entry)
            self.commit()
            return p

    def _status(self, e: dict) -> str | None:
        return (self.s["songs"].get(e["song_id"]) or {}).get("status")

    def _not_ready(self, e: dict) -> str:
        song = self.s["songs"].get(e["song_id"]) or {}
        return f"“{song.get('title')}” isn't ready yet: {STATUS_WORDS.get(song.get('status'), song.get('status'))}."

    def heard(self, rev: int, song_id: str, pos_s: float) -> bool:
        """Where the page playing the sound has it now (a8's audit, 27 Nov): the player's clock is re-anchored there,
        so the lights' grid and the other screens extrapolate from the real sound, not from this machine's clock
        alone (the laptop's WSL clock ran 7.6% slow: seconds off by a song's end). Same rev, playing, and a page or
        browser output only (a Sonos anchors itself: sonos_out.py); no new rev (the page doesn't re-place)."""
        with self.lock:
            p = self.s["player"]
            if (p.get("rev") != rev or p.get("state") != "playing" or p.get("song_id") != song_id
                    or O.kind(self.output()) not in ("screen", "device")):
                return False
            p["pos_s"], p["at"] = float(pos_s), time.time()
            self.changed.notify_all()
            return True

    def control(self, action: str, pos_s: float | None = None, by: str = "you") -> dict:
        """``by``: who: "you" (the panel), "end" (the queue ran out: tick) or "mode" (the room left karaoke);
        live_view.py reads a stop's."""
        with self.lock:
            p, cur = self.s["player"], self.current_entry()
            if action == "pause" and p["state"] == "playing":
                p = self._set_player("paused", cur, self.position())
            elif action == "resume" and p["state"] == "paused" and cur:
                p = self._set_player("playing", cur, p["pos_s"])
            elif action == "seek" and cur and pos_s is not None:
                dur = (self.s["songs"].get(cur["song_id"]) or {}).get("audio_s") or float("inf")
                p = self._set_player(p["state"], cur, min(float(pos_s), dur))
            elif action == "restart" and cur:
                self._auto, self._cut = False, None          # the person took over
                p = self._start(cur)
            elif action == "stop":
                self._auto, self._cut = False, None
                p = self._halt(by)
            elif action == "next":
                # On to the first song to come that is ready; the one that was on joins the songs sung. Nothing
                # ready: nothing is on, and what is still being prepared plays when it is.
                self._cut = None
                nxt = self._first_ready()
                self._auto = nxt is None
                if nxt:
                    p = self._start(nxt, gap=by == "you")
                elif cur:
                    p = self._halt("end")
            elif action == "prev":
                n = self.s["sung"]
                prv = next((e for e in reversed(self.s["queue"][:n]) if self._ready(e)), None)
                if cur and (self.position() > 3.0 or prv is None):
                    self._auto, self._cut = False, None
                    p = self._start(cur)                     # like any player: back to the start first
                elif prv:
                    # The last one sung comes back and goes on; the one that was on is the first to come again.
                    self._auto, self._cut = False, None
                    at = self._start_at(prv)
                    if cur:
                        self._set_player("stopped", None)
                    p = self._start(prv, pos_s=at)
                else:
                    return p
            else:
                return p
            self.commit()
            return p

    def tick(self, now: float | None = None) -> None:
        """The end of a song: the first song to come that is ready (autoplay), else nothing is on; and the songs that
        wait to be ready start (_start_if_ready). Called every half second."""
        with self.lock:
            self._start_if_ready()
            p = self.s["player"]
            if p["state"] != "playing":
                return
            dur = (self.s["songs"].get(p["song_id"] or "") or {}).get("audio_s")
            if dur and self.position(now) >= dur:
                auto = bool(self.s["settings"].get("autoplay"))
                nxt = self._first_ready() if auto else None
                if nxt:
                    self._start(nxt, gap=False)
                else:
                    self._halt("end")
                    self._auto = auto                    # songs still being prepared play when they are ready
                self.commit()

    def settings(self, **kw: Any) -> dict:
        with self.lock:
            st = self.s["settings"]
            if "guide" in kw:
                st["guide"] = min(1.0, max(0.0, float(kw["guide"])))
            if "autoplay" in kw:
                st["autoplay"] = bool(kw["autoplay"])
            if "audio_delay_ms" in kw:                   # the old setting: now the output's own delay (smsound)
                oid = self.output()
                if O.kind(oid) not in ("screen", "device"):
                    raise ValueError("audio_delay_ms")
                L.set_manual(oid, float(min(5000, max(-2000, int(kw["audio_delay_ms"])))))
            if "screen" in kw:                           # the karaoke screen; None: the deployment's own choice
                v = kw["screen"]
                if v is not None and not (isinstance(v, str) and 0 < len(v) <= 64 and v.replace("-", "").replace("_", "").isalnum()):
                    raise ValueError("screen")
                st["screen"] = v
            if "output" in kw:
                o = kw["output"]
                if o == "screen":                        # the old word: the karaoke screen's page
                    o = None
                if o is not None and O.kind(o) not in KINDS:
                    raise ValueError("output")
                st["output"] = o
            if "child_mode" in kw:
                st["child_mode"] = bool(kw["child_mode"])
            if "muted" in kw:                            # the sound silent, the song and its clock running on
                st["muted"] = bool(kw["muted"])
            if "highlight" in kw:
                if kw["highlight"] not in HIGHLIGHTS:
                    raise ValueError("highlight")
                st["highlight"] = kw["highlight"]
            self.commit()
            return self.settings_view()

    # ---- where the sound plays (smsound)
    def output(self) -> str:
        """The output the sound plays on: the setting, else the karaoke screen's own page (else none)."""
        o = self.s["settings"].get("output")
        if isinstance(o, str) and O.kind(o) in KINDS:
            return o
        from .live_view import karaoke_screen
        scr = karaoke_screen(self.s["settings"].get("screen"))
        return O.SCREEN_PREFIX + scr if scr else O.NONE

    def settings_view(self) -> dict:
        """The settings as the pages and feeds read them: ``output`` resolved, and ``audio_delay_ms`` the delay from
        the player's clock to the room for the lyrics: the screen output's (smsound; null when not measured: the
        display page uses its browser's own figure), 0 on a Sonos (its clock is anchored to what the room hears:
        sonos_out.py) or with no sound."""
        st = dict(self.s["settings"])
        oid = self.output()
        st["output"] = oid
        st["output_set"] = self.s["settings"].get("output") is not None
        st["audio_delay_ms"] = L.effective_ms(oid) if O.kind(oid) in ("screen", "device") else 0
        st["eq"] = EQ.gains(oid) if O.kind(oid) in EQ.APPLIES else None   # the page that plays it shapes the sound
        return st

    def _migrate_output(self) -> None:
        """Before smsound (27 Nov): output "screen" and the delay in settings.audio_delay_ms. The output becomes
        None (the karaoke screen's page, as it meant); a measured delay moves to that screen's output in
        smsound.latency, unless one is there already."""
        st = self.s["settings"]
        if st.get("output") == "screen":
            st["output"] = None
        old = st.pop("audio_delay_ms", None)
        if isinstance(old, (int, float)) and old:
            try:
                oid = self.output()
                if O.kind(oid) == "screen" and not L.sound(oid).get("delay_ms"):
                    L.record(oid, float(old), method="chirp", extra={"note": "karaoke's calibration, moved 27 Nov"})
            except Exception:  # noqa: BLE001 - a deployment without screens or files: the delay is measured again
                pass

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
                    "sung": self.s["sung"],              # queue[:sung] are sung; then the one on (if any); then to come
                    "player": {**self.s["player"], "position_s": round(self.position(), 2)},
                    "settings": self.settings_view(), "calibration": self.calibration, "now": time.time(),
                    "singers": self.singers()}

    def now_playing(self) -> dict:
        """What a display page needs, small enough to poll every second."""
        with self.lock:
            p = dict(self.s["player"])
            song = self.s["songs"].get(p.get("song_id") or "")
            nxt = self._first_ready()
            nsong = self.s["songs"].get(nxt["song_id"]) if nxt else None
            cur = self.current_entry()
            child = bool(self.s["settings"].get("child_mode"))
            return {"player": p, "settings": self.settings_view(),
                    "song": (child_song(public_song(song)) if child else public_song(song)) if song else None,
                    "up_next": {"id": nsong["id"], "title": nsong.get("title"), "artist": nsong.get("artist"),
                                "singers": nxt.get("singers") or []} if nsong else None,
                    "singers": (cur.get("singers") or []) if cur else [],
                    # entries after this one, ready or still being made: the screens ask for more when it is 0
                    "queued_after": len(self.to_come()),
                    "calibration": self.calibration if (self.calibration or {}).get("state") == "running" else None,
                    # the music while nothing plays (song ids, in order): only then
                    "background": self.background() if p.get("state") == "stopped" else [],
                    "now": time.time()}


#: A song's catalogue facts (sources.song_facts / catalog_search): for the library's tags and its sorting. ``rank`` is
#: Deezer's popularity (higher: more played), the cheap proxy for "most popular first".
FACTS = ("genre", "year", "rank", "art", "album")


LEAD_IN_S = 8.0
#: A song changed while another is sounding (Play on another, Next, Prev, Restart) starts at least this far into its
#: silent lead-in, so the one before has faded out first (the display page's FADE_S; owner, 27 Nov: "we should fade
#: out / transition old one before new one starts").
CHANGE_GAP_S = 2.0
#: Music while nothing plays (owner, 27 Nov: "if there's no song selected yet, we should play some (quiet) background
#: music -- maybe replay last song"; 28 Nov: the one just done, then at random from the recent ones): /now's
#: background (Store.background). The display page plays it quietly, vocals in.
BACKGROUND_N = 30


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


def is_duet(s: dict) -> bool:
    """A duet: as a person set it (``duet_set`` true / false), else as karaoke/duet.py found it (``duet.found``)."""
    if isinstance(s.get("duet_set"), bool):
        return s["duet_set"]
    return bool((s.get("duet") or {}).get("found"))


def song_tags(s: dict) -> list[str]:
    """The library's tags for a song: its genre and its decade ("Pop", "1980s"), and "Duets"."""
    tags = []
    if s.get("genre"):
        tags.append(str(s["genre"]))
    if isinstance(s.get("year"), int) and s["year"] > 1900:
        tags.append(f"{s['year'] // 10 * 10}s")
    if is_duet(s):
        tags.append("Duets")
    return tags


def public_song(s: dict) -> dict:
    lyr = s.get("lyrics") or {}
    return {k: s.get(k) for k in ("id", "status", "detail", "error", "title", "artist", "duration", "audio_s", "video_id",
                                  "source_url", "offset_ms", "user_offset_ms", "created_at", "ready_at", "refine") + FACTS} | {
        "lyrics_rev": s.get("lyrics_rev") or 0,
        "lyrics": {"kind": lyr.get("kind"), "lines": len(lyr.get("lines") or [])} if lyr else None,
        "request_kind": (s.get("request") or {}).get("kind"), "tags": song_tags(s), "writers": s.get("writers"),
        "lead_in_s": song_lead_in(s),
        "duet": is_duet(s), "duet_found": s.get("duet"), "duet_set": s.get("duet_set")}
