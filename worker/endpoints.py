"""Save the worker's RunPod template and endpoints from worker/endpoints.json (GraphQL; REST v2 can't set cached
models). saveEndpoint replaces the whole endpoint, so every field is sent each time. A deployment's own endpoints
(deployments/<id>/private/endpoints.json, not in git) are added to the shared ones.
    python worker/endpoints.py [name ...]      (RUNPOD_API_KEY from .env; nothing secret is printed)"""
import json
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import smconfig  # noqa: E402

GQL = "https://api.runpod.io/graphql"
SHARED = os.path.join(os.path.dirname(__file__), "endpoints.json")
PRIVATE = smconfig.path("private", "endpoints.json")
cfg = json.load(open(SHARED))
if PRIVATE.exists():
    cfg["endpoints"].update(json.loads(PRIVATE.read_text())["endpoints"])


def gql(query, variables):
    r = requests.post(GQL, json={"query": query, "variables": variables}, timeout=60,
                      headers={"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"})
    j = r.json()
    if j.get("errors"):
        raise SystemExit(f"RunPod: {j['errors'][0].get('message')}")
    return j["data"]


def env_list(e=None):
    """The shared env plus the endpoint's own "env" (e.g. COMFY_ARGS, SM_PREFETCH for a one-model endpoint). An
    endpoint that is not the ComfyUI worker (e.g. sm-judge on RunPod's vLLM worker) sets "shared_env": false."""
    shared = cfg["env"] if (e or {}).get("shared_env", True) else {}
    return [{"key": k, "value": v} for k, v in {**shared, **((e or {}).get("env") or {})}.items()]


def save_template(endpoint_id=None, name=None, e=None):
    """Save the worker template. An existing endpoint keeps the template it was created with (RunPod ignores a new
    templateId on saveEndpoint), so that template is the one updated; otherwise the one named (the endpoint's own
    "template" name, else endpoints.json's), so a new endpoint never re-saves a template others run on."""
    t = dict(cfg["template"], name=name or cfg["template"]["name"])
    if e and e.get("image"):   # an endpoint may run a newer worker image than the shared template
        t["image"] = e["image"]
    tid = None
    if endpoint_id:
        tid = gql('query($id: String!) { myself { endpoint(id: $id) { templateId } } }',
                  {"id": endpoint_id})["myself"]["endpoint"]["templateId"]
    if not tid:
        found = gql("query { myself { podTemplates { id name } } }", {})["myself"]["podTemplates"]
        tid = next((x["id"] for x in found if x["name"] == t["name"]), None)
    names = {x["id"]: x["name"] for x in gql("query { myself { podTemplates { id name } } }", {})["myself"]["podTemplates"]}
    inp = {"name": names.get(tid, t["name"]), "imageName": t["image"], "isServerless": True, "containerDiskInGb": t["container_disk_gb"],
           "dockerArgs": "", "volumeInGb": 0, "containerRegistryAuthId": t["registry_auth"], "env": env_list(e)}
    if tid:
        inp["id"] = tid
    out = gql("mutation($i: SaveTemplateInput) { saveTemplate(input: $i) { id imageName } }", {"i": inp})["saveTemplate"]
    print(f"template {t['name']} {out['id']}: {out['imageName']}")
    return out["id"]


def pinned(ref):
    """https://huggingface.co/<repo>:<branch> -> the same at the branch's current commit. RunPod resolves a branch when
    the endpoint is first saved and keeps that commit, so a bundle update only reaches workers when re-pinned here."""
    url, _, rev = ref.rpartition(":")
    if len(rev) == 40:
        return ref
    repo = url.split("huggingface.co/", 1)[1]
    token = open(os.path.expanduser("~/.config/screen-machine/hf.token")).read().strip()
    r = requests.get(f"https://huggingface.co/api/models/{repo}/revision/{rev}", timeout=30,
                     headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return f"{url}:{r.json()['sha']}"


def save_endpoint(name, e, template_id):
    inp = {"name": name, "templateId": template_id, "gpuIds": e["gpus"], "gpuCount": 1, "workersMin": e.get("workers_min", 0),
           "workersMax": e["workers_max"], "idleTimeout": e["idle_timeout"], "scalerType": "QUEUE_DELAY",
           "scalerValue": e.get("scaler_s", 4), "executionTimeoutMs": e["execution_timeout_s"] * 1000,
           "flashBootType": "FLASHBOOT" if e.get("flashboot", True) else "OFF", "locations": "", "networkVolumeIds": [],
           "modelReferences": [pinned(e["bundle"])] if e.get("bundle") else [], "env": env_list(e),
           "allowedCudaVersions": e.get("cuda", "")}   # e.g. "13.0": only hosts whose driver runs that CUDA
    if e.get("id"):
        inp["id"] = e["id"]
    out = gql("mutation($i: EndpointInput!) { saveEndpoint(input: $i) { id workersMax modelReferences } }",
              {"i": inp})["saveEndpoint"]
    print(f"endpoint {name} {out['id']}: max {out['workersMax']}, model {out['modelReferences']}")
    if not e.get("id"):   # a new endpoint: remember its id, so the next run updates it instead of making another
        path = PRIVATE if PRIVATE.exists() and name in json.loads(PRIVATE.read_text())["endpoints"] else SHARED
        saved = json.load(open(path))
        saved["endpoints"][name]["id"] = out["id"]
        with open(path, "w") as f:
            json.dump(saved, f, indent=2)
            f.write("\n")


if __name__ == "__main__":
    for name in sys.argv[1:] or cfg["endpoints"]:
        e = cfg["endpoints"][name]
        save_endpoint(name, e, save_template(e.get("id"), e.get("template"), e))
