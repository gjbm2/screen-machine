"""The realism stage end to end (realism.py): EQ and width, a pass-through when nothing applies, una corda,
detuned copies (real sfizz), the whole chain on a real engine, and how render() uses it (realism off = EQ only)."""

import json
import math

import numpy as np
import pytest
import soundfile as sf
from scipy.signal import butter, hilbert, sosfilt

from piano_render import engines, realism, render, score
from piano_render.dsp import tone

from .conftest import SR, onset_s


def _noise(secs=2.0, seed=1):
    return np.random.default_rng(seed).standard_normal((int(secs * SR), 2)).astype(np.float32) * 0.1


def _band_db(x, lo, hi):
    X = np.abs(np.fft.rfft(x.astype(np.float64)))
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return 10 * np.log10(np.mean(X[(f >= lo) & (f < hi)] ** 2))


def test_version_and_render_version():
    assert not str(realism.VERSION).startswith("minimal")
    assert render.RENDER_VERSION != "1"


def test_eq_and_width():
    x = _noise()
    assert np.array_equal(realism.apply(x, SR, None, {"eq": [], "width": 1.0}, None), x)     # nothing to do
    y = realism.apply(x, SR, None, {"eq": [{"type": "highshelf", "f": 3000, "gain_db": 6, "q": 0.707}]}, None)
    assert y.shape == x.shape and y.dtype == np.float32
    assert _band_db(y[:, 0], 10000, 20000) - _band_db(x[:, 0], 10000, 20000) == pytest.approx(6.0, abs=0.3)
    assert _band_db(y[:, 0], 50, 300) - _band_db(x[:, 0], 50, 300) == pytest.approx(0.0, abs=0.3)
    y = realism.apply(x, SR, None, {"eq": [{"type": "highpass", "f": 200, "q": 0.707}]}, None)
    assert _band_db(y[:, 0], 20, 50) - _band_db(x[:, 0], 20, 50) < -20
    y = realism.apply(x, SR, None, {"eq": [{"type": "peak", "f": 1000, "gain_db": -6, "q": 2}]}, None)
    assert _band_db(y[:, 0], 950, 1050) - _band_db(x[:, 0], 950, 1050) == pytest.approx(-6.0, abs=0.5)
    mono = realism.apply(x, SR, None, {"width": 0.0}, None)
    assert np.allclose(mono[:, 0], mono[:, 1]) and np.allclose(mono[:, 0], 0.5 * (x[:, 0] + x[:, 1]), atol=1e-6)
    with pytest.raises(ValueError):
        realism.apply(x, SR, None, {"eq": [{"type": "bogus", "f": 100}]}, None)
    # realism off: the EQ only (no width)
    dry = realism.apply_dry(x, SR, {"eq": [{"type": "highshelf", "f": 3000, "gain_db": -6}], "width": 0.0})
    assert _band_db(dry[:, 0], 10000, 20000) - _band_db(x[:, 0], 10000, 20000) == pytest.approx(-6.0, abs=0.3)
    assert not np.allclose(dry[:, 0], dry[:, 1])


def test_una_corda_shapes_only_while_down():
    x = _noise(4.0)
    rate = 93.75
    t = np.arange(int(4.0 * rate) + 1) / rate
    assert tone.una_corda(x, SR, t, np.zeros_like(t)) is x                        # never pressed: untouched
    u = np.where(t >= 2.0, 1.0, 0.0)
    y = tone.una_corda(x, SR, t, u)
    assert np.array_equal(y[:int(1.9 * SR)], x[:int(1.9 * SR)])                   # before: bit-exact
    seg = slice(int(2.5 * SR), int(4.0 * SR))
    low = _band_db(y[seg, 0], 100, 600) - _band_db(x[seg, 0], 100, 600)
    high = _band_db(y[seg, 0], 8000, 16000) - _band_db(x[seg, 0], 8000, 16000)
    assert low == pytest.approx(tone.UC_LEVEL_DB, abs=0.4)
    assert high == pytest.approx(tone.UC_LEVEL_DB + tone.UC_SHELF_DB, abs=0.5)
    # smooth: the level ramps down over tens of ms in small steps, no jump
    w = int(0.005 * SR)
    def rel_db(i):
        a, b = y[i:i + w, 0].astype(np.float64), x[i:i + w, 0].astype(np.float64)
        return 10 * np.log10(np.mean(a ** 2) / np.mean(b ** 2))
    steps = [rel_db(i) for i in range(int(1.97 * SR), int(2.2 * SR), w)]
    assert steps[0] == pytest.approx(0.0, abs=0.05) and steps[-1] < -4.5
    assert max(abs(b - a) for a, b in zip(steps, steps[1:])) < 1.5


@pytest.fixture
def synth_engine(sfz_registry):
    inst = sfz_registry.get("synth")
    eng = engines.create(inst, sfz_registry.engines_root)
    eng.ensure_loaded(SR)
    yield inst, eng
    eng.close()


def test_detuned_copy_beats_without_smearing_time(synth_engine):
    inst, eng = synth_engine
    perf = score.from_events([(0.1, "on", 69, 100), (3.6, "off", 69, 64)])
    dry = eng.render(perf, sr=SR)
    st = {}
    y = realism.apply(dry, SR, perf, dict(inst, detune_cents=[-12.0]), None, engine=eng, stats=st)
    assert st["detune"]["voices"] == 2
    assert onset_s(y) == pytest.approx(onset_s(dry), abs=1.5 / SR)                    # same start, to the sample
    # the 440 Hz partial now beats at 440 * (1 - 2 ** (-12 / 1200)) = 3.04 Hz
    sos = butter(4, [400, 480], btype="bandpass", fs=SR, output="sos")
    env = np.abs(hilbert(sosfilt(sos, y[:, 0].astype(np.float64))))[int(0.6 * SR):int(3.4 * SR)]
    env = env[::48] - env[::48].mean()
    spec = np.abs(np.fft.rfft(env * np.hanning(env.size), 1 << 16))
    f = np.fft.rfftfreq(1 << 16, 48 / SR)
    band = (f > 1) & (f < 10)
    beat = float(f[band][np.argmax(spec[band])])
    assert beat == pytest.approx(440 * (1 - 2 ** (-12 / 1200)), abs=0.3)
    # the attack is not comb-filtered: the copies' first 5 ms line up (correlation ~1)
    a, b = int(0.1 * SR), int(0.105 * SR)
    cp = eng.render(perf, sr=SR, pitch_cents=-12.0)
    assert np.corrcoef(dry[a:b, 0], cp[a:b, 0])[0, 1] > 0.95
    # without an engine the copies are skipped, not faked
    st2 = {}
    realism.apply(dry, SR, perf, dict(inst, detune_cents=[-12.0]), None, engine=None, stats=st2)
    assert "skipped" in st2["detune"]


def test_whole_chain_on_a_real_engine(synth_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("PIANO_RENDER_ANALYSIS", str(tmp_path / "an"))
    inst, eng = synth_engine
    evs = [(0.3, "cc", 64, 127), (0.5, "on", 60, 90), (0.5, "on", 64, 80), (1.2, "off", 60, 64), (1.2, "off", 64, 64),
           (1.5, "cc", 67, 127), (1.6, "on", 67, 70), (2.0, "off", 67, 64), (2.5, "cc", 64, 0), (2.6, "cc", 67, 0)]
    perf = score.from_events(evs)
    dry = eng.render(perf, sr=SR)
    room = {"id": "small", "ir": None, "synth": {"rt60_s": 0.7, "predelay_ms": 8, "width": 1.0}, "wet_db": -18}
    st = {}
    y = realism.apply(dry, SR, perf, dict(inst, resonance_db=-35, width=0.9), room, engine=eng, stats=st)
    assert y.dtype == np.float32 and y.ndim == 2 and y.shape[1] == 2
    assert np.isfinite(y).all() and y.shape[0] >= dry.shape[0]
    assert float(np.abs(y).max()) < 4 * float(np.abs(dry).max())
    pk = float(np.abs(dry).max())
    first = lambda a: int(np.flatnonzero(np.abs(a).max(axis=1) > 0.05 * pk)[0])
    assert first(y) == first(dry)                                                  # time 0 unchanged
    # the pedal goes down at 0.3 s, before any note: the lift burst rings the open strings, quietly
    pre = float(np.abs(y[int(0.3 * SR):int(0.49 * SR)]).max())
    assert 0 < pre < 0.01 * pk
    for k in ("una_corda", "analysis", "resonance", "room"):
        assert k in st, k
    assert st["resonance"]["resonators"] > 1000 and st["room"]["ir_source"] == "synth"
    assert st["una_corda"]["max"] == 1.0
    json.dumps(st)                                                                 # the sidecar can hold it
    off = realism.apply(dry, SR, perf, dict(inst, resonance_db=None), None, engine=eng, stats={})
    assert off.shape[0] == dry.shape[0]                                            # no bank, no room: no tail


@pytest.fixture
def fake_eq(registry_factory, cache):
    from .test_render import FakeEngine
    engines.register("fake", FakeEngine)
    render.release_engines()
    reg = registry_factory({
        "flat": {"label": "Flat", "engine": "fake", "file": "unused", "order": 1, "room": "small"},
        "dark": {"label": "Dark", "engine": "fake", "file": "unused", "order": 2, "room": "small",
                 "eq": [{"type": "highshelf", "f": 300, "gain_db": -6, "q": 0.707}]},
    }, rooms={"small": {"label": "Small room", "ir": None, "synth": {"rt60_s": 0.5, "predelay_ms": 10, "width": 1.0},
                        "wet_db": -18}})
    yield reg
    render.release_engines()
    engines.unregister("fake")


def test_render_realism_off_keeps_the_eq(fake_eq, midi):
    m = midi([(0.25, "on", 60, 100), (0.5, "off", 60, 64)])
    flat = render.render(m, "flat", realism=False, registry=fake_eq)
    dark = render.render(m, "dark", realism=False, registry=fake_eq)
    a, _ = sf.read(flat.path, dtype="float32")
    b, _ = sf.read(dark.path, dtype="float32")
    ra = math.sqrt(float(np.mean(a.astype(np.float64) ** 2)))
    rb = math.sqrt(float(np.mean(b.astype(np.float64) ** 2)))
    assert 20 * math.log10(rb / ra) == pytest.approx(-6.0, abs=0.5)                # the 1 kHz burst, 6 dB down
    assert flat.room is None and "realism" not in flat.stats
    full = render.render(m, "flat", registry=fake_eq)
    assert full.room == "small" and full.stats["realism"]["room"]["ir_source"] == "synth"
    c, _ = sf.read(full.path, dtype="float32")
    assert c.shape[0] > a.shape[0]                                                 # the room's tail
    assert int(np.flatnonzero(np.abs(c).max(1) > 1e-4)[0]) == int(np.flatnonzero(np.abs(a).max(1) > 1e-4)[0])
