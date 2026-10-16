"""Music-derived direction: the show bus proposes the director's direction (owner, 15 Oct 2026).

The only live input is MaestroDMX over Art-Net (config/room/patch.yaml; docs/av/evidence/r3_maestro.md).  Maestro
sends no tempo, genre or section names, so this module reads only what its channels carry, through the session's
Sampler snapshot (inputs.py): ACTIVITY (one global musical-activity level), the DIMMER pulse (its edges -> how often
and how hard the music hits; the beat follower's bpm when locked) and the GOBO step (``effect``: a section change).
No audio DSP.  Later an LLM director fed by a live audio relay can propose direction the same way (a layer above).

Features (loop thread, from ``snapshot()``: never ``peek()``, whose idle easing would turn a stale bus into 0.5):
  act    = clamp((activity_slow - lo) / (hi - lo))           auto.music.activity = [lo, hi], for calibrating Maestro
  E      = EMA(act, tau 4 s)                                  (on top of the bus's own tau 2 s)
  rate   = accepted pulse edges per second over <= 8 s        (0 until the window spans 4 s)
  punch  = clamp((pulse_peak - 0.10) / 0.60)                  the follower's decaying pulse peak
  D      = EMA(0.7 * clamp(rate / 2.2) + 0.3 * punch, tau 8 s)            "drive"
  energy = (1 - w) * E + w * D, w = 0.3 if a pulse edge was accepted in the last 30 s else 0
  tempo  = clamp((bpm - 60) / 90) while the follower is locked, else None
Proposal: pace 0.1 + 0.8 * (0.7 e + 0.3 tempo | e), intensity 1.0 (the per-item mappings make calm music calm), change_rate 0.9 e, fx.density 0.1 + 0.8 e,
fx.rate 0.05 + 0.85 e, and per band (energy edges 0.30 / 0.55 / 0.80, +-0.04 hysteresis; the 0.30-0.55 band splits
on drive into "groove" < 0.55 / "pulse" >= 0.65) moods, fx.moods and transition_style (BANDS).
A GOBO change (a stable new ``effect`` on a fresh frame) starts a 1.5 s settle: E is then re-seeded from the last
second of raw activity and the proposal is published at once; the session holds its director's ``due`` meanwhile so
a section-triggered pick uses the new section's direction.

Publishing: checks at most once a second; numbers re-publish when one moved >= 0.05 and >= 4 s since the last;
a band change, a section re-seed or a change of ``m`` publish at once.

The panel's direction is a TRIM around the music (``effective_direction``): numbers are
``clamp(panel + m * (music - neutral))`` (neutral = DIRECTION_DEFAULTS: 0.5, intensity 1.0), so a panel at its
defaults means "follow the music" and panel energy 0.3 means "0.2 calmer than the music"; the music's words (moods,
transition_style, fx.moods) apply only where the panel left them empty, and only while m >= 0.5.  genre,
palette_family, fx.stages, source and note always come from the panel.  ``m = auto.music.weight x liveness``, in
0.25 steps: a stale bus holds 10 s then fades to 0 over 20 s; recovery needs 3 s of fresh frames then ramps up over
10 s (from cold: 3 s then on at once).  m = 0 is exactly the panel's direction: the behaviour before this module.

The room's director (show_service's ``room.direction`` on --music-bus, docs/SHOW_ENGINE.md §8.7) takes this slot when
it speaks: while a validated room.direction with a vibe is under ROOM_STALE_S old (it republishes every 5 s; a
null vibe is the room's neutral), its numbers and words are the proposal (genre included, filling an empty panel
genre like the moods), liveness counts it as fresh, a new section id bumps ``epoch``, and the mapping signals take
its energy and groove (as drive).  Otherwise the Maestro features above propose, as before.  Switching source
drops the proposal and publishes at once.

Pure state and arithmetic, no I/O: sessions.py calls ``update`` from its 10 Hz tick.
"""
from __future__ import annotations

import collections
import math
from typing import Any, Optional

from .director import AUTO_MUSIC_DEFAULTS, DIRECTION_DEFAULTS, FX_DIRECTION_DEFAULTS

EVAL_S = 1.0
TAU_E_S, TAU_D_S = 4.0, 8.0
SETTLE_S = 1.5
RESEED_WINDOW_S = 1.0
RATE_WINDOW_S, RATE_MIN_SPAN_S = 8.0, 4.0
EDGE_RATE_FULL = 2.2            # edges/s that count as full drive (~132 bpm)
PEAK_LO, PEAK_HI = 0.10, 0.70
DRIVE_RECENT_S = 30.0           # drive counts only while pulse edges are arriving
DRIVE_WEIGHT = 0.3
DEADBAND = 0.05
APPLY_MIN_S = 4.0
EDGES = (0.30, 0.55, 0.80)      # still | warm | lively | peak
BAND_HYST = 0.04
SPLIT_UP, SPLIT_DOWN = 0.65, 0.55
HOLD_S, FADE_S = 10.0, 20.0     # stale: hold, then fade m to 0
RECOVER_S, RAMP_S = 3.0, 10.0   # fresh again: wait, then ramp m back
BANDS = {   # band -> (scene moods, fx moods, transition_style); words from config/live/vocab.yaml
    "still": (["calm", "mellow", "elegant", "dreamy"], ["calm", "mellow", "elegant", "dreamy"], "gentle"),
    "groove": (["mellow", "elegant", "warm", "organic"], ["mellow", "warm", "elegant", "liquid"], "gentle"),
    "pulse": (["dreamy", "hypnotic", "liquid", "organic"], ["dreamy", "hypnotic", "liquid", "mellow"], "lively"),
    "lively": (["bright", "playful", "geometric", "hypnotic"], ["playful", "retro", "hypnotic", "geometric"], "lively"),
    "peak": (["euphoric", "hypnotic", "bright", "aggressive"], ["euphoric", "hypnotic", "glitchy", "bright"], "wild"),
}
ROOM_STALE_S = 12.0             # room.direction republishes every 5 s
NUMERIC = ("energy", "pace", "intensity", "change_rate")
FX_NUMERIC = ("density", "rate")


def _c01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def effective_direction(panel: dict, music: Optional[dict], m: float) -> dict:
    """The direction the director uses: the panel's, trimmed around the music's proposal by ``m`` (see above)."""
    if music is None or m <= 0.0:
        return panel
    out = dict(panel, fx=dict(panel.get("fx") or FX_DIRECTION_DEFAULTS))
    for k in NUMERIC:
        out[k] = round(_c01(panel[k] + m * (music[k] - DIRECTION_DEFAULTS[k])), 3)
    for k in FX_NUMERIC:
        out["fx"][k] = round(_c01(out["fx"][k] + m * (music["fx"][k] - FX_DIRECTION_DEFAULTS[k])), 3)
    if m >= 0.5:
        if not panel.get("moods"):
            out["moods"] = list(music["moods"])
        if panel.get("transition_style") is None:
            out["transition_style"] = music["transition_style"]
        if panel.get("genre") is None and music.get("genre"):
            out["genre"] = music["genre"]
        if not out["fx"].get("moods"):
            out["fx"]["moods"] = list(music["fx"]["moods"])
    return out


def music_fields(panel: dict, music: Optional[dict], m: float) -> list[str]:
    """Which direction fields the music is moving right now (for the console)."""
    if music is None or m <= 0.0:
        return []
    out = [*NUMERIC, *(f"fx.{k}" for k in FX_NUMERIC)]
    if m >= 0.5:
        if not panel.get("moods"):
            out.append("moods")
        if panel.get("transition_style") is None:
            out.append("transition_style")
        if panel.get("genre") is None and music.get("genre"):
            out.append("genre")
        if not (panel.get("fx") or {}).get("moods"):
            out.append("fx.moods")
    return out


def pick_key(d: dict) -> tuple:
    """What a prepared pick depends on: a change here means the next scene should be re-picked."""
    return (round(float(d.get("energy", 0.5)) * 10), tuple(d.get("moods") or ()), d.get("transition_style"),
            d.get("genre"), d.get("palette_family"))


class MusicDirection:
    """One session's music-derived direction (loop thread)."""

    def __init__(self, cfg: Optional[dict] = None):
        self.cfg = dict(AUTO_MUSIC_DEFAULTS)
        self.enabled = False
        self.m = 0.0
        self.lv = 0.0                    # liveness 0..1 (before weight and quantising)
        self.proposal: Optional[dict] = None
        self.band: Optional[str] = None
        self.features: dict = {}
        self.last_change: Optional[dict] = None
        self._split = "groove"
        self._t: Optional[float] = None
        self._E: Optional[float] = None
        self._D: Optional[float] = None
        self._edges: collections.deque = collections.deque()
        self._raw: collections.deque = collections.deque()
        self._last_edge_seen: Optional[float] = None
        self._accepted: Optional[int] = None
        self._section: Optional[tuple] = None
        self._settle_at: Optional[float] = None
        self._fresh_since: Optional[float] = None
        self._stale_since: Optional[float] = None
        self._cold = True
        self._last_eval = -1e9
        self._last_apply = -1e9
        self._lv_t: Optional[float] = None
        self.epoch = 0                    # bumped on a section change or a new energy band (the mappings' section mode)
        self._epoch_band: Optional[int] = None
        self._signals_t: Optional[float] = None
        self._drive_seen = False
        self._tempo: Optional[float] = None
        self.source: Optional[str] = None    # who proposes: "room" (show_service) | "maestro" | None
        self.room: Optional[dict] = None     # the room.direction in use (SessionManager.room_direction_now)
        self._room_section = None
        self.configure(cfg)

    def configure(self, cfg: Optional[dict]) -> None:
        """auto.music (validated by director.validate_auto); None (no auto block) disables."""
        self.enabled = cfg is not None and bool(cfg.get("enabled", True))
        self.cfg = dict(AUTO_MUSIC_DEFAULTS, **(cfg or {}))

    def settling(self, now: float) -> bool:
        return self.m > 0.0 and self._settle_at is not None

    # ------------------------------------------------------------------ the 10 Hz step
    def update(self, now: float, snap: Any, fresh: bool, follow: Any, active: bool,
               room: Optional[dict] = None) -> Optional[str]:
        """Step the features; -> a cause ("music", "band", "section", "online", "offline", "off", "m") when the
        effective direction should be recomputed, else None.  ``room``: the room director's live direction, or None."""
        m_was = self.m
        reseeded = False
        if snap is not None and fresh:      # every tick, director or not: the per-item mappings read the features
            reseeded = self._features(now, snap, tuple(follow or ()))
        self.room = room
        if room is not None:
            sec = (room.get("section") or {}).get("id") if isinstance(room.get("section"), dict) else None
            if self._room_section is not None and sec != self._room_section:
                self.epoch += 1             # the room's new section: section-mode mappings re-sample
                reseeded = True
            self._room_section = sec
        else:
            self._room_section = None
        if not (active and self.enabled):
            self.m, self.lv = 0.0, 0.0
            self._fresh_since = self._stale_since = None
            self._cold = True
            self.source = None
            return "off" if m_was > 0.0 else None
        src = "room" if room is not None else "maestro"
        switched = self.source is not None and src != self.source
        if src != self.source:
            self.source, self.proposal, self.band = src, None, None
        self._liveness(now, room is not None or (snap is not None and fresh))
        cause = None
        if room is not None:
            cause = self._propose_room(now, room, reseeded)
        elif snap is not None and fresh:
            cause = self._propose(now, reseeded)
        if self.m != m_was:
            return "online" if m_was == 0.0 else "offline" if self.m == 0.0 else "m"
        if switched and cause is None:
            cause = "band"
        return cause if self.m > 0.0 else None

    def _propose_room(self, now: float, room: dict, reseeded: bool) -> Optional[str]:
        d = room["direction"]
        target = {k: d[k] for k in NUMERIC}
        target.update(moods=list(d.get("moods") or []), transition_style=d.get("transition_style"),
                      genre=d.get("genre"), fx={"density": d["fx"]["density"], "rate": d["fx"]["rate"],
                                                "moods": list(d["fx"].get("moods") or [])})
        band = self._band_of(target["energy"], float(room.get("groove") or 0.0))
        p = self.proposal
        cause = None
        if p is None:
            cause = "music"
        elif any(target[k] != p.get(k) for k in ("moods", "transition_style", "genre")) \
                or target["fx"]["moods"] != p["fx"]["moods"]:
            cause = "band"
        elif reseeded:
            cause = "section"
        elif any(abs(target[k] - p[k]) >= DEADBAND for k in NUMERIC) \
                or any(abs(target["fx"][k] - p["fx"][k]) >= DEADBAND for k in FX_NUMERIC):
            cause = "music"
        self.band = band
        if cause is not None:
            self.proposal, self._last_apply = target, now
            self.last_change = {"cause": cause, "at": now}
        return cause

    def _liveness(self, now: float, fresh: bool) -> None:
        dt = 0.0 if self._lv_t is None else max(0.0, now - self._lv_t)
        self._lv_t = now
        if fresh:
            self._stale_since = None
            if self._fresh_since is None:
                self._fresh_since = now
            if now - self._fresh_since >= RECOVER_S:
                if self._cold:
                    self.lv, self._cold = 1.0, False
                else:
                    self.lv = min(1.0, self.lv + dt / RAMP_S)
        else:
            self._fresh_since = None
            if self._stale_since is None:
                self._stale_since = now
            if now - self._stale_since >= HOLD_S:
                self.lv = max(0.0, self.lv - dt / FADE_S)
                if self.lv == 0.0:
                    self._cold = True
        w = float(self.cfg.get("weight", 1.0))
        self.m = round(_c01(w * self.lv) * 4.0) / 4.0

    def _features(self, now: float, snap: Any, follow: tuple) -> bool:
        lo, hi = self.cfg.get("activity") or (0.0, 1.0)
        span = max(1e-6, hi - lo)
        act = _c01((float(snap.slow.get("activity_slow", 0.0)) - lo) / span)
        dt = EVAL_S if self._t is None else min(5.0, max(0.0, now - self._t))
        self._t = now
        self._raw.append((now, _c01((float(snap.raw.get("activity") or 0.0) - lo) / span)))
        while self._raw and now - self._raw[0][0] > RESEED_WINDOW_S:
            self._raw.popleft()
        reseeded = False
        section = tuple(snap.stable.get(a) for a in follow)
        if self._section is not None and section != self._section:
            self._settle_at = now + SETTLE_S
        self._section = section
        if self._settle_at is not None and now >= self._settle_at:
            self._E = sum(v for _, v in self._raw) / len(self._raw)
            self._settle_at, reseeded = None, True
        fol = snap.follower or {}
        acc = int(fol.get("edges_accepted", 0) or 0)
        if self._accepted is not None and acc < self._accepted:      # the follower was reset
            self._edges.clear()
        if self._accepted is None or acc != self._accepted:
            if self._accepted is not None and acc > self._accepted:
                self._last_edge_seen = now
            self._accepted = acc
        self._edges.append((now, acc))
        while self._edges and now - self._edges[0][0] > RATE_WINDOW_S:
            self._edges.popleft()
        t0, n0 = self._edges[0]
        rate = (acc - n0) / (now - t0) if now - t0 >= RATE_MIN_SPAN_S else 0.0
        punch = _c01((float(fol.get("pulse_peak", 0.0) or 0.0) - PEAK_LO) / (PEAK_HI - PEAK_LO))
        drive = 0.7 * _c01(rate / EDGE_RATE_FULL) + 0.3 * punch
        ae, ad = 1.0 - math.exp(-dt / TAU_E_S), 1.0 - math.exp(-dt / TAU_D_S)
        self._E = act if self._E is None else self._E + (act - self._E) * ae
        self._D = drive if self._D is None else self._D + (drive - self._D) * ad
        w = DRIVE_WEIGHT if self._last_edge_seen is not None and now - self._last_edge_seen <= DRIVE_RECENT_S else 0.0
        energy = _c01((1.0 - w) * self._E + w * self._D)
        i = self._epoch_band
        i = sum(energy >= x for x in EDGES) if i is None else i
        while i < 3 and energy >= EDGES[i] + BAND_HYST:
            i += 1
        while i > 0 and energy < EDGES[i - 1] - BAND_HYST:
            i -= 1
        if reseeded or (self._epoch_band is not None and i != self._epoch_band):
            self.epoch += 1                 # a section change or a new energy band: section-mode mappings re-sample
        self._epoch_band = i
        self._signals_t = now
        self._drive_seen = self._last_edge_seen is not None and now - self._last_edge_seen <= DRIVE_RECENT_S
        locked = snap.locked(now) and snap.period
        bpm = 60.0 / snap.period if locked else None
        tempo = _c01((bpm - 60.0) / 90.0) if bpm else None
        self.features = {"act": round(act, 3), "E": round(self._E, 3), "edge_rate": round(rate, 2),
                         "punch": round(punch, 3), "D": round(self._D, 3), "energy": round(energy, 3),
                         "bpm": None if bpm is None else round(bpm, 1), "locked": bool(locked)}
        self._tempo = tempo
        return reseeded

    def _band_of(self, e: float, d: float) -> str:
        names = ("still", "warm", "lively", "peak")
        if self.band is None:
            i = sum(e >= x for x in EDGES)
        else:
            i = names.index("warm" if self.band in ("groove", "pulse") else self.band)
            while i < 3 and e >= EDGES[i] + BAND_HYST:
                i += 1
            while i > 0 and e < EDGES[i - 1] - BAND_HYST:
                i -= 1
        b = names[i]
        if b == "warm":
            if d >= SPLIT_UP:
                self._split = "pulse"
            elif d < SPLIT_DOWN:
                self._split = "groove"
            b = self._split
        return b

    def _propose(self, now: float, reseeded: bool) -> Optional[str]:
        if not reseeded and now - self._last_eval < EVAL_S:
            return None
        self._last_eval = now
        e = self.features["energy"]
        tempo = getattr(self, "_tempo", None)
        pace_in = 0.7 * e + 0.3 * tempo if tempo is not None else e
        band = self._band_of(e, self.features["D"])
        moods, fx_moods, style = BANDS[band]
        target = {"energy": round(e, 3), "pace": round(0.1 + 0.8 * pace_in, 3), "intensity": DIRECTION_DEFAULTS["intensity"],
                  "change_rate": round(0.9 * e, 3), "moods": list(moods), "transition_style": style,
                  "fx": {"density": round(0.1 + 0.8 * e, 3), "rate": round(0.05 + 0.85 * e, 3), "moods": list(fx_moods)}}
        cause = None
        if self.proposal is None:
            cause = "music"
        elif band != self.band:
            cause = "band"
        elif reseeded:
            cause = "section"
        elif now - self._last_apply >= APPLY_MIN_S and (
                any(abs(target[k] - self.proposal[k]) >= DEADBAND for k in NUMERIC)
                or any(abs(target["fx"][k] - self.proposal["fx"][k]) >= DEADBAND for k in FX_NUMERIC)):
            cause = "music"
        self.band = band
        if cause is not None:
            self.proposal, self._last_apply = target, now
            self.last_change = {"cause": cause, "at": now}
        return cause

    SIGNALS_STALE_S = 2.0

    def signals(self, now: float) -> dict:
        """The music features as mapping signals (binding.py): {energy, drive, punch, tempo, epoch}; None = n/a."""
        f = self.features
        stale = not f or self._signals_t is None or now - self._signals_t > self.SIGNALS_STALE_S
        r = self.room
        if r is not None:                   # the room's director: its energy, its groove as drive
            return {"energy": r["direction"]["energy"], "drive": r.get("groove"),
                    "punch": None if stale or not self._drive_seen else f["punch"],
                    "tempo": None if stale else self._tempo, "epoch": self.epoch}
        if stale:
            return {"energy": None, "drive": None, "punch": None, "tempo": None, "epoch": self.epoch}
        return {"energy": f["energy"], "drive": f["D"] if self._drive_seen else None,
                "punch": f["punch"] if self._drive_seen else None, "tempo": self._tempo, "epoch": self.epoch}

    def status(self, now: float) -> dict:
        r = self.room
        return {"enabled": self.enabled, "online": self.m > 0.0, "m": self.m, "band": self.band,
                "proposal": self.proposal, "features": self.features, "settling": self.settling(now),
                "last_change": self.last_change, "source": self.source,
                "room": None if r is None else {"vibe": r.get("vibe"), "section": r.get("section"),
                                                "groove": r.get("groove"), "age_s": round(now - r["t"], 1)}}
