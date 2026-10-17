"""tools/mapgen.py: the rules and the art director's loudness-once rule (75 / c8 compose, 17 Oct): a luminance
binding (exposure, glow, contrast) never reads loudness and an item has at most one picture route."""
from __future__ import annotations

import json

from stream_service.tools import mapgen
from stream_service.tools.mapgen import LOUDNESS, LUMINANCE_FAMS, RULES, TRANSITION_RULES, compose_rule, draft_item


def num(lo=0.0, hi=2.0, default=1.0, tier="realtime", bind="level", label="x"):
    d = {"type": "number", "minimum": lo, "maximum": hi, "default": default, "x-tier": tier, "x-ui": {"label": label}}
    if bind:
        d["x-bindable"] = bind
    return d


PROPS = {"exposure": num(), "glow": num(), "contrast": num(0.5, 2.0), "warp_amount": num(0.0, 1.0, 0.3)}


def b(*sigs, at="live"):
    return {"at": at, "in": [{"sig": s, "w": 1.0} for s in sigs], "op": "mean", "curve": "linear",
            "map": {"scale": [0.8, 1.2]}}


def test_the_rules_never_drive_luminance_from_loudness():
    for fam in LUMINANCE_FAMS:
        for table in (RULES, TRANSITION_RULES):
            r = table.get(fam)
            if r is not None:
                assert not {t["sig"] for t in r["in_"]} & LOUDNESS, (fam, r["in_"])
    assert [t["sig"] for t in RULES["brightness_exposure"]["in_"]] == ["level_slow"]
    assert {t["sig"] for t in RULES["glow_light"]["in_"]} == {"level_slow", "punch"}


def test_compose_rule_strips_loudness_and_keeps_one_picture_route():
    out, gone = compose_rule("effect", PROPS, {"exposure": b("energy"), "glow": b("energy", "punch"),
                                               "contrast": b("arousal"), "warp_amount": b("energy", "bass")})
    assert "exposure" not in out                                        # loudness only: dropped
    assert [t["sig"] for t in out["glow"]["in"]] == ["punch"]           # loudness stripped
    assert "contrast" not in out                                        # a second picture route
    assert [t["sig"] for t in out["warp_amount"]["in"]] == ["energy", "bass"]   # not luminance: untouched
    assert len(gone) == 3


def test_compose_rule_prefers_exposure_then_glow():
    out, _ = compose_rule("effect", PROPS, {"glow": b("punch"), "exposure": b("level_slow"), "contrast": b("arousal")})
    assert set(out) == {"exposure"}
    out, _ = compose_rule("effect", PROPS, {"contrast": b("arousal"), "glow": b("level_slow")})
    assert set(out) == {"glow"}


class Fake:
    name, label, description, stage, internal = "fake_fx", "Fake", "", "colour", False

    @classmethod
    def param_props(cls):
        return {"amount": num(0.0, 1.0, 0.5), "pulse": num(0.0, 1.0, 0.0, bind="trigger"), **PROPS}


def test_drafts_obey_the_rule():
    rec = draft_item("effect", "fake_fx", Fake)
    pics = [p for p in rec["bindings"] if mapgen.picture_fam("effect", p, Fake.param_props()[p])]
    assert len(pics) <= 1
    for p in pics:
        assert not mapgen.reads_loudness(rec["bindings"][p])


def test_the_shipped_mappings_obey_the_rule():
    reg = mapgen.registry()
    data = json.loads((mapgen.CONFIG / mapgen.GEN_FILE).read_text())
    bad = []
    for key, rec in data["items"].items():
        kind, plugin = key.split("/", 1)
        cls = reg.table(mapgen.KINDS[kind][0]).get(plugin)
        if cls is None:
            continue
        props = cls.param_props()
        _, gone = compose_rule(kind, props, {p: x for p, x in (rec.get("bindings") or {}).items() if p in props})
        bad += [f"{key}.{g}" for g in gone]
    assert not bad, bad[:20]


def test_counts_and_sizes_are_never_picture_routes():
    props = {"spark_density": num(0.0, 1.0, 0.5), "ember_count": num(0.0, 1.0, 0.5), "glow": num()}
    for p in ("spark_density", "ember_count"):
        assert mapgen.picture_fam("transition", p, props[p]) is None
    out, gone = compose_rule("transition", props, {"spark_density": b("energy"), "glow": b("punch")})
    assert set(out) == {"spark_density", "glow"} and not gone


def test_distortion_intensities_are_not_picture_routes():
    props = {"wave_intensity": num(), "radial_blur_intensity": num(), "dither_amount": num(), "exposure": num()}
    assert [p for p in props if mapgen.picture_fam("effect", p, props[p])] == ["exposure"]
