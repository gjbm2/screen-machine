"""Harmony: the sounding notes (or the audio's chroma) -> music.harmony at 10 Hz (docs/SHOW_ENGINE.md §3.5, §11.1).

Owned by the receive thread ("show-rx"): ``__main__``'s on_tick calls :meth:`Harmony.tick` with the present piano
(``live`` preferred over ``test``) and, once dsp has a medium lane, the audio chroma. Nothing here locks or does I/O.

Source. While ``piano.present``: ``piano.levels(now)`` (every sounding pitch, pedal-held and ringing ones included),
``source`` "midi". Else the audio chroma (dsp.Features.chroma12()), ``source`` "audio": register 0.5 and spread 0
(no pitch heights in a chroma). Else nothing is published.

Output, contract.HarmonyData (all 0..1, rounded to 4 places):
  chroma12      C..B, max-normalised; all 0 in silence. From levels: the sum of each pitch class's amplitudes.
  key, mode     Krumhansl-Kessler (1982) profiles correlated (Pearson) with a chroma averaged over ~8 s (EMA,
                tau KEY_TAU_S): the best of 24 keys. Hysteresis: the first key is taken once one leads the
                runner-up by KEY_MARGIN (0.05 in r) for KEY_FIRST_S (1 s) with r > KEY_MIN_R (a lone note, equally
                major and minor, names none); after that a key changes only when another beats it by KEY_MARGIN
                for KEY_HOLD_S (2 s) without a break. Silence keeps the last key (the average only shrinks, its
                shape stays).
  conf          the current key's r (clamped 0..1) x clamp((r - the best other key's r) / 0.1): 0 when another
                key fits better (a change pending).
  tension       0.6 x roughness + 0.4 x (1 - key fit). Roughness: the chroma-weighted mean dissonance of every pair
                of sounding pitch classes (by interval class: m2/M7 1.0, M2/m7 0.6, m3/M6 0.15, M3/m6 0.1, P4/P5 0,
                tritone 0.8), x min(1, 2 x the pairs' share of the weight) so a faint ringing neighbour counts for
                little (two equal notes and more count in full; a lone pitch class is 0). Key fit: the share of
                chroma inside the key's scale (major; minor = natural + raised 6th/7th), 1 with no key yet.
  chord_change  1 - cos(chroma now, chroma ~0.3 s ago); 1 when sound starts from silence, 0 into silence. Rises at
                once, decays with tau 0.5 s.
  register      the level-weighted mean pitch, (p - 21) / 87 (A0 0 .. C8 1); 0.5 when silent.
  spread        the level-weighted pitch standard deviation / 24 semitones, clamped; 0 when silent.
  t_ms          now (host CLOCK_MONOTONIC ms): the levels are evaluated at the sound time ``now``.
"""

from __future__ import annotations

import math
from collections import deque
from typing import TYPE_CHECKING, Optional

import numpy as np

from .contract import KEY_NAMES, MUSIC_HARMONY, HarmonyData, Publish

if TYPE_CHECKING:                                   # midi.Piano: duck-typed (present, levels), never imported
    from .midi import Piano

# Krumhansl & Kessler (1982) probe-tone profiles, tonic first.
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
MINOR_SCALE = (0, 2, 3, 5, 7, 8, 9, 10, 11)        # natural minor + raised 6th and 7th (melodic / harmonic)
# dissonance by interval class 0..6 (unison, m2, M2, m3, M3, P4, tritone)
IC_DISSONANCE = (0.0, 1.0, 0.6, 0.15, 0.1, 0.0, 0.8)

KEY_TAU_S = 8.0
KEY_MIN_R = 0.3
KEY_MARGIN = 0.05
KEY_FIRST_S = 1.0
KEY_HOLD_S = 2.0
CONF_MARGIN = 0.1
CHANGE_LAG_S = 0.3
CHANGE_TAU_S = 0.5
SILENT = 1e-6
TENSION_ROUGH, TENSION_KEY = 0.6, 0.4


def _zrows(profile: np.ndarray) -> np.ndarray:
    rows = np.stack([np.roll(profile, k) for k in range(12)])
    rows = rows - rows.mean(axis=1, keepdims=True)
    return rows / (rows.std(axis=1, keepdims=True) * 12.0)


_Z = np.concatenate([_zrows(MAJOR_PROFILE), _zrows(MINOR_PROFILE)])     # (24, 12): r = _Z @ zscore(chroma)
_SCALES = np.zeros((24, 12), bool)
for _k in range(12):
    _SCALES[_k, [(_k + i) % 12 for i in MAJOR_SCALE]] = True
    _SCALES[12 + _k, [(_k + i) % 12 for i in MINOR_SCALE]] = True
_SCALEF = _SCALES.astype(np.float64)
_IC = np.array([[min((i - j) % 12, (j - i) % 12) for j in range(12)] for i in range(12)])
_DISS = np.asarray(IC_DISSONANCE)[_IC]                                  # (12, 12), diagonal 0
_PITCH = np.arange(128, dtype=np.float64)
_PITCH2 = _PITCH * _PITCH
_PC = np.zeros((128, 12))                                               # pitch -> pitch class, one-hot
_PC[np.arange(128), np.arange(128) % 12] = 1.0
# (12-element numpy calls cost ~1-2 us each on show-rx: the helpers keep their count low)


def _clamp(x: float) -> float:
    return 0.0 if not x > 0.0 else (1.0 if x > 1.0 else float(x))


def chroma_from_levels(levels: np.ndarray) -> np.ndarray:
    """(128,) amplitudes -> (12,) chroma, C = 0 (MIDI 60 % 12), max-normalised; all 0 if silent."""
    lv = np.asarray(levels, np.float64)
    c = np.maximum(lv, 0.0) @ _PC[:lv.shape[0]]
    m = c.max()
    return c / m if m > SILENT else np.zeros(12)


def key_scores(chroma: np.ndarray) -> Optional[np.ndarray]:
    """Pearson r of ``chroma`` with the 24 Krumhansl-Kessler key profiles (0-11 major C..B, 12-23 minor C..B);
    None for a flat chroma (silence, or every pitch class alike)."""
    d = np.asarray(chroma, np.float64)
    d = d - d.sum() / 12.0
    var = float(d @ d) / 12.0
    if not var > SILENT * SILENT:
        return None
    return (_Z @ d) / math.sqrt(var)


def _runner_up(scores: np.ndarray, k: int) -> float:
    """The best score among the keys other than ``k``."""
    top = float(scores.max())
    return top if scores[k] < top else float(np.partition(scores, -2)[-2])


def _conf(scores: np.ndarray, k: int) -> float:
    r = float(scores[k])
    return _clamp(r) * _clamp((r - _runner_up(scores, k)) / CONF_MARGIN)


def key_name(k: int) -> tuple[str, str]:
    return KEY_NAMES[k % 12], ("major" if k < 12 else "minor")


def key_estimate(chroma: np.ndarray) -> tuple[Optional[str], Optional[str], float]:
    """(key, mode, conf 0..1) by Krumhansl-Kessler on one chroma, no history; (None, None, 0.0) if it is flat."""
    s = key_scores(chroma)
    if s is None:
        return None, None, 0.0
    k = int(np.argmax(s))
    key, mode = key_name(k)
    return key, mode, _conf(s, k)


def roughness(chroma: np.ndarray) -> float:
    """0..1: the chroma-weighted mean dissonance over every pair of sounding pitch classes, scaled down when the
    pairs carry little of the weight (one loud note and a faint one); 0 for one pitch class or none."""
    w = np.maximum(np.asarray(chroma, np.float64), 0.0)
    total = float(w.sum())
    pairs = total * total - float(w @ w)
    if pairs <= SILENT:
        return 0.0
    return _clamp(float((w @ _DISS) @ w) / pairs * min(1.0, 2.0 * pairs / (total * total)))


def key_fit(chroma: np.ndarray, k: Optional[int]) -> float:
    """The share of the chroma inside key ``k``'s scale; 1.0 with no key (or no sound)."""
    c = np.maximum(np.asarray(chroma, np.float64), 0.0)
    total = float(c.sum())
    if k is None or total <= SILENT:
        return 1.0
    return float(_SCALEF[k] @ c) / total


class Harmony:
    """music.harmony at ``rate_hz`` (module docstring). Owned by show-rx."""

    def __init__(self, publish: Publish, *, rate_hz: float = 10.0) -> None:
        self._publish = publish
        self.period_s = 1.0 / float(rate_hz)
        self._next: Optional[float] = None
        self._avg = np.zeros(12)
        self._t_last: Optional[float] = None
        self._key: Optional[int] = None
        self._cand: Optional[int] = None
        self._cand_since = 0.0
        self._change = 0.0
        self._hist: deque[tuple[float, np.ndarray]] = deque(maxlen=64)
        self.latest: Optional[HarmonyData] = None
        self.published = 0

    def tick(self, now_s: float, piano: "Piano | None" = None, audio_chroma: np.ndarray | None = None) -> None:
        """Publish music.harmony when due: from ``piano.levels(now_s)`` while ``piano.present``, else from
        ``audio_chroma``, else nothing."""
        if self._next is not None and now_s < self._next - 1e-6:
            return
        self._next = now_s + self.period_s if self._next is None or now_s - self._next > self.period_s \
            else self._next + self.period_s
        if piano is not None and piano.present:
            levels = piano.levels(now_s)
            data = self.compute(chroma_from_levels(levels), levels, now_s, "midi")
        elif audio_chroma is not None:
            c = np.maximum(np.asarray(audio_chroma, np.float64).reshape(12), 0.0)
            m = c.max()
            data = self.compute(c / m if m > SILENT else np.zeros(12), None, now_s, "audio")
        else:
            return
        self.published += 1
        self._publish({"type": MUSIC_HARMONY, "data": data})

    def compute(self, chroma12: np.ndarray, levels: np.ndarray | None, t_s: float, source: str) -> HarmonyData:
        """One harmony frame at ``t_s`` (advances the key average, the hysteresis and chord_change)."""
        c = np.minimum(np.maximum(np.asarray(chroma12, np.float64).reshape(12), 0.0), 1.0)
        dt = 0.0 if self._t_last is None else max(0.0, t_s - self._t_last)
        a = 1.0 if self._t_last is None else 1.0 - math.exp(-dt / KEY_TAU_S)
        self._avg += (c - self._avg) * a
        self._t_last = t_s
        conf = self._update_key(t_s)

        past = None
        for t_h, c_h in reversed(self._hist):            # the newest frame at least CHANGE_LAG_S old
            if t_h <= t_s - CHANGE_LAG_S + 1e-6:
                past = c_h
                break
        if past is None and self._hist:
            past = self._hist[0][1]
        raw = 0.0 if past is None else _change(c, past)
        self._change = max(self._change * math.exp(-dt / CHANGE_TAU_S), raw)
        self._hist.append((t_s, c))

        register, spread = 0.5, 0.0
        if levels is not None:
            w = np.maximum(np.asarray(levels, np.float64)[:128], 0.0)
            total = float(w.sum())
            if total > SILENT:
                n = w.shape[0]
                mean = float(w @ _PITCH[:n]) / total
                register = _clamp((mean - 21.0) / 87.0)
                spread = _clamp(math.sqrt(max(0.0, float(w @ _PITCH2[:n]) / total - mean * mean)) / 24.0)
        tension = _clamp(TENSION_ROUGH * roughness(c) + TENSION_KEY * (1.0 - key_fit(c, self._key)))
        key, mode = key_name(self._key) if self._key is not None else (None, None)
        data: HarmonyData = {"chroma12": np.round(c, 4).tolist(), "key": key, "mode": mode,
                             "tension": round(tension, 4), "chord_change": round(_clamp(self._change), 4),
                             "register": round(register, 4), "spread": round(spread, 4),
                             "t_ms": round(t_s * 1000.0, 3), "source": "midi" if source == "midi" else "audio",
                             "conf": round(conf, 4)}
        self.latest = data
        return data

    def _update_key(self, t_s: float) -> float:
        s = key_scores(self._avg)
        if s is None:
            self._cand = None
            return 0.0
        best = int(np.argmax(s))
        if self._key is None:
            if s[best] > KEY_MIN_R and s[best] - _runner_up(s, best) >= KEY_MARGIN:
                if self._cand != best:
                    self._cand, self._cand_since = best, t_s
                elif t_s - self._cand_since >= KEY_FIRST_S:
                    self._key, self._cand = best, None
            else:
                self._cand = None
        elif best != self._key and s[best] - s[self._key] >= KEY_MARGIN:
            if self._cand != best:
                self._cand, self._cand_since = best, t_s
            elif t_s - self._cand_since >= KEY_HOLD_S:
                self._key, self._cand = best, None
        else:
            self._cand = None
        return 0.0 if self._key is None else _conf(s, self._key)

    @property
    def key(self) -> tuple[Optional[str], Optional[str]]:
        return key_name(self._key) if self._key is not None else (None, None)


def _change(now: np.ndarray, past: np.ndarray) -> float:
    a, b = math.sqrt(float(now @ now)), math.sqrt(float(past @ past))
    if a <= SILENT:
        return 0.0
    if b <= SILENT:
        return 1.0
    return _clamp(1.0 - float(now @ past) / (a * b))
