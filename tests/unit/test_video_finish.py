"""A video for a screen ends at the screen's size, as a still does (routes/generate.video_finish)."""
import base64
import io
import json
from argparse import Namespace
from pathlib import Path

from PIL import Image

from routes.generate import video_finish

WORKFLOWS = Path(__file__).resolve().parents[2] / "routes" / "workflows"


def graph(name="h3-loop.json"):
    return json.loads((WORKFLOWS / name).read_text())


def img(w, h):
    b = io.BytesIO()
    Image.new("RGB", (w, h)).save(b, "JPEG")
    return {"name": "input.png", "image": base64.b64encode(b.getvalue()).decode()}


def parts(g):
    loader = next(n for n in g.values() if n["class_type"] == "UpscaleModelLoader")
    fin = next(n for n in g.values() if "{{VIDEO-FINISH}}" in n["_meta"]["title"])
    return loader["inputs"]["model_name"], (fin["inputs"]["width"], fin["inputs"]["height"])


def test_a_16_9_still_on_a_4k_screen_comes_back_at_exactly_4k_through_x4():
    g = graph()
    assert video_finish(g, Namespace(width=1344, height=768, images=[img(3840, 2160)]), (3840, 2160)) == (3840, 2160)
    assert parts(g) == ("RealESRGAN_x4plus.pth", (3840, 2160))


def test_a_1080p_screen_needs_only_x2():
    g = graph()
    video_finish(g, Namespace(width=1344, height=768, images=[img(1920, 1080)]), (1920, 1080))
    assert parts(g) == ("RealESRGAN_x2plus.pth", (1920, 1080))


def test_a_portrait_picture_keeps_its_shape_inside_the_screen():
    g = graph("h3-i2v.json")
    video_finish(g, Namespace(width=768, height=1344, images=[img(1080, 1920)]), (3840, 2160))
    model, (w, h) = parts(g)
    assert model == "RealESRGAN_x2plus.pth" and h == 2160 and abs(w / h - 1080 / 1920) < 0.01


def test_every_video_graph_finishes_after_the_upscale_and_before_the_in_betweens():
    for name in ("h3-loop.json", "h3-i2v.json", "h3-fl2v.json", "h3-film.json"):
        g = graph(name)
        fin = next(k for k, n in g.items() if "{{VIDEO-FINISH}}" in n["_meta"]["title"])
        assert "{{UPSCALE_FLAG}}" in g[fin]["_meta"]["title"], name           # skipped with the upscale, inline
        assert g[g[fin]["inputs"]["image"][0]]["class_type"] == "ImageUpscaleWithModel", name
        rife = next(n for n in g.values() if n["class_type"] == "FrameInterpolate")
        assert rife["inputs"]["images"] == [fin, 0], name
