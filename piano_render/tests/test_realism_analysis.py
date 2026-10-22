"""Partial analysis (dsp/analysis.py): the fit on synthetic stiff strings, the fallbacks, and measure() + its cache
through an additive test engine whose tuning and inharmonicity are known."""

import math

import numpy as np
import pytest

from piano_render import score
from piano_render.dsp import analysis as A
from piano_render.engines.base import Engine

SR = 48000


def _cents(a, b):
    return 1200 * math.log2(a / b)


def stiff_string(f0, B, n_s=1.6, kmax=24, seed=0, sr=SR):
    t = np.arange(int(n_s * sr)) / sr
    x = np.zeros_like(t)
    for k in range(1, kmax + 1):
        f = A.partial_hz(f0, B, k)
        if f > 0.45 * sr:
            break
        x += np.sin(2 * np.pi * f * t + k) / k * np.exp(-t * (0.5 + 0.1 * k))
    x += 1e-4 * np.random.default_rng(seed).standard_normal(t.size)
    return x


@pytest.mark.parametrize("m,cents,Bscale", [(33, -20.0, 0.6), (45, 5.0, 1.5), (60, -3.0, 1.0), (81, 12.0, 1.2)])
def test_fit_recovers_f0_and_B(m, cents, Bscale):
    B = A.model_B(m) * Bscale
    p1 = A.nominal_f0(m) * 2 ** (cents / 1200)
    f0 = p1 / math.sqrt(1 + B)
    kt = A.analyse_key(stiff_string(f0, B), SR, m)
    assert kt.status == "measured"
    assert _cents(kt.freq(1), p1) == pytest.approx(0.0, abs=0.3)
    assert kt.B == pytest.approx(B, rel=0.15)
    k = max(kt.partials)
    assert _cents(kt.freq(k), A.partial_hz(f0, B, k)) == pytest.approx(0.0, abs=0.5)


def test_garbage_falls_back_to_the_model():
    t = np.arange(int(1.6 * SR)) / SR
    tone_1k = np.sin(2 * np.pi * 1000 * t)                 # nothing like key 21's partials
    assert A.analyse_key(tone_1k, SR, 21).status == "model"
    assert A.analyse_key(np.zeros(SR), SR, 60).status == "model"
    mt = A.model_tuning()
    assert len(mt.keys) == 88 and mt.source == "model"
    assert mt.key(69).freq(1) == pytest.approx(440.0)
    assert mt.key(21).freq(2) > 2 * mt.key(21).freq(1)      # stiff strings: partials stretch


class AdditiveEngine(Engine):
    """Each note-on plays a stiff string with a known detune (cents) and B for its key."""
    name = "additive"
    renders = 0

    @classmethod
    def available(cls, root):
        return True

    @staticmethod
    def detune(m):
        return 4.0 * math.sin(m)

    @staticmethod
    def B(m):
        return A.model_B(m) * (1.0 + 0.3 * math.cos(m))

    def _load(self, sr):
        pass

    def render(self, perf, sr=48000, progress=None, pitch_cents=0.0):
        AdditiveEngine.renders += 1
        n = int((perf.duration_s + 1.0) * sr)
        out = np.zeros(n)
        t = np.arange(int(2.0 * sr)) / sr
        for e in perf.events:
            if e.kind != "on":
                continue
            B = self.B(e.num)
            f0 = A.nominal_f0(e.num) * 2 ** (self.detune(e.num) / 1200) / math.sqrt(1 + B)
            x = np.zeros_like(t)
            for k in range(1, 13):
                f = A.partial_hz(f0, B, k)
                if f > 5000:
                    break
                x += np.sin(2 * np.pi * f * t) / k
            x *= np.exp(-t / 2.0) * 0.05
            a = int(round(e.t_s * sr))
            b = min(n, a + t.size)
            out[a:b] += x[:b - a]
        return np.stack([out, out], axis=1).astype(np.float32)


def test_measure_and_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("PIANO_RENDER_ANALYSIS", str(tmp_path / "an"))
    AdditiveEngine.renders = 0
    eng = AdditiveEngine({"id": "add", "engine": "additive", "file": None}, tmp_path)
    inst = {"id": "add", "engine": "additive", "file": None, "velocity_gamma": 1.0}
    tun = A.tuning_for(inst, eng, SR)
    assert AdditiveEngine.renders == 1 and tun.info["cached"] is False
    assert tun.measured >= 85
    for m in (21, 40, 60, 75, 90, 100):
        kt = tun.key(m)
        if kt.status != "measured":
            continue
        want = A.nominal_f0(m) * 2 ** (AdditiveEngine.detune(m) / 1200)
        assert _cents(kt.freq(1), want) == pytest.approx(0.0, abs=0.5)
    again = A.tuning_for(inst, eng, SR)
    assert AdditiveEngine.renders == 1 and again.info["cached"] is True
    assert [k.f0 for k in again.keys] == pytest.approx([k.f0 for k in tun.keys])
    assert A.tuning_for(inst, None, SR).source == "model"                 # no engine: the model, nothing cached
    probe = A.probe_performance()
    assert sum(1 for e in probe.events if e.kind == "on") == 88
    assert {e.num for e in probe.events} == set(range(score.KEY_LO, score.KEY_HI + 1))
