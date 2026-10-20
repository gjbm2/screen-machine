"""show.mic (the mode's claim on the room's mic: show_service's GET/PUT /api/v1/mic "mode") against the fake
show_service in test_show_adapter.py: reads that fetch only the asked member's route, the mic alone going stale like
the Feel, a missing mic route never staling the Feel, apply ok / refused (never retried) / down or 5xx (retried) /
unconfirmed, both members in one apply, the cold-restart re-apply, normalize and the words, the mode editor's form, and
a mode that follows the music put back by the room's own restore. Never touches show_service (:8703) or :8700."""

from __future__ import annotations

import json

import pytest

from room_service.adapters.show import ShowAdapter, mic_value
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

async def test_each_member_reads_only_its_own_route(show, tmp_path):
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    assert {m: r.value for m, r in (await a.read(["mic"])).items()} == {"mic": False}
    assert show.gets == ["/api/v1/mic"]                                          # no /vibe for the mic alone
    show.mic["mode"] = True
    show.gets.clear()
    assert (await a.read(["main"]))["main"].value == {"vibe": "home", "listening": False}
    assert show.gets == ["/api/v1/vibe"]                                         # and no /mic for the Feel alone
    got = await a.read(["main", "mic"])
    assert got["main"].value == {"vibe": "home", "listening": False} and got["mic"].value is True
    show.mic.update(muted=True, auto=True)                                       # Mute and Auto aren't the mode's claim
    assert (await a.read(["mic"]))["mic"].value is True
    assert await a.read(["other"]) == {}
    await settled(a)


async def test_the_mic_alone_goes_stale_like_the_feel(show, tmp_path):
    with pytest.raises(ConnectionError, match="unreachable"):
        await ShowAdapter(nowhere(), vibes_yaml(tmp_path)).read(["mic"])
    show.status["mic"] = 503
    with pytest.raises(ConnectionError, match="503"):
        await ShowAdapter(show.url, vibes_yaml(tmp_path)).read(["mic"])
    with pytest.raises(ConnectionError, match="unreachable"):                   # both asked, show_service down
        await ShowAdapter(nowhere(), vibes_yaml(tmp_path)).read(["main", "mic"])


async def test_a_missing_mic_route_never_stales_the_feel(show, tmp_path):
    """A show_service from before the mic API (404): the Feel still reads; the mic is left out (its belief keeps its
    last value) rather than taking the whole subsystem down."""
    show.status["mic"] = 404
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    got = await a.read(["main", "mic"])
    assert set(got) == {"main"} and got["main"].value == {"vibe": "home", "listening": False}
    show.status["vibe"] = 503                                                    # the Feel's failure still fails it
    with pytest.raises(ConnectionError, match="503"):
        await a.read(["main", "mic"])
    await settled(a)


async def test_a_mic_answer_without_mode_is_not_a_reading(show, tmp_path, monkeypatch):
    monkeypatch.setattr(show, "mic_answer", lambda: {"on": True})
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    with pytest.raises(ValueError, match="no mode"):
        await a.read(["mic"])
    assert set(await a.read(["main", "mic"])) == {"main"}
    await settled(a)


# -- apply ------------------------------------------------------------------------------------------------------------

async def test_apply_puts_the_mode_claim_and_reads_it_back(show, tmp_path):
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    out = await a.apply({"mic": True}, {})
    assert out.status == "ok" and out.members == {"mic": out.members["mic"]} and out.members["mic"].status == "ok"
    assert show.mic_puts == [{"mode": True}] and show.mic["mode"] is True and show.puts == []
    out = await a.apply({"mic": "off"}, {})                                       # any spelling normalize takes
    assert out.status == "ok" and show.mic_puts[-1] == {"mode": False} and show.mic["mode"] is False


async def test_apply_refused_is_never_retried(show, tmp_path):
    show.status["mic_put"] = 400
    out = await ShowAdapter(show.url, vibes_yaml(tmp_path)).apply({"mic": True}, {})
    assert out.status == "failed" and out.retry is False and out.members["mic"].retry is False
    assert "bad_mic" in out.detail
    out = await ShowAdapter(show.url, vibes_yaml(tmp_path)).apply({"mic": {"vibe": "party"}}, {})
    assert out.status == "failed" and out.retry is False and len(show.mic_puts) == 1     # never sent


async def test_apply_down_busy_or_missing_is_retried(show, tmp_path):
    out = await ShowAdapter(nowhere(), vibes_yaml(tmp_path)).apply({"mic": True}, {})
    assert out.status == "failed" and out.retry is True and "show_service unreachable" in out.detail
    show.status["mic_put"] = 503
    out = await ShowAdapter(show.url, vibes_yaml(tmp_path)).apply({"mic": True}, {})
    assert out.status == "failed" and out.retry is True and "503 busy" in out.detail
    show.status["mic_put"] = 404
    out = await ShowAdapter(show.url, vibes_yaml(tmp_path)).apply({"mic": True}, {})
    assert out.status == "failed" and out.retry is True and "404" in out.detail


async def test_apply_that_does_not_land_is_unconfirmed(show, tmp_path):
    show.ignore_mic_puts = True
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    a.change_time_s = 0.3
    out = await a.apply({"mic": True}, {})
    assert out.status == "unconfirmed" and out.retry is True and "Mic off" in out.detail
    assert out.members["mic"].status == "unconfirmed"


async def test_both_members_in_one_apply(show, tmp_path):
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    out = await a.apply({"main": "party", "mic": True}, {})
    assert out.status == "ok" and set(out.members) == {"main", "mic"}
    assert show.puts == [{"vibe": "party", "listening": False}] and show.mic_puts == [{"mode": True}]
    show.status["mic_put"] = 400                                                 # one refused: the worst wins
    out = await a.apply({"main": "home", "mic": False}, {})
    assert out.status == "failed" and out.members["main"].status == "ok" and out.members["mic"].retry is False
    assert out.retry is False and out.detail.startswith("Microphone: bad_mic")
    show.status["mic_put"] = 503                                                 # one down: retried
    out = await a.apply({"main": "home", "mic": False}, {})
    assert out.status == "failed" and out.retry is True and out.members["mic"].retry is True
    await settled(a)


async def test_a_cold_show_service_gets_the_mode_claim_again(show, tmp_path):
    """show_service restarted with its state lost (a new boot_id, the claim off) after the adapter landed it on: put
    back once per boot, in the background. A read that can't tell the boot (the mic alone, no boot_id) does nothing."""
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    await a.read(["main", "mic"])                                                # boot-1 seen
    assert (await a.apply({"mic": True}, {})).status == "ok"
    show.mic["mode"], show.boot = False, "boot-2"                                # the cold restart
    assert (await a.read(["mic"]))["mic"].value is False and a._flags["mic"].reapply is None  # boot unknown: nothing
    got = await a.read(["main", "mic"])
    assert got["mic"].value is False                                             # the fact, at once
    await a._flags["mic"].reapply
    assert show.mic["mode"] is True and show.mic_puts == [{"mode": True}, {"mode": True}]
    show.mic["mode"] = False                                                     # off again, same boot: left be
    await a.read(["main", "mic"])
    assert a._flags["mic"].reapply.done() and len(show.mic_puts) == 2
    assert (await a.apply({"mic": False}, {})).status == "ok"                    # the room dropped the claim itself
    show.boot = "boot-3"
    await a.read(["main", "mic"])
    assert len(show.mic_puts) == 3 and a._flags["mic"].reapply.done()                   # nothing to put back
    b = ShowAdapter(show.url, vibes_yaml(tmp_path))                             # never applied: never re-applies
    show.mic_boot, show.boot = True, "boot-4"                                    # (a boot_id on the mic's own answer)
    await b.read(["mic"])
    assert b._flags["mic"].reapply is None and len(show.mic_puts) == 3
    await settled(a)
    await settled(b)


# -- normalize, words, the form ---------------------------------------------------------------------------------------

def test_normalize_takes_bools_and_on_off_words():
    a = ShowAdapter(nowhere())
    for v, want in ((True, True), (False, False), ("on", True), ("OFF", False), (" Listen ", True), ("listens", True),
                    ("True", True), ("false", False)):
        assert a.normalize("mic", v) is want, v
    for bad in (1, 0, None, "yes", "", [], {"vibe": "party"}, "party"):
        with pytest.raises(ValueError, match="true .* or false"):
            a.normalize("mic", bad)
    with pytest.raises(ValueError, match="show.main"):                           # a feel on the mic says where it goes
        mic_value({"vibe": "party", "listening": True})
    assert a.normalize("main", "party") == {"vibe": "party", "listening": False}  # the Feel unchanged
    with pytest.raises(ValueError, match="main .* mic .* and ai"):
        a.normalize("other", True)


def test_mic_words_and_form(tmp_path):
    a = ShowAdapter(nowhere(), vibes_yaml(tmp_path))
    assert a.label("mic") == "Microphone" and a.label("main") == "Feel"
    assert a.show("mic", True) == "Mic listens" and a.show("mic", False) == "Mic off"
    s = a.value_schema("mic")
    assert s["type"] == "boolean" and s["x-ui"]["labels"] == {"true": "Listens in this mode", "false": "Off"}
    assert "Mute on the Music card always wins" in s["description"] and s["x-ui"]["label"] == "Microphone"
    assert "widget" not in s["x-ui"]                                             # two choices, like the pages' setting
    assert check_value(True, s) is None and check_value("on", s) is not None
    assert schema_words(s, True) == "Listens in this mode" and a.words("mic", False) == "Off"
    assert a.value_schema("main")["properties"]["vibe"]["enum"] == ["dinner", "bright"]   # the Feel form unchanged
    json.dumps(s)


# -- the room ---------------------------------------------------------------------------------------------------------

def follows_the_music(raw: dict) -> None:
    raw["modes"]["disco"]["state"]["show.mic"] = {"set": "listens", "restore": True}


async def test_a_mode_that_follows_the_music_claims_the_mic_and_the_room_drops_it(show, tmp_path):
    a = fake_adapters()
    a.show = ShowAdapter(show.url, vibes_yaml(tmp_path), cache_file=tmp_path / "show_catalog.json")
    r = make_room(tmp_path, example(follows_the_music), adapters=a)
    assert r.cfg.modes["disco"].state["show.mic"].set is True                     # normalized at load
    await boot(r)
    await r.c._refresh(["show.mic"])                                              # the room's belief before the switch
    tr = await go(r, "disco")
    assert tr["status"] == "ok", tr
    assert show.mic["mode"] is True
    await go(r, "normal")                                                         # restore: the room's own put-back
    assert show.mic["mode"] is False and show.mic_puts == [{"mode": True}, {"mode": False}]
    assert show.puts == []                                                        # the Feel never touched
    d = r.c.describe()
    t = d["targets"]["show.mic"]
    assert t["category"] == "setting" and t["label"] == "Microphone" and t["subsystem_label"] == "Feel"
    assert d["subsystems"]["show"]["members"] == ["main", "mic", "ai"]
    assert d["modes"]["disco"]["entries"]["show.mic"]["words"] == "Listens in this mode"
    await settled(a.show)


def test_a_feel_on_a_bare_show_key_says_to_name_show_main(tmp_path):
    """A bare "show" selects both members now; a feel there fails to load with words that say where it goes. The row is
    the one the docs still show (config/room/vibes.yaml's header, docs/SHOW_ENGINE.md, show_service/README.md: c8's,
    to be re-keyed); the same row under "show.main" loads and sets only the Feel."""
    row = {"set": {"vibe": "party", "listening": True, "overrides": {"follow_amount": 0.8}}, "restore": True,
           "policy": "ignore"}
    a = fake_adapters()
    a.show = ShowAdapter(nowhere(), vibes_yaml(tmp_path))
    with pytest.raises(Exception, match="show.main"):
        RoomConfig(example(lambda raw: raw["modes"]["disco"]["state"].update(show=row)), list(vars(a).values()))
    cfg = RoomConfig(example(lambda raw: raw["modes"]["disco"]["state"].update({"show.main": row})),
                     list(vars(a).values()))
    assert cfg.modes["disco"].state["show.main"].set == {"vibe": "party", "listening": True,
                                                         "overrides": {"follow_amount": 0.8}}
    assert "show.mic" not in cfg.modes["disco"].state                            # the mic left alone
