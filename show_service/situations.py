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
words) first, then the listener's beliefs; once confident it stays so until its conf is the CED hysteresis under
min_conf.
"""

from __future__ import annotations

import bisect
import json
import logging
import math
import time
from collections import deque
from operator import itemgetter
from datetime import datetime
from typing import Any, Callable, Optional

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

# -- the vocabulary ---------------------------------------------------------------------------------------------------
# Param JSON schemas (the room's catalog subset plus x-ui; "nullable" means null is allowed: "unset").
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
                       "x-ui": {"label": "Mic loudness at least", "unit": "LUFS"}},
    "stream": {"type": "string", "nullable": True, "x-ui": {"label": "Audio stream", "unset_label": "Whichever plays"}},
    "min_prob": {"type": "number", "minimum": 0, "maximum": 1, "x-ui": {"label": "Classifier probability"}},
    "music_below": {"type": "number", "minimum": 0, "maximum": 1, "nullable": True,
                    "x-ui": {"label": "Music probability under", "unset_label": "The starting point"}},
    "speech_below": {"type": "number", "minimum": 0, "maximum": 1, "nullable": True,
                     "x-ui": {"label": "Speech probability under", "unset_label": "The starting point"}},
    "dsp_min_gate": {"type": "number", "minimum": 0, "maximum": 1, "x-ui": {"label": "Loud enough (gate)"}},
    "now_playing": {"type": "string", "nullable": True,
                    "x-ui": {"label": "Now-playing target", "unset_label": "No Sonos"}},
    "volume": {"type": "string", "nullable": True, "x-ui": {"label": "Volume target", "unset_label": "No Sonos"}},
    "own_change_s": {"type": "number", "minimum": 0,
                     "x-ui": {"label": "Ignore after the room changed it", "unit": "s"}},
    "genres": {"type": "array", "items": {"type": "string"}, "x-ui": {"label": "Genres", "widget": "vocab:genre"}},
    "min_conf": {"type": "number", "minimum": 0, "maximum": 1, "x-ui": {"label": "Confidence at least"}},
    "value": {"type": "number", "minimum": 0, "maximum": 1, "x-ui": {"label": "Energy above"}},
    "gate_below": {"type": "number", "minimum": 0, "maximum": 1, "x-ui": {"label": "Quieter than (gate)"}},
    "from": {"type": "string", "format": "HH:MM", "x-ui": {"label": "From", "widget": "time"}},
    "to": {"type": "string", "format": "HH:MM", "x-ui": {"label": "To", "widget": "time"}},
}

#: Design defaults: starting points (§8.9), used only where show.yaml's section and the rule both leave a param out.
#: A None here means "unset" (any stream, every channel, no check), never an invented device or stream name.
DESIGN_DEFAULTS: dict[str, Any] = {
    "streams": None, "channels": None, "include_test": False, "window_s": 10, "min_notes": 12, "min_span_s": 6,
    "min_pitches": 4, "idle_note_s": IDLE_NOTE_S, "heard_by": None, "heard_min_lufs": -45, "stream": None,
    "min_prob": 0.5, "dsp_min_gate": 0.3,
    "now_playing": None, "volume": None, "own_change_s": 60, "min_conf": 0.6, "gate_below": 0.05,
    "music_below": None, "speech_below": None,       # quiet: unset = the gate alone; talking: TALKING_MUSIC_BELOW
}
TALKING_MUSIC_BELOW = 0.5               # starting point (show.yaml situations.talking.music_below; §8.9: speech with
                                        # no music): talking's music threshold when show.yaml and the rule leave it out

_PIANO = ("streams", "channels", "include_test")
_SONOS = ("now_playing", "volume", "own_change_s")
#: id -> label (page words), source (contract.SITUATION_RANK), the show.yaml sections it reads, params, required.
WORDS: dict[str, dict] = {
    "piano_playing": {
        "label": "the piano is playing", "source": "midi", "sections": ("midi",),
        "params": _PIANO + ("window_s", "min_notes", "min_span_s", "min_pitches", "heard_by", "heard_min_lufs"),
        "description": "Enough note-ons on the listed MIDI streams and channels within the window, spread over time "
                       "and keys. While the heard_by mic is up and playing, it must hear the piano too."},
    "piano_idle": {
        "label": "the piano has stopped", "source": "midi", "sections": ("midi",), "params": _PIANO + ("idle_note_s",),
        "description": "No note-on for idle_note_s and no key or damper down. for_s is how long it has been idle."},
    "music_audible": {
        "label": "music is playing", "source": "audio", "sections": ("music", "sonos"),
        "params": ("stream", "min_prob", "dsp_min_gate", "include_test") + _SONOS,
        "description": "The room mic hears music: the ML worker's music/speech classifier (CED, its last 3 s) says "
                       "music at min_prob or more. Without the worker, a steady beat or a clear key over the loudness "
                       "gate. Or Sonos is playing and the mic hears it (over the gate)."},
    "sonos_playing": {
        "label": "Sonos is playing", "source": "sonos", "sections": ("sonos",), "params": _SONOS,
        "description": "The room's now-playing target says playing, not muted, with the volume above 0. Unknown for "
                       "own_change_s after the room itself changed Sonos."},
    "genre_in": {
        "label": "the music is one of these genres", "source": "listener", "sections": (),
        "params": ("genres", "min_conf", "include_test"), "required": ("genres",),
        "description": "The music's genre is one of these, confidently: the ML worker's genre family (through "
                       "show.yaml genre_map) or the AI listener's word. Unknown while neither has one, and while a "
                       "test stream is the one heard (unless include_test)."},
    "energy_above": {
        "label": "the music is lively", "source": "audio", "sections": (),
        "params": ("value", "stream", "include_test"), "required": ("value",),
        "description": "The music's energy, averaged over about 10 s, is above this value. Unknown while a test "
                       "stream is the one measured (unless include_test)."},
    "talking": {
        "label": "people are talking", "source": "audio", "sections": ("talking",),
        "params": ("stream", "min_prob", "music_below", "include_test"),
        "description": "The ML worker's music/speech classifier (CED, its last 3 s) hears speech at min_prob or more "
                       "with music under music_below. Unknown without the worker."},
    "quiet": {
        "label": "the room is quiet", "source": "audio", "sections": ("quiet",),
        "params": ("stream", "gate_below", "music_below", "speech_below", "include_test"),
        "description": "The mic stream is up and below the loudness gate. With the ML worker, also when the "
                       "classifier hears neither music nor speech (both under their limits): only noise."},
    "between": {
        "label": "it's between these times", "source": "time", "sections": (), "params": ("from", "to"),
        "required": ("from", "to"),
        "description": "Local time in [from, to); from after to wraps past midnight."},
}
assert tuple(WORDS) == contract.SITUATIONS


def word_label(sid: str, params: Optional[dict] = None) -> str:
    """Page words for one word, with its params where they say more ("the music is jazz or soul")."""
    p = params or {}
    if sid == "genre_in" and isinstance(p.get("genres"), list) and p["genres"]:
        return "the music is " + " or ".join(str(g) for g in p["genres"])
    if sid == "between" and isinstance(p.get("from"), str) and isinstance(p.get("to"), str):
        return f"it's {p['from']}-{p['to']}"
    return WORDS[sid]["label"] if sid in WORDS else sid


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


# -- Situations (component "situations", 1 Hz) ----------------------------------------------------------------------------
class Situations:
    """Reads the vocabulary. ``read`` evaluates one word with a rule's params on top of show.yaml's; ``tick`` samples
    the features history and refreshes the snapshot (every word with show.yaml's params)."""

    name = "situations"
    tick_hz = contract.SLOW_TICK_HZ

    def __init__(self, ctx: contract.BrainContext, *, local: Optional[Callable[[], datetime]] = None,
                 mono: Callable[[], float] = time.monotonic) -> None:
        self.ctx = ctx
        self._local = local or datetime.now
        self._mono = mono
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
        self._speech_words = words
        self._ced_cfg, self.problems = cfg, probs
        self.revision += 1
        self._memo.clear()

    def restore(self, saved: dict) -> None:
        pass                                              # situation timers start cold (README S3.4)

    def start(self) -> None:
        self._bind_classifier()

    def stop(self) -> None:
        pass

    def persist(self) -> None:
        return None

    def _bind_classifier(self) -> None:
        """README S4.8: the classifier comes from ctx.ml (the ML worker's view) when that slot is filled."""
        if self.classifier is None and getattr(self.ctx, "ml", None) is not None:
            self.classifier = self._ced

    def tick(self, now_s: float) -> None:
        self._bind_classifier()
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
                      "problems": list(self.problems)}

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

    def vocabulary(self) -> list[contract.SituationDef]:
        out = []
        for sid, w in WORDS.items():
            p = self.params(sid)
            params = {"for_s": {**PARAM_SCHEMAS["for_s"], "default": 0}}
            for k in w["params"]:
                sch = {**PARAM_SCHEMAS[k], "x-ui": {**PARAM_SCHEMAS[k].get("x-ui", {}),
                                                    "source": self._param_source(sid, k)}}
                if k in p:
                    sch["default"] = p[k]
                if k in w.get("required", ()):
                    sch["x-ui"]["required"] = True
                params[k] = sch
            out.append({"id": sid, "label": w["label"], "description": w["description"], "source": w["source"],
                        "rank": rank_of(sid), "params": params, "available": self._available(sid, p)})
        return out

    def _available(self, sid: str, p: dict) -> bool:
        c = self.ctx
        if sid in ("piano_playing", "piano_idle"):
            streams, _ = self._health()
            return c.notes is not None and bool(self._piano_streams(p, streams) & set(streams))
        if sid == "sonos_playing":
            return self._sonos(p)[0] is not None or bool(p.get("now_playing") and p.get("volume"))
        if sid == "music_audible":
            return c.features is not None or bool(p.get("now_playing"))
        if sid == "genre_in":
            return c.beliefs is not None or getattr(c, "music_context", None) is not None
        if sid in ("energy_above", "quiet"):
            return c.features is not None
        if sid == "talking":
            return self.classifier is not None
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
            return None, "midi", {"why": "no listed MIDI stream is up", "streams": sorted(names)}
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
        if g is not None:                                 # structure's genre (README S4.8's rule) comes first
            via = g.get("via") if g.get("via") in contract.GENRE_VIA else "listener"
            conf = _num(g.get("conf"), 0.0)
            ev = {"genre": g.get("word"), "conf": round(conf, 2), "genres": genres, "via": via,
                  "parent": g.get("parent")}
            if not isinstance(g.get("word"), str) or not g["word"]:
                return None, via, {**ev, "why": "no genre word yet"}
            if conf < need:
                return None, via, {**ev, "why": "not confident"}
            return g["word"] in genres, via, ev
        b = self._call(self.ctx.beliefs)
        if not isinstance(b, dict) or b.get("state") in (None, "none", "fading"):
            return None, "listener", {"why": "the listener has no confident view", "state": (b or {}).get("state")}
        g = b.get("genre") if isinstance(b.get("genre"), dict) else None
        if not g or not g.get("word"):
            return None, "listener", {"why": "no genre yet"}
        conf = _num(g.get("conf"), 0.0)
        ev = {"genre": g["word"], "conf": round(conf, 2), "genres": genres, "via": "listener", "parent": None}
        if conf < need:
            return None, "listener", {**ev, "why": "not confident"}
        return g["word"] in genres, "listener", ev

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


class Conditions:
    """The rule grammar (README S3.8):

        cond   := {word: params, ...} | {"all": [cond, ...], "for_s"?} | {"any": [...], "for_s"?} | {"not": cond, "for_s"?}
        params := {} | true | null | {"for_s"?: s, <param>: value, ...}

    Three-valued (Kleene). A node *holds* when it has been true continuously for its for_s; a node that is true but not
    yet for long enough counts as false for its parent. False or unknown resets a timer, and so does a gap in
    evaluation (> GAP_S without a call). Timers are keyed by path: ``<rule path>/all/0/piano_playing``. A rule whose
    text changes is reset. A rule with problems (unknown word or param, bad value) never holds."""

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
        out: list[str] = []
        try:
            self._check(cond, "", out)
        except Exception as e:                            # never raises
            out.append(f"can't read this condition ({type(e).__name__})")
        return out

    def _check_for(self, v: Any, where: str, out: list[str]) -> None:
        if (err := check_param("for_s", v)) is not None:
            out.append(f"{where or '/'}for_s {err}")

    def _check(self, node: Any, where: str, out: list[str]) -> None:
        if not isinstance(node, dict) or not node:
            out.append(f"{where or '/'}: must be an object of situations, or all/any/not")
            return
        combs = [k for k in node if k in contract.COMBINATORS]
        if combs:
            if len(combs) > 1 or set(node) - {combs[0], "for_s"}:
                out.append(f"{where or '/'}: {combs[0]} stands alone (only for_s may sit beside it)")
                return
            if "for_s" in node:
                self._check_for(node["for_s"], where + "/", out)
            c, body = combs[0], node[combs[0]]
            if c == "not":
                self._check(body, f"{where}/not", out)
                return
            if not isinstance(body, list) or not body:
                out.append(f"{where}/{c}: must be a non-empty list of conditions")
                return
            for i, ch in enumerate(body):
                self._check(ch, f"{where}/{c}/{i}", out)
            return
        for word, params in node.items():
            if word == "for_s":
                self._check_for(params, where + "/", out)
                continue
            if word not in WORDS:
                out.append(f"{where}/{word}: unknown situation (known: {', '.join(WORDS)})")
                continue
            if params is None or params is True:
                params = {}
            if not isinstance(params, dict):
                out.append(f"{where}/{word}: params must be an object (or true)")
                continue
            for k, v in params.items():
                if k == "for_s":
                    self._check_for(v, f"{where}/{word}/", out)
                elif k not in WORDS[word]["params"]:
                    out.append(f"{where}/{word}/{k}: unknown param (known: for_s, {', '.join(WORDS[word]['params'])})")
                elif (err := check_param(k, v)) is not None:
                    out.append(f"{where}/{word}/{k} {err}")
            merged = self.s.params(word, params)
            for req in WORDS[word].get("required", ()):
                if merged.get(req) is None:
                    out.append(f"{where}/{word}: needs {req}")

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
