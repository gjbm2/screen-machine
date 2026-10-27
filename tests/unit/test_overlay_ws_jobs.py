"""overlay_ws_server: RunPod worker frames go to the job tracker (not to screens), a socket that is slow
to send its first frame still has its frames read, and viewers keep getting broadcasts."""
import asyncio
import json

import websockets

import overlay_ws_server
from connection_registry import registry
from routes.jobs import set_tracker
from routes.jobs.tracker import JobTracker


def test_late_sender_frames_reach_tracker_and_viewers_get_broadcasts():
    tracker = JobTracker()
    set_tracker(tracker)
    job_id = tracker.create(prompt="x")["id"]
    tracker.set_stages(job_id, [{"name": "Rendering", "weight": 100}])
    tracker.update(job_id, status="queued", runpod_job_id="rp-late", endpoint_id="ep")

    async def scenario():
        async with websockets.serve(overlay_ws_server.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"
            async with websockets.connect(url) as viewer, websockets.connect(url) as worker:
                await asyncio.sleep(0.7)   # both now classed as viewers
                await worker.send(json.dumps({"job_id": "rp-late",
                                              "comfy": {"type": "progress", "data": {"value": 5, "max": 10}}}))
                await worker.send(json.dumps({"type": "transcription", "text": "hello"}))
                received = json.loads(await asyncio.wait_for(viewer.recv(), 2))
                # the job frame was not broadcast: the first thing a viewer sees is the plain relay
                assert received == {"type": "transcription", "text": "hello"}

    try:
        asyncio.run(scenario())
        job = tracker.get(job_id)
        assert job["status"] == "running" and job["percent"] == 50
        assert job["message"] == "Rendering 5/10"
    finally:
        set_tracker(None)
        registry.overlays.clear()
