"""The room's gestures on this renderer: the gesture layer (docs/ENSEMBLE.md §8.2; ff's renderer half, 22 Oct).

show_service publishes each musical moment once as ``room.gesture`` (show_service/contract.py GestureData): a kind
(accent, downbeat, phrase, fill, hit, section, swell, note, chord), its shape, and ``parts``, one per player, each
peaking at ``at_ms``: photon time on show's CLOCK_MONOTONIC, the same clock as time.monotonic() here (one host).  A
re-send with the same ``id`` replaces the earlier one; ``parts: []`` calls it off.  room.direction's ``ensemble``
(EnsembleData) names who plays: ``screens`` maps the renderer's screen name to its player id.

Rules:
  * **Who.** A screen takes only the parts whose player is itself: ``pid = ensemble["screens"][screen]`` from the
    latest room.direction.  Without that mapping it shows nothing (unison: today's screens, unchanged).
  * **When** (:func:`schedule`).  A part peaks on the glass at its ``at_ms``; a frame rendered at ``now`` reaches the
    glass ``lead`` later (the session's render->photon lead, inputs.Sampler.lead, set by Session._update_lead), so
    it starts at at - lead - attack (a swell rises over its ``dur_ms`` from at).  Already inside the lead: it starts
    at once, its attack squeezed into what is left (at least ``late_attack_ms``).  More than one beat late on the
    glass (reactive calls included): not shown, it would be off the beat.  That is timing, not a gate.
  * **Look.** One overlay effect, plugins/effects/gesture_accents.py, draws every kind (config/live/
    gesture_accents.yaml, hot-reloaded).  ``part.strength`` is final.  Light is only ever added: the TVs never dim
    or go black from a gesture.
  * **Where.** One slot at the end of the FX bus (origin ``gesture``, fx_state.GESTURE), on a live session while its
    screen has an ensemble part and parts have come for it within ``keep_s``; :func:`sync_slots` puts it there and
    takes it away.  The FX director never counts, moves or removes it; a person's PUT keeps it as it is.  It degrades
    under load like any effect (degrade.py: scene rate and fps) and never refuses.

Threads: :data:`BOOK` is process-wide (the control's loop thread and a render worker's command reader write it; render
threads read it).  The control feeds it from SessionManager.on_room_message (:func:`on_room`) and forwards room.gesture
to the render workers (workers.WorkerPool.on_gesture), which feed their own copy (worker.Worker._input); room.direction
reaches them already.
"""
from __future__ import annotations

import asyncio
import logging
import math
import pathlib
import threading
import time
import weakref
from typing import Any, Optional

import yaml

log = logging.getLogger("stream_service.gestures")

REPO = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO / "config" / "live" / "gesture_accents.yaml"
PLUGIN = "gesture_accents"         # the overlay effect that draws them (plugins/effects/gesture_accents.py)
LABEL = "Room gestures"            # its slot's name on the bus (page words)
RETAIN_S = 30.0                    # a gesture is forgotten this long after its last peak (or its arrival)
DECAYS = 8.0                       # an envelope ends after this many decays (as show's contract.gesture_weight)
RETRY_S = 30.0                     # a gesture slot that went (failed) is set up again at most this often
CHECK_S = 1.0                      # the config file is looked at at most this often
# Used only when config/live/gesture_accents.yaml leaves a key out (the file's own starting points).
FALLBACK = {"late_attack_ms": 60.0, "no_grid_beat_s": 0.5, "keep_s": 30.0, "fade_s": 1.0, "own_colour": 0.6,
            "max_accents": 24}


def num(d: Any, key: str, default: Optional[float] = None) -> float:
    """``d[key]`` as a finite float, else ``default`` (else FALLBACK[key])."""
    v = d.get(key) if isinstance(d, dict) else None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return float(FALLBACK[key] if default is None else default)
    return float(v)


# -- the config file -----------------------------------------------------------------------------------------------------
class AccentConfig:
    """config/live/gesture_accents.yaml, re-read when it changes (looked at at most once a second, from any thread).
    Absent: {} (no kinds: nothing is drawn).  A file that doesn't read keeps the last good copy."""

    def __init__(self, path: pathlib.Path = CONFIG_PATH):
        self.path = path
        self.cfg: dict = {}
        self.error: Optional[str] = None
        self._mt: Any = -1
        self._checked = -math.inf
        self._lock = threading.Lock()

    def get(self) -> dict:
        now = time.monotonic()
        if now - self._checked < CHECK_S:
            return self.cfg
        with self._lock:
            if now - self._checked < CHECK_S:
                return self.cfg
            self._checked = now
            try:
                mt = self.path.stat().st_mtime_ns
            except OSError:
                mt = None
            if mt != self._mt:
                self._mt = mt
                try:
                    raw = yaml.safe_load(self.path.read_text()) if mt is not None else {}
                    if raw is not None and not isinstance(raw, dict):
                        raise ValueError("the top level must be a mapping")
                    self.cfg, self.error = dict(raw or {}), None
                    log.info("gesture accents: %s", "read" if mt is not None else "no file (nothing drawn)")
                except (OSError, ValueError, yaml.YAMLError) as e:
                    self.error = f"{e}"[:300]
                    log.error("cannot read %s: %s (keeping the last good copy)", self.path, e)
        return self.cfg


CONFIG = AccentConfig()


# -- timing ---------------------------------------------------------------------------------------------------------------
def schedule(at_s: float, now: float, lead: float, attack_s: float, beat_s: float, ease_s: float,
             dur_s: float = 0.0) -> Optional[tuple[float, float]]:
    """(start, peak) on the render clock (the frame's CLOCK_MONOTONIC time) for a part that peaks on the glass at
    ``at_s`` (a swell, ``dur_s`` > 0: rises over dur_s from at_s), a frame reaching the glass ``lead`` after it is
    rendered.  Already inside the lead: (now, its peak), the attack squeezed, never under ``ease_s``.  Its peak
    more than ``beat_s`` late on the glass: None."""
    rise = dur_s if dur_s > 0.0 else max(0.0, attack_s)
    peak = at_s + max(0.0, dur_s) - lead
    start = peak - rise
    if start >= now:
        return start, peak
    ease_s = max(0.0, ease_s)
    if peak - now >= ease_s:
        return now, peak
    if now + ease_s - peak > beat_s:
        return None
    return now, now + ease_s


def weight(t: float, start: float, peak: float, hold_s: float, decay_s: float) -> float:
    """How much of a part shows at ``t`` (0..1): a smooth rise from start to peak, held hold_s, then exp(-t / decay),
    0 after DECAYS decays (show's contract.gesture_weight for a stepped output)."""
    if t < start:
        return 0.0
    if t < peak:
        x = (t - start) / max(1e-6, peak - start)
        return x * x * (3.0 - 2.0 * x)
    if t < peak + hold_s:
        return 1.0
    k = (t - peak - hold_s) / max(0.001, decay_s)
    return 0.0 if k > DECAYS else math.exp(-k)


def ended(t: float, peak: float, hold_s: float, decay_s: float) -> bool:
    return t > peak + hold_s + DECAYS * max(0.001, decay_s)


# -- the book: who plays, and the parts ----------------------------------------------------------------------------------
class GestureBook:
    """The latest ensemble mapping (screen -> player id, and the players with a part now or next) and the room.gesture
    messages still in play.  Any thread; ``rev`` moves on every change a reader could see."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.rev = 0
        self._screens: dict[str, str] = {}
        self._players: frozenset = frozenset()
        self._gestures: dict[str, tuple[dict, float]] = {}    # id -> (GestureData, arrival)
        self._heard: dict[str, float] = {}                    # player id -> arrival of its latest part
        self.counts = {"gestures": 0, "parts": 0, "called_off": 0, "bad": 0}

    def on_direction(self, data: Any) -> None:
        """room.direction: its ``ensemble`` (absent: no mapping, so nothing shows)."""
        ens = data.get("ensemble") if isinstance(data, dict) else None
        screens: dict[str, str] = {}
        players: set = set()
        if isinstance(ens, dict):
            sc = ens.get("screens")
            if isinstance(sc, dict):
                screens = {k: v for k, v in sc.items() if isinstance(k, str) and isinstance(v, str)}
            if isinstance(ens.get("players"), dict):
                players |= set(ens["players"])
            nx = ens.get("next")
            if isinstance(nx, dict) and isinstance(nx.get("players"), dict):
                players |= set(nx["players"])
        fp = frozenset(players)
        with self._lock:
            if screens != self._screens or fp != self._players:
                self._screens, self._players = screens, fp
                self.rev += 1

    def on_gesture(self, data: Any, t: float) -> None:
        """room.gesture (``t``: its arrival, time.monotonic()): kept by id, replaced by a re-send, gone with []."""
        gid = data.get("id") if isinstance(data, dict) else None
        parts = data.get("parts") if isinstance(data, dict) else None
        if not isinstance(gid, str) or not isinstance(parts, list):
            self.counts["bad"] += 1
            return
        with self._lock:
            self._prune(t)
            if not parts:
                if self._gestures.pop(gid, None) is not None:
                    self.counts["called_off"] += 1
            else:
                self._gestures[gid] = (data, t)
                self.counts["gestures"] += 1
                for p in parts:
                    if isinstance(p, dict) and isinstance(p.get("player"), str):
                        self._heard[p["player"]] = t
                        self.counts["parts"] += 1
            self.rev += 1

    def _prune(self, now: float) -> None:
        for gid, (d, t) in list(self._gestures.items()):
            last = t
            for p in d.get("parts") or ():
                try:
                    last = max(last, float(p["at_ms"]) / 1000.0 + float(d.get("dur_ms") or 0.0) / 1000.0)
                except (KeyError, TypeError, ValueError):
                    pass
            if now - last > RETAIN_S:
                del self._gestures[gid]

    def pid(self, screen: Optional[str]) -> Optional[str]:
        with self._lock:
            return self._screens.get(screen) if screen else None

    def parts(self, screen: Optional[str]) -> tuple[int, list[tuple[str, dict, dict]]]:
        """(rev, [(gesture id, GestureData, its part)]) for this screen's player; [] without the mapping."""
        with self._lock:
            pid = self._screens.get(screen) if screen else None
            if pid is None:
                return self.rev, []
            return self.rev, [(gid, d, p) for gid, (d, _) in self._gestures.items() for p in d["parts"]
                              if isinstance(p, dict) and p.get("player") == pid]

    def wants_slot(self, screen: Optional[str], now: float, keep_s: float) -> bool:
        """This screen has an ensemble part and a part came for it within keep_s."""
        with self._lock:
            pid = self._screens.get(screen) if screen else None
            if pid is None or pid not in self._players:
                return False
            t = self._heard.get(pid)
            return t is not None and now - t <= keep_s

    def status(self) -> dict:
        with self._lock:
            return {"rev": self.rev, "screens": dict(self._screens), "players": sorted(self._players),
                    "in_play": len(self._gestures), **self.counts}


BOOK = GestureBook()


# -- the control's side (loop thread) ------------------------------------------------------------------------------------
def on_room(mgr: Any, kind: str, data: dict) -> None:
    """SessionManager.on_room_message: room.gesture and room.direction into this process's book; room.gesture on to
    the render workers (room.direction reaches them already, WorkerPool.on_room_direction).  Never raises."""
    t = time.monotonic()
    try:
        if kind == "room.gesture":
            BOOK.on_gesture(data, t)
            w = getattr(mgr, "workers", None)
            if w is not None:
                w.on_gesture(data, t)
        elif kind == "room.direction":
            BOOK.on_direction(data)
    except Exception:
        log.exception("%s not taken", kind)


_slots: "weakref.WeakKeyDictionary[Any, dict]" = weakref.WeakKeyDictionary()   # session -> {task, on, tried}
_logged = [-math.inf]


def _spec(mgr: Any) -> Any:
    from .presets import EffectSpec
    try:
        cls = mgr.registry.effect(PLUGIN, allow_internal=True)
    except Exception:
        return None                                # not loaded (yet): no slot
    return EffectSpec(cls.name, cls.validate({}), None, LABEL, {})


async def _set(s: Any, on: bool, spec: Any, fade_s: float) -> None:
    try:
        await s.fx.gesture(on, spec, fade_s)
    except Exception as e:                         # the session left live meanwhile, a prepare failed: next tick
        log.info("[%s] gesture slot %s: %s", getattr(s, "id", "?"), "on" if on else "off", e)


def sync_slots(mgr: Any, now: float) -> None:
    """SessionManager.ticker (10 Hz): on each live session, the gesture slot while BOOK.wants_slot(its screen), off
    otherwise.  A slot that went without being taken off (it failed) comes back after RETRY_S.  Never raises."""
    try:
        cfg = CONFIG.get()
        keep, fade = num(cfg, "keep_s"), max(0.0, num(cfg, "fade_s"))
        for s in list(getattr(mgr, "sessions", {}).values()):
            if getattr(s, "state", None) != "live" or getattr(s, "exited", False) or getattr(s, "fx", None) is None:
                continue
            st = _slots.setdefault(s, {"task": None, "on": False, "tried": -math.inf})
            if st["task"] is not None and not st["task"].done():
                continue
            have = any(getattr(x, "origin", None) == "gesture" for x in s.fx.stack)
            want = BOOK.wants_slot(getattr(s, "screen", None), now, keep)
            if want == have:
                st["on"] = have
                continue
            if want:
                if st["on"] and now - st["tried"] < RETRY_S:       # asked for and gone: it failed
                    continue
                spec = _spec(mgr)
                if spec is None:
                    continue
                st["tried"] = now
            else:
                spec = None
            st["on"] = want
            st["task"] = asyncio.ensure_future(_set(s, want, spec, fade))
    except Exception:
        if now - _logged[0] > 60.0:
            _logged[0] = now
            log.exception("gesture slots not synced")
