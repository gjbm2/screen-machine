"""Room <-> scheduler: the bridge's events (routes/room_events_bridge.diff), mode names (routes/room_client) and the
scheduler's room actions (set_mode, set_auto, room_state) - owner, 17 Oct."""
from datetime import datetime

from routes import room_client
from routes.room_events_bridge import diff

LABELS = {"gallery": "Normal", "party": "Party", "live-test": "Live test (devtest)", "lights-only": "Lights only"}


def facts(mode="gallery", auto=False, north="art", south="art", source="web"):
    return {"mode": mode, "mode_source": source, "auto": auto,
            "screens": {"north-screen": {"layer": north}, "south-screen": {"layer": south}}}


def keys(events):
    return sorted((s, k) for s, k, _ in events)


def test_first_status_sends_everything_as_initial():
    ev = diff(None, facts(), LABELS)
    assert keys(ev) == sorted([(s, k) for s in ("north-screen", "south-screen")
                               for k in ("_room_mode", "_room_auto", "_screen_layer")])
    assert all(p["initial"] for _, _, p in ev)
    mode = [p for s, k, p in ev if k == "_room_mode" and s == "north-screen"][0]
    assert mode == {"mode": "gallery", "label": "Normal", "from": None, "source": "web", "initial": True}


def test_only_changes_after_that():
    a = facts()
    assert diff(a, facts(), LABELS) == []
    ev = diff(a, facts(mode="party", source="schedule:north-screen"), LABELS)
    assert keys(ev) == [("north-screen", "_room_mode"), ("south-screen", "_room_mode")]
    assert ev[0][2]["from"] == "gallery" and ev[0][2]["source"] == "schedule:north-screen" and not ev[0][2]["initial"]
    ev = diff(a, facts(north="live"), LABELS)
    assert ev == [("north-screen", "_screen_layer",
                   {"screen": "north-screen", "layer": "live", "from": "art", "initial": False})]
    ev = diff(a, facts(auto=True), LABELS)
    assert keys(ev) == [("north-screen", "_room_auto"), ("south-screen", "_room_auto")]
    assert ev[0][2]["on"] is True and ev[0][2]["from"] is False


def test_unknowns_are_not_invented():
    f = facts()
    f["auto"], f["mode"] = None, None
    f["screens"]["north-screen"]["layer"] = None
    ev = diff(None, f, LABELS)
    assert keys(ev) == [("south-screen", "_screen_layer")]


def test_mode_names_resolve_by_key_or_label():
    r = lambda n: room_client.resolve_mode(n, LABELS)  # noqa: E731
    assert [r("Normal"), r("normal"), r("party"), r("Live test"), r("live-test"), r("LIGHTS ONLY"), r("nope"), r("")] == \
        ["gallery", "gallery", "party", "live-test", "live-test", "lights-only", None, None]


def test_room_facts_reads_status():
    st = {"mode": {"current": "party", "last_transition": {"to": "party", "source": "autopilot"}},
          "subsystems": {"autopilot": {"believed": "on"}},
          "screens": {"north-screen": {"desired": {"live": {}}, "observed": {"layer": "live", "phase": "live"}}}}
    f = room_client.room_facts(st)
    assert f == {"mode": "party", "mode_source": "autopilot", "auto": True,
                 "screens": {"north-screen": {"layer": "live", "phase": "live", "desired": "live"}}}


def test_scheduler_room_actions(monkeypatch):
    from routes import scheduler_handlers as H
    calls = []
    monkeypatch.setattr(room_client, "set_mode", lambda m, source, reason=None: (calls.append(("mode", m, source, reason)) or (True, "party")))
    monkeypatch.setattr(room_client, "set_auto", lambda on: (calls.append(("auto", on)) or (True, "ok")))
    monkeypatch.setattr(room_client, "room_facts", lambda st=None: facts(mode="party", auto=True, north="live"))
    ctx, out, now = {"vars": {}}, [], datetime.now()
    assert H.handle_set_mode({"action": "set_mode", "mode": "Party", "reason": "loaded"}, ctx, now, out, "north-screen") is False
    assert H.handle_set_auto({"action": "set_auto", "on": "False"}, ctx, now, out, "north-screen") is False
    assert H.handle_set_auto({"action": "set_auto", "on": "maybe"}, ctx, now, out, "north-screen") is False
    assert calls == [("mode", "Party", "schedule:north-screen", "loaded"), ("auto", False)]   # 'maybe': not sent
    H.handle_room_state({"action": "room_state"}, ctx, now, out, "north-screen")
    assert ctx["vars"]["room_mode"] == "party" and ctx["vars"]["room_auto"] is True and ctx["vars"]["screen_layer"] == "live"
