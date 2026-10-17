"""compose: how much each enabled appliance answers the music (docs/SHOW_ENGINE.md §8.10 "Composition and balance";
README S5.18; session 75's design, the owner's rulings of 17 Oct: one composer (the Director), many renderers; modes
only switch appliances on; volume is NEVER intensity; the Director decides how much each enabled appliance responds,
for an acceptable overall tonal balance and compositional equilibrium).

PURE: inputs in, plan out. No I/O, no threads, no clock reads. The Director owns a ``State`` (the loudness easing, the
song's loudness window, the eased range and lift) and hands it in with each tick's ``Inputs``; ``step`` changes
nothing but that state. Everything else returns new values.

For each enabled appliance A and dimension x (DIMS: picture, level, pace, pulse, depth, change)::

  s     = clamp01(follow x W[A,x] x liveness x range x (1 - hold_A))
  quiet = 1 - clamp01(loud + lift[A,x])
  value = max(floor[A,x], hard_floor[A,x], base[A,x] x (1 - s x (1 - q[A,x]) x quiet x h[A,x]))

  loud      features.gate (lufs_s against the input's own floor-following reference, dsp.py), eased asymmetrically:
            up over loud.rise_s, down over loud.fall_s (slower), and over loud.fade_fall_s while a fade is cued
            (structure's outro, or an armed song end). While the piano plays its MIDI velocity replaces it. Music
            only (loud.music_only): while people talk it reads 0, so speech is never loud. Stale features hold it:
            missing audio is unknown, not quiet (liveness runs the share down instead).
  range     the song's own dynamic range: p90 - p10 of lufs_s over range.window_s (music only: gate >= min_gate),
            through smoothstep(range.lu[0], range.lu[1]) and eased over smooth.tau_s. A compressed song or an AGC'd
            input reads ~0, so nothing pumps. Under MIN_RANGE_S of music in the window: 0.
  lift      per dimension (lift.<dim>.tension / .build): the harmony's tension and structure's build_progress, eased;
            a quiet but tense build keeps the picture up. Starting point: the picture only.
  h         headroom, on headroom.dims (the picture): smoothstep(apl[0], apl[1], the scene's APL) once a renderer
            reports one (README S5.18 "step 3": the ``apl`` input, None today), else headroom.unknown. A dark scene is
            not dimmed further. Every other dimension: 1.
  W         the appliance's role weight: a lamp role's ``dynamics`` (show.yaml lamps.roles), else weights.<class>.
  hold      1 while a lamp shows House lights or Lights low, or a screen's cut guard is up (the room's cut phase now;
            the renderer's MasterBus guard in step 3).
  follow, floor, q, base   the feel's LIMITS (vibes.yaml <feel>.compose, ``parse_limits``); no feel, or a feel with
            no compose block: show.yaml director.compose.defaults. A cell a block leaves out is NEUTRAL_CELL (follow
            0, floor 0, q 1, base 1): that dimension never moves, which is today. The numbers are limits, not the
            answer: the answer is the loudness, the range, the tension, the build and (later) the scene's APL.

Values are shares of today's value (base 1 = today): the picture a gain for the scene's own brightness parameters,
level the lamps' level, pace and change multipliers on the room's pace and change rate, pulse the beat accents'
depth, depth the music depth (the renderer's binding depth). With s = 0 (no audio, a flat song, follow 0) every
value is its base.

``enabled`` is the NEW enabled-set reader: screens whose room layer is ``live``, room members whose believed state is
exactly ``dmx`` (a Hue area streaming the show's Art-Net; ``taken_over`` never counts). Read, never decided: the modes
decide (owner ruling 4). No device or mode name is known here.

``validate`` lists a dimension whose loudness weights (today one term: the dynamics share, follow x W x (1 - q))
sum over 1, and a floor over its base.
"""

from __future__ import annotations

import collections
import copy
import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

DIMS: tuple[str, ...] = ("picture", "level", "pace", "pulse", "depth", "change")
CLASSES: tuple[str, ...] = ("screens", "lamps")      # what the enabled reader finds: live screens, members that follow
MODES: tuple[str, ...] = ("observe", "drive")         # observe: publish the plan only; drive: the outputs take it
PICTURE, LEVEL, PACE, PULSE, DEPTH, CHANGE = DIMS     # readers name a dimension by these, never by a literal
SCREENS, LAMPS = CLASSES
OBSERVE, DRIVE = MODES
LIMIT_KEYS: tuple[str, ...] = ("follow", "floor", "q", "base")
NEUTRAL_CELL: dict[str, float] = {"follow": 0.0, "floor": 0.0, "q": 1.0, "base": 1.0}
LIVE_LAYER = "live"                 # contract.RoomView screens' layer: art | live
FOLLOW_STATE = "dmx"                # a room member following the show's DMX (room's hue value); taken_over never counts
FOLD_DEFAULT: tuple[float, float] = (70.0, 180.0)    # stream_service music_response.json tempo.fold_min / fold_max
SAMPLE_S = 0.5                      # the loudness window takes lufs_s at 2 Hz (dsp's short-term loudness, <= 5 Hz)
RANGE_EVERY_S = 1.0                 # p90 - p10 is worked out at most once a second
MIN_RANGE_S = 10.0                  # under this much music in the window the range reads 0 (nothing pumps yet)

#: show.yaml director.compose's starting points (the file's values win; a bad one takes its starting point and is
#: listed). ``defaults`` here is the code's neutral (nothing answers: today); show.yaml ships the no-feel starting point.
COMPOSE_FALLBACK: dict[str, Any] = {
    "mode": "observe",
    "loud": {"rise_s": 0.7, "fall_s": 1.5, "fade_fall_s": 0.35, "music_only": True},
    "range": {"window_s": 60.0, "lu": [3.0, 12.0], "min_gate": 0.05},
    "response_file": "config/live/music_response.json",
    "headroom": {"apl": [0.12, 0.45], "unknown": 0.5, "dims": ["picture"]},
    "lift": {"picture": {"tension": 0.3, "build": 0.3}},
    "weights": {"screens": 1.0, "lamps": 1.0},
    "hard_floor": {"screens": {"picture": 0.6}},
    "smooth": {"tau_s": 1.0, "deadband": 0.02},
    "defaults": {"follow": 0.0},
}
_SECTION_NUMS: dict[str, dict[str, tuple[float, float]]] = {
    "loud": {"rise_s": (0.0, 30.0), "fall_s": (0.0, 60.0), "fade_fall_s": (0.0, 60.0)},
    "range": {"window_s": (5.0, 600.0), "min_gate": (0.0, 1.0)},
    "headroom": {"unknown": (0.0, 1.0)},
    "smooth": {"tau_s": (0.0, 30.0), "deadband": (0.0, 0.5)},
}


def _num(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return float(v)
    return None


def c01(x: float) -> float:
    return 0.0 if not x > 0.0 else 1.0 if x > 1.0 else x


def smoothstep(e0: float, e1: float, x: float) -> float:
    if e1 <= e0:
        return 1.0 if x >= e1 else 0.0
    u = c01((x - e0) / (e1 - e0))
    return u * u * (3.0 - 2.0 * u)


def fold_bpm(bpm: float, lo: float, hi: float) -> float:
    """``bpm`` doubled or halved into [lo, hi) (copied from stream_service/music_feed.py fold_bpm, §9.3: copied, not
    imported): a half / double-time lock is the same pulse; a range narrower than an octave folds up only."""
    if bpm <= 0.0 or not (0.0 < lo < hi):
        return bpm
    for _ in range(8):
        if bpm < lo:
            bpm *= 2.0
        elif bpm >= hi and bpm / 2.0 >= lo:
            bpm /= 2.0
        else:
            break
    return bpm


def _pct(sorted_vals: list[float], p: float) -> float:
    """Linear-interpolated percentile of an ascending list (p in 0..1)."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    x = p * (n - 1)
    i = int(math.floor(x))
    j = min(n - 1, i + 1)
    return sorted_vals[i] + (sorted_vals[j] - sorted_vals[i]) * (x - i)


# -- settings ------------------------------------------------------------------------------------------------------------
def _unit(v: Any) -> Optional[float]:
    x = _num(v)
    return x if x is not None and 0.0 <= x <= 1.0 else None


def _parse_spec(v: Any, where: str, probs: list[str]) -> Any:
    """One limit: a number 0..1 (every class and dimension) or ``{class: number | {dim: number}}``. A bad value, an
    unknown class or dimension is listed and left out (None: the whole limit is left out)."""
    if not isinstance(v, dict):
        x = _unit(v)
        if x is None:
            probs.append(f"{where}: {v!r} is not a number in 0..1: ignored")
        return x
    per: dict = {}
    for cls, cv in v.items():
        if cls not in CLASSES:
            probs.append(f"{where}.{cls}: not an appliance class ({', '.join(CLASSES)}): ignored")
            continue
        if not isinstance(cv, dict):
            x = _unit(cv)
            if x is None:
                probs.append(f"{where}.{cls}: {cv!r} is not a number in 0..1: ignored")
            else:
                per[cls] = x
            continue
        dims: dict = {}
        for dim, dv in cv.items():
            if dim not in DIMS:
                probs.append(f"{where}.{cls}.{dim}: not a dimension ({', '.join(DIMS)}): ignored")
                continue
            x = _unit(dv)
            if x is None:
                probs.append(f"{where}.{cls}.{dim}: {dv!r} is not a number in 0..1: ignored")
            else:
                dims[dim] = x
        per[cls] = dims
    return per


def parse_limits(raw: Any, where: str, probs: list[str]) -> dict:
    """A compose block's LIMITS (a feel's vibes.yaml ``compose``, or show.yaml director.compose.defaults) -> the clean
    block: each of follow, floor, q, base is a number (every class and dimension) or ``{class: number | {dim:
    number}}``, all 0..1. A bad value, an unknown class, dimension or key is listed and left out (that cell is
    NEUTRAL_CELL's): degrade, never refuse."""
    out: dict = {}
    if raw is None:
        return out
    if not isinstance(raw, dict):
        probs.append(f"{where}: not a mapping: ignored")
        return out
    for k, v in raw.items():
        if k not in LIMIT_KEYS:
            probs.append(f"{where}.{k}: unknown key (one of {', '.join(LIMIT_KEYS)}): ignored")
            continue
        spec = _parse_spec(v, f"{where}.{k}", probs)
        if spec is not None:
            out[k] = spec
    return out


def _cell(spec: Any, key: str, cls: str, dim: str) -> float:
    if spec is None:
        return NEUTRAL_CELL[key]
    if not isinstance(spec, dict):
        return float(spec)
    c = spec.get(cls)
    if c is None:
        return NEUTRAL_CELL[key]
    if not isinstance(c, dict):
        return float(c)
    v = c.get(dim)
    return NEUTRAL_CELL[key] if v is None else float(v)


def table(limits: Optional[Mapping]) -> dict[str, dict[str, dict[str, float]]]:
    """A clean limits block -> {class: {dim: {follow, floor, q, base}}}, every cell filled (NEUTRAL_CELL for what the
    block leaves out). Built once per change; the tick only looks cells up."""
    lim = limits or {}
    return {cls: {dim: {k: _cell(lim.get(k), k, cls, dim) for k in LIMIT_KEYS} for dim in DIMS} for cls in CLASSES}


def _pair(v: Any, lo: float, hi: float) -> Optional[list[float]]:
    if isinstance(v, (list, tuple)) and len(v) == 2 and all(_num(x) is not None for x in v) \
            and lo <= float(v[0]) <= float(v[1]) <= hi:
        return [float(v[0]), float(v[1])]
    return None


def parse_settings(raw: Any, probs: list[str]) -> dict:
    """show.yaml director.compose -> the resolved settings (COMPOSE_FALLBACK fills what is missing; a bad value takes
    its starting point and is listed). ``defaults`` is parsed by ``parse_limits``."""
    out = copy.deepcopy(COMPOSE_FALLBACK)
    p = "director.compose"
    if raw is None:
        return out
    if not isinstance(raw, dict):
        probs.append(f"{p}: not a mapping: the starting points used")
        return out
    known = set(COMPOSE_FALLBACK)
    for k in raw:
        if k not in known:
            probs.append(f"{p}.{k}: unknown key: ignored")
    if "mode" in raw:
        if raw["mode"] in MODES:
            out["mode"] = raw["mode"]
        else:
            probs.append(f"{p}.mode: {raw['mode']!r} is not {' | '.join(MODES)}: observe")
    for sec, keys in _SECTION_NUMS.items():
        got = raw.get(sec)
        if got is None:
            continue
        if not isinstance(got, dict):
            probs.append(f"{p}.{sec}: not a mapping")
            continue
        for k2, (lo, hi) in keys.items():
            if k2 in got:
                v = _num(got[k2])
                if v is not None and lo <= v <= hi:
                    out[sec][k2] = v
                else:
                    probs.append(f"{p}.{sec}.{k2}: {got[k2]!r} is not a number in {lo:g}..{hi:g}")
    ld = raw.get("loud") if isinstance(raw.get("loud"), dict) else {}
    if "music_only" in ld:
        if isinstance(ld["music_only"], bool):
            out["loud"]["music_only"] = ld["music_only"]
        else:
            probs.append(f"{p}.loud.music_only: {ld['music_only']!r} is not true/false")
    rg = raw.get("range") if isinstance(raw.get("range"), dict) else {}
    if "lu" in rg:
        v = _pair(rg["lu"], 0.0, 60.0)
        if v is not None and v[1] > v[0]:
            out["range"]["lu"] = v
        else:
            probs.append(f"{p}.range.lu: {rg['lu']!r} is not [low, high] LU, low < high")
    hr = raw.get("headroom") if isinstance(raw.get("headroom"), dict) else {}
    if "apl" in hr:
        v = _pair(hr["apl"], 0.0, 1.0)
        if v is not None and v[1] > v[0]:
            out["headroom"]["apl"] = v
        else:
            probs.append(f"{p}.headroom.apl: {hr['apl']!r} is not [dark, bright] in 0..1, dark < bright")
    if "dims" in hr:
        d = hr["dims"]
        if isinstance(d, list) and all(x in DIMS for x in d):
            out["headroom"]["dims"] = list(d)
        else:
            probs.append(f"{p}.headroom.dims: {d!r} is not a list of dimensions ({', '.join(DIMS)})")
    if "response_file" in raw:
        if isinstance(raw["response_file"], str) and raw["response_file"].strip():
            out["response_file"] = raw["response_file"].strip()
        else:
            probs.append(f"{p}.response_file: {raw['response_file']!r} is not a path")
    if "lift" in raw:
        lf = raw["lift"]
        if not isinstance(lf, dict):
            probs.append(f"{p}.lift: not a mapping")
        else:
            clean: dict = {}
            for dim, terms in lf.items():
                if dim not in DIMS or not isinstance(terms, dict):
                    probs.append(f"{p}.lift.{dim}: needs a dimension with {{tension, build}}: ignored")
                    continue
                t: dict = {}
                for k2 in ("tension", "build"):
                    if k2 in terms:
                        v = _num(terms[k2])
                        if v is not None and 0.0 <= v <= 1.0:
                            t[k2] = v
                        else:
                            probs.append(f"{p}.lift.{dim}.{k2}: {terms[k2]!r} is not a number in 0..1")
                for k2 in terms:
                    if k2 not in ("tension", "build"):
                        probs.append(f"{p}.lift.{dim}.{k2}: unknown term: ignored")
                clean[dim] = t
            out["lift"] = clean
    if "weights" in raw:
        w = raw["weights"]
        if not isinstance(w, dict):
            probs.append(f"{p}.weights: not a mapping")
        else:
            for cls, v in w.items():
                x = _num(v)
                if cls not in CLASSES:
                    probs.append(f"{p}.weights.{cls}: not an appliance class: ignored")
                elif x is None or not 0.0 <= x <= 2.0:
                    probs.append(f"{p}.weights.{cls}: {v!r} is not a number in 0..2")
                else:
                    out["weights"][cls] = x
    if "hard_floor" in raw:
        hf = _parse_spec(raw["hard_floor"], f"{p}.hard_floor", probs)
        if hf is not None:
            out["hard_floor"] = hf
    if "defaults" in raw:
        out["defaults"] = parse_limits(raw["defaults"], f"{p}.defaults", probs)
    return out


def loudness_terms(tab: Mapping, weights: Mapping[str, float]) -> dict[tuple[str, str], list[float]]:
    """{(class, dim): [the loudness weights reaching that dimension]}: today one term, the dynamics share's largest
    slope follow x W x (1 - q) (W: the class's largest role weight). A later loudness path adds its own term here."""
    out: dict[tuple[str, str], list[float]] = {}
    for cls in CLASSES:
        w = float(weights.get(cls, 1.0))
        for dim in DIMS:
            c = tab[cls][dim]
            out[(cls, dim)] = [c["follow"] * w * (1.0 - c["q"])]
    return out


def validate(tab: Mapping, weights: Mapping[str, float], where: str) -> list[str]:
    """The validator (README S5.18): a dimension whose loudness weights sum over 1 (the share is capped at 1, so the
    room would answer less than the block asks), and a floor over its base (the floor wins)."""
    probs: list[str] = []
    for (cls, dim), terms in loudness_terms(tab, weights).items():
        tot = sum(terms)
        if tot > 1.0 + 1e-9:
            probs.append(f"{where}: {cls}.{dim}: the loudness weights sum to {tot:.2f}, over 1 (follow x W x (1 - q)"
                         f"; the share is capped at 1)")
        c = tab[cls][dim]
        if c["floor"] > c["base"] + 1e-9:
            probs.append(f"{where}: {cls}.{dim}: floor {c['floor']:g} is over base {c['base']:g}: the floor wins")
    return probs


# -- the enabled-set reader ------------------------------------------------------------------------------------------------
def enabled(view: Any) -> tuple[list[str], list[str]]:
    """(screens, members) from the RoomView the Director already reads: the screens whose layer is ``live`` and the
    room members ("<subsystem>.<member>") whose believed state is exactly ``dmx`` (a Hue area streaming the show's
    Art-Net; ``taken_over``, another streamer holding the area, never counts). Read, never decided."""
    if not isinstance(view, dict):
        return [], []
    scr = view.get("screens") if isinstance(view.get("screens"), dict) else {}
    mem = view.get("members") if isinstance(view.get("members"), dict) else {}
    screens = sorted(str(s) for s, x in scr.items() if isinstance(x, dict) and x.get("layer") == LIVE_LAYER)
    members = sorted(str(m) for m, x in mem.items() if isinstance(x, dict) and x.get("believed") == FOLLOW_STATE)
    return screens, members


# -- the tick --------------------------------------------------------------------------------------------------------------
@dataclass
class State:
    """The composer's memory, owned by the Director and handed to ``step`` (nothing else changes it)."""
    loud: Optional[float] = None                    # the eased loudness (None until the first music or piano)
    samples: collections.deque = field(default_factory=collections.deque)   # (t_s, lufs_s) music samples
    next_sample_s: float = -math.inf
    next_range_s: float = -math.inf
    range_lu: Optional[float] = None                # p90 - p10 over the window (None: too little music)
    range_want: float = 0.0                          # smoothstep of it
    range: float = 0.0                               # eased
    lift: dict = field(default_factory=dict)         # dim -> eased lift


@dataclass
class Inputs:
    """One tick's inputs, read by the Director (no I/O here)."""
    now_s: float
    dt: float
    fresh: bool = False                  # features fresh (dsp at 25 Hz)
    gate: Optional[float] = None         # features.gate 0..1
    lufs_s: Optional[float] = None       # features.lufs_s
    liveness: float = 0.0                # the Director's liveness (holds 10 s after the features go stale, then fades)
    piano: Optional[float] = None        # the piano's MIDI velocity 0..1 while it plays, else None
    talking: bool = False                # situations' talking (CED speech with no music)
    fade: bool = False                   # a fade is cued: structure's outro, or an armed song end
    tension: float = 0.0                 # music.harmony tension (smoothed), 0..1
    build: float = 0.0                   # structure's build_progress while the phase is build, else 0


SILENT_GATE = 0.02     # features.gate under this (the clock's and artnet_out's quiet threshold): loud falls at fade_fall_s


def _ease(prev: float, want: float, dt: float, tau: float) -> float:
    if tau <= 0.0:
        return want
    return prev + (want - prev) * (1.0 - math.exp(-dt / tau)) if dt > 0.0 else prev


def step(state: State, inp: Inputs, cfg: Mapping) -> dict:
    """One tick: ease the loudness, keep the song's loudness window, ease the range and lift. Returns the room-level
    terms {loud, range, range_lu, lift: {dim: x}, liveness, fade} that ``plan_one`` reads. Changes only ``state``."""
    lc = cfg["loud"]
    if inp.piano is not None:
        want: Optional[float] = c01(inp.piano)                # MIDI velocity replaces it while the piano plays
    elif not inp.fresh or inp.gate is None:
        want = None                                        # unknown, not quiet: hold
    elif lc["music_only"] and inp.talking:
        want = 0.0                                         # speech is never loud
    else:
        want = c01(inp.gate)
    if want is not None:
        if state.loud is None:
            state.loud = want
        else:
            silent = want < SILENT_GATE                    # the gate has shut: silence is a fade (75: quiet in ~2 s)
            tau = lc["rise_s"] if want > state.loud else (lc["fade_fall_s"] if (inp.fade or silent) else lc["fall_s"])
            state.loud = _ease(state.loud, want, inp.dt, float(tau))
    rc = cfg["range"]
    if (inp.fresh and inp.lufs_s is not None and inp.gate is not None and inp.gate >= rc["min_gate"]
            and not (lc["music_only"] and inp.talking) and inp.now_s >= state.next_sample_s):
        state.next_sample_s = inp.now_s + SAMPLE_S
        state.samples.append((inp.now_s, float(inp.lufs_s)))
    win = float(rc["window_s"])
    while state.samples and state.samples[0][0] < inp.now_s - win:
        state.samples.popleft()
    if inp.now_s >= state.next_range_s:
        state.next_range_s = inp.now_s + RANGE_EVERY_S
        if len(state.samples) * SAMPLE_S >= MIN_RANGE_S:
            vals = sorted(v for _, v in state.samples)
            state.range_lu = _pct(vals, 0.9) - _pct(vals, 0.1)
            state.range_want = smoothstep(float(rc["lu"][0]), float(rc["lu"][1]), state.range_lu)
        else:
            state.range_lu, state.range_want = None, 0.0
    tau = float(cfg["smooth"]["tau_s"])
    state.range = _ease(state.range, state.range_want, inp.dt, tau)
    lift: dict[str, float] = {}
    for dim, terms in cfg["lift"].items():
        want_l = c01(float(terms.get("tension", 0.0)) * c01(inp.tension) + float(terms.get("build", 0.0))
                     * c01(inp.build))
        state.lift[dim] = _ease(state.lift.get(dim, want_l), want_l, inp.dt, tau)
        lift[dim] = state.lift[dim]
    return {"loud": state.loud, "range": state.range, "range_lu": state.range_lu, "lift": lift,
            "liveness": c01(inp.liveness), "fade": bool(inp.fade)}


def headroom(cfg: Mapping, apl: Optional[float]) -> float:
    """The picture's headroom: smoothstep(apl_dark, apl_bright, APL) once the renderer reports the scene's APL (step
    3), else headroom.unknown."""
    hr = cfg["headroom"]
    if apl is None or _num(apl) is None:
        return float(hr["unknown"])
    return smoothstep(float(hr["apl"][0]), float(hr["apl"][1]), float(apl))


def plan_one(terms: Mapping, tab: Mapping, cfg: Mapping, cls: str, *, weight: float, hold: bool = False,
             apl: Optional[float] = None) -> tuple[dict[str, float], dict[str, float], dict[str, float],
                                                    dict[str, float]]:
    """One appliance of class ``cls``: (values, s, quiet, h), each {dim: x} (module docstring's formula)."""
    loud = terms["loud"] if terms["loud"] is not None else 0.0
    base_s = c01(terms["liveness"]) * c01(terms["range"]) * float(weight) * (0.0 if hold else 1.0)
    hard = cfg["hard_floor"]
    hdims = cfg["headroom"]["dims"]
    h_pic = headroom(cfg, apl)
    lift = terms["lift"]
    vals: dict[str, float] = {}
    ss: dict[str, float] = {}
    qq: dict[str, float] = {}
    hh: dict[str, float] = {}
    row = tab[cls]
    for dim in DIMS:
        c = row[dim]
        s = c01(c["follow"] * base_s)
        quiet = 1.0 - c01(loud + lift.get(dim, 0.0))
        h = h_pic if dim in hdims else 1.0
        v = c["base"] * (1.0 - s * (1.0 - c["q"]) * quiet * h)
        vals[dim] = max(c["floor"], _cell(hard, "floor", cls, dim), v)
        ss[dim], qq[dim], hh[dim] = s, quiet, h
    return vals, ss, qq, hh


def rounded(d: Mapping[str, float], nd: int = 3) -> dict[str, float]:
    return {k: round(float(v), nd) for k, v in d.items()}
