"""PC22 output: the player's buffer/callback logic (anywhere); files and streams through the real player and pc22.py
on a fake device (anywhere: no Windows, no sound); and the real thing on PC22 (only with Windows here).

Only digital silence is ever sent to PC22's speakers. The four PC22 device tests (test_windows_*) run only with
PIANO_PC22_DEVICE_TESTS=1 (and Windows here); everything else is logic and runs anywhere:

    chrt -i 0 nice -n 19 ~/.venvs/sm-piano/bin/python -m pytest -q piano_render/tests/test_pc22.py
"""
from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sf = pytest.importorskip("soundfile")
from piano_render import pc22  # noqa: E402


def _load_player():
    spec = importlib.util.spec_from_file_location("pc22_player", ROOT / "piano_render" / "windows" / "pc22_player.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pp = _load_player()
RATE = 48000


def _ti(cur=100.0, dac=100.01):
    return SimpleNamespace(currentTime=cur, outputBufferDacTime=dac)


OK = SimpleNamespace(output_underflow=False)


def _pcm(a: np.ndarray, fmt="f32le") -> bytes:
    return pc22.to_pcm(a, fmt)


def _run_callbacks(player, frames=480, limit=10000):
    """Drives the callback like PortAudio until it stops; returns the concatenated output."""
    outs = []
    for _ in range(limit):
        out = np.full((frames, player.feed.channels), np.nan, np.float32)
        try:
            player.callback(out, frames, _ti(), OK)
        except pp.CallbackStop:
            outs.append(out)
            return np.concatenate(outs), True
        outs.append(out)
    return np.concatenate(outs), False


# ---- the player's logic (no Windows needed) ---------------------------------------------------------------------

def test_header_defaults_and_checks():
    h = pp.parse_header(b'{"rate": 48000, "channels": 2, "format": "s16le", "frames": 10}\n')
    assert h["format"] == "s16le" and h["frames"] == 10 and h["device"] is None
    assert h["prebuffer_ms"] == 300 and h["lead_in_ms"] == 0
    for bad in (b'{"format": "mp3"}\n', b'{"rate": 5}\n', b'{"channels": 0}\n', b"[1]\n", b'{"rate": 48000}'):
        with pytest.raises(ValueError):
            pp.parse_header(bad)


def test_feed_whole_input_completes():
    a = np.random.default_rng(1).uniform(-1, 1, (1000, 2)).astype(np.float32)
    f = pp.Feed(2, "f32le", frames=1000)
    f.run(io.BufferedReader(io.BytesIO(_pcm(a))))
    assert f.complete and not f.stop and not f.short and f.received == 1000
    out = np.zeros((1000, 2), np.float32)
    assert f.fill(out, 1000) == 1000 and np.array_equal(out, a) and f.buffered == 0


def test_feed_odd_tail_is_a_stop_and_frames_past_the_end_are_dropped():
    a = np.zeros((12, 2), np.float32)
    f = pp.Feed(2, "f32le", frames=10)
    f.run(io.BytesIO(_pcm(a) + pc22.STOP_TOKEN))
    assert f.stop and f.complete and f.received == 10 and f.t_stop is not None
    f2 = pp.Feed(2, "s16le", frames=None)
    f2.run(io.BytesIO(_pcm(a[:5], "s16le")[:-1]))        # a torn last frame reads as a stop too
    assert f2.stop and f2.received == 4


def test_feed_short_eof_plays_out():
    f = pp.Feed(2, "f32le", frames=100)
    f.run(io.BytesIO(_pcm(np.zeros((40, 2), np.float32))))
    assert f.complete and f.short and not f.stop and f.received == 40


def test_s16_round_trip_and_clip():
    a = np.array([[0.0, -1.0], [0.5, 1.5], [-0.25, 0.999]], np.float32)
    f = pp.Feed(2, "s16le")
    f.push(pc22.to_pcm(a, "s16le"))
    out = np.zeros((3, 2), np.float32)
    f.fill(out, 3)
    assert np.allclose(out, np.clip(a, -1, 32767 / 32768), atol=1 / 32768)


def test_callback_plays_everything_then_a_silent_tail():
    a = np.random.default_rng(2).uniform(-0.5, 0.5, (1234, 2)).astype(np.float32)
    f = pp.Feed(2, "f32le", frames=1234)
    f.run(io.BytesIO(_pcm(a)))
    p = pp.Player(f, RATE, tail_frames=960)
    out, stopped = _run_callbacks(p)
    assert stopped and p.played == 1234 and p.underruns == 0 and not p.stopped
    assert np.array_equal(out[:1234], a) and not out[1234:].any()
    assert len(out) >= 1234 + 960                         # the tail lets the last frames leave the device
    assert p.first["first_frame"] == 0 and p.started.is_set()


def test_stop_fades_over_10ms():
    f = pp.Feed(1, "f32le")
    f.push(_pcm(np.ones((RATE, 1), np.float32)))
    p = pp.Player(f, RATE, tail_frames=0)
    first = np.zeros((480, 1), np.float32)
    p.callback(first, 480, _ti(), OK)
    assert np.all(first == 1.0)
    f.stop = True
    out, stopped = _run_callbacks(p)
    fade = int(pp.FADE_S * RATE)
    assert stopped and p.stopped and p.played == 960
    assert out[0, 0] == pytest.approx(1.0) and np.all(np.diff(out[:fade, 0]) < 0) and out[fade - 1, 0] < 0.01
    assert not out[fade:].any() and p.underruns == 0
    assert p.fade_dac_end == pytest.approx(100.01 + fade / RATE)


def test_underrun_is_counted_and_padded_with_silence():
    f = pp.Feed(2, "f32le", frames=10000)
    f.push(_pcm(np.ones((700, 2), np.float32)))
    p = pp.Player(f, RATE)
    outs = []
    for _ in range(2):
        o = np.zeros((480, 2), np.float32)
        p.callback(o, 480, _ti(), OK)
        outs.append(o)
    assert p.underruns == 1 and p.underrun_frames == 260 and p.played == 700
    assert np.all(outs[1][:220] == 1) and not outs[1][220:].any()
    p.callback(np.zeros((480, 2), np.float32), 480, _ti(), SimpleNamespace(output_underflow=True))
    assert p.xruns == 1


def test_lead_in_puts_sample_0_later_and_says_so():
    a = np.full((600, 2), 0.25, np.float32)
    f = pp.Feed(2, "f32le", frames=600)
    f.run(io.BytesIO(_pcm(a)))
    p = pp.Player(f, RATE, lead_in_frames=1000)
    out, stopped = _run_callbacks(p)
    assert stopped and p.first["first_frame"] == -1000 and p.underruns == 0
    assert not out[:1000].any() and np.all(out[1000:1600] == 0.25) and not out[1600:].any()


def test_audio0_formula():
    st = {"dac_in_ms": 12.0, "first_frame": -4800}
    assert pc22.audio0(50.0, st, 48000) == pytest.approx(50.0 + 0.012 + 0.1)
    assert pc22.audio0(50.0, {"dac_in_ms": 0.0, "first_frame": 0}, 48000, extra_ms=25) == pytest.approx(50.025)


# ---- streams (and files) through the real player and pc22.py, on a fake device (no Windows, no sound) ------------
#
# The player runs under this Linux Python with a stand-in "sounddevice" module: its OutputStream calls the callback
# every 10 ms of audio in real time and writes what it was given to $FAKE_SD_OUT. Everything else is the real thing:
# pc22.py's writer, pacing and protocol, pc22_player.py's Feed, Player and main().

FAKE_SD = r'''
import os, threading, time
import numpy as np


class CallbackStop(Exception):
    pass


class CallbackAbort(Exception):
    pass


class WasapiSettings:
    def __init__(self, **kw):
        self.kw = kw


def query_hostapis():
    return [{"name": "Windows WASAPI", "default_output_device": 0}]


def query_devices(i=None):
    devs = [{"name": "Fake speakers", "hostapi": 0, "max_output_channels": 2, "default_samplerate": 48000.0}]
    return devs if i is None else devs[i]


class _Status:
    output_underflow = False


class _TI:
    def __init__(self, cur, dac):
        self.currentTime, self.outputBufferDacTime = cur, dac


class OutputStream:
    BLOCK = 480

    def __init__(self, device=None, samplerate=48000, channels=2, dtype="float32", latency=0.02, extra_settings=None,
                 callback=None, finished_callback=None):
        self.rate, self.ch, self.cb, self.fin = int(samplerate), int(channels), callback, finished_callback
        self.latency = max(float(latency or 0), self.BLOCK / self.rate) + 0.01
        self.out = []
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        t, n = time.perf_counter(), 0
        try:
            while not self._stop.is_set():
                buf = np.zeros((self.BLOCK, self.ch), np.float32)
                now = time.perf_counter()
                try:
                    self.cb(buf, self.BLOCK, _TI(now, now + self.latency), _Status())
                except (CallbackStop, CallbackAbort):
                    self.out.append(buf)
                    break
                self.out.append(buf)
                n += self.BLOCK
                d = t + n / self.rate - time.perf_counter()
                if d > 0:
                    time.sleep(d)
        finally:
            p = os.environ.get("FAKE_SD_OUT")
            if p and self.out:
                with open(p, "ab") as f:
                    f.write(np.concatenate(self.out).astype("<f4").tobytes())
            if self.fin:
                self.fin()

    def close(self):
        self._stop.set()

    def abort(self):
        self._stop.set()
'''


@pytest.fixture
def fake_dev(tmp_path, monkeypatch):
    """(PC22Output factory, read_played()): the real player on a fake device; read_played() -> (frames, 2) float32."""
    d = tmp_path / "fake_sd"
    d.mkdir()
    (d / "sounddevice.py").write_text(FAKE_SD)
    out_file = tmp_path / "played.f32"
    monkeypatch.setenv("PYTHONPATH", str(d))
    monkeypatch.setenv("FAKE_SD_OUT", str(out_file))
    monkeypatch.setattr(pc22, "PY_ARGS", ())

    def make(**kw):
        return pc22.PC22Output(py=sys.executable, **kw)

    def played():
        if not out_file.exists():
            return np.zeros((0, 2), np.float32)
        return np.frombuffer(out_file.read_bytes(), "<f4").reshape(-1, 2)
    yield make, played
    assert not pc22.live_pids()


def _ramp(n, start=0):
    x = (np.arange(start, start + n, dtype=np.float64) % 9973) / 9973.0 * 0.5
    return np.stack([x, -x], 1).astype(np.float32)


class PacedSource:
    """A Streamer's shape: blocks(position) yields BLOCK-frame blocks while it is less than ahead_s ahead of
    position(); records how far ahead it ever was, and whether a Halted ended it."""

    def __init__(self, seconds, ahead_s=0.3, sr=RATE, block=512, fail_at=None):
        self.n, self.ahead_s, self.sr, self.block, self.fail_at = int(seconds * sr), ahead_s, sr, block, fail_at
        self.max_ahead = 0.0
        self.halted = False
        self.emitted = 0
        self.positions = []

    def blocks(self, position):
        ahead = self.ahead_s * self.sr
        try:
            while self.emitted < self.n:
                pos = position()
                self.positions.append((time.monotonic(), pos))
                if self.emitted >= pos + ahead:
                    time.sleep(0.002)
                    continue
                if self.fail_at is not None and self.emitted >= self.fail_at:
                    raise RuntimeError("the engine broke")
                k = min(self.block, self.n - self.emitted)
                b = _ramp(k, self.emitted)
                self.emitted += k
                self.max_ahead = max(self.max_ahead, self.emitted - pos)
                yield b
        except pc22.Halted:
            self.halted = True
            raise


def test_a_stream_plays_every_block_in_order_and_ends_by_itself(fake_dev):
    make, played = fake_dev
    parts = [_ramp(n, s) for s, n in ((0, 700), (700, 10000), (10700, 37), (10737, 30000))]   # any sizes
    total = sum(len(p) for p in parts)
    o = make()
    t = time.monotonic()
    t0 = o.start(iter(parts))
    assert o.session.stream and o.session.header["frames"] is None and o.session.prebuffer_ms == 300.0
    assert t < t0 < time.monotonic() + 0.5
    assert o.wait(10)
    d, s = o.done, o.session
    assert d["frames"] == total and d["underruns"] == 0 and not d["stopped"] and not d["short"]
    assert s.eof_sent and s.duration_s == pytest.approx(total / RATE) and s.stream_error is None and s.error is None
    y = played()
    assert np.array_equal(y[:total], np.concatenate(parts)) and not y[total:].any()
    assert s.t_done - t0 > total / RATE - 0.05


def test_a_paced_stream_keeps_its_ahead_and_the_prebuffer_is_that_ahead(fake_dev):
    make, played = fake_dev
    src = PacedSource(1.5, ahead_s=0.3)
    o = make()
    t0 = o.start(src)
    s = o.session
    assert s.prebuffer_ms == pytest.approx(300.0)                 # the source's ahead_s, never more
    assert o.wait(10)
    d = o.done
    assert d["frames"] == src.n and d["underruns"] == 0 and not d["stopped"]
    assert np.array_equal(played()[:src.n], _ramp(src.n))
    assert src.max_ahead <= 0.3 * RATE + 512 + 1
    # position() follows the device: 0 before the start, then the wall clock from t_audio0 (+ the device latency)
    late = [(t, p) for t, p in src.positions if t > t0 + 0.2]
    assert late and all(abs(p - (t - t0 + s.dev_latency_s) * RATE) < 0.01 * RATE for t, p in late)
    assert all(p == 0.0 for t, p in src.positions if t < s.t_started - 0.01)
    assert s.writes < src.n / 512                                  # coalesced into fewer, larger writes


def test_a_stop_mid_stream_fades_and_ends_the_source(fake_dev):
    make, played = fake_dev
    src = PacedSource(30.0, ahead_s=0.3)
    o = make()
    o.start(src)
    time.sleep(0.6)
    t = time.monotonic()
    o.stop()
    assert time.monotonic() - t < 1.0
    d = o.done
    assert d["stopped"] and 0.3 * RATE < d["frames"] < 1.5 * RATE and d["stop_to_silence_ms"] < 100
    assert src.halted and src.emitted < 1.5 * RATE                 # the render stopped too
    with pytest.raises(pc22.Halted):
        o.position()
    assert not o.playing


def test_a_stop_in_the_streams_last_part_still_fades(fake_dev):
    make, played = fake_dev
    o = make()
    o.start(iter([_ramp(int(0.8 * RATE))]))
    s = o.session
    deadline = time.monotonic() + 5
    while not s.eof_sent and time.monotonic() < deadline:
        time.sleep(0.005)
    t_eof = time.monotonic()
    assert s.eof_sent and t_eof >= s.t0 + 0.8 - pc22.END_HOLD_S - 0.06     # held until the end was near
    assert o.wait(3) and o.done["frames"] == int(0.8 * RATE) and o.done["underruns"] == 0


def test_a_source_that_fails_ends_the_stream_and_says_why(fake_dev):
    make, played = fake_dev
    src = PacedSource(5.0, ahead_s=0.3, fail_at=RATE // 2)
    o = make()
    o.start(src)
    assert o.wait(10)
    s = o.session
    assert s.stream_error == "RuntimeError: the engine broke" and s.error is None
    assert o.done["frames"] == src.emitted and not o.done["stopped"]              # what was sent played out
    assert np.array_equal(played()[:src.emitted], _ramp(src.emitted))


def test_a_source_that_fails_at_once_is_a_start_error(fake_dev):
    make, played = fake_dev

    def broken(position):
        raise ValueError("no samples")
        yield                                                    # noqa: unreachable: a generator

    o = make()
    with pytest.raises(pc22.PC22Error, match="no samples"):
        o.start(broken)


def test_the_session_gain_still_scales_a_stream(fake_dev, monkeypatch):
    from piano_render import session
    make, played = fake_dev
    monkeypatch.setattr(pc22, "to_pcm", pc22.to_pcm)            # put back after the test
    assert session.apply_gain(pc22, -120.0) == pytest.approx(1e-6)
    x = np.full((RATE // 2, 2), 0.5, np.float32)
    o = make()
    o.start([x])
    assert o.wait(10) and o.done["frames"] == RATE // 2
    y = played()
    assert y[:RATE // 2].max() == pytest.approx(0.5e-6, rel=1e-3)


def test_a_file_still_plays_as_before(fake_dev, tmp_path):
    make, played = fake_dev
    p = tmp_path / "half.wav"
    x = _ramp(RATE // 2)
    sf.write(str(p), x, RATE, subtype="FLOAT")
    o = make()
    o.start(p, lead_in_ms=100)
    assert not o.session.stream and o.session.header["frames"] == RATE // 2 and o.session.prebuffer_ms == 300.0
    assert o.started["first_frame"] == -RATE // 10
    assert o.wait(10) and o.done["frames"] == RATE // 2 and o.done["underruns"] == 0
    y = played()
    assert not y[:RATE // 10].any() and np.array_equal(y[RATE // 10:RATE // 10 + RATE // 2], x)


def test_a_real_streamer_on_the_fake_device(fake_dev, gm_registry):
    """piano_render.stream.Streamer through PC22Output: the position contract end to end (FluidSynth, no realism)."""
    from piano_render import score, stream
    make, played = fake_dev
    perf = score.from_events([(0.05, "on", 60, 100), (0.4, "on", 64, 90), (0.9, "off", 60, 64), (1.0, "off", 64, 64)])
    st = stream.Streamer(perf, "gm", registry=gm_registry, ahead_s=0.3, realism=False)
    try:
        st.open()
        o = make()
        o.start(st)
        assert o.session.prebuffer_ms == pytest.approx(300.0)
        assert o.wait(30)
        n = o.done["frames"]
        assert n == o.session.sent and abs(n - st.stats["audio_s"] * RATE) <= RATE // 1000     # (audio_s: ms)
        assert o.done["underruns"] == 0 and st.stats["late_blocks"] == 0
        assert np.abs(played()[:n]).max() > 0.01                 # it played the piano
    finally:
        st.close()


def test_position_raises_once_halted():
    s = pc22._Session(iter([]), py=sys.executable, device=None, prebuffer_ms=None, latency_ms=20, fmt="f32le",
                      lead_s=2.0, buffer_ms=None, lead_in_ms=0, extra_ms=0)
    assert s.position() == 0.0 and s.rate == pc22.STREAM_RATE
    s.halt.set()
    with pytest.raises(pc22.Halted):
        s.position()


# ---- the real thing on PC22 (digital silence only) ----------------------------------------------------------------

DEVICE_ENV = "PIANO_PC22_DEVICE_TESTS"
windows = pytest.mark.skipif(not (pc22.available() and os.environ.get(DEVICE_ENV) == "1"),
                             reason=f"PC22's audio device: only with Windows here and {DEVICE_ENV}=1")


@pytest.fixture(scope="module")
def silent3(tmp_path_factory):
    p = tmp_path_factory.mktemp("pc22") / "silent3.flac"
    sf.write(str(p), np.zeros((3 * RATE, 2), np.float32), RATE, subtype="PCM_24")
    return p


def _alive_windows(pids):
    out = subprocess.run(["/mnt/c/Windows/System32/tasklist.exe", "/FO", "CSV", "/NH"], capture_output=True,
                         text=True, timeout=20).stdout
    running = {line.split('","')[1] for line in out.splitlines() if line.count('","') >= 2}
    return [p for p in pids if str(p) in running]


def _my_children():
    me = str(os.getpid())
    kids = []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                stat = open(f"/proc/{d}/stat").read()
            except OSError:
                continue
            if stat[stat.rindex(")") + 2:].split()[1] == me:
                kids.append(int(d))
    return kids


@windows
def test_windows_silence_end_to_end(silent3):
    o = pc22.PC22Output()
    t = time.monotonic()
    t0 = o.start(silent3)
    back = time.monotonic()
    assert back - t < 10
    assert o.ready["rate"] == RATE and o.ready["latency_ms"] > 0 and o.ready["pid"] > 0
    st = o.started
    assert st["first_frame"] == 0 and -5 < st["dac_in_ms"] < 200 and st["since_cb_ms"] < 50
    assert abs(st["perf_minus_cur_ms"]) < 5                  # PortAudio's clock is perf_counter
    assert back - 0.1 < t0 < back + 0.5
    assert o.wait(15)
    d = o.done
    assert d["frames"] == 3 * RATE and d["underruns"] == 0 and not d["stopped"] and not d["short"]
    assert 2.9 < o.session.t_done - t0 < 3.6                  # 3 s, then the device's buffer of silence
    assert o.session.proc.returncode == 0 and not pc22.live_pids()


@windows
def test_windows_stop_midway(silent3):
    o = pc22.PC22Output()
    o.start(silent3)
    time.sleep(0.8)
    t = time.monotonic()
    o.stop()
    assert time.monotonic() - t < 1.5
    d = o.done
    assert d["stopped"] and 0.5 * RATE < d["frames"] < 1.6 * RATE
    assert d["stop_to_silence_ms"] is not None and d["stop_to_silence_ms"] < 100
    assert o.session.proc.returncode == 0 and not o.playing


@windows
def test_windows_repeated_start_stop_leaves_nothing_behind(silent3):
    pids = []
    o = pc22.PC22Output()
    for i in range(3):
        o.start(silent3)
        pids.append(o.ready["pid"])
        time.sleep(0.2)
        o.stop()
    o.start(silent3)                                          # a start while playing stops the previous file
    pids.append(o.ready["pid"])
    first = o.session
    o.start(silent3, fmt="s16le", lead_in_ms=500)
    pids.append(o.ready["pid"])
    assert first.exited.is_set() and first.done["stopped"]
    assert o.started["first_frame"] == -RATE // 2
    with o:
        pass                                                  # leaving the context stops it
    assert o.done["stopped"]
    assert not pc22.live_pids()
    time.sleep(0.3)
    assert _alive_windows(pids) == []
    assert not [k for k in _my_children()
                if "py.exe" in (open(f"/proc/{k}/cmdline").read() if os.path.exists(f"/proc/{k}/cmdline") else "")]


@windows
def test_windows_play_helper(tmp_path):
    p = tmp_path / "silent1.wav"
    sf.write(str(p), np.zeros((RATE, 1), np.float32), RATE)      # mono WAV: sent as stereo
    r = pc22.play(p)
    assert r["error"] is None and r["done"]["frames"] == RATE and r["startup_s"] < 10
    assert r["ready"]["channels"] == 2
