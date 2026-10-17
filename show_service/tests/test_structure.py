"""structure.py (README S4.7): sections, repeats, predictions, track changes, phases, GOBO, the genre rule and
music.context, on synthetic structures driven through fake BrainContext readers at 10 Hz (no sockets, no devices, no
worker). The real-music smoke decodes a test track (ffmpeg) into the dsp fallback's descriptors; skipped without it.
Costs print with -s."""

from __future__ import annotations

import json
import math
import pathlib
import shutil
import subprocess
import time
from typing import Any, Callable, Optional

import numpy as np
import pytest
import yaml

from show_service import contract as c
from show_service import structure as st

DIM = 1280
SONOS = "sonos.zone.now_playing"


# -- the rig --------------------------------------------------------------------------------------------------------------
class Handle:
    def __init__(self, when: float, cb: Callable, args: tuple) -> None:
        self.when, self.cb, self.args, self.cancelled, self.done = when, cb, args, False, False

    def cancel(self) -> None:
        self.cancelled = True


class FakeLoop:
    def __init__(self, rig: "Rig") -> None:
        self.rig = rig
        self.handles: list[Handle] = []

    def time(self) -> float:
        return self.rig.t

    def call_at(self, when: float, cb: Callable, *args: Any) -> Handle:
        h = Handle(when, cb, args)
        self.handles.append(h)
        return h

    def run_due(self) -> None:
        for h in sorted(self.handles, key=lambda h: h.when):
            if not h.done and not h.cancelled and h.when <= self.rig.t + 1e-9:
                h.done = True
                h.cb(*h.args)


def clock_body(t: float, bpm: float = 120.0, gate: str = "beat", bar_conf: float = 0.9,
               phrase_conf: float = 0.9) -> dict:
    return {"bpm": bpm, "anchor_ms": 0.0, "beat_at_anchor": 0, "beats_per_bar": 4, "bar_anchor_beat": 0,
            "phrase_bars": 8, "conf": 0.9, "bar_conf": bar_conf, "phrase_conf": phrase_conf, "state": "locked",
            "gate": gate, "epoch": 1, "stability": 0.9, "source": "madmom", "t_ms": t * 1000.0}


def unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


class Rig:
    """Drives one Structure at 10 Hz. ``scene(t)`` returns a dict of what the room sounds like at t:
    {"vec": letter or None, "energy", "lufs", "bright", "onset", "low", "perc", "gate", "chroma": 12, "mfcc": 13}."""

    def __init__(self, scene: Callable[[float], dict], *, settings: Optional[dict] = None, ml: bool = True,
                 clock: Optional[Callable[[float], Optional[dict]]] = None, seed: int = 1, noise: float = 0.3,
                 stamp_offset: float = 0.0, loop: bool = True) -> None:
        self.t = 0.0
        self.scene = scene
        self.ml = ml
        self.clock_fn = clock or (lambda t: None)
        self.rng = np.random.default_rng(seed)
        self.noise = noise
        self.stamp_offset = stamp_offset
        self.bases: dict[str, np.ndarray] = {}
        self.published: list[dict] = []
        self.events: list[dict] = []
        self.gobo_at: list[float] = []
        self.emb: list = []
        self.emb_id = 0
        self.next_emb = 1.0 + stamp_offset
        self.room: dict = {"reachable": True, "members": {}, "screens": {}}
        self.sits: dict = {}
        self.beliefs: Optional[dict] = None
        self.harmony: Optional[dict] = None
        self.ml_extra: dict = {}
        self.each: Optional[Callable[[float], None]] = None   # called with t before each tick's frame
        ctx = c.BrainContext(publish=self.published.append)
        ctx.features = lambda: self.features
        ctx.rhythm = lambda: self.rhythm
        ctx.clock = lambda: self.clock_fn(self.t)
        ctx.ml = lambda: self.view if self.ml else None
        ctx.room = lambda: self.room
        ctx.situations = lambda: self.sits
        ctx.beliefs = lambda: self.beliefs
        ctx.harmony = lambda: self.harmony
        ctx.section_change = lambda: self.gobo_at.append(self.t)
        ctx.on_music_event.append(self.events.append)
        self.loop = FakeLoop(self)
        ctx.loop = self.loop if loop else None
        self.ctx = ctx
        self.features: Optional[dict] = None
        self.rhythm: Optional[dict] = None
        self.view: Optional[dict] = None
        self.s = st.Structure(ctx, mono=lambda: self.t)
        self.s.configure(settings if settings is not None else {})

    def base(self, letter: str) -> np.ndarray:
        if letter not in self.bases:
            self.bases[letter] = unit(self.rng.standard_normal(DIM))
        return self.bases[letter]

    def _frame(self, t: float) -> None:
        sc = self.scene(t)
        gate = sc.get("gate", 0.8)
        self.features = {"t_ms": t * 1000.0, "stream": "test-audio", "lufs_m": sc.get("lufs", -20.0),
                         "lufs_s": sc.get("lufs_s", sc.get("lufs", -20.0)), "brightness": sc.get("bright", 0.4),
                         "gate": gate, "energy": sc.get("energy", 0.5),
                         "bands": {b: {"level": sc.get("band", 0.5), "hits": 0, "presence": 0.5}
                                   for b in c.BAND_NAMES}}
        self.rhythm = {"t_ms": t * 1000.0, "stream": "test-audio", "chroma12": list(sc.get("chroma", [0.0] * 12)),
                       "chroma12_avg": list(sc.get("chroma", [0.0] * 12)), "harmonic_db": -30.0,
                       "percussive_ratio": sc.get("perc", 0.5), "harm_change": 0.1, "onset_rate": sc.get("onset", 4.0),
                       "density": 0.5, "pulse_clarity": 0.6, "swing": None, "syncopation": None,
                       "low_db": sc.get("low", -20.0), "mfcc13": list(sc.get("mfcc", [1.0] * 13)), "gate": gate}
        # the worker: one embedding per second, its window ending at next_emb (stamp = t_end - 1.05)
        while self.ml and self.next_emb <= t + 1e-9:
            te = self.next_emb
            self.next_emb += 1.0
            s2 = self.scene(te - 1.05)
            if s2.get("vec") is None or s2.get("gate", 0.8) < 0.05:
                continue
            self.emb_id += 1
            v = unit(self.base(s2["vec"]) + self.noise * self.rng.standard_normal(DIM) / math.sqrt(DIM))
            self.emb.append((self.emb_id, te, v.astype(np.float32)))
        self.emb = [e for e in self.emb if e[1] >= t - 30.0]
        tags = None
        if self.emb:
            tags = {"type": "tags", "t": self.emb[-1][1], "window_s": 2.1, "stream": "test-audio", "seg": 1,
                    "genre_parents": sc.get("parents", {}), "styles_top3": [], "moods_top5": [],
                    "instruments_top5": sc.get("instruments", [["piano", 0.5]]), "dance": sc.get("dance", 0.7),
                    "voice_p": 0.2,
                    "engagement": 0.6, "bright": 0.5, "acoustic": 0.3, "arousal": 0.6, "emb_id": self.emb_id,
                    "emb": "", "emb_dim": DIM, "cost_ms": 5.0}
        self.view = {"state": "running", "why": None, "stream": sc.get("stream", "test-audio"), "ced": None,
                     "ced_recent": [], "tags": tags, "emb_recent": list(self.emb), "key": None, "beats": None,
                     "vote": None, "ages": {}, "hello": {"tasks": {"ced": True, "tags": True, "key": True,
                                                                    "beats": True}}, **self.ml_extra}

    def run(self, until: float, dt: float = 0.1) -> None:
        while self.t < until - 1e-9:
            self.t = round(self.t + dt, 6)
            if self.each is not None:
                self.each(self.t)
            self._frame(self.t)
            self.s.tick(self.t)
            self.loop.run_due()

    def kinds(self, kind: str) -> list[dict]:
        return [e for e in self.events if e["kind"] == kind]

    def contexts(self) -> list[dict]:
        return [m["data"] for m in self.published if m["type"] == c.MUSIC_CONTEXT]


def abab(t: float, length: float = 30.0, letters: str = "ABAB", energies: Optional[dict] = None) -> dict:
    i = min(int(t // length), len(letters) - 1)
    L = letters[i]
    e = (energies or {"A": 0.4, "B": 0.8}).get(L, 0.5)
    return {"vec": L, "energy": e}


# -- novelty and the pure helpers -------------------------------------------------------------------------------------------
def test_novelty_is_zero_on_a_steady_sequence_and_high_on_a_change():
    a = unit(np.ones(8))
    b = unit(np.arange(8.0) - 3.5)
    steady = np.stack([a] * 20)
    assert st.novelty(steady, 4, 15) == pytest.approx(0.0, abs=1e-9)
    change = np.stack([a] * 15 + [b] * 4)
    assert st.novelty(change, 4, 15) > 0.9
    assert st.novelty(np.stack([a] * 5), 4, 15) == 0.0          # needs recent + 2 rows
    assert st.novelty(np.zeros((20, 8)), 4, 15) == 0.0          # a zero mean: no novelty, no NaN


# -- sections: A/B/A/B -------------------------------------------------------------------------------------------------------
def test_abab_boundaries_letters_repeats_and_the_chorus():
    r = Rig(lambda t: abab(t))
    r.run(122.0)
    bounds = [b for b in r.s.snapshot()["boundaries"] if b["kind"] == "confirmed" and b.get("section_id") is not None]
    truth = [30.0, 60.0, 90.0]
    assert len(bounds) == 3, r.s.snapshot()["boundaries"]
    for b, want in zip(bounds, truth):
        assert abs(b["t_s"] - want) <= 1.0, (b, want)               # before snapping (no grid: snapped == t_s)
    secs = r.s.snapshot()["sections"]
    assert [s["letter"] for s in secs] == ["A", "B", "A", "B"]
    assert secs[2]["repeat_of"] == secs[0]["id"] and secs[3]["repeat_of"] == secs[1]["id"]
    assert secs[0]["repeat_of"] is None and secs[1]["repeat_of"] is None
    assert r.s.chorus == "B"                                         # the louder of the two repeated letters
    ctx = r.s.context()
    assert ctx["section"]["letter"] == "B" and ctx["section"]["chorus"] is True
    assert ctx["source"] == "ml"
    sc = r.kinds("section_change")
    assert len(sc) == 3 and all(e["predicted"] is False and e["detail"] == "section" for e in sc)
    # confirmed: at_ms is now + the min lead (no grid), published about 4-5 s after the change
    for e, want in zip(sc, truth):
        assert e["at_ms"] - e["t_ms"] == pytest.approx(1100.0, abs=1.0)
        assert 3.0 <= e["t_ms"] / 1000.0 - want <= 6.5
        assert abs(e["boundary_ms"] / 1000.0 - want) <= 1.0


def test_section_ids_count_up_and_the_snapshot_is_json_safe():
    r = Rig(lambda t: abab(t))
    r.run(70.0)
    snap = r.s.snapshot()
    json.dumps(snap)
    for k in ("context", "source", "sections", "novelty", "threshold", "boundaries", "build", "events", "gobo",
              "stats"):
        assert k in snap, k
    assert set(snap["gobo"]) >= {"steps", "skipped_cut"}
    assert set(snap["stats"]) == {"tick_ms_p95", "second_ms_p95"}
    ids = [s["id"] for s in snap["sections"]]
    assert ids == sorted(ids) and ids[0] == 0
    assert len(snap["novelty"]) >= 60 and all(len(p) == 2 for p in snap["novelty"])


# -- snapping, predictions, the minimum length ------------------------------------------------------------------------------
def test_boundaries_snap_to_the_clock_bar_lines():
    # 120 bpm, 4/4: a bar every 2 s. The changes are 0.3 s after bar lines 32, 64.
    r = Rig(lambda t: abab(t - 0.3 if t > 1 else t, length=32.0, letters="ABA"), clock=clock_body)
    r.run(80.0)
    bounds = [b for b in r.s.snapshot()["boundaries"] if b["kind"] == "confirmed" and b.get("section_id") is not None]
    assert [b["snapped"] for b in bounds] == [32.0, 64.0]
    assert all(b["to"] == "bar" for b in bounds)
    sc = r.kinds("section_change")
    for e in sc:
        assert e["at_ms"] % 2000.0 == pytest.approx(0.0, abs=0.5)        # lands on a bar line...
        assert e["at_ms"] - e["t_ms"] >= 1100.0 - 1e-6                   # ...at least the min lead ahead
        assert "bar" in e and "beat" in e


def test_a_known_repeat_is_predicted_on_its_line_and_the_late_boundary_is_the_same():
    r = Rig(lambda t: abab(t - 0.3 if t > 1 else t, length=32.0, letters="ABAB"), clock=clock_body)
    r.run(112.0)
    secs = r.s.snapshot()["sections"]
    assert [s["letter"] for s in secs] == ["A", "B", "A", "B"]
    assert secs[0]["bars"] == 16 and secs[2]["expected_bars"] == 16
    sc = r.kinds("section_change")
    pred = [e for e in sc if e["predicted"]]
    assert len(pred) == 1
    p = pred[0]
    assert p["at_ms"] == pytest.approx(96000.0) and p["detail"] == "repeat"
    assert p["letter"] == "B" and p["repeat_of"] == secs[1]["id"]          # what followed A the first time
    assert 1100.0 <= p["at_ms"] - p["t_ms"] <= 2000.0 + 100.0             # announced <= 1 bar, >= the min lead ahead
    assert p["section_id"] == secs[3]["id"] and secs[3]["kind"] == "predicted"
    assert secs[3]["start_s"] == pytest.approx(96.0)
    # the novelty boundary confirmed ~5 s later is the same one: no second event
    assert len([e for e in sc if not e["predicted"]]) == 2
    same = [b for b in r.s.snapshot()["boundaries"] if b.get("same_as") == secs[3]["id"]]
    assert same and abs(same[0]["snapped"] - 96.0) < 1e-6
    preds = [b for b in r.s.snapshot()["boundaries"] if b["kind"] == "predicted"]
    assert preds[0]["matched"] is True and preds[0]["missed"] is False
    # GOBO stepped at the predicted line itself
    assert any(abs(t - 96.0) < 1e-6 for t in r.gobo_at)


def test_a_predicted_line_with_no_boundary_counts_as_missed():
    # A B A then A stays (no B at 96): the prediction lands, nothing confirms it
    r = Rig(lambda t: abab(t - 0.3 if t > 1 else t, length=32.0, letters="ABAAA"), clock=clock_body)
    r.run(118.0)
    preds = [b for b in r.s.snapshot()["boundaries"] if b["kind"] == "predicted"]
    assert len(preds) == 1 and preds[0]["missed"] is True


def test_a_confirmed_boundary_before_a_predicted_line_cancels_it():
    # A B A, then C arrives 6 bars before A's predicted end (96): the evidence wins, no 3-bar section at 96
    def scene(t: float) -> dict:
        t = t - 0.3 if t > 1 else t
        return {"vec": "C"} if t >= 84.0 else abab(t, length=32.0, letters="ABA")
    r = Rig(scene, clock=clock_body)
    r.s.cfg["predict_lead_bars"] = 8                                         # announce early enough to collide
    r.run(110.0)
    secs = r.s.snapshot()["sections"]
    assert [s["start_s"] for s in secs] == [pytest.approx(x) for x in (0.1, 32.0, 64.0, 84.0)]
    assert all(s["kind"] != "predicted" for s in secs)
    pred = [e for e in r.kinds("section_change") if e["predicted"]]
    assert len(pred) == 1 and pred[0]["at_ms"] == pytest.approx(96000.0)       # announced, then overtaken
    assert r.s.snapshot()["gobo"]["steps"] == 3                              # the cancelled line stepped nothing


def test_a_boundary_closer_than_the_minimum_section_is_dropped():
    # B, then a 6 s blip of C 8 s into it (4 bars < min_section_bars 8)
    def scene(t: float) -> dict:
        if t < 30:
            return {"vec": "A"}
        if 38 <= t < 44:
            return {"vec": "C"}
        return {"vec": "B"}
    r = Rig(scene, clock=lambda t: clock_body(t))
    r.run(70.0)
    b = r.s.snapshot()["boundaries"]
    assert any(x.get("dropped") for x in b), b
    assert len(r.kinds("section_change")) == 1


# -- track changes ---------------------------------------------------------------------------------------------------------------
def test_a_sonos_track_change_is_the_truth_at_once():
    r = Rig(lambda t: abab(t, letters="AB"), settings={"situations": {"sonos": {"now_playing": SONOS}}})
    r.room["members"][SONOS] = {"believed": {"state": "playing", "track_id": "x:1", "title": "One", "artist": "a"},
                                "confidence": "confirmed", "age_s": 0.0, "sent_age_s": None}
    r.sits = {"sonos_playing": {"value": True, "since_s": 10.0, "via": "sonos", "evidence": {}}}
    r.run(45.0)
    assert not r.kinds("track_change")
    letters_before = [s["letter"] for s in r.s.snapshot()["sections"]]
    assert letters_before[:2] == ["A", "B"]
    r.room["members"][SONOS]["believed"] = {"state": "playing", "track_id": "x:2", "title": "Two", "artist": "b"}
    r.run(45.2)
    tc = r.kinds("track_change")
    assert len(tc) == 1 and tc[0]["via"] == "sonos" and tc[0]["track_id"] == 1
    assert tc[0]["at_ms"] == pytest.approx(45100.0, abs=101)
    sc = r.kinds("section_change")[-1]
    assert sc["detail"] == "track" and sc["seq"] > tc[0]["seq"]
    r.run(46.5)
    ctxs = r.contexts()
    changed = [x["track"]["changed"] for x in ctxs if x["track"]["id"] == 1]
    assert changed[0] is True and all(v is False for v in changed[1:])
    secs = r.s.snapshot()["sections"]
    assert len(secs) == 1 and secs[0]["kind"] == "track"               # the memory is cleared
    r.run(60.0)
    assert r.s.snapshot()["sections"][0]["letter"] == "A"               # letters restart at A


def test_a_paused_sonos_doesnt_change_the_track_until_it_plays():
    r = Rig(lambda t: {"vec": "A"}, settings={"situations": {"sonos": {"now_playing": SONOS}}})
    r.room["members"][SONOS] = {"believed": {"track_id": "x:1"}, "confidence": "confirmed"}
    r.sits = {"sonos_playing": {"value": True}}
    r.run(5.0)
    r.sits = {"sonos_playing": {"value": False}}
    r.room["members"][SONOS]["believed"] = {"track_id": "x:2"}
    r.run(8.0)
    assert not r.kinds("track_change")
    r.sits = {"sonos_playing": {"value": True}}
    r.run(9.0)
    assert len(r.kinds("track_change")) == 1


def test_a_gap_then_a_boundary_with_a_tempo_jump_is_a_new_track():
    def scene(t: float) -> dict:
        if t < 40:
            return {"vec": "A"}
        if t < 44.5:
            return {"vec": None, "gate": 0.0}
        return {"vec": "C"}
    r = Rig(scene, clock=lambda t: clock_body(t, bpm=120.0 if t < 44 else 140.0))
    r.run(60.0)
    tc = r.kinds("track_change")
    assert len(tc) == 1 and tc[0]["via"] == "dsp" and tc[0]["track_id"] == 1
    assert 44.0 <= tc[0]["at_ms"] / 1000.0 <= 46.5
    b = [x for x in r.s.snapshot()["boundaries"] if x["kind"] == "track"]
    assert b and "tempo" in b[0]["why"]
    assert r.s.context()["track"]["id"] == 1 and r.s.context()["track"]["via"] == "dsp"


def test_a_gap_is_heard_by_the_momentary_loudness_before_the_slow_gate_falls():
    """The gate follows the 3 s loudness, so it reads silence only ~3 s into it: a 4 s gap between songs is heard by
    lufs_m (400 ms) instead (structure.track.gap_lufs_m)."""
    def scene(t: float) -> dict:
        if t < 40:
            return {"vec": "A"}
        if t < 44:
            return {"vec": None, "lufs": -70.0, "gate": 0.3 if t < 43 else 0.0}   # the gate still up for 3 s
        return {"vec": "C"}
    r = Rig(scene, clock=lambda t: clock_body(t, bpm=120.0 if t < 44 else 140.0))
    r.run(60.0)
    tc = r.kinds("track_change")
    assert len(tc) == 1 and tc[0]["via"] == "dsp"
    b = [x for x in r.s.snapshot()["boundaries"] if x["kind"] == "track"]
    assert b and b[0]["why"].startswith("after a gap")


def test_a_jump_that_shows_after_the_post_gap_boundary_adopts_its_section():
    # the clock re-locks to the new tempo only at 52 s, after the boundary (~44.5) was confirmed (~49)
    def scene(t: float) -> dict:
        if t < 40:
            return {"vec": "A"}
        if t < 44.5:
            return {"vec": None, "gate": 0.0}
        return {"vec": "C"}
    r = Rig(scene, clock=lambda t: clock_body(t, bpm=120.0 if t < 52 else 140.0))
    r.run(60.0)
    sc = r.kinds("section_change")
    tc = r.kinds("track_change")
    assert len(sc) == 1 and len(tc) == 1 and tc[0]["seq"] > sc[0]["seq"]
    assert tc[0]["via"] == "dsp" and 52.0 <= tc[0]["t_ms"] / 1000.0 <= 54.0
    secs = r.s.snapshot()["sections"]
    assert len(secs) == 1 and secs[0]["id"] == sc[0]["section_id"] and secs[0]["kind"] == "track"   # adopted
    assert secs[0]["letter"] == "A"                                         # named afresh in the new track


def test_a_gap_with_the_same_tempo_and_music_is_not_a_new_track():
    def scene(t: float) -> dict:
        if 40 <= t < 44.5:
            return {"vec": None, "gate": 0.0}
        return {"vec": "A"}
    r = Rig(scene, clock=lambda t: clock_body(t, bpm=120.0))
    r.run(70.0)
    assert not r.kinds("track_change")


def test_an_octave_is_not_a_tempo_jump():
    r = Rig(lambda t: {"vec": "A"})
    a = {"bpm": 120.0}
    assert r.s._jump(a, {"bpm": 240.0}) is None and r.s._jump(a, {"bpm": 60.0}) is None
    assert r.s._jump(a, {"bpm": 121.0}) is None
    assert "tempo" in r.s._jump(a, {"bpm": 130.0})
    k1 = {"key": "C", "mode": "major", "key_strength": 0.5}
    assert "key" in r.s._jump(k1, {"key": "F#", "mode": "major", "key_strength": 0.5})
    assert r.s._jump(k1, {"key": "F#", "mode": "major", "key_strength": 0.1}) is None


def test_the_same_parallel_or_relative_key_is_no_key_jump():
    r = Rig(lambda t: {"vec": "A"})
    c = {"key": "C", "mode": "major", "key_strength": 0.6}
    for other in ({"key": "A", "mode": "minor"}, {"key": "C", "mode": "minor"}, {"key": "C", "mode": "major"}):
        assert r.s._jumps(c, {**other, "key_strength": 0.6}) == [], other
    assert r.s._jumps({"key": "A", "mode": "minor", "key_strength": 0.6}, c) == []
    assert r.s._jumps(c, {"key": "G", "mode": "major", "key_strength": 0.6})          # a fifth away: a jump
    assert r.s._jumps(c, {"key": "E", "mode": "minor", "key_strength": 0.6})          # not C's relative


def _spike_rig(*, key_after: str, mode_after: str, bpm_after: float, parents: tuple, settings: Optional[dict] = None
               ) -> "Rig":
    """A to B at 30 s (a strong novelty spike, no gap): the key (music.harmony) and the tempo change at 30 s too;
    the tags' parents are ``parents`` (before, after)."""
    cfg = {"structure": {"key_hold": {"hold_s": 0}}, "genre_map": {"parents": {"Jazz": "jazz", "Rock": "rock"}}}
    for k, v in (settings or {}).items():
        cfg.setdefault(k, {}).update(v)
    r = Rig(lambda t: {"vec": "A" if t < 30 else "B", "parents": parents[0] if t < 30 else parents[1]},
            clock=lambda t: clock_body(t, bpm=120.0 if t < 30 else bpm_after), settings=cfg)
    r.harmony = {"chroma12": [0.0] * 12, "key": "C", "mode": "major", "tension": 0.1, "chord_change": 0.0,
                 "register": 0.5, "spread": 0.0, "t_ms": 0.0, "source": "audio", "conf": 0.6}
    r.run(30.0)
    r.harmony = {**r.harmony, "key": key_after, "mode": mode_after}
    r.run(45.0)
    return r


def test_an_in_song_section_change_with_a_modulation_and_a_near_tied_parent_is_no_track_change():
    near_tie = ({"Jazz": 0.20, "Rock": 0.19}, {"Rock": 0.20, "Jazz": 0.19})
    r = _spike_rig(key_after="G", mode_after="major", bpm_after=120.0, parents=near_tie)
    b = [x for x in r.s.snapshot()["boundaries"] if x["kind"] == "confirmed" and x.get("section_id") is not None]
    assert len(b) == 1 and b[0]["novelty"] >= 2.0 * b[0]["threshold"]      # path (c)'s spike, with a key jump...
    assert not r.kinds("track_change") and r.s.track_id == 0               # ...but one jump only: one song
    assert len(r.kinds("section_change")) == 1


def test_a_no_gap_change_of_tempo_and_key_is_a_new_track_and_spike_k_null_turns_it_off():
    other = ({"Jazz": 0.6, "Rock": 0.1}, {"Jazz": 0.6, "Rock": 0.1})
    r = _spike_rig(key_after="F#", mode_after="major", bpm_after=140.0, parents=other)
    tc = r.kinds("track_change")
    assert len(tc) == 1 and tc[0]["via"] == "dsp"
    b = [x for x in r.s.snapshot()["boundaries"] if x["kind"] == "track"]
    assert b and b[0]["why"].startswith("novelty spike") and "tempo" in b[0]["why"] and "key" in b[0]["why"]
    off = _spike_rig(key_after="F#", mode_after="major", bpm_after=140.0, parents=other,
                     settings={"structure": {"track": {"spike_k": None}}})
    assert off.s.cfg["track"]["spike_k"] is None and off.s.settings_problems == []
    assert not off.kinds("track_change")


# -- the dsp fallback ------------------------------------------------------------------------------------------------------------
CMAJ = [1.0, 0, 0, 0, 1.0, 0, 0, 1.0, 0, 0, 0, 0]
FSMAJ = [0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 1.0, 0]


def test_the_dsp_fallback_finds_a_chroma_change():
    r = Rig(lambda t: {"chroma": CMAJ if t < 30 else FSMAJ, "mfcc": [2.0] * 13}, ml=False)
    r.run(50.0)
    snap = r.s.snapshot()
    assert snap["source"] == "dsp" and r.s.context()["source"] == "dsp"
    bounds = [b for b in snap["boundaries"] if b["kind"] == "confirmed" and b.get("section_id") is not None]
    assert len(bounds) == 1 and abs(bounds[0]["t_s"] - 30.0) <= 1.0
    assert len(r.kinds("section_change")) == 1


def test_a_switch_of_source_clears_the_novelty_history():
    r = Rig(lambda t: {"vec": "A", "chroma": CMAJ}, ml=False)
    r.run(20.0)
    assert r.s.source == "dsp" and len(r.s.vecs) >= 15
    r.ml = True
    r.run(23.0)
    assert r.s.source == "ml" and all(v.shape == (DIM,) for _, v, _ in r.s.vecs)    # no dsp vector kept
    assert len(r.s.vecs) <= 3 + 4                       # 3 s of fresh unseen embeddings, then one a second
    assert not r.kinds("section_change")


def test_no_inputs_at_all_degrades_to_an_empty_context():
    ctx = c.BrainContext(publish=lambda m: None)
    s = st.attach(ctx)
    s.configure({})
    for i in range(30):
        s.tick(100.0 + i * 0.1)
    body = s.context()
    assert body is not None and body["source"] == "none" and body["genre"]["via"] == "none"
    assert body["section"]["id"] == 0 and body["section"]["phase"] == "calm"
    json.dumps(s.snapshot())


# -- build, drop, breakdown ------------------------------------------------------------------------------------------------------
BPM = 128.0
BEAT = 60.0 / BPM


def riser(t: float, drop_at: float = 30.0) -> dict:
    """Steady to 20 s, a 10 s riser (loudness, brightness, onsets up; bass held), the drop at ``drop_at``."""
    sc = {"vec": "A", "lufs": -20.0, "bright": 0.4, "onset": 4.0, "low": -20.0, "perc": 0.5, "energy": 0.5,
          "band": 0.5}
    if 20.0 <= t < drop_at:
        k = t - 20.0
        sc.update(lufs=-20.0 + 0.5 * k, bright=0.4 + 0.012 * k, onset=4.0 + 0.3 * k, low=-20.0 - 0.05 * k,
                  energy=0.6)
    elif t >= drop_at:
        sc.update(lufs=-10.0, bright=0.6, onset=8.0, low=-6.0, vec="C", energy=0.9, band=0.9)
    return sc


def test_a_riser_then_a_bass_jump_is_a_build_an_armed_line_and_a_drop():
    r = Rig(riser, clock=lambda t: clock_body(t, bpm=BPM))
    phases, progress, eta = [], [], []
    while r.t < 40.0:
        r.run(r.t + 0.1)
        sec = r.s.context()["section"] if r.s.context() else None
        if sec:
            phases.append((r.t, sec["phase"]))
            if sec["phase"] == "build":
                progress.append(sec["build_progress"])
                eta.append(sec["drop_eta_beats"])
    b = r.kinds("build")
    assert len(b) == 1 and 18.0 <= b[0]["at_ms"] / 1000.0 <= 22.0 and b[0]["phase"] == "build"
    arm = r.kinds("drop_armed")
    assert arm, "no drop_armed"
    a = arm[-1]
    assert a["at_ms"] == pytest.approx(30000.0, abs=1.0)                  # the 16-bar line (16 x 4 x 60/128 = 30 s)
    assert (a["at_ms"] - a["t_ms"]) / 1000.0 / BEAT >= 2.0                 # at least 2 beats ahead
    assert a["eta_beats"] <= 8.0 + 1e-6
    d = r.kinds("drop")
    assert len(d) == 1 and d[0]["at_ms"] == pytest.approx(30000.0, abs=1.0)    # snapped to the beat
    assert 0.5 < d[0]["strength"] <= 1.0
    assert d[0]["section_id"] == r.s.cur["id"] and r.s.cur["kind"] == "drop"
    assert progress and progress == sorted(progress) and progress[-1] > 0.6
    assert eta and eta[0] > eta[-1]
    assert any(p == "drop" for _, p in phases if p and _ > 30.0)
    # the armed line stepped GOBO; the drop on it didn't step a second one
    assert r.gobo_at == [pytest.approx(30.0)]
    assert not r.kinds("section_change")                                    # the late novelty boundary is the drop's
    assert [b for b in r.s.snapshot()["boundaries"] if b.get("same_as") == d[0]["section_id"]]


def test_no_build_or_drop_without_a_grid():
    r = Rig(riser, clock=lambda t: clock_body(t, bpm=BPM, gate="none"))
    r.run(40.0)
    assert not r.kinds("build") and not r.kinds("drop") and not r.kinds("drop_armed")
    r2 = Rig(riser)                                                         # no clock at all
    r2.run(40.0)
    assert not r2.kinds("build") and not r2.kinds("drop")


def test_the_drop_holds_its_phase_then_becomes_peak_or_calm():
    r = Rig(riser, clock=lambda t: clock_body(t, bpm=BPM))
    r.run(31.0)
    assert r.s.phase == "drop"
    hold = 8 * 4 * BEAT
    r.run(30.0 + hold + 1.0)
    assert r.s.phase == "peak"                                              # energy 0.9 in the drop's section


def breakdown_scene(t: float) -> dict:
    sc = {"vec": "A", "low": -15.0, "perc": 0.5, "gate": 0.8, "energy": 0.6}
    if 35.0 <= t < 47.0:
        sc.update(low=-27.0, perc=0.1, gate=0.6, energy=0.4)
    return sc


def test_a_bass_and_percussion_collapse_with_the_music_on_is_a_breakdown():
    r = Rig(breakdown_scene)
    r.run(40.5)
    bd = r.kinds("breakdown")
    assert len(bd) == 1 and bd[0]["at_ms"] == pytest.approx(35100.0, abs=150.0)
    assert r.s.phase == "breakdown"
    r.run(46.0)
    assert r.s.phase == "breakdown"                                         # the reference is frozen on entry
    r.run(50.5)
    assert r.s.phase == "breakdown"                                         # out only after leave_s (4 s) of failing
    r.run(51.5)
    assert r.s.phase != "breakdown"
    assert len(r.kinds("breakdown")) == 1


def test_silence_is_not_a_breakdown():
    r = Rig(lambda t: {**breakdown_scene(t), "gate": 0.0} if t >= 35.0 else breakdown_scene(t))
    r.run(45.0)
    assert not r.kinds("breakdown")


def test_a_falling_level_at_low_energy_is_the_outro():
    def scene(t: float) -> dict:
        return {"vec": "A", "lufs": -20.0 if t < 30 else -20.0 - 0.6 * (t - 30), "energy": 0.6 if t < 30 else 0.2}
    r = Rig(scene)
    r.run(50.0)
    assert r.s.phase == "outro"


# -- GOBO ------------------------------------------------------------------------------------------------------------------------
def test_gobo_steps_at_each_section_change_s_at_ms():
    r = Rig(lambda t: abab(t))
    r.run(70.0)
    sc = r.kinds("section_change")
    assert len(sc) == 2
    assert r.gobo_at == [pytest.approx(e["at_ms"] / 1000.0, abs=1e-6) for e in sc]
    assert r.s.snapshot()["gobo"]["steps"] == 2


def test_gobo_never_steps_while_a_screen_cuts_and_can_be_turned_off():
    r = Rig(lambda t: abab(t))
    r.room["screens"] = {"screen.x": {"layer": "art", "phase": "cutting", "cutting": True}}
    r.run(40.0)
    assert r.gobo_at == [] and r.s.snapshot()["gobo"]["skipped_cut"] == 1
    r2 = Rig(lambda t: abab(t), settings={"structure": {"gobo_on_sections": False}})
    r2.run(40.0)
    assert r2.gobo_at == [] and len(r2.kinds("section_change")) == 1


def test_gobo_never_twice_within_a_bar_and_works_without_a_loop():
    r = Rig(lambda t: abab(t), loop=False)
    r.run(40.0)
    assert len(r.gobo_at) == 1                                              # stepped from the tick (no loop)
    sc = r.kinds("section_change")[0]
    assert r.gobo_at[0] == pytest.approx(sc["at_ms"] / 1000.0, abs=0.1001)   # within a tick of at_ms
    r.t = r.gobo_at[0] + 1.0
    r.s._gobo_fire(("section", 99))                                         # 1 s later (no grid: a bar is 2 s)
    assert len(r.gobo_at) == 1 and r.s.gobo["skipped_bar"] == 1


def test_gobo_is_the_directors_while_a_vibe_plays():
    """A Dinner-like vibe (change_looks time) plays: a confirmed section steps no GOBO here; the director steps it only
    when it lands a look (README S4.7 "GOBO"). Neutral again: structure steps it."""
    r = Rig(lambda t: abab(t))
    vibe: dict = {"vibe": {"change_looks": "time"}}
    r.ctx.vibe = lambda: vibe["vibe"]
    r.run(40.0)
    assert len(r.kinds("section_change")) == 1                              # the section is still announced...
    assert r.gobo_at == [] and r.s.snapshot()["gobo"]["director"] == 1      # ...but the screens' scene is the director's
    vibe["vibe"] = None                                                     # neutral: §9.5's P0 path
    r.run(70.0)
    assert len(r.kinds("section_change")) == 2 and len(r.gobo_at) == 1


def test_an_unarmed_drop_steps_gobo_on_a_bar_the_screens_can_make():
    r = Rig(lambda t: riser(t, drop_at=25.0), clock=lambda t: clock_body(t, bpm=BPM))
    r.run(27.0)
    d = r.kinds("drop")
    assert len(d) == 1 and not r.kinds("drop_armed")
    h = [x for x in r.loop.handles if x.args and x.args[0] == ("section", d[0]["section_id"])]
    assert len(h) == 1
    bar = 4 * BEAT
    assert h[0].when - d[0]["t_ms"] / 1000.0 >= 1.1 - 1e-6                  # at least events.min_lead_s ahead...
    assert abs(h[0].when / bar - round(h[0].when / bar)) < 1e-6             # ...on a bar line


def _quiet_from(t_quiet: float):
    def scene(t: float) -> dict:
        return {**riser(t, drop_at=99.0), "vec": None, "gate": 0.0} if t >= t_quiet else riser(t, drop_at=99.0)
    return scene


def test_a_build_that_gives_out_well_before_its_line_calls_it_off():
    r = Rig(_quiet_from(27.0), clock=lambda t: clock_body(t, bpm=BPM))
    r.run(32.0)
    arm = r.kinds("drop_armed")
    assert len(arm) == 2 and arm[0]["eta_beats"] > 0 and arm[0]["at_ms"] == pytest.approx(30000.0, abs=1.0)
    off = arm[1]
    assert off["eta_beats"] is None and off["detail"] == "disarmed" and off["at_ms"] == arm[0]["at_ms"]
    assert 27.0 <= off["t_ms"] / 1000.0 <= 27.3                             # at once, 1.5 bars before the line
    assert r.gobo_at == [] and r.s.armed is None                            # the line's GOBO is cancelled


def test_a_pre_drop_break_keeps_the_line_and_a_line_with_no_drop_is_missed():
    r = Rig(_quiet_from(28.7), clock=lambda t: clock_body(t, bpm=BPM))     # 1.3 s (< 1 bar) before the line
    r.run(33.0)
    arm = r.kinds("drop_armed")
    assert [a["eta_beats"] is None for a in arm] == [False, True]
    assert arm[1]["detail"] == "missed" and arm[1]["at_ms"] == pytest.approx(30000.0, abs=1.0)
    assert arm[1]["t_ms"] / 1000.0 >= 30.0 + BEAT + 0.5 - 1e-6             # after the window a drop could come in
    assert r.gobo_at == [pytest.approx(30.0)]                               # the predictive landing stands


# -- the genre rule (S4.8) ---------------------------------------------------------------------------------------------------------
GCFG = {"parents": {"Jazz": "jazz", "Funk / Soul": "soul", "Electronic": {"from_listener": ["house", "techno"]}},
        "tau_s": 30.0, "freeze_below_music": 0.5, "speech_scale_above": 0.3, "switch_margin": 0.1,
        "switch_dwell_s": 20.0}


def fresh(word: str, conf: float = 0.8) -> dict:
    return {"state": "fresh", "genre": {"word": word, "conf": conf}}


def test_genre_maps_the_leading_parent_through_the_table():
    state: dict = {}
    g = st.genre_reading({"Jazz": 0.8, "Rock": 0.2}, None, {"music": 0.9, "speech": 0.0}, GCFG, state, now_s=0.0)
    assert g == {"word": "jazz", "conf": 0.8, "parent": "Jazz", "parent_p": 0.8, "via": "ml"}


def test_genre_switches_only_after_the_margin_and_the_dwell():
    state: dict = {}
    st.genre_reading({"Jazz": 0.8, "Funk / Soul": 0.1}, None, None, GCFG, state, now_s=0.0)
    t = 0.0
    seen = []
    while t < 200.0:
        t += 1.0
        g = st.genre_reading({"Jazz": 0.1, "Funk / Soul": 0.8}, None, None, GCFG, state, now_s=t)
        seen.append((t, g["parent"]))
    first = next(tt for tt, p in seen if p == "Funk / Soul")
    # the EMA (tau 30 s) crosses with the 0.1 margin after ~20 s, then 20 s of dwell
    assert 35.0 <= first <= 50.0
    assert seen[-1][1] == "Funk / Soul"


def test_genre_from_listener_and_unmapped_parents():
    state: dict = {}
    g = st.genre_reading({"Electronic": 0.9}, fresh("house", 0.5), None, GCFG, state, now_s=0.0)
    assert g["word"] == "house" and g["via"] == "ml+listener" and g["conf"] == pytest.approx(0.45)
    g = st.genre_reading({"Electronic": 0.9}, fresh("pop"), None, GCFG, {}, now_s=0.0)
    assert g["word"] is None and g["conf"] == 0.0 and g["parent"] == "Electronic"   # not one of its words
    g = st.genre_reading({"Electronic": 0.9}, {"state": "fading", "genre": {"word": "house", "conf": 0.9}}, None,
                         GCFG, {}, now_s=0.0)
    assert g["word"] is None                                                   # a fading listener doesn't count
    g = st.genre_reading({"Hip Hop": 0.9}, fresh("soul"), None, GCFG, {}, now_s=0.0)
    assert g["word"] is None and g["parent"] == "Hip Hop" and g["via"] == "ml"   # not in the table: no word


def test_genre_speech_scales_conf_and_low_music_freezes_it():
    g = st.genre_reading({"Jazz": 0.8}, None, {"music": 0.9, "speech": 0.65}, GCFG, {}, now_s=0.0)
    assert g["conf"] == pytest.approx(0.8 * 0.5, abs=1e-3)                    # speech half-way from 0.3 to 1
    state: dict = {}
    st.genre_reading({"Jazz": 0.8}, None, {"music": 0.9, "speech": 0.0}, GCFG, state, now_s=0.0)
    g = st.genre_reading({"Jazz": 0.0, "Funk / Soul": 1.0}, None, {"music": 0.2, "speech": 0.0}, GCFG, state,
                         now_s=60.0)
    assert g["parent"] == "Jazz" and g["parent_p"] == pytest.approx(0.8)      # frozen


def test_genre_without_the_worker_is_the_listener_then_nothing():
    g = st.genre_reading(None, fresh("soul", 0.7), None, GCFG, {}, now_s=0.0)
    assert g == {"word": "soul", "conf": 0.7, "parent": None, "parent_p": 0.0, "via": "listener"}
    g = st.genre_reading(None, None, None, GCFG, {}, now_s=0.0)
    assert g == {"word": None, "conf": 0.0, "parent": None, "parent_p": 0.0, "via": "none"}
    assert set(g) == set(c.GenreReading.__annotations__)


def test_the_context_carries_the_genre_from_the_table_in_show_yaml():
    settings = yaml.safe_load(c.SHOW_FILE.read_text())
    parent = next(p for p, w in settings["genre_map"]["parents"].items() if isinstance(w, str))
    r = Rig(lambda t: {"vec": "A", "parents": {parent: 0.9}}, settings=settings)
    r.run(5.0)
    g = r.s.context()["genre"]
    assert g["parent"] == parent and g["word"] == settings["genre_map"]["parents"][parent] and g["via"] == "ml"


# -- music.context's shape and the inputs it fuses ----------------------------------------------------------------------------------
def test_music_context_has_every_key_and_is_json_safe():
    r = Rig(lambda t: abab(t))
    r.ml_extra = {"ced_recent": [{"type": "ced", "t": 0.0, "window_s": 2.0, "stream": "test-audio", "seg": 1,
                                  **{f"{w}_p": 0.1 for w in c.CED_GROUPS}, "cost_ms": 1.0}]}
    r.beliefs = {"state": "fresh", "age_s": 1.0, "genre": {"word": "soul", "conf": 0.7},
                 "moods": [{"word": "warm", "conf": 0.8}, {"word": "dreamy", "conf": 0.3}], "instruments": ["piano"],
                 "vocals": None, "section": {"label": "chorus", "conf": 0.9}, "trajectory": None, "content": "music"}
    r.sits = {"talking": {"value": False, "since_s": 3.0, "via": "classifier", "evidence": {}}}
    r.run(40.0)
    ctxs = r.contexts()
    assert len(ctxs) == pytest.approx(40, abs=1)                               # 1 Hz
    body = ctxs[-1]
    assert set(body) == set(c.MusicContext.__annotations__)
    assert set(body["section"]) == set(c.SectionInfo.__annotations__)
    assert set(body["track"]) == set(c.TrackInfo.__annotations__)
    assert set(body["genre"]) == set(c.GenreReading.__annotations__)
    assert set(body["ml"]) == {"state", "styles_top3", "moods_top5", "instruments_top5", "ages"}
    assert body["section"]["phase"] in c.SECTION_PHASES
    assert body["moods"] == ["warm"] and body["talking"] is False
    assert body["instruments"] == ["piano"]                                    # the ML's while fresh
    assert body["dance"] == pytest.approx(0.7) and body["vocal"] == pytest.approx(0.2)
    assert body["section"]["label"] == "chorus"
    json.dumps(body, allow_nan=False)
    for e in r.events:
        assert set(e) <= set(c.MusicEventData.__annotations__), e
        assert e["kind"] in c.MUSIC_EVENT_KINDS
    pub = [m for m in r.published if m["type"] == c.ROOM_EVENT]
    assert len(pub) == len(r.events)


def _with_key(r: "Rig", until: float, key: Optional[dict]) -> None:
    """Run with the worker's key line fresh every tick (``key`` None: no key line)."""
    while r.t < until - 1e-9:
        r.ml_extra = {"key": {"type": "key", "window_s": 20.0, "stream": "test-audio", "seg": 1, "cost_ms": 2.0,
                              **key, "t": r.t}} if key else {}
        r.run(r.t + 0.1)


def test_the_key_prefers_the_piano_then_the_worker_then_harmony():
    r = Rig(lambda t: {"vec": "A"})
    r.harmony = {"chroma12": [0.0] * 12, "key": "A", "mode": "minor", "tension": 0.1, "chord_change": 0.0,
                 "register": 0.5, "spread": 0.0, "t_ms": 0.0, "source": "audio", "conf": 0.4}
    r.run(3.0)
    assert (r.s.context()["key"], r.s.context()["key_via"]) == ("A", "audio")      # the first key shows at once
    d_major = {"key": "D", "mode": "major", "strength": 0.6}
    _with_key(r, 5.0, d_major)
    assert (r.s.context()["key"], r.s.context()["key_via"]) == ("A", "audio")      # another key waits out hold_s
    _with_key(r, 8.5, d_major)
    assert (r.s.context()["key"], r.s.context()["key_via"]) == ("D", "ml")
    _with_key(r, 12.0, {**d_major, "strength": 0.27})                               # under 0.3, within the margin:
    assert (r.s.context()["key"], r.s.context()["key_via"]) == ("D", "ml")          # the source sticks
    r.harmony = {**r.harmony, "source": "midi", "key": "G", "mode": "major", "conf": 0.9}
    _with_key(r, 13.0, d_major)
    assert (r.s.context()["key"], r.s.context()["key_via"]) == ("G", "midi")        # the piano's at once


def test_a_flickering_key_never_reaches_music_context():
    r = Rig(lambda t: {"vec": "A"})
    _with_key(r, 4.0, {"key": "C", "mode": "major", "strength": 0.5})
    assert r.s.context()["key"] == "C"
    shown = set()
    for i in range(12):                                   # E minor and C major take turns every 2 s for 24 s
        k = {"key": "E", "mode": "minor", "strength": 0.5} if i % 2 == 0 else {"key": "C", "mode": "major",
                                                                                "strength": 0.5}
        _with_key(r, r.t + 2.0, k)
        shown.add(r.s.context()["key"])
    assert shown == {"C"}


def test_tag_numbers_freeze_while_ced_hears_no_music():
    r = Rig(lambda t: {"vec": "A", "dance": 0.8})
    r.run(10.0)
    before = r.s.context()["dance"]
    r.ml_extra = {"ced_recent": [{"t": r.t + k, **{f"{w}_p": 0.0 for w in c.CED_GROUPS}, "music_p": 0.1}
                                 for k in range(0, 20)]}
    r.scene = lambda t: {"vec": "A", "dance": 0.0}
    r.run(15.0)
    assert r.s.context()["dance"] == pytest.approx(before, abs=1e-6)


def test_another_ml_stream_carries_over_no_genre_numbers_or_instruments():
    settings = {"genre_map": {"parents": {"Jazz": "jazz", "Rock": "rock"}}}
    r = Rig(lambda t: {"vec": "A", "parents": {"Jazz": 0.9, "Rock": 0.1}, "dance": 0.9,
                       "instruments": [["sax", 0.8]]}, settings=settings)
    r.run(30.0)
    assert r.s.context()["genre"]["word"] == "jazz" and r.s.context()["instruments"] == ["sax"]
    r.scene = lambda t: {"vec": "A", "parents": {"Rock": 0.4, "Jazz": 0.05}, "dance": 0.2,
                         "instruments": [["piano", 0.6]], "stream": "room-mic"}
    r.run(32.0)
    ctx = r.s.context()
    assert ctx["genre"]["word"] == "rock" and ctx["genre"]["parent"] == "Rock"     # not the test track's jazz
    assert ctx["dance"] == pytest.approx(0.2) and ctx["instruments"] == ["piano"]


def test_instruments_are_smoothed_so_one_window_never_reorders_them():
    def scene(t: float) -> dict:
        blip = 20.0 <= t < 21.0                                             # one window: guitar on top, no piano
        return {"vec": "A", "instruments": [["guitar", 0.9]] if blip else [["piano", 0.8], ["guitar", 0.3]]}
    r = Rig(scene)
    firsts = []
    while r.t < 30.0:
        r.run(r.t + 1.0)
        firsts.append(r.s.context()["instruments"][0])
    assert set(firsts) == {"piano"} and r.s.context()["instruments"] == ["piano", "guitar"]


def test_freshness_follows_the_workers_stretched_cadence():
    r = Rig(lambda t: {"vec": "A"})
    r.run(20.0)
    old = [{"type": "ced", "t": r.t - 5.0, "window_s": 2.0, "stream": "test-audio", "seg": 1,
            **{f"{w}_p": 0.0 for w in c.CED_GROUPS}, "music_p": 0.9, "cost_ms": 1.0}]
    r.ml_extra = {"ced_recent": old}
    r.run(21.0)
    assert r.s.context()["ced"] is None                                     # 5-6 s old: stale at results_stale_s 3
    r.ml_extra = {"ced_recent": old, "stale_s": {"ced": 12.0, "tags": 3.0, "key": 3.0, "beats": 15.0}}
    r.run(22.0)
    assert r.s.context()["ced"]["music"] == pytest.approx(0.9)              # the worker says CED comes every 8 s now


def test_genre_speech_scale_counts_babble_and_conversation():
    g = st.genre_reading({"Jazz": 0.8}, None, {"music": 0.9, "speech": 0.05, "conversation": 0.65, "crowd": 0.1},
                         GCFG, {}, now_s=0.0)
    assert g["conf"] == pytest.approx(0.8 * 0.5, abs=1e-3)                  # conversation half-way from 0.3 to 1
    g = st.genre_reading({"Jazz": 0.8}, None, {"music": 0.9, "speech": 0.05, "conversation": 0.65},
                         {**GCFG, "speech_words": ["speech"]}, {}, now_s=0.0)
    assert g["conf"] == pytest.approx(0.8)
    s = st.Structure(c.BrainContext(publish=lambda m: None))
    s.configure({"situations": {"talking": {"speech_words": ["speech", "nonsense"]}}})
    assert s.gcfg["speech_words"] == ["speech"] and len(s.settings_problems) == 1


def test_peak_leaves_only_after_its_rule_has_failed_for_leave_s():
    dip: dict = {"from": None, "to": None}

    def scene(t: float) -> dict:
        sc = riser(t)
        if dip["from"] is not None and dip["from"] <= t < dip["to"]:
            sc["energy"] = 0.3
        return sc
    r = Rig(scene, clock=lambda t: clock_body(t, bpm=BPM))
    r.run(30.0 + 8 * 4 * BEAT + 2.0)
    assert r.s.phase == "peak"
    dip.update({"from": r.t, "to": r.t + 2.0})                              # a 2 s dip: the EMA falls under 0.75...
    seen = []
    while r.t < dip["to"] + 3.0:
        r.run(r.t + 0.1)
        seen.append(r.s.phase)
    assert set(seen) == {"peak"}                                            # ...for less than leave_s: still peak
    dip.update({"from": r.t, "to": r.t + 30.0})
    r.run(r.t + 10.0)
    assert r.s.phase != "peak"


def test_events_reach_every_callback_and_a_failing_one_is_counted():
    r = Rig(lambda t: abab(t))
    got: list = []
    r.ctx.on_music_event.insert(0, lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    r.ctx.on_music_event.append(got.append)
    r.run(40.0)
    assert len(got) == 1 and got[0]["kind"] == "section_change"
    assert r.s.snapshot()["callback_errors"] == 1


def test_attach_provides_the_context_slot():
    ctx = c.BrainContext(publish=lambda m: None)
    s = st.attach(ctx)
    assert s.name == "structure" and s.tick_hz == c.BRAIN_TICK_HZ
    prov = s.provides()
    assert set(prov) == {"music_context"} and hasattr(ctx, "music_context")
    assert prov["music_context"]() is None and s.persist() is None


def test_gobo_on_a_real_asyncio_loop():
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        steps: list[float] = []
        ctx = c.BrainContext(publish=lambda m: None)
        ctx.loop = loop
        ctx.section_change = lambda: steps.append(time.monotonic())
        s = st.attach(ctx)
        s.configure({})
        at = loop.time() + 0.05
        s._gobo_at(at, ("section", 1))
        s._gobo_at(loop.time() + 0.06, ("section", 2))
        s._gobo_cancel(("section", 2))
        loop.run_until_complete(asyncio.sleep(0.15))
        assert len(steps) == 1 and steps[0] >= at - 0.005
        s.stop()
    finally:
        loop.close()


# -- song ends (README S5.17) ----------------------------------------------------------------------------------------------------------
SON = {"situations": {"sonos": {"now_playing": SONOS}}}
FADE = {"style": "fade-dark", "out_s": 2.5, "in_s": 2.0, "seconds": None, "on": "now", "change": "big",
        "dark_max_s": 8.0}


def hms(x: float) -> str:
    x = int(x)
    return f"{x // 3600}:{(x % 3600) // 60:02d}:{x % 60:02d}"


class FakeSonos:
    """Room's view of a Sonos zone's now_playing, read every ``poll_s`` as room's adapter does: the position is whole
    seconds at the read, and the member's age_s (rounded to 0.1 s, as room's status) grows until the next read.
    ``songs``: [(start_s, duration_s, track_id, artist)], each playing from its start (duration 0: a stream, no
    length). ``state`` and ``seek`` (seconds added to the true position) can be changed."""

    def __init__(self, r: Rig, songs: list, *, poll_s: float = 2.07, phase: float = 0.3) -> None:
        self.r, self.songs, self.poll_s = r, list(songs), poll_s
        self.next_read = phase
        self.state = "playing"
        self.seek = 0.0
        self.read_t = 0.0
        self.believed: Optional[dict] = None
        r.sits = {"sonos_playing": {"value": True}}
        r.each = self.tick

    def tick(self, t: float) -> None:
        if t >= self.next_read - 1e-9:
            self.next_read += self.poll_s
            self.read_t = t
            start, dur, tid, artist = [sg for sg in sorted(self.songs) if t >= sg[0]][-1]
            pos = min(dur, max(0.0, t - start + self.seek))
            self.believed = {"state": self.state, "track_id": tid, "title": tid, "artist": artist,
                             "duration": hms(dur) if dur else "", "position": hms(pos) if dur else ""}
        if self.believed is not None:
            self.r.room["members"][SONOS] = {"believed": dict(self.believed), "confidence": "confirmed",
                                             "age_s": round(t - self.read_t, 1), "sent_age_s": None}


def test_hms_reads_sonos_times():
    assert st._hms("0:03:45") == 225.0 and st._hms("1:00:00") == 3600.0 and st._hms("2:05") == 125.0
    assert st._hms(12) == 12.0
    for bad in ("", "NOT_IMPLEMENTED", None, "1:x:00", "-0:01:00", -3):
        assert st._hms(bad) is None, bad


def test_a_sonos_end_is_armed_ahead_ends_on_time_and_the_next_song_changes_at_the_junction():
    r = Rig(lambda t: {"vec": "A"}, settings=SON)
    FakeSonos(r, [(-150.0, 180.0, "x:1", "Artist One"), (30.0, 200.0, "x:2", "Artist Two")])
    r.run(9.0)
    assert not r.kinds("track_end_armed")
    r.run(11.0)
    arms = r.kinds("track_end_armed")
    assert len(arms) == 1
    a = arms[0]
    assert a["id"] == "track_end_armed:0" and a["via"] == "sonos" and a["track_id"] == 0
    assert a["at_ms"] / 1000.0 == pytest.approx(30.0, abs=0.6) and a["eta_s"] == pytest.approx(20.0, abs=0.7)
    assert 0.5 <= a["conf"] <= 1.0 and a["kind"] in c.MUSIC_EVENT_KINDS
    r.run(29.0)
    arms = r.kinds("track_end_armed")
    assert all(x["id"] == "track_end_armed:0" and x.get("detail") is None for x in arms)   # re-sends replace it
    assert r.s.snapshot()["ends"]["sonos"]["spread_s"] < 0.6              # later reads narrowed the estimate
    assert not r.kinds("track_end")
    r.run(31.5)
    te = r.kinds("track_end")
    assert len(te) == 1 and te[0]["via"] == "sonos" and te[0]["id"] == "track_end:0" and te[0]["track_id"] == 0
    assert te[0]["at_ms"] / 1000.0 == pytest.approx(30.0, abs=0.6)
    tc = r.kinds("track_change")
    assert len(tc) == 1 and tc[0]["via"] == "sonos" and tc[0]["id"] == "track_change:1"
    assert tc[0]["track_id"] == 1 and tc[0]["prev_track_id"] == 0 and tc[0]["seq"] > te[0]["seq"]
    assert tc[0]["at_ms"] / 1000.0 == pytest.approx(30.0, abs=0.6)       # the new song began at the junction
    r.run(40.0)
    assert len(r.kinds("track_end")) == 1                                 # its id came in time: no sound_back
    assert r.s.snapshot()["ends"]["counts"]["sonos"] == 1
    json.dumps(r.s.snapshot(), allow_nan=False)


def test_a_pause_a_seek_back_or_a_skip_calls_the_armed_end_off():
    r = Rig(lambda t: {"vec": "A"}, settings=SON)
    fs = FakeSonos(r, [(-150.0, 180.0, "x:1", "a")])

    def armed() -> list:
        return [e for e in r.kinds("track_end_armed") if e.get("detail") is None]

    def off() -> list:
        return [e for e in r.kinds("track_end_armed") if e.get("detail") == "disarmed"]
    r.run(15.0)
    assert len(armed()) == 1
    fs.state = "paused_playback"                                          # a pause
    r.sits = {"sonos_playing": {"value": False}}
    r.run(17.0)
    assert len(off()) == 1 and off()[0]["eta_s"] is None and off()[0]["id"] == "track_end_armed:0"
    assert "transition" not in off()[0] and off()[0]["at_ms"] == armed()[0]["at_ms"]
    fs.state, fs.seek = "playing", -2.0                                   # play again where it stopped
    r.sits = {"sonos_playing": {"value": True}}
    r.run(21.0)
    assert len(armed()) == 2 and armed()[1]["id"] == "track_end_armed:0"
    assert armed()[1]["at_ms"] / 1000.0 == pytest.approx(32.0, abs=0.7)   # its end moved 2 s on
    fs.seek = -62.0                                                       # a seek back by a minute: far again
    r.run(24.0)
    assert len(off()) == 2
    r.run(80.0)
    assert len(armed()) == 3                                              # armed again for the end at ~92
    fs.songs.append((80.0, 200.0, "x:9", "b"))                            # a skip, 12 s before the end
    fs.seek = 0.0
    r.run(83.0)
    assert len(off()) == 3 and not r.kinds("track_end")
    tc = r.kinds("track_change")
    assert len(tc) == 1 and tc[0]["prev_track_id"] == 0 and tc[0]["via"] == "sonos"
    assert off()[-1]["seq"] < tc[0]["seq"]


def test_a_stream_with_no_length_arms_nothing_and_changes_as_before():
    r = Rig(lambda t: {"vec": "A"}, settings=SON)
    FakeSonos(r, [(-10.0, 0.0, "radio:1", "a"), (20.0, 0.0, "radio:2", "b")])
    r.run(30.0)
    assert not r.kinds("track_end_armed") and not r.kinds("track_end")
    tc = r.kinds("track_change")
    assert len(tc) == 1 and tc[0]["at_ms"] / 1000.0 == pytest.approx(21.0, abs=0.15)   # at the read, as today


def test_a_sonos_change_in_silence_waits_for_the_new_songs_first_sound():
    def scene(t: float) -> dict:
        return {"vec": None, "gate": 0.0, "lufs": -70.0} if 29.0 <= t < 33.0 else {"vec": "A"}
    r = Rig(scene, settings=SON, clock=lambda t: clock_body(t, bpm=128.0))
    FakeSonos(r, [(-150.0, 180.0, "x:1", "a"), (30.0, 200.0, "x:2", "b")])
    seen: list = []
    r.ctx.track_transition = lambda ev, f: seen.append((dict(ev), f)) or {"transition": dict(FADE)}
    r.run(32.5)
    te = r.kinds("track_end")
    assert len(te) == 1 and te[0]["via"] == "sonos"                       # no second end from the sound's gap
    assert not r.kinds("track_change")                                    # the new id came in silence: it waits
    assert seen[-1][0]["kind"] == "track_end" and seen[-1][1]["silent"] is True
    assert te[0]["transition"] == FADE
    r.run(34.0)
    tc = r.kinds("track_change")
    assert len(tc) == 1 and tc[0]["t_ms"] / 1000.0 == pytest.approx(33.0, abs=0.15)
    beat = 60.0 / 128.0
    assert tc[0]["at_ms"] == pytest.approx(math.ceil(33.0 / beat - 1e-6) * beat * 1000.0, abs=1.0)   # first beat
    assert tc[0]["transition"] == FADE and seen[-1][1]["new"]["artist"] == "b" and seen[-1][1]["old"]["artist"] == "a"
    assert not [e for e in r.kinds("track_end") if e.get("detail") == "sound_back"]
    assert r.s.snapshot()["ends"]["counts"]["waited"] == 1


def test_a_gap_after_music_is_a_dsp_end_at_its_start_and_a_short_rest_is_not():
    def scene(t: float) -> dict:
        if 40.0 <= t < 41.5 or 60.0 <= t < 65.0:
            return {"vec": None, "gate": 0.0, "lufs": -70.0}
        return {"vec": "A" if t < 60.0 else "C"}
    r = Rig(scene, clock=lambda t: clock_body(t, bpm=120.0 if t < 62.0 else 140.0))
    seen: list = []
    r.ctx.track_transition = lambda ev, f: seen.append((dict(ev), f)) or {"transition": dict(FADE)}
    r.run(59.0)
    assert not r.kinds("track_end")                                       # 1.5 s: a rest inside the song
    r.run(64.0)
    te = r.kinds("track_end")
    assert len(te) == 1 and te[0]["via"] == "dsp" and te[0]["id"] == "track_end:0" and te[0]["track_id"] == 0
    assert te[0]["at_ms"] / 1000.0 == pytest.approx(60.0, abs=0.15)      # at the gap's start...
    assert te[0]["t_ms"] / 1000.0 == pytest.approx(63.0, abs=0.15)       # ...once it has lasted end_gap_s
    assert te[0]["transition"] == FADE and seen[0][1]["silent"] is True and seen[0][1]["old"]["bpm"] == 120.0
    r.run(66.0)
    back = [e for e in r.kinds("track_end") if e.get("detail") == "sound_back"]
    assert len(back) == 1 and back[0]["id"] == "track_end:0" and back[0]["via"] == "dsp"
    assert back[0]["at_ms"] / 1000.0 == pytest.approx(65.0, abs=0.15)    # the first sound back
    assert seen[-1][1]["back"] is True
    r.run(85.0)
    tc = r.kinds("track_change")
    assert len(tc) == 1 and tc[0]["via"] == "dsp" and tc[0]["track_id"] == 1 and tc[0]["prev_track_id"] == 0
    beat = 60.0 / 140.0
    assert tc[0]["at_ms"] == pytest.approx(math.ceil(65.0 / beat - 1e-6) * beat * 1000.0, abs=1.0)   # first beat
    f = [x[1] for x in seen if x[0]["kind"] == "track_change"][0]
    assert f["back"] is True and f["old"]["bpm"] == 120.0 and f["new"]["bpm"] == 140.0
    assert len([e for e in r.kinds("track_end")]) == 2                    # the end and its sound_back, nothing more


def test_no_dsp_end_after_talk_alone_or_in_a_break_sonos_vouches_for():
    def scene(t: float) -> dict:
        return {"vec": None, "gate": 0.0, "lufs": -70.0} if 30.0 <= t < 36.0 else {"vec": "A"}
    r = Rig(scene)
    r.sits = {"music_audible": {"value": False}}                          # people talking, no music
    r.run(40.0)
    assert not r.kinds("track_end")
    r2 = Rig(scene, settings=SON)
    FakeSonos(r2, [(-10.0, 300.0, "x:1", "a")])                           # a long song: its end is far away
    r2.run(40.0)
    assert not r2.kinds("track_end") and not r2.kinds("track_end_armed")


def test_track_events_carry_the_hooks_answer_held_or_nothing():
    def boom(ev: dict, f: dict) -> dict:
        raise RuntimeError("boom")
    for hook, want in ((lambda ev, f: {"transition": dict(FADE)}, "transition"), (lambda ev, f: {"held": True}, "held"),
                       (boom, None), (None, None)):
        r = Rig(lambda t: {"vec": "A"}, settings=SON)
        r.room["members"][SONOS] = {"believed": {"state": "playing", "track_id": "x:1"}, "confidence": "confirmed"}
        r.sits = {"sonos_playing": {"value": True}}
        r.ctx.track_transition = hook
        r.run(5.0)
        r.room["members"][SONOS]["believed"] = {"state": "playing", "track_id": "x:2"}
        r.run(5.5)
        tc = r.kinds("track_change")[0]
        pub = [m["data"] for m in r.published if m["type"] == c.ROOM_EVENT and m["data"]["kind"] == "track_change"][0]
        if want == "transition":
            assert tc["transition"] == FADE and pub["transition"] == FADE and "held" not in tc
        elif want == "held":
            assert tc["held"] is True and "transition" not in tc
        else:
            assert "transition" not in tc and "held" not in tc
        assert r.s.ends["hook_errors"] == (1 if hook is boom else 0)


def test_every_event_has_a_stable_id_and_a_moved_drop_line_keeps_its_own():
    r = Rig(riser, clock=lambda t: clock_body(t, bpm=BPM))
    r.run(40.0)
    assert r.events and all(isinstance(e["id"], str) and e["id"].startswith(e["kind"] + ":0:") for e in r.events)
    assert [e["id"] for e in r.kinds("build")] == ["build:0:1"]
    assert {e["id"] for e in r.kinds("drop_armed")} == {"drop_armed:0:1"}            # re-sends keep it
    d = r.kinds("drop")[0]
    assert d["id"] == f"drop:0:{d['section_id']}"
    r2 = Rig(lambda t: abab(t))
    r2.run(125.0)
    sc = r2.kinds("section_change")
    assert len(sc) >= 3 and [e["id"] for e in sc] == [f"section_change:0:{e['section_id']}" for e in sc]
    assert len({e["id"] for e in sc}) == len(sc)


# -- settings ------------------------------------------------------------------------------------------------------------------------------
def test_show_yaml_parses_with_no_problems():
    s = st.Structure(c.BrainContext(publish=lambda m: None))
    s.configure(yaml.safe_load(c.SHOW_FILE.read_text()))
    assert s.settings_problems == []
    assert s.gcfg["parents"]                                                   # the live table


def test_bad_values_take_the_starting_point_and_are_listed():
    s = st.Structure(c.BrainContext(publish=lambda m: None))
    s.configure({"structure": {"novelty": {"recent_s": -1, "k_mad": "x"}, "gobo_on_sections": "yes",
                               "outro": {"max_slope_db_s": -0.5}},
                 "genre_map": {"parents": {"Jazz": 3}, "tau_s": 0}})
    assert s.cfg["novelty"]["recent_s"] == 4 and s.cfg["novelty"]["k_mad"] == 3.0
    assert s.cfg["gobo_on_sections"] is True and s.cfg["outro"]["max_slope_db_s"] == -0.5
    assert s.gcfg["parents"] == {} and s.gcfg["tau_s"] == 30
    assert len(s.settings_problems) == 5


def test_the_fallbacks_are_show_yaml_s_starting_points():
    """No invented values: the code's fallbacks are the same numbers as show.yaml's structure section."""
    sy = yaml.safe_load(c.SHOW_FILE.read_text())

    def same(a: dict, b: dict, path: str) -> None:
        assert set(a) == set(b), path
        for k in a:
            if isinstance(a[k], dict):
                same(a[k], b[k], f"{path}.{k}")
            else:
                assert a[k] == b[k], f"{path}.{k}: code {a[k]} vs show.yaml {b[k]}"
    same(st.STRUCTURE_FALLBACK, sy["structure"], "structure")
    for k, v in st.GENRE_FALLBACK.items():
        if k != "parents":
            assert sy["genre_map"][k] == v, k


# -- costs -------------------------------------------------------------------------------------------------------------------------------
def test_cost_with_full_size_vectors_and_many_sections(capsys):
    """README S4.7 budget: the 10 Hz tick <= 1 ms p95, the 1 Hz work <= 3 ms p95 (1280-d, up to 64 sections).
    Asserted loosely (the host renders 4K); the numbers print with -s."""
    letters = "ABCDEFGH"
    seg = 20.0                                                              # > min_section_bars (15 s at 128 bpm)
    r = Rig(lambda t: {"vec": letters[int(t // seg) % len(letters)], "energy": 0.5 + 0.05 * (int(t // seg) % 8)},
            clock=lambda t: clock_body(t, bpm=128.0))
    r.run(1000.0)
    snap = r.s.snapshot()
    n = len(snap["sections"])
    tick95, sec95 = snap["stats"]["tick_ms_p95"], snap["stats"]["second_ms_p95"]
    with capsys.disabled():
        print(f"\n[structure cost] {n} sections, 10 Hz tick p95 {tick95} ms, "
              f"1 Hz work p95 {sec95} ms")
    assert n >= 40
    assert [s["letter"] for s in snap["sections"][:10]] == list("ABCDEFGHAB")
    assert sec95 < 30.0 and tick95 < 30.0


# -- a real track through the dsp fallback ------------------------------------------------------------------------------------------------
def _decode(path: pathlib.Path, fs: int, seconds: float) -> np.ndarray:
    out = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-t", str(seconds), "-map", "0:a:0",
                          "-f", "f32le", "-ac", "1", "-ar", str(fs), "-"], capture_output=True, timeout=120)
    return np.frombuffer(out.stdout, dtype=np.float32)


def _offline_frames(x: np.ndarray, fs: int) -> list[dict]:
    """A rough numpy stand-in for dsp's lanes at 10 Hz: lufs-ish level, brightness, low_db, onset rate,
    percussive ratio (flatness), chroma and mfcc (1 s means)."""
    hop, n = fs // 10, 2048
    win = np.hanning(n)
    freqs = np.fft.rfftfreq(n, 1.0 / fs)
    pcs = np.full(freqs.shape, -1)
    ok = (freqs >= 55) & (freqs <= 5000)
    pcs[ok] = (np.round(12 * np.log2(freqs[ok] / 440.0)) + 9).astype(int) % 12
    mel_edges = 700 * (10 ** (np.linspace(2595 * np.log10(1 + 30 / 700), 2595 * np.log10(1 + 7500 / 700), 42)
                              / 2595) - 1)
    fb = np.zeros((40, len(freqs)))
    for m in range(40):
        lo, ce, hi = mel_edges[m:m + 3]
        fb[m] = np.clip(np.minimum((freqs - lo) / (ce - lo), (hi - freqs) / (hi - ce)), 0, None)
    k = np.arange(40)
    dct = np.cos(np.pi / 40 * (k[None, :] + 0.5) * np.arange(1, 14)[:, None])
    frames, chroma_hist, mfcc_hist, flux_hist = [], [], [], []
    prev = None
    for i in range(0, len(x) - n, hop):
        seg = x[i:i + n] * win
        mag = np.abs(np.fft.rfft(seg))
        p = mag ** 2
        tot = p.sum() + 1e-12
        lvl = 10 * np.log10(np.mean(x[i:i + n] ** 2) + 1e-12)
        cen = (freqs * p).sum() / tot
        bright = float(np.clip(np.log(max(cen, 50) / 50) / np.log(16000 / 50), 0, 1))
        low = 10 * np.log10(p[(freqs >= 20) & (freqs <= 250)].sum() / (n * n / 4) + 1e-12)
        flat = float(np.exp(np.mean(np.log(mag + 1e-9))) / (np.mean(mag) + 1e-9))
        ch = np.array([mag[pcs == q].sum() for q in range(12)])
        ch = ch / (ch.max() + 1e-12)
        mf = dct @ np.log(fb @ p + 1e-9)
        flux = float(np.clip(mag - prev, 0, None).sum()) if prev is not None else 0.0
        prev = mag
        chroma_hist.append(ch)
        mfcc_hist.append(mf)
        flux_hist.append(flux)
        chroma_hist, mfcc_hist, flux_hist = chroma_hist[-10:], mfcc_hist[-10:], flux_hist[-40:]
        fh = np.array(flux_hist)
        onsets = int(((fh[1:-1] > fh[:-2]) & (fh[1:-1] > fh[2:]) & (fh[1:-1] > 1.5 * np.median(fh))).sum())
        frames.append({"lufs": lvl, "bright": bright, "low": low, "perc": flat, "onset": onsets / 4.0,
                       "chroma": list(np.mean(chroma_hist, axis=0)), "mfcc": list(np.mean(mfcc_hist, axis=0)),
                       "energy": float(np.clip((lvl + 40) / 30, 0, 1)), "gate": 1.0 if lvl > -50 else 0.0})
    return frames


def _tempo(x: np.ndarray, fs: int) -> float:
    """A rough offline bpm: the onset envelope's (100 Hz log flux) autocorrelation peak over 70-180 bpm."""
    hop, n = fs // 100, 512
    frames = np.lib.stride_tricks.sliding_window_view(x, n)[::hop] * np.hanning(n)
    mag = np.log1p(np.abs(np.fft.rfft(frames, axis=1)) * 10)
    env = np.clip(np.diff(mag, axis=0), 0, None).sum(axis=1)
    env = env - env.mean()
    ac = np.fft.irfft(np.abs(np.fft.rfft(env, 2 * len(env))) ** 2)[:len(env)]
    lags = np.arange(len(ac)) / 100.0
    ok = (lags >= 60 / 180) & (lags <= 60 / 70)
    return float(60.0 / lags[ok][np.argmax(ac[ok])])


def _smoke_tracks() -> list[str]:
    try:
        idx = json.loads((c.TEST_MUSIC_DIR / "index.json").read_text())
    except (OSError, ValueError):
        return []
    have = [k for k in sorted(idx) if (c.TEST_MUSIC_DIR / idx[k].get("file", "")).exists()]
    beat = [k for k in have if idx[k].get("genre") in ("house", "techno")]       # tracks with builds and drops
    return (have[:1] + beat[:2]) if have else []


@pytest.mark.skipif(shutil.which("ffmpeg") is None or not _smoke_tracks(),
                    reason="needs ffmpeg and tools/show/fetch_test_music.py's tracks")
@pytest.mark.parametrize("name", _smoke_tracks() or ["none"])
def test_real_test_music_through_the_dsp_fallback(name, capsys):
    """Does it crash, on a real track: the dsp fallback's descriptors and the phase inputs computed offline (numpy,
    rough), a locked fake clock at the track's own rough tempo, 4 minutes in fast-forward."""
    idx = json.loads((c.TEST_MUSIC_DIR / "index.json").read_text())
    t0 = time.perf_counter()
    x = _decode(c.TEST_MUSIC_DIR / idx[name]["file"], 16000, 240.0)
    frames = _offline_frames(x, 16000)
    bpm = _tempo(x, 16000)
    prep = time.perf_counter() - t0
    assert len(frames) > 600
    r = Rig(lambda t: frames[min(len(frames) - 1, int(t * 10))], ml=False,
            clock=lambda t: clock_body(t, bpm=bpm, phrase_conf=0.6))
    t1 = time.perf_counter()
    r.run(len(frames) / 10.0)
    snap = r.s.snapshot()
    json.dumps(snap, allow_nan=False)
    json.dumps(r.s.context(), allow_nan=False)
    secs = snap["sections"]
    with capsys.disabled():
        print(f"\n[structure smoke] {name} at ~{bpm:.0f} bpm: {len(frames) / 10:.0f} s in "
              f"{time.perf_counter() - t1:.2f} s (prep {prep:.2f} s); {len(secs)} sections "
              f"{[(round(s['start_s']), s['letter'], s['repeat_of'], s['bars']) for s in secs]}; "
              f"events {dict(snap['events'])}; tick p95 {snap['stats']['tick_ms_p95']} ms, "
              f"1 Hz p95 {snap['stats']['second_ms_p95']} ms")
    assert snap["source"] == "dsp" and secs
    bar = 4 * 60.0 / bpm
    for b in snap["boundaries"]:
        if b["kind"] == "confirmed" and b.get("to") == "bar":
            k = b["snapped"] / bar
            assert abs(k - round(k)) < 1e-6                                  # on the fake clock's bar lines
