"""director: the art director (docs/SHOW_ENGINE.md §8.2-§8.8; README "Stage 3 > director.py").

One component, ``director``, ticked at 10 Hz on show-brain. Each tick composites the layers of §8.2, lowest first:

  L0 vibe       the mode's target -> ``vibes.effective`` -> bounds and policies (every behaviour is a field)
  L1 music      the vibe's ``follow`` inputs (energy, onsets, dynamics, register, harmony) -> numbers, and show.yaml
                ``director.bands`` -> words; liveness holds 10 s after the features go stale, then fades over 20 s
  L2 listener   confident beliefs (the listener only changes a word on two agreeing reads): words first; a small
                trajectory bias on energy, slewed at most 0.1 a second
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
There is no section detector yet (P3), so a confirmed section boundary is never seen: the phrase counter and energy
novelty stand in. Each change is announced (``room.event`` look_change, at_ms >= 350 ms ahead where it can be); at
at_ms the new ``room.look``, ``ctx.section_change()`` (GOBO +1, the screens' scene change) and the crossfade. The
director's own changes keep the palette and move the lamps' roles on (a new allocation); a song change (sound again
after >= 8 s of silence) or a tap (Next look, a palette pick) moves through the vibe's ranked palettes.

**Neutral.** No target, or a vibe that isn't in vibes.yaml: ``palette_fn`` returns None (artnet_out keeps its fixed
colour), ``set_look_out`` and ``set_gate_ref`` are left alone (put back to their defaults once, if we had moved them),
and room.direction / room.look are still published with ``vibe: null``.

``palette_fn(t)`` (show-artnet, any thread) returns the dict built on show-brain, swapped by reference; only a
crossfade's OKLab lerp runs in the call.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import math
import os
import pathlib
import time
import urllib.parse
from typing import Any, Callable, Optional

from . import contract
from . import palette as pal
from .vibes import Vibes

log = logging.getLogger("show_service.director")

# -- fixed vocabulary and timing ---------------------------------------------------------------------------------------
SCREEN_KIND = "screen"              # patch.yaml device kind (room_service/dmx/patch.py KINDS): the renderer's fixtures
RGB_FIELDS = ("r", "g", "b")
RENDERER_CATALOG_PATH = "/renderer/api/catalog"      # stream_service's catalog through room's one front door
ART_PATHS = ("/api/v1/screens/{id}/art",                 # the live art feed: the TV's art now, as on the glass
             "/api/v1/screens/{id}/snapshots/anchor.png")  # else the still room took at the last cut
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
LLM_SLEW_PER_S = 0.1                # §8.2: numbers slewed <= 0.1 per update
# Starting points, overridable in show.yaml (director.listener_bias): the energy nudge per listener trajectory word
# (contract.TRAJECTORY_STATES), x its confidence.
LLM_TRAJECTORY_BIAS = {"building": 0.05, "peak": 0.1, "sustaining": 0.0, "steady": 0.0, "releasing": -0.05,
                       "winding_down": -0.1}
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
NEUTRAL_ROLE = {"palette_role": "base", "follow_screen": False, "chroma_max": "chroma_max", "motion": False}

#: show.yaml ``director`` keys the code falls back to when the file leaves one out: the design's own numbers (§8.4,
#: §8.5; starting points, as the module constants above they come from) or the neutral (no swell, no band words). The
#: file's values win.
DIRECTOR_FALLBACK = {
    "steps": {"size_from": "step_size", "max_from": "max_steps"}, "keep_s": 900.0,
    "low": {"level": 0.10, "fade_s": 2.0}, "house": {"white_k": 2700.0, "level": 0.8}, "next_max_wait_s": 4.0,
    "art": {"poll_s": 60.0, "colours": 5}, "republish_s": 5.0, "accent_depth": {}, "bands": None,
    "transitions": TRANSITIONS, "listener_bias": LLM_TRAJECTORY_BIAS,
    "between_pieces": {"pause_s": PAUSE_S, "damper_up": DAMPER_UP}, "new_song_after_s": SILENCE_SONG_S,
    "novelty": NOVELTY, "variation_bars": float(VARIATION_BARS), "entry_fade_s": ENTRY_FADE_S,
    "emphasis_edges": list(EMPHASIS_EDGES)}


def _c01(x: float) -> float:
    return 0.0 if not x > 0.0 else 1.0 if x > 1.0 else x


def _num(v: Any, default: Optional[float] = None) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return float(v)
    return default


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


class Director:
    """Component ``director`` (contract.Component). Everything runs on show-brain except ``palette_fn``,
    ``snapshot`` and the ``provides`` readers, which read references swapped there."""

    name = "director"
    tick_hz = contract.BRAIN_TICK_HZ

    def __init__(self, ctx: contract.BrainContext, *, vibes: Optional[Vibes] = None,
                 catalog: Optional[pal.Catalog] = None, patch_path: pathlib.Path = contract.PATCH_FILE,
                 log_path: Optional[pathlib.Path] = contract.DIRECTOR_LOG,
                 wall: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic,
                 vibes_path: Optional[pathlib.Path] = None) -> None:
        self.ctx = ctx
        self._mono = mono
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
        # patch
        self.lamps: dict[str, dict] = {}
        self.screens: dict[str, dict] = {}
        self.patch_error: Optional[str] = None
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
        self._build_snapshot(mono())

    # ================================================================================================================
    # Component
    # ================================================================================================================
    def provides(self) -> dict:
        return {"vibe": self._vibe_now, "target": self._target_now, "engaged": self._engaged_now,
                "on_mode_change": self.on_mode_change}

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
                    "entry_fade_s"):
            if key in d:
                v = _num(d[key])
                if v is not None and v > 0:
                    cfg[key] = v
                else:
                    probs.append(f"director.{key}: {d[key]!r} is not a positive number")
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
        self.cfg = cfg
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
                          if isinstance(w.get("transition_style"), str) else None})
        return {"edges": [float(e) for e in edges], "hysteresis": _num(b.get("hysteresis"), 0.0) or 0.0,
                "split": split, "split_band": split_band, "table": table}

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

    def persist(self) -> Optional[dict]:
        k = self._keep_in()
        ku = round(self._wall() + k, 3) if k is not None else None
        return {"target": dict(self.target) if self.target else None,
                "target_source": self.target_source if self.target_source in ("mode", "api") else "mode",
                "target_set_at": self.target_set_at, "mode": self.mode,
                "nudges": {"steps": self.steps, "palette": self.palette_pick, "moods": list(self.moods)
                           if self.moods else None, "keep_until": ku, "house": self.house, "low": self.low}}

    def start(self) -> None:
        self.palettes.poll()
        self.vibes.poll(force=True)
        self._check_patch(force=True)

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
        self._fetches(now_s)

        vs, known = self._effective()
        self.vs, self.known = vs, known
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
        self._music(now_s, dt, vs, grid)
        cutting = self._cutting(room)
        self._changes(now_s, vs, grid, cutting)
        self._compose(now_s, vs, known, grid, room)
        self._outputs(now_s, vs, known, grid)
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
        if fresh:
            self._stale_since = None
            if self._cold:
                self.lv, self._cold = 1.0, False
            else:
                self.lv = min(1.0, self.lv + dt / RECOVER_S)
            e = _c01(_num(f.get("energy"), 0.0))
            self._e = _ema(self._e, e, dt, TAU_E_S)
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
            if self._silent_since is not None and now_s - self._silent_since >= float(self.cfg["new_song_after_s"]):
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
        # L2: the listener's trajectory, slewed
        b = _call(self.ctx.beliefs)
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
        u = (e_out - lo) / (hi - lo) if hi > lo else 0.5
        tempo = _c01((grid.bpm - 60.0) / 90.0) if grid.has_grid and grid.gate in ("beat", "dmx") else None
        pace_in = 0.7 * e_out + 0.3 * tempo if tempo is not None else e_out
        onsets_now = onsets_in if onsets_in is not None and live > 0 else e_out
        arousal = _c01(0.6 * e_out + 0.4 * onsets_now)
        self.music = {"fresh": fresh, "gate": round(gate, 3), "energy_in": round(energy_in, 3), "music": round(music, 3),
                      "conf": round(conf, 3), "inputs": {k: round(v, 3) for k, (v, _) in inputs.items()},
                      "liveness": round(self.lv, 3), "relax": round(self._relax, 3), "llm_bias": round(self._llm_bias, 3),
                      "energy": e_out, "u": u, "pace": 0.1 + 0.8 * pace_in, "arousal": arousal,
                      "onsets": onsets_now, "drive": drive, "tension": self._tension or 0.0,
                      "novelty": abs((self._nov_fast or 0.0) - (self._nov_slow or 0.0)) if fresh else 0.0,
                      "playing": playing, "no_audio": no_audio, "pv": p if isinstance(p, dict) else None, "beliefs": b
                      if isinstance(b, dict) else None, "bpm": grid.bpm, "clock_gate": grid.gate}

    # -- the vibe entered / left ----------------------------------------------------------------------------------------
    def _enter(self, name: Optional[str], now_s: float) -> None:
        old = self._vibe_name
        self._vibe_name = name
        if name is None:
            self._pal = None                              # neutral: artnet_out keeps its fixed colour
            self.pending = None
            self.next_pending = False
            if self._look_out is not None:
                _call(self.ctx.set_look_out, reset=True)
                self._look_out = None
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
        self._log_event("vibe", {"from": old, "to": name})

    # -- when looks change (§8.5) ----------------------------------------------------------------------------------------
    def _min_bars(self, vs: dict) -> float:
        return 32.0 / (4.0 ** _c01(vs["change_rate"]))

    def _changes(self, now_s: float, vs: dict, grid: Grid, cutting: bool) -> None:
        if self._vibe_name is None:
            return
        if not self._phrase_passed and self.entered_s is not None and grid.phrase_s and grid.gate == "beat" \
                and now_s - self.entered_s >= grid.phrase_s:
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
                  palette: Optional[str] = None) -> None:
        self.pending = {"at_s": at_s, "reason": reason, "by": by, "advance": advance, "transition": transition,
                        "palette": palette, "bar": bar}
        ev: contract.EventData = {"kind": "look_change", "at_ms": round(at_s * 1000.0, 1),
                                  "t_ms": round(now_s * 1000.0, 1), "seq": self._next_ev(),
                                  "look_id": f"L{self.look_n + 1}", "detail": reason}
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
        if pd.get("palette"):
            self.palette_name = pd["palette"]
        elif pd["advance"] or self._song_change:
            self._advance_palette(vs)
        self._song_change = False
        if not pd.get("palette") and not pd["advance"]:
            self.variation += 1                          # the director's own change: a new allocation of roles
        tr = pd.get("transition") or self._transition(vs)
        self._look_transition = tr
        _call(self.ctx.section_change)                   # GOBO +1: the screens' scene change
        self._fade_s = self._transition_s(tr, grid)
        self._force_look = True
        self._log_event("look_change", {"look_id": f"L{self.look_n}", "reason": pd["reason"], "by": pd["by"],
                                        "palette": self.palette_name, "transition": tr})

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

    # -- palettes -------------------------------------------------------------------------------------------------------
    def _art_ready(self) -> bool:
        return any(a.get("colours") for a in self.art.values())

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
            if self._art_ready():
                cols: list = []
                for sid, a in self.art.items():
                    if a.get("colours"):
                        screen_roles[sid] = a["roles"]
                        cols += a["colours"]
                roles = pal.roles_from(cols)
                return {"name": contract.PALETTE_ART, "source": "art" if source == "vibe" else source,
                        "roles": roles, "emphasis": "base"}, screen_roles
            # no still yet: the vibe's next catalog palette stands in until one arrives
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
        return b["table"][idx]

    def _latest_features(self) -> Optional[dict]:
        f = _call(self.ctx.features)
        return f if isinstance(f, dict) else None

    def _words(self, vs: dict) -> dict:
        b = self.music.get("beliefs")
        llm_moods: list[str] = []
        llm_genre = None
        if isinstance(b, dict) and b.get("state") in ("fresh", "holding"):
            llm_moods = [m["word"] for m in b.get("moods") or []
                         if isinstance(m, dict) and isinstance(m.get("word"), str)
                         and (_num(m.get("conf"), 0.0) or 0.0) >= LLM_CONF]
            g = b.get("genre") if isinstance(b.get("genre"), dict) else {}
            if isinstance(g.get("word"), str) and g["word"] != "unknown" and (_num(g.get("conf"), 0) or 0) >= LLM_CONF:
                llm_genre = g["word"]
        band = self._band_words(self.music.get("energy", vs["energy_centre"])) if self.music else None
        if self.moods:
            moods, src = list(self.moods), "human"
        elif llm_moods:
            moods, src = llm_moods, "listener"
        elif band and band["moods"]:
            moods, src = list(band["moods"]), "bands"
        else:
            moods, src = list(vs["moods"]), "vibe"
        style = (band or {}).get("transition_style") or vs["transition_style"]
        return {"moods": moods, "moods_from": src, "genre": llm_genre or vs["genre"],
                "transition_style": style, "fx_moods": list(band["fx_moods"]) if band and band["fx_moods"] else None,
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
        rn = lc["placement_roles"].get(dev.get("placement") or "", None) if dev.get("placement") else None
        rn = rn or lc["default_role"]
        if rn is None or rn not in lc["roles"]:
            return "neutral", NEUTRAL_ROLE
        return rn, lc["roles"][rn]

    def _house_rgb(self, did: str, room: Any) -> pal.Rgb:
        h = self.cfg["house"]
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

    def _compose(self, now_s: float, vs: dict, known: bool, grid: Grid, room: Any) -> None:
        m = self.music
        e, u, arousal = m["energy"], m["u"], m["arousal"]
        words = self._words(vs)
        name, source = self._resolve_palette(vs) if known else (None, "neutral")
        look_pal, screen_roles = self._roles_for(name, source, vs)
        emphasis = self._emphasis_of(arousal)
        look_pal["emphasis"] = emphasis
        sat = _lerp(vs["saturation"], 0.5 * u + 0.5 * arousal)
        contrast = _lerp(vs["contrast"], u)
        level = _lerp(vs["level"], u)
        floor = vs["floor"]
        speed = _lerp(vs["motion"], arousal)
        amount = _lerp(vs["motion"], u)
        beat_lock = bool(vs["beat_lock"]) and grid.gate == "beat"
        roles_key = (look_pal["name"], emphasis, tuple(r["hex"] for r in look_pal["roles"].values()),
                     tuple((sid, tuple(r["hex"] for r in sr.values())) for sid, sr in sorted(screen_roles.items())))
        roles_changed = self._roles_key is not None and roles_key != self._roles_key
        self._roles_key = roles_key
        self._screen_roles = screen_roles
        lamps: dict[str, contract.LampOut] = {}
        screens: dict[str, contract.ScreenOut] = {}
        colours: dict[str, pal.Rgb] = {}
        if known:
            lamp_level = max(floor, level)
            for i, (did, dev) in enumerate(sorted(self.lamps.items())):
                rname, rc = self._lamp_role(dev)
                if self.house:
                    rgb, lv_out = self._house_rgb(did, room), None
                else:
                    c = self._lamp_colour(dev, rc, look_pal, screen_roles, vs, sat, speed, amount, i, now_s)
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
        accents = {"style": vs["accents"], "density": round(_c01(u), 3), "subdivision": 1.0,
                   "strobe": bool(vs["strobe"]), "beat_lock": beat_lock}
        tr = self._look_transition or self._transition(vs)
        self.look = {"t_ms": round(now_s * 1000.0, 1), "seq": self._look_seq, "look_id": f"L{self.look_n}",
                     "vibe": self._vibe_name, "mode": self.mode, "palette": look_pal, "level": round(level, 3),
                     "floor": round(floor, 3), "house": self.house, "low": self.low, "key_cct": vs["key_cct"],
                     "chroma_max": vs["chroma_max"], "lamp_chroma_max": vs["lamp_chroma_max"],
                     "saturation": round(sat, 3), "contrast": round(contrast, 3),
                     "motion": {"speed": round(speed, 3), "amount": round(amount, 3)}, "accents": accents,
                     "spread": round(_c01(amount), 3), "change_rate": vs["change_rate"], "transition": tr,
                     "lamps": lamps, "screens": screens}
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
        fam = vs["palette_family"] or (self.palettes.family(look_pal["name"]) if look_pal["name"] else None)
        self.direction = {"t_ms": round(now_s * 1000.0, 1), "seq": self._dir_seq, "source": "show_service",
                          "note": f"show_service · {self._vibe_name}" if self._vibe_name else None,
                          "vibe": self._vibe_name, "energy": round(e, 3), "pace": round(_c01(m["pace"]), 3),
                          "intensity": 1.0, "change_rate": round(vs["change_rate"], 3), "moods": words["moods"],
                          "genre": words["genre"], "transition_style": words["transition_style"],
                          "palette_family": fam,
                          "fx": {"density": round(_c01(0.1 + 0.8 * e), 3), "rate": round(_c01(0.05 + 0.85 * e), 3),
                                 "stages": None, "moods": words["fx_moods"]},
                          "arousal": round(arousal, 3), "tension": round(_c01(m["tension"]), 3),
                          "section": {"id": self.look_n, "label": sec_label, "phase": round(phase, 3),
                                      "bars_in_section": int(bars)},
                          "density": round(_c01(m["onsets"]), 3),
                          "groove": round(_c01(m["drive"] * (grid.conf if grid.gate != "none" else 0.0)), 3),
                          "beat_lock": beat_lock, "loose": bool(vs["beat_lock"]) and m["fresh"] and grid.gate != "beat"}
        self._words_now = words
        if not known:
            return
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
                     speed: float, amount: float, idx: int, now_s: float) -> pal.Rgb:
        """One lamp at full value from its role's fields (no role is known by name): the palette role (moved on by the
        look's variation when the role moves), a slow drift toward the next role, x saturation, white first where
        the role has a white, the chroma cap, the hues to avoid."""
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
        key = (roles[pr]["hex"], roles[nb]["hex"] if depth else None, round(depth / QUANT),
               round(sat / QUANT), wk, cm, tuple(tuple(x) for x in ah) if ah else None)
        hit = self._colour_cache.get(key)
        if hit is not None:
            self._colour_cache.move_to_end(key)
            return hit
        q_sat, q_depth = key[3] * QUANT, key[2] * QUANT
        c = pal.full(pal.rgb_of(roles[pr]["hex"]))
        if q_depth > 0.0:
            c = pal.full(pal.mix(c, pal.full(pal.rgb_of(roles[nb]["hex"])), q_depth))
        c = pal.scale_saturation(c, q_sat)
        if wk is not None:
            c = pal.mix(pal.white(wk), c, q_sat)                     # white first, tinted toward the colour
        if cm is not None:
            c = pal.limit_chroma(c, cm)
        if ah:
            c = pal.avoid_hues(c, ah)
        c = pal.full(c)
        self._colour_cache[key] = c
        if len(self._colour_cache) > COLOUR_CACHE:
            self._colour_cache.popitem(last=False)
        return c

    # -- palette_fn (show-artnet, any thread) --------------------------------------------------------------------------------
    def palette_fn(self, t_s: float) -> Optional[dict[str, tuple[float, float, float]]]:
        p = self._pal
        if p is None:
            return None
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
            changed = self._differs(self._last_direction, d,
                                    ("energy", "pace", "change_rate", "arousal", "tension", "density", "groove",
                                     "fx.density", "fx.rate"),
                                    ("vibe", "moods", "genre", "transition_style", "palette_family", "beat_lock",
                                     "loose")) or \
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
                        "accents.density", "spread"), ("look_id", "vibe", "house", "low", "palette")) or (
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
            return
        beat_lock = bool(vs["beat_lock"]) and grid.gate == "beat"
        depth = 0.0
        if beat_lock and self._phrase_passed and not self.house and not self.low:
            depth = self.cfg["accent_depth"].get(vs["accents"], 0.0)
            lvl = self.look["level"] if self.look else 1.0
            if lvl > 0:
                depth = min(depth, max(0.0, 1.0 - vs["floor"] / lvl))
        out = (round(depth, 3), beat_lock)
        if out != self._look_out:
            self._look_out = out
            _call(self.ctx.set_look_out, hue_depth=out[0], beat_lock=out[1])
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
        self.lamps, self.screens = lamps, screens

    def _art_in_play(self) -> bool:
        if not self.known:
            return False
        return (self.palette_pick == contract.PALETTE_ART or self.palette_name == contract.PALETTE_ART
                or contract.PALETTE_ART in self.vs["palettes"])

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

    def _spawn(self, loop: Any, coro: Any) -> None:
        try:
            task = loop.create_task(coro)
        except Exception:
            coro.close()
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

    async def _fetch_art(self, sids: list[str]) -> None:
        try:
            loop = asyncio.get_running_loop()
            k = max(1, int(self.cfg["art"]["colours"]))
            errors = []
            for sid in sids:
                got = None
                for tmpl in ART_PATHS:
                    path = tmpl.format(id=urllib.parse.quote(sid, safe=""))
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
        listening = bool(t["listening"]) if t else False
        why = None
        if not t:
            why = "no feel is set"
        elif not listening:
            why = "this mode doesn't listen"
        elif not vs["listener"]:
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
        if self._vibe_name is None:
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

    def clear(self, nudge_id: str) -> list[str]:
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
        cleared = self.clear("all")
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

    def _build_snapshot(self, now_s: float) -> None:
        if self._tick_ms and (now_s >= self._p95_next or self._p95 is None):
            self._p95_next = now_s + 1.0
            ticks = sorted(self._tick_ms)
            self._p95 = round(ticks[min(len(ticks) - 1, int(0.95 * len(ticks)))], 3)
        p95 = self._p95
        engaged = bool(self.steps or self.palette_pick != contract.PALETTE_AUTO or self.moods
                       or self.keep_until is not None or self.house or self.low)
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
                      "screens": sorted(self.screens)},
            "settings_problems": list(self.settings_problems),
            "music": {k: v for k, v in self.music.items() if k not in ("pv", "beliefs")},
            "band": self._words_now.get("band"),
            "relaxed": self._relaxed, "pending": dict(self.pending) if self.pending else None,
            "neutral": not self.known,
            "stats": {"tick_ms_p95": p95, "looks": self.looks, "events": self.events, "published": self.published,
                      "log_error": self._log_error}}


def attach(ctx: contract.BrainContext, **kw: Any) -> Director:
    """The brain's ``director``. Keyword args go to Director (__main__ passes ``patch_path`` (artnet_out's),
    ``vibes_path`` and ``log_path`` from its options)."""
    return Director(ctx, **kw)
