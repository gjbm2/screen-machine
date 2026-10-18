"""scope_feed.py: the fast scope / FFT feed (levels frames -> ScopeState -> Sound's GPU copies)."""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from stream_service import scope_feed as sf
from stream_service.scope_feed import FFT_BANDS, FFT_HIST, WAVE_N, ScopeFeed, ScopeState, levels_url, parse_frame

DT = 1.0 / 60.0


def frame_json(**over) -> str:
    body = {"s": "room-mic", "c": 3, "n": 2, "pk": -20.0, "t": None, "a": 100.0,
            "f": [-40] * FFT_BANDS, "fl": 40.0, "fh": 16000, "w": [1000] * WAVE_N}
    body.update(over)
    return json.dumps({k: v for k, v in body.items() if v is not ...})


def sine(n=WAVE_N, cycles=10.0, amp=0.5, phase=0.0, sr=None):
    return (amp * np.sin(2 * np.pi * cycles * np.arange(n) / n + phase)).astype(np.float32)


def live(now, wave=None, bands=None, pk=-20.0):
    return (now, wave, bands, pk)


# -- URL derivation ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bus,want", [
    ("ws://127.0.0.1:8703/v1/show", "ws://127.0.0.1:8703/v1/levels?wave=1024"),
    ("wss://screen.example:443/rs/show/v1/show", "wss://screen.example:443/rs/show/v1/levels?wave=1024"),
    ("ws://10.0.0.5:8703", "ws://10.0.0.5:8703/v1/levels?wave=1024"),
    ("ws://10.0.0.5:8703/", "ws://10.0.0.5:8703/v1/levels?wave=1024"),
    ("wss://host/other/path?x=1", "wss://host/v1/levels?wave=1024"),
    ("http://host:8703/v1/show", "ws://host:8703/v1/levels?wave=1024"),
])
def test_levels_url(bus, want):
    assert levels_url(bus) == want


@pytest.mark.parametrize("bus", [None, "", "ftp://x/v1/show", "not a url", 5])
def test_levels_url_none(bus):
    assert levels_url(bus) is None


def test_from_music_bus():
    assert ScopeFeed.from_music_bus([]) is None
    assert ScopeFeed.from_music_bus(()) is None
    f = ScopeFeed.from_music_bus(["ws://127.0.0.1:8703/v1/show", "ws://other:1/v1/show"])
    assert f.url == "ws://127.0.0.1:8703/v1/levels?wave=1024"
    assert ScopeFeed.from_music_bus("ws://h:2/v1/show").url == "ws://h:2/v1/levels?wave=1024"


# -- frame parsing -------------------------------------------------------------------------------------------------
def test_parse_good():
    fr = parse_frame(frame_json(), 100.01)
    arrival, wave, bands, pk = fr
    assert arrival == 100.0 and pk == -20.0
    assert wave.dtype == np.float32 and wave.shape == (WAVE_N,) and wave[0] == pytest.approx(1000 / 32767)
    assert bands.dtype == np.float32 and bands.shape == (FFT_BANDS,) and bands[0] == -40
    assert not wave.flags.writeable and not bands.flags.writeable


def test_parse_missing_optional_fields():
    fr = parse_frame(frame_json(w=..., f=..., a=...), 50.0)
    assert fr == (50.0, None, None, -20.0)                         # our receipt time without ``a``
    short = parse_frame(frame_json(w=[32767] * 10), 100.0)[1]
    assert short.shape == (WAVE_N,) and (short[:-10] == 0).all() and short[-1] == pytest.approx(1.0)
    long_ = parse_frame(frame_json(w=list(range(2048))), 100.0)[1]
    assert long_.shape == (WAVE_N,) and long_[-1] == pytest.approx(2047 / 32767)


def test_parse_implausible_arrival_uses_local():
    assert parse_frame(frame_json(a=1e9), 100.0)[0] == 100.0       # another clock
    assert parse_frame(frame_json(a=1.0), 100.0)[0] == 100.0       # far in the past


@pytest.mark.parametrize("raw", [
    "not json", "[1, 2]", "null", json.dumps({"pong": 1, "srv": 2}),
    frame_json(pk=...), frame_json(pk="loud"), frame_json(pk=True),
    frame_json(f=[1, 2, 3]), frame_json(f="x"), frame_json(f=["a"] * FFT_BANDS),
    frame_json(w=[]), frame_json(w="x"), frame_json(w=[[1, 2]]), frame_json(w=["a", "b"]),
])
def test_parse_malformed(raw):
    assert parse_frame(raw, 100.0) is None


def test_feed_on_text_counts():
    f = ScopeFeed("ws://h/v1/levels?wave=1024")
    assert f.on_text(frame_json(fl=50.0, fh=12000), 100.0)
    assert f.latest[0] == 100.0 and f.frames == 1 and (f.band_lo_hz, f.band_hi_hz) == (50.0, 12000.0)
    first = f.latest
    assert not f.on_text("garbage", 100.0) and f.bad == 1 and f.latest is first
    assert not f.on_text(json.dumps({"pong": 1, "srv": 2}), 100.0) and f.bad == 1
    st = f.stats()
    assert st["frames"] == 1 and st["bad"] == 1 and st["wave"] and st["bands"] and st["reconnects"] == 0
    json.dumps(st)


# -- ScopeState ----------------------------------------------------------------------------------------------------
def test_fresh_state_is_zero_and_dt0_safe():
    s = ScopeState()
    for dt in (0.0, -1.0, float("nan"), float("inf"), "x"):
        s.update(10.0, None, None, dt)
    assert not s.wave.any() and not s.fft.any() and s.level == 0.0 and not s.live and s.errors == 0
    assert s.fft_row == 0 and s.wave_trigger == 0


def test_agc_bounds_and_quiet_room():
    s = ScopeState()
    now = 0.0
    quiet = sine(amp=0.001)
    for _ in range(600):                              # 10 s of a very quiet room: gain rises to its cap
        now += DT
        s.update(now, live(now, quiet), None, DT)
    assert s.gain == pytest.approx(sf.AGC_MAX, rel=0.01) and s.gain <= sf.AGC_MAX
    assert 0.02 < float(np.abs(s.wave).max()) < 0.05          # 0.001 x 40: visible
    loud = sine(amp=1.0)
    for _ in range(600):
        now += DT
        s.update(now, live(now, loud), None, DT)
    assert s.gain == pytest.approx(sf.AGC_MIN, abs=1e-3)
    assert float(np.abs(s.wave).max()) <= sf.SOFT_CLIP + 1e-6
    mid = sine(amp=0.1)
    for _ in range(900):                              # converges to the 0.7 target peak
        now += DT
        s.update(now, live(now, mid), None, DT)
    assert float(np.abs(s.wave).max()) == pytest.approx(sf.SOFT_CLIP * math.tanh(0.7 / sf.SOFT_CLIP), rel=0.05)


def test_agc_slow_rise():
    s = ScopeState()
    s.update(0.0, live(0.0, sine(amp=0.07)), None, 0.0)
    s.update(1.0 / 60, live(1.0 / 60, sine(amp=0.07)), None, 1.0 / 60)
    assert s.gain < 1.2                               # target 10, but it eases over seconds


def test_dc_removed():
    s = ScopeState()
    w = (sine(amp=0.2) + 0.5).astype(np.float32)
    now = 0.0
    for _ in range(120):
        now += DT
        s.update(now, live(now, w), None, DT)
    assert abs(float(s.wave.mean())) < 0.02


def test_stale_eases_to_zero():
    s = ScopeState()
    now = 0.0
    fr = None
    for _ in range(60):
        now += DT
        fr = live(now, sine(amp=0.5))
        s.update(now, fr, None, DT)
    full = float(np.abs(s.wave).max())
    assert s.live and full > 0.3
    now += 0.6                                        # the feed stops: the frame goes stale
    s.update(now, fr, None, 0.0)
    assert not s.live and float(np.abs(s.wave).max()) == pytest.approx(full)   # no snap at dt 0
    s.update(now + 0.1, fr, None, 0.1)
    mid = float(np.abs(s.wave).max())
    assert 0.0 < mid < full
    s.update(now + 0.2, fr, None, 0.1)
    s.update(now + 0.3, fr, None, 0.1)
    assert not s.wave.any()


def test_trigger_finds_rising_crossing():
    s = ScopeState()
    w = sine(cycles=8.0, amp=0.5, phase=1.0)          # rising crossings where phase = 2 pi k
    now = 0.0
    for _ in range(3):
        now += DT
        s.update(now, live(now, w), None, DT)
    i = s.wave_trigger
    assert 0 < i < WAVE_N // 2
    assert s.wave[i - 1] < 0.0 <= s.wave[i]
    period = WAVE_N / 8.0
    expect = [k * period - 1.0 / (2 * np.pi) * period for k in range(1, 9)]
    expect = [e for e in expect if e < WAVE_N // 2]
    assert abs(i - expect[-1]) <= 1.5                 # the latest one in the first half
    s2 = ScopeState()
    s2.update(1.0, live(1.0, np.zeros(WAVE_N, np.float32)), None, DT)
    assert s2.wave_trigger == 0


def test_trigger_hysteresis_ignores_noise():
    s = ScopeState()
    rng = np.random.default_rng(1)
    w = sine(cycles=4.0, amp=0.5)
    noisy = (w + rng.normal(0, 0.002, WAVE_N)).astype(np.float32)
    s.update(1.0, live(1.0, noisy), None, DT)
    i = s.wave_trigger
    # noise near the falling crossings (pi) never triggers: the result is near a true rising crossing (0, 256, ...)
    assert min(abs(i - k * 256) for k in range(3)) <= 2


def test_fft_mapping_attack_release():
    s = ScopeState()
    loud = np.full(FFT_BANDS, -12.0, np.float32)
    s.update(1.0, live(1.0, None, loud), None, DT)
    assert np.allclose(s.fft, 1.0)                    # instant attack
    quiet = np.full(FFT_BANDS, -80.0, np.float32)
    s.update(1.0 + 0.12, live(1.0 + 0.12, None, quiet), None, 0.12)
    assert np.allclose(s.fft, math.exp(-1.0), atol=1e-4)    # one tau
    s.update(1.24, live(1.24, None, quiet), None, 0.0)      # dt 0: unchanged
    assert np.allclose(s.fft, math.exp(-1.0), atol=1e-4)
    s.update(1.3, live(1.3, None, quiet), None, 1000.0)     # a huge dt: straight to the target
    assert np.allclose(s.fft, 0.0)
    mid = np.full(FFT_BANDS, -46.0, np.float32)
    s.update(1.31, live(1.31, None, mid), None, DT)
    assert np.allclose(s.fft, 0.5)
    over = np.full(FFT_BANDS, 5.0, np.float32)
    s.update(1.32, live(1.32, None, over), None, DT)
    assert np.allclose(s.fft, 1.0)


def test_level_from_peak():
    s = ScopeState()
    s.update(1.0, live(1.0, None, None, pk=-46.0), None, DT)
    assert s.level == pytest.approx(0.5)
    s.update(1.0, live(1.0, None, None, pk=-120.0), None, 1000.0)
    assert s.level == pytest.approx(0.0)


def test_peak_hold_then_fall():
    s = ScopeState()
    t = 0.0
    s.update(t, live(t, None, np.full(FFT_BANDS, -12.0, np.float32)), None, DT)
    assert np.allclose(s.fft_peak, 1.0)
    silent = np.full(FFT_BANDS, -120.0, np.float32)
    for _ in range(int(0.38 / 0.01)):                 # within the hold
        t += 0.01
        s.update(t, live(t, None, silent), None, 0.01)
    assert np.allclose(s.fft_peak, 1.0)
    for _ in range(12):                               # past 0.4 s: 0.1 s of fall at 1.5/s
        t += 0.01
        s.update(t, live(t, None, silent), None, 0.01)
    assert np.allclose(s.fft_peak, 1.0 - 1.5 * 0.1, atol=0.02)
    for _ in range(100):
        t += 0.01
        s.update(t, live(t, None, silent), None, 0.01)
    assert np.allclose(s.fft_peak, 0.0, atol=1e-4)    # it rests on the released fft
    assert (s.fft_peak >= s.fft - 1e-6).all()


def test_history_cadence_and_catchup_cap():
    s = ScopeState()
    t = 0.0
    bands = np.full(FFT_BANDS, -46.0, np.float32)
    rows = 0
    for _ in range(120):                              # 2 s at 60 fps: 60 rows
        t += DT
        s.update(t, live(t, None, bands), None, DT)
        rows += s.rows_written
    assert rows in (59, 60, 61) and s.fft_row == rows % FFT_HIST
    assert np.allclose(s.fft_history[s.fft_row], s.fft)
    row0 = s.fft_row
    t += 5.0                                          # a 5 s stall: at most 4 rows, then the cadence resumes
    s.update(t, live(t, None, bands), None, 5.0)
    assert s.rows_written == sf.HIST_CATCHUP and s.fft_row == (row0 + 4) % FFT_HIST
    t += DT
    s.update(t, live(t, None, bands), None, DT)
    assert s.rows_written in (0, 1)
    s.update(t, live(t, None, bands), None, 0.0)
    assert s.rows_written == 0


def test_fallback_from_spectrum32():
    s = ScopeState()
    spec = np.linspace(0.0, 1.0, 32).astype(np.float32)
    s.update(10.0, None, spec, DT)
    assert not s.live
    assert s.fft.shape == (FFT_BANDS,) and 0.0 <= s.fft.min() and s.fft.max() <= 1.0
    assert (np.diff(s.fft) >= -1e-6).all() and s.fft[-1] > 0.9 and s.fft[0] < 0.1   # follows the bands' slope
    assert s.level == pytest.approx(float(s.fft.max()))
    assert not s.wave.any()
    st = ScopeState()                                 # a stale fast frame also falls back
    st.update(10.0, live(1.0, sine(), np.full(FFT_BANDS, -12.0, np.float32)), np.full(32, 0.25, np.float32), DT)
    assert not st.live and np.allclose(st.fft, 0.25) and not st.wave.any()
    sb = ScopeState()                                 # a bad fallback: silence, no error
    sb.update(10.0, None, [1, 2, 3], DT)
    sb.update(10.0, None, "x", DT)
    assert not sb.fft.any() and sb.errors == 0


def test_never_raises_on_garbage_frame():
    s = ScopeState()
    s.update(1.0, live(1.0, sine(), np.full(FFT_BANDS, -40.0, np.float32)), None, DT)
    before = s.fft.copy()
    s.update(1.01, ("x", "y", "z", "w"), None, DT)    # nonsense: logged once, last values kept
    s.update(1.02, (1.02, "bad"), None, DT)
    assert s.errors == 2 and np.allclose(s.fft, before)


# -- Sound.update with a fake cp ----------------------------------------------------------------------------------
class FakeArr(np.ndarray):
    sets = 0

    def set(self, a):
        assert a.shape == self.shape and a.dtype == self.dtype
        self[...] = a
        FakeArr.sets += 1


class FakeCp:
    float32 = np.float32

    @staticmethod
    def zeros(shape, dtype):
        return np.zeros(shape, dtype).view(FakeArr)


def test_sound_update_with_fake_cp():
    from stream_service.sound import new_sound

    feed = ScopeFeed("ws://h/v1/levels?wave=1024")
    snd = new_sound(feed=feed)
    assert snd is not None
    snd.update(0.0)                                   # before alloc: no GPU arrays, no error
    snd.alloc(FakeCp)
    assert snd.wave_gpu.shape == (WAVE_N,) and snd.fft_history_gpu.shape == (FFT_HIST, FFT_BANDS)
    snd.update(0.1)
    assert not snd.scope_live and snd.fft_row == 0 and snd.level == 0.0
    import time
    w = (np.sin(np.arange(WAVE_N) / 8.0) * 8000).astype(int).tolist()
    for _ in range(5):
        feed.on_text(frame_json(a=time.monotonic(), w=w, f=[-30] * FFT_BANDS))
        snd.update(0.2)
    assert snd.scope_live and snd.fft.max() > 0.7 and np.allclose(np.asarray(snd.fft_gpu), snd.fft)
    assert np.allclose(np.asarray(snd.wave_gpu), snd.wave) and snd.wave.any()
    assert isinstance(snd.wave_trigger, int) and isinstance(snd.fft_row, int)
    feed.latest = ("broken",)                         # garbage in the slot never raises into the frame
    snd.update(0.3)
    assert snd.notes_gpu.shape == (128, 4)


def test_sound_without_feed():
    from stream_service.sound import new_sound

    snd = new_sound()
    snd.alloc(FakeCp)
    snd.frame.spectrum[:] = 0.5                       # a stand-in for the smoothed spectrum
    snd.view.update = lambda ms: snd.frame            # keep it (the view would refresh it from messages)
    for k in range(3):
        snd.update(float(k))
    assert not snd.scope_live and np.allclose(snd.fft, 0.5) and not snd.wave.any()
