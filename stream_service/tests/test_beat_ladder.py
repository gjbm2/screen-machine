"""The predicted beat ladder (music_feed.BeatLadder): the bindings' beat from one source at the frame's photon time
(clock, desk, dmx phase, onset follower, nothing), x "music present"; inputs.BeatFollower.edge; cuts stay on the
sure clock (no GPU)."""
from __future__ import annotations

import dataclasses
import math
import random
import types

from stream_service import effects
from stream_service.inputs import BeatFollower
from stream_service.modulation import SessionRuntime
from stream_service.music_feed import BEAT_DECAY_S, PRESENT_STALE_S, MusicFeed, Response, blend, parse_response

from .test_sessions import client  # noqa: F401 (fixture)

LEAD = 0.7
P = 0.5                                                   # 120 bpm
LAG = 0.0107                                              # dsp's flux peak after a transient (FAST_N / 4 / 48 kHz)


class Desk:
    """The session's sampler: a desk follower that is locked (extrapolating) or not."""

    def __init__(self, beat=0.0, locked=False, follow="extrapolate"):
        self.w, self.lead, self.follow = 1.0, LEAD, follow
        self.beat, self.locked = beat, locked

    def sample(self, now):
        return {"beat": self.beat, "beat_locked": self.locked, "beat_phase": 0.0}

    def beat_grid(self, now):
        return None


def clock(anchor, bpm=120.0, gate="none", stability=0.8, epoch=1, state="holding", conf=0.3):
    return {"bpm": bpm, "anchor_ms": anchor * 1000.0, "beat_at_anchor": 0, "beats_per_bar": 4, "bar_anchor_beat": 0,
            "phrase_bars": 4, "conf": conf, "gate": gate, "state": state, "epoch": epoch, "stability": stability}


def play(f, t0, secs, beats=(), energy=0.6, clk=None, age=0.05, rate=25.0, arrive=None):
    """show_service at 25 Hz: features published on a 40 ms grid (t_ms, acoustic: the latest hop's end) arriving
    ``age`` later (or at ``arrive(k, th)``, kept in order: one socket), onset 0.9 in the window holding a true onset's
    flux peak (from ``beats``, LAG after it, as dsp's), else 0.05; the clock at 10 Hz (``clk(t)`` -> dict).  -> the
    last arrival time."""
    beats = sorted(b + LAG for b in beats)
    n = int(round(secs * rate))
    i = 0
    arr = last = t0
    for k in range(1, n + 1):
        th = t0 + k / rate
        hit = False
        while i < len(beats) and beats[i] <= th:
            hit |= beats[i] > th - 1.0 / rate
            i += 1
        arr = last = max(last, th + age if arrive is None else arrive(k, th))
        f.on_message({"type": "music.features", "data": {"t_ms": th * 1000.0, "energy": energy,
                                                         "onset": 0.9 if hit else 0.05}}, arr)
        if clk is not None and k % 4 == 0:
            f.on_message({"type": "music.clock", "data": clk(th)}, arr)
    return arr


def runtime(f, desk=None):
    rt = SessionRuntime(desk or Desk())
    rt.feed = f
    return rt


def test_rung_a_the_sure_clock():
    f = MusicFeed()
    t = play(f, 10.0, 2.0, clk=lambda _t: clock(10.0, gate="beat", state="locked", conf=0.9))
    rt = runtime(f, Desk(beat=0.4, locked=True))
    rt.begin_frame(12.1)                                  # photon time 12.8: 0.3 s after beat 5
    assert rt.beat_source == "clock" and t < 12.1
    assert abs(rt.signals["beat"] - math.exp(-0.3 / BEAT_DECAY_S)) < 1e-9       # music present: full depth
    assert rt.signals["beat_locked"] is True                                    # the desk's, untouched


def test_rung_b_a_locked_desk_is_never_overridden():
    f = MusicFeed()
    play(f, 10.0, 2.0, clk=lambda _t: clock(10.0, gate="dmx", conf=0.5))
    rt = runtime(f, Desk(beat=0.37, locked=True))
    rt.begin_frame(12.0)
    assert rt.beat_source == "desk" and abs(rt.signals["beat"] - 0.37) < 1e-9   # not the dmx phase's
    rt2 = runtime(f, Desk(beat=0.37, locked=True, follow="off"))                # locked, not extrapolating:
    rt2.begin_frame(12.0)                                                       # the dmx rung
    assert rt2.beat_source == "dmx"
    # the old fallback never overwrites it either (blend's guard), and the ladder wins over it when both are on
    sig = {"beat": 0.37, "beat_locked": True}
    assert blend(sig, f.snapshot(), 1.0, {"beat_phase": 0.2}, None, 1.0) is None and sig["beat"] == 0.37
    f.response.cfg = dataclasses.replace(f.response.cfg, onset_on=True)
    assert f.onset_pulse(12.0) is None
    f.response.cfg = dataclasses.replace(f.response.cfg, ladder_on=False)
    assert f.onset_pulse(12.0) is not None


def test_rung_c_the_dmx_gate_phase_at_its_depth():
    f = MusicFeed()
    play(f, 10.0, 2.0, clk=lambda _t: clock(10.0, gate="dmx", conf=0.5))
    rt = runtime(f, Desk(beat=0.8))                       # an unlocked desk: its raw edge (late) is replaced
    rt.begin_frame(12.1)                                  # photon 12.8: 0.3 s after beat 5
    assert rt.beat_source == "dmx" and abs(rt.signals["beat"] - 0.5 * math.exp(-0.3 / BEAT_DECAY_S)) < 1e-9
    f.response.cfg = dataclasses.replace(f.response.cfg, dmx_depth=0.3)
    rt.begin_frame(12.2)                                  # photon 12.9: 0.4 s after it
    assert abs(rt.signals["beat"] - 0.3 * math.exp(-0.4 / BEAT_DECAY_S)) < 1e-9
    # an epoch jump: silent until the next whole beat (no flash on a re-anchor)
    f.on_message({"type": "music.clock", "data": clock(12.96, gate="dmx", conf=0.5, epoch=2)}, 12.22)
    play(f, 12.0, 0.2)
    rt.begin_frame(12.27)                                 # photon 12.97: just after the new beat 0
    assert rt.beat_source == "dmx" and rt.signals["beat"] == 0.0
    play(f, 12.2, 0.6, clk=lambda _t: clock(12.96, gate="dmx", conf=0.5, epoch=2))
    rt.begin_frame(12.27 + P)                             # the next beat flashes
    assert rt.signals["beat"] > 0.25


def onsets_120(t0, secs, rnd, every=1, jitter=0.015, at=0.237):
    """True beats at t0 + at + k P, the onsets on them +- jitter (off the features' 40 ms grid)."""
    return [t0 + at + k * P + rnd.uniform(-jitter, jitter) for k in range(0, int(secs / P), every)]


def test_rung_d_the_onset_follower_locks_and_lands_on_the_beat_at_photon_time():
    """Strong onsets on a 120 bpm beat (+-15 ms jitter, the 40 ms features grid), a clear tempo but no phase (gate
    none): the follower locks and its grid, extrapolated a lead ahead, lands within 30 ms of the true beat."""
    rnd = random.Random(7)
    f = MusicFeed()
    t0 = 100.0
    true = lambda t: t0 + 0.237 + round((t - t0 - 0.237) / P) * P            # noqa: E731
    rt = runtime(f, Desk(beat=0.6))
    beats = onsets_120(t0, 40.0, rnd)
    clk = lambda _t: clock(t0, gate="none")              # noqa: E731
    # before it locks: no pulse (the desk's raw edge, a lead late, is not shown either)
    t = play(f, t0, 1.6, beats, clk=clk)
    rt.begin_frame(t)
    assert f.onset_grid(t) is None and rt.beat_source is None and rt.signals["beat"] == 0.0
    t = play(f, t0 + 1.6, 6.4, [b for b in beats if b > t0 + 1.6], clk=clk)
    assert f.onset_grid(t) is not None and f.tempo_clear(t)
    worst, tc = 0.0, t0 + 8.0
    for _ in range(20):                                   # 4 s more: the onsets keep coming, frames at 30 fps
        t = play(f, tc, 0.2, [b for b in beats if tc < b <= tc + 0.2], clk=clk)
        for k in range(6):
            now = t + k / 30
            rt.begin_frame(now)
            g = f.onset_grid(now)
            tp = now + LEAD
            pred = g.anchor + round((tp - g.anchor) / g.period) * g.period
            worst = max(worst, abs(pred - true(pred)))
            assert rt.beat_source == "onset_follower" and abs(g.period - P) < 0.01
            assert abs(rt.signals["beat"] - 0.6 * math.exp(-((tp - g.anchor) % g.period) / BEAT_DECAY_S)) < 1e-9
        tc += 0.2
    assert worst < 0.03, worst                            # on glass within 30 ms of the true beat
    out = {}
    rt.inputs_view(out, t)
    ld = out["ladder"]
    assert out["derived"]["beat_source"] == "onset_follower" and ld["rung"] == "onset_follower"
    assert ld["onset_follower"]["locked"] and abs(ld["onset_follower"]["period_ms"] - 500.0) < 5
    assert ld["tempo_clear"] and ld["clear_bpm"] == 120.0 and ld["pulses_per_min"] >= 3


def test_rung_d_folds_the_follower_to_the_clocks_bpm():
    """Onsets only every other beat (kick on 1 and 3) and a half-time clock (60 bpm, folded to 120): the follower
    runs on the clock's beat, so the screens pulse every beat."""
    rnd = random.Random(3)
    f = MusicFeed()
    t0 = 200.0
    t = play(f, t0, 12.0, onsets_120(t0, 12.0, rnd, every=2), clk=lambda _t: clock(t0, bpm=60.0, gate="none"))
    g = f.onset_grid(t)
    assert f.snapshot().clear_bpm == 120.0 and g is not None and abs(g.period - P) < 0.005


def photon_error(seed, arrive, true_p=P, clock_bpm=120.0):
    """Rung d's worst error (s) at photon time against the true beat over 20 s after 8 s of play, and the share of
    frames it was on; onsets +-15 ms on a ``true_p`` beat, delivered by ``arrive``."""
    rnd = random.Random(seed)
    f = MusicFeed()
    t0 = 100.0
    true = lambda t: t0 + 0.237 + round((t - t0 - 0.237) / true_p) * true_p    # noqa: E731
    beats = [t0 + 0.237 + k * true_p + rnd.uniform(-0.015, 0.015) for k in range(int(30.0 / true_p))]
    clk = lambda _t: clock(t0, bpm=clock_bpm, gate="none")                     # noqa: E731
    rt = runtime(f, Desk(beat=0.6))
    play(f, t0, 8.0, [b for b in beats if b <= t0 + 8.0], clk=clk, arrive=arrive)
    worst, on, frames, tc = 0.0, 0, 0, t0 + 8.0
    for _ in range(100):
        t = play(f, tc, 0.2, [b for b in beats if tc < b <= tc + 0.2], clk=clk, arrive=arrive)
        for k in range(6):
            now = t + k / 30
            rt.begin_frame(now)
            frames += 1
            if rt.beat_source != "onset_follower":
                continue
            g = f.onset_grid(now)
            tp = now + LEAD
            pred = g.anchor + round((tp - g.anchor) / g.period) * g.period
            worst, on = max(worst, abs(pred - true(pred))), on + 1
        tc += 0.2
    return worst, on / frames


def test_rung_d_lands_on_the_beat_whatever_the_delivery():
    """The onset's time is its features' t_ms less half their own t_ms step and dsp's flux lag, never the arrival
    step; the period is the clock's: steady, jittery or batched delivery on the loop all land within 30 ms."""
    rnd = random.Random(99)
    modes = {"steady": lambda k, th: th + 0.05,
             "jitter 20-60 ms": lambda k, th: th + 0.02 + rnd.uniform(0.0, 0.04),
             "pairs": lambda k, th: th + 0.05 + (0.04 if k % 2 else 0.0),
             "threes": lambda k, th: th + 0.05 + 0.04 * (2 - k % 3)}
    for name, arrive in modes.items():
        for seed in range(3):
            worst, share = photon_error(seed, arrive)
            assert worst < 0.03 and share > 0.95, (name, seed, worst, share)
    # the clock's bpm a little off the music (1.5 %): the trim takes up the difference
    worst, share = photon_error(5, modes["jitter 20-60 ms"], true_p=60.0 / 121.8)
    assert worst < 0.03 and share > 0.9, (worst, share)


def test_rung_d_edge_time_and_pacing():
    f = MusicFeed()
    edges = []
    f.follower.edge = lambda t, strength=1.0, hint=None: edges.append(t) or True      # noqa: E731
    ft = lambda th, on, arr: f.on_message({"type": "music.features", "data": {         # noqa: E731
        "t_ms": th * 1000.0, "energy": 0.6, "onset": on}}, arr)
    ft(50.00, 0.05, 50.30)
    ft(50.04, 0.05, 50.30)                                # a burst: the arrival step is 0, the t_ms step 40 ms
    ft(50.08, 0.9, 50.30)
    assert abs(edges[-1] - (50.08 - 0.02 - 0.0107)) < 1e-9
    f.on_message({"type": "music.features", "data": {"energy": 0.6, "onset": 0.05}}, 50.34)   # no t_ms: arrival
    f.on_message({"type": "music.features", "data": {"energy": 0.6, "onset": 0.9}}, 50.38)
    assert abs(edges[-1] - (50.38 - 0.02 - 0.0107)) < 1e-9
    # every strong rising onset reaches the follower (its own refractory paces it), not the old pulse's min_gap_s
    g = MusicFeed()
    play(g, 60.0, 4.0, [60.1 + 0.25 * k for k in range(16)])                    # 8ths, all strong
    assert g.follower.accepted + g.follower.rejected == 16 and g.snapshot().onset_n <= 9


def test_rung_d_only_on_a_grid_held_to_this_clock():
    """A grid the follower locked while the tempo was unclear (no hint) is not used the moment the tempo turns clear:
    only once an edge has held it to the clock's period."""
    rnd = random.Random(21)
    f = MusicFeed()
    t0 = 500.0
    beats = onsets_120(t0, 20.0, rnd)
    t = play(f, t0, 8.0, [b for b in beats if b <= t0 + 8.0], clk=lambda _t: clock(t0, stability=0.4))
    g = f.snapshot().grid
    assert g.locked(t) and g.hint is None and not f.tempo_clear(t)
    f.on_message({"type": "music.clock", "data": clock(t0)}, t + 0.001)          # the tempo turns clear
    rt = runtime(f)
    rt.begin_frame(t + 0.002)
    assert f.tempo_clear(t + 0.002) and rt.beat_source is None                   # its grid is not the clock's yet
    nxt = min(b for b in beats if b > t + 0.05)
    t = play(f, t + 0.002, nxt - t + 0.1, [nxt], clk=lambda _t: clock(t0))       # one edge, held to the clock
    rt.begin_frame(t)
    assert f.snapshot().grid.hint == P and rt.beat_source == "onset_follower"


def test_presence_lets_go_when_features_stop():
    f = MusicFeed()
    t = play(f, 10.0, 2.0, energy=0.6)
    assert f.presence(t + PRESENT_STALE_S) == 1.0                             # a late message: still music
    assert f.presence(t + PRESENT_STALE_S + 0.3) < 0.05                       # show_service down: let go


def test_pulse_count_is_relative_to_the_rungs_depth():
    f = MusicFeed()
    f.response.cfg = dataclasses.replace(f.response.cfg, dmx_depth=0.15)
    dmx = lambda _t: clock(10.0, gate="dmx", conf=0.5)                          # noqa: E731
    rt = runtime(f)
    tc = 10.0
    for _ in range(40):                                   # 20 s at 30 fps on the dmx rung, pulses 0.15 deep
        t = play(f, tc, 0.5, clk=dmx)
        for k in range(15):
            rt.begin_frame(t + k / 30)
        tc += 0.5
    assert rt.beat_source == "dmx" and 38 <= rt.ladder.pulses_per_min <= 42, rt.ladder.pulses_per_min


def test_the_feed_gone_leaves_the_desks_beat_and_its_source():
    f = MusicFeed()
    t = play(f, 10.0, 2.0, clk=lambda _t: clock(10.0, gate="dmx", conf=0.5))
    rt = runtime(f, Desk(beat=0.3))
    rt.begin_frame(t)
    assert rt.ladder.active and rt.beat_source == "dmx"
    rt.begin_frame(t + 10.0)                              # features stopped long ago: liveness 0
    out = {"derived": {"beat_source": "desk", "beat": 0.3}}
    rt.inputs_view(out, t + 10.0)
    assert not rt.ladder.active and rt.signals["beat"] == 0.3
    assert out["derived"] == {"beat_source": "desk", "beat": 0.3} and out["ladder"]["running"] is False


def test_rung_e_no_pulse_while_the_tempo_is_unclear():
    rnd = random.Random(5)
    for clk in (lambda _t: clock(300.0, stability=0.4),                          # unclear: low stability
                lambda _t: clock(300.0, bpm=120.0 * (1.05 if int(_t) % 2 else 1.0)),   # wandering 5 %
                None):                                                           # no clock at all
        f = MusicFeed()
        t = play(f, 300.0, 10.0, onsets_120(300.0, 10.0, rnd), clk=clk)
        assert f.snapshot().grid is not None and not f.tempo_clear(t)
        rt = runtime(f, Desk(beat=0.9))
        rt.begin_frame(t)
        assert rt.beat_source is None and rt.signals["beat"] == 0.0
    # the feed gone (liveness 0): the desk's own beat again, untouched
    rt.begin_frame(t + 10.0)
    assert f.liveness(t + 10.0) == 0.0 and rt.signals["beat"] == 0.9


def test_pulses_stop_within_a_fifth_of_a_second_of_silence():
    f = MusicFeed()
    sure = lambda _t: clock(10.0, gate="beat", state="locked", conf=0.9)      # noqa: E731
    rt = runtime(f)
    loud, after, t = [], [], 10.0
    for k in range(1, 8 * 25):                            # 4 s of music then silence, a frame after each message
        energy = 0.5 if k <= 100 else 0.0
        th = 10.0 + k / 25
        t = th + 0.05
        f.on_message({"type": "music.features", "data": {"t_ms": th * 1000.0, "energy": energy, "onset": 0.05}}, t)
        if k % 4 == 0:
            f.on_message({"type": "music.clock", "data": sure(th)}, t)
        if k == 103:                                      # the music stopped at the arrival of message 101
            assert abs(f.presence(t + 0.02) - math.exp(-1.0)) < 1e-9 and f.presence(t + 0.12) < 0.15
        for j in range(2):                                # 50 fps: two frames per message
            now = t + j * 0.02
            rt.begin_frame(now)
            (loud if k <= 100 else after if now >= 10.0 + 101 / 25 + 0.05 + 0.2 else []).append(rt.signals["beat"])
    assert max(loud) > 0.8 and rt.ladder.rung == "clock"
    assert after and max(after) < 0.15, max(after)
    # quiet music is still music: energy 0.05 (over present_at 0.03) keeps the pulses whole
    q = MusicFeed()
    tq = play(q, 10.0, 3.0, energy=0.05, clk=sure)
    assert q.presence(tq) == 1.0


def test_pulse_rate_and_cuts_stay_on_the_sure_clock():
    from stream_service.sessions import SessionManager
    rnd = random.Random(11)
    f = MusicFeed()
    t0 = 400.0
    beats = onsets_120(t0, 40.0, rnd)
    clk = lambda _t: clock(t0, gate="none")              # noqa: E731
    play(f, t0, 10.0, beats, clk=clk)
    rt = runtime(f)
    tc = t0 + 10.0
    for _ in range(40):                                   # 20 s more, frames at 30 fps on the onset follower
        t = play(f, tc, 0.5, [b for b in beats if tc < b <= tc + 0.5], clk=clk)
        for k in range(15):
            now = t + k / 30
            rt.begin_frame(now)
        tc += 0.5
    assert rt.beat_source == "onset_follower" and 38 <= rt.ladder.pulses_per_min <= 42, rt.ladder.pulses_per_min
    # cuts, beat_locked, effects' tempo lock: the sure clock only, never the ladder
    mgr = types.SimpleNamespace(music_feed=f)
    assert SessionManager.music_grid(mgr, now) is None and effects.beat_clock(rt) is None
    assert rt.signals["beat_locked"] is False
    f.on_message({"type": "music.clock", "data": clock(t0, gate="beat", state="locked", conf=0.9)}, now)
    assert SessionManager.music_grid(mgr, now) == (t0, P)
    rt.begin_frame(now + 0.01)
    assert rt.beat_source == "clock" and effects.beat_clock(rt).source == "show"


def test_ladder_config_block():
    c, errs = parse_response({"ladder": {"on": False, "dmx_depth": 0.4, "onset_depth": 2, "steady_pct": 5,
                                         "release_s": 0.2, "present_at": 0.05, "onset_lag_ms": 12, "depht": 1}})
    assert c.ladder_on is False and c.dmx_depth == 0.4 and c.onset_depth == Response.onset_depth
    assert c.steady_pct == 5.0 and c.presence_release_s == 0.2 and c.present_at == 0.05 and c.onset_lag_ms == 12.0
    assert len(errs) == 2 and any("depht" in e for e in errs) and any("onset_depth" in e for e in errs)
    d = Response()
    assert d.ladder_on and (d.dmx_depth, d.onset_depth, d.stability_min, d.steady_pct, d.steady_s, d.onset_lag_ms,
                            d.present_at, d.presence_release_s) == (0.5, 0.6, 0.6, 3.0, 4.0, 10.7, 0.03, 0.1)


def test_follower_edge_skips_the_schmitt_stage_and_holds_to_the_hint():
    f = BeatFollower()
    assert f.edge(1.0, 0.05) and f.strength == 0.05 and f.accepted == 1          # a weak edge: still an edge
    assert not f.edge(1.1, 1.0) and f.rejected == 1                              # the refractory period
    assert not f.edge(0.9, 1.0)                                                  # out of order
    # every other beat, hint P: the period is the hint's, locked
    f = BeatFollower()
    for k in range(9):
        f.edge(10.0 + 2 * k * P, 1.0, P)
    assert f.locked(10.0 + 16 * P) and abs(f.period - P) < 1e-9
    # the hint 1 % off the music: the residuals trim it (alpha-beta) onto the music's period, locked all along
    f = BeatFollower()
    for k in range(40):
        f.edge(10.0 + k * 0.505, 1.0, P)
        assert k < 6 or f.locked(10.0 + k * 0.505)
    assert abs(f.period - 0.505) < 0.0005 and abs(f.trim_c - 0.01) < 0.001
    pred = f.anchor + round((10.0 + 41 * 0.505 - f.anchor) / f.period) * f.period
    assert abs(pred - (10.0 + 41 * 0.505)) < 0.005                               # two beats ahead, on the beat
    # 24 % off: never further than the trim (3 %), never locked
    f = BeatFollower()
    for k in range(20):
        f.edge(10.0 + k * 0.62, 1.0, P)
    assert abs(f.period / P - 1.0) <= 0.03 + 1e-9 and not f.locked(10.0 + 19 * 0.62)
    g = BeatFollower()
    assert g.edge(1.0, 1.0, 5.0) and g.hint is None and g.period is None        # a hint out of range: none
    # a grid locked with no hint on another period (a dotted 0.75 s): the hint drops it, earned again on the hint's
    f = BeatFollower()
    for k in range(10):
        f.edge(30.0 + k * 0.75, 1.0)
    assert f.locked(30.0 + 9 * 0.75) and abs(f.period - 0.75) < 1e-6
    f.edge(30.0 + 10 * 0.75, 1.0, P)
    assert not f.is_locked and f.period == P and len(f.hits) == 1
    # ... while one on the hint's period (within tolerance) keeps its lock
    f = BeatFollower()
    for k in range(10):
        f.edge(40.0 + k * 0.51, 1.0)
    f.edge(40.0 + 10 * 0.51, 1.0, P)
    assert f.locked(40.0 + 10 * 0.51)


def test_follower_edge_refits_in_hint_beats_and_relocks_after_a_phase_jump():
    f = BeatFollower()
    f.hint = P                                            # 7 edges two hint beats apart: a grid on the hint
    f.recent.extend(20.0 + 2 * k * P + (0.004 if k % 2 else -0.004) for k in range(7))
    assert f._refit() and abs(f.period - P) < 0.002 and all(f.hits) and f.refits == 1
    assert abs((f.anchor - 20.0) / P - round((f.anchor - 20.0) / P)) * P < 0.005
    f = BeatFollower()
    f.hint = P                                            # evenly spaced, but 24 % off the hint: no
    f.recent.extend(20.0 + k * 0.62 for k in range(7))
    assert not f._refit()
    f = BeatFollower()
    t = 10.0
    for _ in range(10):
        f.edge(t, 1.0, P)
        t += P
    assert f.locked(t - P)
    t += 0.2                                              # the beat moves 200 ms: unlocked, re-anchored, relocked
    unlocked = relock = None
    for k in range(14):
        f.edge(t, 1.0, P)
        if unlocked is None and not f.locked(t):
            unlocked = k
        elif unlocked is not None and relock is None and f.locked(t):
            relock = k
        t += P
    assert unlocked is not None and unlocked <= 4 and relock is not None and relock <= 11, (unlocked, relock)
    ph = (f.anchor - (10.2 + 10 * P)) / P
    assert abs(ph - round(ph)) * P < 0.03


async def test_session_inputs_show_the_rung_and_the_pivots(client):  # noqa: F811
    import time

    from .test_cue import AUTO, live
    sid, s, eng = await live(client, auto=AUTO)
    f = s.mgr.music_feed
    now = time.monotonic()
    t = play(f, now - 2.0, 1.9, clk=lambda _t: clock(now, gate="dmx", conf=0.5))
    s.tick(t)
    s.runtime.begin_frame(t + 0.01)
    i = s.inputs()
    assert i["derived"]["beat_source"] == "dmx" and i["ladder"]["rung"] == "dmx" and i["ladder"]["on"] is True
    assert i["cuts_on"] is None and i["clock"] is None and s._beat_grid(t) is None
    assert i["ladder"]["onset_follower"] is None
    assert i["pivot"]["on"] is True and set(i["pivot"]["signals"]) >= {"energy", "drive", "motion"}
    assert all(not v["applied"] for v in i["pivot"]["signals"].values())        # not a minute of samples yet
    sig = s._cue_signals()                                                       # (the cue mappings' view)
    assert "drive" in sig
