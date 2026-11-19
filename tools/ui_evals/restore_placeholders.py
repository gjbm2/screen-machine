"""Check: after a reload the Generated tab brings back this browser's pictures still being made, from the job tracker
(owner, 19 Nov: "if I hit refresh while jobs are in progress the page loses them until a further refresh").

Fakes the job stream (/api/jobs/stream) so nothing is generated and nothing is spent: the page is told about one
running job whose placeholder id this browser remembers (localStorage activePlaceholders) and one it doesn't (someone
else's). The placeholder must come back for the first only; when the stream then reports the job completed, it must go
and be forgotten.

    ~/.venvs/sm-room/bin/python tools/ui_evals/restore_placeholders.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from playwright.sync_api import sync_playwright

MINE, THEIRS = "ph-eval-mine", "ph-eval-theirs"
BATCH = "evalbatch_restore"


def job(placeholder: str, batch: str, status: str, now: float) -> dict:
    done = status not in ("preparing", "queued", "running", "publishing")
    return {
        "id": f"job-{placeholder}", "batch_id": batch, "batch_index": 0, "placeholder_id": placeholder,
        "source": "generator", "kind": "image", "target": None, "target_name": None,
        "prompt": f"eval placeholder {placeholder}", "refined_prompt": None, "workflow": None, "refiner": None,
        "status": status, "message": None, "stages": ["prepare", "generate", "publish"], "stage_index": 1,
        "percent": 40, "error": None, "runpod_job_id": None, "endpoint_id": None, "created_at": now - 20,
        "submitted_at": now - 18, "started_at": now - 15, "finished_at": now if done else None,
        "updated_at": now, "duration_s": None, "cost_gbp": None, "seed": None, "result_url": None,
        "published_path": None, "cancel_requested": False,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:5000/", help="this machine passes the sign-in gates")
    a = ap.parse_args()
    state = {"status": "running"}

    def stream(route):
        now = time.time()
        jobs = [job(MINE, BATCH, state["status"], now), job(THEIRS, "evalbatch_other", "running", now)]
        body = f"retry: 300\nevent: snapshot\ndata: {json.dumps({'jobs': jobs, 'server_time': now})}\n\n"
        route.fulfill(status=200, headers={"Content-Type": "text/event-stream"}, body=body)

    failures = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.add_init_script(
            "localStorage.setItem('activePlaceholders', JSON.stringify(["
            f"{{id: '{MINE}', batchId: '{BATCH}', timestamp: Date.now()}}]))"
        )
        page.route("**/api/jobs/stream*", stream)
        page.goto(a.url)
        page.get_by_text("Generated", exact=True).first.click(timeout=20000)

        def shown(pid: str) -> bool:
            return page.evaluate(
                "(t) => document.body.innerText.includes(t)", f"eval placeholder {pid}")

        try:
            page.wait_for_function("document.body.innerText.includes('eval placeholder " + MINE + "')", timeout=15000)
        except Exception:
            failures.append("this browser's running job did not come back")
        if shown(THEIRS):
            failures.append("someone else's running job was shown")

        state["status"] = "completed"
        try:
            page.wait_for_function(
                "!document.body.innerText.includes('eval placeholder " + MINE + "')", timeout=15000)
        except Exception:
            failures.append("the placeholder stayed after its job completed")
        left = page.evaluate("() => localStorage.getItem('activePlaceholders')")
        if MINE in (left or ""):
            failures.append("the finished placeholder was not forgotten (activePlaceholders)")
        browser.close()

    for f in failures:
        print("FAIL", f)
    print("ok" if not failures else f"{len(failures)} failed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
