"""moves.py: a change of scene composed across the layers: parsing, and the draw by boundary, size and what can play."""
import random

from show_service import moves as mv

RAW = {"moves": {
    "lift": {"label": "Lift and land", "from": ["chorus"], "into": ["verse"], "size": "medium",
             "lights": {"transition": "fly-up-snap", "accents": [{"effect": "strobe-hit", "at_beats": -1}]},
             "screen": {"via": ["family:warp", "melt-2s"]}, "land": 1.0, "tags": {"weight": 1.0}},
    "finale": {"into": ["final_chorus"], "size": "big", "lights": {"transition": "strobe-snap"},
               "screen": {"via": "cut"}, "tags": {"weight": 3.0}},
    "plain": {"reasons": ["section", "repeat", "dwell"], "tags": {"weight": 0.3}},
    "bad": {"size": "huge", "into": ["bridge"], "lights": {"accents": [{"at_beats": 1}]}, "screen": {"via": []},
            "land": 2, "nope": 1},
}}


def _moves():
    probs: list[str] = []
    got = mv.parse(RAW, probs)
    return got, probs


def test_parse_keeps_the_good_and_lists_the_bad():
    got, probs = _moves()
    assert got["lift"]["lights"] == {"transition": "fly-up-snap", "accents": [{"effect": "strobe-hit", "at_beats": -1.0}]}
    assert got["lift"]["screen"] == {"via": ["family:warp", "melt-2s"]} and got["lift"]["land"] == 1.0
    assert got["finale"]["screen"] == {"via": ["cut"]} and got["finale"]["reasons"] == ["section", "repeat"]
    b = got["bad"]                                          # every bad value falls back, with a line each
    assert b["size"] == "small" and b["into"] == ["any"] and b["screen"] is None and b["land"] is None
    assert b["lights"]["accents"] == [] and len(probs) == 6


def _pick(reason, into, frm, n=200, **kw):
    m = mv.Moves.__new__(mv.Moves)
    m.moves = _moves()[0]
    del m.moves["bad"]
    rng = random.Random(7)
    kw.setdefault("weight", lambda t: float(t.get("weight", 1.0)))
    return [(m.pick(reason, into, frm, rng, **kw) or (None,))[0] for _ in range(n)]


def test_the_boundary_picks_its_own_move():
    got = _pick("section", "verse", "chorus")
    assert got.count("lift") > got.count("plain") * 5 and "finale" not in got     # x SPECIFIC for into and from
    assert set(_pick("section", "chorus", "verse")) == {"plain"}                   # no move is made for it: any
    assert set(_pick("dwell", None, None)) == {"plain"} and set(_pick("track", None, None)) == {None}


def test_a_big_move_needs_a_big_moment_and_parts_that_can_play():
    assert _pick("section", "final_chorus", "verse").count("finale") > 150
    assert set(_pick("section", "final_chorus", "verse", big_ok=False)) == {"plain"}
    no_strobe = _pick("section", "verse", "chorus", usable=lambda m: not (m.get("lights") or {}).get("accents"))
    assert set(no_strobe) == {"plain"}                                              # its strobe may not fire: left out
