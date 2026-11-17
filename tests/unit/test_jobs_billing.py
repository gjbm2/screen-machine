"""routes.jobs.billing: jobs share RunPod's hourly bill (no network: the hours are given)."""
import pytest

from routes.jobs import billing
from routes.jobs.store import JobStore
from routes.jobs.tracker import JobTracker

H = 1_800_000_000.0 - 1_800_000_000.0 % 3600   # an hour boundary


def _job(tracker, jid, endpoint, t0, t1, cost=0.01, **extra):
    job = {"id": jid, "created_at": t0 - 5, "status": "completed", "source": "scheduler", "endpoint_id": endpoint,
           "workflow": "kreav2.json", "cost_gbp": cost, "cost_state": "provisional",
           "billing": {"billed_from": t0, "billed_to": t1}, **extra}
    tracker.store.upsert(job)


@pytest.fixture
def tracker():
    return JobTracker(store=JobStore(":memory:"))


def test_an_hour_is_shared_by_billed_time(tracker):
    _job(tracker, "a", "ep", H + 100, H + 130)    # 30 s
    _job(tracker, "b", "ep", H + 1000, H + 1090)  # 90 s
    r = billing.reconcile(tracker, H, H + 3600, hours=[("ep", H, 1.20)])
    a, b = tracker.store.get("a"), tracker.store.get("b")
    assert a["cost_gbp"] == pytest.approx(0.30 * billing.FX_USD_TO_GBP, abs=1e-6)
    assert b["cost_gbp"] == pytest.approx(0.90 * billing.FX_USD_TO_GBP, abs=1e-6)
    assert a["cost_state"] == "reconciled" and a["cost_provisional_gbp"] == 0.01
    assert r["attributed_usd"] == pytest.approx(1.20)


def test_a_job_across_two_hours_gets_a_share_of_each(tracker):
    _job(tracker, "a", "ep", H + 3540, H + 3660)  # 60 s in each hour
    _job(tracker, "b", "ep", H + 3700, H + 3760)  # 60 s in the second
    billing.reconcile(tracker, H, H + 7200, hours=[("ep", H, 0.50), ("ep", H + 3600, 1.00)])
    assert tracker.store.get("a")["cost_gbp"] == pytest.approx((0.50 + 0.50) * billing.FX_USD_TO_GBP, abs=1e-6)
    assert tracker.store.get("b")["cost_gbp"] == pytest.approx(0.50 * billing.FX_USD_TO_GBP, abs=1e-6)


def test_other_endpoints_and_empty_hours_stay_unattributed(tracker):
    _job(tracker, "a", "ep", H + 100, H + 160)
    billing.reconcile(tracker, H, H + 7200, hours=[("ep", H, 1.0), ("test", H, 2.0), ("ep", H + 3600, 0.3)])
    s = billing.summary(tracker, days=1, now=H + 7200)
    assert s["billed_gbp"] == pytest.approx(3.3 * billing.FX_USD_TO_GBP, abs=0.01)
    assert s["unattributed_gbp"] == pytest.approx(2.3 * billing.FX_USD_TO_GBP, abs=0.01)
    assert tracker.store.get("a")["cost_gbp"] == pytest.approx(1.0 * billing.FX_USD_TO_GBP, abs=1e-6)


def test_a_job_partly_outside_the_hours_read_is_left_alone(tracker):
    _job(tracker, "a", "ep", H - 30, H + 30)
    billing.reconcile(tracker, H, H + 3600, hours=[("ep", H, 1.0)])
    assert tracker.store.get("a")["cost_state"] == "provisional"


def test_older_jobs_without_billing_use_sent_to_finished(tracker):
    tracker.store.upsert({"id": "old", "created_at": H, "status": "completed", "endpoint_id": "ep",
                          "submitted_at": H + 10, "publishing_at": H + 70, "finished_at": H + 75, "cost_gbp": 0.02})
    billing.reconcile(tracker, H, H + 3600, hours=[("ep", H, 0.65)])
    assert tracker.store.get("old")["cost_gbp"] == pytest.approx(0.65 * billing.FX_USD_TO_GBP, abs=1e-6)
