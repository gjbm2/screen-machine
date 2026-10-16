"""Clock mapping (show_service/timeline.py): lower envelope, skew, alarms, segments (docs/SHOW_ENGINE.md §3.3, §3.5).

Simulated senders: sample n is captured at host time T0 + n/fs * (1 + skew); its packet (the one ending at n) arrives
L + jitter later, jitter >= 0. The map must land on T0 + n/fs * (1 + skew) + L (capture + the smallest transport
delay, §3.3) whatever the jitter.
"""

from __future__ import annotations

import math
import random

import pytest

from show_service.timeline import (CaptureMap, LowerEnvelope, RemoteClock, RemoteTimeMap, SampleClock,
                                   SKEW_MAX_PPM)

FS = 48000
FRAMES = 128
T0 = 5000.0
L = 0.004


def jitter_fn(seed: int, mean_s: float = 0.003, spike_p: float = 0.03, spike_s: float = 0.060):
    rng = random.Random(seed)

    def j() -> float:
        x = rng.expovariate(1.0 / mean_s)
        if rng.random() < spike_p:                       # powerline-ish spikes
            x += rng.uniform(0.0, spike_s)
        return x
    return j


def feed(clock: SampleClock, seconds: float, *, skew: float = 0.0, lat: float = L, t0: float = T0, n_start: int = 0,
         jit=None, cap_n0: int = 0) -> int:
    """Packets for ``seconds`` from index n_start; returns the next index. Capture of n at t0 + (n - cap_n0)/fs*(1+skew)."""
    jit = jit or jitter_fn(1)
    n = n_start
    end = n_start + int(seconds * FS)
    while n < end:
        arrival = t0 + (n + FRAMES - cap_n0) / FS * (1.0 + skew) + lat + jit()
        clock.observe(n, arrival, FRAMES)
        n += FRAMES
    return n


def truth(n: float, *, skew: float = 0.0, lat: float = L, t0: float = T0, cap_n0: int = 0) -> float:
    return t0 + (n - cap_n0) / FS * (1.0 + skew) + lat


def max_err_ms(clock: SampleClock, n_lo: int, n_hi: int, **kw) -> float:
    step = max(1, (n_hi - n_lo) // 200)
    return max(abs(clock.to_host(n) - truth(n, **kw)) for n in range(n_lo, n_hi, step)) * 1000.0


def test_nothing_before_the_first_packet():
    c = SampleClock(FS)
    assert not c.ready
    assert math.isnan(c.to_host(0)) and math.isnan(c.to_n(1.0))
    assert c.state()["ready"] is False


def test_constant_offset_with_jitter():
    c = SampleClock(FS)
    n = feed(c, 30.0, jit=jitter_fn(2))
    assert c.ready
    assert max_err_ms(c, n - 20 * FS, n) < 0.5
    assert abs(c.skew_ppm) < 10.0
    assert not c.skew_alarm
    assert 0.5 < c.jitter_ms < 20.0                       # RFC 3550 estimate sees the jitter
    st = c.state()
    assert st["segment"] == 0 and st["steps"] == 0 and st["fitted"]


def test_first_seconds_already_usable():
    c = SampleClock(FS)
    n = feed(c, 2.0, jit=jitter_fn(3))
    assert max_err_ms(c, 0, n) < 1.0                       # prior slope 0, running minimum so far


@pytest.mark.parametrize("ppm", [100.0, -100.0, 37.0])
def test_skew_tracked(ppm):
    skew = ppm * 1e-6
    c = SampleClock(FS)
    n = feed(c, 90.0, skew=skew, jit=jitter_fn(4))
    assert abs(c.skew_ppm - ppm) < 3.0
    assert not c.skew_alarm
    assert max_err_ms(c, n - 60 * FS, n, skew=skew) < 0.5
    # extrapolating a second ahead stays on it too
    assert abs(c.to_host(n + FS) - truth(n + FS, skew=skew)) * 1000.0 < 0.5


@pytest.mark.parametrize("ppm", [500.0, -450.0])
def test_skew_beyond_limit_is_clamped_and_flagged(ppm):
    c = SampleClock(FS)
    feed(c, 60.0, skew=ppm * 1e-6, jit=jitter_fn(5))
    assert c.skew_alarm
    assert c.skew_ppm == pytest.approx(math.copysign(SKEW_MAX_PPM, ppm))
    assert c.state()["skew_raw_ppm"] == pytest.approx(ppm, abs=5.0)


def test_no_alarm_from_a_short_noisy_start():
    env = LowerEnvelope()
    # 10 s of a big apparent slope: fitted and clamped, but too little data to raise the alarm
    for i in range(10 * 375):
        x = i * FRAMES / FS
        env.add(x, 800e-6 * x)
    assert env.fitted and env.b == pytest.approx(SKEW_MAX_PPM * 1e-6) and not env.alarm


def test_acoustic_offset_and_inverse():
    c = SampleClock(FS, acoustic_offset_ms=12.5)
    n = feed(c, 10.0, jit=jitter_fn(6))
    assert c.to_host(n) - c.to_host(n, offset=False) == pytest.approx(0.0125)
    assert abs(c.to_host(n, offset=False) - truth(n)) * 1000.0 < 0.5
    t = c.to_host(n - 1000)
    assert c.to_n(t) == pytest.approx(n - 1000, abs=1e-3)
    c.acoustic_offset_ms = -3.0                            # hot reload
    assert c.to_host(n) - c.to_host(n, offset=False) == pytest.approx(-0.003)


def test_segment_reanchors():
    c = SampleClock(FS)
    feed(c, 10.0, jit=jitter_fn(7))
    seg = c.segment("sess")
    assert seg == 1 and c.seg == 1 and c.reason == "sess" and not c.ready
    # the new session counts from 0 again, on a different capture origin and path
    t1 = T0 + 30.0
    n = feed(c, 1.0, t0=t1, lat=0.009, jit=jitter_fn(8))
    assert max_err_ms(c, 0, n, t0=t1, lat=0.009) < 0.5
    assert c.state()["points"] <= 2                         # nothing left of the old segment


def test_segment_keep_skew_and_rate_change():
    c = SampleClock(FS)
    feed(c, 40.0, skew=80e-6, jit=jitter_fn(9))
    c.segment("sess", keep_skew=True)
    assert c.skew_ppm == pytest.approx(80.0, abs=3.0)       # the prior until the new fit
    n = feed(c, 3.0, skew=80e-6, t0=T0 + 100.0, jit=jitter_fn(10))
    assert max_err_ms(c, 0, n, skew=80e-6, t0=T0 + 100.0) < 0.5
    c.segment("device", fs=44100)
    assert c.fs == 44100 and c.skew_ppm == 0.0


def test_samples_skipped_reanchors_on_a_step():
    c = SampleClock(FS)
    n = feed(c, 10.0, jit=jitter_fn(11))
    # the sender's index jumps 1 s ahead without time passing: those samples look 1 s early
    jump = FS
    nxt = feed(c, 5.0, n_start=n + jump, cap_n0=jump, jit=jitter_fn(12))
    assert c.seg == 1 and c.reason == "step" and c.steps == 1
    assert max_err_ms(c, nxt - 3 * FS, nxt, cap_n0=jump) < 0.5


def test_lost_time_reanchors_after_persisting():
    c = SampleClock(FS)
    n = feed(c, 10.0, jit=jitter_fn(13))
    # the capture stalled 2 s but kept counting: every later sample is 2 s later than the old map says
    n2 = feed(c, 8.0, n_start=n, t0=T0 + 2.0, jit=jitter_fn(14))
    assert c.seg == 0                                       # not yet: could still be a network stall
    n3 = feed(c, 6.0, n_start=n2, t0=T0 + 2.0, jit=jitter_fn(15))
    assert c.seg == 1 and c.reason == "step"
    assert max_err_ms(c, n3 - 2 * FS, n3, t0=T0 + 2.0) < 0.5


@pytest.mark.parametrize("stall_s, quiet", [(0.05, False), (0.2, False), (0.45, False), (0.02, True)])
@pytest.mark.parametrize("phase_s", [0.0, FRAMES / FS, 0.7, 1.9])  # at a bucket edge, one packet after (worst), ...
def test_small_capture_stall_reanchors_within_6_s(stall_s, quiet, phase_s):
    """The sender lost ``stall_s`` of capture (under STEP_LATE_S) and kept counting: every later sample is that
    much later than the old map says. The backstop re-anchors within 6 s whatever the bucket phase; no alarm.
    Its tolerance is max(5 ms, 3 x jitter): 20 ms is caught on a quiet LAN; on the powerline model (3 ms mean,
    60 ms spikes) a loss that small is the CaptureMap's to catch."""
    jit = (lambda seed: jitter_fn(seed, mean_s=0.0005, spike_p=0.0)) if quiet else jitter_fn
    c = SampleClock(FS)
    n = feed(c, 30.0 + phase_s, jit=jit(50))
    assert c.steps == 0
    n = feed(c, 6.0, n_start=n, t0=T0 + stall_s, jit=jit(51))
    assert c.steps == 1 and c.reason == "step" and not c.skew_alarm
    n = feed(c, 30.0, n_start=n, t0=T0 + stall_s, jit=jit(52))
    assert c.steps == 1 and not c.skew_alarm and c.state()["steps_last_min"] == 1
    assert max_err_ms(c, n - 25 * FS, n, t0=T0 + stall_s) < 0.5


def test_steps_latch_the_alarm_under_a_gross_clock_fault():
    """Our clock 2 % fast against the sender (the old WSL fault): it re-anchors again and again; the alarm, set by
    the second step within 60 s, holds through every later step instead of flapping."""
    c = SampleClock(FS)
    rng = random.Random(60)
    n, held = 0, []
    while n < 200 * FS:
        c.observe(n, T0 + (n + FRAMES) / FS * 1.02 + L + rng.expovariate(1 / 0.003), FRAMES)
        if n > 20 * FS and n % (FS // 4) < FRAMES:
            held.append(c.skew_alarm)
        n += FRAMES
    assert c.steps >= 10 and all(held)
    assert c.state()["steps_last_min"] >= 2


def test_alarm_carried_across_a_step_until_a_good_long_fit():
    skew = -500e-6                                          # beyond the clamp, under the line: no backstop steps
    c = SampleClock(FS)
    n = feed(c, 60.0, skew=skew, jit=jitter_fn(61))
    assert c.skew_alarm and c.steps == 0
    raw = c.state()["skew_raw_ppm"]
    assert raw == pytest.approx(-500.0, abs=5.0)
    jump = FS                                               # samples skipped: a step
    n = feed(c, 3.0, n_start=n + jump, cap_n0=jump, skew=skew, jit=jitter_fn(62))
    assert c.steps == 1 and c.skew_alarm                    # latched, with the raw slope that set it
    assert c.state()["skew_raw_ppm"] == raw and c.state()["steps_last_min"] == 1
    # the fault goes away: a within-limit fit over >= 20 s clears it, not before
    t1 = truth(n, skew=skew, cap_n0=jump) - n / FS - L
    n2 = feed(c, 10.0, n_start=n, t0=t1, jit=jitter_fn(63))
    assert c.skew_alarm
    feed(c, 25.0, n_start=n2, t0=t1, jit=jitter_fn(64))
    assert not c.skew_alarm and c.steps == 1


def test_skew_beyond_the_clamp_over_the_line_reanchors_and_stays_close():
    """+500 ppm runs 200 ppm over the clamped line: the backstop re-anchors each time the drift passes its tolerance
    (max(5 ms, 3 x jitter): ~50 s here), so the map stays bounded instead of drifting, and the alarm holds."""
    skew = 500e-6
    c = SampleClock(FS)
    n, worst = 0, 0.0
    for _ in range(18):
        n = feed(c, 10.0, n_start=n, skew=skew, jit=jitter_fn(65 + n))
        worst = max(worst, max_err_ms(c, n - FS, n, skew=skew))
    assert c.steps >= 2 and c.skew_alarm
    assert worst < 25.0                                     # unbounded would be 200 ppm x 180 s = 36 ms


def test_network_stall_burst_does_not_move_time():
    c = SampleClock(FS)
    n = feed(c, 20.0, jit=jitter_fn(16))
    # 3 s of packets held by the network, then delivered in a burst
    burst_at = truth(n) + 3.0
    for k in range(int(3.0 * FS / FRAMES)):
        c.observe(n, burst_at + k * 1e-5, FRAMES)
        n += FRAMES
    n = feed(c, 10.0, n_start=n, jit=jitter_fn(17))
    assert c.seg == 0 and c.steps == 0
    assert max_err_ms(c, n - 30 * FS, n) < 0.5


def test_remote_clock():
    rng = random.Random(20)
    r0_ns = 77_000_000_000_000
    rc = RemoteClock()
    skew = 50e-6                                            # host s per remote s - 1
    t_remote = 0.0
    last = None
    while t_remote < 70.0:
        t_remote += rng.uniform(0.01, 0.3)                  # sparse, irregular stamps (notes, states)
        t_ns = r0_ns + int(t_remote * 1e9)
        arrival = T0 + t_remote * (1 + skew) + 0.006 + rng.expovariate(1 / 0.004)
        rc.observe(t_ns, arrival)
        last = t_ns
    want = T0 + (last - r0_ns) / 1e9 * (1 + skew) + 0.006
    assert abs(rc.to_host(last) - want) * 1000.0 < 0.5
    assert rc.skew_ppm == pytest.approx(50.0, abs=10.0)
    assert rc.to_remote_ns(rc.to_host(last)) == pytest.approx(last, abs=1e3)


def test_remote_clock_reboot_steps():
    rc = RemoteClock()
    for i in range(200):
        rc.observe(10**15 + i * 50_000_000, T0 + i * 0.05 + 0.005)
    rc.observe(3_000_000_000, T0 + 10.05)                   # rebooted: its clock restarted near zero
    assert rc.seg == 1 and rc.reason == "step"


def _audio_and_capture(seconds: float, *, skew: float, remote_skew: float, r0_ns: int, seed: int):
    """An audio stream plus its heartbeats' (n, t_cap_ns) pairs. Remote clock: R(t) = r0 + (t - T0)(1 + remote_skew)."""
    jit = jitter_fn(seed)
    rng = random.Random(seed + 1)
    audio, cap = SampleClock(FS), CaptureMap(FS)
    n, next_hb = 0, 0.0
    while n < seconds * FS:
        audio.observe(n, truth(n + FRAMES, skew=skew) + jit(), FRAMES)
        if n / FS >= next_hb:                               # a heartbeat every 0.5 s
            t_cap = truth(n, skew=skew, lat=0.0)            # the ADC time of sample n
            t_cap_ns = r0_ns + int(((t_cap - T0) * (1 + remote_skew) + rng.uniform(0, 0.0003)) * 1e9)
            cap.observe(n, t_cap_ns, seg=audio.seg)
            next_hb += 0.5
        n += FRAMES
    return audio, cap


def test_midi_through_the_audio_streams_map():
    r0_ns, skew, remote_skew = 123_000_000_000_000, 30e-6, -20e-6
    audio, cap = _audio_and_capture(40.0, skew=skew, remote_skew=remote_skew, r0_ns=r0_ns, seed=30)
    tm = RemoteTimeMap(acoustic_offset_ms=2.0)
    assert tm.via is None
    tm.attach_audio(audio, cap)
    assert tm.via == "audio"
    # a note struck at host time te, stamped on the sender's clock
    te = T0 + 35.0
    t_ns = r0_ns + int((te - T0) * (1 + remote_skew) * 1e9)
    # it lands where a sound captured at te lands on the audio map: te + L (+ the MIDI source's offset)
    assert abs(tm.to_host(t_ns, offset=False) - (te + L)) * 1000.0 < 0.5
    assert tm.to_host(t_ns) - tm.to_host(t_ns, offset=False) == pytest.approx(0.002)
    assert tm.state()["via"] == "audio"


def test_midi_falls_back_to_its_own_clock():
    r0_ns, skew, remote_skew = 5_000_000_000, 0.0, 10e-6
    audio, cap = _audio_and_capture(10.0, skew=skew, remote_skew=remote_skew, r0_ns=r0_ns, seed=40)
    tm = RemoteTimeMap()
    rng = random.Random(41)
    for i in range(400):                                    # state packets and notes, stamped as sent
        te = T0 + i * 0.05
        tm.observe(r0_ns + int((te - T0) * (1 + remote_skew) * 1e9), te + 0.003 + rng.expovariate(1 / 0.002))
    assert tm.via == "remote"
    te = T0 + 19.0
    t_ns = r0_ns + int((te - T0) * (1 + remote_skew) * 1e9)
    assert abs(tm.to_host(t_ns) - (te + 0.003)) * 1000.0 < 0.5
    tm.attach_audio(audio, cap)
    assert tm.via == "audio"
    audio.segment("sess")                                   # the audio map's pairs belong to the old segment
    assert tm.via == "remote"
    tm.detach_audio()
    assert tm.via == "remote"


def _hb_pairs(cap: CaptureMap, seconds: float, *, start_s: float = 0.0, lost_s: float = 0.0, seed: int = 70,
              r0_ns: int = 10**13) -> list[bool]:
    """Heartbeat pairs every 0.5 s: (n, t_cap_ns) with 0.3 ms of callback noise; ``lost_s`` of capture time lost
    before ``start_s`` (the index kept counting)."""
    rng = random.Random(seed)
    out = []
    k = int(start_s * 2)
    while k < (start_s + seconds) * 2:
        n = k * FS // 2
        out.append(cap.observe(n, r0_ns + int((n / FS + lost_s + rng.uniform(0, 0.0003)) * 1e9), seg=0))
        k += 1
    return out


def test_capture_map_residual_and_tolerance():
    cap = CaptureMap(FS)
    assert math.isnan(cap.residual_ms(0, 10**13))
    assert all(_hb_pairs(cap, 20.0))
    assert cap.tolerance_ms == pytest.approx(2.0)          # quiet callbacks: the 2 ms floor
    n = 40 * FS // 2
    assert cap.residual_ms(n, 10**13 + int(n / FS * 1e9)) == pytest.approx(0.0, abs=0.35)
    assert cap.residual_ms(n, 10**13 + int((n / FS + 0.05) * 1e9)) == pytest.approx(50.0, abs=0.35)
    assert cap.residual_ms(n, 10**13 + int((n / FS - 0.01) * 1e9)) == pytest.approx(-10.0, abs=0.35)


def test_capture_map_one_late_callback_is_dropped():
    cap = CaptureMap(FS)
    _hb_pairs(cap, 10.0)
    pairs = cap.state()["pairs"]
    n = 20 * FS // 2
    assert cap.observe(n, 10**13 + int((n / FS + 0.008) * 1e9), seg=0)   # 8 ms late, alone
    assert cap.state()["pairs"] == pairs and cap.off == 1
    assert all(_hb_pairs(cap, 5.0, start_s=10.5))
    assert cap.off == 0


@pytest.mark.parametrize("lost_s", [0.02, 0.05, -0.01])
def test_capture_map_sees_a_sender_step(lost_s):
    """Capture time lost (or samples skipped) while the index kept counting: two pairs off the line in a row."""
    cap = CaptureMap(FS)
    assert all(_hb_pairs(cap, 15.0))
    got = _hb_pairs(cap, 3.0, start_s=15.0, lost_s=lost_s, seed=71)
    assert got[:2] == [True, False] and all(got[2:])        # flagged on the second pair, then the map starts over
    n = 17 * FS
    assert cap.to_remote_ns(n) == pytest.approx(10**13 + (n / FS + lost_s) * 1e9, abs=0.4e6)


def test_midi_does_not_jump_when_via_switches():
    """in_lat 20 ms: the audio map carries +17.3 ms (in_lat - one packet) that the remote map doesn't. The bridge,
    measured while via is "audio", keeps a switch to "remote" under 0.5 ms."""
    in_lat, r0_ns = 0.020, 40_000_000_000_000
    rng = random.Random(80)
    jit = jitter_fn(81)
    audio, cap, tm = SampleClock(FS), CaptureMap(FS), RemoteTimeMap()
    tm.attach_audio(audio, cap)
    n, next_hb, next_state = 0, 0.0, 0.0
    while n < 40 * FS:
        adc = T0 + n / FS                                   # the ADC time of the packet's first sample
        audio.observe(n, adc + in_lat + L + jit(), FRAMES)  # it leaves at the callback, in_lat later
        if n / FS >= next_hb:
            cap.observe(n, r0_ns + int(((adc - T0) + rng.uniform(0, 0.0003)) * 1e9), seg=audio.seg)
            next_hb += 0.5
        if n / FS >= next_state:                            # a MIDI state, stamped as sent
            te = adc + rng.uniform(0, 0.002)
            tm.observe(r0_ns + int((te - T0) * 1e9), te + L + rng.expovariate(1 / 0.002))
            next_state += 0.5
        n += FRAMES
    assert tm.via == "audio"
    te = T0 + 39.5
    t_ns = r0_ns + int((te - T0) * 1e9)
    via_audio = tm.to_host(t_ns)
    assert abs(via_audio - (te + in_lat - FRAMES / FS + L)) * 1000.0 < 0.5
    assert tm.remote.to_host(t_ns) - via_audio == pytest.approx(-(in_lat - FRAMES / FS), abs=0.0005)  # unbridged
    assert tm.state()["bridge_ms"] == pytest.approx((in_lat - FRAMES / FS) * 1000.0, abs=0.5)
    audio.segment("step")                                   # a self-step: via is "remote" until the next heartbeat
    assert tm.via == "remote"
    assert abs(tm.to_host(t_ns) - via_audio) * 1000.0 < 0.5
    tm.detach_audio()
    assert abs(tm.to_host(t_ns) - via_audio) * 1000.0 < 0.5


def test_capture_map_resets_on_a_new_segment():
    cap = CaptureMap(FS)
    for i in range(20):
        cap.observe(i * 24000, 10**12 + i * 500_000_000, seg=0)
    assert cap.ready and cap.seg == 0
    assert cap.to_n(10**12 + 250_000_000) == pytest.approx(12000, abs=1)
    cap.observe(0, 5 * 10**12, seg=1)
    assert cap.seg == 1 and cap.state()["pairs"] == 1
    assert cap.to_n(5 * 10**12) == pytest.approx(0, abs=1)
    assert cap.to_remote_ns(0) == pytest.approx(5 * 10**12, abs=1e3)
