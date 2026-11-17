"""Minimal RunPod serverless client for the job lifecycle: submit, status, cancel. Every request has a
timeout, and HTTP failures become one plain-text line a person can act on. Also what a finished job cost
(billed_usd), from RunPod's management API."""
import os
import threading
import time
from datetime import datetime

import requests

BASE_URL = "https://api.runpod.ai/v2"
# The management API: REST v2 (v1 at rest.runpod.io was retired on 15 Nov 2026; spec at /v2/openapi.json)
REST_URL = "https://api.runpod.io/v2"

_CACHE_S = 3600          # endpoint details and the GPU price list change rarely
_CACHE_FAIL_S = 60       # a failed read is retried after a minute, not on every error message
_cache = {}              # key -> (read at, value or None)
_cache_lock = threading.Lock()


class RunPodError(Exception):
    """A RunPod request failed; str() is the plain-text reason shown on the job."""


def _headers():
    key = os.getenv("RUNPOD_API_KEY")
    if not key:
        raise RunPodError("RUNPOD_API_KEY is not set")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _cached_get(key, path, keep):
    """GET REST_URL + path, kept for an hour as keep(json); None when it can't be read. Only keep()'s result is
    held, so nothing else in the response (an endpoint's template env) stays in memory."""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < (_CACHE_S if hit[1] is not None else _CACHE_FAIL_S):
        return hit[1]
    value = None
    try:
        r = requests.get(f"{REST_URL}{path}", headers=_headers(), timeout=5)
        if r.status_code == 200:
            value = keep(r.json() or {})
    except Exception:
        pass
    if value is None and hit and hit[1] is not None:
        value = hit[1]   # a stale answer beats none
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def endpoint_info(endpoint_id):
    """{name, pools} for an endpoint (pools: its GPU pools in preference order, e.g. AMPERE_80), or None."""
    return _cached_get(("endpoint", endpoint_id), f"/serverless/{endpoint_id}",
                       lambda d: {"name": d.get("name"), "pools": list((d.get("gpu") or {}).get("pools") or [])})


def endpoint_name(endpoint_id):
    """The endpoint's name on RunPod (cached), or its id when it can't be read."""
    return (endpoint_info(endpoint_id) or {}).get("name") or endpoint_id


def _prices(gpus):
    by_gpu, by_pool = {}, {}
    for g in gpus.get("gpus") or []:
        per_hour = (g.get("price") or {}).get("serverless")
        if not per_hour:
            continue
        by_gpu[g.get("id")] = per_hour / 3600
        if g.get("pool"):
            by_pool[g["pool"]] = max(by_pool.get(g["pool"], 0), per_hour / 3600)
    return {"gpu": by_gpu, "pool": by_pool} if by_gpu else None


def serverless_prices():
    """RunPod's serverless (flex) prices in USD per second, from its GPU catalogue: {"gpu": {gpu type id: rate},
    "pool": {pool: rate}} (a pool's rate is its dearest GPU's), or None."""
    return _cached_get("gpus", "/catalog/gpus", _prices)


def worker(endpoint_id, worker_id):
    """The endpoint's worker ({gpuTypeId, startedAt, ...}) while RunPod still lists it, else None. Not cached."""
    if not worker_id:
        return None
    try:
        r = requests.get(f"{REST_URL}/serverless/{endpoint_id}/workers", headers=_headers(), timeout=5)
        if r.status_code == 200:
            return next((w for w in (r.json() or {}).get("workers") or [] if w.get("id") == worker_id), None)
    except Exception:
        pass
    return None


def _epoch(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def billing(endpoint_id, job_status, submitted_at=None, finished_at=None):
    """The provisional bill for a finished job, as a dict for the job record: usd, billed_s, exec_s, delay_s,
    cold_s, gpu, rate_usd_h, worker_id, and billed_from/billed_to (epoch: the stretch of worker time it paid for,
    which routes.jobs.billing uses to share out RunPod's hourly bill). None when it can't be worked out."""
    detail = {}
    usd, how = billed_usd(endpoint_id, job_status, submitted_at, detail)
    if usd is None:
        return None
    end = finished_at or time.time()
    return dict(detail, usd=round(usd, 6), how=how, billed_from=round(end - detail["billed_s"], 1),
                billed_to=round(end, 1))


def billed_usd(endpoint_id, job_status, submitted_at=None, detail=None):
    """What RunPod bills for a finished job: (USD, how it was worked out), or (None, why not).

    Billed time = executionTime + the cold start inside delayTime: when the worker started after the job was sent,
    the time from its start to the job starting (a warm worker adds nothing; queue waiting is not billed). Rate = the
    serverless per-second price of the GPU the worker ran on, else of the endpoint's first GPU pool. RunPod also bills
    the worker's idle timeout after the job; that part only shows in its hourly bill (routes.jobs.billing).
    detail, if given, is filled with the parts."""
    exec_ms = job_status.get("executionTime")
    if not isinstance(exec_ms, (int, float)):
        return None, "RunPod sent no executionTime"
    delay_s = (job_status.get("delayTime") or 0) / 1000
    prices = serverless_prices()
    if not prices:
        return None, "RunPod's GPU prices could not be read"
    w = worker(endpoint_id, job_status.get("workerId"))
    gpu = (w or {}).get("gpuTypeId")
    rate = prices["gpu"].get(gpu)
    if rate is None:
        pools = (endpoint_info(endpoint_id) or {}).get("pools") or []
        gpu = pools[0] if pools else None
        rate = prices["pool"].get(gpu)
    if rate is None:
        return None, f"no serverless price for {gpu or 'the endpoint GPU'}"
    cold_s = 0.0
    started = _epoch((w or {}).get("startedAt"))
    if started and submitted_at and started > submitted_at - 2:   # 2 s for clock skew
        cold_s = min(max(submitted_at + delay_s - started, 0.0), delay_s)
    billed_s = exec_ms / 1000 + cold_s
    if detail is not None:
        detail.update(billed_s=round(billed_s, 1), exec_s=round(exec_ms / 1000, 1), delay_s=round(delay_s, 1),
                      cold_s=round(cold_s, 1), gpu=gpu, rate_usd_h=round(rate * 3600, 4),
                      worker_id=job_status.get("workerId"))
    return billed_s * rate, (f"{billed_s:.1f} s on {gpu} at ${rate * 3600:.2f}/h "
                             f"(run {exec_ms / 1000:.1f} s, cold start {cold_s:.1f} s, waited {delay_s:.1f} s)")


def describe_http_error(endpoint_id, status_code, body):
    """A RunPod HTTP error response -> one plain line."""
    fields = {}
    if isinstance(body, dict):
        fields = body
    text = " ".join(str(fields.get(k) or "") for k in ("status", "code", "error", "message")).strip()
    if not text and isinstance(body, str):
        text = body.strip()
    if "PAUSED" in text.upper():
        return f"The {endpoint_name(endpoint_id)} endpoint is paused on RunPod"
    if status_code == 401:
        return "RunPod rejected the API key"
    if status_code == 404:
        return f"RunPod has no endpoint {endpoint_id}"
    if status_code == 429:
        return "RunPod is rate limiting requests; try again shortly"
    if status_code >= 500:
        return f"RunPod had a server error (HTTP {status_code})"
    detail = (fields.get("error") or fields.get("message") or text or "").strip() if fields else text
    detail = str(detail)[:200]
    return f"RunPod refused the job (HTTP {status_code})" + (f": {detail}" if detail else "")


def _request(method, url, endpoint_id, timeout, json_body=None):
    try:
        r = requests.request(method, url, headers=_headers(), json=json_body, timeout=timeout)
    except requests.Timeout:
        raise RunPodError("RunPod did not answer in time")
    except requests.RequestException as e:
        raise RunPodError(f"Could not reach RunPod ({type(e).__name__})")
    if r.status_code >= 400:
        try:
            body = r.json()
        except ValueError:
            body = r.text[:500]
        raise RunPodError(describe_http_error(endpoint_id, r.status_code, body))
    try:
        return r.json()
    except ValueError:
        raise RunPodError("RunPod sent a response that isn't JSON")


def submit(endpoint_id, payload, timeout=60):
    """POST /run -> the RunPod job id."""
    data = _request("POST", f"{BASE_URL}/{endpoint_id}/run", endpoint_id, timeout, payload)
    job_id = data.get("id")
    if not job_id:
        raise RunPodError("RunPod accepted the job but sent no job id")
    return job_id


def status(endpoint_id, job_id, timeout=10):
    """GET /status -> RunPod's status dict ({status, output?, error?, ...})."""
    return _request("GET", f"{BASE_URL}/{endpoint_id}/status/{job_id}", endpoint_id, timeout)


def cancel(endpoint_id, job_id, timeout=10):
    """POST /cancel; True when RunPod accepted it. Never raises."""
    try:
        _request("POST", f"{BASE_URL}/{endpoint_id}/cancel/{job_id}", endpoint_id, timeout)
        return True
    except Exception:
        return False
