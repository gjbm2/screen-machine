"""The beat clock's estimator (``clock.Clock(estimator="dbn")``, the default since 19 Oct): madmom's online DBN beat
tracker, rebuilt in numpy, published as our clock line with a fitted gate confidence.

Where it came from
------------------
A port of screen-machine-26's candidate ``tools/room/beat_eval/alt/madmom_dbn.py`` with ``VARIANTS["best2"]`` (2 nets,
lag 15 ms, conf cap until 2 beats, gate beat 0.80/0.60, dmx 0.60/0.35), measured offline on 19 Oct
(tools/room/beat_eval/IMPROVE_2026-10-19.md; tuned on the DEV subset, measured on the held-out baseline subset, 46
steady tracks, 30 s of room sound first, the slow lane off). Against the clock it replaces (``estimator="legacy"``):
- **in the room model** (C: PC22's mic chain and the room, the calibrated gate): F 0.54 against 0.36, coverage 0.21
  against 0.08, locked 0.46 against 0.28 of the time, time to lock 12.3 s against 15.3 s, at the same gated
  precision (0.89) and wrong-lock rate (0.068 against 0.067);
- **on clean audio** (A): F 0.83 against 0.75, coverage 0.77 against 0.61, precision 0.91 against 0.88, lock 3.8 s
  against 7.8 s;
- 0 of the 5 expressive pieces false-locked, in both conditions; on the real calibration recording (rec.wav: 164 s of
  room sound, then the track) the old clock never locked, while the DBN flashes no gated beat in the room sound and
  covers the track (rec-music.wav alone: a flash within 70 ms of 0.87 of the beats, first beat gate at 3.6 s);
- CPU: 122 ms of a core per second of music against 106 (madmom's 2 LSTMs are most of both; this tracker is ~8).
The live code is checked against the candidate flash for flash (the parity runs in 3a's dbn scratch: run_stack.py
on the live Clock against ``madmom_dbn.py run --variant best2`` on the same input).

What it is
----------
- ``BeatHMM``: madmom's BeatStateSpace + BeatTransitionModel + RNNBeatTrackingObservationModel (features/beats_hmm.py:
  45-200 bpm at 100 fps, transition lambda 100, observation lambda 16), its forward step as madmom's
  HiddenMarkovModel.forward computes it (tests/test_beatdbn.py checks it against madmom's own
  DBNBeatTrackingProcessor(online=True), frame for frame). A beat is reported when the forward path's best state
  enters the beat range, at most one per 60/max_bpm s (madmom's online rule).
- ``DbnTracker``: frames in (activation, host time, ``silent``, ``fresh``), a published line out:
  - **the line** (bpm, anchor, beat count: ``music.clock``'s fields) re-anchors on every DBN beat; its period is an
    LS fit over the last 8 beats on it (the DBN's integer-frame intervals jitter by a frame), or the posterior-mean
    interval while there are too few or the fit disagrees with the DBN by > 6 %. A beat more than 1/4 beat off the
    line, or at a tempo > 15 % off, re-phases it (``epoch`` + 1); the count goes on across missed beats and
    re-phases. Frame time - ``lag_ms`` (15) is a beat's time. This is the tracker's own estimate: the clock publishes
    a smoothed line on it, with its own count and epoch (clock.Clock._dbn_publish; README S4.15 "Publishing").
  - **conf** = P(the line's next beat lands within 70 ms of a real one): a logistic (``beatdbn_conf.json``, per net
    count; dbn_sweep.py ``calib`` on the DEV subset, A + C) of 20 of 22 features of what the DBN knows: the hit rate
    of its beats on the line (4/8/16 beats), the beats on this line, the posterior's tempo mass, phase concentration
    and octave mass, the line's LS residual and its agreement with the posterior, the predictive log-likelihood
    against a beatless model over 1/4/8 s (the best single feature), the activation's salience and contrast at the
    beats, the tempogram's clarity at the line's tempo (clock.Tempogram on clock._Peaks' input), the tempo (log2
    bpm/120 and its square). Capped at 0.3 until 2 beats are on the line. **Not** the legacy clock's
    P(within 25 ms): a 25 ms label made the room's conf collapse (its jitter is wider). With no weights for the net
    count (or no file) the hand-set product (hits x tempo mass^0.5 x phase x fit) is used.
  - **state** ``searching`` (no line yet) | ``locked`` (>= 2 DBN beats on the last 4 of the line's) | ``holding``
    (fewer, or the room is quiet) | ``free`` (no audio arriving, or a line from taps alone);
    **gate** with hysteresis (beat on 0.80 / off 0.60, dmx on 0.60 / off 0.35; 0.70 let rubato piano through) and a
    2-bar dwell, ``none`` at once while searching, quiet or without audio; **stability** = the posterior's tempo mass.
  - **resets**: the forward state starts over when the quiet gate ends and whenever the LSTM was reset (``fresh``: a
    segment, a hole, a switch of the beat process), as the activation does; a silent frame (no activation: the quiet
    room, a skipped hole) lets it wait. The line survives every reset.
- Off in best2 and kept (cheap): ``eps``, the ``challenger``, ``prior_oct``, ``act_norm``, ``hits_w`` (the candidate's
  docstring has what each one did; none moved C).

Added here, for the live clock (inert in a replay that has no corrections and continuous audio, so the parity holds)
-----------------------------------------------------------------------------------------------------------------------
- ``events``: the line's judged beats, in order, ``(k, kind, strength)`` (``hit`` a DBN beat on the line, ``miss`` a
  predicted beat that passed without one, ``rephase`` / ``new`` a DBN beat that moved the line's phase / its tempo),
  each hit 70 ms late with its activation peak as the strength: Clock's bar and phrase code reads them as it reads
  the legacy estimator's judged beats.
- Octaves (the human's half/double, the slow lane's octave) never touch the tracker: Clock's publisher maps its line
  (clock.Clock, "the octave map"; README S4.15), so the DBN tracks on at its own octave and its conf and gate stay
  its own. (Until 21 Oct a ``set_octave`` scaled this line and held the DBN off for HOLD_S; the DBN then took its
  own octave back about 6 s later.)
- ``tap(t, run)``: a tap that agrees with the line (within 0.15 beat) is one more hit; otherwise the line's nearest
  beat moves to it (and takes the taps' LS period when 4 or more agree; with no line, such a run starts one, "free"
  until a DBN beat lands, at TAP_CONF), the forward state re-seeded on the new line and the DBN's disagreement held
  off for HOLD_S (its beats off the line are ignored, and a reset re-seeds on the line), so it never snaps straight
  back.
- ``coast(t)``: no audio arriving: the predicted beats pass as misses on tick time, ``free``, gate ``none``.
- ``octave_evidence(t_ref, T, f)`` (21 Oct): the audio's word on an octave of a line, for Clock's gate on the slow
  lane's octave votes (README S4.15 "Octave evidence"): over the finer grid's last OCT_EV_N points heard, the mean
  activation peak at the points only it has (the beats a doubling adds, a halving drops) over the mean at the beats
  both grids keep, each above the activation's median there. It reads the last OCT_EV_S of frames (``_act_ev``, a
  record nothing else reads). ``period()``: the line's period over its last 16 beats (their median), the grid it
  is read on (the line's own jitters by 10 % beat to beat in a room); only the beats since the line's last new
  tempo count (before it they are another tempo's: the 21 Oct check).
Pure numpy and the stdlib (``tg`` imports clock's Tempogram and _Peaks): the Clock, a test or a replay drive one the
same way.
"""

from __future__ import annotations

import collections
import json
import logging
import math
import pathlib
from typing import Any, Optional

import numpy as np

log = logging.getLogger("show_service.beatdbn")

HERE = pathlib.Path(__file__).resolve().parent
CONF_FILE = HERE / "beatdbn_conf.json"
FPS = 100.0                     # clock.FPS: 44100 / 441
HOLD_S = 6.0                    # after a tap: the DBN's disagreement waits this long (a few bars)
SEED_TEMPO_SD = 0.02            # a re-seeded forward state: log-normal over the period (2 %) ...
SEED_PHASE_SD = 2.0             # ... and a Gaussian over the position (frames)
TAP_AGREE = 0.15                # a tap within this many beats of the line's agrees (clock's legacy rule)
TAP_CONF = 0.7                  # a line from taps alone: the DMX swell may follow it; the beat gate waits for audio
EVENTS_MAX = 256
OCT_EV_S = 20.0                 # octave_evidence: the activation kept (16 points of a 45 bpm halving's grid)
OCT_EV_N = 16                   # ... read over the finer grid's last this many points (8 kept beats, 8 between)

# -- the tracker's settings ---------------------------------------------------------------------------------------------
DEFAULTS: dict[str, Any] = {    # the candidate's defaults (madmom_dbn.DEFAULTS), verbatim
    "nets": 2,                  # how many of madmom's 8 BEATS_LSTM nets are averaged (picks the conf weights)
    "min_bpm": 45.0, "max_bpm": 200.0,  # our clock's range (madmom's own default is 55..215)
    "transition_lambda": 100.0, "observation_lambda": 16,      # madmom's defaults
    "lag_ms": 20.0,             # the activation's lag (clock.MadmomActivation.lag_s): frame time - this = beat time
    "fit_n": 8,                 # the line's period: LS over the last fit_n beats on it
    "fit_tol": 0.06,            # ... unless it disagrees with the DBN's posterior-mean period by more than this
    "phase_tol": 0.25,          # a DBN beat within this many beats of the line's is on it; else a re-phase
    "conf_mode": "logit",       # logit (the weights) | hand
    "conf_file": "",            # a JSON of logistic weights (dbn_sweep.py calib --out-w) in place of CONF_W
    "conf_cap_n": 4,            # conf <= conf_cap until this many beats are on the line (our clock's "confirmed")
    "conf_cap": 0.3,
    "beat_on": 0.70, "beat_off": 0.60, "dmx_on": 0.45, "dmx_off": 0.35, "dwell_bars": 2,
    "beats_per_bar": 4,
    "quiet_reset": True,        # the forward state starts over when the quiet gate ends (the LSTM does anyway)
    "act_reset": True,          # ... and whenever the LSTM is reset (a segment, a hole)
    "eps": 0.0,                 # uniform mass mixed back in each frame (0 = stock)
    "ch_every_s": 0.0,          # challenger: start one this often while not gated "beat" (0 = off)
    "ch_min_s": 6.0, "ch_max_s": 12.0, "ch_margin": 8.0,
    "ch_always": False,         # ... also while gated "beat"
    "tg": True,                 # run clock's tempogram (clock.Tempogram on clock._Peaks' input) for two features
    "prior_oct": 0.0,           # the forward state's start: log-normal over tempo around 120 bpm, this sigma in octaves
                                # (0 = madmom's uniform start)
    "act_norm": 0.0,            # > 0: the activation over its own recent peak level (max-held, tau 10 s, floor 0.05)
                                # x this, capped at 0.95, before the DBN (0 = madmom's raw activation)
    "hits_w": 0.0,              # dsp's sub/bass hits as extra beat evidence: a' = 1 - (1 - a)(1 - hits_w x strength)
    "hits_win_ms": 15.0,        # ... a hit within this of the frame's time (only hits already heard count)
}
BEST2: dict[str, Any] = {"nets": 2, "lag_ms": 15.0, "conf_cap_n": 2, "beat_on": 0.8, "beat_off": 0.6, "dmx_on": 0.6,
                         "dmx_off": 0.35}                     # madmom_dbn.VARIANTS["best2"]
SETTINGS: dict[str, Any] = {**DEFAULTS, **BEST2}            # what the live clock runs
FEATURES = ("h8", "h4", "m_tempo", "r_phase", "m_oct", "resid", "n_line", "sal", "contrast", "lb", "lb2", "a_mean",
            "llr", "tg_clar", "tg_rel", "llr8", "llr1", "n_log", "h16", "t_agree", "ibi_cv", "t_drift")


def settings(**over: Any) -> dict:
    """SETTINGS with ``over`` (keys of DEFAULTS only: anything else is a KeyError)."""
    bad = [k for k in over if k not in DEFAULTS]
    if bad:
        raise KeyError(f"not tracker settings: {bad}")
    return {**SETTINGS, **over}


def load_conf(path: "pathlib.Path | str" = CONF_FILE) -> dict:
    """``by_nets`` of the conf weights file (``{"2": {b, w, mu, sd}, "4": ..., "8": ...}``); {} if it can't be read
    (the hand-set conf then; logged)."""
    try:
        return dict(json.loads(pathlib.Path(path).read_text())["by_nets"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("beatdbn: conf weights %s unreadable (%s): the hand-set conf instead", path, e)
        return {}


# conf = sigmoid(b + sum w_k (x_k - mu_k) / sd_k), by the net count (the activation's spread differs)
CONF_W: dict[str, dict] = load_conf()


def _conf_w(cfg: dict) -> Optional[dict]:
    """The conf's logistic weights for these settings: ``conf_file``, else CONF_W for the net count, else None."""
    if cfg.get("conf_file"):
        return json.loads(pathlib.Path(cfg["conf_file"]).expanduser().read_text())
    return CONF_W.get(str(int(cfg["nets"])))


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, x))))


# -- madmom's beat HMM, in numpy -----------------------------------------------------------------------------------------
class BeatHMM:
    """madmom's BeatStateSpace + BeatTransitionModel + RNNBeatTrackingObservationModel (features/beats_hmm.py), the
    forward step as madmom's HiddenMarkovModel.forward computes it. State ``first[i] + m`` is interval ``iv[i]``
    (frames per beat) at position m / iv[i]; positions advance a frame a step, and only the last position of a beat
    moves to another interval (exponentially in the ratio, lambda). The beat range is the first 1/lambda_obs of a
    beat."""

    def __init__(self, min_bpm: float, max_bpm: float, fps: float, transition_lambda: float,
                 observation_lambda: int) -> None:
        lo, hi = 60.0 * fps / max_bpm, 60.0 * fps / min_bpm
        iv = np.arange(np.round(lo), np.round(hi) + 1).astype(int)
        self.iv = iv
        self.N = int(iv.sum())
        self.first = np.cumsum(np.r_[0, iv[:-1]]).astype(int)
        self.last = (np.cumsum(iv) - 1).astype(int)
        self.pos = np.concatenate([np.linspace(0, 1, i, endpoint=False) for i in iv])
        self.sint = np.repeat(iv, iv)
        self.sidx = np.repeat(np.arange(len(iv)), iv)          # each state's interval index
        ratio = iv[None, :].astype(float) / iv[:, None].astype(float)    # [from, to]
        P = np.exp(-float(transition_lambda) * np.abs(ratio - 1.0))
        P[P <= np.spacing(1)] = 0.0
        P /= P.sum(axis=1, keepdims=True)
        self.P = np.ascontiguousarray(P)                        # tempo transitions at the beat boundary
        self.beat = self.pos < 1.0 / float(observation_lambda)
        self.beat_idx = np.nonzero(self.beat)[0]
        self.nb_c = 1.0 / (float(observation_lambda) - 1.0)
        self.cos = np.cos(2 * np.pi * self.pos)
        self.sin = np.sin(2 * np.pi * self.pos)

    def uniform(self) -> np.ndarray:
        return np.full(self.N, 1.0 / self.N)

    def prior(self, sigma_oct: float, bpm0: float = 120.0, fps: float = FPS) -> np.ndarray:
        """A start over tempo: log-normal around ``bpm0`` (sigma in octaves), uniform over each beat's positions."""
        if sigma_oct <= 0.0:
            return self.uniform()
        bpm = 60.0 * fps / self.sint
        w = np.exp(-0.5 * (np.log2(bpm / bpm0) / sigma_oct) ** 2) / self.sint
        return w / w.sum()

    def seed(self, T_s: float, phase: float, tempo_sd: float = SEED_TEMPO_SD, phase_sd: float = SEED_PHASE_SD,
             fps: float = FPS) -> np.ndarray:
        """A forward variable on a line: period ``T_s`` (log-normal, ``tempo_sd``), ``phase`` beats since its last
        beat (a circular Gaussian of ``phase_sd`` frames). No mass anywhere else: the DBN keeps this tempo (its
        transitions move it by a few % a beat at most) until it is reset."""
        iv = self.iv
        w_iv = np.exp(-0.5 * (np.log(iv / max(1e-6, T_s * fps)) / tempo_sd) ** 2)
        w_iv /= max(1e-300, float(w_iv.max()))
        p = np.zeros(self.N)
        ph = float(phase) % 1.0
        for i in np.nonzero(w_iv > 1e-6)[0]:
            n = int(iv[i])
            d = (np.arange(n, dtype=float) - ph * n + 0.5 * n) % n - 0.5 * n
            w = np.exp(-0.5 * (d / phase_sd) ** 2)
            p[self.first[i]:self.first[i] + n] = w_iv[i] * w / w.sum()
        s = float(p.sum())
        return p / s if s > 0.0 and math.isfinite(s) else self.uniform()

    def step(self, p: np.ndarray, a: float) -> tuple[np.ndarray, float]:
        """One forward step: the new normalised forward variable and log of its normaliser (the predictive
        likelihood of ``a``). ``p`` is not modified."""
        q = np.empty(self.N)
        q[1:] = p[:-1]                                          # within a beat: position + 1
        q[self.first] = p[self.last] @ self.P                   # the beat boundary: any tempo, exponentially
        nb = (1.0 - a) * self.nb_c                              # the observation: (1 - a) / (lambda - 1) off the beat,
        q *= nb
        if nb > 0.0:
            q[self.beat_idx] *= a / nb                          # ... a in the beat range
        c = float(q.sum())
        if not (c > 0.0 and math.isfinite(c)):
            return self.uniform(), -50.0
        q *= 1.0 / c
        return q, math.log(c)


# -- the tracker ----------------------------------------------------------------------------------------------------------
_GATE_RANK = {"none": 0, "dmx": 1, "beat": 2}


class DbnTracker:
    """Frames in (activation, time), a published line out (see the module docstring). Show-rx only (``hit`` from
    any thread: a deque append). ``act_floor`` is the activation's peak floor (clock.MadmomActivation.floor) for the
    tempogram's peak picker."""

    def __init__(self, cfg: Optional[dict] = None, *, act_floor: float = 0.03) -> None:
        self.cfg = c = dict(SETTINGS, **(cfg or {}))
        self.hmm = BeatHMM(c["min_bpm"], c["max_bpm"], FPS, c["transition_lambda"], int(c["observation_lambda"]))
        self.lag_s = float(c["lag_ms"]) / 1000.0
        self.min_ibi = 60.0 / float(c["max_bpm"])
        self._log_ol = math.log(float(c["observation_lambda"]))
        self._p0 = self.hmm.prior(float(c["prior_oct"]))
        self.W = _conf_w(c)
        self._tg = self._pk = None
        self._tg_floor = float(act_floor)
        if c["tg"]:                                             # clock's tempogram: a second opinion on the tempo
            from . import clock as _clock
            self._tg = _clock.Tempogram(bpm_hi=_clock.GUARD_BPM)
            self._pk = _clock._Peaks()
        self.counts = collections.Counter()
        self._act: collections.deque = collections.deque(maxlen=int(4 * FPS))   # (t, a): the last 4 s
        self._act_ev: collections.deque = collections.deque(maxlen=int(OCT_EV_S * FPS))   # ... OCT_EV_S of it
        self._llr: collections.deque = collections.deque(maxlen=int(4 * FPS))   # log(16 c): vs a beatless model
        self._llr_sum8 = 0.0                                                     # ... an 8 s running sum of it
        self._llr8: collections.deque = collections.deque()
        self._hitq: collections.deque = collections.deque(maxlen=64)             # (t, strength): dsp's low hits
        self._an_L = 0.05
        self._an_dec = math.exp(-1.0 / (10.0 * FPS))
        self.quiet = False
        self.epoch = 0
        self.k_pub = 0                          # beats published so far (the count carries over re-phases)
        self._line: Optional[tuple[int, float, float]] = None     # (k, t_k, T)
        self._hist: collections.deque = collections.deque(maxlen=max(3, int(c["fit_n"])))
        self._hits: collections.deque = collections.deque(maxlen=16)
        self._k_judged = -1
        self._n_line = 0                        # beats on the line since it started or re-phased
        self._ibis: collections.deque = collections.deque(maxlen=16)   # the line's beat-to-beat periods (rubato)
        self._n_ibi = 0                         # ... of them since the line's last new tempo (period() reads these)
        self._sal: collections.deque = collections.deque(maxlen=8)      # activation peak at each beat (deferred)
        self._sal_wait: collections.deque = collections.deque()         # beat times waiting for their window
        self._x: dict = {}
        self.conf = 0.0
        self.gate = "none"
        self._gate_t = -1e9
        self._gate_up = True
        self.state = "searching"
        self.t_last = -1e9
        # the live clock's additions (module docstring): inert until a correction, a tap or no audio
        self.events: collections.deque = collections.deque(maxlen=EVENTS_MAX)   # (k, kind, strength), for Clock
        self._ev_wait: collections.deque = collections.deque()                  # (t, k, kind): hits for their window
        self.no_audio = False                   # coasting on tick time (Clock's NO_AUDIO_S)
        self.tapped = False                     # a line from taps that no DBN beat has landed on yet
        self._hold_until = -1e9                 # a tap holds the DBN's disagreement off until then
        self.hmm_reset()

    # -- the forward state
    def hmm_reset(self) -> None:
        """The forward state starts over: madmom's start (uniform, or ``prior_oct``), or, while a tap holds the line
        (HOLD_S), on the line itself."""
        ln = self._line
        if ln is not None and self.t_last < self._hold_until:
            self.p = self.hmm.seed(ln[2], (self.t_last - ln[1]) / ln[2])
            self.counts["hmm_reseeds"] += 1
        else:
            self.p = self._p0.copy()
        self.last_beat = -1e9
        self._ch: Optional[dict] = None
        self._ch_last = -1e9
        self.counts["hmm_resets"] += 1

    def set_quiet(self, quiet: bool, t: float) -> None:
        if quiet == self.quiet:
            return
        self.quiet = quiet
        if quiet:
            self.counts["quiet_starts"] += 1
            self.gate, self._gate_t, self._gate_up = "none", t, False
        else:
            self.counts["quiet_ends"] += 1
            if self.cfg["quiet_reset"]:
                self.hmm_reset()
        self._update_state()

    def key(self) -> tuple:
        """Changes whenever the published body's line or labels would (cheap: every frame)."""
        return (self._line, round(self.conf, 4), self.state, "none" if self.quiet else self.gate, self.epoch)

    @property
    def line(self) -> Optional[tuple[int, float, float]]:
        """(k, t_k, T): beat k at host time t_k (acoustic), period T; None before the first DBN beat."""
        return self._line

    @property
    def n_line(self) -> int:
        return self._n_line

    def period(self) -> Optional[float]:
        """The line's period (s) over its last 16 beats: the median of their beat-to-beat periods (4 or more; else
        the line's own, None without a line). Steadier than the line's own, which follows its last 8 DBN beats, or
        the posterior while they disagree, and jittered 110-137 bpm beat to beat on 26's room-mic pop song. Only the
        beats since the line's last new tempo (a new line, a re-phase onto another tempo, a run of taps) count: the
        ones before it are another tempo's (21 Oct check: the tracker at 85.6 for two beats after 172, a median
        of 171.4 judged a slow-lane halving at 172 that the octave map then applied to 85.6: 42.8 published)."""
        n = min(self._n_ibi, len(self._ibis))
        if n >= 4:
            return float(np.median(list(self._ibis)[-n:]))
        return self._line[2] if self._line is not None else None

    def beat_at(self, t: float) -> float:
        """The line's beat (float) at host time ``t``; NaN without a line."""
        ln = self._line
        return math.nan if ln is None else ln[0] + (t - ln[1]) / ln[2]

    # -- one frame
    def frame(self, a: float, t_raw: float, *, silent: bool = False, fresh: bool = False) -> None:
        """Activation ``a`` of the hop whose last sample sounds at host time ``t_raw``. ``silent``: no activation (the
        quiet room, a skipped hole): the forward state waits. ``fresh``: the LSTM was reset before this hop."""
        t = t_raw - self.lag_s
        self.t_last = t
        if fresh and self.cfg["act_reset"]:
            self.hmm_reset()
        if silent:
            self.counts["silent_frames"] += 1
            self._judge(t)
            self._flush_events(t)
            return
        self.counts["frames"] += 1
        if self.cfg["hits_w"] > 0.0 and self._hitq:
            w = self.cfg["hits_win_ms"] / 1000.0
            k = max((st for (th, st) in self._hitq if abs(th - t) <= w), default=0.0)
            if k > 0.0:
                a = 1.0 - (1.0 - a) * (1.0 - min(1.0, self.cfg["hits_w"] * k))
                self.counts["hit_frames"] += 1
        if self.cfg["act_norm"] > 0.0:
            self._an_L = max(float(a), self._an_L * self._an_dec, 0.05)
            a = min(0.95, float(a) * self.cfg["act_norm"] / self._an_L)
        a = min(1.0 - 1e-6, max(1e-6, float(a)))
        self._act.append((t, a))
        self._act_ev.append((t, a))
        self._salience(t)
        if self._tg is not None:
            v, _ = self._pk.push(a, t, self._tg_floor)
            self._tg.push(v, t)
        p, ll = self.hmm.step(self.p, a)
        lr = ll + self._log_ol                                  # vs a beatless model's 1 / observation_lambda
        self._llr.append(lr)
        self._llr8.append(lr)
        self._llr_sum8 += lr
        if len(self._llr8) > 8 * FPS:
            self._llr_sum8 -= self._llr8.popleft()
        c = self.cfg
        if c["eps"] > 0.0:
            p *= 1.0 - c["eps"]
            p += c["eps"] / self.hmm.N
        self.p = p
        self._challenger(a, ll, t)
        s = int(np.argmax(self.p))
        if self.hmm.beat[s] and t >= self.last_beat + self.min_ibi:     # madmom's online rule
            self.last_beat = t
            self.counts["dbn_beats"] += 1
            self._on_beat(t, s)
        self._judge(t)
        self._flush_events(t)

    def hit(self, t_s: float, strength: float) -> None:
        """A dsp sub/bass hit (acoustic host time), heard before the next frame; any thread."""
        self._hitq.append((float(t_s), float(strength)))

    def _challenger(self, a: float, ll: float, t: float) -> None:
        c = self.cfg
        if c["ch_every_s"] <= 0.0:
            return
        ch = self._ch
        if ch is None:
            if t - self._ch_last >= c["ch_every_s"] and (self.gate != "beat" or c["ch_always"]):
                self._ch = {"p": self._p0.copy(), "t0": t, "ll": 0.0, "inc": 0.0}
                self._ch_last = t
                self.counts["ch_starts"] += 1
            return
        q, llc = self.hmm.step(ch["p"], a)
        if c["eps"] > 0.0:
            q *= 1.0 - c["eps"]
            q += c["eps"] / self.hmm.N
        ch["p"], ch["ll"], ch["inc"] = q, ch["ll"] + llc, ch["inc"] + ll
        age = t - ch["t0"]
        if age >= c["ch_min_s"] and ch["ll"] - ch["inc"] > c["ch_margin"]:
            self.p = ch["p"]
            self._ch = None
            self.counts["ch_swaps"] += 1
        elif age >= c["ch_max_s"]:
            self._ch = None

    # -- the line
    def _T_dbn(self, s: int) -> float:
        """The posterior-mean period (s) over the intervals within 10 % of the best state's."""
        P = np.add.reduceat(self.p, self.hmm.first)
        iv = self.hmm.iv
        i0 = iv[self.hmm.sidx[s]]
        m = np.abs(iv / i0 - 1.0) <= 0.10
        w = P[m]
        return float((w * iv[m]).sum() / max(1e-12, w.sum())) / FPS

    def _on_beat(self, t: float, s: int) -> None:
        c = self.cfg
        Td = self._T_dbn(s)
        if self._line is None:
            k = self.k_pub
            self._hist.clear()
            self._hist.append((k, t))
            self._line = (k, t, Td)
            self._k_judged = k
            self._hits.append(1.0)
            self._n_line = 1
            self._n_ibi = 0
            self.counts["lines"] += 1
            self._ev_wait.append((t, k, "new"))
        else:
            k0, t0, T = self._line
            x = k0 + (t - t0) / T
            k = int(round(x))
            last_k = self._hist[-1][0] if self._hist else -(1 << 62)     # (empty only after a halving)
            if k > self._k_judged - 1 and abs(x - k) <= c["phase_tol"] and k > last_k \
                    and abs(T / Td - 1.0) <= 0.15:
                for kk in range(self._k_judged + 1, k):          # beats the DBN skipped
                    self._hits.append(0.0)
                    self.events.append((kk, "miss", 0.0))
                if k > self._k_judged:
                    self._hits.append(1.0)
                    self._ev_wait.append((t, k, "hit"))
                self._k_judged = max(self._k_judged, k)
                if self._hist:
                    kp, tp = self._hist[-1]
                    self._ibis.append((t - tp) / max(1, k - kp))
                    self._n_ibi += 1
                self._hist.append((k, t))
                self._n_line += 1
            elif t < self._hold_until:                          # a tap holds the line: the DBN waits
                self.counts["held_beats"] += 1
                return
            else:                                               # off the line: re-phase onto the DBN's beat
                k = max(last_k + 1, int(math.floor(x + 0.5)))
                self._hits.append(0.0)
                self._hist.clear()
                self._hist.append((k, t))
                self._k_judged = k
                self._n_line = 1
                if abs(T / Td - 1.0) > 0.15:                    # onto another tempo ("new"): period() starts over
                    self._n_ibi = 0
                self.epoch += 1
                self.counts["rephases"] += 1
                self._ev_wait.append((t, k, "rephase" if abs(T / Td - 1.0) <= 0.15 else "new"))
        if self.tapped:                                         # a DBN beat landed: the taps' line is the audio's now
            self.tapped = False
        T = Td
        k_a, t_a = k, t
        resid = math.nan
        if len(self._hist) >= 3:
            kk = np.array([h[0] for h in self._hist], float)
            tt = np.array([h[1] for h in self._hist], float)
            A = np.vstack([np.ones_like(kk), kk - kk[-1]]).T
            (b0, b1), *_ = np.linalg.lstsq(A, tt, rcond=None)
            if b1 > 0 and abs(b1 / Td - 1.0) <= c["fit_tol"]:
                T, t_a = float(b1), float(b0)
                resid = float(np.sqrt(np.mean((tt - A @ np.array([b0, b1])) ** 2))) / T
            else:
                self.counts["fit_rejects"] += 1
        self._line = (k_a, t_a, T)
        self.k_pub = k_a
        self._sal_wait.append(t)
        self._conf_update(t, s, resid)

    def _judge(self, t: float) -> None:
        """Predicted beats that passed without a DBN beat are misses (conf falls through a breakdown)."""
        if self._line is None:
            return
        k0, t0, T = self._line
        changed = False
        while t > t0 + (self._k_judged + 1 - k0) * T + 0.35 * T:
            self._k_judged += 1
            self._hits.append(0.0)
            self.events.append((self._k_judged, "miss", 0.0))
            changed = True
        if changed:
            self._conf_update(t, None, math.nan)

    def _salience(self, t: float) -> None:
        """Each beat's activation peak (within +-70 ms), once that window has been heard."""
        while self._sal_wait and t >= self._sal_wait[0] + 0.07:
            tb = self._sal_wait.popleft()
            v = [a for (ta, a) in self._act if abs(ta - tb) <= 0.07]
            self._sal.append(max(v) if v else 0.0)

    def _flush_events(self, t: float) -> None:
        """The DBN beats whose +-70 ms window has been heard go to ``events``, their peak as the strength."""
        w = self._ev_wait
        while w and t >= w[0][0] + 0.07:
            tb, k, kind = w.popleft()
            v = [a for (ta, a) in self._act if abs(ta - tb) <= 0.07]
            self.events.append((k, kind, max(v) if v else 0.0))

    # -- conf, state, gate
    def conf_features(self, s: Optional[int], resid: float) -> dict:
        x = dict(self._x)
        h = list(self._hits)
        x["h16"] = float(np.mean(h)) if h else 0.0
        x["h8"] = float(np.mean(h[-8:])) if h else 0.0
        x["h4"] = float(np.mean(h[-4:])) if h else 0.0
        if s is not None:                                       # at a beat: the posterior's view
            hm = self.hmm
            P = np.add.reduceat(self.p, hm.first)
            iv = hm.iv
            i0 = iv[hm.sidx[s]]
            near = np.abs(iv / i0 - 1.0) <= 0.05
            x["m_tempo"] = float(P[near].sum())
            oc = (np.abs(iv / (2 * i0) - 1.0) <= 0.05) | (np.abs(iv / (0.5 * i0) - 1.0) <= 0.05)
            x["m_oct"] = float(P[oc].sum())
            ms = near[hm.sidx]
            w = self.p[ms]
            sw = float(w.sum())
            x["r_phase"] = float(math.hypot(float(w @ hm.cos[ms]), float(w @ hm.sin[ms])) / sw) if sw > 0 else 0.0
            x["resid"] = min(0.2, resid) if math.isfinite(resid) else 0.2
            lb = math.log2(max(1e-6, 60.0 / self._line[2]) / 120.0) if self._line else 0.0
            x["lb"], x["lb2"] = lb, lb * lb
            x["n_line"] = min(16, self._n_line) / 16.0
            sal = list(self._sal)
            x["sal"] = float(np.mean(sal)) if sal else 0.0
            act = [a for (_, a) in self._act]
            x["a_mean"] = float(np.mean(act)) if act else 0.0
            x["contrast"] = x["sal"] / (x["sal"] + x["a_mean"] + 1e-6)
            x["llr"] = float(np.mean(self._llr)) if self._llr else 0.0     # nats a frame the DBN predicts better
            x["llr8"] = self._llr_sum8 / len(self._llr8) if self._llr8 else 0.0
            x["llr1"] = float(np.mean(list(self._llr)[-100:])) if self._llr else 0.0
            x["n_log"] = math.log2(1.0 + self._n_line) / 6.0
            x["t_agree"] = min(0.2, abs(self._line[2] / self._T_dbn(s) - 1.0)) if self._line else 0.2
            ib = np.array(self._ibis) if len(self._ibis) >= 4 else None
            x["ibi_cv"] = min(0.3, float(ib.std() / ib.mean())) if ib is not None else 0.3        # tempo wobble
            x["t_drift"] = min(0.3, abs(float(ib[-4:].mean() / ib[:4].mean()) - 1.0)) if ib is not None and \
                len(ib) >= 8 else 0.3                                                              # ... and drift
            x["tg_clar"] = x["tg_rel"] = 0.0
            if self._tg is not None and self._line is not None and self._tg.filled >= 100:
                X = self._tg.spectrum()
                bpm = 60.0 / self._line[2]
                cl = max(self._tg.clarity(X, bpm), self._tg.clarity(X, 2 * bpm), self._tg.clarity(X, bpm / 2))
                x0 = X[0].real
                best = float(np.abs(X[1:]).max() / x0) if x0 > 1e-6 else 0.0
                x["tg_clar"] = self._tg.clarity(X, bpm)
                x["tg_rel"] = cl / best if best > 0 else 0.0
            for k in FEATURES:
                x.setdefault(k, 0.0)
            self._x = x
        return x

    def _conf_update(self, t: float, s: Optional[int], resid: float) -> None:
        c = self.cfg
        x = self.conf_features(s, resid)
        if not x.get("m_tempo") and s is None and not self._x:
            return
        W = self.W
        if c["conf_mode"] == "logit" and W is not None:
            z = W["b"] + sum(W["w"][k] * (x[k] - W["mu"][k]) / W["sd"][k] for k in W["w"])
            conf = _sig(z)
        else:                                                   # hand-set: hits x tempo certainty x phase x fit
            conf = x["h8"] * math.sqrt(max(0.0, x["m_tempo"])) * x["r_phase"] * max(0.0, 1.0 - x["resid"] / 0.1)
        if self._n_line < c["conf_cap_n"]:
            conf = min(conf, c["conf_cap"])
        self.conf = min(0.99, max(0.0, conf))
        self._update_state()
        self._update_gate(t)

    def _update_state(self) -> None:
        if self._line is None:
            self.state = "searching"
        elif self.quiet:
            self.state = "holding"
        elif self.no_audio or self.tapped:
            self.state = "free"
        elif sum(list(self._hits)[-4:]) >= 2.0:
            self.state = "locked"
        else:
            self.state = "holding"

    def _update_gate(self, t: float) -> None:
        """clock.Clock._update_gate, on this conf and these thresholds; ``none`` at once while searching, quiet or
        with no audio."""
        c = self.cfg
        cf, g = self.conf, self.gate
        off = self.state == "searching" or self.quiet or self.no_audio
        if off:
            new = "none"
        elif g == "none":
            new = "beat" if cf >= c["beat_on"] else ("dmx" if cf >= c["dmx_on"] else "none")
        elif g == "dmx":
            new = "beat" if cf >= c["beat_on"] else ("none" if cf < c["dmx_off"] else "dmx")
        else:
            new = "beat" if cf >= c["beat_off"] else ("dmx" if cf >= c["dmx_off"] else "none")
        if new == g:
            return
        up = _GATE_RANK[new] > _GATE_RANK[g]
        T = self._line[2] if self._line else 0.5
        recent = t - self._gate_t < c["dwell_bars"] * c["beats_per_bar"] * T
        if recent and not (new == "none" and off) and (not up or not self._gate_up):
            return
        self.gate, self._gate_t, self._gate_up = new, t, up

    # -- corrections and coasting (the live clock's additions)
    def _hold(self, t: float) -> None:
        """The forward state re-seeded on the (new) line, and its disagreement held off for HOLD_S from ``t``."""
        k0, t0, T = self._line
        self.p = self.hmm.seed(T, (self.t_last - t0) / T)
        self._ch = None
        self._hold_until = max(self._hold_until, t + HOLD_S)
        self.counts["holds"] += 1

    def tap(self, t: float, run: Optional[list[float]] = None) -> tuple[Optional[str], Optional[int]]:
        """The human's beat at host time ``t``; ``run``: the run of taps it ends (4 or more, evenly spaced) or None.
        Returns (what happened, the tap's beat): ``agree`` (within TAP_AGREE of the line, and the run's tempo within
        4 %: one more hit, nothing moves), ``phase`` (the line's nearest beat moves to the tap), ``tempo`` (the line
        is the run's LS line, numbered on from the line's count; with no line it starts one, "free" at TAP_CONF until
        a DBN beat lands), or (None, None) (no line and no run). A move is ``epoch`` + 1, re-seeds the forward state
        and holds the DBN's disagreement off (HOLD_S)."""
        T = None
        if run is not None and len(run) >= 4:
            tt = np.asarray(run, float)
            kk = np.arange(len(tt), dtype=float)
            dk = kk - kk.mean()
            T = float(np.dot(dk, tt - tt.mean()) / np.dot(dk, dk))
            if not 60.0 / self.cfg["max_bpm"] <= T <= 60.0 / self.cfg["min_bpm"]:
                T = None
        ln = self._line
        if ln is None:
            if T is None:
                return None, None
            k = self.k_pub
        else:
            k0, t0, T0 = ln
            x = k0 + (t - t0) / T0
            k = int(round(x))
            if abs(x - k) <= TAP_AGREE and (T is None or abs(T0 / T - 1.0) <= 0.04):
                self._hits.append(1.0)
                self.counts["taps_agree"] += 1
                return "agree", k
        if T is None:                                           # the phase only: the line's period stays
            T = ln[2]
            kind = "phase"
            self._hist.clear()
            self._hist.append((k, t))
        else:                                                   # the run's line: its taps are the LS history
            n = len(run)
            tt = np.asarray(run, float)
            t = float(tt.mean() + T * (n - 1 - 0.5 * (n - 1)))  # the fit at the last tap
            kind = "tempo"
            self._hist.clear()
            self._hist.extend((k - (n - 1) + i, float(ti)) for i, ti in enumerate(tt))
            self._n_line = max(self._n_line, n) if ln is not None else n
            self._ibis.clear()
            self._n_ibi = 0
            if ln is None:
                self.tapped = True
                self.conf = max(self.conf, TAP_CONF)
                self.counts["lines"] += 1
        self._line = (k, t, T)
        self.k_pub = k
        self._k_judged = k
        self._hits.append(1.0)
        self.epoch += 1
        self.counts["taps_" + kind] += 1
        self._hold(t)
        self._update_state()
        self._update_gate(t)
        return kind, k

    def set_no_audio(self, on: bool, t: float) -> None:
        """No audio arriving (Clock's NO_AUDIO_S) or back: ``free`` and the gate ``none`` at once, or the state
        from the hits again (the gate climbs back at the next conf)."""
        if bool(on) == self.no_audio:
            return
        self.no_audio = bool(on)
        if on:
            self.counts["no_audio"] += 1
        self._update_state()
        self._update_gate(t)

    def coast(self, t: float) -> None:
        """No audio: the line's predicted beats up to host time ``t`` pass as misses."""
        self.set_no_audio(True, t)
        self._judge(t)
        self._flush_events(t)

    def octave_evidence(self, t_ref: float, T: float, f: float, n: int = OCT_EV_N) -> Optional[dict]:
        """The audio's word on an octave of a line (Clock's gate on the slow lane's octave votes, README S4.15
        "Octave evidence"). The line's beat at host time ``t_ref`` (acoustic, as this tracker's times) is one both
        grids keep, ``T`` its period, ``f`` 2 (the octave adds the beats midway) or 0.5 (it drops every other beat,
        ``t_ref``'s parity staying). Over the finer grid's last ``n`` points heard (``t_ref + j x P``, P = T at 0.5
        and T / 2 at 2; odd j are the points only the finer grid has), each point's activation peak within +-w
        (w = min(70 ms, P / 4)) from the last OCT_EV_S of frames, less the activation's median over that span (the
        floor between onsets). Returns ``{"ratio", "n", "added", "kept", "floor"}``: ratio = the mean over the odd
        points (the beats a doubling adds, a halving drops) / the mean over the even ones (the beats both keep).
        None with fewer than n / 2 points heard (a quiet room, a hole) or no peak above the floor on the even ones."""
        if not self._act_ev or not T > 0.0 or f not in (0.5, 2.0):
            return None
        P = T if f < 1.0 else 0.5 * T
        w = min(0.07, 0.25 * P)
        A = np.asarray(self._act_ev, float)
        ta, aa = A[:, 0], A[:, 1]
        j0 = math.floor((self.t_last - w - t_ref) / P + 1e-9)    # the newest point whose window has been heard
        odd: list[float] = []
        even: list[float] = []
        t_first = math.inf
        for j in range(j0, j0 - int(n), -1):
            t = t_ref + j * P
            lo, hi = int(np.searchsorted(ta, t - w, "left")), int(np.searchsorted(ta, t + w, "right"))
            if hi <= lo:
                continue
            (odd if j % 2 else even).append(float(aa[lo:hi].max()))
            t_first = t
        if len(odd) + len(even) < 0.5 * n or not odd or not even:
            return None
        span = aa[int(np.searchsorted(ta, t_first - P, "left")):]
        floor = float(np.median(span))
        added, kept = float(np.mean(odd)) - floor, float(np.mean(even)) - floor
        if not kept > 1e-6:
            return None
        return {"ratio": added / kept, "n": len(odd) + len(even), "added": added, "kept": kept, "floor": floor}

    # -- the body (contract.ClockData's fields; Clock fills the bar and phrase ones, and its published line and epoch)
    def body(self, now_s: float, source: str = "madmom-dbn2") -> dict:
        ln = self._line
        return {"bpm": round(60.0 / ln[2], 6) if ln else 0.0, "anchor_ms": ln[1] * 1000.0 if ln else 0.0,
                "beat_at_anchor": ln[0] if ln else 0, "beats_per_bar": int(self.cfg["beats_per_bar"]),
                "bar_anchor_beat": 0, "phrase_bars": 8, "conf": round(self.conf, 4), "bar_conf": 0.0,
                "phrase_conf": 0.0, "state": self.state, "gate": "none" if self.quiet else self.gate,
                "epoch": self.epoch, "stability": round(float(self._x.get("m_tempo", 0.0)), 4),
                "source": source, "t_ms": now_s * 1000.0}

    def stats(self) -> dict:
        return {**dict(self.counts), "state": self.state, "gate": self.gate, "conf": round(self.conf, 3),
                "bpm": round(60.0 / self._line[2], 3) if self._line else 0.0, "epoch": self.epoch,
                "n_line": self._n_line, "conf_w": self.W is not None and self.cfg["conf_mode"] == "logit",
                "held": self.t_last < self._hold_until}
