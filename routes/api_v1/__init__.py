"""The generation API for other apps and agents (owner, 28 Nov; docs/API.md): /api/v1, JSON in and out, an API key on
every call but the index and the spec (Authorization: Bearer smk_…). Keys are made, rolled and revoked on Settings ›
Apps & API keys (routes/auth/keys.py); which call needs what is config/auth/gates.yaml (`role: key`). The spec is
/api/v1/openapi.json. The `api` capability turns it on.

A generation is asynchronous: POST /api/v1/generations answers 202 with its id at once (or after up to `wait` seconds
if it is done by then), and GET /api/v1/generations/<id> (?wait= to long-poll) gives its state and, once done, its
outputs. An Idempotency-Key header makes a retried POST the same generation. It runs the generator page's own path
(process_generate_image_request), so what a key makes lands in its maker's Generated tab, and its jobs show on the Jobs
page as `api`. A key's limits (pictures in 24 h, generations at once) answer 429 with Retry-After."""
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, copy_current_request_context, jsonify, request, send_file

import smconfig
from routes import auth
from routes.api_v1 import inputs, runs
from routes.auth import keys
from routes.auth.gates import api_error
from utils.logger import info

api_v1 = Blueprint("api_v1", __name__)

MAX_N = 4
MAX_PROMPT = 4000
MAX_WAIT_S = 60
FIELDS = {"prompt", "workflow", "refiner", "n", "params", "images", "publish_to", "wait"}


def base_url():
    """This server as the caller reached it. Waitress drops the proxy's X-Forwarded-Proto (it trusts no proxy), so a
    name (not an address, not localhost) is taken to be the https proxy's: names are only served over https."""
    proto = request.headers.get("X-Forwarded-Proto")
    if not proto:
        host = request.host.rsplit(":", 1)[0].strip("[]")
        named = host != "localhost" and not re.fullmatch(r"[\d.]+|[0-9a-fA-F:]+", host)
        proto = "https" if named else request.scheme
    return f"{proto}://{request.host}"


def client_ip():
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()


def _key():
    """The calling key, or a stand-in for a process on this machine without one (no limits)."""
    v = auth.viewer()
    return v.key or {"id": "local", "name": "this machine", "scopes": list(keys.SCOPES), "destinations": [],
                     "private": True, "per_day": 0, "concurrent": 0, "expires_at": None}


@api_v1.before_request
def _before():
    if not smconfig.enabled("api"):
        return api_error(503, "api_off", "the API is off in this deployment")
    v = auth.viewer()
    if v.key:
        ok, remaining, retry = keys.hit(v.key["id"])
        request.environ["sm.rate"] = remaining
        if not ok:
            return api_error(429, "rate_limited", f"over {keys.REQUESTS_PER_MIN} requests a minute",
                             {"Retry-After": str(retry)})
        keys.note_use(v.key, client_ip())
    return None


@api_v1.after_request
def _after(response):
    response.headers["Cache-Control"] = "no-store"
    if "sm.rate" in request.environ:
        response.headers["RateLimit-Limit"] = str(keys.REQUESTS_PER_MIN)
        response.headers["RateLimit-Remaining"] = str(request.environ["sm.rate"])
    return response


# ---------------------------------------------------------------- the catalogue, as this key sees it

def public_id(workflow_id):
    return workflow_id[:-5] if workflow_id and workflow_id.endswith(".json") else workflow_id


def _workflows():
    from routes.utils import _load_json_once
    return [w for w in _load_json_once("workflow", "workflows.json") if not w.get("hidden")]


def _refiners():
    from routes.utils import _load_json_once
    return [r for r in _load_json_once("refiner", "refiners.json") if not r.get("hidden")]


def _find_workflow(wid):
    wid = str(wid)
    return next((w for w in _workflows() if w["id"] in (wid, f"{wid}.json")), None)


def _params(w):
    return [p for p in w.get("params") or [] if not p.get("hidden")]


def _options(p):
    """A parameter's options as [{value, label}] (the catalogue gives them as objects or as bare values)."""
    return [{"value": o.get("value"), "label": o.get("label")} if isinstance(o, dict) else {"value": o, "label": str(o)}
            for o in p.get("options") or []]


def _param_view(p):
    out = {"id": p["id"], "name": p.get("name"), "type": p.get("type")}
    if p.get("options"):
        out["options"] = _options(p)
    for k in ("default", "min", "max"):
        if p.get(k) is not None:
            out[k] = p[k]
    return out


def _workflow_view(w):
    from routes.jobs.workflows import workflow_kind
    takes = w.get("input") or []
    return {"id": public_id(w["id"]), "name": w.get("name"), "summary": w.get("summary"),
            "output": workflow_kind(w), "images": takes.count("image"), "prompt": "text" in takes,
            "default": bool(w.get("default")), "params": [_param_view(p) for p in _params(w)]}


def _check_params(params, workflow):
    """The request's params, checked against the workflow's (any listed workflow's, for auto). Raises Refused."""
    if params in (None, {}):
        return {}
    if not isinstance(params, dict):
        raise inputs.Refused("bad_params", "params is an object")
    known = {}
    for w in ([workflow] if workflow else _workflows()):
        for p in _params(w):
            known.setdefault(p["id"], p)
    out = {}
    for name, value in params.items():
        p = known.get(name)
        if not p:
            raise inputs.Refused("bad_params", f"{name} is not a parameter of "
                                 f"{public_id(workflow['id']) if workflow else 'any workflow'}")
        kind = p.get("type")
        if p.get("options"):
            values = [str(o["value"]) for o in _options(p)]
            if str(value) not in values:
                raise inputs.Refused("bad_params", f"{name} is one of {', '.join(values)}")
            value = str(value)
        elif kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise inputs.Refused("bad_params", f"{name} is a number")
            if (p.get("min") is not None and value < p["min"]) or (p.get("max") is not None and value > p["max"]):
                raise inputs.Refused("bad_params", f"{name} is from {p.get('min')} to {p.get('max')}")
        elif kind == "bool":
            if not isinstance(value, bool):
                raise inputs.Refused("bad_params", f"{name} is true or false")
        out[name] = value
    return out


# ---------------------------------------------------------------- generations, as the API shows them

def _job_progress(job):
    stages = job.get("stages") or []
    i = job.get("stage_index")
    stage = stages[i] if isinstance(i, int) and 0 <= i < len(stages) else None
    if isinstance(stage, dict):
        stage = stage.get("label") or stage.get("name") or stage.get("id")
    return {"status": job.get("status"), "stage": stage, "percent": job.get("percent")}


def _output_view(gen_id, i, image, base):
    from routes.image_formats import VIDEOS
    file = image.get("recent_file")
    url = image.get("url") or ""
    if file:
        url = f"{base}/output/_recent/{quote(file)}"
    elif url.startswith("/"):
        url = base + url
    kind = "video" if Path(file or url.split("?")[0]).suffix.lower() in VIDEOS else "image"
    return {"index": i, "kind": kind, "url": url, "download_url": f"{base}/api/v1/generations/{gen_id}/outputs/{i}",
            "seed": image.get("seed"), "prompt": image.get("final_prompt") or image.get("prompt"),
            "workflow": public_id(image.get("workflow")), "seconds": image.get("generation_time_seconds"),
            "cost_gbp": image.get("generation_cost_gbp")}


def view(g):
    from routes.jobs import get_tracker
    base = base_url()
    status, progress = g["status"], None
    if status in runs.LIVE:
        jobs = [j for j in (get_tracker().get(j) for j in runs.job_ids(g["id"]) or []) if j]
        if any(j.get("status") in ("running", "publishing") for j in jobs):
            status = "running"
        progress = [_job_progress(j) for j in jobs]
    asked = g.get("request") or {}
    out = {"id": g["id"], "object": "generation", "status": status, "created_at": g["created_at"],
           "finished_at": g.get("finished_at"), "prompt": asked.get("prompt"), "workflow": asked.get("workflow"),
           "n": g["n"], "publish_to": asked.get("publish_to"),
           "outputs": [_output_view(g["id"], i, im, base) for i, im in enumerate((g.get("result") or {}).get("images") or [])],
           "links": {"self": f"{base}/api/v1/generations/{g['id']}"}}
    if progress is not None:
        out["progress"] = progress
    if g.get("error"):
        out["error"] = {"code": "generation_" + status, "message": g["error"]}
    return out


KEEP = ("recent_file", "url", "seed", "prompt", "final_prompt", "workflow", "generation_time_seconds",
        "generation_cost_gbp", "job_id")


def _trim(result):
    """What is kept of process_generate_image_request's answer: per output, what the API shows."""
    return {"images": [{k: im.get(k) for k in KEEP} for im in (result or {}).get("images") or []]}


def _own_output(key_id):
    def path(gen_id, index):
        from routes.bucketer import bucket_path
        g = runs.get(gen_id, key_id)
        images = ((g or {}).get("result") or {}).get("images") or []
        if not 0 <= index < len(images) or not images[index].get("recent_file"):
            return None
        p = bucket_path("_recent") / images[index]["recent_file"]
        return p if p.is_file() else None
    return path


# ---------------------------------------------------------------- routes

@api_v1.route("/v1", methods=["GET"])
def index():
    base = base_url()
    return jsonify({"name": "screen-machine generation API", "version": "1",
                    "spec": f"{base}/api/v1/openapi.json", "auth": "Authorization: Bearer smk_…",
                    "endpoints": {"me": f"{base}/api/v1/me", "workflows": f"{base}/api/v1/workflows",
                                  "refiners": f"{base}/api/v1/refiners", "destinations": f"{base}/api/v1/destinations",
                                  "generations": f"{base}/api/v1/generations"}})


@api_v1.route("/v1/openapi.json", methods=["GET"])
def openapi():
    from routes.api_v1.spec import spec
    return jsonify(spec(base_url()))


@api_v1.route("/v1/me", methods=["GET"])
def me():
    key = _key()
    made, _ = runs.made_since(key["id"], time.time() - 86400)
    return jsonify({"object": "key", **(keys.public(key) if auth.viewer().key else {"id": "local", "name": key["name"]}),
                    "usage": {"last_24h": made, "running": runs.running_count(key["id"])},
                    "rate_limit_per_min": keys.REQUESTS_PER_MIN})


@api_v1.route("/v1/workflows", methods=["GET"])
def workflows():
    return jsonify({"object": "list", "data": [_workflow_view(w) for w in _workflows()]})


@api_v1.route("/v1/refiners", methods=["GET"])
def refiners():
    return jsonify({"object": "list", "data": [{"id": r["id"], "name": r.get("name")} for r in _refiners()]})


@api_v1.route("/v1/destinations", methods=["GET"])
def destinations():
    """The screens this key may publish to (none without the publish scope)."""
    from routes.auth import lock
    key = _key()
    if "publish" not in key["scopes"]:
        return jsonify({"object": "list", "data": []})
    mine = key["destinations"]
    data = [{"id": d["id"], "name": d.get("name") or d["id"]} for d in smconfig.destinations()
            if (d["id"] in mine or not auth.viewer().key) and d["id"] != "_recent" and not lock.hidden(d["id"])]
    return jsonify({"object": "list", "data": data})


@api_v1.route("/v1/generations", methods=["GET"])
def list_generations():
    try:
        limit = max(1, min(100, int(request.args.get("limit", 20))))
        before = float(request.args["before"]) if request.args.get("before") else None
    except ValueError:
        return api_error(400, "bad_request", "limit is a whole number and before a time (epoch seconds)")
    rows = runs.listing(_key()["id"], limit, before)
    return jsonify({"object": "list", "data": [view(g) for g in rows],
                    "next_before": rows[-1]["created_at"] if len(rows) == limit else None})


@api_v1.route("/v1/generations/<gen_id>", methods=["GET"])
def get_generation(gen_id):
    key = _key()
    try:
        wait = max(0.0, min(MAX_WAIT_S, float(request.args.get("wait") or 0)))
    except ValueError:
        return api_error(400, "bad_request", "wait is seconds")
    if not runs.get(gen_id, key["id"]):
        return api_error(404, "not_found", "no such generation for this key")
    runs.wait(gen_id, wait)
    return jsonify(view(runs.get(gen_id, key["id"])))


@api_v1.route("/v1/generations/<gen_id>", methods=["DELETE"])
def cancel_generation(gen_id):
    key = _key()
    if not runs.get(gen_id, key["id"]):
        return api_error(404, "not_found", "no such generation for this key")
    if runs.cancel(gen_id):
        info(f"[api] {gen_id} cancelled by key {key['id']}")
        runs.wait(gen_id, 5)
    return jsonify(view(runs.get(gen_id, key["id"])))


@api_v1.route("/v1/generations/<gen_id>/outputs/<int:index>", methods=["GET"])
def get_output(gen_id, index):
    key = _key()
    if not runs.get(gen_id, key["id"]):
        return api_error(404, "not_found", "no such generation for this key")
    path = _own_output(key["id"])(gen_id, index)
    if not path:
        return api_error(404, "not_found", "no such output (it may not be made yet, or it was deleted)")
    return send_file(path, conditional=True)


@api_v1.route("/v1/generations", methods=["POST"])
def create_generation():
    if not smconfig.enabled("generation"):
        return api_error(503, "generation_off", "generation is off in this deployment")
    key = _key()
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return api_error(400, "bad_request", "the body is a JSON object (Content-Type: application/json)")
    unknown = sorted(set(body) - FIELDS)
    if unknown:
        return api_error(400, "bad_request", f"unknown fields: {', '.join(unknown)}", allowed=sorted(FIELDS))

    idem = (request.headers.get("Idempotency-Key") or "").strip()[:200] or None
    req_hash = runs.request_hash({k: v for k, v in body.items() if k != "wait"})
    if idem:
        replay = _replay(key, idem, req_hash)
        if replay is not None:
            return replay

    try:
        asked = _check_request(body)
        images = inputs.read(body.get("images"), _own_output(key["id"]))
    except inputs.Refused as e:
        return api_error(400, e.code, str(e))
    workflow = asked["workflow"]
    if workflow and len(images) < workflow.get("input", []).count("image"):
        return api_error(400, "images_required", f"{public_id(workflow['id'])} needs "
                         f"{workflow['input'].count('image')} image(s)")
    if not asked["prompt"] and not images:
        return api_error(400, "bad_request", "give a prompt, images, or both")

    refused = _over_limits(key, asked["n"])
    if refused is not None:
        return refused

    gen_id = runs.new_id()
    space = auth.own_space()
    auth.note_batch(gen_id, space)
    params = {**asked["params"], "batch": asked["n"]}
    if asked["publish_to"]:
        params["publish_destination"] = asked["publish_to"]
    data = {"prompt": asked["prompt"], "workflow": workflow["id"] if workflow else None, "refiner": asked["refiner"],
            "params": params, "batch_id": gen_id, "space": space, "has_reference_image": bool(images)}
    summary = {"prompt": asked["prompt"], "workflow": public_id(workflow["id"]) if workflow else "auto",
               "refiner": asked["refiner"] or "auto", "n": asked["n"], "params": asked["params"],
               "publish_to": asked["publish_to"], "images": len(images)}
    try:
        run = runs.create(gen_id, key["id"], asked["n"], summary, idem, req_hash)
    except sqlite3.IntegrityError:  # the same Idempotency-Key, sent twice at once
        return _replay(key, idem, req_hash) or api_error(409, "idempotency_conflict", "try again")

    from routes.generate_utils import process_generate_image_request

    @copy_current_request_context
    def work():  # in the request's context, so the router sees only this key's catalogue
        return _trim(process_generate_image_request(data, uploaded_images=images, job_source="api",
                                                    job_ids=run["job_ids"]))

    runs.start(gen_id, run, work)
    info(f"[api] {gen_id} from key {key['id']} ({key['name']}): {summary['workflow']}, n={asked['n']}"
         f"{', to ' + asked['publish_to'] if asked['publish_to'] else ''}")
    runs.wait(gen_id, asked["wait"])
    g = runs.get(gen_id, key["id"])
    response = jsonify(view(g))
    response.status_code = 202 if g["status"] in runs.LIVE else 200
    response.headers["Location"] = f"{base_url()}/api/v1/generations/{gen_id}"
    return response


def _replay(key, idem, req_hash):
    g = runs.by_idempotency(key["id"], idem)
    if not g:
        return None
    if g.get("request_hash") != req_hash:
        return api_error(409, "idempotency_conflict", "that Idempotency-Key was used for a different request")
    response = jsonify(view(g))
    response.headers["Idempotent-Replayed"] = "true"
    response.headers["Location"] = f"{base_url()}/api/v1/generations/{g['id']}"
    return response


def _check_request(body):
    prompt = body.get("prompt") or ""
    if not isinstance(prompt, str) or len(prompt) > MAX_PROMPT:
        raise inputs.Refused("bad_request", f"prompt is text of up to {MAX_PROMPT} characters")
    workflow = None
    wid = body.get("workflow")
    if wid not in (None, "", "auto"):
        workflow = _find_workflow(wid)
        if not workflow:
            raise inputs.Refused("unknown_workflow", f"no workflow {wid}: see GET /api/v1/workflows")
    refiner = body.get("refiner")
    if refiner in ("", "auto"):
        refiner = None
    if refiner is not None and refiner not in {r["id"] for r in _refiners()}:
        raise inputs.Refused("unknown_refiner", f"no refiner {refiner}: see GET /api/v1/refiners")
    n = body.get("n", 1)
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_N:
        raise inputs.Refused("bad_request", f"n is 1 to {MAX_N}")
    wait = body.get("wait", 0)
    if isinstance(wait, bool) or not isinstance(wait, (int, float)) or wait < 0:
        raise inputs.Refused("bad_request", f"wait is seconds, 0 to {MAX_WAIT_S}")
    publish_to = body.get("publish_to") or None
    if publish_to is not None and (not isinstance(publish_to, str)
                                   or publish_to not in {d["id"] for d in smconfig.destinations()}):
        raise inputs.Refused("unknown_destination", f"no destination {publish_to}: see GET /api/v1/destinations")
    return {"prompt": prompt.strip(), "workflow": workflow, "refiner": refiner, "n": n,
            "wait": min(float(wait), MAX_WAIT_S), "publish_to": publish_to,
            "params": _check_params(body.get("params"), workflow)}


def _over_limits(key, n):
    """A 429 when this would go over the key's limits, else None."""
    if key.get("concurrent") and runs.running_count(key["id"]) >= key["concurrent"]:
        return api_error(429, "too_many_running", f"this key may have {key['concurrent']} generation(s) running at once",
                         {"Retry-After": "10"})
    if key.get("per_day"):
        now = time.time()
        made, oldest = runs.made_since(key["id"], now - 86400)
        if made + n > key["per_day"]:
            retry = int((oldest or now) + 86400 - now) + 1
            return api_error(429, "quota_exceeded", f"this key may ask for {key['per_day']} pictures in 24 hours "
                             f"({made} so far)", {"Retry-After": str(max(1, retry))}, used=made, limit=key["per_day"])
    return None
