"""routes.karaoke.room_song: the song playing in the room, the karaoke player's or a Sonos zone's."""
from routes.karaoke import room_song as R
from routes.karaoke.store import Store


def status(state="PLAYING", title="Rehab", artist="Amy Winehouse", pos="0:01:10", dur="0:03:35", age=1.0):
    return {"subsystems": {"sonos": {"members": {
        "drawing-room.now_playing": {"believed": {"state": state, "title": title, "artist": artist, "duration": dur,
                                                  "position": pos}, "age_s": age},
        "drawing-room.volume": {"believed": 30}}}}}


def test_sonos_tracks_reads_the_zone():
    [t] = R.sonos_tracks(status())
    assert t == {"zone": "drawing-room", "state": "playing", "title": "Rehab", "artist": "Amy Winehouse",
                 "duration_s": 215, "position_s": 70, "age_s": 1.0}


def test_a_new_track_is_prepared_once(tmp_path):
    st = Store(tmp_path / "state.json")
    w = R.Watch(st, read=status)
    w.poll_once()
    w.poll_once()
    songs = list(st.s["songs"].values())
    assert len(songs) == 1 and songs[0]["request"]["origin"] == "sonos" and songs[0]["status"] == "queued"
    assert not st.s["queue"]                                         # prepared, not queued for singing


def test_a_ready_track_is_the_song_now_at_sonos_position(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "sonos_delay_ms", lambda zone: 120.0)
    st = Store(tmp_path / "state.json")
    s = st.add_song({"kind": "lrclib", "artist": "Amy Winehouse", "title": "Rehab", "duration": 215})
    st.update_song(s["id"], status="ready", audio_s=215.0)
    body = R.song_now(st, R.sonos_tracks(status(age=2.0)), now=1000.0)
    assert body["source"] == "sonos:drawing-room"
    assert body["player"]["song_id"] == s["id"] and body["player"]["pos_s"] == 70 and body["player"]["at"] == 998.0
    assert body["settings"]["audio_delay_ms"] == 120.0


def test_karaoke_wins_and_nothing_known_is_none(tmp_path):
    st = Store(tmp_path / "state.json")
    assert R.song_now(st, R.sonos_tracks(status()))["source"] is None    # not in the library (yet)
    s = st.add_song({"kind": "text", "q": "x"})
    st.update_song(s["id"], status="ready", audio_s=60.0)
    st.queue_add(s["id"])                                                # starts playing
    assert R.song_now(st, R.sonos_tracks(status()))["source"] == "karaoke"


def test_short_tracks_are_not_prepared(tmp_path):
    st = Store(tmp_path / "state.json")
    assert R.prepare_track(st, R.sonos_tracks(status(dur="0:00:30"))[0]) is None


def test_a_karaoke_room_with_no_song_rests(tmp_path):
    st = Store(tmp_path / "state.json")
    assert R.song_now(st, [], karaoke_room=True)["rest"] is True
    assert R.song_now(st, [], karaoke_room=False)["rest"] is False
