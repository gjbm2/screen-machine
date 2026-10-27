"""Art tags: the lexicon, the sidecar contract, the tagger's request, the queue and the API (shared plumbing: real
tests; no network)."""
import json
import threading

import pytest
from flask import Flask
from PIL import Image

import routes.art_tags as art_tags
from routes.art_tags import api, lexicon, sidecar, tagger, worker
from routes.art_tags.tests.conftest import make_bucket

AI = {"subject": ["landscape"], "medium": "painting", "moods": ["calm", "dreamy"], "palette": "cool",
      "look": "misty lake", "energy": 0.2, "valence": 0.6, "organic": 0.9, "density": 0.3, "quality": 0.75,
      "music": {g: 0.5 for g in ("ambient", "classical", "jazz", "soul", "pop", "rock", "house", "techno", "hiphop",
                                 "world")}, "auto_ok": True, "confidence": 0.8}


# -- the lexicon -------------------------------------------------------------------------------------------------------
def test_normalise():
    assert lexicon.normalise("  Still  Life ") == "still-life"
    assert lexicon.normalise("still_life") == "still-life"
    assert lexicon.normalise("--x--") == "x"
    assert lexicon.normalise("a" * 40) == "a" * 32
    assert lexicon.normalise("   ") is None and lexicon.normalise(3) is None


def test_prompt_and_schema_come_from_the_lexicon():
    p = lexicon.system_prompt()
    assert "{facets}" not in p and "{groups}" not in p
    for w in lexicon.words("subject") + lexicon.words("moods") + lexicon.groups():
        assert w in p
    s = lexicon.tool_schema()["parameters"]["properties"]
    assert s["subject"]["items"]["enum"] == lexicon.words("subject") and s["subject"]["maxItems"] == 2
    assert s["moods"]["maxItems"] == 3 and s["medium"]["enum"] == lexicon.words("medium")
    assert set(s["music"]["properties"]) == set(lexicon.groups())


def test_clean_drops_unknown_words_clamps_and_fills_groups():
    ai = lexicon.clean({"subject": ["Landscape", "spaceship", "Still Life", "sky"], "medium": "oil-painting",
                        "moods": ["calm", "zen", "dreamy", "cool", "dark"], "palette": "WARM",
                        "look": "  a   lake  ", "energy": 1.7, "valence": -2, "organic": "lots",
                        "music": {"jazz": 0.9, "Classical": 3, "polka": 1}, "auto_ok": "yes"})
    assert ai["subject"] == ["landscape", "still-life"]
    assert ai["medium"] is None and ai["palette"] == "warm"
    assert ai["moods"] == ["calm", "dreamy", "cool"]
    assert ai["look"] == "a lake"
    assert (ai["energy"], ai["valence"], ai["organic"]) == (1.0, 0.0, 0.5)
    assert ai["music"]["jazz"] == 0.9 and ai["music"]["classical"] == 1.0
    assert ai["music"]["rock"] == lexicon.MUSIC_DEFAULT and "polka" not in ai["music"]
    assert set(ai["music"]) == set(lexicon.groups())
    assert ai["auto_ok"] is True and ai["confidence"] == 0.5
    with pytest.raises(ValueError):
        lexicon.clean({"subject": ["spaceship"], "moods": ["zen"]})


# -- the sidecar -------------------------------------------------------------------------------------------------------
def test_effective_tags_order_and_removed():
    t = {"ai": {"subject": ["landscape", "sky"], "medium": "painting", "moods": ["calm", "cool"]},
         "added": ["Lake", "calm", "misty"], "removed": ["sky"]}
    assert sidecar.effective_tags(t) == ["landscape", "painting", "calm", "cool", "lake", "misty"]
    assert sidecar.effective_tags(None) == []


def test_edits_add_and_remove_semantics():
    d = sidecar.apply_ai({"prompt": "p"}, dict(AI), "m", 0.001, 1)
    assert d["tags"] == ["landscape", "painting", "calm", "dreamy"]
    d = sidecar.apply_edits(d, add=["Misty Lake", "calm"], remove=["dreamy"])
    assert d["tagging"]["added"] == ["misty-lake"] and d["tagging"]["removed"] == ["dreamy"]
    assert d["tags"] == ["landscape", "painting", "calm", "misty-lake"]
    d = sidecar.apply_edits(d, add=["dreamy"], remove=["misty-lake"])       # back from removed; an added word drops
    assert d["tagging"]["added"] == [] and d["tagging"]["removed"] == []
    assert d["tags"] == ["landscape", "painting", "calm", "dreamy"]
    d = sidecar.apply_edits(d, add=["favourite"], remove=["favorite"])       # favourites live in bucket.json
    assert "favourite" not in d["tags"] and d["tagging"]["added"] == []


def test_a_person_can_tag_before_the_ai():
    d = sidecar.apply_edits({}, add=["jazz bar"])
    assert d["tags"] == ["jazz-bar"] and "status" not in d["tagging"]
    d = sidecar.apply_ai(d, dict(AI), "m", 0.0, 1)                           # the AI keeps the person's words
    assert d["tagging"]["status"] == "done" and d["tags"][-1] == "jazz-bar"


def test_update_is_atomic_and_keeps_other_keys(out):
    make_bucket(out, "b", ["x.jpg"], sidecars={"x.jpg": {"prompt": "a lake", "seed": 7, "nested": {"k": [1, 2]}}})
    p = sidecar.media_path("b", "x.jpg")
    d = sidecar.update(p, lambda d: sidecar.apply_ai(d, dict(AI), "m", 0.0006, 1))
    on_disk = json.loads((out / "b" / "x.jpg.json").read_text())
    assert on_disk == d
    assert on_disk["prompt"] == "a lake" and on_disk["seed"] == 7 and on_disk["nested"] == {"k": [1, 2]}
    assert on_disk["tagging"]["status"] == "done" and on_disk["tags"][0] == "landscape"
    assert not [f for f in (out / "b").iterdir() if f.name.endswith(".tmp")]


def test_update_never_overwrites_a_sidecar_it_cant_read(out):
    make_bucket(out, "b", ["x.jpg"])
    (out / "b" / "x.jpg.json").write_text("{not json")
    with pytest.raises(ValueError):
        sidecar.update(sidecar.media_path("b", "x.jpg"), lambda d: sidecar.apply_edits(d, add=["a"]))
    assert (out / "b" / "x.jpg.json").read_text() == "{not json"


def test_concurrent_updates_lose_nothing(out):
    make_bucket(out, "b", ["x.jpg"], sidecars={"x.jpg": {"prompt": "p"}})
    p = sidecar.media_path("b", "x.jpg")
    words = [f"w{i}" for i in range(30)]
    ts = [threading.Thread(target=sidecar.update, args=(p, lambda d, w=w: sidecar.apply_edits(d, add=[w])))
          for w in words]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    d = json.loads((out / "b" / "x.jpg.json").read_text())
    assert sorted(d["tags"]) == sorted(words) and d["prompt"] == "p"


# -- the tagger --------------------------------------------------------------------------------------------------------
def test_image_is_sent_small_with_its_prompts(out, monkeypatch):
    d = make_bucket(out, "north-screen", ["20261027-223029-613e3305.jpg"],
                    sidecars={"20261027-223029-613e3305.jpg": {
                        "original_prompt": "a lake", "prompt": "A misty lake at dawn " * 100,
                        "refined_prompt": "A misty lake at dawn " * 100}})
    Image.new("RGB", (3840, 2160), (10, 20, 30)).save(d / "20261027-223029-613e3305.jpg", "JPEG")
    seen = {}

    def fake_call_json(task, **kw):
        seen.update(kw, task=task)
        import utils.llm as llm
        return llm.LLMResult(True, {"subject": ["seascape"], "medium": "photograph", "moods": ["calm"],
                                    "palette": "cool", "look": "lake", "energy": 0.1, "valence": 0.5, "organic": 0.8,
                                    "density": 0.2, "quality": 0.7, "music": {"ambient": 1}, "auto_ok": True,
                                    "confidence": 0.9}, None, task, "gemini", "gemini-3.1-flash-lite", 10.0,
                             cost_usd=0.0007)

    import utils.llm as llm
    monkeypatch.setattr(llm, "call_json", fake_call_json)
    res = tagger.tag_file("north-screen", "20261027-223029-613e3305.jpg", model="gemini-x")
    assert res["ok"] and res["ai"]["subject"] == ["seascape"] and res["cost_usd"] == 0.0007
    assert seen["task"] == "tag" and seen["model"] == "gemini-x" and seen["lenient"] is True
    text, img = seen["user"]
    assert text.count("A misty lake") < 40                                   # cut to prompt_chars, said once
    assert "The person's prompt: a lake" in text and text.endswith("Saved to: north-screen")
    assert text.count("Generation prompt") == 0 and "refined prompt" not in text   # refined == prompt: deduped
    assert img["type"] == "image_url" and img["image_url"]["detail"] == "low"
    import base64
    import io
    im = Image.open(io.BytesIO(base64.b64decode(img["image_url"]["url"].split(",", 1)[1])))
    assert max(im.size) == 768


def test_a_video_sends_its_middle_frame(tmp_path):
    import cv2
    import numpy as np
    p = tmp_path / "v.mp4"
    w = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 180))
    for i in range(20):
        w.write(np.full((180, 320, 3), i * 10, np.uint8))
    w.release()
    assert tagger.image_data_url(p, 128).startswith("data:image/jpeg;base64,")
    (tmp_path / "bad.jpg").write_bytes(b"not a jpeg")
    with pytest.raises(tagger.Undecodable):
        tagger.image_data_url(tmp_path / "bad.jpg", 128)


@pytest.mark.parametrize("err,kind", [
    ("timeout", "transient"), ("gemini-3.1-flash-lite: http 429: Resource exhausted, quota", "transient"),
    ("http 503: overloaded", "transient"), ("connection: reset", "transient"),
    ("breaker open (3 failures in a row)", "transient"),
    ("http 402: payment required", "quota"), ("http 400: your credit balance is too low", "quota"),
    ("http 403: billing not enabled", "quota"), ("http 400: quota exceeded for project", "quota"),
    ("no key for gemini (GEMINI_API_KEY)", "config"), ("no model configured", "config"),
    ("http 400: PROHIBITED_CONTENT", "invalid"), ("no tool call in the answer", "invalid"),
    ("invalid: $.subject: expected array", "invalid")])
def test_classify(err, kind):
    assert tagger.classify(err) == kind


# -- the queue ---------------------------------------------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def _worker(results, clock=None, budget=False, alerts=None):
    """A worker whose tag function plays ``results`` (a list of dicts, or a callable per call)."""
    calls = []

    def fake(bucket, filename):
        calls.append((bucket, filename))
        r = results(bucket, filename) if callable(results) else results.pop(0)
        return dict(r)

    w = worker.TagWorker(tag_fn=fake, clock=clock or Clock(), budget_fn=lambda task: budget,
                         alert_fn=(lambda *a, **k: alerts.append((a, k))) if alerts is not None else (lambda *a, **k: 0))
    w.calls = calls
    return w


OK = {"ok": True, "ai": AI, "model": "gemini-3.1-flash-lite", "cost_usd": 0.0006}


def test_queue_dedupes_and_front_jumps(out):
    make_bucket(out, "b", ["1.jpg", "2.jpg", "3.jpg"])
    w = _worker(lambda b, f: OK)
    assert w.enqueue("b", "1.jpg") and w.enqueue("b", "2.jpg")
    assert not w.enqueue("b", "1.jpg")
    assert w.enqueue("b", "3.jpg", front=True)
    assert not w.enqueue("b", "2.jpg", front=True)                         # already queued: moved to the front
    assert list(w.q) == [("b", "2.jpg"), ("b", "3.jpg"), ("b", "1.jpg")]
    assert not w.enqueue("_recent", "x.jpg")                               # never tagged
    for _ in range(3):
        assert w.step() == "done"
    assert w.calls == [("b", "2.jpg"), ("b", "3.jpg"), ("b", "1.jpg")] and w.step() == "idle"
    d = json.loads((out / "b" / "1.jpg.json").read_text())
    assert d["tagging"]["status"] == "done" and d["tagging"]["model"] == "gemini-3.1-flash-lite"
    assert d["tags"] == ["landscape", "painting", "calm", "dreamy"] and w.done_session == 3


def test_done_files_are_skipped_unless_retagged(out):
    make_bucket(out, "b", ["1.jpg"])
    w = _worker(lambda b, f: OK)
    w.enqueue("b", "1.jpg")
    assert w.step() == "done"
    sidecar.update(sidecar.media_path("b", "1.jpg"), lambda d: sidecar.apply_edits(d, add=["mine"], remove=["calm"]))
    w.enqueue("b", "1.jpg")
    assert w.step() == "skipped" and len(w.calls) == 1
    w.enqueue("b", "1.jpg", front=True, retag=True)
    assert w.step() == "done" and len(w.calls) == 2
    d = json.loads((out / "b" / "1.jpg.json").read_text())
    assert d["tagging"]["added"] == ["mine"] and d["tagging"]["removed"] == ["calm"] and "calm" not in d["tags"]
    w.enqueue("b", "gone.jpg")
    assert w.step() == "skipped" and len(w.calls) == 2


def test_transient_failures_back_off_and_keep_the_item(out):
    make_bucket(out, "b", ["1.jpg", "2.jpg"])
    clock = Clock()
    res = [{"ok": False, "error": "http 429: slow down", "kind": "transient"}] * 3 + [OK, OK]
    w = _worker(list(res), clock)
    w.enqueue("b", "1.jpg")
    w.enqueue("b", "2.jpg")
    assert w.step() == "retry" and w.backoff_s == 30 and list(w.q)[0] == ("b", "1.jpg")
    assert w.step() == "waiting"
    clock.t += 30
    assert w.step() == "retry" and w.backoff_s == 60
    clock.t += 60
    assert w.step() == "retry" and w.backoff_s == 120
    w.backoff_s = 1500
    w.not_before = 0
    w.tag_fn = lambda b, f: {"ok": False, "error": "timeout", "kind": "transient"}
    assert w.step() == "retry" and w.backoff_s == worker.BACKOFF_MAX_S      # capped at 30 min
    w.tag_fn = lambda b, f: OK
    clock.t += worker.BACKOFF_MAX_S
    assert w.step() == "done" and w.backoff_s == 0
    assert w.attempts == {}                                                 # a transient failure costs no attempt


def test_bad_answers_fail_after_max_attempts(out):
    make_bucket(out, "b", ["1.jpg", "2.jpg"], sidecars={"1.jpg": {"prompt": "keep me"}})
    bad = {"ok": False, "error": "invalid: no tool call", "kind": "invalid"}
    w = _worker(lambda b, f: bad if f == "1.jpg" else OK)
    w.enqueue("b", "1.jpg")
    w.enqueue("b", "2.jpg")
    assert w.step() == "attempt" and list(w.q) == [("b", "2.jpg"), ("b", "1.jpg")]    # behind the rest
    assert w.step() == "done"
    assert w.step() == "attempt"
    assert w.step() == "failed" and w.failed_session == 1 and not w.q
    d = json.loads((out / "b" / "1.jpg.json").read_text())
    assert d["prompt"] == "keep me"
    assert d["tagging"]["status"] == "failed" and d["tagging"]["attempts"] == 3 and "no tool call" in d["tagging"]["error"]
    w.enqueue("b", "1.jpg")
    assert w.step() == "skipped"                                            # left alone from then on


def test_quota_pauses_an_hour_with_one_alert(out):
    make_bucket(out, "b", ["1.jpg"])
    clock, alerts = Clock(), []
    w = _worker(lambda b, f: {"ok": False, "error": "http 402: out of credit", "kind": "quota"}, clock, alerts=alerts)
    w.enqueue("b", "1.jpg")
    assert w.step() == "paused" and list(w.q) == [("b", "1.jpg")]
    assert w.snapshot()["paused_until"] and "credit" in w.snapshot()["pause_reason"]
    assert w.step() == "paused" and len(w.calls) == 1 and len(alerts) == 1
    assert alerts[0][0][0] == "art_tags.quota"
    clock.t += worker.PAUSE_S
    assert w.step() == "paused" and len(w.calls) == 2 and len(alerts) == 2  # a new pause: one more alert


def test_over_budget_waits_for_midnight(out):
    make_bucket(out, "b", ["1.jpg"])
    w = _worker(lambda b, f: OK, budget=True)
    w.enqueue("b", "1.jpg")
    assert w.step() == "budget" and not w.calls and list(w.q) == [("b", "1.jpg")]
    assert w.paused_until == worker._next_midnight(w.clock()) and w.step() == "paused"


def test_sweep_order_and_what_it_skips(out):
    make_bucket(out, "aaa", ["20261001-000000-aaaaaaaa.jpg"])
    make_bucket(out, "devtest", ["20261001-000000-dddddddd.jpg"])
    make_bucket(out, "south-screen", ["20261001-000000-ssssssss.jpg"])
    make_bucket(out, "north-screen", ["20261001-000000-00000001.jpg", "20261020-000000-00000002.jpg",
                                      "20261010-000000-00000003.jpg", "20261005-000000-00000004.mp4"],
                favs=["20261001-000000-00000001.jpg"],
                sidecars={"20261010-000000-00000003.jpg": {"tagging": {"status": "done"}}})
    (out / "north-screen" / "20261005-000000-00000004.mp4").unlink()        # named but gone
    make_bucket(out, "_recent", ["20261027-000000-rrrrrrrr.jpg"])
    make_bucket(out, "legacy", ["old.jpg"], legacy=True, sidecars={"old.jpg": {"tagging": {"status": "failed"}}})
    (out / "no-bucket-json").mkdir()
    rows = worker.scan()
    assert [(r["bucket"], r["file"]) for r in rows] == [
        ("north-screen", "20261001-000000-00000001.jpg"),                  # the favourite first
        ("north-screen", "20261020-000000-00000002.jpg"),                  # then newest first
        ("north-screen", "20261010-000000-00000003.jpg"),
        ("south-screen", "20261001-000000-ssssssss.jpg"),
        ("devtest", "20261001-000000-dddddddd.jpg"),
        ("aaa", "20261001-000000-aaaaaaaa.jpg"),
        ("legacy", "old.jpg")]
    w = _worker(lambda b, f: OK)
    assert w.sweep() == 5                                                   # not the done or the failed one
    assert list(w.q)[0] == ("north-screen", "20261001-000000-00000001.jpg")
    assert w.sweep() == 0                                                   # deduped
    cov = w.coverage()
    assert cov["north-screen"] == {"total": 3, "tagged": 1, "failed": 0} and cov["legacy"]["failed"] == 1


def test_the_thread_tags_and_never_dies(out, monkeypatch):
    make_bucket(out, "b", ["1.jpg", "2.jpg"])
    monkeypatch.setattr(worker, "FIRST_SWEEP_S", 0.0)
    done = threading.Event()

    def fake(b, f):
        if f == "2.jpg":
            done.set()
        if f == "1.jpg":
            raise RuntimeError("boom")                                     # a crash in tag_fn costs an attempt
        return OK

    w = worker.TagWorker(tag_fn=fake, budget_fn=lambda t: False, alert_fn=lambda *a, **k: 0)
    w.start()
    try:
        assert done.wait(10)
        assert w.snapshot()["running"]
    finally:
        w.stop()
    assert not w.snapshot()["running"]


# -- the API -----------------------------------------------------------------------------------------------------------
@pytest.fixture
def client(out, monkeypatch):
    w = worker.TagWorker(tag_fn=lambda b, f: OK, budget_fn=lambda t: False, alert_fn=lambda *a, **k: 0)
    monkeypatch.setattr(art_tags, "_worker", w)
    app = Flask(__name__)
    app.register_blueprint(api.art_tags_api, url_prefix="/api")
    c = app.test_client()
    c.worker = w
    return c


def test_api_lexicon_and_status(client, out):
    make_bucket(out, "north-screen", ["20261001-000000-00000001.jpg"])
    lex = client.get("/api/art-tags/lexicon").get_json()
    assert lex["version"] == 1 and {"word": "still-life", "desc": lexicon.load()["facets"]["subject"]["still-life"]} \
        in lex["facets"]["subject"]
    assert set(lex["facets"]) == {"subject", "medium", "moods", "palette"}
    st = client.get("/api/art-tags/status").get_json()
    for k in ("running", "queued", "current", "done_session", "failed_session", "paused_until", "pause_reason",
              "spend_today_usd", "model", "coverage"):
        assert k in st
    assert st["coverage"]["north-screen"] == {"total": 1, "tagged": 0, "failed": 0}
    assert client.post("/api/art-tags/sweep").get_json() == {"queued": 1}


def test_api_edits_and_retag(client, out):
    make_bucket(out, "b", ["x.jpg"], sidecars={"x.jpg": {"prompt": "p"}})
    r = client.post("/api/buckets/b/tags/x.jpg", json={"add": ["Jazz Bar"], "remove": []})
    assert r.status_code == 200 and r.get_json()["tags"] == ["jazz-bar"]
    client.worker.enqueue("b", "x.jpg")
    assert client.worker.step() == "done"
    r = client.post("/api/buckets/b/tags/x.jpg", json={"remove": ["calm", "jazz-bar"]})
    j = r.get_json()
    assert j["tags"] == ["landscape", "painting", "dreamy"] and j["tagging"]["removed"] == ["calm"]
    assert json.loads((out / "b" / "x.jpg.json").read_text())["prompt"] == "p"
    assert client.post("/api/buckets/b/tags/x.jpg/retag").get_json() == {"queued": True}
    assert client.worker.retag == {("b", "x.jpg")} and list(client.worker.q) == [("b", "x.jpg")]


@pytest.mark.parametrize("url", ["/api/buckets/b/tags/nope.jpg", "/api/buckets/b/tags/..%2Fsecret.jpg",
                                 "/api/buckets/../tags/x.jpg", "/api/buckets/b/tags/x.jpg.json",
                                 "/api/buckets/.hidden/tags/x.jpg", "/api/buckets/b/tags/x.jpg/retag/x"])
def test_api_refuses_paths_outside_a_bucket(client, out, url):
    make_bucket(out, "b", ["x.jpg"])
    (out / "secret.jpg").write_bytes(b"x")
    assert client.post(url, json={"add": ["a"]}).status_code in (404, 405)


def test_api_bad_bodies(client, out):
    make_bucket(out, "b", ["x.jpg"])
    assert client.post("/api/buckets/b/tags/x.jpg", data="nope").status_code == 400
    assert client.post("/api/buckets/b/tags/x.jpg", json={"add": "word"}).status_code == 400
    assert client.post("/api/buckets/b/tags/x.jpg", json={"add": [1]}).status_code == 400
