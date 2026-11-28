"""Karaoke's mute (the Now playing panel's speaker): the sound silent, the song and its clock running on."""
from flask import Flask

from routes.karaoke import api
from routes.karaoke.sonos_out import KaraokeSonos
from routes.karaoke.store import Store


def _ready(st, title, secs):
    s = st.add_song({"kind": "text", "q": title})
    st.update_song(s["id"], status="ready", audio_s=secs)
    return s["id"]


def test_muted_is_a_setting_off_by_default_and_the_song_runs_on(tmp_path):
    st = Store(tmp_path / "state.json")
    assert st.settings_view()["muted"] is False
    sid = _ready(st, "a", 100.0)
    st.queue_add(sid)
    st.control("play")
    rev = st.s["player"]["rev"]
    assert st.settings(muted=True)["muted"] is True
    assert st.s["player"]["state"] == "playing" and st.s["player"]["rev"] == rev   # the player isn't touched
    assert Store(tmp_path / "state.json").settings_view()["muted"] is True             # kept
    assert st.settings(muted=False)["muted"] is False


def test_the_api_takes_muted(tmp_path, monkeypatch):
    st = Store(tmp_path / "state.json")
    monkeypatch.setattr(api, "store", lambda: st)
    monkeypatch.setattr(api.smconfig, "enabled", lambda cap: True)
    app = Flask(__name__)
    app.register_blueprint(api.karaoke_api, url_prefix="/api")
    c = app.test_client()
    r = c.post("/api/karaoke/settings", json={"muted": True})
    assert r.status_code == 200 and r.get_json()["settings"]["muted"] is True
    assert c.post("/api/karaoke/settings", json={"muted": False}).get_json()["settings"]["muted"] is False


class _FakeSonos:
    def __init__(self, zone, emit):
        self.zone, self.emit, self.starts, self.stops, self.now = zone, emit, [], 0, 1000.0

    def start(self, path, lead_in_ms=0.0, url=None, title=None):
        self.starts.append(lead_in_ms)
        return self.now + 1.0 + lead_in_ms / 1000.0

    def stop(self):
        self.stops += 1


def test_muted_on_a_sonos_stops_it_and_unmuted_it_plays_from_where_the_song_is(tmp_path):
    st = Store(tmp_path / "state.json")
    sid = _ready(st, "a", 100.0)
    outs, mixes = [], []
    clock = {"mono": 1000.0, "wall": 5000.0}

    def factory(zone, emit):
        outs.append(_FakeSonos(zone, emit))
        return outs[-1]

    def mixer(sid_, from_s, guide, mutes):
        mixes.append(round(from_s, 1))
        return tmp_path / ("%032x.flac" % len(mixes))
    d = KaraokeSonos(st, output_factory=factory, mixer=mixer, url_for=lambda k: f"http://x/{k}.flac",
                     mono=lambda: clock["mono"], wall=lambda: clock["wall"])
    st.settings(output="sonos:living")
    st.queue_add(sid)
    d.step()
    o = outs[0]
    assert len(o.starts) == 1
    st.settings(muted=True)
    d.step()
    assert o.stops == 1                                # silent
    d.step()
    assert o.stops == 1 and len(o.starts) == 1         # stays silent, not started again
    st.control("seek", pos_s=40.0)                     # the song runs on while muted
    clock["wall"] += 5
    st.settings(muted=False)
    d.step()
    assert len(o.starts) == 2 and mixes[-1] >= 40.0    # back where the song is now, not where it was muted
