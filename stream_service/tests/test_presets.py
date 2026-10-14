"""Preset loading, hot reload and spec resolution (LIVE_V1 §1.1 spec forms)."""
from __future__ import annotations

import os
import pathlib
import time

import pytest

from stream_service.plugins import default_registry
from stream_service.plugins.base import ParamError
from stream_service.presets import Presets

REPO_CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config" / "live"


@pytest.fixture
def cfg(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "scenes.yaml").write_text(
        "zoom:\n  label: Zoom\n  source: fractal_zoom\n  params: {zoom_rate: 0.4, palette: ice}\n"
        "bad-param:\n  source: fractal_zoom\n  params: {speed: 1}\n"
        "bad-source:\n  source: nope\n"
        "internal:\n  source: image\n")
    (tmp_path / "transitions.yaml").write_text(
        "xf2:\n  label: Crossfade 2 s\n  transition: crossfade\n  params: {duration_s: 2, easing: linear}\n")
    return tmp_path


def test_repo_presets_load_and_contain_the_contract_names():
    p = Presets(REPO_CONFIG, default_registry())
    assert "fractal-zoom" in p.scenes and "crossfade-3s" in p.transitions   # used by modes.yaml / LIVE_V1
    s = p.resolve_scene("fractal-zoom")
    assert s.source == "fractal_zoom" and s.preset == "fractal-zoom"
    t = p.resolve_transition("crossfade-3s")
    assert t.params == {"duration_s": 3.0, "easing": "ease-in-out"}


def test_invalid_presets_are_skipped(cfg):
    p = Presets(cfg, default_registry(), plugin_presets=False)
    assert set(p.scenes) == {"zoom"}   # unknown param, unknown source, internal source all skipped


def test_spec_forms(cfg):
    p = Presets(cfg, default_registry())
    a = p.resolve_scene("zoom")
    assert a.params["zoom_rate"] == 0.4 and a.params["palette"] == "ice" and a.label == "Zoom"
    b = p.resolve_scene({"preset": "zoom", "params": {"zoom_rate": 9}})
    assert b.params["zoom_rate"] == 2.0 and b.params["palette"] == "ice" and b.preset == "zoom"
    c = p.resolve_scene({"source": "fractal_zoom", "params": {"palette": "mono"}})
    assert c.preset is None and c.params["palette"] == "mono" and c.params["zoom_rate"] == 0.25
    d = p.resolve_transition({"transition": "cut"})
    assert d.transition == "cut" and d.params["duration_s"] == 0.0
    e = p.resolve_transition({"preset": "xf2", "params": {"easing": "ease-out"}})
    assert e.params == {"duration_s": 2.0, "easing": "ease-out"}


@pytest.mark.parametrize("spec,code", [
    ("nope", "unknown_preset"),
    ({"preset": "zoom", "source": "solid"}, "bad_spec"),
    ({"preset": "zoom", "extra": 1}, "bad_spec"),
    ({"source": "fractal_zoom", "params": {"speed": 1}}, "unknown_param"),
    ({"source": "image"}, "unknown_source"),
    ({"source": "fractal_zoom", "params": [1]}, "bad_param"),
    (42, "bad_spec"),
])
def test_bad_scene_specs(cfg, spec, code):
    p = Presets(cfg, default_registry())
    with pytest.raises(ParamError) as e:
        p.resolve_scene(spec)
    assert e.value.code == code


def test_unknown_transition(cfg):
    p = Presets(cfg, default_registry())
    with pytest.raises(ParamError) as e:
        p.resolve_transition({"transition": "nope"})
    assert e.value.code == "unknown_transition"


def test_hot_reload_and_parse_error_keeps_last_good(cfg):
    p = Presets(cfg, default_registry(), plugin_presets=False)
    f = cfg / "transitions.yaml"
    f.write_text("cut-now:\n  transition: cut\n")
    os.utime(f, ns=(time.time_ns(), time.time_ns() + 10_000_000))
    assert p.reload_if_changed() is True
    assert set(p.transitions) == {"cut-now"}
    f.write_text("this: [is: not yaml")
    os.utime(f, ns=(time.time_ns(), time.time_ns() + 20_000_000))
    p.reload_if_changed()
    assert set(p.transitions) == {"cut-now"}
