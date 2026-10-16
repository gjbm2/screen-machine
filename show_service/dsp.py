"""dsp: the room's ears in real time. Audio blocks become ``music.features`` (25 Hz) and ``music.hit`` (per hit)
(docs/SHOW_ENGINE.md §4, §6.3, §11.1).

One ``Features`` lives on the hot thread ("show-rx"). ``__main__`` passes ``Features.process`` as ``on_audio`` and
calls ``Features.tick`` from ``on_tick``. Nothing here locks, waits, logs or does I/O, and nothing is thread-safe
except ``latest()`` and ``stats()``, which hand out snapshots swapped in by reference. Every buffer is allocated
when the sample rate or channel count is first seen; a steady stream allocates no sample buffers (only numpy view
headers, the 25 Hz message dicts and a hit's dict).

  process(stream, n0, t0_host_s, pcm, fs, flags)     = contract.OnAudio
      pcm float32 (frames, channels) or (frames,), any block size. Blocks over CHUNK (1024) frames are handled in
      1024-frame pieces. Channels are averaged to mono for everything except loudness: R128 measures the interleaved
      channels, as the standard does. Sample i of a block sounds at t0_host_s + i / fs (acoustic host seconds).
      A new fs or channel count rebuilds every rate-dependent table: band filters, envelopes, mel bank, FFT bins
      and the R128 state. 48 kHz filters never run on 44.1 kHz audio.
  tick(now_s)          publishes music.features at publish_hz when a hop arrived since the last one; when the
                       source stops, a fade (below), then nothing
  latest()             the last FeaturesData (or None)
  chroma12()           None: the medium lane (§4) is not built yet
  reset()              filters, envelopes, onset frames, the analysis ring and the hit detectors. Kept: the R128
                       state (rebuilt on fs/ch change) and the normalisation history. A new ``stream`` name clears
                       the history too (percentiles, presence, punch/drive, the spectrum's smoothing).
  gate_ref_lufs        the mode's expected loudness (the director sets it; §6.3), default -30
  stats()              blocks, hops, gaps, publishes, fades, hits per band, hop_ms_p50/p95 (a hop's blocks included),
                       block_ms_p95, tick_ms_p95 (a publish), fs, channels, stream, kernel

Flags (contract):
  AUDIO_GAP      zero-fill for packets that never came. It advances time only (the next block brings its own
                 t0): no filter, ring, loudness, level, onset or hit sees it. Counted in ``gaps``.
  AUDIO_RESUME   the first real block after a gap: no hit fires in it or in the hop after it, and no onset until
                 the splice has passed the 2048 window's centre (+ a hop): the splice is not a transient.
  AUDIO_SEGMENT  reset() (the receiver also sets it on the first block after the active stream switches).

Hit path, every block (§4: per packet, 2.7 ms at 128 frames), ungated:
  band_b  = Butterworth band-pass (contract.BAND_EDGES_HZ, order 2 = 2 sections, state kept) of mono + 1e-9 dither
  fast_b  = 2-pole critically damped low-pass (tau 2.5 ms) of band_b^2, per sample   the envelope follower
  slow_b  = 1-pole (tau 150 ms) at block rate on the block's mean fast_b             the adaptive threshold's
                                                                                     baseline, and the band's level
  Detection runs in pieces of at most HIT_BLOCK (128) samples whatever the block size, so hits don't depend on it.
  A band fires when it is armed and fast > max(slow x on_b, -70 dB) at least 60 ms after its last hit, with slow
  as it stood at the piece's start (it moves under 2 % in 2.7 ms). on_b is +10 dB on sub, +8 on bass and low_mid,
  +7 above: a narrow band's envelope swings more in noise (in pink noise sub fired 55 times in 30 s at +7 dB, 3 at
  +10; kicks were all caught at +9 within 6 ms). It is then disarmed until fast < slow x +3 dB (hysteresis). A
  crossing inside the refractory or a suppression window is swallowed (it disarms, nothing fires). After a reset
  bands start disarmed, no hit fires for 50 ms, and slow is the running mean until 150 ms have passed. Only a band
  whose piece max (armed) or min (disarmed) crosses is scanned sample by sample, so a quiet piece costs two
  reductions.
  t_ms     = the acoustic time of the crossing sample (no group-delay correction: about 3 ms on bass, 8 on sub)
  strength = clamp((peak_dB - p05) / (p95 + 12 - p05)): peak = the largest fast envelope in the 5 ms from the
             crossing (within the block), p05/p95 = that band level's rolling percentiles
  -> publish {"type": "music.hit", "data": contract.HitData} at once (the bus sends it only to want-ers).
  dB here are re a full-scale sine in the band: 10 log10(mean square) + 3.01.

Fast lane, every hop (256 samples, 5.3 ms at 48 kHz):
  2048-point Hann FFT -> 40-band log-mel power (HTK mel, 30 Hz .. 16 kHz, triangles averaged, dB with a -80 dB
  floor) -> SuperFlux: flux = sum_k max(0, mel_k(j) - maxfilt3(mel(j - 2))_k): lag 2 hops (madmom's rule: half
  the window less where the Hann passes 0.5, in hops), a 3-band max filter. A transient peaks the flux when it
  reaches the window's middle, a quarter window (512 samples, 2 hops) after the sound, so an onset's time is the
  hop's end - 2048/4/fs (the 512-hop diagnostic log ``_olog_t/_olog_v`` keeps that). The publish takes the
  largest flux since the previous publish.

At publish (tick, 25 Hz), from the latest hop:
  spectrum32  4096-point Hann FFT. Band k = the summed bin power whose centre is in SPEC_EDGES_HZ[k]..[k+1]; a
              band with no bin takes the bin nearest its centre. dB; ref = the rolling p95 over 60 s of the loudest
              band (at least -90 dB); value = clamp((dB - (ref - 60)) / 60) x gate, then tau 300 ms both ways.
              One shared ref, so bass still reads above air for a falling spectrum.
  brightness  clamp(log2(centroid / 50) / log2(16000 / 50)), centroid over the 30 Hz-16 kHz bins.
  noisiness   spectral flatness (geometric / arithmetic mean) of the 32 bands' mean bin power.
              Both read 0 when the 30 Hz-16 kHz power is under -90 dBFS (silence has no colour).
  lufs_m      pyebur128 momentary every publish; lufs_s short-term at most 5 Hz, held between reads (it costs
              0.2-0.45 ms a read). -inf reads -70.
  gate        smoothstep(gate_ref_lufs - 15, gate_ref_lufs, lufs_s)                                      (§6.3)
  normalised  clamp((x - lo) / (hi - lo)): lo/hi = rolling p05/p95, the 60 s pair averaged with the 10 min pair.
              Decimated rings (60 s at 5 Hz, 10 min at 1 Hz) are partitioned once a second (about 50 us). Each
              row has a floor under hi and a minimum span, so silence or a steady tone is not stretched to 1.
  bands[b]    level    = normalised(slow_b in dB) x gate
              hits     = this band's hits since the previous publish (ungated count)
              presence = EMA tau 1 s of level
  onset       normalised(largest flux since the last publish) x gate
  energy      normalised(lufs_m) x gate                                              (the arousal proxy, §6.3)
  punch/drive exactly as contract.FeaturesData defines them (stream_service/music_direction.py's formulas, fed by
              low hits): a low hit is a sub or bass hit, both within 60 ms counting once.
              punch = clamp((peak - 0.10) / 0.60): peak = the low hits' strength, held, decaying tau 4 s
              drive = EMA tau 8 s of 0.7 x clamp(rate / 2.2) + 0.3 x punch: rate = low hits per second over the
              last 8 s, 0 until 4 s of audio. Both computed ungated, published x gate.
  peak/rms    over the blocks since the previous publish, dBFS, floored at -120.
  t_ms        the acoustic time of the latest hop's last sample.

A stopped source (no hop for FADE_AFTER_S, 0.1 s = 2.5 publish periods, measured on tick's host clock): tick keeps
publishing at publish_hz with gate 0 until FADE_FOR_S (1.5 s) after the latest hop: spectrum32 and presence fall
with their own taus (300 ms, 1 s), energy, onset, punch, drive, band levels and hits read 0, LUFS -70 and dBFS
-120, brightness and noisiness 0; t_ms runs on from the last real frame at the host's rate. Then one all-zero
frame and nothing until audio returns (``stats()['fades']`` counts these frames). So the renderer (sound_view
keeps a music.features spectrum for 5 s) and Art-Net out see the sound go, not freeze.

Budget (README): a hop, its blocks included, <= 0.45 ms p95 under render load. The band filters run through scipy's
in-place ``_sosfilt`` kernel (about 3 us a call; the public ``sosfilt`` costs about 60 us in argument checks). It
is checked against ``sosfilt`` at import, and the public function is the fallback.
"""

from __future__ import annotations

import collections
import math
import time
from typing import Optional

import numpy as np
import pyebur128
from scipy.signal import butter, sosfilt

from .contract import (AUDIO_GAP, AUDIO_RESUME, AUDIO_SEGMENT, BAND_EDGES_HZ, BAND_NAMES, MUSIC_FEATURES, MUSIC_HIT,
                       SPEC_BANDS, SPEC_EDGES_HZ, SPEC_HI_HZ, SPEC_LO_HZ, SPEC_REF_S, SPEC_TAU_S, SPEC_WINDOW_DB,
                       FeaturesData, Publish)

# -- constants --------------------------------------------------------------------------------------------------------
HOP = 256
CHUNK = 1024                    # the largest piece one pass handles; bigger blocks are split
HIT_BLOCK = 128                 # hit detection runs in pieces of at most this, so hits don't depend on block size
FAST_N = 2048                   # fast-lane FFT (log-mel, SuperFlux)
SPEC_N = 4096                   # spectrum32 / brightness / noisiness FFT (the lowest spectrum band is 7 Hz wide)
RING_N = 8192                   # analysis ring: > SPEC_N + a hop + CHUNK
MEL_BANDS = 40
MEL_LO_HZ, MEL_HI_HZ = 30.0, 16000.0
MEL_FLOOR = 1e-8                # -80 dB: silence makes no flux
SF_LAG = 0                      # 0: madmom's rule, (window/2 - where the Hann passes 0.5) / hop = 2 at hop 256

NB = len(BAND_NAMES)            # 6
BAND_ORDER = 2                  # per edge: a 4th-order band-pass, 2 sections
ENV_FAST_S = 0.0025
ENV_SLOW_S = 0.150
DB_SINE = 3.0103                # 10 log10(mean square) + this = dB re a full-scale sine
HIT_ON_DB = (10.0, 8.0, 8.0, 7.0, 7.0, 7.0)   # per band: narrow low bands fluctuate more in noise (sub is 40 Hz wide)
HIT_OFF_DB = 3.0
HIT_FLOOR_DB = -70.0
REFRACTORY_S = 0.060
HIT_PEAK_S = 0.005
STRENGTH_HEADROOM_DB = 12.0
DITHER = 1e-9
RESET_HIT_SUPPRESS_S = 0.050    # after a reset the zeroed filters ring up and the baseline settles: no hits

LOW_MERGE_S = 0.060             # a sub and a bass hit this close are one low hit
PUNCH_TAU_S = 4.0
PEAK_LO, PEAK_HI = 0.10, 0.70
DRIVE_TAU_S = 8.0
RATE_WINDOW_S, RATE_MIN_SPAN_S, RATE_FULL = 8.0, 4.0, 2.2
PRESENCE_TAU_S = 1.0
FADE_AFTER_S = 0.1               # no hop for this long (2.5 publish periods): the source stopped, fade
FADE_FOR_S = 1.5                 # ... at 25 Hz with the gate at 0 until this long, then one all-zero frame

SPEC_REF_FLOOR_DB = -90.0
SILENCE_DB = -90.0
LUFS_FLOOR, DBFS_FLOOR = -70.0, -120.0
LUFS_S_EVERY_S = 0.2
GATE_SPAN_LU = 15.0
BRIGHT_LO_HZ, BRIGHT_HI_HZ = 50.0, 16000.0
_BRIGHT_OCT = math.log2(BRIGHT_HI_HZ / BRIGHT_LO_HZ)

# rolling percentiles: rows 0-5 band level dB, 6 onset flux, 7 lufs_m, 8 the loudest spectrum32 band dB
Q_ONSET, Q_LUFS, Q_SPEC = NB, NB + 1, NB + 2
Q_ROWS = NB + 3
Q_SHORT_S, Q_SHORT_HZ = SPEC_REF_S, 5.0         # 60 s: also the spectrum ref's window (§11.1)
Q_LONG_S, Q_LONG_HZ = 600.0, 1.0
Q_UPDATE_S = 1.0
Q_MIN_COUNT = 5
#                       bands x6            onset  lufs   spec
Q_LO0 = np.array([-70.0] * NB + [0.0, -50.0, -40.0])
Q_HI0 = np.array([-20.0] * NB + [50.0, -20.0, -40.0])
Q_HI_FLOOR = np.array([HIT_FLOOR_DB] * NB + [10.0, -60.0, SPEC_REF_FLOOR_DB])
Q_MIN_SPAN = np.array([12.0] * NB + [10.0, 10.0, 0.0])

_MODES = pyebur128.MeasurementMode.MODE_M | pyebur128.MeasurementMode.MODE_S


# -- the in-place biquad kernel ---------------------------------------------------------------------------------------
def _public_sosfilt(sos: np.ndarray, x: np.ndarray, zi: np.ndarray) -> None:
    """The fallback, same contract as the kernel: x (signals, n) filtered in place, zi (signals, sections, 2)."""
    y, zf = sosfilt(sos, x, axis=-1, zi=zi.transpose(1, 0, 2))
    x[...] = y
    zi[...] = zf.transpose(1, 0, 2)


def _pick_kernel():
    try:
        from scipy.signal._sosfilt import _sosfilt as kernel
        rng = np.random.default_rng(1)
        sos = butter(2, [60.0, 250.0], btype="bandpass", fs=48000, output="sos")
        x = rng.standard_normal((2, 300))
        want, _ = sosfilt(sos, x, axis=-1, zi=np.zeros((sos.shape[0], 2, 2)))
        got, zi = x.copy(), np.zeros((2, sos.shape[0], 2))
        kernel(np.ascontiguousarray(sos), got, zi)
        if np.allclose(got, want, rtol=1e-9, atol=1e-12):
            return kernel, "scipy-private"
    except Exception:                                   # noqa: BLE001 - any trouble: the public path
        pass
    return _public_sosfilt, "public"


_filt, KERNEL = _pick_kernel()


# -- tables per sample rate -------------------------------------------------------------------------------------------
def _hann(n: int) -> np.ndarray:
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n) / n)          # periodic


def _mel_bank(freqs: np.ndarray, n: int, lo: float, hi: float) -> np.ndarray:
    """(n, bins) HTK-mel triangles over ``freqs``, each normalised to sum 1 (a band's mean bin power). A triangle
    that catches no bin takes the bin nearest its centre."""
    def mel(f):
        return 2595.0 * np.log10(1.0 + np.asarray(f) / 700.0)

    pts = 700.0 * (10.0 ** (np.linspace(mel(lo), mel(hi), n + 2) / 2595.0) - 1.0)
    w = np.zeros((n, freqs.size))
    for i in range(n):
        left, c, right = pts[i], pts[i + 1], pts[i + 2]
        tri = np.minimum((freqs - left) / (c - left), (right - freqs) / (right - c))
        w[i] = np.clip(tri, 0.0, None)
        if w[i].sum() <= 0.0:
            w[i, int(np.argmin(np.abs(freqs - c)))] = 1.0
        w[i] /= w[i].sum()
    return w


def _spec_matrix(freqs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(32, bins) 0/1: bin j counts in band k when SPEC_EDGES_HZ[k] <= f_j < SPEC_EDGES_HZ[k+1]; an empty band takes
    the bin nearest its geometric centre. Also returns each band's bin count (>= 1)."""
    w = np.zeros((SPEC_BANDS, freqs.size))
    for k in range(SPEC_BANDS):
        lo, hi = SPEC_EDGES_HZ[k], SPEC_EDGES_HZ[k + 1]
        sel = (freqs >= lo) & (freqs < hi)
        if sel.any():
            w[k, sel] = 1.0
        else:
            w[k, int(np.argmin(np.abs(freqs - math.sqrt(lo * hi))))] = 1.0
    return w, w.sum(axis=1)


class _Tables:
    """Everything that depends on fs (built once per rate, cached)."""

    def __init__(self, fs: int) -> None:
        self.fs = fs
        nyq = fs / 2.0
        sos = []
        for name in BAND_NAMES:
            lo, hi = BAND_EDGES_HZ[name]
            hi = min(hi, 0.45 * fs)
            if hi > lo * 1.2:
                s = butter(BAND_ORDER, [lo, hi], btype="bandpass", fs=fs, output="sos")
            else:                                        # a rate too low for this band: its top half only
                s = butter(2 * BAND_ORDER, min(lo, 0.4 * fs), btype="highpass", fs=fs, output="sos")
            sos.append(np.ascontiguousarray(s, dtype=np.float64))
        self.sos = sos
        a = math.exp(-1.0 / (ENV_FAST_S * fs))
        self.sos_fast = np.array([[(1.0 - a) ** 2, 0.0, 0.0, 1.0, -2.0 * a, a * a]])
        self.peak_n = max(1, int(round(HIT_PEAK_S * fs)))
        self.slow_n = ENV_SLOW_S * fs
        self.reset_supp_n = int(round(RESET_HIT_SUPPRESS_S * fs))

        self.hann_fast = _hann(FAST_N)
        freqs_f = np.fft.rfftfreq(FAST_N, 1.0 / fs)
        scale_f = (2.0 / self.hann_fast.sum()) ** 2       # a full-scale sine's peak bin reads 1.0
        mel_hi = min(MEL_HI_HZ, 0.95 * nyq)
        self.nb_fast = int(min(freqs_f.size, np.searchsorted(freqs_f, mel_hi) + 2))   # bins the mel bank uses
        self.mel_w = np.ascontiguousarray(_mel_bank(freqs_f[:self.nb_fast], MEL_BANDS, MEL_LO_HZ, mel_hi) * scale_f)

        self.hann_spec = _hann(SPEC_N)
        freqs_s = np.fft.rfftfreq(SPEC_N, 1.0 / fs)
        scale_s = (2.0 / self.hann_spec.sum()) ** 2
        self.nb_spec = int(min(freqs_s.size, np.searchsorted(freqs_s, SPEC_HI_HZ) + 2))
        w, nbins = _spec_matrix(freqs_s[:self.nb_spec])
        self.spec_w = np.ascontiguousarray(w * scale_s)
        self.spec_nbins = nbins
        self.sel0 = int(np.searchsorted(freqs_s, SPEC_LO_HZ))
        self.sel1 = int(min(freqs_s.size, np.searchsorted(freqs_s, SPEC_HI_HZ)))
        self.freqs_sel = np.ascontiguousarray(freqs_s[self.sel0:self.sel1])
        self.scale_s = scale_s
        self.onset_delay_s = (FAST_N / 4) / fs           # a transient peaks the flux when it reaches the Hann's middle


# -- rolling percentiles ----------------------------------------------------------------------------------------------
class _Rolling:
    """Approximate rolling p05/p95 per row. Decimated rings (60 s at 5 Hz, 10 min at 1 Hz) are partitioned once a
    second; ``lo``/``hi`` mix the two pairs (once the long ring has data), then a floor under hi and a minimum span
    apply. ``hi60`` is the 60 s p95 alone (the spectrum's ref)."""

    def __init__(self) -> None:
        self.n_s = int(Q_SHORT_S * Q_SHORT_HZ)
        self.n_l = int(Q_LONG_S * Q_LONG_HZ)
        self.ring_s = np.zeros((Q_ROWS, self.n_s))
        self.ring_l = np.zeros((Q_ROWS, self.n_l))
        self.scratch = np.zeros((Q_ROWS, max(self.n_s, self.n_l)))
        self.lo = Q_LO0.copy()
        self.hi = Q_HI0.copy()
        self.hi60 = Q_HI0.copy()
        self._lo_s, self._hi_s = Q_LO0.copy(), Q_HI0.copy()
        self._lo_l, self._hi_l = Q_LO0.copy(), Q_HI0.copy()
        self.clear()

    def clear(self) -> None:
        self.c_s = self.c_l = 0        # values held
        self.i_s = self.i_l = 0        # next slot
        self.t_s = self.t_l = self.t_q = -math.inf
        self.lo[:] = Q_LO0
        self.hi[:] = Q_HI0
        self.hi60[:] = Q_HI0
        self.ready = False

    def push(self, t: float, vals: np.ndarray) -> None:
        changed = False
        if t - self.t_s >= 1.0 / Q_SHORT_HZ - 1e-6:
            self.t_s = t
            self.ring_s[:, self.i_s] = vals
            self.i_s = (self.i_s + 1) % self.n_s
            self.c_s = min(self.c_s + 1, self.n_s)
            changed = True
        if t - self.t_l >= 1.0 / Q_LONG_HZ - 1e-6:
            self.t_l = t
            self.ring_l[:, self.i_l] = vals
            self.i_l = (self.i_l + 1) % self.n_l
            self.c_l = min(self.c_l + 1, self.n_l)
        if changed and self.c_s >= Q_MIN_COUNT and (t - self.t_q >= Q_UPDATE_S or self.c_s < 25):
            self.t_q = t
            self._quantiles(self.ring_s, self.c_s, self._lo_s, self._hi_s)
            if self.c_l >= Q_MIN_COUNT:
                self._quantiles(self.ring_l, self.c_l, self._lo_l, self._hi_l)
                np.add(self._lo_s, self._lo_l, out=self.lo)
                np.add(self._hi_s, self._hi_l, out=self.hi)
                self.lo *= 0.5
                self.hi *= 0.5
            else:
                self.lo[:] = self._lo_s
                self.hi[:] = self._hi_s
            np.maximum(self._hi_s, Q_HI_FLOOR, out=self.hi60)
            np.maximum(self.hi, Q_HI_FLOOR, out=self.hi)
            np.minimum(self.lo, self.hi - Q_MIN_SPAN, out=self.lo)
            self.ready = True

    def _quantiles(self, ring: np.ndarray, count: int, lo: np.ndarray, hi: np.ndarray) -> None:
        s = self.scratch[:, :count]
        np.copyto(s, ring[:, :count])
        k05, k95 = int(round(0.05 * (count - 1))), int(round(0.95 * (count - 1)))
        s.partition((k05, k95), axis=1)
        lo[:] = s[:, k05]
        hi[:] = s[:, k95]


class _Views:
    """Per block length: (6, L) views onto flat preallocated buffers (C-contiguous, as the kernel needs)."""
    __slots__ = ("work", "band", "row0", "rest", "rows", "fast", "frows", "ones", "rowbool")

    def __init__(self, f: "Features", n: int) -> None:
        self.work = f._work[:n]
        self.band = f._band_buf[:NB * n].reshape(NB, n)
        self.row0, self.rest = self.band[0], self.band[1:]
        self.rows = tuple(self.band[b:b + 1] for b in range(NB))
        self.fast = f._fast_buf[:NB * n].reshape(NB, n)
        self.frows = tuple(self.fast[b] for b in range(NB))
        self.ones = f._ones[:n]
        self.rowbool = f._rowbool[:n]


def _clamp01(x: float) -> float:
    return 0.0 if x <= 0.0 else 1.0 if x >= 1.0 else x


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = _clamp01((x - e0) / (e1 - e0))
    return t * t * (3.0 - 2.0 * t)


class Features:
    """Audio blocks in, music.features (25 Hz) and music.hit out. Hot thread only (see the module docstring)."""

    gate_ref_lufs: float = -30.0

    def __init__(self, *, publish: Optional[Publish] = None, hop: int = HOP, publish_hz: float = 25.0) -> None:
        if not 32 <= hop <= CHUNK:
            raise ValueError(f"hop must be 32..{CHUNK}")
        self._publish = publish
        self.hop = int(hop)
        self._period = 1.0 / float(publish_hz)
        self._tables: dict[int, _Tables] = {}
        self._T: Optional[_Tables] = None
        self._fs = 0
        self._ch = 0
        self._stream: Optional[str] = None
        self._r128 = None
        self._latest: Optional[FeaturesData] = None

        # block buffers (hit path)
        self._work = np.zeros(CHUNK)
        self._band_buf = np.zeros(NB * CHUNK)
        self._fast_buf = np.zeros(NB * CHUNK)
        self._ones = np.ones(CHUNK)
        self._rowbool = np.zeros(CHUNK, dtype=bool)
        self._views: dict[int, _Views] = {}
        self._dither = np.random.default_rng(0x5EED).standard_normal(2 * CHUNK) * DITHER
        self._dpos = 0
        self._zi_band = np.zeros((NB, 1, 2, 2))
        self._zi_rows = tuple(self._zi_band[b] for b in range(NB))
        self._zi_fast = np.zeros((NB, 1, 2))
        self._slow = np.zeros(NB)                     # mean square; also the band level
        self._fmax = np.zeros(NB)
        self._fmin = np.zeros(NB)
        self._fsum = np.zeros(NB)
        self._thr_on = np.zeros(NB)
        self._thr_off = np.zeros(NB)
        self._any_on = np.zeros(NB, dtype=bool)
        self._any_off = np.zeros(NB, dtype=bool)
        self._armed = [False] * NB
        self._ok_t = [-math.inf] * NB
        self._floor_p = 10.0 ** ((HIT_FLOOR_DB - DB_SINE) / 10.0)
        self._k_on = 10.0 ** (np.array(HIT_ON_DB) / 10.0)
        self._k_off = 10.0 ** (HIT_OFF_DB / 10.0)

        # analysis ring (mono, double-written so any window is contiguous) and the fast lane
        self._ring = np.zeros(2 * RING_N)
        self._rpos = 0
        self._hop_fill = 0
        self._hop_rpos = 0
        self._frame_f = np.zeros(FAST_N)
        self._spec_f = np.zeros(FAST_N // 2 + 1, dtype=np.complex128)
        self._re_f, self._im_f = self._spec_f.real, self._spec_f.imag
        self._pw_f = np.zeros(FAST_N // 2 + 1)
        self._tmp_f = np.zeros(FAST_N // 2 + 1)
        self._mel = np.zeros(MEL_BANDS)
        self._mx1 = np.zeros(MEL_BANDS - 1)
        self._dflux = np.zeros(MEL_BANDS)
        self._sf_lag = SF_LAG or max(1, int(round((FAST_N / 4) / self.hop)))
        self._mf = np.full((self._sf_lag + 1, MEL_BANDS), 10.0 * math.log10(MEL_FLOOR))   # max-filtered frames
        self._olog_t = np.zeros(512)                  # (onset time, flux) of the last 512 hops: tests, diagnosis
        self._olog_v = np.zeros(512)
        self._olog_i = 0

        # publish-time buffers
        self._frame_s = np.zeros(SPEC_N)
        self._spec_s = np.zeros(SPEC_N // 2 + 1, dtype=np.complex128)
        self._re_s, self._im_s = self._spec_s.real, self._spec_s.imag
        self._pw_s = np.zeros(SPEC_N // 2 + 1)
        self._tmp_s = np.zeros(SPEC_N // 2 + 1)
        self._p32 = np.zeros(SPEC_BANDS)
        self._db32 = np.zeros(SPEC_BANDS)
        self._val32 = np.zeros(SPEC_BANDS)
        self._psd32 = np.zeros(SPEC_BANDS)
        self._spec_sm = np.zeros(SPEC_BANDS)
        self._level_db = np.zeros(NB)
        self._level_n = np.zeros(NB)
        self._presence = np.zeros(NB)
        self._qvals = np.zeros(Q_ROWS)
        self._q = _Rolling()

        # counters and timing
        self._blocks = self._hops = self._gaps = self._publishes = 0
        self._hits = [0] * NB              # since the last publish
        self._hits_total = [0] * NB
        self._hop_ms = np.zeros(1024)
        self._block_ms = np.zeros(1024)
        self._tick_ms = np.zeros(256)
        self._hop_i = self._block_i = self._tick_i = 0
        self._acc_s = 0.0
        self._next_pub_s = -math.inf
        self._hops_seen = 0                           # the fade (tick): hops counted at the last tick
        self._hop_wall = -math.inf                    # host time the latest hop was first seen
        self._real_t_ms = 0.0                         # t_ms of the latest publish from audio (= the latest hop)
        self._faded = True                            # the final all-zero frame is out (or nothing to fade)
        self._fade_idle = 0.0                         # seconds since the latest hop, at the previous fade frame
        self._fades = 0

        self._clear_history()
        self._reset_state()

    # -- public ------------------------------------------------------------------------------------------------------
    def process(self, stream: str, n0: int, t0_host_s: float, pcm: np.ndarray, fs: int, flags: int) -> None:
        tp = time.perf_counter()
        pcm = np.asarray(pcm)
        if pcm.dtype != np.float32 and pcm.dtype != np.float64:
            pcm = pcm.astype(np.float32)
        frames = int(pcm.shape[0])
        ch = 1 if pcm.ndim == 1 else int(pcm.shape[1])
        if frames == 0 or ch == 0:
            return
        fs = int(fs)
        new_stream = stream != self._stream
        if new_stream:
            self._stream = stream
            self._clear_history()
        if fs != self._fs or ch != self._ch:
            self._rebuild(fs, ch)
        elif new_stream or flags & AUDIO_SEGMENT:
            self.reset()
        self._blocks += 1
        t0 = float(t0_host_s)
        if not math.isfinite(t0):
            t0 = self._t_last + 1.0 / fs if math.isfinite(self._t_last) else time.monotonic()
        if flags & AUDIO_GAP:
            self._gaps += 1
            self._timing(tp, 0)
            return
        if flags & AUDIO_RESUME:
            self._supp_hit_n = max(self._supp_hit_n, self._n + frames + self.hop)
            self._supp_onset_n = max(self._supp_onset_n, self._n + frames + FAST_N // 2 + self.hop)
        if self._t_first is None:
            self._t_first = t0

        if self._r128 is not None:
            try:
                self._r128.add_frames(np.ascontiguousarray(pcm).reshape(-1), frames)   # interleaved, no copy
            except Exception:                          # noqa: BLE001 - loudness must never stop the hit path
                self._r128 = None
        if frames <= CHUNK:
            hops = self._chunk(pcm, frames, ch, t0)
        else:
            hops = 0
            for c0 in range(0, frames, CHUNK):
                c1 = min(frames, c0 + CHUNK)
                hops += self._chunk(pcm[c0:c1], c1 - c0, ch, t0 + c0 / fs)
        self._t_last = t0 + (frames - 1) / fs
        self._timing(tp, hops)

    def tick(self, now_s: float) -> None:
        if self._hops != self._hops_seen:             # host time at which the latest hop was first seen
            self._hops_seen = self._hops
            self._hop_wall = now_s
        if self._T is None or now_s < self._next_pub_s:
            return
        if self._hops_since_pub == 0:
            self._fade(now_s)
            return
        self._next_pub_s = max(self._next_pub_s + self._period, now_s + 0.5 * self._period)
        tp = time.perf_counter()
        data = self._compose()
        self._latest = data
        self._real_t_ms, self._fade_idle = data["t_ms"], 0.0
        self._faded = False
        self._publishes += 1
        if self._publish is not None:
            self._publish({"type": MUSIC_FEATURES, "data": data})
        self._tick_ms[self._tick_i % self._tick_ms.size] = (time.perf_counter() - tp) * 1000.0
        self._tick_i += 1

    def _fade(self, now_s: float) -> None:
        """No hop since the last publish. From FADE_AFTER_S to FADE_FOR_S after the latest hop: publish at the
        usual rate with the gate at 0 (spectrum32 falls with its tau, everything else reads 0); then one all-zero
        frame, and nothing more until audio returns. The renderer never keeps a stopped source's last spectrum."""
        last = self._latest
        idle = now_s - self._hop_wall
        if self._faded or last is None or idle < FADE_AFTER_S:
            return
        self._next_pub_s = max(self._next_pub_s + self._period, now_s + 0.5 * self._period)
        dt = min(1.0, max(0.0, idle - self._fade_idle))      # decay by the time since the sound stopped
        self._fade_idle = idle
        final = idle > FADE_FOR_S
        sm, pr = self._spec_sm, self._presence
        if final:
            sm.fill(0.0)
            pr.fill(0.0)
            self._faded = True
        else:
            sm *= math.exp(-dt / SPEC_TAU_S)
            sm += 1e-12
            sm -= 1e-12
            pr *= math.exp(-dt / PRESENCE_TAU_S)
            pr += 1e-12
            pr -= 1e-12
        # the sound's time runs on from the latest hop (the last real frame's t_ms, first seen at _hop_wall) at the
        # host's rate: there is no audio to say otherwise
        t_ms = max(last["t_ms"] + 0.001, self._real_t_ms + (now_s - self._hop_wall) * 1000.0)
        bands = {name: {"level": 0.0, "hits": 0, "presence": round(float(pr[b]), 4)}
                 for b, name in enumerate(BAND_NAMES)}
        data: FeaturesData = {
            "t_ms": round(t_ms, 3), "stream": last.get("stream", ""), "bands": bands, "onset": 0.0,
            "lufs_m": LUFS_FLOOR, "lufs_s": LUFS_FLOOR, "peak_dbfs": DBFS_FLOOR, "rms_dbfs": DBFS_FLOOR,
            "brightness": 0.0, "noisiness": 0.0, "gate": 0.0, "energy": 0.0, "punch": 0.0, "drive": 0.0,
            "spectrum32": np.round(sm, 4).tolist()}
        self._latest = data
        self._fades += 1
        self._publishes += 1
        if self._publish is not None:
            self._publish({"type": MUSIC_FEATURES, "data": data})

    def latest(self) -> Optional[FeaturesData]:
        return self._latest

    def chroma12(self) -> Optional[np.ndarray]:
        return None

    def reset(self) -> None:
        self._reset_state()

    def stats(self) -> dict:
        def pct(ring: np.ndarray, n: int, q: float) -> Optional[float]:
            k = min(n, ring.size)
            return round(float(np.percentile(ring[:k], q)), 4) if k else None

        return {"blocks": self._blocks, "hops": self._hops, "gaps": self._gaps, "publishes": self._publishes,
                "fades": self._fades,
                "hits": dict(zip(BAND_NAMES, self._hits_total)),
                "hop_ms_p50": pct(self._hop_ms, self._hop_i, 50), "hop_ms_p95": pct(self._hop_ms, self._hop_i, 95),
                "block_ms_p95": pct(self._block_ms, self._block_i, 95),
                "tick_ms_p95": pct(self._tick_ms, self._tick_i, 95),
                "fs": self._fs, "channels": self._ch, "stream": self._stream, "kernel": KERNEL}

    # -- state -------------------------------------------------------------------------------------------------------
    def _rebuild(self, fs: int, ch: int) -> None:
        T = self._tables.get(fs)
        if T is None:
            T = self._tables[fs] = _Tables(fs)
        self._T, self._fs, self._ch = T, fs, ch
        try:
            self._r128 = pyebur128.R128State(ch, fs, _MODES)
        except Exception:                              # noqa: BLE001 - an unsupported rate: no loudness, still hits
            self._r128 = None
        self._lufs_s = LUFS_FLOOR
        self._lufs_s_t = -math.inf
        self._reset_state()

    def _reset_state(self) -> None:
        self._zi_band.fill(0.0)
        self._zi_fast.fill(0.0)
        self._slow.fill(0.0)
        self._armed[:] = [False] * NB
        self._ok_t[:] = [-math.inf] * NB
        self._ring.fill(0.0)
        self._rpos = self._hop_fill = self._hop_rpos = 0
        self._mf.fill(10.0 * math.log10(MEL_FLOOR))
        self._n = 0                                   # real samples since the reset
        self._hops_rs = 0                             # hops since the reset
        T = self._T
        self._supp_hit_n = T.reset_supp_n if T is not None else 0
        self._supp_onset_n = FAST_N + self.hop
        self._onset_acc = 0.0
        self._peak = 0.0
        self._ss = 0.0
        self._nacc = 0
        self._hops_since_pub = 0
        self._t_hop = math.nan
        self._t_last = getattr(self, "_t_last", math.nan)

    def _clear_history(self) -> None:
        self._q.clear()
        self._spec_sm.fill(0.0)
        self._presence.fill(0.0)
        self._punch_pk = 0.0
        self._punch_t = -math.inf
        self._last_low_t = -math.inf
        self._low_times: collections.deque[float] = collections.deque(maxlen=512)
        self._drive = None
        self._t_first = None
        self._t_pub = None
        self._lufs_s = LUFS_FLOOR
        self._lufs_s_t = -math.inf
        self._hits = [0] * NB

    def _timing(self, tp: float, hops: int) -> None:
        dt = time.perf_counter() - tp
        self._block_ms[self._block_i % self._block_ms.size] = dt * 1000.0
        self._block_i += 1
        self._acc_s += dt
        if hops:
            per = self._acc_s * 1000.0 / hops
            for _ in range(min(hops, self._hop_ms.size)):
                self._hop_ms[self._hop_i % self._hop_ms.size] = per
                self._hop_i += 1
            self._acc_s = 0.0

    def _view(self, n: int) -> _Views:
        v = self._views.get(n)
        if v is None:
            if len(self._views) > 32:
                self._views.clear()
            v = self._views[n] = _Views(self, n)
        return v

    # -- a piece of <= CHUNK frames ----------------------------------------------------------------------------------
    def _chunk(self, pcm: np.ndarray, n: int, ch: int, t0: float) -> int:
        v = self._view(n)
        work = v.work
        if ch == 1:
            np.copyto(work, pcm if pcm.ndim == 1 else pcm[:, 0])
        else:
            np.mean(pcm, axis=1, dtype=np.float64, out=work)
        mx, mn = float(work.max()), float(work.min())
        pk = mx if mx > -mn else -mn
        if pk > self._peak:
            self._peak = pk
        self._ss += float(np.dot(work, work))
        self._nacc += n
        if n <= HIT_BLOCK:
            self._hit_path(work, n, t0, self._n)
        else:
            fs = self._fs
            for h0 in range(0, n, HIT_BLOCK):
                h1 = min(n, h0 + HIT_BLOCK)
                self._hit_path(work[h0:h1], h1 - h0, t0 + h0 / fs, self._n + h0)
        return self._analysis(work, n, t0)

    def _hit_path(self, x: np.ndarray, n: int, t0: float, n_base: int) -> None:
        """One piece of <= HIT_BLOCK samples starting at real-sample index ``n_base`` (since the reset)."""
        T = self._T
        v = self._view(n)
        d = self._dpos
        np.add(x, self._dither[d:d + n], out=v.row0)           # mono + dither into all six rows
        np.copyto(v.rest, v.row0)                              # (a broadcasting out= would allocate)
        self._dpos = (d + n) % CHUNK
        sos, rows, zi = T.sos, v.rows, self._zi_rows
        for b in range(NB):
            _filt(sos[b], rows[b], zi[b])
        fast = v.fast
        np.multiply(v.band, v.band, out=fast)
        _filt(T.sos_fast, fast, self._zi_fast)

        slow, thr_on, thr_off = self._slow, self._thr_on, self._thr_off
        np.multiply(slow, self._k_on, out=thr_on)
        np.maximum(thr_on, self._floor_p, out=thr_on)
        np.multiply(slow, self._k_off, out=thr_off)
        np.maximum.reduce(fast, axis=1, out=self._fmax)
        np.greater(self._fmax, thr_on, out=self._any_on)
        armed_l = self._armed
        if all(armed_l):
            self._any_off.fill(False)
        else:
            np.minimum.reduce(fast, axis=1, out=self._fmin)
            np.less(self._fmin, thr_off, out=self._any_off)
        # the slow envelope: a one-pole at block rate on the block's mean (tau 150 ms >> a block); after a reset,
        # the running mean until tau has passed, so the baseline is right from the start
        np.dot(fast, v.ones, out=self._fsum)
        seen = n_base + n
        w = n / seen if seen < T.slow_n else 1.0 - math.exp(-n / T.slow_n)
        self._fsum *= w / n
        slow *= 1.0 - w
        slow += self._fsum

        any_on, any_off = self._any_on, self._any_off
        for b in range(NB):
            if armed_l[b]:
                if any_on[b]:
                    self._scan(b, True, v, n, t0, n_base)
            elif any_off[b]:
                self._scan(b, False, v, n, t0, n_base)

    def _scan(self, b: int, armed: bool, v: _Views, n: int, t0: float, n_base: int) -> None:
        """Walk band b's block sample by sample (vectorised per state): arm, fire, disarm."""
        row, rb = v.frows[b], v.rowbool
        on_thr, off_thr = float(self._thr_on[b]), float(self._thr_off[b])
        fs = self._fs
        i = 0
        while i < n:
            if armed:
                np.greater(row, on_thr, out=rb)
                k = i + int(rb[i:].argmax())
                if not rb[k]:
                    break
                armed = False
                t = t0 + k / fs
                if n_base + k >= self._supp_hit_n and t >= self._ok_t[b]:
                    self._ok_t[b] = t + REFRACTORY_S
                    self._fire(b, t, float(row[k:k + self._T.peak_n].max()))
            else:
                np.less(row, off_thr, out=rb)
                k = i + int(rb[i:].argmax())
                if not rb[k]:
                    break
                armed = True
            i = k + 1
        self._armed[b] = armed

    def _fire(self, b: int, t: float, pk: float) -> None:
        pk_db = 10.0 * math.log10(pk + 1e-30) + DB_SINE
        lo, hi = float(self._q.lo[b]), float(self._q.hi[b])
        strength = _clamp01((pk_db - lo) / max(1e-6, hi + STRENGTH_HEADROOM_DB - lo))
        self._hits[b] += 1
        self._hits_total[b] += 1
        if b < 2:                                      # sub, bass: a low hit
            held = self._punch_pk * math.exp(-(t - self._punch_t) / PUNCH_TAU_S) if self._punch_pk > 0.0 else 0.0
            if strength >= held:
                self._punch_pk, self._punch_t = strength, t
            if t - self._last_low_t >= LOW_MERGE_S:
                self._low_times.append(t)
                self._last_low_t = t
        if self._publish is not None:
            self._publish({"type": MUSIC_HIT, "data": {"t_ms": round(t * 1000.0, 3), "band": BAND_NAMES[b],
                                                       "strength": round(strength, 3), "stream": self._stream}})

    # -- the analysis ring and the fast lane ------------------------------------------------------------------------
    def _analysis(self, x: np.ndarray, n: int, t0: float) -> int:
        hop, fs, ring = self.hop, self._fs, self._ring
        hops = 0
        i = 0
        while i < n:
            take = min(n - i, hop - self._hop_fill)
            p = self._rpos
            end = p + take
            seg = x[i:i + take]
            if end <= RING_N:
                ring[p:end] = seg
                ring[p + RING_N:end + RING_N] = seg
            else:
                k = RING_N - p
                ring[p:RING_N] = seg[:k]
                ring[p + RING_N:] = seg[:k]
                ring[:take - k] = seg[k:]
                ring[RING_N:RING_N + take - k] = seg[k:]
            self._rpos = end % RING_N
            self._hop_fill += take
            i += take
            if self._hop_fill == hop:
                self._hop_fill = 0
                self._fast_lane(t0 + (i - 1) / fs, self._n + i)
                hops += 1
        self._n += n
        return hops

    def _fast_lane(self, t_end: float, n_end: int) -> None:
        T = self._T
        p = self._rpos
        np.multiply(self._ring[p + RING_N - FAST_N:p + RING_N], T.hann_fast, out=self._frame_f)
        np.fft.rfft(self._frame_f, out=self._spec_f)
        np.multiply(self._re_f, self._re_f, out=self._pw_f)
        np.multiply(self._im_f, self._im_f, out=self._tmp_f)
        self._pw_f += self._tmp_f
        mel = self._mel
        np.dot(T.mel_w, self._pw_f[:T.nb_fast], out=mel)
        mel += MEL_FLOOR
        np.log10(mel, out=mel)
        mel *= 10.0
        j = self._hops_rs
        lag = self._sf_lag
        slots = lag + 1
        np.subtract(mel, self._mf[(j - lag) % slots], out=self._dflux)
        np.maximum(self._dflux, 0.0, out=self._dflux)
        flux = float(self._dflux.sum())
        cur = self._mf[j % slots]                     # this frame, 3-band max-filtered, for frame j + lag
        np.maximum(mel[:-1], mel[1:], out=self._mx1)
        np.maximum(self._mx1[:-1], self._mx1[1:], out=cur[1:-1])
        cur[0], cur[-1] = self._mx1[0], self._mx1[-1]
        if n_end <= self._supp_onset_n:
            flux = 0.0
        if flux > self._onset_acc:
            self._onset_acc = flux
        k = self._olog_i % self._olog_t.size
        self._olog_t[k] = t_end - T.onset_delay_s
        self._olog_v[k] = flux
        self._olog_i += 1
        self._hops_rs += 1
        self._hops += 1
        self._hops_since_pub += 1
        self._t_hop = t_end
        self._hop_rpos = p

    # -- publish -------------------------------------------------------------------------------------------------------
    def _compose(self) -> FeaturesData:
        T = self._T
        t = self._t_hop
        dt = self._period if self._t_pub is None else min(1.0, max(0.0, t - self._t_pub))
        self._t_pub = t

        # loudness and the gate
        lufs_m = LUFS_FLOOR
        if self._r128 is not None:
            try:
                lufs_m = max(LUFS_FLOOR, float(pyebur128.get_loudness_momentary(self._r128)))
                if t - self._lufs_s_t >= LUFS_S_EVERY_S:
                    self._lufs_s_t = t
                    self._lufs_s = max(LUFS_FLOOR, float(pyebur128.get_loudness_shortterm(self._r128)))
            except Exception:                          # noqa: BLE001
                pass
        lufs_s = self._lufs_s
        ref_l = float(self.gate_ref_lufs)
        gate = _smoothstep(ref_l - GATE_SPAN_LU, ref_l, lufs_s)

        # the 4096-point spectrum: spectrum32, brightness, noisiness
        p = self._hop_rpos
        np.multiply(self._ring[p + RING_N - SPEC_N:p + RING_N], T.hann_spec, out=self._frame_s)
        np.fft.rfft(self._frame_s, out=self._spec_s)
        pw = self._pw_s
        np.multiply(self._re_s, self._re_s, out=pw)
        np.multiply(self._im_s, self._im_s, out=self._tmp_s)
        pw += self._tmp_s
        p32, db32, val32 = self._p32, self._db32, self._val32
        np.dot(T.spec_w, pw[:T.nb_spec], out=p32)
        np.add(p32, 1e-20, out=db32)
        np.log10(db32, out=db32)
        db32 *= 10.0
        loudest = float(db32.max())
        sel = pw[T.sel0:T.sel1]
        tot = float(sel.sum())
        if 10.0 * math.log10(tot * T.scale_s + 1e-30) < SILENCE_DB:
            brightness = noisiness = 0.0
        else:
            centroid = float(np.dot(T.freqs_sel, sel)) / tot
            brightness = _clamp01(math.log2(max(centroid, 1e-6) / BRIGHT_LO_HZ) / _BRIGHT_OCT)
            psd = self._psd32
            np.divide(p32, T.spec_nbins, out=psd)
            psd += 1e-30
            am = float(np.add.reduce(psd)) / SPEC_BANDS
            np.log(psd, out=psd)
            noisiness = _clamp01(math.exp(float(np.add.reduce(psd)) / SPEC_BANDS) / am)

        # rolling percentiles
        q = self._q
        level_db = self._level_db
        np.add(self._slow, 1e-30, out=level_db)
        np.log10(level_db, out=level_db)
        level_db *= 10.0
        level_db += DB_SINE
        qv = self._qvals
        qv[:NB] = level_db
        qv[Q_ONSET] = self._onset_acc
        qv[Q_LUFS] = lufs_m
        qv[Q_SPEC] = loudest
        q.push(t, qv)
        lo, hi = q.lo, q.hi

        ref = max(SPEC_REF_FLOOR_DB, float(q.hi60[Q_SPEC]) if q.ready else loudest)
        np.subtract(db32, ref - SPEC_WINDOW_DB, out=val32)
        val32 *= gate / SPEC_WINDOW_DB
        np.maximum(val32, 0.0, out=val32)
        np.minimum(val32, gate, out=val32)
        a = 1.0 - math.exp(-dt / SPEC_TAU_S)
        sm = self._spec_sm
        val32 -= sm
        val32 *= a
        sm += val32
        sm += 1e-12                                    # flush what would decay into denormals in long silence
        sm -= 1e-12

        ln = self._level_n
        np.subtract(level_db, lo[:NB], out=ln)
        ln /= (hi[:NB] - lo[:NB])
        np.maximum(ln, 0.0, out=ln)
        np.minimum(ln, 1.0, out=ln)
        ln *= gate
        pr = self._presence
        ap = 1.0 - math.exp(-dt / PRESENCE_TAU_S)
        pr *= 1.0 - ap
        pr += ln * ap
        pr += 1e-12
        pr -= 1e-12

        o_lo, o_hi, l_lo, l_hi = float(lo[Q_ONSET]), float(hi[Q_ONSET]), float(lo[Q_LUFS]), float(hi[Q_LUFS])
        onset = _clamp01((self._onset_acc - o_lo) / (o_hi - o_lo)) * gate
        energy = _clamp01((lufs_m - l_lo) / (l_hi - l_lo)) * gate

        # punch and drive (ungated, published x gate)
        held = self._punch_pk * math.exp(-max(0.0, t - self._punch_t) / PUNCH_TAU_S) if self._punch_pk > 0.0 else 0.0
        punch = _clamp01((held - PEAK_LO) / (PEAK_HI - PEAK_LO))
        lt = self._low_times
        while lt and lt[0] <= t - RATE_WINDOW_S:
            lt.popleft()
        span = t - self._t_first if self._t_first is not None else 0.0
        rate = len(lt) / min(RATE_WINDOW_S, span) if span >= RATE_MIN_SPAN_S else 0.0
        raw = 0.7 * _clamp01(rate / RATE_FULL) + 0.3 * punch
        ad = 1.0 - math.exp(-dt / DRIVE_TAU_S)
        self._drive = raw if self._drive is None else self._drive + (raw - self._drive) * ad

        peak_dbfs = max(DBFS_FLOOR, 20.0 * math.log10(self._peak + 1e-30))
        rms_dbfs = max(DBFS_FLOOR, 10.0 * math.log10(self._ss / self._nacc + 1e-30)) if self._nacc else DBFS_FLOOR

        bands = {}
        for b, name in enumerate(BAND_NAMES):
            bands[name] = {"level": round(float(ln[b]), 4), "hits": self._hits[b],
                           "presence": round(float(pr[b]), 4)}
            self._hits[b] = 0
        data: FeaturesData = {
            "t_ms": round(t * 1000.0, 3), "stream": self._stream or "", "bands": bands,
            "onset": round(onset, 4), "lufs_m": round(lufs_m, 2), "lufs_s": round(lufs_s, 2),
            "peak_dbfs": round(peak_dbfs, 2), "rms_dbfs": round(rms_dbfs, 2),
            "brightness": round(brightness, 4), "noisiness": round(noisiness, 4), "gate": round(gate, 4),
            "energy": round(energy, 4), "punch": round(punch * gate, 4), "drive": round(self._drive * gate, 4),
            "spectrum32": np.round(sm, 4).tolist()}
        self._onset_acc = 0.0
        self._peak = 0.0
        self._ss = 0.0
        self._nacc = 0
        self._hops_since_pub = 0
        return data
