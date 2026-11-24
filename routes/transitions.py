"""A screen's transition from one picture to the next (owner, 24 Nov): instead of a plain cross-fade, a short video
carries the picture on the screen (A) into the new one (B), contextualised to the pair in an appropriate creative way
("the viewer should be mildly entertained by the effect, and pleased to have seen it").

A scheduler `generate` with "transition": true (a param for the scheduler; pictures published by hand just cross-fade)
makes B, then here, before B goes up:
  1. the writer (routes/data/refiner-transition.txt, a vision prompt on both pictures and what they were made from)
     writes the video prompt;
  2. h3-fl2v draws ~4 s from A to B at its native size (both pictures at the drawn size; routes/generate.at_size),
     AI-upscaled x2 and RIFE x3 on the worker;
  3. the ease (routes/clip_ease.py, on the renderer's GPU when it can) slows it to PLAY_S, rising from stillness on A
     and coming to rest on B.
The file goes in the screen's bucket under transitions/ (never an item of the bucket, never in a picture's clips that
play on other occasions) and is named in B's metadata (`transition`), which the TV page reads from the screen's
sidecar when B lands: it plays the transition once over A, swaps to B under it and fades it away. Both pictures'
sidecars list it (`transitions`), and B's publish history entry carries it, so a day can be replayed with its
transitions. Anything failing or taking longer than DEADLINE_S: B goes up with the plain cross-fade."""
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from utils.logger import info, warning

WORKFLOW = "h3-fl2v.json"
DRAW_S = 4.0                # drawn; H3 draws 17k + 5 frames, so 90 (3.75 s)
PLAY_S, RAMP_S = 15.0, 3.5  # on screen: eased in from A and out onto B
DEADLINE_S = 540            # the new picture waits no longer than this for its transition (a cold GPU included)
FOLDER = "transitions"
WRITER = "refiner-transition.txt"
SCHEMA = {"name": "transition", "description": "The transition from picture A to picture B",
          "parameters": {"type": "object", "properties": {
              "idea": {"type": "string", "description": "The link and the manner, in a few words."},
              "full_prompt": {"type": "string", "description": "One or two sentences for the video model."}},
              "required": ["idea", "full_prompt"]}}


def _frames(seconds):
    return 17 * max(1, round((24 * seconds - 5) / 17)) + 5


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _about(meta: dict) -> dict:
    return {"prompt": str(meta.get("prompt") or "")[:600], "tags": meta.get("tags") or []}


def _sidecar(path: Path) -> dict:
    try:
        return json.loads(path.with_suffix(path.suffix + ".json").read_text("utf-8"))
    except Exception:
        return {}


def write(a: bytes, b: bytes, meta_a: dict, meta_b: dict) -> dict:
    """The writer's {idea, full_prompt} for a transition from picture a to picture b (JPEG/PNG bytes)."""
    import routes.openai
    return routes.openai.openai_prompt(
        user_prompt=json.dumps({"A": _about(meta_a), "B": _about(meta_b),
                                "pictures": "Two pictures are attached: A first, then B."}),
        system_prompt=WRITER, schema=SCHEMA,
        images=[{"name": "A.jpg", "image": _b64(a)}, {"name": "B.jpg", "image": _b64(b)}], verbose=False) or {}


def draw(a: bytes, b: bytes, prompt: str, job_id: str, deadline: float, upscale: bool = True) -> tuple[str, float | None]:
    """The worker's from-to video (a local file) and its bill in USD (None when unknown)."""
    from routes.generate import at_size, worker_output
    from routes.jobs import runpod_api
    from routes.utils import _load_json_once
    entry = next(w for w in _load_json_once("workflow", "workflows.json") if w["id"] == WORKFLOW)
    from routes.utils import findfile
    graph = json.loads(Path(findfile(WORKFLOW)).read_text())
    node = next(n for n in graph.values() if {"width", "height", "length"} <= set(n.get("inputs", {})))
    node["inputs"]["prompt"] = prompt
    node["inputs"]["length"] = _frames(DRAW_S)
    for n in graph.values():
        if "noise_seed" in n.get("inputs", {}):
            n["inputs"]["noise_seed"] = int(time.time()) % 100_000_000
    if not upscale:      # {{UPSCALE_FLAG}} nodes out, the in-betweens from the decoded frames
        ups = [k for k, n in graph.items() if "{{UPSCALE_FLAG}}" in n.get("_meta", {}).get("title", "")]
        decode = next(k for k, n in graph.items() if n.get("class_type") == "VAEDecode")
        for k in ups:
            del graph[k]
        for n in graph.values():
            for key, v in n.get("inputs", {}).items():
                if isinstance(v, list) and len(v) == 2 and v[0] in ups:
                    n["inputs"][key] = [decode, 0]
    size = (int(node["inputs"]["width"]), int(node["inputs"]["height"]))
    images = [at_size({"name": "input.png", "image": _b64(a)}, size),
              at_size({"name": "input2.png", "image": _b64(b)}, size)]
    endpoint = entry["runpod_id"]
    submitted = time.time()
    job = runpod_api.submit(endpoint, {"input": {"workflow": graph, "images": images}})
    info(f"[transition] drawing on {endpoint} ({job}): {prompt}")
    while True:
        if time.time() > deadline:
            runpod_api.cancel(endpoint, job, timeout=10)
            raise TimeoutError("the transition took too long to draw")
        time.sleep(3)
        try:
            state = runpod_api.status(endpoint, job)
        except Exception:
            continue
        if state.get("status") == "COMPLETED":
            out = worker_output(state.get("output"), job_id)
            if out.get("status") != "success":
                raise RuntimeError(out.get("message"))
            usd = None
            try:
                usd = (runpod_api.billing(endpoint, state, submitted) or {}).get("usd")
            except Exception:
                pass
            return out["message"], usd
        if state.get("status") in ("FAILED", "TIMED_OUT", "CANCELLED"):
            raise RuntimeError(f"the transition's job {state.get('status')}: {str(state.get('error'))[:200]}")


def ease(source: str, dst: Path, deadline: float) -> None:
    """The drawn transition slowed to PLAY_S, eased in and out, into dst (in its own process, as a clip's ease)."""
    from routes.result_source import read_bytes
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in.mp4")
        Path(src).write_bytes(read_bytes(source))
        part = str(dst) + ".part.mp4"
        subprocess.run(["nice", "-n", "10", sys.executable, "-m", "routes.clip_ease", src, part, str(PLAY_S),
                        str(RAMP_S), "0", ""], check=True, capture_output=True, text=True,
                       timeout=max(30, deadline - time.time()), cwd=Path(__file__).resolve().parent.parent)
        tag = {"clip": {"eased": True, "transition": True, "play_s": PLAY_S, "returns": False}}
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", part, "-c", "copy", "-metadata",
                        "comment=" + json.dumps(tag), "-movflags", "+faststart", str(dst)], check=True, timeout=120)
        os.remove(part)


def _note(path: Path, entry: dict) -> None:
    """List a transition on a picture's sidecar (`transitions`), keeping the rest."""
    sc = path.with_suffix(path.suffix + ".json")
    try:
        meta = json.loads(sc.read_text("utf-8")) if sc.exists() else {}
        meta["transitions"] = [t for t in meta.get("transitions") or [] if t.get("file") != entry["file"]] + [entry]
        sc.write_text(json.dumps(meta, indent=2, ensure_ascii=False), "utf-8")
    except Exception as e:
        warning(f"[transition] could not note it on {path.name}: {e}")


def note_published(screen: str, to_name: str, transition: dict) -> None:
    """After B is filed: both pictures' sidecars list the transition (B under its name in the bucket)."""
    from routes.bucketer import bucket_path
    folder = bucket_path(screen)
    entry = {**transition, "to": to_name}
    for name in (transition.get("from"), to_name):
        if name and (folder / name).exists():
            _note(folder / name, entry)


def before_publish(screen: str, source: str, job_id: str, tracker=None, meta_b: dict | None = None,
                   upscale: bool = True) -> dict | None:
    """Make the transition from what `screen` shows to the new picture `source` (a path or URL): its metadata for B
    ({file, url, from, idea, prompt, at, cost_usd, play_s}), or None (nothing on the screen, or it failed: B
    cross-fades as before). Never raises."""
    from routes.bucketer import bucket_path
    from routes.clips import published_still
    from routes.result_source import read_bytes
    t0 = time.time()
    deadline = t0 + DEADLINE_S
    try:
        showing = published_still(screen)
        a_path = Path("output") / f"{screen}.jpg"           # the picture as fitted to the screen, as it is shown
        if not showing or not a_path.exists():
            info(f"[transition] {screen} shows no picture to start from: none")
            return None
        if tracker:
            stages = [*((tracker.get(job_id) or {}).get("stages") or []), "Transition"]
            tracker.update(job_id, message="Making the transition", stages=stages, stage_index=len(stages) - 1)
        a, b = a_path.read_bytes(), read_bytes(source)
        meta_a = _sidecar(bucket_path(screen) / showing)
        words = write(a, b, meta_a, meta_b or {})
        prompt = (words.get("full_prompt") or "").strip()
        if not prompt:
            raise RuntimeError("the writer gave no prompt")
        drawn, usd = draw(a, b, prompt, job_id, deadline, upscale)
        folder = bucket_path(screen) / FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y%m%d-%H%M%S") + f"-{job_id[-8:]}.mp4"
        ease(drawn, folder / name, deadline)
        out = {"file": f"{FOLDER}/{name}", "url": f"/output/{screen}/{FOLDER}/{name}", "from": showing,
               "idea": words.get("idea"), "prompt": prompt, "at": round(time.time(), 1), "play_s": PLAY_S,
               "cost_usd": usd, "made_s": round(time.time() - t0, 1)}
        info(f"[transition] {screen}: {showing} -> new picture in {out['made_s']} s: {out['idea']}")
        return out
    except Exception as e:
        warning(f"[transition] {screen}: none this time ({e}); the new picture cross-fades")
        return None
