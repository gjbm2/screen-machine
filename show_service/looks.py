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
     The resolve: oscillators scale the base; assignments (the hits below) are highest-takes-precedence for
     intensity; every other attribute is latest-takes-precedence (below, "Movers").
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
     ``colour_mix.encode`` for a type with channels (``fixture_encode.encode`` with ``attrs`` for a mover: below).

**Light scenes** (README S5.24; light_scenes.py). The engine also compiles every scene of config/live/light_scenes.yaml
(its layers and hits, with show.yaml ``looks.layers`` and ``looks.hits``' keys, and its ``caps.flash_hz_max``) against
the registry, one program per scene: at ``configure``, and again on show-brain when the catalogue changes (it
subscribes). The look's ``light_scene`` names the one that runs: its layers replace ``looks.layers`` and its hits
``looks.hits`` (a scene without ``hits`` flashes nothing). No name, or one that isn't compiled: ``looks.layers`` and
``looks.hits`` exactly as before. A change of scene crossfades per output over the look's transition: the old scene's
layer depths fade out as the new one's fade in, and the hits are the larger share's. A scene's problems are its own
(``stats()["program"]["scenes"]``), never ``configure``'s.
**Drop hits.** A hits block's ``drop: {depth, decay_ms}`` flashes its fixtures (on outputs that take hits) at each drop
line: ``trigger_hit(at_s, strength)``, or LookData ``light_scene_drop`` (the director's, read each frame, taken once
per id). The flash is held for the fixture's min_pulse from the line, at the fixture's own latency (a line ahead shows
on it), then decays; HTP like a hit; it shows over the pre-drop dip, but never under House, Low or a held frame.
**Identify and follow** (outputs.Rig calls them). ``identify(fixture_id, seconds, rgb)``: that fixture at full
``rgb`` (white) until it expires, whatever the frame (inactive too), bypassing layers, hits, master and caps.
``set_follow({id: "dmx" | "off" | {hex, bri}})``: a fixture not following the music shows its house state instead of
the look (dark, or the static colour at bri %), still through the output's rise limit; an id never named follows, so
nothing changes until room sends a mapping.

**Movers, room space and flow** (docs/LIGHTS.md §5; README S5.26). ``configure(looks, rig, lamps=, lights=, space=)``
also takes show.yaml ``lights`` (groups, ``motion``, ``jog``) and the room's SpaceView (space.py). Each fixture gets
its capabilities (``fixture_encode.caps(type, setup)``: pan, tilt, zoom, gobo slots, ...), its setup (home, soft
limits, speeds, default beam, named positions: fixtures.setup.yaml), its room position and, once taught, its pose.
Every cross-module piece is imported lazily and degrades: no fixture_encode, movers send pins and centre as before; no
space, no positions (patterns run in the light's own pan/tilt degrees, waves in group order); no ``light_programs``,
the ``light_scene`` path above.
  * **Layer kinds** (``kind``, default from ``attr``: every existing layer is ``intensity``): ``position`` (``mode``
    aim | fan | sweep | circle | figure8 | drift | ballyhoo | bounce | lift | tilt_wave | home, aimed at Room map
    names through the pose's inverse kinematics, else in pan/tilt degrees around ``home`` or the light's own saved
    position), ``beam`` (zoom, focus, iris, frost), ``gobo``, ``prism``, ``colour`` (a palette role mixed in OKLab),
    ``colour_wheel``, ``cct`` and ``shutter`` (effects only). A layer reaches only the members that can show what it
    sets (capability-aware: the rest are skipped silently, counted in stats). ``select`` defaults to ``"*"``. Any
    continuous value may be an oscillator ``{fx, phase, period_bars | period_s, subdivide, between: [a, b], spread,
    order}``. Spread follows ``order`` (group | flow | mirror) and ``spread_mode`` (unison | chase | mirror): with
    ``order: flow`` a wave travels the room along the flow (space.flow_coord of each light's position, else its
    place in the group), latency compensated because each light evaluates at its own time.
  * **Precedence per fixture**, each frame: held (the Lights page's Move it: ``hold``/``release``) > identify > not
    following > inactive, House or Low (house look; a mover glides home) > the group's program (its scene, crossfading
    per group, then its effects, hits, master and caps), then the motion stage, then encode.
  * **Programs** (LookData ``light_programs``, one per show.yaml ``lights.groups`` group, light_scenes.group_members'
    membership): each group runs its own scene (compiled per group: a layer's ``(i, n)`` is within the group), its
    effects (an envelope over each run: up over ``fade.in_beats``, held, down over ``fade.out_beats``; intensity
    layers multiply by depth x envelope, the other kinds lerp over the scene by it) and its transition, which lands
    at the program's ``at_ms`` (the screens' moment) at each fixture's own photon time: ``cut`` (positions pre-rolled
    to arrive on it), ``dark_move``, ``crossfade``, ``fade_dark`` (to the group's ``dark_level`` and back, positions
    moving while dark) or ``sweep`` (each light's crossfade starts at ``at + flow_coord x wave_beats``).
  * **The motion stage** (§5.5): commanded pan and tilt never move faster than the light's ``max_speed`` (setup,
    else lights.motion's starting point), from rest through ``accel_s``; soft limits clamp (``clamped``); a hop or a
    cut that lands on a line is pre-rolled by its travel time so the beam arrives on the beat, and a hop period that
    can't be met doubles on the grid (``slowed``); a pattern faster than the light shrinks to ``min_size_share`` and
    then doubles its period (``slowed``): degrade, never refuse. A dark move takes the intensity to 0 before the
    move starts (its fall latency), moves at full speed, and lights again at arrival + ``settle_ms``.
  * **The hold** (§5.7, any thread): ``hold(fixture, values, idle_s=, smooth_ms=, free=)`` merges values into the
    held target (clamped to the range, and to the soft limits unless ``free``); the fixture is out of everything
    else until ``release`` or ``idle_s`` after the last call, gliding there (pan/tilt first order within its speed)
    and back to its program through the motion stage. A frame with a held or gliding light says ``busy``.

**The ensemble** (docs/ENSEMBLE.md §7.2; the lights builder, 22 Oct). Two more LookData fields; without them the engine
runs exactly as before, byte for byte:
  * ``gestures`` (contract.GestureData, newest last): each ``(id, seq)`` not yet taken goes to ``take_gesture`` (any
    thread, under the drop lock), which resolves each part's player to its member fixtures through the ensemble (a
    player id that is a fixture id is that fixture) and keeps the part on one ring (GESTURE_RING parts, each until 8
    decays past its peak). A re-sent id replaces its parts; ``parts: []`` calls it off. Per lamp per frame (the active
    branch, after the layers, effects and hits, before fade_dark, master and caps), each part at the light's own times
    through ``contract.gesture_weight``, the one envelope rule shared with the virtual Maestro's lamps: a flash HTP over
    the level (a step on an output that takes hits or for a predicted part, peaking on its line; a reactive part on Hue
    swells over HIT_SWELL_MS), a colour pop toward the part's palette role, and a kick (a calibrated mover's aim point
    up KICK_M x kick x w through the motion stage; an uncalibrated light with zoom widens; neither: nothing). Never the
    shutter; on an output with ``caps.min_period_ms`` a part closer than that to the one before merges into it (the
    larger strength). Not under House, Low or a held frame, nor on a held, identified or not-following light; over the
    pre-drop dip only a ``hit``.
  * ``ensemble`` (contract.EnsembleData): a light plays as the player whose ``members`` hold it, its part ``next``'s
    from ``next.at_ms`` at its own time, else the current one. Its role's ``scale``, glided over ``glide_ms``: intensity
    layers with phase ``beat`` take depth x ``pulse``, the other oscillators x ``motion``, and pattern sizes (position
    and beam) x ``motion``. Level and the palette role are already in the director's colours, never applied again. A
    group's program transition at the ensemble's look change (its ``at_ms`` or ``next.at_ms``) lands its part's
    ``stagger_beats`` later on each light (no clock: ``stagger_s``); ``sweep`` and ``fade_dark`` are never staggered.
    Canon's ``clock_offset_beats`` runs that light's oscillators that many beats late.

**Parity with the virtual Maestro.** With no vibe, the neutral colour and depth (artnet_out's ``DEFAULT_RGB`` /
``HUE_DEPTH``, or what outputs.py passes as ``neutral``) and show.yaml's ``pulse`` layer (or this module's default
layer when ``looks.layers`` is absent), a Hue lamp's ``rgb`` equals artnet_out's hue channels / 255 for the same clock,
features and depth: through a beat, energy without a beat, and the quiet and no-features idles (tests/test_looks.py).

Code fallbacks are show.yaml's own starting points (S5.0 rule 2), marked where they are defined. Config names live in
config: nothing here branches on a mode, vibe, fixture, output, group, zone, screen, layer, scene, point or role name.
"""

from __future__ import annotations

import collections
import hashlib
import itertools
import json
import logging
import math
import threading
import time
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
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
DEFAULT_DROP_DEPTH = 1.0        # starting point: a drop flash reaches full (level + 1, HTP)
DROP_RING = 16                  # recent drop lines kept (trigger_hit)
SCENE_FADE = "\x00light_scene"  # the per-output crossfade between light scenes (an _OutState fade key no layer has);
                                # + a group id: that group's (docs/LIGHTS.md §5.6)
_BASE_MIX: list = [(1.0, None)]  # no light scene: looks.layers and looks.hits at full weight

# -- movers, room space and flow (docs/LIGHTS.md §5; show.yaml lights.motion / lights.jog's starting points) -----------
DEFAULT_MAX_SPEED_DEG_S = (180.0, 120.0)   # starting point (lights.motion.default_max_speed_deg_s): pan, tilt, until
                                           # each light's own is measured (setup max_speed wins)
DEFAULT_ACCEL_S = 0.12          # starting point (lights.motion.accel_s): from rest to full speed
DEFAULT_PT_SPEED = 1.0          # lights.motion.pt_speed: the light's own pan/tilt speed channel (1 = its fastest)
DEFAULT_MAX_TRAVEL_SHARE = 0.8  # starting point: a beat-locked hop may spend at most this share of its period moving
DEFAULT_DARK_MOVE_MIN_DEG = 30.0   # starting point: a jump bigger than this travels dark when the transition says so
DEFAULT_SETTLE_MS = 120.0       # starting point: a dark move lights again this long after it arrives
DEFAULT_MIN_SIZE_SHARE = 0.4    # starting point: a pattern too fast for a light shrinks to this share, then slows
DEFAULT_DARK_MAX_S = 8.0        # starting point: fade_dark never stays dark longer (without the song's in time)
DEFAULT_JOG_IDLE_S = 60.0       # starting point (lights.jog.idle_s): a hold with no call this long hands the light back
DEFAULT_JOG_SMOOTH_MS = 80.0    # starting point (lights.jog.smooth_ms): the hold's glide toward each new target
DEFAULT_SIZE_M = 1.0            # starting point: a pattern layer without ``size`` (metres on its surface)
DEFAULT_SIZE_DEG = 20.0         # starting point: ... without ``size_deg`` (an uncalibrated light's own degrees)
DEFAULT_LIFT_M = 1.0            # starting point: a lift layer without ``lift`` (metres up or down)
SPEED_FIT = 0.95                # §5.5: a pattern's peak speed is kept under this share of the light's
FIT_SAMPLES = 64                # samples per cycle when a pattern's peak speed is measured
MAX_DOUBLINGS = 6               # a pattern or hop slows at most 2^6 times (then runs as fast as it can: never refused)
ARRIVED_DEG = 0.5               # a move has arrived within this (dark moves, busy)
DT_MAX_S = 0.25                 # the motion stage's longest step between two frames
FIT_CACHE_MAX = 512             # pattern speed fits kept per output (cleared whole when full)
COMPAT = ""                     # the group key of the light_scene path (no light_programs: every lamp, one group)
LAYER_KINDS: tuple[str, ...] = ("intensity", "position", "beam", "gobo", "prism", "colour", "colour_wheel", "cct",
                                "shutter")
POSITION_MODES: tuple[str, ...] = ("aim", "fan", "sweep", "circle", "figure8", "drift", "ballyhoo", "bounce", "lift",
                                   "tilt_wave", "home")
PATTERN_MODES = frozenset({"fan", "sweep", "circle", "figure8", "drift", "ballyhoo"})
ORDERS: tuple[str, ...] = ("group", "flow", "mirror")
SPREAD_MODES: tuple[str, ...] = ("unison", "chase", "mirror")
EASES: tuple[str, ...] = ("linear", "sine", "smooth")
CHANGE_ON: tuple[str, ...] = ("none", "bar", "phrase", "section", "look")
HOP_ON: tuple[str, ...] = ("beat", "bar")
TARGET_WORDS: tuple[str, ...] = ("home", "flow", "centre", "up", "down")
ROLE_WORDS: tuple[str, ...] = ("base", "accent", "highlight")    # LookData.palette.roles
SHUTTER_LAYER_MODES: tuple[str, ...] = ("strobe", "pulse", "random")
FLOW_DIRS: tuple[str, ...] = tuple(getattr(contract, "FLOW_DIRS", (
    "still", "up", "down", "left", "right", "cw", "ccw", "outward", "inward", "towards", "away")))
FLOW_BACK = frozenset({"left", "down", "ccw", "inward", "away"})   # the second of each opposite pair: group order runs
                                                                   # backwards for it (no positions)
#: What a layer kind needs a fixture to show (any one of them), and the attributes it sets
KIND_ATTRS: dict[str, tuple[str, ...]] = {"position": ("pan",), "beam": ("zoom", "focus", "iris", "frost"),
                                          "prism": ("prism",), "colour": ("colour",), "colour_wheel": ("colour_slot",),
                                          "cct": ("cct",), "shutter": ("shutter",)}
LERP_ATTRS = frozenset({"pan", "tilt", "zoom", "focus", "iris", "frost", "cct", "pt_speed", "gobo_spin", "gobo2_spin",
                        "gobo_shake", "gobo2_shake", "gobo_scroll", "prism_spin", "colour_spin", "pan_spin",
                        "tilt_spin"})
GLIDE_ATTRS = ("intensity", "zoom", "focus", "iris", "frost")   # the hold glides these over smooth_ms
ENC_PLAIN = frozenset({"intensity", "colour", "colour_slot"})    # colour_mix encodes these: anything more is ``enc``
_LAYER_KEYS = frozenset({"id", "enabled", "attr", "kind", "fx", "phase", "subdivide", "period_bars", "period_s",
                         "depth", "depth_scale", "spread", "needs", "without_beat", "attack_ms", "decay_ms", "select",
                         "order", "spread_mode", "flow_dir"})
_KIND_KEYS: dict[str, frozenset] = {
    "intensity": frozenset(),
    "position": frozenset({"mode", "on", "centre", "size", "size_deg", "along", "rotate", "fan", "ease", "points",
                           "hop", "lift", "dark_move"}),
    "beam": frozenset({"zoom", "focus", "iris", "frost"}),
    "gobo": frozenset({"wheel", "slot", "change_on", "spin", "spin_scale", "shake"}),
    "prism": frozenset({"in", "spin", "spin_scale"}),
    "colour": frozenset({"role"}),
    "colour_wheel": frozenset({"mode", "step_on", "spin"}),
    "cct": frozenset({"k"}),
    "shutter": frozenset({"mode", "hz"}),
}
_OSC_KEYS = frozenset({"fx", "phase", "period_bars", "period_s", "subdivide", "between", "spread", "order"})
_HIT_KEYS = frozenset({"select", "styles", "bands", "min_strength", "depth", "decay_ms", "drop"})
_DROP_KEYS = frozenset({"depth", "decay_ms"})
_LOOKS_KEYS = frozenset({"align_pct", "min_visible_ms", "middle_depth", "master", "caps", "layers", "hits"})
_MARKERS = frozenset({"depth", "spread", "fan", "lift", "shake", "zoom", "focus", "iris", "frost"})   # a bad value
                                # whose own problem line is written already

# -- the ensemble (docs/ENSEMBLE.md §7.2; the lights builder, 22 Oct) -------------------------------------------------
GESTURE_RING = 128              # starting point: gesture parts kept (oldest out first), each to 8 decays past its peak
KICK_M = 0.4                    # starting point: a calibrated mover's aim point rises this many metres x shape.kick
GESTURE_KINDS: tuple[str, ...] = tuple(getattr(contract, "GESTURE_KINDS", (
    "accent", "downbeat", "phrase", "fill", "hit", "section", "swell", "note", "chord")))
GESTURE_OVER_DIP = "hit"        # the one kind shown over the pre-drop dip (as the drop flash is)
STAGGER_KINDS = frozenset({"cut", "dark_move", "crossfade"})   # a sweep travels already; fade_dark goes dark together
STAGGER_MATCH_MS = 5.0          # a group's transition is the ensemble's look change when their at_ms agree this closely
GESTURE_AHEAD_S = 1.0           # a part is looked at from this long before its rise (a light's rise latency + attack)
GESTURE_TAIL_S = 0.5            # ... and kept this long past its 8 decays (a light's min_pulse, a reactive swell)
GESTURE_SEEN = 512              # gesture ids remembered with their last seq (oldest forgotten first)
_SHAPE_KEYS = ("attack_ms", "hold_ms", "decay_ms", "depth")   # a GestureShape needs these; colour_depth, kick: 0
_UNSEEN = object()              # a gesture id not taken yet


# -- small helpers ----------------------------------------------------------------------------------------------------
def _c01(x: float) -> float:
    return 0.0 if not x > 0.0 else 1.0 if x > 1.0 else x


def _isnum(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _smoothstep(x: float) -> float:
    x = 0.0 if x < 0.0 else 1.0 if x > 1.0 else x
    return x * x * (3.0 - 2.0 * x)


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _ease(kind: str, x: float) -> float:
    if kind == "sine":
        return 0.5 - 0.5 * math.cos(math.pi * x)
    if kind == "smooth":
        return _smoothstep(x)
    return x


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


def _vec3(v: Any) -> Optional[tuple[float, float, float]]:
    if isinstance(v, (list, tuple)) and len(v) == 3 and all(_isnum(x) for x in v):
        return (float(v[0]), float(v[1]), float(v[2]))
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


def travel_s(d: float, v: float, acc: float) -> float:
    """How long the motion stage takes to move ``d`` degrees from rest to rest at top speed ``v`` (deg/s) and
    acceleration ``acc`` (deg/s², inf: none): ``d/v + v/acc`` once it reaches ``v`` (with acc = v / accel_s that is
    docs/LIGHTS.md §5.5's ``d/v + accel_s``), else a triangle ``2 sqrt(d/acc)``."""
    d = abs(d)
    if d <= 0.0 or v <= 0.0:
        return 0.0
    if not math.isfinite(acc):
        return d / v
    if d >= v * v / acc:
        return d / v + v / acc
    return 2.0 * math.sqrt(d / acc)


def flow_wave(flow: Optional[Mapping[str, Any]], clock_body: Optional[Mapping[str, Any]], t_s: float,
              coord: float) -> float:
    """docs/LIGHTS.md §4.1's shared phase (0..1) at host time ``t_s`` for a place at ``coord`` (space.flow_coord):
    ``frac((beat(t) - anchor_beat) / period_beats - spread x coord)`` with a clock grid, else
    ``frac((t - at_ms / 1000) / period_s - spread x coord)``. 0 without a flow."""
    if not isinstance(flow, Mapping):
        return 0.0
    sp = flow.get("spread")
    sp = float(sp) if _isnum(sp) else 0.0
    pb, ab = flow.get("period_beats"), flow.get("anchor_beat")
    if isinstance(clock_body, Mapping) and _isnum(clock_body.get("bpm")) and clock_body["bpm"] > 0 and \
            _isnum(pb) and pb > 0 and _isnum(ab):
        x = (beat_at(clock_body, t_s) - float(ab)) / float(pb) - sp * coord
    else:
        ps, at = flow.get("period_s"), flow.get("at_ms")
        if not (_isnum(ps) and ps > 0):
            return 0.0
        x = (t_s - (float(at) / 1000.0 if _isnum(at) else 0.0)) / float(ps) - sp * coord
    return x - math.floor(x)


def _slew1(c: float, v: float, tgt: float, moving: bool, vmax: float, acc: float, dt: float
           ) -> tuple[float, float]:
    """One axis of the motion stage (§5.5) over one frame of ``dt`` s: (command, speed). Never faster than ``vmax``.
    A moving target (a pattern) within this frame's reach is taken as is; otherwise the command runs the trapezoid
    toward it (speeding up by ``acc`` per second, then slowing to stop on it), integrated at mid-frame speeds so it
    takes exactly ``travel_s`` (the pre-roll's own number)."""
    err = tgt - c
    if dt <= 0.0:
        return c, v
    a = abs(err)
    if a <= 1e-9:
        return tgt, (err / dt if moving else 0.0)
    v0 = abs(v) if (v > 0.0) == (err > 0.0) else 0.0
    if not math.isfinite(acc):
        if vmax * dt >= a:
            return tgt, (err / dt if moving else 0.0)
        return c + math.copysign(vmax * dt, err), math.copysign(vmax, err)
    v1 = min(vmax, v0 + acc * dt)
    if moving and a <= v1 * dt:
        return tgt, err / dt
    brake = math.sqrt(2.0 * acc * a) - 0.5 * acc * dt            # the mid-frame speed that still stops on it
    mid = min(0.5 * (v0 + v1), brake)
    step = mid * dt
    if brake <= 0.0 or step >= a:
        return tgt, (err / dt if moving else 0.0)
    v_end = v1 if mid >= 0.5 * (v0 + v1) - 1e-12 else max(0.0, brake - 0.5 * acc * dt)
    return c + math.copysign(step, err), math.copysign(v_end, err)


# -- selectors (fixtures.py's: one copy of the grammar, README S5.2) -------------------------------------------------
def _selector() -> tuple[Optional[Callable[..., list[str]]], Optional[str]]:
    """fixtures.select (B12's), imported lazily; (None, why) when it can't be imported (every layer selects nothing)."""
    try:
        from . import fixtures
    except Exception as e:                                      # degrade: the rest of the engine still runs
        return None, f"{type(e).__name__}: {e}"
    fn = getattr(fixtures, "select", None)
    return (fn, None) if callable(fn) else (None, "fixtures.select is missing")


def _module(name: str) -> tuple[Any, Optional[str]]:
    """A sibling module (fixture_encode, space, light_scenes), imported lazily: (module, None) or (None, why)."""
    try:
        import importlib
        return importlib.import_module(f"{__package__}.{name}"), None
    except Exception as e:                                      # degrade: that piece hasn't landed or doesn't load
        return None, f"{name}.py can't be imported ({type(e).__name__}: {e})"


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
    depth: Any                                  # float | a LOOK_INPUTS word | _Osc (not on intensity layers)
    depth_scale: float
    spread: Any                                 # float | a LOOK_INPUTS word
    needs: str
    without_beat: str
    attack_s: float
    decay_s: float
    members: Mapping[str, tuple[int, int]]      # fixture id -> (i, n) in the layer's ordered selection
    # docs/LIGHTS.md §5.2
    kind: str = "intensity"                     # LAYER_KINDS
    order: str = "group"                        # ORDERS: what a member's spread lag follows
    spread_mode: str = "chase"                  # SPREAD_MODES
    flow_dir: Optional[str] = None              # its own flow direction instead of the program's
    p: Mapping[str, Any] = field(default_factory=dict)   # the kind's own keys, compiled
    ids: tuple[str, ...] = ()                   # members in order


@dataclass(frozen=True)
class _Osc:
    """A continuous value that oscillates (§5.2): ``lo + (hi - lo) x shape`` at the member's phase."""
    layer: _Layer                               # its fx, phase and period (a pseudo layer: depth 1, needs none)
    lo: float
    hi: float


@dataclass(frozen=True)
class _Hits:
    members: frozenset
    styles: frozenset
    bands: Optional[frozenset]
    min_strength: float
    depth: float
    decay_s: float
    drop: Optional[tuple[float, float]] = None  # (depth, decay_s): a flash at each drop line (trigger_hit)


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
    # docs/LIGHTS.md §5.1
    caps: Optional[Mapping[str, Any]] = None    # fixture_encode.caps (None: no encoder, intensity and colour only)
    attrs: frozenset = frozenset(("intensity", "colour"))
    setup: Mapping[str, Any] = field(default_factory=dict)
    pos: Optional[tuple[float, float, float]] = None   # its room position (the space's, or its pose's)
    pose: Optional[Mapping[str, Any]] = None
    pos_s: float = 0.0                          # LatencyView position_ms
    fall_s: float = 0.0
    group: str = COMPAT
    gi: tuple[int, int] = (0, 1)                # its place in its group's order
    enc: bool = False                           # ranges beyond intensity, colour and the wheel: fixture_encode
    moves: bool = False                         # it has pan and tilt (the motion stage runs)
    max_speed: tuple[float, float] = DEFAULT_MAX_SPEED_DEG_S
    home: tuple[float, float] = (0.0, 0.0)
    range: tuple[float, float, float, float] = (-270.0, 270.0, -135.0, 135.0)   # pan lo, hi, tilt lo, hi
    limits: tuple[float, float, float, float] = (-270.0, 270.0, -135.0, 135.0)  # the range within setup's limits
    positions: Mapping[str, tuple[float, float]] = field(default_factory=dict)  # setup positions (pan, tilt)
    neutral: Mapping[str, Any] = field(default_factory=dict)   # every attr at rest (no intensity or colour)
    aimkw: Mapping[str, Any] = field(default_factory=dict)     # space.aim's range, limits and speed for it


@dataclass(frozen=True)
class _OutProg:
    id: str
    hits: Optional[bool]                        # fixtures.yaml outputs.<id>.hits (it can only turn them off)
    min_attack_ms: Optional[float]              # ... min_attack_ms (it can only raise the driver's floor)
    slots: tuple[_Slot, ...]
    hit_horizon_s: float
    hit_members: bool                           # any of its fixtures is in looks.hits.select (takes_hit per output)
    max_pulse_s: float = 0.0                    # its fixtures' longest min_pulse (a drop flash's horizon)
    groups: tuple[str, ...] = ()                # the light groups its fixtures are in


@dataclass(frozen=True)
class _Scene:
    """One light scene (light_scenes.yaml), compiled like looks.layers and looks.hits; or one light effect."""
    name: str
    layers: tuple[_Layer, ...]                  # ids "<scene>/<layer id>": their own glides and fades
    hits: Optional[_Hits]                       # None: no flashes in this scene
    flash_hz_max: Optional[float]               # the scene's caps.flash_hz_max (None: looks.caps')
    hit_members: Mapping[str, bool]             # output id -> any of its fixtures is in the scene's hits selection
    hit_horizon_s: Mapping[str, float]          # output id -> a music flash's horizon there
    label: str = ""
    fade: tuple[float, float] = (0.0, 0.0)      # an effect's fade {in_beats, out_beats}
    amembers: frozenset = frozenset()           # the fixtures any non-intensity layer reaches


@dataclass(frozen=True)
class _Motion:
    """show.yaml ``lights.motion`` and ``lights.jog``, read (§12)."""
    max_speed: tuple[float, float] = DEFAULT_MAX_SPEED_DEG_S
    accel_s: float = DEFAULT_ACCEL_S
    pt_speed: float = DEFAULT_PT_SPEED
    max_travel_share: float = DEFAULT_MAX_TRAVEL_SHARE
    dark_move_min_deg: float = DEFAULT_DARK_MOVE_MIN_DEG
    settle_s: float = DEFAULT_SETTLE_MS / 1000.0
    min_size_share: float = DEFAULT_MIN_SIZE_SHARE
    dark_max_s: float = DEFAULT_DARK_MAX_S
    jog_idle_s: float = DEFAULT_JOG_IDLE_S
    jog_smooth_s: float = DEFAULT_JOG_SMOOTH_MS / 1000.0


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
    scenes: Mapping[str, _Scene] = field(default_factory=dict)   # light scenes by name (README S5.24)
    scene_revision: str = ""                    # the catalogue's revision they were compiled from
    scene_problems: tuple[str, ...] = ()
    # docs/LIGHTS.md §5.6
    groups: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)   # group -> GroupInfo
    group_scenes: Mapping[str, Mapping[str, _Scene]] = field(default_factory=dict)    # group -> scene -> compiled
    group_effects: Mapping[str, Mapping[str, _Scene]] = field(default_factory=dict)   # group -> effect -> compiled
    motion: _Motion = _Motion()
    space: Optional[Mapping[str, Any]] = None   # the SpaceView compiled against
    space_mod: Any = None                       # space.py (None: no positions)
    enc_mod: Any = None                         # fixture_encode.py (None: colour_mix only)
    skipped: Mapping[str, int] = field(default_factory=dict)    # layer id -> members it can't reach (capabilities)
    fixture_ids: frozenset = frozenset()        # every fixture evaluated (docs/ENSEMBLE.md §7.2: a part's player)


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


class _ProgFade:
    """A group's program change (§5.6): the old scene -> the new one, landing at the transition's ``at`` (host s) at
    each fixture's own time, by the transition's kind. A new transition run (or a new scene) starts the next one."""
    __slots__ = ("old", "new", "key", "at", "kind", "params", "dur", "wave_s", "dark_level")

    def __init__(self, scene: Optional[str], key: Any) -> None:
        self.old = self.new = scene
        self.key = key
        self.at = -math.inf
        self.kind = "cut"
        self.params: Mapping[str, Any] = {}
        self.dur = 0.0                          # crossfade, sweep: seconds
        self.wave_s = 0.0                       # sweep: the crossing time across the room
        self.dark_level: Any = 0.0

    def x(self, t: float, coord: float = 0.0) -> float:
        """The new scene's share at fixture time ``t`` (``coord``: its place along the flow, for a sweep)."""
        if self.old == self.new:
            return 1.0
        start = self.at + (coord * self.wave_s if self.kind == "sweep" else 0.0)
        if self.kind in ("crossfade", "sweep") and self.dur > 0.0:
            return _c01((t - start) / self.dur)
        return 1.0 if t >= start else 0.0

    def darkness(self, t: float, dark_max_s: float) -> float:
        """fade_dark: 0..1 toward the group's dark level at intensity time ``t`` (out over out_s ending at ``at``,
        dark until the song's in time or ``at + dark_max_s``, in over in_s)."""
        if self.kind != "fade_dark":
            return 0.0
        p = self.params
        out_s = float(p["out_s"]) if _isnum(p.get("out_s")) and p["out_s"] > 0 else 0.0
        in_s = float(p["in_s"]) if _isnum(p.get("in_s")) and p["in_s"] > 0 else 0.0
        in_at = float(p["in_at_ms"]) / 1000.0 if _isnum(p.get("in_at_ms")) else None
        start_in = self.at + dark_max_s if in_at is None else min(in_at, self.at + dark_max_s)
        if t < self.at - out_s:
            return 0.0
        if t < self.at:
            return _c01((t - (self.at - out_s)) / out_s) if out_s > 0.0 else 1.0
        if t < start_in:
            return 1.0
        if in_s <= 0.0:
            return 0.0
        return 1.0 - _c01((t - start_in) / in_s)


class _FixState:
    __slots__ = ("prev", "t_prev", "low", "t_big",
                 # docs/LIGHTS.md §5.5: the motion stage and the last frame (a runner's own)
                 "cmd", "vel", "t_m", "tgt", "dark", "t_move", "t_light", "last_attrs", "last_inten", "last_colour",
                 "slowed", "clamped", "unresolved", "unreachable", "held", "hold_now", "hold_seq")

    def __init__(self) -> None:
        self.prev: Optional[float] = None
        self.t_prev = 0.0
        self.low: Optional[float] = None
        self.t_big: Optional[float] = None
        self.cmd: Optional[list[float]] = None   # commanded [pan, tilt]
        self.vel = [0.0, 0.0]
        self.t_m: Optional[float] = None
        self.tgt: Optional[list[float]] = None   # the last target (a pattern moves it every frame)
        self.dark = 0                            # 0 none, 1 going dark, 2 moving dark, 3 settling
        self.t_move = 0.0
        self.t_light = 0.0
        self.last_attrs: Optional[dict] = None
        self.last_inten = 0.0
        self.last_colour: list = [1.0, 1.0, 1.0]
        self.slowed: dict[str, str] = {}
        self.clamped: set[str] = set()
        self.unresolved: set[str] = set()
        self.unreachable: set[str] = set()
        self.held = False
        self.hold_now: Optional[dict] = None
        self.hold_seq = 0


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
        # docs/LIGHTS.md §5.6
        self.pfades: dict[str, _ProgFade] = {}   # group -> its program change
        self.fx: dict[str, dict[str, list]] = {} # group -> effect run id -> [name, at_s, until_s, left_s, strength]
        self.coords: dict[tuple, dict[str, float]] = {}   # (dir, centre, mirror, revisions) -> fixture -> 0..1
        self.fits: dict[tuple, tuple[float, int]] = {}    # a pattern's (size share, period factor) per light
        self.targets: dict[tuple, Optional[tuple[float, float]]] = {}   # bounce points resolved per light
        self.frames: dict[tuple, tuple] = {}     # where each light draws each pattern (_pattern_frame)
        self.slotc: dict[tuple, list[int]] = {}  # a gobo layer's slot candidates per light
        self.programs: dict[str, Optional[str]] = {}     # group -> the scene showing (stats)
        self.busy = False
        self.prog: Any = None                    # the program the caches above were filled from

    def caches_for(self, prog: Any) -> None:
        """A new program (configure, a catalogue change): every per-light cache is filled again."""
        if prog is not self.prog:
            self.prog = prog
            for c in (self.coords, self.fits, self.targets, self.frames, self.slotc):
                c.clear()

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

    def fixstate(self, fid: str) -> _FixState:
        fs = self.fix.get(fid)
        if fs is None:
            fs = self.fix[fid] = _FixState()
        return fs


@dataclass
class _LayerNow:
    """One layer for one frame: computed once, then evaluated per member."""
    layer: _Layer
    mode: str                                   # "osc" | "energy"
    d: float
    spread: float
    variants: list                              # [(weight, src clock body | None, offset beats, div, period_s)]
    attack_s: float


class _Part:
    """One scene in a group's mix for one frame: its intensity layers (computed once) and the rest (per fixture),
    weighted per fixture by ``w(t, coord)``."""
    __slots__ = ("scene", "inows", "alayers", "amembers", "pf", "new", "w0")

    def __init__(self, scene: Optional[_Scene], inows: list, alayers: tuple, amembers: frozenset,
                 pf: Optional[_ProgFade], new: bool, w0: float) -> None:
        self.scene, self.inows, self.alayers, self.amembers = scene, inows, alayers, amembers
        self.pf, self.new, self.w0 = pf, new, w0

    def w(self, t: float, coord: float = 0.0) -> float:
        if self.pf is None:
            return self.w0
        x = self.pf.x(t, coord)
        return x if self.new else 1.0 - x


class _Group:
    """One group of one output for one frame (§5.6): its scenes' parts, effects, hits, flow and transition."""
    __slots__ = ("gid", "parts", "effects", "blocks", "flash_hz", "flow", "pf", "dark_abs", "dom", "amembers")

    def __init__(self, gid: str) -> None:
        self.gid = gid
        self.parts: list[_Part] = []
        self.effects: list[tuple] = []          # (effect _Scene, envelope fn(t) -> 0..1, inows)
        self.blocks: list[tuple] = []           # (hits, weight, flashes, drops, horizon)
        self.flash_hz: Optional[float] = None
        self.flow: Optional[Mapping[str, Any]] = None
        self.pf: Optional[_ProgFade] = None
        self.dark_abs = 0.0
        self.dom: Optional[_Scene] = None
        self.amembers: frozenset = frozenset()  # every light a non-intensity layer of its scenes or effects reaches


class _Hold:
    """One light held by the Lights page (§5.7). ``target`` is swapped whole by ``hold``; the runner owns ``now``."""
    __slots__ = ("target", "until_s", "free", "smooth_s", "seq")

    def __init__(self, target: dict, until_s: float, free: bool, smooth_s: float, seq: int) -> None:
        self.target, self.until_s, self.free, self.smooth_s, self.seq = target, until_s, free, smooth_s, seq


class _Ctx:
    """What every per-fixture step of one frame reads (built once per evaluate)."""
    __slots__ = ("t", "dt", "look", "snap", "src", "beat_s", "bpb", "bar_anchor", "phrase_bars", "trans_s",
                 "min_period_s", "energy", "vibe", "st", "prog", "op", "sm", "view", "cyc", "flow_key", "look_n",
                 # docs/ENSEMBLE.md §7.2 (None without an ensemble): per light its role's motion scale and canon's
                 # offset (s), filled as each light is evaluated; the palette roles at full value (a colour pop's)
                 "ms", "co", "roles")


class _GPart:
    """One gesture part on the ring (docs/ENSEMBLE.md §7.2), resolved to the fixtures it lights. ``shown`` and
    ``merged`` are counted once per part."""
    __slots__ = ("gid", "kind", "at_s", "strength", "shape", "attack_s", "depth", "cdepth", "kick", "predicted",
                 "colour", "dur_s", "members", "end_s", "shown", "merged")

    def __init__(self, gid: str, kind: str, at_s: float, strength: float, shape: Mapping[str, Any], predicted: bool,
                 colour: Optional[str], dur_s: float, members: frozenset) -> None:
        self.gid, self.kind, self.at_s, self.strength, self.shape = gid, kind, at_s, strength, shape
        self.attack_s = float(shape["attack_ms"]) / 1000.0
        self.depth = float(shape["depth"])
        self.cdepth = float(shape.get("colour_depth") or 0.0)
        self.kick = float(shape.get("kick") or 0.0)
        self.predicted, self.colour, self.dur_s, self.members = predicted, colour, dur_s, members
        peak = at_s + max(dur_s, self.attack_s, HIT_SWELL_MS / 1000.0)
        self.end_s = peak + float(shape["hold_ms"]) / 1000.0 + HIT_TAIL_DECAYS * float(shape["decay_ms"]) / 1000.0 \
            + GESTURE_TAIL_S
        self.shown = self.merged = False


class _EnsView:
    """LookData.ensemble for one frame (docs/ENSEMBLE.md §7.2): each fixture's player now and in ``next``, a fixture's
    part at a time, and the look change's moments (``at_ms``, ``next.at_ms``)."""
    __slots__ = ("cur", "nxt", "fcur", "fnxt", "next_at_s", "ats", "glide_s")

    def part(self, fid: str, tt: float) -> Optional[Mapping[str, Any]]:
        """The part in effect for ``fid`` at its time ``tt``: next's from next.at_ms, else the current one."""
        if self.nxt is not None and tt >= self.next_at_s:
            p = self.nxt.get(self.fnxt.get(fid))
            if isinstance(p, Mapping):
                return p
        p = self.cur.get(self.fcur.get(fid))
        return p if isinstance(p, Mapping) else None

    def moment(self, at_s: float) -> bool:
        """``at_s`` is the ensemble's look change (a program transition landing with it)."""
        return any(abs(at_s * 1000.0 - a) <= STAGGER_MATCH_MS for a in self.ats)


# -- the engine -------------------------------------------------------------------------------------------------------
class LookEngine:
    """README S5.5. ``configure`` on show-brain; ``evaluate`` only from that output's runner; ``set_look``, ``on_hit``,
    ``active``, ``stats``, ``identify``, ``set_follow``, ``trigger_hit``, ``hold``, ``release``, ``holds``,
    ``caps_of`` and ``live_view`` from any thread.

    ``light_scenes``: a light_scenes.Catalogue (tests); None: the process's shared one (light_scenes.shared());
    False: no light scenes. ``encoder`` / ``space_mod``: fixture_encode and space (tests pass fakes); None: imported
    lazily at each configure (a piece that hasn't landed degrades); False: none."""

    def __init__(self, *, palette_fn: Optional[Callable[[float], Any]] = None,
                 look_fn: Optional[Callable[[], Any]] = None, clock: Any = None,
                 features_fn: Optional[Callable[[], Any]] = None,
                 neutral: tuple[Sequence[float], float] = (NEUTRAL_RGB, HUE_DEPTH),
                 mono: Callable[[], float] = time.monotonic, light_scenes: Any = None,
                 encoder: Any = None, space_mod: Any = None) -> None:
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
        # light scenes (README S5.24): the catalogue, resolved at the first configure; the last configure's inputs,
        # so a catalogue change recompiles the scenes on show-brain
        self._scenes_src = light_scenes
        self._scenes_cat: Any = None
        self._scenes_err: Optional[str] = None
        self._configured: Optional[tuple] = None
        self._configuring = False
        # identify and follow (any thread; each swapped by reference, read once per frame)
        self._ident: dict[str, tuple[float, list[float]]] = {}       # fixture -> (until, colour at full value)
        self._follow: dict[str, tuple[float, list[float]]] = {}      # fixture -> (intensity, colour): not following
        self._follow_view: dict[str, Any] = {}                       # fixture -> "off" | {hex, bri}, as accepted
        # drop lines (trigger_hit; any thread)
        self._drops: collections.deque = collections.deque(maxlen=DROP_RING)   # (key, at_s, strength)
        self._drop_lock = threading.Lock()
        self._drop_seen: Any = None                                  # the last LookData.light_scene_drop id taken
        self.drops = 0
        # docs/LIGHTS.md §5: the encoder and the space (None: lazily at configure; False: none), and the holds
        self._enc_src = encoder
        self._space_src = space_mod
        self.modules: dict[str, str] = {}
        self._holds: dict[str, _Hold] = {}                           # swapped whole under _hold_lock
        self._hold_lock = threading.Lock()
        self._hold_seq = itertools.count(1)
        # docs/ENSEMBLE.md §7.2: the gesture ring (swapped whole under _drop_lock), each id's last seq taken, the last
        # LookData.gestures list read, the ensemble's fixture maps (cached on its id and rev) and the counts
        self._gring: tuple[_GPart, ...] = ()
        self._gseq: dict[str, Any] = {}
        self._gsrc: Any = None
        self._ens_cache: Optional[tuple] = None
        self._gstats: dict[str, Any] = {"taken": 0, "replaced": 0, "removed": 0, "shown": 0, "merged": 0, "unknown": 0,
                                        "skipped": 0, "malformed": 0, "by_kind": {}}

    # -- show-brain ---------------------------------------------------------------------------------------------------
    def configure(self, looks: Optional[Mapping[str, Any]], rig: Optional[Mapping[str, Any]], *,
                  lamps: Optional[Mapping[str, Any]] = None, lights: Optional[Mapping[str, Any]] = None,
                  space: Optional[Mapping[str, Any]] = None) -> list[str]:
        """Compile show.yaml ``looks`` against the registry into a Program (swapped by reference). Returns the problem
        lines (page words); a layer, the hits block or a fixture that can't be used as written is left out, the rest
        runs. ``lamps`` (show.yaml's, optional) lets ``role:`` selectors and the allocation fallback follow placements
        as the director does (fixtures.select's ``lamps``); without it, only written roles count. ``lights`` is
        show.yaml's ``lights`` block (groups, motion, jog: docs/LIGHTS.md §12) and ``space`` the room's SpaceView
        (space.py; None: no positions)."""
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
        lights = lights if isinstance(lights, Mapping) else None
        space = space if isinstance(space, Mapping) else None
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
        motion = self._motion_cfg(lights, probs)
        enc_mod = self._piece("fixture_encode", self._enc_src)
        space_mod = self._piece("space", self._space_src) if space is not None else None

        fixtures = rig.get("fixtures") or {}
        types = rig.get("types") or {}
        infos = {fid: f for fid, f in fixtures.items() if isinstance(f, Mapping)}
        lamp_ids = [fid for fid in sorted(infos) if self._is_lamp(infos[fid], types)]
        groups, group_of = self._groups(lights, rig, lamps, space, space_mod, lamp_ids, probs)
        gidx: dict[str, tuple[int, int]] = {}
        for g in groups.values():
            mem = [m for m in g.get("members") or () if m in group_of]
            for i, m in enumerate(mem):
                gidx[m] = (i, len(mem))

        # outputs and their fixtures (their capabilities first: layers reach only who can show them)
        outputs: dict[str, _OutProg] = {}
        zones: dict[str, Optional[str]] = {}
        slots_of: dict[str, list[_Slot]] = {}
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
                slots.append(self._slot(fid, f, t, infos, types, min_visible, lamps, probs, motion=motion,
                                        enc_mod=enc_mod, space=space, group=group_of.get(fid, COMPAT),
                                        gi=gidx.get(fid, (0, 1))))
            slots_of[oid] = slots
        attrs_of = {s.id: s.attrs for sl in slots_of.values() for s in sl}

        # layers
        select = self._select_fn(rig, lamps, probs)
        raw_layers = looks.get("layers", DEFAULT_LAYERS)
        layers: list[_Layer] = []
        skipped: dict[str, int] = {}
        if not isinstance(raw_layers, (list, tuple)):
            probs.append("looks.layers: not a list: no layers")
            raw_layers = ()
        seen: set[str] = set()
        for n, raw in enumerate(raw_layers):
            L = self._compile_layer(raw, n, seen, select, probs, attrs_of=attrs_of, skipped=skipped)
            if L is not None:
                layers.append(L)

        # hits
        hits = None
        raw_hits = looks.get("hits")
        if raw_hits is not None:
            hits = self._compile_hits(raw_hits, select, probs)

        for oid, oc in (rig.get("outputs") or {}).items():
            if not isinstance(oc, Mapping):
                continue
            slots = slots_of.get(oid, [])
            ma = oc.get("min_attack_ms")
            hz = hits.decay_s * HIT_TAIL_DECAYS if hits else 0.0
            mp = max((s.min_pulse_s for s in slots), default=0.0)
            outputs[oid] = _OutProg(
                id=oid, hits=oc.get("hits") if isinstance(oc.get("hits"), bool) else None,
                min_attack_ms=float(ma) if _isnum(ma) and ma > 0 else None, slots=tuple(slots),
                hit_horizon_s=hz + mp, hit_members=bool(hits is not None and any(s.id in hits.members for s in slots)),
                max_pulse_s=mp, groups=tuple(dict.fromkeys(s.group for s in slots)))
        blob = json.dumps({"looks": looks, "rig": rig.get("revision"), "lights": lights,
                           "space": (space or {}).get("revision")}, sort_keys=True, default=str)
        # light scenes (README S5.24): the catalogue is polled here (show-brain) and compiled against the same rig
        self._configuring = True
        try:
            cat = self._scene_catalogue()
            if cat is not None:
                self._call("light_scenes.poll", cat.poll)
        finally:
            self._configuring = False
        self._configured = (rig, lamps, outputs, groups, attrs_of)
        scenes, sprobs, srev, gsc, gfx, sk = self._compile_scenes(rig, lamps, outputs, groups, attrs_of)
        skipped.update(sk)
        prog = _Program(revision=hashlib.sha1(blob.encode()).hexdigest()[:12], layers=tuple(layers), hits=hits,
                        master=master, middle_depth=middle, flash_hz_max=flash_hz, outputs=outputs, zones=zones,
                        problems=tuple(probs), scenes=scenes, scene_revision=srev, scene_problems=tuple(sprobs),
                        groups=groups, group_scenes=gsc, group_effects=gfx, motion=motion, space=space,
                        space_mod=space_mod, enc_mod=enc_mod, skipped=skipped,
                        fixture_ids=frozenset(s.id for op in outputs.values() for s in op.slots))
        self._program = prog
        return list(probs)

    def _piece(self, name: str, src: Any) -> Any:
        """fixture_encode / space: the one given at construction, else imported now (``modules`` says which)."""
        if src is False:
            self.modules[name] = "off"
            return None
        if src is not None:
            self.modules[name] = "given"
            return src
        mod, why = _module(name)
        self.modules[name] = "ok" if mod is not None else f"missing: {why}"
        return mod

    @staticmethod
    def _is_lamp(f: Mapping[str, Any], types: Mapping[str, Any]) -> bool:
        t = types.get(f.get("type"))
        return isinstance(t, Mapping) and t.get("kind") == LAMP and f.get("kind", LAMP) == LAMP

    def _motion_cfg(self, lights: Optional[Mapping[str, Any]], probs: list[str]) -> _Motion:
        """show.yaml ``lights.motion`` and ``lights.jog`` (§12); a value that doesn't read is its starting point, with
        a problem line. The rest of ``lights`` (groups, flow, programs, calibration) is the director's and space's."""
        if lights is None:
            return _Motion()
        m = lights.get("motion")
        m = m if isinstance(m, Mapping) else {}
        j = lights.get("jog")
        j = j if isinstance(j, Mapping) else {}
        sp = m.get("default_max_speed_deg_s")
        speed = DEFAULT_MAX_SPEED_DEG_S
        if sp is not None:
            if isinstance(sp, Mapping) and all(_isnum(sp.get(k)) and sp.get(k) > 0 for k in ("pan", "tilt")):
                speed = (float(sp["pan"]), float(sp["tilt"]))
            else:
                probs.append(f"lights.motion.default_max_speed_deg_s: {sp!r} isn't {{pan: deg/s, tilt: deg/s}}: "
                             f"{DEFAULT_MAX_SPEED_DEG_S} is used")
        w = "lights.motion"
        return _Motion(
            max_speed=speed,
            accel_s=self._num(m, "accel_s", DEFAULT_ACCEL_S, 0.0, 5.0, w, probs),
            pt_speed=self._num(m, "pt_speed", DEFAULT_PT_SPEED, 0.0, 1.0, w, probs),
            max_travel_share=self._num(m, "max_travel_share", DEFAULT_MAX_TRAVEL_SHARE, 0.0, 1.0, w, probs,
                                       positive=True),
            dark_move_min_deg=self._num(m, "dark_move_min_deg", DEFAULT_DARK_MOVE_MIN_DEG, 0.0, None, w, probs),
            settle_s=self._num(m, "settle_ms", DEFAULT_SETTLE_MS, 0.0, None, w, probs) / 1000.0,
            min_size_share=self._num(m, "min_size_share", DEFAULT_MIN_SIZE_SHARE, 0.0, 1.0, w, probs, positive=True),
            dark_max_s=self._num(m, "dark_max_s", DEFAULT_DARK_MAX_S, 0.0, None, w, probs, positive=True),
            jog_idle_s=self._num(j, "idle_s", DEFAULT_JOG_IDLE_S, 0.0, None, "lights.jog", probs, positive=True),
            jog_smooth_s=self._num(j, "smooth_ms", DEFAULT_JOG_SMOOTH_MS, 0.0, None, "lights.jog", probs) / 1000.0)

    def _groups(self, lights: Optional[Mapping[str, Any]], rig: Mapping[str, Any], lamps: Optional[Mapping[str, Any]],
                space: Optional[Mapping[str, Any]], space_mod: Any, lamp_ids: list[str], probs: list[str]
                ) -> tuple[dict[str, dict], dict[str, str]]:
        """show.yaml ``lights.groups`` -> {group: GroupInfo}, {fixture: group}: light_scenes.group_members (the
        director's own, so membership agrees), else the same rule here: each lamp in the first group (file order)
        whose selector takes it, the rest in ``"*"``."""
        cfg = lights.get("groups") if lights is not None else None
        cfg = cfg if isinstance(cfg, Mapping) else {}
        spatial = None
        if space_mod is not None and space is not None:
            fn = getattr(space_mod, "spatial_fn", None)
            spatial = self._call("space.spatial_fn", fn, space) if callable(fn) else None
        ls, _why = _module("light_scenes")
        gm = getattr(ls, "group_members", None) if ls is not None else None
        got = None
        if callable(gm):
            try:
                got = gm(cfg, rig, lamps=dict(lamps) if lamps else None, spatial=spatial)
            except Exception as e:                               # degrade: the rule below
                probs.append(f"lights.groups: the groups didn't resolve ({type(e).__name__}: {e}): the engine's own "
                             f"rule is used")
                got = None
        lamp_set = set(lamp_ids)
        if not isinstance(got, Mapping):
            got = {}
            taken: set[str] = set()
            for gid, g in cfg.items():
                if not isinstance(g, Mapping):
                    continue
                expr = g.get("select")
                ids: list[str] = []
                if isinstance(expr, str) and self._select is not None:
                    try:
                        kw = {"lamps": dict(lamps) if lamps else None}
                        if spatial is not None:
                            kw["spatial"] = spatial
                        ids = self._select(expr, rig, **kw)
                    except TypeError:                            # a fixtures.select without ``spatial`` yet
                        try:
                            ids = self._select(expr, rig, lamps=dict(lamps) if lamps else None)
                        except Exception:
                            ids = []
                    except Exception:
                        ids = []
                mem = [i for i in ids if i in lamp_set and i not in taken]
                taken.update(mem)
                got[str(gid)] = {"id": str(gid), "label": str(g.get("label") or gid), "members": mem, "caps": [],
                                 "follows": g.get("follows") if isinstance(g.get("follows"), str) else None,
                                 "dark_level": g.get("dark_level", 0)}
            rest = [i for i in lamp_ids if i not in taken]
            if rest or not got:
                got["*"] = {"id": "*", "label": "Other lights", "members": rest, "caps": [], "follows": None,
                            "dark_level": 0}
        groups: dict[str, dict] = {}
        group_of: dict[str, str] = {}
        for gid, g in got.items():
            if not isinstance(g, Mapping):
                continue
            mem = [m for m in (g.get("members") or ()) if m in lamp_set and m not in group_of]
            for m in mem:
                group_of[m] = str(gid)
            groups[str(gid)] = {**dict(g), "members": mem}
        return groups, group_of

    def _select_fn(self, rig: Mapping[str, Any], lamps: Optional[Mapping[str, Any]], probs: list[str]
                   ) -> Callable[[Any, str], Optional[list[str]]]:
        """The selector for one compile: fixtures.select over ``rig``, lamps only (the screens rule); a selector that
        can't be used selects nothing, with a line in ``probs``."""
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
        return select

    # -- light scenes (README S5.24; show-brain) -------------------------------------------------------------------------
    def _scene_catalogue(self) -> Any:
        """The light-scene catalogue (``light_scenes`` at construction; None: the shared one), subscribed once."""
        if self._scenes_cat is not None or self._scenes_src is False:
            return self._scenes_cat
        cat = self._scenes_src
        if cat is None:
            try:
                from . import light_scenes
                cat = light_scenes.shared()
            except Exception as e:                               # degrade: looks.layers only
                self._scenes_err = f"light_scenes.py can't be imported: {type(e).__name__}: {e}"
                self._scenes_src = False
                return None
        sub = getattr(cat, "subscribe", None)
        if callable(sub):
            sub(self._on_scenes)
        self._scenes_cat = cat
        return cat

    def _on_scenes(self, cat: Any) -> None:
        """The catalogue changed (its poller's thread: show-brain): recompile the scenes against the last configure's
        registry and swap them into the program by reference."""
        if self._configuring or self._configured is None:
            return                                               # configure compiles them itself
        rig, lamps, outputs, groups, attrs_of = self._configured
        scenes, sprobs, srev, gsc, gfx, sk = self._compile_scenes(rig, lamps, outputs, groups, attrs_of)
        p = self._program
        self._program = replace(p, scenes=scenes, scene_revision=srev, scene_problems=tuple(sprobs),
                                group_scenes=gsc, group_effects=gfx, skipped={**p.skipped, **sk})

    def _compile_scenes(self, rig: Mapping[str, Any], lamps: Optional[Mapping[str, Any]],
                        outputs: Mapping[str, _OutProg], groups: Mapping[str, Mapping[str, Any]] = None,
                        attrs_of: Optional[Mapping[str, frozenset]] = None
                        ) -> tuple[dict[str, _Scene], list[str], str, dict, dict, dict]:
        """Every catalogue scene as a program part: its layers (ids ``<scene>/<id>``), its hits and its flash cap,
        once over every lamp (the light_scene path) and once per light group (§5.6: members within the group; ids
        ``<scene>/<id>@<group>``), and every light effect per group. A layer or block that can't be used is left out
        with a problem line (the scene's own list, each line once), the rest runs."""
        cat = self._scene_catalogue()
        probs: list[str] = []
        skipped: dict[str, int] = {}
        if cat is None:
            if self._scenes_err:
                probs.append(self._scenes_err)
            return {}, probs, "", {}, {}, skipped
        raw = getattr(cat, "scenes", None)
        raw_fx = getattr(cat, "effects", None)
        raw = raw if isinstance(raw, Mapping) else {}
        raw_fx = raw_fx if isinstance(raw_fx, Mapping) else {}
        select = self._select_fn(rig, lamps, probs)
        out = self._compile_set(raw, select, probs, outputs, attrs_of, skipped, where="light_scenes")
        gsc: dict[str, dict[str, _Scene]] = {}
        gfx: dict[str, dict[str, _Scene]] = {}
        for gid, g in (groups or {}).items():
            mem = set(g.get("members") or ())
            if not mem:
                continue

            def gsel(expr: Any, where: str, _mem: set = mem) -> Optional[list[str]]:
                ids = select(expr, where)
                return None if ids is None else [i for i in ids if i in _mem]
            gp: list[str] = []
            gsc[gid] = self._compile_set(raw, gsel, gp, outputs, attrs_of, {}, where="light_scenes", group=gid)
            gfx[gid] = self._compile_set(raw_fx, gsel, gp, outputs, attrs_of, skipped, where="light_effects",
                                         group=gid, effects=True)
            probs.extend(gp)
        return out, list(dict.fromkeys(probs)), str(getattr(cat, "revision", "") or ""), gsc, gfx, skipped

    def _compile_set(self, raw: Mapping[str, Any], select: Callable, probs: list[str],
                     outputs: Mapping[str, _OutProg], attrs_of: Optional[Mapping[str, frozenset]],
                     skipped: dict[str, int], *, where: str, group: Optional[str] = None,
                     effects: bool = False) -> dict[str, _Scene]:
        out: dict[str, _Scene] = {}
        for name, sc in raw.items():
            if not isinstance(sc, Mapping):
                continue
            w = f"{where}.{name}"
            seen: set[str] = set()
            layers = []
            ns = str(name)
            for n, rl in enumerate(sc.get("layers") or ()):
                L = self._compile_layer(rl, n, seen, select, probs, where=f"{w}.layers", ns=ns, group=group,
                                        attrs_of=attrs_of, skipped=skipped, shutter=effects)
                if L is not None:
                    layers.append(L)
            hits = self._compile_hits(sc["hits"], select, probs, where=f"{w}.hits") \
                if sc.get("hits") is not None else None
            fz = None
            caps = sc.get("caps")
            if isinstance(caps, Mapping):
                for k, v in caps.items():
                    if k != "flash_hz_max":
                        probs.append(f"{w}.caps.{k}: not a cap the look engine has: ignored")
                    elif v is not None and not (_isnum(v) and v > 0):
                        probs.append(f"{w}.caps.flash_hz_max: {v!r} isn't a positive number: looks.caps' is used")
                    elif v is not None:
                        fz = float(v)
            fade = (0.0, 0.0)
            if effects:
                fd = sc.get("fade")
                fd = fd if isinstance(fd, Mapping) else {}
                fi, fo = fd.get("in_beats", 0.0), fd.get("out_beats", 0.0)
                fade = (float(fi) if _isnum(fi) and fi > 0 else 0.0, float(fo) if _isnum(fo) and fo > 0 else 0.0)
            members = {oid: bool(hits is not None and any(s.id in hits.members for s in op.slots))
                       for oid, op in outputs.items()}
            horizon = {oid: (hits.decay_s * HIT_TAIL_DECAYS if hits else 0.0) + op.max_pulse_s
                       for oid, op in outputs.items()}
            am = frozenset(m for L in layers if L.kind != "intensity" for m in L.members)
            lab = sc.get("label")
            out[ns] = _Scene(name=ns, layers=tuple(layers), hits=hits, flash_hz_max=fz, hit_members=members,
                             hit_horizon_s=horizon, label=lab if isinstance(lab, str) else ns, fade=fade, amembers=am)
        return out

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

    def _period_of(self, raw: Mapping[str, Any], phase: str, where: str, probs: list[str], bad: list[str]
                   ) -> Optional[tuple[float, float]]:
        pkey = "period_bars" if phase == "bar" else "period_s" if phase == "time" else None
        for k in ("period_bars", "period_s"):
            if k in raw and k != pkey:
                probs.append(f"{where}.{k}: only read with phase {'bar' if k == 'period_bars' else 'time'}: ignored")
        if pkey is None:
            return None
        pv = raw.get(pkey, 1.0 if pkey == "period_bars" else None)
        if _isnum(pv) and pv > 0:
            return (float(pv), float(pv))
        if isinstance(pv, (list, tuple)) and len(pv) == 2 and all(_isnum(x) and x > 0 for x in pv):
            return (float(pv[0]), float(pv[1]))
        bad.append(f"{pkey} {pv!r} isn't a number above 0 or a [slow, fast] pair")
        return None

    def _compile_osc(self, raw: Mapping[str, Any], where: str, probs: list[str], lid: str,
                     between: tuple[float, float] = (0.0, 1.0)) -> Optional[_Osc]:
        """An oscillator spec standing in for a continuous value (§5.2)."""
        for k in raw:
            if k not in _OSC_KEYS:
                probs.append(f"{where}.{k}: not an oscillator key: ignored")
        bad: list[str] = []
        fx, phase = raw.get("fx", "sine"), raw.get("phase", "bar")
        if fx not in contract.FX_SHAPES:
            bad.append(f"fx {fx!r} isn't one of {', '.join(contract.FX_SHAPES)}")
        if phase not in contract.FX_PHASES:
            bad.append(f"phase {phase!r} isn't one of {', '.join(contract.FX_PHASES)}")
        period = self._period_of(raw, phase, where, probs, bad) if phase in contract.FX_PHASES else None
        sub = raw.get("subdivide", False)
        bw = raw.get("between", list(between))
        if not (isinstance(bw, (list, tuple)) and len(bw) == 2 and all(_isnum(x) for x in bw)):
            bad.append(f"between {bw!r} isn't [a, b]")
        ok, spread = self._input_or_num(raw.get("spread", 0.0), f"{where}.spread", probs)
        order = raw.get("order", "group")
        if order not in ORDERS:
            bad.append(f"order {order!r} isn't one of {', '.join(ORDERS)}")
        if bad or not ok:
            probs.append(f"{where}: {'; '.join(bad) or 'spread'}: left out")
            return None
        L = _Layer(id=lid, fx=fx, phase=phase, subdivide=bool(sub), period=period, depth=1.0, depth_scale=1.0,
                   spread=spread, needs="none", without_beat="none", attack_s=HUE_ATTACK_S, decay_s=HUE_DECAY_S,
                   members={}, order=order)
        return _Osc(layer=L, lo=float(bw[0]), hi=float(bw[1]))

    def _value(self, v: Any, where: str, probs: list[str], lid: str, *, words: tuple[str, ...] = (),
               lo: Optional[float] = None, hi: Optional[float] = None, inputs: bool = False) -> tuple[bool, Any]:
        """A continuous value: a number (``lo``..``hi``), one of ``words``, a LOOK_INPUTS word (``inputs``), or an
        oscillator spec. (False, None) with a problem line otherwise."""
        if isinstance(v, str) and (v in words or (inputs and v in contract.LOOK_INPUTS)):
            return True, v
        if _isnum(v) and (lo is None or v >= lo) and (hi is None or v <= hi):
            return True, float(v)
        if isinstance(v, Mapping):
            o = self._compile_osc(v, where, probs, lid)
            return (o is not None), o
        allowed = [f"a number{f' in {lo:g}..{hi:g}' if lo is not None and hi is not None else ''}"] + \
            [repr(w) for w in words] + (["a look input"] if inputs else []) + ["an oscillator"]
        probs.append(f"{where}: {v!r} isn't {' or '.join(allowed)}")
        return False, None

    @staticmethod
    def _names(v: Any) -> Optional[tuple[str, ...]]:
        if isinstance(v, str) and v:
            return (v,)
        if isinstance(v, (list, tuple)) and v and all(isinstance(x, str) and x for x in v):
            return tuple(v)
        return None

    def _kind_params(self, kind: str, raw: Mapping[str, Any], where: str, probs: list[str], bad: list[str],
                     lid: str, shutter: bool) -> dict[str, Any]:
        """A non-intensity layer's own keys (§5.2-§5.4), compiled; ``bad`` collects what leaves it out."""
        p: dict[str, Any] = {}
        if kind == "position":
            mode = raw.get("mode")
            if mode not in POSITION_MODES:
                bad.append(f"mode {mode!r} isn't one of {', '.join(POSITION_MODES)}")
                return p
            p["mode"] = mode
            on = raw.get("on")
            if on is not None and not (isinstance(on, str) and on):
                bad.append(f"on {on!r} isn't a Room map name")
            p["on"] = on
            centre = self._names(raw.get("centre", "centre"))
            if centre is None:
                bad.append(f"centre {raw.get('centre')!r} isn't a name or a list of names")
            p["centre"] = centre or ("centre",)
            for k, dflt in (("size", DEFAULT_SIZE_M), ("size_deg", DEFAULT_SIZE_DEG)):
                v = raw.get(k, dflt)
                if not (_isnum(v) and v >= 0):
                    bad.append(f"{k} {v!r} isn't a number >= 0")
                p[k] = float(v) if _isnum(v) else dflt
            along = raw.get("along", "u")
            if along not in ("flow", "u", "v") + FLOW_DIRS:
                bad.append(f"along {along!r} isn't flow, u, v or a flow direction")
            p["along"] = along
            rot = raw.get("rotate", "cw")
            if rot not in ("cw", "ccw", "flow"):
                bad.append(f"rotate {rot!r} isn't cw, ccw or flow")
            p["rotate"] = rot
            ok, p["fan"] = self._value(raw.get("fan", 1.0), f"{where}.fan", probs, f"{lid}#fan", lo=-1.0, hi=1.0)
            if not ok:
                bad.append("fan")
            ease = raw.get("ease", "linear")
            if ease not in EASES:
                bad.append(f"ease {ease!r} isn't one of {', '.join(EASES)}")
            p["ease"] = ease
            pts = raw.get("points")
            p["points"] = self._names(pts) if pts is not None else ()
            if mode == "bounce" and not p["points"]:
                bad.append("a bounce needs points (Room map names)")
            hop = raw.get("hop", "beat")
            if hop not in HOP_ON:
                bad.append(f"hop {hop!r} isn't one of {', '.join(HOP_ON)}")
            p["hop"] = hop
            ok, p["lift"] = self._value(raw.get("lift", DEFAULT_LIFT_M), f"{where}.lift", probs, f"{lid}#lift")
            if not ok:
                bad.append("lift")
            dm = raw.get("dark_move", False)
            if not isinstance(dm, bool):
                bad.append(f"dark_move {dm!r} isn't true or false")
            p["dark_move"] = dm is True
            p["fx"] = raw.get("fx", "sine") if raw.get("fx", "sine") in contract.FX_SHAPES else "sine"
        elif kind == "beam":
            for a, words, lo, hi in (("zoom", ("narrow", "wide", "mid", "default"), 0.0, 180.0),
                                     ("focus", ("default",), 0.0, 1.0), ("iris", ("default",), 0.0, 1.0),
                                     ("frost", ("default",), 0.0, 1.0)):
                if a in raw:
                    ok, p[a] = self._value(raw[a], f"{where}.{a}", probs, f"{lid}#{a}", words=words, lo=lo, hi=hi)
                    if not ok:
                        bad.append(a)
            if not p:
                bad.append("no zoom, focus, iris or frost")
        elif kind == "gobo":
            wheel = raw.get("wheel", "gobo")
            if wheel not in ("gobo", "gobo2"):
                bad.append(f"wheel {wheel!r} isn't gobo or gobo2")
            p["wheel"] = wheel
            slot = raw.get("slot")
            if slot is not None and not (isinstance(slot, (str, int)) and not isinstance(slot, bool)) and \
                    not (isinstance(slot, (list, tuple)) and slot and
                         all(isinstance(x, (str, int)) and not isinstance(x, bool) for x in slot)):
                bad.append(f"slot {slot!r} isn't a name, a number, a list or auto")
            p["slot"] = tuple(slot) if isinstance(slot, (list, tuple)) else slot
            ch = raw.get("change_on", "none")
            if ch not in CHANGE_ON:
                bad.append(f"change_on {ch!r} isn't one of {', '.join(CHANGE_ON)}")
            p["change_on"] = ch
            self._spin_keys(raw, p, where, bad)
            if "shake" in raw:
                ok, p["shake"] = self._value(raw["shake"], f"{where}.shake", probs, f"{lid}#shake", lo=0.0, hi=1.0)
                if not ok:
                    bad.append("shake")
        elif kind == "prism":
            pin = raw.get("in", True)
            if not (isinstance(pin, bool) or pin == "accents"):
                bad.append(f"in {pin!r} isn't true, false or accents")
            p["in"] = pin
            self._spin_keys(raw, p, where, bad)
        elif kind == "colour":
            role = raw.get("role")
            if role not in ROLE_WORDS:
                bad.append(f"a colour layer needs a role ({', '.join(ROLE_WORDS)}), not {role!r}")
            p["role"] = role
        elif kind == "colour_wheel":
            mode = raw.get("mode", "follow")
            if mode not in ("follow", "step", "spin"):
                bad.append(f"mode {mode!r} isn't follow, step or spin")
            p["mode"] = mode
            so = raw.get("step_on", "bar")
            if so not in HOP_ON:
                bad.append(f"step_on {so!r} isn't one of {', '.join(HOP_ON)}")
            p["step_on"] = so
            self._spin_keys(raw, p, where, bad)
        elif kind == "cct":
            k = raw.get("k")
            if not ((_isnum(k) and k > 0) or k == "key_cct"):
                bad.append(f"k {k!r} isn't kelvin or key_cct")
            p["k"] = k
        elif kind == "shutter":
            if not shutter:
                bad.append("a shutter layer belongs in an effect (strobe only lives there)")
            mode = raw.get("mode", "strobe")
            if mode not in SHUTTER_LAYER_MODES:
                bad.append(f"mode {mode!r} isn't one of {', '.join(SHUTTER_LAYER_MODES)}")
            p["mode"] = mode
            hz = raw.get("hz")
            if _isnum(hz) and hz > 0:
                p["hz"] = (float(hz), float(hz))
            elif isinstance(hz, (list, tuple)) and len(hz) == 2 and all(_isnum(x) and x > 0 for x in hz):
                p["hz"] = (float(hz[0]), float(hz[1]))
            else:
                bad.append(f"hz {hz!r} isn't a number above 0 or [lo, hi]")
        return p

    @staticmethod
    def _spin_keys(raw: Mapping[str, Any], p: dict, where: str, bad: list[str]) -> None:
        spin = raw.get("spin")
        if spin is not None and not ((_isnum(spin) and -1.0 <= spin <= 1.0) or spin in ("flow", "pace")):
            bad.append(f"spin {spin!r} isn't -1..1, flow or pace")
        p["spin"] = float(spin) if _isnum(spin) else spin
        sc = raw.get("spin_scale", 1.0)
        if not _isnum(sc):
            bad.append(f"spin_scale {sc!r} isn't a number")
        p["spin_scale"] = float(sc) if _isnum(sc) else 1.0

    def _compile_layer(self, raw: Any, n: int, seen: set[str], select: Callable, probs: list[str], *,
                       where: str = "looks.layers", ns: str = "", group: Optional[str] = None,
                       attrs_of: Optional[Mapping[str, frozenset]] = None, skipped: Optional[dict] = None,
                       shutter: bool = False) -> Optional[_Layer]:
        """One layer entry (``where`` names its list in problem lines; ``ns``, a light scene's name, prefixes its id
        so its glides and fades are its own; ``group``: compiled within that light group, its id suffixed)."""
        base = where
        if not isinstance(raw, Mapping):
            probs.append(f"{base}[{n}]: not a mapping: left out")
            return None
        lid = raw.get("id")
        if not isinstance(lid, str) or not lid:
            probs.append(f"{base}[{n}]: no id: left out")
            return None
        where = f"{base}.{lid}"
        if lid in seen:
            probs.append(f"{where}: the id is used twice: left out")
            return None
        seen.add(lid)
        kind = raw.get("kind", raw.get("attr", "intensity"))
        if kind not in LAYER_KINDS:
            probs.append(f"{where}: kind {kind!r} isn't one of {', '.join(LAYER_KINDS)}: left out")
            return None
        allowed = _LAYER_KEYS | _KIND_KEYS[kind]
        for k in raw:
            if k not in allowed:
                probs.append(f"{where}.{k}: not a layer key: ignored")
        en = raw.get("enabled", True)
        if not isinstance(en, bool):
            probs.append(f"{where}.enabled: {en!r} isn't true or false: off")
            return None
        if not en:
            return None
        full = f"{ns}/{lid}" if ns else lid
        if group is not None:
            full = f"{full}@{group}"
        bad = []
        intensity = kind == "intensity"
        fx = raw.get("fx", None if intensity else "sine")
        phase = raw.get("phase", None if intensity else "bar")
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
        period = self._period_of(raw, phase, where, probs, bad) if phase in contract.FX_PHASES else None
        if intensity:
            if "depth" not in raw:
                bad.append("no depth")
                depth = None
            else:
                ok, depth = self._input_or_num(raw.get("depth"), f"{where}.depth", probs)
                if not ok:
                    bad.append("depth")
        else:
            ok, depth = self._value(raw.get("depth", 1.0), f"{where}.depth", probs, f"{full}#depth", lo=0.0, hi=1.0,
                                    inputs=True)
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
        order = raw.get("order", "group")
        if order not in ORDERS:
            bad.append(f"order {order!r} isn't one of {', '.join(ORDERS)}")
        sm = raw.get("spread_mode", "chase")
        if sm not in SPREAD_MODES:
            bad.append(f"spread_mode {sm!r} isn't one of {', '.join(SPREAD_MODES)}")
        fd = raw.get("flow_dir")
        if fd is not None and fd not in FLOW_DIRS:
            bad.append(f"flow_dir {fd!r} isn't one of {', '.join(FLOW_DIRS)}")
        p = {} if intensity else self._kind_params(kind, raw, where, probs, bad, full, shutter)
        if bad:
            probs.append(f"{where}: {'; '.join(b for b in bad if b not in _MARKERS) or 'bad value'}: left out")
            return None
        ids = select(raw.get("select", "*"), f"{where}.select") or []
        if not intensity and attrs_of is not None:
            need = KIND_ATTRS.get(kind)
            if kind == "gobo":
                need = (p.get("wheel", "gobo"),)
            elif kind == "beam":
                need = tuple(a for a in KIND_ATTRS["beam"] if a in p)
            able = [i for i in ids if any(a in attrs_of.get(i, ()) for a in need or ())]
            if kind == "position":
                able = [i for i in able if "tilt" in attrs_of.get(i, ())]
            if skipped is not None and len(able) < len(ids):
                skipped[full] = len(ids) - len(able)
            ids = able
        members = {fid: (i, len(ids)) for i, fid in enumerate(ids)}
        return _Layer(id=full, fx=fx, phase=phase, subdivide=bool(sub), period=period, depth=depth,
                      depth_scale=float(ds), spread=spread, needs=needs, without_beat=wob,
                      attack_s=float(att) / 1000.0, decay_s=float(dec) / 1000.0, members=members, kind=kind,
                      order=order, spread_mode=sm, flow_dir=fd, p=p, ids=tuple(ids))

    def _compile_hits(self, raw: Any, select: Callable, probs: list[str], *, where: str = "looks.hits"
                      ) -> Optional[_Hits]:
        if not isinstance(raw, Mapping):
            probs.append(f"{where}: not a mapping: no hits")
            return None
        for k in raw:
            if k not in _HIT_KEYS:
                probs.append(f"{where}.{k}: not a hits key: ignored")
        styles_raw = raw.get("styles")
        if not isinstance(styles_raw, (list, tuple)):
            probs.append(f"{where}.styles: not a list of accent styles: no hits")
            return None
        styles = set()
        for s in styles_raw:
            if s in contract.ACCENT_STYLES:
                styles.add(s)
            else:
                probs.append(f"{where}.styles: {s!r} isn't one of {', '.join(contract.ACCENT_STYLES)}: dropped")
        bands = None
        if raw.get("bands") is not None:
            if isinstance(raw["bands"], (list, tuple)):
                bands = set()
                for b in raw["bands"]:
                    if b in contract.BAND_NAMES:
                        bands.add(b)
                    else:
                        probs.append(f"{where}.bands: {b!r} isn't one of {', '.join(contract.BAND_NAMES)}: dropped")
            else:
                probs.append(f"{where}.bands: not a list: every band")
        ms = self._num(raw, "min_strength", DEFAULT_HIT_MIN_STRENGTH, 0.0, 1.0, where, probs)
        depth = self._num(raw, "depth", DEFAULT_HIT_DEPTH, 0.0, 1.0, where, probs)
        dec = self._num(raw, "decay_ms", DEFAULT_HIT_DECAY_MS, 0.0, None, where, probs, positive=True)
        drop = None
        rd = raw.get("drop")
        if rd is not None:                               # README S5.24: a flash at each drop line
            if not isinstance(rd, Mapping):
                probs.append(f"{where}.drop: not a mapping: no drop flash")
            else:
                for k in rd:
                    if k not in _DROP_KEYS:
                        probs.append(f"{where}.drop.{k}: not a drop key: ignored")
                dd = self._num(rd, "depth", DEFAULT_DROP_DEPTH, 0.0, 1.0, f"{where}.drop", probs)
                ddec = self._num(rd, "decay_ms", DEFAULT_HIT_DECAY_MS, 0.0, None, f"{where}.drop", probs,
                                 positive=True)
                drop = (dd, ddec / 1000.0)
        ids = select(raw.get("select", "*"), f"{where}.select") or []
        return _Hits(members=frozenset(ids), styles=frozenset(styles),
                     bands=frozenset(bands) if bands is not None else None, min_strength=ms, depth=depth,
                     decay_s=dec / 1000.0, drop=drop)

    def _slot(self, fid: str, f: Mapping[str, Any], t: Mapping[str, Any], infos: Mapping[str, Mapping],
              types: Mapping[str, Mapping], min_visible_ms: float, lamps: Optional[Mapping[str, Any]],
              probs: list[str], *, motion: _Motion = _Motion(), enc_mod: Any = None,
              space: Optional[Mapping[str, Any]] = None, group: str = COMPAT, gi: tuple[int, int] = (0, 1)) -> _Slot:
        lat = f.get("latency") if isinstance(f.get("latency"), Mapping) else {}
        rise = float(lat["rise_ms"]) if _isnum(lat.get("rise_ms")) else 0.0
        col = float(lat["colour_ms"]) if _isnum(lat.get("colour_ms")) else rise
        mp = float(lat["min_pulse_ms"]) if _isnum(lat.get("min_pulse_ms")) else min_visible_ms
        pos_ms = float(lat["position_ms"]) if _isnum(lat.get("position_ms")) else 0.0
        fall = float(lat["fall_ms"]) if _isnum(lat.get("fall_ms")) else 0.0
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
        # docs/LIGHTS.md §5.1: capabilities, setup, place in the room
        setup = f.get("setup") if isinstance(f.get("setup"), Mapping) else {}
        caps = None
        if enc_mod is not None and chans:
            fn = getattr(enc_mod, "caps", None)
            got = self._call("fixture_encode.caps", fn, t, setup) if callable(fn) else None
            caps = got if isinstance(got, Mapping) else None
        attrs = frozenset(caps.get("attrs") or ()) if caps is not None else frozenset(t.get("attrs") or ())
        enc = caps is not None and bool(attrs - ENC_PLAIN) and callable(getattr(enc_mod, "encode", None))
        moves = enc and {"pan", "tilt"} <= attrs
        rng = self._range(caps, setup)
        lim = self._limits(caps, setup, rng)
        home = setup.get("home") if isinstance(setup.get("home"), Mapping) else {}
        hp = _clamp(float(home["pan"]), rng[0], rng[1]) if _isnum(home.get("pan")) else _clamp(0.0, rng[0], rng[1])
        ht = _clamp(float(home["tilt"]), rng[2], rng[3]) if _isnum(home.get("tilt")) else _clamp(0.0, rng[2], rng[3])
        ms = setup.get("max_speed") if isinstance(setup.get("max_speed"), Mapping) else {}
        speed = (float(ms["pan"]) if _isnum(ms.get("pan")) and ms["pan"] > 0 else motion.max_speed[0],
                 float(ms["tilt"]) if _isnum(ms.get("tilt")) and ms["tilt"] > 0 else motion.max_speed[1])
        positions: dict[str, tuple[float, float]] = {}
        for name, pv in (setup.get("positions") or {}).items() if isinstance(setup.get("positions"), Mapping) else ():
            if isinstance(pv, Mapping) and _isnum(pv.get("pan")) and _isnum(pv.get("tilt")):
                positions[str(name)] = (float(pv["pan"]), float(pv["tilt"]))
        neutral: dict[str, Any] = {}
        if enc:
            fn = getattr(enc_mod, "neutral", None)
            got = self._call("fixture_encode.neutral", fn, caps, setup) if callable(fn) else None
            neutral = dict(got) if isinstance(got, Mapping) else self._neutral(caps, setup, attrs)
            for k in ("intensity", "colour"):
                neutral.pop(k, None)
            if moves:
                neutral["pan"], neutral["tilt"] = hp, ht
            if "pt_speed" in attrs:
                neutral["pt_speed"] = motion.pt_speed
        pos, pose = None, None
        if space is not None:
            ap = (space.get("appliances") or {}).get(fid)
            if isinstance(ap, Mapping):
                pose = ap.get("pose") if isinstance(ap.get("pose"), Mapping) and \
                    _vec3(ap["pose"].get("at")) is not None else None
                pos = _vec3(pose.get("at")) if pose is not None else _vec3(ap.get("at"))
        hrgb = house_rgb or BLACK
        hi = max(hrgb)
        hcol = [c / hi for c in hrgb] if hi > 0 else [0.0, 0.0, 0.0]
        hdmx = None
        if chans:
            hdmx = colour_mix.encode(f, t, hi, hcol)
            if enc:
                got = self._call("fixture_encode.encode", enc_mod.encode, f, t, hi, hcol, dict(neutral))
                hdmx = got if isinstance(got, (bytes, bytearray)) else hdmx
        return _Slot(id=fid, info=f, type=t, rise_s=rise / 1000.0, colour_s=col / 1000.0, min_pulse_s=mp / 1000.0,
                     role=role if isinstance(role, str) else None, screen=f.get("screen"), zone=f.get("zone"),
                     house_rgb=house_rgb, donors=tuple(d[-1] for d in donors), channels=chans,
                     house=(hi, hcol, list(hrgb), bytes(hdmx) if hdmx is not None else None), caps=caps, attrs=attrs,
                     setup=setup, pos=pos, pose=pose, pos_s=pos_ms / 1000.0, fall_s=fall / 1000.0, group=group,
                     gi=gi, enc=enc, moves=moves, max_speed=speed, home=(hp, ht), range=rng, limits=lim,
                     positions=positions, neutral=neutral,
                     aimkw={"pan_range": (rng[0], rng[1]), "tilt_range": (rng[2], rng[3]),
                            "limits": {"pan": [lim[0], lim[1]], "tilt": [lim[2], lim[3]]}, "speed": speed})

    @staticmethod
    def _range(caps: Optional[Mapping[str, Any]], setup: Mapping[str, Any]) -> tuple[float, float, float, float]:
        """The full mechanical range (pan lo, hi, tilt lo, hi, centred degrees): the caps', else setup ``range``,
        else the encoder's unknown-range starting points (540° and 270°)."""
        out = []
        sr = setup.get("range") if isinstance(setup.get("range"), Mapping) else {}
        for ax, unknown in (("pan", 540.0), ("tilt", 270.0)):
            c = caps.get(ax) if isinstance(caps, Mapping) and isinstance(caps.get(ax), Mapping) else None
            if c is not None and _isnum(c.get("min")) and _isnum(c.get("max")) and c["max"] > c["min"]:
                out += [float(c["min"]), float(c["max"])]
            else:
                span = float(sr[ax]) if _isnum(sr.get(ax)) and sr[ax] > 0 else unknown
                out += [-span / 2.0, span / 2.0]
        return (out[0], out[1], out[2], out[3])

    @staticmethod
    def _limits(caps: Optional[Mapping[str, Any]], setup: Mapping[str, Any],
                rng: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        """The range within the owner's soft limits (setup ``limits``, or the caps' copy of them; none by default)."""
        sl = setup.get("limits") if isinstance(setup.get("limits"), Mapping) else {}
        out = list(rng)
        for k, ax in ((0, "pan"), (2, "tilt")):
            v = sl.get(ax)
            c = caps.get(ax) if isinstance(caps, Mapping) and isinstance(caps.get(ax), Mapping) else None
            if v is None and c is not None:
                v = c.get("limits")
            if isinstance(v, (list, tuple)) and len(v) == 2 and all(_isnum(x) for x in v):
                lo, hi = sorted((float(v[0]), float(v[1])))
                out[k], out[k + 1] = max(rng[k], lo), min(rng[k + 1], hi)
                if out[k] > out[k + 1]:
                    out[k], out[k + 1] = rng[k], rng[k + 1]
        return (out[0], out[1], out[2], out[3])

    @staticmethod
    def _neutral(caps: Mapping[str, Any], setup: Mapping[str, Any], attrs: frozenset) -> dict[str, Any]:
        """Every attr at rest when fixture_encode gives no ``neutral``: §2.1's "when nothing sets it" column."""
        beam = setup.get("beam") if isinstance(setup.get("beam"), Mapping) else {}
        z = caps.get("zoom") if isinstance(caps.get("zoom"), Mapping) else {}
        mid = (z["min"] + z["max"]) / 2.0 if _isnum(z.get("min")) and _isnum(z.get("max")) else None
        rest = {"zoom": beam.get("zoom", mid), "focus": beam.get("focus", 0.5), "iris": beam.get("iris", 1.0),
                "frost": beam.get("frost", 0.0), "shutter": {"mode": "open"}, "colour_slot": None, "cct": None,
                "gobo": None, "gobo2": None, "prism": 0}
        out = {k: v for k, v in rest.items() if k in attrs}
        for k in ("colour_spin", "pan_spin", "tilt_spin", "gobo_spin", "gobo2_spin", "gobo_shake", "gobo2_shake",
                  "gobo_scroll", "prism_spin"):
            if k in attrs:
                out[k] = 0.0
        return out

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

    def trigger_hit(self, at_s: float, strength: float = 1.0, key: Any = None) -> bool:
        """A drop line at ``at_s`` (monotonic s, the runners' clock; ahead or just passed): the running light scene's
        drop flash (its hits block's ``drop``) shows from it on every output that takes hits (README S5.24). ``key``
        (LookData.light_scene_drop's id) takes each line once. False when it was already taken or doesn't read."""
        try:
            at, s = float(at_s), float(strength)
        except (TypeError, ValueError):
            return False
        if not (math.isfinite(at) and math.isfinite(s)):
            return False
        with self._drop_lock:
            if key is not None and any(k == key for k, _a, _s in self._drops):
                return False
            self._drops.append((key, at, _c01(s)))
            self.drops += 1
        return True

    def _take_drop(self, dl: Any) -> None:
        """LookData.light_scene_drop (read each frame by every runner): a new id goes to ``trigger_hit`` once."""
        if not isinstance(dl, Mapping):
            return
        key = dl.get("id")
        if key is None or key == self._drop_seen:
            return
        self._drop_seen = key
        at, s = dl.get("at_ms"), dl.get("strength", 1.0)
        if _isnum(at):
            self.trigger_hit(at / 1000.0, s if _isnum(s) else 1.0, key=key)

    # -- the ensemble's gestures (docs/ENSEMBLE.md §7.2; any thread) ------------------------------------------------
    def take_gesture(self, g: Mapping[str, Any]) -> bool:
        """A room.gesture body (contract.GestureData) onto the gesture ring, under the drop lock: each part resolved
        to the fixtures its player holds (the look's ``ensemble`` members; a player id that is a fixture id is that
        fixture). A re-sent id replaces its parts and ``parts: []`` calls it off; the same or an older seq of an id
        already taken changes nothing. False for a body that doesn't read (counted ``malformed``) or a kind that isn't
        contract.GESTURE_KINDS (skipped, counted); a part whose player the ensemble doesn't name is skipped and
        counted ``unknown``, never raised."""
        look = self._call("look", self.look_fn)
        return self._take(g, self._ens_view(look if isinstance(look, Mapping) else None))

    def _take(self, g: Any, ev: Optional[_EnsView]) -> bool:
        gs = self._gstats
        got = self._read_gesture(g, ev)
        if got is None:
            with self._drop_lock:
                gs["malformed"] += 1
            return False
        gid, seq, kind, parts, unknown = got
        known = kind in GESTURE_KINDS
        with self._drop_lock:
            seen = gid in self._gseq
            last = self._gseq.get(gid)
            if seen and (seq == last or (_isnum(seq) and _isnum(last) and seq < last)):
                return known                                     # taken already, or an older copy
            self._gseq.pop(gid, None)
            self._gseq[gid] = seq
            if len(self._gseq) > GESTURE_SEEN:
                self._gseq.pop(next(iter(self._gseq)))
            if not known:
                gs["skipped"] += 1
                return False
            gs["unknown"] += unknown
            now = self._mono()
            ring = [p for p in self._gring if p.gid != gid and p.end_s >= now] + parts
            self._gring = tuple(ring[-GESTURE_RING:])            # the oldest out first
            if not seen:
                gs["taken"] += 1
                bk = gs["by_kind"]
                bk[kind] = bk.get(kind, 0) + 1
            elif parts:
                gs["replaced"] += 1
            else:
                gs["removed"] += 1
        return True

    def _read_gesture(self, g: Any, ev: Optional[_EnsView]) -> Optional[tuple]:
        """(id, seq, kind, [_GPart], unknown players) from a GestureData body; None when it doesn't read."""
        if not isinstance(g, Mapping):
            return None
        gid, raw = g.get("id"), g.get("parts")
        if not isinstance(gid, str) or not gid or not isinstance(raw, (list, tuple)):
            return None
        seq, kind = g.get("seq"), g.get("kind")
        shape = g.get("shape")
        if raw and not (isinstance(shape, Mapping) and all(_isnum(shape.get(k)) and shape[k] >= 0
                                                            for k in _SHAPE_KEYS)
                        and all(shape.get(k) is None or (_isnum(shape[k]) and shape[k] >= 0)
                                for k in ("colour_depth", "kick"))):
            return None
        dur = g.get("dur_ms")
        dur_s = float(dur) / 1000.0 if _isnum(dur) and dur > 0 else 0.0
        fids = self._program.fixture_ids
        parts: list[_GPart] = []
        unknown = 0
        for p in raw:
            if not isinstance(p, Mapping):
                return None
            pid, at, s = p.get("player"), p.get("at_ms"), p.get("strength")
            col = p.get("colour")
            if not isinstance(pid, str) or not _isnum(at) or not _isnum(s) or not (col is None or isinstance(col, str)):
                return None
            mem = None
            if ev is not None:
                pl = ev.cur.get(pid)
                if not isinstance(pl, Mapping) and ev.nxt is not None:
                    pl = ev.nxt.get(pid)
                if isinstance(pl, Mapping):
                    mem = frozenset(m for m in pl.get("members") or () if isinstance(m, str))
            if mem is None:
                if pid not in fids:
                    unknown += 1                                 # not a player the ensemble names: skipped
                    continue
                mem = frozenset((pid,))
            mem &= fids                                          # a screen or a device: nothing here to light
            if mem and s > 0.0:
                parts.append(_GPart(gid, str(kind), float(at) / 1000.0, _c01(float(s)), dict(shape),
                                    p.get("predicted") is True, col, dur_s, mem))
        return gid, seq, kind, parts, unknown

    def _take_gestures(self, look: Mapping[str, Any]) -> None:
        """LookData.gestures (every runner, once a frame): each (id, seq) not yet taken goes to the ring, once."""
        gl = look.get("gestures")
        if gl is self._gsrc or not isinstance(gl, (list, tuple)):
            return
        self._gsrc = gl
        seen = self._gseq
        new = []
        for g in gl:
            gid = g.get("id") if isinstance(g, Mapping) else None
            last = seen.get(gid, _UNSEEN) if isinstance(gid, str) else _UNSEEN
            if last is not _UNSEEN:
                seq = g.get("seq")
                if seq == last or not (_isnum(seq) and _isnum(last) and seq > last):
                    continue
            new.append(g)
        if new:
            ev = self._ens_view(look)
            for g in new:
                self._take(g, ev)

    def _ens_view(self, look: Optional[Mapping[str, Any]]) -> Optional[_EnsView]:
        """LookData.ensemble for this frame, or None (no ensemble: everything exactly as before). The fixture -> player
        maps are cached on the ensemble's (id, rev)."""
        ens = look.get("ensemble") if look is not None else None
        if not isinstance(ens, Mapping) or not isinstance(ens.get("players"), Mapping):
            return None
        cur = ens["players"]
        nx = ens.get("next") if isinstance(ens.get("next"), Mapping) else None
        nat = nx.get("at_ms") if nx is not None else None
        nxt = nx.get("players") if nx is not None and _isnum(nat) and isinstance(nx.get("players"), Mapping) else None
        i, r = ens.get("id"), ens.get("rev")
        key = (i, r) if _isnum(i) and _isnum(r) else (id(cur), id(nxt))
        c = self._ens_cache
        if c is None or c[0] != key:
            c = self._ens_cache = (key, self._members(cur), self._members(nxt) if nxt is not None else {})
        ev = _EnsView()
        ev.cur, ev.nxt, ev.fcur, ev.fnxt = cur, nxt, c[1], c[2]
        ev.next_at_s = float(nat) / 1000.0 if nxt is not None else math.inf
        ev.ats = tuple(float(a) for a in (ens.get("at_ms"), nat if nxt is not None else None) if _isnum(a))
        gm = ens.get("glide_ms")
        ev.glide_s = float(gm) / 1000.0 if _isnum(gm) and gm > 0 else 0.0
        return ev

    @staticmethod
    def _members(players: Mapping[str, Any]) -> dict[str, str]:
        """fixture id -> the player whose ``members`` hold it (the first, by the players' order)."""
        out: dict[str, str] = {}
        for pid, p in players.items():
            if isinstance(p, Mapping):
                for m in p.get("members") or ():
                    if isinstance(m, str):
                        out.setdefault(m, str(pid))
        return out

    def identify(self, fixture_id: str, seconds: float, rgb: Sequence[float] = (255, 255, 255)) -> bool:
        """That fixture at full ``rgb`` (0..255 each; white) for ``seconds`` from now, whatever it was doing: layers,
        hits, master, caps and its follow value are bypassed, inactive frames included. ``seconds`` <= 0 ends it.
        True when the engine evaluates that fixture now (a lamp in the program; the request is kept either way, so a
        fixture compiled a moment later still lights)."""
        try:
            secs = float(seconds)
            r, g, b = (float(x) for x in rgb)
        except (TypeError, ValueError):
            return False
        if not (math.isfinite(secs) and all(math.isfinite(x) for x in (r, g, b))):
            return False
        now = self._mono()
        new = {k: v for k, v in self._ident.items() if v[0] > now and k != fixture_id}
        if secs > 0.0:
            m = max(r, g, b)
            col = [_c01(r / m), _c01(g / m), _c01(b / m)] if m > 0 else [0.0, 0.0, 0.0]
            new[str(fixture_id)] = (now + secs, col)
        self._ident = new
        return any(s.id == fixture_id for op in self._program.outputs.values() for s in op.slots)

    def set_follow(self, mapping: Mapping[str, Any], *, merge: bool = False) -> dict[str, Any]:
        """Who follows the music (room's DMX-lights adapter, through outputs.Rig, which hands its whole mapping):
        {fixture id: "dmx" (follows the look) | "off" (dark) | {"hex": "#rrggbb", "bri": 0..100} (that colour,
        steady)}. An id not in it follows (so nothing changes until room sends one); ``merge``: only the ids given
        change. Returns what was accepted ("dmx" included); a value that doesn't read is left out."""
        if not isinstance(mapping, Mapping):
            return {}
        cur = dict(self._follow) if merge else {}
        view = dict(self._follow_view) if merge else {}
        accepted: dict[str, Any] = {}
        for fid, v in mapping.items():
            fid = str(fid)
            if v == "dmx":
                cur.pop(fid, None)
                view.pop(fid, None)
                accepted[fid] = "dmx"
            elif v == "off":
                cur[fid] = (0.0, [0.0, 0.0, 0.0])
                view[fid] = accepted[fid] = "off"
            elif isinstance(v, Mapping):
                c = _hex_rgb(v.get("hex"))
                bri = v.get("bri", 100)
                if c is None or not _isnum(bri):
                    continue
                k = _c01(float(bri) / 100.0)
                m = max(c)
                cur[fid] = (k * m, [x / m for x in c] if m > 0 else [0.0, 0.0, 0.0])
                h = "#" + "".join(f"{round(x * 255):02x}" for x in c)
                view[fid] = accepted[fid] = {"hex": h, "bri": round(k * 100.0, 1)}
        self._follow, self._follow_view = cur, view
        return accepted

    def follow(self) -> dict[str, Any]:
        """The fixtures not following the music, as accepted ({id: "off" | {hex, bri}}); every other id follows."""
        return dict(self._follow_view)

    # -- the hold (docs/LIGHTS.md §5.7; any thread) -------------------------------------------------------------------
    def _slot_of(self, fid: str) -> Optional[_Slot]:
        for op in self._program.outputs.values():
            for s in op.slots:
                if s.id == fid:
                    return s
        return None

    def caps_of(self, fixture_id: str) -> Optional[dict]:
        """The fixture's capabilities (fixture_encode.caps, setup applied) as the engine compiled them; None for a
        fixture it doesn't evaluate or without the encoder."""
        s = self._slot_of(fixture_id)
        return dict(s.caps) if s is not None and s.caps is not None else None

    def hold(self, fixture_id: str, values: Mapping[str, Any], *, idle_s: Optional[float] = None,
             smooth_ms: Optional[float] = None, free: bool = False) -> dict:
        """Merge ``values`` (attr -> value, contract.ATTRS) into the fixture's held target (§5.7): the first call
        starts from where it is now with intensity 1 and the shutter open unless the call sets them. Values are
        clamped to the light's range (and its soft limits unless ``free``); an attr it can't show, or a value that
        doesn't read, is listed in ``unknown`` and ignored. Released by ``release`` or ``idle_s`` after the last
        call (show.yaml lights.jog's starting points when unset). Returns {held, until_s, target, now, unknown,
        why}."""
        if not isinstance(values, Mapping):
            raise ValueError("bad_request: values must be {attr: value}")
        mc = self._program.motion
        idle = float(idle_s) if _isnum(idle_s) and idle_s > 0 else mc.jog_idle_s
        smooth = float(smooth_ms) / 1000.0 if _isnum(smooth_ms) and smooth_ms >= 0 else mc.jog_smooth_s
        s = self._slot_of(fixture_id)
        allowed = (s.attrs if s is not None else frozenset()) | {"intensity", "colour"}
        target: dict[str, Any] = {}
        unknown: list[str] = []
        for k, v in values.items():
            k = str(k)
            got = self._clamp_attr(s, k, v, bool(free)) if k in allowed or (k == "macro" and s is not None and
                                                                             s.enc) else None
            if got is None:
                unknown.append(k)
            else:
                target[k] = got[0]
        now = self._mono()
        with self._hold_lock:
            holds = {k: h for k, h in self._holds.items() if h.until_s > now}
            old = holds.get(fixture_id)
            merged = dict(old.target) if old is not None else {"intensity": 1.0}
            if old is None and "shutter" in allowed:
                merged["shutter"] = {"mode": "open"}
            merged.update(target)
            h = _Hold(merged, now + idle, bool(free), smooth, old.seq if old is not None else next(self._hold_seq))
            holds[fixture_id] = h
            self._holds = holds
        fs = self._fs_any(fixture_id)
        why = None if s is not None else "The show doesn't drive this light yet, so it can't be moved from here."
        return {"held": s is not None, "until_s": h.until_s, "target": dict(merged),
                "now": dict(fs.hold_now) if fs is not None and fs.hold_now else {}, "unknown": unknown, "why": why}

    def release(self, fixture_id: str) -> bool:
        """End the fixture's hold now: it glides back to its program through the motion stage. True when it was
        held."""
        with self._hold_lock:
            if fixture_id not in self._holds:
                return False
            self._holds = {k: h for k, h in self._holds.items() if k != fixture_id}
        return True

    def holds(self) -> dict[str, dict]:
        """{fixture: contract.HoldView} for every live hold."""
        now = self._mono()
        out = {}
        for k, h in self._holds.items():
            if h.until_s > now:
                fs = self._fs_any(k)
                out[k] = {"target": dict(h.target), "now": dict(fs.hold_now) if fs is not None and fs.hold_now
                          else {}, "until_s": h.until_s, "free": h.free}
        return out

    def _fs_any(self, fid: str) -> Optional[_FixState]:
        for st in list(self._outs.values()):
            fs = st.fix.get(fid)
            if fs is not None:
                return fs
        return None

    def _clamp_attr(self, s: Optional[_Slot], k: str, v: Any, free: bool) -> Optional[tuple[Any]]:
        """One held value, normalised and clamped to what the light can do: (value,), or None when it doesn't read."""
        caps = s.caps if s is not None and s.caps is not None else {}
        if k in ("pan", "tilt"):
            if not _isnum(v) or s is None:
                return None
            lo, hi = (s.range[0], s.range[1]) if k == "pan" else (s.range[2], s.range[3])
            if not free:
                lo, hi = (s.limits[0], s.limits[1]) if k == "pan" else (s.limits[2], s.limits[3])
            return (_clamp(float(v), lo, hi),)
        if k in ("intensity", "focus", "iris", "frost", "pt_speed", "gobo_shake", "gobo2_shake"):
            return (_c01(float(v)),) if _isnum(v) else None
        if k in ("gobo_spin", "gobo2_spin", "gobo_scroll", "prism_spin", "colour_spin", "pan_spin", "tilt_spin"):
            return (_clamp(float(v), -1.0, 1.0),) if _isnum(v) else None
        if k == "zoom":
            if not _isnum(v):
                return None
            z = caps.get("zoom") if isinstance(caps.get("zoom"), Mapping) else {}
            lo = float(z["min"]) if _isnum(z.get("min")) else 0.0
            hi = float(z["max"]) if _isnum(z.get("max")) else 180.0
            return (_clamp(float(v), lo, hi),)
        if k == "cct":
            if v is None:
                return (None,)
            if not _isnum(v):
                return None
            c = caps.get("cct") if isinstance(caps.get("cct"), Mapping) else {}
            lo = float(c["min_k"]) if _isnum(c.get("min_k")) else 1000.0
            hi = float(c["max_k"]) if _isnum(c.get("max_k")) else 20000.0
            return (_clamp(float(v), lo, hi),)
        if k in ("gobo", "gobo2", "colour_slot"):
            if v is None:
                return (None,)
            return (int(v),) if _isnum(v) and int(v) == v and v >= 0 else None
        if k == "prism":
            if isinstance(v, bool):
                return (1 if v else 0,)
            return (int(v),) if _isnum(v) and int(v) == v and v >= 0 else None
        if k == "shutter":
            if isinstance(v, str):
                v = {"mode": v}
            if not isinstance(v, Mapping) or v.get("mode") not in getattr(contract, "SHUTTER_MODES", (
                    "open", "closed", "strobe", "pulse", "random", "ramp", "burst")):
                return None
            hz = v.get("hz")
            mx = s.setup.get("strobe_hz_max") if s is not None else None
            if _isnum(hz) and _isnum(mx) and mx > 0:
                hz = min(float(hz), float(mx))
            return ({"mode": v["mode"], "hz": float(hz) if _isnum(hz) else None},)
        if k == "colour":
            c = _hex_rgb(v) if isinstance(v, str) else _rgb3(v)
            if c is None:
                return None
            m = max(c)
            return ([x / m for x in c] if m > 0 else [0.0, 0.0, 0.0],)
        if k == "macro":
            return (dict(v),) if isinstance(v, Mapping) and all(isinstance(x, str) for x in v.values()) else None
        return None

    def takes_hit(self, d: Mapping[str, Any], output_id: Optional[str] = None) -> bool:
        """Any thread (show-rx, a few µs): would this music.hit flash now? The program's hits block (strength, band),
        the look's accents style, House lights, Lights low and the pre-drop dip, and the clock's gate, as
        ``evaluate`` reads them; with ``output_id``, also whether any of that output's fixtures is in the hits
        selection (README S5.14). outputs.Rig wakes a hit output's runner (a frame pulled forward) only for these, so
        the hits a look or an output doesn't show never knock DMX off its beat grid (c8's integration ruling, 16
        Oct). While the look names a compiled light scene, that scene's hits block is the one (README S5.24); with
        light programs, each group's scene's and running effects' blocks (docs/LIGHTS.md §5.6)."""
        prog = self._program
        look = self._call("look", self.look_fn) if (prog.hits is not None or prog.scenes or prog.group_scenes) \
            else None
        blocks: list[tuple[_Hits, Optional[Mapping[str, bool]]]] = []
        programs = self._programs_of(look)
        if programs is not None:
            for gid, scs in prog.group_scenes.items():
                p = self._prog_for(programs, gid, prog)
                sc = scs.get(p.get("scene")) if p is not None else scs.get(look.get("light_scene"))
                if sc is not None and sc.hits is not None:
                    blocks.append((sc.hits, sc.hit_members))
                elif sc is None and prog.hits is not None:
                    blocks.append((prog.hits, None))
                for r in (p.get("effects") or ()) if p is not None else ():
                    ef = prog.group_effects.get(gid, {}).get(r.get("name")) if isinstance(r, Mapping) else None
                    if ef is not None and ef.hits is not None:
                        blocks.append((ef.hits, ef.hit_members))
        else:
            sc = prog.scenes.get(look.get("light_scene")) if prog.scenes and isinstance(look, Mapping) else None
            hp = prog.hits if sc is None else sc.hits
            if hp is not None:
                blocks.append((hp, sc.hit_members if sc is not None else None))
        if not blocks:
            return False
        try:
            s = float(d.get("strength", 1.0))
        except (AttributeError, TypeError, ValueError):
            return False
        if not isinstance(look, Mapping) or look.get("house") or look.get("low") or look.get("dip"):
            return False
        style = (look.get("accents") or {}).get("style")
        ok = False
        for hp, members in blocks:
            if output_id is not None:
                if members is not None:
                    if not members.get(output_id):
                        continue
                else:
                    op = prog.outputs.get(output_id)
                    if op is None or not op.hit_members:
                        continue
            if s >= hp.min_strength and (hp.bands is None or d.get("band") in hp.bands) and style in hp.styles:
                ok = True
                break
        if not ok:
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
        motion: dict[str, dict] = {}
        for oid, st in list(self._outs.items()):
            n = min(st.n_eval, EVAL_RING)
            xs = sorted(st.eval_us[:n]) if n else []
            sf = st.fades.get(SCENE_FADE)
            per[oid] = {"eval_us": {"p50": round(xs[n // 2], 1) if xs else None,
                                    "p95": round(xs[min(n - 1, int(0.95 * n))], 1) if xs else None},
                        "frames": st.seq, "hits": st.hits, "held": st.held, "why": st.why,
                        "coarsened": sorted(st.coarsened), "light_scene": sf.new if sf is not None else None,
                        "programs": dict(st.programs), "busy": st.busy}
            op = p.outputs.get(oid)
            for s in (op.slots if op is not None else ()):
                fs = st.fix.get(s.id)
                if fs is None or not (s.enc or fs.held):
                    continue
                motion[s.id] = {"slowed": dict(fs.slowed), "clamped": sorted(fs.clamped),
                                "unresolved": sorted(fs.unresolved), "unreachable": sorted(fs.unreachable),
                                "held": fs.held}
        now = self._mono()
        return {"program": {"revision": p.revision, "layers": [L.id for L in p.layers], "problems": list(p.problems),
                            "scenes": {"revision": p.scene_revision,
                                       "compiled": {n: [L.id for L in s.layers] for n, s in p.scenes.items()},
                                       "problems": list(p.scene_problems)},
                            "groups": {g: list(v.get("members") or ()) for g, v in p.groups.items()},
                            "effects": {g: sorted(v) for g, v in p.group_effects.items()},
                            "skipped": dict(p.skipped), "modules": dict(self.modules)},
                "per_output": per, "errors": dict(self.errors),
                "identify": {k: round(v[0] - now, 1) for k, v in self._ident.items() if v[0] > now},
                "follow": dict(self._follow_view), "drops": self.drops, "motion": motion,
                "holds": sorted(k for k, h in self._holds.items() if h.until_s > now),
                "gestures": {**self._gstats, "by_kind": dict(self._gstats["by_kind"]), "parts": len(self._gring)}}

    # -- the live view (docs/LIGHTS.md §8.1; any thread) -------------------------------------------------------------
    def live_view(self, frames: Mapping[str, Any]) -> dict:
        """contract.LiveView from the runners' last frames ({output: Frame}) and the space's poses."""
        p = self._program
        look = self._call("look", self.look_fn)
        look = look if isinstance(look, Mapping) else {}
        programs = {}
        for g, pr in (look.get("light_programs") or {}).items() if isinstance(look.get("light_programs"),
                                                                                  Mapping) else ():
            if isinstance(pr, Mapping):
                programs[str(g)] = {"label": pr.get("label"), "scene_label": pr.get("scene_label"),
                                    "effects": [e.get("label") for e in pr.get("effects") or ()
                                                if isinstance(e, Mapping)]}
        holds = self._holds
        now = self._mono()
        fixtures: dict[str, dict] = {}
        sm, view = p.space_mod, p.space
        for oid, fr in frames.items():
            op = p.outputs.get(oid)
            if op is None or not isinstance(fr, Mapping):
                continue
            ffs = fr.get("fixtures") or {}
            for s in op.slots:
                ff = ffs.get(s.id)
                if not isinstance(ff, Mapping):
                    continue
                a = ff.get("attrs") if isinstance(ff.get("attrs"), Mapping) else {}
                pan, tilt = a.get("pan"), a.get("tilt")
                d = hit = surf = None
                if s.pose is not None and sm is not None and view is not None and _isnum(pan) and _isnum(tilt):
                    d = self._call("space.beam_dir", getattr(sm, "beam_dir", None), s.pose, float(pan), float(tilt))
                    if _vec3(d) is not None and s.pos is not None:
                        got = self._call("space.ray_hit", getattr(sm, "ray_hit", None), view, s.pos, tuple(d))
                        if isinstance(got, (list, tuple)) and len(got) == 2 and _vec3(got[0]) is not None:
                            hit, surf = [round(x, 3) for x in got[0]], got[1]
                gname = None
                if a.get("gobo") is not None and s.caps is not None:
                    for sl in s.caps.get("gobo_slots") or ():
                        if isinstance(sl, Mapping) and sl.get("slot") == a.get("gobo"):
                            gname = sl.get("name")
                rgb = ff.get("rgb") or (0.0, 0.0, 0.0)
                h = holds.get(s.id)
                fixtures[s.id] = {
                    "at": list(s.pos) if s.pos is not None else None,
                    "dir": [round(x, 4) for x in d] if _vec3(d) is not None else None,
                    "hit": hit, "surface": surf if isinstance(surf, str) else None,
                    "pan": round(float(pan), 2) if _isnum(pan) else None,
                    "tilt": round(float(tilt), 2) if _isnum(tilt) else None,
                    "zoom_deg": round(float(a["zoom"]), 2) if _isnum(a.get("zoom")) else None,
                    "gobo": gname, "prism": bool(_isnum(a.get("prism")) and a["prism"] > 0),
                    "intensity": round(float(ff.get("intensity", 0.0)), 4),
                    "hex": "#%02x%02x%02x" % tuple(max(0, min(255, round(float(c) * 255))) for c in rgb),
                    "held": h is not None and h.until_s > now, "calibrated": s.pose is not None}
        flow = look.get("flow")
        return {"t_ms": now * 1000.0, "flow": dict(flow) if isinstance(flow, Mapping) else None,
                "programs": programs, "fixtures": fixtures}

    # -- inputs --------------------------------------------------------------------------------------------------------
    def _call(self, name: str, fn: Optional[Callable], *a: Any) -> Any:
        if fn is None:
            return None
        try:
            return fn(*a)
        except Exception:
            self._fail(name)
            return None

    def _fail(self, name: str) -> None:
        """A failing input or piece (inside an ``except``): counted, logged once, then at most every ERROR_LOG_S."""
        self.errors[name] = self.errors.get(name, 0) + 1
        now = time.monotonic()
        if now >= self._log_next.get(name, 0.0):
            self._log_next[name] = now + ERROR_LOG_S
            log.exception("look engine: %s failed (%d so far; logged at most every %.0f s)", name,
                          self.errors[name], ERROR_LOG_S)

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

    @staticmethod
    def _programs_of(look: Any) -> Optional[Mapping[str, Any]]:
        """LookData.light_programs when the director sends any (§7.2); None: the light_scene path."""
        lp = look.get("light_programs") if isinstance(look, Mapping) else None
        return lp if isinstance(lp, Mapping) and lp else None

    @staticmethod
    def _prog_for(programs: Mapping[str, Any], gid: str, prog: _Program) -> Optional[Mapping[str, Any]]:
        p = programs.get(gid)
        if not isinstance(p, Mapping):
            g = prog.groups.get(gid) or {}
            f = g.get("follows")
            p = programs.get(f) if isinstance(f, str) else None
        return p if isinstance(p, Mapping) else None

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
        st.caches_for(prog)
        frame = {"output": output_id, "seq": st.seq, "t_send_ms": t * 1000.0, "active": False, "why": None,
                 "fixtures": {}, "busy": False}
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
        if look is not None:
            dl = look.get("light_scene_drop")
            if dl is not None:
                self._take_drop(dl)                              # a new drop line, once per id (README S5.24)
            if look.get("gestures"):                             # docs/ENSEMBLE.md §7.2: each (id, seq) once
                self._call("gestures", self._take_gestures, look)
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
        ident, follow = self._ident, self._follow                 # identify and follow, read once a frame
        ov = bool(ident) or bool(follow)
        holds = self._holds                                       # the Lights page's holds, read once a frame
        mc = prog.motion
        busy = False
        if not active:
            te = t * 1000.0
            for s in op.slots:
                if holds:
                    h = holds.get(s.id)
                    if h is not None and t < h.until_s:
                        fixtures[s.id] = self._held_frame(s, h, st.fixstate(s.id), t, te, mc)
                        busy = True
                        continue
                fs = st.fix.get(s.id) if s.enc else None
                if fs is not None:
                    fs.held = False
                o = self._override(s.id, ident, follow, t) if ov else None
                if o is not None:
                    fixtures[s.id] = self._fixed(s, o[0], o[1], te, fs, o[2])
                    continue
                hi, hcol, hrgb, hdmx = s.house
                ff = {"intensity": hi, "colour": list(hcol), "rgb": list(hrgb), "t_eval_ms": te}
                if s.enc:
                    attrs = dict(s.neutral)
                    if s.moves:                                  # a mover glides home at its speed (§5.1, 4)
                        fs = st.fixstate(s.id)
                        pan, tilt, _dk = self._move(s, fs, t, s.home, mc)
                        attrs["pan"], attrs["tilt"] = pan, tilt
                        if abs(pan - s.home[0]) > ARRIVED_DEG or abs(tilt - s.home[1]) > ARRIVED_DEG:
                            busy = True
                            hdmx = self._encode(s, hi, hcol, attrs)
                        fs.last_attrs = attrs
                    ff["attrs"] = attrs
                if hdmx is not None:
                    ff["dmx"] = hdmx
                fixtures[s.id] = ff
            frame["why"] = why
            frame["busy"] = st.busy = busy
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
        # S5.14; the director's own swell depth and drop hit are already off under them); a mover rests at home
        house_low = look is not None and bool(look.get("house") or look.get("low"))
        steady = held is not None or house_low

        cx = self._ctx(t, look, snap, trans_s, min_period_s, energy, vibe, st, prog, op)
        cx.dt = dt
        # the ensemble (docs/ENSEMBLE.md §7.2): each light's part (its role's scales, canon's offset, a transition's
        # stagger) and the gesture parts on it; neither on the look: nothing below changes
        ev = self._ens_view(look)
        if ev is not None:
            cx.ms, cx.co = {}, {}
        gw = getattr(contract, "gesture_weight", None)
        gparts = self._gesture_parts(t, bool(look.get("dip")), min_period_s) \
            if self._gring and gw is not None and look is not None and not steady else None
        # the groups (§5.6): with light programs, each group runs its own; without, every lamp the light_scene path
        programs = self._programs_of(look)
        gnow: dict[str, _Group] = {}
        for gid in (op.groups if programs is not None else (COMPAT,)):
            gnow[gid] = self._group_now(gid, programs, prog, st, cx, t, snap, gate, caps_hits, min_attack_s, look,
                                        vibe, energy, trans_s, min_period_s, steady, house_low, output_id, op)

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
        memo: dict[tuple, float] = {}                            # one oscillator value per (layer, time, lag)
        fl_memo: dict[tuple, float] = {}
        te0 = t * 1000.0
        for s in op.slots:
            if holds:
                h = holds.get(s.id)
                if h is not None and t < h.until_s:              # held: out of everything (§5.7)
                    fixtures[s.id] = self._held_frame(s, h, st.fixstate(s.id), t, te0, mc)
                    busy = True
                    continue
            fs = st.fix.get(s.id)
            if fs is None:
                fs = st.fix[s.id] = _FixState()
            fs.held = False
            if ov:
                o = self._override(s.id, ident, follow, t)
                if o is not None:                                # identify, or not following: no look at all
                    inten, col, idn = o
                    if not idn and min_attack_s > 0.0 and fs.prev is not None and t >= fs.t_prev:
                        inten = min(inten, fs.prev + SLEW_PEAK * (t - fs.t_prev) / min_attack_s)   # the rise limit
                    fs.prev, fs.t_prev = inten, t
                    fixtures[s.id] = self._fixed(s, inten, col, t * 1000.0, fs if s.enc else None, idn)
                    continue
            gn = gnow.get(s.group if programs is not None else COMPAT)
            if gn is None:
                gn = gnow[COMPAT] = self._group_now(COMPAT, None, prog, st, cx, t, snap, gate, caps_hits,
                                                   min_attack_s, look, vibe, energy, trans_s, min_period_s, steady,
                                                   house_low, output_id, op)
            ti = t + s.rise_s
            tc = t + s.colour_s
            pi = pal_at(ti)
            rgb_i = self._base(s, pi, st, prog, look)
            rgb_c = rgb_i if tc == ti else self._base(s, pal_at(tc), st, prog, look)
            level = max(rgb_i)
            cm = max(rgb_c)
            colour = [rgb_c[0] / cm, rgb_c[1] / cm, rgb_c[2] / cm] if cm > 0.0 else [0.0, 0.0, 0.0]
            inten = level
            sweep = gn.pf is not None and gn.pf.kind == "sweep" and gn.pf.old != gn.pf.new
            cs = self._coord(cx, s, gn.flow, None, gn.flow is not None and gn.flow.get("symmetry") == "mirror") \
                if sweep else 0.0
            # its ensemble part: (pulse, motion) scales, canon's offset (s), the transition's stagger (s)
            rs, co, so = (None, 0.0, 0.0) if ev is None else self._ens_fix(ev, s, st, cx, gn, ti)
            # oscillators scale the base, in file order: each scene of the group's mix at its share for this light
            for part in gn.parts:
                if not part.inows:
                    continue
                w = part.w0 if part.pf is None else part.w(ti - so, cs)
                if w <= 0.0:
                    continue
                inten = self._intensity(part.inows, s, inten, ti, w, memo, energy, cx, gn, rs, co)
            for ef, env, inows in gn.effects:
                if inows:
                    e = env(ti)
                    if e > 0.0:
                        inten = self._intensity(inows, s, inten, ti, e, memo, energy, cx, gn, rs, co)
            # everything else a layer sets (position, beam, gobo, prism, colour, ...): latest takes precedence
            attrs = None
            pan_tilt = None
            dark_ok, light_at = False, None
            if s.enc or s.id in gn.amembers:
                attrs, pan_tilt, colour, dark_ok, light_at = self._attrs(s, fs, gn, cx, t, colour, cs, house_low,
                                                                         so)
            # hits: highest takes precedence (one flash value per frame for each block and min_pulse)
            if gn.blocks:
                fl = 0.0
                member = False
                for bi, (hp, wgt, flashes, drops, _hz) in enumerate(gn.blocks):
                    if s.id not in hp.members:
                        continue
                    member = True
                    key = (gn.gid, bi, s.min_pulse_s)
                    v0 = fl_memo.get(key)
                    if v0 is None:
                        v0 = 0.0
                        for age, F in flashes:
                            v = F if age < s.min_pulse_s else F * math.exp(-(age - s.min_pulse_s) / hp.decay_s)
                            if v > v0:
                                v0 = v
                        fl_memo[key] = v0
                    v0 *= wgt
                    for at, F, dec in drops:                     # at the fixture's own latency: a line ahead lands
                        age = ti - at                            # on it
                        if age >= 0.0:
                            v = (F if age < s.min_pulse_s else F * math.exp(-(age - s.min_pulse_s) / dec)) * wgt
                            if v > v0:
                                v0 = v
                    if v0 > fl:
                        fl = v0
                if member:
                    hv = min(1.0, level + fl)
                    if hv > inten:
                        inten = hv
            # the ensemble's gestures (docs/ENSEMBLE.md §7.2): a flash HTP over the level, a colour pop, a kick
            if gparts is not None:
                gl = gparts.get(s.id)
                if gl:
                    inten, colour, pan_tilt = self._gestures(gl, s, fs, cx, gw, t, level, inten, colour, attrs,
                                                             pan_tilt, caps_hits, min_attack_s)
            # a fade_dark transition: toward the group's dark level and back (§5.6)
            if gn.pf is not None and gn.pf.kind == "fade_dark":
                x = gn.pf.darkness(ti, mc.dark_max_s)
                if x > 0.0:
                    inten += (gn.dark_abs - inten) * x
            # master and caps (lamps only; screens never reach here)
            inten = _c01(inten * master)
            # the motion stage (§5.5): commanded pan and tilt within the light's speed and limits; a dark move
            if pan_tilt is not None:
                pan, tilt, dark = self._move(s, fs, t, pan_tilt, mc, dark_ok=dark_ok, light_at=light_at)
                attrs["pan"], attrs["tilt"] = pan, tilt
                if dark:
                    inten = 0.0
            flash_hz = gn.flash_hz
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
            if s.enc:
                ff["attrs"] = attrs if attrs is not None else dict(s.neutral)
                fs.last_attrs, fs.last_inten, fs.last_colour = ff["attrs"], inten, colour
            if s.channels:
                ff["dmx"] = self._encode(s, inten, colour, ff.get("attrs"))
            fixtures[s.id] = ff
        frame["busy"] = st.busy = busy
        self._record(st, c0)
        return frame

    # -- one frame's pieces --------------------------------------------------------------------------------------------
    def _ctx(self, t: float, look: Any, snap: Optional[Mapping[str, Any]], trans_s: float, min_period_s: float,
             energy: float, vibe: bool, st: _OutState, prog: _Program, op: _OutProg) -> _Ctx:
        cx = _Ctx()
        cx.t, cx.look, cx.snap, cx.trans_s, cx.min_period_s = t, look, snap, trans_s, min_period_s
        cx.energy, cx.vibe, cx.st, cx.prog, cx.op = energy, vibe, st, prog, op
        src = snap if snap is not None else self._free_run()
        cx.src = src
        cx.beat_s = 60.0 / float(src["bpm"])
        cx.bpb = max(1, int(src.get("beats_per_bar") or FREE_RUN_BEATS_PER_BAR))
        cx.bar_anchor = float(src.get("bar_anchor_beat") or 0)
        cx.phrase_bars = max(1, int(src.get("phrase_bars") or FREE_RUN_PHRASE_BARS))
        cx.sm, cx.view = prog.space_mod, prog.space
        cx.cyc = {}
        cx.dt = 0.0
        lid = look.get("look_id") if isinstance(look, Mapping) else None
        digits = "".join(ch for ch in str(lid or "") if ch.isdigit())
        cx.look_n = int(digits) if digits else 0
        cx.flow_key = (prog.revision, (prog.space or {}).get("revision"))
        cx.ms = cx.co = cx.roles = None
        return cx

    # -- the ensemble per light (docs/ENSEMBLE.md §7.2) ---------------------------------------------------------------
    @staticmethod
    def _ens_num(d: Any, key: str, default: float) -> float:
        v = d.get(key) if isinstance(d, Mapping) else None
        return float(v) if _isnum(v) and v >= 0 else default

    @staticmethod
    def _ens_glide(st: _OutState, key: tuple[str, str], v: float, rest: float, t: float, dur: float) -> float:
        """A light's role value gliding to ``v`` over ``dur`` from the change; the first one glides from ``rest`` (the
        value with no ensemble), and a light that has only ever been at rest makes no glide (exactly today's)."""
        gl = st.glides.get(key)
        if gl is None:
            if v == rest:
                return v
            gl = st.glides[key] = _Glide(rest, t)
        return gl.to(v, t, dur)

    def _ens_fix(self, ev: _EnsView, s: _Slot, st: _OutState, cx: _Ctx, gn: _Group, ti: float
                 ) -> tuple[Optional[tuple[float, float]], float, float]:
        """One light's ensemble part this frame: ((pulse, motion) | None, canon's offset in s, its transition's stagger
        in s). The scales and the offset glide over ``glide_ms`` from the change; a light at 1.0 with no offset that
        never had one makes no glide at all (the frame is exactly today's). The stagger is the part in effect at the
        transition's line, for a cut, dark move or crossfade at the ensemble's look change."""
        p = ev.part(s.id, ti)
        sc = p.get("scale") if p is not None else None
        g = ev.glide_s
        pulse = self._ens_glide(st, (s.id, "ens:pulse"), self._ens_num(sc, "pulse", 1.0), 1.0, ti, g)
        motion = self._ens_glide(st, (s.id, "ens:motion"), self._ens_num(sc, "motion", 1.0), 1.0, ti, g)
        cob = self._ens_glide(st, (s.id, "ens:canon"), self._ens_num(p, "clock_offset_beats", 0.0), 0.0, ti, g)
        co = cob * cx.beat_s
        if co:
            cx.co[s.id] = co
        if motion != 1.0:
            cx.ms[s.id] = motion
        so = 0.0
        pf = gn.pf
        if pf is not None and pf.old != pf.new and pf.kind in STAGGER_KINDS and ev.moment(pf.at):
            pa = ev.part(s.id, pf.at + STAGGER_MATCH_MS / 1000.0)
            sb = pa.get("stagger_beats") if pa is not None else None
            if cx.snap is not None and _isnum(sb):
                so = max(0.0, float(sb)) * cx.beat_s
            elif pa is not None:
                so = self._ens_num(pa, "stagger_s", 0.0)
        return (None if pulse == 1.0 and motion == 1.0 else (pulse, motion)), co, so

    def _gesture_parts(self, t: float, dip: bool, min_period_s: float) -> Optional[dict[str, list]]:
        """This frame's gesture parts per light, [(part, strength)], from the ring: over the pre-drop dip only a hit.
        On an output with ``caps.min_period_ms`` (Hue: it never strobes) a part closer than that to the first of its
        run merges into it, which keeps the larger strength (counted once per part)."""
        by: dict[str, list] = {}
        for p in self._gring:
            if p.end_s < t or p.at_s - p.attack_s - GESTURE_AHEAD_S > t:
                continue
            if dip and p.kind != GESTURE_OVER_DIP:
                continue
            e = (p, p.strength)
            for fid in p.members:
                lst = by.get(fid)
                if lst is None:
                    by[fid] = [e]
                else:
                    lst.append(e)
        if not by:
            return None
        if min_period_s > 0.0:
            gs = self._gstats
            for fid, lst in by.items():
                if len(lst) < 2:
                    continue
                lst.sort(key=lambda e: e[0].at_s)
                keep = [lst[0]]
                for e in lst[1:]:
                    f = keep[-1]
                    if e[0].at_s - f[0].at_s < min_period_s:
                        if e[1] > f[1]:
                            keep[-1] = (f[0], e[1])
                        if not e[0].merged:
                            e[0].merged = True
                            gs["merged"] += 1
                    else:
                        keep.append(e)
                by[fid] = keep
        return by

    def _gestures(self, parts: list, s: _Slot, fs: _FixState, cx: _Ctx, gw: Callable, t: float, level: float,
                  inten: float, colour: list, attrs: Optional[dict], pan_tilt: Optional[Sequence[float]],
                  step_out: bool, min_attack_s: float) -> tuple[float, list, Optional[Sequence[float]]]:
        """The gesture parts on one light this frame, each at the light's own times (intensity, colour, position)
        through contract.gesture_weight x its strength: the flash HTP over the level (x the shape's depth), the colour
        toward the part's palette role at full value (x colour_depth), and the largest kick. Never the shutter."""
        ti, tc, tp = t + s.rise_s, t + s.colour_s, t + s.pos_s
        sw, mh = HIT_SWELL_MS / 1000.0, s.min_pulse_s
        kick = 0.0
        gs = self._gstats
        for p, strength in parts:
            step = step_out or p.predicted
            w = gw(ti, p.at_s, p.shape, step=step, min_attack_s=min_attack_s, min_hold_s=mh, swell_s=sw,
                   dur_s=p.dur_s) * strength
            if w > 0.0:
                if not p.shown:
                    p.shown = True
                    gs["shown"] += 1
                hv = min(1.0, level + p.depth * w)
                if hv > inten:
                    inten = hv
            if p.colour is not None and p.cdepth > 0.0:
                wc = w if tc == ti else gw(tc, p.at_s, p.shape, step=step, min_attack_s=min_attack_s, min_hold_s=mh,
                                           swell_s=sw, dur_s=p.dur_s) * strength
                if wc > 0.0:
                    if cx.roles is None:
                        cx.roles = self._role_rgbs(cx.look)
                    rc = cx.roles.get(p.colour)
                    if rc is not None:
                        c = palette_mix(colour, rc, min(1.0, p.cdepth * wc))
                        m = max(c)
                        if m > 0.0:
                            colour = [c[0] / m, c[1] / m, c[2] / m]
            if p.kick > 0.0 and attrs is not None:
                wp = w if tp == ti else gw(tp, p.at_s, p.shape, step=step, min_attack_s=min_attack_s, min_hold_s=mh,
                                           swell_s=sw, dur_s=p.dur_s) * strength
                if p.kick * wp > kick:
                    kick = p.kick * wp
        if kick > 0.0:
            pan_tilt = self._kick(s, fs, cx, min(1.0, kick), attrs, pan_tilt)
        return inten, colour, pan_tilt

    def _kick(self, s: _Slot, fs: _FixState, cx: _Ctx, k: float, attrs: dict, pan_tilt: Optional[Sequence[float]]
              ) -> Optional[Sequence[float]]:
        """A gesture's kick ``k`` (0..1): a calibrated mover's aim point rises KICK_M x k metres, through the motion
        stage after (slew-limited: degrade); an uncalibrated light with zoom widens toward its widest by k; anything
        else: nothing (no guessed directions)."""
        if self._calibrated(s, cx):
            if pan_tilt is None:
                return None
            bd = _vec3(self._call("space.beam_dir", getattr(cx.sm, "beam_dir", None), s.pose, pan_tilt[0],
                                  pan_tilt[1]))
            hit = self._call("space.ray_hit", getattr(cx.sm, "ray_hit", None), cx.view, s.pos, bd) \
                if bd is not None else None
            q0 = _vec3(hit[0]) if isinstance(hit, (list, tuple)) and hit else None
            if q0 is None:
                return pan_tilt
            pt = self._aim(s, fs, cx, (q0[0], q0[1], q0[2] + KICK_M * k), None, current=pan_tilt)
            return [pt[0], pt[1]] if pt is not None else pan_tilt
        z = s.caps.get("zoom") if s.caps is not None else None
        if "zoom" in s.attrs and isinstance(z, Mapping) and _isnum(z.get("max")) and _isnum(attrs.get("zoom")):
            attrs["zoom"] = attrs["zoom"] + (float(z["max"]) - attrs["zoom"]) * k
        return pan_tilt

    @staticmethod
    def _role_rgbs(look: Any) -> dict[str, Rgb]:
        """LookData.palette.roles at full value (a gesture's colour pop)."""
        pal = look.get("palette") if isinstance(look, Mapping) else None
        roles = pal.get("roles") if isinstance(pal, Mapping) else None
        out: dict[str, Rgb] = {}
        if isinstance(roles, Mapping):
            for r, v in roles.items():
                c = _hex_rgb(v.get("hex")) if isinstance(v, Mapping) else None
                m = max(c) if c is not None else 0.0
                if m > 0.0:
                    out[str(r)] = (c[0] / m, c[1] / m, c[2] / m)
        return out

    def _group_now(self, gid: str, programs: Optional[Mapping[str, Any]], prog: _Program, st: _OutState, cx: _Ctx,
                   t: float, snap: Optional[Mapping[str, Any]], gate: str, caps_hits: bool, min_attack_s: float,
                   look: Any, vibe: bool, energy: float, trans_s: float, min_period_s: float, steady: bool,
                   house_low: bool, output_id: str, op: _OutProg) -> _Group:
        """One group's scenes (the mix), effects, hits, flow and transition for this frame (§5.6)."""
        gn = _Group(gid)
        p = self._prog_for(programs, gid, prog) if programs is not None else None
        flow = (p.get("flow") if p is not None else None)
        if not isinstance(flow, Mapping):
            flow = look.get("flow") if isinstance(look, Mapping) else None
        gn.flow = flow if isinstance(flow, Mapping) else None
        if gid == COMPAT:
            scenes = prog.scenes
            key = SCENE_FADE
        else:
            scenes = prog.group_scenes.get(gid) or {}
            key = SCENE_FADE + gid
        mix: list = _BASE_MIX
        pf = None
        if p is not None:
            # the group's program: its scene lands at its transition's at_ms, each fixture at its own time
            sname = p.get("scene") if p.get("scene") in scenes else None
            tr = p.get("transition") if isinstance(p.get("transition"), Mapping) else {}
            kind = tr.get("kind") if tr.get("kind") in ("cut", "dark_move", "crossfade", "fade_dark", "sweep") \
                else "cut"
            at = tr.get("at_ms", p.get("at_ms"))
            pkey = (sname, tr.get("at_ms"), kind, tr.get("name"))
            pf = st.pfades.get(gid)
            if pf is None:
                pf = st.pfades[gid] = _ProgFade(sname, pkey)
            elif pkey != pf.key:
                if pf.x(t) >= 0.5:
                    pf.old = pf.new
                pf.new, pf.key = sname, pkey
                pf.at = float(at) / 1000.0 if _isnum(at) else t
                pf.kind = kind
            pf.params = tr.get("params") if isinstance(tr.get("params"), Mapping) else {}
            prm = pf.params
            beats, secs = prm.get("beats"), prm.get("seconds")
            pf.dur = float(beats) * cx.beat_s if _isnum(beats) and beats > 0 and snap is not None else \
                float(secs) if _isnum(secs) and secs > 0 else 0.0
            wb = prm.get("wave_beats")
            pf.wave_s = float(wb) * cx.beat_s if _isnum(wb) and wb > 0 else 0.0
            dl = (prog.groups.get(gid) or {}).get("dark_level", 0)
            gn.dark_abs = self._look_value(look, "floor") if dl == "floor" else _c01(float(dl)) if _isnum(dl) else 0.0
            gn.pf = pf
            st.programs[gid] = pf.new
        elif scenes or key in st.fades:
            sname = look.get("light_scene") if look is not None else None
            mix = st.fade(key, sname if sname in scenes else None, t, trans_s)
            if gid == COMPAT or gid in st.programs:
                st.programs[gid] = mix[-1][1]
        # the parts: each scene of the mix, its intensity layers computed once (none while steady)
        if pf is not None:
            names = [(pf.new, True)] if pf.old == pf.new else [(pf.old, False), (pf.new, True)]
            for sn, new in names:
                sc = scenes.get(sn) if sn is not None else None
                gn.parts.append(self._part(sc, prog, st, t, snap, gate, caps_hits, min_attack_s, look, vibe, energy,
                                           trans_s, min_period_s, steady, house_low, pf, new, 1.0))
            dom = pf.new if pf.x(t) >= 0.5 else pf.old
        else:
            for w, sn in mix:
                sc = scenes.get(sn) if sn is not None else None
                gn.parts.append(self._part(sc, prog, st, t, snap, gate, caps_hits, min_attack_s, look, vibe, energy,
                                           trans_s, min_period_s, steady, house_low, None, True, w))
            dom = mix[-1][1] if mix[-1][0] >= 0.5 else mix[0][1]
        sc = scenes.get(dom) if dom is not None else None
        gn.dom = sc
        flash_hz = prog.flash_hz_max if sc is None or sc.flash_hz_max is None else sc.flash_hz_max
        # effects (§5.6): each run's envelope over the group's scene
        effs = prog.group_effects.get(gid) or {} if gid != COMPAT else {}
        if p is not None and effs:
            mem = st.fx.setdefault(gid, {})
            seen = set()
            for r in p.get("effects") or ():
                if not isinstance(r, Mapping) or r.get("name") not in effs or not _isnum(r.get("at_ms")):
                    continue
                rid = str(r.get("id") or r.get("name"))
                seen.add(rid)
                u = r.get("until_ms")
                sv = r.get("strength", 1.0)
                old = mem.get(rid)
                mem[rid] = [r["name"], float(r["at_ms"]) / 1000.0, float(u) / 1000.0 if _isnum(u) else None,
                            old[3] if old is not None else None, _c01(float(sv)) if _isnum(sv) else 1.0]
            for rid, m in list(mem.items()):
                ef = effs.get(m[0])
                if rid not in seen and m[3] is None and m[2] is None:
                    m[3] = t                                     # it left the list: it fades out from now
                end = m[2] if m[2] is not None else m[3]
                out_s = ef.fade[1] * cx.beat_s if ef is not None else 0.0
                if ef is None or (end is not None and t > end + out_s + 1.0):
                    mem.pop(rid, None)
                    continue
                env = self._envelope(m, ef, cx.beat_s)
                inows = [] if steady else self._nows(ef.layers, st, t, snap, gate, caps_hits, min_attack_s, look,
                                                     vibe, energy, trans_s, prog.middle_depth, min_period_s)
                gn.effects.append((ef, env, inows))
                if ef.flash_hz_max is not None and env(t) > 0.0:
                    flash_hz = ef.flash_hz_max if flash_hz is None else min(flash_hz, ef.flash_hz_max)
        gn.flash_hz = flash_hz
        am = [part.amembers for part in gn.parts if part.amembers] + [ef.amembers for ef, _e, _i in gn.effects
                                                                       if ef.amembers]
        gn.amembers = am[0] if len(am) == 1 else frozenset().union(*am) if am else frozenset()
        # hits, once per frame (never over the pre-drop dip either: LookData.dip): the scene's own, else looks.hits;
        # and each running effect's at its envelope
        blocks = [(sc.hits if sc is not None else prog.hits, 1.0,
                   sc.hit_horizon_s.get(output_id, 0.0) if sc is not None else op.hit_horizon_s)]
        for ef, env, _i in gn.effects:
            if ef.hits is not None:
                blocks.append((ef.hits, env(t), ef.hit_horizon_s.get(output_id, 0.0)))
        for hp, wgt, horizon in blocks:
            if hp is None or wgt <= 0.0 or not caps_hits or steady:
                continue
            flashes: list[tuple[float, float]] = []
            if gate != "none" and look is not None and (look.get("accents") or {}).get("style") in hp.styles and \
                    not look.get("dip"):
                for seq, th, band, strength in tuple(self._hits):
                    if strength < hp.min_strength or (hp.bands is not None and band not in hp.bands):
                        continue
                    age = t - th
                    if age > horizon:
                        continue
                    if seq > st.hit_seq:
                        st.hit_seq = seq
                        st.hits += 1
                    flashes.append((max(0.0, age), hp.depth * min(1.0, strength)))
            # drop lines (trigger_hit): the hits block's drop flash, over the dip, never steady
            drops: list[tuple[float, float, float]] = []
            if hp.drop is not None and self._drops:
                dd, ddec = hp.drop
                dz = ddec * HIT_TAIL_DECAYS + op.max_pulse_s
                for _k, at, strength in tuple(self._drops):
                    if t - at <= dz and dd * strength > 0.0:
                        drops.append((at, dd * strength, ddec))
            if flashes or drops:
                gn.blocks.append((hp, wgt, flashes, drops, horizon))
        return gn

    def _part(self, sc: Optional[_Scene], prog: _Program, st: _OutState, t: float,
              snap: Optional[Mapping[str, Any]], gate: str, caps_hits: bool, min_attack_s: float, look: Any,
              vibe: bool, energy: float, trans_s: float, min_period_s: float, steady: bool, house_low: bool,
              pf: Optional[_ProgFade], new: bool, w0: float) -> _Part:
        layers = prog.layers if sc is None else sc.layers
        inows = [] if steady else self._nows(layers, st, t, snap, gate, caps_hits, min_attack_s, look, vibe, energy,
                                             trans_s, prog.middle_depth, min_period_s)
        alayers = () if house_low else tuple(L for L in layers if L.kind != "intensity")
        am = (sc.amembers if sc is not None else frozenset(m for L in alayers for m in L.members)) if alayers \
            else frozenset()
        return _Part(sc, inows, alayers, am, pf, new, w0)

    def _nows(self, layers: Sequence[_Layer], st: _OutState, t: float, snap: Optional[Mapping[str, Any]], gate: str,
              caps_hits: bool, min_attack_s: float, look: Any, vibe: bool, energy: float, trans_s: float,
              middle_depth: float, min_period_s: float) -> list[_LayerNow]:
        out = []
        for L in layers:
            if L.kind != "intensity":
                continue
            ln = self._layer_now(L, st, t, snap, gate, caps_hits, min_attack_s, look, vibe, energy, trans_s,
                                 middle_depth, min_period_s)
            if ln is not None:
                out.append(ln)
        return out

    @staticmethod
    def _envelope(m: list, ef: _Scene, beat_s: float) -> Callable[[float], float]:
        """An effect run's envelope e(t) (§5.6): up over fade.in_beats from at, held, down over fade.out_beats from
        its until (or from when it left the list), x its strength."""
        _name, at, until, left, strength = m
        in_s, out_s = ef.fade[0] * beat_s, ef.fade[1] * beat_s
        end = until if until is not None else left

        def env(tt: float) -> float:
            if tt < at:
                return 0.0
            up = 1.0 if in_s <= 0.0 else _c01((tt - at) / in_s)
            if end is None or tt < end:
                down = 1.0
            else:
                down = 0.0 if out_s <= 0.0 else 1.0 - _c01((tt - end) / out_s)
            return min(up, down) * strength
        return env

    def _intensity(self, inows: Sequence[_LayerNow], s: _Slot, inten: float, ti: float, w: float, memo: dict,
                   energy: float, cx: _Ctx, gn: _Group, rs: Optional[tuple[float, float]] = None,
                   co: float = 0.0) -> float:
        """Intensity layers on one fixture at ``ti``, each at depth x ``w`` (a scene's share, an effect's envelope).
        docs/ENSEMBLE.md §7.2: ``rs`` (pulse, motion), its role's scales: a ``beat``-phase layer's depth x pulse, any
        other x motion; ``co``: canon's offset (s), its oscillators that much late."""
        to = ti - co if co else ti
        for ln in inows:
            m = ln.layer.members.get(s.id)
            if m is None:
                continue
            if ln.mode == "energy":
                val = energy
            else:
                L = ln.layer
                if L.spread_mode == "chase" and L.order == "group":     # today's walk in the group's order
                    lag = ln.spread * m[0] / m[1] if ln.spread and m[1] else 0.0
                else:
                    lag = self._lag(L, ln.spread, s, m, cx, gn)
                key = (id(ln), to, lag)
                val = memo.get(key)
                if val is None:
                    val = 0.0
                    for vw, src, off, div, per in ln.variants:
                        cyc = ((beat_at(src, to) - off) / div if src is not None else to / div) - lag
                        val += vw * shape(ln.layer.fx, cyc, per, ln.attack_s, ln.layer.decay_s)
                    memo[key] = val
            dd = ln.d * w
            if rs is not None:
                dd *= rs[0] if ln.layer.phase == "beat" else rs[1]
                if dd > 1.0:
                    dd = 1.0
            inten *= 1.0 - dd + dd * val
        return inten

    # -- spread and flow (§5.2, §5.4) ---------------------------------------------------------------------------------
    def _lag(self, L: _Layer, spread: float, s: _Slot, m: tuple[int, int], cx: _Ctx, gn: Optional[_Group]) -> float:
        """A member's phase lag in cycles: ``spread x`` its place: i/n in the layer's order (chase), its place along
        the flow (order flow / mirror), its mirror partner's (spread_mode mirror), or none (unison)."""
        if spread == 0.0 or L.spread_mode == "unison":
            return 0.0
        mirror = L.spread_mode == "mirror" or L.order == "mirror"
        if L.order == "group" and not mirror:
            return spread * m[0] / m[1] if m[1] else 0.0
        if L.order == "group":                                   # mirror in group order: halves walk together
            i, n = m
            return spread * (min(i, n - 1 - i) / n if n else 0.0)
        flow = gn.flow if gn is not None else None
        if not mirror and flow is not None and flow.get("symmetry") == "mirror":
            mirror = True
        return spread * self._coord(cx, s, flow, L.flow_dir, mirror)

    def _coord(self, cx: _Ctx, s: _Slot, flow: Optional[Mapping[str, Any]], dir_: Optional[str], mirror: bool
               ) -> float:
        """The fixture's place along the flow (0..1, 0 where the wave starts: space.flow_coord of its position;
        mirrored across the room's front axis with ``mirror``), else its place in its group's order (reversed for
        left, down, ccw, inward and away; 0 for still). Cached per output per flow."""
        d = dir_ or (flow.get("dir") if isinstance(flow, Mapping) else None) or "still"
        if d not in FLOW_DIRS:
            d = "still"
        centre = _vec3(flow.get("at_xyz")) if isinstance(flow, Mapping) else None
        key = (d, centre, mirror, cx.flow_key)
        cmap = cx.st.coords.get(key)
        if cmap is None:
            if len(cx.st.coords) > 64:
                cx.st.coords.clear()
            cmap = cx.st.coords[key] = {}
        c = cmap.get(s.id)
        if c is not None:
            return c
        c = None
        sm, view = cx.sm, cx.view
        if d != "still" and sm is not None and view is not None and s.pos is not None:
            got = self._call_kw("space.flow_coord", getattr(sm, "flow_coord", None), view, d, centre, s.pos,
                                symmetry="mirror" if mirror else "none")
            c = _c01(float(got)) if _isnum(got) else None
        if c is None:
            i, n = s.gi
            if d == "still" or n <= 0:
                c = 0.0
            else:
                if mirror:
                    i = min(i, n - 1 - i)
                c = (n - 1 - i) / n if d in FLOW_BACK else i / n
        cmap[s.id] = c
        return c

    # -- the non-intensity layers (§5.2-§5.4) --------------------------------------------------------------------------
    def _attrs(self, s: _Slot, fs: _FixState, gn: _Group, cx: _Ctx, t: float, colour: list, cs: float,
               house_low: bool, so: float = 0.0) -> tuple:
        """(attrs, (pan, tilt) | None, colour, dark_ok, light_at) for one fixture: each scene of the mix from the
        light's resting values, combined by the transition, then the running effects over it. ``so``: its stagger
        (docs/ENSEMBLE.md §7.2): the transition lands that much later on this light."""
        tp, tc = t + s.pos_s, t + s.colour_s
        fs.clamped.clear()
        fs.unresolved.clear()
        fs.unreachable.clear()
        fs.slowed.clear()
        base_pos = list(s.home) if s.moves else None
        res = []
        for part in gn.parts:
            if part.alayers and s.id in part.amembers:
                r = self._apply(part.alayers, s, fs, cx, gn, tp, tc, base_pos, None, dict(s.neutral), colour)
            else:
                r = (base_pos, None, dict(s.neutral), colour, False)
            res.append((part, r))
        dark_ok, light_at = False, None
        if len(res) == 1:
            pos, q, attrs, col, dark_ok = res[0][1]
        else:
            (p_old, r_old), (p_new, r_new) = res
            pf = gn.pf
            xa = p_new.w(tp - so, cs)                            # the new scene's share at its position time
            xc = p_new.w(tc - so, cs)
            attrs = self._mix_attrs(r_old[2], r_new[2], xa)
            col = r_new[3] if xc >= 1.0 else r_old[3] if xc <= 0.0 else list(palette_mix(r_old[3], r_new[3], xc))
            dark_ok = r_new[4]
            pos = r_new[0]
            if s.moves and r_old[0] is not None and r_new[0] is not None:
                o, nw = r_old[0], r_new[0]
                jump = max(abs(nw[0] - o[0]), abs(nw[1] - o[1]))
                kind = pf.kind if pf is not None else "crossfade"
                mc = cx.prog.motion
                if pf is None:
                    pos = [o[0] + (nw[0] - o[0]) * xa, o[1] + (nw[1] - o[1]) * xa]
                elif kind in ("cut", "dark_move"):
                    # pre-rolled: the move starts its travel time (plus the dark lead and settle) before the line
                    cur = fs.cmd or o
                    ac = (s.max_speed[0] / mc.accel_s if mc.accel_s > 0 else math.inf,
                          s.max_speed[1] / mc.accel_s if mc.accel_s > 0 else math.inf)
                    tr = max(travel_s(nw[0] - cur[0], s.max_speed[0], ac[0]),
                             travel_s(nw[1] - cur[1], s.max_speed[1], ac[1]))
                    lead = 0.0
                    if kind == "dark_move" and jump > mc.dark_move_min_deg:
                        lead = max(0.0, s.fall_s - s.pos_s) + cx.dt + mc.settle_s
                        dark_ok, light_at = True, pf.at + so
                    pos = nw if tp - so + tr + lead - cx.dt * 0.5 >= pf.at else o
                elif kind == "fade_dark":
                    pos = nw if tp >= pf.at else o               # positions move while dark
                else:                                            # crossfade, sweep
                    if pf.params.get("dark_move") is True and jump > mc.dark_move_min_deg:
                        pos = nw if xa > 0.0 else o
                        dark_ok = True
                    else:
                        pos = [o[0] + (nw[0] - o[0]) * xa, o[1] + (nw[1] - o[1]) * xa]
            q = r_new[1] if xa >= 0.5 else r_old[1]
        # effects: latest takes precedence over the scene, blended by each run's envelope
        for ef, env, _inows in gn.effects:
            if not any(L.kind != "intensity" for L in ef.layers) or s.id not in ef.amembers:
                continue
            e = env(tp)
            if e <= 0.0:
                continue
            r = self._apply(tuple(L for L in ef.layers if L.kind != "intensity"), s, fs, cx, gn, tp, tc,
                            list(pos) if pos is not None else None, q, dict(attrs), col)
            if pos is not None and r[0] is not None:
                pos = [pos[0] + (r[0][0] - pos[0]) * e, pos[1] + (r[0][1] - pos[1]) * e]
            attrs = self._mix_attrs(attrs, r[2], e)
            if r[3] is not col:
                col = list(palette_mix(col, r[3], e))
            dark_ok = dark_ok or r[4]
        if not s.enc:
            return None, None, col, False, None
        if s.moves and pos is None:
            pos = list(s.home)
        return attrs, pos if s.moves else None, col, dark_ok, light_at

    @staticmethod
    def _mix_attrs(a: Mapping[str, Any], b: Mapping[str, Any], x: float) -> dict:
        """a -> b at x: continuous attrs lerp, discrete ones take b from x >= 0.5."""
        if x <= 0.0:
            return dict(a)
        if x >= 1.0:
            return dict(b)
        out = dict(a)
        for k, vb in b.items():
            va = a.get(k)
            if k in LERP_ATTRS and _isnum(va) and _isnum(vb):
                out[k] = va + (vb - va) * x
            elif x >= 0.5:
                out[k] = vb
        return out

    def _apply(self, layers: Sequence[_Layer], s: _Slot, fs: _FixState, cx: _Ctx, gn: _Group, tp: float, tc: float,
               pos: Optional[list], q: Optional[tuple], attrs: dict, colour: list) -> tuple:
        """One scene's (or effect's) non-intensity layers on one fixture, in file order, latest taking precedence:
        (pos, q, attrs, colour, dark_ok). ``q`` is the room point the last position layer aimed at (lift's)."""
        dark_ok = False
        caps = s.caps or {}
        look = cx.look
        for L in layers:
            m = L.members.get(s.id)
            if m is None:
                continue
            k = L.kind
            p = L.p
            if k == "position":
                if pos is None:
                    continue
                got = self._position(L, m, s, fs, cx, gn, tp, pos, q)
                if got is None:
                    fs.unresolved.add(L.id)
                    continue
                own, oq = got
                if p["mode"] == "lift":
                    pos, q = [own[0], own[1]], oq
                else:
                    d = _c01(self._val(L.depth, L, s, m, cx, gn, tp) * L.depth_scale)
                    pos = [pos[0] + (own[0] - pos[0]) * d, pos[1] + (own[1] - pos[1]) * d]
                    if d >= 0.5:
                        q = oq
                if p.get("dark_move"):
                    dark_ok = True
            elif k == "beam":
                d = _c01(self._val(L.depth, L, s, m, cx, gn, tp) * L.depth_scale)
                for a in ("zoom", "focus", "iris", "frost"):
                    if a not in p or a not in s.attrs:
                        continue
                    v = self._beam(a, p[a], L, s, m, cx, gn, tp)
                    if v is None:
                        continue
                    cur = attrs.get(a)
                    attrs[a] = cur + (v - cur) * d if _isnum(cur) else v
            elif k == "gobo":
                wheel = p["wheel"]
                slots = caps.get(f"{wheel}_slots") or ()
                if p.get("slot") is not None:
                    ck = (L.id, s.id)
                    cands = cx.st.slotc.get(ck)
                    if cands is None:
                        cands = cx.st.slotc[ck] = self._slots(p["slot"], slots)
                    if not cands:
                        fs.unresolved.add(L.id)
                    else:
                        attrs[wheel] = cands[self._counter(p["change_on"], cx, tp) % len(cands)]
                sp = self._spin(p, cx, gn)
                if sp is not None and f"{wheel}_spin" in s.attrs:
                    attrs[f"{wheel}_spin"] = sp
                if p.get("shake") is not None and f"{wheel}_shake" in s.attrs:
                    attrs[f"{wheel}_shake"] = _c01(self._val(p["shake"], L, s, m, cx, gn, tp))
            elif k == "prism":
                pin = p["in"]
                if pin == "accents":
                    pin = isinstance(look, Mapping) and (look.get("accents") or {}).get("style") in ("beat", "full")
                ps = caps.get("prism_slots") or ()
                first = ps[0].get("slot", 1) if ps and isinstance(ps[0], Mapping) else 1
                attrs["prism"] = first if pin else 0
                sp = self._spin(p, cx, gn)
                if sp is not None and "prism_spin" in s.attrs:
                    attrs["prism_spin"] = sp if pin else 0.0
            elif k == "colour":
                roles = ((look or {}).get("palette") or {}).get("roles") if isinstance(look, Mapping) else None
                rc = _hex_rgb((roles.get(p["role"]) or {}).get("hex")) if isinstance(roles, Mapping) and \
                    isinstance(roles.get(p["role"]), Mapping) else None
                if rc is None:
                    fs.unresolved.add(L.id)
                    continue
                d = _c01(self._val(L.depth, L, s, m, cx, gn, tc) * L.depth_scale)
                if d > 0.0:
                    colour = list(palette_mix(colour, rc, d))
            elif k == "colour_wheel":
                mode = p["mode"]
                if mode == "follow":
                    attrs["colour_slot"] = None
                elif mode == "step":
                    cs = [sl.get("slot") for sl in caps.get("colour_slots") or () if isinstance(sl, Mapping)
                          and _isnum(sl.get("slot"))]
                    if cs:
                        attrs["colour_slot"] = cs[self._counter(p["step_on"], cx, tp) % len(cs)]
                else:
                    sp = self._spin(p, cx, gn)
                    if sp is not None and "colour_spin" in s.attrs:
                        attrs["colour_spin"] = sp
            elif k == "cct":
                kv = p["k"]
                if kv == "key_cct":
                    kv = look.get("key_cct") if isinstance(look, Mapping) else None
                if _isnum(kv):
                    c = caps.get("cct") if isinstance(caps.get("cct"), Mapping) else {}
                    lo = float(c["min_k"]) if _isnum(c.get("min_k")) else float(kv)
                    hi = float(c["max_k"]) if _isnum(c.get("max_k")) else float(kv)
                    attrs["cct"] = _clamp(float(kv), lo, hi)
            elif k == "shutter":
                lo, hi = p["hz"]
                hz = lo + (hi - lo) * _c01(cx.energy)
                mx = s.setup.get("strobe_hz_max")
                if _isnum(mx) and mx > 0:
                    hz = min(hz, float(mx))
                fz = gn.flash_hz
                if fz is not None:
                    hz = min(hz, fz)
                attrs["shutter"] = {"mode": p["mode"], "hz": hz}
        return pos, q, attrs, colour, dark_ok

    def _val(self, v: Any, L: _Layer, s: _Slot, m: tuple[int, int], cx: _Ctx, gn: _Group, tt: float) -> float:
        """A compiled continuous value for one member at ``tt``: a number, a look input, or an oscillator at the
        member's phase."""
        if isinstance(v, _Osc):
            ol = v.layer
            vs = self._cycles(ol, cx)
            if not vs:
                return v.lo
            if cx.co:                                            # docs/ENSEMBLE.md §7.2: canon, its waves run late
                tt -= cx.co.get(s.id, 0.0)
            sp = self._input(ol.spread, cx.look, cx.vibe, cx.energy)
            lag = self._lag(ol, sp, s, m, cx, gn) if sp else 0.0
            val = 0.0
            for vw, src, off, div, per in vs:
                cyc = ((beat_at(src, tt) - off) / div if src is not None else tt / div) - lag
                val += vw * shape(ol.fx, cyc, per, ol.attack_s, ol.decay_s)
            return v.lo + (v.hi - v.lo) * val
        if isinstance(v, str):
            return self._input(v, cx.look, cx.vibe, cx.energy)
        return float(v) if _isnum(v) else 0.0

    def _cycles(self, L: _Layer, cx: _Ctx) -> list:
        """A layer's oscillator variants for this frame (computed once per frame per layer)."""
        vs = cx.cyc.get(L.id)
        if vs is None:
            vs = cx.cyc[L.id] = self._variants(L, cx.st, cx.t, cx.snap, cx.look, cx.trans_s, cx.min_period_s,
                                               L.attack_s)
        return vs

    def _phase(self, L: _Layer, s: _Slot, m: tuple[int, int], cx: _Ctx, gn: _Group, tt: float, factor: float = 1.0
               ) -> tuple[float, float]:
        """(w, period_s): a member's place in the layer's own cycle at ``tt`` (unwrapped cycles / ``factor``, minus
        its lag), from the layer's dominant period variant."""
        vs = self._cycles(L, cx)
        if not vs:
            return 0.0, 0.0
        vw, src, off, div, per = max(vs, key=lambda v: v[0])
        sp = self._input(L.spread, cx.look, cx.vibe, cx.energy)
        lag = self._lag(L, sp, s, m, cx, gn) if sp else 0.0
        if cx.co:                                                # docs/ENSEMBLE.md §7.2: canon, its waves run late
            tt -= cx.co.get(s.id, 0.0)
        cyc = (beat_at(src, tt) - off) / div if src is not None else tt / div
        return cyc / factor - lag, per * factor

    def _beam(self, a: str, v: Any, L: _Layer, s: _Slot, m: tuple[int, int], cx: _Ctx, gn: _Group, tt: float
              ) -> Optional[float]:
        caps = s.caps or {}
        if a == "zoom":
            z = caps.get("zoom") if isinstance(caps.get("zoom"), Mapping) else {}
            lo = float(z["min"]) if _isnum(z.get("min")) else None
            hi = float(z["max"]) if _isnum(z.get("max")) else None
            if v == "default":
                b = s.setup.get("beam") if isinstance(s.setup.get("beam"), Mapping) else {}
                d = b.get("zoom", s.neutral.get("zoom"))
                return float(d) if _isnum(d) else None
            if v in ("narrow", "wide", "mid"):
                if lo is None or hi is None:
                    return None
                return lo if v == "narrow" else hi if v == "wide" else (lo + hi) / 2.0
            x = self._sized(v, self._val(v, L, s, m, cx, gn, tt), s, cx)
            return _clamp(x, lo, hi) if lo is not None and hi is not None else x
        if v == "default":
            b = s.setup.get("beam") if isinstance(s.setup.get("beam"), Mapping) else {}
            d = b.get(a, s.neutral.get(a))
            return float(d) if _isnum(d) else None
        return _c01(self._sized(v, self._val(v, L, s, m, cx, gn, tt), s, cx))

    @staticmethod
    def _sized(v: Any, x: float, s: _Slot, cx: _Ctx) -> float:
        """An oscillating beam value's swing round its middle x the light's role motion (docs/ENSEMBLE.md §7.2)."""
        if cx.ms and isinstance(v, _Osc):
            k = cx.ms.get(s.id)
            if k is not None:
                mid = 0.5 * (v.lo + v.hi)
                return mid + (x - mid) * k
        return x

    @staticmethod
    def _slots(spec: Any, slots: Sequence[Any]) -> list[int]:
        """A gobo layer's ``slot``: a name (case-insensitive part of the slot's name), a number, a list of them, or
        auto (every slot that isn't Open), as slot numbers this light has."""
        rows = [sl for sl in slots if isinstance(sl, Mapping) and _isnum(sl.get("slot"))]

        def one(x: Any) -> Optional[int]:
            if isinstance(x, int) and not isinstance(x, bool):
                return x if any(sl["slot"] == x for sl in rows) else None
            if isinstance(x, str):
                for sl in rows:
                    if x.lower() in str(sl.get("name") or "").lower():
                        return int(sl["slot"])
            return None
        if spec == "auto":
            return [int(sl["slot"]) for sl in rows if not sl.get("open") and
                    str(sl.get("name") or "").strip().lower() != "open"]
        if isinstance(spec, tuple):
            return [x for x in (one(v) for v in spec) if x is not None]
        x = one(spec)
        return [x] if x is not None else []

    def _counter(self, on: str, cx: _Ctx, tt: float) -> int:
        """Which candidate a stepping layer shows: bars or phrases on the clock (free-running without one), or the
        look's number (``section`` and ``look``: the engine sees the director's looks, not the song's sections)."""
        if on in ("bar", "phrase", "beat"):
            b = beat_at(cx.src, tt)
            if on == "beat":
                return int(math.floor(b))
            bars = math.floor((b - cx.bar_anchor) / cx.bpb)
            return int(bars if on == "bar" else bars // cx.phrase_bars)
        if on in ("section", "look"):
            return cx.look_n
        return 0

    def _spin(self, p: Mapping[str, Any], cx: _Ctx, gn: _Group) -> Optional[float]:
        sp = p.get("spin")
        if sp is None:
            return None
        if sp == "flow":
            f = gn.flow
            d = f.get("dir") if isinstance(f, Mapping) else "still"
            v = f.get("speed") if isinstance(f, Mapping) else 0.0
            sp = (-1.0 if d == "ccw" else 1.0) * (float(v) if _isnum(v) else 0.0)
        elif sp == "pace":
            sp = self._look_value(cx.look, "motion.speed")
        return _clamp(float(sp) * float(p.get("spin_scale", 1.0)), -1.0, 1.0)

    # -- positions (§5.3, §5.4) ----------------------------------------------------------------------------------------
    def _calibrated(self, s: _Slot, cx: _Ctx) -> bool:
        return s.pose is not None and s.pos is not None and cx.sm is not None and cx.view is not None

    def _aim(self, s: _Slot, fs: _FixState, cx: _Ctx, q: Sequence[float], lid: Optional[str],
             current: Optional[Sequence[float]] = None) -> Optional[tuple[float, float]]:
        """Inverse kinematics through the pose (space.aim): the light's own pan/tilt for room point ``q``, within its
        range and soft limits, nearest the current command. Out of reach: the nearest it can, listed ``unreachable``."""
        fn = getattr(cx.sm, "aim", None)
        if fn is None:
            return None
        cur = current if current is not None else (fs.cmd or s.home)
        try:
            got = fn(s.pose, q, current=(cur[0], cur[1]), **s.aimkw)
            pan, tilt = float(got["pan"]), float(got["tilt"])
        except Exception:                                        # degrade: counted, the layers below stand
            self._fail("space.aim")
            return None
        if not (math.isfinite(pan) and math.isfinite(tilt)):
            return None
        if got.get("reachable") is False and lid is not None:
            fs.unreachable.add(lid)
        return pan, tilt

    def _call_kw(self, name: str, fn: Any, *a: Any, **kw: Any) -> Any:
        return self._call(name, (lambda: fn(*a, **kw)) if callable(fn) else None)

    def _target(self, s: _Slot, names: Sequence[str], cx: _Ctx, gn: _Group, on: Optional[str] = None
                ) -> Optional[tuple[str, Any]]:
        """§5.3: the first name that resolves for this light: its own saved position (works uncalibrated), with a
        pose a Room map name (``space.resolve``), or a word (home, flow, centre, up, down). ("pt", (pan, tilt)) or
        ("xyz", room point); None when none does."""
        cal = self._calibrated(s, cx)
        for name in names:
            if name in s.positions:
                return "pt", s.positions[name]
            if cal and name not in TARGET_WORDS:
                q = _vec3(self._call("space.resolve", getattr(cx.sm, "resolve", None), cx.view, name))
                if q is not None:
                    return "xyz", q
            if name == "home":
                return "pt", s.home
            if name == "flow":
                f = gn.flow if isinstance(gn.flow, Mapping) else {}
                at = f.get("at")
                if isinstance(at, str) and at in s.positions:
                    return "pt", s.positions[at]
                if cal:
                    q = _vec3(f.get("at_xyz"))
                    if q is None and isinstance(at, str):
                        q = _vec3(self._call("space.resolve", getattr(cx.sm, "resolve", None), cx.view, at))
                    if q is not None:
                        return "xyz", q
            elif name == "centre":
                if cal:
                    q = None
                    if on is not None:
                        q = _vec3(self._call("space.resolve", getattr(cx.sm, "resolve", None), cx.view, on))
                    if q is None:
                        q = _vec3((cx.view.get("room") or {}).get("centre"))
                    if q is not None:
                        return "xyz", q
                else:
                    return "pt", s.home
            elif name in ("up", "down") and cal:
                size = (cx.view.get("room") or {}).get("size")
                if name == "down":
                    return "xyz", (s.pos[0], s.pos[1], 0.0)
                if isinstance(size, (list, tuple)) and len(size) == 3 and _isnum(size[2]):
                    return "xyz", (s.pos[0], s.pos[1], float(size[2]))
        return None

    def _surface(self, cx: _Ctx, name: Optional[str]) -> Optional[Mapping[str, Any]]:
        if name is None or cx.sm is None:
            return None
        got = self._call("space.surface_of", getattr(cx.sm, "surface_of", None), cx.view, name)
        if isinstance(got, Mapping) and all(_vec3(got.get(k)) is not None for k in ("origin", "u", "v")):
            return got
        return None

    def _along(self, along: str, cx: _Ctx, gn: _Group, L: _Layer, surf: Optional[Mapping[str, Any]],
               centre: Optional[tuple]) -> tuple[float, float]:
        """The pattern's own u axis in its plane (unit 2-vector): on a surface, ``flow`` (or a flow word) projected
        onto it (still or zero: the surface's u), ``u``/``v``; in pan/tilt degrees, u -> pan and v -> tilt."""
        if along in ("u", None):
            return (1.0, 0.0)
        if along == "v":
            return (0.0, 1.0)
        d = along
        if along == "flow":
            d = L.flow_dir or ((gn.flow or {}).get("dir") if isinstance(gn.flow, Mapping) else None) or "still"
        if d == "still" or d not in FLOW_DIRS:
            return (1.0, 0.0)
        if surf is not None and centre is not None and cx.sm is not None:
            fc = _vec3((gn.flow or {}).get("at_xyz")) if isinstance(gn.flow, Mapping) else None
            sym = (gn.flow or {}).get("symmetry") if isinstance(gn.flow, Mapping) else None
            fv = _vec3(self._call_kw("space.flow_vector", getattr(cx.sm, "flow_vector", None), cx.view, d, fc,
                                     centre, symmetry="mirror" if sym == "mirror" else "none"))
            if fv is not None:
                u, v = _vec3(surf["u"]), _vec3(surf["v"])
                a = fv[0] * u[0] + fv[1] * u[1] + fv[2] * u[2]
                b = fv[0] * v[0] + fv[1] * v[1] + fv[2] * v[2]
                n = math.hypot(a, b)
                if n > 1e-6:
                    return (a / n, b / n)
            return (1.0, 0.0)
        if d in ("up", "down"):
            return (0.0, 1.0 if d == "up" else -1.0)
        return (-1.0, 0.0) if d in FLOW_BACK else (1.0, 0.0)

    def _offset(self, mode: str, w: float, cyc: float, L: _Layer, s: _Slot, m: tuple[int, int], rot: float,
                seed: float) -> tuple[float, float]:
        """A pattern's unit offset (u, v) at phase ``w`` (wrapped) / ``cyc`` (unwrapped, drift's)."""
        p = L.p
        tau = 2.0 * math.pi
        if mode == "fan":
            i, n = m
            return ((2.0 * i / (n - 1) - 1.0) if n > 1 else 0.0, 0.0)
        if mode == "sweep":
            tri = 1.0 - 2.0 * min(w, 1.0 - w)
            return (2.0 * _ease(p["ease"], tri) - 1.0, 0.0)
        if p["ease"] != "linear":
            w = _ease(p["ease"], w)
        if mode == "circle":
            return (math.cos(tau * w), rot * math.sin(tau * w))
        if mode == "figure8":
            return (math.sin(tau * w), 0.5 * math.sin(2.0 * tau * w))
        if mode == "ballyhoo":
            return (math.sin(tau * w), (2.0 / 3.0) * math.sin(1.5 * tau * w) + 1.0 / 3.0)
        # drift: three incommensurate sines per axis, a per-member phase seed (a slow wander)
        u = 0.5 * math.sin(tau * cyc + seed) + 0.3 * math.sin(tau * 1.618 * cyc + 2.1 * seed) + \
            0.2 * math.sin(tau * 2.718 * cyc + 3.7 * seed)
        v = 0.5 * math.sin(tau * 1.272 * cyc + 1.3 * seed + 1.0) + 0.3 * math.sin(tau * 2.236 * cyc + 0.7 * seed) + \
            0.2 * math.sin(tau * 3.142 * cyc + 5.1 * seed)
        return (u, v)

    def _position(self, L: _Layer, m: tuple[int, int], s: _Slot, fs: _FixState, cx: _Ctx, gn: _Group, tp: float,
                  prev: list, prev_q: Optional[tuple]) -> Optional[tuple[tuple[float, float], Optional[tuple]]]:
        """A position layer's own (pan, tilt) for this member at ``tp``, and the room point it aims at (or None).
        None: nothing resolves for this light (it stays where the layers below put it)."""
        p = L.p
        mode = p["mode"]
        cal = self._calibrated(s, cx)
        if mode == "home":
            return (s.home[0], s.home[1]), None
        if mode == "tilt_wave":
            sz = p["size_deg"] * self._fit(L, m, s, fs, cx, gn, tp, None)[0] * (cx.ms.get(s.id, 1.0) if cx.ms else 1.0)
            w, _per = self._phase(L, s, m, cx, gn, tp)
            return (prev[0], prev[1] + sz * (2.0 * shape(p["fx"], w) - 1.0)), prev_q
        if mode == "lift":
            if not cal:
                return None
            d = L.flow_dir or ((gn.flow or {}).get("dir") if isinstance(gn.flow, Mapping) else None)
            sign = 1.0 if d == "up" else -1.0 if d == "down" else 0.0
            if sign == 0.0:
                return (prev[0], prev[1]), prev_q
            q0 = prev_q
            if q0 is None:
                bd = _vec3(self._call("space.beam_dir", getattr(cx.sm, "beam_dir", None), s.pose, prev[0], prev[1]))
                hit = self._call("space.ray_hit", getattr(cx.sm, "ray_hit", None), cx.view, s.pos, bd) \
                    if bd is not None else None
                q0 = _vec3(hit[0]) if isinstance(hit, (list, tuple)) and hit else None
            if q0 is None:
                return None
            dz = self._val(p["lift"], L, s, m, cx, gn, tp) * _c01(self._val(L.depth, L, s, m, cx, gn, tp)) * sign
            q1 = (q0[0], q0[1], q0[2] + dz)
            pt = self._aim(s, fs, cx, q1, L.id)
            return (pt, q1) if pt is not None else None
        if mode == "aim":
            tg = self._target(s, p["centre"], cx, gn, p.get("on"))
            if tg is None:
                return None
            if tg[0] == "pt":
                return (tg[1][0], tg[1][1]), None
            pt = self._aim(s, fs, cx, tg[1], L.id)
            return (pt, tg[1]) if pt is not None else None
        if mode == "bounce":
            return self._bounce(L, m, s, fs, cx, gn, tp)
        # the patterns: in a surface's (u, v) metres through the pose, else in the light's own degrees. Where it is
        # drawn (centre, surface, axes) is worked out once per light, layer, space and flow; each frame moves w only
        fr = self._pattern_frame(L, m, s, fs, cx, gn)
        flip, rsign, seed, ax = fr[1], fr[2], fr[3], fr[4]
        fan = self._val(p["fan"], L, s, m, cx, gn, tp) if mode == "fan" else 1.0
        if fr[0] == "surf":
            surf, u0, v0 = fr[5], fr[6], fr[7]
            spt = cx.sm.surface_point
            size = p["size"] * fan

            def at(w: float, cyc: float, share: float, cur: Optional[Sequence[float]] = None
                   ) -> Optional[tuple[tuple[float, float], tuple]]:
                du, dv = self._offset(mode, w, cyc, L, s, m, rsign, seed)
                du *= flip
                sz = size * share
                try:
                    q = spt(surf, u0 + sz * (du * ax[0] - dv * ax[1]), v0 + sz * (du * ax[1] + dv * ax[0]))
                except Exception:                                # degrade: counted, the layers below stand
                    self._fail("space.surface_point")
                    return None
                pt = self._aim(s, fs, cx, q, L.id, current=cur)
                return (pt, q) if pt is not None else None
            return self._run_pattern(L, m, s, fs, cx, gn, tp, at, mode)
        c0 = fr[5]
        size_deg = p["size_deg"] * fan

        def at_deg(w: float, cyc: float, share: float, cur: Optional[Sequence[float]] = None
                   ) -> Optional[tuple[tuple[float, float], None]]:
            du, dv = self._offset(mode, w, cyc, L, s, m, rsign, seed)
            du *= flip
            sz = size_deg * share
            return (c0[0] + sz * (du * ax[0] - dv * ax[1]), c0[1] + sz * (du * ax[1] + dv * ax[0])), None
        return self._run_pattern(L, m, s, fs, cx, gn, tp, at_deg, mode)

    def _pattern_frame(self, L: _Layer, m: tuple[int, int], s: _Slot, fs: _FixState, cx: _Ctx, gn: _Group
                       ) -> tuple:
        """Where a pattern is drawn for this light (cached per output by layer, light, space and flow):
        ("surf", flip, rsign, seed, axes, surface, u0, v0) on a room surface through the pose, else ("deg", flip,
        rsign, seed, axes, (pan, tilt) centre) in its own degrees (uncalibrated, or no surface to draw on)."""
        f = gn.flow if isinstance(gn.flow, Mapping) else {}
        key = (L.id, s.id, cx.flow_key, f.get("id"), f.get("dir"), f.get("at"))
        got = cx.st.frames.get(key)
        if got is not None:
            return got
        p = L.p
        mode = p["mode"]
        cal = self._calibrated(s, cx)
        on = p.get("on") or ("floor" if mode != "sweep" else None)
        tg = self._target(s, p["centre"], cx, gn, on)
        surf = None
        centre_q = None
        if cal and tg is not None:
            if tg[0] == "xyz":
                centre_q = tg[1]
            else:                                                # an own position: where its beam lands
                bd = _vec3(self._call("space.beam_dir", getattr(cx.sm, "beam_dir", None), s.pose, tg[1][0],
                                      tg[1][1]))
                hit = self._call("space.ray_hit", getattr(cx.sm, "ray_hit", None), cx.view, s.pos, bd) \
                    if bd is not None else None
                centre_q = _vec3(hit[0]) if isinstance(hit, (list, tuple)) and hit else None
            if centre_q is not None:
                surf = self._surface(cx, on if on is not None else
                                     next((n for n in p["centre"] if n not in TARGET_WORDS), None) or "floor")
        flip = 1.0
        if L.spread_mode == "mirror":
            if s.pos is not None and cx.sm is not None and cx.view is not None:
                lat = self._call("space.lateral", getattr(cx.sm, "lateral", None), cx.view, s.pos)
                flip = -1.0 if _isnum(lat) and lat > 0 else 1.0
            else:
                i, n = m
                flip = -1.0 if i >= n / 2.0 and n > 1 else 1.0
        rot = p["rotate"]
        if rot == "flow":
            fd = L.flow_dir or f.get("dir")
            rot = "ccw" if fd == "ccw" else "cw"
        rsign = -1.0 if rot == "cw" else 1.0
        seed = (zlib.crc32(s.id.encode()) % 1000) / 1000.0 * 2.0 * math.pi
        got = None
        if surf is not None and centre_q is not None:
            uv = self._call("space.surface_uv", getattr(cx.sm, "surface_uv", None), surf, centre_q)
            if isinstance(uv, (list, tuple)) and len(uv) == 2 and all(_isnum(x) for x in uv) and \
                    callable(getattr(cx.sm, "surface_point", None)):
                got = ("surf", flip, rsign, seed, self._along(p["along"], cx, gn, L, surf, centre_q), surf,
                       float(uv[0]), float(uv[1]))
        if got is None:
            if tg is None:
                c0 = s.home
            elif tg[0] == "pt":
                c0 = tg[1]
            else:
                c0 = self._aim(s, fs, cx, tg[1], L.id) or s.home
            got = ("deg", flip, rsign, seed, self._along(p["along"], cx, gn, L, None, None), (c0[0], c0[1]))
        if len(cx.st.frames) > FIT_CACHE_MAX:
            cx.st.frames.clear()
        cx.st.frames[key] = got
        return got

    def _run_pattern(self, L: _Layer, m: tuple[int, int], s: _Slot, fs: _FixState, cx: _Ctx, gn: _Group, tp: float,
                     at: Callable, mode: str) -> Optional[tuple]:
        ms = cx.ms.get(s.id, 1.0) if cx.ms else 1.0             # docs/ENSEMBLE.md §7.2: its role's motion x the size
        if mode == "fan":
            return at(0.0, 0.0, ms)
        share, factor = self._fit(L, m, s, fs, cx, gn, tp, at)
        cyc, _per = self._phase(L, s, m, cx, gn, tp, factor)
        return at(cyc - math.floor(cyc), cyc, share * ms)

    def _fit(self, L: _Layer, m: tuple[int, int], s: _Slot, fs: _FixState, cx: _Ctx, gn: _Group, tp: float,
             at: Optional[Callable]) -> tuple[float, float]:
        """Degrade, never trip (§5.5): (size share, period factor) so the pattern's peak speed fits under SPEED_FIT x
        the light's: first shrink (to min_size_share), then double the period on the grid and fit again. Measured
        over one cycle when the pattern's period or size changes (cached per output)."""
        _w, per = self._phase(L, s, m, cx, gn, tp)
        if per <= 0.0 or not s.moves:
            return 1.0, 1.0
        cal = at is not None and s.pose is not None
        key = (L.id, s.id, round(per, 4), cal, cx.flow_key, (gn.flow or {}).get("id") if isinstance(gn.flow, Mapping)
               else None)
        got = cx.st.fits.get(key)
        if got is None:
            mc = cx.prog.motion
            vp, vt = s.max_speed[0] * SPEED_FIT, s.max_speed[1] * SPEED_FIT

            def ratio(share: float, factor: float) -> float:
                pts = []
                cur = None
                for k in range(FIT_SAMPLES + 1):
                    w = k / FIT_SAMPLES
                    if at is None:                               # tilt_wave: the shape's own swing
                        r = ((0.0, L.p["size_deg"] * share * (2.0 * shape(L.p["fx"], w) - 1.0)), None)
                    else:
                        r = at(w, w, share, cur)
                    if r is None:
                        return 0.0
                    cur = r[0]
                    pts.append(r[0])
                step = per * factor / FIT_SAMPLES
                worst = 0.0
                for a, b in zip(pts, pts[1:]):
                    worst = max(worst, abs(b[0] - a[0]) / step / vp, abs(b[1] - a[1]) / step / vt)
                return worst
            share, factor = 1.0, 1.0
            r = ratio(1.0, 1.0)
            if r > 1.0:
                share = max(mc.min_size_share, 1.0 / r)
                r = ratio(share, 1.0)
                n = 0
                while r > 1.0 + 1e-6 and n < MAX_DOUBLINGS:
                    factor *= 2.0
                    n += 1
                    r1 = ratio(1.0, factor)
                    share = 1.0 if r1 <= 1.0 else max(mc.min_size_share, 1.0 / r1)
                    r = ratio(share, factor)
            got = (share, factor)
            if len(cx.st.fits) > FIT_CACHE_MAX:
                cx.st.fits.clear()
            cx.st.fits[key] = got
        if got != (1.0, 1.0):
            fs.slowed[L.id] = f"size x{got[0]:.2f}, period x{got[1]:g}"
        return got

    def _bounce(self, L: _Layer, m: tuple[int, int], s: _Slot, fs: _FixState, cx: _Ctx, gn: _Group, tp: float
                ) -> Optional[tuple[tuple[float, float], Optional[tuple]]]:
        """Hops between ``points`` on the beat or bar (§5.4), pre-rolled so the beam arrives on the line (§5.5): the
        target switches its travel time early; a hop the light can't make in ``max_travel_share`` of its period
        doubles the period on the grid (``slowed``)."""
        p = L.p
        mc = cx.prog.motion
        pts: list[tuple[tuple[float, float], Optional[tuple]]] = []
        for name in p["points"]:
            key = (L.id, s.id, name, cx.flow_key, (gn.flow or {}).get("id") if isinstance(gn.flow, Mapping) else None)
            got = cx.st.targets.get(key, False)
            if got is False:
                tg = self._target(s, (name,), cx, gn)
                if tg is None:
                    got = None
                elif tg[0] == "pt":
                    got = ((tg[1][0], tg[1][1]), None)
                else:
                    pt = self._aim(s, fs, cx, tg[1], L.id, current=s.home)
                    got = (pt, tg[1]) if pt is not None else None
                if len(cx.st.targets) > FIT_CACHE_MAX:
                    cx.st.targets.clear()
                cx.st.targets[key] = got
            if got is not None:
                pts.append(got)
        if not pts:
            return None
        n = len(pts)
        if n == 1:
            return pts[0]
        acc = (s.max_speed[0] / mc.accel_s if mc.accel_s > 0 else math.inf,
               s.max_speed[1] / mc.accel_s if mc.accel_s > 0 else math.inf)

        def trav(a: Sequence[float], b: Sequence[float]) -> float:
            return max(travel_s(b[0] - a[0], s.max_speed[0], acc[0]), travel_s(b[1] - a[1], s.max_speed[1], acc[1]))
        hop_beats = 1.0 if p["hop"] == "beat" else float(cx.bpb)
        anchor = 0.0 if p["hop"] == "beat" else cx.bar_anchor
        worst = max(trav(pts[k][0], pts[(k + 1) % n][0]) for k in range(n))
        factor = 1.0
        while worst > mc.max_travel_share * hop_beats * cx.beat_s * factor and factor < 2.0 ** MAX_DOUBLINGS:
            factor *= 2.0
        if factor > 1.0:
            fs.slowed[L.id] = f"hop period x{factor:g}"
        sp = self._input(L.spread, cx.look, cx.vibe, cx.energy)
        lag = self._lag(L, sp, s, m, cx, gn) if sp else 0.0
        div = hop_beats * factor
        if cx.co:                                                # docs/ENSEMBLE.md §7.2: canon, its hops run late
            tp -= cx.co.get(s.id, 0.0)

        def hop(tt: float) -> float:
            return (beat_at(cx.src, tt) - anchor) / div - lag
        k = math.floor(hop(tp))
        cur, nxt = pts[k % n], pts[(k + 1) % n]
        tr = trav(cur[0], nxt[0])
        if math.floor(hop(tp + tr - cx.dt * 0.5)) > k:
            return nxt
        return cur

    # -- the motion stage (§5.5) ---------------------------------------------------------------------------------------
    def _move(self, s: _Slot, fs: _FixState, t: float, target: Sequence[float], mc: _Motion, *,
              dark_ok: bool = False, light_at: Optional[float] = None) -> tuple[float, float, bool]:
        """Commanded (pan, tilt) toward ``target`` within the light's speed, acceleration and soft limits, and
        whether it must be dark now (a dark move: going dark, moving, or settling)."""
        tgt = [_clamp(target[0], s.limits[0], s.limits[1]), _clamp(target[1], s.limits[2], s.limits[3])]
        if abs(tgt[0] - target[0]) > 1e-9 or abs(tgt[1] - target[1]) > 1e-9:
            fs.clamped.add("limits")
        if fs.cmd is None:
            fs.cmd, fs.vel, fs.t_m = [s.home[0], s.home[1]], [0.0, 0.0], t
        dt = 0.0 if fs.t_m is None else _clamp(t - fs.t_m, 0.0, DT_MAX_S)
        fs.t_m = t
        acc = (s.max_speed[0] / mc.accel_s if mc.accel_s > 0 else math.inf,
               s.max_speed[1] / mc.accel_s if mc.accel_s > 0 else math.inf)
        cmd, vel = fs.cmd, fs.vel
        jump = max(abs(tgt[0] - cmd[0]), abs(tgt[1] - cmd[1]))
        if fs.dark == 0 and dark_ok and jump > mc.dark_move_min_deg:
            fs.dark = 1                                          # dark first: the move waits out the fall
            fs.t_move = t + max(0.0, s.fall_s - s.pos_s) + dt
        if fs.dark == 1:
            if t < fs.t_move:
                fs.vel = [0.0, 0.0]
                return cmd[0], cmd[1], True
            fs.dark = 2
        prev = fs.tgt or tgt
        fs.tgt = tgt
        cmd[0], vel[0] = _slew1(cmd[0], vel[0], tgt[0], abs(tgt[0] - prev[0]) > 1e-9, s.max_speed[0], acc[0], dt)
        cmd[1], vel[1] = _slew1(cmd[1], vel[1], tgt[1], abs(tgt[1] - prev[1]) > 1e-9, s.max_speed[1], acc[1], dt)
        if fs.dark == 2:
            if max(abs(tgt[0] - cmd[0]), abs(tgt[1] - cmd[1])) <= ARRIVED_DEG:
                fs.dark = 3                                      # arrived: light again after the settle
                fs.t_light = t + s.pos_s - s.rise_s + mc.settle_s
                if light_at is not None:
                    fs.t_light = max(fs.t_light, light_at - s.rise_s)
            return cmd[0], cmd[1], True
        if fs.dark == 3:
            if t < fs.t_light:
                return cmd[0], cmd[1], True
            fs.dark = 0
        return cmd[0], cmd[1], False

    def _held_frame(self, s: _Slot, h: _Hold, fs: _FixState, t: float, te: float, mc: _Motion) -> dict:
        """A held fixture's frame (§5.7): its values glide toward the hold's target (pan/tilt first order within its
        speed; intensity and the beam over smooth_ms; the rest at once). Out of everything else."""
        now = fs.hold_now
        if now is None or fs.hold_seq != h.seq:                  # a new hold starts from where it is now
            now = dict(fs.last_attrs or s.neutral)
            if s.moves:
                c = fs.cmd or [s.home[0], s.home[1]]
                now["pan"], now["tilt"] = c[0], c[1]
            now["intensity"] = fs.prev if fs.prev is not None else fs.last_inten
            now["colour"] = list(fs.last_colour)
            fs.hold_seq = h.seq
            fs.t_m = t
        dt = 0.0 if fs.t_m is None else _clamp(t - fs.t_m, 0.0, DT_MAX_S)
        fs.t_m = t
        k = 1.0 - math.exp(-dt / h.smooth_s) if h.smooth_s > 0 else 1.0
        new = dict(now)
        for a, v in h.target.items():
            cur = now.get(a)
            if a in ("pan", "tilt") and _isnum(v) and _isnum(cur):
                step = (v - cur) * k
                lim = (s.max_speed[0] if a == "pan" else s.max_speed[1]) * dt
                new[a] = cur + _clamp(step, -lim, lim)
                if abs(v - new[a]) < 1e-6:
                    new[a] = v
            elif a in GLIDE_ATTRS and _isnum(v) and _isnum(cur):
                new[a] = cur + (v - cur) * k
            else:
                new[a] = v
        fs.hold_now = new
        fs.held = True
        if s.moves:
            fs.cmd = [float(new.get("pan", s.home[0])), float(new.get("tilt", s.home[1]))]
            fs.vel = [0.0, 0.0]
        inten = _c01(float(new.get("intensity", 1.0))) if _isnum(new.get("intensity")) else 1.0
        col = new.get("colour") if isinstance(new.get("colour"), list) else list(fs.last_colour)
        fs.prev, fs.t_prev = inten, t
        fs.last_inten, fs.last_colour = inten, col
        ff = {"intensity": inten, "colour": list(col), "rgb": [col[0] * inten, col[1] * inten, col[2] * inten],
              "t_eval_ms": te}
        attrs = None
        if s.enc:
            attrs = {a: v for a, v in new.items() if a not in ("intensity", "colour")}
            ff["attrs"] = attrs
            fs.last_attrs = attrs
        if s.channels:
            ff["dmx"] = self._encode(s, inten, col, attrs)
        return ff

    def _encode(self, s: _Slot, inten: float, colour: Sequence[float], attrs: Optional[Mapping[str, Any]]) -> bytes:
        """fixture_encode.encode with the attrs for a mover (§2.4), else (or when it fails) colour_mix.encode."""
        if s.enc and attrs is not None:
            enc = self._program.enc_mod
            fn = getattr(enc, "encode", None) if enc is not None else None
            got = self._call("fixture_encode.encode", fn, s.info, s.type, inten, colour, attrs) if fn else None
            if isinstance(got, (bytes, bytearray)):
                return bytes(got)
        return colour_mix.encode(s.info, s.type, inten, colour)

    @staticmethod
    def _override(fid: str, ident: Mapping[str, tuple], follow: Mapping[str, tuple], t: float
                  ) -> Optional[tuple[float, list, bool]]:
        """(intensity, colour, identify?) for a fixture identifying at ``t`` or not following the music; else None."""
        i = ident.get(fid)
        if i is not None and t < i[0]:
            return 1.0, i[1], True
        f = follow.get(fid)
        return (f[0], f[1], False) if f is not None else None

    def _fixed(self, s: _Slot, inten: float, col: Sequence[float], te: float, fs: Optional[_FixState] = None,
               ident: bool = False) -> dict:
        """A FixtureFrame at ``inten`` x ``col`` (identify, a follow value): no layer, hit, master or cap. A mover
        keeps its last position (identify opens its shutter)."""
        ff = {"intensity": inten, "colour": list(col), "rgb": [col[0] * inten, col[1] * inten, col[2] * inten],
              "t_eval_ms": te}
        attrs = None
        if s.enc:
            attrs = dict(fs.last_attrs) if fs is not None and fs.last_attrs else dict(s.neutral)
            if ident and "shutter" in s.attrs:
                attrs["shutter"] = {"mode": "open"}
            ff["attrs"] = attrs
        if s.channels:
            ff["dmx"] = self._encode(s, inten, list(col), attrs)
        return ff

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
        return _LayerNow(layer=L, mode="osc", d=d, spread=spread,
                         variants=self._variants(L, st, t, snap, look, trans_s, min_period_s, attack_s),
                         attack_s=attack_s)

    def _variants(self, L: _Layer, st: _OutState, t: float, snap: Optional[Mapping[str, Any]], look: Any,
                  trans_s: float, min_period_s: float, attack_s: float) -> list:
        """A layer's oscillator for this frame: [(weight, src clock body | None, offset beats, div, period_s)], the
        old and new period crossfading over the transition."""
        src = snap if snap is not None else (None if L.phase == "time" else self._free_run())
        variants = []
        if L.phase == "time":
            per = self._period(L, look)
            for w, p in st.fade(L.id, per, t, trans_s):
                variants.append((w, None, 0.0, p, p))
            return self._coarsen(variants, min_period_s, st, L.id)
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
        return self._coarsen(variants, min_period_s, st, L.id)

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
