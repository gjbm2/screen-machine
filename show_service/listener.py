"""listener.py: the LLM listener (docs/SHOW_ENGINE.md §7) and Describe it's helper (§7.4); show_service/README.md,
"Stage 3 > S3.12".

A Stage-3 component ("listener", 1 Hz on show-brain). It never runs on show-rx: it reads the audio from ring.Rings
(written there) and the DSP's latest values by reference, and does its work in asyncio tasks on show-brain's loop.

* **Each tick:** sample the DSP (features, clock, harmony) into a 120 s timeline; age the beliefs; decide whether a
  clip may go (``why``: the first failing reason, in page words); when one is due, start a call (up to
  ``max_in_flight`` in flight).
* **Gating** (§7.3, §7.5), in order, for every stream (test streams too): the AI listening switch ("AI
  listening"; persisted; default off: nothing is uploaded until the owner turns it on); the mode's permission (owner,
  20 Oct: "it's intrusive and expensive to have it running by default if e.g. people are just in the room talking
  normally": room_service's per-mode device ``show.ai`` PUTs ``{"mode": bool}``, as ``show.mic`` claims the mic;
  persisted; default off, so only the modes whose row turns it on (the live ones) listen; ``requires_mode``; "AI
  listening is off in <the mode's label>"); Auto on and the room's mic on
  (owner, 17 Oct; ``requires_auto`` / ``requires_mic``; unknown = off); the vibe's own ``listener`` (a feel can say
  off); sound coming in (fresh features, audio in the ring); the music gate (``classifier``: CED from the ML worker,
  below; ``dsp``: a beat or a key over ``situations.music.dsp_min_gate``), skipped only for a stream whose show.yaml
  sender says ``room_sound: false`` (a music file: no one's speech in it); gate >= ``quiet_gate`` over the last 10 s; a
  usable model in config/llm.yaml. The mode's show target no longer gates (owner, 17 Oct: its ``listening`` stays in
  the API for readers; the permission above is a separate, explicit per-mode row). The ring records only while the
  switch, the mode's permission, Auto, the mic and the vibe allow a clip (``Rings.armed``).
* **The CED gate** (Stage 4, README S4.8; ``music_gate: classifier``, room-sound streams only): no fresh CED reading
  for the stream (``ctx.ml``; MLView.stale_s, at least ``ml.results_stale_s``): "the music/speech check isn't
  running"; the latest reading's speech >= ``listener.ced.speech_max``: "people are talking" (speech is the max over
  ``ced.speech_words``: speech, conversation, crowd); else the clip is the longest contiguous run of seconds in the
  last ``clip_s`` whose readings say music >= ``ced.music_min`` and speech < ``ced.speech_max`` (a run starts
  ``ced.speaks_s`` before its first reading, consecutive passing readings join while at most the CED's stale time apart,
  so a worker slowed to a reading every 4-8 s still makes one run, and the run's last second also needs the next
  reading, which covers it too, to have passed; the latest run wins a tie), cut with ``rings.clip``; a run shorter than ``min_clip_s`` is
  skipped: "not enough music without talking".
* **Sections** (Stage 4, §7.3): structure's events (``on_music_event``) and music.context keep a short section history.
  A confirmed ``section_change`` makes one extra call due at its boundary + ``section_call_after_s`` (still
  ``min_gap_s`` after the last call, one in flight, and slowed with the cadence: at least half the current interval
  after the last call, at most one section call per interval), whose clip ends at that time; every call's text
  carries the segment map (the sections the clip spans), and ``InsightData.segment_id`` is the section at the clip's
  end. A ``track_change`` lets the next verdict's genre win at once (the beliefs' track-change rule).
* **The mic on demand** (README S5.16): a stream the mic keeps off (the ring's ``off``, receiver health's ``off``, or
  state "off") never gives a clip ("the mic is off"); the ring forgets what it held of it; and a clip cut before a
  stream went off (the ring's ``off_gen`` moved while it was encoded) is never uploaded.
* **Cadence** (README S5.22; owner 18 Oct): a ``clip_s`` (10 s) clip every ``cadence_s`` (15 s) while the music is
  steady, every ``fast_cadence_s`` (5 s) while something develops (``_developing``: structure's build or outro, a
  forecast window open or opening within ``fast_lookahead_s``, an armed song end within ``fast_end_s``, a confirmed
  section boundary in the last ``fast_after_section_s``); call starts at least ``min_gap_s`` apart, up to
  ``max_in_flight`` (2) in flight; x3 over the daily budget (never stopped); after failures in a row, x2 per failure
  up to ERROR_BACKOFF_MAX_S (no retries: the next window is the retry).
* **One call:** ``rings.latest(stream, clip_s)`` -> normalised to -16 LUFS (pyebur128, gain clamped to +-24 dB, in a
  worker thread, in short chunks) -> ffmpeg MP3 at llm.yaml's rate and bitrate (asyncio, 5 s), else WAV ->
  ``utils.llm.acall_json("listen", ..., lenient=True)`` with a static system prompt, the DSP timeline and (except
  every 5th call) the previous verdict as a hypothesis -> checked against vocab.yaml -> ``music.insight`` on the bus,
  fused into the beliefs, logged (memory and LISTENER_LOG). The tool's words (content, vocals, section, trajectory)
  are plain strings and ``lenient`` keeps an answer whose only misses are soft (a word, a range, a count):
  ``to_insight`` cleans them, so a usable answer is never sent again to the fallback at a second charge.
* **Which model, what it costs** (README S5.20): llm.yaml's listen list (Gemini Flash first, gpt-audio-1.5 the
  fallback; a provider without its key is skipped). The warm-up thread runs ``utils.llm.probe("listen")``, so a
  discovered id is known before the first call; the snapshot's ``llm`` shows what each entry resolved to. The view's
  ``estimate`` (``utils.llm.estimate``: the ledger's recent mean for the model that would be used, else llm.yaml's
  ``typical`` x prices) gives per hour and per evening (``evening_h`` x 3600 / ``cadence_s`` calls), shown even
  while the switch is off.
* **The answer** (README S5.22): content, character (<= 12 words), genre, mood, approach (intensity, change,
  transitions: the director's style of approach), trajectory, ending (cold | fade | segue | unknown), coming (<= 2
  forecasts: kind, window from the clip's end, signs, size), track_change, why (<= 12 words). Compact: output tokens
  dominate the cost.
* **Beliefs** (§7.1): votes decay (half-lives genre 120 s, mood and the approach's words and intensity 45 s); the
  genre word changes only when a new leader leads two reads in a row by LEADER_MARGIN of the vote weight, or at once
  on a track change; confidence comes from agreement over time, not the model's own number. character and coming are
  the newest clip's (coming on the acoustic clock: the clip's end + the window, gone once passed); ending holds
  ENDING_HOLD_S. After the last verdict: fresh, holding to HOLD_S, fading to neutral over FADE_S, then none.
* **Describe it:** ``describe(text, context)`` asks the LLM (task "describe") within ``describe_timeout_s``; anything
  else (no model, a timeout, a bad answer) falls back to a local keyword match. It never raises.

Nothing here branches on a mode, vibe, device or stream name: whether a stream carries the room's sound is the
show.yaml sender's ``room_sound`` flag, never its name.
"""

from __future__ import annotations

import asyncio
import collections
import io
import json
import logging
import math
import os
import re
import shutil
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from . import contract

log = logging.getLogger("show_service.listener")

LISTENER_LOG = contract.LISTENER_LOG    # every verdict and failure (genre.detail grows the vocab)
LOG_ROTATE_BYTES = 1_000_000

# Design defaults (§7.3; starting points), used for any key show.yaml's ``listener`` leaves out. quiet_gate: the
# loudness gate's mean over QUIET_WINDOW_S must reach this; min_clip_s: the ring must hold min(clip_s, this) before a
# call (it records only while a clip could go, so a first clip waits this long).
DEFAULTS: dict[str, Any] = {"cadence_s": 15.0, "clip_s": 10.0, "min_gap_s": 4.0,
                            # README S5.22 (owner, 18 Oct: "24 s every 20 s is too long a round trip"): a 10 s clip
                            # every 15 s while the music is steady, every fast_cadence_s while something develops
                            # (structure's build or outro, a forecast window open or opening within
                            # fast_lookahead_s, a predicted song end within fast_end_s, a section boundary in the
                            # last fast_after_section_s), with up to max_in_flight calls at once, starts at least
                            # min_gap_s apart
                            "fast_cadence_s": 5.0, "max_in_flight": 2, "fast_lookahead_s": 20.0, "fast_end_s": 30.0,
                            "fast_after_section_s": 10.0,
                            "music_gate": "classifier", "describe_timeout_s": 8.0,
                            "quiet_gate": 0.1, "min_clip_s": 6.0, "section_call_after_s": 8.0,
                            # owner, 17 Oct: nothing goes to any provider unless Auto is on and the room's mic is
                            # on (cost, and it's intrusive); unknown reads as off. show.yaml listener.requires_auto /
                            # requires_mic
                            "requires_auto": True, "requires_mic": True,
                            # owner, 20 Oct: and nothing unless the mode allows it (room_service's show.ai: PUT
                            # /api/v1/listening {"mode": bool}); never set reads as off. show.yaml
                            # listener.requires_mode
                            "requires_mode": True,
                            # README S5.20: the evening the Music card's estimate covers (hours; starting point);
                            # S5.22: the share of the time at the fast cadence the estimate assumes until it has
                            # measured an hour of its own listening
                            "evening_h": 4.0, "est_fast_share": 0.5}
INT_KEYS = ("max_in_flight",)
SHARE_KEYS = ("est_fast_share",)
FAST_SHARE_MIN_S = 3600.0     # the estimate's fast share is measured once an hour of effective listening is behind it
# Stage 4 (README S4.8, §7.5): the CED gate's thresholds (show.yaml listener.ced; starting points, the same numbers)
CED_DEFAULTS: dict[str, float] = {"music_min": 0.5, "speech_max": 0.3}
RESULTS_STALE_S = 3.0         # show.yaml ml.results_stale_s when it is left out (starting point): an older CED reading
                              # is not "now"
CED_SPEAKS_S = 1.0            # show.yaml listener.ced.speaks_s (README S4.8): each CED reading vouches for the last
                              # second of its window
CONTEXT_STALE_S = 3.0         # music.context comes at 1 Hz
SECTIONS_KEEP = 64            # the section history (ids, letters, starts) for segment maps
MUSIC_GATES = ("classifier", "dsp")
DSP_MIN_GATE = 0.3            # show.yaml situations.music.dsp_min_gate when it is left out (starting point)
HARMONY_MIN_CONF = 0.3        # "or a clear key" for the dsp music gate (README S3.12)
QUIET_WINDOW_S = 10.0
FEATURES_STALE_S = 5.0
CONFIDENT = 0.6
BUDGET_SLOWDOWN = 3.0
ERROR_BACKOFF_MAX_S = 300.0
PREV_EVERY = 5                # the previous verdict is left out of every 5th call
TIMELINE_S = 120.0
TIMELINE_STEP_S = 2.0
LUFS_TARGET = -16.0
GAIN_CLAMP_DB = 24.0
LUFS_CHUNK = 8000             # pyebur128 in 0.5 s pieces: the worker thread never holds the GIL long
ENCODE_TIMEOUT_S = 5.0
ENCODE_NICE = 10
LOG_N = 200

# beliefs (§7.1; README S5.22)
HALF_LIFE_S = {"genre": 120.0, "mood": 45.0, "change": 45.0, "transitions": 45.0, "intensity": 45.0}
#: a music_moods word's visual moods, first to last: the conf each takes (the list's order is 04's ranking)
MOOD_RANK_WEIGHTS = (1.0, 0.85, 0.7, 0.6)
LEADER_MARGIN = 0.15
REF_WEIGHT = 2.0              # vote weight that reads as full confidence (two agreeing reads)
FRESH_S = 30.0
HOLD_S = 60.0
FADE_S = 60.0
ENDING_HOLD_S = 60.0          # README S5.22: how the piece will end holds this long after the verdict that said it
INTENSITY_SPREAD = 0.25       # approach.intensity: reads this far apart (standard deviation) agree on nothing
COMING_MAX_ETA_S = 120.0      # a forecast further out than this is cut to it
CHARACTER_WORDS = 12
WHY_WORDS = 12
MUSICAL = ("music", "mixed")

WHY_OFF = "AI listening is off"
WHY_VIBE = "this feel doesn't listen"
WHY_NO_CED = "the music/speech check isn't running"        # Stage 4: no fresh CED reading for the stream
WHY_P1 = WHY_NO_CED                                         # the Stage-3 name, kept for readers of it
WHY_TALKING = "people are talking"
WHY_SHORT = "not enough music without talking"
WHY_NO_MUSIC = "no music heard"
WHY_QUIET = "too quiet"
WHY_NO_SOUND = "no sound coming in"
WHY_NO_MODEL = "no model configured"
WHY_MIC_OFF = "the mic is off"                              # the mic on demand (README S5.16)
WHY_MIC_WENT_OFF = "the mic went off"                       # a clip cut before that is never uploaded
WHY_AUTO_OFF = "Auto is off"                                  # owner, 17 Oct: no uploads unless Auto is on
WHY_MODE_OFF = "AI listening is off in {mode}"                # owner, 20 Oct: the mode's permission (show.ai) is off
THIS_MODE = "this mode"                                       # {mode} when the room doesn't say its label (as mic.py)


def why_mode(label: Optional[str]) -> str:
    """WHY_MODE_OFF in page words: "AI listening is off in Normal", else "... in this mode"."""
    return WHY_MODE_OFF.format(mode=label.strip() if isinstance(label, str) and label.strip() else THIS_MODE)


def _one_of(words: Any) -> str:
    return "One of: " + ", ".join(words) + "."


# The words are plain strings (README S5.20): an enum fails the whole answer on one stray word ("sustaining" as a
# section) and the same clip then goes to the fallback at a second charge. to_insight maps every word to its list.
# README S5.22 (owner, 18 Oct): compact, because output tokens (and thinking) dominate the cost. The answer describes
# the music (content, character, genre, mood), recommends the director's approach, says where it is heading
# (trajectory), how it will end, and what is coming (the cue engine arms those). No instrumentation, vocals, section,
# same-track or evidence: the local ML worker gives instruments and vocals, and nothing used the rest.
_CONF = {"type": "number", "minimum": 0, "maximum": 1}
LISTEN_TOOL: dict = {
    "name": "report_music",
    "description": "Report what the clip sounds like and what is coming, for the room's lighting and visuals "
                   "director.",
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": _one_of(contract.INSIGHT_CONTENT)},
            "character": {"type": "string", "description": "At most 12 words."},
            "genre": {"type": "object",
                      "properties": {"label": {"type": "string"}, "detail": {"type": "string"}, "confidence": _CONF},
                      "required": ["label", "detail", "confidence"]},
            "mood": {"type": "object",
                     "properties": {"words": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                                    "confidence": _CONF},
                     "required": ["words", "confidence"]},
            "approach": {"type": "object",
                         "properties": {"intensity": _CONF,
                                        "change": {"type": "string",
                                                   "description": _one_of(contract.APPROACH_CHANGE)},
                                        "transitions": {"type": "string",
                                                        "description": _one_of(contract.APPROACH_TRANSITIONS)},
                                        "confidence": _CONF},
                         "required": ["intensity", "change", "transitions", "confidence"]},
            "trajectory": {"type": "object",
                           "properties": {"state": {"type": "string",
                                                    "description": _one_of(contract.TRAJECTORY_STATES)},
                                          "confidence": _CONF},
                           "required": ["state", "confidence"]},
            "ending": {"type": "string", "description": _one_of(contract.ENDINGS)},
            "coming": {"type": "array", "maxItems": 2,
                       "items": {"type": "object",
                                 "properties": {"kind": {"type": "string",
                                                         "description": _one_of(contract.COMING_KINDS)},
                                                "eta_min_s": {"type": "number"}, "eta_max_s": {"type": "number"},
                                                "signs": {"type": "array", "items": {"type": "string"},
                                                          "maxItems": 4},
                                                "size": {"type": "string",
                                                         "description": _one_of(contract.COMING_SIZES)},
                                                "confidence": _CONF},
                                 "required": ["kind", "eta_min_s", "eta_max_s", "signs", "size", "confidence"]}},
            "track_change": {"type": "boolean"},
            "why": {"type": "string", "description": "At most 12 words."},
        },
        "required": ["content", "character", "genre", "mood", "approach", "trajectory", "ending", "coming",
                     "track_change", "why"],
    },
}

DESCRIBE_TOOL: dict = {
    "name": "set_music_card",
    "description": "Set the room's Music card from the guest's words. Only these settings exist.",
    "parameters": {
        "type": "object",
        "properties": {
            "steps": {"type": "integer", "minimum": -3, "maximum": 3},
            "palette": {"type": ["string", "null"]},
            "moods": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 3},
            "keep": {"type": "boolean"},
            "switch_mode": {"type": ["string", "null"]},
            "reply": {"type": "string"},
        },
        "required": ["steps", "palette", "moods", "keep", "switch_mode", "reply"],
    },
}

# Describe it's keyword fallback (§7.4; README S3.12). Plain English step words only: no word that is (or could be)
# a mode or vibe name, so naming a mode ("party") offers the switch and never also steps. At run time any word that
# names a mode in the context is left out of the steps as well.
CALMER_WORDS = frozenset("calm calmer calming quiet quieter soft softer gentle gentler chilled relax relaxed "
                         "relaxing mellow mellower dim dimmer slow slower peaceful sleepy cosy cozy subtle".split())
WILDER_WORDS = frozenset("wild wilder loud louder energy energetic more bright brighter dance dancing lively "
                         "livelier faster hype intense crazy pumping".split())
ART_WORDS = frozenset("art painting paintings picture pictures artwork".split())
KEEP_WORDS = frozenset("keep stay hold".split())
MAX_STEPS = 3
NOT_CAUGHT = "I didn't catch that: try a mood like 'mellow' or a palette like 'ember'."


def no_room_sound_streams(settings: dict) -> tuple[set[str], list[str]]:
    """show.yaml ``senders.<name>.room_sound: false`` -> (that sender's streams, problems). Only those streams skip the
    music/speech check; every other stream (unknown ones, or a sender that doesn't say) is the room's own sound."""
    out: set[str] = set()
    problems: list[str] = []
    senders = (settings or {}).get("senders")
    if not isinstance(senders, dict):
        return out, problems
    for name, spec in senders.items():
        if not isinstance(spec, dict) or "room_sound" not in spec:
            continue
        v = spec.get("room_sound")
        if not isinstance(v, bool):
            problems.append(f"senders.{name}.room_sound: {v!r} is not true/false: treated as room sound")
            continue
        if not v:
            streams = spec.get("streams")
            streams = [streams] if isinstance(streams, str) else streams if isinstance(streams, list) else []
            out |= {x for x in streams if isinstance(x, str) and x}
    return out, problems


def _f(v: Any, default: Optional[float] = None) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return default
    return float(v)


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _norm_text(s: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", s.lower()).strip() + " "


# -- vocab (04's vocab.yaml, read-only, mtime-polled) ------------------------------------------------------------------
class Vocab:
    def __init__(self, path: Path = contract.VOCAB_FILE) -> None:
        self.path = Path(path)
        self.moods: list[str] = []
        self.genre: list[str] = []
        self.music_moods: dict[str, list[str]] = {}   # how the music feels -> the visual moods it means (04, 17 Oct)
        self.error: Optional[str] = None
        self._sig: Any = None
        self._poll = -math.inf
        self.revision = 0

    def poll(self, now_s: Optional[float] = None) -> None:
        now = time.monotonic() if now_s is None else now_s
        if now - self._poll < contract.RELOAD_S:
            return
        self._poll = now
        try:
            st = self.path.stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError as e:
            if self._sig != "missing":
                self._sig, self.error = "missing", f"{self.path.name}: {e.strerror}"
            return
        if sig == self._sig:
            return
        self._sig = sig
        try:
            import yaml
            raw = yaml.safe_load(self.path.read_text()) or {}
            self.moods = [str(w) for w in (raw.get("moods") or [])]
            self.genre = [str(w) for w in (raw.get("genre") or [])]
            mm = raw.get("music_moods") if isinstance(raw.get("music_moods"), dict) else {}
            vis = set(self.moods)
            self.music_moods = {str(w): [str(v) for v in vs if str(v) in vis]
                                for w, vs in mm.items() if isinstance(vs, list)}
            self.music_moods = {w: vs for w, vs in self.music_moods.items() if vs}
            self.error = None
            self.revision += 1
        except Exception as e:                    # noqa: BLE001 - keep the last good words
            self.error = f"{self.path.name}: {type(e).__name__}: {e}"
            log.warning("vocab not reloaded (%s): keeping the last good words", self.error)


# -- beliefs (§7.1; README S5.22) ------------------------------------------------------------------------------------
def _t1_s(ins: Optional[dict]) -> float:
    """A verdict's clip end (acoustic monotonic s); -inf when it has none."""
    t = _f(((ins or {}).get("window") or {}).get("t1_ms"))
    return t / 1000.0 if t is not None else -math.inf


class BeliefState:
    """The fused, decaying view of the verdicts. Show-brain only; ``view()`` builds a fresh dict.

    Genre and mood, and the approach's change and transitions words, are votes that decay (HALF_LIFE_S); the genre
    word changes only on two reads in a row by LEADER_MARGIN (or at once on a track change). approach.intensity is a
    decaying weighted mean whose confidence falls with the reads' spread (INTENSITY_SPREAD). character, ending and
    coming are the latest verdict's (ending: the latest word that said something, for ENDING_HOLD_S; coming: each
    window on the acoustic clock, from the clip's end, gone once it has passed). Two calls may be in flight: a verdict
    for an older clip that arrives after a newer one adds its votes but replaces nothing of the newer one's."""

    def __init__(self) -> None:
        self.votes: dict[str, dict[str, float]] = {"genre": {}, "mood": {}, "change": {}, "transitions": {}}
        self.intensity = [0.0, 0.0, 0.0]          # approach.intensity, decayed: (sum w, sum w x, sum w x^2)
        self.t_votes: Optional[float] = None
        self.word: Optional[str] = None
        self.challenger: Optional[str] = None
        self.wins = 0
        self.last: Optional[dict] = None          # the newest clip's InsightData
        self.last_s: Optional[float] = None       # the last verdict's arrival (monotonic s)
        self.track_pending = False                # a DSP track change (structure): the next verdict counts as one
        self.mood_map: dict[str, list[str]] = {}  # vocab.yaml music_moods (the listener sets it from its Vocab)
        self.character: Optional[str] = None
        self.ending: Optional[tuple[str, float]] = None   # (word, the verdict's arrival)

    def clear(self) -> None:
        mm = self.mood_map
        self.__init__()
        self.mood_map = mm

    def track_changed(self) -> None:
        """structure heard a new track (§7.1: "or at once on a DSP track change"): the next verdict's genre wins at
        once, as a verdict that reports track_change itself does."""
        self.track_pending = True

    def _decay(self, now_s: float) -> None:
        if self.t_votes is not None and now_s > self.t_votes:
            dt = now_s - self.t_votes
            for kind, table in self.votes.items():
                f = 0.5 ** (dt / HALF_LIFE_S[kind])
                for w in list(table):
                    table[w] *= f
                    if table[w] < 1e-3:
                        del table[w]
            f = 0.5 ** (dt / HALF_LIFE_S["intensity"])
            self.intensity = [x * f for x in self.intensity]
        self.t_votes = now_s

    def add(self, ins: dict, now_s: float) -> None:
        self._decay(now_s)
        track_change = bool(ins.get("track_change")) or self.track_pending
        self.track_pending = False
        if track_change:
            for table in self.votes.values():
                table.clear()
            self.intensity = [0.0, 0.0, 0.0]
            self.character, self.ending = None, None
        newer = self.last is None or track_change or _t1_s(ins) >= _t1_s(self.last) - 1e-6
        if ins.get("content") in MUSICAL:
            g = (ins.get("genre") or {}).get("label")
            if g and g != "unknown":
                self.votes["genre"][g] = self.votes["genre"].get(g, 0.0) + 1.0
            for w in (ins.get("mood") or {}).get("words") or []:
                self.votes["mood"][w] = self.votes["mood"].get(w, 0.0) + 1.0
            ap = ins.get("approach") or {}
            for kind in ("change", "transitions"):
                w = ap.get(kind)
                if isinstance(w, str) and w:
                    self.votes[kind][w] = self.votes[kind].get(w, 0.0) + 1.0
            x = _f(ap.get("intensity"))
            if x is not None:
                self.intensity = [self.intensity[0] + 1.0, self.intensity[1] + x, self.intensity[2] + x * x]
            if newer:
                if ins.get("character"):
                    self.character = str(ins["character"])
                e = ins.get("ending")
                if isinstance(e, str) and e in contract.ENDINGS and e != "unknown":
                    self.ending = (e, now_s)
        self._pick_genre(track_change)
        if newer:
            self.last = ins
        self.last_s = now_s

    def _pick_genre(self, track_change: bool) -> None:
        votes = self.votes["genre"]
        if not votes:
            if track_change:
                self.word, self.challenger, self.wins = None, None, 0
            return
        leader = max(votes, key=votes.get)
        if track_change or self.word is None:
            self.word, self.challenger, self.wins = leader, None, 0
            return
        if leader == self.word:
            self.challenger, self.wins = None, 0
            return
        total = sum(votes.values())
        margin = (votes[leader] - votes.get(self.word, 0.0)) / total if total > 0 else 0.0
        if margin >= LEADER_MARGIN:
            if self.challenger == leader:
                self.wins += 1
            else:
                self.challenger, self.wins = leader, 1
            if self.wins >= 2:
                self.word, self.challenger, self.wins = leader, None, 0
        else:
            self.challenger, self.wins = None, 0

    @staticmethod
    def _leader(table: dict[str, float], fade: float) -> tuple[Optional[str], float]:
        """The leading word and its confidence: its share of the votes x how much agreement there is (REF_WEIGHT of
        votes reads as full) x the fade, as the genre's."""
        if not table:
            return None, 0.0
        w = max(table, key=table.get)
        total = sum(table.values())
        share = table[w] / total if total > 0 else 0.0
        return w, round(share * min(1.0, total / REF_WEIGHT) * fade, 3)

    def _approach(self, fade: float) -> Optional[dict]:
        change, cc = self._leader(self.votes["change"], fade)
        trans, tc = self._leader(self.votes["transitions"], fade)
        w, wx, wxx = self.intensity
        inten, ic = None, 0.0
        if w > 1e-3:
            mean = wx / w
            spread = math.sqrt(max(0.0, wxx / w - mean * mean))
            inten = round(_clamp(mean, 0.0, 1.0), 3)
            ic = round(min(1.0, w / REF_WEIGHT) * max(0.0, 1.0 - spread / INTENSITY_SPREAD) * fade, 3)
        if inten is None and change is None and trans is None:
            return None
        return {"intensity": inten, "intensity_conf": ic, "change": change, "change_conf": cc,
                "transitions": trans, "transitions_conf": tc, "conf": round(min(ic, cc, tc), 3)}

    def view(self, now_s: float) -> dict:
        none = {"state": "none", "age_s": None, "genre": None, "moods": [], "feel": [], "trajectory": None,
                "content": None, "character": None, "approach": None, "ending": None, "coming": []}
        if self.last_s is None or self.last is None:
            return none
        age = max(0.0, now_s - self.last_s)
        if age >= HOLD_S + FADE_S:
            self.clear()
            return dict(none, age_s=round(age, 1))
        self._decay(now_s)
        fade = 1.0 if age < HOLD_S else max(0.0, 1.0 - (age - HOLD_S) / FADE_S)
        state = "fresh" if age < FRESH_S else "holding" if age < HOLD_S else "fading"
        last = self.last
        musical = last.get("content") in MUSICAL
        genre = None
        gv = self.votes["genre"]
        if self.word is not None:
            total = sum(gv.values())
            share = gv.get(self.word, 0.0) / total if total > 0 else 0.0
            genre = {"word": self.word, "conf": round(share * min(1.0, total / REF_WEIGHT) * fade, 3)}
        feel = [{"word": w, "conf": round(min(1.0, v / REF_WEIGHT) * fade, 3)}
                for w, v in sorted(self.votes["mood"].items(), key=lambda kv: -kv[1])[:3]]
        feel = [m for m in feel if m["conf"] >= 0.05]
        moods = self._visual(feel)
        trajectory = None
        tr = last.get("trajectory") or {}
        if tr.get("state"):
            trajectory = {"state": tr["state"], "conf": round(float(tr.get("confidence") or 0.0) * fade, 3)}
        ending = None
        if self.ending is not None and now_s - self.ending[1] < ENDING_HOLD_S:
            ending = self.ending[0]
        coming = []
        t1 = _t1_s(last)
        if musical and math.isfinite(t1):
            for i, c in enumerate(last.get("coming") or []):
                lo, hi = _f(c.get("eta_min_s")), _f(c.get("eta_max_s"))
                if lo is None or hi is None:
                    continue
                a, b = t1 + lo, t1 + hi
                if b < now_s:
                    continue                      # the window has passed
                coming.append({"id": f"{_f(last.get('t_ms'), 0.0):.0f}:{i}", "kind": c.get("kind"),
                               "t0_s": round(a, 2), "t1_s": round(b, 2),
                               "in_s": [round(max(0.0, a - now_s), 1), round(b - now_s, 1)],
                               "signs": list(c.get("signs") or []), "size": c.get("size"),
                               "conf": round(_clamp(_f(c.get("confidence"), 0.0), 0.0, 1.0), 3)})
        return {"state": state, "age_s": round(age, 1), "genre": genre, "moods": moods, "feel": feel,
                "trajectory": trajectory, "content": last.get("content"),
                "character": self.character if musical else None, "approach": self._approach(fade),
                "ending": ending, "coming": coming}

    def _visual(self, feel: list[dict]) -> list[dict]:
        """The listener's words for how the music feels -> the visual moods scenes carry (vocab.yaml music_moods: each
        word's moods in order, the first counting most; a word that is itself a visual mood stands for itself). Top 3,
        each at the best conf any word gives it."""
        if not self.mood_map:
            return list(feel)
        score: dict[str, float] = {}
        for f in feel:
            vis = self.mood_map.get(f["word"]) or [f["word"]]
            for i, v in enumerate(vis):
                c = f["conf"] * MOOD_RANK_WEIGHTS[min(i, len(MOOD_RANK_WEIGHTS) - 1)]
                score[v] = max(score.get(v, 0.0), c)
        top = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        return [{"word": w, "conf": round(c, 3)} for w, c in top if c >= 0.05]


# -- verdict -> InsightData ----------------------------------------------------------------------------------------------
def word_of(v: Any, allowed: Any, default: Optional[str]) -> Optional[str]:
    """A model's word in one of ``allowed``: case, spaces and hyphens don't matter ("Pre-chorus" -> "pre_chorus",
    "winding down" -> "winding_down"); anything else is ``default``."""
    if not isinstance(v, str):
        return default
    w = re.sub(r"[\s\-]+", "_", v.strip().lower())
    return w if w in allowed else default


def _obj(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _items(v: Any) -> list:
    return v if isinstance(v, list) else []


def _words(v: Any, n: int) -> str:
    return " ".join(str(v or "").split()[:n])[:160]


def _conf(v: Any) -> float:
    return round(_clamp(_f(v, 0.0), 0.0, 1.0), 3)


def _coming(items: Any) -> list[dict]:
    """The forecasts, cleaned: a kind outside COMING_KINDS is a new_section; a missing end takes the other end; the
    window is ordered and cut to 0..COMING_MAX_ETA_S; signs outside COMING_SIGNS are dropped; size defaults medium."""
    out = []
    for c in _items(items)[:2]:
        c = _obj(c)
        lo, hi = _f(c.get("eta_min_s")), _f(c.get("eta_max_s"))
        if lo is None and hi is None:
            continue
        lo = hi if lo is None else lo
        hi = lo if hi is None else hi
        lo, hi = sorted((_clamp(lo, 0.0, COMING_MAX_ETA_S), _clamp(hi, 0.0, COMING_MAX_ETA_S)))
        signs: list[str] = []
        for x in _items(c.get("signs")):
            w = word_of(x, contract.COMING_SIGNS, None)
            if w and w not in signs:
                signs.append(w)
        out.append({"kind": word_of(c.get("kind"), contract.COMING_KINDS, "new_section"),
                    "eta_min_s": round(lo, 1), "eta_max_s": round(hi, 1), "signs": signs[:4],
                    "size": word_of(c.get("size"), contract.COMING_SIZES, "medium"),
                    "confidence": _conf(c.get("confidence"))})
    return sorted(out, key=lambda c: c["eta_min_s"])


def to_insight(data: dict, *, vocab: Vocab, stream: str, model: str, latency_ms: float, window: tuple[float, float],
               t_ms: float) -> dict:
    """The model's tool arguments, checked against vocab.yaml and the fixed vocabularies (contract.InsightData). Any
    word outside its list, a number out of range or too many items is cleaned here (the call is lenient)."""
    genres, moods_ok = set(vocab.genre), set(vocab.music_moods) or set(vocab.moods)
    data = _obj(data)
    g = _obj(data.get("genre"))
    label = str(g.get("label") or "").strip().lower()
    detail = str(g.get("detail") or "").strip()
    if label not in genres:
        if label and label != "unknown" and not detail:
            detail = label
        label = "unknown"
    words: list[str] = []
    for w in _items(_obj(data.get("mood")).get("words")):
        w = str(w).strip().lower()
        if w in moods_ok and w not in words:
            words.append(w)
    ap = _obj(data.get("approach"))
    inten = _f(ap.get("intensity"))
    tr = _obj(data.get("trajectory"))
    return {"t_ms": round(t_ms, 1), "window": {"t0_ms": round(window[0] * 1000.0, 1),
                                              "t1_ms": round(window[1] * 1000.0, 1)},
            "segment_id": None, "stream": stream, "model": model, "latency_ms": round(latency_ms, 1),
            "content": word_of(data.get("content"), contract.INSIGHT_CONTENT, "music"),
            "character": _words(data.get("character"), CHARACTER_WORDS),
            "genre": {"label": label, "detail": detail[:80], "confidence": _conf(g.get("confidence"))},
            "mood": {"words": words[:3], "confidence": _conf(_obj(data.get("mood")).get("confidence"))},
            "approach": {"intensity": round(_clamp(inten, 0.0, 1.0), 3) if inten is not None else None,
                         "change": word_of(ap.get("change"), contract.APPROACH_CHANGE, None),
                         "transitions": word_of(ap.get("transitions"), contract.APPROACH_TRANSITIONS, None),
                         "confidence": _conf(ap.get("confidence"))},
            "trajectory": {"state": word_of(tr.get("state"), contract.TRAJECTORY_STATES, "steady"),
                           "confidence": _conf(tr.get("confidence"))},
            "ending": word_of(data.get("ending"), contract.ENDINGS, "unknown"),
            "coming": _coming(data.get("coming")),
            "track_change": bool(data.get("track_change")),
            "why": _words(data.get("why"), WHY_WORDS)}


# -- audio: loudness and encoding -----------------------------------------------------------------------------------------
def normalise(pcm16: np.ndarray, fs: int, target_lufs: float = LUFS_TARGET) -> tuple[np.ndarray, Optional[float], float]:
    """(int16 normalised to ``target_lufs``, the clip's true integrated LUFS or None, the gain in dB). Worker thread."""
    x = pcm16.astype(np.float64) / 32768.0
    lufs: Optional[float] = None
    try:
        import pyebur128
        st = pyebur128.R128State(1, int(fs), pyebur128.MeasurementMode.MODE_I)
        for i in range(0, x.shape[0], LUFS_CHUNK):
            piece = x[i:i + LUFS_CHUNK]
            st.add_frames(piece, piece.shape[0])
        v = float(pyebur128.get_loudness_global(st))
        lufs = v if math.isfinite(v) and v > -70.0 else None
    except Exception:                             # noqa: BLE001 - no pyebur128: an RMS estimate
        rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
        lufs = 20.0 * math.log10(rms) - 0.691 if rms > 1e-6 else None
    gain_db = _clamp(target_lufs - lufs, -GAIN_CLAMP_DB, GAIN_CLAMP_DB) if lufs is not None else 0.0
    y = x * (10.0 ** (gain_db / 20.0))
    np.clip(y, -1.0, 32767.0 / 32768.0, out=y)
    return (y * 32768.0).astype(np.int16), lufs, gain_db


def wav_bytes(pcm16: np.ndarray, fs: int, out_fs: Optional[int] = None) -> bytes:
    """A mono 16-bit WAV, resampled to ``out_fs`` when soxr is there (else at ``fs``)."""
    data, rate = pcm16, int(fs)
    if out_fs and int(out_fs) != rate:
        try:
            import soxr
            y = soxr.resample(pcm16.astype(np.float32) / 32768.0, rate, int(out_fs))
            data = (np.clip(y, -1.0, 32767.0 / 32768.0) * 32768.0).astype(np.int16)
            rate = int(out_fs)
        except Exception:                         # noqa: BLE001
            pass
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.ascontiguousarray(data, dtype="<i2").tobytes())
    return buf.getvalue()


async def encode(pcm16: np.ndarray, fs: int, *, fmt: str = "mp3", sample_rate: Optional[int] = None,
                 bitrate: str = "64k", timeout_s: float = ENCODE_TIMEOUT_S) -> tuple[bytes, str]:
    """(bytes, format): MP3 through ffmpeg on a pipe (asyncio, ``timeout_s``), else WAV."""
    ffmpeg = shutil.which("ffmpeg")
    if fmt == "mp3" and ffmpeg:
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(int(fs)), "-ac", "1",
               "-i", "pipe:0", "-ac", "1"]
        if sample_rate:
            cmd += ["-ar", str(int(sample_rate))]
        cmd += ["-b:a", str(bitrate), "-threads", "1", "-f", "mp3", "pipe:1"]
        nice = shutil.which("nice")
        if nice:
            cmd = [nice, "-n", str(ENCODE_NICE)] + cmd
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.PIPE,
                                                        stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(proc.communicate(np.ascontiguousarray(pcm16, "<i2").tobytes()),
                                              timeout_s)
            if proc.returncode == 0 and out:
                return out, "mp3"
            log.warning("ffmpeg failed (%s): sending WAV", (err or b"").decode(errors="replace").strip()[:200])
        except asyncio.CancelledError:
            if proc is not None and proc.returncode is None:
                proc.kill()
            raise
        except Exception as e:                    # noqa: BLE001 - a timeout or no ffmpeg: WAV
            if proc is not None and proc.returncode is None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:                 # noqa: BLE001
                    pass
            log.warning("ffmpeg didn't encode (%s): sending WAV", type(e).__name__)
    return wav_bytes(pcm16, fs, sample_rate), "wav"


# -- Describe it's keyword fallback ----------------------------------------------------------------------------------------
def _named(items: Any) -> list[tuple[str, str]]:
    """[(name, label)] from a list of names or of {"name", "label"} dicts."""
    out = []
    for it in items or []:
        if isinstance(it, str):
            out.append((it, it))
        elif isinstance(it, dict) and isinstance(it.get("name"), str):
            out.append((it["name"], str(it.get("label") or it["name"])))
    return out


def _mentions(text_n: str, phrase: str) -> bool:
    p = _norm_text(phrase).strip()
    return bool(p) and f" {p} " in text_n


def keyword_intent(text: str, context: dict, moods_vocab: Optional[list[str]] = None) -> dict:
    """§7.4's local match (README S3.12): steps, palette, moods, keep, an offered mode, and a reply."""
    tn = _norm_text(text)
    words = tn.split()
    named = {_norm_text(x).strip() for pair in _named(context.get("modes")) for x in pair}
    if isinstance(context.get("vibe"), str):
        named.add(_norm_text(context["vibe"]).strip())
    steps = (sum(-1 for w in words if w in CALMER_WORDS and w not in named)
             + sum(1 for w in words if w in WILDER_WORDS and w not in named))
    steps = int(_clamp(steps, -MAX_STEPS, MAX_STEPS))
    palette = None
    for name, label in _named(context.get("palettes")):
        if name in (contract.PALETTE_AUTO, contract.PALETTE_ART):
            continue
        if _mentions(tn, name) or _mentions(tn, label):
            palette = name
            break
    if palette is None and any(w in ART_WORDS for w in words):
        palette = contract.PALETTE_ART
    vocab = list(moods_vocab or []) + [m for m in (context.get("moods") or []) if isinstance(m, str)]
    moods: list[str] = []
    for w in words:
        if w in vocab and w not in moods:
            moods.append(w)
        if len(moods) == 3:
            break
    keep = any(w in KEEP_WORDS for w in words)
    current = context.get("current_mode")
    switch = None
    for name, label in _named(context.get("modes")):
        if name != current and (_mentions(tn, name) or _mentions(tn, label)):
            switch = name
            break
    return {"steps": steps, "palette": palette, "moods": moods or None, "keep": keep, "switch_mode": switch,
            "reply": _reply(steps, palette, moods, keep, switch, context)}


def _label_of(name: Optional[str], items: Any) -> Optional[str]:
    for n, label in _named(items):
        if n == name:
            return label
    return name


def _reply(steps: int, palette: Optional[str], moods: list[str], keep: bool, switch: Optional[str],
           context: dict) -> str:
    parts = []
    if steps:
        parts.append(f"{'calmer' if steps < 0 else 'wilder'} ×{abs(steps)}")
    if palette:
        parts.append("colours from the art" if palette == contract.PALETTE_ART
                     else f"palette {_label_of(palette, context.get('palettes'))}")
    if moods:
        parts.append("moods " + ", ".join(moods))
    if keep:
        parts.append("keeping this look")
    if not parts and not switch:
        return NOT_CAUGHT
    s = ("Okay: " + ", ".join(parts) + ".") if parts else ""
    if switch:
        s = (s + " " if s else "") + f"Switch to {_label_of(switch, context.get('modes'))}?"
    return s


def check_intent(data: dict, context: dict, moods_vocab: Optional[list[str]] = None) -> dict:
    """The LLM's describe answer, kept to what the card may set (contract.DescribeIntent)."""
    steps = int(_clamp(int(round(_f(data.get("steps"), 0.0))), -MAX_STEPS, MAX_STEPS))
    palette = data.get("palette")
    names = {n: n for n, _ in _named(context.get("palettes"))}
    names.update({label.lower(): n for n, label in _named(context.get("palettes"))})
    if isinstance(palette, str):
        p = palette.strip()
        palette = contract.PALETTE_ART if p.lower() == contract.PALETTE_ART else names.get(p) or names.get(p.lower())
        if palette == contract.PALETTE_AUTO:
            palette = None
    else:
        palette = None
    vocab = set(moods_vocab or []) | {m for m in (context.get("moods") or []) if isinstance(m, str)}
    moods = []
    for w in data.get("moods") or []:
        w = str(w).strip().lower()
        if w in vocab and w not in moods:
            moods.append(w)
    switch = data.get("switch_mode")
    modes = {n for n, _ in _named(context.get("modes"))}
    if not (isinstance(switch, str) and switch in modes and switch != context.get("current_mode")):
        switch = None
    keep = bool(data.get("keep"))
    reply = " ".join(str(data.get("reply") or "").split())[:200]
    if not reply:
        reply = _reply(steps, palette, moods, keep, switch, context)
    return {"steps": steps, "palette": palette, "moods": moods[:3] or None, "keep": keep, "switch_mode": switch,
            "reply": reply}


# -- the component ---------------------------------------------------------------------------------------------------------
class Listener:
    """Component "listener" (contract.Component), 1 Hz on show-brain."""

    name = "listener"
    tick_hz = contract.SLOW_TICK_HZ

    def __init__(self, ctx: contract.BrainContext, *, llm: Any = None, mono: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time, log_path: Optional[Path] = LISTENER_LOG,
                 vocab_path: Path = contract.VOCAB_FILE, encoder: Optional[Callable[..., Any]] = None,
                 warm: bool = True) -> None:
        self.ctx = ctx
        self._auto_fn: Optional[Callable[[], Optional[bool]]] = None   # Autopilot.is_on (set_auto)
        self._mic_fn: Optional[Callable[[], Any]] = None               # Mic.state (set_mic): {"on": bool, ...}
        self._llm = llm
        self._llm_error: Optional[str] = None
        self._llm_retry = -math.inf
        self._mono, self._wall = mono, wall
        self._log_path = Path(log_path) if log_path else None
        self._encode = encoder or encode
        self._warm = warm
        self.vocab = Vocab(vocab_path)
        self.cfg: dict = dict(DEFAULTS)
        self.ced_cfg: dict = dict(CED_DEFAULTS)
        self.speech_words: tuple[str, ...] = contract.SPEECH_WORDS   # listener.ced.speech_words
        self.speaks_s = CED_SPEAKS_S
        self.results_stale_s = RESULTS_STALE_S
        self._ced_state: dict = {"why": None, "run": None, "latest": None}   # the CED gate's last look (snapshot)
        self._sections: collections.deque = collections.deque(maxlen=SECTIONS_KEEP)   # {"id", "letter", "start_s"}
        self._section_due: Optional[float] = None    # the section call's clip end (boundary + section_call_after_s)
        self._section_calls = 0
        self._last_section_start: Optional[float] = None
        self.dsp_min_gate = DSP_MIN_GATE
        self.no_room_sound: set[str] = set()      # streams whose sender says room_sound: false (show.yaml)
        self.problems: list[str] = []
        self.on = False
        self.set_at: Optional[float] = None
        self.mode = False                         # owner, 20 Oct: this mode lets the AI listen (show.ai; persisted)
        self.beliefs_state = BeliefState()
        self._timeline: collections.deque = collections.deque(maxlen=int(TIMELINE_S) + 8)
        self._tasks: list = []                    # calls in flight (at most listener.max_in_flight)
        self._last_start: Optional[float] = None
        self._last_end: Optional[float] = None
        self._calls = 0
        self.errors = 0
        self._errors_row = 0
        self._agree = 0
        self._prev: Optional[dict] = None         # the previous InsightData
        self._log: collections.deque = collections.deque(maxlen=LOG_N)
        self._status: dict = {}
        self._estimate: Optional[dict] = None        # utils.llm.estimate("listen") this tick
        self._llm_sig: Any = None                   # what the listen models resolved to (logged when it changes)
        self._budget_hit = False
        self._why: Optional[str] = WHY_OFF
        self._stream: Optional[str] = None
        self._due: Optional[float] = None
        self._fast: Optional[str] = None            # README S5.22: why the fast cadence is on now
        self._end_at: Optional[float] = None        # structure's armed song end (track_end_armed's at_ms, s)
        self._boundary_s: Optional[float] = None    # when the last confirmed section boundary was heard
        self._eff_s = 0.0                           # seconds of effective listening since start ...
        self._fast_s = 0.0                          # ... and of those at the fast cadence (the estimate's share)
        self._acc_t: Optional[float] = None
        self._beliefs: dict = self.beliefs_state.view(0.0)
        self._view: dict = {}
        self._snap: dict = {}
        self._view = self._build_view(self._mono())
        self._snap = dict(self._view, beliefs=self._beliefs)

    # -- Component -----------------------------------------------------------------------------------------------
    def provides(self) -> dict:
        return {"beliefs": self.beliefs, "on_music_event": self.on_music_event}

    def configure(self, settings: dict) -> None:
        src = (settings or {}).get("listener") or {}
        cfg, problems = dict(DEFAULTS), []
        if not isinstance(src, dict):
            problems.append("listener: not a mapping")
            src = {}
        for k, d in DEFAULTS.items():
            if k not in src:
                continue
            v = src[k]
            if k == "music_gate":
                if v in MUSIC_GATES:
                    cfg[k] = v
                else:
                    problems.append(f"listener.music_gate: {v!r} is not one of {', '.join(MUSIC_GATES)}")
            elif k in ("requires_auto", "requires_mic", "requires_mode"):
                if isinstance(v, bool):
                    cfg[k] = v
                else:
                    problems.append(f"listener.{k}: {v!r} is not true/false")
            elif k in INT_KEYS:
                if isinstance(v, int) and not isinstance(v, bool) and v >= 1:
                    cfg[k] = v
                else:
                    problems.append(f"listener.{k}: {v!r} is not a whole number >= 1")
            elif k in SHARE_KEYS:
                fv = _f(v)
                if fv is not None and 0.0 <= fv <= 1.0:
                    cfg[k] = fv
                else:
                    problems.append(f"listener.{k}: {v!r} is not 0..1")
            else:
                fv = _f(v)
                if fv is not None and fv > 0:
                    cfg[k] = fv
                else:
                    problems.append(f"listener.{k}: {v!r} is not a positive number")
        ced, ced_cfg = src.get("ced"), dict(CED_DEFAULTS)
        speaks = CED_SPEAKS_S
        if isinstance(ced, dict):
            for k in CED_DEFAULTS:
                if k in ced:
                    fv = _f(ced[k])
                    if fv is not None and 0.0 <= fv <= 1.0:
                        ced_cfg[k] = fv
                    else:
                        problems.append(f"listener.ced.{k}: {ced[k]!r} is not 0..1")
            if "speaks_s" in ced:
                fv = _f(ced["speaks_s"])
                if fv is not None and 0.0 < fv <= 10.0:
                    speaks = fv
                else:
                    problems.append(f"listener.ced.speaks_s: {ced['speaks_s']!r} is not 0..10 s")
        elif ced is not None:
            problems.append("listener.ced: not a mapping")
        words, prob = contract.speech_words(ced.get("speech_words") if isinstance(ced, dict) else None,
                                            "listener.ced.speech_words")
        if prob:
            problems.append(prob)
        self.ced_cfg, self.speech_words, self.speaks_s = ced_cfg, words, speaks
        ml = (settings or {}).get("ml")
        rs = _f(ml.get("results_stale_s")) if isinstance(ml, dict) else None
        self.results_stale_s = rs if rs is not None and rs > 0 else RESULTS_STALE_S
        music = ((settings or {}).get("situations") or {}).get("music") or {}
        g = _f(music.get("dsp_min_gate")) if isinstance(music, dict) else None
        self.dsp_min_gate = _clamp(g, 0.0, 1.0) if g is not None else DSP_MIN_GATE
        self.no_room_sound, more = no_room_sound_streams(settings or {})
        problems += more
        self.cfg, self.problems = cfg, problems
        for p in problems:
            log.warning("show.yaml %s: using %s's design default", p, p.split(":")[0])

    def restore(self, saved: dict) -> None:
        saved = saved or {}
        self.on = bool(saved.get("on", False))
        at = _f(saved.get("set_at"))
        self.set_at = at
        self.mode = saved.get("mode") is True     # never saved, or not a bool: off (fail closed)

    def start(self) -> None:
        if self._warm and self._llm is None:
            # utils.llm, openai and today's ledger off show-brain: the first call then never waits on an import
            threading.Thread(target=self._warm_up, name="show-llm-warm", daemon=True).start()

    def _warm_up(self) -> None:
        try:
            m = self._llm_mod()
            if m is not None:
                m.status()
                import openai  # noqa: F401
                probe = getattr(m, "probe", None)
                if probe is not None:             # models.list() off show-brain: a discovered id before the first call
                    t = probe("listen") or {}
                    log.info("listen models: %s", ", ".join(
                        f"{r.get('model') or r.get('configured')} "
                        f"({r.get('provider')}: {r.get('skip') or r.get('via')})"
                        for r in t.get("resolved") or []) or "none")
        except Exception as e:                    # noqa: BLE001
            log.info("LLM layer warm-up: %s", e)

    def tick(self, now_s: float) -> None:
        self.vocab.poll(now_s)
        self.beliefs_state.mood_map = self.vocab.music_moods
        self._sample(now_s)
        m = self._llm_mod()
        if m is not None:
            try:
                self._status = m.status()
                self._budget_hit = bool(m.over_budget("listen"))
            except Exception:                     # noqa: BLE001
                self._status, self._budget_hit = {}, False
            try:
                est = getattr(m, "estimate", None)
                self._estimate = est("listen", audio_s=float(self.cfg["clip_s"])) if est is not None else None
            except Exception:                     # noqa: BLE001
                self._estimate = None
            self._note_models()
        self._beliefs = self.beliefs_state.view(now_s)
        self._section_tick(now_s)
        self._fast = self._developing(now_s)
        self._why, self._stream = self._gate(now_s)
        self._account(now_s)
        self._due = self._next_due(now_s)
        free = len(self._tasks) < int(self.cfg["max_in_flight"])
        if self._why is None and free and self._section_due is not None and \
                now_s >= self._section_due and self._section_allowed(now_s):
            due, self._section_due = self._section_due, None
            self._section_calls += 1
            self._last_section_start = now_s
            self._launch(self._stream, now_s, t_end=due, kind="section")
        elif self._why is None and free and self._due is not None and now_s >= self._due:
            self._launch(self._stream, now_s)
        if self._section_due is not None and now_s > self._section_due + float(self.cfg["cadence_s"]):
            self._log_event({"kind": "skipped", "call": "section", "why": self._why or "no window for it in time",
                             "due_age_s": round(now_s - self._section_due, 1)})
            self._section_due = None              # a stale section call is no use: the regular cadence carries on
        self._refresh(now_s)

    def _refresh(self, now_s: float) -> None:
        self._view = self._build_view(now_s)
        lt = ((self._status or {}).get("tasks") or {}).get("listen") or {}
        self._snap = dict(self._view, beliefs=self._beliefs, agreeing=self._agree, problems=list(self.problems), vocab_error=self.vocab.error,
                          llm_error=self._llm_error, ced=dict(self._ced_state, **self.ced_cfg),
                          llm={"usable": list(lt.get("usable") or []), "resolved": list(lt.get("resolved") or [])},
                          sections={"known": len(self._sections), "current": self._sections[-1]["id"]
                                    if self._sections else None,
                                    "section_call_in_s": round(self._section_due - now_s, 1)
                                    if self._section_due is not None else None,
                                    "section_calls": self._section_calls})

    def snapshot(self) -> dict:
        return self._snap

    def persist(self) -> Optional[dict]:
        return {"on": self.on, "set_at": self.set_at, "mode": self.mode}

    def stop(self) -> None:
        self._cancel()

    # -- the API's calls (show-brain) --------------------------------------------------------------------------------
    def set_on(self, on: bool) -> dict:
        """The AI listening switch (PUT /api/v1/listening). Off cancels any call in flight."""
        self.on = bool(on)
        self.set_at = self._wall()
        if not self.on and self._tasks:
            self._cancel()                        # every upload needs the switch, test streams included
        if self.ctx.persist is not None:
            try:
                self.ctx.persist()
            except Exception:                     # noqa: BLE001
                log.exception("persist failed")
        now = self._mono()
        self._why, self._stream = self._gate(now)
        self._refresh(now)
        self._log_event({"kind": "switch", "on": self.on})
        return self._view

    def set_mode(self, mode: bool) -> dict:
        """The mode's permission (owner, 20 Oct; PUT /api/v1/listening {"mode": bool}, room_service's per-mode device
        ``show.ai``, as ``show.mic`` claims the mic). The Room page's switch stays the master: both must be on. Off
        cancels any call in flight, as the switch going off does. Room re-sends it on every re-sync, so only a change
        is persisted and logged."""
        mode = bool(mode)
        changed, self.mode = mode != self.mode, mode
        if not self.mode and self.cfg["requires_mode"] and self._tasks:
            self._cancel()                        # every upload needs the mode's permission too, test streams included
        if changed and self.ctx.persist is not None:
            try:
                self.ctx.persist()
            except Exception:                     # noqa: BLE001
                log.exception("persist failed")
        now = self._mono()
        self._why, self._stream = self._gate(now)
        self._refresh(now)
        if changed:
            self._log_event({"kind": "mode", "mode": self.mode})
        return self._view

    def view(self) -> dict:
        return self._view

    def beliefs(self) -> Optional[dict]:
        """Any thread: the last Beliefs built on show-brain."""
        return self._beliefs

    def log(self, n: int = 50) -> list[dict]:
        items = list(self._log)
        return items[-max(0, int(n)):] if n else []

    async def describe(self, text: str, context: dict) -> tuple[dict, str]:
        """(DescribeIntent, "llm" | "keywords"). Never raises; the LLM gets describe_timeout_s."""
        context = context or {}
        text = str(text or "")[:300]
        m = self._llm_mod()
        try:
            if m is not None and self._usable(m, "describe"):
                res = await asyncio.wait_for(
                    m.acall_json("describe", system=self._describe_prompt(), user=self._describe_user(text, context),
                                 tool=DESCRIBE_TOOL, timeout_s=float(self.cfg["describe_timeout_s"])),
                    timeout=float(self.cfg["describe_timeout_s"]))
                if res.ok and isinstance(res.data, dict):
                    intent = check_intent(res.data, context, self.vocab.moods)
                    self._log_event({"kind": "describe", "via": "llm", "text": text, "intent": intent,
                                     "model": res.model, "cost_usd": res.cost_usd})
                    return intent, "llm"
                self._log_event({"kind": "describe_failed", "error": res.error})
        except asyncio.CancelledError:
            raise
        except Exception as e:                    # noqa: BLE001 - a timeout or anything: keywords
            self._log_event({"kind": "describe_failed", "error": f"{type(e).__name__}"})
        intent = keyword_intent(text, context, self.vocab.moods)
        self._log_event({"kind": "describe", "via": "keywords", "text": text, "intent": intent})
        return intent, "keywords"

    # -- internals -----------------------------------------------------------------------------------------------
    def _llm_mod(self) -> Any:
        if self._llm is not None:
            return self._llm
        if self._llm_error is not None and self._mono() < self._llm_retry:
            return None
        try:
            try:
                import utils.llm as m
            except ImportError:
                root = str(contract.REPO_ROOT)
                if root not in sys.path:
                    sys.path.append(root)
                import utils.llm as m
        except Exception as e:                    # noqa: BLE001 - no LLM layer: keywords, no clips
            self._llm_error = f"utils.llm: {type(e).__name__}: {e}"
            self._llm_retry = self._mono() + 60.0
            log.error("%s: listening and Describe it's LLM are off", self._llm_error)
            return None
        self._llm, self._llm_error = m, None
        return m

    def _usable(self, m: Any, task: str) -> bool:
        st = self._status or {}
        if not st:
            try:
                st = m.status()
            except Exception:                     # noqa: BLE001
                return False
        return bool(((st.get("tasks") or {}).get(task) or {}).get("usable"))

    def _active_stream(self, now_s: float) -> Optional[str]:
        h = None
        if self.ctx.health is not None:
            try:
                h = self.ctx.health()
            except Exception:                     # noqa: BLE001
                h = None
        s = (h or {}).get("active_audio") if isinstance(h, dict) else None
        if s:
            return s
        f = self._features(now_s)
        if f is not None and f.get("stream"):
            return f["stream"]
        rings = self.ctx.rings
        return getattr(rings, "last_stream", None) if rings is not None else None

    def _features(self, now_s: float) -> Optional[dict]:
        if self.ctx.features is None:
            return None
        try:
            f = self.ctx.features()
        except Exception:                         # noqa: BLE001
            return None
        if not f:
            return None
        t = _f(f.get("t_ms"))
        if t is None or now_s * 1000.0 - t > FEATURES_STALE_S * 1000.0:
            return None
        return f

    def _call_or_none(self, fn: Any) -> Any:
        if fn is None:
            return None
        try:
            return fn()
        except Exception:                         # noqa: BLE001
            return None

    def _sample(self, now_s: float) -> None:
        f = self._features(now_s)
        if f is None:
            self._timeline.append((now_s, None, None, None, None, None))
            return
        self._timeline.append((now_s, _f(f.get("lufs_s")), _f(f.get("energy")), _f(f.get("gate")),
                               _f(f.get("brightness")), _f(f.get("onset"))))

    def _gate_mean(self, now_s: float, window_s: float) -> float:
        vals = [row[3] or 0.0 for row in self._timeline if now_s - row[0] < window_s]
        return sum(vals) / len(vals) if vals else 0.0

    def _mic_off(self, stream: Optional[str]) -> bool:
        """Is ``stream`` kept off by the mic on demand (README S5.16)? The ring's own off set first (it moves the moment
        mic.py decides), then receiver health's ``off`` and the stream's state."""
        if not stream:
            return False
        rings = self.ctx.rings
        if rings is not None and stream in (getattr(rings, "off", None) or ()):
            return True
        h = None
        if self.ctx.health is not None:
            try:
                h = self.ctx.health()
            except Exception:                     # noqa: BLE001
                h = None
        h = h if isinstance(h, dict) else {}
        if isinstance(h.get("off"), list) and stream in h["off"]:
            return True
        st = (h.get("streams") or {}).get(stream) if isinstance(h.get("streams"), dict) else None
        return isinstance(st, dict) and st.get("state") == "off"

    def room_sound(self, stream: Optional[str]) -> bool:
        """Does ``stream`` carry the room's own sound? True unless its show.yaml sender says ``room_sound: false``
        (a music file played as a test). Unknown streams count as room sound: the strict side."""
        return not (stream and stream in self.no_room_sound)

    def set_auto(self, fn: Optional[Callable[[], Optional[bool]]]) -> None:
        """Auto on/off now (autopilot.Autopilot.is_on; any thread, no side effects). Unset or None: off."""
        self._auto_fn = fn

    def set_mic(self, fn: Optional[Callable[[], Any]]) -> None:
        """The room mic's state (mic.Mic.state: {"on": bool, ...}). Unset or unknown: off."""
        self._mic_fn = fn

    def _mode_label(self) -> Optional[str]:
        """The mode's label for WHY_MODE_OFF (ctx.room's RoomView, read by reference; the mode it is going to, else the
        one it is in), None when the room doesn't say. Words only: nothing branches on it."""
        view = self._call_or_none(self.ctx.room)
        if not isinstance(view, dict):
            return None
        name = view.get("target") or view.get("current")
        modes = view.get("modes") if isinstance(view.get("modes"), dict) else {}
        row = modes.get(name) if isinstance(name, str) else None
        lab = row.get("label") if isinstance(row, dict) else None
        return lab if isinstance(lab, str) and lab.strip() else None

    def _auto_is_on(self) -> bool:
        try:
            return self._auto_fn is not None and self._auto_fn() is True
        except Exception:                         # noqa: BLE001
            return False

    def _mic_is_on(self) -> bool:
        try:
            st = self._mic_fn() if self._mic_fn is not None else None
        except Exception:                         # noqa: BLE001
            st = None
        return isinstance(st, dict) and st.get("on") is True

    def _gate(self, now_s: float) -> tuple[Optional[str], Optional[str]]:
        """(the first failing reason in page words, or None; the stream a clip would come from). Every stream, test
        or not, needs the AI listening switch, the mode's permission (owner, 20 Oct: show.ai), Auto and the mic on
        (owner, 17 Oct) and a vibe that doesn't say ``listener: off``; the mode's show target no longer gates (owner,
        17 Oct). Only a stream whose sender says ``room_sound: false`` skips the music/speech check (it can't carry
        anyone's speech). The upload re-checks all of it (_call)."""
        stream = self._active_stream(now_s)
        why = None
        if not self.on:
            why = WHY_OFF
        elif self.cfg["requires_mode"] and self.mode is not True:
            why = why_mode(self._mode_label())        # owner, 20 Oct: off by default, the live modes turn it on
        elif self.cfg["requires_auto"] and not self._auto_is_on():
            why = WHY_AUTO_OFF                        # owner, 17 Oct: cost, and it's intrusive; checked again at upload
        elif self.cfg["requires_mic"] and not self._mic_is_on():
            why = WHY_MIC_OFF
        else:
            vibe = self._call_or_none(self.ctx.vibe)
            if vibe is not None and vibe.get("listener", True) is False:
                why = WHY_VIBE                            # a feel can still say no (the piano: MIDI already says it)
        self._arm(why is None)
        if why is not None:
            return why, stream
        if self._mic_off(stream):
            return WHY_MIC_OFF, stream
        if stream is None or self._features(now_s) is None:
            return WHY_NO_SOUND, stream
        self._ced_state = {"why": None, "run": None, "latest": None}
        if self.room_sound(stream):
            if self.cfg["music_gate"] == "classifier":
                _run, why = self._ced_run(stream, now_s)
                if why is not None:
                    return why, stream
            else:
                why = self._dsp_music_gate(now_s)
                if why is not None:
                    return why, stream
        if self._gate_mean(now_s, QUIET_WINDOW_S) < float(self.cfg["quiet_gate"]):
            return WHY_QUIET, stream
        rings = self.ctx.rings
        held = 0.0
        if rings is not None:
            try:
                held = float(((rings.info() or {}).get(stream) or {}).get("held_s") or 0.0)
            except Exception:                     # noqa: BLE001
                held = 0.0
        if held < min(float(self.cfg["clip_s"]), float(self.cfg["min_clip_s"])):
            return WHY_NO_SOUND, stream
        m = self._llm_mod()
        if m is None or not self._usable(m, "listen"):
            return WHY_NO_MODEL, stream
        return None, stream

    def _dsp_music_gate(self, now_s: float) -> Optional[str]:
        """``music_gate: dsp`` (Stage 3, the owner's choice): a beat or a key over situations.music.dsp_min_gate."""
        clock = self._call_or_none(self.ctx.clock) or {}
        harmony = self._call_or_none(self.ctx.harmony) or {}
        beat = clock.get("gate") in ("beat", "dmx")
        keyed = (_f(harmony.get("conf"), 0.0) or 0.0) >= HARMONY_MIN_CONF and harmony.get("key") is not None
        if self._gate_mean(now_s, QUIET_WINDOW_S) < self.dsp_min_gate:
            return WHY_QUIET
        if not (beat or keyed):
            return WHY_NO_MUSIC
        return None

    # -- the CED gate and sections (Stage 4) -------------------------------------------------------------------------
    def _ml_view(self) -> Optional[dict]:
        v = self._call_or_none(getattr(self.ctx, "ml", None))
        return v if isinstance(v, dict) else None

    def _ced_run(self, stream: str, now_s: float,
                 t_end: Optional[float] = None) -> tuple[Optional[tuple[float, float]], Optional[str]]:
        """README S4.8's CED gate for a room-sound stream: ((t0, t1) the clip, None) or (None, why). A reading's
        speech is the max over listener.ced.speech_words (speech, conversation, crowd: guests talking over music
        show up as babble and conversation, not only as Speech). The run is the longest contiguous stretch of seconds
        in the ``clip_s`` that ends at ``t_end`` (None: the latest reading) whose readings say music >= ced.music_min
        with speech < ced.speech_max; the latest run wins a tie. A run starts speaks_s before its first reading, and
        each passing reading joins the one before it when they are at most the CED's stale time apart (its real
        cadence, stretched under load), else speaks_s or its window. Every second must be vouched for by each reading
        that covers it: the run's last second is also in
        the next reading's window, so it counts only when that reading exists and passed (or doesn't reach back
        into it); otherwise the run ends speaks_s earlier."""
        v = self._ml_view()
        rows = []
        if v is not None and v.get("stream") == stream:
            rows = sorted((r for r in v.get("ced_recent") or [] if isinstance(r, dict) and _f(r.get("t")) is not None
                           and r.get("stream", stream) == stream), key=lambda r: r["t"])
        latest = rows[-1] if rows else None
        stale = contract.ml_stale_s(v, "ced", self.results_stale_s)
        if latest is None or now_s - float(latest["t"]) > stale:
            self._ced_state = {"why": WHY_NO_CED, "run": None, "latest": None}
            return None, WHY_NO_CED
        music_min, speech_max = float(self.ced_cfg["music_min"]), float(self.ced_cfg["speech_max"])
        words, speaks = self.speech_words, float(self.speaks_s)

        def speech(r: dict, missing: float) -> float:
            x = contract.speech_of(r, words)
            return missing if x is None else x

        def ok(r: dict) -> bool:
            return _f(r.get("music_p"), 0.0) >= music_min and speech(r, 1.0) < speech_max

        lat = {"t": latest["t"], "music": _f(latest.get("music_p"), 0.0), "speech": speech(latest, 0.0)}
        if lat["speech"] >= speech_max:
            self._ced_state = {"why": WHY_TALKING, "run": None, "latest": lat}
            return None, WHY_TALKING
        end = float(latest["t"]) if t_end is None else float(t_end)
        start = end - float(self.cfg["clip_s"])
        runs: list[list] = []                     # [first second's start, the last reading's t, its index]
        cur: Optional[list] = None
        prev: Optional[dict] = None
        for i, r in enumerate(rows):
            t = float(r["t"])
            if t <= start or t > end + 1e-6:
                continue
            if not ok(r):
                cur, prev = None, None
                continue
            # a reading vouches back to the one before it while that one was still "now" (ml_stale_s: the worker's
            # real cadence, which it stretches under load), so readings every 4-8 s join into one run (21 Oct)
            joined = prev is not None and t - float(prev["t"]) <= max(speaks, stale, _f(r.get("window_s"), 0.0)) + 1e-6
            if cur is None or not joined:
                cur = [max(start, t - speaks), t, i]
                runs.append(cur)
            else:
                cur[1], cur[2] = t, i
            prev = r
        best: Optional[tuple[float, float]] = None
        for run0, t_last, i in runs:
            t1 = min(t_last, end)
            nxt = rows[i + 1] if i + 1 < len(rows) else None
            vouched = nxt is not None and (ok(nxt) or float(nxt["t"]) - (_f(nxt.get("window_s"), speaks) or speaks)
                                           >= t_last - 1e-6)
            if not vouched:
                t1 = min(t1, t_last - speaks)     # its last second waits for the next reading's word
            if t1 <= run0 + 1e-9:
                continue
            if best is None or t1 - run0 >= best[1] - best[0] - 1e-9:
                best = (run0, t1)                 # >=: the latest run wins a tie
        if best is None or best[1] - best[0] < float(self.cfg["min_clip_s"]) - 1e-6:
            self._ced_state = {"why": WHY_SHORT, "run": [round(best[0], 2), round(best[1], 2)] if best else None,
                               "latest": lat}
            return None, WHY_SHORT
        self._ced_state = {"why": None, "run": [round(best[0], 2), round(best[1], 2)], "latest": lat}
        return best, None

    def on_music_event(self, ev: dict) -> None:
        """structure's room.event (show-brain): a confirmed section_change makes one extra call due at its boundary +
        section_call_after_s (never a predicted one) and turns the fast cadence on for fast_after_section_s; a
        track_end_armed's end (README S5.22: the fast cadence within fast_end_s of it); a track_change lets the next
        verdict's genre win at once."""
        if not isinstance(ev, dict):
            return
        kind = ev.get("kind")
        if kind == "section_change":
            start_ms = _f(ev.get("boundary_ms"), _f(ev.get("at_ms")))
            sid = ev.get("section_id")
            if start_ms is not None and isinstance(sid, int) and not isinstance(sid, bool):
                self._note_section(sid, ev.get("letter") if isinstance(ev.get("letter"), str) else None,
                                   start_ms / 1000.0)
            if not ev.get("predicted") and start_ms is not None:
                self._section_due = start_ms / 1000.0 + float(self.cfg["section_call_after_s"])
                self._boundary_s = self._mono()
        elif kind == "track_end_armed":
            at = _f(ev.get("at_ms"))
            self._end_at = at / 1000.0 if at is not None and ev.get("eta_s") is not None else None
        elif kind == "track_end":
            self._end_at = None
        elif kind == "track_change":
            self._end_at = None
            self.beliefs_state.track_changed()
            self._log_event({"kind": "track_change", "via": ev.get("via"), "track_id": ev.get("track_id")})

    def _note_section(self, sid: int, letter: Optional[str], start_s: float) -> None:
        for sec in self._sections:
            if sec["id"] == sid:
                if letter:
                    sec["letter"] = letter
                return
        self._sections.append({"id": sid, "letter": letter, "start_s": start_s})
        if len(self._sections) > 1 and self._sections[-2]["start_s"] > start_s:
            ordered = sorted(self._sections, key=lambda x: x["start_s"])
            self._sections.clear()
            self._sections.extend(ordered)

    def _context(self, now_s: float) -> Optional[dict]:
        mc = self._call_or_none(getattr(self.ctx, "music_context", None))
        if not isinstance(mc, dict) or _f(mc.get("t_ms")) is None or now_s - mc["t_ms"] / 1000.0 > CONTEXT_STALE_S:
            return None
        return mc

    def _section_tick(self, now_s: float) -> None:
        """The current section (and its letter, once structure has named it) from music.context."""
        mc = self._context(now_s)
        sec = mc.get("section") if mc is not None and isinstance(mc.get("section"), dict) else None
        if sec is None or not isinstance(sec.get("id"), int) or _f(sec.get("started_ms")) is None:
            return
        self._note_section(sec["id"], sec.get("letter") if isinstance(sec.get("letter"), str) else None,
                           sec["started_ms"] / 1000.0)

    def _segments(self, t0: float, t1: float) -> list[dict]:
        """The sections the clip [t0, t1) spans: [{"id", "letter", "from_s", "to_s"}], seconds from the clip's start."""
        secs = list(self._sections)
        out = []
        for i, sec in enumerate(secs):
            a = sec["start_s"]
            b = secs[i + 1]["start_s"] if i + 1 < len(secs) else math.inf
            lo, hi = max(a, t0), min(b, t1)
            if hi > lo:
                out.append({"id": sec["id"], "letter": sec["letter"], "from_s": round(lo - t0, 1),
                            "to_s": round(hi - t0, 1)})
        return out

    def _segment_at(self, t: float) -> Optional[int]:
        sid = None
        for sec in self._sections:
            if sec["start_s"] <= t + 1e-6:
                sid = sec["id"]
        return sid

    @staticmethod
    def _segment_text(segs: list[dict]) -> str:
        parts = [f"#{x['id']}{' ' + x['letter'] if x['letter'] else ''} {x['from_s']:.0f}-{x['to_s']:.0f} s" for x in segs]
        return ("Segment map, from the room's own section detector (seconds from the clip's start; a repeated letter "
                "is the same section again): " + ", ".join(parts) + ".")

    def _arm(self, on: bool) -> None:
        """The ring records only while a clip could go (the switch, the mode, Auto, the mic and the vibe allow it): no
        resampling on show-rx for nothing. A first clip then waits for the ring to fill (min_clip_s)."""
        rings = self.ctx.rings
        if rings is not None and getattr(rings, "armed", on) != on:
            try:
                rings.armed = on
            except Exception:                     # noqa: BLE001
                pass

    def _section_allowed(self, now_s: float) -> bool:
        """A section call follows the cadence's own slowing (x3 over the daily budget, backed off after failures): at
        least min_gap_s and half the current interval after the last call started, and at most one section call per
        interval. So false boundaries never become a stream of uploads."""
        iv = self.interval()
        if self._last_start is not None and now_s < self._last_start + max(float(self.cfg["min_gap_s"]), iv / 2.0):
            return False
        return self._last_section_start is None or now_s >= self._last_section_start + iv

    def _developing(self, now_s: float) -> Optional[str]:
        """README S5.22: why the fast cadence is on (page words), or None: structure says build or outro; a forecast
        window is open or opens within fast_lookahead_s; structure's armed song end is within fast_end_s; a confirmed
        section boundary came in the last fast_after_section_s."""
        mc = self._context(now_s)
        sec = mc.get("section") if mc is not None and isinstance(mc.get("section"), dict) else {}
        if sec.get("phase") == "build":
            return "a build"
        if sec.get("phase") == "outro":
            return "the music is fading"
        ahead = float(self.cfg["fast_lookahead_s"])
        for c in (self._beliefs or {}).get("coming") or []:
            if isinstance(c, dict) and _f(c.get("t0_s")) is not None and _f(c.get("t1_s")) is not None \
                    and c["t0_s"] - now_s <= ahead and c["t1_s"] >= now_s:
                return "a forecast window"
        if self._end_at is not None:
            if 0.0 <= self._end_at - now_s <= float(self.cfg["fast_end_s"]):
                return "the song's end is near"
            if self._end_at < now_s - float(self.cfg["fast_end_s"]):
                self._end_at = None
        if self._boundary_s is not None and now_s - self._boundary_s <= float(self.cfg["fast_after_section_s"]):
            return "a new section"
        return None

    def _account(self, now_s: float) -> None:
        """The share of effective listening spent at the fast cadence (the estimate's, once an hour is measured)."""
        if self._acc_t is not None and self._why is None:
            dt = min(2.0, max(0.0, now_s - self._acc_t))
            self._eff_s += dt
            if self._fast:
                self._fast_s += dt
        self._acc_t = now_s

    def interval(self) -> float:
        """Seconds between call starts now (README S5.22's cadence): fast_cadence_s while something develops, else
        cadence_s; x3 over the daily budget; backed off after failures in a row."""
        base = float(self.cfg["fast_cadence_s"] if self._fast else self.cfg["cadence_s"])
        if self._budget_hit:                      # utils.llm.over_budget("listen"), read each tick
            base *= BUDGET_SLOWDOWN
        if self._errors_row:
            base = max(base, min(float(self.cfg["cadence_s"]) * 2.0 ** self._errors_row, ERROR_BACKOFF_MAX_S))
        return max(base, float(self.cfg["min_gap_s"]))

    def _next_due(self, now_s: float) -> Optional[float]:
        if self._last_start is None:
            return now_s
        return self._last_start + self.interval()

    def _launch(self, stream: Optional[str], now_s: float, *, t_end: Optional[float] = None,
                kind: str = "cadence") -> None:
        if stream is None:
            return
        self._last_start = now_s
        coro = self._one_call(stream, t_end, kind)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            task: Any = loop.create_task(coro)
        elif self.ctx.loop is not None:
            task = asyncio.run_coroutine_threadsafe(coro, self.ctx.loop)
        else:
            coro.close()
            return
        self._tasks.append(task)
        task.add_done_callback(self._done)        # also runs for a task cancelled before it started

    def _done(self, task: Any) -> None:
        if task in self._tasks:
            self._tasks.remove(task)
            self._last_end = self._mono()

    def _cancel(self) -> None:
        for t in list(self._tasks):
            try:
                loop = t.get_loop() if hasattr(t, "get_loop") else None
                try:
                    running = asyncio.get_running_loop()
                except RuntimeError:
                    running = None
                if loop is not None and loop is not running:
                    loop.call_soon_threadsafe(t.cancel)
                else:
                    t.cancel()
            except Exception:                     # noqa: BLE001
                pass

    async def _one_call(self, stream: str, t_end: Optional[float] = None, kind: str = "cadence") -> None:
        try:
            await self._call(stream, t_end, kind)
        except asyncio.CancelledError:
            self._log_event({"kind": "cancelled", "stream": stream})
            raise
        except Exception as e:                    # noqa: BLE001 - counted; the next window is the retry
            self.errors += 1
            self._errors_row += 1
            log.exception("listener call failed")
            self._log_event({"kind": "error", "stream": stream, "error": f"{type(e).__name__}: {e}"[:300]})

    def _cut_clip(self, stream: str, t_end: Optional[float]) -> tuple[Any, Optional[str]]:
        """(the ring clip this call sends, None) or (None, why). A room-sound stream under ``music_gate: classifier``
        sends only its longest music-dominant run (README S4.8); a section call's clip ends at ``t_end``."""
        rings = self.ctx.rings
        if rings is None:
            return None, WHY_NO_SOUND
        clip_s = float(self.cfg["clip_s"])
        if self.room_sound(stream) and self.cfg["music_gate"] == "classifier":
            run, why = self._ced_run(stream, self._mono(), t_end)
            if run is None:
                return None, why
            return rings.clip(stream, run[0], run[1]), None
        if t_end is not None:
            return rings.clip(stream, t_end - clip_s, t_end), None
        return rings.latest(stream, clip_s), None

    async def _call(self, stream: str, t_end: Optional[float] = None, kind: str = "cadence") -> None:
        off_gen = getattr(self.ctx.rings, "off_gen", None)    # a stream going off from now on voids this clip
        clip, why = self._cut_clip(stream, t_end)
        if clip is None or clip.pcm.shape[0] < clip.fs:
            self._log_event({"kind": "skipped", "stream": stream, "call": kind, "why": why or WHY_NO_SOUND})
            return
        m = self._llm_mod()
        if m is None:
            return
        loop = asyncio.get_running_loop()
        pcm, lufs, gain = await loop.run_in_executor(None, normalise, clip.pcm, clip.fs)
        ts = getattr(m, "task_settings", None)
        aud = ((ts("listen") if ts is not None else None) or {}).get("audio")
        aud = aud if isinstance(aud, dict) else {}
        audio, fmt = await self._encode(pcm, clip.fs, fmt=str(aud.get("format") or "mp3"),
                                        sample_rate=int(aud["sample_rate"]) if aud.get("sample_rate") else None,
                                        bitrate=str(aud.get("bitrate") or "64k"))
        why, now_stream = self._gate(self._mono())   # still allowed, for this stream, right before the upload?
        if why is None and getattr(self.ctx.rings, "off_gen", None) != off_gen:
            why = WHY_MIC_WENT_OFF                    # the mic went off while it was encoded: never sent
        if why is not None or now_stream != stream:
            self._log_event({"kind": "skipped", "stream": stream, "why": why or "the stream changed"})
            return
        t1 = clip.t0_s + clip.pcm.shape[0] / clip.fs
        user = [self._timeline_text(t1, clip, lufs)]
        segs = self._segments(clip.t0_s, t1)
        if segs:
            user.append(self._segment_text(segs))
        now_line = self._now_text(t1)
        if now_line:
            user.append(now_line)
        if self._prev is not None and self._calls % PREV_EVERY != PREV_EVERY - 1:
            user.append(self._prev_text(self._prev))
        self._calls += 1
        res = await m.acall_json("listen", system=self._listen_prompt(), user=user, tool=LISTEN_TOOL, audio=audio,
                                 audio_format=fmt, lenient=True)   # a stray word is cleaned, never paid for twice
        if not res.ok:
            self.errors += 1
            self._errors_row += 1
            self._log_event({"kind": "error", "stream": stream, "error": res.error, "attempts": res.attempts,
                             "cost_usd": res.cost_usd})
            self._refresh(self._mono())
            return
        self._errors_row = 0
        now = self._mono()
        ins = to_insight(res.data or {}, vocab=self.vocab, stream=stream, model=res.model or "",
                         latency_ms=res.latency_ms, window=(clip.t0_s, t1), t_ms=now * 1000.0)
        ins["segment_id"] = self._segment_at(t1)  # README S4.8: the section at the clip's end (structure's id)
        self._agree_update(ins)
        if self._prev is None or _t1_s(ins) >= _t1_s(self._prev) - 1e-6:
            self._prev = ins                      # two in flight: an older clip's verdict never becomes the hypothesis
        self.beliefs_state.add(ins, now)
        self._beliefs = self.beliefs_state.view(now)
        try:
            self.ctx.publish({"type": contract.MUSIC_INSIGHT, "data": ins})
        except Exception:                         # noqa: BLE001
            log.exception("music.insight publish failed")
        if self.ctx.notify is not None:
            try:
                self.ctx.notify("card", None)
            except Exception:                     # noqa: BLE001
                pass
        self._log_event({"kind": "verdict", "insight": ins, "lufs": round(lufs, 1) if lufs is not None else None,
                         "gain_db": round(gain, 1), "format": fmt, "bytes": len(audio), "cost_usd": res.cost_usd,
                         "usage": res.usage, "call": kind, "segments": segs,
                         "problems": list(getattr(res, "problems", None) or []) or None})
        self._log_genre_detail(ins, now)
        self._refresh(now)

    def _log_genre_detail(self, ins: dict, now_s: float) -> None:
        """§7.3: genre.detail values ("bossa nova", "Afro house") are logged to grow vocab.yaml, next to what the
        local ML heard (music.context's genre and EffNet's top styles) for comparison."""
        g = ins.get("genre") or {}
        if not g.get("detail"):
            return
        mc = self._context(now_s) or {}
        mg = mc.get("genre") if isinstance(mc.get("genre"), dict) else {}
        mml = mc.get("ml") if isinstance(mc.get("ml"), dict) else {}
        self._log_event({"kind": "genre_detail", "detail": g["detail"], "label": g.get("label"),
                         "confidence": g.get("confidence"), "segment_id": ins.get("segment_id"),
                         "ml": {"word": mg.get("word"), "parent": mg.get("parent"), "via": mg.get("via"),
                                "styles_top3": mml.get("styles_top3")} if mc else None})

    def _agree_update(self, ins: dict) -> None:
        g = ins["genre"]
        confident = g["label"] != "unknown" and g["confidence"] >= CONFIDENT and ins["content"] in MUSICAL
        prev = self._prev
        same = (prev is not None and prev["genre"]["label"] == g["label"] and prev["content"] == ins["content"]
                and not ins["track_change"])
        if confident and same and self._agree > 0:
            self._agree += 1
        else:
            self._agree = 1 if confident else 0

    # -- prompts -----------------------------------------------------------------------------------------------------
    def _listen_prompt(self) -> str:
        genres = ", ".join(self.vocab.genre) or "(none: use unknown)"
        moods = ", ".join(self.vocab.music_moods or self.vocab.moods) or "(none: leave the list empty)"
        return (
            "You listen to a short clip of what is playing in a home's drawing room, for the room's lighting and "
            "visuals director: describe it, recommend how the visuals should treat it, and forecast what comes next. "
            "Answer only by calling the tool, briefly.\n\n"
            "Fields:\n"
            "- content: music, speech, mixed (music under talk), silence or noise.\n"
            "- character: the music in at most 12 plain words, e.g. \"soft late-night jazz trio\".\n"
            f"- genre.label: one of {genres}, or unknown. genre.detail: the style in a few words. confidence 0-1.\n"
            f"- mood.words: up to 3 of {moods}. confidence 0-1.\n"
            "- approach: how the visuals should treat this music. intensity 0-1 (0 still and gentle, 1 wild and "
            "intense); change: slow, medium or fast (how often the looks should change); transitions: gentle, lively "
            "or wild; confidence 0-1.\n"
            "- trajectory.state: building, peak, sustaining, releasing, steady or winding_down. confidence 0-1.\n"
            "- ending: how this piece will most likely end: cold (a sudden stop), fade (fades out), segue (runs "
            "straight into the next piece) or unknown.\n"
            "- coming: up to 2 changes you expect, soonest first, else []. kind: chorus, drop, climax, breakdown, "
            "key_lift, band_in, vocal_in, stop, outro, end or new_section. eta_min_s and eta_max_s: when, in seconds "
            "from the clip's end. Your answer reaches the director about 4 s after the clip ends, so forecast only "
            "changes at least 4 s away (eta_min_s >= 4), up to 60 s ahead. signs: what will announce it, from louder, low_in, busier, stop_return, "
            "timbre_change, key_change, drums_in, drums_out, vocal_in, vocal_out, phrase_line. size: small, medium "
            "or big (how big a visual moment it deserves). confidence 0-1.\n"
            "- track_change: true if a different piece starts inside the clip.\n"
            "- why: at most 12 words on what decided this.\n\n"
            "Rules:\n"
            "- Trust the measured numbers in the message (loudness, tempo, key, the timeline and the sections): they "
            "come from the room's own signal processing.\n"
            "- \"unknown\" is always a valid answer, and [] is a valid coming. Don't guess.\n"
            "- Never give tempo, key, BPM or beat numbers yourself. Never name the track, the artist or any lyrics.\n"
            "- Ignore what people say; describe only the sound.\n"
            "- A previous verdict, when given, is a hypothesis and may be wrong.")

    def _now_text(self, t_end: float) -> Optional[str]:
        """README S5.22: what structure knows at the clip's end, for the forecast (its phase, how far into the
        section, a known repeat's length, the probable chorus, an armed song end)."""
        mc = self._context(self._mono())
        sec = mc.get("section") if mc is not None and isinstance(mc.get("section"), dict) else None
        bits = []
        if sec is not None:
            if isinstance(sec.get("phase"), str):
                bits.append(f"phase {sec['phase']}")
            if isinstance(sec.get("bars_in_section"), int):
                exp = sec.get("expected_bars")
                bits.append(f"{sec['bars_in_section']} bars into this section" +
                            (f" (it lasted {exp} bars last time)" if isinstance(exp, int) else ""))
            if sec.get("chorus"):
                bits.append("this section is the probable chorus")
        if self._end_at is not None and self._end_at >= t_end:
            bits.append(f"the player says this piece ends about {self._end_at - t_end:.0f} s after the clip")
        return ("Now, from the room's own analysis: " + "; ".join(bits) + ".") if bits else None

    def _timeline_text(self, t_end: float, clip: Any, lufs: Optional[float]) -> str:
        lines = [f"Measured by the room's DSP. The audio is the last {clip.pcm.shape[0] / clip.fs:.0f} s; its true "
                 f"loudness is {lufs:.1f} LUFS (normalised to {LUFS_TARGET:.0f} LUFS for you)."
                 if lufs is not None else
                 f"Measured by the room's DSP. The audio is the last {clip.pcm.shape[0] / clip.fs:.0f} s; it is "
                 "nearly silent."]
        clock = self._call_or_none(self.ctx.clock) or {}
        bpm = _f(clock.get("bpm"), 0.0) or 0.0
        if bpm > 0 and clock.get("gate") in ("beat", "dmx"):
            lines.append(f"Tempo: {bpm:.1f} bpm (beat clock {clock.get('state')}, confidence "
                         f"{_f(clock.get('conf'), 0.0):.2f}).")
        else:
            lines.append("Tempo: no steady beat measured.")
        h = self._call_or_none(self.ctx.harmony) or {}
        if h.get("key") and (_f(h.get("conf"), 0.0) or 0.0) >= HARMONY_MIN_CONF:
            key = f"{h['key']} {h['mode']}" if h.get("mode") else str(h["key"])
            lines.append(f"Key: {key} (confidence {_f(h.get('conf'), 0.0):.2f}).")
        else:
            lines.append("Key: unclear.")
        if clip.gaps:
            lost = sum(b - a for a, b in clip.gaps)
            lines.append(f"{lost:.1f} s of the clip is silence filled in for lost audio.")
        bins: dict[int, list] = {}
        for t, lu, en, _g, br, on in self._timeline:
            d = t_end - t                         # a sample taken just after the clip's last sound counts as its end
            if d < -TIMELINE_STEP_S:
                continue
            k = max(0, int(d // TIMELINE_STEP_S))
            if k < int(TIMELINE_S / TIMELINE_STEP_S) and lu is not None:
                bins.setdefault(k, []).append((lu, en or 0.0, br or 0.0, on or 0.0))
        if bins:
            lines.append(f"Timeline, oldest first, one row per {TIMELINE_STEP_S:.0f} s (t = seconds before the clip's "
                         "end; lufs short-term; energy, brightness and onset 0-100):")
            lines.append("t lufs energy bright onset")
            for k in sorted(bins, reverse=True):
                rows = bins[k]
                n = len(rows)
                lines.append(f"{-int(k * TIMELINE_STEP_S)} {round(sum(r[0] for r in rows) / n)} "
                             f"{round(100 * sum(r[1] for r in rows) / n)} {round(100 * sum(r[2] for r in rows) / n)} "
                             f"{round(100 * max(r[3] for r in rows))}")
        return "\n".join(lines)

    @staticmethod
    def _prev_text(prev: dict) -> str:
        g = prev["genre"]
        bits = [f"character {prev.get('character') or 'unknown'}",
                f"genre {g['label']}" + (f" ({g['detail']})" if g.get("detail") else ""),
                "moods " + (", ".join(prev["mood"]["words"]) or "none"),
                f"trajectory {prev['trajectory']['state']}", f"ending {prev.get('ending') or 'unknown'}",
                f"content {prev['content']}"]
        return "Previous verdict (a hypothesis, may be wrong): " + "; ".join(bits) + "."

    def _describe_prompt(self) -> str:
        return (
            "You turn a guest's few words about the room's music lights into the Music card's own settings. Answer "
            "only by calling the tool. These are the only settings:\n"
            "- steps: -3 to 3. Negative is calmer, positive is wilder, 0 leaves it.\n"
            "- palette: one palette name from the list, \"art\" for colours taken from the paintings on the screens, "
            "or null to leave it.\n"
            "- moods: up to 3 words from the mood list, or null.\n"
            "- keep: true to keep the current look for a while.\n"
            "- switch_mode: the name of another mode from the list, only when the words clearly ask for a different "
            "kind of evening; never the current mode; else null. It is only offered to the guest, never applied.\n"
            "- reply: one short, friendly sentence (at most 15 words) saying what you changed.")

    def _describe_user(self, text: str, context: dict) -> str:
        pal = "; ".join(f"{n} ({label})" if label != n else n for n, label in _named(context.get("palettes")))
        modes = "; ".join(f"{n} ({label})" if label != n else n for n, label in _named(context.get("modes")))
        moods = ", ".join(self.vocab.moods or [m for m in (context.get("moods") or []) if isinstance(m, str)])
        lines = [f"The guest wrote: {json.dumps(text)}"]
        if context.get("vibe"):
            lines.append(f"Current feel: {context['vibe']}" + (f": {context['persona']}" if context.get("persona")
                                                                 else ""))
        lines += [f"Palettes: {pal or 'none'}", f"Moods: {moods or 'none'}", f"Modes: {modes or 'none'}",
                  f"Current mode: {context.get('current_mode') or 'unknown'}"]
        return "\n".join(lines)

    # -- views and logs ----------------------------------------------------------------------------------------------
    def _first_model(self) -> Optional[str]:
        t = (self._status.get("tasks") or {}).get("listen") or {}
        usable = t.get("usable") or []
        if usable:
            return usable[0]
        models = t.get("models") or []
        return models[0] if models else None

    def _build_view(self, now_s: float) -> dict:
        st = self._status or {}
        nxt = None
        if self._why is None and self._due is not None:
            nxt = round(max(0.0, self._due - now_s), 1)
        budget = (st.get("budgets") or {}).get("listen")
        est = self.estimate_view()
        b = self._beliefs if isinstance(self._beliefs, dict) else {}
        ap = b.get("approach") if isinstance(b.get("approach"), dict) else None
        return {"on": self.on, "mode": self.mode, "effective": self._why is None, "why": self._why,
                "stream": self._stream,
                "next_in_s": nxt, "cadence_s": round(self.interval(), 1), "fast": self._fast,
                "in_flight": len(self._tasks),
                "calls_today": int((st.get("calls_today") or {}).get("listen", 0)),
                "spend_today_usd": round(float((st.get("spend_today_usd") or {}).get("listen", 0.0)), 4),
                "budget_usd": float(budget) if budget is not None else None,
                "model": self._first_model(), "last": self._prev, "errors": self.errors,
                "provider": est["provider"] if est else None,
                "est_evening_usd": est["per_evening_usd"] if est else None, "estimate": est,
                "character": b.get("character") if isinstance(b.get("character"), str) else None,
                "approach": {k: ap.get(k) for k in ("intensity", "change", "transitions", "conf")} if ap else None,
                "armed": self._armed()}

    def _armed(self) -> list:
        """The cue engine's sheet (ctx.cue_sheet; README S5.22), [] without it."""
        got = self._call_or_none(getattr(self.ctx, "cue_sheet", None))
        return [dict(x) for x in got if isinstance(x, dict)] if isinstance(got, list) else []

    def estimate_view(self) -> Optional[dict]:
        """README S5.20/S5.22: what listening costs, shown even while the switch is off: {per_call_usd (a clip_s clip),
        per_hour_usd, per_evening_usd, evening_h, cadence_s (the blend the arithmetic uses), steady_cadence_s,
        fast_cadence_s, fast_share, fast_share_basis ("measured" once an hour of effective listening is behind it, else
        "assumed": show.yaml est_fast_share), clip_s, model, provider (page words: "Google", "OpenAI"), basis:
        "ledger" | "price table"}. Calls an hour = 3600 x ((1 - share) / cadence_s + share / fast_cadence_s); the
        budget's slowing only makes it cheaper. None when no model is usable or its cost isn't known."""
        e = self._estimate
        usd = _f((e or {}).get("usd"))
        if not e or usd is None or not e.get("model"):
            return None
        steady = max(float(self.cfg["cadence_s"]), float(self.cfg["min_gap_s"]))
        fast = max(float(self.cfg["fast_cadence_s"]), float(self.cfg["min_gap_s"]))
        if self._eff_s >= FAST_SHARE_MIN_S:
            share, sbasis = self._fast_s / self._eff_s, "measured"
        else:
            share, sbasis = float(self.cfg["est_fast_share"]), "assumed"
        per_hour_calls = 3600.0 * ((1.0 - share) / steady + share / fast)
        per_hour = usd * per_hour_calls
        hours = float(self.cfg["evening_h"])
        return {"per_call_usd": round(usd, 5), "per_hour_usd": round(per_hour, 3),
                "per_evening_usd": round(per_hour * hours, 2), "evening_h": hours,
                "cadence_s": round(3600.0 / per_hour_calls, 1), "steady_cadence_s": steady, "fast_cadence_s": fast,
                "fast_share": round(share, 3), "fast_share_basis": sbasis, "clip_s": float(self.cfg["clip_s"]),
                "model": e["model"], "provider": e.get("label") or e.get("provider"), "basis": e.get("basis")}

    def _note_models(self) -> None:
        """Logs (memory and LISTENER_LOG) what the listen models resolved to, when it changes (README S5.20)."""
        rows = (((self._status or {}).get("tasks") or {}).get("listen") or {}).get("resolved") or []
        sig = tuple((r.get("configured"), r.get("model"), r.get("via"), r.get("skip")) for r in rows
                    if isinstance(r, dict))
        if sig and sig != self._llm_sig:
            self._llm_sig = sig
            self._log_event({"kind": "models", "resolved": [dict(r) for r in rows if isinstance(r, dict)]})

    def _log_event(self, entry: dict) -> None:
        e = {"at": round(self._wall(), 3), **entry}
        self._log.append(e)
        if self._log_path is None:
            return
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                if self._log_path.stat().st_size > LOG_ROTATE_BYTES:
                    os.replace(self._log_path, self._log_path.with_suffix(".jsonl.1"))
            except OSError:
                pass
            with self._log_path.open("a") as fh:
                fh.write(json.dumps(e, separators=(",", ":"), default=str) + "\n")
        except Exception:                         # noqa: BLE001 - the in-memory log still has it
            pass


def attach(ctx: contract.BrainContext, **kw: Any) -> Listener:
    """The brain's ``listener``. Keyword args go to Listener (__main__ passes ``log_path``; tests pass ``llm``)."""
    return Listener(ctx, **kw)
