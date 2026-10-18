#!/usr/bin/env python3
"""Make a synthetic calibration run with KNOWN degradation, to test analyse.py before a real run exists.

From one test-music track (~/.cache/screen-machine/test-music, index.json) it builds a run directory exactly like
calibrate.py's (rec.wav, rec.json, run.json, original/), plus truth.json with every planted number:
  - the player starts --trim s into the original (default 2 s) and plays --seconds of it (default 150);
  - the recording's clock runs --drift-ppm long (default 90 ppm);
  - EQ: a 120 Hz high-pass (2nd-order Butterworth) and a +6 dB peak at 2 kHz (Q 1);
  - a synthetic room IR: direct sound 3 ms late, six early reflections, an exponential diffuse tail
    (RT60 --rt60, default 0.5 s, frequency-independent), DRR about 0 dB;
  - room tone: pink noise at -55 dBFS RMS plus 50 Hz hum (-72 dBFS) and 100 Hz (-80 dBFS);
  - 10 s of 'talk' (speech-band noise, syllabic modulation) at 60 s into the music, -36 dBFS RMS;
  - a feed-forward compressor on the mic signal: threshold -32 dBFS, 3:1, attack 10 ms, release 200 ms (1 ms RMS
    blocks), so the room tone (under the threshold) is untouched;
  - 7.3 s of room tone before the music, 4 s after; INT16 quantisation.
--bus also fabricates bus.jsonl: music.clock at 10 Hz following Beat This!'s beats of the original (searching for
the first 6 s, dmx for 2 s, then beat; an 8 s stretch at 90 s 'holding' 120 ms off phase with gate none) and
music.hit from dsp's own hits on the recording, every band (1 ms jitter, 10 % dropped, 5 % spurious), on a host clock offset by
H0 ms and running 25 ppm fast.

Usage:
  flock ~/.cache/screen-machine/cpu.lock nice -n 19 ~/.venvs/sm-roundtrip/bin/python tools/room/audio_cal/synth_run.py \\
      [--track house-galeria-disco-memorias] [--seconds 150] [--bus]
  ... then: analyse.py <printed run dir> --no-config
"""
from __future__ import annotations

import os

for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_k, "1")

import argparse
import datetime as dt
import json
import math
import pathlib
import shutil
import subprocess
import sys

import numpy as np
import scipy.signal as sps
import soundfile as sf
import soxr

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyse  # noqa: E402
import roomsim  # noqa: E402

FS = 48000
MUSIC = pathlib.Path.home() / ".cache" / "screen-machine" / "test-music"
OUT_ROOT = pathlib.Path.home() / ".cache" / "screen-machine" / "audio_cal_synth"


def decode(path: pathlib.Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2", "-ar", str(FS), "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).astype(np.float64)


def peaking(f0: float, gain_db: float, q: float):
    """RBJ peaking EQ biquad (b, a)."""
    A = 10 ** (gain_db / 40.0)
    w = 2 * math.pi * f0 / FS
    al = math.sin(w) / (2 * q)
    b = [1 + al * A, -2 * math.cos(w), 1 - al * A]
    a = [1 + al / A, -2 * math.cos(w), 1 - al / A]
    return np.array(b) / a[0], np.array(a) / a[0]


def pink(n: int, rng) -> np.ndarray:
    X = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1.0 / FS)
    X /= np.sqrt(np.maximum(f, 20.0))
    x = np.fft.irfft(X, n)
    return x / np.sqrt(np.mean(x ** 2))


def room_ir(rt60: float, rng) -> tuple[np.ndarray, dict]:
    n = int(1.2 * rt60 * FS) + int(0.01 * FS)
    h = np.zeros(n)
    d0 = int(0.003 * FS)
    h[d0] = 1.0
    for ms, g in ((4.1, 0.55), (6.8, -0.45), (9.7, 0.38), (13.2, -0.3), (17.5, 0.25), (23.0, -0.2)):
        h[d0 + int(ms * FS / 1000)] += g
    t = np.arange(n - d0) / FS
    tail = rng.standard_normal(n - d0) * np.exp(-6.91 * t / rt60) * np.clip((t - 0.005) / 0.01, 0, 1)
    direct_e = 1.0
    early_e = float(np.sum(h ** 2)) - direct_e
    want_rev = direct_e * 1.0                      # DRR 0 dB: reverberant (early + tail) = direct
    tail *= math.sqrt(max(want_rev - early_e, 1e-6) / float(np.sum(tail ** 2)))
    h[d0:] += tail
    drr = 10 * math.log10(direct_e / (float(np.sum(h ** 2)) - direct_e))
    return h, {"rt60_s": rt60, "direct_ms": 3.0, "drr_db": drr}


def compressor(x: np.ndarray, thr_db: float, ratio: float, att_s: float, rel_s: float) -> tuple[np.ndarray, np.ndarray]:
    blk = int(0.001 * FS)
    nb = len(x) // blk
    lv = 10 * np.log10(np.mean(x[:nb * blk].reshape(nb, blk) ** 2, axis=1) + 1e-20)
    det = roomsim.detector(lv, blk / FS, att_s, rel_s)
    g_db = -(1 - 1 / ratio) * np.maximum(0.0, det - thr_db)
    g = np.interp(np.arange(len(x)), np.arange(nb) * blk + blk / 2, 10 ** (g_db / 20.0))
    return x * g, g_db


def talk(n: int, rng) -> np.ndarray:
    sos = sps.butter(4, [200, 3500], btype="bandpass", fs=FS, output="sos")
    x = sps.sosfilt(sos, rng.standard_normal(n))
    t = np.arange(n) / FS
    syl = np.abs(np.sin(2 * math.pi * 2.0 * t + rng.uniform(0, 6))) ** 2
    gaps = np.repeat(rng.random(int(n / FS * 3) + 2) > 0.25, int(FS / 3) + 1)[:n]
    env = sps.sosfilt(sps.butter(2, 20, fs=FS, output="sos"), syl * gaps)
    y = x * np.maximum(env, 0)
    return y / np.sqrt(np.mean(y ** 2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--track", default="house-galeria-disco-memorias")
    ap.add_argument("--seconds", type=float, default=150.0)
    ap.add_argument("--trim", type=float, default=2.0)
    ap.add_argument("--drift-ppm", type=float, default=90.0)
    ap.add_argument("--rt60", type=float, default=0.5)
    ap.add_argument("--pre-roll", type=float, default=7.3)
    ap.add_argument("--bus", action="store_true", help="fabricate bus.jsonl (runs Beat This! on the original)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out-root", default=str(OUT_ROOT))
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    index = json.loads((MUSIC / "index.json").read_text(encoding="utf-8"))
    meta = index[a.track]
    src = MUSIC / meta["file"]
    now = dt.datetime.now(dt.timezone.utc)
    run = now.strftime("%Y%m%dT%H%M%SZ") + "-synth-mic"
    rd = pathlib.Path(a.out_root) / run
    (rd / "original").mkdir(parents=True)
    shutil.copy(src, rd / "original" / src.name)
    orig = decode(src)
    sf.write(str(rd / "original" / "original-48k.wav"), orig.astype(np.float32), FS, subtype="FLOAT")

    # what the PA played: mono mix from trim, for --seconds
    i0 = int(a.trim * FS)
    played = orig.mean(axis=1)[i0:i0 + int(a.seconds * FS)]
    # the recording clock runs long by drift_ppm
    played = soxr.resample(played, FS, FS * (1 + a.drift_ppm * 1e-6), quality="VHQ")
    # the PA + room: EQ, IR
    sos = sps.butter(2, 120.0, btype="highpass", fs=FS, output="sos")
    y = sps.sosfilt(sos, played)
    y = sps.lfilter(*peaking(2000.0, 6.0, 1.0), y)
    h, ir_truth = room_ir(a.rt60, rng)
    y = sps.oaconvolve(y, h)
    y *= 10 ** ((-28.0 - 10 * math.log10(np.mean(y ** 2))) / 20.0)     # music at about -28 dBFS RMS
    pre_n, post_n = int(a.pre_roll * FS), int(4.0 * FS)
    n = pre_n + len(y) + post_n
    mic = np.zeros(n)
    mic[pre_n:pre_n + len(y)] += y
    t0_talk = a.pre_roll + 60.0
    if a.seconds > 75:
        tk = talk(int(10 * FS), rng) * 10 ** (-36 / 20)
        s = int(t0_talk * FS)
        mic[s:s + len(tk)] += tk
    tt = np.arange(n) / FS
    room = pink(n, rng) * 10 ** (-55 / 20)
    hum = 10 ** (-72 / 20) * math.sqrt(2) * np.sin(2 * math.pi * 50 * tt) + \
        10 ** (-80 / 20) * math.sqrt(2) * np.sin(2 * math.pi * 100 * tt + 0.3)
    mic += room + hum
    noise_dbfs = 10 * math.log10(np.mean((room + hum) ** 2))
    mic, _ = compressor(mic, -32.0, 3.0, 0.010, 0.200)
    rec = roomsim.quantise_int16(mic)
    sf.write(str(rd / "rec.wav"), rec, FS, subtype="FLOAT")
    started = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    stopped = (now + dt.timedelta(seconds=n / FS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (rd / "rec.json").write_text(json.dumps({"device": "synthetic", "fs": FS, "channels": 1, "frames": n,
                                             "overflows": 0, "started_utc": started, "stopped_utc": stopped,
                                             "synthetic": True}, indent=1) + "\n", encoding="utf-8")
    offset_s = a.pre_roll - a.trim * (1 + a.drift_ppm * 1e-6) + 0.003
    truth = {"track": a.track, "trim_s": a.trim, "seconds": a.seconds, "pre_roll_s": a.pre_roll,
             "offset_s": offset_s, "drift_ppm": a.drift_ppm,
             "eq": {"highpass_hz": 120.0, "highpass_order": 2, "peak_hz": 2000.0, "peak_db": 6.0, "peak_q": 1.0},
             "ir": ir_truth, "noise_dbfs": noise_dbfs, "noise_pink_dbfs": -55.0, "hum": {"50": -72.0, "100": -80.0},
             "talk": [t0_talk, t0_talk + 10.0] if a.seconds > 75 else None, "talk_dbfs": -36.0,
             "music_rms_dbfs_before_compressor": -28.0,
             "compressor": {"threshold_dbfs": -32.0, "ratio": 3.0, "attack_ms": 10.0, "release_ms": 200.0,
                            "detector": "1 ms RMS blocks"}}
    run_json = {"run": run, "stream": "synth-mic", "device": "synthetic (synth_run.py)", "started_utc": started,
                "stopped_utc": stopped, "synthetic": True,
                "original": {"source": f"test-music:{a.track}", "title": meta.get("title"),
                             "file": f"original/{src.name}", "wav": "original/original-48k.wav"}}
    if a.bus:
        host0 = 8.5e9                       # host ms at recording t = 0
        rate = 1 + 25e-6                    # host ms per recording ms
        to_host = lambda t_rec: host0 + t_rec * 1000.0 * rate    # noqa: E731
        work = rd / "work"
        work.mkdir()
        res = analyse.run_trackers({"beatthis": {"orig": orig.mean(axis=1)}, "dsphits": {"rec": rec}}, work, print)
        bt = res["beatthis"].get("orig")
        if bt is None:
            raise SystemExit(f"Beat This! failed: {res['errors']}")
        dh = res["dsphits"].get("rec")
        if dh is None:
            raise SystemExit(f"dsp hits failed: {res['errors']}")
        u = np.asarray(bt["beats"])
        u = u[(u >= a.trim) & (u <= a.trim + a.seconds)]
        tb = a.pre_roll + (u - a.trim) * (1 + a.drift_ppm * 1e-6) + 0.003      # true beats, recording s
        lines = []
        hits = {band: np.asarray(ts) for band, ts in dh["hits"].items()}    # dsp's own hits on the recording
        for band, ts in hits.items():
            keep = ts[rng.random(len(ts)) > 0.10]
            spur = rng.uniform(0, n / FS, int(0.05 * len(ts)))
            for t in np.concatenate([keep + rng.normal(0, 0.001, len(keep)), spur]):
                lines.append((to_host(t), {"type": "music.hit", "data": {"t_ms": to_host(t), "band": band,
                                                                          "strength": 0.6, "stream": "synth-mic"}}))
        music0 = a.pre_roll
        jit = rng.normal(0, 0.004, len(tb))
        epoch = 0
        for t in np.arange(music0, n / FS, 0.1):
            k = int(np.searchsorted(tb, t)) - 1
            el = t - music0
            if k < 1 or el < 6.0:
                body = {"bpm": 0.0 if el < 3 else 92.0, "anchor_ms": to_host(t), "beat_at_anchor": 0,
                        "beats_per_bar": 4, "bar_anchor_beat": 0, "phrase_bars": 4, "conf": 0.1, "bar_conf": 0,
                        "phrase_conf": 0, "state": "searching", "gate": "none", "epoch": 0, "stability": 0.1,
                        "source": "madmom"}
            else:
                kk = min(k, len(tb) - 2)
                per = tb[kk + 1] - tb[kk]
                hold = 90.0 <= el < 98.0
                shift = 0.12 if hold else 0.0
                anchor = tb[kk] + 0.008 + jit[kk] + shift
                gate = "none" if hold else ("dmx" if el < 8.0 else "beat")
                body = {"bpm": 60.0 / per, "anchor_ms": to_host(anchor), "beat_at_anchor": int(kk),
                        "beats_per_bar": 4, "bar_anchor_beat": 0, "phrase_bars": 8,
                        "conf": 0.3 if hold else (0.5 if gate == "dmx" else 0.85), "bar_conf": 0.5,
                        "phrase_conf": 0.4, "state": "holding" if hold else "locked", "gate": gate, "epoch": epoch,
                        "stability": 0.5, "source": "madmom"}
            body["t_ms"] = to_host(t)
            lines.append((to_host(t), {"type": "music.clock", "data": body}))
        lines.sort(key=lambda r: r[0])
        with open(rd / "bus.jsonl", "w", encoding="utf-8") as fh:
            for rx, msg in lines:
                fh.write(json.dumps({"rx_ms": rx + 2.0, "msg": msg}) + "\n")
        shutil.rmtree(work, ignore_errors=True)
        truth["bus"] = {"host_ms_at_rec0": host0, "host_rate": rate, "lock_after_s": 6.0, "beat_gate_after_s": 8.0,
                        "holding": [90.0, 98.0], "phase_bias_ms": 8.0, "phase_jitter_ms": 4.0}
    (rd / "run.json").write_text(json.dumps(run_json, indent=1) + "\n", encoding="utf-8")
    (rd / "truth.json").write_text(json.dumps(truth, indent=1) + "\n", encoding="utf-8")
    print(rd)


if __name__ == "__main__":
    main()
