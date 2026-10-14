"""Registry, schema and parameter validation, easing (no GPU needed)."""
from __future__ import annotations

import math

import jsonschema
import pytest

from stream_service.easing import ease
from stream_service.plugins import Registry, default_registry
from stream_service.plugins.base import ParamEaser, ParamError, Source, Transition, validate_params


def test_discovery_finds_v1_plugins():
    r = default_registry()
    assert {"image", "solid", "fractal_zoom"} <= set(r.sources)
    assert {"cut", "crossfade"} <= set(r.transitions)
    assert r.sources["image"].internal is True
    assert r.public_sources() == sorted(n for n in r.sources if n != "image")


def test_catalog_entries_and_schemas_are_valid_json_schema():
    cat = default_registry().catalog()
    assert cat["filters"] == []
    for entry in cat["sources"] + cat["transitions"]:
        assert entry["name"] and entry["label"] and "description" in entry
        schema = entry["schema"]
        jsonschema.Draft202012Validator.check_schema(schema)
        for name, prop in schema["properties"].items():
            assert "default" in prop, f"{entry['name']}.{name} has no default"
            # every default validates against its own property
            jsonschema.validate(prop["default"], {k: v for k, v in prop.items() if not k.startswith("x-")})
            w = (prop.get("x-ui") or {}).get("widget")
            assert w in (None, "slider", "number", "select", "checkbox", "color")
    for t in cat["transitions"]:
        props = t["schema"]["properties"]
        assert "duration_s" in props and "easing" in props
        assert props["easing"]["enum"] == ["linear", "ease-in-out", "ease-in", "ease-out"]
    for s in cat["sources"]:
        assert "internal" in s


def test_validate_rejects_unknown_and_clamps():
    fz = default_registry().source("fractal_zoom")
    full = fz.validate({"zoom_rate": 99, "iterations": 3.6, "palette": "ice"})
    assert full["zoom_rate"] == 2.0                      # clamped to maximum
    assert full["iterations"] == 16                      # clamped to minimum, integer
    assert full["palette"] == "ice"
    assert set(full) == set(fz.param_props())            # defaults filled in
    with pytest.raises(ParamError) as e:
        fz.validate({"speed": 1})
    assert e.value.code == "unknown_param"
    for bad in ({"palette": "sepia"}, {"zoom_rate": "fast"}, {"zoom_rate": True}, {"zoom_rate": float("nan")},
                {"aa": "2"}):
        with pytest.raises(ParamError) as e:
            fz.validate(bad)
        assert e.value.code == "bad_param"
    assert fz.validate({"aa": 7})["aa"] == 2             # clamp then enum membership


def test_validate_merges_onto_base():
    fz = default_registry().source("fractal_zoom")
    base = fz.validate({"palette": "neon", "zoom_rate": 0.5})
    merged = fz.validate({"zoom_rate": 0.1}, base=base)
    assert merged["palette"] == "neon" and merged["zoom_rate"] == 0.1


def test_color_params():
    solid = default_registry().source("solid")
    assert solid.validate({"rgb": "#AABBCC"})["rgb"] == "#aabbcc"
    assert solid.validate({"rgb": [255, 0, 300]})["rgb"] == "#ff00ff"
    with pytest.raises(ParamError):
        solid.validate({"rgb": "red"})


def test_transition_common_params():
    xf = default_registry().transition("crossfade")
    p = xf.validate({})
    assert p == {"duration_s": 3.0, "easing": "ease-in-out"}
    assert xf.validate({"duration_s": 1000})["duration_s"] == 60.0
    cut = default_registry().transition("cut")
    assert cut.validate({})["duration_s"] == 0.0
    c = cut({"duration_s": 1.0, "easing": "linear"})
    assert c.needs(0.5) == (True, False)


def test_registry_rejects_bad_plugins():
    class NoDefault(Source):
        name = "nodefault"
        params = {"x": {"type": "number"}}

    class Dup(Transition):
        name = "crossfade"

    r = Registry().discover()
    with pytest.raises(TypeError):
        r.register(NoDefault)
    with pytest.raises(TypeError):
        r.register(Dup)


def test_param_easer_time_constant():
    props = {"x": {"type": "number", "default": 0.0}, "n": {"type": "integer", "default": 0},
             "mode": {"type": "string", "enum": ["a", "b"], "default": "a"},
             "fixed": {"type": "number", "default": 0.0, "x-ease": False}}
    e = ParamEaser(props, validate_params(props, {}), tau_s=0.8)
    e.set_targets({"x": 1.0, "n": 10, "mode": "b", "fixed": 5.0})
    assert e.values()["mode"] == "b" and e.values()["fixed"] == 5.0   # non-numeric / x-ease false: at once
    for _ in range(48):                                               # 0.8 s at 60 fps
        e.step(1 / 60)
    assert e.values()["x"] == pytest.approx(1 - math.exp(-1), abs=1e-6)
    assert isinstance(e.values()["n"], int)
    for _ in range(60 * 30):
        e.step(1 / 60)
    assert e.values()["x"] == 1.0 and e.settled


def test_easing_curves():
    for name in ("linear", "ease-in-out", "ease-in", "ease-out"):
        assert ease(name, 0) == 0 and ease(name, 1) == 1
        ys = [ease(name, i / 100) for i in range(101)]
        assert all(b >= a - 1e-9 for a, b in zip(ys, ys[1:])), name
    assert ease("ease-in-out", 0.5) == pytest.approx(0.5, abs=1e-6)
    assert ease("ease-in", 0.5) < 0.5 < ease("ease-out", 0.5)
    # CSS cubic-bezier(0.42, 0, 0.58, 1) at x = 0.25 is ~0.1291
    assert ease("ease-in-out", 0.25) == pytest.approx(0.1291, abs=2e-3)
