"""HTTP + SSE API for generation jobs (docs/JOBS.md). Open like the rest of the generation API."""
import json
import time

from flask import Blueprint, Response, jsonify, request

from routes import auth
from routes.jobs import get_tracker

jobs_api = Blueprint("jobs_api", __name__)

PING_S = 15
STREAM_S = 300
RETRY_MS = 3000


def _float_arg(name):
    try:
        value = request.args.get(name)
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


@jobs_api.route("/jobs", methods=["GET"])
def list_jobs():
    state = request.args.get("state", "all")
    if state not in ("active", "finished", "all"):
        state = "all"
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
    jobs, next_before = get_tracker().list(
        state=state, limit=limit, before=_float_arg("before"),
        target=request.args.get("target") or None, source=request.args.get("source") or None,
        q=request.args.get("q") or None)
    # each person sees their own generator jobs and the house's for screens they may use (docs/ACCOUNTS.md)
    everyone = request.args.get("everyone") not in ("0", "false")
    jobs = [j for j in jobs if auth.can_see_job(j, everyone=everyone)]
    return jsonify({"jobs": jobs, "next_before": next_before})


@jobs_api.route("/jobs/warm", methods=["POST"])
def warm_gpu():
    """Warm a GPU for a workflow before the picture is asked for (warm.py)."""
    from routes.jobs import warm
    body = request.get_json(silent=True) or {}
    return jsonify({"result": warm.warm(str(body.get("workflow") or "auto"))})


@jobs_api.route("/jobs/costs", methods=["GET"])
def job_costs():
    """What generation cost: RunPod's bill against the jobs, per day, workflow, source and endpoint (billing.py)."""
    from routes.jobs import billing
    try:
        days = max(1, min(90, int(request.args.get("days", 30))))
    except ValueError:
        days = 30
    return jsonify(billing.summary(get_tracker(), days=days))


@jobs_api.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id):
    job = get_tracker().get(job_id)
    return (jsonify(job), 200) if job and auth.can_see_job(job) else (jsonify({"error": "unknown job"}), 404)


@jobs_api.route("/jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id):
    seen = get_tracker().get(job_id)
    if not seen or not auth.can_see_job(seen):
        return jsonify({"error": "unknown job"}), 404
    job = get_tracker().cancel(job_id)
    return (jsonify(job), 200) if job else (jsonify({"error": "unknown job"}), 404)


@jobs_api.route("/jobs/cancel-all", methods=["POST"])
@jobs_api.route("/cancel_all_jobs", methods=["GET", "POST"])
def cancel_all_jobs():
    return jsonify({"cancelled": get_tracker().cancel_all()})


def _event(name, data):
    return f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"


# What a screen's page shows of a job on that screen (its pill: a ring and one stage word). Nothing about what was
# asked for or made.
ON_SCREEN = ("id", "source", "kind", "target", "target_name", "status", "stages", "stage_index", "percent",
             "created_at", "submitted_at", "started_at", "generating_at", "publishing_at", "finished_at", "updated_at",
             "cancel_requested", "estimate")


def on_screen(job):
    return {k: job[k] for k in ON_SCREEN if k in job}


def event_stream(tracker, target=None, ping_s=PING_S, stream_s=STREAM_S, visible=lambda job: True, shown=lambda job: job):
    """SSE frames: retry + snapshot, then each job change, a ping comment every ping_s, and the end
    after stream_s (EventSource reconnects and gets a fresh snapshot). Only jobs `visible` passes are sent, each as
    `shown` gives it."""
    sub = tracker.subscribe(target=target)
    try:
        yield f"retry: {RETRY_MS}\n\n"
        jobs = [shown(j) for j in tracker.snapshot(target) if visible(j)]
        yield _event("snapshot", {"jobs": jobs, "server_time": time.time()})
        end = time.monotonic() + stream_s
        next_ping = time.monotonic() + ping_s
        while True:
            now = time.monotonic()
            if now >= end:
                return
            if now >= next_ping:
                yield ": ping\n\n"
                next_ping = now + ping_s
            job = sub.get(timeout=max(0.05, min(next_ping, end) - now))
            if job is not None and visible(job):
                yield _event("job", shown(job))
    finally:
        sub.close()


@jobs_api.route("/jobs/stream", methods=["GET"])
def stream_jobs():
    target = request.args.get("target") or None
    everyone = request.args.get("everyone") not in ("0", "false")
    # the stream outlives the request, so who is watching is settled now (docs/ACCOUNTS.md)
    visible = auth.job_visibility(everyone=everyone)
    shown = lambda job: job
    if target:
        # One screen's feed is that screen's own page (its pill), and a screen is never signed in: it gets every job
        # on it, whoever started it, down to what the pill draws (owner, 19 Nov: a picture being fitted to devtest
        # showed no progress there; since sign-in the TVs had lost the pill for generator jobs too).
        mine = visible
        visible, shown = (lambda job: True), (lambda job: job if mine(job) else on_screen(job))
    return Response(event_stream(get_tracker(), target, visible=visible, shown=shown), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
