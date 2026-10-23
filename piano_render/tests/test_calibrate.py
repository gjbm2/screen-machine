"""piano_render.calibrate: the envelope, the mic tap (stitching, races, gaps, the host-time map), the matcher, the
Calibrator's decisions (lock, slew, step, clamp, persist, prior, fail closed, sink rebases), the websocket client
against a fake levels server, and detection accuracy against SNR on music rendered silently offline (TimGM6mb)
through a synthetic room. No sound, no show_service, nothing outside tmp.

    pytest -s piano_render/tests/test_calibrate.py -k snr      # prints the accuracy table
"""

import base64
import hashlib
import json
import math
import socket
import threading
import time

import numpy as np
import pytest

from piano_render import calibrate as C
from piano_render import engines, score

from .conftest import SR, TIMGM

PK = 128
IN_LAT_MS = 10.67
INFO = {"ok": True, "active": "room-mic", "active_label": "Drawing room", "label": "Drawing room",
        "state": "streaming", "mic_on": True, "mic_why": "Listening for Auto", "fs": SR, "packet": PK,
        "in_lat_ms": IN_LAT_MS, "offset_ms": 0.0}


# ---------------------------------------------------------------------------------------------------- signals

def synth_piano(seconds: float, seed: int = 0, fs: int = SR) -> np.ndarray:
    """Piano-like music without an engine: decaying harmonic notes at irregular onsets, some chords, mono float32."""
    rng = np.random.default_rng(seed)
    n = int(seconds * fs)
    y = np.zeros(n + 3 * fs, np.float64)
    t = 0.2
    while t < seconds:
        for _ in range(1 if rng.random() < 0.7 else int(rng.integers(2, 4))):
            key = int(rng.integers(40, 88))
            f0 = 440.0 * 2 ** ((key - 69) / 12)
            amp = 10 ** (rng.uniform(-26, -8) / 20)
            tau = float(np.clip(2.5 * 2 ** (-(key - 40) / 18), 0.15, 2.0))
            m = int(min(3 * tau, 3.0) * fs)
            tt = np.arange(m) / fs
            env = np.exp(-tt / tau) * np.minimum(1.0, tt / 0.002)
            note = sum((1.0 / h) * np.sin(2 * np.pi * f0 * h * tt) for h in (1, 2, 3, 4) if f0 * h < 9000)
            s = int(t * fs)
            y[s:s + m] += amp * env * note
        t += float(rng.uniform(0.12, 0.7))
    return y[:n].astype(np.float32)


def room(x: np.ndarray, delay_s: float, *, snr_db: float = 15.0, rt60: float = 0.8, drr_db: float = -3.0,
         level_dbfs: float = -45.0, reflection: float = 0.5, seed: int = 1, fs: int = SR) -> np.ndarray:
    """What a room mic hears: a direct path, one early reflection 15 ms later, a diffuse exponential tail (rt60,
    direct-to-reverberant drr_db), a 120 Hz-7 kHz band limit, the delay, a level, pink-ish noise at snr_db
    (music power / noise power) and INT16 quantisation. Output as int16 (the levels feed's samples)."""
    import scipy.signal as sps
    rng = np.random.default_rng(seed)
    n_ir = int(rt60 * fs)
    tt = np.arange(n_ir) / fs
    tail = rng.standard_normal(n_ir) * np.exp(-6.91 * tt / rt60)
    tail[:int(0.003 * fs)] = 0.0
    tail *= math.sqrt(10 ** (-drr_db / 10) / float(np.sum(tail ** 2)))
    ir = tail
    ir[0] += 1.0
    ir[int(0.015 * fs)] += reflection
    y = sps.fftconvolve(x.astype(np.float64), ir)[:len(x)]
    sos = sps.butter(2, [120.0, 7000.0], btype="bandpass", fs=fs, output="sos")
    y = sps.sosfilt(sos, y)
    y = np.concatenate([np.zeros(int(round(delay_s * fs))), y])
    p = float(np.mean(y[int(delay_s * fs):] ** 2))
    y *= math.sqrt(10 ** (level_dbfs / 10) / p)
    noise = sps.lfilter([1.0], [1.0, -0.95], rng.standard_normal(len(y)))
    noise *= math.sqrt(10 ** ((level_dbfs - snr_db) / 10) / float(np.mean(noise ** 2)))
    y = y + noise
    return np.clip(np.rint(y * 32767), -32767, 32767).astype(np.int16)


class Feed:
    """levels.py frames of a recording, as show_service sends them: ~60 a second, ``c`` and ``a`` = the newest
    packet that had arrived when the frame's slot was read (a = its arrival: capture end + in_lat - one packet +
    transport + jitter), the wave = the ring's newest WAVE_N samples a moment later (with probability race_p up to
    race_ms later: packets arriving in between are in the wave but not in c, levels.py's race); optional dropped
    frames (four in a row: 67 ms, more than a wave holds)."""

    def __init__(self, rec: np.ndarray, t_rec0: float, *, race_p: float = 0.3, race_ms: float = 4.0,
                 drop_every: int = 0, jitter_ms: float = 3.0, seed: int = 2, c0: int = 1_000_000,
                 stream: str = "room-mic"):
        rng = np.random.default_rng(seed)
        self.rec, self.t0, self.c0, self.stream = rec, t_rec0, c0, stream
        n_pk = len(rec) // PK
        ends = t_rec0 + (np.arange(n_pk) + 1) * PK / SR
        base = ends + IN_LAT_MS / 1000 - PK / SR + C.TRANSPORT_S
        jit = rng.exponential(jitter_ms / 1000, n_pk) * (rng.random(n_pk) < 0.7)
        self.arr = np.maximum.accumulate(base + jit)
        self.race_p, self.race_ms, self.drop_every, self.rng = race_p, race_ms, drop_every, rng

    def frames(self):
        i = 0
        t = self.arr[20]
        while True:
            t += 1 / 60
            c = int(np.searchsorted(self.arr, t, side="right")) - 1
            if c + 2 >= len(self.arr):
                return
            i += 1
            if self.drop_every and i % self.drop_every == 0:
                t += 3 / 60
                continue
            late = self.rng.uniform(0, self.race_ms / 1000) if self.rng.random() < self.race_p else 0.0
            cw = int(np.searchsorted(self.arr, t + late, side="right")) - 1
            end = (cw + 1) * PK
            w = self.rec[max(0, end - C.WAVE_N):end]
            if len(w) < C.WAVE_N:
                w = np.concatenate([np.zeros(C.WAVE_N - len(w), np.int16), w])
            yield t, {"s": self.stream, "c": (self.c0 + c) % (1 << 32), "n": 4, "pk": -40.0, "t": None,
                      "a": float(self.arr[c]), "wb": base64.b64encode(w.astype("<i2").tobytes()).decode()}


def drive(cal: C.Calibrator, feed: Feed, now: list, step_s: float = 0.1, until: float = None) -> None:
    last = -1e9
    for t, f in feed.frames():
        if until is not None and t > until:
            break
        now[0] = t
        cal.feed_frame(f, t)
        if t - last >= step_s:
            last = t
            cal.step(t)


@pytest.fixture(scope="module")
def piece():
    return synth_piano(48.0, seed=3)


@pytest.fixture(scope="module")
def heard(piece):
    """(rec int16, true delay in the recording)"""
    return room(piece, 0.3137, snr_db=15.0, seed=4), 0.3137


def make_cal(tmp_path, now, events, **kw):
    cal = C.Calibrator("sonos:drawing-room", emit=events.append, connect=False, clock=lambda: now[0],
                       lat_path=tmp_path / "latency.json", **kw)
    cal.set_show(INFO)
    return cal


def rebases(events):
    return [e for e in events if e["ev"] == "rebase"]


# ---------------------------------------------------------------------------------------------------- envelope

def test_bandpower_incremental_equals_whole():
    rng = np.random.default_rng(0)
    x = (rng.standard_normal(SR * 2) * 0.1).astype(np.float32)
    bp = C.BandPower(SR)
    whole = bp.frames(x, 0, 0, 900)
    inc = C.BandPower(SR)
    parts, i = [], 0
    while i < len(x):
        k = int(rng.integers(1, 5000))
        k0, p = inc.push(x[i:i + k])
        if p.shape[0]:
            assert k0 == sum(q.shape[0] for q in parts)
            parts.append(p)
        i += k
    got = np.concatenate(parts)[:900]
    np.testing.assert_allclose(got, whole, rtol=2e-4, atol=1e-12)


def test_bandpower_level_and_bands():
    t = np.arange(SR) / SR
    f0 = 11 * SR / 512                                                     # on an FFT bin (1031.25 Hz)
    x = (0.1 * np.sin(2 * np.pi * f0 * t)).astype(np.float32)            # -20 dBFS peak: mean square -23 dB
    p = C.BandPower(SR).frames(x, 0, 50, 400)
    assert abs(10 * np.log10(np.mean(p[:, C.N_BANDS])) - (-23.01)) < 0.1
    edges = np.geomspace(C.BAND_LO_HZ, C.BAND_HI_HZ, C.N_BANDS + 1)
    loud = int(np.argmax(p[:, :C.N_BANDS].mean(axis=0)))
    assert edges[loud] <= f0 < edges[loud + 1]


def test_envring_wrap_and_gaps():
    r = C.EnvRing(10, width=2)
    r.append(0, np.ones((6, 2), np.float32))
    r.append(8, 2 * np.ones((6, 2), np.float32))         # frames 6, 7 unknown; the oldest fall out
    p, ok = r.get(3, 15)
    assert r.start == 4 and r.end == 14
    assert list(ok) == [False, True, True, False, False, True, True, True, True, True, True, False]
    assert p[5, 0] == 2.0 and p[1, 0] == 1.0


def test_envelope_ignores_fixed_eq_and_level():
    rng = np.random.default_rng(1)
    p = rng.random((3000, C.N_BANDS + 1)).astype(np.float32) + 0.1
    eq = (10 ** (rng.uniform(-20, 20, C.N_BANDS + 1) / 10)).astype(np.float32)
    a, _ = C.envelope(p)
    b, _ = C.envelope(p * eq * 1e-3)
    np.testing.assert_allclose(a, b, atol=1e-5)


# ---------------------------------------------------------------------------------------------------- tap

def first_sample(frame: dict, feed: Feed, seg: np.ndarray) -> int:
    """The recording index of the tap's content sample 0 (the first frame's oldest sample; it may have raced)."""
    c = (int(frame["c"]) - feed.c0) % (1 << 32)
    w0 = np.frombuffer(base64.b64decode(frame["wb"]), "<i2")
    end = next(e for e in ((c + j) * PK for j in range(1, 6)) if np.array_equal(seg[e - C.WAVE_N:e], w0))
    return end - C.WAVE_N


def test_tap_host_map_and_gaps(heard):
    rec, _ = heard
    seg = rec[:SR * 8]
    feed = Feed(seg, 1000.0, race_p=0.3, drop_every=40)
    frames = list(feed.frames())
    tap = C.MicTap("room-mic")
    for t, f in frames:
        tap.feed(f, t)
    tap.flush()
    assert tap.stats["race"] > 20 and tap.stats["gaps"] >= 5
    _, ok = tap.ring.get(tap.ring.start, tap.ring.end)
    assert 0 < (~ok).sum() < 0.2 * ok.size                 # the frames over the holes are masked, only those
    # the host-time map: a frame's ADC time back to within a millisecond (gaps keep the content on the counter)
    s0 = first_sample(frames[0][1], feed, seg)
    k = tap.ring.end - 200
    t_adc = tap.frame_time(k, C.mic_latency_s(INFO))
    assert abs(t_adc - (1000.0 + (s0 + k * tap.bp.hop) / SR)) < 0.001


def test_tap_content_matches_recording(heard):
    rec, _ = heard
    seg = rec[:SR * 4]
    feed = Feed(seg, 50.0, race_p=0.4, drop_every=0)
    tap = C.MicTap("room-mic")
    out = []
    orig_write = tap._write

    def spy(x):
        out.append(np.array(x, np.int16))
        orig_write(x)
    tap._write = spy
    frames = list(feed.frames())
    for t, f in frames:
        tap.feed(f, t)
    y = np.concatenate(out)
    s0 = first_sample(frames[0][1], feed, seg)
    np.testing.assert_array_equal(y, seg[s0:s0 + len(y)])
    assert tap.stats["race"] > 50 and tap.stats["gaps"] == 0 and tap.stats["unmatched"] == 0


def test_tap_resets_on_other_stream_and_counter_wrap(heard):
    rec, _ = heard
    tap = C.MicTap("room-mic")
    fr = list(Feed(rec[:SR * 2], 0.0, c0=(1 << 32) - 40).frames())   # the counter wraps inside
    for t, f in fr[:60]:
        tap.feed(f, t)
    assert tap.stats["resets"] == 1 and tap.stats["gaps"] == 0
    tap.feed(dict(fr[60][1], s="pc22-mic"), fr[60][0])
    assert tap.stats["other_stream"] == 1 and tap.stats["resets"] == 2 and tap.prev is None
    tap.feed(fr[61][1], fr[61][0])
    assert tap.n == C.WAVE_N


def test_tap_shift_prefers_counter_and_packets():
    rng = np.random.default_rng(5)
    s = rng.integers(-3000, 3000, 6000).astype(np.int16)
    prev, x = s[1000:1000 + C.WAVE_N], s[1000 + 768:1000 + 768 + C.WAVE_N]
    assert C.tap_shift(prev, x, 768) == 768
    assert C.tap_shift(prev, x, 640) == 768                # a race: one packet more than the counter said
    assert C.tap_shift(prev, x, 100) == 768                # found by content
    assert C.tap_shift(prev, prev, 0) == 0
    assert C.tap_shift(prev, s[4000:4000 + C.WAVE_N], 3000) is None
    quiet = np.zeros(C.WAVE_N, np.int16)
    assert C.tap_shift(quiet, quiet, 768) == 768           # digital silence: trust the counter


# ---------------------------------------------------------------------------------------------------- matching

def test_measure_offline_finds_the_delay(piece, heard):
    rec, d = heard
    res = C.measure_offline(piece, SR, rec.astype(np.float32) / 32767, SR, rec_offset_s=d + 0.6)
    clear = [r for r in res if r.clear]
    assert len(clear) >= 0.7 * len([r for r in res if not r.get("skip")])
    err = [r["lag_s"] + 0.6 for r in clear]
    assert abs(float(np.median(err))) < 0.005 and max(abs(e) for e in err) < 0.03


def agreeing_runs(res: list) -> int:
    """How often AGREE_N consecutive windows are clear and within AGREE_S of their median (the Calibrator's lock)."""
    n = 0
    for i in range(len(res) - C.AGREE_N + 1):
        run = res[i:i + C.AGREE_N]
        if all(r.clear for r in run):
            lags = [r["lag_s"] for r in run]
            n += max(abs(x - float(np.median(lags))) for x in lags) <= C.AGREE_S
    return n


def test_unrelated_music_never_agrees(piece):
    """Another piece of the same synthetic kind (the worst case: same timbre, same onset statistics) can clear single
    windows (measured: 3.5 % of 370 windows over 6 pairs, 6 of 20 for one pair, three overlapping ones agreeing at
    +2.2 s; real piano pieces and pop through the measured rooms: none, peak/sidelobe <= 1.58, z <= 5.3). It never
    makes AGREE_N agreeing windows whose first and last share no audio. Noise never clears one."""
    for seed in (11, 12, 13):
        other = room(synth_piano(48.0, seed=seed), 0.3, snr_db=20.0, seed=6)
        res = C.measure_offline(piece, SR, other.astype(np.float32) / 32767, SR, rec_offset_s=0.3)
        live = [r for r in res if not r.get("skip")]
        assert sum(r.clear for r in live) <= 0.4 * len(live)
        assert agreeing_runs(live) == 0
    noise = room(np.zeros(SR * 48, np.float32) + 1e-9, 0.3, snr_db=-60.0, seed=7)
    res = C.measure_offline(piece, SR, noise.astype(np.float32) / 32767, SR, rec_offset_s=0.3)
    assert not [r for r in res if r.clear]


def test_music_throughout_skips_silence(piece):
    bp = C.BandPower(SR)
    p = bp.frames(piece, 0, 2000, 5000)
    assert C.music_throughout(p) > 0.9
    x = piece.copy()
    x[int(3.5 * SR):int(5.5 * SR)] = 0.0
    assert C.music_throughout(bp.frames(x, 0, 1500, 4500)) < C.MUSIC_MIN


# ---------------------------------------------------------------------------------------------------- calibrator

def test_calibrator_locks_and_slews_small_errors(tmp_path, piece, heard):
    rec, d = heard
    t_rec0 = 5000.0
    h0 = t_rec0 + d                                        # MIDI time 0 reaches the mic
    now, ev = [t_rec0], []
    cal = make_cal(tmp_path, now, ev)
    announced = cal.begin(h0 - 0.06, C.ArrayReference(piece))   # the sink says 60 ms early
    assert announced == pytest.approx(h0 - 0.06)
    drive(cal, Feed(rec, t_rec0), now)
    rb = rebases(ev)
    assert rb and all(abs(r["t_audio0"] - r["was"]) <= C.SLEW_STEP_S + 1e-9 for r in rb)
    assert abs(cal.announced - h0) < 0.005, (cal.announced - h0)
    assert cal.locked and cal.status()["state"] == "locked"
    st = C.load_latency(tmp_path / "latency.json")["outputs"]["sonos:drawing-room"]
    assert abs(st["delay_ms"] - 60.0) < 5.0 and st["n"] >= 1
    first = next(i for i, e in enumerate(ev) if e["ev"] == "calibration" and e["state"] == "locked")
    lock_t = [e for e in ev[:first] if e["ev"] == "rebase"]
    assert not lock_t                                       # nothing moved before the lock
    # the next play starts from what was measured
    ev2 = []
    cal2 = make_cal(tmp_path, now, ev2)
    assert cal2.begin(7000.0, C.ArrayReference(piece)) == pytest.approx(7000.0 + st["delay_ms"] / 1000, abs=2e-5)


def test_calibrator_steps_big_errors_and_clamps(tmp_path, piece, heard):
    """Once a play has locked, a big error is made up in steps of MAX_JUMP_S at most. Until then (owner, 23 Oct: a
    Sonos's radio stream is heard seconds after its estimate) a Sonos is searched SEARCH_WIDE_S wide and its first
    correction is one step, however big."""
    rec, d = heard
    t_rec0 = 100.0
    h0 = t_rec0 + d
    for err, steps, wide in ((0.5, 1, False), (2.6, 2, False), (2.6, 1, True), (-7.5, 1, True), (9.0, 1, True)):
        now, ev = [t_rec0], []
        cal = make_cal(tmp_path, now, ev, persist=False)
        if not wide:                                     # as after a lock: the narrow search, small steps
            cal.search_s, cal.first_jump = C.SEARCH_S, False
        cal.begin(h0 + err, C.ArrayReference(piece))       # the sink's estimate is err off
        drive(cal, Feed(rec, t_rec0, seed=9), now)
        big = [r for r in rebases(ev) if abs(r["t_audio0"] - r["was"]) > C.SLEW_MAX_S]
        assert len(big) == steps, (err, wide, [(r["t_audio0"] - r["was"]) for r in rebases(ev)])
        most = C.SEARCH_WIDE_S if wide else C.MAX_JUMP_S
        assert all(abs(r["t_audio0"] - r["was"]) <= most + 1e-9 for r in rebases(ev))
        assert abs(cal.announced - h0) < 0.006, (err, wide, cal.announced - h0)


def test_calibrator_rechecks_drift(tmp_path, piece):
    """The output's timeline drifts 1.5 ms/s (a sink clock 1500 ppm off): re-checks every RECHECK_S follow it."""
    import scipy.signal as sps
    x = synth_piano(100.0, seed=13)
    stretched = sps.resample(x, int(len(x) * 1.0015)).astype(np.float32)
    rec = room(stretched, 0.25, snr_db=20.0, seed=14)
    now, ev = [10.0], []
    cal = make_cal(tmp_path, now, ev, persist=False)
    cal.begin(10.25, C.ArrayReference(x))
    drive(cal, Feed(rec, 10.0), now)
    locks = [e for e in ev if e["ev"] == "calibration" and e["state"] == "locked"]
    assert len(locks) >= 2
    # by the end the piece is ~140 ms later than at its start; the clock has followed most of it
    assert cal.delay > 0.06, cal.delay
    assert all(abs(r["t_audio0"] - r["was"]) <= C.SLEW_STEP_S + 1e-9 for r in rebases(ev))


@pytest.mark.parametrize("info, says", [
    ({"ok": False, "why": "can't reach show_service (URLError)"}, "can't reach show_service"),
    (dict(INFO, mic_on=False, mic_why="Auto is off"), "the mic is off (Auto is off)"),
    (dict(INFO, state="off"), "the Drawing room mic is off"),
])
def test_calibrator_fails_closed(tmp_path, piece, heard, info, says):
    rec, d = heard
    now, ev = [0.0], []
    cal = make_cal(tmp_path, now, ev)
    cal.set_show(info)
    cal.begin(d + 0.1, C.ArrayReference(piece))
    drive(cal, Feed(rec, 0.0), now, until=30.0)
    assert not rebases(ev)
    st = cal.status()
    assert st["state"] == "off" and says in st["why"] and "using the Sonos's own estimate" in st["why"], st
    assert not (tmp_path / "latency.json").exists()


def test_an_older_feed_without_stream_falls_back_to_the_active_input(tmp_path, piece, heard):
    """A feed that ignores ?stream sends the active input: with another input active, no check, and the status says
    why; once the named mic is the active input, its frames calibrate as before."""
    rec, d = heard
    now, ev = [0.0], []
    cal = make_cal(tmp_path, now, ev, persist=False)
    cal.set_show(dict(INFO, active="pc22-mic", active_label="PC22"))
    cal.begin(d + 0.1, C.ArrayReference(piece))
    drive(cal, Feed(rec, 0.0, stream="pc22-mic"), now, until=30.0)
    assert not rebases(ev) and cal.feed_named is False
    st = cal.status()
    assert st["state"] == "off" and st["feed"] == "active only", st
    assert "listens on the PC22 mic, not the Drawing room one" in st["why"] and "doesn't take ?stream" in st["why"]
    assert "using the Sonos's own estimate" in st["why"] and ev[-1]["feed"] == "active only"
    cal.set_show(INFO)                                     # the drawing-room mic becomes the active input
    t0 = now[0] + 1.0
    cal.begin(t0 + d + 0.02, C.ArrayReference(piece))
    drive(cal, Feed(rec, t0), now)
    assert cal.locked and abs(cal.announced - (t0 + d)) < 0.005


def test_the_named_mic_calibrates_while_another_input_is_active(tmp_path, piece, heard):
    """show_service's ?stream feed: the drawing-room mic's frames while PC22's is the active input."""
    rec, d = heard
    now, ev = [0.0], []
    cal = make_cal(tmp_path, now, ev, persist=False)
    cal.set_show(dict(INFO, active="pc22-mic", active_label="PC22"))
    cal.begin(d + 0.02, C.ArrayReference(piece))
    drive(cal, Feed(rec, 0.0), now)
    assert cal.feed_named is True and cal.status()["feed"] == "by name"
    assert cal.locked and abs(cal.announced - d) < 0.005, cal.announced - d
    assert any(e["ev"] == "calibration" and e["mic"] == "room-mic" and e["feed"] == "by name" for e in ev)
    cal.new_feed()                                         # a reconnect learns it again
    assert cal.feed_named is None


def test_no_mic_named_means_no_check_and_no_feed(tmp_path, piece, monkeypatch):
    """--calibration-mic "": show_service found no mic that hears the output: the prior stands, nothing connects."""
    def no_get(url):
        raise AssertionError(f"asked {url}")
    now, ev = [0.0], []
    cal = C.Calibrator("sonos:drawing-room", mic="", emit=ev.append, clock=lambda: now[0],
                       lat_path=tmp_path / "latency.json", get=no_get, show_url="http://127.0.0.1:9")
    try:
        assert cal.mic is None and cal.begin(10.0, C.ArrayReference(piece)) == 10.0
        assert cal._threads == []
        cal.step(12.0)
        st = cal.status()
        assert st["state"] == "off" and st["mic"] is None and st["feed"] is None
        assert st["why"] == ("show_service names no mic that hears sonos:drawing-room: no mic check; using the "
                             "Sonos's own estimate")
        assert ev[-1]["ev"] == "calibration" and ev[-1]["mic"] is None
    finally:
        cal.close()
    monkeypatch.setenv(C.MIC_ENV, "other-mic")
    assert C.Calibrator("sonos:drawing-room", connect=False, mic="room-mic").mic == "room-mic"   # named: it wins
    assert C.Calibrator("sonos:drawing-room", connect=False).mic == "other-mic"
    assert C.levels_url("http://127.0.0.1:8703", "room-mic") == \
        "ws://127.0.0.1:8703/v1/levels?wave=2048&stream=room-mic"
    assert C.levels_url("http://127.0.0.1:8703", None) == "ws://127.0.0.1:8703/v1/levels?wave=2048"


@pytest.mark.parametrize("kind", ["silent", "noisy", "other music"])
def test_calibrator_quiet_room_or_wrong_music_no_correction(tmp_path, piece, kind):
    rec = {"silent": lambda: room(np.zeros(SR * 40, np.float32) + 1e-9, 0.3, snr_db=10.0, level_dbfs=-110, seed=3),
           "noisy": lambda: room(np.zeros(SR * 40, np.float32) + 1e-9, 0.3, snr_db=-60.0, level_dbfs=-95, seed=3),
           "other music": lambda: room(synth_piano(40.0, seed=21), 0.3, snr_db=20.0, seed=22)}[kind]()
    now, ev = [0.0], []
    cal = make_cal(tmp_path, now, ev)
    cal.begin(0.4, C.ArrayReference(piece))
    drive(cal, Feed(rec, 0.0), now)
    assert not rebases(ev) and not cal.locked
    st = cal.status()
    assert st["state"] in ("listening", "measuring")
    if kind == "silent":
        assert "the mic hears almost nothing" in st["why"]
    assert not (tmp_path / "latency.json").exists()


def test_sink_rebase_moves_with_the_sink_and_rechecks(tmp_path, piece, heard):
    rec, d = heard
    now, ev = [0.0], []
    cal = make_cal(tmp_path, now, ev, persist=False)
    cal.begin(d + 0.02, C.ArrayReference(piece))
    feed = list(Feed(rec, 0.0).frames())
    half = [x for x in feed if x[0] < 25.0]
    for t, f in half:
        now[0] = t
        cal.feed_frame(f, t)
        cal.step(t)
    assert cal.locked
    delay = cal.delay
    emit_outer = []
    sink = cal.sink_emit(emit_outer.append)
    sink({"ev": "warning", "why": "Sonos volume is 0"})
    sink({"ev": "rebase", "t_audio0": cal.sink_t0 + 0.9, "was": cal.sink_t0})
    assert emit_outer == [{"ev": "warning", "why": "Sonos volume is 0"}]
    r = rebases(ev)[-1]
    assert r["by"] == "sink" and r["t_audio0"] == pytest.approx(cal.sink_t0 + delay)
    assert cal.last_accept_t is None and cal.results == []
    for t, f in feed[len(half):]:                          # the audio didn't really move: the mic brings it back
        now[0] = t
        cal.feed_frame(f, t)
        cal.step(t)
    assert abs(cal.announced - d) < 0.01, cal.announced - d


def test_calibrator_end_and_status(tmp_path, piece):
    now, ev = [0.0], []
    cal = make_cal(tmp_path, now, ev)
    cal.begin(1.0, C.ArrayReference(piece))
    cal.end()
    st = cal.status()
    assert st["state"] == "idle" and ev[-1]["state"] == "idle"
    cal.step(5.0)                                          # not playing: nothing happens
    assert cal.windows == 0


# ---------------------------------------------------------------------------------------------------- persistence

def test_record_delay_keeps_the_newest_and_takes_the_median(tmp_path):
    p = tmp_path / "piano" / "latency.json"
    for v in (0.100, 0.300, 0.110, 0.120, 0.5, 0.105, 0.115, 0.112, 0.108, 0.111, 0.109):
        C.record_delay("pc22:default", v, {"mic": "pc22-mic"}, path=p)
    d = json.loads(p.read_text())
    o = d["outputs"]["pc22:default"]
    assert o["n"] == C.KEEP_N and len(o["measurements"]) == C.KEEP_N
    assert C.prior_delay("pc22:default", p) == pytest.approx(0.111)
    assert C.prior_delay("sonos:kitchen", p) is None
    p.write_text("{not json")
    assert C.prior_delay("pc22:default", p) is None
    C.record_delay("pc22:default", 0.2, path=p)               # a broken file starts again
    assert C.prior_delay("pc22:default", p) == pytest.approx(0.2)


def test_output_keys_and_mics():
    assert C.output_key("pc22") == "pc22:default"
    assert C.output_key("pc22", device="DELL UP2715K") == "pc22:DELL UP2715K"
    assert C.output_key("sonos:drawing-room") == "sonos:drawing-room"
    assert C.Calibrator("pc22", connect=False).mic == "pc22-mic"
    assert C.Calibrator("sonos:drawing-room", connect=False).mic == "room-mic"


def test_show_info_reads_what_show_service_says(tmp_path):
    inputs = {"active": "pc22-mic", "choice": None, "inputs": [
        {"stream": "room-mic", "sender": "kiosk", "label": "Drawing room", "state": "off", "device": "UM04"},
        {"stream": "pc22-mic", "sender": "pc22", "label": "PC22", "state": "quiet", "device": "Echo"}]}
    state = {"mic": {"on": True, "why": "Listening for Auto", "streams": {"capturing": ["pc22-mic"],
                                                                          "off": ["room-mic"]}},
             "streams": {"room-mic": {"format": None, "sender": {"in_lat_ms": 0.0}, "state": "off"},
                         "pc22-mic": {"format": {"sr": 48000, "channels": 1, "frames": 128},
                                      "sender": {"in_lat_ms": 22.0}, "state": "quiet"}}}
    get = lambda url: inputs if url.endswith("/inputs") else state          # noqa: E731
    i = C.show_info("room-mic", get=get)
    assert i["ok"] and i["active"] == "pc22-mic" and i["active_label"] == "PC22" and i["mic_off"] and i["fs"] == 48000
    j = C.show_info("pc22-mic", get=get)
    assert not j["mic_off"] and j["in_lat_ms"] == 22.0
    assert abs(C.mic_latency_s(j) - (0.022 - 128 / 48000 + 0.0005)) < 1e-9

    def down(url):
        raise OSError("refused")
    assert C.show_info("room-mic", get=down)["ok"] is False


def test_room_latency_offset_is_read_only(tmp_path):
    p = tmp_path / "latency.json"
    p.write_text(json.dumps({"version": 2, "sources": {"room-mic": {"acoustic_offset_ms": 4.5}}}))
    before = p.read_bytes()
    assert C.room_acoustic_offset_ms("room-mic", p) == 4.5 and C.room_acoustic_offset_ms("pc22-mic", p) == 0.0
    assert p.read_bytes() == before


# ---------------------------------------------------------------------------------------------------- websocket

class FakeLevels:
    """A levels feed on a local port (stdlib): the handshake, then text frames (some fragmented, both extended
    lengths), pings that must be answered with a masked pong, and the close. ``frames`` is an iterable of str."""

    def __init__(self, frames, ping_every: int = 5, pace_s: float = 0.0):
        self.frames, self.ping_every, self.pace = frames, ping_every, pace_s
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        self.pongs, self.paths, self.closed_by_client = [], [], threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    @staticmethod
    def _frame(op: int, payload: bytes, fin: bool = True) -> bytes:
        n = len(payload)
        h = bytes([(0x80 if fin else 0) | op])
        if n < 126:
            h += bytes([n])
        elif n < 65536:
            h += bytes([126]) + n.to_bytes(2, "big")
        else:
            h += bytes([127]) + n.to_bytes(8, "big")
        return h + payload

    def _read_client(self, c):
        buf = b""
        try:
            while True:
                d = c.recv(4096)
                if not d:
                    self.closed_by_client.set()
                    return
                buf += d
                while len(buf) >= 6:
                    op, ln = buf[0] & 0x0F, buf[1] & 0x7F
                    assert buf[1] & 0x80, "client frames must be masked"
                    if len(buf) < 6 + ln:
                        break
                    mask, body = buf[2:6], buf[6:6 + ln]
                    body = bytes(b ^ mask[i % 4] for i, b in enumerate(body))
                    buf = buf[6 + ln:]
                    if op == 0xA:
                        self.pongs.append(body)
                    elif op == 0x8:
                        self.closed_by_client.set()
        except OSError:
            self.closed_by_client.set()

    def _serve(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            req = b""
            while b"\r\n\r\n" not in req:
                req += c.recv(4096)
            lines = req.decode().split("\r\n")
            self.paths.append(lines[0].split()[1])
            key = next(ln.split(":", 1)[1].strip() for ln in lines if ln.lower().startswith("sec-websocket-key"))
            acc = base64.b64encode(hashlib.sha1((key + C.WSClient.GUID).encode()).digest()).decode()
            c.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       f"Sec-WebSocket-Accept: {acc}\r\n\r\n").encode())
            threading.Thread(target=self._read_client, args=(c,), daemon=True).start()
            try:
                for i, s in enumerate(self.frames):
                    b = s.encode()
                    if i % 7 == 3 and len(b) > 10:          # fragmented
                        c.sendall(self._frame(0x1, b[:5], fin=False) + self._frame(0x0, b[5:]))
                    else:
                        c.sendall(self._frame(0x1, b))
                    if self.ping_every and i % self.ping_every == 0:
                        c.sendall(self._frame(0x9, b"hb%d" % i))
                    if self.pace:
                        time.sleep(self.pace)
                c.sendall(self._frame(0x8, b"\x03\xe8"))
            except OSError:
                pass

    def close(self):
        self.srv.close()


def test_ws_client_reads_frames_answers_pings_and_closes():
    big = json.dumps({"x": "y" * 70000})                 # a 64-bit length
    msgs = [json.dumps({"i": i, "pad": "z" * (200 if i % 2 else 3000)}) for i in range(12)] + [big]
    srv = FakeLevels(msgs, ping_every=4)
    ws = C.WSClient(f"ws://127.0.0.1:{srv.port}/v1/levels?wave=2048").connect()
    got = []
    with pytest.raises(ConnectionError):
        while True:
            m = ws.recv(2.0)
            assert m is not None, "timed out"
            got.append(m)
    assert got == msgs
    assert srv.paths == ["/v1/levels?wave=2048"]
    deadline = time.time() + 2
    while len(srv.pongs) < 4 and time.time() < deadline:
        time.sleep(0.01)
    assert srv.pongs[:4] == [b"hb0", b"hb4", b"hb8", b"hb12"]
    srv.close()


def test_ws_client_timeout_returns_none():
    srv = FakeLevels([json.dumps({"i": 0})] * 1, ping_every=0, pace_s=5.0)
    ws = C.WSClient(f"ws://127.0.0.1:{srv.port}/v1/levels").connect()
    assert ws.recv(1.0) is not None
    t = time.monotonic()
    assert ws.recv(0.2) is None and time.monotonic() - t < 0.5
    ws.close()
    srv.close()


def test_calibrator_threads_with_a_fake_feed(tmp_path, piece, heard):
    """The real threads: the tap connects when a play begins, reads the feed, and lets go when it ends."""
    rec, d = heard
    frames = [json.dumps(f) for _, f in Feed(rec[:SR * 4], time.monotonic()).frames()][:120]
    srv = FakeLevels(frames, ping_every=30, pace_s=1 / 60)
    get = lambda url: ({"active": "room-mic", "inputs": [{"stream": "room-mic", "label": "Drawing room",  # noqa: E731
                                                          "state": "streaming"}]} if url.endswith("/inputs") else
                       {"mic": {"on": True}, "streams": {"room-mic": {"format": {"sr": SR, "frames": PK},
                                                                     "sender": {"in_lat_ms": IN_LAT_MS}}}})
    ev = []
    cal = C.Calibrator("sonos:drawing-room", emit=ev.append, show_url=f"http://127.0.0.1:{srv.port}",
                       lat_path=tmp_path / "l.json", get=get)
    try:
        cal.begin(time.monotonic() + 1.0, C.ArrayReference(piece))
        deadline = time.monotonic() + 5
        while cal.tap.stats["frames"] < 60 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert cal.tap.stats["frames"] >= 60 and cal.info.get("ok") and cal.info["in_lat_ms"] == IN_LAT_MS
        assert srv.paths == ["/v1/levels?wave=2048&stream=room-mic"]       # the mic by name (?stream)
        assert cal.feed_named is None                                      # it is the active input too: can't tell
        cal.end()
        assert srv.closed_by_client.wait(2.0), "the feed is let go between plays"
    finally:
        cal.close()
        srv.close()


# ---------------------------------------------------------------------------------------------------- accuracy

@pytest.fixture(scope="module")
def gm_piece(tmp_path_factory):
    """30 s of a generated phrase rendered silently offline with FluidSynth (TimGM6mb's piano)."""
    if not TIMGM.exists():
        pytest.skip("TimGM6mb.sf2 missing")
    rng = np.random.default_rng(31)
    ev, t = [(0.0, "cc", 64, 0)], 0.3
    while t < 29.0:
        keys = sorted(set(int(k) for k in rng.integers(36, 90, 1 if rng.random() < 0.6 else 3)))
        vel = int(rng.integers(35, 120))
        dur = float(rng.uniform(0.15, 1.2))
        for k in keys:
            ev += [(t, "on", k, vel), (t + dur, "off", k, 64)]
        if rng.random() < 0.1:
            ev += [(t, "cc", 64, 127), (t + 1.5, "cc", 64, 0)]
        t += float(rng.choice([0.125, 0.25, 0.25, 0.375, 0.5, 0.75])) * float(rng.uniform(0.85, 1.2))
    perf = score.from_events(ev)
    inst = {"id": "gm", "engine": "fluidsynth", "file": str(TIMGM), "preset": {"bank": 0, "program": 0}}
    e = engines.create(inst, tmp_path_factory.mktemp("engines"))
    a = e.render(perf, SR)
    return a.mean(axis=1).astype(np.float32)[:30 * SR]


def test_accuracy_against_snr(gm_piece, capsys):
    """Detection accuracy vs SNR on the rendered phrase through the synthetic room (direct + 15 ms reflection + RT60
    0.8 s at DRR -3 dB, band-limited, -45 dBFS, pink noise), true delay 0.4137 s, searched from 0.52 s off."""
    rows = []
    for snr in (20, 10, 5, 0, -5, -10):
        rec = room(gm_piece, 0.4137, snr_db=snr, seed=40 + snr)
        res = C.measure_offline(gm_piece, SR, rec.astype(np.float32) / 32767, SR, rec_offset_s=0.4137 - 0.52)
        live = [r for r in res if not r.get("skip")]
        clear = [r for r in live if r.clear]
        err = np.array([1000 * (r["lag_s"] - 0.52) for r in clear])
        rows.append((snr, len(live), len(clear), float(np.median(err)) if err.size else float("nan"),
                     float(np.max(np.abs(err))) if err.size else float("nan")))
        assert not err.size or np.max(np.abs(err)) < 60.0, (snr, err)         # never a clear wrong peak
        if snr >= 5:
            assert len(clear) >= 0.6 * len(live) and abs(float(np.median(err))) < 5.0, rows[-1]
    with capsys.disabled():
        print("\nSNR dB | windows | clear | median error ms | max |error| ms")
        for r in rows:
            print(f"{r[0]:6d} | {r[1]:7d} | {r[2]:5d} | {r[3]:15.2f} | {r[4]:.2f}")


def test_cli_measure(tmp_path, piece, heard, capsys):
    import soundfile as sf
    rec, d = heard
    sf.write(str(tmp_path / "ref.wav"), piece[:SR * 30], SR, subtype="FLOAT")
    sf.write(str(tmp_path / "rec.wav"), rec[:SR * 31].astype(np.float32) / 32767, SR, subtype="FLOAT")
    assert C.main(["measure", str(tmp_path / "ref.wav"), str(tmp_path / "rec.wav"), "--offset", "0.3"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1].startswith("clear ")
    med = float(out[-1].split("lag median ")[1].split(" ms")[0])
    assert abs(med - 1000 * (d - 0.3)) < 5.0
