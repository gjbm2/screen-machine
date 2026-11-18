"""Save a serverless worker's logs (REST v2 SSE stream; it never closes, so read until quiet).

    python worker/worker_logs.py <endpoint_id> [worker_id ...] [--since 2026-11-18T09:00:00Z] [--out DIR]

No worker ids: every worker the endpoint lists. One file per worker: <out>/<worker>.txt, lines "ts source line".
"""
import argparse
import json
import os
import threading

import requests

REST = "https://api.runpod.io/v2"


def headers():
    return {"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"}


def fetch(endpoint, wid, since, quiet_s=30, max_s=120):
    """Lines until the stream has been quiet for quiet_s or max_s passed. The backfill arrives in bursts with pauses
    between them, and only lasts while the worker exists (a removed container's lines are gone)."""
    params = {"since": since} if since else {"tail": 5000}
    lines, done = [], threading.Event()

    def read():
        try:
            with requests.get(f"{REST}/serverless/{endpoint}/workers/{wid}/logs", headers=headers(), params=params,
                              stream=True, timeout=(10, quiet_s)) as r:
                for raw in r.iter_lines(decode_unicode=True):
                    if raw and raw.startswith("data:"):
                        try:
                            d = json.loads(raw[5:])
                            lines.append(f"{d.get('ts', '')} {d.get('source', '')[:3]} {d.get('line', '')}")
                        except ValueError:
                            pass
        except requests.RequestException:
            pass  # read timeout = quiet
        done.set()

    threading.Thread(target=read, daemon=True).start()
    done.wait(max_s)
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("endpoint")
    ap.add_argument("workers", nargs="*")
    ap.add_argument("--since")
    ap.add_argument("--out", default=".")
    a = ap.parse_args()
    ids = a.workers
    if not ids:
        d = requests.get(f"{REST}/serverless/{a.endpoint}/workers", headers=headers(), timeout=15).json()
        ids = [w["id"] for w in d.get("workers", [])]
    os.makedirs(a.out, exist_ok=True)
    for wid in ids:
        lines = fetch(a.endpoint, wid, a.since)
        with open(os.path.join(a.out, f"{wid}.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
        print(wid, len(lines), "lines", flush=True)


if __name__ == "__main__":
    main()
