#!/usr/bin/env python3
"""Synthetic Sonos calibration runs with known truth, to test programme_analyse.py without playing anything.

  programme  a small calibration programme in programme.py's layout (the interface calibrate.py sonos-run and
             programme_analyse.py share): programme.flac (44.1 kHz, 16-bit, stereo), programme-48k.wav (float32 mono
             mix, 48 kHz), manifest.json, beats/<segment>.beats in programme time. 8 s of silence, a 4-rung volume
             ladder (one 18 s excerpt at Sonos volumes 20, 30, 40, 50), then three annotated dataset excerpts and a
             piano piece at volume 40, each normalised to -14 LUFS, a 6 s silent gap (a volume cue at its start)
             between any two. About 4.3 minutes.
  run        a fake ``calibrate.py sonos-run`` directory from any programme (this one, or programme.py's):
               - the Sonos: the volume cues ramped over 4 s from --cue-delay-s after their time (room_service),
                 --db-per-step dB per volume step around the manifest's volume_main (the planted truth: Sonos' real
                 curve is what the ladder measures), a 4 s fade-up at the start;
               - the room: roomsim.apply with a room model (the pc22-mic run's by default; its dropouts left out
                 unless --dropouts, as they would blur the planted ladder; --plain leaves out all of the mic's
                 processing, whose scramble, a heavy-tailed random spectral gain, moves a 12 s rung's loudness by a
                 dB or so: the planted ladder is then exact), the room tone --snr-db under the music at volume_main;
               - the recorder: the programme starts --offset-s into the recording (the Sonos' latency and the
                 acoustic path), the recorder's clock runs --drift-ppm long against the Sonos', INT16;
               - tapmap.jsonl: a levels frame every 6-7 VBAN packets (16-19 ms), ``a`` = the host line (the stream's
                 clock --skew-ppm against the host's) + transport jitter (never early; 1 % late spikes);
               - bus.jsonl: run_stack.py's replay of the fake recording at --gate-ref (every published music.clock
                 body, and music.features at 2 Hz) moved onto that host clock, as the bus logger logs show_service;
                 state.jsonl at 1 Hz (receiver stats);
               - run.json (``synthetic``: programme_analyse.py never writes the real config from it), rec.json;
               - truth.json: every planted number, run_stack's own lufs_s medians and flash scores per segment on the
                 true map (programme_analyse.py's functions: like with like), for ``check``.
  check      programme_analyse.py's analysis/programme_report.json against truth.json, as a table; with
             --audio-cal-before/--audio-cal-after, that a --write-config into a copy changed only streams.<stream>.

Usage (heavy: run_stack replays the whole fake recording, about 0.4 x real time; CPU lock, nice 19):
  P=~/.venvs/sm-roundtrip/bin/python; S=tools/room/audio_cal/programme_synth.py
  $P $S programme [--out ~/.cache/screen-machine/audio_cal_synth/programmes/synth-YYYYMMDD]
  flock ~/.cache/screen-machine/cpu.lock nice -n 19 $P $S run --programme <programme dir> [--offset-s 3.7] ...
  flock ~/.cache/screen-machine/cpu.lock nice -n 19 $P tools/room/audio_cal/programme_analyse.py <run dir> \\
      --programme <programme dir> --write-config --audio-cal-file <a copy of config/room/audio_cal.json>
  $P $S check <run dir> [--audio-cal-before <the original> --audio-cal-after <the copy>]
Synthetic programmes and runs live in ~/.cache/screen-machine/audio_cal_synth/, never in the repo.
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
import subprocess
import sys

import numpy as np
import soundfile as sf
import soxr

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyse as A  # noqa: E402
import programme_analyse as PA  # noqa: E402
import roomsim  # noqa: E402

FS = 48000
FS_PROG = 44100
DATASETS = pathlib.Path.home() / ".cache" / "screen-machine" / "beat-datasets"
SYNTH = pathlib.Path.home() / ".cache" / "screen-machine" / "audio_cal_synth"
MODEL = HERE / "runs" / "20261018T145602Z-pc22-mic" / "model" / "model.json"
TARGET_LUFS = -14.0
H0 = 8_600_000.0              # the fake host clock's seconds at recording 0 (CLOCK_MONOTONIC-like)
TRANSPORT_S = 0.0003          # the smallest transport delay: part of the receiver's map (timeline.py), so the clock's
                              # times carry it too

# (kind, dataset id, start s, seconds, volume): the synthetic programme
PICKS = [("ladder", "harmonix/0130_hotstuff", 40.0, 18.0, (20, 30, 40, 50)),
         ("music", "ballroom/Albums-Cafe_Paradiso-05", 0.0, 30.0, 40),
         ("music", "gtzan/disco.00010", 0.0, 30.0, 40),
         ("music", "harmonix/0074_djgotusfallininlove", 60.0, 40.0, 40),
         ("music", "asap/Schubert_Moment_musical_no_3_Tetzloff09M", 10.0, 30.0, 40)]
LEAD_S, GAP_S, TAIL_S = 8.0, 6.0, 6.0


def log(s: str) -> None:
    print(s, file=sys.stderr, flush=True)


def decode(path: pathlib.Path, start_s: float, dur_s: float) -> np.ndarray:
    """(n, 2) float64 at 48 kHz via ffmpeg (mono sources doubled)."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start_s:.3f}", "-i", str(path), "-t", f"{dur_s:.3f}",
           "-f", "f32le", "-ac", "2", "-ar", str(FS), "-"]
    raw = subprocess.run(cmd, check=True, capture_output=True, timeout=300).stdout
    return np.frombuffer(raw, "<f4").reshape(-1, 2).astype(np.float64)


# ======================================================================================================================
# programme
# ======================================================================================================================
def build_programme(out: pathlib.Path) -> pathlib.Path:
    index = json.loads((DATASETS / "index.json").read_text())
    SC = A._score_module()
    out.mkdir(parents=True, exist_ok=True)
    (out / "beats").mkdir(exist_ok=True)
    pieces, segs, cues = [], [], []
    t = 0.0

    def silence(sec: float, vol: float, sid: str) -> None:
        nonlocal t
        pieces.append(np.zeros((int(round(sec * FS)), 2)))
        segs.append({"id": sid, "kind": "silence", "t0": round(t, 6), "t1": round(t + sec, 6), "volume": vol,
                     "steady": True, "genre": None, "source": None, "gain_db": None, "lufs": None, "beats": None})
        t += sec

    def excerpt(entry: dict, start: float, dur: float):
        x = decode(DATASETS / entry["audio"], start, dur)
        n = int(round(dur * FS))
        x = np.pad(x, ((0, max(0, n - len(x))), (0, 0)))[:n]
        li = roomsim.lufs(x.mean(axis=1))
        g = TARGET_LUFS - li
        pk = float(np.max(np.abs(x))) * 10 ** (g / 20)
        if pk > 0.97:                                  # no clipping in 16 bits: a quieter segment instead
            g -= 20 * math.log10(pk / 0.97)
        x = x * 10 ** (g / 20)
        f = int(0.02 * FS)                             # 20 ms fades: no clicks
        ramp = np.linspace(0.0, 1.0, f)[:, None]
        x[:f] *= ramp
        x[-f:] *= ramp[::-1]
        b = SC.read_beats(DATASETS / entry["beats"])
        b = b[(b >= start) & (b < start + dur)] - start
        return x, g, roomsim.lufs(x.mean(axis=1)), b

    def add(kind: str, sid: str, entry: dict, start: float, dur: float, vol: float):
        nonlocal t
        x, g, lv, b = excerpt(entry, start, dur)
        pieces.append(x)
        (out / "beats" / f"{sid}.beats").write_text("".join(f"{t + v:.6f}\t0\n" for v in b))
        segs.append({"id": sid, "kind": kind, "t0": round(t, 6), "t1": round(t + dur, 6), "volume": vol,
                     "steady": True, "genre": entry.get("genre"),
                     "source": {"dataset": entry["dataset"], "id": entry["id"], "file": entry["audio"],
                                "start_s": start, "dur_s": dur},
                     "gain_db": round(g, 3), "lufs": round(lv, 3), "beats": f"beats/{sid}.beats"})
        t += dur

    lad = PICKS[0]
    cues.append({"t": 0.0, "volume": lad[4][0]})
    silence(LEAD_S, lad[4][0], "s00")
    k = 0
    for i, vol in enumerate(lad[4]):
        if i:
            cues.append({"t": round(t, 6), "volume": vol})
            silence(GAP_S, vol, f"s{len(segs):02d}")
        add("ladder", f"l{vol:02d}", index[lad[1]], lad[2], lad[3], vol)
    vol_main = 40
    cues.append({"t": round(t, 6), "volume": vol_main})
    for kind, did, start, dur, vol in PICKS[1:]:
        silence(GAP_S, vol_main, f"s{len(segs):02d}")
        k += 1
        add(kind, f"m{k:02d}", index[did], start, dur, vol)
    silence(TAIL_S, vol_main, f"s{len(segs):02d}")
    x = np.concatenate(pieces)
    mono = x.mean(axis=1).astype(np.float32)
    sf.write(str(out / "programme-48k.wav"), mono, FS, subtype="FLOAT")
    x44 = soxr.resample(x, FS, FS_PROG, quality="VHQ")
    sf.write(str(out / "programme.flac"), np.clip(x44, -1.0, 32767 / 32768), FS_PROG, subtype="PCM_16")
    man = {"name": out.name, "fs": FS_PROG, "duration_s": round(len(x) / FS, 6), "file": "programme.flac",
           "target_lufs": TARGET_LUFS, "volume_main": vol_main, "volume_cues": cues, "segments": segs,
           "synthetic": True, "made_by": "tools/room/audio_cal/programme_synth.py programme",
           "made_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    (out / "manifest.json").write_text(json.dumps(man, indent=1) + "\n")
    log(f"programme {out}: {len(x) / FS:.1f} s, {len(segs)} segments")
    return out


# ======================================================================================================================
# a fake run
# ======================================================================================================================
def volume_gain_db(man: dict, n: int, db_per_step: float, cue_delay_s: float, fade_s: float = 4.0) -> np.ndarray:
    """The Sonos' gain (dB, programme samples) with the planted curve: each cue ramps linearly in volume over 4 s
    from ``cue_delay_s`` after its time; the first is the start volume, reached by a fade-up from silence."""
    cues = sorted(man["volume_cues"], key=lambda c: c["t"])
    v_main = float(man["volume_main"])
    u = np.arange(n) / FS
    vol = np.full(n, float(cues[0]["volume"]))
    for prev, c in zip(cues[:-1], cues[1:]):
        t0 = float(c["t"]) + cue_delay_s
        r = np.clip((u - t0) / PA.RAMP_S, 0.0, 1.0)
        on = u >= t0
        vol[on] = float(prev["volume"]) + (float(c["volume"]) - float(prev["volume"])) * r[on]
    g = db_per_step * (vol - v_main)
    fade = np.clip(u / fade_s, 1e-6, 1.0)
    return g + 20 * np.log10(fade)


def sim_model(src: pathlib.Path, dst_dir: pathlib.Path, dropouts: bool, plain: bool = False) -> pathlib.Path:
    """A copy of the room model (its files linked), without the mic's dropouts unless asked, without any of its
    processing when ``plain``."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    m = json.loads(src.read_text())
    if plain:
        m["processing"] = None
    elif not dropouts and isinstance(m.get("processing"), dict):
        m["processing"].pop("dropouts", None)
    for f in ("ir.wav", "noise.wav", "band_limit.wav"):
        p, q = src.parent / f, dst_dir / f
        if p.exists() and not q.exists():
            q.symlink_to(p)
    (dst_dir / "model.json").write_text(json.dumps(m, indent=1))
    return dst_dir / "model.json"


def make_run(a) -> pathlib.Path:
    pdir = pathlib.Path(a.programme).expanduser().resolve()
    man = json.loads((pdir / "manifest.json").read_text())
    SC = A._score_module()
    prog = PA.load_programme(pdir, SC)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = SYNTH / (a.name or f"sonos-{stamp}-{prog['name']}")
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(a.seed)
    x, fs, _ = A.read_audio(pdir / "programme-48k.wav")
    n = len(x)
    drift = a.drift_ppm * 1e-6
    skew = a.skew_ppm * 1e-6
    v_main = float(man["volume_main"])

    # the Sonos plays it; the world signal holds the recorder's lead and a tail
    g = volume_gain_db(man, n, a.db_per_step, a.cue_delay_s)
    xp = x * 10 ** (g / 20)
    n_pre = int(round(a.offset_s * FS))
    w = np.concatenate([np.zeros(n_pre), xp, np.zeros(int(3.0 * FS))])
    # the room tone a.snr_db under the music at volume_main: roomsim's snr is over its whole output
    main = np.zeros(len(w), bool)
    for s in prog["segments"]:
        if s["kind"] == "music" and PA._num(s.get("volume")) == v_main:
            main[n_pre + int(s["t0"] * FS):n_pre + int(s["t1"] * FS)] = True
    p_all, p_main = float(np.mean(w ** 2)), float(np.mean(w[main] ** 2))
    snr_param = a.snr_db - 10 * math.log10(p_main / p_all)
    model = sim_model(pathlib.Path(a.model).expanduser().resolve(), d / "sim" / "model", a.dropouts, a.plain)
    log(f"room: {model} ({'no mic processing' if a.plain else 'dropouts ' + ('kept' if a.dropouts else 'left out')}), "
        f"room tone {a.snr_db:g} dB under the music at {v_main:g} (roomsim snr {snr_param:.1f} dB)")
    y, _ = roomsim.apply(w, FS, str(model), seed=a.seed, snr_db=snr_param)
    # the recorder's clock runs long: resampled to (1 + drift) samples per Sonos sample
    rec = soxr.resample(np.asarray(y, np.float64), FS, FS * (1.0 + drift), quality="VHQ")
    rec = roomsim.quantise_int16(rec)
    sf.write(str(d / "rec.wav"), rec, FS, subtype="FLOAT")
    offset_true = n_pre / FS * (1.0 + drift)
    nr = len(rec)
    log(f"recording: {nr / FS:.1f} s; programme 0 at {offset_true:.6f} s, drift {a.drift_ppm:+g} ppm")

    def host(tr):
        return H0 + np.asarray(tr, float) * (1.0 + skew)

    def clock_host(tr):                             # the receiver's map: the earliest arrivals' line
        return host(tr) + TRANSPORT_S

    # tapmap: frames every 6-7 packets, arrival = host line + jitter (never early)
    rows, n_end, c0 = [], 0, 4_000_000
    while True:
        n_end += 128 * int(rng.choice([6, 7], p=[0.4, 0.6]))
        if n_end > nr:
            break
        jit = TRANSPORT_S + rng.exponential(0.0015) + (rng.uniform(0.02, 0.08) if rng.random() < 0.01 else 0.0)
        av = float(host(n_end / FS)) + jit
        rows.append({"n_end": n_end, "c": c0 + n_end // 128, "a": round(av, 6), "t": None, "rx": round(av + 0.0008, 6)})
    with open(d / "tapmap.jsonl", "w") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in rows)

    # the live clock: run_stack.py's replay of the fake recording, every body
    stack_p = d / "sim" / "stack_live.json"
    cmd = ["nice", "-n", "19", str(A.PY_SHOW), str(A.RUN_STACK), str(d / "rec.wav"), "--out", str(stack_p), "--id",
           "sim-live", "--no-slow-lane", "--preroll-s", "0", "--all-bodies", f"--gate-ref-lufs={a.gate_ref}"]
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    log("live clock: run_stack.py on the fake recording (about 0.4 x real time)")
    subprocess.run(cmd, check=True, env=env, capture_output=True, text=True, cwd=str(PA.REPO))
    res = json.loads(stack_p.read_text())
    bf = res["body_fields"]
    lines = [(float(clock_host(0.0)) * 1000 - 5.0, {"type": "welcome", "source": "show_service (synthetic)"})]
    for b in res["bodies"]:
        bd = dict(zip(bf, b))
        bpm = float(bd["bpm"] or 0.0)
        data = {"bpm": bpm / (1.0 + skew) if bpm else 0.0,
                "anchor_ms": float(clock_host(bd["anchor"])) * 1000 if bd["anchor"] is not None else 0.0,
                "beat_at_anchor": bd["beat_at_anchor"], "beats_per_bar": bd["beats_per_bar"],
                "bar_anchor_beat": bd["bar_anchor_beat"], "phrase_bars": 16, "conf": bd["conf"],
                "bar_conf": bd["bar_conf"], "state": bd["state"], "gate": bd["gate"], "epoch": bd["epoch"],
                "stability": bd["stability"], "source": "dbn", "t_ms": float(clock_host(bd["t"])) * 1000}
        rx = float(clock_host(bd["h"])) * 1000 + 1.5 + rng.exponential(1.0)
        lines.append((rx, {"type": "music.clock", "data": data}))
    ff = res["feature_fields"]
    for r in res["features"]:
        fd = dict(zip(ff, r))
        lines.append((float(clock_host(fd["t"])) * 1000 + 2.0, {"type": "music.features", "data": {
            "t_ms": float(clock_host(fd["t"])) * 1000, "stream": a.stream, "lufs_s": fd["lufs_s"], "gate": fd["gate"],
            "energy": fd["energy"]}}))
    lines.sort(key=lambda r: r[0])
    with open(d / "bus.jsonl", "w") as fh:
        fh.writelines('{"rx_ms":%.3f,"msg":%s}\n' % (rx, json.dumps(m)) for rx, m in lines)
    with open(d / "state.jsonl", "w") as fh:
        for k in range(int(nr / FS)):
            fh.write(json.dumps({"rx_ms": float(clock_host(k)) * 1000, "clock": None,
                                 "stream": {"state": "streaming", "net": {"packets": k * 375, "lost": 0,
                                                                          "loss_pct": 0.0, "gaps": 0,
                                                                          "segments": 1}}}) + "\n")

    # run.json, rec.json as calibrate.py's sonos-run leaves them
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    t_start_true = float(clock_host(offset_true))
    cues = [{"t": c["t"], "volume": c["volume"], "issued_prog_s": round(float(c["t"]) + a.cue_delay_s, 2)}
            for c in sorted(man["volume_cues"], key=lambda c: c["t"])[1:]]
    run = {"run": d.name, "stream": a.stream, "device": f"synthetic ({pathlib.Path(a.model).parent.parent.name} model)",
           "state": "stopped", "started_utc": now, "stopped_utc": now, "synthetic": True, "via": "show",
           "seconds": round(nr / FS, 3), "note": "programme_synth.py run: known truth in truth.json",
           "sonos": {"programme": man["name"], "programme_dir": str(pdir), "zone": "synthetic",
                     "play_cmd_mono": t_start_true - 2.6, "t_start_mono": t_start_true + a.ticks_error_s,
                     "start_ticks": [], "cues": cues},
           "programme": {"name": man["name"], "dir": str(pdir)}}
    (d / "run.json").write_text(json.dumps(run, indent=1))
    (d / "rec.json").write_text(json.dumps({"state": "stopped", "stream": a.stream, "via": "show", "fs": FS,
                                            "started_utc": now, "frames": nr, "seconds": round(nr / FS, 3),
                                            "gaps": 0, "gap_s": 0.0, "race": 0, "reconnects": 0}, indent=1))

    # the truth, measured like programme_analyse.py measures (its own functions), on the true map
    al_true = {"c0": -offset_true / (1.0 + drift), "c1": 1.0 / (1.0 + drift)}
    F = np.array([[r[ff.index("t")], np.nan if r[ff.index("lufs_s")] is None else r[ff.index("lufs_s")]]
                  for r in res["features"]], float)
    settles = PA.cue_settles(man, run)
    lv = PA.segment_levels(prog, settles, {"stack": (PA.to_prog(al_true, F[:, 0]), F[:, 1])})
    fl, bodies = PA.offline_clock_u(res, al_true)
    scores = {s["id"]: PA.clock_on_segment(s, fl, bodies, SC) for s in prog["segments"]}
    gr = PA.infer_gate_ref(F[:, 1], np.array([r[ff.index("gate")] for r in res["features"]], float))
    truth = {"synthetic": True, "programme": str(pdir),
             "map": {"offset_s": offset_true, "drift_ppm": a.drift_ppm, "c0": al_true["c0"], "c1": al_true["c1"],
                     "note": "recording s = offset_s + programme s x (1 + drift_ppm 1e-6)"},
             "host": {"H0": H0, "skew_ppm": a.skew_ppm, "note": "host s of recording s t = H0 + t (1 + skew)"},
             "sonos": {"db_per_step": a.db_per_step, "volume_main": v_main, "curve": "linear",
                       "cue_delay_s": a.cue_delay_s, "ticks_error_s": a.ticks_error_s},
             "room": {"model": str(a.model), "dropouts": a.dropouts, "plain": a.plain, "snr_db_at_main": a.snr_db,
                      "seed": a.seed},
             "live_gate_ref_lufs": a.gate_ref, "live_gate_effective": gr,
             "levels": {k: (v.get("stack") or {}).get("median") for k, v in lv.items()},
             "scores": scores, "stack": {"rt": res.get("rt"), "flashes": len(res["flashes"]),
                                         "bodies": len(res["bodies"])}}
    (d / "truth.json").write_text(json.dumps(A._clean(truth), indent=1))
    log(f"-> {d}")
    print(d)
    return d


# ======================================================================================================================
# check
# ======================================================================================================================
def check(a) -> int:
    d = pathlib.Path(a.run_dir).expanduser().resolve()
    tr = json.loads((d / "truth.json").read_text())
    rep = json.loads((d / "analysis" / "programme_report.json").read_text())
    al, hm, lad = rep["alignment"], rep["host_map"], rep["ladder"]
    if not lad.get("ok") and (rep.get("gate") or {}).get("ladder_from"):   # pass 2: pass 1's ladder, checked there
        print(f"(no ladder here: pass 1's, {rep['gate']['ladder_from']}, rise "
              f"{(rep.get('ladder_used') or {}).get('rise', {}).get('db')} dB)")
        lad = {"ok": None}
    bad = []

    def row(name, got, want, tol, fmt="{:.4f}"):
        ok = got is not None and want is not None and abs(got - want) <= tol
        if not ok:
            bad.append(name)
        print(f"  {name:<34} got {fmt.format(got) if got is not None else 'n/a':>12}   truth "
              f"{fmt.format(want) if want is not None else 'n/a':>12}   {'ok' if ok else 'OFF'} (tol {tol:g})")

    print(f"{d.name}")
    print("alignment and clocks")
    row("offset (s)", al["offset_s"], tr["map"]["offset_s"], 0.002, "{:.5f}")
    row("drift (ppm)", al["drift_ppm"], tr["map"]["drift_ppm"], 3.0, "{:+.2f}")
    row("host skew (ppm)", hm.get("skew_ppm"), tr["host"]["skew_ppm"], 2.0, "{:+.2f}")
    row("Sonos ticks - fit (s)", al.get("ticks_minus_fit_s"), tr["sonos"]["ticks_error_s"], 0.01, "{:+.3f}")
    print("ladder")
    plain = bool(tr["room"].get("plain"))
    if lad.get("ok"):
        if plain:
            row("dB per step (linear)", lad["db_per_step"], tr["sonos"]["db_per_step"], 0.03)
        else:
            print(f"  dB per step (linear) {lad['db_per_step']:.4f} (planted {tr['sonos']['db_per_step']}): not "
                  f"checked, the model's scramble moves each rung's loudness (run --plain checks it)")
        print(f"  model chosen: {lad['model']} (truth: linear); residual linear "
              f"{lad['fits']['linear']['rms_db']:.3f} dB, log {lad['fits']['log']['rms_db']:.3f} dB")
        lv = tr["levels"]
        ids = {s["id"]: s for s in rep["programme"]["segments"]}
        main = [lv[k] for k, s in ids.items() if s["kind"] == "music" and s.get("volume") == tr["sonos"]["volume_main"]
                and lv.get(k) is not None and not PA.piano_like(s)]
        want70 = float(np.median(main)) + tr["sonos"]["db_per_step"] * (rep["gate"]["party_volume"] -
                                                                         tr["sonos"]["volume_main"]) if main else None
        g = rep["gate"]
        if plain:     # the room's own sound in the rungs biases the slope a little: within the stated uncertainty
            row("music_lufs_party (median of songs)", g.get("music_lufs_party"), want70,
                max(0.6, g.get("music_lufs_party_u_db") or 0.0), "{:.2f}")
        else:
            print(f"  music_lufs_party {g.get('music_lufs_party')} (planted curve: {want70}; not checked, as above)")
        print(f"  (party +- {g.get('music_lufs_party_u_db')} dB; the ladder's excerpt at 70: "
              f"{g.get('ladder_lufs_party')}; songs at the main volume: {g.get('music_lufs_test')})")
    elif lad.get("ok") is False:
        bad.append("ladder")
        print(f"  no ladder: {lad.get('reason')}")
    print("levels per segment (lufs_s median: analysis primary vs run_stack truth)")
    for s in rep["programme"]["segments"]:
        got = ((rep["levels"]["segments"].get(s["id"]) or {}).get("primary") or {}).get("median")
        want = tr["levels"].get(s["id"])
        if got is not None and want is not None:
            row(f"{s['id']} {s['kind']}", got, want, 0.25, "{:.2f}")
    print("live clock per segment vs run_stack's own flashes (F / hit25 / cover / t_lock)")
    clk = rep.get("clock") or {}
    if not clk.get("available"):
        bad.append("clock")
        print(f"  not available: {clk.get('reason')}")
    for r in clk.get("segments", []):
        t = tr["scores"].get(r["id"]) or {}
        if t.get("f") is None:
            continue
        v, o = r.get("live") or {}, r.get("offline") or {}
        dd = [abs((v.get(k) or 0) - (t.get(k) or 0)) for k in ("f", "hit25", "cover_beat")]
        ok = max(dd) <= 0.03
        if not ok:
            bad.append(f"clock {r['id']}")
        print(f"  {r['id']:<5} live {v.get('f', 0):.3f} {v.get('hit25', 0):.3f} {v.get('cover_beat', 0):.3f} "
              f"{v.get('t_lock') if v.get('t_lock') is not None else '-':>6}   truth {t['f']:.3f} {t['hit25']:.3f} "
              f"{t['cover_beat']:.3f} {t.get('t_lock') if t.get('t_lock') is not None else '-':>6}   offline "
              f"{o.get('f', float('nan')):.3f}   {'ok' if ok else 'OFF'}")
    print(f"live gate read back: {rep['live_gate'].get('ref_lufs')} (the sim ran at {tr['live_gate_ref_lufs']}, "
          f"effective {tr['live_gate_effective'].get('ref_lufs')})")
    if a.audio_cal_before and a.audio_cal_after:
        b = json.loads(pathlib.Path(a.audio_cal_before).read_text())
        f = json.loads(pathlib.Path(a.audio_cal_after).read_text())
        stream = rep["run"]["stream"]
        others_b = {k: v for k, v in b.items() if k != "streams"}
        others_f = {k: v for k, v in f.items() if k != "streams"}
        sb = {k: v for k, v in (b.get("streams") or {}).items() if k != stream}
        sf_ = {k: v for k, v in (f.get("streams") or {}).items() if k != stream}
        ok = others_b == others_f and sb == sf_ and stream in (f.get("streams") or {})
        if not ok:
            bad.append("audio_cal merge")
        ent = (f.get("streams") or {}).get(stream) or {}
        print(f"audio_cal: only streams.{stream} changed: {ok}; music_lufs {ent.get('summary', {}).get('music_lufs')}"
              f"; other streams kept: {sorted(sf_)}")
    print("RESULT: " + ("all ok" if not bad else "OFF: " + ", ".join(bad)))
    return 0 if not bad else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("programme", help="build the small synthetic programme")
    p.add_argument("--out", type=pathlib.Path,
                   default=SYNTH / "programmes" / f"synth-{dt.date.today().strftime('%Y%m%d')}")
    r = sub.add_parser("run", help="a fake sonos-run directory from a programme")
    r.add_argument("--programme", required=True)
    r.add_argument("--name", default=None)
    r.add_argument("--model", default=str(MODEL))
    r.add_argument("--dropouts", action="store_true", help="keep the model's dropouts")
    r.add_argument("--plain", action="store_true", help="none of the model's mic processing (an exact ladder)")
    r.add_argument("--offset-s", type=float, default=3.7)
    r.add_argument("--drift-ppm", type=float, default=40.0)
    r.add_argument("--skew-ppm", type=float, default=12.0)
    r.add_argument("--db-per-step", type=float, default=0.5)
    r.add_argument("--snr-db", type=float, default=25.0, help="the room tone under the music at volume_main")
    r.add_argument("--cue-delay-s", type=float, default=0.3)
    r.add_argument("--ticks-error-s", type=float, default=0.08, help="the Sonos position ticks' error")
    r.add_argument("--gate-ref", type=float, default=-30.0, help="the live gate reference the fake bus ran at")
    r.add_argument("--stream", default="room-mic")
    r.add_argument("--seed", type=int, default=0)
    c = sub.add_parser("check", help="programme_analyse.py's report against truth.json")
    c.add_argument("run_dir")
    c.add_argument("--audio-cal-before", default=None)
    c.add_argument("--audio-cal-after", default=None)
    a = ap.parse_args()
    if a.cmd == "programme":
        build_programme(a.out.expanduser())
        return 0
    if a.cmd == "run":
        make_run(a)
        return 0
    return check(a)


if __name__ == "__main__":
    raise SystemExit(main())
