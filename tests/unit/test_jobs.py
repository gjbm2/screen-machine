"""Generation job tracker (routes/jobs, docs/JOBS.md): lifecycle, pub/sub, store, progress, API, and the
RunPod lifecycle in routes.generate.start with RunPod mocked. No network."""
import json
import time

import pytest

from routes.jobs import runpod_api, set_tracker
from routes.jobs.progress import ComfyProgress, normalise_stages
from routes.jobs.estimate import Estimator
from routes.jobs.store import JobStore
from routes.jobs.tracker import JobTracker, RESTART_ERROR


@pytest.fixture
def store(tmp_path):
    s = JobStore(str(tmp_path / "jobs.db"))
    yield s
    s.close()


@pytest.fixture
def remote_cancels():
    return []


@pytest.fixture
def tracker(store, remote_cancels):
    def cancel(ep, rid):
        remote_cancels.append((ep, rid))
        return True
    t = JobTracker(store=store, progress_interval=0.1, persist_interval=0.5, cancel_remote=cancel)
    set_tracker(t)
    yield t
    set_tracker(None)


def wait_for(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# ------------------------------------------------------------------ tracker lifecycle

def test_job_has_every_contract_field(tracker):
    job = tracker.create(prompt="a cat", source="generator", target="north-screen",
                         target_name="North screen", batch_id="b1", batch_index=0,
                         placeholder_id="p1")
    expected = {"id", "batch_id", "batch_index", "placeholder_id", "source", "kind", "target",
                "target_name", "prompt", "refined_prompt", "workflow", "refiner", "status", "message",
                "stages", "stage_index", "percent", "error", "runpod_job_id", "endpoint_id",
                "created_at", "submitted_at", "started_at", "generating_at", "publishing_at",
                "finished_at", "updated_at", "duration_s", "cost_gbp", "seed", "result_url",
                "published_path", "cancel_requested", "estimate"}
    assert set(job) == expected
    assert job["id"].startswith("job_") and len(job["id"]) == 16
    assert job["status"] == "preparing" and job["stages"] == [] and job["cancel_requested"] is False


def test_unknown_source_becomes_api(tracker):
    assert tracker.create(prompt="x", source="bogus")["source"] == "api"


def test_lifecycle_to_completed(tracker):
    job_id = tracker.create(prompt="x")["id"]
    tracker.update(job_id, status="queued", runpod_job_id="rp-1", endpoint_id="ep")
    tracker.update(job_id, status="running")
    tracker.update(job_id, status="publishing")
    done = tracker.complete(job_id, result_url="http://r/1.jpg", seed=5, cost_gbp=0.01)
    assert done["status"] == "completed" and done["percent"] == 100
    assert done["submitted_at"] and done["started_at"] and done["finished_at"]
    assert done["duration_s"] is not None and done["result_url"] == "http://r/1.jpg"


def test_terminal_state_is_final(tracker):
    job_id = tracker.create(prompt="x")["id"]
    tracker.fail(job_id, "boom")
    late = tracker.complete(job_id, result_url="u")
    assert late["status"] == "failed" and late["error"] == "boom" and late["result_url"] is None
    tracker.progress(job_id, percent=50)
    assert tracker.get(job_id)["percent"] is None


def test_unknown_status_rejected(tracker):
    job_id = tracker.create(prompt="x")["id"]
    with pytest.raises(ValueError):
        tracker.update(job_id, status="weird")


def test_cancel_marks_cancelled_and_cancels_runpod(tracker, remote_cancels):
    job_id = tracker.create(prompt="x")["id"]
    tracker.update(job_id, status="queued", runpod_job_id="rp-9", endpoint_id="ep-9")
    job = tracker.cancel(job_id)
    assert job["status"] == "cancelled" and job["cancel_requested"] is True
    assert tracker.is_cancelled(job_id)
    assert wait_for(lambda: remote_cancels == [("ep-9", "rp-9")])


def test_cancel_before_submit_needs_no_remote_cancel(tracker, remote_cancels):
    job_id = tracker.create(prompt="x")["id"]
    assert tracker.cancel(job_id)["status"] == "cancelled"
    time.sleep(0.05)
    assert remote_cancels == []


def test_cancel_all_counts_active_only(tracker):
    a = tracker.create(prompt="a")["id"]
    tracker.create(prompt="b")
    tracker.fail(a, "x")
    assert tracker.cancel_all() == 1
    assert tracker.active_jobs() == []


# ------------------------------------------------------------------ pub/sub

def test_subscribe_target_filter(tracker):
    north = tracker.subscribe(target="north-screen")
    everyone = tracker.subscribe()
    tracker.create(prompt="n", target="north-screen")
    tracker.create(prompt="s", target="south-screen")
    assert north.get(0.5)["target"] == "north-screen"
    assert north.get(0.05) is None
    assert [everyone.get(0.5)["prompt"], everyone.get(0.5)["prompt"]] == ["n", "s"]
    north.close()
    everyone.close()


def test_status_changes_always_published_progress_throttled(tracker):
    job_id = tracker.create(prompt="x")["id"]
    tracker.update(job_id, status="running")
    sub = tracker.subscribe()
    time.sleep(0.15)
    for pct in range(1, 51):
        tracker.progress(job_id, percent=pct)
    tracker.update(job_id, status="publishing")
    events = []
    while True:
        job = sub.get(0.3)
        if job is None:
            break
        events.append(job)
    statuses = [e["status"] for e in events]
    assert "publishing" in statuses
    progress_events = [e for e in events if e["status"] == "running"]
    assert len(progress_events) == 1        # 50 updates in ~0 s at 10/s max: the first goes out
    assert events[-1]["percent"] == 50      # and the status change carries the latest
    sub.close()


def test_progress_trailing_publish_carries_latest(tracker):
    job_id = tracker.create(prompt="x")["id"]
    sub = tracker.subscribe()
    sub.get(0.1)   # the create
    tracker.progress(job_id, percent=10)
    tracker.progress(job_id, percent=20)
    tracker.progress(job_id, percent=30)
    seen = []
    while True:
        job = sub.get(0.4)
        if job is None:
            break
        seen.append(job["percent"])
    assert seen[0] == 10 and seen[-1] == 30 and len(seen) == 2
    sub.close()


def test_slow_subscriber_drops_oldest_and_never_blocks(tracker):
    sub = tracker.subscribe(maxlen=5)
    start = time.monotonic()
    for i in range(50):
        tracker.create(prompt=f"p{i}")
    assert time.monotonic() - start < 2
    got = [sub.get(0.1)["prompt"] for _ in range(5)]
    assert got == [f"p{i}" for i in range(45, 50)]
    assert sub.dropped == 45
    sub.close()


def test_closed_subscription_gets_nothing(tracker):
    sub = tracker.subscribe()
    sub.close()
    tracker.create(prompt="x")
    assert sub.get(0.05) is None


def test_snapshot_active_plus_recently_finished(tracker):
    active = tracker.create(prompt="a", target="t1")["id"]
    done = tracker.create(prompt="b", target="t1")["id"]
    tracker.fail(done, "x")
    old = tracker.create(prompt="c", target="t1")["id"]
    tracker.fail(old, "x")
    tracker._jobs[old]["finished_at"] = time.time() - 120
    ids = {j["id"] for j in tracker.snapshot(target="t1")}
    assert ids == {active, done}
    assert tracker.snapshot(target="other") == []


# ------------------------------------------------------------------ store, retention, reconcile

def test_store_persists_status_changes(store, tracker):
    job_id = tracker.create(prompt="persist me")["id"]
    tracker.update(job_id, status="queued", runpod_job_id="rp", endpoint_id="ep")
    assert store.get(job_id)["status"] == "queued"
    tracker.fail(job_id, "nope")
    row = store.get(job_id)
    assert row["status"] == "failed" and row["error"] == "nope"


def test_store_progress_persist_is_throttled(store, tracker):
    job_id = tracker.create(prompt="x")["id"]
    tracker.update(job_id, status="running")
    time.sleep(0.6)
    tracker.progress(job_id, percent=10)        # persisted (interval passed)
    assert store.get(job_id)["percent"] == 10
    tracker.progress(job_id, percent=20)        # within interval: memory only
    time.sleep(0.15)
    assert tracker.get(job_id)["percent"] == 20
    assert store.get(job_id)["percent"] == 10


def test_store_query_filters_and_paging(store):
    now = time.time()
    for i in range(5):
        store.upsert({"id": f"j{i}", "created_at": now + i, "status": "completed" if i % 2 else "running",
                      "target": "north-screen" if i < 3 else "south-screen",
                      "source": "alexa" if i == 4 else "generator", "prompt": f"red fox {i}"})
    assert [j["id"] for j in store.query(limit=2)] == ["j4", "j3"]
    assert [j["id"] for j in store.query(limit=2, before=now + 3)] == ["j2", "j1"]
    assert {j["id"] for j in store.query(state="active")} == {"j0", "j2", "j4"}
    assert {j["id"] for j in store.query(state="finished")} == {"j1", "j3"}
    assert {j["id"] for j in store.query(target="south-screen")} == {"j3", "j4"}
    assert [j["id"] for j in store.query(source="alexa")] == ["j4"]
    assert [j["id"] for j in store.query(q="fox 2")] == ["j2"]
    assert store.query(q="100%") == []


def test_store_retention_prune(store):
    now = time.time()
    store.upsert({"id": "old", "created_at": now - 91 * 86400, "status": "completed", "prompt": ""})
    store.upsert({"id": "new", "created_at": now - 89 * 86400, "status": "completed", "prompt": ""})
    assert store.prune(now=now) == 1
    assert store.get("old") is None and store.get("new")


def test_store_survives_reopen(tmp_path):
    path = str(tmp_path / "re.db")
    s = JobStore(path)
    s.upsert({"id": "x", "created_at": 1.0, "status": "queued", "prompt": "p"})
    s.close()
    s2 = JobStore(path)
    assert s2.get("x")["status"] == "queued"
    s2.close()


def test_reconcile_fails_orphans_and_cancels_runpod(store, remote_cancels):
    before = JobTracker(store=store, cancel_remote=lambda ep, rid: True)
    orphan = before.create(prompt="o")["id"]
    before.update(orphan, status="running", runpod_job_id="rp-o", endpoint_id="ep-o")
    preparing = before.create(prompt="p")["id"]
    done = before.create(prompt="d")["id"]
    before.complete(done)

    after = JobTracker(store=store, cancel_remote=lambda ep, rid: remote_cancels.append((ep, rid)) or True)
    assert after.reconcile_on_startup(cancel_in_background=False) == 2
    for job_id in (orphan, preparing):
        row = store.get(job_id)
        assert row["status"] == "failed" and row["error"] == RESTART_ERROR and row["finished_at"]
    assert store.get(done)["status"] == "completed"
    assert remote_cancels == [("ep-o", "rp-o")]


def test_reconcile_background_cancel_does_not_block(store):
    before = JobTracker(store=store, cancel_remote=lambda ep, rid: True)
    job_id = before.create(prompt="o")["id"]
    before.update(job_id, status="queued", runpod_job_id="rp", endpoint_id="ep")
    after = JobTracker(store=store, cancel_remote=lambda ep, rid: time.sleep(2) or True)
    t0 = time.monotonic()
    assert after.reconcile_on_startup() == 1
    assert time.monotonic() - t0 < 0.5


def test_list_overlays_fresher_memory(store, tracker):
    job_id = tracker.create(prompt="x")["id"]
    tracker.update(job_id, status="running")
    time.sleep(0.6)
    tracker.progress(job_id, percent=10)
    tracker.progress(job_id, percent=77)
    jobs, next_before = tracker.list(state="active")
    assert jobs[0]["percent"] == 77 and next_before is None


# ------------------------------------------------------------------ progress weighting

def test_normalise_stages():
    assert normalise_stages(None) == [{"name": "Rendering", "weight": 100}]
    assert normalise_stages(["A", "B"]) == [{"name": "A", "weight": 50}, {"name": "B", "weight": 50}]


def frame(value, max_val):
    return {"type": "progress", "data": {"value": value, "max": max_val}}


def test_weighted_progress_and_stage_advance():
    p = ComfyProgress([{"name": "Rendering", "weight": 90}, {"name": "Upscaling", "weight": 10}])
    assert p.feed(frame(14, 28)) == {"stage_index": 0, "percent": 45, "message": "Rendering 14/28"}
    assert p.feed(frame(14, 28)) == {}                                  # repeat: nothing new
    assert p.feed(frame(28, 28))["percent"] == 90
    out = p.feed(frame(1, 4))                                           # value dropped: next stage
    assert out["stage_index"] == 1 and out["percent"] == 92 and out["message"] == "Upscaling 1/4"
    assert p.feed(frame(4, 4))["percent"] == 100
    assert p.feed(frame(1, 4))["stage_index"] == 1                      # never past the last stage


def test_stage_advances_when_max_changes():
    p = ComfyProgress([{"name": "A", "weight": 50}, {"name": "B", "weight": 50}])
    p.feed(frame(5, 10))
    out = p.feed(frame(6, 20))
    assert out["stage_index"] == 1 and out["percent"] == 65


def test_status_frames():
    p = ComfyProgress()
    status = lambda n: {"type": "status", "data": {"status": {"exec_info": {"queue_remaining": n}}}}
    assert p.feed(status(1)) == {"message": "Starting the workflow"}
    assert p.feed(status(0)) == {}                                      # nothing ran yet
    p.feed(frame(1, 2))
    assert p.feed(status(0)) == {"message": "Finalising"}
    assert p.feed({"type": "executing", "data": {}}) == {}
    assert p.feed("junk") == {}


def test_worker_frames_drive_the_job(tracker):
    job_id = tracker.create(prompt="x")["id"]
    tracker.set_stages(job_id, [{"name": "Rendering", "weight": 90}, {"name": "Upscaling", "weight": 10}])
    tracker.update(job_id, status="queued", runpod_job_id="rp-w", endpoint_id="ep")
    assert tracker.on_worker_frame("rp-w", frame(10, 20)) is True
    job = tracker.get(job_id)
    assert job["status"] == "running" and job["stages"] == ["Rendering", "Upscaling"]
    assert wait_for(lambda: tracker.get(job_id)["percent"] == 45)
    assert tracker.on_worker_frame("someone-else", frame(1, 2)) is False


def test_phase_timestamps(tracker):
    job_id = tracker.create(prompt="x")["id"]
    tracker.set_stages(job_id, [{"name": "Rendering", "weight": 100}])
    tracker.update(job_id, status="queued", runpod_job_id="rp-p", endpoint_id="ep")
    status_frame = {"type": "status", "data": {"status": {"exec_info": {"queue_remaining": 1}}}}
    with tracker.subscribe() as sub:
        tracker.on_worker_frame("rp-p", status_frame)    # worker up, models still loading
        job = tracker.get(job_id)
        assert job["status"] == "running" and job["started_at"] and job["generating_at"] is None
        tracker.on_worker_frame("rp-p", frame(1, 20))    # first step: generating begins
        first = tracker.get(job_id)["generating_at"]
        assert first and first >= job["started_at"]
        events = []
        while (e := sub.get(timeout=0.05)) is not None:
            events.append(e)
        # published at once (not throttled away) and persisted, so history can learn from it
        assert events[-1]["generating_at"] == first and events[-1]["percent"] == 5
        assert tracker.store.get(job_id)["generating_at"] == first
    tracker.on_worker_frame("rp-p", frame(2, 20))
    assert tracker.get(job_id)["generating_at"] == first
    assert tracker.update(job_id, status="publishing")["publishing_at"]
    done = tracker.complete(job_id)
    assert done["generating_at"] == first and done["publishing_at"] <= done["finished_at"]


def test_phase_timestamps_are_tracker_owned(tracker):
    job_id = tracker.create(prompt="x", generating_at=1.0, publishing_at=2.0, estimate={})["id"]
    tracker.update(job_id, generating_at=5.0, publishing_at=6.0, estimate={"x": 1})
    job = tracker.get(job_id)
    assert job["generating_at"] is None and job["publishing_at"] is None
    assert job["estimate"]["basis"] == "default"


def test_old_history_rows_get_new_fields(store, tracker):
    store.upsert({"id": "job_old", "created_at": 1.0, "status": "completed", "prompt": "old"})
    job = tracker.get("job_old")
    assert job["generating_at"] is None and job["estimate"] is None and job["prompt"] == "old"
    assert tracker.list()[0][0]["publishing_at"] is None


# ------------------------------------------------------------------ learned time estimate

_seq = iter(range(10_000))


def past_job(store, workflow="wf-a", prepare=2.0, wait=10.0, generate=20.0, publish=1.0,
             created=None, status="completed", generating=True):
    """A finished job in the history with the given phase lengths (wait = queue + cold start)."""
    n = next(_seq)
    created = created if created is not None else 1_000_000.0 + n * 1000
    job = {"id": f"job_hist{n:06d}", "status": status, "workflow": workflow, "prompt": "",
           "created_at": created, "submitted_at": created + prepare,
           "started_at": created + prepare + wait / 2,
           "generating_at": created + prepare + wait if generating else None,
           "publishing_at": created + prepare + wait + generate,
           "finished_at": created + prepare + wait + generate + publish}
    store.upsert(job)
    return job


def test_estimate_defaults_with_no_history(store):
    est = Estimator(store).estimate("wf-a")
    assert est == {"prepare_s": 4.0, "generate_s": 30.0, "publish_s": 3.0, "samples": 0,
                   "basis": "default"}


def test_estimate_learns_per_workflow_median(store):
    for g in (10, 12, 50):
        past_job(store, "wf-a", generate=g, prepare=3, publish=2)
    past_job(store, "wf-b", generate=99)
    est = Estimator(store).estimate("wf-a")
    assert est == {"prepare_s": 3.0, "generate_s": 12.0, "publish_s": 2.0, "samples": 3,
                   "basis": "workflow"}


def test_estimate_falls_back_to_all_workflows(store):
    for g in (10, 20, 30):
        past_job(store, "wf-b", generate=g)
    past_job(store, "wf-a", generate=100)
    est = Estimator(store)
    assert est.estimate("wf-a")["basis"] == "all" and est.estimate("wf-a")["generate_s"] == 25.0
    assert est.estimate("wf-a")["samples"] == 4
    assert est.estimate(None)["basis"] == "all"


def test_cold_starts_do_not_move_the_estimate(store):
    for _ in range(5):
        past_job(store, "wf-a", wait=3, generate=20)
    warm = Estimator(store).estimate("wf-a")
    for _ in range(5):
        past_job(store, "wf-a", wait=900, generate=20)   # 15-minute cold boots
    cold = Estimator(store).estimate("wf-a")
    assert warm["generate_s"] == cold["generate_s"] == 20.0
    assert set(cold) == {"prepare_s", "generate_s", "publish_s", "samples", "basis"}  # no queue figure


def test_jobs_without_generating_at_are_not_samples(store):
    for _ in range(10):
        past_job(store, "wf-a", generating=False)
    past_job(store, "wf-a", status="failed")
    past_job(store, "wf-a", status="cancelled")
    assert Estimator(store).estimate("wf-a")["basis"] == "default"
    for _ in range(3):
        past_job(store, "wf-a", generate=7)
    est = Estimator(store).estimate("wf-a")
    assert est["basis"] == "workflow" and est["samples"] == 3 and est["generate_s"] == 7.0


def test_estimate_ignores_outliers_and_old_jobs(store):
    for _ in range(3):
        past_job(store, "wf-a", generate=-5)
        past_job(store, "wf-a", generate=31 * 60)
    assert Estimator(store).estimate("wf-a")["basis"] == "default"
    for _ in range(30):
        past_job(store, "wf-a", generate=100)
    for _ in range(30):
        past_job(store, "wf-a", generate=10)              # newer: only these 30 count
    est = Estimator(store).estimate("wf-a")
    assert est["generate_s"] == 10.0 and est["samples"] == 30
    for _ in range(3):
        past_job(store, "wf-c", publish=-1, prepare=40 * 60, generate=8)
    est = Estimator(store).estimate("wf-c")              # bad phases fall back phase by phase
    assert est["generate_s"] == 8.0 and est["prepare_s"] == 4.0 and est["publish_s"] == 3.0


def test_estimate_is_cached_per_workflow(store):
    now = [0.0]
    est = Estimator(store, cache_s=60, clock=lambda: now[0])
    first = est.estimate("wf-a")
    scans = est.scans
    for _ in range(3):
        past_job(store, "wf-a", generate=9)
    assert est.estimate("wf-a") == first and est.scans == scans   # cached: no history scan
    first["generate_s"] = -1                                       # callers get copies
    assert est.estimate("wf-a")["generate_s"] == 30.0
    now[0] = 61
    assert est.estimate("wf-a")["generate_s"] == 9.0 and est.scans > scans


def test_estimate_survives_a_store_error(store):
    class Broken:
        def recent_completed(self, *a, **k):
            raise RuntimeError("db gone")
    assert Estimator(Broken()).estimate("wf-a")["basis"] == "default"


def test_tracker_sets_estimate_and_redoes_it_when_workflow_known(store, tracker):
    for _ in range(3):
        past_job(store, "wf-a", generate=11)
    job = tracker.create(prompt="x")
    assert job["estimate"]["basis"] == "all"
    updated = tracker.update(job["id"], workflow="wf-a")
    assert updated["estimate"] == {"prepare_s": 2.0, "generate_s": 11.0, "publish_s": 1.0,
                                   "samples": 3, "basis": "workflow"}
    assert tracker.create(prompt="y", workflow="wf-zz")["estimate"]["basis"] == "all"
    scans = tracker.estimator.scans
    tracker.update(job["id"], status="queued", message="Waiting")   # no workflow change: no lookup
    assert tracker.estimator.scans == scans


# ------------------------------------------------------------------ RunPod error text

def test_paused_endpoint_error(monkeypatch):
    monkeypatch.setattr(runpod_api, "endpoint_name", lambda ep: "flux-dev")
    text = runpod_api.describe_http_error("ep", 409, {"error": "Endpoint is paused", "status": "ENDPOINT_PAUSED"})
    assert text == "The flux-dev endpoint is paused on RunPod"


def test_other_http_errors_are_plain():
    assert runpod_api.describe_http_error("ep", 401, "") == "RunPod rejected the API key"
    assert "HTTP 503" in runpod_api.describe_http_error("ep", 503, "<html>")
    assert runpod_api.describe_http_error("ep", 400, {"error": "bad input"}) == \
        "RunPod refused the job (HTTP 400): bad input"


# ------------------------------------------------------------------ API

@pytest.fixture
def client(tracker):
    from flask import Flask
    from routes.jobs.api import jobs_api
    app = Flask(__name__)
    app.register_blueprint(jobs_api, url_prefix="/api")
    return app.test_client()


def test_api_list_get_cancel(client, tracker):
    a = tracker.create(prompt="alpha", target="north-screen", source="generator")["id"]
    b = tracker.create(prompt="beta", target="south-screen", source="alexa")["id"]
    body = client.get("/api/jobs?state=active").get_json()
    assert [j["id"] for j in body["jobs"]] == [b, a] and body["next_before"] is None
    assert [j["id"] for j in client.get("/api/jobs?target=north-screen").get_json()["jobs"]] == [a]
    assert [j["id"] for j in client.get("/api/jobs?source=alexa").get_json()["jobs"]] == [b]
    assert [j["id"] for j in client.get("/api/jobs?q=alp").get_json()["jobs"]] == [a]
    page = client.get("/api/jobs?limit=1").get_json()
    assert len(page["jobs"]) == 1 and page["next_before"] is not None
    assert client.get(f"/api/jobs/{a}").get_json()["prompt"] == "alpha"
    assert client.get(f"/api/jobs/{a}").get_json()["estimate"]["basis"] == "default"
    assert all("generating_at" in j and j["estimate"] for j in body["jobs"])
    assert client.get("/api/jobs/job_nope").status_code == 404
    assert client.post(f"/api/jobs/{a}/cancel").get_json()["status"] == "cancelled"
    assert client.post("/api/jobs/job_nope/cancel").status_code == 404
    assert client.get("/api/jobs?state=finished").get_json()["jobs"][0]["id"] == a


def test_api_cancel_all_and_alias(client, tracker):
    tracker.create(prompt="1")
    tracker.create(prompt="2")
    assert client.post("/api/jobs/cancel-all").get_json() == {"cancelled": 2}
    tracker.create(prompt="3")
    assert client.post("/api/cancel_all_jobs").get_json() == {"cancelled": 1}
    assert client.get("/api/cancel_all_jobs").get_json() == {"cancelled": 0}


def parse_sse(text):
    events = []
    for block in text.split("\n\n"):
        name, data = None, None
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
            elif line.startswith(": ping"):
                name = "ping"
            elif line.startswith("retry: "):
                name = "retry"
        if name:
            events.append((name, data))
    return events


def test_event_stream_frames(tracker):
    from routes.jobs.api import event_stream
    tracker.create(prompt="before", target="t")
    gen = event_stream(tracker, target="t", ping_s=0.1, stream_s=0.5)
    assert next(gen) == "retry: 3000\n\n"
    name, snap = parse_sse(next(gen))[0]
    assert name == "snapshot" and [j["prompt"] for j in snap["jobs"]] == ["before"] and snap["server_time"]
    assert snap["jobs"][0]["estimate"]["generate_s"] == 30.0 and "publishing_at" in snap["jobs"][0]
    tracker.create(prompt="other target", target="u")
    tracker.create(prompt="after", target="t")
    rest = parse_sse("".join(gen))   # runs to the 0.5 s end
    jobs = [d["prompt"] for n, d in rest if n == "job"]
    assert all(d["estimate"]["basis"] == "default" for n, d in rest if n == "job")
    assert jobs == ["after"] and ("ping", None) in rest
    assert tracker._subs == set()    # closed at the end


def test_stream_route_headers(client):
    from routes.jobs import api
    resp = client.get("/api/jobs/stream", buffered=False)
    assert resp.mimetype == "text/event-stream"
    assert resp.headers["Cache-Control"] == "no-cache" and resp.headers["X-Accel-Buffering"] == "no"
    resp.close()


# ------------------------------------------------------------------ lifecycle in routes.generate.start

class FakeRunPod:
    """Stands in for routes.jobs.runpod_api inside routes.generate."""
    RunPodError = runpod_api.RunPodError

    def __init__(self, statuses, submit_error=None):
        self.statuses = list(statuses)
        self.submit_error = submit_error
        self.cancelled = []
        self.payload = None

    def submit(self, endpoint_id, payload, timeout=60):
        if self.submit_error:
            raise self.submit_error
        self.payload = payload
        return "rp-123"

    def status(self, endpoint_id, job_id, timeout=10):
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def cancel(self, endpoint_id, job_id, timeout=10):
        self.cancelled.append(job_id)
        return True


@pytest.fixture
def gen(monkeypatch, tracker):
    import routes.generate as generate
    import routes.generate_handler as handler
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("RUNPOD_API_KEY", "x")
    monkeypatch.setenv("RUNPOD_ID", "default-ep")
    monkeypatch.setattr(handler, "calculate_generation_cost", lambda *a, **k: 0.02)
    return generate


def run_start(gen, fake, monkeypatch, tracker, **kwargs):
    monkeypatch.setattr(gen, "runpod_api", fake)
    job_id = tracker.create(prompt="a red fox", source="generator")["id"]
    kwargs.setdefault("poll_interval", 0.01)
    try:
        result = gen.start(prompt="a red fox", workflow="flux1-dev-scale-l.json", job_id=job_id, **kwargs)
    except ValueError:
        result = None
    return tracker.get(job_id), result


def test_start_completes(gen, monkeypatch, tracker):
    fake = FakeRunPod([{"status": "IN_QUEUE"}, {"status": "IN_PROGRESS"},
                       {"status": "COMPLETED", "output": {"status": "success", "message": "http://r/out.jpg"}}])
    job, result = run_start(gen, fake, monkeypatch, tracker)
    assert job["status"] == "completed" and job["result_url"] == "http://r/out.jpg"
    assert job["runpod_job_id"] == "rp-123" and job["endpoint_id"] == "k98wg36ip597n0"
    assert job["stages"] == ["Rendering", "Upscaling"] and job["kind"] == "image"
    assert job["cost_gbp"] == 0.02 and job["seed"] is not None and job["refined_prompt"] == "a red fox"
    assert job["started_at"] and job["submitted_at"] and job["publishing_at"]
    assert job["generating_at"] is None          # no relay frames: not a learning sample
    assert job["estimate"]["basis"] == "default"
    assert result["job_id"] == job["id"]


def test_start_paused_endpoint(gen, monkeypatch, tracker):
    fake = FakeRunPod([], submit_error=runpod_api.RunPodError("The flux endpoint is paused on RunPod"))
    job, _ = run_start(gen, fake, monkeypatch, tracker)
    assert job["status"] == "failed" and job["error"] == "The flux endpoint is paused on RunPod"


def test_start_runpod_failed(gen, monkeypatch, tracker):
    fake = FakeRunPod([{"status": "FAILED", "error": "CUDA out of memory\ntraceback..."}])
    job, _ = run_start(gen, fake, monkeypatch, tracker)
    assert job["status"] == "failed" and job["error"] == "The job failed on RunPod: CUDA out of memory"


def test_start_runpod_timed_out(gen, monkeypatch, tracker):
    job, _ = run_start(gen, FakeRunPod([{"status": "TIMED_OUT"}]), monkeypatch, tracker)
    assert job["status"] == "failed" and job["error"] == "RunPod timed the job out"


def test_start_local_timeout_cancels_runpod(gen, monkeypatch, tracker):
    monkeypatch.setattr(gen, "RUNPOD_TIMEOUT_S", 0.05)
    fake = FakeRunPod([{"status": "IN_QUEUE"}])
    job, _ = run_start(gen, fake, monkeypatch, tracker)
    assert job["status"] == "failed" and job["error"].startswith("Timed out")
    assert fake.cancelled == ["rp-123"]


def test_start_worker_error_output(gen, monkeypatch, tracker):
    fake = FakeRunPod([{"status": "COMPLETED", "output": {"status": "error", "message": "bad node"}}])
    job, _ = run_start(gen, fake, monkeypatch, tracker)
    assert job["status"] == "failed" and job["error"] == "The worker sent no result: bad node"


def test_start_cancel_while_polling(gen, monkeypatch, tracker):
    import threading
    fake = FakeRunPod([{"status": "IN_QUEUE"}])
    monkeypatch.setattr(gen, "runpod_api", fake)
    job_id = tracker.create(prompt="x")["id"]
    t = threading.Thread(target=lambda: pytest.raises(ValueError, gen.start, prompt="x",
                                                      workflow="flux1-dev-scale-l.json",
                                                      job_id=job_id, poll_interval=0.01))
    t.start()
    assert wait_for(lambda: tracker.get(job_id)["status"] == "queued")
    tracker.cancel(job_id)
    t.join(2)
    assert not t.is_alive()
    assert tracker.get(job_id)["status"] == "cancelled" and "rp-123" in fake.cancelled


def test_start_cancelled_before_submit(gen, monkeypatch, tracker):
    fake = FakeRunPod([{"status": "IN_QUEUE"}])
    monkeypatch.setattr(gen, "runpod_api", fake)
    job_id = tracker.create(prompt="x")["id"]
    tracker.cancel(job_id)
    with pytest.raises(ValueError):
        gen.start(prompt="x", workflow="flux1-dev-scale-l.json", job_id=job_id)
    assert fake.payload is None and tracker.get(job_id)["status"] == "cancelled"


def test_start_bad_workflow_fails_the_job(gen, monkeypatch, tracker):
    monkeypatch.setattr(gen, "runpod_api", FakeRunPod([]))
    job_id = tracker.create(prompt="x")["id"]
    with pytest.raises(ValueError):
        gen.start(prompt="x", workflow="no-such-workflow.json", job_id=job_id)
    assert tracker.get(job_id)["status"] == "failed"


def test_handler_makes_jobs_before_refining(monkeypatch, tracker):
    import routes.generate as generate
    import routes.generate_handler as handler
    seen = {}

    def fake_start(**kwargs):
        seen.setdefault("calls", []).append(kwargs)
        tracker.complete(kwargs["job_id"])
        return {"job_id": kwargs["job_id"]}

    def failing_refine(*a, **k):
        seen["at_refine"] = [j["status"] for j in tracker.active_jobs()]
        raise RuntimeError("OpenAI is down")

    monkeypatch.setattr(generate, "start", fake_start)
    monkeypatch.setattr(handler, "_refine", failing_refine)
    out = []
    with pytest.raises(RuntimeError):
        handler.handle_image_generation(
            {"data": {"prompt": "x", "refiner": "none", "targets": ["north-screen", "south-screen"],
                      "batch_id": "B", "batch": 2,
                      "placeholders": [{"batch_index": 4, "placeholder_id": "p4"},
                                       {"batch_index": 5, "placeholder_id": "p5"}]}},
            wait=True, source="generator", job_ids_out=out)
    assert seen["at_refine"] == ["preparing"] * 4
    jobs = [tracker.get(i) for i in out]
    assert len(jobs) == 4 and all(j["status"] == "failed" for j in jobs)
    assert jobs[0]["error"] == "Refining the prompt failed: OpenAI is down"
    assert [j["placeholder_id"] for j in jobs] == ["p4", "p5", "p4", "p5"]
    assert [j["batch_index"] for j in jobs] == [4, 5, 4, 5]
    assert {j["batch_id"] for j in jobs} == {"B"} and {j["source"] for j in jobs} == {"generator"}
    assert [j["target"] for j in jobs] == ["north-screen", "north-screen", "south-screen", "south-screen"]
    assert jobs[0]["target_name"] == "North screen"

    # and the happy path passes each thread its own job id and the batch id
    monkeypatch.setattr(handler, "_refine", lambda *a, **k: ("flux1-dev-scale-l.json", "refined", "final"))
    out = []
    results = handler.handle_image_generation(
        {"data": {"prompt": "x", "targets": ["north-screen"], "batch_id": "C"}},
        wait=True, source="scheduler", job_ids_out=out)
    assert [r["job_id"] for r in results] == out
    call = seen["calls"][-1]
    assert call["job_id"] == out[0] and call["batch_id"] == "C" and call["prompt"] == "final"
    assert tracker.get(out[0])["source"] == "scheduler"
