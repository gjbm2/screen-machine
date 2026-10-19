#!/usr/bin/env python3
"""OUR clock, improved: variants of show_service's beat clock (clock.Clock), made by subclassing it here, never by
editing show_service, and measured offline on the DEV subset under conditions A (clean) and C (the room model).

show_service's owner (screen-machine-3a) proposed four changes and will put the winners into clock.py; each is a
switch on ``VarClock`` (a ``clock.Clock`` subclass), alone or combined, plus the extra knobs the task allowed:

  b  seed only on music: no cold seed, no confirmation and no beat gate while the ML worker's CED-mini says the room
     isn't hearing music (its ``ced`` lines, replayed at the live cadence: alt/ced_lane.py), and a cold re-seed
     (grid, tempo prior and tempogram dropped) when music starts after a stretch without it
  d  conf from peak-to-grid agreement (and a logistic calibration of it on the dev subset's beats)
  a  escape a stale lock: conf under a floor for N s while locked or holding, with rhythm present -> cold search,
     the running tempo prior dropped (it pulled the new seed back onto the stale tempo)
  c  Beat This! seeds: the slow lane's window seeds (or re-seeds) a grid when the clock has none or a weak one
  e  the tempogram's PLP phase as a weak phase observation on missed beats (the activation's pulse, where the
     peaks are too sparse to observe the phase)
  s  seed selection: the cold seed's tempo chosen among the tempogram's top 3 by clarity x prior x the recent
     peaks' comb coherence at that period (the stock seed takes the top one)
  nets=N / peaks / consts  madmom's net count (i), the peak picker's thresholds (iv), module constants such as
     FAST_BPM / SEED_CAP_BPM (v) or GATE_FRAC

Every variant's result is run_stack.py's own JSON (score.py scores it unchanged), replayed exactly and fast on
clockvar_replay.py's per-track cache (madmom and dsp recorded once per track and condition; see its docstring).

  PY=~/.venvs/sm-show/bin/python; export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=1
  nice -n 19 $PY tools/room/beat_eval/alt/clockvar.py batch --subset dev --conds A,C --variants base,b,d,a,b+d+a
  nice -n 19 $PY tools/room/beat_eval/alt/clockvar.py check --subset dev --cond C        # fast replay == recording
  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/alt/clockvar_report.py --subset dev --conds A,C

``batch`` takes the CPU lock in short slices (``--slice-s``, a few excerpts or one full track; record if the cache is
missing, then every variant), writes ~/.cache/screen-machine/beat-eval/clockvar/<subset>-<cond>/<variant>/
<dataset>__<id>.json and scores each dir. Per-track runner for run_batch.py (``--runner``): alt/clockvar_run.py.

Dev results (19 Oct; dev-subset.json, 46 steady-beat tracks; A clean gate -30, C room model gate -48.64, each track
its baseline seed and 30 s pre-roll in C; means per track from the music's start, clockvar_report.py)
---------------------------------------------------------------------------------------------------------------
  variant          C: F    cover  prec   wrong  tLock  locked |  A: F    cover  prec   tLock  locked | madmom
  stock               .306  .030   .857   .125   25.6   .13    |    .704  .522   .881   8.8    .74    | 2 nets
  b                   .343  .048   .853   .096   20.6   .20    |    .703  .519   .881   9.1    .74    |
  a                   .387  .041   .825   .096   33.4   .15    |    .715  .527   .873   8.8    .74    |
  d (agreement)       .306  .035   .716   .229   24.5   .13    |    .710  .511   .878   7.9    .76    |
  d logistic, gate .7 .304  .093   .631   .257   15.4   .17    |    .688  .680   .799   5.9    .74    |
  c (BT normalised)   .335  .035   .868   .109   19.5   .15    |    .706  .526   .888   8.9    .76    |
  nets=8              .336  .037   .931   .014   27.6   .15    |    .743  .560   .899   7.0    .76    | 8 nets
  b+a                 .376  .050   .885   .080   20.6   .24    |    .714  .523   .873   9.1    .74    |
  nets=8+b+a          .402  .079   .933   .056   14.0   .30    |    .760  .577   .904   7.2    .80    | 8 nets
- The room is the limit, not the clock's logic: in C madmom's activation on the beat is 1.2-2.4x its mean (2 nets),
  the tempogram's top candidate is the right tempo ~20 % of the time on ballroom, Beat This! finds no beats at the
  mic's -48 dBFS (it does once each window is normalised to -20 dBFS: bt_lane.py) and CED's music_p reads 0.1-0.5.
  No variant gets near the bars in C (cover >= 0.7); nets=8+b+a is the best that keeps precision (C cover 2.6x,
  precision .86 -> .93, wrong locks halved, lock time 26 -> 14 s; A better on every column).
- b stops the pre-roll's room sound seeding (the 42-47 bpm grids); a frees the grids that still stick; d's
  agreement and logistic confs open the gate more but on wrong grids too (precision falls): not recommended alone.
- refine off (the low-band hits not replacing the madmom peak time) helps A (hit25 .545 -> .598, offset +5 -> 0 ms,
  cover .522 -> .571, precision .881 -> .900); neutral in C.

For clock.py (3a): which code changes
-------------------------------------
- (i) nets: ``Clock(nets=8)`` / beatproc's ``--nets 8`` (MadmomActivation(nets=8)). CPU (nets_cost.py, process time
  per audio s, same audio): 1 net 52, 2 nets 73, 4 nets 103, 8 nets 164 ms (x2.26 of 2 nets, not x4: the spectrogram
  is shared). Live 2 nets ~0.13 core -> 8 nets ~0.29 core: show_service + worker + madmom ~0.61 of the 0.8 budget.
  4 nets is as good on A but not in C.
- (b) new ``Clock.observe_ced(line)`` (any thread: a deque, drained in ``_drain``) fed by mllink ``_on_line`` on each
  ML_CED line (a BrainContext slot, e.g. ``clock_ced``). ``_on_ced``: music turns on when the mean music_p of the
  last 2 lines >= 0.10, off when all of the last 4 are < 0.04 (raw CED on this mic; it reads 0.01 on room sound,
  0.1-0.6 on music); a line older than 5 s means unknown (stock behaviour). While not music: ``_try_seed`` returns
  False, an unconfirmed grid is dropped, ``_update_state`` reads locked as holding, ``_update_gate`` sets "none" at
  once. Music after >= 3 s without: ``_drop_grid`` + ``_prior_c = None`` + ``_last_locked_t = -1e9`` + tempogram and
  peaks reset (VarClock._on_ced, _cold_restart).
- (a) in ``_after_beat``: confirmed grid, locked or holding, not quiet, conf < 0.2 for >= 6 s and >= 4 peaks in the
  last 4 s -> the same cold restart without the tempogram reset (VarClock._after_beat). Dropping the running tempo
  prior matters: ``_prior`` otherwise pulls the new seed back to the stuck tempo for 60 s.
- (d), (c), (e), s, peaks: not recommended on this evidence (tables above / clockvar_report.py). (c) needs the
  worker's Beat This! to normalise its window level first (models.BeatsTask; bt_lane.py shows it); then
  ``Clock.observe_bt(line)`` from mllink on ML_BEATS (VarClock._on_bt) seeds grids on the full tracks.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import json
import math
import pathlib
import subprocess
import sys
import time
from typing import Any, Optional

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import clockvar_replay as cr  # noqa: E402  (sets up sys.path for run_stack and show_service)

import numpy as np  # noqa: E402

import baseline  # noqa: E402
import run_batch  # noqa: E402
from show_service import clock as C  # noqa: E402

OUT = pathlib.Path("~/.cache/screen-machine/beat-eval/clockvar").expanduser()
SUBSETS = {"dev": pathlib.Path("~/.cache/screen-machine/beat-eval/dev-subset.json").expanduser(),
           "test": baseline.SUBSET}
RT_PY = pathlib.Path("~/.venvs/sm-roundtrip/bin/python").expanduser()

TRACE_FIELDS = ("t", "kind", "h", "prec", "clar", "cterm", "conf0", "conf", "confirmed", "state", "gate", "bpm",
                "agree", "coh", "npk", "nhit8", "sig", "t_next", "music", "clar1", "tgph")


# =====================================================================================================================
# the variant clock
# =====================================================================================================================
DEFAULTS: dict = {
    "b": {"m_on": 0.10, "m_off": 0.04, "avg": 2, "off_n": 4, "reseed_s": 3.0, "stale_s": 5.0, "gate": True,
          "tg_reset": True},
    "a": {"conf": 0.2, "hold_s": 6.0, "rhythm": 4, "clear_prior": True},
    "d": {"mode": "mix", "hterm": "agree", "prec": "obs", "lo": 0.2, "hi": 0.6},
    "c": {"conf": 0.45, "min_beats": 8, "cv_max": 0.08},
    "e": {"gain": 0.15, "clar": 0.15, "on": "miss", "max_ph": 0.25},
    "s": {"k": 3, "p": 1.0},
}


class VarPeaks(C._Peaks):
    """(iv) clock._Peaks with its thresholds as parameters: a peak must reach max(floor, l_frac x L, mu_mult x the
    0.5 s local mean) (stock: the activation's floor, 0.3, 1.5), ``floor`` overriding the activation's own."""

    def __init__(self, l_frac: float = 0.3, mu_mult: float = 1.5, floor: Optional[float] = None) -> None:
        self.l_frac, self.mu_mult, self.floor_over = float(l_frac), float(mu_mult), floor
        super().__init__()

    def push(self, a: float, t: float, floor: float):
        if self.floor_over is not None:
            floor = float(self.floor_over)
        self.mu += (a - self.mu) * self._mu_a
        av, tv = self.a, self.t
        av.pop(0)
        av.append(a)
        tv.pop(0)
        tv.append(t)
        self.n += 1
        self.L = max(self.L * self._dec, self.L_floor)
        thr = max(floor, self.l_frac * self.L, self.mu_mult * self.mu)
        out = max(0.0, a - self.mu) if a >= thr else 0.0
        if self.n < len(av):
            return out, None
        c = self.W
        h = av[c]
        if h <= av[c - 1] or h < max(av):
            return out, None
        if h < thr or h <= 0.0:
            return out, None
        den = av[c - 1] - 2.0 * h + av[c + 1]
        d = 0.5 * (av[c - 1] - av[c + 1]) / den if den < 0.0 else 0.0
        d = min(0.5, max(-0.5, d))
        hp = h - 0.25 * (av[c - 1] - av[c + 1]) * d
        if hp > self.L:
            self.L = hp
        return out, (tv[c] + d / C.FPS, min(1.5, hp / max(self.L, 1e-12)), hp)


class VarClock(C.Clock):
    """show_service's Clock with the variants' switches (``var``: {"b": {...}, "a": {...}, "d": {...}, ...}; a key
    present turns that variant on, its dict overrides DEFAULTS). Off, every method is the stock one."""

    def __init__(self, publish=None, *, var: Optional[dict] = None, **kw: Any) -> None:
        self.var = {k: ({**DEFAULTS.get(k, {}), **(v or {})} if isinstance(v, dict) or v is None else v)
                    for k, v in (var or {}).items()}
        self._trace: list[list] = []
        self._cedq: collections.deque = collections.deque()
        self._btq: collections.deque = collections.deque()
        self._vcount: collections.Counter = collections.Counter()
        super().__init__(publish, **kw)
        pk = self.var.get("peaks")
        if pk is not None:                                # (iv) the peak picker's thresholds
            self._pk = VarPeaks(**pk)
            self._pk.reset()

    # ---- state ------------------------------------------------------------------------------------------------------
    def reset(self) -> None:
        super().reset()
        self._music: Optional[bool] = None
        self._ced_t = -1e9
        b = self.var.get("b") or {}
        self._ced_hist: collections.deque = collections.deque(maxlen=max(int(b.get("avg", 2)), int(b.get("off_n", 4))))
        self._nm_since: Optional[float] = None
        self._low_since: Optional[float] = None
        self._terms: dict = {}

    def stats(self) -> dict:
        s = super().stats()
        s.update({f"var_{k}": v for k, v in self._vcount.items()})
        return s

    # ---- (b) the CED word -------------------------------------------------------------------------------------------
    def observe_ced(self, line: dict) -> None:
        """A CED line (any thread; mllink would call it on each ``ced`` line): drained on show-rx."""
        self._cedq.append(line)

    def _drain(self) -> None:
        while self._cedq:
            self._on_ced(self._cedq.popleft())
        while self._btq:
            self._on_bt(self._btq.popleft())
        super()._drain()

    # ---- (c) Beat This! seeds ---------------------------------------------------------------------------------------
    def observe_bt(self, line: dict) -> None:
        """A slow-lane ``beats`` line (any thread; mllink would call it on each one, besides its beat vote)."""
        self._btq.append(line)

    def _on_bt(self, line: dict) -> None:
        """Seed (or re-seed) from a Beat This! window when the clock has no confirmed grid, or a weak one (conf under
        c.conf, gate not "beat") at a tempo unrelated to the window's (x1, x2, x1/2 within 4 % are the integer
        votes' business). The window's interior beats give the period (an LS line through them, on the median
        IBI's count) and a phase, carried to now by beat count; the fast lane's last 4 s of peaks then set the
        phase when they agree with that period (strength-weighted phase coherence >= c.coh)."""
        c = self.var.get("c")
        if c is None:
            return
        beats = np.array(sorted(float(b) for b in (line.get("beats") or [])), float)
        if len(beats) < c["min_beats"]:
            self._vcount["bt_few"] += 1
            return
        med = float(np.median(np.diff(beats)))
        if not 60.0 / C.MAX_BPM <= med <= 60.0 / C.MIN_BPM:
            self._vcount["bt_range"] += 1
            return
        k = np.round((beats - beats[0]) / med)
        if len(np.unique(k)) != len(k):
            self._vcount["bt_irregular"] += 1
            return
        km, tm = k.mean(), beats.mean()
        T = float(np.dot(k - km, beats - tm) / np.dot(k - km, k - km))
        a0 = tm - T * km
        res = beats - (a0 + T * k)
        if float(np.std(res)) > c["cv_max"] * T:
            self._vcount["bt_irregular"] += 1
            return
        if self._not_music():
            self._vcount["bt_not_music"] += 1
            return
        if self._grid and self._confirmed:
            if self._gate == "beat" or self._conf >= c["conf"]:
                return
            r = (60.0 / self._T) / (60.0 / T)
            if any(abs(r / f - 1.0) <= C.ALT_SAME for f in (1.0, 2.0, 0.5)):
                self._vcount["bt_related"] += 1
                return
        tk = self._t_known
        t_b = a0 + math.floor((tk - a0) / T) * T
        pts = [(tp, s) for tp, s, _h in self._recent if tp >= tk - 4.0]
        if len(pts) >= 4:
            z = sum(s * complex(math.cos(2 * math.pi * (tp - t_b) / T), math.sin(2 * math.pi * (tp - t_b) / T))
                    for tp, s in pts)
            coh = abs(z) / sum(s for _tp, s in pts)
            if coh >= c.get("coh", 0.3):
                t_b += math.atan2(z.imag, z.real) / (2 * math.pi) * T
                self._vcount["bt_phase_fast"] += 1
        self._seed(60.0 / T, t_b, clarity=max(self._clar, 0.3), confirmed=bool(c.get("confirm", False)),
                   hit_fill=float(c.get("fill", 0.5)))
        if c.get("confirm", False):
            self._bar_anchor = self._k_next + self._off
            self._bar_conf = 0.1
        self._prior_c = math.log2(60.0 / T)
        self._vcount["bt_seeds"] += 1
        self._conf_update()
        self._update_state()

    def _on_ced(self, line: dict) -> None:
        b = self.var.get("b")
        if b is None:
            return
        m = float(line.get("music_p") or 0.0)
        t = float(line.get("t_end") or self._t_known)
        self._ced_hist.append(m)
        h = list(self._ced_hist)
        na = max(1, int(b["avg"]))
        mm = sum(h[-na:]) / len(h[-na:])               # on: the mean of the last ``avg`` lines reaches m_on
        off = len(h) >= int(b["off_n"]) and max(h[-int(b["off_n"]):]) < b["m_off"]   # off: all of the last off_n under
        self._ced_t = t
        was = self._music
        if was is None:
            self._music = mm >= b["m_on"]
            if not self._music:
                self._nm_since = t
        elif was and off:
            self._music, self._nm_since = False, t
            self._vcount["ced_music_off"] += 1
        elif not was and mm >= b["m_on"]:
            self._music = True
            self._vcount["ced_music_on"] += 1
            if self._nm_since is not None and t - self._nm_since >= b["reseed_s"]:
                self._cold_restart("ced_reseeds", tg=b["tg_reset"])
        if self._music is False and self._grid and not self._confirmed:
            self._drop_grid()                           # a tentative grid seeded on room sound
            self._vcount["ced_drops"] += 1

    def _not_music(self) -> bool:
        """(b): a fresh CED word says the room isn't hearing music (no line for stale_s: unknown, as stock)."""
        b = self.var.get("b")
        return bool(b) and self._music is False and self._t_known - self._ced_t <= b["stale_s"]

    def _cold_restart(self, why: str, tg: bool = False) -> None:
        """Drop the grid and the running tempo prior (and, with ``tg``, the tempogram and peaks: the seed then reads
        only what comes next)."""
        if self._grid:
            self._drop_grid()
        self._prior_c = None
        self._last_locked_t = -1e9
        self._seed_prev = None
        self._R_s = None
        if tg:
            self.tg.reset()
            self._recent.clear()
            self._peaks.clear()
        self._conf_update()
        self._update_state()
        self._vcount[why] += 1

    # ---- seeding ----------------------------------------------------------------------------------------------------
    def _try_seed(self) -> bool:
        if self._not_music():
            self._seed_prev = None
            return False
        if self.var.get("s") is None:
            return super()._try_seed()
        return self._try_seed_s(self.var["s"])

    def _comb_coh(self, T: float) -> float:
        """The best strength-weighted phase coherence of the last 4 s of peaks over periods T +-6 % (the comb
        _fit_peaks searches): 1 = every peak on one grid, ~0 = no grid."""
        tk = self._t_known
        pts = [(tp, s) for tp, s, _h in self._recent if tp >= tk - 4.0]
        if len(pts) < 4:
            return 0.0
        tp = np.array([p[0] for p in pts]) - tk
        sp = np.array([p[1] for p in pts])
        Ts = T * (1.0 + np.arange(-0.06, 0.0601, 0.01))
        v = np.exp(2j * np.pi * tp[None, :] / Ts[:, None]) @ sp
        return float(np.max(np.abs(v)) / sp.sum())

    def _try_seed_s(self, sv: dict) -> bool:
        """(s) the stock _try_seed, but the candidate is chosen among the tempogram's top ``k`` local maxima (score =
        clarity x prior) by score x comb coherence ** ``p`` of the recent peaks at that period (a tempo the peaks
        don't line up on loses to one they do)."""
        tg = self.tg
        if tg.filled < C.SEED_MIN_FRAMES:
            return False
        X = tg.spectrum()
        x0 = X[0].real
        if x0 <= C.X0_MIN:
            return False
        R = np.abs(X[1:]) / x0
        self._R_s = R if self._R_s is None else self._R_s + 0.4 * (R - self._R_s)
        Rs = self._R_s
        score = Rs * self._prior(self._t_known)
        score[tg.bpm > C.MAX_BPM] = 0.0
        loc = np.ones(tg.K, bool)
        loc[1:] &= Rs[1:] >= Rs[:-1]
        loc[:-1] &= Rs[:-1] >= Rs[1:]
        score[~loc] = 0.0
        order = [int(j) for j in np.argsort(-score)[:int(sv.get("k", 3))] if score[j] > 0.0 and Rs[j] >= C.SEED_MIN_CLARITY]
        if not order:
            self._seed_prev = None
            return False
        pw = float(sv.get("p", 1.0))
        best = max(order, key=lambda j: score[j] * max(1e-3, self._comb_coh(60.0 / float(tg.bpm[j]))) ** pw)
        i = best
        d = 0.0
        if 0 < i < tg.K - 1:
            a, b, c = Rs[i - 1], Rs[i], Rs[i + 1]
            den = a - 2.0 * b + c
            if den < 0.0:
                d = min(0.5, max(-0.5, 0.5 * (a - c) / den))
        bpm = float(tg.bpm[i] + d * tg.step)
        if self._seed_prev is None or abs(bpm / self._seed_prev - 1.0) > 0.02:
            self._seed_prev = bpm
            return False
        T, t_b, fit = self._fit_peaks(60.0 / bpm, tg.beat_time(X, i))
        if 60.0 / T > C.FAST_BPM and 30.0 / T >= C.MIN_BPM:
            vote, par = self._kick_vote(T, t_b, self._t_known)
            if vote < 0 or (vote == 0 and 60.0 / T > C.SEED_CAP_BPM):
                self._counts["seed_halved"] += 1
                T, t_b, fit = self._fit_peaks(2.0 * T, t_b + par * T, comb=vote == 0)
        if i != int(np.argmax(score)):
            self._vcount["seed_reranked"] += 1
        self._seed(60.0 / T, t_b, clarity=float(R[i]), fit=fit)
        return True

    # ---- confidence -------------------------------------------------------------------------------------------------
    def _agreement(self) -> tuple[float, float, int, int]:
        """Over the last 8 beats of the current grid (back from the next beat): the share of peak strength within
        +-10 % of a period of a grid beat (``agree``), the strength-weighted phase coherence of the peaks
        (``coh``), the number of peaks, and the number of those 8 beats with a peak on them."""
        T = self._T
        g0 = self._t_next
        lo = g0 - 8.5 * T
        hi = g0 - 0.5 * T
        num = den = 0.0
        cs = sn = 0.0
        on: set[int] = set()
        n = 0
        for tp, s, _h in self._recent:
            if tp < lo or tp >= hi:
                continue
            q = (tp - g0) / T
            k = round(q)
            e = q - k
            n += 1
            den += s
            cs += s * math.cos(2 * math.pi * e)
            sn += s * math.sin(2 * math.pi * e)
            if abs(e) <= 0.1:
                num += s
                on.add(k)
        if den <= 0:
            return 0.0, 0.0, 0, 0
        return num / den, math.hypot(cs, sn) / den, n, len(on)

    def _tg_phase(self) -> tuple[float, float]:
        """The tempogram at our tempo (its nearest 1-bpm bin): clarity there, and where its PLP phase puts the beat
        relative to our grid (a fraction of a period, -0.5..0.5; 0 = the activation's pulse is on our beats)."""
        tg = self.tg
        bpm = 60.0 / self._T
        if not tg.filled or not (tg.bpm_lo <= bpm <= tg.bpm[-1]):
            return 0.0, 0.5
        X = tg.spectrum()
        c1 = tg.clarity(X, bpm)
        i = int(round((bpm - tg.bpm_lo) / tg.step))
        tb = tg.beat_time(X, min(max(i, 0), tg.K - 1))
        return c1, ((tb - self._t_next) / self._T + 0.5) % 1.0 - 0.5

    def _conf_update(self) -> None:
        super()._conf_update()
        if not self._grid:
            self._terms = {}
            return
        h = self._hit_rate()
        if self._res:
            r = np.fromiter(self._res, float, len(self._res))
            sig = max(0.003, math.sqrt(float(np.mean(r * r))))
            frac = float(np.mean(np.abs(r) < C.CONF_X_S))
            prec = 0.5 * frac + 0.5 * math.erf(C.CONF_X_S / (math.sqrt(2.0) * sig))
            n = min(32, self._n_innov)
            prec = (n * prec + 3.0 * 0.35) / (n + 3.0)
        else:
            sig, prec = math.nan, 0.35
        cterm = 0.3 + 0.7 * C._smoothstep(0.08, 0.35, self._clar)
        agree, coh, npk, nhit = self._agreement()
        clar1, tgph = self._tg_phase()
        conf0 = self._conf
        self._terms = {"h": h, "prec": prec, "clar": self._clar, "cterm": cterm, "conf0": conf0, "agree": agree,
                       "coh": coh, "npk": npk, "nhit8": nhit, "sig": sig, "clar1": clar1, "tgph": tgph,
                       "tgcos": clar1 * math.cos(2 * math.pi * tgph)}
        d = self.var.get("d")
        if d is not None:
            self._conf = self._conf_d(d, self._terms)

    def _conf_d(self, d: dict, x: dict) -> float:
        """(d) the confidence from other terms. ``mode``:
        - ``mix`` (default): H x P x clarity term. H (``hterm``): ``h`` the stock hit rate, ``agree`` (the share of
          peak strength on the grid), ``coh`` (the peaks' phase coherence) or ``agreecoh`` (their geometric mean),
          each but ``h`` through smoothstep(lo, hi). P (``prec``): ``obs`` the stock P(an observation within 25 ms),
          or ``est`` P(the estimate within 25 ms): the innovations' spread through the alpha-beta loop
          (sigma x sqrt(alpha / (2 - alpha))), shrunk toward 0.35 like the stock one. ``clar``: keep the clarity
          term (default true).
        - ``logit``: a logistic over the terms, fitted on the dev subset (clockvar_report.py --fit).
        Unconfirmed grids stay capped at ``cap`` (0.3, as stock)."""
        mode = d.get("mode", "mix")
        if mode == "mix":
            ht = d.get("hterm", "agree")
            lo, hi = float(d.get("lo", 0.2)), float(d.get("hi", 0.6))
            if ht == "h":
                H = x["h"]
            elif ht == "agree":
                H = C._smoothstep(lo, hi, x["agree"])
            elif ht == "coh":
                H = C._smoothstep(lo, hi, x["coh"])
            elif ht == "agreecoh":
                H = C._smoothstep(lo, hi, math.sqrt(max(0.0, x["agree"] * x["coh"])))
            elif ht == "tg":                             # the tempogram's pulse on our phase, as clear as it is
                H = C._smoothstep(lo, hi, x["clar1"] * max(0.0, math.cos(2 * math.pi * x["tgph"])))
            else:
                raise ValueError(f"d.hterm {ht!r}")
            if d.get("prec", "obs") == "est":
                sig = x["sig"] if math.isfinite(x["sig"]) else 0.1
                se = max(0.002, sig * math.sqrt(C.ALPHA / (2.0 - C.ALPHA)))
                P = math.erf(C.CONF_X_S / (math.sqrt(2.0) * se))
                n = min(32, self._n_innov)
                P = (n * P + 3.0 * 0.35) / (n + 3.0)
            else:
                P = x["prec"]
            c = H * P * (x["cterm"] if d.get("clar", True) else 1.0)
            if d.get("nhit_full"):
                c *= min(1.0, x["nhit8"] / float(d["nhit_full"]))
        elif mode == "logit":                            # a fitted logistic over the terms (clockvar_report --fit)
            w = d["w"]
            z = w.get("b0", 0.0) + sum(w.get(k, 0.0) * float(x[k] if math.isfinite(x[k]) else 0.0)
                                       for k in ("h", "prec", "clar", "cterm", "agree", "coh", "nhit8", "npk", "clar1",
                                                 "tgcos"))
            z += w.get("sig", 0.0) * (min(0.1, x["sig"]) if math.isfinite(x["sig"]) else 0.1)
            c = 1.0 / (1.0 + math.exp(-z))
        else:
            raise ValueError(f"d.mode {mode!r}")
        if not self._confirmed:
            c = min(c, d.get("cap", 0.3))
        return min(0.99, max(0.0, c))

    # ---- state, gate, escape ----------------------------------------------------------------------------------------
    def _update_state(self) -> None:
        super()._update_state()
        if self._state == "locked" and self._not_music():
            self._state = "holding"

    def _update_gate(self, t: float) -> None:
        b = self.var.get("b")
        if b is not None and b.get("gate", True) and self._not_music():
            if self._gate != "none":
                self._gate, self._gate_t, self._gate_up = "none", t, False
            return
        super()._update_gate(t)

    def _refine(self, t_a: float) -> float:
        """``refine``: {"off": true} keeps the activation peak's own time (no low-band hit replaces it, no lag
        learning); {"min_s": x} lets only hits of strength >= x refine."""
        r = self.var.get("refine")
        if r is None:
            return super()._refine(t_a)
        if r.get("off"):
            return t_a
        keep = self._hitq
        self._hitq = collections.deque((h for h in keep if h[1] >= r.get("min_s", 0.0)), maxlen=keep.maxlen)
        try:
            return super()._refine(t_a)
        finally:
            self._hitq = keep

    def _process_beat(self) -> bool:
        """(e) the tempogram's PLP phase as a weak phase observation: after a judged beat (a miss, or every beat),
        the next beat moves ``gain`` of the way to where the PLP kernel at our tempo puts it, while the tempogram is
        that clear at our tempo (clarity >= ``clar``) and the grid is confirmed."""
        n_miss = self._counts["misses"]
        changed = super()._process_beat()
        e = self.var.get("e")
        if e is not None and self._grid and self._confirmed and (e.get("on", "miss") == "all"
                                                                or self._counts["misses"] > n_miss):
            c1, ph = self._tg_phase()
            if c1 >= e["clar"] and abs(ph) <= e.get("max_ph", 0.25):
                self._t_next += e["gain"] * ph * self._T
                self._vcount["tg_nudges"] += 1
        return changed

    def _after_beat(self, k: int, tb: float, kind: str, strength: float, entry: bool) -> bool:
        changed = super()._after_beat(k, tb, kind, strength, entry)
        a = self.var.get("a")
        if a is not None and self._grid and self._confirmed and self._state in ("locked", "holding") \
                and not self._quiet:
            if self._conf < a["conf"]:
                if self._low_since is None:
                    self._low_since = tb
                elif tb - self._low_since >= a["hold_s"]:
                    pk = sum(1 for tp, _s, _h in self._recent if tp >= tb - 4.0)
                    if pk >= a["rhythm"]:
                        self._low_since = None
                        self._cold_restart("stale_escapes", tg=False) if a["clear_prior"] else self._drop_grid()
                        self._update_gate(tb)
                        changed = True
            else:
                self._low_since = None
        else:
            self._low_since = None
        x = self._terms
        self._trace.append([tb, kind, x.get("h"), x.get("prec"), x.get("clar"), x.get("cterm"), x.get("conf0"),
                            self._conf, self._confirmed, self._state, self._gate, 60.0 / self._T if self._grid else 0.0,
                            x.get("agree"), x.get("coh"), x.get("npk"), x.get("nhit8"), x.get("sig"),
                            self._t_next if self._grid else None, self._music, x.get("clar1"), x.get("tgph")])
        return changed


@contextlib.contextmanager
def consts(over: Optional[dict]):
    """Module constants of show_service.clock for one replay (FAST_BPM, SEED_CAP_BPM, BEAT_ON ...), put back after."""
    old = {}
    try:
        for k, v in (over or {}).items():
            old[k] = getattr(C, k)
            setattr(C, k, v)
        yield
    finally:
        for k, v in old.items():
            setattr(C, k, v)


def trace_out(clock: Any, zero: float) -> dict:
    rows = []
    for r in getattr(clock, "_trace", []):
        r = list(r)
        r[0] = round(r[0] - zero, 4)
        if r[17] is not None:
            r[17] = round(r[17] - zero, 4)
        rows.append([round(v, 4) if isinstance(v, float) and math.isfinite(v) else
                     (None if isinstance(v, float) else v) for v in r])
    return {"trace_fields": list(TRACE_FIELDS), "trace": rows}


# =====================================================================================================================
# variants by name
# =====================================================================================================================
def parse_variant(name: str) -> dict:
    """``base`` | parts joined by ``+``: ``b``, ``a``, ``d``, ``c``, each with optional ``:k=v,k=v`` overrides,
    ``nets=8``, ``consts:FAST_BPM=150,SEED_CAP_BPM=170``, ``d:mode=logit,fit=<name>`` (a fit saved by
    clockvar_report.py --fit, in OUT/fits/<name>.json)."""
    spec: dict = {"name": name, "var": {}, "nets": 2, "consts": {}}
    if name in ("", "base", "stock"):                    # base: the recording itself; stock: VarClock, no switches
        return spec
    for part in name.split("+"):
        key, _, rest = part.partition(":")
        if key.startswith("nets="):
            spec["nets"] = int(key[5:])
            continue
        kv = {}
        for item in filter(None, rest.split(",")):
            k, _, v = item.partition("=")
            try:
                kv[k] = json.loads(v)
            except ValueError:
                kv[k] = v
        if key == "consts":
            spec["consts"].update(kv)
        elif key in ("a", "b", "c", "d", "e", "s", "peaks", "refine"):
            if key == "d" and kv.get("fit"):
                fit = json.loads((OUT / "fits" / f"{kv['fit']}.json").read_text())
                kv = {"mode": "logit", "w": fit["w"], **{k: v for k, v in kv.items() if k != "fit"}}
            spec["var"][key] = kv
        else:
            raise SystemExit(f"unknown variant part {part!r}")
    return spec


def needs_ced(spec: dict) -> bool:
    return "b" in spec["var"]


def needs_slow(spec: dict) -> bool:
    return "c" in spec["var"]


def _bt_hook(rp: Any, h: float, line: dict) -> None:
    if line.get("type") == "beats" and hasattr(rp.clock, "observe_bt"):
        rp.clock.observe_bt(line)


def replay_variant(argv: list[str], cache: cr.Cache, spec: dict) -> dict:
    """One variant on one cached track. (c) turns the slow lane on (run_stack's own schedule and votes; Beat This!
    from the track's beats cache, computed on first use)."""
    kw = {"var": spec["var"]}
    cand = {"candidate": "ourclock", "variant": spec["name"], "var": spec["var"], "nets": spec["nets"],
            "consts": spec["consts"]}
    if needs_slow(spec):
        argv = [x for x in argv if x != "--no-slow-lane"]
    with consts(spec["consts"]):
        return cr.replay(argv, cache, VarClock, kw, nets=spec["nets"], candidate=cand, ced=needs_ced(spec),
                         trace_fn=trace_out, on_ml_line=_bt_hook if needs_slow(spec) else None,
                         ced_norm=ced_norm(spec), bt_norm=(spec["var"].get("c") or {}).get("bt_norm"),
                         candidate_extra={"var_resolved": {k: ({**DEFAULTS.get(k, {}), **v} if isinstance(v, dict)
                                                               else v) for k, v in spec["var"].items()}})


def ced_norm(spec: dict) -> Optional[float]:
    return (spec["var"].get("b") or {}).get("ced_norm")


# =====================================================================================================================
# batch
# =====================================================================================================================
def cond_args(cond: str) -> list[str]:
    return list(baseline.conditions()[cond]["args"])


def track_argv(e: dict, cond: str, out: pathlib.Path, seed0: int = 0, dur_cap: float = baseline.DUR_CAP_S) -> list:
    """run_stack's argv for one subset entry, exactly as baseline.py -> run_batch.py builds it."""
    argv = [str(e["audio"]), "--out", str(out), "--id", e["id"], "--dataset", e["dataset"], "--genre", e["genre"],
            "--seed", str(run_batch.track_seed(seed0, e))]
    if e.get("beats") and pathlib.Path(e["beats"]).exists():
        argv += ["--beats", str(e["beats"])]
    if e.get("start_s"):
        argv += ["--start-s", str(e["start_s"])]
    dur = dur_cap if dur_cap is not None else e.get("dur_s")
    if dur:
        argv += ["--dur-s", str(dur)]
    return argv + cond_args(cond)


def entries(subset: str, datasets: Optional[str] = None, only: Optional[str] = None) -> list[dict]:
    path = SUBSETS.get(subset, pathlib.Path(subset).expanduser())
    _, es = run_batch.load_index(path)
    if datasets:
        want = {s.strip() for s in datasets.split(",")}
        es = [e for e in es if e["dataset"] in want]
    if only:
        es = [e for e in es if only in f"{e['dataset']}/{e['id']}"]
    return es


def res_dir(subset: str, cond: str, variant: str) -> pathlib.Path:
    return OUT / f"{subset}-{cond}" / cr.safe(variant.replace(":", "~").replace("+", "_"))


def ensure_cache(argv_base: list[str], cache_p: pathlib.Path, base_out: pathlib.Path, ced: Any,
                 seed: int, log=print) -> cr.Cache:
    """The track's cache (recorded now if missing) and the CED lanes ``ced`` asks for (False/None: none; True:
    the raw one; a list of norms, None meaning raw)."""
    if not cache_p.exists():
        t = time.time()
        m = cr.record(argv_base, cache_p)
        log(f"    recorded in {time.time() - t:.0f} s (madmom {m['act_ms_per_s']:.0f} ms per audio s)")
    cache = cr.Cache(cache_p)
    norms = [None] if ced is True else (list(ced) if ced else [])
    for norm in norms:
        if cache.ced(norm) is None:
            t = time.time()
            cr.ced_prepass(cache, seed, norm=norm)
            log(f"    ced lane{'' if norm is None else f' (norm {norm:g})'} in {time.time() - t:.0f} s")
    return cache


def cmd_batch(a: argparse.Namespace) -> int:
    cr.nice19()
    specs = [parse_variant(v) for v in a.variants.split(";") if v.strip()] if ";" in a.variants else \
        [parse_variant(v) for v in a.variants.split(",") if v.strip()]
    es = entries(a.subset, a.datasets, a.only)
    t_all = time.time()
    slices = cr.LockSlices(not a.no_lock, a.slice_s)
    for cond in a.conds.split(","):
        dirs = {s["name"]: res_dir(a.subset, cond, s["name"]) for s in specs}
        base_dir = res_dir(a.subset, cond, "base")
        for d in [*dirs.values(), base_dir]:
            d.mkdir(parents=True, exist_ok=True)
        for i, e in enumerate(es, 1):
            name = f"{cr.safe(e['dataset'])}__{cr.safe(e['id'])}.json"
            todo = [s for s in specs if s["name"] != "base" and (a.redo or not (dirs[s["name"]] / name).exists())]
            argv0 = track_argv(e, cond, base_dir / name)
            cache_p = cr.cache_path(argv0)
            if not todo and cache_p.exists() and (base_dir / name).exists():
                continue
            if a.no_lock and not cache_p.exists():           # never record (heavy) without the CPU lock
                print(f"[{cond} {i}/{len(es)}] {e['dataset']}/{e['id']}: no cache yet, skipped (--no-lock)")
                continue
            with slices.track() as waited:
                t = time.time()
                try:
                    cache = ensure_cache(argv0, cache_p, base_dir / name,
                                         sorted({ced_norm(s) for s in todo if needs_ced(s)}, key=str),
                                         run_batch.track_seed(0, e))
                except (cr.CacheMismatch, RuntimeError, FileNotFoundError) as ex:
                    print(f"[{cond} {i}/{len(es)}] {e['dataset']}/{e['id']}: FAILED {type(ex).__name__}: {ex}")
                    continue
                for s in todo:
                    try:
                        if s["nets"] != 2 and not cache.side(f"act{s['nets']}.npz").exists():
                            bn = res_dir(a.subset, cond, f"base-nets{s['nets']}")     # madmom with N nets (heavy)
                            bn.mkdir(parents=True, exist_ok=True)
                            cr.record_act(track_argv(e, cond, bn / name), cache, s["nets"])
                        replay_variant(track_argv(e, cond, dirs[s["name"]] / name), cache, s)
                    except (cr.CacheMismatch, RuntimeError, FileNotFoundError) as ex:
                        print(f"[{cond} {i}/{len(es)}] {e['dataset']}/{e['id']} {s['name']}: FAILED "
                              f"{type(ex).__name__}: {str(ex)[:200]}")
            print(f"[{cond} {i}/{len(es)}] {e['dataset']}/{e['id']}: {len(todo)} variants in {time.time() - t:.1f} s"
                  f" (lock wait {waited:.0f} s)", flush=True)
        slices.close()
        if not a.no_score:
            for s in specs:
                subprocess.run([str(RT_PY), str(baseline.HERE / "score.py"), str(dirs[s["name"]]), "--tracks", "off"],
                               capture_output=True, text=True)
    print(f"batch done in {time.time() - t_all:.0f} s")
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    """The stock clock replayed from the cache must flash exactly what the recording (the live path) flashed."""
    cr.nice19()
    es = entries(a.subset, a.datasets, a.only)
    bad = 0
    tmp = OUT / "check"
    tmp.mkdir(parents=True, exist_ok=True)
    for e in es[:a.n]:
        name = f"{cr.safe(e['dataset'])}__{cr.safe(e['id'])}.json"
        base = res_dir(a.subset, a.cond, "base") / name
        argv0 = track_argv(e, a.cond, base)
        cp = cr.cache_path(argv0)
        if not cp.exists() or not base.exists():
            continue
        r0 = json.loads(base.read_text())
        with cr.cpu_lock(not a.no_lock):
            r1 = cr.replay(track_argv(e, a.cond, tmp / name), cr.Cache(cp), C.Clock, {})
        same = r0["flashes"] == r1["flashes"] and r0["bodies"] == r1["bodies"]
        bad += not same
        print(f"{e['dataset']}/{e['id']}: {'identical' if same else 'DIFFERENT'} ({len(r0['flashes'])} vs "
              f"{len(r1['flashes'])} flashes); clock {r1['clock_cpu']['clock_ms_per_s']:.1f} ms/s, madmom "
              f"{r1['clock_cpu']['act_ms_per_s']} ms/s")
    return 1 if bad else 0


def cmd_record_act(a: argparse.Namespace) -> int:
    cr.nice19()
    es = entries(a.subset, a.datasets, a.only)
    slices = cr.LockSlices(not a.no_lock, a.slice_s)
    for cond in a.conds.split(","):
        out = res_dir(a.subset, cond, f"base-nets{a.nets}")
        out.mkdir(parents=True, exist_ok=True)
        for i, e in enumerate(es, 1):
            name = f"{cr.safe(e['dataset'])}__{cr.safe(e['id'])}.json"
            argv0 = track_argv(e, cond, out / name)
            cp = cr.cache_path(argv0)
            if not cp.exists():
                print(f"{e['id']}: no cache (batch first)")
                continue
            cache = cr.Cache(cp)
            if cache.side(f"act{a.nets}.npz").exists() and not a.redo:
                continue
            with slices.track() as waited:
                t = time.time()
                m = cr.record_act(argv0, cache, a.nets)
            print(f"[{cond} {i}/{len(es)}] {e['dataset']}/{e['id']}: nets {a.nets} {m['act_ms_per_s']:.0f} ms/s, "
                  f"{time.time() - t:.0f} s (lock wait {waited:.0f} s)", flush=True)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("batch", "check", "record-act"):
        p = sub.add_parser(name)
        p.add_argument("--subset", default="dev", help="dev | test | an index path")
        p.add_argument("--datasets", default=None)
        p.add_argument("--only", default=None, help="tracks whose dataset/id contains this")
        p.add_argument("--no-lock", action="store_true")
        p.add_argument("--redo", action="store_true")
        p.add_argument("--slice-s", type=float, default=45.0, help="hold the CPU lock this long over several tracks "
                                                                   "(then let the other builders have a turn)")
        if name == "batch":
            p.add_argument("--conds", default="A,C")
            p.add_argument("--variants", default="base", help="comma-separated (or ;-separated when a variant has "
                                                               "commas) variant names; see parse_variant")
            p.add_argument("--no-score", action="store_true")
        elif name == "check":
            p.add_argument("--cond", default="C")
            p.add_argument("-n", type=int, default=5)
        else:
            p.add_argument("--conds", default="A,C")
            p.add_argument("--nets", type=int, default=8)
    a = ap.parse_args(argv)
    return {"batch": cmd_batch, "check": cmd_check, "record-act": cmd_record_act}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
