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
    import routes.reframe as reframe

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
    # a picture of another shape: each test sets the screen's choice (Settings > Screens), and the vision model that
    # finds a crop's subject is never asked (a test that crops gives its own answer)
    policy = {"mode": "frame"}
    monkeypatch.setattr(reframe, "screen_policy", lambda screen_id: policy["mode"])

    def no_vision(img):
        raise RuntimeError("no vision model in tests")
    monkeypatch.setattr(reframe, "_vision_focus", no_vision)

    store = JobStore(str(tmp_path / "jobs.db"))
    tracker = JobTracker(store=store, progress_interval=0.1, persist_interval=0.5, cancel_remote=lambda ep, rid: True)
    set_tracker(tracker)
    yield {"output": output, "tracker": tracker, "generate": generate, "publisher": publisher,
           "monkeypatch": monkeypatch, "policy": policy, "reframe": reframe}
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
    # request_picture asks as Alexa: someone waiting, so the workflow's interactive endpoint when it has one
    assert fake.endpoint == (ran.get("runpod_id_live") or ran["runpod_id"])

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


# ---------------------------------------------------------------------------- a picture keeps its shape (owner, 19 Nov)
# An uploaded portrait photo, adapted, came back cut to the landscape screen: the new graphs' {{FINISH}} scaled to the
# screen's size with a centre crop. The fake worker below makes its picture at the size the graph's {{FINISH}} node was
# given, as ComfyUI's ImageScale does, so what reaches the TV or Recent is the shape the app asked for.

def _destination(screen=SCREEN):
    destinations = json.loads(Path(smconfig.path("publish-destinations.json")).read_text())
    return next(d for d in destinations if d["id"] == screen)


def _takes_images():
    """Live still workflows whose picture is made from an uploaded one (it sets the picture's shape)."""
    return [w["id"] for w in _catalogue()
            if not w.get("replaced_by") and not w["id"].startswith("wan_") and "image" in (w.get("input") or [])]


def _text_only():
    return [w["id"] for w in _catalogue()
            if not w.get("replaced_by") and not w["id"].startswith("wan_") and "image" not in (w.get("input") or [])]


def _finish_node(payload):
    return next((n for n in payload["input"]["workflow"].values()
                 if "{{FINISH}}" in (n.get("_meta") or {}).get("title", "")), None)


class FinishingRunPod(FakeRunPod):
    """The worker: its picture is the size the graph's {{FINISH}} node was given (ImageScale with a centre crop)."""

    def __init__(self):
        super().__init__({"status": "COMPLETED"})

    def submit(self, endpoint_id, payload, timeout=60):
        node = _finish_node(payload)
        assert node is not None, "the graph has no {{FINISH}} node"
        size = (int(node["inputs"]["width"]), int(node["inputs"]["height"]))
        self.statuses[-1] = inline_sized(size)
        self.images = copy.deepcopy(payload["input"].get("images"))    # routes.generate strips them after the job
        return super().submit(endpoint_id, payload, timeout)


def inline_sized(size):
    return {"status": "COMPLETED", "output": {"images": [
        {"filename": "out_00001_.png", "type": "base64",
         "data": base64.b64encode(_image_bytes("PNG", size=size)).decode()}]}}


def upload(size, exif_orientation=None):
    """A photo as the generator page's upload reaches routes.generate (routes.utils.encode_image_uploads)."""
    from werkzeug.datastructures import FileStorage
    from routes.utils import encode_image_uploads
    img = Image.new("RGB", size, OLD)
    buf = BytesIO()
    if exif_orientation:
        exif = img.getexif()
        exif[0x0112] = exif_orientation
        img.save(buf, format="JPEG", exif=exif.tobytes())
    else:
        img.save(buf, format="JPEG")
    buf.seek(0)
    return encode_image_uploads([FileStorage(stream=buf, filename="photo.jpg")])


def shape(size):
    return size[0] / size[1]


def same_shape(a, b, tolerance=0.02):
    return abs(shape(a) / shape(b) - 1) <= tolerance


def size_of(path):
    with Image.open(path) as img:
        return img.size


PHOTOS = {"portrait": (556, 1024), "landscape 4:3": (1024, 768), "square": (800, 800), "tall": (400, 1200)}


@pytest.mark.parametrize("photo", PHOTOS, ids=list(PHOTOS))
@pytest.mark.parametrize("workflow", _takes_images())
def test_an_uploaded_photo_keeps_its_shape_on_the_tv(house, workflow, photo):
    ran = next(w for w in _catalogue() if w["id"] == workflow)
    images = upload(PHOTOS[photo]) * (ran.get("uses_images") or 1)
    fake = FinishingRunPod()
    house["monkeypatch"].setattr(house["generate"], "runpod_api", fake)
    job_id = house["tracker"].create(prompt="make it snow", source="generator", target=SCREEN)["id"]
    house["generate"].start(prompt="make it snow", workflow=workflow, images=images, job_id=job_id,
                            publish_destination=SCREEN, silent=True, poll_interval=0.01)

    shown = size_of(tv_shows(house["output"]))
    screen = (_destination()["maxwidth"], _destination()["maxheight"])
    assert same_shape(shown, PHOTOS[photo]), f"a {photo} photo came back {shown[0]}x{shown[1]}"
    assert shown[0] <= screen[0] and shown[1] <= screen[1], "bigger than the screen"
    assert shown[0] == screen[0] or shown[1] == screen[1], "smaller than the screen allows"


@pytest.mark.parametrize("photo", PHOTOS, ids=list(PHOTOS))
@pytest.mark.parametrize("workflow", _takes_images())
def test_an_uploaded_photo_keeps_its_shape_in_recent(house, workflow, photo):
    """The generator page with no screen (the owner's case on 19 Nov: a portrait photo, 'Adapt', into Recent): the
    picture keeps its shape at the full size, turned to suit it."""
    import routes.generate_handler as handler
    import routes.generate_utils as generate_utils

    ran = next(w for w in _catalogue() if w["id"] == workflow)
    mp = house["monkeypatch"]
    mp.setattr(house["generate"], "runpod_api", FinishingRunPod())
    mp.setattr(handler, "_refine", lambda data, prompt, workflow, *a, **k: (workflow, prompt, prompt))
    out = generate_utils.process_generate_image_request(
        {"prompt": "make it snow", "workflow": workflow, "batch_id": "abc123_frontend",
         "params": {"poll_interval": 0.01}, "global_params": {"batch_size": 1}},
        uploaded_images=upload(PHOTOS[photo]) * (ran.get("uses_images") or 1))

    made = size_of(house["output"] / "_recent" / out["recent_files"][0])
    assert same_shape(made, PHOTOS[photo]), f"a {photo} photo came back {made[0]}x{made[1]}"
    finish = _finish_node({"input": {"workflow": json.loads((REPO / "routes" / "workflows" / workflow).read_text())}})
    budget = sorted((finish["inputs"]["width"], finish["inputs"]["height"]))
    assert sorted(made)[1] <= budget[1] and sorted(made)[0] <= budget[0]
    assert sorted(made)[1] == budget[1] or sorted(made)[0] == budget[0], "smaller than the graph's size allows"


@pytest.mark.parametrize("workflow", _takes_images())
def test_a_phones_portrait_with_a_rotate_tag_stays_portrait(house, workflow):
    """A phone stores a portrait as landscape pixels plus an EXIF 'rotate 90'; the re-encoded upload drops the tag,
    so it must be turned first or the model is given the photo on its side."""
    ran = next(w for w in _catalogue() if w["id"] == workflow)
    images = upload((1024, 556), exif_orientation=6) * (ran.get("uses_images") or 1)
    fake = FinishingRunPod()
    house["monkeypatch"].setattr(house["generate"], "runpod_api", fake)
    job_id = house["tracker"].create(prompt="make it snow", source="generator", target=SCREEN)["id"]
    house["generate"].start(prompt="make it snow", workflow=workflow, images=images, job_id=job_id,
                            publish_destination=SCREEN, silent=True, poll_interval=0.01)

    sent = Image.open(BytesIO(base64.b64decode(fake.images[0]["image"])))
    assert sent.size == (556, 1024), "the model was given the photo on its side"
    shown = size_of(tv_shows(house["output"]))
    assert shown[1] > shown[0] and same_shape(shown, (556, 1024))


@pytest.mark.parametrize("workflow", _text_only())
def test_a_text_picture_fills_the_screen(house, workflow):
    fake = FinishingRunPod()
    house["monkeypatch"].setattr(house["generate"], "runpod_api", fake)
    job_id = house["tracker"].create(prompt="a puppy", source="generator", target=SCREEN)["id"]
    house["generate"].start(prompt="a puppy", workflow=workflow, job_id=job_id,
                            publish_destination=SCREEN, silent=True, poll_interval=0.01)
    assert size_of(tv_shows(house["output"])) == (_destination()["maxwidth"], _destination()["maxheight"])


@pytest.mark.parametrize("workflow", _text_only())
def test_a_portrait_text_picture_is_not_cut_to_the_screen(house, workflow):
    """A caller that asks for a portrait render (width < height) gets a portrait, not a landscape strip of it."""
    latent = next(n["inputs"] for n in json.loads((REPO / "routes" / "workflows" / workflow).read_text()).values()
                  if "{{LATENT-IMAGE}}" in (n.get("_meta") or {}).get("title", ""))
    portrait = (latent["height"], latent["width"])
    fake = FinishingRunPod()
    house["monkeypatch"].setattr(house["generate"], "runpod_api", fake)
    job_id = house["tracker"].create(prompt="a puppy", source="generator", target=SCREEN)["id"]
    house["generate"].start(prompt="a puppy", workflow=workflow, width=portrait[0], height=portrait[1], job_id=job_id,
                            publish_destination=SCREEN, silent=True, poll_interval=0.01)
    shown = size_of(tv_shows(house["output"]))
    assert same_shape(shown, portrait), f"a {portrait[0]}x{portrait[1]} render came back {shown[0]}x{shown[1]}"


# ------------------------------------------------- a picture of another shape on a screen, and a chosen shape (19 Nov)
# Owner, 19 Nov: what happens when a portrait photo is published to a landscape TV, and when a landscape output is
# chosen for a portrait photo at the generator; AI extends the picture or picks the part to crop (routes/reframe.py).

import threading

TOP = (0, 160, 0)        # the upper half of a two-part portrait


class GatedRunPod(FinishingRunPod):
    """As FinishingRunPod, but the job only finishes when the test opens the gate (the worker is still drawing)."""

    def __init__(self, fail=False):
        super().__init__()
        self.gate = threading.Event()
        self.jobs = 0
        self.fail = fail

    def submit(self, endpoint_id, payload, timeout=60):
        self.jobs += 1
        return super().submit(endpoint_id, payload, timeout)

    def status(self, endpoint_id, job_id, timeout=10):
        if not self.gate.is_set():
            return {"status": "IN_PROGRESS"}
        if self.fail:
            return {"status": "FAILED", "error": "out of memory"}
        return super().status(endpoint_id, job_id, timeout)


def wait_for_extensions(timeout=30):
    for t in threading.enumerate():
        if t.name.startswith("extend-"):
            t.join(timeout)


def library_picture(house, size=(556, 1024), name="20261119-090000-portrait1.jpg", two_part=False):
    img = Image.new("RGB", size, OLD)
    if two_part:
        img.paste(Image.new("RGB", (size[0], size[1] // 2), TOP), (0, 0))
    stored = house["output"] / SCREEN / name
    img.save(stored, format="JPEG", quality=95)
    return stored


def publish(house, stored, metadata=None):
    result = house["publisher"].publish_to_destination(source=stored, publish_destination_id=SCREEN, silent=True,
                                                       metadata=metadata or {"type": "image", "prompt": "a photo"})
    assert result["success"], result
    return result


def screen_size():
    return _destination()["maxwidth"], _destination()["maxheight"]


def pixel(path, x, y):
    with Image.open(path) as img:
        return img.convert("RGB").getpixel((x, y))


def test_a_portrait_on_a_screen_set_to_extend_goes_up_whole_then_fills_it(house):
    house["policy"]["mode"] = "adapt"
    fake = GatedRunPod()
    house["monkeypatch"].setattr(house["generate"], "runpod_api", fake)
    stored = library_picture(house)
    publish(house, stored)

    # at once: the portrait, whole, while the worker draws
    shown = tv_shows(house["output"])
    assert size_of(shown) == (556, 1024)
    fake.gate.set()
    wait_for_extensions()

    shown = tv_shows(house["output"])
    assert size_of(shown) == screen_size(), "the screen was not filled"
    w, h = screen_size()
    assert near(pixel(shown, w // 2, h // 2), OLD), "the middle is not the original picture"
    assert near(pixel(shown, 10, h // 2), NEW), "the sides are not the extension"
    assert size_of(stored) == (556, 1024), "the library's picture was changed"
    sent = fake.images[0]
    with Image.open(BytesIO(base64.b64decode(sent["image"]))) as canvas:
        assert same_shape(canvas.size, screen_size()), "the worker was not given the screen's shape"
        assert near(canvas.convert("RGB").getpixel((5, canvas.height // 2)), (128, 128, 128)), "no grey to fill"
    assert _prompt_sent(fake.payload) == house["reframe"].EXTEND_PROMPT
    jobs, _ = house["tracker"].list(source="reframe", limit=1)
    job = jobs[0]
    assert job["status"] == "completed" and job["target"] == SCREEN, "the extension is not in the Jobs dock"


def test_republishing_an_extended_picture_uses_the_kept_copy(house):
    house["policy"]["mode"] = "adapt"
    fake = GatedRunPod()
    fake.gate.set()
    house["monkeypatch"].setattr(house["generate"], "runpod_api", fake)
    stored = library_picture(house)
    publish(house, stored)
    wait_for_extensions()
    assert fake.jobs == 1

    other = house["output"] / SCREEN / "20261119-090100-landscape.jpg"
    other.write_bytes(_image_bytes("JPEG", NEW, size=(64, 36)))
    publish(house, other)
    publish(house, stored)
    assert size_of(tv_shows(house["output"])) == screen_size(), "the kept copy was not used at once"
    wait_for_extensions()
    assert fake.jobs == 1, "the picture was extended again"


def test_a_screen_given_something_else_meanwhile_keeps_it(house):
    house["policy"]["mode"] = "adapt"
    fake = GatedRunPod()
    house["monkeypatch"].setattr(house["generate"], "runpod_api", fake)
    publish(house, library_picture(house))
    newer = house["output"] / SCREEN / "20261119-090200-newer.jpg"
    newer.write_bytes(_image_bytes("JPEG", NEW, size=(64, 36)))
    publish(house, newer)
    fake.gate.set()
    wait_for_extensions()
    assert size_of(tv_shows(house["output"])) == (64, 36), "the extension replaced a newer picture"


def test_a_failed_extension_leaves_the_picture_whole(house):
    house["policy"]["mode"] = "adapt"
    fake = GatedRunPod(fail=True)
    fake.gate.set()
    house["monkeypatch"].setattr(house["generate"], "runpod_api", fake)
    publish(house, library_picture(house))
    wait_for_extensions()
    assert size_of(tv_shows(house["output"])) == (556, 1024)


def test_a_portrait_on_a_screen_set_to_crop_shows_the_part_the_ai_picked(house):
    house["policy"]["mode"] = "crop"
    house["monkeypatch"].setattr(house["reframe"], "_vision_focus",
                                 lambda img: (0, 0, img.width, img.height // 4))      # the subject is at the top
    stored = library_picture(house, two_part=True)
    publish(house, stored)
    shown = tv_shows(house["output"])
    assert same_shape(size_of(shown), screen_size()), "not cropped to the screen's shape"
    assert near(colour_of(shown), TOP), "the crop is not where the subject is"
    assert size_of(stored) == (556, 1024), "the library's picture was changed"


def test_a_crop_with_no_answer_from_the_ai_keeps_the_middle(house):
    house["policy"]["mode"] = "crop"
    publish(house, library_picture(house))
    assert same_shape(size_of(tv_shows(house["output"])), screen_size())


@pytest.mark.parametrize("mode", ["auto", "adapt", "crop", "frame"])
def test_a_picture_of_the_screens_shape_is_never_reframed(house, mode):
    house["policy"]["mode"] = mode
    fake = GatedRunPod()
    fake.gate.set()
    house["monkeypatch"].setattr(house["generate"], "runpod_api", fake)
    stored = house["output"] / SCREEN / "20261119-090300-wide.jpg"
    stored.write_bytes(_image_bytes("JPEG", NEW, size=(1000, 560)))       # 0.4% off 16:9
    publish(house, stored)
    wait_for_extensions()
    assert size_of(tv_shows(house["output"])) == (1000, 560) and fake.jobs == 0


def test_a_screen_set_to_show_whole_shows_it_whole(house):
    publish(house, library_picture(house))
    assert size_of(tv_shows(house["output"])) == (556, 1024)


def test_a_picture_whose_shape_was_chosen_at_the_generator_is_not_reframed(house):
    house["policy"]["mode"] = "adapt"
    fake = GatedRunPod()
    fake.gate.set()
    house["monkeypatch"].setattr(house["generate"], "runpod_api", fake)
    publish(house, library_picture(house), metadata={"type": "image", "shape": "portrait"})
    wait_for_extensions()
    assert size_of(tv_shows(house["output"])) == (556, 1024) and fake.jobs == 0


def test_the_blank_screen_is_not_reframed(house):
    house["policy"]["mode"] = "adapt"
    fake = GatedRunPod()
    fake.gate.set()
    house["monkeypatch"].setattr(house["generate"], "runpod_api", fake)
    (house["output"] / "_blank.jpg").write_bytes(_image_bytes("JPEG", (0, 0, 0), size=(100, 100)))
    house["monkeypatch"].chdir(house["output"].parent)
    house["publisher"].publish_to_destination(source=None, publish_destination_id=SCREEN, blank=True, silent=True)
    wait_for_extensions()
    assert fake.jobs == 0


# A landscape output chosen for a portrait photo at the generator (Adapt and Combine's "shape" and "fill")

def _prompt_sent(payload):
    node = next(n for n in payload["input"]["workflow"].values()
                if "{{POSITIVE-PROMPT}}" in (n.get("_meta") or {}).get("title", ""))
    return node["inputs"].get("prompt") or node["inputs"].get("text")


def adapt(house, workflow, images, **params):
    fake = FinishingRunPod()
    house["monkeypatch"].setattr(house["generate"], "runpod_api", fake)
    job_id = house["tracker"].create(prompt="make it snow", source="generator")["id"]
    out = house["generate"].start(prompt="make it snow", workflow=workflow, images=images, job_id=job_id,
                                  poll_interval=0.01, **params)
    with Image.open(BytesIO(base64.b64decode(fake.images[0]["image"]))) as sent:
        sent = sent.convert("RGB")
    node = _finish_node(fake.payload)
    return fake, sent, (int(node["inputs"]["width"]), int(node["inputs"]["height"])), out


EDITS = [w for w in _takes_images() if w != "upscale-finish.json"]


@pytest.mark.parametrize("workflow", EDITS)
@pytest.mark.parametrize("shape,aspect", [("landscape", 16 / 9), ("square", 1.0)])
def test_a_chosen_shape_extends_the_photo_in_the_same_job(house, workflow, shape, aspect):
    ran = next(w for w in _catalogue() if w["id"] == workflow)
    fake, sent, finish, _ = adapt(house, workflow, upload((556, 1024)) * (ran.get("uses_images") or 1),
                                  shape=shape, fill="extend")
    assert abs((sent.width / sent.height) / aspect - 1) < 0.01, f"the photo was sent at {sent.size}"
    assert near(sent.getpixel((3, sent.height // 2)), (128, 128, 128)), "the photo was not put on a grey canvas"
    assert near(sent.getpixel((sent.width // 2, sent.height // 2)), OLD)
    assert _prompt_sent(fake.payload).startswith(house["reframe"].EXTEND_EDIT) and "make it snow" in _prompt_sent(fake.payload)
    assert abs((finish[0] / finish[1]) / aspect - 1) < 0.02, f"finished at {finish}"


@pytest.mark.parametrize("workflow", EDITS)
def test_a_chosen_shape_crops_the_photo_where_the_ai_finds_the_subject(house, workflow):
    house["monkeypatch"].setattr(house["reframe"], "_vision_focus", lambda img: (0, 0, img.width, img.height // 4))
    img = Image.new("RGB", (556, 1024), OLD)
    img.paste(Image.new("RGB", (556, 512), TOP), (0, 0))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=95)
    photo = [{"name": "photo.jpg", "image": base64.b64encode(buf.getvalue()).decode()}]
    ran = next(w for w in _catalogue() if w["id"] == workflow)
    fake, sent, finish, _ = adapt(house, workflow, photo * (ran.get("uses_images") or 1), shape="landscape", fill="crop")
    assert same_shape(sent.size, (16, 9))
    assert near(sent.getpixel((sent.width // 2, sent.height // 2)), TOP), "the crop is not where the subject is"
    assert _prompt_sent(fake.payload) == "make it snow"
    assert same_shape(finish, (16, 9))


@pytest.mark.parametrize("workflow", EDITS)
def test_same_as_the_photo_changes_nothing(house, workflow):
    ran = next(w for w in _catalogue() if w["id"] == workflow)
    fake, sent, finish, _ = adapt(house, workflow, upload((556, 1024)) * (ran.get("uses_images") or 1),
                                  shape="photo", fill="extend")
    assert sent.size == (556, 1024) and _prompt_sent(fake.payload) == "make it snow"
    assert same_shape(finish, (556, 1024))


def test_the_generator_pages_shape_reaches_the_job(house):
    """The page sends Adapt's options as params; only start()'s own parameters get through, so shape and fill must
    be among them."""
    import routes.generate_handler as handler
    import routes.generate_utils as generate_utils
    mp = house["monkeypatch"]
    mp.setattr(house["generate"], "runpod_api", FinishingRunPod())
    mp.setattr(handler, "_refine", lambda data, prompt, workflow, *a, **k: (workflow, prompt, prompt))
    out = generate_utils.process_generate_image_request(
        {"prompt": "make it snow", "workflow": "qwen21-edit.json", "batch_id": "abc123_frontend",
         "params": {"poll_interval": 0.01, "shape": "landscape", "fill": "extend", "seed": 5},
         "global_params": {"batch_size": 1}},
        uploaded_images=upload((556, 1024)))
    made = size_of(house["output"] / "_recent" / out["recent_files"][0])
    assert same_shape(made, (16, 9)), f"a landscape was chosen, {made[0]}x{made[1]} came back"


def test_the_catalogues_shape_options_are_the_ones_the_app_knows():
    from routes import reframe
    for w in _catalogue():
        for p in w.get("params") or []:
            if p["id"] == "shape":
                values = {o["value"] if isinstance(o, dict) else o for o in p["options"]}
                assert values - {"auto", "photo"} <= set(reframe.SHAPES), f"{w['id']}: shapes the app doesn't know"
            if p["id"] == "fill":
                assert {o["value"] if isinstance(o, dict) else o for o in p["options"]} <= {"extend", "crop"}


# ------------------------------------------------- two pictures: which one the result is built on (owner, 19 Nov)
# "add this person into the painting", "replace that woman with this one", "render this in the style of this
# painting", "put these two together": Qwen 2.1 builds its result on image 1, so the adapt refiner names the canvas
# (A, B or none) and the app puts it first; the result takes the canvas's shape whatever the other picture's is.

PAINTING, PERSON = (150, 110, 60), (40, 40, 160)


def picture(colour, size):
    buf = BytesIO()
    Image.new("RGB", size, colour).save(buf, format="JPEG", quality=95)
    return {"name": f"{colour[0]}.jpg", "image": base64.b64encode(buf.getvalue()).decode()}


def combine_through_the_page(house, framing, uploads, **params):
    """The generator page, Adapt's refiner answering with framing; returns (fake worker, picture made)."""
    import routes.generate_handler as handler
    import routes.generate_utils as generate_utils
    mp = house["monkeypatch"]
    fake = FinishingRunPod()
    mp.setattr(house["generate"], "runpod_api", fake)
    mp.setattr(handler, "_refine", lambda data, prompt, workflow, *a, **k: (
        "qwen21-combine.json", "Put the man from image 2 into image 1", "Put the man from image 2 into image 1", framing))
    out = generate_utils.process_generate_image_request(
        {"prompt": "add this man into the painting", "batch_id": "abc123_frontend", "refiner": "adapt",
         "params": {"poll_interval": 0.01, **params}, "global_params": {"batch_size": 1}}, uploaded_images=uploads)
    return fake, size_of(house["output"] / "_recent" / out["recent_files"][0])


def sent_colours(fake):
    out = []
    for img in fake.images:
        with Image.open(BytesIO(base64.b64decode(img["image"]))) as im:
            im = im.convert("RGB")
            out.append(im.getpixel((im.width // 2, im.height // 2)))
    return out


def test_the_canvas_uploaded_second_goes_first_and_sets_the_shape(house):
    """'add this man into the painting', the man uploaded first: the painting is image 1 and the result is its shape."""
    fake, made = combine_through_the_page(house, {"canvas": "B", "shape": "canvas"},
                                          [picture(PERSON, (556, 1024)), picture(PAINTING, (1600, 900))])
    first, second = sent_colours(fake)
    assert near(first, PAINTING) and near(second, PERSON), "the painting is not image 1"
    assert same_shape(made, (1600, 900)), f"the result is {made}, not the painting's shape"


def test_the_canvas_uploaded_first_stays_first(house):
    fake, made = combine_through_the_page(house, {"canvas": "A", "shape": "canvas"},
                                          [picture(PAINTING, (1600, 900)), picture(PERSON, (556, 1024))])
    assert near(sent_colours(fake)[0], PAINTING) and same_shape(made, (1600, 900))


def test_a_new_scene_takes_the_refiners_shape(house):
    """'put these two together': no canvas; two portraits side by side make a landscape."""
    fake, made = combine_through_the_page(house, {"canvas": "none", "shape": "landscape"},
                                          [picture(PERSON, (556, 1024)), picture(PAINTING, (600, 1000))])
    with Image.open(BytesIO(base64.b64decode(fake.images[0]["image"]))) as first:
        assert same_shape(first.size, (16, 9)), "image 1 was not given the new scene's shape"
    assert same_shape(made, (16, 9))


def test_the_users_shape_wins_over_the_refiners(house):
    fake, made = combine_through_the_page(house, {"canvas": "none", "shape": "landscape"},
                                          [picture(PERSON, (556, 1024)), picture(PAINTING, (600, 1000))],
                                          shape="square", fill="extend")
    assert same_shape(made, (1, 1))


def test_automatic_with_a_canvas_keeps_the_canvass_shape(house):
    fake, made = combine_through_the_page(house, {"canvas": "B", "shape": "canvas"},
                                          [picture(PERSON, (556, 1024)), picture(PAINTING, (1024, 768))], shape="auto")
    assert same_shape(made, (1024, 768))


def test_the_adapt_refiner_is_told_which_picture_is_which_and_answers_with_a_canvas(monkeypatch):
    """_refine sends Adapt's own answer schema (canvas, shape) and names the uploads A and B; its answer comes back."""
    import routes.generate_handler as handler
    import routes.openai
    from routes.utils import build_schema_subs
    seen = {}

    def answer(**kw):
        seen.update(kw)
        return {"canvas": "B", "shape": "canvas", "full_prompt": "Put the man from image 2 into image 1",
                "must_keep": [], "focus": "scene", "workflow": "qwen21-combine.json"}
    monkeypatch.setattr(routes.openai, "openai_prompt", answer)
    images = [picture(PERSON, (56, 102)), picture(PAINTING, (160, 90))]
    wf, refined, final, framing = handler._refine(
        {}, "add this man into the painting", None, images, "refiner-adapt.txt.j2", 2, images,
        {"prompt": "add this man into the painting"}, build_schema_subs())
    assert wf == "qwen21-combine.json" and framing == {"canvas": "B", "shape": "canvas"}
    assert set(seen["schema"]["parameters"]["properties"]) >= {"canvas", "shape", "full_prompt"}
    assert "A (uploaded first), then B" in seen["user_prompt"]
    assert len(seen["images"]) == 2


@pytest.mark.parametrize("framing,shape,order,expected_shape", [
    ({"canvas": "A", "shape": "canvas"}, None, "ab", None),
    ({"canvas": "B", "shape": "canvas"}, None, "ba", None),
    ({"canvas": "B", "shape": "canvas"}, "auto", "ba", None),
    ({"canvas": "none", "shape": "landscape"}, None, "ab", "landscape"),
    ({"canvas": "none", "shape": "landscape"}, "auto", "ab", "landscape"),
    ({"canvas": "none", "shape": "landscape"}, "portrait", "ab", "portrait"),   # the user's choice wins
    ({"canvas": "B", "shape": "canvas"}, "square", "ba", "square"),
    ({}, None, "ab", None),
])
def test_framing_for(framing, shape, order, expected_shape):
    from routes.generate_handler import framing_for
    images, got = framing_for([{"name": "a"}, {"name": "b"}], framing, shape)
    assert "".join(i["name"] for i in images) == order and got == expected_shape


def test_one_picture_is_never_reordered():
    from routes.generate_handler import framing_for
    images, _ = framing_for([{"name": "a"}], {"canvas": "B", "shape": "canvas"}, None)
    assert [i["name"] for i in images] == ["a"]


@pytest.mark.parametrize("canvas,expected", [
    ("B", "Put the man from image 2 into the room in image 1; keep image 1 unchanged."),
    ("A", "Put the man from image 1 into the room in image 2; keep image 2 unchanged."),
    ("none", "Put the man from image 1 into the room in image 2; keep image 2 unchanged."),
])
def test_the_refiners_letters_become_the_models_numbers(canvas, expected):
    """The refiner calls the uploads image A and image B; the model gets the canvas as image 1."""
    from routes.generate_handler import number_pictures
    raw = "Put the man from image A into the room in image B; keep image B unchanged."
    assert number_pictures(raw, {"canvas": canvas}) == expected
    assert number_pictures("A cat on a mat, a Bauhaus poster", {"canvas": "B"}) == "A cat on a mat, a Bauhaus poster"


def test_the_numbered_prompt_reaches_the_model(house):
    """Through the page: the refiner's letters are numbered with the canvas first, in what the model is sent."""
    import routes.generate_handler as handler
    import routes.generate_utils as generate_utils
    mp = house["monkeypatch"]
    fake = FinishingRunPod()
    mp.setattr(house["generate"], "runpod_api", fake)
    raw = "Put the man from image A into the room in image B."
    mp.setattr(handler, "_refine", lambda *a, **k: ("qwen21-combine.json", raw, raw, {"canvas": "B", "shape": "canvas"}))
    generate_utils.process_generate_image_request(
        {"prompt": "add this man into the painting", "batch_id": "abc123_frontend", "refiner": "adapt",
         "params": {"poll_interval": 0.01}, "global_params": {"batch_size": 1}},
        uploaded_images=[picture(PERSON, (556, 1024)), picture(PAINTING, (1600, 900))])
    assert _prompt_sent(fake.payload) == "Put the man from image 2 into the room in image 1."


# ------------------------------------------------- a picture drawn from text for a screen of another shape (19 Nov)
# Owner, 19 Nov: a text-to-image picture for a portrait screen (a phone, 1080x2400) was drawn landscape and
# letterboxed. It is now drawn in the screen's shape at the workflow's own pixel count, unless the caller chose a size.

PHONE = (1080, 2400)


def render_for(house, workflow, box, **params):
    fake = FinishingRunPod()
    house["monkeypatch"].setattr(house["generate"], "runpod_api", fake)
    job_id = house["tracker"].create(prompt="a lighthouse", source="generator")["id"]
    house["generate"].start(prompt="a lighthouse", workflow=workflow, job_id=job_id, poll_interval=0.01,
                            maxwidth=box[0], maxheight=box[1], **params)
    latent = next(n["inputs"] for n in fake.payload["input"]["workflow"].values()
                  if "{{LATENT-IMAGE}}" in (n.get("_meta") or {}).get("title", ""))
    node = _finish_node(fake.payload)
    return (int(latent["width"]), int(latent["height"])), (int(node["inputs"]["width"]), int(node["inputs"]["height"]))


def _graph_latent(workflow):
    return next((int(n["inputs"]["width"]), int(n["inputs"]["height"]))
                for n in json.loads((REPO / "routes" / "workflows" / workflow).read_text()).values()
                if "{{LATENT-IMAGE}}" in (n.get("_meta") or {}).get("title", ""))


def _default_size(workflow):
    params = {p["id"]: p.get("default") for p in next(w for w in _catalogue() if w["id"] == workflow).get("params") or []}
    return int(params["width"]), int(params["height"])


@pytest.mark.parametrize("workflow", _text_only())
def test_a_text_picture_for_a_phone_is_drawn_tall_and_fills_it(house, workflow):
    latent, finish = render_for(house, workflow, PHONE)
    assert same_shape(latent, PHONE), f"drawn {latent}, not the phone's shape"
    assert latent[0] % 16 == 0 and latent[1] % 16 == 0
    graph = _graph_latent(workflow)
    assert abs(latent[0] * latent[1] / (graph[0] * graph[1]) - 1) < 0.06, "not the workflow's own pixel count"
    assert finish == PHONE, f"finished at {finish}, not the whole phone"


@pytest.mark.parametrize("workflow", _text_only())
def test_the_pages_default_size_still_draws_for_the_phone(house, workflow):
    """The generator page sends the workflow's default Width/Height whenever a workflow is picked by hand."""
    latent, finish = render_for(house, workflow, PHONE, width=_default_size(workflow)[0], height=_default_size(workflow)[1])
    assert same_shape(latent, PHONE) and finish == PHONE


@pytest.mark.parametrize("workflow", _text_only())
def test_a_chosen_size_is_drawn_as_chosen(house, workflow):
    w, h = _default_size(workflow)
    latent, finish = render_for(house, workflow, PHONE, width=w - 64, height=h)
    assert latent == (w - 64, h), "the caller's own size was changed"
    assert same_shape(finish, latent) and finish[0] <= PHONE[0] and finish[1] <= PHONE[1]


@pytest.mark.parametrize("workflow", _text_only())
def test_a_text_picture_for_a_tv_is_drawn_as_the_graph_says(house, workflow):
    latent, finish = render_for(house, workflow, (3840, 2160))
    assert latent == _graph_latent(workflow) and finish == (3840, 2160)


# ------------------------------------------------- the person's choice when publishing; auto (owner, 19 Nov)

def test_the_persons_choice_wins_over_the_screens(house):
    house["policy"]["mode"] = "adapt"
    fake = GatedRunPod()
    fake.gate.set()
    house["monkeypatch"].setattr(house["generate"], "runpod_api", fake)
    stored = library_picture(house)
    house["publisher"].publish_to_destination(source=stored, publish_destination_id=SCREEN, silent=True, fit="frame",
                                              metadata={"type": "image"})
    wait_for_extensions()
    assert size_of(tv_shows(house["output"])) == (556, 1024) and fake.jobs == 0
    house["publisher"].publish_to_destination(source=stored, publish_destination_id=SCREEN, silent=True, fit="crop",
                                              metadata={"type": "image"})
    assert same_shape(size_of(tv_shows(house["output"])), screen_size())


def test_a_chosen_fit_applies_even_to_a_picture_shaped_at_the_generator(house):
    stored = library_picture(house)
    house["publisher"].publish_to_destination(source=stored, publish_destination_id=SCREEN, silent=True, fit="crop",
                                              metadata={"type": "image", "shape": "portrait"})
    assert same_shape(size_of(tv_shows(house["output"])), screen_size())


def test_auto_reads_the_picture_then_paints_to_its_plan(house):
    """The AI's plan: trim the top and bottom fifth, put the rest left of centre, say what to paint; one generator
    pass; the kept part's own pixels are on the screen."""
    import routes.openai
    house["policy"]["mode"] = "auto"
    house["monkeypatch"].setattr(routes.openai, "openai_prompt", lambda *a, **k: {
        "keep_start": 0.2, "keep_end": 0.8, "place": 0.25, "fill": "more of the street, a lamp post on the right",
        "explanation": "empty sky and pavement"})
    fake = GatedRunPod()
    fake.gate.set()
    house["monkeypatch"].setattr(house["generate"], "runpod_api", fake)
    publish(house, library_picture(house, two_part=True))
    wait_for_extensions()

    assert fake.jobs == 1
    assert "a lamp post on the right" in _prompt_sent(fake.payload)
    with Image.open(BytesIO(base64.b64decode(fake.images[0]["image"]))) as canvas:
        canvas = canvas.convert("RGB")
        assert same_shape(canvas.size, screen_size())
        kept_h = canvas.height
        assert abs(canvas.width - round(kept_h * 16 / 9)) <= 2
        # the kept part (556 wide, 60% of 1024 tall, scaled to the canvas height) sits left of centre
        kept_w = round(556 * kept_h / round(1024 * 0.6))
        left = round((canvas.width - kept_w) * 0.25)
        assert near(canvas.getpixel((left + kept_w // 2, 5)), TOP)       # its top is the picture's upper half
        assert near(canvas.getpixel((canvas.width - 5, kept_h // 2)), (128, 128, 128))
    shown = tv_shows(house["output"])
    w, h = screen_size()
    assert size_of(shown) == (w, h)
    kept_on_screen = round(556 * h / round(1024 * 0.6))
    x_mid = round((w - kept_on_screen) * 0.25) + kept_on_screen // 2
    assert near(pixel(shown, x_mid, h // 4), TOP) and near(pixel(shown, x_mid, 3 * h // 4), OLD)
    assert near(pixel(shown, w - 10, h // 2), NEW), "the right is not the painted part"
