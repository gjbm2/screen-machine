"""director: the art director (docs/SHOW_ENGINE.md §8.2-§8.8; README "Stage 3 > director.py").

One component, ``director``, ticked at 10 Hz on show-brain. Each tick composites the layers of §8.2, lowest first:

  L0 vibe       the mode's target -> ``vibes.effective`` -> bounds and policies (every behaviour is a field)
  L1 music      the vibe's ``follow`` inputs (energy, onsets, dynamics, register, harmony) -> numbers, and show.yaml
                ``director.bands`` -> words; liveness holds 10 s after the features go stale, then fades over 20 s
  L2 listener   confident beliefs (the listener only changes a word on two agreeing reads): words first; a small
                trajectory bias on energy, slewed at most 0.1 a second; its style of approach (README S5.22):
                approach.intensity is the band base's genre term (slewed), approach.transitions and .change set
                transition_style and change_rate under a person's or the feel's own and over the band row's
  L3 people     Calmer/Wilder steps, a palette pick, Keep this look, House lights, Lights low (and Describe it)
  L4 momentary  Next look, on the next bar (at most ``next_max_wait_s``)
  L5 caps       strobe only when the vibe allows it

  energy = clamp_to_vibe(centre + follow_amount * conf * liveness * (music - 0.5) + llm_bias + steps * step_size)

and allocates one look per output:
  * **lamps** (every patch.yaml device that carries r/g/b and isn't a screen): placement -> show.yaml ``lamps`` role ->
    that role's fields (palette_role, follow_screen, white_k, chroma_max, avoid_hues, motion; a string value names a
    vibe field). Then x level (with the floor), or Lights low, or House lights;
  * **screens** (patch.yaml ``kind: screen``, room_service/dmx/patch.py's vocabulary): the emphasised palette role at
    full value, NEVER scaled by level, floor, Lights low, House lights or a dip (the screens rule, §8.3).

Nothing here knows a mode, vibe, lamp or screen by name. Looks change by the vibe's ``change_looks`` (§8.5):
  * ``phrase``  on the clock's phrase lines (gate beat|dmx, bar_conf >= 0.5), at least min_bars apart
                (32 / 16 / 8 at change_rate 0 / 0.5 / 1); the clock unsure: as ``time``;
  * ``time``    after dwell_s[0] at an energy-novelty point, and at dwell_s[1] regardless;
  * ``between_pieces``  once per pause: no strike for >= 2 s, no key down, damper < 0.1 (PianoView), after
                dwell_s[0]; without any notes yet: as ``time``.
Quiet or talking (with ``relax_on_talk``), and no audio at all (liveness run down after the 10 s hold and the 20 s
fade, nobody playing): relax to the base look once, then no changes until the sound comes back (§8.5). The behaviour
thresholds are show.yaml ``director`` keys (DIRECTOR_FALLBACK: the starting points).
Each change is announced (``room.event`` look_change, at_ms >= 350 ms ahead where it can be); at at_ms the new
``room.look``, ``ctx.section_change()`` (GOBO +1, the screens' scene change) and the crossfade. The director's own
changes keep the palette and move the lamps' roles on (a new allocation); a song change (sound again after >= 8 s of
silence) or a tap (Next look, a palette pick) moves through the vibe's ranked palettes.

**Sections, builds and drops** (Stage 4, §8.5; README S4.8), from structure's ``ctx.music_context()`` and its events
(``on_music_event``). Without structure, everything above carries on unchanged.
  * ``phrase`` vibes: a predicted section_change (a known repeat) lands a look at its at_ms, with that letter's
    remembered look; a confirmed one lands at its at_ms unless that is more than ``director.sections.max_late_bars``
    after the boundary, when the section shows inside the look instead (level, accent density and spread move by
    ``express_gain`` x the energy step from the last section, within the vibe's ranges, over one beat). min_bars
    doesn't apply. ``time`` and ``between_pieces`` vibes: a section only shows inside the look.
  * Section memory: per track, letter -> {palette, variation, look_id}; cleared on track_change (which also lets the
    palette move on unless its transition's change is small, and replaces the silence test as the song-change signal
    while structure runs).
  * GOBO: while a vibe plays the director owns it: every look that lands steps it once, whatever caused it
    (section, repeat, drop, track, its own), and nothing the vibe holds back does (change_looks time or
    between_pieces, Keep this look, relaxing, a late section, no token). structure steps it only while we are neutral.
  * Peak tokens: the vibe's ``peak_tokens_per_hour``, a sliding 60-minute budget; only a drop within
    ``director.peaks.after_build_s`` of a build spends one. A line structure calls off (drop_armed with eta_beats
    None) releases a reservation ("disarmed") or refunds the token its line spent ("missed": no drop came).
  * Genre on room.direction: the listener's (confident, fresh or holding: README S5.22, owner 18 Oct), then
    music.context's (the local ML's, fresh, at genre_min_conf), then the vibe's (``words_from`` in the snapshot).
  * The build -> drop grammar (only with a vibe, beat_lock and the clock's gate ``beat``), lamps and DMX ONLY: during
    a build the accent subdivision steps 1/2 -> 1/4 -> 1/8, saturation rises toward the vibe's top and the lamps
    narrow toward the accent colour; an armed drop with a token dips the lamps for the last ``dip_bars``, then cuts
    to a new look with a unison hit on the lamps at the line; an unarmed drop with a token hits at once and cuts at
    the first bar line structure.events.min_lead_s ahead (the screens' lead, §5.4); without a token the drop shows
    inside the look; a breakdown lifts the floor and slows and
    desaturates the lamps. Screens are never dipped or hit (the hit only carries lamps). The hit is LookData.hit
    (with ``predicted`` and show.yaml ``director.build.hit_swell_ms``), never palette_fn: each rig output takes it by
    its caps (looks.py), and ``maestro_palette_fn`` lays it over the virtual Maestro's lamps as Hue takes it (a
    predicted line's hit at the line, an unforeseen drop's as a slow swell, §5.4). LookData.dip says the pre-drop dip
    is on (no music.hit flashes over it).
  * Song ends (README S5.17; owner 17 Oct): structure hands every track_end_armed, track_end and track_change to
    ``track_transition`` while it builds it, and the choice goes on that room.event (``transition``), so the renderer
    runs it for that change: through dark across a gap (fade-dark: out_s as the old song ends, in_s with the new
    one), else a cut on the new song's first downbeat (the clock at segue.cut_gate) or a crossfade, biased by the
    feel's transition_style (show.yaml director.track_transitions.styles; no feel: "default"), and ``change`` big
    (a new scene family and palette) or small (the same) from the two songs' genre, key, tempo, energy and artist.
    A person's hold (Keep this look, their change pending) puts ``held: true`` on the event instead, and no look of
    ours fires. Our own look lands at the same junction: a fade-dark track_end dips the lamps to the vibe's floor
    over out_s (never off; released by the new look, a sound_back, or dark_max_s), and the track_change's look lands
    on the first bar line the screens' lead allows at or after its at_ms, crossing over as the event says. This
    holds for every change_looks (a song change is the junction), not only phrase vibes.
  * Screens' palette (agreed with 2f and 04, 16 Oct): when the look's palette changes to a renderer catalog name, it
    goes to every live screen through the room (PATCH /api/v1/screens/{id}/look {palette, palette_transition_s});
    an art palette has no renderer name, so the screens keep their own (the snapshot says so).

**Light scenes** (README S5.24; light_scenes.py, the scenes builder's, 18 Oct). While a palette resolves (a feel, or
director.no_feel), the director picks the lamps' light scene from config/live/light_scenes.yaml: a weighted random
draw (seeded) by how well each scene's tags fit room.direction (stream_service/tags.py's rule), never the one showing
now. It picks when a feel is set, at each look that lands, at a song change and on a person's Next look (with no feel
no look lands, so those two pick at once), and when none is set yet; never while Keep this look holds (a person's own
Next look still picks). The pick and its why go in room.look (``light_scene``, ``light_scene_why``) and the
snapshot's ``light_scene``; no scenes: null, and the look engine runs show.yaml's looks.layers. Every drop the director
hears (structure's drop, a cue's forecast drop at its at_ms) goes to the look engine as LookData ``light_scene_drop``
(one a beat), whatever the peak tokens say, so a scene with drop hits flashes at the line.

**The screens' hand-pick hold** (Stage 5, README S5.8; agreed with 2f, 16 Oct). Our look PATCHes carry
``source: "show_service"`` unless this room boot refused it (the probe: a 2xx with it, or any ``screen.look`` event
carrying a ``source``, is ``yes``; a 400 ``bad_request`` is the old room: ``no``, and the PATCH goes again at once
without it; a new ``ctx.room_boot()`` probes again). Room's ``screen.look`` events come in through ``on_room_event``.
One with a ``palette`` that isn't ours (ours: source ``show_service``, or the palette we last sent that screen within
``director.screens.own_echo_s``) is a person's pick: while ``hold_hand_picks`` is on, the director stops sending the
screens' palette (to every screen), shows the chip ``screen_pick`` ("Colours: <label> (picked on a screen)"), and
counts as engaged. It ends at the next mode change, Back to auto, or the chip's ×; then what was sent is forgotten,
so the director's palette goes back to every live screen at once. The lamps never change for it. Persisted with the
other nudges.

**Neutral.** No target, or a vibe that isn't in vibes.yaml: ``palette_fn`` returns None (artnet_out keeps its fixed
colour), ``set_look_out`` and ``set_gate_ref`` are left alone (put back to their defaults once, if we had moved them),
and room.direction / room.look are still published with ``vibe: null``.

``palette_fn(t)`` (the rig's look engine, any thread) returns the dict built on show-brain, swapped by reference;
only a crossfade's OKLab lerp runs in the call. ``maestro_palette_fn(t)`` (show-artnet) is the same plus the drop's
hit as Hue takes it (README S5.14).

**set_look_out** (artnet_out's and the rig's): ``hue_depth`` (the accent depth, 0 unless the clock's gate is beat),
``beat_lock`` (the vibe's own) and ``beat_depth`` (the accent depth before the gate, for DMX at middle confidence,
§5.4; artnet_out never gets it). A key role's white (``white_k``) is tinted toward the palette at most ``chroma_max``
from that white (``white_tint``), so the key light stays near key_cct (§8.8).

**Composition and balance** (README S5.18; SHOW_ENGINE §8.10; compose.py, pure). Every tick, before the look is
composed: the composer's inputs (features.gate and lufs_s, liveness, the piano's velocity, talking, a fade cue from
structure's outro or an armed song end, tension, build_progress) go through ``compose.step`` (the director owns its
State), the NEW enabled-set reader (``compose.enabled``: live screens, room members exactly ``dmx``) picks the
appliances, and ``compose.plan_one`` gives each one its plan per dimension, by the feel's limits (vibes.yaml
``compose``; no feel or no block: show.yaml director.compose.defaults) and its role weight (a lamp role's ``dynamics``,
else director.compose.weights). show.yaml director.compose.mode (hot-reloaded):
  * ``observe`` (the default): the plan goes out on room.look as ``compose`` (LookData.compose) and NOTHING else
    changes: room.direction, palette_fn, set_look_out and the publish cadence are exactly as before;
  * ``drive``: room.direction's ``intensity`` is the screens' music depth (``depth``; observe: 1.0), it gains
    ``pulse_depth`` (``pulse``) and ``peak`` (the build/drop words: the feel's energy top, its moods and transition
    style, else the top band's), and intensity and pulse_depth join the publish-on-change fields; pace's tempo is
    folded by octaves with stream_service's ONE range (config/live/music_response.json tempo.fold_min / fold_max,
    director.compose.response_file; missing: [70, 180) and a listed problem), so a half-time lock no longer reads
    slow; artnet_out's lamps take ``set_look_out(plan=...)``: the accent depth before the gate on their own ladder
    (beat; else the clock's dmx beats at show.yaml looks.middle_depth; else none) times each lamp's ``pulse``, and
    with no beat the swell follows each lamp's ``level`` instead of the raw energy. Back to observe clears it.
Steps 3-5 (the screens' picture on INTENSITY, the APL headroom, figure and ground) are later: ``apl`` is None today
and the cut guard is the room's cut phase.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import math
import os
import pathlib
import random
import statistics
import time
import urllib.parse
from typing import Any, Callable, Optional

from . import compose as comp
from . import contract
from . import light_scenes as lscenes
from . import palette as pal
from .vibes import Vibes

log = logging.getLogger("show_service.director")

# -- fixed vocabulary and timing ---------------------------------------------------------------------------------------
SCREEN_KIND = "screen"              # patch.yaml device kind (room_service/dmx/patch.py KINDS): the renderer's fixtures;
                                    # also a fixtures.yaml type's kind (contract.FIXTURE_KINDS)
LAMP_KIND = "lamp"                  # contract.FIXTURE_KINDS: a fixtures.yaml type's kind for a lamp (Stage 5)
COLOUR_ATTR = "colour"              # contract TypeInfo.attrs: a type the engine can colour
RGB_FIELDS = ("r", "g", "b")
RENDERER_CATALOG_PATH = "/renderer/api/catalog"      # stream_service's catalog through room's one front door
ART_PATHS = ("/api/v1/screens/{id}/art",                 # the live art feed: the TV's art now, as on the glass
             "/api/v1/screens/{id}/snapshots/anchor.png")  # else the still room took at the last cut
# The art palette's sources, first that gives colours wins (show.yaml director.art.sources): the UNMASKED art first
# (75 / owner, 17 Oct: the light mask is display dimming, not the art's colour, so the glass reads near-black at
# night). An http(s) template is fetched as is; a path goes to room (ctx.room_get).
ART_SOURCES = ("http://127.0.0.1:5000/output/{id}.jpg",) + ART_PATHS
HOUSE_FOLLOWING = ("dmx", "taken_over")                 # room hue values that are not a stored house light state
GATE_REF_DEFAULT_LUFS = -30.0       # dsp.Features.gate_ref_lufs's own default: put back when a vibe stops setting it
STALE_S = 2.0                       # features older than this are stale (they come at 25 Hz)
CLOCK_STALE_S = 3.0                 # music.clock comes at 10 Hz
HARMONY_STALE_S = 3.0
HOLD_S, FADE_S = 10.0, 20.0         # §8.5 no audio: hold, then drift to the base look
RECOVER_S = 5.0                     # fresh again (not from cold): liveness ramps back over this
TAU_E_S, TAU_ONSET_S, TAU_TENSION_S, TAU_CHORD_S = 2.0, 2.0, 2.0, 1.0
TAU_NOVELTY_FAST_S, TAU_NOVELTY_SLOW_S = 3.0, 30.0
NOVELTY = 0.12                      # starting point (show.yaml director.novelty): |fast - slow| energy, a change point
RELAX_TAU_S = 3.0
LLM_CONF = 0.6                      # a listener word counts at this confidence (§8.2)
#: room.why's look line: why the last look changed, in words (the reasons _announce is called with)
LOOK_REASON_WORDS = {"phrase": "a new phrase", "section": "a new section", "repeat": "a section came back",
                     "drop": "the drop", "track": "a new song", "dwell": "time for a change",
                     "novelty": "the music changed", "relax": "the room went quiet", "between_pieces": "between pieces",
                     "next": "someone pressed Next look", "palette": "someone picked a palette"}
#: room.look's light_scene_why: the light scene's own reasons, beside LOOK_REASON_WORDS (README S5.24)
LIGHT_REASON_WORDS = {"feel": "a new feel", "first": "the first light scene", "scenes": "the light scenes changed"}
LIGHT_SCENE_SEED = 0                # the light-scene draw's seed (a fixed seed, as palette.from_image's k-means)
BAND_BASE_TERMS = ("genre", "rate", "clarity", "moods")   # director.bands.base.weights' keys (README S5.21)
GENRE_MIN_CONF = 0.4                # the local ML's genre word counts at this confidence (director.genre_min_conf;
                                    # structure's style rule: a leader's share, 0.5-0.95 on clear tracks, ~0.3 mixed)
LLM_SLEW_PER_S = 0.1                # §8.2: numbers slewed <= 0.1 per update
# Starting points, overridable in show.yaml (director.listener_bias): the energy nudge per listener trajectory word
# (contract.TRAJECTORY_STATES), x its confidence.
LLM_TRAJECTORY_BIAS = {"building": 0.05, "peak": 0.1, "sustaining": 0.0, "steady": 0.0, "releasing": -0.05,
                       "winding_down": -0.1}
# README S5.22 (owner, 18 Oct): the listener's approach.change word -> room.direction's change_rate while no feel sets
# it (show.yaml director.listener_change; starting points, the band rows' own values: still 0.15 .. peak 0.75)
LLM_CHANGE_RATE = {"slow": 0.15, "medium": 0.45, "fast": 0.75}
EMPHASIS_EDGES = (0.4, 0.75)        # starting point (director.emphasis_edges): arousal base | accent | highlight
EMPHASIS_HYST = 0.05
LOOK_MIN_LEAD_S = 0.35              # an announced change lands at least this far ahead (>= 250 ms, plus a tick)
ANNOUNCE_HORIZON_S = 4.0            # a phrase line is decided when it is this close (or one bar)
GRID_CONF = 0.5                     # bar_conf needed to use the clock's bars and phrases
ENTRY_FADE_S = 8.0                  # starting point (director.entry_fade_s): lamps fade in from house (§8.6)
GLIDE_S = 0.12                      # a tick's small change glides over about one tick
HOUSE_GLIDE_S = 0.3                 # House lights: "instant"
ROLE_GLIDE_S = 3.0                  # new art, or a palette that vanished: the lamps glide over this
TIME_FADE_S = 3.0                   # §8.5: changes by time crossfade over 2-4 s
VARIATION_BARS = 64                 # starting point (director.variation_bars): a variation while Keep holds (§8.5)
PAUSE_S, DAMPER_UP = 2.0, 0.1       # starting points (director.between_pieces pause_s, damper_up; §8.1)
SILENCE_SONG_S = 8.0                # starting point (director.new_song_after_s): sound after this silence = a new song
SILENT_GATE = 0.05
CATALOG_POLL_S = 300.0
COLOUR_CACHE = 512                  # lamp colours by their quantised inputs (the tick's budget is 1 ms p95)
QUANT = 0.005                       # saturation and drift depth are quantised to this for the cache
LOG_MEM = 500
LOG_MAX_BYTES = 1 << 20
# Starting points, overridable in show.yaml (director.transitions): how a look change crosses over for each vocab.yaml
# transition_style word. A style with no entry, or no style, crossfades over TIME_FADE_S.
TRANSITIONS = {"gentle": {"style": "crossfade", "beats": 4.0, "seconds": 4.0},
               "lively": {"style": "crossfade", "beats": 1.0, "seconds": 1.0},
               "wild": {"style": "cut", "beats": None, "seconds": 0.05}}
NEUTRAL_TRANSITION = {"style": "crossfade", "beats": None, "seconds": 3.0}
SLOW_TRANSITION = {"style": "crossfade", "beats": None, "seconds": 4.0}     # relaxing to the base look
DROP_CUT = {"style": "cut", "beats": None, "seconds": 0.05}                  # §8.5: a drop is a hard look cut
# Stage 4 (README S4.8)
CONTEXT_STALE_S = 3.0               # music.context comes at 1 Hz
RHYTHM_STALE_S = 1.0                # README S4.6: a music.rhythm frame over 1 s old is stale
E_HIST_N = 6000                     # energy samples kept for section energies (10 min at 10 Hz)
SUBDIVISIONS = (0.5, 0.25, 0.125)   # §8.5: a build steps the accent subdivision 1/2 -> 1/4 -> 1/8
TOKEN_WINDOW_S = 3600.0             # peak_tokens_per_hour: at most that many spends in any 60 minutes
STRUCTURE_REASONS = ("section", "repeat", "drop", "track")   # look changes structure's events caused
EVENTS_LEAD_S = 1.1                 # show.yaml structure.events.min_lead_s (starting point, §5.4): a drop's or a new
                                    # song's look (with its GOBO step) lands on a bar at least this far ahead
SCREEN_LOOK_PATH = "/api/v1/screens/{id}/look"               # room's live look route (PATCH: palette)
SCREENS_RETRY_S = 15.0              # a screen whose PATCH failed is tried again after this
PICK_CUSTOM = "custom colours"      # the hold chip's words for a pick that has no name (README S5.8)
PICK_SCENE = "the scene's own"      # ... for palette null: the Room page's own words for "The scene's own colours"
SOURCE_REFUSED = (400, "bad_request")   # the old room's answer to a look PATCH with a key it doesn't know (S5.8)
MUSIC_EVENTS_MAX = 64
NEUTRAL_ROLE = {"palette_role": "base", "follow_screen": False, "chroma_max": "chroma_max", "motion": False}
# Song ends (README S5.17; SHOW_ENGINE §8.5 "Song ends", owner 17 Oct). Starting points, overridable in show.yaml
# (director.track_transitions): how the screens cross a song change. ``styles`` is keyed by vocab.yaml's
# transition_style words, with "default" for no feel or a word with no entry; ``cut``: "never" (a crossfade instead),
# "beat" (a cut on the first downbeat while the clock is at segue.cut_gate or better, else a crossfade), "always"
# (a cut; on the downbeat when the clock allows, else now); ``scale`` multiplies every length.
TRACK_TRANSITIONS = {
    "gap": {"out_s": 2.5, "in_s": 2.0, "min_s": 1.5, "max_s": 4.0, "dark_max_s": 8.0},
    "segue": {"cut_gate": "beat", "crossfade_s": 2.0},
    "styles": {"gentle": {"cut": "never", "scale": 1.5}, "lively": {"cut": "beat", "scale": 1.0},
               "wild": {"cut": "always", "scale": 0.6}, "default": {"cut": "beat", "scale": 1.0}},
    "change": {"genre": True, "key_fifths": 2.0, "tempo_ratio": 0.08, "energy": 0.25, "artist": True,
               "big_share": 0.5, "unknown": "big"}}
DEFAULT_STYLE = "default"
CUT_RULES = ("never", "beat", "always")
GATE_RANK = {"none": 0, "dmx": 1, "beat": 2}        # clock gates, weakest first (contract.ClockData.gate)
TRACK_CUT = {"style": "cut", "beats": None, "seconds": 0.05}   # the director's own look at a song's cut
# Composition and balance (README S5.18)
END_FADE_S = 10.0                   # an armed song end cues the composer's fade until this long after its at_ms
MIDDLE_DEPTH = 0.5                  # show.yaml looks.middle_depth's starting point (looks.py DEFAULT_MIDDLE_DEPTH)

#: show.yaml ``director`` keys the code falls back to when the file leaves one out: the design's own numbers (§8.4,
#: §8.5; starting points, as the module constants above they come from) or the neutral (no swell, no band words). The
#: file's values win.
DIRECTOR_FALLBACK = {
    "steps": {"size_from": "step_size", "max_from": "max_steps"}, "keep_s": 900.0,
    "low": {"level": 0.10, "fade_s": 2.0}, "house": {"white_k": 2700.0, "level": 0.8}, "next_max_wait_s": 4.0,
    "art": {"poll_s": 60.0, "colours": 5, "sources": list(ART_SOURCES), "min_chroma": 0.03, "min_lightness": 0.3},
    "republish_s": 5.0, "accent_depth": {}, "bands": None, "genre_min_conf": GENRE_MIN_CONF,
    "transitions": TRANSITIONS, "listener_bias": LLM_TRAJECTORY_BIAS, "listener_change": LLM_CHANGE_RATE,
    "between_pieces": {"pause_s": PAUSE_S, "damper_up": DAMPER_UP}, "new_song_after_s": SILENCE_SONG_S,
    "novelty": NOVELTY, "variation_bars": float(VARIATION_BARS), "entry_fade_s": ENTRY_FADE_S,
    "emphasis_edges": list(EMPHASIS_EDGES),
    # Stage 4 (§8.5; show.yaml director.sections/peaks/build/breakdown, starting points, the same numbers)
    "sections": {"max_late_bars": 1.0, "express_gain": 0.5, "phase_hint_s": 1.5},
    "peaks": {"after_build_s": 30.0},
    "build": {"step_bars": 4.0, "step_bars_late": 2.0, "narrow": 0.6, "dip_level": 0.25, "dip_bars": 1.0,
              "hit_beats": 1.0, "hit_swell_ms": 300.0},
    "breakdown": {"floor_add": 0.1, "motion_scale": 0.5, "saturation_scale": 0.8},
    # the screens' palette (agreed with 2f and 04, 16 Oct): palette_transition_s is a starting point (the renderer's
    # own default, LIVE_V1 §1.11); send_palette false turns it off (hot-reloaded). Stage 5 (README S5.8): a person's
    # pick on a screen holds (hold_hand_picks, hot-reloaded); own_echo_s is agreed with 2f (16 Oct)
    "screens": {"send_palette": True, "palette_transition_s": 4.0, "hold_hand_picks": True, "own_echo_s": 3.0},
    # with no feel set (owner, 17 Oct: the director always resolves a palette): the palettes it picks from, as a
    # vibe's list (art = the screens' own stills). [] keeps the old neutral (artnet_out's fixed colour). Only the
    # colours: every other behaviour stays neutral until a feel is set.
    "no_feel": {"palettes": []},
    # song ends (README S5.17)
    "track_transitions": TRACK_TRANSITIONS,
    # composition and balance (README S5.18; compose.py): observe | drive, the loudness, range, headroom and limits
    "compose": comp.COMPOSE_FALLBACK}
#: Stage 4's show.yaml director keys: section -> key -> (lowest, highest) allowed, or bool
STAGE4_KEYS: dict[str, dict[str, Any]] = {
    "sections": {"max_late_bars": (0.0, 64.0), "express_gain": (0.0, 2.0), "phase_hint_s": (0.0, 10.0)},
    "peaks": {"after_build_s": (0.0, 600.0)},
    "build": {"step_bars": (0.25, 64.0), "step_bars_late": (0.25, 64.0), "narrow": (0.0, 1.0), "dip_level": (0.0, 1.0),
              "dip_bars": (0.0, 16.0), "hit_beats": (0.0, 16.0), "hit_swell_ms": (300.0, 10000.0)},
    "breakdown": {"floor_add": (0.0, 1.0), "motion_scale": (0.0, 1.0), "saturation_scale": (0.0, 1.0)},
    "screens": {"send_palette": bool, "palette_transition_s": (0.0, 60.0), "hold_hand_picks": bool,
                "own_echo_s": (0.0, 60.0)}}


def _c01(x: float) -> float:
    return 0.0 if not x > 0.0 else 1.0 if x > 1.0 else x


def _num(v: Any, default: Optional[float] = None) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return float(v)
    return default


def _r3(v: Any) -> Optional[float]:
    """A 0..1 number rounded for room.direction, or None when there is none."""
    x = _num(v)
    return None if x is None else round(_c01(x), 3)


def _ema(prev: Optional[float], x: float, dt: float, tau: float) -> float:
    if prev is None:
        return x
    return prev + (x - prev) * (1.0 - math.exp(-dt / tau)) if dt > 0 else prev


def _lerp(rng: Any, u: float) -> float:
    lo, hi = float(rng[0]), float(rng[1])
    return lo + (hi - lo) * _c01(u)


def _call(fn: Optional[Callable], *a: Any, **kw: Any) -> Any:
    """An input slot: None when missing or when it raises (degrade, never fail a tick)."""
    if fn is None:
        return None
    try:
        return fn(*a, **kw)
    except Exception:
        return None


# -- the clock grid (the Link formula, as clock.py's consumer helpers; no import of another module) -----------------------
class Grid:
    """Bars and phrases from a music.clock body. ``ok``: usable for bar/phrase timing (gate beat|dmx, bar_conf >= 0.5)."""

    def __init__(self, body: Optional[dict], now_s: float) -> None:
        self.body = body
        b = body or {}
        self.bpm = _num(b.get("bpm"), 0.0) or 0.0
        self.gate = b.get("gate") if isinstance(b.get("gate"), str) else "none"
        self.conf = _num(b.get("conf"), 0.0) or 0.0
        self.bar_conf = _num(b.get("bar_conf"), 0.0) or 0.0
        self.bpb = max(1, int(_num(b.get("beats_per_bar"), 4) or 4))
        self.pb = max(1, int(_num(b.get("phrase_bars"), 8) or 8))
        self.bar_anchor = int(_num(b.get("bar_anchor_beat"), 0) or 0)
        self.anchor_s = (_num(b.get("anchor_ms"), 0.0) or 0.0) / 1000.0
        self.beat_anchor = int(_num(b.get("beat_at_anchor"), 0) or 0)
        self.has_grid = self.bpm > 0.0
        self.ok = self.has_grid and self.gate in ("beat", "dmx") and self.bar_conf >= GRID_CONF
        self.beat_s = 60.0 / self.bpm if self.has_grid else None
        self.bar_s = self.beat_s * self.bpb if self.beat_s else None
        self.phrase_s = self.bar_s * self.pb if self.bar_s else None

    def beat_at(self, t_s: float) -> float:
        return self.beat_anchor + (t_s - self.anchor_s) * self.bpm / 60.0 if self.has_grid else math.nan

    def t_of_beat(self, k: float) -> float:
        return self.anchor_s + (k - self.beat_anchor) * 60.0 / self.bpm

    def bar_at(self, t_s: float) -> int:
        return math.floor((self.beat_at(t_s) - self.bar_anchor) / self.bpb + 1e-9)

    def next_line(self, after_s: float, bars: int = 1) -> tuple[float, int]:
        """(time, bar index) of the first bar line that is a multiple of ``bars`` bars at or after ``after_s``."""
        rel = (self.beat_at(after_s) - self.bar_anchor) / (self.bpb * bars)
        n = math.ceil(rel - 1e-9)
        bar = n * bars
        return self.t_of_beat(self.bar_anchor + bar * self.bpb), bar


# -- patch.yaml (read-only: devices -> lamps and screens) ---------------------------------------------------------------
def parse_patch(raw: Any) -> tuple[dict[str, dict], dict[str, dict]]:
    """(lamps, screens): ``{device id: {"kind", "screen", "placement", "label"}}`` for every device whose profile
    carries r/g/b. A device's kind is its ``kind:`` or its id's prefix (the patch format's own rule)."""
    if not isinstance(raw, dict):
        raise ValueError("patch: not a mapping")
    profiles = raw.get("profiles") or {}
    devices = raw.get("devices") or {}
    if not isinstance(profiles, dict) or not isinstance(devices, dict):
        raise ValueError("patch: profiles and devices must be mappings")
    lamps: dict[str, dict] = {}
    screens: dict[str, dict] = {}
    for did, d in devices.items():
        if not isinstance(d, dict):
            continue
        prof = profiles.get(d.get("profile"))
        chans = prof.get("channels") if isinstance(prof, dict) else None
        fields = {c.get("field") for c in chans if isinstance(c, dict)} if isinstance(chans, list) else set()
        if not set(RGB_FIELDS) <= fields:
            continue
        kind = str(d.get("kind") or str(did).split(".", 1)[0])
        info = {"kind": kind, "screen": d.get("screen") if isinstance(d.get("screen"), str) else None,
                "placement": d.get("placement") if isinstance(d.get("placement"), str) else None,
                "label": str(d.get("label") or did)}
        (screens if kind == SCREEN_KIND else lamps)[str(did)] = info
    return lamps, screens


def layout_from_rig(view: Any) -> Optional[tuple[dict[str, dict], dict[str, dict]]]:
    """(lamps, screens) from the fixture registry (README S5.8, Stage 5): the ``lamp`` fixtures whose type has
    ``colour`` in its attrs, and the ``screen`` fixtures, each ``{"kind", "screen", "placement", "label", "role",
    "house"}`` (a fixture's own ``role`` wins over its placement; ``house`` is fixtures.yaml's, what House lights and
    an entry fade start from when room has no stored state for it). None without a view that has fixtures (no rig,
    or an error with no last good registry): the caller keeps patch.yaml's layout."""
    fixtures = view.get("fixtures") if isinstance(view, dict) else None
    if not isinstance(fixtures, dict) or not fixtures:
        return None
    types = view.get("types") if isinstance(view.get("types"), dict) else {}
    lamps: dict[str, dict] = {}
    screens: dict[str, dict] = {}
    for fid, f in fixtures.items():
        if not isinstance(f, dict):
            continue
        t = types.get(f.get("type")) if isinstance(types.get(f.get("type")), dict) else {}
        kind = t.get("kind") or f.get("kind")
        info = {"kind": kind, "screen": f.get("screen") if isinstance(f.get("screen"), str) else None,
                "placement": f.get("placement") if isinstance(f.get("placement"), str) else None,
                "label": str(f.get("label") or fid), "role": f.get("role") if isinstance(f.get("role"), str) else None,
                "house": f.get("house") if isinstance(f.get("house"), dict) else None}
        if kind == SCREEN_KIND:
            screens[str(fid)] = info
        elif kind == LAMP_KIND and COLOUR_ATTR in (t.get("attrs") or ()):
            lamps[str(fid)] = info
    return lamps, screens


def white_tint(white: pal.Rgb, colour: pal.Rgb, amount: float, cap: Optional[float]) -> pal.Rgb:
    """A role with a white (``white_k``): that white, tinted toward ``colour`` by ``amount`` (the look's saturation),
    with the tint's OKLab (a, b) distance from the white at most ``cap`` (the role's ``chroma_max``; README S5.14).
    A colour's neutral axis is re-anchored on the white in proportion to how achromatic it is, so a grey or white
    palette leaves the white exactly as it is (the key light stays at key_cct, §8.8), and a fully coloured one moves
    toward the colour itself, as far as the cap lets it (no cap: the old mix, the colour at amount 1)."""
    Lw, aw, bw = pal.to_oklab(white)
    Lc, ac, bc = pal.to_oklab(colour)
    Cw, Cc = math.hypot(aw, bw), math.hypot(ac, bc)
    s = min(1.0, Cc / Cw) if Cw > 1e-9 else 1.0
    u = _c01(amount)
    da, db, dL = u * (ac - s * aw), u * (bc - s * bw), u * s * (Lc - Lw)
    d = math.hypot(da, db)
    k = 1.0 if cap is None or d <= cap else max(0.0, cap) / d
    return pal.from_oklab((Lw + dL * k, aw + da * k, bw + db * k))


# -- song ends: the transition (README S5.17) ------------------------------------------------------------------------------
def key_fifths(a: Optional[str], a_mode: Optional[str], b: Optional[str], b_mode: Optional[str]) -> Optional[int]:
    """Steps between two keys on the circle of fifths, 0..6: the same, parallel (C / C minor) and relative (C / A
    minor) keys are 0 (structure's own rule for a key jump); others compare their relative majors. None: unknown."""
    if a not in contract.KEY_NAMES or b not in contract.KEY_NAMES:
        return None
    pa, pb = contract.KEY_NAMES.index(a), contract.KEY_NAMES.index(b)
    if pa == pb:
        return 0
    ma = (pa + 3) % 12 if a_mode == "minor" else pa
    mb = (pb + 3) % 12 if b_mode == "minor" else pb
    d = ((mb - ma) * 7) % 12
    return min(d, 12 - d)


def tempo_off(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """|b / a - 1| with the ratio folded by octaves into [1/sqrt 2, sqrt 2] (x2 and x0.5 are the same tempo)."""
    if not a or not b or a <= 0 or b <= 0:
        return None
    r = b / a
    while r > math.sqrt(2.0):
        r /= 2.0
    while r < 1.0 / math.sqrt(2.0):
        r *= 2.0
    return abs(r - 1.0)


def change_size(old: Optional[dict], new: Optional[dict], rules: dict) -> tuple[str, dict[str, bool]]:
    """("big" | "small", {term: differs}) between two contract.TrackProfile: each term known on both sides votes
    (rules: genre words differ; keys >= key_fifths apart; tempo off by > tempo_ratio; mean energy >= energy apart;
    Sonos's artist differs; a rule set to null/false is off). big when at least big_share of the votes differ;
    ``unknown`` when nothing is known on both sides (a Sonos change at once: the new song isn't heard yet)."""
    terms: dict[str, bool] = {}
    o, n = old if isinstance(old, dict) else {}, new if isinstance(new, dict) else {}
    if rules.get("genre") and isinstance(o.get("genre"), str) and isinstance(n.get("genre"), str):
        terms["genre"] = o["genre"] != n["genre"]
    kf = _num(rules.get("key_fifths"))
    d = key_fifths(o.get("key"), o.get("mode"), n.get("key"), n.get("mode"))
    if kf is not None and d is not None:
        terms["key"] = d >= kf - 1e-9
    tr = _num(rules.get("tempo_ratio"))
    off = tempo_off(_num(o.get("bpm")), _num(n.get("bpm")))
    if tr is not None and off is not None:
        terms["tempo"] = off > tr + 1e-9
    en = _num(rules.get("energy"))
    eo, ew = _num(o.get("energy")), _num(n.get("energy"))
    if en is not None and eo is not None and ew is not None:
        terms["energy"] = abs(ew - eo) >= en - 1e-9
    if rules.get("artist") and isinstance(o.get("artist"), str) and isinstance(n.get("artist"), str) \
            and o["artist"] and n["artist"]:
        terms["artist"] = o["artist"].casefold() != n["artist"].casefold()
    if not terms:
        return str(rules.get("unknown") or "big"), terms
    share = sum(1 for v in terms.values() if v) / len(terms)
    return ("big" if share >= float(rules.get("big_share", 0.5)) - 1e-9 else "small"), terms


def choose_track_transition(cfg: dict, kind: str, ev: dict, facts: dict, style_word: Optional[str], *,
                            dark: bool, last: Optional[dict] = None) -> dict:
    """The transition for one track event (contract.TrackTransition), from show.yaml director.track_transitions
    (``cfg``). A gap (track_end via dsp; at a Sonos end, or armed ahead of it: the room silent or the song in its
    outro) fades through dark; a track_change fades in from dark only while the screens are dark (``dark``: this
    junction's track_end chose fade-dark and no sound_back came); anything else is a segue: by the feel's cut rule,
    a cut on the new song's first downbeat with the clock at segue.cut_gate or better, else a crossfade. A sound_back
    repeats ``last`` (the track_end's choice), whose in_s brings the screens back. The feel's ``scale`` lengthens or
    shortens every length (fade halves within gap.min_s..max_s). ``change``: the old and new songs' distance on a
    track_change, ``change.unknown`` before the new song is known."""
    styles = cfg["styles"]
    st = styles.get(style_word or "") or styles.get(DEFAULT_STYLE) or TRACK_TRANSITIONS["styles"][DEFAULT_STYLE]
    scale = float(st.get("scale", 1.0))
    if kind == "track_change":
        change, _terms = change_size(facts.get("old"), facts.get("new"), cfg["change"])
    else:
        change = str(cfg["change"].get("unknown") or "big")
    if kind == "track_end" and ev.get("detail") == "sound_back" and isinstance(last, dict):
        return dict(last)
    gap = cfg["gap"]
    if kind == "track_change":
        through_dark = dark and not facts.get("back")
    elif kind == "track_end" and ev.get("via") == "dsp":
        through_dark = True                               # the end in the sound: a gap, by definition
    else:
        through_dark = bool(facts.get("silent")) or facts.get("phase") == "outro"

    def bound(x: float) -> float:
        return round(max(float(gap["min_s"]), min(float(gap["max_s"]), x * scale)), 3)
    if through_dark:
        return {"style": "fade-dark", "out_s": bound(float(gap["out_s"])), "in_s": bound(float(gap["in_s"])),
                "seconds": None, "on": "now", "change": change, "dark_max_s": round(float(gap["dark_max_s"]), 3)}
    seg = cfg["segue"]
    locked = GATE_RANK.get(str(facts.get("gate")), 0) >= GATE_RANK.get(str(seg["cut_gate"]), 2)
    rule = st.get("cut", "beat")
    if rule == "always" or (rule == "beat" and locked):
        return {"style": "cut", "out_s": None, "in_s": None, "seconds": 0.0, "on": "downbeat" if locked else "now",
                "change": change, "dark_max_s": None}
    return {"style": "crossfade", "out_s": None, "in_s": None, "seconds": round(float(seg["crossfade_s"]) * scale, 3),
            "on": "now", "change": change, "dark_max_s": None}


def parse_track_transitions(raw: Any, probs: list[str]) -> dict:
    """show.yaml director.track_transitions -> the resolved table (TRACK_TRANSITIONS fills what is missing; a bad
    value takes its starting point and is listed)."""
    out = json.loads(json.dumps(TRACK_TRANSITIONS))
    if raw is None:
        return out
    if not isinstance(raw, dict):
        probs.append("director.track_transitions: not a mapping: the starting points used")
        return out
    p = "director.track_transitions"
    for sec, keys in (("gap", ("out_s", "in_s", "min_s", "max_s", "dark_max_s")), ("segue", ("crossfade_s",))):
        got = raw.get(sec)
        if got is None:
            continue
        if not isinstance(got, dict):
            probs.append(f"{p}.{sec}: not a mapping")
            continue
        for k in keys:
            if k in got:
                v = _num(got[k])
                if v is not None and 0.0 <= v <= 60.0:
                    out[sec][k] = v
                else:
                    probs.append(f"{p}.{sec}.{k}: {got[k]!r} is not a number in 0..60")
        if sec == "segue" and "cut_gate" in got:
            if got["cut_gate"] in ("dmx", "beat"):
                out["segue"]["cut_gate"] = got["cut_gate"]
            else:
                probs.append(f"{p}.segue.cut_gate: {got['cut_gate']!r} is not dmx|beat")
    g = out["gap"]
    if g["min_s"] > g["max_s"]:
        probs.append(f"{p}.gap: min_s over max_s: the starting points used")
        g["min_s"], g["max_s"] = TRACK_TRANSITIONS["gap"]["min_s"], TRACK_TRANSITIONS["gap"]["max_s"]
    styles = raw.get("styles")
    if styles is not None:
        if isinstance(styles, dict):
            ok: dict[str, dict] = {}
            for word, v in styles.items():
                sc = _num(v.get("scale", 1.0)) if isinstance(v, dict) else None
                if isinstance(v, dict) and v.get("cut", "beat") in CUT_RULES and sc is not None and 0.0 < sc <= 10.0:
                    ok[str(word)] = {"cut": v.get("cut", "beat"), "scale": sc}
                else:
                    probs.append(f"{p}.styles.{word}: needs cut never|beat|always and scale in 0..10")
            ok.setdefault(DEFAULT_STYLE, dict(TRACK_TRANSITIONS["styles"][DEFAULT_STYLE]))
            out["styles"] = ok
        else:
            probs.append(f"{p}.styles: not a mapping")
    ch = raw.get("change")
    if ch is not None:
        if not isinstance(ch, dict):
            probs.append(f"{p}.change: not a mapping")
        else:
            for k in ("genre", "artist"):
                if k in ch:
                    if isinstance(ch[k], bool) or ch[k] is None:
                        out["change"][k] = bool(ch[k])
                    else:
                        probs.append(f"{p}.change.{k}: {ch[k]!r} is not true/false")
            for k, hi in (("key_fifths", 6.0), ("tempo_ratio", 1.0), ("energy", 1.0), ("big_share", 1.0)):
                if k in ch:
                    v = ch[k]
                    if v is None and k != "big_share":
                        out["change"][k] = None                # that term off
                    elif _num(v) is not None and 0.0 <= v <= hi:
                        out["change"][k] = float(v)
                    else:
                        probs.append(f"{p}.change.{k}: {v!r} is not a number in 0..{hi:g}")
            if "unknown" in ch:
                if ch["unknown"] in contract.CHANGE_SIZES:
                    out["change"]["unknown"] = ch["unknown"]
                else:
                    probs.append(f"{p}.change.unknown: {ch['unknown']!r} is not big|small")
    return out


class Director:
    """Component ``director`` (contract.Component). Everything runs on show-brain except ``palette_fn``,
    ``snapshot`` and the ``provides`` readers, which read references swapped there."""

    name = "director"
    tick_hz = contract.BRAIN_TICK_HZ

    def __init__(self, ctx: contract.BrainContext, *, vibes: Optional[Vibes] = None,
                 catalog: Optional[pal.Catalog] = None, patch_path: pathlib.Path = contract.PATCH_FILE,
                 log_path: Optional[pathlib.Path] = contract.DIRECTOR_LOG,
                 wall: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic,
                 vibes_path: Optional[pathlib.Path] = None, light_scenes: Optional[lscenes.Catalogue] = None,
                 rng: Optional[random.Random] = None) -> None:
        self.ctx = ctx
        self._mono = mono
        # light scenes (README S5.24): the process's catalogue (the rig's look engine reads the same one)
        self.light_scenes = light_scenes if light_scenes is not None else lscenes.shared()
        self._ls_rng = rng if rng is not None else random.Random(LIGHT_SCENE_SEED)
        self.light_scene: Optional[str] = None
        self.light_scene_why: Optional[str] = None
        self._ls_at: Optional[float] = None               # when it was picked (monotonic s)
        self._ls_reason = "first"                         # why the next automatic pick happens (none set yet)
        self._ls_drop: Optional[contract.LightSceneDrop] = None   # LookData.light_scene_drop: the last drop line
        self._ls_n = 0
        self._ls_stats = {"picks": 0, "drops": 0}
        self.palettes = catalog if catalog is not None else pal.Catalog()
        self.vibes = vibes if vibes is not None else Vibes(pathlib.Path(vibes_path) if vibes_path is not None
                                                           else contract.VIBES_FILE, palette_names=self.palettes.names)
        self.patch_path = pathlib.Path(patch_path)
        self.log_path = pathlib.Path(log_path) if log_path is not None else None
        self._wall = wall
        # settings (show.yaml)
        self.cfg: dict = json.loads(json.dumps(DIRECTOR_FALLBACK))
        self.lamps_cfg: dict = {"placement_roles": {}, "default_role": None, "roles": {}}
        self.settings_problems: list[str] = []
        # patch (and, Stage 5, the fixture registry: ctx.rig)
        self.lamps: dict[str, dict] = {}
        self.screens: dict[str, dict] = {}
        self.patch_error: Optional[str] = None
        self._patch_layout: tuple[dict, dict] = ({}, {})   # patch.yaml's (lamps, screens), the fallback
        self._rig_seen: Any = None                         # the registry view the layout was last read from
        self._rig_layout: Optional[tuple[dict, dict]] = None
        self.lamps_from = "patch.yaml"                     # "fixtures.yaml" while the registry gives the layout
        self._patch_sig: Optional[tuple] = None
        # the target (L0) and nudges (L3), persisted
        self.target: Optional[contract.VibeTarget] = None
        self.target_source = "none"
        self.target_set_at: Optional[float] = None       # wall epoch
        self.restored = False
        self.mode: Optional[str] = None
        self.steps = 0
        self.palette_pick = contract.PALETTE_AUTO
        self.moods: Optional[list[str]] = None
        self.keep_until: Optional[float] = None          # monotonic
        self.house = False
        self.low = False
        # look state
        self.vs: contract.VibeSettings = self.vibes.effective(None)[0]
        self.known = False
        self.pal_on = False                         # colours resolve (a feel, or director.no_feel.palettes)
        self.vp: dict = {}
        self._vibe_name: Optional[str] = None            # the vibe entered (None: neutral)
        self.entered_s: Optional[float] = None
        self.look_n = 0
        self.look_started_s: Optional[float] = None
        self.look_bar: Optional[int] = None
        self.variation = 0
        self._var_mark_bar: Optional[int] = None
        self._var_mark_s: Optional[float] = None
        self.pal_idx = 0
        self.palette_name: Optional[str] = None          # what the look uses: catalog name | "art" | None (neutral)
        self.pending: Optional[dict] = None              # an announced change: {"at_s", "reason", "by", "advance"}
        self.next_pending = False
        self._pause_used = False
        self._phrase_passed = False
        self._relaxed = False
        self._relax = 0.0
        self._relax_done = False
        self._silent_since: Optional[float] = None
        self._song_change = False
        self._emphasis = 0
        self._band: Optional[int] = None
        self._split_hi = False
        self._swing_hist: collections.deque = collections.deque(maxlen=600)   # (t, heard music) at 1 Hz
        self._band_base: Optional[float] = None   # the last absolute base (README S5.21), for the readback
        # README S5.22: what the listener's confident beliefs say for the style of approach (_llm_guide), and the
        # band base's genre term while the listener sets it (slewed <= LLM_SLEW_PER_S from what it was)
        self._guide: dict = {"genre": None, "intensity": None, "change": None, "transitions": None}
        self._gterm: Optional[float] = None
        self._gterm_t: Optional[float] = None
        self._gterm_from: Optional[str] = None
        self._look_why: Optional[dict] = None     # {"why": words, "t"}: room.why's look line
        self._band_idx: Optional[int] = None      # the band words' row now (held director.bands.dwell_s)...
        self._band_cand: Optional[int] = None     # ...and the row waiting to take over, since
        self._band_since: Optional[float] = None
        # music state
        self._t_prev: Optional[float] = None
        self.lv = 0.0
        self._cold = True
        self._stale_since: Optional[float] = None
        self._e: Optional[float] = None
        self._onset: Optional[float] = None
        self._tension: Optional[float] = None
        self._chord: Optional[float] = None
        self._register: Optional[float] = None
        self._nov_fast: Optional[float] = None
        self._nov_slow: Optional[float] = None
        self._llm_bias = 0.0
        self.music: dict = {}
        # art stills and the catalog API
        self.art: dict[str, dict] = {}                   # screen -> {"colours": [Rgb], "roles", "at_s"}
        self._art_next = 0.0
        self._art_busy = False
        self._art_error: Optional[str] = None
        self._cat_next = 0.0
        self._cat_busy = False
        self._tasks: set = set()
        self._reload_next = 0.0
        # outputs
        self._pal: Optional[tuple] = None                # (t0, t1, frm, to, frm_lab, to_lab), swapped by reference
        self._last_direction: Optional[dict] = None
        self._last_look: Optional[dict] = None
        self._dir_pub_s = -1e9
        self._look_pub_s = -1e9
        self._dir_seq = 0
        self._look_seq = 0
        self._ev_seq = 0
        self._look_out: Optional[tuple] = None
        self._gate_ref: Optional[float] = None
        self.direction: Optional[contract.DirectionData] = None
        self.look: Optional[contract.LookData] = None
        self._roles_key: Optional[tuple] = None
        self._screen_roles: dict[str, contract.PaletteRoles] = {}
        # stats and log
        self._tick_ms: collections.deque = collections.deque(maxlen=600)
        self.looks = 0
        self.events = 0
        self.published = 0
        self._log: collections.deque = collections.deque(maxlen=LOG_MEM)
        self._log_error: Optional[str] = None
        self._snap: dict = {}
        self._vs_pub: Optional[contract.VibeSettings] = None
        self._now_s: Optional[float] = None
        self._restore_check = False                      # the first RoomView after a restore decides (restore())
        self._eff_key: Optional[tuple] = None
        self._eff: tuple = (self.vs, False)
        self._eff_problems: list[str] = []
        self._entry_fade = False
        self._fade_s: Optional[float] = None             # a landed look change: crossfade over this
        self._house_toggled: Optional[float] = None      # House / Low toggled: glide over this
        self._force_look = False
        self._look_transition: Optional[dict] = None
        self._words_now: dict = {}
        self._colour_cache: collections.OrderedDict = collections.OrderedDict()
        self._p95: Optional[float] = None
        self._p95_next = 0.0
        self._pview_key: Optional[tuple] = None
        self._pview: dict = {}
        self._pal_chosen: Optional[str] = None
        # Stage 4: sections, builds, drops and peak tokens (README S4.8), all on show-brain
        self._mev: collections.deque = collections.deque(maxlen=MUSIC_EVENTS_MAX)   # structure's events, not yet read
        self._mc: Optional[dict] = None                  # music.context while fresh
        self._phase_ctx = contract.SECTION_PHASES[0]     # its section.phase
        self._phase_hint: Optional[tuple[str, float]] = None   # (phase, since): an event's phase, until the context
        self._track_id: Optional[int] = None
        self._ctx_track: Optional[tuple] = None          # (id, via, at_s): a new track id seen in the context
        self._sec_mem: dict[str, dict] = {}              # letter -> {"palette", "variation", "look_id"} (this track)
        self._last_boundary_s: Optional[float] = None
        self._e_hist: collections.deque = collections.deque(maxlen=E_HIST_N)   # (t_s, smoothed energy)
        self._x = (0.0, 0.0, 0.0, 0.0)                   # the section's expression: (from, to, t0, t1), in u units
        self._tokens: collections.deque = collections.deque()   # peak-token spends (monotonic s), the last hour
        self._armed: Optional[dict] = None               # a reserved token at a drop line: {"at_s", "section_id"}
        self._last_line_s: Optional[float] = None        # the last armed line that spent its token
        self._hit: Optional[tuple] = None                # (t0, t1, {lamp: rgb}, predicted): the unison hit, in
                                                         # LookData.hit and maestro_palette_fn (README S5.14)
        self._hit_want: Optional[tuple[float, float]] = None   # an unarmed drop's hit, coloured at the next compose
        self._build: Optional[dict] = None               # {"start_bar", "idx", "step_bar"}: the subdivision steps
        self._build_last_s: Optional[float] = None       # the last moment a build was heard
        self._drop_inside_s: Optional[float] = None      # a drop with no token: it shows inside the look
        self._cut_mark: Optional[tuple[float, str]] = None   # (at_s, reason) of the last drop/track look: a section
                                                         # boundary within 2 bars of it is the same boundary
        self._ev_lead = EVENTS_LEAD_S                    # show.yaml structure.events.min_lead_s
        self.gobo_steps = 0                              # GOBO steps (ctx.section_change): one per look that lands
        self.sec_stats: dict[str, Any] = {"events": {k: 0 for k in contract.MUSIC_EVENT_KINDS}, "looks": 0,
                                          "recalled": 0, "kept": 0, "expressed": 0, "tokens_spent": 0,
                                          "tokens_refused": 0, "tokens_refunded": 0}
        self._scr: dict[str, Any] = {"palette": None, "sent": {}, "sent_at": {}, "retry": {}, "busy": False,
                                     "why": "no feel is set", "error": None, "live": [], "sends": 0, "picks": 0,
                                     "own_echoes": 0}
        # Stage 5: the screens' hand-pick hold (README S5.8), all on show-brain
        self.screen_pick: Optional[contract.ScreenPick] = None   # persisted as nudges.screen_pick
        self._look_source = contract.LOOK_SOURCE_STATES[0]       # "unknown": does this room boot take {source}?
        self._look_boot: Optional[str] = None            # the room boot (ctx.room_boot) that _look_source is about
        self._echo: dict[str, tuple[Any, float]] = {}    # screen -> (palette we last PATCHed there, monotonic s)
        # song ends (README S5.17), all on show-brain
        self._junction: Optional[dict] = None            # the song now ending: {"track_id", "dark", "transition",
                                                         # "kind", "held"}, until its track_change
        self._song_dip: Optional[dict] = None            # fade-dark's outgoing half on the lamps: {"t0", "out_s",
                                                         # "in_s", "until", "track_id"} (to the vibe's floor, never off)
        self.ends_stats: dict[str, Any] = {"chosen": {}, "held": 0, "dips": 0, "dip_released": 0}
        # composition and balance (README S5.18), all on show-brain
        self._cstate = comp.State()                      # the composer's memory (compose.step changes only this)
        self._cfg_gen = 0                                # +1 per configure: the limits cache's key
        self._ctab_key: Optional[tuple] = None
        self._ctab: dict = comp.table(None)
        self._csrc = "defaults"                          # "feel" | "defaults": whose limits are in use
        self._cval: list[str] = []                       # the validator's lines for the limits in use
        self._cblock: Optional[dict] = None              # LookData.compose, built before the look
        self._cnom: Optional[dict] = None                # {class: values}: the nominal plans (direction, unplanned lamps)
        self._clamps: Optional[dict] = None              # {member id | "*": (level, pulse)}: artnet_out's lamps (drive)
        self._plan_sent: Optional[dict] = None           # what set_look_out(plan=) last sent (drive)
        self._feat_now: Optional[dict] = None            # this tick's features while fresh
        self._end_armed: Optional[float] = None          # an armed song end's at (s): a fade cue
        self._fold: tuple[float, float] = comp.FOLD_DEFAULT
        self._resp_sig: Optional[tuple] = None
        self._resp_problem: Optional[str] = None
        self._middle_depth = MIDDLE_DEPTH
        self._build_snapshot(mono())

    # ================================================================================================================
    # Component
    # ================================================================================================================
    def provides(self) -> dict:
        return {"vibe": self._vibe_now, "target": self._target_now, "engaged": self._engaged_now,
                "on_mode_change": self.on_mode_change, "on_music_event": self.on_music_event,
                "on_room_event": self.on_room_event, "track_transition": self.track_transition, "held": self._held}

    def on_music_event(self, ev: dict) -> None:
        """structure's room.event (contract.MusicEventData), on show-brain: queued for this tick (structure ticks
        first in the same 10 Hz beat)."""
        if isinstance(ev, dict) and ev.get("kind") in contract.MUSIC_EVENT_KINDS:
            self._mev.append(ev)

    def _held(self) -> bool:
        """A person holds the look: Keep this look, or their change (Next look, a palette pick) still pending."""
        return self.keep_until is not None or (self.pending is not None and self.pending.get("by") == "human")

    def track_transition(self, ev: dict, facts: dict) -> Optional[dict]:
        """ctx.track_transition (README S5.17): structure calls it on show-brain, synchronously, while it builds a
        track_end_armed, track_end or track_change; what it returns goes on that event. {"held": True} while a person
        holds the look (no transition: the renderer leaves the change alone, and our own look change doesn't fire);
        else {"transition": choose_track_transition(...)}, by the feel's transition_style (none: the "default" rule:
        the screens follow songs without a feel too). Remembers this junction, so the track_change knows whether the
        screens went dark."""
        if not isinstance(ev, dict) or ev.get("kind") not in contract.TRACK_EVENT_KINDS:
            return None
        facts = facts if isinstance(facts, dict) else {}
        kind = ev["kind"]
        tid = ev.get("track_id")
        prev = ev.get("prev_track_id") if kind == "track_change" else tid
        j = self._junction if (self._junction is not None and self._junction.get("track_id") == prev) else None
        if self._held():
            self.ends_stats["held"] += 1
            if kind == "track_change":
                self._junction = None
            else:
                self._junction = {"track_id": tid, "dark": False, "transition": None, "kind": kind, "held": True}
            return {"held": True}
        style = self._words(self.vs)["transition_style"] if (self.known and self._vibe_name is not None) else None
        tr = choose_track_transition(self.cfg["track_transitions"], kind, ev, facts, style,
                                     dark=bool(j and j.get("dark")), last=(j or {}).get("transition"))
        self.ends_stats["chosen"][tr["style"]] = self.ends_stats["chosen"].get(tr["style"], 0) + 1
        if kind == "track_change":
            self._junction = None
        elif kind == "track_end":
            back = ev.get("detail") == "sound_back"
            self._junction = {"track_id": tid, "dark": tr["style"] == "fade-dark" and not back,
                              "transition": tr, "kind": kind, "held": False}
        else:
            self._junction = {"track_id": tid, "dark": bool(j and j.get("dark")), "transition": tr, "kind": kind,
                              "held": False}
        self._log_event("transition", {"event": {k: ev.get(k) for k in ("kind", "id", "via", "track_id",
                                                                          "prev_track_id", "detail", "at_ms")},
                                       "style": style, "transition": tr, "facts": {
                                           k: facts.get(k) for k in ("silent", "phase", "gate", "back")},
                                       "old": facts.get("old"), "new": facts.get("new")})
        return {"transition": tr}

    def on_room_event(self, type_: str, data: dict) -> None:
        """A room WS event (autopilot.RoomLink, on show-brain). Only ``screen.look`` matters here (README S5.8): one
        that carries a ``source`` says this room boot takes ours; one with a ``palette`` that isn't ours is a person's
        pick on a screen, which holds the screens' palette while ``hold_hand_picks`` is on."""
        if type_ != contract.ROOM_SCREEN_LOOK or not isinstance(data, dict):
            return
        source = data.get("source")
        if "source" in data:
            self._set_look_source(contract.LOOK_SOURCE_STATES[1], self._look_boot, "event")
        if "palette" not in data:
            return                                       # not a palette change (direction, auto, ...)
        sc = self._scr
        screen = data.get("screen") if isinstance(data.get("screen"), str) else ""
        palette = data["palette"]
        echo = self._echo.get(screen)
        own_s = float(self.cfg["screens"]["own_echo_s"])
        if source == contract.SHOW_SOURCE or (echo is not None and echo[0] == palette
                                              and self._mono() - echo[1] <= own_s):
            sc["own_echoes"] += 1
            return
        sc["picks"] += 1
        scfg = self.cfg["screens"]
        # a hold only means something while the director is sending the screens' palette: a vibe plays and
        # send_palette is on (README S5.14); otherwise (and with hold_hand_picks off) the pick is only logged
        why = None
        if not scfg["hold_hand_picks"]:
            why = "director.screens.hold_hand_picks is off"
        elif not self.pal_on:
            why = "no palette is playing"
        elif not scfg["send_palette"]:
            why = "director.screens.send_palette is off"
        self._log_event("screen_pick", {"what": "picked", "event": dict(data), "held": why is None,
                                        **({"why": why} if why else {})})
        if why is not None:
            return
        self.screen_pick = {"palette": palette, "label": self._pick_label(palette), "screen": screen,
                            "at": round(self._wall(), 3), "source": source if isinstance(source, str) else None}
        self._changed()

    def _pick_label(self, palette: Any) -> str:
        """The chip's words for a pick: the catalog's label for a name, else the name; null (the screen card's "The
        scene's own colours") reads as the scene's own; anything else "custom colours"."""
        if isinstance(palette, str) and palette:
            return self.palettes.label(palette)          # the name itself when the catalog doesn't know it
        if palette is None:
            return PICK_SCENE
        return PICK_CUSTOM

    def _set_look_source(self, state: str, boot: Optional[str], via: str) -> None:
        """The probe's answer for ``boot`` (dropped when the room has restarted since); logged once per change."""
        if boot != self._look_boot or state == self._look_source:
            return
        self._look_source = state
        self._log_event("look_source", {"state": state, "boot": boot, "via": via})

    def _end_pick(self, why: str) -> None:
        """End the hold: forget what was sent, so the director's palette goes back to every live screen at once."""
        pick, self.screen_pick = self.screen_pick, None
        sc = self._scr
        sc["sent"].clear()
        sc["sent_at"].clear()
        sc["retry"].clear()
        self._log_event("screen_pick", {"what": "cleared", "why": why, "pick": pick})

    def _vibe_now(self) -> Optional[contract.VibeSettings]:
        return self._vs_pub

    def _target_now(self) -> Optional[contract.VibeTarget]:
        return self.target

    def _engaged_now(self) -> bool:
        return bool(self._snap.get("engaged"))

    def configure(self, settings: dict) -> None:
        """show.yaml's ``director`` and ``lamps`` sections. A bad value falls back (DIRECTOR_FALLBACK) and is listed."""
        probs: list[str] = []
        s = settings if isinstance(settings, dict) else {}
        d = s.get("director") if isinstance(s.get("director"), dict) else {}
        cfg = json.loads(json.dumps(DIRECTOR_FALLBACK))
        for key in ("keep_s", "next_max_wait_s", "republish_s", "new_song_after_s", "novelty", "variation_bars",
                    "entry_fade_s", "genre_min_conf"):
            if key in d:
                v = _num(d[key])
                if v is not None and v > 0:
                    cfg[key] = v
                else:
                    probs.append(f"director.{key}: {d[key]!r} is not a positive number")
        ad = d.get("art") if isinstance(d.get("art"), dict) else {}
        if "sources" in ad:
            src = ad["sources"]
            if isinstance(src, list) and src and all(isinstance(x, str) and "{id}" in x for x in src):
                cfg["art"]["sources"] = list(src)
            else:
                probs.append(f"director.art.sources: {src!r} is not a list of templates with {{id}}")
        for key in ("min_chroma", "min_lightness"):
            if key in ad:
                v = _num(ad[key])
                if v is not None and v >= 0:
                    cfg["art"][key] = v
                else:
                    probs.append(f"director.art.{key}: {ad[key]!r} is not a number >= 0")
        nf = d.get("no_feel")
        if nf is not None:
            pl = nf.get("palettes") if isinstance(nf, dict) else None
            if isinstance(pl, list) and all(isinstance(x, str) and x for x in pl):
                cfg["no_feel"]["palettes"] = list(pl)
            else:
                probs.append(f"director.no_feel: {nf!r} is not {{palettes: [names]}}")
        ee = d.get("emphasis_edges")
        if ee is not None:
            if (isinstance(ee, list) and len(ee) == 2 and all(_num(x) is not None for x in ee)
                    and 0.0 <= ee[0] <= ee[1] <= 1.0):
                cfg["emphasis_edges"] = [float(ee[0]), float(ee[1])]
            else:
                probs.append(f"director.emphasis_edges: {ee!r} is not [low, high] in 0..1")
        for key, sub in (("steps", ("size_from", "max_from")), ("low", ("level", "fade_s")),
                         ("house", ("white_k", "level")), ("art", ("poll_s", "colours")),
                         ("between_pieces", ("pause_s", "damper_up"))):
            got = d.get(key)
            if got is None:
                continue
            if not isinstance(got, dict):
                probs.append(f"director.{key}: not a mapping")
                continue
            for k2 in sub:
                if k2 in got:
                    v = got[k2]
                    if key == "steps":
                        if isinstance(v, str) or _num(v) is not None:
                            cfg[key][k2] = v
                        else:
                            probs.append(f"director.{key}.{k2}: {v!r}")
                    elif _num(v) is not None and v >= 0:
                        cfg[key][k2] = float(v)
                    else:
                        probs.append(f"director.{key}.{k2}: {v!r} is not a number")
        ad = d.get("accent_depth")
        if isinstance(ad, dict):
            cfg["accent_depth"] = {k: _c01(float(v)) for k, v in ad.items() if _num(v) is not None}
        elif ad is not None:
            probs.append("director.accent_depth: not a mapping")
        bands = d.get("bands")
        if bands is not None:
            b = self._check_bands(bands, probs)
            cfg["bands"] = b
        tr = d.get("transitions")
        if tr is not None:
            if isinstance(tr, dict):
                ok = {}
                for style, v in tr.items():
                    if (isinstance(v, dict) and v.get("style") in ("cut", "crossfade")
                            and all(v.get(k) is None or (_num(v.get(k)) is not None and v[k] >= 0)
                                    for k in ("beats", "seconds"))):
                        ok[str(style)] = {"style": v["style"], "beats": _num(v.get("beats")),
                                          "seconds": _num(v.get("seconds"))}
                    else:
                        probs.append(f"director.transitions.{style}: needs style cut|crossfade, beats, seconds")
                cfg["transitions"] = ok
            else:
                probs.append("director.transitions: not a mapping")
        lb = d.get("listener_bias")
        if lb is not None:
            if isinstance(lb, dict):
                cfg["listener_bias"] = {str(k): max(-0.5, min(0.5, float(v))) for k, v in lb.items()
                                        if _num(v) is not None}
            else:
                probs.append("director.listener_bias: not a mapping")
        lc = d.get("listener_change")
        if lc is not None:
            if isinstance(lc, dict):
                cfg["listener_change"] = {str(k): _c01(float(v)) for k, v in lc.items() if _num(v) is not None}
            else:
                probs.append("director.listener_change: not a mapping")
        for key, spec in STAGE4_KEYS.items():
            got = d.get(key)
            if got is None:
                continue
            if not isinstance(got, dict):
                probs.append(f"director.{key}: not a mapping")
                continue
            for k2, lim in spec.items():
                if k2 not in got:
                    continue
                v = got[k2]
                if lim is bool:
                    if isinstance(v, bool):
                        cfg[key][k2] = v
                    else:
                        probs.append(f"director.{key}.{k2}: {v!r} is not true/false")
                elif _num(v) is not None and lim[0] <= v <= lim[1]:
                    cfg[key][k2] = float(v)
                else:
                    probs.append(f"director.{key}.{k2}: {v!r} is not a number in {lim[0]:g}..{lim[1]:g}")
        cfg["track_transitions"] = parse_track_transitions(d.get("track_transitions"), probs)
        cfg["compose"] = comp.parse_settings(d.get("compose"), probs)          # README S5.18
        self.cfg = cfg
        lk = s.get("looks") if isinstance(s.get("looks"), dict) else {}
        md = _num(lk.get("middle_depth"))                  # the look engine's own key (it lists a bad one)
        self._middle_depth = md if md is not None and 0.0 <= md <= 1.0 else MIDDLE_DEPTH
        stc = s.get("structure") if isinstance(s.get("structure"), dict) else {}
        evc = stc.get("events") if isinstance(stc.get("events"), dict) else {}
        lead = _num(evc.get("min_lead_s"))
        self._ev_lead = lead if lead is not None and lead > 0 else EVENTS_LEAD_S
        lam = s.get("lamps") if isinstance(s.get("lamps"), dict) else {}
        pr = lam.get("placement_roles") if isinstance(lam.get("placement_roles"), dict) else {}
        roles = lam.get("roles") if isinstance(lam.get("roles"), dict) else {}
        clean_roles: dict[str, dict] = {}
        for rname, r in roles.items():
            if not isinstance(r, dict):
                probs.append(f"lamps.roles.{rname}: not a mapping")
                continue
            rr = dict(NEUTRAL_ROLE)
            rr.update(r)
            if rr.get("palette_role") not in contract.PALETTE_ROLES:
                probs.append(f"lamps.roles.{rname}.palette_role: {rr.get('palette_role')!r}: base used")
                rr["palette_role"] = "base"
            ah = rr.get("avoid_hues")
            if ah is not None and not (isinstance(ah, list) and all(
                    isinstance(x, list) and len(x) == 2 and all(_num(y) is not None for y in x) for x in ah)):
                probs.append(f"lamps.roles.{rname}.avoid_hues: not a list of [lo, hi] degrees: ignored")
                rr["avoid_hues"] = None
            dy = rr.get("dynamics")                        # README S5.18: the role's weight W in the composer
            if dy is not None and not (_num(dy) is not None and 0.0 <= dy <= 2.0):
                probs.append(f"lamps.roles.{rname}.dynamics: {dy!r} is not a number in 0..2: "
                             f"director.compose.weights used")
                rr["dynamics"] = None
            clean_roles[str(rname)] = rr
        for pl, rn in pr.items():
            if rn not in clean_roles:
                probs.append(f"lamps.placement_roles.{pl}: {rn!r} is not a role")
        dr = lam.get("default_role")
        if dr is not None and dr not in clean_roles:
            probs.append(f"lamps.default_role: {dr!r} is not a role")
        self.lamps_cfg = {"placement_roles": {str(k): v for k, v in pr.items() if v in clean_roles},
                          "default_role": dr if dr in clean_roles else None, "roles": clean_roles}
        self.settings_problems = probs
        self._roles_key = None
        self._cfg_gen += 1
        self._ctab_key = None
        self._check_response(force=True)

    @staticmethod
    def _check_bands(b: Any, probs: list[str]) -> Optional[dict]:
        if not isinstance(b, dict):
            probs.append("director.bands: not a mapping")
            return None
        edges = b.get("edges")
        words = b.get("words")
        if (not isinstance(edges, list) or not edges or not all(_num(e) is not None for e in edges)
                or sorted(edges) != edges):
            probs.append("director.bands.edges: an ascending list of numbers")
            return None
        if not isinstance(words, dict) or not words:
            probs.append("director.bands.words: a mapping of band -> words")
            return None
        names = list(words)
        n_bands = len(edges) + 1
        split = b.get("split") if isinstance(b.get("split"), dict) else None
        split_band = None
        if split is not None:
            split_band = int(_num(split.get("band"), 1) or 0)    # default: the second band, as music_direction.py
            if not (0 <= split_band < n_bands and isinstance(split.get("by"), str)
                    and _num(split.get("up")) is not None and _num(split.get("down")) is not None):
                probs.append("director.bands.split: needs by, up, down (and band in range)")
                split, split_band = None, None
        want = n_bands + (1 if split is not None else 0)
        if len(names) != want:
            probs.append(f"director.bands.words: {len(names)} bands named, {want} expected: band words off")
            return None
        table = []
        for n in names:
            w = words[n] if isinstance(words[n], dict) else {}
            table.append({"name": n, "moods": [x for x in w.get("moods") or [] if isinstance(x, str)],
                          "fx_moods": [x for x in w.get("fx_moods") or [] if isinstance(x, str)],
                          "transition_style": w.get("transition_style")
                          if isinstance(w.get("transition_style"), str) else None,
                          "change_rate": _c01(w["change_rate"]) if _num(w.get("change_rate")) is not None else None})
        dwell = _num(b.get("dwell_s"), 0.0) or 0.0
        if dwell < 0:
            probs.append(f"director.bands.dwell_s: {b.get('dwell_s')!r} is not >= 0: 0 used")
            dwell = 0.0
        return {"edges": [float(e) for e in edges], "hysteresis": _num(b.get("hysteresis"), 0.0) or 0.0,
                "split": split, "split_band": split_band, "table": table, "dwell_s": dwell,
                "base": Director._check_band_base(b.get("base"), probs)}

    @staticmethod
    def _check_band_base(raw: Any, probs: list[str]) -> Optional[dict]:
        """director.bands.base: how energetic the music is, absolutely (genre_energy per vocab genre word, the onset
        rate that reads 1, the terms' weights), and how far the normalised energy swings it (swing round its running
        median over swing_window_s). None: the band words read the heard energy alone."""
        if raw is None:
            return None
        if not isinstance(raw, dict):
            probs.append("director.bands.base: not a mapping: off")
            return None
        ge = raw.get("genre_energy") if isinstance(raw.get("genre_energy"), dict) else {}
        w = raw.get("weights") if isinstance(raw.get("weights"), dict) else {}
        out = {"genre_energy": {str(k): _c01(float(v)) for k, v in ge.items() if _num(v) is not None},
               "rate_full": _num(raw.get("rate_full"), 0.0) or 0.0,
               "weights": {k: max(0.0, _num(w.get(k), 0.0) or 0.0) for k in BAND_BASE_TERMS},
               "swing": max(0.0, _num(raw.get("swing"), 0.0) or 0.0),
               "swing_max": max(0.0, _num(raw.get("swing_max"), 1.0) or 0.0),
               "swing_max_down": max(0.0, _num(raw.get("swing_max_down"), _num(raw.get("swing_max"), 1.0)) or 0.0),
               "swing_window_s": max(1.0, _num(raw.get("swing_window_s"), 60.0) or 60.0),
               "phase_lift": {str(k): float(v) for k, v in (raw.get("phase_lift") or {}).items()
                              if _num(v) is not None} if isinstance(raw.get("phase_lift"), dict) else {}}
        if out["rate_full"] <= 0 or not any(out["weights"].values()):
            probs.append("director.bands.base: needs rate_full > 0 and a weight: off")
            return None
        return out

    def restore(self, saved: dict) -> None:
        """state.json's ``director``: the target and nudges. A passed Keep is dropped; the first mode that differs
        from the saved one clears every nudge."""
        if not isinstance(saved, dict) or not saved:
            return
        t = saved.get("target")
        if isinstance(t, dict) and isinstance(t.get("vibe"), str):
            tgt: contract.VibeTarget = {"vibe": t["vibe"], "listening": bool(t.get("listening", False))}
            if isinstance(t.get("overrides"), dict) and t["overrides"]:
                tgt["overrides"] = dict(t["overrides"])
            self.target = tgt
            self.target_source = saved.get("target_source") if saved.get("target_source") in ("mode", "api") \
                else "mode"
            self.target_set_at = _num(saved.get("target_set_at"))
            self.restored = True
        self.mode = saved.get("mode") if isinstance(saved.get("mode"), str) else None
        n = saved.get("nudges") if isinstance(saved.get("nudges"), dict) else {}
        self.steps = int(_num(n.get("steps"), 0) or 0)
        p = n.get("palette")
        self.palette_pick = p if isinstance(p, str) and p else contract.PALETTE_AUTO
        m = n.get("moods")
        self.moods = ([x for x in m if isinstance(x, str)] or None) if isinstance(m, list) else None
        ku = _num(n.get("keep_until"))
        now_w = self._wall()
        self.keep_until = self._mono() + (ku - now_w) if ku is not None and ku > now_w else None
        self._restore_check = self.mode is not None
        self.house = bool(n.get("house", False))
        self.low = bool(n.get("low", False)) and not self.house
        sp = n.get(contract.SCREEN_PICK)
        if isinstance(sp, dict) and "palette" in sp:     # the hold (S5.8); a junk record is dropped
            src = sp.get("source")
            self.screen_pick = {"palette": sp["palette"],
                                "label": sp["label"] if isinstance(sp.get("label"), str) and sp["label"]
                                else self._pick_label(sp["palette"]),
                                "screen": sp["screen"] if isinstance(sp.get("screen"), str) else "",
                                "at": _num(sp.get("at"), self._wall()), "source": src if isinstance(src, str) else None}

    def persist(self) -> Optional[dict]:
        k = self._keep_in()
        ku = round(self._wall() + k, 3) if k is not None else None
        return {"target": dict(self.target) if self.target else None,
                "target_source": self.target_source if self.target_source in ("mode", "api") else "mode",
                "target_set_at": self.target_set_at, "mode": self.mode,
                "nudges": {"steps": self.steps, "palette": self.palette_pick, "moods": list(self.moods)
                           if self.moods else None, "keep_until": ku, "house": self.house, "low": self.low,
                           contract.SCREEN_PICK: dict(self.screen_pick) if self.screen_pick else None}}

    def start(self) -> None:
        self.palettes.poll()
        self.vibes.poll(force=True)
        self._check_patch(force=True)
        self._poll_light_scenes()

    def stop(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        self._tasks.clear()

    def snapshot(self) -> dict:
        return self._snap

    # ================================================================================================================
    # the tick (show-brain, 10 Hz)
    # ================================================================================================================
    def tick(self, now_s: float) -> None:
        t0 = time.perf_counter()
        self._now_s = now_s
        dt = 0.0 if self._t_prev is None else min(1.0, max(0.0, now_s - self._t_prev))
        self._t_prev = now_s
        if now_s >= self._reload_next:
            self._reload_next = now_s + contract.RELOAD_S
            self.palettes.poll()
            self.vibes.poll()
            self._check_patch()
            self._check_response()
            self._poll_light_scenes()
        self._check_rig()
        self._fetches(now_s)

        vs, known = self._effective()
        self.vs, self.known = vs, known
        nf = self.cfg["no_feel"]["palettes"]
        self.pal_on = known or bool(nf)                   # colours resolve: a feel, or no_feel's palettes
        self.vp = vs if known else {**vs, "palettes": list(nf)}
        name = self.target["vibe"] if (known and self.target) else None
        if name != self._vibe_name:
            self._enter(name, now_s)
        if not known:
            self._vs_pub = None
        elif self._vs_pub is None or self._vs_pub != vs:
            self._vs_pub = vs
        steps_max = self._steps_max(vs)
        if abs(self.steps) > steps_max:
            self.steps = max(-steps_max, min(steps_max, self.steps))

        room = _call(self.ctx.room)
        if self._restore_check and isinstance(room, dict) and isinstance(room.get("current"), str):
            self._restore_check = False
            if room["current"] != self.mode:
                self.on_mode_change(self.mode, room["current"])
        if self.keep_until is not None and now_s >= self.keep_until:
            self.keep_until = None
            self._log_event("nudge", {"nudge": "keep", "expired": True})
            self._notify()
            self._dirty()

        grid = self._grid(now_s)
        self._read_context(now_s, vs, grid)
        self._music(now_s, dt, vs, grid)
        cutting = self._cutting(room)
        self._music_events(now_s, vs, grid)
        self._armed_tick(now_s, vs, grid)
        self._changes(now_s, vs, grid, cutting)
        if self.light_scene is None and self.pal_on and self.light_scenes.scenes:
            self._pick_light_scene(self._ls_reason, now_s)   # none set yet (or it left the catalogue)
        self._phase_tick(now_s, vs, grid)
        self._compose_plan(now_s, dt, room)
        self._compose(now_s, vs, known, grid, room)
        self._outputs(now_s, vs, known, grid)
        self._screens_palette(now_s, room)
        self._tick_ms.append((time.perf_counter() - t0) * 1000.0)
        self._build_snapshot(now_s)

    # -- inputs -------------------------------------------------------------------------------------------------------
    def _grid(self, now_s: float) -> Grid:
        body = _call(self.ctx.clock)
        if isinstance(body, dict):
            t = _num(body.get("t_ms"))
            if t is not None and now_s - t / 1000.0 > CLOCK_STALE_S:
                body = None
        else:
            body = None
        return Grid(body, now_s)

    def _cutting(self, room: Any) -> bool:
        if not isinstance(room, dict):
            return False
        return any(isinstance(s, dict) and s.get("cutting") for s in (room.get("screens") or {}).values())

    def _situation(self, sid: str) -> Optional[bool]:
        r = _call(self.ctx.situations)
        if not isinstance(r, dict):
            return None
        v = (r.get(sid) or {}).get("value") if isinstance(r.get(sid), dict) else None
        return v if isinstance(v, bool) else None

    def _music(self, now_s: float, dt: float, vs: dict, grid: Grid) -> None:
        """L1 + L2: smoothed inputs, liveness, the composited energy and the other direction numbers."""
        f = _call(self.ctx.features)
        ft = _num(f.get("t_ms")) if isinstance(f, dict) else None
        fresh = ft is not None and now_s - ft / 1000.0 <= STALE_S
        self._feat_now = f if fresh else None              # the composer's gate and lufs_s (README S5.18)
        if fresh:
            self._stale_since = None
            if self._cold:
                self.lv, self._cold = 1.0, False
            else:
                self.lv = min(1.0, self.lv + dt / RECOVER_S)
            e = _c01(_num(f.get("energy"), 0.0))
            self._e = _ema(self._e, e, dt, TAU_E_S)
            self._e_hist.append((now_s, self._e))
            self._onset = _ema(self._onset, _c01(_num(f.get("onset"), 0.0)), dt, TAU_ONSET_S)
            self._nov_fast = _ema(self._nov_fast, e, dt, TAU_NOVELTY_FAST_S)
            self._nov_slow = _ema(self._nov_slow, e, dt, TAU_NOVELTY_SLOW_S)
            gate = _c01(_num(f.get("gate"), 0.0))
            drive = _c01(_num(f.get("drive"), 0.0))
        else:
            if self._stale_since is None:
                self._stale_since = now_s
            if now_s - self._stale_since >= HOLD_S:
                self.lv = max(0.0, self.lv - dt / FADE_S)
                if self.lv <= 0.0:
                    self._cold = True
            gate, drive = 0.0, 0.0
        # silence -> a new song when the sound comes back
        if not fresh or gate < SILENT_GATE:
            if self._silent_since is None:
                self._silent_since = now_s
        else:
            if self._silent_since is not None and now_s - self._silent_since >= float(self.cfg["new_song_after_s"]) \
                    and self._mc is None:                # with structure, its track_change says it (README S4.8)
                self._song_change = True
            self._silent_since = None
        h = _call(self.ctx.harmony)
        ht = _num(h.get("t_ms")) if isinstance(h, dict) else None
        h_fresh = ht is not None and now_s - ht / 1000.0 <= HARMONY_STALE_S
        h_conf = _c01(_num(h.get("conf"), 0.0)) if h_fresh else 0.0
        if h_fresh:
            self._tension = _ema(self._tension, _c01(_num(h.get("tension"), 0.0)), dt, TAU_TENSION_S)
            self._chord = _ema(self._chord, _c01(_num(h.get("chord_change"), 0.0)), dt, TAU_CHORD_S)
            self._register = _ema(self._register, _c01(_num(h.get("register"), 0.5)), dt, TAU_TENSION_S)
        p = _call(self.ctx.piano)
        playing = (isinstance(p, dict) and _num(p.get("last_note_age_s")) is not None
                   and p["last_note_age_s"] <= 4.0)
        energy_in = self._e if self._e is not None else 0.5
        onsets_in = _c01(0.6 * drive + 0.4 * (self._onset or 0.0)) if fresh else None
        inputs: dict[str, tuple[float, float]] = {}       # follow input -> (value, conf)
        for name in vs["follow"]:
            if name == "energy" and self._e is not None:
                inputs[name] = (energy_in, gate if fresh else 0.5)
            elif name == "onsets" and onsets_in is not None:
                inputs[name] = (onsets_in, gate)
            elif name == "dynamics":
                if playing:
                    inputs[name] = (_c01(_num(p.get("velocity"), 0.5)), 1.0)
                elif self._e is not None:
                    inputs[name] = (energy_in, gate if fresh else 0.5)
            elif name == "register" and h_fresh and self._register is not None:
                inputs[name] = (self._register, h_conf)
            elif name == "harmony" and h_fresh and self._tension is not None:
                inputs[name] = (_c01(0.7 * self._tension + 0.3 * (self._chord or 0.0)), h_conf)
        if inputs:
            music = sum(v for v, _ in inputs.values()) / len(inputs)
            conf = sum(c for _, c in inputs.values()) / len(inputs)
        else:
            music, conf = 0.5, 0.0
        # relax (§8.5): quiet or talking, unless the vibe says otherwise; and no audio at all (liveness run down after
        # the hold and the fade, nobody playing): drift to the base look and stop changing, whatever the vibe says
        no_audio = not fresh and self.lv <= 0.0 and not playing
        relax_want = 1.0 if (no_audio or (vs["relax_on_talk"] and (self._situation("quiet")
                                                                     or self._situation("talking")))) else 0.0
        self._relax = _ema(self._relax, relax_want, dt, RELAX_TAU_S)
        was = self._relaxed
        self._relaxed = self._relax > 0.5
        if self._relaxed and not was:
            self._relax_done = False
        # L2: the listener's trajectory, slewed; and its style of approach (README S5.22)
        b = _call(self.ctx.beliefs)
        self._guide = self._llm_guide(b)
        bias_want = 0.0
        if isinstance(b, dict) and b.get("state") in ("fresh", "holding"):
            tr = b.get("trajectory") if isinstance(b.get("trajectory"), dict) else {}
            if (_num(tr.get("conf"), 0.0) or 0.0) >= LLM_CONF:
                bias_want = (_num(self.cfg["listener_bias"].get(tr.get("state")), 0.0) or 0.0) \
                    * _c01(_num(tr.get("conf"), 0.0))
        step = LLM_SLEW_PER_S * dt
        self._llm_bias += max(-step, min(step, bias_want - self._llm_bias))
        lo, hi = vs["energy"]
        centre = vs["energy_centre"]
        live = self.lv * (1.0 - self._relax)
        e_out = centre + vs["follow_amount"] * conf * live * (music - 0.5) + self._llm_bias * live \
            + self.steps * self._step_size(vs)
        e_out = min(hi, max(lo, e_out))
        # the band words' energy: the music as heard (full follow round the middle), within the feel's range; the
        # feel's centre while nothing is live (2b's audit, 17 Oct: with e_out, no feel never reached the peak band and
        # Party read mellow jazz as euphoric)
        heard_e = self._band_heard(music, conf, now_s)
        heard = centre + live * (heard_e - centre) + self._llm_bias * live + self.steps * self._step_size(vs)
        band_e = min(hi, max(lo, heard))
        u = (e_out - lo) / (hi - lo) if hi > lo else 0.5
        bpm = grid.bpm
        if self.cfg["compose"]["mode"] == comp.DRIVE and grid.has_grid:
            bpm = comp.fold_bpm(bpm, *self._fold)          # README S5.18: 75's one fold, so half-time isn't slow
        tempo = _c01((bpm - 60.0) / 90.0) if grid.has_grid and grid.gate in ("beat", "dmx") else None
        pace_in = 0.7 * e_out + 0.3 * tempo if tempo is not None else e_out
        onsets_now = onsets_in if onsets_in is not None and live > 0 else e_out
        arousal = _c01(0.6 * e_out + 0.4 * onsets_now)
        self.music = {"fresh": fresh, "gate": round(gate, 3), "energy_in": round(energy_in, 3), "music": round(music, 3),
                      "conf": round(conf, 3), "inputs": {k: round(v, 3) for k, (v, _) in inputs.items()},
                      "liveness": round(self.lv, 3), "relax": round(self._relax, 3), "llm_bias": round(self._llm_bias, 3),
                      "energy": e_out, "band_energy": band_e, "band_base": self._band_base, "u": u, "pace": 0.1 + 0.8 * pace_in, "arousal": arousal,
                      "onsets": onsets_now, "drive": drive, "tension": self._tension or 0.0,
                      "novelty": abs((self._nov_fast or 0.0) - (self._nov_slow or 0.0)) if fresh else 0.0,
                      "playing": playing, "no_audio": no_audio, "pv": p if isinstance(p, dict) else None, "beliefs": b
                      if isinstance(b, dict) else None, "bpm": grid.bpm, "clock_gate": grid.gate}

    # -- the vibe entered / left ----------------------------------------------------------------------------------------
    def _enter(self, name: Optional[str], now_s: float) -> None:
        old = self._vibe_name
        self._vibe_name = name
        if name is None:
            if self.cfg["no_feel"]["palettes"]:
                self._entry_fade = True                   # no feel: cross over to no_feel's colours from what shows
            else:
                self._pal = None                          # neutral: artnet_out keeps its fixed colour
            self.pending = None
            self.next_pending = False
            self._stage4_reset()
            if self._look_out is not None:
                _call(self.ctx.set_look_out, reset=True)
                self._look_out = None
                self._plan_sent = None                    # reset cleared artnet_out's plan too: drive sends it again
            if self._gate_ref is not None and self._gate_ref != GATE_REF_DEFAULT_LUFS:
                _call(self.ctx.set_gate_ref, GATE_REF_DEFAULT_LUFS)
            self._gate_ref = None
            self._log_event("vibe", {"from": old, "to": None})
            return
        self.entered_s = now_s
        self._phrase_passed = False
        self.look_n += 1
        self.looks += 1
        self.look_started_s = now_s
        self.look_bar = None
        self.variation = 0
        self._var_mark_bar, self._var_mark_s = None, now_s
        self.pal_idx = 0
        self._pause_used = False
        self.pending = None
        self.next_pending = False
        self.palette_name = None
        self._look_transition = None
        self._entry_fade = True
        self._art_next = 0.0                               # the first look may come from the art: fetch now
        self._stage4_reset()
        self._log_event("vibe", {"from": old, "to": name})
        self._pick_light_scene("feel", now_s)              # a new feel is a new look: the lamps' scene too (S5.24)

    # -- when looks change (§8.5) ----------------------------------------------------------------------------------------
    def _min_bars(self, vs: dict) -> float:
        return 32.0 / (4.0 ** _c01(vs["change_rate"]))

    def _changes(self, now_s: float, vs: dict, grid: Grid, cutting: bool) -> None:
        if self._vibe_name is None:
            # no feel: nothing changes looks on a cadence, but a change already announced (a person's Next look, a
            # song change) lands with no_feel's palettes, or is dropped when no colours resolve. Left pending, it
            # never landed and _held() read true for ever (every track event held, room.direction.hold stuck)
            pd = self.pending
            if pd is not None and now_s >= pd["at_s"] - 1e-6 and not cutting:
                if self.pal_on:
                    self._land(pd, now_s, self.vp, grid)
                else:
                    self.pending, self.next_pending = None, False
            return
        if not self._phrase_passed and self.entered_s is not None and grid.phrase_s and grid.gate in ("beat", "dmx") \
                and now_s - self.entered_s >= grid.phrase_s:     # §8.6: locked (DMX's from middle confidence, §5.4)
            self._phrase_passed = True
        if grid.ok and self.look_bar is None:
            self.look_bar = grid.bar_at(now_s)
        # land a change that is due
        if self.pending is not None and now_s >= self.pending["at_s"] - 1e-6:
            pd = self.pending
            if cutting and pd["by"] == "director":
                self.pending = None                        # §8.6: no look changes around a screen cut
                self._log_event("look_skipped", {"why": "screen cut", "reason": pd["reason"]})
            elif not cutting:
                self._land(pd, now_s, vs, grid)
            return
        if self.pending is not None:
            return
        keep = self.keep_until is not None
        if self._relaxed:
            if not self._relax_done:
                self._relax_done = True
                base = self._base_palette(vs)
                if base is not None and base != self.palette_name and not keep and not cutting:
                    self._announce(now_s, "relax", "director", grid, at_s=now_s + LOOK_MIN_LEAD_S,
                                   transition=dict(SLOW_TRANSITION), palette=base)
            return
        if cutting:
            return
        mode = vs["change_looks"]
        p = self.music.get("pv")
        if mode == "between_pieces" and not (isinstance(p, dict) and _num(p.get("last_note_age_s")) is not None):
            mode = "time"                                  # no notes yet: there are no pieces to wait for
        if mode == "phrase" and not grid.ok:
            mode = "time"                                  # the clock is unsure: by time (§8.5)
        age = now_s - (self.look_started_s or now_s)
        dmin, dmax = vs["dwell_s"]
        if mode == "phrase":
            t_line, bar = grid.next_line(now_s + LOOK_MIN_LEAD_S, grid.pb)
            ahead = t_line - now_s
            if ahead > min(ANNOUNCE_HORIZON_S, grid.bar_s or ANNOUNCE_HORIZON_S):
                return
            bars_since = bar - (self.look_bar if self.look_bar is not None else bar)
            if keep:
                if self._var_mark_bar is None:
                    self._var_mark_bar = self.look_bar if self.look_bar is not None else bar
                if bar - self._var_mark_bar >= float(self.cfg["variation_bars"]):
                    self._var_mark_bar = bar
                    self._variation(now_s, "64 bars kept")
                return
            if bars_since >= self._min_bars(vs) - 1e-9:
                self._announce(now_s, "phrase", "director", grid, at_s=t_line, bar=bar)
            return
        if mode == "between_pieces":
            last = _num(p.get("last_note_age_s"))
            bp = self.cfg["between_pieces"]
            in_pause = (last is not None and last >= bp["pause_s"] and int(_num(p.get("keys_down"), 0) or 0) == 0
                        and (_num(p.get("damper"), 0.0) or 0.0) < bp["damper_up"])
            if not in_pause:
                self._pause_used = False
                return
            if self._pause_used or age < dmin:
                return
            self._pause_used = True
            if keep:
                self._variation(now_s, "between pieces, kept")
                return
            self._announce(now_s, "between_pieces", "director", grid, at_s=now_s + LOOK_MIN_LEAD_S,
                           transition=self._transition(vs))
            return
        # time
        if keep:
            if now_s - (self._var_mark_s or now_s) >= dmax:
                self._var_mark_s = now_s
                self._variation(now_s, "dwell, kept")
            return
        novel = self.music.get("novelty", 0.0) >= float(self.cfg["novelty"]) and self.lv >= 0.5
        if age >= dmax or (age >= dmin and novel):
            self._announce(now_s, "dwell" if age >= dmax else "novelty", "director", grid,
                           at_s=now_s + LOOK_MIN_LEAD_S,
                           transition={"style": "crossfade", "beats": None, "seconds": TIME_FADE_S})

    def _announce(self, now_s: float, reason: str, by: str, grid: Grid, *, at_s: float,
                  bar: Optional[int] = None, transition: Optional[dict] = None, advance: bool = False,
                  palette: Optional[str] = None, recall: Optional[dict] = None,
                  section: Optional[dict] = None) -> None:
        """Announce a look change at ``at_s`` (room.event look_change) and hold it as ``pending``. ``recall``: a
        remembered look (palette, variation) to bring back; ``section``: the structure event that caused it (its
        reason is then one of STRUCTURE_REASONS). Whatever the cause, the landing steps GOBO."""
        self.pending = {"at_s": at_s, "reason": reason, "by": by, "advance": advance, "transition": transition,
                        "palette": palette, "bar": bar, "recall": dict(recall) if recall else None,
                        "section": dict(section) if section else None,
                        "structure": reason in STRUCTURE_REASONS and section is not None}
        if self.pending["structure"] and reason in ("drop", "track"):
            b = _num((section or {}).get("boundary_ms")) or _num((section or {}).get("at_ms"))
            self._cut_mark = ((b / 1000.0) if b is not None else at_s, reason)
        look_id = f"L{self.look_n + 1}"                   # re-announcing a pending change keeps its id (04, 17 Oct)
        ev: contract.EventData = {"kind": "look_change", "id": f"look_change:{look_id}",
                                  "at_ms": round(at_s * 1000.0, 1), "t_ms": round(now_s * 1000.0, 1),
                                  "seq": self._next_ev(), "look_id": look_id, "detail": reason}
        if grid.has_grid:
            ev["beat"] = round(grid.beat_at(at_s), 3)
            ev["bar"] = grid.bar_at(at_s + 1e-6)
            ev["phrase"] = math.floor(ev["bar"] / grid.pb)
        self._publish(contract.ROOM_EVENT, ev)
        self.events += 1

    def _next_ev(self) -> int:
        self._ev_seq += 1
        return self._ev_seq

    def _variation(self, now_s: float, why: str) -> None:
        self.variation += 1
        self._log_event("variation", {"why": why, "variation": self.variation})

    def _land(self, pd: dict, now_s: float, vs: dict, grid: Grid) -> None:
        self.pending = None
        self.next_pending = False
        self.look_n += 1
        self.looks += 1
        self.look_started_s = now_s
        self.look_bar = grid.bar_at(now_s + 1e-6) if grid.ok else None
        self._var_mark_bar, self._var_mark_s = self.look_bar, now_s
        rec = pd.get("recall")
        if pd.get("palette"):
            self.palette_name = pd["palette"]
        elif rec is not None and rec.get("palette") in self._candidates(vs):
            self.palette_name = rec["palette"]           # a returning section gets its look back (§8.5)
        elif pd["advance"] or self._song_change:
            self._advance_palette(vs)
        self._song_change = False
        if rec is not None:
            self.variation = int(_num(rec.get("variation"), self.variation) or 0)
        elif not pd.get("palette") and not pd["advance"]:
            self.variation += 1                          # the director's own change: a new allocation of roles
        tr = pd.get("transition") or self._transition(vs)
        self._look_transition = tr
        self._song_dip = None                            # a new look ends a song's outgoing half (S5.17)
        # GOBO +1, the screens' scene change: while a vibe plays the director owns it (structure steps it only while
        # we are neutral), so it comes with every look that actually lands, and with nothing the vibe holds back
        _call(self.ctx.section_change)
        self.gobo_steps += 1
        self._fade_s = self._transition_s(tr, grid)
        self._force_look = True
        self._express_set(0.0, now_s, 0.0)              # a new look starts without the last section's expression
        detail = {"look_id": f"L{self.look_n}", "reason": pd["reason"], "by": pd["by"], "palette": self.palette_name,
                  "transition": tr, "gobo": True}
        if pd.get("section"):
            detail["section"] = pd["section"]
            detail["recalled"] = rec is not None
            detail["context"] = self._ctx_brief()
            self.sec_stats["looks"] += 1
            self.sec_stats["recalled"] += 1 if rec is not None else 0
        self._log_event("look_change", detail)
        self._look_why = {"why": LOOK_REASON_WORDS.get(pd["reason"], str(pd["reason"]).replace("_", " ")),
                          "t": now_s}
        self._pick_light_scene(pd["reason"], now_s, human=pd["by"] == "human")   # README S5.24

    def _transition(self, vs: dict) -> dict:
        style = self._words(vs)["transition_style"]
        return dict(self.cfg["transitions"].get(style or "", NEUTRAL_TRANSITION))

    @staticmethod
    def _transition_s(tr: dict, grid: Grid) -> float:
        if tr.get("style") == "cut":
            return float(tr.get("seconds") or 0.05)
        if tr.get("beats") and grid.ok and grid.beat_s:
            return float(tr["beats"]) * grid.beat_s
        return float(tr.get("seconds") or TIME_FADE_S)

    # -- Stage 4: sections, builds, drops and peak tokens (§8.5; README S4.8) ---------------------------------------
    def _stage4_reset(self) -> None:
        """A vibe entered or left: the section memory, the expression, a reserved token, a build's steps, the
        screens' palette record all start again (spent tokens stay spent: the budget is per hour, not per vibe)."""
        if self._armed is not None and self.pending is not None and self.pending.get("reason") == "drop":
            self.pending = None
        self._armed = None
        self._hit = None
        self._hit_want = None
        self._build = None
        self._drop_inside_s = None
        self._sec_mem.clear()
        self._x = (0.0, 0.0, 0.0, 0.0)
        self._cut_mark = None
        self._song_dip = None
        self._scr["sent"].clear()
        self._scr["retry"].clear()

    def _read_context(self, now_s: float, vs: dict, grid: Grid) -> None:
        """structure's music.context: fresh (or None), its phase, a new track id, and the section memory's fill."""
        mc = _call(getattr(self.ctx, "music_context", None))
        t = _num(mc.get("t_ms")) if isinstance(mc, dict) else None
        if t is None or now_s - t / 1000.0 > CONTEXT_STALE_S:
            self._mc = None
            return
        self._mc = mc
        sec = mc.get("section") if isinstance(mc.get("section"), dict) else {}
        ph = sec.get("phase")
        self._phase_ctx = ph if ph in contract.SECTION_PHASES else contract.SECTION_PHASES[0]
        if self._phase_ctx == "build":
            self._build_last_s = now_s
        tr = mc.get("track") if isinstance(mc.get("track"), dict) else {}
        tid = tr.get("id")
        if isinstance(tid, int) and not isinstance(tid, bool):
            if self._track_id is None:
                self._track_id = tid
            elif tid != self._track_id:                   # handled after this tick's events (theirs is exact)
                age = _num(tr.get("age_s"))
                self._ctx_track = (tid, tr.get("via"), now_s - age if age is not None else now_s)
        # memory: a section, once structure has named it, keeps the look that landed in it (first time through)
        letter, started = sec.get("letter"), _num(sec.get("started_ms"))
        if (self._vibe_name is not None and isinstance(letter, str) and letter and letter not in self._sec_mem
                and started is not None and self.look_started_s is not None
                and self.look_started_s >= started / 1000.0 - 0.5):
            self._sec_mem[letter] = {"palette": self.palette_name, "variation": self.variation,
                                     "look_id": f"L{self.look_n}"}
            self._log_event("section_memory", {"letter": letter, **self._sec_mem[letter],
                                               "context": self._ctx_brief()})

    def _ctx_brief(self) -> Optional[dict]:
        mc = self._mc
        if mc is None:
            return None
        sec = mc.get("section") if isinstance(mc.get("section"), dict) else {}
        tr = mc.get("track") if isinstance(mc.get("track"), dict) else {}
        g = mc.get("genre") if isinstance(mc.get("genre"), dict) else {}
        return {"section": sec.get("id"), "letter": sec.get("letter"), "phase": sec.get("phase"),
                "build_progress": sec.get("build_progress"), "bars": sec.get("bars_in_section"),
                "track": tr.get("id"), "genre": g.get("word"), "source": mc.get("source")}

    def _phase_eff(self, now_s: float) -> str:
        """The music's phase now: an event's (until music.context, at 1 Hz, has caught up), else the context's."""
        h = self._phase_hint
        if h is not None and now_s - h[1] < float(self.cfg["sections"]["phase_hint_s"]):
            return h[0]
        return self._phase_ctx if self._mc is not None else contract.SECTION_PHASES[0]

    def _grammar(self, vs: dict, grid: Grid) -> bool:
        """The build -> drop grammar plays only with a vibe, beat_lock and the clock's gate at beat (README S4.8)."""
        return self._vibe_name is not None and bool(vs["beat_lock"]) and grid.has_grid and grid.gate == "beat"

    def _music_events(self, now_s: float, vs: dict, grid: Grid) -> None:
        while self._mev:
            ev = self._mev.popleft()
            try:
                self._on_event(ev, now_s, vs, grid)
            except Exception:                                # a bad event never fails the tick
                log.debug("music event %r failed", ev.get("kind"), exc_info=True)
        ct, self._ctx_track = self._ctx_track, None
        if ct is not None and ct[0] != self._track_id:      # a new track id with no event for it (a missed one)
            self._on_track(ct[0], ct[1], now_s, vs, grid, at_s=ct[2])

    def _on_event(self, ev: dict, now_s: float, vs: dict, grid: Grid) -> None:
        kind = ev.get("kind")
        at_ms = _num(ev.get("at_ms"))
        if at_ms is None:
            return
        self.sec_stats["events"][kind] = self.sec_stats["events"].get(kind, 0) + 1
        if kind == "track_end_armed":                     # the composer's fade cue (README S5.18); a call-off ends it
            self._end_armed = None if ("eta_s" in ev and ev["eta_s"] is None) else at_ms / 1000.0
        elif kind == "track_change":
            self._end_armed = None
            self._on_track(ev.get("track_id"), ev.get("via"), now_s, vs, grid, at_s=at_ms / 1000.0, ev=ev)
        elif kind == "track_end":
            self._on_track_end(ev, now_s, vs)
        elif kind == "section_change":
            self._on_section(ev, at_ms / 1000.0, now_s, vs, grid)
        elif kind == "build":
            self._phase_hint = ("build", now_s)
            self._build_last_s = now_s
            if self._build is None and grid.has_grid:
                self._build_begin(at_ms / 1000.0, grid)
        elif kind == "breakdown":
            self._phase_hint = ("breakdown", now_s)
        elif kind == "drop_armed":
            self._on_drop_armed(ev, at_ms / 1000.0, now_s, vs, grid)
        elif kind == "drop":
            self._phase_hint = ("drop", now_s)
            self._light_drop(ev, at_ms / 1000.0, now_s, grid)   # the light scene's drop hits (S5.24), token or not
            self._on_drop(ev, at_ms / 1000.0, now_s, vs, grid)

    def _section_info(self, ev: dict) -> dict:
        return {k: ev.get(k) for k in ("kind", "section_id", "letter", "repeat_of", "predicted", "at_ms", "boundary_ms",
                                       "eta_beats", "track_id", "via", "prev_track_id") if k in ev}

    def _on_track_end(self, ev: dict, now_s: float, vs: dict) -> None:
        """README S5.17: a track_end that fades through dark starts the outgoing half on the lamps (down to the
        vibe's floor over out_s, never off; the screens fade themselves); its "sound_back" brings them back over
        in_s. Nothing while a person holds the look, or without a vibe."""
        tr = ev.get("transition") if isinstance(ev.get("transition"), dict) else None
        if ev.get("detail") == "sound_back":
            self._release_dip(now_s, "the sound is back")
            return
        if not self.pal_on or ev.get("held") or self._held() or tr is None or tr.get("style") != "fade-dark":
            return
        out_s = _num(tr.get("out_s"), 2.0) or 2.0
        in_s = _num(tr.get("in_s"), 2.0) or 2.0
        dark_max = _num(tr.get("dark_max_s"), out_s + in_s) or (out_s + in_s)
        self._song_dip = {"t0": now_s, "out_s": max(0.05, out_s), "in_s": in_s, "until": now_s + max(out_s, dark_max),
                          "track_id": ev.get("track_id")}
        self.ends_stats["dips"] += 1
        self._log_event("song_end", {"event": self._section_info(ev), "dip": dict(self._song_dip),
                                     "context": self._ctx_brief()})

    def _release_dip(self, now_s: float, why: str) -> None:
        d, self._song_dip = self._song_dip, None
        if d is None:
            return
        self._fade_s = float(d["in_s"])                  # the lamps come back over in_s
        self.ends_stats["dip_released"] += 1
        self._log_event("song_end", {"released": why, "track_id": d["track_id"]})

    @staticmethod
    def _look_transition_for(tr: Optional[dict]) -> Optional[dict]:
        """The director's own look transition for a song change's transition (contract.TrackTransition): a cut, a
        crossfade over its seconds, or fade-dark's in-half (in_s) from the dipped lamps."""
        if not isinstance(tr, dict):
            return None
        if tr.get("style") == "cut":
            return dict(TRACK_CUT)
        secs = _num(tr.get("in_s")) if tr.get("style") == "fade-dark" else _num(tr.get("seconds"))
        return {"style": "crossfade", "beats": None, "seconds": secs if secs is not None else TIME_FADE_S}

    def _on_track(self, tid: Any, via: Any, now_s: float, vs: dict, grid: Grid, *, at_s: float,
                  ev: Optional[dict] = None) -> None:
        """A song change (structure's track_change, or a new track id in the context): the section memory clears; a
        vibe's look changes at the junction (README S5.17): the first bar line at or after the new song's at_ms that
        the screens' lead allows (no grid: then), with the event's transition (a cut, a crossfade, or fade-dark's
        in-half). The palette moves on for a big change only (big: a new palette; small: the same). Nothing while a
        person holds the look (the event's ``held``, Keep this look, their change pending) or while the feel is
        relaxed because people talk (relaxed by the gap's own quiet, the new song is the sound coming back)."""
        if isinstance(tid, int) and not isinstance(tid, bool):
            if tid == self._track_id:
                return
            self._track_id = tid
        ev = ev if isinstance(ev, dict) else {}
        tr = ev.get("transition") if isinstance(ev.get("transition"), dict) else None
        n = len(self._sec_mem)
        self._sec_mem.clear()
        self._song_change = (tr or {}).get("change") != "small"
        info = {"kind": "track_change", "track_id": tid, "via": via, "at_ms": round(at_s * 1000.0, 1),
                "prev_track_id": ev.get("prev_track_id"), "transition": tr}
        self._log_event("track", {"track_id": tid, "via": via, "memory_cleared": n, "transition": tr,
                                  "held": bool(ev.get("held")), "context": self._ctx_brief()})
        if self._vibe_name is None:                       # no feel: no look of ours lands, so the lamps' light scene
            self._pick_light_scene("track", now_s)        # changes with the song here (README S5.24)
        # relaxed by the gap itself (quiet, no audio): the new song is the sound coming back, so its look lands;
        # relaxed because people talk over the music: it holds (§8.5)
        talk_hold = self._relaxed and bool(vs["relax_on_talk"]) and self._situation("talking") is True
        if not self.pal_on or talk_hold or self.keep_until is not None or ev.get("held"):
            self._release_dip(now_s, "the new song, with no look change")
            return                                        # the palette moves on at the next look change
        pd = self.pending
        if pd is not None and pd.get("recall"):
            pd["recall"] = None                           # the last track's look doesn't come back in this one
        if pd is not None and (pd["by"] == "human" or pd.get("structure")):
            self._release_dip(now_s, "a pending change lands instead")
            return                                        # a pending change takes the song change's palette
        t0 = max(at_s, now_s + max(LOOK_MIN_LEAD_S, self._ev_lead))   # the screens' lead (§5.4)
        at, bar = grid.next_line(t0, 1) if grid.ok else (t0, None)
        self._announce(now_s, "track", "director", grid, at_s=at, bar=bar, section=info,
                       transition=self._look_transition_for(tr))

    def _on_section(self, ev: dict, at_s: float, now_s: float, vs: dict, grid: Grid) -> None:
        """§8.5: a predicted section (a known repeat) lands on its line with that letter's look; a confirmed one lands
        at its at_ms unless that is more than max_late_bars into the section: then it shows inside the look."""
        info = self._section_info(ev)
        predicted = bool(ev.get("predicted"))
        boundary_s = (_num(ev.get("boundary_ms"), at_s * 1000.0) or at_s * 1000.0) / 1000.0
        prev_b = self._last_boundary_s
        self._last_boundary_s = boundary_s
        if self._vibe_name is None:
            return
        letter = ev.get("letter") if isinstance(ev.get("letter"), str) and ev.get("letter") else None
        mode = vs["change_looks"]
        if mode == "phrase" and not grid.ok:
            mode = "time"                                 # the clock unsure: as time (§8.5)
        pd = self.pending
        cm = self._cut_mark
        if not predicted and cm is not None and abs(boundary_s - cm[0]) <= 2.0 * (grid.bar_s or 2.0):
            # the drop's (or the new song's) own boundary: its look already shows it. A pending track look moves to
            # this section's line (the bar structure announced to the screens), and its GOBO step with it.
            if pd is not None and pd.get("structure") and pd["reason"] == "track" and at_s > pd["at_s"] \
                    and at_s - pd["at_s"] <= 2.0 * (grid.bar_s or 2.0):
                self._announce(now_s, "track", "director", grid, at_s=at_s, bar=pd.get("bar"),
                               section=pd.get("section"), transition=pd.get("transition"))
            self.sec_stats["kept"] += 1
            self._log_event("section_kept", {"section": info, "why": f"the same boundary as the {cm[1]} look",
                                             "express": None, "context": self._ctx_brief()})
            return
        why = None
        if self._relaxed:
            why = "relaxed (quiet, talking or no sound)"
        elif mode != "phrase":
            why = f"the feel changes looks by {mode}: the section shows inside the look"
        elif self.keep_until is not None:
            why = "Keep this look holds"
        elif pd is not None and pd["by"] == "human":
            why = "a person's change is pending"
        elif pd is not None and pd.get("structure") and pd["reason"] in ("drop", "track"):
            why = f"a {pd['reason']} change is pending"
        elif not predicted and grid.bar_s and \
                at_s - boundary_s > float(self.cfg["sections"]["max_late_bars"]) * grid.bar_s + 1e-6:
            why = "late: more than max_late_bars into the section"
        if why is not None:
            x = None if self._relaxed else self._express_section(now_s, boundary_s, prev_b, grid)
            self.sec_stats["kept"] += 1
            self.sec_stats["expressed"] += 1 if x is not None else 0
            self._log_event("section_kept", {"section": info, "why": why, "express": x,
                                             "late_bars": round((at_s - boundary_s) / grid.bar_s, 2)
                                             if grid.bar_s else None, "context": self._ctx_brief()})
            return
        recall = self._sec_mem.get(letter) if letter else None
        at = at_s
        if at < now_s + 0.25:                             # too close to announce: the next bar line
            at = grid.next_line(now_s + LOOK_MIN_LEAD_S, 1)[0]
        self._announce(now_s, "repeat" if predicted else "section", "director", grid, at_s=at,
                       bar=grid.bar_at(at + 1e-6), recall=recall, section=info)
        self._log_event("section_look", {"section": info, "at_ms": round(at * 1000.0, 1), "recall": recall,
                                         "context": self._ctx_brief()})

    def _mean_e(self, t0: float, t1: float) -> Optional[float]:
        vals = [e for t, e in self._e_hist if t0 <= t < t1]
        return sum(vals) / len(vals) if vals else None

    def _express_section(self, now_s: float, boundary_s: float, prev_b: Optional[float], grid: Grid) -> Optional[float]:
        """§8.5: the section shows inside the look: level, accent density and spread move by express_gain x (this
        section's energy - the last one's), over one beat. Returns the new offset (u units), or None (no energy)."""
        cur = self._mean_e(boundary_s, now_s + 1e-6)
        start = prev_b if prev_b is not None and prev_b < boundary_s else boundary_s - 30.0
        last = self._mean_e(start, boundary_s)
        if cur is None or last is None:
            return None
        x = max(-1.0, min(1.0, float(self.cfg["sections"]["express_gain"]) * (cur - last)))
        self._express_set(x, now_s, grid.beat_s if grid.has_grid and grid.beat_s else 0.5)
        return round(x, 3)

    def _express_set(self, to: float, now_s: float, dur: float) -> None:
        self._x = (self._express_now(now_s), float(to), now_s, now_s + max(0.0, dur))

    def _express_now(self, now_s: float) -> float:
        frm, to, t0, t1 = self._x
        if now_s >= t1 or t1 <= t0:
            return to
        return frm + (to - frm) * (now_s - t0) / (t1 - t0)

    # peak tokens
    def _tokens_left(self, vs: dict, now_s: float) -> tuple[int, int]:
        """(the vibe's peak_tokens_per_hour, how many may still be spent in the 60 minutes ending now)."""
        n = max(0, int(_num(vs.get("peak_tokens_per_hour"), 0) or 0))
        while self._tokens and now_s - self._tokens[0] >= TOKEN_WINDOW_S:
            self._tokens.popleft()
        used = len(self._tokens) + (1 if self._armed is not None else 0)
        return n, max(0, n - used)

    def _token_refused(self, why: str, info: dict) -> None:
        self.sec_stats["tokens_refused"] += 1
        self._log_event("token", {"decision": "refused", "why": why, "event": info, "context": self._ctx_brief()})

    def _no_token_why(self, n: int) -> str:
        return "the feel has no peak tokens (peak_tokens_per_hour unset)" if n == 0 else "no token left this hour"

    def _on_drop_armed(self, ev: dict, at_s: float, now_s: float, vs: dict, grid: Grid) -> None:
        """A predicted drop line: with a token, reserve it, dip the lamps before the line, then at the line a hard cut
        to a new look and a unison hit on the lamps; the token is spent at the line. ``eta_beats`` None: structure
        called the line off (``_line_off``)."""
        info = self._section_info(ev)
        if "eta_beats" in ev and ev["eta_beats"] is None:
            self._line_off(ev, at_s, info)
            return
        if not self._grammar(vs, grid):
            return
        if self._armed is not None:                       # the line moved (a clock epoch): the reservation follows
            self._armed.update(at_s=at_s, section_id=ev.get("section_id"))
            if self.pending is not None and self.pending.get("reason") == "drop":
                self._announce(now_s, "drop", "director", grid, at_s=at_s, bar=grid.bar_at(at_s + 1e-6),
                               transition=dict(DROP_CUT), section=info)
            self._log_event("token", {"decision": "moved", "event": info})
            return
        n, left = self._tokens_left(vs, now_s)
        if self.keep_until is not None:
            self._token_refused("Keep this look holds", info)
            return
        if left <= 0:
            self._token_refused(self._no_token_why(n), info)
            return
        self._armed = {"at_s": at_s, "section_id": ev.get("section_id"), "since_s": now_s}
        if self.pending is None or self.pending["by"] != "human":
            self._announce(now_s, "drop", "director", grid, at_s=at_s, bar=grid.bar_at(at_s + 1e-6),
                           transition=dict(DROP_CUT), section=info)
        self._log_event("token", {"decision": "reserved", "event": info, "left": left - 1, "per_hour": n,
                                  "context": self._ctx_brief()})

    def _line_off(self, ev: dict, at_s: float, info: dict) -> None:
        """structure called an armed line off (§8.5: only a drop that follows a build spends a token). "disarmed"
        (the build ended well before it): a reservation is released, with its dip, hit and cut. "missed" (the line
        passed with no drop): the look already landed on the line (the predictive landing, §5.4), but the token it
        spent comes back."""
        why = ev.get("detail") if ev.get("detail") in ("disarmed", "missed") else "disarmed"
        a = self._armed
        if a is not None and abs(a["at_s"] - at_s) <= 0.01:
            self._armed = None
            self._hit = None
            if self.pending is not None and self.pending.get("reason") == "drop":
                self.pending = None
            self._log_event("token", {"decision": "released", "why": "the build ended before its line"
                                      if why == "disarmed" else "no drop came at its line", "event": info,
                                      "context": self._ctx_brief()})
            return
        if why == "missed" and self._last_line_s is not None and abs(self._last_line_s - at_s) <= 0.01:
            spent = self._last_line_s
            self._last_line_s = None
            for t in list(self._tokens):
                if abs(t - spent) <= 1e-6:
                    self._tokens.remove(t)
                    self.sec_stats["tokens_refunded"] += 1
                    self._log_event("token", {"decision": "refunded", "why": "no drop came at the armed line",
                                              "event": info, "context": self._ctx_brief()})
                    break

    def _armed_tick(self, now_s: float, vs: dict, grid: Grid) -> None:
        a = self._armed
        if a is None:
            return
        if not self._grammar(vs, grid):                   # the beat is gone: no dip, no hit, no cut
            self._armed = None
            self._hit = None
            if self.pending is not None and self.pending.get("reason") == "drop":
                self.pending = None
            self._log_event("token", {"decision": "released", "why": "the clock lost the beat"})
            return
        if now_s >= a["at_s"] - 1e-6:
            self._tokens.append(a["at_s"])
            self._last_line_s = a["at_s"]
            self._armed = None
            self.sec_stats["tokens_spent"] += 1
            self._log_event("token", {"decision": "spent", "at": "the armed line", "section_id": a["section_id"],
                                      "context": self._ctx_brief()})

    def _on_drop(self, ev: dict, at_s: float, now_s: float, vs: dict, grid: Grid) -> None:
        """A drop heard: at an armed line, nothing more; else with a token (and a build just before) the lamps hit
        at once and the look cuts at the next bar; without one it shows inside the look."""
        info = self._section_info(ev)
        beat = grid.beat_s if grid.has_grid and grid.beat_s else 0.5
        if (self._last_line_s is not None and abs(at_s - self._last_line_s) <= beat + 0.05) or \
                (self._armed is not None and at_s >= self._armed["at_s"] - beat):
            self._log_event("drop", {"at": "the armed line", "event": info})
            return
        if not self._grammar(vs, grid):
            return
        if self._armed is not None:                       # it came before its predicted line: the reservation is
            self._armed = None                            # released, and this drop may spend the token now
            self._hit = None
            if self.pending is not None and self.pending.get("reason") == "drop":
                self.pending = None
            self._log_event("token", {"decision": "released", "why": "the drop came before its line", "event": info})
        after = float(self.cfg["peaks"]["after_build_s"])
        n, left = self._tokens_left(vs, now_s)
        why = None
        if self._build_last_s is None or now_s - self._build_last_s > after:
            why = "no build just before it"
        elif self.keep_until is not None:
            why = "Keep this look holds"
        elif left <= 0:
            why = self._no_token_why(n)
        if why is not None:
            self._drop_inside_s = now_s                    # accent density and saturation up, inside the look
            self._token_refused(why, info)
            return
        self._tokens.append(now_s)
        self.sec_stats["tokens_spent"] += 1
        hb = float(self.cfg["build"]["hit_beats"]) * beat
        self._hit_want = (now_s, now_s + hb) if hb > 0 else None
        if self.pending is None or self.pending["by"] != "human":
            at, bar = grid.next_line(now_s + max(LOOK_MIN_LEAD_S, self._ev_lead), 1)   # the screens' lead (§5.4)
            self._announce(now_s, "drop", "director", grid, at_s=at, bar=bar, transition=dict(DROP_CUT),
                           section=info)
        self._log_event("token", {"decision": "spent", "at": "an unarmed drop", "event": info, "left": left - 1,
                                  "per_hour": n, "context": self._ctx_brief()})

    # -- light scenes (README S5.24) -----------------------------------------------------------------------------------
    def _poll_light_scenes(self) -> None:
        """Re-read config/live/light_scenes.yaml when it changed (the look engine hears it as a subscriber, on this
        thread). A scene that left the catalogue is dropped; the next tick picks again."""
        try:
            self.light_scenes.poll()
        except Exception:                                 # a broken catalogue never fails the tick
            log.exception("light scenes: poll failed")
        if self.light_scene is not None and self.light_scene not in self.light_scenes.scenes:
            self._log_event("light_scene", {"scene": None, "was": self.light_scene,
                                            "why": "it left config/live/light_scenes.yaml"})
            self.light_scene, self.light_scene_why, self._ls_reason = None, None, "scenes"

    def _pick_light_scene(self, reason: str, now_s: float, *, human: bool = False) -> None:
        """The lamps' light scene for a new look (``reason``: a LOOK_REASON_WORDS or LIGHT_REASON_WORDS key): a
        weighted random draw by the scenes' tags against room.direction, never the one showing now. Not while no
        palette resolves (neutral: hands off), nor while Keep this look holds unless it is a person's own change."""
        if not self.pal_on or (self.keep_until is not None and not human):
            return
        try:
            got = self.light_scenes.pick(self.direction, self._ls_rng, avoid=self.light_scene)
        except Exception:                                 # a broken catalogue never fails the tick
            log.exception("light scenes: pick failed")
            got = None
        if got is None:                                   # no scenes: show.yaml's looks.layers
            if self.light_scene is not None:
                self._log_event("light_scene", {"scene": None, "was": self.light_scene, "why": "no light scenes"})
            self.light_scene, self.light_scene_why = None, None
            return
        name, why, ws = got
        words = LIGHT_REASON_WORDS.get(reason) or LOOK_REASON_WORDS.get(reason) or str(reason).replace("_", " ")
        was = self.light_scene
        self.light_scene, self.light_scene_why, self._ls_at = name, f"{words}: {why}", now_s
        self._ls_reason = "first"
        self._ls_stats["picks"] += 1
        d = self.direction or {}
        self._log_event("light_scene", {"scene": name, "was": was, "why": self.light_scene_why, "reason": reason,
                                        "weights": {k: round(v, 4) for k, v in ws.items()},
                                        "direction": {k: d.get(k) for k in ("energy", "moods", "genre", "valence",
                                                                            "organic", "density")}})

    def _light_drop(self, ev: dict, at_s: float, now_s: float, grid: Grid) -> None:
        """Every drop the director hears (structure's, or a cue's forecast at its at_ms) goes to the look engine as
        LookData.light_scene_drop, whatever the peak tokens say: the running light scene's drop hits flash at the line.
        One a beat: a forecast and structure's own drop at the same line are one drop. Nothing while neutral."""
        if not self.pal_on:
            return
        beat = grid.beat_s if grid.has_grid and grid.beat_s else 0.5
        last = self._ls_drop
        if last is not None and abs(last["at_ms"] / 1000.0 - at_s) <= beat + 0.05:
            return
        self._ls_n += 1
        self._ls_drop = {"id": f"drop:{self._ls_n}", "at_ms": round(at_s * 1000.0, 1), "strength": 1.0}
        self._ls_stats["drops"] += 1
        self._log_event("light_drop", {"drop": dict(self._ls_drop), "scene": self.light_scene,
                                       "event": {**self._section_info(ev), "id": ev.get("id"),
                                                 "detail": ev.get("detail")},
                                       "ahead_s": round(at_s - now_s, 3)})

    def _build_begin(self, start_s: float, grid: Grid) -> None:
        bar = grid.bar_at(start_s)
        self._build = {"start_bar": bar, "idx": 0, "step_bar": float(bar)}

    def _build_progress(self) -> float:
        sec = (self._mc or {}).get("section") if isinstance((self._mc or {}).get("section"), dict) else {}
        return _c01(_num(sec.get("build_progress"), 0.0) or 0.0)

    def _phase_tick(self, now_s: float, vs: dict, grid: Grid) -> None:
        """The build's subdivision steps (every step_bars, step_bars_late from half way); a drop shown inside the look
        lasts while the phase is drop."""
        phase = self._phase_eff(now_s)
        if phase == "build" and self._grammar(vs, grid):
            if self._build is None:
                self._build_begin(now_s, grid)
            b = self._build
            bar = grid.bar_at(now_s)
            bc = self.cfg["build"]
            need = bc["step_bars_late"] if self._build_progress() >= 0.5 else bc["step_bars"]
            while b["idx"] < len(SUBDIVISIONS) - 1 and bar >= b["step_bar"] + need:
                b["idx"] += 1
                b["step_bar"] += need
        elif self._armed is None:
            self._build = None
        if self._drop_inside_s is not None and phase != "drop":
            self._drop_inside_s = None
        h = self._hit
        if h is not None and now_s >= h[1] + (0.0 if h[3] else float(self.cfg["build"]["hit_swell_ms"]) / 1000.0):
            self._hit = None                             # an unforeseen drop's swell falls back over swell_ms first

    def _hit_colours(self, look_pal: dict, vs: dict, now_s: float) -> dict[str, tuple]:
        """The unison hit: every lamp in the look's accent at full value, through its own role's limits (a key lamp's
        white and chroma cap still hold). Lamps only: never a screen."""
        out: dict[str, tuple] = {}
        hi_sat = float(vs["saturation"][1])
        for i, (did, dev) in enumerate(sorted(self.lamps.items())):
            _rn, rc = self._lamp_role(dev)
            rc2 = dict(rc, palette_role="accent", follow_screen=False, motion=False)
            out[did] = tuple(self._lamp_colour(dev, rc2, look_pal, {}, vs, hi_sat, 0.0, 0.0, i, now_s))
        return out

    # -- the screens' palette (agreed with 2f and 04, 16 Oct) ----------------------------------------------------------
    def _screens_palette(self, now_s: float, room: Any) -> None:
        """A renderer catalog palette goes to every live screen once per change (PATCH /api/v1/screens/{id}/look,
        idempotent); an art palette has no renderer name, so the screens keep their own. A screen that leaves live is
        forgotten, so it gets the palette again when it comes back. Stage 5 (S5.8): a person's pick on a screen holds
        every screen's palette (nothing is sent) until the hold ends."""
        sc = self._scr
        cfg = self.cfg["screens"]
        boot = _call(getattr(self.ctx, "room_boot", None))
        if isinstance(boot, str) and boot != self._look_boot:
            if self._look_boot is not None and self._look_source != contract.LOOK_SOURCE_STATES[0]:
                self._look_source = contract.LOOK_SOURCE_STATES[0]     # room restarted: probe again
                self._log_event("look_source", {"state": self._look_source, "boot": boot, "via": "room_boot"})
            self._look_boot = boot
        if self.screen_pick is not None and not (cfg["hold_hand_picks"] and cfg["send_palette"]):
            self._end_pick("show.yaml director.screens." + ("hold_hand_picks" if not cfg["hold_hand_picks"]
                                                            else "send_palette") + " is off")   # hot-reloaded off
            self._dirty()
            self._notify()
        if isinstance(room, dict) and room.get("reachable") is False:
            sc["why"] = "the room isn't answering"         # keep what was sent: a blip is not a screen leaving live
            return
        screens = room.get("screens") if isinstance(room, dict) and isinstance(room.get("screens"), dict) else {}
        live = sorted(sid for sid, x in screens.items() if isinstance(x, dict) and x.get("layer") == "live")
        for sid in [x for x in sc["sent"] if x not in live]:
            del sc["sent"][sid]
            sc["sent_at"].pop(sid, None)
        sc["live"] = live
        name = ((self.look or {}).get("palette") or {}).get("name") if getattr(self, "pal_on", self.known) else None
        if self._pal_chosen == contract.PALETTE_ART:      # art not on hand yet: the lamps' stand-in stays theirs
            name = contract.PALETTE_ART
        sc["palette"] = name
        if not cfg["send_palette"]:
            sc["why"] = "off (show.yaml director.screens.send_palette)"
            return
        if self.screen_pick is not None:
            sc["why"] = f"held: {self.screen_pick['label']} was picked on a screen"
            return
        if not getattr(self, "pal_on", self.known) or not name:
            sc["why"] = "no feel is set"
            return
        if name == contract.PALETTE_ART:
            sc["why"] = "an art palette has no renderer name: the screens keep their own"
            return
        if not self.palettes.has(name):
            sc["why"] = "no renderer palette in this look"
            return
        patch = getattr(self.ctx, "room_patch", None)
        if patch is None or self.ctx.loop is None:
            sc["why"] = "no room PATCH route here (BrainContext.room_patch)"
            return
        sc["why"] = None if live else "no screen is live"
        if sc["busy"]:
            return
        todo = [sid for sid in live if sc["sent"].get(sid) != name and not screens[sid].get("cutting")
                and now_s >= sc["retry"].get(sid, -math.inf)]
        if todo:
            sc["busy"] = True
            self._spawn(self.ctx.loop, self._patch_screens(todo, name, float(cfg["palette_transition_s"])),
                        busy="screens")

    async def _patch_screens(self, sids: list[str], name: str, tr_s: float) -> None:
        sc = self._scr
        try:
            for sid in sids:
                if self.screen_pick is not None and self.cfg["screens"]["hold_hand_picks"]:
                    break                                # a person picked on a screen meanwhile: hold
                path = SCREEN_LOOK_PATH.format(id=urllib.parse.quote(sid, safe=""))
                req = {"palette": name, "palette_transition_s": tr_s}
                boot = self._look_boot
                if self._look_source != contract.LOOK_SOURCE_STATES[2]:
                    req["source"] = contract.SHOW_SOURCE
                self._echo[sid] = (name, self._mono())   # before the PATCH: room's event can beat its answer
                st, body = await self.ctx.room_patch(path, req)
                if "source" in req:
                    if isinstance(st, int) and 200 <= st < 300:
                        self._set_look_source(contract.LOOK_SOURCE_STATES[1], boot, "patch")
                    elif (st, body.get("error") if isinstance(body, dict) else None) == SOURCE_REFUSED:
                        self._set_look_source(contract.LOOK_SOURCE_STATES[2], boot, "patch")   # the old room
                        del req["source"]
                        self._echo[sid] = (name, self._mono())
                        st, body = await self.ctx.room_patch(path, req)
                if isinstance(st, int) and 200 <= st < 300:
                    sc["sent"][sid] = name
                    sc["sent_at"][sid] = round(self._wall(), 3)
                    sc["retry"].pop(sid, None)
                    sc["error"] = None
                    sc["sends"] += 1
                    self._log_event("screens_palette", {"screen": sid, "palette": name, "transition_s": tr_s,
                                                        "source": "source" in req})
                else:
                    sc["retry"][sid] = self._mono() + SCREENS_RETRY_S
                    err = body.get("error") if isinstance(body, dict) else None
                    sc["error"] = f"PATCH {path}: {st or 'no answer'}" + (f" {err}" if err else "")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            sc["error"] = f"{type(e).__name__}: {e}"
        finally:
            sc["busy"] = False

    # -- palettes -------------------------------------------------------------------------------------------------------
    def _art_ready(self) -> bool:
        return any(a.get("colours") for a in self.art.values())

    def _art_ok(self) -> bool:
        """The art qualifies as a palette (75, 17 Oct: a rule, not a special case): its lightest colour clears
        director.art.min_lightness (OKLab L) and its most colourful clears min_chroma (OKLab chroma); else the next
        palette in the list takes over (a near-black or grey still has no colour to give)."""
        cols = [c for a in self.art.values() for c in (a.get("colours") or [])]
        if not cols:
            return False
        labs = [pal.to_oklab(c) for c in cols]
        return (max(L for L, _, _ in labs) >= float(self.cfg["art"]["min_lightness"])
                and max(math.hypot(a, b) for _, a, b in labs) >= float(self.cfg["art"]["min_chroma"]))

    def _candidates(self, vs: dict) -> list[str]:
        return [p for p in vs["palettes"] if p == contract.PALETTE_ART or self.palettes.has(p)]

    def _base_palette(self, vs: dict) -> Optional[str]:
        c = self._candidates(vs)
        return c[0] if c else None

    def _advance_palette(self, vs: dict) -> None:
        c = self._candidates(vs)
        if not c:
            self.palette_name = None
            return
        cur = self.palette_name if self.palette_name in c else None
        self.pal_idx = (c.index(cur) + 1) % len(c) if cur is not None else 0
        self.palette_name = c[self.pal_idx]

    def _resolve_palette(self, vs: dict) -> tuple[Optional[str], str]:
        """(name in use, source) for this tick: the pick, else the look's choice from the vibe's list (validated:
        a palette that has vanished is replaced by the vibe's first)."""
        pick = self.palette_pick
        if pick == contract.PALETTE_ART:
            return contract.PALETTE_ART, "human"
        if pick != contract.PALETTE_AUTO and self.palettes.has(pick):
            return pick, "human"
        c = self._candidates(vs)
        if self.palette_name not in c:
            if self.palette_name is None and contract.PALETTE_ART in vs["palettes"]:
                self.palette_name = contract.PALETTE_ART      # §8.6: the first look from the art where allowed
            else:
                self.palette_name = c[0] if c else None
        return self.palette_name, "vibe"

    def _roles_for(self, name: Optional[str], source: str, vs: dict) -> tuple[contract.LookPalette,
                                                                              dict[str, contract.PaletteRoles]]:
        """The look's palette (with roles) and per-screen roles (the art's, where a screen has its own still)."""
        screen_roles: dict[str, contract.PaletteRoles] = {}
        if name == contract.PALETTE_ART:
            if self._art_ready() and self._art_ok():
                cols: list = []
                for sid, a in self.art.items():
                    if a.get("colours"):
                        screen_roles[sid] = a["roles"]
                        cols += a["colours"]
                roles = pal.roles_from(cols)
                return {"name": contract.PALETTE_ART, "source": "art" if source == "vibe" else source,
                        "roles": roles, "emphasis": "base"}, screen_roles
            # no still yet, or one with no colour to give: the vibe's next catalog palette stands in
            alt = next((p for p in self._candidates(vs) if p != contract.PALETTE_ART), None)
            if alt is None:
                name = None
            else:
                return {"name": alt, "source": source, "roles": self.palettes.roles(alt), "emphasis": "base"}, {}
        if name is not None and self.palettes.has(name):
            return {"name": name, "source": source, "roles": self.palettes.roles(name), "emphasis": "base"}, {}
        w = pal.role(pal.white(vs["key_cct"]))
        return {"name": "neutral", "source": "neutral",
                "roles": {"base": w, "accent": dict(w), "highlight": dict(w)}, "emphasis": "base"}, {}

    # -- words (§8.2: human, listener, bands, vibe) ------------------------------------------------------------------------
    def _band_heard(self, music: float, conf: float, now_s: float) -> float:
        """The band words' energy as heard (2b's audit, 17 Oct: normalised loudness has no absolute level, so mastered
        dance and pop read peak throughout and an ambient pad read lively). With director.bands.base: an absolute base
        (the weighted genre_energy of music.context's genre words, the medium lane's onset rate / rate_full, its pulse
        clarity, music.context's mood_energy: the terms present, their weights renormalised) plus swing x (music -
        its median over swing_window_s), at most swing_max up and swing_max_down down, plus phase_lift for structure's phase (a drop
        or a peak lands where the song drops: mastered house barely moves in normalised loudness). Without a base, or with none of
        its terms: 0.5 + conf x (music - 0.5)."""
        heard = 0.5 + conf * (music - 0.5)
        bc = (self.cfg.get("bands") or {}).get("base")
        if not bc:
            return heard
        h = self._swing_hist
        if not h or now_s - h[-1][0] >= 1.0:
            h.append((now_s, music))
        while h and now_s - h[0][0] > bc["swing_window_s"]:
            h.popleft()
        terms: dict[str, float] = {}
        mg = (self._mc or {}).get("genre") if isinstance((self._mc or {}).get("genre"), dict) else {}
        words = mg.get("words") if isinstance(mg.get("words"), dict) else {}
        ge = bc["genre_energy"]
        known = {w: _num(p, 0.0) or 0.0 for w, p in words.items() if w in ge}
        tot = sum(known.values())
        if tot > 0:
            terms["genre"] = sum(ge[w] * p for w, p in known.items()) / tot
        # README S5.22: the listener's confident approach.intensity is the genre term (the style's base wildness),
        # else its confident genre word's genre_energy; slewed from what the term was. The ML's words are the fallback
        gd = self._guide
        want, src = ((gd["intensity"], "listener approach") if gd.get("intensity") is not None
                     else (ge[gd["genre"]], "listener genre") if gd.get("genre") in ge else (None, None))
        if want is not None:
            was = self._gterm if self._gterm is not None else terms.get("genre", want)
            dt = max(0.0, min(1.0, now_s - self._gterm_t)) if self._gterm_t is not None else 0.0
            step = LLM_SLEW_PER_S * dt
            self._gterm = was + max(-step, min(step, want - was))
            terms["genre"] = self._gterm
            self._gterm_from = src
        else:
            self._gterm = None
            self._gterm_from = "ml" if "genre" in terms else None
        self._gterm_t = now_s
        r = _call(getattr(self.ctx, "rhythm", None))
        if isinstance(r, dict) and _num(r.get("t_ms")) is not None and now_s - r["t_ms"] / 1000.0 <= RHYTHM_STALE_S:
            if _num(r.get("onset_rate")) is not None:
                terms["rate"] = _c01(r["onset_rate"] / bc["rate_full"])
            if _num(r.get("pulse_clarity")) is not None:
                terms["clarity"] = _c01(r["pulse_clarity"])
        if _num((self._mc or {}).get("mood_energy")) is not None:
            terms["moods"] = _c01(self._mc["mood_energy"])
        wsum = sum(bc["weights"][k] for k in terms)
        if wsum <= 0:
            self._band_base = None
            return heard
        base = sum(bc["weights"][k] * v for k, v in terms.items()) / wsum
        self._band_base = round(base, 3)
        med = statistics.median(m for _, m in h) if h else music
        sw = max(-bc["swing_max_down"], min(bc["swing_max"], bc["swing"] * conf * (music - med)))
        sec = (self._mc or {}).get("section") if isinstance((self._mc or {}).get("section"), dict) else {}
        lift = bc["phase_lift"].get(sec.get("phase"), 0.0) if isinstance(sec.get("phase"), str) else 0.0
        return _c01(base + sw + lift)

    def _band_words(self, e: float) -> Optional[dict]:
        b = self.cfg.get("bands")
        if not b or self.lv < 0.5:
            return None
        edges, hy = b["edges"], b["hysteresis"]
        i = self._band
        if i is None:
            i = sum(e >= x for x in edges)
        while i < len(edges) and e >= edges[i] + hy:
            i += 1
        while i > 0 and e < edges[i - 1] - hy:
            i -= 1
        self._band = i
        idx = i
        sp = b.get("split")
        if sp is not None:
            sb = b["split_band"]
            if i == sb:
                v = _num((self._latest_features() or {}).get(sp["by"]), None)
                if v is not None:
                    if v >= sp["up"]:
                        self._split_hi = True
                    elif v < sp["down"]:
                        self._split_hi = False
                idx = i + (1 if self._split_hi else 0)
            elif i > sb:
                idx = i + 1
        # a new row lands only once it has held dwell_s (2b's audit, 17 Oct: the words flapped up to 4 times a minute)
        now = self._now_s if self._now_s is not None else 0.0
        if self._band_idx is None or b["dwell_s"] <= 0:
            self._band_idx, self._band_cand, self._band_since = idx, None, None
        elif idx == self._band_idx:
            self._band_cand, self._band_since = None, None
        elif idx != self._band_cand:
            self._band_cand, self._band_since = idx, now
        elif now - (self._band_since if self._band_since is not None else now) >= b["dwell_s"]:
            self._band_idx, self._band_cand, self._band_since = idx, None, None
        return b["table"][self._band_idx]

    def _latest_features(self) -> Optional[dict]:
        f = _call(self.ctx.features)
        return f if isinstance(f, dict) else None

    def _llm_guide(self, b: Any) -> dict:
        """README S5.22 (owner, 18 Oct: the AI "can characterise 'this is soft jazz', which otherwise the system
        generally won't know"): what the listener's beliefs say for the style of approach, while they are fresh or
        holding and each part is confident (LLM_CONF): {"genre": its word, "intensity": approach.intensity,
        "change": approach.change (a director.listener_change word), "transitions": approach.transitions}; None
        where it isn't sure."""
        out: dict = {"genre": None, "intensity": None, "change": None, "transitions": None}
        if not isinstance(b, dict) or b.get("state") not in ("fresh", "holding"):
            return out
        g = b.get("genre") if isinstance(b.get("genre"), dict) else {}
        if isinstance(g.get("word"), str) and g["word"] and g["word"] != "unknown" \
                and (_num(g.get("conf"), 0.0) or 0.0) >= LLM_CONF:
            out["genre"] = g["word"]
        ap = b.get("approach") if isinstance(b.get("approach"), dict) else {}
        if _num(ap.get("intensity")) is not None and (_num(ap.get("intensity_conf"), 0.0) or 0.0) >= LLM_CONF:
            out["intensity"] = _c01(ap["intensity"])
        if ap.get("change") in self.cfg["listener_change"] and (_num(ap.get("change_conf"), 0.0) or 0.0) >= LLM_CONF:
            out["change"] = ap["change"]
        if isinstance(ap.get("transitions"), str) and ap["transitions"] \
                and (_num(ap.get("transitions_conf"), 0.0) or 0.0) >= LLM_CONF:
            out["transitions"] = ap["transitions"]
        return out

    def _words(self, vs: dict) -> dict:
        b = self.music.get("beliefs")
        llm_moods: list[str] = []
        if isinstance(b, dict) and b.get("state") in ("fresh", "holding"):
            llm_moods = [m["word"] for m in b.get("moods") or []
                         if isinstance(m, dict) and isinstance(m.get("word"), str)
                         and (_num(m.get("conf"), 0.0) or 0.0) >= LLM_CONF]
        gd = self._guide
        llm_genre = gd.get("genre")
        # the genre (README S5.22, owner 18 Oct): the listener's when it is confident (it characterises the music,
        # which the local ML generally can't), then the local ML's (music.context, fresh, confident), then the
        # vibe's. No one picks a genre by hand on the card.
        ml_genre = None
        mg = (self._mc or {}).get("genre") if isinstance((self._mc or {}).get("genre"), dict) else None
        ml_on = mg is not None and mg.get("via") in ("ml", "ml+listener")
        if (ml_on and isinstance(mg.get("word"), str)
                and mg["word"] and (_num(mg.get("conf"), 0.0) or 0.0) >= float(self.cfg["genre_min_conf"])):
            ml_genre = mg["word"]
        genre, genre_from = ((llm_genre, "listener") if llm_genre else (ml_genre, "ml") if ml_genre
                             else (vs["genre"], "vibe") if vs["genre"] else (None, None))
        # room.direction's genres (soft: word -> share): the listener's word while it leads, else the local ML's
        # while it reads one (even a mixed one, under genre_min_conf), else the chosen word's
        mw = mg.get("words") if ml_on and isinstance(mg.get("words"), dict) else None
        genres = ({llm_genre: 1.0} if llm_genre
                  else {str(w): round(_c01(_num(p, 0.0) or 0.0), 3) for w, p in mw.items()} if mw
                  else {genre: 1.0} if genre else {})
        band = self._band_words(self.music.get("band_energy", self.music.get("energy", vs["energy_centre"]))) \
            if self.music else None
        if self.moods:
            moods, src = list(self.moods), "human"
        elif llm_moods:
            moods, src = llm_moods, "listener"
        elif band and band["moods"]:
            moods, src = list(band["moods"]), "bands"
        else:
            moods, src = list(vs["moods"]), "vibe"
        # transition_style and change_rate (README S5.22): a person's or the feel's own (vs), then the listener's
        # confident approach, then the band row's. room.direction's change_rate takes these only with no feel (a
        # feel's own change_rate always wins there)
        if vs["transition_style"]:
            style, style_from = vs["transition_style"], "feel"
        elif gd.get("transitions"):
            style, style_from = gd["transitions"], "listener"
        elif (band or {}).get("transition_style"):
            style, style_from = band["transition_style"], "bands"
        else:
            style, style_from = None, None
        lcr = self.cfg["listener_change"].get(gd["change"]) if gd.get("change") else None
        cr, cr_from = ((lcr, "listener") if lcr is not None
                       else ((band or {}).get("change_rate"), "bands") if (band or {}).get("change_rate") is not None
                       else (None, None))
        return {"moods": moods, "moods_from": src, "genre": genre, "genre_from": genre_from, "genres": genres,
                "change_rate": cr, "change_from": cr_from, "transition_style": style, "style_from": style_from,
                "fx_moods": list(band["fx_moods"]) if band and band["fx_moods"] else None,
                "band": band["name"] if band else None}

    # -- composing the look ------------------------------------------------------------------------------------------------
    def _steps_max(self, vs: dict) -> int:
        v = self._from_vibe(self.cfg["steps"].get("max_from"), vs, "max_steps")
        return max(0, int(v))

    def _step_size(self, vs: dict) -> float:
        return float(self._from_vibe(self.cfg["steps"].get("size_from"), vs, "step_size"))

    @staticmethod
    def _from_vibe(spec: Any, vs: dict, fallback_field: str) -> Any:
        if isinstance(spec, str) and spec in vs:
            v = vs[spec]
            return v if _num(v) is not None else vs[fallback_field]
        v = _num(spec)
        return v if v is not None else vs[fallback_field]

    def _role_value(self, spec: Any, vs: dict) -> Optional[float]:
        """A role field: a number, or the name of a vibe field to take it from."""
        if isinstance(spec, str):
            return _num(vs.get(spec)) if spec in vs else None
        return _num(spec)

    def _emphasis_of(self, arousal: float) -> str:
        i = self._emphasis
        edges = self.cfg["emphasis_edges"]
        while i < 2 and arousal >= edges[i] + EMPHASIS_HYST:
            i += 1
        while i > 0 and arousal < edges[i - 1] - EMPHASIS_HYST:
            i -= 1
        self._emphasis = i
        return contract.PALETTE_ROLES[i]

    def _lamp_role(self, dev: dict) -> tuple[str, dict]:
        lc = self.lamps_cfg
        rn = dev.get("role") if dev.get("role") in lc["roles"] else None      # a fixture's own role wins (Stage 5)
        if rn is None:
            rn = lc["placement_roles"].get(dev.get("placement") or "", None) if dev.get("placement") else None
        rn = rn or lc["default_role"]
        if rn is None or rn not in lc["roles"]:
            return "neutral", NEUTRAL_ROLE
        return rn, lc["roles"][rn]

    def _house_rgb(self, did: str, room: Any) -> pal.Rgb:
        """A lamp's house light: room's stored state for it, else its fixtures.yaml ``house`` (hex x intensity; a DMX
        fixture room knows nothing about, README S5.14), else show.yaml director.house (§8.4: warm white at 80 %)."""
        h = self.cfg["house"]
        fallback = self._fixture_house(did)
        if fallback is None:
            fallback = pal.scale_level(pal.white(h["white_k"]), h["level"])
        st = (room or {}).get("stored_restore") if isinstance(room, dict) else None
        v = st.get(did) if isinstance(st, dict) else None
        if isinstance(v, dict) and "value" in v and "on" not in v:
            v = v["value"]
        if isinstance(v, dict):
            if not v.get("on"):
                return (0.0, 0.0, 0.0)
            bri = _c01((_num(v.get("bri"), 100.0) or 0.0) / 100.0)
            if isinstance(v.get("xy"), (list, tuple)) and len(v["xy"]) == 2:
                c = pal.from_xy(*v["xy"])
            elif _num(v.get("mirek")):
                c = pal.white(1e6 / max(1.0, float(v["mirek"])))
            else:
                c = pal.white(h["white_k"])
            return pal.scale_level(c, bri)
        if isinstance(v, str) and v not in HOUSE_FOLLOWING:
            return (0.0, 0.0, 0.0)                         # the room's stored state is the lamp switched off
        return fallback

    def _fixture_house(self, did: str) -> Optional[pal.Rgb]:
        """fixtures.yaml's ``house`` for a registry lamp, as the look engine reads it (hex x intensity, intensity
        default 1.0); None when it has none, or it doesn't read."""
        hv = (self.lamps.get(did) or {}).get("house")
        if not isinstance(hv, dict):
            return None
        try:
            c = pal.rgb_of(str(hv.get("hex", "")))
        except ValueError:
            return None
        k = _num(hv.get("intensity", 1.0))
        if k is None:
            return None
        k = _c01(k)
        return (c[0] * k, c[1] * k, c[2] * k)

    def _compose(self, now_s: float, vs: dict, known: bool, grid: Grid, room: Any) -> None:
        m = self.music
        e, u, arousal = m["energy"], m["u"], m["arousal"]
        words = self._words(vs)
        pal_on = getattr(self, "pal_on", known)
        vp = getattr(self, "vp", vs) if pal_on else vs
        name, source = self._resolve_palette(vp) if pal_on else (None, "neutral")
        self._pal_chosen = name                           # the screens' palette follows the choice, not a stand-in
        look_pal, screen_roles = self._roles_for(name, source, vp)
        if name == contract.PALETTE_ART and self._art_ready() and not self._art_ok():
            self._pal_chosen = look_pal.get("name")       # the art has no colour to give: the next entry is the choice
        emphasis = self._emphasis_of(arousal)
        look_pal["emphasis"] = emphasis
        xpr = self._express_now(now_s) if known else 0.0    # a section shown inside the look (§8.5)
        ux = _c01(u + xpr)
        sat = _lerp(vs["saturation"], 0.5 * u + 0.5 * arousal)
        contrast = _lerp(vs["contrast"], u)
        level = _lerp(vs["level"], ux)
        floor = vs["floor"]
        speed = _lerp(vs["motion"], arousal)
        amount = _lerp(vs["motion"], u)
        density = ux
        spread = _c01(amount + xpr)
        beat_lock = bool(vs["beat_lock"]) and grid.gate == "beat"
        # the build -> drop grammar (§8.5): lamps and DMX only; the screens below never read any of it
        sub, narrow, dip = 1.0, 0.0, None
        gram = known and self._grammar(vs, grid)
        mphase = self._phase_eff(now_s) if gram else contract.SECTION_PHASES[0]
        if gram:
            if mphase == "build" and self._build is not None:
                bp = self._build_progress()
                sat += (float(vs["saturation"][1]) - sat) * bp
                narrow = _c01(bp * float(self.cfg["build"]["narrow"]))
                sub = SUBDIVISIONS[self._build["idx"]]
            elif mphase == "drop" and self._drop_inside_s is not None:
                sat, density = float(vs["saturation"][1]), 1.0
            elif mphase == "breakdown":
                bd = self.cfg["breakdown"]
                floor = min(1.0, floor + bd["floor_add"])
                speed *= bd["motion_scale"]
                amount *= bd["motion_scale"]
                spread = _c01(amount + xpr)
                sat *= bd["saturation_scale"]
            a = self._armed
            if a is not None and grid.bar_s and now_s >= a["at_s"] - float(self.cfg["build"]["dip_bars"]) * grid.bar_s:
                dip = float(self.cfg["build"]["dip_level"])
        song_dip = None
        sd = self._song_dip
        if sd is not None and now_s >= sd["until"]:
            self._release_dip(now_s, "dark_max_s passed")    # never dark for long: back over in_s
            sd = None
        if sd is not None and pal_on:
            song_dip = _c01((now_s - sd["t0"]) / sd["out_s"])
        roles_key = (look_pal["name"], emphasis, tuple(r["hex"] for r in look_pal["roles"].values()),
                     tuple((sid, tuple(r["hex"] for r in sr.values())) for sid, sr in sorted(screen_roles.items())))
        roles_changed = self._roles_key is not None and roles_key != self._roles_key
        self._roles_key = roles_key
        self._screen_roles = screen_roles
        lamps: dict[str, contract.LampOut] = {}
        screens: dict[str, contract.ScreenOut] = {}
        colours: dict[str, pal.Rgb] = {}
        if pal_on:                                       # a palette resolves (a feel, or no_feel's): the devices' colours
            lamp_level = max(floor, level)
            if dip is not None:
                lamp_level = min(lamp_level, dip)        # pre-drop: the lamps dip (screens never do)
            if song_dip is not None:                     # a song's outgoing half (S5.17): down to the floor, never off
                lamp_level += (min(lamp_level, floor) - lamp_level) * song_dip
            for i, (did, dev) in enumerate(sorted(self.lamps.items())):
                rname, rc = self._lamp_role(dev)
                if self.house:
                    rgb, lv_out = self._house_rgb(did, room), None
                else:
                    c = self._lamp_colour(dev, rc, look_pal, screen_roles, vs, sat, speed, amount, i, now_s, narrow)
                    lv_out = self.cfg["low"]["level"] if self.low else lamp_level
                    rgb = pal.scale_level(c, lv_out)
                lv_rep = lv_out if lv_out is not None else max(rgb)
                colours[did] = rgb
                lamps[did] = {"role": rname, "screen": dev.get("screen"), "hex": pal.hex_of(rgb),
                              "rgb": [round(x, 4) for x in rgb], "level": round(lv_rep, 3)}
            for did, dev in sorted(self.screens.items()):
                roles = screen_roles.get(dev.get("screen") or "", look_pal["roles"])
                c = pal.full(pal.rgb_of(roles[emphasis]["hex"]))          # the screens rule: never scaled
                colours[did] = c
                screens[did] = {"hex": pal.hex_of(c), "rgb": [round(x, 4) for x in c]}
            # the unison hit: an armed line's (from the line: predicted), or an unarmed drop's (now: nobody predicted
            # it); lamps only, never under House lights or Lights low. It goes out in LookData.hit, never in
            # palette_fn: each output takes it from its driver's caps (§5.4: Hue swells for an unforeseen drop)
            if self.house or self.low or not gram:
                self._hit = self._hit_want = None
            elif self._armed is not None:
                hb = float(self.cfg["build"]["hit_beats"]) * (grid.beat_s or 0.5)
                a_s = self._armed["at_s"]
                if now_s >= a_s - max(1.0, float(self.cfg["build"]["dip_bars"]) * (grid.bar_s or 2.0)):
                    # coloured from the dip on (it starts exactly at the line)
                    self._hit = (a_s, a_s + hb, self._hit_colours(look_pal, vs, now_s), True) if hb > 0 else None
            elif self._hit_want is not None:
                self._hit = (self._hit_want[0], self._hit_want[1], self._hit_colours(look_pal, vs, now_s), False)
                self._hit_want = None
        accents = {"style": vs["accents"], "density": round(_c01(density), 3), "subdivision": sub,
                   "strobe": bool(vs["strobe"]), "beat_lock": beat_lock}
        if not pal_on and self.light_scene is not None:   # neutral: hands off, the lamps run show.yaml's looks.layers
            self.light_scene, self.light_scene_why, self._ls_reason = None, None, "first"
        tr = self._look_transition or self._transition(vs)
        self.look = {"t_ms": round(now_s * 1000.0, 1), "seq": self._look_seq, "look_id": f"L{self.look_n}",
                     "vibe": self._vibe_name, "mode": self.mode, "palette": look_pal, "level": round(level, 3),
                     "floor": round(floor, 3), "house": self.house, "low": self.low, "key_cct": vs["key_cct"],
                     "chroma_max": vs["chroma_max"], "lamp_chroma_max": vs["lamp_chroma_max"],
                     "saturation": round(sat, 3), "contrast": round(contrast, 3),
                     "motion": {"speed": round(speed, 3), "amount": round(amount, 3)}, "accents": accents,
                     "spread": round(spread, 3), "change_rate": vs["change_rate"], "transition": tr,
                     "lamps": lamps, "screens": screens,
                     "dip": bool(pal_on and (dip is not None or song_dip is not None) and not self.house
                                 and not self.low),
                     "hit": self._hit_view(), "compose": self._cblock,
                     "light_scene": self.light_scene, "light_scene_why": self.light_scene_why,
                     "light_scene_drop": dict(self._ls_drop) if self._ls_drop else None}
        bars = 0
        if grid.ok and self.look_bar is not None:
            bars = max(0, grid.bar_at(now_s) - self.look_bar)
        phase = 0.0
        if self.look_started_s is not None:
            phase = _c01(bars / self._min_bars(vs)) if grid.ok else _c01((now_s - self.look_started_s)
                                                                          / max(1.0, vs["dwell_s"][1]))
        b = m.get("beliefs")
        sec_label = None
        if isinstance(b, dict) and isinstance(b.get("section"), dict) and \
                (_num(b["section"].get("conf"), 0) or 0) >= LLM_CONF and isinstance(b["section"].get("label"), str):
            sec_label = b["section"]["label"]
        section_ref = {"id": self.look_n, "label": sec_label, "phase": round(phase, 3), "bars_in_section": int(bars)}
        msec = (self._mc or {}).get("section") if isinstance((self._mc or {}).get("section"), dict) else None
        if msec is not None and isinstance(msec.get("id"), int):      # structure's section (README S4.8)
            bis, exp = _num(msec.get("bars_in_section")), _num(msec.get("expected_bars"))
            section_ref = {"id": int(msec["id"]),
                           "label": msec.get("label") if isinstance(msec.get("label"), str) else None,
                           "phase": round(_c01(bis / exp), 3) if bis is not None and exp else 0.0,
                           "bars_in_section": int(bis or 0)}
        dens = _c01(m["onsets"])
        r = _call(getattr(self.ctx, "rhythm", None))
        if isinstance(r, dict) and _num(r.get("t_ms")) is not None and _num(r.get("density")) is not None \
                and now_s - r["t_ms"] / 1000.0 <= RHYTHM_STALE_S:
            dens = _c01(r["density"])                    # the medium lane's onset density while it is fresh
        fam = vs["palette_family"] or (self.palettes.family(look_pal["name"]) if look_pal["name"] else None)
        # the renderer's energy and change_rate: a feel's own (its range, centre and follow amount compose them);
        # with no feel, the music's (2b's audit, 17 Oct: the neutral follow squeezed the energy to 0.25-0.75 and
        # change_rate sat at 0.5): the band energy (absolute base + swing) and the band row's change_rate (show.yaml
        # director.bands.words.*.change_rate). The lamps keep e.
        feel_on = self.known and self._vibe_name is not None
        e_dir = e if feel_on else _c01(_num(m.get("band_energy"), e) or 0.0)
        cr = vs["change_rate"] if feel_on or words.get("change_rate") is None else words["change_rate"]
        self.direction = {"t_ms": round(now_s * 1000.0, 1), "seq": self._dir_seq, "source": "show_service",
                          "note": f"show_service · {self._vibe_name}" if self._vibe_name else None,
                          "vibe": self._vibe_name, "energy": round(e_dir, 3), "pace": round(_c01(m["pace"]), 3),
                          "intensity": 1.0, "change_rate": round(cr, 3), "moods": words["moods"],
                          "genre": words["genre"], "genres": words.get("genres") or {},
                          "transition_style": words["transition_style"],
                          "palette_family": fam,
                          "fx": {"density": round(_c01(0.1 + 0.8 * e_dir), 3),
                                 "rate": round(_c01(0.05 + 0.85 * e_dir), 3),
                                 "stages": None, "moods": words["fx_moods"]},
                          "arousal": round(arousal, 3), "tension": round(_c01(m["tension"]), 3),
                          "section": section_ref,
                          "density": round(dens, 3),
                          "hold": self._held(),
                          "valence": _r3((self._mc or {}).get("valence")),
                          "organic": _r3((self._mc or {}).get("acoustic")),
                          "groove": round(_c01(m["drive"] * (grid.conf if grid.gate != "none" else 0.0)), 3),
                          "beat_lock": beat_lock, "loose": bool(vs["beat_lock"]) and m["fresh"] and grid.gate != "beat"}
        if self.cfg["compose"]["mode"] == comp.DRIVE:
            self._drive_direction(self.direction, vs)     # README S5.18: drive only
        self._words_now = words
        if not pal_on:
            return
        if not known and self._pal is None:
            self._entry_fade = True                       # no feel, first colours: fade in from the house light
        # palette_fn: glide to the new colours
        if self._entry_fade:
            self._entry_fade = False
            start = None                                    # another vibe was on: cross over from what shows now
            if self._pal is None:                           # from neutral: the lamps were at their house state
                start = {did: self._house_rgb(did, room) for did in self.lamps}
                start.update({did: colours[did] for did in self.screens if did in colours})
            self._set_colours(colours, now_s, float(self.cfg["entry_fade_s"]), start=start)
        elif self._fade_s is not None:
            d = self._fade_s
            self._fade_s = None
            self._set_colours(colours, now_s, d)
        elif self._house_toggled is not None:
            d = self._house_toggled
            self._house_toggled = None
            self._set_colours(colours, now_s, d)
        else:
            self._set_colours(colours, now_s, ROLE_GLIDE_S if roles_changed else GLIDE_S)

    def _lamp_colour(self, dev: dict, rc: dict, look_pal: dict, screen_roles: dict, vs: dict, sat: float,
                     speed: float, amount: float, idx: int, now_s: float, narrow: float = 0.0) -> pal.Rgb:
        """One lamp at full value from its role's fields (no role is known by name): the palette role (moved on by the
        look's variation when the role moves), a slow drift toward the next role, a build's narrowing toward the
        accent (``narrow``, §8.5), x saturation, white first where the role has a white (its chroma cap then limits
        the tint's distance from that white: ``white_tint``), else the chroma cap, then the hues to avoid."""
        pr = rc.get("palette_role") if rc.get("palette_role") in contract.PALETTE_ROLES else "base"
        moving = bool(rc.get("motion"))
        if moving and self.variation:
            pr = contract.PALETTE_ROLES[(contract.PALETTE_ROLES.index(pr) + self.variation) % 3]
        roles = look_pal["roles"]
        if rc.get("follow_screen") and dev.get("screen") in screen_roles:
            roles = screen_roles[dev["screen"]]
        nb = contract.PALETTE_ROLES[(contract.PALETTE_ROLES.index(pr) + 1) % 3]
        depth = 0.0
        if moving and amount > 0.0:
            period = 120.0 + (15.0 - 120.0) * _c01(speed)          # one slow drift: 120 s .. 15 s a cycle
            ph = (now_s / period + idx * 0.5 * _c01(amount)) % 1.0
            depth = 0.5 * _c01(amount) * (0.5 - 0.5 * math.cos(2.0 * math.pi * ph))
        wk = self._role_value(rc.get("white_k"), vs)
        cm = self._role_value(rc.get("chroma_max"), vs)
        ah = rc.get("avoid_hues")
        nq = round(_c01(narrow) / QUANT)
        key = (roles[pr]["hex"], roles[nb]["hex"] if depth else None, round(depth / QUANT),
               round(sat / QUANT), wk, cm, tuple(tuple(x) for x in ah) if ah else None,
               (roles["accent"]["hex"], nq) if nq else None)
        hit = self._colour_cache.get(key)
        if hit is not None:
            self._colour_cache.move_to_end(key)
            return hit
        q_sat, q_depth = key[3] * QUANT, key[2] * QUANT
        c = pal.full(pal.rgb_of(roles[pr]["hex"]))
        if q_depth > 0.0:
            c = pal.full(pal.mix(c, pal.full(pal.rgb_of(roles[nb]["hex"])), q_depth))
        if nq:
            c = pal.full(pal.mix(c, pal.full(pal.rgb_of(roles["accent"]["hex"])), nq * QUANT))
        c = pal.scale_saturation(c, q_sat)
        if wk is not None:                                          # white first, tinted toward the colour, the
            c = white_tint(pal.white(wk), c, q_sat, cm)             # tint at most cm from the white (S5.14)
        elif cm is not None:
            c = pal.limit_chroma(c, cm)
        if ah:
            c = pal.avoid_hues(c, ah)
        c = pal.full(c)
        self._colour_cache[key] = c
        if len(self._colour_cache) > COLOUR_CACHE:
            self._colour_cache.popitem(last=False)
        return c

    # -- composition and balance (README S5.18; compose.py) ------------------------------------------------------------------
    def _check_response(self, force: bool = False) -> None:
        """stream_service's config/live/music_response.json (75's file; show.yaml director.compose.response_file):
        its tempo.fold_min / fold_max, the ONE tempo fold, read again when it changes (mtime; never a second range
        here). Missing or bad: compose.FOLD_DEFAULT [70, 180), and the compose view says why."""
        rf = str(self.cfg["compose"]["response_file"])
        lo0, hi0 = comp.FOLD_DEFAULT
        path = pathlib.Path(rf).expanduser()
        if not path.is_absolute():
            path = contract.REPO_ROOT / path
        try:
            st = path.stat()
            sig: tuple = (str(path), st.st_mtime_ns, st.st_size)
        except OSError as e:
            sig = (str(path), None, None)
            if force or sig != self._resp_sig:
                self._resp_sig, self._fold = sig, comp.FOLD_DEFAULT
                self._resp_problem = f"{rf}: {e.strerror or e}: the tempo folds into [{lo0:g}, {hi0:g})"
            return
        if not force and sig == self._resp_sig:
            return
        self._resp_sig = sig
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            t = data.get("tempo") if isinstance(data, dict) else None
            lo = _num(t.get("fold_min")) if isinstance(t, dict) else None
            hi = _num(t.get("fold_max")) if isinstance(t, dict) else None
            if lo is None or hi is None or not 0.0 < lo < hi:
                raise ValueError(f"tempo.fold_min / fold_max {lo!r} / {hi!r} are not 0 < min < max")
            self._fold, self._resp_problem = (lo, hi), None
        except Exception as e:                           # noqa: BLE001  degrade, never refuse
            self._fold = comp.FOLD_DEFAULT
            self._resp_problem = f"{rf}: {type(e).__name__}: {e}: the tempo folds into [{lo0:g}, {hi0:g})"

    def _limits(self) -> dict:
        """The limits table in use (compose.table): the feel's compose block, else show.yaml director.compose.defaults
        (no feel, or a feel without one). Cached per (vibes load, feel, settings); its validator lines go to the
        snapshot's compose view."""
        cm = self.cfg["compose"]
        name = self._vibe_name if self.known else None
        key = (getattr(self.vibes, "generation", 0), name, self._cfg_gen)
        if key != self._ctab_key:
            self._ctab_key = key
            get = getattr(self.vibes, "compose_limits", None)
            lim = get(name) if (get is not None and name is not None) else None
            self._csrc = "feel" if lim is not None else "defaults"
            self._ctab = comp.table(lim if lim is not None else cm["defaults"])
            w = dict(cm["weights"])                        # the validator's W: the largest a class can take
            dyn = [float(r["dynamics"]) for r in self.lamps_cfg["roles"].values()
                   if _num(r.get("dynamics")) is not None]
            if dyn:
                w[comp.LAMPS] = max([float(w.get(comp.LAMPS, 1.0))] + dyn)
            self._cval = comp.validate(self._ctab, w, f"vibes.{name}.compose" if lim is not None
                                       else "director.compose.defaults")
        return self._ctab

    def _weight(self, cls: str, rc: Optional[dict]) -> float:
        """W: a lamp role's own ``dynamics`` (show.yaml lamps.roles), else director.compose.weights.<class>."""
        w = _num((rc or {}).get("dynamics"))
        return w if w is not None else float(self.cfg["compose"]["weights"].get(cls, 1.0))

    def _compose_plan(self, now_s: float, dt: float, room: Any) -> None:
        """The composer's plan for this tick (README S5.18), before the look is composed: compose.step on this tick's
        inputs, the enabled set (compose.enabled: live screens, members exactly dmx), each enabled appliance's plan
        and the classes' nominal plans (room.direction and the lamps without a plan of their own take those). Only
        reads: what it builds goes out as LookData.compose, and in drive mode through ``_drive_direction`` and
        ``_plan_out``."""
        cm = self.cfg["compose"]
        m = self.music
        f = self._feat_now
        pv = m.get("pv")
        piano = _c01(_num(pv.get("velocity"), 0.5) or 0.0) if m.get("playing") and isinstance(pv, dict) else None
        phase = self._phase_eff(now_s)
        if self._end_armed is not None and now_s > self._end_armed + END_FADE_S:
            self._end_armed = None
        inp = comp.Inputs(now_s=now_s, dt=dt, fresh=bool(m.get("fresh")),
                          gate=_num(f.get("gate")) if f else None, lufs_s=_num(f.get("lufs_s")) if f else None,
                          liveness=self.lv, piano=piano, talking=self._situation("talking") is True,
                          fade=phase == "outro" or self._end_armed is not None, tension=self._tension or 0.0,
                          build=self._build_progress() if phase == "build" else 0.0)
        terms = comp.step(self._cstate, inp, cm)
        tab = self._limits()
        scr_on, mem_on = comp.enabled(room)
        scr_view = room.get("screens") if isinstance(room, dict) and isinstance(room.get("screens"), dict) else {}
        hold_lamps = bool(self.house or self.low)
        w_scr = self._weight(comp.SCREENS, None)
        apps: dict[str, dict] = {}
        for sid in scr_on:
            cut = bool((scr_view.get(sid) or {}).get("cutting"))     # the cut guard (step 3: the MasterBus's own)
            apps[sid] = comp.plan_one(terms, tab, cm, comp.SCREENS, weight=w_scr, hold=cut)[0]
        lamps: dict[str, tuple[float, float]] = {}
        for mid in mem_on:
            _rn, rc = self._lamp_role(self.lamps.get(mid) or {})
            v = comp.plan_one(terms, tab, cm, comp.LAMPS, weight=self._weight(comp.LAMPS, rc), hold=hold_lamps)[0]
            apps[mid] = v
            lamps[mid] = (v[comp.LEVEL], v[comp.PULSE])
        sv, ss, sq, sh = comp.plan_one(terms, tab, cm, comp.SCREENS, weight=w_scr)
        _rn, rc0 = self._lamp_role({})                     # the default role's weight
        lv = comp.plan_one(terms, tab, cm, comp.LAMPS, weight=self._weight(comp.LAMPS, rc0), hold=hold_lamps)[0]
        lamps["*"] = (lv[comp.LEVEL], lv[comp.PULSE])
        self._cnom = {comp.SCREENS: sv, comp.LAMPS: lv}
        self._clamps = lamps
        loud, rlu = terms["loud"], terms["range_lu"]
        self._cblock = {"mode": cm["mode"], "limits": self._csrc,
                        "appliances": {k: comp.rounded(v) for k, v in apps.items()},
                        "nominal": {k: comp.rounded(v) for k, v in self._cnom.items()},
                        "balance": {"loud": round(loud, 3) if loud is not None else None,
                                    "range": round(terms["range"], 3), "range_lu": round(rlu, 2) if rlu is not None
                                    else None, "lift": comp.rounded(terms["lift"]), "s": comp.rounded(ss),
                                    "quiet": comp.rounded(sq), "h": comp.rounded(sh),
                                    "liveness": round(terms["liveness"], 3), "fade": terms["fade"]},
                        "enabled": scr_on + mem_on}

    def _drive_direction(self, d: dict, vs: dict) -> None:
        """Drive mode only (README S5.18): room.direction's intensity is the screens' music depth, and it gains
        pulse_depth and peak. Observe never calls this, so the message stays exactly as it was."""
        sv = (self._cnom or {}).get(comp.SCREENS)
        if sv is None:
            return
        d["intensity"] = round(_c01(sv[comp.DEPTH]), 3)
        d["pulse_depth"] = round(max(0.0, sv[comp.PULSE]), 3)
        d["peak"] = self._peak(vs)

    def _peak(self, vs: dict) -> dict:
        """room.direction.peak, the build/drop words (the renderer's build pick, from step 3): the feel's energy top,
        its own moods and transition style when it sets them, else the top energy band's (show.yaml director.bands),
        else the vibe's neutral."""
        b = self.cfg.get("bands")
        top = (b["table"][-1] if b and b.get("table") else None) or {}
        feel = self._vibe_name is not None and self.known
        moods = list(vs["moods"]) if feel and vs["moods"] else list(top.get("moods") or vs["moods"])
        style = (vs["transition_style"] if feel else None) or top.get("transition_style") or vs["transition_style"]
        return {"energy": round(float(vs["energy"][1]), 3), "moods": moods, "transition_style": style}

    def _plan_out(self, depth: Optional[float]) -> None:
        """Drive mode (README S5.18): artnet_out's lamps take ``set_look_out(plan=...)``: ``depth`` (the accent depth
        before the clock's gate; None with no feel: artnet_out keeps its own), ``middle_depth`` (show.yaml
        looks.middle_depth: the clock's dmx rung) and each lamp's (level, pulse), "*" for one without its own plan.
        Sent when it moves by director.compose.smooth.deadband or more. Observe: nothing, and a plan sent before is
        cleared once (``plan={}``), so artnet_out is exactly as before."""
        cm = self.cfg["compose"]
        if cm["mode"] != comp.DRIVE or self._clamps is None:
            if self._plan_sent is not None:
                self._plan_sent = None
                _call(self.ctx.set_look_out, plan={})
            return
        plan = {"depth": None if depth is None else round(float(depth), 3), "middle_depth": self._middle_depth,
                "lamps": {k: (round(lv, 3), round(pu, 3)) for k, (lv, pu) in self._clamps.items()}}
        last = self._plan_sent
        db = float(cm["smooth"]["deadband"])
        moved = (last is None or last["depth"] != plan["depth"] or last["middle_depth"] != plan["middle_depth"]
                 or last["lamps"].keys() != plan["lamps"].keys()
                 or any(abs(a - b) >= db - 1e-9 for k, v in plan["lamps"].items()
                        for a, b in zip(v, last["lamps"][k])))
        if moved:
            self._plan_sent = plan
            _call(self.ctx.set_look_out, plan=plan)

    # -- look_fn (Stage 5: the rig's look engine, any thread) ----------------------------------------------------------------
    def look_fn(self) -> Optional[contract.LookData]:
        """The latest LookData, swapped by reference each compose (README S5.8): the look engine's ``look_fn``."""
        return self.look

    def _hit_view(self) -> Optional[contract.LookHit]:
        """LookData.hit: the drop's unison hit while it lasts (README S5.14), or None."""
        h = self._hit
        if h is None:
            return None
        return {"t0_ms": round(h[0] * 1000.0, 1), "t1_ms": round(h[1] * 1000.0, 1), "predicted": bool(h[3]),
                "swell_ms": float(self.cfg["build"]["hit_swell_ms"]),
                "lamps": {k: [round(x, 4) for x in v] for k, v in h[2].items()}}

    # -- palette_fn (the rig's look engine, and show-artnet; any thread) ------------------------------------------------------
    def palette_fn(self, t_s: float) -> Optional[dict[str, tuple[float, float, float]]]:
        """The director's colours at ``t_s`` (crossfades, House, Low, dips). The drop's unison hit is not in it: it
        is LookData.hit, which each output takes by its caps (looks.py), and ``maestro_palette_fn`` for the virtual
        Maestro (README S5.14)."""
        p = self._pal
        if p is None:
            return None
        if not self.known and self._relaxed:
            return None                                   # a silent room with no feel: artnet_out's P0 idle, as before
        t0, t1, frm, to, frm_lab, to_lab = p
        if t_s >= t1:
            return to
        if t_s <= t0:
            return frm
        x = (t_s - t0) / (t1 - t0)
        out = dict(to)
        for k, la in frm_lab.items():                       # only the devices that move
            out[k] = pal.mix_lab(la, to_lab[k], x)
        return out

    def maestro_palette_fn(self, t_s: float) -> Optional[dict[str, tuple[float, float, float]]]:
        """artnet_out's palette_fn (show-artnet, any thread): ``palette_fn`` with the drop's hit laid over the lamps
        as an output without hits takes it, since the virtual Maestro's lamp channels are what room streams to Hue
        (P0-P4): a predicted drop line's hit exactly at the line, an unforeseen drop's as a slow swell (§5.4;
        contract.hit_weight, the look engine's own rule). Screens never carry it."""
        out = self.palette_fn(t_s)
        h = self._hit
        if out is None or h is None:
            return out
        w = contract.hit_weight(t_s, h[0], h[1], bool(h[3]), float(self.cfg["build"]["hit_swell_ms"]) / 1000.0)
        if w <= 0.0:
            return out
        out = dict(out)
        for k, c in h[2].items():
            out[k] = tuple(c) if w >= 1.0 else pal.mix(out.get(k) or (0.0, 0.0, 0.0), c, w)   # OKLab, as looks.py
        return out

    def _set_colours(self, new: dict[str, pal.Rgb], now_s: float, dur: float,
                     start: Optional[dict[str, pal.Rgb]] = None) -> None:
        p = self._pal
        if start is None and p is not None:
            to = p[3]
            if to.keys() == new.keys() and all(max(abs(a - b) for a, b in zip(to[k], new[k])) < 1e-4 for k in new):
                return
            start = self.palette_fn(now_s) or {}
            t1_prev = p[1]
        else:
            t1_prev = now_s
        start = start or {}
        frm = {k: tuple(start.get(k, v)) for k, v in new.items()}
        t1 = max(now_s + max(0.0, dur), t1_prev)
        to = {k: tuple(v) for k, v in new.items()}
        moving = [k for k in to if frm[k] != to[k]]
        self._pal = (now_s, t1, frm, to, {k: pal.to_oklab(frm[k]) for k in moving},
                     {k: pal.to_oklab(to[k]) for k in moving})

    # -- outputs ---------------------------------------------------------------------------------------------------------------
    def _publish(self, type_: str, data: dict) -> None:
        try:
            self.ctx.publish({"type": type_, "data": data})
            self.published += 1
        except Exception:
            pass

    @staticmethod
    def _differs(a: Optional[dict], b: dict, numeric: tuple[str, ...], words: tuple[str, ...]) -> bool:
        if a is None:
            return True
        for k in numeric:
            x, y = a, b
            for part in k.split("."):
                x = x.get(part) if isinstance(x, dict) else None
                y = y.get(part) if isinstance(y, dict) else None
            if (x is None) != (y is None) or (x is not None and abs(float(x) - float(y)) >= 0.02):
                return True
        for k in words:
            if a.get(k) != b.get(k):
                return True
        return False

    def _outputs(self, now_s: float, vs: dict, known: bool, grid: Grid) -> None:
        rep = self.cfg["republish_s"]
        d = self.direction
        if d is not None:
            sec_a = (self._last_direction or {}).get("section") or {}
            # intensity is 1.0 and pulse_depth / peak absent in observe (README S5.18), so the cadence is as before
            changed = self._differs(self._last_direction, d,
                                    ("energy", "pace", "intensity", "pulse_depth", "change_rate", "arousal", "tension",
                                     "density", "groove", "fx.density", "fx.rate"),
                                    ("vibe", "moods", "genre", "transition_style", "palette_family", "beat_lock",
                                     "loose", "peak")) or \
                sec_a.get("id") != d["section"]["id"] or sec_a.get("label") != d["section"]["label"] or \
                (self._last_direction or {}).get("fx", {}).get("moods") != d["fx"]["moods"]
            if changed or now_s - self._dir_pub_s >= rep:
                self._dir_seq += 1
                d["seq"] = self._dir_seq
                self._publish(contract.ROOM_DIRECTION, d)
                self._last_direction = d
                self._dir_pub_s = now_s
        lk = self.look
        if lk is not None:
            a = self._last_look
            changed = self._force_look or self._differs(
                a, lk, ("level", "floor", "saturation", "contrast", "motion.speed", "motion.amount",
                        "accents.density", "spread"), ("look_id", "vibe", "house", "low", "palette",
                                                       "light_scene")) or (
                a is not None and (a.get("accents", {}).get("beat_lock") != lk["accents"]["beat_lock"]
                                   or self._lamps_moved(a, lk)))
            self._force_look = False
            if changed or now_s - self._look_pub_s >= rep:
                self._look_seq += 1
                lk["seq"] = self._look_seq
                self._publish(contract.ROOM_LOOK, lk)
                self._last_look = lk
                self._look_pub_s = now_s
        if not known:
            self._plan_out(None)                          # drive: the lamps' plan, artnet_out keeping its own depth
            return
        # The accent depth (§8.5), and how each reader takes it (README S5.14): ``hue_depth`` is 0 unless the clock's
        # gate is beat (the virtual Maestro's Hue swell, and the look engine's Hue parity); ``beat_depth`` is the same
        # depth before the gate, so DMX stays beat-locked at the clock's middle confidence (§5.4: the engine reads
        # the gate itself); ``beat_lock`` is the vibe's own (artnet_out's clock is gated at beat by itself).
        vibe_lock = bool(vs["beat_lock"])
        depth = 0.0
        if vibe_lock and self._phrase_passed and not self.house and not self.low:
            depth = self.cfg["accent_depth"].get(vs["accents"], 0.0)
            lvl = self.look["level"] if self.look else 1.0
            if lvl > 0:
                depth = min(depth, max(0.0, 1.0 - vs["floor"] / lvl))
        out = (round(depth if grid.gate == "beat" else 0.0, 3), vibe_lock, round(depth, 3))
        if out != self._look_out:
            self._look_out = out
            _call(self.ctx.set_look_out, hue_depth=out[0], beat_lock=out[1], beat_depth=out[2])
        self._plan_out(depth)                             # drive only (README S5.18): the depth before the gate
        ref = vs["loudness_ref_lufs"]
        if ref is not None and ref != self._gate_ref:
            self._gate_ref = ref
            _call(self.ctx.set_gate_ref, float(ref))
        elif ref is None and self._gate_ref is not None:
            if self._gate_ref != GATE_REF_DEFAULT_LUFS:
                _call(self.ctx.set_gate_ref, GATE_REF_DEFAULT_LUFS)
            self._gate_ref = None

    @staticmethod
    def _lamps_moved(a: dict, b: dict) -> bool:
        for key in ("lamps", "screens"):
            la, lb = a.get(key) or {}, b.get(key) or {}
            if la.keys() != lb.keys():
                return True
            for k in lb:
                if max(abs(x - y) for x, y in zip(la[k]["rgb"], lb[k]["rgb"])) >= 0.05:
                    return True
        return False

    # -- reloads and fetches ---------------------------------------------------------------------------------------------------
    def _check_patch(self, force: bool = False) -> None:
        try:
            st = self.patch_path.stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError as e:
            self.patch_error = f"{self.patch_path.name}: {e.strerror or e}"
            return
        if not force and sig == self._patch_sig:
            return
        self._patch_sig = sig
        try:
            import yaml
            with open(self.patch_path, encoding="utf-8") as f:
                lamps, screens = parse_patch(yaml.safe_load(f))
        except Exception as e:
            self.patch_error = f"{self.patch_path.name}: {type(e).__name__}: {e} (the last good layout stays)"
            return
        self.patch_error = None
        self._patch_layout = (lamps, screens)
        self._apply_layout()

    def _check_rig(self) -> None:
        """Stage 5 (README S5.8): the lamps and screens from the fixture registry (ctx.rig, read by reference each
        tick), so a fixture added to fixtures.yaml gets the director's colours; patch.yaml's without one. The ids are
        the same for the six existing devices, so nothing changes for them."""
        view = _call(getattr(self.ctx, "rig", None))
        if view is self._rig_seen:
            return
        self._rig_seen = view
        try:
            self._rig_layout = layout_from_rig(view)
        except Exception:                                # noqa: BLE001  a malformed view: patch.yaml's layout
            log.exception("director: the registry's layout couldn't be read; patch.yaml's stays")
            self._rig_layout = None
        self._apply_layout()

    def _apply_layout(self) -> None:
        if self._rig_layout is not None:
            self.lamps, self.screens = self._rig_layout
            self.lamps_from = "fixtures.yaml"
        else:
            self.lamps, self.screens = self._patch_layout
            self.lamps_from = "patch.yaml"

    def _art_in_play(self) -> bool:
        if not getattr(self, "pal_on", self.known):
            return False
        return (self.palette_pick == contract.PALETTE_ART or self.palette_name == contract.PALETTE_ART
                or contract.PALETTE_ART in getattr(self, "vp", self.vs)["palettes"])

    def _fetches(self, now_s: float) -> None:
        loop, get = self.ctx.loop, self.ctx.room_get
        if loop is None or get is None:
            return
        if not self._cat_busy and now_s >= self._cat_next:
            self._cat_next = now_s + CATALOG_POLL_S
            self._cat_busy = True
            self._spawn(loop, self._fetch_catalog())
        if not self._art_busy and now_s >= self._art_next and self._art_in_play():
            self._art_next = now_s + max(1.0, float(self.cfg["art"]["poll_s"]))
            sids = sorted({d["screen"] for d in self.screens.values() if d.get("screen")}
                          | {d["screen"] for d in self.lamps.values() if d.get("screen")})
            if sids:
                self._art_busy = True
                self._spawn(loop, self._fetch_art(sids))

    def _spawn(self, loop: Any, coro: Any, busy: Optional[str] = None) -> None:
        try:
            task = loop.create_task(coro)
        except Exception:
            coro.close()
            if busy == "screens":
                self._scr["busy"] = False
            else:
                self._cat_busy = self._art_busy = False
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _fetch_catalog(self) -> None:
        try:
            st, body = await self.ctx.room_get(RENDERER_CATALOG_PATH)
            if st != 200 or not body:
                self.palettes.api_failed(f"GET {RENDERER_CATALOG_PATH}: {st or 'unreachable'}")
                return
            loop = asyncio.get_running_loop()
            block = await loop.run_in_executor(None, pal.block_from_catalog, body)
            if block is None:
                self.palettes.api_failed("the renderer's catalog had no palettes block")
                return
            self.palettes.ingest(block)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.palettes.api_failed(f"{type(e).__name__}: {e}")
        finally:
            self._cat_busy = False

    async def _http_get(self, url: str) -> tuple[int, bytes]:
        """An art source outside room (director.art.sources): (status, body); (0, b"") on any failure."""
        try:
            import aiohttp
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as s:
                async with s.get(url) as r:
                    return r.status, (await r.read() if r.status == 200 else b"")
        except asyncio.CancelledError:
            raise
        except Exception:                                # noqa: BLE001
            return 0, b""

    async def _fetch_art(self, sids: list[str]) -> None:
        try:
            loop = asyncio.get_running_loop()
            k = max(1, int(self.cfg["art"]["colours"]))
            errors = []
            for sid in sids:
                got = None
                for tmpl in self.cfg["art"]["sources"]:
                    path = tmpl.format(id=urllib.parse.quote(sid, safe=""))
                    if path.startswith(("http://", "https://")):
                        st, body = await self._http_get(path)
                    else:
                        st, body = await self.ctx.room_get(path)
                    if st == 200 and body:
                        got = await loop.run_in_executor(None, pal.from_image, body, k)
                        if got:
                            break
                if got:
                    self.art[sid] = {"colours": got, "roles": pal.roles_from(got), "at_s": self._mono()}
                else:
                    errors.append(sid)
            self._art_error = f"no still for {', '.join(errors)}" if errors else None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._art_error = f"{type(e).__name__}: {e}"
        finally:
            self._art_busy = False

    # ================================================================================================================
    # nudges and the API (show-brain, through brain.call)
    # ================================================================================================================
    def set_target(self, body: Any, source: str = "api") -> contract.VibeReadback:
        """PUT /api/v1/vibe (and DELETE: ``None``). Degrade, never refuse: the target is kept as asked (an unknown vibe
        plays neutral, a bad override is dropped; the read-back's ``problems`` say so); ``{"vibe": null}`` or None
        is neutral, so the room's put-back of "no feel" lands. ValueError only for a body that isn't a target."""
        tgt = self.vibes.check_target(body)
        if tgt != self.target:
            self._log_event("target", {"from": self.target, "to": tgt, "source": source,
                                       "problems": self.vibes.target_problems(tgt)})
        self.target = tgt
        self.target_source = (source if source in ("mode", "api") else "api") if tgt else "none"
        self.target_set_at = self._wall()
        self.restored = False
        self._changed()                                  # persist, notify, and the snapshot now (the card reads it)
        return self.readback()

    def _effective(self) -> tuple[contract.VibeSettings, bool]:
        """vibes.effective(target), cached by (vibes revision, palette catalog, target): one copy per change, shared
        by the tick, the readback and ctx.vibe (readers never mutate it). The target's problems are cached with it."""
        key = (self.vibes.generation, id(self.target))       # targets are replaced, never mutated
        if key != self._eff_key:
            self._eff_key = key
            self._eff = self.vibes.effective(self.target)
            self._eff_problems = self.vibes.target_problems(self.target)
        return self._eff

    def readback(self) -> contract.VibeReadback:
        t = self.target
        vs, known = self._effective()
        listening = bool(t["listening"]) if t else False    # as the mode asked: kept for readers, no longer a gate
        why = None                                           # (owner, 17 Oct: the AI listening switch, Auto and the
        if not vs["listener"]:                               # mic decide, and a feel may still say listener: off)
            why = "this feel doesn't listen"
        out: contract.VibeReadback = {
            "vibe": t["vibe"] if t else None, "listening": listening,
            "source": "restored" if (t and self.restored) else (self.target_source if t else "none"),
            "set_age_s": round(self._wall() - self.target_set_at, 1) if self.target_set_at is not None else None,
            "known": known, "label": (vs["label"] or (t["vibe"] if t else "")) if t else "",
            "effective": vs, "listening_effective": why is None, "listening_why": why,
            "problems": list(self._eff_problems)}
        if t and t.get("overrides"):
            out["overrides"] = dict(t["overrides"])
        return out

    def catalog_choices(self) -> list[contract.PaletteChoice]:
        return self.palettes.choices() + [self._art_choice()]

    def catalog(self) -> contract.VibesCatalog:
        """GET /api/v1/vibes: the vibes, their field schemas, and the palettes (the catalog, then the art)."""
        return self.vibes.catalog(self.catalog_choices())

    def _art_choice(self) -> contract.PaletteChoice:
        cols: list = []
        for a in self.art.values():
            cols += a.get("colours") or []
        return {"name": contract.PALETTE_ART, "label": "From the art", "family": None,
                "swatch": [pal.hex_of(c) for c in cols[:5]]}

    def _steps_view(self) -> contract.StepsView:
        mx = self._steps_max(self.vs)
        return {"value": self.steps, "min": -mx, "max": mx, "at_limit": abs(self.steps) >= mx}

    def step(self, direction: str) -> dict:
        if direction not in contract.STEP_DIRS:
            raise ValueError(f"bad_request: dir must be one of {', '.join(contract.STEP_DIRS)}")
        mx = self._steps_max(self.vs)
        want = self.steps + (-1 if direction == contract.STEP_DIRS[0] else 1)
        moved = -mx <= want <= mx
        if moved:
            self.steps = want
            self._log_event("nudge", {"nudge": "steps", "dir": direction, "steps": self.steps})
            self._changed()
        view = self._steps_view()
        if not moved:
            view["at_limit"] = True
        return {"steps": view, "chips": self.chips(), "done": moved}

    def set_palette(self, name: str) -> dict:
        if not isinstance(name, str) or not (name in (contract.PALETTE_AUTO, contract.PALETTE_ART)
                                             or self.palettes.has(name)):
            raise ValueError(f"unknown_palette: {name}")
        if name != self.palette_pick:
            self.palette_pick = name
            if name == contract.PALETTE_AUTO:
                self.palette_name = None
            self._log_event("nudge", {"nudge": "palette", "palette": name})
            self._human_change("palette")
            self._changed()
        return {"palette": {"pick": self.palette_pick, "current": self._current_palette_name()},
                "chips": self.chips(), "done": True}

    def _current_palette_name(self) -> Optional[str]:
        return (self.look or {}).get("palette", {}).get("name") if self.look else None

    def action(self, do: str) -> dict:
        if do not in contract.DIRECTOR_ACTIONS:
            raise ValueError(f"unknown_action: {do}")
        now = self._now()
        if do == "next":
            self._human_change("next", advance=self.palette_pick == contract.PALETTE_AUTO)
            if self._vibe_name is None:                   # no feel: no look lands, so the light scene moves on now
                self._pick_light_scene("next", now, human=True)
        elif do == "keep":
            self.keep_until = now + float(self.cfg["keep_s"])
        elif do == "house":
            self.house = not self.house
            if self.house:
                self.low = False
            self._house_toggled = HOUSE_GLIDE_S
        elif do == "low":
            self.low = not self.low
            if self.low:
                self.house = False
            self._house_toggled = float(self.cfg["low"]["fade_s"])
        self._log_event("nudge", {"nudge": do, "house": self.house, "low": self.low,
                                  "keep_in_s": self._keep_in(now)})
        self._changed()
        return {"done": True, "chips": self.chips(),
                "look": {"id": f"L{self.look_n}", "next_pending": self.next_pending}}

    def _human_change(self, reason: str, advance: bool = False) -> None:
        """Next look (or a palette pick): on the next bar, at most next_max_wait_s away; "Changing…" at once."""
        if not self.pal_on:
            return
        now = self._now()
        grid = self._grid(now)
        at = now + LOOK_MIN_LEAD_S
        if grid.ok:
            t_bar, _bar = grid.next_line(now + LOOK_MIN_LEAD_S, 1)
            if t_bar - now <= float(self.cfg["next_max_wait_s"]):
                at = t_bar
        self.next_pending = True
        self._announce(now, reason, "human", grid, at_s=at, advance=advance)

    def _now(self) -> float:
        return self._now_s if self._now_s is not None else self._mono()

    def _keep_in(self, now: Optional[float] = None) -> Optional[float]:
        if self.keep_until is None:
            return None
        return max(0.0, self.keep_until - (now if now is not None else self._now()))

    def clear(self, nudge_id: str, why: Optional[str] = None) -> list[str]:
        """DELETE /api/v1/nudge/{id} ("all" = Back to auto), and every nudge at a mode change. ``why`` is only for
        the log (the screens' hold says what ended it)."""
        active = [c["id"] for c in self.chips()]
        ids = active if nudge_id == "all" else [nudge_id] if nudge_id in active else []
        for i in ids:
            if i == "steps":
                self.steps = 0
            elif i == "palette":
                self.palette_pick = contract.PALETTE_AUTO
                self.palette_name = None
            elif i == "moods":
                self.moods = None
            elif i == "keep":
                self.keep_until = None
            elif i == "house":
                self.house = False
                self._house_toggled = HOUSE_GLIDE_S
            elif i == "low":
                self.low = False
                self._house_toggled = float(self.cfg["low"]["fade_s"])
            elif i == contract.SCREEN_PICK:
                self._end_pick(why or ("back to auto" if nudge_id == "all" else "the chip's ×"))
        if ids:
            self._log_event("nudge", {"nudge": "clear", "ids": ids})
            self._changed()
        return ids

    def apply_describe(self, intent: contract.DescribeIntent) -> list[contract.ChipData]:
        """Describe it: only what the card can set, within the mode (§7.4). Returns the chips it set."""
        if not isinstance(intent, dict):
            return []
        touched: list[str] = []
        st = int(_num(intent.get("steps"), 0) or 0)
        if st:
            mx = self._steps_max(self.vs)
            new = max(-mx, min(mx, self.steps + st))
            if new != self.steps:
                self.steps = new
                touched.append("steps")
        p = intent.get("palette")
        if isinstance(p, str) and (p == contract.PALETTE_ART or self.palettes.has(p)) and p != self.palette_pick:
            self.palette_pick = p
            self._human_change("palette")
            touched.append("palette")
        moods = intent.get("moods")
        if isinstance(moods, list):
            vocab = (self.vibes.vocab or {}).get("moods")
            ok = [m for m in moods if isinstance(m, str) and (vocab is None or m in vocab)][:3]
            if ok:
                self.moods = ok
                touched.append("moods")
        if intent.get("keep"):
            now = self._now()
            self.keep_until = now + float(self.cfg["keep_s"])
            touched.append("keep")
        if touched:
            self._log_event("nudge", {"nudge": "describe", "set": touched, "reply": intent.get("reply")})
            self._changed()
        return [c for c in self.chips() if c["id"] in touched]

    def release_hue(self) -> dict:
        return {"released": True, "via": "room"}

    def on_mode_change(self, old: Optional[str], new: str) -> None:
        """Every nudge clears when the room's mode changes (§8.4); the same mode coming back (a restart) keeps them."""
        if new == self.mode:
            return
        cleared = self.clear("all", why="the mode changed")
        self._log_event("mode", {"from": old, "to": new, "cleared": cleared})
        self.mode = new
        self._dirty()

    def chips(self) -> list[contract.ChipData]:
        out: list[contract.ChipData] = []
        if self.steps:
            word = "Calmer" if self.steps < 0 else "Wilder"
            out.append({"id": "steps", "kind": "steps", "label": f"{word} ×{abs(self.steps)}", "value": self.steps,
                        "expires_in_s": None})
        if self.palette_pick != contract.PALETTE_AUTO:
            lbl = "From the art" if self.palette_pick == contract.PALETTE_ART else self.palettes.label(self.palette_pick)
            out.append({"id": "palette", "kind": "palette", "label": f"Palette: {lbl}", "value": self.palette_pick,
                        "expires_in_s": None})
        if self.moods:
            out.append({"id": "moods", "kind": "moods", "label": "Mood: " + ", ".join(self.moods),
                        "value": list(self.moods), "expires_in_s": None})
        k = self._keep_in()
        if k is not None:
            out.append({"id": "keep", "kind": "keep", "label": f"Keep this look · {max(1, math.ceil(k / 60))} min",
                        "value": True, "expires_in_s": round(k, 1)})
        if self.house:
            out.append({"id": "house", "kind": "house", "label": "House lights", "value": True, "expires_in_s": None})
        if self.low:
            out.append({"id": "low", "kind": "low", "label": "Lights low", "value": True, "expires_in_s": None})
        if self.screen_pick is not None:
            out.append({"id": contract.SCREEN_PICK, "kind": contract.SCREEN_PICK,
                        "label": f"Colours: {self.screen_pick['label']} (picked on a screen)",
                        "value": dict(self.screen_pick), "expires_in_s": None})
        return out

    def log(self, n: int = 50) -> list[dict]:
        n = max(0, int(n))
        return list(self._log)[-n:] if n else []

    # -- housekeeping --------------------------------------------------------------------------------------------------------
    def _changed(self) -> None:
        self._dirty()
        self._notify()
        self._build_snapshot(self._now())

    def _dirty(self) -> None:
        _call(self.ctx.persist)

    def _notify(self) -> None:
        _call(self.ctx.notify, "card", None)

    def _log_event(self, kind: str, detail: dict) -> None:
        m = self.music
        entry = {"t": round(self._wall(), 3), "kind": kind, **detail, "vibe": self._vibe_name, "mode": self.mode,
                 "music": {k: (round(m[k], 3) if isinstance(m.get(k), float) else m.get(k))
                           for k in ("energy", "arousal", "gate", "liveness", "bpm", "clock_gate", "relax")} if m
                 else None}
        self._log.append(entry)
        if self.log_path is None:
            return
        try:
            line = json.dumps(entry, default=str) + "\n"
            self.log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                if self.log_path.stat().st_size > LOG_MAX_BYTES:
                    os.replace(self.log_path, self.log_path.with_suffix(self.log_path.suffix + ".1"))
            except FileNotFoundError:
                pass
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line)
            self._log_error = None
        except Exception as e:                          # a full disk never stops the show
            self._log_error = f"{type(e).__name__}: {e}"

    def _palette_view(self) -> dict:
        key = (id(self.vs), self.known, self._roles_key, self.palette_pick, len(self.palettes.names()),
               tuple((s, a.get("at_s")) for s, a in self.art.items()))
        if key == self._pview_key:
            return self._pview
        self._pview_key = key
        self._pview = self._palette_view_build()
        return self._pview

    def _palette_view_build(self) -> dict:
        vs = self.vs
        names = [p for p in vs["palettes"] if p != contract.PALETTE_ART and self.palettes.has(p)] if self.known else []
        if not names:
            names = self.palettes.names()
        choices: list[contract.PaletteChoice] = [
            {"name": contract.PALETTE_AUTO, "label": "Auto", "family": None,
             "swatch": [r["hex"] for r in ((self.look or {}).get("palette") or {}).get("roles", {}).values()]},
            self._art_choice()]
        choices += [self.palettes.choice(n) for n in names[:5]]
        return {"pick": self.palette_pick, "current": self._current_palette_name(), "choices": choices}

    def _sections_view(self, now_s: float) -> dict:
        n, left = self._tokens_left(self.vs, now_s)
        return {"structure": self._mc is not None, "context": self._ctx_brief(), "phase": self._phase_eff(now_s),
                "track": self._track_id, "memory": {k: dict(v) for k, v in self._sec_mem.items()},
                "express": round(self._express_now(now_s), 3),
                "build": ({"subdivision": SUBDIVISIONS[self._build["idx"]], "start_bar": self._build["start_bar"],
                           "last_step_bar": self._build["step_bar"]} if self._build else None),
                "drop_inside": self._drop_inside_s is not None,
                "armed": ({"in_s": round(self._armed["at_s"] - now_s, 2), "section_id": self._armed["section_id"]}
                          if self._armed else None),
                "tokens": {"per_hour": n, "left": left, "spent_last_hour": len(self._tokens)},
                "stats": {**self.sec_stats, "events": dict(self.sec_stats["events"])},
                "song_end": {"junction": dict(self._junction) if self._junction else None,
                             "dip": ({"progress": round(_c01((now_s - self._song_dip["t0"]) / self._song_dip["out_s"]),
                                                        3), "until_s": round(self._song_dip["until"] - now_s, 1),
                                      "track_id": self._song_dip["track_id"]} if self._song_dip else None),
                             "stats": {**self.ends_stats, "chosen": dict(self.ends_stats["chosen"])}}}

    def _build_snapshot(self, now_s: float) -> None:
        if self._tick_ms and (now_s >= self._p95_next or self._p95 is None):
            self._p95_next = now_s + 1.0
            ticks = sorted(self._tick_ms)
            self._p95 = round(ticks[min(len(ticks) - 1, int(0.95 * len(ticks)))], 3)
        p95 = self._p95
        engaged = bool(self.steps or self.palette_pick != contract.PALETTE_AUTO or self.moods
                       or self.keep_until is not None or self.house or self.low or self.screen_pick is not None)
        art_age = None
        if self.art:
            art_age = round(self._mono() - max(a["at_s"] for a in self.art.values()), 1)
        self._snap = {
            "target": dict(self.target) if self.target else None, "readback": self.readback(),
            "direction": self.direction, "look": self.look, "steps": self._steps_view(),
            "palette": self._palette_view(), "chips": self.chips(), "house": self.house, "low": self.low,
            "next_pending": self.next_pending, "keep_in_s": self._keep_in(now_s), "engaged": engaged,
            "vibes": {"revision": self.vibes.revision, "error": self.vibes.error, "problems": list(self.vibes.problems)},
            "art": {"age_s": art_age, "error": self._art_error,
                    "screens": {s: [pal.hex_of(c) for c in a.get("colours") or []] for s, a in self.art.items()}},
            "catalog": {"source": self.palettes.source, "error": self.palettes.error,
                        "api_error": self.palettes.api_error, "palettes": len(self.palettes.names())},
            "patch": {"error": self.patch_error, "lamps": {d: self._lamp_role(v)[0] for d, v in self.lamps.items()},
                      "screens": sorted(self.screens), "from": self.lamps_from},
            "settings_problems": list(self.settings_problems),
            "music": {k: v for k, v in self.music.items() if k not in ("pv", "beliefs")},
            "band": self._words_now.get("band"),
            "words_from": {"moods": self._words_now.get("moods_from"), "genre": self._words_now.get("genre_from"),
                           "transition_style": self._words_now.get("style_from"),
                           "change_rate": self._words_now.get("change_from"), "band_genre": self._gterm_from},
            "guide": dict(self._guide),
            "relaxed": self._relaxed, "pending": dict(self.pending) if self.pending else None,
            "neutral": not self.known,
            "last_look": ({"why": self._look_why["why"], "age_s": int(max(0.0, now_s - self._look_why["t"]))}
                          if self._look_why else None),
            "sections": self._sections_view(now_s),
            "screens_palette": {**{k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                                   for k, v in self._scr.items() if k != "retry"},
                                "hold": dict(self.screen_pick) if self.screen_pick else None,
                                "look_source": self._look_source, "boot": self._look_boot},
            "stats": {"tick_ms_p95": p95, "looks": self.looks, "events": self.events, "published": self.published,
                      "gobo_steps": self.gobo_steps, "log_error": self._log_error},
            "compose": self._compose_view(),
            "light_scene": {"name": self.light_scene, "label": self.light_scenes.label(self.light_scene),
                            "why": self.light_scene_why,
                            "age_s": int(max(0.0, now_s - self._ls_at)) if self._ls_at is not None else None,
                            "drop": dict(self._ls_drop) if self._ls_drop else None, **self._ls_stats,
                            "catalogue": self.light_scenes.view()}}

    def _compose_view(self) -> dict:
        """The snapshot's composer (README S5.18): the mode, whose limits, the problems (the limits' validator, the
        response file), the tempo fold, the dmx rung's depth and, in drive, what artnet_out was last sent."""
        cm = self.cfg["compose"]
        probs = list(self._cval) + ([self._resp_problem] if self._resp_problem else [])
        return {"mode": cm["mode"], "limits": self._csrc, "problems": probs, "fold": list(self._fold),
                "response_file": cm["response_file"], "middle_depth": self._middle_depth,
                "plan_sent": ({"depth": self._plan_sent["depth"], "middle_depth": self._plan_sent["middle_depth"],
                               "lamps": {k: list(v) for k, v in self._plan_sent["lamps"].items()}}
                              if self._plan_sent else None)}


def attach(ctx: contract.BrainContext, **kw: Any) -> Director:
    """The brain's ``director``. Keyword args go to Director (__main__ passes ``patch_path`` (artnet_out's),
    ``vibes_path`` and ``log_path`` from its options)."""
    return Director(ctx, **kw)
