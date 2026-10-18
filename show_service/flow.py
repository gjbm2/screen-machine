"""flow: the room's one direction of travel, decided from the music (docs/LIGHTS.md §4.2; the catalogue builder's,
18 Oct). Pure: no I/O and no clock of its own. director.py drives it at 10 Hz on show-brain and builds FlowData
(room.direction.flow, room.look.flow) from what it says; space.py does the geometry.

**Votes** (``FlowDecider.votes``, each tick). Each source adds ``weight x confidence`` to one dir (show.yaml
``lights.flow``):
  * structure  music.context's section phase -> the ``structure`` map, weight ``sources.structure``;
  * pitch      the least-squares slope of harmony's register over ``pitch.window_s`` (samples at harmony conf >=
               ``pitch.min_conf`` while the gate is open; the piano's MIDI feeds harmony, so a rising run counts):
               >= ``pitch.slope`` votes up, <= -slope down, weight ``sources.pitch`` x min(1, |slope| / 2 slope);
  * listener   the listener's trajectory at conf >= the director's LLM_CONF -> the ``listener`` map, weight
               ``sources.listener`` x conf;
  * moods      room.direction's first three moods -> the ``moods`` map, ``sources.moods`` / 3 each.

**The decision** (``step``). net[d] = score[d] - score[opposite d]; the leader is the largest net, or ``still`` under
``min_score`` (or the energy under ``still_below_energy``). A new leader must beat the current dir by ``margin`` and
hold that for ``dwell_s`` before it lands (``still`` counts as ``min_score``). Events bypass the dwell (``event``):
an event with a ``dir`` and ``bars`` holds that dir for those bars from its line, then ``then``; one with a ``dir``
and no ``bars`` holds it while the music's phase is the event's word; one with no ``dir`` lands the voted dir at its
line (a new song). Coherence is ``coherence.events`` under an event, else max(floor, min(1, net / 2 min_score)).

Nothing here knows a mood, phase or event by anything but the fixed vocabularies (contract); every mapping is config.
"""

from __future__ import annotations

import collections
import json
import math
from typing import Any, Mapping, Optional

from . import contract

#: contract.FLOW_DIRS / FLOW_OPPOSITE are the space builder's (docs/LIGHTS.md §3.3): the same words until they land
FLOW_DIRS: tuple[str, ...] = getattr(contract, "FLOW_DIRS", ("still", "up", "down", "left", "right", "cw", "ccw",
                                                             "outward", "inward", "towards", "away"))
_OPP = getattr(contract, "FLOW_OPPOSITE", {"up": "down", "left": "right", "cw": "ccw", "outward": "inward",
                                           "towards": "away"})
FLOW_OPPOSITE: dict[str, str] = {**_OPP, **{v: k for k, v in _OPP.items()}}
STILL = FLOW_DIRS[0]
SYMMETRIES: tuple[str, ...] = getattr(contract, "FLOW_SYMMETRY", ("none", "mirror"))
SOURCES: tuple[str, ...] = ("structure", "pitch", "listener", "moods")   # the voting FLOW_SOURCES (show.yaml sources)
EVENT_KEYS = frozenset({"dir", "bars", "then", "sweep_beats"})
#: the why's words for a section phase or a music event (contract.SECTION_PHASES / MUSIC_EVENT_KINDS words)
PHASE_WHY = {"calm": "a calm passage", "build": "the song builds", "peak": "the peak", "drop": "a drop",
             "breakdown": "a breakdown", "outro": "the song ends", "track_change": "a new song",
             "section_change": "a new section"}
PITCH_MIN_SAMPLES = 5               # the slope needs this many samples ...
PITCH_MIN_SPAN = 0.5                # ... spanning at least this share of pitch.window_s
PERIOD_HYST = 0.1                   # a period's power of two moves only past the halfway point plus this (in log2)
NO_GRID_BAR_S = 2.0                 # an event's bars with no clock grid: the director's own stand-in (120 bpm, 4/4)

#: show.yaml lights.flow keys the code falls back to when the file leaves one out: the design's starting points
#: (docs/LIGHTS.md §12; the file's values win). The maps are empty: with no lights block nothing votes (still), and
#: the mirror ball and the blinds are off.
FLOW_FALLBACK: dict[str, Any] = {
    "centre": None, "sources": {"structure": 1.0, "pitch": 0.6, "listener": 0.5, "moods": 0.3},
    "structure": {}, "listener": {}, "moods": {}, "pitch": {"window_s": 4.0, "slope": 0.03, "min_conf": 0.5},
    "events": {}, "min_score": 0.25, "margin": 0.15, "dwell_s": 8.0, "still_below_energy": 0.15,
    "cross_beats": [16.0, 2.0], "cross_s": [8.0, 1.0], "speed": {"pace": 0.6, "energy": 0.4}, "spread": "look.spread",
    "coherence": {"events": 1.0, "floor": 0.3}, "symmetry": "none",
    "mirror_ball": {"enabled": False, "power_member": None, "power_on": None, "direction_target": None,
                    "speed_target": None, "directions": {}, "speeds": [], "min_gap_s": 120.0},
    "blinds": {"enabled": False, "targets": {}}}


def _num(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return float(v)
    return None


def _c01(x: float) -> float:
    return 0.0 if not x > 0.0 else 1.0 if x > 1.0 else x


def opposite(d: Optional[str]) -> Optional[str]:
    return FLOW_OPPOSITE.get(d) if d else None


# -- settings ------------------------------------------------------------------------------------------------------------
def parse_settings(raw: Any, probs: list[str], where: str = "lights.flow") -> dict:
    """show.yaml ``lights.flow`` -> the decider's settings (FLOW_FALLBACK filled in). A bad value falls back and is
    listed (page-words problem lines); a dir word outside FLOW_DIRS is dropped with a line."""
    cfg = json.loads(json.dumps(FLOW_FALLBACK))
    if raw is None:
        return cfg
    if not isinstance(raw, dict):
        probs.append(f"{where}: not a mapping: the flow stays still")
        return cfg
    c = raw.get("centre")
    if c is not None and not (isinstance(c, str) and c):
        probs.append(f"{where}.centre: {c!r} isn't a Room map name: the room's centre is used")
    else:
        cfg["centre"] = c
    src = raw.get("sources")
    if src is not None:
        if isinstance(src, dict):
            for k, v in src.items():
                if k not in SOURCES:
                    probs.append(f"{where}.sources.{k}: not a source ({', '.join(SOURCES)}): ignored")
                elif _num(v) is None or v < 0:
                    probs.append(f"{where}.sources.{k}: {v!r} isn't a number >= 0")
                else:
                    cfg["sources"][k] = float(v)
        else:
            probs.append(f"{where}.sources: not a mapping")
    for key, words in (("structure", contract.SECTION_PHASES), ("listener", contract.TRAJECTORY_STATES),
                       ("moods", None)):
        m = raw.get(key)
        if m is None:
            continue
        if not isinstance(m, dict):
            probs.append(f"{where}.{key}: not a mapping of word -> direction")
            continue
        for w, d in m.items():
            if words is not None and w not in words:
                probs.append(f"{where}.{key}.{w}: not one of {', '.join(words)}: ignored")
            elif d not in FLOW_DIRS:
                probs.append(f"{where}.{key}.{w}: {d!r} isn't a direction ({', '.join(FLOW_DIRS)}): ignored")
            else:
                cfg[key][str(w)] = d
    p = raw.get("pitch")
    if p is not None:
        if isinstance(p, dict):
            for k, lo, hi in (("window_s", 0.5, 60.0), ("slope", 1e-4, 1.0), ("min_conf", 0.0, 1.0)):
                if k in p:
                    v = _num(p[k])
                    if v is not None and lo <= v <= hi:
                        cfg["pitch"][k] = v
                    else:
                        probs.append(f"{where}.pitch.{k}: {p[k]!r} isn't a number in {lo:g}..{hi:g}")
        else:
            probs.append(f"{where}.pitch: not a mapping")
    ev = raw.get("events")
    if ev is not None:
        if isinstance(ev, dict):
            kinds = set(contract.MUSIC_EVENT_KINDS) | set(contract.SECTION_PHASES)
            for k, spec in ev.items():
                w = f"{where}.events.{k}"
                if k not in kinds:
                    probs.append(f"{w}: not a music event or phase: ignored")
                    continue
                if not isinstance(spec, dict):
                    probs.append(f"{w}: not a mapping: ignored")
                    continue
                out: dict[str, Any] = {}
                for kk in spec:
                    if kk not in EVENT_KEYS:
                        probs.append(f"{w}.{kk}: not an event key ({', '.join(sorted(EVENT_KEYS))}): ignored")
                for kk in ("dir", "then"):
                    if spec.get(kk) is not None:
                        if spec[kk] in FLOW_DIRS:
                            out[kk] = spec[kk]
                        else:
                            probs.append(f"{w}.{kk}: {spec[kk]!r} isn't a direction: ignored")
                for kk in ("bars", "sweep_beats"):
                    if spec.get(kk) is not None:
                        v = _num(spec[kk])
                        if v is not None and v > 0:
                            out[kk] = v
                        else:
                            probs.append(f"{w}.{kk}: {spec[kk]!r} isn't a number above 0: ignored")
                cfg["events"][str(k)] = out
        else:
            probs.append(f"{where}.events: not a mapping")
    for k, lo, hi in (("min_score", 0.0, 10.0), ("margin", 0.0, 10.0), ("dwell_s", 0.0, 600.0),
                      ("still_below_energy", 0.0, 1.0)):
        if k in raw:
            v = _num(raw[k])
            if v is not None and lo <= v <= hi:
                cfg[k] = v
            else:
                probs.append(f"{where}.{k}: {raw[k]!r} isn't a number in {lo:g}..{hi:g}")
    for k in ("cross_beats", "cross_s"):
        if k in raw:
            v = raw[k]
            if isinstance(v, list) and len(v) == 2 and all(_num(x) is not None and x > 0 for x in v):
                cfg[k] = [float(v[0]), float(v[1])]
            else:
                probs.append(f"{where}.{k}: {v!r} isn't [slow, fast], both above 0")
    for k, keys in (("speed", ("pace", "energy")), ("coherence", ("events", "floor"))):
        got = raw.get(k)
        if got is None:
            continue
        if not isinstance(got, dict):
            probs.append(f"{where}.{k}: not a mapping")
            continue
        for kk in keys:
            if kk in got:
                v = _num(got[kk])
                if v is not None and 0.0 <= v <= (1.0 if k == "coherence" else 2.0):
                    cfg[k][kk] = v
                else:
                    probs.append(f"{where}.{k}.{kk}: {got[kk]!r} is out of range")
    if "spread" in raw:
        v = raw["spread"]
        if v in contract.LOOK_INPUTS or (_num(v) is not None and 0.0 <= v <= 1.0):
            cfg["spread"] = v
        else:
            probs.append(f"{where}.spread: {v!r} is neither a number in 0..1 nor look.spread")
    if "symmetry" in raw:
        if raw["symmetry"] in SYMMETRIES:
            cfg["symmetry"] = raw["symmetry"]
        else:
            probs.append(f"{where}.symmetry: {raw['symmetry']!r} isn't one of {', '.join(SYMMETRIES)}")
    cfg["mirror_ball"] = _parse_mirror_ball(raw.get("mirror_ball"), probs, f"{where}.mirror_ball")
    cfg["blinds"] = _parse_blinds(raw.get("blinds"), probs, f"{where}.blinds")
    return cfg


def _parse_mirror_ball(raw: Any, probs: list[str], where: str) -> dict:
    out = json.loads(json.dumps(FLOW_FALLBACK["mirror_ball"]))
    if raw is None:
        return out
    if not isinstance(raw, dict):
        probs.append(f"{where}: not a mapping: off")
        return out
    for k in ("power_member", "power_on", "direction_target", "speed_target"):
        v = raw.get(k)
        if v is not None and not (isinstance(v, str) and v):
            probs.append(f"{where}.{k}: {v!r} isn't a room name")
        else:
            out[k] = v
    dirs = raw.get("directions")
    if dirs is not None:
        if isinstance(dirs, dict) and all(d in FLOW_DIRS and isinstance(w, str) for d, w in dirs.items()):
            out["directions"] = dict(dirs)
        else:
            probs.append(f"{where}.directions: not {{direction: the room's word}}")
    sp = raw.get("speeds")
    if sp is not None:
        if isinstance(sp, list) and all(isinstance(x, list) and len(x) == 2 and _num(x[0]) is not None
                                        and isinstance(x[1], str) for x in sp):
            out["speeds"] = sorted(([float(a), b] for a, b in sp), key=lambda x: x[0])
        else:
            probs.append(f"{where}.speeds: not [[from 0..1, the room's speed word], ...]")
    g = raw.get("min_gap_s")
    if g is not None:
        if _num(g) is not None and g >= 0:
            out["min_gap_s"] = float(g)
        else:
            probs.append(f"{where}.min_gap_s: {g!r} isn't a number >= 0")
    en = raw.get("enabled", False)
    if not isinstance(en, bool):
        probs.append(f"{where}.enabled: {en!r} isn't true or false: off")
        en = False
    if en and not (out["power_member"] and out["power_on"] and out["directions"]
                   and (out["direction_target"] or out["speed_target"])):
        probs.append(f"{where}: needs power_member, power_on, directions and a target: off")
        en = False
    out["enabled"] = en
    return out


def _parse_blinds(raw: Any, probs: list[str], where: str) -> dict:
    out = {"enabled": False, "targets": {}}
    if raw is None:
        return out
    if not isinstance(raw, dict):
        probs.append(f"{where}: not a mapping: off")
        return out
    t = raw.get("targets")
    if t is not None:
        if isinstance(t, dict) and all(isinstance(v, dict) and all(d in FLOW_DIRS for d in v) for v in t.values()):
            out["targets"] = {str(k): dict(v) for k, v in t.items()}
        else:
            probs.append(f"{where}.targets: not {{room target: {{up: value, down: value}}}}")
    en = raw.get("enabled", False)
    if not isinstance(en, bool):
        probs.append(f"{where}.enabled: {en!r} isn't true or false: off")
        en = False
    if en and not out["targets"]:
        probs.append(f"{where}: enabled with no targets: off (set them from modes.json's blinds first)")
        en = False
    out["enabled"] = en
    return out


# -- timing ----------------------------------------------------------------------------------------------------------
def period(slow_fast: Any, speed: float, n_prev: Optional[int] = None) -> tuple[float, int]:
    """(period, n): ``slow x (fast / slow) ** speed`` moved in halvings of ``slow`` (a power of two of it, looks.py's
    rule) and clamped to the pair; n the halvings. ``n_prev``: keep it until the raw value is past the halfway point
    plus PERIOD_HYST, so a jittering speed doesn't flip the period."""
    slow, fast = float(slow_fast[0]), float(slow_fast[1])
    if slow <= 0 or fast <= 0 or slow == fast:
        return slow, 0
    p = slow * (fast / slow) ** _c01(speed)
    x = math.log2(slow / p)
    n = round(x)
    if n_prev is not None and abs(x - n_prev) <= 0.5 + PERIOD_HYST:
        n = n_prev
    q = slow / (2.0 ** n)
    lo, hi = min(slow, fast), max(slow, fast)
    q = min(hi, max(lo, q))
    return q, round(math.log2(slow / q))


def speed_word(speeds: list, speed: float) -> Optional[str]:
    """The last ``[from, word]`` whose ``from`` <= speed (lights.flow.mirror_ball.speeds)."""
    got = None
    for frm, word in speeds:
        if speed >= frm - 1e-9:
            got = word
    return got


# -- the decision ---------------------------------------------------------------------------------------------------------
class FlowDecider:
    """docs/LIGHTS.md §4.2 (module docstring). One per director; every method on show-brain."""

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg if isinstance(cfg, dict) else parse_settings(None, [])
        self.listener_min_conf = 0.0          # the director sets its LLM_CONF
        self.dir = STILL
        self.source = "none"
        self.why = "nothing leads yet"
        self.coherence = float(self.cfg["coherence"]["floor"])
        self.since_s: Optional[float] = None
        self.change_at_s: Optional[float] = None   # an event's line for the last change (None: the next line)
        self.changes = 0
        self.last_votes: dict[str, float] = {}
        self._cand: Optional[str] = None
        self._cand_since: Optional[float] = None
        self._reg: collections.deque = collections.deque(maxlen=600)   # (t_s, register) at 10 Hz, the window's
        self._slope: Optional[float] = None
        self._hold: Optional[dict] = None      # {"kind", "dir", "at_s", "until_s", "then", "why"}
        self._land: Optional[tuple[float, str]] = None   # (at_s, kind): the voted dir lands at this line
        self._phase: Optional[str] = None
        self._energy: Optional[float] = None
        self._by: dict[str, list] = {}
        self._net: dict[str, float] = {}

    def configure(self, cfg: dict) -> None:
        self.cfg = cfg

    def reset(self, now_s: Optional[float] = None) -> None:
        """Neutral (no palette): still, holds and history dropped."""
        if self.dir != STILL:
            self._set(STILL, "none", "no feel", float(self.cfg["coherence"]["floor"]), now_s, None)
        self._hold = self._land = None
        self._cand = self._cand_since = None
        self._reg.clear()
        self.last_votes, self._by, self._net = {}, {}, {}

    # -- votes ----------------------------------------------------------------------------------------------------
    def votes(self, *, phase: Optional[str], register: Optional[tuple[float, float, float]],
              trajectory: Optional[dict], moods: list[str], energy: float) -> dict[str, float]:
        """This tick's score per dir (module docstring). ``register``: (t_s, register 0..1, conf) while harmony is
        fresh and the gate open, else None."""
        c = self.cfg
        src = c["sources"]
        self._phase, self._energy = phase, energy
        scores: dict[str, float] = {}
        by: dict[str, list] = {}

        def add(source: str, d: Any, w: float, why: str) -> None:
            if d not in FLOW_DIRS or d == STILL or not w > 0:
                return
            scores[d] = scores.get(d, 0.0) + w
            by.setdefault(source, []).append({"dir": d, "w": round(w, 4), "why": why})

        if phase and phase in c["structure"]:
            add("structure", c["structure"][phase], float(src.get("structure", 0.0)),
                PHASE_WHY.get(phase, f"the {phase}"))
        self._slope = None
        if register is not None:
            t, reg, conf = register
            pc = c["pitch"]
            if _num(t) is not None and _num(reg) is not None and (_num(conf) or 0.0) >= pc["min_conf"]:
                self._reg.append((float(t), float(reg)))
                while self._reg and t - self._reg[0][0] > pc["window_s"]:
                    self._reg.popleft()
                s = self._fit_slope(pc["window_s"])
                self._slope = s
                k = pc["slope"]
                if s is not None and abs(s) >= k:
                    add("pitch", "up" if s > 0 else "down", float(src.get("pitch", 0.0)) * min(1.0, abs(s) / (2 * k)),
                        "rising melody" if s > 0 else "falling melody")
        if isinstance(trajectory, dict):
            st = trajectory.get("state")
            conf = _num(trajectory.get("conf"))
            if conf is None:
                conf = _num(trajectory.get("confidence"))
            if st in c["listener"] and conf is not None and conf >= self.listener_min_conf:
                add("listener", c["listener"][st], float(src.get("listener", 0.0)) * _c01(conf),
                    f"the listener hears it {str(st).replace('_', ' ')}")
        for m in [x for x in (moods or []) if isinstance(x, str)][:3]:
            if m in c["moods"]:
                add("moods", c["moods"][m], float(src.get("moods", 0.0)) / 3.0, f"{m} mood")
        self.last_votes, self._by = scores, by
        return scores

    def _fit_slope(self, window_s: float) -> Optional[float]:
        pts = list(self._reg)
        if len(pts) < PITCH_MIN_SAMPLES or pts[-1][0] - pts[0][0] < PITCH_MIN_SPAN * window_s:
            return None
        n = len(pts)
        mt = sum(p[0] for p in pts) / n
        mr = sum(p[1] for p in pts) / n
        sxx = sum((p[0] - mt) ** 2 for p in pts)
        if sxx <= 0:
            return None
        return sum((p[0] - mt) * (p[1] - mr) for p in pts) / sxx

    # -- events ---------------------------------------------------------------------------------------------------
    def event(self, kind: str, at_s: float, grid_bar_s: Optional[float]) -> None:
        """A music event at its line ``at_s`` (lights.flow.events.<kind>): hold a dir (for ``bars``, then ``then``;
        or while the phase is ``kind``), or land the voted dir at the line (no ``dir``)."""
        spec = self.cfg["events"].get(kind)
        if spec is None or _num(at_s) is None:
            return
        why = PHASE_WHY.get(kind, kind.replace("_", " "))
        if spec.get("dir") is None:
            self._land = (float(at_s), kind)
            return
        bars = spec.get("bars")
        until = float(at_s) + float(bars) * (grid_bar_s or NO_GRID_BAR_S) if bars else None
        self._hold = {"kind": kind, "dir": spec["dir"], "at_s": float(at_s), "until_s": until,
                      "then": spec.get("then"), "why": why}

    # -- the decision ---------------------------------------------------------------------------------------------
    def _strength(self, d: str, quiet: bool) -> float:
        if d == STILL:
            return math.inf if quiet else float(self.cfg["min_score"])
        return self._net.get(d, 0.0)

    def _leader(self, votes: Mapping[str, float]) -> tuple[str, float, bool]:
        net = {d: float(votes.get(d, 0.0)) - float(votes.get(opposite(d) or "", 0.0)) for d in votes}
        self._net = net
        e = self._energy
        quiet = e is not None and e < float(self.cfg["still_below_energy"])
        best = max(net.items(), key=lambda kv: (kv[1], -FLOW_DIRS.index(kv[0]))) if net else None
        if quiet or best is None or best[1] < float(self.cfg["min_score"]):
            return STILL, 0.0, quiet
        return best[0], best[1], quiet

    def _source_of(self, d: str) -> tuple[str, str]:
        best: Optional[tuple[float, str, str]] = None
        for source, vs in self._by.items():
            for v in vs:
                if v["dir"] == d and (best is None or v["w"] > best[0]):
                    best = (v["w"], source, v["why"])
        return (best[1], best[2]) if best else ("none", "nothing leads")

    def _set(self, d: str, source: str, why: str, coherence: float, now_s: Optional[float],
             at_s: Optional[float]) -> None:
        self.dir, self.source, self.why, self.coherence = d, source, why, coherence
        self.since_s = now_s
        self.change_at_s = at_s
        self.changes += 1
        self._cand = self._cand_since = None

    def step(self, now_s: float, votes: Mapping[str, float]) -> tuple[str, str, float, str]:
        """(dir, source, coherence, why) now (module docstring). ``change_at_s`` says when the last change takes effect
        when an event set it."""
        c = self.cfg
        ev_coh = float(c["coherence"]["events"])
        floor = float(c["coherence"]["floor"])
        leader, lead_net, quiet = self._leader(votes)
        h = self._hold
        if h is not None:
            nxt = None
            ended = False
            if h["until_s"] is not None and now_s >= h["until_s"]:
                ended, nxt = True, h.get("then")
            elif h["until_s"] is None and now_s >= h["at_s"] and self._phase != h["kind"]:
                ended = True
            if ended:
                self._hold = None
                if nxt is not None and nxt != self.dir:
                    self._set(nxt, "event", f"after {h['why']}", ev_coh, now_s, None)
                return self.dir, self.source, self.coherence, self.why
            if self.dir != h["dir"]:
                self._set(h["dir"], "event", h["why"], ev_coh, now_s, max(h["at_s"], now_s))
            self.coherence = ev_coh
            return self.dir, self.source, self.coherence, self.why
        land = self._land
        if land is not None:
            self._land = None
            if leader != self.dir:
                src, why = self._source_of(leader) if leader != STILL else ("none", "quiet" if quiet else
                                                                                 "nothing leads")
                self._set(leader, "event", f"{PHASE_WHY.get(land[1], land[1])}: {why}", ev_coh, now_s,
                          max(land[0], now_s))
            return self.dir, self.source, self.coherence, self.why
        if leader == self.dir:
            self._cand = self._cand_since = None
        elif self._strength(leader, quiet) >= self._strength(self.dir, quiet) + float(c["margin"]):
            if self._cand != leader:
                self._cand, self._cand_since = leader, now_s
            if now_s - (self._cand_since or now_s) >= float(c["dwell_s"]) - 1e-9:
                if leader == STILL:
                    self._set(STILL, "none", "quiet" if quiet else "nothing leads", floor, now_s, None)
                else:
                    src, why = self._source_of(leader)
                    self._set(leader, src, why, floor, now_s, None)
        else:
            self._cand = self._cand_since = None
        if self.dir == STILL:
            self.coherence = floor
        else:
            self.coherence = max(floor, min(1.0, self._net.get(self.dir, 0.0) / (2.0 * float(c["min_score"]))))
        return self.dir, self.source, self.coherence, self.why

    def view(self) -> dict:
        """For the snapshot: the votes, the current dir and the one waiting, a hold."""
        return {"dir": self.dir, "source": self.source, "why": self.why, "coherence": round(self.coherence, 3),
                "since_s": self.since_s, "changes": self.changes,
                "votes": {d: round(v, 3) for d, v in self.last_votes.items()},
                "net": {d: round(v, 3) for d, v in self._net.items()},
                "by_source": {k: list(v) for k, v in self._by.items()},
                "candidate": self._cand, "candidate_since_s": self._cand_since,
                "pitch_slope": round(self._slope, 4) if self._slope is not None else None,
                "hold": dict(self._hold) if self._hold else None,
                "land": list(self._land) if self._land else None}
