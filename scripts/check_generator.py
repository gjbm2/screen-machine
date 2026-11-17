#!/usr/bin/env python3
"""Is the picture generator ready? A read-only check of the live set-up that the tests cannot see. It makes no picture
and costs nothing.

    .venv/bin/python scripts/check_generator.py

For every workflow a person, a schedule or Alexa can reach (src/data/workflows.json), its RunPod endpoint must be on
and its workers must have an S3 bucket to upload to: without one a worker sends the picture inside its reply, and
RunPod drops a reply that is too big (17 Nov 2026: jobs "finished" with no picture). It also lists the workflows the
screens' schedules name, since those run unattended. Exit status 1 when something needs attention.
"""
import glob
import json
import sys
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import smconfig  # noqa: E402

REST = "https://api.runpod.io/v2"
RUN = "https://api.runpod.ai/v2"


def get(url, key):
    r = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=15)
    r.raise_for_status()
    return r.json()


def schedule_workflows():
    """{workflow id: [screen, ...]} for every workflow named in the screens' loaded schedules."""
    found = {}

    def walk(node, screen):
        if isinstance(node, dict):
            name = node.get("workflow")
            if isinstance(name, str) and name.endswith(".json"):
                found.setdefault(name, set()).add(screen)
            for value in node.values():
                walk(value, screen)
        elif isinstance(node, list):
            for value in node:
                walk(value, screen)

    for path in glob.glob(str(REPO / "routes" / "scheduler" / "*.json")):
        try:
            walk(json.loads(Path(path).read_text()), Path(path).stem)
        except (OSError, ValueError):
            pass
    return {k: sorted(v) for k, v in found.items()}


def main():
    key = smconfig.env("RUNPOD_API_KEY")
    if not key:
        print("RUNPOD_API_KEY is not set")
        return 1
    catalogue = json.loads((REPO / "src" / "data" / "workflows.json").read_text())
    by_id = {w["id"]: w for w in catalogue}
    scheduled = schedule_workflows()

    def runs_as(workflow_id):
        entry = by_id.get(workflow_id)
        return by_id.get(entry["replaced_by"]) if entry and entry.get("replaced_by") else entry

    # endpoint -> the workflows that reach it, and whether anything can actually ask for it
    endpoints = {}
    for w in catalogue:
        target = runs_as(w["id"])
        if not target or not target.get("runpod_id"):
            continue
        e = endpoints.setdefault(target["runpod_id"], {"workflows": set(), "reachable": False})
        e["workflows"].add(target["id"])
        if not w.get("hidden") or w["id"] in scheduled:
            e["reachable"] = True

    problems = []
    for endpoint_id, e in sorted(endpoints.items()):
        names = ", ".join(sorted(e["workflows"]))
        try:
            info = get(f"{REST}/serverless/{endpoint_id}", key)
            health = get(f"{RUN}/{endpoint_id}/health", key).get("workers") or {}
        except Exception as exc:
            print(f"?    {endpoint_id}  could not be read: {exc}   ({names})")
            if e["reachable"]:
                problems.append(f"{endpoint_id} could not be read")
            continue
        workers_max = (info.get("workers") or {}).get("max") or 0
        has_bucket = "BUCKET_ENDPOINT_URL" in (info.get("env") or {})          # key names only, never the values
        faults = []
        if workers_max == 0:
            faults.append("switched off (max workers 0)")
        if not has_bucket:
            faults.append("no S3 bucket set, so pictures come back inline")
        if health.get("unhealthy"):
            faults.append(f"{health['unhealthy']} unhealthy worker(s)")
        state = "OK  " if not faults else ("FAIL" if e["reachable"] else "off ")
        print(f"{state} {endpoint_id}  {info.get('name')}  max {workers_max}  image {info.get('image')}  ({names})"
              + (f"\n       {'; '.join(faults)}" if faults else ""))
        if faults and e["reachable"]:
            problems.append(f"{info.get('name')}: {'; '.join(faults)}")

    print()
    for workflow_id, screens in sorted(scheduled.items()):
        target = runs_as(workflow_id)
        if not target:
            print(f"FAIL schedules on {', '.join(screens)} name {workflow_id}, which is not in the catalogue")
            problems.append(f"{workflow_id} (schedules: {', '.join(screens)}) is not in the catalogue")
        else:
            via = "" if target["id"] == workflow_id else f" -> {target['id']}"
            print(f"     schedules on {', '.join(screens)} use {workflow_id}{via}")

    print()
    print("Ready." if not problems else "Needs attention:\n  " + "\n  ".join(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
