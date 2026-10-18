#!/usr/bin/env python3
"""Audio calibration analysis: what one room + PA + mic chain does to music, and a model of it (roomsim.py).

Like tools/room/latency_cal (light latency), this is an occasional measurement per room and mic. The owner plays a
track over the PA, calibrate.py records what the mic hears (rec.wav, with room sound before the music) and fetches the
original (original/original-48k.wav). This script compares the two and writes, in the run directory:
  analysis/report.json   every number (sections below)
  analysis/report.md     a short plain-English summary for the owner
  analysis/figs/*.png    response + coherence, IR + decay, room sound, music level over time, gain, level in/out,
                         onset envelopes, alignment, model fit, live beats vs ground truth
  analysis/beats.json    the pseudo ground truth (madmom and Beat This!), original and recording time
  model/model.json       the room model roomsim.apply() replays (+ ir.wav, band_limit.wav, noise.wav)
and, for a real run (not --no-config, not synthetic), config/room/audio_cal.json's entry for the run's stream
(summary.music_lufs: the music's loudness at the mic, what dsp's loudness gate should expect).

Nothing in the real world is controlled, so every step is built to cope:
- the original may be another master, edit, start point or speed (up to 2 %) than what was played;
- the owner may start or seek the track before the clean run; other sound (talk, typing, a phone) overlaps it;
- the mic may be a speakerphone whose own processing (noise suppression, echo cancelling, a gate, a 16 kHz
  internal rate) reworks the sound so much that waveform methods (H1, an impulse response) stop working.

Sections (report.json keys):
  alignment  run.json ``music`` (calibrate.py already found and speed-matched the clean section: rec-music.wav and
             original/original-matched.wav) is used when present, plus the acoustic lag (GCC-PHAT over the section;
             band features as the cross-check). Otherwise ``align``: 6 s windows of 24 log band energies (80 Hz-8 kHz,
             200 fps) matched against the whole original by normalised cross-correlation, a consensus line through
             them (speed within +-2 %), the original resampled to that speed and matched again (sub-frame), then
             GCC-PHAT per 8 s window when the waveform allows it. Either way ``align_bands`` also runs: a cross-check
             of the run's map, and the windows where the track played off the line (the owner seeking: ``seek``).
  regions    music start/end, the clean spans (the music minus events, below).
  room_sound the recording outside the music and the seeks (events included: it is the room's real sound) ->
             noise.wav, the model's room-sound bed; its level, spectrum, mains hum; the quiet floor (the quietest open
             10 ms frames) and the mic's gate (how often it shuts to digital silence, at what level, how fast).
  levels     music at the mic: short-term (3 s) loudness median and spread (``at_mic``: what dsp's gate reads),
             integrated loudness, RMS, peaks, clipping; the original as played; the chain's gain (music only: the
             room sound's power taken off); music over room sound; 1 s loudness blocks against the original ->
             ``events``: other sound over the music (6 dB or more over the median difference) and dropouts (15 dB
             or more under it while the music plays), both left out of every fit and comparison.
  response   H1 = Gyx / Gxx with coherence (Welch, 2^17-point segments, clean spans), corners, tilt, group delay;
             and the power response (music only: the room sound's spectrum taken off the recording's), which the
             model uses when the coherence is low; the band limits (``split_response``: the cliffs at the band's
             ends, the mic's own filters) and the passband between them.
  ir         the impulse response by regularised deconvolution (Wiener-weighted for the decay), Schroeder decay,
             EDT/T20/T30, DRR, C50, C80 - each with an honest caveat when the coherence makes them the estimator's
             numbers, not the room's.
  dynamics   50 ms gain trajectory, level in/out curve, a fitted level curve (compressor/AGC/expander), ducking,
             sustained vs transient gain; ``noise_suppression``: gain against each octave band's level over the
             room sound (a level-dependent gate). Both go in the model only for a plain mic (no processing fitted).
  residual   recording minus the linear prediction: per 1/3 octave, per level, interference blocks.
  processing ``fit_processing``: what a linear chain can't explain, fitted by replaying the original and matching
             the real recording's coherence (per octave) and onset survival (dsp's bands): a diffuse room tail (RT60,
             DRR from a grid: the room's echo, when no impulse response can be read through the mic) and roomsim's
             ``scramble`` (a random spectral gain per frequency region: the mic's processing, a stand-in); plus the
             gate measured from the room sound and the dropouts' rate from ``events``.
  beats      onset-envelope survival (dsp.py's log-mel SuperFlux: 2048 Hann, hop 256, 40 HTK bands 30 Hz-16 kHz,
             lag 2, 3-band max filter) overall and per band; madmom offline, madmom online (2 nets: the live
             clock's) and Beat This! activations and beats on the recording and the model's replays, against the
             original's (``trackers``).
  ground_truth  madmom's offline beats of the whole original (8 nets + DBN; Beat This! as the cross-check), as
             heard at the mic (and against reference/beats-at-mic.beats when the run has one).
  live       bus.jsonl: host time -> recording time from dsp's music.hit against dsp's own hits on the recording
             (beats_offline.py dsphits: the same detector on the same audio), else against the onset envelope, else
             the clock lines (+-1 s: not scored); the beats the live clock flashed against the ground truth; the live
             stream's packet loss while the music played (state.jsonl).
  model_fit  the original through the model (several seeds) next to the real recording on the clean music: loudness,
             band levels and edges, coherence, onsets, madmom online, and our beat stack (beat_eval/run_stack.py)
             at dsp's default gate and at the music's loudness (``--stack-gates``).

Usage (heavy: run it under the CPU lock, nice 19; the beat trackers and the stack run as subprocesses in their envs):
  flock ~/.cache/screen-machine/cpu.lock nice -n 19 ~/.venvs/sm-roundtrip/bin/python \\
      tools/room/audio_cal/analyse.py tools/room/audio_cal/runs/<run> [--no-config] [--skip-beats] [--no-stack]
      [--stack-gates -30,-47] [--seeds 0,1,2]
From Python (calibrate.py): analyse(run_dir, write_config=True, cpu_lock=True) takes the lock in-process instead.
"""
from __future__ import annotations

import os

for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_k, "1")      # the host renders 4K: one core, never a pool

import argparse
import datetime as dt
import json
import math
import pathlib
import re
import shutil
import subprocess
import sys
import time
import warnings

import numpy as np
import scipy.fft as sfft
import scipy.ndimage as ndi
import scipy.optimize as spo
import scipy.signal as sps
import soundfile as sf
import soxr

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import roomsim  # noqa: E402

REPO = HERE.parents[2]
CONFIG_JSON = REPO / "config" / "room" / "audio_cal.json"
SHOW_YAML = REPO / "config" / "room" / "show.yaml"
PY_SHOW = pathlib.Path.home() / ".venvs" / "sm-show" / "bin" / "python"
PY_ML = pathlib.Path.home() / ".venvs" / "sm-show-ml" / "bin" / "python"
CPU_LOCK = pathlib.Path.home() / ".cache" / "screen-machine" / "cpu.lock"
BEATS_WORKER = HERE / "beats_offline.py"
RUN_STACK = REPO / "tools" / "room" / "beat_eval" / "run_stack.py"
BEAT_MODEL = "beat-this-small0"

FS = 48000
EPS = 1e-20
VERSION = 2                   # model.json: 2 adds the mic's processing and the room-sound bed
VAL_SEEDS = (0, 1, 2)         # the model's replays for the fit table

# alignment
WIN_S, HOP_S = 8.0, 4.0       # alignment windows
GCC_BAND = (150.0, 6000.0)
MIN_PHAT_SNR = 6.0            # a window's GCC-PHAT peak must stand this many sigmas
MAX_DRIFT = 2e-3              # 2000 ppm: anything beyond is not a clock
BF_FPS = 200.0                # band-feature alignment (align_bands): frames per second,
BF_BANDS, BF_LO_HZ, BF_HI_HZ = 24, 80.0, 8000.0          # 24 log bands 80 Hz-8 kHz,
BF_WIN_S, BF_HOP_S = 6.0, 3.0  # 6 s windows every 3 s,
BF_MIN_R, BF_MIN_PROM = 0.15, 0.05                        # a window matches when its NCC peak is this high and clear,
BF_SEEK_R, BF_SEEK_R_STRONG = 0.3, 0.45  # (off the line: two such windows advancing together, or one strong: a seek)
BF_SPEED_MAX = 0.02           # and the played copy may run up to 2 % faster or slower than the fetched one
# linear chain
H_NFFT = 2 ** 17              # 2.73 s segments: long against a domestic RT60
IR_PRE_S = 0.010              # IR samples kept before the alignment point
IR_KEEP_MAX_S = 1.5
# the mic's processing stand-in (roomsim.scramble): fixed choices, sigma is fitted (fit_processing)
SCR_TAU_MS = 40.0
SCR_BPO = 6.0
# room sound
GATED_DBFS = -100.0           # a 10 ms frame this quiet is the mic's gate closed (digital near-silence)
BED_GUARD_S = 2.0             # the room-sound bed stops this long before the music (or the first seek) starts
BED_MAX_S = 180.0             # noise.wav at most this long
EDGE_SLOPE = 24.0             # dB per octave: steeper than this at the band's ends is the mic's own filter
# dynamics
FRAME, FHOP = 2400, 1200      # 50 ms frames, 25 ms hop
OCT_EDGES = [44.2, 88.4, 176.8, 353.6, 707.1, 1414.2, 2828.4, 5656.9, 11313.7, 16000.0]
OCT_CENTRES = [63, 125, 250, 500, 1000, 2000, 4000, 8000, 12000]
# beats
BEAT_BANDS = {"sub": (20.0, 60.0), "bass": (60.0, 250.0), "mid": (250.0, 4000.0), "high": (4000.0, 16000.0)}

PAL = {"blue": "#2a78d6", "orange": "#eb6834", "aqua": "#1baf7a", "yellow": "#eda100", "violet": "#6250d6"}
SURF, INK, INK2, GRID, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df", "#a3a29d"


# ======================================================================================================================
# small helpers
# ======================================================================================================================
def _db(p):
    return 10.0 * np.log10(np.asarray(p, dtype=np.float64) + EPS)


def _f(v, nd=4):
    """A float for the report: rounded, None for NaN/inf."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return round(v, nd)


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.ndarray):
        return [_clean(v) for v in o.tolist()]
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (int, np.integer)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return _f(o, 5)
    return o


def _pearson(a, b) -> float:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 10:
        return math.nan
    a, b = a[ok] - a[ok].mean(), b[ok] - b[ok].mean()
    d = math.sqrt(float(a @ a) * float(b @ b))
    return float(a @ b) / d if d > 0 else math.nan


def _best_lag_corr(ref, est, max_lag: int, mask=None) -> tuple[float, int]:
    """Pearson r of est shifted by each lag in +-max_lag frames (positive: est later than ref) -> (best r, lag)."""
    best, lag_b = -2.0, 0
    n = len(ref)
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            a, b = ref[:n - lag], est[lag:]
            m = mask[:n - lag] if mask is not None else None
        else:
            a, b = ref[-lag:], est[:n + lag]
            m = mask[-lag:] if mask is not None else None
        r = _pearson(a[m], b[m]) if m is not None else _pearson(a, b)
        if r > best:
            best, lag_b = r, lag
    return best, lag_b


def read_audio(path: pathlib.Path) -> tuple[np.ndarray, int, int]:
    """(mono float64 at 48 kHz, original rate, channels)."""
    x, fs = sf.read(str(path), dtype="float64", always_2d=True)
    ch = x.shape[1]
    x = x.mean(axis=1)
    if fs != FS:
        x = soxr.resample(x, fs, FS, quality="VHQ")
    return x, fs, ch


def mask_from_spans(spans, n: int, rate: float = FS) -> np.ndarray:
    """Boolean mask of length n (at ``rate`` per second) from [(start_s, end_s)]."""
    m = np.zeros(n, dtype=bool)
    for a, b in spans:
        m[max(0, int(round(a * rate))):max(0, min(n, int(round(b * rate))))] = True
    return m


def runs_of(mask: np.ndarray) -> list[tuple[int, int]]:
    """[(start, end)] index runs where mask is True."""
    if not len(mask):
        return []
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(d == 1).tolist(), np.flatnonzero(d == -1).tolist()))


def third_octave_centres(lo: float = 25.0, hi: float = 16000.0) -> np.ndarray:
    k = np.arange(round(10 * math.log10(lo)), round(10 * math.log10(hi)) + 1)
    return 10 ** (k / 10.0)


def band_sum(f: np.ndarray, psd: np.ndarray, centres: np.ndarray, frac: float = 3.0) -> np.ndarray:
    """Power per fractional-octave band (sum of PSD x df) at ``centres``."""
    df = f[1] - f[0]
    out = np.empty(len(centres))
    for i, c in enumerate(centres):
        sel = (f >= c * 2 ** (-0.5 / frac)) & (f < c * 2 ** (0.5 / frac))
        out[i] = psd[sel].sum() * df if sel.any() else psd[np.argmin(np.abs(f - c))] * df
    return out


def welch_spans(x: np.ndarray, spans_smp, nperseg: int = 8192) -> tuple[np.ndarray, np.ndarray]:
    """One-sided PSD (power per Hz, mean square = integral) averaged over all segments of all spans."""
    acc, cnt = None, 0
    f = np.fft.rfftfreq(nperseg, 1.0 / FS)
    for a, b in spans_smp:
        if b - a < nperseg:
            continue
        f, p = sps.welch(x[a:b], FS, window="hann", nperseg=nperseg, noverlap=nperseg // 2)
        k = 1 + (b - a - nperseg) // (nperseg // 2)
        acc = p * k if acc is None else acc + p * k
        cnt += k
    return f, (acc / cnt if cnt else np.zeros(len(f)))


# ======================================================================================================================
# onset envelopes (log-mel SuperFlux, dsp.py's recipe)
# ======================================================================================================================
_FB = {}


def mel_bank_htk(n_fft: int = 2048, n_mels: int = 40, fmin: float = 30.0, fmax: float = 16000.0):
    key = (n_fft, n_mels, fmin, fmax)
    if key not in _FB:
        hz2mel = lambda f: 2595.0 * np.log10(1.0 + np.asarray(f) / 700.0)   # noqa: E731
        mel2hz = lambda m: 700.0 * (10 ** (np.asarray(m) / 2595.0) - 1.0)   # noqa: E731
        pts = mel2hz(np.linspace(hz2mel(fmin), hz2mel(fmax), n_mels + 2))
        f = np.fft.rfftfreq(n_fft, 1.0 / FS)
        fb = np.zeros((len(f), n_mels))
        for k in range(n_mels):
            lo, c, hi = pts[k], pts[k + 1], pts[k + 2]
            tri = np.maximum(0.0, np.minimum((f - lo) / (c - lo), (hi - f) / (hi - c)))
            if tri.sum() <= 0:
                tri[np.argmin(np.abs(f - c))] = 1.0
            fb[:, k] = tri / tri.sum()                  # triangles averaged
        _FB[key] = (fb, pts[1:-1])
    return _FB[key]


def logmel(x: np.ndarray, hop: int, n_fft: int = 2048) -> np.ndarray:
    """(frames, 40) log-mel power in dB (floor -80); frame j is centred on sample j * hop."""
    fb, _ = mel_bank_htk(n_fft)
    win = sps.get_window("hann", n_fft)
    scale = 4.0 / win.sum() ** 2                       # a full-scale sine's bin reads 0 dB
    xp = np.pad(np.asarray(x, dtype=np.float64), (n_fft // 2, n_fft // 2))
    view = np.lib.stride_tricks.sliding_window_view(xp, n_fft)[::hop]
    out = np.empty((len(view), fb.shape[1]), dtype=np.float32)
    for a in range(0, len(view), 1024):
        spec = np.abs(np.fft.rfft(view[a:a + 1024] * win, axis=1)) ** 2 * scale
        out[a:a + 1024] = np.maximum(10 * np.log10(spec @ fb + 1e-12), -80.0)
    return out


def superflux(mel_db: np.ndarray, lag: int) -> np.ndarray:
    """Per-band positive flux against a 3-band max filter of the frame ``lag`` back (frames, bands)."""
    mx = ndi.maximum_filter1d(mel_db, size=3, axis=1)
    d = np.zeros_like(mel_db)
    d[lag:] = mel_db[lag:] - mx[:-lag]
    return np.maximum(d, 0.0)


def onset_bands(x: np.ndarray, hop: int = 256, lag: int = 2) -> dict:
    """{"total": flux, "<band>": flux over the mel bands centred in that band}, at FS / hop frames per second."""
    d = superflux(logmel(x, hop), lag)
    _, centres = mel_bank_htk()
    out = {"total": d.sum(axis=1)}
    for name, (lo, hi) in BEAT_BANDS.items():
        sel = (centres >= lo) & (centres < hi)
        if not sel.any():
            sel = np.zeros(len(centres), dtype=bool)
            sel[np.argmin(np.abs(centres - math.sqrt(lo * hi)))] = True
        out[name] = d[:, sel].sum(axis=1)
    return out


def peak_pick(env: np.ndarray, fps: float, wait_s: float = 0.03, avg_s: float = 0.1, delta: float = 0.3) -> np.ndarray:
    """Onset times (s): local maxima within +-wait_s above the moving mean over +-avg_s plus delta x std."""
    w = max(1, int(round(wait_s * fps)))
    a = max(1, int(round(avg_s * fps)))
    mx = ndi.maximum_filter1d(env, 2 * w + 1)
    mean = ndi.uniform_filter1d(env, 2 * a + 1)
    thr = mean + delta * float(np.std(env))
    pk = np.flatnonzero((env == mx) & (env > thr) & (env > 0))
    keep, last = [], -10 ** 9
    for i in pk:
        if i - last > w:
            keep.append(i)
            last = i
    return np.asarray(keep, dtype=np.float64) / fps


# ======================================================================================================================
# robust lines
# ======================================================================================================================
def ransac_line(x, y, w, tol: float, slope_range: tuple[float, float], seed: int = 0):
    """Line y = c0 + c1 x by consensus: every pair (or 4000 random pairs) proposes a line with a slope in range, the
    one whose inliers (|residual| < tol) carry the most weight wins, then weighted least squares on its inliers,
    twice. -> (c0, c1, inlier mask) or None."""
    x, y, w = (np.asarray(v, dtype=np.float64) for v in (x, y, w))
    n = len(x)
    if n < 2:
        return None
    if n <= 90:
        i, j = np.triu_indices(n, 1)
    else:
        rng = np.random.default_rng(seed)
        i, j = rng.integers(0, n, 4000), rng.integers(0, n, 4000)
        k = i != j
        i, j = i[k], j[k]
    dx = x[j] - x[i]
    ok = np.abs(dx) > 1e-9
    i, j, dx = i[ok], j[ok], dx[ok]
    s = (y[j] - y[i]) / dx
    ok = (s >= slope_range[0]) & (s <= slope_range[1])
    i, j, s = i[ok], j[ok], s[ok]
    if not len(s):
        return None
    xm = x.mean()
    res = np.abs(y[None, :] - (y[i, None] + s[:, None] * (x[None, :] - x[i, None])))
    score = ((res < tol) * w[None, :]).sum(axis=1)
    b = int(np.argmax(score))
    inl = res[b] < tol
    c0, c1 = y[i[b]] - s[b] * x[i[b]], s[b]
    for _ in range(2):
        if inl.sum() < 2:
            break
        A = np.stack([np.ones(inl.sum()), x[inl] - xm], axis=1)
        ww = np.sqrt(w[inl])
        coef, *_ = np.linalg.lstsq(A * ww[:, None], y[inl] * ww, rcond=None)
        c1 = float(coef[1])
        c0 = float(coef[0] - coef[1] * xm)
        inl = np.abs(y - (c0 + c1 * x)) < tol
    return c0, c1, inl


# ======================================================================================================================
# 1. alignment
# ======================================================================================================================
def _zenv(e: np.ndarray) -> np.ndarray:
    e = np.sqrt(np.maximum(e, 0.0))
    e = np.minimum(e, np.percentile(e, 99.5) + 1e-9)
    return (e - e.mean()) / (e.std() + 1e-12)


def gcc_phat(y_seg: np.ndarray, x_seg: np.ndarray, k_lo: int, k_hi: int, span: int = 2400) -> tuple[float, float]:
    """GCC-PHAT (150 Hz-6 kHz): the lag k (samples, sub-sample) maximising sum y[n] x[n - k] over k_lo..k_hi, and the
    peak's height in standard deviations of the correlation within +-span samples (50 ms) of it, outside +-1 ms."""
    n = len(y_seg) + len(x_seg)
    nfft = sfft.next_fast_len(n, real=True)
    ty = sps.windows.tukey(len(y_seg), 0.1)
    tx = sps.windows.tukey(len(x_seg), 0.1)
    G = np.fft.rfft(y_seg * ty, nfft) * np.conj(np.fft.rfft(x_seg * tx, nfft))
    G /= np.abs(G) + 1e-9 * (np.abs(G).mean() + EPS)
    f = np.fft.rfftfreq(nfft, 1.0 / FS)
    G[(f < GCC_BAND[0]) | (f > GCC_BAND[1])] = 0.0
    cc = np.fft.irfft(G, nfft)
    ks = np.arange(k_lo, k_hi + 1)
    v = cc[ks % nfft]
    j = int(np.argmax(v))
    frac = 0.0
    if 0 < j < len(v) - 1:
        den = v[j - 1] - 2 * v[j] + v[j + 1]
        if den < 0:
            frac = 0.5 * (v[j - 1] - v[j + 1]) / den
    kn = np.arange(ks[j] - span, ks[j] + span + 1)
    rest = cc[kn[np.abs(kn - ks[j]) > 48] % nfft]
    snr = float(v[j] / (rest.std() + 1e-12))
    return float(ks[j] + frac), snr


def band_features(x: np.ndarray, fps: float = BF_FPS) -> np.ndarray:
    """(frames, BF_BANDS) alignment features: log energy in 24 log-spaced bands (80 Hz-8 kHz, 1024-point Hann,
    ``fps`` frames per second), minus each band's 1 s moving average (EQ, level and slow gain riding drop out),
    z-scored per band. Frame j is centred on sample j * FS / fps."""
    hop = int(round(FS / fps))
    n_fft = 1024
    f = np.fft.rfftfreq(n_fft, 1.0 / FS)
    edges = np.geomspace(BF_LO_HZ, BF_HI_HZ, BF_BANDS + 1)
    M = np.zeros((len(f), BF_BANDS))
    for b in range(BF_BANDS):
        M[(f >= edges[b]) & (f < edges[b + 1]), b] = 1.0
    win = sps.get_window("hann", n_fft)
    xp = np.pad(np.asarray(x, dtype=np.float64), (n_fft // 2, n_fft // 2))
    view = np.lib.stride_tricks.sliding_window_view(xp, n_fft)[::hop]
    out = np.empty((len(view), BF_BANDS))
    for a in range(0, len(view), 2048):
        out[a:a + 2048] = (np.abs(np.fft.rfft(view[a:a + 2048] * win, axis=1)) ** 2) @ M
    lv = 10 * np.log10(out + 1e-12)
    lv = np.maximum(lv, lv.max() - 100.0)
    lv -= ndi.uniform_filter1d(lv, int(fps), axis=0, mode="nearest")
    return (lv - lv.mean(axis=0)) / (lv.std(axis=0) + 1e-9)


def _ncc_all_lags(F_o: np.ndarray, W: np.ndarray, csum: np.ndarray) -> np.ndarray:
    """Normalised cross-correlation of the window W (frames, bands; mean-centred per band) with F_o at every start
    frame 0..len(F_o) - len(W): FFT correlation per band, summed; csum = cumulative sum of F_o's squared rows."""
    n, w = len(F_o), len(W)
    nfft = sfft.next_fast_len(n + w, real=True)
    acc = np.zeros(nfft)
    for b in range(F_o.shape[1]):
        acc += np.fft.irfft(np.fft.rfft(F_o[:, b], nfft) * np.conj(np.fft.rfft(W[:, b], nfft)), nfft)
    num = acc[:n - w + 1]
    den = np.sqrt(np.maximum(csum[w:] - csum[:-w], 1e-9)) * (np.linalg.norm(W) + 1e-9)
    return num / den


def align_bands(rec: np.ndarray, orig: np.ndarray, log, speed_max: float = BF_SPEED_MAX) -> dict:
    """Find the original in the recording when the copy that was played may run up to ``speed_max`` (2 %) faster or
    slower than the fetched one (another master or transfer: a source difference, not a room effect), as
    screen-machine-26 did by hand for the first run: 6 s windows of band features (``band_features``, 200 fps),
    each matched against the whole original by normalised cross-correlation; a consensus line u = c0 + c1 t through
    the window matches (u original s, t recording s; slope within 1 +- speed_max); then the original is resampled to
    that speed and the windows matched again within +-100 ms of the line, with sub-frame (parabolic) peaks, for the
    final line. Windows that match the original clearly but off the line (the owner seeking, another version) are
    kept as ``seek`` so the room-sound bed can leave them out."""
    fps = BF_FPS
    F_r = band_features(rec, fps)
    F_o = band_features(orig, fps)
    cs = np.concatenate([[0.0], np.cumsum(np.sum(F_o ** 2, axis=1))])
    _, lv = roomsim.frame_power(rec, int(0.1 * FS), int(0.05 * FS))
    lv = _db(lv)
    floor_guess = float(np.percentile(lv, 3)) if len(lv) else -120.0
    wf, hf = int(BF_WIN_S * fps), int(BF_HOP_S * fps)
    far = int(0.5 * fps)
    wins = []
    for i0 in range(0, max(1, len(F_r) - wf + 1), hf):
        t0 = i0 / fps
        w = {"t0": t0, "t1": t0 + BF_WIN_S, "tc": t0 + BF_WIN_S / 2}
        seg_lv = lv[int(t0 * 20):int((t0 + BF_WIN_S) * 20)]
        if not len(seg_lv) or float(np.median(seg_lv)) < floor_guess + 6.0 or wf > len(F_o):
            w["status"] = "quiet"
            wins.append(w)
            continue
        W = F_r[i0:i0 + wf] - F_r[i0:i0 + wf].mean(axis=0)
        r = _ncc_all_lags(F_o, W, cs)
        j = int(np.argmax(r))
        lo, hi = max(0, j - far), min(len(r), j + far + 1)
        rest = np.concatenate([r[:lo], r[hi:]])
        w["r"] = float(r[j])
        w["prom"] = float(r[j] - (rest.max() if len(rest) else 0.0))
        w["u"] = j / fps + BF_WIN_S / 2                     # original time of the window's centre
        w["status"] = "candidate" if (w["r"] >= BF_MIN_R and w["prom"] >= BF_MIN_PROM) else "no_match"
        wins.append(w)
    cand = [w for w in wins if w["status"] == "candidate"]
    if len(cand) < 3:
        raise RuntimeError(f"alignment failed: {len(cand)} windows match the original; is it the right track?")
    fit = ransac_line([w["tc"] for w in cand], [w["u"] for w in cand], [w["r"] for w in cand],
                      tol=0.03, slope_range=(1 - speed_max, 1 + speed_max))
    if fit is None:
        raise RuntimeError("alignment failed: no consistent line through the window matches")
    c0, c1, inl = fit
    for w, ok in zip(cand, inl):
        if not ok:
            w["status"] = "no_match"
            w["off_line_s"] = w["u"] - (c0 + c1 * w["tc"])
    # the track off the line (the owner seeking, starting it twice): a strong match, or two windows that don't
    # overlap, both clear, advancing together at the line's speed. A single weak off-line match is chance (typing and
    # talk match a busy original at r ~0.3-0.4 now and then; overlapping windows share half their audio, so they
    # don't count as two), so it stays no_match.
    step = max(1, int(round(BF_WIN_S / BF_HOP_S)))
    for i, wa in enumerate(wins):
        if wa.get("off_line_s") is None:
            continue
        if wa.get("r", 0) >= BF_SEEK_R_STRONG and wa.get("prom", 0) >= 0.1:
            wa["status"] = "seek"
        wb = wins[i + step] if i + step < len(wins) else None
        if wb is not None and wb.get("off_line_s") is not None and min(wa.get("r", 0), wb.get("r", 0)) >= BF_SEEK_R:
            if abs((wb["u"] - wa["u"]) - c1 * (wb["tc"] - wa["tc"])) < 0.1:
                wa["status"] = wb["status"] = "seek"
    on = [w for w in cand if w["status"] == "candidate"]
    log(f"align (bands): pass 1 {len(on)}/{len(cand)} windows on the line, speed {c1:.5f} "
        f"({(c1 - 1) * 100:+.3f} %)")
    # pass 2: the original at the recording's speed, +-100 ms around the line, sub-frame peaks
    o_res = soxr.resample(orig, FS, FS / c1, quality="VHQ")    # o_res sample m <-> original time m c1 / FS
    F_s = band_features(o_res, fps)
    cs2 = np.concatenate([[0.0], np.cumsum(np.sum(F_s ** 2, axis=1))])
    srch = int(0.1 * fps)
    pts = []
    for w in on:
        i0 = int(round(w["t0"] * fps))
        W = F_r[i0:i0 + wf] - F_r[i0:i0 + wf].mean(axis=0)
        j_exp = (c0 + c1 * w["t0"]) / c1 * fps               # o_res frame expected at the window's start
        a, b = int(math.floor(j_exp)) - srch, int(math.ceil(j_exp)) + srch
        if a < 0 or b + wf > len(F_s):
            w["status"] = "no_overlap"
            continue
        r = _ncc_all_lags(F_s[a:b + wf], W, cs2[a:b + wf + 1] - cs2[a])
        j = int(np.argmax(r))
        frac = 0.0
        if 0 < j < len(r) - 1:
            den = r[j - 1] - 2 * r[j] + r[j + 1]
            if den < 0:
                frac = 0.5 * (r[j - 1] - r[j + 1]) / den
        w["r2"] = float(r[j])
        w["u2"] = (a + j + frac) / fps * c1 + BF_WIN_S / 2 * c1   # original time of the window's centre
        pts.append(w)
    if len(pts) < 3:
        raise RuntimeError("alignment failed in pass 2 (bands)")
    tc = np.array([w["tc"] for w in pts])
    u2 = np.array([w["u2"] for w in pts])
    fit2 = ransac_line(tc, u2, np.array([w["r2"] for w in pts]), tol=0.006,
                       slope_range=(c1 - 2e-3, c1 + 2e-3))
    c0f, c1f, inl2 = fit2 if fit2 is not None else (c0, c1, np.ones(len(pts), bool))
    res = u2 - (c0f + c1f * tc)
    for w, ok, rv in zip(pts, inl2, res):
        w["resid_ms"] = float(rv * 1000.0)
        w["status"] = "matched" if ok else "off_line"
        if not ok:
            w["off_line_s"] = float(rv)
    matched = [w for w in pts if w["status"] == "matched"]
    rr = res[inl2]
    status = {}
    for w in wins:
        status[w["status"]] = status.get(w["status"], 0) + 1
    log(f"align (bands): {len(matched)} windows matched, speed {c1f:.6f}, offset {-c0f / c1f:+.4f} s, residual rms "
        f"{float(np.sqrt(np.mean(rr ** 2))) * 1000 if len(rr) else 0:.2f} ms")
    return {"c0": float(c0f), "c1": float(c1f), "windows": wins, "matched": matched,
            "summary": {"method": "band features (24 log bands 80 Hz-8 kHz, 200 fps, 6 s windows)",
                        "speed": float(c1f), "speed_pct": float((c1f - 1) * 100), "offset_s": float(-c0f / c1f),
                        "windows": len(wins), "status": status, "matched": len(matched),
                        "residual_rms_ms": float(np.sqrt(np.mean(rr ** 2)) * 1000) if len(rr) else None,
                        "residual_max_ms": float(np.max(np.abs(rr)) * 1000) if len(rr) else None,
                        "r_median": float(np.median([w["r2"] for w in matched])) if matched else None,
                        "seek_windows_s": [[w["t0"], w["t1"]] for w in wins if w["status"] == "seek"]}}


def place_original(orig: np.ndarray, c0: float, c1: float, n: int) -> np.ndarray:
    """The original on the recording's timeline for u = c0 + c1 t: resampled to the recording's speed, then shifted
    by the (fractional) offset. Zero where the original has no sample."""
    x_res = soxr.resample(orig, FS, FS / c1, quality="VHQ")     # x_res sample m <-> original time m c1 / FS
    D = c0 * FS / c1                                           # x_al[m] = x_res[m + D]
    Di = int(math.floor(D))
    fr = D - Di
    out = np.zeros(n)
    a, b = max(0, -Di), min(n, len(x_res) - Di - 1)
    if b <= a:
        return out
    seg = x_res[a + Di:b + Di + 1]
    if abs(fr) > 1e-6:
        nf = sfft.next_fast_len(len(seg) + 64, real=True)
        seg = np.fft.irfft(np.fft.rfft(seg, nf) * np.exp(2j * np.pi * np.fft.rfftfreq(nf) * fr), nf)[:len(seg)]
    out[a:b] = seg[:b - a]
    return out


def acoustic_lag(y: np.ndarray, x: np.ndarray, max_ms: float = 60.0) -> dict:
    """The delay of y behind x (both on one timeline): GCC-PHAT over the whole span (150 Hz-6 kHz), and band
    features (sub-frame) as a cross-check. -> {"lag_ms", "gcc_ms", "gcc_snr", "bands_ms", "method"}."""
    m = int(max_ms / 1000 * FS)
    k, snr = gcc_phat(y, x, -m, m)
    Fy, Fx = band_features(y, BF_FPS), band_features(x, BF_FPS)
    n = min(len(Fy), len(Fx))
    L = int(max_ms / 1000 * BF_FPS) + 1
    rs = []
    for lag in range(-L, L + 1):
        a, b = (Fx[:n - lag], Fy[lag:n]) if lag >= 0 else (Fx[-lag:n], Fy[:n + lag])
        rs.append(float(np.sum(a * b)) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))
    rs = np.asarray(rs)
    j = int(np.argmax(rs))
    frac = 0.0
    if 0 < j < len(rs) - 1:
        den = rs[j - 1] - 2 * rs[j] + rs[j + 1]
        if den < 0:
            frac = 0.5 * (rs[j - 1] - rs[j + 1]) / den
    bands_ms = (j - L + frac) / BF_FPS * 1000.0
    gcc_ms = k / FS * 1000.0
    # GCC-PHAT's peak is the waveform's own evidence; band features read a few ms long through heavy processing
    use_gcc = snr >= 3.5 and abs(gcc_ms - bands_ms) <= 8.0
    return {"lag_ms": gcc_ms if use_gcc else bands_ms, "gcc_ms": gcc_ms, "gcc_snr": snr, "bands_ms": bands_ms,
            "bands_r": float(rs[j]), "method": "gcc-phat" if use_gcc else "band features"}


def align(rec: np.ndarray, orig: np.ndarray, log) -> dict:
    """Map original time u to recording time t: u = c0 + c1 t (+ a smooth sub-ms residual). Returns the windows,
    the fit, and ``x_al`` (the original on the recording's timeline, mono).

    Pass 1 is ``align_bands`` (copes with speed differences up to 2 %). Pass 2 resamples the original to that speed
    and refines each 8 s window by GCC-PHAT on the waveform (sub-sample). Where the room's processing scrambles the
    waveform so much that GCC-PHAT finds too few windows (a speakerphone), the band-feature line is used as it is
    (about 1-2 ms), and the acoustic delay is measured once over the whole match (``acoustic_lag``)."""
    bal = align_bands(rec, orig, log)
    try:
        al = _align_gcc(rec, orig, bal, log)
        al["summary"]["method"] = "band features, then GCC-PHAT per window"
    except RuntimeError as e:
        log(f"align: {e}; using the band-feature line")
        c0, c1 = bal["c0"], bal["c1"]
        x0 = place_original(orig, c0, c1, len(rec))
        m = bal["matched"]
        a, b = int(m[0]["t0"] * FS), int(m[-1]["t1"] * FS)
        lag = acoustic_lag(rec[a:b], x0[a:b])
        c0 = c0 - c1 * lag["lag_ms"] / 1000.0                   # the original as heard at the mic
        al = {"x_al": place_original(orig, c0, c1, len(rec)), "c0": c0, "c1": c1, "windows": bal["windows"],
              "matched": m,
              "summary": {"method": "band features (GCC-PHAT unusable here)", "offset_s": -c0 / c1,
                          "drift_ppm": (1.0 / c1 - 1.0) * 1e6, "windows": len(bal["windows"]),
                          "status": bal["summary"]["status"], "acoustic_lag": lag,
                          "residual_rms_ms": bal["summary"]["residual_rms_ms"],
                          "residual_max_ms": bal["summary"]["residual_max_ms"],
                          "off_line": [{"t_s": w["tc"], "off_s": w.get("off_line_s")} for w in bal["windows"]
                                       if w["status"] in ("off_line", "seek")],
                          "edit_like_windows": sum(1 for w in bal["windows"] if w["status"] == "seek")}}
    al["summary"]["speed"] = al["c1"]
    al["summary"]["speed_pct"] = (al["c1"] - 1.0) * 100.0
    al["bands"] = bal
    return al


def _align_gcc(rec: np.ndarray, orig: np.ndarray, bal: dict, log) -> dict:
    """Pass 2 of ``align``: GCC-PHAT per 8 s window on the original resampled to the band line's speed."""
    c0, c1 = bal["c0"], bal["c1"]
    m_b = bal["matched"]
    t_lo, t_hi = m_b[0]["t0"], m_b[-1]["t1"]
    _, lv = roomsim.frame_power(rec, int(0.1 * FS), int(0.05 * FS))
    lv = _db(lv)
    floor_guess = float(np.percentile(lv, 3)) if len(lv) else -120.0
    wins, cand = [], []
    for t0 in np.arange(0.0, max(0.0, len(rec) / FS - WIN_S) + 1e-9, HOP_S):
        w = {"t0": float(t0), "t1": float(t0 + WIN_S), "tc": float(t0 + WIN_S / 2)}
        seg_lv = lv[int(t0 * 20):int((t0 + WIN_S) * 20)]
        if t0 < t_lo - 1.0 or t0 + WIN_S > t_hi + 1.0:
            w["status"] = "outside"
        elif not len(seg_lv) or float(np.median(seg_lv)) < floor_guess + 6.0:
            w["status"] = "quiet"
        else:
            w["status"] = "candidate"
            cand.append(w)
        wins.append(w)
    if len(cand) < 2:
        raise RuntimeError(f"GCC-PHAT: only {len(cand)} windows inside the band match")

    # the original resampled to the band line's speed, +-8 ms GCC around it
    x_res = soxr.resample(orig, FS, FS / c1, quality="VHQ")     # x_res index n <-> original time n c1 / FS
    D0 = c0 * FS / c1                                         # x_al[m] = x_res[m + D(m)]
    pts = []
    for w in cand:
        if w["status"] != "candidate":
            continue
        s_y = int(round(w["t0"] * FS))
        ny = int(WIN_S * FS)
        m = int(0.01 * FS)
        s_x0 = s_y + int(round(D0)) - m
        if s_x0 < 0 or s_x0 + ny + 2 * m > len(x_res):
            w["status"] = "no_overlap"
            continue
        k, snr = gcc_phat(rec[s_y:s_y + ny], x_res[s_x0:s_x0 + ny + 2 * m], -m - int(0.008 * FS), -m + int(0.008 * FS))
        # y[n] ~ x_res[s_x0 + n - k] = x_res[(s_y + n) + D]  =>  D = s_x0 - s_y - k
        w["D"] = s_x0 - s_y - k
        w["phat2"] = snr
        if snr < MIN_PHAT_SNR:
            w["status"] = "no_match"
            continue
        pts.append(w)
    if len(pts) < max(3, int(0.3 * len(cand))):
        raise RuntimeError(f"GCC-PHAT found {len(pts)} of {len(cand)} windows (waveform too scrambled)")
    mc = np.array([w["tc"] * FS for w in pts])
    Dv = np.array([w["D"] for w in pts])
    fit2 = ransac_line(mc, Dv, np.array([w["phat2"] for w in pts]), tol=3.0, slope_range=(-1e-4, 1e-4))
    d0, d1, inl = fit2 if fit2 is not None else (float(np.median(Dv)), 0.0, np.ones(len(Dv), bool))
    res = Dv - (d0 + d1 * mc)
    mad = float(np.median(np.abs(res[inl] - np.median(res[inl])))) * 1.4826 if inl.sum() > 2 else 1.0
    tol = max(1.5, 5.0 * mad)
    inl = np.abs(res) < tol
    if inl.sum() >= 2:
        A = np.stack([np.ones(inl.sum()), mc[inl]], axis=1)
        (d0, d1), *_ = np.linalg.lstsq(A, Dv[inl], rcond=None)
        res = Dv - (d0 + d1 * mc)
    for w, ok, rv in zip(pts, inl, res):
        w["resid_ms"] = rv / FS * 1000.0 * c1
        w["status"] = "matched" if ok else "off_line"
        if not ok:
            w["off_line_s"] = rv / FS * c1
    matched = [w for w in pts if w["status"] == "matched"]
    # final map: u = (m + D(m)) c1 / FS with D = d0 + d1 m  ->  u = c0f + c1f t
    c1f = c1 * (1.0 + d1)
    c0f = d0 * c1 / FS
    drift_ppm = (1.0 / c1f - 1.0) * 1e6
    # smoothed residual (samples of x_res) at every matched window centre, running median of 5
    mt = np.array([w["tc"] * FS for w in matched])
    mr = np.array([w["D"] - (d0 + d1 * w["tc"] * FS) for w in matched])
    mr_s = ndi.median_filter(mr, size=min(5, len(mr)), mode="nearest") if len(mr) else mr
    # the original on the recording's timeline: 4 s pieces, each with its own (fractional) shift
    n = len(rec)
    x_al = np.zeros(n)
    piece = int(HOP_S * FS)
    pad = 4096
    for p0 in range(0, n, piece):
        p1 = min(n, p0 + piece)
        mcen = (p0 + p1) / 2.0
        D = d0 + d1 * mcen + (float(np.interp(mcen, mt, mr_s)) if len(mt) else 0.0)
        Di = int(math.floor(D))
        fr = D - Di
        a, b = p0 + Di - pad, p1 + Di + pad + 1
        if b <= 0 or a >= len(x_res):
            continue
        seg = np.zeros(b - a)
        sa, sb = max(a, 0), min(b, len(x_res))
        seg[sa - a:sb - a] = x_res[sa:sb]
        if abs(fr) > 1e-6:
            nf = sfft.next_fast_len(len(seg), real=True)
            S = np.fft.rfft(seg, nf) * np.exp(2j * np.pi * np.fft.rfftfreq(nf) * fr)   # advance by fr samples
            seg = np.fft.irfft(S, nf)[:len(seg)]
        x_al[p0:p1] = seg[pad:pad + (p1 - p0)]
    status = {}
    for w in wins:
        status[w["status"]] = status.get(w["status"], 0) + 1
    resid = np.array([w["resid_ms"] for w in matched]) if matched else np.zeros(0)
    log(f"align: {len(matched)} windows matched, offset {-c0f / c1f:+.4f} s, drift {drift_ppm:+.1f} ppm, "
        f"residual rms {float(np.sqrt(np.mean(resid ** 2))) if len(resid) else 0:.3f} ms")
    return {
        "x_al": x_al, "c0": c0f, "c1": c1f, "windows": wins, "matched": matched,
        "summary": {
            "offset_s": -c0f / c1f,          # recording time of the original's t = 0
            "drift_ppm": drift_ppm,          # + : the recording runs long (its clock slow vs the player's, or...)
            "band_line": {"c0": c0, "c1": c1},
            "windows": len(wins), "status": status,
            "residual_rms_ms": float(np.sqrt(np.mean(resid ** 2))) if len(resid) else None,
            "residual_max_ms": float(np.max(np.abs(resid))) if len(resid) else None,
            "phat_snr_median": float(np.median([w["phat2"] for w in matched])) if matched else None,
            "off_line": [{"t_s": w["tc"], "off_s": w.get("off_line_s")} for w in wins if w["status"] == "off_line"],
            "edit_like_windows": sum(1 for w in wins if w["status"] == "off_line" and
                                     abs(w.get("off_line_s") or 0.0) >= 0.05),
        },
    }


def shift_frac(x: np.ndarray, d: float) -> np.ndarray:
    """x delayed by d samples (fractional, FFT), same length."""
    nf = sfft.next_fast_len(len(x) + int(abs(d)) + 64, real=True)
    return np.fft.irfft(np.fft.rfft(x, nf) * np.exp(-2j * np.pi * np.fft.rfftfreq(nf) * d), nf)[:len(x)]


def align_from_run(rec: np.ndarray, orig: np.ndarray, run_dir: pathlib.Path, music: dict, log) -> dict:
    """run.json ``music`` (calibrate.py found and aligned the clean section: rec-music.wav and
    original/original-matched.wav line up sample for sample, the source's speed difference resampled away):
    the original on the recording's timeline is original-matched, delayed by the acoustic lag measured here
    (``acoustic_lag``), so x_al lines up with the direct sound. The map u = c0 + c1 t keeps the run's speed and puts
    the lag into c0 (u = original time heard at the mic at recording time t). ``align_bands`` runs too, as a
    cross-check of the run's map and to find seeks (the track started or sought before the clean section)."""
    files = music.get("files") or {}
    rm, _, _ = read_audio(run_dir / files.get("rec", "rec-music.wav"))
    om, _, _ = read_audio(run_dir / files.get("original", "original/original-matched.wav"))
    i0 = int(round(float(music["rec_t0"]) * FS))
    n = min(len(rm), len(om), len(rec) - i0)
    same = float(np.max(np.abs(rec[i0:i0 + n] - rm[:n]))) if n > 0 else math.inf
    if same > 1e-6:
        log(f"align: rec-music.wav differs from rec.wav at {music['rec_t0']} s (max {same:.2g}); using rec.wav")
    speed, c = float(music["speed"]), float(music["c"])
    t_play = (0.0 - c) / speed                               # recording time of the original's t = 0 (no lag)
    a = max(0, int(round(t_play * FS)) - i0)                  # the clean section, from where the track starts
    lag = acoustic_lag(rec[i0 + a:i0 + n], om[a:n])
    d = lag["lag_ms"] / 1000.0 * FS
    x_al = np.zeros(len(rec))
    x_al[i0:i0 + n] = shift_frac(om[:n], d)
    c0 = c - speed * lag["lag_ms"] / 1000.0
    log(f"align: run.json music section {music['rec_t0']:.3f}-{music['rec_t1']:.3f} s, speed {speed:.7f}, acoustic lag "
        f"{lag['lag_ms']:.2f} ms ({lag['method']}; GCC-PHAT {lag['gcc_ms']:.2f} ms at {lag['gcc_snr']:.1f} sigma, "
        f"band features {lag['bands_ms']:.2f} ms)")
    try:
        bal = align_bands(rec, orig, log)
    except RuntimeError as e:
        bal = {"windows": [], "matched": [], "c0": None, "c1": None, "summary": {"error": str(e)}}
    xc = None
    if bal.get("c1"):
        tm = (music["rec_t0"] + music["rec_t1"]) / 2
        xc = {"speed_diff_ppm": (bal["c1"] / speed - 1.0) * 1e6,
              "offset_diff_ms_mid": ((bal["c0"] + bal["c1"] * tm) - (c0 + speed * tm)) * 1000.0,
              "note": "band-feature line minus the run's map (lag included) at the section's middle; the band "
                      "features' own lag reads a few ms long through heavy mic processing"}
    win = {"t0": float(music["rec_t0"]), "t1": float(music["rec_t1"]),
           "tc": float(music["rec_t0"] + music["rec_t1"]) / 2, "status": "matched", "resid_ms": 0.0}
    return {"x_al": x_al, "c0": c0, "c1": speed, "windows": bal["windows"] or [win],
            "matched": [w for w in bal["windows"] if w["status"] == "matched"] or [win], "bands": bal,
            "summary": {"method": "run.json music section (calibrate.py's alignment) + acoustic lag",
                        "offset_s": -c0 / speed, "speed": speed, "speed_pct": (speed - 1.0) * 100.0,
                        "drift_ppm": (1.0 / speed - 1.0) * 1e6, "run_fit": music.get("fit"),
                        "speed_note": music.get("speed_note"), "acoustic_lag": lag,
                        "rec_music_matches_rec": bool(same <= 1e-6), "track_starts_s": t_play + lag["lag_ms"] / 1000,
                        "band_cross_check": xc, "band_summary": bal["summary"],
                        "windows": len(bal["windows"]), "status": (bal["summary"] or {}).get("status"),
                        "residual_rms_ms": (music.get("fit") or {}).get("residual_ms_median"),
                        "off_line": [], "edit_like_windows": 0}}


# ======================================================================================================================
# 2. regions, noise and levels
# ======================================================================================================================
def find_regions(rec: np.ndarray, al: dict, orig_len_s: float) -> dict:
    """Music start/end in the recording (its 100 ms level against the room tone), pre-roll and post-roll."""
    hop_s = 0.05
    c, p = roomsim.frame_power(rec, int(0.1 * FS), int(hop_s * FS))
    lv = _db(p)
    tt = c / FS
    m = al["matched"]
    pre = lv[(tt > 0.2) & (tt < m[0]["t0"] - 1.0)]
    floor = float(np.median(pre)) if len(pre) >= 20 else float(np.percentile(lv, 5))
    above = lv > floor + 6.0
    # walk back from the first matched window's centre while the level stays up (gaps under 0.3 s bridged)
    i = int(np.searchsorted(tt, m[0]["tc"]))
    gap = 0
    j = i
    while j > 0:
        j -= 1
        gap = 0 if above[j] else gap + 1
        if gap > int(0.3 / hop_s):
            j += gap
            break
    else:
        j = 0
    t_u0 = al["offset_s"] if "offset_s" in al else -al["c0"] / al["c1"]
    start = max(float(tt[min(j, len(tt) - 1)]) - 0.05, t_u0 - 0.05, 0.0)
    # walk on from the last matched window: the original may run on past what was played, and the room's tail is
    # not the music, so the end is where the level drops 25 dB under the last window's (or to the floor + 6 dB)
    i = int(np.searchsorted(tt, m[-1]["tc"]))
    last_lv = float(np.median(lv[(tt >= m[-1]["t0"]) & (tt <= m[-1]["t1"])])) if len(lv) else floor
    above_end = lv > max(floor + 6.0, last_lv - 25.0)
    gap = 0
    j = i
    while j < len(tt) - 1:
        j += 1
        gap = 0 if above_end[j] else gap + 1
        if gap > int(0.3 / hop_s):
            j -= gap
            break
    t_uend = t_u0 + orig_len_s / al["c1"] * 1.0
    end = min(float(tt[min(j, len(tt) - 1)]) + 0.05, t_uend + 2.0, len(rec) / FS)
    return {"music_start_s": start, "music_end_s": end, "floor_guess_dbfs": floor,
            "pre_roll": [0.2, max(0.2, start - 0.3)], "post_roll": [min(len(rec) / FS, end + 0.5), len(rec) / FS - 0.05]}


def _hum(f: np.ndarray, psd: np.ndarray) -> tuple[dict, float]:
    hum = {}
    for base in (50.0, 60.0):
        fam = []
        for h in range(1, 6):
            fh = base * h
            pk = psd[(f > fh - 1.5) & (f < fh + 1.5)]
            loc = psd[((f > fh - 15) & (f < fh - 3)) | ((f > fh + 3) & (f < fh + 15))]
            if len(pk) and len(loc):
                fam.append({"hz": fh, "above_floor_db": float(_db(pk.max()) - _db(np.median(loc))),
                            "level_dbfs": float(_db(pk.max() * (f[1] - f[0])))})
        hum[f"{int(base)}hz"] = fam
    return hum, max((h["above_floor_db"] for fam in hum.values() for h in fam), default=0.0)


def _join_loop(pieces: list[np.ndarray], max_s: float) -> np.ndarray:
    """Pieces joined with 50 ms crossfades, cut to max_s, the end crossfaded into the start (loopable)."""
    fade = int(0.05 * FS)
    clip = pieces[0]
    for pc in pieces[1:]:
        if len(pc) <= 2 * fade or len(clip) <= 2 * fade:
            clip = np.concatenate([clip, pc])
            continue
        r = np.linspace(0, 1, fade)
        clip = np.concatenate([clip[:-fade], clip[-fade:] * (1 - r) + pc[:fade] * r, pc[fade:]])
    clip = clip[:int(max_s * FS)]
    if len(clip) > 4 * fade:
        r = np.linspace(0, 1, fade)
        loop = clip[fade:].copy()
        loop[-fade:] = clip[-fade:] * (1 - r) + clip[:fade] * r
        clip = loop
    return clip


def measure_gate(lv10: np.ndarray) -> dict | None:
    """A mic that gates quiet sound (a speakerphone) delivers near digital silence (10 ms frames under GATED_DBFS)
    between sounds. From the room-sound bed's 10 ms levels: the threshold (the quietest 5 % of open frames: the
    quietest sound it lets through), the hold (median frames under that threshold just before a closure) and the
    depth (the threshold down to the median closed level). None when it never closes."""
    closed = lv10 < GATED_DBFS
    runs = [(a, b) for a, b in runs_of(closed) if b - a >= 3]
    if len(runs) < 5 or (~closed).sum() < 100:
        return None
    thr = float(np.percentile(lv10[~closed], 5))
    holds = []
    for a, _ in runs:
        k = 0
        while a - k - 1 >= 0 and not closed[a - k - 1] and lv10[a - k - 1] < thr + 6.0:
            k += 1
        holds.append(k)
    return {"thr_dbfs": thr, "hold_ms": float(max(10.0, np.median(holds) * 10.0)),
            "depth_db": float(max(0.0, thr - np.median(lv10[closed]))), "closures": len(runs),
            "closed_share": float(closed.mean())}


def analyse_room_sound(rec: np.ndarray, reg: dict, seek_spans, music_spans) -> dict:
    """What the mic delivers when no music plays, from the recording outside the music (and outside any stretch
    where the track was started or sought: ``seek_spans``):
      bed     everything there, events included (typing, talk, a kettle: it is the room's real sound, and onsets in
              it are what the beat stack has to ignore) -> noise.wav for the model (looped, at its own level);
      floor   the quiet parts: 10 ms frames, gated ones (under GATED_DBFS: the mic's own gate) apart; the quietest
              5 % of the open ones, the quietest 1 s, and their spectrum (steady tone, mains hum);
      gate    measure_gate on the bed."""
    n = len(rec)
    ms, me = reg["music_start_s"], reg["music_end_s"]
    first_seek = min([a for a, _ in seek_spans] + [ms])
    spans = [(0.2, first_seek - BED_GUARD_S), (me + 1.0, n / FS - 0.05)]
    keep = []
    for a, b in spans:
        cur = a
        for sa, sb in sorted(seek_spans) + sorted(music_spans):
            if sb + 1.0 <= cur or sa - 1.0 >= b:
                continue
            if sa - 1.0 > cur:
                keep.append((cur, sa - 1.0))
            cur = max(cur, sb + 1.0)
        if b > cur:
            keep.append((cur, b))
    keep = [(a, b) for a, b in keep if b - a >= 1.0]
    source = "outside the music" + (" (seeks left out)" if seek_spans else "")
    if not keep:                                       # no room sound recorded: the quietest 5 % of 100 ms frames
        source = "quietest frames"
        _, p = roomsim.frame_power(rec, int(0.1 * FS), int(0.05 * FS))
        lv = _db(p)
        thr = np.percentile(lv, 5)
        keep = [(r0 * 0.05, r1 * 0.05 + 0.05) for r0, r1 in runs_of(lv <= thr) if r1 - r0 >= 6] or \
               [(float(np.argmin(lv)) * 0.05, float(np.argmin(lv)) * 0.05 + 0.1)]
    pieces = [rec[int(a * FS):int(b * FS)] for a, b in keep]
    x = np.concatenate(pieces)
    p_bed = float(np.mean(x ** 2))
    _, p10 = roomsim.frame_power(x, 480, 480)
    lv10 = _db(p10)
    gated = lv10 < GATED_DBFS
    open_lv = lv10[~gated] if (~gated).any() else lv10
    floor = float(np.percentile(open_lv, 5))
    c1s, p1s = roomsim.frame_power(x, FS, FS // 2)
    quiet1 = float(_db(np.min(p1s))) if len(p1s) else None
    # the steady floor: runs of open 10 ms frames within the quietest fifth, at least 100 ms
    q20 = float(np.percentile(open_lv, 20))
    fl_runs = [(a, b) for a, b in runs_of((lv10 <= q20) & ~gated) if b - a >= 10]
    x_floor = np.concatenate([x[a * 480:b * 480] for a, b in fl_runs]) if fl_runs else x
    nper = min(16384, 2 ** int(math.floor(math.log2(max(256, len(x))))))
    f, psd = sps.welch(x, FS, window="hann", nperseg=nper, noverlap=nper // 2)
    npf = min(8192, 2 ** int(math.floor(math.log2(max(256, len(x_floor))))))
    ff, psd_fl = sps.welch(x_floor, FS, window="hann", nperseg=npf, noverlap=npf // 2)
    cen = third_octave_centres()
    hum, hum_peak = _hum(ff, psd_fl)
    clip = _join_loop(pieces, BED_MAX_S)
    st_t, st_v = short_term(x, [(0.0, len(x) / FS)])
    return {"p_noise": p_bed, "x": x, "clip": clip, "f": f, "psd": psd, "nperseg": nper,
            "gate": measure_gate(lv10),
            "summary": {"source": source, "spans_s": keep, "seconds": len(x) / FS,
                        "rms_dbfs": float(_db(p_bed)), "lufs": _f(roomsim.lufs(x), 2),
                        "short_term_lufs_p10_50_90": [float(v) for v in np.percentile(st_v, [10, 50, 90])]
                        if len(st_v) else None,
                        "gated_share": float(gated.mean()), "floor_dbfs": floor, "quietest_1s_dbfs": quiet1,
                        "third_octave_hz": cen, "third_octave_dbfs": _db(band_sum(f, psd, cen)),
                        "floor_third_octave_dbfs": _db(band_sum(ff, psd_fl, cen)),
                        "hum": hum, "hum_detected": bool(hum_peak >= 10.0), "hum_peak_db": hum_peak,
                        "noise_wav_s": len(clip) / FS}}


def short_term(x: np.ndarray, spans) -> tuple[np.ndarray, np.ndarray]:
    """3 s K-weighted loudness every 1 s inside spans: (centre s, LUFS)."""
    z = roomsim.k_weight(x) ** 2
    c = np.concatenate([[0.0], np.cumsum(z)])
    ts, vs = [], []
    for a, b in spans:
        for s in range(int(a * FS), int(b * FS) - 3 * FS + 1, FS):
            ts.append(s / FS + 1.5)
            vs.append(-0.691 + 10 * math.log10((c[s + 3 * FS] - c[s]) / (3 * FS) + EPS))
    return np.asarray(ts), np.asarray(vs)


def analyse_levels(rec, x_played, reg, noise, spans) -> dict:
    a, b = int(reg["music_start_s"] * FS), int(reg["music_end_s"] * FS)
    y = rec[a:b]
    p_rec = float(np.mean(y ** 2))
    pn = noise["p_noise"]
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    clip = int(np.sum(np.abs(rec) >= 32766.0 / 32768.0))
    near = int(np.sum(np.abs(rec) >= 0.98))
    xo = x_played[a:b]
    l_rec, l_org = roomsim.lufs(y), roomsim.lufs(x_played)
    t1, s_rec = short_term(rec, spans)
    _, s_org = short_term(x_played, spans)
    d = s_rec - s_org
    ok = np.isfinite(d) & (s_org > -60)
    stab = {}
    if ok.sum() >= 5:
        dd = d[ok]
        rng_o = float(np.percentile(s_org[ok], 95) - np.percentile(s_org[ok], 5))
        slope = float(np.polyfit(s_org[ok] - s_org[ok].mean(), s_rec[ok], 1)[0]) if ok.sum() > 5 else None
        stab = {"st_diff_std_db": float(dd.std()), "st_diff_p5_db": float(np.percentile(dd, 5)),
                "st_diff_p95_db": float(np.percentile(dd, 95)), "orig_st_range_db": rng_o,
                "st_out_vs_in_slope": slope, "st_out_vs_in_r": _pearson(s_org[ok], s_rec[ok]),
                "informative": bool(rng_o >= 4.0),
                "note": "3 s loudness, recording against the original: slope 1 passes the track's loudness changes, "
                        "0 levels them flat (AGC); only informative when the original's own range is >= 4 dB"}
    return {
        "rec_music": {"rms_dbfs": float(_db(p_rec)), "lufs": _f(l_rec, 2), "peak_dbfs": float(_db(peak ** 2)),
                      "crest_db": float(_db(peak ** 2) - _db(p_rec)), "clipped_samples": clip,
                      "near_full_scale_samples": near},
        "original_as_played": {"lufs": _f(l_org, 2), "rms_dbfs": float(_db(np.mean(xo ** 2))),
                               "peak_dbfs": float(_db(np.max(np.abs(xo)) ** 2)) if len(xo) else None},
        "system_gain_db": _f(l_rec - l_org, 2),
        "snr_db": float(_db(max(p_rec - pn, EPS)) - _db(pn)),
        "stability": stab,
        "_st": (t1, s_rec, s_org),
    }


def loud_blocks(x: np.ndarray, a_s: float, b_s: float, win_s: float = 1.0, hop_s: float = 0.5):
    """K-weighted loudness (LUFS) of win_s blocks every hop_s in [a_s, b_s): (block centres s, LUFS)."""
    z = roomsim.k_weight(x[int(a_s * FS):int(b_s * FS)]) ** 2
    c = np.concatenate([[0.0], np.cumsum(z)])
    w, h = int(win_s * FS), int(hop_s * FS)
    st = np.arange(0, len(z) - w + 1, h)
    return a_s + (st + w / 2) / FS, -0.691 + 10 * np.log10((c[st + w] - c[st]) / w + EPS)


def music_events(rec: np.ndarray, x_played: np.ndarray, spans) -> dict:
    """1 s loudness blocks (0.5 s hop) of the recording against the original as played: their median difference is
    the chain's gain; blocks 6 dB or more over it carry other sound (talk, a phone, a kettle); blocks 15 dB or more
    under it while the music plays are dropouts (the mic shut the music out). Each merged, padded 0.5 s. ->
    {"gain_db_median", "interference_s": [[a, b]...], "dropouts_s": [...], "clean_s": spans minus both}."""
    tt, dd, lo = [], [], []
    for a, b in spans:
        if b - a < 2.0:
            continue
        t, lr = loud_blocks(rec, a, b)
        _, lo_ = loud_blocks(x_played, a, b)
        tt.append(t)
        dd.append(lr - lo_)
        lo.append(lo_)
    if not tt:
        return {"gain_db_median": None, "interference_s": [], "dropouts_s": [], "clean_s": list(spans)}
    t, d, lo = np.concatenate(tt), np.concatenate(dd), np.concatenate(lo)
    playing = lo > -50.0
    med = float(np.median(d[playing])) if playing.any() else float(np.median(d))

    def merged(mask):
        out = []
        for i in np.flatnonzero(mask):
            a, b = t[i] - 0.5 - 0.5, t[i] + 0.5 + 0.5
            if out and a <= out[-1][1]:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        return out

    interf = merged((d >= med + 6.0) & playing)
    drop = merged((d <= med - 15.0) & playing)
    clean = []
    for a, b in spans:
        cur = a
        for ea, eb in sorted(interf + drop):
            if eb <= cur or ea >= b:
                continue
            if ea > cur:
                clean.append((cur, ea))
            cur = max(cur, eb)
        if b > cur:
            clean.append((cur, b))
    clean = [(a, b) for a, b in clean if b - a >= 1.0]
    return {"gain_db_median": med, "block_gain_db_p10_90": [float(v) for v in np.percentile(d[playing], [10, 90])]
            if playing.any() else None,
            "interference_s": interf, "dropouts_s": drop, "clean_s": clean,
            "dropout_seconds": float(sum(b - a for a, b in drop)),
            "dropout_depth_db": float(np.median(med - d[(d <= med - 15.0) & playing])) if drop else None,
            "interference_seconds": float(sum(b - a for a, b in interf)),
            "_blocks": (t, d)}


def music_loudness(y: np.ndarray, spans) -> dict:
    """What dsp's gate reads: short-term (3 s) loudness every 0.5 s inside spans, its median (``music_lufs``) and
    spread, and the integrated loudness of the spans joined."""
    vs = []
    for a, b in spans:
        if b - a >= 3.0:
            vs.append(loud_blocks(y, a, b, 3.0, 0.5)[1])
    v = np.concatenate(vs) if vs else np.zeros(0)
    joined = np.concatenate([y[int(a * FS):int(b * FS)] for a, b in spans]) if spans else np.zeros(0)
    return {"short_term_median_lufs": float(np.median(v)) if len(v) else None,
            "short_term_p10_lufs": float(np.percentile(v, 10)) if len(v) else None,
            "short_term_p90_lufs": float(np.percentile(v, 90)) if len(v) else None,
            "integrated_lufs": _f(roomsim.lufs(joined), 2) if len(joined) else None}


def power_response(rec: np.ndarray, x_al: np.ndarray, spans_smp, noise: dict) -> dict:
    """The chain's magnitude response from power spectra (music only: the room-sound bed's spectrum taken off the
    recording's), 1/6-octave smoothed on 1/24-octave centres: robust where the mic's processing scrambles the
    waveform and H1 (cross-spectrum) reads low. Band edges: where the response falls 6 and 20 dB under its
    300 Hz-3 kHz median, walking out from there."""
    nper = 16384
    acc_y = acc_x = None
    cnt = 0
    for a, b in spans_smp:
        if b - a < nper:
            continue
        f, py = sps.welch(rec[a:b], FS, nperseg=nper, noverlap=nper // 2)
        _, px = sps.welch(x_al[a:b], FS, nperseg=nper, noverlap=nper // 2)
        k = b - a
        acc_y = py * k if acc_y is None else acc_y + py * k
        acc_x = px * k if acc_x is None else acc_x + px * k
        cnt += k
    if not cnt:
        return {}
    Pyy, Pxx = acc_y / cnt, acc_x / cnt
    Pbb = np.interp(f, noise["f"], noise["psd"])
    centres = 20.0 * 2 ** (np.arange(0, 241) / 24.0)
    centres = centres[centres <= 20000.0]
    sy, sx, sb = (frac_oct_smooth(f, v, centres) for v in (Pyy, Pxx, Pbb))
    sm = np.maximum(sy - sb, 0.05 * sy)
    mag = _db(sm) - _db(sx)
    excited = _db(sx) > _db(sx).max() - 70.0                 # where the original has something to measure with
    ref = float(np.median(mag[(centres >= 300) & (centres <= 3000)]))
    shape = mag - ref

    def edge(lo_side: bool, drop: float):
        idx = np.flatnonzero((centres <= 300) if lo_side else (centres >= 3000))
        idx = idx[::-1] if lo_side else idx
        for i in idx:
            if not excited[i]:
                return None
            if shape[i] < -drop:
                return float(centres[i])
        return None

    e = {"low_6db_hz": edge(True, 6), "low_20db_hz": edge(True, 20), "high_6db_hz": edge(False, 6),
         "high_20db_hz": edge(False, 20)}
    for side in ("low", "high"):
        a6, a20 = e[f"{side}_6db_hz"], e[f"{side}_20db_hz"]
        e[f"{side}_slope_db_per_oct"] = (14.0 / abs(math.log2(a20 / a6))) if a6 and a20 and a6 != a20 else None
    toc = third_octave_centres(50, 12500)
    return {"centres": centres, "mag_db": mag, "excited": excited, "gain_db_300_3k": ref,
            "music_bed_ratio_db_300_3k": float(np.median((_db(sy) - _db(sb))[(centres >= 300) & (centres <= 3000)])),
            "third_oct_hz": toc, "third_oct_db": np.interp(np.log(toc), np.log(centres), mag),
            **e}


def split_response(f: np.ndarray, mag: np.ndarray) -> dict:
    """The measured response = the passband's shape (room, PA, mic capsule: before the mic's processing) x the
    band limits (a speakerphone's own filters, after its processing). A band limit is a cliff: the steepest slope
    under 300 Hz (rising) or over 2 kHz (falling), steeper than EDGE_SLOPE dB per octave, with the response 10 dB or
    more lower beyond it. Its edge is where the response, walking from the cliff into the passband, comes within
    3 dB of the passband's level next to it (the median over the half octave further in). Beyond an edge the
    passband part holds the edge's level; the band-limit part is the rest (0 dB inside)."""
    lf = np.log2(f)
    slope = np.gradient(mag, lf)
    pre = mag.copy()
    f_lo = f_hi = None
    lo_side = np.flatnonzero(f <= 300)
    if len(lo_side):
        i = int(lo_side[np.argmax(slope[lo_side])])
        if slope[i] > EDGE_SLOPE and mag[i:].max() - mag[:i + 1].min() >= 10.0:
            j = i
            while j < len(f) - 1 and f[j] < 300:
                ref = float(np.median(mag[(lf > lf[j]) & (lf <= lf[j] + 0.5)]))
                if mag[j] >= ref - 3.0:
                    break
                j += 1
            f_lo = float(f[j])
            pre[:j] = mag[j]
    hi_side = np.flatnonzero(f >= 2000)
    if len(hi_side):
        i = int(hi_side[np.argmin(slope[hi_side])])
        if slope[i] < -EDGE_SLOPE and mag[:i + 1].max() - mag[i:].min() >= 10.0:
            j = i
            while j > 0 and f[j] > 2000:
                ref = float(np.median(mag[(lf < lf[j]) & (lf >= lf[j] - 0.5)]))
                if mag[j] >= ref - 3.0:
                    break
                j -= 1
            f_hi = float(f[j])
            pre[j + 1:] = mag[j]
    return {"pre_db": pre, "post_db": mag - pre, "lo_hz": f_lo, "hi_hz": f_hi}


# ======================================================================================================================
# 3. linear chain: H1, coherence, IR, decay
# ======================================================================================================================
def hann_autocorr(n: int) -> np.ndarray:
    w = sps.get_window("hann", n)
    r = np.fft.irfft(np.abs(np.fft.rfft(w, 2 * n)) ** 2, 2 * n)[:n]
    return r / r[0]


def frac_oct_smooth(f, v, centres, frac: float = 6.0) -> np.ndarray:
    out = np.empty(len(centres))
    for i, c in enumerate(centres):
        sel = (f >= c * 2 ** (-0.5 / frac)) & (f < c * 2 ** (0.5 / frac))
        out[i] = v[sel].mean() if sel.any() else v[np.argmin(np.abs(f - c))]
    return out


def edc_metrics(h: np.ndarray, noise_pw_fn, pre: int, fs: int = FS) -> dict:
    """Schroeder decay of one IR (pre = index of the alignment point): direct-sound index, truncation where the
    10 ms envelope meets the noise floor + 3 dB, noise-subtracted backward integral, EDT/T20/T30 with their validity,
    DRR, C50, C80, peak-to-noise."""
    e = h ** 2
    win = int(0.01 * fs)
    sm = ndi.uniform_filter1d(e, win)
    lo = max(0, pre - int(0.005 * fs))
    i_pk = lo + int(np.argmax(e[lo:pre + int(0.05 * fs)]))
    nz = noise_pw_fn(np.arange(len(e)))
    snr_db = float(_db(sm[i_pk]) - _db(nz[i_pk]))
    tp = len(e) - 1
    cross = np.flatnonzero((sm[i_pk:] < 2.0 * nz[i_pk:]))
    if len(cross):
        tp = i_pk + int(cross[0])
    tp = max(tp, i_pk + int(0.02 * fs))
    seg = np.maximum(e[i_pk:tp] - nz[i_pk:tp], 0.0)
    edc = np.cumsum(seg[::-1])[::-1]
    edc_db = _db(edc / (edc[0] + EPS))
    t = np.arange(len(edc_db)) / fs
    rng_db = float(-edc_db[-max(1, int(0.005 * fs))]) if len(edc_db) > 10 else 0.0

    def fit(top, bot):
        sel = (edc_db <= top) & (edc_db >= bot)
        if sel.sum() < int(0.01 * fs) or edc_db.min() > bot:
            return None
        k = np.polyfit(t[sel], edc_db[sel], 1)[0]
        return float(-60.0 / k) if k < 0 else None

    edt, t20, t30 = fit(0.0, -10.0), fit(-5.0, -25.0), fit(-5.0, -35.0)
    dw = int(0.0025 * fs)
    d0 = max(0, i_pk - int(0.001 * fs))
    direct = float(np.sum(e[d0:i_pk + dw]))
    rev = float(np.sum(seg[dw:]))
    c50 = float(np.sum(seg[:int(0.05 * fs)])) + direct - float(np.sum(seg[:dw]))
    late50 = float(np.sum(seg[int(0.05 * fs):]))
    c80e = float(np.sum(seg[:int(0.08 * fs)])) + direct - float(np.sum(seg[:dw]))
    late80 = float(np.sum(seg[int(0.08 * fs):]))
    return {"direct_ms": (i_pk - pre) / fs * 1000.0, "peak_to_noise_db": snr_db, "decay_range_db": rng_db,
            "truncation_ms": (tp - i_pk) / fs * 1000.0,
            "edt_s": edt if rng_db >= 20 else None, "t20_s": t20 if rng_db >= 35 else None,
            "t30_s": t30 if rng_db >= 45 else None,
            "drr_db": float(_db(direct) - _db(rev)) if rev > 0 else None,
            "c50_db": float(_db(c50) - _db(late50)) if late50 > 0 else None,
            "c80_db": float(_db(c80e) - _db(late80)) if late80 > 0 else None,
            "_edc": (t, edc_db), "_i_pk": i_pk, "_tp": tp}


def analyse_linear(x_al: np.ndarray, rec: np.ndarray, spans_smp, log) -> dict:
    N = H_NFFT
    total = sum(max(0, b - a - N) // (N // 4) + 1 for a, b in spans_smp if b - a >= N)
    if total < 12:
        N = 2 ** 15
    w = sps.get_window("hann", N)
    Sxx = np.zeros(N // 2 + 1)
    Syy = np.zeros(N // 2 + 1)
    Sxy = np.zeros(N // 2 + 1, dtype=np.complex128)
    nseg = 0
    for a, b in spans_smp:
        for s in range(a, b - N + 1, N // 4):
            X = np.fft.rfft(x_al[s:s + N] * w)
            Y = np.fft.rfft(rec[s:s + N] * w)
            Sxx += np.abs(X) ** 2
            Syy += np.abs(Y) ** 2
            Sxy += np.conj(X) * Y
            nseg += 1
    if nseg == 0:
        raise RuntimeError("no matched stretch is long enough for the response estimate")
    f = np.fft.rfftfreq(N, 1.0 / FS)
    coh = np.abs(Sxy) ** 2 / (Sxx * Syy + EPS)
    H1 = Sxy / (Sxx + EPS)
    log(f"linear: {nseg} segments of {N} samples")
    centres = 20.0 * 2 ** (np.arange(0, 241) / 24.0)
    centres = centres[centres <= 20000.0]
    # coherence-weighted 1/6-octave smoothing: bins the input never excited (above a codec's cut-off) don't count
    coh_s = frac_oct_smooth(f, coh, centres)
    mag_db = _db(frac_oct_smooth(f, coh * np.abs(H1) ** 2, centres) / np.maximum(coh_s, 1e-6))
    ref = float(np.median(mag_db[(centres >= 300) & (centres <= 3000)]))
    shape = mag_db - ref
    rel = coh_s >= 0.2

    def corner(lo_side: bool, drop: float):
        idx = np.flatnonzero((centres <= 300) if lo_side else (centres >= 3000))
        idx = idx[::-1] if lo_side else idx
        for i in idx:
            if shape[i] < -drop:
                return float(centres[i])
        return None

    sel = (centres >= 200) & (centres <= 8000) & rel
    tilt = float(np.polyfit(np.log2(centres[sel]), shape[sel], 1)[0]) if sel.sum() > 5 else None
    toc = third_octave_centres(50, 12500)
    toc_shape = np.interp(np.log(toc), np.log(centres), shape)
    feats = [{"hz": float(c), "db": float(v)} for c, v in zip(toc, toc_shape) if abs(v) >= 4.0 and 100 <= c <= 8000]
    # group delay: weighted slope of the unwrapped phase, 200 Hz - 4 kHz
    gsel = (f >= 200) & (f <= 4000)
    ph = np.unwrap(np.angle(H1[gsel]))
    gd = float(-np.polyfit(2 * np.pi * f[gsel], ph, 1, w=np.sqrt(coh[gsel] + 1e-6))[0]) * 1000.0

    # IRs: regularised deconvolution; the Wiener (coherence-weighted) one for the decay, the plain one for the model
    band = (f > 50) & (f < 10000)
    lam = 1e-4 * float(np.median(Sxx[band]))
    Hreg = Sxy / (Sxx + lam)
    coh_lin = np.interp(np.log(np.maximum(f, 1.0)), np.log(centres), coh_s)
    pre = int(IR_PRE_S * FS)
    rw = hann_autocorr(N)
    corr = np.ones(N)
    amax = int(0.4 * N)
    corr[:amax] = 1.0 / np.maximum(rw[:amax], 0.3)
    corr[amax:N // 2] = 0.0                           # beyond 0.4 of a segment: not estimable here
    corr[N // 2:] = 0.0

    def make_ir(H):
        h = np.fft.irfft(H, N)
        hr = np.roll(h, pre)                          # index pre = the alignment point; pre-ringing before it
        raw = hr.copy()
        lagi = np.arange(N) - pre
        cc = np.where(lagi >= 0, corr[np.clip(lagi, 0, N - 1)], 1.0)
        return hr * cc, raw, cc

    h_w, raw_w, cc = make_ir(Hreg * np.clip(coh_lin, 0.0, 1.0))
    # The model's response: measured between the first and last 1/6-octave bands with coherence >= 0.3; beyond them
    # (no excitation, e.g. above the original's codec cut-off) the edge band's level carried on flat (never above the
    # 300 Hz-3 kHz level), zero phase at the direct sound, crossfaded over 1/6 octave. An unexcited band never gets
    # H1's noise-over-nothing gain into the simulation.
    good = np.flatnonzero(coh_s >= 0.3)
    if len(good):
        f_lo, f_hi = centres[good[0]], centres[good[-1]]
        cap = float(np.median(mag_db[(centres >= 300) & (centres <= 3000)]))
        ext = np.interp(np.log(np.maximum(f, 1.0)), np.log(centres[good[0]:good[-1] + 1]),
                        np.minimum(mag_db[good[0]:good[-1] + 1], cap))
        lf = np.log2(np.maximum(f, 1.0))
        wlo = np.clip((lf - (math.log2(f_lo) - 1 / 6)) / (1 / 6), 0, 1)
        whi = np.clip(((math.log2(f_hi) + 1 / 6) - lf) / (1 / 6), 0, 1)
        wm = 0.5 - 0.5 * np.cos(np.pi * np.minimum(wlo, whi))
        Hmod = wm * Hreg + (1 - wm) * 10 ** (ext / 20.0)
    else:
        f_lo = f_hi = None
        Hmod = Hreg
    h_m, raw_m, _ = make_ir(Hmod)

    def noise_fn(raw):
        # The estimate's noise floor: the larger of the negative lags (-N/2 .. -50 ms: nothing causal lives there) and
        # the late positive lags (0.85-1.0 s, uncorrected: past any domestic decay). A compressor/AGC's errors are
        # causal (the gain follows the past input), so they only show at positive lags. In the corrected IR the floor
        # is scaled by the same correction, cc^2.
        neg = raw[N // 2:N - int(0.05 * FS)]
        pos = raw[pre + int(0.85 * FS):pre + int(min(1.0 * FS, 0.45 * N))]
        pw = max(float(np.mean(neg ** 2)) if len(neg) else EPS, float(np.mean(pos ** 2)) if len(pos) else EPS)
        return lambda idx: pw * cc[np.clip(idx, 0, N - 1)] ** 2

    lim = int(min(IR_KEEP_MAX_S * FS, 0.4 * N)) + pre
    m_w = edc_metrics(h_w[:lim], noise_fn(raw_w), pre)
    m_m = edc_metrics(h_m[:lim], noise_fn(raw_m), pre)
    bands = {}
    for fc in (125, 250, 500, 1000, 2000, 4000, 8000):
        sos = sps.butter(3, [fc / math.sqrt(2), min(fc * math.sqrt(2), 0.45 * FS)], btype="bandpass", fs=FS, output="sos")
        hb = sps.sosfilt(sos, h_w)
        rb = sps.sosfilt(sos, raw_w)
        mb = edc_metrics(hb[:lim], noise_fn(rb), pre)
        bands[str(fc)] = {k: v for k, v in mb.items() if not k.startswith("_")}
    # the model's IR: plain deconvolution, truncated where it meets its own noise, 20 ms fade
    tpm = min(m_m["_tp"] + int(0.02 * FS), lim)
    ir_model = h_m[:tpm].copy()
    fade = min(int(0.02 * FS), len(ir_model) // 4)
    ir_model[-fade:] *= 0.5 + 0.5 * np.cos(np.linspace(0, np.pi, fade))
    med_coh = float(np.median(coh[(f >= 200) & (f <= 4000)]))
    reliable = bool(m_m["peak_to_noise_db"] >= 20.0 and med_coh >= 0.2)
    rt = m_w["t30_s"] or m_w["t20_s"] or (m_w["edt_s"] if m_w["edt_s"] else None)
    return {
        "f": f, "coh": coh, "H1": H1, "centres": centres, "mag_db": mag_db, "coh_s": coh_s, "nseg": nseg, "N": N,
        "h_w": h_w, "m_w": m_w, "ir_model": ir_model, "pre": pre, "reliable": reliable, "rt60": rt,
        "response": {"segments": nseg, "nfft": N, "gain_db_300_3k": ref, "low_6db_hz": corner(True, 6),
                     "low_12db_hz": corner(True, 12), "high_6db_hz": corner(False, 6),
                     "high_12db_hz": corner(False, 12), "tilt_db_per_oct": tilt, "features_third_oct": feats,
                     "coherence_median_200_4k": med_coh, "measured_band_hz": [f_lo, f_hi],
                     "coherence_median_bands": {str(c): float(np.median(coh[(f >= c / 1.41) & (f < c * 1.41)]))
                                                for c in (63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)},
                     "group_delay_ms": gd},
        "ir": {"broadband": {k: v for k, v in m_w.items() if not k.startswith("_")},
               "octaves": bands, "model_ir_ms": len(ir_model) / FS * 1000.0, "model_ir_peak_to_noise_db":
               m_m["peak_to_noise_db"], "reliable": reliable,
               "caveats": "one source position and one mic; music, not a sweep, excites it; the PA's own response "
                          "and any mic processing are in it; T30/T20/EDT are reported only when the decay range "
                          "allows (45/35/20 dB)."},
    }


# ======================================================================================================================
# 4. dynamics
# ======================================================================================================================
def _movavg(v: np.ndarray, ok: np.ndarray, n: int) -> np.ndarray:
    num = ndi.uniform_filter1d(np.where(ok, v, 0.0), n, mode="nearest")
    den = ndi.uniform_filter1d(ok.astype(np.float64), n, mode="nearest")
    out = num / np.maximum(den, 1e-9)
    out[den < 0.3] = np.nan
    return out


def gain_track(dyn: dict, n: int) -> np.ndarray:
    """The measured gain trajectory as a per-sample linear factor: frame gains (dB) where measured, interpolated
    across the rest, 100 ms average, +-20 dB at most."""
    t, g, ok = dyn["t"], dyn["g"], dyn["ok"]
    gi = np.interp(t, t[ok], g[ok])
    gi = ndi.uniform_filter1d(np.clip(gi, -20.0, 20.0), 4, mode="nearest")
    return 10 ** (np.interp(np.arange(n), t * FS, gi) / 20.0)


def fit_level_curve(Lp_all: np.ndarray, g: np.ndarray, ok: np.ndarray) -> dict:
    """Grid over attack/release, threshold by grid with (makeup, slope) by least squares; then an expander hinge."""
    hop_s = FHOP / FS
    Lp_c = np.maximum(Lp_all, -100.0)
    gv = g[ok]
    sst = float(np.sum((gv - gv.mean()) ** 2))
    best = None
    for att in (0.005, 0.015, 0.05, 0.15, 0.5, 1.5, 5.0):
        for rel in (0.05, 0.15, 0.5, 1.5, 5.0):
            if rel < att:
                continue
            det = roomsim.detector(Lp_c, hop_s, att, rel)[ok]
            Ts = np.arange(np.floor(np.percentile(det, 5)), np.ceil(np.percentile(det, 98)) + 1, 1.0)
            for T in Ts:
                hgt = np.maximum(0.0, det - T)
                A = np.stack([np.ones(len(det)), -hgt], axis=1)
                coef, *_ = np.linalg.lstsq(A, gv, rcond=None)
                s = float(np.clip(coef[1], 0.0, 0.999))
                mk = float(np.mean(gv + s * hgt))
                sse = float(np.sum((gv - (mk - s * hgt)) ** 2))
                if best is None or sse < best["sse"]:
                    best = {"sse": sse, "attack_s": att, "release_s": rel, "T": float(T), "s": s, "m": mk}
    det = roomsim.detector(Lp_c, hop_s, best["attack_s"], best["release_s"])[ok]
    hgt = np.maximum(0.0, det - best["T"])
    exp = None
    for Te in np.arange(np.floor(np.percentile(det, 3)), best["T"], 1.0):
        lo = np.maximum(0.0, Te - det)
        A = np.stack([np.ones(len(det)), -hgt, -lo], axis=1)
        coef, *_ = np.linalg.lstsq(A, gv, rcond=None)
        s, e = float(np.clip(coef[1], 0, 0.999)), float(np.clip(coef[2], 0, 3.0))
        mk = float(np.mean(gv + s * hgt + e * lo))
        sse = float(np.sum((gv - (mk - s * hgt - e * lo)) ** 2))
        if sse < best["sse"] * 0.98 and (exp is None or sse < exp["sse"]):
            exp = {"sse": sse, "Te": float(Te), "e": e, "s": s, "m": mk}
    if exp is not None:
        best.update({"sse": exp["sse"], "s": exp["s"], "m": exp["m"], "Te": exp["Te"], "e": exp["e"]})
    r2 = 1.0 - best["sse"] / sst if sst > 0 else 0.0
    rmse0 = math.sqrt(sst / len(gv))
    rmse1 = math.sqrt(best["sse"] / len(gv))
    params = {"kind": "level_curve", "frame_ms": FRAME / FS * 1000, "hop_ms": FHOP / FS * 1000,
              "threshold_db": best["T"], "ratio": 1.0 / (1.0 - best["s"]), "attack_ms": best["attack_s"] * 1000,
              "release_ms": best["release_s"] * 1000, "makeup_db": best["m"],
              "exp_threshold_db": best.get("Te"), "exp_ratio": 1.0 + best.get("e", 0.0), "max_atten_db": 40.0}
    significant = bool(r2 >= 0.15 and rmse0 - rmse1 >= 0.3 and (best["s"] >= 0.1 or best.get("e", 0) >= 0.1))
    kind = "agc" if (best["attack_s"] >= 0.5 or best["release_s"] >= 1.5) and best["s"] >= 0.5 else (
        "compressor" if best["s"] >= 0.1 else ("expander" if best.get("e", 0) >= 0.1 else "none"))
    return {"params": params, "r2": r2, "rmse_linear_db": rmse0, "rmse_model_db": rmse1, "significant": significant,
            "kind": kind}


def analyse_dynamics(x_al_ir: np.ndarray, rec: np.ndarray, noise: dict, spans, log) -> dict:
    """x_al_ir: the linear prediction (the original through the model IR) on the recording's timeline."""
    c, Pp = roomsim.frame_power(x_al_ir, FRAME, FHOP)
    _, Py = roomsim.frame_power(rec, FRAME, FHOP)
    t = c / FS
    pn = noise["p_noise"]
    inside = mask_from_spans([(a + 0.5, b - 0.5) for a, b in spans], len(t), FS / FHOP)
    sig = Pp > 4.0 * pn
    Lp = _db(Pp)
    Lyc = _db(np.maximum(Py - pn, 0.05 * Py))
    g = Lyc - Lp
    ok = inside & sig
    if ok.sum() < 200:
        return {"summary": {"frames": int(ok.sum()), "note": "too little music above the room tone"}, "model": None}
    gmed = float(np.median(g[ok]))
    interf = ok & (g > gmed + 10.0)               # talk/bumps: far louder than the music can explain
    missing = ok & (g < gmed - 15.0)              # far quieter: not played (an edit, the end) or a dropout
    ok &= ~(interf | missing)
    # in/out curve (2 dB bins)
    lo, hi = np.percentile(Lp[ok], 1), np.percentile(Lp[ok], 99)
    edges = np.arange(math.floor(lo), math.ceil(hi) + 2, 2.0)
    curve = []
    for a, b in zip(edges[:-1], edges[1:]):
        sel = ok & (Lp >= a) & (Lp < b)
        if sel.sum() >= 20:
            curve.append({"in_db": (a + b) / 2, "out_db": float(np.median(Lyc[sel])), "n": int(sel.sum())})
    slope = float(np.polyfit(Lp[ok], Lyc[ok], 1)[0])
    medL = float(np.median(Lp[ok]))
    up, dn = ok & (Lp >= medL), ok & (Lp < medL)
    slope_up = float(np.polyfit(Lp[up], Lyc[up], 1)[0]) if up.sum() > 50 else None
    slope_dn = float(np.polyfit(Lp[dn], Lyc[dn], 1)[0]) if dn.sum() > 50 else None
    # slow levelling (AGC): 2 s averages of gain vs input level
    n2 = int(2.0 * FS / FHOP)
    g_slow = _movavg(g, ok, n2)
    L_slow = _movavg(Lp, ok, n2)
    sk = ok & np.isfinite(g_slow) & np.isfinite(L_slow)
    agc_slope = float(np.polyfit(L_slow[sk] - L_slow[sk].mean(), g_slow[sk], 1)[0]) if sk.sum() > 50 else None
    slow_share = float(np.var(g_slow[sk]) / np.var(g[ok])) if sk.sum() > 50 and np.var(g[ok]) > 0 else None
    fit = fit_level_curve(Lp, g, ok)
    log(f"dynamics: {fit['kind']}, R2 {fit['r2']:.2f}, ratio {fit['params']['ratio']:.2f}, "
        f"T {fit['params']['threshold_db']:.1f} dB, att {fit['params']['attack_ms']:.0f} ms, "
        f"rel {fit['params']['release_ms']:.0f} ms")
    # ducking: 150 ms gain dips 6 dB under the 3 s running median, at least 100 ms
    gs = _movavg(g, ok, 6)
    base = ndi.median_filter(np.nan_to_num(gs, nan=gmed), size=int(3.0 * FS / FHOP), mode="nearest")
    dip = ok & np.isfinite(gs) & (gs < base - 6.0)
    ducks = [(a, b) for a, b in runs_of(dip) if b - a >= 4]
    # sustained vs transient (noise suppressors eat steady sounds): input-level variability over the last 1 s
    n1 = int(1.0 * FS / FHOP)
    m1 = ndi.uniform_filter1d(Lp, n1, origin=n1 // 2 - 1 if n1 % 2 == 0 else n1 // 2, mode="nearest")
    v1 = ndi.uniform_filter1d(Lp ** 2, n1, origin=n1 // 2 - 1 if n1 % 2 == 0 else n1 // 2, mode="nearest") - m1 ** 2
    sd1 = np.sqrt(np.maximum(v1, 0.0))
    q1, q3 = np.percentile(Lp[ok], 25), np.percentile(Lp[ok], 75)
    mid = ok & (Lp >= q1) & (Lp <= q3)
    sus, tra = mid & (sd1 < 1.5), mid & (sd1 > 4.0)

    def pr_gain(sel):
        return float(_db(np.sum(np.maximum(Py[sel] - pn, 0.0))) - _db(np.sum(Pp[sel]))) if sel.sum() >= 40 else None

    gs_sus, gs_tra = pr_gain(sus), pr_gain(tra)
    return {
        "t": t, "Lp": Lp, "Lyc": Lyc, "g": g, "ok": ok, "fit": fit,
        "summary": {
            "frames": int(ok.sum()), "interference_s": float(interf.sum() * FHOP / FS),
            "missing_s": float(missing.sum() * FHOP / FS),
            "gain_std_db": float(np.std(g[ok])), "gain_p5_db": float(np.percentile(g[ok], 5)),
            "gain_p95_db": float(np.percentile(g[ok], 95)),
            "in_out_slope": slope, "in_out_slope_upper": slope_up, "in_out_slope_lower": slope_dn,
            "in_out_curve": curve,
            "agc_slope": agc_slope, "slow_gain_share": slow_share,
            "fit": {k: v for k, v in fit.items() if k != "params"} | {"params": fit["params"]},
            "ducking": {"events": len(ducks), "seconds": float(sum(b - a for a, b in ducks) * FHOP / FS),
                        "first_s": [float(t[a]) for a, _ in ducks[:8]]},
            "sustained_gain_db": gs_sus, "transient_gain_db": gs_tra,
            "sustained_minus_transient_db": (gs_sus - gs_tra) if gs_sus is not None and gs_tra is not None else None,
        },
        "model": fit["params"] if fit["significant"] else None,
    }


def analyse_gate(pred: np.ndarray, rec: np.ndarray, noise: dict, spans, dyn_ok_fn) -> dict:
    """Per octave band: gain vs the band's level over its noise floor (power ratios, room tone subtracted), pooled;
    a soft gate fitted when the low-SNR gain falls clearly below the high-SNR gain."""
    _, _, _, Lp = roomsim.band_levels_stft(pred, OCT_EDGES)
    _, _, _, Ly = roomsim.band_levels_stft(rec, OCT_EDGES)
    _, _, _, Ln = roomsim.band_levels_stft(noise["x"], OCT_EDGES)
    nb = 10 * np.log10(np.mean(10 ** (Ln / 10.0), axis=1) + EPS)
    nfr = min(Lp.shape[1], Ly.shape[1])
    Lp, Ly = Lp[:, :nfr], Ly[:, :nfr]
    tfr = np.arange(nfr) * roomsim.STFT_HOP / FS
    ok = dyn_ok_fn(tfr)
    Pp, Py, Pn = 10 ** (Lp / 10.0), 10 ** (Ly / 10.0), 10 ** (nb / 10.0)[:, None]
    snr = Lp - nb[:, None]
    edges = np.arange(-3.0, 48.0, 3.0)
    per_band, pooled_num, pooled_cnt = {}, np.zeros(len(edges) - 1), np.zeros(len(edges) - 1)
    for b in range(len(OCT_CENTRES)):
        gains, cnts = [], []
        for i, (a, c) in enumerate(zip(edges[:-1], edges[1:])):
            sel = ok & (snr[b] >= a) & (snr[b] < c)
            if sel.sum() >= 30:
                gv = float(_db(np.sum(np.maximum(Py[b, sel] - Pn[b, 0], 0.0)) + EPS) - _db(np.sum(Pp[b, sel])))
                gains.append(gv)
                cnts.append(int(sel.sum()))
            else:
                gains.append(math.nan)
                cnts.append(0)
        gains = np.asarray(gains)
        cnts = np.asarray(cnts)
        hi = np.isfinite(gains) & ((edges[:-1] + 1.5) >= 24.0)
        if hi.sum() == 0:
            hi = np.isfinite(gains)
            if hi.sum() == 0:
                continue
            hi = hi & (np.arange(len(gains)) >= np.flatnonzero(hi).max() - 2)
        refg = float(np.average(gains[hi], weights=cnts[hi]))
        relg = gains - refg
        per_band[str(OCT_CENTRES[b])] = {"noise_dbfs": float(nb[b]),
                                         "rel_gain_db": [_f(v, 2) for v in relg], "n": cnts.tolist()}
        good = np.isfinite(relg)
        pooled_num[good] += relg[good] * cnts[good]
        pooled_cnt[good] += cnts[good]
    centres = (edges[:-1] + edges[1:]) / 2
    pooled = np.where(pooled_cnt > 0, pooled_num / np.maximum(pooled_cnt, 1), np.nan)
    low = np.isfinite(pooled) & (centres >= 3) & (centres <= 12)
    low_gain = float(np.average(pooled[low], weights=pooled_cnt[low])) if low.any() else None
    model = None
    fitres = None
    good = np.isfinite(pooled) & (pooled_cnt >= 30)
    if low_gain is not None and low_gain <= -3.0 and good.sum() >= 5:
        def resid(p):
            return (roomsim.gate_gain_db(centres[good], {"thr_db": p[0], "width_db": p[1], "depth_db": p[2]})
                    - pooled[good]) * np.sqrt(pooled_cnt[good])
        r = spo.least_squares(resid, [10.0, 3.0, 10.0], bounds=([-3, 0.5, 0], [45, 15, 60]))
        thr, wd, dp = (float(v) for v in r.x)
        pred_c = roomsim.gate_gain_db(centres[good], {"thr_db": thr, "width_db": wd, "depth_db": dp})
        ss = float(np.sum(pooled_cnt[good] * (pooled[good] - np.average(pooled[good], weights=pooled_cnt[good])) ** 2))
        r2 = 1.0 - float(np.sum(pooled_cnt[good] * (pred_c - pooled[good]) ** 2)) / ss if ss > 0 else 0.0
        fitres = {"thr_db": thr, "width_db": wd, "depth_db": dp, "r2": r2}
        if dp >= 3.0 and r2 >= 0.5:
            model = {"kind": "spectral_gate", "edges_hz": OCT_EDGES, "noise_band_db": nb.tolist(),
                     "thr_db": thr, "width_db": wd, "depth_db": dp}
    return {"summary": {"snr_bin_db": centres.tolist(), "pooled_rel_gain_db": [_f(v, 2) for v in pooled],
                        "low_snr_gain_db": low_gain, "per_band": per_band, "fit": fitres,
                        "detected": bool(model is not None)},
            "model": model}


# ======================================================================================================================
# 5. residual
# ======================================================================================================================
def analyse_residual(pred, rec, dyn, noise, spans_smp) -> dict:
    t, g, ok = dyn.get("t"), dyn.get("g"), dyn.get("ok")
    n = len(rec)
    if t is not None:
        gs = _movavg(np.nan_to_num(g), ok, 3)
        gs = np.where(np.isfinite(gs), gs, 0.0)
        glin = np.interp(np.arange(n), t * FS, 10 ** (gs / 20.0))
    else:
        glin = np.ones(n)
    r = rec - pred
    rg = rec - pred * glin
    nper = 8192
    f, Pr = welch_spans(r, spans_smp, nper)
    _, Prg = welch_spans(rg, spans_smp, nper)
    _, Pp = welch_spans(pred, spans_smp, nper)
    _, Py = welch_spans(rec, spans_smp, nper)
    fn, Pn = sps.welch(noise["x"], FS, window="hann", nperseg=min(nper, len(noise["x"])),
                       noverlap=min(nper, len(noise["x"])) // 2)
    Pn = np.interp(f, fn, Pn)
    cen = third_octave_centres(31.5, 16000)
    bp, br, brg, bn, by = (band_sum(f, v, cen) for v in (Pp, Pr, Prg, Pn, Py))
    rsr = _db(br) - _db(bp)
    rsr_g = _db(brg) - _db(bp)
    nsr = _db(bn) - _db(bp)
    excess = _db(np.maximum(brg - bn, 1e-3 * bn)) - _db(bp)
    # per level (50 ms frames)
    _, Fp = roomsim.frame_power(pred, FRAME, FHOP)
    _, Frg = roomsim.frame_power(rg, FRAME, FHOP)
    per_level = []
    if ok is not None:
        Lp = _db(Fp)
        for a in np.arange(math.floor(np.percentile(Lp[ok], 2) / 5) * 5, np.percentile(Lp[ok], 99.5), 5.0):
            sel = ok & (Lp >= a) & (Lp < a + 5)
            if sel.sum() >= 40:
                rs = float(_db(np.sum(Frg[sel])) - _db(np.sum(Fp[sel])))
                ns = float(_db(sel.sum() * noise["p_noise"]) - _db(np.sum(Fp[sel])))
                ex = float(_db(max(np.sum(Frg[sel]) - sel.sum() * noise["p_noise"], 1e-3 * sel.sum() * noise["p_noise"]))
                           - _db(np.sum(Fp[sel])))
                per_level.append({"in_db": a + 2.5, "rsr_db": rs, "noise_db": ns, "excess_db": ex, "n": int(sel.sum())})
    distortion = None
    if len(per_level) >= 3:
        top = per_level[-1]["excess_db"]
        midv = float(np.median([p["excess_db"] for p in per_level]))
        distortion = {"loudest_excess_db": top, "median_excess_db": midv,
                      "level_dependent": bool(top - midv > 3.0 and top > -25.0)}
    # interference (talk, bumps): 1 s blocks whose gain-corrected residual is 6 dB over the median block's and
    # well above the room tone's share
    blocks = []
    for a, b in spans_smp:
        for s0 in range(a, b - FS + 1, FS):
            pp = float(np.mean(pred[s0:s0 + FS] ** 2))
            pr = float(np.mean(rg[s0:s0 + FS] ** 2))
            if pp > 0:
                blocks.append((s0, float(_db(pr) - _db(pp)), float(_db(noise["p_noise"]) - _db(pp))))
    interf = []
    if len(blocks) >= 10:
        med = float(np.median([x[1] for x in blocks]))
        for s0, rs, ns in blocks:
            if rs > med + 6.0 and rs > ns + 6.0:
                if interf and abs(interf[-1][1] - s0 / FS) < 1e-6:
                    interf[-1][1] = s0 / FS + 1.0
                else:
                    interf.append([s0 / FS, s0 / FS + 1.0])
    p_y = float(np.sum(Py))
    return {"interference_spans_s": interf, "interference_s": float(sum(b - a for a, b in interf)),
            "third_oct_hz": cen, "rsr_db": rsr, "rsr_gain_corrected_db": rsr_g, "noise_to_signal_db": nsr,
            "excess_db": excess, "per_level": per_level, "distortion": distortion,
            "linear_explains": 1.0 - float(np.sum(Pr)) / p_y if p_y > 0 else None,
            "gain_corrected_explains": 1.0 - float(np.sum(Prg)) / p_y if p_y > 0 else None}


# ======================================================================================================================
# 5b. the mic's processing (a fitted stand-in), and the model's replay against the real recording
# ======================================================================================================================
COH_OCT = ((125, 250), (250, 500), (500, 1000), (1000, 2000), (2000, 4000), (4000, 8000))


def coherence_spans(y: np.ndarray, x: np.ndarray, spans_smp, nper: int = 16384) -> list[float | None]:
    """Magnitude-squared coherence of y with x (cross-spectra accumulated over the spans), median per octave
    (COH_OCT)."""
    acc = None
    for a, b in spans_smp:
        if b - a < nper:
            continue
        f, pxy = sps.csd(x[a:b], y[a:b], FS, nperseg=nper, noverlap=nper // 2)
        _, pxx = sps.welch(x[a:b], FS, nperseg=nper, noverlap=nper // 2)
        _, pyy = sps.welch(y[a:b], FS, nperseg=nper, noverlap=nper // 2)
        k = b - a
        acc = [pxy * k, pxx * k, pyy * k] if acc is None else [acc[0] + pxy * k, acc[1] + pxx * k, acc[2] + pyy * k]
    if acc is None:
        return [None] * len(COH_OCT)
    C = np.abs(acc[0]) ** 2 / (acc[1] * acc[2] + 1e-30)
    return [float(np.median(C[(f >= lo) & (f < hi)])) for lo, hi in COH_OCT]


def onset_r_spans(y: np.ndarray, x: np.ndarray, spans, ob_x: dict | None = None) -> dict:
    """Onset-envelope survival: dsp-like SuperFlux envelopes of y against x's, best Pearson r within +-40 ms, frames
    inside the spans only; total and per band."""
    fps = FS / 256
    ob_x = ob_x or onset_bands(x)
    ob_y = onset_bands(y)
    n = min(len(ob_x["total"]), len(ob_y["total"]))
    m = in_spans(np.arange(n) / fps, spans)
    return {k: _best_lag_corr(ob_x[k][:n], ob_y[k][:n], int(0.04 * fps), m)[0] for k in ["total"] + list(BEAT_BANDS)}


def third_oct_spans(y: np.ndarray, spans_smp) -> tuple[np.ndarray, np.ndarray]:
    f, P = welch_spans(y, spans_smp, 8192)
    cen = third_octave_centres(50, 12500)
    return cen, _db(band_sum(f, P, cen))


def section(spans, pad_s: float, n: int) -> tuple[int, int]:
    """Sample range around all spans (+pad), for working on the music only."""
    a = max(0, int((min(s[0] for s in spans) - pad_s) * FS))
    b = min(n, int((max(s[1] for s in spans) + pad_s) * FS))
    return a, b


SCR_REGIONS = {   # frequency region -> (its coherence octaves (COH_OCT indices), its onset bands)
    "low": ((0,), ("sub", "bass")),             # under 250 Hz
    "mid": ((1, 2, 3, 4), ("mid",)),            # 250 Hz-4 kHz
    "high": ((5,), ("high",)),                  # over 4 kHz
}
SCR_EDGES_HZ = [250.0, 4000.0]
SCR_GRID_DB = (0.0, 3.0, 6.0, 9.0, 12.0, 15.0, 18.0)
SCR_START_DB = 6.0              # the scramble while the room's tail is searched first
REV_RT60_S = (0.25, 0.5, 0.75, 1.0)
REV_DRR_DB = (6.0, 3.0, 0.0, -3.0, -6.0, -9.0)
ONSET_WEIGHT = 2.0              # onsets count double: they are what the beat stack hears


def fit_processing(x_al: np.ndarray, rec: np.ndarray, bed: np.ndarray, clean, log, resp_f: np.ndarray,
                   pre_db: np.ndarray, post: np.ndarray | None = None, seeds=(0, 1)) -> dict:
    """What a linear model of this mic can't explain, fitted by replaying the original (x_al, on the section's
    timeline) and comparing with the real recording inside the clean spans: coherence with the original (median per
    octave) and onset-envelope survival (dsp's bands and their total; onsets weighted x2). Two parts:
      the room's reverberant tail   roomsim.synth_ir on the passband response (resp_f, pre_db) with an RT60 and a
                                    direct-to-reverberant ratio from REV_RT60_S x REV_DRR_DB, or none. Where the
                                    mic's processing hides the impulse response this is the only handle on the
                                    room's echo: it smears onsets, and they are what the beat stack hears;
      the mic's processing          roomsim's ``scramble`` (a random spectral gain), one sigma per frequency region
                                    (under 250 Hz, 250 Hz-4 kHz, over 4 kHz: a speakerphone's suppressor works
                                    hardest on the speech band), from SCR_GRID_DB.
    Search: the tail first under a uniform SCR_START_DB scramble; then each region's sigma (the best of the grid for
    its own octaves and onset bands, parabolic between grid points); then the tail again around its first pick.
    ``post`` (the band-limit FIR) goes after the scramble, as roomsim applies it. tau and the band density of the
    scramble are fixed choices of the stand-in, not fitted (they barely move these targets)."""
    clean_smp = [(int(a * FS), int(b * FS)) for a, b in clean]
    ob_x = onset_bands(x_al)
    real_c = coherence_spans(rec, x_al, clean_smp)
    real_o = onset_r_spans(rec, x_al, clean, ob_x)
    n = len(x_al)
    lin_cache = {}

    def linear(rev):
        if rev not in lin_cache:
            h = (roomsim.minphase_fir(resp_f, pre_db, FS, n_fft=32768, taps=8192) if rev is None else
                 roomsim.synth_ir(resp_f, pre_db, rev[0], rev[1], FS).astype(np.float64))
            lin_cache[rev] = sps.oaconvolve(x_al, h)[:n]
        return lin_cache[rev]

    cache = {}

    def replay(rev, scr):
        key = (rev, json.dumps(scr, sort_keys=True))
        if key not in cache:
            cs, os_ = [], []
            for sd in seeds:
                y = linear(rev) + bed_segment(bed, n, sd)
                y = roomsim.scramble(y, scr, sd)
                if post is not None:
                    y = sps.oaconvolve(y, post)[:n]
                cs.append(coherence_spans(y, x_al, clean_smp))
                os_.append(onset_r_spans(y, x_al, clean, ob_x))
            cache[key] = (np.mean(np.asarray(cs, dtype=float), axis=0),
                          {k: float(np.mean([v[k] for v in os_])) for k in os_[0]})
        return cache[key]

    def region_err(c, o, reg):
        octs, bands = SCR_REGIONS[reg]
        e = sum((c[i] - real_c[i]) ** 2 for i in octs if real_c[i] is not None)
        return e + ONSET_WEIGHT * sum((o[k] - real_o[k]) ** 2 for k in bands)

    def total_err(c, o):
        return sum(region_err(c, o, r) for r in SCR_REGIONS) + ONSET_WEIGHT * (o["total"] - real_o["total"]) ** 2

    def scr_params(sig):
        if isinstance(sig, dict):
            return {"regions": {"edges_hz": SCR_EDGES_HZ, "sigma_db": [sig["low"], sig["mid"], sig["high"]]},
                    "tau_ms": SCR_TAU_MS, "bands_per_oct": SCR_BPO}
        return {"sigma_db": float(sig), "tau_ms": SCR_TAU_MS, "bands_per_oct": SCR_BPO}

    def best_rev(scr, cands):
        errs = {rev: total_err(*replay(rev, scr)) for rev in cands}
        return min(errs, key=errs.get), errs

    revs = [None] + [(rt, drr) for rt in REV_RT60_S for drr in REV_DRR_DB]
    rev1, errs1 = best_rev(scr_params(SCR_START_DB), revs)
    log(f"processing: room tail {('RT60 %.2f s, DRR %+.0f dB' % rev1) if rev1 else 'none'} (first pass)")
    pick, table = {}, {}
    for reg in SCR_REGIONS:
        errs = np.array([region_err(*replay(rev1, scr_params(sg)), reg) for sg in SCR_GRID_DB])
        table[reg] = errs.tolist()
        j = int(np.argmin(errs))
        v = SCR_GRID_DB[j]
        if 0 < j < len(errs) - 1:
            den = errs[j - 1] - 2 * errs[j] + errs[j + 1]
            if den > 0:
                v += 0.5 * (errs[j - 1] - errs[j + 1]) / den * (SCR_GRID_DB[1] - SCR_GRID_DB[0])
        pick[reg] = float(round(max(0.0, v), 1))
    scr = scr_params(pick)
    if rev1 is None:
        near = [None] + [(rt, drr) for rt in REV_RT60_S[:1] for drr in REV_DRR_DB[:2]]
    else:
        i_rt, i_d = REV_RT60_S.index(rev1[0]), REV_DRR_DB.index(rev1[1])
        near = [None] + [(REV_RT60_S[i], REV_DRR_DB[j]) for i in range(max(0, i_rt - 1), min(len(REV_RT60_S), i_rt + 2))
                         for j in range(max(0, i_d - 1), min(len(REV_DRR_DB), i_d + 2))]
    rev, errs2 = best_rev(scr, near)
    c_fit, o_fit = replay(rev, scr)
    c0_, o0_ = replay(None, scr_params(0.0))
    e_fit, e_none = total_err(c_fit, o_fit), total_err(c0_, o0_)
    names_c = [f"{lo}-{hi}" for lo, hi in COH_OCT]
    log(f"processing: room tail {('RT60 %.2f s, DRR %+.0f dB' % rev) if rev else 'none'}; scramble sigma "
        f"low/mid/high {pick['low']:.1f}/{pick['mid']:.1f}/{pick['high']:.1f} dB (error {e_fit:.4f}; linear: "
        f"{e_none:.4f})")
    used = bool(e_fit < 0.7 * e_none)
    return {"tail": {"rt60_s": rev[0], "drr_db": rev[1]} if rev else None,
            "params": scr if max(pick.values()) >= 1.0 else None, "sigma_db": pick, "error": e_fit,
            "error_linear": e_none, "used": used,
            "real": {"coherence_oct": dict(zip(names_c, real_c)), "onset_r": real_o},
            "fitted": {"coherence_oct": dict(zip(names_c, c_fit.tolist())), "onset_r": o_fit},
            "linear": {"coherence_oct": dict(zip(names_c, c0_.tolist())), "onset_r": o0_},
            "tail_errors_first": {("none" if k is None else f"{k[0]}s/{k[1]:+.0f}dB"): v for k, v in errs1.items()},
            "tail_errors_final": {("none" if k is None else f"{k[0]}s/{k[1]:+.0f}dB"): v for k, v in errs2.items()},
            "region_errors": table, "grid_db": list(SCR_GRID_DB)}


def bed_segment(bed: np.ndarray, n: int, seed: int) -> np.ndarray:
    """The room-sound bed looped from a seeded offset, as roomsim.apply takes it."""
    if bed is None or not len(bed):
        return np.zeros(n)
    return roomsim.loop_segment(bed, n, seed)


# ======================================================================================================================
# 6-7. beat trackers (subprocesses), ground truth, what survives
# ======================================================================================================================
def models_dir() -> pathlib.Path:
    d = os.environ.get("SM_MODELS_DIR")
    if not d:
        try:
            m = re.search(r"^\s*models_dir:\s*([^\s#]+)", SHOW_YAML.read_text(encoding="utf-8"), re.M)
            d = m.group(1) if m else None
        except OSError:
            d = None
    return pathlib.Path(os.path.expanduser(d or "~/.cache/screen-machine/models"))


TRACKERS = {   # name -> (python, input rate, extra args)
    "madmom": (PY_SHOW, 44100, []),
    "madmom_online": (PY_SHOW, 44100, ["--nets", "2"]),
    "beatthis": (PY_ML, 22050, None),               # None: the models dir and model, filled in at run time
    "dsphits": (PY_SHOW, 48000, []),
}


def run_trackers(jobs: dict[str, dict[str, np.ndarray]], work: pathlib.Path, log) -> dict:
    """{tracker: {name: signal at 48 kHz}} -> {tracker: {name: result}, "errors": [...]}: each tracker one
    subprocess (beats_offline.py in its own env, nice 19, one thread). A result holds beats (+ downbeats), the
    activation as ``act_data`` (madmom: probability at 100 fps; Beat This!: logits at 50 fps), or dsp's ``hits``."""
    work.mkdir(parents=True, exist_ok=True)
    out = {"errors": []}
    env = dict(os.environ)
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        env[k] = "1"
    for tracker, signals in jobs.items():
        out[tracker] = {}
        py, fs, extra = TRACKERS[tracker]
        if extra is None:
            extra = ["--models-dir", str(models_dir()), "--model", BEAT_MODEL]
        if not py.is_file():
            out["errors"].append(f"{tracker}: {py} missing")
            continue
        paths = {}
        for name, x in signals.items():
            p = work / f"{tracker}-{name}-{fs}.npy"
            y = x if fs == FS else soxr.resample(x, FS, fs, quality="HQ")
            np.save(p, np.asarray(y, dtype=np.float32))
            paths[name] = str(p)
        res_p = work / f"{tracker}.json"
        cmd = ["nice", "-n", "19", str(py), str(BEATS_WORKER), tracker, "--out", str(res_p), *extra, *paths.values()]
        t0 = time.time()
        log(f"beats: {tracker} on {', '.join(paths)} ...")
        try:
            subprocess.run(cmd, check=True, env=env, timeout=3600, capture_output=True, text=True)
            res = json.loads(res_p.read_text(encoding="utf-8"))
        except subprocess.CalledProcessError as e:
            out["errors"].append(f"{tracker}: exit {e.returncode}: {(e.stderr or '')[-400:]}")
            continue
        except Exception as e:
            out["errors"].append(f"{tracker}: {type(e).__name__}: {e}"[:400])
            continue
        log(f"beats: {tracker} done in {time.time() - t0:.0f} s")
        for name, p in paths.items():
            r = res["files"].get(p)
            if r is None:
                out["errors"].append(f"{tracker} {name}: {res.get('errors', {}).get(p)}")
                continue
            if r.get("act"):
                r["act_data"] = np.load(r["act"])
            out[tracker][name] = r
    return out


def score_beats(ref: np.ndarray, est: np.ndarray) -> dict:
    import mir_eval
    ref, est = np.sort(np.asarray(ref, float)), np.sort(np.asarray(est, float))
    if len(ref) < 4 or len(est) < 4:
        return {"n_ref": len(ref), "n_est": len(est)}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        F = mir_eval.beat.f_measure(ref, est, 0.07)
        cmlc, cmlt, amlc, amlt = mir_eval.beat.continuity(ref, est)
    near = nearest_diff(est, ref)[1]
    m = np.abs(near) <= 0.07
    return {"n_ref": len(ref), "n_est": len(est), "f_measure": F, "cmlt": cmlt, "amlt": amlt,
            "hit_rate_25ms": float(np.mean(np.abs(near) <= 0.025)),
            "offset_ms_median": float(np.median(near[m]) * 1000) if m.any() else None,
            "offset_ms_std": float(np.std(near[m]) * 1000) if m.sum() > 1 else None}


def act_on_rec_timeline(act: np.ndarray, fps: float, c0: float, c1: float, n_frames: int) -> np.ndarray:
    """An activation of the whole original (original time) resampled onto recording frames."""
    u = c0 + c1 * np.arange(n_frames) / fps
    return np.interp(u * fps, np.arange(len(act)), act, left=0.0, right=0.0)


def nearest_diff(a_sorted: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For each b: (index of the nearest element of the sorted array a, a[that] - b). a must not be empty."""
    j = np.clip(np.searchsorted(a_sorted, b), 1, max(1, len(a_sorted) - 1))
    hi = np.minimum(j, len(a_sorted) - 1)
    near = np.where(np.abs(a_sorted[hi] - b) < np.abs(a_sorted[j - 1] - b), hi, j - 1)
    return near, a_sorted[near] - b


def in_spans(t: np.ndarray, spans) -> np.ndarray:
    ok = np.zeros(len(t), dtype=bool)
    for a, b in spans:
        ok |= (t >= a) & (t < b)
    return ok


# ======================================================================================================================
# 8. the live clock
# ======================================================================================================================
def read_jsonl(path: pathlib.Path) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def bus_messages(run_dir: pathlib.Path, stream: str):
    """-> (clock bodies [(t_ms, body)] sorted, {band: hit times s} of ``stream``, message type counts)."""
    clocks, hits, types = [], [], {}
    for r in read_jsonl(run_dir / "bus.jsonl"):
        msg = r.get("msg") if isinstance(r.get("msg"), dict) else {}
        typ = msg.get("type")
        data = msg.get("data") if isinstance(msg.get("data"), dict) else None
        types[str(typ)] = types.get(str(typ), 0) + 1
        if data is None:
            continue
        rx = r.get("rx_ms")
        if typ == "music.clock":
            tv = data.get("t_ms", rx)
            if tv is not None:
                clocks.append((float(tv), data))
        elif typ == "music.hit" and data.get("t_ms") is not None:
            hits.append((float(data["t_ms"]), str(data.get("stream", "")), str(data.get("band", ""))))
    if hits and any(h[1] == stream for h in hits):
        hits = [h for h in hits if h[1] == stream]
    if not clocks:                          # fall back to the 1 Hz state samples
        for r in read_jsonl(run_dir / "state.jsonl"):
            c = r.get("clock")
            body = c.get("clock") if isinstance(c, dict) and isinstance(c.get("clock"), dict) else c
            if isinstance(body, dict) and "bpm" in body:
                clocks.append((float(body.get("t_ms", r.get("rx_ms", 0.0))), body))
    clocks.sort(key=lambda c: c[0])
    by_band: dict[str, list] = {}
    for t, _, band in hits:
        by_band.setdefault(band, []).append(t / 1000.0)
    return clocks, {k: np.sort(np.asarray(v)) for k, v in by_band.items()}, types


def map_host_by_hits(bus: dict, rec_hits: dict) -> dict:
    """rec_s = a + b host_s from dsp's music.hit on the bus against dsp's own hits on the recording (the same
    detector on the same audio: pairs agree to a millisecond while the live stream is intact): all-band impulse
    trains at 100 Hz cross-correlated (coarse), then same-band pairs within 30 ms and a consensus line (2 ms).
    Pairs only exist where the live stream carried the same audio as the recording: a stretch of heavy packet loss
    or re-anchored segments has none, so the line is fitted where they exist and extrapolated (the two clocks are
    both crystal: ppm)."""
    bands = [k for k in bus if k in rec_hits and len(bus[k]) and len(rec_hits[k])]
    if not bands:
        return {"ok": False, "reason": "no hit bands in common"}
    hb_all = np.sort(np.concatenate([bus[k] for k in bands]))
    hr_all = np.sort(np.concatenate([rec_hits[k] for k in bands]))
    if len(hb_all) < 20 or len(hr_all) < 20:
        return {"ok": False, "reason": f"too few hits (bus {len(hb_all)}, recording {len(hr_all)})"}
    fps = 100.0
    h0 = hb_all.min()
    tb = np.zeros(int((hb_all.max() - h0) * fps) + 2)
    np.add.at(tb, ((hb_all - h0) * fps).astype(int), 1.0)
    tr = np.zeros(int(hr_all.max() * fps) + 2)
    np.add.at(tr, (hr_all * fps).astype(int), 1.0)
    tb, tr = ndi.uniform_filter1d(tb, 3), ndi.uniform_filter1d(tr, 3)
    cc = sps.correlate(tr - tr.mean(), tb - tb.mean(), mode="full", method="fft")
    lags = np.arange(-(len(tb) - 1), len(tr))
    i = int(np.argmax(cc))
    z = float((cc[i] - np.median(cc)) / (cc.std() + 1e-12))
    off = lags[i] / fps - h0
    ph, pr = [], []
    for k in bands:
        pred = bus[k] + off
        near, d = nearest_diff(rec_hits[k], pred)
        sel = np.abs(d) < 0.03
        ph.append(bus[k][sel])
        pr.append(rec_hits[k][near[sel]])
    ph, pr = np.concatenate(ph), np.concatenate(pr)
    if len(ph) < 20:
        return {"ok": False, "reason": f"only {len(ph)} hit pairs (xcorr z {z:.1f})", "xcorr_z": z}
    fit = ransac_line(ph - h0, pr, np.ones(len(ph)), tol=0.002, slope_range=(1 - 5e-4, 1 + 5e-4))
    if fit is None:
        return {"ok": False, "reason": "no consistent line through the hit pairs", "xcorr_z": z}
    c0, c1, inl = fit
    a, b = c0 - c1 * h0, c1
    res = pr - (a + b * ph)
    return {"ok": bool(z >= 5.0 and inl.sum() >= 20), "method": "dsp hits (same detector on the recording)",
            "a": a, "b": b, "xcorr_z": z, "pairs": int(inl.sum()), "bus_hits": int(len(hb_all)),
            "residual_rms_ms": float(np.sqrt(np.mean(res[inl] ** 2)) * 1000),
            "slope_ppm": (b - 1.0) * 1e6, "pairs_span_s": [float(pr[inl].min()), float(pr[inl].max())]}


def map_host_by_onsets(bus: dict, rec: np.ndarray) -> dict:
    """Fallback without dsp's own hits: the bus's hit train (all bands) against the recording's onset envelope
    (SuperFlux), cross-correlated at 100 Hz; then the median offset of each bus hit to the envelope's largest peak
    within 30 ms. Rate 1 (crystal clocks)."""
    hb = np.sort(np.concatenate(list(bus.values()))) if bus else np.zeros(0)
    if len(hb) < 20:
        return {"ok": False, "reason": "too few bus hits"}
    env = onset_bands(rec)["total"]
    fps_e = FS / 256
    fps = 100.0
    e = np.interp(np.arange(0, len(env) / fps_e, 1 / fps), np.arange(len(env)) / fps_e, env)
    h0 = hb.min()
    tb = np.zeros(int((hb.max() - h0) * fps) + 2)
    np.add.at(tb, ((hb - h0) * fps).astype(int), 1.0)
    cc = sps.correlate(e - e.mean(), tb - tb.mean(), mode="full", method="fft")
    lags = np.arange(-(len(tb) - 1), len(e))
    i = int(np.argmax(cc))
    z = float((cc[i] - np.median(cc)) / (cc.std() + 1e-12))
    off = lags[i] / fps - h0
    w = int(0.03 * fps_e)
    ds = []
    for t in hb + off:
        j = int(round(t * fps_e))
        if w <= j < len(env) - w:
            k = int(np.argmax(env[j - w:j + w + 1])) - w
            ds.append(k / fps_e)
    d = float(np.median(ds)) if ds else 0.0
    return {"ok": bool(z >= 5.0), "method": "bus hits vs the recording's onset envelope", "a": off + d, "b": 1.0,
            "xcorr_z": z, "pairs": len(ds), "residual_rms_ms": float(np.std(ds) * 1000) if ds else None,
            "slope_ppm": 0.0}


def map_host_by_clock_lines(run_dir: pathlib.Path) -> dict:
    """Last resort: the recorder's start (rec.json started_utc) against the bus logger's 'bus connected' line
    (buslog.log, UTC, whole seconds) and the first rx_ms in bus.jsonl. About +-1 s."""
    try:
        recm = json.loads((run_dir / "rec.json").read_text(encoding="utf-8"))
        t_rec = dt.datetime.fromisoformat(recm["started_utc"]).timestamp()
        line = next(ln for ln in (run_dir / "buslog.log").read_text(encoding="utf-8").splitlines()
                    if "bus connected" in ln)
        t_bus = dt.datetime.fromisoformat(line.split()[0]).timestamp()
        first = next(iter(read_jsonl(run_dir / "bus.jsonl")))["rx_ms"] / 1000.0
    except Exception as e:                                     # noqa: BLE001
        return {"ok": False, "reason": f"clock lines unusable: {type(e).__name__}: {e}"[:200]}
    a = -(first + (t_rec - t_bus))                             # rec_s = host_s + a
    return {"ok": True, "method": "clock lines (rec.json start vs buslog 'bus connected'), about +-1 s", "a": a,
            "b": 1.0, "pairs": 0, "residual_rms_ms": None, "slope_ppm": 0.0}


def stream_health(run_dir: pathlib.Path, a: float, b: float, spans) -> dict | None:
    """The live stream's packet loss and re-anchoring while the music played (state.jsonl's 1 Hz receiver
    stats), mapped onto the recording's time."""
    rows = []
    for r in read_jsonl(run_dir / "state.jsonl"):
        st = r.get("stream") if isinstance(r.get("stream"), dict) else {}
        net = st.get("net") if isinstance(st.get("net"), dict) else {}
        if r.get("rx_ms") is None or not net:
            continue
        rows.append((a + b * r["rx_ms"] / 1000.0, net))
    if not rows:
        return None
    lo, hi = min(s[0] for s in spans), max(s[1] for s in spans)
    inside = [(t, n) for t, n in rows if lo <= t <= hi]
    before = [(t, n) for t, n in rows if t < lo]
    if not inside:
        return None

    def delta(key):
        v1 = inside[-1][1].get(key)
        v0 = (before[-1][1] if before else inside[0][1]).get(key)
        return (v1 - v0) if isinstance(v1, (int, float)) and isinstance(v0, (int, float)) else None

    loss = [n.get("loss_pct") for _, n in inside if isinstance(n.get("loss_pct"), (int, float))]
    pk, lost = delta("packets"), delta("lost")
    return {"loss_pct_median": float(np.median(loss)) if loss else None, "loss_pct_max": max(loss) if loss else None,
            "lost_share": (lost / (pk + lost)) if pk and lost is not None and pk + lost > 0 else None,
            "packets": pk, "lost": lost, "gaps": delta("gaps"), "segments": delta("segments"),
            "note": "the live stream (PC22 agent -> show_service, VBAN) while the music played: lost packets and "
                    "re-anchored segments are the network's and the receiver's, not the room's"}


def live_beats(clocks: list, cap_ms: float = 2000.0) -> list[dict]:
    """Integer-beat crossings of the published clock line: body i predicts the beats in [t_i, t_{i+1})."""
    out = []
    for i, (tv, c) in enumerate(clocks):
        bpm = float(c.get("bpm") or 0.0)
        if bpm <= 0:
            continue
        t_end = clocks[i + 1][0] if i + 1 < len(clocks) else tv + 100.0
        t_end = min(t_end, tv + cap_ms)
        per = 60000.0 / bpm
        anc, baa = float(c["anchor_ms"]), float(c["beat_at_anchor"])
        b0 = baa + (tv - anc) / per
        b1 = baa + (t_end - anc) / per
        for k in range(int(math.ceil(b0 - 1e-9)), int(math.ceil(b1 - 1e-9))):
            tk = anc + (k - baa) * per
            if tv <= tk < t_end:
                out.append({"t_ms": tk, "k": k, "gate": c.get("gate", "none"), "conf": c.get("conf"),
                            "state": c.get("state"), "epoch": c.get("epoch"), "bpm": bpm})
    return out


def body_at(clocks: list, t_ms: np.ndarray) -> np.ndarray:
    tv = np.array([c[0] for c in clocks])
    return np.searchsorted(tv, t_ms, side="right") - 1


def analyse_live(run_dir, stream, rec, gt_rec, music_spans, log, rec_hits: dict | None = None) -> dict:
    """What the live clock flashed during the calibration, against the ground truth. Host time -> recording time:
    dsp's hits on the bus against dsp's own hits on the recording (``rec_hits`` from beats_offline.py dsphits), else
    the bus hits against the recording's onset envelope, else the clock lines (+-1 s: not scored)."""
    clocks, bus_hits, types = bus_messages(run_dir, stream)
    if not clocks:
        return {"available": False, "reason": "no music.clock in bus.jsonl or state.jsonl", "bus_types": types}
    tried = []
    mp = {"ok": False}
    if rec_hits:
        mp = map_host_by_hits(bus_hits, rec_hits)
        tried.append({k: v for k, v in mp.items() if k not in ("a", "b")})
    if not mp.get("ok") and bus_hits:
        mp = map_host_by_onsets(bus_hits, rec)
        tried.append({k: v for k, v in mp.items() if k not in ("a", "b")})
    if not mp.get("ok"):
        cl = map_host_by_clock_lines(run_dir)
        tried.append({k: v for k, v in cl.items() if k not in ("a", "b")})
        return {"available": False, "reason": "no host <-> recording time map good enough to score beats "
                                              "(the clock lines alone are +-1 s)", "map_tried": tried,
                "bus_types": types}
    a, b = mp["a"], mp["b"]
    log(f"live: host -> recording by {mp['method']}: {mp.get('pairs')} pairs, rms {mp.get('residual_rms_ms')} ms")
    beats = live_beats(clocks)
    lt = np.array([(x["t_ms"] / 1000.0) * b + a for x in beats])          # recording seconds
    # eval span: the music (matched) inside the bus log's coverage
    c_lo, c_hi = clocks[0][0] / 1000.0 * b + a, clocks[-1][0] / 1000.0 * b + a
    spans = [(max(s, c_lo), min(e, c_hi)) for s, e in music_spans if min(e, c_hi) > max(s, c_lo)]
    gt = gt_rec[in_spans(gt_rec, spans)]
    if len(gt) < 8:
        return {"available": False, "reason": "the bus log does not cover the music", "map": mp}
    est_ok = in_spans(lt, [(s - 0.1, e + 0.1) for s, e in spans])
    lt_e = lt[est_ok]
    gates = np.array([x["gate"] for x in beats])[est_ok]
    # double flashes: two beats within 30 ms of each other (a jump back)
    srt = np.sort(lt_e)
    doubles = int(np.sum(np.diff(srt) < 0.03)) if len(srt) > 1 else 0
    est = srt[np.concatenate([[True], np.diff(srt) >= 0.03])] if len(srt) else srt
    overall = score_beats(gt, est)
    # per GT beat: the body valid then (gate, state, bpm), and the nearest live beat
    host_gt = (gt - a) / b * 1000.0
    bi = body_at(clocks, host_gt)
    gate_gt = np.array([clocks[i][1].get("gate", "none") if i >= 0 else "none" for i in bi])
    state_gt = np.array([clocks[i][1].get("state", "") if i >= 0 else "" for i in bi])
    bpm_gt_live = np.array([float(clocks[i][1].get("bpm") or 0.0) if i >= 0 else 0.0 for i in bi])
    near = nearest_diff(est, gt)[1] if len(est) else np.full(len(gt), np.inf)
    hit70, hit25 = np.abs(near) <= 0.07, np.abs(near) <= 0.025
    # precision while gated: live beats flashed under gate beat
    gb = lt_e[gates == "beat"]
    if len(gb):
        ng = np.abs(nearest_diff(gt, gb)[1])
        prec70, prec25 = float(np.mean(ng <= 0.07)), float(np.mean(ng <= 0.025))
    else:
        prec70 = prec25 = None
    # time to the first correct lock: 4 GT beats in a row, each with a live beat within 70 ms under gate beat
    first = {}
    for name, gset in (("beat", ("beat",)), ("beat_or_dmx", ("beat", "dmx"))):
        good = hit70 & np.isin(gate_gt, gset)
        t_first = None
        for i in range(len(gt) - 3):
            if good[i:i + 4].all():
                t_first = float(gt[i] - spans[0][0])
                break
        first[name] = t_first
    # bpm vs GT (local: median of the 4 inter-beat intervals around each GT beat)
    ibi = np.diff(gt)
    loc = np.array([np.median(ibi[max(0, i - 2):i + 2]) for i in range(len(gt))]) if len(ibi) >= 2 else np.ones(len(gt))
    gbpm = 60.0 / loc
    ratio = np.where(bpm_gt_live > 0, bpm_gt_live / gbpm, np.nan)
    rr = ratio[np.isfinite(ratio)]
    after = None
    if first["beat_or_dmx"] is not None:
        t0 = spans[0][0] + first["beat_or_dmx"]
        after = score_beats(gt[gt >= t0], est[est >= t0 - 0.1])
    log(f"live: {len(beats)} clock beats, F {overall.get('f_measure', 0):.3f}, "
        f"coverage(beat) {np.mean(gate_gt == 'beat'):.2f}, first lock {first['beat']}")
    gate_share = {g: float(np.mean(gate_gt == g)) for g in ("beat", "dmx", "none")}
    states = {}
    for i in bi:
        st = clocks[i][1].get("state", "") if i >= 0 else ""
        states[st] = states.get(st, 0) + 1
    return {
        "available": True, "lead_ms": 0.0, "bus_types": types, "clock_bodies": len(clocks),
        "map": {k: v for k, v in mp.items() if k not in ("a", "b")} | {"offset_s": a, "rate": b},
        "map_tried": tried, "stream_health": stream_health(run_dir, a, b, spans),
        "gate_share": gate_share, "states": {k: v / max(1, len(bi)) for k, v in states.items()},
        "eval_spans_s": spans, "gt_beats": int(len(gt)), "live_beats": int(len(est)), "double_flashes": doubles,
        "overall": overall, "after_first_lock": after,
        "hit_rate_25ms": float(np.mean(hit25)), "hit_rate_70ms": float(np.mean(hit70)),
        "coverage_beat": float(np.mean(gate_gt == "beat")),
        "coverage_beat_or_dmx": float(np.mean(np.isin(gate_gt, ("beat", "dmx")))),
        "coverage_locked": float(np.mean(state_gt == "locked")),
        "precision_gated_70ms": prec70, "precision_gated_25ms": prec25,
        "time_to_first_lock_s": first,
        "bpm": {"within_4pct": float(np.mean(np.abs(rr - 1) <= 0.04)) if len(rr) else None,
                "octave_double": float(np.mean(np.abs(rr - 2) <= 0.08)) if len(rr) else None,
                "octave_half": float(np.mean(np.abs(rr - 0.5) <= 0.02)) if len(rr) else None,
                "median_abs_err_pct": float(np.median(np.abs(rr - 1)) * 100) if len(rr) else None,
                "gt_bpm_median": float(np.median(gbpm))},
        "_plot": {"gt": gt, "near": near, "gate_gt": gate_gt, "gbpm": gbpm, "live_bpm": bpm_gt_live},
    }


# ======================================================================================================================
# 9. our beat stack on the real recording and on the model's replays (tools/room/beat_eval/run_stack.py)
# ======================================================================================================================
STACK_KEYS = ("f_0", "hit25_0", "hit70_gated_0", "f_gated_0", "cover_beat_0", "cover_gated_0", "prec_beat_0",
              "t_lock_0", "acc1_time_0", "offset_ms_0")


def _score_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("beat_eval_score", RUN_STACK.parent / "score.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_beats(path: pathlib.Path, beats: np.ndarray) -> None:
    path.write_text("".join(f"{t:.6f}\t0\n" for t in np.asarray(beats)), encoding="utf-8")


def run_stack_cases(cases: list[dict], work: pathlib.Path, log) -> dict:
    """Each case {"id", "wav", "beats", "gate"}: run_stack.py (show_service's own dsp + clock, madmom inline, no
    slow lane, no pre-roll: the reference runs' settings) in show_service's env, then beat_eval/score.py's
    score_one. -> {id: {metric: value} | {"error": ...}}."""
    out = {}
    score = None
    env = dict(os.environ)
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        env[k] = "1"
    for c in cases:
        res_p = work / f"stack_{c['id']}.json"
        cmd = ["nice", "-n", "19", str(PY_SHOW), str(RUN_STACK), str(c["wav"]), "--beats", str(c["beats"]),
               "--out", str(res_p), "--id", c["id"], "--no-slow-lane", "--preroll-s", "0",
               f"--gate-ref-lufs={c['gate']}"]
        t0 = time.time()
        try:
            subprocess.run(cmd, check=True, env=env, timeout=1800, capture_output=True, text=True, cwd=str(REPO))
            r = json.loads(res_p.read_text(encoding="utf-8"))
            score = score or _score_module()
            row = score.score_one(r, score.read_beats(pathlib.Path(c["beats"])))
            out[c["id"]] = {k[:-2]: row.get(k) for k in STACK_KEYS} | {"gate_ref_lufs": c["gate"],
                                                                        "seeds": row.get("seeds")}
            log(f"stack: {c['id']} gate {c['gate']}: hit25 {row.get('hit25_0', 0):.2f}, gated<=70ms "
                f"{row.get('hit70_gated_0', 0):.2f}, lock {row.get('t_lock_0')} ({time.time() - t0:.0f} s)")
        except subprocess.CalledProcessError as e:
            out[c["id"]] = {"error": f"exit {e.returncode}: {(e.stderr or '')[-300:]}"}
        except Exception as e:                                 # noqa: BLE001
            out[c["id"]] = {"error": f"{type(e).__name__}: {e}"[:300]}
    return out


# ======================================================================================================================
# figures
# ======================================================================================================================
def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF, "axes.edgecolor": GRID,
        "axes.labelcolor": INK2, "text.color": INK, "xtick.color": INK2, "ytick.color": INK2, "axes.grid": True,
        "grid.color": GRID, "grid.linewidth": 0.6, "lines.linewidth": 1.4, "font.size": 9,
        "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False, "axes.titlesize": 10,
        "axes.titleweight": "bold", "axes.titlelocation": "left"})
    return plt


def make_figs(figs: pathlib.Path, ctx: dict, log) -> list[str]:
    plt = _plt()
    figs.mkdir(parents=True, exist_ok=True)
    made = []

    def save(fig, name):
        fig.tight_layout()
        fig.savefig(figs / name, dpi=130)
        plt.close(fig)
        made.append(name)

    lin = ctx["lin"]
    # frequency response + coherence (two axes, one unit each)
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(8, 5.2), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    c, m, ch = lin["centres"], lin["mag_db"] - lin["response"]["gain_db_300_3k"], lin["coh_s"]
    rel = ch >= 0.3
    a1.semilogx(c, np.where(rel, m, np.nan), color=PAL["blue"], label="H1 (cross-spectrum), coherence >= 0.3")
    a1.semilogx(c, np.where(~rel, m, np.nan), color=PAL["blue"], ls=":", label="H1, unreliable")
    pr = ctx.get("pr") or {}
    if pr.get("mag_db") is not None:
        ex = pr["excited"]
        a1.semilogx(pr["centres"][ex], pr["mag_db"][ex] - lin["response"]["gain_db_300_3k"], color=PAL["orange"],
                    label="power response (music only)")
    a1.set_ylabel("dB re 300 Hz-3 kHz")
    a1.set_title(f"What the room + mic does to each frequency (gain {lin['response']['gain_db_300_3k']:+.1f} dB at 300 Hz-3 kHz)")
    a1.legend(loc="lower center")
    a1.set_ylim(-60, 20)
    a2.semilogx(c, ch, color=PAL["blue"])
    a2.set_ylim(0, 1)
    a2.set_ylabel("coherence")
    a2.set_xlabel("Hz")
    a2.set_xlim(20, 20000)
    save(fig, "freq_response.png")
    # IR + decay
    mw = lin["m_w"]
    h = lin["h_w"]
    pre = lin["pre"]
    n_show = min(len(h), pre + int(min(1.2, max(0.3, (mw["truncation_ms"] or 500) / 1000 * 1.5)) * FS))
    env = _db(ndi.uniform_filter1d(h[:n_show] ** 2, int(0.002 * FS)))
    env -= env.max()
    tt = (np.arange(n_show) - pre) / FS * 1000
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(8, 5.2))
    a1.plot(tt, env, color=PAL["blue"], lw=0.9)
    a1.axvline(mw["truncation_ms"] + mw["direct_ms"], color=MUTED, ls="--", lw=1)
    a1.set_ylim(-80, 3)
    a1.set_ylabel("dB (2 ms energy)")
    a1.set_xlabel("ms after the direct sound")
    a1.set_title("Impulse response (dashed: where it meets the estimate's noise floor)")
    te, ed = mw["_edc"]
    a2.plot(te * 1000, ed, color=PAL["blue"], label="Schroeder decay")
    for key, lab, col, (top, bot) in (("t30_s", "T30", PAL["orange"], (-5, -35)), ("edt_s", "EDT", PAL["aqua"], (0, -10))):
        v = mw.get(key)
        if v:
            sel = (ed <= top) & (ed >= bot)
            if sel.any():
                t0 = te[sel][0]
                xs = np.array([t0, t0 + (bot - top) / (-60.0 / v)])
                a2.plot(xs * 1000, [top, bot], color=col, lw=1.2, ls="--", label=f"{lab} {v:.2f} s")
    a2.set_ylim(-60, 2)
    a2.set_ylabel("dB")
    a2.set_xlabel("ms")
    a2.legend()
    save(fig, "ir_decay.png")
    # noise spectrum vs music
    nz = ctx["noise"]["summary"]
    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.semilogx(nz["third_octave_hz"], nz["third_octave_dbfs"], color=MUTED, marker="o", ms=3,
                label="room sound (no music)")
    if nz.get("floor_third_octave_dbfs") is not None:
        ax.semilogx(nz["third_octave_hz"], nz["floor_third_octave_dbfs"], color=MUTED, ls=":", label="its quiet floor")
    if ctx.get("music_bands") is not None:
        ax.semilogx(ctx["music_bands"][0], ctx["music_bands"][1], color=PAL["blue"], marker="o", ms=3,
                    label="music in the recording")
    ax.set_ylabel("dBFS per 1/3 octave")
    ax.set_xlabel("Hz")
    ax.set_title(f"Room sound {nz['rms_dbfs']:.1f} dBFS RMS vs the music")
    ax.legend()
    save(fig, "noise_spectrum.png")
    dyn = ctx["dyn"]
    if dyn.get("t") is not None:
        t, Lp, Lyc, g, ok = dyn["t"], dyn["Lp"], dyn["Lyc"], dyn["g"], dyn["ok"]
        fig, (a1, a2) = plt.subplots(2, 1, figsize=(9, 5.2), sharex=True)
        sm = lambda v: _movavg(v, ok, 8)   # noqa: E731  (200 ms)
        a1.plot(t, sm(Lp), color=PAL["blue"], lw=0.9, label="expected at the mic (linear)")
        a1.plot(t, sm(Lyc), color=PAL["orange"], lw=0.9, label="recorded (room tone removed)")
        a1.set_ylabel("dBFS (50 ms)")
        a1.legend(loc="lower right")
        a1.set_title("Level over time")
        a2.plot(t, sm(g), color=PAL["blue"], lw=0.9, label="measured gain")
        if dyn["fit"]["significant"]:
            p = dyn["fit"]["params"]
            det = roomsim.detector(np.maximum(Lp, -100), FHOP / FS, p["attack_ms"] / 1000, p["release_ms"] / 1000)
            gm = roomsim.level_curve_gain(det, p)
            a2.plot(t, np.where(ok, gm, np.nan), color=PAL["aqua"], lw=0.9, label=f"fitted {dyn['fit']['kind']}")
        a2.set_ylabel("gain dB")
        a2.set_xlabel("recording s")
        a2.legend(loc="lower right")
        save(fig, "gain_over_time.png")
        fig, ax = plt.subplots(figsize=(5.6, 5))
        idx = np.flatnonzero(ok)
        if len(idx) > 6000:
            idx = idx[:: len(idx) // 6000]
        ax.scatter(Lp[idx], Lyc[idx], s=2, color=MUTED, alpha=0.35, lw=0, label="50 ms frames")
        cv = dyn["summary"]["in_out_curve"]
        if cv:
            ax.plot([q["in_db"] for q in cv], [q["out_db"] for q in cv], color=PAL["blue"], marker="o", ms=3,
                    label="median")
        lo, hi = np.percentile(Lp[ok], 1), np.percentile(Lp[ok], 99)
        xs = np.linspace(lo, hi, 50)
        ax.plot(xs, xs + np.median(g[ok]), color=INK2, ls=":", lw=1, label="no dynamics")
        if dyn["fit"]["significant"] and cv:
            p = dyn["fit"]["params"]
            det = roomsim.detector(np.maximum(Lp, -100), FHOP / FS, p["attack_ms"] / 1000, p["release_ms"] / 1000)
            Lm = Lp + roomsim.level_curve_gain(det, p)
            mm = []
            for q in cv:
                sel = ok & (Lp >= q["in_db"] - 1) & (Lp < q["in_db"] + 1)
                mm.append(float(np.median(Lm[sel])) if sel.any() else np.nan)
            ax.plot([q["in_db"] for q in cv], mm, color=PAL["aqua"], marker="o", ms=3,
                    label=f"fitted {dyn['fit']['kind']} (median)")
        ax.set_xlabel("expected level at the mic, dBFS")
        ax.set_ylabel("recorded level, dBFS")
        ax.set_title("Level in vs out")
        ax.legend(loc="upper left")
        save(fig, "level_in_out.png")
    on = ctx.get("onsets")
    if on is not None:
        fps = FS / 256
        t0 = on["excerpt_t0"]
        i0, i1 = int(t0 * fps), int((t0 + 12) * fps)
        rows = [("original", on["orig"], PAL["blue"]), ("recording", on["rec"], PAL["orange"])]
        if on.get("sim") is not None:
            rows.append(("room model", on["sim"], PAL["aqua"]))
        fig, axs = plt.subplots(len(rows), 1, figsize=(9, 1.6 * len(rows) + 0.8), sharex=True)
        tx = np.arange(i0, min(i1, len(on["orig"]))) / fps
        t_off = float(on.get("t_offset") or 0.0)
        for ax, (lab, v, col) in zip(np.atleast_1d(axs), rows):
            seg = v[i0:i0 + len(tx)]
            ax.plot(tx + t_off, seg / (np.percentile(v, 99.5) + 1e-9), color=col, lw=0.8)
            ax.set_ylabel(lab)
            ax.set_ylim(0, 1.3)
            if on.get("gt") is not None:
                gtv = on["gt"][(on["gt"] >= tx[0]) & (on["gt"] <= tx[-1])]
                ax.vlines(gtv + t_off, 1.15, 1.3, color=INK2, lw=0.8)
        np.atleast_1d(axs)[0].set_title("Onset envelopes, 12 s excerpt (ticks: ground-truth beats)")
        np.atleast_1d(axs)[-1].set_xlabel("recording s")
        save(fig, "onset_envelopes.png")
    ev = ctx.get("ev") or {}
    if ev.get("_blocks") is not None:
        t, d = ev["_blocks"]
        fig, ax = plt.subplots(figsize=(9, 3.2))
        ax.plot(t, d, color=PAL["blue"], lw=1, label="recording minus original, 1 s loudness")
        ax.axhline(ev["gain_db_median"], color=INK2, ls=":", lw=1, label=f"median {ev['gain_db_median']:.1f} dB")
        for a, b in ev.get("interference_s", []):
            ax.axvspan(a, b, color=PAL["orange"], alpha=0.18, lw=0)
        for a, b in ev.get("dropouts_s", []):
            ax.axvspan(a, b, color=PAL["violet"], alpha=0.18, lw=0)
        ax.set_xlabel("recording s (orange: other sound over the music; violet: the mic dropped the music)")
        ax.set_ylabel("dB")
        ax.set_title("How loud the music arrives, second by second")
        ax.legend(loc="lower left")
        save(fig, "music_level.png")
    rc, sc = ctx.get("real_cmp"), ctx.get("sim_cmp")
    if rc and sc:
        fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(13, 3.8), gridspec_kw={"width_ratios": [2, 1.3, 1.3]})
        cen = rc["third_oct_hz"]
        a1.semilogx(cen, rc["third_oct_dbfs"], color=PAL["orange"], marker="o", ms=3, label="real recording")
        sm_ = np.mean([c_["third_oct_dbfs"] for c_ in sc], axis=0)
        a1.semilogx(cen, sm_, color=PAL["aqua"], marker="o", ms=3, label="room model (mean of seeds)")
        a1.set_xlabel("Hz")
        a1.set_ylabel("dBFS per 1/3 octave")
        a1.set_title("Level per band")
        a1.legend(loc="lower left")
        lab = [f"{lo // 1000 if lo >= 1000 else lo}{'k' if lo >= 1000 else ''}" for lo, _ in COH_OCT]
        xi = np.arange(len(COH_OCT))
        a2.bar(xi - 0.2, [v or 0 for v in rc["coherence_oct"]], 0.4, color=PAL["orange"], label="real")
        a2.bar(xi + 0.2, np.mean([[v or 0 for v in c_["coherence_oct"]] for c_ in sc], axis=0), 0.4,
               color=PAL["aqua"], label="model")
        a2.set_xticks(xi, lab)
        a2.set_ylim(0, 1)
        a2.set_xlabel("octave from (Hz)")
        a2.set_title("Coherence with the original")
        a2.legend()
        keys = ["total"] + list(BEAT_BANDS)
        xi = np.arange(len(keys))
        a3.bar(xi - 0.2, [rc["onset_r"][k] for k in keys], 0.4, color=PAL["orange"], label="real")
        a3.bar(xi + 0.2, np.mean([[c_["onset_r"][k] for k in keys] for c_ in sc], axis=0), 0.4, color=PAL["aqua"],
               label="model")
        a3.set_xticks(xi, keys)
        a3.set_ylim(0, 1)
        a3.set_title("Onsets kept (r with the original)")
        save(fig, "model_fit.png")
    al = ctx["al"]
    fig, ax = plt.subplots(figsize=(8, 3.2))
    mt = [w["tc"] for w in al["matched"]]
    ax.plot(mt, [w["resid_ms"] for w in al["matched"]], color=PAL["blue"], marker="o", ms=3, lw=0.8,
            label="matched (residual)")
    offl = [w for w in al["windows"] if w["status"] == "off_line" and w.get("off_line_s") is not None]
    if offl:
        ax.scatter([w["tc"] for w in offl], np.clip([w["off_line_s"] * 1000 for w in offl], -5, 5), color=PAL["orange"],
                   s=14, marker="x", label="off the line (clipped +-5 ms)", zorder=3)
    nm = [w["tc"] for w in al["windows"] if w["status"] in ("no_match", "quiet", "no_overlap")]
    if nm:
        ax.scatter(nm, np.zeros(len(nm)), color=MUTED, s=10, marker="|", label="no match / quiet")
    ax.set_xlabel("recording s")
    ax.set_ylabel("ms")
    ax.set_title(f"Alignment: speed {al['summary'].get('speed_pct', 0):+.3f} % (a source difference), residual per window")
    ax.legend(loc="upper right")
    save(fig, "alignment.png")
    lv = ctx.get("live")
    if lv and lv.get("available"):
        p = lv["_plot"]
        fig, (a1, a2) = plt.subplots(2, 1, figsize=(9, 5.2), sharex=True)
        for gname, col in (("beat", PAL["blue"]), ("dmx", PAL["orange"]), ("none", MUTED)):
            sel = p["gate_gt"] == gname
            if sel.any():
                a1.scatter(p["gt"][sel], np.clip(p["near"][sel] * 1000, -250, 250), s=6, color=col,
                           label=f"gate {gname}")
        a1.axhspan(-25, 25, color=GRID, alpha=0.6, lw=0)
        a1.set_ylabel("live - true beat, ms")
        a1.set_ylim(-260, 260)
        a1.legend(loc="upper right", ncol=3)
        a1.set_title("Live clock vs ground truth (band: +-25 ms)")
        a2.plot(p["gt"], p["gbpm"], color=INK2, lw=1, label="ground truth")
        a2.plot(p["gt"], np.where(p["live_bpm"] > 0, p["live_bpm"], np.nan), color=PAL["blue"], lw=1,
                label="live clock")
        a2.set_ylabel("bpm")
        a2.set_xlabel("recording s")
        a2.legend(loc="upper right")
        save(fig, "live_beats.png")
    return made


# ======================================================================================================================
# the plain-English summary
# ======================================================================================================================
def _fmt(v, fmt="{:.1f}", none="n/a"):
    return none if v is None else fmt.format(v)


def _ms(v, fmt="{:.2f}"):
    """'mean +- sd' of a model-fit cell, or the plain value."""
    if isinstance(v, dict):
        if v.get("mean") is None:
            return "n/a"
        return (fmt + " ± " + fmt).format(v["mean"], v["sd"]) if v.get("n", 1) > 1 else fmt.format(v["mean"])
    return _fmt(v, fmt)


def report_md(rep: dict) -> str:
    L = []
    run = rep["run"]
    orig = run.get("original") or {}
    al, lv, rs, rsp = rep["alignment"], rep["levels"], rep["room_sound"], rep["response"]
    pw = rsp.get("power") or {}
    ev = lv.get("events") or {}
    mf = rep.get("model_fit") or {}
    proc = (rep.get("processing") or {}).get("model") or {}
    pfit = (rep.get("processing") or {}).get("fit") or {}
    live = rep.get("live") or {}
    st = mf.get("stack") or {}
    L.append(f"# Room calibration: {run.get('stream')} ({run.get('run')})")
    L.append("")
    L.append(f"Mic: {run.get('device') or 'unknown'}. Track: {orig.get('title') or orig.get('source') or 'unknown'}."
             + (" **Synthetic test run.**" if run.get("synthetic") else ""))
    L.append(f"Music analysed: {al.get('music_s', 0):.0f} s ({sum(b - a for a, b in rep['regions']['clean_s']):.0f} s "
             f"of it clean). Alignment: {al.get('method')}. The copy that was played runs "
             f"{al.get('speed_pct', 0):+.2f} % against the fetched one (a difference between the two files, not the "
             f"room)" + (f"; the sound reaches the mic {al['acoustic_lag_ms']:.1f} ms after the PA plays it"
                         if al.get("acoustic_lag_ms") is not None else "") + ".")
    L.append("")
    L.append("## What this mic and room do to music")
    am = lv["at_mic"]
    L.append(f"- **Level.** At party volume the music reaches the mic at **{_fmt(am['short_term_median_lufs'])} LUFS** "
             f"(typical 3 s loudness; 10-90 %: {_fmt(am['short_term_p10_lufs'])} to {_fmt(am['short_term_p90_lufs'])}). "
             f"LUFS is loudness on the digital scale: a streaming master plays at about -14, so this is very quiet; "
             f"the chain loses {-lv['system_gain_db']:.0f} dB against the original as played.")
    bl = rsp.get("band_limits") or {}
    edges = []
    if bl.get("lo_hz"):
        edges.append(f"bass below about {bl['lo_hz']:.0f} Hz is cut steeply (down 20 dB by "
                     f"{_fmt(pw.get('low_20db_hz'), '{:.0f}')} Hz)")
    elif pw.get("low_6db_hz"):
        edges.append(f"bass below about {pw['low_6db_hz']:.0f} Hz is down 6 dB or more")
    if bl.get("hi_hz"):
        edges.append(f"nothing above about {bl['hi_hz'] / 1000:.1f} kHz gets through (down 20 dB by "
                     f"{_fmt((pw.get('high_20db_hz') or 0) / 1000, '{:.1f}')} kHz: a hard digital cut, the mic "
                     f"working at 16 kHz inside)")
    elif pw.get("high_6db_hz"):
        edges.append(f"treble above about {pw['high_6db_hz'] / 1000:.1f} kHz is down 6 dB or more")
    if bl.get("passband_min_db") is not None:
        edges.append(f"in between (1/3 octave in from each edge) the level stays within {bl['passband_min_db']:+.0f} "
                     f"to {bl['passband_max_db']:+.0f} dB of the 300 Hz-3 kHz level")
    tone = "; ".join(edges) if edges else "even across the range"
    L.append("- **Tone.** " + tone[:1].upper() + tone[1:] + ". A kick drum's thump is "
             "mostly under 100 Hz, so the beat's low end reaches the beat stack thin.")
    L.append(f"- **Room sound.** With no music the mic delivers {rs['rms_dbfs']:.0f} dBFS of the room's own sound "
             f"({rs['seconds']:.0f} s measured: {rs['source']}), {lv['snr_db']:.0f} dB under the music. "
             + (f"It shuts to digital silence when the room is quiet ({rs['gated_share'] * 100:.0f} % of the time; "
                f"the quietest sound it lets through is about {rs['floor_dbfs']:.0f} dBFS)."
                if rs.get("gate") else f"Its quiet floor is about {rs['floor_dbfs']:.0f} dBFS; it never gates."))
    coh = rsp.get("coherence_median_200_4k")
    scr = proc.get("scramble") or {}
    if scr.get("regions"):
        sv = scr["regions"]["sigma_db"]
        sig_txt = f"{sv[0]:.0f} dB under 250 Hz, {sv[1]:.0f} dB from 250 Hz to 4 kHz and {sv[2]:.0f} dB above"
    else:
        sig_txt = f"{scr.get('sigma_db', 0):.0f} dB"
    if scr:
        L.append(f"- **The mic's own processing.** The sound that comes out barely matches the original wave for wave "
                 f"(coherence {coh:.2f} from 200 Hz to 4 kHz, where 1 means identical and 0 unrelated): the "
                 f"speakerphone's noise suppression and echo cancelling rework it frame by frame. Even the onsets the "
                 f"beat stack feeds on survive only in part. The model stands in for this with a random gain on each "
                 f"part of the spectrum, changing every few tens of ms, typically {sig_txt}, fitted to how much the "
                 f"real recording loses.")
    elif coh is not None:
        L.append(f"- **The mic's own processing.** None that matters: the sound matches the original wave for wave "
                 f"(coherence {coh:.2f}, 200 Hz-4 kHz).")
    cav = (rep.get("ir") or {}).get("honest_caveat")
    if cav:
        L.append("- **Echo.** The room's echo can't be measured directly through this mic (its processing scrambles "
                 "the waveform, so there is no clean impulse response to read)."
                 + (f" The model gives it an echo that dies away by 60 dB in {pfit['tail']['rt60_s']:.2f} s "
                    f"(RT60), with the direct sound {pfit['tail']['drr_db']:+.0f} dB against the echo (DRR), "
                    f"because that is what makes the replay lose onsets the way the real recording does: an "
                    f"estimate, not a measurement." if pfit.get("tail") else ""))
    else:
        ir = rep["ir"]["broadband"]
        L.append(f"- **Echo.** Reverberation {_fmt(ir.get('t30_s') or ir.get('t20_s'), '{:.2f}')} s, "
                 f"direct-to-reverberant {_fmt(ir.get('drr_db'), '{:+.0f}')} dB.")
    if ev.get("interference_s"):
        L.append(f"- **Other sound over the music** at {', '.join(f'{a:.0f}-{b:.0f}' for a, b in ev['interference_s'])} s "
                 f"(recording time): louder than the music can explain (talk, a tone, typing); left out of the fit.")
    if ev.get("dropouts_s"):
        L.append(f"- **The mic dropped the music** at {', '.join(f'{a:.0f}-{b:.0f}' for a, b in ev['dropouts_s'])} s: "
                 f"{_fmt(ev.get('dropout_depth_db'), '{:.0f}')} dB down while the track played on. The model repeats "
                 f"that at the same rate ({proc.get('dropouts', {}).get('per_min', 0):.1f} a minute), but one event "
                 f"is all there is to go on.")
    L.append("")
    L.append("## What it means for beat detection")
    on = ((rep.get("beats") or {}).get("onset_envelope") or {})
    if on.get("total"):
        bands = ", ".join(f"{k} {v['r_best']:.2f}" for k, v in on.get("bands", {}).items())
        L.append(f"- **Onsets** (the drum hits our DSP listens for) keep a correlation of {on['total']['r_best']:.2f} "
                 f"with the original's (1 = untouched; by band: {bands}). The bass band carries the beat best.")
    tr = (rep.get("beats") or {}).get("trackers") or {}
    mo = tr.get("madmom_online") or {}
    if mo.get("rec"):
        L.append(f"- **madmom** (the live clock's beat network, 2 nets, online): its beat activation on the recording "
                 f"correlates {mo['rec']['r']:.2f} with the original's; its beats score F = "
                 f"{_fmt((mo['rec'].get('beats_vs_gt') or {}).get('f_measure'), '{:.2f}')} "
                 f"(F: 1 = every beat found within 70 ms, nothing extra).")
    for gk, g in sorted(st.items()):
        if not gk.startswith("gate_"):
            continue
        gv = int(gk.split("_")[1])
        real = {k: (v or {}).get("real") for k, v in g.items()}
        locked = real.get("locked")
        mus = am.get("short_term_median_lufs")
        lab = (" (dsp's default)" if gv == -30 else
               f" (about the music's measured {mus:.1f})" if mus is not None and abs(gv - mus) <= 2.0 else "")
        L.append(f"- **Our beat stack** on the recording, loudness gate reference {gv} LUFS{lab}: "
                 + (f"locks after {real['t_lock']:.1f} s; " if locked and real.get("t_lock") is not None else
                    "never locks; ")
                 + f"{(real.get('hit25') or 0) * 100:.0f} % of beats flashed within 25 ms, "
                   f"{(real.get('hit70_gated') or 0) * 100:.0f} % flashed under the beat gate within 70 ms.")
    if st:
        L.append(f"- So the gate's reference must sit near the music's real loudness at this mic "
                 f"({_fmt(am['short_term_median_lufs'], '{:.0f}')} LUFS, in config/room/audio_cal.json "
                 f"summary.music_lufs): at -30 dsp hears the music as quiet and the clock waits.")
    if live.get("available"):
        o = live["overall"]
        sh = live.get("stream_health") or {}
        L.append(f"- **The live clock during the calibration**: F = {_fmt(o.get('f_measure'), '{:.2f}')}, "
                 f"{live['hit_rate_25ms'] * 100:.0f} % of beats within 25 ms, beat gate on for "
                 f"{live['coverage_beat'] * 100:.0f} % of the beats."
                 + (f" The live stream lost {sh['lost_share'] * 100:.0f} % of its packets while the music played "
                    f"({_fmt(sh.get('segments'), '{:.0f}')} restarts): that, not the room alone, is in this number."
                    if sh.get("lost_share") else ""))
    elif live:
        L.append(f"- The live clock: not scored ({live.get('reason')}).")
    L.append("")
    L.append("## How faithful the room model is")
    L.append(f"The original was played through the model (roomsim.py) with {len(mf.get('seeds', []))} different "
             f"room-sound and processing seeds and compared with the real recording on the clean music.")
    L.append("")
    L.append("| what | real | model |")
    L.append("|---|---|---|")

    def tr_row(label, cell, fmt="{:.2f}"):
        if cell:
            L.append(f"| {label} | {_fmt(cell.get('real'), fmt)} | {_ms(cell.get('sim'), fmt)} |")

    tr_row("loudness, typical 3 s (LUFS)", mf.get("loudness_short_term_median_lufs"), "{:.1f}")
    tr_row("loudness, quiet 10 % (LUFS)", mf.get("loudness_short_term_p10_lufs"), "{:.1f}")
    tr_row("loudness, loud 10 % (LUFS)", mf.get("loudness_short_term_p90_lufs"), "{:.1f}")
    for k, lab in (("low_6db_hz", "bass cut, -6 dB (Hz)"), ("low_20db_hz", "bass cut, -20 dB (Hz)"),
                   ("high_6db_hz", "treble cut, -6 dB (Hz)"), ("high_20db_hz", "treble cut, -20 dB (Hz)")):
        tr_row(lab, (mf.get("edges_hz") or {}).get(k), "{:.0f}")
    be = mf.get("band_level_error_db") or {}
    if be:
        L.append(f"| band levels 100 Hz-8 kHz, mean error | | {be['mean_abs_100_8k']:.1f} dB (worst "
                 f"{be['max_abs_100_8k']:.1f}) |")
    for k, v in (mf.get("coherence_oct") or {}).items():
        if k in ("250-500", "1000-2000"):
            tr_row(f"coherence {k} Hz", v)
    for k in ("total", "bass", "mid", "high"):
        tr_row(f"onsets kept, {k}", (mf.get("onset_r") or {}).get(k))
    tr_row("madmom online: activation vs original", mf.get("madmom_online_act_r"))
    tr_row("madmom online: beats F", mf.get("madmom_online_beats_f"))
    tr_row("madmom online: beats within 25 ms", mf.get("madmom_online_beats_hit25"))
    for gk, g in sorted(st.items()):
        if not gk.startswith("gate_"):
            continue
        gv = gk.split("_")[1]
        tr_row(f"our stack, gate {gv}: within 25 ms", g.get("hit25"))
        tr_row(f"our stack, gate {gv}: gated within 70 ms", g.get("hit70_gated"))
        lk = g.get("locked") or {}
        runs = lk.get("sim_runs") or []
        tl = g.get("t_lock") or {}
        sim_t = [v for v in (tl.get("sim_runs") or []) if v is not None]
        L.append(f"| our stack, gate {gv}: locks | {('after ' + format(tl.get('real'), '.1f') + ' s') if lk.get('real') else 'never'} | "
                 f"{sum(bool(v) for v in runs)} of {len(runs)} runs"
                 + (f" (after {', '.join(format(v, '.1f') for v in sim_t)} s)" if sim_t else "") + " |")
    L.append("")
    if pfit.get("sigma_db"):
        sg = pfit["sigma_db"]
        tl = pfit.get("tail")
        L.append("Fitted: " + (f"room tail RT60 {tl['rt60_s']:.2f} s, DRR {tl['drr_db']:+.0f} dB; " if tl else "")
                 + f"processing sigma {sg.get('low', 0):.1f} / {sg.get('mid', 0):.1f} / {sg.get('high', 0):.1f} dB "
                   f"(under 250 Hz / to 4 kHz / above); fit error {pfit.get('error', 0):.3f} against "
                   f"{pfit.get('error_linear', 0):.3f} for the linear chain and room sound alone.")
    L.append("")
    L.append("Figures: analysis/figs/. Every number: analysis/report.json. Model: model/model.json (roomsim.py).")
    return "\n".join(L) + "\n"


# ======================================================================================================================
# the whole analysis
# ======================================================================================================================
def _lock():
    import fcntl
    CPU_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fh = open(CPU_LOCK, "a")
    fcntl.flock(fh, fcntl.LOCK_EX)
    return fh


def write_config(run_dir: pathlib.Path, rep: dict, model_path: pathlib.Path) -> None:
    try:
        cfg = json.loads(CONFIG_JSON.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            cfg = {}
    except (OSError, ValueError):
        cfg = {}
    cfg.setdefault("streams", {})
    lv, rs, pr = rep["levels"], rep["room_sound"], rep["response"].get("power") or {}
    mf = rep.get("model_fit") or {}
    live = rep.get("live") or {}
    summary = {
        "music_lufs": lv["at_mic"]["short_term_median_lufs"],
        "music_lufs_note": "the music at the mic as dsp's loudness gate reads it: median short-term (3 s) loudness "
                           "while the calibration track played (party volume); integrated: music_lufs_integrated",
        "music_lufs_p10": lv["at_mic"]["short_term_p10_lufs"], "music_lufs_p90": lv["at_mic"]["short_term_p90_lufs"],
        "music_lufs_integrated": lv["at_mic"]["integrated_lufs"],
        "music_rms_dbfs": lv["rec_music"]["rms_dbfs"], "system_gain_db": lv["system_gain_db"],
        "room_sound_dbfs": rs["rms_dbfs"], "room_sound_lufs": rs["lufs"], "music_over_room_sound_db": lv["snr_db"],
        "floor_dbfs": rs["floor_dbfs"], "gated_share": rs["gated_share"],
        "low_6db_hz": pr.get("low_6db_hz"), "high_6db_hz": pr.get("high_6db_hz"),
        "high_20db_hz": pr.get("high_20db_hz"), "acoustic_lag_ms": rep["alignment"].get("acoustic_lag_ms"),
        "coherence_median_200_4k": rep["response"].get("coherence_median_200_4k"),
        "processing": rep["model"].get("processing"),
        "onset_r": ((rep.get("beats") or {}).get("onset_envelope") or {}).get("total", {}).get("r_best"),
        "live_f": (live.get("overall") or {}).get("f_measure") if live.get("available") else None,
        "stack_real": (mf.get("stack") or {}).get("real"),
    }
    try:
        rel_model = str(model_path.relative_to(REPO))
    except ValueError:
        rel_model = str(model_path)
    cfg["streams"][rep["run"]["stream"]] = {
        "run": rep["run"]["run"], "measured_at": rep["run"].get("started_utc") or rep["analysed_utc"],
        "device": rep["run"].get("device"), "summary": _clean(summary), "model": rel_model}
    tmp = CONFIG_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, CONFIG_JSON)


def analyse(run_dir: pathlib.Path, write_config: bool = True, *, cpu_lock: bool = False, skip_beats: bool = False,
            stack: bool = True, stack_gates=None, seeds=VAL_SEEDS, log=None) -> dict:
    """Analyse one calibration run directory (see the module docstring) -> the report dict (also written).
    ``stack`` False skips the beat-stack replays; ``stack_gates`` (default: dsp's -30 and the measured music
    loudness) are the gate references they run at; ``seeds`` the model replays' room-sound/processing seeds."""
    log = log or (lambda s: print(s, file=sys.stderr, flush=True))
    run_dir = pathlib.Path(run_dir).resolve()
    lock = _lock() if cpu_lock else None
    try:
        try:
            os.nice(19 - os.nice(0))            # heavy: always at the back of the queue
        except OSError:
            pass
        return _analyse(run_dir, write_config, skip_beats, log, stack=stack, stack_gates=stack_gates,
                        seeds=tuple(seeds))
    finally:
        if lock is not None:
            lock.close()


def _model_dict(run, run_dir, recm, ref_lufs, ir_spec, resp, noise, levels, dyn_model, gate_model, processing,
                lag) -> dict:
    return {
        "version": VERSION, "run": run.get("run", run_dir.name), "stream": run.get("stream"),
        "device": run.get("device") or recm.get("device"), "measured_at": run.get("started_utc"),
        "synthetic": bool(run.get("synthetic")), "fs": FS,
        "reference": {"lufs": ref_lufs,
                      "note": "the calibration original's loudness as played (BS.1770 integrated, mono mix): "
                              "roomsim scales its input to this before the IR, so the measured gain applies "
                              "(the PA at the calibration's volume)"},
        "ir": ir_spec,
        "response": resp,
        "noise": {"file": "noise.wav", "rms_dbfs": noise["summary"]["rms_dbfs"], "lufs": noise["summary"]["lufs"],
                  "source": noise["summary"]["source"], "seconds": noise["summary"]["noise_wav_s"],
                  "note": "the room's own sound as the mic delivered it with no music (events included), looped"},
        "music": {"short_term_median_lufs": levels["at_mic"]["short_term_median_lufs"],
                  "rms_dbfs": levels["rec_music"]["rms_dbfs"]},
        "snr_db": levels["snr_db"],
        "dynamics": dyn_model,
        "spectral_gate": gate_model,
        "processing": processing,
        "quantise": "int16",
        "system_delay": {"acoustic_lag_ms": lag, "note": "measured, not applied: roomsim keeps the input's timeline "
                                                         "(run_stack.py --align-xcorr takes a delay out if needed)"},
    }


def _write_model(mdir: pathlib.Path, model: dict, ir: np.ndarray, noise_clip: np.ndarray | None = None,
                 band_limit: np.ndarray | None = None) -> pathlib.Path:
    sf.write(str(mdir / "ir.wav"), np.asarray(ir, dtype=np.float32), FS, subtype="FLOAT")
    if noise_clip is not None:
        sf.write(str(mdir / "noise.wav"), np.asarray(noise_clip, dtype=np.float32), FS, subtype="FLOAT")
    if band_limit is not None:
        sf.write(str(mdir / "band_limit.wav"), np.asarray(band_limit, dtype=np.float32), FS, subtype="FLOAT")
    path = mdir / "model.json"
    path.write_text(json.dumps(_clean(model), indent=1) + "\n", encoding="utf-8")
    roomsim._load.cache_clear()
    return path


def compare(sig: np.ndarray, x_ref: np.ndarray, clean, noise: dict, ob_x: dict | None = None) -> dict:
    """One signal (the real recording, or a model replay) against the original, inside the clean spans: what the
    model has to reproduce."""
    smp = [(int(a * FS), int(b * FS)) for a, b in clean]
    cen, lv = third_oct_spans(sig, smp)
    pr = power_response(sig, x_ref, smp, noise)
    _, p10 = roomsim.frame_power(np.concatenate([sig[a:b] for a, b in smp]), 480, 480)
    return {"loudness": music_loudness(sig, clean), "third_oct_hz": cen, "third_oct_dbfs": lv,
            "edges": {k: pr.get(k) for k in ("low_6db_hz", "low_20db_hz", "high_6db_hz", "high_20db_hz")},
            "coherence_oct": coherence_spans(sig, x_ref, smp),
            "onset_r": onset_r_spans(sig, x_ref, clean, ob_x),
            "gated_10ms_share": float(np.mean(_db(p10) < GATED_DBFS))}


def _analyse(run_dir: pathlib.Path, do_config: bool, skip_beats: bool, log, *, stack: bool = True,
             stack_gates=None, seeds=VAL_SEEDS) -> dict:
    t_start = time.time()
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    try:
        recm = json.loads((run_dir / "rec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        recm = {}
    orig_info = run.get("original") or {}
    if not orig_info.get("wav"):
        raise RuntimeError("run.json has no original.wav yet: fetch the original first (calibrate.py)")
    rec, rec_fs, _ = read_audio(run_dir / "rec.wav")
    orig, orig_fs, orig_ch = read_audio(run_dir / orig_info["wav"])
    n = len(rec)
    log(f"analyse {run_dir.name}: recording {n / FS:.1f} s, original {len(orig) / FS:.1f} s ({orig_ch} ch)")
    ana, mdir = run_dir / "analysis", run_dir / "model"
    work = ana / "work"
    for d in (ana, mdir, work):
        d.mkdir(exist_ok=True)

    # 1. alignment, and where the music is
    music = run.get("music") if isinstance(run.get("music"), dict) else None
    if music and not (run_dir / (music.get("files") or {}).get("original", "original/original-matched.wav")).is_file():
        music = None
    if music:
        al = align_from_run(rec, orig, run_dir, music, log)
        ms = max(float(music["rec_t0"]), al["summary"]["track_starts_s"])
        me = min(float(music["rec_t1"]), n / FS)
        reg = {"music_start_s": ms, "music_end_s": me, "source": "run.json music",
               "pre_roll": [0.2, max(0.2, ms - 0.3)], "post_roll": [min(n / FS, me + 0.5), n / FS - 0.05]}
    else:
        al = align(rec, orig, log)
        al["offset_s"] = al["summary"]["offset_s"]
        reg = find_regions(rec, al, len(orig) / FS)
        ms, me = reg["music_start_s"], reg["music_end_s"]
    c0, c1 = al["c0"], al["c1"]
    lag_ms = (al["summary"].get("acoustic_lag") or {}).get("lag_ms")
    al["summary"]["acoustic_lag_ms"] = lag_ms
    x_played = al.pop("x_al")
    i_ms, i_me = int(ms * FS), int(me * FS)
    x_played[:i_ms] = 0.0
    x_played[i_me:] = 0.0
    fd = int(0.01 * FS)
    x_played[i_ms:i_ms + fd] *= np.linspace(0, 1, len(x_played[i_ms:i_ms + fd]))
    x_played[max(0, i_me - fd):i_me] *= np.linspace(1, 0, len(x_played[max(0, i_me - fd):i_me]))
    if music:
        spans = [(ms + 0.3, me - 0.3)]
    else:
        spans = []
        for w in al["matched"]:
            s0, s1 = max(w["t0"], ms), min(w["t1"], me)
            if s1 <= s0:
                continue
            if spans and s0 <= spans[-1][1]:
                spans[-1][1] = max(spans[-1][1], s1)
            else:
                spans.append([s0, s1])
        spans = [tuple(s) for s in spans]
    al["summary"]["music_s"] = me - ms
    al["summary"]["matched_s"] = sum(b - a for a, b in spans)
    al["summary"]["matched_fraction"] = al["summary"]["matched_s"] / max(1e-9, me - ms)
    seek_spans = [(w["t0"], w["t1"]) for w in (al.get("bands") or {}).get("windows", []) if w["status"] == "seek"]

    # 2. room sound, levels, events in the music
    noise = analyse_room_sound(rec, reg, seek_spans, [(ms, me)])
    ev = music_events(rec, x_played, spans)
    clean = ev["clean_s"] or list(spans)
    clean_smp = [(int(a * FS), int(b * FS)) for a, b in clean]
    levels = analyse_levels(rec, x_played, reg, noise, spans)
    levels["at_mic"] = music_loudness(rec, clean)
    levels["original_as_played_clean"] = music_loudness(x_played, clean)
    kw = lambda y: float(np.mean(np.concatenate([roomsim.k_weight(y[a:b]) ** 2 for a, b in clean_smp])))  # noqa
    p_rec, p_org = kw(rec), kw(x_played)
    p_bed = float(np.mean(roomsim.k_weight(noise["x"]) ** 2))
    levels["system_gain_db_raw"] = levels["system_gain_db"]
    levels["system_gain_db"] = float(_db(max(p_rec - p_bed, 0.05 * p_rec)) - _db(p_org))
    levels["system_gain_note"] = ("music at the mic over the original as played, K-weighted, clean spans, the room "
                                  "sound's power taken off; system_gain_db_raw: integrated loudness difference over "
                                  "the whole music, other sounds included")
    p_rec_u = float(np.mean(np.concatenate([rec[a:b] for a, b in clean_smp]) ** 2))
    levels["snr_db"] = float(_db(max(p_rec_u - noise["p_noise"], EPS)) - _db(noise["p_noise"]))
    levels["snr_note"] = "music power over the room sound's (unweighted RMS), clean spans"
    levels["events"] = {k: v for k, v in ev.items() if not k.startswith("_")}
    log(f"levels: music at the mic {levels['at_mic']['short_term_median_lufs']:.1f} LUFS (3 s median), gain "
        f"{levels['system_gain_db']:.1f} dB, room sound {noise['summary']['rms_dbfs']:.1f} dBFS "
        f"({levels['snr_db']:.1f} dB under the music); events: interference {ev['interference_s']}, "
        f"dropouts {ev['dropouts_s']}")

    # 3. linear chain: H1 + coherence + IR (diagnostic), and the power response (the model's, when H1 can't be)
    lin = analyse_linear(x_played, rec, clean_smp, log)
    pr = power_response(rec, x_played, clean_smp, noise)
    coh_med = lin["response"]["coherence_median_200_4k"]
    use_measured_ir = bool(lin["reliable"] and coh_med >= 0.5)
    ex = pr["excited"]
    resp_f, resp_db = pr["centres"][ex], pr["mag_db"][ex]
    split = split_response(resp_f, resp_db) if not use_measured_ir else {"pre_db": resp_db, "post_db": None,
                                                                          "lo_hz": None, "hi_hz": None}
    has_bl = split["post_db"] is not None and (split["lo_hz"] or split["hi_hz"])

    def fir(mag_db):
        return roomsim.minphase_fir(resp_f, mag_db, FS, n_fft=32768, taps=8192)

    def build_ir(pre_db):
        """(the model's room filter, its kind, pre-samples): the measured IR for a clean mic, else a minimum-phase
        FIR of the passband part of the power response (the band limits go after the processing)."""
        if use_measured_ir:
            return lin["ir_model"], "measured", lin["pre"]
        kind = "minphase (power response" + (", passband)" if has_bl else ")")
        if tail:
            return (roomsim.synth_ir(resp_f, pre_db, tail["rt60_s"], tail["drr_db"], FS).astype(np.float64),
                    kind + " + fitted diffuse tail", 0)
        return fir(pre_db), kind, 0

    tail = None                                                  # fitted with the processing (step 5)
    ir_model, ir_kind, ir_pre = build_ir(split["pre_db"] if has_bl else resp_db)
    band_limit = fir(split["post_db"]) if has_bl else None

    def predict(ir, pre):
        y = sps.oaconvolve(x_played, ir)[pre:pre + n]
        if band_limit is not None:
            y = sps.oaconvolve(y, band_limit)[:n]
        return np.pad(y, (0, n - len(y))) if len(y) < n else y

    pred = predict(ir_model, ir_pre)
    log(f"response: {ir_kind}; coherence 200 Hz-4 kHz {coh_med:.2f}; power response -6 dB at "
        f"{_fmt(pr.get('low_6db_hz'), '{:.0f}')} / {_fmt(pr.get('high_6db_hz'), '{:.0f}')} Hz, -20 dB at "
        f"{_fmt(pr.get('low_20db_hz'), '{:.0f}')} / {_fmt(pr.get('high_20db_hz'), '{:.0f}')} Hz; band limits "
        f"{_fmt(split['lo_hz'], '{:.0f}')} / {_fmt(split['hi_hz'], '{:.0f}')} Hz")

    # 4. dynamics and the octave-band gate (level-dependent behaviour), residual
    dyn = analyse_dynamics(pred, rec, noise, clean, log)
    if dyn.get("t") is not None:
        t_d, ok_d = dyn["t"], dyn["ok"]
        ok_fn = lambda tq: np.interp(tq, t_d, ok_d.astype(float)) > 0.5   # noqa: E731
    else:
        ok_fn = lambda tq: in_spans(tq, clean)                            # noqa: E731
    gate = analyse_gate(pred, rec, noise, clean, ok_fn)
    resid = analyse_residual(pred, rec, dyn, noise, clean_smp)

    # 5. the mic's processing (fitted on the music section only)
    sa, sb = section(spans, 1.0, n)
    rel = lambda sp: [(a - sa / FS, b - sa / FS) for a, b in sp]       # noqa: E731
    x_sec, rec_sec = x_played[sa:sb], rec[sa:sb]
    clean_rel = rel(clean)
    if use_measured_ir:
        proc_fit = {"used": False, "note": "a clean mic: the measured impulse response carries the room"}
    else:
        proc_fit = fit_processing(x_sec, rec_sec, noise["clip"], clean_rel, log, resp_f,
                                  split["pre_db"] if has_bl else resp_db, post=band_limit)
    processing = {}
    if proc_fit["used"]:
        if proc_fit["params"]:
            processing["scramble"] = proc_fit["params"]
        tail = proc_fit["tail"]
        if tail:
            ir_model, ir_kind, ir_pre = build_ir(split["pre_db"] if has_bl else resp_db)
    if noise.get("gate"):
        g_ = noise["gate"]
        processing["gate"] = {"thr_dbfs": g_["thr_dbfs"], "hold_ms": g_["hold_ms"], "depth_db": g_["depth_db"]}
    music_min = max(1e-9, (me - ms) / 60.0)
    if ev["dropouts_s"]:
        lens = [b - a - 1.0 for a, b in ev["dropouts_s"]]          # the padding off
        processing["dropouts"] = {"per_min": len(lens) / music_min, "mean_s": float(max(0.3, np.mean(lens))),
                                  "depth_db": ev["dropout_depth_db"], "observed": len(lens),
                                  "note": f"{len(lens)} seen in {me - ms:.0f} s of music: the rate is that uncertain"}
    processing = processing or None
    # A level curve / octave gate fitted on top of a scrambled, room-sound-laden recording mostly fits that; they go
    # in the model only when the processing stand-in isn't used (a plain mic).
    dyn_model = dyn.get("model") if not (processing and processing.get("scramble")) else None
    gate_model = gate.get("model") if not (processing and processing.get("scramble")) else None

    # 6. the model, then one correction round: the replay's band levels and loudness against the real recording's
    ref_lufs = _f(roomsim.lufs(x_played[i_ms:i_me]), 3)
    ir_spec = {"file": "ir.wav", "kind": ir_kind, "pre_samples": ir_pre,
               "rt60_s": lin["rt60"] if use_measured_ir else (tail or {}).get("rt60_s"),
               "drr_db": lin["m_w"]["drr_db"] if use_measured_ir else (tail or {}).get("drr_db"),
               "tail": "measured" if use_measured_ir else ("fitted to onset and coherence loss" if tail else None),
               "length_ms": len(ir_model) / FS * 1000.0}
    resp = {"freqs_hz": resp_f, "mag_db": resp_db, "source": "measured IR" if use_measured_ir else "power response",
            "passband_db": split["pre_db"], "band_limit_db": split["post_db"],
            "coherence": np.interp(np.log(resp_f), np.log(lin["centres"]), lin["coh_s"])}
    model = _model_dict(run, run_dir, recm, ref_lufs, ir_spec, resp, noise, levels, dyn_model, gate_model,
                        processing, lag_ms)
    if band_limit is not None:
        model["band_limit"] = {"file": "band_limit.wav", "lo_hz": split["lo_hz"], "hi_hz": split["hi_hz"],
                               "note": "the mic's own filters, applied after its processing"}
    model_path = _write_model(mdir, model, ir_model, noise["clip"], band_limit)
    ob_x = onset_bands(x_sec)
    real_cmp = compare(rec_sec, x_sec, clean_rel, noise, ob_x)
    x_in = x_played[i_ms:i_me]                                    # the original as played, its own timeline
    off = i_ms - sa                                               # x_in sample 0 sits at section sample off

    def replay(seed, no_drop=False):
        """The original as played through the model as it stands on disk, on the section's timeline. no_drop: without
        the dropouts (the level fit compares clean spans)."""
        m_path = mdir / "model.json"
        if no_drop and processing and processing.get("dropouts"):
            m2 = json.loads(m_path.read_text(encoding="utf-8"))
            m2["processing"] = {k: v for k, v in m2["processing"].items() if k != "dropouts"}
            m2["ir"]["file"] = str(mdir / "ir.wav")
            m2["noise"]["file"] = str(mdir / "noise.wav")
            if m2.get("band_limit"):
                m2["band_limit"]["file"] = str(mdir / "band_limit.wav")
            m_path = work / "model_nodrop.json"
            m_path.write_text(json.dumps(m2), encoding="utf-8")
            roomsim._load.cache_clear()
        y, _ = roomsim.apply(x_in, FS, m_path, seed=seed)
        out = np.zeros(sb - sa)
        out[off:off + len(y)] = y[:len(out) - off]
        return out

    corr = None
    if not use_measured_ir:
        sims = [replay(sd, no_drop=True) for sd in seeds[:2]]
        cmp0 = [compare(s_, x_sec, clean_rel, noise, ob_x) for s_ in sims]
        err_b = np.mean([c_["third_oct_dbfs"] for c_ in cmp0], axis=0) - real_cmp["third_oct_dbfs"]
        err_l = float(np.mean([c_["loudness"]["short_term_median_lufs"] for c_ in cmp0])
                      - real_cmp["loudness"]["short_term_median_lufs"])
        cen = real_cmp["third_oct_hz"]
        # take the band error off the passband filter inside the band limits (1/3-octave error interpolated on log
        # frequency; at most +-10 dB), then the remaining loudness error off the gain
        lo_c, hi_c = split["lo_hz"] or 0.0, split["hi_hz"] or 1e9
        e_f = np.interp(np.log(resp_f), np.log(cen), np.clip(err_b, -10, 10))
        e_f = np.where((resp_f >= lo_c) & (resp_f <= hi_c), e_f, np.interp(np.clip(resp_f, lo_c, hi_c), resp_f, e_f))
        pre2 = (split["pre_db"] if has_bl else resp_db) - e_f
        ir2, _, _ = build_ir(pre2)
        model_path = _write_model(mdir, model, ir2)
        sims = [replay(sd, no_drop=True) for sd in seeds[:2]]
        e2 = float(np.mean([music_loudness(s_, clean_rel)["short_term_median_lufs"] for s_ in sims])
                   - real_cmp["loudness"]["short_term_median_lufs"])
        ir_model = ir2 * 10 ** (-e2 / 20.0)
        resp["passband_db"] = pre2 - e2
        resp["mag_db"] = resp["passband_db"] + (split["post_db"] if has_bl else 0.0)
        model["response"] = resp
        model_path = _write_model(mdir, model, ir_model)
        corr = {"third_oct_hz": cen, "band_error_before_db": err_b, "loudness_error_before_db": err_l,
                "gain_step_db": -e2}
        log(f"model: corrected band levels (mean |error| {np.mean(np.abs(err_b)):.1f} dB before) and loudness "
            f"({err_l:+.1f} dB before, {e2:+.1f} after the band step)")

    # 7. the model's replays against the real recording (several seeds: the room sound and processing are random)
    sims = [replay(sd) for sd in seeds]
    sim_cmp = [compare(s_, x_sec, clean_rel, noise, ob_x) for s_ in sims]
    log("model: written; replayed the original through it " + f"(seeds {', '.join(map(str, seeds))})")

    # onset survival on the whole music (for the report's beats section, real vs the first replay)
    fps_o = FS / 256
    ob_r, ob_s = onset_bands(rec_sec), onset_bands(sims[0])
    nfr = min(len(ob_x["total"]), len(ob_r["total"]), len(ob_s["total"]))
    m_o = in_spans(np.arange(nfr) / fps_o, clean_rel)
    onset = {"fps": fps_o, "bands": {}, "spans": "clean"}
    maxlag = int(0.04 * fps_o)
    for key in ["total"] + list(BEAT_BANDS):
        r0 = _pearson(ob_x[key][:nfr][m_o], ob_r[key][:nfr][m_o])
        rb, lg = _best_lag_corr(ob_x[key][:nfr], ob_r[key][:nfr], maxlag, m_o)
        rs, lgs = _best_lag_corr(ob_x[key][:nfr], ob_s[key][:nfr], maxlag, m_o)
        ent = {"r0": r0, "r_best": rb, "lag_ms": lg / fps_o * 1000, "r_sim_best": rs, "lag_sim_ms": lgs / fps_o * 1000}
        if key == "total":
            onset["total"] = ent
        else:
            onset["bands"][key] = ent
    on_o = peak_pick(ob_x["total"][:nfr] * m_o, fps_o)
    on_r = peak_pick(ob_r["total"][:nfr] * m_o, fps_o)
    import mir_eval
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        f0 = mir_eval.onset.f_measure(on_o, on_r, window=0.05)[0] if len(on_o) and len(on_r) else None
    onset_f = {"n_orig": len(on_o), "n_rec": len(on_r), "f_measure": f0}

    # 8. trackers: ground truth on the whole original; what survives in the recording and the replays (sections)
    beats = {"onset_envelope": onset, "onset_f": onset_f}
    gt_rep, gt_rec, trk, rec_hits = {}, None, None, None
    to_rec = lambda u: (np.asarray(u) - c0) / c1                         # noqa: E731
    has_bus = (run_dir / "bus.jsonl").is_file()
    if not skip_beats:
        sec_sigs = {"orig": x_sec, "rec": rec_sec, **{f"sim{sd}": s_ for sd, s_ in zip(seeds, sims)}}
        jobs = {"madmom": {"orig_full": orig, "rec": rec_sec, f"sim{seeds[0]}": sims[0]},
                "beatthis": {"orig_full": orig, "rec": rec_sec, f"sim{seeds[0]}": sims[0]},
                "madmom_online": sec_sigs}
        if has_bus:
            jobs["dsphits"] = {"rec_full": rec}
        trk = run_trackers(jobs, work, log)
        beats["tracker_errors"] = trk["errors"]
        rec_hits = {k: np.asarray(v) for k, v in ((trk.get("dsphits") or {}).get("rec_full") or {}).get("hits", {}).items()}
        mm_o, bt_o = trk["madmom"].get("orig_full"), trk["beatthis"].get("orig_full")
        if mm_o or bt_o:
            gt_src = "madmom" if mm_o else "beatthis"
            gtb = np.asarray((mm_o or bt_o)["beats"])
            gt_rec_all = to_rec(gtb)
            gt_rec = gt_rec_all[(gt_rec_all >= ms) & (gt_rec_all <= me)]
            agree = score_beats(np.asarray(mm_o["beats"]), np.asarray(bt_o["beats"])) if mm_o and bt_o else None
            gt_rep = {"primary": gt_src, "note": "madmom offline (8 nets + DBN) on the whole original, the reference "
                                                 "runs' choice; Beat This! as the cross-check",
                      "agreement_madmom_vs_beatthis": agree, "n_beats_music": int(len(gt_rec)),
                      "bpm_median": float(60.0 / np.median(np.diff(gtb))) if len(gtb) > 2 else None}
            ref_mic = run_dir / "reference" / "beats-at-mic.beats"
            if ref_mic.is_file():
                rb_ = np.array([float(ln.split()[0]) for ln in ref_mic.read_text().splitlines() if ln.strip()])
                gt_rep["vs_reference_beats_at_mic"] = score_beats(rb_ + float(music["rec_t0"]) if music else rb_,
                                                                  gt_rec)
            (ana / "beats.json").write_text(json.dumps(_clean({
                "c0": c0, "c1": c1, "note": "recording s = (original s - c0) / c1 (the original as heard at the mic)",
                "music_s": [ms, me], "clean_spans_s": clean,
                "madmom": {"beats_orig_s": mm_o["beats"], "beats_rec_s": to_rec(mm_o["beats"])} if mm_o else None,
                "beatthis": {"beats_orig_s": bt_o["beats"], "downbeats_orig_s": bt_o["downbeats"],
                             "beats_rec_s": to_rec(bt_o["beats"]), "downbeats_rec_s": to_rec(bt_o["downbeats"])}
                if bt_o else None})) + "\n", encoding="utf-8")
        # activations against the original's (section timeline, clean spans), best lag within 50 ms
        gt_sec = gt_rec - sa / FS if gt_rec is not None else None
        acts = {}
        for tr, fps_a in (("madmom", 100.0), ("beatthis", 50.0), ("madmom_online", 100.0)):
            res_t = trk.get(tr) or {}
            if tr == "madmom_online":
                ao = (res_t.get("orig") or {}).get("act_data")
            else:
                o_ = res_t.get("orig_full")
                if not o_:
                    continue
                ao_full = o_["act_data"] if tr == "madmom" else 1.0 / (1.0 + np.exp(-o_["act_data"][0]))
                nfa = int(len(rec_sec) / FS * fps_a)
                u = c0 + c1 * (sa / FS + np.arange(nfa) / fps_a)
                ao = np.interp(u * fps_a, np.arange(len(ao_full)), ao_full, left=0.0, right=0.0)
            if ao is None:
                continue
            for nm in ["rec"] + [f"sim{sd}" for sd in seeds]:
                e_ = res_t.get(nm)
                if not e_:
                    continue
                a_ = e_["act_data"] if tr != "beatthis" else 1.0 / (1.0 + np.exp(-e_["act_data"][0]))
                k_ = min(len(a_), len(ao))
                m_a = in_spans(np.arange(k_) / fps_a, clean_rel)
                r_b, l_b = _best_lag_corr(ao[:k_], a_[:k_], int(0.05 * fps_a), m_a)
                ent = {"r": r_b, "lag_ms": l_b / fps_a * 1000}
                if gt_sec is not None and e_.get("beats"):
                    ent["beats_vs_gt"] = score_beats(gt_sec[(gt_sec >= 0)], np.asarray(e_["beats"]))
                acts.setdefault(tr, {})[nm] = ent
            if tr == "madmom_online" and gt_sec is not None and (res_t.get("orig") or {}).get("beats"):
                acts[tr]["orig"] = {"beats_vs_gt": score_beats(gt_sec[gt_sec >= 0], np.asarray(res_t["orig"]["beats"]))}
        beats["trackers"] = acts
        beats["madmom_act_r"] = (acts.get("madmom", {}).get("rec") or {}).get("r")
        beats["madmom_online_act_r"] = (acts.get("madmom_online", {}).get("rec") or {}).get("r")

    # 9. the live clock during the calibration
    live = None
    if has_bus or (run_dir / "state.jsonl").is_file():
        if gt_rec is not None:
            try:
                live = analyse_live(run_dir, run.get("stream", ""), rec, gt_rec, spans, log, rec_hits)
            except Exception as e:                    # never lose the rest of the report to the live section
                live = {"available": False, "reason": f"error: {type(e).__name__}: {e}"[:300]}
        else:
            live = {"available": False, "reason": "no ground truth (beat trackers skipped or failed)"}

    # 10. our beat stack on the real recording and the replays, at dsp's default gate and at the music's loudness
    stack_res = None
    if stack and not skip_beats and gt_rec is not None and RUN_STACK.is_file():
        gates = list(stack_gates) if stack_gates else [-30.0, float(round(levels["at_mic"]["short_term_median_lufs"]))]
        gt_path = work / "gt_section.beats"
        write_beats(gt_path, gt_sec[gt_sec >= 0])
        files = {"real": work / "real_section.wav"}
        sf.write(str(files["real"]), rec_sec.astype(np.float32), FS, subtype="FLOAT")
        for sd, s_ in zip(seeds, sims):
            files[f"sim{sd}"] = work / f"sim{sd}_section.wav"
            sf.write(str(files[f"sim{sd}"]), s_.astype(np.float32), FS, subtype="FLOAT")
        cases = [{"id": f"{nm}_g{abs(int(g))}", "wav": p_, "beats": gt_path, "gate": g}
                 for g in gates for nm, p_ in files.items()]
        stack_res = {"gates": gates, "runs": run_stack_cases(cases, work, log),
                     "settings": "run_stack.py --no-slow-lane --preroll-s 0 (the reference runs' settings); the "
                                 "music section only; ground truth = madmom offline beats as heard at the mic"}

    # model fit table: the real recording next to the replays
    def mean_sd(vals):
        v = np.asarray([x for x in vals if x is not None and np.isfinite(x)], dtype=float)
        return {"mean": float(v.mean()), "sd": float(v.std()), "n": int(len(v))} if len(v) else None

    def row(get):
        return {"real": get(real_cmp), "sim": mean_sd([get(c_) for c_ in sim_cmp])}

    mf = {"spans": "clean music (interference and dropouts left out of the real recording)", "seeds": list(seeds),
          "loudness_short_term_median_lufs": row(lambda c_: c_["loudness"]["short_term_median_lufs"]),
          "loudness_short_term_p10_lufs": row(lambda c_: c_["loudness"]["short_term_p10_lufs"]),
          "loudness_short_term_p90_lufs": row(lambda c_: c_["loudness"]["short_term_p90_lufs"]),
          "integrated_lufs": row(lambda c_: c_["loudness"]["integrated_lufs"]),
          "gated_10ms_share": row(lambda c_: c_["gated_10ms_share"]),
          "edges_hz": {k: row(lambda c_, k=k: c_["edges"].get(k)) for k in ("low_6db_hz", "low_20db_hz",
                                                                                 "high_6db_hz", "high_20db_hz")},
          "coherence_oct": {f"{lo}-{hi}": row(lambda c_, i=i: c_["coherence_oct"][i])
                            for i, (lo, hi) in enumerate(COH_OCT)},
          "onset_r": {k: row(lambda c_, k=k: c_["onset_r"][k]) for k in ["total"] + list(BEAT_BANDS)}}
    err = np.mean([c_["third_oct_dbfs"] for c_ in sim_cmp], axis=0) - real_cmp["third_oct_dbfs"]
    cen = real_cmp["third_oct_hz"]
    inb = (cen >= 100) & (cen <= 8000)
    mf["band_level_error_db"] = {"third_oct_hz": cen, "per_band": err, "mean_abs_100_8k": float(np.mean(np.abs(err[inb]))),
                                 "max_abs_100_8k": float(np.max(np.abs(err[inb])))}
    mf["correction"] = corr
    mf["processing_fit"] = proc_fit
    if beats.get("trackers"):
        tr_ = beats["trackers"]
        for tr in ("madmom_online", "madmom", "beatthis"):
            if tr in tr_:
                mf[f"{tr}_act_r"] = {"real": (tr_[tr].get("rec") or {}).get("r"),
                                     "sim": mean_sd([(tr_[tr].get(f"sim{sd}") or {}).get("r") for sd in seeds])}
                mf[f"{tr}_beats_f"] = {
                    "real": ((tr_[tr].get("rec") or {}).get("beats_vs_gt") or {}).get("f_measure"),
                    "sim": mean_sd([((tr_[tr].get(f"sim{sd}") or {}).get("beats_vs_gt") or {}).get("f_measure")
                                    for sd in seeds]),
                    "original": ((tr_[tr].get("orig") or {}).get("beats_vs_gt") or {}).get("f_measure")}
                mf[f"{tr}_beats_hit25"] = {
                    "real": ((tr_[tr].get("rec") or {}).get("beats_vs_gt") or {}).get("hit_rate_25ms"),
                    "sim": mean_sd([((tr_[tr].get(f"sim{sd}") or {}).get("beats_vs_gt") or {}).get("hit_rate_25ms")
                                    for sd in seeds])}
    if stack_res:
        st = {}
        for g in stack_res["gates"]:
            gk = f"g{abs(int(g))}"
            real_r = stack_res["runs"].get(f"real_{gk}") or {}
            sims_r = [stack_res["runs"].get(f"sim{sd}_{gk}") or {} for sd in seeds]
            st[f"gate_{int(g)}"] = {k[:-2]: {"real": real_r.get(k[:-2]), "sim": mean_sd([s_.get(k[:-2]) for s_ in sims_r]),
                                             "sim_runs": [s_.get(k[:-2]) for s_ in sims_r]} for k in STACK_KEYS}
            st[f"gate_{int(g)}"]["locked"] = {"real": real_r.get("t_lock") is not None and
                                              np.isfinite(real_r.get("t_lock") or np.nan),
                                              "sim_runs": [s_.get("t_lock") is not None and
                                                           np.isfinite(s_.get("t_lock") or np.nan) for s_ in sims_r]}
        mf["stack"] = st
        mf["stack"]["real"] = {f"gate_{int(g)}": {k: (stack_res["runs"].get(f"real_g{abs(int(g))}") or {}).get(k)
                                                   for k in ("hit25", "hit70_gated", "t_lock", "f")}
                               for g in stack_res["gates"]}
    model_fit = mf

    # the passband's flatness between the band limits (1/3-octave smoothed, relative to 300 Hz-3 kHz)
    band_limits = {"lo_hz": split["lo_hz"], "hi_hz": split["hi_hz"]}
    if has_bl:
        inside = (resp_f >= (split["lo_hz"] or 0) * 2 ** (1 / 3)) & (resp_f <= (split["hi_hz"] or 1e9) * 2 ** (-1 / 3))
        lfv = np.log2(resp_f)
        smv = np.array([np.mean(resp_db[np.abs(lfv - v) <= 1 / 6]) for v in lfv])
        rel_db = smv - float(np.median(resp_db[(resp_f >= 300) & (resp_f <= 3000)]))
        band_limits["passband_min_db"] = float(rel_db[inside].min())
        band_limits["passband_max_db"] = float(rel_db[inside].max())

    # report
    nzs = noise["summary"]
    rep = {
        "version": VERSION, "analysed_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "compute_s": None,
        "run": {"run": run.get("run", run_dir.name), "stream": run.get("stream"),
                "device": run.get("device") or recm.get("device"), "started_utc": run.get("started_utc"),
                "stopped_utc": run.get("stopped_utc"), "original": orig_info, "synthetic": bool(run.get("synthetic")),
                "note": run.get("note"),
                "recording": {"seconds": n / FS, "fs": rec_fs, "overflows": recm.get("overflows")},
                "original_audio": {"seconds": len(orig) / FS, "fs": orig_fs, "channels": orig_ch}},
        "alignment": al["summary"] | {"band_windows": [{k: w.get(k) for k in ("tc", "status", "r", "u", "resid_ms")}
                                                       for w in (al.get("bands") or {}).get("windows", [])]},
        "regions": reg | {"spans_s": spans, "clean_s": clean},
        "room_sound": nzs | {"gate": noise.get("gate")},
        "levels": {k: v for k, v in levels.items() if not k.startswith("_")},
        "response": lin["response"] | {"power": {k: v for k, v in pr.items() if k not in ("excited",)},
                                       "model_source": resp["source"], "band_limits": band_limits},
        "ir": lin["ir"] | {"used_by_model": use_measured_ir,
                           "honest_caveat": None if use_measured_ir else (
                               f"the waveform coherence is {coh_med:.2f} (200 Hz-4 kHz): the mic's processing "
                               "scrambles the phase, so the impulse response and its decay times can't be measured "
                               "through it; the numbers here are the estimator's, not the room's. The model uses the "
                               "power response (magnitude only)" + (
                                   f" and a diffuse tail (RT60 {tail['rt60_s']:.2f} s, direct-to-reverberant "
                                   f"{tail['drr_db']:+.0f} dB) fitted to how much the onsets and the coherence "
                                   f"suffer, not measured" if tail else " and no reverberant tail"))},
        "dynamics": dyn["summary"],
        "noise_suppression": gate["summary"],
        "residual": resid,
        "processing": {"fit": proc_fit, "model": processing},
        "beats": beats,
        "ground_truth": gt_rep,
        "live": {k: v for k, v in live.items() if not k.startswith("_")} if live else None,
        "model_fit": model_fit,
        "model": {"path": "model/model.json", "ir_kind": ir_kind, "dynamics": (dyn_model or {}).get("kind"),
                  "spectral_gate": gate_model is not None,
                  "processing": sorted(processing) if processing else None},
    }
    # figures
    ctx = {"lin": lin, "pr": pr, "noise": noise, "dyn": dyn, "al": al, "live": live, "ev": ev,
           "real_cmp": real_cmp, "sim_cmp": sim_cmp, "model_fit": model_fit, "music_s": (ms, me)}
    try:
        f_m, P_m = welch_spans(rec, clean_smp, 16384)
        tc = np.asarray(nzs["third_octave_hz"])
        ctx["music_bands"] = (tc, _db(band_sum(f_m, P_m, tc)))
    except Exception:                                  # noqa: BLE001
        ctx["music_bands"] = None
    exc = clean_rel[0][0] + min(20.0, max(0.0, (clean_rel[0][1] - clean_rel[0][0]) / 2 - 6)) if clean_rel else 0.0
    ctx["onsets"] = {"orig": ob_x["total"][:nfr], "rec": ob_r["total"][:nfr], "sim": ob_s["total"][:nfr],
                     "excerpt_t0": exc, "t_offset": sa / FS,
                     "gt": (gt_rec - sa / FS) if gt_rec is not None else None}
    try:
        rep["figures"] = make_figs(ana / "figs", ctx, log)
    except Exception as e:                            # a plotting problem never loses the numbers
        rep["figures_error"] = f"{type(e).__name__}: {e}"[:300]
    rep["compute_s"] = time.time() - t_start
    rep = _clean(rep)
    (ana / "report.json").write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
    (ana / "report.md").write_text(report_md(rep), encoding="utf-8")
    if do_config and not run.get("synthetic"):
        write_config(run_dir, rep, model_path)
        log(f"config: {CONFIG_JSON.relative_to(REPO)} updated for {run.get('stream')}")
    shutil.rmtree(work, ignore_errors=True)
    log(f"done in {rep['compute_s']:.0f} s: {ana / 'report.md'}")
    return rep


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyse an audio calibration run (see the module docstring).")
    ap.add_argument("run_dir")
    ap.add_argument("--no-config", action="store_true", help="don't update config/room/audio_cal.json")
    ap.add_argument("--skip-beats", action="store_true",
                    help="skip the beat trackers, the live-clock score and the beat-stack replays")
    ap.add_argument("--no-stack", action="store_true", help="skip only the beat-stack replays (run_stack.py)")
    ap.add_argument("--stack-gates", default=None,
                    help="gate references (LUFS) for the beat-stack replays, comma-separated (default: -30, dsp's "
                         "default, and the measured music loudness at the mic)")
    ap.add_argument("--seeds", default=",".join(map(str, VAL_SEEDS)), help="the model replays' seeds (default 0,1,2)")
    ap.add_argument("--lock", action="store_true", help="take the CPU lock in-process (instead of wrapping in flock)")
    a = ap.parse_args()
    gates = [float(v) for v in a.stack_gates.split(",")] if a.stack_gates else None
    analyse(pathlib.Path(a.run_dir), write_config=not a.no_config, cpu_lock=a.lock, skip_beats=a.skip_beats,
            stack=not a.no_stack, stack_gates=gates, seeds=[int(v) for v in a.seeds.split(",")])


if __name__ == "__main__":
    main()
