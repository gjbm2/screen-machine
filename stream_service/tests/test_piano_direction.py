"""The piano in the art director's choice (piano_direction.py, music_direction.py): the blend with the audio (piano
only, audio only, both), the leans' words, the density axis, the config's checks and the FX roles (no GPU)."""
from __future__ import annotations

import pathlib

import pytest

from stream_service import music_direction as M
from stream_service import piano_direction as PD
from stream_service.director import AUTO_MUSIC_DEFAULTS, validate_direction
from stream_service.music_feed import FeedSnap

REPO = pathlib.Path(__file__).resolve().parents[2]
VOCAB = REPO / "config" / "live" / "vocab.yaml"
CFG = """
on: true
energy: {note_rate: 0.6, velocity: 0.4}
pace: {note_rate: 0.8, velocity: 0.2}
weight: 1.0
both_boost: 0.2
smooth_s: 0.5
lean_on: 0.5
lean_off: 0.3
keep_moods: 1
leans:
  legato: {when: {pedal: [0.35, 0.75]}, moods: [dreamy, liquid, calm], fx_moods: [dreamy, liquid]}
  staccato: {when: {note_rate: [0.35, 0.65]}, unless: {pedal: [0.2, 0.5]}, moods: [playful, bright, geometric],
             fx_moods: [glitchy, playful, geometric]}
density: {spread: [0.15, 0.75], to: [0.2, 0.9], weight: 0.5}
"""


@pytest.fixture
def pcfg(tmp_path):
    p = tmp_path / "piano_direction.yaml"
    p.write_text(CFG)
    return PD.PianoConfig(p, VOCAB)


def md_with(pcfg):
    md = M.MusicDirection(dict(AUTO_MUSIC_DEFAULTS))
    md.piano = PD.PianoFeel(pcfg)
    return md


def piano(note_rate=0.5, velocity=0.6, pedal=0.0, spread=0.3, register=0.5, tension=0.2):
    return {"note_rate": note_rate, "velocity": velocity, "pedal": pedal, "spread": spread, "register": register,
            "tension": tension}


SILENT = {"note_rate": None, "velocity": None, "pedal": 0.0, "spread": None, "register": None, "tension": None}


def feed(t, energy):
    return FeedSnap(t=t, fresh_since=0.0, energy=energy, activity=energy, activity_slow=energy, drive=0.0, punch=0.0)


def run(md, seconds, audio=None, keys=None, t0=0.0):
    """Step at 10 Hz; audio: energy or None (no audio reading); keys: piano levels or None (no piano feed)."""
    t = t0
    for _ in range(int(seconds * 10)):
        t += 0.1
        md.update(t, None, False, (), True, feed=None if audio is None else feed(t, audio), piano=keys)
    return t


# -- the blend rule --------------------------------------------------------------------------------------------------
def test_blend_piano_only_audio_only_both():
    assert PD.blend(None, 0.6, 0.2) == 0.6                     # piano only: the piano
    assert PD.blend(0.4, None, 0.2) == 0.4                     # audio only: the audio, untouched
    assert PD.blend(None, None) is None
    assert PD.blend(0.0, 0.7, 0.2) == pytest.approx(0.7)       # MIDI the mic can't hear: the piano drives
    assert PD.blend(0.8, 0.2, 0.0) == pytest.approx(0.8)       # singing over quiet playing: the stronger leads
    both = PD.blend(0.5, 0.5, 0.2)
    assert both == pytest.approx(0.5 + 0.2 * 0.5 * 0.5) and both > 0.5
    assert PD.blend(1.0, 1.0, 0.2) == 1.0
    assert PD.blend(0.3, 0.9, 0.2, weight=0.0) == pytest.approx(0.3)   # weight 0: the audio alone
    assert PD.blend(0.3, 0.9, 0.0, weight=0.5) == pytest.approx(0.6)


def test_no_piano_feed_is_exactly_as_before(pcfg):
    a, b = M.MusicDirection(dict(AUTO_MUSIC_DEFAULTS)), md_with(pcfg)
    for i in range(80):
        t = (i + 1) * 0.1
        ca = a.update(t, None, False, (), True, feed=feed(t, 0.45))
        cb = b.update(t, None, False, (), True, feed=feed(t, 0.45), piano=SILENT if i % 2 else None)
        assert ca == cb
    assert a.proposal == b.proposal and "piano" not in b.proposal and a.source == b.source == "show"
    assert b.status(8.0)["piano"] is None


def test_piano_only_drives_the_proposal(pcfg):
    md = md_with(pcfg)
    run(md, 6, audio=None, keys=piano(note_rate=0.5, velocity=0.6))
    assert md.source == "piano" and md.m == 1.0 and md.proposal is not None
    e = 0.6 * 0.5 + 0.4 * 0.6
    assert md.proposal["energy"] == pytest.approx(e, abs=0.01)
    assert md.proposal["pace"] == pytest.approx(0.1 + 0.8 * (0.8 * 0.5 + 0.2 * 0.6), abs=0.01)
    assert md.proposal["fx"]["density"] == pytest.approx(0.1 + 0.8 * e, abs=0.01)
    assert md.status(6.0)["piano"]["energy"] == pytest.approx(e, abs=0.01)


def test_audio_only_and_both(pcfg):
    md = md_with(pcfg)
    t = run(md, 8, audio=0.3, keys=None)
    assert md.proposal["energy"] == pytest.approx(0.3, abs=0.02)        # audio only
    t = run(md, 8, audio=0.3, keys=piano(note_rate=0.9, velocity=0.9), t0=t)
    hi = 0.6 * 0.9 + 0.4 * 0.9
    assert md.proposal["energy"] == pytest.approx(hi + 0.2 * 0.3 * (1 - hi), abs=0.02)   # the louder piano leads
    assert md.source == "show"
    t = run(md, 12, audio=0.85, keys=piano(note_rate=0.1, velocity=0.2), t0=t)
    lo = 0.6 * 0.1 + 0.4 * 0.2
    a = md.features["energy"]                                                             # (eased towards 0.85)
    assert a > 0.8 and md.proposal["energy"] == pytest.approx(a + 0.2 * lo * (1 - a), abs=0.02)   # singing leads
    run(md, 2, audio=0.85, keys=SILENT, t0=t)                                             # the piano stops
    assert "piano" not in md.proposal and md.proposal["energy"] == pytest.approx(md.features["energy"], abs=0.01)


def test_room_director_blends_too(pcfg):
    md = md_with(pcfg)
    d = validate_direction({"energy": 0.2, "pace": 0.3, "moods": ["calm"], "density": 0.4})
    room = {"direction": d, "groove": 0.1, "t": 0.0, "section": {"id": 1}}
    t = 0.0
    for _ in range(40):
        t += 0.1
        md.update(t, None, False, (), True, room=room, piano=piano(note_rate=0.8, velocity=0.8, spread=0.75))
    assert md.source == "room"
    assert md.proposal["energy"] == pytest.approx(0.8 + 0.2 * 0.2 * 0.2, abs=0.01)
    assert md.proposal["density"] == pytest.approx(0.5 * 0.4 + 0.5 * 0.9, abs=0.02)        # full chords: denser
    eff = M.effective_direction(validate_direction({}), md.proposal, md.m)
    assert eff["piano"]["staccato"] > 0.5 and eff["density"] == md.proposal["density"]


# -- the leans -------------------------------------------------------------------------------------------------------
def test_pedal_leans_dreamy_and_fast_dry_notes_lean_busy(pcfg):
    md = md_with(pcfg)
    t = run(md, 8, audio=0.4, keys=piano(note_rate=0.3, pedal=1.0))
    assert md.piano_reading.lean == "legato"
    assert md.proposal["moods"][:3] == ["dreamy", "liquid", "calm"] and len(md.proposal["moods"]) <= 4
    assert md.proposal["fx"]["moods"][:2] == ["dreamy", "liquid"]
    assert md.proposal["piano"]["legato"] == 1.0
    t = run(md, 10, audio=0.4, keys=piano(note_rate=0.8, pedal=0.0), t0=t)
    assert md.piano_reading.lean == "staccato"
    assert md.proposal["moods"][:3] == ["playful", "bright", "geometric"]
    assert md.proposal["fx"]["moods"][0] == "glitchy"
    run(md, 10, audio=0.4, keys=piano(note_rate=0.2, pedal=0.1), t0=t)
    assert md.piano_reading.lean is None                                                   # neither: the band's words
    assert md.proposal["moods"] == list(M.BANDS[md.band][0])


def test_lean_words_come_from_the_vocab(tmp_path):
    bad = tmp_path / "p.yaml"
    bad.write_text("leans:\n  legato: {when: {pedal: [0.3, 0.7]}, moods: [dreamy, smeary]}\n")
    cfg, errs = PD.parse(__import__("yaml").safe_load(bad.read_text()), PD._vocab_moods(VOCAB))
    assert cfg["leans"]["legato"]["moods"] == ["dreamy"] and any("smeary" in e for e in errs)


def test_the_shipped_config_reads_cleanly():
    pc = PD.PianoConfig(PD.CONFIG_PATH, VOCAB)
    cfg = pc.get()
    assert pc.errors == [] and cfg["on"] and set(cfg["leans"]) >= {"legato", "staccato"}


def test_fx_roles_follow_the_piano(tmp_path):
    from stream_service.fx_interplay import Interplay
    live = REPO / "config" / "live"
    for n in ("fx_interplay.yaml", "effect_roles.generated.json", "look_stats.generated.json"):
        (tmp_path / n).write_text((live / n).read_text())
    ip = Interplay(tmp_path)
    base = ip.context((None, None), [], {"energy": 0.5}).per_role
    leg = ip.context((None, None), [], {"energy": 0.5, "piano": {"legato": 1.0, "staccato": 0.0}}).per_role
    sta = ip.context((None, None), [], {"energy": 0.5, "piano": {"legato": 0.0, "staccato": 1.0}}).per_role
    assert leg["trails"][2] > base["trails"][2] and leg["camera"][2] < base["camera"][2]
    assert sta["camera"][2] > base["camera"][2] and sta["trails"][2] < base["trails"][2]


# -- the mappings' piano pass (tools/mapgen.py piano) ---------------------------------------------------------------
def test_mapgen_adds_one_piano_input_by_family():
    from stream_service.tools import mapgen as G
    b = {"at": "live", "in": [{"sig": "energy", "w": .7}, {"sig": "activity", "w": .3}], "op": "mean"}
    assert G.add_piano("macro.energy", "energy", b)["in"][-1] == {"sig": "note_rate", "w": .3}
    assert G.add_piano("feedback_trail", "decay", b)["in"][-1]["sig"] == "pedal"
    assert G.add_piano("glow_light", "glow", b)["in"][-1]["sig"] == "velocity"
    assert G.add_piano("timing", "fade_time", b)["in"][-1] == {"sig": "note_rate", "w": .3, "inv": True}
    assert G.add_piano("position_xy", "center_y", b)["in"][-1]["sig"] == "register"
    assert G.add_piano("position_xy", "center_x", b) is None                 # only the vertical axis
    assert G.add_piano("palette", "palette", b) is None                      # a family with no piano input
    full = {**b, "in": [{"sig": "energy", "w": .3}, {"sig": "drive", "w": .25}, {"sig": "tempo", "w": .25},
                        {"sig": "groove", "w": .2}]}
    got = G.add_piano("motion_rate", "speed2", full)["in"]
    assert [t["sig"] for t in got] == ["energy", "drive", "tempo", "note_rate"]   # the lightest gave way
    assert G.add_piano("motion_rate", "x", {**b, "op": "mul"}) is None
    assert G.add_piano("motion_rate", "x", {**b, "in": b["in"] + [{"sig": "note_rate", "w": .3}]}) is None


def test_generated_piano_bindings_fall_back_to_base_without_a_piano():
    import json
    from stream_service import binding
    data = json.loads((REPO / "config" / "live" / "mappings.generated.json").read_text())
    prop = {"type": "number", "minimum": 0.0, "maximum": 1.0}
    n = 0
    for rec in data["items"].values():
        for p, b in (rec.get("bindings") or {}).items():
            if not b or "colour" in b or not any(t["sig"] in ("note_rate", "velocity", "pedal", "register")
                                                for t in b.get("in") or ()):
                continue
            bd = binding.Bound(p, prop, b)
            idle = {t["sig"]: binding.idle_value(t["sig"]) for t in b["in"]}
            quiet = {**idle, **{t["sig"]: None for t in b["in"] if t["sig"] in ("note_rate", "velocity", "pedal",
                                                                                 "register")}}
            assert bd.x(quiet, None) == pytest.approx(bd.x0)       # no piano, music at its norm: the authored look
            n += 1
    assert n > 1000
