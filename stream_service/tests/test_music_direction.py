"""Music-derived direction (music_direction.py): the show bus -> direction mapping on show-synth songs, liveness,
the panel-as-trim rule, auto.music validation, and the session's _apply_direction (no GPU)."""
from __future__ import annotations

import random

import pytest

from stream_service import music_direction as M
from stream_service.director import AUTO_DEFAULTS, AUTO_MUSIC_DEFAULTS, DIRECTION_DEFAULTS, FX_DIRECTION_DEFAULTS, \
    validate_auto, validate_auto_music, validate_direction
from stream_service.inputs import FixtureSignals
from stream_service.plugins.base import ParamError
from stream_service.tags import descriptor_matches
from stream_service.tools.show_synth import STYLES, Song

from .test_cue import AUTO, live
from .test_sessions import AUTH, client  # noqa: F401 (fixture)

RATE_S = 0.025


def run(style, bpm=None, seconds=None, fixture="screen.north", follow=("effect",), until=None):
    """Play a show-synth song through a FixtureSignals and step a MusicDirection at 10 Hz.
    -> [(t, section, band, proposal, cause, md)] once a proposal exists."""
    sg = Song(style, bpm, 7, t0=0.0)
    if seconds is None:                                # one pass through the song's section plan
        seconds = sg.song_bars * 4 * 60.0 / float(bpm or STYLES[style]["bpm"])
    fs, md = FixtureSignals(fixture), M.MusicDirection(dict(AUTO_MUSIC_DEFAULTS))
    rnd, n, nxt, rows = random.Random(1), 0, 0.1, []
    while n * RATE_S < seconds:
        n += 1
        t = n * RATE_S + rnd.uniform(-0.003, 0.003)
        st = sg.state(t)
        fs.update(t, t, st["fixtures"][fixture], st["activity"])
        if t >= nxt:
            nxt += 0.1
            cause = md.update(t, fs.snapshot, True, follow, True)
            if md.proposal is not None:
                rows.append((t, st["_truth"]["section"], md.band, md.proposal, cause, md))
            if until is not None and until(t, st, md):
                break
    return rows


def settled(rows, section, skip_s=12.0):
    """The rows of `section` after it has run skip_s (each occurrence)."""
    out, start, cur = [], None, None
    for r in rows:
        if r[1] != cur:
            cur, start = r[1], r[0]
        if r[1] == section and r[0] - start >= skip_s:
            out.append(r)
    return out


@pytest.mark.parametrize("bpm", [None, 110])
def test_ambient_is_still_calm_and_gentle(bpm):
    rows = run("ambient", bpm, seconds=90)
    late = [r for r in rows if r[0] > 45]
    assert late and all(r[2] == "still" for r in late)
    p = late[-1][3]
    # drift ~0.11-0.16, swell ~0.30: always under the still band's upper edge (0.30 + 0.04 hysteresis)
    assert p["energy"] < M.EDGES[0] + M.BAND_HYST and p["transition_style"] == "gentle" and "calm" in p["moods"]
    assert p["change_rate"] <= 0.3 and p["fx"]["rate"] <= 0.35 and p["pace"] <= 0.4


def test_jazz_grooves_gently_and_locks():
    rows = run("jazz", seconds=60)
    late = [r for r in rows if r[0] > 40]
    assert {r[2] for r in late} <= {"groove", "still", "lively"} and "groove" in {r[2] for r in late}
    md = late[-1][5]
    assert md.features["locked"] and 90 <= md.features["bpm"] <= 102
    assert 0.25 <= late[-1][3]["energy"] <= 0.6


def test_pop_reaches_peak_in_the_chorus_and_is_wild():
    rows = run("pop")
    chorus = settled(rows, "chorus")
    assert chorus, "the song has a chorus"
    assert max(r[3]["energy"] for r in chorus) >= 0.8
    assert any(r[2] == "peak" and r[3]["transition_style"] == "wild" for r in chorus)
    assert max(r[3]["change_rate"] for r in chorus) >= 0.7
    intro = [r for r in rows if r[1] == rows[0][1]]
    assert min(r[3]["energy"] for r in intro) < 0.6           # it starts lower than the chorus


def test_techno_peak_is_high_energy():
    rows = run("techno")
    assert max(r[3]["energy"] for r in rows) >= 0.9


def test_a_section_change_settles_then_reseeds():
    sg = Song("pop", None, 7, t0=0.0)
    secs, seen = [], {"settle": None, "section": None}

    def until(t, st, md):
        if md._settle_at is not None and seen["settle"] is None:
            seen["settle"] = t
        if seen["settle"] is not None and md.last_change and md.last_change["cause"] in ("section", "band") \
                and md.last_change["at"] >= seen["settle"]:
            seen["section"] = md.last_change["at"]
            return True
        return False
    run("pop", until=until)
    assert seen["settle"] is not None and seen["section"] is not None
    assert seen["section"] - seen["settle"] <= M.SETTLE_S + 0.2      # published at the end of the settle


def test_publishes_are_rate_limited():
    rows = run("ambient", seconds=120)
    pubs = [r[0] for r in rows if r[4] == "music"]
    assert all(b - a >= M.APPLY_MIN_S - 0.05 for a, b in zip(pubs, pubs[1:]))


def test_liveness_holds_then_fades_and_recovers():
    sg = Song("pop", None, 7, t0=0.0)
    fs, md = FixtureSignals("screen.north"), M.MusicDirection(dict(AUTO_MUSIC_DEFAULTS))
    t, ms = 0.0, []
    for i in range(int(20 / RATE_S)):                  # 20 s fresh
        t = (i + 1) * RATE_S
        st = sg.state(t)
        fs.update(t, t, st["fixtures"]["screen.north"], st["activity"])
        if i % 4 == 0:
            md.update(t, fs.snapshot, True, ("effect",), True)
    assert md.m == 1.0
    t_stale = t
    while t < t_stale + 35:                            # stale: hold 10 s, fade over 20 s
        t += 0.1
        md.update(t, fs.snapshot, False, ("effect",), True)
        ms.append((t - t_stale, md.m))
    assert all(m == 1.0 for dt, m in ms if dt < M.HOLD_S)
    assert [m for dt, m in ms if dt > M.HOLD_S + M.FADE_S + 0.5][-1] == 0.0
    t_back, ms = t, []
    while t < t_back + 15:                             # fresh again: 3 s, then from cold at once
        t += 0.1
        md.update(t, fs.snapshot, True, ("effect",), True)
        ms.append((t - t_back, md.m))
    assert all(m == 0.0 for dt, m in ms if dt < M.RECOVER_S - 0.15)
    assert ms[-1][1] == 1.0


def test_inactive_or_disabled_is_zero_at_once():
    rows = run("pop", seconds=20)
    md = rows[-1][5]
    assert md.m == 1.0
    assert md.update(30.0, None, False, ("effect",), False) == "off" and md.m == 0.0
    md2 = M.MusicDirection({**AUTO_MUSIC_DEFAULTS, "enabled": False})
    assert md2.update(1.0, None, True, ("effect",), True) is None and md2.m == 0.0


def test_activity_calibration_range():
    md = M.MusicDirection({**AUTO_MUSIC_DEFAULTS, "activity": [0.2, 0.7]})

    class Snap:
        slow = {"activity_slow": 0.45}
        raw = {"activity": 0.45}
        stable = {"effect": 0}
        follower = {"edges_accepted": 0, "pulse_peak": 0.0}
        period = None

        def locked(self, now):
            return False
    md._features(1.0, Snap(), ("effect",))
    assert md.features["act"] == pytest.approx(0.5)


# -- the panel as a trim around the music ---------------------------------------------------------------------
def panel(**kw):
    return validate_direction(kw)


MUSIC = {"energy": 0.11, "pace": 0.19, "intensity": 0.64, "change_rate": 0.1, "moods": ["calm"],
         "transition_style": "gentle", "fx": {"density": 0.19, "rate": 0.14, "moods": ["calm"]}}


def test_defaults_follow_the_music():
    eff = M.effective_direction(panel(), MUSIC, 1.0)
    assert eff["energy"] == 0.11 and eff["pace"] == 0.19 and eff["change_rate"] == 0.1
    assert eff["intensity"] == 0.64                    # intensity's neutral is 1.0
    assert eff["fx"]["rate"] == 0.14 and eff["fx"]["moods"] == ["calm"]
    assert eff["moods"] == ["calm"] and eff["transition_style"] == "gentle"


def test_panel_numbers_trim_and_words_pin():
    eff = M.effective_direction(panel(energy=0.3, transition_style="wild", moods=["dark"]), MUSIC, 1.0)
    assert eff["energy"] == 0.0                        # 0.3 + (0.11 - 0.5), clamped
    assert eff["transition_style"] == "wild" and eff["moods"] == ["dark"]
    fields = M.music_fields(panel(energy=0.3, transition_style="wild", moods=["dark"]), MUSIC, 1.0)
    assert "energy" in fields and "transition_style" not in fields and "moods" not in fields and "fx.moods" in fields


def test_m_zero_is_the_panel_and_small_m_keeps_panel_words():
    p = panel()
    assert M.effective_direction(p, MUSIC, 0.0) is p and M.music_fields(p, MUSIC, 0.0) == []
    eff = M.effective_direction(p, MUSIC, 0.25)
    assert eff["moods"] == [] and eff["transition_style"] is None
    assert eff["energy"] == pytest.approx(0.5 + 0.25 * (0.11 - 0.5), abs=1e-3)


def test_pick_key_ignores_small_numeric_moves():
    a = dict(MUSIC, energy=0.31)
    assert M.pick_key(a) == M.pick_key(dict(a, energy=0.34, pace=0.9))
    assert M.pick_key(a) != M.pick_key(dict(a, energy=0.36))
    assert M.pick_key(a) != M.pick_key(dict(a, transition_style="wild"))


# -- validation and defaults ----------------------------------------------------------------------------------
def test_auto_music_defaults_and_validation():
    a = validate_auto({})
    assert a["music"] == AUTO_MUSIC_DEFAULTS and a["follow_dmx"] == ["effect"] == AUTO_DEFAULTS["follow_dmx"]
    b = validate_auto({"music": {"weight": 0.5}}, a)
    assert b["music"] == {**AUTO_MUSIC_DEFAULTS, "weight": 0.5}
    assert validate_auto_music({"enabled": False}, b["music"])["weight"] == 0.5
    for bad in ({"x": 1}, {"enabled": 1}, {"weight": "a"}, {"activity": [0.5, 0.52]}, {"activity": [0, 1, 2]}, []):
        with pytest.raises(ParamError):
            validate_auto_music(bad)


def test_scalar_style_tag_matches():
    assert descriptor_matches("style", "gentle", "gentle")
    assert not descriptor_matches("style", "wild", "gentle")
    assert descriptor_matches("moods", ["calm", "dark"], ["dark"])


# -- the session: one place a direction takes effect -------------------------------------------------------------
async def test_session_applies_music_without_dropping_on_small_moves(client):
    sid, s, eng = await live(client, auto=AUTO)
    s.prepared_pick = {"src": object(), "trans": object()}
    s.music.proposal, s.music.m = dict(MUSIC, energy=0.52), 1.0       # same pick key as the panel's 0.5
    s.music.proposal["moods"], s.music.proposal["transition_style"] = [], None
    s._apply_direction("music")
    assert s.prepared_pick is not None                                   # a small move keeps the prepared pick
    assert s.director.direction is s.direction_effective and s.direction_effective["energy"] == 0.52
    s.music.proposal = dict(MUSIC)                                        # calm, gentle: a new pick key
    s._apply_direction("band")
    assert s.prepared_pick is None and s._dir_gen == 1                   # dropped once ...
    s.prepared_pick = {"src": object(), "trans": object()}
    s.music.proposal = dict(MUSIC, transition_style="lively")
    s._apply_direction("band")
    assert s.prepared_pick is not None and s._dir_gen == 1               # ... and not again within 30 s
    assert s.fx_director.direction is s.direction_effective
    s._apply_direction("panel")                                           # a panel change always re-picks
    assert s.prepared_pick is None and s._dir_gen == 2


async def test_session_summary_shows_panel_effective_and_music_fields(client):
    sid, s, eng = await live(client, auto=AUTO)
    s.music.proposal, s.music.m = dict(MUSIC), 1.0
    s._apply_direction("online")
    r = await client.get(f"/api/sessions/{sid}", headers=AUTH)
    j = await r.json()
    assert j["direction"]["energy"] == 0.5                                # the panel's own, unchanged
    assert j["direction_effective"]["energy"] == 0.11 and j["direction_effective"]["transition_style"] == "gentle"
    assert "energy" in j["music_fields"] and "moods" in j["music_fields"]
    assert j["music"]["m"] == 1.0 and j["auto"]["music"] == AUTO_MUSIC_DEFAULTS
    r = await client.patch(f"/api/sessions/{sid}", json={"auto": {"music": {"enabled": False}}}, headers=AUTH)
    assert r.status == 200
    s.tick(1e9)
    j = await (await client.get(f"/api/sessions/{sid}", headers=AUTH)).json()
    assert j["direction_effective"] == j["direction"] and j["music_fields"] == []


# -- the room's director (show_service room.direction, §8.7) takes the proposal slot ------------------------------
def room_dir(energy=0.7, vibe="party", section=1, moods=("bright",), genre="house", groove=0.6, t=0.0):
    data = {"energy": energy, "pace": 0.6, "intensity": 1.0, "change_rate": 0.5, "moods": list(moods),
            "genre": genre, "transition_style": None, "palette_family": None,
            "fx": {"density": 0.5, "rate": 0.4, "stages": None, "moods": ["bright"]},
            "arousal": 0.6, "tension": 0.2, "section": {"id": section}, "density": 0.3, "groove": groove}
    return {"direction": validate_direction(data), "vibe": vibe, "section": data["section"], "groove": groove, "t": t}


def test_room_direction_takes_the_slot_and_maestro_returns_when_it_goes():
    md = M.MusicDirection(dict(AUTO_MUSIC_DEFAULTS))
    causes = [md.update(t / 10, None, False, (), True, room=room_dir()) for t in range(40)]
    assert md.source == "room" and md.m == 1.0 and "online" in causes          # after RECOVER_S, as for Maestro
    assert md.proposal["energy"] == 0.7 and md.proposal["genre"] == "house" and md.proposal["moods"] == ["bright"]
    panel = validate_direction({})
    eff = M.effective_direction(panel, md.proposal, md.m)
    assert eff["genre"] == "house" and eff["moods"] == ["bright"] and eff["energy"] == 0.7
    assert "genre" in M.music_fields(panel, md.proposal, md.m)
    sig = md.signals(4.0)
    assert sig["energy"] == 0.7 and sig["drive"] == 0.6
    # a new section bumps the epoch; new words publish at once
    ep = md.epoch
    assert md.update(4.1, None, False, (), True, room=room_dir(section=2)) == "section" and md.epoch == ep + 1
    assert md.update(4.2, None, False, (), True, room=room_dir(section=2, moods=("calm",))) == "band"
    # the room goes quiet: the proposal is dropped at once (no Maestro features): the panel's direction
    assert md.update(4.3, None, False, (), True, room=None) == "band"
    assert md.source == "maestro" and md.proposal is None
    assert M.effective_direction(panel, md.proposal, md.m) == panel


def test_manager_keeps_only_a_speaking_valid_room_direction():
    from types import SimpleNamespace

    from stream_service.sessions import SessionManager
    mgr = SimpleNamespace(room_direction=None, presets=SimpleNamespace(vocab=SimpleNamespace(words={})))
    on, now = SessionManager.on_room_message, SessionManager.room_direction_now
    body = {"energy": 0.4, "moods": ["calm"], "vibe": "dinner", "groove": 0.1, "section": {"id": 3}, "arousal": 0.2}
    on(mgr, {"type": "room.look", "data": {"palette": "x"}})
    assert mgr.room_direction is None
    on(mgr, {"type": "room.direction", "data": body})
    r = mgr.room_direction
    assert r["vibe"] == "dinner" and r["direction"]["energy"] == 0.4 and now(mgr, r["t"] + 1) is r
    assert now(mgr, r["t"] + M.ROOM_STALE_S + 1) is None                 # stale
    on(mgr, {"type": "room.direction", "data": {**body, "vibe": None}})
    assert now(mgr, mgr.room_direction["t"]) is None                     # neutral: Maestro proposes
    on(mgr, {"type": "room.direction", "data": {**body, "energy": "loud"}})
    assert mgr.room_direction["vibe"] is None                            # a bad one is dropped, the last kept
