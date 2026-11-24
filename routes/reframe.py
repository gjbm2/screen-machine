"""A picture of another shape than the place it goes (owner, 19 Nov 2026). Four ways to fit it to a screen:
- auto: an AI read of the picture plans the reframe for this screen (a little cropping where the picture has little to
  lose, the rest extended, the subject placed, what to paint in the new parts), then one generator pass carries it out;
- crop: AI picks the part to keep;
- frame: shown whole, with bars;
- adapt: the whole picture, extended with AI.
When publishing from the app the person is asked (Auto first); a publish nobody watches (a schedule, Alexa) uses the
screen's setting.

Two places use it:
- a screen (publish-destinations.json "reframe": auto | crop | frame | adapt, set on Settings > Screens, or the
  person's choice for one publish): a still published to the screen whose shape is off the screen's is cropped before
  it goes up, or reframed in the background while the screen keeps its picture, then placed (routes/publisher.py). The library keeps the original; reframed copies are
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
MODES = ("auto", "crop", "frame", "adapt")
OLD_MODES = {"extend": "adapt", "whole": "frame"}     # the first names (19 Nov), still read from a screen's setting
GREY = (128, 128, 128)
FEATHER = 0.02          # the soft edge of the original put back over an extended picture, as a share of its width
EXTEND_WORKFLOW = "qwen21-edit.json"
EXTEND_PROMPT = ("Extend this picture to fill the whole frame: replace the plain grey areas with more of the same "
                 "scene, continuing it seamlessly in the same style, light and colours. Keep the original part of the "
                 "picture exactly as it is.")
# Before an edit's own instruction, when the photo was put on a grey canvas of the chosen shape
EXTEND_EDIT = ("Image 1 has plain grey areas at its edges: fill them by extending the scene seamlessly in the same "
               "style and light. Then: ")
CACHE_DAYS = 60


def off_shape(size, aspect, tolerance=TOLERANCE) -> bool:
    """True when a picture of size (w, h) is further than tolerance from the shape aspect (w / h)."""
    w, h = size
    return bool(w and h and aspect) and abs((w / h) / aspect - 1) > tolerance


def mode_of(value) -> str | None:
    """A fit mode by its name or its first name, else None."""
    value = OLD_MODES.get(value, value)
    return value if value in MODES else None


def pad(img: Image.Image, aspect: float, place: float = 0.5) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """The picture on a grey canvas of the shape, and where it sits (x0, y0, x1, y1). place: where it sits along the
    side that grows, 0 (left / top) to 1 (right / bottom); 0.5 centred."""
    w, h = img.size
    if w / h < aspect:
        cw, ch = round(h * aspect), h
    else:
        cw, ch = w, round(w / aspect)
    canvas = Image.new("RGB", (cw, ch), GREY)
    place = min(max(float(place), 0.0), 1.0)
    x0, y0 = round((cw - w) * place), round((ch - h) * place)
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
    w, h = img.size
    vals = [float(answer[k]) for k in ("x0", "y0", "x1", "y1")]
    if max(vals) > 1000:
        # pixels of the picture it was shown (routes/openai sends it at most 1536 px on its long side)
        sw, sh = (w, h) if max(w, h) <= 1536 else (round(w * 1536 / max(w, h)), round(h * 1536 / max(w, h)))
        vals = [vals[0] / sw, vals[1] / sh, vals[2] / sw, vals[3] / sh]
    elif max(vals) > 1.5:
        # Gemini's own way of giving a box, whatever it is asked for: thousandths of the width and height. (19 Nov,
        # a 3238x2160 group picture: 339,234 to 697,985 is the two people shaking hands as thousandths, and someone
        # else's back as pixels.) Clamped to 1 these were an empty box.
        vals = [v / 1000 for v in vals]
    x0, y0, x1, y1 = (min(max(v, 0.0), 1.0) for v in vals)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"an empty box: {answer}")
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


# Auto: the AI read that plans the reframe (owner, 19 Nov: "ai should reason through how to adapt the image for the
# screen smartly -- which may be a bit of cropping, a bit of extending")
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "keep_start": {"type": "number"}, "keep_end": {"type": "number"}, "place": {"type": "number"},
        "fill": {"type": "string"}, "explanation": {"type": "string"},
    },
    "required": ["keep_start", "keep_end", "place", "fill", "explanation"],
}
PLAN_SYSTEM = """You reframe a picture for a screen of another shape, as a picture editor would. The picture is {pw}x{ph}
and the screen is {sw}x{sh}, so the picture is too {long_word} for it. Two tools: cut some of its {cut_side} away, and
paint more of the scene at its {grow_side} (an AI continues the scene there). Use each as far as it serves the picture:
- Cut only what adds little (empty sky, blank floor, a plain margin), never the subject (faces, heads, the action,
  what the picture is about) or what frames it. Keep everything when nothing is expendable.
- keep_start and keep_end: the part to keep {along}, as fractions 0 to 1 ({start_word} = 0).
- place: where the kept part sits across the new frame {across}: 0 = {place0}, 0.5 = centred, 1 = {place1}. Leave room
  where the subject looks or moves; centre a frontal or symmetrical subject.
- fill: one sentence on what to paint in the new parts, continuing this scene (say what goes on each side).
- explanation: one short sentence."""


def _plan_words(size, aspect):
    tall = size[0] / size[1] < aspect
    if tall:
        return {"long_word": "tall", "cut_side": "top and bottom", "grow_side": "left and right", "along": "from top to bottom",
                "start_word": "top", "across": "from left to right", "place0": "at the left", "place1": "at the right"}
    return {"long_word": "wide", "cut_side": "left and right", "grow_side": "top and bottom", "along": "from left to right",
            "start_word": "left", "across": "from top to bottom", "place0": "at the top", "place1": "at the bottom"}


def plan(img: Image.Image, aspect: float, size) -> dict:
    """The AI's reframe of a picture for a screen of size (w, h): {"keep": (x0, y0, x1, y1) in pixels, "place",
    "fill", "why"}. The kept part is cut only along the picture's long side and never past the screen's shape (beyond
    that there would be nothing left to extend). With no answer: everything kept, centred, a plain continuation."""
    from routes.openai import openai_prompt
    w, h = img.size
    tall = w / h < aspect
    answer = {}
    try:
        buf = BytesIO()
        small = img.convert("RGB").copy()
        small.thumbnail((1536, 1536))
        small.save(buf, format="JPEG", quality=90)
        answer = openai_prompt("Plan the reframe of this picture for the screen.",
                               system_prompt=PLAN_SYSTEM.format(pw=w, ph=h, sw=size[0], sh=size[1],
                                                                **_plan_words(img.size, aspect)),
                               schema=PLAN_SCHEMA, images=[base64.b64encode(buf.getvalue()).decode()], verbose=False)
    except Exception as e:
        warning(f"[reframe] no plan from the AI ({e}); extending the whole picture")
    start = min(max(float(answer.get("keep_start", 0.0)), 0.0), 1.0)
    end = min(max(float(answer.get("keep_end", 1.0)), 0.0), 1.0)
    if end <= start:
        start, end = 0.0, 1.0
    length = h if tall else w
    shortest = (w / aspect) if tall else (h * aspect)          # cut no further than the screen's own shape
    a, b = start * length, end * length
    if b - a < shortest:
        mid = (a + b) / 2
        a = min(max(mid - shortest / 2, 0), length - shortest)
        b = a + shortest
    keep = (0, round(a), w, round(b)) if tall else (round(a), 0, round(b), h)
    return {"keep": keep, "place": min(max(float(answer.get("place", 0.5)), 0.0), 1.0),
            "fill": str(answer.get("fill") or "").strip(), "why": str(answer.get("explanation") or "").strip()}


def auto(img: Image.Image, aspect: float, size, label: str = "", target: str | None = None) -> Image.Image:
    """The picture reframed for the screen as the AI planned it: the kept part, placed, the rest painted to its
    plan (one generator pass), the kept part's own pixels put back. A plan that is all cropping needs no pass."""
    p = plan(img, aspect, size)
    kept = img.crop(p["keep"])
    info(f"[reframe] auto plan: keep {p['keep']} of {img.size}, place {p['place']:.2f}; {p['fill']} ({p['why']})")
    if not off_shape(kept.size, aspect):
        return ImageOps.fit(kept, tuple(size), Image.LANCZOS)
    prompt = EXTEND_PROMPT + (f" In the new parts: {p['fill']}" if p["fill"] else "")
    return extend(kept, aspect, size, label=label, target=target, place=p["place"], prompt=prompt)


def extend(img: Image.Image, aspect: float, size, label: str = "", target: str | None = None, place: float = 0.5,
           prompt: str = EXTEND_PROMPT) -> Image.Image:
    """The picture extended to the shape by Qwen 2.1 edit at size (w, h), the original put back over its part.
    A tracker job (source "reframe") shows it in the Jobs dock. The model now and then leaves the grey as it is (19 Nov,
    one of five on devtest): then it is asked once more with another seed. Raises when the job fails or the new parts
    are still grey, so the picture stays whole rather than going up with grey bars."""
    import routes.generate as generate
    from routes.jobs import get_tracker
    from routes.jobs.workflows import target_name
    from routes.result_source import read_bytes
    canvas, box = pad(img, aspect, place)
    buf = BytesIO()
    canvas.save(buf, format="JPEG", quality=95)
    # a picture whose own edges are that grey (a studio backdrop): grey new parts are it, carried on (24 Nov: a portrait
    # on a grey backdrop was refused twice and cropped to the face)
    own_grey = edge_grey(img) >= OWN_GREY
    for attempt in (1, 2):
        job = get_tracker().create(prompt=(label or "Extend the picture to fill the screen")
                                   + (" (again: the first left grey)" if attempt == 2 else ""),
                                   source="reframe", target=target, target_name=target_name(target))
        out = generate.start(prompt=prompt, workflow=EXTEND_WORKFLOW, job_id=job["id"],
                             images=[{"name": "reframe.jpg", "image": base64.b64encode(buf.getvalue()).decode()}],
                             maxwidth=size[0], maxheight=size[1], silent=True)
        with Image.open(BytesIO(read_bytes(out["message"]))) as result:
            result = ImageOps.exif_transpose(result).convert("RGB")
        if result.size != tuple(size):
            result = ImageOps.fit(result, tuple(size), Image.LANCZOS)
        grey = grey_share(result, canvas.size, box)
        if grey <= GREY_LEFT or own_grey:
            return paste_back(result, img, canvas.size, box)
        warning(f"[reframe] the new parts came back {grey:.0%} grey (attempt {attempt})")
    raise RuntimeError("the model left the new parts grey twice")


GREY_LEFT = 0.25      # more than this share of the new parts still flat grey: the model didn't fill them
OWN_GREY = 0.5        # at least this share of the picture's own edges is that grey: grey new parts are no sign of it


def edge_grey(img: Image.Image, band: float = 0.06) -> float:
    """The share of the picture's outer band (each side's outer `band`) that is about the canvas's grey."""
    small = img.convert("RGB").resize((64, max(1, round(64 * img.height / img.width))))
    W, H = small.size
    bx, by = max(1, round(W * band)), max(1, round(H * band))
    px = small.load()
    grey = total = 0
    for y in range(H):
        for x in range(W):
            if bx <= x < W - bx and by <= y < H - by:
                continue
            total += 1
            r, g, b = px[x, y]
            grey += abs(r - GREY[0]) < 16 and abs(g - GREY[1]) < 16 and abs(b - GREY[2]) < 16
    return grey / total if total else 0.0


def grey_share(result: Image.Image, canvas_size, box) -> float:
    """The share of the new parts (outside where the original sat on the canvas) that is still the canvas's grey."""
    W, H = result.size
    sx, sy = W / canvas_size[0], H / canvas_size[1]
    x0, y0, x1, y1 = round(box[0] * sx), round(box[1] * sy), round(box[2] * sx), round(box[3] * sy)
    small = result.convert("RGB").resize((max(1, W // 8), max(1, H // 8)))
    inside = (x0 // 8, y0 // 8, x1 // 8, y1 // 8)
    grey = total = 0
    px = small.load()
    for y in range(small.height):
        for x in range(small.width):
            if inside[0] <= x < inside[2] and inside[1] <= y < inside[3]:
                continue
            total += 1
            r, g, b = px[x, y]
            grey += abs(r - GREY[0]) < 8 and abs(g - GREY[1]) < 8 and abs(b - GREY[2]) < 8
    return grey / total if total else 0.0


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
    """The screen's choice for a picture of another shape (Settings > Screens), read fresh each time: auto, crop,
    frame (the default) or adapt."""
    from routes.utils import _load_json_once
    try:
        dest = next((d for d in _load_json_once("publish_destinations", "publish-destinations.json")
                     if d.get("id") == screen_id), {})
    except Exception as e:
        warning(f"[reframe] could not read the screens: {e}")
        return "frame"
    return mode_of(dest.get("reframe")) or "frame"
