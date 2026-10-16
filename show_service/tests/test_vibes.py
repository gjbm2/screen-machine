"""vibes: the real vibes.yaml loads clean; tmp files for the load rules (neutral fill, bad values, unknown fields and
words, a bad file keeps the last good, hot reload); check_target's canonical forms and errors; effective; catalog."""

from __future__ import annotations

import os

import pytest

from show_service import contract
from show_service import vibes as vmod

VOCAB = """moods: [calm, mellow, warm, bright, euphoric]
genre: [jazz, soul]
transition_style: [gentle, lively, wild]
palette_family: [warm, cool, vivid]
"""
PALETTES = ("ember", "gold", "neon")


@pytest.fixture
def files(tmp_path):
    (tmp_path / "vocab.yaml").write_text(VOCAB)
    path = tmp_path / "vibes.yaml"

    def make(text: str) -> vmod.Vibes:
        path.write_text(text)
        v = vmod.Vibes(path, vocab_path=tmp_path / "vocab.yaml", palette_names=lambda: PALETTES)
        v.poll(force=True)
        return v
    return path, make


def bump(path, text: str) -> None:
    """Rewrite a file so its mtime (ns) and size both move, whatever the filesystem's clock resolution."""
    st = path.stat()
    path.write_text(text)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def test_the_real_vibes_file_loads_clean():
    v = vmod.Vibes(palette_names=lambda: ("ember", "gold", "dusk", "ice", "ocean", "aurora", "neon", "rainbow",
                                          "acid", "mono"))
    assert v.poll(force=True)
    assert v.error is None, v.error
    assert v.problems == [], v.problems
    assert v.order and len(v.revision) == 12
    for n in v.names():
        s = v.get(n)
        assert tuple(s) == contract.VIBE_FIELDS
        lo, hi = s["energy"]
        assert lo <= s["energy_centre"] <= hi


def test_neutral_fill_and_per_vibe_neutrals(files):
    _, make = files
    v = make("version: 1\nvibes:\n  plain: {}\n  half:\n    energy: [0.2, 0.6]\n")
    plain = v.get("plain")
    assert plain["label"] == "plain"                              # the name
    assert {k: plain[k] for k in contract.VIBE_FIELDS if k != "label"} == \
        {k: vmod.NEUTRAL[k] for k in contract.VIBE_FIELDS if k != "label"}
    assert v.get("half")["energy_centre"] == pytest.approx(0.4)   # the middle of energy
    assert v.get("nope") is None
    assert v.order == ["half", "plain"]                            # no order: alphabetical


def test_bad_values_fall_to_neutral_with_problem_lines(files):
    _, make = files
    v = make("""vibes:
  x:
    level: [0.9, 0.2]
    floor: 2
    accents: sparkle
    beat_lock: "yes"
    max_steps: 2.5
    wobble: 3
    moods: [warm, grumpy]
    palettes: [ember, plaid, art]
    follow: [energy, vibes]
    genre: polka
    key_cct: 2400
""")
    x = v.get("x")
    assert x["level"] == vmod.NEUTRAL["level"] and x["floor"] == vmod.NEUTRAL["floor"]
    assert x["accents"] == "none" and x["beat_lock"] is True and x["max_steps"] == 3 and x["genre"] is None
    assert x["moods"] == ["warm"] and x["palettes"] == ["ember", "art"] and x["follow"] == ["energy"]
    assert x["key_cct"] == 2400.0                                 # the good fields stay
    text = "\n".join(v.problems)
    assert "vibes.x.level: [0.9, 0.2] is not a range" in text
    for frag in ("vibes.x.floor", "vibes.x.accents", "vibes.x.beat_lock", "vibes.x.max_steps",
                 "vibes.x.wobble: unknown field", "'grumpy'", "'plaid'", "'vibes'", "vibes.x.genre"):
        assert frag in text, frag
    assert v.error is None


def test_bad_names_and_order(files):
    _, make = files
    v = make("order: [b, ghost, a]\nvibes:\n  a: {}\n  b: {}\n  c: {}\n  Bad Name: {}\n")
    assert v.order == ["b", "a", "c"]
    assert any("ghost" in p for p in v.problems) and any("Bad Name" in p for p in v.problems)


def test_bad_file_keeps_the_last_good_and_hot_reload(files):
    path, make = files
    v = make("vibes:\n  a: {energy: [0.1, 0.3]}\n")
    rev = v.revision
    assert not v.poll()                                           # unchanged
    bump(path, "vibes:\n  a: {energy: [0.1, 0.3]\n")               # broken YAML
    assert not v.poll()
    assert v.error and v.get("a")["energy"] == [0.1, 0.3] and v.revision == rev
    bump(path, "- just\n- a list\n")
    assert not v.poll() and "not a mapping" in v.error
    bump(path, "vibes:\n  a: {energy: [0.5, 0.9]}\n  b: {}\n")
    assert v.poll()
    assert v.error is None and v.get("a")["energy"] == [0.5, 0.9] and v.names() == ["a", "b"]
    assert v.revision != rev


def test_words_are_kept_when_vocab_cannot_be_read(tmp_path):
    p = tmp_path / "vibes.yaml"
    p.write_text("vibes:\n  a: {moods: [warm, anything]}\n")
    v = vmod.Vibes(p, vocab_path=tmp_path / "missing.yaml", palette_names=lambda: ())
    v.poll(force=True)
    assert v.get("a")["moods"] == ["warm", "anything"]
    assert any("vocab" in x for x in v.problems)


def test_palette_names_are_checked_once_the_catalog_appears(tmp_path):
    (tmp_path / "vocab.yaml").write_text(VOCAB)
    p = tmp_path / "vibes.yaml"
    p.write_text("vibes:\n  a: {palettes: [ember, plaid]}\n")
    names: list = []
    v = vmod.Vibes(p, vocab_path=tmp_path / "vocab.yaml", palette_names=lambda: names)
    v.poll(force=True)
    assert v.get("a")["palettes"] == ["ember", "plaid"]           # no catalog yet: nothing to check against
    names[:] = ["ember"]
    assert v.poll()                                               # the catalog appeared: re-checked
    assert v.get("a")["palettes"] == ["ember"]


# -- the target -------------------------------------------------------------------------------------------------------
def test_check_target_canonical_forms(files):
    _, make = files
    v = make("vibes:\n  a: {}\n  b: {}\n")
    assert v.check_target("a") == {"vibe": "a", "listening": False}
    assert v.check_target({"name": "b"}) == {"vibe": "b", "listening": False}
    assert v.check_target({"vibe": "a", "listening": True}) == {"vibe": "a", "listening": True}
    assert v.check_target({"vibe": "a", "overrides": {}}) == {"vibe": "a", "listening": False}
    t = v.check_target({"vibe": "a", "listening": True, "overrides": {"strobe": True, "level": [0.2, 0.4]}})
    assert t == {"vibe": "a", "listening": True, "overrides": {"strobe": True, "level": [0.2, 0.4]}}


@pytest.mark.parametrize("body", [
    3, {"vibe": "Bad Name"}, {"vibe": 7}, {"vibe": "a", "listening": "yes"}, {"vibe": "a", "extra": 1},
    {"vibe": "a", "name": "b"}, {"vibe": "a", "overrides": []}, {"vibe": "a", "overrides": {3: 1}},
])
def test_only_a_body_that_isnt_a_target_is_refused(files, body):
    _, make = files
    v = make("vibes:\n  a: {}\n  b: {}\n")
    with pytest.raises(ValueError) as e:
        v.check_target(body)
    assert str(e.value).startswith("bad_target:")


@pytest.mark.parametrize("body", [None, {}, {"vibe": None}, {"vibe": None, "listening": True}, {"name": None}])
def test_no_feel_is_the_neutral_target(files, body):
    _, make = files
    v = make("vibes:\n  a: {}\n")
    assert v.check_target(body) is None


def test_an_unknown_vibe_or_bad_override_is_kept_as_asked_and_degrades(files):
    """Degrade, never refuse: the target is kept as asked (the room's read-back matches), an unknown vibe plays
    neutral and a bad override is dropped; target_problems says what, in page words."""
    _, make = files
    v = make("vibes:\n  a: {level: [0.3, 0.6]}\n")
    t = v.check_target({"vibe": "zz", "listening": True})
    assert t == {"vibe": "zz", "listening": True}
    s, known = v.effective(t)
    assert not known and s["level"] == vmod.NEUTRAL["level"]
    assert v.target_problems(t) == ["the feel 'zz' isn't in vibes.yaml: the neutral look plays"]
    body = {"vibe": "a", "overrides": {"floor": 9, "persona": "x", "accents": "huge", "moods": ["warm", "grumpy"],
                                       "nonsense": 1, "level": [0.1, 0.2], "genre": None}}
    t = v.check_target(body)
    assert t == {"vibe": "a", "listening": False,                       # as asked; a null override is unset
                 "overrides": {k: x for k, x in body["overrides"].items() if x is not None}}
    s, known = v.effective(t)
    assert known and s["level"] == [0.1, 0.2] and s["floor"] == vmod.NEUTRAL["floor"]
    assert s["accents"] == "none" and s["moods"] == ["warm"]
    probs = v.target_problems(t)
    for word in ("overrides.floor", "overrides.persona", "overrides.accents", "overrides.moods: 'grumpy'",
                 "overrides.nonsense"):
        assert any(p.startswith(word) for p in probs), (word, probs)
    assert not any(p.startswith("overrides.level") for p in probs)


def test_auto_is_a_word_field_s_neutral(files):
    _, make = files
    v = make("vibes:\n  a: {genre: jazz, transition_style: wild}\n")
    s, _ = v.effective({"vibe": "a", "listening": False, "overrides": {"genre": "auto", "transition_style": "auto"}})
    assert s["genre"] is None and s["transition_style"] is None
    assert v.target_problems({"vibe": "a", "listening": False, "overrides": {"genre": "auto"}}) == []


def test_effective_with_overrides_and_unknown(files):
    _, make = files
    v = make("vibes:\n  a: {energy: [0.2, 0.8], level: [0.3, 0.6], label: Aye}\n")
    s, known = v.effective({"vibe": "a", "listening": False, "overrides": {"level": [0.1, 0.2], "energy": [0.4, 0.5]}})
    assert known and s["level"] == [0.1, 0.2] and s["energy_centre"] == pytest.approx(0.45) and s["label"] == "Aye"
    s, known = v.effective({"vibe": "gone", "listening": True})
    assert not known and s["label"] == "gone" and s["level"] == vmod.NEUTRAL["level"]
    s, known = v.effective(None)
    assert not known and s == vmod.NEUTRAL
    a, _ = v.effective({"vibe": "a", "listening": False})
    a["level"][0] = 0.99                                          # a caller's copy never leaks back
    assert v.get("a")["level"] == [0.3, 0.6]


def test_catalog_shape(files):
    _, make = files
    v = make("order: [a]\nvibes:\n  a: {label: Aye, description: hi, moods: [warm]}\n")
    choices = [{"name": n, "label": n.title(), "family": None, "swatch": []} for n in PALETTES] + \
        [{"name": "art", "label": "From the art", "family": None, "swatch": []}]
    c = v.catalog(choices)
    assert set(c) == set(contract.VibesCatalog.__annotations__)
    assert tuple(c["fields"]) == contract.VIBE_FIELDS
    assert c["vibes"]["a"] == {"label": "Aye", "description": "hi", "fields": {"label": "Aye", "description": "hi",
                                                                               "moods": ["warm"]}}
    assert c["fields"]["palettes"]["items"]["enum"] == list(PALETTES) + ["art"]
    assert c["fields"]["genre"]["enum"] == ["jazz", "soul", "auto"]       # the neutral as a real choice
    assert c["neutral"] == vmod.NEUTRAL and c["vocab"]["moods"][0] == "calm"
    assert "enum" not in vmod.FIELD_SCHEMAS["palettes"]["items"]  # the module's schemas are never mutated


def test_field_schemas_match_the_neutral_values():
    for f in contract.VIBE_FIELDS:
        sch = vmod.FIELD_SCHEMAS[f]
        assert sch["type"] in ("string", "number", "integer", "boolean", "array") and "label" in sch["x-ui"]
        if "default" in sch and f not in ("label",):
            assert sch["default"] == vmod.NEUTRAL[f], f
        val, probs, ok = vmod.check_field(f, vmod.NEUTRAL[f], vocab=None, palette_names=())
        assert ok and not probs, f                                # every neutral value passes its own check
