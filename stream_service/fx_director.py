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
"""
from __future__ import annotations

import collections
import math
import random
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .director import AUTO_FX_DEFAULTS, FX_DIRECTION_DEFAULTS, resolve_pool
from .effects import MAX_BUS_SLOTS
from .plugins.base import STAGES
from .tags import match_weight

FX_INTERVAL_AT_0, FX_INTERVAL_AT_1 = 90.0, 15.0
FX_FADE_AT_0, FX_FADE_AT_1 = 6.0, 2.0
FX_BUS_BUDGET_MS, FX_UNKNOWN_COST_MS = 3.0, 1.5
NOVELTY_WINDOW = 4
NOVELTY_WEIGHT = 0.25
SWAP_P = 0.6
QUARANTINE_S = 600.0
FX_DESCRIPTORS = ("energy", "moods", "style")


@dataclass
class FxMove:
    op: str                     # add | remove | swap
    add: Optional[str]          # effect preset to add
    remove: Optional[str]       # slot id to remove
    why: str


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

    def interval(self) -> float:
        return interval_for(self.fxd["rate"])

    def fade_s(self) -> float:
        return fade_for(self.fxd["rate"])

    # -- when ----------------------------------------------------------------------
    def arm(self, armed: bool, now: float) -> bool:
        """Armed only in live with the bus open, not exited, auto.fx enabled.  -> changed?"""
        if armed == self.armed:
            return False
        self.armed = armed
        # the first move comes half an interval after arming (the bus has just opened)
        self.next_at = now + 0.5 * self.interval() * self.rng.uniform(0.75, 1.25) if armed else None
        return True

    def due(self, now: float) -> bool:
        return self.armed and self.enabled and self.next_at is not None and now >= self.next_at

    def done(self, now: float, move: Optional[FxMove]) -> None:
        if move is not None and move.add:
            self.recent.append(move.add)
        self.next_at = now + self.interval() * self.rng.uniform(0.75, 1.25)

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
                "style": self.direction.get("transition_style")}
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

    def _pick(self, names: list[str], presets: dict[str, dict]) -> Optional[tuple[str, str]]:
        if not names:
            return None
        recent = set(list(self.recent)[-NOVELTY_WINDOW:])
        w = [max(1e-6, self.weight(presets[n].get("tags") or {}) * (NOVELTY_WEIGHT if n in recent else 1.0)
                 * self._rated(presets[n].get("effect"))) for n in names]
        name = self.rng.choices(names, weights=w)[0]
        p = w[names.index(name)] / sum(w)
        return name, f"{name} (p {p:.2f} of {len(names)}, {presets[name].get('stage')})"

    def plan(self, now: float, stack: list, presets: dict[str, dict], cost: Callable[[str], Optional[float]],
             pixels_ratio: float = 1.0) -> Optional[FxMove]:
        """One move (or None).  ``stack``: the session's SlotStates (id, spec, stage, origin, added order)."""
        if not self.enabled:
            return None
        autos = [s for s in stack if getattr(s, "origin", "manual") == "auto"]
        k = self.target_count()
        if len(autos) > k:
            old = autos[0]
            return FxMove("remove", None, old.id, f"remove {old.spec.name} (density: {len(autos)} > {k})")
        if len(autos) < k:
            if len(stack) >= MAX_BUS_SLOTS:
                return None
            got = self._pick(self.candidates(now, stack, presets, cost, pixels_ratio), presets)
            return None if got is None else FxMove("add", got[0], None, f"add: {got[1]}")
        if k > 0 and self.rng.random() < SWAP_P:
            old = autos[0]
            got = self._pick(self.candidates(now, stack, presets, cost, pixels_ratio, freed=old), presets)
            if got is not None:
                return FxMove("swap", got[0], old.id, f"swap: {got[1]} for {old.spec.name}")
        return None

    def next_move(self, now: float, stack: list, presets: dict[str, dict], cost: Callable[[str], Optional[float]],
                  pixels_ratio: float = 1.0) -> Optional[FxMove]:
        """The owner's "next effect" (16 Oct): a change now, whatever the schedule or auto.fx says.  Swaps the oldest
        auto effect for a new pick, or adds one when there is none; manual (hand-placed) effects are never touched."""
        autos = [s for s in stack if getattr(s, "origin", "manual") == "auto"]
        if autos:
            old = autos[0]
            got = self._pick(self.candidates(now, stack, presets, cost, pixels_ratio, freed=old), presets)
            return None if got is None else FxMove("swap", got[0], old.id, f"next: {got[1]} for {old.spec.name}")
        if len(stack) >= MAX_BUS_SLOTS:
            return None
        got = self._pick(self.candidates(now, stack, presets, cost, pixels_ratio), presets)
        return None if got is None else FxMove("add", got[0], None, f"next: {got[1]}")

    def status(self, now: float) -> dict:
        fxd = self.fxd
        return {**self.auto_fx, "density": fxd["density"], "rate": fxd["rate"], "armed": self.armed,
                "next_in_s": None if self.next_at is None or not self.enabled else round(max(0.0, self.next_at - now), 1),
                "last": self.last}
