"""The Music page's player and playlists (8f, 22 Oct; the music SPEC §3): the brain component ``player`` (tick 1 Hz).

**It drives sources.py's test piano** (in-process, on show-brain): ``start_test(PIANO, piece, instrument=I, output=O,
once=True)`` (always naming the instrument and the output: the remembered ones while sources still offers them, else
its default instrument and its first output; the lists are sources.choices()', never ours), ``stop_test(PIANO)`` and
``status()[PIANO]``.

- **Edges, not levels.** The player remembers the run it started (piece, pid once known, the state it last saw and when
  that state began: now - since_s). It advances on that run's change to ``finished`` (once: the run is replaced by the
  next one's), and on ``failed`` it shows the error and skips to the next (a whole pass of failures in a row ends in
  state ``error``). A piece it can't start (gone from the library) is skipped with a word.
- **It never fights.** When the piano's piece, pid or a state's start changes to something the player didn't start
  (TestShow, the Piano card, curl), it steps aside (``stepped_aside``, "Something else started the piano") and touches
  nothing. A stop from elsewhere (state off) stops the player.
- **No auto-repeat.** repeat "off" (default) stops after the last item; "all" wraps; "one" replays the current (Next
  still moves on). Shuffle (off by default) keeps one shuffled order in the queue (the current first), not re-rolled.
- **After a restart it is stopped** (it never plays by itself) but remembers the queue, its place, the sound, the
  output, repeat and shuffle (STATE_DIR/player.json); playlists in STATE_DIR/playlists.json. Written atomically in one
  worker thread (show-brain never waits on a disk), the latest state last.

Routes (show-bus; everything reaches the player through Brain.call): GET/POST /api/v1/playlists, GET/PUT/DELETE
/api/v1/playlists/{id}, GET/POST /api/v1/player (the SPEC's shapes; 400 ``{"error", "detail"}`` in page words).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import math
import pathlib
import random
import secrets
import time
from typing import Any, Callable, Optional

from . import contract
from . import library as libmod

log = logging.getLogger("show.player")

PIANO = next(s for s, p in contract.TEST_SOURCE_PLAYERS.items() if p["kind"] == "midi")
PLAYLISTS_FILE = "playlists.json"
PLAYER_FILE = "player.json"
RUNNING = ("starting", "preparing", "playing")
RESTART_PREV_S = 5.0                   # Back restarts the current piece after this long, else goes to the previous
UPCOMING_N = 5
REPEATS = ("off", "all", "one")
NAME_MAX = 80
ITEMS_MAX = 2000
PLAYLISTS_MAX = 500
STATE_SLACK_S = 1.5                    # the same state starting this much later: someone restarted the piano
ERROR_MAX = 160
STATES = ("stopped", "preparing", "playing", "paused", "stepped_aside", "error")
ASIDE_WORDS = "Something else started the piano"


SPEED_RANGE = (0.25, 4.0)      # sources.SPEED_RANGE: a piece's tempo times this (owner, 23 Oct: "a speed/pacing
                               # control setting (1X obviously the default)")


def _speed(v: Any) -> Optional[float]:
    """A valid playback speed, else None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if SPEED_RANGE[0] <= float(v) <= SPEED_RANGE[1] else None


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None


def _clean_name(name: Any, default: Optional[str] = None) -> str:
    try:
        t = libmod.clean_text(name, "name", NAME_MAX)
    except ValueError:
        raise ValueError(f"bad_name: a name has at most {NAME_MAX} characters") from None
    if t is None:
        if default is None:
            raise ValueError("bad_name: a playlist needs a name")
        return default
    return t


def _clean_items(items: Any) -> list[str]:
    if not isinstance(items, list):
        raise ValueError("bad_items: send the pieces as a list")
    if len(items) > ITEMS_MAX:
        raise ValueError(f"bad_items: a list holds at most {ITEMS_MAX} pieces")
    out = []
    for x in items:
        if libmod.split_id(x) is None:
            raise ValueError("bad_items: one of those isn't a piece from the library")
        out.append(x)
    return out


class Player:
    """Component ``player`` (module docstring). Every method runs on show-brain."""

    name = "player"
    tick_hz = contract.SLOW_TICK_HZ

    def __init__(self, ctx: Optional[contract.BrainContext] = None, *, sources_fn: Optional[Callable[[], Any]] = None,
                 library: Any = None, state_dir: pathlib.Path = contract.STATE_DIR,
                 clock: Callable[[], float] = time.monotonic, rng: Optional[random.Random] = None) -> None:
        """``sources_fn()``: the sources component (None while it is missing); ``library``: library.Library (its
        ``item()`` gives titles; None: pieces show by id)."""
        self.ctx = ctx
        self.sources_fn = sources_fn or (lambda: None)
        self.library = library
        self.state_dir = pathlib.Path(state_dir)
        self.clock = clock
        self.rng = rng or random.Random()
        self.playlists: list[dict] = []
        self.items: list[str] = []            # the queue in its own order
        self.order: list[int] = []            # the play order (indices into items): identity unless shuffled
        self.pos = 0                          # where in ``order`` the current piece is
        self.q_playlist: Optional[str] = None
        self.q_name: Optional[str] = None
        self.instrument: Optional[str] = None
        self.output: Optional[str] = None
        self.speed = 1.0                       # the piece's tempo times this, on every start (1c's start_test speed)
        self.paused_s: Optional[float] = None  # paused: the place in the current piece (its own tempo) to resume at
        self.repeat = "off"
        self.shuffle = False
        self.state = "stopped"
        self.phase: Optional[str] = None      # the piano's own state while ours runs (starting / preparing / playing)
        self.progress: Optional[float] = None
        self.error: Optional[str] = None
        self.note: Optional[str] = None       # the stopped state's words ("Played to the end of the queue")
        self._run: Optional[dict] = None
        self._fails = 0
        self._problems: list[str] = []
        self._io: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self._written: dict[str, str] = {}
        self._snap: dict = {}
        self._loaded = False

    # -- Component -------------------------------------------------------------------------------------------------
    def provides(self) -> dict[str, Any]:
        return {}

    def configure(self, settings: dict) -> None:
        pass

    def restore(self, saved: dict) -> None:
        """state.json holds nothing: player.json and playlists.json are the player's own files."""

    def start(self) -> None:
        self.load()
        self._rebuild()

    def tick(self, now_s: float) -> None:
        self._sync()
        self._rebuild()

    def snapshot(self) -> dict:
        return self._snap

    def persist(self) -> Optional[dict]:
        return None

    def stop(self) -> None:
        """Shutdown: the last writes land (the piano itself is sources.py's to stop)."""
        io, self._io = self._io, None
        if io is not None:
            io.shutdown(wait=True)
        self._save(inline=True)

    # -- files -----------------------------------------------------------------------------------------------------
    def load(self) -> None:
        """playlists.json and player.json; a missing or bad file is empty (said in ``problems``). Always stopped."""
        self._problems = []
        doc = self._read(PLAYLISTS_FILE)
        rows = doc.get("playlists") if isinstance(doc, dict) else None
        self.playlists = []
        for p in rows if isinstance(rows, list) else []:
            if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not isinstance(p.get("name"), str):
                continue
            items = [x for x in p.get("items") or [] if libmod.split_id(x) is not None]
            self.playlists.append({"id": p["id"], "name": p["name"][:NAME_MAX], "items": items[:ITEMS_MAX],
                                   "created_at": p.get("created_at"), "updated_at": p.get("updated_at")})
        doc = self._read(PLAYER_FILE)
        if isinstance(doc, dict):
            q = doc.get("queue") if isinstance(doc.get("queue"), dict) else {}
            items = [x for x in q.get("items") or [] if libmod.split_id(x) is not None][:ITEMS_MAX]
            order = q.get("order")
            if not (isinstance(order, list) and sorted(order) == list(range(len(items)))
                    and all(isinstance(i, int) and not isinstance(i, bool) for i in order)):
                order = list(range(len(items)))
            pos = q.get("index")
            self.items, self.order = items, order
            self.pos = pos if isinstance(pos, int) and not isinstance(pos, bool) and 0 <= pos < len(order) else 0
            self.q_playlist = q.get("playlist") if isinstance(q.get("playlist"), str) else None
            self.q_name = q.get("name") if isinstance(q.get("name"), str) else None
            self.instrument = doc.get("instrument") if isinstance(doc.get("instrument"), str) else None
            self.output = doc.get("output") if isinstance(doc.get("output"), str) else None
            self.repeat = doc.get("repeat") if doc.get("repeat") in REPEATS else "off"
            self.shuffle = doc.get("shuffle") is True
            self.speed = _speed(doc.get("speed")) or 1.0
            if not self.shuffle:
                self.order = list(range(len(self.items)))
        self.state, self._run, self.phase, self.progress = "stopped", None, None, None
        self._loaded = True
        self._written = {PLAYLISTS_FILE: self._dump(self._playlists_doc()), PLAYER_FILE: self._dump(self._player_doc())}

    def _read(self, name: str) -> Any:
        path = self.state_dir / name
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            self._problems.append(f"{name}: not read ({type(e).__name__}: {e}); starting empty")
            log.error("player: %s not read (%s): starting empty", name, e)
            return None

    @staticmethod
    def _dump(doc: dict) -> str:
        return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"

    def _playlists_doc(self) -> dict:
        return {"v": 1, "playlists": self.playlists}

    def _player_doc(self) -> dict:
        return {"v": 1, "queue": {"playlist": self.q_playlist, "name": self.q_name, "items": self.items,
                                  "order": self.order, "index": self.pos},
                "instrument": self.instrument, "output": self.output, "repeat": self.repeat, "shuffle": self.shuffle,
                "speed": self.speed}

    def _save(self, inline: bool = False) -> None:
        """Write what changed: in the worker thread when show-brain's loop runs (FIFO, so the latest lands last),
        else here."""
        jobs = []
        for name, doc in ((PLAYLISTS_FILE, self._playlists_doc()), (PLAYER_FILE, self._player_doc())):
            text = self._dump(doc)
            if self._written.get(name) != text:
                self._written[name] = text
                jobs.append((self.state_dir / name, text.encode("utf-8")))
        if not jobs:
            return
        run_here = inline
        if not run_here:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                run_here = True
        for path, data in jobs:
            if run_here:
                self._write(path, data)
            else:
                if self._io is None:
                    self._io = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="show-player-io")
                self._io.submit(self._write, path, data)

    @staticmethod
    def _write(path: pathlib.Path, data: bytes) -> None:
        try:
            libmod.atomic_write(path, data)
        except OSError:
            log.exception("player: %s not written", path.name)

    # -- the piano (sources.py) ------------------------------------------------------------------------------------
    def _sources(self) -> Any:
        try:
            return self.sources_fn()
        except Exception:                              # noqa: BLE001
            return None

    def _choices(self, src: Any = None) -> dict:
        src = src if src is not None else self._sources()
        fn = getattr(src, "choices", None)
        if callable(fn):
            try:
                ch = fn()
                if isinstance(ch, dict):
                    return {"instruments": list(ch.get("instruments") or []),
                            "default_instrument": ch.get("default_instrument"),
                            "outputs": list(ch.get("outputs") or [])}
            except Exception:                          # noqa: BLE001
                log.debug("player: choices() failed", exc_info=True)
        return {"instruments": [], "default_instrument": None, "outputs": []}

    def _effective(self, ch: dict) -> tuple[Optional[str], Optional[str]]:
        """(instrument, output) to play with: the remembered ones while offered, else sources' default and first."""
        ids = [i.get("id") for i in ch["instruments"] if isinstance(i, dict)]
        outs = [o.get("id") for o in ch["outputs"] if isinstance(o, dict)]
        inst = self.instrument if self.instrument in ids else (ch["default_instrument"] if ch["default_instrument"]
                                                                in ids else (ids[0] if ids else None))
        out = self.output if self.output in outs else (outs[0] if outs else None)
        return inst, out

    def _piano(self) -> Optional[dict]:
        src = self._sources()
        fn = getattr(src, "status", None)
        if not callable(fn):
            return None
        try:
            st = (fn() or {}).get(PIANO)
        except Exception:                              # noqa: BLE001
            log.debug("player: status() failed", exc_info=True)
            return None
        return st if isinstance(st, dict) else None

    def _sync(self) -> None:
        """Follow the run this player started (module docstring): edges only."""
        run = self._run
        if run is None:
            return
        st = self._piano()
        if st is None:
            return
        now = self.clock()
        piece, state, pid = st.get("piece"), st.get("state"), st.get("pid")
        since = _num(st.get("since_s"))
        began = now - since if since is not None else None
        if piece != run["piece"] or (pid is not None and run["pid"] is not None and pid != run["pid"]):
            return self._aside()
        if run["pid"] is None and pid is not None:
            run["pid"] = pid
        seen = run["seen"]
        if (state == seen and began is not None and run["began"] is not None
                and began > run["began"] + STATE_SLACK_S):
            return self._aside()                       # the same state begun again: restarted between our looks
        if seen == "playing" and state in ("starting", "preparing"):
            return self._aside()
        if state != seen:
            run["began"] = began
        run["seen"] = state
        if state == "finished":
            if seen != "finished":
                self._fails = 0
                self.error = None
                log.info("player: finished %s", piece)
                self._advance(auto=True)
            return
        if state == "failed":
            if seen != "failed":
                self._failed(piece, st.get("error"))
            return
        if state not in RUNNING:                       # off / stopping: stopped from elsewhere
            self._run, self.state, self.phase, self.progress = None, "stopped", None, None
            self.note = "Stopped"
            return
        self.phase = state
        if state == "playing":
            if run["playing_at"] is None:
                run["playing_at"] = began if began is not None else now
            self.state, self.error, self.progress = "playing", None, None
        else:
            self.state = "preparing"
            self.progress = _num(st.get("progress")) if state == "preparing" else None

    def _aside(self) -> None:
        log.info("player: stepped aside (%s)", ASIDE_WORDS.lower())
        self._run, self.state, self.phase, self.progress = None, "stepped_aside", None, None

    def _failed(self, piece: str, err: Any) -> None:
        line = ""
        if isinstance(err, str):
            lines = [ln.strip() for ln in err.splitlines() if ln.strip()]
            line = lines[-1][:ERROR_MAX] if lines else ""
        title = self._title(piece)
        self.error = f"{title} couldn't play" + (f": {line}" if line else ".")
        self._fails += 1
        log.warning("player: %s failed (%d in a row): %s", piece, self._fails, line)
        if self._fails >= max(1, len(self.order)):
            self._run, self.state, self.phase, self.progress = None, "error", None, None
            self._save()
            return
        self._advance(auto=True, skip=True)

    def _title(self, piece: Optional[str]) -> str:
        it = self._item(piece) if piece else None
        return str(it.get("title")) if it else "That piece"

    def _start(self, piece: str, start_s: Optional[float] = None) -> dict:
        """start_test for ``piece`` with the effective sound, output and speed (``start_s``: that far into the piece,
        at its own tempo). ValueError (sources'), RuntimeError (none)."""
        src = self._sources()
        if src is None or not callable(getattr(src, "start_test", None)):
            raise RuntimeError("The piano player isn't running.")
        inst, out = self._effective(self._choices(src))
        kw: dict[str, Any] = {}
        if self.speed != 1.0:
            kw["speed"] = self.speed
        if start_s:
            kw["start_s"] = start_s
        try:
            st = src.start_test(PIANO, piece, instrument=inst, output=out, once=True, **kw)
        except TypeError:                              # a sources without speed / start points
            st = src.start_test(PIANO, piece, instrument=inst, output=out, once=True)
        return st if isinstance(st, dict) else {}

    def _pos_s(self) -> Optional[float]:
        """Where in the current piece the piano is (sources' pos_s, at the piece's own tempo), while it's ours."""
        run, st = self._run, self._piano()
        if run is None or st is None or st.get("piece") != run["piece"]:
            return None
        return _num(st.get("pos_s"))

    def _start_current(self, start_s: Optional[float] = None) -> None:
        """Play the piece at ``pos``; a piece sources can't find is skipped (a word in ``error``)."""
        tries = max(1, len(self.order))
        skipped: list[str] = []
        while tries > 0:
            tries -= 1
            piece = self._cur_piece()
            if piece is None:
                self._stopped("Nothing queued")
                return
            try:
                st = self._start(piece, start_s)
            except RuntimeError as e:
                self._run, self.state, self.error = None, "error", str(e)
                self._save()
                return
            except ValueError as e:
                code = str(e).split(":", 1)[0].strip()
                if code != "unknown_piece":
                    self._run, self.state = None, "error"
                    self.error = {"unknown_instrument": "That sound isn't available any more.",
                                  "unknown_output": "That place to play isn't available any more.",
                                  "unknown_source": "The piano player isn't set up."}.get(code, str(e))
                    self._save()
                    return
                skipped.append(self._title(piece))
                if not self._step(1):
                    self.pos = 0
                    self._stopped("Played to the end of the queue", error=self._skipped_words(skipped))
                    return
                continue
            now = self.clock()
            since = _num(st.get("since_s"))
            state = st.get("state")
            # a run that failed at once is seen as starting, so the next look sees its failure as an edge
            self._run = {"piece": piece, "pid": st.get("pid"), "seen": state if state in RUNNING else "starting",
                         "began": now - since if since is not None else now, "playing_at": None}
            self.state = "playing" if state == "playing" else "preparing"
            self.phase = state if state in RUNNING else "starting"
            self.progress = _num(st.get("progress")) if state == "preparing" else None
            if state == "playing":
                self._run["playing_at"] = self._run["began"]
            self.note = None
            if skipped:
                self.error = self._skipped_words(skipped)
            log.info("player: playing %s (%d of %d)", piece, self.pos + 1, len(self.order))
            self._save()
            return
        self._stopped("Nothing in the queue could play", error=self._skipped_words(skipped))

    @staticmethod
    def _skipped_words(titles: list[str]) -> Optional[str]:
        if not titles:
            return None
        return (f"{titles[0]} is no longer in the library: skipped" if len(titles) == 1
                else f"{len(titles)} pieces are no longer in the library: skipped")

    def _stopped(self, note: Optional[str], error: Optional[str] = None) -> None:
        self._run, self.state, self.phase, self.progress = None, "stopped", None, None
        self.note, self.error = note, error
        self._save()

    def _stop_piano(self) -> None:
        """Stop the piano only while it plays the run this player started."""
        run = self._run
        if run is None:
            return
        st = self._piano()
        if st is not None and st.get("piece") == run["piece"] and st.get("state") in RUNNING:
            src = self._sources()
            try:
                try:
                    src.stop_test(PIANO, piece=run["piece"])       # only if it's still ours (23 Oct)
                except TypeError:                      # a sources without the piece guard
                    src.stop_test(PIANO)
            except Exception:                          # noqa: BLE001
                log.exception("player: stop_test failed")

    # -- the queue -------------------------------------------------------------------------------------------------
    def _cur_piece(self) -> Optional[str]:
        if 0 <= self.pos < len(self.order):
            return self.items[self.order[self.pos]]
        return None

    def _step(self, d: int) -> bool:
        """Move ``pos`` by d (+1/-1): wraps with repeat all; False at an end otherwise (pos unchanged)."""
        n = len(self.order)
        if n == 0:
            return False
        p = self.pos + d
        if 0 <= p < n:
            self.pos = p
            return True
        if self.repeat == "all":
            self.pos = p % n
            return True
        return False

    def _advance(self, auto: bool, skip: bool = False) -> None:
        """After a piece (auto) or on Next: repeat one replays (auto only, not a skip); the end of the queue stops."""
        if not self.order:
            self._stopped("Nothing queued")
            return
        if auto and not skip and self.repeat == "one":
            self._start_current()
            return
        if not self._step(1):
            self.pos = 0
            self._stopped("Played to the end of the queue", error=self.error if skip else None)
            return
        self._start_current()

    def _set_queue(self, items: list[str], *, playlist: Optional[str], name: Optional[str], start: int = 0) -> None:
        self.items = list(items)[:ITEMS_MAX]
        n = len(self.items)
        start = max(0, min(n - 1, start)) if n else 0
        if self.shuffle and n:
            rest = [i for i in range(n) if i != start]
            self.rng.shuffle(rest)
            self.order, self.pos = [start] + rest, 0
        else:
            self.order, self.pos = list(range(n)), start
        self.q_playlist, self.q_name = playlist, name
        self._fails = 0

    def _set_shuffle(self, on: bool) -> None:
        if on == self.shuffle:
            return
        self.shuffle = on
        n = len(self.items)
        if not n:
            return
        cur = self.order[self.pos] if 0 <= self.pos < len(self.order) else 0
        if on:
            rest = [i for i in range(n) if i != cur]
            self.rng.shuffle(rest)
            self.order, self.pos = [cur] + rest, 0
        else:
            self.order, self.pos = list(range(n)), cur

    def _insert(self, piece: str, after_current: bool) -> None:
        if len(self.items) >= ITEMS_MAX:
            raise ValueError(f"queue_full: the queue holds at most {ITEMS_MAX} pieces")
        if not self.items:
            self.items, self.order, self.pos = [piece], [0], 0
            return
        if not after_current:
            self.items.append(piece)
            self.order.append(len(self.items) - 1)
            return
        if self.shuffle:
            self.items.append(piece)
            self.order.insert(self.pos + 1, len(self.items) - 1)
        else:
            at = self.order[self.pos] + 1
            self.items.insert(at, piece)
            self.order = list(range(len(self.items)))

    def _known(self, piece: Any) -> str:
        if libmod.split_id(piece) is None or (self.library is not None and self._item(piece) is None):
            raise ValueError("unknown_piece: that piece isn't in the library")
        return piece

    # -- items -----------------------------------------------------------------------------------------------------
    def _item(self, piece: str) -> Optional[dict]:
        lib = self.library
        if lib is None:
            return None
        try:
            return lib.item(piece)
        except Exception:                              # noqa: BLE001
            log.debug("player: library.item failed", exc_info=True)
            return None

    def _item_or_stub(self, piece: Optional[str]) -> Optional[dict]:
        if piece is None:
            return None
        it = self._item(piece)
        if it is not None:
            return it
        return {"id": piece, "title": "No longer in the library" if self.library is not None else piece,
                "kind": libmod.kind_of(piece) or "lib", "pinned": False, "editable": False, "missing": True}

    def _playlist(self, pid: Any) -> dict:
        p = next((p for p in self.playlists if p["id"] == pid), None)
        if p is None:
            raise LookupError("unknown_playlist")
        return p

    def _playlist_view(self, p: dict) -> dict:
        items = []
        for piece in p["items"]:
            it = self._item(piece)
            row: dict = {"piece": piece, "title": it.get("title") if it else "No longer in the library",
                         "missing": it is None}
            for k in ("composer", "genre_label", "duration_s"):
                if it and it.get(k) is not None:
                    row[k] = it[k]
            items.append(row)
        return {"id": p["id"], "name": p["name"], "items": items}

    def _summary(self, p: dict) -> dict:
        total, known = 0.0, False
        for piece in p["items"]:
            d = _num((self._item(piece) or {}).get("duration_s"))
            if d is not None:
                total, known = total + d, True
        return {"id": p["id"], "name": p["name"], "count": len(p["items"]),
                "duration_s": round(total, 1) if known else None}

    # -- API: playlists ----------------------------------------------------------------------------------------------
    def playlists_view(self) -> dict:
        return {"playlists": [self._summary(p) for p in self.playlists]}

    def playlist_get(self, pid: str) -> dict:
        return self._playlist_view(self._playlist(pid))

    def playlist_create(self, name: Any = None, items: Any = None) -> dict:
        if len(self.playlists) >= PLAYLISTS_MAX:
            raise ValueError(f"too_many: at most {PLAYLISTS_MAX} playlists")
        nm = _clean_name(name, "New playlist")
        its = _clean_items(items) if items is not None else []
        now = libmod._now_iso()
        p = {"id": f"pl-{secrets.token_hex(4)}", "name": nm, "items": its, "created_at": now, "updated_at": now}
        self.playlists.append(p)
        self._save()
        self._rebuild()
        return self._playlist_view(p)

    def playlist_put(self, pid: str, name: Any = None, items: Any = None, *, fields: Optional[set] = None) -> dict:
        p = self._playlist(pid)
        fields = fields if fields is not None else {k for k, v in (("name", name), ("items", items)) if v is not None}
        if "name" in fields:
            p["name"] = _clean_name(name)
            if self.q_playlist == pid:
                self.q_name = p["name"]
        if "items" in fields:
            p["items"] = _clean_items(items)
        p["updated_at"] = libmod._now_iso()
        self._save()
        self._rebuild()
        return self._playlist_view(p)

    def playlist_delete(self, pid: str) -> dict:
        p = self._playlist(pid)
        self.playlists.remove(p)
        if self.q_playlist == pid:                      # the queue stays; it just isn't that playlist any more
            self.q_playlist, self.q_name = None, p["name"]
        self._save()
        self._rebuild()
        return {"deleted": pid}

    # -- API: the player ---------------------------------------------------------------------------------------------
    def view(self) -> dict:
        """GET /api/v1/player (PlayerView)."""
        self._sync()
        ch = self._choices()
        inst, out = self._effective(ch)
        cur = self._cur_piece()
        nxt_pos = self._next_pos()
        upcoming = []
        n = len(self.order)
        p = self.pos
        for _ in range(min(UPCOMING_N, max(0, n - 1))):
            p += 1
            if p >= n:
                if self.repeat != "all":
                    break
                p = 0
            if p == self.pos:
                break
            upcoming.append(self._item_or_stub(self.items[self.order[p]]))
        pl = next((x for x in self.playlists if x["id"] == self.q_playlist), None)
        cur_item = self._item_or_stub(cur)
        run = self._run
        elapsed = None
        if self.state == "paused" and self.paused_s is not None:
            elapsed = round(self.paused_s, 1)
        elif run is not None and self.state == "playing":
            pos = self._pos_s()                        # the place in the piece at its own tempo (any speed)
            if pos is not None:
                elapsed = round(pos, 1)
            elif run.get("playing_at") is not None:
                elapsed = round(max(0.0, self.clock() - run["playing_at"]) * self.speed, 1)
        name = pl["name"] if pl else (self.q_name or ("Queue" if self.items else None))
        return {"state": self.state, "words": self._words(),
                "queue": {"playlist": {"id": pl["id"], "name": pl["name"]} if pl else None, "name": name,
                          "count": n, "index": self.pos if n else 0},
                "current": cur_item, "next": self._item_or_stub(self.items[self.order[nxt_pos]])
                if nxt_pos is not None else None,
                "upcoming": upcoming, "progress": self.progress if self.state == "preparing" else None,
                "elapsed_s": elapsed, "duration_s": _num((cur_item or {}).get("duration_s")),
                "instrument": inst, "output": out, "repeat": self.repeat, "shuffle": self.shuffle,
                "speed": self.speed, "error": self.error, "choices": ch,
                # when elapsed_s was true (wall ms): the score page carries it on between polls at ``speed``
                "at_ms": int(time.time() * 1000)}

    def _next_pos(self) -> Optional[int]:
        n = len(self.order)
        if not n:
            return None
        if self.repeat == "one":
            return self.pos
        if self.pos + 1 < n:
            return self.pos + 1
        return 0 if self.repeat == "all" else None

    def _words(self) -> str:
        if self.state == "playing":
            return "Playing"
        if self.state == "preparing":
            if self.phase == "preparing":
                return "Getting ready"         # loading the sound (1c, 22 Oct: the piano renders as it plays)
            return "Starting"
        if self.state == "paused":
            return "Paused"
        if self.state == "stepped_aside":
            return f"Stepped aside: {ASIDE_WORDS[0].lower()}{ASIDE_WORDS[1:]}"
        if self.state == "error":
            return self.error or "Something went wrong"
        if not self.items:
            return "Nothing queued"
        return self.note or "Stopped"

    def act(self, body: Any) -> dict:
        """POST /api/v1/player: one action (the SPEC's), then the view. ValueError("<code>: <words>")."""
        if not isinstance(body, dict) or not isinstance(body.get("action"), str):
            raise ValueError('bad_request: send {"action": "play" | "stop" | "pause" | "resume" | "seek" | "next" | '
                             '"prev" | "add" | "play_next" | "set", ...}')
        a = body["action"]
        self._sync()
        if a == "play":
            self._play(body)
        elif a == "stop":
            self._stop_piano()
            self.paused_s = None
            self._stopped(None)
        elif a == "seek":
            self._seek(body)
        elif a == "pause":
            self._pause()
        elif a == "resume":
            self._resume()
        elif a == "next":
            self.paused_s = None
            self._next()
        elif a == "prev":
            self.paused_s = None
            self._prev()
        elif a in ("add", "play_next"):
            piece = self._known(body.get("piece"))
            self._insert(piece, after_current=a == "play_next")
            self._save()
        elif a == "set":
            self._set(body)
        else:
            raise ValueError(f"bad_action: there's no action called “{a}”")
        self._rebuild()
        return self.view()

    def _index(self, body: dict) -> int:
        i = body.get("index", 0)
        if i is None:
            return 0
        if isinstance(i, bool) or not isinstance(i, int) or i < 0:
            raise ValueError("bad_index: index is a position from 0")
        return i

    def _start_s(self, body: dict) -> Optional[float]:
        """A request's ``start_s`` (seconds into the piece at its own tempo), None when absent."""
        v = body.get("start_s")
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise ValueError("bad_start: start_s is a number of seconds into the piece")
        return float(v)

    def _play(self, body: dict) -> None:
        start_s = self._start_s(body)
        bare = all(body.get(k) is None for k in ("playlist", "piece", "items")) and "index" not in body
        if bare and start_s is None and self.state == "paused" and self._cur_piece() is not None:
            start_s = self.paused_s                    # Play on a paused piece carries on from where it was
        self.paused_s = None
        self._stop_piano_quiet()
        if body.get("playlist") is not None:
            try:
                p = self._playlist(body["playlist"])
            except LookupError:
                raise ValueError("unknown_playlist: that playlist has gone") from None
            if not p["items"]:
                raise ValueError("empty_playlist: that playlist has nothing in it yet")
            self._set_queue(p["items"], playlist=p["id"], name=p["name"], start=self._index(body))
        elif body.get("piece") is not None:
            self._set_queue([self._known(body["piece"])], playlist=None, name=None)
        elif body.get("items") is not None:
            items = _clean_items(body["items"])
            if not items:
                raise ValueError("bad_items: send at least one piece")
            nm = libmod.clean_text(body.get("name"), "name", NAME_MAX) if body.get("name") is not None else None
            self._set_queue(items, playlist=None, name=nm, start=self._index(body))
        else:
            if not self.order:
                raise ValueError("nothing_queued: nothing is queued: pick a piece or a playlist")
            if "index" in body:
                i = self._index(body)
                if i >= len(self.order):
                    raise ValueError("bad_index: the queue isn't that long")
                self.pos = i
        self._fails = 0
        self.error = None
        self._start_current(start_s)

    def _seek(self, body: dict) -> None:
        """Carry on with the current piece from ``start_s`` (playing, paused or stopped: it plays from there)."""
        start_s = self._start_s(body)
        if start_s is None:
            raise ValueError("bad_start: say where: start_s, seconds into the piece")
        if self._cur_piece() is None:
            raise ValueError("nothing_queued: nothing is queued: pick a piece or a playlist")
        self.paused_s = None
        self._stop_piano_quiet()
        self._fails, self.error = 0, None
        self._start_current(start_s)

    def _pause(self) -> None:
        """Stop the piano but keep the piece and its place (``paused``); Resume (or Play) carries on from there."""
        if self.state not in ("playing", "preparing"):
            return
        pos = self._pos_s()
        if pos is None and self._run is not None and self._run.get("playing_at") is not None:
            pos = max(0.0, self.clock() - self._run["playing_at"]) * self.speed
        self._stop_piano()
        self._run, self.phase, self.progress = None, None, None
        self.state, self.paused_s, self.note = "paused", round(pos or 0.0, 3), None
        log.info("player: paused %s at %.1f s", self._cur_piece(), self.paused_s)
        self._save()

    def _resume(self) -> None:
        if self.state != "paused" or self._cur_piece() is None:
            if self.state in ("playing", "preparing"):
                return
            if not self.order:
                raise ValueError("nothing_queued: nothing is queued: pick a piece or a playlist")
        start_s, self.paused_s = (self.paused_s if self.state == "paused" else None), None
        self._fails, self.error = 0, None
        self._start_current(start_s)

    def _stop_piano_quiet(self) -> None:
        """Before starting something new: nothing to stop (start_test switches the piano itself)."""
        self._run = None

    def _next(self) -> None:
        active = self.state in ("playing", "preparing")
        if not self._step(1):
            if active:
                self._stop_piano()
            self.pos = 0
            self._stopped("Played to the end of the queue")
            return
        if active:
            self._start_current()
        else:
            self._save()

    def _prev(self) -> None:
        active = self.state in ("playing", "preparing")
        run = self._run
        played = (self.clock() - run["playing_at"]) if run is not None and run.get("playing_at") is not None else 0.0
        if not (active and played > RESTART_PREV_S):
            self._step(-1)                             # at the start (repeat off): the first piece again
        if active:
            self._start_current()
        else:
            self._save()

    def _set(self, body: dict) -> None:
        ch = self._choices()
        restart = False
        if "instrument" in body and body["instrument"] is not None:
            ids = [i.get("id") for i in ch["instruments"] if isinstance(i, dict)]
            if body["instrument"] not in ids:
                raise ValueError("unknown_instrument: that sound isn't available")
            restart |= body["instrument"] != self._effective(ch)[0]
            self.instrument = body["instrument"]
        if "output" in body and body["output"] is not None:
            outs = [o.get("id") for o in ch["outputs"] if isinstance(o, dict)]
            if body["output"] not in outs:
                raise ValueError("unknown_output: that place to play isn't available")
            restart |= body["output"] != self._effective(ch)[1]
            self.output = body["output"]
        if "repeat" in body and body["repeat"] is not None:
            if body["repeat"] not in REPEATS:
                raise ValueError("bad_repeat: repeat is off, all or one")
            self.repeat = body["repeat"]
        if "shuffle" in body and body["shuffle"] is not None:
            if not isinstance(body["shuffle"], bool):
                raise ValueError("bad_shuffle: shuffle is on or off")
            self._set_shuffle(body["shuffle"])
        if "speed" in body and body["speed"] is not None:
            sp = _speed(body["speed"])
            if sp is None:
                raise ValueError("bad_speed: the speed is between 0.25 and 4 times")
            restart |= sp != self.speed
            self.speed = sp
        if restart and self.state in ("playing", "preparing"):
            # the new sound, place or speed, carrying on from where the piece was (1c: start_s = its pos_s)
            self._start_current(self._pos_s())
        else:
            self._save()

    # -- snapshot --------------------------------------------------------------------------------------------------
    def _rebuild(self) -> None:
        self._snap = {"state": self.state, "piece": self._cur_piece(), "index": self.pos, "count": len(self.order),
                      "playlist": self.q_playlist, "repeat": self.repeat, "shuffle": self.shuffle,
                      "error": self.error, "run": dict(self._run) if self._run else None,
                      "playlists": len(self.playlists), "problems": list(self._problems)}


def attach(ctx: contract.BrainContext, **kw: Any) -> Player:
    """The brain's ``player``. __main__ passes ``sources_fn``, ``library`` and ``state_dir``."""
    return Player(ctx, **kw)


# -- routes (show-bus) -------------------------------------------------------------------------------------------------
STARTING = "The show is starting; try again in a moment."
NO_PLAYER = "The player isn't running."
BUSY = "The show is busy; try again in a moment."


def routes(app: Any, brain_ref: Callable[[], Any], name: str = "player") -> None:
    """Mount the playlists and player routes; each reaches the player through ``brain_ref().call``."""
    def wrap(fn: Callable) -> Callable:
        async def handler(request: Any) -> Any:
            try:
                brain = brain_ref()
            except Exception:                          # noqa: BLE001
                brain = None
            if brain is None or getattr(brain, "running", True) is not True:
                return libmod._err(503, "starting", STARTING)
            comps = getattr(brain, "components", None)
            player = comps.get(name) if isinstance(comps, dict) else None
            if player is None:
                return libmod._err(503, "unavailable", NO_PLAYER)
            try:
                return libmod._json(await fn(request, brain, player))
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                return libmod._err(503, "busy", BUSY)
            except LookupError as e:
                if isinstance(e, KeyError):
                    log.exception("player: %s %s failed", request.method, request.path)
                    return libmod._err(500, "failed", "Something went wrong (KeyError); the show carries on.")
                return libmod._err(404, "unknown_playlist", "That playlist has gone.")
            except ValueError as e:
                code, _, detail = str(e).partition(":")
                code = code.strip() if code.strip().replace("_", "").isalpha() else "bad_request"
                detail = detail.strip()
                return libmod._err(400, code, (detail[:1].upper() + detail[1:]) if detail else
                                   "That request wasn't understood.")
            except RuntimeError as e:
                if "brain_not_running" in str(e):
                    return libmod._err(503, "starting", STARTING)
                log.exception("player: %s %s failed", request.method, request.path)
                return libmod._err(500, "failed", f"Something went wrong ({type(e).__name__}); the show carries on.")
            except Exception as e:                     # noqa: BLE001
                log.exception("player: %s %s failed", request.method, request.path)
                return libmod._err(500, "failed", f"Something went wrong ({type(e).__name__}); the show carries on.")
        return handler

    async def list_(request: Any, brain: Any, pl: Player) -> Any:
        return await brain.call(pl.playlists_view)

    async def create(request: Any, brain: Any, pl: Player) -> Any:
        body = await libmod.read_json(request)
        body = body if isinstance(body, dict) else {}
        return await brain.call(pl.playlist_create, body.get("name"), body.get("items"))

    async def get(request: Any, brain: Any, pl: Player) -> Any:
        return await brain.call(pl.playlist_get, request.match_info["id"])

    async def put(request: Any, brain: Any, pl: Player) -> Any:
        body = await libmod.read_json(request)
        if not isinstance(body, dict):
            raise ValueError('bad_request: send {"name"?, "items"?}')
        fields = {k for k in ("name", "items") if k in body}
        return await brain.call(lambda: pl.playlist_put(request.match_info["id"], body.get("name"),
                                                        body.get("items"), fields=fields))

    async def delete(request: Any, brain: Any, pl: Player) -> Any:
        return await brain.call(pl.playlist_delete, request.match_info["id"])

    async def view(request: Any, brain: Any, pl: Player) -> Any:
        return await brain.call(pl.view)

    async def act(request: Any, brain: Any, pl: Player) -> Any:
        body = await libmod.read_json(request)
        return await brain.call(pl.act, body)

    r = app.router
    r.add_get("/api/v1/playlists", wrap(list_))
    r.add_post("/api/v1/playlists", wrap(create))
    r.add_get("/api/v1/playlists/{id}", wrap(get))
    r.add_put("/api/v1/playlists/{id}", wrap(put))
    r.add_delete("/api/v1/playlists/{id}", wrap(delete))
    r.add_get("/api/v1/player", wrap(view))
    r.add_post("/api/v1/player", wrap(act))
