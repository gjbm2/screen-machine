"""The resonator bank (dsp/resonance.py): decay times per damper state, steady-state gain, the own-sound gate,
pedal dependence, the pedal-lift burst, stability on dense pedalled chords, and time alignment."""

import math

import numpy as np
import pytest

from piano_render import score
from piano_render.dsp import analysis, resonance as R

SR = 48000


def _one(m=60, f=261.63, tau_free=1.0, tau_damp=0.1, peak=1.0) -> R.Bank:
    """A bank with one resonator on key m, centre-panned."""
    i = m - score.KEY_LO
    kstart = np.where(np.arange(88) <= i, 0, 1).astype(np.int64)
    kstart[i] = 0
    kend = np.where(np.arange(88) < i, 0, 1).astype(np.int64)
    c = np.full(88, 1 / math.sqrt(2))
    return R.Bank(key=np.array([m]), k=np.array([1]), freq=np.array([f]), tau_free=np.array([tau_free]),
                  tau_damp=np.array([tau_damp]), peak=np.array([peak]), kstart=kstart, kend=kend, pan_l=c, pan_r=c)


def _env_db(y, t0, t1, win=0.02):
    a, b, w = int(t0 * SR), int(t1 * SR), int(win * SR)
    seg = y[a:b, 0].astype(np.float64)
    n = len(seg) // w
    r = np.sqrt(np.mean(seg[:n * w].reshape(n, w) ** 2, axis=1))
    t = t0 + (np.arange(n) + 0.5) * win
    return t, 20 * np.log10(r + 1e-30)


def _tau(y, t0, t1):
    t, db = _env_db(y, t0, t1)
    slope = np.polyfit(t, db, 1)[0]            # dB per second
    return -8.685889638 / slope


def _burst(f, t0, t1, n_s):
    t = np.arange(int(n_s * SR)) / SR
    x = np.where((t >= t0) & (t < t1), np.sin(2 * np.pi * f * t), 0.0)
    return x.astype(np.float32)


def test_free_damped_and_half_pedal_decay():
    f = 261.63
    bank = _one(f=f, tau_free=1.0, tau_damp=0.1)
    x = _burst(f, 0.1, 0.3, 4.0)
    perf = score.from_events([(0.0, "cc", 64, 127), (2.0, "cc", 64, 0)])
    y, st = R.run(x, SR, perf, R.REF_DB, bank=bank)
    assert np.isfinite(y).all()
    assert _tau(y, 0.5, 1.8) == pytest.approx(1.0, rel=0.05)          # pedal down: free string
    assert _tau(y, 2.05, 2.45) == pytest.approx(0.1, rel=0.1)         # pedal up: the damper's rate
    # half pedal: tau_d + (tau_free - tau_d) * d ** (2 + f / 2 kHz), d quantised to 1/64
    perf = score.from_events([(0.0, "cc", 64, 127), (1.0, "cc", 64, 64)])
    y, _ = R.run(x, SR, perf, R.REF_DB, bank=bank)
    d = round(float(score.damper_lift(64)) * R.LIFT_STEPS) / R.LIFT_STEPS
    expect = 0.1 + 0.9 * d ** (2 + f / 2000)
    assert _tau(y, 1.1, 2.0) == pytest.approx(expect, rel=0.1)


def test_steady_state_gain_is_peak_times_level():
    f, peak = 440.0, 0.8
    bank = _one(m=69, f=f, tau_free=0.4, peak=peak)
    t = np.arange(int(6 * SR)) / SR
    x = (0.1 * np.sin(2 * np.pi * f * t)).astype(np.float32)
    perf = score.from_events([(0.0, "cc", 64, 127)])
    for rdb in (R.REF_DB, R.REF_DB + 12):
        y, _ = R.run(x, SR, perf, rdb, bank=bank)
        amp = np.sqrt(2) * np.sqrt(np.mean(y[int(4 * SR):int(5.5 * SR), 0].astype(np.float64) ** 2))
        assert amp == pytest.approx(0.1 * peak * R.gain_for(rdb) / math.sqrt(2), rel=0.05)
    assert R.gain_for(R.REF_DB + 6) / R.gain_for(R.REF_DB) == pytest.approx(10 ** (6 / 20))


def _tone(m, t0, n_s, tau=3.0, kmax=12, tuning=None):
    """A synthetic piano-like note on key m whose partials sit exactly on the bank's (model) tuning."""
    kt = (tuning or analysis.model_tuning()).key(m)
    t = np.arange(int(n_s * SR)) / SR
    env = np.where(t >= t0, np.exp(-(t - t0) / tau), 0.0)
    x = np.zeros_like(t)
    for k in range(1, kmax + 1):
        f = kt.freq(k)
        if f > 5000:
            break
        x += np.sin(2 * np.pi * f * (t - t0)) / k
    return (0.1 * env * x).astype(np.float32)


def _rms_db(y, t0, t1):
    s = y[int(t0 * SR):int(t1 * SR)].astype(np.float64)
    return 10 * np.log10(np.mean(s ** 2) + 1e-30)


def _only(bank: R.Bank, keep: set) -> R.Bank:
    """The same bank with only the resonators of the keys in ``keep``."""
    sel = np.isin(bank.key, list(keep))
    keys = bank.key[sel]
    kstart = np.searchsorted(keys, score.KEYS, side="left").astype(np.int64)
    kend = np.searchsorted(keys, score.KEYS, side="right").astype(np.int64)
    return R.Bank(key=keys, k=bank.k[sel], freq=bank.freq[sel], tau_free=bank.tau_free[sel],
                  tau_damp=bank.tau_damp[sel], peak=bank.peak[sel], kstart=kstart, kend=kend,
                  pan_l=bank.pan_l, pan_r=bank.pan_r)


def test_own_sound_is_gated(monkeypatch):
    """A held, still-sounding note must not drive its own resonators (that would double its sampled sound)."""
    x = _tone(60, 0.2, 4.0)
    perf = score.from_events([(0.0, "cc", 64, 127), (0.2, "on", 60, 90)])
    own = _only(R.build_bank(), {60})
    gated, _ = R.run(x, SR, perf, R.REF_DB, bank=own)
    monkeypatch.setattr(R, "OWN_GATE_DB", (400.0, 399.0))          # gate always open
    ungated, _ = R.run(x, SR, perf, R.REF_DB, bank=own)
    assert _rms_db(ungated, 1.5, 3.5) - _rms_db(gated, 1.5, 3.5) > 25
    # once the note has died away (here: released, pedal up and down again later) its strings ring again
    perf2 = score.from_events([(0.0, "cc", 64, 127), (0.2, "on", 60, 90), (0.3, "off", 60, 64),
                               (0.4, "cc", 64, 0), (3.0, "cc", 64, 127)])
    x2 = x + _tone(48, 3.2, 4.0, tau=1.0)                          # C3 sounds C4's partials
    monkeypatch.setattr(R, "OWN_GATE_DB", (-40.0, -50.0))
    later, _ = R.run(x2, SR, perf2, R.REF_DB, bank=own)
    assert _rms_db(later, 3.4, 3.9) > _rms_db(later, 2.0, 2.9) + 20


def test_resonance_needs_the_pedal():
    chord = [48, 60, 64, 67]
    x = sum(_tone(m, 0.5 + 0.05 * i, 6.0, tau=1.5) for i, m in enumerate(chord))
    notes = [ev for i, m in enumerate(chord) for ev in ((0.5 + 0.05 * i, "on", m, 90), (1.0, "off", m, 64))]
    down, _ = R.run(x, SR, score.from_events([(0.0, "cc", 64, 127)] + notes), R.REF_DB)
    up, _ = R.run(x, SR, score.from_events(notes), R.REF_DB)
    assert np.isfinite(down).all() and np.isfinite(up).all()
    assert _rms_db(down, 1.5, 4.0) - _rms_db(up, 1.5, 4.0) > 20


def test_pedal_lift_burst_and_silence():
    x = np.zeros(int(2 * SR), np.float32)
    x[int(1.5 * SR):] = 1e-3 * np.random.default_rng(0).standard_normal(int(0.5 * SR))   # some level for the burst
    nothing, _ = R.run(x, SR, score.from_events([(1.9, "on", 60, 1)]), R.REF_DB)
    assert np.abs(nothing[:int(1.49 * SR)]).max() == 0.0          # no pedal, no notes, no input: exactly silent
    lifted, st = R.run(x, SR, score.from_events([(1.6, "cc", 64, 127)]), R.REF_DB)
    assert st["lift_events"] == 1
    assert np.abs(lifted[int(1.6 * SR):int(1.8 * SR)]).max() > np.abs(nothing[int(1.6 * SR):int(1.8 * SR)]).max()


def test_dense_pedalled_chords_stay_stable():
    rng = np.random.default_rng(3)
    n_s = 5.5
    x = rng.uniform(-1, 1, int(n_s * SR)).astype(np.float32)          # full-scale noise in ...
    x[int(4.0 * SR):] = 0.0                                           # ... until the dampers come down
    evs = [(0.0, "cc", 64, 127)]
    for i in range(16):                                               # 12-note clusters, a new one every 0.25 s
        chord = rng.choice(np.arange(21, 109), 12, replace=False)
        evs += [(0.25 * i, "on", int(m), 127) for m in chord]
        evs += [(0.25 * i + 0.2, "off", int(m), 64) for m in chord]
    evs += [(4.0, "cc", 64, 0)]
    y, st = R.run(x, SR, score.from_events(evs), R.REF_DB + 20)      # +20 dB over the default
    assert np.isfinite(y).all()
    assert float(np.abs(y).max()) < 50.0
    assert st["resonators"] > 1000
    assert _rms_db(y, 3.0, 3.9) > -60                                # it really rang
    assert _rms_db(y, 4.8, 5.3) < _rms_db(y, 3.0, 3.9) - 20          # dampers down: it dies away
    # (the top strings, MIDI 89 up, have no dampers and keep ringing with any input: that is the instrument)


def test_starts_with_the_music_and_has_a_tail():
    x = _tone(60, 1.0, 3.0, tau=1.0)
    perf = score.from_events([(0.5, "cc", 64, 127), (1.0, "on", 64, 90), (1.2, "off", 64, 64)])
    y, st = R.run(x, SR, perf, R.REF_DB)
    assert y.shape[0] >= x.shape[0] and y.shape[1] == 2 and y.dtype == np.float32
    peak = float(np.abs(y).max())
    assert peak > 0
    # before the pedal comes down nothing at all; the lift burst rings, but the music's own part starts at 1.0 s
    assert np.abs(y[:int(0.49 * SR)]).max() == 0.0
    assert st["tail_s"] > 0                                      # pedal still down at the end: it rings on
    assert st["tail_s"] <= R.TAIL_MAX_S


def test_bank_layout_and_pan():
    b = R.build_bank()
    assert 1200 <= b.size <= 1600
    assert b.freq.max() <= R.F_MAX
    assert (b.tau_free >= 1.5 * b.tau_damp - 1e-12).all()
    i60 = 60 - score.KEY_LO
    kt = analysis.model_tuning().key(60)
    n60 = sum(1 for k in range(1, R.MAX_PARTIALS + 1) if kt.freq(k) <= R.F_MAX)
    assert 15 <= n60 <= 19
    assert b.kend[i60] - b.kstart[i60] == n60 + R.BEAT_PARTIALS              # + a beating twin for k <= 4
    assert b.pan_l[0] > b.pan_r[0] and b.pan_r[-1] > b.pan_l[-1]          # bass left, treble right
    assert np.allclose(b.pan_l ** 2 + b.pan_r ** 2, 1.0)
    audience = R.build_bank(pan_width=-0.3)
    assert audience.pan_r[0] > audience.pan_l[0]
