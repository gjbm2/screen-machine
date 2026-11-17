"""Save the worker's RunPod template and endpoints from worker/endpoints.json (GraphQL; REST v2 can't set cached
models). saveEndpoint replaces the whole endpoint, so every field is sent each time.
    python worker/endpoints.py [name ...]      (RUNPOD_API_KEY from .env; nothing secret is printed)"""
import json
import os
import sys

import requests

GQL = "https://api.runpod.io/graphql"
cfg = json.load(open(os.path.join(os.path.dirname(__file__), "endpoints.json")))


def gql(query, variables):
    r = requests.post(GQL, json={"query": query, "variables": variables}, timeout=60,
                      headers={"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"})
    j = r.json()
    if j.get("errors"):
        raise SystemExit(f"RunPod: {j['errors'][0].get('message')}")
    return j["data"]


def env_list():
    return [{"key": k, "value": v} for k, v in cfg["env"].items()]


def save_template():
    t = cfg["template"]
    found = gql("query { myself { podTemplates { id name } } }", {})["myself"]["podTemplates"]
    tid = next((x["id"] for x in found if x["name"] == t["name"]), None)
    inp = {"name": t["name"], "imageName": t["image"], "isServerless": True, "containerDiskInGb": t["container_disk_gb"],
           "dockerArgs": "", "volumeInGb": 0, "containerRegistryAuthId": t["registry_auth"], "env": env_list()}
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
           "flashBootType": "FLASHBOOT", "locations": "", "networkVolumeIds": [],
           "modelReferences": [pinned(e["bundle"])] if e.get("bundle") else [], "env": env_list()}
    if e.get("id"):
        inp["id"] = e["id"]
    out = gql("mutation($i: EndpointInput!) { saveEndpoint(input: $i) { id workersMax modelReferences } }",
              {"i": inp})["saveEndpoint"]
    print(f"endpoint {name} {out['id']}: max {out['workersMax']}, model {out['modelReferences']}")


if __name__ == "__main__":
    tid = save_template()
    for name in sys.argv[1:] or cfg["endpoints"]:
        save_endpoint(name, cfg["endpoints"][name], tid)
