"""routes/result_source.py: a finished job's picture as one thing, whether the worker sent a URL or the image itself."""
import os
import time
from io import BytesIO

import pytest
from PIL import Image

import routes.bucketer as bucketer
import routes.result_source as rs


def image_bytes(fmt, colour=(200, 0, 0)):
    buf = BytesIO()
    Image.new("RGB", (32, 18), colour).save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def output(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(bucketer, "BASE_OUTPUT", out)
    return out


@pytest.mark.parametrize("fmt,filename", [("PNG", "a_00001_.png"), ("WEBP", "a.webp"), ("JPEG", "a.jpg"),
                                          ("PNG", ""), ("PNG", None), ("PNG", "A.PNG")])
def test_an_inline_still_is_kept_as_a_jpeg(output, fmt, filename):
    path = rs.save_inline(image_bytes(fmt), filename, "job_1")
    assert path == str(output / "_inline" / "worker_job_1.jpg")
    with Image.open(path) as img:
        assert img.format == "JPEG" and img.size == (32, 18)


def test_an_inline_video_keeps_its_bytes(output):
    path = rs.save_inline(b"\x00\x00\x00\x18ftypmp42", "clip.mp4", "job_2")
    assert path.endswith("worker_job_2.mp4") and open(path, "rb").read() == b"\x00\x00\x00\x18ftypmp42"


def test_a_still_that_will_not_decode_is_kept_as_sent(output):
    path = rs.save_inline(b"not an image", "a.png", "job_3")
    assert path.endswith("worker_job_3.png") and open(path, "rb").read() == b"not an image"


def test_an_empty_inline_result_is_an_error(output):
    with pytest.raises(ValueError):
        rs.save_inline(b"", "a.png", "job_4")


def test_old_inline_results_are_pruned(output):
    old = rs.save_inline(image_bytes("PNG"), "a.png", "old")
    two_days_ago = time.time() - 2 * 24 * 3600
    os.utime(old, (two_days_ago, two_days_ago))
    new = rs.save_inline(image_bytes("PNG"), "a.png", "new")
    assert not os.path.exists(old) and os.path.exists(new)


def test_read_bytes_from_a_file_and_a_url(output, monkeypatch):
    path = rs.save_inline(image_bytes("PNG"), "a.png", "job_5")
    assert rs.read_bytes(path) == open(path, "rb").read()

    seen = {}

    class Response:
        content = b"from the bucket"

        def raise_for_status(self):
            pass

    def get(url, timeout=None, **kwargs):
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(rs.requests, "get", get)
    assert rs.read_bytes("https://bucket.example/a.png") == b"from the bucket"
    assert seen["timeout"], "a download with no timeout can hang a job for ever"


def test_read_bytes_refuses_a_file_outside_output(output, tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("KEY=1")
    with pytest.raises(ValueError):
        rs.read_bytes(str(secret))
    with pytest.raises(ValueError):
        rs.read_bytes(str(output / ".." / ".env"))


def test_browser_url(output):
    path = rs.save_inline(image_bytes("PNG"), "a.png", "job_6")
    assert rs.browser_url(path) == "/output/_inline/worker_job_6.jpg"
    assert rs.browser_url("https://bucket.example/a.png?sig=1") == "https://bucket.example/a.png?sig=1"
    assert rs.browser_url("/output/north-screen/a.jpg") == "/output/north-screen/a.jpg"
    assert rs.browser_url("/home/someone/.env") is None
    assert rs.browser_url(None) is None
