"""The piano's MIDI in the art director's choice (owner, 24 Oct 2026: "the piano's MIDI steers the screens").

piano.py's live signals (Session.piano, from the notes; never the room harmony's stand-ins) shape the music's
proposal (music_direction.py) on top of the room's audio reading:

  * strength: the piano's energy (a weighted sum of note_rate and velocity) and pace input (likewise) blend with
    the audio's: ``blend(a, p) = max(a, p) + both_boost x min(a, p) x (1 - max(a, p))``, then the piano's share is
    scaled by ``weight`` (0: the audio alone).  The stronger reading leads, so the mic hearing singing over quiet
    playing keeps the singing's energy, and a quiet room while the MIDI goes to headphones lets the piano drive.
    No audio reading at all: the piano's alone.  No piano (n/a after piano.SILENT_S of silence): the audio's,
    exactly as before.  change_rate, fx.density and fx.rate follow from the blended energy by the audio path's
    own formulas, so every field moves together.
  * leans: each ``leans`` row turns the eased signals into a strength 0..1 (the product of ``when`` ramps and of
    1 - each ``unless`` ramp; [lo, hi] -> 0..1).  The strongest row at or above ``lean_on`` leads (it holds until
    it falls under ``lean_off``) and puts its ``moods`` / ``fx_moods`` first in the proposal's words, keeping
    ``keep_moods`` of the band's.  Every row's strength goes out as ``direction.piano`` {row: s}, which
    fx_interplay.yaml's ``roles_by_piano`` turns into role multipliers (legato -> trails, staccato -> camera / light).
  * density: ``spread`` ([lo, hi] -> ``to`` [lo, hi]) proposes the direction's density axis; with the audio's own
    density it is ``(1 - weight) audio + weight piano``.

Words are only those in config/live/vocab.yaml's moods (a word outside it is dropped and listed in ``errors``).
All numbers are in config/live/piano_direction.yaml, re-read when it changes (looked at at most once a second).
Pure arithmetic and one file read, no I/O on the hot path: music_direction.py calls it from the 10 Hz tick.
"""
from __future__ import annotations

import logging
import math
import pathlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

log = logging.getLogger("stream_service.piano_direction")

REPO = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO / "config" / "live" / "piano_direction.yaml"
VOCAB_PATH = REPO / "config" / "live" / "vocab.yaml"
CHECK_S = 1.0
SIGNALS = ("note_rate", "velocity", "register", "spread", "pedal", "tension")
EASED = ("pedal", "spread")          # instantaneous signals eased over smooth_s before they lean anything
# Used only when the file leaves a key out (or there is no file: then the piano is off, as before 24 Oct).
FALLBACK: dict = {"on": False, "weight": 1.0, "both_boost": 0.0, "smooth_s": 4.0,
                  "energy": {"note_rate": 0.6, "velocity": 0.4}, "pace": {"note_rate": 0.8, "velocity": 0.2},
                  "lean_on": 0.5, "lean_off": 0.3, "keep_moods": 1, "leans": {}, "density": None}


def _c01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _pair(v: Any) -> Optional[tuple[float, float]]:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        a, b = _num(v[0]), _num(v[1])
        if a is not None and b is not None:
            return a, b
    return None


def ramp(x: float, lo_hi: tuple[float, float]) -> float:
    """[lo, hi] -> 0..1 (hi <= lo: a step at lo)."""
    lo, hi = lo_hi
    if hi <= lo:
        return 1.0 if x >= lo else 0.0
    return _c01((x - lo) / (hi - lo))


def blend(audio: Optional[float], piano: Optional[float], both_boost: float = 0.0, weight: float = 1.0
          ) -> Optional[float]:
    """The stronger reading, lifted a little when both are busy; ``weight`` scales the piano's share.
    Either side None: the other (both None: None)."""
    if piano is None:
        return audio
    if audio is None:
        return _c01(piano)
    hi, lo = max(audio, piano), min(audio, piano)
    both = _c01(hi + max(0.0, both_boost) * lo * (1.0 - hi))
    return _c01(audio + _c01(weight) * (both - audio))


def numbers(e: float, pace_in: float) -> dict:
    """The proposal's numbers from one energy and pace input (music_direction's own formulas, in one place)."""
    return {"energy": round(_c01(e), 3), "pace": round(0.1 + 0.8 * _c01(pace_in), 3), "change_rate": round(0.9 * _c01(e), 3),
            "fx": {"density": round(0.1 + 0.8 * _c01(e), 3), "rate": round(0.05 + 0.85 * _c01(e), 3)}}


# -- the config file ----------------------------------------------------------------------------------------------------
def _vocab_moods(path: pathlib.Path) -> Optional[set]:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
        m = raw.get("moods") if isinstance(raw, dict) else None
        return {str(w) for w in m} if isinstance(m, list) else None
    except (OSError, yaml.YAMLError):
        return None


def parse(raw: Any, vocab: Optional[set]) -> tuple[dict, list[str]]:
    """The file's mapping -> (config, errors).  A bad value keeps its fallback; a word outside the vocab is dropped."""
    errs: list[str] = []
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return dict(FALLBACK), ["the top level must be a mapping"]
    cfg = dict(FALLBACK)
    cfg["on"] = bool(raw.get("on", True))
    for k in ("weight", "both_boost", "smooth_s", "lean_on", "lean_off"):
        if k in raw:
            v = _num(raw[k])
            if v is None or v < 0:
                errs.append(f"{k} = {raw[k]!r} is not usable: keeping {FALLBACK[k]}")
            else:
                cfg[k] = v
    if "keep_moods" in raw:
        v = _num(raw["keep_moods"])
        cfg["keep_moods"] = int(v) if v is not None and v >= 0 else FALLBACK["keep_moods"]
    for k in ("energy", "pace"):
        if k in raw:
            w = raw[k] if isinstance(raw[k], dict) else {}
            got = {s: _num(x) for s, x in w.items() if s in SIGNALS and _num(x) is not None}
            if not got or len(got) != len(w):
                errs.append(f"{k}: weights of {', '.join(SIGNALS)} only")
            if got:
                cfg[k] = got

    def words(v: Any, where: str) -> list[str]:
        ws = [str(x) for x in (v if isinstance(v, list) else [v] if isinstance(v, str) else [])]
        bad = [w for w in ws if vocab is not None and w not in vocab]
        if bad:
            errs.append(f"{where}: {bad} are not vocab.yaml moods (dropped)")
        return [w for w in ws if w not in bad]

    leans = {}
    for name, row in (raw.get("leans") or {}).items() if isinstance(raw.get("leans"), dict) else ():
        if not isinstance(row, dict):
            errs.append(f"leans.{name} must be a mapping")
            continue
        when = {s: p for s, p in ((s, _pair(v)) for s, v in (row.get("when") or {}).items()) if s in SIGNALS and p}
        unless = {s: p for s, p in ((s, _pair(v)) for s, v in (row.get("unless") or {}).items()) if s in SIGNALS and p}
        if not when:
            errs.append(f"leans.{name}: needs when: {{signal: [lo, hi]}}")
            continue
        leans[str(name)] = {"when": when, "unless": unless, "moods": words(row.get("moods"), f"leans.{name}.moods"),
                            "fx_moods": words(row.get("fx_moods"), f"leans.{name}.fx_moods")}
    cfg["leans"] = leans
    d = raw.get("density")
    if d is not None:
        d = d if isinstance(d, dict) else {}
        frm, to, w = _pair(d.get("spread")), _pair(d.get("to")), _num(d.get("weight", 0.5))
        if frm and to and w is not None:
            cfg["density"] = {"spread": frm, "to": to, "weight": _c01(w)}
        else:
            errs.append("density: needs spread: [lo, hi], to: [lo, hi], weight")
    return cfg, errs


class PianoConfig:
    """config/live/piano_direction.yaml, re-read when it or vocab.yaml changes (looked at at most once a second).
    Absent: the piano is off (the audio alone, as before).  A file that doesn't read keeps the last good copy."""

    def __init__(self, path: pathlib.Path = CONFIG_PATH, vocab: pathlib.Path = VOCAB_PATH):
        self.path, self.vocab = path, vocab
        self.cfg: dict = dict(FALLBACK)
        self.errors: list[str] = []
        self._mt: Any = -1
        self._checked = -math.inf
        self._lock = threading.Lock()

    def _mtimes(self) -> tuple:
        out = []
        for p in (self.path, self.vocab):
            try:
                out.append(p.stat().st_mtime_ns)
            except OSError:
                out.append(None)
        return tuple(out)

    def get(self) -> dict:
        now = time.monotonic()
        if now - self._checked < CHECK_S:
            return self.cfg
        with self._lock:
            if now - self._checked < CHECK_S:
                return self.cfg
            self._checked = now
            mt = self._mtimes()
            if mt != self._mt:
                self._mt = mt
                try:
                    raw = yaml.safe_load(self.path.read_text()) if mt[0] is not None else None
                    if mt[0] is None:
                        self.cfg, self.errors = dict(FALLBACK), []
                    else:
                        self.cfg, self.errors = parse(raw, _vocab_moods(self.vocab))
                    for e in self.errors:
                        log.warning("piano_direction.yaml: %s", e)
                    log.info("piano direction: %s", "read" if mt[0] is not None else "no file (off)")
                except (OSError, yaml.YAMLError) as e:
                    self.errors = [f"{e}"[:300]]
                    log.error("cannot read %s: %s (keeping the last good copy)", self.path, e)
        return self.cfg


CONFIG = PianoConfig()


# -- the reading --------------------------------------------------------------------------------------------------------
@dataclass
class Reading:
    """What the piano says this tick (None from PianoFeel.reading while it is silent or off)."""
    energy: float
    pace: float                  # the pace input 0..1 (numbers() maps it like the audio's)
    leans: dict = field(default_factory=dict)      # row -> strength 0..1
    lean: Optional[str] = None   # the row that leads the words (hysteresis), or None
    density: Optional[float] = None
    signals: dict = field(default_factory=dict)

    def status(self) -> dict:
        return {"energy": round(self.energy, 3), "pace": round(self.pace, 3), "lean": self.lean,
                "leans": {k: round(v, 2) for k, v in self.leans.items()},
                "density": None if self.density is None else round(self.density, 3),
                "signals": {k: (None if v is None else round(v, 3)) for k, v in self.signals.items()}}


class PianoFeel:
    """One session's piano reading (loop thread): ``step`` each tick with piano.py's levels, ``reading`` when the
    proposal is made."""

    def __init__(self, config: Optional[PianoConfig] = None):
        self.config = config or CONFIG
        self._t: Optional[float] = None
        self._eased: dict[str, Optional[float]] = {k: None for k in EASED}
        self._sig: dict = {}
        self._lean: Optional[str] = None
        self.present = False

    def step(self, now: float, levels: Optional[dict]) -> None:
        cfg = self.config.get()
        dt = 0.0 if self._t is None else max(0.0, min(2.0, now - self._t))
        self._t = now
        lv = levels or {}
        self.present = bool(cfg.get("on")) and _num(lv.get("note_rate")) is not None
        if not self.present:
            self._eased = {k: None for k in EASED}
            self._sig, self._lean = {}, None
            return
        tau = max(0.1, float(cfg.get("smooth_s", 4.0)))
        a = 1.0 - math.exp(-dt / tau) if dt > 0 else 0.0
        for k in EASED:
            v = _num(lv.get(k))
            p = self._eased[k]
            self._eased[k] = None if v is None else v if p is None else p + (v - p) * a
        self._sig = {k: (self._eased[k] if k in EASED else _num(lv.get(k))) for k in SIGNALS}

    def reading(self) -> Optional[Reading]:
        if not self.present:
            return None
        cfg = self.config.get()
        s = self._sig

        def wsum(w: dict) -> float:
            return _c01(sum(float(x) * (s.get(k) or 0.0) for k, x in w.items()))

        leans = {}
        for name, row in (cfg.get("leans") or {}).items():
            v = 1.0
            for k, lh in row["when"].items():
                v *= ramp(s.get(k) or 0.0, lh)
            for k, lh in row["unless"].items():
                v *= 1.0 - ramp(s.get(k) or 0.0, lh)
            leans[name] = round(v, 3)
        on, off = float(cfg.get("lean_on", 0.5)), float(cfg.get("lean_off", 0.3))
        if self._lean is not None and leans.get(self._lean, 0.0) < off:
            self._lean = None
        best = max(leans, key=leans.get) if leans else None
        if best is not None and leans[best] >= on and (self._lean is None or leans[best] > leans.get(self._lean, 0.0) + 0.1):
            self._lean = best
        den = None
        d = cfg.get("density")
        if d and s.get("spread") is not None:
            lo, hi = d["to"]
            den = _c01(lo + (hi - lo) * ramp(s["spread"], d["spread"]))
        return Reading(energy=wsum(cfg.get("energy") or {}), pace=wsum(cfg.get("pace") or {}), leans=leans,
                       lean=self._lean, density=den, signals=dict(s))


def lean_words(band: list, lean: list, keep: int) -> list:
    """The lean's words first, then ``keep`` of the band's (no repeats)."""
    out = list(dict.fromkeys(lean))
    out += [w for w in band if w not in out][:max(0, keep)]
    return out


def apply(target: dict, r: Optional[Reading], cfg: Optional[dict] = None, audio: bool = True) -> dict:
    """The piano's layer over a proposal ``target`` (music_direction's shape: energy, pace, change_rate, fx {density,
    rate, moods}, moods, density): numbers blended, words leaned, density proposed, ``piano`` {row: s} added.
    ``audio`` False: ``target``'s numbers are ignored (no audio reading).  ``r`` None: ``target`` unchanged."""
    if r is None:
        return target
    cfg = cfg if cfg is not None else CONFIG.get()
    w, boost = float(cfg.get("weight", 1.0)), float(cfg.get("both_boost", 0.0))
    p = numbers(r.energy, r.pace)
    out = dict(target, fx=dict(target.get("fx") or {}))
    for k in ("energy", "pace", "change_rate"):
        out[k] = round(blend(target.get(k) if audio else None, p[k], boost, w), 3)
    for k in ("density", "rate"):
        out["fx"][k] = round(blend(out["fx"].get(k) if audio else None, p["fx"][k], boost, w), 3)
    if r.density is not None:
        d = cfg.get("density") or {}
        have = _num(target.get("density")) if audio else None
        out["density"] = round(r.density if have is None else (1 - d.get("weight", 0.5)) * have
                               + d.get("weight", 0.5) * r.density, 3)
    out["piano"] = dict(r.leans)
    return out


def lean(target: dict, r: Optional[Reading], cfg: Optional[dict] = None) -> dict:
    """The leading lean's words over the proposal's (in place; after the band chose them)."""
    if r is None or r.lean is None:
        return target
    cfg = cfg if cfg is not None else CONFIG.get()
    row = (cfg.get("leans") or {}).get(r.lean)
    if not row:
        return target
    keep = int(cfg.get("keep_moods", 1))
    if row["moods"]:
        target["moods"] = lean_words(list(target.get("moods") or []), row["moods"], keep)
    if row["fx_moods"]:
        target.setdefault("fx", {})["moods"] = lean_words(list((target.get("fx") or {}).get("moods") or []),
                                                          row["fx_moods"], keep)
    return target
