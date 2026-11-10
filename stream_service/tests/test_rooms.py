"""Room-level direction (rooms.py): the screens in a room share one director, with the fake engine (no GPU)."""
from __future__ import annotations

import asyncio

from stream_service import rooms, sessions
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
    # "next" on the follower moves the leader, and the follower with it (a second later: not the same request)
    a._room_next_last["done"] -= 2 * sessions.ROOM_NEXT_DEDUPE_S
    before = a.current_name
    r = await cue(client, sb, to="auto")
    assert r.status == 202 and (await r.json())["session"] == sa
    await settle(a, b)
    assert a.current_name != before and b.current_name == a.current_name
    # a manual cue stays on its screen
    await cue(client, sb, to="warp-cells")
    await settle(a, b)
    assert b.current_name == "warp-cells" != a.current_name


async def test_one_next_sent_to_every_screen_of_the_room_moves_it_once(client):
    """audit stream_flask:F7: Alexa's "change the scene" cues every live screen of the room, one after another."""
    (sa, a, ea), (sb, b, eb) = await room_pair(client)
    now = asyncio.get_running_loop().time()
    a.tick(now), b.tick(now)
    n0 = len(scene_cmds(ea))
    r1 = await cue(client, sb, to="auto")                 # the follower first: forwarded to the leader
    r2 = await cue(client, sa, to="auto")                 # then the leader, within the second: the same move
    assert r1.status == r2.status == 202
    j1, j2 = await r1.json(), await r2.json()
    assert j2["cue_id"] == j1["cue_id"] and j2.get("deduped") is True
    await settle(a, b)
    assert len(scene_cmds(ea)) == n0 + 1 and b.current_name == a.current_name
    # both at once (in flight together): still one move
    a._room_next_last["done"] -= 2 * sessions.ROOM_NEXT_DEDUPE_S
    r3, r4 = await asyncio.gather(cue(client, sa, to="auto"), cue(client, sb, to="auto"))
    assert (await r3.json())["cue_id"] == (await r4.json())["cue_id"]
    await settle(a, b)
    assert len(scene_cmds(ea)) == n0 + 2
    # the same screen twice is two presses
    r5 = await cue(client, sa, to="auto")
    await settle(a, b)
    r6 = await cue(client, sa, to="auto")
    assert (await r5.json())["cue_id"] != (await r6.json())["cue_id"]
    await settle(a, b)
    assert len(scene_cmds(ea)) == n0 + 4


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


async def test_a_screen_card_acts_on_its_screen_only(client):
    """Owner 17 Oct: a control acts where it sits.  scope "screen" sets that screen's direction / palette / effects
    for itself (never mirrored); a room-scope change of the same part makes it the room's again everywhere."""
    (sa, a, ea), (sb, b, eb) = await room_pair(client)
    r = await client.patch(f"/api/sessions/{sb}", json={"direction": {"energy": 0.9}, "palette": "ember",
                                                         "scope": "screen"}, headers=AUTH)
    assert r.status == 200, await r.text()
    assert b.direction["energy"] == 0.9 and a.direction["energy"] != 0.9 and b.palette == "ember" != a.palette
    assert {"direction", "palette"} <= b.own and not a.own
    assert b.direction_effective["energy"] == 0.9                         # its own, not the leader's
    a._apply_direction("panel")
    assert b.direction_effective["energy"] == 0.9                         # the leader's moves don't overwrite it
    # effects for itself: it directs its own, the room's come from the leader still
    r = await client.patch(f"/api/sessions/{sb}", json={"auto": {"fx": {"enabled": False}}, "scope": "screen"},
                           headers=AUTH)
    assert r.status == 200 and "fx" in b.own and b.fx_source() is None and a.fx_source() is None
    # the leader sets its own effects: the follower directs the room's effects (nobody else to mirror)
    r = await client.patch(f"/api/sessions/{sa}", json={"auto": {"fx": {"enabled": True}}, "scope": "screen"},
                           headers=AUTH)
    assert "fx" in a.own and b.fx_source() is None
    # a room-scope change makes it the room's again on both
    r = await client.patch(f"/api/sessions/{sa}", json={"direction": {"energy": 0.3}, "auto": {"fx": {"enabled": True}}},
                           headers=AUTH)
    assert r.status == 200 and not a.own and b.own == {"palette"}
    assert a.direction["energy"] == b.direction["energy"] == 0.3 and b.fx_source() is a
    assert (await client.patch(f"/api/sessions/{sa}", json={"palette": None, "scope": "everywhere"},
                               headers=AUTH)).status == 400
    assert "own" in await get_session(client, sb)


async def get_session(client, sid):
    return await (await client.get(f"/api/sessions/{sid}", headers=AUTH)).json()


async def test_a_screen_cards_next_effect_is_that_screens(client):
    (sa, a, ea), (sb, b, eb) = await room_pair(client)
    for s in (a, b):
        s.on_fx({"bus": "open"})
    r = await client.post(f"/api/sessions/{sb}/fx/next", json={"scope": "screen"}, headers=AUTH)
    assert r.status == 200, await r.text()
    assert "fx" in b.own and rooms.auto_fx(b.fx.stack) and not rooms.auto_fx(a.fx.stack)


class _Slot:
    def __init__(self, preset, level, origin="auto"):
        self.spec = type("S", (), {"preset": preset})()
        self.level, self.origin = level, origin


def test_followers_take_the_sources_levels():
    from stream_service import rooms, sessions
    src = [_Slot("posterize", 0.35), _Slot("ocean", 0.6), _Slot("mine", 1.0, origin="manual")]
    lv = rooms.auto_fx_levels(src)
    assert lv == {"posterize": 0.35, "ocean": 0.6}
    assert rooms.fx_level_drift([_Slot("posterize", 1.0), _Slot("ocean", 0.6)], lv)
    assert not rooms.fx_level_drift([_Slot("posterize", 0.35), _Slot("other", 1.0)], lv)


# -- F3: the ensemble segment spans the session's own room ------------------------------------------------------------
def _mgr(*ss):
    mgr = sessions.SessionManager.__new__(sessions.SessionManager)
    mgr._screen_seen, mgr._screen_room = {}, {}
    mgr.sessions = {f"s{i}": s for i, s in enumerate(ss)}
    return mgr


def _sess(screen, room, state="live"):
    from types import SimpleNamespace
    return SimpleNamespace(screen=screen, room=room, state=state, exited=False)


ENS3 = {"screens": {"west": "p.w", "middle": "p.m", "east": "p.e"},
        "players": {"p.w": {"x": -0.5}, "p.m": {"x": 0.0}, "p.e": {"x": 0.5}}}


def _seg(mgr, screen, room, now=100.0):
    from stream_service import ensemble
    return ensemble.segment(ENS3, screen, mgr.room_live_screens(now, room) if room else frozenset({screen}))


def test_segment_two_rooms_of_one_screen_each_are_each_alone():
    mgr = _mgr(_sess("west", "a"), _sess("east", "b"))
    assert _seg(mgr, "west", "a") is None and _seg(mgr, "east", "b") is None       # was (0, 2) / (1, 2)
    assert mgr.room_live_screens(100.0, "a") == {"west"} and mgr.room_live_screens(100.0, None) == frozenset()


def test_segment_counts_the_rooms_own_screens_only():
    mgr = _mgr(_sess("west", "a"), _sess("middle", "b"), _sess("east", "a"))
    assert _seg(mgr, "west", "a") == (0, 2) and _seg(mgr, "east", "a") == (1, 2)    # middle is another room's
    assert _seg(mgr, "middle", "b") is None


def test_segment_finsbury_shape_is_unchanged():
    from stream_service import ensemble
    ens = {"screens": {"south-screen": "screen.south", "north-screen": "screen.north"},
           "players": {"screen.south": {"x": 0.2}, "screen.north": {"x": 0.8}}}
    mgr = _mgr(_sess("north-screen", "drawing-room"), _sess("south-screen", "drawing-room"),
               _sess("devtest", "lab"))
    for scr in ("north-screen", "south-screen"):
        today = ensemble.segment(ens, scr, mgr.live_screens(100.0))
        assert ensemble.segment(ens, scr, mgr.room_live_screens(100.0, "drawing-room")) == today
    assert ensemble.segment(ens, "devtest", mgr.room_live_screens(100.0, "lab")) is None


def test_segment_a_screen_in_no_room_is_alone_and_the_hold_keeps_the_room():
    north = _sess("east", "a")
    mgr = _mgr(_sess("west", None), _sess("middle", "a"), north)
    assert _seg(mgr, "west", None) is None
    assert _seg(mgr, "middle", "a") == (0, 2)
    north.state = "ended"                                   # a brief gap: its slot stays, in its own room
    assert _seg(mgr, "middle", "a", 100.0 + sessions.SEGMENT_HOLD_S - 1) == (0, 2)
    assert _seg(mgr, "middle", "a", 100.0 + sessions.SEGMENT_HOLD_S + 1) is None


async def test_sessions_in_two_rooms_get_no_shared_segment(client):
    from stream_service.server import MANAGER

    from .test_ensemble import ens, part, say
    sa, a, ea = await live(client, screen="north-screen", room="r1", auto=FX_AUTO)
    sb, b, eb = await live(client, screen="south-screen", room="r2", auto=FX_AUTO)
    say(client.app[MANAGER], ens(north=part(x=0.71), south=part(x=-0.71)))
    now = asyncio.get_running_loop().time()
    a.tick(now), b.tick(now)
    assert "segment" not in a.runtime.extra and "segment" not in b.runtime.extra


# -- golden (multi-room plan, step 1): finsbury's screens, their rooms and the show scope as they are today -----------
def test_golden_finsburys_screens_are_all_in_the_show():
    """Until a room sets "show": false every screen is in the show (RoomMap.in_show, when it exists, says True), and
    the rooms are what rooms.json says today. Step 8 (the Lab's own modes) leaves this as it is: the Lab stays in."""
    import smconfig
    m = rooms.RoomMap(smconfig.load("finsbury").path("rooms.json"))
    assert {s: m.room_of(s) for s in ("north-screen", "south-screen", "devtest")} == \
        {"north-screen": "drawing-room", "south-screen": "drawing-room", "devtest": "lab"}
    in_show = getattr(m, "in_show", None)
    for s in ("north-screen", "south-screen", "devtest", "no-such-screen"):
        assert in_show is None or in_show(s) is True, s


# -- step 3: a room with "show": false is outside the show (F4) --------------------------------------------------------
def test_room_map_in_show_is_false_only_for_a_show_false_room_and_reads_live(tmp_path):
    import os
    p = tmp_path / "rooms.json"
    p.write_text('{"front": {"screens": ["fs"]}, "back": {"screens": ["bs"], "show": false}}')
    m = rooms.RoomMap(p)
    assert m.in_show("fs") and not m.in_show("bs") and m.in_show("nowhere")
    assert m.room_of("bs") == "back"
    p.write_text('{"front": {"screens": ["fs"]}, "back": {"screens": ["bs"], "show": true}}')
    os.utime(p, (1e9, 1e9))                                  # a new mtime: re-read
    assert m.in_show("bs")
    p.write_text('{"back": {"screens": ["bs"], "show": "no"}}')
    os.utime(p, (1e9 + 5, 1e9 + 5))
    assert m.in_show("bs")                                   # only the literal false counts


def _outside(tmp_path, mgr, screen="south-screen"):
    p = tmp_path / "rooms.json"
    p.write_text('{"r1": {"screens": ["north-screen"]}, "r2": {"screens": ["%s"], "show": false}}' % screen)
    mgr.rooms = rooms.RoomMap(p)


async def test_a_session_outside_the_show_gets_no_direction_hold_events_or_music(client, tmp_path):
    import time

    from stream_service.server import MANAGER
    mgr = client.app[MANAGER]
    _outside(tmp_path, mgr)
    sa, a, ea = await live(client, screen="north-screen", room="r1", auto=FX_AUTO)
    sb, b, eb = await live(client, screen="south-screen", room="r2", auto=FX_AUTO)
    assert a.show is True and a.runtime.feed is mgr.music_feed
    assert b.show is False and b.runtime.feed is None                # no music feed (frames, cue mappings)
    b._cue_signals()                                                  # silent, never raises
    assert mgr.in_show(a) and not mgr.in_show(b) and mgr.in_show(None) and not mgr.in_show("south-screen")
    mgr.on_room_message({"type": "room.direction", "data": {"hold": True, "vibe": "x"}})
    now = time.monotonic()
    assert mgr.room_direction_now(now) is not None and mgr.room_hold(now)          # no session: as before
    assert mgr.room_hold(now, a) and mgr.room_direction_now(now, a) is not None
    assert not mgr.room_hold(now, b) and mgr.room_direction_now(now, b) is None
    assert b._ens_now(now) is None and b._music_grid(now) is None
    heard = {}
    for s in (a, b):
        s.room_event = lambda kind, *x, _s=s: heard.setdefault(_s.screen, []).append(kind)
    mgr._room_event({"kind": "drop", "at_ms": 1.0, "id": "e1"})
    assert heard == {"north-screen": ["drop"]}


async def test_with_no_show_false_room_every_session_is_in_the_show(client, tmp_path):
    from stream_service.server import MANAGER
    mgr = client.app[MANAGER]
    p = tmp_path / "rooms.json"
    p.write_text('{"r1": {"screens": ["north-screen", "south-screen"]}}')
    mgr.rooms = rooms.RoomMap(p)
    sa, a, ea = await live(client, screen="north-screen", room="r1", auto=FX_AUTO)
    assert a.show is True and a.runtime.feed is mgr.music_feed and mgr.in_show(a)


def test_gesture_slots_skip_sessions_outside_the_show(monkeypatch):
    from stream_service import gestures
    set_calls = []

    async def fake_set(s, want, spec, fade):
        set_calls.append((s.screen, want))

    class S:
        state, exited = "live", False

        def __init__(self, screen):
            self.screen = screen
            self.fx = type("Fx", (), {"stack": []})()

    a, b = S("in"), S("out")
    mgr = type("M", (), {"sessions": {"a": a, "b": b}, "in_show": lambda self, s: s.screen != "out"})()
    monkeypatch.setattr(gestures.BOOK, "wants_slot", lambda screen, now, keep: True)
    monkeypatch.setattr(gestures, "_spec", lambda m: {"x": 1})
    monkeypatch.setattr(gestures, "_set", fake_set)

    async def run():
        gestures.sync_slots(mgr, 1000.0)
        await asyncio.sleep(0)

    asyncio.run(run())
    assert set_calls == [("in", True)]
    gestures._slots.pop(a, None), gestures._slots.pop(b, None)
