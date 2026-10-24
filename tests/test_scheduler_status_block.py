"""routes/scheduler_api.timed_block_info: the next run's block, as /api/schedulers/all/status adds it."""

from datetime import datetime

from routes.scheduler_api import timed_block_info

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def block(time, actions, every=None, until=None):
    sa = {"time": time, "trigger_actions": {"instructions_block": actions}}
    if every is not None:
        sa["repeat_schedule"] = {"every": every, **({"until": until} if until else {})}
    return sa


SCHED = {
    "triggers": [
        {"type": "day_of_week", "days": DAYS, "scheduled_actions": [
            block("07:30", [{"action": "throw_event", "event": "Generate"}], every="60"),
            block("08:05", [{"action": "import_var", "var_name": "x"}, {"action": "throw_event", "event": "Animate"}], every="120", until="19:00"),
        ]},
        {"type": "day_of_week", "days": DAYS, "scheduled_actions": [block("03:00", [{"action": "purge", "days": 30}])]},
        {"type": "event", "value": "Generate", "trigger_actions": {"instructions_block": []}},
    ]
}


def test_repeating_block_rounded_down_minute():
    # the scheduler floors the next time: 21:29 is the 21:30 tick of the 07:30 block
    assert timed_block_info(SCHED, {"next_time": "21:29"}) == {"interval_min": 60, "until": "23:59", "next_label": "Generate"}


def test_repeat_until_and_label_skips_plumbing():
    assert timed_block_info(SCHED, {"next_time": "10:05"}) == {"interval_min": 120, "until": "19:00", "next_label": "Animate"}


def test_single_time_block():
    assert timed_block_info(SCHED, {"next_time": "03:00"}) == {"interval_min": None, "until": None, "next_label": "Purge"}


def test_nothing_next():
    empty = {"interval_min": None, "until": None, "next_label": None}
    assert timed_block_info(SCHED, None) == empty
    assert timed_block_info(None, {"next_time": "21:29"}) == empty
    assert timed_block_info(SCHED, {"next_time": "05:17"}) == empty


def test_prefers_a_block_that_runs_today():
    other = [d for d in DAYS if d != datetime.now().strftime("%A")]
    sched = {"triggers": [
        {"type": "day_of_week", "days": other, "scheduled_actions": [block("09:00", [{"action": "throw_event", "event": "Elsewhere"}])]},
        {"type": "day_of_week", "days": DAYS, "scheduled_actions": [block("09:00", [{"action": "throw_event", "event": "Today"}])]},
    ]}
    assert timed_block_info(sched, {"next_time": "09:00"})["next_label"] == "Today"
