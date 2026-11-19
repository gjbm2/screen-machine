"""A picture of another shape than the place it goes (owner, 19 Nov 2026): extend it with AI, or let AI pick the
part to crop, or show it whole.

Two places use it:
- a screen (publish-destinations.json "reframe": extend | crop | whole, set on Settings > Screens): a still published
  to the screen whose shape is off the screen's is cropped before it goes up, or shown whole at once and swapped for
  its extended copy when that is made (routes/publisher.py). The library keeps the original; reframed copies are
  kept under output/_reframe/ and reused.
- the generator's Adapt and Combine (params "shape" and "fill"): a photo turned into another shape before the edit,
  so it is still one job (routes/generate.py).

Extending is Qwen 2.1 edit on the picture centred on a grey canvas of the new shape: it fills the grey with more of
the scene and redraws the middle with the layout kept (checked 19 Nov: a portrait harbour painting to 16:9; a blurred
copy instead of grey drifted more). For a screen the original's own pixels are put back over the middle with a soft
edge, so a photograph (a face) is exactly as it was. Cropping asks the vision model where the subject is, else finds
faces, else keeps the centre.
"""
from __future__ import annotations

import base64
import hashlib
import time
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from utils.logger import info, warning

# A picture this close to the shape it goes into is left alone (routes.generate fills the screen with it)
TOLERANCE = 0.02
SHAPES = {"landscape": 16 / 9, "portrait": 9 / 16, "square": 1.0}
MODES = ("extend", "crop", "whole")
GREY = (128, 128, 128)
FEATHER = 0.02          # the soft edge of the original put back over an extended picture, as a share of its width
EXTEND_WORKFLOW = "qwen21-edit.json"
EXTEND_PROMPT = ("Extend this picture to fill the whole frame: replace the plain grey areas with more of the same "
                 "scene, continuing it seamlessly in the same style, light and colours. Keep the original part of the "
                 "picture exactly as it is.")
# Before an edit's own instruction, when the photo was put on a grey canvas of the chosen shape
EXTEND_EDIT = ("Picture 1 has plain grey areas at its edges: fill them by extending the scene seamlessly in the same "
               "style and light. Then: ")
CACHE_DAYS = 60


def off_shape(size, aspect, tolerance=TOLERANCE) -> bool:
    """True when a picture of size (w, h) is further than tolerance from the shape aspect (w / h)."""
    w, h = size
    return bool(w and h and aspect) and abs((w / h) / aspect - 1) > tolerance


def pad(img: Image.Image, aspect: float) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """The picture centred on a grey canvas of the shape, and where it sits (x0, y0, x1, y1)."""
    w, h = img.size
    if w / h < aspect:
        cw, ch = round(h * aspect), h
    else:
        cw, ch = w, round(w / aspect)
    canvas = Image.new("RGB", (cw, ch), GREY)
    x0, y0 = (cw - w) // 2, (ch - h) // 2
    canvas.paste(img.convert("RGB"), (x0, y0))
    return canvas, (x0, y0, x0 + w, y0 + h)


def crop_window(size, aspect, focus=None) -> tuple[int, int, int, int]:
    """The largest window of the shape inside a picture of size, centred on focus (x0, y0, x1, y1: what must stay
    in view, in pixels) as far as the picture allows; no focus: the centre."""
    w, h = size
    if w / h > aspect:
        cw, ch = round(h * aspect), h
    else:
        cw, ch = w, round(w / aspect)
    fx, fy = ((focus[0] + focus[2]) / 2, (focus[1] + focus[3]) / 2) if focus else (w / 2, h / 2)
    x0 = min(max(round(fx - cw / 2), 0), w - cw)
    y0 = min(max(round(fy - ch / 2), 0), h - ch)
    return x0, y0, x0 + cw, y0 + ch


FOCUS_SCHEMA = {
    "type": "object",
    "properties": {
        "x0": {"type": "number"}, "y0": {"type": "number"}, "x1": {"type": "number"}, "y1": {"type": "number"},
        "explanation": {"type": "string"},
    },
    "required": ["x0", "y0", "x1", "y1", "explanation"],
}
FOCUS_SYSTEM = ("You frame pictures. Given a picture, give the box around what must stay in view when it is cropped "
                "to another shape: the main subject (people's faces and heads first, then the action or the object "
                "the picture is about). Answer as fractions of the picture's width and height, 0 to 1, x to the "
                "right and y down.")


def _vision_focus(img: Image.Image):
    from routes.openai import openai_prompt
    buf = BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=90)
    answer = openai_prompt("Where is the subject of this picture?", system_prompt=FOCUS_SYSTEM, schema=FOCUS_SCHEMA,
                           images=[base64.b64encode(buf.getvalue()).decode()], verbose=False)
    x0, y0, x1, y1 = (min(max(float(answer[k]), 0.0), 1.0) for k in ("x0", "y0", "x1", "y1"))
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"an empty box: {answer}")
    w, h = img.size
    return round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)


def _face_focus(img: Image.Image):
    import cv2
    import numpy as np
    grey = np.array(img.convert("L"))
    scale = 1024 / max(grey.shape)
    small = cv2.resize(grey, None, fx=scale, fy=scale) if scale < 1 else grey
    found = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml").detectMultiScale(
        small, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
    if len(found) == 0:
        return None
    s = min(scale, 1)
    return (round(min(x for x, _, _, _ in found) / s), round(min(y for _, y, _, _ in found) / s),
            round(max(x + fw for x, _, fw, _ in found) / s), round(max(y + fh for _, y, _, fh in found) / s))


def focus(img: Image.Image):
    """What must stay in view when the picture is cropped: the vision model's answer, else its faces, else None (the
    centre). Never fails."""
    for name, find in (("vision", _vision_focus), ("faces", _face_focus)):
        try:
            box = find(img)
            if box:
                info(f"[reframe] focus by {name}: {box}")
                return box
        except Exception as e:
            warning(f"[reframe] focus by {name} failed: {e}")
    return None


def ai_crop(img: Image.Image, aspect: float) -> Image.Image:
    return img.crop(crop_window(img.size, aspect, focus(img)))


def paste_back(extended: Image.Image, original: Image.Image, canvas_size, box) -> Image.Image:
    """The original's own pixels over the middle of its extended copy, with a soft edge where it meets the new parts.
    canvas_size and box are the padded canvas's size and where the original sat on it (pad())."""
    W, H = extended.size
    sx, sy = W / canvas_size[0], H / canvas_size[1]
    x0, y0, x1, y1 = round(box[0] * sx), round(box[1] * sy), round(box[2] * sx), round(box[3] * sy)
    inner = original.convert("RGB").resize((x1 - x0, y1 - y0), Image.LANCZOS)
    feather = max(2, round(FEATHER * W))
    mask = Image.new("L", inner.size, 0)
    # solid inside, a ramp on each edge that meets the extension (an edge on the frame's own border stays hard)
    left, top = (feather if x0 > 0 else 0), (feather if y0 > 0 else 0)
    right, bottom = (feather if x1 < W else 0), (feather if y1 < H else 0)
    ImageDraw.Draw(mask).rectangle((left, top, inner.width - 1 - right, inner.height - 1 - bottom), fill=255)
    if left or top or right or bottom:
        mask = mask.filter(ImageFilter.GaussianBlur(feather / 2))
    out = extended.convert("RGB").copy()
    out.paste(inner, (x0, y0), mask)
    return out


def extend(img: Image.Image, aspect: float, size, label: str = "", target: str | None = None) -> Image.Image:
    """The picture extended to the shape by Qwen 2.1 edit at size (w, h), the original put back over the middle.
    A tracker job (source "reframe") shows it in the Jobs dock. Raises when the job fails."""
    import routes.generate as generate
    from routes.jobs import get_tracker
    from routes.jobs.workflows import target_name
    from routes.result_source import read_bytes
    canvas, box = pad(img, aspect)
    buf = BytesIO()
    canvas.save(buf, format="JPEG", quality=95)
    job = get_tracker().create(prompt=label or "Extend the picture to fill the screen", source="reframe",
                               target=target, target_name=target_name(target))
    out = generate.start(prompt=EXTEND_PROMPT, workflow=EXTEND_WORKFLOW, job_id=job["id"],
                         images=[{"name": "reframe.jpg", "image": base64.b64encode(buf.getvalue()).decode()}],
                         maxwidth=size[0], maxheight=size[1], silent=True)
    with Image.open(BytesIO(read_bytes(out["message"]))) as result:
        result = ImageOps.exif_transpose(result).convert("RGB")
    if result.size != tuple(size):
        result = ImageOps.fit(result, tuple(size), Image.LANCZOS)
    return paste_back(result, img, canvas.size, box)


# ---------------------------------------------------------------------------------------------- reframed copies

def cache_dir() -> Path:
    from routes.bucketer import BASE_OUTPUT
    return Path(BASE_OUTPUT) / "_reframe"


def cache_path(src: Path, size, mode: str) -> Path:
    """Where the reframed copy of a picture for a screen of size is kept: by the picture's bytes, so a copy in
    another library or a re-publish finds it."""
    digest = hashlib.sha1(Path(src).read_bytes()).hexdigest()[:20]
    return cache_dir() / f"{digest}-{size[0]}x{size[1]}-{mode}.jpg"


def save_cached(img: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    img.convert("RGB").save(tmp, format="JPEG", quality=95, subsampling=0)
    tmp.replace(path)
    cutoff = time.time() - CACHE_DAYS * 86400
    for old in path.parent.glob("*.jpg"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
    return path


def screen_policy(screen_id: str) -> str:
    """The screen's choice for a picture of another shape (Settings > Screens), read fresh each time: extend, crop or
    whole (the default)."""
    from routes.utils import _load_json_once
    try:
        dest = next((d for d in _load_json_once("publish_destinations", "publish-destinations.json")
                     if d.get("id") == screen_id), {})
    except Exception as e:
        warning(f"[reframe] could not read the screens: {e}")
        return "whole"
    mode = dest.get("reframe")
    return mode if mode in MODES else "whole"
