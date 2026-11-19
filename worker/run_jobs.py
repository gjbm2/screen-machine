"""Send ComfyUI graphs to a RunPod endpoint and time them (pre-spike, bake-off).

    python worker/run_jobs.py <endpoint_id> <graph.json> [--prompt TEXT] [--n N] [--pair]
        [--size WxH] [--out DIR]

Each job prints one JSON line: delay_s (queue + cold start), exec_s, worker id, GPU, whether the
worker was new, the image size, and the error if any. Images are saved to --out. --pair sends two
jobs at once. RUNPOD_API_KEY comes from the environment (.env); it is never printed.
"""
import argparse
import base64
import json
import os
import random
import sys
import time

import requests

API = "https://api.runpod.ai/v2"
REST = "https://api.runpod.io/v2"


def headers():
    return {"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}", "Content-Type": "application/json"}


def fill(graph, prompt, size, seed):
    g = json.loads(json.dumps(graph))
    for node in g.values():
        title = node.get("_meta", {}).get("title", "")
        ins = node.get("inputs", {})
        if "{{POSITIVE-PROMPT}}" in title:
            for k in ("prompt", "text", "value"):
                if k in ins:
                    ins[k] = prompt
        if size and node.get("class_type") in ("EmptyLatentImage", "EmptySD3LatentImage", "ModelSamplingFlux"):
            ins["width"], ins["height"] = size
        for k in ("seed", "noise_seed"):
            if k in ins:
                ins[k] = seed
    return g


def workers(endpoint):
    try:
        r = requests.get(f"{REST}/serverless/{endpoint}/workers", headers=headers(), timeout=10)
        if not r.ok:
            return {}
        d = r.json()
        return {w.get("id"): w for w in (d if isinstance(d, list) else d.get("workers") or [])}
    except Exception:
        return {}


def submit(endpoint, graph, images=None):
    body = {"workflow": graph}
    if images:
        body["images"] = images
    r = requests.post(f"{API}/{endpoint}/run", headers=headers(), json={"input": body}, timeout=30)
    r.raise_for_status()
    return r.json()["id"]


def wait(endpoint, job, timeout_s=1800):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        r = requests.get(f"{API}/{endpoint}/status/{job}", headers=headers(), timeout=30)
        s = r.json()
        if s.get("status") in ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"):
            return s
        time.sleep(2)
    requests.post(f"{API}/{endpoint}/cancel/{job}", headers=headers(), timeout=10)
    return {"status": "LOCAL_TIMEOUT"}


def report(endpoint, job, s, before, out, label):
    after = workers(endpoint)
    wid = s.get("workerId")
    w = after.get(wid) or {}
    row = {"label": label, "job": job, "status": s.get("status"),
           "delay_s": round((s.get("delayTime") or 0) / 1000, 1),
           "exec_s": round((s.get("executionTime") or 0) / 1000, 1),
           "worker": wid, "new_worker": wid not in before, "gpu": w.get("gpuTypeId") or w.get("gpu")}
    o = s.get("output") or {}
    if isinstance(o, dict) and o.get("error"):
        row["error"] = str(o.get("error"))[:300]
    for i, img in enumerate((o.get("images") or []) if isinstance(o, dict) else []):
        if img.get("type") == "base64" and out:
            data = base64.b64decode(img["data"])
            path = os.path.join(out, f"{label}_{job[:8]}_{i}.png")
            open(path, "wb").write(data)
            row["image"] = path
            row["mb"] = round(len(data) / 1e6, 1)
        elif img.get("type") == "s3_url":
            row["image"] = img.get("data")
            if out:
                path = os.path.join(out, f"{label}_{job[:8]}_{i}.png")
                open(path, "wb").write(requests.get(img["data"], timeout=60).content)
                row["image"] = path
    if s.get("error"):
        row["error"] = str(s["error"])[:300]
    print(json.dumps(row), flush=True)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("endpoint")
    ap.add_argument("graph")
    ap.add_argument("--prompt", default="A Turner-style oil painting of a harbour at dusk, storm light breaking over masts, thick impasto, gold and slate")
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--pair", action="store_true")
    ap.add_argument("--size")
    ap.add_argument("--label", default="job")
    ap.add_argument("--image", action="append", default=[], help="input image(s) for LoadImage nodes: input.png, input2.png")
    ap.add_argument("--out", default=".")
    a = ap.parse_args()
    graph = json.load(open(a.graph))
    size = tuple(int(x) for x in a.size.split("x")) if a.size else None
    os.makedirs(a.out, exist_ok=True)
    for _ in range(a.n):
        before = workers(a.endpoint)
        k = 2 if a.pair else 1
        images = [{"name": ("input.png", "input2.png")[i], "image": base64.b64encode(open(p, "rb").read()).decode()}
                  for i, p in enumerate(a.image[:2])]
        jobs = [submit(a.endpoint, fill(graph, a.prompt, size, random.randrange(2**31)), images) for _ in range(k)]
        for j in jobs:
            report(a.endpoint, j, wait(a.endpoint, j), before, a.out, a.label)


if __name__ == "__main__":
    sys.exit(main())
