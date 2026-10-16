"""structure: sections, phases and music.context (docs/SHOW_ENGINE.md §6.1, §6.2, §11.1; README "Stage 4", S4.7).

One component, ``structure``, ticked at 10 Hz on show-brain (after ml, before the listener and the director).

**Every second** (the tick that crosses a whole second):
  descriptor   with the worker running (MLView.state "running" and its tags task able to run): every unseen
               embedding in ``ml.emb_recent`` that is fresh (``contract.ml_stale_s``: ml.results_stale_s, stretched
               with the worker's own intervals) (source "ml"), stamped at its window's centre (t -
               ml.tasks.tags.window_s / 2). Without it (source "dsp"): the latest music.rhythm frame, while it sounds
               (gate >= structure.track.gap_gate): [chroma12_avg L2-normalised x w_chroma, mfcc13 z-scored over the last
               60 s then L2-normalised x w_mfcc], stamped t - 0.5 s (both are 1 s means). A vector of another source or
               stream clears the novelty history (the spaces differ). Silence adds none.
  novelty      §6.1 ``novelty()``: 1 - cos(mean of the last recent_s vectors, mean of the before_s before them),
               counted in vectors (one a second). A candidate is a local maximum over the past peak_s that has fallen
               for one second, over novelty.min[source] and over median + k_mad x MAD of the background (the
               history_s before its rise, less each earlier real boundary's response: tau to tau + recent_s +
               before_s, the old section still in the before-window), and whose recent window also differs from the
               recent_s vectors just before it by that threshold (local contrast: the plateau a short blip leaves in
               the 15 s before-window, with the recent window steady, makes no boundary).
  boundary     tau = the vector in [peak - 6 s, peak - 2 s] most unlike the mean of the 4 before it; snapped to the
               nearest bar line (gate beat|dmx, bar_conf >= grid_conf), else the nearest beat (gate not none), else as
               is. Within same_bars of a predicted line, a drop or a track change that started the section: the same
               boundary (no event). Else a track change (below), else dropped when within min_section_bars
               (min_section_s without a grid) of the section's start, else a new section and a section_change.
  identity     identify_after_s into a section: its centroid (first and last 2 s left out) against this track's
               earlier sections of the same source: cos >= repeat_sim[source] is a repeat (its letter, repeat_of), else
               the next letter (A..Z, then Z). chorus = the letter, seen at least twice, with the largest count x mean
               energy. A repeat whose model section had a bar length gets ``expected_bars``.
  predicted    (10 Hz) a known repeat's end line (its start + expected_bars bars) is announced predict_lead_bars ahead
               (and >= events.min_lead_s): section_change predicted, with the letter and id of the section that
               followed its model; the section starts on the line. A section started any other way first cancels a
               pending line (evidence wins). A predicted line no boundary confirms within same_bars is ``missed``.
  track        (a) the room's Sonos member (show.yaml situations.sonos.now_playing) changes its track_id (else title +
               artist) while situations' sonos_playing doesn't read false: at once, via sonos. (b) a silence (gate <
               gap_gate, or lufs_m <= gap_lufs_m: the gate's 3 s loudness alone notices silence only ~3 s in) of
               gap_s, then a boundary within window_s of the return, with one jump: the clock's bpm off by
               > tempo_jump (not x2 / x0.5); another key at strength >= track.key_min_conf both sides that isn't the
               same, parallel or relative key (music.context's key, itself held by key_hold); or another leading
               Discogs parent of the last parent_window_s of tags, leading by parent_margin (a near tie is no
               evidence). A boundary with no jump yet keeps checking until return + window_s. (c) no gap: a peak >=
               spike_k x the threshold (spike_k null: off) with spike_jumps independent jumps, the genre by
               genre_reading's smoothed leader: a section change inside a song moves the key or one window's parent,
               not two things at once. A change clears the section memory (letters restart at A), track.id + 1,
               track.changed in the next context only, and a new section (or the post-gap one is adopted).
  context      ``genre_reading()`` (S4.8; speech = the max over situations.talking.speech_words), the tag numbers and
               the instruments (EMA tags_tau_s, frozen while CED music < genre_map.freeze_below_music; None once tags
               are tags_hold_s old), the key (the piano's midi harmony, else the worker's at key_min_strength, else
               music.harmony's; a sticky source, and another key shows only after key_hold.hold_s), ced (ced_mean_s
               mean), talking, then publish music.context. Another ML stream clears the genre, the numbers and the key.

**Every tick (10 Hz): the phases** (§6.2) from music.features + music.rhythm samples. Builds and drops need the clock
gate beat|dmx (no grid, no build: §6.2's jazz and classical, by evidence). Ported from FastLED's audio detectors
(MIT; fl/audio/detector buildup + drop) to numpy, with README S4.7's slopes as the start rule (FastLED's own numbers
are show.yaml structure.fastled):
  build      least-squares slopes over build.window_s: lufs_m, brightness, onset_rate all above min_slope, low_db's at
             most low_max_slope_db_s. It holds (FastLED BuildupDetector's end rule) while lufs_m's slope stays above
             fastled.build_end x its start threshold and low_db stays held. Its start = the ramp's foot (the last
             sample within build.foot_db of the window's quietest lufs_m).
             build_progress = elapsed / (predicted line - start), else the lufs rise / full_rise_db.
  line       the next 16-bar line when phrase_conf >= phrase16_conf and it is <= 16 bars from the build's start, else
             the next 8-bar line; drop_eta_beats; drop_armed once the line is <= arm_beats and >= 2 beats and >=
             events.min_lead_s ahead (again, replacing it, when the line moves); GOBO scheduled at the line. The line
             is called off (drop_armed with eta_beats None): "disarmed" when the build ends more than
             drop.disarm_bars before it (or a new build or track starts; its GOBO is cancelled), "missed" when it
             passes with no drop.
  drop       within drop.within_beats after a build (or of the armed line), low_db >= low_jump_db and lufs_m >=
             lufs_jump_db above the 2 s before (the build's last 2 s). Heard at the first sample half-way up, snapped to
             a beat within drop.snap_beats. strength = FastLED DropDetector's impact blend (fastled.drop_weights:
             loudness flux, bass flux, band change by fastled.band_weights, level; a flux of twice the threshold = 1).
             A new section at once (no section_change: the drop event carries section_id; the director's token rules
             decide). fastled.drop_cooldown_s between drops.
  breakdown  low_db >= low_drop_db under its 30 s median (frozen on entry) and percussive_ratio < perc_ratio_max,
             gate >= min_gate, for for_s. Left once the rule has failed for leave_s (no rhythm frame counts as failing).
  peak       energy (EMA 4 s) >= peak.energy_min, in a section a drop started or the chorus: in after for_s, out after
             leave_s of failing.
  outro      lufs_s slope over outro.window_s <= max_slope_db_s with energy < energy_max: in after for_s, out after
             leave_s.
  phase      drop (hold_bars) > build > breakdown > peak > outro > calm. The word holds at least phase_dwell_s, except
             that drop, build and breakdown (each announced by an event) show at once.

**Events** (room.event, MusicEventData), each also handed to every ``ctx.on_music_event`` (copied, guarded):
section_change (confirmed: at the next bar >= now + events.min_lead_s, else the next beat, else now + the lead;
predicted: the line; a track change's new section: detail "track"), build, drop_armed (eta_beats None: called off,
``detail`` disarmed | missed), drop, breakdown, track_change. ``detail`` on section_change says why: "section" |
"repeat" (predicted) | "track".

**GOBO** (ctx.section_change, the screens' scene change, §9.5) **only while the director is neutral** (``ctx.vibe()``
None: §9.5's P0 path). While a vibe plays the director owns it and steps it when it lands a look, so the vibe's
change_looks, Keep this look, relaxing, the late rule and the peak tokens hold the screens too (counted as
``director`` here). Neutral: once per section change at its at_ms, at an armed drop line, and for an unarmed drop at
the first bar line events.min_lead_s ahead, through ``ctx.loop.call_at`` (time.monotonic); never while a screen cuts
(skipped_cut), never twice within a bar (2 s without a grid), off with structure.gobo_on_sections: false.

Every threshold is show.yaml ``structure`` / ``genre_map`` (the fallbacks below are the same starting points). The
code names no mode, vibe, device, stream or genre: Discogs parent -> word is genre_map.parents only.
"""

from __future__ import annotations

import collections
import copy
import logging
import math
import time
from typing import Any, Callable, Optional

import numpy as np

from . import contract

log = logging.getLogger("show_service.structure")

# -- fixed: the spec's own numbers and DSP internals (README S4.7) ------------------------------------------------------
FEATURES_STALE_S = 2.0          # music.features comes at 25 Hz
RHYTHM_STALE_S = 1.0            # S4.6: a consumer treats a music.rhythm frame over 1 s old as stale
CLOCK_STALE_S = 3.0             # music.clock comes at 10 Hz
HIST_N = 700                    # 10 Hz samples kept (70 s: breakdown's 30 s median, outro's and build's windows)
VEC_KEEP = 360                  # 1 Hz vectors kept for centroids (6 min; 1280 float32 each)
MFCC_Z_S = 60                   # S4.7: mfcc13 z-scored over the last 60 s
DSP_STAMP_S = 0.5               # chroma12_avg and mfcc13 are 1 s means: their centre
EDGE_S = 2.0                    # S4.7 identity: a centroid leaves out a section's first and last 2 s
TAU_FROM_S, TAU_TO_S, TAU_PAST = 6.0, 2.0, 4   # S4.7 step 3
ENERGY_TAU_S = 4.0              # S4.7 peak: energy EMA 4 s
BREAKDOWN_REF_S = 30.0          # S4.7 breakdown: under its 30 s median
DROP_BASE_S = 2.0               # S4.7 drop: above the build's last 2 s mean
DROP_NOW_S = 0.2                # ...measured on the last 2 samples
DROP_DETECT_S = 0.5             # lufs_m's 400 ms window + a tick: how long after its beat a drop can still be read
ARM_MIN_BEATS = 2.0             # S4.7: drop_armed at least 2 beats ahead
LINE_MOVED_BEATS = 0.25         # an armed line that moves more than this is re-armed
NO_GRID_BAR_S = 2.0             # GOBO's "never twice within one bar" without a grid: a 4/4 bar at 120 bpm
MIN_BACKGROUND = 5              # novelty values needed before the median + k x MAD threshold applies
PREDICT_SLACK_S = 2.0           # a predicted line counts as missed this long after the latest a boundary could confirm
NOVELTY_KEEP_S = 120.0          # snapshot: the novelty curve's last 120 s
STAT_N = 600
INSTR_KEEP = 1e-3               # an instrument whose smoothed score falls under this is forgotten
INSTR_TOP = 5                   # music.context instruments: the top 5 smoothed (MlTags.instruments_top5's length)
EVENT_PHASES = ("drop", "build", "breakdown")   # phase words an event announces: they show at once (no dwell)

#: show.yaml ``structure`` (README S4.9): the starting points, the same numbers as the file. The file's values win.
#: ``fastled`` holds FastLED's own audio-detector numbers (MIT; fl/audio/detector buildup.cpp.hpp, drop.cpp.hpp).
STRUCTURE_FALLBACK: dict = {
    "gobo_on_sections": True,
    "novelty": {"recent_s": 4, "before_s": 15, "k_mad": 3.0, "history_s": 120, "min": {"ml": 0.05, "dsp": 0.10},
                "peak_s": 3},
    "dsp_weights": {"chroma": 0.5, "mfcc": 0.5},
    "min_section_bars": 8, "min_section_s": 12, "identify_after_s": 8,
    "repeat_sim": {"ml": 0.85, "dsp": 0.80}, "max_sections": 64, "predict_lead_bars": 1,
    "grid_conf": 0.5, "same_bars": 2, "phase_dwell_s": 2,
    "tags_tau_s": 20, "tags_hold_s": 30, "ced_mean_s": 3, "listener_conf": 0.6,
    "key_min_strength": 0.3, "key_hold": {"strength_margin": 0.05, "hold_s": 4},
    "events": {"min_lead_s": 1.1},
    "track": {"gap_s": 3, "gap_gate": 0.05, "gap_lufs_m": -60, "window_s": 20, "tempo_jump": 0.04,
              "key_min_conf": 0.3, "parent_window_s": 5, "parent_margin": 0.05, "spike_k": 2.0, "spike_jumps": 2},
    "build": {"window_s": 12, "min_slope": {"lufs_db_s": 0.15, "brightness_s": 0.004, "onset_rate_s": 0.05},
              "low_max_slope_db_s": 0.05, "full_rise_db": 6, "phrase16_conf": 0.5, "arm_beats": 8, "foot_db": 0.5},
    "drop": {"low_jump_db": 6, "lufs_jump_db": 3, "within_beats": 1, "hold_bars": 8, "snap_beats": 0.25,
             "disarm_bars": 1},
    "breakdown": {"low_drop_db": 9, "perc_ratio_max": 0.25, "min_gate": 0.3, "for_s": 4, "leave_s": 4},
    "peak": {"energy_min": 0.75, "for_s": 4, "leave_s": 4},
    "outro": {"window_s": 16, "max_slope_db_s": -0.25, "energy_max": 0.4, "for_s": 2, "leave_s": 2},
    "fastled": {"build_end": 0.5, "drop_weights": [0.40, 0.35, 0.15, 0.10], "band_weights": [0.5, 0.3, 0.2],
                "drop_cooldown_s": 2},
}
#: show.yaml ``genre_map``: starting points. ``parents`` is config only (no genre name in code): empty = no words.
GENRE_FALLBACK: dict = {"parents": {}, "tau_s": 30, "freeze_below_music": 0.5, "speech_scale_above": 0.3,
                        "switch_margin": 0.1, "switch_dwell_s": 20}
#: what structure reads from show.yaml ``ml``: the same starting points.
ML_FALLBACK: dict = {"results_stale_s": 3.0, "tags_window_s": 2.1, "key_every_s": 2.0}
#: paths whose value may be negative (slopes); every other number must be >= 0, and these must be > 0:
_SIGNED = {"structure.build.low_max_slope_db_s", "structure.outro.max_slope_db_s", "structure.track.gap_lufs_m",
           "structure.build.min_slope.lufs_db_s", "structure.build.min_slope.brightness_s",
           "structure.build.min_slope.onset_rate_s"}
_POSITIVE = {"structure.novelty.recent_s", "structure.novelty.before_s", "structure.novelty.history_s",
             "structure.novelty.peak_s", "structure.identify_after_s", "structure.max_sections",
             "structure.tags_tau_s", "structure.track.window_s", "structure.build.window_s",
             "structure.build.full_rise_db", "structure.build.arm_beats", "structure.drop.hold_bars",
             "structure.outro.window_s", "structure.min_section_bars", "structure.min_section_s",
             "structure.same_bars", "structure.tags_hold_s", "structure.ced_mean_s", "structure.track.parent_window_s",
             "structure.track.spike_jumps", "structure.fastled.build_end", "genre_map.tau_s"}
#: paths that may be null (unset): track.spike_k null turns the no-gap track change off
_NULLABLE = {"structure.track.spike_k"}

# 10 Hz sample columns
T, LUFS_M, LUFS_S, BRIGHT, GATE, ENERGY, LOW, PERC, ONSET, HARM, B_LO, B_MID, B_HI = range(13)
NCOL = 13
TAG_FIELDS = (("dance", "dance"), ("arousal", "arousal"), ("engagement", "engagement"), ("bright", "bright"),
              ("acoustic", "acoustic"), ("vocal", "voice_p"))      # music.context field <- MlTags field


# -- small helpers --------------------------------------------------------------------------------------------------------
def _f(v: Any, default: Optional[float] = None) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return float(v)
    return default


def _c01(x: float) -> float:
    return 0.0 if not x > 0.0 else 1.0 if x > 1.0 else float(x)


def _call(fn: Optional[Callable], *a: Any) -> Any:
    """An input slot: None when missing or when it raises (degrade, never fail a tick)."""
    if fn is None:
        return None
    try:
        return fn(*a)
    except Exception:
        return None


def _p95(values: Any) -> Optional[float]:
    v = sorted(values)
    return round(v[min(len(v) - 1, int(0.95 * len(v)))], 3) if v else None


def _r(x: Optional[float], nd: int = 3) -> Optional[float]:
    return None if x is None or not math.isfinite(x) else round(float(x), nd)


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _slopes(t: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Least-squares slope of each column of ``y`` (n, k) against ``t`` (n,), per second."""
    x = t - t.mean()
    den = float((x * x).sum())
    if den <= 0.0:
        return np.full(y.shape[1], np.nan)
    return (x[:, None] * (y - y.mean(axis=0))).sum(axis=0) / den


# -- pure functions (README S4.7 signatures) ------------------------------------------------------------------------------
def novelty(vectors: np.ndarray, recent: int, before: int) -> float:
    """§6.1's causal half-checkerboard: 1 - cos(mean of the last ``recent`` rows, mean of the ``before`` rows before
    them), >= 0. 0.0 until there are ``recent`` + 2 rows (or with a zero mean)."""
    v = np.asarray(vectors, dtype=np.float64)
    recent, before = int(recent), int(before)
    if v.ndim != 2 or recent < 1 or before < 1 or v.shape[0] < recent + 2:
        return 0.0
    n = v.shape[0]
    a = v[n - recent:].mean(axis=0)
    b = v[max(0, n - recent - before):n - recent].mean(axis=0)
    if float(np.linalg.norm(a)) <= 1e-12 or float(np.linalg.norm(b)) <= 1e-12:
        return 0.0
    return max(0.0, 1.0 - _cos(a, b))


def _listener_genre(beliefs: Any) -> tuple[Optional[str], float]:
    """The listener's genre word and conf while its view is fresh or holding (Stage 3's rule), else (None, 0)."""
    if not isinstance(beliefs, dict) or beliefs.get("state") not in ("fresh", "holding"):
        return None, 0.0
    g = beliefs.get("genre") if isinstance(beliefs.get("genre"), dict) else None
    w = g.get("word") if g else None
    if not isinstance(w, str) or not w or w == "unknown":
        return None, 0.0
    return w, _c01(_f(g.get("conf"), 0.0) or 0.0)


def genre_reading(parents: Optional[dict], beliefs: Optional[dict], ced: Optional[dict], cfg: dict, state: dict,
                  *, now_s: Optional[float] = None) -> contract.GenreReading:
    """README S4.8's genre rule. ``parents``: fresh tags' genre_parents (None: no worker or no fresh tags);
    ``ced``: {CED word: p} (the 3 s mean) or None; ``cfg``: show.yaml genre_map (resolved); ``state``: this function's
    own hysteresis state (a dict it keeps; ``now_s`` defaults to state["now_s"], else time.monotonic()).
      1. parents smoothed (EMA tau_s), updated only while CED music >= freeze_below_music (no CED: updated);
      2. the leader changes only when another leads by switch_margin for switch_dwell_s;
      3. its word: genre_map.parents[leader]: a word, or {from_listener: [words]} (the listener's genre if it is one of
         them, else none); a parent not in the table: none;
      4. conf = the leader's activation x the speech scale (1 at speech <= speech_scale_above, 0 at speech 1; speech
         is the max over ``cfg["speech_words"]``, contract.SPEECH_WORDS by default: babble counts, not only Speech),
         x the listener's conf for from_listener; 0 when there is no word;
      5. no parents: the listener's genre (via listener), else nothing (via none)."""
    now = now_s if now_s is not None else _f(state.get("now_s"))
    if now is None:
        now = time.monotonic()
    clean = {str(k): _c01(v) for k, v in (parents or {}).items() if _f(v) is not None} if isinstance(parents, dict) \
        else {}
    if not clean:
        w, c = _listener_genre(beliefs)
        if w is not None:
            return {"word": w, "conf": round(c, 3), "parent": None, "parent_p": 0.0, "via": "listener"}
        return {"word": None, "conf": 0.0, "parent": None, "parent_p": 0.0, "via": "none"}
    tau = max(1e-3, _f(cfg.get("tau_s"), GENRE_FALLBACK["tau_s"]))
    freeze = _f(cfg.get("freeze_below_music"), GENRE_FALLBACK["freeze_below_music"])
    above = _f(cfg.get("speech_scale_above"), GENRE_FALLBACK["speech_scale_above"])
    margin = _f(cfg.get("switch_margin"), GENRE_FALLBACK["switch_margin"])
    dwell = _f(cfg.get("switch_dwell_s"), GENRE_FALLBACK["switch_dwell_s"])
    music = _f((ced or {}).get("music")) if isinstance(ced, dict) else None
    words = cfg.get("speech_words") or contract.SPEECH_WORDS
    speech = contract.speech_of(ced, tuple(words)) if isinstance(ced, dict) else None
    ema: dict = state.setdefault("ema", {})
    last_t = _f(state.get("t"))
    if not (music is not None and music < freeze):
        a = 1.0 if (last_t is None or not ema) else 1.0 - math.exp(-max(0.0, now - last_t) / tau)
        for p in set(ema) | set(clean):
            x = clean.get(p, 0.0)
            ema[p] = x if p not in ema else ema[p] + (x - ema[p]) * a
    state["t"] = now
    if not ema:
        return {"word": None, "conf": 0.0, "parent": None, "parent_p": 0.0, "via": "none"}
    best = max(sorted(ema), key=lambda p: ema[p])
    leader = state.get("leader")
    if leader not in ema:
        state["leader"], state["challenger"], state["since"] = best, None, None
    elif best != leader and ema[best] >= ema[leader] + margin:
        if state.get("challenger") != best:
            state["challenger"], state["since"] = best, now
        elif now - (_f(state.get("since"), now) or now) >= dwell:
            state["leader"], state["challenger"], state["since"] = best, None, None
    else:
        state["challenger"], state["since"] = None, None
    leader = state["leader"]
    lp = ema[leader]
    scale = 1.0
    if speech is not None and speech > above:
        scale = _c01((1.0 - speech) / max(1e-6, 1.0 - above))
    table = cfg.get("parents") if isinstance(cfg.get("parents"), dict) else {}
    entry = table.get(leader)
    word: Optional[str] = None
    conf, via = 0.0, "ml"
    if isinstance(entry, str) and entry:
        word, conf = entry, lp * scale
    elif isinstance(entry, dict) and isinstance(entry.get("from_listener"), list):
        lw, lc = _listener_genre(beliefs)
        if lw is not None and lw in entry["from_listener"]:
            word, conf, via = lw, lp * scale * lc, "ml+listener"
    return {"word": word, "conf": round(_c01(conf), 3) if word else 0.0, "parent": leader,
            "parent_p": round(lp, 3), "via": via}


# -- the clock grid (the Link formula, as clock.py's consumer helpers; no import of another builder's module) -------------
class _Grid:
    """Beats, bars and phrases from a music.clock body (contract.ClockData)."""

    def __init__(self, body: Any, now_s: float, grid_conf: float = STRUCTURE_FALLBACK["grid_conf"]) -> None:
        b = body if isinstance(body, dict) else {}
        t_ms = _f(b.get("t_ms"))
        stale = t_ms is not None and now_s - t_ms / 1000.0 > CLOCK_STALE_S
        self.bpm = 0.0 if stale else (_f(b.get("bpm"), 0.0) or 0.0)
        self.gate = b.get("gate") if isinstance(b.get("gate"), str) else "none"
        self.bar_conf = _f(b.get("bar_conf"), 0.0) or 0.0
        self.phrase_conf = _f(b.get("phrase_conf"), 0.0) or 0.0
        self.bpb = max(1, int(_f(b.get("beats_per_bar"), 4) or 4))
        self.pb = max(1, int(_f(b.get("phrase_bars"), 8) or 8))
        self.bar_anchor = int(_f(b.get("bar_anchor_beat"), 0) or 0)
        self.anchor_s = (_f(b.get("anchor_ms"), 0.0) or 0.0) / 1000.0
        self.beat_anchor = int(_f(b.get("beat_at_anchor"), 0) or 0)
        self.epoch = int(_f(b.get("epoch"), 0) or 0)
        self.has_grid = self.bpm > 0.0
        self.beats = self.has_grid and self.gate != "none"              # beat snapping
        self.gridded = self.has_grid and self.gate in ("beat", "dmx")    # builds and drops
        self.ok = self.gridded and self.bar_conf >= grid_conf            # bar lines (structure.grid_conf)
        self.beat_s = 60.0 / self.bpm if self.has_grid else None
        self.bar_s = self.beat_s * self.bpb if self.beat_s else None

    def beat_at(self, t_s: float) -> float:
        return self.beat_anchor + (t_s - self.anchor_s) * self.bpm / 60.0 if self.has_grid else math.nan

    def t_of_beat(self, k: float) -> float:
        return self.anchor_s + (k - self.beat_anchor) * 60.0 / self.bpm

    def bar_pos(self, t_s: float) -> float:
        return (self.beat_at(t_s) - self.bar_anchor) / self.bpb

    def bar_at(self, t_s: float) -> int:
        return math.floor(self.bar_pos(t_s) + 1e-9)

    def line_after(self, t_s: float, bars: int = 1) -> float:
        """The first line that is a multiple of ``bars`` bars (from bar_anchor_beat) at or after ``t_s``."""
        n = math.ceil(self.bar_pos(t_s) / bars - 1e-9)
        return self.t_of_beat(self.bar_anchor + n * bars * self.bpb)

    def bar_line(self, k: int) -> float:
        return self.t_of_beat(self.bar_anchor + k * self.bpb)

    def nearest_bar(self, t_s: float) -> float:
        return self.bar_line(round(self.bar_pos(t_s)))

    def nearest_beat(self, t_s: float) -> float:
        return self.t_of_beat(round(self.beat_at(t_s)))

    def beat_after(self, t_s: float) -> float:
        return self.t_of_beat(math.ceil(self.beat_at(t_s) - 1e-9))


class _Ring:
    """The 10 Hz samples: a fixed numpy ring of rows, oldest first on read."""

    def __init__(self, n: int = HIST_N) -> None:
        self.a = np.full((n, NCOL), np.nan)
        self.i = 0
        self.n = 0

    def push(self, row: list) -> None:
        self.a[self.i] = row
        self.i = (self.i + 1) % self.a.shape[0]
        self.n = min(self.a.shape[0], self.n + 1)

    def rows(self) -> np.ndarray:
        if self.n < self.a.shape[0]:
            return self.a[:self.n]
        return np.concatenate((self.a[self.i:], self.a[:self.i]))

    def clear(self) -> None:
        self.a[:] = np.nan
        self.i = self.n = 0


def _since(rows: np.ndarray, t0: float) -> np.ndarray:
    return rows[int(np.searchsorted(rows[:, T], t0, side="left")):] if len(rows) else rows


# -- settings -----------------------------------------------------------------------------------------------------------------
def _resolve(fallback: dict, given: Any, path: str, probs: list[str]) -> dict:
    out: dict = {}
    g = given if isinstance(given, dict) else {}
    if given is not None and not isinstance(given, dict):
        probs.append(f"{path}: not a mapping: the starting points used")
    for k, fv in fallback.items():
        p = f"{path}.{k}"
        if k not in g:
            out[k] = copy.deepcopy(fv)
            continue
        v = g[k]
        if v is None and p in _NULLABLE:
            out[k] = None
        elif isinstance(fv, dict):
            out[k] = _resolve(fv, v, p, probs) if fv else (dict(v) if isinstance(v, dict) else {})
        elif isinstance(fv, list):
            xs = [_f(x) for x in v] if isinstance(v, list) else []
            if len(xs) == len(fv) and all(x is not None and x >= 0 for x in xs):
                out[k] = xs
            else:
                out[k] = list(fv)
                probs.append(f"{p}: {v!r} is not {len(fv)} numbers: {fv} used")
        elif isinstance(fv, bool):
            if isinstance(v, bool):
                out[k] = v
            else:
                out[k] = fv
                probs.append(f"{p}: {v!r} is not true/false: {fv} used")
        else:
            x = _f(v)
            bad = x is None or (p not in _SIGNED and x < 0) or (p in _POSITIVE and x <= 0)
            if bad:
                out[k] = fv
                probs.append(f"{p}: {v!r} is not a usable number: {fv} used")
            else:
                out[k] = x
    return out


def _parents_table(raw: Any, probs: list[str]) -> dict:
    out: dict = {}
    if raw is None:
        return out
    if not isinstance(raw, dict):
        probs.append("genre_map.parents: not a mapping: no genre words")
        return out
    for parent, v in raw.items():
        if isinstance(v, str) and v:
            out[str(parent)] = v
        elif (isinstance(v, dict) and isinstance(v.get("from_listener"), list)
              and all(isinstance(w, str) for w in v["from_listener"])):
            out[str(parent)] = {"from_listener": list(v["from_listener"])}
        else:
            probs.append(f"genre_map.parents.{parent}: {v!r} is not a word or {{from_listener: [words]}}: ignored")
    return out


# =====================================================================================================================
class Structure:
    """Component ``structure`` (contract.Component). Everything runs on show-brain except ``context`` and
    ``snapshot``, which read references swapped there."""

    name = "structure"
    tick_hz = contract.BRAIN_TICK_HZ

    def __init__(self, ctx: contract.BrainContext, *, mono: Callable[[], float] = time.monotonic) -> None:
        self.ctx = ctx
        self._mono = mono
        self.cfg: dict = copy.deepcopy(STRUCTURE_FALLBACK)
        self.gcfg: dict = copy.deepcopy(GENRE_FALLBACK)
        self.mlcfg: dict = dict(ML_FALLBACK)
        self.np_target: Optional[str] = None             # situations.sonos.now_playing (a room target)
        self.settings_problems: list[str] = []
        self._context: Optional[contract.MusicContext] = None
        self._snap: dict = {}
        self._tick_ms: collections.deque = collections.deque(maxlen=STAT_N)
        self._second_ms: collections.deque = collections.deque(maxlen=STAT_N)
        self._gobo_handles: dict = {}
        self._gobo_queue: list = []                      # (at_s, key) when there is no loop
        self.gobo = {"steps": 0, "skipped_cut": 0, "skipped_bar": 0, "scheduled": 0, "director": 0}
        self.event_counts: collections.Counter = collections.Counter()
        self.callback_errors = 0
        self._ev_seq = 0
        self._next_id = 0
        self._last_second: Optional[int] = None
        self._grid = _Grid(None, 0.0)
        self._reset_music()
        self._reset_track_state()
        self.track_id = 0
        self.track_via: Optional[str] = None
        self.track_start: Optional[float] = None
        self._track_pending = False
        self._sonos_key: Optional[tuple] = None
        self._tags_stream: Optional[str] = None          # the stream the ML state below was heard on
        self._reset_ml_state()
        self._ctx_parts: dict = {}
        self._build_snapshot(mono())

    def _reset_ml_state(self) -> None:
        """The genre (EMA, leader), the smoothed tag numbers and instruments, the parents window and the key's
        hysteresis: per measured stream, so a test track's genre never carries over to the room's (README S4.7)."""
        self.genre: contract.GenreReading = {"word": None, "conf": 0.0, "parent": None, "parent_p": 0.0,
                                             "via": "none"}
        self._gstate: dict = {}
        self._tag_ema: dict[str, Optional[float]] = {k: None for k, _ in TAG_FIELDS}
        self._inst_ema: dict[str, float] = {}           # instrument label -> smoothed score (tags_tau_s)
        self._parents: collections.deque = collections.deque(maxlen=64)   # (t, {parent: p}) of fresh tags lines
        self._tags_t: Optional[float] = None             # the last tags line taken (acoustic t)
        self._tags_seen_s: Optional[float] = None
        self._key_shown: dict = {"key": None, "mode": None, "key_strength": 0.0, "key_via": None}
        self._key_cand: Optional[tuple] = None           # ((key, mode), since_s): a new key waiting out key_hold

    def _reset_music(self) -> None:
        """The descriptor history and the 10 Hz samples (a new source or stream; not a track change)."""
        self.ring = _Ring()
        self._t_last: Optional[float] = None             # the newest sample's acoustic time
        self._energy: Optional[float] = None
        self.source = "none"                             # the novelty history's space: "ml" | "dsp"
        self.source_now = "none"                         # what is available this second (music.context source)
        self.vec_stream: Optional[str] = None
        self.vecs: collections.deque = collections.deque(maxlen=VEC_KEEP)   # (stamp, vec float32, energy)
        self.nov: collections.deque = collections.deque(maxlen=int(NOVELTY_KEEP_S) + 64)   # (stamp, value)
        self._nov_now = 0.0
        self._threshold = 0.0
        self._last_peak: Optional[float] = None
        self._masks: list[tuple[float, float]] = []      # candidates' own responses, left out of the threshold
        self._last_emb_t = -math.inf
        self._mfcc: collections.deque = collections.deque(maxlen=MFCC_Z_S)
        self._dsp_t: Optional[float] = None
        self.stream: Optional[str] = None
        self._silent_since: Optional[float] = None
        self._gap: Optional[dict] = None                 # {"from_s", "return_s", "sig"}
        self._sigs: collections.deque = collections.deque(maxlen=180)

    def _reset_track_state(self) -> None:
        """Sections and phases (a track change, or start)."""
        self.sections: list[dict] = []
        self.cur: Optional[dict] = None
        self.boundaries: collections.deque = getattr(self, "boundaries", None) or collections.deque(maxlen=20)
        self.chorus: Optional[str] = None
        self._pred: Optional[dict] = None
        self._pending_gap: Optional[dict] = None
        self.phase = "calm"
        self.building = False
        self.build_start: Optional[float] = None
        self.build_end: Optional[float] = None
        self._build_lufs0: Optional[float] = None
        self.build_progress = 0.0
        self.drop_line: Optional[float] = None
        self._line_epoch: Optional[int] = None
        self.drop_eta: Optional[float] = None
        self.armed: Optional[dict] = None
        self.slopes_view: dict = {}
        self.drop_until: Optional[float] = None
        self._last_drop: Optional[float] = None
        self._bd_since: Optional[float] = None
        self._bd_fail: Optional[float] = None
        self.breakdown = False
        self._bd_ref: Optional[float] = None
        self._bd_ref_pending: Optional[float] = None
        self._latches: dict[str, dict] = {}              # peak, outro: {"on", "since"} (for_s in, leave_s out)
        self._phase_since: Optional[float] = None        # when the phase word last changed (phase_dwell_s)

    # ================================================================================================================
    # Component
    # ================================================================================================================
    def provides(self) -> dict:
        return {"music_context": self.context}

    def context(self) -> Optional[contract.MusicContext]:
        """Any thread: the latest music.context body (sections and phases refreshed at 10 Hz, the rest at 1 Hz)."""
        return self._context

    def configure(self, settings: dict) -> None:
        """show.yaml ``structure``, ``genre_map``, ``ml`` (results_stale_s, tasks.tags.window_s, tasks.key.every_s) and
        ``situations.sonos.now_playing``. A bad value takes its starting point and is listed."""
        probs: list[str] = []
        s = settings if isinstance(settings, dict) else {}
        self.cfg = _resolve(STRUCTURE_FALLBACK, s.get("structure"), "structure", probs)
        gm = s.get("genre_map")
        g = _resolve({k: v for k, v in GENRE_FALLBACK.items() if k != "parents"},
                     {k: v for k, v in gm.items() if k != "parents"} if isinstance(gm, dict) else gm,
                     "genre_map", probs)
        g["parents"] = _parents_table(gm.get("parents") if isinstance(gm, dict) else None, probs)
        self.gcfg = g
        ml = s.get("ml") if isinstance(s.get("ml"), dict) else {}
        tasks = ml.get("tasks") if isinstance(ml.get("tasks"), dict) else {}
        mc = dict(ML_FALLBACK)
        for key, raw in (("results_stale_s", ml.get("results_stale_s")),
                         ("tags_window_s", (tasks.get("tags") or {}).get("window_s")
                          if isinstance(tasks.get("tags"), dict) else None),
                         ("key_every_s", (tasks.get("key") or {}).get("every_s")
                          if isinstance(tasks.get("key"), dict) else None)):
            if raw is None:
                continue
            x = _f(raw)
            if x is not None and x > 0:
                mc[key] = x
            else:
                probs.append(f"ml ({key}): {raw!r} is not a positive number: {ML_FALLBACK[key]} used")
        self.mlcfg = mc
        sit = s.get("situations") if isinstance(s.get("situations"), dict) else {}
        son = sit.get("sonos") if isinstance(sit.get("sonos"), dict) else {}
        npt = son.get("now_playing")
        self.np_target = npt if isinstance(npt, str) and npt else None
        talk = sit.get("talking") if isinstance(sit.get("talking"), dict) else {}
        words, prob = contract.speech_words(talk.get("speech_words"), "situations.talking.speech_words")
        g["speech_words"] = list(words)                  # the genre speech scale reads talking's words (S4.8)
        if prob:
            probs.append(prob)
        self.settings_problems = probs

    def restore(self, saved: dict) -> None:
        """Nothing: sections start cold (§11.2)."""

    def start(self) -> None:
        pass

    def persist(self) -> Optional[dict]:
        return None

    def stop(self) -> None:
        for h in list(self._gobo_handles.values()):
            try:
                h.cancel()
            except Exception:
                pass
        self._gobo_handles.clear()
        self._gobo_queue.clear()

    def snapshot(self) -> dict:
        """Any thread: the last dict built on show-brain (README S4.7's keys)."""
        return self._snap

    # ================================================================================================================
    # the tick
    # ================================================================================================================
    def tick(self, now_s: float) -> None:
        t0 = time.perf_counter()
        ctx = self.ctx
        self._grid = grid = _Grid(_call(ctx.clock), now_s, self.cfg["grid_conf"])
        if self.cur is None:
            self._start_section(now_s, "start", now_s, emit=False)
        self._run_gobo_queue(now_s)
        self._sample(now_s)
        self._sonos(now_s)
        self._phases(now_s, grid)
        self._predict(now_s, grid)
        sec = math.floor(now_s)
        second_ms = 0.0
        if self._last_second is None or sec != self._last_second:
            self._last_second = sec
            t1 = time.perf_counter()
            self._second(now_s, grid)
            second_ms = (time.perf_counter() - t1) * 1000.0
            self._second_ms.append(second_ms)
        self._refresh_context(now_s, grid)
        self._tick_ms.append((time.perf_counter() - t0) * 1000.0 - second_ms)   # the 10 Hz part alone

    # -- the 10 Hz samples -------------------------------------------------------------------------------------------
    def _sample(self, now_s: float) -> None:
        f = _call(self.ctx.features)
        tf = _f(f.get("t_ms")) if isinstance(f, dict) else None
        fresh = tf is not None and now_s - tf / 1000.0 <= FEATURES_STALE_S
        tr = self.cfg["track"]
        gate = (_f(f.get("gate"), 0.0) or 0.0) if fresh else 0.0
        lm = _f(f.get("lufs_m")) if fresh else None
        # silence for the track test: the gate (3 s loudness: it notices silence only ~3 s in), or the momentary
        # loudness (400 ms) at a line's rest, so a 4 s gap between songs reads as one
        self._gap_state(now_s, gate >= tr["gap_gate"] and not (lm is not None and lm <= tr["gap_lufs_m"]))
        if not fresh:
            return
        t = tf / 1000.0
        if self._t_last is not None and t <= self._t_last:
            return
        self.stream = f.get("stream") if isinstance(f.get("stream"), str) else self.stream
        r = _call(self.ctx.rhythm)
        tr = _f(r.get("t_ms")) if isinstance(r, dict) else None
        rfresh = tr is not None and abs(t - tr / 1000.0) <= RHYTHM_STALE_S
        bands = f.get("bands") if isinstance(f.get("bands"), dict) else {}

        def lvl(*names: str) -> float:
            vals = [_f((bands.get(n) or {}).get("level")) for n in names if isinstance(bands.get(n), dict)]
            vals = [v for v in vals if v is not None]
            return sum(vals) / len(vals) if vals else math.nan

        nan = math.nan
        row = [t, _f(f.get("lufs_m"), nan), _f(f.get("lufs_s"), nan), _f(f.get("brightness"), nan), gate,
               _f(f.get("energy"), nan),
               _f(r.get("low_db"), nan) if rfresh else nan, _f(r.get("percussive_ratio"), nan) if rfresh else nan,
               _f(r.get("onset_rate"), nan) if rfresh else nan, _f(r.get("harmonic_db"), nan) if rfresh else nan,
               lvl(*contract.BAND_NAMES[0:2]), lvl(*contract.BAND_NAMES[2:4]), lvl(*contract.BAND_NAMES[4:6])]
        dt = t - self._t_last if self._t_last is not None else None
        self.ring.push(row)
        self._t_last = t
        e = row[ENERGY]
        if math.isfinite(e):
            self._energy = e if (self._energy is None or dt is None) else \
                self._energy + (e - self._energy) * (1.0 - math.exp(-max(0.0, dt) / ENERGY_TAU_S))

    def _gap_state(self, now_s: float, sound: bool) -> None:
        tr = self.cfg["track"]
        if not sound:
            if self._silent_since is None:
                self._silent_since = now_s
            return
        if self._silent_since is not None:
            if now_s - self._silent_since >= tr["gap_s"]:
                self._gap = {"from_s": self._silent_since, "return_s": now_s,
                             "sig": self._sigs[-1] if self._sigs else None}
            self._silent_since = None
        cur = self.cur
        if cur is not None and cur["nvec"] == 0 and not cur.get("sounded"):
            cur["start_s"] = now_s                       # a section begins with its first sound
        if cur is not None:
            cur["sounded"] = True

    # -- Sonos now-playing (README S4.7 step 6a) ---------------------------------------------------------------------
    def _sonos(self, now_s: float) -> None:
        if not self.np_target:
            return
        room = _call(self.ctx.room)
        if not isinstance(room, dict) or not room.get("reachable"):
            return
        m = (room.get("members") or {}).get(self.np_target)
        if not isinstance(m, dict) or m.get("confidence") in ("unknown", "stale"):
            return
        b = m.get("believed")
        if not isinstance(b, dict):
            return
        tid = b.get("track_id")
        if isinstance(tid, str) and tid:
            key: Optional[tuple] = ("id", tid)
        else:
            title, artist = b.get("title"), b.get("artist")
            key = ("title", str(title), str(artist or "")) if isinstance(title, str) and title else None
        if key is None:
            return
        sits = _call(self.ctx.situations)
        rd = sits.get("sonos_playing") if isinstance(sits, dict) else None
        playing = rd.get("value") if isinstance(rd, dict) else None
        if playing is False:
            return                                       # paused or stopped: the next play decides
        if self._sonos_key is None:
            self._sonos_key = key
            return
        if key != self._sonos_key:
            self._sonos_key = key
            self._track_change(now_s, "sonos", now_s, adopt=False)

    # -- phases (README S4.7 "Every tick") -----------------------------------------------------------------------------
    def _phases(self, now_s: float, grid: _Grid) -> None:
        rows = self.ring.rows()
        if not len(rows) or self._t_last is None:
            self._set_phase("drop" if (self.drop_until is not None and now_s < self.drop_until) else "calm", now_s)
            return
        tl = self._t_last
        c = self.cfg
        last = rows[-1]
        self._build(now_s, tl, rows, grid)
        self._drop(now_s, tl, rows, grid)
        self._breakdown(now_s, tl, rows, last)
        # peak: in for for_s, out after its rule has failed for leave_s (never faster out than in)
        pk = c["peak"]
        cur = self.cur or {}
        hot = self._energy is not None and self._energy >= pk["energy_min"]
        peak = self._latch("peak", bool(hot and (cur.get("dropped") or (cur.get("letter")
                                                                          and cur.get("letter") == self.chorus))),
                           tl, pk["for_s"], pk["leave_s"])
        # outro: the same in/out rule
        oc = c["outro"]
        outro = False
        w = _since(rows, tl - oc["window_s"])
        if len(w) >= 10 and w[-1, T] - w[0, T] >= 0.8 * oc["window_s"]:
            m = np.isfinite(w[:, LUFS_S])
            if m.sum() >= 10:
                s = _slopes(w[m, T], w[m][:, [LUFS_S]])[0]
                outro = bool(math.isfinite(s) and s <= oc["max_slope_db_s"] and self._energy is not None
                             and self._energy < oc["energy_max"])
        outro = self._latch("outro", outro, tl, oc["for_s"], oc["leave_s"])
        if self.drop_until is not None and now_s < self.drop_until:
            want = "drop"
        elif self.building:
            want = "build"
        elif self.breakdown:
            want = "breakdown"
        elif peak:
            want = "peak"
        elif outro:
            want = "outro"
        else:
            want = "calm"
        self._set_phase(want, now_s)

    def _set_phase(self, want: str, now_s: float) -> None:
        """The phase word holds at least phase_dwell_s; the phases an event announces (drop, build, breakdown) show at
        once, so the word never lags its event."""
        if want == self.phase:
            return
        if (want in EVENT_PHASES or self._phase_since is None
                or now_s - self._phase_since >= self.cfg["phase_dwell_s"] - 1e-9):
            self.phase = want
            self._phase_since = now_s

    def _latch(self, name: str, cond: bool, t: float, for_s: float, leave_s: float) -> bool:
        """A phase rule with hysteresis in time: on once ``cond`` has held for ``for_s``, off once it has failed for
        ``leave_s``."""
        st = self._latches.setdefault(name, {"on": False, "since": None})
        if cond == st["on"]:
            st["since"] = None
            return st["on"]
        if st["since"] is None:
            st["since"] = t
        if t - st["since"] >= (for_s if cond else leave_s) - 1e-9:
            st["on"], st["since"] = cond, None
        return st["on"]

    def _build(self, now_s: float, tl: float, rows: np.ndarray, grid: _Grid) -> None:
        b = self.cfg["build"]
        ms = b["min_slope"]
        w = _since(rows, tl - b["window_s"])
        cols = [LUFS_M, BRIGHT, ONSET, LOW]
        slopes = None
        if len(w) >= 10:
            m = np.isfinite(w[:, cols]).all(axis=1)
            ww = w[m]
            if len(ww) >= 10 and ww[-1, T] - ww[0, T] >= 0.75 * b["window_s"]:
                slopes = _slopes(ww[:, T], ww[:, cols])
        sound = rows[-1, GATE] >= self.cfg["track"]["gap_gate"]
        if slopes is not None:
            self.slopes_view = {"lufs_db_s": _r(slopes[0], 4), "brightness_s": _r(slopes[1], 5),
                            "onset_rate_s": _r(slopes[2], 4), "low_db_s": _r(slopes[3], 4)}
        else:
            self.slopes_view = {}
        if not self.building:
            ok = (slopes is not None and grid.gridded and sound and slopes[0] > ms["lufs_db_s"]
                  and slopes[1] > ms["brightness_s"] and slopes[2] > ms["onset_rate_s"]
                  and slopes[3] <= b["low_max_slope_db_s"])
            if ok:
                ww = _since(rows, tl - b["window_s"])
                lm = ww[:, LUFS_M]
                i = 0
                if np.isfinite(lm).any():                # the ramp's foot: the last sample near the window's minimum
                    near = np.nonzero(lm <= np.nanmin(lm) + b["foot_db"])[0]
                    i = int(near[-1]) if len(near) else 0
                self.building = True
                self.build_start = float(ww[i, T])
                self.build_end = None
                self._build_lufs0 = float(lm[i]) if math.isfinite(lm[i]) else None
                self.drop_line = None
                if self.armed is not None:               # a new build: its own line arms afresh
                    self._disarm(now_s, "disarmed")
                self._update_build(now_s, tl, rows, grid)
                self._emit("build", self.build_start, now_s, phase="build",
                           build_progress=round(self.build_progress, 3), section_id=self._cur_id())
            return
        held = (slopes is not None and grid.gridded and sound
                and slopes[0] > self.cfg["fastled"]["build_end"] * ms["lufs_db_s"]
                and slopes[3] <= b["low_max_slope_db_s"])
        if not held:
            self.building = False
            self.build_end = tl
            self.build_progress = 0.0
            self.drop_eta = None
            a = self.armed                               # the build gave out well before its line: no drop is coming
            bar = grid.bar_s or NO_GRID_BAR_S
            if a is not None and a["line"] - now_s > self.cfg["drop"]["disarm_bars"] * bar + 1e-6:
                self._disarm(now_s, "disarmed")
            return
        self._update_build(now_s, tl, rows, grid)

    def _update_build(self, now_s: float, tl: float, rows: np.ndarray, grid: _Grid) -> None:
        b = self.cfg["build"]
        line = None
        win = self.cfg["drop"]["within_beats"] * (grid.beat_s or 0.0) + DROP_DETECT_S
        if (grid.ok and self.drop_line is not None and self._line_epoch == grid.epoch
                and self.drop_line - 1e-6 <= now_s <= self.drop_line + win):
            line = self.drop_line                        # at the line: kept while a drop can still be read on it
        elif grid.ok and self.build_start is not None:
            l8 = grid.line_after(now_s + 1e-3, 8)
            line = l8
            if grid.phrase_conf >= b["phrase16_conf"]:
                l16 = grid.line_after(now_s + 1e-3, 16)
                if (l16 - self.build_start) <= 16 * grid.bar_s + 1e-6:
                    line = l16
        self.drop_line = line
        self._line_epoch = grid.epoch
        if line is not None and self.build_start is not None and line > self.build_start:
            self.build_progress = _c01((now_s - self.build_start) / (line - self.build_start))
            self.drop_eta = max(0.0, (line - now_s) / grid.beat_s)
        else:
            lm = rows[-3:, LUFS_M]
            rise = float(np.nanmean(lm)) - self._build_lufs0 if (self._build_lufs0 is not None
                                                                 and np.isfinite(lm).any()) else 0.0
            self.build_progress = _c01(rise / b["full_rise_db"])
            self.drop_eta = None
        if line is None:
            return
        ahead = line - now_s
        beats = ahead / grid.beat_s
        lead = self.cfg["events"]["min_lead_s"]
        if beats <= b["arm_beats"] and beats >= ARM_MIN_BEATS and ahead >= lead:
            if self.armed is None or abs(self.armed["line"] - line) > LINE_MOVED_BEATS * grid.beat_s:
                if self.armed is not None:
                    self._gobo_cancel(("drop", self.armed["line"]))
                self.armed = {"line": line, "epoch": grid.epoch, "at_s": now_s}
                self._emit("drop_armed", line, now_s, eta_beats=round(beats, 3), section_id=self._cur_id())
                self._gobo_at(line, ("drop", line))

    def _disarm(self, now_s: float, why: str) -> None:
        """The armed line is off: ``disarmed`` (the build ended more than drop.disarm_bars before it, or a new track)
        cancels its GOBO; ``missed`` (it passed with no drop) comes after the line. Either way a drop_armed with
        eta_beats None and that ``detail`` goes out, at the line, so the director can release or refund its token."""
        a, self.armed = self.armed, None
        if a is None:
            return
        if why != "missed":
            self._gobo_cancel(("drop", a["line"]))
        self._emit("drop_armed", a["line"], now_s, eta_beats=None, detail=why, section_id=self._cur_id())

    def _drop(self, now_s: float, tl: float, rows: np.ndarray, grid: _Grid) -> None:
        d = self.cfg["drop"]
        if self.drop_until is not None and now_s >= self.drop_until:
            self.drop_until = None
        if self.armed is not None and tl > self.armed["line"] + d["within_beats"] * (grid.beat_s or 0.0) \
                + DROP_DETECT_S:
            self._disarm(now_s, "missed")                # the armed line passed with no drop
        if not grid.gridded or not grid.beat_s:
            return
        if self._last_drop is not None and tl - self._last_drop < self.cfg["fastled"]["drop_cooldown_s"]:
            return
        win = d["within_beats"] * grid.beat_s + DROP_DETECT_S
        eligible = self.building or (self.build_end is not None and tl - self.build_end <= win) or (
            self.armed is not None and abs(tl - self.armed["line"]) <= win)
        if not eligible:
            return
        ref = tl - max(d["within_beats"] * grid.beat_s, DROP_DETECT_S)
        if not self.building and self.build_end is not None:
            ref = min(ref, self.build_end)
        base = rows[(rows[:, T] >= ref - DROP_BASE_S) & (rows[:, T] < ref)]
        now_rows = rows[rows[:, T] > tl - DROP_NOW_S - 1e-6]
        if len(base) < 5 or not len(now_rows):
            return
        with np.errstate(all="ignore"):
            b_low, b_lufs = np.nanmean(base[:, LOW]), np.nanmean(base[:, LUFS_M])
            n_low, n_lufs = np.nanmean(now_rows[:, LOW]), np.nanmean(now_rows[:, LUFS_M])
        if not all(math.isfinite(x) for x in (b_low, b_lufs, n_low, n_lufs)):
            return
        d_low, d_lufs = n_low - b_low, n_lufs - b_lufs
        if d_low < d["low_jump_db"] or d_lufs < d["lufs_jump_db"]:
            return
        # heard: the first sample after ref half-way up the bass jump
        after = rows[rows[:, T] >= ref]
        idx = np.nonzero(after[:, LOW] >= b_low + 0.5 * d["low_jump_db"])[0]
        heard = float(after[idx[0], T]) if len(idx) else tl
        if grid.beats:
            nb = grid.nearest_beat(heard)
            if abs(nb - heard) <= d["snap_beats"] * grid.beat_s:
                heard = nb
        with np.errstate(all="ignore"):
            bb = [np.nanmean(base[:, k]) for k in (B_LO, B_MID, B_HI)]
            nn = [np.nanmean(now_rows[:, k]) for k in (B_LO, B_MID, B_HI)]
        fl = self.cfg["fastled"]
        spec = sum(w * abs(x - y) for w, x, y in zip(fl["band_weights"], nn, bb)
                   if math.isfinite(x) and math.isfinite(y))
        gate = float(rows[-1, GATE]) if math.isfinite(rows[-1, GATE]) else 0.0
        we, wb, ws, wl = fl["drop_weights"]
        strength = _c01(we * _c01(d_lufs / (2.0 * d["lufs_jump_db"])) + wb * _c01(d_low / (2.0 * d["low_jump_db"]))
                        + ws * _c01(spec) + wl * min(1.0, gate))
        at_line = self.armed is not None and abs(heard - self.armed["line"]) <= win
        if self.armed is not None and not at_line:
            self._gobo_cancel(("drop", self.armed["line"]))    # it came before its line: the line steps nothing
        self._last_drop = heard
        self.building = False
        self.build_end = None
        self.build_progress = 0.0
        self.drop_eta = None
        self.drop_line = None
        self.armed = None
        self._cancel_pred()
        self.drop_until = heard + d["hold_bars"] * (grid.bar_s or NO_GRID_BAR_S)
        sec = self._start_section(heard, "drop", now_s, emit=False)
        sec["dropped"] = True
        self._boundary_record(heard, None, heard, "drop", sec["id"])
        self._emit("drop", heard, now_s, section_id=sec["id"], strength=round(strength, 3), phase="drop")
        if not at_line:                                  # the screens cut on the first bar line the renderer can make
            self._gobo_at(self._at_for_confirmed(now_s, grid), ("section", sec["id"]))

    def _breakdown(self, now_s: float, tl: float, rows: np.ndarray, last: np.ndarray) -> None:
        bd = self.cfg["breakdown"]
        low, perc, gate = last[LOW], last[PERC], last[GATE]
        known = math.isfinite(low) and math.isfinite(perc)
        if self.breakdown and self._bd_ref is not None:
            ref = self._bd_ref
        elif known:
            hist = _since(rows, tl - BREAKDOWN_REF_S)[:, LOW]
            hist = hist[np.isfinite(hist)]
            ref = float(np.median(hist)) if len(hist) >= 20 else None
        else:
            ref = None
        cond = known and ref is not None and low <= ref - bd["low_drop_db"] and perc < bd["perc_ratio_max"] and \
            gate >= bd["min_gate"]
        if self.breakdown:                               # left once its rule has failed for leave_s (no rhythm frame
            if cond:                                     # counts as failing): never faster out than in
                self._bd_fail = None
            else:
                self._bd_fail = self._bd_fail if self._bd_fail is not None else tl
                if tl - self._bd_fail >= bd["leave_s"] - 1e-9:
                    self.breakdown = False
                    self._bd_ref = None
                    self._bd_since = None
                    self._bd_fail = None
            return
        if not cond:
            self._bd_since = None
            return
        if self._bd_since is None:
            self._bd_since = tl
            self._bd_ref_pending = ref
        if tl - self._bd_since >= bd["for_s"]:
            self.breakdown = True
            self._bd_ref = self._bd_ref_pending if self._bd_ref_pending is not None else ref
            self._bd_fail = None
            self._emit("breakdown", self._bd_since, now_s, section_id=self._cur_id(), phase="breakdown")

    # -- known repeats: the predicted line (README S4.7 step 5) ----------------------------------------------------------
    def _predict(self, now_s: float, grid: _Grid) -> None:
        lead = self.cfg["events"]["min_lead_s"]
        p = self._pred
        if p is not None:
            if now_s >= p["line"] - 1e-6:
                self._pred = None
                sec = self._start_section(p["line"], "predicted", now_s, emit=False)
                sec["letter"], sec["repeat_of"] = p["letter"], p["repeat_of"]
                sec["provisional"] = True
                self._boundary_record(p["line"], None, p["line"], "predicted", sec["id"])
            return
        cur = self.cur
        if cur is None or not cur.get("expected_bars") or cur.get("predicted_done") or not grid.ok:
            return
        line = self._line_from(cur["start_s"], int(cur["expected_bars"]), grid)
        ahead = line - now_s
        if ahead < lead:
            cur["predicted_done"] = True                 # identified too late for this line
            return
        if ahead > max(self.cfg["predict_lead_bars"] * grid.bar_s, lead + 0.15):
            return
        cur["predicted_done"] = True
        nxt = self._follower(cur.get("repeat_of"))
        sid = self._next_id
        self._pred = {"line": line, "letter": nxt["letter"] if nxt else None,
                      "repeat_of": nxt["id"] if nxt else None, "from": cur["id"], "id": sid}
        self._emit("section_change", line, now_s, section_id=sid, letter=self._pred["letter"],
                   repeat_of=self._pred["repeat_of"], predicted=True, boundary_ms=round(line * 1000.0, 1),
                   detail="repeat")
        self._gobo_at(line, ("section", sid))

    def _cancel_pred(self) -> None:
        if self._pred is not None:
            self._gobo_cancel(("section", self._pred["id"]))
            self._pred = None

    def _follower(self, sid: Optional[int]) -> Optional[dict]:
        """The section that followed section ``sid`` in this track (a known repeat's next letter)."""
        if sid is None:
            return None
        for i, s in enumerate(self.sections):
            if s["id"] == sid and i + 1 < len(self.sections) and self.sections[i + 1] is not self.cur:
                return self.sections[i + 1]
        return None

    @staticmethod
    def _line_from(start_s: float, bars: int, grid: _Grid) -> float:
        return grid.bar_line(round(grid.bar_pos(start_s)) + bars)

    # ================================================================================================================
    # every second
    # ================================================================================================================
    def _second(self, now_s: float, grid: _Grid) -> None:
        source, stream, new = self._descriptor(now_s)
        self.source_now = source
        if new and (source != self.source or stream != self.vec_stream):
            if self.vecs:
                log.info("structure: descriptor %s/%s -> %s/%s: novelty history cleared", self.source,
                         self.vec_stream, source, stream)
            self.vecs.clear()
            self.nov.clear()
            self._last_peak = None
            self._masks = []
        if new:
            self.source, self.vec_stream = source, stream
        for stamp, vec in new:
            self._add_vector(now_s, stamp, vec, grid)
        self._identify_current(now_s)
        self._chorus()
        sig = self._signature(now_s, grid)
        if sig is not None:
            self._sigs.append(sig)
        self._pending_gap_check(now_s, sig, grid)
        self._missed(now_s, grid)
        self._context_second(now_s, grid)

    def _descriptor(self, now_s: float) -> tuple[str, Optional[str], list]:
        view = _call(self.ctx.ml)
        if self._ml_ok(view):
            stale = self._stale(view, "tags")
            half = self.mlcfg["tags_window_s"] / 2.0
            out = []
            newest = self._last_emb_t
            for e in view.get("emb_recent") or []:
                try:
                    _, t, vec = e
                    t = float(t)
                except (TypeError, ValueError):
                    continue
                if t <= self._last_emb_t:
                    continue
                newest = max(newest, t)
                if now_s - t > stale:
                    continue
                v = np.asarray(vec, dtype=np.float32).ravel()
                if v.size and np.isfinite(v).all():
                    out.append((t - half, v))
            self._last_emb_t = newest
            return "ml", view.get("stream"), out
        r = _call(self.ctx.rhythm)
        tr = _f(r.get("t_ms")) if isinstance(r, dict) else None
        if tr is None or now_s - tr / 1000.0 > RHYTHM_STALE_S:
            return "none", None, []
        t = tr / 1000.0
        if self._dsp_t is not None and t <= self._dsp_t:
            return "dsp", r.get("stream"), []
        self._dsp_t = t
        if (_f(r.get("gate"), 0.0) or 0.0) < self.cfg["track"]["gap_gate"]:
            return "dsp", r.get("stream"), []
        ch = np.asarray([_f(x, 0.0) for x in (r.get("chroma12_avg") or [])][:12], dtype=np.float64)
        mf = np.asarray([_f(x, 0.0) for x in (r.get("mfcc13") or [])][:13], dtype=np.float64)
        if ch.size != 12 or mf.size != 13:
            return "dsp", r.get("stream"), []
        w = self.cfg["dsp_weights"]
        nc = float(np.linalg.norm(ch))
        ch = ch / nc * w["chroma"] if nc > 1e-12 else ch * 0.0
        self._mfcc.append(mf)
        hist = np.asarray(self._mfcc)
        z = (mf - hist.mean(axis=0)) / (hist.std(axis=0) + 1e-6) if len(hist) >= 2 else mf * 0.0
        nz = float(np.linalg.norm(z))
        z = z / nz * w["mfcc"] if nz > 1e-12 else z * 0.0
        return "dsp", r.get("stream"), [(t - DSP_STAMP_S, np.concatenate((ch, z)).astype(np.float32))]

    def _stale(self, view: Any, task: str) -> float:
        """How old ``task``'s result may be and still count (MLView.stale_s: stretched with the worker's intervals)."""
        return contract.ml_stale_s(view, task, self.mlcfg["results_stale_s"])

    @staticmethod
    def _ml_ok(view: Any) -> bool:
        if not isinstance(view, dict) or view.get("state") != "running":
            return False
        hello = view.get("hello")
        tasks = hello.get("tasks") if isinstance(hello, dict) else None
        return not (isinstance(tasks, dict) and tasks.get("tags") is False)

    def _add_vector(self, now_s: float, stamp: float, vec: np.ndarray, grid: _Grid) -> None:
        rows = self.ring.rows()
        e = 0.0
        if len(rows):
            w = rows[(rows[:, T] > stamp - 1.0) & (rows[:, T] <= stamp + 1.0)][:, ENERGY]
            w = w[np.isfinite(w)]
            e = float(w.mean()) if len(w) else 0.0
        self.vecs.append((stamp, vec, e))
        if self.cur is not None:
            self.cur["nvec"] += 1
        nv = self.cfg["novelty"]
        rec, bef = int(round(nv["recent_s"])), int(round(nv["before_s"]))
        k = rec + bef
        tail = list(self.vecs)[-k:]
        dim = vec.shape[0]
        tail = [x for x in tail if x[1].shape[0] == dim]
        v = novelty(np.stack([x[1] for x in tail]), rec, bef) if len(tail) >= rec + 2 else 0.0
        self.nov.append((stamp, v))
        self._nov_now = v
        self._masks = [m for m in self._masks if m[1] >= stamp - nv["history_s"]]
        self._threshold = self._threshold_at(stamp)
        self._candidate(now_s, grid)

    def _threshold_at(self, ps: float) -> float:
        """median + k_mad x MAD of the background before a peak at ``ps``: the novelty in [ps - history_s,
        ps - recent_s), less each earlier real boundary's own response (tau to tau + recent_s + before_s: the old
        section still in the before-window), which would otherwise lift the median when sections are short. Never
        under novelty.min[source]; that alone until there are MIN_BACKGROUND values."""
        nv = self.cfg["novelty"]
        lo, hi = ps - nv["history_s"], ps - nv["recent_s"]
        vals = [v for s_, v in self.nov if lo <= s_ < hi and not any(m0 <= s_ <= m1 for m0, m1 in self._masks)]
        floor = self._min_novelty()
        if len(vals) < MIN_BACKGROUND:
            return floor
        a = np.asarray(vals)
        med = float(np.median(a))
        return max(floor, med + nv["k_mad"] * float(np.median(np.abs(a - med))))

    def _candidate(self, now_s: float, grid: _Grid) -> None:
        nv = self.cfg["novelty"]
        if len(self.nov) < 3:
            return
        p = len(self.nov) - 2
        ps, pv = self.nov[p]
        if not pv > self.nov[p + 1][1]:
            return
        lo = max(0, p - int(round(nv["peak_s"])))
        if any(self.nov[j][1] > pv for j in range(lo, p)):
            return
        thr = self._threshold_at(ps)
        if pv < thr:
            return
        if self._last_peak is not None and ps - self._last_peak <= nv["peak_s"] + 1e-6:
            return
        # local contrast: the recent window also differs from the recent_s vectors just before it, so the plateau a
        # short blip leaves in the 15 s before-window (the recent window steady) makes no boundary of its own
        if self._local_contrast(ps) < thr:
            return
        self._last_peak = ps
        tau = self._tau(ps, nv)
        if self._boundary(now_s, tau, pv, thr, grid):
            self._masks.append((tau - 1e-6, tau + nv["recent_s"] + nv["before_s"]))

    def _local_contrast(self, ps: float) -> float:
        rec = int(round(self.cfg["novelty"]["recent_s"]))
        vs = list(self.vecs)
        i = next((k for k in range(len(vs) - 1, -1, -1) if abs(vs[k][0] - ps) < 1e-6), None)
        if i is None or i + 1 < 2 * rec:
            return 1.0                                   # too early to tell: the other rules decide
        dim = vs[i][1].shape
        win = [x[1] for x in vs[i + 1 - 2 * rec:i + 1] if x[1].shape == dim]
        if len(win) < 2 * rec:
            return 1.0
        return max(0.0, 1.0 - _cos(np.mean(win[rec:], axis=0, dtype=np.float64),
                                   np.mean(win[:rec], axis=0, dtype=np.float64)))

    def _min_novelty(self) -> float:
        mn = self.cfg["novelty"]["min"]
        return float(mn.get(self.source, mn.get("dsp", STRUCTURE_FALLBACK["novelty"]["min"]["dsp"])))

    def _tau(self, peak_stamp: float, nv: dict) -> float:
        vs = list(self.vecs)
        best, best_t = -1.0, None
        for i in range(TAU_PAST, len(vs)):
            s = vs[i][0]
            if s < peak_stamp - TAU_FROM_S - 1e-6 or s > peak_stamp - TAU_TO_S + 1e-6:
                continue
            prev = [x[1] for x in vs[i - TAU_PAST:i] if x[1].shape == vs[i][1].shape]
            if len(prev) < TAU_PAST:
                continue
            d = 1.0 - _cos(vs[i][1].astype(np.float64), np.mean(prev, axis=0))
            if d > best:
                best, best_t = d, s
        return best_t if best_t is not None else peak_stamp - (int(round(nv["recent_s"])) - 1)

    def _snap_time(self, t_s: float, grid: _Grid) -> tuple[float, Optional[str]]:
        if grid.ok:
            return grid.nearest_bar(t_s), "bar"
        if grid.beats:
            return grid.nearest_beat(t_s), "beat"
        return t_s, None

    def _boundary(self, now_s: float, tau: float, nov_v: float, thr: float, grid: _Grid) -> bool:
        """A confirmed candidate at ``tau``. True when it was a real boundary (a new section, the same as a predicted
        line / drop / track change, or a track change); False when dropped as too close to the last."""
        snapped, how = self._snap_time(tau, grid)
        rec = {"t_s": round(tau, 3), "novelty": round(nov_v, 4), "snapped": round(snapped, 3), "to": how,
               "kind": "confirmed", "missed": False, "section_id": None, "threshold": round(thr, 4)}
        bar = grid.bar_s if grid.has_grid else NO_GRID_BAR_S
        for b in reversed(self.boundaries):              # the same as a predicted line, a drop or a track change
            if b["kind"] in ("predicted", "drop", "track") and abs(b["snapped"] - snapped) <= self.cfg["same_bars"] * bar + 1e-6:
                b["matched"] = True
                b["missed"] = False
                rec.update(same_as=b["section_id"])
                self.boundaries.append(rec)
                return True
        jump = self._track_jump(now_s, tau, nov_v, thr, grid)
        if jump is not None:
            rec["kind"] = "track"
            rec["why"] = jump
            self._track_change(now_s, "dsp", snapped, adopt=False, rec=rec)
            return True
        cur = self.cur
        min_len = self.cfg["min_section_bars"] * grid.bar_s if grid.has_grid and grid.beats \
            else self.cfg["min_section_s"]
        if cur is not None and snapped - cur["start_s"] < min_len - 1e-6:
            rec["dropped"] = "too close to the last"
            self.boundaries.append(rec)
            return False
        sec = self._start_section(snapped, "confirmed", now_s, emit=True)
        rec["section_id"] = sec["id"]
        self.boundaries.append(rec)
        g = self._gap
        if g is not None and now_s - g["return_s"] <= self.cfg["track"]["window_s"] and tau >= g["from_s"] - 1.0:
            self._pending_gap = {"until": g["return_s"] + self.cfg["track"]["window_s"], "sig": g["sig"],
                                 "section_id": sec["id"]}
        return True

    def _boundary_record(self, t_s: float, nov_v: Optional[float], snapped: float, kind: str, sid: int) -> None:
        self.boundaries.append({"t_s": round(t_s, 3), "novelty": None if nov_v is None else round(nov_v, 4),
                                "snapped": round(snapped, 3), "kind": kind, "missed": False, "matched": False,
                                "section_id": sid})

    def _missed(self, now_s: float, grid: _Grid) -> None:
        nv = self.cfg["novelty"]
        bar = grid.bar_s if grid.has_grid else NO_GRID_BAR_S
        late = self.cfg["same_bars"] * bar + nv["recent_s"] + nv["peak_s"] + PREDICT_SLACK_S
        for b in self.boundaries:
            if b["kind"] == "predicted" and not b.get("matched") and not b.get("missed_checked") \
                    and now_s > b["snapped"] + late:
                b["missed"] = True
                b["missed_checked"] = True

    # -- sections ------------------------------------------------------------------------------------------------------
    def _cur_id(self) -> Optional[int]:
        return self.cur["id"] if self.cur is not None else None

    def _start_section(self, start_s: float, kind: str, now_s: float, *, emit: bool, detail: str = "section") -> dict:
        grid = self._grid
        if kind != "predicted" and self._pred is not None:
            self._cancel_pred()                          # evidence first: a pending predicted line no longer applies
        if self.cur is not None:
            self._finish(self.cur, start_s, grid)
        sid = self._next_id
        self._next_id += 1
        sec = {"id": sid, "letter": None, "repeat_of": None, "start_s": float(start_s), "end_s": None, "bars": None,
               "kind": kind, "identified": False, "centroid": None, "space": None, "energy": None, "nvec": 0,
               "expected_bars": None, "dropped": False, "provisional": False, "predicted_done": False,
               "sounded": kind != "start"}
        self.sections.append(sec)
        mx = int(self.cfg["max_sections"])
        if len(self.sections) > mx:
            del self.sections[:len(self.sections) - mx]
        self.cur = sec
        if emit:
            at = self._at_for_confirmed(now_s, grid)
            self._emit("section_change", at, now_s, section_id=sid, letter=None, repeat_of=None, predicted=False,
                       boundary_ms=round(start_s * 1000.0, 1), detail=detail)
            self._gobo_at(at, ("section", sid))
        return sec

    def _at_for_confirmed(self, now_s: float, grid: _Grid) -> float:
        t = now_s + self.cfg["events"]["min_lead_s"]
        if grid.ok:
            return grid.line_after(t, 1)
        if grid.beats:
            return grid.beat_after(t)
        return t

    def _finish(self, sec: dict, end_s: float, grid: _Grid) -> None:
        sec["end_s"] = float(end_s)
        if grid.ok and grid.bar_s:
            sec["bars"] = max(1, int(round((end_s - sec["start_s"]) / grid.bar_s)))
        cen, e, n = self._centroid(sec["start_s"] + EDGE_S, end_s - EDGE_S)
        if cen is None:
            cen, e, n = self._centroid(sec["start_s"], end_s)
        if cen is not None:
            sec["centroid"], sec["space"] = cen, self.source
        _, e_all, _ = self._centroid(sec["start_s"], end_s, energy_only=True)
        if e_all is not None:
            sec["energy"] = e_all
        if not sec["identified"] and cen is not None:
            self._identify(sec, cen)

    def _centroid(self, t0: float, t1: float, *, energy_only: bool = False
                  ) -> tuple[Optional[np.ndarray], Optional[float], int]:
        sel = [x for x in self.vecs if t0 - 1e-6 <= x[0] <= t1 + 1e-6]
        if sel and not energy_only:
            dim = sel[-1][1].shape[0]
            sel = [x for x in sel if x[1].shape[0] == dim]
        if not sel:
            return None, None, 0
        e = float(np.mean([x[2] for x in sel]))
        if energy_only:
            return None, e, len(sel)
        return np.mean(np.stack([x[1] for x in sel]).astype(np.float64), axis=0), e, len(sel)

    def _identify_current(self, now_s: float) -> None:
        cur = self.cur
        if cur is None or cur["identified"] or not self.vecs:
            return
        latest = self.vecs[-1][0]
        if latest - cur["start_s"] < self.cfg["identify_after_s"] - 1e-6:
            return
        cen, _, n = self._centroid(cur["start_s"] + EDGE_S, latest - EDGE_S)
        if cen is None:
            cen, _, n = self._centroid(cur["start_s"], latest)
        if cen is None:
            return
        self._identify(cur, cen)

    def _identify(self, sec: dict, cen: np.ndarray) -> None:
        sim = self.cfg["repeat_sim"].get(self.source, self.cfg["repeat_sim"].get("dsp", 0.8))
        best, best_s = -2.0, None
        for s in self.sections:
            if s is sec or s["id"] >= sec["id"] or s.get("centroid") is None or s.get("space") != self.source \
                    or s["centroid"].shape != cen.shape or s.get("letter") is None:
                continue
            c = _cos(cen, s["centroid"])
            if c > best:
                best, best_s = c, s
        sec["identified"] = True
        sec["provisional"] = False
        sec["similarity"] = round(best, 4) if best_s is not None else None
        if best_s is not None and best >= sim:
            sec["letter"], sec["repeat_of"] = best_s["letter"], best_s["id"]
            bars = best_s.get("bars")
            if bars is None:
                firsts = [s for s in self.sections if s.get("letter") == best_s["letter"] and s.get("bars")
                          and s is not sec]
                bars = firsts[0]["bars"] if firsts else None
            sec["expected_bars"] = bars
        else:
            used = {s["letter"] for s in self.sections if s is not sec and s.get("letter") and s.get("identified")}
            n = len(used)
            sec["letter"] = chr(ord("A") + min(n, 25))
            sec["repeat_of"] = None
            sec["expected_bars"] = None
        if sec is self.cur:
            sec["space"] = self.source

    def _chorus(self) -> None:
        stats: dict[str, list] = {}
        for s in self.sections:
            if not s.get("identified") or not s.get("letter"):
                continue
            e = s.get("energy")
            if e is None and s is self.cur:
                _, e, _ = self._centroid(s["start_s"], math.inf, energy_only=True)
            stats.setdefault(s["letter"], []).append(e if e is not None else 0.0)
        best, best_v = None, -1.0
        for letter in sorted(stats):
            es = stats[letter]
            if len(es) < 2:
                continue
            v = len(es) * float(np.mean(es))
            if v > best_v:
                best, best_v = letter, v
        self.chorus = best

    # -- track changes (README S4.7 step 6) ---------------------------------------------------------------------------------
    def _signature(self, now_s: float, grid: _Grid) -> Optional[dict]:
        if self._silent_since is not None:
            return None
        kv = self._ctx_parts.get("key") or {}
        g = self.genre
        return {"t": now_s, "bpm": grid.bpm if grid.beats else None, "key": kv.get("key"), "mode": kv.get("mode"),
                "key_strength": kv.get("key_strength", 0.0), "parent": self._recent_parent(now_s),
                "leader": g.get("parent") if g.get("via") in ("ml", "ml+listener") else None}

    def _recent_parent(self, now_s: float) -> Optional[str]:
        """The leading Discogs parent of the fresh tags lines' mean over the last track.parent_window_s, when it leads
        the runner-up by track.parent_margin; None for a near tie (no genre evidence either way) or no tags."""
        tr = self.cfg["track"]
        sel = [gp for t, gp in self._parents if now_s - t <= tr["parent_window_s"] + 1e-6]
        if not sel:
            return None
        mean: dict[str, float] = {}
        for gp in sel:
            for k, v in gp.items():
                mean[k] = mean.get(k, 0.0) + v / len(sel)
        ranked = sorted(mean.items(), key=lambda kv: (-kv[1], kv[0]))
        if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < tr["parent_margin"] - 1e-9:
            return None
        return ranked[0][0]

    @staticmethod
    def _pc(key: Any) -> Optional[int]:
        return contract.KEY_NAMES.index(key) if key in contract.KEY_NAMES else None

    def _related(self, a: dict, b: dict) -> bool:
        """The same key for a track change: equal, parallel (C major / C minor) or relative (C major / A minor)."""
        pa, pb = self._pc(a.get("key")), self._pc(b.get("key"))
        if pa is None or pb is None:
            return a.get("key") == b.get("key")
        ma, mb = a.get("mode"), b.get("mode")
        if pa == pb:
            return True
        if ma == mb or None in (ma, mb):
            return False
        major, minor = (pa, pb) if ma == "major" else (pb, pa)
        return (major + 9) % 12 == minor

    def _jumps(self, a: Optional[dict], b: Optional[dict], genre: str = "parent") -> list[str]:
        """The independent evidence for another song between signatures ``a`` and ``b``: the clock's tempo (off by
        more than tempo_jump, not x2 or x0.5), the key (both at key_min_conf or more, and not the same, parallel or
        relative key), and the genre (``genre``: "parent", the recent tags' clear leader, or "leader",
        genre_reading's smoothed one)."""
        out: list[str] = []
        if not a or not b:
            return out
        tr = self.cfg["track"]
        tj = tr["tempo_jump"]
        if a.get("bpm") and b.get("bpm"):
            r = b["bpm"] / a["bpm"]
            octave = abs(r / 2.0 - 1.0) <= tj or abs(r * 2.0 - 1.0) <= tj
            if abs(r - 1.0) > tj and not octave:
                out.append(f"tempo {a['bpm']:.0f} -> {b['bpm']:.0f}")
        kc = tr["key_min_conf"]
        if (a.get("key") and b.get("key") and (a.get("key_strength") or 0) >= kc
                and (b.get("key_strength") or 0) >= kc and not self._related(a, b)):
            out.append(f"key {a['key']} {a.get('mode')} -> {b['key']} {b.get('mode')}")
        if a.get(genre) and b.get(genre) and a[genre] != b[genre]:
            out.append(f"genre {a[genre]} -> {b[genre]}")
        return out

    def _jump(self, a: Optional[dict], b: Optional[dict]) -> Optional[str]:
        """After a gap: any one jump (the gap is evidence already)."""
        js = self._jumps(a, b, "parent")
        return js[0] if js else None

    def _sig_before(self, t_s: float) -> Optional[dict]:
        for s in reversed(self._sigs):
            if s["t"] <= t_s:
                return s
        return None

    def _track_jump(self, now_s: float, tau: float, nov_v: float, thr: float, grid: _Grid) -> Optional[str]:
        tr = self.cfg["track"]
        now_sig = self._signature(now_s, grid)
        g = self._gap
        if g is not None and now_s - g["return_s"] <= tr["window_s"] and tau >= g["from_s"] - 1.0:
            j = self._jump(g["sig"], now_sig)
            if j:
                return f"after a gap: {j}"
        if tr["spike_k"] is not None and nov_v >= tr["spike_k"] * thr:
            # no gap: a section change inside one song can move the key or the tags' leader, so it takes
            # track.spike_jumps independent jumps, the genre by its smoothed leader (README S4.7)
            js = self._jumps(self._sig_before(tau - 2.0), now_sig, "leader")
            if len(js) >= int(tr["spike_jumps"]):
                return "novelty spike: " + ", ".join(js)
        return None

    def _pending_gap_check(self, now_s: float, sig: Optional[dict], grid: _Grid) -> None:
        pg = self._pending_gap
        if pg is None:
            return
        if now_s > pg["until"] or self.cur is None or self.cur["id"] != pg["section_id"]:
            self._pending_gap = None
            return
        j = self._jump(pg["sig"], sig)
        if j:
            self._pending_gap = None
            rec = {"t_s": round(self.cur["start_s"], 3), "novelty": None, "snapped": round(self.cur["start_s"], 3),
                   "kind": "track", "missed": False, "section_id": self.cur["id"], "why": f"after a gap: {j}"}
            self._track_change(now_s, "dsp", self.cur["start_s"], adopt=True, rec=rec)

    def _track_change(self, now_s: float, via: str, at_s: float, *, adopt: bool, rec: Optional[dict] = None) -> None:
        cur = self.cur
        self.track_id += 1
        self.track_via = via
        self.track_start = at_s
        self._track_pending = True
        self._gap = None
        self._cancel_pred()
        if self.armed is not None:
            self._disarm(now_s, "disarmed")              # a new song: the old build's line is off
        keep = cur if adopt else None
        boundaries = self.boundaries
        self._reset_track_state()
        self.boundaries = boundaries
        self._emit("track_change", at_s, now_s, track_id=self.track_id, via=via)
        if keep is not None:
            keep.update(letter=None, repeat_of=None, identified=False, expected_bars=None, provisional=False,
                        predicted_done=False, kind="track")
            self.sections = [keep]
            self.cur = keep
            sid = keep["id"]
        else:
            self.cur = None
            sec = self._start_section(at_s, "track", now_s, emit=True, detail="track")
            sid = sec["id"]
        r = rec or {"t_s": round(at_s, 3), "novelty": None, "snapped": round(at_s, 3), "kind": "track",
                    "missed": False}
        r["section_id"] = sid
        r["via"] = via
        self.boundaries.append(r)
        log.info("structure: track change %d via %s%s", self.track_id, via, f" ({r['why']})" if r.get("why") else "")

    # -- events ----------------------------------------------------------------------------------------------------------
    def _emit(self, kind: str, at_s: float, now_s: float, **extra: Any) -> dict:
        grid = self._grid
        self._ev_seq += 1
        ev: dict = {"kind": kind, "at_ms": round(at_s * 1000.0, 1), "t_ms": round(now_s * 1000.0, 1),
                    "seq": self._ev_seq}
        if grid.has_grid:
            ev["beat"] = round(grid.beat_at(at_s), 3)
            if grid.ok:
                ev["bar"] = grid.bar_at(at_s + 1e-6)
                ev["phrase"] = math.floor(ev["bar"] / grid.pb)
        ev.update(extra)
        self.event_counts[kind] += 1
        try:
            self.ctx.publish({"type": contract.ROOM_EVENT, "data": ev})
        except Exception:
            pass
        for cb in list(self.ctx.on_music_event or []):
            try:
                cb(dict(ev))
            except Exception:
                self.callback_errors += 1
                log.debug("an on_music_event callback failed", exc_info=True)
        return ev

    # -- GOBO (§9.5) -------------------------------------------------------------------------------------------------------
    def _gobo_at(self, at_s: float, key: tuple) -> None:
        if not self.cfg["gobo_on_sections"] or self.ctx.section_change is None:
            return
        self._gobo_cancel(key)
        self.gobo["scheduled"] += 1
        loop = self.ctx.loop
        if loop is not None and hasattr(loop, "call_at"):
            try:
                self._gobo_handles[key] = loop.call_at(at_s, self._gobo_fire, key)
                return
            except Exception:
                pass
        self._gobo_queue.append((at_s, key))

    def _gobo_cancel(self, key: tuple) -> None:
        h = self._gobo_handles.pop(key, None)
        if h is not None:
            try:
                h.cancel()
            except Exception:
                pass
        self._gobo_queue = [q for q in self._gobo_queue if q[1] != key]

    def _run_gobo_queue(self, now_s: float) -> None:
        if not self._gobo_queue:
            return
        due = [q for q in self._gobo_queue if q[0] <= now_s]
        self._gobo_queue = [q for q in self._gobo_queue if q[0] > now_s]
        for _, key in sorted(due):
            self._gobo_fire(key)

    def _gobo_fire(self, key: tuple) -> None:
        try:
            self._gobo_handles.pop(key, None)
            if not self.cfg["gobo_on_sections"]:
                return
            if _call(self.ctx.vibe) is not None:
                # a vibe plays: the director owns GOBO, stepped when it actually lands a look, so the vibe's
                # change_looks, Keep this look, relaxing, the late rule and peak tokens all hold the screens too
                self.gobo["director"] += 1
                return
            now = self._mono()
            room = _call(self.ctx.room)
            screens = room.get("screens") if isinstance(room, dict) else None
            if isinstance(screens, dict) and any(isinstance(s, dict) and s.get("cutting") for s in screens.values()):
                self.gobo["skipped_cut"] += 1
                return
            grid = self._grid
            min_gap = grid.bar_s if grid.has_grid else NO_GRID_BAR_S
            last = self.gobo.get("last_s")
            if last is not None and now - last < min_gap - 0.05:
                self.gobo["skipped_bar"] += 1
                return
            fn = self.ctx.section_change
            if fn is None:
                return
            fn()
            self.gobo["steps"] += 1
            self.gobo["last_s"] = now
        except Exception:
            log.debug("structure: GOBO step failed", exc_info=True)

    # -- music.context -------------------------------------------------------------------------------------------------------
    def _ced_mean(self, view: Any, now_s: float) -> Optional[dict]:
        if not self._ml_ok(view):
            return None
        rec = [c for c in (view.get("ced_recent") or []) if isinstance(c, dict) and _f(c.get("t")) is not None]
        if not rec:
            return None
        newest = max(float(c["t"]) for c in rec)
        if now_s - newest > self._stale(view, "ced"):
            return None
        sel = [c for c in rec if float(c["t"]) > newest - self.cfg["ced_mean_s"] + 1e-6]
        return {w: round(float(np.mean([_f(c.get(f"{w}_p"), 0.0) for c in sel])), 3) for w in contract.CED_GROUPS}

    def _key_wanted(self, view: Any, now_s: float) -> dict:
        """The key the sources give now: the piano's (music.harmony from midi) while it plays, else the worker's at
        key_min_strength (key_hold.strength_margin less while it is the one shown: a sticky source), else
        music.harmony's from the audio."""
        h = _call(self.ctx.harmony)
        if isinstance(h, dict) and h.get("source") == "midi" and h.get("key"):
            return {"key": h["key"], "mode": h.get("mode"), "key_strength": round(_f(h.get("conf"), 0.0), 3),
                    "key_via": "midi"}
        if self._ml_ok(view) and isinstance(view.get("key"), dict):
            k = view["key"]
            t = _f(k.get("t"))
            stale = max(self._stale(view, "key"), 2.0 * self.mlcfg["key_every_s"])
            st = _f(k.get("strength"), 0.0) or 0.0
            need = self.cfg["key_min_strength"] - (self.cfg["key_hold"]["strength_margin"]
                                                   if self._key_shown.get("key_via") == "ml" else 0.0)
            if k.get("key") and t is not None and now_s - t <= stale and st >= need - 1e-9:
                return {"key": k["key"], "mode": k.get("mode"), "key_strength": round(st, 3), "key_via": "ml"}
        if isinstance(h, dict) and h.get("key"):
            return {"key": h["key"], "mode": h.get("mode"), "key_strength": round(_f(h.get("conf"), 0.0), 3),
                    "key_via": "audio"}
        return {"key": None, "mode": None, "key_strength": 0.0, "key_via": None}

    def _key(self, view: Any, now_s: float) -> dict:
        """music.context's key, with hysteresis (README S4.7): the same key from any source shows at once; another
        key (or none) shows once it has held for key_hold.hold_s; the piano's shows at once (it is the truth while it
        plays), and so does the first key after none."""
        want = self._key_wanted(view, now_s)
        shown = self._key_shown
        km = (want["key"], want["mode"])
        if km == (shown["key"], shown["mode"]):
            self._key_shown, self._key_cand = want, None
        elif want["key_via"] == "midi" or shown["key"] is None:
            self._key_shown, self._key_cand = want, None
        elif self._key_cand is not None and self._key_cand[0] == km:
            if now_s - self._key_cand[1] >= self.cfg["key_hold"]["hold_s"] - 1e-9:
                self._key_shown, self._key_cand = want, None
        else:
            self._key_cand = (km, now_s)
        return dict(self._key_shown)

    def _context_second(self, now_s: float, grid: _Grid) -> None:
        view = _call(self.ctx.ml)
        ml_ok = self._ml_ok(view)
        vstream = view.get("stream") if ml_ok and isinstance(view.get("stream"), str) else None
        if vstream is not None and vstream != self._tags_stream:
            if self._tags_stream is not None:            # another stream measured: its genre, numbers and key
                log.info("structure: ML stream %s -> %s: genre and tag history cleared", self._tags_stream, vstream)
                self._reset_ml_state()
            self._tags_stream = vstream
        ced = self._ced_mean(view, now_s)
        beliefs = _call(self.ctx.beliefs)
        tags = view.get("tags") if ml_ok and isinstance(view.get("tags"), dict) else None
        tt = _f(tags.get("t")) if tags else None
        fresh = tt is not None and now_s - tt <= self._stale(view, "tags")
        new = fresh and (self._tags_t is None or tt > self._tags_t)
        music = ced.get("music") if ced else None
        frozen = music is not None and music < self.gcfg["freeze_below_music"]
        if new:
            dt = (tt - self._tags_t) if self._tags_t is not None else None
            self._tags_t = tt
            self._tags_seen_s = now_s
            gp = tags.get("genre_parents") if isinstance(tags.get("genre_parents"), dict) else None
            if gp and not frozen:                        # the track test's recent parents: music only
                self._parents.append((now_s, {str(k): float(v) for k, v in gp.items() if _f(v) is not None}))
            if not frozen:
                a = 1.0 if dt is None else 1.0 - math.exp(-max(0.0, dt) / self.cfg["tags_tau_s"])
                for out, src in TAG_FIELDS:
                    x = _f(tags.get(src))
                    if x is None:
                        continue
                    prev = self._tag_ema.get(out)
                    self._tag_ema[out] = x if prev is None else prev + (x - prev) * a
                self._instruments_update(tags.get("instruments_top5"), a)
            self.genre = genre_reading(gp, beliefs, ced, self.gcfg, self._gstate, now_s=now_s)
        elif not fresh:
            self.genre = genre_reading(None, beliefs, ced, self.gcfg, self._gstate, now_s=now_s)
        if self._tags_seen_s is not None and now_s - self._tags_seen_s > self.cfg["tags_hold_s"]:
            self._tag_ema = {k: None for k, _ in TAG_FIELDS}
            self._inst_ema = {}
            self._tags_seen_s = None
            self._tags_t = None
        key = self._key(view, now_s)
        self._ctx_parts["key"] = key
        moods: list[str] = []
        instr: list[str] = []
        lc = self.cfg["listener_conf"]
        if isinstance(beliefs, dict):
            for m in beliefs.get("moods") or []:
                if isinstance(m, dict) and isinstance(m.get("word"), str) and (_f(m.get("conf"), 0.0) or 0.0) >= lc:
                    moods.append(m["word"])
        if self._inst_ema:                               # the ML's, smoothed (held while the tags are recent)
            instr = [k for k, _ in sorted(self._inst_ema.items(), key=lambda kv: (-kv[1], kv[0]))[:INSTR_TOP]]
        elif isinstance(beliefs, dict) and isinstance(beliefs.get("instruments"), list):
            instr = [str(x) for x in beliefs["instruments"] if isinstance(x, str)]
        sits = _call(self.ctx.situations)
        tk = sits.get("talking") if isinstance(sits, dict) else None
        talking = tk.get("value") if isinstance(tk, dict) and isinstance(tk.get("value"), bool) else None
        ages = {}
        for task in contract.ML_TASKS:
            res = view.get(task) if ml_ok else None
            t = _f(res.get("t")) if isinstance(res, dict) else None
            if task == "beats" and isinstance(res, dict):
                t = _f(res.get("window_t1"))
            ages[task] = round(now_s - t, 1) if t is not None else None
        mlpart = {"state": view.get("state") if isinstance(view, dict) else None,
                  "styles_top3": list(tags.get("styles_top3") or []) if fresh else [],
                  "moods_top5": list(tags.get("moods_top5") or []) if fresh else [],
                  "instruments_top5": list(tags.get("instruments_top5") or []) if fresh else [],
                  "ages": ages}
        self._ctx_parts.update(
            stream=self.vec_stream if self.source == "ml" and self.vec_stream else self.stream,
            genre=dict(self.genre), moods=moods, instruments=instr, talking=talking, ced=ced, ml=mlpart,
            tags={k: _r(v, 3) for k, v in self._tag_ema.items()}, beliefs=beliefs)
        body = self._build_context(now_s, grid)
        self._track_pending = False
        self._context = body
        try:
            self.ctx.publish({"type": contract.MUSIC_CONTEXT, "data": body})
        except Exception:
            pass
        self._build_snapshot(now_s)

    def _instruments_update(self, top: Any, a: float) -> None:
        """The instruments' scores, smoothed like the tag numbers (EMA tags_tau_s, frozen with them): each tags line's
        top 5 in, every other label decaying toward 0, so one window's ranking never flips the list."""
        seen: dict[str, float] = {}
        for x in top if isinstance(top, list) else []:
            if isinstance(x, (list, tuple)) and len(x) >= 2 and isinstance(x[0], str) and _f(x[1]) is not None:
                seen[x[0]] = _c01(_f(x[1]))
        if not self._inst_ema:
            self._inst_ema = dict(seen)
            return
        for lab in set(self._inst_ema) | set(seen):
            prev = self._inst_ema.get(lab, 0.0)
            v = prev + (seen.get(lab, 0.0) - prev) * a
            if v < INSTR_KEEP:
                self._inst_ema.pop(lab, None)
            else:
                self._inst_ema[lab] = v

    def _section_info(self, now_s: float, grid: _Grid) -> contract.SectionInfo:
        cur = self.cur or {"id": 0, "letter": None, "repeat_of": None, "start_s": now_s, "expected_bars": None}
        bars_in = None
        if grid.ok:
            bars_in = max(0, grid.bar_at(now_s) - grid.bar_at(cur["start_s"] + 1e-3))
        return {"id": cur["id"], "letter": cur.get("letter"), "repeat_of": cur.get("repeat_of"),
                "novelty": round(self._nov_now, 4), "phase": self.phase,
                "build_progress": round(self.build_progress, 3) if self.building else 0.0,
                "drop_eta_beats": _r(self.drop_eta, 2) if self.building else None,
                "bars_in_section": bars_in, "started_ms": round(cur["start_s"] * 1000.0, 1),
                "expected_bars": cur.get("expected_bars"),
                "chorus": bool(cur.get("letter") and cur.get("letter") == self.chorus),
                "label": self._label(now_s, cur)}

    def _label(self, now_s: float, cur: dict) -> Optional[str]:
        b = self._ctx_parts.get("beliefs")
        if not isinstance(b, dict) or b.get("state") not in ("fresh", "holding"):
            return None
        s = b.get("section") if isinstance(b.get("section"), dict) else None
        age = _f(b.get("age_s"))
        if not s or age is None or now_s - age < cur["start_s"]:
            return None
        lab = s.get("label")
        if not isinstance(lab, str) or lab not in contract.SECTION_LABELS or lab == "unknown":
            return None
        return lab if (_f(s.get("conf"), 0.0) or 0.0) >= self.cfg["listener_conf"] else None

    def _build_context(self, now_s: float, grid: _Grid) -> contract.MusicContext:
        p = self._ctx_parts
        key = p.get("key") or {"key": None, "mode": None, "key_strength": 0.0, "key_via": None}
        tags = p.get("tags") or {}
        return {"t_ms": round(now_s * 1000.0, 1), "stream": p.get("stream"),
                "source": self.source_now if self.source_now in ("ml", "dsp") else "none",
                "genre": p.get("genre") or dict(self.genre), "moods": list(p.get("moods") or []),
                "instruments": list(p.get("instruments") or []),
                "vocal": tags.get("vocal"), "dance": tags.get("dance"), "arousal": tags.get("arousal"),
                "engagement": tags.get("engagement"), "bright": tags.get("bright"), "acoustic": tags.get("acoustic"),
                "key": key["key"], "mode": key["mode"], "key_strength": key["key_strength"],
                "key_via": key["key_via"], "talking": p.get("talking"), "ced": p.get("ced"),
                "section": self._section_info(now_s, grid),
                "track": {"id": self.track_id, "changed": bool(self._track_pending),
                          "age_s": _r(now_s - self.track_start, 1) if self.track_start is not None else None,
                          "via": self.track_via},
                "ml": p.get("ml") or {"state": None, "styles_top3": [], "moods_top5": [], "instruments_top5": [],
                                      "ages": {t: None for t in contract.ML_TASKS}}}

    def _refresh_context(self, now_s: float, grid: _Grid) -> None:
        """10 Hz: the section and track parts of the last body (only the 1 Hz body is published)."""
        last = self._context
        if last is None:
            return
        sec = self._section_info(now_s, grid)
        trk = {"id": self.track_id, "changed": bool(self._track_pending) or last["track"]["changed"],
               "age_s": _r(now_s - self.track_start, 1) if self.track_start is not None else None,
               "via": self.track_via}
        if sec != last["section"] or trk["id"] != last["track"]["id"]:
            body = dict(last)
            body["section"] = sec
            body["track"] = trk
            self._context = body

    # -- the snapshot -------------------------------------------------------------------------------------------------------
    def _build_snapshot(self, now_s: float) -> None:
        def sec_view(s: dict) -> dict:
            return {"id": s["id"], "letter": s.get("letter"), "repeat_of": s.get("repeat_of"),
                    "start_s": round(s["start_s"], 3), "end_s": _r(s.get("end_s"), 3), "bars": s.get("bars"),
                    "expected_bars": s.get("expected_bars"), "kind": s["kind"], "energy": _r(s.get("energy"), 3),
                    "similarity": s.get("similarity"), "chorus": bool(s.get("letter") and s["letter"] == self.chorus),
                    "dropped": bool(s.get("dropped")), "n_vectors": s.get("nvec", 0)}

        nv_keep = [[round(t, 1), round(v, 4)] for t, v in self.nov if t >= now_s - NOVELTY_KEEP_S]
        self._snap = {
            "context": self._context, "source": self.source_now, "space": self.source, "stream": self.vec_stream,
            "sections": [sec_view(s) for s in self.sections],
            "novelty": nv_keep, "threshold": round(self._threshold, 4),
            "boundaries": [{k: v for k, v in b.items() if k != "missed_checked"} for b in self.boundaries],
            "build": {"active": self.building, "start_s": _r(self.build_start, 3), "progress": round(
                self.build_progress, 3), "line_s": _r(self.drop_line, 3), "eta_beats": _r(self.drop_eta, 2),
                "armed_s": _r(self.armed["line"], 3) if self.armed else None, "slopes": dict(self.slopes_view),
                "breakdown": self.breakdown, "phase": self.phase},
            "events": dict(self.event_counts), "callback_errors": self.callback_errors,
            "gobo": {k: v for k, v in self.gobo.items() if k != "last_s"},
            "track": {"id": self.track_id, "via": self.track_via},
            "pending": {"predicted_s": _r(self._pred["line"], 3) if self._pred else None,
                        "gap_check": self._pending_gap is not None},
            "settings_problems": list(self.settings_problems),
            "stats": {"tick_ms_p95": _p95(self._tick_ms), "second_ms_p95": _p95(self._second_ms)}}


def attach(ctx: contract.BrainContext, **kw: Any) -> Structure:
    """The brain's ``structure`` (README S4.7). Keyword args go to Structure (tests: ``mono``, a fake clock)."""
    return Structure(ctx, **kw)
