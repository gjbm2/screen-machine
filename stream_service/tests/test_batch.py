"""batch.py: batch GPU jobs run one at a time per listed GPU, and a live session always wins the GPU."""
import asyncio

import pytest

from stream_service.batch import Batch, known_kind


async def until(cond, timeout=8.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.02)


@pytest.fixture
async def batch():
    live = {}                                   # gpu -> why it is busy
    b = Batch([1], lambda gpu: live.get(gpu), tick_s=0.02)
    b.live = live
    task = asyncio.create_task(b.run())
    yield b
    task.cancel()
    await b.close()


def test_a_kind_is_a_module_of_batch_jobs():
    assert known_kind("sleep")
    assert not known_kind("nope") and not known_kind("../sleep") and not known_kind("__init__") and not known_kind(3)


async def test_a_job_runs_to_done_on_the_listed_gpu(batch, tmp_path):
    job = batch.submit("sleep", {"s": 0.2, "touch": str(tmp_path / "made")})
    await until(lambda: job.state == "done")
    assert job.gpu == 1 and job.progress == 1.0 and job.error is None and (tmp_path / "made").exists()


async def test_jobs_run_one_at_a_time_in_order(batch):
    a, b = batch.submit("sleep", {"s": 0.4}), batch.submit("sleep", {"s": 0.1})
    await until(lambda: a.state == "running")
    await asyncio.sleep(0.1)
    assert b.state == "queued" and b.waiting == "another job is running"
    await until(lambda: b.state == "done")
    assert a.state == "done" and a.finished <= b.started


async def test_a_job_waits_while_its_gpu_is_live(batch):
    batch.live[1] = "north-screen is live"
    job = batch.submit("sleep", {"s": 0.1})
    await asyncio.sleep(0.2)
    assert job.state == "queued" and job.waiting == "north-screen is live" and job.restarts == 0
    del batch.live[1]
    await until(lambda: job.state == "done")


async def test_a_session_taking_the_gpu_kills_the_job_and_it_runs_again_from_the_start(batch, tmp_path):
    job = batch.submit("sleep", {"s": 1.5, "touch": str(tmp_path / "made")})
    await until(lambda: job.state == "running" and job.progress > 0)
    pid = job.proc.pid
    batch.live[1] = "north-screen is live"
    await until(lambda: job.state == "queued")
    assert job.restarts == 1 and job.progress == 0.0 and not (tmp_path / "made").exists()
    with pytest.raises(ProcessLookupError):
        import os
        os.kill(pid, 0)                         # its process is gone, not paused
    del batch.live[1]
    await until(lambda: job.state == "done")
    assert (tmp_path / "made").exists() and job.restarts == 1


async def test_a_failed_job_says_why_and_the_next_one_still_runs(batch):
    bad, good = batch.submit("sleep", {"s": 0.1, "fail": "the clip is not a video"}), batch.submit("sleep", {"s": 0.1})
    await until(lambda: good.state == "done")
    assert bad.state == "failed" and "the clip is not a video" in bad.error


async def test_cancel_a_running_and_a_queued_job(batch):
    a, b = batch.submit("sleep", {"s": 5}), batch.submit("sleep", {"s": 5})
    await until(lambda: a.state == "running")
    assert batch.cancel(b.id).state == "cancelled"
    batch.cancel(a.id)
    await until(lambda: a.state == "cancelled")
    assert batch.cancel("nope") is None


async def test_finished_jobs_are_forgotten_after_a_while(batch):
    batch.keep_s = 0.1
    job = batch.submit("sleep", {"s": 0.05})
    await until(lambda: job.id not in batch.jobs)


# ------------------------------------------------------------------ the API

async def _client(batch_gpus):
    import pathlib

    from aiohttp.test_utils import TestClient, TestServer

    from stream_service.server import make_app

    config = pathlib.Path(__file__).resolve().parents[2] / "config" / "live"
    return TestClient(TestServer(make_app(config_dir=config, batch_gpus=batch_gpus)))


async def test_api_with_no_batch_gpu_listed_says_so():
    async with await _client([]) as c:
        r = await c.post("/api/batch", json={"kind": "sleep", "args": {}})
        assert r.status == 503 and (await r.json())["error"] == "no_batch_gpu"
        assert (await (await c.get("/api/batch")).json()) == {"gpus": [], "jobs": []}


async def test_api_queue_read_and_cancel():
    async with await _client([1]) as c:
        assert (await c.post("/api/batch", json={"kind": "nope", "args": {}})).status == 400
        assert (await c.post("/api/batch", json={"kind": "sleep", "args": "x"})).status == 400
        r = await c.post("/api/batch", json={"kind": "sleep", "args": {"s": 0.2}})
        job = await r.json()
        assert r.status == 202 and job["state"] == "running" and job["gpu"] == 1    # a free GPU takes it at once

        async def state():
            return (await (await c.get(f"/api/batch/{job['id']}")).json())["state"]
        for _ in range(100):
            if await state() == "done":
                break
            await asyncio.sleep(0.05)
        assert await state() == "done"
        slow = await (await c.post("/api/batch", json={"kind": "sleep", "args": {"s": 30}})).json()
        gone = await c.delete(f"/api/batch/{slow['id']}")
        assert gone.status == 200
        assert [j["id"] for j in (await (await c.get("/api/batch")).json())["jobs"]] == [job["id"], slow["id"]]
        assert (await c.get("/api/batch/nope")).status == 404 and (await c.delete("/api/batch/nope")).status == 404
