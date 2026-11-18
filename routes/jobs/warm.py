"""Warm a GPU before the picture is asked for (owner, 18 Nov: "'getting ready' takes a very long time ... unacceptable").

Warm-ups go to the workflow's interactive endpoint (runpod_id_live, a 2-minute idle timeout). A serverless worker that has gone idle needs a start-up and its model loaded (about 20-30 s, more when RunPod has to
find a GPU) before a job can run. When someone opens the generator or picks a workflow, the page asks for a warm-up:
the workflow's own graph at 64x64, which starts a worker and loads that workflow's models for about 2 US cents. The
worker then stays warm for the endpoint's idle timeout, so the real picture starts at once. One warm-up per
workflow per WARM_EVERY_S, whoever asks."""
import json
import threading
import time

from routes.jobs import runpod_api

WARM_EVERY_S = 120
_last = {}
_lock = threading.Lock()


def _tiny(graph):
    """The workflow's graph shrunk to a 64x64 picture with one step (models still load in full)."""
    g = json.loads(json.dumps(graph))
    for node in g.values():
        ins = node.get("inputs", {})
        ct = node.get("class_type", "")
        if ct in ("EmptyLatentImage", "EmptySD3LatentImage", "ImageScale", "ModelSamplingFlux"):
            if "width" in ins:
                ins["width"], ins["height"] = 64, 64
        if ct in ("KSampler",) and "steps" in ins:
            ins["steps"] = 1
        if ct == "ManualSigmas":
            ins["sigmas"] = "1.0, 0"
        title = node.get("_meta", {}).get("title", "")
        if "{{POSITIVE-PROMPT}}" in title:
            for k in ("text", "prompt"):
                if k in ins:
                    ins[k] = "warm-up"
    return g


def warm(workflow_id):
    """Start a warm-up for a workflow unless one went in the last WARM_EVERY_S. Returns what happened, in words."""
    from routes.utils import _load_json_once, findfile
    catalogue = _load_json_once("workflow", "workflows.json")
    w = next((x for x in catalogue if x.get("id") == workflow_id), None)
    if w is None or workflow_id == "auto":
        w = next((x for x in catalogue if x.get("default") and not x.get("hidden")), None)
    if not w or not w.get("runpod_id"):
        return "no such workflow"
    if w.get("replaced_by"):
        w = next((x for x in catalogue if x.get("id") == w["replaced_by"]), w)
    if "image" in (w.get("input") or []):
        return "not warmed (needs an input image)"
    key = w["id"]
    now = time.time()
    with _lock:
        if now - _last.get(key, 0) < WARM_EVERY_S:
            return "already warm"
        _last[key] = now
    try:
        with open(findfile(w["id"])) as f:
            graph = _tiny(json.load(f))
        runpod_api.submit(w.get("runpod_id_live") or w["runpod_id"], {"input": {"workflow": graph}})
        return "warming"
    except Exception as e:
        with _lock:
            _last.pop(key, None)
        return f"warm-up failed: {e}"
