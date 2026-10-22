import json

import pytest

from piano_render import instruments


def test_registry_resolution_and_availability(registry_factory, tmp_path):
    root = registry_factory.root
    (root / "sf").mkdir(parents=True)
    (root / "sf" / "a.sf2").write_bytes(b"x")
    (root / "_irs").mkdir()
    reg = registry_factory({
        "b": {"label": "B", "engine": "fluidsynth", "file": "sf/a.sf2", "order": 20, "room": "small"},
        "a": {"label": "A", "engine": "fluidsynth", "file": "sf/missing.sf2", "order": 10},
        "basic": {"label": "Basic (Windows synth)", "engine": "windows-gs", "order": 90},
        "x": {"label": "X", "engine": "no-such-engine", "file": "sf/a.sf2", "order": 5},
    }, rooms={"small": {"label": "Small room", "ir": "_irs/s.wav", "synth": None, "wet_db": -16}}, default="b")
    assert reg.ids() == ["x", "a", "b", "basic"]
    assert reg.available() == ["b", "basic"]
    assert reg.default == "b"
    g = reg.get("b")
    assert g["file"] == str(root / "sf" / "a.sf2") and g["id"] == "b"
    assert g["velocity_gamma"] == 1.0 and g["eq"] == [] and g["detune_cents"] == []     # defaults filled in
    assert reg.raw("b") == {"label": "B", "engine": "fluidsynth", "file": "sf/a.sf2", "order": 20, "room": "small"}
    assert reg.rooms["small"]["ir"] == str(root / "_irs" / "s.wav")
    assert "missing" in reg.why_unavailable("a") and "not installed" in reg.why_unavailable("x")
    assert reg.why_unavailable("basic") is None
    with pytest.raises(KeyError):
        reg.get("nope")


def test_load_path_rules(tmp_path, monkeypatch):
    cfg = {"root": "inst", "engines_root": "~/eng", "instruments": {"k": {"engine": "windows-gs"}}}
    p = tmp_path / "cfg" / "i.json"
    p.parent.mkdir()
    p.write_text(json.dumps(cfg))
    monkeypatch.setenv(instruments.CONFIG_ENV, str(p))
    reg = instruments.load()
    assert reg.root == (tmp_path / "cfg" / "inst").resolve()        # relative: against the config's directory
    assert str(reg.engines_root).startswith(str(instruments.Path.home()))
    assert reg.default == "k"
    monkeypatch.setenv(instruments.CONFIG_ENV, str(tmp_path / "nope.json"))
    with pytest.raises(FileNotFoundError):
        instruments.load()
