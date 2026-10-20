"""show.ai (the mode's leave for the AI to listen: show_service's GET/PUT /api/v1/listening "mode", owner 20 Oct: by
default only the live modes let the AI hear the room) against the fake show_service in test_show_adapter.py: reads that
fetch only the asked member's route, AI listening alone going stale like the Feel, a failing listening route never
staling the others, apply ok / refused (never retried) / down or 5xx (retried) / unconfirmed, the Music card's "on"
never sent, all three members in one apply, the cold-restart re-apply, normalize and the words, the mode editor's form,
and a live mode's row loaded and put back by the room's own restore. Never touches show_service (:8703) or :8700."""

from __future__ import annotations

import json

import pytest

from room_service.adapters.show import ShowAdapter, ai_value
from room_service.roomstate.contract import schema_words
from room_service.roomstate.model import RoomConfig
from room_service.roomstate.schema import check_value

from .roomstate.harness import boot, example, fake_adapters, go, make_room
from .test_show_adapter import FakeShow, nowhere, settled, vibes_yaml


@pytest.fixture
async def show():
    fake = FakeShow()
    fake.url = await fake.start()
    yield fake
    await fake.server.close()


# -- read -------------------------------------------------------------------------------------------------------------

async def test_ai_reads_only_its_own_route_and_only_the_modes_leave(show, tmp_path):
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    assert {m: r.value for m, r in (await a.read(["ai"])).items()} == {"ai": False}     # never set: off (fail closed)
    assert show.gets == ["/api/v1/listening"]
    show.listening["mode"] = True
    show.gets.clear()
    got = await a.read(["main", "mic", "ai"])
    assert {m: r.value for m, r in got.items()} == {"main": {"vibe": "home", "listening": False}, "mic": False,
                                                    "ai": True}
    assert sorted(show.gets) == ["/api/v1/listening", "/api/v1/mic", "/api/v1/vibe"]
    show.listening.update(on=True, mode=False)                                   # the Music card's switch isn't the mode's
    assert (await a.read(["ai"]))["ai"].value is False
    await settled(a)


async def test_ai_alone_goes_stale_like_the_feel(show, tmp_path, monkeypatch):
    with pytest.raises(ConnectionError, match="unreachable"):
        await ShowAdapter(nowhere(), vibes_yaml(tmp_path)).read(["ai"])
    with pytest.raises(ConnectionError, match="unreachable"):                   # all asked, show_service down
        await ShowAdapter(nowhere(), vibes_yaml(tmp_path)).read(["main", "mic", "ai"])
    show.status["listening"] = 503
    with pytest.raises(ConnectionError, match="503"):
        await ShowAdapter(show.url, vibes_yaml(tmp_path)).read(["ai"])
    show.status["listening"] = 200
    monkeypatch.setattr(show, "listening_answer", lambda: {"on": True, "effective": False})   # before the 20 Oct API
    with pytest.raises(ValueError, match="/api/v1/listening answer has no mode"):
        await ShowAdapter(show.url, vibes_yaml(tmp_path)).read(["ai"])


async def test_a_failing_listening_route_never_stales_the_others(show, tmp_path, monkeypatch):
    """A show_service from before the 20 Oct contract (no "mode" in the answer) or a listening route that fails: the
    Feel and the mic still read; AI listening is left out (its belief keeps its last value)."""
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    show.status["listening"] = 404
    got = await a.read(["main", "mic", "ai"])
    assert set(got) == {"main", "mic"}
    assert set(await a.read(["mic", "ai"])) == {"mic"}                           # no Feel asked: the mic answered
    monkeypatch.setattr(show, "listening_answer", lambda: {"on": True})
    show.status["listening"] = 200
    assert set(await a.read(["main", "ai"])) == {"main"}
    show.status["mic"] = 503                                                     # neither bool answers, the Feel did
    assert set(await a.read(["main", "mic", "ai"])) == {"main"}
    show.status["vibe"] = 503                                                    # the Feel's failure still fails it
    with pytest.raises(ConnectionError, match="503"):
        await a.read(["main", "ai"])
    await settled(a)


# -- apply ------------------------------------------------------------------------------------------------------------

async def test_apply_puts_the_modes_leave_and_reads_it_back(show, tmp_path):
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    out = await a.apply({"ai": True}, {})
    assert out.status == "ok" and set(out.members) == {"ai"} and out.members["ai"].status == "ok"
    assert show.ai_puts == [{"mode": True}] and show.listening == {"on": False, "mode": True}   # "on" never sent
    assert show.puts == [] and show.mic_puts == []
    out = await a.apply({"ai": "off"}, {})                                         # any spelling normalize takes
    assert out.status == "ok" and show.ai_puts[-1] == {"mode": False} and show.listening["mode"] is False


async def test_apply_refused_is_never_retried(show, tmp_path):
    show.status["listening_put"] = 400                                            # e.g. a show_service that takes only "on"
    out = await ShowAdapter(show.url, vibes_yaml(tmp_path)).apply({"ai": True}, {})
    assert out.status == "failed" and out.retry is False and out.members["ai"].retry is False
    assert "bad_request" in out.detail
    out = await ShowAdapter(show.url, vibes_yaml(tmp_path)).apply({"ai": {"vibe": "party"}}, {})
    assert out.status == "failed" and out.retry is False and len(show.ai_puts) == 1      # never sent


async def test_apply_down_busy_or_missing_is_retried(show, tmp_path):
    out = await ShowAdapter(nowhere(), vibes_yaml(tmp_path)).apply({"ai": True}, {})
    assert out.status == "failed" and out.retry is True and "show_service unreachable" in out.detail
    show.status["listening_put"] = 503
    out = await ShowAdapter(show.url, vibes_yaml(tmp_path)).apply({"ai": True}, {})
    assert out.status == "failed" and out.retry is True and "503 busy" in out.detail
    show.status["listening_put"] = 404
    out = await ShowAdapter(show.url, vibes_yaml(tmp_path)).apply({"ai": True}, {})
    assert out.status == "failed" and out.retry is True and "PUT /api/v1/listening" in out.detail


async def test_apply_that_does_not_land_is_unconfirmed(show, tmp_path):
    show.ignore_ai_puts = True
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    a.change_time_s = 0.3
    out = await a.apply({"ai": True}, {})
    assert out.status == "unconfirmed" and out.retry is True and "AI off" in out.detail
    assert out.members["ai"].status == "unconfirmed"


async def test_three_members_in_one_apply(show, tmp_path):
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    out = await a.apply({"main": "party", "mic": True, "ai": True}, {})
    assert out.status == "ok" and set(out.members) == {"main", "mic", "ai"}
    assert show.mic_puts == [{"mode": True}] and show.ai_puts == [{"mode": True}]
    show.status["listening_put"] = 400                                           # one refused: the worst wins
    out = await a.apply({"main": "home", "mic": False, "ai": False}, {})
    assert out.status == "failed" and out.members["mic"].status == "ok" and out.members["ai"].retry is False
    assert out.retry is False and out.detail.startswith("AI listening: bad_request")
    show.status["listening_put"] = 503                                           # one down: retried
    out = await a.apply({"mic": True, "ai": True}, {})
    assert out.status == "failed" and out.retry is True and out.members["ai"].retry is True
    await settled(a)


async def test_a_cold_show_service_gets_the_modes_leave_again(show, tmp_path):
    """show_service restarted with the leave lost (a new boot_id, mode off) after the adapter landed it on: put back
    once per boot, in the background. The listening answer carries boot_id, so AI listening read alone can tell."""
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    assert (await a.apply({"ai": True}, {})).status == "ok"
    await a.read(["ai"])
    assert a._flags["ai"].reapply is None                                        # same boot: nothing
    show.listening["mode"], show.boot = False, "boot-2"                          # the cold restart
    assert (await a.read(["ai"]))["ai"].value is False                           # the fact, at once
    await a._flags["ai"].reapply
    assert show.listening["mode"] is True and show.ai_puts == [{"mode": True}, {"mode": True}]
    show.listening["mode"] = False                                               # off again, same boot: left be
    await a.read(["main", "mic", "ai"])
    assert a._flags["ai"].reapply.done() and len(show.ai_puts) == 2
    assert (await a.apply({"ai": False}, {})).status == "ok"                     # the room dropped the leave itself
    show.boot = "boot-3"
    await a.read(["ai"])
    assert len(show.ai_puts) == 3 and a._flags["ai"].reapply.done()              # nothing to put back
    assert a._flags["mic"].reapply is None and show.mic_puts == []               # the mic's own, untouched
    b = ShowAdapter(show.url, vibes_yaml(tmp_path))                             # never applied: never re-applies
    show.boot = "boot-4"
    await b.read(["ai"])
    assert b._flags["ai"].reapply is None and len(show.ai_puts) == 3
    await settled(a)
    await settled(b)


# -- normalize, words, the form ---------------------------------------------------------------------------------------

def test_normalize_takes_bools_and_on_off_words():
    a = ShowAdapter(nowhere())
    for v, want in ((True, True), (False, False), ("on", True), ("OFF", False), (" Listen ", True), ("listens", True),
                    ("True", True), ("false", False)):
        assert a.normalize("ai", v) is want, v
    for bad in (1, 0, None, "yes", "", [], {"vibe": "party"}, "party"):
        with pytest.raises(ValueError, match="AI listening is true .* or false"):
            a.normalize("ai", bad)
    with pytest.raises(ValueError, match="show.main"):                           # a feel there says where it goes
        ai_value({"vibe": "party", "listening": True})
    with pytest.raises(ValueError, match="main .* mic .* and ai"):               # the members, all three
        a.normalize("other", True)


def test_ai_words_and_form(tmp_path):
    a = ShowAdapter(nowhere(), vibes_yaml(tmp_path))
    assert a.label("ai") == "AI listening" and a.label("mic") == "Microphone" and a.label("main") == "Feel"
    assert a.show("ai", True) == "AI listens" and a.show("ai", False) == "AI off"
    assert a.show("mic", True) == "Mic listens"                                  # the mic's words unchanged
    s = a.value_schema("ai")
    assert s["type"] == "boolean" and s["x-ui"]["labels"] == {"true": "Listens in this mode", "false": "Off"}
    assert s["x-ui"]["label"] == "AI listening" and "costs money" in s["description"] and "widget" not in s["x-ui"]
    assert "AI listening switch" in s["x-ui"]["help"]
    assert check_value(True, s) is None and check_value("on", s) is not None
    assert schema_words(s, True) == "Listens in this mode" and a.words("ai", False) == "Off"
    json.dumps(s)


# -- the room ---------------------------------------------------------------------------------------------------------

def lets_the_ai_listen(raw: dict) -> None:
    raw["modes"]["disco"]["state"]["show.ai"] = {"set": True, "restore": True}


def test_a_live_modes_row_loads(tmp_path):
    a = fake_adapters()
    a.show = ShowAdapter(nowhere(), vibes_yaml(tmp_path))
    cfg = RoomConfig(example(lets_the_ai_listen), list(vars(a).values()))
    row = cfg.modes["disco"].state["show.ai"]
    assert row.set is True and row.restore is True
    assert "show.ai" not in cfg.modes["normal"].state                             # a mode without the row: no leave
    with pytest.raises(Exception, match="AI listening is true"):
        RoomConfig(example(lambda raw: raw["modes"]["disco"]["state"].update({"show.ai": {"set": "maybe"}})),
                   list(vars(a).values()))


async def test_a_mode_that_lets_the_ai_listen_and_the_room_drops_it(show, tmp_path):
    a = fake_adapters()
    a.show = ShowAdapter(show.url, vibes_yaml(tmp_path), cache_file=tmp_path / "show_catalog.json")
    r = make_room(tmp_path, example(lets_the_ai_listen), adapters=a)
    await boot(r)
    await r.c._refresh(["show.ai"])                                               # the room's belief before the switch
    tr = await go(r, "disco")
    assert tr["status"] == "ok", tr
    assert show.listening == {"on": False, "mode": True}
    await go(r, "normal")                                                         # restore: the room's own put-back
    assert show.listening["mode"] is False and show.ai_puts == [{"mode": True}, {"mode": False}]
    assert show.puts == [] and show.mic_puts == []                                # the Feel and the mic never touched
    d = r.c.describe()
    t = d["targets"]["show.ai"]
    assert t["category"] == "setting" and t["label"] == "AI listening" and t["subsystem_label"] == "Feel"
    assert d["subsystems"]["show"]["members"] == ["main", "mic", "ai"]
    assert d["modes"]["disco"]["entries"]["show.ai"]["words"] == "Listens in this mode"
    await settled(a.show)
