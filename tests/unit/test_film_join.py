"""A film's segments joined into one clip (routes/film.py): real ffmpeg on small made-up clips, no network."""
import json
import subprocess
from pathlib import Path

import pytest

from routes.film import join


def clip(path: Path, seconds: float, colour: str, sound: bool, size="320x180", fps=24) -> Path:
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s={size}:r={fps}:d={seconds}"]
    if sound:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", *(["-c:a", "aac"] if sound else []), str(path)]
    subprocess.run(cmd, check=True)
    return path


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_segments_join_in_order_with_their_sound(tmp_path):
    a = clip(tmp_path / "a.mp4", 1.0, "red", sound=True)
    b = clip(tmp_path / "b.mp4", 1.5, "blue", sound=True)
    out = join([a, b], tmp_path / "film.mp4")
    info = probe(out)
    kinds = sorted(s["codec_type"] for s in info["streams"])
    assert kinds == ["audio", "video"]
    assert abs(float(info["format"]["duration"]) - 2.5) < 0.2
    # the first frame is the first clip's, the last the second's
    def pixel(at):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(out), "-frames:v", "1", "-vf",
                              "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        return tuple(raw[:3])
    r, g, b_ = pixel(0.1)
    assert r > 200 and b_ < 60, "the film does not start with the first segment"
    r, g, b_ = pixel(2.3)
    assert b_ > 200 and r < 60, "the film does not end with the second segment"


def test_a_silent_segment_gets_silence_and_another_size_is_fitted(tmp_path):
    a = clip(tmp_path / "a.mp4", 1.0, "red", sound=True)
    b = clip(tmp_path / "b.mp4", 1.0, "blue", sound=False, size="180x320", fps=30)
    info = probe(join([a, b], tmp_path / "film.mp4"))
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (320, 180)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert abs(float(info["format"]["duration"]) - 2.0) < 0.2


def test_all_silent_makes_a_silent_film(tmp_path):
    a = clip(tmp_path / "a.mp4", 1.0, "red", sound=False)
    b = clip(tmp_path / "b.mp4", 1.0, "blue", sound=False)
    info = probe(join([a, b], tmp_path / "film.mp4"))
    assert [s["codec_type"] for s in info["streams"]] == ["video"]


def test_one_clip_is_not_a_film(tmp_path):
    with pytest.raises(ValueError):
        join([clip(tmp_path / "a.mp4", 1.0, "red", sound=True)], tmp_path / "film.mp4")
