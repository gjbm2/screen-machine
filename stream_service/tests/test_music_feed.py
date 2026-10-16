"""show_service's music as the renderer's signals (music_feed.py; d4's G1 / G2): features smoothing and liveness,
the clock's beat / bar / phrase signals at photon time, the epoch guard, the blend, and MusicDirection's feed path
(no GPU)."""
from __future__ import annotations

import math

from stream_service import music_direction as M
from stream_service.director import AUTO_MUSIC_DEFAULTS
from stream_service.music_feed import ClockSignals, FRESH_S, MusicFeed, blend, clock_signals


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
