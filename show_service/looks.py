"""looks: the look engine (docs/SHOW_ENGINE.md §9.1, §5.4, §8.2-§8.3; README "Stage 5", S5.5). B13's.

One ``LookEngine`` per Rig. show-brain compiles a Program from show.yaml ``looks`` and the fixture registry
(``configure``); each output's runner thread (``show-out-<id>``) asks for a frame at its planned send time
(``evaluate``). Every input is read by reference, nothing locks, nothing blocks, and a runner touches only its own
output's state (smoothers, glides, the per-fixture slew and flash-cap memory, its cursor into the hit ring).

**The screens rule** (S5.0 rule 3, §8.3). A fixture whose type's kind is ``screen`` is never evaluated: it never
appears in a Frame, so it never gets a level, floor, master, cap, dip, hit or intensity layer. Only ``lamp`` fixtures
reach a frame.

``evaluate(output, t)``, per lamp fixture of that output:
  1. **Times.** ``ti = t + rise_ms`` and ``tc = t + colour_ms`` (the fixture's LatencyView, §5.4: each output evaluates
     its look at now + L(output, attribute)). ``palette_fn`` is called once per distinct time in a frame.
  2. **Base (allocation).** The director allocates colours per fixture from the lamp roles (placement -> role,
     show.yaml ``lamps``) and hands them over keyed by fixture id (``palette_fn``: crossfades, House, Low and dips).
     The drop's unison hit is not in palette_fn: it comes in ``look_fn().hit`` (contract.LookHit) and is laid over
     the director's colours per output from its caps (README S5.14): an output that takes hits (DMX) shows it at
     once for [t0, t1); one that doesn't (Hue) shows it at once only for a predicted drop line, else as a swell
     (smoothstep up over ``swell_ms``, §5.4's >= 300 ms, and back down after t1), its colour moving with the level.
     A fixture the director doesn't name takes, in order: the colour of a lamp the director
     gives the same role (``look_fn().lamps[*].role``; the fixture's role is its own, else, given show.yaml ``lamps``,
     its placement's or the default role, as the director decides it), then of a registry fixture with the same
     written role or placement, then the same decided role (same screen first, then same zone, then by id), then
     ``"*"``, then its ``house`` look, else dark. No vibe (``palette_fn`` gives no dict): the neutral colour at full
     value (artnet_out's P0 colour), or the one colour ``palette_fn`` gave. ``level = max(rgb at ti)``, ``colour = rgb
     at tc / max``.
  3. **Activity**, exactly artnet_out's P0 idle rule: with a vibe, always active (its relaxed look, House and Low go
     out in a quiet room: §8.5 "never black"), but while the features are stale or the room is quiet the frame is
     *held*: no layer and no hit follows the sound (artnet_out sends hue_depth 0 then). With no vibe: inactive when
     the features are older than IDLE_AFTER_S (``no_features``), or the room is quiet (gate under QUIET_GATE_OFF for
     QUIET_AFTER_S, until it is back over QUIET_GATE_ON: ``quiet``). On an output whose caps say
     ``idle_without_vibe`` (artnet: nothing room-side decides which DMX fixtures follow, §8.6) no vibe is inactive
     whatever the sound (``no_vibe``); Hue keeps the neutral colour, since room's hue adapter decides which lamps
     follow. An inactive frame carries every fixture at its ``house`` look (or dark), with no layers, hits, master or
     caps. House lights and Lights low (LookData ``house``/``low``) are steady like a held frame: no layer, no hit.
  4. **Layers** (show.yaml ``looks.layers``, file order). Each enabled ``intensity`` layer whose selection holds the
     fixture multiplies its intensity by ``1 - d + d x shape``:
       * the phase: ``beat`` (x 1 / the look's ``accents.subdivision`` with ``subdivide``), ``bar`` (``period_bars``),
         ``phrase``, or ``time`` (``period_s``), at ``ti``, from the clock body through clock.py's ``beat_at``;
       * phase spread: member ``i`` of ``n`` in the layer's ordered selection runs ``spread x i / n`` cycles behind
         member 0, so the wave walks the group in its order (each lamp a quarter cycle apart at spread 1 in four);
       * ``d``: a number or a ``contract.LOOK_INPUTS`` word (``swell`` = the set_look depth: ``beat_depth``, the
         director's accent depth before the clock's gate, while the layer runs beat-locked, else ``hue_depth``, which
         is 0 unless the gate is ``beat``, as the virtual Maestro takes it; the neutral depth with no vibe;
         ``motion.*``, ``accents.density``, ``look.spread`` from ``look_fn()``; ``energy`` = the features' energy
         smoothed with TAU_ENERGY_S), x ``depth_scale``;
       * ``needs`` (§5.4's confidence policy): ``beat`` runs at full depth on the clock's gate ``beat``, on gate ``dmx``
         only on outputs whose caps take hits (x ``looks.middle_depth``); on gate ``none``, or with ``beat_lock``
         false, it uses ``without_beat`` (``energy``: shape = the smoothed energy, as artnet_out's swell does;
         ``none``: steady). ``grid`` needs any grid (else ``without_beat``). ``none`` always runs: without a grid it
         free-runs at the last tempo the clock had (FREE_RUN_BPM before any);
       * parameter changes glide over the look's ``transition`` (its beats at the clock's tempo, else its seconds;
         ``cut``: at once): depth and spread move continuously; the discrete ones (subdivision, a period chosen from
         ``motion.speed``) crossfade the old wave into the new one, so a wave never jumps phase. A period picked from
         a ``[slow, fast]`` pair by ``motion.speed`` moves in halvings (geometric, rounded to a power of two of the
         slow period), so a bar wave stays on the bar lines.
     ``colour`` layers are kept for later (left out with a problem line). The resolve: oscillators scale the base;
     assignments (the hits below) are highest-takes-precedence for intensity; colour is latest-takes-precedence
     (only the base sets it in Stage 5).
  5. **Shapes** (``p`` = the cycle's fraction, 1 at the beat): ``sine`` 0.5 + 0.5 cos 2πp; ``saw`` 1 − p; ``square`` 1
     for p < 0.5; ``triangle`` 1 − 2 min(p, 1 − p); ``swell``: artnet_out's envelope, max(smoothstep(1 − (to next) /
     attack) while the next cycle is within ``attack``, exp(−(since the last) / decay)), ``attack = max(layer
     attack_ms, caps.min_attack_ms)``. A beat layer whose subdivided period is under 2 x attack on this output uses the
     next coarser subdivision. Any oscillator whose cycle is shorter than the output's ``caps.min_period_ms`` (hue:
     2 x min_attack, "never strobes") runs at double its period until it isn't (``stats()`` lists it).
  6. **Hits** (only when the output's caps take them: §5.4, DMX yes, Hue never). Each ``music.hit`` (``on_hit``, any
     thread, into one ring; each runner keeps its own cursor) with ``strength >= min_strength``, a band in ``bands``
     (unset: any), while the look's ``accents.style`` is in ``looks.hits.styles``, the gate isn't ``none`` and the
     look isn't House lights, Lights low or the pre-drop dip (LookData ``house``/``low``/``dip``: steady, as the
     director's own drop hit and its swell depth are; c8's integration ruling, 16 Oct, and README S5.14):
     ``flash = depth x strength``, held for the fixture's ``min_pulse_ms`` from the hit's time, then decaying with
     ``decay_ms``; ``intensity = max(intensity, min(1, level + flash))`` (HTP). A hit is shown at once (evaluated at
     the send time), never delayed to its own latency.
  7. **Master and caps** (lamps only): x ``looks.master``; ``caps.flash_hz_max`` (unset: off) lets a rise of more than
     0.5 above the fixture's low since its last such rise happen at most that often; then the output's rise limit: no
     rise is steeper than a smoothstep over ``caps.min_attack_ms`` (its peak slope, SLEW_PEAK / attack), so the swell
     itself is never clipped and nothing else rises faster (Hue: 80 ms, §5.4).
  8. **Encode.** ``FixtureFrame{intensity, colour, rgb = colour x intensity, t_eval_ms}``, plus ``dmx`` =
     ``colour_mix.encode`` for a type with channels.

**Parity with the virtual Maestro.** With no vibe, the neutral colour and depth (artnet_out's ``DEFAULT_RGB`` /
``HUE_DEPTH``, or what outputs.py passes as ``neutral``) and show.yaml's ``pulse`` layer (or this module's default
layer when ``looks.layers`` is absent), a Hue lamp's ``rgb`` equals artnet_out's hue channels / 255 for the same clock,
features and depth: through a beat, energy without a beat, and the quiet and no-features idles (tests/test_looks.py).

Code fallbacks are show.yaml's own starting points (S5.0 rule 2), marked where they are defined. Config names live in
config: nothing here branches on a mode, vibe, fixture, output, group, zone, screen, layer or role name.
"""

from __future__ import annotations

import collections
import hashlib
import itertools
import json
import logging
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from . import colour_mix, contract
from .artnet_out import (DEFAULT_RGB, NEUTRAL_RGB, HUE_ATTACK_S, HUE_DECAY_S, HUE_DEPTH, IDLE_AFTER_S, QUIET_AFTER_S,
                         QUIET_GATE_OFF, QUIET_GATE_ON, TAU_ENERGY_S)
from .clock import beat_at
from .palette import mix as palette_mix

log = logging.getLogger("show_service.looks")

Rgb = tuple[float, float, float]
BLACK: Rgb = (0.0, 0.0, 0.0)

# -- constants (S5.10's show.yaml numbers are the fallbacks, each a starting point with its §) -------------------------
DEFAULT_MIN_VISIBLE_MS = 40.0   # starting point (show.yaml looks.min_visible_ms; §5.4 the visible pulse width)
DEFAULT_MIDDLE_DEPTH = 0.5      # starting point (§5.4 "Middle")
DEFAULT_MASTER = 1.0            # lamps and DMX only, never screens
DEFAULT_HIT_MIN_STRENGTH = 0.3  # starting point
DEFAULT_HIT_DEPTH = 0.5         # starting point
DEFAULT_HIT_DECAY_MS = 120.0    # starting point
#: The layer used when show.yaml has no ``looks.layers`` key at all: artnet_out's own Hue swell (§5.4, §9.4), so a rig
#: with no looks config looks like the virtual Maestro. An empty list means no layers.
DEFAULT_LAYERS: tuple[dict, ...] = (
    {"id": "swell", "attr": "intensity", "fx": "swell", "phase": "beat", "subdivide": True, "depth": "swell",
     "needs": "beat", "without_beat": "energy", "attack_ms": HUE_ATTACK_S * 1000.0,
     "decay_ms": HUE_DECAY_S * 1000.0, "select": "kind:lamp"},
)
WITHOUT_BEAT: tuple[str, ...] = ("energy", "none")
SUBDIVISIONS: tuple[float, ...] = (1.0, 0.5, 0.25, 0.125)     # LookData.accents.subdivision's values
FREE_RUN_BPM = 120.0            # starting point (clock.py's prior): ``needs: none`` before the clock ever had a tempo
FREE_RUN_BEATS_PER_BAR = 4      # clock.py's defaults
FREE_RUN_PHRASE_BARS = 8
SLEW_PEAK = contract.SLEW_PEAK  # a smoothstep's peak slope: the rise limit is SLEW_PEAK / min_attack per second
                                # (hue_out's own rise limit uses the same constant)
HIT_SWELL_MS = 300.0            # starting point (§5.4: an unforeseen drop on an output without hits swells, attack
                                # >= 300 ms); LookData.hit carries show.yaml director.build.hit_swell_ms
BIG_RISE = 0.5                  # README S5.5 step 7: what caps.flash_hz_max counts
HIT_RING = 64                   # recent music.hit bodies kept (one ring; each runner keeps its own cursor)
HIT_TAIL_DECAYS = 8.0           # a flash is over this many decay constants after its hold (e^-8 ~ 0.0003)
EVAL_RING = 256                 # evaluate() timings kept per output for stats()
ERROR_LOG_S = 30.0              # a failing input is logged once, then at most this often
LAMP = "lamp"                   # contract.FIXTURE_KINDS: the only kind the engine evaluates (never "screen")
_LAYER_KEYS = frozenset({"id", "enabled", "attr", "fx", "phase", "subdivide", "period_bars", "period_s", "depth",
                         "depth_scale", "spread", "needs", "without_beat", "attack_ms", "decay_ms", "select"})
_HIT_KEYS = frozenset({"select", "styles", "bands", "min_strength", "depth", "decay_ms"})
_LOOKS_KEYS = frozenset({"align_pct", "min_visible_ms", "middle_depth", "master", "caps", "layers", "hits"})


# -- small helpers ----------------------------------------------------------------------------------------------------
def _c01(x: float) -> float:
    return 0.0 if not x > 0.0 else 1.0 if x > 1.0 else x


def _isnum(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _smoothstep(x: float) -> float:
    x = 0.0 if x < 0.0 else 1.0 if x > 1.0 else x
    return x * x * (3.0 - 2.0 * x)


def _rgb3(v: Any) -> Optional[Rgb]:
    """Three numbers clamped to 0..1 (NaN 0), else None. On the hot path: no helper calls."""
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None
    try:
        r, g, b = float(v[0]), float(v[1]), float(v[2])
    except (TypeError, ValueError):
        return None
    return (0.0 if not r > 0.0 else 1.0 if r > 1.0 else r, 0.0 if not g > 0.0 else 1.0 if g > 1.0 else g,
            0.0 if not b > 0.0 else 1.0 if b > 1.0 else b)


def _hex_rgb(h: Any) -> Optional[Rgb]:
    s = str(h or "").strip().lstrip("#")
    if len(s) != 6:
        return None
    try:
        return (int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0, int(s[4:6], 16) / 255.0)
    except ValueError:
        return None


def shape(fx: str, cycles: float, period_s: float = 0.0, attack_s: float = HUE_ATTACK_S,
          decay_s: float = HUE_DECAY_S) -> float:
    """An oscillator's value 0..1 at ``cycles`` (1 at each whole cycle); ``swell`` needs the cycle's length in s."""
    p = cycles - math.floor(cycles)
    if fx == "sine":
        return 0.5 + 0.5 * math.cos(2.0 * math.pi * p)
    if fx == "saw":
        return 1.0 - p
    if fx == "square":
        return 1.0 if p < 0.5 else 0.0
    if fx == "triangle":
        return 1.0 - 2.0 * min(p, 1.0 - p)
    # swell: artnet_out's envelope (attack up to each cycle, then decay)
    to_next = (1.0 - p) * period_s if p > 0.0 else 0.0
    up = _smoothstep(1.0 - to_next / attack_s) if attack_s > 0.0 and to_next < attack_s else (
        1.0 if to_next <= 0.0 else 0.0)
    down = math.exp(-p * period_s / decay_s) if decay_s > 0.0 else (1.0 if p <= 0.0 else 0.0)
    return up if up > down else down


# -- selectors (fixtures.py's: one copy of the grammar, README S5.2) -------------------------------------------------
def _selector() -> tuple[Optional[Callable[..., list[str]]], Optional[str]]:
    """fixtures.select (B12's), imported lazily; (None, why) when it can't be imported (every layer selects nothing)."""
    try:
        from . import fixtures
    except Exception as e:                                      # degrade: the rest of the engine still runs
        return None, f"{type(e).__name__}: {e}"
    fn = getattr(fixtures, "select", None)
    return (fn, None) if callable(fn) else (None, "fixtures.select is missing")


def role_of(f: Mapping[str, Any], lamps: Optional[Mapping[str, Any]]) -> Optional[str]:
    """A fixture's role as the director decides it: its own, else (show.yaml ``lamps``) its placement's, else
    ``lamps.default_role``, for a lamp."""
    r = f.get("role")
    if isinstance(r, str) and r:
        return r
    if not isinstance(lamps, Mapping) or f.get("kind", LAMP) != LAMP:
        return None
    pr = lamps.get("placement_roles")
    r = pr.get(f.get("placement")) if isinstance(pr, Mapping) and f.get("placement") else None
    if isinstance(r, str):
        return r
    dr = lamps.get("default_role")
    return dr if isinstance(dr, str) else None


# -- the compiled program ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class _Layer:
    id: str
    fx: str
    phase: str
    subdivide: bool
    period: Optional[tuple[float, float]]       # bars (phase bar) or seconds (phase time): (slow, fast)
    depth: Any                                  # float | a LOOK_INPUTS word
    depth_scale: float
    spread: Any                                 # float | a LOOK_INPUTS word
    needs: str
    without_beat: str
    attack_s: float
    decay_s: float
    members: Mapping[str, tuple[int, int]]      # fixture id -> (i, n) in the layer's ordered selection


@dataclass(frozen=True)
class _Hits:
    members: frozenset
    styles: frozenset
    bands: Optional[frozenset]
    min_strength: float
    depth: float
    decay_s: float


@dataclass(frozen=True)
class _Slot:
    id: str
    info: Mapping[str, Any]
    type: Mapping[str, Any]
    rise_s: float
    colour_s: float
    min_pulse_s: float
    role: Optional[str]
    screen: Optional[str]
    zone: Optional[str]
    house_rgb: Optional[Rgb]
    donors: tuple[str, ...]
    channels: bool
    house: tuple                                # (intensity, colour list, rgb list, dmx bytes | None): the idle look


@dataclass(frozen=True)
class _OutProg:
    id: str
    hits: Optional[bool]                        # fixtures.yaml outputs.<id>.hits (it can only turn them off)
    min_attack_ms: Optional[float]              # ... min_attack_ms (it can only raise the driver's floor)
    slots: tuple[_Slot, ...]
    hit_horizon_s: float
    hit_members: bool                           # any of its fixtures is in looks.hits.select (takes_hit per output)


@dataclass(frozen=True)
class _Program:
    revision: str
    layers: tuple[_Layer, ...]
    hits: Optional[_Hits]
    master: float
    middle_depth: float
    flash_hz_max: Optional[float]
    outputs: Mapping[str, _OutProg]
    zones: Mapping[str, Optional[str]]          # fixture id -> zone (role donors' ordering)
    problems: tuple[str, ...]


_EMPTY = _Program(revision="", layers=(), hits=None, master=DEFAULT_MASTER, middle_depth=DEFAULT_MIDDLE_DEPTH,
                  flash_hz_max=None, outputs={}, zones={}, problems=())


# -- a runner's own state -------------------------------------------------------------------------------------------
class _Glide:
    """A number moving to its newest target over the transition (linear)."""
    __slots__ = ("v0", "v1", "t0", "dur")

    def __init__(self, v: float, t: float) -> None:
        self.v0 = self.v1 = v
        self.t0, self.dur = t, 0.0

    def at(self, t: float) -> float:
        if self.dur <= 0.0 or t >= self.t0 + self.dur:
            return self.v1
        return self.v0 + (self.v1 - self.v0) * max(0.0, t - self.t0) / self.dur

    def to(self, v: float, t: float, dur: float) -> float:
        if abs(v - self.v1) > 1e-9:
            self.v0, self.v1, self.t0, self.dur = self.at(t), v, t, max(0.0, dur)
        return self.at(t)


class _Fade:
    """A discrete parameter: the old value's wave crossfades into the new one's over the transition."""
    __slots__ = ("old", "new", "t0", "dur")

    def __init__(self, v: Any, t: float) -> None:
        self.old = self.new = v
        self.t0, self.dur = t, 0.0

    def x(self, t: float) -> float:
        return 1.0 if self.dur <= 0.0 else _c01((t - self.t0) / self.dur)

    def to(self, v: Any, t: float, dur: float) -> list[tuple[float, Any]]:
        if v != self.new:
            if self.x(t) >= 0.5:
                self.old = self.new
            self.new, self.t0, self.dur = v, t, max(0.0, dur)
        x = self.x(t)
        if x >= 1.0 or self.old == self.new:
            return [(1.0, self.new)]
        return [(1.0 - x, self.old), (x, self.new)]


class _FixState:
    __slots__ = ("prev", "t_prev", "low", "t_big")

    def __init__(self) -> None:
        self.prev: Optional[float] = None
        self.t_prev = 0.0
        self.low: Optional[float] = None
        self.t_big: Optional[float] = None


class _OutState:
    def __init__(self) -> None:
        self.coarsened: set[str] = set()         # layers this output ran coarser (caps.min_period_ms)
        self.seq = 0
        self.t_prev: Optional[float] = None      # the last active frame's send time (artnet_out's _t_prev)
        self.energy: Optional[float] = None      # TAU_ENERGY_S-smoothed features.energy
        self.glides: dict[tuple[str, str], _Glide] = {}
        self.fades: dict[str, _Fade] = {}
        self.fix: dict[str, _FixState] = {}
        self.hit_seq = 0
        self.hits = 0
        self.held: Optional[str] = None
        self.why: Optional[str] = None
        self.eval_us = [0.0] * EVAL_RING
        self.n_eval = 0
        self.role_look: Any = None
        self.role_map: dict[str, list[str]] = {}

    def glide(self, key: tuple[str, str], v: float, t: float, dur: float) -> float:
        g = self.glides.get(key)
        if g is None:
            g = self.glides[key] = _Glide(v, t)
        return g.to(v, t, dur)

    def fade(self, key: str, v: Any, t: float, dur: float) -> list[tuple[float, Any]]:
        f = self.fades.get(key)
        if f is None:
            f = self.fades[key] = _Fade(v, t)
        return f.to(v, t, dur)


@dataclass
class _LayerNow:
    """One layer for one frame: computed once, then evaluated per member."""
    layer: _Layer
    mode: str                                   # "osc" | "energy"
    d: float
    spread: float
    variants: list                              # [(weight, src clock body | None, offset beats, div, period_s)]
    attack_s: float


# -- the engine -------------------------------------------------------------------------------------------------------
class LookEngine:
    """README S5.5. ``configure`` on show-brain; ``evaluate`` only from that output's runner; ``set_look``, ``on_hit``,
    ``active`` and ``stats`` from any thread."""

    def __init__(self, *, palette_fn: Optional[Callable[[float], Any]] = None,
                 look_fn: Optional[Callable[[], Any]] = None, clock: Any = None,
                 features_fn: Optional[Callable[[], Any]] = None,
                 neutral: tuple[Sequence[float], float] = (NEUTRAL_RGB, HUE_DEPTH),
                 mono: Callable[[], float] = time.monotonic) -> None:
        self.palette_fn = palette_fn
        self.look_fn = look_fn
        self.features_fn = features_fn
        if clock is None:
            self._snap_fn = None
        elif callable(getattr(clock, "snapshot", None)):
            self._snap_fn = clock.snapshot                       # clock.Clock: the last music.clock body
        elif callable(clock):
            self._snap_fn = clock                                # () -> ClockData | None
        else:
            self._snap_fn = None
        n_rgb, n_depth = (neutral if neutral is not None else (NEUTRAL_RGB, HUE_DEPTH))
        self.neutral_rgb: Rgb = _rgb3(n_rgb) or NEUTRAL_RGB
        self.neutral_depth = _c01(float(n_depth)) if _isnum(n_depth) else HUE_DEPTH
        self._mono = mono
        # set_look (any thread; each swapped by reference, read once per frame)
        self.hue_depth = self.neutral_depth
        self.beat_lock = True
        self.beat_depth: Optional[float] = None      # the director's accent depth, not zeroed by the clock's gate
        # the program (show-brain swaps it)
        self._program: _Program = _EMPTY
        self._select, self._select_err = _selector()
        # cross-thread state
        self._hits: collections.deque = collections.deque(maxlen=HIT_RING)
        self._hit_n = itertools.count(1)
        self._quiet: tuple[bool, Optional[float]] = (False, None)   # (quiet, below-gate since)
        self._last_grid: Optional[Mapping[str, Any]] = None          # the last clock body with a tempo
        self._outs: dict[str, _OutState] = {}
        self.errors: dict[str, int] = {}
        self._log_next: dict[str, float] = {}

    # -- show-brain ---------------------------------------------------------------------------------------------------
    def configure(self, looks: Optional[Mapping[str, Any]], rig: Optional[Mapping[str, Any]], *,
                  lamps: Optional[Mapping[str, Any]] = None) -> list[str]:
        """Compile show.yaml ``looks`` against the registry into a Program (swapped by reference). Returns the problem
        lines (page words); a layer, the hits block or a fixture that can't be used as written is left out, the rest
        runs. ``lamps`` (show.yaml's, optional) lets ``role:`` selectors and the allocation fallback follow placements
        as the director does (fixtures.select's ``lamps``); without it, only written roles count."""
        probs: list[str] = []
        if looks is None:
            looks = {}
        elif not isinstance(looks, Mapping):
            probs.append("looks: not a mapping: the starting points are used")
            looks = {}
        for k in looks:
            if k not in _LOOKS_KEYS:
                probs.append(f"looks.{k}: not a key the look engine reads: ignored")
        if not isinstance(rig, Mapping):
            rig = {"revision": "", "fixtures": {}, "types": {}, "outputs": {}, "groups": {}}
            probs.append("looks: no fixture registry yet: no fixture is evaluated")
        self._select, self._select_err = _selector()
        lamps = lamps if isinstance(lamps, Mapping) else None
        min_visible = self._num(looks, "min_visible_ms", DEFAULT_MIN_VISIBLE_MS, 0.0, None, "looks", probs)
        middle = self._num(looks, "middle_depth", DEFAULT_MIDDLE_DEPTH, 0.0, 1.0, "looks", probs)
        master = self._num(looks, "master", DEFAULT_MASTER, 0.0, 1.0, "looks", probs)
        caps = looks.get("caps")
        flash_hz = None
        if caps is not None and not isinstance(caps, Mapping):
            probs.append("looks.caps: not a mapping: no caps")
        elif isinstance(caps, Mapping):
            v = caps.get("flash_hz_max")
            if v is not None:
                if _isnum(v) and v > 0:
                    flash_hz = float(v)
                else:
                    probs.append(f"looks.caps.flash_hz_max: {v!r} isn't a positive number: off")
            for k in caps:
                if k != "flash_hz_max":
                    probs.append(f"looks.caps.{k}: not a cap the look engine has: ignored")

        fixtures = rig.get("fixtures") or {}
        types = rig.get("types") or {}

        def lamp(fid: str) -> bool:
            f = fixtures.get(fid)
            t = types.get(f.get("type")) if isinstance(f, Mapping) else None
            return isinstance(t, Mapping) and t.get("kind") == LAMP and f.get("kind", LAMP) == LAMP

        def select(expr: Any, where: str) -> Optional[list[str]]:
            if self._select is None:
                probs.append(f"{where}: fixtures.py can't be imported ({self._select_err}): it selects nothing")
                return None
            try:
                ids = self._select(expr, rig, lamps=dict(lamps) if lamps else None) if isinstance(expr, str) \
                    else None
            except ValueError as e:
                probs.append(f"{where}: selector {expr!r} doesn't read ({e}): it selects nothing")
                return None
            except Exception as e:                               # a broken selector module: degrade
                probs.append(f"{where}: selector {expr!r} failed ({type(e).__name__}): it selects nothing")
                return None
            if ids is None:
                probs.append(f"{where}: select must be a selector string: it selects nothing")
                return None
            return [i for i in ids if lamp(i)]                   # the screens rule: never a screen

        # layers
        raw_layers = looks.get("layers", DEFAULT_LAYERS)
        layers: list[_Layer] = []
        if not isinstance(raw_layers, (list, tuple)):
            probs.append("looks.layers: not a list: no layers")
            raw_layers = ()
        seen: set[str] = set()
        for n, raw in enumerate(raw_layers):
            L = self._compile_layer(raw, n, seen, select, probs)
            if L is not None:
                layers.append(L)

        # hits
        hits = None
        raw_hits = looks.get("hits")
        if raw_hits is not None:
            hits = self._compile_hits(raw_hits, select, probs)

        # outputs and their fixtures
        outputs: dict[str, _OutProg] = {}
        zones: dict[str, Optional[str]] = {}
        infos = {fid: f for fid, f in fixtures.items() if isinstance(f, Mapping)}
        for fid, f in infos.items():
            zones[fid] = f.get("zone")
        for oid, oc in (rig.get("outputs") or {}).items():
            if not isinstance(oc, Mapping):
                continue
            slots = []
            for fid in sorted(infos):
                f = infos[fid]
                if f.get("output") != oid:
                    continue
                t = types.get(f.get("type"))
                if not isinstance(t, Mapping):
                    probs.append(f"fixtures.{fid}: type {f.get('type')!r} isn't in the registry: left out")
                    continue
                kind = t.get("kind")
                if kind != LAMP or f.get("kind", LAMP) != LAMP:
                    if kind not in contract.FIXTURE_KINDS:
                        probs.append(f"fixtures.{fid}: kind {kind!r} isn't one of "
                                     f"{', '.join(contract.FIXTURE_KINDS)}: left out")
                    continue                                     # a screen: never evaluated (the screens rule)
                slots.append(self._slot(fid, f, t, infos, types, min_visible, lamps, probs))
            ma = oc.get("min_attack_ms")
            hz = hits.decay_s * HIT_TAIL_DECAYS if hits else 0.0
            outputs[oid] = _OutProg(
                id=oid, hits=oc.get("hits") if isinstance(oc.get("hits"), bool) else None,
                min_attack_ms=float(ma) if _isnum(ma) and ma > 0 else None, slots=tuple(slots),
                hit_horizon_s=hz + max((s.min_pulse_s for s in slots), default=0.0),
                hit_members=bool(hits is not None and any(s.id in hits.members for s in slots)))
        blob = json.dumps({"looks": looks, "rig": rig.get("revision")}, sort_keys=True, default=str)
        prog = _Program(revision=hashlib.sha1(blob.encode()).hexdigest()[:12], layers=tuple(layers), hits=hits,
                        master=master, middle_depth=middle, flash_hz_max=flash_hz, outputs=outputs, zones=zones,
                        problems=tuple(probs))
        self._program = prog
        return list(probs)

    @staticmethod
    def _num(d: Mapping, key: str, default: Optional[float], lo: Optional[float], hi: Optional[float], where: str,
             probs: list[str], positive: bool = False) -> Optional[float]:
        if key not in d or d.get(key) is None:
            return default
        v = d.get(key)
        if not _isnum(v) or (lo is not None and v < lo) or (hi is not None and v > hi) or (positive and v <= 0):
            rng = f"{lo if lo is not None else '-inf'}..{hi if hi is not None else 'inf'}"
            probs.append(f"{where}.{key}: {v!r} isn't a number in {rng}{' above 0' if positive else ''}: "
                         f"{default!r} is used")
            return default
        return float(v)

    def _input_or_num(self, v: Any, where: str, probs: list[str]) -> tuple[bool, Any]:
        if v in contract.LOOK_INPUTS:
            return True, v
        if _isnum(v) and 0.0 <= v <= 1.0:
            return True, float(v)
        probs.append(f"{where}: {v!r} is neither a number in 0..1 nor one of {', '.join(contract.LOOK_INPUTS)}")
        return False, None

    def _compile_layer(self, raw: Any, n: int, seen: set[str], select: Callable, probs: list[str]
                       ) -> Optional[_Layer]:
        if not isinstance(raw, Mapping):
            probs.append(f"looks.layers[{n}]: not a mapping: left out")
            return None
        lid = raw.get("id")
        if not isinstance(lid, str) or not lid:
            probs.append(f"looks.layers[{n}]: no id: left out")
            return None
        where = f"looks.layers.{lid}"
        if lid in seen:
            probs.append(f"{where}: the id is used twice: left out")
            return None
        seen.add(lid)
        for k in raw:
            if k not in _LAYER_KEYS:
                probs.append(f"{where}.{k}: not a layer key: ignored")
        en = raw.get("enabled", True)
        if not isinstance(en, bool):
            probs.append(f"{where}.enabled: {en!r} isn't true or false: off")
            return None
        if not en:
            return None
        attr = raw.get("attr", "intensity")
        if attr != "intensity":
            why = ("colour layers are kept for later (Stage 5 builds intensity)" if attr == "colour"
                   else f"attr {attr!r} isn't built (intensity only)")
            probs.append(f"{where}: {why}: left out")
            return None
        bad = []
        fx, phase = raw.get("fx"), raw.get("phase")
        if fx not in contract.FX_SHAPES:
            bad.append(f"fx {fx!r} isn't one of {', '.join(contract.FX_SHAPES)}")
        if phase not in contract.FX_PHASES:
            bad.append(f"phase {phase!r} isn't one of {', '.join(contract.FX_PHASES)}")
        needs = raw.get("needs", "beat" if phase == "beat" else "none")
        if needs not in contract.FX_NEEDS:
            bad.append(f"needs {needs!r} isn't one of {', '.join(contract.FX_NEEDS)}")
        wob = raw.get("without_beat", "none")
        if wob not in WITHOUT_BEAT:
            bad.append(f"without_beat {wob!r} isn't one of {', '.join(WITHOUT_BEAT)}")
        sub = raw.get("subdivide", False)
        if not isinstance(sub, bool):
            bad.append(f"subdivide {sub!r} isn't true or false")
        period = None
        pkey = "period_bars" if phase == "bar" else "period_s" if phase == "time" else None
        for k in ("period_bars", "period_s"):
            if k in raw and k != pkey:
                probs.append(f"{where}.{k}: only read with phase {'bar' if k == 'period_bars' else 'time'}: ignored")
        if pkey is not None:
            pv = raw.get(pkey, 1.0 if pkey == "period_bars" else None)
            if _isnum(pv) and pv > 0:
                period = (float(pv), float(pv))
            elif isinstance(pv, (list, tuple)) and len(pv) == 2 and all(_isnum(x) and x > 0 for x in pv):
                period = (float(pv[0]), float(pv[1]))
            else:
                bad.append(f"{pkey} {pv!r} isn't a number above 0 or a [slow, fast] pair")
        if "depth" not in raw:
            bad.append("no depth")
            depth = None
        else:
            ok, depth = self._input_or_num(raw.get("depth"), f"{where}.depth", probs)
            if not ok:
                bad.append("depth")
        ok, spread = self._input_or_num(raw.get("spread", 0.0), f"{where}.spread", probs)
        if not ok:
            bad.append("spread")
        ds = raw.get("depth_scale", 1.0)
        if not (_isnum(ds) and ds >= 0):
            bad.append(f"depth_scale {ds!r} isn't a number >= 0")
        att = raw.get("attack_ms", HUE_ATTACK_S * 1000.0)
        if not (_isnum(att) and att >= 0):
            bad.append(f"attack_ms {att!r} isn't a number >= 0")
        dec = raw.get("decay_ms", HUE_DECAY_S * 1000.0)
        if not (_isnum(dec) and dec > 0):
            bad.append(f"decay_ms {dec!r} isn't a number above 0")
        if bad:
            probs.append(f"{where}: {'; '.join(b for b in bad if b not in ('depth', 'spread')) or 'bad value'}: "
                         f"left out")
            return None
        if "select" not in raw:
            probs.append(f"{where}: no select: it selects nothing")
        ids = select(raw.get("select"), f"{where}.select") or []
        members = {fid: (i, len(ids)) for i, fid in enumerate(ids)}
        return _Layer(id=lid, fx=fx, phase=phase, subdivide=bool(sub), period=period, depth=depth,
                      depth_scale=float(ds), spread=spread, needs=needs, without_beat=wob, attack_s=float(att) / 1000.0,
                      decay_s=float(dec) / 1000.0, members=members)

    def _compile_hits(self, raw: Any, select: Callable, probs: list[str]) -> Optional[_Hits]:
        if not isinstance(raw, Mapping):
            probs.append("looks.hits: not a mapping: no hits")
            return None
        for k in raw:
            if k not in _HIT_KEYS:
                probs.append(f"looks.hits.{k}: not a hits key: ignored")
        styles_raw = raw.get("styles")
        if not isinstance(styles_raw, (list, tuple)):
            probs.append("looks.hits.styles: not a list of accent styles: no hits")
            return None
        styles = set()
        for s in styles_raw:
            if s in contract.ACCENT_STYLES:
                styles.add(s)
            else:
                probs.append(f"looks.hits.styles: {s!r} isn't one of {', '.join(contract.ACCENT_STYLES)}: dropped")
        bands = None
        if raw.get("bands") is not None:
            if isinstance(raw["bands"], (list, tuple)):
                bands = set()
                for b in raw["bands"]:
                    if b in contract.BAND_NAMES:
                        bands.add(b)
                    else:
                        probs.append(f"looks.hits.bands: {b!r} isn't one of {', '.join(contract.BAND_NAMES)}: dropped")
            else:
                probs.append("looks.hits.bands: not a list: every band")
        ms = self._num(raw, "min_strength", DEFAULT_HIT_MIN_STRENGTH, 0.0, 1.0, "looks.hits", probs)
        depth = self._num(raw, "depth", DEFAULT_HIT_DEPTH, 0.0, 1.0, "looks.hits", probs)
        dec = self._num(raw, "decay_ms", DEFAULT_HIT_DECAY_MS, 0.0, None, "looks.hits", probs, positive=True)
        if "select" not in raw:
            probs.append("looks.hits: no select: it selects nothing")
        ids = select(raw.get("select"), "looks.hits.select") or []
        return _Hits(members=frozenset(ids), styles=frozenset(styles),
                     bands=frozenset(bands) if bands is not None else None, min_strength=ms, depth=depth,
                     decay_s=dec / 1000.0)

    def _slot(self, fid: str, f: Mapping[str, Any], t: Mapping[str, Any], infos: Mapping[str, Mapping],
              types: Mapping[str, Mapping], min_visible_ms: float, lamps: Optional[Mapping[str, Any]],
              probs: list[str]) -> _Slot:
        lat = f.get("latency") if isinstance(f.get("latency"), Mapping) else {}
        rise = float(lat["rise_ms"]) if _isnum(lat.get("rise_ms")) else 0.0
        col = float(lat["colour_ms"]) if _isnum(lat.get("colour_ms")) else rise
        mp = float(lat["min_pulse_ms"]) if _isnum(lat.get("min_pulse_ms")) else min_visible_ms
        house = f.get("house")
        house_rgb = None
        if isinstance(house, Mapping):
            c = _hex_rgb(house.get("hex", "#ffffff"))
            hi = house.get("intensity", 1.0)
            if c is None or not _isnum(hi):
                probs.append(f"fixtures.{fid}.house: {dict(house)!r} doesn't read: dark")
            else:
                k = _c01(float(hi))
                house_rgb = (c[0] * k, c[1] * k, c[2] * k)
        elif house is not None:
            probs.append(f"fixtures.{fid}.house: not a mapping: dark")
        # registry donors: the same written role or placement first, then the same role as the director decides it
        # (show.yaml lamps); within each, the same screen first, then the same zone, then by id
        role, place = role_of(f, lamps), f.get("placement")
        donors: list[tuple] = []
        for oid, o in infos.items():
            if oid == fid:
                continue
            ot = types.get(o.get("type"))
            if not isinstance(ot, Mapping) or ot.get("kind") != LAMP:
                continue
            if (f.get("role") and o.get("role") == f.get("role")) or (place and o.get("placement") == place):
                rank = 0
            elif role and role_of(o, lamps) == role:
                rank = 1
            else:
                continue
            donors.append((rank, o.get("screen") != f.get("screen"), o.get("zone") != f.get("zone"), oid))
        donors.sort()
        chans = bool(t.get("channels")) and _isnum(t.get("footprint")) and t.get("footprint") > 0
        hrgb = house_rgb or BLACK
        hi = max(hrgb)
        hcol = [c / hi for c in hrgb] if hi > 0 else [0.0, 0.0, 0.0]
        hdmx = colour_mix.encode(f, t, hi, hcol) if chans else None
        return _Slot(id=fid, info=f, type=t, rise_s=rise / 1000.0, colour_s=col / 1000.0, min_pulse_s=mp / 1000.0,
                     role=role if isinstance(role, str) else None, screen=f.get("screen"), zone=f.get("zone"),
                     house_rgb=house_rgb, donors=tuple(d[-1] for d in donors), channels=chans,
                     house=(hi, hcol, list(hrgb), hdmx))

    # -- any thread ----------------------------------------------------------------------------------------------------
    def set_look(self, *, hue_depth: Optional[float] = None, beat_lock: Optional[bool] = None,
                 reset: bool = False, beat_depth: Optional[float] = None) -> None:
        """VirtualMaestro.set_look's, plus ``beat_depth``: ``reset`` restores the neutral depth, beat_lock True and no
        beat_depth first; a depth that isn't a finite number is ignored.

        ``hue_depth`` is the director's swell depth as the virtual Maestro takes it (0 unless the clock's gate is
        ``beat``); ``beat_depth`` is the same accent depth before the gate (README S5.14), so a ``swell``-depth layer
        that runs beat-locked at the clock's middle confidence (gate ``dmx``, outputs that take hits: §5.4) has a
        depth. ``beat_lock`` is the vibe's own: the engine reads the clock's gate itself."""
        if reset:
            self.hue_depth, self.beat_lock, self.beat_depth = self.neutral_depth, True, None
        for name, v in (("hue_depth", hue_depth), ("beat_depth", beat_depth)):
            if v is None:
                continue
            try:
                d = float(v)
            except (TypeError, ValueError):
                d = math.nan
            if math.isfinite(d):
                setattr(self, name, _c01(d))
        if beat_lock is not None:
            self.beat_lock = bool(beat_lock)

    def on_hit(self, d: Mapping[str, Any]) -> None:
        """A music.hit body (show-rx): kept in the ring for every hits-capable runner."""
        try:
            t = float(d["t_ms"]) / 1000.0
            s = float(d.get("strength", 1.0))
        except (KeyError, TypeError, ValueError):
            return
        if math.isfinite(t) and math.isfinite(s):
            self._hits.append((next(self._hit_n), t, d.get("band"), s))

    def takes_hit(self, d: Mapping[str, Any], output_id: Optional[str] = None) -> bool:
        """Any thread (show-rx, a few µs): would this music.hit flash now? The program's hits block (strength, band),
        the look's accents style, House lights, Lights low and the pre-drop dip, and the clock's gate, as
        ``evaluate`` reads them; with ``output_id``, also whether any of that output's fixtures is in the hits
        selection (README S5.14). outputs.Rig wakes a hit output's runner (a frame pulled forward) only for these, so
        the hits a look or an output doesn't show never knock DMX off its beat grid (c8's integration ruling, 16
        Oct)."""
        prog = self._program
        hp = prog.hits
        if hp is None:
            return False
        if output_id is not None:
            op = prog.outputs.get(output_id)
            if op is None or not op.hit_members:
                return False
        try:
            s = float(d.get("strength", 1.0))
        except (AttributeError, TypeError, ValueError):
            return False
        if not s >= hp.min_strength or (hp.bands is not None and d.get("band") not in hp.bands):
            return False
        look = self._call("look", self.look_fn)
        if not isinstance(look, Mapping) or look.get("house") or look.get("low") or look.get("dip") or \
                (look.get("accents") or {}).get("style") not in hp.styles:
            return False
        snap = self._call("clock", self._snap_fn)
        return isinstance(snap, Mapping) and _isnum(snap.get("bpm")) and snap["bpm"] > 0 and \
            (snap.get("gate") or "none") != "none"

    def active(self, t_s: float) -> tuple[bool, Optional[str]]:
        """The activity rule (module docstring, 3) at ``t_s``: (active, why)."""
        a, why, _ = self._activity(float(t_s), self._call("palette", self.palette_fn, float(t_s)),
                                   self._call("features", self.features_fn))
        return a, why

    def stats(self) -> dict:
        p = self._program
        per = {}
        for oid, st in list(self._outs.items()):
            n = min(st.n_eval, EVAL_RING)
            xs = sorted(st.eval_us[:n]) if n else []
            per[oid] = {"eval_us": {"p50": round(xs[n // 2], 1) if xs else None,
                                    "p95": round(xs[min(n - 1, int(0.95 * n))], 1) if xs else None},
                        "frames": st.seq, "hits": st.hits, "held": st.held, "why": st.why,
                        "coarsened": sorted(st.coarsened)}
        return {"program": {"revision": p.revision, "layers": [L.id for L in p.layers], "problems": list(p.problems)},
                "per_output": per, "errors": dict(self.errors)}

    # -- inputs --------------------------------------------------------------------------------------------------------
    def _call(self, name: str, fn: Optional[Callable], *a: Any) -> Any:
        if fn is None:
            return None
        try:
            return fn(*a)
        except Exception:
            self.errors[name] = self.errors.get(name, 0) + 1
            now = time.monotonic()
            if now >= self._log_next.get(name, 0.0):
                self._log_next[name] = now + ERROR_LOG_S
                log.exception("look engine: %s failed (%d so far; logged at most every %.0f s)", name,
                              self.errors[name], ERROR_LOG_S)
            return None

    def _quiet_room(self, f: Any, t: float) -> bool:
        """artnet_out's: gate under QUIET_GATE_OFF for QUIET_AFTER_S -> quiet until it is back over QUIET_GATE_ON."""
        gate = f.get("gate") if isinstance(f, Mapping) else None
        quiet, since = self._quiet
        if not _isnum(gate):
            if quiet or since is not None:
                self._quiet = (False, None)
            return False
        if quiet:
            if gate >= QUIET_GATE_ON:
                self._quiet = (False, None)
                log.info("look engine: sound again (gate %.2f): active", gate)
                return False
            return True
        if gate < QUIET_GATE_OFF:
            if since is None:
                self._quiet = (False, t)
            elif t - since >= QUIET_AFTER_S:
                self._quiet = (True, since)
                log.info("look engine: a quiet room for %.0f s (gate %.2f)", QUIET_AFTER_S, gate)
                return True
        elif since is not None:
            self._quiet = (False, None)
        return False

    def _activity(self, t: float, pal: Any, feats: Any) -> tuple[bool, Optional[str], Optional[str]]:
        """(active, why inactive, held why): artnet_out's P0 idle rule."""
        vibe = isinstance(pal, Mapping)
        why = None
        if self.features_fn is not None:
            ft = feats.get("t_ms") if isinstance(feats, Mapping) else None
            if not _isnum(ft) or t - ft / 1000.0 > IDLE_AFTER_S:
                why = "no_features"
        if why is None and self._quiet_room(feats, t):
            why = "quiet"
        if why is not None and not vibe:
            return False, why, None
        return True, None, why

    def _snapshot(self) -> Optional[Mapping[str, Any]]:
        snap = self._call("clock", self._snap_fn)
        if isinstance(snap, Mapping) and _isnum(snap.get("bpm")) and snap["bpm"] > 0:
            if snap is not self._last_grid:
                self._last_grid = snap
            return snap
        return None

    def _free_run(self) -> Mapping[str, Any]:
        g = self._last_grid
        if g is not None:
            return g
        return {"bpm": FREE_RUN_BPM, "anchor_ms": 0.0, "beat_at_anchor": 0, "beats_per_bar": FREE_RUN_BEATS_PER_BAR,
                "bar_anchor_beat": 0, "phrase_bars": FREE_RUN_PHRASE_BARS, "gate": "none"}

    @staticmethod
    def _look_value(look: Any, word: str) -> float:
        parts = word.split(".")
        if parts[0] == "look":
            parts = parts[1:]
        v: Any = look
        for p in parts:
            v = v.get(p) if isinstance(v, Mapping) else None
        return _c01(float(v)) if _isnum(v) else 0.0

    def _input(self, spec: Any, look: Any, vibe: bool, energy: float, beat: bool = False) -> float:
        if not isinstance(spec, str):
            return float(spec)
        if spec == "swell":
            if not vibe:
                return self.neutral_depth
            bd = self.beat_depth
            return bd if beat and bd is not None else self.hue_depth     # beat-locked: the depth before the gate
        if spec == "energy":
            return energy
        return self._look_value(look, spec)

    # -- the runner ----------------------------------------------------------------------------------------------------
    def evaluate(self, output_id: str, t_send_s: float, caps: Mapping[str, Any]) -> dict:
        """The Frame for ``output_id`` sent at ``t_send_s`` (README S5.5; module docstring). Only from that output's
        runner thread."""
        c0 = time.perf_counter()
        t = float(t_send_s)
        prog = self._program
        st = self._outs.get(output_id)
        if st is None:
            st = self._outs[output_id] = _OutState()
        st.seq += 1
        frame = {"output": output_id, "seq": st.seq, "t_send_ms": t * 1000.0, "active": False, "why": None,
                 "fixtures": {}}
        op = prog.outputs.get(output_id)
        if op is None:
            frame["why"] = st.why = "no_look"
            self._record(st, c0)
            return frame
        caps = caps if isinstance(caps, Mapping) else {}
        caps_hits = bool(caps.get("hits")) and op.hits is not False
        ma = caps.get("min_attack_ms")
        min_attack_s = max(float(ma) if _isnum(ma) and ma > 0 else 0.0, op.min_attack_ms or 0.0) / 1000.0
        mp = caps.get("min_period_ms")
        min_period_s = float(mp) / 1000.0 if _isnum(mp) and mp > 0 else 0.0

        look = self._call("look", self.look_fn)
        look = look if isinstance(look, Mapping) else None
        drop = self._drop_hit(look, caps_hits)
        pals: dict[float, Any] = {}

        def pal_at(tt: float) -> Any:
            if tt in pals:
                return pals[tt]
            v = self._call("palette", self.palette_fn, tt)
            if v is not None and not isinstance(v, dict) and isinstance(v, Mapping):
                v = dict(v)                                      # the hot path below checks dict only
            if drop is not None and isinstance(v, dict):
                v = self._with_hit(v, drop, tt)
            pals[tt] = v
            return v

        p0 = pal_at(t)
        feats = self._call("features", self.features_fn)
        active, why, held = self._activity(t, p0, feats)
        if active and not isinstance(p0, Mapping) and caps.get("idle_without_vibe"):
            active, why, held = False, "no_vibe", None           # nothing room-side gates this output (§8.6)
        st.held, st.why = held, why
        fixtures: dict[str, dict] = frame["fixtures"]
        if not active:
            te = t * 1000.0
            for s in op.slots:
                hi, hcol, hrgb, hdmx = s.house
                ff = {"intensity": hi, "colour": list(hcol), "rgb": list(hrgb), "t_eval_ms": te}
                if hdmx is not None:
                    ff["dmx"] = hdmx
                fixtures[s.id] = ff
            frame["why"] = why
            self._record(st, c0)
            return frame
        frame["active"] = True
        vibe = isinstance(p0, Mapping)

        # the features' smoothed energy (artnet_out's: dt between active frames, seeded once, not while held)
        dt = 0.0 if st.t_prev is None else min(1.0, max(0.0, t - st.t_prev))
        st.t_prev = t
        if held is None:
            e = feats.get("energy", 0.0) if isinstance(feats, Mapping) else 0.0
            e = _c01(float(e)) if _isnum(e) else 0.0
            if st.energy is None:
                st.energy = e
            else:
                st.energy += (e - st.energy) * (1.0 - math.exp(-dt / TAU_ENERGY_S))
                if abs(st.energy) < 1e-12:
                    st.energy = 0.0
        energy = st.energy or 0.0

        snap = self._snapshot()
        gate = (snap.get("gate") or "none") if snap is not None else "none"
        trans_s = self._transition_s(look, snap)
        # House lights and Lights low are steady, like a held frame: no layer and no hit moves the lamps (README
        # S5.14; the director's own swell depth and drop hit are already off under them)
        steady = held is not None or (look is not None and bool(look.get("house") or look.get("low")))

        # layers, once per frame
        nows: list[_LayerNow] = []
        if not steady:
            for L in prog.layers:
                ln = self._layer_now(L, st, t, snap, gate, caps_hits, min_attack_s, look, vibe, energy, trans_s,
                                     prog.middle_depth, min_period_s)
                if ln is not None:
                    nows.append(ln)

        # hits, once per frame (never over the pre-drop dip either: LookData.dip)
        flashes: list[tuple[float, float]] = []
        hp = prog.hits
        if hp is not None and caps_hits and not steady and gate != "none" and look is not None and \
                (look.get("accents") or {}).get("style") in hp.styles and not look.get("dip"):
            for seq, th, band, strength in tuple(self._hits):
                if strength < hp.min_strength or (hp.bands is not None and band not in hp.bands):
                    continue
                age = t - th
                if age > op.hit_horizon_s:
                    continue
                if seq > st.hit_seq:
                    st.hit_seq = seq
                    st.hits += 1
                flashes.append((max(0.0, age), hp.depth * min(1.0, strength)))

        # the role map from the director's look (a fixture it doesn't name takes a same-role lamp's colour)
        if look is not st.role_look:
            st.role_look = look
            rm: dict[str, list[str]] = {}
            lamps = look.get("lamps") if look is not None else None
            if isinstance(lamps, Mapping):
                for did, lo in lamps.items():
                    r = lo.get("role") if isinstance(lo, Mapping) else None
                    if isinstance(r, str):
                        rm.setdefault(r, []).append(did)
            st.role_map = rm

        master = prog.master
        flash_hz = prog.flash_hz_max
        memo: dict[tuple, float] = {}                            # one oscillator value per (layer, time, lag)
        fl_memo: dict[float, float] = {}
        for s in op.slots:
            ti = t + s.rise_s
            tc = t + s.colour_s
            pi = pal_at(ti)
            rgb_i = self._base(s, pi, st, prog, look)
            rgb_c = rgb_i if tc == ti else self._base(s, pal_at(tc), st, prog, look)
            level = max(rgb_i)
            cm = max(rgb_c)
            colour = [rgb_c[0] / cm, rgb_c[1] / cm, rgb_c[2] / cm] if cm > 0.0 else [0.0, 0.0, 0.0]
            inten = level
            # oscillators scale the base, in file order
            for k, ln in enumerate(nows):
                m = ln.layer.members.get(s.id)
                if m is None:
                    continue
                if ln.mode == "energy":
                    val = energy
                else:
                    lag = ln.spread * m[0] / m[1] if m[1] else 0.0
                    key = (k, ti, lag)
                    val = memo.get(key)
                    if val is None:
                        val = 0.0
                        for w, src, off, div, per in ln.variants:
                            cyc = ((beat_at(src, ti) - off) / div if src is not None else ti / div) - lag
                            val += w * shape(ln.layer.fx, cyc, per, ln.attack_s, ln.layer.decay_s)
                        memo[key] = val
                inten *= 1.0 - ln.d + ln.d * val
            # hits: highest takes precedence (one flash value per frame for each min_pulse)
            if flashes and s.id in hp.members:
                fl = fl_memo.get(s.min_pulse_s)
                if fl is None:
                    fl = 0.0
                    for age, F in flashes:
                        v = F if age < s.min_pulse_s else F * math.exp(-(age - s.min_pulse_s) / hp.decay_s)
                        if v > fl:
                            fl = v
                    fl_memo[s.min_pulse_s] = fl
                hv = min(1.0, level + fl)
                if hv > inten:
                    inten = hv
            # master and caps (lamps only; screens never reach here)
            inten = _c01(inten * master)
            fs = st.fix.get(s.id)
            if fs is None:
                fs = st.fix[s.id] = _FixState()
            if flash_hz is not None:
                if fs.low is None:
                    fs.low = inten
                if inten - fs.low > BIG_RISE:
                    if fs.t_big is None or t - fs.t_big >= 1.0 / flash_hz:
                        fs.t_big, fs.low = t, inten
                    else:
                        inten = fs.low + BIG_RISE
                if inten < fs.low:
                    fs.low = inten
            if min_attack_s > 0.0 and fs.prev is not None and t >= fs.t_prev:
                lim = fs.prev + SLEW_PEAK * (t - fs.t_prev) / min_attack_s
                if inten > lim:
                    inten = lim
            fs.prev, fs.t_prev = inten, t
            ff = {"intensity": inten, "colour": colour,
                  "rgb": [colour[0] * inten, colour[1] * inten, colour[2] * inten], "t_eval_ms": ti * 1000.0}
            if s.channels:
                ff["dmx"] = colour_mix.encode(s.info, s.type, inten, colour)
            fixtures[s.id] = ff
        self._record(st, c0)
        return frame

    def _record(self, st: _OutState, c0: float) -> None:
        st.eval_us[st.n_eval % EVAL_RING] = (time.perf_counter() - c0) * 1e6
        st.n_eval += 1

    def _base(self, s: _Slot, pal: Any, st: _OutState, prog: _Program, look: Any) -> Rgb:
        if isinstance(pal, dict):
            c = _rgb3(pal.get(s.id))
            if c is not None:
                return c
            if s.role is not None:
                cands = [d for d in st.role_map.get(s.role, ()) if d != s.id]
                if cands:
                    lamps = look.get("lamps") if isinstance(look, Mapping) else {}
                    cands.sort(key=lambda d: ((lamps.get(d) or {}).get("screen") != s.screen,
                                              prog.zones.get(d) != s.zone, d))
                    for d in cands:
                        c = _rgb3(pal.get(d))
                        if c is not None:
                            return c
            for d in s.donors:
                c = _rgb3(pal.get(d))
                if c is not None:
                    return c
            c = _rgb3(pal.get("*"))
            if c is not None:
                return c
            return s.house_rgb or BLACK
        c = _rgb3(pal)
        return c if c is not None else self.neutral_rgb

    @staticmethod
    def _drop_hit(look: Optional[Mapping[str, Any]], takes_hits: bool) -> Optional[tuple]:
        """LookData.hit for this output: (t0, t1, step, swell_s, {lamp: rgb}) or None. ``step``: the output takes
        hits (DMX), or the drop was predicted (its timed hit reaches Hue too); otherwise it swells (§5.4: Hue never
        gets a reactive hit; an unforeseen drop gets a slow swell)."""
        h = look.get("hit") if look is not None else None
        if not isinstance(h, Mapping):
            return None
        t0, t1 = h.get("t0_ms"), h.get("t1_ms")
        lamps = h.get("lamps")
        if not (_isnum(t0) and _isnum(t1) and t1 > t0) or not isinstance(lamps, Mapping):
            return None
        cols = {}
        for k, v in lamps.items():
            c = _rgb3(v)
            if c is not None:
                cols[k] = c
        if not cols:
            return None
        sw = h.get("swell_ms")
        swell_s = (float(sw) if _isnum(sw) and sw > 0 else HIT_SWELL_MS) / 1000.0
        return (t0 / 1000.0, t1 / 1000.0, bool(takes_hits or h.get("predicted") is True), swell_s, cols)

    @staticmethod
    def _with_hit(pal: dict, hit: tuple, tt: float) -> dict:
        """The director's colours at ``tt`` with the drop's hit laid over them: a step for [t0, t1), or a swell
        (contract.hit_weight: smoothstep up over swell_s from t0, back down over swell_s from t1), mixed in OKLab so
        the colour moves as smoothly as the level."""
        t0, t1, step, swell_s, cols = hit
        w = contract.hit_weight(tt, t0, t1, step, swell_s)
        if w <= 0.0:
            return pal
        out = dict(pal)
        for k, c in cols.items():
            out[k] = c if w >= 1.0 else palette_mix(_rgb3(pal.get(k)) or BLACK, c, w)   # OKLab, as crossfades are
        return out

    @staticmethod
    def _transition_s(look: Any, snap: Optional[Mapping[str, Any]]) -> float:
        tr = look.get("transition") if isinstance(look, Mapping) else None
        if not isinstance(tr, Mapping) or tr.get("style") == "cut":
            return 0.0
        beats, secs = tr.get("beats"), tr.get("seconds")
        if _isnum(beats) and beats > 0 and snap is not None:
            return float(beats) * 60.0 / float(snap["bpm"])
        if _isnum(secs) and secs > 0:
            return float(secs)
        return 0.0

    def _layer_now(self, L: _Layer, st: _OutState, t: float, snap: Optional[Mapping[str, Any]], gate: str,
                   caps_hits: bool, min_attack_s: float, look: Any, vibe: bool, energy: float, trans_s: float,
                   middle_depth: float, min_period_s: float = 0.0) -> Optional[_LayerNow]:
        scale = 1.0
        if L.needs == "beat":
            if gate == "beat" and self.beat_lock:
                mode = "osc"
            elif gate == "dmx" and caps_hits and self.beat_lock:
                mode, scale = "osc", middle_depth
            else:
                mode = L.without_beat
        elif L.needs == "grid":
            mode = "osc" if snap is not None else L.without_beat
        else:
            mode = "osc"
        beat_locked = L.needs == "beat" and mode == "osc"
        d = self._input(L.depth, look, vibe, energy, beat_locked) * L.depth_scale * scale
        d = st.glide((L.id, "depth"), _c01(d), t, trans_s)
        if mode == "none" or d <= 0.0:
            return None
        if mode == "energy":
            return _LayerNow(layer=L, mode="energy", d=d, spread=0.0, variants=[], attack_s=0.0)
        spread = st.glide((L.id, "spread"), self._input(L.spread, look, vibe, energy), t, trans_s)
        attack_s = max(L.attack_s, min_attack_s)
        src = snap if snap is not None else (None if L.phase == "time" else self._free_run())
        variants = []
        if L.phase == "time":
            per = self._period(L, look)
            for w, p in st.fade(L.id, per, t, trans_s):
                variants.append((w, None, 0.0, p, p))
            return _LayerNow(layer=L, mode="osc", d=d, spread=spread,
                             variants=self._coarsen(variants, min_period_s, st, L.id), attack_s=attack_s)
        beat_s = 60.0 / float(src["bpm"])
        bpb = max(1, int(src.get("beats_per_bar") or FREE_RUN_BEATS_PER_BAR))
        anchor = float(src.get("bar_anchor_beat") or 0)
        if L.phase == "beat":
            sub = 1.0
            if L.subdivide:
                a = (look.get("accents") or {}).get("subdivision") if isinstance(look, Mapping) else None
                sub = float(a) if a in SUBDIVISIONS else 1.0
                while sub < 1.0 and beat_s * sub < 2.0 * attack_s:      # the 2 x attack rule: the next coarser
                    sub *= 2.0
            for w, sv in st.fade(L.id, sub, t, trans_s):
                variants.append((w, src, 0.0, sv, beat_s * sv))
        elif L.phase == "bar":
            for w, pb in st.fade(L.id, self._period(L, look), t, trans_s):
                variants.append((w, src, anchor, bpb * pb, beat_s * bpb * pb))
        else:                                                   # phrase
            pbars = max(1, int(src.get("phrase_bars") or FREE_RUN_PHRASE_BARS))
            variants.append((1.0, src, anchor, bpb * pbars, beat_s * bpb * pbars))
        return _LayerNow(layer=L, mode="osc", d=d, spread=spread,
                         variants=self._coarsen(variants, min_period_s, st, L.id), attack_s=attack_s)

    @staticmethod
    def _coarsen(variants: list, min_period_s: float, st: _OutState, lid: str) -> list:
        """The output's ``caps.min_period_ms`` (hue: 2 x min_attack, "never strobes", §5.4) for every oscillator,
        whatever its shape or phase: a cycle shorter than that runs at twice the period (and again), so it stays on
        its own grid (beats, bars, or seconds) and nothing on that output cycles faster. Noted in stats."""
        if min_period_s <= 0.0:
            return variants
        out = []
        for w, src, off, div, per in variants:
            if 0.0 < per < min_period_s:
                st.coarsened.add(lid)
                while per < min_period_s:
                    div, per = div * 2.0, per * 2.0
            out.append((w, src, off, div, per))
        return out

    def _period(self, L: _Layer, look: Any) -> float:
        """The layer's period (bars or seconds): a single number, or [slow, fast] by the look's motion.speed, moved in
        halvings of the slow period (a power of two), so a bar wave stays on the bar lines."""
        slow, fast = L.period or (1.0, 1.0)
        if slow == fast:
            return slow
        sp = self._look_value(look, "motion.speed")
        p = slow * (fast / slow) ** sp
        n = round(math.log2(slow / p))
        q = slow / (2.0 ** n)
        lo, hi = min(slow, fast), max(slow, fast)
        return min(hi, max(lo, q))
