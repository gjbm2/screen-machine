"""routes/scheduler_room.py: the Room page's view of a room's schedules (no names in code)."""

from routes.scheduler_room import build_room_schedule, export_order, label_for, room_group, schedule_events

GROUP = "drawing-room"

CLOUD = {
    "initial_actions": {"instructions_block": []},
    "triggers": [
        {"type": "day_of_week", "days": ["Monday"], "scheduled_actions": [
            {"time": "06:00", "trigger_actions": {"instructions_block": [{"action": "throw_event", "event": "Get_inspiration"}]}}]},
        {"type": "event", "value": "Develop_theme", "trigger_actions": {"instructions_block": [
            {"action": "export_var", "var_name": "a_theme", "friendly_name": "A theme", "scope": GROUP},
            {"action": "export_var", "var_name": "secret", "scope": GROUP, "room": False},
            {"action": "export_var", "var_name": "style", "scope": GROUP, "description": "The look"},
        ]}},
        {"type": "event", "value": "Get_inspiration", "display_name": "New idea", "trigger_actions": {"instructions_block": [
            {"action": "throw_event", "event": "Develop_theme", "scope": "cloud"},
            {"action": "export_var", "var_name": "inspiration", "scope": GROUP},
        ]}},
        {"type": "event", "value": "_internal", "trigger_actions": {"instructions_block": []}},
        {"type": "event", "value": "Hidden", "room": False, "trigger_actions": {"instructions_block": []}},
    ],
}
NORTH = {"triggers": [
    {"type": "event", "value": "Generate", "description": "Make a picture", "trigger_actions": {"instructions_block": []}},
    {"type": "event", "value": "_room_mode", "trigger_actions": {"instructions_block": []}},
]}
ROOMS = {"_comment": "x", GROUP: {"name": "Drawing room", "screens": ["north"]}, "lab": {"screens": ["dev"], "scheduler_group": "bench"}}
DESTS = [{"id": "cloud", "name": "Cloud"}, {"id": "north", "name": "North screen", "groups": [GROUP]}]
REGISTRY = {
    "groups": {GROUP: {
        "a_theme": {"friendly_name": "A theme", "owner": "cloud", "timestamp": "2026-10-24T06:00:10"},
        "inspiration": {"friendly_name": "inspiration", "owner": "cloud", "timestamp": "2026-10-24T06:00:06"},
        "secret": {"friendly_name": "secret", "owner": "cloud", "timestamp": "2026-10-24T06:00:10"},
        "style": {"friendly_name": "style", "owner": "cloud", "timestamp": "2026-10-24T06:00:10",
                  "edited_by": "Room page", "edited_at": "2026-10-24T09:00:00"},
        "_hidden": {"friendly_name": "h", "owner": "cloud", "timestamp": "2026-10-24T06:00:10"},
    }},
    "imports": {"a_theme": {"north": {"imported_as": "_a_theme", "source_type": "scope", "source": GROUP}}},
}
STACKS = {"cloud": [CLOUD], "north": [NORTH]}
CONTEXTS = {"cloud": [{"vars": {"a_theme": "Owls", "inspiration": "Autumn", "style": "Ink", "secret": "s", "_hidden": 1}}]}


def test_labels_and_groups():
    assert label_for("Develop_theme") == "Develop theme"
    assert label_for("get-inspiration") == "Get inspiration"
    assert room_group(GROUP, ROOMS) == GROUP
    assert room_group("lab", ROOMS) == "bench"


def test_export_order_follows_a_run():
    # 06:00 throws Get_inspiration, which exports inspiration, then (thrown) Develop_theme exports the rest
    assert export_order(CLOUD, GROUP) == ["inspiration", "a_theme", "secret", "style"]


def test_events_are_public_only():
    evs = schedule_events(CLOUD)
    assert [e["event"] for e in evs] == ["Develop_theme", "Get_inspiration"]
    assert [e["label"] for e in evs] == ["Develop theme", "New idea"]
    assert schedule_events(NORTH) == [{"event": "Generate", "label": "Generate", "description": "Make a picture"}]


def test_build_room_schedule():
    out = build_room_schedule(GROUP, ROOMS, DESTS, REGISTRY, STACKS, CONTEXTS)
    vs = out["variables"]
    assert [v["name"] for v in vs] == ["inspiration", "a_theme", "style"]
    assert vs[0]["label"] == "Inspiration" and vs[0]["value"] == "Autumn" and vs[0]["set_by"] == "Cloud"
    assert vs[1]["used_by"] == [{"id": "north", "name": "North screen"}]
    assert vs[2]["set_by"] == "Room page" and vs[2]["set_at"] == "2026-10-24T09:00:00" and vs[2]["description"] == "The look"
    assert [(a["event"], a["scope"]) for a in out["actions"]] == [("Develop_theme", "cloud"), ("Get_inspiration", "cloud")]
    assert [e["event"] for e in out["screens"]["north"]] == ["Generate"]
    assert out["screens"]["north"][0]["scope"] == "north"


def test_room_without_exports():
    out = build_room_schedule("lab", ROOMS, DESTS, REGISTRY, STACKS, CONTEXTS)
    assert out["group"] == "bench" and out["variables"] == [] and out["actions"] == [] and out["screens"] == {"dev": []}
