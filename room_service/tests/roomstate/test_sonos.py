"""sonos adapter against a fake SoCo player: reads the playback context (favourite or uri), sets volume and playback,
rejects bad values at load, and never retries a favourite that doesn't exist. No LAN."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from room_service.adapters.sonos import SonosAdapter

R4 = "x-sonosapi-hls:stations%7eplayable%7e%7ebbc_radio_fourfm?sid=325&flags=8232&sn=1"


class FakeSoCo:
    def __init__(self, ip: str) -> None:
        self.ip, self.player_name, self.volume, self.mute = ip, "Drawing Room", 61, False
        self.state, self.uri = "STOPPED", R4
        self.group = SimpleNamespace(coordinator=self, members=[self])
        self.avTransport = SimpleNamespace(GetMediaInfo=lambda args: {"CurrentURI": self.uri})
        favs = [SimpleNamespace(title="BBC Radio 4", resources=[SimpleNamespace(uri=R4.split("?")[0])],
                                resource_meta_data="<meta/>", reference=None),
                SimpleNamespace(title="Disco", resources=[SimpleNamespace(uri="x-rincon-cpcontainer:disco")],
                                resource_meta_data="", reference="container")]
        self.music_library = SimpleNamespace(get_sonos_favorites=lambda: favs)
        self.queue: list = []
        self.shuffle = False

    def get_sonos_playlists(self):
        return [SimpleNamespace(title="Dinner", item_id="SQ:1")]

    def get_current_transport_info(self):
        return {"current_transport_state": self.state}

    def get_current_track_info(self):
        return {"title": "Today", "artist": "", "album": "", "position": "0:01:00", "duration": "", "uri": self.uri}

    def pause(self):
        self.state = "PAUSED_PLAYBACK"

    def stop(self):
        self.state = "STOPPED"

    def play_uri(self, uri, meta=""):
        if uri.startswith("x-rincon-cpcontainer"):
            raise RuntimeError("UPnP 714: illegal MIME type")      # a playlist: the adapter goes through the queue
        self.uri, self.state = uri, "PLAYING"

    def clear_queue(self):
        self.queue = []

    def add_to_queue(self, ref):
        self.queue.append(ref)

    def play_from_queue(self, i):
        self.uri, self.state = "x-rincon-cpcontainer:disco", "PLAYING"


def adapter() -> tuple[SonosAdapter, FakeSoCo]:
    dev = FakeSoCo("192.168.1.17")
    a = SonosAdapter({"drawing-room": {"room": "Drawing Room", "ip": dev.ip}}, factory=lambda ip: dev, ramp_s=0.05)
    a.change_time_s = 1.0
    return a, dev


def test_reads_the_playback_context_and_now_playing():
    async def body():
        a, dev = adapter()
        r = await a.read(a.members)
        assert r["drawing-room.volume"].value == 61 and r["drawing-room.playback"].value == "stopped"
        dev.state = "PLAYING"
        r = await a.read(["drawing-room.playback", "drawing-room.now_playing"])
        assert r["drawing-room.playback"].value == {"favourite": "BBC Radio 4"}       # query string ignored
        np = r["drawing-room.now_playing"].value
        assert np["state"] == "playing" and np["source"] == "radio" and np["group"] == ["Drawing Room"]
        dev.uri = "x-rincon-mp3radio://example/stream"
        assert (await a.read(["drawing-room.playback"]))["drawing-room.playback"].value == {"uri": dev.uri}
    asyncio.run(body())


def test_sets_volume_and_playback_and_confirms():
    async def body():
        a, dev = adapter()
        out = await a.apply({"drawing-room.volume": 40}, {"over_s": 0.2})
        assert out.status == "ok" and dev.volume == 40
        out = await a.apply({"drawing-room.playback": {"favourite": "BBC Radio 4"}})
        assert out.status == "ok" and dev.state == "PLAYING"
        out = await a.apply({"drawing-room.playback": {"favourite": "Disco"}})       # a playlist: via the queue
        assert out.status == "ok" and dev.queue == ["container"]
        out = await a.apply({"drawing-room.playback": "paused"})
        assert out.status == "ok" and dev.state == "PAUSED_PLAYBACK"
        out = await a.apply({"drawing-room.playback": {"favourite": "Nope"}})
        assert out.status == "failed" and out.retry is False
    asyncio.run(body())


def test_values_are_checked_at_load_and_now_playing_is_read_only():
    a, _ = adapter()
    assert a.normalize("drawing-room.volume", 40.4) == 40
    for member, value in [("drawing-room.volume", 120), ("drawing-room.playback", "loud"),
                          ("drawing-room.playback", {"favourite": 3}), ("drawing-room.now_playing", {})]:
        with pytest.raises(ValueError):
            a.normalize(member, value)
    assert a.poll_all and "drawing-room.now_playing" in a.members


def test_the_playback_schema_offers_the_real_favourites_and_playlists_after_a_poll():
    """c8 review #7: oneOf pause / stop / a favourite / a Sonos playlist / a Spotify link, each object with shuffle,
    volume and only_if_idle; music is never fought by default; "only if music was playing" for put-back."""
    async def body():
        a, dev = adapter()
        s = a.value_schema("drawing-room.playback")
        assert [b.get("const") or b["required"][0] for b in s["oneOf"]] == \
            ["paused", "stopped", "favourite", "sonos_playlist", "spotify_link"]
        assert s["oneOf"][2]["properties"]["favourite"]["enum"] == []                  # no I/O before a poll
        await a.read(["drawing-room.playback"])
        s = a.value_schema("drawing-room.playback")
        assert s["oneOf"][2]["properties"]["favourite"]["enum"] == ["BBC Radio 4", "Disco"]
        assert s["oneOf"][3]["properties"]["sonos_playlist"]["enum"] == ["Dinner"]
        assert set(s["oneOf"][2]["properties"]) == {"favourite", "shuffle", "volume", "only_if_idle"}
        assert s["x-ui"]["default_policy"] == "ignore" and s["x-ui"]["store_if_labels"][0]["value"][0] == "favourite"
        assert a.value_schema("drawing-room.now_playing")["readOnly"] is True
        v = a.value_schema("drawing-room.volume")["x-ui"]
        assert v["unit"] == "%" and v["ends"] == {"min": "Silent", "max": "Loud"} and a.subsystem_label == "Music"
    asyncio.run(body())


def test_playback_objects_take_their_options_and_words():
    a, _ = adapter()
    n = a.normalize("drawing-room.playback", {"sonos_playlist": "Dinner", "shuffle": True, "volume": 30.2})
    assert n == {"sonos_playlist": "Dinner", "shuffle": True, "volume": 30}
    assert a.normalize("drawing-room.playback", {"spotify_link": "https://open.spotify.com/playlist/x"})
    for bad in [{"favourite": "A", "sonos_playlist": "B"}, {"favourite": "A", "loud": True},
                {"spotify_link": "https://example.com/x"}, {"favourite": "A", "shuffle": "yes"},
                {"favourite": "A", "volume": 101}, {"shuffle": True}]:
        with pytest.raises(ValueError):
            a.normalize("drawing-room.playback", bad)
    assert a.words("drawing-room.playback", "paused") == "Paused"
    assert a.words("drawing-room.playback", {"favourite": "Disco", "shuffle": True, "only_if_idle": True}) == \
        "Play Disco, shuffled, unless music is already playing"
    assert a.label("drawing-room.playback") == "Drawing Room music"


def test_a_playlist_plays_shuffled_and_only_if_idle_leaves_music_alone():
    async def body():
        a, dev = adapter()
        out = await a.apply({"drawing-room.playback": {"sonos_playlist": "Dinner", "shuffle": True}}, {"over_s": 0.05})
        assert out.status == "ok" and dev.queue[-1].title == "Dinner" and dev.shuffle is True
        dev.uri = R4                                                     # someone put the radio on
        out = await a.apply({"drawing-room.playback": {"favourite": "Disco", "only_if_idle": True}}, {"over_s": 0.05})
        assert out.status == "ok" and "left alone" in out.members["drawing-room.playback"].detail and dev.uri == R4
    asyncio.run(body())


def test_fades_pause_restores_volume_and_play_fades_up():
    """Owner, 16 Oct: mode-driven Sonos changes are smooth. A pause fades out, pauses and puts the volume back silently;
    starting music begins at 0 and fades up to where the volume was; a newer change cancels a running ramp."""
    async def body():
        a, dev = adapter()
        dev.state, dev.volume = "PLAYING", 50
        seen = []
        orig = type(dev).volume if isinstance(type(dev).__dict__.get("volume"), property) else None
        await a.apply({"drawing-room.playback": "paused"}, {"over_s": 0.4})
        assert dev.state == "PAUSED_PLAYBACK" and dev.volume == 50          # faded out, paused, volume restored
        await a.apply({"drawing-room.playback": {"favourite": "BBC Radio 4"}}, {"over_s": 0.4})
        assert dev.state == "PLAYING" and dev.volume == 50                  # started at 0, faded up to 50
        z = a.zones["drawing-room"]
        task = asyncio.create_task(a.apply({"drawing-room.volume": 0}, {"over_s": 2.0}))
        await asyncio.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.3)
        mid = dev.volume
        assert 0 < mid < 50                                                 # stopped part-way, not at either end
        await asyncio.sleep(0.5)
        assert dev.volume == mid                                            # and the ramp thread really stopped
    asyncio.run(body())


def test_entering_piano_with_the_player_stopped_sends_nothing(tmp_path):
    """mo-2: Piano wants "paused"; a stopped player already means no music. No playback row, no reconcile retry,
    and the switch isn't partial."""
    from .harness import boot, example, fake_adapters, go, make_room, row, sent

    def lounge(raw):                       # the real adapter adds now_playing: name the two settable members
        house = raw["modes"]["house"]["state"]
        house.pop("sonos.lounge")
        house.update({"sonos.lounge.volume": {"retrieve": "pre_live"}, "sonos.lounge.playback": {"retrieve": "pre_live"}})

    async def body():
        dev = FakeSoCo("192.168.1.17")
        a = fake_adapters()
        a.sonos = SonosAdapter({"lounge": {"room": "Lounge", "ip": dev.ip}}, factory=lambda ip: dev, ramp_s=0.05)
        a.sonos.change_time_s = 1.0
        r = make_room(tmp_path, example(lounge), adapters=a)
        await boot(r)
        n = len(sent(r, "sonos"))
        tr = await go(r, "piano")
        assert tr["status"] == "ok" and row(tr, "sonos.lounge.playback") is None
        assert len(sent(r, "sonos")) == n and dev.state == "STOPPED"
        d = r.c.s["desired"]["sonos.lounge.playback"]
        assert d["settled"] and d["retry"] is None
        await r.c.reconcile_once()
        assert len(sent(r, "sonos")) == n and r.c.s["desired"]["sonos.lounge.playback"]["retry"] is None
    asyncio.run(body())
