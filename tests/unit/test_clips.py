"""A still's clips are one object with it (routes/clips.py)."""
from routes.clips import group, still_of


def test_clips_go_under_their_still_newest_first():
    files = [{"filename": "a.jpg", "metadata": {}},
             {"filename": "a1.mp4", "metadata": {"still": "a.jpg"}, "modified": 1},
             {"filename": "a2.mp4", "metadata": {"still": "a.jpg"}, "modified": 2},
             {"filename": "orphan.mp4", "metadata": {"still": "gone.jpg"}},
             {"filename": "b.jpg", "metadata": {}}]
    out = group(files)
    assert [f["filename"] for f in out] == ["a.jpg", "orphan.mp4", "b.jpg"]
    assert [c["filename"] for c in out[0]["clips"]] == ["a2.mp4", "a1.mp4"]


def test_still_named_in_the_clip_block_too():
    assert still_of({"clip": {"still": "x.jpg"}}) == "x.jpg"
    assert still_of(None) is None
