"""situations.py: NoteTap and the condition evaluator are shared plumbing (real tests); the vocabulary is tested at the
"does it work" level against fake snapshots. No sockets, no devices."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime

import pytest
from aiohttp import web

from show_service import contract as c
from show_service.situations import (DESIGN_DEFAULTS, PARAM_SCHEMAS, WORDS, Conditions, NoteTap, Situations,
                                     check_param, in_window, kleene_all, kleene_any, word_label)
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
    "midi": {"streams": ["room-piano"], "channels": [1], "window_s": 10, "min_notes": 12, "min_span_s": 6,
             "min_pitches": 4},
    "music": {"stream": "room-mic", "min_prob": 0.5, "dsp_min_gate": 0.3},
    "sonos": {"now_playing": "sonos.zone.now_playing", "volume": "sonos.zone.volume", "own_change_s": 60},
    "quiet": {"stream": "room-mic", "gate_below": 0.05}}}


class Env:
    def __init__(self, ml: bool = False) -> None:
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
        self.ml: dict | None = None                       # Stage 4: the ML worker's view (MLView)
        self.mc: dict | None = None                       # Stage 4: structure's music.context
        if ml:
            ctx.ml = lambda: self.ml
        ctx.music_context = lambda: self.mc
        self.sit = Situations(ctx, local=lambda: self.local)
        self.sit.configure(SETTINGS)
        self.sit.start()

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


def test_piano_playing_reads_midi_alone():
    """21 Oct (H12): no "the mic must hear it too": a quiet mic (headphones, the mic far away) never vetoes the MIDI."""
    e = Env()
    e.play(20, 0.5)
    e.feat(lufs_s=-60.0)                             # the mic is up and hears next to nothing
    r = e.read("piano_playing")
    assert r["value"] is True and r["via"] == "midi" and "heard_lufs" not in r["evidence"]
    assert "heard_by" not in PARAM_SCHEMAS and "heard_by" not in DESIGN_DEFAULTS
    assert "heard_by" not in WORDS["piano_playing"]["params"]


def test_a_piano_that_isnt_sending_has_stopped():
    e = Env()
    e.health["streams"]["room-piano"]["state"] = "down"     # its sender stopped (owner, 17 Oct)
    assert e.read("piano_playing")["value"] is None
    r = e.read("piano_idle")
    assert r["value"] is True and r["evidence"]["why"] == "the piano isn't sending"
    del e.health["streams"]["room-piano"]                    # never connected (or gone after a restart): stopped too
    assert e.read("piano_idle")["value"] is True and e.read("piano_playing")["value"] is None


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
    e.room = sonos_room("PLAYING", sent_age=5.0)     # the room just changed Sonos itself: Sonos is left out...
    e.health["streams"]["room-mic"]["state"] = "streaming"
    e.health["active_audio"] = "room-mic"
    e.feat(gate=0.6)
    e.clock = {"gate": "beat"}
    r = e.read("music_audible")                      # ...but the mic still decides (21 Oct, H9)
    assert r["value"] is True and r["via"] == "dsp" and r["evidence"]["sonos"] is None
    assert "sent_age_s" in r["evidence"]["sonos_detail"]
    e.clock = None
    assert e.read("music_audible")["value"] is False   # the mic hears no music: Sonos's own change doesn't make it so
    e.feat(gate=0.01)
    assert e.read("quiet")["value"] is True          # and the room's quiet is the mic's, own change or not


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


# -- Stage 4: CED and music.context (README S4.8) ---------------------------------------------------------------------
STAGE4 = {"situations": {**SETTINGS["situations"],
                         "quiet": {"stream": "room-mic", "gate_below": 0.05, "music_below": 0.2, "speech_below": 0.2},
                         "talking": {"stream": "room-mic", "min_prob": 0.5, "music_below": 0.5},
                         "ced": {"window_s": 3, "hysteresis": 0.05}}}


def ced(t: float, music: float = 0.0, speech: float = 0.0, stream: str = "room-mic", **kw) -> dict:
    row = {"type": "ced", "t": t, "window_s": 2.0, "stream": stream, "seg": 1, "music_p": music, "speech_p": speech,
           "conversation_p": 0.0, "singing_p": 0.0, "crowd_p": 0.0, "applause_p": 0.0, "silence_p": 0.0,
           "cost_ms": 20.0}
    row.update({f"{k}_p": v for k, v in kw.items()})
    return row


def ml_view(rows: list[dict], stream: str = "room-mic") -> dict:
    return {"state": "running", "why": None, "stream": stream, "ced": rows[-1] if rows else None, "ced_recent": rows,
            "tags": None, "emb_recent": [], "key": None, "beats": None, "vote": None, "ages": {}, "hello": None}


def stage4_env() -> Env:
    e = Env(ml=True)
    e.sit.configure(STAGE4)
    return e


def say(e: Env, music: float, speech: float, stream: str = "room-mic") -> None:
    """Three fresh CED readings (one a second) with these numbers."""
    e.ml = ml_view([ced(e.now - 2 + i, music, speech, stream) for i in range(3)], stream)


def test_the_classifier_is_the_mean_of_the_last_3_s_for_its_stream():
    e = stage4_env()
    assert e.sit.classifier is not None                      # bound to ctx.ml at start
    e.ml = ml_view([ced(e.now - 6, 1.0, 1.0), ced(e.now - 2, 0.2, 0.6), ced(e.now - 1, 0.4, 0.8), ced(e.now, 0.6, 0.7)])
    got = e.sit._ced("room-mic", e.now)
    assert got["n"] == 3 and got["music"] == pytest.approx(0.4) and got["speech"] == pytest.approx(0.7)
    assert set(c.CED_GROUPS) <= set(got) and got["stream"] == "room-mic"
    assert e.sit._ced("other-mic", e.now) is None             # another stream's results: none for this one
    assert e.sit._ced(None, e.now)["n"] == 3                  # None: the measured stream
    e.ml = ml_view([])
    assert e.sit._ced(None, e.now) is None
    e.ml = None
    assert e.sit._ced(None, e.now) is None
    assert Env().sit.classifier is None                       # no ML slot: Stage 3 as before


def test_talking_true_false_unknown_and_hysteresis():
    e = Env()
    assert e.read("talking")["value"] is None                 # no worker: unknown
    e = stage4_env()
    r = e.read("talking")
    assert r["value"] is None and "no fresh" in r["evidence"]["why"]
    say(e, music=0.2, speech=0.7)
    r = e.read("talking")
    assert r["value"] is True and r["via"] == "classifier" and r["evidence"]["speech"] == pytest.approx(0.7)
    say(e, music=0.47, speech=0.47)                           # holding: back past each threshold by 0.05 to let go
    assert e.read("talking")["value"] is True
    say(e, music=0.2, speech=0.44)
    assert e.read("talking")["value"] is False
    say(e, music=0.2, speech=0.48)                            # not holding: the plain threshold again
    assert e.read("talking")["value"] is False
    say(e, music=0.6, speech=0.9)                             # speech over music: not "talking"
    assert e.read("talking")["value"] is False
    assert e.read("talking", {"music_below": 0.7})["value"] is True    # a rule's own params win


def test_quiet_by_ced_only_noise_over_the_gate():
    e = stage4_env()
    e.feat(gate=0.3)                                          # over the gate...
    say(e, music=0.05, speech=0.1)                            # ...but neither music nor speech
    r = e.read("quiet")
    assert r["value"] is True and r["via"] == "classifier" and r["evidence"]["music"] == pytest.approx(0.05)
    say(e, music=0.6, speech=0.1)
    assert e.read("quiet")["value"] is False
    e.feat(gate=0.01)
    assert e.read("quiet")["via"] == "dsp" and e.read("quiet")["value"] is True       # the gate first, as before
    e2 = Env(ml=True)                                         # no music_below/speech_below: the gate alone
    e2.feat(gate=0.3)
    say(e2, music=0.05, speech=0.1)
    r = e2.read("quiet")
    assert r["value"] is False and r["via"] == "dsp"


def test_music_audible_by_ced():
    e = stage4_env()
    e.feat(gate=0.6)
    e.clock = {"gate": "beat"}                                # the DSP guess would say yes...
    say(e, music=0.2, speech=0.7)
    r = e.read("music_audible")
    assert r["value"] is False and r["via"] == "classifier"   # ...CED decides
    say(e, music=0.8, speech=0.1)
    e.clock = {"gate": "none"}
    r = e.read("music_audible")
    assert r["value"] is True and r["via"] == "classifier" and r["evidence"]["music_prob"] == 0.8
    say(e, music=0.47, speech=0.1)                            # holding
    assert e.read("music_audible")["value"] is True
    say(e, music=0.3, speech=0.1)
    assert e.read("music_audible")["value"] is False
    e.room = sonos_room("PLAYING")                            # Sonos plays and the mic hears it (§8.9)
    r = e.read("music_audible")
    assert r["value"] is True and r["via"] == "sonos+mic"
    e.ml = ml_view([])                                        # no fresh reading: the Stage-3 DSP guess
    e.room = None
    e.clock = {"gate": "beat"}
    assert e.read("music_audible")["via"] == "dsp"


def test_ced_words_ignore_a_test_stream_unless_asked():
    e = stage4_env()
    e.health["active_audio"] = "test-audio"
    e.health["streams"]["test-audio"] = {"kind": "audio", "state": "streaming", "test": True}
    e.feat(gate=0.6, stream="test-audio")
    say(e, music=0.1, speech=0.9, stream="test-audio")
    assert e.read("talking")["value"] is None                 # show.yaml's room-mic: not this stream's results
    r = e.read("talking", {"stream": None})
    assert r["value"] is None and "test stream" in r["evidence"]["why"]
    assert e.read("talking", {"stream": None, "include_test": True})["value"] is True
    say(e, music=0.9, speech=0.0, stream="test-audio")
    assert e.read("music_audible", {"stream": None})["value"] is None
    assert e.read("music_audible", {"stream": None, "include_test": True})["value"] is True


def context(e: Env, word, conf, via="ml", parent="Jazz", age_s=0.5) -> dict:
    return {"t_ms": (e.now - age_s) * 1000.0, "genre": {"word": word, "conf": conf, "parent": parent,
                                                        "parent_p": conf, "via": via}}


def test_genre_in_reads_the_context_first_then_the_beliefs():
    e = Env()
    e.beliefs = {"state": "fresh", "genre": {"word": "soul", "conf": 0.9}}
    e.mc = context(e, "jazz", 0.8)
    r = e.read("genre_in", {"genres": ["jazz"]})
    assert r["value"] is True and r["via"] == "ml" and r["evidence"]["parent"] == "Jazz"
    assert r["evidence"]["via"] == "ml"
    assert e.read("genre_in", {"genres": ["soul"]})["value"] is False     # the ML's word wins (§8.9)
    e.mc = context(e, "jazz", 0.4)                        # the ML isn't sure: the AI listener's word (S5.20)
    r = e.read("genre_in", {"genres": ["jazz"]})
    assert r["value"] is False and r["via"] == "listener" and r["evidence"]["ml"]["why"] == "not confident"
    e.mc = context(e, None, 0.0, via="none", parent="Electronic")         # a family genre_map gives no word for
    r = e.read("genre_in", {"genres": ["soul"]})
    assert r["value"] is True and r["via"] == "listener" and r["evidence"]["parent"] == "Electronic"
    assert r["evidence"]["ml"]["why"] == "no genre word yet"
    e.beliefs = None                                      # ...and with no listener either: unknown, the ML's reason
    r = e.read("genre_in", {"genres": ["soul"]})
    assert r["value"] is None and r["evidence"]["why"] == "no genre word yet"
    e.beliefs = {"state": "fresh", "genre": {"word": "soul", "conf": 0.9}}
    e.mc = context(e, "house", 0.7, via="ml+listener", parent="Electronic")
    r = e.read("genre_in", {"genres": ["house"]})
    assert r["value"] is True and r["via"] == "ml+listener"
    e.mc = context(e, "jazz", 0.9, age_s=10.0)                # a stale context: the listener's beliefs
    r = e.read("genre_in", {"genres": ["soul"]})
    assert r["value"] is True and r["via"] == "listener" and r["evidence"]["via"] == "listener"
    e.mc = None
    assert e.read("genre_in", {"genres": ["soul"]})["value"] is True
    voc = {d["id"]: d for d in e.sit.vocabulary()}
    assert voc["genre_in"]["rank"] == c.SITUATION_RANK["listener"]


def test_stage4_vocabulary_and_settings():
    e = stage4_env()
    e.sit.tick(e.now)
    voc = {d["id"]: d for d in e.sit.snapshot()["vocabulary"]}
    assert voc["talking"]["available"] is True
    assert voc["talking"]["params"]["music_below"]["default"] == 0.5
    assert voc["talking"]["params"]["music_below"]["x-ui"]["source"] == "show.yaml situations.talking"
    assert voc["quiet"]["params"]["speech_below"]["default"] == 0.2
    assert all("(P1)" not in d["description"] for d in voc.values())
    assert e.sit.snapshot()["classifier"]["on"] is True and e.sit.problems == []
    e.sit.configure({"situations": {"ced": {"window_s": -1, "hysteresis": 0.9}}})
    assert len(e.sit.problems) == 2 and e.sit._ced_cfg == {"window_s": 3.0, "hysteresis": 0.05}
    cond = Conditions(e.sit)
    assert cond.check({"talking": {"music_below": 0.3, "for_s": 5}}) == []
    assert cond.check({"quiet": {"speech_below": 2}}) != []


def test_babble_and_conversation_count_as_speech():
    """README S4.8, §7.5: guests talking over music read as conversation and babble, not only Speech
    (situations.talking.speech_words: talking, quiet and the genre's speech scale read the same list)."""
    e = stage4_env()
    e.ml = ml_view([ced(e.now - 2 + i, 0.2, 0.1, conversation=0.7) for i in range(3)])
    r = e.read("talking")
    assert r["value"] is True and r["evidence"]["speech"] == pytest.approx(0.7)
    e.feat(gate=0.3)
    e.ml = ml_view([ced(e.now - 2 + i, 0.05, 0.05, crowd=0.4) for i in range(3)])
    r = e.read("quiet")
    assert r["value"] is False and r["evidence"]["speech"] == pytest.approx(0.4)    # chatter: not only noise
    e.sit.configure({"situations": {**STAGE4["situations"], "talking": {**STAGE4["situations"]["talking"],
                                                                         "speech_words": ["speech"]}}})
    assert e.read("quiet")["value"] is True and e.sit.problems == []                 # config, not code
    e.sit.configure({"situations": {**STAGE4["situations"], "talking": {"speech_words": "speech"}}})
    assert len(e.sit.problems) == 1 and e.sit._speech_words == c.SPEECH_WORDS


def test_genre_in_holds_its_confidence_with_the_ced_hysteresis():
    e = Env()
    e.mc = context(e, "jazz", 0.62)
    assert e.read("genre_in", {"genres": ["jazz"]})["value"] is True
    for conf, want in ((0.57, True), (0.62, True), (0.54, None), (0.57, None), (0.61, True)):
        e.mc = context(e, "jazz", conf)
        assert e.read("genre_in", {"genres": ["jazz"]})["value"] is want, conf


def test_the_ced_window_follows_the_workers_stretched_cadence():
    e = stage4_env()
    rows = [ced(e.now - 5.0 - 6.0 * k, 0.8, 0.0) for k in range(3)][::-1]     # every 6 s, the newest 5 s old
    e.ml = ml_view(rows)
    assert e.sit._ced(None, e.now) is None                                     # not in the last 3 s
    e.ml = {**ml_view(rows), "stale_s": {"ced": 9.0, "tags": 9.0, "key": 9.0, "beats": 30.0}}
    got = e.sit._ced(None, e.now)
    assert got is not None and got["n"] == 1 and got["music"] == pytest.approx(0.8)


# -- the mic on demand (README S5.16) ----------------------------------------------------------------------------------
def _mic_off(e: Env, *, state: bool = True) -> None:
    """The receiver keeps room-mic off: its health says so (and, once the state pass ran, its state and the active)."""
    e.health["off"] = ["room-mic"]
    if state:
        e.health["streams"]["room-mic"]["state"] = "off"
        e.health["active_audio"] = None


@pytest.mark.parametrize("ml", [False, True])
def test_the_mic_switched_off_is_never_quiet_and_its_past_is_not_now(ml):
    e = stage4_env() if ml else Env()
    e.feat(gate=0.01, energy=0.9)                             # a quiet room, just measured
    if ml:
        say(e, music=0.05, speech=0.8)
    assert e.read("quiet")["value"] is True
    assert e.read("quiet", {"stream": None})["value"] is True
    _mic_off(e, state=False)                                  # health has the off set, the state pass not yet run
    for params in (None, {"stream": None}):
        r = e.read("quiet", params)
        assert r["value"] is None and r["evidence"]["why"] == "the mic is off", (params, r)
    _mic_off(e)
    for params in (None, {"stream": None}, {"for_s": 0}):
        r = e.read("quiet", params)
        assert r["value"] is None and r["evidence"]["why"] == "the mic is off", (params, r)
    assert e.read("talking")["value"] is None
    assert e.read("music_audible")["value"] is not True
    assert e.read("energy_above", {"value": 0.5})["value"] is None
    e.features = None                                         # the features age out too: still unknown, never quiet
    e.now += 10
    assert e.read("quiet")["value"] is None and e.read("quiet", {"stream": None})["value"] is None


def test_a_rule_on_quiet_never_holds_because_the_mic_went_off():
    e = Env()
    cond = Conditions(e.sit)
    e.feat(gate=0.2)                                          # loud: not quiet
    assert cond.holds({"quiet": {"for_s": 5}}, "r", e.now)[0] is False
    _mic_off(e)
    for _ in range(30):
        e.now += 1
        assert cond.holds({"quiet": {"for_s": 5}}, "r", e.now)[0] is False
        assert cond.holds({"not": {"music_audible": {}}}, "m", e.now)[0] is False


# -- the room's light (README S5.19) ------------------------------------------------------------------------------------
LIGHT_URL = "http://127.0.0.1:9/api/lightsensor/lightsense"     # never polled here: no loop, answers fed by hand


class LightEnv:
    """Situations with show.yaml's light section and a fake clock; the sensor route's answers are fed by hand."""

    def __init__(self, **light) -> None:
        self.t = 1000.0
        self.sit = Situations(c.BrainContext(publish=lambda m: None), mono=lambda: self.t, wall=self.wall)
        self.sit.configure({"situations": {"light": {"url": LIGHT_URL, "sensor": "living-room", **light}}})
        self.sit.start()                                      # no event loop: nothing is polled

    def wall(self) -> float:
        return 1.79e9 + self.t

    def answer(self, lux, *, every_s: float = 1.0, name: str = "living-room", age_s: float = 0.0,
               extra: dict | None = None) -> dict:
        """The route's JSON (routes/lightsensor.py's shape): ``lux`` one value or a list of samples, the newest age_s
        old, every_s apart."""
        vals = lux if isinstance(lux, list) else [lux]
        end = self.wall() - age_s
        hist = [{"timestamp": end - (len(vals) - 1 - i) * every_s, "lux": v, "target_intensity": 1.0}
                for i, v in enumerate(vals)]
        sensors = {name: {"current": {"lux": vals[-1], "target_group": name, "target_intensity": 1.0}, "history": hist}}
        return {"sensors": {**sensors, **(extra or {})}}

    def feed(self, lux, **kw) -> None:
        assert self.sit.light.ingest(self.answer(lux, **kw))

    def read(self, sid: str, **params) -> dict:
        self.t += 0.001                                       # a new now: never the memo
        return self.sit.read(sid, params, self.t)


def test_light_words_read_the_median_so_someone_walking_past_doesnt_flip_them():
    e = LightEnv()
    e.feed([300.0] * 50 + [8.0] * 6 + [300.0] * 4)           # a bright minute; a shadow for 6 s
    r = e.read("light_below", lux=50)
    assert r["value"] is False and r["via"] == "light" and r["evidence"]["level"] == 300.0
    assert r["evidence"]["samples"] == 60 and r["evidence"]["sensor"] == "living-room"
    assert e.read("light_above", lux=200)["value"] is True
    e.t += 60
    e.feed([10.0] * 60)                                       # the lights go down for a minute
    r = e.read("light_below", lux=50)
    assert r["value"] is True and r["evidence"]["level"] == 10.0 and r["evidence"]["last"] == 10.0
    e.t += 120
    e.feed([10.0] * 20 + [400.0] * 40)                        # the curtains open: the median follows within the window
    assert e.read("light_below", lux=50)["value"] is False


def test_light_hysteresis_and_its_mirror():
    e = LightEnv(smooth_s=0)                                  # the newest sample alone: steps are exact

    def below(lux: float):
        e.t += 1
        e.feed(lux)
        return e.read("light_below", lux=50)["value"]

    # true under 50; once true, false again only above 50 x 1.15 = 57.5; then true again only under 50
    assert [below(x) for x in (60, 49, 55, 57.5, 58, 52, 49.9)] == [False, True, True, True, False, False, True]
    e = LightEnv(smooth_s=0)

    def above(lux: float):
        e.t += 1
        e.feed(lux)
        return e.read("light_above", lux=200)["value"]

    assert [above(x) for x in (190, 201, 175, 170, 169, 199, 201)] == [False, True, True, True, False, False, True]
    e = LightEnv(smooth_s=0, hysteresis=0)
    assert [below(x) for x in (49, 50, 49)] == [True, False, True]


def test_light_is_unknown_when_stale_silent_or_missing():
    e = LightEnv()                                            # poll_s 10: stale after 30 s
    r = e.read("light_below", lux=50)
    assert r["value"] is None and r["evidence"]["why"] == "no light reading yet"
    e.feed(20.0)
    assert e.read("light_below", lux=50)["value"] is True
    e.t += 29.9
    assert e.read("light_below", lux=50)["value"] is True    # no poll for 3 x poll_s: still good
    e.t += 1
    r = e.read("light_below", lux=50)
    assert r["value"] is None and r["evidence"]["why"].startswith("no good light reading for 31")
    e.feed(20.0, age_s=40)                                    # the route answers, its newest sample older still
    r = e.read("light_below", lux=50)
    assert r["value"] is None and r["evidence"]["why"] == "the sensor hasn't reported for 31 s"
    e2 = LightEnv()
    e2.feed(20.0, age_s=45)                                   # the route answers, the sensor itself went silent
    r = e2.read("light_below", lux=50)
    assert r["value"] is None and r["evidence"]["why"] == "the sensor hasn't reported for 45 s"
    e.feed(20.0)
    assert e.read("light_below", lux=50)["value"] is True
    assert e.sit.light.ingest(e.answer(20.0, name="hall"))   # Flask restarted with another sensor only
    r = e.read("light_below", lux=50)
    assert r["value"] is None and "has no 'living-room'" in r["evidence"]["why"] and r["evidence"]["sensors"] == ["hall"]
    assert e.read("light_below", lux=50, sensor="hall")["value"] is True     # a rule may name its own sensor
    assert not e.sit.light.ingest({"error": "x"}) and e.sit.light.error   # not an answer: changes nothing
    assert e.read("light_below", lux=50, sensor="hall")["value"] is True

    e = LightEnv(sensor=None)                                 # show.yaml names none: the only one, else say which
    e.feed(20.0, extra={"hall": {"current": {"lux": 400.0}, "history": []}})   # no history: its current lux, now
    r = e.read("light_below", lux=50)
    assert r["value"] is None and r["evidence"]["why"].startswith("say which sensor")
    assert e.read("light_above", lux=200, sensor="hall")["value"] is True
    assert e.sit.light.ingest(e.answer(20.0))
    assert e.read("light_below", lux=50)["value"] is True
    e = LightEnv(url=None)                                    # no url: unknown, never an invented value
    r = e.read("light_below", lux=50)
    assert r["value"] is None and "situations.light.url" in r["evidence"]["why"]
    assert e.read("light_below")["value"] is None             # no lux: unknown (the rule check says why)


def test_a_failed_or_slow_poll_never_blocks_and_keeps_the_last_value_until_stale():
    """The real poll against a local aiohttp server (an ephemeral port): async, bounded by its timeout, and a failure
    leaves the last value until 3 x poll_s have passed."""
    async def go():
        t = [1000.0]
        state = {"status": 200, "delay": 0.0, "lux": 20.0}

        async def route(request):
            if state["delay"]:
                await asyncio.sleep(state["delay"])
            if state["status"] != 200:
                return web.json_response({"error": "boom"}, status=state["status"])
            return web.json_response({"sensors": {"living-room": {"current": {"lux": state["lux"]}, "history": [
                {"timestamp": 1.79e9 + t[0], "lux": state["lux"]}]}}})

        app = web.Application()
        app.router.add_get("/api/lightsensor/lightsense", route)
        runner = web.AppRunner(app, shutdown_timeout=0.2)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        assert port not in (5000, 8700, 8701, 8702, 8703)
        ctx = c.BrainContext(publish=lambda m: None)
        ctx.loop = asyncio.get_running_loop()
        sit = Situations(ctx, mono=lambda: t[0], wall=lambda: 1.79e9 + t[0])
        try:
            sit.configure({"situations": {"light": {"url": f"http://127.0.0.1:{port}/api/lightsensor/lightsense",
                                                    "sensor": "living-room", "poll_s": 60}}})
            sit.start()                                       # the poll task: its first read comes at once
            for _ in range(200):
                if sit.light.stats["ok"]:
                    break
                await asyncio.sleep(0.01)
            assert sit.light.stats["ok"] == 1 and sit.light._task is not None
            assert sit.read("light_below", {"lux": 50}, t[0])["value"] is True
            state["status"] = 500
            assert await sit.light.poll_once() is False and "500" in sit.light.error
            t[0] += 100
            assert sit.read("light_below", {"lux": 50}, t[0])["value"] is True      # the last value holds
            state["status"], state["delay"] = 200, 3.0        # slower than its budget (2 s)
            t0 = time.monotonic()
            assert await sit.light.poll_once() is False
            assert time.monotonic() - t0 < 2.8 and "Timeout" in sit.light.error
            t[0] += 81                                        # 181 s since the last good read: stale (3 x 60 s)
            r = sit.read("light_below", {"lux": 50}, t[0])
            assert r["value"] is None and r["evidence"]["why"].startswith("no good light reading")
            state["delay"], state["lux"] = 0.0, 400.0
            assert await sit.light.poll_once() is True
            assert sit.read("light_above", {"lux": 200}, t[0] + 1)["value"] is True
            sit.configure({"situations": {}})                 # the url goes: the poll stops, the words read unknown
            assert sit.light._task is None and sit.read("light_above", {"lux": 200}, t[0] + 2)["value"] is None
        finally:
            sit.stop()
            await runner.cleanup()

    asyncio.run(go())


def test_light_words_in_the_vocabulary_and_their_labels():
    e = LightEnv()
    voc = {d["id"]: d for d in e.sit.vocabulary()}
    lb = voc["light_below"]
    assert lb["label"] == "the room is darker than" and lb["source"] == "light" and lb["rank"] == c.SITUATION_RANK["light"]
    assert lb["available"] is False and lb["unavailable"] == "The light sensor isn't reporting"
    assert set(lb["params"]) == {"for_s", "lux", "sensor"}
    lux = lb["params"]["lux"]
    assert lux["type"] == "number" and "default" not in lux
    assert {k: lux["x-ui"][k] for k in ("label", "unit", "required")} == {"label": "Light level", "unit": "lux",
                                                                           "required": True}
    sensor = lb["params"]["sensor"]
    assert sensor["default"] == "living-room" and sensor["x-ui"]["source"] == "show.yaml situations.light"
    assert sensor["x-ui"]["required"] is False and sensor["enum"] == ["living-room"]     # show.yaml's own, no other
    e.feed(30.0, extra={"hall": {"current": {"lux": 5.0}, "history": []}})
    voc = {d["id"]: d for d in e.sit.vocabulary()}
    sensor = voc["light_above"]["params"]["sensor"]
    assert sensor["enum"] == ["hall", "living-room"] and sensor["x-ui"]["enum_labels"] == ["Hall", "Living room"]
    assert voc["light_below"]["available"] is True and voc["light_above"]["available"] is True
    # every param carries the room's hints: a label and required; 0..1 sliders name their ends
    for d in voc.values():
        assert d["unavailable"]
        for k, sch in d["params"].items():
            assert sch["x-ui"]["label"] and isinstance(sch["x-ui"]["required"], bool), (d["id"], k)
            if sch.get("minimum") == 0 and sch.get("maximum") == 1:
                assert set(sch["x-ui"]["ends"]) == {"min", "max"} and sch["x-ui"]["widget"] == "slider", (d["id"], k)
    assert voc["genre_in"]["params"]["genres"]["x-ui"]["required"] is True
    assert word_label("light_below", {"lux": 50}) == "the room is darker than 50 lux"
    assert word_label("light_above", {"lux": 200.5, "sensor": "hall"}) == "the room is brighter than 200.5 lux (the hall sensor)"
    assert word_label("light_below") == "the room is darker than … lux"
    e.sit.tick(e.t)
    snap = e.sit.snapshot()
    assert snap["readings"]["light_below"]["value"] is None and "needs lux" in snap["readings"]["light_below"]["evidence"]["why"]
    assert snap["light"]["level"] == 30.0 and snap["light"]["sensors"] == ["hall", "living-room"]


def test_the_real_show_yaml_light_section_and_bad_values():
    import yaml as _yaml
    real = _yaml.safe_load(c.SHOW_FILE.read_text())
    e = LightEnv()
    e.sit.configure(real)
    assert e.sit.problems == []
    lt = e.sit.light
    assert lt.url == "http://127.0.0.1:5000/api/lightsensor/lightsense" and lt.sensor == "living-room"
    assert (lt.poll_s, lt.smooth_s, lt.hysteresis, lt.stale_s()) == (10.0, 60.0, 0.15, 30.0)
    assert lt._task is None                                   # no loop here: the real route is never read by a test
    e.sit.configure({"situations": {"light": {"url": "ftp://x", "sensor": "", "poll_s": 0, "smooth_s": -1,
                                              "hysteresis": 2}}})
    assert len(e.sit.problems) == 5 and lt.url is None
    assert (lt.poll_s, lt.smooth_s, lt.hysteresis) == (10.0, 60.0, 0.15)
    e.sit.configure({"situations": {"light": "on"}})
    assert e.sit.problems == ["situations.light: not a mapping"] and lt.url is None


def test_a_light_rule_checks_and_holds():
    e = LightEnv()
    cond = Conditions(e.sit)
    assert cond.check({"light_below": {"lux": 50, "for_s": 120}}) == []
    assert any("needs lux" in p for p in cond.check({"light_below": {}}))
    assert any("lux must be at least 0" in p for p in cond.check({"light_above": {"lux": -1}}))
    e.feed(20.0)
    rule = {"all": [{"light_below": {"lux": 50, "for_s": 2}}, {"not": {"light_above": {"lux": 300}}}]}
    got = []
    for _ in range(4):
        e.t += 1
        e.feed(20.0)
        got.append(cond.holds(rule, "evening/enter_when", e.t)[0])
    assert got == [False, False, True, True]
    assert cond.describe(rule, "enter_when")["nodes"]["enter_when.all[0]"] == \
        "the room is darker than 50 lux, for at least 2 s"


# -- the rules in page words, per node (README S5.19) ---------------------------------------------------------------------
def test_describe_reads_naturally_with_nested_all_any_not_and_for_s():
    s = Stub()
    cond = Conditions(s)
    s.values.update(piano_playing=True, quiet=False, light_below=True)
    rule = {"any": [{"all": [{"piano_playing": {"for_s": 10}}, {"light_below": {"lux": 50}}]},
                    {"not": {"quiet": {"for_s": 30}}}], "for_s": 5}
    d = cond.describe(rule, "enter_when", 0.0)
    assert d["text"] == ("any of: (both: (the piano is playing, for at least 10 s) and the room is darker than 50 lux) "
                         "or not (the room is quiet, for at least 30 s), for at least 5 s")
    assert d["nodes"] == {
        "enter_when.any[0].all[0]": "the piano is playing, for at least 10 s",
        "enter_when.any[0].all[1]": "the room is darker than 50 lux",
        "enter_when.any[0]": "both: (the piano is playing, for at least 10 s) and the room is darker than 50 lux",
        "enter_when.any[1].not": "the room is quiet, for at least 30 s",
        "enter_when.any[1]": "not (the room is quiet, for at least 30 s)",
        "enter_when": d["text"]}
    # live: the raw truth now, for_s left out (Auto's timers are its own)
    assert d["live"] == {"enter_when.any[0].all[0]": True, "enter_when.any[0].all[1]": True, "enter_when.any[0]": True,
                         "enter_when.any[1].not": False, "enter_when.any[1]": True, "enter_when": True}
    assert d["words"]["enter_when.any[0].all[1]"] == {"id": "light_below", "params": {"lux": 50}}
    s.values.update(piano_playing=None, quiet=True)
    assert cond.describe(rule, "enter_when", 1.0)["live"]["enter_when"] is None          # unknown or false: unknown
    assert cond.describe(rule, "enter_when", None)["live"]["enter_when"] is None          # nothing read
    # leave rules, several words in one object, three items, durations
    assert cond.describe({"piano_idle": {"for_s": 120}}, "leave_when")["text"] == "the piano has stopped, for at least 2 min"
    d = cond.describe({"piano_idle": {}, "between": {"from": "18:00", "to": "23:00"}, "for_s": 3600}, "leave_when")
    assert d["text"] == "both: the piano has stopped and it's 18:00-23:00, for at least 1 hour"
    assert set(d["nodes"]) == {"leave_when", "leave_when.piano_idle", "leave_when.between"}
    d = cond.describe({"all": [{"quiet": {}}, {"piano_idle": True}, {"genre_in": {"genres": ["jazz", "soul"]}}]}, "x")
    assert d["text"] == "all of: the room is quiet, the piano has stopped and the music is jazz or soul"
    assert cond.describe({"any": [{"quiet": {"for_s": 90}}, {"talking": {}}]}, "x")["text"] == \
        "any of: (the room is quiet, for at least 90 s) or people are talking"
    d = cond.describe({"all": [{"nope": {}}, 3]}, "enter_when")                           # malformed: "?" and None
    assert d["text"] == "both: “nope” and ?" and d["live"]["enter_when.all[1]"] is None


def test_check_nodes_paths_and_plain_words_and_check_unchanged():
    cond = Conditions(Stub())
    rule = {"any": [{"piano_playin": {}}, {"all": [{"not": {"piano_playing": {"min_notes": "lots"}}}]},
                    {"piano_idle": {}, "between": {"from": "25:00"}}], "for_s": -1}
    got = cond.check_nodes(rule, "leave_when")
    assert [(p["path"], p["level"]) for p in got] == [
        ("leave_when", "error"),                              # for_s -1
        ("leave_when.any[0]", "error"),                       # unknown word
        ("leave_when.any[1].all[0].not", "error"),            # a bad param
        ("leave_when.any[2].between", "error"),               # a bad time
        ("leave_when.any[2].between", "error")]               # and a missing one
    texts = [p["text"] for p in got]
    assert texts[0] == "“For at least” must be at least 0"
    assert texts[1] == "This isn't something the room can notice (it may have been renamed)"
    assert texts[2] == "“the piano is playing”: “At least this many notes” must be a number"
    assert texts[4] == "“it's between these times” needs “To”"
    assert cond.check(rule) == [p["where"] for p in got]     # check() says exactly what it said before
    assert cond.check_nodes({}, "enter_when")[0]["text"] == "Empty: pick what Auto should notice"
    assert cond.check_nodes({"all": [{"quiet": {}}], "quiet": {}}, "e")[0]["path"] == "e"



# -- README S5.20: the listener's words for Auto ----------------------------------------------------------------------
def test_genre_in_falls_back_to_the_listener_when_the_ml_family_has_no_word():
    """Audit, 17 Oct: the ML leading with Hip Hop (no vocab word) made genre_in unknown even with a sure listener."""
    e = Env()
    e.beliefs = {"state": "holding", "genre": {"word": "jazz", "conf": 0.8}}
    e.mc = context(e, None, 0.0, via="none", parent="Hip Hop")
    r = e.read("genre_in", {"genres": ["jazz", "soul"]})
    assert r["value"] is True and r["via"] == "listener" and r["evidence"]["parent"] == "Hip Hop"
    e.beliefs = {"state": "holding", "genre": {"word": "jazz", "conf": 0.3}}           # the listener unsure too
    r = e.read("genre_in", {"genres": ["jazz"]})
    assert r["value"] is None and r["via"] == "none" and r["evidence"]["why"] == "no genre word yet"
    e.beliefs = {"state": "fading", "genre": {"word": "jazz", "conf": 0.9}}            # fading: not "now"
    assert e.read("genre_in", {"genres": ["jazz"]})["value"] is None


def ml_context(e: Env, *, moods=(), phase="calm", track_id=0, track_age=None, track_via=None, age_s=0.5) -> dict:
    return {"t_ms": (e.now - age_s) * 1000.0, "genre": {"word": None, "conf": 0.0, "parent": None, "parent_p": 0.0,
                                                        "via": "none"},
            "moods": [], "section": {"id": 3, "phase": phase, "build_progress": 0.4 if phase == "build" else 0.0},
            "track": {"id": track_id, "changed": False, "age_s": track_age, "via": track_via},
            "ml": {"moods_top5": [list(x) for x in moods]}}


def test_mood_in():
    e = Env()
    e.feat(gate=0.5)
    params = {"moods": ["calm", "dreamy"]}
    assert e.read("mood_in", params)["value"] is None                                  # nothing listens
    assert e.read("mood_in", {})["value"] is None                                      # needs moods
    e.beliefs = {"state": "fresh", "moods": [{"word": "dreamy", "conf": 0.8}, {"word": "warm", "conf": 0.3}]}
    r = e.read("mood_in", params)
    assert r["value"] is True and r["via"] == "listener" and r["evidence"]["mood"] == "dreamy"
    r = e.read("mood_in", {"moods": ["euphoric"]})
    assert r["value"] is False and r["evidence"]["heard"][0] == {"word": "dreamy", "conf": 0.8, "via": "listener"}
    e.beliefs = {"state": "fresh", "moods": [{"word": "dreamy", "conf": 0.5}]}         # one read: not sure yet
    assert e.read("mood_in", {"moods": ["euphoric"]})["value"] is None
    e.beliefs = None
    e.mc = ml_context(e, moods=[("calm", 0.7), ("dark", 0.2)])                         # the ML's mood tags count too
    r = e.read("mood_in", params)
    assert r["value"] is True and r["via"] == "ml"
    assert e.read("mood_in", {"moods": ["calm"], "min_conf": 0.8})["value"] is None
    e.health["active_audio"] = "test-piano"                                            # a test is measured
    e.feat(gate=0.5, stream="test-piano")
    assert e.read("mood_in", params)["value"] is None
    assert e.read("mood_in", dict(params, include_test=True))["value"] is True
    assert word_label("mood_in", params) == "the music feels calm or dreamy"


def test_building():
    e = Env()
    assert e.read("building", {})["value"] is None                                     # nothing follows the music
    e.feat(gate=0.5)
    e.mc = ml_context(e, phase="build")
    r = e.read("building", {})
    assert r["value"] is True and r["via"] == "structure" and r["evidence"]["build_progress"] == 0.4
    e.mc = ml_context(e, phase="peak")
    assert e.read("building", {})["value"] is False
    e.beliefs = {"state": "fresh", "trajectory": {"state": "building", "conf": 0.7, "eta_s": 8.0}}
    r = e.read("building", {})
    assert r["value"] is True and r["via"] == "listener"                              # the listener's word
    e.beliefs = {"state": "fresh", "trajectory": {"state": "building", "conf": 0.4, "eta_s": None}}
    assert e.read("building", {})["value"] is False                                   # unsure: structure says peak
    e.mc = None
    assert e.read("building", {})["value"] is None
    e.beliefs = {"state": "fresh", "trajectory": {"state": "steady", "conf": 0.8, "eta_s": None}}
    assert e.read("building", {})["value"] is False
    e.features = None                                                                 # the mic off: structure isn't now
    e.beliefs = None
    e.mc = ml_context(e, phase="build")
    assert e.read("building", {})["value"] is None


def test_track_changed():
    e = Env()
    assert e.read("track_changed", {})["value"] is None                                # no music.context
    e.feat(gate=0.5)
    e.mc = ml_context(e, track_id=0, track_age=300.0)                                 # the first track since start
    assert e.read("track_changed", {})["value"] is False
    e.mc = ml_context(e, track_id=4, track_age=12.0, track_via="dsp")
    r = e.read("track_changed", {})
    assert r["value"] is True and r["via"] == "dsp" and r["evidence"]["within_s"] == 30
    assert e.read("track_changed", {"within_s": 10})["value"] is False
    e.features = None                                                                 # not heard now, not via Sonos
    assert e.read("track_changed", {"within_s": 10})["value"] is None
    e.mc = ml_context(e, track_id=5, track_age=50.0, track_via="sonos")
    assert e.read("track_changed", {})["value"] is False                              # Sonos still says it
    e.mc = ml_context(e, track_id=5, track_age=5.0, track_via="sonos")
    assert e.read("track_changed", {})["value"] is True
    assert word_label("track_changed", {"within_s": 120}) == "a new song started in the last 2 min"


def test_the_new_words_in_the_vocabulary_and_the_rule_grammar():
    e = Env()
    e.sit.tick(e.now)
    snap = e.sit.snapshot()
    assert set(snap["readings"]) == set(c.SITUATIONS) and set(WORDS) >= {"mood_in", "building", "track_changed"}
    voc = {d["id"]: d for d in snap["vocabulary"]}
    assert voc["mood_in"]["params"]["moods"]["x-ui"]["widget"] == "vocab:moods"
    assert voc["mood_in"]["params"]["moods"]["x-ui"]["required"] is True
    assert voc["track_changed"]["params"]["within_s"]["default"] == 30
    assert voc["track_changed"]["params"]["within_s"]["x-ui"]["source"].startswith("design default")
    assert voc["building"]["rank"] == c.SITUATION_RANK["audio"]
    assert voc["mood_in"]["rank"] == c.SITUATION_RANK["listener"]
    assert snap["readings"]["mood_in"]["evidence"]["why"].startswith("needs")
    for sid in ("mood_in", "building", "track_changed"):
        assert voc[sid]["label"] and voc[sid]["description"] and voc[sid]["unavailable"]
    cond = Conditions(e.sit)
    assert cond.check({"mood_in": {"moods": ["calm"], "min_conf": 0.7, "for_s": 20}}) == []
    assert cond.check({"all": [{"building": {}}, {"track_changed": {"within_s": 60}}]}) == []
    assert cond.check({"mood_in": {}}) and cond.check({"track_changed": {"within_s": 0}})
    assert cond.check({"building": {"moods": ["calm"]}})                              # not a param of building
    e.now += 1.0                                                                      # past the tick's memoised reads
    e.feat(gate=0.5)
    e.mc = ml_context(e, phase="build", track_id=2, track_age=3.0, track_via="dsp")
    ok, info = cond.holds({"all": [{"building": True}, {"track_changed": {}}]}, "p", e.now)
    assert ok is True and set(info["words"]) == {"building", "track_changed"}
    d = cond.describe({"any": [{"mood_in": {"moods": ["calm"]}}, {"building": {"for_s": 10}}]}, "x", e.now)
    assert d["text"] == "any of: the music feels calm or (the music is building, for at least 10 s)"
