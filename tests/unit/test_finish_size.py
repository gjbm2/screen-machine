"""routes.generate.finish_size: the size the {{FINISH}} node scales a picture to. No network.

Owner, 19 Nov 2026: a portrait photo uploaded and adapted came back cropped to the landscape screen; the output must
keep the uploaded picture's proportions."""
import base64
from io import BytesIO

import pytest
from PIL import Image

from routes.generate import finish_size, image_size

SCREEN = (3840, 2160)


@pytest.mark.parametrize("picture,box,turn,expected", [
    ((2752, 1536), SCREEN, False, SCREEN),             # Qwen's latent is 0.8% off 16:9: fills the screen
    ((1920, 1088), SCREEN, False, SCREEN),             # a 1920×1088 graph's, 0.7% off
    ((2048, 1152), SCREEN, False, SCREEN),             # exactly 16:9
    ((1112, 2048), SCREEN, False, (1173, 2160)),       # the owner's portrait photo on a TV: whole, full height
    ((1112, 2048), SCREEN, True, (2085, 3840)),        # the same with no screen: the 4K budget turned upright
    ((1024, 1024), SCREEN, False, (2160, 2160)),
    ((1024, 1024), SCREEN, True, (2160, 2160)),
    ((1024, 768), SCREEN, False, (2880, 2160)),
    ((3000, 1000), SCREEN, False, (3840, 1280)),       # wider than the screen: full width
    ((2752, 1536), (1080, 2400), False, (1080, 603)),  # a landscape render on a portrait screen: whole, not a strip
    ((1536, 2752), (1080, 2400), False, (1080, 1935)),
    (None, SCREEN, False, SCREEN),                      # nothing known about the picture: the box
    (("2752", "1536"), ("3840", "2160"), False, SCREEN),  # sizes that arrive as text
])
def test_finish_size(picture, box, turn, expected):
    assert finish_size(picture, box, turn) == expected


def test_a_screen_box_never_turns():
    assert finish_size((1112, 2048), SCREEN, turn=False)[1] == 2160


def _b64(img, **save):
    buf = BytesIO()
    img.save(buf, format="JPEG", **save)
    return {"name": "photo.jpg", "image": base64.b64encode(buf.getvalue()).decode()}


def test_image_size_reads_a_request_image():
    assert image_size(_b64(Image.new("RGB", (556, 1024)))) == (556, 1024)


def test_image_size_applies_the_rotate_tag_as_comfyuis_loadimage_does():
    img = Image.new("RGB", (1024, 556))
    exif = img.getexif()
    exif[0x0112] = 6
    assert image_size(_b64(img, exif=exif.tobytes())) == (556, 1024)


def test_image_size_of_something_that_is_not_an_image():
    assert image_size({"name": "x.jpg", "image": "bm90IGFuIGltYWdl"}) is None
    assert image_size({"name": "x.jpg"}) is None


@pytest.mark.parametrize("size,box,expected", [
    ((2048, 1152), (1080, 2400), (1024, 2304)),     # a phone: the same pixels, the phone's shape
    ((2752, 1536), (1080, 2400), (1376, 3072)),
    ((2048, 1152), (3840, 2160), (2048, 1152)),     # a TV: as it is
    ((2752, 1536), (3840, 2160), (2752, 1536)),     # 0.8% off 16:9: as it is
    ((2048, 1152), (2000, 2000), (1536, 1536)),
])
def test_latent_for_screen(size, box, expected):
    from routes.generate import latent_for_screen
    assert latent_for_screen(size, box) == expected
