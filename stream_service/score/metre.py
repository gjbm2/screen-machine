"""Metre from a performance: beats per bar (2, 3, 4), the bar lines' phase, and simple vs compound beats.

Numpy only (the analysis worker runs it next to PM2S, tests run it anywhere).  PM2S finds the beats well, but its
downbeats and its duple/triple head are weak on their own (half the windows wrong on our test pieces; jazz swing
reads as triple).  Here every beat gets salience cues, and each candidate bar (length L, first downbeat phase p) is
scored by how much its downbeats stand out:

  down   PM2S's downbeat probability at the beat's notes
  bass   how low the beat's lowest new note is;  bnew  the bass changed since the last beat
  harm   harmonic change: the chroma of what sounds over this beat against the last beat's
  vel / cnt / dur   the strike weight, the number of notes, and how long the beat's notes are held
  rep    (per L) bars repeat: rhythm-and-pitch patterns one bar apart are more alike than at other lags

Each cue's contrast (mean at the candidate's downbeats minus elsewhere) and the repetition feature go into a linear
score per candidate, with a prior per L and PM2S's triple vote; a softmax over the nine candidates places the bar
lines (and splits duple between 2 and 4 beats).  The metre's class (duple or triple, simple or compound) comes
from a small network (one hidden layer, numpy here) on window features: each cue's autocorrelation at 1-8 beats,
pattern repetition, the best contrast per bar length, where off-beat onsets sit in the beat (thirds against halves
and quarters: jazz swing stays simple), tempo and its steadiness, PM2S's triple vote and its downbeat spacing.
Without the network (old weights file) a logistic on the off-beat positions decides compound.  Fitted on ASAP
(classical; time signatures and downbeats annotated per performance) and PiJAMA (jazz, weakly labelled 4/4 swing):
tools/score/fit_metre.py writes metre_weights.json beside this file.  Cross-validated by piece, one 24 s window:
signature right 77% (classical) / 81% (jazz); three windows of a performance combined: 80% / 90%, duple or
triple 91% / 96%.
"""
from __future__ import annotations

import json
import math
import pathlib
from dataclasses import dataclass
from typing import Optional

import numpy as np

CANDIDATES = [(L, p) for L in (2, 3, 4) for p in range(L)]
CLASSES = ("Dc", "Ds", "Tc", "Ts")             # duple / triple x compound / simple
CUES = ("down", "bass", "bnew", "harm", "vel", "cnt", "dur")
SUB_BINS = 12
WEIGHTS_PATH = pathlib.Path(__file__).with_name("metre_weights.json")


@dataclass
class Metre:
    beats_per_bar: int                 # 2, 3 or 4
    phase: int                         # index (in the beats given) of the first downbeat
    compound: bool                     # dotted beats: 6/8, 9/8, 12/8
    confidence: float                  # the chosen candidate's softmax probability
    compound_p: float
    probs: dict                        # {L: probability summed over phases}
    phases: dict                       # {L: that length's best phase}

    @property
    def signature(self) -> tuple[int, int]:
        return (self.beats_per_bar * 3, 8) if self.compound else (self.beats_per_bar, 4)


def _near(onsets: np.ndarray, beats: np.ndarray) -> tuple[np.ndarray, float]:
    """Each onset's nearest beat index (-1 when it isn't within tolerance of one) and the tolerance."""
    ibi = float(np.median(np.diff(beats))) if len(beats) > 1 else 0.5
    tol = min(0.07, 0.2 * ibi)
    k = np.clip(np.searchsorted(beats, onsets), 1, len(beats) - 1)
    left = np.where(np.abs(onsets - beats[k - 1]) <= np.abs(onsets - beats[k]), k - 1, k)
    ok = np.abs(onsets - beats[left]) <= tol
    return np.where(ok, left, -1), tol


def saliences(notes: np.ndarray, beats: np.ndarray, down: Optional[np.ndarray] = None) -> dict:
    """Per-beat cues (z-scored over the window) and per-beat rhythm-and-pitch pattern vectors."""
    m = len(beats)
    pitch, on, dur, vel = notes[:, 0], notes[:, 1], notes[:, 2], notes[:, 3]
    idx, _tol = _near(on, beats)
    s = {c: np.zeros(m) for c in CUES}
    bass = np.full(m, np.nan)
    for i in range(m):
        sel = idx == i
        if not sel.any():
            continue
        if down is not None:
            s["down"][i] = float(down[sel].max())
        s["vel"][i] = float(vel[sel].sum()) / 127.0
        s["cnt"][i] = float(sel.sum())
        s["dur"][i] = float(np.log1p(dur[sel].max() * 4.0))
        bass[i] = float(pitch[sel].min())
    have = ~np.isnan(bass)
    s["bass"][have] = np.clip((72.0 - bass[have]) / 36.0, 0.0, 1.5)
    prev = np.concatenate([[np.nan], bass[:-1]])
    s["bnew"] = (have & (np.isnan(prev) | (bass != prev))).astype(float)
    # chroma sounding over each beat span, and the onset pattern inside it
    edges = np.concatenate([beats, [beats[-1] + (beats[-1] - beats[-2] if m > 1 else 0.5)]])
    chroma = np.zeros((m, 12))
    pattern = np.zeros((m, SUB_BINS + 12))
    off = on + dur
    pc = pitch.astype(int) % 12
    for i in range(m):
        lo, hi = edges[i], edges[i + 1]
        ov = np.clip(np.minimum(off, hi) - np.maximum(on, lo), 0.0, None)
        if ov.any():
            np.add.at(chroma[i], pc, ov)
        inside = (on >= lo - 0.03) & (on < hi - 0.03)
        if inside.any():
            ph = np.clip(((on[inside] - lo) / max(1e-3, hi - lo) * SUB_BINS).round().astype(int), 0, SUB_BINS - 1)
            np.add.at(pattern[i], ph, vel[inside] / 127.0)
            np.add.at(pattern[i], SUB_BINS + pc[inside], 0.5)
    nrm = np.linalg.norm(chroma, axis=1, keepdims=True)
    cn = np.divide(chroma, nrm, out=np.zeros_like(chroma), where=nrm > 0)
    s["harm"][1:] = 1.0 - (cn[1:] * cn[:-1]).sum(1)
    for c in CUES:
        v = s[c]
        sd = v.std()
        s[c] = (v - v.mean()) / sd if sd > 1e-9 else np.zeros(m)
    pn = np.linalg.norm(pattern, axis=1, keepdims=True)
    s["pattern"] = np.divide(pattern, pn, out=np.zeros_like(pattern), where=pn > 0)
    return s


def _rep(pattern: np.ndarray, lag: int) -> float:
    if len(pattern) <= lag:
        return 0.0
    return float((pattern[lag:] * pattern[:-lag]).sum(1).mean())


def candidate_features(s: dict, ts_triple: float) -> np.ndarray:
    """(9, F): per candidate (L, phase), each cue's downbeat contrast, the bar-repetition gain at L, L one-hot
    (the prior) and L one-hot x PM2S's triple vote."""
    m = len(s["down"])
    reps = {L: _rep(s["pattern"], L) for L in (2, 3, 4, 6, 8)}
    rmean = (reps[2] + reps[3] + reps[4]) / 3.0
    rows = []
    for L, p in CANDIDATES:
        on_bar = (np.arange(m) - p) % L == 0
        f = []
        for c in CUES:
            v = s[c]
            f.append(float(v[on_bar].mean() - v[~on_bar].mean()) if on_bar.any() and (~on_bar).any() else 0.0)
        # the bar's half-way beat (L=4: beat 3) is stronger than its neighbours: the 4-vs-2 cue
        if L == 4:
            half = (np.arange(m) - p) % 4 == 2
            weak = (np.arange(m) - p) % 2 == 1
            f.append(float(sum(s[c][half].mean() - s[c][weak].mean() for c in ("bass", "harm", "down")) / 3.0)
                     if half.any() and weak.any() else 0.0)
        else:
            f.append(0.0)
        f.append(reps[L] - rmean)
        f.append(reps[2 * L if 2 * L in reps else L] - rmean)
        f += [1.0 if L == k else 0.0 for k in (2, 3, 4)]
        f += [ts_triple - 0.5 if L == k else 0.0 for k in (2, 3, 4)]
        rows.append(f)
    return np.array(rows)


def _ac(v: np.ndarray, lag: int) -> float:
    if len(v) <= lag + 2:
        return 0.0
    a, b = v[:-lag], v[lag:]
    sa, sb = a.std(), b.std()
    return float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb)) if sa > 1e-9 and sb > 1e-9 else 0.0


def window_features(notes: np.ndarray, beats: np.ndarray, s: dict, X: np.ndarray, ts: Optional[np.ndarray],
                    downs: Optional[np.ndarray]) -> np.ndarray:
    """The network's input (see the module docstring)."""
    triple = float(np.mean(ts > 0.5)) if ts is not None and len(ts) else 0.5
    f: list[float] = []
    for c in CUES:
        f += [_ac(s[c], L) for L in range(1, 9)]
    f += [_rep(s["pattern"], L) for L in (1, 2, 3, 4, 6, 8)]
    for L in (2, 3, 4):
        rows = [i for i, (LL, _p) in enumerate(CANDIDATES) if LL == L]
        f += list(X[rows, :len(CUES) + 1].max(0))
    f += list(subdivision_features(notes, beats, triple)[:7])
    ibi = np.diff(beats)
    f += [triple, float(np.mean(ts)) if ts is not None and len(ts) else 0.5, 60.0 / float(np.median(ibi)),
          float(ibi.std() / ibi.mean())]
    if downs is not None and len(downs) > 1:
        idx = sorted({int(np.argmin(np.abs(beats - d))) for d in downs})
        gaps = np.diff(idx) if len(idx) > 1 else np.array([0])
    else:
        gaps = np.array([0])
    f += [float(np.mean(gaps == g)) for g in (2, 3, 4, 6, 8)]
    f.append((len(downs) if downs is not None else 0) / len(beats))
    return np.array(f, dtype=float)


def mlp_proba(net: dict, x: np.ndarray) -> np.ndarray:
    """StandardScaler -> ReLU hidden layers -> softmax, from the fitted weights (sklearn's MLPClassifier layout)."""
    h = (x - np.asarray(net["mean"])) / np.asarray(net["scale"])
    layers = list(zip(net["coefs"], net["intercepts"]))
    for k, (W, b) in enumerate(layers):
        h = h @ np.asarray(W) + np.asarray(b)
        if k < len(layers) - 1:
            h = np.maximum(h, 0.0)
    if h.shape[-1] == 1:                       # binary: sklearn's logistic output
        p1 = 1.0 / (1.0 + np.exp(-h[..., 0]))
        return np.stack([1 - p1, p1], -1)
    h = np.exp(h - h.max(-1, keepdims=True))
    return h / h.sum(-1, keepdims=True)


def subdivision_features(notes: np.ndarray, beats: np.ndarray, ts_triple: float) -> np.ndarray:
    """Where onsets fall inside their beat: fractions near 1/3, 2/3, 1/2, 1/4 and 3/4, the thirds' share of the
    off-beat onsets, and PM2S's triple vote."""
    on = notes[:, 1]
    k = np.searchsorted(beats, on) - 1
    ok = (k >= 0) & (k < len(beats) - 1)
    if ok.sum() < 8:
        return np.array([0, 0, 0, 0, 0, 0, ts_triple - 0.5, 1.0])
    ph = (on[ok] - beats[k[ok]]) / (beats[k[ok] + 1] - beats[k[ok]])
    def near(c, w=0.05):
        return float(np.mean(np.abs(ph - c) < w))
    t1, t2, h, q1, q3 = near(1 / 3), near(2 / 3), near(0.5), near(0.25), near(0.75)
    offb = t1 + t2 + h + q1 + q3
    return np.array([t1, t2, h, q1 + q3, (t1 + t2) / offb if offb > 0 else 0.0, t1 / (t2 + 1e-3) if t2 > 0 else 0.0,
                     ts_triple - 0.5, 1.0])


class MetreModel:
    def __init__(self, weights: Optional[dict] = None) -> None:
        if weights is None:
            try:
                weights = json.loads(WEIGHTS_PATH.read_text())
            except (OSError, ValueError):
                weights = {}
        self.w = np.array(weights.get("bar", []), dtype=float)
        self.ws = np.array(weights.get("sub", []), dtype=float)
        self.net = weights.get("mlp")

    def __call__(self, notes: np.ndarray, beats: np.ndarray, down: Optional[np.ndarray] = None,
                 ts: Optional[np.ndarray] = None, downs: Optional[np.ndarray] = None) -> Optional[Metre]:
        if len(beats) < 6 or len(notes) < 8:
            return None
        triple = float(np.mean(ts > 0.5)) if ts is not None and len(ts) else 0.5
        s = saliences(notes, beats, down)
        X = candidate_features(s, triple)
        w = self.w if len(self.w) == X.shape[1] else _default_bar_weights(X.shape[1])
        z = X @ w
        z = np.exp(z - z.max())
        pr = z / z.sum()
        probs = {k: float(sum(pr[i] for i, (LL, _) in enumerate(CANDIDATES) if LL == k)) for k in (2, 3, 4)}
        phases = {k: max(((pr[i], pp) for i, (LL, pp) in enumerate(CANDIDATES) if LL == k))[1] for k in (2, 3, 4)}
        if self.net:                               # the class from the network; 2 vs 4 and the phase from above
            cls = dict(zip(self.net["classes"], mlp_proba(self.net, window_features(notes, beats, s, X, ts, downs))))
            pt = float(cls.get("Tc", 0.0) + cls.get("Ts", 0.0))
            cp = float(cls.get("Dc", 0.0) + cls.get("Tc", 0.0))
            d24 = probs[2] + probs[4]
            probs = {2: (1 - pt) * (probs[2] / d24 if d24 > 0 else 0.5), 3: pt,
                     4: (1 - pt) * (probs[4] / d24 if d24 > 0 else 0.5)}
        else:
            xs = subdivision_features(notes, beats, triple)
            ws = self.ws if len(self.ws) == len(xs) else np.array([0, 0, 0, 0, 6.0, 0, 2.0, -3.0])
            cp = 1.0 / (1.0 + math.exp(-float(xs @ ws)))
        L = max(probs, key=probs.get)
        return Metre(L, phases[L], cp > 0.5, float(probs[L]), cp, probs, phases)


def _default_bar_weights(n: int) -> np.ndarray:
    """Hand weights for when metre_weights.json is missing: the cues' contrasts, repetition, a 4/4 prior."""
    w = np.zeros(n)
    w[:len(CUES)] = (1.0, 1.0, 0.6, 1.2, 0.5, 0.4, 0.6)
    w[len(CUES)] = 0.8
    w[len(CUES) + 1] = 4.0
    w[len(CUES) + 2] = 2.0
    w[len(CUES) + 3:len(CUES) + 6] = (0.0, -0.3, 0.3)
    w[len(CUES) + 6:len(CUES) + 9] = (-1.0, 2.0, -1.0)
    return w
