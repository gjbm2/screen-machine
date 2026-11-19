"""routes/reframe.py: the shapes and pixels of extending and cropping a picture to another shape. No network: the
vision model and RunPod are never called here (tests/integration/test_generate_roundtrip.py runs the whole path)."""
import json

import pytest
from PIL import Image

from routes import reframe

RED, BLUE = (200, 0, 0), (0, 0, 200)


def test_off_shape():
    assert not reframe.off_shape((2752, 1536), 16 / 9)        # 0.8% off: left alone
    assert reframe.off_shape((1112, 2048), 16 / 9)
    assert reframe.off_shape((1000, 1000), 16 / 9)
    assert not reframe.off_shape((0, 0), 16 / 9)


@pytest.mark.parametrize("size,aspect,canvas,box", [
    ((556, 1024), 16 / 9, (1820, 1024), (632, 0, 1188, 1024)),     # a portrait: grey at the sides
    ((1600, 900), 9 / 16, (1600, 2844), (0, 972, 1600, 1872)),     # a landscape: grey above and below
    ((556, 1024), 1.0, (1024, 1024), (234, 0, 790, 1024)),
])
def test_pad_puts_the_picture_in_the_middle_of_a_grey_canvas(size, aspect, canvas, box):
    out, where = reframe.pad(Image.new("RGB", size, RED), aspect)
    assert out.size == canvas and where == box
    assert out.getpixel((1, 1)) == reframe.GREY
    assert out.getpixel(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)) == RED


@pytest.mark.parametrize("size,aspect,focus,window", [
    ((556, 1024), 16 / 9, None, (0, 356, 556, 669)),                    # no subject: the middle band
    ((556, 1024), 16 / 9, (100, 0, 400, 150), (0, 0, 556, 313)),        # a face at the top: the top band
    ((556, 1024), 16 / 9, (100, 900, 400, 1024), (0, 711, 556, 1024)),  # at the bottom: clamped to the bottom
    ((3840, 1080), 16 / 9, (3000, 200, 3600, 900), (1920, 0, 3840, 1080)),  # a panorama, subject right
])
def test_crop_window_follows_the_subject_and_stays_inside(size, aspect, focus, window):
    assert reframe.crop_window(size, aspect, focus) == window


def test_paste_back_puts_the_originals_pixels_over_the_middle_with_a_soft_edge():
    original = Image.new("RGB", (556, 1024), RED)
    canvas, box = reframe.pad(original, 16 / 9)
    extended = Image.new("RGB", (3840, 2160), BLUE)                     # what the model drew, at the screen's size
    out = reframe.paste_back(extended, original, canvas.size, box)
    assert out.size == (3840, 2160)
    assert out.getpixel((1920, 1080)) == RED
    assert out.getpixel((20, 1080)) == BLUE
    x0 = round(box[0] * 3840 / canvas.width)
    edge = out.getpixel((x0 + 2, 1080))                                 # the seam is blended, not cut
    assert edge != RED and edge != BLUE
    assert out.getpixel((1920, 1)) == RED                               # the frame's own top edge stays hard


def test_focus_asks_the_vision_model_first(monkeypatch):
    monkeypatch.setattr(reframe, "_vision_focus", lambda img: (1, 2, 3, 4))
    monkeypatch.setattr(reframe, "_face_focus", lambda img: pytest.fail("faces looked for after an answer"))
    assert reframe.focus(Image.new("RGB", (10, 10))) == (1, 2, 3, 4)


def test_focus_falls_back_to_faces_then_the_middle(monkeypatch):
    def broken(img):
        raise RuntimeError("the model is down")
    monkeypatch.setattr(reframe, "_vision_focus", broken)
    monkeypatch.setattr(reframe, "_face_focus", lambda img: (5, 6, 7, 8))
    assert reframe.focus(Image.new("RGB", (10, 10))) == (5, 6, 7, 8)
    monkeypatch.setattr(reframe, "_face_focus", lambda img: None)
    assert reframe.focus(Image.new("RGB", (10, 10))) is None
    img = Image.new("RGB", (556, 1024))
    assert reframe.ai_crop(img, 16 / 9).size == (556, 313)


def test_the_vision_answer_is_turned_into_pixels(monkeypatch):
    import routes.openai
    monkeypatch.setattr(routes.openai, "openai_prompt",
                        lambda *a, **k: {"x0": 0.25, "y0": 0.1, "x1": 0.75, "y1": 1.4, "explanation": "a face"})
    assert reframe._vision_focus(Image.new("RGB", (400, 1000))) == (100, 100, 300, 1000)


def test_face_focus_on_a_picture_with_no_face():
    assert reframe._face_focus(Image.new("RGB", (400, 800), RED)) is None


def test_a_screens_choice_defaults_to_whole(monkeypatch):
    import routes.utils
    monkeypatch.setattr(routes.utils, "_load_json_once", lambda *a, **k: [
        {"id": "north-screen", "reframe": "extend"}, {"id": "south-screen", "reframe": "sideways"}, {"id": "devtest"}])
    assert reframe.screen_policy("north-screen") == "extend"
    assert reframe.screen_policy("south-screen") == "whole"
    assert reframe.screen_policy("devtest") == "whole"
    assert reframe.screen_policy("nowhere") == "whole"


def test_setting_a_screens_choice(tmp_path, monkeypatch):
    """PUT /api/publish-destinations/<id>/reframe (Settings > Screens) writes the deployment's file, nothing else."""
    from flask import Flask
    import routes.publish_api as publish_api
    path = tmp_path / "publish-destinations.json"
    dests = [{"id": "north-screen", "maxwidth": 3840, "maxheight": 2160, "name": "North"}, {"id": "_recent"}]
    path.write_text(json.dumps(dests, indent=2) + "\n")
    monkeypatch.setattr(publish_api, "PUBLISH_DESTINATIONS_FILE", str(path))
    app = Flask(__name__)
    app.register_blueprint(publish_api.publish_api, url_prefix="/api")
    client = app.test_client()

    ok = client.put("/api/publish-destinations/north-screen/reframe", json={"reframe": "crop"})
    assert ok.status_code == 200 and ok.get_json() == {"id": "north-screen", "reframe": "crop"}
    saved = json.loads(path.read_text())
    assert saved[0] == {**dests[0], "reframe": "crop"} and saved[1] == dests[1]
    assert client.put("/api/publish-destinations/north-screen/reframe", json={"reframe": "sideways"}).status_code == 400
    assert client.put("/api/publish-destinations/_recent/reframe", json={"reframe": "crop"}).status_code == 400
    assert client.put("/api/publish-destinations/nowhere/reframe", json={"reframe": "crop"}).status_code == 404
