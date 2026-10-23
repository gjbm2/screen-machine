"""show_service/playlist.py (the Music page's player, 8f 22 Oct) against a fake sources component: edge advance (no
double skip), failed -> skip, stepping aside, repeat off/all/one, a stable shuffle, Back's rule, persistence across a
restart (stopped, queue kept), a sound change while playing, playlists, and the routes through a brain. tmp only."""
from __future__ import annotations

import asyncio
import json
import random

import pytest

from show_service import playlist
from show_service.playlist import Player

RUNNING = ("starting", "preparing", "playing")


class Clock:
    def __init__(self) -> None:
        self.t = 5000.0

    def __call__(self) -> float:
        return self.t


class FakeSources:
    """sources.py's surface the player uses: choices(), start_test(), stop_test(), status()["piano"]."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.cur: dict | None = None
        self.pid = 100
        self.calls: list[tuple] = []
        self.known: set | None = None
        self.instruments = [{"id": "windows-gs", "label": "Basic", "kind": "live"},
                            {"id": "salamander", "label": "Grand", "kind": "rendered"}]
        self.outputs = [{"id": "pc22", "label": "PC22 speakers"}, {"id": "none", "label": "Screens only"}]

    def choices(self) -> dict:
        return {"instruments": self.instruments, "default_instrument": "windows-gs", "outputs": self.outputs}

    def start_test(self, source, piece=None, instrument=None, output=None, once=None):
        self.calls.append(("start", piece, instrument, output, once))
        if self.known is not None and piece not in self.known:
            raise ValueError("unknown_piece")
        self.pid += 1
        self.cur = {"piece": piece, "state": "starting", "t": self.clock(), "pid": self.pid, "error": None,
                    "progress": None, "instrument": instrument, "output": output, "once": bool(once)}
        return self.status()["piano"]

    def stop_test(self, source=None):
        self.calls.append(("stop",))
        if self.cur is not None and self.cur["state"] in RUNNING:
            self.set("off")

    def set(self, state: str, **kw) -> None:
        self.cur.update(state=state, t=self.clock(), **kw)

    def status(self) -> dict:
        c = self.cur
        if c is None:
            return {"piano": {"source": "piano", "state": "off", "piece": None, "pid": None, "since_s": None,
                              "error": None, "progress": None}}
        return {"piano": {"source": "piano", "state": c["state"], "piece": c["piece"], "pid": c["pid"],
                          "since_s": round(self.clock() - c["t"], 3), "error": c["error"],
                          "progress": c["progress"], "instrument": c["instrument"], "output": c["output"],
                          "once": c["once"]}}

    def starts(self) -> list:
        return [c[1] for c in self.calls if c[0] == "start"]


class FakeLibrary:
    def __init__(self, pieces: dict) -> None:
        self.pieces = pieces

    def item(self, piece):
        x = self.pieces.get(piece)
        return None if x is None else {"id": piece, "kind": "lib", "pinned": False, "editable": False, **x}


PIECES = {f"lib:p{i}": {"title": f"Piece {i}", "composer": "Someone", "genre_label": "Classical",
                        "duration_s": 60.0 + i} for i in range(1, 7)}
PIECES["medley"] = {"title": "Test pattern: a bit of everything"}


class Rig:
    def __init__(self, tmp_path, seed: int = 7) -> None:
        self.clock = Clock()
        self.src = FakeSources(self.clock)
        self.lib = FakeLibrary(dict(PIECES))
        self.tmp = tmp_path
        self.seed = seed
        self.p = self.new()

    def new(self) -> Player:
        p = Player(None, sources_fn=lambda: self.src, library=self.lib, state_dir=self.tmp, clock=self.clock,
                   rng=random.Random(self.seed))
        p.start()
        return p

    def tick(self, dt: float = 1.0) -> None:
        self.clock.t += dt
        self.p.tick(self.clock.t)

    def play(self, **body) -> dict:
        return self.p.act({"action": "play", **body})

    def finish(self) -> None:
        self.src.set("finished")
        self.tick()

    def playing(self) -> None:
        self.src.set("playing")
        self.tick()


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


ITEMS = ["lib:p1", "lib:p2", "lib:p3"]


def test_plays_once_with_the_named_sound_and_advances_on_the_finished_edge_only(rig):
    v = rig.play(items=ITEMS, name="Evening")
    assert rig.src.calls[-1] == ("start", "lib:p1", "windows-gs", "pc22", True)   # always named, always once
    assert v["state"] == "preparing" and v["words"] == "Starting" and v["current"]["title"] == "Piece 1"
    assert v["next"]["id"] == "lib:p2" and [u["id"] for u in v["upcoming"]] == ["lib:p2", "lib:p3"]
    assert v["queue"] == {"playlist": None, "name": "Evening", "count": 3, "index": 0}
    rig.src.set("preparing", progress=0.4)
    rig.tick()
    v = rig.p.view()
    assert v["state"] == "preparing" and v["words"] == "Getting ready" and v["progress"] == 0.4
    rig.playing()
    rig.clock.t += 10
    v = rig.p.view()
    assert v["state"] == "playing" and v["elapsed_s"] == pytest.approx(11.0) and v["duration_s"] == 61.0
    rig.finish()
    assert rig.src.starts() == ["lib:p1", "lib:p2"]
    for _ in range(3):                                            # the level stays "finished" a while: no double skip
        rig.p.view()
        rig.tick()
    assert rig.src.starts() == ["lib:p1", "lib:p2"]
    assert rig.p.view()["queue"]["index"] == 1


def test_a_failure_shows_the_error_and_skips_and_a_whole_pass_of_failures_stops(rig):
    rig.play(items=ITEMS)
    rig.src.set("failed", error="Traceback...\nRuntimeError: no synth")
    rig.tick()
    v = rig.p.view()
    assert rig.src.starts() == ["lib:p1", "lib:p2"] and v["error"] == "Piece 1 couldn't play: RuntimeError: no synth"
    rig.playing()
    assert rig.p.view()["error"] is None                          # playing again: the error goes
    for _ in range(3):
        rig.src.set("failed", error="boom")
        rig.tick()
    v = rig.p.view()
    assert v["state"] in ("stopped", "error") and v["error"].endswith("couldn't play: boom")
    rig.p.act({"action": "set", "repeat": "all"})
    rig.play(index=0)
    for _ in range(3):
        rig.src.set("failed", error="boom")
        rig.tick()
    v = rig.p.view()
    assert v["state"] == "error" and v["words"] == v["error"]
    n = len(rig.src.starts())
    rig.tick()
    assert len(rig.src.starts()) == n                             # no endless loop


def test_steps_aside_when_something_else_starts_the_piano_and_never_fights(rig):
    rig.play(items=ITEMS)
    rig.playing()
    rig.src.start_test("piano", "file:Other.mid", instrument="windows-gs")     # the Piano card, TestShow, curl
    rig.tick()
    v = rig.p.view()
    assert v["state"] == "stepped_aside" and v["words"] == "Stepped aside: something else started the piano"
    assert ("stop",) not in rig.src.calls and rig.src.cur["piece"] == "file:Other.mid"
    rig.src.set("finished")
    rig.tick()
    assert rig.src.starts()[-1] == "file:Other.mid"               # nothing restarted by itself
    rig.p.act({"action": "stop"})
    assert ("stop",) not in rig.src.calls                          # Stop never stops someone else's piece
    rig.play()                                                    # Play resumes the queue where it was
    assert rig.src.starts()[-1] == "lib:p1"


def test_the_same_piece_restarted_by_someone_else_is_noticed(rig):
    rig.play(piece="lib:p1")
    rig.playing()
    rig.src.start_test("piano", "lib:p1", instrument="salamander")           # a new run: a new pid
    rig.tick()
    assert rig.p.view()["state"] == "stepped_aside"
    pid_less = Rig(rig.tmp / "b")                                 # without a pid: the state began again later
    pid_less.play(piece="lib:p2")
    pid_less.playing()
    pid_less.src.cur["pid"] = None
    pid_less.tick()
    pid_less.clock.t += 5
    pid_less.src.set("playing")                                   # "playing" again, begun 5 s after the one seen
    pid_less.tick()
    assert pid_less.p.view()["state"] == "stepped_aside"


def test_a_start_that_fails_at_once_is_a_failure_edge(rig):
    real = rig.src.start_test

    def failing(*a, **kw):
        st = real(*a, **kw)
        if a[1] == "lib:p1":
            rig.src.set("failed", error="couldn't start: [Errno 2] No such file")
            st = rig.src.status()["piano"]
        return st
    rig.src.start_test = failing
    rig.play(items=ITEMS)
    rig.tick()
    v = rig.p.view()
    assert rig.src.starts() == ["lib:p1", "lib:p2"] and v["error"].startswith("Piece 1 couldn't play")


def test_a_stop_from_elsewhere_stops_the_player(rig):
    rig.play(items=ITEMS)
    rig.playing()
    rig.src.stop_test("piano")
    rig.tick()
    v = rig.p.view()
    assert v["state"] == "stopped" and v["words"] == "Stopped" and rig.src.starts() == ["lib:p1"]


def test_repeat_off_stops_after_the_last_all_wraps_one_replays(rig):
    rig.play(items=ITEMS[:2])
    rig.finish()
    rig.finish()
    v = rig.p.view()
    assert v["state"] == "stopped" and v["words"] == "Played to the end of the queue" and v["queue"]["index"] == 0
    assert rig.src.starts() == ["lib:p1", "lib:p2"]
    rig.tick()
    assert rig.src.starts() == ["lib:p1", "lib:p2"]               # no auto-repeat
    rig.p.act({"action": "set", "repeat": "all"})
    rig.play()
    rig.finish()
    rig.finish()
    assert rig.src.starts()[-3:] == ["lib:p1", "lib:p2", "lib:p1"]
    rig.p.act({"action": "set", "repeat": "one"})
    assert rig.p.view()["next"]["id"] == "lib:p1"
    rig.finish()
    rig.finish()
    assert rig.src.starts()[-2:] == ["lib:p1", "lib:p1"]
    rig.p.act({"action": "next"})                                 # Next still moves on
    assert rig.src.starts()[-1] == "lib:p2"


def test_shuffle_keeps_one_order_with_the_current_first(rig):
    rig.p.act({"action": "set", "shuffle": True})
    v = rig.play(items=[f"lib:p{i}" for i in range(1, 7)], index=2)
    assert v["current"]["id"] == "lib:p3" and v["queue"]["index"] == 0
    order = [u["id"] for u in v["upcoming"]]
    assert sorted(order + ["lib:p3"]) != order and len(set(order)) == 5
    for _ in range(4):
        rig.tick()
        assert [u["id"] for u in rig.p.view()["upcoming"]] == order          # not re-rolled
    rig.finish()
    assert rig.src.starts()[-1] == order[0]
    rig.p.act({"action": "set", "shuffle": False})
    v = rig.p.view()
    assert v["current"]["id"] == order[0] and v["queue"]["index"] == int(order[0][-1]) - 1


def test_back_restarts_after_five_seconds_else_goes_back(rig):
    rig.play(items=ITEMS, index=1)
    rig.playing()
    rig.clock.t += 6
    rig.p.act({"action": "prev"})
    assert rig.src.starts() == ["lib:p2", "lib:p2"]               # > 5 s in: the current again
    rig.playing()
    rig.clock.t += 2
    rig.p.act({"action": "prev"})
    assert rig.src.starts()[-1] == "lib:p1"                       # within 5 s: the previous
    rig.playing()
    rig.p.act({"action": "prev"})
    assert rig.src.starts()[-1] == "lib:p1"                       # at the start: the first again
    rig.p.act({"action": "stop"})
    assert rig.src.calls[-1] == ("stop",)
    rig.p.act({"action": "next"})                                 # stopped: Next/Back only move the place
    assert rig.p.view()["current"]["id"] == "lib:p2" and rig.src.starts()[-1] == "lib:p1"


def test_after_a_restart_it_is_stopped_but_remembers(rig, tmp_path):
    rig.p.act({"action": "set", "instrument": "salamander", "output": "none", "repeat": "all"})
    rig.play(items=ITEMS, index=1, name="Late")
    rig.playing()
    rig.p.stop()
    doc = json.loads((tmp_path / "player.json").read_text())
    assert doc["v"] == 1 and doc["queue"]["items"] == ITEMS and doc["queue"]["index"] == 1
    n = len(rig.src.calls)
    p2 = rig.new()
    rig.clock.t += 1
    p2.tick(rig.clock.t)
    v = p2.view()
    assert v["state"] == "stopped" and len(rig.src.calls) == n                 # never plays by itself
    assert v["current"]["id"] == "lib:p2" and v["queue"]["name"] == "Late" and v["instrument"] == "salamander"
    assert v["output"] == "none" and v["repeat"] == "all" and v["shuffle"] is False
    p2.act({"action": "play"})
    assert rig.src.calls[-1] == ("start", "lib:p2", "salamander", "none", True)
    (tmp_path / "playlists.json").write_text("{torn")
    p3 = rig.new()
    assert p3.snapshot()["problems"] and p3.playlists_view() == {"playlists": []}


def test_a_sound_or_place_change_while_playing_restarts_the_piece_with_it(rig):
    rig.play(items=ITEMS, index=1)
    rig.playing()
    rig.p.act({"action": "set", "instrument": "salamander"})
    assert rig.src.calls[-1] == ("start", "lib:p2", "salamander", "pc22", True)
    rig.p.act({"action": "set", "output": "none"})
    assert rig.src.calls[-1] == ("start", "lib:p2", "salamander", "none", True)
    n = len(rig.src.calls)
    rig.p.act({"action": "set", "output": "none", "repeat": "one"})            # nothing new: no restart
    assert len(rig.src.calls) == n
    rig.tick()
    assert rig.p.view()["state"] == "preparing"                   # its own new run, not "something else"
    for bad, code in (({"instrument": "steinway"}, "unknown_instrument"), ({"output": "sonos:x"}, "unknown_output"),
                      ({"repeat": "twice"}, "bad_repeat"), ({"shuffle": "yes"}, "bad_shuffle")):
        with pytest.raises(ValueError, match=code):
            rig.p.act({"action": "set", **bad})
    rig.src.instruments = rig.src.instruments[:1]                 # the sound went away: sources' default, quietly
    rig.p.act({"action": "next"})
    assert rig.src.calls[-1][2] == "windows-gs"


def test_queue_edits_and_unknown_pieces(rig):
    with pytest.raises(ValueError, match="nothing_queued"):
        rig.play()
    rig.p.act({"action": "add", "piece": "lib:p4"})                # an empty queue: added, not played
    assert rig.src.calls == [] and rig.p.view()["current"]["id"] == "lib:p4"
    rig.play(items=ITEMS)
    rig.p.act({"action": "add", "piece": "lib:p5"})
    rig.p.act({"action": "play_next", "piece": "lib:p6"})
    assert [u["id"] for u in rig.p.view()["upcoming"]] == ["lib:p6", "lib:p2", "lib:p3", "lib:p5"]
    for bad in ({"action": "add", "piece": "lib:gone"}, {"action": "play", "piece": "../x"}, {"action": "jump"},
                {"action": "play", "playlist": "pl-nope"}, {}, "play"):
        with pytest.raises(ValueError):
            rig.p.act(bad)
    rig.src.known = {"lib:p3"}                                     # p1 and p2 vanished from sources' view
    rig.play(items=ITEMS)
    v = rig.p.view()
    assert rig.src.starts()[-1] == "lib:p3" and v["error"] == "2 pieces are no longer in the library: skipped"
    rig.src.known = set()
    rig.play(items=ITEMS)
    assert rig.p.view()["state"] == "stopped"
    rig.lib.pieces.pop("lib:p1")
    v = rig.p.view()
    assert v["current"] == {"id": "lib:p1", "title": "No longer in the library", "kind": "lib", "pinned": False,
                            "editable": False, "missing": True}


def test_playlists_crud_missing_and_persistence(rig, tmp_path):
    pl = rig.p.playlist_create("Sunday", ["lib:p1", "lib:p2", "lib:gone"])
    assert pl["name"] == "Sunday" and pl["id"].startswith("pl-")
    assert pl["items"][2] == {"piece": "lib:gone", "title": "No longer in the library", "missing": True}
    assert pl["items"][0] == {"piece": "lib:p1", "title": "Piece 1", "missing": False, "composer": "Someone",
                              "genre_label": "Classical", "duration_s": 61.0}
    assert rig.p.playlists_view() == {"playlists": [{"id": pl["id"], "name": "Sunday", "count": 3,
                                                     "duration_s": 123.0}]}
    v = rig.play(playlist=pl["id"], index=1)
    assert v["current"]["id"] == "lib:p2" and v["queue"]["playlist"] == {"id": pl["id"], "name": "Sunday"}
    rig.p.playlist_put(pl["id"], "Sunday best", ["lib:p2", "lib:p1"])
    assert rig.p.view()["queue"]["name"] == "Sunday best"
    assert [i["piece"] for i in rig.p.playlist_get(pl["id"])["items"]] == ["lib:p2", "lib:p1"]
    empty = rig.p.playlist_create(None)
    assert empty["name"] == "New playlist" and empty["items"] == []
    with pytest.raises(ValueError, match="empty_playlist"):
        rig.play(playlist=empty["id"])
    with pytest.raises(ValueError, match="bad_items"):
        rig.p.playlist_put(pl["id"], items=["lib:p1", "../x"])
    with pytest.raises(ValueError, match="bad_name"):
        rig.p.playlist_create("x" * 200)
    with pytest.raises(LookupError):
        rig.p.playlist_get("pl-nope")
    rig.p.stop()
    doc = json.loads((tmp_path / "playlists.json").read_text())
    assert doc["v"] == 1 and [p["name"] for p in doc["playlists"]] == ["Sunday best", "New playlist"]
    p2 = rig.new()
    assert [p["name"] for p in p2.playlists_view()["playlists"]] == ["Sunday best", "New playlist"]
    assert p2.playlist_delete(pl["id"]) == {"deleted": pl["id"]}
    v = p2.view()
    assert v["queue"]["playlist"] is None and v["queue"]["name"] == "Sunday best" and v["queue"]["count"] == 3


def test_without_sources_it_says_so(tmp_path):
    p = Player(None, sources_fn=lambda: None, library=None, state_dir=tmp_path)
    p.start()
    v = p.act({"action": "play", "piece": "lib:p1"})
    assert v["state"] == "error" and v["words"] == "The piano player isn't running."
    assert v["current"]["title"] == "lib:p1" and v["choices"] == {"instruments": [], "default_instrument": None,
                                                                  "outputs": []}


def test_routes_through_a_brain(rig):
    from show_service.tests.test_api import FakeBrain
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    brain = FakeBrain([rig.p])
    try:
        async def go():
            app = web.Application(client_max_size=64 << 10)
            playlist.routes(app, lambda: brain)
            async with TestClient(TestServer(app, host="127.0.0.1", port=0)) as cl:
                r = await cl.get("/api/v1/player")
                b = await r.json()
                assert r.status == 200 and b["state"] == "stopped" and b["words"] == "Nothing queued"
                assert set(b) >= {"queue", "current", "next", "upcoming", "progress", "elapsed_s", "duration_s",
                                  "instrument", "output", "repeat", "shuffle", "error", "choices"}
                r = await cl.post("/api/v1/playlists", json={"name": "Big", "items": ["lib:p1"] * 2000})
                b = await r.json()                                 # > 64 KB of JSON: read past the bus's cap
                assert r.status == 200 and len(b["items"]) == 2000
                pid = b["id"]
                r = await cl.get("/api/v1/playlists")
                assert (await r.json())["playlists"][0]["count"] == 2000
                r = await cl.put(f"/api/v1/playlists/{pid}", json={"items": ["lib:p2", "lib:p3"]})
                assert [i["piece"] for i in (await r.json())["items"]] == ["lib:p2", "lib:p3"]
                r = await cl.get(f"/api/v1/playlists/{pid}")
                assert (await r.json())["name"] == "Big"
                r = await cl.post("/api/v1/player", json={"action": "play", "playlist": pid})
                b = await r.json()
                assert r.status == 200 and b["current"]["id"] == "lib:p2" and b["state"] == "preparing"
                r = await cl.post("/api/v1/player", json={"action": "set", "output": "sonos:nowhere"})
                b = await r.json()
                assert r.status == 400 and b == {"error": "unknown_output",
                                                 "detail": "That place to play isn't available"}
                r = await cl.post("/api/v1/player", data=b"{not json")
                assert r.status == 400 and (await r.json())["error"] == "bad_request"
                r = await cl.post("/api/v1/player", json={"action": "stop"})
                assert (await r.json())["state"] == "stopped"
                r = await cl.get("/api/v1/playlists/pl-nope")
                assert r.status == 404 and (await r.json())["error"] == "unknown_playlist"
                r = await cl.delete(f"/api/v1/playlists/{pid}")
                assert (await r.json()) == {"deleted": pid}
            app2 = web.Application()
            playlist.routes(app2, lambda: None)
            async with TestClient(TestServer(app2, host="127.0.0.1", port=0)) as cl:
                r = await cl.get("/api/v1/player")
                assert r.status == 503 and (await r.json())["error"] == "starting"
        asyncio.run(go())
    finally:
        brain.close()
        rig.p.stop()


class SpeedSources(FakeSources):
    """1c's start_test(speed, start_s) and status pos_s (23 Oct)."""

    def __init__(self, clock: Clock) -> None:
        super().__init__(clock)
        self.pos_s: float | None = None

    def start_test(self, source, piece=None, instrument=None, output=None, once=None, speed=None, start_s=None):
        st = super().start_test(source, piece, instrument, output, once)
        self.calls[-1] = self.calls[-1] + (speed, start_s)
        return st

    def status(self) -> dict:
        s = super().status()
        s["piano"]["pos_s"] = self.pos_s
        return s


def test_speed_persists_rides_every_start_and_a_change_carries_on_from_where_it_was(tmp_path):
    """Owner, 23 Oct: a speed control, 1x by default. The player's speed goes to every start_test; a change while a
    piece plays restarts it at the new speed from sources' pos_s (its own run, never 'stepped aside'); elapsed is
    the place in the piece; a bad speed is refused; it survives a restart."""
    r = Rig(tmp_path)
    r.src = SpeedSources(r.clock)
    r.play(items=ITEMS)
    assert r.src.calls[-1][-2:] == (None, None)                       # 1x: nothing extra sent
    r.playing()
    r.src.pos_s = 42.5
    v = r.p.act({"action": "set", "speed": 1.25})
    assert r.src.calls[-1][:2] == ("start", "lib:p1") and r.src.calls[-1][-2:] == (1.25, 42.5)
    assert v["speed"] == 1.25 and v["state"] in ("preparing", "playing")
    r.playing()
    assert r.p.view()["state"] == "playing" and r.p.view()["elapsed_s"] == 42.5      # ours, the place in the piece
    r.finish()
    assert r.src.calls[-1][1] == "lib:p2" and r.src.calls[-1][-2:] == (1.25, None)    # the next piece keeps it
    with pytest.raises(ValueError, match="bad_speed"):
        r.p.act({"action": "set", "speed": 9})
    r.p.stop()
    p2 = r.new()
    assert p2.view()["speed"] == 1.25 and p2.view()["state"] == "stopped"


def test_pause_resume_seek_and_play_from_a_place(tmp_path):
    """1c's score follow-along (23 Oct): play {piece, start_s}; seek {start_s} carries on from there (playing,
    paused or stopped); pause stops the piano but keeps the piece and its place (state paused, elapsed_s = the place),
    resume or a bare play carries on from it; the view says when elapsed_s was true (at_ms)."""
    r = Rig(tmp_path)
    r.src = SpeedSources(r.clock)
    v = r.p.act({"action": "play", "piece": "lib:p2", "start_s": 30.0})
    assert r.src.calls[-1][1] == "lib:p2" and r.src.calls[-1][-1] == 30.0 and isinstance(v["at_ms"], int)
    r.playing()
    r.src.pos_s = 47.25
    v = r.p.act({"action": "pause"})
    assert v["state"] == "paused" and v["words"] == "Paused" and v["elapsed_s"] in (47.2, 47.3)
    assert r.src.calls[-1] == ("stop",) and r.src.cur["state"] == "off"
    r.tick()
    assert r.p.view()["state"] == "paused"                            # a stopped piano doesn't end the pause
    r.p.act({"action": "resume"})
    assert r.src.calls[-1][1] == "lib:p2" and r.src.calls[-1][-1] == 47.25
    r.playing()
    r.p.act({"action": "pause"})
    r.p.act({"action": "play"})                                       # a bare Play resumes too
    assert r.src.calls[-1][1] == "lib:p2" and r.src.calls[-1][-1] == 47.25
    r.playing()
    r.p.act({"action": "seek", "start_s": 12})
    assert r.src.calls[-1][1] == "lib:p2" and r.src.calls[-1][-1] == 12.0
    r.p.act({"action": "stop"})
    r.p.act({"action": "seek", "start_s": 5})                         # stopped: seek plays from there
    assert r.src.calls[-1][-1] == 5.0 and r.p.view()["state"] in ("preparing", "playing")
    with pytest.raises(ValueError, match="bad_start"):
        r.p.act({"action": "seek", "start_s": -1})


def test_a_device_output_is_kept_even_before_it_connects(tmp_path):
    """The player takes 'device:<id>' as the output (This device on a phone), listed by sources yet or not."""
    r = Rig(tmp_path)
    r.p.act({"action": "set", "output": "device:ipad-1"})
    r.play(items=ITEMS)
    assert r.p.view()["output"] == "device:ipad-1"
    with pytest.raises(ValueError, match="unknown_output"):
        r.p.act({"action": "set", "output": "device:"})
