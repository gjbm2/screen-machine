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
import os
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


def test_with_no_feel_the_colours_still_resolve_from_no_feel_palettes(tmp_path):
    st = json.loads(json.dumps(SETTINGS))
    st.setdefault("director", {})["no_feel"] = {"palettes": ["ember"]}      # owner, 17 Oct: always a palette
    r = Rig(tmp_path, settings=st)
    r.run(2.0)
    assert r.d.palette_fn(r.t) is not None and not r.d.readback()["known"]
    assert (r.d.look or {}).get("palette", {}).get("name") == "ember"
    assert r.d.look["lamps"] and r.d.look["screens"]                       # 75's pass check: the devices get colours
    amber = (1.0, 0.62, 0.32)
    assert all(tuple(round(x, 2) for x in c) != amber for c in r.colours().values())
    r.d.set_target({"vibe": "alpha"}, "mode")
    r.run(1.0)
    r.d.set_target({"vibe": None})                                          # back to no feel: colours stay
    r.run(1.0)
    assert r.d.palette_fn(r.t) is not None



def test_with_no_feel_next_look_lands_and_does_not_hold_the_room(tmp_path):
    """No feel: a person's Next look (and a song change) still lands with no_feel's palettes. It used to stay pending
    for ever, so _held() read true: every track event held and room.direction.hold stuck."""
    st = json.loads(json.dumps(SETTINGS))
    st.setdefault("director", {})["no_feel"] = {"palettes": ["ember", "gold"]}
    r = Rig(tmp_path, settings=st)
    r.run(2.0)
    before = (r.d.look or {}).get("palette", {}).get("name")
    r.d.action("next")
    r.run(6.0)
    assert r.d.pending is None and not r.d.next_pending and not r.d._held()
    assert r.d.direction["hold"] is False
    assert (r.d.look or {}).get("palette", {}).get("name") != before        # the palette moved on
    st["director"]["no_feel"] = {"palettes": []}                             # no colours: a change is dropped
    (tmp_path / "b").mkdir()
    r2 = Rig(tmp_path / "b", settings=st)
    r2.run(1.0)
    r2.d.action("next")
    r2.run(6.0)
    assert r2.d.pending is None and not r2.d._held()


def test_with_no_feel_a_quiet_room_keeps_its_colours(tmp_path):
    """09's audit F3 (21 Oct): a relaxed room with no feel handed the lamps to artnet_out's P0 idle, flipping the page
    to "Show stopped sending" about 400 times a day. The director always resolves a palette: its colours stay."""
    st = json.loads(json.dumps(SETTINGS))
    st.setdefault("director", {})["no_feel"] = {"palettes": ["ember"]}
    r = Rig(tmp_path, settings=st)
    r.run(2.0)
    before = r.d.palette_fn(r.t)
    assert before is not None
    r.situations = {"quiet": {"value": True}}
    r.run(8.0)
    assert r.d._relaxed and not r.d.known
    assert r.d.palette_fn(r.t) is not None and r.d.maestro_palette_fn(r.t) is not None


def test_house_and_low_take_the_wanted_state_and_keep_toggles(rig):
    """09's audit F6: the page sends the state it wants (a stale card or a second phone can't flip it the wrong way);
    without ``on`` it toggles, as before. F13: a second Keep this look stops keeping."""
    rig.d.set_target("alpha")
    rig.run(0.5)
    for _ in range(2):
        rig.d.action("house", True)
        assert rig.d.house is True and rig.d.low is False
    rig.d.action("low", True)
    assert rig.d.low is True and rig.d.house is False                   # one or the other
    rig.d.action("low", True)
    assert rig.d.low is True
    rig.d.action("house", False)
    assert rig.d.house is False and rig.d.low is True                   # off leaves Lights low alone
    rig.d.action("low", False)
    rig.d.action("low", False)
    assert rig.d.low is False and rig.d.house is False
    rig.d.action("house")
    assert rig.d.house is True                                          # no "on": a toggle
    rig.d.action("house")
    assert rig.d.house is False
    rig.d.action("keep")
    assert rig.d.keep_until is not None and any(c["id"] == "keep" for c in rig.d.chips())
    rig.d.action("keep")
    assert rig.d.keep_until is None and not any(c["id"] == "keep" for c in rig.d.chips())


def test_the_palettes_auto_choice_is_let_it_choose(rig):
    rig.d.set_target("alpha")
    rig.run(0.5)
    first = rig.d.snapshot()["palette"]["choices"][0]
    assert first["name"] == contract.PALETTE_AUTO and first["label"] == "Let it choose"   # only the room's Auto

def test_art_without_colour_to_give_falls_through_to_the_next_palette(tmp_path):
    st = json.loads(json.dumps(SETTINGS))
    st.setdefault("director", {})["no_feel"] = {"palettes": ["art", "ember"]}
    r = Rig(tmp_path, settings=st)
    dark = [(0.01, 0.01, 0.008), (0.0, 0.0, 0.0)]                      # the masked glass at night
    r.d.art = {"north-screen": {"colours": dark, "roles": pal.roles_from(dark), "at_s": 0.0}}
    r.run(1.0)
    assert (r.d.look or {}).get("palette", {}).get("name") == "ember"   # 75, 17 Oct: a rule, not a special case
    bright = [(0.9, 0.4, 0.1), (0.2, 0.5, 0.9)]
    r.d.art = {"north-screen": {"colours": bright, "roles": pal.roles_from(bright), "at_s": r.t}}
    r.run(1.0)
    assert (r.d.look or {}).get("palette", {}).get("name") == "art"


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
    assert looks[-1] == {"hue_depth": 0.0, "beat_lock": False, "beat_depth": 0.0}
    assert r.d.direction["beat_lock"] is False and r.d.direction["loose"] is False


def test_beat_lock_and_accents_after_one_phrase(tmp_path):
    r = Rig(tmp_path, clock=True)
    r.d.set_target("alpha")
    r.run(1.0)
    assert [kw for k, kw in r.calls if k == "look"][-1] == {"hue_depth": 0.0, "beat_lock": True, "beat_depth": 0.0}
    r.run(16.5)                                                        # one phrase (16 s at 120 bpm)
    depth = [kw for k, kw in r.calls if k == "look"][-1]
    level = r.d.look["level"]
    assert depth["beat_lock"] is True
    assert depth["hue_depth"] == pytest.approx(min(0.5, 1 - 0.1 / level), abs=1e-3)
    assert depth["beat_depth"] == depth["hue_depth"]
    r.gate = "dmx"                                                     # the clock unsure: no beat swell, "loosely"
    r.run(0.3)
    # README S5.14: Hue (hue_depth) and the virtual Maestro stop swelling; beat_lock stays the vibe's own and the
    # depth before the gate stays, so DMX keeps its beat at the clock's middle confidence (§5.4; the engine gates it)
    got = [kw for k, kw in r.calls if k == "look"][-1]
    assert got["hue_depth"] == 0.0 and got["beat_lock"] is True and got["beat_depth"] == depth["beat_depth"]
    assert r.d.direction["loose"] is True and r.d.direction["beat_lock"] is False and \
        r.d.look["accents"]["beat_lock"] is False                      # the renderer's words are unchanged
    r.gate = "none"
    r.run(0.3)
    assert [kw for k, kw in r.calls if k == "look"][-1]["hue_depth"] == 0.0


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
    # docs/LIGHTS.md (18 Oct): the look engine's own words (layer kinds, position modes, spreads, targets) and the room
    # frame's (compass words) are fixed vocabulary too, wherever a scene's layer id or a zone happens to reuse one
    import importlib
    for mod in ("looks", "space"):
        try:
            m = importlib.import_module(f"show_service.{mod}")
        except Exception:                                            # not landed yet
            continue
        for k, v in vars(m).items():
            if not k.isupper():
                continue
            vals = list(v.keys()) + list(v.values()) if isinstance(v, dict) else v
            if isinstance(vals, (tuple, list, set, frozenset)):
                for x in vals:
                    if isinstance(x, str):
                        words.add(x)
                    elif isinstance(x, (tuple, list, set, frozenset)):
                        words |= {y for y in x if isinstance(y, str)}
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
    # Stage 5 (README S5.0 rule 1): fixtures.yaml's ids, zones, screens and placements, and show.yaml's layer ids.
    # The builtin type ids are fixed vocabulary (fixtures.BUILTIN_TYPES), like contract.DRIVERS.
    from show_service import fixtures as fxmod
    fx = yaml.safe_load(contract.FIXTURES_FILE.read_text()) or {}
    for sec in ("fixtures", "outputs", "nodes", "types", "groups"):
        names |= set((fx.get(sec) or {}).keys())
    for f in (fx.get("fixtures") or {}).values():
        if isinstance(f, dict):
            names |= {f[k] for k in ("zone", "screen", "placement", "role") if isinstance(f.get(k), str)}
            names |= {t for t in f.get("tags") or [] if isinstance(t, str)}
    names |= {L["id"] for L in ((sh.get("looks") or {}).get("layers") or []) if isinstance(L, dict) and "id" in L}
    # README S5.24: the light scenes' names and their layer ids are config too; docs/LIGHTS.md §6, §12: so are the
    # effects', the transitions' and the light groups' names
    from show_service import light_scenes as lsmod
    for fname, sec in (("light_scenes.yaml", "scenes"), ("light_effects.yaml", "effects"),
                       ("light_transitions.yaml", "transitions")):
        p = contract.REPO_ROOT / "config" / "live" / fname
        ls_doc = (lsmod.load_yaml(p.read_text()) or {}) if p.exists() else {}
        for sname, sc in (ls_doc.get(sec) or {}).items():
            names.add(sname)
            names |= {L["id"] for L in ((sc or {}).get("layers") or []) if isinstance(L, dict) and "id" in L}
    names |= set(((sh.get("lights") or {}).get("groups") or {}).keys())
    names -= set(fxmod.BUILTIN_TYPES)
    return (names - _fixed_vocabulary()) | {"piano"}


@pytest.mark.parametrize("module", ["director.py", "palette.py", "vibes.py", "api.py", "autopilot.py", "situations.py",
                                    "listener.py", "sources.py", "brain.py", "mllink.py", "structure.py",
                                    "fixtures.py", "ofl.py", "outputs.py", "looks.py", "colour_mix.py", "hue_out.py",
                                    "artnet_lan.py", "cues.py", "light_scenes.py", "flow.py"])
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
    # README S5.18: pulse_depth and peak only in compose drive mode (the default, observe, leaves the message as it was)
    assert set(snap["direction"]) == set(contract.DirectionData.__annotations__) - set(contract.DIRECTION_DRIVE_KEYS)
    assert set(snap["look"]) == set(contract.LookData.__annotations__)
    assert set(snap["look"]["compose"]) == set(contract.ComposeView.__annotations__)
    assert set(snap["readback"]) >= set(contract.VibeReadback.__annotations__) - {"overrides", "boot_id"}   # api adds boot_id
    json.dumps(snap)
    json.dumps(r.d.persist())
    json.dumps(r.d.catalog())
    assert set(r.d.catalog()) == set(contract.VibesCatalog.__annotations__)


@pytest.mark.parametrize("module", ["director.py", "situations.py", "listener.py", "api.py", "mllink.py",
                                    "structure.py", "cues.py"])
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
    lh = r.d.look["hit"]                                              # README S5.14: the hit is LookData's ...
    assert lh["predicted"] is True and lh["t0_ms"] == pytest.approx(line * 1000.0, abs=0.1)
    assert set(lh["lamps"]) == set(r.d.look["lamps"]) and r.d.look["dip"] is True
    r.sec.update(phase="drop", bp=0.0)
    r.run_to(line + 0.05, check_screens)
    base = r.d.palette_fn(line + 0.01)                                # ... never palette_fn's (each output takes
    assert all(max(base[d]) < 1.0 - 1e-6 or base[d] != pytest.approx(hit[2][d]) for d in ("lamp.two",))   # it
    pf = r.d.maestro_palette_fn(line + 0.01)                          # by its caps); the virtual Maestro's lamps
    for did in r.d.look["lamps"]:                                     # (Hue's, P0-P4) get a predicted line's at once
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


def test_room_direction_takes_the_local_mls_genre_without_a_sure_listener(tmp_path):
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
    assert cfg["build"]["narrow"] == 0.6 and cfg["screens"] == {"send_palette": True, "palette_transition_s": 2.0,
                                                                "hold_hand_picks": True, "own_echo_s": 3.0}   # S5.8
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
        assert patches == [("/api/v1/screens/wall-left/look", {"palette": "neon", "palette_transition_s": 4.0,
                                                               "source": contract.SHOW_SOURCE})]   # Stage 5: source
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
        assert patches[-1] == ("/api/v1/screens/wall-right/look", {"palette": "rainbow", "palette_transition_s": 4.0,
                                                                   "source": contract.SHOW_SOURCE})
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


# -- song ends (README S5.17) ------------------------------------------------------------------------------------------------
TT = json.loads(json.dumps(dmod.TRACK_TRANSITIONS))
OLD = {"genre": "house", "key": "C", "mode": "major", "bpm": 124.0, "energy": 0.7, "artist": "A"}


def facts(**kw) -> dict:
    f = {"silent": False, "phase": "calm", "gate": "beat", "old": dict(OLD), "new": None, "back": False}
    f.update(kw)
    return f


def choose(kind: str, *, style=None, dark=False, last=None, **kw) -> dict:
    ev = {"kind": kind, "id": f"{kind}:0", "via": kw.pop("via", "sonos"), "track_id": 0,
          "detail": kw.pop("detail", None)}
    return dmod.choose_track_transition(TT, kind, ev, facts(**kw), style, dark=dark, last=last)


def test_the_transition_table_gap_segue_and_the_feels():
    # a gap between the songs: through dark, both halves on the event, bounded
    t = choose("track_end", via="dsp", gate="none")
    assert t == {"style": "fade-dark", "out_s": 2.5, "in_s": 2.0, "seconds": None, "on": "now", "change": "big",
                 "dark_max_s": 8.0}
    assert choose("track_end_armed", phase="outro")["style"] == "fade-dark"           # a fade-out ending: a gap coming
    assert choose("track_end", silent=True)["style"] == "fade-dark"                   # silent at a Sonos end
    assert choose("track_change", dark=True, gate="none")["style"] == "fade-dark"     # the screens are dark: in_s
    # a gapless segue: a cut on the first downbeat with the clock at beat, else a crossfade
    assert choose("track_end") == {"style": "cut", "out_s": None, "in_s": None, "seconds": 0.0, "on": "downbeat",
                                   "change": "big", "dark_max_s": None}
    assert choose("track_change", gate="dmx")["style"] == "crossfade"                 # cut_gate: beat
    assert choose("track_change", gate="none")["seconds"] == 2.0
    assert choose("track_change", dark=True, back=True)["style"] == "cut"             # sound_back brought them back
    # the feel biases it: gentle never cuts (longer), wild cuts without the beat (now), shorter (bounded)
    g = choose("track_change", style="gentle")
    assert g["style"] == "crossfade" and g["seconds"] == 3.0
    gd = choose("track_end", style="gentle", via="dsp")
    assert gd["out_s"] == 3.75 and gd["in_s"] == 3.0
    w = choose("track_change", style="wild", gate="none")
    assert w["style"] == "cut" and w["on"] == "now"
    wd = choose("track_end", style="wild", via="dsp")
    assert wd["out_s"] == 1.5 and wd["in_s"] == 1.5                                   # min_s
    # no feel, or a word with no entry: the default rule, sensibly
    assert choose("track_change", style=None)["style"] == "cut"
    assert choose("track_change", style="unheard-of", gate="none")["style"] == "crossfade"
    # a sound_back repeats the end's choice
    assert choose("track_end", detail="sound_back", last=dict(gd)) == gd


def test_the_change_size_from_the_two_songs():
    near = {"genre": "house", "key": "A", "mode": "minor", "bpm": 125.0, "energy": 0.65, "artist": "a"}
    far = {"genre": "jazz", "key": "F#", "mode": "major", "bpm": 90.0, "energy": 0.3, "artist": "B"}
    assert choose("track_change", new=near)["change"] == "small"     # relative key, same genre/tempo/energy, artist
    assert choose("track_change", new=far)["change"] == "big"
    assert dmod.change_size(OLD, near, TT["change"])[1] == {"genre": False, "key": False, "tempo": False,
                                                             "energy": False, "artist": False}
    assert choose("track_change", new=None)["change"] == "big"                         # nothing known: unknown
    only = {"genre": None, "key": None, "mode": None, "bpm": None, "energy": None, "artist": "a"}
    assert choose("track_change", new=only)["change"] == "small"                       # Sonos: the same artist
    assert choose("track_change", new={**only, "artist": "Someone"})["change"] == "big"
    assert choose("track_end")["change"] == "big"                                      # before the new song: unknown
    assert dmod.change_size(OLD, {**OLD, "bpm": 248.0}, TT["change"])[1]["tempo"] is False   # an octave
    assert dmod.key_fifths("C", "major", "C", "minor") == 0 and dmod.key_fifths("C", "major", "G", "major") == 1
    assert dmod.key_fifths("C", "major", "F#", "major") == 6 and dmod.key_fifths("C", "major", None, None) is None


def _track_ev(kind: str, at_s: float, now_s: float, **kw) -> dict:
    return {"kind": kind, "id": f"{kind}:{kw.get('track_id', 0)}", "at_ms": at_s * 1000.0, "t_ms": now_s * 1000.0,
            "seq": 1, **kw}


def test_a_song_end_fades_the_lamps_through_the_floor_and_the_new_song_lands_its_look(tmp_path):
    """A time vibe too: the song change is the junction. The track_end (a gap: fade-dark) dips the lamps toward the
    vibe's floor over out_s, never off; the track_change's look lands on the next bar the lead allows, crossing over
    in in_s; the palette moves on (big)."""
    r = stage4_rig(tmp_path, vibe="gamma")
    lv0 = max(x["level"] for x in r.d.look["lamps"].values())
    pal0 = r.d.palette_name                                               # the look's choice ("art" here)
    end = _track_ev("track_end", r.t, r.t, via="dsp", track_id=0)
    got = r.d.track_transition(end, facts(gate="beat"))
    assert got["transition"]["style"] == "fade-dark"
    r.d.on_music_event({**end, "transition": got["transition"]})
    r.run(1.2)
    mid = max(x["level"] for x in r.d.look["lamps"].values())
    assert r.d.look["dip"] is True and mid < lv0
    r.run(got["transition"]["out_s"])                                     # out_s passed: at the floor, never off
    floor = r.d.vs["floor"]
    assert floor > 0 and all(x["level"] == pytest.approx(floor, abs=0.02) for x in r.d.look["lamps"].values())
    n0, g0 = len(r.looks()), r.gobos()
    ch = _track_ev("track_change", r.t, r.t, via="dsp", track_id=1, prev_track_id=0)
    got2 = r.d.track_transition(ch, facts(new={**OLD, "genre": "jazz", "bpm": 90.0, "key": "F#", "energy": 0.3}))
    assert got2["transition"]["style"] == "fade-dark" and got2["transition"]["change"] == "big"   # dark: in_s
    r.sec.update(track=1, id=3, letter=None, started_s=r.t)
    r.d.on_music_event({**ch, "transition": got2["transition"]})
    r.run(0.1)
    pd = r.d.pending
    assert pd["reason"] == "track" and pd["transition"] == {"style": "crossfade", "beats": None,
                                                            "seconds": got2["transition"]["in_s"]}   # the in-half
    bar = 4 * BEAT
    assert pd["at_s"] >= ch["t_ms"] / 1000.0 + 1.1 - 1e-6 and (pd["at_s"] / bar) == pytest.approx(
        round(pd["at_s"] / bar), abs=1e-6)                                # the first bar line the lead allows
    r.run(3.0)
    assert len(r.looks()) == n0 + 1 and r.gobos() == g0 + 1 and r.d.palette_name != pal0   # big: moved on
    assert r.d.look["dip"] is False and r.d._song_dip is None
    snap = r.d.snapshot()["sections"]["song_end"]
    assert snap["stats"]["dips"] == 1 and snap["junction"] is None and snap["stats"]["chosen"]["fade-dark"] == 2
    json.dumps(r.d.snapshot())


def test_a_small_change_keeps_the_palette_and_a_sound_back_or_dark_max_brings_the_lamps_back(tmp_path):
    r = stage4_rig(tmp_path, LATE_OK)
    pal0 = r.d.look["palette"]["name"]
    lv0 = max(x["level"] for x in r.d.look["lamps"].values())
    end = _track_ev("track_end", r.t, r.t, via="dsp", track_id=0)
    tr = r.d.track_transition(end, facts())["transition"]
    r.d.on_music_event({**end, "transition": tr})
    r.run(3.0)
    assert r.d._song_dip is not None
    back = _track_ev("track_end", r.t, r.t, via="dsp", track_id=0, detail="sound_back")
    assert r.d.track_transition(back, facts(back=True))["transition"] == tr          # the end's choice again
    r.d.on_music_event({**back, "transition": tr})
    r.run(3.0)
    assert r.d._song_dip is None and max(x["level"] for x in r.d.look["lamps"].values()) == pytest.approx(lv0, abs=0.05)
    ch = _track_ev("track_change", r.t, r.t, via="dsp", track_id=1, prev_track_id=0)
    tr2 = r.d.track_transition(ch, facts(back=True, new={**OLD, "key": "G", "artist": "A"}))["transition"]
    assert tr2["style"] == "cut" and tr2["on"] == "downbeat" and tr2["change"] == "small"   # back: a segue
    r.sec.update(track=1, id=3, letter=None, started_s=r.t)
    r.d.on_music_event({**ch, "transition": tr2})
    r.run(0.1)
    assert r.d.pending["transition"]["style"] == "cut"
    r.run(3.0)
    assert r.d.look["palette"]["name"] == pal0                                         # small: the same palette
    # dark_max_s: no new song and no sound: the lamps come back by themselves
    end2 = _track_ev("track_end", r.t, r.t, via="dsp", track_id=1)
    tr3 = r.d.track_transition(end2, facts())["transition"]
    r.d.on_music_event({**end2, "transition": tr3})
    r.run(2.0)
    assert r.d._song_dip is not None
    r.run(7.0)
    assert r.d._song_dip is None and r.d.snapshot()["sections"]["song_end"]["stats"]["dip_released"] == 2


def test_a_held_look_puts_held_on_the_event_and_no_look_fires(tmp_path):
    r = stage4_rig(tmp_path, LATE_OK)
    r.d.action("keep")
    r.run(0.5)
    n0, g0 = len(r.looks()), r.gobos()
    end = _track_ev("track_end", r.t, r.t, via="dsp", track_id=0)
    assert r.d.track_transition(end, facts()) == {"held": True}
    r.d.on_music_event({**end, "held": True})
    r.run(1.0)
    assert r.d._song_dip is None
    ch = _track_ev("track_change", r.t, r.t, via="sonos", track_id=1, prev_track_id=0)
    assert r.d.track_transition(ch, facts()) == {"held": True}
    r.d.on_music_event({**ch, "held": True})
    r.run(3.0)
    assert len(r.looks()) == n0 and r.gobos() == g0 and r.d.pending is None
    r.d.clear("keep")
    r.d.action("next")                                                    # a person's change pending: held too
    assert r.d.pending["by"] == "human"
    assert r.d.track_transition(_track_ev("track_end_armed", r.t + 20, r.t, via="sonos", track_id=1),
                                facts()) == {"held": True}
    assert r.d.ends_stats["held"] == 3


def test_a_new_song_after_a_quiet_gap_lands_its_look_but_talk_holds_it(tmp_path):
    r = stage4_rig(tmp_path, LATE_OK)
    r.situations = {"quiet": {"value": True}}                             # the gap between the songs
    r.run(6.0)
    assert r.d._relaxed
    r.situations = {"quiet": {"value": False}}
    n0 = len(r.looks())
    r.sec.update(track=1, id=3, letter=None, started_s=r.t)
    r.event("track_change", r.t, track_id=1, prev_track_id=0, via="sonos", transition=choose("track_change"))
    r.run(0.1)
    assert r.d.pending is not None and r.d.pending["reason"] == "track"  # the sound is back: the new song's look
    r.run(3.0)
    assert len(r.looks()) == n0 + 1
    r.situations = {"talking": {"value": True}}                           # people talk over the music: it holds
    r.run(6.0)
    assert r.d._relaxed
    r.event("track_change", r.t, track_id=2, prev_track_id=1, via="sonos", transition=choose("track_change"))
    r.run(0.1)
    assert r.d.pending is None or r.d.pending["reason"] != "track"


def test_without_a_feel_the_screens_still_get_a_transition(rig):
    rig.run(1.0)
    assert rig.d.snapshot()["neutral"] is True
    got = rig.d.track_transition(_track_ev("track_change", rig.t, rig.t, via="sonos", track_id=1,
                                           prev_track_id=0), facts(gate="none"))
    assert got["transition"]["style"] == "crossfade" and got["transition"]["seconds"] == 2.0   # the default rule
    assert rig.d.track_transition({"kind": "drop"}, {}) is None


def test_structure_puts_the_directors_transition_on_its_track_events(tmp_path):
    """The real hook (provides()["track_transition"] in ctx, as brain.add does): a gap in the sound -> structure's
    track_end carries the director's fade-dark, and the director dips the lamps."""
    from show_service import structure as smod
    r = stage4_rig(tmp_path, LATE_OK)
    r.sec = None                                                          # structure's own context instead
    s = smod.Structure(r.ctx, mono=lambda: r.t)
    s.configure({})
    r.ctx.track_transition = r.d.provides()["track_transition"]
    r.ctx.music_context = s.context
    r.ctx.on_music_event.append(r.d.on_music_event)
    r.run(5.0, each=lambda rr: s.tick(rr.t))
    r.feat = {"t_ms": r.t * 1000.0, "energy": 0.0, "gate": 0.0}           # silence (stale features)...
    r.run(5.0, each=lambda rr: s.tick(rr.t))
    ends = [p["data"] for p in r.pubs if p["type"] == contract.ROOM_EVENT and p["data"]["kind"] == "track_end"]
    assert len(ends) == 1 and ends[0]["via"] == "dsp" and ends[0]["transition"]["style"] == "fade-dark"
    assert r.d.look["dip"] is True
    r.feat = None                                                         # ...and the sound back
    r.run(1.0, each=lambda rr: s.tick(rr.t))
    ends = [p["data"] for p in r.pubs if p["type"] == contract.ROOM_EVENT and p["data"]["kind"] == "track_end"]
    assert ends[-1]["detail"] == "sound_back" and ends[-1]["transition"] == ends[0]["transition"]
    assert r.d._song_dip is None
    assert all(isinstance(p["data"]["id"], str) for p in r.pubs if p["type"] == contract.ROOM_EVENT)


def test_track_transitions_settings_and_bad_values(tmp_path):
    settings = json.loads(json.dumps(SETTINGS))
    settings["director"]["track_transitions"] = {
        "gap": {"out_s": 3.0, "dark_max_s": -1}, "segue": {"cut_gate": "dmx", "crossfade_s": 1.0},
        "styles": {"calm-ish": {"cut": "never", "scale": 2.0}, "odd": {"cut": "sometimes"}},
        "change": {"genre": False, "tempo_ratio": None, "unknown": "medium", "energy": 3}}
    r = Rig(tmp_path, settings=settings)
    tt = r.d.cfg["track_transitions"]
    assert tt["gap"]["out_s"] == 3.0 and tt["gap"]["dark_max_s"] == 8.0
    assert tt["segue"] == {"cut_gate": "dmx", "crossfade_s": 1.0}
    assert set(tt["styles"]) == {"calm-ish", "default"} and tt["styles"]["calm-ish"]["scale"] == 2.0
    assert tt["change"]["genre"] is False and tt["change"]["tempo_ratio"] is None
    assert tt["change"]["unknown"] == "big" and tt["change"]["energy"] == 0.25
    probs = "\n".join(r.d.settings_problems)
    for frag in ("track_transitions.gap.dark_max_s", "track_transitions.styles.odd", "track_transitions.change.unknown",
                 "track_transitions.change.energy"):
        assert frag in probs, frag
    sy = yaml.safe_load(contract.SHOW_FILE.read_text())
    probs2: list = []
    assert dmod.parse_track_transitions(sy["director"]["track_transitions"], probs2) == dmod.TRACK_TRANSITIONS
    assert probs2 == []                                                   # show.yaml = the code's starting points


# -- Stage 5: the screens' hand-pick hold (README S5.8) -------------------------------------------------------------------
LIVE = {"layer": "live", "phase": "live", "cutting": False}


class LookRoom:
    """A fake room's look route and its ``screen.look`` events (README S5.8); never the real room.
    ``new``: room with 2f's code (takes ``source``; its event carries it, "web" by default). ``new=False``: the old room
    (400 bad_request for a key it doesn't know; its events, when ``events``, carry no source). The event reaches the
    director before the PATCH answers, as room's WS can beat its HTTP answer."""

    def __init__(self, r: Rig, new: bool = True, events: bool = True) -> None:
        self.r, self.new, self.events = r, new, events
        self.patches: list[tuple[str, dict]] = []
        self.boot = "boot-1"

    async def patch(self, path: str, body: dict, timeout: float = 5.0):
        self.patches.append((path, dict(body)))
        sid = path.split("/")[4]
        body = dict(body)
        source = body.pop("source", None)
        if source is not None and not self.new:
            return 400, {"error": "bad_request", "detail": "body may contain only ['palette', ...]"}
        self.emit(sid, body["palette"], source or "web", changed=sorted(body))
        return 200, {"screen": sid}

    def emit(self, sid: str, palette, source: str = "web", changed=("palette",)) -> None:
        """A screen.look as room sends it (a person's pick when called by the test)."""
        if not self.events:
            return
        evt = {"screen": sid, "session": "s-" + sid, "changed": list(changed), "palette": palette}
        if self.new:
            evt["source"] = source
        self.r.d.on_room_event(contract.ROOM_SCREEN_LOOK, evt)

    def sent(self, since: int = 0) -> list[tuple[str, dict]]:
        return [(p.split("/")[4], b) for p, b in self.patches[since:]]


def hold_rig(tmp_path, *, new: bool = True, events: bool = True, settings: dict | None = None) -> tuple[Rig, LookRoom]:
    r = Rig(tmp_path, clock=True, settings=settings)
    room = LookRoom(r, new, events)
    r.ctx.room_boot = lambda: room.boot
    r.room = {"current": "m1", "stored_restore": {}, "screens": {"wall-left": dict(LIVE), "wall-right": dict(LIVE)}}
    r.d.on_mode_change(None, "m1")
    return r, room


async def ticks(r: Rig, seconds: float) -> None:
    for _ in range(int(round(seconds * 10))):
        r.t = round(r.t + 0.1, 6)
        r.wall += 0.1
        r.d.tick(r.t)
        await asyncio.sleep(0)
        await asyncio.sleep(0)


def go(r: Rig, room: LookRoom, body) -> None:
    async def main():
        r.ctx.loop = asyncio.get_running_loop()
        r.ctx.room_patch = room.patch
        try:
            await body()
        finally:
            r.d.stop()
    asyncio.run(main())


def logged(r: Rig, kind: str) -> list[dict]:
    return [e for e in r.d.log(LOG_ALL) if e["kind"] == kind]


LOG_ALL = 500
NEON = {"palette": "neon", "palette_transition_s": 4.0}


def pick_chip(r: Rig) -> dict | None:
    got = [c for c in r.d.chips() if c["id"] == contract.SCREEN_PICK]
    return got[0] if got else None


def test_the_source_probe_against_a_new_room(tmp_path):
    r, room = hold_rig(tmp_path)

    async def body():
        await ticks(r, 0.5)
        sp = r.d.snapshot()["screens_palette"]
        assert sp["look_source"] == "unknown" and sp["boot"] == "boot-1" and sp["hold"] is None
        r.d.set_target("alpha")
        await ticks(r, 0.5)
        assert room.sent() == [("wall-left", {**NEON, "source": contract.SHOW_SOURCE}),
                               ("wall-right", {**NEON, "source": contract.SHOW_SOURCE})]
        sp = r.d.snapshot()["screens_palette"]
        assert sp["look_source"] == "yes" and sp["own_echoes"] == 2 and sp["picks"] == 0
        assert set(sp["sent_at"]) == {"wall-left", "wall-right"} and pick_chip(r) is None
        room.boot = "boot-2"                                           # room_service restarted: probe again
        await ticks(r, 0.2)
        assert r.d.snapshot()["screens_palette"]["look_source"] == "unknown"
        r.d.set_palette("rainbow")
        await ticks(r, 0.5)
        assert [b.get("source") for _, b in room.sent(2)] == [contract.SHOW_SOURCE] * 2
        sp = r.d.snapshot()["screens_palette"]
        assert sp["look_source"] == "yes" and sp["boot"] == "boot-2" and sp["picks"] == 0

    go(r, room, body)
    assert [(e["state"], e["via"]) for e in logged(r, "look_source")] == [
        ("yes", "event"), ("unknown", "room_boot"), ("yes", "event")]


def test_the_source_probe_against_the_old_room(tmp_path):
    r, room = hold_rig(tmp_path, new=False)

    async def body():
        r.d.set_target("alpha")
        await ticks(r, 0.5)
        assert room.sent() == [("wall-left", {**NEON, "source": contract.SHOW_SOURCE}),   # refused (400 bad_request)
                               ("wall-left", NEON),                                     # again at once, without
                               ("wall-right", NEON)]                                    # and the next one without
        sp = r.d.snapshot()["screens_palette"]
        assert sp["look_source"] == "no" and sp["sent"] == {"wall-left": "neon", "wall-right": "neon"}
        assert sp["error"] is None and sp["picks"] == 0 and sp["own_echoes"] == 2   # no source: ours by the echo
        r.d.set_palette("rainbow")
        await ticks(r, 0.5)
        assert room.sent(3) == [("wall-left", {**NEON, "palette": "rainbow"}),
                                ("wall-right", {**NEON, "palette": "rainbow"})]
        r.d.on_room_event(contract.ROOM_SCREEN_LOOK, {"screen": "wall-left", "session": "s", "source": "web",
                                                      "changed": ["direction"]})   # any event with a source: yes
        await ticks(r, 0.1)
        sp = r.d.snapshot()["screens_palette"]
        assert sp["look_source"] == "yes" and sp["picks"] == 0 and r.d.screen_pick is None   # no palette: no pick

    go(r, room, body)
    assert [(e["state"], e["via"]) for e in logged(r, "look_source")] == [("no", "patch"), ("yes", "event")]


def test_our_echo_is_not_a_pick_and_another_palette_or_a_late_echo_is(tmp_path):
    r, room = hold_rig(tmp_path)

    async def body():
        r.d.set_target("alpha")
        await ticks(r, 0.5)
        room.emit("wall-left", "ice", source=contract.SHOW_SOURCE)     # source show_service: ours, whatever it says
        room.emit("wall-left", "neon", source="web")                   # ours within own_echo_s (a PATCH that lost
        await ticks(r, 0.2)                                            # its source on the way)
        assert r.d.screen_pick is None and r.d.snapshot()["screens_palette"]["own_echoes"] == 4
        room.emit("wall-right", "ember")                               # another palette: a person's
        await ticks(r, 0.1)
        assert r.d.screen_pick["palette"] == "ember" and r.d.screen_pick["screen"] == "wall-right"

    go(r, room, body)

    r2, room2 = hold_rig(tmp_path / "old" if (tmp_path / "old").mkdir() is None else tmp_path, new=False)

    async def body2():
        r2.d.set_target("alpha")
        await ticks(r2, 0.5)
        await ticks(r2, 2.0)
        room2.emit("wall-left", "neon")                                # 2.5 s after we sent it: ours
        assert r2.d.screen_pick is None
        await ticks(r2, 1.0)
        room2.emit("wall-left", "neon")                                # 3.5 s: past own_echo_s, so a person's
        await ticks(r2, 0.1)
        assert r2.d.screen_pick == {"palette": "neon", "label": "Neon", "screen": "wall-left",
                                    "at": pytest.approx(r2.wall - 0.1, abs=1e-3), "source": None}

    go(r2, room2, body2)


def test_a_hand_pick_holds_every_screen_with_a_chip_and_the_lamps_carry_on(tmp_path):
    r, room = hold_rig(tmp_path)

    async def body():
        r.d.set_target("alpha")
        await ticks(r, 0.5)
        n = len(room.patches)
        assert not r.d.snapshot()["engaged"]
        w = r.wall
        room.emit("wall-right", "ember")                               # a person, on wall-right's card
        await ticks(r, 0.1)
        chip = pick_chip(r)
        assert {k: v for k, v in chip.items() if k != "value"} == {
            "id": contract.SCREEN_PICK, "kind": contract.SCREEN_PICK, "label": "Colours: Ember (picked on a screen)",
            "expires_in_s": None}
        assert chip["value"] == {"palette": "ember", "label": "Ember", "screen": "wall-right",
                                 "at": pytest.approx(w, abs=1e-3), "source": "web"}
        snap = r.d.snapshot()
        assert snap["engaged"] and r.d.provides()["engaged"]() is True     # Auto won't leave the mode under it
        assert snap["screens_palette"]["hold"] == chip["value"]
        assert snap["screens_palette"]["why"] == "held: Ember was picked on a screen"
        json.dumps(snap)
        before = r.colours()
        r.d.set_palette("rainbow")                                     # the director moves on...
        await ticks(r, 2.0)
        assert r.d.look["palette"]["name"] == "rainbow" and r.colours() != before   # ...the lamps follow it...
        assert len(room.patches) == n                                  # ...the screens don't: every one is held
        r.room["screens"]["wall-third"] = dict(LIVE)                   # a screen going live: held too
        await ticks(r, 0.5)
        assert len(room.patches) == n
        for palette, label in (("zz-unknown", "zz-unknown"), ({"stops": ["#ff0000", "#0000ff"]}, "custom colours"),
                               (None, "the scene's own"), ("gold", "Gold")):     # null: the card's own words
            room.emit("wall-left", palette)                            # a newer pick replaces it: the chip follows
            await ticks(r, 0.1)
            assert [c["label"] for c in r.d.chips() if c["kind"] == contract.SCREEN_PICK] == [
                f"Colours: {label} (picked on a screen)"]
            assert r.d.screen_pick["palette"] == palette and r.d.screen_pick["screen"] == "wall-left"
        assert len(room.patches) == n and r.d.snapshot()["screens_palette"]["picks"] == 5

    go(r, room, body)
    picked = [e for e in logged(r, "screen_pick") if e["what"] == "picked"]
    assert len(picked) == 5 and all(e["held"] for e in picked) and picked[0]["event"]["palette"] == "ember"
    lines = [json.loads(z) for z in r.log_path.read_text().splitlines() if '"screen_pick"' in z]
    assert len(lines) == 5                                             # in director.jsonl too


@pytest.mark.parametrize("how", ["chip", "all", "mode"])
def test_the_hold_ends_by_the_chip_back_to_auto_or_a_mode_change_and_resends_at_once(tmp_path, how):
    r, room = hold_rig(tmp_path)

    async def body():
        r.d.set_target("alpha")
        r.d.step("wilder")                                             # another nudge: × clears only the hold
        await ticks(r, 0.5)
        room.emit("wall-left", "ember")
        await ticks(r, 0.5)
        n = len(room.patches)
        if how == "chip":
            assert r.d.clear(contract.SCREEN_PICK) == [contract.SCREEN_PICK] and r.d.steps == 1
        elif how == "all":
            assert sorted(r.d.clear("all")) == sorted(["steps", contract.SCREEN_PICK])
        else:
            r.d.on_mode_change("m1", "m2")
        assert r.d.screen_pick is None and pick_chip(r) is None
        await ticks(r, 0.1)                                            # at once, to every live screen
        assert room.sent(n) == [("wall-left", {**NEON, "source": contract.SHOW_SOURCE}),
                                ("wall-right", {**NEON, "source": contract.SHOW_SOURCE})]
        await ticks(r, 1.0)
        assert r.d.screen_pick is None and len(room.patches) == n + 2   # our own echoes are not picks
        snap = r.d.snapshot()
        assert snap["screens_palette"]["hold"] is None and snap["screens_palette"]["why"] is None
        assert snap["engaged"] == (how == "chip")                      # the steps stay only after the ×

    go(r, room, body)
    cleared = [e for e in logged(r, "screen_pick") if e["what"] == "cleared"]
    assert [e["why"] for e in cleared] == [{"chip": "the chip's ×", "all": "back to auto",
                                            "mode": "the mode changed"}[how]]
    assert cleared[0]["pick"]["palette"] == "ember"


def test_the_hold_is_persisted_and_restored_until_the_mode_differs(tmp_path):
    r, room = hold_rig(tmp_path)

    async def body():
        r.d.set_target("alpha")
        await ticks(r, 0.5)
        room.emit("wall-left", "ember")
        await ticks(r, 0.1)

    go(r, room, body)
    saved = json.loads(json.dumps(r.d.persist()))
    assert saved["nudges"][contract.SCREEN_PICK] == {k: v for k, v in r.d.screen_pick.items()}
    assert saved["nudges"][contract.SCREEN_PICK]["label"] == "Ember"

    for sub, mode, held in (("same", "m1", True), ("other", "m2", False)):
        (tmp_path / sub).mkdir()
        r2, room2 = hold_rig(tmp_path / sub)
        r2.d.restore(saved)
        assert pick_chip(r2)["label"] == "Colours: Ember (picked on a screen)"
        r2.room["current"] = mode

        async def body2():
            await ticks(r2, 0.5)

        go(r2, room2, body2)
        if held:                                                       # the same mode: still held, nothing sent
            assert r2.d.screen_pick["palette"] == "ember" and room2.patches == []
            assert r2.d.snapshot()["engaged"]
        else:                                                          # another mode: it ends, and ours goes out
            assert r2.d.screen_pick is None and [s for s, _ in room2.sent()] == ["wall-left", "wall-right"]

    r3, _ = hold_rig(tmp_path / "junk" if (tmp_path / "junk").mkdir() is None else tmp_path)
    r3.d.restore({"nudges": {contract.SCREEN_PICK: "junk"}})
    assert r3.d.screen_pick is None
    r3.d.restore({"nudges": {contract.SCREEN_PICK: {"palette": None, "screen": 5, "at": "x"}}})
    assert r3.d.screen_pick["label"] == "the scene's own" and r3.d.screen_pick["screen"] == ""
    assert r3.d.screen_pick["at"] == r3.wall and r3.d.screen_pick["source"] is None


def test_hold_hand_picks_false_only_logs_and_turning_it_off_ends_a_hold(tmp_path):
    r, room = hold_rig(tmp_path)

    async def body():
        r.d.set_target("alpha")
        await ticks(r, 0.5)
        room.emit("wall-left", "ember")
        await ticks(r, 0.1)
        assert r.d.screen_pick is not None
        n = len(room.patches)
        off = json.loads(json.dumps(SETTINGS))
        off["director"]["screens"] = {"hold_hand_picks": False}
        r.d.configure(off)                                             # hot-reloaded off: the hold ends at once
        await ticks(r, 0.1)
        assert r.d.screen_pick is None and [s for s, _ in room.sent(n)] == ["wall-left", "wall-right"]
        room.emit("wall-right", "gold")                                # a pick now is only logged
        await ticks(r, 0.5)
        assert r.d.screen_pick is None and pick_chip(r) is None and not r.d.snapshot()["engaged"]
        r.d.set_palette("rainbow")
        await ticks(r, 0.5)                                            # and the director sends as in Stage 4
        assert [(s, b["palette"]) for s, b in room.sent(n + 2)] == [("wall-left", "rainbow"), ("wall-right", "rainbow")]

    go(r, room, body)
    picks = [e for e in logged(r, "screen_pick")]
    assert [(e["what"], e.get("held")) for e in picks] == [("picked", True), ("cleared", None), ("picked", False)]
    assert "hold_hand_picks" in picks[1]["why"]


def test_the_hold_settings_and_bad_values(tmp_path):
    s = json.loads(json.dumps(SETTINGS))
    s["director"]["screens"] = {"hold_hand_picks": "yes", "own_echo_s": "soon"}
    r = Rig(tmp_path, settings=s)
    assert any("director.screens.hold_hand_picks" in p for p in r.d.settings_problems)
    assert any("director.screens.own_echo_s" in p for p in r.d.settings_problems)
    assert r.d.cfg["screens"]["hold_hand_picks"] is True and r.d.cfg["screens"]["own_echo_s"] == 3.0
    s["director"]["screens"] = {"own_echo_s": 1.5}
    r.d.configure(s)
    assert r.d.cfg["screens"]["own_echo_s"] == 1.5 and not r.d.settings_problems
    r.d.on_room_event("log", {"level": "info", "msg": "x"})            # other events, and junk: nothing
    r.d.on_room_event(contract.ROOM_SCREEN_LOOK, "junk")
    # README S5.14: no feel playing, so the director isn't sending the screens' palette: a pick is only logged
    r.d.on_room_event(contract.ROOM_SCREEN_LOOK, {"palette": "ember"})
    assert r.d.screen_pick is None and not r.d.snapshot()["engaged"]
    r.d.set_target("alpha")
    r.run(0.3)
    r.d.on_room_event(contract.ROOM_SCREEN_LOOK, {"palette": "ember"})   # no screen: still a person's pick
    assert r.d.screen_pick["screen"] == "" and r.d.screen_pick["source"] is None
    assert "on_room_event" in r.d.provides()
    # send_palette off (hot-reloaded): the director isn't sending, so the hold ends and a new pick is only logged
    s["director"]["screens"] = {"send_palette": False}
    r.d.configure(s)
    r.run(0.3)
    assert r.d.screen_pick is None and not r.d.snapshot()["engaged"]
    r.d.on_room_event(contract.ROOM_SCREEN_LOOK, {"palette": "gold", "screen": "wall-left"})
    assert r.d.screen_pick is None
    picks = [json.loads(z) for z in r.log_path.read_text().splitlines() if '"screen_pick"' in z]
    assert [(e["what"], e.get("held"), e.get("why")) for e in picks] == [
        ("picked", False, "no palette is playing"), ("picked", True, None),
        ("cleared", None, "show.yaml director.screens.send_palette is off"),
        ("picked", False, "director.screens.send_palette is off")]


def test_the_hold_through_roomlink_and_a_fake_rooms_events(tmp_path):
    """The real RoomLink against a fake room on an ephemeral 127.0.0.1 port (never room's): PATCH
    /api/v1/screens/{id}/look emits screen.look on WS /api/v1/events as room_service does (the new room: with
    ``source``; the old: 400 bad_request for it, events without one); status carries service.started_at."""
    from aiohttp import web
    from show_service.autopilot import RoomLink

    class Room:
        def __init__(self) -> None:
            self.new = True
            self.started_at = "2026-10-16T09:00:00+00:00"
            self.patches: list[tuple[str, dict]] = []
            self.clients: set = set()

        def status(self) -> dict:
            return {"service": {"version": "x", "started_at": self.started_at},
                    "mode": {"current": "m1", "target": None, "transition": None, "last_transition": None},
                    "screens": {s: {"observed": {"layer": "live", "phase": "live"}} for s in ("wall-left", "wall-right")},
                    "subsystems": {}, "stored": {}, "config": {"revision": "r1", "error": None}}

        async def start(self) -> None:
            async def h_status(q):
                return web.json_response(self.status())

            async def h_config(q):
                return web.json_response({"config": {"modes": {"m1": {"label": "M1"}}}, "revision": "r1"})

            app = web.Application()
            app.router.add_get("/api/v1/status", h_status)
            app.router.add_get("/api/v1/room/config", h_config)
            app.router.add_get("/api/v1/events", self.h_events)
            app.router.add_patch("/api/v1/screens/{id}/look", self.h_look)
            self.runner = web.AppRunner(app, shutdown_timeout=0.2)
            await self.runner.setup()
            site = web.TCPSite(self.runner, "127.0.0.1", 0)
            await site.start()
            port = self.runner.addresses[0][1]
            assert port not in (6454, 6980, 6981, 6982, 8700, 8701, 8702, 8703)
            self.url = f"http://127.0.0.1:{port}"

        async def h_events(self, q):
            ws = web.WebSocketResponse()
            await ws.prepare(q)
            await ws.send_json({"type": "status", "data": self.status()})
            self.clients.add(ws)
            try:
                async for _ in ws:
                    pass
            finally:
                self.clients.discard(ws)
            return ws

        async def send(self, msg: dict) -> None:
            for ws in list(self.clients):
                await ws.send_json(msg)

        async def h_look(self, q):
            sid = q.match_info["id"]
            b = await q.json()
            self.patches.append((sid, dict(b)))
            src = b.pop("source", None)
            if src is not None and not self.new:
                return web.json_response({"error": "bad_request", "detail": "test"}, status=400)
            await self.pick(sid, b["palette"], src or "web", sorted(b))
            return web.json_response({"screen": sid})

        async def pick(self, sid: str, palette, source: str = "web", changed=("palette",)) -> None:
            evt = {"screen": sid, "session": "s-" + sid, "changed": list(changed), "palette": palette}
            if self.new:
                evt["source"] = source
            await self.send({"type": "screen.look", "data": evt})

    r = Rig(tmp_path, clock=True)
    room = Room()

    async def main():
        await room.start()
        loop = asyncio.get_running_loop()
        r.ctx.loop = loop
        link = RoomLink(r.ctx)
        for k, v in link.provides().items():
            setattr(r.ctx, k, v)
        r.ctx.on_room_event.append(r.d.provides()["on_room_event"])    # as brain.add does
        link.configure({"room": {"url": room.url}})
        link.start()

        async def until(pred, limit: int = 300) -> None:
            for _ in range(limit):
                if pred():
                    return
                r.t = round(r.t + 0.1, 6)
                r.wall += 0.1
                r.d.tick(r.t)
                await asyncio.sleep(0.01)
            raise AssertionError("never happened")

        try:
            await until(lambda: link.view()["reachable"] and link.view()["screens"])
            assert r.ctx.room_boot() == room.started_at
            r.d.set_target("alpha")
            await until(lambda: len(room.patches) == 2 and r.d._look_source == "yes")
            assert [b.get("source") for _, b in room.patches] == [contract.SHOW_SOURCE] * 2
            await until(lambda: r.d.snapshot()["screens_palette"]["own_echoes"] == 2)
            assert r.d.screen_pick is None
            await room.pick("wall-left", "ember")                      # a person, on the screen's card
            await until(lambda: r.d.screen_pick is not None)
            assert pick_chip(r)["label"] == "Colours: Ember (picked on a screen)"
            r.d.set_palette("rainbow")
            for _ in range(20):                                        # 2 s of ticks
                r.t = round(r.t + 0.1, 6)
                r.d.tick(r.t)
                await asyncio.sleep(0.005)
            assert len(room.patches) == 2                              # held
            assert r.d.clear(contract.SCREEN_PICK) == [contract.SCREEN_PICK]
            await until(lambda: len(room.patches) == 4)
            assert [(s, b["palette"]) for s, b in room.patches[2:]] == [("wall-left", "rainbow"),
                                                                       ("wall-right", "rainbow")]
            assert link.snapshot()["stats"]["room_events"].get(contract.ROOM_SCREEN_LOOK, 0) >= 3
            # room_service restarts with the old code: a new boot, the probe again, refused, sent without
            room.new = False
            room.started_at = "2026-10-16T10:00:00+00:00"
            await room.send({"type": "status", "data": room.status()})
            await until(lambda: r.d.snapshot()["screens_palette"]["boot"] == room.started_at)
            r.d.set_palette("gold")
            await until(lambda: len(room.patches) == 7)
            assert [("source" in b, b["palette"]) for _, b in room.patches[4:]] == [
                (True, "gold"), (False, "gold"), (False, "gold")]
            await until(lambda: r.d.snapshot()["screens_palette"]["own_echoes"] >= 6)
            assert r.d._look_source == "no" and r.d.screen_pick is None   # our echoes without source: not picks
            await room.pick("wall-right", "ice")                       # the old room's event: no source
            await until(lambda: r.d.screen_pick is not None)
            assert r.d.screen_pick["palette"] == "ice" and r.d.screen_pick["source"] is None
        finally:
            link.stop()
            r.d.stop()
            for ws in list(room.clients):
                await ws.close()
            await room.runner.cleanup()
            await asyncio.sleep(0.05)

    asyncio.run(main())


# -- Stage 5: the lamps from the fixture registry, look_fn (README S5.8) ---------------------------------------------------
def registry_view(fixtures: dict | None = None) -> dict:
    """A contract.RigView with this file's made-up names: two screens, lamps by placement, a lamp with its own role,
    an intensity-only fixture (not one of the director's lamps)."""
    types = {"tvt": {"id": "tvt", "kind": "screen", "attrs": ["colour"]},
             "lampt": {"id": "lampt", "kind": "lamp", "attrs": ["intensity", "colour"]},
             "dimt": {"id": "dimt", "kind": "lamp", "attrs": ["intensity"]}}
    fx = fixtures if fixtures is not None else {
        "tv.left": {"id": "tv.left", "type": "tvt", "screen": "wall-left", "label": "L"},
        "tv.right": {"id": "tv.right", "type": "tvt", "screen": "wall-right", "label": "R"},
        "lamp.one": {"id": "lamp.one", "type": "lampt", "screen": "wall-left", "placement": "over"},
        "lamp.two": {"id": "lamp.two", "type": "lampt", "screen": "wall-left", "placement": "facing"},
        "par.new": {"id": "par.new", "type": "lampt", "screen": "wall-right", "placement": "over", "role": "keylight"},
        "dim.x": {"id": "dim.x", "type": "dimt"}}
    return {"revision": "test", "path": "test", "error": None, "problems": [], "outputs": {}, "nodes": {},
            "types": types, "fixtures": fx, "groups": {}}


def test_the_lamps_and_screens_come_from_the_registry(tmp_path):
    r = Rig(tmp_path, clock=True)
    view = registry_view()
    r.ctx.rig = lambda: view
    r.d.set_target("alpha")
    r.run(3.0)
    p = r.d.snapshot()["patch"]
    assert p["from"] == "fixtures.yaml" and p["screens"] == ["tv.left", "tv.right"]
    assert p["lamps"] == {"lamp.one": "wash", "lamp.two": "keylight", "par.new": "keylight"}   # its own role wins
    cols = r.colours()
    assert {"lamp.one", "lamp.two", "par.new", "tv.left", "tv.right"} <= set(cols) and "dim.x" not in cols
    assert "lamp.three" not in cols                                  # patch.yaml's layout isn't used
    look = r.d.look_fn()
    assert look is r.d.look and look["lamps"]["par.new"]["role"] == "keylight"
    assert set(look["screens"]) == {"tv.left", "tv.right"}
    assert max(cols["tv.left"]) == pytest.approx(1.0, abs=1e-6)          # screens are never scaled
    # an error with no fixtures, or no rig at all: patch.yaml's layout, as before Stage 5
    view = dict(registry_view({}), error="bad yaml")
    r.ctx.rig = lambda: view
    r.run(0.3)
    p = r.d.snapshot()["patch"]
    assert p["from"] == "patch.yaml" and set(p["lamps"]) == {"lamp.one", "lamp.two", "lamp.three", "lamp.four"}
    r.ctx.rig = None
    r.run(0.3)
    assert r.d.snapshot()["patch"]["from"] == "patch.yaml"


def test_look_fn_is_none_until_the_first_compose(tmp_path):
    ctx = contract.BrainContext(publish=lambda m: None)
    (tmp_path / "vocab.yaml").write_text(VOCAB)
    (tmp_path / "vibes.yaml").write_text(VIBES)
    d = dmod.Director(ctx, vibes=Vibes(tmp_path / "vibes.yaml", vocab_path=tmp_path / "vocab.yaml"),
                      catalog=pal.Catalog(), patch_path=tmp_path / "missing.yaml", log_path=None)
    assert d.look_fn() is None
    assert dmod.layout_from_rig(None) is None and dmod.layout_from_rig({"fixtures": {}}) is None
    lamps, screens = dmod.layout_from_rig(registry_view())
    assert set(lamps) == {"lamp.one", "lamp.two", "par.new"} and set(screens) == {"tv.left", "tv.right"}
    assert lamps["par.new"] == {"kind": "lamp", "screen": "wall-right", "placement": "over", "label": "par.new",
                                "role": "keylight", "house": None}


# -- README S5.14: the review fixes ------------------------------------------------------------------------------------
def test_an_unforeseen_drop_is_lookdatas_and_swells_on_the_virtual_maestros_lamps(tmp_path):
    """An unarmed drop's hit: LookData.hit with predicted false; palette_fn never carries it (each rig output takes it
    by its caps); maestro_palette_fn (the virtual Maestro's lamps are Hue's, P0-P4) swells: >= 300 ms up."""
    r = stage4_rig(tmp_path)
    r.event("build", r.t - 8.0, section_id=0, phase="build", build_progress=0.5)
    r.run(0.2)
    r.sec["phase"] = "drop"
    r.event("drop", r.t - 0.1, section_id=1, strength=0.8)
    r.run(0.1)
    h = r.d._hit
    assert h is not None and h[3] is False
    lh = r.d.look["hit"]
    assert lh["predicted"] is False and lh["swell_ms"] == 300.0 and set(lh["lamps"]) == set(r.d.look["lamps"])
    assert set(lh["lamps"]).isdisjoint({"tv.left", "tv.right"})          # never a screen
    t0, t1 = h[0], h[1]
    base = r.d.palette_fn(t0 + 0.01)
    assert all(base[k] != pytest.approx(h[2][k]) for k in h[2] if max(h[2][k]) - max(base[k]) > 0.05)
    lamp = max(sorted(h[2]), key=lambda k: max(h[2][k]) - max(base[k]))   # the lamp the hit lifts most
    lift = max(h[2][lamp]) - max(base[lamp])
    assert lift > 0.02
    levels = [(t0 + i * 0.01, max(r.d.maestro_palette_fn(t0 + i * 0.01)[lamp])) for i in range(0, 60)]
    full = next(t for t, v in levels if v >= max(h[2][lamp]) - 1e-6)
    assert full - t0 >= 0.3 - 1e-9                                    # a swell, not a reactive hit (§5.4)
    assert max(b[1] - a[1] for a, b in zip(levels, levels[1:])) < 0.1 * lift + 1e-9
    for k in ("tv.left", "tv.right"):                                 # screens: never
        assert r.d.maestro_palette_fn(t0 + 0.3)[k] == r.d.palette_fn(t0 + 0.3)[k]
    assert r.d.maestro_palette_fn(t1 + 0.31)[lamp] == pytest.approx(r.d.palette_fn(t1 + 0.31)[lamp])
    r.run_to(t1 + 0.15)
    assert r.t < t1 + 0.3 and r.d._hit is not None                    # kept while its swell falls back
    r.run_to(t1 + 0.45)
    assert r.d._hit is None and r.d.look["hit"] is None


def _cct(rgb) -> float:
    """McCamy's CCT from sRGB (fine near the Planckian locus, which is where a key light is)."""
    lin = [pal._lin(c) for c in rgb]
    X = 0.4124564 * lin[0] + 0.3575761 * lin[1] + 0.1804375 * lin[2]
    Y = 0.2126729 * lin[0] + 0.7151522 * lin[1] + 0.0721750 * lin[2]
    Z = 0.0193339 * lin[0] + 0.1191920 * lin[1] + 0.9503041 * lin[2]
    x, y = X / (X + Y + Z), Y / (X + Y + Z)
    n = (x - 0.3320) / (0.1858 - y)
    return 449 * n ** 3 + 3525 * n ** 2 + 6823.3 * n + 5520.33


def test_the_key_light_stays_at_key_cct_in_the_warm_white_feels(tmp_path):
    """README S5.14 (§8.8: the key is warm white, 2400-3000 K, in the quiet feels). The real vibes.yaml and show.yaml:
    every feel whose lamp_chroma_max is a faint tint, every role with a white. A neutral palette leaves the key at
    its key_cct; a coloured one tints it at most lamp_chroma_max (OKLab) from that white, never into the avoided
    hues. It used to be cut to an absolute chroma, which dragged a 2700 K white to ~5000 K, lavender or lime."""
    show = yaml.safe_load(contract.SHOW_FILE.read_text())
    r = Rig(tmp_path, settings={"lamps": show["lamps"], "director": show["director"]})
    real = Vibes(contract.VIBES_FILE, palette_names=r.d.palettes.names)
    real.poll(force=True)
    roles = {n: rc for n, rc in r.d.lamps_cfg["roles"].items() if rc.get("white_k")}
    assert roles
    quiet = [n for n in real.names() if (real.get(n)["lamp_chroma_max"] or 1.0) <= 0.06]
    assert quiet
    greys = ("#ffffff", "#808080", "#f0f0f0")
    coloured = ("#2050ff", "#00c8b0", "#ff0000", "#00ff00", "#a000ff", "#ffe000", "#ff8080")
    checked = 0
    for vibe in quiet:
        vs = real.get(vibe)
        wk, cm = float(vs["key_cct"]), float(vs["lamp_chroma_max"])
        w_lab = pal.to_oklab(pal.white(wk))
        for rn, rc in roles.items():
            ah = rc.get("avoid_hues") or []
            for sat in (float(vs["saturation"][0]), float(vs["saturation"][1]), 1.0):
                for hx in greys + coloured:
                    look_pal = {"roles": {k: {"hex": hx} for k in contract.PALETTE_ROLES}}
                    c = r.d._lamp_colour({"placement": None, "screen": None}, rc, look_pal, {}, vs, sat, 0.0, 0.0,
                                         0, r.t)
                    lab = pal.to_oklab(c)
                    L, C, h = pal.to_oklch(c)
                    assert not any(lo <= h <= hi for lo, hi in ah) or C < 1e-3, (vibe, hx, h)
                    if hx in greys:
                        assert abs(_cct(c) - wk) <= 200.0, (vibe, rn, hx, _cct(c))
                    else:                                             # a faint tint of the white, at most cm away
                        tint = math.hypot(lab[1] * w_lab[0] / lab[0] - w_lab[1], lab[2] * w_lab[0] / lab[0] - w_lab[2])
                        assert tint <= cm + 0.02, (vibe, hx, tint)
                        assert abs(_cct(c) - wk) <= 20000.0 * cm, (vibe, rn, hx, _cct(c))
                        assert _cct(c) < 4000.0                       # never the old ~5000 K, lavender or lime
                    checked += 1
    assert checked >= 40
    # the old failure cases, one by one (vibes with key_cct 2700 and lamp_chroma_max 0.04)
    for hx in ("#2050ff", "#00c8b0"):
        c = dmod.white_tint(pal.white(2700), pal.scale_saturation(pal.full(pal.rgb_of(hx)), 0.6), 0.6, 0.04)
        assert _cct(pal.full(c)) < 3600 and not 140 <= pal.to_oklch(c)[2] <= 230
    assert dmod.white_tint(pal.white(2700), (1.0, 1.0, 1.0), 0.6, 0.04) == pytest.approx(pal.white(2700), abs=2e-3)
    blue = pal.full(pal.rgb_of("#2050ff"))                            # no cap: the old mix (full colour at 1)
    assert dmod.white_tint(pal.white(3000), blue, 1.0, None) == pytest.approx(blue, abs=2e-3)


def test_house_lights_use_the_fixtures_own_house_when_room_has_none(tmp_path):
    """README S5.14: a fixture room knows nothing about (every DMX fixture) takes its fixtures.yaml ``house`` under
    House lights and as the start of an entry fade, before show.yaml's director.house (§8.4: 80 % warm white)."""
    r = Rig(tmp_path, clock=True)
    fx = {"tv.left": {"id": "tv.left", "type": "tvt", "screen": "wall-left"},
          "lamp.one": {"id": "lamp.one", "type": "lampt", "screen": "wall-left", "placement": "over"},
          "spot.dark": {"id": "spot.dark", "type": "lampt", "placement": "over",
                        "house": {"intensity": 0, "hex": "#ffffff"}},
          "spot.red": {"id": "spot.red", "type": "lampt", "placement": "over",
                       "house": {"intensity": 0.5, "hex": "#ff0000"}},
          "spot.bad": {"id": "spot.bad", "type": "lampt", "placement": "over", "house": {"hex": "nope"}}}
    view = registry_view(fx)
    r.ctx.rig = lambda: view
    r.room = {"current": "x", "screens": {}, "stored_restore": {"lamp.one": {"on": True, "bri": 50, "mirek": 370}}}
    r.run(0.2)
    t_entry = r.t + 0.1
    r.d.set_target("alpha")
    r.run(0.1)
    start = r.d.palette_fn(t_entry)                                   # the entry fade starts from the house state
    assert start["spot.dark"] == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)
    assert start["spot.red"] == pytest.approx((0.5, 0.0, 0.0))
    warm = pal.scale_level(pal.white(2700), 0.8)
    assert start["spot.bad"] == pytest.approx(warm)                   # doesn't read: show.yaml's house
    assert start["lamp.one"] == pytest.approx(pal.scale_level(pal.white(1e6 / 370), 0.5))   # room's stored state
    r.run(9.0)
    r.d.action("house")
    r.run(1.0)
    c = r.colours()
    assert c["spot.dark"] == (0.0, 0.0, 0.0) and c["spot.red"] == pytest.approx((0.5, 0.0, 0.0))
    assert c["spot.bad"] == pytest.approx(warm)
    assert max(c["tv.left"]) == pytest.approx(1.0)                    # screens: never


# -- composition and balance (README S5.18; compose.py) ------------------------------------------------------------------
from show_service import compose as comp  # noqa: E402

COMPOSE = {"defaults": {"follow": 0.8, "q": {"screens": {"picture": 0.9, "pace": 0.6, "pulse": 0.7, "depth": 0.7,
                                                         "change": 0.6},
                                             "lamps": {"level": 0.6, "pulse": 0.7}}}}


def compose_rig(tmp_path, mode: str = "observe", *, extra: dict | None = None, vibes_text: str = VIBES,
                looks: dict | None = None) -> Rig:
    settings = json.loads(json.dumps(SETTINGS))
    settings["director"]["compose"] = {**json.loads(json.dumps(COMPOSE)), "mode": mode, **(extra or {})}
    settings["lamps"]["roles"]["keylight"]["dynamics"] = 0.6
    if looks is not None:
        settings["looks"] = looks
    r = Rig(tmp_path, clock=True, settings=settings, vibes_text=vibes_text)
    r.room = {"reachable": True, "current": "x", "stored_restore": {},
              "screens": {"wall-left": {"layer": "live", "phase": "live", "cutting": False},
                          "wall-right": {"layer": "art", "phase": "idle", "cutting": False}},
              "members": {"lamp.one": {"believed": "dmx"}, "lamp.two": {"believed": "dmx"},
                          "lamp.three": {"believed": "taken_over"}, "lamp.four": {"believed": "off"}}}
    return r


def song(r: Rig, *, quiet_after: float | None = None) -> None:
    """Features with a song's own range (loud and soft 8 s passages, lufs_s -16 / -32) and, from ``quiet_after`` s
    into the run, a quiet ending (gate 0.1)."""
    t0 = r.t

    def each(rig: Rig) -> None:
        soft = int((rig.t - t0) // 8) % 2 == 1
        quiet = quiet_after is not None and rig.t - t0 >= quiet_after
        g = 0.1 if quiet else (0.5 if soft else 0.95)
        rig.feat = {**feats(rig.t, energy=0.3 if soft or quiet else 0.8, gate=g),
                    "lufs_s": -32.0 if soft or quiet else -16.0}
    r._song = each                                                   # noqa: SLF001 (the test's own hook)


def run_song(r: Rig, seconds: float) -> None:
    r.run(seconds, getattr(r, "_song", None))


def test_observe_changes_nothing_else(tmp_path):
    """observe (the default): the plan goes out on room.look as ``compose`` and NOTHING else changes: the same script
    with the composer cut out gives the same room.direction, room.event, the rest of room.look, palette_fn and every
    set_look_out / GOBO / gate_ref call, frame for frame."""
    rigs = []
    for cut in (False, True):
        d = tmp_path / ("cut" if cut else "on")
        d.mkdir()
        r = compose_rig(d)
        if cut:
            r.d._compose_plan = lambda *a, **k: None                  # noqa: SLF001 (today's director)
        song(r, quiet_after=70.0)
        r.d.set_target("alpha")
        samples: list = []

        def each(rig: Rig, _s=samples) -> None:
            rig._song(rig)                                           # noqa: SLF001
            _s.append(rig.d.palette_fn(rig.t))
            if abs(rig.t - 1040.0) < 0.05:
                rig.d.action("house")
            if abs(rig.t - 1045.0) < 0.05:
                rig.d.action("house")
            if abs(rig.t - 1050.0) < 0.05:
                rig.event("track_end_armed", rig.t + 15.0, eta_s=15.0, track_id=1)
        r.run(90.0, each)
        rigs.append((r, samples))
    (a, sa), (b, sb) = rigs
    assert a.of(contract.ROOM_DIRECTION) == b.of(contract.ROOM_DIRECTION)
    assert a.of(contract.ROOM_EVENT) == b.of(contract.ROOM_EVENT)
    la = [{k: v for k, v in x.items() if k != "compose"} for x in a.of(contract.ROOM_LOOK)]
    lb = [{k: v for k, v in x.items() if k != "compose"} for x in b.of(contract.ROOM_LOOK)]
    assert la == lb and len(la) > 10
    assert sa == sb and a.calls == b.calls
    assert not [c for c in a.calls if c[0] == "look" and "plan" in c[1]]
    d = a.of(contract.ROOM_DIRECTION)[-1]
    assert d["intensity"] == 1.0 and not set(contract.DIRECTION_DRIVE_KEYS) & set(d)
    blk = a.d.look["compose"]                                        # ...while the plan itself moved with the song
    assert blk["mode"] == "observe" and blk["appliances"]["wall-left"][comp.PICTURE] < 1.0


def test_the_compose_block_on_room_look(tmp_path):
    r = compose_rig(tmp_path)
    song(r)
    r.d.set_target("alpha")
    run_song(r, 40.0)
    blk = r.of(contract.ROOM_LOOK)[-1]["compose"]
    assert set(blk) == set(contract.ComposeView.__annotations__)
    assert blk["enabled"] == ["wall-left", "lamp.one", "lamp.two"]   # live screens; members exactly dmx
    assert set(blk["appliances"]) == set(blk["enabled"]) and blk["limits"] == "defaults"
    for vals in list(blk["appliances"].values()) + list(blk["nominal"].values()):
        assert set(vals) == set(comp.DIMS) and all(0.0 <= x <= 1.0 for x in vals.values())
    b = blk["balance"]
    assert set(b) >= {"loud", "range", "lift", "s", "quiet", "h", "liveness"}
    assert b["range"] > 0.9 and b["liveness"] == 1.0 and b["h"][comp.PICTURE] == 0.5 and b["h"][comp.PULSE] == 1.0
    lamps = blk["appliances"]
    assert lamps["lamp.two"][comp.LEVEL] >= lamps["lamp.one"][comp.LEVEL]   # the key role (W 0.6) moves less
    json.dumps(r.d.snapshot())
    sc = r.d.snapshot()["compose"]
    assert sc["mode"] == "observe" and sc["problems"] == [] and sc["fold"] == [70.0, 180.0]
    r.d.action("house")                                              # House lights: the lamps are held at base
    run_song(r, 1.0)
    assert r.d.look["compose"]["appliances"]["lamp.one"] == {d: 1.0 for d in comp.DIMS}


def test_a_feels_compose_block_sets_the_limits(tmp_path):
    text = VIBES.replace("    moods: [bright]\n", "    moods: [bright]\n    compose:\n      follow: 1.0\n"
                         "      q: {screens: {depth: 0.2}}\n")
    r = compose_rig(tmp_path, vibes_text=text)
    song(r, quiet_after=40.0)
    r.d.set_target("alpha")
    run_song(r, 60.0)
    blk = r.d.look["compose"]
    assert blk["limits"] == "feel"
    assert blk["appliances"]["wall-left"][comp.DEPTH] < 0.5          # the feel's own quiet end, nearly reached
    assert blk["appliances"]["wall-left"][comp.PICTURE] == 1.0       # a dimension the block leaves out never moves
    r.d.set_target("beta")                                           # a feel without a block: the defaults
    run_song(r, 0.2)
    assert r.d.look["compose"]["limits"] == "defaults"


def test_drive_puts_the_plan_on_room_direction(tmp_path):
    r = compose_rig(tmp_path, "drive")
    song(r, quiet_after=40.0)
    r.d.set_target("alpha")
    run_song(r, 30.0)
    loud = r.of(contract.ROOM_DIRECTION)[-1]
    run_song(r, 30.0)                                                # the quiet ending
    quiet = r.of(contract.ROOM_DIRECTION)[-1]
    nom = r.d.look["compose"]["nominal"][comp.SCREENS]
    assert quiet["intensity"] == pytest.approx(nom[comp.DEPTH], abs=0.021) and quiet["intensity"] < 0.85
    assert quiet["pulse_depth"] == pytest.approx(nom[comp.PULSE], abs=0.021) and quiet["pulse_depth"] < 0.85
    assert loud["intensity"] > quiet["intensity"] and loud["pulse_depth"] > quiet["pulse_depth"]
    assert quiet["peak"] == {"energy": 0.9, "moods": ["bright"], "transition_style": "wild"}   # alpha's top, its
    #                                                                 moods; no style of its own: the top band's
    snap = r.d.snapshot()
    assert set(snap["direction"]) == set(contract.DirectionData.__annotations__)
    r.d.configure({**json.loads(json.dumps(SETTINGS)), "director": {**SETTINGS["director"], "compose": {
        **COMPOSE, "mode": "drive", "defaults": {"follow": 0.0}}}})  # hot-reloaded: nothing answers now
    n = len(r.of(contract.ROOM_DIRECTION))
    r.run(0.1, r._song)                                              # noqa: SLF001
    assert len(r.of(contract.ROOM_DIRECTION)) == n + 1               # intensity moved: published at once
    assert r.of(contract.ROOM_DIRECTION)[-1]["intensity"] == 1.0 and r.of(contract.ROOM_DIRECTION)[-1][
        "pulse_depth"] == 1.0
    r.d.configure({**json.loads(json.dumps(SETTINGS)), "director": {**SETTINGS["director"], "compose": {
        **COMPOSE, "mode": "observe"}}})
    r.run(0.1, r._song)                                              # noqa: SLF001
    d = r.of(contract.ROOM_DIRECTION)[-1]
    assert not set(contract.DIRECTION_DRIVE_KEYS) & set(d) and d["intensity"] == 1.0


def test_drive_folds_a_half_time_tempo_and_observe_does_not(tmp_path):
    paces = {}
    for mode in ("observe", "drive"):
        d = tmp_path / mode
        d.mkdir()
        r = compose_rig(d, mode)
        r.ctx.clock = lambda r=r: {**clock_body(r.t), "bpm": 60.0}  # a half-time lock of a 120 bpm song
        r.d.set_target("alpha")
        r.run(3.0)
        paces[mode] = r.d.direction["pace"]
        e = r.d.music["energy"]
    assert paces["observe"] == pytest.approx(0.1 + 0.8 * (0.7 * e + 0.3 * 0.0), abs=1e-3)        # today: tempo 0
    assert paces["drive"] == pytest.approx(0.1 + 0.8 * (0.7 * e + 0.3 * (60.0 / 90.0)), abs=1e-3)   # 120 bpm


def test_drive_sends_the_lamps_plan_and_observe_clears_it(tmp_path):
    r = compose_rig(tmp_path, "drive", looks={"middle_depth": 0.4})
    song(r, quiet_after=40.0)
    r.d.set_target("alpha")
    run_song(r, 60.0)
    plans = [c[1]["plan"] for c in r.calls if c[0] == "look" and "plan" in c[1]]
    assert plans and all(set(c[1]) == {"plan"} for c in r.calls if c[0] == "look" and "plan" in c[1])
    p = plans[-1]
    assert p["middle_depth"] == 0.4 and p["depth"] == pytest.approx(0.5)     # alpha's downbeat accent depth
    assert set(p["lamps"]) == {"lamp.one", "lamp.two", "*"}
    lv, pu = p["lamps"]["lamp.one"]
    blk = r.d.look["compose"]["appliances"]["lamp.one"]
    assert lv == pytest.approx(blk[comp.LEVEL], abs=0.021) and pu == pytest.approx(blk[comp.PULSE], abs=0.021)
    assert lv < 0.9                                                  # the quiet ending: the lamps soften
    assert r.d.snapshot()["compose"]["plan_sent"]["middle_depth"] == 0.4
    n = len(plans)
    run_song(r, 5.0)                                                 # steady: nothing new under the deadband
    assert len([c for c in r.calls if c[0] == "look" and "plan" in c[1]]) == n
    r.d.configure({**json.loads(json.dumps(SETTINGS)), "director": {**SETTINGS["director"], "compose": {
        **COMPOSE, "mode": "observe"}}})
    run_song(r, 1.0)
    after = [c[1] for c in r.calls if c[0] == "look" and "plan" in c[1]][n:]
    assert after == [{"plan": {}}]                                   # cleared once: artnet_out as before
    r.d.set_target(None)                                             # no feel, drive: the plan keeps artnet's depth
    r.d.configure({**json.loads(json.dumps(SETTINGS)), "director": {**SETTINGS["director"], "compose": {
        **COMPOSE, "mode": "drive"}}})
    run_song(r, 1.0)
    last = [c[1]["plan"] for c in r.calls if c[0] == "look" and "plan" in c[1]][-1]
    assert last["depth"] is None and "*" in last["lamps"]


def test_the_response_file_gives_the_one_tempo_fold(tmp_path):
    resp = tmp_path / "music_response.json"
    resp.write_text(json.dumps({"tempo": {"fold_min": 80, "fold_max": 160}}))
    r = compose_rig(tmp_path, "drive", extra={"response_file": str(resp)})
    r.run(0.1)
    assert r.d.snapshot()["compose"]["fold"] == [80.0, 160.0] and r.d.snapshot()["compose"]["problems"] == []
    resp.write_text(json.dumps({"tempo": {"fold_min": 0}}))          # hot: a bad edit falls back and is listed
    st = resp.stat()
    os.utime(resp, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    r.run(contract.RELOAD_S + 0.2)
    sc = r.d.snapshot()["compose"]
    assert sc["fold"] == [70.0, 180.0] and any("fold" in p for p in sc["problems"])
    (tmp_path / "other").mkdir()
    r2 = compose_rig(tmp_path / "other", extra={"response_file": str(tmp_path / "missing.json")})
    r2.run(0.1)
    sc2 = r2.d.snapshot()["compose"]
    assert sc2["fold"] == [70.0, 180.0] and any("missing.json" in p for p in sc2["problems"])
    assert r.d.cfg["compose"]["response_file"] == str(resp) and r2.d.settings_problems == []


def test_an_armed_song_end_cues_the_fade_and_a_call_off_ends_it(tmp_path):
    r = compose_rig(tmp_path)
    song(r)
    r.d.set_target("alpha")
    run_song(r, 2.0)
    assert r.d.look["compose"]["balance"]["fade"] is False
    r.event("track_end_armed", r.t + 15.0, eta_s=15.0, track_id=1)
    run_song(r, 0.2)
    assert r.d.look["compose"]["balance"]["fade"] is True
    r.event("track_end_armed", r.t + 15.0, eta_s=None, track_id=1, detail="disarmed")
    run_song(r, 0.2)
    assert r.d.look["compose"]["balance"]["fade"] is False
    r.event("track_end_armed", r.t + 1.0, eta_s=1.0, track_id=1)
    run_song(r, 1.0 + dmod.END_FADE_S + 0.5)                        # never longer than END_FADE_S past the end
    assert r.d.look["compose"]["balance"]["fade"] is False


def test_compose_settings_and_bad_values(tmp_path):
    r = compose_rig(tmp_path, extra={"mode": "loud", "loud": {"rise_s": "fast"}})
    probs = "\n".join(r.d.settings_problems)
    assert "director.compose.mode" in probs and "director.compose.loud.rise_s" in probs
    assert r.d.cfg["compose"]["mode"] == "observe"
    bad = json.loads(json.dumps(SETTINGS))
    bad["lamps"]["roles"]["wash"]["dynamics"] = "lots"
    r.d.configure(bad)
    assert any("lamps.roles.wash.dynamics" in p for p in r.d.settings_problems)
    sy = yaml.safe_load(contract.SHOW_FILE.read_text())               # the real show.yaml's compose: clean, observe
    r.d.configure(sy)
    assert not [p for p in r.d.settings_problems if "compose" in p or "dynamics" in p]
    assert r.d.cfg["compose"]["mode"] == "observe"


def test_compose_tick_cost_with_every_appliance_enabled(tmp_path):
    r = compose_rig(tmp_path, "drive")
    r.room["screens"]["wall-right"]["layer"] = "live"
    r.room["members"] = {f"lamp.{n}": {"believed": "dmx"} for n in ("one", "two", "three", "four")}
    song(r)
    r.d.set_target("alpha")
    run_song(r, 15.0)
    samples = []
    for _ in range(300):
        r.t = round(r.t + 0.1, 6)
        r._song(r)                                                   # noqa: SLF001
        t0 = time.perf_counter()
        r.d._compose_plan(r.t, 0.1, r.room)                          # noqa: SLF001
        samples.append((time.perf_counter() - t0) * 1000.0)
    p95 = sorted(samples)[int(0.95 * len(samples))]
    print(f"\ncompose plan, 6 appliances: p50 {statistics.median(samples):.3f} ms, p95 {p95:.3f} ms")
    assert p95 < 2.0                                                 # README S5.18: ~0.1 ms unloaded


def test_band_words_land_after_the_dwell(rig):
    """director.bands.dwell_s (2b's audit, 17 Oct: band words flapped up to 4 times a minute): a new row's words
    land only once it has held the dwell; a dip back before then cancels it."""
    s = json.loads(json.dumps(SETTINGS))
    s["director"]["bands"]["dwell_s"] = 20
    d = rig.d
    d.configure(s)
    d.lv = 1.0
    d._now_s = 0.0
    assert d._band_words(0.1)["name"] == "low"
    d._now_s = 5.0
    assert d._band_words(0.9)["name"] == "low"                       # waiting
    d._now_s = 10.0
    assert d._band_words(0.1)["name"] == "low"                       # back: the wait is off
    d._now_s = 12.0
    assert d._band_words(0.9)["name"] == "low"
    d._now_s = 31.0
    assert d._band_words(0.9)["name"] == "low"
    d._now_s = 32.5
    assert d._band_words(0.9)["name"] == "top"


def test_band_energy_has_an_absolute_base_and_swings_round_it(rig):
    """director.bands.base (2b's replay, 17 Oct): the genre words' energy, the onset rate and the pulse clarity set
    the level; the heard energy only swings it round its own running median."""
    s = json.loads(json.dumps(SETTINGS))
    s["director"]["bands"]["base"] = {"genre_energy": {"ambient": 0.15, "techno": 0.7}, "rate_full": 12,
                                      "weights": {"genre": 0.5, "rate": 0.3, "clarity": 0.2}, "swing": 0.5,
                                      "swing_window_s": 60}
    d = rig.d
    d.configure(s)
    assert d.cfg["bands"]["base"]["rate_full"] == 12
    d._mc = {"genre": {"words": {"techno": 1.0}}}
    rig.rhythm = {"t_ms": 100_000.0, "onset_rate": 12.0, "pulse_clarity": 0.8}
    for k in range(30):                                             # steady: the median is the music itself
        rig.rhythm["t_ms"] = (100.0 + k) * 1000.0
        e = d._band_heard(0.9, 1.0, 100.0 + k)
    assert e == pytest.approx(0.5 * 0.7 + 0.3 * 1.0 + 0.2 * 0.8)   # mastered loudness 0.9 doesn't count
    rig.rhythm["t_ms"] = 130_000.0
    lift = d._band_heard(1.0, 1.0, 130.0)                           # a drop: above the median
    assert lift > e
    d._mc = {"genre": {"words": {"ambient": 1.0}}}
    rig.rhythm = {"t_ms": 131_000.0, "onset_rate": 1.5, "pulse_clarity": 0.2}
    assert d._band_heard(0.9, 1.0, 131.0) < 0.3                    # an ambient pad near its own p95: still
    rig.rhythm = None
    d._mc = {}
    assert d._band_heard(0.9, 1.0, 132.0) == pytest.approx(0.9)    # no terms: the heard energy as before


def test_without_a_feel_the_direction_reads_the_music_and_the_band_rows_change_rate(rig):
    """2b's audit (17 Oct): with no feel, room.direction's energy is the band energy (not squeezed by the neutral
    follow) and its change_rate the band row's."""
    s = json.loads(json.dumps(SETTINGS))
    for row, cr in zip(("low", "mid_a", "mid_b", "high", "top"), (0.15, 0.3, 0.45, 0.6, 0.75)):
        s["director"]["bands"]["words"][row]["change_rate"] = cr
    rig.d.configure(s)
    rig.energy = 1.0
    rig.run(20.0)
    d = rig.d.direction
    assert rig.d.snapshot()["neutral"] is True
    assert d["energy"] == pytest.approx(rig.d.music["band_energy"], abs=1e-3) and d["energy"] > 0.8
    assert d["change_rate"] == 0.75 and d["moods"] == ["euphoric"]


# -- README S5.22: the listener guides the art director ------------------------------------------------------------------
def guide_beliefs(word="jazz", gconf=0.9, intensity=None, change=None, trans=None, conf=0.9, state="fresh"):
    """listener.beliefs() with a genre and an approach (contract.Beliefs)."""
    ap = {"intensity": intensity, "intensity_conf": conf if intensity is not None else 0.0,
          "change": change, "change_conf": conf if change else 0.0,
          "transitions": trans, "transitions_conf": conf if trans else 0.0}
    ap["conf"] = min(ap["intensity_conf"], ap["change_conf"], ap["transitions_conf"])
    return {"state": state, "age_s": 5.0, "genre": {"word": word, "conf": gconf} if word else None, "moods": [],
            "feel": [], "trajectory": None, "content": "music", "character": "soft late-night jazz trio",
            "approach": ap if (intensity is not None or change or trans) else None, "ending": None, "coming": []}


def test_a_confident_listener_genre_beats_the_local_mls(tmp_path):
    """Owner, 18 Oct: the AI "can characterise 'this is soft jazz', which otherwise the system generally won't know":
    its confident genre (fresh or holding) leads room.direction's genre and genres; the local ML is the fallback."""
    r = stage4_rig(tmp_path)
    r.sec["genre"] = {"word": "soul", "conf": 0.8, "parent": "x", "parent_p": 0.8, "via": "ml",
                      "words": {"soul": 0.7, "house": 0.3}}
    b = guide_beliefs("jazz", 0.9)
    r.ctx.beliefs = lambda: b
    r.run(1.0)
    assert r.d.direction["genre"] == "jazz" and r.d.direction["genres"] == {"jazz": 1.0}
    assert r.d.snapshot()["words_from"]["genre"] == "listener" and r.d.snapshot()["guide"]["genre"] == "jazz"
    b["genre"]["conf"] = 0.5                                          # not sure: the ML's word and shares
    r.run(1.0)
    assert r.d.direction["genre"] == "soul" and r.d.direction["genres"] == {"soul": 0.7, "house": 0.3}
    b["genre"]["conf"], b["state"] = 0.9, "fading"                    # fading beliefs don't lead
    r.run(1.0)
    assert r.d.snapshot()["words_from"]["genre"] == "ml"


def test_the_listeners_intensity_is_the_band_bases_genre_term_slewed(rig):
    s = json.loads(json.dumps(SETTINGS))
    s["director"]["bands"]["base"] = {"genre_energy": {"jazz": 0.3, "house": 0.8}, "rate_full": 12,
                                      "weights": {"genre": 1.0}, "swing": 0.0, "swing_window_s": 60}
    d = rig.d
    d.configure(s)
    d._mc = {"genre": {"words": {"house": 1.0}}}
    none = {"genre": None, "intensity": None, "change": None, "transitions": None}
    d._guide = dict(none)
    assert d._band_heard(0.5, 1.0, 100.0) == pytest.approx(0.8) and d._gterm_from == "ml"   # the ML's house
    d._guide = dict(none, genre="jazz")                               # the listener's confident jazz: 0.3
    got = [d._band_heard(0.5, 1.0, 100.0 + 0.1 * k) for k in range(1, 61)]
    assert got[0] == pytest.approx(0.79) and got[-1] == pytest.approx(0.3)   # slewed 0.1 a second
    assert all(a >= b for a, b in zip(got, got[1:])) and d._gterm_from == "listener genre"
    d._guide = dict(none, genre="jazz", intensity=0.9)                # its approach.intensity wins over the genre's
    got = [d._band_heard(0.5, 1.0, 106.0 + 0.1 * k) for k in range(1, 81)]
    assert got[-1] == pytest.approx(0.9) and d._gterm_from == "listener approach"
    d._guide = dict(none)                                             # the listener lapses: the ML's again
    assert d._band_heard(0.5, 1.0, 115.0) == pytest.approx(0.8) and d._gterm_from == "ml"
    assert d._llm_guide(guide_beliefs("jazz", gconf=0.5, intensity=0.2, conf=0.5)) == dict(none)   # unsure
    assert d._llm_guide(guide_beliefs("jazz", intensity=0.2, change="slow", trans="gentle")) == \
        {"genre": "jazz", "intensity": 0.2, "change": "slow", "transitions": "gentle"}
    assert d._llm_guide(guide_beliefs("jazz", state="fading")) == dict(none)


def test_the_listeners_transitions_and_change_apply_under_a_persons_or_a_feels_own(rig):
    """human > feel > listener > bands: approach.transitions -> transition_style and approach.change -> change_rate
    while no person or feel sets them; the band row's otherwise."""
    s = json.loads(json.dumps(SETTINGS))
    for row, cr in zip(("low", "mid_a", "mid_b", "high", "top"), (0.15, 0.3, 0.45, 0.6, 0.75)):
        s["director"]["bands"]["words"][row]["change_rate"] = cr
    rig.d.configure(s)
    b = guide_beliefs(None, change="slow", trans="gentle")
    rig.ctx.beliefs = lambda: b
    rig.energy = 1.0
    rig.run(20.0)                                                     # no feel; the top band says wild, 0.75
    d = rig.d.direction
    assert d["transition_style"] == "gentle" and d["change_rate"] == 0.15
    wf = rig.d.snapshot()["words_from"]
    assert wf["transition_style"] == "listener" and wf["change_rate"] == "listener"
    b["approach"].update(change_conf=0.3, transitions_conf=0.3)       # unsure: the band row's
    rig.run(1.0)
    assert rig.d.direction["transition_style"] == "wild" and rig.d.direction["change_rate"] == 0.75
    b["approach"].update(change_conf=0.9, transitions_conf=0.9)
    rig.d.set_target("alpha")                                         # a feel: its own change_rate (0.5) wins;
    rig.run(12.0)                                                     # alpha leaves transitions to the music
    assert rig.d.direction["change_rate"] == 0.5 and rig.d.direction["transition_style"] == "gentle"
    rig.d.set_target({"vibe": "alpha", "listening": True, "overrides": {"transition_style": "lively"}})
    rig.run(1.0)                                                      # a person's own word wins
    assert rig.d.direction["transition_style"] == "lively"
    assert rig.d.snapshot()["words_from"]["transition_style"] == "feel"
    assert rig.d.provides()["held"]() is False


# -- the ensemble (docs/ENSEMBLE.md §11.1; the show builder's, 22 Oct) ---------------------------------------------------
from show_service import ensemble as ensmod  # noqa: E402

ENS_ZONES = {"port": "left", "starboard": "right"}                   # made-up zones: the code knows no room
ROUND = """  a-round:
    kind: canon
    line: "a round, {second} follows {first} by {beats}"
    params: {team_by: side, order: room, offset_beats: 2, offset_s: 1.0, every_bars: 1, clock_offset: true}
    routes: [downbeat, phrase, section]
    needs: [sides]
"""
SPOT = """  spotlight:
    kind: solo_and_accompany
    params: {team_by: side, order: room, pass_on: section, pass: next, accompany_beats: 8, lead_kinds: [screen, lights]}
    routes: []
    needs: [sides]
"""


def ensemble_registry() -> dict:
    """registry_view's types, zoned: a screen on each side, two colour lamps hung at each."""
    fx = {"tv.left": {"id": "tv.left", "type": "tvt", "screen": "wall-left", "label": "L", "zone": "port"},
          "tv.right": {"id": "tv.right", "type": "tvt", "screen": "wall-right", "label": "R", "zone": "starboard"}}
    for fid, scr, zone in (("lamp.one", "wall-left", "port"), ("lamp.two", "wall-left", "port"),
                           ("lamp.three", "wall-right", "starboard"), ("lamp.four", "wall-right", "starboard")):
        fx[fid] = {"id": fid, "type": "lampt", "screen": scr, "placement": "over", "zone": zone}
    return registry_view(fx)


def ensemble_rig(tmp_path, forms: str | None = None, *, block: bool = True) -> Rig:
    settings = json.loads(json.dumps(SETTINGS))
    if block:
        settings["ensemble"] = {"players": {"zones": dict(ENS_ZONES)}}
    r = Rig(tmp_path, clock=True, settings=settings)
    view = ensemble_registry()
    r.ctx.rig = lambda: view
    if forms is not None:                                            # a catalogue of the test's own forms
        p = tmp_path / "interplays.yaml"
        p.write_text("version: 1\ninterplays:\n" + forms)
        r.d.ensemble.catalogue = ensmod.Catalogue(p, scorer=r.d.light_scenes)
    r.sec = {"id": 0, "letter": None, "phase": "calm", "track": 0, "started_s": r.t}
    r.d.set_target("alpha")
    r.run(10.0)
    return r


def test_the_ensemble_rides_on_room_direction_and_room_look_but_gestures_stay_off_the_bus(tmp_path):
    r = ensemble_rig(tmp_path, ROUND)
    d, lk = r.of(contract.ROOM_DIRECTION)[-1], r.of(contract.ROOM_LOOK)[-1]
    assert set(d["ensemble"]) == set(contract.EnsembleData.__annotations__)
    assert "ensemble" in lk and "gestures" not in lk
    look = r.d.look_fn()
    assert "gestures" in look and look["ensemble"] is r.d.ensemble.block()
    snap = r.d.snapshot()["ensemble"]
    assert {p["id"]: p["side"] for p in snap["players"]} == {"*:left": "left", "tv.left": "left",
                                                              "*:right": "right", "tv.right": "right"}
    assert d["ensemble"]["screens"] == {"wall-left": "tv.left", "wall-right": "tv.right"}
    json.dumps(r.d.snapshot())


def test_a_round_puts_the_right_side_two_beats_behind_and_each_screen_pops_at_its_cut(tmp_path):
    r = ensemble_rig(tmp_path, ROUND)
    r.d.action("next")                                               # the first look picks the form
    r.run(6.0)
    blk = r.d.ensemble.block()
    assert blk["form"] == "a-round" and r.d.snapshot()["ensemble"]["line"] == \
        "Playing together: a round, right follows left by 2 beats"
    assert blk["players"]["tv.right"]["clock_offset_beats"] == 2.0 and blk["players"]["tv.right"]["stagger_s"] == 1.0
    gs = r.of(contract.ROOM_GESTURE)
    pairs = [{p["player"]: p["at_ms"] for p in g["parts"]} for g in gs if g["kind"] == "downbeat"]
    pairs = [at for at in pairs if "tv.left" in at and "tv.right" in at]
    assert pairs and all(at["tv.right"] - at["tv.left"] == pytest.approx(1000.0) for at in pairs)
    nxt = [g for g in gs if g["id"].startswith("g:look:")][-1]       # the Next look's pop, staggered per screen
    at = {p["player"]: p["at_ms"] for p in nxt["parts"]}
    assert at["tv.right"] - at["tv.left"] == pytest.approx(1000.0) and at["*:left"] == at["tv.left"]
    at_s = r.next_bar(2.5)                                           # a section from structure, on its line
    r.sec.update(id=1, started_s=at_s)
    r.event("section_change", at_s, section_id=1, letter=None, repeat_of=None, predicted=False,
            boundary_ms=at_s * 1000.0)
    r.run_to(at_s + 0.15)
    sec = [g for g in r.of(contract.ROOM_GESTURE) if g["kind"] == "section"][-1]
    at = {p["player"]: p["at_ms"] / 1000.0 for p in sec["parts"]}
    assert at["tv.left"] == pytest.approx(at_s, abs=1e-3) and at["tv.right"] == pytest.approx(at_s + 1.0, abs=1e-3)


def test_accompanying_lamps_sit_lower_and_never_under_the_floor(tmp_path):
    r = ensemble_rig(tmp_path, SPOT)
    r.d.action("next")
    r.run(6.0)
    roles = {pid: p["role"] for pid, p in r.d.ensemble.block()["players"].items()}
    assert roles["*:left"] == "lead" and roles["*:right"] == "accompany"
    lk = r.d.look
    floor, level = lk["floor"], max(lk["floor"], lk["level"])
    want = floor + (level - floor) * 0.7
    assert lk["lamps"]["lamp.one"]["level"] == pytest.approx(level, abs=2e-3)
    assert lk["lamps"]["lamp.three"]["level"] == pytest.approx(want, abs=2e-3)
    cols = r.colours()
    assert max(cols["lamp.three"]) == pytest.approx(want, abs=5e-3) and max(cols["lamp.four"]) < max(cols["lamp.two"])
    assert max(cols["tv.right"]) == pytest.approx(1.0)                # screens: never a gain


def test_the_virtual_maestros_lamps_take_the_gestures_side_by_side(tmp_path):
    r = ensemble_rig(tmp_path, ROUND)
    r.d.action("next")
    r.run(6.0)
    g = [g for g in r.of(contract.ROOM_GESTURE) if g["kind"] == "downbeat"
         and any(p["player"] == "*:left" for p in g["parts"])][-1]
    t_left = next(p["at_ms"] for p in g["parts"] if p["player"] == "*:left") / 1000.0
    base, got = r.d.palette_fn(t_left), r.d.maestro_palette_fn(t_left)
    assert max(got["lamp.one"]) > max(base["lamp.one"]) + 0.01      # the left side's voice peaks here
    assert got["lamp.three"] == pytest.approx(base["lamp.three"], abs=0.01)   # the right's comes 2 beats later
    later = r.d.maestro_palette_fn(t_left + 1.0)
    assert max(later["lamp.three"]) > max(r.d.palette_fn(t_left + 1.0)["lamp.three"]) + 0.01
    assert got["tv.left"] == base["tv.left"]                          # screens never carry a lamp's gesture


def test_with_no_ensemble_block_nothing_new_goes_out_and_the_colours_are_as_before(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a, b = ensemble_rig(tmp_path / "a", block=False), ensemble_rig(tmp_path / "b", block=False)
    b.d.ensemble = None                                              # as if ensemble.py were not there
    for r in (a, b):
        r.d.action("next")
        r.run(8.0)
    assert a.of(contract.ROOM_GESTURE) == []
    assert a.colours() == b.colours()
    assert [x for x in a.calls if x[0] == "look"] == [x for x in b.calls if x[0] == "look"]   # set_look_out
    assert a.d.snapshot()["ensemble"]["line"] is None and a.d.ensemble.block()["form"] == "unison"


# -- the art wish (owner, 28 Oct): room.direction's art beside the genre ------------------------------------------------
ART_YAML = """facets:
  subject: {landscape: x, still-life: x, abstract: x, urban: x, people: x, animals: x, interior: x}
  medium: {painting: x, photograph: x}
  moods: {calm: x, dark: x, elegant: x, warm: x}
music_art:
  jazz: {subjects: [abstract, interior, urban, people], moods: [dark, elegant, warm], media: [painting, photograph],
         avoid: [animals], theme: "late-night city, smoky rooms, painterly abstraction"}
"""
LISTENER_ART = {"subjects": ["landscape", "still-life"], "moods": ["calm"], "media": ["painting"], "avoid": [],
                "theme": "misty lakes and quiet rooms", "via": "listener"}


def art_stage4_rig(tmp_path) -> Rig:
    r = stage4_rig(tmp_path)
    (tmp_path / "art_tags.yaml").write_text(ART_YAML)
    r.d.art_tags = contract.ArtTags(tmp_path / "art_tags.yaml")
    r.d.art_tags.poll(0.0)
    return r


def test_room_direction_art_falls_back_to_music_art_by_genre_group(tmp_path):
    r = art_stage4_rig(tmp_path)
    r.sec["genre"] = {"word": "blues", "conf": 0.8, "parent": "x", "parent_p": 0.8, "via": "ml"}   # blues -> jazz
    r.run(1.0)
    a = r.d.direction["art"]
    assert a["via"] == "genre" and a["subjects"] == ["abstract", "interior", "urban", "people"]
    assert a["theme"] == "late-night city, smoky rooms, painterly abstraction"
    assert r.of(contract.ROOM_DIRECTION)[-1]["art"] == a
    r.sec["genre"] = {"word": "pop", "conf": 0.8, "parent": "x", "parent_p": 0.8, "via": "ml"}     # no pop row
    r.run(1.0)
    assert r.d.direction["genre"] == "pop" and r.d.direction["art"] is None                    # nothing invented


def test_room_direction_art_is_none_with_no_music_known(tmp_path):
    r = art_stage4_rig(tmp_path)
    r.run(1.0)
    assert r.d.direction["genre"] is None and r.d.direction["art"] is None


def test_a_confident_listener_art_wish_wins(tmp_path):
    r = art_stage4_rig(tmp_path)
    r.sec["genre"] = {"word": "jazz", "conf": 0.8, "parent": "x", "parent_p": 0.8, "via": "ml"}
    b = guide_beliefs("jazz", 0.9)
    b["art"] = dict(LISTENER_ART, conf=0.6)
    r.ctx.beliefs = lambda: b
    r.run(1.0)
    assert r.d.direction["art"] == LISTENER_ART                                     # conf >= 0.6: the listener's
    b["art"]["conf"] = 0.55                                                         # not sure: music_art by genre
    r.run(1.0)
    assert r.d.direction["art"]["via"] == "genre" and r.d.direction["art"]["subjects"][0] == "abstract"
    b["art"]["conf"], b["state"] = 0.9, "fading"                                    # fading beliefs don't lead
    r.run(1.0)
    assert r.d.direction["art"]["via"] == "genre"
