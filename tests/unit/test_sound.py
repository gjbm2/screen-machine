"""smsound: the sound outputs, the latency store, and the calibration (docs/SOUND.md)."""
import json

import numpy as np
import pytest

import smconfig
from smsound import chirps as C
from smsound import latency as L
from smsound import outputs as O


@pytest.fixture
def lat(tmp_path, monkeypatch):
    p = tmp_path / "latency.json"
    p.write_text(json.dumps({"version": 2, "screens": {}, "sources": {},
                             "outputs": {"hue": {"intensity": {"rise": {"t50": 129.8}, "trim_ms": 0.0}}}}))
    monkeypatch.setattr(L, "path", lambda dep=None: p)
    monkeypatch.setattr(L, "browser_path", lambda dep=None: tmp_path / "state" / "browser.json")
    return p


def test_output_ids_parse_into_kinds():
    assert O.parse("screen:vj") == ("screen", "vj") and O.parse("sonos:living") == ("sonos", "living")
    assert O.parse("pc22") == ("pc22", "") and O.parse("none") == ("none", "") and O.parse("device:ab-1")
    for bad in ("screen:", "sonos:../x", "loud", None, 3, "device:" + "x" * 65):
        assert O.parse(bad) is None, bad


def test_the_laptop_has_its_screen_and_no_sound_and_nothing_it_lacks():
    rows = O.static_outputs(smconfig.load("laptop"))
    ids = [r["id"] for r in rows]
    assert ids[0] == "screen:vj" and ids[-1] == "none" and "pc22" not in ids
    assert not any(i.startswith("sonos:") for i in ids)               # its sonos capability is off
    assert rows[0]["calibrate"] == "chirp" and rows[0]["kind_label"] == "Screen"


def test_finsbury_has_the_pc_speakers_labelled_in_its_own_words():
    rows = O.static_outputs(smconfig.load("finsbury"))
    pc = next(r for r in rows if r["id"] == "pc22")
    assert pc["label"] == smconfig.load("finsbury").words("windows_speakers", "")
    assert {r["kind"] for r in rows} >= {"screen", "pc22", "none"}


def test_a_consumer_sees_every_place_and_why_it_cant_play_some():
    rows = O.for_consumer({"screen", "none"}, [O.row("screen:a", "A"), O.row("pc22", "PC")], "Karaoke can't")
    assert rows[0]["available"] and not rows[1]["available"] and rows[1]["why"] == "Karaoke can't"


def test_a_measured_delay_keeps_the_trim_and_the_rest_of_the_file(lat):
    assert L.effective_ms("screen:vj") is None                        # nothing yet: the browser's own figure
    assert L.describe("screen:vj")["source"] == "browser"
    L.set_trim("screen:vj", 15)
    L.record("screen:vj", 134, extra={"spread_ms": 3.0})
    assert L.effective_ms("screen:vj") == 149.0
    d = L.describe("screen:vj")
    assert (d["delay_ms"], d["trim_ms"], d["source"], d["method"]) == (134.0, 15.0, "measured", "chirp")
    doc = json.loads(lat.read_text())
    assert doc["outputs"]["hue"]["intensity"]["rise"]["t50"] == 129.8   # the lights' entry untouched
    L.set_manual("screen:vj", None)
    assert L.effective_ms("screen:vj") == 15.0 and L.describe("screen:vj")["source"] == "browser"
    with pytest.raises(ValueError):
        L.record("loud", 10)


def test_sonos_falls_back_to_its_old_start_ms_then_the_default(lat):
    assert L.effective_ms("sonos:living") == 500.0 and L.describe("sonos:living")["source"] == "default"
    doc = json.loads(lat.read_text())
    doc["outputs"]["sonos:living"] = {"start_ms": 620}
    lat.write_text(json.dumps(doc))
    assert L.effective_ms("sonos:living") == 620.0 and L.describe("sonos:living")["source"] == "measured"
    L.record("sonos:living", 480)
    assert L.effective_ms("sonos:living") == 480.0


def test_piano_render_reads_the_zones_start_from_the_one_store(lat):
    from piano_render import sonos
    L.record("sonos:living", 455)
    assert sonos.start_ms_for("living", lat) == 455.0
    assert sonos.start_ms_for("kitchen", lat) == 500.0


def test_the_overview_lists_every_kind_of_latency(lat):
    L.record("screen:vj", 134)
    o = L.overview(smconfig.load("laptop"))
    assert [r["id"] for r in o["sound"]] == ["screen:vj"] and o["sound"][0]["latency"]["effective_ms"] == 134.0
    assert o["lights"][0]["key"] == "hue.intensity" and o["lights"][0]["value_ms"] == 129.8
    assert "rule" in o and o["screens"] == [] and o["sources"] == []


def test_the_chirp_file_has_each_chirp_where_measure_looks():
    sr = 48000
    specs = C.chirp_specs(len(C.OFFSETS_S))
    sig = C.render(sr, list(C.OFFSETS_S), specs, bed_s=2.0)
    t_file0 = 100.0
    clicks = [t_file0 + 2.0 + o for o in C.OFFSETS_S]
    heard = np.concatenate([np.zeros(int(0.3 * sr)), 0.3 * sig])      # heard 300 ms after the file's start time
    r = C.measure(t_file0, heard, sr, clicks, specs, min_delay_s=-1.0, max_delay_s=2.0)
    assert r["ok"] and abs(r["delay_ms"] - 300) <= 2, r


def test_the_calibrator_refuses_what_it_cant_measure_and_starts_on_a_screen(monkeypatch):
    from routes.sound.calibrate import Calibrator
    c = Calibrator("http://127.0.0.1:8703", connect=lambda url: (_ for _ in ()).throw(OSError("no show")))
    assert c.levels_base == "ws://127.0.0.1:8703/v1/levels?wave=2048"
    for bad in ("pc22", "device:x", "none", "loud"):
        with pytest.raises(ValueError):
            c.start(bad)
    monkeypatch.setattr(O, "known", lambda oid, dep=None, devices=(): oid == "screen:vj")
    with pytest.raises(ValueError):
        c.start("screen:nope")
    st = c.start("screen:vj")
    assert st["state"] == "running" and st["output"] == "screen:vj"
    c._thread.join(5)
    assert c.state()["state"] == "failed" and "broke" in c.state()["message"]


def test_karaoke_plays_on_a_sound_output_and_carries_its_delay(tmp_path, lat, monkeypatch):
    from routes.karaoke import live_view
    from routes.karaoke.store import Store
    monkeypatch.setattr(live_view, "karaoke_screen", lambda setting: setting or "vj")
    old = tmp_path / "state.json"
    old.write_text(json.dumps({"settings": {"output": "screen", "audio_delay_ms": 134}}))
    st = Store(old)                                                     # the first version's settings
    assert st.output() == "screen:vj" and L.sound("screen:vj")["delay_ms"] == 134.0
    v = st.settings_view()
    assert v["output"] == "screen:vj" and v["audio_delay_ms"] == 134.0 and "audio_delay_ms" not in st.s["settings"]
    st.settings(audio_delay_ms=150)                                     # the old nudge: the output's own delay
    assert L.effective_ms("screen:vj") == 150.0
    st.settings(output="sonos:living")
    assert st.settings_view()["audio_delay_ms"] == 0                    # a Sonos's clock is anchored to the room
    st.settings(output="none")
    assert st.output() == "none"
    st.settings(output="screen")                                        # the old word: the karaoke screen
    assert st.output() == "screen:vj"
    st.settings(output="device:abc")                                    # This device: a browser plays it itself
    assert st.output() == "device:abc" and st.settings_view()["audio_delay_ms"] is None   # unmeasured: its own figure
    for bad in ("pc22", "device:", "loud"):
        with pytest.raises(ValueError):
            st.settings(output=bad)


def test_a_sonos_is_measured_from_its_play_request_and_its_delay_corrected(lat, tmp_path, monkeypatch):
    """The chirp file plays on a (fake) Sonos that is really 620 ms late while the store says 500: the zone's delay
    becomes 620, measured through a (fake) mic feed of levels frames."""
    import base64
    import time as _t
    from routes.sound import calibrate as cal
    from smsound import files
    monkeypatch.setattr(files, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(files, "audio_url", lambda key: f"http://x/{key}.flac")
    monkeypatch.setattr(O, "known", lambda oid, dep=None, devices=(): oid == "sonos:living")
    monkeypatch.setattr(cal, "MIC_WAIT_S", 2.0)
    sr = cal.SR
    true_start_s = 0.62

    class FakeSonos:
        def start(self, path, lead_in_ms, url, title):
            self.t_put = _t.monotonic()
            self.sig = C.render(sr, list(C.OFFSETS_S), C.chirp_specs(len(C.OFFSETS_S)), bed_s=cal.SONOS_BED_S,
                                tail_s=cal.SONOS_WINDOW_S[1] + 0.5)
            return self.t_put + 0.5                              # what the store said: 500 ms

        def stop(self):
            pass

    sonos = FakeSonos()

    class FakeMic:
        """Levels frames of what the room hears, as fast as asked (arrival times on CLOCK_MONOTONIC)."""
        def __init__(self):
            self.t = _t.monotonic() - 1.0

        def recv(self):
            n = 2048
            self.t += n / sr
            room = np.random.default_rng(int(self.t * 1000)).normal(0, 0.003, n)
            if hasattr(sonos, "t_put"):
                i1 = int(round((self.t - sonos.t_put - true_start_s) * sr))
                seg = sonos.sig[max(0, i1 - n):max(0, i1)]
                if len(seg):
                    room[n - len(seg):] += 0.3 * seg
            _t.sleep(n / sr / 4)                                 # 4x real time: the run waits on the wall clock
            return json.dumps({"a": self.t + cal.MIC_PATH_S, "wb": base64.b64encode(
                (np.clip(room, -1, 1) * 32767).astype("<i2").tobytes()).decode()})

        def close(self):
            pass

    monkeypatch.setattr(cal, "C", C)
    c = cal.Calibrator("http://127.0.0.1:8703", connect=lambda url: FakeMic(), sonos_factory=lambda zone: sonos)
    c.start("sonos:living")
    c._thread.join(60)
    st = c.state()
    assert st["state"] == "done", st
    assert abs(L.effective_ms("sonos:living") - 620) <= 5, (L.sound("sonos:living"), st)


def test_a_lights_camera_measurement_is_stored_as_the_show_engine_reads_it(lat):
    from show_service import fixtures
    L.set_trim("screen:vj", 5.0)                                   # a sound output's entry stays as it was
    L.record_light("bar.par1", 42.4, 30.0, extra={"evidence": {"trials": 8}})
    L.record_light("bar.par1", 40.0, 28.0)                       # again: replaced
    doc = L.read()
    e = doc["outputs"]["bar.par1"]["intensity"]
    assert e["rise"] == {"t50": 40.0} and e["fall"] == {"t50": 28.0} and e["method"] == "camera"
    assert doc["outputs"]["screen:vj"]["sound"]["trim_ms"] == 5.0
    lv = fixtures.resolve_latency("bar.par1", None, doc)
    assert lv["rise_ms"] == 40.0 and lv["fall_ms"] == 28.0 and lv["source"]["rise"].startswith("latency.json")
    with pytest.raises(ValueError):
        L.record_light("screen:vj", 40.0)                          # a sound output isn't a light
    with pytest.raises(ValueError):
        L.record_light("bar.par1", -5.0)
    assert any(r["key"] == "bar.par1.intensity" and r["value_ms"] == 40.0 for r in L.overview()["lights"])


def test_the_sound_in_run_moves_the_streams_offset_by_how_late_the_beat_clock_heard_the_chirps(lat, monkeypatch):
    """A fake mic on the beat clock 30 ms late: the chirps the page plays at clicks_mono come back 30 ms late in the
    levels frames' ``k`` times, and the stream's acoustic offset becomes -30 (from 0)."""
    import base64
    import time as _t
    from routes.sound import calibrate as cal
    sr, late = cal.SR, 0.030
    monkeypatch.setattr(cal, "LEAD_S", 0.2)
    monkeypatch.setattr(C, "OFFSETS_S", tuple(x * 0.25 for x in C.OFFSETS_S))   # a quick train
    runner = {"c": None}

    class FakeWs:
        def __init__(self):
            self.t = _t.monotonic() - 0.5
            self.n = 2048

        def recv(self):
            _t.sleep(0.002)
            st = runner["c"].state()
            clicks = st.get("clicks_mono") or []
            specs = st.get("chirps") or []
            self.t += self.n / sr
            ts = self.t - (self.n - 1) / sr + np.arange(self.n) / sr
            w = np.random.default_rng(int(self.t * 1000) % 2**32).normal(0, 0.002, self.n)
            for ck, sp in zip(clicks, specs):
                c = C.chirp(sr, sp["f0"], sp["f1"], sp["dur_s"]) * 0.5
                i0 = int(round((ck + late - ts[0]) * sr))
                for j in range(len(c)):
                    if 0 <= i0 + j < self.n:
                        w[i0 + j] += c[j]
            wb = base64.b64encode((np.clip(w, -1, 1) * 32767).astype("<i2").tobytes()).decode()
            return json.dumps({"s": "laptop-mic", "a": self.t, "k": self.t, "wb": wb})

        def close(self):
            pass

    c = cal.Calibrator("http://127.0.0.1:8703", connect=lambda url: FakeWs())
    runner["c"] = c
    st = c.start("in")
    assert st["output"] == "in"
    c._thread.join(30)
    assert c.state()["state"] == "done", c.state()
    src = L.read()["sources"]["laptop-mic"]
    assert abs(src["acoustic_offset_ms"] - (-30.0)) <= 3 and src["method"] == "chirp"


def test_a_screens_camera_measurement_is_what_the_renderer_reads(lat):
    from stream_service import inputs
    L.record_screen("vj", 142.0, extra={"evidence": {"used": 8}})
    ms, src = inputs.load_photon(L.path(), "vj", "screen.vj")
    assert ms == 142.0 and src == "latency.json:vj"
    assert any(r["key"] == "vj" and r["value_ms"] == 142.0 for r in L.overview()["screens"])
    with pytest.raises(ValueError):
        L.record_screen("vj", 0.0)


def test_a_browsers_reported_latency_never_touches_the_deployments_file(lat):
    """It moves with every page load and audio device, and latency.json is tracked in git (28 Nov: the laptop's file
    flipped 68 <-> 76 ms and was never clean)."""
    before = lat.read_bytes()
    assert L.set_browser("screen:vj", 68) == 68.0 and L.set_browser("screen:vj", 76) == 76.0
    assert L.set_browser("screen:vj", 78) is None                      # under 5 ms: not written
    assert lat.read_bytes() == before
    assert L.browser_ms("screen:vj") == 76.0 and L.effective_ms("screen:vj") == 76.0
    assert L.describe("screen:vj")["source"] == "browser"
    L.set_trim("screen:vj", 10)
    assert L.effective_ms("screen:vj") == 86.0                         # the trim rides on it
    L.record("screen:vj", 82)
    assert L.effective_ms("screen:vj") == 92.0                         # a measurement wins
    assert "browser_ms" not in lat.read_text()
    with pytest.raises(ValueError):
        L.set_browser("nonsense", 50)
