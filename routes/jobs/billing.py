"""Each job's true cost: its share of what RunPod actually billed (docs/JOBS.md, "Cost").

RunPod bills a serverless worker for its whole life (start-up, model loading, the jobs, the idle timeout after the
last one), and reports the bill per endpoint per hour (REST v2 /billing/serverless). A job's own timings only cover
part of that, so its first cost is provisional (routes.jobs.runpod_api.billing). Here each endpoint-hour's amount is
shared among the jobs that ran on that endpoint in that hour, by how much of each job's billed time
(billing.billed_from..billed_to; older jobs: sent..finished) falls inside it. The job's cost becomes the sum of its
shares ("reconciled"), so the jobs of an hour add up to RunPod's bill for it. An hour with no job in it (a test, a
worker that started for a job that was cancelled) stays unattributed and shows as such in the cost summary.

Runs in Flask: start() makes one thread that reconciles the last WINDOW_S every INTERVAL_S (RunPod's hour buckets
fill in late), after a first pass over the whole history."""
import statistics
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests

from routes.jobs import runpod_api

FX_USD_TO_GBP = 1 / 1.3      # as routes.generate_handler
INTERVAL_S = 3600
WINDOW_S = 48 * 3600
FIRST_DELAY_S = 120
HOUR = 3600

_thread = None


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_hours(start, end):
    """RunPod's serverless bill per endpoint per hour in [start, end): [(endpoint, hour start, usd)]."""
    out = []
    t = start
    while t < end:   # a week per request keeps each answer small
        t1 = min(end, t + 7 * 24 * HOUR)
        r = requests.get(f"{runpod_api.REST_URL}/billing/serverless", headers=runpod_api._headers(), timeout=30,
                         params={"bucketSize": "hour", "startTime": _iso(t), "endTime": _iso(t1)})
        r.raise_for_status()
        for rec in (r.json() or {}).get("records") or []:
            h = runpod_api._epoch(rec.get("startTime"))
            usd = rec.get("totalAmount") or 0
            if rec.get("serverlessId") and h is not None and usd > 0:
                out.append((rec["serverlessId"], h, float(usd)))
        t = t1
    return out


def billed_span(job):
    """(from, to) epoch of the worker time a job paid for, or None."""
    b = job.get("billing") or {}
    if b.get("billed_from") and b.get("billed_to"):
        return b["billed_from"], b["billed_to"]
    start = job.get("submitted_at") or job.get("started_at")
    end = job.get("publishing_at") or job.get("finished_at")
    if start and end and end > start:
        return start, end
    return None


def reconcile(tracker, start, end, hours=None):
    """Share RunPod's bill for the hours in [start, end) among the jobs, re-cost those jobs and record each hour.
    hours: fetch_hours() output (fetched when None). Returns {"hours", "usd", "attributed_usd", "jobs"}."""
    start = start - start % HOUR
    if hours is None:
        hours = fetch_hours(start, end)
    jobs = [j for j in tracker.store.created_between(start - 6 * HOUR, end)
            if j.get("endpoint_id") and billed_span(j)]
    by_endpoint = defaultdict(list)
    for j in jobs:
        by_endpoint[j["endpoint_id"]].append((j, billed_span(j)))
    share = defaultdict(float)
    rows = []
    for endpoint, h, usd in hours:
        overlaps = [(j["id"], min(t1, h + HOUR) - max(t0, h)) for j, (t0, t1) in by_endpoint.get(endpoint, ())]
        overlaps = [(jid, s) for jid, s in overlaps if s > 0]
        total = sum(s for _, s in overlaps)
        for jid, s in overlaps:
            share[jid] += usd * s / total
        rows.append((endpoint, h, usd, usd if total else 0.0, len(overlaps)))
    tracker.store.save_billing_hours(rows)
    recosted = 0
    for j, (t0, t1) in ((j, billed_span(j)) for j in jobs):
        if t0 < start or t1 > end or j["id"] not in share:
            continue   # part of its time is outside the hours read: left as it is until a pass covers it all
        gbp = round(share[j["id"]] * FX_USD_TO_GBP, 6)
        if j.get("cost_state") != "reconciled" or abs((j.get("cost_gbp") or 0) - gbp) > 1e-6:
            tracker.set_cost(j["id"], gbp, "reconciled")
            recosted += 1
    return {"hours": len(rows), "usd": round(sum(r[2] for r in rows), 4),
            "attributed_usd": round(sum(r[3] for r in rows), 4), "jobs": recosted}


def summary(tracker, days=30, now=None):
    """Cost report for the last `days` days (GET /api/jobs/costs): RunPod's bill against what the jobs carry,
    per day, per workflow (cost per image), per source and per endpoint. Money in GBP."""
    now = now or time.time()
    start = now - days * 24 * HOUR
    jobs = [j for j in tracker.store.created_between(start, now) if j.get("cost_gbp") is not None]
    hours = tracker.store.billing_hours(start - start % HOUR, now)

    def gbp(x):
        return round(x * FX_USD_TO_GBP, 2)

    def day(t):
        return datetime.fromtimestamp(t).strftime("%Y-%m-%d")

    by_day = defaultdict(lambda: {"jobs_gbp": 0.0, "images": 0, "billed_usd": 0.0})
    for j in jobs:
        d = by_day[day(j["created_at"])]
        d["jobs_gbp"] += j["cost_gbp"]
        d["images"] += 1
    by_endpoint = defaultdict(lambda: {"billed_usd": 0.0, "attributed_usd": 0.0})
    for endpoint, h, usd, attributed, _ in hours:
        by_day[day(h)]["billed_usd"] += usd
        by_endpoint[endpoint]["billed_usd"] += usd
        by_endpoint[endpoint]["attributed_usd"] += attributed

    def group(key):
        g = defaultdict(list)
        for j in jobs:
            g[j.get(key) or "unknown"].append(j)
        out = []
        for k, js in g.items():
            costs = [j["cost_gbp"] for j in js]
            out.append({"key": k, "images": len(js), "total_gbp": round(sum(costs), 2),
                        "per_image_gbp": round(statistics.median(costs), 4),
                        "mean_gbp": round(sum(costs) / len(costs), 4),
                        "reconciled": sum(1 for j in js if j.get("cost_state") == "reconciled")})
        return sorted(out, key=lambda r: -r["total_gbp"])

    billed = sum(e["billed_usd"] for e in by_endpoint.values())
    attributed = sum(e["attributed_usd"] for e in by_endpoint.values())
    return {
        "days": days,
        "billed_gbp": gbp(billed),                 # what RunPod billed in the period (hours read so far)
        "jobs_gbp": round(sum(j["cost_gbp"] for j in jobs), 2),
        "unattributed_gbp": gbp(billed - attributed),
        "images": len(jobs),
        "reconciled": sum(1 for j in jobs if j.get("cost_state") == "reconciled"),
        "last_hour_read": max((h for _, h, *_ in hours), default=None),
        "by_day": [{"day": k, "jobs_gbp": round(v["jobs_gbp"], 2), "billed_gbp": gbp(v["billed_usd"]),
                    "images": v["images"]} for k, v in sorted(by_day.items())],
        "by_workflow": group("workflow"),
        "by_source": group("source"),
        "by_endpoint": sorted(({"endpoint": e, "name": runpod_api.endpoint_name(e), "billed_gbp": gbp(v["billed_usd"]),
                                "unattributed_gbp": gbp(v["billed_usd"] - v["attributed_usd"])}
                               for e, v in by_endpoint.items()), key=lambda r: -r["billed_gbp"]),
    }


def _loop(tracker):
    from utils.logger import info, error
    time.sleep(FIRST_DELAY_S)
    first = True
    while True:
        now = time.time()
        try:
            start = now - (90 * 24 * HOUR if first else WINDOW_S)
            r = reconcile(tracker, start, now)
            info(f"[jobs] RunPod bill: {r['hours']} endpoint-hours, ${r['usd']:.2f}, ${r['attributed_usd']:.2f} "
                 f"shared among jobs, {r['jobs']} jobs re-costed")
            first = False
        except Exception as e:
            error(f"[jobs] RunPod bill could not be read: {e}")
        time.sleep(INTERVAL_S)


def start(tracker):
    """Start the hourly reconcile thread once per process."""
    global _thread
    if _thread is None:
        _thread = threading.Thread(target=_loop, args=(tracker,), name="jobs-billing", daemon=True)
        _thread.start()
