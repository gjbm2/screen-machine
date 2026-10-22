"""The room: an impulse response (a file, or a synthetic one) convolved with the instrument, mixed in at wet_db.

IR files (a room's "ir"): 4 channels = true stereo in the order L->L, L->R, R->L, R->R (Samplicity's Bricasti
M7 files); 2 channels = mono in (the mean of L and R), stereo out; 1 channel = the same IR on each channel. Any
sample rate (resampled to the render's). The IR is trimmed where it falls 90 dB below its peak, then normalised
to unit energy per output channel for uncorrelated equal-level inputs, so wet_db is the wet level relative to the
dry for broadband sound (r4: 14-18 dB below dry for close-miked samples). A mono-in IR (and the synthetic one)
hears the mono mix (L + R) / 2, so there wet_db is relative to that mix.

Synthetic IR (a room's "synth": {"rt60_s", "predelay_ms", "width"}), used when "ir" is null or not installed:
decorrelated noise per channel (a fixed seed, so renders are repeatable), shaped in the STFT domain so every
frequency decays at its own rate: T60(f) = rt60 x 1.15 below 200 Hz (log-interpolated to 1.0 at 500 Hz), rt60 up to
2 kHz, rt60 x (2 kHz / f) ** 0.45 above (8 kHz: about half); a 4 ms fade-in after the predelay; six early
reflections per channel within 30 ms of it (different times left and right); width = mid/side on the IR.

The wet path's filters (high-pass 100 Hz, low-pass 9 kHz, 2nd-order Butterworth, r4 item 6) are applied to the IR
once, which by linearity is the same as filtering the wet signal. The convolution is a full FFT overlap-add (no
partitioned latency), so the dry path is untouched and the wet starts with the IR's own pre-delay.
"""

from __future__ import annotations

import hashlib
import json
import math
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
import scipy.fft as sfft
from scipy.signal import resample_poly, sosfilt, istft, stft

from .filters import butter_sos

WET_HP_HZ = 100.0
WET_LP_HZ = 9000.0
TRIM_DB = -90.0
SYNTH_SEED = 20261022
SYNTH_FADE_IN_S = 0.004
SYNTH_ER_MS = (3.1, 6.7, 10.9, 15.8, 21.4, 28.3)
SYNTH_ER_GAIN = (0.55, 0.45, 0.38, 0.3, 0.25, 0.2)    # relative to each other
SYNTH_ER_REL = 0.1                                      # -10 dB re the diffuse energy of the first 30 ms


class RoomError(RuntimeError):
    pass


def t60_curve(freqs: np.ndarray, rt60: float) -> np.ndarray:
    f = np.maximum(np.asarray(freqs, np.float64), 1.0)
    lo = np.interp(np.log(f), [math.log(200.0), math.log(500.0)], [1.15, 1.0])
    hi = np.where(f > 2000.0, (2000.0 / f) ** 0.45, 1.0)
    return rt60 * lo * hi


def synthetic_ir(sr: int, rt60_s: float, predelay_ms: float = 10.0, width: float = 1.0,
                 seed: int = SYNTH_SEED) -> np.ndarray:
    """(L, 2) float32 mono-in, stereo-out IR (module docstring), not yet normalised or filtered."""
    rt60 = float(rt60_s)
    if not 0.05 <= rt60 <= 20:
        raise RoomError(f"synthetic rt60_s {rt60} out of range")
    pre = max(0, int(round(float(predelay_ms or 0) * 1e-3 * sr)))
    tail_n = int(math.ceil(1.6 * rt60 * 1.15 * sr))      # 60 dB x 1.6 = about -96 dB at the end, bass included
    rng = np.random.default_rng(seed)
    nper = 1024
    hop = 256
    chans = []
    for c in range(2):
        # one window of noise either side is thrown away, so every kept sample has full STFT overlap
        noise = rng.standard_normal(tail_n + 3 * nper).astype(np.float64)
        f, t, Z = stft(noise, fs=sr, nperseg=nper, noverlap=nper - hop, boundary=None, padded=False)
        t_rel = np.maximum(t - nper / sr, 0.0)
        decay = np.exp(-6.907755 * t_rel[None, :] / t60_curve(f, rt60)[:, None])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")              # NOLA fails only in the discarded edge windows
            _, x = istft(Z * decay, fs=sr, nperseg=nper, noverlap=nper - hop, boundary=False)
        x = x[nper:nper + tail_n]
        fade = min(len(x), max(1, int(SYNTH_FADE_IN_S * sr)))
        x[:fade] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(fade) / fade)
        a0 = float(np.sqrt(np.mean(np.square(x[fade:fade + int(0.01 * sr)])))) if len(x) > fade else 1.0
        h = np.zeros(pre + tail_n, np.float64)
        h[pre:] = x
        jit = rng.uniform(-1.5, 1.5, len(SYNTH_ER_MS))
        sign = rng.choice([-1.0, 1.0], len(SYNTH_ER_MS))
        # the taps together hold SYNTH_ER_REL of the diffuse energy in the first 30 ms ("slight")
        scale = a0 * math.sqrt(SYNTH_ER_REL * 0.03 * sr / sum(g * g for g in SYNTH_ER_GAIN))
        for ms, g, j, s in zip(SYNTH_ER_MS, SYNTH_ER_GAIN, jit, sign):
            i = pre + int(round((ms + j) * 1e-3 * sr))
            if i < len(h):
                h[i] += s * g * scale
        chans.append(h)
    h = np.stack(chans, axis=1)
    w = float(1.0 if width is None else width)
    if w != 1.0:
        mid = 0.5 * (h[:, 0] + h[:, 1])
        side = 0.5 * (h[:, 0] - h[:, 1]) * w
        h = np.stack([mid + side, mid - side], axis=1)
    return h.astype(np.float32)


def _trim(h: np.ndarray, sr: int) -> np.ndarray:
    env = np.max(np.abs(h), axis=1)
    peak = float(env.max()) if env.size else 0.0
    if peak <= 0:
        raise RoomError("impulse response is silent")
    idx = np.flatnonzero(env > peak * 10 ** (TRIM_DB / 20))
    end = int(idx[-1]) + 1
    h = h[:end].copy()
    fade = min(end, int(0.01 * sr))
    h[end - fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)[:, None]
    return h


def _energy(h: np.ndarray, mode: str) -> float:
    e = np.sum(np.square(h.astype(np.float64)), axis=0)
    if mode == "true":
        return float((e[0] + e[1] + e[2] + e[3]) / 2.0)
    return float(np.mean(e))


@lru_cache(maxsize=4)
def _load_ir_cached(spec: str, sr: int) -> tuple:
    d = json.loads(spec)
    if d.get("ir"):
        import soundfile as sf
        h, fs = sf.read(d["ir"], dtype="float32", always_2d=True)
        if fs != sr:
            g = math.gcd(int(fs), int(sr))
            h = resample_poly(h, sr // g, int(fs) // g, axis=0).astype(np.float32)
        ch = h.shape[1]
        mode = {4: "true", 2: "mono-in", 1: "per-channel"}.get(ch)
        if mode is None:
            raise RoomError(f"{d['ir']}: {ch} channels (expected 1, 2 or 4)")
        source = "file"
    else:
        s = d["synth"]
        seed = SYNTH_SEED + int(hashlib.sha256(json.dumps(s, sort_keys=True).encode()).hexdigest()[:6], 16)
        h = synthetic_ir(sr, float(s.get("rt60_s", 1.0)), float(s.get("predelay_ms", 10.0)),
                         float(s.get("width", 1.0)), seed=seed)
        mode, source = "mono-in", "synth"
    h = _trim(h, sr)
    sos = np.concatenate([butter_sos(2, WET_HP_HZ, sr, "highpass"), butter_sos(2, min(WET_LP_HZ, 0.45 * sr), sr,
                                                                                 "lowpass")])
    h = np.stack([sosfilt(sos, h[:, c]) for c in range(h.shape[1])], axis=1).astype(np.float32)
    e = _energy(h, mode)
    if not e > 0:
        raise RoomError("impulse response has no energy after filtering")
    h /= np.float32(math.sqrt(e))
    h.setflags(write=False)
    return h, mode, source


def load_ir(room: dict, sr: int) -> Optional[tuple]:
    """(ir (L, C) float32, mode "true"|"mono-in"|"per-channel", source "file"|"synth") for a room entry, or None
    if the room has neither an installed IR file nor synth settings."""
    ir = room.get("ir")
    if ir and Path(ir).exists():
        spec = {"ir": str(Path(ir).resolve()), "mtime": Path(ir).stat().st_mtime_ns}
    elif room.get("synth"):
        spec = {"synth": room["synth"]}
    else:
        return None
    return _load_ir_cached(json.dumps(spec, sort_keys=True), int(sr))


def _block_plan(n_ir: int) -> tuple[int, int]:
    nfft = sfft.next_fast_len(max(4 * n_ir, 1 << 15), real=True)
    return nfft, nfft - n_ir + 1


def convolve(x: np.ndarray, h: np.ndarray, mode: str, progress=None) -> np.ndarray:
    """Full linear convolution of (N, 2) x with the IR. Returns (N + L - 1, 2) float32: true stereo
    y_L = x_L*h_LL + x_R*h_RL, y_R = x_L*h_LR + x_R*h_RR;  mono-in  y_c = mean(x)*h_c;  per-channel  y_c = x_c*h."""
    x = np.asarray(x, np.float32)
    n, L = x.shape[0], h.shape[0]
    buf = np.zeros((n + L - 1, 2), np.float32)
    if n == 0:
        return buf
    src = x.copy()
    buf[:n] = x
    convolve_into(buf, n, h, mode, gain=1.0, progress=progress)
    buf[:n] -= src
    return buf


def convolve_into(buf: np.ndarray, n: int, h: np.ndarray, mode: str, gain: float = 1.0, progress=None) -> int:
    """buf[:n + L - 1] += gain * (buf[:n] convolved with h), in place: overlap-add in blocks, each block's input
    read before its output span is written, the spill-over into later samples carried in an (L - 1)-sample
    buffer. buf must have L - 1 samples of room after n (zeros there). Returns the new length n + L - 1."""
    L = h.shape[0]
    if n <= 0:
        return n
    if buf.shape[0] < n + L - 1:
        raise ValueError("convolve_into: no room for the tail")
    nfft, blk = _block_plan(L)
    H = sfft.rfft(h, nfft, axis=0, workers=1) * np.float32(gain)         # (nfft//2+1, C) complex64
    carry = np.zeros((L - 1, 2), np.float32)
    for s in range(0, n, blk):
        e = min(n, s + blk)
        m = e - s
        if mode == "mono-in":
            X = sfft.rfft((0.5 * (buf[s:e, 0] + buf[s:e, 1]))[:, None], nfft, axis=0, workers=1)
            YL, YR = X[:, 0] * H[:, 0], X[:, 0] * H[:, 1]
        else:
            X = sfft.rfft(buf[s:e], nfft, axis=0, workers=1)
            if mode == "true":
                YL = X[:, 0] * H[:, 0] + X[:, 1] * H[:, 2]
                YR = X[:, 0] * H[:, 1] + X[:, 1] * H[:, 3]
            else:
                YL, YR = X[:, 0] * H[:, 0], X[:, 1] * H[:, 0]
        y = np.stack([sfft.irfft(YL, nfft, workers=1)[:m + L - 1], sfft.irfft(YR, nfft, workers=1)[:m + L - 1]],
                     axis=1)
        k = min(m, L - 1)
        y[:k] += carry[:k]
        rest = carry[k:].copy()                      # spill-over still owed past this block (short last blocks)
        buf[s:e] += y[:m]
        carry[:] = 0.0
        carry[:L - 1] = y[m:m + L - 1]
        if rest.shape[0]:
            carry[:rest.shape[0]] += rest
        if progress is not None:
            progress(e, n)
    buf[n:n + L - 1] += carry
    return n + L - 1


def wet_into(buf: np.ndarray, n: int, room: Optional[dict], sr: int, progress=None) -> tuple[int, dict]:
    """Add the room's wet signal (at wet_db) to buf in place; returns the new length and stats. buf needs
    ir_len(room, sr) - 1 samples of room after n."""
    if not room or room.get("wet_db") is None:
        return n, {"room": None}
    loaded = load_ir(room, sr)
    if loaded is None:
        return n, {"room": room.get("id"), "skipped": "no IR file installed and no synth settings"}
    h, mode, source = loaded
    n2 = convolve_into(buf, n, h, mode, gain=10 ** (float(room["wet_db"]) / 20.0), progress=progress)
    return n2, {"room": room.get("id"), "ir_source": source, "ir_mode": mode, "ir_s": round(h.shape[0] / sr, 3),
                "wet_db": float(room["wet_db"])}


def ir_len(room: Optional[dict], sr: int) -> int:
    """Samples in the room's (loaded, trimmed) IR; 0 if it adds no wet."""
    if not room or room.get("wet_db") is None:
        return 0
    loaded = load_ir(room, sr)
    return 0 if loaded is None else int(loaded[0].shape[0])


def wet(x: np.ndarray, room: Optional[dict], sr: int, progress=None) -> tuple[Optional[np.ndarray], dict]:
    """The room's wet signal alone at wet_db, (N + L - 1, 2), or None (no room, wet_db null, or nothing to
    convolve with); plus stats."""
    if not room or room.get("wet_db") is None:
        return None, {"room": None}
    loaded = load_ir(room, sr)
    if loaded is None:
        return None, {"room": room.get("id"), "skipped": "no IR file installed and no synth settings"}
    h, mode, source = loaded
    y = convolve(x, h, mode, progress)
    y *= np.float32(10 ** (float(room["wet_db"]) / 20.0))
    return y, {"room": room.get("id"), "ir_source": source, "ir_mode": mode, "ir_s": round(h.shape[0] / sr, 3),
               "wet_db": float(room["wet_db"])}
