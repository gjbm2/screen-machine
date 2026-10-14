"""Bindings, derived masters and the session palette (modulation.py; no GPU)."""
from __future__ import annotations

import pathlib

import numpy as np
import pytest

from stream_service import palettes
from stream_service.layers import FrameCtx, SourceLayer
from stream_service.modulation import Modulation, SessionRuntime, parse_binding, speed_for, split_bindings
from stream_service.plugins import default_registry
from stream_service.plugins.base import ParamError
from stream_service.presets import Presets

REPO_CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config" / "live"
FZ = default_registry().source("fractal_zoom")
WS = default_registry().source("warp_stack")


def test_binding_defaults_rest_by_signal_kind():
    props = FZ.param_props()
    trig = parse_binding("exposure", props["exposure"], {"bind": "beat", "range": [1.0, 1.4]})
    assert trig == {"bind": "beat", "range": [1.0, 1.4], "rest": 1.0}           # triggers rest at lo
    lvl = parse_binding("zoom_rate", props["zoom_rate"], {"bind": "activity_slow", "range": [0.1, 0.5]})
    assert lvl["rest"] == pytest.approx(0.3)                                      # levels rest at the midpoint
    full = parse_binding("zoom_rate", props["zoom_rate"], {"bind": "level"})
    assert full["range"] == [-2.0, 2.0]                                           # default: the param's range
    clamped = parse_binding("exposure", props["exposure"], {"bind": "pulse", "range": [0, 99], "rest": -5})
    assert clamped["range"] == [0.0, 3.0] and clamped["rest"] == 0.0


@pytest.mark.parametrize("spec,detail", [
    ({"bind": "energy", "range": [0, 1]}, "unknown signal"),          # renamed to activity_slow (R19)
    ({"bind": "rgb", "range": [0, 1]}, "unknown signal"),             # rgb is not bindable in v1
    ({"kick": "beat", "amount": 0.3}, "binding"),                     # v1 is {bind, range, rest}
    ({"bind": "beat", "curve": 2}, "unknown binding keys"),
    ({"bind": "beat", "range": [0, "x"]}, "finite"),
])
def test_bad_bindings(spec, detail):
    with pytest.raises(ParamError) as e:
        split_bindings(FZ.param_props(), {"exposure": spec})
    assert detail in e.value.detail


def test_only_bindable_params_take_bindings():
    with pytest.raises(ParamError) as e:
        split_bindings(FZ.param_props(), {"iterations": {"bind": "beat"}})
    assert "not bindable" in e.value.detail


def test_split_merge_replace_and_restore():
    props = FZ.param_props()
    plain, b = split_bindings(props, {"exposure": {"bind": "beat", "range": [1, 2]}, "zoom_rate": 0.4})
    assert plain == {"exposure": 1.0, "zoom_rate": 0.4} and set(b) == {"exposure"}
    # a value replaces the binding
    plain2, b2 = split_bindings(props, {"exposure": 1.5}, b)
    assert b2 == {} and plain2 == {"exposure": 1.5}
    # null restores the preset's binding (or value)
    restore = ({"exposure": 1.0, "zoom_rate": 0.25}, {"exposure": b["exposure"]})
    plain3, b3 = split_bindings(props, {"exposure": None, "zoom_rate": None}, b2, restore)
    assert b3 == {"exposure": b["exposure"]} and plain3 == {"exposure": 1.0, "zoom_rate": 0.25}


def test_resolve_value_rest_depth_and_idle():
    props = FZ.param_props()
    _, b = split_bindings(props, {"exposure": {"bind": "beat", "range": [1.0, 1.4]},
                                  "zoom_rate": {"bind": "activity_slow", "range": [0.1, 0.5]},
                                  "iterations": 120})
    rt = SessionRuntime(None)
    m = Modulation(rt, props, b)
    base = FZ.validate({"exposure": 1.0, "zoom_rate": 0.3})
    rt.signals = {"beat": 1.0, "activity_slow": 1.0}
    out = m.apply(base)
    assert out["exposure"] == pytest.approx(1.4) and out["zoom_rate"] == pytest.approx(0.5)
    rt.depth = 0.5                                   # intensity = depth: half the swing about rest
    out = m.apply(base)
    assert out["exposure"] == pytest.approx(1.2) and out["zoom_rate"] == pytest.approx(0.4)
    rt.depth = 1.0
    rt.signals = {"beat": 0.0, "activity_slow": 0.5}  # idle values: triggers 0, levels 0.5 -> rest
    out = m.apply(base)
    assert out["exposure"] == pytest.approx(1.0) and out["zoom_rate"] == pytest.approx(0.3)
    assert m.describe()[0]["param"] in ("exposure", "zoom_rate") and "value" in m.describe()[0]


def test_presets_carry_bindings_and_plain_params():
    p = Presets(REPO_CONFIG, default_registry())
    for name in ("fractal-zoom", "warp-kaleido", "warp-ink-tunnel"):
        spec = p.resolve_scene(name)
        assert spec.bindings, name
        assert all(not isinstance(v, dict) for v in spec.params.values())
        assert spec.params["render_scale" if spec.source == "warp_stack" else "exposure"] in (0.5, 1.0)
    warp = p.resolve_scene("warp-kaleido")
    assert warp.bindings["energy"]["bind"] == "activity_slow"     # the warp_stack *param* named energy


def test_speed_master_is_the_scene_clock_only():
    assert speed_for(0.0) == 0.5 and speed_for(0.5) == 1.0 and speed_for(1.0) == 2.0
    rt = SessionRuntime(None, speed=2.0)

    class Src:
        def __init__(self, mod):
            self.modulation = mod
            self.calls = []

        def render(self, t, dt, out):
            self.calls.append((round(t, 6), round(dt, 6)))

    scene = Src(Modulation(rt, {}, {}))
    still = Src(None)                               # the image source has no modulation: real time (R1)
    for src in (scene, still):
        layer = SourceLayer(src, 0.0)
        for n in range(3):
            layer.render(FrameCtx(n, 10, pool=None), None)
    assert scene.calls == [(0.0, 0.0), (0.2, 0.2), (0.4, 0.2)]
    assert still.calls == [(0.0, 0.0), (0.1, 0.1), (0.2, 0.1)]


def test_masters_ease_and_palette_crossfade():
    rt = SessionRuntime(None, speed=1.0, depth=1.0, ease_s=4.0)
    rt.speed_target, rt.depth_target = 2.0, 0.2
    t = 0.0
    rt.begin_frame(t)
    for _ in range(4 * 60):                   # 4 s at 60 fps: ~98 % there
        t += 1 / 60
        rt.begin_frame(t)
    assert rt.speed == pytest.approx(2.0, abs=0.03) and rt.depth == pytest.approx(0.2, abs=0.02)
    rt.palette = "ember"
    rt.begin_frame(t + 1 / 60)
    assert np.allclose(rt.palette_rows, palettes.cosine("ember"))      # first set: at once (sources ease)
    rt.palette, rt.palette_s = "ice", 2.0      # a change crossfades over palette_transition_s
    t += 1 / 60
    for _ in range(60):
        t += 1 / 60
        rt.begin_frame(t)
    mid = rt.palette_rows.copy()
    assert not np.allclose(mid, palettes.cosine("ice")) and not np.allclose(mid, palettes.cosine("ember"))
    for _ in range(70):
        t += 1 / 60
        rt.begin_frame(t)
    assert np.allclose(rt.palette_rows, palettes.cosine("ice"))


def test_schema_marks():
    for cls in (FZ, WS):
        props = cls.param_props()
        assert props["palette"].get("x-palette") is True
        assert all(p.get("x-bindable") in (None, True) for p in props.values())
        basic = [k for k, p in props.items() if (p.get("x-ui") or {}).get("basic")]
        assert basic and not set(basic) & {"energy", "exposure", "gain"}
    assert [k for k, p in FZ.param_props().items() if (p.get("x-ui") or {}).get("basic")] == ["zoom_rate", "palette"]
