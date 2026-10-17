"""Song ends (c8's room.events, SHOW_ENGINE §8.5): fade-dark across a gap (out to black, capped, in on the new
song), a segue's cut or crossfade on the new song's first beat, big / small change, held = left alone, the
track junction's section_change skipped, ids dedupe (fake engine, no GPU)."""
from __future__ import annotations

import asyncio
import time

from .test_cue import AUTO, live
from .test_sessions import client  # noqa: F401 (fixture)


def ev(mgr, **data):
    mgr.on_room_message({"type": "room.event", "data": data})


async def settle(s, n=20):
    for _ in range(n):
        await asyncio.sleep(0.01)
        if s._auto_task is None and not s.inflight:
            pass


async def test_fade_dark_across_the_gap_then_in_on_the_new_song(client):
    sid, s, eng = await live(client, auto=AUTO)
    s.director.arm(True)
    mgr, now = s.mgr, time.monotonic()
    first = s.current_name
    ev(mgr, kind="track_end", id="track_end:0", at_ms=now * 1000, via="dsp", track_id=0,
       transition={"style": "fade-dark", "out_s": 2.5, "in_s": 2.0, "seconds": None, "on": "now", "change": "big",
                   "dark_max_s": 8.0})
    await settle(s)
    assert s._dark is not None and s.current_name == "black" and s.last_cue["detail"].startswith("the song ended")
    assert s.last_cue["transition"] == "crossfade-1s"
    ev(mgr, kind="section_change", id="section_change:1:0", at_ms=now * 1000, detail="track")   # skipped
    ev(mgr, kind="track_change", id="track_change:1", at_ms=(now + 0.1) * 1000, track_id=1, prev_track_id=0,
       transition={"style": "fade-dark", "out_s": 2.5, "in_s": 1.5, "seconds": None, "on": "now", "change": "big"})
    await settle(s, 40)
    assert s._dark is None and s.current_name not in ("black", None) and "new song" in s.last_cue["detail"]
    assert s.director.latched is None                                # no second cut queued from the section


async def test_segue_cut_held_and_small_change(client):
    sid, s, eng = await live(client, auto=AUTO)
    s.director.arm(True)
    mgr, now = s.mgr, time.monotonic()
    n0 = len(eng.cmds)
    ev(mgr, kind="track_change", id="track_change:5", at_ms=now * 1000, held=True, track_id=5)
    await settle(s)
    assert len(eng.cmds) == n0                                       # Keep this look: nothing
    cur = s.current_name
    ev(mgr, kind="track_change", id="track_change:6", at_ms=now * 1000, track_id=6,
       transition={"style": "cut", "seconds": 0.0, "on": "downbeat", "change": "small"})
    await settle(s, 40)
    assert s.last_cue["transition"] == "cut" and s.last_cue["reason"] == "track"
    src = lambda n: (s.mgr.presets.scenes.get(n) or {}).get("source")       # noqa: E731
    if any(src(n) == src(cur) and n != cur for n in s.mgr.presets.scenes):
        assert src(s.current_name) == src(cur)                         # a variant of what was on


async def test_track_end_armed_warms_the_next_and_disarms(client):
    sid, s, eng = await live(client, auto=AUTO)
    s.director.arm(True)
    mgr, now = s.mgr, time.monotonic()
    ev(mgr, kind="track_end_armed", id="track_end_armed:0", at_ms=(now + 20) * 1000, eta_s=20, via="sonos",
       track_id=0, transition={"style": "cut", "on": "downbeat", "change": "big"})
    assert s._track_arm is not None and s.upnext.head("scene")["for_song"] == "big"
    assert s._pick_task is not None or s.prepared_pick is not None
    ev(mgr, kind="track_end_armed", id="track_end_armed:0", at_ms=None, detail="disarmed", track_id=0)
    assert s._track_arm is None
