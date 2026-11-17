"""Round trip: a generation request, through the worker's result and the publisher, to the picture a TV shows.

Only RunPod and the download of a result URL are faked. The workflow catalogue (src/data/workflows.json), the graphs,
routes.generate, the publisher and the bucket are the real ones, writing into a temp output folder.

What a TV shows is worked out as the TV page does (src/pages/DisplayPage/hooks/useFilePolling.ts; room_service's live
art feed picks the same way): the newest of output/<screen>.jpg, .JPG and .mp4. A publish that leaves the picture under
any other name says "done" and changes nothing on the wall, which is what happened on 17 Nov 2026 ("a puppy" for north:
the new worker sent a PNG inline, it was published as north-screen.png).
"""
import base64
import copy
import json
import os
import re
import time
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from routes.jobs import runpod_api, set_tracker
from routes.jobs.store import JobStore
from routes.jobs.tracker import JobTracker

import smconfig

REPO = Path(__file__).resolve().parents[2]


def _a_screen():
    """A real screen of the deployment the tests run against (its id, size and file name are what the pipeline
    resolves a target to); every file it gets here is under the test's temp output folder."""
    destinations = json.loads(Path(smconfig.path("publish-destinations.json")).read_text())
    return next(d["id"] for d in destinations
                if d.get("has_bucket") and not d.get("headless") and not d["id"].startswith("_"))


SCREEN = _a_screen()
OLD = (0, 0, 200)      # the picture on the screen before the request
NEW = (200, 0, 0)      # the picture the worker sends


def _catalogue():
    return json.loads((REPO / "src" / "data" / "workflows.json").read_text())


def _still_workflows():
    """Every workflow a caller can name that makes a still (a retired one runs on its replacement)."""
    return [w["id"] for w in _catalogue() if not w["id"].startswith("wan_")]


def _default_workflow():
    return next(w["id"] for w in _catalogue() if w.get("default"))


def _image_bytes(fmt, colour=NEW, size=(64, 36)):
    buf = BytesIO()
    Image.new("RGB", size, colour).save(buf, format=fmt)
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------- what the TV page reads

def tv_page_extensions():
    """The extensions the TV page asks for, read from its own source, so this test follows the page."""
    src = (REPO / "src" / "pages" / "DisplayPage" / "hooks" / "useFilePolling.ts").read_text()
    return set(re.findall(r"\$\{base\}\.(\w+)`", src))


def tv_shows(output_dir, screen=SCREEN):
    """The file the TV page would show for a screen: the newest of the names it asks for, else None."""
    found = [p for p in (output_dir / f"{screen}.{ext}" for ext in sorted(tv_page_extensions())) if p.exists()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def colour_of(path):
    with Image.open(path) as img:
        return img.convert("RGB").getpixel((img.width // 2, img.height // 2))


def near(a, b, tolerance=12):
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def test_tv_page_still_reads_jpg_and_mp4_only():
    """If the TV page learns a new extension, tv_shows() follows it; if it drops one, the publisher must follow."""
    from routes.publisher import SCREEN_STILL_EXT
    exts = tv_page_extensions()
    assert exts == {"jpg", "JPG", "mp4"}
    assert SCREEN_STILL_EXT.lstrip(".") in exts


# ------------------------------------------------------------------------------------------------------------ fixtures

class FakeRunPod:
    """Stands in for routes.jobs.runpod_api inside routes.generate: one job that finishes with ``final``."""
    RunPodError = runpod_api.RunPodError

    def __init__(self, final):
        # a fresh copy each time, as RunPod's reply is: routes.generate adds to the output it is given
        self.statuses = [{"status": "IN_QUEUE"}, {"status": "IN_PROGRESS"}, copy.deepcopy(final)]
        self.endpoint = None
        self.payload = None

    def submit(self, endpoint_id, payload, timeout=60):
        self.endpoint, self.payload = endpoint_id, payload
        return "rp-roundtrip"

    def status(self, endpoint_id, job_id, timeout=10):
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def cancel(self, endpoint_id, job_id, timeout=10):
        return True

    def billing(self, endpoint_id, status, submitted_at=None, finished_at=None):
        return None


class FakeDownload:
    """requests.get(url, stream=True) for a result URL."""

    def __init__(self, content, content_type):
        self.content = content
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        pass


@pytest.fixture
def house(tmp_path, monkeypatch):
    """A temp output folder with one screen that already shows a picture, and the app pointed at it."""
    import routes.bucketer as bucketer
    import routes.generate as generate
    import routes.generate_handler as handler
    import routes.publisher as publisher

    output = tmp_path / "output"
    (output / SCREEN / "thumbnails").mkdir(parents=True)
    old = output / f"{SCREEN}.jpg"
    old.write_bytes(_image_bytes("JPEG", OLD))
    an_hour_ago = time.time() - 3600
    os.utime(old, (an_hour_ago, an_hour_ago))

    monkeypatch.chdir(tmp_path)                           # routes.generate saves an inline result under ./output
    monkeypatch.setattr(bucketer, "BASE_OUTPUT", output)
    assert tmp_path in bucketer.bucket_path(SCREEN).parents      # nothing here may reach the house's own output/
    monkeypatch.setattr(publisher, "_send_overlay_prompt", lambda *a, **k: None)
    monkeypatch.setattr(generate, "alert", lambda *a, **k: None)
    monkeypatch.setattr(handler, "calculate_generation_cost", lambda *a, **k: 0.02)
    for var in ("OPENAI_API_KEY", "RUNPOD_API_KEY", "RUNPOD_ID"):
        monkeypatch.setenv(var, "x")

    store = JobStore(str(tmp_path / "jobs.db"))
    tracker = JobTracker(store=store, progress_interval=0.1, persist_interval=0.5, cancel_remote=lambda ep, rid: True)
    set_tracker(tracker)
    yield {"output": output, "tracker": tracker, "generate": generate, "publisher": publisher,
           "monkeypatch": monkeypatch}
    set_tracker(None)
    store.close()


def request_picture(house, final, workflow=None, downloads=None, **kwargs):
    """Ask for a picture on the screen as the generator, the scheduler and Alexa do; returns (job, fake RunPod)."""
    generate, tracker = house["generate"], house["tracker"]
    fake = FakeRunPod(final)
    house["monkeypatch"].setattr(generate, "runpod_api", fake)
    if downloads is not None:
        house["monkeypatch"].setattr(house["publisher"].requests, "get",
                                     lambda url, **k: FakeDownload(*downloads))
    job_id = tracker.create(prompt="a puppy", source="alexa", target=SCREEN)["id"]
    try:
        generate.start(prompt="a puppy", workflow=workflow or _default_workflow(), job_id=job_id,
                       publish_destination=SCREEN, silent=True, poll_interval=0.01, **kwargs)
    except ValueError:
        pass
    return tracker.get(job_id), fake


def inline(fmt, filename):
    """The new worker's result when it has no bucket: the image inside the reply."""
    return {"status": "COMPLETED", "output": {"images": [
        {"filename": filename, "type": "base64", "data": base64.b64encode(_image_bytes(fmt)).decode()}]}}


def by_url(url):
    return {"status": "COMPLETED", "output": {"images": [{"filename": url.rsplit("/", 1)[-1], "type": "s3_url",
                                                         "data": url}]}}


def old_worker(url):
    return {"status": "COMPLETED", "output": {"status": "success", "message": url}}


# Every shape a worker's finished job has come back in: (name, RunPod's final status, the download behind a URL)
RESULT_SHAPES = [
    ("old worker, jpg url", old_worker("https://bucket.example/a/out.jpg"), (_image_bytes("JPEG"), "image/jpeg")),
    ("old worker, png url", old_worker("https://bucket.example/a/out.png?X-Amz-Signature=1"),
     (_image_bytes("PNG"), "image/png")),
    ("new worker, png url", by_url("https://bucket.example/a/zimage_00001_.png"), (_image_bytes("PNG"), "image/png")),
    ("new worker, url with no extension", by_url("https://bucket.example/a/result"),
     (_image_bytes("PNG"), "image/png")),
    ("new worker, inline png", inline("PNG", "zimage_00001_.png"), None),
    ("new worker, inline jpg", inline("JPEG", "qwen21_00001_.jpg"), None),
    ("new worker, inline webp", inline("WEBP", "qwen21_00001_.webp"), None),
    ("new worker, inline, no filename", inline("PNG", ""), None),
]


# --------------------------------------------------------------------------------------------------------------- tests

@pytest.mark.parametrize("name,final,downloads", RESULT_SHAPES, ids=[s[0] for s in RESULT_SHAPES])
def test_every_result_shape_reaches_the_tv(house, name, final, downloads):
    job, _ = request_picture(house, final, downloads=downloads)
    assert job["status"] == "completed", job.get("error")

    shown = tv_shows(house["output"])
    assert shown is not None
    assert near(colour_of(shown), NEW), f"the TV still shows the old picture ({shown.name})"

    # the picture is in the screen's library with a thumbnail, and that is what the job names
    stored = house["output"] / SCREEN / job["published_path"]
    assert stored.exists() and near(colour_of(stored), NEW)
    assert (house["output"] / SCREEN / "thumbnails" / f"{stored.name}.jpg").exists()
    # and the screen has no stray file the TV page cannot see
    strays = [p.name for p in house["output"].glob(f"{SCREEN}.*")
              if not p.name.endswith(".json") and p.suffix.lstrip(".") not in tv_page_extensions()]
    assert strays == []


@pytest.mark.parametrize("workflow", _still_workflows())
def test_every_workflow_round_trips(house, workflow):
    """Each workflow in the catalogue loads, takes the prompt, goes to an endpoint and ends on the TV."""
    entry = next(w for w in _catalogue() if w["id"] == workflow)
    runs_as = entry.get("replaced_by") or workflow
    ran = next(w for w in _catalogue() if w["id"] == runs_as)
    images = None
    if "image" in (ran.get("input") or []):
        ref = base64.b64encode(_image_bytes("JPEG", OLD)).decode()
        images = [{"name": f"ref{i}.jpg", "image": ref} for i in range(ran.get("uses_images") or 1)]

    job, fake = request_picture(house, inline("PNG", "out_00001_.png"), workflow=workflow, images=images)
    assert job["status"] == "completed", job.get("error")
    assert job["workflow"] == runs_as
    assert fake.endpoint == ran["runpod_id"]

    graph = json.dumps(fake.payload["input"]["workflow"])
    if "text" in (ran.get("input") or []):
        assert "a puppy" in graph, "the prompt never reached the graph"
    if images:
        assert [i["name"] for i in fake.payload["input"]["images"]] == [i["name"] for i in images]
        assert images[0]["name"] in graph, "the reference image never reached the graph"
    assert "{{" not in re.sub(r'"title": "[^"]*"', "", graph), "a placeholder was left in the graph's values"

    assert near(colour_of(tv_shows(house["output"])), NEW)


@pytest.mark.parametrize("final", [
    {"status": "COMPLETED"},                                    # finished, but RunPod dropped an oversized result
    {"status": "COMPLETED", "output": {"images": []}},
    {"status": "COMPLETED", "output": {"error": "node 9 failed"}},
], ids=["no output", "no images", "worker error"])
def test_a_job_with_no_picture_fails_and_leaves_the_tv_alone(house, final):
    job, _ = request_picture(house, final)
    assert job["status"] == "failed" and job["error"]
    assert near(colour_of(tv_shows(house["output"])), OLD)


def test_no_output_says_what_happened(house):
    job, _ = request_picture(house, {"status": "COMPLETED"})
    assert "RunPod" in job["error"] and "no picture" in job["error"]


# ------------------------------------------------------------------------------------- the generator page (no screen)

@pytest.mark.parametrize("name,final,downloads", RESULT_SHAPES, ids=[s[0] for s in RESULT_SHAPES])
def test_every_result_shape_reaches_the_generator_page(house, name, final, downloads):
    """A request from the generator page with no screen: the picture goes to Recent and the page is given a URL
    it can load."""
    import routes.generate_handler as handler
    import routes.generate_utils as generate_utils
    import routes.result_source as result_source

    generate, mp = house["generate"], house["monkeypatch"]
    mp.setattr(generate, "runpod_api", FakeRunPod(final))
    mp.setattr(handler, "_refine", lambda data, prompt, workflow, *a, **k: (workflow or _default_workflow(), prompt, prompt))
    fetched = []
    if downloads is not None:
        def get(url, **kwargs):
            fetched.append(url)
            return FakeDownload(*downloads)
        mp.setattr(result_source.requests, "get", get)

    out = generate_utils.process_generate_image_request(
        {"prompt": "a puppy", "workflow": _default_workflow(), "batch_id": "abc123_frontend",
         "params": {"poll_interval": 0.01}, "global_params": {"batch_size": 1}})

    assert out["success"] and len(out["images"]) == 1
    url = out["images"][0]["url"]
    assert url.startswith(("https://", "/output/")), f"the page was given something a browser cannot load: {url}"
    if url.startswith("/output/"):
        served = house["output"] / url[len("/output/"):]
        assert served.exists() and near(colour_of(served), NEW)

    assert len(out["recent_files"]) == 1, "nothing was saved to Recent"
    recent = house["output"] / "_recent" / out["recent_files"][0]
    assert near(colour_of(recent), NEW)
    assert (house["output"] / "_recent" / "thumbnails" / f"{recent.name}.jpg").exists()


# ----------------------------------------------------------------------------- the scheduler (most of the house's jobs)

@pytest.fixture
def schedule(house):
    """The scheduler's generate and publish instructions on the real pipeline (in tests the scheduler otherwise gets
    a mock generation service, so nothing behind it runs)."""
    import routes.generate_handler as handler
    import routes.scheduler_handlers as scheduler_handlers
    from routes.scheduler_utils import default_context

    mp = house["monkeypatch"]
    mp.setattr(scheduler_handlers, "get_generation_service", lambda: handler.handle_image_generation)
    mp.setattr(scheduler_handlers, "_generation_refused", lambda *a, **k: False)
    mp.setattr(handler, "_refine", lambda data, prompt, workflow, *a, **k: (workflow or _default_workflow(), prompt, prompt))
    context = default_context()
    context["vars"] = {}

    def run(action, instruction, final=None, downloads=None):
        from datetime import datetime
        if final is not None:
            mp.setattr(house["generate"], "runpod_api", FakeRunPod(final))
        if downloads is not None:
            mp.setattr(house["publisher"].requests, "get", lambda url, **k: FakeDownload(*downloads))
        handle = scheduler_handlers.handle_generate if action == "generate" else scheduler_handlers.handle_publish
        handle({"action": action, **instruction}, context, datetime.now(), [], SCREEN)
        return context

    return run


SCHEDULER_SHAPES = [s for s in RESULT_SHAPES if s[0] in ("old worker, png url", "new worker, png url",
                                                         "new worker, inline png")]


@pytest.mark.parametrize("name,final,downloads", SCHEDULER_SHAPES, ids=[s[0] for s in SCHEDULER_SHAPES])
def test_a_scheduled_generate_reaches_the_tv(house, schedule, name, final, downloads):
    context = schedule("generate", {"input": {"prompt": "a puppy"}, "workflow": _default_workflow(),
                                    "additionalProperties": {"poll_interval": 0.01}, "output_var": "img"},
                       final, downloads)
    assert near(colour_of(tv_shows(house["output"])), NEW)
    assert context["vars"].get("img"), "the schedule was not told which picture it made"


@pytest.mark.parametrize("name,final,downloads", SCHEDULER_SHAPES, ids=[s[0] for s in SCHEDULER_SHAPES])
def test_a_scheduled_generate_without_publish_then_publish(house, schedule, name, final, downloads):
    """A schedule that makes pictures for the library, then publishes one of them."""
    context = schedule("generate", {"input": {"prompt": "a puppy"}, "workflow": _default_workflow(),
                                    "additionalProperties": {"poll_interval": 0.01}, "publish": False,
                                    "output_var": "img"}, final, downloads)
    assert near(colour_of(tv_shows(house["output"])), OLD), "publish: false still changed the TV"
    stored = Path(context["vars"]["img"])
    assert stored.parent == house["output"] / SCREEN and near(colour_of(stored), NEW)

    schedule("publish", {"source": context["vars"]["img"], "silent": True})
    assert near(colour_of(tv_shows(house["output"])), NEW)


# ------------------------------------------------------------------------------- publishing a file already in a library

@pytest.mark.parametrize("fmt,ext", [("PNG", ".png"), ("WEBP", ".webp"), ("JPEG", ".jpeg"), ("JPEG", ".jpg")])
def test_publishing_a_library_picture_of_any_format_reaches_the_tv(house, fmt, ext):
    """Dragging a library picture onto Published (the 17 Nov puppy is a PNG in north's library)."""
    stored = house["output"] / SCREEN / f"20261117-231854-66a72208{ext}"
    stored.write_bytes(_image_bytes(fmt))
    result = house["publisher"].publish_to_destination(source=stored, publish_destination_id=SCREEN,
                                                       metadata={"type": "image", "prompt": "a puppy"}, silent=True)
    assert result["success"], result
    assert near(colour_of(tv_shows(house["output"])), NEW)
    assert house["publisher"].get_published_info(SCREEN)["published"] == stored.name


def test_publishing_from_another_library_reaches_the_tv(house):
    other = house["output"] / "_recent"
    other.mkdir(exist_ok=True)
    source = other / "20261117-120000-aaaaaaaa.png"
    source.write_bytes(_image_bytes("PNG"))
    result = house["publisher"].publish_to_destination(source=source, publish_destination_id=SCREEN,
                                                       metadata={"type": "image"}, silent=True)
    assert result["success"], result
    assert near(colour_of(tv_shows(house["output"])), NEW)


def test_publishing_a_video_reaches_the_tv(house):
    house["monkeypatch"].setattr(house["publisher"], "_after_placing", lambda dst: None)   # no ffmpeg re-encode here
    stored = house["output"] / SCREEN / "20261117-231854-bbbbbbbb.mp4"
    stored.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42mp41\x00\x00\x00\x00moov")
    result = house["publisher"].publish_to_destination(source=stored, publish_destination_id=SCREEN,
                                                       metadata={"type": "video"}, silent=True)
    assert result["success"], result
    shown = tv_shows(house["output"])
    assert shown.suffix == ".mp4" and shown.read_bytes() == stored.read_bytes()
