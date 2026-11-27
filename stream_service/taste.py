"""Scene taste from live operator feedback: what the people in the room do with the scenes the director picks.

The owner's rule (29 Oct): the art director must work with whatever it gets (MIDI, the mic, the AI listener, Sonos
metadata, scene and effect ratings, live operator feedback) and do its best in most circumstances.  The stars
(ratings.py) are the owner's considered view in the Library; this is the quick, everyday one, read from the presses
people make anyway on the Room page (screen-machine-63, 29 Oct).  Three signals, hooked in sessions.py:

  skip  -1    a person pressed Next while a scene the director picked had been on screen under SKIP_S
  like  +1    a person cued a named scene by hand
  keep  +0.5  a scene the director picked stayed on screen while the room held the look (Keep this look) for
              KEEP_S in all; once per showing
  good  +1.5  a person pressed "This is good" on the Room page (owner, 29 Oct: "right now, this is good", so it
              comes up again): the scene's plug-in and its preset (scene_key), each effect on the bus (fx_key) and
              each scene + effect pair (pair_key); the FX director multiplies an effect's weight by its own and its
              pair's with the scene on screen (fx_multiplier, at most FX_MULT_MAX)

Each lands on the scene's plug-in (its ``source``, the same key as the stars) twice: in the context it happened in
(the effective direction's genre word, else "-") and in the plug-in's context-free total.  Every score halves every
HALF_LIFE_DAYS, so a skip last month counts for little and the store forgets what nobody repeats.  The auto-director
multiplies a scene's weight (beside the stars) by

    clamp(exp(K * (ctx + TOTAL_SHARE * total)), MULT_MIN, MULT_MAX)

so one skip in a genre is x0.59 there and x0.84 in the others, one like x1.69 there (two reach the ceiling); never
below x0.4 or above x2.5 (soft: a skipped plug-in is rarer, never gone; owner, "degrade, never refuse").  One press that reaches every
screen of a room (the Room page cues each screen for a scene by hand) counts once (DEDUPE_S).

One store per SessionManager, shared by its sessions (like the ratings), persisted as JSON in the service's state
dir (STATE_DIR: $STREAM_STATE_DIR, else ~/.local/state/screen-machine/stream; never in git), written atomically
(tmp + rename) off the loop thread.  Pure arithmetic apart from that file.  ``GET /dev/taste`` shows it.
"""
from __future__ import annotations

import asyncio
import collections
import datetime as dt
import json
import logging
import math
import os
import pathlib
import threading
import time
from typing import Any, Optional

log = logging.getLogger("stream_service.taste")

STATE_DIR = pathlib.Path(os.environ.get("STREAM_STATE_DIR") or "~/.local/state/screen-machine/stream").expanduser()
FILE_NAME = "taste.json"

# Starting points (screen-machine-63, 29 Oct): to be tuned from what the room does with them.
SIGNALS = {"skip": -1.0, "like": 1.0, "keep": 0.5, "good": 1.5,
           "ungood": -1.5}         # a "This is good" taken back (owner, 27 Nov: "there's no way to undo")
SKIP_S = 60.0              # Next on a scene the director picked, sooner than this: a skip (later: it had its turn)
KEEP_S = 120.0             # held (Keep this look) this long in all on one showing: a keep
HALF_LIFE_DAYS = 14.0
K = 0.35                   # exp(K * score): one skip in its own context x0.59 (with the total's half), one like x1.69
TOTAL_SHARE = 0.5          # how much the context-free total adds to a context's own score
MULT_MIN, MULT_MAX = 0.4, 2.5
FX_MULT_MAX = 4.0          # an effect's own taste x its pair's with the scene on screen, at most this
DEDUPE_S = 10.0            # the same signal for the same plug-in this soon again: one press on several screens
RECENT_N = 30              # the last signals kept for GET /dev/taste
PRUNE_BELOW = 0.01         # a score decayed below this is forgotten when the file is written
NO_CONTEXT = "-"

HALF_LIFE_S = HALF_LIFE_DAYS * 86400.0


def scene_key(preset: str) -> str:
    """The store's key for a scene preset (beside its plug-in's, which is the plug-in's name)."""
    return f"scene:{preset}"


def fx_key(preset: str) -> str:
    return f"fx:{preset}"


def pair_key(scene: str, fx: str) -> str:
    """A scene preset and an effect preset liked together."""
    return f"pair:{scene}|{fx}"


def context_of(direction: Optional[dict]) -> str:
    """The context a signal lands in: the effective direction's genre word, else "-"."""
    g = (direction or {}).get("genre")
    return g.strip().lower()[:40] if isinstance(g, str) and g.strip() else NO_CONTEXT


def decayed(entry: Any, now: float) -> float:
    """[score, wall t] -> the score now (HALF_LIFE_DAYS half-life); anything malformed is 0."""
    try:
        score, t = float(entry[0]), float(entry[1])
    except (TypeError, ValueError, IndexError):
        return 0.0
    if not (math.isfinite(score) and math.isfinite(t)):
        return 0.0
    return score * 0.5 ** (max(0.0, now - t) / HALF_LIFE_S)


def multiplier_of(ctx: float, total: float) -> float:
    """The weight multiplier for a context's score and the plug-in's total (see the module doc)."""
    return min(MULT_MAX, max(MULT_MIN, math.exp(K * (ctx + TOTAL_SHARE * total))))


class Taste:
    """The store: {plug-in: {"total": [score, t], "ctx": {context: [score, t]}}}, the signal counts and the last
    few signals.  Thread-safe (the loop records and reads; a worker thread writes the file)."""

    def __init__(self, path: Optional[pathlib.Path] = None):
        self.path = pathlib.Path(path) if path is not None else None     # None: in memory only
        self.lock = threading.Lock()
        self._write_lock = threading.Lock()
        self.plugins: dict[str, dict] = {}
        self.counts: dict[str, int] = {k: 0 for k in SIGNALS}
        self.recent: collections.deque = collections.deque(maxlen=RECENT_N)
        self._last: dict[tuple[str, str], float] = {}     # (signal, plug-in) -> wall t (DEDUPE_S; not kept)
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError) as e:
            log.warning("taste: %s unreadable (%s); starting empty", self.path, e)
            return
        if not isinstance(raw, dict):
            return
        for p, e in (raw.get("plugins") or {}).items():
            if isinstance(p, str) and isinstance(e, dict):
                ctx = e.get("ctx") if isinstance(e.get("ctx"), dict) else {}
                self.plugins[p] = {"total": e.get("total") or [0.0, 0.0],
                                   "ctx": {str(c): v for c, v in ctx.items() if isinstance(v, list)}}
        for k, v in (raw.get("counts") or {}).items():
            if k in self.counts and isinstance(v, int) and not isinstance(v, bool):
                self.counts[k] = v
        self.recent.extend(r for r in (raw.get("recent") or [])[-RECENT_N:] if isinstance(r, dict))

    # -- the director's side ---------------------------------------------------------------------------------
    def multiplier(self, plugin: Optional[str], context: str = NO_CONTEXT, now: Optional[float] = None) -> float:
        """How much the auto-director favours this plug-in here and now: 1.0 with no signals."""
        if not plugin:
            return 1.0
        with self.lock:
            e = self.plugins.get(plugin)
            if e is None:
                return 1.0
            now = time.time() if now is None else now
            return multiplier_of(decayed(e["ctx"].get(context), now), decayed(e["total"], now))

    # -- the room's side -------------------------------------------------------------------------------------
    def record(self, signal: str, plugin: Optional[str], context: str = NO_CONTEXT, *, scene: Optional[str] = None,
               now: Optional[float] = None) -> bool:
        """One signal for a plug-in in a context; -> False when it was not counted (no plug-in, an unknown signal,
        or the same one again within DEDUPE_S).  Saved soon after, off the loop thread."""
        delta = SIGNALS.get(signal)
        if not plugin or delta is None:
            return False
        now = time.time() if now is None else now
        with self.lock:
            last = self._last.get((signal, plugin))
            if last is not None and 0.0 <= now - last < DEDUPE_S:
                return False
            self._last[(signal, plugin)] = now
            e = self.plugins.setdefault(plugin, {"total": [0.0, now], "ctx": {}})
            e["total"] = [round(decayed(e["total"], now) + delta, 4), now]
            e["ctx"][context] = [round(decayed(e["ctx"].get(context), now) + delta, 4), now]
            self.counts[signal] = self.counts.get(signal, 0) + 1
            self.recent.append({"at": _iso(now), "signal": signal, "plugin": plugin, "scene": scene,
                                "context": context, "delta": delta})
        log.info("taste: %s %s (%s, %s) -> x%.2f", signal, plugin, scene or "-", context,
                 self.multiplier(plugin, context, now))
        self._save_soon()
        return True

    def view(self, now: Optional[float] = None) -> dict:
        """GET /dev/taste: each plug-in's scores now, its multiplier per context and with none of its own."""
        now = time.time() if now is None else now
        with self.lock:
            plugins = {}
            for p, e in sorted(self.plugins.items()):
                tot = decayed(e["total"], now)
                ctx = {c: round(decayed(v, now), 3) for c, v in sorted(e["ctx"].items())}
                plugins[p] = {"total": round(tot, 3), "ctx": ctx,
                              "multiplier": {c: round(multiplier_of(s, tot), 3) for c, s in ctx.items()},
                              "multiplier_elsewhere": round(multiplier_of(0.0, tot), 3)}
            return {"file": None if self.path is None else str(self.path), "half_life_days": HALF_LIFE_DAYS,
                    "signals": dict(SIGNALS), "skip_s": SKIP_S, "keep_s": KEEP_S, "counts": dict(self.counts),
                    "plugins": plugins, "recent": list(self.recent)[::-1]}

    def summary(self) -> dict:
        with self.lock:
            return {"counts": dict(self.counts), "plugins": len(self.plugins)}

    # -- the file ----------------------------------------------------------------------------------------------
    def _snapshot(self, now: float) -> dict:
        with self.lock:
            plugins = {}
            for p, e in self.plugins.items():
                ctx = {c: v for c, v in e["ctx"].items() if abs(decayed(v, now)) >= PRUNE_BELOW}
                if ctx or abs(decayed(e["total"], now)) >= PRUNE_BELOW:
                    plugins[p] = {"total": e["total"], "ctx": ctx}
            return {"version": 1, "half_life_days": HALF_LIFE_DAYS, "counts": dict(self.counts),
                    "plugins": dict(sorted(plugins.items())), "recent": list(self.recent)}

    def save(self, now: Optional[float] = None) -> None:
        """Write the store atomically (tmp + rename): a crash never leaves half a file."""
        if self.path is None:
            return
        data = json.dumps(self._snapshot(time.time() if now is None else now), indent=1, sort_keys=True) + "\n"
        with self._write_lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(data)
                tmp.replace(self.path)
            except OSError as e:
                log.warning("taste: could not write %s (%s)", self.path, e)

    def _save_soon(self) -> None:
        if self.path is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.save()
            return
        loop.run_in_executor(None, self.save)


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def default_store() -> Taste:
    """The service's store, in STATE_DIR (read at call time, so tests can point it elsewhere)."""
    return Taste(STATE_DIR / FILE_NAME)
