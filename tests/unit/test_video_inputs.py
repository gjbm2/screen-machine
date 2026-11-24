"""A video's pictures go to the worker at the size the video is drawn at (routes/generate.py at_size; owner, 24 Nov:
anything larger is lost in the model's own resize, unpredictably, and is wasteful on the way in)."""
import base64
import io

from PIL import Image

from routes.generate import at_size


def _pic(size, fmt="JPEG"):
    b = io.BytesIO()
    Image.new("RGB", size, (120, 80, 40)).save(b, fmt)
    return {"name": "input.png", "image": base64.b64encode(b.getvalue()).decode()}


def _size(image):
    return Image.open(io.BytesIO(base64.b64decode(image["image"]))).size


def test_a_4k_still_goes_at_the_drawn_size():
    src = _pic((3840, 2160))
    out = at_size(src, (1344, 768))
    assert _size(out) == (1344, 768)
    assert out["name"] == "input.png"
    assert len(out["image"]) < len(src["image"])


def test_a_small_or_png_picture_comes_to_the_drawn_size_too():
    assert _size(at_size(_pic((640, 360), "PNG"), (1344, 768))) == (1344, 768)


def test_an_unreadable_picture_goes_as_given():
    bad = {"name": "input.png", "image": "bm90IGFuIGltYWdl"}
    assert at_size(bad, (1344, 768)) is bad
