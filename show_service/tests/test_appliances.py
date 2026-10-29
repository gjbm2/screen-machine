"""Appliances in the art director's light groups (appliances.py; owner, 28 Oct): their config, what a scene asks of
them, the paced sends, the scenes that fit them, a mode's light scene tags, and the director driving them end to end."""
import asyncio
import json

from show_service import appliances as ap
from show_service import light_scenes as ls
from show_service.tests import test_light_scenes as tls

APPS_RAW = {
    "ball": {"label": "Ball", "controls": {"pan_spin": {
        "power": {"target": "ball.main", "on": "spin", "off": "stop"},
        "speed": {"target": "ball_motor.speed", "words": [[0.0, "slow"], [0.5, "fast"]]},
        "direction": {"target": "ball_motor.dir", "cw": "right", "ccw": "left", "min_gap_s": 100}}}},
    "spots": {"controls": {"intensity": {"power": {"target": "spots.main", "on": "on", "off": "off"},
                                         "level": {"target": "spots.level", "steps": 8}},
                           "colour_slot": {"target": "spots.colour"},
                           "shutter": {"target": "spots.flash", "strobe": "on", "open": "off"}}},
    "junk": {"controls": {"zoom": {"target": "x"}}}}


def apps() -> dict:
    probs: list[str] = []
    out = ap.parse(APPS_RAW, probs)
    assert "junk" not in out and any("junk" in p for p in probs)
    return out


def room(**vals) -> dict:
    return {"current": "m", "transition": None,
            "members": {k.replace("__", "."): {"believed": v} for k, v in vals.items()}}


def test_targets_power_speed_direction_and_level():
    a = apps()
    assert a["ball"]["caps"] == ["pan_spin"] and a["spots"]["caps"] == ["intensity", "colour_slot", "shutter"]
    t = ap.targets(a["ball"], {"pan_spin": {"speed": 0.6, "dir": "flow"}}, "ccw")
    assert {k: v for k, (v, _) in t.items()} == {"ball.main": "spin", "ball_motor.speed": "fast",
                                                "ball_motor.dir": "left"}
    assert t["ball_motor.dir"][1] == 100
    assert {k: v for k, (v, _) in ap.targets(a["ball"], {"pan_spin": 0}, "cw").items()} == {"ball.main": "stop"}
    t = ap.targets(a["spots"], {"intensity": 0.75, "colour_slot": "white", "shutter": "strobe"}, None)
    assert {k: v for k, (v, _) in t.items()} == {"spots.main": "on", "spots.level": 6, "spots.colour": "white",
                                                "spots.flash": "on"}
    off = ap.targets(a["spots"], {"intensity": 0.0, "colour_slot": "red"}, None)
    assert {k: v for k, (v, _) in off.items()} == {"spots.main": "off"}      # off: the rest left alone


def test_states_parse_and_problems():
    probs: list[str] = []
    st = ap.parse_states({"pan_spin": {"speed": 2, "dir": "flow"}, "intensity": 0.5, "shutter": "blink", "x": 1},
                         "s", probs)
    assert st == {"pan_spin": {"speed": 1.0, "dir": "flow"}, "intensity": 0.5} and len(probs) == 2


def test_executor_sends_changes_once_paced_and_never_while_the_room_changes_mode():
    a = apps()
    ex = ap.Executor()
    want = {"ball": (a["ball"], {"pan_spin": {"speed": 0.2, "dir": "flow"}})}
    r = room(ball__main="stop", ball_motor__speed="slow", ball_motor__dir="right")
    busy_room = {**r, "transition": {"id": "t1"}}
    assert ex.step(0.0, want, "cw", busy_room, False) == {}
    assert ex.step(0.1, want, "cw", r, True) == {}                          # a send in flight
    assert ex.step(0.2, want, "cw", r, False) == {"ball.main": "spin"}      # speed and way already right
    assert ex.step(0.3, want, "cw", r, False) == {}                         # asked once
    r["members"]["ball.main"]["believed"] = "spin"
    assert ex.step(5.0, want, "ccw", r, False) == {"ball_motor.dir": "left"}
    assert ex.step(10.0, want, "cw", r, False) == {}                        # the way back: within its 100 s gap
    assert ex.step(106.0, want, "cw", r, False) == {"ball_motor.dir": "right"}


def cat(tmp_path) -> ls.Catalogue:
    scenes = {"version": 1, "scenes": {
        **tls.MOVERS["scenes"],
        "rest": {"appliances": {"pan_spin": 0, "intensity": 0}, "tags": {"modes": ["party"]}},
        "classic": {"appliances": {"pan_spin": {"speed": 0.5, "dir": "flow"}, "intensity": 1.0,
                                   "colour_slot": "white"}, "tags": {"modes": ["disco", "party"]}},
        "wild": {"appliances": {"pan_spin": 1.0, "intensity": 1.0}, "tags": {"modes": ["disco"]}},
        "both": {"layers": [], "appliances": {"intensity": 0.5}, "tags": {"modes": ["other"]}}}}
    effects = {"version": 1, "effects": {
        **tls.EFFECTS["effects"],
        "strobe": {"on": ["drop"], "length": {"bars": 1}, "needs": ["shutter"], "appliances": {"shutter": "strobe"}}}}
    return tls.full_cat(tmp_path, scenes=scenes, effects=effects)


def groups(tmp_path) -> dict:
    a = apps()
    cfg = {"lamps": {"select": "kind:lamp"}, "remote": {"appliances": ["ball", "spots"], "repeat_weight": 0.5}}
    return ls.group_members(cfg, tls.rig_of(tmp_path, movers=0), appliances=a)


def test_scenes_fit_by_what_they_drive_and_mode_tags_narrow_with_a_fallback(tmp_path):
    c, g = cat(tmp_path), groups(tmp_path)
    assert g["remote"]["members"] == [] and g["remote"]["appliances"] == ["ball", "spots"]
    assert sorted(c.pool_for(g["remote"])) == ["both", "classic", "rest", "wild"]
    assert "rest" not in c.pool_for(g["lamps"]) and "both" in c.pool_for(g["lamps"])
    assert sorted(c.pool_for(g["remote"], ("modes:disco",))) == ["classic", "wild"]
    assert sorted(c.pool_for(g["remote"], ("modes:party",))) == ["classic", "rest"]
    assert sorted(c.pool_for(g["lamps"], ("modes:disco",))) == sorted(c.pool_for(g["lamps"]))   # none tagged: all
    assert c.fits(c.effects["strobe"], g["remote"]) and not c.fits(c.effects["strobe"], g["lamps"])


def test_a_group_with_repeat_weight_may_stay_and_a_person_next_never_does(tmp_path):
    import random
    c, g = cat(tmp_path), groups(tmp_path)
    rng = random.Random(4)
    stays = sum(c.pick_for(g["remote"], {}, None, rng, avoid="rest", tags=("modes:party",))[0] == "rest"
                for _ in range(400))
    assert 60 < stays < 200                                               # 0.5 / 1.5 of the draws
    assert all(c.pick_for(g["remote"], {}, None, rng, avoid="rest", tags=("modes:party",), hard_avoid=True)[0]
               == "classic" for _ in range(50))
    assert all(c.pick_for(g["lamps"], {}, None, rng, avoid="hush")[0] != "hush" for _ in range(50))


def test_the_director_drives_allowed_appliances_with_their_groups_scene(tmp_path):
    lights = json.loads(json.dumps(tls.LIGHTS))
    lights["groups"]["remote"] = {"label": "Remote", "appliances": ["ball", "spots"], "repeat_weight": 0.5}
    lights["appliances"] = APPS_RAW
    r = tls.prig(tmp_path, lights=lights, cat=cat(tmp_path))
    puts: list[dict] = []

    async def room_put(path, body, timeout=5.0):
        puts.append(body["values"])
        return 200, {}

    async def main():
        r.ctx.loop = asyncio.get_running_loop()
        r.ctx.room_put = room_put

        async def run(seconds):
            for _ in range(int(round(seconds * 10))):
                r.t = round(r.t + 0.1, 6)
                r.d.tick(r.t)
                await asyncio.sleep(0)

        r.room = room(ball__main="stop", spots__main="off", director__ball=False, director__spots=False)
        r.d.set_target("alpha")
        await run(2.0)
        assert "remote" not in r.d._lp and puts == []                    # not allowed: no program, hands off
        await run(10.0)                                                  # settled: no look change pending
        d = r.d                                                          # a scene-less pick already tried at this
        d._lp_tried = (d.light_scenes.revision, d._groups_key, d.look_n, d._ls_reason)   # look (as in the room)
        r.room["members"].update({"director.ball": {"believed": True}, "director.spots": {"believed": True},
                                  "director.light_scenes": {"believed": {"scene_tags": ["modes:disco"]}}})
        await run(0.3)
        assert r.d._lp["remote"]["scene"] in ("classic", "wild")         # at once, not at the next look
        await run(2.0)
        scene = r.d._lp["remote"]["scene"]
        assert scene in ("classic", "wild")
        sent = {k: v for p in puts for k, v in p.items()}
        assert sent["ball.main"] == "spin" and sent["spots.main"] == "on" and sent["spots.level"] == 8
        view = r.d.snapshot()["lights"]["appliances"]
        assert view["allowed"] == ["ball", "spots"] and view["scene_tags"] == ["modes:disco"]
        n = len(puts)
        r.room["members"]["director.ball"]["believed"] = False            # the mode takes the ball back
        r.room["members"]["director.spots"]["believed"] = False
        await run(2.0)
        assert len(puts) == n                                             # hands off

    asyncio.run(main())
