"""/api/v1/room/* (room_api.py) through the real service: auth split, and each call answers sensibly."""

from __future__ import annotations

from .conftest import Harness


async def test_room_routes_open_and_admin(harness: Harness):
    h = harness
    st, d = await h.api("GET", "/api/v1/room/describe", token=None)
    assert st == 200 and "screens.devtest" in d["targets"] and d["modes"]["gallery"]["label"] == "Gallery"
    st, p = await h.api("POST", "/api/v1/room/plan", {"mode": "live-test"}, token=None)
    assert st == 200 and any(r["subsystem"] == "screens.devtest" for r in p["steps"])
    st, _ = await h.api("POST", "/api/v1/room/plan", {"mode": "nope"}, token=None)
    assert st == 404
    # Owner ruling, 14 Oct: no auth anywhere in the AV stack (LAN test rig), so the editor calls are open too.
    st, c = await h.api("GET", "/api/v1/room/config", token=None)
    assert st == 200 and c["revision"]
    st, v = await h.api("POST", "/api/v1/room/config/validate", {"config": {}}, token=None)
    assert st == 200 and not v["ok"]


async def test_config_validate_save_and_conflict(harness: Harness):
    h = harness
    st, c = await h.api("GET", "/api/v1/room/config")
    assert st == 200 and c["error"] is None
    cfg, rev = c["config"], c["revision"]
    bad = {**cfg, "modes": {**cfg["modes"], "gallery": {"state": {"blinds.ch99": 1}}}}
    st, v = await h.api("POST", "/api/v1/room/config/validate", {"config": bad})
    assert st == 200 and not v["ok"] and v["errors"][0]["path"].startswith("/modes/gallery")
    new = {**cfg, "modes": {**cfg["modes"], "gallery": {**cfg["modes"]["gallery"], "label": "Normal"}}}
    st, _ = await h.api("PUT", "/api/v1/room/config", {"config": new})
    assert st == 428
    st, out = await h.api("PUT", "/api/v1/room/config", {"config": new, "base_revision": rev})
    assert st == 200 and out["revision"] != rev
    st, _ = await h.api("PUT", "/api/v1/room/config", {"config": new, "base_revision": rev})   # stale
    assert st == 409
    st, m = await h.api("GET", "/api/v1/modes")
    assert m["modes"]["gallery"]["label"] == "Normal"


async def test_targets_by_hand_and_refusals(harness: Harness):
    h = harness
    st, body = await h.api("PUT", "/api/v1/room/targets", {"values": {"mirror_ball.main": "spinning"}}, token=None)
    assert st == 409 and body["error"] == "unavailable"                       # a placeholder: nothing to send
    st, body = await h.api("PUT", "/api/v1/room/targets", {"values": {"blinds.ch99": 1}}, token=None)
    assert st == 404
    st, body = await h.api("PUT", "/api/v1/room/targets", {"values": {"blinds.ch05": 40}}, token=None)
    assert st == 202 and body["accepted"] == ["blinds.ch05"]
    st, body = await h.api("PUT", "/api/v1/room/targets", {"values": {"blinds.ch05": 40}, "hold": True}, token=None)
    assert st in (202, 409)                                                   # open (no auth); 409 while ch05 moves
    st, body = await h.api("DELETE", "/api/v1/room/stored/pre_live")
    assert st in (200, 409)                                                   # 409 device_busy while ch05 still moves
    await h.wait(lambda: not any(not t.done() for t in h.svc.coordinator._rtasks), 3, "hand set finished")
    st, body = await h.api("DELETE", "/api/v1/room/stored/pre_live")
    assert st == 200


async def test_put_mode_autopilot_fields(harness: Harness):
    h = harness
    st, body = await h.api("PUT", "/api/v1/mode", {"mode": "live-test", "skip": "nope"})
    assert st == 400
    st, body = await h.api("PUT", "/api/v1/mode", {"mode": "live-test", "skip": ["blinds.ch99"]})
    assert st == 400 and body["error"] == "bad_skip"
    st, body = await h.api("PUT", "/api/v1/mode", {"mode": "live-test", "skip": [{"sensor_mask": "follow"}]})
    assert st == 400 and body["error"] == "bad_request"                     # a condition is {"from": value}
    st, body = await h.api("PUT", "/api/v1/mode", {"mode": "gallery", "skip": [{"sensor_mask": {"from": "follow"}}]})
    assert st == 202
    st, body = await h.api("PUT", "/api/v1/mode", {"mode": "live-test", "expect": {"current": "nope"}})
    assert st == 409 and body["error"] == "stale"
    st, body = await h.api("PUT", "/api/v1/mode", {"mode": "live-test", "source": "autopilot", "reason": "test",
                                                   "expect": {"current": "gallery"}})
    assert st == 202
