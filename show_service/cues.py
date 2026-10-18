"""cues.py: the cue engine (show_service/README.md S5.22; docs/SHOW_ENGINE.md §7.1, §8.5). The LLM listener arms, a
real-time component fires (owner, 18 Oct).

One component, ``cues``, ticked at 10 Hz on show-brain (after structure and the listener, before the director). It
reads music.features and music.rhythm (the DSP, by reference), the clock, music.context (structure), the ML worker's
voice reading, situations' music_audible, the listener's beliefs and the director's hold, and publishes room.events
the renderer already knows (stream_service/sessions.py ``room_event`` / ``_track_event``).

**Standing cues**, armed while music plays (no listener needed):
  stop      an abrupt stop: the fast level (features.rms_dbfs, one 20 ms block a tick) falls ``stop.collapse_db`` (20)
            under the median of the 2 s before it within ``stop.fall_s`` (0.3 s), stays down ``stop.quiet_ticks``
            ticks, ends near the stream's floor (the median level of its recent quiet seconds, those whose loudness
            gate stayed under floor_gate, + floor_margin_db; no quiet second yet: under quiet_dbfs), after music
            (music_audible not false, and that 2 s median at least the collapse over the floor). It fires at once: track_end {id "track_end:<track id>", detail
            "stop", transition fade-dark, out_s 0.2, in_s 1.0}. Sound back before structure's own end takes over (its gap-based track_end with the
            same id) sends the same id with detail "sound_back". structure's later track_end is the same id: the
            renderer, already dark, ignores the second dark.
  fade      structure's outro phase (loudness sliding down): track_end {detail "fade", fade-dark, out_s = the time
            to reach the floor from lufs_s's slope over fade.slope_s, clamped fade.min_s..max_s (3..12)}; the music
            coming back up (lufs_s fade.back_db over where it was) sends "sound_back".
  ending    the listener's ``ending`` (beliefs, held 60 s) picks which: cold arms the stop only; fade arms the fade
            (and the stop, which a fade never trips); segue arms neither (the track_change's crossfade carries it);
            unknown or no listener: both.
  The existing drop, breakdown, track_change and track_end_armed (structure's) are untouched.

**Forecast cues**, from the beliefs' ``coming`` (each window on the acoustic clock): armed with their window, size,
signs and confidence; one per kind (a newer forecast replaces an older one); the window's end (+ forecast.slack_s)
disarms it ("missed"). Firing:
  * confidence >= forecast.min_conf, a phrase line of the beat clock (16 bars at phrase16_conf, else 8) inside the
    window, and an early sign agreeing (structure's build, lufs_s rising at rise_db_s over rise_s, or one of the
    cue's signs seen): scheduled AT that line, published lead_bars (>= min_lead_s) ahead;
  * otherwise at the next downbeat (>= min_lead_s ahead; no bar grid: now + min_lead_s) once any of its signs is
    seen inside the window (a ``phrase_line`` sign fires at that line itself).
  big -> room.event "drop", medium -> "section_change" ({id "cue:<kind>:<n>", detail "forecast: <kind>", at_ms the
  line or downbeat, acoustic monotonic ms like structure's}); small: logged only. At most one fire per bar; nothing
  fires while a person holds the look (room.direction ``hold``: ctx.held). A track change disarms every forecast.

**Signs** (contract.COMING_SIGNS; show.yaml ``cues.signs``): louder (lufs_m's last 0.3 s over the 2 s before by
louder_db), low_in (music.rhythm low_db, the same by low_in_db), busier (onset_rate over the one 3-4 s before by
busier_rate and busier_ratio), stop_return (a dip of stop_return_db for stop_return_min_s..max_s, then back),
timbre_change (structure's confirmed section boundary or drop, as it arrives), key_change (music.context's key, at
key_min_strength, other than at arm time), drums_in / drums_out (music.rhythm percussive_ratio up / down by
perc_jump), vocal_in / vocal_out (the ML worker's voice_p crossing voice_on / voice_off within voice_window_s),
phrase_line (the clock's next 8-bar line within one bar). A sign counts for seen_s after it is seen.

**The sheet** (``sheet()``, any thread; ctx.cue_sheet): [contract.CueLine] for the card's listening block and
GET /api/v1/listening ``armed``. **The log**: every arm, schedule, fire, hold, disarm, miss and standing fire, and
each forecast's outcome once its window has passed (what structure itself heard in that window: the hit rate), in
memory (the snapshot's ``log``) and CUES_LOG (JSONL, rotated at 1 MB).

Every threshold is show.yaml ``cues`` (CUES_FALLBACK: the starting points). Nothing here names a mode, vibe, device or
stream.
"""

from __future__ import annotations

import collections
import copy
import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

from . import contract
from .structure import _Grid

log = logging.getLogger("show_service.cues")

FEATURES_STALE_S = 2.0          # music.features comes at 50 Hz
RHYTHM_STALE_S = 1.0            # README S4.6
CONTEXT_STALE_S = 3.0           # music.context comes at 1 Hz (sections at 10 Hz through ctx.music_context)
HIST_N = 300                    # 10 Hz rows kept (30 s)
FLOOR_N = 120                   # quiet seconds' levels kept for the floor
FLOOR_MIN_N = 5                 # ... needed before it counts
FLOOR_MIN_ROWS = 3              # a second counts with this many rows in it
NO_FLOOR_DBFS = -70.0           # the floor before any quiet second
NO_GRID_BAR_S = 2.0             # "one fire per bar" without a grid: a 4/4 bar at 120 bpm
SEEN_KEEP = 64                  # forecast ids already armed
EVENTS_KEEP = 64                # structure's own events, for the outcomes
LOG_N = 200
LOG_ROTATE_BYTES = 1_000_000
STAT_N = 600
STANDING = ("stop", "fade", "segue")

#: show.yaml ``cues``: starting points, all of them. The file's values win.
CUES_FALLBACK: dict = {
    "stop": {"collapse_db": 20.0, "fall_s": 0.3, "ref_s": 2.0, "loud_db": 6.0, "quiet_ticks": 2,
             "floor_margin_db": 12.0, "floor_gate": 0.05, "quiet_dbfs": -45.0, "back_db": 15.0, "back_ticks": 2,
             "watch_s": 15.0, "out_s": 0.2, "in_s": 1.0, "dark_max_s": 8.0},
    "fade": {"slope_s": 4.0, "min_s": 3.0, "max_s": 12.0, "floor_above_db": 6.0, "floor_lufs": -50.0,
             "back_db": 3.0, "back_s": 1.0, "in_s": 2.0, "dark_max_s": 8.0, "watch_s": 30.0},
    "forecast": {"min_conf": 0.6, "slack_s": 2.0, "min_lead_s": 1.1, "lead_bars": 1.0, "phrase16_conf": 0.5,
                 "grid_conf": 0.5, "rise_db_s": 0.15, "rise_s": 6.0},
    "signs": {"louder_db": 4.0, "low_in_db": 6.0, "busier_rate": 0.75, "busier_ratio": 1.3, "perc_jump": 0.15,
              "voice_on": 0.6, "voice_off": 0.4, "voice_window_s": 6.0, "stop_return_db": 15.0,
              "stop_return_min_s": 0.15, "stop_return_max_s": 3.0, "key_min_strength": 0.3, "seen_s": 4.0},
}
_SIGNED = {"cues.stop.quiet_dbfs", "cues.fade.floor_lufs"}
_INTS = {"cues.stop.quiet_ticks", "cues.stop.back_ticks"}

#: the sheet's words (the Room page shows them as they are)
KIND_AT = {"chorus": "at the chorus", "drop": "at the drop", "climax": "at the climax",
           "breakdown": "at the breakdown", "key_lift": "at the key change", "band_in": "when the band comes in",
           "vocal_in": "when the singing comes in", "stop": "at the stop", "outro": "at the outro",
           "end": "at the end", "new_section": "at the next section"}
KIND_NOUN = {"chorus": "the chorus", "drop": "the drop", "climax": "the climax", "breakdown": "the breakdown",
             "key_lift": "the key change", "band_in": "the band coming in", "vocal_in": "the singing coming in",
             "stop": "the stop", "outro": "the outro", "end": "the end", "new_section": "the next section"}
FIRE_KIND = {"big": "drop", "medium": "section_change"}    # small: logged only
READY_WORDS = {"stop": "Ready: snap to dark if the music stops", "fade": "Ready: slow fade if the music fades",
               "segue": "Ready: straight on into the next song"}
DARK_WORDS = {"stop": "Dark: the music stopped; back if it returns",
              "fade": "Fading out with the music; back if it comes up"}


def _f(v: Any, default: Optional[float] = None) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return float(v)
    return default


def _call(fn: Optional[Callable], *a: Any) -> Any:
    if fn is None:
        return None
    try:
        return fn(*a)
    except Exception:                                 # noqa: BLE001 - a missing piece reads as missing
        return None


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
        if isinstance(fv, dict):
            out[k] = _resolve(fv, g[k], p, probs)
            continue
        x = _f(g[k])
        if x is None or (p not in _SIGNED and x < 0) or (p in _INTS and x < 1):
            out[k] = fv
            probs.append(f"{p}: {g[k]!r} is not a usable number: {fv} used")
        else:
            out[k] = int(round(x)) if p in _INTS else x
    return out


def _median(xs: list[float]) -> Optional[float]:
    v = sorted(x for x in xs if x is not None and math.isfinite(x))
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def _mean(xs: list[Optional[float]]) -> Optional[float]:
    v = [x for x in xs if x is not None and math.isfinite(x)]
    return sum(v) / len(v) if v else None


def _slope(ts: list[float], ys: list[Optional[float]]) -> Optional[float]:
    """Least-squares slope (per second); None with fewer than 10 points."""
    pts = [(t, y) for t, y in zip(ts, ys) if y is not None and math.isfinite(y)]
    if len(pts) < 10:
        return None
    mt = sum(t for t, _ in pts) / len(pts)
    my = sum(y for _, y in pts) / len(pts)
    den = sum((t - mt) ** 2 for t, _ in pts)
    return sum((t - mt) * (y - my) for t, y in pts) / den if den > 0 else None


class Cues:
    """Component ``cues`` (contract.Component), 10 Hz on show-brain. ``sheet`` and ``snapshot`` read references
    swapped there (any thread)."""

    name = "cues"
    tick_hz = contract.BRAIN_TICK_HZ

    def __init__(self, ctx: contract.BrainContext, *, mono: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time, log_path: Optional[Path] = contract.CUES_LOG) -> None:
        self.ctx = ctx
        self._mono, self._wall = mono, wall
        self._log_path = Path(log_path) if log_path else None
        self.cfg: dict = copy.deepcopy(CUES_FALLBACK)
        self.problems: list[str] = []
        # 10 Hz rows: (t, level dBFS, lufs_m, lufs_s, low_db, onset_rate, percussive_ratio, gate)
        self._rows: collections.deque = collections.deque(maxlen=HIST_N)
        self._floor_secs: collections.deque = collections.deque(maxlen=FLOOR_N)   # quiet seconds' mean levels
        self._sec: Optional[int] = None
        self._sec_acc: list = [0.0, 0, 0.0]            # this second: (sum of levels, rows, max gate)
        self.floor: Optional[float] = None
        self.ref: Optional[float] = None
        self._voice: collections.deque = collections.deque(maxlen=64)      # (t, voice_p) from the ML worker's tags
        self._sign_t: dict[str, float] = {}             # sign -> when it was last seen (monotonic s)
        self._dip: Optional[tuple[float, float]] = None   # a stop_return dip: (its start, the median before it)
        self._dark: Optional[dict] = None               # a standing cue's dark: {"kind", "id", "track_id", ...}
        self._outro_done = False                        # the fade fired (or was held) for this outro
        self._phase: Optional[str] = None
        self._armed: dict[str, dict] = {}               # kind -> the armed forecast cue
        self._seen: collections.deque = collections.deque(maxlen=SEEN_KEEP)
        self._outcomes: list[dict] = []                 # resolved forecasts waiting for their window's end
        self._sevents: collections.deque = collections.deque(maxlen=EVENTS_KEEP)   # structure's: (kind, at_s, id)
        self._n = 0
        self._seq = 0
        self._last_fire: Optional[float] = None
        self._ending: Optional[str] = None
        self._stop_on = True
        self._fade_on = True
        self._playing = False
        self._grid = _Grid(None, 0.0)
        self._log: collections.deque = collections.deque(maxlen=LOG_N)
        self._tick_ms: collections.deque = collections.deque(maxlen=STAT_N)
        self.counts: collections.Counter = collections.Counter()
        self._sheet: list[dict] = []
        self._snap: dict = {}
        self._build_snapshot(mono())

    # -- Component -----------------------------------------------------------------------------------------------------
    def provides(self) -> dict:
        return {"cue_sheet": self.sheet, "on_music_event": self.on_music_event}

    def configure(self, settings: dict) -> None:
        probs: list[str] = []
        s = settings if isinstance(settings, dict) else {}
        self.cfg = _resolve(CUES_FALLBACK, s.get("cues"), "cues", probs)
        self.problems = probs
        for p in probs:
            log.warning("show.yaml %s", p)

    def restore(self, saved: dict) -> None:
        """Nothing: cues start cold."""

    def start(self) -> None:
        pass

    def persist(self) -> Optional[dict]:
        return None

    def stop(self) -> None:
        pass

    def sheet(self) -> list[dict]:
        """Any thread: the cue sheet (contract.CueLine), built on show-brain."""
        return self._sheet

    def snapshot(self) -> dict:
        return self._snap

    def log(self, n: int = 50) -> list[dict]:
        items = list(self._log)
        return items[-max(0, int(n)):] if n else []

    # -- structure's events (show-brain) ---------------------------------------------------------------------------------
    def on_music_event(self, ev: dict) -> None:
        if not isinstance(ev, dict):
            return
        kind = ev.get("kind")
        now = self._mono()
        at = _f(ev.get("boundary_ms"), _f(ev.get("at_ms")))     # a section's own start, else the event's time
        self._sevents.append((kind, at / 1000.0 if at is not None else now, ev.get("id"), ev.get("detail")))
        if (kind == "section_change" and not ev.get("predicted") and ev.get("detail") != "track") or kind == "drop":
            self._sign_t["timbre_change"] = now
        elif kind == "track_end" and self._dark is not None and ev.get("id") == self._dark["id"] \
                and ev.get("detail") != "sound_back" and not self._dark.get("handed"):
            self._dark["handed"] = True               # structure's own end: its sound_back from here on
            self.counts["handed"] += 1
            self._log_event({"kind": "handed", "cue": self._dark["kind"], "id": self._dark["id"]})
        elif kind == "track_change":
            if self._dark is not None:
                self._log_event({"kind": "dark_over", "cue": self._dark["kind"], "id": self._dark["id"],
                                 "why": "a new song"})
                self._dark = None
            for c in list(self._armed.values()):
                self._resolve_cue(c, "disarmed", now, why="a new song")

    # -- the tick ----------------------------------------------------------------------------------------------------------
    def tick(self, now_s: float) -> None:
        t0 = time.perf_counter()
        self._grid = _Grid(_call(self.ctx.clock), now_s, float(self.cfg["forecast"]["grid_conf"]))
        self._sample(now_s)
        mc = _call(getattr(self.ctx, "music_context", None))
        mc = mc if isinstance(mc, dict) and _f(mc.get("t_ms")) is not None \
            and now_s - mc["t_ms"] / 1000.0 <= CONTEXT_STALE_S else None
        b = _call(self.ctx.beliefs)
        b = b if isinstance(b, dict) and b.get("state") not in (None, "none") else None
        end = b.get("ending") if b is not None else None
        self._ending = end if end in contract.ENDINGS and end != "unknown" else None
        self._stop_on = self._ending != "segue"
        self._fade_on = self._ending in (None, "fade")
        self._signs(now_s, mc)
        self._standing(now_s, mc)
        self._forecasts(now_s, mc, b)
        self._outcome_tick(now_s)
        self._build_sheet(now_s)
        self._tick_ms.append((time.perf_counter() - t0) * 1000.0)
        self._build_snapshot(now_s)

    def _sample(self, now_s: float) -> None:
        f = _call(self.ctx.features)
        tf = _f(f.get("t_ms")) if isinstance(f, dict) else None
        if tf is None or now_s - tf / 1000.0 > FEATURES_STALE_S:
            return
        t = tf / 1000.0
        if self._rows and t <= self._rows[-1][0] + 1e-6:
            return
        r = _call(getattr(self.ctx, "rhythm", None))
        tr = _f(r.get("t_ms")) if isinstance(r, dict) else None
        rf = r if tr is not None and abs(t - tr / 1000.0) <= RHYTHM_STALE_S else {}
        lv = _f(f.get("rms_dbfs"))
        gate = _f(f.get("gate"), 0.0) or 0.0
        self._rows.append((t, lv, _f(f.get("lufs_m")), _f(f.get("lufs_s")), _f(rf.get("low_db")),
                           _f(rf.get("onset_rate")), _f(rf.get("percussive_ratio")), gate))
        sec = math.floor(t)
        if self._sec is None or sec != self._sec:
            tot, n, gmax = self._sec_acc
            if n >= FLOOR_MIN_ROWS and gmax < self.cfg["stop"]["floor_gate"]:
                self._floor_secs.append(tot / n)      # a quiet second: the room's (or the stream's) own floor
                if len(self._floor_secs) >= FLOOR_MIN_N:
                    self.floor = _median(list(self._floor_secs))
            self._sec, self._sec_acc = sec, [0.0, 0, 0.0]
        if lv is not None:
            self._sec_acc = [self._sec_acc[0] + lv, self._sec_acc[1] + 1, max(self._sec_acc[2], gate)]
        v = _call(getattr(self.ctx, "ml", None))
        tags = v.get("tags") if isinstance(v, dict) and isinstance(v.get("tags"), dict) else None
        if tags is not None and _f(tags.get("t")) is not None and _f(tags.get("voice_p")) is not None:
            tt = float(tags["t"])
            if now_s - tt <= contract.ml_stale_s(v, "tags", 3.0) and (not self._voice or tt > self._voice[-1][0]):
                self._voice.append((tt, float(tags["voice_p"])))

    def _window(self, t_from: float, t_to: float, col: int) -> list[Optional[float]]:
        return [r[col] for r in self._rows if t_from <= r[0] < t_to]

    # -- signs ---------------------------------------------------------------------------------------------------------------
    def _signs(self, now_s: float, mc: Optional[dict]) -> None:
        """Each detector that holds now stamps its sign (self._sign_t). key_change and phrase_line are per cue."""
        if not self._rows:
            return
        sc = self.cfg["signs"]
        t = self._rows[-1][0]
        seen = []

        def jump(col: int, db: float) -> bool:
            a, b = _mean(self._window(t - 0.3, t + 1e-6, col)), _mean(self._window(t - 2.3, t - 0.3, col))
            return a is not None and b is not None and a - b >= db

        if jump(2, sc["louder_db"]):
            seen.append("louder")
        if jump(4, sc["low_in_db"]):
            seen.append("low_in")
        a, b = _mean(self._window(t - 0.5, t + 1e-6, 5)), _mean(self._window(t - 4.0, t - 3.0, 5))
        if a is not None and b is not None and a - b >= sc["busier_rate"] and a >= b * sc["busier_ratio"]:
            seen.append("busier")
        a, b = _mean(self._window(t - 0.5, t + 1e-6, 6)), _mean(self._window(t - 3.5, t - 0.5, 6))
        if a is not None and b is not None:
            if a - b >= sc["perc_jump"]:
                seen.append("drums_in")
            elif b - a >= sc["perc_jump"]:
                seen.append("drums_out")
        # stop_return: a dip of stop_return_db under the 2 s median before it, then back near that median, within
        # min_s..max_s (the median is the one from the dip's start: a long dip would drag a moving one down)
        lv = self._rows[-1][1]
        if lv is not None:
            if self._dip is None:
                ref = _median(self._window(t - 2.5, t - 0.5, 1))
                if ref is not None and lv <= ref - sc["stop_return_db"]:
                    self._dip = (t, ref)
            else:
                t_dip, ref = self._dip
                if lv >= ref - 6.0:
                    if sc["stop_return_min_s"] <= t - t_dip <= sc["stop_return_max_s"]:
                        seen.append("stop_return")
                    self._dip = None
                elif t - t_dip > sc["stop_return_max_s"]:
                    self._dip = None
        if self._voice:
            tv, vp = self._voice[-1]
            recent = [p for (x, p) in self._voice if tv - x <= sc["voice_window_s"]]
            if vp >= sc["voice_on"] and min(recent) <= sc["voice_off"]:
                seen.append("vocal_in")
            elif vp <= sc["voice_off"] and max(recent) >= sc["voice_on"]:
                seen.append("vocal_out")
        for s in seen:
            self._sign_t[s] = now_s

    def _key_now(self, mc: Optional[dict]) -> Optional[str]:
        if mc is None or not mc.get("key") or (_f(mc.get("key_strength"), 0.0) or 0.0) < \
                self.cfg["signs"]["key_min_strength"]:
            return None
        return f"{mc['key']} {mc.get('mode') or ''}".strip()

    # -- standing cues ---------------------------------------------------------------------------------------------------
    def _music_now(self) -> bool:
        sits = _call(self.ctx.situations)
        rd = sits.get("music_audible") if isinstance(sits, dict) else None
        v = rd.get("value") if isinstance(rd, dict) else None
        return v is not False                         # unknown: any sound counts (degrade)

    def _held(self) -> bool:
        return _call(getattr(self.ctx, "held", None)) is True

    def _track_id(self, mc: Optional[dict]) -> int:
        tr = mc.get("track") if mc is not None and isinstance(mc.get("track"), dict) else {}
        tid = tr.get("id")
        return int(tid) if isinstance(tid, int) and not isinstance(tid, bool) else 0

    def _standing(self, now_s: float, mc: Optional[dict]) -> None:
        sc, fc = self.cfg["stop"], self.cfg["fade"]
        rows = self._rows
        phase = ((mc or {}).get("section") or {}).get("phase") if mc is not None else None
        if phase != "outro":
            self._outro_done = False
        floor = self.floor
        if not rows:
            self._playing = False
            self._phase = phase
            return
        t = rows[-1][0]
        self.ref = ref = _median(self._window(t - sc["ref_s"] - 0.5, t - 0.5, 1))
        music = self._music_now()
        self._playing = bool(ref is not None and music
                             and ref - (floor if floor is not None else NO_FLOOR_DBFS) >= sc["collapse_db"])
        d = self._dark
        if d is not None:
            self._dark_tick(now_s, t, floor)
        elif ref is not None and self._stop_on and self._playing:
            self._stop_check(now_s, t, ref, floor, mc)
        if self._dark is None and self._fade_on and phase == "outro" and self._phase == "outro" \
                and not self._outro_done and music:
            self._fade_fire(now_s, t, floor, mc)
        self._phase = phase

    def _quiet_line(self, floor: Optional[float]) -> float:
        """"Near the stream's floor": floor_margin_db over it, or quiet_dbfs before there is one."""
        sc = self.cfg["stop"]
        return floor + sc["floor_margin_db"] if floor is not None else sc["quiet_dbfs"]

    def _stop_check(self, now_s: float, t: float, ref: float, floor: Optional[float], mc: Optional[dict]) -> None:
        sc = self.cfg["stop"]
        n = int(sc["quiet_ticks"])
        quiet = ref - sc["collapse_db"]
        recent = [r for r in self._rows if r[0] > t - 1.0]
        if len(recent) < n + 1:
            return
        loud = [r for r in recent if r[1] is not None and r[1] >= ref - sc["loud_db"]]
        if not loud:
            return
        last_loud = loud[-1][0]
        after = [r for r in recent if r[0] > last_loud]
        if len(after) < n or any(r[1] is None or r[1] > quiet for r in after[-n:]):
            return
        first_q = next((r for r in after if r[1] is not None and r[1] <= quiet), None)
        if first_q is None or first_q[0] - last_loud > sc["fall_s"] + 0.05:
            return                                    # it slid down, not a collapse
        if any(r[1] is None or r[1] > quiet for r in after if r[0] >= first_q[0]):
            return                                    # it came back up in between
        lv = after[-1][1]
        if lv > self._quiet_line(floor):
            return
        if self._held():
            self.counts["held"] += 1
            self._log_event({"kind": "held", "cue": "stop"})
            self._dark = {"kind": "stop", "id": None, "held": True, "since": now_s, "ref": ref}
            return
        tid = self._track_id(mc)
        ev_id = f"track_end:{tid}"
        at = last_loud + 0.1
        self._publish({"kind": "track_end", "id": ev_id, "at_ms": round(at * 1000.0, 1), "detail": "stop",
                       "via": "cues", "track_id": tid,
                       "transition": self._dark_transition(sc["out_s"], sc["in_s"], sc["dark_max_s"])}, now_s, at)
        self._dark = {"kind": "stop", "id": ev_id, "track_id": tid, "since": now_s, "at_s": at, "ref": ref,
                      "back_n": 0, "handed": False}
        self.counts["stop"] += 1
        self._log_event({"kind": "fire", "cue": "stop", "id": ev_id, "at_s": round(at, 2), "ref_dbfs": round(ref, 1),
                         "level_dbfs": round(lv, 1), "floor_dbfs": round(floor, 1) if floor is not None else None})
        log.info("cues: an abrupt stop: the screens go dark (%s)", ev_id)

    def _fade_fire(self, now_s: float, t: float, floor: Optional[float], mc: Optional[dict]) -> None:
        fc = self.cfg["fade"]
        self._outro_done = True
        w = [r for r in self._rows if r[0] >= t - fc["slope_s"]]
        slope = _slope([r[0] for r in w], [r[3] for r in w])
        lufs = self._rows[-1][3]
        if lufs is None:
            return
        target = (floor + fc["floor_above_db"]) if floor is not None else fc["floor_lufs"]
        est = (lufs - target) / -slope if slope is not None and slope < -1e-3 else fc["max_s"]
        out_s = round(max(fc["min_s"], min(fc["max_s"], est)), 1)
        if self._held():
            self.counts["held"] += 1
            self._log_event({"kind": "held", "cue": "fade"})
            return
        tid = self._track_id(mc)
        ev_id = f"track_end:{tid}"
        self._publish({"kind": "track_end", "id": ev_id, "at_ms": round(now_s * 1000.0, 1), "detail": "fade",
                       "via": "cues", "track_id": tid,
                       "transition": self._dark_transition(out_s, fc["in_s"], fc["dark_max_s"])}, now_s, now_s)
        self._dark = {"kind": "fade", "id": ev_id, "track_id": tid, "since": now_s, "at_s": now_s, "lufs_at": lufs,
                      "back_since": None, "handed": False}
        self.counts["fade"] += 1
        self._log_event({"kind": "fire", "cue": "fade", "id": ev_id, "out_s": out_s,
                         "slope_db_s": round(slope, 3) if slope is not None else None, "lufs_s": round(lufs, 1),
                         "target_lufs": round(target, 1)})
        log.info("cues: the music fades out: the screens fade to dark over %.1f s (%s)", out_s, ev_id)

    @staticmethod
    def _dark_transition(out_s: float, in_s: float, dark_max_s: float) -> dict:
        """contract.TrackTransition for a standing cue's dark."""
        return {"style": "fade-dark", "out_s": round(float(out_s), 2), "in_s": round(float(in_s), 2),
                "seconds": None, "on": "now", "change": "big", "dark_max_s": round(float(dark_max_s), 2)}

    def _dark_tick(self, now_s: float, t: float, floor: Optional[float]) -> None:
        """After a standing cue: sound back (before structure's own end took over) -> track_end "sound_back"; the
        watch ends after watch_s, at structure's end (it sends its own), or at a track change."""
        d = self._dark
        sc, fc = self.cfg["stop"], self.cfg["fade"]
        watch = sc["watch_s"] if d["kind"] == "stop" else fc["watch_s"]
        if d.get("handed") or now_s - d["since"] > watch:
            if not d.get("handed"):
                self._log_event({"kind": "dark_over", "cue": d["kind"], "id": d.get("id"), "why": "watch over"})
            self._dark = None
            return
        if d["kind"] == "stop":
            lv_ok = [r for r in list(self._rows)[-int(sc["back_ticks"]):] if r[0] > d["since"] - 1e-6]
            want = max(d["ref"] - sc["back_db"], (floor if floor is not None else NO_FLOOR_DBFS) + sc["back_db"])
            back = len(lv_ok) >= int(sc["back_ticks"]) and all(r[1] is not None and r[1] >= want for r in lv_ok)
            at = lv_ok[0][0] if back else None
        else:
            lufs = self._rows[-1][3]
            up = lufs is not None and lufs >= d["lufs_at"] + fc["back_db"]
            if up and d["back_since"] is None:
                d["back_since"] = t
            elif not up:
                d["back_since"] = None
            back = d["back_since"] is not None and t - d["back_since"] >= fc["back_s"]
            at = d["back_since"]
        if not back:
            return
        if d.get("held") or d.get("id") is None:
            self._dark = None                         # nothing went dark (a person held the look)
            return
        self._publish({"kind": "track_end", "id": d["id"], "at_ms": round(at * 1000.0, 1), "detail": "sound_back",
                       "via": "cues", "track_id": d["track_id"]}, now_s, at)
        self.counts["sound_back"] += 1
        self._log_event({"kind": "sound_back", "cue": d["kind"], "id": d["id"], "after_s": round(at - d["at_s"], 2)})
        self._dark = None

    # -- forecasts ---------------------------------------------------------------------------------------------------------
    def _forecasts(self, now_s: float, mc: Optional[dict], b: Optional[dict]) -> None:
        for c in (b or {}).get("coming") or []:
            if not isinstance(c, dict) or not isinstance(c.get("id"), str) or c["id"] in self._seen:
                continue
            if c.get("kind") not in contract.COMING_KINDS or _f(c.get("t0_s")) is None or _f(c.get("t1_s")) is None:
                continue
            self._seen.append(c["id"])
            self._arm(c, now_s, mc)
        for kind in list(self._armed):
            self._cue_tick(self._armed[kind], now_s, mc)

    def _arm(self, c: dict, now_s: float, mc: Optional[dict]) -> None:
        old = self._armed.get(c["kind"])
        if old is not None:
            self._resolve_cue(old, "replaced", now_s)
        self._n += 1
        cue = {"n": self._n, "id": f"cue:{c['kind']}:{self._n}", "kind": c["kind"], "t0": float(c["t0_s"]),
               "t1": float(c["t1_s"]), "signs": [s for s in c.get("signs") or [] if s in contract.COMING_SIGNS],
               "size": c.get("size") if c.get("size") in contract.COMING_SIZES else "medium",
               "conf": _f(c.get("conf"), 0.0) or 0.0, "from": c["id"], "armed_s": now_s, "key0": self._key_now(mc),
               "sched": None, "held_logged": False}
        self._armed[c["kind"]] = cue
        self.counts["armed"] += 1
        self._log_event({"kind": "arm", **self._cue_log(cue, now_s)})

    def _cue_log(self, cue: dict, now_s: float) -> dict:
        return {"id": cue["id"], "cue": cue["kind"], "size": cue["size"], "conf": round(cue["conf"], 3),
                "signs": list(cue["signs"]), "window_in_s": [round(cue["t0"] - now_s, 1), round(cue["t1"] - now_s, 1)],
                "from": cue["from"]}

    def _lead(self) -> float:
        fc = self.cfg["forecast"]
        bar = self._grid.bar_s if self._grid.ok and self._grid.bar_s else NO_GRID_BAR_S
        return max(fc["min_lead_s"], fc["lead_bars"] * bar)

    def _bar_s(self) -> float:
        return self._grid.bar_s if self._grid.ok and self._grid.bar_s else NO_GRID_BAR_S

    def _seen_sign(self, cue: dict, now_s: float, mc: Optional[dict],
                   since: Optional[float] = None) -> Optional[str]:
        """The first of the cue's signs seen inside its window (and within seen_s of now, or since ``since``)."""
        fc, sc = self.cfg["forecast"], self.cfg["signs"]
        lo = max(cue["t0"] - fc["slack_s"], now_s - sc["seen_s"] if since is None else since)
        hi = cue["t1"] + fc["slack_s"]
        for s in cue["signs"]:
            if s == "key_change":
                k = self._key_now(mc)
                if cue["key0"] is None:
                    cue["key0"] = k
                elif k is not None and k != cue["key0"] and lo <= now_s <= hi:
                    self._sign_t["key_change"] = now_s
                    return s
                continue
            ts = self._sign_t.get(s)
            if ts is not None and lo <= ts <= hi:
                return s
        return None

    def _phrase_line(self, cue: dict, now_s: float) -> Optional[float]:
        """The phrase line (16 bars at phrase16_conf, else 8) inside the cue's window, at least the lead ahead."""
        g, fc = self._grid, self.cfg["forecast"]
        if not g.ok:
            return None
        lo = max(cue["t0"] - fc["slack_s"], now_s + self._lead())
        hi = cue["t1"] + fc["slack_s"]
        if g.phrase_conf >= fc["phrase16_conf"]:
            line = g.line_after(lo, 16)
            if line <= hi:
                return line
        line = g.line_after(lo, 8)
        return line if line <= hi else None

    def _early(self, cue: dict, now_s: float, mc: Optional[dict]) -> Optional[str]:
        """An early sign that agrees with a scheduled line: structure's build, the loudness rising, or one of the
        cue's own signs since it was armed."""
        if ((mc or {}).get("section") or {}).get("phase") == "build":
            return "a build"
        fc = self.cfg["forecast"]
        if self._rows:
            t = self._rows[-1][0]
            w = [r for r in self._rows if r[0] >= t - fc["rise_s"]]
            sl = _slope([r[0] for r in w], [r[3] for r in w])
            if sl is not None and sl >= fc["rise_db_s"]:
                return "the music getting louder"
        return self._seen_sign(cue, now_s, mc, since=cue["armed_s"])

    def _cue_tick(self, cue: dict, now_s: float, mc: Optional[dict]) -> None:
        fc = self.cfg["forecast"]
        if cue["sched"] is not None:
            at = cue["sched"]["at"]
            if now_s >= at - self._lead() - 0.05:
                if self._held():
                    self._resolve_cue(cue, "held", now_s)
                    return
                self._fire(cue, at, now_s, cue["sched"]["via"])
            return
        if now_s > cue["t1"] + fc["slack_s"]:
            self._resolve_cue(cue, "missed", now_s)
            return
        if cue["conf"] >= fc["min_conf"]:
            line = self._phrase_line(cue, now_s)
            if line is not None:
                why = self._early(cue, now_s, mc)
                if why:
                    cue["sched"] = {"at": line, "via": f"the phrase line ({why})"}
                    self.counts["scheduled"] += 1
                    self._log_event({"kind": "schedule", "id": cue["id"], "cue": cue["kind"], "at_s": round(line, 2),
                                     "in_s": round(line - now_s, 1), "why": why})
                    return
        if now_s < cue["t0"] - fc["slack_s"]:
            return
        g = self._grid
        at = None
        via = None
        if "phrase_line" in cue["signs"] and g.ok:
            line = g.line_after(now_s + self._lead(), 8)
            if line - now_s <= self._lead() + self._bar_s() and line <= cue["t1"] + fc["slack_s"]:
                at, via = line, "phrase_line"
        if at is None:
            s = self._seen_sign(cue, now_s, mc)
            if s is None:
                return
            via = s
            at = g.line_after(now_s + fc["min_lead_s"], 1) if g.ok else now_s + fc["min_lead_s"]
        if self._held():
            if not cue["held_logged"]:
                cue["held_logged"] = True
                self.counts["held"] += 1
                self._log_event({"kind": "held", "id": cue["id"], "cue": cue["kind"], "via": via})
            return
        self._fire(cue, at, now_s, via)

    def _fire(self, cue: dict, at: float, now_s: float, via: str) -> None:
        bar = self._bar_s()
        if self._last_fire is not None and abs(at - self._last_fire) < bar - 1e-3:
            if cue["sched"] is not None:
                self._resolve_cue(cue, "skipped", now_s, why="one fire per bar")
                return
            at = self._grid.line_after(self._last_fire + bar, 1) if self._grid.ok else self._last_fire + bar
        kind = FIRE_KIND.get(cue["size"])
        g = self._grid
        if kind is not None:
            self._publish({"kind": kind, "id": cue["id"], "at_ms": round(at * 1000.0, 1),
                           "detail": f"forecast: {cue['kind']}", "via": "cues",
                           "cue": {"kind": cue["kind"], "size": cue["size"], "conf": round(cue["conf"], 3),
                                   "sign": via, "window_ms": [round(cue["t0"] * 1000.0, 1),
                                                              round(cue["t1"] * 1000.0, 1)]}}, now_s, at)
            self.counts["published"] += 1
            self._last_fire = at
        else:
            self.counts["logged_only"] += 1
        self.counts["fired"] += 1
        self._log_event({"kind": "fire", "id": cue["id"], "cue": cue["kind"], "size": cue["size"], "event": kind,
                         "at_s": round(at, 2), "in_s": round(at - now_s, 2), "via": via,
                         "published": kind is not None, "bar": g.bar_at(at + 1e-6) if g.ok else None})
        if kind is not None:
            log.info("cues: %s fires %s at the %s (%s)", cue["id"], kind, "line" if cue["sched"] else "downbeat", via)
        self._resolve_cue(cue, "fired", now_s, at=at, via=via)

    def _resolve_cue(self, cue: dict, result: str, now_s: float, *, why: Optional[str] = None,
                     at: Optional[float] = None, via: Optional[str] = None) -> None:
        if self._armed.get(cue["kind"]) is cue:
            del self._armed[cue["kind"]]
        if result != "fired":
            self.counts[result] += 1
            self._log_event({"kind": result, "id": cue["id"], "cue": cue["kind"], **({"why": why} if why else {})})
        self._outcomes.append({"id": cue["id"], "kind": cue["kind"], "size": cue["size"], "conf": cue["conf"],
                               "t0": cue["t0"], "t1": cue["t1"], "result": result, "at": at, "via": via})

    def _outcome_tick(self, now_s: float) -> None:
        """Each forecast's outcome once its window (+ slack) has passed: what structure itself heard in it."""
        slack = self.cfg["forecast"]["slack_s"]
        keep = []
        for o in self._outcomes:
            if now_s < o["t1"] + slack + 1.0:
                keep.append(o)
                continue
            saw = sorted({k for (k, a, _i, d) in self._sevents
                          if k in ("section_change", "drop", "breakdown", "build", "track_change", "track_end")
                          and o["t0"] - slack <= a <= o["t1"] + slack and d != "sound_back"})
            self._log_event({"kind": "outcome", "id": o["id"], "cue": o["kind"], "size": o["size"],
                             "conf": round(o["conf"], 3), "result": o["result"], "via": o["via"],
                             "fired_at_s": round(o["at"], 2) if o["at"] is not None else None, "structure_saw": saw})
            self.counts["outcome_" + ("heard" if saw else "quiet")] += 1
        self._outcomes = keep[-64:]

    # -- publishing, the sheet, the snapshot, the log ----------------------------------------------------------------------
    def _publish(self, ev: dict, now_s: float, at_s: float) -> None:
        g = self._grid
        self._seq += 1
        ev = dict(ev, t_ms=round(now_s * 1000.0, 1), seq=self._seq)
        if g.has_grid:
            ev["beat"] = round(g.beat_at(at_s), 3)
            if g.ok:
                ev["bar"] = g.bar_at(at_s + 1e-6)
                ev["phrase"] = math.floor(ev["bar"] / g.pb)
        try:
            self.ctx.publish({"type": contract.ROOM_EVENT, "data": ev})
        except Exception:                             # noqa: BLE001
            log.debug("cues: publish failed", exc_info=True)

    def _words(self, cue: dict, now_s: float) -> str:
        a, b = max(0, round(cue["t0"] - now_s)), max(0, round(cue["t1"] - now_s))
        if cue["sched"] is not None:
            when = f"on the phrase line in {max(0, round(cue['sched']['at'] - now_s))} s"
        elif now_s >= cue["t0"]:
            when = "any moment now"
        elif a == b:
            when = f"likely in about {a} s"
        else:
            when = f"likely in {a}-{b} s"
        if cue["size"] == "small":
            return f"Watching for {KIND_NOUN.get(cue['kind'], 'a change')}, {when}"
        head = "Big change" if cue["size"] == "big" else "Change"
        return f"{head} {KIND_AT.get(cue['kind'], 'at the next change')}, {when}"

    def _build_sheet(self, now_s: float) -> None:
        out = []
        for cue in sorted(self._armed.values(), key=lambda c: c["t0"]):
            out.append({"kind": cue["kind"], "words": self._words(cue, now_s),
                        "window_s": [round(max(0.0, cue["t0"] - now_s), 1), round(max(0.0, cue["t1"] - now_s), 1)],
                        "size": cue["size"]})
        d = self._dark
        if d is not None and d.get("id"):
            out.append({"kind": d["kind"], "words": DARK_WORDS[d["kind"]], "window_s": None, "size": None})
        elif self._playing:
            if self._ending == "segue":
                out.append({"kind": "segue", "words": READY_WORDS["segue"], "window_s": None, "size": None})
            for k, on in (("stop", self._stop_on), ("fade", self._fade_on)):
                if on:
                    out.append({"kind": k, "words": READY_WORDS[k], "window_s": None, "size": None})
        self._sheet = out

    def _build_snapshot(self, now_s: float) -> None:
        ms = sorted(self._tick_ms)
        last = self._rows[-1] if self._rows else None
        self._snap = {
            "armed": list(self._sheet),
            "cues": [{**self._cue_log(c, now_s), "scheduled_in_s": round(c["sched"]["at"] - now_s, 1)
                      if c["sched"] else None} for c in self._armed.values()],
            "standing": {"stop": self._stop_on, "fade": self._fade_on, "ending": self._ending,
                         "playing": self._playing,
                         "dark": {k: v for k, v in self._dark.items() if k in ("kind", "id", "track_id", "handed")}
                         if self._dark else None},
            "level": {"dbfs": round(last[1], 1) if last and last[1] is not None else None,
                      "ref_dbfs": round(self.ref, 1) if self.ref is not None else None,
                      "floor_dbfs": round(self.floor, 1) if self.floor is not None else None},
            "signs": {s: round(now_s - t, 1) for s, t in sorted(self._sign_t.items())},
            "counts": dict(self.counts), "problems": list(self.problems),
            "tick_ms_p95": round(ms[min(len(ms) - 1, int(0.95 * len(ms)))], 3) if ms else None,
            "log": list(self._log)[-50:]}

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
        except Exception:                             # noqa: BLE001 - the in-memory log still has it
            pass


def attach(ctx: contract.BrainContext, **kw: Any) -> Cues:
    """The brain's ``cues``. Keyword args go to Cues (__main__ passes ``log_path``)."""
    return Cues(ctx, **kw)
