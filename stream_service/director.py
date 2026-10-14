"""The renderer's auto-director (LIVE_V1 1.1 §1.7, §1.8; review R4, R8, R11, R12): what next, via what, when.

The art director (the panel, a mode, room_service's random director, later an LLM) sets ``direction``:
descriptors, never scenes.  This module maps it to weighted picks over tagged presets (tags.py), and real-time
input says only *when*:
  * ``dmx_change``: a ``follow_dmx`` attribute (effect, variant) took a new value, stable for >= 300 ms on fresh
    frames, different from its value at the last cue: an explicit change on the bus, not inference.  The first
    fresh frame after arming or recovery is a baseline.  A change inside the min dwell is latched and fires
    when min is reached;
  * ``max_dwell``: the dwell reached its maximum;
  * ``next``: ``cue {to: "auto"}`` (ignores min).
Dwell is measured in seconds from the previous scene transition's ``done``; with no explicit ``dwell_s`` it is
log-linear in ``direction.change_rate``: 0 -> [120, 240] s, 1 -> [15, 30] s.  The director is armed only while
the session is ``live`` (and never again after exit); triggers seen while disarmed are discarded.  It never
moves while a scene transition is running.  Picks use seeded RNGs, one stream for scenes and one for
transitions.  A preset that fails to prepare is quarantined for 10 minutes.  ``vet(kind, plug-in)`` (set by the
session: the vetted pool, sessions.py ``auto_ok``) drops presets whose plug-in is unvetted, slow or broken.

Pure state + decisions, no GPU and no I/O: sessions.py calls it on the asyncio loop thread.

Effects layer (EFFECTS_LAYER_DESIGN D.3): ``direction.fx`` {density, rate, stages, moods} are the art
director's descriptors for the master FX bus; ``auto.fx`` {enabled, pool, max} says what the FX director
(fx_director.py) may do on its own.  ``auto.fx.enabled`` defaults to false, so existing clients see no change.
"""
from __future__ import annotations

import collections
import math
import random
import secrets
from typing import Any, Callable, Optional

from .inputs import FOLLOW_ATTRS, Snapshot
from .plugins.base import STAGES, ParamError
from .tags import matches_filter, scene_weight, transition_weight

FX_DIRECTION_DEFAULTS: dict[str, Any] = {"density": 0.5, "rate": 0.5, "stages": None, "moods": None}
AUTO_FX_DEFAULTS: dict[str, Any] = {"enabled": False, "pool": "all", "max": 2}
AUTO_FX_MAX = 4
DIRECTION_DEFAULTS: dict[str, Any] = {
    "energy": 0.5, "pace": 0.5, "intensity": 1.0, "change_rate": 0.5, "moods": [], "genre": None,
    "transition_style": None, "palette_family": None, "source": None, "note": None, "fx": FX_DIRECTION_DEFAULTS}
AUTO_DEFAULTS: dict[str, Any] = {
    "enabled": True, "pool": "all", "transitions": "all", "dwell_s": None, "snap": "beat",
    "follow_dmx": list(FOLLOW_ATTRS), "avoid_repeat": 2, "seed": None, "fx": AUTO_FX_DEFAULTS}
SNAPS = ("now", "beat")
DWELL_AT_0 = (120.0, 240.0)      # change_rate 0
DWELL_AT_1 = (15.0, 30.0)        # change_rate 1
PREPARE_AHEAD_S = 2.0            # the next pick is prepared this long before min dwell
QUARANTINE_S = 600.0


def _unit(v: Any, field: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ParamError("bad_request", f"{field} must be a number 0-1")
    return min(1.0, max(0.0, float(v)))


def _word(v: Any, field: str) -> Optional[str]:
    if v is not None and not isinstance(v, str):
        raise ParamError("bad_request", f"{field} must be a word or null")
    return v


def validate_direction(body: Any, vocab: Optional[dict] = None) -> dict:
    """The complete direction object (R12): a missing field takes its neutral default."""
    if not isinstance(body, dict):
        raise ParamError("bad_request", "direction must be an object")
    unknown = set(body) - set(DIRECTION_DEFAULTS)
    if unknown:
        raise ParamError("bad_request", f"direction: unknown fields {sorted(unknown)}")
    out = dict(DIRECTION_DEFAULTS, moods=[], fx=dict(FX_DIRECTION_DEFAULTS))
    if body.get("fx") is not None:
        out["fx"] = validate_direction_fx(body["fx"])
    for k in ("energy", "pace", "intensity", "change_rate"):
        if body.get(k) is not None:
            out[k] = _unit(body[k], f"direction.{k}")
    m = body.get("moods")
    if m is not None:
        if not (isinstance(m, list) and all(isinstance(x, str) for x in m)):
            raise ParamError("bad_request", "direction.moods must be a list of words")
        out["moods"] = list(dict.fromkeys(m))
    for k in ("genre", "transition_style", "palette_family"):
        out[k] = _word(body.get(k), f"direction.{k}")
    styles = (vocab or {}).get("transition_style")
    if out["transition_style"] is not None and styles and out["transition_style"] not in styles:
        raise ParamError("bad_request", f"direction.transition_style must be one of {styles} or null")
    for k in ("source", "note"):
        v = _word(body.get(k), f"direction.{k}")
        out[k] = v[:500] if v else None
    return out


def validate_direction_fx(body: Any) -> dict:
    """direction.fx: the whole sub-object (a missing field takes its neutral default)."""
    if not isinstance(body, dict):
        raise ParamError("bad_request", "direction.fx must be an object")
    unknown = set(body) - set(FX_DIRECTION_DEFAULTS)
    if unknown:
        raise ParamError("bad_request", f"direction.fx: unknown fields {sorted(unknown)}")
    out = dict(FX_DIRECTION_DEFAULTS)
    for k in ("density", "rate"):
        if body.get(k) is not None:
            out[k] = _unit(body[k], f"direction.fx.{k}")
    st = body.get("stages")
    if st is not None:
        if not (isinstance(st, list) and all(isinstance(x, str) and x in STAGES for x in st)):
            raise ParamError("bad_request", f"direction.fx.stages must be null or a list from {list(STAGES)}")
        out["stages"] = list(dict.fromkeys(st))
    m = body.get("moods")
    if m is not None:
        if not (isinstance(m, list) and all(isinstance(x, str) for x in m)):
            raise ParamError("bad_request", "direction.fx.moods must be null or a list of words")
        out["moods"] = list(dict.fromkeys(m))
    return out


def validate_auto_fx(body: Any, base: Optional[dict] = None) -> dict:
    """auto.fx, merged over ``base`` (like the rest of auto)."""
    if not isinstance(body, dict):
        raise ParamError("bad_request", "auto.fx must be an object")
    unknown = set(body) - set(AUTO_FX_DEFAULTS)
    if unknown:
        raise ParamError("bad_request", f"auto.fx: unknown fields {sorted(unknown)}")
    out = dict(base or AUTO_FX_DEFAULTS)
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise ParamError("bad_request", "auto.fx.enabled must be true or false")
        out["enabled"] = body["enabled"]
    if "pool" in body:
        out["pool"] = _pool_spec(body["pool"], "auto.fx.pool")
    if "max" in body:
        m = body["max"]
        if isinstance(m, bool) or not isinstance(m, int) or not 0 <= m <= AUTO_FX_MAX:
            raise ParamError("bad_request", f"auto.fx.max must be an integer 0-{AUTO_FX_MAX}")
        out["max"] = m
    return out


def _pool_spec(v: Any, field: str) -> Any:
    if v == "all":
        return v
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return list(v)
    if isinstance(v, dict) and set(v) == {"tags"} and isinstance(v["tags"], dict):
        return {"tags": dict(v["tags"])}
    raise ParamError("bad_request", f"{field} must be \"all\", a list of preset names or {{\"tags\": {{...}}}}")


def validate_auto(body: Any, base: Optional[dict] = None) -> dict:
    """An auto block (R11), merged over ``base`` (the defaults for a new block)."""
    if not isinstance(body, dict):
        raise ParamError("bad_request", "auto must be an object")
    unknown = set(body) - set(AUTO_DEFAULTS)
    if unknown:
        raise ParamError("bad_request", f"auto: unknown fields {sorted(unknown)}")
    out = dict(base or AUTO_DEFAULTS)
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise ParamError("bad_request", "auto.enabled must be true or false")
        out["enabled"] = body["enabled"]
    for k in ("pool", "transitions"):
        if k in body:
            out[k] = _pool_spec(body[k], f"auto.{k}")
    if "dwell_s" in body:
        v = body["dwell_s"]
        if v is not None:
            if not (isinstance(v, list) and len(v) == 2 and all(
                    isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in v)):
                raise ParamError("bad_request", "auto.dwell_s must be [min, max] seconds")
            v = [min(3600.0, max(2.0, float(x))) for x in v]
            if v[0] > v[1]:
                raise ParamError("bad_request", "auto.dwell_s: min > max")
        out["dwell_s"] = v
    if "snap" in body:
        if body["snap"] not in SNAPS:
            raise ParamError("bad_request", f"auto.snap must be one of {list(SNAPS)}")
        out["snap"] = body["snap"]
    if "follow_dmx" in body:
        f = body["follow_dmx"]
        if not (isinstance(f, list) and all(x in FOLLOW_ATTRS for x in f)):
            raise ParamError("bad_request", f"auto.follow_dmx must be a list from {list(FOLLOW_ATTRS)}")
        out["follow_dmx"] = list(dict.fromkeys(f))
    if "avoid_repeat" in body:
        a = body["avoid_repeat"]
        if isinstance(a, bool) or not isinstance(a, int) or not 0 <= a <= 16:
            raise ParamError("bad_request", "auto.avoid_repeat must be an integer 0-16")
        out["avoid_repeat"] = a
    if "seed" in body:
        s = body["seed"]
        if s is not None and (isinstance(s, bool) or not isinstance(s, int)):
            raise ParamError("bad_request", "auto.seed must be an integer or null")
        out["seed"] = s
    if "fx" in body:
        out["fx"] = validate_auto_fx(body["fx"], (base or AUTO_DEFAULTS).get("fx") or AUTO_FX_DEFAULTS)
    else:
        out["fx"] = dict(out.get("fx") or AUTO_FX_DEFAULTS)
    return out


def default_dwell(change_rate: float) -> tuple[float, float]:
    """Log-linear: change_rate 0 -> [120, 240] s, 1 -> [15, 30] s."""
    lo = DWELL_AT_0[0] * (DWELL_AT_1[0] / DWELL_AT_0[0]) ** change_rate
    hi = DWELL_AT_0[1] * (DWELL_AT_1[1] / DWELL_AT_0[1]) ** change_rate
    return lo, hi


def dwell_window(auto: dict, direction: dict) -> tuple[float, float, str]:
    """-> (min_s, max_s, where from)."""
    if auto.get("dwell_s"):
        lo, hi = auto["dwell_s"]
        return lo, hi, "dwell_s"
    lo, hi = default_dwell(direction["change_rate"])
    return lo, hi, "change_rate"


def resolve_pool(spec: Any, presets: dict[str, dict]) -> list[str]:
    """"all" = every tagged preset; a list of names; or a tag filter (over tagged presets).  Presets tagged
    ``auto: false`` (lab presets outside --lab-auto) never enter "all" or a tag filter."""
    if spec == "all":
        return [n for n, p in presets.items() if p.get("tags") and p["tags"].get("auto", True) is not False]
    if isinstance(spec, list):
        return [n for n in spec if n in presets]
    return [n for n, p in presets.items() if p.get("tags") and p["tags"].get("auto", True) is not False
            and matches_filter(p["tags"], spec["tags"])]


def weighted_pick(names: list[str], weight: Callable[[str], float], rng: random.Random,
                  avoid: Any = ()) -> Optional[tuple[str, float, int]]:
    """Weighted-random choice among ``names`` minus ``avoid`` (all of them if that leaves none).
    -> (name, probability, candidates) or None for an empty pool."""
    avoid = set(avoid)
    cands = [n for n in names if n not in avoid] or list(names)
    if not cands:
        return None
    w = [max(1e-6, weight(n)) for n in cands]
    name = rng.choices(cands, weights=w)[0]
    return name, w[cands.index(name)] / sum(w), len(cands)


class Director:
    """One session's auto-director state (loop thread)."""

    def __init__(self, auto: Optional[dict], direction: dict):
        self.auto = auto                      # None: no auto block (cue "auto" -> 409 auto_unavailable)
        self.direction = direction
        self.seed = (auto or {}).get("seed")
        if self.seed is None:
            self.seed = secrets.randbits(31)
        self.rng_scene = random.Random(f"{self.seed}:scene")
        self.rng_transition = random.Random(f"{self.seed}:transition")
        self.recent_scenes: collections.deque[str] = collections.deque(maxlen=16)
        self.recent_transitions: collections.deque[str] = collections.deque(maxlen=16)
        self.armed = False
        self.dwell_start: Optional[float] = None      # the previous scene transition's done (monotonic s)
        self.baseline: Optional[dict] = None          # follow_dmx values at the last cue / arming / recovery
        self.latched: Optional[str] = None            # a dmx_change seen inside min dwell
        self.was_fresh = False
        self.quarantine: dict[str, float] = {}        # preset -> until (monotonic s)
        self.bad_transitions: set[str] = set()        # transition presets that failed to prepare
        self.last: Optional[dict] = None              # the last decision, for the panel and GET session
        self.vet: Optional[Callable[[str, Optional[str]], bool]] = None   # (kind, plug-in) -> auto may pick it

    def _vetted(self, kind: str, plugin: Optional[str]) -> bool:
        if self.vet is None:
            return True
        try:
            return bool(self.vet(kind, plugin))
        except Exception:
            return False

    @property
    def enabled(self) -> bool:
        return self.auto is not None and self.auto["enabled"]

    def set_auto(self, auto: Optional[dict]) -> None:
        if auto is not None and auto.get("seed") is not None and auto["seed"] != self.seed:
            self.seed = auto["seed"]
            self.rng_scene.seed(f"{self.seed}:scene")
            self.rng_transition.seed(f"{self.seed}:transition")
        self.auto = auto

    # -- when ------------------------------------------------------------------
    def window(self) -> tuple[float, float, str]:
        return dwell_window(self.auto or AUTO_DEFAULTS, self.direction)

    def arm(self, armed: bool) -> bool:
        """Set the armed state; True if it changed.  Disarming discards any latched trigger."""
        if armed == self.armed:
            return False
        self.armed = armed
        self.latched = None
        self.baseline = None                  # the next fresh frame is a baseline
        return True

    def scene_done(self, now: float) -> None:
        self.dwell_start = now

    def observe(self, snap: Optional[Snapshot], fresh: bool) -> None:
        """follow_dmx bookkeeping on each tick (armed or not: disarmed, changes are discarded)."""
        if not fresh or snap is None:
            self.was_fresh = False
            return
        if not self.was_fresh or self.baseline is None or not self.armed:
            self.baseline = dict(snap.stable)         # first fresh frame after arming / recovery: a baseline
            self.was_fresh = True
            if not self.armed:
                self.latched = None
            return
        if self.latched is None and self.auto:
            for attr in self.auto["follow_dmx"]:
                if attr in snap.stable and snap.stable[attr] != self.baseline.get(attr, snap.stable[attr]):
                    self.latched = f"{attr} {self.baseline.get(attr)}→{snap.stable[attr]}"
                    break

    def due(self, now: float) -> Optional[tuple[str, str]]:
        """-> (reason, detail) when an armed, enabled director should move now, else None."""
        if not (self.armed and self.enabled) or self.dwell_start is None:
            return None
        dwell = now - self.dwell_start
        lo, hi, _ = self.window()
        if dwell < lo:
            return None
        if self.latched:
            return "dmx_change", f"{self.latched}; dwell {dwell:.1f} s ≥ min {lo:.1f} s"
        if dwell >= hi:
            return "max_dwell", f"dwell {dwell:.1f} s ≥ max {hi:.1f} s"
        return None

    def prepare_due(self, now: float) -> bool:
        """Time to choose and prepare the next pick (>= 2 s before min dwell)."""
        if not (self.armed and self.enabled) or self.dwell_start is None:
            return False
        return now - self.dwell_start >= self.window()[0] - PREPARE_AHEAD_S

    def next_eligible(self, now: float) -> dict:
        lo, hi, src = self.window()
        out = {"dwell_s": [round(lo, 2), round(hi, 2)], "dwell_from": src, "eligible_in_s": None,
               "max_in_s": None}
        if self.dwell_start is not None:
            out["eligible_in_s"] = round(max(0.0, self.dwell_start + lo - now), 2)
            out["max_in_s"] = round(max(0.0, self.dwell_start + hi - now), 2)
        return out

    def note_cue(self, scene: Optional[str], transition: Optional[str], snap: Optional[Snapshot]) -> None:
        """Any scene cue (manual, patch or auto): novelty, and a new follow_dmx baseline."""
        if scene:
            self.recent_scenes.append(scene)
        if transition:
            self.recent_transitions.append(transition)
        self.latched = None
        if snap is not None:
            self.baseline = dict(snap.stable)

    # -- what --------------------------------------------------------------------
    def _quarantined(self, now: float) -> set[str]:
        self.quarantine = {n: t for n, t in self.quarantine.items() if t > now}
        return set(self.quarantine)

    def quarantine_preset(self, name: str, now: float, *, session: bool = False) -> None:
        """Keep a preset out of the auto pool for 10 min, or for the rest of the session (``session``: it failed
        to prepare, e.g. a shader that does not compile)."""
        self.quarantine[name] = math.inf if session else max(self.quarantine.get(name, 0.0), now + QUARANTINE_S)

    def blacklist_transition(self, name: str) -> None:
        self.bad_transitions.add(name)

    def pick_scene(self, scenes: dict[str, dict], current: Optional[str], now: float) -> Optional[tuple[str, str]]:
        auto = self.auto or AUTO_DEFAULTS
        q = self._quarantined(now)
        names = [n for n in resolve_pool(auto["pool"], scenes)
                 if n not in q and self._vetted("source", scenes[n].get("source"))]
        n_avoid = auto["avoid_repeat"]
        if len(names) <= n_avoid:
            avoid = {current} if current else set()
        else:
            avoid = set(list(self.recent_scenes)[-n_avoid:]) | ({current} if current else set()) if n_avoid else \
                ({current} if current else set())
        got = weighted_pick(names, lambda n: scene_weight(scenes[n].get("tags") or {}, self.direction),
                            self.rng_scene, avoid)
        if got is None:
            return None
        name, p, n = got
        return name, f"{name} (p {p:.2f} of {n})"

    def pick_transition(self, transitions: dict[str, dict]) -> Optional[tuple[str, str]]:
        auto = self.auto or AUTO_DEFAULTS
        names = [n for n in resolve_pool(auto["transitions"], transitions)
                 if n not in self.bad_transitions and self._vetted("transition", transitions[n].get("transition"))]
        avoid = set(list(self.recent_transitions)[-1:])
        got = weighted_pick(names, lambda n: transition_weight(transitions[n].get("tags") or {}, self.direction),
                            self.rng_transition, avoid)
        if got is None:
            return None
        name, p, n = got
        style = self.direction.get("transition_style") or "any style"
        return name, f"{name} (p {p:.2f} of {n}, {style})"
