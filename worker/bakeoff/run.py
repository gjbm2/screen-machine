"""Make a bake-off round: every prompt on every contender, through the app's own generator (refiner "none", so each
model gets the same words), published to devtest. Writes ~/.local/state/screen-machine/bakeoff/<round>.json for the
page at /api/bakeoff/<round> (routes/bakeoff_api.py). Re-running fills in only what is missing.

    python worker/bakeoff/run.py worker/bakeoff/round1.json [--app http://127.0.0.1:5000] [--parallel 3]

A spec with "kind": "score" makes a scorecard (routes/bakeoff_api.py): each lane lists prompts, and every prompt is sent
"seeds" times exactly as a person would (no workflow, no refiner: the app chooses), keys 1, 2, ...
"""
import argparse
import hashlib
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from routes.bakeoff_api import load, save  # noqa: E402

lock = threading.Lock()


def build(spec, data):
    """The round's items, keeping what an earlier run made. Keys A/B/C are shuffled per prompt, stably."""
    score = spec.get("kind") == "score"
    old = {(it["id"], e["key"] if score else e["workflow"]): e for lane in (data or {}).get("lanes", [])
           for it in lane["items"] for e in it["entries"]}
    lanes = []
    if spec.get("kind") == "score":
        for lane in spec["lanes"]:
            items = []
            for prompt in lane["prompts"]:
                iid = hashlib.sha1(prompt.encode()).hexdigest()[:10]
                entries = [dict(old.get((iid, str(k)), {}), key=str(k), workflow=None)
                           for k in range(1, spec.get("seeds", 2) + 1)]
                items.append({"id": iid, "prompt": prompt, "entries": entries})
            lanes.append({"lane": lane["lane"], "items": items})
        return {"round": spec["round"], "kind": "score", "lanes": lanes, "scores": (data or {}).get("scores", {})}
    for lane in spec["lanes"]:
        items = []
        for prompt in lane["prompts"]:
            iid = hashlib.sha1(prompt.encode()).hexdigest()[:10]
            order = list(lane["contenders"])
            random.Random(iid).shuffle(order)
            entries = [dict(old.get((iid, wf), {}), key="ABCDEF"[i], workflow=wf) for i, wf in enumerate(order)]
            items.append({"id": iid, "prompt": prompt, "entries": entries})
        lanes.append({"lane": lane["lane"], "items": items})
    return {"round": spec["round"], "lanes": lanes, "picks": (data or {}).get("picks", {})}


def make(app, round_id, data, item, entry):
    body = {"prompt": item["prompt"], "workflow": entry["workflow"], "params": {"publish_destination": "devtest"}}
    if entry["workflow"]:
        body["refiner"] = "none"
    t0 = time.time()
    try:
        r = requests.post(f"{app}/api/generate-image", data={"data": json.dumps(body)}, timeout=1800)
        res = r.json()
        if r.status_code != 200:
            raise RuntimeError(res.get("error") or r.status_code)
        img = (res.get("images") or [{}])[0]
        job = requests.get(f"{app}/api/jobs/{img.get('job_id')}", timeout=30).json()
        fn = job.get("published_path")
        entry.update(job_id=img.get("job_id"), filename=fn, image=f"/api/buckets/devtest/raw/{fn}" if fn else img.get("url"),
                     cost_gbp=job.get("cost_gbp"), seconds=round(time.time() - t0, 1), error=None)
        if data.get("kind") == "score":
            entry.update(workflow=job.get("workflow"), refined_prompt=job.get("refined_prompt"))
    except Exception as e:
        entry.update(error=str(e)[:300], image=None)
    with lock:
        save(round_id, data)
    print(f"{entry['workflow']:<24} {entry.get('seconds')}s {entry.get('cost_gbp')} {entry.get('error') or ''} | {item['prompt'][:50]}",
          flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--app", default="http://127.0.0.1:5000")
    ap.add_argument("--parallel", type=int, default=3)
    a = ap.parse_args()
    spec = json.load(open(a.spec))
    data = build(spec, load(spec["round"]))
    save(spec["round"], data)
    todo = [(it, e) for lane in data["lanes"] for it in lane["items"] for e in it["entries"] if not e.get("image")]
    print(f"{len(todo)} pictures to make", flush=True)
    with ThreadPoolExecutor(a.parallel) as pool:
        for it, e in todo:
            pool.submit(make, a.app, spec["round"], data, it, e)


if __name__ == "__main__":
    main()
