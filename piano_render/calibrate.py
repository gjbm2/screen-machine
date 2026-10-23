"""Acoustic latency calibration: hear the rendered piano through the room's mic and put the MIDI clock where the
sound really is (owner, 22 Oct: "buffer the midi with the same latency to sonos to ensure timing accuracy (you have
the mic so you can calibrate for that in drawing room anyway)").

    cal = Calibrator("sonos:drawing-room", emit=proto.emit)     # or "pc22" / "pc22:<device>"
    t_audio0 = cal.begin(sink_t0, FileReference(render_path))     # announce this one in "started"
    ...                                                           # streaming: StreamReference + ref.sent(pos, blk)
    out = sonos.SonosOutput(zone, emit=cal.sink_emit(proto.emit)) # the sink's own rebases go through the calibrator
    cal.end(); cal.close()

The rule (README "Latency and compute"): MIDI time 0 is heard at H0 = the sink's own report + that output's delay.
``sink_t0`` is the sink's report (PC22: the player's DAC time of sample 0; Sonos: sonos.py's start estimate, then its
RelTime rebases). The delay starts at what this module measured last time on this output (``prior_delay``: the
median of the last KEEP_N good measurements in ~/.local/state/screen-machine/piano/latency.json, 0 with none) and is
then measured during the play:

1. **The mic.** show_service's levels feed (``ws://127.0.0.1:8703/v1/levels?wave=2048&stream=<mic>``,
   show_service/levels.py) carries the newest 2048 samples of the named mic's stream (``?stream=``, 23 Oct: whether
   or not it is the room's active input; it sends nothing while that mic isn't sending) at up to 60 frames a second,
   with the newest packet's counter ``c`` and its arrival time ``a`` (time.monotonic(), this box's clock: the one
   t_audio0 uses). An older feed ignores ``?stream`` and carries the *active* input only: the first frame of another
   stream tells (``feed`` "active only"), and then the check runs only while the named mic is the active input (the
   status says so otherwise).
   Frames overlap and are stitched on content (MicTap; tools/room/audio_cal/calibrate.py's tap: a whole-packet
   "race" where the wave is newer than ``c``, dropped frames filled with zeros and masked). The host time of a
   sample is the lower envelope of the arrivals (min of a - sample/fs over the window, the receiver's own rule),
   less the sender's input buffering beyond one packet (heartbeat ``in_lat_ms``) and TRANSPORT_S, plus
   config/room/latency.json ``sources.<mic>.acoustic_offset_ms`` (screen-machine-10's; 0 today). The mic per output:
   the session's ``mic`` (show_service's sources.py names it from show.yaml's senders and sonos.json: the one that
   hears that room; "" = none does: no mic check, and the status says why), else $PIANO_CALIBRATE_MIC, else MIC_FOR
   (the drawing room's kiosk "room-mic" for Sonos, "pc22-mic" for PC22). Read only: this module never changes
   show_service's input and never turns a mic on (a Sonos run's mic is claimed by show_service itself).
2. **What we sent.** The reference knows exactly what the sink was given: MIDI frame p plays at sink_t0 + p/fs.
   FileReference reads a rendered file; StreamReference takes the streamer's blocks (``sent(pos, data)``, cheap
   enough for the audio thread: a mono mix and an append; the work is done in the calibrator's thread).
3. **Correlate.** Both sides become the same envelope, 500 frames a second: 20 log-spaced bands (150 Hz-7 kHz) of a
   512-point (10.7 ms) FFT, each band's power over its own median to the power GAMMA (0.3; a fixed EQ, the room's
   or the mic's, cancels), less its 100 ms running mean. Every HOP_S (2 s) a WINDOW_S (6 s) stretch of the sent
   envelope is cross-correlated with the mic's over +-SEARCH_S (3 s) around the current estimate: the bands'
   cross-spectra summed, divided by their magnitude^WHITEN (0.7) so steady beats don't make sidelobes a beat apart,
   gaps masked. A window counts only if what was sent is music throughout (MUSIC_MIN of its 100 ms blocks within
   MUSIC_RANGE_DB of its loud ones: a window running into silence times badly) and the peak is clear: peak/sidelobe
   >= PSR_MIN (1.5; the best correlation more than MAIN_LOBE_S away) and z >= Z_MIN (7; the peak over the robust
   spread of the rest). The peak is refined by a parabola, so H0 = mic time - MIDI time of the peak.
   Why this feature: on the 21 Oct drawing-room recording (tools/room/audio_cal runs/20261021T074506Z-room-mic, the
   Sonos at 40, the kiosk's UM04 at -43 dBFS, SNR 14 dB; the measured chain is RT60 1 s, DRR -3 dB, and scrambles
   the spectrum by 6-10 dB at 40 ms scales) an onset feature (spectral flux) found the right lag in 9-60 % of 6 s
   windows and its clear peaks were no better than its wrong ones; this one in 76 % (62 of 82 non-quiet windows),
   with no clear window more than 60 ms off. The room's early reflections make a comb of peaks ~15 ms apart, so a
   window lands on one of them: AGREE_S is 20 ms.
4. **Accept.** AGREE_N (4) consecutive clear windows within AGREE_S of their median give a measurement: the run's
   first and last windows share no audio, so one coincidence (a loud chord that happens to line up with another)
   can't clear them all; on unrelated music of the same kind single windows cleared up to 30 % of the time, and
   three overlapping ones agreed once. So the first lock comes ~15 s into the piece (2 windows + the search).
   Once locked, a re-check every RECHECK_S (30 s) needs RECHECK_AGREE_N (2) when it is within RECHECK_NEAR_S of
   the lock, else AGREE_N (between re-checks no windows are run).
5. **Correct.** delay = H0 - sink_t0. A change under DEADBAND_S does nothing; up to SLEW_MAX_S it is slewed in steps
   of at most SLEW_STEP_S every SLEW_EVERY_S (the MIDI clock never jumps for small corrections); beyond that one step,
   never more than MAX_JUMP_S (a bigger offset is reached over two measurements). Each move is the session's
   ``{"ev": "rebase", "t_audio0", "was", "by": "mic", "delay_ms"}`` line (show_sender moves its MIDI clock). A sink
   rebase (``sink_rebased``; sonos.py sends one only past 0.7 s, so the timeline most likely changed) moves t_audio0
   with the sink, keeps the delay and checks again at once. Each accepted measurement (at most one per RECHECK_S) is
   persisted for next time, as the delay after the sink's report at that moment.
6. **Fail closed.** No clear peak, a quiet room, the mic off (the mic on demand: nothing has asked for it), another
   input active, show_service unreachable: no correction, the prior stays, and ``{"ev": "calibration", "state",
   "why", ...}`` says why in plain words (``status()`` too).

Compute: 3-4 % of one core while a piece plays, nothing between plays (the feed is closed: no client, no load on
show_service), ~10 MB beyond numpy. Measured: the live levels feed through the tap alone 3.4 % (``listen``, 20 s:
the JSON and base64 of ~55 frames a second 0.6 %, stitching 0.6 %, the mic's envelope 1.3 %); replaying the
recording below through Calibrator 2.3-2.9 % (241 s, 13,047 frames, both envelopes and the windows; between
re-checks no windows run). A 250 frames/s envelope halves the envelope cost but loses a third to two thirds of the
clear windows on quiet sparse piano, so it stays at 500.
Accuracy, replaying the 21 Oct drawing-room recording (the Sonos, the room mic) through Calibrator: the first lock
1 ms from the recording's own alignment (audio_cal's fit), later re-checks 11-15 ms early of it (the comb above);
from a start estimate 496 ms late (the Sonos's RelTime ticks) one 495 ms step, then within 15 ms. Synthetic
(roomsim with the measured drawing-room and PC22 chains, known delay; Chopin, Rachmaninoff, Monk renders): median
error 0.3-0.9 ms, no clear window wrong, clear windows 63-100 % down to SNR -5 dB. tests/test_calibrate.py has an
SNR sweep on a FluidSynth render through a synthetic room.

How piano_render/session.py wires it (one Calibrator per session process; its threads idle between plays):
  * Every play is a stream: ``ref = StreamReference()``, and each block handed to the sink also goes to
    ``ref.sent(blk.pos, blk.data)`` (session.PlaySource).
  * PC22: ``t0 = out.start(source)``, then ``t_audio0 = cal.begin(t0, ref)`` and "started" carries that t_audio0;
    ``cal.end()`` on stop or played. The key: ``output_key("pc22", device=<the player's ready device>)``.
  * Sonos: ``SonosOutput(zone, emit=cal.sink_emit(proto.emit))`` (its rebases come through here), the same begin with
    its start estimate.
  * Off for a silent check run (PIANO_SESSION_GAIN_DB set: there is nothing to hear) and with $PIANO_CALIBRATE=0.
  * FileReference stays for offline checks of a rendered file (``measure``).

CLI (read only, no sound):
    python -m piano_render.calibrate status                       # persisted delays; show_service's mic state
    python -m piano_render.calibrate listen [--output OUT] [--seconds N]   # the levels feed through the tap: frames,
                                                                  # gaps, races, level, the host-time map
    python -m piano_render.calibrate measure REF REC [--offset S] [--start S] [--end S]   # offline: per-window lag
                                                                  # of a recording against what was played
"""
from __future__ import annotations

import argparse
import base64
import collections
import datetime as _dt
import hashlib
import json
import logging
import math
import os
import socket
import statistics
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import numpy as np

log = logging.getLogger("piano_render.calibrate")

REPO = Path(__file__).resolve().parent.parent
SHOW_URL_ENV = "PIANO_SHOW_URL"
SHOW_URL = "http://127.0.0.1:8703"
LEVELS_PATH = "/v1/levels"
WAVE_N = 2048                       # levels.WAVE_MAX: samples a frame carries
TAP_MIN_OVERLAP = 64                # samples two frames must share to be stitched on content
PACKET_DEFAULT = 128                # samples a VBAN packet carries (state streams.<s>.format.frames)
TRANSPORT_S = 0.0005                # the smallest one-way network delay (LAN / WSL bridge): not observable here
GAP_FILL_MAX_S = 2.0                # a longer hole in the feed starts the tap again
LAT_FILE_ENV = "PIANO_LATENCY_FILE"
DEFAULT_LAT_FILE = Path("~/.local/state/screen-machine/piano/latency.json").expanduser()
ROOM_LATENCY_FILE = REPO / "config" / "room" / "latency.json"    # screen-machine-10's: read only
MIC_ENV = "PIANO_CALIBRATE_MIC"
MIC_FOR = {"pc22": "pc22-mic", "sonos": "room-mic"}
KEEP_N = 9                          # measurements kept per output; the prior is their median
DELAY_LIMIT_S = 15.0                # a persisted delay beyond this is ignored (a Sonos radio stream: seconds)

ENV_HZ = 500                        # envelope frames per second (1000: the same accuracy at twice the CPU)
FFT_N_48K = 512                     # the envelope's FFT at 48 kHz (scaled to other rates)
BAND_LO_HZ, BAND_HI_HZ, N_BANDS = 150.0, 7000.0, 20
GAMMA = 0.3                         # the bands' power compression
DETREND_S = 0.1                     # the running mean taken off each band
WHITEN = 0.7                        # the cross-spectrum is divided by its magnitude to this power

WINDOW_S = 6.0
HOP_S = 2.0
SEARCH_S = 3.0
# A Sonos plays a radio stream several seconds after its estimate (owner, 23 Oct: "when sonos plays music it's nowhere
# NEAR in time with the midi notes ... its latency is MUCH longer"): until a play's first lock its windows search this
# far either side, and that first correction is made in one step (a later one: MAX_JUMP_S at most, as before).
SEARCH_WIDE_S = 12.0
MAIN_LOBE_S = 0.06                  # the peak's own lobe (the room's early reflections make a comb inside it)
PSR_MIN = 1.5
Z_MIN = 7.0
COVERAGE_MIN = 0.85                 # of a window's mic frames that must be real samples
REF_QUIET_DBFS = -60.0              # a sent window quieter than this (mean power) isn't music enough
MUSIC_RANGE_DB = 35.0               # a 100 ms block counts as music within this of the window's loud blocks...
MUSIC_MIN = 0.8                     # ...and this share of them must be
MIC_QUIET_DBFS = -85.0              # a mic window quieter than this hears nothing
AGREE_S = 0.020
AGREE_N = 1 + math.ceil(WINDOW_S / HOP_S - 1e-9)  # 4: the run's first and last windows share no audio (one
                                    # coincidence in shared audio can clear overlapping windows together)
RECHECK_S = 30.0
RECHECK_AGREE_N = 2
RECHECK_NEAR_S = 0.05
DEADBAND_S = 0.008
SLEW_MAX_S = 0.15
SLEW_STEP_S = 0.010
SLEW_EVERY_S = 0.2
MAX_JUMP_S = 2.0
RING_S = 40.0                       # envelope history kept per side
MAP_KEEP_S = 60.0                   # host-map points kept
STATE_POLL_S = 5.0
FEED_SILENT_S = 3.0                 # no frame of our mic this long: "no sound from the mic"
STATUS_MIN_S = 5.0                  # new words in the same state at most this often (a new state: at once)
STEP_S = 0.1                        # the control loop's period


# ---------------------------------------------------------------------------------------------------- envelope

class BandPower:
    """Mono samples -> per-band power every 1 ms (frame k centred on sample round(k * fs / ENV_HZ), Hann window),
    plus the window's mean square as the last column. Incremental (``push``) or on one array (``frames``)."""

    def __init__(self, fs: int = 48000):
        self.fs = int(fs)
        self.hop = self.fs / ENV_HZ
        self.n = int(2 ** round(math.log2(FFT_N_48K * self.fs / 48000.0)))
        self.half = self.n // 2
        self.win = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(self.n) / self.n)).astype(np.float32)
        freqs = np.fft.rfftfreq(self.n, 1.0 / self.fs)
        hi = min(BAND_HI_HZ, 0.45 * self.fs)
        edges = np.geomspace(BAND_LO_HZ, hi, N_BANDS + 1)
        m = np.zeros((freqs.size, N_BANDS), np.float32)
        for b in range(N_BANDS):
            sel = (freqs >= edges[b]) & (freqs < edges[b + 1])
            if not sel.any():                               # narrow low bands: the nearest bin
                sel = np.zeros(freqs.size, bool)
                sel[int(np.argmin(np.abs(freqs - math.sqrt(edges[b] * edges[b + 1]))))] = True
            m[sel, b] = 1.0
        self.bands = m
        self.wsum = float(np.sum(self.win ** 2))
        # incremental state: buf holds samples from absolute index buf0
        self.buf = np.zeros(self.half, np.float32)
        self.buf0 = -self.half
        self.k = 0

    def _start(self, k: np.ndarray) -> np.ndarray:
        return np.rint(k * self.hop).astype(np.int64) - self.half

    def frames(self, x: np.ndarray, x0: int, k0: int, k1: int) -> np.ndarray:
        """Frames k0..k1-1 from x (x[0] = absolute sample x0; samples outside x read 0): (k1-k0, N_BANDS+1)."""
        out = np.zeros((max(0, k1 - k0), N_BANDS + 1), np.float32)
        step = 1024
        for a in range(k0, k1, step):
            b = min(k1, a + step)
            st = self._start(np.arange(a, b)) - x0
            lo, hi = int(st[0]), int(st[-1]) + self.n
            seg = np.zeros(hi - lo, np.float32)
            s0, s1 = max(lo, 0), min(hi, len(x))
            if s1 > s0:
                seg[s0 - lo:s1 - lo] = x[s0:s1]
            idx = (st - lo)[:, None] + np.arange(self.n)[None, :]
            spec = np.fft.rfft(seg[idx] * self.win, axis=1)
            p = spec.real ** 2 + spec.imag ** 2
            out[a - k0:b - k0, :N_BANDS] = (p @ self.bands) / self.wsum
            # the window's mean square (Parseval over the full spectrum: the rfft's inner bins count twice)
            out[a - k0:b - k0, N_BANDS] = (2.0 * p.sum(axis=1) - p[:, 0] - p[:, -1]) / (self.n * self.wsum)
        return out

    def seek(self, pos: int) -> None:
        """The next pushed sample is absolute index ``pos`` (what lies before it reads as silence); frames start
        again at the first whose window begins at or after pos - n/2."""
        self.buf = np.zeros(self.half, np.float32)
        self.buf0 = int(pos) - self.half
        self.k = int(math.ceil(pos / self.hop - 1e-9))

    def push(self, x: np.ndarray) -> tuple[int, np.ndarray]:
        """Append samples; returns (first frame index, the frames now complete)."""
        x = np.asarray(x, np.float32).reshape(-1)
        if x.size:
            self.buf = np.concatenate([self.buf, x])
        end = self.buf0 + self.buf.size
        k_last = int(math.floor((end - self.n + self.half) / self.hop + 1e-9))
        while k_last >= 0 and int(self._start(np.array([k_last]))[0]) + self.n > end:
            k_last -= 1
        k0 = self.k
        if k_last < k0:
            return k0, np.zeros((0, N_BANDS + 1), np.float32)
        out = self.frames(self.buf, self.buf0, k0, k_last + 1)
        self.k = k_last + 1
        keep_from = int(self._start(np.array([self.k]))[0])
        cut = keep_from - self.buf0
        if cut > 0:
            self.buf = self.buf[cut:]
            self.buf0 = keep_from
        return k0, out


class EnvRing:
    """Envelope frames by absolute index, the newest ``cap`` kept, each with a valid flag."""

    def __init__(self, cap: int, width: int = N_BANDS + 1):
        self.cap = int(cap)
        self.p = np.zeros((self.cap, width), np.float32)
        self.ok = np.zeros(self.cap, bool)
        self.end = 0                                        # next frame index
        self.start = 0                                      # oldest held

    def append(self, k0: int, p: np.ndarray, ok: Optional[np.ndarray] = None) -> None:
        n = p.shape[0]
        if n == 0:
            return
        if k0 != self.end:                                  # a jump: what lies between is unknown
            if k0 > self.end:
                gap = min(k0 - self.end, self.cap)
                self._write(k0 - gap, np.zeros((gap, self.p.shape[1]), np.float32), np.zeros(gap, bool))
            else:
                self.end = self.start = k0
        okv = np.ones(n, bool) if ok is None else np.asarray(ok, bool)
        if n > self.cap:
            p, okv, k0 = p[-self.cap:], okv[-self.cap:], k0 + n - self.cap
        self._write(k0, p, okv)

    def _write(self, k0: int, p: np.ndarray, ok: np.ndarray) -> None:
        n = p.shape[0]
        i = np.arange(k0, k0 + n) % self.cap
        self.p[i] = p
        self.ok[i] = ok
        self.end = k0 + n
        self.start = max(self.start, self.end - self.cap)

    def get(self, k0: int, k1: int) -> tuple[np.ndarray, np.ndarray]:
        n = max(0, k1 - k0)
        p = np.zeros((n, self.p.shape[1]), np.float32)
        ok = np.zeros(n, bool)
        a, b = max(k0, self.start), min(k1, self.end)
        if b > a:
            i = np.arange(a, b) % self.cap
            p[a - k0:b - k0] = self.p[i]
            ok[a - k0:b - k0] = self.ok[i]
        return p, ok


def envelope(p: np.ndarray, ok: Optional[np.ndarray] = None) -> tuple[np.ndarray, np.ndarray]:
    """Band powers (T, N_BANDS[+1]) -> the matching feature (T, N_BANDS) float64: each band's power over its own
    median, to the GAMMA (a fixed EQ cancels; loud notes don't swamp the rest), less its DETREND_S running mean, at
    unit variance; frames marked invalid read 0. (Chosen on the 21 Oct drawing-room recording and roomsim: README.)"""
    t = p.shape[0]
    ok = np.ones(t, bool) if ok is None else np.asarray(ok, bool)
    bands = p[:, :N_BANDS].astype(np.float64)
    if not ok.any():
        return np.zeros((t, N_BANDS)), ok
    med = np.median(bands[ok], axis=0)
    c = (bands / np.maximum(med, 1e-20)) ** GAMMA
    c[~ok] = 0.0
    w = max(1, int(round(DETREND_S * ENV_HZ)))
    cs = np.vstack([np.zeros((1, N_BANDS)), np.cumsum(c, axis=0)])
    cn = np.concatenate([[0.0], np.cumsum(ok.astype(np.float64))])
    lo = np.clip(np.arange(t) - w // 2, 0, t)
    hi = np.clip(np.arange(t) + w // 2 + 1, 0, t)
    mean = (cs[hi] - cs[lo]) / np.maximum(cn[hi] - cn[lo], 1.0)[:, None]
    f = np.where(ok[:, None], c - mean, 0.0)
    sd = np.std(f[ok], axis=0)
    f /= np.where(sd > 0, sd, 1.0)
    return f, ok


def music_throughout(p: np.ndarray, ok: Optional[np.ndarray] = None) -> float:
    """The share of 100 ms blocks of a window within MUSIC_RANGE_DB of its loud blocks (its 95th percentile): a
    window that runs into silence (a piece's start or end, a long rest) times nothing well."""
    ms = p[:, N_BANDS].astype(np.float64)
    if ok is not None:
        ms = np.where(ok, ms, np.nan)
    n = (len(ms) // 100) * 100
    if n == 0:
        return 0.0
    blk = np.nanmean(ms[:n].reshape(-1, 100), axis=1)
    blk = 10 * np.log10(np.maximum(np.nan_to_num(blk, nan=0.0), 1e-30))
    return float(np.mean(blk > np.percentile(blk, 95) - MUSIC_RANGE_DB))


# ---------------------------------------------------------------------------------------------------- correlation

class Match(dict):
    """One window's result: lag_s (mic time - expected time of the peak), r, psr, z, coverage, clear, why."""

    @property
    def clear(self) -> bool:
        return bool(self.get("clear"))


def match(ref: np.ndarray, ref_ok: np.ndarray, mic: np.ndarray, mic_ok: np.ndarray, *,
          psr_min: float = PSR_MIN, z_min: float = Z_MIN) -> Match:
    """Cross-correlation of a reference feature (W frames, N_BANDS) against the mic's (W + 2S frames): the bands'
    cross-spectra summed and partly whitened (divided by |X|^WHITEN: periodic music's beat lines no longer dominate,
    so the sidelobes a beat apart drop), then the peak lag L in frames (0..2S, fractional) where mic[L + j] best
    matches ref[j], with its peak/sidelobe ratio, z and the mic's coverage there. r is on a robust scale (the
    correlation's own spread), only its shape counts."""
    w, m = ref.shape[0], mic.shape[0]
    lags = m - w + 1
    if w < 100 or lags < 3:
        return Match(clear=False, why="window too short")
    ref = np.where(np.asarray(ref_ok)[:, None], ref, 0.0)
    mic = np.where(np.asarray(mic_ok)[:, None], mic, 0.0)
    if not np.any(ref):
        return Match(clear=False, why="nothing in what was sent")
    nfft = 1 << int(math.ceil(math.log2(m + w)))
    x = np.sum(np.fft.rfft(mic, nfft, axis=0) * np.conj(np.fft.rfft(ref, nfft, axis=0)), axis=1)
    if WHITEN:
        x = x / (np.abs(x) ** WHITEN + 1e-12)
    num = np.fft.irfft(x, nfft)[:lags]
    cv = np.concatenate([[0.0], np.cumsum(np.asarray(mic_ok, np.float64))])
    cov = (cv[w:w + lags] - cv[:lags]) / w
    use = cov >= COVERAGE_MIN
    if not use.any():
        return Match(clear=False, why="the mic has too many gaps", coverage=round(float(cov.max()), 3))
    r = np.full(lags, np.nan)
    sd = float(np.std(num[use]))
    r[use] = num[use] / (sd if sd > 0 else 1.0)
    i = int(np.nanargmax(r))
    pk = float(r[i])
    frac = 0.0
    if 0 < i < lags - 1 and np.isfinite(r[i - 1]) and np.isfinite(r[i + 1]):
        den = r[i - 1] - 2 * pk + r[i + 1]
        if den < 0:
            frac = float(np.clip(0.5 * (r[i - 1] - r[i + 1]) / den, -0.5, 0.5))
    lobe = int(round(MAIN_LOBE_S * ENV_HZ))
    off = np.ones(lags, bool)
    off[max(0, i - lobe):i + lobe + 1] = False
    rest = r[off & np.isfinite(r)]
    if rest.size < 10:
        return Match(clear=False, why="search range too small", r=round(pk, 3))
    side = float(np.max(rest))
    med = float(np.median(rest))
    mad = float(np.median(np.abs(rest - med))) * 1.4826
    psr = pk / side if side > 1e-9 else float("inf")
    z = (pk - med) / mad if mad > 1e-12 else float("inf")
    out = Match(lag=i + frac, r=round(pk, 4), psr=round(psr, 3), z=round(z, 2), coverage=round(float(cov[i]), 3))
    if pk <= 0:
        out.update(clear=False, why="no correlation")
    elif psr < psr_min:
        out.update(clear=False, why=f"no clear match (peak/sidelobe {psr:.2f}, needs {psr_min:.2f})")
    elif z < z_min:
        out.update(clear=False, why=f"no clear match (z {z:.1f}, needs {z_min:.0f})")
    else:
        out.update(clear=True, why="clear")
    return out


def db(p: float) -> float:
    return 10.0 * math.log10(p) if p > 1e-30 else -300.0


# ---------------------------------------------------------------------------------------------------- references

class StreamReference:
    """What the sink was given, block by block: ``sent(pos, data)`` (pos = MIDI frame of data[0]; stereo or mono
    float). Cheap in the caller's thread; the envelope is computed by ``bands`` (the calibrator's thread)."""

    def __init__(self, fs: int = 48000, ring_s: float = RING_S):
        self.fs = int(fs)
        self.bp = BandPower(self.fs)
        self.ring = EnvRing(int(ring_s * ENV_HZ))
        self._lock = threading.Lock()
        self._pending: list = []
        self._next = 0                                      # the sample position the next block should start at

    def sent(self, pos: int, data: np.ndarray) -> None:
        x = np.asarray(data, np.float32)
        mono = x.mean(axis=1) if x.ndim == 2 else x.reshape(-1)
        with self._lock:
            self._pending.append((int(pos), mono.astype(np.float32, copy=True)))

    def _drain(self) -> None:
        with self._lock:
            items, self._pending = self._pending, []
        for pos, mono in items:
            if pos < self._next:                            # a restart from earlier: begin again there
                self.ring = EnvRing(self.ring.cap)
                self.bp.seek(pos)
            elif pos > self._next:                          # a hole: silence, as the sink plays it
                if pos - self._next <= self.fs:
                    k0, p = self.bp.push(np.zeros(pos - self._next, np.float32))
                    self.ring.append(k0, p)
                else:
                    self.bp.seek(pos)
            k0, p = self.bp.push(mono)
            self.ring.append(k0, p)
            self._next = pos + mono.size

    def end_frame(self) -> int:
        self._drain()
        return self.ring.end

    def bands(self, k0: int, k1: int) -> tuple[np.ndarray, np.ndarray]:
        self._drain()
        return self.ring.get(k0, k1)


class _PieceReference:
    """A whole piece known up front: frames computed as the windows ask for them, each once (the newest RING_S
    kept). Subclasses give ``frames_total`` and ``_samples(a, b)`` (mono float32, a..b within the piece)."""

    fs: int
    frames_total: int

    def _setup(self, fs: int, ring_s: float) -> None:
        self.fs = int(fs)
        self.bp = BandPower(self.fs)
        self.ring = EnvRing(int(ring_s * ENV_HZ))

    def _samples(self, a: int, b: int) -> np.ndarray:
        raise NotImplementedError

    def end_frame(self) -> int:
        return int(self.frames_total / self.bp.hop) + 1

    def bands(self, k0: int, k1: int) -> tuple[np.ndarray, np.ndarray]:
        k1 = min(k1, self.end_frame())
        if k1 <= k0:
            return np.zeros((0, N_BANDS + 1), np.float32), np.zeros(0, bool)
        if not (self.ring.start <= k0 and k1 <= self.ring.end):
            a = max(k0, self.ring.end) if self.ring.start <= k0 <= self.ring.end else k0
            self._compute(a, k1)
        return self.ring.get(k0, k1)

    def _compute(self, k0: int, k1: int) -> None:
        s0 = int(round(k0 * self.bp.hop)) - self.bp.half
        s1 = int(round((k1 - 1) * self.bp.hop)) + self.bp.half + 1
        a, b = max(0, s0), min(self.frames_total, s1)
        x = np.zeros(max(0, s1 - s0), np.float32)
        if b > a:
            y = self._samples(a, b)
            x[a - s0:a - s0 + y.shape[0]] = y
        p = self.bp.frames(x, s0, k0, k1)
        if k0 != self.ring.end:
            self.ring = EnvRing(self.ring.cap)
            self.ring.end = self.ring.start = k0
        self.ring.append(k0, p)


class ArrayReference(_PieceReference):
    """A whole rendered piece in memory ((n, 2) or (n,) float at fs; sample 0 = MIDI time 0)."""

    def __init__(self, audio: np.ndarray, fs: int = 48000, ring_s: float = RING_S):
        x = np.asarray(audio, np.float32)
        self.x = x.mean(axis=1) if x.ndim == 2 else x.reshape(-1)
        self.frames_total = int(self.x.size)
        self._setup(fs, ring_s)

    def _samples(self, a: int, b: int) -> np.ndarray:
        return self.x[a:b]


class FileReference(_PieceReference):
    """A rendered file (FLAC/WAV; sample 0 = MIDI time 0), read in pieces as the windows need them (never the whole
    file in memory)."""

    def __init__(self, path: "str | os.PathLike", ring_s: float = RING_S):
        import soundfile as sf
        self.path = str(path)
        info = sf.info(self.path)
        self.frames_total = int(info.frames)
        self._setup(info.samplerate, ring_s)

    def _samples(self, a: int, b: int) -> np.ndarray:
        import soundfile as sf
        with sf.SoundFile(self.path) as f:
            f.seek(a)
            y = f.read(b - a, dtype="float32", always_2d=True)
        return y.mean(axis=1)


# ---------------------------------------------------------------------------------------------------- mic tap

def tap_shift(prev: np.ndarray, x: np.ndarray, est: int, packet: int = PACKET_DEFAULT) -> Optional[int]:
    """How many new samples frame ``x`` carries after ``prev`` (both the newest WAVE_N samples of the ring): the shift
    k at which x's head equals prev's tail exactly over at least TAP_MIN_OVERLAP samples, nearest the counter's
    ``est`` of several; 0 if nothing is new, None if no shift fits (a splice or a gap). After
    tools/room/audio_cal/calibrate.py's tap_shift. The counter's guess and its whole-packet neighbours (the "race":
    the wave is read a moment after the counter) are tried first, nearest first; then a content search."""
    n = len(x)
    lim = n - TAP_MIN_OVERLAP
    for j in (0, 1, -1, 2, -2, 3, -3):
        k = est + j * packet
        if 0 < k <= lim and np.array_equal(x[:n - k], prev[k:]):
            return k
    if np.array_equal(x, prev):
        return 0
    sig = prev[-32:]
    win = np.lib.stride_tricks.sliding_window_view(x[:lim + 32], 32)
    pos = np.flatnonzero((win == sig).all(axis=1))
    ks = sorted((n - 32 - int(p) for p in pos), key=lambda k: abs(k - est))
    for k in ks[:64]:                   # a quiet mic (mostly 0 and +-1 LSB) matches the signature in many places
        if 0 < k <= lim and np.array_equal(x[:n - k], prev[k:]):
            return k
    return None


class MicTap:
    """The levels feed's ?wave frames of one stream -> a continuous sample stream (content index from 0 at the
    tap's start) with its envelope, validity, level and the host-time map (module docstring, step 1)."""

    def __init__(self, stream: str, fs: int = 48000, packet: int = PACKET_DEFAULT, ring_s: float = RING_S):
        self.stream = stream
        self.fs = int(fs)
        self.packet = int(packet)
        self.ring_cap = int(ring_s * ENV_HZ)
        self.stats = collections.Counter()
        self.reset("start")

    def reset(self, why: str) -> None:
        self.bp = BandPower(self.fs)
        self.ring = EnvRing(self.ring_cap)
        self.prev: Optional[np.ndarray] = None
        self.prev_c: Optional[int] = None
        self.c_unw = 0
        self.n = 0                                           # samples written (content index of the next)
        self._flushed = 0
        self.pending: list = []
        self.holes: collections.deque = collections.deque()  # (s0, s1) content ranges filled with zeros
        self.map: collections.deque = collections.deque()    # (n_end, c_unwrapped, a)
        self.base: Optional[int] = None                      # min(n_end - packet * c): content end of packet c
        self.last_frame_t: Optional[float] = None
        self.segment_why = why
        self.stats["resets"] += 1

    # -- frames --
    def feed(self, f: dict, now: float) -> bool:
        """One decoded levels frame; True if it was our stream's and carried samples."""
        if "wb" not in f:
            return False
        if f.get("s") != self.stream:
            self.stats["other_stream"] += 1
            if self.prev is not None:
                self.reset(f"the feed moved to {f.get('s')}")
            return False
        try:
            x = np.frombuffer(base64.b64decode(f["wb"]), "<i2")
            c = int(f["c"])
            a = float(f["a"])
        except (KeyError, TypeError, ValueError):
            self.stats["bad"] += 1
            return False
        if x.size != WAVE_N:
            self.stats["bad"] += 1
            return False
        if self.prev is not None and self.last_frame_t is not None and now - self.last_frame_t > GAP_FILL_MAX_S:
            self.reset("the feed stopped for a while")
        dc = None if self.prev is None else (c - self.prev_c) % (1 << 32)
        if dc is not None and dc > (1 << 31):
            self.reset("the counter went back")
            dc = None
        if dc is not None and dc * self.packet - WAVE_N > GAP_FILL_MAX_S * self.fs:
            self.reset("a long gap in the feed")
            dc = None
        if dc is None:                                      # the first frame of a tap segment: all of it is new
            self._write(x)
            self.c_unw = c
        else:
            est = dc * self.packet
            k = tap_shift(self.prev, x, est, self.packet)
            if k is not None:
                self.stats["race"] += int(k != est)
                if k:
                    self._write(x[WAVE_N - k:])
            elif est <= WAVE_N - TAP_MIN_OVERLAP:          # should overlap but doesn't (a receiver splice):
                self._write(x[WAVE_N - max(est, 0):])      # trust the counter's shift
                self.stats["unmatched"] += 1
            else:                                           # frames dropped: zeros for what the counter says is gone
                miss = est - WAVE_N
                self.stats["gaps"] += 1
                self.stats["gap_samples"] += miss
                if miss:
                    self.holes.append((self.n, self.n + miss))
                    self._write(np.zeros(miss, np.int16))
                self._write(x)
            self.c_unw += dc
        self.prev, self.prev_c = x, c
        self.last_frame_t = now
        self.stats["frames"] += 1
        d = self.n - self.packet * self.c_unw
        self.base = d if self.base is None else min(self.base, d)
        self.map.append((self.n, self.c_unw, a))
        while self.map and self.map[0][0] < self.n - MAP_KEEP_S * self.fs:
            self.map.popleft()
        if self.n - self._flushed >= self.fs // 4:           # the envelope in 0.25 s batches (fewer, larger FFTs)
            self.flush()
        return True

    def _write(self, x: np.ndarray) -> None:
        if x.size:
            self.pending.append(np.asarray(x, np.int16))
            self.n += int(x.size)

    def flush(self) -> None:
        if not self.pending:
            return
        y = np.concatenate(self.pending).astype(np.float32) / 32767.0
        self.pending = []
        self._flushed = self.n
        k0, p = self.bp.push(y)
        if p.shape[0]:
            ok = np.ones(p.shape[0], bool)
            if self.holes:
                centres = np.rint(np.arange(k0, k0 + p.shape[0]) * self.bp.hop)
                for s0, s1 in self.holes:
                    ok &= ~((centres + self.bp.half > s0) & (centres - self.bp.half < s1))
                lim = centres[0] - self.bp.n if centres.size else 0
                while self.holes and self.holes[0][1] < lim:
                    self.holes.popleft()
            self.ring.append(k0, p, ok)

    # -- host time --
    def arrival_offset(self, n_lo: Optional[int] = None, n_hi: Optional[int] = None) -> Optional[float]:
        """A = the host time of content sample 0 at its arrival (lower envelope of a - sample/fs over the map points
        with n_end in [n_lo, n_hi]; all of them when None)."""
        if not self.map or self.base is None:
            return None
        m = np.asarray(self.map, np.float64)
        sel = np.ones(m.shape[0], bool)
        if n_lo is not None:
            sel &= m[:, 0] >= n_lo
        if n_hi is not None:
            sel &= m[:, 0] <= n_hi
        if sel.sum() < 5:
            sel[:] = True
        return float(np.min(m[sel, 2] - (self.packet * m[sel, 1] + self.base) / self.fs))

    def arrival_lateness(self) -> np.ndarray:
        """Each map point's arrival after the lower envelope, s (the feed's jitter as this tap sees it)."""
        a = self.arrival_offset()
        if a is None:
            return np.zeros(0)
        m = np.asarray(self.map, np.float64)
        return m[:, 2] - (self.packet * m[:, 1] + self.base) / self.fs - a

    def frame_time(self, k: float, mic_lat_s: float, n_lo: Optional[int] = None,
                   n_hi: Optional[int] = None) -> Optional[float]:
        """Host time at which envelope frame k's centre reached the mic (see the module docstring)."""
        a = self.arrival_offset(n_lo, n_hi)
        return None if a is None else a + k * self.bp.hop / self.fs - mic_lat_s

    def level_dbfs(self, seconds: float = 1.0) -> Optional[float]:
        self.flush()
        k1 = self.ring.end
        p, ok = self.ring.get(k1 - int(seconds * ENV_HZ), k1)
        if not ok.any():
            return None
        return round(db(float(np.mean(p[ok, N_BANDS]))), 1)


# ---------------------------------------------------------------------------------------------------- websocket

class WSClient:
    """A minimal RFC 6455 client (stdlib only: the piano venv has no aiohttp): text messages in, pings answered,
    nothing sent but pongs and the close. ``recv(timeout)`` -> str | None (timeout); ConnectionError when closed."""

    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    MAX_MSG = 1 << 22

    def __init__(self, url: str, timeout: float = 3.0):
        u = urllib.parse.urlsplit(url)
        if u.scheme != "ws":
            raise ValueError(f"{url}: ws:// only")
        self.host, self.port = u.hostname or "127.0.0.1", u.port or 80
        self.path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.buf = bytearray()
        self._frag: list = []
        self._frag_op = 0

    def connect(self) -> "WSClient":
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {self.path} HTTP/1.1\r\nHost: {self.host}:{self.port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        s.sendall(req.encode())
        head = bytearray()
        while b"\r\n\r\n" not in head:
            chunk = s.recv(4096)
            if not chunk:
                s.close()
                raise ConnectionError("closed during the handshake")
            head += chunk
            if len(head) > 65536:
                s.close()
                raise ConnectionError("handshake too long")
        hdr, _, rest = bytes(head).partition(b"\r\n\r\n")
        lines = hdr.decode("latin-1").split("\r\n")
        if len(lines[0].split()) < 2 or lines[0].split()[1] != "101":
            s.close()
            raise ConnectionError(f"not a websocket: {lines[0]!r}")
        fields = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
        want = base64.b64encode(hashlib.sha1((key + self.GUID).encode()).digest()).decode()
        if fields.get("sec-websocket-accept") != want:
            s.close()
            raise ConnectionError("bad Sec-WebSocket-Accept")
        self.sock = s
        self.buf = bytearray(rest)
        return self

    def _send(self, op: int, payload: bytes = b"") -> None:
        if self.sock is None:
            return
        n = len(payload)
        head = bytearray([0x80 | op])
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += n.to_bytes(2, "big")
        else:
            head.append(0x80 | 127)
            head += n.to_bytes(8, "big")
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(head) + mask + body)

    def _frame(self) -> Optional[tuple[bool, int, bytes]]:
        b = self.buf
        if len(b) < 2:
            return None
        fin, op, masked, ln = bool(b[0] & 0x80), b[0] & 0x0F, bool(b[1] & 0x80), b[1] & 0x7F
        i = 2
        if ln == 126:
            if len(b) < 4:
                return None
            ln, i = int.from_bytes(b[2:4], "big"), 4
        elif ln == 127:
            if len(b) < 10:
                return None
            ln, i = int.from_bytes(b[2:10], "big"), 10
        if ln > self.MAX_MSG:
            raise ConnectionError(f"frame of {ln} bytes")
        mask = None
        if masked:
            if len(b) < i + 4:
                return None
            mask, i = bytes(b[i:i + 4]), i + 4
        if len(b) < i + ln:
            return None
        payload = bytes(b[i:i + ln])
        del b[:i + ln]
        if mask:
            arr = np.frombuffer(payload, np.uint8) ^ np.resize(np.frombuffer(mask, np.uint8), ln)
            payload = arr.tobytes()
        return fin, op, payload

    def recv(self, timeout: float) -> Optional[str]:
        if self.sock is None:
            raise ConnectionError("not connected")
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            fr = self._frame()
            if fr is not None:
                fin, op, payload = fr
                if op == 0x9:                               # ping
                    self._send(0xA, payload)
                    continue
                if op == 0xA:
                    continue
                if op == 0x8:
                    try:
                        self._send(0x8, payload[:2])
                    except OSError:
                        pass
                    self.close()
                    raise ConnectionError("the server closed the feed")
                if op in (0x1, 0x2):
                    self._frag, self._frag_op = [payload], op
                elif op == 0x0:
                    self._frag.append(payload)
                else:
                    continue
                if fin:
                    data, kind = b"".join(self._frag), self._frag_op
                    self._frag = []
                    if kind == 0x1:
                        return data.decode("utf-8", "replace")
                    continue
                continue
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            s = self.sock
            if s is None:
                raise ConnectionError("closed")
            s.settimeout(left)
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                return None
            if not chunk:
                self.close()
                raise ConnectionError("the feed closed")
            self.buf += chunk

    def close(self) -> None:
        s, self.sock = self.sock, None
        if s is not None:
            try:
                s.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------------------------------- persistence

def lat_file() -> Path:
    env = os.environ.get(LAT_FILE_ENV)
    return Path(env).expanduser() if env else DEFAULT_LAT_FILE


def _utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def load_latency(path: "Path | None" = None) -> dict:
    p = Path(path) if path else lat_file()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def prior_delay(key: str, path: "Path | None" = None) -> Optional[float]:
    """The output's delay in seconds (median of its last KEEP_N good measurements), or None."""
    o = (load_latency(path).get("outputs") or {}).get(key) or {}
    vals = [float(m["delay_ms"]) for m in o.get("measurements") or []
            if isinstance(m, dict) and isinstance(m.get("delay_ms"), (int, float)) and math.isfinite(m["delay_ms"])
            and abs(m["delay_ms"]) <= DELAY_LIMIT_S * 1000]
    return statistics.median(vals) / 1000.0 if vals else None


def record_delay(key: str, delay_s: float, meta: Optional[dict] = None, path: "Path | None" = None,
                 keep: int = KEEP_N) -> dict:
    """Append one measurement (atomically, under a lock file); returns the output's entry."""
    import fcntl
    p = Path(path) if path else lat_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_name(p.name + ".lock"), "a+") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        d = load_latency(p)
        d.setdefault("_comment", "piano_render/calibrate.py: each output's delay after the sink's own report of when "
                                 "MIDI time 0 is heard, measured with the room's mic; the prior is the median")
        d["v"] = 1
        outs = d.setdefault("outputs", {})
        o = outs.setdefault(key, {})
        ms = [m for m in (o.get("measurements") or []) if isinstance(m, dict)]
        ms.append({"delay_ms": round(delay_s * 1000.0, 2), "t_utc": _utc(), **(meta or {})})
        ms = ms[-keep:]
        vals = [float(m["delay_ms"]) for m in ms if isinstance(m.get("delay_ms"), (int, float))]
        o.update(measurements=ms, delay_ms=round(statistics.median(vals), 2) if vals else None, n=len(vals),
                 updated_utc=_utc())
        tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
        os.replace(tmp, p)
        return dict(o)


def output_key(output: str, device: Optional[str] = None) -> str:
    """"pc22" / "pc22:<device>" / "sonos:<zone>" -> the persisted key ("pc22:default" when no device is named)."""
    if output.startswith("sonos:"):
        return output
    if output == "pc22" or output.startswith("pc22:"):
        dev = device or (output[5:] if output.startswith("pc22:") else "") or "default"
        return f"pc22:{dev}"
    return output


def output_kind(output: str) -> str:
    return "sonos" if output.startswith("sonos:") else "pc22"


# ---------------------------------------------------------------------------------------------------- show_service

def _get_json(url: str, timeout: float = 2.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def room_acoustic_offset_ms(stream: str, path: Path = ROOM_LATENCY_FILE) -> float:
    try:
        v = ((json.loads(path.read_text(encoding="utf-8")).get("sources") or {}).get(stream) or {}) \
            .get("acoustic_offset_ms")
    except (OSError, ValueError, AttributeError):
        return 0.0
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else 0.0


def show_info(mic: str, show_url: str = SHOW_URL, get: Callable[[str], Any] = _get_json) -> dict:
    """What show_service says about the mic (read only): {ok, why, active, active_label, label, state, mic_on,
    mic_why, fs, packet, in_lat_ms, offset_ms}."""
    out: dict = {"ok": False, "mic": mic}
    try:
        inp = get(show_url + "/api/v1/inputs")
        st = get(show_url + "/api/v1/state")
    except Exception as e:                                  # noqa: BLE001
        out["why"] = f"can't reach show_service ({type(e).__name__})"
        return out
    labels = {i.get("stream"): i.get("label") or i.get("stream") for i in inp.get("inputs") or []
              if isinstance(i, dict)}
    states = {i.get("stream"): i.get("state") for i in inp.get("inputs") or [] if isinstance(i, dict)}
    sm = ((st.get("streams") or {}).get(mic) or {}) if isinstance(st, dict) else {}
    fmt = sm.get("format") or {}
    snd = sm.get("sender") or {}
    m = (st.get("mic") or {}) if isinstance(st, dict) else {}
    active = inp.get("active")
    out.update(ok=True, active=active, active_label=labels.get(active, active), label=labels.get(mic, mic),
               state=states.get(mic, sm.get("state")), mic_on=bool(m.get("on", True)), mic_why=m.get("why"),
               mic_off=mic in ((m.get("streams") or {}).get("off") or []),
               fs=int(fmt.get("sr") or 48000), packet=int(fmt.get("frames") or PACKET_DEFAULT),
               channels=int(fmt.get("channels") or 1),
               in_lat_ms=float(snd.get("in_lat_ms") or 0.0), offset_ms=room_acoustic_offset_ms(mic))
    if mic not in labels and not sm:
        out.update(ok=False, why=f"show_service has no input {mic!r}")
    return out


def levels_url(show_url: str, mic: Optional[str]) -> str:
    """The levels feed for ``mic`` by name (``?stream=``; an older feed ignores it and sends the active input)."""
    url = show_url.replace("http://", "ws://", 1) + LEVELS_PATH + f"?wave={WAVE_N}"
    return url + f"&stream={urllib.parse.quote(mic, safe='')}" if mic else url


def mic_latency_s(info: dict) -> float:
    """Seconds from a sample's ADC time to the lower envelope of its arrivals, less the acoustic offset."""
    packet_s = int(info.get("packet") or PACKET_DEFAULT) / float(info.get("fs") or 48000)
    in_lat = max(float(info.get("in_lat_ms") or 0.0) / 1000.0, packet_s)
    return in_lat - packet_s + TRANSPORT_S - float(info.get("offset_ms") or 0.0) / 1000.0


# ---------------------------------------------------------------------------------------------------- calibrator

class Calibrator:
    """One output's live calibration (module docstring). Thread-safe: begin/end/sink_rebased from the session,
    ``sent`` on the reference from the audio thread, the tap and control loop on the calibrator's own threads
    (``connect=False`` for tests: drive ``feed_frame``, ``set_show`` and ``step`` yourself)."""

    def __init__(self, output: str, *, emit: Optional[Callable[[dict], Any]] = None, mic: Optional[str] = None,
                 device: Optional[str] = None, show_url: Optional[str] = None, lat_path: "Path | None" = None,
                 clock: Callable[[], float] = time.monotonic, persist: bool = True, connect: bool = True,
                 get: Callable[[str], Any] = _get_json):
        self.output = output
        self.kind = output_kind(output)
        self.search_s = SEARCH_WIDE_S if self.kind == "sonos" else SEARCH_S
        self.first_jump = self.kind == "sonos"        # the first lock's correction in one step (module docstring)
        self.key = output_key(output, device)
        if mic is None:
            mic = os.environ.get(MIC_ENV) or MIC_FOR[self.kind]
        self.mic: Optional[str] = mic.strip() or None       # "": show_service named no mic that hears this output
        self.no_mic_why = None if self.mic else f"show_service names no mic that hears {output}"
        self.feed_named: Optional[bool] = None              # the feed honours ?stream (True), ignores it (False)
        self.emit_fn = emit
        self.show_url = (show_url or os.environ.get(SHOW_URL_ENV) or SHOW_URL).rstrip("/")
        self.lat_path = lat_path
        self.clock = clock
        self.persist = persist
        self.connect = connect
        self.get = get
        self.lock = threading.RLock()
        self.info: dict = {"ok": False, "why": "not asked yet"}
        self.tap = MicTap(self.mic or "")
        self.playing = False
        self.ref: Any = None
        self.sink_t0 = 0.0
        self.delay = 0.0
        self.prior: Optional[float] = None
        self.announced = 0.0
        self.target: Optional[float] = None                 # the announced value a slew is heading for
        self.locked = False
        self.results: list = []
        self.last_window_t: Optional[float] = None
        self.last_accept_t: Optional[float] = None
        self.last_slew_t = 0.0
        self.measured: Optional[float] = None
        self.measurements = 0
        self.heard_s: Optional[float] = None               # how far into the piece the mic has heard
        self.state, self.why = "idle", "not playing"
        self._status_sent: Optional[tuple] = None
        self._status_t = -1e9
        self.windows = 0
        self.cpu_s = 0.0
        self._stop = threading.Event()
        self._poll_now = threading.Event()
        self._threads: list = []
        self._ws: Optional[WSClient] = None
        self.ws_error: Optional[str] = None

    # -- the session's side --
    def begin(self, sink_t0: float, reference: Any) -> float:
        """A play starts: the sink says MIDI time 0 is heard at sink_t0; returns the t_audio0 to announce."""
        with self.lock:
            self.prior = prior_delay(self.key, self.lat_path)
            self.delay = self.prior or 0.0
            self.sink_t0 = float(sink_t0)
            self.announced = self.sink_t0 + self.delay
            self.target = None
            self.ref = reference
            self.locked = False
            self.results = []
            self.last_window_t = self.last_accept_t = None
            self.measured = None
            self.heard_s = None
            self.playing = True
            self.tap.reset("a new play")
            log.info("calibrate %s: sink t0 %.3f, prior delay %s, mic %s", self.key, self.sink_t0,
                     "none" if self.prior is None else f"{self.prior * 1000:.1f} ms", self.mic or self.no_mic_why)
            if self.mic is None:                            # nothing to hear it with: the prior (or the sink) stands
                self._set_status("off", f"{self.no_mic_why}: no mic check; {self._keeping()}", force=True)
                return self.announced
            if self.connect:
                self.info = {"ok": False, "pending": True}   # until the poll thread's first answer
            self._set_status("listening", f"waiting for the music to reach the {self.mic} mic")
        if self.connect:
            self._start_threads()
            self._poll_now.set()                            # ask show_service now, not at the next poll
        return self.announced

    def sink_rebased(self, sink_t0: float) -> float:
        """The sink's own estimate moved (Sonos RelTime): t_audio0 moves with it, the delay stays; emits the rebase."""
        with self.lock:
            was = self.announced
            self.sink_t0 = float(sink_t0)
            self.target = None
            self.announced = self.sink_t0 + self.delay
            # sonos.py rebases only past REBASE_S (0.7 s): the timeline most likely changed. Check it at once: the
            # windows so far heard the old one, and the next measurement needs the full AGREE_N.
            self.results = []
            self.measured = None
            self.last_accept_t = None
            self._emit({"ev": "rebase", "t_audio0": self.announced, "was": was, "by": "sink",
                        "delay_ms": round(self.delay * 1000, 1)})
            return self.announced

    def sink_emit(self, emit: Callable[[dict], Any]) -> Callable[[dict], Any]:
        """Wrap a sink's emit (sonos.SonosOutput's): its rebases go through ``sink_rebased``, the rest through."""
        def f(msg: dict) -> Any:
            if isinstance(msg, dict) and msg.get("ev") == "rebase" and isinstance(msg.get("t_audio0"), (int, float)):
                return self.sink_rebased(float(msg["t_audio0"]))
            return emit(msg)
        return f

    def end(self) -> None:
        """The play ended (the tap thread lets go of the feed within STEP_S: no client, no load on show_service)."""
        with self.lock:
            if self.playing:
                self._set_status("idle", "not playing", force=True)
            self.playing = False
            self.ref = None

    def close(self) -> None:
        self.end()
        self._stop.set()
        self._poll_now.set()
        for t in self._threads:
            t.join(timeout=2.0)
        self._threads = []
        self._drop_ws()

    def status(self) -> dict:
        with self.lock:
            return {"state": self.state, "why": self.why, "output": self.key, "mic": self.mic,
                    "feed": self._feed_word(),
                    "delay_ms": round(self.delay * 1000, 1), "prior_ms": None if self.prior is None
                    else round(self.prior * 1000, 1), "measured_ms": None if self.measured is None
                    else round((self.measured - self.sink_t0) * 1000, 1), "locked": self.locked,
                    "t_audio0": self.announced, "windows": self.windows, "heard_s": None if self.heard_s is None
                    else round(self.heard_s, 1), "cpu_s": round(self.cpu_s, 2),
                    "level_dbfs": self.tap.level_dbfs() if self.tap.ring.end else None,
                    "tap": dict(self.tap.stats)}

    # -- inputs (threads, or tests) --
    def set_show(self, info: dict) -> None:
        with self.lock:
            self.info = dict(info)
            fs, pk = int(info.get("fs") or 48000), int(info.get("packet") or PACKET_DEFAULT)
            if info.get("ok") and (fs != self.tap.fs or pk != self.tap.packet):
                self.tap = MicTap(self.mic or "", fs=fs, packet=pk)

    def feed_frame(self, f: dict, now: float) -> None:
        with self.lock:
            if not self.playing:
                return
            s = f.get("s")
            if self.mic and "wb" in f and isinstance(s, str):
                if s != self.mic:
                    if self.feed_named is not False:        # asked for the mic by name, got another stream
                        log.info("calibrate %s: the levels feed sent %s for ?stream=%s: it carries the active input "
                                 "only", self.key, s, self.mic)
                    self.feed_named = False
                elif self.feed_named is None and self.info.get("ok") and self.info.get("active") not in (None, s):
                    self.feed_named = True                  # our mic's frames while another input is active
            self.tap.feed(f, now)

    def new_feed(self) -> None:
        """A new levels connection: whether it honours ?stream is learnt again from its frames."""
        with self.lock:
            self.feed_named = None

    def _feed_word(self) -> Optional[str]:
        return {True: "by name", False: "active only"}.get(self.feed_named) if self.mic else None

    # -- the control loop --
    def step(self, now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        with self.lock:
            if not self.playing:
                return
            self._slew(now)
            if not self._gate(now):
                return
            if self.last_window_t is not None and now - self.last_window_t < HOP_S:
                return
            if self.locked and self.last_accept_t is not None and \
                    now - self.last_accept_t < RECHECK_S - (AGREE_N + 1) * HOP_S:
                return                                      # locked: windows again only for the next re-check
            c0 = time.process_time()
            res = self._window(now)
            self.cpu_s += time.process_time() - c0
            if res is None:
                return
            self.last_window_t = now
            self.windows += 1
            self.results.append(res)
            self.results = self.results[-8:]
            log.debug("calibrate %s window: %s", self.key, dict(res))
            self._decide(now, res)

    def _gate(self, now: float) -> bool:
        i = self.info
        keep = self._keeping()
        if self.mic is None:
            self._set_status("off", f"{self.no_mic_why}: no mic check; {keep}")
            return False
        if i.get("pending"):
            self._set_status("listening", "asking show_service which mic is on")
            return False
        if not i.get("ok"):
            self._set_status("off", f"{i.get('why') or 'no word from show_service'}: no mic check; {keep}")
            return False
        label = i.get("label") or self.mic
        if not i.get("mic_on", True):                       # the mic on demand: nothing has asked for it
            why = i.get("mic_why")
            self._set_status("off", "the mic is off" + (f" ({why})" if why else "") + f": no mic check; {keep}")
            return False
        if i.get("active") != self.mic and self.feed_named is False:     # an older feed: the active input only
            self._set_status("off", f"the room listens on the {i.get('active_label') or i.get('active')} mic, not "
                                    f"the {label} one, and show_service's feed carries only that one (it doesn't "
                                    f"take ?stream): no mic check; {keep}")
            return False
        if i.get("mic_off") or i.get("state") == "off":
            self._set_status("off", f"the {label} mic is off: no mic check; {keep}")
            return False
        if self.ws_error and self.tap.last_frame_t is None:
            self._set_status("off", f"can't read the mic feed ({self.ws_error}): no mic check; {keep}")
            return False
        if self.tap.last_frame_t is None or now - self.tap.last_frame_t > FEED_SILENT_S:
            self._set_status("listening", f"no sound from the {label} mic yet")
            return False
        return True

    def _window(self, now: float) -> Optional[Match]:
        """One correlation window, or None when it isn't time yet."""
        tap, ref = self.tap, self.ref
        tap.flush()
        mic_lat = mic_latency_s(self.info)
        k_end = tap.ring.end
        if k_end < 100:
            return None
        n_hi = tap.n
        search = self.search_s
        n_lo = max(0, tap.n - int((WINDOW_S + 2 * search + 2) * tap.fs))
        t_mic_end = tap.frame_time(k_end - 1, mic_lat, n_lo, n_hi)
        if t_mic_end is None:
            return None
        w = int(WINDOW_S * ENV_HZ)
        s = int(search * ENV_HZ)
        j1 = int(math.floor((t_mic_end - search - self.announced) * ENV_HZ)) - 1
        j1 = min(j1, ref.end_frame())
        j0 = j1 - w
        self.heard_s = t_mic_end - self.announced
        if j0 < 0:
            self._set_status("listening", "waiting for the piece to start" if self.heard_s <= 0 else
                             f"listening (the first check needs {WINDOW_S + search:.0f} s of the piece)")
            return None
        rp, rok = ref.bands(j0, j1)
        ref_db = db(float(np.mean(rp[rok, N_BANDS]))) if rok.any() else -300.0
        a = tap.arrival_offset(n_lo, n_hi)
        # mic frame k is at a + k/ENV_HZ - mic_lat (hop = fs / ENV_HZ): the frame expected for ref frame j0 - s
        kf = (self.announced + (j0 - s) / ENV_HZ + mic_lat - a) * ENV_HZ
        ks = int(math.floor(kf))
        mp, mok = tap.ring.get(ks, ks + w + 2 * s)
        mic_db = db(float(np.mean(mp[mok, N_BANDS]))) if mok.any() else -300.0
        base = Match(t=now, j0=j0, ref_dbfs=round(ref_db, 1), mic_dbfs=round(mic_db, 1))
        mus = music_throughout(rp, rok)
        if ref_db < REF_QUIET_DBFS or mus < MUSIC_MIN:
            base.update(clear=False, why="the piece is quiet here", skip=True, music=round(mus, 2))
            return base
        if mic_db < MIC_QUIET_DBFS:
            base.update(clear=False, why="the mic hears almost nothing" + (f" ({mic_db:.0f} dBFS)" if mic_db > -200
                                                                           else " (digital silence)"))
            return base
        rf, rv = envelope(rp, rok)
        mf, mv = envelope(mp, mok)
        m = match(rf, rv, mf, mv)
        m.update(base)
        if "lag" in m:
            # mic frame ks + lag matches ref frame j0: its host time minus MIDI time j0 is H0
            m["h0"] = a + (ks + m["lag"]) * tap.bp.hop / tap.fs - mic_lat - j0 / ENV_HZ
            m["delay_s"] = m["h0"] - self.sink_t0
        return m

    def _decide(self, now: float, res: Match) -> None:
        if res.get("skip"):
            self._set_status("listening" if not self.locked else "locked",
                             "waiting for louder music" if not self.locked else self._locked_why())
            return
        if not res.clear:
            if not self.locked:
                self._set_status("listening", f"{res.get('why')}: no correction")
            return
        if self.locked and self.last_accept_t is not None and now - self.last_accept_t < RECHECK_S:
            return
        run = []
        for r in reversed(self.results):
            if not r.clear or "h0" not in r:
                break
            run.append(r)
        need = AGREE_N
        if self.locked and self.measured is not None and run and abs(run[0]["h0"] - self.measured) <= RECHECK_NEAR_S:
            need = RECHECK_AGREE_N
        if len(run) < need:
            if not self.locked:
                self._set_status("measuring", f"a clear match ({len(run)} of {need} windows)")
            return
        run = run[:need]
        h = [r["h0"] for r in run]
        med = statistics.median(h)
        if max(abs(x - med) for x in h) > AGREE_S:
            if not self.locked:
                self._set_status("measuring", f"the windows disagree by {1000 * (max(h) - min(h)):.0f} ms")
            return
        self._accept(now, med, run)

    def _accept(self, now: float, h0: float, run: list) -> None:
        self.measured = h0
        self.last_accept_t = now
        self.results = []
        delay = h0 - self.sink_t0
        self.measurements += 1
        self.delay = delay
        target = self.sink_t0 + delay
        d = target - self.announced
        log.info("calibrate %s: heard %+.1f ms from the announced time (delay %.1f ms; r %.2f, psr %.2f, z %.1f)",
                 self.key, d * 1000, delay * 1000, run[0].get("r", 0), run[0].get("psr", 0), run[0].get("z", 0))
        slew = False
        if abs(d) < DEADBAND_S:
            self.target = None
        elif abs(d) <= SLEW_MAX_S:
            self.target = target
            self.last_slew_t = -1e9
            slew = True
        else:
            jump = SEARCH_WIDE_S if self.first_jump else MAX_JUMP_S
            step = max(-jump, min(jump, d))
            was = self.announced
            self.announced += step
            self.target = None
            self._emit({"ev": "rebase", "t_audio0": self.announced, "was": was, "by": "mic",
                        "delay_ms": round(delay * 1000, 1)})
            if step != d:                                    # the rest after the next measurement
                self.delay = self.announced - self.sink_t0
                self.locked = False
                self._set_status("measuring", f"moved {step * 1000:+.0f} ms of {d * 1000:+.0f} ms; measuring again",
                                 force=True)
                self._persist(delay, run)
                return
        self.locked = True
        self.search_s, self.first_jump = SEARCH_S, False   # found: the narrow search and small steps from now on
        self._persist(delay, run)
        self._set_status("locked", self._locked_why(), force=True)
        if slew:
            self._slew(now)                                 # the first step now, the rest every SLEW_EVERY_S

    def _sink_name(self) -> str:
        return "the Sonos's own estimate" if self.kind == "sonos" else "the PC22 player's own estimate"

    def _locked_why(self) -> str:
        ms = self.delay * 1000
        return f"in time: heard {abs(ms):.0f} ms {'after' if ms >= 0 else 'before'} {self._sink_name()}"

    def _keeping(self) -> str:
        if self.locked or self.measured is not None:
            return f"keeping this play's measurement ({self.delay * 1000:+.0f} ms)"
        if self.prior is not None:
            return f"using the last measurement ({self.prior * 1000:+.0f} ms)"
        return f"using {self._sink_name()}"

    def _persist(self, delay: float, run: list) -> None:
        if not self.persist:
            return
        meta = {"mic": self.mic, "windows": len(run), "r": run[0].get("r"), "psr": run[0].get("psr"),
                "z": run[0].get("z"), "spread_ms": round(1000 * (max(r["h0"] for r in run)
                                                                 - min(r["h0"] for r in run)), 2)}
        try:
            record_delay(self.key, delay, meta, self.lat_path)
        except OSError as e:
            log.warning("calibrate %s: couldn't save the delay: %s", self.key, e)

    def _slew(self, now: float) -> None:
        if self.target is None or now - self.last_slew_t < SLEW_EVERY_S:
            return
        d = self.target - self.announced
        if abs(d) < 1e-4:
            self.target = None
            return
        step = max(-SLEW_STEP_S, min(SLEW_STEP_S, d))
        was = self.announced
        self.announced += step
        self.last_slew_t = now
        if abs(self.target - self.announced) < 1e-4:
            self.announced = self.target
            self.target = None
        self._emit({"ev": "rebase", "t_audio0": self.announced, "was": was, "by": "mic",
                    "delay_ms": round(self.delay * 1000, 1)})

    def _emit(self, msg: dict) -> None:
        if self.emit_fn is not None:
            try:
                self.emit_fn(msg)
            except Exception as e:                          # noqa: BLE001  the reader may be gone
                log.warning("calibrate: emit failed: %s", e)

    def _set_status(self, state: str, why: str, force: bool = False) -> None:
        """Keep the status; a "calibration" line goes out on a change (only the words changed: at most every
        STATUS_MIN_S)."""
        now = self.clock()
        self.state, self.why = state, why
        sent = self._status_sent
        if sent == (state, why) and not force:
            return
        if not force and sent is not None and sent[0] == state and now - self._status_t < STATUS_MIN_S:
            return
        self._status_sent, self._status_t = (state, why), now
        log.info("calibrate %s: %s: %s", self.key, state, why)
        self._emit({"ev": "calibration", "state": state, "why": why, "mic": self.mic, "feed": self._feed_word(),
                    "output": self.key, "delay_ms": round(self.delay * 1000, 1), "locked": self.locked})

    # -- threads --
    def _start_threads(self) -> None:
        if self.mic is None:
            return
        if self._threads and all(t.is_alive() for t in self._threads):
            return
        self._stop.clear()
        self._threads = [threading.Thread(target=self._run_tap, name="calibrate-tap", daemon=True),
                         threading.Thread(target=self._run_poll, name="calibrate-poll", daemon=True)]
        for t in self._threads:
            t.start()

    def _drop_ws(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            ws.close()

    def _run_poll(self) -> None:
        while not self._stop.is_set():
            if self.playing:
                self.set_show(show_info(self.mic, self.show_url, self.get))
            self._poll_now.wait(STATE_POLL_S if self.playing else 1.0)
            self._poll_now.clear()

    def _run_tap(self) -> None:
        url = levels_url(self.show_url, self.mic)
        backoff = 1.0
        last_step = 0.0
        while not self._stop.is_set():
            if not self.playing:
                self._drop_ws()
                self._stop.wait(0.2)
                continue
            if self._ws is None:
                try:
                    self._ws = WSClient(url).connect()
                    self.ws_error = None
                    self.new_feed()
                    backoff = 1.0
                except (OSError, ConnectionError, ValueError) as e:
                    self.ws_error = f"{type(e).__name__}: {e}"
                    self.step()
                    self._stop.wait(backoff)
                    backoff = min(10.0, backoff * 2)
                    continue
            try:
                msg = self._ws.recv(STEP_S)
            except (OSError, ConnectionError, ValueError) as e:
                self.ws_error = f"{type(e).__name__}: {e}"
                self._drop_ws()
                with self.lock:
                    self.tap.reset("the feed reconnects")
                continue
            if msg is not None:
                try:
                    f = json.loads(msg)
                except ValueError:
                    f = None
                if isinstance(f, dict):
                    c0 = time.process_time()
                    self.feed_frame(f, self.clock())
                    self.cpu_s += time.process_time() - c0
            now = self.clock()
            if now - last_step >= STEP_S:
                last_step = now
                self.step(now)


# ---------------------------------------------------------------------------------------------------- offline

def measure_offline(ref_audio: np.ndarray, ref_fs: int, rec: np.ndarray, rec_fs: int, *,
                    window_s: float = WINDOW_S, hop_s: float = HOP_S, search_s: float = SEARCH_S,
                    rec_offset_s: float = 0.0, start_s: float = 0.0, end_s: Optional[float] = None) -> list[Match]:
    """Per-window matches of a recording against what was played: lag_s = when the recording hears reference time
    t, minus (t + rec_offset_s) (rec_offset_s: the expected start of the reference in the recording)."""
    rp = BandPower(ref_fs)
    mp = BandPower(rec_fs)
    x = np.asarray(ref_audio, np.float32)
    x = x.mean(axis=1) if x.ndim == 2 else x
    y = np.asarray(rec, np.float32)
    y = y.mean(axis=1) if y.ndim == 2 else y
    w, s = int(window_s * ENV_HZ), int(search_s * ENV_HZ)
    n_ref = int(len(x) / rp.hop)
    n_rec = int(len(y) / mp.hop)
    out = []
    j0 = int(start_s * ENV_HZ)
    end = n_ref if end_s is None else min(n_ref, int(end_s * ENV_HZ))
    ref_all = rp.frames(x, 0, 0, n_ref)                     # each side's envelope once (tens of MB at most)
    rec_all = mp.frames(y, 0, 0, n_rec)
    while j0 + w <= end:
        ks = j0 - s + int(round(rec_offset_s * ENV_HZ))
        if ks + w + 2 * s > n_rec:
            break
        r = ref_all[j0:j0 + w]
        kk = np.arange(ks, ks + w + 2 * s)
        mok = (kk >= 0) & (kk < n_rec)
        m = np.zeros((kk.size, N_BANDS + 1), np.float32)
        m[mok] = rec_all[kk[mok]]
        ref_db = db(float(np.mean(r[:, N_BANDS])))
        mus = music_throughout(r)
        if ref_db < REF_QUIET_DBFS or mus < MUSIC_MIN:
            out.append(Match(t_s=j0 / ENV_HZ, clear=False, why="quiet", skip=True, ref_dbfs=round(ref_db, 1),
                             music=round(mus, 2)))
            j0 += int(hop_s * ENV_HZ)
            continue
        rf, rv = envelope(r)
        mf, mv = envelope(m, mok)
        res = match(rf, rv, mf, mv)
        res.update(t_s=j0 / ENV_HZ, ref_dbfs=round(ref_db, 1),
                   mic_dbfs=round(db(float(np.mean(m[mok, N_BANDS]))) if mok.any() else -300.0, 1))
        if "lag" in res:
            res["lag_s"] = (ks + res["lag"] - j0) / ENV_HZ - rec_offset_s
        out.append(res)
        j0 += int(hop_s * ENV_HZ)
    return out


# ---------------------------------------------------------------------------------------------------- CLI

def _cmd_status(a) -> int:
    d = load_latency()
    print(f"{lat_file()}:")
    for k, o in sorted((d.get("outputs") or {}).items()):
        print(f"  {k}: {o.get('delay_ms')} ms (median of {o.get('n')}; {o.get('updated_utc')})")
    if not d.get("outputs"):
        print("  (no measurements yet)")
    for out in ("pc22", "sonos:drawing-room"):
        mic = os.environ.get(MIC_ENV) or MIC_FOR[output_kind(out)]
        i = show_info(mic, (os.environ.get(SHOW_URL_ENV) or SHOW_URL).rstrip("/"))
        if not i.get("ok"):
            print(f"{out}: mic {mic}: {i.get('why')}")
            continue
        print(f"{out}: mic {mic} ({i.get('label')}): state {i.get('state')}, active input {i.get('active')}, "
              f"mic on {i.get('mic_on')} ({i.get('mic_why')}), {i.get('fs')} Hz, packet {i.get('packet')}, "
              f"in_lat {i.get('in_lat_ms')} ms, latency.json offset {i.get('offset_ms')} ms "
              f"-> mic latency {mic_latency_s(i) * 1000:.1f} ms")
    return 0


def _cmd_listen(a) -> int:
    """Read the levels feed through the tap for a while (read only) and say what it saw."""
    show = (os.environ.get(SHOW_URL_ENV) or SHOW_URL).rstrip("/")
    mic = a.mic or os.environ.get(MIC_ENV) or MIC_FOR[output_kind(a.output)]
    info = show_info(mic, show)
    print(json.dumps({k: info.get(k) for k in ("ok", "why", "active", "label", "state", "mic_on", "mic_why", "fs",
                                               "packet", "in_lat_ms", "offset_ms")}))
    tap = MicTap(mic, fs=int(info.get("fs") or 48000), packet=int(info.get("packet") or PACKET_DEFAULT))
    ws = WSClient(levels_url(show, mic)).connect()
    t0 = time.monotonic()
    c0 = time.process_time()
    seen: collections.Counter = collections.Counter()
    try:
        while time.monotonic() - t0 < a.seconds:
            msg = ws.recv(0.5)
            if msg is None:
                continue
            f = json.loads(msg)
            if isinstance(f.get("s"), str):
                seen[f["s"]] += 1
            tap.feed(f, time.monotonic())
    finally:
        ws.close()
    stream = mic
    if seen and mic not in seen:
        print(f"the feed sent {', '.join(seen)} for ?stream={mic}: it carries the active input only (an older "
              f"show_service)")
    tap.flush()
    cpu = time.process_time() - c0
    a_off = tap.arrival_offset()
    lags = tap.arrival_lateness()
    span = (tap.n / tap.fs) if tap.n else 0.0
    out = {"stream": stream, "frames_by_stream": dict(seen), "seconds": round(time.monotonic() - t0, 2),
           "samples_s": round(span, 3),
           "tap": dict(tap.stats), "level_dbfs": tap.level_dbfs(), "cpu_pct": round(100 * cpu / a.seconds, 2),
           "frames_valid": int(tap.ring.ok[:min(tap.ring.end, tap.ring.cap)].sum()),
           "arrival_after_envelope_ms": None if not lags.size else
           {"p50": round(1000 * float(np.percentile(lags, 50)), 2),
            "p95": round(1000 * float(np.percentile(lags, 95)), 2),
            "max": round(1000 * float(np.max(lags)), 2)},
           "mic_latency_ms": round(1000 * mic_latency_s(info), 2), "arrival_offset": a_off}
    print(json.dumps(out))
    return 0


def _cmd_measure(a) -> int:
    import soundfile as sf
    ref, rfs = sf.read(a.ref, dtype="float32", always_2d=True)
    rec, cfs = sf.read(a.rec, dtype="float32", always_2d=True)
    res = measure_offline(ref, rfs, rec, cfs, rec_offset_s=a.offset, start_s=a.start, end_s=a.end)
    for r in res:
        print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}))
    good = [r["lag_s"] for r in res if r.clear]
    if good:
        print(f"clear {len(good)}/{len(res)}: lag median {statistics.median(good) * 1000:.1f} ms, "
              f"spread {1000 * (max(good) - min(good)):.1f} ms")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m piano_render.calibrate", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    li = sub.add_parser("listen")
    li.add_argument("--output", default="sonos:drawing-room")
    li.add_argument("--mic", default=None)
    li.add_argument("--seconds", type=float, default=10.0)
    me = sub.add_parser("measure")
    me.add_argument("ref")
    me.add_argument("rec")
    me.add_argument("--offset", type=float, default=0.0, help="expected start of REF in REC, s")
    me.add_argument("--start", type=float, default=0.0)
    me.add_argument("--end", type=float, default=None)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return {"status": _cmd_status, "listen": _cmd_listen, "measure": _cmd_measure}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
