"""Sympathetic and sustain-pedal resonance (research r4 item 3, the DAFx-08 form y = x + H_ped x).

A bank of complex one-pole resonators at FS_BANK (12 kHz), driven by the dry instrument's mono mix:
  * per key 21..108, partials k = 1.. up to F_MAX (5 kHz), at most MAX_PARTIALS (20), at the frequencies
    measured from the instrument (analysis.py) or the stiff-string model; partials k <= 4 get a second resonator
    BEAT_HZ (0.3-1 Hz, a fixed seed per key) away, for the beating of a key's unison strings: 1,366 resonators in all
    with the model tuning (about 0.5 ns per resonator-sample here, so ~2 s for a fully pedalled 5-minute piece);
  * free decay tau_free(k) = tau_sus(m) * (f_1 / f_k) ** 0.6 (tau_sus = show_service/midi.py's, via score.TAU_SUS,
    so what you hear decays like what the screens draw), at least 1.5 x the damper's;
  * damped decay: tau_d(m) = 0.25 s at A0, 0.12 s mid-keyboard (midi.py's DAMP_S), 0.06 s at E6 (r4 item 1);
    under a part-lifted damper (lift d from score's half-pedal curve) partial k decays with
    tau_d + (tau_free - tau_d) * d ** (2 + f_k / 2 kHz), so high partials are damped first (r4 item 2);
    strings that are free regardless of the pedal: keys down, sostenuto-latched, MIDI >= 89 (no dampers);
  * coupling: a string's steady response to a matching sine is P = k ** -0.5 * tau_free(k) / 1 s (a driven
    string: the build-up grows with its decay time), kept as a fixed input coefficient, so a damped string both
    decays fast and barely responds; strings under a down damper get no input at all (and stop costing CPU);
  * a key's own resonators get no input while its own sound is still up (own_gate: a level model reset at each
    strike, decaying at OWN_TAU_SCALE x the midi.py rates, gate opening between -40 and -50 dB), so the bank never
    doubles a note's own sampled sound; with the soft pedal down they still get UC_SELF_DB x u (the unstruck
    string ringing in sympathy, r4 item 8);
  * pedal-lift noise: a 30 ms pink-noise burst into the bank each time the dampers lift (lift rising through 0.5,
    re-armed below 0.2), BURST_DB below the dry's RMS;
  * output: each key panned by PAN_WIDTH x (m - 64.5) / 43.5 (constant power; bass left, the player's view;
    an instrument's "resonance_pan" overrides PAN_WIDTH, negative = audience view), high-passed at 60 Hz,
    upsampled, scaled by G_CAL x 10 ** ((resonance_db - REF_DB) / 20); null resonance_db = no bank at all.

REF_DB is the configured starting level (-35, the value in instruments.json); G_CAL is set by measurement so
that at -35 the halo is a subtle addition (see CALIBRATION below). The pedal state comes from
score.Performance.controls() at one row per BLOCK (10.7 ms); a block uses the row at its start, except
that a key's own-sound gate is the lower of the rows at its start and end, so a strike anywhere in a block closes
the gate for the whole block (none of the attack reaches the key's own resonators).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.signal import firwin, resample_poly

from .. import score as _score
from .analysis import Tuning, model_tuning
from .filters import butter_sos

FS_BANK = 12000
BLOCK = 128
F_MAX = 5000.0
MAX_PARTIALS = 20
BEAT_PARTIALS = 4
BEAT_HZ = (0.3, 1.0)
LIFT_STEPS = 64
TAU_POW = 0.6
PAN_WIDTH = 0.3
HP_HZ = 60.0
REF_DB = -35.0
# CALIBRATION (22 Oct, concert-d with its measured partials; RMS of the bank's output against the dry in 0.2 s
# windows): with resonance_db = REF_DB the bank sits 21.1 dB under the dry in Chopin Op. 9/2 (pedal down
# throughout) and 19.6 dB under it in Bill Evans' "Never Let Me Go" with the pedal down, 32.6 dB under it in that
# piece's pedal-up windows (release decays included) and 47.4 dB under it from 0.5 s after a release. The patent
# figure for single related strings is 10-20 dB under the struck tone (r4), so this is the subtle end; each dB of
# resonance_db is a dB of halo. Nothing has been judged by ear yet. G_CAL is the factor that puts it there.
G_CAL = 0.3
OWN_TAU_SCALE = 2.0
OWN_GATE_DB = (-40.0, -50.0)
UC_SELF_DB = -25.0
BURST_S = 0.03
BURST_DB = -20.0             # re the dry RMS: the ringing it leaves is ~45 dB under the music's peak
LIFT_ON, LIFT_REARM = 0.5, 0.2
TAIL_MAX_S = 8.0
TAIL_FLOOR_DB = -120.0
DORMANT_EPS = 1e-16
CHUNK_BLOCKS = 4096
SEED = 64


def damper_tau(m: np.ndarray) -> np.ndarray:
    """r4 item 1: 0.25 s at A0, 0.12 s at E4 (midi.py's DAMP_S), 0.06 s at E6, log-interpolated."""
    return np.exp(np.interp(m, [21.0, 64.0, 88.0], np.log([0.25, _score.DAMP_S, 0.06])))


@dataclass
class Bank:
    key: np.ndarray        # (R,) MIDI key
    k: np.ndarray          # (R,) partial number
    freq: np.ndarray       # (R,) Hz
    tau_free: np.ndarray   # (R,) s
    tau_damp: np.ndarray   # (R,) s
    peak: np.ndarray       # (R,) steady-state gain at resonance, free string
    kstart: np.ndarray     # (88,)
    kend: np.ndarray       # (88,)
    pan_l: np.ndarray      # (88,)
    pan_r: np.ndarray      # (88,)

    @property
    def size(self) -> int:
        return int(self.freq.size)


def build_bank(tuning: Optional[Tuning] = None, pan_width: float = PAN_WIDTH, fs: int = FS_BANK,
               f_max: float = F_MAX, max_partials: int = MAX_PARTIALS, beat_partials: int = BEAT_PARTIALS,
               seed: int = SEED) -> Bank:
    tuning = tuning or model_tuning()
    rng = np.random.default_rng(seed)
    keys, ks, fr, tf, td, pk = [], [], [], [], [], []
    kstart = np.zeros(_score.N_KEYS, np.int64)
    kend = np.zeros(_score.N_KEYS, np.int64)
    fmax = min(f_max, 0.45 * fs)
    for i, m in enumerate(_score.KEYS):
        kt = tuning.key(int(m))
        tsus = float(_score.TAU_SUS[i])
        tdm = float(damper_tau(np.array([m], np.float64))[0])
        beat = rng.uniform(*BEAT_HZ) * rng.choice([-1.0, 1.0])
        kstart[i] = len(fr)
        f1 = kt.freq(1)
        for k in range(1, max_partials + 1):
            f = kt.freq(k)
            if f > fmax:
                break
            tau = max(tsus * (f1 / f) ** TAU_POW, 1.5 * tdm)
            p = k ** -0.5 * tau
            pairs = [(f, p)] if k > beat_partials else [(f, p / math.sqrt(2)), (f + beat, p / math.sqrt(2))]
            for ff, pp in pairs:
                keys.append(m)
                ks.append(k)
                fr.append(ff)
                tf.append(tau)
                td.append(tdm)
                pk.append(pp)
        kend[i] = len(fr)
    pos = pan_width * (_score.KEYS - 64.5) / 43.5
    theta = (np.clip(pos, -1, 1) + 1) * math.pi / 4      # -1 = left
    return Bank(key=np.array(keys, np.int64), k=np.array(ks, np.int64), freq=np.array(fr), tau_free=np.array(tf),
                tau_damp=np.array(td), peak=np.array(pk), kstart=kstart, kend=kend,
                pan_l=np.cos(theta), pan_r=np.sin(theta))


def _coefficients(bank: Bank, fs: int):
    w = 2 * math.pi * bank.freq / fs
    rfree = np.exp(-1.0 / (bank.tau_free * fs))
    d = np.arange(LIFT_STEPS + 1) / LIFT_STEPS
    expo = 2.0 + bank.freq / 2000.0
    tau_eff = bank.tau_damp[:, None] + (bank.tau_free - bank.tau_damp)[:, None] * d[None, :] ** expo[:, None]
    rtab = np.exp(-1.0 / (tau_eff * fs))
    g = 2.0 * bank.peak * (1.0 - rfree)                     # Re(y) steady gain at resonance = g / (2 (1 - r))
    ki = bank.key - _score.KEY_LO
    return (rfree.astype(np.float32), np.ascontiguousarray(rtab, np.float32), np.cos(w).astype(np.float32),
            np.sin(w).astype(np.float32), g.astype(np.float32), bank.pan_l[ki].astype(np.float32),
            bank.pan_r[ki].astype(np.float32))


RESAMPLE_TAPS = 63            # Kaiser FIR at 48 kHz, cut-off 6 kHz: about 45 dB down by 7 kHz


def _fir(rate_hi: int, factor: int) -> np.ndarray:
    return firwin(RESAMPLE_TAPS, 0.5 * rate_hi / factor, window=("kaiser", 4.5), fs=rate_hi).astype(np.float32)


def to_bank_rate(x: np.ndarray, sr: int, fs: int = FS_BANK) -> np.ndarray:
    """(N,) or (N, 2) (its mono mix) at sr -> (M,) at the bank rate, centred (no delay). Integer ratios use the
    numba FIR, others resample_poly."""
    from . import _kernels as K
    x = np.asarray(x, np.float32)
    if sr % fs == 0:
        D = sr // fs
        h = _fir(sr, D)
        n_out = -(-x.shape[0] // D)
        if x.ndim == 2:
            return K.fir_down2(np.ascontiguousarray(x), h, D, RESAMPLE_TAPS // 2, n_out)
        return K.fir_down(np.ascontiguousarray(x), h, D, RESAMPLE_TAPS // 2, n_out)
    g = math.gcd(fs, int(sr))
    mono = x if x.ndim == 1 else 0.5 * (x[:, 0] + x[:, 1])
    return resample_poly(mono, fs // g, int(sr) // g).astype(np.float32)


def from_bank_rate(y: np.ndarray, sr: int, n_out: int, fs: int = FS_BANK) -> np.ndarray:
    """(M, 2) at the bank rate -> (n_out, 2) at sr, centred."""
    out = np.zeros((n_out, 2), np.float32)
    add_from_bank_rate(out, np.ascontiguousarray(y[:, 0]), np.ascontiguousarray(y[:, 1]), sr, n_out, 1.0, fs)
    return out


def add_from_bank_rate(out: np.ndarray, yl: np.ndarray, yr: np.ndarray, sr: int, n_out: int, gain: float,
                       fs: int = FS_BANK) -> None:
    """out[:n_out] += gain x (yl, yr) brought from the bank rate to sr."""
    from . import _kernels as K
    if sr % fs == 0:
        U = sr // fs
        K.fir_up2_add(yl, yr, _fir(sr, U) * np.float32(U * gain), U, RESAMPLE_TAPS // 2, out, n_out)
        return
    g = math.gcd(fs, int(sr))
    z = resample_poly(np.stack([yl, yr], axis=1), int(sr) // g, fs // g, axis=0).astype(np.float32)
    z = _fit_len(z, n_out)
    out[:n_out] += np.float32(gain) * z


def _fit_len(z: np.ndarray, n: int) -> np.ndarray:
    if z.shape[0] < n:
        return np.concatenate([z, np.zeros((n - z.shape[0],) + z.shape[1:], np.float32)])
    return z[:n]


def _pink_burst(n: int, rng: np.random.Generator) -> np.ndarray:
    X = np.fft.rfft(rng.standard_normal(n))
    f = np.arange(X.size, dtype=np.float64)
    f[0] = 1.0
    X /= np.sqrt(f)
    X[0] = 0.0
    x = np.fft.irfft(X, n)
    x *= np.hanning(n)
    r = float(np.sqrt(np.mean(x * x)))
    return (x / r if r > 0 else x).astype(np.float32)


def lift_events(lift: np.ndarray) -> np.ndarray:
    """Block indices where the dampers lift (rising through LIFT_ON, re-armed below LIFT_REARM)."""
    out = []
    armed = True
    for i, d in enumerate(lift):
        if armed and d >= LIFT_ON:
            out.append(i)
            armed = False
        elif not armed and d < LIFT_REARM:
            armed = True
    return np.array(out, np.int64)


def gain_for(resonance_db: float) -> float:
    return G_CAL * 10 ** ((float(resonance_db) - REF_DB) / 20.0)


def run(dry: np.ndarray, sr: int, perf: _score.Performance, resonance_db: float,
        tuning: Optional[Tuning] = None, pan_width: float = PAN_WIDTH, progress=None,
        bank: Optional[Bank] = None, ctl: "Optional[_score.Controls]" = None) -> tuple[np.ndarray, dict]:
    """The bank's output alone, (M, 2) float32 at sr, M >= len(dry) (the ringing may outlast the dry by up to
    TAIL_MAX_S), at its level; sample 0 = MIDI time 0. dry: (N,) or (N, 2) at sr, what drives the bank."""
    n = int(np.asarray(dry).shape[0])
    buf = np.zeros((n + int(math.ceil(TAIL_MAX_S * sr)) + 4 * BLOCK, 2), np.float32)
    n_out, st = run_into(buf, n, sr, perf, resonance_db, tuning, pan_width, progress, bank, ctl, src=dry)
    return buf[:n_out], st


def run_into(buf: np.ndarray, n: int, sr: int, perf: _score.Performance, resonance_db: float,
             tuning: Optional[Tuning] = None, pan_width: float = PAN_WIDTH, progress=None,
             bank: Optional[Bank] = None, ctl: "Optional[_score.Controls]" = None,
             src: Optional[np.ndarray] = None) -> tuple[int, dict]:
    """Add the bank's output to buf in place (module docstring); returns the new length (n plus the ringing that
    outlasts it, up to TAIL_MAX_S; buf needs that much room after n) and stats. The bank is driven by src, (N,) or
    (N, 2) at sr (default buf[:n], read in full before anything is added). ctl: the performance's controls at
    FS_BANK / BLOCK rows per second (computed here if not given)."""
    from . import _kernels as K
    from .filters import apply_sos_inplace

    t_wall, t_cpu = time.monotonic(), time.process_time()
    fs = FS_BANK
    bank = bank or build_bank(tuning, pan_width, fs)
    tc = [time.process_time()]
    x = to_bank_rate(buf[:n] if src is None else src, int(sr), fs)
    tc.append(time.process_time())
    n_in = x.size
    n_tail = int(TAIL_MAX_S * fs)
    x = np.concatenate([x, np.zeros(n_tail, np.float32)])
    nb = -(-x.size // BLOCK)
    rate = fs / BLOCK
    if ctl is None or abs(ctl.rate_hz - rate) > 1e-9:
        ctl = perf.controls(rate, duration_s=(nb - 1) / rate)
    rows = ctl.n
    idx = np.minimum(np.arange(nb), rows - 1)
    lift = ctl.damper_lift[idx].astype(np.float64)
    free = np.ascontiguousarray(ctl.free()[idx])
    struck = np.ascontiguousarray(ctl.struck[idx])
    uc = ctl.una_corda[idx].astype(np.float64)
    lift_q = np.rint(lift * LIFT_STEPS).astype(np.int64)
    live = np.ascontiguousarray(free | (lift_q > 0)[:, None])
    tau_own_free = (OWN_TAU_SCALE * _score.TAU_SUS).astype(np.float64)
    tau_own_damp = (OWN_TAU_SCALE * damper_tau(_score.KEYS.astype(np.float64))).astype(np.float64)
    gate = K.own_gate(struck, free, lift, BLOCK / fs, tau_own_free, tau_own_damp, OWN_GATE_DB[0], OWN_GATE_DB[1],
                      uc, 10 ** (UC_SELF_DB / 20))
    gate[:-1] = np.minimum(gate[:-1], gate[1:])          # a strike inside a block closes it from the block's start
    del struck
    # pedal-lift noise into the bank
    ev = lift_events(lift)
    rms = float(np.sqrt(np.mean(np.square(x[:n_in], dtype=np.float64)))) if n_in else 0.0
    if ev.size and rms > 0:
        rng = np.random.default_rng(SEED + 1)
        nburst = int(BURST_S * fs)
        amp = rms * 10 ** (BURST_DB / 20)
        for b in ev:
            s = int(b) * BLOCK
            e = min(x.size, s + nburst)
            x[s:e] += amp * _pink_burst(nburst, rng)[:e - s]
    rfree, rtab, cw, sw, g, owl, owr = _coefficients(bank, fs)
    tc.append(time.process_time())
    R = bank.size
    yr = np.zeros(R, np.float32)
    yi = np.zeros(R, np.float32)
    dormant = np.ones(_score.N_KEYS, np.bool_)
    outl = np.zeros(x.size, np.float32)
    outr = np.zeros(x.size, np.float32)
    work = 0
    total_s = x.size / fs
    for b0 in range(0, nb, CHUNK_BLOCKS):
        b1 = min(nb, b0 + CHUNK_BLOCKS)
        work += K.bank_run(x, b0, b1, BLOCK, lift_q, free, gate, live, bank.kstart, bank.kend, rfree, rtab, cw, sw,
                           g, owl, owr, yr, yi, dormant, outl, outr, np.float32(DORMANT_EPS))
        if progress is not None:
            progress(min(b1 * BLOCK, x.size) / fs, total_s)
    tc.append(time.process_time())
    del x
    sos = butter_sos(2, HP_HZ, fs, "highpass")
    apply_sos_inplace(outl, outl.size, sos)
    apply_sos_inplace(outr, outr.size, sos)
    # trim the tail where it has died away; fade if it is cut at the cap
    peak = max(float(outl.max()), -float(outl.min()), float(outr.max()), -float(outr.min())) if outl.size else 0.0
    floor = peak * 10 ** (TAIL_FLOOR_DB / 20)
    end = n_in
    if floor > 0:
        tail = np.maximum(np.abs(outl[n_in:]), np.abs(outr[n_in:]))
        loud = np.flatnonzero(tail > floor)
        end = n_in + (int(loud[-1]) + 1 if loud.size else 0)
    capped = end >= outl.size - BLOCK
    if capped:
        f = min(end - n_in, int(0.5 * fs))
        if f > 0:
            ramp = np.linspace(1.0, 0.0, f, dtype=np.float32)
            outl[end - f:end] *= ramp
            outr[end - f:end] *= ramp
    n_out = max(n, int(round(end * sr / fs)))
    if buf.shape[0] < n_out:
        raise ValueError("resonance.run_into: buf has no room for the tail")
    add_from_bank_rate(buf, outl[:end], outr[:end], int(sr), n_out, gain_for(resonance_db), fs)
    tc.append(time.process_time())
    live_frac = float(live[:-(n_tail // BLOCK) or None].mean()) if nb else 0.0
    stats = {"resonators": R, "keys_measured": tuning.measured if tuning else 0,
             "tuning": tuning.source if tuning else "model", "lift_events": int(ev.size),
             "live_key_fraction": round(live_frac, 3),
             "resonator_samples_M": round(work / 1e6, 1), "tail_s": round(max(0, end - n_in) / fs, 3),
             "tail_capped": bool(capped), "wall_s": round(time.monotonic() - t_wall, 3),
             "cpu_s": round(time.process_time() - t_cpu, 3), "gain": gain_for(resonance_db),
             "cpu_split_s": {k: round(b - a, 3) for k, a, b in
                             zip(("decimate", "controls", "kernel", "post+upsample"), tc[:-1], tc[1:])}}
    return n_out, stats
