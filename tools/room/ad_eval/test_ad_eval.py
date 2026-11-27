"""Does-it-crash tests for the ad_eval scorers, on synthetic timelines (no audio, no show_service).

    ~/.venvs/sm-show/bin/python -m pytest tools/room/ad_eval/test_ad_eval.py -q
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import score  # noqa: E402

DUR = 120.0
BEAT = 0.5


def ref() -> dict:
    t = np.arange(0, DUR, 0.1)
    e = 0.3 + 0.5 * (t > 60) + 0.1 * np.sin(t)
    return {"beats": list(np.arange(1.0, DUR, BEAT)), "downbeats": list(np.arange(1.0, DUR, 4 * BEAT)),
            "sections": [{"start_s": 0.0, "end_s": 30.0, "label": "A", "kind": "intro"},
                         {"start_s": 30.0, "end_s": 60.0, "label": "B", "kind": "verse"},
                         {"start_s": 60.0, "end_s": DUR, "label": "C", "kind": "chorus"}],
            "energy": {"hop_s": 0.1, "v": list(np.clip(e, 0, 1))}, "climax": [70.0, 90.0], "last_hit_s": 115.0,
            "end_s": DUR}


def timeline(lag: float = 0.0, beats_off: float = 0.0, changes=(30.0, 60.0)) -> dict:
    r = ref()
    hop = 0.05
    t = np.arange(0, DUR + 5, hop)
    e = np.interp(t - lag, np.arange(len(r["energy"]["v"])) * 0.1, r["energy"]["v"])
    e[t > DUR] = 0.0
    dec = [{"t_s": c - 1.0, "land_s": c, "kind": "look_change", "why": "x", "detail": {}} for c in changes]
    dec += [{"t_s": c - 1.0, "kind": "light_scene", "why": "x", "detail": {}} for c in changes]
    dec += [{"t_s": 74.0, "land_s": 75.0, "kind": "move", "why": "x", "detail": {"size": "big"}}]
    return {"song": {"id": "s", "duration_s": DUR}, "mode": "karaoke", "decisions": dec,
            "outputs": {"hop_s": hop, "t0_s": 0.0, "lamp_level": list(e), "screen_energy": list(e)},
            "show": {"beats": [b + beats_off for b in r["beats"]], "sections": [], "lock_s": 1.0}}


def test_a_perfect_show_scores_high():
    m = score.score(timeline(), ref())
    assert m["beat"] == 1.0 and m["section_hit"] == 1.0 and m["climax"] == 1.0
    assert m["lag_lamp_s"] == 0.0 and m["contour"] > 0.9 and m["overall"] > 0.8


def test_lag_and_off_beat_and_missed_sections_score_lower():
    good = score.score(timeline(), ref())
    bad = score.score(timeline(lag=1.5, beats_off=0.2, changes=(40.0,)), ref())
    assert bad["lag_lamp_s"] >= 1.0
    assert bad["beat"] < 0.2
    assert bad["section_hit"] == 0.0 and bad["strays_per_min"] > 0
    assert bad["overall"] < good["overall"]


def test_empty_timeline_does_not_crash():
    tl = {"song": {"id": "s"}, "decisions": [], "outputs": {}, "show": {}}
    m = score.score(tl, ref())
    assert m["beat"] == 0.0


def test_cases_measure_their_window():
    for dim in ("cadence", "contour", "shape", "restraint", "coherence", "ending", "tone", "other"):
        v = score.case_value(timeline(), ref(), {"song": "s", "t_s": 60.0, "dimension": dim, "verdict": "bad"})
        assert v is None or isinstance(v, float)


def test_design_measures_restraint_and_coherence():
    """design.py: one palette, a small kit, rare flourishes, still heads and a reserved full rig score 1; a show that
    changes everything on every section scores low on each."""
    import design
    scenes = {"pools": {"layers": [{"kind": "position", "mode": "drift"}]},
              "sweep": {"layers": [{"kind": "position", "mode": "sweep"}]},
              "circle": {"layers": [{"kind": "position", "mode": "circle"}]},
              "hop": {"layers": [{"kind": "position", "mode": "bounce"}]},
              "eight": {"layers": [{"kind": "position", "mode": "figure8"}]},
              "pulse": {"layers": []}, "chase": {"layers": []}, "wave": {"layers": []}, "spark": {"layers": []},
              "rest": {"layers": [], "tags": {"rest": True}}}
    trans = {"glide": {"kind": "crossfade"}, "fly-up": {"kind": "crossfade", "gesture": {"layers": []}}}

    def show(looks):
        dec = []
        for t, pal, mv, lamp, tr, plays, into in looks:
            dec.append({"t_s": t, "kind": "look_change", "detail": {"palette": pal}})
            dec.append({"t_s": t, "kind": "light_program", "detail": {"groups": {
                "movers": {"scene": mv, "transition": tr}, "lamps": {"scene": lamp, "transition": tr}}}})
            dec.append({"t_s": t, "kind": "lineup", "detail": {"plays": plays, "into": into}})
        return {"song": {"duration_s": 100.0}, "decisions": dec}
    good = design.design(show([(0, "dusk", "pools", "pulse", "glide", ["lamps"], "verse"),
                               (25, "dusk", "sweep", "pulse", "fly-up", ["lamps", "movers"], "chorus"),
                               (50, "dusk", "pools", "rest", "glide", ["lamps"], "verse"),
                               (75, "dusk", "sweep", "chase", "glide", ["lamps", "movers", "derbies"], "climax")]),
                         scenes, trans)
    assert good["palette"] == 1.0 and good["kit"] == 1.0 and good["flourish"] == 1.0 and good["reserve"] == 1.0
    assert good["still_share"] == 0.5 and good["stillness"] == pytest.approx(0.5 / 0.6, abs=0.01)
    assert good["scenes_per_group"] == {"movers": 2, "lamps": 2}          # the rest isn't one of the song's looks
    busy = design.design(show([(0, "dusk", "sweep", "pulse", "fly-up", ["lamps", "movers", "derbies"], "verse"),
                               (20, "gold", "circle", "chase", "fly-up", ["lamps"], "chorus"),
                               (40, "ocean", "hop", "wave", "fly-up", ["lamps", "movers", "derbies"], "verse"),
                               (60, "ice", "eight", "spark", "fly-up", ["lamps", "movers"], "climax"),
                               (80, "dusk", "sweep", "pulse", "fly-up", ["lamps"], "outro")]), scenes, trans)
    assert busy["palette"] == 0.25 and busy["kit"] == 0.75 and busy["flourish"] == 0.6
    assert busy["stillness"] == 0.0 and busy["reserve"] == 0.0           # the biggest line-ups came in the verses
    assert busy["design"] < 0.4 < 0.9 < good["design"]
    assert design.design({"decisions": []})["design"] is None


# -- what was shown: form.py and controls.py --------------------------------------------------------------------------
def _song(seed: int = 0, bright: float = 0.7, speed: float = 30.0, n_sec: int = 8, sec_s: float = 20.0):
    """A song of alternating quiet (A) and loud (B) sections, and a show whose lights hold one look for each: (the
    timeline with its reference inside, the reference)."""
    import form
    rng = np.random.default_rng(seed)
    dur = n_sec * sec_s
    kinds = ["A" if i % 2 == 0 else "B" for i in range(n_sec)]
    t = np.arange(0, dur, 0.1)
    e = np.array([0.3 if kinds[min(n_sec - 1, int(x // sec_s))] == "A" else 0.8 for x in t]) + rng.normal(0, 0.01, len(t))
    secs = [{"start_s": i * sec_s, "end_s": (i + 1) * sec_s, "label": k, "kind": "verse" if k == "A" else "chorus",
             "energy": 0.3 if k == "A" else 0.8} for i, k in enumerate(kinds)]
    sim = [[1.0 if a == b else 0.1 for b in kinds] for a in kinds]
    r = {"beats": list(np.arange(0.0, dur, BEAT)), "downbeats": list(np.arange(0.0, dur, 4 * BEAT)), "sections": secs,
         "sim": {"audio": sim, "lyrics": None, "agree": None}, "energy": {"hop_s": 0.1, "v": list(np.clip(e, 0, 1))},
         "climax": [dur - 2 * sec_s + 1.0, dur - sec_s - 1.0] if kinds[-2] == "B" else [dur - sec_s, dur],
         "last_hit_s": dur - 1.0, "end_s": dur,
         "character": {"bpm": 120.0, "punch": 0.1, "range_db": 10.0, "bright": 10.0, "low": 0.2}}
    hop = 0.05
    tt = np.arange(0, dur + 5, hop)
    looks = {"A": dict(level=0.3, lit=0.5, heads=0.0, move=0.0, flicker=0.05, r=0.1, g=0.2, b=1.0),
             "B": dict(level=bright, lit=1.0, heads=1.0, move=speed, flicker=0.6, r=1.0, g=0.3, b=0.1, tilt=40.0,
                       spread=30.0)}
    lamp, rows = [], []
    for x in tt:
        lk = looks[kinds[min(n_sec - 1, int(x // sec_s))]] if x < dur else {}
        lamp.append(lk.get("level", 0.0))
        rows.append([lk.get(k, 0.0) for k in form.KEYS])
    dec = []
    for i, k in enumerate(kinds):
        dec.append({"t_s": i * sec_s, "land_s": i * sec_s, "kind": "look_change", "detail": {"palette": "dusk"}})
        dec.append({"t_s": i * sec_s, "kind": "light_program", "detail": {"groups": {
            "movers": {"scene": "pools" if k == "A" else "sweep", "transition": "glide"}}}})
    tl = {"song": {"id": f"s{seed}", "duration_s": dur}, "mode": "karaoke", "decisions": dec, "reference": r,
          "outputs": {"hop_s": hop, "t0_s": 0.0, "lamp_level": lamp, "screen_energy": lamp,
                      "shown": {"keys": list(form.KEYS), "v": rows}},
          "show": {"beats": list(r["beats"]), "sections": [], "lock_s": 0.0}}
    return tl, r


def test_the_describer_reads_a_frame():
    import form
    d = form.Describer()
    fx = {"par": {"intensity": 1.0, "colour": [1.0, 0.0, 0.0]},
          "spot": {"intensity": 0.5, "colour": [0.0, 0.0, 1.0], "attrs": {"pan": 0.0, "tilt": 30.0, "gobo": 3}},
          "off": {"intensity": 0.0, "colour": [1, 1, 1], "attrs": {"pan": 0.0, "tilt": 0.0}}}
    d.row(fx, 0.0)
    fx["spot"] = {**fx["spot"], "attrs": {"pan": 3.0, "tilt": 34.0, "gobo": 3}}
    row = dict(zip(form.KEYS, d.row(fx, 0.1)))
    assert row["lit"] == pytest.approx(2 / 3, abs=0.01) and row["heads"] == 0.5 and row["gobo"] == 1.0
    assert row["move"] == pytest.approx(50.0, abs=0.1)            # 5 degrees in a tenth of a second
    assert row["r"] == pytest.approx(2 / 3, abs=0.01) and row["variety"] > 0.5 and row["tilt"] == 34.0


def test_form_reads_what_was_shown_not_when():
    """A show whose looks follow the song's sections has its form; the same looks dealt to the wrong sections do
    not, though every change is still on its line (the first scorer could not tell them apart)."""
    import random
    import controls
    import form
    tl, r = _song()
    good = form.measure(tl, r)
    assert good["form"] > 0.9 and good["contrast"] == 1.0 and good["proportion"] is not None
    assert good["arc"] > 0.8 and good["release"] > 0.9 and good["means_used"] >= 3
    still = form.measure(controls.static(tl), r)
    assert still["form"] == 0.0 and still["contrast"] == 0.0 and still["arc"] == 0.0 and still["release"] == 0.0
    box = form.measure(controls.box(tl, r), r)
    assert box["means_used"] <= 1 and box["contrast"] < good["contrast"]   # one knob: little to see
    wrong = score.score(controls.shuffled(tl, r, random.Random(3)), r)
    right = score.score(tl, r)
    assert right["section_hit"] == wrong["section_hit"] == 1.0
    assert wrong["overall"] < right["overall"] - 0.1


def test_restraint_is_a_limit_not_a_goal():
    """overall = goals x gate: keeping every limit earns nothing (one static look keeps them all), breaking one costs."""
    import controls
    tl, r = _song()
    m = score.score(tl, r)
    assert m["gate"] == 1.0 and m["overall"] == m["goals"] and m["score_version"] == score.SCORE_VERSION
    still = score.score(controls.static(tl), r)
    assert still["gate"] == 1.0 and still["overall"] < m["overall"] - 0.2
    loud = {**tl, "decisions": [{**d, "detail": {**d["detail"], "palette": f"p{i}"}} if d["kind"] == "look_change"
                                else d for i, d in enumerate(tl["decisions"])]}
    over = score.score(loud, r)                                   # a new palette on every section
    assert over["gate"] < 1.0 and over["overall"] < m["overall"] and over["goals"] == m["goals"]


def test_the_controls_tell_the_real_show_from_wrong_ones():
    import controls
    tls = [_song(seed=i, n_sec=6 + 2 * (i % 3), sec_s=16.0 + 4.0 * (i % 2))[0] for i in range(6)]
    res = controls.check(tls)
    assert set(res["margins"]) == {*controls.WRONG, controls.BOX} and res["songs"] == 6
    for name in ("shuffled", "static", "backwards"):
        assert res["margins"][name]["ok"], (name, res["margins"][name])
    assert res["table"]["the real show"]["overall"] > res["table"]["static"]["overall"]
    assert controls.check(tls[:2])["pass"] is False               # too few songs to tell anything apart


def test_different_songs_get_different_shows():
    import form
    rows = []
    for i in range(8):                                            # the harder the song hits, the bigger its show
        tl, r = _song(seed=i, bright=0.3 + 0.08 * i, speed=5.0 * i)
        r["character"] = {"bpm": 80.0 + 10 * i, "punch": 0.08 + 0.01 * i, "range_db": 10.0, "bright": 10.0, "low": 0.2}
        rows.append((r["character"], form.song_look(tl, r)))
    assert form.character(rows)["character"] > 0.8
    same = [(c, rows[0][1]) for c, _lk in rows]                   # every song the same show
    assert form.character(same)["character"] == 0.0
    assert form.character(rows[:3])["character"] is None
