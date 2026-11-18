"""How RunPod starts a worker after it has sat idle: send one job, wait a gap, send another, for growing gaps, and
record per job the queue wait, the run time, which worker took it and whether that worker is new. A run time well
above the warm figure means the model was loaded from disk; a new worker id means a new container.

    python worker/probe_boot.py <endpoint_id> <graph.json> [--gaps 30,120,300,600,1200,2400,3600] [--out probe.jsonl]
"""
import argparse
import json
import random
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from run_jobs import fill, submit, wait, workers  # noqa: E402


def one(endpoint, graph, gap, seen, out):
    before = workers(endpoint)
    t0 = time.time()
    job = submit(endpoint, fill(graph, "a lighthouse on a rocky shore at dusk, oil painting", None, random.randrange(2**31)))
    s = wait(endpoint, job)
    after = workers(endpoint)
    wid = s.get("workerId")
    w = after.get(wid) or {}
    row = {"t": round(t0), "endpoint": endpoint, "gap_s": gap, "status": s.get("status"),
           "delay_s": round((s.get("delayTime") or 0) / 1000, 1), "exec_s": round((s.get("executionTime") or 0) / 1000, 1),
           "total_s": round(time.time() - t0, 1), "worker": wid, "worker_seen_before": wid in seen,
           "worker_started": w.get("startedAt"), "gpu": w.get("gpuTypeId"), "dc": w.get("dataCenterId"),
           "workers_before": {k: v.get("status") for k, v in before.items()}}
    if s.get("error") or (isinstance(s.get("output"), dict) and s["output"].get("error")):
        row["error"] = str(s.get("error") or s["output"].get("error"))[:200]
    seen.add(wid)
    print(json.dumps(row), flush=True)
    with open(out, "a") as f:
        f.write(json.dumps(row) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("endpoint")
    ap.add_argument("graph")
    ap.add_argument("--gaps", default="30,120,300,600,1200,2400,3600")
    ap.add_argument("--out", default="probe.jsonl")
    a = ap.parse_args()
    graph = json.load(open(a.graph))
    seen = set()
    one(a.endpoint, graph, None, seen, a.out)       # first job: whatever state the endpoint is in
    one(a.endpoint, graph, 0, seen, a.out)          # straight after: the warm figure
    for gap in [int(x) for x in a.gaps.split(",")]:
        time.sleep(gap)
        one(a.endpoint, graph, gap, seen, a.out)


if __name__ == "__main__":
    main()
