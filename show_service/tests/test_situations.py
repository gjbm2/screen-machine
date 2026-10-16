"""situations.py: NoteTap and the condition evaluator are shared plumbing (real tests); the vocabulary is tested at the
"does it work" level against fake snapshots. No sockets, no devices."""

from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest

from show_service import contract as c
from show_service.situations import (WORDS, Conditions, NoteTap, Situations, check_param, in_window, kleene_all,
                                     kleene_any, word_label)
from show_service.vban import MidiBatch, MidiState


def batch(seq: int, pitches=(60,), *, sess: str | None = "s1", ch: int = 1, vel: int = 80, off: bool = False):
    status = (0x80 if off else 0x90) | (ch - 1)
    return MidiBatch(seq=seq, ev=[(0, status, p, vel) for p in pitches], prev=[], sess=sess)


def to_host(t_ns: int) -> float:
    return t_ns / 1e9


# -- NoteTap ------------------------------------------------------------------------------------------------------------
def test_notetap_dedupes_by_wire_seq():
    tap = NoteTap()
    tap.on_midi("room-piano", batch(1, (60,)), 1.0, to_host)
    tap.on_midi("room-piano", batch(2, (62,)), 2.0, to_host)
    tap.on_midi("room-piano", batch(2, (62,)), 2.1, to_host)        # duplicate: old
    tap.on_midi("room-piano", batch(1, (64,)), 2.2, to_host)        # straggler: old
    tap.on_midi("room-piano", batch(1, (65,), sess="s2"), 3.0, to_host)   # a new wire session: restart, taken
    tap.on_midi("other", batch(1, (67,)), 3.5, to_host)             # per stream: its own seq space
    got = [(n["stream"], n["pitch"]) for n in tap.since(0.0)]
    assert got == [("room-piano", 60), ("room-piano", 62), ("room-piano", 65), ("other", 67)]
    s = tap.stats()
    assert s["old"] == 2 and s["restarts"] == 3 and s["batches"] == 4 and s["notes"] == 4
    assert s["streams"] == {"room-piano": 3, "other": 1}


def test_notetap_takes_only_note_ons_and_keeps_the_channel():
    tap = NoteTap()
    tap.on_midi("p", batch(1, (60,), off=True), 1.0, to_host)        # note-off
    tap.on_midi("p", batch(2, (61,), vel=0), 1.1, to_host)           # note-on with velocity 0 = off
    tap.on_midi("p", MidiState(seq=3, keys=[0] * 88, cc64=0, cc66=0, cc67=0, t_ns=0, sess="s1"), 1.2, to_host)
    tap.on_midi("p", batch(3, (62,), ch=2), 1.3, to_host)
    notes = tap.since(0.0)
    assert notes == [{"t_s": 1.3, "stream": "p", "channel": 2, "pitch": 62, "vel": 80}]
    assert tap.last_note_times() == {("p", 2): 1.3}


def test_notetap_since_filters_and_the_ring_keeps_the_newest():
    tap = NoteTap(size=16)
    for i in range(40):
        tap.on_midi("p", batch(i + 1, (21 + i,)), float(i), to_host)
    got = tap.since(0.0)
    assert len(got) <= 16 and [n["t_s"] for n in got] == sorted(n["t_s"] for n in got)
    assert got[-1]["t_s"] == 39.0 and got[0]["t_s"] >= 40 - 16
    assert [n["t_s"] for n in tap.since(35.0)] == [35.0, 36.0, 37.0, 38.0, 39.0]


def test_notetap_reader_never_sees_a_torn_ring_while_show_rx_writes():
    tap = NoteTap(size=64)
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set() and i < 200_000:
            i += 1
            tap.on_midi("p", batch(i, (60 + i % 12,)), float(i), to_host)

    th = threading.Thread(target=writer)
    th.start()
    try:
        reads = 0
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            got = [n["t_s"] for n in tap.since(0.0)]
            assert got == sorted(got) and len(set(got)) == len(got) and len(got) <= 64
            reads += 1
        assert reads > 10
    finally:
        stop.set()
        th.join()


def test_notetap_cost_per_batch():
    tap = NoteTap()
    batches = [batch(i + 1, (60, 64, 67, 72)) for i in range(5000)]
    t0 = time.perf_counter()
    for i, b in enumerate(batches):
        tap.on_midi("room-piano", b, float(i), to_host)
    us = (time.perf_counter() - t0) / len(batches) * 1e6
    print(f"\nNoteTap.on_midi: {us:.2f} µs per 4-note batch (budget 20 µs p95)")
    assert us < 200                                  # loose: a loaded, niced host; the print is the measurement


# -- the vocabulary -----------------------------------------------------------------------------------------------------
SETTINGS = {"situations": {
    "midi": {"streams": ["room-piano"], "channels": [1], "heard_by": "room-mic", "window_s": 10, "min_notes": 12,
              "min_span_s": 6, "min_pitches": 4, "heard_min_lufs": -45},
    "music": {"stream": "room-mic", "min_prob": 0.5, "dsp_min_gate": 0.3},
    "sonos": {"now_playing": "sonos.zone.now_playing", "volume": "sonos.zone.volume", "own_change_s": 60},
    "quiet": {"stream": "room-mic", "gate_below": 0.05}}}


class Env:
    def __init__(self) -> None:
        self.now = 1000.0
        self.health = {"active_audio": "room-mic", "streams": {
            "room-piano": {"kind": "midi", "state": "streaming", "test": False},
            "room-mic": {"kind": "audio", "state": "streaming", "test": False},
            "test-piano": {"kind": "midi", "state": "streaming", "test": True}}}
        self.features = None
        self.clock = None
        self.harmony = None
        self.piano = None
        self.room = None
        self.beliefs = None
        self.local = datetime(2026, 10, 16, 12, 0)
        self.tap = NoteTap()
        self.seq: dict[str, int] = {}
        ctx = c.BrainContext(publish=lambda m: None, features=lambda: self.features, clock=lambda: self.clock,
                             harmony=lambda: self.harmony, piano=lambda: self.piano, health=lambda: self.health,
                             notes=self.tap)
        ctx.room = lambda: self.room
        ctx.beliefs = lambda: self.beliefs
        self.sit = Situations(ctx, local=lambda: self.local)
        self.sit.configure(SETTINGS)

    def note(self, t: float, pitch: int, stream: str = "room-piano", ch: int = 1) -> None:
        self.seq[stream] = self.seq.get(stream, 0) + 1
        self.tap.on_midi(stream, batch(self.seq[stream], (pitch,), ch=ch), t, to_host)

    def play(self, n: int, dt: float, **kw) -> None:
        for i in range(n):
            self.note(self.now - (n - 1 - i) * dt, 60 + i % 5, **kw)

    def feat(self, **kw) -> None:
        self.features = {"t_ms": self.now * 1000.0, "stream": "room-mic", "gate": 0.0, "lufs_s": -70.0,
                         "energy": 0.0, **kw}

    def read(self, sid: str, params: dict | None = None):
        self.now += 0.001                                # time moves between reads (readings are memoised per now_s)
        return self.sit.read(sid, params, self.now)


def sonos_room(state="PLAYING", mute=False, volume=20, sent_age=None, reachable=True):
    return {"reachable": reachable, "members": {
        "sonos.zone.now_playing": {"believed": {"state": state.lower(), "mute": mute}, "confidence": "confirmed",
                                   "age_s": 1.0, "sent_age_s": sent_age},
        "sonos.zone.volume": {"believed": volume, "confidence": "confirmed", "age_s": 1.0, "sent_age_s": sent_age}}}


def test_piano_playing_thresholds():
    e = Env()
    e.play(12, 0.45)                                 # 12 notes over 4.95 s: too short a span
    assert e.read("piano_playing")["value"] is False
    e.now += 2.0
    e.play(4, 0.5)                                   # 16 notes in the window now, spanning 6.95 s over 5 pitches
    r = e.read("piano_playing")
    assert r["value"] is True and r["via"] == "midi" and r["evidence"]["notes"] >= 12
    assert e.read("piano_playing", {"min_pitches": 6})["value"] is False   # a rule's own params win


def test_piano_playing_ignores_other_channels_and_test_streams_unless_asked():
    e = Env()
    e.play(20, 0.5, ch=2)                            # the adsilent's own playback
    e.play(20, 0.5, stream="test-piano")
    assert e.read("piano_playing")["value"] is False
    assert e.read("piano_playing", {"channels": [2]})["value"] is True
    assert e.read("piano_playing", {"channels": None, "streams": None, "include_test": True})["value"] is True


def test_piano_playing_must_be_heard_by_the_mic_while_it_is_up():
    e = Env()
    e.play(20, 0.5)
    e.feat(lufs_s=-60.0)                             # the mic is up and measured: silent headphone practice
    r = e.read("piano_playing")
    assert r["value"] is False and r["via"] == "midi+mic" and r["evidence"]["heard_lufs"] == -60.0
    e.feat(lufs_s=-30.0)
    assert e.read("piano_playing")["value"] is True
    e.health["streams"]["room-mic"]["state"] = "down"      # the mic is down: MIDI alone decides
    e.feat(lufs_s=-60.0)
    assert e.read("piano_playing")["value"] is True


def test_piano_words_are_unknown_with_no_stream_up():
    e = Env()
    e.health["streams"]["room-piano"]["state"] = "down"
    assert e.read("piano_playing")["value"] is None
    assert e.read("piano_idle")["value"] is None


def test_piano_idle():
    e = Env()
    e.note(e.now - 0.5, 60)
    assert e.read("piano_idle")["value"] is False
    e.now += 2
    e.piano = {"present": True, "stream": "room-piano", "source": "live", "keys_down": 1, "damper": 0.0,
               "last_note_age_s": 2.0, "velocity": 0.5, "notes_per_s": 0.0}
    assert e.read("piano_idle")["value"] is False    # a key still held
    e.piano = {**e.piano, "keys_down": 0, "damper": 0.8}
    assert e.read("piano_idle")["value"] is False    # the damper is down
    e.piano = {**e.piano, "damper": 0.0}
    r = e.read("piano_idle")
    assert r["value"] is True and r["evidence"]["last_note_age_s"] == 2.5


def test_music_audible():
    e = Env()
    e.feat(gate=0.6)
    e.clock = {"gate": "beat"}
    assert e.read("music_audible")["value"] is True
    e.clock = {"gate": "none"}
    e.harmony = {"conf": 0.5}                        # a clear key counts too
    assert e.read("music_audible")["value"] is True
    e.harmony = {"conf": 0.1}
    assert e.read("music_audible")["value"] is False
    e.room = sonos_room("PLAYING")
    r = e.read("music_audible")                      # Sonos plays and the mic agrees
    assert r["value"] is True and r["via"] == "sonos+mic"
    e.feat(gate=0.1)
    assert e.read("music_audible")["value"] is False   # Sonos says playing; the mic doesn't hear it


def test_music_audible_unknown_when_the_mic_is_down_and_sonos_unknown():
    e = Env()
    e.health["streams"]["room-mic"]["state"] = "down"
    e.health["active_audio"] = None
    assert e.read("music_audible")["value"] is None
    e.room = sonos_room("STOPPED")
    assert e.read("music_audible")["value"] is False
    e.room = sonos_room("PLAYING", sent_age=5.0)     # the room just changed Sonos itself: ignored
    e.health["streams"]["room-mic"]["state"] = "streaming"
    e.feat(gate=0.6)
    r = e.read("music_audible")
    assert r["value"] is None and "sent_age_s" in r["evidence"]


def test_sonos_playing():
    e = Env()
    assert e.read("sonos_playing")["value"] is None          # no room view yet
    e.room = sonos_room("PLAYING")
    assert e.read("sonos_playing")["value"] is True
    e.room = sonos_room("TRANSITIONING")
    assert e.read("sonos_playing")["value"] is True
    e.room = sonos_room("PLAYING", mute=True)
    assert e.read("sonos_playing")["value"] is False
    e.room = sonos_room("PLAYING", volume=0)
    assert e.read("sonos_playing")["value"] is False
    e.room = sonos_room("PAUSED_PLAYBACK")
    assert e.read("sonos_playing")["value"] is False
    e.room = sonos_room("PLAYING", sent_age=10.0)
    assert e.read("sonos_playing")["value"] is None
    e.room = sonos_room("PLAYING", reachable=False)
    assert e.read("sonos_playing")["value"] is None
    e.room = sonos_room("PLAYING")
    assert e.read("sonos_playing", {"now_playing": None})["value"] is None   # not configured


def test_quiet_energy_talking_genre_between():
    e = Env()
    e.feat(gate=0.01)
    assert e.read("quiet")["value"] is True
    e.feat(gate=0.2)
    assert e.read("quiet")["value"] is False
    e.features = None
    assert e.read("quiet")["value"] is True                  # up, but nothing measured: quiet
    e.health["streams"]["room-mic"]["state"] = "down"
    assert e.read("quiet")["value"] is None

    e = Env()
    for _ in range(30):
        e.now += 1
        e.feat(energy=0.8)
        e.sit.tick(e.now)
    assert e.read("energy_above", {"value": 0.6})["value"] is True
    assert e.read("energy_above", {"value": 0.9})["value"] is False
    e.now += 10
    assert e.read("energy_above", {"value": 0.6})["value"] is None   # stale features

    assert e.read("talking")["value"] is None                # no classifier until P1

    e.beliefs = {"state": "fresh", "genre": {"word": "jazz", "conf": 0.8}}
    assert e.read("genre_in", {"genres": ["jazz", "soul"]})["value"] is True
    e.beliefs = {"state": "holding", "genre": {"word": "techno", "conf": 0.8}}
    assert e.read("genre_in", {"genres": ["jazz", "soul"]})["value"] is False
    e.beliefs = {"state": "fresh", "genre": {"word": "jazz", "conf": 0.4}}
    assert e.read("genre_in", {"genres": ["jazz"]})["value"] is None
    e.beliefs = {"state": "none", "genre": None}
    assert e.read("genre_in", {"genres": ["jazz"]})["value"] is None

    e.local = datetime(2026, 10, 16, 23, 30)
    assert e.read("between", {"from": "22:00", "to": "02:00"})["value"] is True
    e.local = datetime(2026, 10, 16, 3, 0)
    assert e.read("between", {"from": "22:00", "to": "02:00"})["value"] is False
    e.local = datetime(2026, 10, 16, 12, 0)
    assert e.read("between", {"from": "09:00", "to": "17:00"})["value"] is True
    assert in_window(600, 600, 600) is False


def test_audio_and_listener_words_ignore_a_test_stream_unless_asked():
    """Test audio played "from another room" is the measured stream (rank_first): the audio and listener words read
    unknown on it, like the piano words, unless a rule sets include_test."""
    e = Env()
    e.health["active_audio"] = "test-audio"
    e.health["streams"]["test-audio"] = {"kind": "audio", "state": "streaming", "test": True}
    for _ in range(30):
        e.now += 1
        e.feat(energy=0.8, gate=0.9, stream="test-audio")
        e.sit.tick(e.now)
    r = e.read("energy_above", {"value": 0.6})
    assert r["value"] is None and "test stream" in r["evidence"]["why"]
    assert e.read("energy_above", {"value": 0.6, "include_test": True})["value"] is True
    e.beliefs = {"state": "fresh", "genre": {"word": "jazz", "conf": 0.9}}
    assert e.read("genre_in", {"genres": ["jazz"]})["value"] is None
    assert e.read("genre_in", {"genres": ["jazz"], "include_test": True})["value"] is True
    e.clock = {"gate": "beat"}
    assert e.read("music_audible", {"stream": None})["value"] is None
    assert e.read("music_audible", {"stream": None, "include_test": True})["value"] is True
    assert e.read("quiet", {"stream": None})["value"] is None
    e.health["active_audio"] = "room-mic"                    # the room's own mic again: counted
    e.feat(energy=0.8, gate=0.9)
    assert e.read("genre_in", {"genres": ["jazz"]})["value"] is True


def test_tick_snapshot_and_vocabulary():
    e = Env()
    e.sit.tick(e.now)
    snap = e.sit.snapshot()
    assert set(snap["readings"]) == set(c.SITUATIONS)
    assert snap["readings"]["genre_in"]["value"] is None and "needs" in snap["readings"]["genre_in"]["evidence"]["why"]
    voc = {d["id"]: d for d in snap["vocabulary"]}
    assert voc["piano_playing"]["rank"] == 0 and voc["sonos_playing"]["rank"] == 1 and voc["between"]["rank"] == 4
    assert voc["piano_playing"]["params"]["min_notes"]["default"] == 12
    assert voc["piano_playing"]["params"]["min_notes"]["x-ui"]["source"] == "show.yaml situations.midi"
    assert voc["talking"]["available"] is False
    e.sit.configure({})                                      # no show.yaml: design defaults, marked as such
    voc = {d["id"]: d for d in e.sit.vocabulary()}
    assert voc["piano_playing"]["params"]["min_notes"]["x-ui"]["source"].startswith("design default")
    assert voc["sonos_playing"]["params"]["now_playing"]["default"] is None      # never an invented device
    assert e.sit.provides()["situations"]() is snap["readings"]
    assert word_label("genre_in", {"genres": ["jazz", "soul"]}) == "the music is jazz or soul"


def test_reading_since_tracks_how_long_it_has_held():
    e = Env()
    e.feat(gate=0.01)
    assert e.read("quiet")["since_s"] == 0.0
    e.now += 4
    e.feat(gate=0.01)
    assert e.read("quiet")["since_s"] == 4.0
    e.feat(gate=0.2)
    assert e.read("quiet")["since_s"] == 0.0


# -- the condition grammar (shared plumbing) ----------------------------------------------------------------------------
class Stub:
    """A Situations stand-in: each word reads what ``values`` says."""
    revision = 0

    def __init__(self) -> None:
        self.values: dict[str, bool | None] = {}

    def params(self, sid, override=None):
        return dict(override or {})

    def read(self, sid, params, now_s):
        return {"value": self.values.get(sid), "since_s": None, "via": "stub", "evidence": {}}


def test_kleene():
    T, F, U = True, False, None
    assert kleene_all([T, T]) is T and kleene_all([T, U]) is U and kleene_all([U, F]) is F
    assert kleene_any([F, F]) is F and kleene_any([F, U]) is U and kleene_any([U, T]) is T


def test_for_s_and_resets():
    s = Stub()
    cond = Conditions(s)
    rule = {"quiet": {"for_s": 3}}
    s.values["quiet"] = True
    assert [cond.holds(rule, "r", t)[0] for t in (0, 1, 2, 3, 4)] == [False, False, False, True, True]
    held, info = cond.holds(rule, "r", 5)
    assert info["value"] is True and info["held_s"] == 5 and info["needs_s"] == 3
    s.values["quiet"] = None                                  # unknown resets the timer and never holds
    assert cond.holds(rule, "r", 6) == (False, {"value": None, "held_s": 0.0, "needs_s": 3.0,
                                                 "words": {"quiet": s.read("quiet", {}, 6)}})
    s.values["quiet"] = True
    assert not cond.holds(rule, "r", 7)[0] and not cond.holds(rule, "r", 9)[0] and cond.holds(rule, "r", 10)[0]
    assert not cond.holds(rule, "r", 20)[0]                    # a gap in evaluation (> 3 s) starts again
    assert cond.holds(rule, "r", 23)[0]


def test_combinators_with_nested_for_s():
    s = Stub()
    cond = Conditions(s)
    rule = {"all": [{"piano_idle": {"for_s": 2}}, {"not": {"music_audible": {}}}], "for_s": 3}
    s.values.update(piano_idle=True, music_audible=False)
    # piano_idle holds from t=2; the all from t=2, so it holds from t=5
    assert [cond.holds(rule, "hold", t)[0] for t in range(7)] == [False] * 5 + [True, True]
    assert "hold/all/0/piano_idle" in cond._t and "hold/all" in cond._t
    s.values["music_audible"] = None                         # not(unknown) = unknown: all is unknown
    held, info = cond.holds(rule, "hold", 7)
    assert not held and info["value"] is None
    s.values["music_audible"] = True
    assert cond.holds(rule, "hold", 8)[1]["value"] is False
    any_rule = {"any": [{"quiet": {}}, {"piano_playing": {}}]}
    s.values.update(quiet=None, piano_playing=True)
    assert cond.holds(any_rule, "a", 0)[0] is True
    s.values.update(piano_playing=False)
    assert cond.holds(any_rule, "a", 1)[1]["value"] is None
    s.values.update(quiet=False)
    assert cond.holds(any_rule, "a", 2)[1]["value"] is False


def test_several_words_in_one_object_must_all_hold():
    s = Stub()
    cond = Conditions(s)
    s.values.update(piano_idle=True, quiet=False)
    assert cond.holds({"piano_idle": {}, "quiet": {}}, "w", 0)[0] is False
    s.values["quiet"] = True
    assert cond.holds({"piano_idle": {}, "quiet": True}, "w", 1)[0] is True


def test_bad_rules_never_hold_and_say_why():
    cond = Conditions(Stub())
    assert cond.check({"piano_playing": {"for_s": 10}}) == []
    assert cond.check({"between": {"from": "18:00", "to": "23:00"}}) == []
    for bad, words in [({"piano_playin": {}}, "unknown situation"),
                       ({"piano_playing": {"min_notez": 3}}, "unknown param"),
                       ({"piano_playing": {"min_notes": "lots"}}, "number"),
                       ({"piano_playing": {"for_s": -1}}, "at least 0"),
                       ({"between": {"from": "25:00", "to": "01:00"}}, "HH:MM"),
                       ({"between": {"from": "18:00"}}, "needs to"),
                       ({"genre_in": {}}, "needs genres"),
                       ({"all": []}, "non-empty list"),
                       ({"all": [{"quiet": {}}], "quiet": {}}, "stands alone"),
                       ({"piano_playing": 3}, "params must be an object"),
                       ([], "must be an object")]:
        problems = cond.check(bad)
        assert problems and any(words in p for p in problems), (bad, problems)
        held, info = cond.holds(bad, "bad", 0.0)
        assert held is False and info["problems"] == problems


def test_a_changed_rule_resets_its_timers():
    s = Stub()
    cond = Conditions(s)
    s.values["quiet"] = True
    for t in range(4):
        cond.holds({"quiet": {"for_s": 3}}, "r", t)
    assert cond.holds({"quiet": {"for_s": 3}}, "r", 4)[0]
    assert not cond.holds({"quiet": {"for_s": 2}}, "r", 5)[0]        # new text: starts again
    assert cond.holds({"quiet": {"for_s": 2}}, "r", 7)[0]
    cond.reset("r")
    assert not cond.holds({"quiet": {"for_s": 2}}, "r", 8)[0]


def test_check_param():
    assert check_param("window_s", 10) is None and check_param("window_s", 0) is not None
    assert check_param("channels", [1, 2]) is None and check_param("channels", [0]) is not None
    assert check_param("streams", None) is None and check_param("window_s", None) is not None
    assert check_param("include_test", 1) is not None
    assert set(WORDS) == set(c.SITUATIONS)


def test_readings_are_memoised_within_one_tick_only():
    e = Env()
    e.feat(gate=0.01)
    r1 = e.sit.read("quiet", {}, e.now)
    e.feat(gate=0.5)
    assert e.sit.read("quiet", None, e.now) is r1          # the same now_s: rules sharing a leaf share the reading
    e.now += 1
    e.feat(gate=0.5)
    assert e.sit.read("quiet", None, e.now)["value"] is False
