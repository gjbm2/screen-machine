"""Art tags: one AI pass over one bucket file (docs/ART_TAGS.md). A small JPEG of the picture (a middle frame for a
video) plus its generation prompt go to the ``tag`` task (config/llm.yaml: Gemini only); the answer is cleaned to the
lexicon. Never writes: the worker (or the CLI with --write) does."""
from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import Any, Optional

from routes.art_tags import lexicon, sidecar

# Failure kinds the worker acts on: "transient" backs off and keeps the item; "quota" and "config" pause the queue for
# an hour with one alert; "invalid" (a refusal, an unreadable picture, an answer that can't be used) costs an attempt.
QUOTA_RX = re.compile(r"\b402\b|credit|billing|payment", re.I)
TRANSIENT_RX = re.compile(r"^timeout|^connection|http 429|http 5\d\d|breaker open|rate.?limit|unavailable|overloaded",
                          re.I)
CONFIG_RX = re.compile(r"no model configured|no key for|unknown provider|not in models\.list|couldn't be read|"
                       r"no id in models\.list|http 401|http 403|http 404|rejected the forced tool|lexicon", re.I)
SAFETY_RX = re.compile(r"PROHIBITED_CONTENT|CSAM|BLOCKLIST|safety|content_filter", re.I)


class Undecodable(ValueError):
    pass


def classify(error: str) -> str:
    """An utils.llm error string -> transient | quota | config | invalid."""
    e = error or ""
    if SAFETY_RX.search(e):
        return "invalid"
    if QUOTA_RX.search(e):
        return "quota"
    if TRANSIENT_RX.search(e):
        return "transient"
    if re.search(r"quota|resource.?exhausted", e, re.I):   # a quota word without 429: the account, not the minute
        return "quota"
    if CONFIG_RX.search(e):
        return "config"
    return "invalid"


def _frame(path: Path):
    """A video's middle frame as a PIL image."""
    import cv2
    from PIL import Image
    cap = cv2.VideoCapture(str(path))
    try:
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if n > 1:
            cap.set(cv2.CAP_PROP_POS_FRAMES, n // 2)
        ok, frame = cap.read()
        if not ok or frame is None:
            raise Undecodable(f"no frame could be read from {path.name}")
        return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    finally:
        cap.release()


def image_data_url(path: Path, px: int) -> str:
    """The picture as a JPEG data URL, longest side ``px`` (never the 4K original: draft mode decodes JPEGs at a
    fraction of full size, so a 4K still costs a few ms)."""
    from PIL import Image
    try:
        if path.suffix.lower() in sidecar.VIDEO_EXT:
            im = _frame(path)
        else:
            im = Image.open(path)
            im.draft("RGB", (px, px))
        im = im.convert("RGB")
        im.thumbnail((px, px), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
    except Undecodable:
        raise
    except Exception as e:                            # noqa: BLE001 - a broken file is a failed attempt, not a crash
        raise Undecodable(f"{path.name}: {type(e).__name__}: {e}") from None
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def context_text(meta: dict, bucket: str, chars: int) -> str:
    """The generation prompt(s) as context: the person's own words and the refined prompt, each once, each cut."""
    seen: list[str] = []
    lines = []
    for key, label in (("original_prompt", "The person's prompt"), ("prompt", "The prompt it was generated from"),
                       ("refined_prompt", "The refined prompt")):
        v = meta.get(key)
        if not isinstance(v, str):
            continue
        v = re.sub(r"\s+", " ", v).strip()
        if not v or v.lower() in seen:
            continue
        seen.append(v.lower())
        lines.append(f"{label}: {v[:chars]}")
    if not lines:
        lines.append("No generation prompt is known for this picture.")
    lines.append(f"Saved to: {bucket}")
    return "\n".join(lines)


def tag_file(bucket: str, filename: str, model: Optional[str] = None) -> dict:
    """One AI pass. -> {"ok", "ai" (the cleaned block), "model", "cost_usd", "problems", "error", "kind"}; ``kind`` is
    set on a failure (see classify). ``model``: one model id for calibration (utils.llm call_json(model=)). Never
    raises."""
    out: dict[str, Any] = {"ok": False, "ai": None, "model": None, "cost_usd": 0.0, "problems": [], "error": None,
                           "kind": None}
    try:
        cfg = lexicon.settings()
    except Exception as e:                            # noqa: BLE001 - art_tags.yaml unreadable: pause, not fail
        out.update(error=f"lexicon: {e}"[:300], kind="config")
        return out
    try:
        path = sidecar.media_path(bucket, filename)
        if not path.is_file():
            out.update(error="file gone", kind="gone")
            return out
        try:
            meta = sidecar.read(path)
        except ValueError:
            meta = {}
        url = image_data_url(path, int(cfg["image_px"]))
        text = context_text(meta, bucket, int(cfg["prompt_chars"]))
        import utils.llm as llm
        res = llm.call_json(cfg["task"], system=lexicon.system_prompt(),
                            user=[text, {"type": "image_url", "image_url": {"url": url, "detail": "low"}}],
                            tool=lexicon.tool_schema(), lenient=True, model=model)
        out.update(model=res.model, cost_usd=float(res.cost_usd or 0.0), problems=list(res.problems or []))
        if not res.ok:
            tried = "; ".join(f"{a.get('model')}: {a.get('error')}" for a in (res.attempts or []) if a.get("error"))
            out.update(error=(tried or res.error or "failed")[:300], kind=classify(tried or res.error or ""))
            return out
        out.update(ok=True, ai=lexicon.clean(res.data))
        return out
    except Undecodable as e:
        out.update(error=str(e)[:300], kind="invalid")
        return out
    except ValueError as e:                           # clean(): nothing usable in the answer
        out.update(error=f"unusable answer: {e}"[:300], kind="invalid")
        return out
    except Exception as e:                            # noqa: BLE001
        out.update(error=f"{type(e).__name__}: {e}"[:300], kind="invalid")
        return out
