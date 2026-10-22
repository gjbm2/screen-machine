"""PC22 output: the player's buffer/callback logic (anywhere) and the real thing on PC22 (only with Windows here).

Only digital silence is ever sent to PC22's speakers.

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


# ---- the real thing on PC22 (digital silence only) ----------------------------------------------------------------

windows = pytest.mark.skipif(not pc22.available(), reason="no Windows here (WSL interop, /mnt/c/Windows/py.exe)")


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
def test_silence_end_to_end(silent3):
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
def test_stop_midway(silent3):
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
def test_repeated_start_stop_leaves_nothing_behind(silent3):
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
def test_play_helper(tmp_path):
    p = tmp_path / "silent1.wav"
    sf.write(str(p), np.zeros((RATE, 1), np.float32), RATE)      # mono WAV: sent as stereo
    r = pc22.play(p)
    assert r["error"] is None and r["done"]["frames"] == RATE and r["startup_s"] < 10
    assert r["ready"]["channels"] == 2
