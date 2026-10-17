"""compose.py (README S5.18; SHOW_ENGINE §8.10): the composer's pure core against synthetic inputs. No I/O, no clock:
every tick is an Inputs at a made-up time. The design's checks: floors hold; the slope against loud is at most
follow x W x (1 - q); a flat song gives s ~ 0; neutral equals today; a step input never oscillates; a dark scene's
APL is never dimmed further; the enabled reader counts only live screens and members exactly dmx; the validator.
"""

from __future__ import annotations

import random

import pytest
import yaml

from show_service import compose as comp
from show_service import contract

LIMITS = {"follow": 0.8,
          "floor": {"screens": {"picture": 0.7}, "lamps": {"level": 0.3}},
          "q": {"screens": {"picture": 0.9, "pace": 0.6, "pulse": 0.7, "depth": 0.7, "change": 0.6},
                "lamps": {"level": 0.6, "pulse": 0.7}}}


def settings(**over) -> dict:
    probs: list[str] = []
    s = comp.parse_settings(over or None, probs)
    assert probs == []
    return s


def terms(loud=0.5, rng=1.0, live=1.0, lift=None) -> dict:
    return {"loud": loud, "range": rng, "range_lu": 12.0, "lift": lift or {}, "liveness": live, "fade": False}


def drive(state: comp.State, cfg: dict, seconds: float, t0: float = 0.0, **inp) -> tuple[float, dict]:
    """Ticks at 10 Hz: (the time after, the last step's terms). ``inp``: Inputs fields, or callables of t."""
    t, out = t0, {}
    for _ in range(int(round(seconds * 10))):
        t = round(t + 0.1, 6)
        kw = {k: (v(t) if callable(v) else v) for k, v in inp.items()}
        out = comp.step(state, comp.Inputs(now_s=t, dt=0.1, **kw), cfg)
    return t, out


# -- the formula ------------------------------------------------------------------------------------------------------
def test_floors_hold_everywhere():
    cfg = settings()
    tab = comp.table(LIMITS)
    rnd = random.Random(7)
    for _ in range(2000):
        tm = terms(loud=rnd.random(), rng=rnd.random(), live=rnd.random(), lift={"picture": rnd.random() * 0.6})
        for cls, w in ((comp.SCREENS, 1.0), (comp.LAMPS, 0.6), (comp.LAMPS, 1.0)):
            v, s, q, h = comp.plan_one(tm, tab, cfg, cls, weight=w, apl=rnd.choice([None, rnd.random()]))
            for dim in comp.DIMS:
                c = tab[cls][dim]
                hard = comp._cell(cfg["hard_floor"], "floor", cls, dim)
                assert v[dim] >= max(c["floor"], hard) - 1e-12
                assert v[dim] <= c["base"] + 1e-12
                assert 0.0 <= s[dim] <= 1.0 and 0.0 <= q[dim] <= 1.0 and 0.0 <= h[dim] <= 1.0
    # the hard floor holds even when a feel asks for less
    v = comp.plan_one(terms(loud=0.0), comp.table({"follow": 1.0, "q": 0.0}), cfg, comp.SCREENS, weight=1.0,
                      apl=0.9)[0]
    assert v[comp.PICTURE] == pytest.approx(0.6) and v[comp.PULSE] == pytest.approx(0.0)


@pytest.mark.parametrize("cls,w", [(comp.SCREENS, 1.0), (comp.LAMPS, 0.6), (comp.LAMPS, 1.0)])
def test_the_slope_against_loud_is_at_most_follow_x_w_x_1_minus_q(cls, w):
    cfg = settings(headroom={"dims": []})                        # h = 1: the steepest case
    cfg["hard_floor"] = {}
    tab = comp.table(LIMITS)
    for dim in comp.DIMS:
        c = tab[cls][dim]
        bound = c["follow"] * w * (1.0 - c["q"]) * c["base"]
        prev = None
        for i in range(101):
            loud = i / 100.0
            v = comp.plan_one(terms(loud=loud), tab, cfg, cls, weight=w)[0][dim]
            if prev is not None:
                assert abs(v - prev) / 0.01 <= bound + 1e-9, (cls, dim)
            prev = v


def test_a_flat_song_gives_s_about_0_and_a_dynamic_one_spends_the_share():
    cfg = settings()
    tab = comp.table(LIMITS)
    rnd = random.Random(3)
    st = comp.State()
    _t, tm = drive(st, cfg, 70.0, fresh=True, gate=0.8, liveness=1.0, lufs_s=lambda t: -20.0 + rnd.uniform(-0.5, 0.5))
    assert tm["range_lu"] < 1.5 and tm["range"] < 0.01
    v, s, _q, _h = comp.plan_one(tm, tab, cfg, comp.SCREENS, weight=1.0)
    assert max(s.values()) < 0.01 and all(v[d] > 0.99 for d in comp.DIMS)
    st2 = comp.State()                                           # loud and soft passages: the song has range
    _t, tm2 = drive(st2, cfg, 70.0, fresh=True, gate=0.8, liveness=1.0,
                    lufs_s=lambda t: -32.0 if int(t // 8) % 2 else -16.0)
    assert tm2["range_lu"] > 12.0 and tm2["range"] > 0.99
    s2 = comp.plan_one(tm2, tab, cfg, comp.SCREENS, weight=1.0)[1]
    assert s2[comp.PICTURE] == pytest.approx(0.8, abs=0.01)     # follow x W x liveness x range


def test_neutral_equals_today():
    """No limits (the code's neutral), or no audio, or a flat song: every value is its base, 1.0 = today's (the
    picture at the fixture's 255, today's beat accents, intensity 1.0, today's pace and change rate)."""
    cfg = settings()
    for tab, tm in ((comp.table(None), terms(loud=0.0)),                     # nothing configured, a silent song
                    (comp.table(LIMITS), terms(loud=0.0, live=0.0)),         # no audio: liveness 0
                    (comp.table(LIMITS), terms(loud=0.0, rng=0.0))):         # a compressed / AGC'd input
        for cls in comp.CLASSES:
            v = comp.plan_one(tm, tab, cfg, cls, weight=1.0)[0]
            assert v == {d: 1.0 for d in comp.DIMS}, (cls, v)
    st = comp.State()                                            # before any audio at all
    tm = comp.step(st, comp.Inputs(now_s=1.0, dt=0.1), cfg)
    assert tm["loud"] is None and comp.plan_one(tm, comp.table(LIMITS), cfg, comp.SCREENS, weight=1.0)[0] == \
        {d: 1.0 for d in comp.DIMS}


def test_a_step_input_never_oscillates():
    """gate 0.1 -> 0.9 -> 0.1 (a quiet verse, a loud chorus, quiet again): every value moves one way only after each
    step, with no overshoot, so nothing in it oscillates (above 0.5 Hz or at all)."""
    cfg = settings()
    tab = comp.table(LIMITS)
    st = comp.State()
    t, _ = drive(st, cfg, 60.0, fresh=True, gate=0.1, liveness=1.0, lufs_s=lambda t: -32.0 if int(t // 8) % 2
                 else -16.0)                                     # range ~1 in the window first
    series: dict[str, list[float]] = {d: [] for d in comp.DIMS}
    gates = [0.9] * 150 + [0.1] * 150
    for g in gates:
        t = round(t + 0.1, 6)
        tm = comp.step(st, comp.Inputs(now_s=t, dt=0.1, fresh=True, gate=g, liveness=1.0, lufs_s=-24.0), cfg)
        v = comp.plan_one(tm, tab, cfg, comp.SCREENS, weight=1.0)[0]
        for d in comp.DIMS:
            series[d].append(v[d])
    for d, xs in series.items():
        up, down = xs[:150], xs[150:]
        assert all(b >= a - 1e-12 for a, b in zip(up, up[1:])), d          # louder: never back down
        assert all(b <= a + 1e-12 for a, b in zip(down, down[1:])), d      # quieter: never back up
        assert max(xs) <= 1.0 + 1e-12 and min(xs) >= min(xs[0], xs[-1]) - 1e-12   # no overshoot either way


def test_a_dark_scene_is_not_dimmed_further_and_the_headroom_seam():
    cfg = settings()
    tab = comp.table({"follow": 1.0, "q": 0.5})
    tm = terms(loud=0.0)                                          # silent: the full quiet term
    dark = comp.plan_one(tm, tab, cfg, comp.SCREENS, weight=1.0, apl=0.05)          # below headroom.apl[0]
    assert dark[0][comp.PICTURE] == 1.0 and dark[3][comp.PICTURE] == 0.0
    bright = comp.plan_one(tm, tab, cfg, comp.SCREENS, weight=1.0, apl=0.9)         # above apl[1]: the full share
    assert bright[0][comp.PICTURE] == pytest.approx(0.6)          # max(hard floor 0.6, 1 - 1 x 0.5 x 1 x 1 = 0.5)
    unknown = comp.plan_one(tm, tab, cfg, comp.SCREENS, weight=1.0, apl=None)       # today: headroom.unknown 0.5
    assert unknown[0][comp.PICTURE] == pytest.approx(0.75) and unknown[3][comp.PICTURE] == 0.5
    assert dark[0][comp.PULSE] == bright[0][comp.PULSE] == 0.5    # h only on headroom.dims (the picture)


def test_hold_and_role_weights():
    cfg = settings()
    tab = comp.table(LIMITS)
    tm = terms(loud=0.0)
    held = comp.plan_one(tm, tab, cfg, comp.LAMPS, weight=1.0, hold=True)
    assert held[0] == {d: 1.0 for d in comp.DIMS} and set(held[1].values()) == {0.0}
    full = comp.plan_one(tm, tab, cfg, comp.LAMPS, weight=1.0)[0]
    key = comp.plan_one(tm, tab, cfg, comp.LAMPS, weight=0.6)[0]
    assert full[comp.LEVEL] < key[comp.LEVEL] < 1.0               # the slower partner moves less


def test_lift_keeps_a_tense_build_up_on_the_picture_only():
    cfg = settings()
    tab = comp.table(LIMITS)
    plain = comp.plan_one(terms(loud=0.2), tab, cfg, comp.SCREENS, weight=1.0, apl=0.9)[0]
    lifted = comp.plan_one(terms(loud=0.2, lift={"picture": 0.6}), tab, cfg, comp.SCREENS, weight=1.0, apl=0.9)[0]
    assert lifted[comp.PICTURE] > plain[comp.PICTURE]
    assert all(lifted[d] == plain[d] for d in comp.DIMS if d != comp.PICTURE)
    st = comp.State()                                             # tension 1 and a build at 1: 0.3 + 0.3, eased
    _t, tm = drive(st, cfg, 10.0, fresh=True, gate=0.5, liveness=1.0, tension=1.0, build=1.0)
    assert tm["lift"] == {"picture": pytest.approx(0.6, abs=1e-3)}


# -- loud and range ----------------------------------------------------------------------------------------------------
def test_loud_rises_fast_falls_slowly_and_faster_on_a_fade():
    cfg = settings()
    st = comp.State()
    drive(st, cfg, 5.0, fresh=True, gate=0.0)
    assert st.loud == pytest.approx(0.0, abs=1e-6)
    drive(st, cfg, 0.7, t0=5.0, fresh=True, gate=1.0)             # one rise_s up: ~63 %
    assert st.loud == pytest.approx(1.0 - 2.718281828 ** -1.0, abs=0.01)
    drive(st, cfg, 10.0, t0=6.0, fresh=True, gate=1.0)
    drive(st, cfg, 0.7, t0=16.0, fresh=True, gate=0.2)            # a softer passage, 0.7 s down: slower (fall_s 1.5)
    slow = (1.0 - st.loud) / 0.8                                  # the share of the way down to 0.2
    st2 = comp.State(loud=1.0)
    drive(st2, cfg, 0.7, t0=16.0, fresh=True, gate=0.2, fade=True)   # an outro or an armed end: fade_fall_s 0.35
    assert slow < 0.5 < (1.0 - st2.loud) / 0.8                    # (silence itself falls fast: its own test)
    st3 = comp.State(loud=0.8)                                    # stale features: unknown, held
    drive(st3, cfg, 5.0, fresh=False, gate=None)
    assert st3.loud == 0.8
    drive(st3, cfg, 10.0, fresh=True, gate=0.9, talking=True)     # speech is never loud
    assert st3.loud < 0.01
    drive(st3, cfg, 10.0, fresh=False, piano=0.7)                 # the piano's velocity replaces it
    assert st3.loud == pytest.approx(0.7, abs=1e-3)


def test_the_range_counts_music_only_and_needs_some_of_it():
    cfg = settings()
    st = comp.State()
    _t, tm = drive(st, cfg, 8.0, fresh=True, gate=0.8, lufs_s=-20.0)
    assert tm["range_lu"] is None and tm["range"] == 0.0          # under MIN_RANGE_S of music: nothing pumps yet
    st2 = comp.State()                                            # gaps of silence (-70 LUFS) don't widen it
    _t, tm2 = drive(st2, cfg, 70.0, fresh=True, gate=lambda t: 0.0 if int(t // 5) % 2 else 0.8,
                    lufs_s=lambda t: -70.0 if int(t // 5) % 2 else -20.0)
    assert tm2["range_lu"] == pytest.approx(0.0, abs=1e-9)
    assert len(st2.samples) <= cfg["range"]["window_s"] / comp.SAMPLE_S


def test_fold_bpm_is_75s_rule():
    assert comp.fold_bpm(60.0, 70.0, 180.0) == 120.0
    assert comp.fold_bpm(200.0, 70.0, 180.0) == 100.0
    assert comp.fold_bpm(90.0, 70.0, 180.0) == 90.0 and comp.fold_bpm(0.0, 70.0, 180.0) == 0.0


# -- the enabled-set reader ----------------------------------------------------------------------------------------------
def test_the_enabled_reader_counts_only_live_screens_and_members_exactly_dmx():
    view = {"screens": {"wall-a": {"layer": "live", "cutting": False}, "wall-b": {"layer": "art"},
                        "lab": {"layer": "live", "cutting": True}, "junk": "x"},
            "members": {"lights.a": {"believed": "dmx"}, "lights.b": {"believed": "taken_over"},
                        "lights.c": {"believed": "off"}, "lights.d": {"believed": {"on": True, "bri": 80}},
                        "other.e": {"believed": "dmx"}, "lights.f": {"believed": "DMX"}, "bad": None}}
    assert comp.enabled(view) == (["lab", "wall-a"], ["lights.a", "other.e"])
    assert comp.enabled(None) == ([], []) and comp.enabled({"screens": [], "members": "x"}) == ([], [])


# -- settings and the validator ------------------------------------------------------------------------------------------
def test_bad_settings_and_limits_degrade_and_are_listed():
    probs: list[str] = []
    s = comp.parse_settings({"mode": "loud", "loud": {"rise_s": -1, "music_only": "yes"}, "range": {"lu": [12, 3]},
                             "headroom": {"apl": [0.5, 0.2], "dims": ["glow"]}, "weights": {"tv": 1, "lamps": 9},
                             "lift": {"picture": {"tension": 2, "mood": 1}}, "defaults": {"follow": 1.5,
                                                                                         "q": {"tv": 1}},
                             "extra": 1}, probs)
    text = "\n".join(probs)
    for frag in ("director.compose.mode", "loud.rise_s", "loud.music_only", "range.lu", "headroom.apl",
                 "headroom.dims", "weights.tv", "weights.lamps", "lift.picture.tension", "lift.picture.mood",
                 "defaults.follow", "defaults.q.tv", "director.compose.extra"):
        assert frag in text, frag
    assert s["mode"] == "observe" and s["loud"]["rise_s"] == 0.7 and s["range"]["lu"] == [3.0, 12.0]
    assert s["weights"]["lamps"] == 1.0 and s["defaults"] == {"q": {}}
    lp: list[str] = []
    lim = comp.parse_limits({"follow": 0.5, "q": {"screens": {"picture": 0.9, "glow": 0.1, "pace": "x"}},
                             "floor": 2, "speed": 1}, "vibes.x.compose", lp)
    assert lim == {"follow": 0.5, "q": {"screens": {"picture": 0.9}}}
    assert len(lp) == 4 and all(x.startswith("vibes.x.compose.") for x in lp)


def test_the_validator_lists_loudness_over_1_and_a_floor_over_its_base():
    tab = comp.table({"follow": 1.0, "q": {"lamps": {"level": 0.0}}, "floor": {"screens": {"pace": 0.9}},
                      "base": {"screens": {"pace": 0.8}}})
    probs = comp.validate(tab, {"screens": 1.0, "lamps": 1.4}, "vibes.x.compose")
    assert any("lamps.level" in p and "1.40" in p for p in probs)
    assert any("screens.pace" in p and "floor" in p for p in probs)
    assert comp.validate(comp.table(LIMITS), {"screens": 1.0, "lamps": 1.0}, "x") == []


def test_the_real_show_yaml_and_vibes_compose_blocks_load_clean():
    """show.yaml director.compose and every feel's compose block (vibes.yaml) parse with no problems and pass the
    validator at show.yaml's own weights (the lamp roles' dynamics included)."""
    sy = yaml.safe_load(contract.SHOW_FILE.read_text())
    probs: list[str] = []
    cfg = comp.parse_settings(sy["director"]["compose"], probs)
    assert probs == [] and cfg["mode"] in comp.MODES
    w = dict(cfg["weights"])
    dyn = [r["dynamics"] for r in sy["lamps"]["roles"].values() if "dynamics" in r]
    w[comp.LAMPS] = max([w[comp.LAMPS]] + dyn)
    assert comp.validate(comp.table(cfg["defaults"]), w, "defaults") == []
    vib = yaml.safe_load(contract.VIBES_FILE.read_text())["vibes"]
    blocks = {n: v["compose"] for n, v in vib.items() if "compose" in v}
    assert blocks, "the feels carry their compose limits"
    for n, b in blocks.items():
        lp: list[str] = []
        lim = comp.parse_limits(b, f"vibes.{n}.compose", lp)
        assert lp == [], lp
        assert comp.validate(comp.table(lim), w, n) == []


def test_silence_reaches_quiet_within_two_seconds():
    """75, 17 Oct: with fall_s 1.5 the gate shutting still reads quiet in ~2 s (silence is a fade)."""
    cfg = comp.parse_settings({}, [])
    st = comp.State()
    t = 0.0
    for _ in range(50):                                          # 5 s of loud music
        t += 0.1
        comp.step(st, comp.Inputs(now_s=t, dt=0.1, fresh=True, gate=0.9, lufs_s=-20.0, liveness=1.0), cfg)
    assert st.loud > 0.85
    for _ in range(20):                                          # the gate shuts
        t += 0.1
        comp.step(st, comp.Inputs(now_s=t, dt=0.1, fresh=True, gate=0.0, lufs_s=-60.0, liveness=1.0), cfg)
    assert st.loud < 0.05
    st.loud = 0.9                                                # a softer passage (the gate still open) eases slowly
    for _ in range(20):
        t += 0.1
        comp.step(st, comp.Inputs(now_s=t, dt=0.1, fresh=True, gate=0.4, lufs_s=-35.0, liveness=1.0), cfg)
    assert st.loud > 0.5
