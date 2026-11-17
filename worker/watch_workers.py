"""Log an endpoint's worker states every 5 s (one line per change) until killed or --for seconds.
    python worker/watch_workers.py <endpoint_id> [--for 1800]"""
import argparse
import json
import os
import time

import requests

ap = argparse.ArgumentParser()
ap.add_argument("endpoint")
ap.add_argument("--for", dest="dur", type=int, default=1800)
a = ap.parse_args()
h = {"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"}
last, t0 = None, time.time()
while time.time() - t0 < a.dur:
    try:
        d = requests.get(f"https://api.runpod.io/v2/serverless/{a.endpoint}/workers", headers=h, timeout=10).json()
        cur = sorted((w["id"], w["status"], w.get("gpuTypeId", "")) for w in d.get("workers", []))
        if cur != last:
            print(time.strftime("%H:%M:%S"), json.dumps(cur), flush=True)
            last = cur
    except Exception as e:
        print(time.strftime("%H:%M:%S"), "ERR", type(e).__name__, flush=True)
    time.sleep(5)
