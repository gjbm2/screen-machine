#!/usr/bin/env python3
"""Candidate "micfe": a mic-compensation front end for the beat clock's rhythm lane (owner's idea, 18 Oct: measure
the chain, then filter before bpm/DSP).

Why. The room calibration (tools/room/audio_cal/, run 20261018T145602Z-pc22-mic) shows what PC22's Dell speakerphone
does to music before show_service hears it: a high-pass near 165 Hz, a hard cut at 7.9 kHz, a passband 10-20 dB
down above 2 kHz, a diffuse room tail (fitted RT60 1.0 s, DRR 0 dB), and noise suppression that reworks the
spectrum every few tens of ms (coherence 0.15 from 200 Hz to 4 kHz; onsets kept: sub 51 %, bass 65 %, mid 9 %,
high 5 %). The beat stack (madmom's online LSTMs -> our peaks/tempogram/PLL) starves. This module is a causal,
stateful filter on the 48 kHz mono stream, run before the clock's lane (and optionally before dsp), that tries to
give madmom back a beat it recognises.

Engine. One STFT (960-point sqrt-Hann frames every 240 samples, 5 ms; weighted overlap-add, sums to the input at
unity gains): per frame, a gain per bin = a static curve x the band gains, then one inverse FFT. Cost: one rfft + one
irfft per 5 ms and a few small vector ops (``bench``: 0.3-0.5 x the clock's madmom with 2 nets, streaming 128-frame
blocks). Output = the processed signal delayed by exactly one frame (D = 960 samples, 20 ms = two of the clock's
hops, so at unity gains the clock sees the very same hops, two later): the runner moves the lane's time base back by
the measured latency (D plus the onset shift ``measure_latency`` reads off a drum loop), so the clock's times stay
the sound's, as live would.

Stages (``FrontEnd(cfg, mic)``; ``VARIANTS`` names the settings tried), each optional:
  eq      a static gain per bin from the calibration. ``mode`` "inverse": the measured passband inverted (0 dB over
          300 Hz-3 kHz, clamped to [-cut_db, +boost_db], flat beyond the mic's band limits so nothing out of band is
          lifted); "coherence": that plus ``coh_weight`` x 10 log10(coh / (1 - coh)) of the measured coherence (lift
          the bands the chain keeps, cut those its processing scrambles); "tilt": the coherence weighting alone.
  lp_hz / hp_hz   static Butterworth-shaped magnitude (e.g. drop the scrambled top).
  bands   ``edges_hz`` (1/2-octave-ish inside the mic's passband); per band and frame a gain =
            weight   static dB per band (``weights_db``, or ``"coherence"``: from the calibration's coherence, or
                     ``"snr"``: the music's level over the room's own sound per band at the mic, ``band_snr``)
            norm     a slow per-band AGC towards a pink target (equal power per octave): whitening, so the surviving
                     bass/low-mid carry their share whatever the chain's tilt (``alpha`` 0..1, ``tau_s``, clamped)
            trans    an onset boost: (band energy / its ``slow_ms`` one-pole, previous frame)^(k/2), clamped: lifts
                     attacks and shortens the room's tail, in ``bands`` (the ones that survive) or all
            xtrans   the same boost from the MEAN rise (dB) over ``bands`` (all in band, or "low": under 1 kHz),
                     one gain for every in-band bin: a real onset rises in many bands at once, the mic's processing
                     moves each band on its own, so the mean keeps the one and averages the other away
  derev   late-reverb suppression per bin: the power ``delay_ms`` ago, decayed by the calibration's RT60
          (``rt60_s``, model.json ir.rt60_s), is taken off (a spectral-subtraction gain, at most ``max_cut_db``)
  fsmooth  |X| smoothed across +-``oct``/2 octaves per frame (the processing's band gains are independent per
          1/6 octave; an onset is broadband), clamped to +-``max_db``
  vkick   a "virtual kick": a rise of ``thr_db`` in the kick band that survives this mic (``lo_hz``-``hi_hz``,
          110-250 Hz) triggers a synthetic kick (a sine falling 110 -> 50 Hz, ``decay_ms``), at the band's own level
          x ``gain_db``, restoring the thump under the mic's high-pass that madmom's nets were trained on.
  level   the output's slow power is matched to the input's (tau 2 s), so the clock's own AGC sees the same level.

Causality: frames use only samples heard, every recursion runs forward, the output is a fixed delay later; any
block cut of the stream gives the same output (``selftest``).

Per-mic parameters (``mic_params(model.json)``, all from the stream's calibration run): the band limits ``lo_hz`` /
``hi_hz`` (the lowest band starts at 0.7 x lo_hz, 115 Hz here; "low" = the bands under 500 Hz; the kick band), the
room tail's ``rt60_s`` (derev), the passband response and coherence (eq, coherence weights), and the music-over-room
SNR per third octave (``band_snr``: rec-music.wav against model/noise.wav; snr weights). Keyed by stream like the
gate offset (config/room/audio_cal.json streams.<stream>.model): a stream without a calibration gets none.

What the dev subset said (19 Oct; micfe_screen.py, results in ~/.cache/screen-machine/beat-eval/alt-micfe/):
- Only the onset boost in the surviving low bands (``trans``, bands under 500 Hz) helps, best with late-reverb
  suppression (``derev_trans_lo_h480``: hop 480, the cheaper engine): C, 27 steady tracks, our clock F 0.28 -> 0.31
  (15 better / 7 worse), madmom's DBN 0.45 -> 0.47 (16/7); the real calibration recording with its 182 s of real
  room sound first: our clock from never locking (F 0.39, stuck at 42 bpm) to F 0.99, gated 96 % of the beats.
  Small in the model; the clock's own conf still keeps it under its 0.70 gate on most tracks.
- Static EQ (inverse, coherence, tilt), whitening (norm), coherence or SNR band weights, the low-pass, the
  cross-band boost (xtrans), the virtual kick and spectral smoothing don't (within noise or worse). madmom's input is
  log magnitude + positive log differences, so a static per-band gain cancels in the differences; whitening and
  inverse EQ lift the bands the mic's processing scrambles; what the processing, the tail and the room's sound took
  away is gone, not filtered. The onset boost works where onsets survive (bass/low-mid: 51-65 %): it restores the
  attack/decay contrast the tail and noise flatten, and on the real recording it keeps the room's talk and typing
  from seeding a sub-octave tempo.
- Risk: a low-band onset boost can pull the phase onto a syncopated bass (an anti-phase lock on one rumba in C, one
  hip-hop track in A with derev): watch wrong-lock.

Numpy and scipy only (show_service's env has both).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import pathlib
from typing import Optional

import numpy as np
import scipy.signal as sps

FS = 48000
EPS = 1e-20
N_FFT = 960                 # 20 ms: the output's fixed delay is two of the clock's 10 ms hops (and 6 x soxr's 160
HOP = 240                   # samples), so at unity gains the lane sees the same hops, two later ("pass" == "none")
REPO = pathlib.Path(__file__).resolve().parents[4]
PC22_MODEL = REPO / "tools/room/audio_cal/runs/20261018T145602Z-pc22-mic/model/model.json"


# -- per-mic parameters from the calibration ---------------------------------------------------------------------------
def mic_params(model_path) -> dict:
    """What the front end takes from a room model (tools/room/audio_cal/runs/<run>/model/model.json)."""
    p = pathlib.Path(model_path).expanduser()
    m = json.loads(p.read_text())
    resp = m.get("response") or {}
    bl = m.get("band_limit") or {}
    return {
        "model": str(p), "run": m.get("run"), "stream": m.get("stream"),
        "freqs_hz": [float(v) for v in resp.get("freqs_hz", [])],
        "passband_db": [float(v) for v in resp.get("passband_db", resp.get("mag_db", []))],
        "coherence": [float(v) for v in resp.get("coherence", [])],
        "lo_hz": float(bl.get("lo_hz") or 40.0), "hi_hz": float(bl.get("hi_hz") or 20000.0),
        "music_lufs": (m.get("music") or {}).get("short_term_median_lufs"),
        "noise_rms_dbfs": (m.get("noise") or {}).get("rms_dbfs"),
        "rt60_s": (m.get("ir") or {}).get("rt60_s"), "drr_db": (m.get("ir") or {}).get("drr_db"),
        "band_snr": band_snr(p.parent.parent),
    }


def read_wav(path) -> tuple[np.ndarray, int]:
    """Mono float audio from a RIFF WAV (IEEE float32 or PCM16; the calibration's files): numpy only, no soundfile."""
    b = pathlib.Path(path).read_bytes()
    if b[:4] != b"RIFF" or b[8:12] != b"WAVE":
        raise ValueError(f"{path}: not a RIFF WAVE file")
    i, fmt, fs, ch, bits, data = 12, None, None, 1, 16, None
    while i + 8 <= len(b):
        cid, n = b[i:i + 4], int.from_bytes(b[i + 4:i + 8], "little")
        body = b[i + 8:i + 8 + n]
        if cid == b"fmt ":
            fmt = int.from_bytes(body[0:2], "little")
            ch = int.from_bytes(body[2:4], "little")
            fs = int.from_bytes(body[4:8], "little")
            bits = int.from_bytes(body[14:16], "little")
            if fmt == 0xFFFE and len(body) >= 26:              # WAVE_FORMAT_EXTENSIBLE: the subformat's tag
                fmt = int.from_bytes(body[24:26], "little")
        elif cid == b"data":
            data = body
        i += 8 + n + (n & 1)
    if data is None or fs is None:
        raise ValueError(f"{path}: no fmt/data chunk")
    if fmt == 3 and bits == 32:
        x = np.frombuffer(data, "<f4").astype(np.float64)
    elif fmt == 1 and bits == 16:
        x = np.frombuffer(data, "<i2").astype(np.float64) / 32768.0
    else:
        raise ValueError(f"{path}: format {fmt}/{bits} bit not handled")
    return x.reshape(-1, ch).mean(axis=1), fs


THIRD_OCT_HZ = [100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300,
                8000]


def band_snr(run_dir) -> Optional[dict]:
    """The music's level over the room's own sound per third octave, at the mic (the calibration run's clean music,
    rec-music.wav, against the room model's noise.wav): where in the spectrum the beat stands out of the room."""
    run_dir = pathlib.Path(run_dir)
    try:
        music, fs_m = read_wav(run_dir / "rec-music.wav")
        room, fs_r = read_wav(run_dir / "model" / "noise.wav")
    except (OSError, ValueError):
        return None
    out = []
    for x, fs in ((music, fs_m), (room, fs_r)):
        f, P = sps.welch(x, fs, nperseg=8192)
        out.append([10 * math.log10(float(P[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))].sum()) + 1e-30)
                    for c in THIRD_OCT_HZ])
    return {"hz": THIRD_OCT_HZ, "music_db": [round(v, 2) for v in out[0]], "room_db": [round(v, 2) for v in out[1]],
            "snr_db": [round(a - b, 2) for a, b in zip(*out)]}


def _smooth_log(freqs: np.ndarray, v: np.ndarray, oct_w: float) -> np.ndarray:
    lf = np.log2(np.maximum(freqs, 1.0))
    return np.array([float(np.mean(v[np.abs(lf - f) <= oct_w / 2])) for f in lf])


def _coh_snr_db(mic: dict, smooth_oct: float = 1 / 3) -> tuple[np.ndarray, np.ndarray]:
    f = np.asarray(mic["freqs_hz"], float)
    coh = _smooth_log(f, np.clip(np.asarray(mic["coherence"], float), 0.01, 0.95), smooth_oct)
    return f, 10 * np.log10(coh / (1 - coh))


def eq_curve(mic: dict, mode: str = "inverse", boost_db: float = 12.0, cut_db: float = 12.0,
             coh_weight: float = 0.5, smooth_oct: float = 1 / 3, ref_band=(300.0, 3000.0)) -> tuple:
    """(freqs, gain dB) of the static EQ (module docstring, ``eq``)."""
    f = np.asarray(mic["freqs_hz"], float)
    lo, hi = mic["lo_hz"], mic["hi_hz"]
    inside = (f >= lo) & (f <= hi)
    rb = (f >= ref_band[0]) & (f <= ref_band[1])
    g = np.zeros(len(f))
    if mode in ("inverse", "coherence"):
        pb = _smooth_log(f, np.asarray(mic["passband_db"], float), smooth_oct)
        inv = float(np.mean(pb[rb])) - pb
        g += np.where(inside, inv, np.interp(np.clip(f, lo, hi), f, inv))      # flat beyond the band limits
    if mode in ("coherence", "tilt"):
        _, snr = _coh_snr_db(mic, smooth_oct)
        w = coh_weight * (snr - float(np.mean(snr[rb])))
        g += np.where(inside, w, np.interp(np.clip(f, lo, hi), f, w))
    g = np.clip(g, -cut_db, boost_db)
    return f, g - float(np.mean(g[rb]))


# -- the front end -----------------------------------------------------------------------------------------------------
class FrontEnd:
    """The chain (module docstring). ``process(x)``: float mono 48 kHz, any block length -> the same length,
    float32, ``delay`` samples later."""

    def __init__(self, cfg: dict, mic: Optional[dict] = None, fs: int = FS) -> None:
        if fs != FS:
            raise ValueError("micfe runs at 48 kHz (show_service's stream rate)")
        self.cfg = copy.deepcopy(cfg)
        self.mic = mic
        self.identity = not (cfg.get("engine") or any(cfg.get(k) for k in ("eq", "lp_hz", "hp_hz", "bands", "vkick",
                                                                             "gain_db", "fsmooth", "derev")))
        self.N, self.H = int(cfg.get("n_fft", N_FFT)), int(cfg.get("hop", HOP))
        N, H = self.N, self.H
        self.win = np.sqrt(0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N) / N))        # periodic sqrt-Hann
        self.ola_norm = 1.0 / (N / (2.0 * H))                                       # sum of hann over hops
        self.f = np.fft.rfftfreq(N, 1.0 / fs)
        self.delay = N if not self.identity else 0
        self.a_frame = lambda tau_s: math.exp(-H / (max(tau_s, 1e-4) * fs))
        # the static gain per bin
        g0 = np.zeros(len(self.f))
        ec = cfg.get("eq")
        self.eq_db = None
        if ec:
            if not mic:
                raise ValueError("the eq stage needs a mic calibration (mic_params)")
            fe, ge = eq_curve(mic, ec.get("mode", "inverse"), float(ec.get("boost_db", 12.0)),
                              float(ec.get("cut_db", 12.0)), float(ec.get("coh_weight", 0.5)))
            g0 += np.interp(np.log(np.maximum(self.f, 1.0)), np.log(fe), ge)
            self.eq_db = (fe, ge)
        if cfg.get("lp_hz"):
            o = int(cfg.get("lp_order", 4))
            g0 += -10 * np.log10(1 + (self.f / float(cfg["lp_hz"])) ** (2 * o))
        if cfg.get("hp_hz"):
            o = int(cfg.get("hp_order", 2))
            g0 += -10 * np.log10(1 + (float(cfg["hp_hz"]) / np.maximum(self.f, 1.0)) ** (2 * o))
        g0 += float(cfg.get("gain_db", 0.0))
        self.g0 = 10 ** (g0 / 20)
        # bands
        bc = cfg.get("bands") or {}
        self.bc = bc
        lo_lim = (mic or {}).get("lo_hz", 40.0)
        hi_lim = min(fs / 2, (mic or {}).get("hi_hz", fs / 2))
        edges = [float(e) for e in bc.get("edges_hz", [0.7 * lo_lim, 250, 350, 500, 700, 1000, 1400, 2000, 2800,
                                                        4000, 5600, hi_lim])]
        self.edges = edges
        # band index per bin: 0 = under the first edge (out of band), nb+1 = over the last (out of band)
        self.bin_band = np.searchsorted(edges, self.f, side="right")
        nb = len(edges) + 1
        self.nb = nb
        M = np.zeros((len(self.f), nb))
        M[np.arange(len(self.f)), self.bin_band] = 1.0
        self.M = M
        self.inband = np.array([0 < b < nb - 1 for b in range(nb)])
        bounds = [(edges[b - 1], edges[b]) if 0 < b < nb - 1 else None for b in range(nb)]
        self.bounds = bounds
        w = bc.get("weights_db", 0.0)
        if w == "coherence":
            if not mic:
                raise ValueError("bands.weights_db 'coherence' needs a mic calibration (mic_params)")
            fc, snr = _coh_snr_db(mic)
            vals = [float(np.mean(snr[(fc >= a) & (fc < b)])) if ((fc >= a) & (fc < b)).any()
                    else float(np.interp(math.sqrt(a * b), fc, snr)) if bnd else math.nan
                    for bnd in bounds for a, b in [bnd or (1.0, 2.0)]]
            top = max(v for v in vals if math.isfinite(v))
            k = float(bc.get("coh_weight", 0.5))
            w = [max(-float(bc.get("max_cut_db", 18.0)), k * (v - top)) if math.isfinite(v)
                 else float(bc.get("out_db", 0.0)) for v in vals]
        if w == "snr":                                        # Wiener-like: the band's music-over-room SNR
            bs = (mic or {}).get("band_snr")
            if not bs:
                raise ValueError("bands.weights_db 'snr' needs the calibration's rec-music.wav and noise.wav")
            lf = np.log(np.asarray(bs["hz"], float))
            snr = np.asarray(bs["snr_db"], float)
            k = float(bc.get("snr_weight", 1.0))
            ref = float(np.max(snr))
            vals = [float(np.interp(math.log(math.sqrt(b[0] * b[1])), lf, snr)) if b else math.nan for b in bounds]
            w = [max(-float(bc.get("max_cut_db", 15.0)), k * (v - ref)) if math.isfinite(v) else 0.0 for v in vals]
        if isinstance(w, (int, float)):
            w = [float(w)] * nb
        self.weights_db = [round(float(v), 2) for v in w]
        self.w = 10 ** (np.array(w, float) / 20)
        nc = bc.get("norm")
        if nc:
            octs = np.array([math.log2(b[1] / b[0]) if b else 0.0 for b in bounds])
            self.pink = octs / max(octs.sum(), 1e-9)
        tc = bc.get("trans")
        if tc:
            tb = tc.get("bands")
            if tb == "low":                                   # the bands that survive this mic: under 500 Hz
                tb = [b for b in range(nb) if bounds[b] and bounds[b][1] <= 500.0]
            self.t_mask = np.array([bool(self.inband[b]) and (tb is None or b in tb) for b in range(nb)])
        kc = cfg.get("vkick")
        if kc:
            klo = float(kc.get("lo_hz", max(100.0, 0.7 * lo_lim)))
            khi = float(kc.get("hi_hz", 250.0))
            self.k_bins = (self.f >= klo) & (self.f < khi)
            n = int(0.25 * fs)
            t = np.arange(n) / fs
            f0, f1, tf = float(kc.get("f_hi", 110.0)), float(kc.get("f_lo", 50.0)), 0.03
            ph = 2 * np.pi * (f1 * t + (f0 - f1) * tf * (1 - np.exp(-t / tf)))
            kk = np.sin(ph) * np.exp(-t / (float(kc.get("decay_ms", 70.0)) / 1000)) * np.clip(t / 0.002, 0, 1)
            self.kick = kk / np.max(np.abs(kk))
            # where in the output stream a kick triggered by frame m goes (output samples are z[n - delay])
            self.k_off = int(kc.get("place", N - H))
        # per-frame constants (the one-frame path)
        self._winn = self.win * self.ola_norm
        if bc.get("norm"):
            nc = bc["norm"]
            self._na = self.a_frame(float(nc.get("tau_s", 2.0)))
            self._nfl = 10 ** (float(nc.get("floor_db", -40.0)) / 10)
            self._nex = 0.5 * float(nc.get("alpha", 1.0))
            self._nlo = 10 ** (-float(nc.get("max_cut_db", 20.0)) / 20)
            self._nhi = 10 ** (float(nc.get("max_boost_db", 20.0)) / 20)
        if bc.get("trans"):
            tc = bc["trans"]
            self._ta = self.a_frame(float(tc.get("slow_ms", 80.0)) / 1000)
            self._tex = 0.5 * float(tc.get("k", 1.0))
            self._tlo = 10 ** (-float(tc.get("max_cut_db", 12.0)) / 20)
            self._thi = 10 ** (float(tc.get("max_boost_db", 12.0)) / 20)
        xc = bc.get("xtrans")
        if xc:                                                # the onset boost from the bands' mean rise
            xb = xc.get("bands")
            if xb == "low":
                xb = [b for b in range(nb) if bounds[b] and bounds[b][1] <= 1000.0]
            self.x_mask = np.array([bool(self.inband[b]) and (xb is None or b in xb) for b in range(nb)])
            self._xa = self.a_frame(float(xc.get("slow_ms", 80.0)) / 1000)
            self._xk = float(xc.get("k", 1.0))
            self._xlo = -float(xc.get("max_cut_db", 12.0))
            self._xhi = float(xc.get("max_boost_db", 12.0))
            self._xclip = float(xc.get("rise_clip_db", 18.0))
        dc = cfg.get("derev")
        if dc:                                                # late-reverb suppression (per bin)
            rt = float(dc.get("rt60_s") or (mic or {}).get("rt60_s") or 0.5)
            td = float(dc.get("delay_ms", 40.0)) / 1000
            self._dd = max(1, int(round(td * fs / H)))
            self._dk = float(dc.get("beta", 1.0)) * 10 ** (-6.0 * self._dd * H / fs / rt)
            self._dmin = 10 ** (-float(dc.get("max_cut_db", 12.0)) / 20)
            self.rt60_used = rt
        sc = cfg.get("fsmooth")
        if sc:                                                # smooth |X| across +-oct/2 (log frequency)
            lf = np.log2(np.maximum(self.f, 20.0))
            W = (np.abs(lf[:, None] - lf[None, :]) <= float(sc.get("oct", 0.5)) / 2).astype(float)
            self._fsW = W / W.sum(axis=1, keepdims=True)
            self._fslim = 10 ** (float(sc.get("max_db", 12.0)) / 20)
        self.reset()
        self.latency_s = float(cfg["latency_ms"]) / 1000 if "latency_ms" in cfg else None

    # -- state
    def reset(self) -> None:
        N, H, nb = self.N, self.H, self.nb
        self.buf = np.zeros(4 * N)                 # input from the next frame's first sample (frame m: m H - (N - H))
        self.fill = N - H                          # ... the first frame's head is the N - H zeros before the stream
        self.n_in = 0                              # samples heard
        self.acc = np.zeros(N)                     # overlap-add accumulator: z from the next unfinished sample
        self.oq = np.zeros(8 * N)                  # finished output waiting, oq[r:w]: y[n] = z[n - N] (docstring)
        self.oq_r, self.oq_w = 0, H                # ... it starts as H zeros
        self.e_norm = None
        self.e_ref = None
        self.x_ref = None
        self.d_hist = None                         # derev: the last _dd frames' power per bin (a ring)
        self.d_i = 0
        self.p_in = self.p_out = None
        self.k_armed, self.k_last = True, -10 ** 9
        self.k_ref = None
        self.k_tail = np.zeros(0)
        self.m = 0                                 # frames done
        self.n_kicks = 0

    # -- processing
    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, np.float64).reshape(-1)
        n = len(x)
        if self.identity or n == 0:
            return x.astype(np.float32)
        N, H = self.N, self.H
        if self.fill + n > len(self.buf):
            self.buf = np.concatenate([self.buf[:self.fill], np.zeros(self.fill + n)])
        self.buf[self.fill:self.fill + n] = x
        self.fill += n
        nf = (self.fill - N) // H + 1 if self.fill >= N else 0
        if nf:
            if self.oq_w + nf * H > len(self.oq):           # compact (and grow) the output queue
                live = self.oq[self.oq_r:self.oq_w].copy()
                self.oq = np.zeros(max(8 * N, 2 * (len(live) + nf * H)))
                self.oq[:len(live)] = live
                self.oq_r, self.oq_w = 0, len(live)
            buf = self.buf
            if nf >= 8:                                     # many frames (offline): all at once
                idx = np.arange(nf)[:, None] * H + np.arange(N)[None, :]
                X = np.fft.rfft(buf[idx] * self.win, axis=1)
                fr = np.fft.irfft(X * self._gains(X), N, axis=1) * self._winn
                kicks = self._kicks(X) if self.cfg.get("vkick") else None
            for j in range(nf):
                if nf < 8:                                  # streaming: one frame, no lfilter
                    X1 = np.fft.rfft(buf[j * H:j * H + N] * self.win)
                    f1 = np.fft.irfft(X1 * self._gains1(X1), N) * self._winn
                    k1 = self._kicks(X1[None, :])[0] if self.cfg.get("vkick") else 0.0
                else:
                    f1 = fr[j]
                    k1 = kicks[j] if kicks is not None else 0.0
                self.acc += f1
                done = self.acc[:H].copy()                  # z over [s_m, s_m + H): no later frame reaches it
                if self.cfg.get("level", True):
                    done = self._level(buf[j * H:j * H + H], done)      # the input under the same samples
                if k1 > 0:
                    self._add_kick(k1)
                if len(self.k_tail):
                    k = min(H, len(self.k_tail))
                    done[:k] += self.k_tail[:k]
                    self.k_tail = self.k_tail[k:]
                self.oq[self.oq_w:self.oq_w + H] = done
                self.oq_w += H
                self.acc[:-H] = self.acc[H:]
                self.acc[-H:] = 0.0
            rest = self.fill - nf * H
            self.buf[:rest] = self.buf[nf * H:self.fill]
            self.fill = rest
        out = self.oq[self.oq_r:self.oq_r + n].astype(np.float32)
        self.oq_r += n
        if self.oq_r == self.oq_w:
            self.oq_r = self.oq_w = 0
        self.n_in += n
        return out

    def _gains1(self, X: np.ndarray) -> np.ndarray:
        """``_gains`` for one frame (the streaming path): the same recursions, written out."""
        bc = self.bc
        gb = self.w
        if bc.get("norm") or bc.get("trans") or bc.get("xtrans"):
            P = (X.real * X.real + X.imag * X.imag) @ self.M + EPS
        if bc.get("norm"):
            a = self._na
            E = a * (self.e_norm if self.e_norm is not None else P) + (1 - a) * P
            self.e_norm = E
            tot = float(E[self.inband].sum())
            gn = np.clip((self.pink * tot / np.maximum(E, tot * self._nfl)) ** self._nex, self._nlo, self._nhi)
            gb = gb * np.where(self.inband, gn, 1.0)
        if bc.get("trans"):
            prev = self.e_ref if self.e_ref is not None else P
            gt = np.clip((P / (prev + EPS)) ** self._tex, self._tlo, self._thi)
            self.e_ref = self._ta * prev + (1 - self._ta) * P
            gb = gb * np.where(self.t_mask, gt, 1.0)
        if bc.get("xtrans"):
            prev = self.x_ref if self.x_ref is not None else P
            r = np.clip(10 * np.log10(P / (prev + EPS)), -self._xclip, self._xclip)
            gx = 10 ** (min(max(self._xk * float(r[self.x_mask].mean()), self._xlo), self._xhi) / 20)
            self.x_ref = self._xa * prev + (1 - self._xa) * P
            gb = gb * np.where(self.inband, gx, 1.0)
        g = gb[self.bin_band] * self.g0
        if self.cfg.get("fsmooth"):
            m2 = X.real * X.real + X.imag * X.imag + EPS
            g = g * np.clip(np.sqrt(np.exp(self._fsW @ np.log(m2)) / m2), 1 / self._fslim, self._fslim)
        if self.cfg.get("derev"):
            m2 = X.real * X.real + X.imag * X.imag + EPS
            if self.d_hist is None:
                self.d_hist = np.zeros((self._dd, len(m2)))
            late = self._dk * self.d_hist[self.d_i]          # the power _dd frames ago, decayed
            self.d_hist[self.d_i] = m2
            self.d_i = (self.d_i + 1) % self._dd
            g = g * np.sqrt(np.maximum(1.0 - late / m2, self._dmin ** 2))
        return g

    def _gains(self, X: np.ndarray) -> np.ndarray:
        nf = X.shape[0]
        bc = self.bc
        gb = np.repeat(self.w[None, :], nf, axis=0)                  # (nf, nb)
        if bc.get("norm") or bc.get("trans") or bc.get("xtrans"):
            P = (np.abs(X) ** 2) @ self.M + EPS                       # band energies
        if bc.get("norm"):
            nc = bc["norm"]
            a = self.a_frame(float(nc.get("tau_s", 2.0)))
            zi = (a * (self.e_norm if self.e_norm is not None else P[0]))[None, :]
            E, zf = sps.lfilter([1 - a], [1, -a], P, axis=0, zi=zi)
            self.e_norm = zf[0] / a
            tot = (E * self.inband).sum(axis=1, keepdims=True)
            Ef = np.maximum(E, tot * 10 ** (float(nc.get("floor_db", -40.0)) / 10))
            gn = (self.pink[None, :] * tot / Ef) ** (0.5 * float(nc.get("alpha", 1.0)))
            gn = np.clip(gn, 10 ** (-float(nc.get("max_cut_db", 20.0)) / 20),
                         10 ** (float(nc.get("max_boost_db", 20.0)) / 20))
            gb = gb * np.where(self.inband[None, :], gn, 1.0)
        if bc.get("trans"):
            tc = bc["trans"]
            a = self.a_frame(float(tc.get("slow_ms", 80.0)) / 1000)
            prev0 = self.e_ref if self.e_ref is not None else P[0]
            zi = (a * prev0)[None, :]
            R, zf = sps.lfilter([1 - a], [1, -a], P, axis=0, zi=zi)
            self.e_ref = zf[0] / a
            Rprev = np.vstack([prev0[None, :], R[:-1]])               # the reference before this frame
            gt = (P / (Rprev + EPS)) ** (0.5 * float(tc.get("k", 1.0)))
            gt = np.clip(gt, 10 ** (-float(tc.get("max_cut_db", 12.0)) / 20),
                         10 ** (float(tc.get("max_boost_db", 12.0)) / 20))
            gb = gb * np.where(self.t_mask[None, :], gt, 1.0)
        if bc.get("xtrans"):
            a = self._xa
            prev0 = self.x_ref if self.x_ref is not None else P[0]
            R, zf = sps.lfilter([1 - a], [1, -a], P, axis=0, zi=(a * prev0)[None, :])
            self.x_ref = zf[0] / a
            Rprev = np.vstack([prev0[None, :], R[:-1]])
            r = np.clip(10 * np.log10(P / (Rprev + EPS)), -self._xclip, self._xclip)
            gx = 10 ** (np.clip(self._xk * r[:, self.x_mask].mean(axis=1), self._xlo, self._xhi) / 20)
            gb = gb * np.where(self.inband[None, :], gx[:, None], 1.0)
        g = gb[:, self.bin_band] * self.g0[None, :]
        if self.cfg.get("fsmooth"):
            m2 = X.real * X.real + X.imag * X.imag + EPS
            g = g * np.clip(np.sqrt(np.exp(np.log(m2) @ self._fsW.T) / m2), 1 / self._fslim, self._fslim)
        if self.cfg.get("derev"):
            m2 = X.real * X.real + X.imag * X.imag + EPS
            d = self._dd
            if self.d_hist is None:
                self.d_hist = np.zeros((d, m2.shape[1]))
            hist = np.vstack([np.roll(self.d_hist, -self.d_i, axis=0), m2])   # oldest first, then these frames
            late = self._dk * hist[:len(m2)]
            nf = len(m2)
            last = hist[-d:]                                  # the newest d frames, oldest first
            self.d_hist = last.copy()
            self.d_i = 0
            g = g * np.sqrt(np.maximum(1.0 - late / m2, self._dmin ** 2))
        return g

    def _kicks(self, X: np.ndarray) -> np.ndarray:
        """Per frame: the amplitude of a kick to start (0: none)."""
        kc = self.cfg["vkick"]
        Pk = (np.abs(X[:, self.k_bins]) ** 2).sum(axis=1) + EPS
        a = self.a_frame(float(kc.get("slow_ms", 150.0)) / 1000)
        thr = 10 ** (float(kc.get("thr_db", 6.0)) / 10)
        rearm = 10 ** (float(kc.get("rearm_db", 2.0)) / 10)
        refr = int(float(kc.get("refractory_ms", 110.0)) / 1000 * FS / self.H)
        floor = 10 ** (float(kc.get("floor_db", -90.0)) / 10)
        out = np.zeros(len(Pk))
        ref = self.k_ref if self.k_ref is not None else Pk[0]
        # the band's mean square in this frame (Parseval, one-sided bins): 2 sum |X|^2 / (N sum w^2)
        msk = 2.0 / (self.N * float(np.sum(self.win ** 2)))
        for j, p in enumerate(Pk.tolist()):
            r = p / (ref + EPS)
            if r < rearm:
                self.k_armed = True
            elif (r > thr and self.k_armed and self.m + j - self.k_last >= refr
                  and p * msk > floor):
                self.k_armed = False
                self.k_last = self.m + j
                out[j] = 10 ** (float(kc.get("gain_db", 6.0)) / 20) * math.sqrt(2 * p * msk)
                self.n_kicks += 1
            ref = a * ref + (1 - a) * p
        self.k_ref = ref
        self.m += len(Pk)
        return out

    def _add_kick(self, amp: float) -> None:
        """Start a kick in the stream being finished (at ``k_off`` into the frame just done)."""
        seg = np.concatenate([np.zeros(self.k_off), amp * self.kick])
        if len(self.k_tail) < len(seg):
            self.k_tail = np.pad(self.k_tail, (0, len(seg) - len(self.k_tail)))
        self.k_tail[:len(seg)] += seg

    def _level(self, x_hop: np.ndarray, z_hop: np.ndarray) -> np.ndarray:
        """Match the output's slow power (tau 2 s) to the input's: one gain per hop, from the powers before it."""
        a = math.exp(-self.H / (2.0 * FS))
        px, pz = float(np.mean(x_hop * x_hop)), float(np.mean(z_hop * z_hop))
        if self.p_in is None:
            self.p_in, self.p_out = px, pz
        g = min(max(math.sqrt((self.p_in + 1e-14) / (self.p_out + 1e-14)), 0.03), 30.0)
        self.p_in = a * self.p_in + (1 - a) * px
        self.p_out = a * self.p_out + (1 - a) * pz
        return z_hop * g

    def describe(self) -> dict:
        d = {"cfg": self.cfg, "n_fft": self.N, "hop": self.H, "delay_samples": self.delay}
        if self.eq_db is not None:
            f, g = self.eq_db
            d["eq_db_at"] = {str(int(hz)): round(float(np.interp(hz, f, g)), 1)
                             for hz in (100, 165, 250, 500, 1000, 2000, 4000, 7000)}
        if self.cfg.get("bands"):
            d["band_edges_hz"] = [round(e) for e in self.edges]
            d["band_weights_db"] = self.weights_db
        return d


# -- the variants tried (names are what the runner's --fe takes) -------------------------------------------------------
VARIANTS: dict[str, dict] = {
    "none": {},
    "pass": {"engine": True},                                  # the engine at unity: STFT + delay + level only
    # a static EQ from the calibration
    "inveq": {"eq": {"mode": "inverse", "boost_db": 12, "cut_db": 12}},
    "coheq": {"eq": {"mode": "coherence", "boost_db": 12, "cut_db": 18, "coh_weight": 0.5}},
    "tilt": {"eq": {"mode": "tilt", "boost_db": 12, "cut_db": 18, "coh_weight": 0.5}},
    # drop the top the processing scrambles
    "lp2k": {"lp_hz": 2000, "lp_order": 4},
    "lp1k": {"lp_hz": 1000, "lp_order": 4},
    # per-band adaptive normalisation (whitening to pink), half and full
    "norm50": {"bands": {"norm": {"alpha": 0.5, "tau_s": 2.0, "max_boost_db": 15, "max_cut_db": 15}}},
    "norm100": {"bands": {"norm": {"alpha": 1.0, "tau_s": 2.0, "max_boost_db": 20, "max_cut_db": 20}}},
    # reliability weights per band from the coherence
    "cohw": {"bands": {"weights_db": "coherence", "coh_weight": 0.5}},
    # onset boost in the bands that survive (under 500 Hz), and everywhere
    "trans_lo": {"bands": {"trans": {"k": 1.0, "slow_ms": 80, "bands": "low", "max_boost_db": 12, "max_cut_db": 12}}},
    "trans_all": {"bands": {"trans": {"k": 1.0, "slow_ms": 80, "max_boost_db": 12, "max_cut_db": 12}}},
    "trans_lo2": {"bands": {"trans": {"k": 2.0, "slow_ms": 80, "bands": "low", "max_boost_db": 18, "max_cut_db": 18}}},
    # the onset boost from the mean rise across bands (a real onset rises in many bands at once; the mic's
    # processing moves each band on its own)
    "xtrans": {"bands": {"xtrans": {"k": 1.0, "slow_ms": 80, "max_boost_db": 12, "max_cut_db": 12}}},
    "xtrans_lo": {"bands": {"xtrans": {"k": 1.0, "slow_ms": 80, "bands": "low", "max_boost_db": 12, "max_cut_db": 12}}},
    "xtrans2": {"bands": {"xtrans": {"k": 2.0, "slow_ms": 80, "max_boost_db": 18, "max_cut_db": 18}}},
    # smooth the spectrum across frequency (averages the processing's independent band gains)
    "fsmooth": {"fsmooth": {"oct": 0.5, "max_db": 12}},
    "fsmooth1": {"fsmooth": {"oct": 1.0, "max_db": 12}},
    # combinations (second round)
    "trans_lo_coheq": {"eq": {"mode": "coherence", "boost_db": 12, "cut_db": 18, "coh_weight": 0.5},
                       "bands": {"trans": {"k": 1.0, "slow_ms": 80, "bands": "low", "max_boost_db": 12,
                                           "max_cut_db": 12}}},
    "trans_lo_vkick": {"bands": {"trans": {"k": 1.0, "slow_ms": 80, "bands": "low", "max_boost_db": 12,
                                           "max_cut_db": 12}},
                       "vkick": {"thr_db": 6, "gain_db": 6, "refractory_ms": 110}},
    "trans_1k": {"bands": {"trans": {"k": 1.0, "slow_ms": 80, "bands": [1, 2, 3, 4, 5], "max_boost_db": 12,
                                     "max_cut_db": 12}}},
    "lp2k_trans_lo": {"lp_hz": 2000, "lp_order": 4,
                      "bands": {"trans": {"k": 1.0, "slow_ms": 80, "bands": "low", "max_boost_db": 12,
                                          "max_cut_db": 12}}},
    # band weights from the calibration's music-over-room SNR per band (de-weight where the room's sound competes)
    "snrw": {"bands": {"weights_db": "snr", "snr_weight": 1.0, "max_cut_db": 15}},
    "snrw_trans_lo": {"bands": {"weights_db": "snr", "snr_weight": 1.0, "max_cut_db": 15,
                                "trans": {"k": 1.0, "slow_ms": 80, "bands": "low", "max_boost_db": 12,
                                          "max_cut_db": 12}}},
    # band weights from the calibration's music-over-room SNR per band (de-weight where the room's sound competes)
    "snrw": {"bands": {"weights_db": "snr", "snr_weight": 1.0, "max_cut_db": 15}},
    "snrw_trans_lo": {"bands": {"weights_db": "snr", "snr_weight": 1.0, "max_cut_db": 15,
                                "trans": {"k": 1.0, "slow_ms": 80, "bands": "low", "max_boost_db": 12,
                                          "max_cut_db": 12}}},
    # late-reverb suppression with the calibration's RT60 (spectral subtraction of the decayed past)
    "derev": {"derev": {"delay_ms": 40, "beta": 1.0, "max_cut_db": 12}},
    "derev_trans_lo": {"derev": {"delay_ms": 40, "beta": 1.0, "max_cut_db": 12},
                       "bands": {"trans": {"k": 1.0, "slow_ms": 80, "bands": "low", "max_boost_db": 12,
                                           "max_cut_db": 12}}},
    "derev_lp2k_trans_lo": {"lp_hz": 2000, "lp_order": 4, "derev": {"delay_ms": 40, "beta": 1.0, "max_cut_db": 12},
                            "bands": {"trans": {"k": 1.0, "slow_ms": 80, "bands": "low", "max_boost_db": 12,
                                                "max_cut_db": 12}}},
    "derev2_trans_lo": {"derev": {"delay_ms": 40, "beta": 2.0, "max_cut_db": 18},
                        "bands": {"trans": {"k": 1.0, "slow_ms": 80, "bands": "low", "max_boost_db": 12,
                                            "max_cut_db": 12}}},
    "derev_trans_lo2": {"derev": {"delay_ms": 40, "beta": 1.0, "max_cut_db": 12},
                        "bands": {"trans": {"k": 2.0, "slow_ms": 80, "bands": "low", "max_boost_db": 18,
                                            "max_cut_db": 18}}},
    "derev_trans_lo_h480": {"hop": 480, "derev": {"delay_ms": 40, "beta": 1.0, "max_cut_db": 12},
                            "bands": {"trans": {"k": 1.0, "slow_ms": 80, "bands": "low", "max_boost_db": 12,
                                                "max_cut_db": 12}}},
    "trans_lo_h480": {"hop": 480, "bands": {"trans": {"k": 1.0, "slow_ms": 80, "bands": "low", "max_boost_db": 12,
                                                      "max_cut_db": 12}}},
    # the virtual kick
    "vkick": {"vkick": {"thr_db": 6, "gain_db": 6, "refractory_ms": 110}},
    "vkick_hi": {"vkick": {"thr_db": 6, "gain_db": 12, "refractory_ms": 110}},
}


def make(name_or_cfg, mic: Optional[dict] = None) -> FrontEnd:
    cfg = VARIANTS[name_or_cfg] if isinstance(name_or_cfg, str) else name_or_cfg
    return FrontEnd(cfg, mic)


def fingerprint(cfg: dict, mic: Optional[dict]) -> str:
    src = pathlib.Path(__file__).read_bytes()
    blob = json.dumps({"cfg": cfg, "mic": (mic or {}).get("model")}, sort_keys=True).encode()
    return "fe" + hashlib.sha256(src + blob).hexdigest()[:10]


# -- checks ------------------------------------------------------------------------------------------------------------
def test_loop(seconds: float = 8.0, bpm: float = 120.0, fs: int = FS, seed: int = 0) -> np.ndarray:
    """A plain drum loop (kick on beats, snare on 2 and 4, 8th hats): the latency and block-invariance probe."""
    rng = np.random.default_rng(seed)
    n = int(seconds * fs)
    x = np.zeros(n)
    T = 60.0 / bpm
    t = np.arange(int(0.25 * fs)) / fs
    kick = np.sin(2 * np.pi * (50 * t + 60 * 0.02 * (1 - np.exp(-t / 0.02)))) * np.exp(-t / 0.08)
    sn = rng.standard_normal(len(t)) * np.exp(-t / 0.05) * 0.5
    hat = sps.lfilter(*sps.butter(2, 6000, "high", fs=fs), rng.standard_normal(len(t))) * np.exp(-t / 0.01) * 0.3
    k = 0
    while k * T / 2 < seconds - 0.3:
        i = int(k * T / 2 * fs)
        seg = hat.copy()
        if k % 2 == 0:
            seg += kick
        if k % 4 == 2:
            seg += sn
        x[i:i + len(seg)] += seg[:max(0, min(len(seg), n - i))]
        k += 1
    return (x / np.sqrt(np.mean(x * x)) * 10 ** (-20 / 20)).astype(np.float32)


def onset_env(x: np.ndarray, fs: int = FS, hop: int = 48) -> np.ndarray:
    """A 1 ms spectral-flux onset envelope (the latency probe)."""
    f, t, Z = sps.stft(x, fs, nperseg=512, noverlap=512 - hop, boundary=None, padded=False)
    m = np.log1p(1000 * np.abs(Z[(f > 60) & (f < 4000)]))
    return np.maximum(0, np.diff(m, axis=1)).sum(axis=0)


def measure_latency(fe: FrontEnd, fs: int = FS) -> float:
    """Seconds by which the chain delays a drum loop's onsets (its fixed delay + what the cross-correlation of 1 ms
    onset envelopes finds on top)."""
    x = test_loop()
    fe2 = copy.deepcopy(fe)
    fe2.reset()
    y = fe2.process(x)
    D = fe2.delay
    y = np.concatenate([y[D:], np.zeros(D)])
    a, b = onset_env(x), onset_env(y)
    n = min(len(a), len(b))
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    lags = np.arange(-10, 30)
    c = [float(np.dot(a[max(0, -L):n - max(0, L)], b[max(0, L):n - max(0, -L)])) for L in lags]
    return D / fs + float(lags[int(np.argmax(c))]) * 48 / fs


def stream_cost(fe: FrontEnd, x: np.ndarray, block: int = 128) -> float:
    """CPU ms of front end per second of audio, streaming ``block``-frame blocks (as show-rx would)."""
    if fe.identity or len(x) < FS:
        return 0.0 if fe.identity else math.nan
    import time
    f2 = copy.deepcopy(fe)
    f2.reset()
    t = time.process_time()                        # CPU time: the host is shared, wall time isn't the cost
    for i in range(0, len(x), block):
        f2.process(x[i:i + block])
    return (time.process_time() - t) * 1000.0 / (len(x) / FS)


def selftest() -> None:
    """Block invariance (any cut of the stream gives the same output) for every variant; latency; cost; unity."""
    mic = mic_params(PC22_MODEL) if PC22_MODEL.exists() else None
    x = test_loop(4.0)
    for name, cfg in VARIANTS.items():
        if (cfg.get("eq") or (cfg.get("bands") or {}).get("weights_db") == "coherence") and mic is None:
            continue
        a, b = make(name, mic), make(name, mic)
        ya = a.process(x)
        rng = np.random.default_rng(1)
        parts, i = [], 0
        while i < len(x):
            k = int(rng.choice([1, 7, 128, 256, 480, 1000, 3000]))
            parts.append(b.process(x[i:i + k]))
            i += k
        yb = np.concatenate(parts)
        err = float(np.max(np.abs(ya - yb)))
        lat = measure_latency(a) * 1000
        D = a.delay
        rms = 20 * math.log10(float(np.sqrt(np.mean(ya.astype(float) ** 2))) + 1e-12)
        unity = float(np.max(np.abs(ya[D:].astype(float) - x[:len(x) - D]))) if name == "pass" else math.nan
        cost = stream_cost(a, test_loop(10.0))
        print(f"{name:10s} block err {err:.1e}  latency {lat:5.1f} ms  rms {rms:6.1f} dBFS  cost {cost:5.1f} ms/s"
              + (f"  unity err {unity:.1e}" if name == "pass" else "") + (f"  kicks {a.n_kicks}" if a.cfg.get("vkick") else ""))
        assert err < 1e-5, name


def bench(variants: Optional[list[str]] = None, seconds: float = 20.0, reps: int = 3) -> dict:
    """CPU cost next to madmom's: each variant streaming 128-frame blocks, and the clock's MadmomActivation (2 nets)
    per 441-sample hop, on the same drum loop, in this process, interleaved ``reps`` times (the host is shared:
    the ratio is the robust number). Needs show_service's env (madmom)."""
    import time
    import sys
    sys.path.insert(0, str(REPO))
    import soxr
    from show_service import clock as clockmod
    mic = mic_params(PC22_MODEL) if PC22_MODEL.exists() else None
    x = test_loop(seconds)
    x44 = soxr.resample(x, FS, 44100).astype(np.float32)
    act = clockmod.MadmomActivation(nets=2)
    names = variants or [v for v in VARIANTS if v != "none"]
    res: dict = {"madmom2_ms_s": [], **{v: [] for v in names}}
    for _ in range(reps):
        act.reset()
        t = time.process_time()
        for i in range(0, len(x44) - 441, 441):
            act.process_hop(x44[i:i + 441])
        res["madmom2_ms_s"].append((time.process_time() - t) * 1000 / seconds)
        for v in names:
            res[v].append(stream_cost(make(v, mic), x))
    out = {k: float(np.median(v)) for k, v in res.items()}
    m = out["madmom2_ms_s"]
    print(f"madmom 2 nets (the clock's MadmomActivation): {m:.1f} CPU ms per audio s")
    for v in names:
        print(f"  {v:10s} {out[v]:6.1f} ms/s = {out[v] / m:5.2f} x madmom 2 nets")
    return out


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) > 1 and _sys.argv[1] == "bench":
        bench(_sys.argv[2].split(",") if len(_sys.argv) > 2 else None)
    else:
        selftest()
