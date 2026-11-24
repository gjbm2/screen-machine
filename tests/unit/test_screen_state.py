"""What a screen shows, as one document (routes/screen_state.py): a still, a still with its own clip, a still reached
through a transition, a video someone published; written in one step, never-changing URLs, pruned."""
import json
from pathlib import Path

import pytest
from PIL import Image

from routes import screen_state as ss


@pytest.fixture
def out(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "output_root", lambda: tmp_path)
    monkeypatch.setattr(ss, "push", lambda screen, state: None)
    return tmp_path


def still(out, name="north-screen.jpg", size=(64, 36)):
    p = out / name
    Image.new("RGB", size, (10, 20, 30)).save(p)
    return p


def clip_file(out, name="north-screen.mp4"):
    p = out / name
    p.write_bytes(b"\0\0\0\x18ftypmp42")
    return p


CLIP = {"eased": True, "pace": "slow", "play_s": 30, "rest_s": [35, 55]}


def test_a_still_is_the_picture(out):
    s = ss.placed("north-screen", still(out), {"origin": "north-screen/a.jpg", "filename": "a.jpg"})
    assert s["seq"] == 1 and s["clip"] is None and s["video"] is None and s["transition"] is None
    assert s["still"]["origin"] == "north-screen/a.jpg" and (s["still"]["width"], s["still"]["height"]) == (64, 36)
    assert s["still"]["url"] == "/output/_screens/north-screen/000001.jpg"
    assert (out / "_screens/north-screen/000001.jpg").exists()
    assert json.loads((out / "north-screen.state.json").read_text()) == s


def test_its_own_clip_stays_a_still_with_a_clip(out):
    ss.placed("north-screen", still(out), {"origin": "south-screen/a.jpg", "filename": "a.jpg"})
    s = ss.placed("north-screen", clip_file(out), {"still": "a.jpg", "clip": CLIP})
    assert s["seq"] == 2 and s["video"] is None
    assert s["still"]["url"].endswith("000001.jpg")            # the picture is unchanged
    assert s["clip"]["url"].endswith("000002.mp4") and s["clip"]["rest_s"] == [35, 55]


def test_a_clip_of_another_picture_is_a_video(out):
    ss.placed("north-screen", still(out), {"origin": "north-screen/a.jpg", "filename": "a.jpg"})
    s = ss.placed("north-screen", clip_file(out), {"still": "b.jpg", "clip": CLIP})
    assert s["still"] is None and s["clip"] is None and s["video"]["url"].endswith("000002.mp4")


def test_a_published_video_is_the_picture(out):
    ss.placed("north-screen", still(out), {"origin": "north-screen/a.jpg"})
    # a normal video (ease_video: it doesn't come back to its first frame), with no still named
    s = ss.placed("north-screen", clip_file(out), {"clip": {"eased": True, "returns": False, "pace": "natural"}})
    assert s["video"] and s["still"] is None
    # named its still, but plays to its end and holds: still a video
    ss.placed("north-screen", still(out), {"origin": "north-screen/a.jpg"})
    s = ss.placed("north-screen", clip_file(out), {"still": "a.jpg", "clip": {"eased": True, "returns": False}})
    assert s["video"] and s["clip"] is None


def test_a_transition_comes_with_its_still_and_not_with_the_next(out):
    t = {"url": "/output/south-screen/transitions/x.mp4", "from": "north-screen/a.jpg", "to": "south-screen/b.jpg",
         "at": 1795559403.9, "cost_usd": 0.14}
    s = ss.placed("south-screen", still(out, "south-screen.jpg"), {"origin": "south-screen/b.jpg", "transition": t})
    assert s["transition"]["url"] == t["url"] and "cost_usd" not in s["transition"]
    s = ss.placed("south-screen", still(out, "south-screen.jpg"), {"origin": "south-screen/c.jpg"})
    assert s["transition"] is None


def test_each_document_names_files_that_never_change(out):
    for i in range(ss.KEEP + 5):
        ss.placed("north-screen", still(out), {"origin": f"north-screen/{i}.jpg"})
    kept = sorted(p.name for p in (out / "_screens/north-screen").iterdir())
    assert len(kept) == ss.KEEP and kept[-1] == f"{ss.KEEP + 5:06d}.jpg"
    # the still a clip document still names is never pruned
    s = ss.read("north-screen")
    for i in range(ss.KEEP + 2):
        s = ss.placed("north-screen", clip_file(out), {"still": f"{ss.KEEP + 4}.jpg", "clip": CLIP})
    assert (out / "_screens/north-screen" / Path(s["still"]["url"]).name).exists()


def test_a_failure_never_raises_into_the_publish(out, monkeypatch):
    monkeypatch.setattr(ss, "_keep", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    assert ss.placed("north-screen", still(out), {}) is None


def test_its_clip_is_found_by_the_screen_copy_name_when_the_copy_was_renamed(out):
    ss.placed("north-screen", still(out), {"origin": "south-screen/a.jpg", "filename": "a_1.jpg"})
    s = ss.placed("north-screen", clip_file(out), {"still": "a_1.jpg", "clip": CLIP})
    assert s["clip"] and s["video"] is None
