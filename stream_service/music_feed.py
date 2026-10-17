"""show_service's music on --music-bus as the renderer's signals (owner, 17 Oct: "wired in properly now";
docs/SHOW_ENGINE.md §5.3, §9.5, §11.1; d4's map G1, G2).

One MusicFeed per process, fed on the loop thread (SessionManager.broadcast_sound) with every music.* message:

  music.features (25 Hz)  energy (normalised loudness x gate), drive, punch, brightness: smoothed levels only, never
                          raw hits (§9.5; the one exception, the onset pulse below, is the mappings' only and
                          switchable): ``activity`` = energy eased tau ACT_TAU_S, ``activity_slow`` tau
                          SLOW_TAU_S (the desk's ACTIVITY smoothing), drive and punch as published (already
                          smoothed upstream).  A quiet room is a real low value, not stale: the feed is live while
                          features keep arriving (FRESH_S), whatever their level.
  music.clock (10 Hz)     the shared beat clock (bpm, anchor_ms on CLOCK_MONOTONIC, beat_at_anchor, beats_per_bar,
                          bar_anchor_beat, phrase_bars, conf, gate, epoch); ClockSignals turns it into beat,
                          beat_phase, bar_phase, phrase_phase, downbeat and tempo at the frame's photon time.
  music.context (1 Hz)    the section id (a new id re-seeds MusicDirection like the desk's GOBO step).
  music.rhythm (10 Hz)    density (onset density, x gate), asked for in the bus hello (``want``; G4).
  room.direction          arousal and groove (show_service's director), via SessionManager.on_room_message.
  Bind signals from these (G5; inputs.FEED_SIGNALS, all eased tau >= ACT_TAU_S, never raw hits): bass (sub +
  bass bands), mids (low_mid + mid), highs (high_mid + air), brightness, density, groove, arousal.

How directly the room drives the mappings (owner, 17 Oct: "the mic is on"; Response, config/live/
music_response.json, hot-reloaded with the presets): while features arrive, the bindings' ``energy`` is
features.energy through an attack / release envelope (``energy_fast``, attack tau >= ACT_TAU_S by default;
MusicDirection's slow energy, which steers the picks and the section epoch and seeds the held (section / cue)
samples, is ``energy_slow``); with no sure beat (neither the clock's gate "beat" nor a locked desk follower) the
bindings' ``beat`` is a pulse of 1 at each strong onset (strength relative to the loudness gate; strong: over a
fixed level, or well over its own recent mean, so sparse quiet music pulses too), decaying (``onset_pulse``; never
for cuts: those use only the sure clock, SessionManager.music_grid); and ``tempo`` takes the clock's bpm, eased,
once it is fairly sure (``tempo_bpm``: not "searching", conf >= tempo_conf), the phases keep their gate.  Section
mappings follow the live value around their latch (binding.Bound.step, by ``follow`` x the feed's liveness; never
integers, gobo inputs, snaps (ease 0), the latched families / bindings; a regional param no faster than
``regional_ease_s``, binding.parse_v2's rule for live on one).  When the features stop, all of it fades out with
the feed's liveness (FADE_S; Session.tick keeps the music signals' weight on it meanwhile), never a jump to idle.

Readers take ``snapshot()`` (an immutable FeedSnap swapped whole, atomic under the GIL) from any thread.
"""
from __future__ import annotations

import json
import logging
import math
import pathlib
import time
from dataclasses import dataclass, fields, replace
from typing import Any, Optional

from .inputs import MUSIC_SIGNALS

log = logging.getLogger("stream_service.music_feed")

FRESH_S = 1.0              # features come at 25 Hz: older than this, the feed is down
ACT_TAU_S = 0.3            # "activity": a smoothed level (§9.5: tau >= 300 ms on the screens)
SLOW_TAU_S = 2.0           # "activity_slow": the desk's ACTIVITY smoothing
RECOVER_S, FADE_S = 1.0, 2.0   # liveness: up over 1 s when features resume, down over 2 s after FRESH_S
CLOCK_FRESH_S = 1.5        # music.clock comes at 10 Hz and on every re-anchor
BEAT_DECAY_S = 0.18        # as inputs.BEAT_DECAY_S: the beat envelope after each predicted beat


def _c01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _num(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


RESPONSE_FILE = "music_response.json"


def fold_bpm(bpm: float, lo: float, hi: float) -> float:
    """``bpm`` doubled or halved into [lo, hi) (a clock's half / double-time guess is the same pulse); a range
    narrower than an octave folds up only."""
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


@dataclass(frozen=True)
class Response:
    """How directly the room's sound drives the per-item mappings (the defaults; the file overrides)."""
    energy_on: bool = True          # energy: the bindings' follows features.energy, up with tau attack_s ...
    attack_s: float = ACT_TAU_S     # (§9.5: tau >= 300 ms on the screens)
    release_s: float = 0.6          # ... and down with tau release_s, while features arrive
    follow: float = 0.7             # section mappings follow the live value around their latch by this much ...
    follow_ease_s: float = 1.0      # ... eased by min(the binding's ease_s, this) ...
    regional_ease_s: float = 2.0    # ... and never faster than this on a regional param (binding.REGIONAL_EASE_S)
    latched: frozenset = frozenset(("symmetry_count", "style_morph", "timing", "mode_enum", "seed", "quality"))
    latched_bindings: frozenset = frozenset()   # "<kind>/<item>/<param>" that never follow (reviewed: they snap)
    onset_on: bool = True           # beat fallback (no sure beat): a pulse at an onset this strong (0-1) ...
    onset: float = 0.2
    onset_rel: float = 2.5          # ... or this many times its recent mean (0: off) ...
    onset_floor: float = 0.1        # ... but never under this ...
    onset_mean_s: float = 4.0       # ... (the mean: eased with this tau) ...
    onset_gate_floor: float = 0.15  # ... read as onset / max(the loudness gate, this): level-free (1: absolute) ...
    onset_gap_s: float = 0.33       # ... at least this long after the last (3/s: a sure clock at 180 bpm) ...
    onset_decay_s: float = 0.15     # ... decaying with this tau
    tempo_on: bool = True           # tempo: the clock's bpm once its conf is this (and it is not "searching") ...
    tempo_conf: float = 0.3
    tempo_ease_s: float = 2.0       # ... eased with this tau until the clock is sure (its guesses wander) ...
    tempo_fold_lo: float = 70.0     # ... and folded by octaves into [fold_min, fold_max): a half-time lock (53)
    tempo_fold_hi: float = 180.0    # reads as its real tempo (106), not as "slow"


# file block, key -> (Response field, lo, hi); a bool field takes true / false, a set field a list of strings
_RESPONSE_KEYS = {("energy", "on"): ("energy_on", None, None), ("energy", "attack_s"): ("attack_s", 0.0, 5.0),
                  ("energy", "release_s"): ("release_s", 0.0, 10.0),
                  ("section_follow", "amount"): ("follow", 0.0, 1.0),
                  ("section_follow", "max_ease_s"): ("follow_ease_s", 0.0, 10.0),
                  ("section_follow", "regional_min_ease_s"): ("regional_ease_s", 0.0, 10.0),
                  ("section_follow", "latched_fams"): ("latched", None, None),
                  ("section_follow", "latched_bindings"): ("latched_bindings", None, None),
                  ("beat_fallback", "on"): ("onset_on", None, None), ("beat_fallback", "onset"): ("onset", 0.0, 1.0),
                  ("beat_fallback", "relative"): ("onset_rel", 0.0, 20.0),
                  ("beat_fallback", "floor"): ("onset_floor", 0.0, 1.0),
                  ("beat_fallback", "mean_s"): ("onset_mean_s", 0.5, 60.0),
                  ("beat_fallback", "gate_floor"): ("onset_gate_floor", 0.01, 1.0),
                  ("beat_fallback", "min_gap_s"): ("onset_gap_s", 0.0, 2.0),
                  ("beat_fallback", "decay_s"): ("onset_decay_s", 0.01, 2.0),
                  ("tempo", "on"): ("tempo_on", None, None), ("tempo", "min_conf"): ("tempo_conf", 0.0, 1.0),
                  ("tempo", "ease_s"): ("tempo_ease_s", 0.0, 10.0),
                  ("tempo", "fold_min"): ("tempo_fold_lo", 20.0, 200.0),
                  ("tempo", "fold_max"): ("tempo_fold_hi", 40.0, 400.0)}
_SETS = ("latched", "latched_bindings")
_BLOCKS = {blk for blk, _ in _RESPONSE_KEYS}


def parse_response(d: Any) -> tuple[Response, list[str]]:
    """The file's blocks -> (Response, problems): a bad or missing value keeps its default (degrade, never refuse)."""
    if not isinstance(d, dict):
        return Response(), ["music_response: must be an object"]
    vals: dict = {}
    errs: list[str] = [f"music_response: {k!r} is not a setting (ignored)" for k in d
                       if k not in _BLOCKS and k not in ("format", "note")]
    for blk in sorted(_BLOCKS):                 # a misspelt key never passes silently
        b = d.get(blk)
        if b is not None and not isinstance(b, dict):
            errs.append(f"music_response: {blk} must be an object (ignored)")
        errs += [f"music_response: {blk}.{k} is not a setting (ignored)" for k in (b if isinstance(b, dict) else ())
                 if (blk, k) not in _RESPONSE_KEYS and k != "note"]
    for (blk, key), (name, lo, hi) in _RESPONSE_KEYS.items():
        b = d.get(blk)
        if not isinstance(b, dict) or key not in b:
            continue
        v = b[key]
        if name in _SETS:
            if isinstance(v, list) and all(isinstance(x, str) for x in v):
                vals[name] = frozenset(v)
                continue
        elif lo is None:
            if isinstance(v, bool):
                vals[name] = v
                continue
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi:
            vals[name] = float(v)
            continue
        keep = getattr(Response, name)
        keep = sorted(keep) if isinstance(keep, frozenset) else keep
        errs.append(f"music_response: {blk}.{key} = {v!r} is not usable: keeping {keep!r}")
    return Response(**vals), errs


class ResponseFile:
    """config/live/music_response.json, re-read when it changes (presets.Presets; looked at once a second at most,
    as mappings.Mappings); absent: defaults."""

    def __init__(self, config_dir: Any = None):
        self.path = None if config_dir is None else pathlib.Path(config_dir) / RESPONSE_FILE
        self.cfg = Response()
        self.errors: list[str] = []
        self._mt: Any = -1
        self._checked = -1e9
        self.reload_if_changed(force=True)

    def reload_if_changed(self, force: bool = False) -> bool:
        """``force``: look now (else at most once a second); re-read only when the file changed."""
        if self.path is None:
            return False
        t = time.monotonic()
        if not force and t - self._checked < 1.0:
            return False
        self._checked = t
        try:
            mt = self.path.stat().st_mtime_ns
        except OSError:
            mt = None
        if mt == self._mt:
            return False
        self._mt = mt
        if mt is None:
            self.cfg, self.errors = Response(), []
            return True
        try:
            cfg, errs = parse_response(json.loads(self.path.read_text()))
        except (OSError, ValueError) as e:
            log.error("%s unreadable (%s): keeping the last good copy", self.path, e)
            self.errors = [f"music_response: {e}"]
            return False
        for e in errs:
            log.warning("%s", e)
        self.cfg, self.errors = cfg, errs
        log.info("music response: %s", self.describe())
        return True

    def describe(self) -> dict:
        return {f.name: (sorted(v) if isinstance(v, frozenset) else v)
                for f in fields(self.cfg) for v in (getattr(self.cfg, f.name),)} | {"errors": list(self.errors)}


@dataclass(frozen=True)
class FeedSnap:
    t: float = 0.0                       # arrival (monotonic s) of the last features
    fresh_since: float = 0.0             # when features resumed after a gap
    energy: float = 0.0
    activity: float = 0.0
    activity_slow: float = 0.0
    drive: float = 0.0
    punch: float = 0.0
    brightness: float = 0.5
    section_id: Any = None
    clock: Optional[dict] = None
    clock_t: float = -1e9
    harmony_t: float = -1e9              # music.harmony (10 Hz): eased tau ACT_TAU_S
    tension: Optional[float] = None
    register: Optional[float] = None
    spread: Optional[float] = None
    chord_t: Optional[float] = None      # the last chord change (a rising edge of its chord_change), monotonic s
    chord_level: float = 0.0
    build_progress: float = 0.0          # music.context section.build_progress (0 outside a build)
    context_t: float = -1e9
    levels: Any = None                   # FEED_SIGNALS -> eased value (a dict, replaced whole)
    levels_t: Any = None                 # FEED_SIGNALS -> arrival of its last update
    energy_fast: float = 0.0             # energy through Response's attack / release (the bindings' energy)
    onset_t: Optional[float] = None      # the last onset pulse (arrival, monotonic s): the beat fallback
    onset_last: float = 0.0              # the previous features' onset strength over the gate (the threshold's)
    onset_mean: Optional[float] = None   # that strength's recent mean (Response.onset_mean_s; the relative threshold)
    onset_n: int = 0                     # onset pulses so far (the inputs API: the pulse rate)
    tempo_bpm: Optional[float] = None    # the clock's bpm while usable for the mappings' tempo (eased while unsure)


class MusicFeed:
    def __init__(self, response: Optional[ResponseFile] = None) -> None:
        self._snap: Optional[FeedSnap] = None
        self.response = response if response is not None else ResponseFile()

    def snapshot(self) -> Optional[FeedSnap]:
        return self._snap

    # -- loop thread ----------------------------------------------------------------------------------------------
    def on_message(self, msg: dict, now: Optional[float] = None) -> None:
        kind, data = msg.get("type"), msg.get("data")
        if not isinstance(data, dict):
            return
        now = time.monotonic() if now is None else now
        s = self._snap or FeedSnap(fresh_since=now)
        if kind == "music.features":
            e = _c01(_num(data.get("energy")))
            gap = now - s.t > FRESH_S
            dt = 0.04 if gap else max(0.0, min(0.5, now - s.t))
            a_fast = 1.0 - math.exp(-dt / ACT_TAU_S)
            a_slow = 1.0 - math.exp(-dt / SLOW_TAU_S)
            act = e if gap else s.activity + (e - s.activity) * a_fast
            slow = e if gap else s.activity_slow + (e - s.activity_slow) * a_slow
            b = data.get("bands") if isinstance(data.get("bands"), dict) else {}
            lv = lambda *names: sum(_c01(_num((b.get(n) or {}).get("level"))) for n in names) / len(names)  # noqa: E731
            s = self._eased(s, {"bass": lv("sub", "bass"), "mids": lv("low_mid", "mid"), "highs": lv("high_mid", "air"),
                                "brightness": _c01(_num(data.get("brightness"), 0.5))}, now)
            r = self.response.cfg
            tau = r.attack_s if e > s.energy_fast else r.release_s
            fast = e if gap or tau <= 0.0 else s.energy_fast + (e - s.energy_fast) * (1.0 - math.exp(-dt / tau))
            # onset = normalised flux x the loudness gate: over the gate it is the flux itself, so a quiet mic
            # pulses on the same hits as a loud one (no gate in the message: as published)
            on = _c01(_num(data.get("onset")) / max(_num(data.get("gate"), 1.0), r.onset_gate_floor))
            # a hit: as strong as Response.onset, or (quieter music, sparser hits) onset_rel x its recent mean, never
            # under onset_floor; rising, and a gap after the last
            om = None if gap else s.onset_mean
            thr = r.onset if om is None or r.onset_rel <= 0.0 else max(r.onset_floor, min(r.onset, r.onset_rel * om))
            ot, n = s.onset_t, s.onset_n
            if on >= thr and on > s.onset_last and (ot is None or now - ot >= r.onset_gap_s):
                ot, n = now, n + 1
            om = on if om is None else om + (on - om) * (1.0 - math.exp(-dt / r.onset_mean_s))
            self._snap = replace(s, t=now, fresh_since=now if gap else s.fresh_since, energy=e, activity=act,
                                 activity_slow=slow, drive=_c01(_num(data.get("drive"))),
                                 punch=_c01(_num(data.get("punch"))),
                                 brightness=_c01(_num(data.get("brightness"), 0.5)), energy_fast=fast, onset_t=ot,
                                 onset_last=on, onset_mean=om, onset_n=n)
        elif kind == "music.clock":
            r = self.response.cfg
            bpm = fold_bpm(_num(data.get("bpm")), r.tempo_fold_lo, r.tempo_fold_hi)
            sure = data.get("gate") == "beat"
            usable = bpm > 0.0 and (sure or (r.tempo_on and data.get("state") not in ("searching", None)
                                             and _num(data.get("conf")) >= r.tempo_conf))
            tb = s.tempo_bpm if now - s.clock_t <= CLOCK_FRESH_S else None
            if not usable:
                tb = None
            elif sure or tb is None or r.tempo_ease_s <= 0.0:
                tb = bpm                             # a sure clock's bpm as it is; a first guess as it is
            else:                                    # an unsure one wanders (half / double): eased
                tb += (bpm - tb) * (1.0 - math.exp(-max(0.0, now - s.clock_t) / r.tempo_ease_s))
            self._snap = replace(s, clock=data, clock_t=now, tempo_bpm=tb)
        elif kind == "music.rhythm":
            self._snap = self._eased(s, {"density": _c01(_num(data.get("density")))}, now)
        elif kind == "music.harmony":
            gap = now - s.harmony_t > FRESH_S
            a = 1.0 if gap else 1.0 - math.exp(-max(0.0, min(0.5, now - s.harmony_t)) / ACT_TAU_S)
            ease = lambda old, v: v if old is None or gap else old + (v - old) * a     # noqa: E731
            cc = _c01(_num(data.get("chord_change")))
            rose = cc >= 0.5 and s.chord_level < 0.5
            self._snap = replace(s, harmony_t=now, tension=ease(s.tension, _c01(_num(data.get("tension")))),
                                 register=ease(s.register, _c01(_num(data.get("register"), 0.5))),
                                 spread=ease(s.spread, _c01(_num(data.get("spread")))),
                                 chord_t=now if rose else s.chord_t, chord_level=cc)
        elif kind == "music.context":
            sec = data.get("section")
            sec = sec if isinstance(sec, dict) else {}
            self._snap = replace(s, section_id=sec.get("id"), build_progress=_c01(_num(sec.get("build_progress"))),
                                 context_t=now)

    def on_room_direction(self, data: dict, now: Optional[float] = None) -> None:
        """show_service's director's arousal and groove (loop thread)."""
        now = time.monotonic() if now is None else now
        s = self._snap or FeedSnap(fresh_since=now)
        self._snap = self._eased(s, {"arousal": _c01(_num(data.get("arousal"), 0.5)),
                                     "groove": _c01(_num(data.get("groove")))}, now)

    @staticmethod
    def _eased(s: FeedSnap, vals: dict, now: float) -> FeedSnap:
        lv, lt = dict(s.levels or {}), dict(s.levels_t or {})
        for k, v in vals.items():
            t0 = lt.get(k)
            if k not in lv or t0 is None or now - t0 > FRESH_S * 6:
                lv[k] = v
            else:
                lv[k] += (v - lv[k]) * (1.0 - math.exp(-max(0.0, min(0.5, now - t0)) / ACT_TAU_S))
            lt[k] = now
        return replace(s, levels=lv, levels_t=lt)

    def levels(self, now: float) -> dict:
        """FEED_SIGNALS that are fresh (room.direction's every 5 s: FRESH_S x 6), else absent."""
        s = self._snap
        if s is None or not s.levels:
            return {}
        return {k: v for k, v in s.levels.items() if now - s.levels_t.get(k, -1e9) <= FRESH_S * 6}

    # -- any thread -----------------------------------------------------------------------------------------------
    def fresh(self, now: float) -> Optional[FeedSnap]:
        """The snapshot while features are arriving, else None."""
        s = self._snap
        return s if s is not None and now - s.t <= FRESH_S else None

    def liveness(self, now: float) -> float:
        """0-1: up over RECOVER_S once features resume, down over FADE_S once they stop."""
        s = self._snap
        if s is None:
            return 0.0
        age = now - s.t
        if age > FRESH_S:
            return _c01(1.0 - (age - FRESH_S) / FADE_S)
        return _c01((now - s.fresh_since) / RECOVER_S) if RECOVER_S > 0 else 1.0

    def energy_now(self, now: float) -> Optional[float]:
        """The bindings' energy (``energy_fast``; its last value once features stop: the caller weighs it by the
        feed's liveness, so it fades out with it); None once that has faded (or off)."""
        s = self._snap
        if s is None or now - s.t > FRESH_S + FADE_S or not self.response.cfg.energy_on:
            return None
        return s.energy_fast

    def onset_pulse(self, now: float) -> Optional[float]:
        """The beat fallback at ``now``: 1 at each onset pulse, decaying; None with no feed yet (or off)."""
        s = self._snap
        r = self.response.cfg
        if s is None or not r.onset_on:
            return None
        t = s.onset_t
        return math.exp(-(now - t) / r.onset_decay_s) if t is not None and now >= t else 0.0

    def tempo_bpm(self, now: float) -> Optional[float]:
        """The clock's bpm for the mappings' tempo while it is fresh and usable (FeedSnap.tempo_bpm), else None."""
        s = self._snap
        return None if s is None or now - s.clock_t > CLOCK_FRESH_S else s.tempo_bpm

    def building(self, now: float) -> float:
        """music.context's build progress while fresh (it comes at 1 Hz), else 0."""
        s = self._snap
        return s.build_progress if s is not None and now - s.context_t <= 3.0 else 0.0

    def harmony(self, now: float) -> Optional[dict]:
        """The room's harmony while fresh: tension, register, spread (eased) and chord_change_t, else None."""
        s = self._snap
        if s is None or now - s.harmony_t > FRESH_S or s.tension is None:
            return None
        return {"tension": s.tension, "register": s.register, "spread": s.spread, "chord_change_t": s.chord_t}

    def clock(self, now: float) -> Optional[dict]:
        """The clock while fresh with a tempo (any gate), else None."""
        s = self._snap
        if s is None or s.clock is None or now - s.clock_t > CLOCK_FRESH_S:
            return None
        return s.clock if _num(s.clock.get("bpm")) > 0.0 else None


def beat_at(c: dict, t_s: float) -> float:
    """The clock's (fractional) beat number at host time ``t_s`` (show_service clock.beat_at)."""
    return _num(c.get("beat_at_anchor")) + (t_s * 1000.0 - _num(c.get("anchor_ms"))) / 1000.0 * _num(c.get("bpm")) / 60.0


def clock_signals(c: dict, t_s: float, mute_until: Optional[float] = None) -> dict:
    """Clock signals at ``t_s`` (the frame's photon time).  Gate "beat": beat, downbeat, tempo and the phases;
    gate "dmx": the phases only; "none": nothing.  ``mute_until``: no beat / downbeat flash before this beat
    number (after an epoch jump)."""
    gate = c.get("gate")
    if gate not in ("beat", "dmx"):
        return {}
    bpm = _num(c.get("bpm"))
    period = 60.0 / bpm
    b = beat_at(c, t_s)
    k = math.floor(b)
    phase = b - k
    bpb = max(1, int(_num(c.get("beats_per_bar"), 4)))
    bars = max(1, int(_num(c.get("phrase_bars"), 4)))
    rel = b - _num(c.get("bar_anchor_beat"))
    in_bar = rel % bpb
    out = {"beat_phase": phase, "bar_phase": in_bar / bpb, "phrase_phase": (rel % (bpb * bars)) / (bpb * bars),
           "beat_confidence": _c01(_num(c.get("conf")))}
    if gate == "beat":
        muted = mute_until is not None and k < mute_until
        out["beat"] = 0.0 if muted else math.exp(-phase * period / BEAT_DECAY_S)
        out["downbeat"] = 0.0 if muted or in_bar >= 1.0 else math.exp(-in_bar * period / BEAT_DECAY_S)
        out["tempo"] = _c01((bpm - 60.0) / 90.0)
    return out


class ClockSignals:
    """Per session (render thread): clock_signals with the epoch guard, so a re-anchor never flashes a beat."""

    def __init__(self) -> None:
        self._epoch: Any = None
        self._mute_until: Optional[float] = None

    def sample(self, c: Optional[dict], t_s: float) -> dict:
        if c is None:
            return {}
        ep = c.get("epoch")
        if self._epoch is not None and ep != self._epoch:
            self._mute_until = math.floor(beat_at(c, t_s)) + 1     # silent until the next whole beat
        self._epoch = ep
        return clock_signals(c, t_s, self._mute_until)


def blend(sig: dict, snap: Optional[FeedSnap], w: float, clock: dict, levels: Optional[dict] = None,
          onset: Optional[float] = None) -> Optional[str]:
    """Fold the room's music into a signals dict in place: activity / activity_slow eased over the desk's by the
    feed's liveness ``w``; the clock's signals (``clock``: clock_signals output) replace the follower's;
    ``levels`` (MusicFeed.levels) give the FEED_SIGNALS, eased to idle 0.5 with ``w`` (absent: n/a); with no sure
    beat in ``clock`` and no locked desk follower, ``onset`` (MusicFeed.onset_pulse) is the beat, over the desk's by
    ``w`` (the mappings' only: cuts never read the signal).  -> where the beat came from: "clock" | "onset" | None
    (the desk's or nothing)."""
    for k, v in (levels or {}).items():
        sig[k] = 0.5 + w * (v - 0.5) if w > 0.0 else None
    if snap is not None and w > 0.0:
        for k, v in (("activity", snap.activity), ("activity_slow", snap.activity_slow)):
            old = sig.get(k)
            sig[k] = v if old is None else old + w * (v - old)
    for k, v in clock.items():
        if k != "tempo":                  # tempo is a music signal (MusicDirection's), eased with the others
            sig[k] = v
    if "beat" in clock:
        return "clock"
    if onset is not None and w > 0.0 and not sig.get("beat_locked"):   # a locked desk's beat predicts: it stays
        old = sig.get("beat") or 0.0
        sig["beat"] = old + w * (onset - old)
        return "onset"
    return None


def music_signals(sig: dict, ex: dict, w: float, fast: Optional[float] = None, wf: float = 0.0) -> None:
    """MusicDirection's signals (``ex``: SessionRuntime.extra) into ``sig``, eased to idle 0.5 by their source's
    liveness ``w``; ``fast`` (MusicFeed.energy_now, while the session's music is fed by show_service) replaces the
    slow ``energy`` by the feed's liveness ``wf`` (``energy_slow`` stays MusicDirection's)."""
    for k in MUSIC_SIGNALS:
        v = ex.get(k)
        if k == "energy" and v is not None and fast is not None:
            v += wf * (fast - v)
        sig[k] = None if v is None else 0.5 + w * (v - 0.5)
