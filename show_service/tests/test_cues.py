"""cues.py (README S5.22): the standing cues (an abrupt stop, a fade-out) and the forecast cues the listener arms, on
synthetic feature streams and a fake clock (10 Hz ticks). No network, no real state dir."""

from __future__ import annotations

import json

import pytest

from show_service import contract
from show_service import cues as C

BPM = 120.0                     # a beat every 0.5 s, a bar every 2 s, an 8-bar line every 16 s (1008, 1024, ...)


def clock_body(t_s: float, phrase_conf: float = 0.8) -> dict:
    return {"bpm": BPM, "anchor_ms": 0.0, "beat_at_anchor": 0, "beats_per_bar": 4, "bar_anchor_beat": 0,
            "phrase_bars": 8, "conf": 0.9, "bar_conf": 0.9, "phrase_conf": phrase_conf, "state": "locked",
            "gate": "beat", "epoch": 1, "stability": 0.8, "source": "test", "t_ms": t_s * 1000.0}


class Rig:
    def __init__(self, tmp_path, settings=None, clock=True) -> None:
        self.t = 1000.0
        self.pub: list = []
        self.level, self.lufs, self.lufs_s, self.gate = -20.0, -20.0, -20.0, 0.8
        self.low, self.onsets, self.perc, self.voice = -20.0, 2.0, 0.3, None
        self.phase, self.key, self.track = "calm", None, 3
        self.beliefs = None
        self.held = False
        self.music = True
        self.clock_on = clock
        self.phrase_conf = 0.8
        ctx = contract.BrainContext(publish=self.pub.append)
        ctx.features = lambda: {"t_ms": self.t * 1000.0, "stream": "s", "rms_dbfs": self.level, "lufs_m": self.lufs,
                                "lufs_s": self.lufs_s, "gate": self.gate}
        ctx.rhythm = lambda: {"t_ms": self.t * 1000.0, "low_db": self.low, "onset_rate": self.onsets,
                              "percussive_ratio": self.perc}
        ctx.clock = lambda: clock_body(self.t, self.phrase_conf) if self.clock_on else None
        ctx.music_context = lambda: {"t_ms": self.t * 1000.0, "section": {"phase": self.phase}, "key": self.key,
                                     "mode": "major", "key_strength": 0.6 if self.key else 0.0,
                                     "track": {"id": self.track}}
        ctx.beliefs = lambda: self.beliefs
        ctx.situations = lambda: {"music_audible": {"value": self.music}}
        ctx.held = lambda: self.held
        ctx.ml = lambda: ({"tags": {"t": self.t, "voice_p": self.voice}} if self.voice is not None else None)
        self.ctx = ctx
        self.log_path = tmp_path / "cues.jsonl"
        self.c = C.Cues(ctx, mono=lambda: self.t, wall=lambda: 1.79e9 + self.t, log_path=self.log_path)
        self.c.configure(settings or {})

    def run(self, seconds: float, each=None) -> None:
        for _ in range(int(round(seconds * 10))):
            self.t = round(self.t + 0.1, 6)
            if each:
                each(self)
            self.c.tick(self.t)

    def events(self, kind=None) -> list[dict]:
        return [m["data"] for m in self.pub if m["type"] == contract.ROOM_EVENT
                and (kind is None or m["data"]["kind"] == kind)]

    def kinds(self, n: int = 50) -> list[str]:
        return [e["kind"] for e in self.c.log(n)]

    def learn_floor(self, level: float = -65.0, seconds: float = 6.0) -> None:
        self.level, self.gate = level, 0.0
        self.run(seconds)
        self.level, self.gate = -20.0, 0.8

    def forecast(self, kind="chorus", lo=5.0, hi=15.0, signs=("louder",), size="big", conf=0.5, fid="v1:0",
                 ending=None) -> None:
        self.beliefs = {"state": "fresh", "ending": ending,
                        "coming": [{"id": fid, "kind": kind, "t0_s": self.t + lo, "t1_s": self.t + hi,
                                    "in_s": [lo, hi], "signs": list(signs), "size": size, "conf": conf}]}


# -- standing cues ---------------------------------------------------------------------------------------------------
def test_an_abrupt_stop_goes_dark_at_once_and_comes_back_with_the_sound(tmp_path):
    r = Rig(tmp_path)
    r.learn_floor()
    assert r.c.floor == pytest.approx(-65.0)
    r.run(5.0)                                                          # music at -20 dBFS
    assert [x["kind"] for x in r.c.sheet()] == ["stop", "fade"]         # both ready while nothing says how it ends
    assert r.c.sheet()[0]["words"] == "Ready: snap to dark if the music stops"
    r.level = -64.0                                                     # it stops dead
    r.run(0.3)
    ends = r.events("track_end")
    assert len(ends) == 1, ends
    e = ends[0]
    assert e["id"] == "track_end:3" and e["detail"] == "stop" and e["via"] == "cues" and e["track_id"] == 3
    assert e["transition"] == {"style": "fade-dark", "out_s": 0.2, "in_s": 1.0, "seconds": None, "on": "now",
                               "change": "big", "dark_max_s": 8.0}
    assert e["at_ms"] < r.t * 1000.0 and "bar" in e and "beat" in e
    assert r.c.sheet() == [{"kind": "stop", "words": "Dark: the music stopped; back if it returns", "window_s": None,
                            "size": None}]
    r.run(1.0)
    assert len(r.events("track_end")) == 1                              # one dark, not one a tick
    r.level = -21.0                                                     # the sound comes back
    r.run(0.3)
    back = r.events("track_end")[-1]
    assert back["detail"] == "sound_back" and back["id"] == "track_end:3" and "transition" not in back
    assert r.kinds()[-2:] == ["fire", "sound_back"]
    lines = [json.loads(x) for x in r.log_path.read_text().splitlines()]
    assert [x["kind"] for x in lines][-2:] == ["fire", "sound_back"] and lines[-2]["cue"] == "stop"


def test_a_slide_down_or_a_dip_is_no_stop(tmp_path):
    r = Rig(tmp_path)
    r.learn_floor()
    r.run(5.0)

    def slide(rr):
        rr.level = max(-64.0, rr.level - 4.0)                           # 4 dB a tick: 10 dB takes 0.25 s, 20 dB 0.5 s
    r.run(2.0, slide)
    assert r.events("track_end") == []
    r.level = -20.0
    r.run(4.0)
    r.level = -30.0                                                     # 10 dB down: a quiet bar, not a stop
    r.run(1.0)
    assert r.events("track_end") == []


def test_no_stop_without_music_while_held_or_when_it_segues(tmp_path):
    r = Rig(tmp_path)
    r.learn_floor()
    r.music = False                                                     # what sounds isn't music (talk)
    r.run(4.0)
    r.level = -64.0
    r.run(0.5)
    assert r.events() == [] and r.c.sheet() == []
    r.music, r.level = True, -20.0
    r.run(4.0)
    r.held = True                                                       # Keep this look
    r.level = -64.0
    r.run(0.5)
    assert r.events() == [] and "held" in r.kinds()
    r.held, r.level = False, -20.0
    r.run(4.0)
    r.beliefs = {"state": "fresh", "ending": "segue", "coming": []}     # the listener: it runs into the next song
    r.run(0.1)
    assert [x["kind"] for x in r.c.sheet()] == ["segue"]
    assert r.c.sheet()[0]["words"] == "Ready: straight on into the next song"
    r.level = -64.0
    r.run(0.5)
    assert r.events() == []
    r.beliefs = {"state": "fresh", "ending": "cold", "coming": []}      # cold: the stop only
    r.level = -20.0
    r.run(4.0)
    assert [x["kind"] for x in r.c.sheet()] == ["stop"]
    r.level = -64.0
    r.run(0.4)
    assert [e["detail"] for e in r.events("track_end")] == ["stop"]


def test_structures_own_end_takes_over_the_sound_back(tmp_path):
    r = Rig(tmp_path)
    r.learn_floor()
    r.run(5.0)
    r.level = -64.0
    r.run(3.5)
    assert len(r.events("track_end")) == 1
    r.c.on_music_event({"kind": "track_end", "id": "track_end:3", "at_ms": (r.t - 3.4) * 1000.0, "via": "dsp",
                        "track_id": 3})                                 # structure's gap-based end, the same id
    r.level = -20.0
    r.run(1.0)
    assert [e["detail"] for e in r.events("track_end")] == ["stop"]     # structure sends its own sound_back
    assert "handed" in r.kinds() and r.c.snapshot()["counts"]["handed"] == 1


def test_a_fade_out_goes_dark_over_its_time_to_the_floor(tmp_path):
    r = Rig(tmp_path)
    r.learn_floor()
    r.run(5.0)
    t0 = r.t

    def fall(rr):
        rr.lufs_s = -20.0 - 3.0 * (rr.t - t0)                           # 3 dB a second
        rr.lufs = rr.lufs_s
        rr.level = rr.lufs_s
        if rr.t - t0 >= 5.0:
            rr.phase = "outro"                                          # structure's outro latch
    r.run(5.3, fall)
    ends = r.events("track_end")
    assert len(ends) == 1 and ends[0]["detail"] == "fade"
    # at about -35.5 LUFS, 3 dB/s down to the floor + 6 (-59): about 7.8 s
    assert 7.0 <= ends[0]["transition"]["out_s"] <= 8.5 and ends[0]["transition"]["in_s"] == 2.0
    assert r.c.sheet()[0]["words"] == "Fading out with the music; back if it comes up"
    r.phase = "calm"
    r.lufs_s = r.lufs = r.level = -30.0                                 # it comes back up
    r.run(1.3)
    assert r.events("track_end")[-1]["detail"] == "sound_back"


def test_a_cold_ending_never_fades(tmp_path):
    r = Rig(tmp_path)
    r.learn_floor()
    r.beliefs = {"state": "fresh", "ending": "cold", "coming": []}
    r.run(5.0)
    r.phase = "outro"
    r.run(2.0)
    assert r.events() == []


# -- signs -----------------------------------------------------------------------------------------------------------
def test_the_sign_detectors(tmp_path):
    r = Rig(tmp_path)
    r.run(5.0)
    assert r.c.snapshot()["signs"] == {}
    r.lufs = -14.0                                                      # 6 dB louder
    r.low = -12.0                                                       # the bass comes in, 8 dB
    r.run(0.4)
    assert {"louder", "low_in"} <= set(r.c.snapshot()["signs"])
    r.lufs, r.low = -20.0, -20.0
    r.run(5.0)
    r.onsets = 3.5                                                      # busier
    r.perc = 0.6                                                        # drums in
    r.run(0.5)
    assert {"busier", "drums_in"} <= set(r.c.snapshot()["signs"])
    r.run(5.0)
    r.perc = 0.1                                                        # drums out
    r.run(1.0)
    assert "drums_out" in r.c.snapshot()["signs"]
    r.voice = 0.2
    r.run(2.0)
    r.voice = 0.8                                                       # a voice comes in
    r.run(0.2)
    assert "vocal_in" in r.c.snapshot()["signs"]
    r.run(7.0)
    r.voice = 0.2
    r.run(0.2)
    assert "vocal_out" in r.c.snapshot()["signs"]
    r.level = -40.0                                                     # a 20 dB hole for half a second...
    r.run(0.5)
    r.level = -20.0                                                     # ...and back: a stop-and-return
    r.run(0.2)
    assert "stop_return" in r.c.snapshot()["signs"]
    r.run(5.0)
    r.c._sign_t.pop("stop_return")
    r.level = -40.0                                                     # a long hole is no stop-and-return
    r.run(3.5)
    assert "stop_return" not in r.c.snapshot()["signs"]
    r.level = -20.0
    r.run(0.5)
    assert "stop_return" not in r.c.snapshot()["signs"]
    r.c.on_music_event({"kind": "section_change", "at_ms": r.t * 1000.0, "section_id": 4, "predicted": False})
    r.run(0.1)
    assert r.c.snapshot()["signs"]["timbre_change"] <= 0.2


# -- forecasts -------------------------------------------------------------------------------------------------------
def test_a_forecast_fires_on_the_downbeat_after_its_sign(tmp_path):
    r = Rig(tmp_path)
    r.run(3.0)
    r.forecast(lo=5.0, hi=15.0, signs=("louder",), size="big", conf=0.5)
    r.run(0.1)
    sheet = [x for x in r.c.sheet() if x["kind"] == "chorus"]
    assert sheet == [{"kind": "chorus", "words": "Big change at the chorus, likely in 5-15 s", "window_s": [4.9, 14.9],
                      "size": "big"}]
    r.lufs = -14.0                                                      # louder, but before the window (- slack)
    r.run(0.5)
    r.lufs = -20.0
    r.run(3.0)
    assert r.events("drop") == []
    r.run(3.0)
    r.lufs = -14.0                                                      # inside the window now
    r.run(0.3)
    drops = r.events("drop")
    assert len(drops) == 1
    d = drops[0]
    assert d["id"] == "cue:chorus:1" and d["detail"] == "forecast: chorus" and d["via"] == "cues"
    assert d["at_ms"] % 2000.0 == pytest.approx(0.0, abs=1.0) and d["at_ms"] >= (r.t + 1.1) * 1000.0 - 1.0
    assert d["at_ms"] <= (r.t + 1.1 + 2.0) * 1000.0                     # the next downbeat after the lead
    assert d["cue"]["sign"] == "louder" and d["cue"]["size"] == "big"
    assert [x for x in r.c.sheet() if x["kind"] == "chorus"] == []      # fired: off the sheet
    assert {"arm", "fire"} <= set(r.kinds())


def test_a_confident_forecast_with_a_build_lands_on_the_phrase_line(tmp_path):
    r = Rig(tmp_path)
    r.run(3.0)                                                          # t = 1003; the next 8-bar line is 1008
    r.phrase_conf = 0.3                                                 # (8-bar lines: not sure of 16)
    r.forecast(lo=10.0, hi=30.0, signs=("drums_in",), size="big", conf=0.8)   # window 1013..1033: line 1024
    r.run(0.1)
    assert r.c.snapshot()["cues"][0]["scheduled_in_s"] is None          # no early sign yet
    r.phase = "build"
    r.run(0.1)
    snap = r.c.snapshot()["cues"][0]
    assert snap["scheduled_in_s"] == pytest.approx(1024.0 - r.t, abs=0.01)
    assert r.c.sheet()[0]["words"].startswith("Big change at the chorus, on the phrase line in ")
    r.run_to = None
    while r.t < 1021.9:
        r.run(0.1)
    assert r.events("drop") == []
    r.run(0.2)                                                          # one bar (2 s) ahead of the line
    drops = r.events("drop")
    assert len(drops) == 1 and drops[0]["at_ms"] == pytest.approx(1024000.0)
    assert drops[0]["cue"]["sign"].startswith("the phrase line")


def test_medium_is_a_section_change_and_small_is_logged_only(tmp_path):
    r = Rig(tmp_path)
    r.run(3.0)
    r.forecast(kind="band_in", lo=0.0, hi=10.0, signs=("drums_in",), size="medium")
    r.run(0.1)
    assert r.c.sheet()[0]["words"] == "Change when the band comes in, any moment now"
    r.perc = 0.7
    r.run(0.5)
    sc = r.events("section_change")
    assert len(sc) == 1 and sc[0]["detail"] == "forecast: band_in" and sc[0]["id"] == "cue:band_in:1"
    r.perc = 0.3
    r.run(5.0)
    r.forecast(kind="vocal_in", lo=0.0, hi=10.0, signs=("louder",), size="small", fid="v2:0")
    r.run(0.1)
    assert r.c.sheet()[0]["words"].startswith("Watching for the singing coming in")
    r.lufs = -14.0
    r.run(0.3)
    assert len(r.events()) == 1                                         # nothing published for a small one
    fire = [e for e in r.c.log(20) if e["kind"] == "fire" and e["cue"] == "vocal_in"]
    assert fire and fire[0]["published"] is False and r.c.snapshot()["counts"]["logged_only"] == 1


def test_a_passed_window_is_missed_and_its_outcome_says_what_structure_heard(tmp_path):
    r = Rig(tmp_path)
    r.run(3.0)
    r.forecast(kind="drop", lo=2.0, hi=6.0, signs=("low_in",), size="big")
    r.run(4.0)
    r.c.on_music_event({"kind": "drop", "at_ms": r.t * 1000.0, "section_id": 9, "id": "drop:3:9"})   # unseen sign
    r.run(5.0)
    assert r.events("drop") == [] and "missed" in r.kinds()
    r.run(2.0)
    out = [e for e in r.c.log(50) if e["kind"] == "outcome"]
    assert len(out) == 1 and out[0]["result"] == "missed" and out[0]["structure_saw"] == ["drop"]


def test_one_per_kind_a_newer_one_replaces_it_and_a_new_song_disarms(tmp_path):
    r = Rig(tmp_path)
    r.run(3.0)
    r.forecast(kind="chorus", lo=10.0, hi=20.0, fid="v1:0")
    r.run(0.1)
    r.forecast(kind="chorus", lo=8.0, hi=12.0, fid="v2:0")
    r.run(0.1)
    cues = r.c.snapshot()["cues"]
    assert len(cues) == 1 and cues[0]["id"] == "cue:chorus:2" and "replaced" in r.kinds()
    r.run(0.1)                                                          # the same forecast again: not re-armed
    assert r.c.snapshot()["counts"]["armed"] == 2
    r.c.on_music_event({"kind": "track_change", "at_ms": r.t * 1000.0, "track_id": 4, "id": "track_change:4"})
    assert r.c.snapshot()["cues"] == [] or r.c._armed == {}
    assert "disarmed" in r.kinds()


def test_nothing_fires_while_a_person_holds_the_look(tmp_path):
    r = Rig(tmp_path)
    r.run(3.0)
    r.forecast(lo=0.0, hi=10.0, signs=("louder",))
    r.held = True
    r.lufs = -14.0
    r.run(0.5)
    assert r.events("drop") == [] and "held" in r.kinds()
    r.held = False
    r.run(0.1)
    assert len(r.events("drop")) == 1                                   # still in the window, still seen


def test_at_most_one_fire_a_bar(tmp_path):
    r = Rig(tmp_path)
    r.run(3.0)
    r.beliefs = {"state": "fresh", "ending": None, "coming": [
        {"id": "v1:0", "kind": "chorus", "t0_s": r.t, "t1_s": r.t + 10, "signs": ["louder"], "size": "big",
         "conf": 0.5},
        {"id": "v1:1", "kind": "breakdown", "t0_s": r.t, "t1_s": r.t + 10, "signs": ["louder"], "size": "medium",
         "conf": 0.5}]}
    r.run(0.1)
    r.lufs = -14.0
    r.run(0.3)
    ats = sorted(e["at_ms"] for e in r.events() if e["kind"] in ("drop", "section_change"))
    assert len(ats) == 2 and ats[1] - ats[0] == pytest.approx(2000.0)    # the second one, a bar later


def test_a_phrase_line_sign_fires_at_the_line(tmp_path):
    r = Rig(tmp_path)
    r.run(3.0)                                                          # t = 1003
    r.forecast(kind="new_section", lo=0.0, hi=12.0, signs=("phrase_line",), size="medium", conf=0.4)
    r.run(0.5)
    assert r.events() == []                                             # 1008 is more than the lead + a bar away
    while not r.events():
        r.run(0.1)
    assert r.events()[0]["at_ms"] == pytest.approx(1008000.0) and r.t == pytest.approx(1004.0)


def test_a_key_change_counts_against_the_key_at_arm_time(tmp_path):
    r = Rig(tmp_path)
    r.key = "C"
    r.run(3.0)
    r.forecast(kind="key_lift", lo=0.0, hi=10.0, signs=("key_change",))
    r.run(1.0)
    assert r.events() == []
    r.key = "D"
    r.run(0.1)
    assert len(r.events("drop")) == 1 and r.events("drop")[0]["cue"]["sign"] == "key_change"


def test_settings_snapshot_and_wiring(tmp_path):
    r = Rig(tmp_path, settings={"cues": {"stop": {"collapse_db": -3, "quiet_ticks": 0, "quiet_dbfs": -50},
                                         "forecast": "no"}})
    assert r.c.cfg["stop"]["collapse_db"] == 20.0 and r.c.cfg["stop"]["quiet_ticks"] == 2
    assert r.c.cfg["stop"]["quiet_dbfs"] == -50.0 and len(r.c.problems) == 3
    r.run(2.0)
    snap = r.c.snapshot()
    assert set(snap) >= {"armed", "cues", "standing", "level", "signs", "counts", "log", "problems"}
    json.dumps(snap)
    c = C.attach(contract.BrainContext(publish=lambda m: None), log_path=None)
    assert c.name == "cues" and c.tick_hz == contract.BRAIN_TICK_HZ
    assert set(c.provides()) == {"cue_sheet", "on_music_event"}
    c.tick(1.0)                                                         # nothing wired: it degrades, never raises
    assert c.sheet() == []
    for line in r.c.sheet():
        assert set(line) == set(contract.CueLine.__annotations__)


def test_the_real_show_yaml_has_every_cues_key(tmp_path):
    import yaml
    sh = yaml.safe_load(contract.SHOW_FILE.read_text())
    r = Rig(tmp_path, settings=sh)
    assert r.c.problems == []
    for sec, keys in C.CUES_FALLBACK.items():
        assert set(sh["cues"][sec]) == set(keys), sec
