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
