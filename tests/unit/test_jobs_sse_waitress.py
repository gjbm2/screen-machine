"""The job stream (SSE) under waitress, as served in production: events must reach the client as they
happen, not when a buffer fills or the stream ends."""
import http.client
import threading
import time

import pytest
from flask import Flask
from waitress.server import create_server

from routes.jobs import set_tracker
from routes.jobs.store import JobStore
from routes.jobs.tracker import JobTracker


@pytest.fixture
def served(tmp_path):
    from routes.jobs.api import jobs_api
    tracker = JobTracker(store=JobStore(str(tmp_path / "jobs.db")), progress_interval=0.05)
    set_tracker(tracker)
    app = Flask(__name__)
    app.register_blueprint(jobs_api, url_prefix="/api")
    # The same server settings app.py uses
    server = create_server(app, host="127.0.0.1", port=0, threads=4)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    yield tracker, server.effective_port
    server.close()
    set_tracker(None)


def test_events_arrive_promptly_under_waitress(served):
    tracker, port = served
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/api/jobs/stream")
    resp = conn.getresponse()
    assert resp.status == 200
    assert resp.getheader("Content-Type").startswith("text/event-stream")
    assert resp.getheader("Cache-Control") == "no-cache"

    def next_block():
        # http.client decodes chunked bodies in read1(); gather until a blank line
        buf = b""
        while b"\n\n" not in buf:
            chunk = resp.read1(65536)
            if not chunk:
                break
            buf += chunk
        return buf.decode()

    first = next_block()
    if "event: snapshot" not in first:
        first += next_block()
    assert "retry: 3000" in first and "event: snapshot" in first

    delays = []
    for i in range(3):
        sent = time.monotonic()
        tracker.create(prompt=f"job {i}")
        block = next_block()
        delays.append(time.monotonic() - sent)
        assert "event: job" in block and f"job {i}" in block
        time.sleep(0.1)
    conn.close()
    # Each small event must be flushed at once (waitress would otherwise hold it until ~18 KB or the end)
    assert max(delays) < 0.5, delays
