"""Show-bus inputs: the beat follower against show-synth songs, liveness, follow_dmx debouncing (no GPU)."""
from __future__ import annotations

import math
import random
import statistics

import pytest

from stream_service.inputs import (BIND_SIGNALS, BeatFollower, FixtureSignals, Sampler, ShowInputs, load_photon,
                                   signal_catalog)
from stream_service.tools.show_synth import Song

RATE_S = 0.025          # show-synth's 40 Hz cadence


def play(song: Song, seconds: float, fs: FixtureSignals, *, t0: float = 0.0, seed: int = 1, fixture="screen.north"):
    """Feed a song into a FixtureSignals on show-synth's jittered 40 Hz grid.  Yields (t, snapshot)."""
    rnd = random.Random(seed)
    n = 0
    while n * RATE_S < seconds:
        n += 1
        t = t0 + n * RATE_S + rnd.uniform(-0.003, 0.003)
        st = song.state(t)
        fs.update(t, t, st["fixtures"][fixture], st["activity"])
        yield t, fs.snapshot


def song(style="pop", bpm=None) -> Song:
    s = Song(style, bpm, 7)
    s.t0 = 0.0
    return s


@pytest.mark.parametrize("style,bpm", [("pop", 124), ("techno", None), ("pop", 90), ("jazz", None)])
def test_follower_locks_on_tempo_and_phase(style, bpm):
    sg = song(style, bpm)
    fs = FixtureSignals("screen.north")
    first_lock, errs, bpms = None, [], []
    for t, snap in play(sg, 60, fs):
        if snap.locked(t):
            first_lock = first_lock if first_lock is not None else t
            if t > 15:
                k = round(snap.anchor / sg.spb)
                errs.append(abs(snap.anchor - k * sg.spb))
                bpms.append(60 / snap.period)
    beats = 10 if style == "jazz" else 8                 # jazz has no hit on beat 3 of the bar
    assert first_lock is not None and first_lock <= beats * sg.spb + 0.5, first_lock
    assert abs(statistics.median(bpms) - sg.bpm) <= 1.0, statistics.median(bpms)
    assert fs.follower.confidence >= 0.8
    assert sorted(errs)[int(0.95 * len(errs))] <= 0.020                                # phase p95 <= 20 ms
    assert all(abs(b - sg.bpm) <= 2.5 for b in bpms)


def test_follower_unlocks_when_the_pulse_stops_and_ignores_random_edges():
    sg = song("pop", 124)
    fs = FixtureSignals("screen.north")
    t_end = 0.0
    for t_end, snap in play(sg, 20, fs):
        pass
    assert snap.locked(t_end)
    # the pulse stops: unlocked within max(2.5 P, 1.5 s)
    t = t_end
    while t < t_end + 3.0:
        t += RATE_S
        fs.update(t, t, {"pulse": 0.0, "level": 0.3}, 0.3)
    assert not fs.snapshot.locked(t)
    # 60 s of random edges never lock
    f = BeatFollower()
    rnd = random.Random(3)
    t, locked = 0.0, False
    while t < 60:
        t += RATE_S
        f.feed(t, 1.0 if rnd.random() < 0.08 else 0.0)
        locked |= f.locked(t)
    assert not locked


def test_follower_random_edges_rarely_lock():
    """R18: no lock in 60 s of random edges.  A few seeds did lock before the re-fit too (6 of these 120 runs);
    the re-fit (7 evenly spaced edges, at the tighter tolerance) must not add to them."""
    locks = 0
    for p in (0.08, 0.15):
        for seed in range(60):
            f, rnd, t = BeatFollower(), random.Random(seed), 0.0
            while t < 60 and not f.locked(t):
                t += RATE_S
                f.feed(t, 1.0 if rnd.random() < p else 0.0)
            locks += f.locked(t)
    assert locks <= 6, locks


@pytest.mark.parametrize("i", range(6))
def test_follower_does_not_lock_off_beat_when_beats_come_back_mid_beat(i):
    """A 5.4 s breakdown, then the pulses come back mid-beat (a part-decayed pulse: an off-beat edge).  The
    timeout cleared the hit history, so confidence reads 0 while unlocked and the off-beat edge cannot inherit the
    old lock; the follower locks only on the true grid, within 8 beats."""
    sg = song("pop", 110)
    fs = FixtureSignals("screen.north")
    off_at, on_at = 20.0 + 0.043 * i, 25.4 + 0.081 * i
    rnd = random.Random(i)
    n, t, relock = 0, 0.0, None
    while t < 40:
        n += 1
        t = n * RATE_S + rnd.uniform(-0.003, 0.003)
        sg.pulses = not off_at <= t < on_at                 # straight back, mid-beat (show-synth waits for the beat)
        st = sg.state(t)
        fs.update(t, t, st["fixtures"]["screen.north"], st["activity"])
        snap = fs.snapshot
        if off_at + 2.0 < t < on_at:
            assert not snap.locked(t) and snap.confidence == 0.0
        if t >= on_at and snap.locked(t):
            beat = sg.tempo_map.pos(snap.anchor)
            assert abs(beat - round(beat)) < 0.12 and abs(60 / snap.period - 110) < 2, (t - on_at, beat)
            relock = t if relock is None else relock
    assert relock is not None and (relock - on_at) / sg.spb <= 8, relock


def test_follower_survives_a_missed_pulse():
    sg = song("pop", 124)
    fs = FixtureSignals("screen.north")
    for t, snap in play(sg, 20, fs):
        pass
    period = snap.period
    # drop every 4th beat's pulse for 10 s: folding keeps P, confidence stays above unlock
    rnd = random.Random(2)
    n = int(t / RATE_S)
    end = t + 10
    while t < end:
        n += 1
        t = n * RATE_S + rnd.uniform(-0.003, 0.003)
        st = sg.state(t)
        fx = dict(st["fixtures"]["screen.north"])
        beat, frac = divmod((t - sg.t0) / sg.spb, 1)
        if int(beat) % 4 == 3:
            fx["pulse"] = 0.0
        fs.update(t, t, fx, st["activity"])
        assert fs.snapshot.locked(t)
    assert abs(fs.snapshot.period - period) < 0.01


def test_sampler_predicts_beats_ahead_and_off_mode_is_raw():
    sg = song("pop", 124)
    inputs = ShowInputs("ws://test")
    inputs.connected = True
    fs = inputs.fixture("screen.north")
    for t, snap in play(sg, 20, fs):
        pass
    lead = 0.3
    s = Sampler(inputs, "screen.north", lead)
    # sample at the true beat minus the lead (+10 ms): the predicted beat lands on glass at now + lead
    k = math.ceil((t + 0.1) / sg.spb)
    now = k * sg.spb - lead + 0.01
    for i in range(50, 0, -1):                # 2.5 s of render frames first: the liveness ease reaches 1
        s.sample(now - i * 0.05)
    sig = s.sample(now)
    assert sig["beat_locked"] and sig["beat"] > 0.9
    assert sig["beat_phase"] < 0.05 or sig["beat_phase"] > 0.97
    assert abs(sig["bpm"] - 124) < 1.5
    off = Sampler(inputs, "screen.north", lead, follow="off")
    raw = off.sample(now)
    assert raw["bpm"] is None and raw["beat"] < 0.9      # the raw edge: not phased to now + lead


def test_liveness_holds_then_eases_to_idle_and_recovers():
    sg = song("pop", 124)
    inputs = ShowInputs("ws://test")
    inputs.connected = True
    fs = inputs.fixture("screen.north")
    s = Sampler(inputs, "screen.north", 0.3)
    w = []
    for t, snap in play(sg, 4, fs):
        sig = s.sample(t)
        w.append(s.w)
    assert w[0] == 0.0 and s.w == 1.0                    # 1 s of fresh frames, then a 1 s ease in
    assert s.stale is None
    level = sig["level"]
    # the bus goes quiet: hold for 250 ms, then ease to idle (levels 0.5, triggers 0) over 2 s
    t_stop = t
    sig = s.sample(t_stop + 0.6)                          # link down (> 500 ms without a frame)
    assert s.stale == "link"
    t2 = t_stop + 0.6
    while t2 < t_stop + 3.5:
        t2 += 0.05
        sig = s.sample(t2)
    assert s.w == 0.0 and sig["level"] == pytest.approx(0.5) and sig["pulse"] == 0.0 and sig["beat"] == 0.0
    assert level != pytest.approx(0.5)


def test_input_not_live_counts_as_stale():
    inputs = ShowInputs("ws://test")
    inputs.connected = True
    inputs.on_frame({"type": "show.state", "t_ms": 1000.0, "feed": "test", "input": {"online": True},
                     "global": {"activity": 0.5}, "fixtures": {"screen.north": {"pulse": 0.2, "level": 0.4}}},
                    arrival=1.0)
    s = Sampler(inputs, "screen.north", 0.3)
    s.sample(1.01)
    assert s.stale == "input"


def test_follow_dmx_needs_300ms_of_stable_value():
    fs = FixtureSignals("screen.north")
    t = 0.0
    for _ in range(10):
        t += RATE_S
        fs.update(t, t, {"effect": 1, "variant": 0}, 0.5)
    assert fs.snapshot.stable == {"effect": 1, "variant": 0}
    # a 100 ms blip to effect 2 is not a change
    for _ in range(4):
        t += RATE_S
        fs.update(t, t, {"effect": 2, "variant": 0}, 0.5)
    for _ in range(4):
        t += RATE_S
        fs.update(t, t, {"effect": 1, "variant": 0}, 0.5)
    assert fs.snapshot.stable["effect"] == 1
    # held for 300 ms it is
    for _ in range(14):
        t += RATE_S
        fs.update(t, t, {"effect": 3, "variant": 0}, 0.5)
    assert fs.snapshot.stable["effect"] == 3


def test_signal_catalog_and_photon_lead(tmp_path):
    cat = signal_catalog()
    assert set(cat) == set(BIND_SIGNALS) and "energy" not in cat and "activity_slow" in cat
    assert cat["beat"]["idle"] == 0.0 and cat["level"]["idle"] == 0.5 and "rgb" not in cat
    ms, src = load_photon(tmp_path / "missing.json", "north-screen")
    assert ms > 0 and src == "default"
    f = tmp_path / "latency.json"
    f.write_text('{"screens": {"south": {"effective": {"presented_to_photon_ms": 150.0}}}}')
    assert load_photon(f, "south-screen") == (150.0, "latency.json:south")
    ms, src = load_photon(f, "north-screen")
    assert ms == 150.0 and "no north" in src
