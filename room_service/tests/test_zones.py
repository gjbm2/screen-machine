"""Mode zones (room_service/zones.py; deployments/README.md "Rooms with their own modes") on a tmp two-room site:
"front" (fs) shares the site's modes, "back" (bs) has "own_modes": true and its own modes.back.json. Fake
stream_service, Flask and show_service (test_show_adapter.FakeShow); nothing real is reached.

Covers the plan's step 5: bare selectors scoped per zone, each zone's switch leaving the other's targets alone and never
cancelling the other's transition, PUT /screens routed to the owner, conflicts staying with the site, a restart
resuming each zone, sensor_mask.<room> and brightness.<room>.screens, the ORed show claims, Auto's room-less PUT reaching
the site, the remotes API on the raw adapters, the API's room parameter (404 unknown_room), prune_outside and
migrate_from from a state recorded before the room opted in, and no zones at all without own_modes."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
import pytest_asyncio

from room_service.adapters.show import ShowAdapter
from room_service.zones import ScopedAdapter, plan_scopes

from .conftest import Harness, LIVE
from .test_show_adapter import FakeShow, vibes_yaml

SCREENS = {"fs": {"gpu": 0, "live_enabled": True, "mask_push": True},
           "bs": {"gpu": 1, "live_enabled": True, "mask_push": True}}
ROOMS = {"front": {"name": "Front", "screens": ["fs"], "blinds": ["ch02"]},
         "back": {"name": "Back", "screens": ["bs"], "blinds": ["ch02", "ch03"], "own_modes": True}}
LIVE_V = LIVE["live"]
SITE_MODES = {
    "default": "gallery",
    "modes": {
        "gallery": {"label": "Gallery", "state": {"screens": "art", "sensor_mask": "follow", "diag": False}},
        "live": {"label": "Live", "state": {"screens": {"live": LIVE_V}, "sensor_mask": "suspended", "diag": True}},
        "slow": {"label": "Slow", "state": {"diag": True, "show.mic": False}},
        "dim": {"label": "Dim", "state": {"brightness.screens": 60}},
    },
    "transitions": [{"from": "*", "to": "slow", "steps": ["diag", {"delay": 1.0}, "rest"]},
                    {"from": "*", "to": "*", "steps": ["sensor_mask", "screens", "rest"]}],
}
BACK_MODES = {
    "default": "gallery",
    "modes": {
        "gallery": {"label": "Normal", "state": {"screens": "art", "sensor_mask": "follow", "diag": False,
                                                 "brightness.screens": 100, "show.mic": False}},
        "live": {"label": "Live", "state": {"screens": {"live": LIVE_V}, "sensor_mask.main": "suspended",
                                            "diag": True, "brightness.screens": 40, "show.mic": True}},
        "slow": {"label": "Slow", "state": {"diag": True}},
    },
    "transitions": [{"from": "*", "to": "slow", "steps": ["diag", {"delay": 1.0}, "rest"]},
                    {"from": "*", "to": "*", "steps": ["sensor_mask", "screens", "rest"]}],
}


def write_site(tmp: Path, rooms: dict = ROOMS, site: dict = SITE_MODES, back: dict | None = BACK_MODES) -> Path:
    (tmp / "rooms.json").write_text(json.dumps(rooms))
    (tmp / "modes.yaml").write_text(json.dumps(site))       # JSON is YAML
    if back is not None:
        (tmp / "modes.back.json").write_text(json.dumps(back))
    return tmp / "rooms.json"


async def start(tmp: Path, show: FakeShow, zones: list[str] | None = None) -> Harness:
    h = Harness(tmp)
    h.overrides = {"screens": SCREENS, "service": {"rooms_file": str(tmp / "rooms.json")}}
    h.extra_adapters = [ShowAdapter(show.url, vibes_yaml(tmp), zones=["back"] if zones is None else zones,
                                    claims_file=tmp / "state" / "show_claims.json")]
    await h.start()
    return h


@pytest_asyncio.fixture
async def show():
    fake = FakeShow()
    fake.url = await fake.start()
    yield fake
    await fake.server.close()


@pytest_asyncio.fixture
async def zoned(tmp_path, show):
    write_site(tmp_path)
    h = await start(tmp_path, show)
    try:
        await settle(h)
        yield h
    finally:
        await h.stop()


def site(h: Harness):
    return h.svc.zones.site.coordinator


def back(h: Harness):
    return h.svc.zones.zones["back"].coordinator


async def settle(h: Harness, timeout: float = 5.0) -> None:
    """Every zone's boot (or current) switch finished."""
    await h.wait(lambda: all(z.coordinator.s["mode"]["target"] is None and z.coordinator.s["mode"]["current"]
                             for z in h.svc.zones.all()), timeout, "every zone idle")
    for z in h.svc.zones.all():
        await z.coordinator.wait_idle()


async def switch(h: Harness, mode: str, room: str | None = None, timeout: float = 6.0) -> dict:
    body = {"mode": mode, **({"room": room} if room else {})}
    st, out = await h.api("PUT", "/api/v1/mode", body)
    assert st == 202, out
    co = h.svc.zones.for_room(room).coordinator
    await h.wait(lambda: co.s["mode"]["current"] == mode and co.s["mode"]["target"] is None, timeout, f"{room} {mode}")
    return out


# -- the layout -------------------------------------------------------------------------------------------------------

async def test_bare_screens_is_scoped_per_zone(zoned):
    h = zoned
    assert set(h.svc.zones.zones) == {"back"}
    s_cfg, b_cfg = site(h).cfg, back(h).cfg
    assert "screens.fs" in s_cfg.targets and "screens.bs" not in s_cfg.targets
    assert "screens.bs" in b_cfg.targets and "screens.fs" not in b_cfg.targets
    assert set(s_cfg.modes["gallery"].state) >= {"screens.fs", "sensor_mask.main", "diag.fs"}
    assert not any("bs" in t or "back" in t for t in s_cfg.modes["gallery"].state)
    assert set(b_cfg.modes["live"].state) == {"screens.bs", "sensor_mask.back", "diag.bs", "brightness.back.screens",
                                              "show.back.mic"}
    # every zone sees ScopedAdapters; the raw list is the adapters themselves
    assert all(isinstance(a, ScopedAdapter) for a in site(h).adapters.values() if a.subsystem in ("screens", "show"))
    assert not any(isinstance(a, ScopedAdapter) for a in h.svc.adapters)
    st, s = await h.api("GET", "/api/v1/status")
    assert [z["room"] for z in s["zones"]] == ["back"]
    z = s["zones"][0]
    assert z["name"] == "Back" and z["screens"] == ["bs"] and z["mode"]["current"] == "gallery"
    assert z["mode"]["default"] == "gallery" and z["config"]["error"] is None
    assert s["mode"]["current"] == "gallery"                       # the site's, as before
    members = s["subsystems"]["screens"]["members"]
    assert set(members) == {"fs", "bs"} and members["bs"]["room"] == "back" and "room" not in members["fs"]
    assert set(s["subsystems"]["sensor_mask"]["members"]) == {"main", "back"}
    assert set(s["subsystems"]["brightness"]["members"]) == {"screens", "back.screens"}
    assert set(s["subsystems"]["show"]["members"]) == {"main", "mic", "ai", "back.mic", "back.ai"}
    assert [r.get("own_modes") for r in s["rooms"]] == [None, True]


async def test_a_conflicting_target_stays_in_the_site(zoned):
    h = zoned
    plan = h.svc.zones.plan
    assert plan.owner("blinds.ch02") is None and plan.owner("blinds.ch03") == "back"
    assert plan.conflicts == {"blinds.ch02": ["back", "front"]}
    assert "blinds.ch02" in site(h).cfg.targets and "blinds.ch02" not in back(h).cfg.targets
    st, s = await h.api("GET", "/api/v1/status")
    assert "blinds.ch02" in s["mode"]["rooms_error"] and "site" in s["mode"]["rooms_error"]
    assert h.svc.zones.for_target("blinds.ch02").is_site
    st, v = await h.api("POST", "/api/v1/room/config/validate?room=back",
                        {"config": {**BACK_MODES, "modes": {**BACK_MODES["modes"],
                                                            "x": {"state": {"blinds.ch02": 50}}}}})
    assert st == 200 and not v["ok"]
    assert any(e["code"] == "other_room" and "the site's modes" in e["text"] for e in v["errors"]), v["errors"]


async def test_names_the_owner_and_the_aliases(zoned):
    h = zoned
    bad = {**BACK_MODES, "modes": {"gallery": {"state": {"show.main": "party"}}}}
    st, v = await h.api("POST", "/api/v1/room/config/validate", {"config": bad, "room": "back"})
    assert not v["ok"] and "show.main belongs to the site's modes" in v["errors"][0]["message"]
    st, v = await h.api("POST", "/api/v1/room/config/validate",
                        {"config": {**SITE_MODES, "modes": {"gallery": {"state": {"screens.bs": "art"}}}}})
    assert not v["ok"] and "screens.bs belongs to Back's modes" in v["errors"][0]["message"]
    ok = {"default": "gallery", "transitions": [{"from": "*", "to": "*", "steps": ["rest"]}],
          "modes": {"gallery": {"state": {"show.ai": True, "sensor_mask.main": "follow", "brightness.screens": 80,
                                          "page": True}}}}
    st, v = await h.api("POST", "/api/v1/room/config/validate?room=back", {"config": ok})
    assert v["ok"], v["errors"]


# -- switching --------------------------------------------------------------------------------------------------------

async def test_each_zones_live_leaves_the_others_screens_alone(zoned):
    h = zoned
    await h.page("fs")
    await h.page("bs")
    await switch(h, "live")                                        # the site's Live: fs only
    await h.wait(lambda: h.screen("fs").layer == "live", 5, "fs live")
    assert h.screen("bs").desired == "art" and h.screen("bs").layer == "art"
    assert back(h).s["mode"]["current"] == "gallery" and back(h).s["last_transition"]["to"] == "gallery"
    assert h.svc.mask.held("fs") and not h.svc.mask.held("bs")
    await switch(h, "gallery")
    await h.wait(lambda: h.screen("fs").layer == "art", 5, "fs art")
    await switch(h, "live", "back")                                # the back's Live: bs only
    await h.wait(lambda: h.screen("bs").layer == "live", 5, "bs live")
    assert h.screen("fs").desired == "art"
    assert site(h).s["mode"]["current"] == "gallery" and site(h).s["last_transition"]["to"] == "gallery"


async def test_room_mode_events_carry_the_room(zoned):
    h = zoned
    seen: list[dict] = []
    for z in h.svc.zones.all():
        z.coordinator.on_event = lambda e, f=z.coordinator.on_event: (seen.append(e), f(e))
    await switch(h, "slow", "back", timeout=8)
    types = {e["type"] for e in seen if "transition" in e["type"]}
    assert types == {"room_mode.transition"}
    assert all(e["data"]["room"] == "back" for e in seen if e["type"] == "room_mode.transition")


async def test_a_back_switch_does_not_cancel_a_running_front_transition(zoned):
    h = zoned
    st, _ = await h.api("PUT", "/api/v1/mode", {"mode": "slow"})      # the site: diag, a 1 s wait, then the rest
    assert st == 202
    await h.wait(lambda: (site(h).s["transition"] or {}).get("to") == "slow" and site(h)._task is not None, 2, "running")
    await asyncio.sleep(0.2)
    assert not site(h)._task.done()
    out = await switch(h, "slow", "back", timeout=8)               # two transitions at once, in two zones
    assert out["room"] == "back"
    await h.wait(lambda: site(h).s["mode"]["current"] == "slow", 8, "the site's slow")
    tr = site(h).s["last_transition"]
    assert tr["to"] == "slow" and tr["status"] == "ok" and tr["phase"] == "done", tr
    assert not [r for r in tr["steps"] if r["status"] == "cancelled"]
    assert back(h).s["last_transition"]["status"] == "ok"
    assert h.screen("fs").hud is True and h.screen("bs").hud is True


async def test_put_screen_routes_to_the_back(zoned):
    h = zoned
    st, out = await h.api("PUT", "/api/v1/screens/bs", {"desired": "art"})
    assert st == 202, out
    await back(h).wait_idle()
    assert any(e["op"] == "hand_set" and e["target"] == "screens.bs" for e in back(h).s["log"])
    assert not any(e["op"] == "hand_set" for e in site(h).s["log"])
    st, out = await h.api("PUT", "/api/v1/room/targets", {"values": {"diag.bs": True, "diag.fs": True}})
    assert st == 202 and out["accepted"] == ["diag.bs", "diag.fs"]
    await back(h).wait_idle()
    await site(h).wait_idle()
    assert any(e["op"] == "hand_set" and e["target"] == "diag.bs" for e in back(h).s["log"])
    assert any(e["op"] == "hand_set" and e["target"] == "diag.fs" for e in site(h).s["log"])


async def test_auto_without_a_room_reaches_the_site_and_rooms_route(zoned):
    h = zoned
    st, out = await h.api("PUT", "/api/v1/mode", {"mode": "slow", "source": "autopilot", "reason": "quiet"})
    assert st == 202 and "room" not in out
    assert (site(h).s["transition"] or site(h).s["last_transition"])["to"] == "slow"
    assert (site(h).s["transition"] or site(h).s["last_transition"])["source"] == "autopilot"
    assert back(h).s["mode"]["target"] is None
    st, out = await h.api("PUT", "/api/v1/mode", {"mode": "gallery", "room": "front"})   # a room without own modes
    assert st == 202 and out["room"] == "front" and site(h).s["mode"]["target"] == "gallery"
    st, out = await h.api("PUT", "/api/v1/mode", {"mode": "gallery", "room": "nope"})
    assert st == 404 and out["error"] == "unknown_room"
    st, out = await h.api("PUT", "/api/v1/mode", {"mode": "dim", "room": "back"})      # the site's mode, not the back's
    assert st == 404 and out["error"] == "unknown_mode"
    st, m = await h.api("GET", "/api/v1/modes?room=back")
    assert set(m["modes"]) == {"gallery", "live", "slow"} and m["modes"]["gallery"]["label"] == "Normal"
    st, m = await h.api("GET", "/api/v1/modes")
    assert set(m["modes"]) == {"gallery", "live", "slow", "dim"}
    st, m = await h.api("GET", "/api/v1/modes?room=nope")
    assert st == 404 and m["error"] == "unknown_room"
    st, d = await h.api("GET", "/api/v1/room/describe?room=back")
    assert "screens.bs" in d["targets"] and "screens.fs" not in d["targets"]
    st, c = await h.api("GET", "/api/v1/room/config?room=back")
    assert c["path"].endswith("modes.back.json") and c["config"]["modes"]["gallery"]["label"] == "Normal"
    st, p = await h.api("POST", "/api/v1/room/plan", {"mode": "live", "room": "back"})
    assert st == 200 and {r["subsystem"] for r in p["steps"]} >= {"screens.bs"}
    assert not any(r["subsystem"].endswith("fs") for r in p["steps"])
    st, p = await h.api("POST", "/api/v1/room/plan", {"mode": "live", "room": "nope"})
    assert st == 404 and p["error"] == "unknown_room"


async def test_saving_a_zones_modes_file(zoned, tmp_path):
    h = zoned
    st, c = await h.api("GET", "/api/v1/room/config?room=back")
    new = {**c["config"], "modes": {**c["config"]["modes"], "dark": {"label": "Dark", "state": {"diag": False}}}}
    st, out = await h.api("PUT", "/api/v1/room/config?room=back", {"config": new, "base_revision": c["revision"]})
    assert st == 200, out
    assert "dark" in json.loads((tmp_path / "modes.back.json").read_text())["modes"]
    assert "dark" in back(h).cfg.modes and "dark" not in site(h).cfg.modes
    assert list((tmp_path / "state" / "rooms" / "back" / "config-history").glob("*.json"))


# -- per-zone members -------------------------------------------------------------------------------------------------

async def test_sensor_mask_and_brightness_per_zone(zoned, tmp_path):
    h = zoned
    await h.page("bs")
    await switch(h, "live", "back")
    assert h.svc.mask.held("bs") and not h.svc.mask.held("fs") and not h.svc.mask.suspended
    assert h.screen("bs").level == 0.4 and h.screen("fs").level != 0.4
    saved = json.loads((tmp_path / "state" / "sensor_mask.json").read_text())
    assert saved["scopes"] == {"main": False, "back": True}
    st, s = await h.api("GET", "/api/v1/status")
    assert s["subsystems"]["sensor_mask"]["members"]["back"]["believed"] == "suspended"
    assert s["subsystems"]["sensor_mask"]["members"]["main"]["believed"] == "follow"
    assert s["subsystems"]["brightness"]["members"]["back.screens"]["believed"] == 40
    await switch(h, "dim")                                         # the site's brightness.screens: fs only
    assert h.screen("fs").level == 0.6 and h.screen("bs").level == 0.4


async def test_the_show_claims_are_ored(zoned, show):
    h = zoned
    a = h.svc.zones.site.coordinator.adapters["show"].inner
    await switch(h, "live", "back")                                # the back claims the mic
    assert show.mic["mode"] is True and show.mic_puts[-1] == {"mode": True}
    await switch(h, "slow")                                        # the site's show.mic false (already): nothing to send
    assert show.mic["mode"] is True
    for v in (True, False):                                        # the site claims, then lets go: the back's holds
        n = len(show.mic_puts)
        st, _ = await h.api("PUT", "/api/v1/room/targets", {"values": {"show.mic": v}})
        assert st == 202
        await site(h).wait_idle()
        assert len(show.mic_puts) == n + 1 and show.mic_puts[-1] == {"mode": True}
    assert show.mic["mode"] is True
    assert a.wants["mic"] is False and a.wants["back.mic"] is True
    st, s = await h.api("GET", "/api/v1/status")
    mic = s["subsystems"]["show"]["members"]
    await h.wait(lambda: site(h).s["believed"].get("show.mic", {}).get("value") is False, 6, "the site reads its want")
    assert not site(h).s["drift"].get("show.mic")                  # never drift over the back's claim
    assert mic["back.mic"]["room"] == "back"
    await switch(h, "gallery", "back")                             # both off: off
    assert show.mic_puts[-1] == {"mode": False} and show.mic["mode"] is False
    # show_service restarted cold while the back claims: a read puts the OR back
    await switch(h, "live", "back")
    show.mic["mode"], show.boot = False, "boot-2"
    await a.read(["back.mic"])
    await h.wait(lambda: show.mic["mode"] is True, 3, "the OR put back")
    assert json.loads((h.tmp / "state" / "show_claims.json").read_text())["wants"]["back.mic"] is True


async def test_a_restart_resumes_each_zone(zoned):
    h = zoned
    st, _ = await h.api("PUT", "/api/v1/mode", {"mode": "slow"})
    assert st == 202
    st, _ = await h.api("PUT", "/api/v1/mode", {"mode": "slow", "room": "back"})
    assert st == 202
    await asyncio.sleep(0.3)                                       # both inside their 1 s wait
    assert site(h).s["mode"]["target"] == "slow" and back(h).s["mode"]["target"] == "slow"
    await h.restart_room()
    assert (h.tmp / "state" / "rooms" / "back" / "state.json").exists()
    await h.wait(lambda: site(h).s["mode"]["current"] == "slow" and back(h).s["mode"]["current"] == "slow", 8,
                 "both resumed")
    assert site(h).s["last_transition"]["resumes"] and site(h).s["last_transition"]["status"] == "ok"
    assert back(h).s["last_transition"]["resumes"] and back(h).s["last_transition"]["status"] == "ok"


# -- the remotes API keeps the raw adapters ---------------------------------------------------------------------------

async def test_the_remotes_api_still_works(tmp_path, show, monkeypatch):
    pytest.importorskip("broadlink")
    from room_service.adapters import broadlink_rf
    d = tmp_path / "rf"
    shutil.copytree(broadlink_rf.TYPES_DIR, d / "types")
    monkeypatch.setattr(broadlink_rf, "RF_DIR", d)
    monkeypatch.setattr(broadlink_rf, "TYPES_DIR", d / "types")
    write_site(tmp_path)
    h = await start(tmp_path, show)
    try:
        await settle(h)
        st, dev = await h.api("POST", "/api/v1/remotes", {"model": "pinspot", "label": "Pin", "room": "back",
                                                           "host": "192.0.2.1"})
        assert st == 201 and dev["id"] == "pin" and dev["room"] == "back" and dev["light"]
        st, v = await h.api("GET", "/api/v1/remotes")
        assert [r["id"] for r in v["remotes"]] == ["pin"]
        assert h.svc.zones.for_target("pin.main").room == "back"
        st, dz = await h.api("GET", "/api/v1/room/describe?room=back")
        st, ds = await h.api("GET", "/api/v1/room/describe")
        assert "pin.colour" in dz["targets"] and "pin.colour" not in ds["targets"]
        st, v = await h.api("PUT", "/api/v1/remotes/pin/state", {"values": {"pin.main": "on", "pin.level": 7}})
        assert st == 200 and {r["target"]: r["value"] for r in v["state_rows"]}["pin.level"] == 7
        st, out = await h.api("DELETE", "/api/v1/remotes/pin")
        assert st == 200 and "pin.main" not in back(h).cfg.targets
    finally:
        await h.stop()


# -- stale state at opt-in: prune_outside and migrate_from --------------------------------------------------------------

async def test_opting_in_prunes_the_site_and_moves_a_running_mode(tmp_path, show):
    """Recorded before the opt-in: back is an ordinary room and the site is in a mode that sets the back's screen, its
    blind and diag (what finsbury's Live test did to devtest), and claims the mic and the AI listening with restore.
    Then back gets own_modes with that mode: the back starts in it (nothing sent), the site is relabelled to its
    default, the site's entries for the back's targets are gone, and the claims are the back's (show.back.mic and
    .ai, with the site's put-back values): so the back's Normal releases them, as the site's gallery claims neither."""
    claims = {"show.mic": {"set": True, "restore": True}, "show.ai": {"set": True, "restore": True}}
    shared = {**SITE_MODES, "modes": {**SITE_MODES["modes"],
                                      "live-test": {"label": "Live test", "state": {"diag.bs": True, "blinds.ch03": 30,
                                                                                    "diag.fs": False, **claims}}}}
    rooms = {k: {kk: vv for kk, vv in v.items() if kk != "own_modes"} for k, v in ROOMS.items()}
    write_site(tmp_path, rooms=rooms, site=shared, back=None)
    h = await start(tmp_path, show, zones=[])
    try:
        await settle(h)
        assert h.svc.zones.single
        await switch(h, "live-test")
        recorded = json.loads((tmp_path / "state" / "state.json").read_text())
        assert {"diag.bs", "blinds.ch03", "show.mic", "show.ai"} <= set(recorded["desired"])
        assert recorded["stored"]["_restore"]["show.mic"]["value"] is False
        assert show.mic["mode"] is True and show.listening["mode"] is True
        await h.stop_room()
        write_site(tmp_path, site=SITE_MODES, back={**BACK_MODES, "modes": {
            **BACK_MODES["modes"], "live-test": {"label": "Live test", "state": {"diag": True, "blinds.ch03": 30,
                                                                                 **claims}}}})
        h.extra_adapters = [ShowAdapter(show.url, vibes_yaml(tmp_path), zones=["back"],
                                        claims_file=tmp_path / "state" / "show_claims.json")]
        await h.start_room()
        assert not h.svc.zones.single
        assert back(h).s["mode"]["current"] == "live-test" and back(h).s["mode"]["target"] is None
        assert site(h).s["mode"]["current"] == "gallery"
        assert not {"diag.bs", "blinds.ch03", "screens.bs"} & set(site(h).s["desired"])
        assert not {"diag.bs", "blinds.ch03"} & set(site(h).s["believed"])
        assert any(e["op"] == "pruned" and e["target"] == "diag.bs" and e["owner"] == "Back" for e in site(h).s["log"])
        assert any(e["op"] == "migrated" for e in back(h).s["log"])
        assert back(h).s["transition"] is None and back(h).s["last_transition"] is None   # nothing sent to the back
        assert h.screen("bs").hud is True                          # as live-test left it
        # the claims are the back's now: its own desired and put-back, the site holding neither
        assert not {"show.mic", "show.ai"} & set(site(h).s["desired"]) and "_restore" not in site(h).s["stored"]
        assert back(h).s["desired"]["show.back.mic"]["value"] is True
        assert back(h).s["stored"]["_restore"]["show.back.ai"]["value"] is False
        a = site(h).adapters["show"].inner
        assert a.wants == {"mic": False, "ai": False, "back.mic": True, "back.ai": True}
        assert show.mic["mode"] is True and show.listening["mode"] is True           # nothing sent: still on
        await switch(h, "gallery", "back")                         # the Lab's Normal ends it
        assert h.screen("bs").hud is False
        await h.wait(lambda: show.mic["mode"] is False and show.listening["mode"] is False, 3, "claims released")
        assert a.wants == {"mic": False, "ai": False, "back.mic": False, "back.ai": False}
        await settle(h)
        assert not site(h).s["drift"] and not back(h).s["drift"]
    finally:
        await h.stop()


# -- a room outside the show, and no zones at all ----------------------------------------------------------------------

async def test_a_zone_outside_the_show_has_no_show_claims(tmp_path, show):
    rooms = {**ROOMS, "back": {**ROOMS["back"], "show": False}}
    back_modes = {**BACK_MODES, "modes": {k: {**m, "state": {t: v for t, v in m["state"].items() if t != "show.mic"}}
                                          for k, m in BACK_MODES["modes"].items()}}
    write_site(tmp_path, rooms=rooms, back=back_modes)
    h = await start(tmp_path, show, zones=[])
    try:
        await settle(h)
        st, s = await h.api("GET", "/api/v1/status")
        assert s["zones"][0]["show"] is False
        assert set(s["subsystems"]["show"]["members"]) == {"main", "mic", "ai"}
        bad = {**back_modes, "modes": {"gallery": {"state": {"show.mic": True}}}}
        st, v = await h.api("POST", "/api/v1/room/config/validate?room=back", {"config": bad})
        assert not v["ok"] and "show.mic belongs to the site's modes" in v["errors"][0]["message"]
    finally:
        await h.stop()


async def test_no_own_modes_builds_todays_objects(harness):
    """finsbury's shape (no room has own_modes): one site zone, the raw adapters, no 'zones' key, no zone members."""
    h = harness
    assert h.svc.zones.single and h.svc.zones.plan is None
    assert h.svc.coordinator is h.svc.zones.site.coordinator
    assert not any(isinstance(a, ScopedAdapter) for a in h.svc.coordinator.adapters.values())
    st, s = await h.api("GET", "/api/v1/status")
    assert "zones" not in s and s["mode"]["rooms_error"] is None
    assert list(s["subsystems"]["sensor_mask"]["members"]) == ["main"]
    st, out = await h.api("PUT", "/api/v1/mode", {"mode": "gallery", "room": "drawing-room"})
    assert st == 202 and out["room"] == "drawing-room"
    st, out = await h.api("PUT", "/api/v1/mode", {"mode": "gallery", "room": "nope"})
    assert st == 404 and out["error"] == "unknown_room"
    st, out = await h.api("GET", "/api/v1/room/describe?room=nope")
    assert st == 404 and out["error"] == "unknown_room"


async def test_a_rooms_json_layout_change_says_restart(zoned, tmp_path):
    h = zoned
    rooms = {**ROOMS, "front": {**ROOMS["front"], "own_modes": True}}
    (tmp_path / "rooms.json").write_text(json.dumps(rooms))
    await h.wait(lambda: "restart room_service" in (h.svc.room_config.rooms_error or ""), 6, "rooms_error")
    assert set(h.svc.zones.zones) == {"back"}                      # not rebuilt while running


def test_plan_scopes_unit():
    from room_service.roomstate.contract import Placeholder
    ads = [Placeholder("screens", ["fs", "bs"]), Placeholder("light", ["a", "b"]),
           Placeholder("sensor_mask", ["main", "back"]), Placeholder("show", ["main", "mic", "back.mic"]),
           Placeholder("autopilot", ["main"])]
    rooms = {"front": {"screens": ["fs"], "lights": ["light.a"]},
             "back": {"screens": ["bs"], "lights": ["light"], "own_modes": True}}
    scopes, conflicts = plan_scopes(rooms, ads, ["back"])
    assert scopes["back"] == {"screens.bs", "sensor_mask.back", "show.back.mic"}
    assert conflicts == {"light.b": ["back"]}                      # the whole "light": stays with the site


async def test_a_zone_keeps_its_entries_for_a_target_no_adapter_has_yet(tmp_path, show):
    """A light the back lists that show_service hasn't registered (no rig cache either) has no known owner at start:
    the back's desired value and put-back for it are kept, not pruned as 'moved to the site'."""
    rooms = {**ROOMS, "back": {**ROOMS["back"], "lights": ["light.zz"]}}
    write_site(tmp_path, rooms=rooms)
    h = await start(tmp_path, show)
    try:
        await settle(h)
        co = back(h)
        assert "light.zz" not in co.cfg.targets
        co.s["desired"]["light.zz"] = {"value": 40, "policy": "respect", "slot": None, "settled": True,
                                       "retry": None, "reasserted": False}
        co.s["stored"].setdefault("_restore", {})["light.zz"] = {"value": 10, "at": 1.0, "confidence": "confirmed",
                                                                 "from_mode": "gallery", "stored_at": 1.0,
                                                                 "closed": False}
        co._save()
        await h.restart_room()
        await settle(h)
        assert back(h).s["desired"]["light.zz"]["value"] == 40
        assert back(h).s["stored"]["_restore"]["light.zz"]["value"] == 10
        assert not any(e["op"] == "pruned" for e in back(h).s["log"])
    finally:
        await h.stop()


def test_moved_from_names_only_known_targets_another_zone_owns():
    from room_service.roomstate.contract import Placeholder
    from room_service.zones import ZonePlan
    ads = [Placeholder("screens", ["fs", "bs"]), Placeholder("diag", ["fs", "bs"])]
    rooms = {"front": {"screens": ["fs"]}, "back": {"name": "Back", "screens": ["bs"], "own_modes": True}}
    plan = ZonePlan({"back": {"name": "Back", "screens": ["bs"], "show": True}}, ads)
    plan.replan(rooms)
    back_, site_ = plan.moved_from("back"), plan.moved_from(None)
    assert back_("screens.fs") == "the site" and back_("screens.bs") is None
    assert site_("screens.bs") == "Back" and site_("screens.fs") is None
    assert back_("light.zz") is None and site_("light.zz") is None          # no adapter has it: kept by both


async def test_single_mode_claims_round_trip_on_show_service(tmp_path, show):
    """No own_modes (every shipped deployment): a mode's show.mic and show.ai claims with restore are one PUT
    {"mode": true} each on show_service's /api/v1/mic and /api/v1/listening, and leaving puts {"mode": false} back;
    no claims file is written and the adapter keeps no wants (the golden probe's show_service is unreachable, so this
    is the round trip it can't see)."""
    shared = {**SITE_MODES, "modes": {**SITE_MODES["modes"], "listen": {"label": "Listen", "state": {
        "show.mic": {"set": True, "restore": True}, "show.ai": {"set": True, "restore": True}}}}}
    rooms = {k: {kk: vv for kk, vv in v.items() if kk != "own_modes"} for k, v in ROOMS.items()}
    write_site(tmp_path, rooms=rooms, site=shared, back=None)
    h = await start(tmp_path, show, zones=[])
    try:
        await settle(h)
        assert h.svc.zones.single and set(site(h).adapters["show"].members) == {"main", "mic", "ai"}
        show.mic_puts.clear()
        show.ai_puts.clear()
        await switch(h, "listen")
        assert show.mic_puts == [{"mode": True}] and show.ai_puts == [{"mode": True}]
        assert show.mic["mode"] is True and show.listening["mode"] is True
        await switch(h, "gallery")
        await h.wait(lambda: show.mic["mode"] is False and show.listening["mode"] is False, 3, "claims put back")
        assert show.mic_puts == [{"mode": True}, {"mode": False}] and show.ai_puts == [{"mode": True}, {"mode": False}]
        assert site(h).adapters["show"].wants == {} and not (tmp_path / "state" / "show_claims.json").exists()
        assert "_restore" not in site(h).s["stored"]
    finally:
        await h.stop()
