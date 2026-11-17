"""routes.jobs.runpod_api.billed_usd: what RunPod bills for a finished job (no network: the reads are faked)."""
from datetime import datetime, timezone

import pytest

from routes.jobs import runpod_api

A100_S = 2.99 / 3600
H100_S = 5.89 / 3600
PRICES = {"gpu": {"NVIDIA A100 80GB PCIe": A100_S, "NVIDIA H100 80GB HBM3": H100_S},
          "pool": {"AMPERE_80": A100_S, "ADA_80_PRO": H100_S}}
SENT = 1_800_000_000.0


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture
def fake(monkeypatch):
    state = {"worker": None}
    monkeypatch.setattr(runpod_api, "serverless_prices", lambda: PRICES)
    monkeypatch.setattr(runpod_api, "endpoint_info", lambda ep: {"name": "render-krea", "pools": ["AMPERE_80", "ADA_80_PRO"]})
    monkeypatch.setattr(runpod_api, "worker", lambda ep, wid: state["worker"])
    return state


def test_warm_worker_bills_execution_only_at_its_gpu(fake):
    fake["worker"] = {"id": "w1", "gpuTypeId": "NVIDIA H100 80GB HBM3", "startedAt": _iso(SENT - 600)}
    usd, how = runpod_api.billed_usd("ep", {"executionTime": 20000, "delayTime": 3000, "workerId": "w1"}, SENT)
    assert usd == pytest.approx(20 * H100_S)
    assert "NVIDIA H100" in how


def test_cold_worker_adds_its_boot_but_not_queue_wait(fake):
    # sent at SENT, worker started 10 s later, job started 25 s after sending: 15 s cold start, 10 s queue wait
    fake["worker"] = {"id": "w1", "gpuTypeId": "NVIDIA A100 80GB PCIe", "startedAt": _iso(SENT + 10)}
    usd, _ = runpod_api.billed_usd("ep", {"executionTime": 20000, "delayTime": 25000, "workerId": "w1"}, SENT)
    assert usd == pytest.approx(35 * A100_S)


def test_unknown_worker_prices_on_the_first_pool(fake):
    usd, how = runpod_api.billed_usd("ep", {"executionTime": 10000, "delayTime": 40000, "workerId": "gone"}, SENT)
    assert usd == pytest.approx(10 * A100_S)
    assert "AMPERE_80" in how


def test_no_execution_time_is_no_bill(fake):
    assert runpod_api.billed_usd("ep", {"status": "COMPLETED"}, SENT)[0] is None


def test_catalogue_prices_by_gpu_and_dearest_in_pool():
    p = runpod_api._prices({"gpus": [
        {"id": "a", "pool": "P", "price": {"serverless": 3.6}},
        {"id": "b", "pool": "P", "price": {"serverless": 7.2}},
        {"id": "c", "pool": None, "price": {"secure": 1.0}},
    ]})
    assert p["gpu"] == {"a": 0.001, "b": 0.002} and p["pool"] == {"P": 0.002}


def test_cost_in_gbp_uses_the_bill(monkeypatch):
    from routes import generate_handler
    assert generate_handler.calculate_generation_cost(99, runpod_id="ep", billing={"usd": 1.3, "how": "test"}) == 1.0


def test_billing_gives_the_worker_time_paid_for(fake):
    fake["worker"] = {"id": "w1", "gpuTypeId": "NVIDIA A100 80GB PCIe", "startedAt": _iso(SENT + 10)}
    b = runpod_api.billing("ep", {"executionTime": 20000, "delayTime": 25000, "workerId": "w1"}, SENT, SENT + 60)
    assert b["billed_s"] == 35.0 and b["cold_s"] == 15.0 and b["gpu"] == "NVIDIA A100 80GB PCIe"
    assert b["billed_to"] == SENT + 60 and b["billed_from"] == SENT + 25
    assert b["usd"] == pytest.approx(35 * A100_S, abs=1e-6)
