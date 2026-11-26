"""picture.py: a scene's direction from its words, the facts files, and what the movers' pick favours."""
import json

from show_service import flow, picture as pic


def test_a_direction_is_read_from_the_pictures_words():
    assert pic.read_dir("Classic synthwave flight down a violet neon canyon toward a big sun")[0] == "inward"
    assert pic.read_dir("Roaring orange flames lick up into black")[0] == "up"
    assert pic.read_dir("Craggy rock planet with glowing lava seams, rotating slowly")[0] == "cw"
    assert pic.read_dir("gold staves filling in left to right")[0] == "right"
    assert pic.read_dir("a turning wheel, anticlockwise")[0] == "ccw"
    assert pic.read_dir("A single green curve on a dark grey checkerboard") == (None, [])
    assert pic.read_dir(None, "pixel-tunnel")[0] == "inward"            # a preset's name alone


def test_build_and_the_hand_file_wins(tmp_path):
    body = pic.build({"presets": {"scene": {"a": {"_look": "Endless zoom into boxes"}, "b": {"_look": "plain"}}}},
                     {"scenes": {"a": {"motion_pct": 0.8, "busy_pct": 0.9, "brightness_pct": 0.2}}})
    assert body["scenes"]["a"] == {"dir": "inward", "pace": 0.8, "busy": 0.9, "bright": 0.2,
                                    "said": ["endless zoom", "zoom"]}
    assert body["scenes"]["b"]["dir"] is None
    p, h = tmp_path / "f.json", tmp_path / "h.yaml"
    p.write_text(json.dumps(body))
    h.write_text("scenes:\n  b: {dir: cw}\n  a: {dir: off}\n")
    f = pic.Facts(p, h)
    assert f.poll() and not f.poll() and f.error is None
    assert f.of("b")["dir"] == "cw" and f.of("a")["dir"] is None and f.of("a")["pace"] == 0.8
    assert f.of("neon-tunnel-run") == {"name": "neon-tunnel-run", "dir": "inward", "pace": None, "busy": None,
                                       "bright": None, "sure": 0.6}
    assert f.of(None) is None


SCENES = {
    "converge": {"layers": [{"kind": "position", "mode": "aim"}],
                 "tags": {"energy": [0.5, 1.0], "flow": {"dirs": ["inward", "towards"]}}},
    "fan-out": {"layers": [{"kind": "position", "mode": "fan"}], "tags": {"energy": [0.4, 0.9], "flow": {"dirs": ["outward"]}}},
    "ballyhoo": {"layers": [{"kind": "position", "mode": "ballyhoo"}],
                 "tags": {"energy": [0.75, 1.0], "flow": {"dirs": ["cw", "ccw"]}}},
    "wave": {"layers": [{"kind": "position", "mode": "tilt_wave"}], "tags": {"energy": [0.3, 0.8], "flow": {"honours": True}}},
    "pulse": {"layers": [{"kind": "intensity"}], "tags": {"energy": [0.3, 0.8]}},
}


def test_the_movers_pick_favours_what_fits_the_picture():
    f = pic.prefer(SCENES, {"name": "t", "dir": "inward", "pace": 0.75, "busy": 0.9})
    assert f["converge"] > 1.5 and f["fan-out"] < 0.7          # with the picture / against it
    assert 1.0 < f["wave"] < f["converge"]                      # honours the flow: fits, but isn't the shape itself
    assert f["ballyhoo"] < 0.7                                  # busy movement over a busy picture
    assert "pulse" not in f                                     # it doesn't move: left alone
    sparse = pic.prefer(SCENES, {"name": "t", "dir": None, "pace": 0.9, "busy": 0.1})
    assert sparse["ballyhoo"] > 1.2                             # a sparse picture: the lights carry it
    assert pic.prefer(SCENES, None) == {}


def test_the_picture_votes_in_the_flow():
    probs: list[str] = []
    dec = flow.FlowDecider(flow.parse_settings({"sources": {"screen": 0.9}}, probs))
    assert probs == []
    v = dec.votes(phase=None, register=None, trajectory=None, moods=[], energy=0.6, screen=("inward", 1.0, "tunnel"))
    assert v == {"inward": 0.9}
    v = dec.votes(phase=None, register=None, trajectory=None, moods=[], energy=0.6, screen=("inward", 0.6, "tunnel"))
    assert abs(v["inward"] - 0.54) < 1e-9
    assert dec.votes(phase=None, register=None, trajectory=None, moods=[], energy=0.6) == {}
