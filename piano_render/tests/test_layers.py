"""Layers (engines.base.LayeredEngine): a blend of several files of one engine, rendered with identical events and
summed sample-aligned at each layer's gain_db and offset_ms; the registry and the render cache key know the layer
files (fetch's side is in test_fetch.py)."""

from pathlib import Path

import numpy as np
import pytest

from piano_render import engines, render
from piano_render.engines.base import Engine, LayeredEngine, layer_specs

from .conftest import SR, make_sfz


class LevelEngine(Engine):
    """A 100 ms burst per note-on, at an amplitude set by the file's name (LEVELS), ramped so shifts show; loads and
    renders are logged per file."""
    name = "level"
    LEVELS: dict = {}
    log: list = []

    @classmethod
    def available(cls, root):
        return True

    def _load(self, sr):
        LevelEngine.log.append(("load", Path(self.inst["file"]).name))
        self.amp = float(LevelEngine.LEVELS.get(Path(self.inst["file"]).name, 0.0))
        self.burst = np.linspace(1.0, 0.0, int(0.1 * sr), dtype=np.float32) * np.float32(self.amp)
        self.pos, self.active = 0, []

    def _reset(self):
        self.pos, self.active = 0, []
        LevelEngine.log.append(("render", Path(self.inst["file"]).name))

    def _set_pitch(self, c):
        pass

    def _render_block(self, l, r, evs):
        for off, e, v in evs:
            if e.kind == "on":
                self.active.append(self.pos + off)
        n = len(l)
        for s in self.active:
            a, b = max(s, self.pos), min(s + len(self.burst), self.pos + n)
            if b > a:
                l[a - self.pos:b - self.pos] += self.burst[a - s:b - s]
                r[a - self.pos:b - self.pos] -= self.burst[a - s:b - s]
        self.active = [s for s in self.active if s + len(self.burst) > self.pos + n]
        self.pos += n

    def _active_voices(self):
        return len(self.active)

    def close(self):
        if getattr(self, "_loaded_sr", None) is not None:
            LevelEngine.log.append(("close", Path(self.inst["file"]).name))
        self._loaded_sr = None


@pytest.fixture
def level(registry_factory, cache):
    engines.register("level", LevelEngine)
    LevelEngine.LEVELS = {"a.x": 0.5, "b.x": 0.25, "c.x": 0.125}
    LevelEngine.log = []
    render.release_engines()
    root = registry_factory.root
    root.mkdir(parents=True, exist_ok=True)
    for n in ("a.x", "b.x", "c.x"):
        (root / n).write_text(n)
    yield registry_factory
    render.release_engines()
    engines.unregister("level")


def _perf(midi, t=0.25):
    from piano_render import score
    return score.load_midi(midi([(t, "on", 60, 100), (t + 0.2, "off", 60, 64)]))


def _single(reg, f, perf):
    e = engines.create(dict(reg.get("one"), file=str(reg.root / f)), reg.engines_root)
    try:
        return e.render(perf, SR)
    finally:
        e.close()


def test_layers_sum_with_gain_and_offset(level, midi):
    reg = level({
        "one": {"label": "One", "engine": "level", "file": "a.x", "order": 1},
        "blend": {"label": "Blend", "engine": "level", "file": "a.x", "order": 2, "layers": [
            {"file": "a.x"}, {"file": "b.x", "gain_db": -6.0206, "offset_ms": 2.0}, {"file": "c.x", "offset_ms": -1.0}]},
    })
    perf = _perf(midi)
    a, b, c = (_single(reg, f, perf) for f in ("a.x", "b.x", "c.x"))
    eng = engines.create(reg.get("blend"), reg.engines_root)
    assert isinstance(eng, LayeredEngine)
    out = eng.render(perf, SR)
    want = np.zeros((max(len(a), len(b) + 96, len(c) - 48), 2), np.float32)
    want[:len(a)] += a
    want[96:96 + len(b)] += 0.5 * b
    want[:len(c) - 48] += c[48:]
    assert out.shape == want.shape and np.allclose(out, want, atol=1e-6)
    f0 = int(round(0.25 * SR))                      # sample 0 is MIDI 0: the 0 ms layer starts exactly on time
    assert out[f0 - 49, 0] == 0.0 and out[f0 - 48, 0] > 0.0 and np.isclose(out[f0, 0], 0.5 + 0.125 * (1 - 48 / 4800),
                                                                           atol=1e-4)
    st = eng.stats
    assert [p["file"] for p in st["layers"]] == [str(reg.root / f) for f in ("a.x", "b.x", "c.x")]
    assert st["audio_s"] == round(out.shape[0] / SR, 3) and st["tail_end"] in ("voices", "floor", "cap")
    eng.close()


def test_one_layer_loaded_at_a_time_and_the_sum_is_order_independent(level, midi):
    reg = level({"blend": {"label": "Blend", "engine": "level", "file": "a.x", "order": 1, "gain_db": 3.0,
                           "layers": [{"file": "a.x"}, {"file": "b.x", "gain_db": -3}]}})
    perf = _perf(midi)
    eng = engines.create(reg.get("blend"), reg.engines_root)
    eng.ensure_loaded(SR)
    assert LevelEngine.log == [("load", "a.x")]
    first = eng.render(perf, SR)
    assert LevelEngine.log == [("load", "a.x"), ("render", "a.x"), ("close", "a.x"), ("load", "b.x"), ("render", "b.x")]
    LevelEngine.log = []
    eng.ensure_loaded(SR)                           # b is still loaded: nothing to do
    second = eng.render(perf, SR)
    assert LevelEngine.log == [("render", "b.x"), ("close", "b.x"), ("load", "a.x"), ("render", "a.x")]
    assert np.array_equal(first, second)
    # each layer's engine got the instrument's gain_db plus its own (its tail floor is the one it is heard at)
    assert [p["tail_floor_dbfs"] for p in eng.stats["layers"]] == [-73.0, -70.0]
    eng.close()
    assert LevelEngine.log[-1] == ("close", "a.x")


def test_layer_specs_and_errors():
    assert layer_specs({"layers": [{"file": "x"}, {"file": "y", "gain_db": -4, "offset_ms": 1.5}]}) == [
        {"file": "x", "gain_db": 0.0, "offset_ms": 0.0}, {"file": "y", "gain_db": -4.0, "offset_ms": 1.5}]
    assert layer_specs({}) == []
    with pytest.raises(engines.EngineError, match="layer 1 has no file"):
        layer_specs({"id": "z", "layers": [{"file": "x"}, {"gain_db": 0}]})
    with pytest.raises(engines.EngineError, match="unknown engine"):
        engines.create({"engine": "nope", "layers": [{"file": "x"}]}, "/tmp")


def test_registry_resolves_layers_and_needs_every_file(level):
    reg = level({
        "blend": {"label": "Blend", "engine": "level", "file": "a.x", "order": 1,
                  "layers": [{"file": "a.x"}, {"file": "b.x", "gain_db": -4}]},
        "broken": {"label": "Broken", "engine": "level", "file": "a.x", "order": 2,
                   "layers": [{"file": "a.x"}, {"file": "missing.x"}]},
    })
    inst = reg.get("blend")
    assert [layer["file"] for layer in inst["layers"]] == [str(reg.root / "a.x"), str(reg.root / "b.x")]
    assert inst["layers"][1]["gain_db"] == -4 and reg.raw("blend")["layers"][1]["file"] == "b.x"
    assert reg.why_unavailable("blend") is None and "missing layer" in reg.why_unavailable("broken")
    assert reg.available() == ["blend"]


def test_render_key_holds_every_layer_file(level, midi, cache):
    reg = level({
        "one": {"label": "One", "engine": "level", "file": "a.x", "order": 1},
        "blend": {"label": "Blend", "engine": "level", "file": "a.x", "order": 2,
                  "layers": [{"file": "a.x"}, {"file": "b.x", "gain_db": -6}]},
    })
    m = midi([(0.1, "on", 60, 100), (0.3, "off", 60, 64)])
    # a single-file instrument's key is what it was before layers existed
    assert render.inst_files_digest(reg.get("one")) == render.instrument_files_digest(reg.get("one")["file"])
    k0, one0 = render.plan(m, "blend", registry=reg).key, render.plan(m, "one", registry=reg).key
    r0 = render.render(m, "blend", registry=reg)
    assert not r0.cached and r0.cache_key == k0 and render.render(m, "blend", registry=reg).cached
    assert len(r0.stats["engine"]["layers"]) == 2
    (reg.root / "b.x").write_text("b changed")                  # the second layer's file changes
    assert render.plan(m, "blend", registry=reg).key != k0 and render.plan(m, "one", registry=reg).key == one0
    # a layered instrument never reuses the single-file engine kept for the same main file
    render.render(m, "one", registry=reg)
    r1 = render.render(m, "blend", registry=reg)
    assert not r1.cached and len(r1.stats["engine"]["layers"]) == 2


def test_sfizz_layers_are_sample_aligned(registry_factory, sfizz_tree, midi):
    """Two layers of the same SFZ at 0 and -6 dB sum to 1.5x the single render, sample for sample; a 1 ms offset
    moves the second by exactly 48 samples."""
    sfz = make_sfz(registry_factory.root / "synth")
    rel = str(sfz.relative_to(registry_factory.root))
    reg = registry_factory({
        "one": {"label": "One", "engine": "sfizz", "file": rel, "order": 1},
        "blend": {"label": "Blend", "engine": "sfizz", "file": rel, "order": 2,
                  "layers": [{"file": rel}, {"file": rel, "gain_db": -6.0206}]},
        "late": {"label": "Late", "engine": "sfizz", "file": rel, "order": 3,
                 "layers": [{"file": rel}, {"file": rel, "gain_db": -6.0206, "offset_ms": 1.0}]},
    })
    from piano_render import score
    perf = score.load_midi(midi([(0.2, "on", 69, 100), (0.6, "off", 69, 64)]))
    outs = {}
    for iid in ("one", "blend", "late"):
        e = engines.create(reg.get(iid), reg.engines_root)
        outs[iid] = e.render(perf, SR)
        e.close()
    one, blend, late = outs["one"], outs["blend"], outs["late"]
    n = min(len(one), len(blend))
    assert np.allclose(blend[:n], 1.5 * one[:n], atol=1e-5)
    m = min(len(one) + 48, len(late))
    want = np.zeros((m, 2), np.float32)
    want[:min(m, len(one))] += one[:m]
    want[48:m] += 0.5 * one[:m - 48]
    assert np.allclose(late[:m], want, atol=1e-5)
