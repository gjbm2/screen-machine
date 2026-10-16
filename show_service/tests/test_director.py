"""director: deterministic 10 Hz ticks against a fake BrainContext (synthetic features, clock bodies, PianoView,
RoomView). No network, no real state dir (logs go to tmp), never the real room: room_get is a fake coroutine.

Vibe, lamp and screen names here are made up on purpose: the director must not know any real one.
"""

from __future__ import annotations

import ast
import asyncio
import io
import json
import math
import pathlib
import statistics
import time

import pytest
import yaml

from show_service import contract
from show_service import director as dmod
from show_service import palette as pal
from show_service.vibes import Vibes

SRC = pathlib.Path(dmod.__file__).parent

VOCAB = """moods: [calm, mellow, dreamy, warm, bright, euphoric, playful, hypnotic, elegant, organic, liquid, geometric,
        aggressive, glitchy, retro]
genre: [jazz, soul, house]
transition_style: [gentle, lively, wild]
palette_family: [warm, cool, vivid, neutral]
"""

VIBES = """version: 1
order: [alpha, beta, gamma]
vibes:
  alpha:                    # beat-locked, changes on phrases
    energy: [0.4, 0.9]
    follow: [energy]
    follow_amount: 1.0
    beat_lock: true
    change_looks: phrase
    change_rate: 0.5         # min_bars 16
    palettes: [neon, rainbow]
    level: [0.5, 1.0]
    floor: 0.1
    accents: downbeat
    peak_tokens_per_hour: 2
    chroma_max: 0.37
    lamp_chroma_max: 0.05
    key_cct: 2700
    loudness_ref_lufs: -20
    step_size: 0.1
    max_steps: 3
    moods: [bright]
  beta:                     # no beat lock, between pieces
    energy: [0.2, 0.6]
    beat_lock: false
    change_looks: between_pieces
    dwell_s: [20, 600]
    palettes: [ember, gold]
    accents: none
    relax_on_talk: false
  gamma:                    # by time
    energy: [0.1, 0.5]
    beat_lock: false
    change_looks: time
    dwell_s: [30, 60]
    palettes: [art, ember]
"""

PATCH = """artnet: {universe: 0}
profiles:
  tv: {channels: [{field: r}, {field: g}, {field: b}, {field: pulse}]}
  lamp: {channels: [{field: dimmer}, {field: r}, {field: g}, {field: b}]}
  dimmer: {channels: [{field: dimmer}]}
devices:
  tv.left:   {profile: tv, kind: screen, screen: wall-left}
  tv.right:  {profile: tv, kind: screen, screen: wall-right}
  lamp.one:  {profile: lamp, screen: wall-left, placement: over}
  lamp.two:  {profile: lamp, screen: wall-left, placement: facing}
  lamp.three: {profile: lamp, screen: wall-right, placement: over}
  lamp.four: {profile: lamp}
  smoke.a:   {profile: dimmer}
"""

SETTINGS = {
    "lamps": {"placement_roles": {"over": "wash", "facing": "keylight"}, "default_role": "wash",
              "roles": {"wash": {"palette_role": "accent", "follow_screen": True, "chroma_max": "chroma_max",
                                 "motion": True},
                        "keylight": {"palette_role": "base", "white_k": "key_cct", "chroma_max": "lamp_chroma_max",
                                     "avoid_hues": [[140, 230]], "motion": False}}},
    "director": {"steps": {"size_from": "step_size", "max_from": "max_steps"}, "keep_s": 900,
                 "low": {"level": 0.1, "fade_s": 2.0}, "house": {"white_k": 2700, "level": 0.8},
                 "next_max_wait_s": 4.0, "art": {"poll_s": 60, "colours": 5}, "republish_s": 5,
                 "accent_depth": {"none": 0.0, "soft": 0.3, "downbeat": 0.5, "beat": 0.7, "full": 0.9},
                 "bands": {"edges": [0.3, 0.55, 0.8], "hysteresis": 0.04, "split": {"by": "drive", "up": 0.65,
                                                                                    "down": 0.55},
                           "words": {"low": {"moods": ["calm"], "fx_moods": ["calm"], "transition_style": "gentle"},
                                     "mid_a": {"moods": ["mellow"], "fx_moods": ["warm"],
                                               "transition_style": "gentle"},
                                     "mid_b": {"moods": ["hypnotic"], "fx_moods": ["liquid"],
                                               "transition_style": "lively"},
                                     "high": {"moods": ["playful"], "fx_moods": ["retro"],
                                              "transition_style": "lively"},
                                     "top": {"moods": ["euphoric"], "fx_moods": ["glitchy"],
                                             "transition_style": "wild"}}}},
}

BPM = 120.0
BEAT = 60.0 / BPM


def clock_body(t_s: float, *, gate: str = "beat", bar_conf: float = 0.9) -> dict:
    return {"bpm": BPM, "anchor_ms": 0.0, "beat_at_anchor": 0, "beats_per_bar": 4, "bar_anchor_beat": 0,
            "phrase_bars": 8, "conf": 0.9, "bar_conf": bar_conf, "phrase_conf": 0.8, "state": "locked", "gate": gate,
            "epoch": 1, "stability": 0.8, "source": "test", "t_ms": t_s * 1000.0}


def feats(t_s: float, energy: float = 0.6, gate: float = 0.9, drive: float = 0.5) -> dict:
    return {"t_ms": t_s * 1000.0, "stream": "test", "energy": energy, "gate": gate, "onset": 0.3, "drive": drive,
            "punch": 0.4}


class Rig:
    """A Director on a fake BrainContext, ticked with fake monotonic times (100 ms apart)."""

    def __init__(self, tmp_path: pathlib.Path, *, clock: bool = False, settings: dict | None = None,
                 vibes_text: str = VIBES) -> None:
        (tmp_path / "vocab.yaml").write_text(VOCAB)
        (tmp_path / "vibes.yaml").write_text(vibes_text)
        (tmp_path / "patch.yaml").write_text(PATCH)
        self.t = 1000.0
        self.wall = 1_800_000_000.0
        self.pubs: list[dict] = []
        self.calls: list[tuple] = []
        self.feat: dict | None = None
        self.energy = 0.6
        self.clock_on = clock
        self.gate = "beat"
        self.piano: dict | None = None
        self.room: dict | None = None
        self.situations: dict | None = None
        self.sec: dict | None = None                       # Stage 4: structure's section (None: no structure)
        self.rhythm: dict | None = None
        self.persisted = 0
        self.notified = 0
        ctx = contract.BrainContext(publish=self.pubs.append)
        ctx.music_context = lambda: music_context(self.t, self.sec) if self.sec is not None else None
        ctx.rhythm = lambda: self.rhythm
        ctx.features = lambda: feats(self.t, self.energy) if self.feat is None else self.feat
        ctx.clock = lambda: clock_body(self.t, gate=self.gate) if self.clock_on else None
        ctx.piano = lambda: self.piano
        ctx.room = lambda: self.room
        ctx.situations = lambda: self.situations
        ctx.section_change = lambda: self.calls.append(("gobo", self.t))
        ctx.set_look_out = lambda **kw: self.calls.append(("look", kw))
        ctx.set_gate_ref = lambda v: self.calls.append(("gate_ref", v))
        ctx.persist = self._persist
        ctx.notify = self._notify
        self.ctx = ctx
        cat = pal.Catalog()
        self.vibes = Vibes(tmp_path / "vibes.yaml", vocab_path=tmp_path / "vocab.yaml", palette_names=cat.names)
        self.log_path = tmp_path / "state" / "director.jsonl"
        self.d = dmod.Director(ctx, vibes=self.vibes, catalog=cat, patch_path=tmp_path / "patch.yaml",
                               log_path=self.log_path, wall=lambda: self.wall, mono=lambda: self.t)
        self.d.configure(settings if settings is not None else json.loads(json.dumps(SETTINGS)))
        self.d.start()

    def _persist(self) -> None:
        self.persisted += 1

    def _notify(self, kind, payload) -> None:
        self.notified += 1

    def run(self, seconds: float, each=None) -> None:
        for _ in range(int(round(seconds * 10))):
            self.t = round(self.t + 0.1, 6)
            self.wall += 0.1
            if each:
                each(self)
            self.d.tick(self.t)

    def of(self, type_: str) -> list[dict]:
        return [p["data"] for p in self.pubs if p["type"] == type_]

    def colours(self) -> dict:
        return self.d.palette_fn(self.t + 100.0)                   # after any glide

    def event(self, kind: str, at_s: float, **kw) -> None:
        """structure's room.event, handed over as structure does (ctx.on_music_event)."""
        self.d.on_music_event({"kind": kind, "at_ms": at_s * 1000.0, "t_ms": self.t * 1000.0, "seq": 1, **kw})

    def run_to(self, t_s: float, each=None) -> None:
        self.run(max(0.0, t_s - self.t), each)

    def gobos(self) -> int:
        return len([c for c in self.calls if c[0] == "gobo"])

    def looks(self) -> list[dict]:
        return [e for e in self.of(contract.ROOM_EVENT) if e["kind"] == "look_change"]

    def next_bar(self, lead: float = 1.1) -> float:
        bar = 4 * BEAT
        return math.ceil((self.t + lead) / bar - 1e-9) * bar


def music_context(t_s: float, sec: dict) -> dict:
    """A contract.MusicContext with ``sec``'s section fields (id, letter, phase, bp, bars, expected, started_s, label,
    track)."""
    return {"t_ms": t_s * 1000.0, "stream": "test", "source": "ml",
            "genre": sec.get("genre") or {"word": None, "conf": 0.0, "parent": None, "parent_p": 0.0, "via": "none"},
            "moods": [], "instruments": [], "vocal": None, "dance": None, "arousal": None, "engagement": None,
            "bright": None, "acoustic": None, "key": None, "mode": None, "key_strength": 0.0, "key_via": None,
            "talking": False, "ced": None,
            "section": {"id": sec.get("id", 0), "letter": sec.get("letter"), "repeat_of": None, "novelty": 0.1,
                        "phase": sec.get("phase", "calm"), "build_progress": sec.get("bp", 0.0),
                        "drop_eta_beats": None, "bars_in_section": sec.get("bars"),
                        "started_ms": sec.get("started_s", t_s) * 1000.0, "expected_bars": sec.get("expected"),
                        "chorus": False, "label": sec.get("label")},
            "track": {"id": sec.get("track", 0), "changed": False, "age_s": 10.0, "via": None},
            "ml": {"state": "running", "styles_top3": [], "moods_top5": [], "instruments_top5": [], "ages": {}}}


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


# -- neutral ----------------------------------------------------------------------------------------------------------
def test_neutral_is_hands_off(rig):
    rig.run(2.0)
    assert rig.d.palette_fn(rig.t) is None                         # artnet_out keeps its fixed colour
    assert not [c for c in rig.calls if c[0] in ("look", "gate_ref")]
    looks, dirs = rig.of(contract.ROOM_LOOK), rig.of(contract.ROOM_DIRECTION)
    assert looks and dirs and looks[-1]["vibe"] is None and dirs[-1]["vibe"] is None
    assert looks[-1]["lamps"] == {} and looks[-1]["screens"] == {}
    rb = rig.d.readback()
    assert rb["vibe"] is None and rb["source"] == "none" and not rb["known"]
    assert rig.d.snapshot()["neutral"] is True and rig.d.provides()["vibe"]() is None


def test_an_unknown_vibe_plays_neutral_and_a_vanished_vibe_goes_neutral(tmp_path):
    r = Rig(tmp_path)
    r.d.set_target({"vibe": "alpha"}, "mode")
    r.run(1.0)
    assert r.d.palette_fn(r.t) is not None
    rb = r.d.set_target({"vibe": "nope"}, "mode")         # degrade, never refuse: the previous feel doesn't linger
    assert rb["vibe"] == "nope" and rb["known"] is False and "isn't in vibes.yaml" in rb["problems"][0]
    r.run(1.0)
    assert r.d.palette_fn(r.t) is None and r.d.snapshot()["neutral"] is True
    rb = r.d.set_target({"vibe": None})                   # no feel: the room's put-back of "nothing set"
    assert rb["vibe"] is None and rb["source"] == "none" and rb["problems"] == [] and r.d.target is None
    with pytest.raises(ValueError, match="^bad_target"):
        r.d.set_target({"vibe": "a b"})
    r.d.set_target({"vibe": "beta", "listening": False}, "mode")
    r.run(1.0)
    assert r.d.palette_fn(r.t) is not None
    (tmp_path / "vibes.yaml").write_text(VIBES.replace("  beta:", "  beta2:"))
    r.d._reload_next = 0.0
    r.vibes._sig = None
    r.run(0.5)
    assert r.d.palette_fn(r.t) is None and not r.d.readback()["known"]
    assert ("look", {"reset": True}) in r.calls                     # artnet put back to its own look


# -- entering a vibe --------------------------------------------------------------------------------------------------
def test_entering_a_vibe_drives_lamps_and_screens(rig):
    rig.run(1.0)
    rb = rig.d.set_target({"vibe": "alpha", "listening": True}, "mode")
    assert rb["vibe"] == "alpha" and rb["known"] and rb["source"] == "mode" and rb["listening"]
    rig.run(0.2)
    pf = rig.d.palette_fn(rig.t)
    assert set(pf) == {"lamp.one", "lamp.two", "lamp.three", "lamp.four", "tv.left", "tv.right"}   # no smoke.a
    assert ("gate_ref", -20.0) in rig.calls
    house = pal.scale_level(pal.white(2700), 0.8)
    t0 = rig.t
    early = rig.d.palette_fn(t0 + 0.05)
    assert max(abs(a - b) for a, b in zip(early["lamp.one"], house)) < 0.05     # lamps fade in from house...
    late = rig.d.palette_fn(t0 + dmod.ENTRY_FADE_S + 1)
    assert late["lamp.one"] != early["lamp.one"]                    # ...over about one phrase
    assert early["tv.left"] == late["tv.left"]                      # screens don't fade from house
    look = rig.d.look
    assert look["vibe"] == "alpha" and look["palette"]["name"] == "neon"
    assert {d: x["role"] for d, x in look["lamps"].items()} == {"lamp.one": "wash", "lamp.two": "keylight",
                                                                "lamp.three": "wash", "lamp.four": "wash"}
    assert rig.d.provides()["vibe"]()["energy"] == [0.4, 0.9]


def test_steps_clamp_and_chips(rig):
    rig.d.set_target("alpha")
    rig.run(0.5)
    outs = [rig.d.step("wilder") for _ in range(5)]
    assert [o["steps"]["value"] for o in outs] == [1, 2, 3, 3, 3]
    assert outs[3]["steps"]["at_limit"] and outs[3]["done"] is False
    assert outs[-1]["chips"][0] == {"id": "steps", "kind": "steps", "label": "Wilder ×3", "value": 3,
                                    "expires_in_s": None}
    for _ in range(8):
        last = rig.d.step("calmer")
    assert last["steps"] == {"value": -3, "min": -3, "max": 3, "at_limit": True}
    rig.run(3.0)
    e_calm = rig.d.direction["energy"]
    rig.d.clear("steps")
    rig.run(3.0)
    assert rig.d.direction["energy"] > e_calm
    with pytest.raises(ValueError, match="^bad_request"):
        rig.d.step("sideways")
    assert rig.persisted and rig.notified


def test_mode_change_clears_nudges_and_the_same_mode_keeps_them(rig):
    rig.d.set_target("alpha")
    rig.run(0.5)
    rig.d.on_mode_change(None, "one")
    rig.d.step("wilder")
    rig.d.set_palette("rainbow")
    rig.d.action("keep")
    rig.d.action("low")
    assert {c["id"] for c in rig.d.chips()} == {"steps", "palette", "keep", "low"}
    assert rig.d.snapshot()["engaged"] and rig.d.provides()["engaged"]()
    rig.d.on_mode_change("one", "one")                              # not a change
    assert len(rig.d.chips()) == 4
    rig.d.on_mode_change("one", "two")
    assert rig.d.chips() == [] and rig.d.steps == 0 and rig.d.palette_pick == "auto"
    assert not rig.d.snapshot()["engaged"]


def test_clear_and_unknown_ids(rig):
    rig.d.set_target("alpha")
    rig.run(0.2)
    assert rig.d.clear("nothing-here") == []
    rig.d.action("house")
    rig.d.step("calmer")
    assert sorted(rig.d.clear("all")) == ["house", "steps"]
    assert rig.d.chips() == []


# -- the screens rule -------------------------------------------------------------------------------------------------
def test_house_low_and_level_change_lamps_never_screens(rig):
    rig.d.set_target({"vibe": "alpha", "listening": False})
    rig.run(3.0)
    base = rig.colours()
    screens = {k: v for k, v in base.items() if k.startswith("tv.")}
    lamps = {k: v for k, v in base.items() if k.startswith("lamp.")}

    def check(label):
        now = rig.colours()
        assert {k: now[k] for k in screens} == screens, label       # screens untouched
        return {k: now[k] for k in lamps}

    rig.d.action("house")
    rig.run(1.0)
    assert check("house") != lamps
    assert all(v == pytest.approx(pal.scale_level(pal.white(2700), 0.8)) for v in check("house").values())
    rig.d.action("house")
    rig.d.action("low")
    rig.run(3.0)
    low = check("low")
    assert all(max(v) == pytest.approx(0.1, abs=1e-6) for v in low.values())
    rig.d.action("low")
    rig.run(3.0)
    back = check("back")                                            # (the wash lamps drift: compare levels)
    assert {k: round(max(v), 4) for k, v in back.items()} == {k: round(max(v), 4) for k, v in lamps.items()}
    # a different level (the mode's override) moves lamps only
    rig.d.set_target({"vibe": "alpha", "listening": False, "overrides": {"level": [0.2, 0.25]}})
    rig.run(3.0)
    dim = check("level")
    assert all(max(dim[k]) < max(lamps[k]) for k in lamps)
    assert all(max(rig.d.look["screens"][k]["rgb"]) == pytest.approx(1.0) for k in screens)    # full value


def test_house_uses_the_rooms_stored_put_back_value(rig):
    rig.room = {"current": "x", "screens": {},
                "stored_restore": {"lamp.one": {"on": True, "bri": 50, "mirek": 370},
                                   "lamp.two": {"value": {"on": True, "bri": 100, "xy": [0.3127, 0.329]}},
                                   "lamp.three": "off-ish word", "lamp.four": "dmx"}}
    rig.d.set_target("beta")
    rig.run(0.5)
    rig.d.action("house")
    rig.run(1.0)
    c = rig.colours()
    assert c["lamp.one"] == pytest.approx(pal.scale_level(pal.white(1e6 / 370), 0.5))
    assert c["lamp.two"] == pytest.approx((1.0, 1.0, 1.0), abs=0.01)
    assert c["lamp.three"] == (0.0, 0.0, 0.0)                        # a stored state that is the lamp off
    assert c["lamp.four"] == pytest.approx(pal.scale_level(pal.white(2700), 0.8))    # following: house white


# -- when looks change ------------------------------------------------------------------------------------------------
def test_looks_change_only_on_phrase_lines(tmp_path):
    r = Rig(tmp_path, clock=True)
    r.d.set_target("alpha")
    r.run(200.0)
    events = [e for e in r.of(contract.ROOM_EVENT) if e["kind"] == "look_change"]
    gobos = [t for k, t in r.calls if k == "gobo"]
    assert len(events) >= 4 and len(gobos) == len(events)
    phrase_s = 8 * 4 * BEAT                                          # 16 s
    for e in events:
        at = e["at_ms"] / 1000.0
        assert abs(at / phrase_s - round(at / phrase_s)) < 1e-6      # on a phrase line
        assert e["bar"] % 8 == 0 and e["at_ms"] - e["t_ms"] >= dmod.LOOK_MIN_LEAD_S * 1000 - 1e-6
    ats = [e["at_ms"] / 1000.0 for e in events]
    assert min(b - a for a, b in zip(ats, ats[1:])) >= 16 * 4 * BEAT - 1e-6     # min_bars 16 at change_rate 0.5
    for e, g in zip(events, gobos):
        assert 0 <= g - e["at_ms"] / 1000.0 < 0.1 + 1e-6            # landed on the tick at at_ms
    ids = [lk["look_id"] for lk in r.of(contract.ROOM_LOOK)]
    assert len(set(ids)) == len(events) + 1


def test_the_unsure_clock_falls_back_to_time(tmp_path):
    vibes = VIBES.replace("    change_rate: 0.5         # min_bars 16", "    change_rate: 0.5\n    dwell_s: [20, 40]")
    r = Rig(tmp_path, clock=True, vibes_text=vibes)
    r.gate = "none"
    r.d.set_target("alpha")
    r.run(130.0)
    events = [e for e in r.of(contract.ROOM_EVENT) if e["kind"] == "look_change"]
    assert 3 <= len(events) <= 6 and all(e["detail"] in ("dwell", "novelty") for e in events)


def test_keep_holds_the_look_and_expires(tmp_path):
    r = Rig(tmp_path, clock=True)
    r.d.set_target("alpha")
    r.run(1.0)
    out = r.d.action("keep")
    chip = next(c for c in out["chips"] if c["id"] == "keep")
    assert chip["expires_in_s"] == pytest.approx(900.0) and chip["label"] == "Keep this look · 15 min"
    r.run(120.0)
    assert not [e for e in r.of(contract.ROOM_EVENT) if e["kind"] == "look_change"]
    assert r.d.snapshot()["keep_in_s"] == pytest.approx(780.0, abs=0.2)
    r.run(781.0)
    assert r.d.keep_until is None and not any(c["id"] == "keep" for c in r.d.chips())
    assert r.d.variation >= 1                                        # variations went on while kept


def test_next_look_lands_on_the_next_bar_and_moves_the_palette(tmp_path):
    r = Rig(tmp_path, clock=True)
    r.d.set_target("alpha")
    r.run(3.0)
    assert r.d.look["palette"]["name"] == "neon"
    out = r.d.action("next")
    assert out["look"]["next_pending"] and out["done"]
    ev = [e for e in r.of(contract.ROOM_EVENT) if e["kind"] == "look_change"][-1]
    bar_s = 4 * BEAT
    at = ev["at_ms"] / 1000.0
    assert abs(at / bar_s - round(at / bar_s)) < 1e-6 and at - r.t <= 4.0 + 1e-6
    r.run(2.5)
    assert not r.d.next_pending and r.d.look["palette"]["name"] == "rainbow"
    assert [k for k, _ in r.calls].count("gobo") == 1


def test_between_pieces_once_per_pause(tmp_path):
    r = Rig(tmp_path)
    r.piano = {"present": True, "stream": "keys", "source": "live", "keys_down": 2, "damper": 0.5,
               "last_note_age_s": 0.1, "velocity": 0.7, "notes_per_s": 3.0}
    r.d.set_target("beta")
    r.run(25.0)                                                       # playing: no change
    assert not [e for e in r.of(contract.ROOM_EVENT) if e["kind"] == "look_change"]
    r.piano = dict(r.piano, keys_down=0, damper=0.0, last_note_age_s=3.0, present=False)
    r.run(10.0)                                                       # one pause: one change
    assert len([e for e in r.of(contract.ROOM_EVENT) if e["kind"] == "look_change"]) == 1
    r.piano = dict(r.piano, keys_down=1, damper=0.5, last_note_age_s=0.2, present=True)
    r.run(5.0)
    r.piano = dict(r.piano, keys_down=0, damper=0.0, last_note_age_s=3.0)
    r.run(5.0)                                                        # a new pause, but the look is too young
    assert len([e for e in r.of(contract.ROOM_EVENT) if e["kind"] == "look_change"]) == 1
    r.run(20.0)                                                       # the same long pause: the look is old enough
    r.run(30.0)                                                       # ...and changes once, not again
    assert len([e for e in r.of(contract.ROOM_EVENT) if e["kind"] == "look_change"]) == 2


def test_no_look_changes_while_a_screen_cuts(tmp_path):
    r = Rig(tmp_path, clock=True)
    r.room = {"current": "x", "screens": {"wall-left": {"layer": "art", "phase": "entering", "cutting": True}},
              "stored_restore": {}}
    r.d.set_target("alpha")
    r.run(120.0)
    assert not [e for e in r.of(contract.ROOM_EVENT) if e["kind"] == "look_change"]
    assert not [c for c in r.calls if c[0] == "gobo"]


# -- outputs to artnet_out and dsp -------------------------------------------------------------------------------------
def test_beat_lock_false_tells_artnet(tmp_path):
    r = Rig(tmp_path, clock=True)
    r.d.set_target("beta")
    r.run(1.0)
    looks = [kw for k, kw in r.calls if k == "look"]
    assert looks[-1] == {"hue_depth": 0.0, "beat_lock": False}
    assert r.d.direction["beat_lock"] is False and r.d.direction["loose"] is False


def test_beat_lock_and_accents_after_one_phrase(tmp_path):
    r = Rig(tmp_path, clock=True)
    r.d.set_target("alpha")
    r.run(1.0)
    assert [kw for k, kw in r.calls if k == "look"][-1] == {"hue_depth": 0.0, "beat_lock": True}
    r.run(16.5)                                                        # one phrase (16 s at 120 bpm)
    depth = [kw for k, kw in r.calls if k == "look"][-1]
    level = r.d.look["level"]
    assert depth["beat_lock"] is True
    assert depth["hue_depth"] == pytest.approx(min(0.5, 1 - 0.1 / level), abs=1e-3)
    r.gate = "dmx"                                                     # the clock unsure: no beat swell, "loosely"
    r.run(0.3)
    assert [kw for k, kw in r.calls if k == "look"][-1] == {"hue_depth": 0.0, "beat_lock": False}
    assert r.d.direction["loose"] is True


def test_gate_ref_follows_the_vibe_and_is_put_back(rig):
    rig.d.set_target("alpha")
    rig.run(0.3)
    rig.d.set_target("beta")                                           # beta leaves it unset
    rig.run(0.3)
    refs = [v for k, v in rig.calls if k == "gate_ref"]
    assert refs == [-20.0, dmod.GATE_REF_DEFAULT_LUFS]


def test_direction_numbers_and_words(rig):
    rig.d.set_target("alpha")
    rig.energy = 1.0
    rig.run(20.0)
    hot = rig.d.direction
    assert hot["energy"] == pytest.approx(0.9) and hot["intensity"] == 1.0   # clamped to the vibe
    assert hot["moods"] == ["euphoric"] and hot["transition_style"] == "wild" and hot["fx"]["moods"] == ["glitchy"]
    rig.energy = 0.0
    rig.run(20.0)
    cold = rig.d.direction
    assert cold["energy"] == pytest.approx(0.4) and cold["moods"] in (["mellow"], ["hypnotic"])
    rig.d.apply_describe({"steps": 0, "palette": None, "moods": ["dreamy", "nonsense"], "keep": False,
                          "switch_mode": None, "reply": "ok"})
    rig.run(0.2)
    assert rig.d.direction["moods"] == ["dreamy"]                       # a person's words first


def test_no_audio_holds_then_drifts_to_the_base(rig):
    rig.d.set_target("alpha")
    rig.energy = 1.0
    rig.run(10.0)
    assert rig.d.direction["energy"] == pytest.approx(0.9, abs=0.01)
    rig.feat = {"t_ms": rig.t * 1000.0, "energy": 1.0, "gate": 0.9}     # frozen: goes stale
    rig.run(9.0)
    assert rig.d.direction["energy"] == pytest.approx(0.9, abs=0.01)    # held
    rig.run(30.0)
    assert rig.d.direction["energy"] == pytest.approx(0.65, abs=0.01)   # the energy centre
    assert rig.d.palette_fn(rig.t) is not None                          # never black, never hands-off


def test_no_audio_stops_the_look_changes_after_the_drift(rig):
    """§8.5 "no audio: hold 10 s, then drift to the base look": an empty room (the stream down) stops changing looks
    once liveness has run down, even when the vibe changes by time."""
    rig.d.set_target("gamma")                                       # change_looks time, dwell_s [30, 60]
    rig.run(1.0)
    rig.feat = {"t_ms": rig.t * 1000.0, "energy": 0.3, "gate": 0.9}     # frozen: goes stale
    rig.run(40.0)                                                   # hold 10 s + fade 20 s, then relaxed
    assert rig.d.music["no_audio"] is True and rig.d.snapshot()["relaxed"] is True
    before = [e for e in rig.of(contract.ROOM_EVENT) if e["kind"] == "look_change"]
    gobos = len([c for c in rig.calls if c[0] == "gobo"])
    rig.run(240.0)                                                  # 4 x dwell_s[1]: nothing changes
    after = [e for e in rig.of(contract.ROOM_EVENT) if e["kind"] == "look_change"]
    assert after == before and len([c for c in rig.calls if c[0] == "gobo"]) == gobos
    assert rig.d.palette_fn(rig.t) is not None                      # never black
    rig.feat = None                                                 # sound again: changes resume
    rig.run(70.0)
    assert len([e for e in rig.of(contract.ROOM_EVENT) if e["kind"] == "look_change"]) > len(after)


def test_thresholds_come_from_show_yaml(tmp_path):
    settings = json.loads(json.dumps(SETTINGS))
    settings["director"].update({"novelty": 0.3, "entry_fade_s": 2.0, "emphasis_edges": [0.2, 0.9],
                                 "between_pieces": {"pause_s": 5.0}, "variation_bars": 32, "new_song_after_s": 20})
    r = Rig(tmp_path, settings=settings)
    cfg = r.d.cfg
    assert cfg["novelty"] == 0.3 and cfg["entry_fade_s"] == 2.0 and cfg["emphasis_edges"] == [0.2, 0.9]
    assert cfg["between_pieces"] == {"pause_s": 5.0, "damper_up": dmod.DAMPER_UP} and cfg["variation_bars"] == 32
    assert cfg["new_song_after_s"] == 20 and r.d.settings_problems == []
    settings["director"]["emphasis_edges"] = [0.9, 0.2]
    r.d.configure(settings)
    assert r.d.cfg["emphasis_edges"] == list(dmod.EMPHASIS_EDGES) and r.d.settings_problems


def test_describe_sets_only_what_the_card_can(rig):
    rig.d.set_target("alpha")
    rig.run(0.5)
    chips = rig.d.apply_describe({"steps": -2, "palette": "ember", "moods": ["warm"], "keep": True,
                                  "switch_mode": "elsewhere", "reply": "Calmer and warmer."})
    assert {c["id"] for c in chips} == {"steps", "palette", "moods", "keep"}
    assert rig.d.steps == -2 and rig.d.palette_pick == "ember" and rig.d.moods == ["warm"]
    assert rig.d.apply_describe({"palette": "plaid", "steps": 0}) == []
    assert rig.d.apply_describe("junk") == []


def test_set_palette_and_actions_validate(rig):
    rig.d.set_target("alpha")
    rig.run(0.5)
    with pytest.raises(ValueError, match="^unknown_palette"):
        rig.d.set_palette("plaid")
    with pytest.raises(ValueError, match="^unknown_action"):
        rig.d.action("dance")
    out = rig.d.set_palette("ember")
    assert out["palette"]["pick"] == "ember" and out["chips"][0]["label"] == "Palette: Ember"
    rig.run(5.0)
    assert rig.d.look["palette"]["name"] == "ember" and rig.d.look["palette"]["source"] == "human"
    rig.d.set_palette("auto")
    rig.run(1.0)
    assert rig.d.look["palette"]["name"] == "neon"
    assert rig.d.release_hue() == {"released": True, "via": "room"}


# -- the art ----------------------------------------------------------------------------------------------------------
def _png(rgb_left, rgb_right) -> bytes:
    from PIL import Image
    im = Image.new("RGB", (128, 72), tuple(round(255 * c) for c in rgb_left))
    im.paste(tuple(round(255 * c) for c in rgb_right), (64, 0, 128, 72))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_palette_from_the_art_through_the_room(tmp_path):
    r = Rig(tmp_path)
    left, right = (0.9, 0.2, 0.1), (0.1, 0.3, 0.9)
    gets: list[str] = []
    catalog_body = json.dumps({"presets": {}, "palettes": {"only": {
        "label": "Only", "cosine": {"a": [0.5, 0.5, 0.5], "b": [0.5, 0.5, 0.5], "c": [1, 1, 1],
                                    "d": [0, 0.33, 0.67]}}}}).encode()

    async def room_get(path: str, timeout: float = 3.0):
        gets.append(path)
        if path == dmod.RENDERER_CATALOG_PATH:
            return 200, catalog_body
        if path.endswith("/art"):
            return 503, b""                                            # the feed warming: the anchor answers
        if "wall-left" in path:
            return 200, _png(left, left)
        if "wall-right" in path:
            return 200, _png(right, right)
        return 404, b""

    async def main():
        r.ctx.loop = asyncio.get_running_loop()
        r.ctx.room_get = room_get
        r.d.set_target("gamma")
        for _ in range(40):
            r.t = round(r.t + 0.1, 6)
            r.d.tick(r.t)
            await asyncio.sleep(0.01)
        r.d.stop()

    asyncio.run(main())
    assert "/api/v1/screens/wall-left/art" in gets and "/api/v1/screens/wall-left/snapshots/anchor.png" in gets
    assert dmod.RENDERER_CATALOG_PATH in gets
    assert r.d.palettes.source == "api" and r.d.palettes.names() == ["only"]
    assert r.d.look["palette"]["name"] == "art" and r.d.look["palette"]["source"] == "art"
    scr = r.d.look["screens"]
    assert math.dist(pal.to_oklab(scr["tv.left"]["rgb"]), pal.to_oklab(pal.full(left))) < 0.03
    assert math.dist(pal.to_oklab(scr["tv.right"]["rgb"]), pal.to_oklab(pal.full(right))) < 0.03
    snap = r.d.snapshot()
    assert set(snap["art"]["screens"]) == {"wall-left", "wall-right"}
    assert snap["palette"]["choices"][1]["name"] == "art" and snap["palette"]["choices"][1]["swatch"]


def test_art_without_a_room_degrades_to_the_next_palette(rig):
    rig.d.set_target("gamma")                                         # [art, ember], no room_get: no stills
    rig.run(1.0)
    assert rig.d.look["palette"]["name"] == "ember"
    assert rig.d.palette_fn(rig.t) is not None


# -- persistence, logs, budgets ---------------------------------------------------------------------------------------
def test_persist_and_restore_round_trip(tmp_path):
    r = Rig(tmp_path)
    r.d.set_target({"vibe": "alpha", "listening": True, "overrides": {"strobe": True}}, "mode")
    r.d.on_mode_change(None, "m1")
    r.run(0.5)
    r.d.step("wilder")
    r.d.set_palette("rainbow")
    r.d.action("keep")
    saved = json.loads(json.dumps(r.d.persist()))
    assert saved["nudges"]["keep_until"] == pytest.approx(r.wall + 900.0, abs=0.2)

    r2 = Rig(tmp_path / "again" if (tmp_path / "again").mkdir() is None else tmp_path)
    r2.wall, r2.t = r.wall + 60.0, 5.0
    r2.d.restore(saved)
    r2.run(0.3)
    assert r2.d.target == {"vibe": "alpha", "listening": True, "overrides": {"strobe": True}}
    assert r2.d.readback()["source"] == "restored"
    assert {c["id"] for c in r2.d.chips()} == {"steps", "palette", "keep"}
    assert r2.d._keep_in() == pytest.approx(840.0, abs=0.5)
    r2.room = {"current": "m1", "screens": {}, "stored_restore": {}}
    r2.run(0.2)
    assert len(r2.d.chips()) == 3                                     # the same mode: kept
    r3 = Rig(tmp_path / "third" if (tmp_path / "third").mkdir() is None else tmp_path)
    r3.d.restore(saved)
    r3.room = {"current": "m2", "screens": {}, "stored_restore": {}}
    r3.run(0.2)
    assert r3.d.chips() == []                                         # another mode: every nudge cleared
    old = dict(saved, nudges=dict(saved["nudges"], keep_until=r.wall - 10))
    r4 = Rig(tmp_path / "fourth" if (tmp_path / "fourth").mkdir() is None else tmp_path)
    r4.wall = r.wall
    r4.d.restore(old)
    assert r4.d.keep_until is None                                    # a passed Keep is dropped
    r4.d.restore({})
    r4.d.restore({"target": "junk", "nudges": {"moods": "junk"}})


def test_director_log_is_written(rig):
    rig.d.set_target("alpha")
    rig.run(0.5)
    rig.d.step("wilder")
    lines = [json.loads(x) for x in rig.log_path.read_text().splitlines()]
    assert any(x["kind"] == "nudge" and x["nudge"] == "steps" for x in lines)
    assert all("music" in x for x in lines)
    assert rig.d.log(2) == list(rig.d._log)[-2:]


def test_palette_fn_crossfades_in_oklab_and_is_cheap(rig):
    rig.d.set_target("alpha")
    rig.run(10.0)
    frm = {"lamp.one": (1.0, 0.0, 0.0)}
    rig.d._set_colours({"lamp.one": (0.0, 0.0, 1.0)}, rig.t, 2.0, start=frm)
    mid = rig.d.palette_fn(rig.t + 1.0)["lamp.one"]
    want = pal.mix((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), 0.5)
    assert mid == pytest.approx(want, abs=1e-9)
    assert rig.d.palette_fn(rig.t - 1.0)["lamp.one"] == (1.0, 0.0, 0.0)
    assert rig.d.palette_fn(rig.t + 3.0)["lamp.one"] == (0.0, 0.0, 1.0)
    rig.run(0.1)                                                       # a full set of devices, mid-glide
    rig.d._set_colours({k: (0.2, 0.4, 0.9) for k in rig.d._pal[3]}, rig.t, 10.0)
    n, t0 = 2000, time.perf_counter()
    for i in range(n):
        rig.d.palette_fn(rig.t + 1.0 + i * 1e-4)
    per_call_us = (time.perf_counter() - t0) / n * 1e6
    print(f"\npalette_fn mid-crossfade, 6 devices: {per_call_us:.1f} us a call")
    assert per_call_us < 200.0                                         # README: <= 30 us (unloaded, here ~10)


def test_tick_budget(tmp_path):
    r = Rig(tmp_path, clock=True)
    r.d.set_target("alpha")
    r.run(5.0)
    samples = []
    for i in range(600):
        r.t = round(r.t + 0.1, 6)
        r.energy = 0.3 + 0.6 * ((i // 40) % 2)
        t0 = time.perf_counter()
        r.d.tick(r.t)
        samples.append((time.perf_counter() - t0) * 1000.0)
    p95 = sorted(samples)[int(0.95 * len(samples))]
    print(f"\ndirector tick: p50 {statistics.median(samples):.3f} ms, p95 {p95:.3f} ms")
    assert p95 < 10.0                                                  # README: <= 1 ms (unloaded, here ~0.5)


def test_bad_settings_fall_back_and_are_listed(tmp_path):
    bad = {"director": {"keep_s": -1, "low": "x", "bands": {"edges": [0.5, 0.2], "words": {}}},
           "lamps": {"roles": {"r": {"palette_role": "sideways", "avoid_hues": "green"}},
                     "placement_roles": {"over": "missing"}}}
    r = Rig(tmp_path, settings=bad)
    probs = "\n".join(r.d.settings_problems)
    for frag in ("director.keep_s", "director.low", "director.bands.edges", "palette_role", "avoid_hues",
                 "placement_roles.over"):
        assert frag in probs, frag
    r.d.set_target("alpha")
    r.run(1.0)
    assert r.d.cfg["keep_s"] == 900.0 and r.d.palette_fn(r.t) is not None
    r.d.configure({})                                                  # nothing at all: neutral roles, no bands
    r.run(1.0)
    assert set(r.d.look["lamps"]) == {"lamp.one", "lamp.two", "lamp.three", "lamp.four"}
    assert {x["role"] for x in r.d.look["lamps"].values()} == {"neutral"}


def test_missing_inputs_never_fail_a_tick(tmp_path):
    r = Rig(tmp_path, clock=True)

    def boom():
        raise RuntimeError("no")
    r.ctx.features = boom
    r.ctx.clock = lambda: {"bpm": "junk"}
    r.ctx.room = lambda: ["not", "a", "view"]
    r.ctx.situations = boom
    r.d.set_target("alpha")
    r.run(2.0)
    assert r.d.palette_fn(r.t) is not None and r.d.direction["energy"] == pytest.approx(0.65)


# -- the owner's rule: nothing branches on a name ------------------------------------------------------------------------
def _fixed_vocabulary() -> set[str]:
    """Words of the fixed vocabularies (contract tuples, vocab.yaml, contract TypedDict keys). Code may use these:
    they are the system's own words. Config names that happen to equal one (a band called "peak") are not flagged."""
    words: set[str] = set()
    for v in vars(contract).values():
        if isinstance(v, tuple) and all(isinstance(x, str) for x in v):
            words |= set(v)
        ann = getattr(v, "__annotations__", None)
        if isinstance(v, type) and isinstance(ann, dict):
            words |= set(ann)
    voc = yaml.safe_load(contract.VOCAB_FILE.read_text())
    for ws in voc.values():
        words |= {w for w in ws if isinstance(w, str)}
    return words


def _names_from_config() -> set[str]:
    names = {"piano"}
    vib = yaml.safe_load(contract.VIBES_FILE.read_text())
    names |= set((vib.get("vibes") or {}).keys())
    modes = json.loads((contract.CONFIG_ROOM_DIR / "modes.json").read_text())
    names |= set((modes.get("modes") or {}).keys())
    patch = yaml.safe_load(contract.PATCH_FILE.read_text())
    for did, d in (patch.get("devices") or {}).items():
        names.add(did)
        if isinstance(d, dict) and d.get("screen"):
            names.add(d["screen"])
    sh = yaml.safe_load(contract.SHOW_FILE.read_text())
    names |= set(((sh.get("lamps") or {}).get("roles") or {}).keys())                 # role names: config only
    names |= set(((sh.get("lamps") or {}).get("placement_roles") or {}).keys())
    names |= set((((sh.get("director") or {}).get("bands") or {}).get("words") or {}).keys())
    return (names - _fixed_vocabulary()) | {"piano"}


@pytest.mark.parametrize("module", ["director.py", "palette.py", "vibes.py", "api.py", "autopilot.py", "situations.py",
                                    "listener.py", "sources.py", "brain.py", "mllink.py", "structure.py"])
def test_no_vibe_mode_device_or_role_names_in_code(module):
    if not (SRC / module).exists():                                  # Stage 4's parallel builders: not landed yet
        pytest.skip(f"{module} isn't there yet")
    names = _names_from_config()
    tree = ast.parse((SRC / module).read_text())
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                doc_nodes.add(id(body[0].value))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc_nodes:
            if node.value in names:
                hits.append(node.value)
        if isinstance(node, ast.Compare):                               # == / in against a name-looking literal
            for c in node.comparators:
                if isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value in names:
                    hits.append(c.value)
    assert hits == [], f"{module} names config things in code: {sorted(set(hits))}"


def test_a_new_vibe_crosses_over_from_what_shows_now(rig):
    rig.d.set_target("alpha")
    rig.run(12.0)
    before = rig.d.palette_fn(rig.t)
    rig.d.set_target("beta")
    rig.run(0.1)
    now = rig.d.palette_fn(rig.t)
    assert max(abs(a - b) for a, b in zip(now["lamp.two"], before["lamp.two"])) < 0.05   # no jump to house
    after = rig.d.palette_fn(rig.t + dmod.ENTRY_FADE_S + 0.5)
    assert after["lamp.two"] != now["lamp.two"]


def test_snapshot_has_the_readme_keys_and_serialises(tmp_path):
    r = Rig(tmp_path, clock=True)
    r.d.set_target({"vibe": "alpha", "listening": True})
    r.d.step("wilder")
    r.run(3.0)
    snap = r.d.snapshot()
    for k in ("target", "readback", "direction", "look", "steps", "palette", "chips", "house", "low", "next_pending",
              "keep_in_s", "engaged", "vibes", "art", "stats"):
        assert k in snap, k
    assert set(snap["palette"]) >= {"pick", "current", "choices"}
    assert [c["name"] for c in snap["palette"]["choices"]][:4] == ["auto", "art", "neon", "rainbow"]
    assert set(snap["stats"]) >= {"tick_ms_p95", "looks", "events", "published"}
    assert set(snap["direction"]) == set(contract.DirectionData.__annotations__)
    assert set(snap["look"]) == set(contract.LookData.__annotations__)
    assert set(snap["readback"]) >= set(contract.VibeReadback.__annotations__) - {"overrides", "boot_id"}   # api adds boot_id
    json.dumps(snap)
    json.dumps(r.d.persist())
    json.dumps(r.d.catalog())
    assert set(r.d.catalog()) == set(contract.VibesCatalog.__annotations__)


@pytest.mark.parametrize("module", ["director.py", "situations.py", "listener.py", "api.py", "mllink.py",
                                    "structure.py"])
def test_no_genre_family_names_in_code(module):
    """README S4.0: Discogs parent -> vocab genre is config (show.yaml genre_map); no code names a parent."""
    if not (SRC / module).exists():
        pytest.skip(f"{module} isn't there yet")
    sh = yaml.safe_load(contract.SHOW_FILE.read_text())
    parents = set(contract.DISCOGS_PARENTS) | set(((sh.get("genre_map") or {}).get("parents") or {}).keys())
    hits = [n.value for n in ast.walk(ast.parse((SRC / module).read_text()))
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in parents]
    assert hits == [], f"{module} names genre families in code: {sorted(set(hits))}"


# -- Stage 4: sections, builds, drops, peak tokens (README S4.8) ---------------------------------------------------------
LATE_OK = {"director": {**SETTINGS["director"], "sections": {"max_late_bars": 4, "express_gain": 0.5}}}


def stage4_rig(tmp_path, settings_extra: dict | None = None, vibe: str = "alpha") -> Rig:
    settings = json.loads(json.dumps(SETTINGS))
    for k, v in (settings_extra or {}).items():
        settings[k] = json.loads(json.dumps(v))
    r = Rig(tmp_path, clock=True, settings=settings)
    r.sec = {"id": 0, "letter": None, "phase": "calm", "track": 0, "started_s": r.t}
    r.d.set_target(vibe)
    r.run(10.0)                                                       # past the entry fade
    return r


def test_sections_land_looks_and_a_predicted_repeat_restores_its_letters_look(tmp_path):
    r = stage4_rig(tmp_path, LATE_OK)
    g0, n0 = r.gobos(), len(r.looks())
    # section 1, confirmed 5 s after it began: within max_late_bars (4 bars = 8 s here), so it lands at its at_ms
    at = r.next_bar()
    r.sec.update(id=1, started_s=r.t - 5.0)
    r.event("section_change", at, section_id=1, letter=None, repeat_of=None, predicted=False,
            boundary_ms=(r.t - 5.0) * 1000.0)
    r.run_to(at + 0.15)
    ev = r.looks()[-1]
    assert len(r.looks()) == n0 + 1 and ev["detail"] == "section" and ev["at_ms"] == pytest.approx(at * 1000.0)
    assert r.gobos() == g0 + 1                                        # a vibe plays: the director steps GOBO
    look_a = r.d.look["look_id"]
    r.sec["letter"] = "A"                                             # structure names it: remembered
    r.run(1.0)
    mem = r.d.snapshot()["sections"]["memory"]
    assert mem["A"] == {"palette": "neon", "variation": r.d.variation, "look_id": look_a}
    var_a = r.d.variation
    r.d.action("next")                                                # a person's look: the palette moves on
    r.run(3.0)
    assert r.d.look["palette"]["name"] == "rainbow" and r.gobos() == g0 + 2   # the director's own: GOBO
    # section 2 (B)
    at = r.next_bar()
    r.sec.update(id=2, letter=None, started_s=r.t - 4.0)
    r.event("section_change", at, section_id=2, letter=None, predicted=False, boundary_ms=(r.t - 4.0) * 1000.0)
    r.run_to(at + 0.15)
    r.sec["letter"] = "B"
    r.run(1.0)
    assert r.d.snapshot()["sections"]["memory"]["B"]["palette"] == "rainbow"
    assert r.d.variation != var_a
    # a known repeat of A, predicted on its line: A's look comes back
    line = r.next_bar(2.0)
    r.event("section_change", line, section_id=3, letter="A", repeat_of=1, predicted=True, boundary_ms=line * 1000.0)
    r.run(0.1)
    assert r.d.pending["reason"] == "repeat" and r.d.pending["recall"]["palette"] == "neon"
    r.run_to(line + 0.15)
    assert r.d.look["palette"]["name"] == "neon" and r.d.variation == var_a
    assert r.looks()[-1]["detail"] == "repeat" and r.gobos() == g0 + 4      # one per look that landed
    st = r.d.snapshot()["sections"]["stats"]
    assert st["looks"] == 3 and st["recalled"] == 1 and st["events"]["section_change"] == 3
    logged = [json.loads(x) for x in r.log_path.read_text().splitlines()]
    assert [x["reason"] for x in logged if x["kind"] == "look_change" and x.get("section")] == ["section", "section",
                                                                                              "repeat"]
    assert all(x["context"]["track"] == 0 for x in logged if x["kind"] == "look_change" and x.get("section"))


def test_a_late_confirmed_boundary_keeps_the_look_and_moves_the_level(tmp_path):
    r = stage4_rig(tmp_path)
    r.energy = 0.3
    r.run(30.0)
    switch = r.t
    r.energy = 0.55                                                   # (the level stays inside the vibe's range)
    r.run(6.0)
    n0, look0 = len(r.looks()), r.d.look["look_id"]
    at = r.next_bar()                                                 # >= 6 s after the boundary: 3 bars late
    r.event("section_change", at, section_id=1, letter=None, predicted=False, boundary_ms=switch * 1000.0)
    r.run(0.1)
    assert r.d.pending is None
    r.run(0.2)
    x_part = r.d._express_now(r.t)
    r.run(1.0)                                                        # within one beat (0.5 s) it is all there
    x = r.d._express_now(r.t)
    assert 0.0 < x_part <= x and x == pytest.approx(0.5 * (r.d._mean_e(switch, r.t - 1.0) - r.d._mean_e(
        switch - 30.0, switch)), abs=0.1)
    r.run_to(at + 1.0)
    assert len(r.looks()) == n0 and r.d.look["look_id"] == look0      # the look is kept
    u = r.d.music["u"]
    look = r.d.look
    assert look["level"] == pytest.approx(round(dmod._lerp([0.5, 1.0], dmod._c01(u + x)), 3), abs=2e-3)
    assert look["level"] > dmod._lerp([0.5, 1.0], u) and look["accents"]["density"] > round(u, 3)
    assert look["level"] <= 1.0                                       # within the vibe's range
    kept = [json.loads(z) for z in r.log_path.read_text().splitlines() if '"section_kept"' in z]
    assert kept and kept[-1]["why"].startswith("late") and kept[-1]["express"] > 0
    assert r.d.snapshot()["sections"]["stats"]["expressed"] == 1


def test_time_vibes_show_a_section_inside_the_look(tmp_path):
    """A Dinner-like vibe (change_looks time) and a confirmed section: no look and no GOBO step (the screens keep
    their scene: structure leaves GOBO to the director while a vibe plays)."""
    r = stage4_rig(tmp_path, LATE_OK, vibe="gamma")
    n0, g0 = len(r.looks()), r.gobos()
    at = r.next_bar()
    r.event("section_change", at, section_id=1, letter=None, predicted=False,
            boundary_ms=(r.t - 1.0) * 1000.0)
    r.run(1.0)
    assert r.d.pending is None and len(r.looks()) == n0
    r.run_to(at + 1.0)
    assert r.gobos() == g0
    kept = [json.loads(z) for z in r.log_path.read_text().splitlines() if '"section_kept"' in z]
    assert "by time" in kept[-1]["why"]


def test_keep_this_look_holds_the_screens_through_a_section(tmp_path):
    r = stage4_rig(tmp_path, LATE_OK)
    r.d.action("keep")
    r.run(0.5)
    n0, g0 = len(r.looks()), r.gobos()
    at = r.next_bar()
    r.event("section_change", at, section_id=1, letter=None, predicted=False, boundary_ms=(r.t - 1.0) * 1000.0)
    r.run_to(at + 1.0)
    assert len(r.looks()) == n0 and r.gobos() == g0                  # no look, no scene change
    kept = [json.loads(z) for z in r.log_path.read_text().splitlines() if '"section_kept"' in z]
    assert kept[-1]["why"] == "Keep this look holds"


def test_track_change_clears_the_memory_and_moves_the_palette_on(tmp_path):
    r = stage4_rig(tmp_path, LATE_OK)
    r.d._sec_mem["A"] = {"palette": "neon", "variation": 0, "look_id": "L1"}
    g0 = r.gobos()
    r.sec.update(track=1, id=5, letter=None, started_s=r.t)
    r.event("track_change", r.t, track_id=1, via="sonos")
    r.run(0.1)
    assert r.d.snapshot()["sections"]["memory"] == {} and r.d.pending["reason"] == "track"
    r.run(3.0)
    assert r.d.look["palette"]["name"] == "rainbow" and r.gobos() == g0 + 1
    assert r.d.snapshot()["sections"]["stats"]["events"]["track_change"] == 1
    r.event("track_change", r.t, track_id=1, via="sonos")          # the same track again: nothing
    r.run(3.0)
    assert r.d.look["palette"]["name"] == "rainbow"


def test_the_silence_test_stands_down_while_structure_runs(tmp_path):
    r = stage4_rig(tmp_path)
    r.feat = {"t_ms": r.t * 1000.0, "energy": 0.6, "gate": 0.01}       # silence...
    r.run(9.0)
    r.feat = None                                                     # ...then sound: no song change from us
    r.run(1.0)
    assert r.d._song_change is False
    r.sec = None                                                      # no structure: the fallback test is back
    r.feat = {"t_ms": r.t * 1000.0, "energy": 0.6, "gate": 0.01}
    r.run(9.0)
    r.feat = None
    r.run(1.0)
    assert r.d._song_change is True


def test_build_steps_the_subdivision_raises_saturation_and_narrows_the_lamps(tmp_path):
    r = stage4_rig(tmp_path)
    sat0 = r.d.look["saturation"]
    assert r.d.look["accents"]["subdivision"] == 1.0
    r.sec.update(phase="build", bp=0.0)
    r.event("build", r.t, section_id=0, phase="build", build_progress=0.0)
    r.run(0.2)
    assert r.d.look["accents"]["subdivision"] == 0.5
    r.run(8.0)                                                        # 4 bars
    assert r.d.look["accents"]["subdivision"] == 0.25
    r.sec["bp"] = 0.8                                                 # past half way: every 2 bars
    r.run(4.2)
    assert r.d.look["accents"]["subdivision"] == 0.125
    assert r.d.look["saturation"] > sat0 and r.d.look["saturation"] == pytest.approx(
        sat0 + (1.0 - sat0) * 0.8, abs=0.02)
    r.sec.update(phase="calm", bp=0.0)
    r.run(2.0)
    assert r.d.look["accents"]["subdivision"] == 1.0
    # narrowing: a base-role lamp moves toward the accent colour
    look_pal = r.d.look["palette"]
    vs = r.d.vs
    dev = {"placement": None, "screen": None}
    rc = {"palette_role": "base", "follow_screen": False, "chroma_max": "chroma_max", "motion": False}
    acc = pal.to_oklab(pal.full(pal.rgb_of(look_pal["roles"]["accent"]["hex"])))
    c0 = r.d._lamp_colour(dev, rc, look_pal, {}, vs, 1.0, 0.0, 0.0, 0, r.t, 0.0)
    c1 = r.d._lamp_colour(dev, rc, look_pal, {}, vs, 1.0, 0.0, 0.0, 0, r.t, 0.6)
    assert math.dist(pal.to_oklab(c1), acc) < math.dist(pal.to_oklab(c0), acc)


def test_no_grammar_without_beat_lock_or_a_beat(tmp_path):
    r = stage4_rig(tmp_path)
    r.gate = "dmx"
    r.sec.update(phase="build", bp=0.5)
    r.run(1.0)
    assert r.d.look["accents"]["subdivision"] == 1.0
    r.event("drop_armed", r.next_bar(3.0), section_id=0, eta_beats=8)
    r.run(0.1)
    assert r.d._armed is None and r.d.pending is None


def test_an_armed_drop_dips_then_cuts_and_hits_the_lamps_never_the_screens(tmp_path):
    r = stage4_rig(tmp_path)
    r.sec.update(phase="build", bp=0.5)
    r.event("build", r.t - 4.0, section_id=0, phase="build", build_progress=0.3)
    r.run(0.5)
    line = r.next_bar(5.0)                                            # 8+ beats ahead
    g0, n0 = r.gobos(), len(r.looks())
    r.event("drop_armed", line, section_id=0, eta_beats=(line - r.t) / BEAT)
    r.run(0.1)
    assert r.d.pending["reason"] == "drop" and r.d.pending["transition"]["style"] == "cut"
    assert r.d.snapshot()["sections"]["tokens"] == {"per_hour": 2, "left": 1, "spent_last_hour": 0}   # reserved
    level0 = r.d.look["level"]
    screens_ok = []

    def check_screens(rig):
        pf = rig.d.palette_fn(rig.t)
        if pf:
            screens_ok.append(all(max(pf[k]) == pytest.approx(1.0) for k in ("tv.left", "tv.right")))

    r.run_to(line - 2 * BEAT * 4 / 2 - 0.3, check_screens)            # before the last bar: no dip yet
    assert all(x["level"] == pytest.approx(max(0.1, level0), abs=0.06) for x in r.d.look["lamps"].values())
    r.run_to(line - 0.3, check_screens)                               # the last bar: the lamps dip
    assert all(x["level"] == 0.25 for x in r.d.look["lamps"].values())
    assert all(max(r.d.palette_fn(r.t)[k]) == pytest.approx(0.25, abs=1e-3) for k in r.d.look["lamps"])
    hit = r.d._hit
    assert hit is not None and hit[0] == pytest.approx(line) and set(hit[2]) == set(r.d.look["lamps"])
    r.sec.update(phase="drop", bp=0.0)
    r.run_to(line + 0.05, check_screens)
    pf = r.d.palette_fn(line + 0.01)
    for did in r.d.look["lamps"]:
        assert pf[did] == pytest.approx(hit[2][did]) and max(pf[did]) == pytest.approx(1.0)   # the unison hit, full
    washes = {tuple(round(v, 3) for v in pf[d]) for d in ("lamp.one", "lamp.three", "lamp.four")}
    assert len(washes) == 1                                           # one colour across a role
    for k in ("tv.left", "tv.right"):                                 # screens: the look's colour, never the hit
        assert k not in hit[2] and max(pf[k]) == pytest.approx(1.0)
    assert r.looks()[-1]["detail"] == "drop" and r.looks()[-1]["at_ms"] == pytest.approx(line * 1000.0)
    assert len(r.looks()) == n0 + 1 and r.gobos() == g0 + 1 and r.d.look["transition"]["style"] == "cut"
    assert r.d.snapshot()["sections"]["tokens"] == {"per_hour": 2, "left": 1, "spent_last_hour": 1}
    r.event("drop", line + 0.1, section_id=1, strength=0.9)          # the drop heard at the line: nothing more
    r.run(1.5, check_screens)
    assert r.d.snapshot()["sections"]["stats"]["tokens_spent"] == 1
    after = r.d.palette_fn(r.t + 0.5)
    assert all(after[d] != pytest.approx(hit[2][d]) or max(after[d]) < 1.0 for d in ("lamp.two",))
    assert all(x["level"] > 0.25 for x in r.d.look["lamps"].values())
    assert screens_ok and all(screens_ok)
    spent = [json.loads(z) for z in r.log_path.read_text().splitlines() if '"token"' in z]
    assert [x["decision"] for x in spent] == ["reserved", "spent"]


def test_peak_tokens_are_a_sliding_hour_and_only_a_drop_after_a_build_spends_one(tmp_path):
    r = stage4_rig(tmp_path)

    def drop(after_build: bool) -> None:
        if after_build:
            r.event("build", r.t - 8.0, section_id=0, phase="build", build_progress=0.5)
            r.run(0.2)
        r.sec["phase"] = "drop"
        r.event("drop", r.t - 0.1, section_id=1, strength=0.8)
        r.run(0.1)

    drop(after_build=False)                                           # no build before it: shows inside the look
    assert r.d._drop_inside_s is not None and r.d.look["accents"]["density"] == 1.0
    assert r.d.look["saturation"] == pytest.approx(1.0)               # the vibe's top (neutral [0.5, 1.0])
    r.sec["phase"] = "calm"
    r.run(3.0)
    assert r.d._drop_inside_s is None
    n0 = len(r.looks())
    drop(after_build=True)                                            # spent: a hit at once, a cut at the next bar
    assert r.d._hit is not None and r.d._hit[0] == pytest.approx(r.t - 0.1, abs=0.11)
    assert r.d.pending["reason"] == "drop"
    r.sec["phase"] = "calm"
    r.run(3.0)
    assert len(r.looks()) == n0 + 1
    drop(after_build=True)
    r.sec["phase"] = "calm"
    r.run(3.0)
    drop(after_build=True)                                            # the third in the hour: refused
    assert r.d.snapshot()["sections"]["tokens"] == {"per_hour": 2, "left": 0, "spent_last_hour": 2}
    assert r.d.pending is None and r.d._drop_inside_s is not None
    r.sec["phase"] = "calm"
    r.run(1.0)
    r.t += 3600.0                                                     # an hour on: the first spends have aged out
    r.wall += 3600.0
    r.run(1.0)
    assert r.d.snapshot()["sections"]["tokens"]["left"] == 2
    drop(after_build=True)
    assert r.d.pending["reason"] == "drop"
    tok = [json.loads(z) for z in r.log_path.read_text().splitlines() if '"token"' in z]
    assert [x["decision"] for x in tok] == ["refused", "spent", "spent", "refused", "spent"]
    assert tok[0]["why"].startswith("no build") and tok[3]["why"] == "no token left this hour"


def test_a_feel_without_tokens_never_cuts_on_a_drop(tmp_path):
    r = stage4_rig(tmp_path)
    r.d.set_target({"vibe": "alpha", "overrides": {"peak_tokens_per_hour": 0}})
    r.run(1.0)
    r.event("build", r.t - 5.0, section_id=0)
    r.event("drop_armed", r.next_bar(4.0), section_id=0, eta_beats=8)
    r.run(0.2)
    assert r.d._armed is None and r.d.pending is None
    tok = [json.loads(z) for z in r.log_path.read_text().splitlines() if '"token"' in z]
    assert tok[-1]["decision"] == "refused" and "unset" in tok[-1]["why"]


def test_a_line_called_off_releases_its_token_and_a_missed_one_refunds_it(tmp_path):
    r = stage4_rig(tmp_path)
    r.event("build", r.t - 4.0, section_id=0, phase="build", build_progress=0.3)
    r.run(0.5)
    line = r.next_bar(5.0)
    r.event("drop_armed", line, section_id=0, eta_beats=(line - r.t) / BEAT)
    r.run(0.1)
    assert r.d._armed is not None and r.d.pending["reason"] == "drop"
    n0, g0 = len(r.looks()), r.gobos()
    r.event("drop_armed", line, section_id=0, eta_beats=None, detail="disarmed")   # the build gave out
    r.run(0.1)
    assert r.d._armed is None and r.d.pending is None and r.d._hit is None
    assert r.d.snapshot()["sections"]["tokens"]["left"] == 2               # the reservation is back
    r.run_to(line + 0.5)
    assert len(r.looks()) == n0 and r.gobos() == g0                        # no dip, no cut, no scene change
    # armed again; this time the line lands (the predictive landing) but no drop comes: the token comes back
    line = r.next_bar(5.0)
    r.event("drop_armed", line, section_id=0, eta_beats=(line - r.t) / BEAT)
    r.run_to(line + 0.2)
    assert r.looks()[-1]["detail"] == "drop" and r.gobos() == g0 + 1
    assert r.d.snapshot()["sections"]["tokens"]["spent_last_hour"] == 1
    r.run(1.0)
    r.event("drop_armed", line, section_id=0, eta_beats=None, detail="missed")
    r.run(0.1)
    toks = r.d.snapshot()["sections"]
    assert toks["tokens"]["spent_last_hour"] == 0 and toks["stats"]["tokens_refunded"] == 1
    tok = [json.loads(z) for z in r.log_path.read_text().splitlines() if '"token"' in z]
    assert [x["decision"] for x in tok] == ["reserved", "released", "reserved", "spent", "refunded"]


def test_room_direction_takes_the_local_mls_genre_over_the_listeners(tmp_path):
    r = stage4_rig(tmp_path)
    r.sec["genre"] = {"word": "jazz", "conf": 0.8, "parent": "Jazz", "parent_p": 0.8, "via": "ml"}
    r.run(1.0)
    assert r.d.direction["genre"] == "jazz" and r.d.snapshot()["words_from"]["genre"] == "ml"
    r.sec["genre"] = {"word": "jazz", "conf": 0.3, "parent": "Jazz", "parent_p": 0.3, "via": "ml"}   # not sure yet
    r.run(1.0)
    assert r.d.direction["genre"] != "jazz" or r.d.snapshot()["words_from"]["genre"] != "ml"
    r.sec = None                                                      # no structure: Stage 3's words
    r.run(4.0)
    assert r.d.snapshot()["words_from"]["genre"] != "ml"


def test_breakdown_lifts_the_floor_slows_and_desaturates_the_lamps(tmp_path):
    r = stage4_rig(tmp_path)
    r.run(5.0)
    before = r.d.look
    r.sec["phase"] = "breakdown"
    r.event("breakdown", r.t - 4.0, section_id=0)
    r.run(0.2)
    now = r.d.look
    assert now["floor"] == pytest.approx(before["floor"] + 0.1)
    assert now["motion"]["speed"] == pytest.approx(before["motion"]["speed"] * 0.5, abs=0.01)
    assert now["saturation"] == pytest.approx(before["saturation"] * 0.8, abs=0.01)
    assert now["screens"] == before["screens"]                       # screens untouched
    r.sec["phase"] = "calm"
    r.run(2.0)
    assert r.d.look["floor"] == pytest.approx(before["floor"])


def test_direction_carries_structures_section_and_the_medium_lanes_density(tmp_path):
    r = stage4_rig(tmp_path)
    r.sec.update(id=7, bars=3, expected=8, label="chorus")
    r.rhythm = {"t_ms": r.t * 1000.0, "density": 0.77}
    r.run(0.2)
    r.rhythm["t_ms"] = r.t * 1000.0
    r.run(0.1)
    d = r.d.direction
    assert d["section"] == {"id": 7, "label": "chorus", "phase": 0.375, "bars_in_section": 3}
    assert d["density"] == 0.77
    r.run(2.0)                                                        # the rhythm frame goes stale: onsets again
    assert r.d.direction["density"] != 0.77
    r.sec = None                                                      # no structure: Stage 3's section ref
    r.run(4.0)
    assert r.d.direction["section"]["id"] == r.d.look_n


def test_stage4_settings_and_bad_values(tmp_path):
    settings = json.loads(json.dumps(SETTINGS))
    settings["director"].update({"sections": {"max_late_bars": 2, "express_gain": 9}, "peaks": {"after_build_s": 12},
                                 "build": {"dip_level": 0.3, "narrow": "lots"}, "breakdown": "no",
                                 "screens": {"send_palette": "yes", "palette_transition_s": 2}})
    r = Rig(tmp_path, settings=settings)
    cfg = r.d.cfg
    assert cfg["sections"] == {"max_late_bars": 2.0, "express_gain": 0.5, "phase_hint_s": 1.5}
    assert cfg["peaks"] == {"after_build_s": 12.0} and cfg["build"]["dip_level"] == 0.3
    assert cfg["build"]["narrow"] == 0.6 and cfg["screens"] == {"send_palette": True, "palette_transition_s": 2.0}
    probs = "\n".join(r.d.settings_problems)
    for frag in ("director.sections.express_gain", "director.build.narrow", "director.breakdown",
                 "director.screens.send_palette"):
        assert frag in probs, frag


def test_the_screens_get_the_directors_palette_once_per_change(tmp_path):
    r = Rig(tmp_path, clock=True)
    patches: list[tuple[str, dict]] = []
    answers: dict[str, int] = {}

    async def room_patch(path: str, body: dict, timeout: float = 5.0):
        patches.append((path, body))
        return answers.get(path, 200), {"error": "not_live"} if answers.get(path, 200) >= 400 else {}

    async def main():
        r.ctx.loop = asyncio.get_running_loop()
        r.ctx.room_patch = room_patch
        r.room = {"current": "x", "stored_restore": {},
                  "screens": {"wall-left": {"layer": "live", "phase": "live", "cutting": False},
                              "wall-right": {"layer": "art", "phase": "idle", "cutting": False}}}

        async def run(seconds):
            for _ in range(int(round(seconds * 10))):
                r.t = round(r.t + 0.1, 6)
                r.d.tick(r.t)
                await asyncio.sleep(0)
                await asyncio.sleep(0)

        await run(1.0)
        assert patches == []                                           # no feel: nothing
        r.d.set_target("alpha")
        await run(1.0)
        assert patches == [("/api/v1/screens/wall-left/look", {"palette": "neon", "palette_transition_s": 4.0})]
        await run(2.0)
        assert len(patches) == 1                                       # only on change
        r.d.set_palette("rainbow")
        await run(1.0)
        assert patches[-1][1]["palette"] == "rainbow" and len(patches) == 2
        answers["/api/v1/screens/wall-right/look"] = 409               # wall-right goes live, but answers not_live
        r.room["screens"]["wall-right"] = {"layer": "live", "phase": "live", "cutting": False}
        await run(1.0)
        assert patches[-1][0] == "/api/v1/screens/wall-right/look" and len(patches) == 3
        assert "409" in r.d.snapshot()["screens_palette"]["error"]
        await run(2.0)
        assert len(patches) == 3                                       # retried only after SCREENS_RETRY_S
        answers.clear()
        await run(dmod.SCREENS_RETRY_S)
        assert patches[-1] == ("/api/v1/screens/wall-right/look", {"palette": "rainbow", "palette_transition_s": 4.0})
        n = len(patches)
        r.d.set_palette("art")                                         # an art palette: the screens keep their own
        await run(1.0)
        assert len(patches) == n
        assert "art palette" in r.d.snapshot()["screens_palette"]["why"]
        r.d.set_palette("rainbow")                                     # back to the palette they have: no resend
        await run(1.0)
        assert len(patches) == n
        r.room["screens"]["wall-left"] = {"layer": "art", "phase": "idle", "cutting": False}
        await run(0.5)
        r.room["screens"]["wall-left"] = {"layer": "live", "phase": "entering", "cutting": True}
        await run(0.5)
        assert len(patches) == n                                       # never while it cuts
        r.room["screens"]["wall-left"] = {"layer": "live", "phase": "live", "cutting": False}
        await run(0.5)
        assert patches[-1][0] == "/api/v1/screens/wall-left/look"      # back live: it gets the palette again
        r.d.stop()

    asyncio.run(main())
    lines = [json.loads(z) for z in r.log_path.read_text().splitlines() if '"screens_palette"' in z]
    assert lines and all(x["palette"] in ("neon", "rainbow") for x in lines)


def test_tick_budget_with_sections_a_build_and_an_armed_drop(tmp_path):
    r = stage4_rig(tmp_path)
    r.sec.update(phase="build", bp=0.4, letter="A")
    r.event("build", r.t, section_id=0)
    r.event("drop_armed", r.t + 120.0, section_id=0, eta_beats=240)   # armed for the whole run: hit colours each tick
    r.run(1.0)
    samples = []
    for i in range(600):
        r.t = round(r.t + 0.1, 6)
        r.sec["bp"] = min(1.0, 0.4 + i / 1000.0)
        r.energy = 0.3 + 0.6 * ((i // 40) % 2)
        t0 = time.perf_counter()
        r.d.tick(r.t)
        samples.append((time.perf_counter() - t0) * 1000.0)
    p95 = sorted(samples)[int(0.95 * len(samples))]
    print(f"\ndirector tick (Stage 4: a build, an armed drop): p50 {statistics.median(samples):.3f} ms, "
          f"p95 {p95:.3f} ms")
    assert r.d._armed is not None and r.d.look["accents"]["subdivision"] < 1.0
    assert p95 < 10.0                                                  # README: <= 1 ms (unloaded)


def test_a_section_boundary_at_a_drops_cut_is_the_same_boundary(tmp_path):
    r = stage4_rig(tmp_path, LATE_OK)
    r.event("build", r.t - 6.0, section_id=0)
    r.run(0.2)
    r.sec["phase"] = "drop"
    r.event("drop", r.t - 0.1, section_id=1, strength=0.9)       # spent: a cut at the next bar
    r.run(0.1)
    cut_at = r.d.pending["at_s"]
    r.run_to(cut_at + 0.2)
    n = len(r.looks())
    r.event("section_change", r.next_bar(), section_id=1, letter=None, predicted=False,
            boundary_ms=(cut_at - 1.0) * 1000.0)
    r.run(3.0)
    assert len(r.looks()) == n and r.d.pending is None             # no second look for the same boundary
    kept = [json.loads(z) for z in r.log_path.read_text().splitlines() if '"section_kept"' in z]
    assert kept[-1]["why"] == "the same boundary as the drop look"


def test_a_new_songs_own_section_boundary_lands_with_the_track_look(tmp_path):
    r = stage4_rig(tmp_path, LATE_OK)
    g0, n0 = r.gobos(), len(r.looks())
    t_change = r.t
    r.sec.update(track=1, id=4, letter=None, started_s=t_change)
    r.event("track_change", t_change, track_id=1, via="sonos")
    line = r.next_bar(1.1) + 4 * BEAT                                 # structure's GOBO line, a bar after ours
    r.event("section_change", line, section_id=4, letter=None, predicted=False, boundary_ms=t_change * 1000.0,
            detail="track")
    r.run(0.1)
    assert r.d.pending["reason"] == "track" and r.d.pending["at_s"] == pytest.approx(line)
    r.run_to(line + 0.2)
    assert len(r.looks()) - n0 == 2 and r.looks()[-1]["at_ms"] == pytest.approx(line * 1000.0)   # re-announced
    assert r.d.look["palette"]["name"] == "rainbow" and r.gobos() == g0 + 1     # with the look, on its line
    assert r.d.snapshot()["sections"]["stats"]["looks"] == 1
