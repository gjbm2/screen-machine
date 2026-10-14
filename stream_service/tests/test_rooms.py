"""Room-level direction (rooms.py): the screens in a room share one director, with the fake engine (no GPU)."""
from __future__ import annotations

import asyncio

from stream_service import rooms
from stream_service.director import DIRECTION_DEFAULTS, FX_DIRECTION_DEFAULTS
from stream_service.renderer import StartTransition

from .test_cue import AUTO, cue, live
from .test_fx_api import client, get  # noqa: F401 (fixture)
from .test_sessions import AUTH

FX_AUTO = {**AUTO, "fx": {"enabled": True, "max": 2}}


async def settle(*ss):
    """Let the room's tasks run, and finish every scene transition (the fake engine never does)."""
    for _ in range(200):
        await asyncio.sleep(0.01)
        for s in ss:
            for tid in list(s.inflight):
                s.on_transition("scene", "done", 9.0, tid, False)
        if all(s._fx_task is None and s._follow_prep is None and not s.inflight and s.pending is None
               for s in ss) and not [t for t in asyncio.all_tasks() if "_follow_cue" in repr(t.get_coro())]:
            return


def scene_cmds(eng):
    return [c for c in eng.cmds if isinstance(c, StartTransition) and c.kind == "scene"]


async def room_pair(client, **kw):
    a = await live(client, screen="north-screen", room="r1", auto=FX_AUTO, **kw)
    b = await live(client, screen="south-screen", room="r1", auto=FX_AUTO, **kw)
    return a, b


async def test_followers_cut_with_the_leader_and_next_moves_the_room(client):
    (sa, a, ea), (sb, b, eb) = await room_pair(client)
    now = asyncio.get_running_loop().time()
    a.tick(now), b.tick(now)
    assert a.room_status()["role"] == "leader" and b.room_status() == {
        "id": "r1", "role": "follower", "leader": "north-screen", "screens": ["north-screen", "south-screen"]}
    r = await cue(client, sa, to="auto")
    assert r.status == 202
    await settle(a, b)
    assert b.current_name == a.current_name != "fractal-zoom"
    assert b.last_cue["reason"] == "room" and b.last_cue["cause"] == "auto" and b.scene_cause == "auto"
    # "next" on the follower moves the leader, and the follower with it
    before = a.current_name
    r = await cue(client, sb, to="auto")
    assert r.status == 202 and (await r.json())["session"] == sa
    await settle(a, b)
    assert a.current_name != before and b.current_name == a.current_name
    # a manual cue stays on its screen
    await cue(client, sb, to="warp-cells")
    await settle(a, b)
    assert b.current_name == "warp-cells" != a.current_name


async def test_prepared_pick_is_prepared_on_the_followers_too(client):
    (sa, a, ea), (sb, b, eb) = await room_pair(client)
    await a._prepare_next()
    await settle(a, b)
    assert a.prepared_pick is not None and b.follow_pick is not None
    assert b.follow_pick["want"][0] == a.prepared_pick["scene"]
    src = b.follow_pick["src"]
    await cue(client, sa, to="auto")
    await settle(a, b)
    assert scene_cmds(eb)[-1].source is src and b.follow_pick is None       # the copy prepared ahead was used


async def test_look_patch_applies_to_the_room_and_direction_follows_the_leader(client):
    (sa, a, ea), (sb, b, eb) = await room_pair(client)
    r = await client.patch(f"/api/sessions/{sb}", json={"direction": {"energy": 0.9}, "palette": "ember"},
                           headers=AUTH)
    assert r.status == 200
    assert a.direction["energy"] == b.direction["energy"] == 0.9 and a.palette == b.palette == "ember"
    assert b.direction_effective == a.direction_effective
    a.music.proposal = {**DIRECTION_DEFAULTS, "energy": 0.2, "moods": ["calm"], "transition_style": "gentle",
                        "fx": {**FX_DIRECTION_DEFAULTS, "moods": ["calm"]}}
    a.music.m = 1.0
    a._apply_direction("music")
    assert b.direction_effective is a.direction_effective                     # the leader's, music and all


async def test_fx_mirror_the_leader(client):
    (sa, a, ea), (sb, b, eb) = await room_pair(client, direction={"fx": {"density": 1.0, "rate": 1.0}})
    for s in (a, b):
        s.on_fx({"bus": "open"})
    now = asyncio.get_running_loop().time()
    a.tick(now), b.tick(now)
    a.fx_director.next_at = b.fx_director.next_at = 0.0
    a.tick(now), b.tick(now)                                                  # only the leader plans
    await settle(a, b)
    want = [p for _, p in rooms.auto_fx(a.fx.stack)]
    assert want and [p for _, p in rooms.auto_fx(b.fx.stack)] == want
    nb, na = (await get(client, sb))["auto"]["next_in_s"], (await get(client, sa))["auto"]["next_in_s"]
    assert nb is not None and abs(nb - na) <= 0.3                               # the follower shows the leader's timer


async def test_membership_leadership_and_joining_takes_the_rooms_look(client):
    (sa, a, ea), (sb, b, eb) = await room_pair(client, direction={"energy": 0.7})
    await client.patch(f"/api/sessions/{sa}", json={"palette": "ember"}, headers=AUTH)
    # a session without an auto block (a Library push) is not part of the room
    sc, c, ec = await live(client, screen="devtest", room="r1")
    assert c.room_status()["role"] == "solo" and [s.screen for s in rooms.members(a.mgr.sessions.values(), "r1")] \
        == ["north-screen", "south-screen"]
    await client.delete(f"/api/sessions/{sc}", headers=AUTH)
    # the leader leaves: the follower directs
    await client.post(f"/api/sessions/{sa}/exit", json={"target": {"rgb": [0, 0, 0]}}, headers=AUTH)
    now = asyncio.get_running_loop().time()
    b.tick(now)
    assert b.room_status()["role"] == "leader" and not b._following
    # a new session for the north screen joins with the room's look, whatever its body says
    sn, n, en = await live(client, screen="north-screen", room="r1", auto={"enabled": False},
                           direction={"energy": 0.1})
    assert n.palette == "ember" and n.direction["energy"] == 0.7 and n.director.auto["enabled"] is True
    assert n.room_status()["role"] == "follower" and n.room_status()["leader"] == "south-screen"


def test_room_map_and_fx_step(tmp_path):
    p = tmp_path / "rooms.json"
    p.write_text('{"_comment": "x", "drawing-room": {"screens": ["north-screen", "south-screen"]}, '
                 '"lab": {"screens": ["devtest"]}}')
    m = rooms.RoomMap(p)
    assert m.room_of("south-screen") == "drawing-room" and m.room_of("devtest") == "lab" and m.room_of("x") is None
    assert rooms.fx_step([("a", "grade"), ("b", "chroma")], ["grade", "shake"]) == ("shake", "b")
    assert rooms.fx_step([("a", "grade")], ["grade"]) == (None, None)
    assert rooms.fx_step([], ["grade", "grade"]) == ("grade", None)
