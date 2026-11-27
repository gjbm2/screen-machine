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
A day's pictures (the cloud's set, routes/scheduler/cloud.json) have theirs made ahead instead (commission, from a
scheduler `animate` with `to`): each is a cached object kept with B and listed on both pictures, and a publish that
asks for a transition (routes/publisher.py: the screen knows what it shows, A, and what it is putting up, B) plays the
one for that pair if there is one (cached), else cross-fades. A picture is known by its origin ("bucket/name", kept
on its copies), so a copy on a screen finds the transitions made for the original.

At publish time, the file goes in the screen's bucket under transitions/ (never an item of the bucket, never in a picture's clips that
play on other occasions) and is named in B's metadata (`transition`), which the TV page reads from the screen's
sidecar when B lands: it plays the transition once over A, swaps to B under it and fades it away. Both pictures'
sidecars list it (`transitions`), and B's publish history entry carries it, so a day can be replayed with its
transitions. Anything failing or taking longer than DEADLINE_S: B goes up with the plain cross-fade."""
import base64
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from utils.logger import info, warning

WORKFLOW = "h3-fl2v.json"
DRAW_S = 4.0                # drawn; H3 draws 17k + 5 frames, so 90 (3.75 s)
PLAY_S, RAMP_S = 15.0, 3.5  # on screen: eased in from A and out onto B
DEADLINE_S = 540            # the new picture waits no longer than this for its transition (a cold GPU included)
AHEAD_S = 900               # a transition made ahead (commission) is given up after this
FOLDER = "transitions"
WRITER = "refiner-transition.txt"
SCHEMA_FILE = "refiner-transition.schema.json"   # the answer's fields, next to the brief: both read on every call


def schema() -> dict:
    from routes.utils import findfile
    return json.loads(Path(findfile(SCHEMA_FILE)).read_text("utf-8"))


# What the video model does with a vague prompt: paint or light bursting out of the centre (owner, 24 Nov: "a bit much
# 'expanding paint star'"). A prompt with these words goes back to the writer once.
BURST = re.compile(r"\b(burst|explod|radiat|star ?burst|rays?\b|bloom|splash|shatter)", re.I)


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
    """The writer's answer (FIELDS) for a transition from picture a to picture b (JPEG/PNG bytes). A prompt that
    still reads like a burst goes back once with a note."""
    import routes.openai
    ask = {"A": _about(meta_a), "B": _about(meta_b), "pictures": "Two pictures are attached: A first, then B."}
    images = [{"name": "A.jpg", "image": _b64(a)}, {"name": "B.jpg", "image": _b64(b)}]
    out = {}
    for _ in range(2):
        out = routes.openai.openai_prompt(user_prompt=json.dumps(ask), system_prompt=WRITER, schema=schema(),
                                          images=images, verbose=False) or {}
        hit = BURST.search(out.get("full_prompt") or "")
        if not hit:
            break
        info(f"[transition] the writer's prompt has {hit.group(0)!r}: asking again")
        ask["note"] = (f"Your last full_prompt said {hit.group(0)!r}, which the video model turns into a burst from the "
                       f"centre. Write it again as directional movement, without that word: {out.get('full_prompt')}")
    return out


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
    if upscale:          # finished at the new picture's size (as it is shown, at most 4K), as a screen clip is
        from argparse import Namespace
        from routes.generate import image_size, video_finish
        w, h = image_size({"image": _b64(b)}) or (3840, 2160)
        k = min(1.0, 3840 / max(w, h))
        video_finish(graph, Namespace(width=None, height=None, images=[{"image": _b64(b)}]), (round(w * k), round(h * k)))
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
                        str(RAMP_S), "0"], check=True, capture_output=True, text=True,
                       # an ease under way gets its time (25 Nov: the deadline killed one at 04:01:00 that the GPU
                       # finished at 04:01:35); the deadline is for a drawing that never comes
                       timeout=max(300, deadline - time.time()), cwd=Path(__file__).resolve().parent.parent)
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
    # its own record, for the review page (records): the writer's working, cost and time, and later its score
    try:
        (folder / (transition["file"] + ".json")).write_text(json.dumps({**entry, "screen": screen}, indent=2), "utf-8")
    except Exception as e:
        warning(f"[transition] could not write its record: {e}")


def origin(path: Path, meta: dict | None = None) -> str:
    """What a picture is known by: the origin its sidecar carries (a copy's original), else "bucket/name"."""
    path = Path(path)
    got = (meta if meta is not None else _sidecar(path)).get("origin")
    return got if isinstance(got, str) and got else f"{path.parent.name}/{path.name}"


def _origin_file(o: str) -> Path:
    from routes.bucketer import bucket_path
    bucket, _, name = o.partition("/")
    return bucket_path(bucket) / name


def _made(o_from: str, o_to: str) -> dict | None:
    """The transition already made from one picture to the other (by origin), its file still there."""
    to = _origin_file(o_to)
    for t in _sidecar(to).get("transitions") or []:
        if t.get("from") == o_from and t.get("to") == o_to and (to.parent / str(t.get("file"))).exists():
            return t
    return None


def cached(screen: str, b_path: Path, meta_b: dict) -> dict | None:
    """The transition to play as `screen` changes from what it shows to the picture b_path (its metadata meta_b), as
    B's `transition` for the TV page (stamped with the publish time), or None: the plain cross-fade."""
    from routes.bucketer import bucket_path
    from routes.clips import published_still
    showing = published_still(screen)
    if not showing:
        return None
    t = _made(origin(bucket_path(screen) / showing), origin(b_path, meta_b))
    return {**t, "at": round(time.time(), 1)} if t else None


def commission(a: str | Path, b: str | Path) -> dict | None:
    """Make the transition from picture a to picture b (files in buckets) ahead of time and keep it with b
    (transitions/ in b's bucket), listed on both pictures' sidecars: a publish from a to b plays it (cached). The one
    already made for the pair if there is one; None if it couldn't be made (logged for the review page)."""
    import uuid
    a, b = Path(a), Path(b)
    oa, ob = origin(a), origin(b)
    if (t := _made(oa, ob)):
        info(f"[transition] {oa} -> {ob} already made")
        return t
    t0 = time.time()
    deadline = t0 + AHEAD_S
    job_id = f"transition-{uuid.uuid4().hex[:8]}"
    try:
        meta_a, meta_b = _sidecar(a), _sidecar(b)
        words = write(a.read_bytes(), b.read_bytes(), meta_a, meta_b)
        prompt = (words.get("full_prompt") or "").strip()
        if not prompt:
            raise RuntimeError("the writer gave no prompt")
        drawn, usd = draw(a.read_bytes(), b.read_bytes(), prompt, job_id, deadline)
        folder = b.parent / FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y%m%d-%H%M%S") + f"-{job_id[-8:]}.mp4"
        ease(drawn, folder / name, deadline)
        entry = {"file": f"{FOLDER}/{name}", "url": f"/output/{b.parent.name}/{FOLDER}/{name}", "from": oa, "to": ob,
                 "connection": words.get("connection"), "device": words.get("device"), "idea": words.get("idea"),
                 "prompt": prompt, "writer": {k: words.get(k) for k in ("link_a", "link_b", "motion", "rest")},
                 "at": round(time.time(), 1), "play_s": PLAY_S, "cost_usd": usd, "made_s": round(time.time() - t0, 1)}
        for path in (a, b):
            _note(path, entry)
        (folder / (name + ".json")).write_text(json.dumps({**entry, "screen": b.parent.name}, indent=2), "utf-8")
        info(f"[transition] {oa} -> {ob} made in {entry['made_s']} s: {entry['idea']}")
        return entry
    except Exception as e:
        warning(f"[transition] {oa} -> {ob}: not made ({e}); that change cross-fades")
        _failed(b.parent.name, {"at": round(time.time(), 1), "error": str(e)[:300], "made_s": round(time.time() - t0, 1),
                                "from": oa, "to": ob})
        return None


_AT_ONCE = threading.BoundedSemaphore(2)   # transitions drawn at once (the video endpoint has 3 workers)
_PENDING_LOCK = threading.Lock()
PENDING = "pending.json"                   # in B's transitions/: commissioned, not yet made
TRIES = 3                                  # a commission cut short by restarts is made again up to this many times


def _edit_pending(b: Path, change) -> None:
    f = b.parent / FOLDER / PENDING
    with _PENDING_LOCK:
        try:
            rows = json.loads(f.read_text("utf-8")) if f.exists() else []
        except Exception:
            rows = []
        rows = change(rows)
        try:
            if rows:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps(rows, indent=2), "utf-8")
            elif f.exists():
                f.unlink()
        except Exception as e:
            warning(f"[transition] could not keep {f}: {e}")


def commission_later(a: str | Path, b: str | Path, tries: int = 0) -> None:
    """commission in the background, two at a time (made in line, each held the schedule's loop for minutes). It is
    kept in B's transitions/pending.json until it has run, so one cut short by a Flask restart (its thread goes with
    the process) is made again when Flask starts (resume_pending). 27 Nov: a restart at 07:57 dropped 7 of the day's 9
    and the evening's replay cross-faded."""
    a, b = str(a), str(b)
    same = lambda r: (r.get("a"), r.get("b")) == (a, b)
    _edit_pending(Path(b), lambda rows: [r for r in rows if not same(r)]
                  + [{"a": a, "b": b, "at": round(time.time(), 1), "tries": tries + 1}])

    def run():
        try:
            with _AT_ONCE:
                made = commission(a, b)
            info(f"[animate] transition {os.path.basename(a)} -> {os.path.basename(b)}: "
                 + ((made.get("idea") or "made") if made else "not made (that change cross-fades)"))
        finally:
            _edit_pending(Path(b), lambda rows: [r for r in rows if not same(r)])
    threading.Thread(target=run, name=f"transition-{Path(b).name[:16]}", daemon=True).start()


def resume_pending() -> int:
    """At Flask's start: each transition commissioned and not made when the last process stopped, made again (up to
    TRIES times in all; one already made is found and kept). Returns how many were resumed."""
    from routes.bucketer import BASE_OUTPUT
    n = 0
    for f in sorted(Path(BASE_OUTPUT).glob(f"*/{FOLDER}/{PENDING}")):
        try:
            rows = json.loads(f.read_text("utf-8"))
        except Exception:
            rows = []
        f.unlink(missing_ok=True)
        for r in rows:
            a, b, tries = r.get("a"), r.get("b"), int(r.get("tries") or 0)
            if not a or not b or not Path(a).exists() or not Path(b).exists():
                continue
            if tries >= TRIES:
                warning(f"[transition] {origin(Path(a))} -> {origin(Path(b))}: given up after {tries} tries")
                _failed(Path(b).parent.name, {"at": round(time.time(), 1), "error": f"cut short {tries} times",
                                              "from": origin(Path(a)), "to": origin(Path(b))})
                continue
            commission_later(a, b, tries)
            n += 1
    if n:
        info(f"[transition] {n} commissioned before the restart and not made: making them now")
    return n


def records(screen: str) -> dict:
    """The screen's transitions for the review page, newest first (each with its pictures' URLs), and the ones that
    weren't made."""
    from routes.bucketer import bucket_path
    folder = bucket_path(screen) / FOLDER
    out = []
    for sc in sorted(folder.glob("*.mp4.json"), reverse=True):
        try:
            r = json.loads(sc.read_text("utf-8"))
        except Exception:
            continue
        for end in ("from", "to"):
            name = r.get(end)
            if name:     # a name in this bucket, or an origin ("bucket/name") for one made ahead
                bucket, _, file = name.rpartition("/")
                bucket = bucket or screen
                r[end + "_url"] = f"/output/{bucket}/{file}"
                r[end + "_thumb"] = f"/output/{bucket}/thumbnails/{file}.jpg"
        out.append({**r, "name": sc.name[:-len(".json")], "screen": screen})
    failed = []
    try:
        failed = [json.loads(l) for l in (folder / "failed.jsonl").read_text("utf-8").splitlines() if l.strip()]
    except Exception:
        pass
    return {"transitions": out, "failed": failed[::-1]}


def set_score(screen: str, name: str, score: int | None, note: str | None) -> dict:
    """The review page's score (1-5, or None to clear) and note on one transition."""
    from routes.bucketer import bucket_path
    if "/" in name or not name.endswith(".mp4"):
        raise FileNotFoundError(name)
    sc = bucket_path(screen) / FOLDER / (name + ".json")
    r = json.loads(sc.read_text("utf-8"))
    if score is not None and not (1 <= int(score) <= 5):
        raise ValueError("a score is 1 to 5")
    r["score"] = int(score) if score is not None else None
    if note is not None:
        r["note"] = note.strip()[:2000]
    r["scored_at"] = round(time.time(), 1)
    sc.write_text(json.dumps(r, indent=2), "utf-8")
    return r


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
               "connection": words.get("connection"), "device": words.get("device"), "idea": words.get("idea"), "prompt": prompt,
               "writer": {k: words.get(k) for k in ("link_a", "link_b", "motion", "rest")}, "at": round(time.time(), 1), "play_s": PLAY_S,
               "cost_usd": usd, "made_s": round(time.time() - t0, 1)}
        info(f"[transition] {screen}: {showing} -> new picture in {out['made_s']} s: {out['idea']}")
        return out
    except Exception as e:
        warning(f"[transition] {screen}: none this time ({e}); the new picture cross-fades")
        _failed(screen, {"at": round(time.time(), 1), "error": str(e)[:300], "made_s": round(time.time() - t0, 1)})
        return None


def _failed(screen: str, row: dict) -> None:
    """A transition that wasn't made, for the review page (transitions/failed.jsonl)."""
    try:
        from routes.bucketer import bucket_path
        folder = bucket_path(screen) / FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "failed.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass
