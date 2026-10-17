"""show_service's music as the renderer's signals (music_feed.py; d4's G1 / G2): features smoothing and liveness,
the clock's beat / bar / phrase signals at photon time, the epoch guard, the blend, and MusicDirection's feed path
(no GPU)."""
from __future__ import annotations

import math

from stream_service import music_direction as M
from stream_service.director import AUTO_MUSIC_DEFAULTS
from stream_service.music_feed import ClockSignals, FRESH_S, MusicFeed, blend, clock_signals

from .test_sessions import client  # noqa: F401 (fixture)


def feed_for(seconds, energy=0.8, drive=0.4, punch=0.3, t0=100.0, rate=25.0):
    f = MusicFeed()
    n = int(seconds * rate)
    for i in range(n):
        f.on_message({"type": "music.features", "data": {"energy": energy, "drive": drive, "punch": punch}},
                     t0 + i / rate)
    return f, t0 + (n - 1) / rate


def test_features_smooth_and_a_quiet_room_is_live():
    f, t = feed_for(6.0, energy=0.8)
    s = f.snapshot()
    assert abs(s.activity - 0.8) < 0.01 and 0.7 < s.activity_slow <= 0.8 and s.drive == 0.4
    assert f.liveness(t) == 1.0 and f.fresh(t) is s
    q, tq = feed_for(3.0, energy=0.0)
    assert q.fresh(tq) is not None and q.snapshot().activity_slow == 0.0      # quiet: a real 0, still live
    assert f.fresh(t + FRESH_S + 0.1) is None and 0.0 < f.liveness(t + FRESH_S + 1.0) < 1.0
    assert f.liveness(t + FRESH_S + 3.0) == 0.0


def clock(now, bpm=120.0, gate="beat", epoch=1, anchor_beat=0):
    return {"bpm": bpm, "anchor_ms": now * 1000.0, "beat_at_anchor": anchor_beat, "beats_per_bar": 4,
            "bar_anchor_beat": 0, "phrase_bars": 4, "conf": 0.9, "gate": gate, "state": "locked", "epoch": epoch}


def test_clock_signals_at_photon_time():
    c = clock(10.0)                                   # beat 0 at t = 10 s, 0.5 s a beat
    s = clock_signals(c, 10.0)
    assert s["beat_phase"] == 0.0 and s["beat"] == 1.0 and s["downbeat"] == 1.0 and s["bar_phase"] == 0.0
    s = clock_signals(c, 10.75)                       # beat 1.5
    assert abs(s["beat_phase"] - 0.5) < 1e-9 and abs(s["bar_phase"] - 1.5 / 4) < 1e-9 and s["downbeat"] == 0.0
    assert abs(s["beat"] - math.exp(-0.25 / 0.18)) < 1e-9 and abs(s["phrase_phase"] - 1.5 / 16) < 1e-9
    assert abs(s["tempo"] - 60 / 90) < 1e-9
    d = clock_signals({**c, "gate": "dmx"}, 10.75)    # not sure: phases only
    assert "beat" not in d and "downbeat" not in d and "tempo" not in d and "bar_phase" in d
    assert clock_signals({**c, "gate": "none"}, 10.75) == {}


def test_an_epoch_jump_never_flashes_a_beat():
    cs = ClockSignals()
    assert cs.sample(clock(10.0, epoch=1), 10.1)["beat"] > 0
    jumped = clock(10.2, epoch=2)                     # re-anchored: beat 0 right now
    s = cs.sample(jumped, 10.2)
    assert s["beat"] == 0.0 and s["downbeat"] == 0.0
    assert cs.sample(jumped, 10.7)["beat"] == 1.0     # the next whole beat flashes as usual


def test_blend_eases_activity_and_replaces_the_followers_beat():
    f, t = feed_for(3.0, energy=0.9)
    sig = {"activity": 0.2, "activity_slow": 0.2, "beat": 0.7, "beat_phase": 0.3}
    blend(sig, f.snapshot(), 0.5, {"beat": 0.0, "beat_phase": 0.9, "tempo": 0.4})
    assert 0.5 < sig["activity"] < 0.6 and sig["beat"] == 0.0 and sig["beat_phase"] == 0.9 and "tempo" not in sig


def test_music_direction_follows_the_feed():
    md = M.MusicDirection(dict(AUTO_MUSIC_DEFAULTS))
    f = MusicFeed()
    causes = []
    for i in range(400):                              # 16 s of loud, driving music at 25 Hz, ticked at 10 Hz-ish
        t = 100.0 + i * 0.04
        f.on_message({"type": "music.features", "data": {"energy": 0.9, "drive": 0.8, "punch": 0.6}}, t)
        f.on_message({"type": "music.clock", "data": clock(100.0, bpm=128.0)}, t)
        if i % 3 == 0:
            causes.append(md.update(t, None, False, (), True, feed=f.fresh(t)))
    assert md.source == "show" and md.feeding and md.m == 1.0 and "online" in causes
    assert md.proposal["energy"] > 0.7 and md.band in ("lively", "peak")
    sig = md.signals(t)
    assert sig["drive"] == 0.8 and sig["punch"] == 0.6 and abs(sig["tempo"] - (128 - 60) / 90) < 1e-6
    # show_service goes away: the desk's path (nothing fresh there): the proposal is dropped at once
    assert md.update(t + 2.0, None, False, (), True, feed=None) is not None
    assert md.source == "maestro" and not md.feeding and md.proposal is None


def test_room_harmony_eases_and_marks_chord_changes():
    f = MusicFeed()
    for i, cc in enumerate([0.0, 0.0, 0.9, 0.9, 0.1, 0.8]):
        f.on_message({"type": "music.harmony", "data": {"tension": 0.6, "register": 0.4, "spread": 0.3,
                                                       "chord_change": cc}}, 50.0 + i * 0.1)
    h = f.harmony(50.5)
    assert h["tension"] == 0.6 and h["register"] == 0.4 and h["chord_change_t"] == 50.5   # the second rising edge
    assert f.harmony(52.0) is None


def test_shader_textures_use_the_room_spectrum_without_the_piano():
    from types import SimpleNamespace

    from stream_service.sound import Sound
    s = Sound.__new__(Sound)
    s.view = SimpleNamespace(features_t=1000.0, t=1500.0)
    s.frame = SimpleNamespace(present=False)
    assert s.spectrum_live and not s.present
    s.view.t = 2500.0
    assert not s.spectrum_live


async def test_bar_snap_event_hints_and_shared_beats(client):  # noqa: F811
    """G9: "bar" cuts land on show_service's next downbeat; a room.event latches a cut at its own time; the cue
    carries its glass time for the room's followers."""
    import time

    from .test_cue import AUTO, cue, live
    sid, s, eng = await live(client, auto={**AUTO, "snap": "bar"})
    mgr = s.mgr
    now = time.monotonic()
    c = {"bpm": 120.0, "anchor_ms": now * 1000.0, "beat_at_anchor": 0, "beats_per_bar": 4, "bar_anchor_beat": 0,
         "phrase_bars": 4, "conf": 0.9, "gate": "beat", "state": "locked", "epoch": 1}
    mgr.music_feed.on_message({"type": "music.clock", "data": c}, now)
    t = s._next_downbeat(now)
    assert t is not None and abs(((t - now) / 0.5) % 4) < 1e-6 and t > now + s.sampler.lead   # a whole bar
    r = await cue(client, sid, to="auto", at="bar")
    assert r.status == 202, await r.text()
    assert abs(s.last_cue["at_pts"] - s._pts_at(t - s.sampler.lead)) < 0.02 if s.last_cue.get("at_pts") else True
    inflight = [x for x in s.inflight.values() if x.get("cue_id") == s.last_cue["cue_id"]]
    assert inflight and abs(inflight[0]["photon_at"] - t) < 1e-6        # what the followers are given
    # a room.event latches a cut (deduped across the two publishers)
    s.director.arm(True)
    s.director.latched = None
    mgr.on_room_message({"type": "room.event", "data": {"kind": "section_change", "at_ms": (now + 3.0) * 1000}})
    mgr.on_room_message({"type": "room.event", "data": {"kind": "section_change", "at_ms": (now + 3.05) * 1000}})
    assert s.director.latched and s.director.latched_kind == "room_event" and s._event_hint == now + 3.0
    mgr.on_room_message({"type": "room.event", "data": {"kind": "accent", "at_ms": now * 1000}})   # not a cut
    # no sure clock: bar falls back to the beat (then now)
    mgr.music_feed.on_message({"type": "music.clock", "data": {**c, "gate": "none"}}, now)
    assert s._next_downbeat(now) is None


def test_feed_levels_bands_rhythm_and_room():
    f = MusicFeed()
    bands = {"sub": {"level": 0.8}, "bass": {"level": 0.6}, "low_mid": {"level": 0.2}, "mid": {"level": 0.4},
             "high_mid": {"level": 0.1}, "air": {"level": 0.3}}
    for i in range(50):
        f.on_message({"type": "music.features", "data": {"energy": 0.5, "bands": bands, "brightness": 0.7}},
                     10.0 + i * 0.04)
    f.on_message({"type": "music.rhythm", "data": {"density": 0.9}}, 12.0)
    f.on_room_direction({"arousal": 0.8, "groove": 0.4}, 12.0)
    lv = f.levels(12.0)
    assert abs(lv["bass"] - 0.7) < 1e-6 and abs(lv["mids"] - 0.3) < 1e-6 and abs(lv["highs"] - 0.2) < 1e-6
    assert abs(lv["brightness"] - 0.7) < 1e-6 and lv["density"] == 0.9 and lv["arousal"] == 0.8 and lv["groove"] == 0.4
    sig = {}
    blend(sig, f.snapshot(), 1.0, {}, lv)
    assert sig["bass"] == 0.7 and sig["arousal"] == 0.8
    assert "bass" not in f.levels(30.0)                                   # stale: n/a


async def test_a_build_queues_and_prepares_the_peak_scene_and_a_drop_cuts_early(client):  # noqa: F811
    """G10: music.context's build_progress >= 0.5 puts a peak pick at the head of Up next and prepares it; a drop
    (urgent) is honoured at half of min dwell."""
    import time

    from .test_cue import AUTO, live
    sid, s, eng = await live(client, auto=AUTO)
    s.director.arm(True)
    now = time.monotonic()
    s._upnext_fill(now)
    s.mgr.music_feed.on_message({"type": "music.context", "data": {"section": {"id": 3, "build_progress": 0.7}}}, now)
    s._build_tick(now)
    head = s.upnext.head("scene")
    assert s._building and head["why_words"].startswith("For the drop") and s._pick_task is not None
    s._build_tick(now)                                                    # once per build
    assert s.upnext.head("scene")["id"] == head["id"]
    s.mgr.music_feed.on_message({"type": "music.context", "data": {"section": {"id": 4, "build_progress": 0.0}}},
                                now + 1)
    s._build_tick(now + 1)
    assert not s._building
    # the drop: urgent, at half of min dwell (AUTO dwell_s [6, 12])
    d = s.director
    d.latched = None
    d.scene_done(now - 3.5)
    assert d.hint("drop (the room's music)", urgent=True)
    assert d.due(now) is not None and d.due(now)[0] == "room_event"
    d.latched, d.urgent = None, False
    assert d.hint("section change") and d.due(now) is None               # a section waits for the full min dwell


# -- the room's sound straight into the mappings (music_feed.Response; owner, 17 Oct: "the mic is on") ---------------
def feed_steps(f, t0, steps, rate=25.0, **extra):
    """[(seconds, energy, onset)] at 25 Hz from t0 -> the last message's time."""
    t = t0
    for secs, e, on in steps:
        for _ in range(int(secs * rate)):
            f.on_message({"type": "music.features", "data": {"energy": e, "onset": on, "drive": 0.4, "punch": 0.5,
                                                             **extra}}, t)
            t += 1.0 / rate
    return t - 1.0 / rate


class _DeskSampler:
    """A live desk with nothing in its signals (the runtime's sampler slot): liveness 1."""
    w, lead = 1.0, 0.0

    def sample(self, now):
        return {}


def test_fast_energy_follows_a_step_while_the_directions_stays_slow():
    from stream_service.music_feed import FADE_S, Response, music_signals
    from stream_service.modulation import SessionRuntime
    md, f = M.MusicDirection(dict(AUTO_MUSIC_DEFAULTS)), MusicFeed()
    assert Response().attack_s >= 0.3                                  # §9.5: tau >= 300 ms on the screens
    t, seen = 100.0, {}
    for e, secs in ((0.2, 8.0), (0.9, 1.0)):            # quiet, then a loud step, ticked at 10 Hz
        for i in range(int(secs * 25)):
            t += 0.04
            f.on_message({"type": "music.features", "data": {"energy": e, "drive": 0.4, "punch": 0.5}}, t)
            if i % 2 == 0:
                md.update(t, None, False, (), True, feed=f.fresh(t))
            if e == 0.9:
                seen[i + 1] = f.energy_now(t)           # n messages (x 40 ms) into the step
        if e == 0.2:
            assert abs(f.energy_now(t) - 0.2) < 1e-6
    # attack tau 0.3 s: 63% of the step by 0.3 s, 81% by 0.5 s, 96% by 1 s
    assert 0.2 + 0.7 * 0.6 < seen[8] < 0.2 + 0.7 * 0.7 and seen[13] > 0.2 + 0.7 * 0.8 and seen[25] > 0.86
    slow = md.signals(t)
    assert slow["energy"] == slow["energy_slow"] < 0.4                  # MusicDirection's (direction, epoch, picks)
    sig = {}
    music_signals(sig, {**slow, "music_w": 1.0}, 1.0, f.energy_now(t), 1.0)
    assert sig["energy"] > 0.86 and sig["energy_slow"] == slow["energy"]
    sig = {}
    music_signals(sig, slow, 1.0)                                       # no feed: as before (the slow one)
    assert sig["energy"] == slow["energy"]
    rt = SessionRuntime(None)                                           # the render thread's view
    rt.feed, rt.extra = f, {**slow, "music_w": f.liveness(t)}
    for k in range(10):                                                 # (n/a-able signals fade in over frames)
        rt.begin_frame(t + k / 30)
    assert rt.signals["energy"] > 0.86 and rt.signals["energy_slow"] == slow["energy"]
    rt.sampler, rt.extra = _DeskSampler(), dict(slow)                   # the session's music is the desk's (no
    rt.begin_frame(t + 0.4)                                             # music_w): the slow energy, not the feed's
    assert abs(rt.signals["energy"] - slow["energy"]) < 1e-9 and rt.signals["energy"] < 0.4
    # release: down within about a second
    t2 = feed_steps(f, t + 0.04, [(1.0, 0.2, 0.0)])
    assert f.energy_now(t2) < 0.4
    # the feed stops: the last fast value, weighed by the liveness (fading), then none (end to end, through the
    # session tick and the render thread: test_session_inputs_show_the_rooms_drive_and_cuts_stay_on_the_sure_clock)
    sig = {}
    music_signals(sig, {**slow, "music_w": 1.0}, 1.0, f.energy_now(t2 + 2.0), f.liveness(t2 + 2.0))
    assert f.energy_now(t2 + 2.0) == f.snapshot().energy_fast and 0.0 < f.liveness(t2 + 2.0) < 1.0
    assert f.energy_now(t2 + FRESH_S + FADE_S + 0.1) is None
    # off in the file: no fast energy
    f.response.cfg = __import__("dataclasses").replace(f.response.cfg, energy_on=False)
    assert f.energy_now(t2) is None


def test_onset_pulses_stand_in_for_an_unsure_beat_only():
    f = MusicFeed()
    t0 = 50.0
    t = feed_steps(f, t0, [(1.2, 0.6, 0.1)])
    assert f.onset_pulse(t) == 0.0                                      # weak onsets: no pulse
    f.on_message({"type": "music.features", "data": {"energy": 0.6, "onset": 0.8}}, t + 0.04)
    f.on_message({"type": "music.features", "data": {"energy": 0.6, "onset": 0.9}}, t + 0.08)   # same hit (< gap)
    hit = t + 0.04
    assert f.snapshot().onset_t == hit and f.onset_pulse(hit) == 1.0
    assert abs(f.onset_pulse(hit + 0.15) - math.exp(-1.0)) < 1e-9       # decays, tau 0.15 s
    f.on_message({"type": "music.features", "data": {"energy": 0.6, "onset": 0.1}}, t + 0.20)
    f.on_message({"type": "music.features", "data": {"energy": 0.6, "onset": 0.7}}, t + 0.32)  # too soon (gap)
    assert f.snapshot().onset_t == hit
    f.on_message({"type": "music.features", "data": {"energy": 0.6, "onset": 0.1}}, t + 0.36)
    f.on_message({"type": "music.features", "data": {"energy": 0.6, "onset": 0.7}}, t + 0.40)  # the next hit
    assert f.snapshot().onset_t == t + 0.40
    assert f.snapshot().onset_n == 2                                    # counted (the inputs API's pulse rate)
    # the strength is read against the loudness gate (onset = flux x gate): a quiet mic pulses on the same hits
    for gate, on, fires in ((0.3, 0.2, True), (1.0, 0.08, False), (0.02, 0.01, False), (0.05, 0.03, True)):
        g = MusicFeed()
        tg = feed_steps(g, t0, [(0.5, 0.3, 0.0)], gate=gate)
        g.on_message({"type": "music.features", "data": {"energy": 0.3, "onset": on, "gate": gate}}, tg + 0.04)
        assert (g.snapshot().onset_t == tg + 0.04) is fires, (gate, on)
    # strong: over Response.onset (0.2), or relative (2.5) x its recent mean, never under the floor (0.1): sparse
    # quiet music pulses on its own hits; busy music still on the fixed level
    for level, hit, rel, fires in ((0.06, 0.16, 2.5, True), (0.06, 0.16, 0.0, False), (0.06, 0.12, 2.5, False),
                                   (0.12, 0.21, 2.5, True), (0.0, 0.09, 2.5, False), (0.0, 0.11, 2.5, True)):
        g = MusicFeed()
        g.response.cfg = __import__("dataclasses").replace(g.response.cfg, onset_rel=rel)
        tg = feed_steps(g, t0, [(6.0, 0.3, level)])
        assert g.snapshot().onset_t is None and abs(g.snapshot().onset_mean - level) < 1e-9
        g.on_message({"type": "music.features", "data": {"energy": 0.3, "onset": hit}}, tg + 0.04)
        assert (g.snapshot().onset_t == tg + 0.04) is fires, (level, hit, rel)
    # the blend: the onset pulse is the beat unless the clock is sure; a sure clock's beat always wins
    sig = {"beat": 0.0}
    assert blend(sig, f.snapshot(), 1.0, {"beat_phase": 0.3}, None, f.onset_pulse(t + 0.40)) == "onset"
    assert sig["beat"] == 1.0
    sig = {"beat": 0.3, "beat_locked": True}                            # a locked desk follower predicts: it stays
    assert blend(sig, f.snapshot(), 1.0, {}, None, 1.0) is None and sig["beat"] == 0.3
    sig = {"beat": 0.0}
    assert blend(sig, f.snapshot(), 1.0, {"beat": 0.25, "beat_phase": 0.3}, None, 1.0) == "clock"
    assert sig["beat"] == 0.25
    sig = {"beat": 0.0}
    assert blend(sig, f.snapshot(), 0.0, {}, None, 1.0) is None and sig["beat"] == 0.0   # feed down: the desk's


def test_tempo_comes_sooner_than_the_sure_clock_but_phases_keep_their_gate():
    f = MusicFeed()
    base = {**clock(10.0, bpm=105.0, gate="none"), "state": "holding", "conf": 0.35}
    f.on_message({"type": "music.clock", "data": base}, 10.0)
    assert f.tempo_bpm(10.0) == 105.0 and f.tempo_bpm(12.0) is None              # stale: none
    for bad in ({"conf": 0.25}, {"state": "searching", "conf": 0.5}, {"bpm": 0.0}):
        f.on_message({"type": "music.clock", "data": {**base, **bad}}, 10.0)
        assert f.tempo_bpm(10.0) is None, bad
    f.on_message({"type": "music.clock", "data": {**base, "gate": "beat", "conf": 0.9}}, 10.0)
    assert f.tempo_bpm(10.0) == 105.0
    assert clock_signals(base, 10.25) == {}                                        # gate none: no phases, no beat
    # an unsure guess wanders (half / double): eased, tau 2 s; a sure clock's bpm is taken as it is
    g = MusicFeed()
    g.on_message({"type": "music.clock", "data": base}, 30.0)
    g.on_message({"type": "music.clock", "data": {**base, "bpm": 140.0}}, 30.1)
    assert abs(g.tempo_bpm(30.1) - (105.0 + 35.0 * (1.0 - math.exp(-0.05)))) < 1e-9
    for k in range(2, 21):                                                         # 2 s at 10 Hz: 63% of the way
        g.on_message({"type": "music.clock", "data": {**base, "bpm": 140.0}}, 30.0 + k * 0.1)
    assert abs(g.tempo_bpm(32.0) - (140.0 - 35.0 * math.exp(-1.0))) < 1e-6
    g.on_message({"type": "music.clock", "data": {**base, "bpm": 140.0, "gate": "beat", "conf": 0.8}}, 32.1)
    assert g.tempo_bpm(32.1) == 140.0
    # MusicDirection: the mappings' tempo now; the proposal's pace waits for a sure clock
    md = M.MusicDirection(dict(AUTO_MUSIC_DEFAULTS))
    for i in range(100):
        t = 20.0 + i * 0.04
        f.on_message({"type": "music.features", "data": {"energy": 0.5, "drive": 0.4, "punch": 0.3}}, t)
        f.on_message({"type": "music.clock", "data": base}, t)
        md.update(t, None, False, (), True, feed=f.fresh(t))
    assert abs(md.signals(t)["tempo"] - (105.0 - 60.0) / 90.0) < 1e-9
    assert md.features["bpm"] == 105.0 and md.features["locked"] is False and md._tempo_sure is None
    e = md.features["energy"]
    assert md.proposal["pace"] == round(0.1 + 0.8 * e, 3)                         # no tempo in the pace yet


def test_tempo_folds_half_and_double_time_guesses_by_octaves():
    from stream_service.music_feed import fold_bpm
    assert fold_bpm(53.0, 70.0, 180.0) == 106.0 and fold_bpm(30.0, 70.0, 180.0) == 120.0
    assert fold_bpm(183.0, 70.0, 180.0) == 91.5 and fold_bpm(120.0, 70.0, 180.0) == 120.0
    assert fold_bpm(0.0, 70.0, 180.0) == 0.0 and fold_bpm(53.0, 0.0, 180.0) == 53.0     # off: as it is
    assert fold_bpm(130.0, 100.0, 150.0) == 130.0 and fold_bpm(90.0, 100.0, 150.0) == 180.0   # narrow: up only
    f = MusicFeed()
    base = {**clock(10.0, bpm=53.0, gate="beat"), "state": "locked", "conf": 0.9}
    f.on_message({"type": "music.clock", "data": base}, 10.0)
    assert f.tempo_bpm(10.0) == 106.0                                              # a half-time lock: its real tempo
    md = M.MusicDirection(dict(AUTO_MUSIC_DEFAULTS))
    for i in range(50):
        t = 10.0 + i * 0.04
        f.on_message({"type": "music.features", "data": {"energy": 0.5, "drive": 0.4, "punch": 0.3}}, t)
        f.on_message({"type": "music.clock", "data": base}, t)
        md.update(t, None, False, (), True, feed=f.fresh(t))
    assert abs(md.signals(t)["tempo"] - (106.0 - 60.0) / 90.0) < 1e-9 and md._tempo_sure == md._tempo


def test_music_response_file_hot_reloads_and_degrades(tmp_path):
    import json as _json
    import os

    from stream_service.music_feed import RESPONSE_FILE, Response, ResponseFile, parse_response
    cfg, errs = parse_response({"energy": {"attack_s": 0.05, "release_s": "slow"},
                                "section_follow": {"amount": 0.5, "latched_fams": ["timing"], "amout": 0.2,
                                                   "latched_bindings": ["scene/x/p"]},
                                "beat_fallback": {"on": False}, "tempo": {"min_conf": 2}, "tempoo": {}})
    assert cfg.attack_s == 0.05 and cfg.release_s == Response.release_s and cfg.follow == 0.5
    assert cfg.latched == frozenset({"timing"}) and cfg.onset_on is False and cfg.tempo_conf == Response.tempo_conf
    assert cfg.latched_bindings == frozenset({"scene/x/p"})
    assert len(errs) == 4 and any("amout" in e for e in errs) and any("tempoo" in e for e in errs)   # misspelt: said
    rf = ResponseFile(tmp_path)
    assert rf.cfg == Response() and not rf.reload_if_changed(force=True)       # absent: defaults
    p = tmp_path / RESPONSE_FILE
    p.write_text(_json.dumps({"section_follow": {"amount": 0.4}}))
    assert not rf.reload_if_changed() and rf.cfg.follow == 0.7                   # looked at once a second at most
    assert rf.reload_if_changed(force=True) and rf.cfg.follow == 0.4
    p.write_text("{not json")
    os.utime(p, ns=(1, 1))
    assert not rf.reload_if_changed(force=True) and rf.cfg.follow == 0.4 and rf.errors   # unreadable: the last good
    assert rf.describe()["latched"] == sorted(Response.latched)
    from stream_service.tests.test_sessions import REPO_CONFIG
    shipped = ResponseFile(REPO_CONFIG)
    assert shipped.errors == []                                                   # the shipped file parses clean
    assert "effect/isf_collage/cell_size" in shipped.cfg.latched_bindings


async def test_session_inputs_show_the_rooms_drive_and_cuts_stay_on_the_sure_clock(client):  # noqa: F811
    import time

    from .test_cue import AUTO, live
    from dataclasses import replace
    sid, s, eng = await live(client, auto={**AUTO, "snap": "beat"})
    mgr = s.mgr
    mgr.music_feed.response.cfg = replace(mgr.music_feed.response.cfg, onset_on=True)   # the live file ships it off
    now = time.monotonic()
    c = {**clock(now, bpm=112.0, gate="none"), "state": "holding", "conf": 0.35}
    t = now - 2.0
    while t < now - 0.02:                                  # 2 s of loud music, an onset each 0.5 s, an unsure clock
        on = 0.9 if abs((t - now) % 0.5) < 0.02 else 0.05
        mgr.music_feed.on_message({"type": "music.features", "data": {"energy": 0.8, "onset": on, "drive": 0.5,
                                                                        "punch": 0.6}}, t)
        mgr.music_feed.on_message({"type": "music.clock", "data": c}, t)
        t += 0.04
    mgr.music_feed.on_message({"type": "music.features", "data": {"energy": 0.8, "onset": 1.0}}, now)
    s.tick(now)
    i = s.inputs()
    d = i["derived"]
    assert d["beat_source"] == "onset" and d["beat"] > 0.5 and d["beat_locked"] is False
    assert d["bpm"] == 112.0 and d["bpm_source"] == "clock_tentative"
    assert i["cuts_on"] is None and i["clock"] is None and s._beat_grid(now) is None
    assert mgr.music_grid(now) is None and s._next_downbeat(now) is None
    assert i["feed"]["energy_fast"] > 0.75 and i["feed"]["tempo_bpm"] == 112.0 and i["feed"]["onset_age_ms"] < 100
    assert i["signals"]["energy"] > 0.75 and i["signals"]["energy_slow"] is not None
    assert abs(i["signals"]["tempo"] - (112.0 - 60.0) / 90.0) < 1e-3
    assert i["music_response"]["follow"] == 0.7 and i["music_response"]["errors"] == []
    s.runtime.begin_frame(now + 0.01)                      # the render thread: the same, and section mappings follow
    assert s.runtime.beat_source == "onset" and s.runtime.signals["beat"] > 0.5
    assert abs(s.runtime.follow - 0.7 * mgr.music_feed.liveness(now + 0.01)) < 1e-9 and s.runtime.follow > 0.0
    # a sure clock: its beat, and cuts on it
    mgr.music_feed.on_message({"type": "music.clock", "data": {**c, "gate": "beat", "state": "locked",
                                                               "conf": 0.9}}, now)
    i = s.inputs()
    assert i["derived"]["beat_source"] == "clock" and i["derived"]["bpm_source"] == "clock"
    assert i["cuts_on"] == "show_service clock" and i["derived"]["beat_locked"] is True
    assert i["feed"]["onset_n"] >= 4 and i["feed"]["onset_mean"] is not None
    # the features stop (the last at ``now``): the bindings' energy fades out with the feed's liveness over FADE_S,
    # frame by frame, as the loop (10 Hz tick) and the render thread (30 fps) build it: never a jump to idle
    rt, seen = s.runtime, []
    for k in range(1, 5 * 30):
        t = now + k / 30
        if k % 3 == 0:
            s.tick(t)
        rt.begin_frame(t)
        seen.append(rt.signals["energy"])
    assert seen[0] > 0.7 and seen[40] is not None and seen[40] < seen[0]          # fading (1.33 s in) ...
    vals = [v for v in seen if v is not None]
    assert max(abs(b - a) for a, b in zip(vals, vals[1:])) < 0.02, vals           # ... never a jump
    assert seen[-1] in (None, 0.5) and rt.extra["music_w"] is None                # ... then idle: the desk's
    assert mgr.music_feed.energy_now(now + 4.0) is None
