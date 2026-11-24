"""A screen clip's ease made on the renderer's GPU (routes/clip_ease.py _on_gpu -> stream_service batch queue ->
stream_service/batch_jobs/ease.py), and the CPU taking over whenever the GPU can't."""
import json

import pytest

from routes import clip_ease as ce


def _old_loop(n, play_s, ramp_s, settle_s, fps, ramp_out_s=None):
    """The ease loop as it was written inline before the schedule was split out (the CPU's frames, unchanged)."""
    out = []
    for k in range(int(round(play_s * fps)) + 1):
        t = k / fps
        x = ce.position(t, play_s, ramp_s, ramp_out_s) * (n - 1)
        wgt = 0.0
        if settle_s and t > play_s - settle_s:
            u = (t - (play_s - settle_s)) / settle_s
            wgt = u * u * (3 - 2 * u)
        out.append([x, wgt])
    return out


@pytest.mark.parametrize("args", [(121, 30, 8, 3, 30), (121, 12, 3, 2, 30), (73, 5.5, 1.5, 0, 24, 2.0)])
def test_schedule_is_the_old_loop(args):
    frames = ce.schedule(*args)
    assert frames == _old_loop(*args)
    assert frames[0] == [0.0, 0.0] and all(b[0] >= a[0] for a, b in zip(frames, frames[1:]))   # never goes back


class _Resp:
    def __init__(self, code, body=None):
        self.status_code, self._body = code, body or {}

    def json(self):
        return self._body


class _Renderer:
    """The batch API: POST answers `post`, then GETs answer `states` in turn (the last repeats)."""

    def __init__(self, post, states=(), on_done=None):
        self.post_resp, self.states, self.on_done = post, list(states), on_done
        self.posted, self.deleted = [], []

    def post(self, url, json=None, timeout=None):
        if isinstance(self.post_resp, Exception):
            raise self.post_resp
        self.posted.append(json)
        return self.post_resp

    def get(self, url, timeout=None):
        st = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if isinstance(st, _Resp):
            return st
        if st.get("state") == "done" and self.on_done:
            self.on_done()
        return _Resp(200, st)

    def delete(self, url, timeout=None):
        self.deleted.append(url)
        return _Resp(200)


@pytest.fixture
def renderer(monkeypatch):
    import requests
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(ce, "_idle", False)
    monkeypatch.setenv("CLIP_EASE_GPU", "1")      # the conftest keeps it off for every other test

    def use(r):
        for verb in ("post", "get", "delete"):
            monkeypatch.setattr(requests, verb, getattr(r, verb))
        return r
    return use


def test_done_on_the_gpu_reads_the_motion_and_leaves_no_file(renderer, tmp_path):
    dst = tmp_path / "out.mp4"
    seen = []
    r = renderer(_Renderer(_Resp(202, {"id": "b1"}),
                           [{"state": "queued", "waiting": None}, {"state": "running", "progress": 0.5},
                            {"state": "done"}],
                           on_done=lambda: (tmp_path / "out.mp4.json").write_text(json.dumps({"motion": 12.5}))))
    frames = ce.schedule(121, 30, 8, 3, 30)
    assert ce._on_gpu("in.mp4", str(dst), 30, frames, seen.append) == 12.5
    assert not (tmp_path / "out.mp4.json").exists()
    assert seen == [0.5]
    job = r.posted[0]
    assert job["kind"] == "ease" and job["args"]["frames"] == frames and job["args"]["still"] is None
    assert job["args"]["dst"] == str(dst) and job["args"]["fps"] == 30


@pytest.mark.parametrize("post", [_Resp(503, {"error": "no_batch_gpu"}), _Resp(400), ConnectionError("down")])
def test_no_gpu_batch_means_the_cpu_at_once(renderer, post):
    renderer(_Renderer(post))
    assert ce._on_gpu("in.mp4", "/tmp/x.mp4", 30, [[0, 0]], lambda f: None) is None
    assert ce._idle is False          # nothing live held it up: the CPU at its usual priority


@pytest.mark.parametrize("state", [{"state": "failed", "error": "boom"}, {"state": "cancelled"}, _Resp(404)])
def test_a_failed_or_forgotten_job_means_the_cpu(renderer, state):
    renderer(_Renderer(_Resp(202, {"id": "b1"}), [state]))
    assert ce._on_gpu("in.mp4", "/tmp/x.mp4", 30, [[0, 0]], lambda f: None) is None


def test_held_too_long_behind_a_live_tv_cancels_and_goes_idle(renderer, monkeypatch):
    import time
    clock = iter(range(0, 10_000, 60))
    monkeypatch.setattr(time, "time", lambda: next(clock))
    r = renderer(_Renderer(_Resp(202, {"id": "b1"}), [{"state": "queued", "waiting": "north-screen is live"}]))
    assert ce._on_gpu("in.mp4", "/tmp/x.mp4", 30, [[0, 0]], lambda f: None) is None
    assert r.deleted and r.deleted[0].endswith("/b1")
    assert ce._idle is True           # the CPU makes it at idle priority


def _clip(path, frames):
    import subprocess
    import numpy as np
    raw = np.stack(frames).astype(np.uint8).tobytes()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "64x36", "-r", "30",
                    "-i", "-", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", str(path)], input=raw, check=True)


def test_a_hard_cut_back_to_the_picture_is_found(tmp_path):
    import numpy as np
    # drifts slowly away from the picture, then cuts straight back to it at frame 40 (owner, 24 Nov: "snapped back")
    frames = [np.full((36, 64), 40 + i * 2, np.uint8) for i in range(40)] + [np.full((36, 64), 40, np.uint8)] * 10
    _clip(tmp_path / "cut.mp4", frames)
    assert ce.cut_at(str(tmp_path / "cut.mp4")) == 40
    _clip(tmp_path / "smooth.mp4", [np.full((36, 64), 40 + i * 2, np.uint8) for i in range(50)])
    assert ce.cut_at(str(tmp_path / "smooth.mp4")) is None
