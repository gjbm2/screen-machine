"""The fault checks (checks.py) on synthetic timelines: each check passes a right show and fails a wrong one, and
compare() finds a new fault and a fixed one. They are the overnight loop's keep rule, so these are real tests.

    ~/.venvs/sm-show/bin/python -m pytest tools/room/ad_eval/test_checks.py -q
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import checks  # noqa: E402

DUR, BEAT, HOP = 120.0, 0.5, 0.05


def ref() -> dict:
    return {"beats": list(np.arange(1.0, DUR, BEAT)),
            "sections": [{"start_s": 0.0, "end_s": 30.0, "kind": "verse", "arrive": "none"},
                         {"start_s": 30.0, "end_s": 60.0, "kind": "chorus", "arrive": "bump"},
                         {"start_s": 60.0, "end_s": 80.0, "kind": "verse", "arrive": "none"},
                         {"start_s": 80.0, "end_s": 110.0, "kind": "chorus", "arrive": "lift"},
                         {"start_s": 110.0, "end_s": DUR, "kind": "outro", "arrive": "none"}],
            "climax": [80.0, 110.0], "last_hit_s": 115.0, "end_s": DUR,
            "moments": [{"t": 50.0, "end": 51.5, "kind": "stop", "salience": 0.9},
                        {"t": 115.0, "end": 115.0, "kind": "end", "style": "cold", "salience": 1.0}]}


def lamp(fn) -> list[float]:
    return [float(fn(i * HOP)) for i in range(int((DUR + 6) / HOP))]


def good_level(t: float) -> float:
    if 50.0 <= t < 51.5 or t >= 115.2:
        return 0.0                                                      # the stop, and dark after the last hit
    return 0.9 if 80.0 <= t < 110.0 else 0.5


def tl(changes=(30.0, 60.0, 80.0, 110.0), level=good_level, lineups=((20.0, 2), (75.0, 3))) -> dict:
    dec = [{"t_s": c, "kind": "look_change"} for c in changes]
    dec += [{"t_s": t, "kind": "lineup", "detail": {"plays": ["g"] * k, "lineup": f"l{k}"}} for t, k in lineups]
    return {"decisions": dec, "outputs": {"hop_s": HOP, "t0_s": 0.0, "lamp_level": lamp(level)}}


def by(res: list[dict], check: str) -> list[dict]:
    return [r for r in res if r["check"] == check]


def test_a_right_show_passes_every_check():
    res = checks.run(tl(), ref())
    assert [r for r in res if not r["ok"]] == []
    assert {r["check"] for r in res} == set(checks.CHECKS)


def test_lands_and_stray():
    res = checks.run(tl(changes=(27.9, 60.0, 80.0, 110.0, 70.0)), ref())
    lands = by(res, "lands")
    assert [r["ok"] for r in lands] == [False, True]
    assert "early" in lands[0]["why"]
    assert [r["t_s"] for r in by(res, "stray") if not r["ok"]] == [27.9, 70.0]
    res = checks.run(tl(changes=(30.0, 60.0, 110.0)), ref())
    assert "no look change" in [r for r in by(res, "lands") if not r["ok"]][0]["why"]


def test_peak_wants_the_climax_biggest():
    res = checks.run(tl(level=lambda t: 0.3 if 80 <= t < 110 else good_level(t)), ref())
    assert not by(res, "peak")[0]["ok"]
    res = checks.run(tl(lineups=((20.0, 3), (75.0, 1))), ref())
    assert "fewer than earlier" in by(res, "peak")[0]["why"]


def test_dark_stop_and_ending():
    res = checks.run(tl(level=lambda t: 0.0 if 40 <= t < 44 else good_level(t)), ref())
    assert [r["t_s"] for r in by(res, "dark") if not r["ok"]][0] == pytest_approx(40.0, 0.5)
    res = checks.run(tl(level=lambda t: 0.5 if 50 <= t < 51.5 else good_level(t)), ref())
    assert not by(res, "stop")[0]["ok"]
    res = checks.run(tl(level=lambda t: 0.5 if t >= 110 else good_level(t)), ref())
    assert not by(res, "ending")[0]["ok"]
    res = checks.run(tl(level=lambda t: 0.0 if t >= 108 else good_level(t)), ref())
    assert "before" in by(res, "ending")[0]["why"]


def test_compare_finds_new_and_fixed_faults():
    a = {"checks": {"faults": {"s.karaoke.s0": [["lands", 30], ["stray", 70]]}}}
    b = {"checks": {"faults": {"s.karaoke.s0": [["stray", 71], ["peak", 80]], "other.karaoke.s0": [["dark", 5]]}}}
    new, fixed = checks.compare(a, b)
    assert new == [("s.karaoke.s0", "peak", 80)]                   # the replay only the second run has is not compared
    assert fixed == [("s.karaoke.s0", "lands", 30)]                # stray moved a second: the same fault


def test_transitions_list_the_moments_to_review():
    tr = checks.transitions(ref())
    assert [x["what"] for x in tr] == ["section", "stop", "section", "section", "climax", "section", "ending"]
    assert tr[0]["label"] == "verse → chorus (bump)"


def pytest_approx(v: float, tol: float):
    class _A:
        def __eq__(self, x):
            return abs(x - v) <= tol
    return _A()
