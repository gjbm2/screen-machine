"""The realism stage and the master, block by block with their state carried across blocks (piano_render/stream.py).

Each stage takes the next input frames and returns the next output frames, both in order from frame 0, with sample 0
= MIDI time 0 on both sides (a stage holds back what it can't finish yet, so its output may lag its input by a few
blocks; nothing is ever shifted in time). ``end_input(n)``: the input has n frames in all (the caller goes on pushing
zeros so tails can ring out); ``out_end`` is the output's length once the stage knows it.

  ToneStage       una corda, the instrument's EQ, width (realism.py steps 2-4; EQ alone with realism off): no delay,
                  the same as the offline (the una corda's low-pass primed on the 20 ms before each segment, as
                  tone.una_corda_inplace does, from the unshaped input).
  ResonanceStage  the resonator bank (resonance.py): the same numba kernels and coefficients, the bank state
                  (resonators, dormant keys) and the 60 Hz high-pass carried from block to block, the pedal and key
                  state from the Performance (all known in advance), the decimating and interpolating FIRs run on
                  windows of the stream. Holds back up to ~1,150 frames (24 ms at 48 kHz: one bank block plus the
                  two FIRs). Differences from the offline: the pedal-lift noise level follows the dry's running RMS
                  (with the instrument's calibrated level as the prior) instead of the whole piece's, and the 8 s tail
                  cap's 0.5 s fade is decided where it starts.
  RoomStage       the room by uniform-partitioned FFT convolution (overlap-save, PARTITION frames per partition, a
                  frequency-domain delay line of the IR's partition spectra; true stereo, mono in or per channel as
                  room.py): the same linear convolution as the offline (float32 FFT rounding apart), the IR's tail
                  carrying on after the last note. Holds back up to PARTITION - 1 frames.
  TrimStage       ends the stream the way realism.apply trims it: once the dry has ended and the tails (bank, room)
                  have stayed under -120 dBFS for QUIET_END_S, the output ends after the last frame above it (the
                  quiet frames already passed on make the stream up to QUIET_END_S longer than the offline).
  MasterStage     the instrument's gain_db, then a lookahead true-peak limiter at -1 dBTP: the true peak per frame
                  from master.py's 4x interpolation filter, the gain held at the lowest needed over the next
                  LIMIT_LOOKAHEAD_S, released with LIMIT_RELEASE_S and smoothed by a box of the lookahead's length,
                  the signal delayed by the lookahead plus the filter's 10 frames (3.2 ms). Unlimited, it is exactly
                  master.process's gain. Where master.process turned a whole render down to keep its loudest peak
                  under the ceiling, this limits just those peaks.

Chain strings stages together and passes each stage's output length on as the next one's input length.
"""

from __future__ import annotations

import math
import time
from typing import Optional

import numpy as np

from .. import score as _score

END_FLOOR = 10 ** (-120 / 20)          # realism.py's
QUIET_END_S = 0.25
PARTITION = 2048
CEILING_DBTP = -1.0
CEILING_MARGIN_DB = 0.01
LIMIT_MARGIN_DB = 0.05                 # the gain moves within a frame's neighbourhood: a little more headroom
LIMIT_LOOKAHEAD_S = 0.003
LIMIT_RELEASE_S = 0.15
TP_OVERSAMPLE = 4
TP_HALF = 10                           # master.true_peak's filter: 2 * 10 * 4 + 1 taps, 10 input frames each side
BURST_PRIOR_S = 5.0                    # the pedal-noise level's prior counts as this much audio
BURST_PRIOR_DB = -21.0                 # the dry's RMS that gain_db is calibrated to (about -20 LUFS after gain)

_EMPTY = np.zeros((0, 2), np.float32)


class Stage:
    name = "stage"

    def __init__(self) -> None:
        self.in_pos = 0
        self.out_pos = 0
        self.in_end: Optional[int] = None
        self.out_end: Optional[int] = None
        self.cpu_s = 0.0
        self.max_held = 0                      # most input frames held back at once

    def end_input(self, n: int) -> None:
        if self.in_end is None:
            self.in_end = int(n)
            self._on_end()

    def _on_end(self) -> None:
        self.out_end = self.in_end

    def push(self, x: np.ndarray) -> np.ndarray:
        c0 = time.thread_time()                  # this thread's CPU (sfizz's loader threads run alongside)
        x = np.asarray(x, np.float32)
        y = self._push(x) if x.shape[0] else self._push(_EMPTY)
        self.in_pos += x.shape[0]
        self.out_pos += y.shape[0]
        self.max_held = max(self.max_held, self.in_pos - self.out_pos)
        self.cpu_s += time.thread_time() - c0
        return y

    def _push(self, x: np.ndarray) -> np.ndarray:
        return x

    def stats(self) -> dict:
        return {"cpu_s": round(self.cpu_s, 3), "max_held_frames": int(self.max_held)}


# --- tone: una corda, EQ, width ---------------------------------------------------------------------------------
class ToneStage(Stage):
    name = "tone"

    def __init__(self, inst: dict, sr: int, ctl: "Optional[_score.Controls]" = None, una_corda: bool = True,
                 width: bool = True) -> None:
        super().__init__()
        from .filters import butter_sos, eq_sos
        from . import tone as _tone
        self.sr = int(sr)
        self.eq = eq_sos(inst.get("eq") or [], sr)
        self.eq_zi = [np.zeros((self.eq.shape[0], 2)) for _ in range(2)] if self.eq is not None else None
        w = inst.get("width", 1.0) if width else None
        self.width = None if w is None or float(w) == 1.0 else np.float32(w)
        self.segs: list = []
        self.uc_info: dict = {"segments": 0}
        if una_corda and ctl is not None and inst.get("una_corda", True) is not False and ctl.n >= 2 \
                and float(ctl.una_corda.max()) > 0.0:
            t = np.asarray(ctl.t, np.float64)
            rate = 1.0 / float(t[1] - t[0])
            us = np.clip(_tone.smooth_rows(np.asarray(ctl.una_corda, np.float64), rate), 0.0, 1.0)
            act = us > 1e-4
            act[1:] |= act[:-1]
            act[:-1] |= act[1:]
            for r0, r1 in _tone._segments(act):
                s0 = int(math.floor(t[r0] * sr))
                s1 = int(math.ceil(t[min(r1, len(t) - 1)] * sr)) + 1
                if s1 > s0:
                    self.segs.append((s0, s1, t[r0:r1 + 1], us[r0:r1 + 1]))
            self.uc_sos = butter_sos(2, _tone.UC_SHELF_HZ, sr, "lowpass")
            self.uc_zi: Optional[list] = None            # the low-pass state inside the current segment
            self.uc_seg: Optional[int] = None            # its start frame
            self.pre_n = int(0.02 * sr)                  # tone.una_corda_inplace primes on the 20 ms before
            self.pre = np.zeros((0, 2), np.float32)      # the input's last pre_n frames before this block
            self.k_cut = np.float32(_tone.UC_SHELF_DB * math.log(10) / 20)
            self.k_lv = np.float32(_tone.UC_LEVEL_DB * math.log(10) / 20)
            self.uc_info = {"segments": len(self.segs), "max": round(float(ctl.una_corda.max()), 3)}

    def _push(self, x: np.ndarray) -> np.ndarray:
        from scipy.signal import sosfilt
        n = x.shape[0]
        if n == 0:
            return x
        p = self.in_pos
        lim = n if self.in_end is None else max(0, min(n, self.in_end - p))
        y = np.array(x, np.float32, copy=True)
        if lim < n:
            y[lim:] = 0.0
        if self.segs:
            while self.segs and self.segs[0][1] <= p:
                self.segs.pop(0)
            for s0, s1, tt, uu in self.segs:
                if s0 >= p + lim:
                    break
                a, b = max(s0, p), min(s1, p + lim)
                if b <= a:
                    continue
                if self.uc_seg != s0:                    # the segment starts: prime on the 20 ms before it
                    self.uc_seg = s0
                    hist = np.concatenate([self.pre, x[:max(0, s0 - p)]])
                    src = hist[max(0, hist.shape[0] - min(self.pre_n, s0)):]
                    self.uc_zi = []
                    for c in range(2):
                        z0 = np.zeros((self.uc_sos.shape[0], 2))
                        if src.shape[0]:
                            _, z0 = sosfilt(self.uc_sos, src[:, c], zi=z0)
                        self.uc_zi.append(z0)
                useg = np.interp(np.arange(a, b) / self.sr, tt, uu).astype(np.float32)
                g_cut = 1.0 - np.exp(useg * self.k_cut)
                g_lv = np.exp(useg * self.k_lv)
                for c in range(2):
                    xx = x[a - p:b - p, c]
                    lp, self.uc_zi[c] = sosfilt(self.uc_sos, xx, zi=self.uc_zi[c])
                    y[a - p:b - p, c] = g_lv * (xx - g_cut * (xx - lp.astype(np.float32)))
            tail = np.concatenate([self.pre, x]) if self.pre.shape[0] else x
            self.pre = np.array(tail[-self.pre_n:], np.float32)
        if self.eq is not None and lim > 0:
            from ._stream_kernels import sos_filter
            for c in range(2):
                col = np.ascontiguousarray(y[:lim, c])
                sos_filter(col, self.eq, self.eq_zi[c], col)
                y[:lim, c] = col
        if self.width is not None and lim > 0:
            mid = 0.5 * (y[:lim, 0] + y[:lim, 1])
            side = 0.5 * (y[:lim, 0] - y[:lim, 1]) * self.width
            y[:lim, 0] = mid + side
            y[:lim, 1] = mid - side
        return y


# --- resonance ----------------------------------------------------------------------------------------------------
class ResonanceStage(Stage):
    name = "resonance"

    def __init__(self, sr: int, perf: _score.Performance, resonance_db: float, tuning=None,
                 pan_width: Optional[float] = None, ctl: "Optional[_score.Controls]" = None,
                 rms_prior: float = 10 ** (BURST_PRIOR_DB / 20)) -> None:
        super().__init__()
        from . import _kernels as K
        from . import resonance as R
        from .filters import butter_sos
        self.K, self.R = K, R
        fs = R.FS_BANK
        if sr % fs:
            raise ValueError(f"streaming resonance needs a sample rate that is a multiple of {fs} (got {sr})")
        self.sr, self.fs = int(sr), fs
        self.U = self.D = sr // fs
        self.B = R.BLOCK
        self.half = R.RESAMPLE_TAPS // 2
        self.T = (R.RESAMPLE_TAPS + self.U - 1) // self.U
        self.bank = R.build_bank(tuning, R.PAN_WIDTH if pan_width is None else pan_width, fs)
        (self.rfree, self.rtab, self.cw, self.sw, self.g, self.owl, self.owr) = R._coefficients(self.bank, fs)
        rate = fs / self.B
        if ctl is None or abs(ctl.rate_hz - rate) > 1e-9:
            ctl = perf.controls(rate, duration_s=perf.duration_s + 45.0)
        lift = np.asarray(ctl.damper_lift, np.float64)
        free = np.ascontiguousarray(ctl.free())
        self.lift_q = np.ascontiguousarray(np.rint(lift * R.LIFT_STEPS).astype(np.int64))
        self.free = free
        self.live = np.ascontiguousarray(free | (self.lift_q > 0)[:, None])
        tau_own_free = (R.OWN_TAU_SCALE * _score.TAU_SUS).astype(np.float64)
        tau_own_damp = (R.OWN_TAU_SCALE * R.damper_tau(_score.KEYS.astype(np.float64))).astype(np.float64)
        gate = K.own_gate(np.ascontiguousarray(ctl.struck), free, lift, self.B / fs, tau_own_free, tau_own_damp,
                          R.OWN_GATE_DB[0], R.OWN_GATE_DB[1], np.asarray(ctl.una_corda, np.float64),
                          10 ** (R.UC_SELF_DB / 20))
        gate[:-1] = np.minimum(gate[:-1], gate[1:])
        self.gate = gate
        self.rows = ctl.n
        self.lift_ev = [int(b) for b in R.lift_events(lift)]
        self.rng = np.random.default_rng(R.SEED + 1)
        self.nburst = int(R.BURST_S * fs)
        self.bursts: list = []                   # (start sample, burst) of events reached so far, still to be added
        self.burst_level = 10 ** (R.BURST_DB / 20)
        self.prior_ms = float(rms_prior) ** 2 * BURST_PRIOR_S * fs
        self.prior_n = BURST_PRIOR_S * fs
        self.ms_sum = 0.0                        # bank input energy so far (before any noise burst)
        self.ms_n = 0
        self.gain = R.gain_for(resonance_db)
        self.h_dn = R._fir(sr, self.D)
        self.h_up = R._fir(sr, self.U) * np.float32(self.U * self.gain)
        self.hp = butter_sos(2, R.HP_HZ, fs, "highpass")
        self.hp_zi = [np.zeros((self.hp.shape[0], 2)) for _ in range(2)]
        nres = self.bank.size
        self.yr = np.zeros(nres, np.float32)
        self.yi = np.zeros(nres, np.float32)
        self.dormant = np.ones(_score.N_KEYS, np.bool_)
        self.work = 0
        # stream positions: 48 kHz input history from frame h0; bank input (decimated) from bank sample xb0;
        # bank output (high-passed) from yb0; the dry, kept for the output, from frame d0
        self.pad = 2 * self.half + 2              # frames of history the decimator reaches back (a multiple of D)
        self.pad += (-self.pad) % self.D
        self.h0 = -self.pad
        self.hist = np.zeros((self.pad, 2), np.float32)
        self.dec_next = 0                        # next bank sample to decimate
        self.xb0 = 0
        self.xb = np.zeros(0, np.float32)
        self.blk_next = 0                        # next bank block to run
        self.yb0 = 0
        self.ybl = np.zeros(0, np.float32)
        self.ybr = np.zeros(0, np.float32)
        self.dry0 = 0
        self.dry = np.zeros((0, 2), np.float32)
        self.n_in: Optional[int] = None          # bank samples of real input (offline's n_in), once known
        self.cap: Optional[int] = None           # bank samples incl. the tail (offline's x.size)
        self.bank_end: Optional[int] = None      # the bank output's end (offline's "end")
        self.peak = 0.0
        self.last_loud = -1
        self.fade_from: Optional[int] = None
        self.fade_len = 0
        self.capped = False

    # input end: the bank has n_in samples of input, then TAIL_MAX_S of zeros (the offline buffer)
    def _on_end(self) -> None:
        self.n_in = -(-self.in_end // self.D)
        self.cap = self.n_in + int(self.R.TAIL_MAX_S * self.fs)
        f = min(self.cap - self.n_in, int(0.5 * self.fs))
        self.fade_from, self.fade_len = self.cap - f, f

    def _decimate(self) -> None:
        D = self.D
        avail = self.h0 + self.hist.shape[0]                      # input frames held up to here
        ready = max(0, (avail - self.half - 1) // D + 1)          # bank samples whose window is complete
        if ready <= self.dec_next:
            return
        w0 = self.dec_next * D - self.pad
        a = w0 - self.h0
        b = (ready - 1) * D + self.half + 1 - self.h0
        win = np.ascontiguousarray(self.hist[a:b])
        n_out = ready - self.dec_next
        y = self.K.fir_down2(win, self.h_dn, D, self.half + self.pad, n_out)
        self.xb = np.concatenate([self.xb, y]) if self.xb.size else y
        self.dec_next = ready
        keep = ready * D - self.pad - self.h0
        if keep > 0:
            self.hist = self.hist[keep:]
            self.h0 += keep

    def _add_bursts(self, s0: int, s1: int, x: np.ndarray) -> None:
        """Pedal-lift noise into x (bank input s0..s1, before any noise): each burst at the RMS of the dry's bank
        input before its block (with the prior), so the level never depends on how the stream was split."""
        R, B = self.R, self.B
        nblk = (s1 - s0) // B
        sq = np.square(x[:nblk * B], dtype=np.float64)
        if self.n_in is not None and s1 > self.n_in:
            sq[max(0, self.n_in - s0):] = 0.0                     # the zeros after the dry don't count
            real = np.clip(self.n_in - (s0 + B * np.arange(nblk)), 0, B)
        else:
            real = np.full(nblk, B, np.int64)
        e = sq.reshape(nblk, B).sum(axis=1)
        e_before = np.concatenate([[0.0], np.cumsum(e)])
        n_before = np.concatenate([[0], np.cumsum(real)])
        while self.lift_ev and self.lift_ev[0] * B < s1:
            b = self.lift_ev.pop(0)
            k = max(0, b * B - s0) // B
            rms = math.sqrt((self.ms_sum + e_before[k] + self.prior_ms) / (self.ms_n + n_before[k] + self.prior_n))
            if rms > 0:
                self.bursts.append((b * B, np.float32(rms * self.burst_level) * R._pink_burst(self.nburst,
                                                                                               self.rng)))
        live = []
        for s, burst in self.bursts:
            a, b = max(s, s0), min(s + burst.shape[0], s1, self.cap if self.cap is not None else s1)
            if b > a:
                x[a - s0:b - s0] += burst[a - s:b - s]
            if s + burst.shape[0] > s1:
                live.append((s, burst))
        self.bursts = live
        self.ms_sum += float(e_before[-1])
        self.ms_n += int(n_before[-1])

    def _run_blocks(self) -> None:
        B = self.B
        k0 = self.blk_next
        k1 = self.dec_next // B                                   # whole bank blocks of input decimated
        if self.cap is not None:
            k1 = min(k1, -(-self.cap // B))                       # the offline buffer ends at the cap
        if self.bank_end is not None:
            k1 = k0
        if k1 <= k0:
            return
        s0, s1 = k0 * B, k1 * B
        x = np.zeros(s1 - s0, np.float32)
        have = min(s1, self.xb0 + self.xb.size) - s0
        if have > 0:
            x[:have] = self.xb[s0 - self.xb0:s0 - self.xb0 + have]
        if self.cap is not None and s1 > self.cap:
            x[self.cap - s0:] = 0.0
        self._add_bursts(s0, s1, x)
        rows = self.rows
        idx = np.minimum(np.arange(k0, k1), rows - 1)
        if k1 <= rows:
            lq, fr, gt, lv = (self.lift_q[k0:k1], self.free[k0:k1], self.gate[k0:k1], self.live[k0:k1])
        else:
            lq, fr, gt, lv = (np.ascontiguousarray(self.lift_q[idx]), np.ascontiguousarray(self.free[idx]),
                              np.ascontiguousarray(self.gate[idx]), np.ascontiguousarray(self.live[idx]))
        outl = np.zeros(s1 - s0, np.float32)
        outr = np.zeros(s1 - s0, np.float32)
        bk = self.bank
        n_valid = (min(s1, self.cap) - s0) if self.cap is not None else (s1 - s0)
        xs = x[:n_valid] if n_valid < x.size else x
        self.work += self.K.bank_run(xs, 0, k1 - k0, B, lq, fr, gt, lv, bk.kstart, bk.kend, self.rfree, self.rtab,
                                     self.cw, self.sw, self.g, self.owl, self.owr, self.yr, self.yi, self.dormant,
                                     outl, outr, np.float32(self.R.DORMANT_EPS))
        from ._stream_kernels import sos_filter
        sos_filter(outl, self.hp, self.hp_zi[0], outl)
        sos_filter(outr, self.hp, self.hp_zi[1], outr)
        if n_valid < outl.size:
            outl[n_valid:] = 0.0
            outr[n_valid:] = 0.0
        self.blk_next = k1
        # drop decimated input the bank has used
        used = s1 - self.xb0
        if used > 0:
            self.xb = self.xb[min(used, self.xb.size):]
            self.xb0 += used
        self._tail(s0, outl, outr)
        self.ybl = np.concatenate([self.ybl, outl]) if self.ybl.size else outl
        self.ybr = np.concatenate([self.ybr, outr]) if self.ybr.size else outr

    def _tail(self, s0: int, outl: np.ndarray, outr: np.ndarray) -> None:
        """Track the bank's peak and the tail's end (offline: the last sample above -120 dB re the peak after the
        input, or the cap with a 0.5 s fade)."""
        m = np.maximum(np.abs(outl), np.abs(outr))
        if m.size:
            self.peak = max(self.peak, float(m.max()))
        floor = self.peak * 10 ** (self.R.TAIL_FLOOR_DB / 20)
        loud = np.flatnonzero(m > floor) if floor > 0 else np.zeros(0, np.int64)
        if loud.size:
            self.last_loud = s0 + int(loud[-1])
        if self.n_in is None:
            return
        s1 = s0 + outl.size
        if self.fade_from is not None and self.fade_len > 0 and s1 > self.fade_from:
            if not self.capped and s0 <= self.fade_from < s1 + 1:
                ref = m[max(0, self.fade_from - s0 - self.B):max(1, self.fade_from - s0)]
                self.capped = bool(ref.size and float(ref.max()) > floor)
            if self.capped:
                a = max(s0, self.fade_from)
                b = min(s1, self.cap)
                if b > a:
                    ramp = np.linspace(1.0, 0.0, self.fade_len, dtype=np.float32)[a - self.fade_from:b - self.fade_from]
                    outl[a - s0:b - s0] *= ramp
                    outr[a - s0:b - s0] *= ramp
        if s1 >= self.cap:
            self.bank_end = self.cap if self.capped else max(self.n_in, self.last_loud + 1)
        elif s1 > self.n_in and not self.bursts and not self.lift_ev and not self.capped and \
                s1 - max(self.n_in, self.last_loud + 1) >= int(QUIET_END_S * self.fs):
            # no input any more and the ringing has stayed under the floor for QUIET_END_S: it won't come back
            self.bank_end = max(self.n_in, self.last_loud + 1)
        if self.bank_end is not None:
            self.out_end = max(self.in_end, int(round(self.bank_end * self.sr / self.fs)))

    def _upsample(self) -> np.ndarray:
        U, half, T = self.U, self.half, self.T
        q0 = self.out_pos
        bank_avail = self.yb0 + self.ybl.size
        if self.bank_end is not None:
            q1 = self.dry0 + self.dry.shape[0]                    # the bank is done: zeros after its end
        else:
            q1 = min(self.dry0 + self.dry.shape[0], U * bank_avail - half)
        if q1 <= q0:
            return _EMPTY
        out = np.array(self.dry[q0 - self.dry0:q1 - self.dry0], np.float32, copy=True)
        c0 = max(0, (q0 + half) // U - (T - 1))
        c1 = (q1 - 1 + half) // U + 1                           # bank samples needed: [c0, c1)
        lim_end = self.bank_end if self.bank_end is not None else c1
        winl = np.zeros(c1 - c0, np.float32)
        winr = np.zeros(c1 - c0, np.float32)
        a, b = max(c0, self.yb0), min(c1, self.yb0 + self.ybl.size, lim_end)
        if b > a:
            winl[a - c0:b - c0] = self.ybl[a - self.yb0:b - self.yb0]
            winr[a - c0:b - c0] = self.ybr[a - self.yb0:b - self.yb0]
        n_out = q1 - q0
        if self.out_end is not None:
            n_out = max(0, min(n_out, self.out_end - q0))
        if n_out > 0 and c1 > c0:
            self.K.fir_up2_add(winl, winr, self.h_up, U, half + q0 - U * c0, out, n_out)
        # drop what no later output needs
        keep_c = max(0, (q1 + half) // U - (T - 1))
        drop = min(max(0, keep_c - self.yb0), self.ybl.size)
        if drop:
            self.ybl = self.ybl[drop:]
            self.ybr = self.ybr[drop:]
            self.yb0 += drop
        dd = q1 - self.dry0
        self.dry = self.dry[dd:]
        self.dry0 = q1
        return out

    def _push(self, x: np.ndarray) -> np.ndarray:
        if x.shape[0]:
            self.hist = np.concatenate([self.hist, x])
            self.dry = np.concatenate([self.dry, x]) if self.dry.shape[0] else np.array(x, np.float32)
        self._decimate()
        self._run_blocks()
        return self._upsample()

    def stats(self) -> dict:
        st = super().stats()
        st.update(resonators=int(self.bank.size), resonator_samples_M=round(self.work / 1e6, 1),
                  tail_capped=bool(self.capped), gain=self.gain,
                  burst_rms=round(math.sqrt((self.ms_sum + self.prior_ms) / (self.ms_n + self.prior_n)), 6))
        return st


# --- room -----------------------------------------------------------------------------------------------------------
_MODES = {"true": 0, "mono-in": 1, "per-channel": 2}


class RoomStage(Stage):
    name = "room"

    def __init__(self, room: dict, sr: int, partition: int = PARTITION) -> None:
        super().__init__()
        import scipy.fft as sfft
        from . import room as _room
        self.sfft = sfft
        loaded = _room.load_ir(room, sr)
        if loaded is None:
            raise _room.RoomError(f"room {room.get('id')!r}: no IR file installed and no synth settings")
        h, mode, source = loaded
        self.info = {"room": room.get("id"), "ir_source": source, "ir_mode": mode,
                     "ir_s": round(h.shape[0] / sr, 3), "wet_db": float(room["wet_db"])}
        P = int(partition)
        L = h.shape[0]
        self.P, self.L = P, L
        self.Kp = Kp = -(-L // P)
        C = h.shape[1]
        hp = np.zeros((Kp * P, C), np.float32)
        hp[:L] = h
        gain = np.float32(10 ** (float(room["wet_db"]) / 20.0))
        Hs = sfft.rfft(hp.reshape(Kp, P, C), 2 * P, axis=1, workers=1) * gain       # (Kp, P + 1, C) complex64
        Hs = np.transpose(Hs.astype(np.complex64), (0, 2, 1))                      # (Kp, C, P + 1)
        self.Hr = np.ascontiguousarray(Hs.real, np.float32)
        self.Hi = np.ascontiguousarray(Hs.imag, np.float32)
        self.mode = _MODES[mode]
        F = P + 1
        self.fr = np.zeros((2 * Kp, 2, F), np.float32)
        self.fi = np.zeros((2 * Kp, 2, F), np.float32)
        self.part = 0                                   # partitions done
        self.prev = np.zeros((P, 2), np.float32)        # the previous partition's input (overlap-save)
        self.pend = np.zeros((0, 2), np.float32)
        self.y = [np.zeros(F, np.float32) for _ in range(4)]

    def _on_end(self) -> None:
        self.out_end = self.in_end + self.L - 1

    def _push(self, x: np.ndarray) -> np.ndarray:
        from ._stream_kernels import fdl_mac_soa
        sfft = self.sfft
        P, Kp = self.P, self.Kp
        if x.shape[0]:
            self.pend = np.concatenate([self.pend, x]) if self.pend.shape[0] else np.array(x, np.float32)
        nparts = self.pend.shape[0] // P
        if nparts == 0:
            return _EMPTY
        out = np.empty((nparts * P, 2), np.float32)
        for j in range(nparts):
            blk = self.pend[j * P:(j + 1) * P]
            win = np.concatenate([self.prev, blk])
            slot = self.part % Kp
            if self.mode == 1:
                X = sfft.rfft(0.5 * (win[:, 0] + win[:, 1]), workers=1)
                for sl in (slot, slot + Kp):
                    self.fr[sl, 0] = X.real
                    self.fi[sl, 0] = X.imag
            else:
                X = sfft.rfft(win, axis=0, workers=1)                  # (P + 1, 2)
                for sl in (slot, slot + Kp):
                    self.fr[sl] = X.real.T
                    self.fi[sl] = X.imag.T
            ylr, yli, yrr, yri = self.y
            fdl_mac_soa(self.fr, self.fi, self.Hr, self.Hi, (self.part + 1) % Kp, Kp, self.mode, ylr, yli, yrr, yri)
            wl = sfft.irfft(ylr + 1j * yli, 2 * P, workers=1)[P:]
            wr = sfft.irfft(yrr + 1j * yri, 2 * P, workers=1)[P:]
            o = out[j * P:(j + 1) * P]
            o[:, 0] = blk[:, 0] + wl
            o[:, 1] = blk[:, 1] + wr
            self.prev = blk
            self.part += 1
        self.pend = self.pend[nparts * P:]
        return out

    def stats(self) -> dict:
        st = super().stats()
        st.update(self.info, partitions=self.Kp, partition=self.P)
        return st


# --- the end of the tails ---------------------------------------------------------------------------------------------
class TrimStage(Stage):
    """Pass-through; once the dry (n0 frames) has ended and the input has stayed under END_FLOOR for QUIET_END_S
    after it, the output ends after the last loud frame (realism.apply's trim, never shorter than n0)."""
    name = "trim"

    def __init__(self, sr: int) -> None:
        super().__init__()
        self.sr = int(sr)
        self.n0: Optional[int] = None
        self.last_loud = -1
        self.quiet_n = int(QUIET_END_S * sr)

    def set_dry_end(self, n0: int) -> None:
        self.n0 = int(n0)

    def _on_end(self) -> None:
        if self.out_end is None:
            self.out_end = self.in_end

    def _push(self, x: np.ndarray) -> np.ndarray:
        if x.shape[0] == 0:
            return x
        p = self.in_pos
        if self.n0 is None or p + x.shape[0] <= self.n0:            # the end is never before n0
            return x
        m = np.max(np.abs(x), axis=1)
        loud = np.flatnonzero(m > END_FLOOR)
        if loud.size:
            self.last_loud = p + int(loud[-1])
        if self.out_end is None:                                     # the dry has ended: only tails are left
            end_here = p + x.shape[0]
            if end_here - max(self.n0, self.last_loud + 1) >= self.quiet_n:
                self.out_end = max(self.n0, self.last_loud + 1)
        if self.out_end is not None and p + x.shape[0] > self.out_end:
            x = np.array(x, np.float32, copy=True)
            x[max(0, self.out_end - p):] = 0.0
        return x


# --- master: gain and a lookahead true-peak limiter ---------------------------------------------------------------
class MasterStage(Stage):
    name = "master"

    def __init__(self, sr: int, gain_db: float, ceiling_dbtp: float = CEILING_DBTP, limiter: bool = True,
                 lookahead_s: float = LIMIT_LOOKAHEAD_S, release_s: float = LIMIT_RELEASE_S) -> None:
        super().__init__()
        self.sr = int(sr)
        self.gain_db = float(gain_db or 0.0)
        self.g = np.float32(10.0 ** (self.gain_db / 20.0))
        self.limiter = bool(limiter)
        self.ceiling = 10 ** ((ceiling_dbtp - CEILING_MARGIN_DB - LIMIT_MARGIN_DB) / 20)
        self.La = max(1, int(round(lookahead_s * sr)))
        self.a_rel = 1.0 - math.exp(-1.0 / (release_s * sr))
        if self.limiter:
            from scipy.signal import firwin
            h = firwin(2 * TP_HALF * TP_OVERSAMPLE + 1, 1.0 / TP_OVERSAMPLE, window=("kaiser", 5.0)) * TP_OVERSAMPLE
            T = 2 * TP_HALF + 1
            self.phases = np.zeros((TP_OVERSAMPLE, T))
            for p in range(TP_OVERSAMPLE):
                taps = h[p::TP_OVERSAMPLE]
                self.phases[p, :taps.size] = taps
            # no interpolated value can exceed this times the largest frame in its reach (master._phase_l1)
            self.bound = float(np.abs(self.phases).sum(axis=1).max()) * 1.001
        self.x = np.zeros((2 * TP_HALF, 2), np.float32)     # gained signal from frame x0 (zeros before frame 0)
        self.x0 = -2 * TP_HALF
        self.req = np.ones(self.La, np.float64)               # required gain of the La frames before the next
        self.r_next = 0                                       # next frame to get its required gain
        self.state = np.array([1.0, float(self.La), 0.0, 0.0])
        self.ring = np.ones(self.La, np.float64)
        self.min_gain = 1.0
        self.limited_frames = 0
        self.delay = self.La + TP_HALF if self.limiter else 0

    def _push(self, x: np.ndarray) -> np.ndarray:
        xg = x * self.g if self.g != np.float32(1.0) else np.array(x, np.float32, copy=True)
        if not self.limiter:
            return xg.astype(np.float32, copy=False)
        from ._stream_kernels import limiter_gain
        if xg.shape[0]:
            self.x = np.concatenate([self.x, xg.astype(np.float32, copy=False)])
        avail = self.x0 + self.x.shape[0]
        r1 = avail - TP_HALF                                  # frames whose interpolation window is complete
        n = r1 - self.r_next
        if n <= 0:
            return _EMPTY
        a = self.r_next - TP_HALF - self.x0
        win32 = self.x[a:a + n + 2 * TP_HALF]                       # frames r_next - H .. r1 + H - 1
        req = np.ones(n)
        if float(np.max(np.abs(win32))) * self.bound > self.ceiling:     # else no peak can reach the ceiling
            from ._stream_kernels import true_peak_req
            true_peak_req(np.ascontiguousarray(win32), self.phases, self.ceiling, req)
        if self.state[0] == 1.0 and self.state[3] == 0 and not (req < 1.0).any() and not (self.req < 1.0).any():
            gain = req                                              # nothing to limit, nothing releasing
        else:
            reqx = np.concatenate([self.req, req])
            gain = np.empty(n)
            limiter_gain(reqx, self.La, self.a_rel, self.state, self.ring, gain)
        self.req = np.concatenate([self.req, req])[-self.La:]
        f0 = self.r_next - self.La                            # the signal frames these gains are for
        self.r_next = r1
        lo = max(0, f0)
        k = lo - f0
        out = (self.x[lo - self.x0:f0 + n - self.x0] * gain[k:, None].astype(np.float32)).astype(np.float32)
        if n - k > 0:
            gmin = float(gain[k:].min())
            self.min_gain = min(self.min_gain, gmin)
            self.limited_frames += int(np.count_nonzero(gain[k:] < 1.0))
        keep_from = self.r_next - 2 * TP_HALF
        cut = max(0, min(keep_from, f0 + n) - self.x0)
        if cut:
            self.x = self.x[cut:]
            self.x0 += cut
        return out

    def stats(self) -> dict:
        st = super().stats()
        st.update(gain_db=self.gain_db, limiter=self.limiter, delay_frames=self.delay,
                  min_gain_db=round(20 * math.log10(self.min_gain), 2) if self.min_gain > 0 else None,
                  limited_s=round(self.limited_frames / self.sr, 3))
        return st


# --- the chain ---------------------------------------------------------------------------------------------------
class Chain:
    """Stages in order; each stage's output length becomes the next one's input length as soon as it is known."""

    def __init__(self, stages: list) -> None:
        self.stages = stages

    def end_input(self, n: int) -> None:
        self.stages[0].end_input(n)
        self._propagate()

    def _propagate(self) -> None:
        for a, b in zip(self.stages, self.stages[1:]):
            if a.out_end is not None and b.in_end is None:
                b.end_input(a.out_end)

    def push(self, x: np.ndarray) -> np.ndarray:
        for s in self.stages:
            x = s.push(x)
            self._propagate()
        return x

    @property
    def out_end(self) -> Optional[int]:
        return self.stages[-1].out_end

    @property
    def out_pos(self) -> int:
        return self.stages[-1].out_pos

    @property
    def done(self) -> bool:
        e = self.out_end
        return e is not None and self.out_pos >= e

    def stats(self) -> dict:
        return {s.name: s.stats() for s in self.stages}
