"""A performance (MIDI with no beat grid: the Steinway's recordings, a file played in) to bars in about a second
(owner, 23 Oct: "first pass can't take minutes to generate a score").

The beat is tracked once over the whole piece, the classic way (Ellis 2007, "Beat tracking by dynamic programming"):
an onset envelope from the notes (100 frames a second: each onset weighs its loudness, more for a bass note), the
beat period from its autocorrelation (globally, then followed locally within -20 / +25 % for rubato, kept in the
readable range 0.4-1.0 s as the live clock keeps it), then the beats that best fit the onsets at that period by
dynamic programming.  The metre: each beat's accent (loudness, a bass note, long notes struck on it), and which of
2, 3 or 4 beats a bar, and which beat starts it, puts the accents on the downbeats; a beat whose notes fall on its
thirds rather than halves and quarters makes it compound (6/8, 9/8, 12/8).  Then sheet_grid.write puts the notes
on that grid as on the screens.  The neural reading (PM2S, sheet_analysis.py) is the slower pass asked for on its
own.
"""
from __future__ import annotations

import math

import numpy as np

from .sheet_grid import MidiFile, _key, _staffing, seconds_of, write

FPS = 100
P_LO, P_HI = 0.4, 1.0                # the beat's readable range (s)
P_PRIOR = 0.65                       # the most likely beat (s) and ...
P_PRIOR_OCT = 0.8                    # ... the prior's width (octaves)
LOCAL_WIN_S = 8.0                    # the local period's window
RUBATO = (0.8, 1.25)                 # the local period within this of the global one
ALPHA = 80.0                         # DP: how hard a beat interval off the local period is punished
BASS = 52                            # a note below E3 is a bass note (accent)
NEAR_S = 0.07                        # a note this close to a beat is on it
TERNARY_MIN = 0.2                    # compound: this share of off-beat notes on thirds and ...
TERNARY_OVER = 1.3                   # ... this many times those on halves / quarters


def _envelope(on: np.ndarray, w: np.ndarray, n: int) -> np.ndarray:
    env = np.zeros(n)
    np.add.at(env, np.clip((on * FPS).round().astype(int), 0, n - 1), w)
    k = np.exp(-0.5 * (np.arange(-6, 7) / 2.0) ** 2)
    return np.convolve(env, k / k.sum(), mode="same")


def _autocorr(x: np.ndarray, max_lag: int) -> np.ndarray:
    x = x - x.mean()
    f = np.fft.rfft(x, 2 * len(x))
    ac = np.fft.irfft(f * np.conj(f))[:max_lag + 1]
    return ac / (ac[0] + 1e-9)


def _period(ac: np.ndarray, lo: float, hi: float, prior: bool) -> float:
    """The best period (frames) in [lo, hi]: autocorrelation at the lag plus half at its double and a third at its
    triple (a comb: the bar repeats it; never its half, which pulls to half speed), times the log-normal prior around
    P_PRIOR."""
    best, score = lo, -1e9
    for lag in range(max(2, int(lo)), min(len(ac) - 1, int(math.ceil(hi))) + 1):
        s = (ac[lag] + 0.5 * (ac[2 * lag] if 2 * lag < len(ac) else 0.0)
             + 0.33 * (ac[3 * lag] if 3 * lag < len(ac) else 0.0))
        if prior:
            s *= math.exp(-0.5 * (math.log2(lag / (P_PRIOR * FPS)) / P_PRIOR_OCT) ** 2)
        if s > score:
            best, score = lag, s
    return float(best)


def track(on: np.ndarray, w: np.ndarray, end_s: float) -> np.ndarray:
    """Beat times (s) for onsets ``on`` (s) weighing ``w``, covering 0 .. ``end_s``."""
    n = int(end_s * FPS) + 2
    env = _envelope(on, w, n)
    tau = _period(_autocorr(env, int(3 * P_HI * FPS) + 2), P_LO * FPS, P_HI * FPS, prior=True)
    # the local period, every second, within the rubato range of the global one
    half = int(LOCAL_WIN_S * FPS / 2)
    centres = np.arange(0, n, FPS)
    local = []
    for c in centres:
        seg = env[max(0, c - half):c + half]
        if seg.max() <= 0:
            local.append(tau)
            continue
        local.append(_period(_autocorr(seg, int(tau * RUBATO[1]) * 3 + 2), tau * RUBATO[0], tau * RUBATO[1],
                             prior=False))
    local = np.array(local)
    if len(local) >= 5:
        local = np.array([np.median(local[max(0, i - 2):i + 3]) for i in range(len(local))])
    per = np.interp(np.arange(n), centres, local)
    # dynamic programming: each frame's best previous beat 0.5-2 local periods back
    score = env.copy()
    back = np.full(n, -1)
    for t in range(n):
        p = per[t]
        a, b = t - int(round(2 * p)), t - int(round(p / 2))
        if b <= 0:
            continue
        a = max(0, a)
        d = t - np.arange(a, b + 1)
        cand = score[a:b + 1] - ALPHA * np.log(d / p) ** 2
        k = int(np.argmax(cand))
        if cand[k] > 0:
            score[t] = env[t] + cand[k]
            back[t] = a + k
    last_on = int(min(n - 1, on.max() * FPS + per[-1]))
    t = int(np.argmax(score[max(0, last_on - int(per[last_on])):last_on + 1])) + max(0, last_on - int(per[last_on]))
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    beats = np.array(beats[::-1], dtype=float) / FPS
    if len(beats) < 2:
        beats = np.arange(0.0, end_s + tau / FPS, tau / FPS)
    # cover the whole piece at the edge periods
    while beats[0] > on.min() - 1e-3:
        beats = np.insert(beats, 0, beats[0] - (beats[1] - beats[0]))
    while beats[-1] < end_s:
        beats = np.append(beats, beats[-1] + (beats[-1] - beats[-2]))
    return beats


def metre(on: np.ndarray, pitch: np.ndarray, vel: np.ndarray, dur: np.ndarray, beats: np.ndarray) -> tuple:
    """(beats a bar, 16ths a beat (4 simple, 6 compound), the index of a downbeat in ``beats``)."""
    ibi = np.diff(beats)
    k = np.clip(np.searchsorted(beats, on) - 1, 0, len(beats) - 2)
    frac = (on - beats[k]) / ibi[k]
    near = np.where(frac < 0.5, k, k + 1)
    off = np.abs(on - beats[near]) <= np.minimum(NEAR_S, 0.25 * ibi[np.minimum(near, len(ibi) - 1)])
    acc = np.zeros(len(beats))
    np.add.at(acc, near[off], vel[off] / 127.0 + 0.3 * np.minimum(dur[off] / ibi[np.minimum(near[off], len(ibi) - 1)], 2.0))
    bass = np.zeros(len(beats))
    np.maximum.at(bass, near[off], (pitch[off] < BASS).astype(float))
    acc += bass
    used = acc[:max(1, len(acc) - 1)]
    sd = used.std() + 1e-9
    best = {}
    for L in (2, 3, 4):
        cands = []
        for ph in range(L):
            idx = np.arange(len(used)) % L == ph
            if idx.sum() < 2 or (~idx).sum() < 2:
                continue
            cands.append(((used[idx].mean() - used[~idx].mean()) / sd, ph))
        best[L] = max(cands) if cands else (0.0, 0)
    c2, c3, c4 = best[2][0], best[3][0], best[4][0]
    L = 3 if c3 > 1.1 * max(c2, c4, 0.05) else (4 if c4 >= 0.85 * c2 else 2)
    # compound: off-beat notes on the beat's thirds, not its halves and quarters
    mid = (frac > 0.12) & (frac < 0.88)
    f = frac[mid]
    tern = np.sum((np.abs(f - 1 / 3) < 0.06) | (np.abs(f - 2 / 3) < 0.06))
    bina = np.sum((np.abs(f - 0.5) < 0.06) | (np.abs(f - 0.25) < 0.05) | (np.abs(f - 0.75) < 0.05))
    unit = 6 if len(f) and tern >= TERNARY_MIN * len(f) and tern > TERNARY_OVER * bina else 4
    return L, unit, best[L][1]


def bars(mf: MidiFile) -> list:
    """A performance as [(Bar, fifths)]: beats tracked, metre read, notes placed (sheet_grid.write)."""
    if not mf.notes:
        return []
    sec = seconds_of(mf)
    on = np.array([sec(n.on) for n in mf.notes])
    off = np.array([sec(n.off) for n in mf.notes])
    pitch = np.array([n.pitch for n in mf.notes])
    vel = np.array([n.vel for n in mf.notes], dtype=float)
    w = vel / 127.0 * np.where(pitch < BASS, 1.5, 1.0)
    end_s = float(off.max())
    beats = track(on, w, end_s)
    L, U, ph = metre(on, pitch, vel, off - on, beats)
    idx = np.arange(len(beats), dtype=float)

    def pos(t):                          # seconds -> beats (linear past the ends)
        t = np.asarray(t, dtype=float)
        b = np.interp(t, beats, idx)
        b = np.where(t < beats[0], (t - beats[0]) / (beats[1] - beats[0]), b)
        return np.where(t > beats[-1], idx[-1] + (t - beats[-1]) / (beats[-1] - beats[-2]), b)

    s16 = np.round(pos(on) * U).astype(int)
    e16 = np.maximum(np.round(pos(off) * U).astype(int), s16 + 1)
    notes = [(int(s), int(e), n.pitch, n.vel, n.id) for s, e, n in zip(s16, e16, mf.notes)]
    first_bar = int(math.floor((s16.min() / U - ph) / L))
    last16 = int(e16.max())
    lines, k = [], first_bar
    while (ph + k * L) * U < last16 + L * U:
        lines.append(((ph + k * L) * U, L, U))
        k += 1
    to16 = lambda tick: int(np.round(pos(sec(tick)) * U))       # noqa: E731
    keys = [(to16(t), f) for t, f in _key(mf)]
    pedal = [(to16(t), d) for t, d in mf.pedal]
    return write(notes, lines, _staffing(mf), keys, pedal, lambda x: float(np.interp(x / U, idx, beats)))
