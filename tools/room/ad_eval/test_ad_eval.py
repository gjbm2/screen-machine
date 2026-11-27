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
