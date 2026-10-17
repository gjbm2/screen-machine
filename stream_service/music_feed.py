"""show_service's music on --music-bus as the renderer's signals (owner, 17 Oct: "wired in properly now";
docs/SHOW_ENGINE.md §5.3, §9.5, §11.1; d4's map G1, G2).

One MusicFeed per process, fed on the loop thread (SessionManager.broadcast_sound) with every music.* message:

  music.features (25 Hz)  energy (normalised loudness x gate), drive, punch, brightness: smoothed levels only, never
                          raw hits (§9.5): ``activity`` = energy eased tau ACT_TAU_S, ``activity_slow`` tau
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

Readers take ``snapshot()`` (an immutable FeedSnap swapped whole, atomic under the GIL) from any thread.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace
from typing import Any, Optional

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
    levels: Any = None                   # FEED_SIGNALS -> eased value (a dict, replaced whole)
    levels_t: Any = None                 # FEED_SIGNALS -> arrival of its last update


class MusicFeed:
    def __init__(self) -> None:
        self._snap: Optional[FeedSnap] = None

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
            self._snap = replace(s, t=now, fresh_since=now if gap else s.fresh_since, energy=e, activity=act,
                                 activity_slow=slow, drive=_c01(_num(data.get("drive"))),
                                 punch=_c01(_num(data.get("punch"))),
                                 brightness=_c01(_num(data.get("brightness"), 0.5)))
        elif kind == "music.clock":
            self._snap = replace(s, clock=data, clock_t=now)
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
            self._snap = replace(s, section_id=sec.get("id") if isinstance(sec, dict) else None)

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


def blend(sig: dict, snap: Optional[FeedSnap], w: float, clock: dict, levels: Optional[dict] = None) -> None:
    """Fold the room's music into a signals dict in place: activity / activity_slow eased over the desk's by the
    feed's liveness ``w``; the clock's signals (``clock``: clock_signals output) replace the follower's;
    ``levels`` (MusicFeed.levels) give the FEED_SIGNALS, eased to idle 0.5 with ``w`` (absent: n/a)."""
    for k, v in (levels or {}).items():
        sig[k] = 0.5 + w * (v - 0.5) if w > 0.0 else None
    if snap is not None and w > 0.0:
        for k, v in (("activity", snap.activity), ("activity_slow", snap.activity_slow)):
            old = sig.get(k)
            sig[k] = v if old is None else old + w * (v - old)
    for k, v in clock.items():
        if k != "tempo":                  # tempo is a music signal (MusicDirection's), eased with the others
            sig[k] = v
