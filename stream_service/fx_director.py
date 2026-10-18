"""The FX director: adds, removes or swaps one master-bus effect on its own cadence (EFFECTS_LAYER_DESIGN D.4).

Pure state + decisions (no GPU, no I/O), on the asyncio loop thread like director.py.  ``auto.fx`` says whether
it may act (``enabled``, default false), over which effect presets (``pool``) and how many bus effects it may
own (``max``); ``direction.fx`` says how many of those to run (``density``) and how often to change them
(``rate``: 0 -> about every 90 s, 1 -> about every 15 s, log-linear, jitter x U(0.75, 1.25)), optionally which
stages and moods.  Only slots it added (``origin: "auto"``) are counted and touched: manual slots stay and count
toward the bus limit and the cost budget.  At most one effect per stage; the summed bench cost of the bus
stays within ``FX_BUS_BUDGET_MS`` (4K-equivalent ms, scaled to the session's pixels).  Weights follow the scene
director: tags.weight x energy match x mood match x style match (each mismatch x 0.3), x 0.25 for the last few
picks (novelty).  It is armed only while the session is live with the bus open, never after exit, and the
session never applies a move while a scene transition is in flight.  ``vet(kind, plug-in)`` (the session's vetted
pool) drops effects that are unvetted, slow or broken.

On the music (owner, 17 Oct): while the session's beat clock is sure (``clock``, set by the session: (beat s, beats
per bar)), the cadence is whole phrases (FX_BARS bars, FX_MIN_BARS .. FX_MAX_BARS) and fades are whole beats
(FX_FADE_BEATS); the session lands each move on the next downbeat.  show_service's room.events steer it
(:meth:`on_event`, by ``direction.fx.follow``, 0 = never): a drop or a new track brings a move at once (a drop's
effect fades in over a beat, and a drop always has at least one), a section change sometimes, and a breakdown
thins the bus by one auto effect until the music lifts again (a drop, a section change or a new track).

Scenes and effects together (docs/SCENE_FX_INTERPLAY.md, fx_interplay.py; ``looks`` and ``scene`` set by the
session): each candidate's weight is also x its fit with the scene on screen (its role against the scene's measured
busyness, motion, brightness, symmetry, colour source), with the other effects on the bus and with the music's
genre, and the retag's per-preset guess counts only as weight ** tag_prior; the effect comes in at a level the
music's load budget and the scene leave it (never below level_min).  One layer moves at a time: a section change
waits SECTION_WAIT_S for the scene director, and when the scene changes the carried auto effects are re-checked
against the new scene (the one that fits worst is swapped once its fit fell below refit_below of what it was).
"""
from __future__ import annotations

import collections
import math
import random
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .director import AUTO_FX_DEFAULTS, FX_DIRECTION_DEFAULTS, resolve_pool
from .effects import MAX_BUS_SLOTS
from .fx_interplay import Fit
from .plugins.base import STAGES
from .tags import match_weight

FX_INTERVAL_AT_0, FX_INTERVAL_AT_1 = 90.0, 15.0
FX_FADE_AT_0, FX_FADE_AT_1 = 6.0, 2.0
FX_BUS_BUDGET_MS, FX_UNKNOWN_COST_MS = 3.0, 1.5
NOVELTY_WINDOW = 4
NOVELTY_WEIGHT = 0.25
SWAP_P = 0.6
QUARANTINE_S = 600.0
FX_DESCRIPTORS = ("energy", "moods", "style", "genre", "music_moods", "music_style")
FX_BARS = 4                    # on the clock: a scheduled move comes after whole phrases of this many bars ...
FX_MIN_BARS, FX_MAX_BARS = 4, 64
FX_FADE_BEATS = (1, 2, 4, 8, 16)   # ... and a fade lasts whole beats (the nearest to the rate's seconds)
FX_FADE_MAX_S = 8.0
FX_FOLLOW_DEFAULT = 0.8        # direction.fx.follow when unset: how much the room's events move the bus
FX_EVENT_P = {"drop": 1.0, "track_change": 1.0, "section_change": 0.5, "breakdown": 0.6}   # x follow
FX_LIFTS = ("drop", "section_change", "track_change")   # the music lifts: a breakdown's thinning ends
FX_EVENT_GAP_S = 8.0           # no event move this soon after the last move
FX_THIN_S = 90.0               # a breakdown's thinning lasts at most this long
FX_DROP_FADE_S = 0.5           # a drop's fade-in with no clock
SECTION_WAIT_S = 4.0           # a section change: the scene director moves first; if it did, only re-check the bus


@dataclass
class FxMove:
    op: str                     # add | remove | swap
    add: Optional[str]          # effect preset to add
    remove: Optional[str]       # slot id to remove
    why: str
    level: float = 1.0          # the added effect's level (x its amount): the interplay's ceiling


def interval_for(rate: float) -> float:
    return FX_INTERVAL_AT_0 * (FX_INTERVAL_AT_1 / FX_INTERVAL_AT_0) ** max(0.0, min(1.0, rate))


def fade_for(rate: float) -> float:
    r = max(0.0, min(1.0, rate))
    return FX_FADE_AT_0 + (FX_FADE_AT_1 - FX_FADE_AT_0) * r


class FxDirector:
    def __init__(self, seed: int):
        self.seed = seed
        self.rng = random.Random(f"{seed}:fx")
        self.auto_fx: dict = dict(AUTO_FX_DEFAULTS)
        self.direction: dict = {"fx": dict(FX_DIRECTION_DEFAULTS)}
        self.armed = False
        self.next_at: Optional[float] = None
        self.recent: collections.deque[str] = collections.deque(maxlen=16)
        self.quarantined: dict[str, float] = {}
        self.last: Optional[dict] = None
        self.vet: Optional[Callable[[str, Optional[str]], bool]] = None   # (kind, plug-in) -> auto may add it
        self.rate: Optional[Callable[[str, Optional[str]], float]] = None  # (kind, plug-in) -> owner-rating weight
        self.clock: Optional[Callable[[float], Any]] = None   # now -> (beat s, beats per bar) while sure, else None
        self.grid: Optional[tuple[float, int]] = None         # the clock at the last tick (arm)
        self.event: Optional[str] = None                      # the room.event the pending move answers
        self.thin_until: Optional[float] = None               # a breakdown thins the bus until a lift or then
        self.last_move_at: Optional[float] = None
        self.looks: Any = None      # fx_interplay.Interplay (set by the session): roles, measured looks, the table
        self.scene: Optional[Callable[[], tuple[Optional[str], Optional[dict]]]] = None   # the scene on screen
        self._scene_seen: Optional[str] = None
        self._event_scene: Optional[str] = None               # the scene when a section change came in
        self._fit_at_add: dict[str, float] = {}                # preset -> its fit when it came in
        self.last_fit: Optional[str] = None

    # -- configuration -------------------------------------------------------------
    def configure(self, auto_fx: Optional[dict], direction: dict) -> None:
        self.auto_fx = dict(auto_fx or AUTO_FX_DEFAULTS)
        self.direction = direction

    @property
    def enabled(self) -> bool:
        return bool(self.auto_fx.get("enabled")) and int(self.auto_fx.get("max", 0)) > 0

    @property
    def fxd(self) -> dict:
        return {**FX_DIRECTION_DEFAULTS, **(self.direction.get("fx") or {})}

    @property
    def follow(self) -> float:
        try:
            return max(0.0, min(1.0, float(self.fxd.get("follow", FX_FOLLOW_DEFAULT))))
        except (TypeError, ValueError):
            return FX_FOLLOW_DEFAULT

    def _read_clock(self, now: float) -> Optional[tuple[float, int]]:
        if self.clock is None:
            return None
        try:
            g = self.clock(now)
            if not g:
                return None
            beat_s, bpb = float(g[0]), max(1, int(g[1]))
        except Exception:
            return None
        return (beat_s, bpb) if 0.0 < beat_s < 5.0 else None

    def _bars(self, s: float) -> float:
        """Seconds -> whole phrases on the clock (self.grid), else as they are."""
        if self.grid is None:
            return s
        bar = self.grid[0] * self.grid[1]
        return bar * min(FX_MAX_BARS, max(FX_MIN_BARS, FX_BARS * round(s / bar / FX_BARS)))

    def interval(self) -> float:
        return self._bars(interval_for(self.fxd["rate"]))

    def _gap(self, share: float = 1.0) -> float:
        return self._bars(share * interval_for(self.fxd["rate"]) * self.rng.uniform(0.75, 1.25))

    def fade_s(self) -> float:
        s = fade_for(self.fxd["rate"])
        drop = self.event == "drop"
        if self.grid is None:
            return min(s, FX_DROP_FADE_S) if drop else s
        beat = self.grid[0]
        if drop:
            return beat
        n = min(FX_FADE_BEATS, key=lambda b: abs(math.log2(b * beat / s)))
        while n > 1 and n * beat > FX_FADE_MAX_S:
            n //= 2
        return n * beat

    # -- when ----------------------------------------------------------------------
    def arm(self, armed: bool, now: float) -> bool:
        """Armed only in live with the bus open, not exited, auto.fx enabled (every tick: the clock is read here).
        -> changed?"""
        self.grid = self._read_clock(now)
        name = self._scene()[0]
        if name != self._scene_seen:
            prev, self._scene_seen = self._scene_seen, name
            if prev is not None and name is not None and self.armed and self.enabled and self.event is None:
                self.event = "scene_change"                  # re-check what the bus carries onto the new scene
                self.next_at = now
        if armed == self.armed:
            return False
        self.armed = armed
        self.event = None
        # the first move comes half an interval after arming (the bus has just opened)
        self.next_at = now + self._gap(0.5) if armed else None
        return True

    def on_event(self, kind: str, now: float) -> bool:
        """show_service's room.event (loop thread), by ``follow``: a drop or a new track makes a move due now, a
        section change sometimes; a breakdown thins the bus by one auto effect (a move due now when that leaves one
        too many) until the music lifts.  Never this soon after the last move (FX_EVENT_GAP_S).  -> a move is due"""
        if kind in FX_LIFTS:
            self.thin_until = None
        p = FX_EVENT_P.get(kind, 0.0) * self.follow
        if not (self.armed and self.enabled) or p <= 0.0 or self.rng.random() >= p:
            return False
        if kind == "breakdown":
            self.thin_until = now + FX_THIN_S
        if self.last_move_at is not None and now - self.last_move_at < FX_EVENT_GAP_S:
            return False
        self.event = kind
        self.next_at = now
        if kind == "section_change":                      # the scene director gets the first say
            self._event_scene = self._scene_seen
            self.next_at = now + SECTION_WAIT_S
        return True

    def due(self, now: float) -> bool:
        return self.armed and self.enabled and self.next_at is not None and now >= self.next_at

    def done(self, now: float, move: Optional[FxMove]) -> None:
        if move is not None and move.add:
            self.recent.append(move.add)
        if move is not None:
            self.last_move_at = now
        self.event = None
        self.next_at = now + self._gap()

    def quarantine(self, preset: str, now: float, *, session: bool = False) -> None:
        """Out of the auto pool for 10 min, or for the rest of the session (``session``: it failed to prepare or
        to render, e.g. a shader that does not compile)."""
        self.quarantined[preset] = math.inf if session else now + QUARANTINE_S

    def _quarantined(self, now: float) -> set[str]:
        self.quarantined = {n: t for n, t in self.quarantined.items() if t > now}
        return set(self.quarantined)

    # -- what ----------------------------------------------------------------------
    def target_count(self) -> int:
        mx = int(self.auto_fx.get("max", 2))
        k = math.floor(self.fxd["density"] * mx + self.rng.random())     # stochastic rounding
        return max(0, min(mx, k))

    def weight(self, tags: dict) -> float:
        fxd = self.fxd
        want = {"energy": self.direction.get("energy"),
                "moods": fxd.get("moods") if fxd.get("moods") else self.direction.get("moods"),
                "style": self.direction.get("transition_style"),
                "genre": self.direction.get("genre"), "genres": self.direction.get("genres"),
                "music_moods": self.direction.get("music_moods"), "music_style": self.direction.get("music_style")}
        return match_weight(tags, want, FX_DESCRIPTORS)

    def candidates(self, now: float, stack: list, presets: dict[str, dict], cost: Callable[[str], Optional[float]],
                   pixels_ratio: float, freed: Any = None) -> list[str]:
        """Eligible presets for one more bus effect (``freed``: a slot about to be swapped out)."""
        pool = resolve_pool(self.auto_fx.get("pool", "all"), presets)
        q = self._quarantined(now)
        allowed = self.fxd.get("stages")
        keep = [s for s in stack if s is not freed]
        used_stages = {getattr(s, "stage", None) for s in keep}
        used_presets = {getattr(s.spec, "preset", None) for s in stack}    # a swap never re-picks the same
        bus_cost = sum(self._cost(cost, s.spec.preset or s.spec.effect) for s in keep)
        budget = FX_BUS_BUDGET_MS / max(1e-3, pixels_ratio)
        out = []
        for name in pool:
            p = presets[name]
            tags = p.get("tags") or {}
            stage = p.get("stage")
            if tags.get("auto", True) is False or name in q or name in used_presets:
                continue
            if self.vet is not None and not self._vetted(p.get("effect")):
                continue
            if stage not in STAGES or stage in used_stages or (allowed and stage not in allowed):
                continue
            if bus_cost + self._cost(cost, name) > budget:
                continue
            out.append(name)
        return out

    def _rated(self, plugin: Optional[str]) -> float:
        if self.rate is None:
            return 1.0
        try:
            return float(self.rate("effect", plugin))
        except Exception:
            return 1.0

    def _vetted(self, plugin: Optional[str]) -> bool:
        try:
            return bool(self.vet("effect", plugin))
        except Exception:
            return False

    @staticmethod
    def _cost(cost: Callable[[str], Optional[float]], name: str) -> float:
        try:
            v = cost(name)
        except Exception:
            v = None
        return FX_UNKNOWN_COST_MS if v is None else float(v)

    def _scene(self) -> tuple[Optional[str], Optional[dict]]:
        try:
            got = self.scene() if self.scene is not None else None
            return (got[0], got[1]) if got else (None, None)
        except Exception:
            return None, None

    def _ctx(self, stack: list = (), freed: Any = None) -> Any:
        """The pick's context (fx_interplay.Interplay.context): the scene, the bus minus ``freed``, the music."""
        if self.looks is None:
            return None
        others = [(getattr(s.spec, "preset", None) or getattr(s.spec, "effect", None), getattr(s.spec, "effect", None))
                  for s in stack if s is not freed]
        try:
            return self.looks.context(self._scene(), others, self.direction)
        except Exception:
            return None

    def fit(self, name: str, presets: dict[str, dict], stack: list = (), freed: Any = None, ctx: Any = None) -> Fit:
        """How ``name`` fits the scene on screen, the other bus effects and the music (fx_interplay.py)."""
        ctx = ctx if ctx is not None else self._ctx(stack, freed)
        if ctx is None or name not in presets:
            return Fit()
        try:
            return self.looks.fit_in(ctx, name, presets[name])
        except Exception:
            return Fit()

    def _prior(self) -> float:
        try:
            return float((self.looks.cfg if self.looks is not None else {}).get("tag_prior", 1.0))
        except (TypeError, ValueError):
            return 1.0

    def _pick(self, names: list[str], presets: dict[str, dict], stack: list = (),
              freed: Any = None) -> Optional[tuple[str, str, Fit]]:
        if not names:
            return None
        recent = set(list(self.recent)[-NOVELTY_WINDOW:])
        prior = self._prior()
        ctx = self._ctx(stack, freed)
        fits = [self.fit(n, presets, ctx=ctx) for n in names]
        w = [max(1e-6, self.weight(presets[n].get("tags") or {}) ** prior * f.mult
                 * (NOVELTY_WEIGHT if n in recent else 1.0) * self._rated(presets[n].get("effect")))
             for n, f in zip(names, fits)]
        i = names.index(self.rng.choices(names, weights=w)[0])
        name, f = names[i], fits[i]
        return name, f"{name} (p {w[i] / sum(w):.2f} of {len(names)}, {presets[name].get('stage')}; {f.why()})", f

    def _choose(self, names: list[str], presets: dict[str, dict], prefer: tuple, stack: list = (),
                freed: Any = None) -> Optional[tuple[str, str, Fit]]:
        """The first queued effect (upnext.py) that fits now, else a weighted pick."""
        for n in prefer:
            if n in names:
                f = self.fit(n, presets, stack, freed)
                return n, f"{n} (up next, {presets[n].get('stage')}; {f.why()})", f
        return self._pick(names, presets, stack, freed)

    def _move(self, op: str, got: tuple, remove: Optional[str], why: str) -> FxMove:
        name, _, f = got
        self._fit_at_add[name] = f.mult
        self.last_fit = f.why()
        return FxMove(op, name, remove, why, level=f.level)

    def _refit(self, now: float, stack: list, presets: dict[str, dict], cost: Callable[[str], Optional[float]],
               pixels_ratio: float, prefer: tuple, on: str) -> Optional[FxMove]:
        """A new scene: swap the carried auto effect that now fits it worst, if its fit fell below refit_below of
        what it was when it came in (else leave the bus as it is)."""
        try:
            below = float((self.looks.cfg if self.looks is not None else {}).get("refit_below", 0.5))
        except (TypeError, ValueError):
            below = 0.5
        worst, worst_ratio, worst_fit = None, 1.0, None
        for s in (s for s in stack if getattr(s, "origin", "manual") == "auto"):
            name = getattr(s.spec, "preset", None)
            if not name:
                continue
            f = self.fit(name, presets, stack, freed=s)
            ratio = f.mult / max(1e-6, self._fit_at_add.get(name, f.mult))
            if ratio < worst_ratio:
                worst, worst_ratio, worst_fit = s, ratio, f
        if worst is None or worst_ratio >= below:
            return None
        got = self._choose(self.candidates(now, stack, presets, cost, pixels_ratio, freed=worst), presets, prefer,
                           stack, worst)
        if got is None:
            return None
        return self._move("swap", got, worst.id, f"swap: {got[1]} for {worst.spec.name}, which no longer suits the "
                          f"scene (fit x{worst_fit.mult:.2f}, {worst_ratio:.0%} of when it came in){on}")

    def ahead(self, now: float, stack: list, presets: dict[str, dict], cost: Callable[[str], Optional[float]],
              pixels_ratio: float, queued: tuple) -> Optional[tuple[str, str]]:
        """One more effect for the queue: what could come in at the next move (the oldest auto effect freed),
        not already queued.  None when nothing else fits."""
        autos = [s for s in stack if getattr(s, "origin", "manual") == "auto"]
        freed = autos[0] if autos else None
        names = [n for n in self.candidates(now, stack, presets, cost, pixels_ratio, freed=freed) if n not in queued]
        got = self._pick(names, presets, stack, freed)
        return None if got is None else (got[0], got[1])

    def plan(self, now: float, stack: list, presets: dict[str, dict], cost: Callable[[str], Optional[float]],
             pixels_ratio: float = 1.0, prefer: tuple = ()) -> Optional[FxMove]:
        """One move (or None).  ``stack``: the session's SlotStates (id, spec, stage, origin, added order).
        ``prefer``: the queued effects, in order (an add or a swap takes the first that fits)."""
        if not self.enabled:
            return None
        autos = [s for s in stack if getattr(s, "origin", "manual") == "auto"]
        ev = self.event
        on = f" (on the {ev.replace('_', ' ')})" if ev else ""
        if ev == "scene_change" or (ev == "section_change" and self._scene_seen != self._event_scene):
            return self._refit(now, stack, presets, cost, pixels_ratio, prefer, on)   # one layer at a time
        k = self.target_count()
        if ev == "drop":
            k = max(k, min(1, int(self.auto_fx.get("max", 2))))      # a drop always brings an effect
        if self.thin_until is not None:
            if now < self.thin_until:
                k = max(0, k - 1)                                    # a breakdown: one fewer
            else:
                self.thin_until = None
        if len(autos) > k:
            old = autos[0]
            return FxMove("remove", None, old.id, f"remove {old.spec.name} (density: {len(autos)} > {k}){on}")
        if len(autos) < k:
            if len(stack) >= MAX_BUS_SLOTS:
                return None
            got = self._choose(self.candidates(now, stack, presets, cost, pixels_ratio), presets, prefer, stack)
            return None if got is None else self._move("add", got, None, f"add: {got[1]}{on}")
        if k > 0 and (ev in FX_LIFTS or self.rng.random() < SWAP_P):   # the music's events always change
            old = autos[0]
            got = self._choose(self.candidates(now, stack, presets, cost, pixels_ratio, freed=old), presets, prefer,
                               stack, old)
            if got is not None:
                return self._move("swap", got, old.id, f"swap: {got[1]} for {old.spec.name}{on}")
        return None

    def next_move(self, now: float, stack: list, presets: dict[str, dict], cost: Callable[[str], Optional[float]],
                  pixels_ratio: float = 1.0, prefer: tuple = ()) -> Optional[FxMove]:
        """The owner's "next effect" (16 Oct): a change now, whatever the schedule or auto.fx says.  Swaps the oldest
        auto effect for a new pick, or adds one when there is none; manual (hand-placed) effects are never touched."""
        autos = [s for s in stack if getattr(s, "origin", "manual") == "auto"]
        if autos:
            old = autos[0]
            got = self._choose(self.candidates(now, stack, presets, cost, pixels_ratio, freed=old), presets, prefer,
                               stack, old)
            return None if got is None else self._move("swap", got, old.id, f"next: {got[1]} for {old.spec.name}")
        if len(stack) >= MAX_BUS_SLOTS:
            return None
        got = self._choose(self.candidates(now, stack, presets, cost, pixels_ratio), presets, prefer, stack)
        return None if got is None else self._move("add", got, None, f"next: {got[1]}")

    def status(self, now: float) -> dict:
        fxd = self.fxd
        return {**self.auto_fx, "density": fxd["density"], "rate": fxd["rate"], "follow": self.follow,
                "armed": self.armed,
                "next_in_s": None if self.next_at is None or not self.enabled else round(max(0.0, self.next_at - now), 1),
                "on_the_bar": self.grid is not None, "event": self.event,
                "thinned": self.thin_until is not None and now < self.thin_until, "last": self.last,
                "scene": self._scene_seen, "last_fit": self.last_fit}
