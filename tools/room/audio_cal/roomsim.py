#!/usr/bin/env python3
"""The room model: make any music sound the way a calibrated room + PA + mic chain delivers it to show_service.

analyse.py measures a room from one calibration run and writes ``model/model.json`` (+ ``ir.wav``, ``noise.wav``).
This module replays that measurement on other audio, so beat-annotated datasets can be heard "through the room"
(tools/room/beat_eval/) without playing anything. Only numpy, scipy, soundfile and soxr are needed.

The chain, in the order the room applies it:
  1. mono mix, then 48 kHz (soxr VHQ);
  2. loudness match: the input is scaled to the calibration original's integrated loudness (BS.1770, mono), so the
     measured system gain and SNR apply as they were measured ("the PA at the same volume");
  3. the room: convolution with ``ir.wav``: the measured impulse response, or a minimum-phase FIR of the measured
     response (band edges, gain and EQ) when the measured IR is unreliable (``model.ir.kind``), with a diffuse tail
     only when a decay was measured. The IR's ``pre_samples`` are cut, so the output keeps the input's timeline
     (output sample n is the room's response at the direct sound of input sample n): beat annotations stay valid as
     they are. The measured acoustic delay is reported in ``system_delay``, never applied here;
  4. dynamics (``dynamics=True``): the fitted level curve (compressor/AGC above a threshold, expander below one,
     attack/release on 50 ms frame levels), then the fitted level-dependent octave-band gate, each only when
     analyse.py found it significant (model.json ``dynamics`` / ``spectral_gate`` not null);
  5. room sound: ``noise.wav`` (what the mic delivered with no music: room tone, or the run's real room sound when
     analyse.py found any, e.g. typing, talk) looped from an offset chosen by ``seed``, at its measured level, or at
     ``snr_db`` = 10 log10(music power / room-sound power) over the output. It is added before step 6, as the room
     adds it before the mic's own processing;
  6. the mic's processing (``dynamics=True`` and model.json ``processing`` not null; model version 2), what a
     speakerphone's noise suppressor / echo canceller does that a linear chain can't:
       scramble  a random spectral gain: log-normal, ``sigma_db`` standard deviation (or one per frequency region,
                 ``regions``), smoothed over ``tau_ms`` in time and ``bands_per_oct`` in frequency, power-neutral on
                 average (20 ms frames, 10 ms hop). A stand-in fitted to the measured loss of coherence and onset
                 survival, not a copy of the device's algorithm;
       gate      a broadband gate: 10 ms frames under ``thr_dbfs`` for ``hold_ms`` close it (``depth_db`` down);
       dropouts  the mic going (nearly) silent for a while in the middle of music: Poisson events at ``per_min``
                 per minute, exponential lengths of mean ``mean_s``, ``depth_db`` down, 20 ms ramps;
     all seeded by ``seed``;
  7. the mic's own band limits (``band_limit.wav``, model version 2): a minimum-phase FIR of the measured response's
     roll-offs outside the passband (a speakerphone's high-pass and its hard cut at its internal Nyquist), applied
     after step 6 as the device applies them after its processing (it also removes what step 6 smears out of band);
     the passband's shape is in step 3. Linear: applied with ``dynamics=False`` too;
  8. INT16 quantisation (round to 1/32768 steps, clipped), as show_service receives the VBAN stream.

API:
  apply(x, fs, model_path, seed=0, snr_db=None, dynamics=True) -> (y float32 (n,) at 48 kHz, 48000)
CLI:
  python roomsim.py in.wav out.wav --model runs/<run>/model/model.json [--snr-db N] [--seed N] [--no-dynamics]

Shared with analyse.py (so the fit and the replay measure levels the same way): ``lufs``, ``frame_power``,
``level_curve_gain``, ``band_levels_stft``, ``gate_gain_db``, ``synth_ir``, ``minphase_fir``, ``process``.

Memory stays bounded on long tracks: the processing runs frame chunks (overlap-add), never one whole-track STFT.
"""
from __future__ import annotations

import argparse
import functools
import json
import math
import os
import pathlib

import numpy as np
import scipy.ndimage as ndi
import scipy.signal as sps
import soundfile as sf
import soxr

FS = 48000
EPS = 1e-20

# BS.1770-4 K-weighting at 48 kHz: the high shelf, then the RLB high-pass.
_K_SHELF = ([1.53512485958697, -2.69169618940638, 1.19839281085285], [1.0, -1.69065929318241, 0.73248077421585])
_K_HP = ([1.0, -2.0, 1.0], [1.0, -1.99004745483398, 0.99007225036621])


def k_weight(x: np.ndarray) -> np.ndarray:
    """K-weighted copy of mono 48 kHz audio."""
    y = sps.lfilter(*_K_SHELF, x)
    return sps.lfilter(*_K_HP, y)


def lufs(x: np.ndarray, fs: int = FS) -> float:
    """Integrated loudness (BS.1770-4, mono, 400 ms blocks at 75 % overlap, -70 LUFS absolute and -10 LU relative
    gates). -inf when nothing passes the absolute gate."""
    if fs != FS:
        x = soxr.resample(np.asarray(x, dtype=np.float64), fs, FS)
    z = k_weight(np.asarray(x, dtype=np.float64)) ** 2
    blk, hop = int(0.4 * FS), int(0.1 * FS)
    if len(z) < blk:
        return -math.inf
    c = np.concatenate([[0.0], np.cumsum(z)])
    starts = np.arange(0, len(z) - blk + 1, hop)
    ms = (c[starts + blk] - c[starts]) / blk
    lk = -0.691 + 10 * np.log10(ms + EPS)
    ms = ms[lk > -70.0]
    if not len(ms):
        return -math.inf
    rel = -0.691 + 10 * np.log10(ms.mean()) - 10.0
    keep = ms[-0.691 + 10 * np.log10(ms) > rel]
    return float(-0.691 + 10 * np.log10(keep.mean())) if len(keep) else -math.inf


def frame_power(x: np.ndarray, win: int, hop: int) -> tuple[np.ndarray, np.ndarray]:
    """Mean square of ``x`` in frames of ``win`` samples every ``hop``: (centres in samples, power)."""
    x = np.asarray(x, dtype=np.float64)
    c = np.concatenate([[0.0], np.cumsum(x * x)])
    if len(x) < win:
        return np.zeros(0), np.zeros(0)
    starts = np.arange(0, len(x) - win + 1, hop)
    return starts + win / 2.0, np.maximum((c[starts + win] - c[starts]) / win, 0.0)


def detector(level_db: np.ndarray, hop_s: float, attack_s: float, release_s: float) -> np.ndarray:
    """One-pole level detector on frame levels in dB: rises with ``attack_s``, falls with ``release_s``."""
    aa = math.exp(-hop_s / max(attack_s, 1e-6))
    ar = math.exp(-hop_s / max(release_s, 1e-6))
    out = np.empty(len(level_db))
    d = float(level_db[0]) if len(level_db) else 0.0
    for i, v in enumerate(level_db.tolist()):
        d = aa * d + (1 - aa) * v if v > d else ar * d + (1 - ar) * v
        out[i] = d
    return out


def level_curve_gain(det_db: np.ndarray, p: dict) -> np.ndarray:
    """The static curve of model.json ``dynamics`` (kind "level_curve"), in dB: makeup, minus (1 - 1/ratio) of the
    detector level above ``threshold_db``, minus (exp_ratio - 1) of the level below ``exp_threshold_db`` (at most
    ``max_atten_db``)."""
    g = np.full(len(det_db), float(p.get("makeup_db", 0.0)))
    s = 1.0 - 1.0 / max(float(p.get("ratio", 1.0)), 1e-6)
    g -= s * np.maximum(0.0, det_db - float(p.get("threshold_db", 0.0)))
    e = float(p.get("exp_ratio", 1.0)) - 1.0
    if e > 0 and p.get("exp_threshold_db") is not None:
        att = e * np.maximum(0.0, float(p["exp_threshold_db"]) - det_db)
        g -= np.minimum(att, float(p.get("max_atten_db", 40.0)))
    return g


def apply_level_curve(y: np.ndarray, p: dict, fs: int = FS) -> np.ndarray:
    win = int(round(float(p.get("frame_ms", 50.0)) * fs / 1000))
    hop = int(round(float(p.get("hop_ms", 25.0)) * fs / 1000))
    centres, pw = frame_power(y, win, hop)
    if not len(pw):
        return y
    det = detector(10 * np.log10(pw + EPS), hop / fs, float(p["attack_ms"]) / 1000, float(p["release_ms"]) / 1000)
    g_db = level_curve_gain(det, p)
    g = np.interp(np.arange(len(y)), centres, 10 ** (g_db / 20.0))
    return y * g


# -- the spectral gate (level-dependent, per band: a noise-suppressor stand-in) ----------------------------------------
STFT_WIN = 2400         # 50 ms
STFT_HOP = 1200         # 25 ms


def band_levels_stft(x: np.ndarray, edges_hz: list[float], fs: int = FS):
    """(Z, freqs, band index per bin (-1: none), band levels dB (n_bands, frames)): one STFT, the levels every
    fitter and replay uses (10 log10 of the band's summed bin power)."""
    f, _, z = sps.stft(np.asarray(x, dtype=np.float64), fs, window="hann", nperseg=STFT_WIN,
                       noverlap=STFT_WIN - STFT_HOP, boundary="even", padded=True)
    band = np.full(len(f), -1)
    for b in range(len(edges_hz) - 1):
        band[(f >= edges_hz[b]) & (f < edges_hz[b + 1])] = b
    p = np.abs(z) ** 2
    lv = np.stack([10 * np.log10(p[band == b].sum(axis=0) + EPS) for b in range(len(edges_hz) - 1)])
    return z, f, band, lv


def gate_gain_db(snr_db: np.ndarray, p: dict) -> np.ndarray:
    """model.json ``spectral_gate``: a band ``snr_db`` (its level over the band's noise floor) well above
    ``thr_db`` passes; well below it loses ``depth_db``; ``width_db`` sets how soft the knee is."""
    return -float(p["depth_db"]) / (1.0 + np.exp((snr_db - float(p["thr_db"])) / max(float(p["width_db"]), 0.1)))


def apply_spectral_gate(y: np.ndarray, p: dict, fs: int = FS) -> np.ndarray:
    edges = p["edges_hz"]
    z, _, band, lv = band_levels_stft(y, edges, fs)
    noise = np.asarray(p["noise_band_db"], dtype=np.float64)[:, None]
    g = 10 ** (gate_gain_db(lv - noise, p) / 20.0)          # (bands, frames)
    gb = np.ones((len(band), z.shape[1]))
    for b in range(len(edges) - 1):
        gb[band == b] = g[b]
    _, out = sps.istft(z * gb, fs, window="hann", nperseg=STFT_WIN, noverlap=STFT_WIN - STFT_HOP, boundary=True)
    out = out[:len(y)]
    return np.pad(out, (0, len(y) - len(out))) if len(out) < len(y) else out


# -- a fallback IR: min-phase FIR of the smoothed response + a synthetic diffuse tail ----------------------------------
def minphase_fir(freqs_hz: np.ndarray, mag_db: np.ndarray, fs: int = FS, n_fft: int = 16384,
                 taps: int = 4096) -> np.ndarray:
    """Minimum-phase FIR whose magnitude follows (freqs_hz, mag_db) (interpolated on log frequency; flat beyond the
    ends): the real cepstrum, folded."""
    f = np.fft.rfftfreq(n_fft, 1.0 / fs)
    lf = np.log(np.maximum(f, 1.0))
    mag = np.interp(lf, np.log(np.maximum(freqs_hz, 1.0)), mag_db) / 20.0 * math.log(10)   # ln |H|
    c = np.fft.irfft(mag, n_fft)
    fold = np.zeros(n_fft)
    fold[0] = c[0]
    fold[1:n_fft // 2] = 2 * c[1:n_fft // 2]
    fold[n_fft // 2] = c[n_fft // 2]
    h = np.fft.irfft(np.exp(np.fft.rfft(fold)), n_fft)[:taps]
    fade = taps // 8
    h[-fade:] *= 0.5 + 0.5 * np.cos(np.linspace(0, math.pi, fade))
    return h


def synth_ir(freqs_hz, mag_db, rt60_s: float, drr_db: float, fs: int = FS, seed: int = 0,
             pre_samples: int = 0) -> np.ndarray:
    """Direct part = the min-phase FIR; tail = Gaussian noise under exp(-6.91 t / rt60) from 3 ms (2 ms fade-in),
    coloured by the same FIR, at ``drr_db`` below the direct part. Scaled so its energy matches the response's
    mean power gain (the measured |H| already holds the reverberant energy)."""
    rng = np.random.default_rng(seed)
    h = minphase_fir(np.asarray(freqs_hz, dtype=float), np.asarray(mag_db, dtype=float), fs)
    n = int(max(0.1, min(3.0, 1.2 * rt60_s)) * fs)
    t = np.arange(n) / fs
    env = np.exp(-6.91 * t / max(rt60_s, 0.05)) * np.clip((t - 0.003) / 0.002, 0, 1)
    tail = sps.fftconvolve(rng.standard_normal(n) * env, h)[:n]
    e_d, e_t = float(np.sum(h ** 2)), float(np.sum(tail ** 2)) + EPS
    tail *= math.sqrt(e_d * 10 ** (-drr_db / 10.0) / e_t)
    ir = np.zeros(max(n, len(h)))
    ir[:len(h)] += h
    ir[:n] += tail
    ir *= math.sqrt(e_d / float(np.sum(ir ** 2)))            # total energy = the response's power gain
    return np.concatenate([np.zeros(pre_samples), ir]).astype(np.float32)


# -- the mic's processing (model version 2) -----------------------------------------------------------------------------
P_WIN = 960             # 20 ms frames
P_HOP = 480             # 10 ms hop: sqrt-Hann analysis and synthesis, 50 % overlap, sums to exactly 1
P_CHUNK = 2048          # frames per overlap-add chunk (bounded memory on long tracks)
SEED_SCRAMBLE = 1_000_003
SEED_DROPOUTS = 2_000_003


def _sqrt_hann(n: int) -> np.ndarray:
    return np.sqrt(0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n))      # periodic: hann^2 sums to 1 at hop n/2


def sigma_per_bin(p: dict, f: np.ndarray) -> np.ndarray:
    """The scramble's sigma (dB) per frequency bin: ``sigma_db`` everywhere, or per region with ``regions``
    ({"edges_hz": [e1, ...], "sigma_db": [s0, s1, ...]}: s0 below e1, s1 between e1 and e2, ...), crossfaded over
    1/3 octave (log frequency) around each edge."""
    if p.get("regions"):
        edges = [float(e) for e in p["regions"]["edges_hz"]]
        sig = [float(v) for v in p["regions"]["sigma_db"]]
        lf = np.log2(np.maximum(f, 1.0))
        out = np.full(len(f), sig[0])
        for e, s_lo, s_hi in zip(edges, sig[:-1], sig[1:]):
            out += (s_hi - s_lo) * np.clip((lf - (math.log2(e) - 1 / 6)) / (1 / 3), 0.0, 1.0)
        return out
    return np.full(len(f), float(p.get("sigma_db", 0.0)))


def scramble(y: np.ndarray, p: dict, seed: int = 0, fs: int = FS) -> np.ndarray:
    """model.json ``processing.scramble``: multiply the short-time spectrum by 10^(sigma g / 20), g a unit-variance
    Gaussian field: one-pole smoothed over ``tau_ms`` per band, bands ``bands_per_oct`` per octave from 50 Hz to
    20 kHz (linear in log frequency between band centres, renormalised to unit variance per bin); sigma per bin
    from ``sigma_per_bin`` (one value, or one per frequency region). Power-neutral: E[gain^2] = 1 per bin, and the
    output is rescaled to the input's mean power (overlapping frames with different gains partly cancel)."""
    y = np.asarray(y, dtype=np.float64)
    f = np.fft.rfftfreq(P_WIN, 1.0 / fs)
    sig = sigma_per_bin(p, f)
    if float(np.max(sig)) <= 0 or len(y) < P_WIN:
        return y
    tau = max(float(p.get("tau_ms", 40.0)), 1.0) / 1000.0
    bpo = max(float(p.get("bands_per_oct", 6.0)), 0.5)
    nb = max(2, int(round(math.log2(20000.0 / 50.0) * bpo)))
    cen = np.geomspace(50.0, 20000.0, nb)
    lf = np.log(np.clip(f, 50.0, 20000.0))
    # bin -> (lower band, weight) for the log-frequency interpolation; its variance, to renormalise
    pos = np.interp(lf, np.log(cen), np.arange(nb))
    lo = np.minimum(np.floor(pos).astype(int), nb - 2)
    wt = pos - lo
    vnorm = 1.0 / np.sqrt((1 - wt) ** 2 + wt ** 2)
    a = math.exp(-P_HOP / fs / tau)
    norm = math.sqrt((1 + a) / (1 - a))                       # the one-pole's output std for unit white input
    k = np.maximum(sig, 0.0) * math.log(10) / 20.0            # dB -> nepers of amplitude, per bin
    inv_rms = np.exp(-k * k)                                  # 1 / sqrt(E[exp(2 k g)]) for unit Gaussian g
    rng = np.random.default_rng(seed + SEED_SCRAMBLE)
    win = _sqrt_hann(P_WIN)
    pad = np.concatenate([np.zeros(P_WIN), y, np.zeros(2 * P_WIN)])
    n_fr = (len(pad) - P_WIN) // P_HOP + 1
    out = np.zeros(len(pad))
    zi = a * rng.standard_normal(nb) / norm                  # y[-1] drawn stationary (std 1 / norm); zi = a y[-1]
    for j0 in range(0, n_fr, P_CHUNK):
        j1 = min(n_fr, j0 + P_CHUNK)
        idx = (np.arange(j0, j1) * P_HOP)[:, None] + np.arange(P_WIN)[None, :]
        Z = np.fft.rfft(pad[idx] * win, axis=1)
        g, zf = sps.lfilter([1 - a], [1, -a], rng.standard_normal((j1 - j0, nb)), axis=0, zi=zi[None, :])
        zi = zf[0]
        g = g * norm                                          # unit variance
        gb = (g[:, lo] * (1 - wt) + g[:, lo + 1] * wt) * vnorm   # (frames, bins), unit variance per bin
        Z *= np.exp(k * gb) * inv_rms
        fr = np.fft.irfft(Z, P_WIN, axis=1) * win
        _ola(out, fr, j0)
    out = out[P_WIN:P_WIN + len(y)]
    p_in, p_out = float(np.mean(y * y)), float(np.mean(out * out))
    return out * math.sqrt(p_in / p_out) if p_out > 0 else out


def _ola(out: np.ndarray, frames: np.ndarray, j0: int) -> None:
    """Overlap-add frames (hop P_HOP, 50 % overlap) into out from frame j0: even and odd frames don't overlap."""
    for par in (0, 1):
        sub = frames[par::2]
        if not len(sub):
            continue
        s0 = (j0 + par) * P_HOP
        flat = sub.reshape(-1)
        out[s0:s0 + len(flat)] += flat[:max(0, min(len(flat), len(out) - s0))]


def gate(y: np.ndarray, p: dict, fs: int = FS) -> np.ndarray:
    """model.json ``processing.gate``: 10 ms frames whose level stays under ``thr_dbfs`` for ``hold_ms`` close the
    gate (``depth_db`` down) until a frame reaches the threshold again; 5 ms ramps."""
    thr = p.get("thr_dbfs")
    if thr is None or len(y) < 480:
        return y
    hop = 480
    c, pw = frame_power(y, hop, hop)
    lv = 10 * np.log10(pw + EPS)
    hold = max(1, int(round(float(p.get("hold_ms", 100.0)) / 10.0)))
    below = lv < float(thr)
    # closed where the last ``hold`` frames (this one included) were all below
    run = np.zeros(len(below), dtype=int)
    cnt = 0
    for i, b in enumerate(below.tolist()):
        cnt = cnt + 1 if b else 0
        run[i] = cnt
    closed = run >= hold
    g_fr = np.where(closed, 10 ** (-float(p.get("depth_db", 100.0)) / 20.0), 1.0)
    g_fr = ndi.uniform_filter1d(g_fr, 2, mode="nearest") if len(g_fr) > 1 else g_fr
    return y * np.interp(np.arange(len(y)), c, g_fr)


def dropouts(y: np.ndarray, p: dict, seed: int = 0, fs: int = FS) -> np.ndarray:
    """model.json ``processing.dropouts``: Poisson events (``per_min``), exponential lengths (``mean_s``, at most
    4 x), ``depth_db`` down with 20 ms ramps."""
    rate = float(p.get("per_min") or 0.0)
    if rate <= 0 or not len(y):
        return y
    rng = np.random.default_rng(seed + SEED_DROPOUTS)
    dur = len(y) / fs
    g = np.ones(len(y))
    t = rng.exponential(60.0 / rate)
    ramp = int(0.02 * fs)
    depth = 10 ** (-float(p.get("depth_db", 60.0)) / 20.0)
    mean_s = max(float(p.get("mean_s", 1.0)), 0.05)
    while t < dur:
        d = min(rng.exponential(mean_s), 4 * mean_s)
        a, b = int(t * fs), min(len(y), int((t + d) * fs))
        if b > a:
            env = np.full(b - a, depth)
            r = min(ramp, (b - a) // 2)
            if r > 0:
                env[:r] = np.linspace(1.0, depth, r)
                env[-r:] = np.linspace(depth, 1.0, r)
            g[a:b] = np.minimum(g[a:b], env)
        t += d + rng.exponential(60.0 / rate)
    return y * g


def process(y: np.ndarray, p: dict, seed: int = 0, fs: int = FS) -> np.ndarray:
    """model.json ``processing``: scramble, then gate, then dropouts (each when present)."""
    if p.get("scramble"):
        y = scramble(y, p["scramble"], seed, fs)
    if p.get("gate"):
        y = gate(y, p["gate"], fs)
    if p.get("dropouts"):
        y = dropouts(y, p["dropouts"], seed, fs)
    return y


# -- the model ----------------------------------------------------------------------------------------------------------
@functools.lru_cache(maxsize=8)
def _load(model_path: str, mtime: float) -> dict:
    p = pathlib.Path(model_path)
    m = json.loads(p.read_text(encoding="utf-8"))
    base = p.parent
    ir_spec = m.get("ir", {})
    ir_file = base / ir_spec.get("file", "ir.wav")
    if ir_file.is_file():
        ir, fs = sf.read(str(ir_file), dtype="float64", always_2d=False)
        if fs != FS:
            ir = soxr.resample(ir, fs, FS)
        pre = int(ir_spec.get("pre_samples", 0))
    else:                                         # rebuild the fallback from the stored response
        resp = m["response"]
        pre = 0
        ir = synth_ir(resp["freqs_hz"], resp["mag_db"], float(ir_spec.get("rt60_s") or 0.4),
                      float(ir_spec.get("drr_db") or 0.0)).astype(np.float64)
    noise_spec = m.get("noise", {})
    nf = base / noise_spec.get("file", "noise.wav")
    noise = None
    if nf.is_file():
        noise, fs = sf.read(str(nf), dtype="float64", always_2d=False)
        if noise.ndim > 1:
            noise = noise.mean(axis=1)
        if fs != FS:
            noise = soxr.resample(noise, fs, FS)
    bl = None
    bl_spec = m.get("band_limit") or {}
    if bl_spec.get("file") and (base / bl_spec["file"]).is_file():
        bl, fs = sf.read(str(base / bl_spec["file"]), dtype="float64", always_2d=False)
        if fs != FS:
            bl = soxr.resample(bl, fs, FS)
    return {"m": m, "ir": np.asarray(ir, dtype=np.float64), "pre": pre, "noise": noise, "band_limit": bl}


def load_model(model_path) -> dict:
    p = os.path.abspath(os.path.expanduser(str(model_path)))
    return _load(p, os.path.getmtime(p))


def to_mono48k(x, fs: int) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=1)
    if fs != FS:
        x = soxr.resample(x, fs, FS, quality="VHQ")
    return x


def loop_segment(noise: np.ndarray, n: int, seed: int) -> np.ndarray:
    """n samples of ``noise`` looped from an offset chosen by ``seed`` (no whole-loop copies: long tracks stay
    small in memory)."""
    out = np.empty(n)
    i = int(np.random.default_rng(seed).integers(len(noise)))
    pos = 0
    while pos < n:
        take = min(n - pos, len(noise) - i)
        out[pos:pos + take] = noise[i:i + take]
        pos += take
        i = 0
    return out


def quantise_int16(y: np.ndarray) -> np.ndarray:
    return (np.clip(np.round(y * 32768.0), -32768, 32767) / 32768.0).astype(np.float32)


def apply(x, fs: int, model_path, seed: int = 0, snr_db=None, dynamics: bool = True):
    """``x`` (n,) or (n, ch) at ``fs`` -> (y float32 mono at 48 kHz, quantised to INT16 steps, 48000): the room's
    version of ``x``, as show_service would receive it. ``snr_db`` overrides the measured room-sound level;
    ``dynamics`` False skips everything fitted that isn't linear (the level curve, the spectral gate and the mic's
    processing): the room's EQ and room sound only. Deterministic for a given ``seed``."""
    mod = load_model(model_path)
    m = mod["m"]
    x = to_mono48k(x, fs)
    n = len(x)
    if n == 0:
        return np.zeros(0, dtype=np.float32), FS
    ref = (m.get("reference") or {}).get("lufs")
    li = lufs(x)
    if ref is not None and math.isfinite(li):
        x = x * 10 ** ((float(ref) - li) / 20.0)
    y = sps.oaconvolve(x, mod["ir"])[mod["pre"]:mod["pre"] + n]       # overlap-add: bounded memory
    if len(y) < n:
        y = np.pad(y, (0, n - len(y)))
    if dynamics and m.get("dynamics"):
        y = apply_level_curve(y, m["dynamics"])
    if dynamics and m.get("spectral_gate"):
        y = apply_spectral_gate(y, m["spectral_gate"])
    noise = mod["noise"]
    if noise is not None and len(noise) > 0:
        seg = loop_segment(noise, n, seed)
        if snr_db is not None:
            p_music = float(np.mean(y ** 2))
            p_noise = float(np.mean(seg ** 2)) + EPS
            seg = seg * math.sqrt(p_music / (p_noise * 10 ** (float(snr_db) / 10.0)))
        y = y + seg
    if dynamics and m.get("processing"):
        y = process(y, m["processing"], seed)
    if mod["band_limit"] is not None:                         # the mic's own filters, after its processing
        y = sps.oaconvolve(y, mod["band_limit"])[:n]
    return quantise_int16(y), FS


def main() -> None:
    ap = argparse.ArgumentParser(description="Play a file through a calibrated room model (offline).")
    ap.add_argument("inp")
    ap.add_argument("out")
    ap.add_argument("--model", required=True, help="tools/room/audio_cal/runs/<run>/model/model.json")
    ap.add_argument("--snr-db", type=float, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-dynamics", action="store_true", help="linear chain + room sound only (no fitted processing)")
    a = ap.parse_args()
    x, fs = sf.read(a.inp, dtype="float64", always_2d=False)
    y, fs_out = apply(x, fs, a.model, seed=a.seed, snr_db=a.snr_db, dynamics=not a.no_dynamics)
    sf.write(a.out, y, fs_out, subtype="FLOAT")


if __name__ == "__main__":
    main()
