"""Lookups the job lifecycle needs from the app's config: a workflow's kind and stages, and a publish
destination's display name."""
from routes.utils import _load_json_once

VIDEO_PARAMS = {"video_length", "interpolate_frames", "framerate"}


def workflow_meta(workflow_id):
    try:
        return next((w for w in _load_json_once("workflow", "workflows.json")
                     if w.get("id") == workflow_id), {})
    except Exception:
        return {}


def workflow_kind(meta):
    """'video' for workflows that render video (they take frame/length params or say so in their id
    or name), else 'image'."""
    meta = meta or {}
    params = {p.get("id") for p in meta.get("params", []) if isinstance(p, dict)}
    text = f"{meta.get('id', '')} {meta.get('name', '')}".lower()
    return "video" if params & VIDEO_PARAMS or "video" in text else "image"


def target_name(target):
    if not target:
        return None
    try:
        dest = next((d for d in _load_json_once("destination", "publish-destinations.json")
                     if d.get("id") == target), None)
    except Exception:
        dest = None
    return (dest or {}).get("name") or target
