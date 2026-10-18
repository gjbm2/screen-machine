"""Auto's senses (docs/SHOW_ENGINE.md §8.9; README "Stage 3 > S3.8", builder B2).

- ``NoteTap``: show-rx's MIDI tap. It logs every accepted note-on in a fixed ring that other threads read.
- ``Situations``: the fixed situation vocabulary, read from show_service's own snapshots. It is a 1 Hz component that
  autopilot.attach builds.
- ``Conditions``: the rule grammar (``all`` / ``any`` / ``not`` / ``for_s``, three-valued) that autopilot.py's
  per-mode rules use.

Nothing here knows a mode, a vibe or a device. The words are a fixed vocabulary, and their inputs are NoteTap,
PianoView, features, clock, harmony, receiver health, the listener's beliefs, RoomView and (Stage 4) the ML worker's
CED readings (``ctx.ml``) and structure's music.context (``ctx.music_context``). Which stream, channel or room target
a word reads is config: show.yaml's ``situations`` section (``midi``, ``music``, ``sonos``, ``quiet``, ``talking``;
``ced`` for the classifier's window and hysteresis), then the rule's own params, then the design defaults below
(starting points, marked as such in GET /api/v1/situations). Test streams never count unless a rule sets
``include_test``: the piano words leave them out, and the audio and listener words read unknown while a test stream is
the one measured (a test played from another room never switches this one).

The mic on demand (README S5.16). A stream the receiver keeps off (health ``off``, or state "off") is never measured:
features and CED readings of it read as missing, so the audio words (quiet, talking, music_audible, energy_above) read
unknown while the mic is off, never "quiet" because it was switched off; piano_playing skips heard_by (as when it is
down).

Stage 4 (README S4.8). ``Situations.classifier`` is CED, from ``ctx.ml``: ``(stream) -> {"music", "speech",
"conversation", "singing", "crowd", "applause", "silence", "stream", "n"}``, the mean of the worker's readings for that
stream (None: the measured one) whose ``t`` is in the last ``situations.ced.window_s`` (3 s; longer while the worker
stretches its cadence: MLView.stale_s), or None when there are none (no worker, a missing model, another stream).
With it, ``talking``, ``quiet`` and ``music_audible`` read CED with hysteresis (a word that holds keeps holding until
its number is back past the threshold by ``situations.ced.hysteresis``); "speech" there is the max over
``situations.talking.speech_words`` (speech, conversation, crowd: babble counts). Without it they read exactly as in
Stage 3. ``genre_in`` reads music.context's genre (the ML's parents through show.yaml ``genre_map``, or the listener's
words) first, then the listener's beliefs: also when the ML leads with a family that has no word, or isn't sure (as the
director does); once confident it stays so until its conf is the CED hysteresis under min_conf.

The listener's words for Auto (README S5.20, 17 Oct): ``mood_in`` (the listener's moods, or the ML's mood tags, include
one of the rule's), ``building`` (structure's section phase is a build, or the listener's trajectory says building) and
``track_changed`` (music.context's track changed within ``within_s``, via Sonos or the sound). Each reads unknown while
nothing listens, and while only a test stream is measured (unless include_test).

The room's light (README S5.19). ``light_below`` / ``light_above`` read ``LightSensor``: show.yaml
``situations.light.url`` (Flask's light sensor route) polled every ``poll_s`` on show-brain's loop (async, a short
timeout: a slow or failed poll never blocks the brain and leaves the last value until it is stale), the median of the
named sensor's samples over ``smooth_s`` (someone walking past the sensor doesn't flip it), with ``hysteresis`` (a word
that holds keeps holding until the level is back past ``lux`` by that fraction). Unknown with no good read for 3 polls,
while the sensor's own newest sample is that old, or when the sensor isn't in the answer. With no url, unknown.

``Conditions.check_nodes`` and ``Conditions.describe`` give the same grammar per node, keyed by the editor's paths
("enter_when.any[1]"): plain-words problems and sentences, and each node's live reading (autopilot.py's rules and check).
"""

from __future__ import annotations

import asyncio
import bisect
import json
import logging
import math
import statistics
import time
from collections import deque
from operator import itemgetter
from datetime import datetime
from typing import Any, Callable, Optional

import aiohttp

from . import contract, vban

log = logging.getLogger("show_service.situations")

UP_STATES = ("streaming", "quiet")      # a stream counts as up in these health states (README S3.8)
FEATURES_FRESH_S = 5.0                  # features older than this are stale
IDLE_NOTE_S = 1.0                       # starting point (show.yaml situations.midi.idle_note_s): piano_idle needs
                                        # no note-on within this long
DAMPER_DOWN = 0.1                       # a damper at or above this is "down" (§8.5 uses < 0.1 as up)
HARMONY_MIN_CONF = 0.3                  # music_audible's DSP guess: a clear key also counts as music
ENERGY_TAU_S = 10.0                     # energy_above's EMA
HISTORY_S = 660.0                       # features samples kept (covers any window_s up to 600 s)
NOTES_KEEP_S = 600.0                    # how far back a tick's note copy reaches
SINCE_FORGET_S = 60.0                   # a since_s tracker not read for this long is dropped
SONOS_PLAYING = ("PLAYING", "TRANSITIONING")
# Stage 4: the CED classifier (show.yaml situations.ced; README S4.8)
CED_WINDOW_S = 3.0                      # README S4.8: the mean of the CED readings whose t is in the last 3 s
CED_HYSTERESIS = 0.05                   # starting point (show.yaml situations.ced.hysteresis): a CED word that holds
                                        # keeps holding until its number is back past its threshold by this much
CONTEXT_STALE_S = 3.0                   # music.context comes at 1 Hz: older than this, genre_in reads the beliefs
# README S5.19: the room's light (show.yaml situations.light)
LIGHT_POLL_S = 10.0                     # starting point (situations.light.poll_s): how often the sensor route is read
LIGHT_SMOOTH_S = 60.0                   # starting point (situations.light.smooth_s): the median over this window
LIGHT_HYSTERESIS = 0.15                 # starting point (situations.light.hysteresis): a fraction of the word's lux
LIGHT_STALE_POLLS = 3                   # no good read for this many polls: unknown
LIGHT_TIMEOUT_S = 2.0                   # one poll's whole budget (never more than half of poll_s)
LIGHT_KEEP = 4096                       # samples kept per sensor at most
LIGHT_MAX_BYTES = 4 << 20               # an answer bigger than this isn't read

# -- the vocabulary ---------------------------------------------------------------------------------------------------
# Param JSON schemas (the room's catalog subset plus x-ui; "nullable" means null is allowed: "unset"). The x-ui hints are
# the room's own (room_service/roomstate/schema.py): label, unit, unset_label, help, widget, ends (a slider's two ends);
# vocabulary() adds required, source, and enum + enum_labels where the choices are known now (the light sensors).
_P = {"widget": "slider"}               # a bounded scale (the page shows a 0..1 one as a percentage)
PARAM_SCHEMAS: dict[str, dict] = {
    "for_s": {"type": "number", "minimum": 0, "x-ui": {"label": "For at least", "unit": "s"}},
    "streams": {"type": "array", "items": {"type": "string"}, "nullable": True,
                "x-ui": {"label": "MIDI streams", "unset_label": "Any live MIDI stream"}},
    "channels": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 16}, "nullable": True,
                 "x-ui": {"label": "MIDI channels", "unset_label": "All channels"}},
    "include_test": {"type": "boolean", "x-ui": {"label": "Count test streams too", "widget": "checkbox",
                                                  "help": "A test played from another room never switches this room "
                                                          "unless this is on"}},
    "window_s": {"type": "number", "minimum": 1, "maximum": 600, "x-ui": {"label": "Within", "unit": "s"}},
    "min_notes": {"type": "integer", "minimum": 1, "x-ui": {"label": "At least this many notes"}},
    "min_span_s": {"type": "number", "minimum": 0, "x-ui": {"label": "Spread over at least", "unit": "s"}},
    "min_pitches": {"type": "integer", "minimum": 1, "maximum": 88, "x-ui": {"label": "Different keys, at least"}},
    "idle_note_s": {"type": "number", "minimum": 0.1, "maximum": 60,
                    "x-ui": {"label": "No note for at least", "unit": "s"}},
    "heard_by": {"type": "string", "nullable": True,
                 "x-ui": {"label": "The mic must hear it too", "unset_label": "Don't check"}},
    "heard_min_lufs": {"type": "number", "minimum": -70, "maximum": 0,
                       "x-ui": {"label": "Mic loudness at least", "unit": "LUFS", **_P,
                                "ends": {"min": "Faint", "max": "Loud"}}},
    "stream": {"type": "string", "nullable": True, "x-ui": {"label": "Audio stream", "unset_label": "Whichever plays"}},
    "min_prob": {"type": "number", "minimum": 0, "maximum": 1,
                 "x-ui": {"label": "Classifier probability", **_P, "ends": {"min": "A hint", "max": "Certain"}}},
    "music_below": {"type": "number", "minimum": 0, "maximum": 1, "nullable": True,
                    "x-ui": {"label": "Music probability under", "unset_label": "The starting point", **_P,
                             "ends": {"min": "No music at all", "max": "Any music"}}},
    "speech_below": {"type": "number", "minimum": 0, "maximum": 1, "nullable": True,
                     "x-ui": {"label": "Speech probability under", "unset_label": "The starting point", **_P,
                              "ends": {"min": "No speech at all", "max": "Any speech"}}},
    "dsp_min_gate": {"type": "number", "minimum": 0, "maximum": 1,
                     "x-ui": {"label": "Loud enough (gate)", **_P, "ends": {"min": "Faint", "max": "Loud"}}},
    "now_playing": {"type": "string", "nullable": True,
                    "x-ui": {"label": "Now-playing target", "unset_label": "No Sonos"}},
    "volume": {"type": "string", "nullable": True, "x-ui": {"label": "Volume target", "unset_label": "No Sonos"}},
    "own_change_s": {"type": "number", "minimum": 0,
                     "x-ui": {"label": "Ignore after the room changed it", "unit": "s"}},
    "genres": {"type": "array", "items": {"type": "string"}, "x-ui": {"label": "Genres", "widget": "vocab:genre"}},
    # README S5.20: the listener's words
    "moods": {"type": "array", "items": {"type": "string"}, "x-ui": {"label": "Moods", "widget": "vocab:moods"}},
    "within_s": {"type": "number", "minimum": 1, "maximum": 3600,
                 "x-ui": {"label": "Within the last", "unit": "s"}},
    "min_conf": {"type": "number", "minimum": 0, "maximum": 1,
                 "x-ui": {"label": "Confidence at least", **_P, "ends": {"min": "A guess", "max": "Sure"}}},
    "value": {"type": "number", "minimum": 0, "maximum": 1,
              "x-ui": {"label": "Energy above", **_P, "ends": {"min": "Calm", "max": "Wild"}}},
    "gate_below": {"type": "number", "minimum": 0, "maximum": 1,
                   "x-ui": {"label": "Quieter than (gate)", **_P, "ends": {"min": "Silent", "max": "Loud"}}},
    "from": {"type": "string", "format": "HH:MM", "x-ui": {"label": "From", "widget": "time"}},
    "to": {"type": "string", "format": "HH:MM", "x-ui": {"label": "To", "widget": "time"}},
    # README S5.19: the room's light
    "lux": {"type": "number", "minimum": 0, "maximum": 100000,
            "x-ui": {"label": "Light level", "unit": "lux",
                     "help": "A room by day is a few hundred lux; a lamp-lit evening is about 10-50"}},
    "sensor": {"type": "string", "nullable": True, "x-ui": {"label": "Light sensor", "unset_label": "The only sensor"}},
}

#: Design defaults: starting points (§8.9), used only where show.yaml's section and the rule both leave a param out.
#: A None here means "unset" (any stream, every channel, no check), never an invented device or stream name.
DESIGN_DEFAULTS: dict[str, Any] = {
    "streams": None, "channels": None, "include_test": False, "window_s": 10, "min_notes": 12, "min_span_s": 6,
    "min_pitches": 4, "idle_note_s": IDLE_NOTE_S, "heard_by": None, "heard_min_lufs": -45, "stream": None,
    "min_prob": 0.5, "dsp_min_gate": 0.3,
    "now_playing": None, "volume": None, "own_change_s": 60, "min_conf": 0.6, "gate_below": 0.05,
    "music_below": None, "speech_below": None,       # quiet: unset = the gate alone; talking: TALKING_MUSIC_BELOW
    "sensor": None,                                  # unset: the light sensor's only sensor (show.yaml names it)
    "within_s": 30,                                  # track_changed: a song that began in the last 30 s is new
}
TALKING_MUSIC_BELOW = 0.5               # starting point (show.yaml situations.talking.music_below; §8.9: speech with
                                        # no music): talking's music threshold when show.yaml and the rule leave it out

_PIANO = ("streams", "channels", "include_test")
_SONOS = ("now_playing", "volume", "own_change_s")
#: id -> label (page words), source (contract.SITUATION_RANK), the show.yaml sections it reads, params, required, and
#: unavailable: what the Auto editor says while the word can't be sensed (README S5.19).
_NO_MIC = "The room mic isn't connected yet"
_NO_PIANO = "The piano isn't connected yet"
_NO_LIGHT = "The light sensor isn't reporting"
WORDS: dict[str, dict] = {
    "piano_playing": {
        "label": "the piano is playing", "source": "midi", "sections": ("midi",), "unavailable": _NO_PIANO,
        "params": _PIANO + ("window_s", "min_notes", "min_span_s", "min_pitches", "heard_by", "heard_min_lufs"),
        "description": "Someone is really playing: enough notes in a short time, spread over a few seconds and "
                       "several keys, so a single key press or a cat on the keys doesn't count. While the room's mic "
                       "is on, it must hear the piano too, so silent practice with headphones never switches."},
    "piano_idle": {
        "label": "the piano has stopped", "source": "midi", "sections": ("midi",), "params": _PIANO + ("idle_note_s",),
        "unavailable": _NO_PIANO,
        "description": "No note has been played for a moment and no key or the sustain pedal is held down. "
                       "\u201cFor at least\u201d is how long the piano has been quiet."},
    "music_audible": {
        "label": "music is playing", "source": "audio", "sections": ("music", "sonos"), "unavailable": _NO_MIC,
        "params": ("stream", "min_prob", "dsp_min_gate", "include_test") + _SONOS,
        "description": "The room's mic hears music, not talk: the music-or-speech listener says music over its last "
                       "few seconds. When that listener isn't running, a steady beat or a clear key, loud enough, "
                       "counts. Sonos playing and loud enough to hear counts too."},
    "sonos_playing": {
        "label": "Sonos is playing", "source": "sonos", "sections": ("sonos",), "params": _SONOS,
        "unavailable": "Sonos isn't connected yet",
        "description": "Sonos says it is playing, isn't muted and has its volume up. Just after the room itself "
                       "changed Sonos (a mode starting), this is left out for a short while, so Auto never reacts "
                       "to its own change."},
    "genre_in": {
        "label": "the music is one of these genres", "source": "listener", "sections": (),
        "params": ("genres", "min_conf", "include_test"), "required": ("genres",),
        "unavailable": "Nothing is listening for the genre yet",
        "description": "The music being heard is confidently one of these genres, from the room's own music "
                       "listener (or the AI listener, if it is on). Unknown until one of them is sure, and while "
                       "only test music is playing."},
    "energy_above": {
        "label": "the music is lively", "source": "audio", "sections": (),
        "params": ("value", "stream", "include_test"), "required": ("value",), "unavailable": _NO_MIC,
        "description": "The music's energy, averaged over about 10 seconds, is above this level (0 calm, 1 "
                       "flat out). Unknown while only test music is playing."},
    "talking": {
        "label": "people are talking", "source": "audio", "sections": ("talking",),
        "params": ("stream", "min_prob", "music_below", "include_test"),
        "unavailable": "The music/speech check isn't running yet",
        "description": "The room's mic hears people talking, with little or no music under it, from the "
                       "music-or-speech listener over its last few seconds. Unknown while that listener isn't "
                       "running."},
    "quiet": {
        "label": "the room is quiet", "source": "audio", "sections": ("quiet",),
        "params": ("stream", "gate_below", "music_below", "speech_below", "include_test"), "unavailable": _NO_MIC,
        "description": "The room's mic is on and hears almost nothing: below the loudness it needs, or only "
                       "background noise (neither music nor talk). Unknown while the mic is off."},
    "between": {
        "label": "it's between these times", "source": "time", "sections": (), "params": ("from", "to"),
        "required": ("from", "to"), "unavailable": "The clock isn't readable",
        "description": "The time of day is between these two times. A \u201cFrom\u201d after the \u201cTo\u201d "
                       "runs past midnight (22:00 to 07:00 is overnight)."},
    # README S5.19: the room's light sensor (show.yaml situations.light)
    "light_below": {
        "label": "the room is darker than", "source": "light", "sections": ("light",), "params": ("lux", "sensor"),
        "required": ("lux",), "unavailable": _NO_LIGHT,
        "description": "The room's light sensor reads less than this, taken over the last minute so someone "
                       "walking past doesn't count. Once true it stays true until the room is clearly brighter "
                       "again (about 15 % more). Unknown while the sensor isn't reporting."},
    "light_above": {
        "label": "the room is brighter than", "source": "light", "sections": ("light",), "params": ("lux", "sensor"),
        "required": ("lux",), "unavailable": _NO_LIGHT,
        "description": "The room's light sensor reads more than this, taken over the last minute so a passing "
                       "shadow doesn't count. Once true it stays true until the room is clearly darker again (about "
                       "15 % less). Unknown while the sensor isn't reporting."},
    # README S5.20 (17 Oct): the listener's words
    "mood_in": {
        "label": "the music feels like one of these", "source": "listener", "sections": (),
        "params": ("moods", "min_conf", "include_test"), "required": ("moods",),
        "unavailable": "Nothing is listening for the mood yet",
        "description": "The music being heard has one of these moods, from the AI listener (when AI listening is on) "
                       "or the room's own music listener. Unknown until one of them is sure of a mood, and "
                       "while only test music is playing."},
    "building": {
        "label": "the music is building", "source": "audio", "sections": (), "params": ("min_conf", "include_test"),
        "unavailable": "Nothing is following the music's shape yet",
        "description": "The music is building up toward a peak: the room's own section detector hears a build, or the "
                       "AI listener says it is building. False while music plays without building; unknown while "
                       "neither is listening, and while only test music is playing."},
    "track_changed": {
        "label": "a new song started", "source": "audio", "sections": (), "params": ("within_s", "include_test"),
        "unavailable": "Nothing is following the songs yet",
        "description": "A new song began within the last few seconds, from Sonos's now-playing or the room hearing "
                       "the change. Unknown while nothing follows the music, and while only test music is playing."},
}
assert tuple(WORDS) == contract.SITUATIONS


def word_label(sid: str, params: Optional[dict] = None) -> str:
    """Page words for one word, with its params where they say more ("the music is jazz or soul")."""
    p = params or {}
    if sid == "genre_in" and isinstance(p.get("genres"), list) and p["genres"]:
        return "the music is " + " or ".join(str(g) for g in p["genres"])
    if sid == "between" and isinstance(p.get("from"), str) and isinstance(p.get("to"), str):
        return f"it's {p['from']}-{p['to']}"
    if sid == "mood_in" and isinstance(p.get("moods"), list) and p["moods"]:
        return "the music feels " + " or ".join(str(m) for m in p["moods"])
    if sid == "track_changed" and isinstance(p.get("within_s"), (int, float)) and not isinstance(p["within_s"], bool):
        return f"a new song started in the last {duration_words(p['within_s'])}"
    if WORDS.get(sid, {}).get("source") == "light":       # "the room is darker than 50 lux"
        lux = p.get("lux")
        n = _fmt(lux) if isinstance(lux, (int, float)) and not isinstance(lux, bool) else "…"
        at = f" (the {p['sensor']} sensor)" if isinstance(p.get("sensor"), str) and p["sensor"] else ""
        return f"{WORDS[sid]['label']} {n} lux{at}"
    return WORDS[sid]["label"] if sid in WORDS else sid


def _fmt(v: float) -> str:
    """A number in page words: 50, 50.5."""
    f = float(v)
    return str(int(f)) if f.is_integer() else f"{f:g}"


def duration_words(s: Any) -> str:
    """for_s in page words: "10 s", "2 min", "1 hour", "90 s"."""
    n = max(0.0, _num(s, 0.0))
    if n >= 3600 and n % 3600 == 0:
        h = int(n // 3600)
        return f"{h} hour" + ("s" if h != 1 else "")
    if n >= 60 and n % 60 == 0:
        return f"{int(n // 60)} min"
    return f"{_fmt(n)} s"


def rank_of(sid: str) -> int:
    return contract.SITUATION_RANK.get(WORDS.get(sid, {}).get("source", ""), 99)


# -- small helpers ------------------------------------------------------------------------------------------------------
def _num(v: Any, default: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        return float(default)
    return float(v)


def _hhmm(v: Any) -> Optional[int]:
    """"HH:MM" -> minutes after midnight, or None."""
    if not isinstance(v, str):
        return None
    parts = v.strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    h, m = int(parts[0]), int(parts[1])
    return h * 60 + m if 0 <= h <= 23 and 0 <= m <= 59 else None


def in_window(minutes: float, start: int, end: int) -> bool:
    """Local minutes in [start, end); start > end wraps midnight; start == end is never."""
    if start == end:
        return False
    if start < end:
        return start <= minutes < end
    return minutes >= start or minutes < end


def _is_test(name: str, info: Optional[dict] = None) -> bool:
    """The contract's test rule (receiver.py marks the same): a known test stream or any ``test-*``."""
    return bool((info or {}).get("test")) or name in contract.TEST_STREAMS or name.startswith("test-")


def check_param(name: str, value: Any) -> Optional[str]:
    """A problem with one param value against PARAM_SCHEMAS, or None."""
    sch = PARAM_SCHEMAS.get(name)
    if sch is None:
        return "unknown param"
    if value is None:
        return None if sch.get("nullable") else "can't be null"
    t = sch.get("type")

    def num_ok(v: Any, s: dict) -> Optional[str]:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            return "must be a number"
        if s.get("type") == "integer" and int(v) != v:
            return "must be a whole number"
        if "minimum" in s and v < s["minimum"]:
            return f"must be at least {s['minimum']}"
        if "maximum" in s and v > s["maximum"]:
            return f"must be at most {s['maximum']}"
        return None

    if t in ("number", "integer"):
        return num_ok(value, sch)
    if t == "boolean":
        return None if isinstance(value, bool) else "must be true or false"
    if t == "string":
        if not isinstance(value, str) or not value:
            return "must be a non-empty string"
        if sch.get("format") == "HH:MM" and _hhmm(value) is None:
            return 'must be "HH:MM" (24-hour)'
        return None
    if t == "array":
        if not isinstance(value, list):
            return "must be a list"
        it = sch.get("items") or {}
        for x in value:
            if it.get("type") == "string" and not (isinstance(x, str) and x):
                return "must be a list of names"
            if it.get("type") == "integer" and (err := num_ok(x, it)):
                return f"items {err}"
        return None
    return None


def _reading(value: Optional[bool], via: str, evidence: dict, since: Optional[float] = None) -> contract.SituationReading:
    return {"value": value, "since_s": since, "via": via, "evidence": evidence}


# -- NoteTap (show-rx writer, any-thread reader) --------------------------------------------------------------------------
class NoteTap:
    """Every accepted note-on, in a fixed ring. ``on_midi`` runs on show-rx (contract.OnMidi). ``since`` runs on any
    thread: it copies the ring between two reads of the write counter and keeps only the entries no write can have
    overwritten meanwhile (it never iterates a container show-rx appends to)."""

    def __init__(self, size: int = 4096) -> None:
        self._size = max(16, int(size))
        self._ring: list[Optional[tuple]] = [None] * self._size
        self._n = 0                                       # writes so far; slot = index % size, written before +1
        self._last: dict[str, tuple[int, Optional[str]]] = {}
        self._batches = self._notes = self._old = self._restarts = 0
        self._per: dict[str, int] = {}
        self._last_t: dict[tuple[str, int], float] = {}   # (stream, channel) -> the newest note-on's arrival

    def on_midi(self, stream: str, msg: Any, arrival_s: float, to_host: Callable[[int], float]) -> None:
        ev = getattr(msg, "ev", None)
        if ev is None:                                    # a MidiState: states never count
            return
        last = self._last.get(stream)
        verdict = vban.midi_seq_verdict(msg.seq, msg.sess, last[0] if last else None, last[1] if last else None)
        if verdict == "old":
            self._old += 1
            return
        if verdict == "restart":
            self._restarts += 1
        self._last[stream] = (msg.seq, msg.sess)
        self._batches += 1
        n = 0
        size, ring = self._size, self._ring
        for _t, status, d1, d2 in ev:                     # prev is not replayed (a missed batch costs a few notes)
            if status & 0xF0 == 0x90 and d2 > 0:
                i = self._n
                ch = (status & 0x0F) + 1
                ring[i % size] = (arrival_s, stream, ch, d1, d2)
                self._n = i + 1
                self._last_t[(stream, ch)] = arrival_s
                n += 1
        if n:
            self._notes += n
            self._per[stream] = self._per.get(stream, 0) + n

    def raw_since(self, t_s: float) -> list[tuple]:
        """(t_s, stream, channel, pitch, vel) tuples with t_s >= ``t_s``, oldest first (any thread). Entries are in
        arrival order (one writer, one monotonic clock), so the cut is a bisect."""
        n1 = self._n
        buf = self._ring[:]                               # one C-level copy
        n2 = self._n
        lo = max(0, n2 + 1 - self._size)                  # a write in progress may have taken slot n2 already
        if n1 <= lo:
            return []
        a, b = lo % self._size, n1 % self._size
        out = buf[a:b] if a < b else buf[a:] + buf[:b]
        if out and out[0][0] < t_s:
            out = out[bisect.bisect_left(out, t_s, key=itemgetter(0)):]
        return out

    def last_note_times(self) -> dict[tuple[str, int], float]:
        """(stream, channel) -> the newest note-on's arrival time (any thread)."""
        for _ in range(3):
            try:
                return dict(self._last_t)
            except RuntimeError:                          # resized during the copy: try again
                continue
        return {}

    def since(self, t_s: float) -> list[contract.NoteOn]:
        return [{"t_s": e[0], "stream": e[1], "channel": e[2], "pitch": e[3], "vel": e[4]} for e in self.raw_since(t_s)]

    def stats(self) -> dict:
        for _ in range(3):
            try:
                per = dict(self._per)
                break
            except RuntimeError:                          # resized during the copy: try again
                per = {}
        return {"batches": self._batches, "notes": self._notes, "old": self._old, "restarts": self._restarts,
                "streams": per}


# -- LightSensor (show-brain: an asyncio task; README S5.19) ---------------------------------------------------------------
def _fnum(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        return None
    return float(v)


def friendly(name: str) -> str:
    """A config name in page words: "living-room" -> "Living room"."""
    t = name.replace("-", " ").replace("_", " ").strip()
    return t[:1].upper() + t[1:] if t else name


class LightSensor:
    """The room's light level, from show.yaml ``situations.light``: ``url`` (Flask's light sensor route: JSON
    ``{"sensors": {name: {"current": {"lux"}, "history": [{"timestamp", "lux"}, ...]}}}``), ``sensor`` (the one the
    light words read when a rule names none; unset: the answer's only sensor), ``poll_s``, ``smooth_s`` and
    ``hysteresis`` (read by the words). Polled on show-brain's loop: one GET at a time, ``LIGHT_TIMEOUT_S`` at most (never
    more than half of poll_s), so the brain never waits on it. A failed poll changes nothing: the last value holds until
    it is stale. ``ingest`` and ``reading`` are plain calls (tests drive them without a socket).

    Samples are the answer's history (the sensor's own timestamps), else its current lux as of the poll. A sensor's read
    is good when its newest sample is no older than stale_s (3 polls); a sensor with no good read for stale_s, or not in
    the last answer, reads unknown."""

    def __init__(self, *, mono: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        self._mono, self._wall = mono, wall
        self.url: Optional[str] = None
        self.sensor: Optional[str] = None
        self.poll_s, self.smooth_s, self.hysteresis = LIGHT_POLL_S, LIGHT_SMOOTH_S, LIGHT_HYSTERESIS
        self._samples: dict[str, deque] = {}              # sensor -> deque[(wall t, lux)], oldest first
        self._good: dict[str, float] = {}                 # sensor -> mono of its last good read
        self._quiet: dict[str, float] = {}                # sensor -> how old its newest sample was (a silent sensor)
        self._names: list[str] = []                       # the sensors in the last good answer
        self._answered = False                            # a good answer has come since the url was set
        self.error: Optional[str] = None
        self.stats = {"polls": 0, "ok": 0, "failed": 0}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None

    # -- config ------------------------------------------------------------------------------------------------------
    def configure(self, sec: Any) -> list[str]:
        """show.yaml ``situations.light`` -> problems (a bad value keeps its starting point). Starts or stops the poll
        when the url comes or goes."""
        probs: list[str] = []
        sec = sec if isinstance(sec, dict) else {}
        url = sec.get("url")
        if url is not None and not (isinstance(url, str) and url.startswith(("http://", "https://"))):
            probs.append(f"situations.light.url: {url!r} is not an http(s) URL: the light words read unknown")
            url = None
        sensor = sec.get("sensor")
        if sensor is not None and not (isinstance(sensor, str) and sensor):
            probs.append(f"situations.light.sensor: {sensor!r} is not a sensor name: unset (the only sensor)")
            sensor = None

        def num(key: str, lo: float, hi: float, d: float) -> float:
            v = sec.get(key)
            if v is None:
                return d
            n = _fnum(v)
            if n is None or not lo <= n <= hi:
                probs.append(f"situations.light.{key}: {v!r} is not {lo:g}..{hi:g}: {d:g} used")
                return d
            return n

        poll = num("poll_s", 1.0, 600.0, LIGHT_POLL_S)
        smooth = num("smooth_s", 0.0, 3600.0, LIGHT_SMOOTH_S)
        hyst = num("hysteresis", 0.0, 1.0, LIGHT_HYSTERESIS)
        if url != self.url:
            self._samples.clear()
            self._good.clear()
            self._quiet.clear()
            self._names, self._answered, self.error = [], False, None
        self.url, self.sensor, self.poll_s, self.smooth_s, self.hysteresis = url, sensor, poll, smooth, hyst
        self._ensure()
        return probs

    def stale_s(self) -> float:
        return LIGHT_STALE_POLLS * self.poll_s

    # -- the poll (show-brain's loop) --------------------------------------------------------------------------------
    def start(self, loop: Optional[asyncio.AbstractEventLoop]) -> None:
        self._loop = loop
        self._ensure()

    def _ensure(self) -> None:
        loop = self._loop
        want = self.url is not None and loop is not None and not loop.is_closed()
        if want and self._task is None:
            self._task = loop.create_task(self._run(), name="show-light")
        elif not want and self._task is not None:
            self._task.cancel()
            self._task = None

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        s, self._session = self._session, None
        loop = self._loop
        if s is not None and not s.closed and loop is not None and not loop.is_closed():
            try:
                loop.create_task(s.close())
            except RuntimeError:
                pass

    async def _run(self) -> None:
        try:
            while True:
                await self.poll_once()
                await asyncio.sleep(self.poll_s)
        finally:
            s, self._session = self._session, None
            if s is not None and not s.closed:
                try:
                    await asyncio.shield(s.close())
                except BaseException:
                    pass

    async def poll_once(self) -> bool:
        """One GET of ``url``; True when its answer was taken. Never raises but for cancellation."""
        url = self.url
        if not url:
            return False
        self.stats["polls"] += 1
        try:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
            budget = aiohttp.ClientTimeout(total=min(LIGHT_TIMEOUT_S, self.poll_s / 2.0))
            async with self._session.get(url, timeout=budget) as r:
                if r.status != 200:
                    raise ValueError(f"answered {r.status}")
                chunks, size = [], 0
                async for chunk in r.content.iter_chunked(1 << 16):
                    size += len(chunk)
                    if size > LIGHT_MAX_BYTES:
                        raise ValueError("the answer is too big")
                    chunks.append(chunk)
            obj = json.loads(b"".join(chunks))
        except asyncio.CancelledError:
            raise
        except Exception as e:                            # the last value holds until it is stale
            self.stats["failed"] += 1
            self.error = f"GET {url}: {type(e).__name__}: {e}"[:300]
            return False
        if url != self.url:                               # the url changed meanwhile: not this one's answer
            return False
        return self.ingest(obj)

    def ingest(self, obj: Any) -> bool:
        """One answer from the sensor route -> samples. True when it was an answer (even one without our sensor)."""
        sensors = obj.get("sensors") if isinstance(obj, dict) else None
        if not isinstance(sensors, dict):
            self.stats["failed"] += 1
            self.error = "the light sensor's answer has no sensors"
            return False
        now_m, now_w, stale = self._mono(), self._wall(), self.stale_s()
        names = []
        for name, s in sensors.items():
            if not isinstance(name, str) or not name or not isinstance(s, dict):
                continue
            names.append(name)
            pts = []
            for h in s.get("history") if isinstance(s.get("history"), list) else ():
                if isinstance(h, dict):
                    t, lux = _fnum(h.get("timestamp")), _fnum(h.get("lux"))
                    if t is not None and lux is not None and lux >= 0:
                        pts.append((t, lux))
            if not pts:                                   # no timestamps: the current lux, as of this poll
                cur = s.get("current") if isinstance(s.get("current"), dict) else {}
                lux = _fnum(cur.get("lux"))
                if lux is None or lux < 0:
                    continue
                pts = [(now_w, lux)]
            pts.sort(key=itemgetter(0))
            dq = self._samples.setdefault(name, deque())
            last_t = dq[-1][0] if dq else -math.inf
            dq.extend(p for p in pts if p[0] > last_t)
            newest = dq[-1][0]
            cut = newest - self.smooth_s - 1.0
            while dq and (dq[0][0] < cut or len(dq) > LIGHT_KEEP):
                dq.popleft()
            quiet = now_w - newest
            if quiet > stale:                             # the route answers, but the sensor itself went silent
                self._quiet[name] = quiet
                continue
            self._quiet.pop(name, None)
            self._good[name] = now_m
        self._names = sorted(names)
        self._answered = True
        self.stats["ok"] += 1
        self.error = None
        return True

    # -- reading (show-brain) ----------------------------------------------------------------------------------------
    def choices(self) -> list[str]:
        """The sensors to offer: the last answer's, and show.yaml's own (never an invented one)."""
        return sorted(set(self._names) | ({self.sensor} if self.sensor else set()))

    def which(self, sensor: Optional[str]) -> tuple[Optional[str], Optional[dict]]:
        """(the sensor a word reads, None) or (None, why not)."""
        if not self.url:
            return None, {"why": "no light sensor is set (show.yaml situations.light.url)"}
        name = sensor or self.sensor
        if name:
            return name, None
        if len(self._names) == 1:
            return self._names[0], None
        if not self._names:
            return None, {"why": "no light reading yet", "error": self.error}
        return None, {"why": "say which sensor (show.yaml situations.light.sensor)", "sensors": list(self._names)}

    def reading(self, sensor: Optional[str] = None) -> tuple[Optional[float], dict]:
        """(the median lux over smooth_s, evidence), or (None, why) while it can't be read now."""
        name, why = self.which(sensor)
        if name is None:
            return None, why or {}
        ev: dict = {"sensor": name}
        if self._answered and name not in self._names:
            return None, {**ev, "why": f"the light sensor has no {name!r}", "sensors": list(self._names)}
        good = self._good.get(name)
        age = None if good is None else self._mono() - good
        if age is None or age > self.stale_s():
            q = self._quiet.get(name)
            what = (f"the sensor hasn't reported for {q:.0f} s" if q is not None else
                    "no light reading yet" if age is None else f"no good light reading for {age:.0f} s")
            return None, {**ev, "why": what, "error": self.error}
        dq = self._samples.get(name) or deque()
        if not dq:
            return None, {**ev, "why": "no light reading yet"}
        t0 = dq[-1][0] - self.smooth_s
        vals = [lux for t, lux in dq if t >= t0]
        med = float(statistics.median(vals))
        return med, {**ev, "level": round(med, 1), "last": round(dq[-1][1], 1), "samples": len(vals),
                     "smooth_s": self.smooth_s, "read_age_s": round(age, 1)}

    def view(self) -> dict:
        lvl, ev = self.reading(None) if self.url else (None, {"why": "off"})
        return {"url": self.url, "sensor": self.sensor, "sensors": list(self._names), "level": lvl,
                "why": None if lvl is not None else ev.get("why"), "poll_s": self.poll_s, "smooth_s": self.smooth_s,
                "hysteresis": self.hysteresis, "error": self.error, "stats": dict(self.stats)}


# -- Situations (component "situations", 1 Hz) ----------------------------------------------------------------------------
class Situations:
    """Reads the vocabulary. ``read`` evaluates one word with a rule's params on top of show.yaml's; ``tick`` samples
    the features history and refreshes the snapshot (every word with show.yaml's params)."""

    name = "situations"
    tick_hz = contract.SLOW_TICK_HZ

    def __init__(self, ctx: contract.BrainContext, *, local: Optional[Callable[[], datetime]] = None,
                 mono: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        self.ctx = ctx
        self._local = local or datetime.now
        self._mono = mono
        self.light = LightSensor(mono=mono, wall=wall)    # README S5.19: show.yaml situations.light
        self.last_now: Optional[float] = None             # the last tick's now_s (autopilot's rules read at it)
        # CED (Stage 4): (stream) -> {CED_GROUPS word: 0..1, "stream", "n"} | None. Bound to ctx.ml at start (or the
        # first tick); a test may set its own.
        self.classifier: Optional[Callable[[Optional[str]], Optional[dict]]] = None
        self._ced_cfg: dict = {"window_s": CED_WINDOW_S, "hysteresis": CED_HYSTERESIS}
        self._speech_words: tuple[str, ...] = contract.SPEECH_WORDS   # situations.talking.speech_words
        self._results_stale_s = CED_WINDOW_S              # show.yaml ml.results_stale_s (the CED window's floor too)
        self._prev: Optional[bool] = None                 # the reading's previous value while a reader runs (hysteresis)
        self.problems: list[str] = []
        self._section: dict = {}
        self.revision = 0                                 # +1 per configure (Conditions re-checks rules)
        self._hist: deque = deque()                       # (t_s, stream, lufs_s, gate)
        self._ema: dict[str, tuple[float, float]] = {}    # stream -> (t_s, energy EMA)
        self._since: dict[str, list] = {}                 # key -> [value, since_t, last_read_t]
        self._notes_cache: tuple[Optional[float], list] = (None, [])   # (now_s, notes)
        self._memo_t: Optional[float] = None              # readings memoised within one now_s (rules share leaves)
        self._memo: dict[str, contract.SituationReading] = {}
        self._snap: dict = {"readings": {}, "vocabulary": []}

    # -- Component ------------------------------------------------------------------------------------------------------
    def provides(self) -> dict:
        return {"situations": lambda: self._snap["readings"]}

    def configure(self, settings: dict) -> None:
        s = (settings or {}).get("situations") if isinstance(settings, dict) else None
        self._section = s if isinstance(s, dict) else {}
        ced = self._section.get("ced")
        cfg, probs = {"window_s": CED_WINDOW_S, "hysteresis": CED_HYSTERESIS}, []
        if isinstance(ced, dict):
            w, h = ced.get("window_s"), ced.get("hysteresis")
            if w is not None:
                if _num(w, -1.0) > 0:
                    cfg["window_s"] = float(w)
                else:
                    probs.append(f"situations.ced.window_s: {w!r} is not a positive number: {CED_WINDOW_S} used")
            if h is not None:
                if 0.0 <= _num(h, -1.0) <= 0.5:
                    cfg["hysteresis"] = float(h)
                else:
                    probs.append(f"situations.ced.hysteresis: {h!r} is not 0..0.5: {CED_HYSTERESIS} used")
        elif ced is not None:
            probs.append("situations.ced: not a mapping")
        talk = self._section.get("talking") if isinstance(self._section.get("talking"), dict) else {}
        words, prob = contract.speech_words(talk.get("speech_words"), "situations.talking.speech_words")
        if prob:
            probs.append(prob)
        ml = (settings or {}).get("ml") if isinstance(settings, dict) else None
        rs = _num(ml.get("results_stale_s"), -1.0) if isinstance(ml, dict) else -1.0
        self._results_stale_s = rs if rs > 0 else CED_WINDOW_S
        light = self._section.get("light")
        if light is not None and not isinstance(light, dict):
            probs.append("situations.light: not a mapping")
        probs.extend(self.light.configure(light))
        self._speech_words = words
        self._ced_cfg, self.problems = cfg, probs
        self.revision += 1
        self._memo.clear()

    def restore(self, saved: dict) -> None:
        pass                                              # situation timers start cold (README S3.4)

    def start(self) -> None:
        self._bind_classifier()
        loop = self.ctx.loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None                               # no loop (a test): the light is read only by hand
        self.light.start(loop)

    def stop(self) -> None:
        self.light.stop()

    def persist(self) -> None:
        return None

    def _bind_classifier(self) -> None:
        """README S4.8: the classifier comes from ctx.ml (the ML worker's view) when that slot is filled."""
        if self.classifier is None and getattr(self.ctx, "ml", None) is not None:
            self.classifier = self._ced

    def tick(self, now_s: float) -> None:
        self._bind_classifier()
        self.last_now = now_s
        self._sample(now_s)
        readings = {}
        for sid in WORDS:
            p = self.params(sid)
            missing = [k for k in WORDS[sid].get("required", ()) if p.get(k) is None]
            readings[sid] = (_reading(None, "config", {"why": f"needs {', '.join(missing)} (set in a rule)"})
                             if missing else self.read(sid, None, now_s))
        cut = now_s - SINCE_FORGET_S
        for k in [k for k, v in self._since.items() if v[2] < cut]:
            del self._since[k]
        self._snap = {"readings": readings, "vocabulary": self.vocabulary(), "classifier": self._ced_view(now_s),
                      "light": self.light.view(), "problems": list(self.problems)}

    def snapshot(self) -> dict:
        return self._snap

    # -- the CED classifier (Stage 4) -------------------------------------------------------------------------------------
    def _ced(self, stream: Optional[str], now_s: Optional[float] = None) -> Optional[dict]:
        """The mean of ctx.ml's CED readings for ``stream`` (None: the stream the worker measures) with ``t`` in the
        last ``situations.ced.window_s``; None when there are none."""
        v = self._call(getattr(self.ctx, "ml", None))
        if not isinstance(v, dict):
            return None
        vstream = v.get("stream")
        if stream and vstream != stream:
            return None
        now = self._mono() if now_s is None else now_s
        # the window follows the worker's own freshness (MLView.stale_s): while its CPU guard stretches CED's cadence
        # to 3-8 s, a fixed 3 s would often hold no reading and the words would flap between CED and the DSP guess
        window = max(float(self._ced_cfg["window_s"]), contract.ml_stale_s(v, "ced", self._results_stale_s))
        t_min = now - window
        rows = [r for r in (v.get("ced_recent") or []) if isinstance(r, dict) and _num(r.get("t"), -math.inf) >= t_min
                and r.get("stream", vstream) == vstream]
        if not rows:
            return None
        out: dict[str, Any] = {w: sum(_num(r.get(f"{w}_p"), 0.0) for r in rows) / len(rows)
                               for w in contract.CED_GROUPS}
        out["stream"] = vstream
        out["n"] = len(rows)
        return out

    def _speech(self, c: Any) -> Optional[float]:
        """A classifier reading's speech: the max over situations.talking.speech_words (babble and conversation
        count, not only Speech)."""
        return contract.speech_of(c, self._speech_words)

    def _classify(self, stream: Optional[str], now_s: float) -> Optional[dict]:
        fn = self.classifier
        if fn is None:
            return None
        if fn == self._ced:                               # ours: read at the reading's own now_s
            c = self._call(lambda: self._ced(stream, now_s))
        else:
            c = self._call(lambda: fn(stream))
        if isinstance(c, dict) and (c.get("stream") in self._off() or (stream and stream in self._off())):
            return None                                   # the mic went off: what it heard is not now
        return c

    def _ced_view(self, now_s: float) -> dict:
        c = self._classify(None, now_s) if self.classifier is not None else None
        return {"on": self.classifier is not None, "window_s": self._ced_cfg["window_s"],
                "hysteresis": self._ced_cfg["hysteresis"], "speech_words": list(self._speech_words),
                "now": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in c.items()} if c else None}

    def _hyst(self) -> float:
        """The hysteresis margin for the reading being made: the CED margin while it holds (it was True), else 0."""
        return float(self._ced_cfg["hysteresis"]) if self._prev is True else 0.0

    # -- the vocabulary -------------------------------------------------------------------------------------------------
    def params(self, sid: str, override: Optional[dict] = None) -> dict:
        """The word's params: design defaults, then show.yaml's sections, then ``override`` (the rule's)."""
        w = WORDS.get(sid)
        if w is None:
            return dict(override or {})
        names = w["params"]
        out = {k: DESIGN_DEFAULTS[k] for k in names if k in DESIGN_DEFAULTS}
        for sec in w["sections"]:
            s = self._section.get(sec)
            if isinstance(s, dict):
                out.update({k: v for k, v in s.items() if k in names})
        if override:
            out.update({k: v for k, v in override.items() if k in names})
        return out

    def _param_source(self, sid: str, k: str) -> str:
        for sec in WORDS[sid]["sections"]:
            s = self._section.get(sec)
            if isinstance(s, dict) and k in s:
                return f"show.yaml situations.{sec}"
        return "design default (starting point)" if k in DESIGN_DEFAULTS else "set in the rule"

    def _choices(self, k: str) -> list[str]:
        """The values a param can take right now, where they are known (the light sensors), else []."""
        return self.light.choices() if k == "sensor" else []

    def vocabulary(self) -> list[contract.SituationDef]:
        out = []
        for sid, w in WORDS.items():
            p = self.params(sid)
            params = {"for_s": {**PARAM_SCHEMAS["for_s"], "default": 0,
                                "x-ui": {**PARAM_SCHEMAS["for_s"]["x-ui"], "required": False}}}
            for k in w["params"]:
                sch = {**PARAM_SCHEMAS[k], "x-ui": {**PARAM_SCHEMAS[k].get("x-ui", {}),
                                                    "source": self._param_source(sid, k),
                                                    "required": k in w.get("required", ())}}
                if k in p:
                    sch["default"] = p[k]
                choices = self._choices(k)
                if choices:
                    sch["enum"] = choices
                    sch["x-ui"].update(enum_labels=[friendly(c) for c in choices], widget="select")
                params[k] = sch
            out.append({"id": sid, "label": w["label"], "description": w["description"], "source": w["source"],
                        "rank": rank_of(sid), "params": params, "available": self._available(sid, p),
                        "unavailable": w.get("unavailable") or "It can't be sensed right now"})
        return out

    def available(self, sid: str, params: Optional[dict] = None) -> bool:
        """The word's inputs exist now, with a rule's params on show.yaml's (False: it reads unknown)."""
        if sid not in WORDS:
            return False
        try:
            return bool(self._available(sid, self.params(sid, params)))
        except Exception:
            return False

    def _available(self, sid: str, p: dict) -> bool:
        c = self.ctx
        if sid in ("piano_playing", "piano_idle"):
            streams, _ = self._health()
            return c.notes is not None and bool(self._piano_streams(p, streams) & set(streams))
        if sid == "sonos_playing":
            return self._sonos(p)[0] is not None or bool(p.get("now_playing") and p.get("volume"))
        if sid == "music_audible":
            return c.features is not None or bool(p.get("now_playing"))
        if sid in ("genre_in", "mood_in", "building"):
            return c.beliefs is not None or getattr(c, "music_context", None) is not None
        if sid == "track_changed":
            return getattr(c, "music_context", None) is not None
        if sid in ("energy_above", "quiet"):
            return c.features is not None
        if sid == "talking":
            return self.classifier is not None
        if WORDS[sid]["source"] == "light":
            return self.light.reading(p.get("sensor") if isinstance(p.get("sensor"), str) else None)[0] is not None
        return True

    # -- reading one word -----------------------------------------------------------------------------------------------
    def read(self, sid: str, params: Optional[dict], now_s: float) -> contract.SituationReading:
        if sid not in WORDS:
            return _reading(None, "config", {"why": f"unknown situation {sid!r}"})
        key = sid + json.dumps(params or {}, sort_keys=True, default=str)
        if self._memo_t != now_s:
            self._memo_t, self._memo = now_s, {}
        hit = self._memo.get(key)
        if hit is not None:
            return hit
        p = self.params(sid, params)
        t = self._since.get(key)
        self._prev = t[0] if t is not None else None      # what this reading said last (the CED words' hysteresis)
        try:
            value, via, ev = getattr(self, "_r_" + sid)(p, now_s)
        except Exception as e:                            # a reader bug reads unknown, never breaks Auto
            log.debug("situation %s failed", sid, exc_info=True)
            value, via, ev = None, "error", {"error": f"{type(e).__name__}: {e}"}
        finally:
            self._prev = None
        if t is None or t[0] != value:
            t = self._since[key] = [value, now_s, now_s]
        t[2] = now_s
        r = self._memo[key] = _reading(value, via, ev, round(now_s - t[1], 1))
        return r

    # inputs
    def _health(self) -> tuple[dict, Optional[str]]:
        try:
            h = self.ctx.health() if self.ctx.health else None
        except Exception:
            h = None
        h = h if isinstance(h, dict) else {}
        s = h.get("streams")
        return (s if isinstance(s, dict) else {}), h.get("active_audio")

    @staticmethod
    def _up(streams: dict, name: Optional[str]) -> bool:
        return bool(name) and (streams.get(name) or {}).get("state") in UP_STATES

    def _off(self) -> set[str]:
        """The streams the mic on demand keeps off now (README S5.16): receiver health's ``off`` and any stream whose
        state is "off". Never measured."""
        try:
            h = self.ctx.health() if self.ctx.health else None
        except Exception:
            h = None
        h = h if isinstance(h, dict) else {}
        out = {x for x in h.get("off") or () if isinstance(x, str)} if isinstance(h.get("off"), list) else set()
        streams = h.get("streams") if isinstance(h.get("streams"), dict) else {}
        out.update(n for n, i in streams.items() if isinstance(i, dict) and i.get("state") == "off")
        return out

    def _features(self, now_s: float) -> Optional[dict]:
        """The latest features if fresh (<= 5 s) and not from a stream the mic keeps off, else None."""
        try:
            f = self.ctx.features() if self.ctx.features else None
        except Exception:
            return None
        if not isinstance(f, dict) or not isinstance(f.get("t_ms"), (int, float)):
            return None
        if now_s - f["t_ms"] / 1000.0 > FEATURES_FRESH_S:
            return None
        return None if f.get("stream") in self._off() else f

    def _call(self, slot: Optional[Callable[[], Any]]) -> Any:
        try:
            return slot() if slot else None
        except Exception:
            return None

    def _notes(self, now_s: float) -> list[tuple]:
        """The tap's notes for this now_s (copied once per tick), time-sorted for bisecting."""
        t, notes = self._notes_cache
        if t != now_s:
            tap = self.ctx.notes
            notes = tap.raw_since(now_s - NOTES_KEEP_S) if tap is not None else []
            notes.sort(key=itemgetter(0))                 # already in order on show-rx's clock: O(n)
            self._notes_cache = (now_s, notes)
        return notes

    def _sample(self, now_s: float) -> None:
        f = self._features(now_s)
        if f is not None:
            stream = f.get("stream")
            self._hist.append((now_s, stream, _num(f.get("lufs_s"), -70.0), _num(f.get("gate"), 0.0)))
            e = _num(f.get("energy"), 0.0)
            prev = self._ema.get(stream)
            if prev is None or now_s - prev[0] > 3 * ENERGY_TAU_S:
                self._ema[stream] = (now_s, e)
            else:
                a = 1.0 - math.exp(-max(0.0, now_s - prev[0]) / ENERGY_TAU_S)
                self._ema[stream] = (now_s, prev[1] + a * (e - prev[1]))
        cut = now_s - HISTORY_S
        while self._hist and self._hist[0][0] < cut:
            self._hist.popleft()

    def _test_measured(self, p: dict, f: Optional[dict]) -> Optional[dict]:
        """Evidence when the sound being measured (the features' stream, else the receiver's active audio) is a test
        stream and the rule doesn't set include_test, else None: a test played from another room never switches
        this one."""
        if p.get("include_test"):
            return None
        streams, active = self._health()
        name = (f or {}).get("stream") or active
        if isinstance(name, str) and name and _is_test(name, streams.get(name)):
            return {"why": f"{name} is a test stream (include_test is off)", "stream": name}
        return None

    def _piano_streams(self, p: dict, streams: dict) -> set[str]:
        listed = p.get("streams")
        if isinstance(listed, list) and listed:
            names = {str(s) for s in listed}
        else:                                             # unset: every live (non-test) MIDI stream
            names = {n for n, i in streams.items() if (i or {}).get("kind") == "midi" and not _is_test(n, i)}
        if p.get("include_test"):
            names |= {n for n, i in streams.items() if (i or {}).get("kind") == "midi" and _is_test(n, i)}
        return names

    @staticmethod
    def _channels(p: dict) -> Optional[set[int]]:
        ch = p.get("channels")
        return {int(c) for c in ch if isinstance(c, (int, float)) and not isinstance(c, bool)} if (
            isinstance(ch, list) and ch) else None

    def _own_change(self, p: dict) -> Optional[dict]:
        """Evidence when the room itself changed the Sonos targets within own_change_s (the mic and Sonos readings are
        then the room's own doing, §8.9 "Never react to the room's own actions"), else None."""
        targets = [t for t in (p.get("now_playing"), p.get("volume")) if isinstance(t, str) and t]
        if not targets:
            return None
        room = self._call(self.ctx.room)
        if not isinstance(room, dict) or not room.get("reachable"):
            return None
        own = _num(p.get("own_change_s"), DESIGN_DEFAULTS["own_change_s"])
        for t in targets:
            sa = ((room.get("members") or {}).get(t) or {}).get("sent_age_s")
            if isinstance(sa, (int, float)) and sa < own:
                return {"why": f"the room changed {t} {sa:.0f} s ago", "sent_age_s": sa, "own_change_s": own}
        return None

    def _sonos(self, p: dict) -> tuple[Optional[bool], dict]:
        np_t, vol_t = p.get("now_playing"), p.get("volume")
        if not (isinstance(np_t, str) and np_t and isinstance(vol_t, str) and vol_t):
            return None, {"why": "no Sonos targets set (show.yaml situations.sonos)"}
        room = self._call(self.ctx.room)
        if not isinstance(room, dict) or not room.get("reachable"):
            return None, {"why": "room unreachable"}
        members = room.get("members") or {}
        npm, vm = members.get(np_t), members.get(vol_t)
        if not isinstance(npm, dict) or not isinstance(vm, dict):
            return None, {"why": "the room doesn't know these targets", "targets": [np_t, vol_t]}
        own = self._own_change(p)
        if own is not None:
            return None, own
        if any(m.get("confidence") in ("unknown", "stale") or m.get("believed") is None for m in (npm, vm)):
            return None, {"why": "Sonos state unknown"}
        st = npm["believed"] if isinstance(npm["believed"], dict) else {}
        state = str(st.get("state") or "").upper()
        mute = bool(st.get("mute"))
        vol = vm["believed"]
        vol = float(vol) if isinstance(vol, (int, float)) and not isinstance(vol, bool) else None
        if vol is None:
            return None, {"why": "volume unknown"}
        playing = state in SONOS_PLAYING and not mute and vol > 0
        return playing, {"state": state.lower(), "mute": mute, "volume": vol}

    def _context(self, now_s: float) -> Optional[dict]:
        """structure's music.context while it is fresh, else None."""
        mc = self._call(getattr(self.ctx, "music_context", None))
        if not isinstance(mc, dict):
            return None
        t = mc.get("t_ms")
        if not isinstance(t, (int, float)) or isinstance(t, bool) or now_s - t / 1000.0 > CONTEXT_STALE_S:
            return None
        return mc

    def _test_stream_evidence(self, name: Any) -> Optional[dict]:
        """Evidence when the classifier's stream is a test stream (the caller checks include_test), else None."""
        if isinstance(name, str) and name:
            streams, _ = self._health()
            if _is_test(name, streams.get(name)):
                return {"why": f"{name} is a test stream (include_test is off)", "stream": name}
        return None

    # readers: (params, now_s) -> (value, via, evidence)
    def _r_piano_playing(self, p: dict, now_s: float):
        streams, _ = self._health()
        names = self._piano_streams(p, streams)
        up = sorted(n for n in names if self._up(streams, n))
        if not up:
            return None, "midi", {"why": "no listed MIDI stream is up", "streams": sorted(names)}
        if self.ctx.notes is None:
            return None, "midi", {"why": "no note tap"}
        chans = self._channels(p)
        window = _num(p.get("window_s"), DESIGN_DEFAULTS["window_s"])
        t0 = now_s - window
        alln = self._notes(now_s)
        notes = [e for e in alln[bisect.bisect_left(alln, t0, key=itemgetter(0)):]
                 if e[1] in names and (chans is None or e[2] in chans)]
        n = len(notes)
        span = max(e[0] for e in notes) - min(e[0] for e in notes) if n else 0.0
        pitches = len({e[3] for e in notes})
        ev: dict = {"notes": n, "span_s": round(span, 1), "pitches": pitches, "window_s": window, "streams": up}
        ok = (n >= _num(p.get("min_notes"), DESIGN_DEFAULTS["min_notes"])
              and span >= _num(p.get("min_span_s"), DESIGN_DEFAULTS["min_span_s"])
              and pitches >= _num(p.get("min_pitches"), DESIGN_DEFAULTS["min_pitches"]))
        via = "midi"
        heard_by = p.get("heard_by")
        if ok and isinstance(heard_by, str) and heard_by and self._up(streams, heard_by):
            f = self._features(now_s)
            if f is not None and f.get("stream") == heard_by:
                vals = [h[2] for h in self._hist if h[0] >= t0 and h[1] == heard_by]
                vals.append(_num(f.get("lufs_s"), -70.0))
                loud, need = max(vals), _num(p.get("heard_min_lufs"), DESIGN_DEFAULTS["heard_min_lufs"])
                ev.update(heard_by=heard_by, heard_lufs=round(loud, 1), heard_min_lufs=need)
                via = "midi+mic"
                if loud < need:
                    ok = False
                    ev["why"] = "the mic doesn't hear the piano"
        return ok, via, ev

    def _r_piano_idle(self, p: dict, now_s: float):
        streams, _ = self._health()
        names = self._piano_streams(p, streams)
        up = sorted(n for n in names if self._up(streams, n))
        if not up:
            # nothing is sending MIDI (the sender stopped, the piano is off, a test piece ended, or it was never
            # connected): the piano isn't being played, so a leave rule can run its for_s (owner, 17 Oct: Piano never
            # went back after the MIDI file stopped). "The piano is playing" stays unknown; availability says
            # "isn't connected yet" on its own.
            return True, "midi", {"why": "the piano isn't sending", "streams": sorted(names)}
        chans = self._channels(p)
        lasts = [t for (st, ch), t in (self.ctx.notes.last_note_times() if self.ctx.notes is not None else {}).items()
                 if st in names and (chans is None or ch in chans)]
        last_age = round(now_s - max(lasts), 1) if lasts else None
        ev: dict = {"last_note_age_s": last_age, "streams": up}
        if last_age is not None and last_age < _num(p.get("idle_note_s"), IDLE_NOTE_S):
            return False, "midi", ev
        pv = self._call(self.ctx.piano)
        if isinstance(pv, dict) and pv.get("stream") in names:
            keys, damper = int(_num(pv.get("keys_down"), 0)), _num(pv.get("damper"), 0.0)
            ev.update(keys_down=keys, damper=round(damper, 2))
            if keys > 0 or damper >= DAMPER_DOWN:
                return False, "midi", ev
        return True, "midi", ev

    def _r_music_audible(self, p: dict, now_s: float):
        sonos, sev = self._sonos(p)
        own = self._own_change(p)
        if own is not None:
            return None, "sonos", own
        streams, active = self._health()
        stream = p.get("stream") if isinstance(p.get("stream"), str) and p.get("stream") else None
        f = self._features(now_s)
        off = self._off()
        up = self._up(streams, stream) if stream else ((active is not None and active not in off) or f is not None)
        ev: dict = {"stream": stream or (f or {}).get("stream"), "sonos": sonos, "sonos_detail": sev}
        if not up:
            if (stream in off) if stream else bool(off):
                ev["why"] = "the mic is off"
            return (False if sonos is False else None), "sonos", ev
        c = self._classify(stream, now_s)
        if isinstance(c, dict) and isinstance(c.get("music"), (int, float)):
            # CED decides (README S4.3): music at min_prob (less the hysteresis while it holds), or Sonos playing and
            # the mic hearing it over the gate (§8.9). Without a reading: the Stage-3 DSP guess below.
            test = None if stream or p.get("include_test") else self._test_stream_evidence(c.get("stream"))
            if test is not None:
                return (False if sonos is False else None), "classifier", {**ev, **test}
            need = _num(p.get("min_prob"), DESIGN_DEFAULTS["min_prob"])
            ev.update(music_prob=round(c["music"], 3), min_prob=need, ced_n=c.get("n"))
            if c["music"] >= need - self._hyst():
                return True, "classifier", ev
            if sonos is True and f is not None and (not stream or f.get("stream") == stream) and \
                    _num(f.get("gate"), 0.0) >= _num(p.get("dsp_min_gate"), DESIGN_DEFAULTS["dsp_min_gate"]):
                return True, "sonos+mic", {**ev, "gate": round(_num(f.get("gate"), 0.0), 3)}
            return False, "classifier", ev
        if f is None or (stream and f.get("stream") != stream):
            return (False if sonos is False else None), "dsp", {**ev, "why": "its sound isn't measured now"}
        test = self._test_measured(p, f)
        if test is not None:
            return (False if sonos is False else None), "dsp", {**ev, **test}
        gate = _num(f.get("gate"), 0.0)
        mic = gate >= _num(p.get("dsp_min_gate"), DESIGN_DEFAULTS["dsp_min_gate"])
        clk = self._call(self.ctx.clock) or {}
        harm = self._call(self.ctx.harmony) or {}
        beat = clk.get("gate") in ("beat", "dmx") or _num(harm.get("conf"), 0.0) >= HARMONY_MIN_CONF
        ev.update(gate=round(gate, 3), clock_gate=clk.get("gate"), harmony_conf=harm.get("conf"))
        if mic and beat:
            return True, "dsp", ev
        if mic and sonos is True:
            return True, "sonos+mic", ev
        return False, "dsp", ev

    def _r_sonos_playing(self, p: dict, now_s: float):
        v, ev = self._sonos(p)
        return v, "sonos", ev

    def _r_genre_in(self, p: dict, now_s: float):
        genres = p.get("genres")
        if not (isinstance(genres, list) and genres):
            return None, "listener", {"why": "needs genres"}
        test = self._test_measured(p, self._features(now_s))
        if test is not None:
            return None, "listener", test
        need = _num(p.get("min_conf"), DESIGN_DEFAULTS["min_conf"])
        if self._prev is not None:                        # it was confident (true or false): it stays so until its
            need -= float(self._ced_cfg["hysteresis"])    # conf is the CED hysteresis under min_conf (no flapping)
        mc = self._context(now_s)
        g = mc.get("genre") if mc is not None and isinstance(mc.get("genre"), dict) else None
        ctx_no: Optional[tuple] = None                    # why structure's genre couldn't say (the beliefs may)
        if g is not None:                                 # structure's genre (README S4.8's rule) comes first
            via = g.get("via") if g.get("via") in contract.GENRE_VIA else "listener"
            conf = _num(g.get("conf"), 0.0)
            ev = {"genre": g.get("word"), "conf": round(conf, 2), "genres": genres, "via": via,
                  "parent": g.get("parent")}
            if not isinstance(g.get("word"), str) or not g["word"]:
                ctx_no = (via, {**ev, "why": "no genre word yet"})
            elif conf < need:
                ctx_no = (via, {**ev, "why": "not confident"})
            else:
                return g["word"] in genres, via, ev
        # README S5.20: the ML leads with a family that has no word (Hip Hop, Reggae, ...) or isn't sure: the AI
        # listener's own word, as the director's _words does. Unknown when it has none either.
        b = self._call(self.ctx.beliefs)
        if not isinstance(b, dict) or b.get("state") in (None, "none", "fading"):
            if ctx_no is not None:
                return None, ctx_no[0], ctx_no[1]
            return None, "listener", {"why": "the listener has no confident view", "state": (b or {}).get("state")}
        bg = b.get("genre") if isinstance(b.get("genre"), dict) else None
        if not bg or not bg.get("word") or bg.get("word") == "unknown":
            if ctx_no is not None:
                return None, ctx_no[0], ctx_no[1]
            return None, "listener", {"why": "no genre yet"}
        conf = _num(bg.get("conf"), 0.0)
        ev = {"genre": bg["word"], "conf": round(conf, 2), "genres": genres, "via": "listener",
              "parent": (ctx_no[1].get("parent") if ctx_no else None)}
        if ctx_no is not None:
            ev["ml"] = {"why": ctx_no[1]["why"], "genre": ctx_no[1].get("genre"), "conf": ctx_no[1].get("conf")}
        if conf < need:
            if ctx_no is not None:
                return None, ctx_no[0], ctx_no[1]
            return None, "listener", {**ev, "why": "not confident"}
        return bg["word"] in genres, "listener", ev

    def _beliefs_live(self) -> Optional[dict]:
        """The listener's beliefs while they are fresh or holding, else None."""
        b = self._call(self.ctx.beliefs)
        return b if isinstance(b, dict) and b.get("state") in ("fresh", "holding") else None

    def _r_mood_in(self, p: dict, now_s: float):
        """README S5.20: the listener's moods (beliefs, conf >= min_conf), or the ML worker's mood tags (music.context
        ml.moods_top5, the same bar), include one of the rule's. False when a mood is confident and none is listed;
        unknown until one is confident."""
        moods = p.get("moods")
        if not (isinstance(moods, list) and moods):
            return None, "listener", {"why": "needs moods"}
        test = self._test_measured(p, self._features(now_s))
        if test is not None:
            return None, "listener", test
        need = _num(p.get("min_conf"), DESIGN_DEFAULTS["min_conf"])
        if self._prev is not None:                        # as genre_in: no flapping at the bar
            need -= float(self._ced_cfg["hysteresis"])
        want = {str(m).strip().lower() for m in moods}
        heard: dict[str, tuple[float, str]] = {}          # word -> (conf, via), the best of the two sources
        b = self._beliefs_live()
        for m in (b or {}).get("moods") or []:
            if isinstance(m, dict) and isinstance(m.get("word"), str):
                c = _num(m.get("conf"), 0.0)
                if c > heard.get(m["word"], (-1.0, ""))[0]:
                    heard[m["word"]] = (c, "listener")
        mc = self._context(now_s)
        ml = mc.get("ml") if mc is not None and isinstance(mc.get("ml"), dict) else {}
        for x in ml.get("moods_top5") or []:
            if isinstance(x, (list, tuple)) and len(x) >= 2 and isinstance(x[0], str):
                w, c = x[0].strip().lower(), _num(x[1], 0.0)
                if c > heard.get(w, (-1.0, ""))[0]:
                    heard[w] = (c, "ml")
        ev: dict = {"moods": sorted(want), "min_conf": round(need, 3),
                    "heard": [{"word": w, "conf": round(c, 2), "via": v}
                              for w, (c, v) in sorted(heard.items(), key=lambda kv: -kv[1][0])[:6]]}
        if b is None and mc is None:
            return None, "listener", {**ev, "why": "nothing is listening for the mood"}
        hits = sorted(((c, w, v) for w, (c, v) in heard.items() if w in want and c >= need), reverse=True)
        if hits:
            return True, hits[0][2], {**ev, "mood": hits[0][1]}
        if any(c >= need for c, _ in heard.values()):
            return False, "listener" if b is not None else "ml", ev
        return None, "listener", {**ev, "why": "no confident mood yet"}

    def _r_building(self, p: dict, now_s: float):
        """README S5.20: structure's section phase is "build" (while the room's sound is measured now), or the
        listener's trajectory says "building" at min_conf. False when either is following the music and says
        otherwise; unknown while neither is."""
        f = self._features(now_s)
        test = self._test_measured(p, f)
        if test is not None:
            return None, "structure", test
        need = _num(p.get("min_conf"), DESIGN_DEFAULTS["min_conf"])
        mc = self._context(now_s) if f is not None else None     # the mic off or no sound: structure isn't now
        sec = mc.get("section") if mc is not None and isinstance(mc.get("section"), dict) else None
        phase = sec.get("phase") if sec is not None and isinstance(sec.get("phase"), str) else None
        b = self._beliefs_live()
        tr = b.get("trajectory") if b is not None and isinstance(b.get("trajectory"), dict) else None
        state = tr.get("state") if tr is not None and isinstance(tr.get("state"), str) else None
        conf = _num((tr or {}).get("conf"), 0.0)
        ev: dict = {"phase": phase, "build_progress": (sec or {}).get("build_progress"), "trajectory": state,
                    "trajectory_conf": round(conf, 2) if tr is not None else None, "min_conf": need}
        if phase == "build":
            return True, "structure", ev
        if state == "building" and conf >= need:
            return True, "listener", ev
        if phase is not None:
            return False, "structure", ev
        if state is not None and conf >= need:
            return False, "listener", ev
        return None, "structure", {**ev, "why": "nothing is following the music's shape"}

    def _r_track_changed(self, p: dict, now_s: float):
        """README S5.20: music.context's track changed (id > 0: a change since show_service started) within
        ``within_s``, via Sonos's now-playing or the sound. False otherwise while Sonos says it or the sound is
        measured; unknown while nothing follows the music."""
        f = self._features(now_s)
        test = self._test_measured(p, f)
        if test is not None:
            return None, "structure", test
        within = _num(p.get("within_s"), DESIGN_DEFAULTS["within_s"])
        mc = self._context(now_s)
        tr = mc.get("track") if mc is not None and isinstance(mc.get("track"), dict) else None
        if tr is None:
            return None, "structure", {"why": "nothing is following the songs", "within_s": within}
        tid = tr.get("id")
        age = tr.get("age_s")
        via = tr.get("via") if isinstance(tr.get("via"), str) else "structure"
        ev = {"track_id": tid, "age_s": age, "via": tr.get("via"), "within_s": within}
        if isinstance(tid, int) and not isinstance(tid, bool) and tid > 0 and isinstance(age, (int, float)) \
                and not isinstance(age, bool) and age <= within:
            return True, via, ev
        if f is None and tr.get("via") != "sonos":
            return None, "structure", {**ev, "why": "the music isn't being heard now"}
        return False, via, ev

    def _r_energy_above(self, p: dict, now_s: float):
        if not isinstance(p.get("value"), (int, float)) or isinstance(p.get("value"), bool):
            return None, "dsp", {"why": "needs value"}
        f = self._features(now_s)
        stream = p.get("stream") if isinstance(p.get("stream"), str) and p.get("stream") else None
        if f is None:
            return None, "dsp", {"why": "no fresh features"}
        if stream and f.get("stream") != stream:
            return None, "dsp", {"why": f"{stream} isn't the stream being measured", "stream": f.get("stream")}
        test = self._test_measured(p, f)
        if test is not None:
            return None, "dsp", test
        ema = self._ema.get(f.get("stream"))
        e = ema[1] if ema else _num(f.get("energy"), 0.0)
        return e > float(p["value"]), "dsp", {"energy_ema": round(e, 3), "value": p["value"], "stream": f.get("stream")}

    def _r_talking(self, p: dict, now_s: float):
        if self.classifier is None:
            return None, "classifier", {"why": "the music/speech check isn't running (no ML worker)"}
        stream = p.get("stream") if isinstance(p.get("stream"), str) and p.get("stream") else None
        test = None if stream else self._test_measured(p, self._features(now_s))
        if test is not None:
            return None, "classifier", test
        c = self._classify(stream, now_s)
        speech = self._speech(c)
        if not isinstance(c, dict) or speech is None:
            return None, "classifier", {"why": "no fresh music/speech reading", "stream": stream}
        test = None if stream or p.get("include_test") else self._test_stream_evidence(c.get("stream"))
        if test is not None:
            return None, "classifier", test
        min_prob = _num(p.get("min_prob"), DESIGN_DEFAULTS["min_prob"])
        music_below = _num(p.get("music_below"), TALKING_MUSIC_BELOW)
        h = self._hyst()
        talk = speech >= min_prob - h and _num(c.get("music"), 0.0) < music_below + h
        return talk, "classifier", {"speech": round(speech, 3), "music": round(_num(c.get("music"), 0.0), 3),
                                    "speech_words": list(self._speech_words),
                                    "min_prob": min_prob, "music_below": music_below, "hysteresis": h,
                                    "ced_n": c.get("n"), "stream": c.get("stream")}

    def _r_quiet(self, p: dict, now_s: float):
        own = self._own_change(self.params("sonos_playing"))      # the room's own Sonos change isn't the room's quiet
        if own is not None:
            return None, "dsp", own
        streams, active = self._health()
        stream = p.get("stream") if isinstance(p.get("stream"), str) and p.get("stream") else None
        off = self._off()
        up = self._up(streams, stream) if stream else (active is not None and active not in off)
        if not up:                                        # the mic switched off is never "quiet" (README S5.16)
            mic_off = (stream in off) if stream else bool(off)
            return None, "dsp", {"why": "the mic is off" if mic_off else "the stream is down", "stream": stream}
        f = self._features(now_s)
        if f is None:
            raw = self._call(self.ctx.features)
            if isinstance(raw, dict) and raw.get("stream") in off:   # the last sound is a mic that went off
                return None, "dsp", {"why": "the mic is off", "stream": raw.get("stream")}
            return True, "dsp", {"why": "no fresh features while it is up", "stream": stream or active}
        if not stream:
            test = self._test_measured(p, f)
            if test is not None:
                return None, "dsp", test
        if stream and f.get("stream") != stream:
            state = (streams.get(stream) or {}).get("state")
            return (True if state == "quiet" else None), "health", {"stream": stream, "state": state}
        gate, below = _num(f.get("gate"), 0.0), _num(p.get("gate_below"), DESIGN_DEFAULTS["gate_below"])
        ev = {"gate": round(gate, 3), "gate_below": below, "stream": f.get("stream")}
        if gate < below:
            return True, "dsp", ev
        mb, sb = p.get("music_below"), p.get("speech_below")
        c = self._classify(stream, now_s) if isinstance(mb, (int, float)) and isinstance(sb, (int, float)) else None
        speech = self._speech(c)
        if isinstance(c, dict) and isinstance(c.get("music"), (int, float)) and speech is not None:
            h = self._hyst()                              # over the gate, but neither music nor speech: only noise
            ev.update(music=round(c["music"], 3), speech=round(speech, 3), music_below=mb, speech_below=sb,
                      hysteresis=h, ced_n=c.get("n"))
            return (c["music"] < float(mb) + h and speech < float(sb) + h), "classifier", ev
        return False, "dsp", ev

    def _r_between(self, p: dict, now_s: float):
        a, b = _hhmm(p.get("from")), _hhmm(p.get("to"))
        if a is None or b is None:
            return None, "clock", {"why": 'needs from and to as "HH:MM"'}
        t = self._local()
        mins = t.hour * 60 + t.minute + t.second / 60.0
        return in_window(mins, a, b), "clock", {"now": f"{t.hour:02d}:{t.minute:02d}", "from": p["from"], "to": p["to"]}

    def _r_light_below(self, p: dict, now_s: float):
        return self._light_word(p, below=True)

    def _r_light_above(self, p: dict, now_s: float):
        return self._light_word(p, below=False)

    def _light_word(self, p: dict, below: bool):
        """README S5.19: the median level against lux. While it holds (its last reading was True) it keeps holding
        until the level is past lux by the hysteresis: darker than 50 lux stays true up to 57.5 (0.15), brighter than
        200 down to 170."""
        lux = _fnum(p.get("lux"))
        if lux is None:
            return None, "light", {"why": "needs lux"}
        sensor = p.get("sensor") if isinstance(p.get("sensor"), str) and p.get("sensor") else None
        level, ev = self.light.reading(sensor)
        if level is None:
            return None, "light", ev
        h = float(self.light.hysteresis) if self._prev is True else 0.0
        if below:                                         # (1e-9: 50 x 1.15 is 57.49999... in floats)
            v = level <= lux * (1.0 + h) + 1e-9 if h else level < lux
        else:
            v = level >= lux * (1.0 - h) - 1e-9 if h else level > lux
        return v, "light", {**ev, "lux": lux, "hysteresis": h}


# -- Conditions -------------------------------------------------------------------------------------------------------
def kleene_all(vals: list[Optional[bool]]) -> Optional[bool]:
    if any(v is False for v in vals):
        return False
    return None if any(v is None for v in vals) else True


def kleene_any(vals: list[Optional[bool]]) -> Optional[bool]:
    if any(v is True for v in vals):
        return True
    return None if any(v is None for v in vals) else False


def _for(v: Any) -> float:
    return max(0.0, _num(v, 0.0))


COMB_WORDS = {"all": "both / all of", "any": "any of", "not": "not"}     # the combinators in page words


def _join(path: str, seg: str) -> str:
    return f"{path}.{seg}" if path else seg


def word_path(node: dict, path: str, word: str) -> str:
    """A word's path in a rule: the object's own path when it holds only that word (and for_s), else <path>.<word>."""
    return path if sum(1 for k in node if k != "for_s") == 1 else _join(path, word)


def param_label(k: str) -> str:
    return (PARAM_SCHEMAS.get(k) or {}).get("x-ui", {}).get("label") or k


def _wrap(t: str) -> str:
    """A part of a bigger sentence: in brackets when it is itself a list or carries a for_s (a ", " or ": " outside
    any brackets it already has)."""
    depth = 0
    for i, ch in enumerate(t):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and ch in ",:" and t[i + 1:i + 2] == " ":
            return f"({t})"
    return t


def _list_words(items: list[str], conj: str) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" {conj} " + items[-1]


def all_words(items: list[str]) -> str:
    """"both: A and B", "all of: A, B and C"."""
    if not items:
        return "?"
    if len(items) == 1:
        return items[0]
    return ("both: " if len(items) == 2 else "all of: ") + _list_words(items, "and")


def any_words(items: list[str]) -> str:
    """"any of: A or B"."""
    if not items:
        return "?"
    return items[0] if len(items) == 1 else "any of: " + _list_words(items, "or")


def for_words(v: Any) -> str:
    n = _fnum(v)
    return f", for at least {duration_words(n)}" if n is not None and n > 0 else ""


class Conditions:
    """The rule grammar (README S3.8):

        cond   := {word: params, ...} | {"all": [cond, ...], "for_s"?} | {"any": [...], "for_s"?} | {"not": cond, "for_s"?}
        params := {} | true | null | {"for_s"?: s, <param>: value, ...}

    Three-valued (Kleene). A node *holds* when it has been true continuously for its for_s; a node that is true but not
    yet for long enough counts as false for its parent. False or unknown resets a timer, and so does a gap in
    evaluation (> GAP_S without a call). Timers are keyed by path: ``<rule path>/all/0/piano_playing``. A rule whose
    text changes is reset. A rule with problems (unknown word or param, bad value) never holds.

    README S5.19: ``check_nodes`` is ``check`` per node and ``describe`` is the rule in page words with each node's
    live reading, both keyed by the editor's paths (``enter_when.any[1]``, ``leave_when.all[0].not``; a word of a
    several-word object ``enter_when.piano_playing``): "both: A and B", "any of: A or B", "not A", ", for at least 2
    min"."""

    GAP_S = 3.0

    def __init__(self, situations: Situations) -> None:
        self.s = situations
        self._t: dict[str, float] = {}                    # path -> since (true)
        self._touch: dict[str, float] = {}                # path -> last evaluated
        self._texts: dict[str, str] = {}                  # rule path -> its text
        self._text_of: dict[str, tuple[Any, str]] = {}    # rule path -> (the rule object, its text)
        self._checked: dict[tuple[str, int], list[str]] = {}

    # -- checking -------------------------------------------------------------------------------------------------------
    def check(self, cond: Any) -> list[str]:
        """Problems, in page words; [] when fine. Never raises."""
        return [p["where"] for p in self.check_nodes(cond)]

    def check_nodes(self, cond: Any, root: str = "") -> list[dict]:
        """check(), per node (README S5.19): [{"path": the node's path under ``root`` ("enter_when.any[1]", see
        contract.RuleProblem), "text": plain words for the Room page, "level": "error", "where": check()'s words}].
        Each one means the rule never holds. Never raises."""
        out: list[dict] = []
        try:
            self._check(cond, "", root, out)
        except Exception as e:                            # never raises
            out.append({"path": root, "text": "This condition can't be read", "level": "error",
                        "where": f"can't read this condition ({type(e).__name__})"})
        return out

    @staticmethod
    def _bad(out: list[dict], where: str, path: str, text: str) -> None:
        out.append({"path": path, "text": text, "level": "error", "where": where})

    def _check_for(self, v: Any, where: str, path: str, out: list[dict]) -> None:
        if (err := check_param("for_s", v)) is not None:
            self._bad(out, f"{where or '/'}for_s {err}", path, f"“For at least” {err}")

    def _check(self, node: Any, where: str, path: str, out: list[dict]) -> None:
        if not isinstance(node, dict) or not node:
            self._bad(out, f"{where or '/'}: must be an object of situations, or all/any/not", path,
                      "Empty: pick what Auto should notice" if isinstance(node, dict) else
                      "This isn't a condition: pick what Auto should notice, or both / any of / not")
            return
        combs = [k for k in node if k in contract.COMBINATORS]
        if combs:
            c = combs[0]
            if len(combs) > 1 or set(node) - {c, "for_s"}:
                self._bad(out, f"{where or '/'}: {c} stands alone (only for_s may sit beside it)", path,
                          f"“{COMB_WORDS[c]}” must stand alone (only “For at least” may sit beside it)")
                return
            if "for_s" in node:
                self._check_for(node["for_s"], where + "/", path, out)
            body = node[c]
            if c == "not":
                self._check(body, f"{where}/not", _join(path, "not"), out)
                return
            if not isinstance(body, list) or not body:
                self._bad(out, f"{where}/{c}: must be a non-empty list of conditions", path,
                          f"“{COMB_WORDS[c]}” needs at least one condition")
                return
            for i, ch in enumerate(body):
                self._check(ch, f"{where}/{c}/{i}", f"{_join(path, c)}[{i}]", out)
            return
        for word, params in node.items():
            if word == "for_s":
                self._check_for(params, where + "/", path, out)
                continue
            wpath = word_path(node, path, word)
            if word not in WORDS:
                self._bad(out, f"{where}/{word}: unknown situation (known: {', '.join(WORDS)})", wpath,
                          "This isn't something the room can notice (it may have been renamed)")
                continue
            label = WORDS[word]["label"]
            if params is None or params is True:
                params = {}
            if not isinstance(params, dict):
                self._bad(out, f"{where}/{word}: params must be an object (or true)", wpath,
                          f"“{label}”: its settings must be an object (or true)")
                continue
            for k, v in params.items():
                if k == "for_s":
                    self._check_for(v, f"{where}/{word}/", wpath, out)
                elif k not in WORDS[word]["params"]:
                    self._bad(out, f"{where}/{word}/{k}: unknown param (known: for_s, {', '.join(WORDS[word]['params'])})",
                              wpath, f"“{label}” has no setting “{k}”")
                elif (err := check_param(k, v)) is not None:
                    self._bad(out, f"{where}/{word}/{k} {err}", wpath, f"“{label}”: “{param_label(k)}” {err}")
            merged = self.s.params(word, params)
            for req in WORDS[word].get("required", ()):
                if merged.get(req) is None:
                    self._bad(out, f"{where}/{word}: needs {req}", wpath, f"“{label}” needs “{param_label(req)}”")

    # -- page words (README S5.19) ------------------------------------------------------------------------------------
    def describe(self, cond: Any, root: str = "", now_s: Optional[float] = None) -> dict:
        """The rule in page words and what each node reads now, keyed by check_nodes' paths under ``root``:
        {"text": the whole rule's sentence, "nodes": {path: sentence}, "live": {path: True | False | None}, "words":
        {path: {"id", "params"}}}. live is the raw truth now: the words' readings (the same reads, memoised, that Auto's
        own evaluation makes) combined with Kleene logic, every for_s left out (Auto's timers are its own). Without
        ``now_s`` nothing is read and every node is None. Malformed parts read "?" and None (check_nodes says why).
        Never raises."""
        out: dict = {"text": "?", "nodes": {}, "live": {}, "words": {}}
        try:
            out["text"] = self._desc(cond, root, now_s, out)[0]
        except Exception:
            log.debug("describe failed", exc_info=True)
        return out

    def _desc(self, node: Any, path: str, now_s: Optional[float], out: dict) -> tuple[str, Optional[bool]]:
        text, v = "?", None
        if isinstance(node, dict) and node:
            combs = [k for k in node if k in contract.COMBINATORS]
            if combs:
                c, body = combs[0], node[combs[0]]
                if c == "not":
                    t, v0 = self._desc(body, _join(path, "not"), now_s, out)
                    text, v = "not " + _wrap(t), (None if v0 is None else not v0)
                else:
                    kids = [self._desc(ch, f"{_join(path, c)}[{i}]", now_s, out)
                            for i, ch in enumerate(body)] if isinstance(body, list) else []
                    texts, vals = [_wrap(t) for t, _ in kids], [x for _, x in kids]
                    if c == "all":
                        text, v = all_words(texts), (kleene_all(vals) if kids else None)
                    else:
                        text, v = any_words(texts), (kleene_any(vals) if kids else None)
            else:
                kids = []
                for w, prm in node.items():
                    if w == "for_s":
                        continue
                    wp = word_path(node, path, w)
                    prm = {} if prm is None or prm is True else prm
                    params = {k: x for k, x in prm.items() if k != "for_s"} if isinstance(prm, dict) else {}
                    t = (word_label(w, params) if w in WORDS else f"“{w}”") + (
                        for_words(prm.get("for_s")) if isinstance(prm, dict) else "")
                    wv = None
                    if now_s is not None and w in WORDS and isinstance(prm, dict):
                        wv = self.s.read(w, params, now_s)["value"]
                    out["words"][wp] = {"id": w, "params": params}
                    if wp != path:
                        out["nodes"][wp], out["live"][wp] = t, wv
                    kids.append((t, wv))
                if len(kids) == 1:
                    text, v = kids[0]
                elif kids:
                    text, v = all_words([_wrap(t) for t, _ in kids]), kleene_all([x for _, x in kids])
            text += for_words(node.get("for_s"))
        out["nodes"][path], out["live"][path] = text, v
        return text, v

    def _problems(self, text: str, cond: Any) -> list[str]:
        key = (text, self.s.revision)
        p = self._checked.get(key)
        if p is None:
            if len(self._checked) > 512:
                self._checked.clear()
            p = self._checked[key] = self.check(cond)
        return p

    # -- evaluating -----------------------------------------------------------------------------------------------------
    def holds(self, cond: Any, path: str, now_s: float) -> tuple[bool, dict]:
        """(held long enough, {"value": the root's raw value, "held_s", "needs_s", "words": {word: reading}}),
        plus "problems" when the rule can't hold."""
        c = self._text_of.get(path)
        if c is not None and c[0] is cond:                # the same rule object as last tick: same text
            text = c[1]
        else:
            text = json.dumps(cond, sort_keys=True, default=str)
            self._text_of[path] = (cond, text)
        if self._texts.get(path) != text:
            if path in self._texts:
                self.reset(path)
            self._texts[path] = text
        problems = self._problems(text, cond)
        if problems:
            return False, {"value": None, "held_s": 0.0, "needs_s": None, "words": {}, "problems": problems}
        words: dict[str, contract.SituationReading] = {}
        eff, raw, held, needs = self._node(cond, path, now_s, words)
        return eff is True, {"value": raw, "held_s": round(held, 1), "needs_s": needs, "words": words}

    def _timed(self, key: str, raw: Optional[bool], for_s: float, now_s: float) -> tuple:
        last = self._touch.get(key)
        self._touch[key] = now_s
        if raw is True:
            t0 = self._t.get(key)
            if t0 is None or (last is not None and now_s - last > self.GAP_S):
                t0 = self._t[key] = now_s
            held = now_s - t0
            return held >= for_s - 1e-9, True, held, for_s
        self._t.pop(key, None)
        return raw, raw, 0.0, for_s

    def _node(self, node: dict, path: str, now_s: float, words: dict) -> tuple:
        """(effective value for the parent, raw value, held_s, needs_s)."""
        combs = [k for k in node if k in contract.COMBINATORS]
        if combs:
            c = combs[0]
            key = f"{path}/{c}"
            if c == "not":
                v = self._node(node["not"], key, now_s, words)[0]
                raw = None if v is None else not v
            else:
                vals = [self._node(ch, f"{key}/{i}", now_s, words)[0] for i, ch in enumerate(node[c])]
                raw = kleene_all(vals) if c == "all" else kleene_any(vals)
            return self._timed(key, raw, _for(node.get("for_s")), now_s)
        leaves = []
        for word, params in node.items():
            if word == "for_s":
                continue
            prm = dict(params) if isinstance(params, dict) else {}
            for_s = _for(prm.pop("for_s", 0))
            r = self.s.read(word, prm, now_s)
            words[word] = r
            leaves.append(self._timed(f"{path}/{word}", r["value"], for_s, now_s))
        eff = kleene_all([lf[0] for lf in leaves])
        raw = kleene_all([lf[1] for lf in leaves])
        if "for_s" in node:                               # a word group's own for_s (a friendly extra)
            e2, _, held, needs = self._timed(f"{path}/for_s", eff, _for(node["for_s"]), now_s)
            return e2, raw, held, needs
        return eff, raw, min(lf[2] for lf in leaves), max(lf[3] for lf in leaves)

    def reset(self, prefix: str = "") -> None:
        """Drop the timers under ``prefix`` (a rule path; "" = all)."""
        if not prefix:
            self._t.clear()
            self._touch.clear()
            return
        for d in (self._t, self._touch):
            for k in [k for k in d if k == prefix or k.startswith(prefix + "/")]:
                del d[k]

    def prune(self, now_s: float, older_than_s: float = 60.0) -> None:
        """Forget timers not evaluated for a while (rules that were removed)."""
        cut = now_s - older_than_s
        for k in [k for k, t in self._touch.items() if t < cut]:
            self._touch.pop(k, None)
            self._t.pop(k, None)
