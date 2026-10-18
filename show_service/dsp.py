"""dsp: the room's ears in real time. Audio blocks become ``music.features`` (50 Hz), ``music.hit`` (per hit) and
``music.rhythm`` (10 Hz, the medium lane) (docs/SHOW_ENGINE.md §4, §6.3, §11.1; README "Stage 4" S4.6).

One ``Features`` lives on the hot thread ("show-rx"). ``__main__`` passes ``Features.process`` as ``on_audio`` and
calls ``Features.tick`` from ``on_tick``. Nothing here locks, waits, logs or does I/O, and nothing is thread-safe
except ``latest()``, ``rhythm()`` and ``stats()``, which hand out snapshots swapped in by reference. Every buffer is allocated
when the sample rate or channel count is first seen; a steady stream allocates no sample buffers (only numpy view
headers, the 50 Hz and 10 Hz message dicts built in tick, a hit's dict, and a medium frame's few small scalars),
whatever its block sizes up to CHUNK.

  process(stream, n0, t0_host_s, pcm, fs, flags)     = contract.OnAudio
      pcm float32 (frames, channels) or (frames,), any block size: one packet, or the receiver's run of contiguous
      packets in one block (receiver.COALESCE_MAX_FRAMES). Blocks over CHUNK (4096) frames are handled in 4096-frame
      pieces. The outputs are the same (to float rounding) whether a span comes as 128-frame packets or as one block:
      the hit path and the fast lane batch their work, never their arithmetic. Channels are averaged to mono for
      everything except loudness: R128 measures the interleaved channels, as the standard does. Sample i of a block
      sounds at t0_host_s + i / fs (acoustic host seconds). pcm is only read (a gap block may be a read-only view).
      A new fs or channel count rebuilds every rate-dependent table: band filters, envelopes, mel bank, FFT bins
      and the R128 state. 48 kHz filters never run on 44.1 kHz audio.
  tick(now_s)          publishes music.features at publish_hz when a hop arrived since the last one; when the
                       source stops, a fade (below), then nothing. Then music.rhythm once per new medium frame.
  latest()             the last FeaturesData (or None)
  rhythm()             the last RhythmData (or None); any thread (swapped by reference). A consumer treats a frame
                       over 1 s old as stale: when the source stops, no more frames come.
  chroma12()           the latest medium frame's harmonic chroma (12,) C..B, max-normalised (a new array per frame;
                       don't write to it); None under the harmonic floor, before the first frame, or when the frame
                       was first seen by tick more than 0.5 s (tick's clock) before the latest tick
  clock_fn             None, or a callable returning the clock's ClockData (or None), read once per medium frame
                       on this thread (``clock.snapshot``): swing, syncopation and the beat-synchronous chord change
  reset()              filters, envelopes, onset frames, the analysis ring and the hit detectors, and the medium
                       lane's history. Kept: the R128 state (rebuilt on fs/ch change) and the normalisation history.
                       A new ``stream`` name clears the history too (percentiles, presence, punch/drive, the
                       spectrum's smoothing).
  gate_ref_lufs        the mode's expected loudness (the director sets it; §6.3), default -30
  gate_offsets         {stream: dB} added to gate_ref_lufs for that stream: its mic's calibration (audio_cal.json's
                       music_lufs - (-30); __main__ swaps the dict in by reference); a stream not in it: 0
  stats()              blocks, hops, gaps, publishes (music.features), fades, hits per band, hop_ms_p50/p95 (a hop's
                       blocks included, its medium frame not), block_ms_p95 (everything), tick_ms_p95 (a publish),
                       fs, channels, stream, kernel; medium_frames, medium_ms_p50/p95, rhythm_publishes, onsets;
                       batch (the constructor's switch: the batched hit path and multi-hop fast lane on), batch_fallbacks
                       (a batched pass that raised and was redone the per-piece / per-hop way, from the state before
                       it: 0 always, unless numpy or scipy misbehave)

Flags (contract):
  AUDIO_GAP      zero-fill for packets that never came. It advances time only (the next block brings its own
                 t0): no filter, ring, loudness, level, onset or hit sees it. Counted in ``gaps``.
  AUDIO_RESUME   the first real block after a gap: no hit fires in its first HIT_BLOCK samples (a packet; the
                 receiver sends it alone, and a longer block doesn't stretch it) or the hop after them, and no onset
                 until the splice has passed the 2048 window's centre (+ a hop): the splice is not a transient.
  AUDIO_SEGMENT  reset() (the receiver also sets it on the first block after the active stream switches).

Hit path, every block (§4: per packet, 2.7 ms at 128 frames), ungated:
  band_b  = Butterworth band-pass (contract.BAND_EDGES_HZ, order 2 = 2 sections, state kept) of mono + 1e-9 dither
  fast_b  = 2-pole critically damped low-pass (tau 2.5 ms) of band_b^2, per sample   the envelope follower
  slow_b  = 1-pole (tau 150 ms) at block rate on the block's mean fast_b             the adaptive threshold's
                                                                                     baseline, and the band's level
  Detection runs in pieces of at most HIT_BLOCK (128) samples whatever the block size, so hits don't depend on it.
  A block of several pieces runs in one pass (_hit_block): the filters over the whole block (their state carried),
  each piece's max/min/sum from one reduction, the slow envelope stepped per piece by the same kernel, and the
  hits fired after the scans in today's (piece, band) order; a short tail piece goes through the per-piece path.
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
  largest flux since the previous publish. A block that completes MULTI_HOPS (3) or more hops writes the ring once
  and runs its hops' windows (one strided view), FFTs (one rfft over the rows) and SuperFlux at once, the log-mel
  bank row by row (bit-identical to a hop at a time), then the per-hop stores and medium frames in order.

At publish (tick, 50 Hz), from the latest hop:
  spectrum32  4096-point FFT of a 2048-sample Hann (the newest samples, zero-padded: SPEC_WIN_N). Band k = the
              summed bin power whose centre is in SPEC_EDGES_HZ[k]..[k+1]; a band with no bin takes the bin nearest
              its centre. dB; ref = the rolling p95 over 60 s of the loudest band (at least -90 dB); value =
              clamp((dB - (ref - 60)) / 60) x spec_gate, then rise tau 35 ms, fall 300 ms (owner, 17 Oct: sound
              to glass under 0.5 s). spec_gate = smoothstep(bottom, bottom + 6, the window's own level in dBFS),
              bottom the gate's (ref - 15): it opens and closes with the sound, not lufs_s's 3 s.
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

Medium lane (§4, README S4.6): one frame per 100 ms of stream time, at the hop that crosses each multiple of fs/10
samples since the reset, inside process (show-rx), from the analysis ring. RhythmData's dict is built and published
by tick, so process allocates nothing but scalars for it. Window lengths and floors are dsp constants (DSP internals,
not behaviour thresholds).
  spectrum    N_M-point Hann FFT, N_M the power of two nearest 0.17 s (8192 at 44.1 and 48 kHz), power |X|^2 of
              the bins up to 8 kHz.
  HPSS-lite   Fitzgerald's median filtering, made causal. H = the median, per bin, of this frame and the 8 before it
              (0.9 s; past frames only). P = the median over 17 bins of this frame (edges: the nearest bin repeated).
              mask M = H^2 / (H^2 + P^2 + eps) on magnitudes, i.e. Hp / (Hp + Pp + eps) on powers (the median
              commutes with squaring); harmonic = |X| M, percussive = |X| (1 - M). The 9-frame median is exact and
              allocation-free: the history is 3 groups of 3 frames, each group kept sorted (only the group the new
              frame lands in is re-sorted), and med9 = med3(max of the groups' lows, med3 of their middles, min of
              their highs).
  harmonic_db 10 log10(mean square of the harmonic part, 30 Hz-8 kHz) by Parseval (2 sum H^2 / (N_M sum w^2)), dBFS
              (a full-scale sine reads -3.01), floored -120. Under HARM_FLOOR_DB (-60) the frame is "under the
              harmonic floor": chroma12 reads all 0, chroma12() None, and its chord change counts 0.
  chroma12    the harmonic magnitudes |X| M from 55 Hz to 5 kHz, each bin to its nearest semitone (A4 = 440 Hz:
              round(69 + 12 log2(f / 440)) mod 12, C = 0), summed per pitch class, max-normalised.
              chroma12_avg = the mean of the last 10 frames' chroma12 (1 s; fewer after a reset).
  percussive_ratio  sum P^2 / sum (H^2 + P^2) over the last 10 frames (1 s), bins 30 Hz-8 kHz; 0 in silence.
  harm_change the mean over the last 4 s of d = 1 - cos(c, c_before) on the unnormalised harmonic chroma; d = 0 when
              either frame is under the harmonic floor. Without a clock lock, c_before is the frame 0.3 s (3 frames)
              earlier and the mean is over the last 40 frames. While the clock's gate is beat or dmx it is
              beat-synchronous: each beat's chroma is the sum of its frames' (beat(t) as contract.ClockData
              defines it), d compares a beat with the beat before it, and the mean is over the beats that ended in
              the last 4 s (at least 2, else the time-based value). A clock epoch or a lost lock restarts it.
  onsets      the fast lane's per-hop SuperFlux, peak-picked once per frame for the hops up to 3 hops ago: a hop is
              an onset when its flux is the largest within +-3 hops, above 1.5 x the median flux of the last 1 s
              of hops (one median per frame) + ONSET_FLOOR, and over 3 hops after the previous onset. Its time is
              the hop's onset time (the fast lane's: the hop's end - FAST_N / 4 / fs) and its strength the flux.
              onset_rate = onsets in the last 4 s / 4 s (/ the time since the first frame, at least 1 s, earlier).
              density = clamp((onset_rate - lo) / (hi - lo)) x gate, lo/hi the rolling p05/p95 (a row of the same
              percentiles as onset and energy, pushed at 50 Hz).
  pulse_clarity  the autocorrelation (FFT, zero-padded past the largest lag: no wrap; mean removed) of the last 6 s
              of the flux, summed over pairs of hops (94 Hz at 48 kHz: half the FFT, the same lags to 11 ms); max
              over lags 0.25-1.5 s / lag 0, clamped 0..1. 0 until 3 s of hops, and 0 when that envelope's RMS is
              under PULSE_FLOOR (a steady tone's flux is ~0.2, noise's ~8): no pulse in a held chord's beating.
              No clock. Computed on even frames (5 Hz), held on odd ones.
  swing       with the clock's gate beat or dmx: the onsets of the last 8 beats whose phase frac(beat(t)) is in
              [0.4, 0.8]; with at least 4, swing = median phase / (1 - median phase), clamped 1..3; else None.
              Swing and syncopation are computed on odd frames (5 Hz) and held on even ones; the first frame with
              a lock computes them at once, and they are None from the first frame without one. (A 16th-note hat
              at phase 0.75 counts too, so straight 16ths read above 1: the formula as specified.)
  syncopation with the gate beat or dmx: the onsets of the last 4 bars on a 16th grid (beats_per_bar x 4 steps
              from bar_anchor_beat, each onset to its nearest step), summed per step (the 4 bars folded: the
              mean pattern, up to scale). Levels: the downbeat 0, other beats 1, 8ths 2, 16ths 3. syncopation =
              the share of the pattern's strength on levels 2-3 whose next stronger step (the next step, going
              round the bar, of a lower level) carries less than half of it; 0 with no onsets; else None.
  low_db      10 log10(slow_sub + slow_bass) + 3.01 (the hit path's envelopes, dB re a full-scale sine), floored
              -120: the envelopes as they stood at the end of the piece holding the frame's hop (not at the end of
              the block, which read up to 13 dB off for 4096-frame blocks).
  mfcc13      the orthonormal DCT-II of the fast lane's 40 log-mel bands (dB), c1..c13, of their mean over the
              hops of the last 10 frames (1 s): the DCT is linear, so this is the mean MFCC.
  gate        the loudness gate of the latest music.features (0 during the fade).
  The clock is read once per frame through clock_fn (the snapshot, by reference); a body that raises, has no
  tempo or isn't at gate beat|dmx means no grid, never an error.
  Budget: a medium frame <= 0.5 ms p95 unloaded (<= 1 ms under the render), timed into medium_ms_p50/p95; its time
  is left out of hop_ms (the fast lane's cost), so the hop's p95 does not move. The fast lane gains only a few
  stores per hop (the flux and onset-time rings, the log-mel sum).

Budget (README): a hop, its blocks included, <= 0.45 ms p95 under render load. The band filters run through scipy's
in-place ``_sosfilt`` kernel (about 3 us a call; the public ``sosfilt`` costs about 60 us in argument checks). It
is checked against ``sosfilt`` at import, and the public function is the fallback. A call's cost is mostly fixed
(numpy and kernel calls), so a coalesced block costs far less per packet than packets one by one: a 32-packet
block about a third (measured, scratch bench), with the same numbers out. ``Features(batch=False)`` turns both batched
passes off (a rollback switch: packet-sized pieces and one hop at a time, the same numbers at today's cost).
"""

from __future__ import annotations

import collections
import math
import time
from typing import Callable, Optional

import numpy as np
import pyebur128
from numpy.lib.stride_tricks import sliding_window_view
from scipy.ndimage import median_filter
from scipy.signal import butter, sosfilt

from .contract import (AUDIO_GAP, AUDIO_RESUME, AUDIO_SEGMENT, BAND_EDGES_HZ, BAND_NAMES, MUSIC_FEATURES, MUSIC_HIT,
                       MUSIC_RHYTHM, RHYTHM_HZ, SPEC_BANDS, SPEC_EDGES_HZ, SPEC_HI_HZ, SPEC_LO_HZ, SPEC_REF_S,
                       SPEC_RISE_TAU_S, SPEC_TAU_S, SPEC_WINDOW_DB, ClockData, FeaturesData, Publish, RhythmData)

# -- constants --------------------------------------------------------------------------------------------------------
HOP = 256
CHUNK = 4096                    # the largest piece one pass handles (32 packets: the receiver's coalesced run); bigger
                                # blocks are split
HIT_BLOCK = 128                 # hit detection runs in pieces of at most this, so hits don't depend on block size
HOP_MAX = 1024                  # the largest hop Features takes
DITHER_N = 1024                 # the dither's period (samples): tiled, so any block reads it as one slice
MULTI_HOPS = 3                  # a block that completes this many hops runs the fast lane over them at once ...
MULTI_MAX = 16                  # ... in groups of at most this many (CHUNK / HOP at 256)
FAST_N = 2048                   # fast-lane FFT (log-mel, SuperFlux)
SPEC_N = 4096                   # spectrum32 / brightness / noisiness FFT (the lowest spectrum band is 7 Hz wide)...
SPEC_WIN_N = 2048               # ...over a Hann of the newest 2048 samples, zero-padded (owner, 17 Oct: sound to glass
                                # under 0.5 s; the window's centre is 21 ms back, not 43; bins stay 4096's, so the
                                # bass bands interpolate instead of stair-stepping; the bass blurs over ~+-47 Hz)
PUBLISH_HZ = 50.0               # music.features (owner, 17 Oct: the publish phase costs up to a period; 25 Hz before)
RING_N = 16384                  # analysis ring: >= the longest read-back (SPEC_N, the medium lane's n_raw) + CHUNK + a hop
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
FADE_FOR_S = 1.5                 # ... at 50 Hz with the gate at 0 until this long, then one all-zero frame

SPEC_REF_FLOOR_DB = -90.0
SILENCE_DB = -90.0
LUFS_FLOOR, DBFS_FLOOR = -70.0, -120.0
LUFS_S_EVERY_S = 0.2
GATE_SPAN_LU = 15.0
#: spectrum32's own gate (owner, 17 Oct: sound to glass under 0.5 s): the level of its own 43 ms window (the Hann's
#: weighted mean square, dBFS like the floor) from the gate's bottom (ref - GATE_SPAN_LU, i.e. floor + FLOOR_ABOVE_LU
#: once the floor is known) to this many dB over it. The gate's lufs_s (3 s) held the bars up 1.3-3.3 s after the music
#: stopped and opened them ~1 s late (screen-machine-73's replay of production features); lufs_m (400 ms) still ~0.3 s.
#: Music well over the floor reads 1 (no pumping on kicks); the room's noise stays shut, as with the gate.
SPEC_GATE_FAST_LU = 6.0
# The gate's reference follows the input's own noise floor down, never up: ref = min(gate_ref_lufs, floor +
# FLOOR_ABOVE_LU + GATE_SPAN_LU). floor = the FLOOR_PCT percentile of the input's 100 ms RMS levels (dBFS; gap fill
# skipped) over the last FLOOR_WINDOW_S, once FLOOR_MIN_S of them are in; a new stream starts over. Short bins find
# the room's own floor between notes even under continuous music. A quiet or distant mic still opens the gate on music
# over its own noise (degrade, never refuse); noise within FLOOR_ABOVE_LU of the floor stays shut; an input at the
# mode's loudness is gated exactly as before. A bin closes at exactly round(FLOOR_BIN_S x fs) samples (4800 at 48 kHz)
# whatever the block size, split inside a block (it closed at the first block boundary past it before: 4864 samples
# with 128-frame packets, up to 8192 with 4096-frame blocks).
FLOOR_BIN_S = 0.1
FLOOR_WINDOW_S = 600.0
FLOOR_MIN_S = 30.0
FLOOR_PCT = 2.0
FLOOR_ABOVE_LU = 6.0
BRIGHT_LO_HZ, BRIGHT_HI_HZ = 50.0, 16000.0
_BRIGHT_OCT = math.log2(BRIGHT_HI_HZ / BRIGHT_LO_HZ)

# rolling percentiles: rows 0-5 band level dB, 6 onset flux, 7 lufs_m, 8 the loudest spectrum32 band dB,
# 9 the medium lane's onset_rate (density)
Q_ONSET, Q_LUFS, Q_SPEC, Q_RATE = NB, NB + 1, NB + 2, NB + 3
Q_ROWS = NB + 4
Q_SHORT_S, Q_SHORT_HZ = SPEC_REF_S, 5.0         # 60 s: also the spectrum ref's window (§11.1)
Q_LONG_S, Q_LONG_HZ = 600.0, 1.0
Q_UPDATE_S = 1.0
Q_MIN_COUNT = 5
#                       bands x6            onset  lufs   spec   rate
Q_LO0 = np.array([-70.0] * NB + [0.0, -50.0, -40.0, 0.0])
Q_HI0 = np.array([-20.0] * NB + [50.0, -20.0, -40.0, 8.0])
Q_HI_FLOOR = np.array([HIT_FLOOR_DB] * NB + [10.0, -60.0, SPEC_REF_FLOOR_DB, 2.0])
Q_MIN_SPAN = np.array([12.0] * NB + [10.0, 10.0, 0.0, 2.0])

# -- the medium lane (§4; README S4.6). Window lengths and floors: DSP internals, like the rest of dsp ----------------
MED_HZ = int(RHYTHM_HZ)         # frames per second of stream time
MED_WINDOW_S = 0.17             # N_M = the power of two nearest this (8192 at 44.1 and 48 kHz)
MED_HI_HZ = 8000.0              # the medium spectrum's top
MED_DEC_MIN_FS = 40000          # from here up the frame is halved in rate before its FFT (Nyquist >= 10 kHz after)
HPSS_F = 17                     # P: the median over this many bins of the frame (H: 9 frames, 3 groups of 3)
HPSS_EPS = 1e-30
HIST_FLOOR = 1e-30              # added to |X|^2 in the float32 history: a normal float32, far under any sound
PERC_LO_HZ = 30.0               # percussive_ratio and harmonic_db: 30 Hz-8 kHz
CHROMA_LO_HZ, CHROMA_HI_HZ = 55.0, 5000.0
A4_HZ = 440.0
HARM_FLOOR_DB = -60.0           # the harmonic part under this (dBFS): no chroma, no chord change
AVG_FRAMES = 10                 # 1 s of frames: chroma12_avg, percussive_ratio, mfcc13
CHANGE_LAG = 3                  # harm_change without a lock: against the frame 0.3 s before ...
CHANGE_FRAMES = 40              # ... averaged over 4 s
CHANGE_S = 4.0                  # with a lock: the beats that ended in the last 4 s
CHANGE_MIN_BEATS = 2
CHANGE_BEATS_N = 64
CHROMA_STALE_S = 0.5
ONSET_HALF = 3                  # local max over +-3 hops
ONSET_K = 1.5                   # above 1.5 x the median flux of the last 1 s ...
ONSET_MED_S = 1.0
ONSET_FLOOR = 20.0              # ... + this (SuperFlux units: dB summed over the 40 mel bands)
ON_N = 512                      # onsets kept (double-written ring): > 8 beats / 4 bars of them at any sane rate
RATE_S = 4.0
PULSE_S = 6.0
PULSE_MIN_S = 3.0
PULSE_LAG_S = (0.25, 1.5)
PULSE_HOPS = 2                  # the autocorrelation runs on the flux summed over 2 hops (94 Hz at 48 kHz)
PULSE_FLOOR = 2.0               # that envelope's RMS (mean removed) under which there is no pulse (steady tones ~0.2)
LOCK_GATES = ("beat", "dmx")    # contract.ClockData.gate values at which the grid is trusted
SWING_BEATS = 8
SWING_PHASE = (0.4, 0.8)
SWING_MIN_N = 4
SWING_RANGE = (1.0, 3.0)
SYNC_BARS = 4
SYNC_HALF = 0.5
MFCC_N = 13


# The ring must hold the longest read-back behind a hop that a whole CHUNK has been written past (the multi-hop fast
# lane writes the block first): the medium lane's n_raw (_Tables: n_m <= the power of two under (RING_N - 2) / dec,
# so at most 8194 at any rate), SPEC_N or FAST_N, plus CHUNK and a hop.
_N_RAW_MAX = max(1 << int(math.log2(RING_N - 2)), 2 * (1 << int(math.log2((RING_N - 2) // 2))) + 2)
assert RING_N >= max(_N_RAW_MAX, SPEC_N, FAST_N) + CHUNK + HOP_MAX, "RING_N too small for CHUNK"


def _dct_c1_c13() -> np.ndarray:
    """(13, 40): rows c1..c13 of the orthonormal DCT-II."""
    n = np.arange(MEL_BANDS)
    k = np.arange(1, MFCC_N + 1)[:, None]
    return np.sqrt(2.0 / MEL_BANDS) * np.cos(np.pi * k * (2 * n + 1) / (2 * MEL_BANDS))


_DCT = np.ascontiguousarray(_dct_c1_c13())


def _pow2_nearest(x: float) -> int:
    lo = 1 << max(0, int(math.floor(math.log2(max(1.0, x)))))
    return lo if x - lo <= 2 * lo - x else 2 * lo


def _pow2_at_least(x: float) -> int:
    return 1 << max(0, int(math.ceil(math.log2(max(1.0, x)))))


def _sync_tables(steps: int) -> tuple[list[int], list[int]]:
    """For a bar of ``steps`` 16ths: the steps on levels 2-3 (8ths, 16ths) and each one's next stronger step (the
    next step, going round the bar, whose level is lower)."""
    def level(s: int) -> int:
        return 0 if s == 0 else 1 if s % 4 == 0 else 2 if s % 2 == 0 else 3

    weak, nxt = [], []
    for s in range(steps):
        lv = level(s)
        if lv < 2:
            continue
        k = 1
        while level((s + k) % steps) >= lv:
            k += 1
        weak.append(s)
        nxt.append((s + k) % steps)
    return weak, nxt

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


def _public_median(x: np.ndarray, size: int, out: np.ndarray) -> None:
    """The fallback, same contract as the kernel: out <- the running median of x over ``size`` (odd) samples, the
    edges repeating the nearest sample. x and out must not share memory."""
    median_filter(x, size=size, output=out, mode="nearest")


def _pick_median():
    """scipy's 1-D rank filter without median_filter's argument handling (about 40 us a call under load: as much
    as the filter), checked against median_filter at import; the public function is the fallback."""
    try:
        from scipy.ndimage import _ni_support, _rank_filter_1d
        mode = _ni_support._extend_mode_to_code("nearest", is_filter=True)
        zero = np.float64(0.0)

        def kernel(x: np.ndarray, size: int, out: np.ndarray) -> None:
            _rank_filter_1d.rank_filter(x, size // 2, size, out, mode, zero, 0)

        rng = np.random.default_rng(2)
        for n in (17, 40, 1366):
            x = rng.random(n) ** 4
            want, got = np.empty(n), np.empty(n)
            _public_median(x, HPSS_F, want)
            kernel(x, HPSS_F, got)
            if not np.array_equal(want, got):
                raise ValueError("mismatch")
        return kernel, "scipy-private"
    except Exception:                                   # noqa: BLE001 - any trouble: the public path
        return _public_median, "public"


_median, MEDIAN_KERNEL = _pick_median()


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
        # the slow envelope's steady-state step per HIT_BLOCK piece, slow <- slow (1 - w) + sum(fast) w / HIT_BLOCK,
        # as a one-pole section for the kernel (a block's pieces in one call): y = b0 x + a y_prev, rounded exactly as
        # the per-piece numpy steps are (two products, one sum)
        self.w_slow = w = 1.0 - math.exp(-HIT_BLOCK / self.slow_n)
        self.sos_slow = np.array([[w / HIT_BLOCK, 0.0, 0.0, 1.0, -(1.0 - w), 0.0]])
        self.reset_supp_n = int(round(RESET_HIT_SUPPRESS_S * fs))
        self.floor_bin_n = max(1, int(round(FLOOR_BIN_S * fs)))   # the noise floor's bin: exactly 100 ms of samples

        self.hann_fast = _hann(FAST_N)
        freqs_f = np.fft.rfftfreq(FAST_N, 1.0 / fs)
        scale_f = (2.0 / self.hann_fast.sum()) ** 2       # a full-scale sine's peak bin reads 1.0
        mel_hi = min(MEL_HI_HZ, 0.95 * nyq)
        self.nb_fast = int(min(freqs_f.size, np.searchsorted(freqs_f, mel_hi) + 2))   # bins the mel bank uses
        self.mel_w = np.ascontiguousarray(_mel_bank(freqs_f[:self.nb_fast], MEL_BANDS, MEL_LO_HZ, mel_hi) * scale_f)

        self.hann_spec = np.zeros(SPEC_N)
        self.hann_spec[SPEC_N - SPEC_WIN_N:] = _hann(SPEC_WIN_N)
        self.hann_spec_ss = float(np.dot(self.hann_spec, self.hann_spec))   # its weighted mean square's divisor
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

        # the medium lane: at fs >= MED_DEC_MIN_FS the window is halved in rate first (same 0.17 s, same bins)
        self.m_dec = dec = 2 if fs >= MED_DEC_MIN_FS else 1
        fs_m = fs / dec
        self.n_m = n_m = min(1 << int(math.log2((RING_N - 2) // dec)), _pow2_nearest(MED_WINDOW_S * fs_m))
        self.n_raw = n_m * dec + (2 if dec == 2 else 0)          # ring samples one frame reads
        self.hann_m = _hann(n_m)
        freqs_m = np.fft.rfftfreq(n_m, 1.0 / fs_m)
        self.k_m = int(min(freqs_m.size, np.searchsorted(freqs_m, MED_HI_HZ, side="right")))   # bins 0..k_m-1
        self.k_p0 = int(min(self.k_m, np.searchsorted(freqs_m, PERC_LO_HZ)))
        self.k_c0 = int(min(self.k_m, np.searchsorted(freqs_m, CHROMA_LO_HZ)))
        self.k_c1 = int(max(self.k_c0, min(self.k_m, np.searchsorted(freqs_m, CHROMA_HI_HZ, side="right"))))
        pc = np.rint(69.0 + 12.0 * np.log2(freqs_m[self.k_c0:self.k_c1] / A4_HZ)).astype(np.intp) % 12
        w = np.zeros((12, self.k_c1 - self.k_c0))
        w[pc, np.arange(pc.size)] = 1.0
        self.chroma_w = np.ascontiguousarray(w)
        self.ms_m = 2.0 / (n_m * float(np.dot(self.hann_m, self.hann_m)))   # one-sided sum |X|^2 -> mean square
        # the decimator [1 3 3 1] has |H(f)| = 8 cos^3(pi f / fs): its gain and droop are divided back out of |X|^2
        self.m_comp = (1.0 / (64.0 * np.cos(np.pi * freqs_m[:self.k_m] / fs) ** 6)) if dec == 2 else None


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


class _Pieces:
    """Per number of HIT_BLOCK pieces P (a block of P x HIT_BLOCK): views onto flat preallocated buffers. f2 is the
    block's fast envelope as (6P, HIT_BLOCK) rows (band-major: row b P + p); the reductions come out (6, P); sb is
    (6, P + 1): the slow envelope at each piece's start, column P after the block."""
    __slots__ = ("f2", "pmax", "pmin", "psum", "pmax2", "pmin2", "psum2", "sb", "sb0", "sb1", "st", "stc", "kon",
                 "ton", "toff", "aon", "aoff")

    def __init__(self, f: "Features", P: int) -> None:
        k = NB * P
        self.f2 = f._fast_buf[:k * HIT_BLOCK].reshape(k, HIT_BLOCK)
        self.pmax, self.pmin, self.psum = f._pmax[:k], f._pmin[:k], f._psum[:k]
        self.pmax2, self.pmin2, self.psum2 = (a.reshape(NB, P) for a in (self.pmax, self.pmin, self.psum))
        self.sb = f._sb[:NB * (P + 1)].reshape(NB, P + 1)
        self.sb0, self.sb1, self.st = self.sb[:, 0], self.sb[:, 1:], self.sb[:, :P]
        # every operand below (6, P) and C-contiguous: numpy 2 buffers (allocates) for broadcast or strided 2-D
        self.stc = f._stc[:k].reshape(NB, P)
        self.kon = np.ascontiguousarray(np.repeat(f._k_on[:, None], P, axis=1))
        self.ton, self.toff = f._ton[:k].reshape(NB, P), f._toff[:k].reshape(NB, P)
        self.aon, self.aoff = f._aon[:k].reshape(NB, P), f._aoff[:k].reshape(NB, P)


class _Hops:
    """Per group of g hops (the multi-hop fast lane) at one rate: views onto Features' (MULTI_MAX, .) buffers,
    built once (rebuilt with the rate: the mel bank's bins). Every 2-D operand is C-contiguous and the same shape
    as its partner, and the max filter runs on flattened rows: numpy 2 allocates a buffer for broadcast or strided
    2-D operands."""
    __slots__ = ("F", "hann", "S", "RE", "IM", "PW", "TP", "pw_rows", "MEL", "mel_rows", "MF", "mf_in", "cur",
                 "cur_rows", "melf0", "melf1", "mx", "mx0", "mx1", "cur_mid", "cur0", "cur1", "mx_first", "mx_last",
                 "DF", "FL")

    def __init__(self, f: "Features", g: int, nb: int) -> None:
        lag, mb = f._sf_lag, MEL_BANDS
        self.F, self.hann = f._mF[:g], f._hann_rows[:g]
        self.S, self.RE, self.IM = f._mS[:g], f._mRE[:g], f._mIM[:g]
        self.PW, self.TP = f._mPW[:g], f._mTP[:g]
        self.pw_rows = tuple(self.PW[k, :nb] for k in range(g))
        self.MEL = f._mMEL[:g]
        self.mel_rows = tuple(self.MEL[k] for k in range(g))
        mf = f._mMF[:g + lag]                          # rows: frames j0 - lag .. j0 + g - 1, max-filtered
        self.MF = mf[:g]                               # hop j's reference: frame j - lag
        self.mf_in = tuple(mf[q] for q in range(lag))  # the lag frames before this group (from _mf)
        self.cur = cur = mf[lag:]                      # this group's frames
        self.cur_rows = tuple(cur[k] for k in range(g))
        melf, curf, mx = self.MEL.reshape(-1), cur.reshape(-1), f._mMX[:g * mb - 1]
        self.melf0, self.melf1 = melf[:-1], melf[1:]
        self.mx, self.mx0, self.mx1 = mx, mx[:-1], mx[1:]
        self.cur_mid = curf[1:g * mb - 1]
        self.cur0, self.cur1 = cur[:, 0], cur[:, -1]
        self.mx_first, self.mx_last = mx[0::mb], mx[mb - 2::mb]
        self.DF, self.FL = f._mDF[:g], f._mFL[:g]


def _clamp01(x: float) -> float:
    return 0.0 if x <= 0.0 else 1.0 if x >= 1.0 else x


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = _clamp01((x - e0) / (e1 - e0))
    return t * t * (3.0 - 2.0 * t)


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    """The cosine of two non-negative vectors, 0..1; 1 (no change) when either is all zero."""
    na, nb = float(np.dot(a, a)), float(np.dot(b, b))
    if not (na > 0.0 and nb > 0.0):
        return 1.0
    return min(1.0, max(0.0, float(np.dot(a, b)) / math.sqrt(na * nb)))


class Features:
    """Audio blocks in, music.features (50 Hz) and music.hit out. Hot thread only (see the module docstring)."""

    gate_ref_lufs: float = -30.0
    clock_fn: Optional[Callable[[], Optional[ClockData]]] = None      # set by __main__: clock.snapshot

    def __init__(self, *, publish: Optional[Publish] = None, hop: int = HOP, publish_hz: float = PUBLISH_HZ,
                 batch: bool = True) -> None:
        """``batch`` (default on): a block of several packets runs the batched hit path and, completing MULTI_HOPS
        or more hops, the multi-hop fast lane; off, it runs packet-sized pieces and hop by hop as before (the same
        numbers either way, to float rounding: the off switch is a rollback, not a mode)."""
        if not 32 <= hop <= HOP_MAX:
            raise ValueError(f"hop must be 32..{HOP_MAX}")
        self._publish = publish
        self._batch_hits = self._batch_hops = bool(batch)
        self.gate_offsets: dict[str, float] = {}       # {stream: dB} over gate_ref_lufs (the mic's calibration)
        self.hop = int(hop)
        self._period = 1.0 / float(publish_hz)
        self._tables: dict[int, _Tables] = {}
        self._T: Optional[_Tables] = None
        self._fs = 0
        self._ch = 0
        self._stream: Optional[str] = None
        self._floor_vals: collections.deque = collections.deque(maxlen=int(FLOOR_WINDOW_S / FLOOR_BIN_S))  # dBFS
        self._floor_lufs: Optional[float] = None
        self._floor_t = -math.inf
        self._fl_ss = 0.0                          # the 100 ms bin being filled: sum of squares, samples
        self._fl_n = 0
        self._gate_ref_eff = float(self.gate_ref_lufs)
        self._r128 = None
        self._latest: Optional[FeaturesData] = None

        # block buffers (hit path)
        self._work = np.zeros(CHUNK)
        self._band_buf = np.zeros(NB * CHUNK)
        self._fast_buf = np.zeros(NB * CHUNK)
        self._ones = np.ones(CHUNK)
        self._rowbool = np.zeros(CHUNK, dtype=bool)
        self._views: dict[int, _Views] = {}
        pm = CHUNK // HIT_BLOCK                       # the batched hit path (_hit_block): per piece, per band
        self._pieces: dict[int, _Pieces] = {}
        self._pmax, self._pmin, self._psum = np.zeros(NB * pm), np.zeros(NB * pm), np.zeros(NB * pm)
        self._sb = np.zeros(NB * (pm + 1))
        self._ton, self._toff, self._stc = np.zeros(NB * pm), np.zeros(NB * pm), np.zeros(NB * pm)
        self._aon, self._aoff = np.zeros(NB * pm, dtype=bool), np.zeros(NB * pm, dtype=bool)
        self._ones_h = np.ones(HIT_BLOCK)
        self._rb_h = self._rowbool[:HIT_BLOCK]
        self._zi_slow = np.zeros((NB, 1, 2))
        self._zs0, self._zs1 = self._zi_slow[:, 0, 0], self._zi_slow[:, 0, 1]
        self._pend: list = []                         # a block's hits, fired in today's (piece, band) order
        self._batch_fallbacks = 0                     # batched passes that raised: redone the per-piece/per-hop way
        self._sat_p = 0                               # _medium's slow: the current chunk's pieces (0: self._slow)
        self._sat_n0 = 0
        self._sat_sb: Optional[np.ndarray] = None
        # the dither: a period of DITHER_N (today's first 1024 values), tiled so a block of up to CHUNK reads one slice
        base = np.random.default_rng(0x5EED).standard_normal(2 * DITHER_N)[:DITHER_N] * DITHER
        self._dither = np.tile(base, CHUNK // DITHER_N + 2)
        self._dpos = 0
        self._zi_band = np.zeros((NB, 1, 2, 2))
        self._zi_rows = tuple(self._zi_band[b] for b in range(NB))
        self._zi_fast = np.zeros((NB, 1, 2))
        self._slow = np.zeros(NB)                     # mean square; also the band level
        self._bk_zi_band, self._bk_zi_fast, self._bk_slow = (np.zeros_like(a) for a in
                                                             (self._zi_band, self._zi_fast, self._slow))
        self._bk_armed: list = [False] * NB           # the hit path's state before a batched pass (its fallback)
        self._bk_ok: list = [0.0] * NB
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
        # the multi-hop fast lane (_hops_multi): up to MULTI_MAX hops' windows, spectra and log-mel at once
        self._swv = sliding_window_view(self._ring, FAST_N)   # every window of the double ring, as one strided view
        hm = MULTI_MAX
        self._mF = np.zeros((hm, FAST_N))
        self._mS = np.zeros((hm, FAST_N // 2 + 1), dtype=np.complex128)
        self._mRE, self._mIM = self._mS.real, self._mS.imag
        self._mPW = np.zeros((hm, FAST_N // 2 + 1))
        self._mTP = np.zeros((hm, FAST_N // 2 + 1))
        self._mMEL = np.zeros((hm, MEL_BANDS))
        self._mMF = np.zeros((hm + self._sf_lag, MEL_BANDS))
        self._mMX = np.zeros(hm * MEL_BANDS)
        self._mDF = np.zeros((hm, MEL_BANDS))
        self._mFL = np.zeros(hm)
        self._hann_rows = np.tile(_hann(FAST_N), (hm, 1))
        self._hops_v: dict[int, _Hops] = {}
        self._mf_rows = tuple(self._mf[q] for q in range(self._sf_lag + 1))
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
        self._a32 = np.zeros(SPEC_BANDS)              # per band: the rise or the fall coefficient this publish
        self._up32 = np.zeros(SPEC_BANDS, dtype=bool)
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

        # the medium lane: rate-dependent buffers come with _rebuild (_alloc_medium); these don't depend on the rate
        self._mk: Optional[tuple] = None              # what the medium buffers were sized for
        self._mel_acc = np.zeros(MEL_BANDS)           # log-mel summed over the hops since the last frame
        self._mel_ring = np.zeros((AVG_FRAMES, MEL_BANDS))
        self._mel_cnt = np.zeros(AVG_FRAMES)
        self._mel_mean = np.zeros(MEL_BANDS)
        self._mfcc = np.zeros(MFCC_N)
        self._craw = np.zeros(12)                     # this frame's chroma, unnormalised (0 under the floor)
        self._cn = np.zeros(12)                       # ... max-normalised
        self._c_hist = np.zeros((CHANGE_LAG + 1, 12)) # the last 4 frames' craw (time-based change)
        self._c_ok = np.zeros(CHANGE_LAG + 1, dtype=bool)
        self._cn_ring = np.zeros((AVG_FRAMES, 12))
        self._cavg = np.zeros(12)
        self._chg_ring = np.zeros(CHANGE_FRAMES)
        self._hp_ring = np.zeros(AVG_FRAMES)          # sum H^2, sum P^2 per frame (30 Hz-8 kHz)
        self._pp_ring = np.zeros(AVG_FRAMES)
        self._bs_acc = np.zeros(12)                   # the beat-synchronous change: this beat's chroma sum ...
        self._bs_prev = np.zeros(12)                  # ... and the previous beat's
        self._bs_t = np.zeros(CHANGE_BEATS_N)         # (end time, d) per finished beat
        self._bs_d = np.zeros(CHANGE_BEATS_N)
        self._bs_m = np.zeros(CHANGE_BEATS_N, dtype=bool)
        self._on_t = np.full(2 * ON_N, -math.inf)     # onsets (double-written: the last m are contiguous)
        self._on_s = np.zeros(2 * ON_N)
        self._sync: dict[int, tuple[list[int], list[int]]] = {}
        self._med_ms = np.zeros(512)
        self._med_i = 0
        self._med_frames = 0                          # since construction (stats)
        self._med_seq = 0                             # +1 per frame; tick publishes when it moves
        self._med_pub = 0
        self._med_block_s = 0.0                       # medium time inside the current process call (not a hop's)
        self._rhythm: Optional[RhythmData] = None
        self._rhythm_pubs = 0
        self._chroma_out: Optional[np.ndarray] = None
        self._rhythm_wall = -math.inf                 # tick's now_s when the latest frame was published
        self._tick_now = -math.inf
        self._gate_now = 0.0
        self._onsets_total = 0
        self._m_rate = 0.0

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
        x0 = pcm if pcm.ndim == 1 else pcm[:, 0]       # the noise floor's 100 ms bins (the gate's reference): a bin
        bin_n = self._T.floor_bin_n                    # closes at exactly floor_bin_n samples, whatever the blocks
        i = 0
        while i < frames:
            take = min(frames - i, bin_n - self._fl_n)
            if take > 0:
                seg = x0[i:i + take] if take < frames else x0
                self._fl_ss += float(np.dot(seg, seg))
                self._fl_n += take
                i += take
            if self._fl_n >= bin_n:
                p = self._fl_ss / self._fl_n
                self._floor_vals.append(10.0 * math.log10(p) if p > 1e-12 else DBFS_FLOOR)
                self._fl_ss, self._fl_n = 0.0, 0
        if flags & AUDIO_RESUME:                       # the splice: its first packet's worth, whatever the block
            r = min(frames, HIT_BLOCK)
            self._supp_hit_n = max(self._supp_hit_n, self._n + r + self.hop)
            self._supp_onset_n = max(self._supp_onset_n, self._n + r + FAST_N // 2 + self.hop)
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
        self._tick_now = now_s
        self._tick_features(now_s)
        if self._med_seq != self._med_pub:
            self._publish_rhythm(now_s)

    def _tick_features(self, now_s: float) -> None:
        if self._T is None or now_s < self._next_pub_s:
            return
        if self._hops_since_pub == 0:
            self._fade(now_s)
            return
        self._next_pub_s = max(self._next_pub_s + self._period, now_s + 0.5 * self._period)
        tp = time.perf_counter()
        data = self._compose()
        self._latest = data
        self._gate_now = data["gate"]
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
        self._gate_now = 0.0
        self._fades += 1
        self._publishes += 1
        if self._publish is not None:
            self._publish({"type": MUSIC_FEATURES, "data": data})

    def latest(self) -> Optional[FeaturesData]:
        return self._latest

    def rhythm(self) -> Optional[RhythmData]:
        return self._rhythm

    def chroma12(self) -> Optional[np.ndarray]:
        c = self._chroma_out
        if c is None or self._tick_now - self._rhythm_wall > CHROMA_STALE_S:
            return None
        return c

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
                "fs": self._fs, "channels": self._ch, "stream": self._stream, "kernel": KERNEL,
                "medium_frames": self._med_frames, "medium_ms_p50": pct(self._med_ms, self._med_i, 50),
                "medium_ms_p95": pct(self._med_ms, self._med_i, 95), "rhythm_publishes": self._rhythm_pubs,
                "onsets": self._onsets_total, "batch": self._batch_hits, "batch_fallbacks": self._batch_fallbacks,
                "gate_ref_lufs": round(self._gate_ref_eff, 1),
                "gate_offset_db": self.gate_offsets.get(self._stream, 0.0),
                "floor_lufs": None if self._floor_lufs is None else round(self._floor_lufs, 1)}

    # -- state -------------------------------------------------------------------------------------------------------
    def _rebuild(self, fs: int, ch: int) -> None:
        T = self._tables.get(fs)
        if T is None:
            T = self._tables[fs] = _Tables(fs)
        self._T, self._fs, self._ch = T, fs, ch
        self._hops_v.clear()                           # the multi-hop views hold the rate's mel bins
        try:
            self._r128 = pyebur128.R128State(ch, fs, _MODES)
        except Exception:                              # noqa: BLE001 - an unsupported rate: no loudness, still hits
            self._r128 = None
        self._lufs_s = LUFS_FLOOR
        self._lufs_s_t = -math.inf
        self._alloc_medium(T)
        self._reset_state()

    def _alloc_medium(self, T: _Tables) -> None:
        """The medium lane's rate-dependent buffers (sizes from the rate and the hop); kept when nothing changed."""
        hps = T.fs / self.hop                                      # hops per second
        eps = hps / PULSE_HOPS                                     # the pulse envelope's samples per second
        h1s = max(1, int(round(ONSET_MED_S * hps)))
        pulse_n = max(16, PULSE_HOPS * int(round(PULSE_S * eps)))  # hops, a whole number of envelope samples
        lag_lo = max(1, int(round(PULSE_LAG_S[0] * eps)))          # in envelope samples
        lag_hi = max(lag_lo, int(round(PULSE_LAG_S[1] * eps)))
        fx_n = _pow2_at_least(max(pulse_n, h1s) + 2 * ONSET_HALF + int(hps) + 64)
        nf = _pow2_at_least(pulse_n // PULSE_HOPS + lag_hi + 1)   # zero-padded past the largest lag: no wrap
        key = (T.n_m, T.k_m, T.k_c1 - T.k_c0, fx_n, nf)
        self._h1s, self._pulse_n, self._lag_lo, self._lag_hi = h1s, pulse_n, lag_lo, lag_hi
        self._pulse_min = PULSE_HOPS * max(2 * lag_hi, int(round(PULSE_MIN_S * eps)))
        if key == self._mk:
            return
        self._mk = key
        km, kc = T.k_m, T.k_c1 - T.k_c0
        self._m_frame = np.zeros(T.n_m)
        self._m_spec = np.zeros(T.n_m // 2 + 1, dtype=np.complex128)
        self._m_re, self._m_im = self._m_spec.real[:km], self._m_spec.imag[:km]
        self._m_pw = np.zeros(km)                     # |X|^2
        # the time median runs in float32 (half the cache; min/max only pick stored values, each >= HIST_FLOOR,
        # so nothing goes denormal): the last 9 frames' |X|^2 as [place in its group, group, bin], each group
        # also kept sorted along the first axis
        f4 = np.float32
        self._m_hist = np.zeros((3, 3, km), dtype=f4)
        self._m_sort = np.zeros((3, 3, km), dtype=f4)
        self._m_t4 = np.zeros(km, dtype=f4)
        self._m_lo = np.zeros(km, dtype=f4)
        self._m_hi = np.zeros(km, dtype=f4)
        self._m_md = np.zeros(km, dtype=f4)
        self._m_H4 = np.zeros(km, dtype=f4)
        self._m_t1 = np.zeros(km)
        self._m_H = np.zeros(km)                      # median in time (harmonic)
        self._m_m2 = np.zeros(km)
        self._m_om = np.zeros(km)
        self._m_P = np.zeros(km)                      # median in frequency (percussive)
        self._m_M = np.zeros(km)                      # the soft mask
        self._m_mag = np.zeros(kc)
        self._fx_n = fx_n
        self._fx = np.zeros(2 * fx_n)                 # per-hop flux, double-written
        self._ft = np.zeros(2 * fx_n)                 # per-hop onset time
        self._fx_i = 0                                # hops written (never reset; the ring is cleared instead)
        self._fx_med = np.zeros(fx_n)
        self._b1 = np.zeros(fx_n, dtype=bool)
        self._nf = nf
        self._ac_in = np.zeros(nf)
        self._ac_spec = np.zeros(nf // 2 + 1, dtype=np.complex128)
        self._ac_re, self._ac_im = self._ac_spec.real, self._ac_spec.imag
        self._ac_t = np.zeros(nf // 2 + 1)
        self._ac_out = np.zeros(nf)

    def _med_reset(self) -> None:
        if self._mk is not None:
            self._m_hist.fill(0.0)
            self._m_sort.fill(0.0)
            self._fx.fill(0.0)
            self._ft.fill(0.0)
        self._fx_rs = self._fx_i if self._mk is not None else 0   # the first hop since the reset
        self._pp_i = self._fx_rs                       # the next hop to peak-pick
        self._last_on_j = -(1 << 60)
        self._med_k = 1                                # the next frame: when n_end * MED_HZ >= k * fs
        self._med_n = 0                                # frames since the reset
        self._med_t0 = math.nan                        # the first frame's time
        self._mel_acc.fill(0.0)
        self._mel_cnt_acc = 0
        self._mel_ring.fill(0.0)
        self._mel_cnt.fill(0.0)
        self._c_hist.fill(0.0)
        self._c_ok.fill(False)
        self._cn_ring.fill(0.0)
        self._chg_ring.fill(0.0)
        self._hp_ring.fill(0.0)
        self._pp_ring.fill(0.0)
        self._on_t.fill(-math.inf)
        self._on_s.fill(0.0)
        self._on_i = 0
        self._bs_reset()
        self._m_rate = 0.0
        self._m_pulse = 0.0
        self._m_swing: Optional[float] = None
        self._m_sync: Optional[float] = None

    def _bs_reset(self) -> None:
        self._bs_k: Optional[int] = None               # the beat being summed
        self._bs_n = 0
        self._bs_acc.fill(0.0)
        self._bs_prev_k: Optional[int] = None
        self._bs_epoch = None
        self._bs_t.fill(-math.inf)
        self._bs_d.fill(0.0)
        self._bs_i = 0

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
        self._med_reset()

    def _clear_history(self) -> None:
        self._floor_vals.clear()
        self._floor_lufs = None
        self._floor_t = -math.inf
        self._fl_ss, self._fl_n = 0.0, 0
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
        self._acc_s += dt - self._med_block_s          # a medium frame is timed on its own (medium_ms)
        self._med_block_s = 0.0
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
            self._sat_p = 0
        else:                                          # whole pieces in one pass, then a short tail as today
            P, r = divmod(n, HIT_BLOCK)
            m = n - r
            if self._batch_hits:
                self._hit_pieces(work, m, P, t0)
            else:
                self._hit_pieces_1(work, m, P, t0)
            if r:
                self._hit_path(work[m:], r, t0 + m / self._fs, self._n + m)
        return self._analysis(work, n, t0)

    def _hit_pieces(self, work: np.ndarray, m: int, P: int, t0: float) -> None:
        """The first m = P x HIT_BLOCK samples of the chunk: one batched pass, its hits fired after it in order. If
        the pass raises, the hit path's state is put back and the pieces run one by one (degrade, never refuse)."""
        np.copyto(self._bk_zi_band, self._zi_band)
        np.copyto(self._bk_zi_fast, self._zi_fast)
        np.copyto(self._bk_slow, self._slow)
        self._bk_armed[:] = self._armed
        self._bk_ok[:] = self._ok_t
        d = self._dpos
        pend = self._pend
        try:
            self._hit_block(work[:m], m, P, t0, self._n)
            self._sat_p, self._sat_n0, self._sat_sb = P, self._n, self._pieces[P].sb
        except Exception:                              # noqa: BLE001 - today's per-piece path, from the same state
            self._batch_fallbacks += 1
            pend.clear()
            np.copyto(self._zi_band, self._bk_zi_band)
            np.copyto(self._zi_fast, self._bk_zi_fast)
            np.copyto(self._slow, self._bk_slow)
            self._armed[:] = self._bk_armed
            self._ok_t[:] = self._bk_ok
            self._dpos = d
            self._hit_pieces_1(work, m, P, t0)
            return
        if pend:                                       # today's publish order: piece by piece, band by band
            if len(pend) > 1:
                pend.sort()
            for _p, b, t, pk in pend:
                self._fire(b, t, pk)
            pend.clear()

    def _hit_pieces_1(self, work: np.ndarray, m: int, P: int, t0: float) -> None:
        """The same m = P x HIT_BLOCK samples piece by piece, today's way (batch=False, and the batched pass's
        fallback), each hit fired as found; the slow envelopes at each piece's end are kept for _medium all the same."""
        pc = self._pieces.get(P)
        if pc is None:
            pc = self._pieces[P] = _Pieces(self, P)
        sb, fs = pc.sb, self._fs
        for k, h0 in enumerate(range(0, m, HIT_BLOCK)):
            np.copyto(sb[:, k], self._slow)
            self._hit_path(work[h0:h0 + HIT_BLOCK], HIT_BLOCK, t0 + h0 / fs, self._n + h0)
        np.copyto(sb[:, P], self._slow)
        self._sat_p, self._sat_n0, self._sat_sb = P, self._n, sb

    def _slow_for(self, n_end: int) -> np.ndarray:
        """The slow envelopes as the hit path left them at the end of the piece holding sample n_end - 1 of the
        current chunk (what per-packet delivery reads at that hop): a column of the batched path's sb, else
        self._slow."""
        P = self._sat_p
        if P:
            k = -(-(n_end - self._sat_n0) // HIT_BLOCK)
            if 0 <= k <= P:
                return self._sat_sb[:, k]
        return self._slow

    def _hit_path(self, x: np.ndarray, n: int, t0: float, n_base: int) -> None:
        """One piece of <= HIT_BLOCK samples starting at real-sample index ``n_base`` (since the reset)."""
        T = self._T
        v = self._view(n)
        d = self._dpos
        np.add(x, self._dither[d:d + n], out=v.row0)           # mono + dither into all six rows
        np.copyto(v.rest, v.row0)                              # (a broadcasting out= would allocate)
        self._dpos = (d + n) % DITHER_N
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
        self._armed[b] = self._scan_row(b, armed, v.frows[b], v.rowbool, float(self._thr_on[b]),
                                        float(self._thr_off[b]), t0, n_base, None, 0)

    def _scan_row(self, b: int, armed: bool, row: np.ndarray, rb: np.ndarray, on_thr: float, off_thr: float,
                  t0: float, n_base: int, pend: Optional[list], key: int) -> bool:
        """Band b's piece ``row`` (fast envelope) sample by sample: arm, fire, disarm; returns the armed state. A hit
        fires at once, or with ``pend`` is kept as (key, b, t, peak) for the caller to fire in order."""
        fs = self._fs
        n = row.shape[0]
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
                    pk = float(row[k:k + self._T.peak_n].max())
                    if pend is None:
                        self._fire(b, t, pk)
                    else:
                        pend.append((key, b, t, pk))
            else:
                np.less(row, off_thr, out=rb)
                k = i + int(rb[i:].argmax())
                if not rb[k]:
                    break
                armed = True
            i = k + 1
        return armed

    def _hit_block(self, x: np.ndarray, n: int, P: int, t0: float, n_base: int) -> None:
        """The hit path over P >= 1 whole HIT_BLOCK pieces in one pass, with exactly the per-piece arithmetic: the
        filters run over the block with their state carried (the recursion is sequential, so this is the pieces
        one after another), each piece's max, min and sum come from one reduction over (6P, HIT_BLOCK) rows, the
        slow envelope steps piece by piece (the kernel, as a one-pole section; the running mean after a reset in a
        loop), the thresholds from slow at each piece's start, and only a band and piece whose max (armed) or min
        (disarmed) crosses is scanned. Hits go to self._pend as (piece, band, t, peak): the caller fires them."""
        T = self._T
        v = self._view(n)
        pc = self._pieces.get(P)
        if pc is None:
            pc = self._pieces[P] = _Pieces(self, P)
        d = self._dpos
        np.add(x, self._dither[d:d + n], out=v.row0)           # mono + dither into all six rows
        np.copyto(v.rest, v.row0)
        self._dpos = (d + n) % DITHER_N
        sos, rows, zi = T.sos, v.rows, self._zi_rows
        for b in range(NB):
            _filt(sos[b], rows[b], zi[b])
        fast = v.fast
        np.multiply(v.band, v.band, out=fast)
        _filt(T.sos_fast, fast, self._zi_fast)
        f2 = pc.f2
        np.maximum.reduce(f2, axis=1, out=pc.pmax)
        np.minimum.reduce(f2, axis=1, out=pc.pmin)
        np.dot(f2, self._ones_h, out=pc.psum)

        # the slow envelope at each piece's start (sb[:, p]) and after the block (sb[:, P])
        slow, sb = self._slow, pc.sb
        H = HIT_BLOCK
        if n_base + H >= T.slow_n:                     # steady state: y = b0 sum + a y_prev from y = slow
            np.copyto(pc.sb1, pc.psum2)
            pc.sb0.fill(0.0)
            np.copyto(self._zs0, slow)
            self._zs1.fill(0.0)
            _filt(T.sos_slow, sb, self._zi_slow)
            np.copyto(slow, sb[:, P])
        else:                                          # after a reset: the running mean, piece by piece
            np.copyto(pc.sb0, slow)
            fsum = self._fsum
            seen = n_base
            for p in range(P):
                seen += H
                w = H / seen if seen < T.slow_n else T.w_slow
                np.multiply(pc.psum2[:, p], w / H, out=fsum)
                slow *= 1.0 - w
                slow += fsum
                np.copyto(sb[:, p + 1], slow)

        stc, ton, toff, aon, aoff = pc.stc, pc.ton, pc.toff, pc.aon, pc.aoff
        np.copyto(stc, pc.st)
        np.multiply(stc, pc.kon, out=ton)
        np.maximum(ton, self._floor_p, out=ton)
        np.multiply(stc, self._k_off, out=toff)
        np.greater(pc.pmax2, ton, out=aon)
        np.less(pc.pmin2, toff, out=aoff)
        pend = self._pend
        fs, rb, armed_l = self._fs, self._rb_h, self._armed
        for b in range(NB):                            # per band: the next piece that can change its state
            armed = armed_l[b]
            on_b, off_b, row_b = aon[b], aoff[b], fast[b]
            p = 0
            while p < P:
                m = on_b if armed else off_b
                p = p + int(m[p:].argmax())
                if not m[p]:
                    break
                p0 = p * H
                armed = self._scan_row(b, armed, row_b[p0:p0 + H], rb, float(ton[b, p]), float(toff[b, p]),
                                       t0 + p0 / fs, n_base + p0, pend, p)
                p += 1
            armed_l[b] = armed

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
        first = hop - self._hop_fill                  # samples into the block at which its first hop ends
        if self._batch_hops and n >= first + (MULTI_HOPS - 1) * hop:
            return self._analysis_multi(x, n, t0, first)
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

    def _analysis_multi(self, x: np.ndarray, n: int, t0: float, first: int) -> int:
        """The ring and the fast lane for a block that completes MULTI_HOPS or more hops: the whole block into the
        ring at once (RING_N leaves CHUNK of room behind every read), then its hops in groups of MULTI_MAX."""
        hop, ring = self.hop, self._ring
        p0 = self._rpos
        end = p0 + n
        if end <= RING_N:
            ring[p0:end] = x
            ring[p0 + RING_N:end + RING_N] = x
        else:
            k = RING_N - p0
            ring[p0:RING_N] = x[:k]
            ring[p0 + RING_N:] = x[:k]
            ring[:n - k] = x[k:]
            ring[RING_N:RING_N + n - k] = x[k:]
        self._rpos = end % RING_N
        H = (n - first) // hop + 1
        self._hop_fill = n - (first + hop * (H - 1))
        h = 0
        while h < H:
            g = min(MULTI_MAX, H - h)
            i0 = first + hop * h
            try:
                hv = self._hops_flux(p0, i0, g)
            except Exception:                          # noqa: BLE001 - these hops the per-hop way (nothing changed)
                self._batch_fallbacks += 1
                fs, end_pos = self._fs, self._rpos
                for i in range(i0, i0 + hop * g, hop):
                    self._rpos = (p0 + i) % RING_N
                    self._fast_lane(t0 + (i - 1) / fs, self._n + i)
                self._rpos = end_pos
            else:
                self._hops_multi(hv, t0, p0, i0, g)
            h += g
        self._n += n
        return H

    def _hops_flux(self, p0: int, i0: int, g: int) -> _Hops:
        """g hops ending at block indices i0, i0 + hop, ... (the block starting at ring position p0): their log-mel
        rows and SuperFlux, exactly _fast_lane's numbers hop by hop, into scratch only (no state changes). The
        windows are one strided view of the double ring (copied, never np.take on it: that copies every window),
        one rfft over the rows (row by row identical), the log-mel bank row by row (np.dot as _fast_lane:
        bit-identical; one matmul would save ~7 us a hop for 4e-16 relative), SuperFlux against the carried
        max-filtered frames (hv.cur: this group's, for _hops_multi to keep)."""
        T = self._T
        hop = self.hop
        hv = self._hops_v.get(g)
        if hv is None:
            hv = self._hops_v[g] = _Hops(self, g, T.nb_fast)
        q0 = p0 + i0                                   # ring positions after the hops, unwrapped: q0 .. qL
        qL = q0 + hop * (g - 1)
        s0 = q0 + (RING_N if qL <= RING_N else 0) - FAST_N
        F = hv.F
        np.copyto(F, self._swv[s0:s0 + hop * (g - 1) + 1:hop])
        np.multiply(F, hv.hann, out=F)
        np.fft.rfft(F, axis=-1, out=hv.S)
        PW = hv.PW
        np.multiply(hv.RE, hv.RE, out=PW)
        np.multiply(hv.IM, hv.IM, out=hv.TP)
        PW += hv.TP
        MEL = hv.MEL
        mel_w = T.mel_w
        for pw_k, mel_k in zip(hv.pw_rows, hv.mel_rows):
            np.dot(mel_w, pw_k, out=mel_k)
        MEL += MEL_FLOOR
        np.log10(MEL, out=MEL)
        MEL *= 10.0
        # SuperFlux: hop j against frame j - lag max-filtered; the lag frames before this group come from _mf
        lag = self._sf_lag
        slots = lag + 1
        j0 = self._hops_rs
        mf = self._mf_rows
        for q, row in enumerate(hv.mf_in):
            np.copyto(row, mf[(j0 - lag + q) % slots])
        # the 3-band max filter on the flattened rows (1-D: no buffer): mx[j] = max(mel[j], mel[j + 1]); the pairs
        # that straddle two rows land only on each row's ends, which are then set from mx as _fast_lane's are
        mx = hv.mx
        np.maximum(hv.melf0, hv.melf1, out=mx)
        np.maximum(hv.mx0, hv.mx1, out=hv.cur_mid)
        np.copyto(hv.cur0, hv.mx_first)
        np.copyto(hv.cur1, hv.mx_last)
        DF, FL = hv.DF, hv.FL
        np.subtract(MEL, hv.MF, out=DF)
        np.maximum(DF, 0.0, out=DF)
        np.add.reduce(DF, axis=1, out=FL)
        return hv

    def _hops_multi(self, hv: _Hops, t0: float, p0: int, i0: int, g: int) -> None:
        """The state for the g hops _hops_flux computed: the newest max-filtered frames into _mf, the counters, then
        per hop, in order, _fast_lane's stores and the medium frame (given that hop's ring position and slow)."""
        T = self._T
        hop, fs = self.hop, self._fs
        lag = self._sf_lag
        slots = lag + 1
        j0 = self._hops_rs
        mf = self._mf_rows
        for q in range(min(slots, g)):                 # the newest frames back into _mf
            np.copyto(mf[(j0 + g - 1 - q) % slots], hv.cur_rows[g - 1 - q])
        FL = hv.FL
        self._hops_rs += g
        self._hops += g
        self._hops_since_pub += g

        # per hop, in order: suppression, the onset accumulator and log, the medium lane's rings, a medium frame
        nb0 = self._n
        supp = self._supp_onset_n
        delay = T.onset_delay_s
        olog_t, olog_v, on = self._olog_t, self._olog_v, self._olog_t.size
        fx, ft, fn = self._fx, self._ft, self._fx_n
        mel_acc, mel_rows = self._mel_acc, hv.mel_rows
        acc = self._onset_acc
        i = i0
        t_end = n_end = 0
        for k, flux in enumerate(FL.tolist()):
            t_end = t0 + (i - 1) / fs
            n_end = nb0 + i
            if n_end <= supp:
                flux = 0.0
            if flux > acc:
                acc = flux
            t_on = t_end - delay
            ko = self._olog_i % on
            olog_t[ko] = t_on
            olog_v[ko] = flux
            self._olog_i += 1
            fi = self._fx_i & (fn - 1)
            fx[fi] = fx[fi + fn] = flux
            ft[fi] = ft[fi + fn] = t_on
            self._fx_i += 1
            np.add(mel_acc, mel_rows[k], out=mel_acc)
            self._mel_cnt_acc += 1
            if n_end * MED_HZ >= self._med_k * fs:
                self._onset_acc = acc
                self._medium(t_end, n_end, (p0 + i) % RING_N, self._slow_for(n_end))
            i += hop
        self._onset_acc = acc
        self._t_hop = t_end
        self._hop_rpos = (p0 + i - hop) % RING_N

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
        # the medium lane's per-hop part: the flux and onset-time rings, the log-mel sum; a frame per 100 ms
        fn = self._fx_n
        fi = self._fx_i & (fn - 1)
        self._fx[fi] = self._fx[fi + fn] = flux
        self._ft[fi] = self._ft[fi + fn] = t_end - T.onset_delay_s
        self._fx_i += 1
        np.add(self._mel_acc, mel, out=self._mel_acc)
        self._mel_cnt_acc += 1
        if n_end * MED_HZ >= self._med_k * self._fs:
            self._medium(t_end, n_end, p, self._slow_for(n_end))

    # -- the medium lane ------------------------------------------------------------------------------------------------
    def _med3(self, a: np.ndarray, b: np.ndarray, c: np.ndarray, out: np.ndarray) -> None:
        """out <- the element-wise median of a, b, c: max(min(a, b), min(max(a, b), c)). out aliases none of them;
        all float32 (the time median's scratch)."""
        t = self._m_t4
        np.minimum(a, b, out=t)
        np.maximum(a, b, out=out)
        np.minimum(out, c, out=out)
        np.maximum(out, t, out=out)

    def _medium(self, t_end: float, n_end: int, p: int, slow: np.ndarray) -> None:
        """One medium frame at the hop ending at real sample n_end (the module docstring has every formula), its
        spectrum read from the ring back from ``p`` (the ring position after that hop) and low_db from ``slow``
        (the hit path's envelopes at that hop: _slow_for), so neither depends on how much of the block was
        processed before. Numbers only: tick builds the message."""
        tp = time.perf_counter()
        T = self._T
        self._med_k = n_end * MED_HZ // self._fs + 1
        i = self._med_n
        if i == 0:
            self._med_t0 = t_end

        # the spectrum: |X|^2 up to 8 kHz (at 44.1/48 kHz: [1 3 3 1], every other sample, a 4096-point FFT)
        n = T.n_m
        y = self._m_frame
        if T.m_dec == 2:
            r = self._ring[p + RING_N - T.n_raw:p + RING_N]
            np.add(r[1:2 * n + 1:2], r[2:2 * n + 2:2], out=y)
            y *= 3.0
            y += r[0:2 * n:2]
            y += r[3:2 * n + 3:2]
            y *= T.hann_m
        else:
            np.multiply(self._ring[p + RING_N - n:p + RING_N], T.hann_m, out=y)
        np.fft.rfft(y, out=self._m_spec)
        pw = self._m_pw
        np.multiply(self._m_re, self._m_re, out=pw)
        np.multiply(self._m_im, self._m_im, out=self._m_t1)
        pw += self._m_t1
        if T.m_comp is not None:
            pw *= T.m_comp

        # HPSS-lite: H = the median of the last 9 frames (3 sorted groups of 3), P = the median over 17 bins
        slot = i % 9
        g = slot // 3
        hist, srt = self._m_hist, self._m_sort
        np.add(pw, HIST_FLOOR, out=self._m_t1)
        np.copyto(hist[slot % 3, g], self._m_t1)       # a cast copy (a mixed-type ufunc would buffer)
        a, b, c = hist[0, g], hist[1, g], hist[2, g]
        np.minimum(a, b, out=srt[0, g])               # the group sorted: low, middle, high
        np.minimum(srt[0, g], c, out=srt[0, g])
        np.maximum(a, b, out=srt[2, g])
        np.maximum(srt[2, g], c, out=srt[2, g])
        self._med3(a, b, c, srt[1, g])
        H, P, M = self._m_H, self._m_P, self._m_M
        lo, hi, md = self._m_lo, self._m_hi, self._m_md
        np.maximum(srt[0, 0], srt[0, 1], out=lo)
        np.maximum(lo, srt[0, 2], out=lo)
        np.minimum(srt[2, 0], srt[2, 1], out=hi)
        np.minimum(hi, srt[2, 2], out=hi)
        self._med3(srt[1, 0], srt[1, 1], srt[1, 2], md)
        self._med3(lo, md, hi, self._m_H4)
        np.copyto(H, self._m_H4)
        _median(pw, HPSS_F, P)
        np.add(H, P, out=M)
        M += HPSS_EPS
        np.divide(H, M, out=M)                        # = H^2 / (H^2 + P^2) on magnitudes
        m2, om = self._m_m2, self._m_om
        np.multiply(M, M, out=m2)
        np.subtract(1.0, M, out=om)
        np.multiply(om, om, out=om)
        k0 = T.k_p0
        sum_h = float(np.dot(pw[k0:], m2[k0:]))       # sum H^2, sum P^2 over 30 Hz-8 kHz
        sum_p = float(np.dot(pw[k0:], om[k0:]))
        hdb = max(DBFS_FLOOR, 10.0 * math.log10(sum_h * T.ms_m + 1e-30))

        # chroma12 from the harmonic magnitudes, 55 Hz-5 kHz
        craw, cn = self._craw, self._cn
        if hdb < HARM_FLOOR_DB or T.k_c1 <= T.k_c0:
            craw.fill(0.0)
        else:
            mag = self._m_mag
            np.sqrt(pw[T.k_c0:T.k_c1], out=mag)
            mag *= M[T.k_c0:T.k_c1]
            np.dot(T.chroma_w, mag, out=craw)
        cmax = float(craw.max())
        ok = cmax > 1e-30
        if ok:
            np.divide(craw, cmax, out=cn)              # the max reads exactly 1.0
        else:
            craw.fill(0.0)
            cn.fill(0.0)

        # the rings over 1 s: chroma12_avg, percussive_ratio, mfcc13
        k = i % AVG_FRAMES
        n_avg = min(i + 1, AVG_FRAMES)
        np.copyto(self._cn_ring[k], cn)
        np.add.reduce(self._cn_ring[:n_avg], axis=0, out=self._cavg)
        self._cavg *= 1.0 / n_avg
        self._hp_ring[k] = sum_h
        self._pp_ring[k] = sum_p
        sh, sp = float(self._hp_ring.sum()), float(self._pp_ring.sum())
        perc = sp / (sh + sp) if (sh + sp) * T.ms_m > 1e-12 else 0.0
        np.copyto(self._mel_ring[k], self._mel_acc)
        self._mel_cnt[k] = self._mel_cnt_acc
        self._mel_acc.fill(0.0)
        self._mel_cnt_acc = 0
        cnt = float(self._mel_cnt.sum())
        if cnt > 0.0:
            np.add.reduce(self._mel_ring, axis=0, out=self._mel_mean)
            self._mel_mean *= 1.0 / cnt
            np.dot(_DCT, self._mel_mean, out=self._mfcc)

        # the chord change, time-based: against the frame 0.3 s before, mean over 4 s
        lag_n = CHANGE_LAG + 1
        np.copyto(self._c_hist[i % lag_n], craw)
        self._c_ok[i % lag_n] = ok
        j = (i - CHANGE_LAG) % lag_n
        d = 1.0 - _cos(craw, self._c_hist[j]) if i >= CHANGE_LAG and ok and self._c_ok[j] else 0.0
        self._chg_ring[i % CHANGE_FRAMES] = d
        n_chg = min(i + 1, CHANGE_FRAMES)
        harm = float(np.add.reduce(self._chg_ring[:n_chg])) / n_chg

        # onsets, onset_rate; pulse_clarity on even frames (5 Hz: a 6 s window), held on odd ones
        self._pick_onsets()
        self._m_rate = self._onset_rate(t_end)
        if i % 2 == 0:
            self._m_pulse = self._pulse_clarity()

        # with the clock locked: the beat-synchronous chord change every frame; swing and syncopation on odd
        # frames (5 Hz: 8 beats, 4 bars), held on even ones; None as soon as the lock goes
        ck = self._clock()
        if ck is None:
            self._m_swing = self._m_sync = None
            if self._bs_k is not None or self._bs_i:
                self._bs_reset()
        else:
            try:
                hb = self._beat_change(ck, t_end, craw)
                if hb is not None:
                    harm = hb
                if i % 2 == 1 or self._m_sync is None:
                    self._m_swing, self._m_sync = self._grid(ck, t_end)
            except Exception:                          # noqa: BLE001 - a bad clock body: no grid, never a failure
                self._m_swing = self._m_sync = None
                self._bs_reset()

        self._m_low = max(DBFS_FLOOR, 10.0 * math.log10(float(slow[0]) + float(slow[1]) + 1e-30) + DB_SINE)
        self._m_t = t_end
        self._m_hdb = hdb
        self._m_under = not ok
        self._m_perc = perc
        self._m_harm = min(1.0, max(0.0, harm))
        self._med_n += 1
        self._med_frames += 1
        self._med_seq += 1
        dt = time.perf_counter() - tp
        self._med_block_s += dt
        self._med_ms[self._med_i % self._med_ms.size] = dt * 1000.0
        self._med_i += 1

    def _pick_onsets(self) -> None:
        """Peak-pick the hops that have 3 hops after them and were not picked yet."""
        fx, ft, n_fx = self._fx, self._ft, self._fx_n
        last = self._fx_i - 1 - ONSET_HALF
        j0 = max(self._pp_i, self._fx_rs + ONSET_HALF, self._fx_i - n_fx + 2 * ONSET_HALF + 1)
        if last < j0:
            return
        n = last - j0 + 1
        self._pp_i = last + 1
        m1 = min(self._h1s, last + 1 - self._fx_rs)    # the median of the last 1 s of hops (up to `last`)
        s0 = (last + 1 - m1) % n_fx
        med = self._fx_med[:m1]
        np.copyto(med, fx[s0:s0 + m1])
        kth = m1 // 2
        med.partition(kth)
        thr = ONSET_K * float(med[kth]) + ONSET_FLOOR
        b = (j0 - ONSET_HALF) % n_fx
        seg = fx[b:b + n + 2 * ONSET_HALF]               # hops j0 - 3 .. last + 3, contiguous
        above = self._b1[:n]
        np.greater(seg[ONSET_HALF:ONSET_HALF + n], thr, out=above)
        if not above.any():
            return
        for q in np.flatnonzero(above):                  # a few hops: each checked for the local max over +-3
            q = int(q)
            v = seg[q + ONSET_HALF]
            j = j0 + q
            if v < seg[q:q + 2 * ONSET_HALF + 1].max() or j - self._last_on_j <= ONSET_HALF:
                continue
            self._last_on_j = j
            ko = self._on_i % ON_N
            self._on_t[ko] = self._on_t[ko + ON_N] = ft[j % n_fx]
            self._on_s[ko] = self._on_s[ko + ON_N] = v
            self._on_i += 1
            self._onsets_total += 1

    def _recent_onsets(self, t_from: float) -> tuple[np.ndarray, np.ndarray]:
        """Views of the kept onsets' times and strengths from ``t_from`` on (oldest first)."""
        m = min(self._on_i, ON_N)
        e = self._on_i % ON_N + ON_N
        tv = self._on_t[e - m:e]
        a = int(np.searchsorted(tv, t_from, side="left"))
        return tv[a:], self._on_s[e - m + a:e]

    def _onset_rate(self, t_end: float) -> float:
        tv, _ = self._recent_onsets(t_end - RATE_S)
        span = min(RATE_S, max(1.0, t_end - self._med_t0 + 1.0 / MED_HZ))
        return tv.size / span

    def _pulse_clarity(self) -> float:
        L = min(self._fx_i - self._fx_rs, self._pulse_n) // PULSE_HOPS * PULSE_HOPS
        if L < self._pulse_min:
            return 0.0
        s = (self._fx_i - L) % self._fx_n
        x = self._fx[s:s + L]
        m = L // PULSE_HOPS
        xin = self._ac_in
        e = xin[:m]
        np.add(x[0::PULSE_HOPS], x[1::PULSE_HOPS], out=e)    # the 2-hop envelope (PULSE_HOPS = 2)
        mu = float(np.add.reduce(e)) / m
        e -= mu
        xin[m:] = 0.0
        if float(np.dot(e, e)) < m * PULSE_FLOOR * PULSE_FLOOR:
            return 0.0                                   # a steady sound's flux is not a pulse
        np.fft.rfft(xin, out=self._ac_spec)
        re, im, t = self._ac_re, self._ac_im, self._ac_t
        np.multiply(re, re, out=t)
        np.multiply(im, im, out=im)
        np.add(t, im, out=re)
        im.fill(0.0)
        np.fft.irfft(self._ac_spec, n=self._nf, out=self._ac_out)
        r0 = float(self._ac_out[0])
        if not r0 > 1e-9:
            return 0.0
        return _clamp01(float(self._ac_out[self._lag_lo:self._lag_hi + 1].max()) / r0)

    def _clock(self) -> Optional[ClockData]:
        """The clock's body when its grid is trusted (gate beat or dmx, a tempo); else None. Never raises."""
        fn = self.clock_fn
        if fn is None:
            return None
        try:
            ck = fn()
            if ck is None or ck.get("gate") not in LOCK_GATES:
                return None
            bpm = float(ck["bpm"])
            return ck if 1.0 <= bpm <= 1000.0 and math.isfinite(float(ck["anchor_ms"])) else None
        except Exception:                              # noqa: BLE001
            return None

    def _beat_change(self, ck: ClockData, t_end: float, craw: np.ndarray) -> Optional[float]:
        """The beat-synchronous chord change: the mean d over the beats that ended in the last 4 s; None with
        fewer than 2 of them."""
        ep = ck.get("epoch")
        if ep != self._bs_epoch:
            self._bs_reset()
            self._bs_epoch = ep
        k = math.floor(float(ck["beat_at_anchor"]) + (t_end * 1000.0 - float(ck["anchor_ms"]))
                       * float(ck["bpm"]) / 60000.0)
        if self._bs_k is not None and k != self._bs_k:
            if self._bs_n > 0:
                cur, prev = self._bs_acc, self._bs_prev
                if self._bs_prev_k is not None and self._bs_prev_k == self._bs_k - 1:
                    d = 1.0 - _cos(cur, prev) if cur.any() and prev.any() else 0.0
                    slot = self._bs_i % CHANGE_BEATS_N
                    self._bs_t[slot] = t_end
                    self._bs_d[slot] = d
                    self._bs_i += 1
                np.copyto(prev, cur)
                self._bs_prev_k = self._bs_k
            self._bs_acc.fill(0.0)
            self._bs_n = 0
        self._bs_k = k
        self._bs_acc += craw
        self._bs_n += 1
        if self._bs_i < CHANGE_MIN_BEATS:
            return None
        mk = self._bs_m
        np.greater_equal(self._bs_t, t_end - CHANGE_S, out=mk)    # unused slots hold -inf
        n = int(np.count_nonzero(mk))
        return float(np.add.reduce(self._bs_d, where=mk)) / n if n >= CHANGE_MIN_BEATS else None

    def _grid(self, ck: ClockData, t_end: float) -> tuple[Optional[float], float]:
        """(swing, syncopation) from the onsets of the last max(8 beats, 4 bars), in one pass. A few dozen
        onsets: plain Python is cheaper here than numpy's per-call cost."""
        bpm = float(ck["bpm"])
        bpb = max(1, int(ck.get("beats_per_bar") or 4))
        steps = 4 * bpb
        k = bpm / 60000.0
        a_ms, b0 = float(ck["anchor_ms"]), float(ck["beat_at_anchor"])
        bar0 = float(ck.get("bar_anchor_beat") or 0)
        b_now = b0 + (t_end * 1000.0 - a_ms) * k
        sw_from, sy_from = b_now - SWING_BEATS, b_now - SYNC_BARS * bpb
        tv, sv = self._recent_onsets(t_end - max(SWING_BEATS, SYNC_BARS * bpb) * 60.0 / bpm)
        phases: list[float] = []
        pat = [0.0] * steps
        ph_lo, ph_hi = SWING_PHASE
        for t, st in zip(tv.tolist(), sv.tolist()):
            b = b0 + (t * 1000.0 - a_ms) * k
            if b >= sy_from:
                pat[int(math.floor((b - bar0) * 4.0 + 0.5)) % steps] += st
            if b >= sw_from:
                ph = b - math.floor(b)
                if ph_lo <= ph <= ph_hi:
                    phases.append(ph)
        swing = None
        if len(phases) >= SWING_MIN_N:
            phases.sort()
            h = len(phases) // 2
            m = phases[h] if len(phases) % 2 else 0.5 * (phases[h - 1] + phases[h])
            swing = min(SWING_RANGE[1], max(SWING_RANGE[0], m / (1.0 - m)))
        tot = sum(pat)
        if not tot > 0.0:
            return swing, 0.0
        tabs = self._sync.get(steps)
        if tabs is None:
            tabs = self._sync[steps] = _sync_tables(steps)
        syn = 0.0
        for w, nx in zip(*tabs):
            if pat[nx] < SYNC_HALF * pat[w]:
                syn += pat[w]
        return swing, _clamp01(syn / tot)

    def _publish_rhythm(self, now_s: float) -> None:
        self._med_pub = self._med_seq
        q = self._q
        lo, hi = float(q.lo[Q_RATE]), float(q.hi[Q_RATE])
        gate = float(self._gate_now)
        rate = float(self._m_rate)
        density = _clamp01((rate - lo) / max(1e-6, hi - lo)) * gate
        sw, sy = self._m_swing, self._m_sync
        data: RhythmData = {
            "t_ms": round(self._m_t * 1000.0, 3), "stream": self._stream or "",
            "chroma12": np.round(self._cn, 4).tolist(), "chroma12_avg": np.round(self._cavg, 4).tolist(),
            "harmonic_db": round(float(self._m_hdb), 2), "percussive_ratio": round(float(self._m_perc), 4),
            "harm_change": round(float(self._m_harm), 4), "onset_rate": round(rate, 3),
            "density": round(density, 4), "pulse_clarity": round(float(self._m_pulse), 4),
            "swing": None if sw is None else round(float(sw), 3),
            "syncopation": None if sy is None else round(float(sy), 4),
            "low_db": round(float(self._m_low), 2), "mfcc13": np.round(self._mfcc, 3).tolist(),
            "gate": round(gate, 4)}
        self._rhythm = data
        self._chroma_out = None if self._m_under else self._cn.copy()
        self._rhythm_wall = now_s
        self._rhythm_pubs += 1
        if self._publish is not None:
            self._publish({"type": MUSIC_RHYTHM, "data": data})

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
        if t - self._floor_t >= 1.0:
            self._floor_t = t
            if len(self._floor_vals) >= FLOOR_MIN_S / FLOOR_BIN_S:
                self._floor_lufs = max(LUFS_FLOOR, float(np.percentile(np.fromiter(self._floor_vals, float), FLOOR_PCT)))
        ref_l = float(self.gate_ref_lufs) + self.gate_offsets.get(self._stream, 0.0)
        if self._floor_lufs is not None:
            ref_l = min(ref_l, self._floor_lufs + FLOOR_ABOVE_LU + GATE_SPAN_LU)
        self._gate_ref_eff = ref_l
        gate = _smoothstep(ref_l - GATE_SPAN_LU, ref_l, lufs_s)

        # the 4096-point spectrum: spectrum32, brightness, noisiness
        p = self._hop_rpos
        np.multiply(self._ring[p + RING_N - SPEC_N:p + RING_N], T.hann_spec, out=self._frame_s)
        np.fft.rfft(self._frame_s, out=self._spec_s)
        ms = float(np.dot(self._frame_s, self._frame_s)) / T.hann_spec_ss
        fast_db = 10.0 * math.log10(ms) if ms > 1e-12 else DBFS_FLOOR
        spec_gate = _smoothstep(ref_l - GATE_SPAN_LU, ref_l - GATE_SPAN_LU + SPEC_GATE_FAST_LU, fast_db)
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
        qv[Q_RATE] = self._m_rate
        q.push(t, qv)
        lo, hi = q.lo, q.hi

        ref = max(SPEC_REF_FLOOR_DB, float(q.hi60[Q_SPEC]) if q.ready else loudest)
        np.subtract(db32, ref - SPEC_WINDOW_DB, out=val32)
        val32 *= spec_gate / SPEC_WINDOW_DB
        np.maximum(val32, 0.0, out=val32)
        np.minimum(val32, spec_gate, out=val32)
        sm = self._spec_sm
        val32 -= sm
        a32 = self._a32
        a32.fill(1.0 - math.exp(-dt / SPEC_TAU_S))
        np.greater(val32, 0.0, out=self._up32)
        np.copyto(a32, 1.0 - math.exp(-dt / SPEC_RISE_TAU_S), where=self._up32)
        val32 *= a32
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
