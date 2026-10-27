"""Minimal RunPod serverless client for the job lifecycle: submit, status, cancel. Every request has a
timeout, and HTTP failures become one plain-text line a person can act on."""
import os
import threading

import requests

BASE_URL = "https://api.runpod.ai/v2"
REST_URL = "https://rest.runpod.io/v1"

_names = {}
_names_lock = threading.Lock()


class RunPodError(Exception):
    """A RunPod request failed; str() is the plain-text reason shown on the job."""


def _headers():
    key = os.getenv("RUNPOD_API_KEY")
    if not key:
        raise RunPodError("RUNPOD_API_KEY is not set")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def endpoint_name(endpoint_id):
    """The endpoint's name on RunPod (cached), or its id when it can't be read."""
    with _names_lock:
        if endpoint_id in _names:
            return _names[endpoint_id]
    name = endpoint_id
    try:
        r = requests.get(f"{REST_URL}/endpoints/{endpoint_id}", headers=_headers(), timeout=5)
        if r.status_code == 200:
            name = (r.json() or {}).get("name") or endpoint_id
    except Exception:
        pass
    with _names_lock:
        _names[endpoint_id] = name
    return name


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
