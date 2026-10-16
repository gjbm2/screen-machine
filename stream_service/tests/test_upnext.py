"""Up next (upnext.py): the director's forward plan of scenes (with their transitions) and effects, which the owner
sees and edits; Next plays the head; a direction change re-plans the director's own entries (fake engine, no GPU)."""
from __future__ import annotations

import time

from stream_service.upnext import EFFECTS_AHEAD, SCENES_AHEAD, UpNext, why_words

from .test_cue import AUTO, cue, live
from .test_fx_api import client as fx_client  # noqa: F401 (fixture)
from .test_sessions import AUTH, client  # noqa: F401 (fixture)


async def plan(client, sid):
    r = await client.get(f"/api/sessions/{sid}/upnext", headers=AUTH)
    assert r.status == 200
    return await r.json()


async def op(client, sid, **body):
    return await client.post(f"/api/sessions/{sid}/upnext", json=body, headers=AUTH)


def test_queue_ops_and_drop_auto_keeps_the_owners():
    q = UpNext()
    a = q.add("scene", {"scene": "a", "transition": "t1", "origin": "auto", "transition_origin": "auto"})
    b = q.add("scene", {"scene": "b", "transition": "t2", "origin": "you", "transition_origin": "auto"})
    c = q.add("scene", {"scene": "c", "transition": "t3", "origin": "you", "transition_origin": "you"}, at=0)
    assert [e["scene"] for e in q.scenes] == ["c", "a", "b"] and q.find(b["id"]) == ("scene", 2)
    assert q.move(b["id"], 0) and [e["scene"] for e in q.scenes] == ["b", "c", "a"]
    rev = q.rev
    q.drop_auto("scene")
    assert [e["scene"] for e in q.scenes] == ["b", "c"] and q.rev > rev
    assert q.scenes[0]["transition"] is None and q.scenes[1]["transition"] == "t3"   # only auto transitions go
    assert q.remove(a["id"]) is None and q.remove(c["id"])["scene"] == "c"
    v = q.view((5.0, 10.0), (6.0, 12.0), None, 30.0)
    assert v["scenes"][0]["eta_s"] == [5.0, 10.0] and v["effects"] == []


async def test_director_plans_ahead_and_next_plays_the_head(client):
    sid, s, eng = await live(client, auto=AUTO)
    s._upnext_fill(time.monotonic())
    p = await plan(client, sid)
    scenes = p["scenes"]
    assert len(scenes) == SCENES_AHEAD and all(e["origin"] == "auto" and e["transition"] for e in scenes)
    assert all(a["scene"] != b["scene"] for a, b in zip(scenes, scenes[1:]))       # never the same twice running
    assert scenes[0]["scene"] != s.current_name and p["planned_by"] == "devtest"
    head = scenes[0]
    r = await cue(client, sid, to="auto", at="now")                                # Next: the head plays
    assert r.status == 202, await r.text()
    assert s.current_name == head["scene"] and s.last_cue["transition"] == head["transition"]
    p = await plan(client, sid)
    assert head["id"] not in [e["id"] for e in p["scenes"]] and len(p["scenes"]) == SCENES_AHEAD
    assert p["scenes"][0]["id"] == scenes[1]["id"]


async def test_owner_edits_are_kept_and_played(client):
    sid, s, eng = await live(client, auto=AUTO)
    s._upnext_fill(time.monotonic())
    pick = next(n for n in s.mgr.presets.scenes if n != s.current_name)
    r = await op(client, sid, op="add", scene=pick)
    assert r.status == 200, await r.text()
    res = await r.json()
    mine = res["upnext"]["scenes"][0]
    assert mine["id"] == res["id"] and mine["scene"] == pick and mine["origin"] == "you"
    assert mine["transition"] and mine["transition_origin"] == "auto"              # the director picks the way in
    # re-planning (all the director's entries) keeps the owner's
    r = await op(client, sid, op="reroll", kind="scene")
    p = (await r.json())["upnext"]
    assert p["scenes"][0]["id"] == mine["id"] and len(p["scenes"]) == SCENES_AHEAD
    # set a transition by hand, move it down, keep a director's pick, remove one
    tname = next(iter(s.mgr.presets.transitions))
    assert (await op(client, sid, op="set", id=mine["id"], transition=tname)).status == 200
    assert (await op(client, sid, op="move", id=mine["id"], to=2)).status == 200
    p = await plan(client, sid)
    assert p["scenes"][2]["id"] == mine["id"] and p["scenes"][2]["transition"] == tname
    keep = p["scenes"][0]
    assert (await op(client, sid, op="keep", id=keep["id"])).status == 200
    assert (await plan(client, sid))["scenes"][0]["origin"] == "you"
    gone = p["scenes"][1]["id"]
    assert (await op(client, sid, op="remove", id=gone)).status == 200
    assert gone not in [e["id"] for e in (await plan(client, sid))["scenes"]]
    # play one now
    r = await op(client, sid, op="play", id=mine["id"])
    assert r.status == 200, await r.text()
    assert s.current_name == pick and s.last_cue["transition"] == tname
    # bad requests
    for b, code in (({"op": "add", "scene": "no-such-scene"}, 400), ({"op": "remove"}, 400),
                    ({"op": "remove", "id": "nope"}, 400), ({"op": "fly"}, 400),
                    ({"op": "add", "scene": pick, "transition": "no-such"}, 400)):
        assert (await op(client, sid, **b)).status == code, b


async def test_a_direction_change_replans_the_directors_entries(client):
    sid, s, eng = await live(client, auto=AUTO)
    s._upnext_fill(time.monotonic())
    pick = next(n for n in s.mgr.presets.scenes if n != s.current_name)
    mine = (await (await op(client, sid, op="add", scene=pick, at=1)).json())["id"]
    before = [e["id"] for e in (await plan(client, sid))["scenes"]]
    r = await client.patch(f"/api/sessions/{sid}", json={"direction": {"energy": 0.95, "moods": ["euphoric"]}},
                           headers=AUTH)
    assert r.status == 200, await r.text()
    s._upnext_fill(time.monotonic())
    after = [e["id"] for e in (await plan(client, sid))["scenes"]]
    assert mine in after and len(after) == SCENES_AHEAD
    assert not (set(before) - {mine}) & set(after)                                 # the director's were re-picked


async def test_effects_queue_feeds_next_effect(fx_client):  # noqa: F811
    client = fx_client
    sid, s, eng = await live(client, auto={**AUTO, "fx": {"enabled": True, "max": 2}})
    s.on_fx({"bus": "open"})
    s._upnext_fill(time.monotonic())
    p = await plan(client, sid)
    assert 1 <= len(p["effects"]) <= EFFECTS_AHEAD and all(e["origin"] == "auto" for e in p["effects"])
    head = p["effects"][0]
    r = await client.post(f"/api/sessions/{sid}/fx/next", headers=AUTH)
    res = await r.json()
    assert r.status == 200 and res["preset"] == head["effect"] and "up next" in res["why"]
    assert head["id"] not in [e["id"] for e in (await plan(client, sid))["effects"]]
    # the owner's effect, played now
    eff = next(n for n in s.mgr.presets.effects if n != head["effect"])
    mine = (await (await op(client, sid, op="add", kind="effect", effect=eff)).json())["id"]
    r = await op(client, sid, op="play", id=mine)
    assert r.status == 200, await r.text()
    assert (await r.json())["fx"]["preset"] == eff


def test_why_in_plain_words():
    d = {"moods": ["calm", "dreamy"], "energy": 0.2, "genre": "jazz", "fx": {"moods": ["warm"]}}
    assert why_words("scene", {"moods": ["dreamy", "calm"], "energy": [0, 0.4], "genre": ["jazz"]}, d, 4.0) == \
        "Fits dreamy, calm · fits jazz · suits the calm · you rated it 4★"
    assert why_words("scene", {}, d) == "For variety"
    assert why_words("effect", {"moods": ["warm"]}, d, 1.0, "light") == "A light effect · fits warm"
    assert why_words("effect", {}, d, 1.0, "light") == "A light effect · for variety"
