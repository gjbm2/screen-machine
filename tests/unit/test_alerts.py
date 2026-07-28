"""Unit tests for the Siren alerting subsystem (utils/alerts/).

Policy functions are driven directly with controlled `now` values so no test
depends on wall-clock timing; one end-to-end test exercises the real worker
thread with a FakeChannel. Teardown always leaves the dispatcher bound to an
inert channel list and a throwaway state path so a leaked worker cannot touch
the real state file or Gmail.
"""

import time

import pytest

import config
from utils.alerts import alert, dispatcher
from utils.alerts.state import AlertState

CRIT_COOLDOWN = config.ALERT_THROTTLE["critical"]["min_interval_s"]
CRIT_REMINDER = config.ALERT_THROTTLE["critical"]["reminder_s"]
ERR_COOLDOWN = config.ALERT_THROTTLE["error"]["min_interval_s"]
CAP = config.ALERT_GLOBAL_MAX_PER_HOUR


class FakeChannel:
    name = "fake"

    def __init__(self, fail_times=0):
        self.sent = []
        self.fail_times = fail_times

    def configured(self):
        return True

    def send(self, subject, body):
        if self.fail_times > 0:
            self.fail_times -= 1
            return False
        self.sent.append((subject, body))
        return True


def _record(category="openai.quota", severity="critical", key=None, summary="boom"):
    return {
        "category": category,
        "summary": summary,
        "severity": severity,
        "detail": "",
        "exc_text": None,
        "context": {},
        "dedup_key": key or category,
        "ts": time.time(),
    }


@pytest.fixture
def fake(tmp_path):
    ch = FakeChannel()
    dispatcher._reset_for_tests(state_path=str(tmp_path / "alerts_state.json"),
                                channels=[ch])
    yield ch
    dispatcher._reset_for_tests(state_path=str(tmp_path / "teardown.json"),
                                channels=[])


def test_alert_never_raises_on_garbage(monkeypatch):
    monkeypatch.setenv("ALERTING_ENABLED", "0")
    alert(None, None)
    alert(object(), object(), severity="nonsense", exc="not-an-exception",
          context="not-a-dict", dedup_key=12345)
    alert("x", "y", exc=ValueError("boom"), context={"k": object()})


def test_critical_sends_once_then_suppresses(fake):
    now = time.time()
    dispatcher._process(_record(), now)
    dispatcher._process(_record(), now + 1)
    dispatcher._process(_record(), now + 2)
    assert len(fake.sent) == 1
    subject, body = fake.sent[0]
    assert "CRITICAL" in subject and "openai.quota" in subject
    entry = dispatcher._get_state().keys["openai.quota"]
    assert entry["count"] == 3
    assert entry["count_since_sent"] == 2


def test_recurring_critical_does_not_resend_after_cooldown(fake):
    """The 6-h reminder owns redelivery during a live incident — a recurring
    critical must NOT re-email every cooldown window (would be 144/day)."""
    now = time.time()
    dispatcher._process(_record(), now)
    dispatcher._process(_record(), now + CRIT_COOLDOWN + 1)
    dispatcher._process(_record(), now + 2 * CRIT_COOLDOWN + 2)
    assert len(fake.sent) == 1


def test_lapsed_incident_resends_immediately(fake):
    """A key quiet for a full reminder window is a NEW incident."""
    now = time.time()
    dispatcher._process(_record(), now)
    dispatcher._process(_record(), now + CRIT_REMINDER + 100)
    assert len(fake.sent) == 2


def test_critical_reminder_reports_suppressed_count(fake):
    now = time.time()
    dispatcher._process(_record(), now)
    for i in range(10):
        dispatcher._process(_record(), now + 1 + i)
    assert len(fake.sent) == 1
    # incident still live (last occurrence recent relative to reminder check)
    dispatcher._critical_reminders(now + CRIT_REMINDER + 5)
    assert len(fake.sent) == 2
    assert "STILL FAILING" in fake.sent[1][0]
    assert "10 time(s)" in fake.sent[1][1]


def test_no_reminder_for_stopped_incident(fake):
    """An incident that stopped recurring closes quietly — no STILL FAILING
    email for something that is over."""
    now = time.time()
    dispatcher._process(_record(), now)
    dispatcher._process(_record(), now + 10)  # suppressed occurrence, then quiet
    dispatcher._critical_reminders(now + 2 * CRIT_REMINDER)
    assert len(fake.sent) == 1
    entry = dispatcher._get_state().keys["openai.quota"]
    assert entry["count_since_sent"] == 0


def test_error_resends_after_cooldown_with_count(fake):
    now = time.time()
    r = _record(category="app.unhandled", severity="error", key="err1")
    dispatcher._process(r, now)
    for i in range(5):
        dispatcher._process(r, now + 1 + i)
    dispatcher._process(r, now + ERR_COOLDOWN + 1)
    assert len(fake.sent) == 2
    assert "Suppressed since last email for this key: 5" in fake.sent[1][1]


def test_storm_brake_caps_email_volume(fake):
    now = time.time()
    for i in range(CAP + 5):
        dispatcher._process(
            _record(category="app.unhandled", severity="error", key=f"k{i}"),
            now + i * 0.001)
    # cap ordinary sends + exactly one storm notification
    assert len(fake.sent) == CAP + 1
    assert "alerting.storm" in fake.sent[CAP][0]
    assert dispatcher._storm_active


def test_storm_suppressed_alert_is_not_lost(fake):
    """A storm-suppressed alert's state stays untouched, so it redelivers
    (with counts) once the window frees."""
    now = time.time()
    for i in range(CAP):
        dispatcher._process(
            _record(category="app.unhandled", severity="error", key=f"k{i}"), now)
    dispatcher._process(_record(key="quota"), now + 1)   # suppressed by storm
    entry = dispatcher._get_state().keys["quota"]
    assert entry["last_attempt"] == 0.0 and entry["count_since_sent"] == 1
    dispatcher._send_times.clear()                        # window frees
    dispatcher._process(_record(key="quota"), now + 2)
    assert any("quota" in s for s, _ in fake.sent)


def test_failed_sends_do_not_consume_storm_slots(fake):
    fake.fail_times = 999
    now = time.time()
    for i in range(5):
        dispatcher._process(
            _record(category="app.unhandled", severity="error", key=f"f{i}"), now)
    assert len(dispatcher._send_times) == 0
    assert len(dispatcher._pending_retries) == 5


def test_failed_send_is_retried_with_spacing(fake):
    fake.fail_times = 2
    now = time.time()
    dispatcher._process(_record(), now)
    assert fake.sent == [] and dispatcher._channel_failures == 1
    dispatcher._retry_pending(now + 1)                     # not due yet
    assert fake.sent == []
    dispatcher._retry_pending(now + dispatcher.RETRY_SPACING_S + 1)   # fails again
    assert fake.sent == []
    dispatcher._retry_pending(now + 3 * dispatcher.RETRY_SPACING_S + 2)  # delivers
    assert len(fake.sent) == 1
    assert dispatcher._channel_failures == 0


def test_undelivered_critical_reaches_reminder_path(fake):
    """If every retry fails, the reminder still re-attempts the undelivered
    critical instead of dropping it forever."""
    fake.fail_times = 1 + dispatcher.RETRY_MAX_ATTEMPTS
    now = time.time()
    dispatcher._process(_record(), now)
    t = now
    for i in range(dispatcher.RETRY_MAX_ATTEMPTS + 1):
        t += dispatcher.RETRY_SPACING_S * (i + 1) + 1
        dispatcher._retry_pending(t)
    assert fake.sent == [] and not dispatcher._pending_retries
    dispatcher._critical_reminders(now + CRIT_REMINDER + 1)
    assert len(fake.sent) == 1
    assert "STILL FAILING" in fake.sent[0][0]


def test_warning_batching_rolls_up(fake):
    now = time.time()
    dispatcher._last_warning_flush = now
    for _ in range(5):
        dispatcher._process(
            _record(category="device.samsung_token", severity="warning",
                    key="tv:north", summary="re-pair needed"), now)
    dispatcher._process(
        _record(category="assemblyai.stream", severity="warning",
                key="aai", summary="gave up"), now)
    assert fake.sent == []
    flush_s = config.ALERT_THROTTLE["warning"]["batch_flush_s"]
    dispatcher._flush_warnings(now + flush_s + 1)
    assert len(fake.sent) == 1
    body = fake.sent[0][1]
    assert "tv:north" in body and "(x5)" in body and "aai" in body


def test_newline_in_summary_yields_single_line_subject(fake):
    now = time.time()
    dispatcher._process(
        _record(summary="line one\r\nline two\nline three"), now)
    assert len(fake.sent) == 1
    assert "\n" not in fake.sent[0][0] and "\r" not in fake.sent[0][0]


def test_poisoned_state_file_starts_fresh(tmp_path):
    p = tmp_path / "alerts_state.json"
    p.write_text("{not json at all", encoding="utf-8")
    st = AlertState.load(str(p))
    assert st.keys == {}
    ch = FakeChannel()
    dispatcher._reset_for_tests(state_path=str(p), channels=[ch])
    dispatcher._process(_record(), time.time())
    assert len(ch.sent) == 1
    dispatcher._reset_for_tests(state_path=str(tmp_path / "teardown.json"),
                                channels=[])


def test_state_round_trips_atomically(tmp_path):
    p = str(tmp_path / "alerts_state.json")
    st = AlertState.load(p)
    e = st.entry("k1", "critical", 100.0)
    e["count"] = 7
    e["last_sent"] = 100.0
    st.save(101.0)
    st2 = AlertState.load(p)
    assert st2.keys["k1"]["count"] == 7
    assert st2.last_alive is not None


def test_info_is_logged_never_emailed(fake):
    dispatcher._process(_record(category="app.startup", severity="info"),
                        time.time())
    assert fake.sent == []


def test_stats_does_not_mutate_windows(fake):
    dispatcher._emails_24h.append(time.time() - 90000)   # expired entry
    before = len(dispatcher._emails_24h)
    s = dispatcher.stats()
    assert s["emails_24h"] == 0
    assert len(dispatcher._emails_24h) == before          # pruning is worker-only


def test_end_to_end_pipeline_with_worker(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERTING_ENABLED", "1")
    ch = FakeChannel()
    dispatcher._reset_for_tests(state_path=str(tmp_path / "s.json"),
                                channels=[ch])
    try:
        alert("selftest", "pipeline test", severity="critical",
              dedup_key=f"pipeline-{time.time()}")
        deadline = time.time() + 10
        while time.time() < deadline and not ch.sent:
            time.sleep(0.05)
        assert len(ch.sent) == 1
        assert dispatcher.stats()["worker_alive"]
    finally:
        monkeypatch.setenv("ALERTING_ENABLED", "0")
        dispatcher._reset_for_tests(state_path=str(tmp_path / "teardown.json"),
                                    channels=[])
