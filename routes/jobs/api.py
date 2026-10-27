"""HTTP + SSE API for generation jobs (docs/JOBS.md). Open like the rest of the generation API."""
import json
import time

from flask import Blueprint, Response, jsonify, request

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
    return jsonify({"jobs": jobs, "next_before": next_before})


@jobs_api.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id):
    job = get_tracker().get(job_id)
    return (jsonify(job), 200) if job else (jsonify({"error": "unknown job"}), 404)


@jobs_api.route("/jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id):
    job = get_tracker().cancel(job_id)
    return (jsonify(job), 200) if job else (jsonify({"error": "unknown job"}), 404)


@jobs_api.route("/jobs/cancel-all", methods=["POST"])
@jobs_api.route("/cancel_all_jobs", methods=["GET", "POST"])
def cancel_all_jobs():
    return jsonify({"cancelled": get_tracker().cancel_all()})


def _event(name, data):
    return f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"


def event_stream(tracker, target=None, ping_s=PING_S, stream_s=STREAM_S):
    """SSE frames: retry + snapshot, then each job change, a ping comment every ping_s, and the end
    after stream_s (EventSource reconnects and gets a fresh snapshot)."""
    sub = tracker.subscribe(target=target)
    try:
        yield f"retry: {RETRY_MS}\n\n"
        yield _event("snapshot", {"jobs": tracker.snapshot(target), "server_time": time.time()})
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
            if job is not None:
                yield _event("job", job)
    finally:
        sub.close()


@jobs_api.route("/jobs/stream", methods=["GET"])
def stream_jobs():
    target = request.args.get("target") or None
    return Response(event_stream(get_tracker(), target), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
