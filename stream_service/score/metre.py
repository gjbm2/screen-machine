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
triple 91% / 96%.  (PM2S was trained on ASAP, so its outputs are in-sample there: ASAP numbers flatter it.)

**The phrase** (the analysis worker, analyse.py, keeps one per phrase): PM2S changes metrical level from window to
window (a waltz's beat at 0.34 s, then 0.62 s a second later), so one window's bar evidence never adds up.
``Phrase`` keeps the phrase's notes with PM2S's per-note downbeat and time-signature outputs and a ``HeldGrid``:
the pulse held at one level (each window's newest beats brought onto it: subdivided, every 2nd or 3rd, through
2:3, beat by beat; the level follows another only when the windows' vote for it leads).  ``phrase_metre`` reads
the class over the phrase's last PHRASE_S on that grid with the phrase network (metre_weights.json "phrase";
tools/score/fit_metre_phrase.py): window_features without PM2S's time-signature head (it calls waltzes duple off
ASAP), subdivision2 (where onsets fall in the pulse over the whole stretch), the accent signal's periodicity in
time at multiples of the pulse (``periodicity``: robust to a drifting grid and to the pulse's level), the tempo.
It is fitted on every level of the held pulse (level_grids), so it reads the class whichever level PM2S holds,
on ASAP, PiJAMA jazz and PianoCoRe performances labelled by their PDMX score's time signature.
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
    hit = idx >= 0
    ih = idx[hit]
    cnt = np.bincount(ih, minlength=m).astype(float)
    have = cnt > 0
    if down is not None:
        dmax = np.full(m, -np.inf)
        np.maximum.at(dmax, ih, down[hit])
        s["down"][have] = dmax[have]
    s["vel"] = np.bincount(ih, weights=vel[hit], minlength=m) / 127.0
    s["cnt"] = cnt
    dm = np.zeros(m)
    np.maximum.at(dm, ih, dur[hit])
    s["dur"][have] = np.log1p(dm[have] * 4.0)
    bass = np.full(m, np.inf)
    np.minimum.at(bass, ih, pitch[hit])
    bass[~have] = np.nan
    s["bass"][have] = np.clip((72.0 - bass[have]) / 36.0, 0.0, 1.5)
    prev = np.concatenate([[np.nan], bass[:-1]])
    s["bnew"] = (have & (np.isnan(prev) | (bass != prev))).astype(float)
    # chroma sounding over each beat span, and the onset pattern inside it
    edges = np.concatenate([beats, [beats[-1] + (beats[-1] - beats[-2] if m > 1 else 0.5)]])
    off = on + dur
    pc = pitch.astype(int) % 12
    lo, hi = edges[:-1, None], edges[1:, None]
    ov = np.clip(np.minimum(off[None, :], hi) - np.maximum(on[None, :], lo), 0.0, None)
    chroma = ov @ np.eye(12)[pc]
    pattern = np.zeros((m, SUB_BINS + 12))
    bi = np.searchsorted(edges - 0.03, on, side="right") - 1
    ok = (bi >= 0) & (bi < m)
    if ok.any():
        b_ = bi[ok]
        span = np.maximum(1e-3, edges[b_ + 1] - edges[b_])
        ph = np.clip(((on[ok] - edges[b_]) / span * SUB_BINS).round().astype(int), 0, SUB_BINS - 1)
        np.add.at(pattern, (b_, ph), vel[ok] / 127.0)
        np.add.at(pattern, (b_, SUB_BINS + pc[ok]), 0.5)
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
    return _acs(v, (lag,))[0]


def _acs(v: np.ndarray, lags) -> list:
    """The autocorrelation (Pearson, over the overlap) at each lag, from running sums (_ac's values, faster)."""
    n = len(v)
    c1 = np.concatenate([[0.0], np.cumsum(v)])
    c2 = np.concatenate([[0.0], np.cumsum(v * v)])
    out = []
    for L in lags:
        if n <= L + 2:
            out.append(0.0)
            continue
        k = n - L
        ma, mb = c1[k] / k, (c1[n] - c1[L]) / k
        va, vb = c2[k] / k - ma * ma, (c2[n] - c2[L]) / k - mb * mb
        if va <= 1e-12 or vb <= 1e-12:              # constant (std under 1e-6: round-off)
            out.append(0.0)
            continue
        out.append(float((float(v[:k] @ v[L:]) / k - ma * mb) / math.sqrt(va * vb)))
    return out


def window_features(notes: np.ndarray, beats: np.ndarray, s: dict, X: np.ndarray, ts: Optional[np.ndarray],
                    downs: Optional[np.ndarray]) -> np.ndarray:
    """The network's input (see the module docstring)."""
    triple = float(np.mean(ts > 0.5)) if ts is not None and len(ts) else 0.5
    f: list[float] = []
    for c in CUES:
        f += _acs(s[c], range(1, 9))
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
        self.pnet = weights.get("phrase")         # the class over a phrase's held grid (phrase_metre)

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


# -- the phrase: a held pulse grid over many windows ------------------------------------------------------------------
SUB_W = 0.045                          # an onset this close (in pulses) to a subdivision point counts for it


def chord_onsets(notes: np.ndarray, gap: float = 0.035) -> tuple[np.ndarray, np.ndarray]:
    """Onsets merged into chords (within ``gap`` s): (times, weights: the chord's summed velocity / 127, at most 3)."""
    on = np.sort(notes[:, 1])
    if len(on) == 0:
        return on, on
    vel = notes[np.argsort(notes[:, 1], kind="stable"), 3] / 127.0
    start = np.concatenate([[True], np.diff(on) > gap])
    idx = np.cumsum(start) - 1
    w = np.minimum(np.bincount(idx, weights=vel), 3.0)
    return on[start], w


def pulse_phases(t: np.ndarray, grid: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Each time's pulse index and phase (0-1) inside its pulse on ``grid`` (times outside it dropped)."""
    k = np.searchsorted(grid, t, side="right") - 1
    ok = (k >= 0) & (k < len(grid) - 1)
    k = k[ok]
    ph = (t[ok] - grid[k]) / np.maximum(1e-6, grid[k + 1] - grid[k])
    return k, ph, ok


def subdivision2(notes: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Where chord onsets fall inside the pulse, over the whole stretch (shares of the off-pulse onsets near 1/3,
    2/3, 1/2, 1/4 or 3/4, 1/6 or 5/6; the swing zone 0.55-0.72 off 2/3), the on-pulse share, and the inter-onset
    intervals in pulses (shares near 1/3, 2/3, 1/2, 1/4, 3/4, 1, 3/2); each also velocity-weighted for the thirds and
    halves; log of the onsets counted."""
    c, w = chord_onsets(notes)
    k, ph, ok = pulse_phases(c, grid)
    w = w[ok]
    n = len(ph)
    if n < 8:
        return np.zeros(19)
    d = lambda x: np.abs(ph - x) < SUB_W               # noqa: E731
    onb = (ph < 0.06) | (ph > 0.94)
    off = ~onb
    no = max(1.0, float(off.sum()))
    wo = max(1e-6, float(w[off].sum()))
    t1, t2, h = d(1 / 3), d(2 / 3), d(0.5)
    q = d(0.25) | d(0.75)
    s6 = (np.abs(ph - 1 / 6) < 0.03) | (np.abs(ph - 5 / 6) < 0.03)
    sw = (ph > 0.55) & (ph < 0.72) & ~t2
    f = [t1.sum() / no, t2.sum() / no, h.sum() / no, q.sum() / no, s6.sum() / no, sw.sum() / no, onb.mean(),
         (w[t1 | t2].sum()) / wo, w[h].sum() / wo, w[q].sum() / wo]
    # inter-onset intervals in local pulses
    pos = k + ph
    io = np.diff(pos)
    io = io[(io > 0.05) & (io < 2.5)]
    if len(io) >= 4:
        near = lambda x, tol=0.06: float(np.mean(np.abs(io - x) < tol * max(1.0, x)))   # noqa: E731
        f += [near(1 / 3), near(2 / 3), near(0.5), near(0.25), near(0.75), near(1.0), near(1.5)]
    else:
        f += [0.0] * 7
    f += [math.log(1 + n), float(t1.sum() + t2.sum()) / max(1.0, float(h.sum() + q.sum() + t1.sum() + t2.sum()))]
    return np.array(f, dtype=float)


AC_DT = 0.02                           # the accent signal's frame (s)
AC_RATIOS = (1 / 3, 1 / 2, 2 / 3, 1.0, 3 / 2, 2.0, 3.0, 4.0, 6.0, 8.0)


def accent_signal(notes: np.ndarray, t0: float, t1: float) -> np.ndarray:
    """Onset accents on a AC_DT frame grid over [t0, t1]: each chord's loudness and size, a low bass, a long note,
    smoothed over about 40 ms."""
    n = int((t1 - t0) / AC_DT) + 1
    x = np.zeros(n)
    if len(notes) == 0 or n < 8:
        return x
    on = notes[:, 1]
    k = np.clip(((on - t0) / AC_DT).round().astype(int), 0, n - 1)
    w = notes[:, 3] / 127.0 + np.clip((60.0 - notes[:, 0]) / 24.0, 0.0, 1.5) + 0.5 * np.log1p(4.0 * np.minimum(notes[:, 2], 2.0))
    np.add.at(x, k, w)
    ker = np.array([0.25, 0.5, 1.0, 0.5, 0.25])
    return np.convolve(x, ker, mode="same")


def periodicity(notes: np.ndarray, P: float, t0: float, t1: float) -> np.ndarray:
    """The accent signal's autocorrelation (in time, so a drifting beat grid doesn't blur it) at multiples of the
    held pulse P (each the local peak within 6 %), relative to the mean over lags 0.1-6 s: ``AC_RATIOS`` x P, then
    the same normalised by the peak at P.  Bars of 3 beats show at 3P and 6P against 2P, 4P, 8P; dotted beats at P/3
    and 2P/3 against P/2; whatever level PM2S's pulse is at, the ratios between these say the metre."""
    x = accent_signal(notes, t0, t1)
    x = x - x.mean()
    n = len(x)
    if n < 64 or not np.any(x):
        return np.zeros(2 * len(AC_RATIOS))
    m = 1 << int(np.ceil(np.log2(2 * n)))
    f = np.fft.rfft(x, m)
    ac = np.fft.irfft(f * np.conj(f), m)[:n]
    ac = ac / max(1e-9, ac[0])
    lo, hi = int(0.1 / AC_DT), min(n - 1, int(6.0 / AC_DT))
    base = float(np.mean(ac[lo:hi])) if hi > lo else 0.0
    out = []
    for r in AC_RATIOS:
        c = r * P / AC_DT
        a, b = int(c * 0.94), int(np.ceil(c * 1.06)) + 1
        if b >= n or a < 1:
            out.append(0.0)
            continue
        out.append(float(ac[a:b].max()) - base)
    out = np.array(out)
    p1 = out[AC_RATIOS.index(1.0)]
    return np.concatenate([out, out / max(1e-3, abs(p1))])


# window_features' entries from PM2S's time-signature head (its triple vote, twice, and the mean): left out of the
# phrase network's input ("wfx").  PM2S was trained on ASAP, so on ASAP its time-signature head looks far better than
# it is on other music (it reads waltzes as duple), and a network fitted there leans on it.
WF_TS = (92, 93, 94)


def phrase_features(notes: np.ndarray, grid: np.ndarray, down: np.ndarray, ts: np.ndarray) -> Optional[dict]:
    """The class network's input on a held grid over the phrase: today's window features (the grid's own
    downbeats from the notes' PM2S downbeat probabilities), the phrase's subdivision evidence, the evidence's size.
    -> {"wf", "sub", "X"} or None."""
    if len(grid) < 6 or len(notes) < 8:
        return None
    triple = float(np.mean(ts > 0.5)) if len(ts) else 0.5
    s = saliences(notes, grid, down)
    X = candidate_features(s, triple)
    hi = down >= max(0.3, 0.5 * float(down.max()))
    downs = np.unique(np.round(notes[hi, 1], 2)) if hi.any() else None
    wf = window_features(notes, grid, s, X, ts, downs)
    sub = subdivision2(notes, grid)
    P = _ibi(grid)
    per = np.clip(periodicity(notes, P, float(grid[0]) - 0.5, float(max(grid[-1], notes[-1, 1])) + 0.5), -5.0, 5.0)
    return {"wf": wf, "wfx": np.delete(wf, WF_TS), "sub": sub, "per": per,
            "lp": np.array([math.log(P / 0.5)]),
            "X": X, "n": np.array([math.log(len(grid)), math.log(1 + len(notes))]), "s": s,
            "triple": triple}


FACTORS = (1 / 3, 1 / 2, 2 / 3, 1.0, 3 / 2, 2.0, 3.0)
KEEP_NEW_S = 6.0                       # a window's beats replace the held grid from this long before its newest note
LEVEL_DECAY = 0.97                     # the level vote: each window's weight against the next one's ...
LEVEL_FULL_S = 10.0                    # ... its beats' span (s) over this (a phrase's first, short windows count less)
LEVEL_MARGIN = 1.0                     # ... and another level takes over once it leads by this


def _ibi(b) -> float:
    return float(np.median(np.diff(b))) if len(b) > 1 else 0.5


def _subdivide(b: np.ndarray, k: int) -> np.ndarray:
    if len(b) < 2:
        return b
    f = np.arange(k) / k
    return np.concatenate([(b[:-1, None] + np.diff(b)[:, None] * f[None, :]).ravel(), b[-1:]])


def _decimate(b: np.ndarray, k: int, ref: Optional[np.ndarray] = None, weight: Optional[np.ndarray] = None) -> np.ndarray:
    """Every k-th beat: the phase that best matches ``ref`` where both have beats, else (``weight`` per beat) the
    strongest, else the first."""
    if len(b) < k + 1:
        return b
    best, best_s = 0, -1e18
    for j in range(k):
        sub = b[j::k]
        s = -float(j)
        if ref is not None and len(ref) > 1:
            ov = (sub >= ref[0] - 0.05) & (sub <= ref[-1] + 0.05)
            if ov.sum() >= 2:
                s = 1e6 - float(np.abs(sub[ov][:, None] - ref[None, :]).min(1).mean())
        if s < 1e5 and weight is not None:
            s = float(weight[j::k].mean())
        if s > best_s:
            best, best_s = j, s
    return b[best::k]


def relevel(W: np.ndarray, P: float, ref: Optional[np.ndarray] = None, weight=None, f: Optional[float] = None):
    """Beats ``W`` brought to the level of period ``P``: (beats, factor), factor = their period / P, the nearest of
    FACTORS (1 when none is near): subdivided when theirs are slower, every 2nd or 3rd when faster, and at 2:3 / 3:2
    through the common subdivision."""
    if f is None:
        r = _ibi(W) / P
        f = min(FACTORS, key=lambda x: abs(math.log(r / x)))
        if abs(math.log(r / f)) > 0.25:
            f = 1.0
    near = lambda x: abs(f - x) < 1e-6        # noqa: E731
    if near(1.0) or len(W) < 2:
        return W, f
    if near(2.0) or near(3.0):
        return _subdivide(W, int(round(f))), f
    if near(1 / 2) or near(1 / 3):
        return _decimate(W, int(round(1 / f)), ref, weight), f
    if near(2 / 3):                            # theirs are 2/3 of ours: halved (thirds of ours), every 3rd
        return _decimate(_subdivide(W, 2), 3, ref), f
    return _decimate(_subdivide(W, 3), 2, ref), f      # 3/2: their thirds are our halves, every 2nd


def _factor(ibi: float, P: float) -> float:
    r = ibi / P
    f = min(FACTORS, key=lambda x: abs(math.log(r / x)))
    return f if abs(math.log(r / f)) <= 0.25 else 1.0


def _on_level(a: float, t: np.ndarray, P: float, tol: float = 0.25) -> list:
    """Beats ``t`` (after ``a``) one ``P`` apart from ``a`` on: each next beat is the one of the next four whose
    distance is nearest a whole number of pulses (one preferred); missed pulses (or a rest) are filled evenly;
    beats between pulses (a stretch read at twice or three times the rate) are passed over.  A beat nowhere near the
    pulse is skipped; the newest edge stops at it."""
    out, i, n = [a], 0, len(t)
    while i < n:
        a = out[-1]
        best = None
        for j in range(i, min(n, i + 4)):
            r = (t[j] - a) / P
            if r > 3.4:
                break
            k = max(1, int(round(r)))
            e = abs(r / k - 1.0) + 0.12 * (k - 1)
            if abs(r / k - 1.0) <= tol and (best is None or e < best[0]):
                best = (e, j, k)
        if best is None:
            if t[i] - a > 3.4 * P:             # a long gap: carry on from there
                out.append(t[i])
            i += 1
            continue
        _e, j, k = best
        out += [a + (t[j] - a) * q / k for q in range(1, k)] + [float(t[j])]
        i = j + 1
    return out[1:]


class HeldGrid:
    """The phrase's pulse, held at one level across analysis windows (PM2S changes level window to window: a waltz's
    beat at 0.34 s then 0.62 s a second later, and inside a window too), so bar evidence builds up on one grid.
    Each window's newest KEEP_NEW_S of beats replace the grid there (a window's older part was spliced when it was
    central in an earlier one), first brought to the held level (subdivided, every 2nd or 3rd, or through 2:3),
    their phase onto ours, then beat by beat onto the pulse (_on_level).  The level is the windows' vote (each by its
    beats' span up to LEVEL_FULL_S, decaying by LEVEL_DECAY a window): another level takes over once its vote leads
    the held one's by LEVEL_MARGIN (a phrase's first, short windows often read another level than it settles on)."""

    def __init__(self) -> None:
        self.t: Optional[np.ndarray] = None
        self.P: Optional[float] = None
        self.votes: dict = {}

    def update(self, W: np.ndarray, w_end: float) -> Optional[float]:
        W = np.asarray(W, float)
        if len(W) < 4:
            return None
        wt = min(1.0, (W[-1] - W[0]) / LEVEL_FULL_S)
        if self.t is None:
            P = _ibi(W)
            self.t = np.array(_on_level(W[0] - P, W, P))
            self.P = _ibi(self.t)
            self.votes = {1.0: wt}
            return 1.0
        f = _factor(_ibi(W), self.P)
        self.votes = {k: v * LEVEL_DECAY for k, v in self.votes.items()}
        self.votes[f] = self.votes.get(f, 0.0) + wt
        if f != 1.0 and self.votes[f] > self.votes.get(1.0, 0.0) + LEVEL_MARGIN:
            # their level leads the vote: it's ours now (the grid re-expressed, the votes counted from it)
            self.t, _ = relevel(self.t, self.P * f, W, f=1.0 / f)
            self.P *= f
            votes = {}
            for g, v in self.votes.items():
                h = min(FACTORS, key=lambda x: abs(math.log(g / f / x)))
                if abs(math.log(g / f / h)) < 0.05:
                    votes[h] = votes.get(h, 0.0) + v
            self.votes = votes
        cut = max(W[0], w_end - KEEP_NEW_S)
        old = self.t[self.t < cut - 0.5 * self.P]
        if len(old) < 2:
            old = self.t[:2]
        a = float(old[-1])
        raw = W[W > a + 0.3 * self.P]
        if len(raw) < 2:
            return f
        fn = _factor(_ibi(raw), self.P) if len(raw) >= 4 else 1.0
        pred = a + self.P * np.arange(1, int((raw[-1] - a) / self.P) + 3)     # ours carried on
        new, _ = relevel(raw, self.P, pred, f=fn)
        # their phase against ours carried on: agreed on and over a quarter pulse off, they read the pulse between
        # ours (an off-beat reading); ours stand, theirs move (as the builder's BeatGrid.merge)
        ref = pred[pred <= new[-1] + 0.5 * self.P][:8]
        if len(ref) >= 2 and len(new) >= 2:
            j = np.clip(np.searchsorted(new, ref), 1, len(new) - 1)
            near = np.where(np.abs(new[j - 1] - ref) <= np.abs(new[j] - ref), new[j - 1], new[j])
            ok = np.abs(near - ref) < 0.6 * self.P
            if ok.sum() >= 2:
                ang = 2.0 * np.pi * (near[ok] - ref[ok]) / self.P
                c, s_ = np.cos(ang).sum(), np.sin(ang).sum()
                off = math.atan2(s_, c) / (2.0 * np.pi)
                if math.hypot(c, s_) / ok.sum() > 0.5 and abs(off) > 0.25:
                    new = new - off * self.P
        on = _on_level(a, new[new > a + 0.3 * self.P], self.P)
        if on:
            self.t = np.concatenate([old, on])
            self.t = self.t[self.t >= self.t[-1] - HIST_S - 10.0]
            self.P = _ibi(self.t[-17:])
        return f


class Phrase:
    """A phrase's analysis state in the worker: its notes (absolute onsets) with PM2S's per-note downbeat and
    time-signature outputs, each from the window the note sat most centrally in, and the held pulse grid."""

    def __init__(self) -> None:
        self.notes: dict = {}                  # id -> [quality, pitch, onset, duration, velocity, down, ts]
        self.grid = HeldGrid()
        self.newest = -1e18

    def add(self, ns: np.ndarray, ids: list, t0: float, out: dict) -> None:
        on = ns[:, 1] + t0
        q = np.minimum(on - on[0], on[-1] - on)
        for i, nid in enumerate(ids):
            old = self.notes.get(nid)
            if old is None or q[i] >= old[0] - 0.5:
                self.notes[nid] = [float(q[i]), float(ns[i, 0]), float(on[i]), float(ns[i, 2]), float(ns[i, 3]),
                                   float(out["down"][i]), float(out["ts"][i])]
            else:                              # keep the better-placed outputs, the newer duration
                old[3] = float(ns[i, 2])
        self.newest = max(self.newest, float(on[-1]))
        for nid in [k for k, v in self.notes.items() if v[2] < self.newest - HIST_S]:
            del self.notes[nid]

    def arrays(self, lo: float, hi: float):
        """(notes (pitch, onset, duration, velocity), downbeat and time-signature probabilities) of the notes
        starting in [lo, hi], or None."""
        v = np.array([x for x in self.notes.values() if lo <= x[2] <= hi])
        if len(v) == 0:
            return None
        v = v[np.argsort(v[:, 2], kind="stable")]
        return v[:, 1:5], v[:, 5], v[:, 6]


HIST_S = 60.0                          # the notes a phrase keeps (the builder's history)
PHRASE_S = 40.0                        # the class is read over the phrase's last this many seconds


def level_grids(notes: np.ndarray, grid: np.ndarray, down: Optional[np.ndarray]) -> list:
    """The held grid and the same pulse at other levels a written beat may be at: every 2nd or 3rd pulse (the phase
    whose pulses are the strongest: downbeat probability, bass, harmonic change, loudness), halves and thirds.
    -> [(name, grid, step: k (every k-th) or -k (k-ths), phase: the first pulse taken)]."""
    out = [("1", grid, 1, 0)]
    if len(grid) >= 6:
        base = grid[0]
        n = notes.copy()
        n[:, 1] -= base
        s = saliences(n, grid - base, down)
        sal = s["down"] + s["bass"] + s["harm"] + s["vel"]
        for k in (2, 3):
            j = int(np.argmax([sal[jj::k].mean() for jj in range(k)]))
            out.append((f"x{k}", grid[j::k], k, j))
    out += [("/2", _subdivide(grid, 2), -2, 0), ("/3", _subdivide(grid, 3), -3, 0)]
    return out




def _phrase_x(net: dict, f: dict) -> np.ndarray:
    x = np.concatenate([np.atleast_1d(f[k]).ravel() for k in net["feats"]])
    return np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)


def phrase_metre(model: "MetreModel", ph: Phrase, now: float) -> Optional[tuple]:
    """The metre over a phrase's held grid (its last PHRASE_S).  The phrase network ("joint" in the weights; or the
    older "phrase" one) reads the class at the held pulse and, with "levels": "all", at the same pulse every 2nd and
    3rd, halved and in thirds too (PM2S's level is off for about a third of the phrases, and its pairs-of-beats
    reading of a waltz comes and goes): the class is the probabilities summed over the levels.  The written beat is
    the held pulse, except a compound class whose pulse groups in threes (6/8 held in eighths): then every 3rd pulse,
    from the bar candidates' strongest phase.  The bar lines: the candidate scores on the written beat, within the
    class's family.  -> (Metre, the written beats over the whole phrase (absolute), info) or None."""
    g = ph.grid
    net = model.pnet
    if g.t is None or not net:
        return None
    lo = now - PHRASE_S
    arr = ph.arrays(lo, now + 0.05)
    sel = (g.t >= lo - 0.05) & (g.t <= now + 0.05)
    grid = g.t[sel]
    if arr is None or len(grid) < 6 or len(arr[0]) < 8:
        return None
    notes, down, ts = arr
    i0 = int(np.argmax(sel))                      # the stretch's first pulse in the phrase grid
    cands = level_grids(notes, grid, down) if net.get("levels") == "all" else [("1", grid, 1, 0)]
    acc, feats = np.zeros(4), {}
    for name, cg, step, j in cands:
        if len(cg) < 6:
            continue
        n = notes.copy()
        n[:, 1] -= cg[0]
        f = phrase_features(n, cg - cg[0], down, ts)
        if f is None:
            continue
        feats[name] = (cg, step, j, f)
        p = dict(zip(net["classes"], mlp_proba(net, _phrase_x(net, f))))
        acc += np.array([p.get(c, 0.0) for c in ("Ds", "Dc", "Ts", "Tc")])
    if "1" not in feats or acc.sum() <= 0:
        return None
    acc = acc / acc.sum()
    cls = dict(zip(("Ds", "Dc", "Ts", "Tc"), (float(x) for x in acc)))
    pt, cp = cls["Ts"] + cls["Tc"], cls["Dc"] + cls["Tc"]
    cg, step, j, f = feats["1"]
    w = model.w if len(model.w) == f["X"].shape[1] else _default_bar_weights(f["X"].shape[1])
    level = "1"
    if cp > 0.5 and len(cg) >= 9:
        z = f["X"] @ w
        top = CANDIDATES[int(np.argmax(z))]
        if top[0] == 3:                           # dotted beats held in eighths: the beat is every 3rd pulse
            cg = cg[top[1]::3]
            step, j, level = 3, top[1], "x3"
            n = notes.copy()
            n[:, 1] -= cg[0]
            f = phrase_features(n, cg - cg[0], down, ts) if len(cg) >= 6 else None
            if f is None:
                cg, step, j, f = feats["1"]
                level = "1"
    if step > 1:                                  # the written beat over the whole phrase: every step-th pulse
        written = g.t[(i0 + j) % step::step]
    else:
        written = g.t
    triple = pt > 0.5
    z = f["X"] @ w
    probs, phases = {}, {}
    for L in (2, 3, 4):
        idx = [i for i, (LL, _p) in enumerate(CANDIDATES) if LL == L]
        zz = z[idx]
        phases[L] = CANDIDATES[idx[int(np.argmax(zz))]][1]
        probs[L] = float(np.exp(zz - z.max()).sum())
    d24 = probs[2] + probs[4]
    probs = {2: (1 - pt) * probs[2] / d24 if d24 > 0 else (1 - pt) / 2, 3: pt,
             4: (1 - pt) * probs[4] / d24 if d24 > 0 else (1 - pt) / 2}
    L = 3 if triple else (2 if probs[2] >= probs[4] else 4)
    downs = {LL: float(cg[phases[LL] % len(cg)]) for LL in (2, 3, 4)}     # as times: the caller re-indexes
    m = Metre(L, phases[L], cp > 0.5, float(probs[L]), cp, probs, phases)
    return m, written, {"cls": cls, "level": level, "downs": downs, "P": float(g.P)}
