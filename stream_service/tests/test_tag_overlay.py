"""config/live/tags.generated.json: reviewed tags layered over the plug-ins' own (tag_overlay.py, presets.py)."""
from __future__ import annotations

import json
import os
import pathlib
import shutil

from stream_service.plugins import default_registry
from stream_service.presets import Presets
from stream_service.tag_overlay import TagOverlay

REPO_CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config" / "live"
VOCAB = {"genre": ["techno", "house", "jazz", "ambient"], "moods": ["dreamy", "dark", "icy", "warm"],
         "transition_style": ["gentle", "lively"], "palette_family": []}


def overlay(tmp_path, data) -> TagOverlay:
    (tmp_path / "tags.generated.json").write_text(json.dumps({"format": 1, **data}))
    o = TagOverlay(tmp_path)
    assert o.reload_if_changed()
    return o


def test_layers_fields_null_modes_and_notes(tmp_path):
    o = overlay(tmp_path, {"plugins": {"scene": {"foam": {"moods": ["dreamy"], "energy": [0.1, 0.5]}}},
                           "presets": {"scene": {"foam-dusk": {"moods": ["dark"], "palette_family": None,
                                                               "modes": ["piano"], "_why": "a note"}}}})
    have = {"moods": ["icy"], "energy": [0.2, 0.9], "palette_family": ["ocean"], "modes": ["ambient"], "weight": 1.0}
    tags, probs = o.apply("scene", "foam-dusk", "foam", have, VOCAB)
    assert tags["moods"] == ["dark"] and tags["energy"] == [0.1, 0.5] and "palette_family" not in tags
    assert tags["modes"] == ["ambient", "piano"] and "_why" not in tags and probs == []
    other, _ = o.apply("scene", "foam-noon", "foam", have, VOCAB)              # the plug-in layer only
    assert other["moods"] == ["dreamy"] and other["palette_family"] == ["ocean"]
    same, _ = o.apply("scene", "else", "else", have, VOCAB)
    assert same is have                                                         # nothing for it: untouched


def test_genre_map_unknown_words_and_authored(tmp_path):
    o = overlay(tmp_path, {"plugins": {"scene": {"foam": {"genre": {"techno": 1.0, "house": 0.7, "jazz": 0.2,
                                                                     "polka": 0.9}, "moods": ["dreamy", "zesty"]}}}})
    tags, probs = o.apply("scene", "p", "foam", {"weight": 1.0}, VOCAB)
    assert tags["genre"] == ["techno", "house"] and tags["genre_fit"] == {"techno": 1.0, "house": 0.7, "jazz": 0.2}
    assert tags["moods"] == ["dreamy"] and len(probs) == 2                      # polka and zesty listed, dropped
    own, _ = o.apply("scene", "p", "foam", {"moods": ["warm"], "weight": 1.0}, VOCAB, authored=True)
    assert own["moods"] == ["warm"] and own["genre"] == ["techno", "house"]     # a config/live preset's fields win


def test_bad_file_keeps_last_good(tmp_path):
    o = overlay(tmp_path, {"plugins": {"scene": {"foam": {"moods": ["dark"]}}}})
    (tmp_path / "tags.generated.json").write_text("{not json")
    os.utime(tmp_path / "tags.generated.json", ns=(1, 1))
    assert not o.reload_if_changed() and o.errors and o.plugins["scene"]["foam"] == {"moods": ["dark"]}


def test_presets_hot_reload_and_auto_false(tmp_path):
    for f in ("scenes.yaml", "transitions.yaml", "vocab.yaml", "effects.yaml"):
        if (REPO_CONFIG / f).exists():
            shutil.copy(REPO_CONFIG / f, tmp_path / f)
    p = Presets(tmp_path, default_registry())
    name, e = next((n, e) for n, e in p.scenes.items() if e.get("auto") and n not in p._file["scene"])
    rev = p.rev
    genre = (p.vocab.words.get("genre") or ["techno"])[0]
    (tmp_path / "tags.generated.json").write_text(json.dumps({"format": 1, "presets": {"scene": {
        name: {"genre": {genre: 0.9}, "auto": False, "energy": [0.0, 0.4]}}}}))
    assert p.reload_if_changed(force=True) and p.rev > rev
    t = p.scenes[name]["tags"]
    assert t["genre"] == [genre] and t["auto"] is False and t["energy"] == [0.0, 0.4]
    (tmp_path / "tags.generated.json").unlink()
    p.reload_if_changed(force=True)
    assert "genre" not in p.scenes[name]["tags"]
