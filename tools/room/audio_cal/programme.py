#!/usr/bin/env python3
"""Calibration programmes: the music the Drawing Room Sonos plays while the room mic is calibrated.

The first calibration (18 Oct, pc22-mic) was one track the owner played by hand. The room-mic calibration (the kiosk's
NEEWER/UM04 USB boundary mic in the Drawing Room) is driven instead: screen-machine-26's ``calibrate.py sonos-run``
plays a programme file through room_service at timed Sonos volumes, records room-mic through show_service's levels feed
and logs the live beat clock; programme_analyse.py then reads that recording against the programme. This script only
builds the programmes. It never plays anything and never talks to a device.

  ~/.venvs/sm-roundtrip/bin/python tools/room/audio_cal/programme.py build pass1 --date 20261021 [--out DIR]
  ~/.venvs/sm-roundtrip/bin/python tools/room/audio_cal/programme.py build pass2 --date 20261021 [--out DIR]
  ~/.venvs/sm-roundtrip/bin/python tools/room/audio_cal/programme.py show <programme dir>      # the segment table
A build takes about half a minute of one core (resampling, true peaks): run it under
``flock ~/.cache/screen-machine/cpu.lock nice -n 19``. It is deterministic: the same sources give the same files.

A programme is <out>/<pass>-<date>/ (out: ~/.cache/screen-machine/audio-cal/programmes; never in git):
  programme.flac      44.1 kHz, 16-bit, stereo: what the Sonos plays
  programme-48k.wav   the FLAC decoded, (L+R)/2, resampled to 48 kHz, float32 mono: the analysis' "original"
  manifest.json       what 26's controller and programme_analyse.py read (below)
  beats/<seg>.beats   each music and ladder segment's annotated beats in programme time, "<t_s>\\t<position or 0>"
It is written to a temporary folder and swapped in whole, so a reader never sees half a programme.

manifest.json (the interface 26's controller codes against; fields beyond these are extra information):
  {"name", "fs": 44100, "duration_s", "file": "programme.flac", "target_lufs": -14, "volume_main": 40,
   "volume_cues": [{"t", "volume"}],      the controller issues each at its programme time; every cue sits at the
                                          start of a digital-silence gap of at least 5 s (room_service ramps a volume
                                          change over 4 s while playing, so the ramp never touches the music)
   "segments": [{"id", "kind": "silence"|"ladder"|"music", "t0", "t1", "volume" (the Sonos volume in effect), "steady"
                 (a steady beat: false for silence and the rubato piano), "genre", "source": {"dataset", "id", "file",
                 "start_s", "dur_s"}, "gain_db", "lufs", "beats": "beats/<id>.beats" or null}]}
  Extras per segment: "true_peak_dbtp", "n_beats", "bpm" (60 / median interval), "title", "cue" (a silence carrying a
  volume cue), and in "source" the item's channels, "fold_db" (a stereo source's mono fold: the mix's power against
  the channels' mean) and "beats_from".

pass1-<date> (about 13 min):
  1. 20 s of digital silence (the room tone at the mic; a cue to volume 20 at 0 s, so every second's volume is known).
  2. The volume ladder: one steady, dense, full-band 20 s excerpt of Sweet Dreams (LADDER_START_S below), four times,
     at Sonos volumes 20, 30, 40, 50 (26 caps the test at 50; the owner's party volume, about 70, is extrapolated by
     the analysis), each after a 6 s gap that carries its volume's cue at its start.
  3. A 6 s gap with the cue to volume_main (40), then the music at 40, each item after 6 s of silence (room tone
     between songs, as at a party): Sweet Dreams from its start (75 s), eight 30 s dataset excerpts over eight genres,
     two Harmonix songs (90 s from 30 s in: one Pop, one Dance/Electronic) and a rubato piano piece (60 s, steady:
     false: the no-false-lock check).
  4. 20 s of silence at the end (the room's decay and the gate letting go after the music).
pass2-<date> (about 4 min; after 26 has set the gate from pass 1): 10 s of silence carrying the cue to 40, then four
items at 40 after 6 s gaps: Sweet Dreams' first 60 s (the same audio as pass 1's, so the clock can be compared like for
like), one ballroom and one GTZAN excerpt and 60 s of a Harmonix song (none of them in pass 1), then 20 s of silence.

Every music and ladder item:
- Folded to mono ((L+R)/2 for a stereo source) and played as the same signal on both channels: mono-compatible by
  construction, the mic hears one signal through both speakers, and programme-48k.wav is exactly what was played
  (true stereo would make the mic's input a mix the mono original can't predict).
- Resampled to 44.1 kHz (soxr VHQ; the excerpt is cut with 50 ms of margin on each side, so the resampler's edges fall
  outside it), with 10 ms raised-cosine fades at both ends.
- Loudness-normalised to -14 LUFS integrated (ITU-R BS.1770-4 via pyloudnorm, measured on the stereo file as the Sonos
  plays it): Spotify's normal level, so "the music at Sonos volume V" means typical streamed music at volume V. If
  that gain would put the true peak (4x oversampled) above -1 dBTP, the gain is lowered to meet it (nothing is
  limited or compressed) and the item's gain_db and resulting lufs say so.
- Quantised to 16 bits without dither, so the gaps stay digital zero. The manifest's lufs and true peak are measured
  on the decoded FLAC.
- Beats: the item's annotations inside the excerpt, shifted to programme time (to the 44.1 kHz sample: the excerpt's
  start is snapped to a source sample and the resampler's grid, and start_s records where it landed).

Sweet Dreams (Eurythmics, the exact YouTube audio of the first calibration run: runs/20261018T145602Z-pc22-mic/
original/original-48k.wav). Its beats are 26's reference (reference/beats-original-timeline.beats: madmom offline,
8 nets + DBN, on original-matched.wav), mapped onto the original's timeline with run.json's music map
(orig = speed * (t + rec_t0) + c). Mapped, they agree with madmom's beats of the whole original in
analysis/beats.json to 3 ms (median absolute difference; -5..+6 ms for 80 % of the beats: madmom's 10 ms frames). The
reference covers the original's first 53.4 s (a beat at -0.29 s, inside the matched copy's leading silence, is
dropped); later beats come from analysis/beats.json's madmom beats (the same tracker). Bar positions come from Beat
This!'s downbeats there (4/4 throughout). The ladder excerpt is the steadiest dense 20 s of the song (measured 20 Oct
over 20 s windows starting on Beat This! downbeats: the 3 s loudness moves 0.9 LU across 165.0-185.0 s, the final
refrains, full bass and drums); it starts half a beat early, so the first downbeat is not under the fade. The song's
opening has a high peak-to-loudness ratio: its first 75 s (and 60 s) are held by the true-peak cap at about -15.9
LUFS, the ladder excerpt is at -14.

The dataset picks (PICKS; ~/.cache/screen-machine/beat-datasets, tools/room/beat_eval/fetch_datasets.py). Hard rules,
checked again at every build: not a duplicate, not in the dataset's verify.json problems, no beats past the audio,
beats right through the excerpt (no interval over 2.5 beats; 3 s for the rubato piece). Then, from a scan of every
candidate in the genres (20 Oct, screen-machine-26's builder): annotations that line up with the audio's onsets
(onset_seg_ok 1.0, onset lag within 8 ms of its set's median, high onset salience), a steady tempo inside the excerpt
(the intervals' CV under 0.06), 60-210 bpm, no clipping, a band at or above its genre's median (GTZAN is 22.05 kHz,
so at most 11 kHz; the ballroom set is 44.1 kHz but many excerpts are band-limited), under 1.5 dB lost in a stereo
source's mono fold, and a peak-to-loudness ratio of 13 dB or less, so -14 LUFS stands without the true-peak cap. Among
those, the highest onset salience, or a clean score from the live estimator where tools/room/beat_eval has one
(baseline and DEV subsets, clean audio). Each pick's note says why.
"""
from __future__ import annotations

import os

for _k, _v in (("OMP_NUM_THREADS", "2"), ("OPENBLAS_NUM_THREADS", "2"), ("MKL_NUM_THREADS", "1")):
    os.environ.setdefault(_k, _v)       # the host renders 4K: two threads at most

import argparse
import datetime as dt
import json
import pathlib
import re
import shutil
import sys

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
import soxr

HERE = pathlib.Path(__file__).resolve().parent
DATASETS = pathlib.Path.home() / ".cache" / "screen-machine" / "beat-datasets"
OUT_ROOT = pathlib.Path.home() / ".cache" / "screen-machine" / "audio-cal" / "programmes"
FIRST_RUN = HERE / "runs" / "20261018T145602Z-pc22-mic"
SD_WAV = FIRST_RUN / "original" / "original-48k.wav"
SD_REF = FIRST_RUN / "reference" / "beats-original-timeline.beats"
SD_TITLE = "Eurythmics - Sweet Dreams (Are Made of This)"

FS = 44100                    # what the Sonos plays
FS_ANA = 48000                # the analysis' original (show_service's rate)
TARGET_LUFS = -14.0
TP_MAX_DBTP = -1.0
FADE_S = 0.010
MARGIN_S = 0.05               # cut this much either side before resampling, so the resampler's edges fall outside
GAP_S = 6.0                   # a gap before each item (>= 5 s: a cue's 4 s ramp fits in it)
CUE_GAP_MIN_S = 5.0
VOLUME_MAIN = 40
VOLUME_CAP = 50               # 26 caps the test at 50 (the owner: party is about 70, the test need not be that loud)
LADDER_VOLUMES = (20, 30, 40, 50)
LADDER_START_S = 164.80       # half a beat (125 bpm) before Beat This!'s downbeat at 165.04 s
LADDER_DUR_S = 20.0
DUR_RANGE_MIN = {"pass1": (12.5, 15.5), "pass2": (3.0, 6.0)}

# The dataset items, in playing order after Sweet Dreams:
#   (combined-index id, segment slug, start_s, dur_s, steady, why).
# "salience" is the index's onset_salience (how sharply the annotations sit on the audio's onsets), "lag" its
# onset_lag_ms (each set's median: ballroom -12, GTZAN -22.5, Harmonix -14.4 ms), CV the beat intervals' spread
# inside the excerpt; band = the 12-16 kHz level against 1-4 kHz (8-10.5 kHz for GTZAN), from the 20 Oct scan.
PICKS: dict[str, list[tuple[str, str, float, float, bool, str]]] = {
    "pass1": [
        ("ballroom/Albums-Ballroom_Classics4-04", "waltz", 0.0, 30.0, True,
         "3/4 at 87 bpm; the widest band of the waltzes that pass (most are band-limited: -22.6 dB against the "
         "set's median -32), salience 7.9 (the genre's best), lag -13 ms, CV 0.034"),
        ("gtzan/hiphop.00065", "hiphop", 0.0, 30.0, True,
         "96 bpm, salience 13.6, CV 0.006, band -6.4 dB (up to GTZAN's 11 kHz), lag -29 ms"),
        ("ballroom/Media-105604", "chachacha", 0.0, 30.0, True,
         "125 bpm, the widest band of the cha-cha-chas that pass (-7.3 dB), salience 9.1, CV 0.011, lag -12 ms"),
        ("gtzan/rock.00056", "rock", 0.0, 30.0, True,
         "138 bpm, salience 20.1 (the genre's highest), CV 0.005, band -8.8 dB, lag -21.5 ms"),
        ("ballroom/Albums-Ballroom_Classics4-19", "quickstep", 0.0, 30.0, True,
         "200 bpm, band -16.5 dB, salience 5.1 (the genre's best: quicksteps run low), CV 0.023, lag -6 ms"),
        ("gtzan/disco.00023", "disco", 0.0, 30.0, True,
         "115 bpm, salience 14.9 (the genre's highest), CV 0.007, band -9.1 dB, lag -21.5 ms"),
        ("ballroom/Albums-Latin_Jam5-10", "samba", 0.0, 30.0, True,
         "100 bpm, salience 14.6 (the genre's highest), CV 0.007, band -14.9 dB, lag -16 ms"),
        ("gtzan/reggae.00061", "reggae", 0.0, 30.0, True,
         "92 bpm, salience 11.0 (the genre's highest at a plain tempo), CV 0.005, band -8.6 dB, lag -21 ms"),
        ("harmonix/0074_djgotusfallininlove", "pop", 30.0, 90.0, True,
         "Usher, 120 bpm 4/4, salience 16.0 (Pop's highest), mono fold -0.25 dB, peak-to-loudness 8.4 dB"),
        ("harmonix/0664_eyesextendedmix", "dance", 30.0, 90.0, True,
         "Kaskade, 128 bpm 4/4; the live estimator on it clean: F 0.99, coverage 0.93 (held-out baseline, A); "
         "salience 11.9, mono fold -1.3 dB"),
        ("asap/Schumann_Arabeske_Min09M", "piano", 120.0, 60.0, False,
         "rubato: in this minute the beat-to-beat tempo change is 24 % (median), the intervals' CV 0.51, 58 beats; "
         "onset_seg_ok 1.0, lag -3.9 ms; the DBN estimator gated no beat on the piece clean (DEV, 8 nets: coverage "
         "0); peak-to-loudness 18 dB, so the true peak sets its level, not -14 LUFS"),
    ],
    "pass2": [
        ("ballroom/Albums-Latin_Jam3-11", "jive", 0.0, 30.0, True,
         "176 bpm; the DBN estimator on it clean: F 0.99 (DEV, 8 nets); salience 10.1, CV 0.016, band -15.1 dB"),
        ("gtzan/pop.00046", "pop", 0.0, 30.0, True,
         "88 bpm, salience 14.8, CV 0.007, band -8.1 dB, lag -26.5 ms"),
        ("harmonix/0116_goodies", "rnb", 30.0, 60.0, True,
         "Ciara, 102 bpm 4/4, salience 15.4 (R&B's highest), mono fold -0.17 dB"),
    ],
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def read_json(p: pathlib.Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------------------------------------------------
# sources
# ----------------------------------------------------------------------------------------------------------------------
def read_beats(p: pathlib.Path) -> np.ndarray:
    """A .beats file as an (n, 2) array: time, position in bar (0 = unknown)."""
    rows = []
    for line in p.read_text().splitlines():
        bits = line.split()
        if not bits:
            continue
        rows.append((float(bits[0]), float(bits[1]) if len(bits) > 1 else 0.0))
    return np.array(rows, float).reshape(-1, 2)


def bar_positions(beats: np.ndarray, downbeats: np.ndarray, per_bar: int = 4, tol_s: float = 0.04) -> np.ndarray:
    """Positions in bar (1 = the downbeat) for beat times from a downbeat list, counting on from each downbeat and back
    from the first. All zeros (unknown) when the downbeats don't fall every per_bar beats."""
    near = np.array([np.min(np.abs(downbeats - t)) <= tol_s for t in beats]) if len(downbeats) else np.zeros(0, bool)
    d_idx = np.flatnonzero(near)
    if len(d_idx) < 2 or np.mean(np.diff(d_idx) == per_bar) < 0.9:
        return np.zeros(len(beats))
    pos = np.zeros(len(beats))
    for i in range(len(beats)):
        prev = d_idx[d_idx <= i]
        k = (i - prev[-1]) if len(prev) else (i - d_idx[0])
        pos[i] = k % per_bar + 1
    return pos


def sweet_dreams_beats() -> tuple[np.ndarray, str]:
    """Sweet Dreams' beats on original-48k.wav's timeline (see the module docstring), and where they came from."""
    run = read_json(FIRST_RUN / "run.json")["music"]
    sp, c, r0 = float(run["speed"]), float(run["c"]), float(run["rec_t0"])
    ref = read_beats(SD_REF)[:, 0]
    ref = sp * (ref + r0) + c
    ref = ref[ref >= 0.0]                          # the matched copy's leading silence
    b = read_json(FIRST_RUN / "analysis" / "beats.json")
    mm = np.array(b["madmom"]["beats_orig_s"], float)
    down = np.array(b["beatthis"]["downbeats_orig_s"], float)
    # the two agree where both exist; check it, so a changed first-run analysis can't slip in a shifted grid
    d = np.array([mm[np.argmin(np.abs(mm - t))] - t for t in ref])
    if np.median(np.abs(d)) > 0.010:
        raise SystemExit(f"Sweet Dreams: the reference and analysis/beats.json disagree "
                         f"({np.median(np.abs(d)) * 1e3:.1f} ms median): check the first run")
    ibi = float(np.median(np.diff(ref)))
    t = np.concatenate([ref, mm[mm > ref[-1] + ibi / 2]])
    pos = bar_positions(t, down)
    how = (f"{len(ref)} beats to {ref[-1]:.2f} s: 26's reference (madmom offline on original-matched.wav) mapped by "
           f"run.json's music map; after that madmom's beats of the whole original (analysis/beats.json, "
           f"{np.median(np.abs(d)) * 1e3:.1f} ms median from the reference where both exist); bar positions from "
           f"Beat This!'s downbeats")
    return np.column_stack([t, pos]), how


def dataset_entry(iid: str) -> tuple[dict, pathlib.Path, pathlib.Path]:
    """A combined-index entry (id "<set>/<id>") with its audio and beats paths, after the hard rules."""
    idx = read_json(DATASETS / "index.json")
    if iid not in idx:
        raise SystemExit(f"{iid}: not in {DATASETS / 'index.json'}")
    e = idx[iid]
    ds, local = iid.split("/", 1)
    probs = read_json(DATASETS / ds / "verify.json").get("problems") or {}
    if local in probs:
        raise SystemExit(f"{iid}: flagged in {ds}/verify.json: {probs[local]}")
    if e.get("duplicate"):
        raise SystemExit(f"{iid}: a duplicate excerpt")
    if int(e.get("beats_after_audio") or 0):
        raise SystemExit(f"{iid}: beats past the audio")
    return e, DATASETS / e["audio"], DATASETS / e["beats"]


# ----------------------------------------------------------------------------------------------------------------------
# audio
# ----------------------------------------------------------------------------------------------------------------------
def excerpt(path: pathlib.Path, start_s: float, dur_s: float) -> tuple[np.ndarray, float, dict]:
    """[start_s, start_s + dur_s) of a file as mono at FS (float64), the source time of its first output sample, and
    the source's facts. The cut has MARGIN_S either side, so the resampler's edges fall outside the excerpt."""
    info = sf.info(str(path))
    fs = int(info.samplerate)
    s0 = int(round(start_s * fs))
    n_out = int(round(dur_s * FS))
    m = int(round(MARGIN_S * fs))
    a, b = max(0, s0 - m), min(info.frames, s0 + int(np.ceil(dur_s * fs)) + m)
    x, _ = sf.read(str(path), start=a, stop=b, dtype="float64", always_2d=True)
    facts = {"channels": int(x.shape[1]), "fs": fs}
    if x.shape[1] > 1:
        mono = x.mean(axis=1)
        facts["fold_db"] = round(float(10 * np.log10((mono ** 2).mean() / max((x ** 2).mean(), 1e-20) + 1e-20)), 2)
    else:
        mono = x[:, 0]
    y = soxr.resample(mono, fs, FS, quality="VHQ") if fs != FS else mono
    k0 = int(round((s0 - a) / fs * FS))
    y = y[k0:k0 + n_out]
    if len(y) < n_out:
        log(f"  {path.name}: {(n_out - len(y)) / FS:.3f} s short of the excerpt: padded with silence")
        y = np.concatenate([y, np.zeros(n_out - len(y))])
    return y, a / fs + k0 / FS, facts


def fade(y: np.ndarray) -> np.ndarray:
    n = int(round(FADE_S * FS))
    w = 0.5 - 0.5 * np.cos(np.pi * (np.arange(n) + 0.5) / n)
    y = y.copy()
    y[:n] *= w
    y[-n:] *= w[::-1]
    return y


def true_peak_db(y: np.ndarray) -> float:
    """BS.1770-4's true peak, 4x oversampled (soxr VHQ as the interpolator), dBTP."""
    up = soxr.resample(y, FS, 4 * FS, quality="VHQ")
    return float(20 * np.log10(max(np.abs(up).max(), np.abs(y).max(), 1e-12)))


def loudness(y: np.ndarray) -> float:
    """Integrated loudness of y played as dual mono (both channels), LUFS."""
    return float(pyln.Meter(FS).integrated_loudness(np.column_stack([y, y])))


def normalise(y: np.ndarray) -> tuple[np.ndarray, float, bool]:
    """y at TARGET_LUFS, or lower if that would put the true peak over TP_MAX_DBTP: (y, gain dB, capped)."""
    g = TARGET_LUFS - loudness(y)
    tp = true_peak_db(y) + g
    capped = tp > TP_MAX_DBTP
    if capped:
        g -= tp - TP_MAX_DBTP + 0.02              # 0.02 dB for the 16-bit rounding
    return y * 10 ** (g / 20), g, capped


# ----------------------------------------------------------------------------------------------------------------------
# the programme
# ----------------------------------------------------------------------------------------------------------------------
class Builder:
    """Segments laid end to end on FS's sample grid, with the cues and the volume in effect."""

    def __init__(self, name: str):
        self.name = name
        self.parts: list[np.ndarray] = []
        self.n = 0
        self.segments: list[dict] = []
        self.cues: list[dict] = []
        self.volume: int | None = None
        self.beats: dict[str, np.ndarray] = {}

    @property
    def t(self) -> float:
        return self.n / FS

    def silence(self, sid: str, dur_s: float, cue: int | None = None) -> None:
        if cue is not None:
            if not 0 <= cue <= VOLUME_CAP:
                raise SystemExit(f"{sid}: volume {cue} is over the test's cap ({VOLUME_CAP})")
            if dur_s < CUE_GAP_MIN_S:
                raise SystemExit(f"{sid}: a cue needs a gap of {CUE_GAP_MIN_S} s or more")
            self.cues.append({"t": round(self.t, 6), "volume": int(cue)})
            self.volume = int(cue)
        n = int(round(dur_s * FS))
        t0 = self.t
        self.parts.append(np.zeros(n))
        self.n += n
        self.segments.append({"id": sid, "kind": "silence", "t0": round(t0, 6), "t1": round(self.t, 6),
                              "volume": self.volume, "steady": False, "genre": None, "source": None, "gain_db": None,
                              "lufs": None, "beats": None, "cue": cue is not None})

    def item(self, sid: str, kind: str, path: pathlib.Path, start_s: float, dur_s: float, beats: np.ndarray, *,
             steady: bool, genre: str, source: dict, title: str) -> None:
        if self.volume is None:
            raise SystemExit(f"{sid}: no volume cue before it")
        y, src_t0, facts = excerpt(path, start_s, dur_s)
        y, g, capped = normalise(fade(y))
        t0 = self.t
        n = len(y)
        self.parts.append(y)
        self.n += n
        inside = beats[(beats[:, 0] >= src_t0) & (beats[:, 0] < src_t0 + n / FS)]
        prog = np.column_stack([t0 + inside[:, 0] - src_t0, inside[:, 1]])
        self.beats[sid] = prog
        ibi = np.diff(prog[:, 0])
        src = {**source, "file": str(path), "start_s": round(src_t0, 6), "dur_s": round(n / FS, 6), **facts}
        self.segments.append({"id": sid, "kind": kind, "t0": round(t0, 6), "t1": round(self.t, 6),
                              "volume": self.volume, "steady": steady, "genre": genre, "source": src,
                              "gain_db": round(g, 2), "lufs": None, "beats": f"beats/{sid}.beats",
                              "true_peak_dbtp": None, "tp_capped": capped, "n_beats": int(len(prog)),
                              "bpm": round(60 / float(np.median(ibi)), 2) if len(ibi) else None, "title": title})

    def audio(self) -> np.ndarray:
        return np.concatenate(self.parts) if self.parts else np.zeros(0)


def add_sweet_dreams(b: Builder, sid: str, kind: str, start_s: float, dur_s: float, sd: tuple[np.ndarray, str]) -> None:
    beats, how = sd
    b.item(sid, kind, SD_WAV, start_s, dur_s, beats, steady=True, genre="synth-pop",
           source={"dataset": "audio_cal", "id": f"{FIRST_RUN.name}/original", "beats_from": how}, title=SD_TITLE)


def add_pick(b: Builder, sid: str, iid: str, start_s: float, dur_s: float, steady: bool, why: str) -> None:
    e, audio, beats_p = dataset_entry(iid)
    dur = min(dur_s, float(e["duration_s"]) - start_s)
    dur = np.floor(dur * 100) / 100
    if dur < dur_s - 0.5:
        raise SystemExit(f"{iid}: only {dur:.2f} s from {start_s} s (wanted {dur_s})")
    beats = read_beats(beats_p)
    inside = beats[(beats[:, 0] >= start_s) & (beats[:, 0] < start_s + dur)]
    if len(inside) < 8:
        raise SystemExit(f"{iid}: only {len(inside)} beats in the excerpt")
    ibi = float(np.median(np.diff(inside[:, 0])))
    gap_max = 2.5 * ibi if steady else 3.0             # rubato: a held chord may stretch a beat
    if inside[0, 0] - start_s > gap_max + 0.5 or start_s + dur - inside[-1, 0] > gap_max + 0.5 or \
            np.diff(inside[:, 0]).max() > gap_max:
        raise SystemExit(f"{iid}: the annotations don't run right through the excerpt")
    ds, local = iid.split("/", 1)
    title = " - ".join(str(v) for v in (e.get("artist") or e.get("composer"), e.get("title")) if v) or local
    b.item(sid, "music", audio, start_s, dur, beats, steady=steady, genre=str(e.get("genre")),
           source={"dataset": ds, "id": local, "why": why, "annotations": e.get("notes"),
                   "onset_lag_ms": e.get("onset_lag_ms"), "beats_per_bar": e.get("beats_per_bar")},
           title=title)


def layout(which: str, sd: tuple[np.ndarray, str]) -> Builder:
    """The segment sequence of pass1 or pass2 (the module docstring)."""
    b = Builder(which)
    picks = PICKS[which]
    if which == "pass1":
        b.silence("silence-start", 20.0, cue=LADDER_VOLUMES[0])
        for v in LADDER_VOLUMES:
            b.silence(f"gap-ladder-v{v}", GAP_S, cue=v)
            add_sweet_dreams(b, f"ladder-v{v}", "ladder", LADDER_START_S, LADDER_DUR_S, sd)
        b.silence("gap-main", GAP_S, cue=VOLUME_MAIN)
        n = 1
        b.silence(f"gap-m{n:02d}", GAP_S)
        add_sweet_dreams(b, f"m{n:02d}-sweet-dreams", "music", 0.0, 75.0, sd)
    elif which == "pass2":
        b.silence("silence-start", 10.0, cue=VOLUME_MAIN)
        n = 1
        b.silence(f"gap-m{n:02d}", GAP_S)
        add_sweet_dreams(b, f"m{n:02d}-sweet-dreams", "music", 0.0, 60.0, sd)
    else:
        raise SystemExit(f"unknown programme {which!r} (pass1 or pass2)")
    for iid, slug, start_s, dur_s, steady, why in picks:
        n += 1
        b.silence(f"gap-m{n:02d}", GAP_S)
        add_pick(b, f"m{n:02d}-{slug}", iid, start_s, dur_s, steady, why)
    b.silence("silence-end", 20.0)
    return b


def check(man: dict, beats_dir: pathlib.Path) -> list[str]:
    """The interface's rules, on the finished manifest: problems (empty when it is sound)."""
    out = []
    segs = man["segments"]
    if not segs or segs[0]["t0"] != 0:
        out.append("the first segment doesn't start at 0")
    for a, b in zip(segs, segs[1:]):
        if abs(a["t1"] - b["t0"]) > 1e-6:
            out.append(f"{a['id']} -> {b['id']}: not contiguous")
    if abs(segs[-1]["t1"] - man["duration_s"]) > 1e-6:
        out.append("the last segment doesn't end at duration_s")
    for c in man["volume_cues"]:
        s = next((s for s in segs if abs(s["t0"] - c["t"]) < 1e-6), None)
        if s is None or s["kind"] != "silence" or s["t1"] - s["t0"] < CUE_GAP_MIN_S:
            out.append(f"cue at {c['t']} s: not at the start of a silent gap of {CUE_GAP_MIN_S} s or more")
        if not 0 <= c["volume"] <= VOLUME_CAP:
            out.append(f"cue at {c['t']} s: volume {c['volume']} is over the cap")
    for s in segs:
        cues = [c for c in man["volume_cues"] if c["t"] <= s["t0"] + 1e-6]
        if not cues or cues[-1]["volume"] != s["volume"]:
            out.append(f"{s['id']}: volume {s['volume']} isn't the cue in effect")
        if s["kind"] == "silence":
            continue
        if s["kind"] == "music" and s["volume"] != man["volume_main"]:
            out.append(f"{s['id']}: music not at volume_main")
        if s["lufs"] is None or s["lufs"] > TARGET_LUFS + 0.1 or (not s["tp_capped"] and s["lufs"] < TARGET_LUFS - 0.1):
            out.append(f"{s['id']}: loudness {s['lufs']}")
        if s["true_peak_dbtp"] is None or s["true_peak_dbtp"] > TP_MAX_DBTP + 0.01:
            out.append(f"{s['id']}: true peak {s['true_peak_dbtp']}")
        bt = read_beats(beats_dir / pathlib.Path(s["beats"]).name)[:, 0]
        if len(bt) < 8 or bt.min() < s["t0"] or bt.max() >= s["t1"]:
            out.append(f"{s['id']}: beats missing or outside the segment")
    return out


def table(man: dict) -> str:
    rows = [f"{man['name']}: {man['duration_s']:.2f} s ({man['duration_s'] / 60:.2f} min), {len(man['segments'])} "
            f"segments, cues " + ", ".join(f"{c['volume']}@{c['t']:g}s" for c in man["volume_cues"]),
            f"{'id':<22} {'kind':<7} {'t0':>8} {'t1':>8} {'dur':>6} {'vol':>3} {'std':<3} {'gain':>6} {'lufs':>6} "
            f"{'tp':>5} {'beats':>5} {'bpm':>6}  source"]
    for s in man["segments"]:
        src = s.get("source") or {}
        f = (lambda v, nd=1: "" if v is None else f"{v:.{nd}f}")
        rows.append(f"{s['id']:<22} {s['kind']:<7} {s['t0']:>8.2f} {s['t1']:>8.2f} {s['t1'] - s['t0']:>6.2f} "
                    f"{s['volume']:>3} {'y' if s['steady'] else '-':<3} {f(s.get('gain_db')):>6} {f(s.get('lufs')):>6} "
                    f"{f(s.get('true_peak_dbtp')):>5} {s.get('n_beats') or '':>5} {f(s.get('bpm')):>6}  "
                    + (f"{src.get('dataset')}/{src.get('id')} @{src.get('start_s', 0):.2f}+{src.get('dur_s', 0):.2f} "
                       f"[{s.get('genre')}]{' TP-capped' if s.get('tp_capped') else ''}" if src else
                       ("(cue)" if s.get("cue") else "")))
    return "\n".join(rows)


def build(which: str, date: str, out_root: pathlib.Path) -> pathlib.Path:
    if not re.fullmatch(r"\d{8}", date):
        raise SystemExit(f"--date {date!r}: YYYYMMDD")
    name = f"{which}-{date}"
    used = {p[0] for k, v in PICKS.items() if k != which for p in v}
    for p in PICKS.get(which, []):
        if p[0] in used:
            raise SystemExit(f"{p[0]}: in both programmes (only Sweet Dreams repeats)")
    log(f"building {name}")
    b = layout(which, sweet_dreams_beats())
    x = b.audio()
    q = np.clip(np.round(x * 32768.0), -32768, 32767).astype(np.int16)

    out_root.mkdir(parents=True, exist_ok=True)
    tmp = out_root / f".{name}.tmp-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "beats").mkdir(parents=True)
    flac = tmp / "programme.flac"
    sf.write(str(flac), np.column_stack([q, q]), FS, format="FLAC", subtype="PCM_16")
    z, fs_z = sf.read(str(flac), dtype="int16", always_2d=True)
    if fs_z != FS or z.shape != (len(q), 2) or not (np.array_equal(z[:, 0], q) and np.array_equal(z[:, 1], q)):
        raise SystemExit("programme.flac doesn't decode to what was written")
    mono = (z[:, 0].astype(np.float64) + z[:, 1].astype(np.float64)) / 2 / 32768.0
    for s in b.segments:                           # measured on what the Sonos will play
        if s["kind"] != "silence":
            seg = mono[int(round(s["t0"] * FS)):int(round(s["t1"] * FS))]
            s["lufs"] = round(loudness(seg), 2)
            s["true_peak_dbtp"] = round(true_peak_db(seg), 2)
    y48 = soxr.resample(mono, FS, FS_ANA, quality="VHQ").astype(np.float32)
    sf.write(str(tmp / "programme-48k.wav"), y48, FS_ANA, subtype="FLOAT")
    for sid, bt in b.beats.items():
        (tmp / "beats" / f"{sid}.beats").write_text("".join(f"{t:.6f}\t{int(p)}\n" for t, p in bt))

    man = {"name": name, "fs": FS, "duration_s": round(len(q) / FS, 6), "file": "programme.flac",
           "target_lufs": int(TARGET_LUFS), "volume_main": VOLUME_MAIN, "volume_cues": b.cues, "segments": b.segments,
           "pass": which, "date": date, "built_utc": utc_now(), "builder": "tools/room/audio_cal/programme.py",
           "analysis_file": "programme-48k.wav", "fs_analysis": FS_ANA, "true_peak_max_dbtp": TP_MAX_DBTP,
           "loudness": "ITU-R BS.1770-4 integrated (pyloudnorm), on the stereo file as played (dual mono: the mono "
                       "file reads 3.01 dB lower); true peak 4x oversampled",
           "notes": "every item is mono, the same on both channels; the gaps are digital silence; a cue's 4 s ramp "
                    "falls inside its gap"}
    probs = check(man, tmp / "beats")
    if probs:
        shutil.rmtree(tmp)
        raise SystemExit(f"{name}: " + "; ".join(probs))
    (tmp / "manifest.json").write_text(json.dumps(man, indent=1) + "\n")

    final = out_root / name
    if final.exists():
        old = out_root / f".{name}.old-{os.getpid()}"
        final.rename(old)
        tmp.rename(final)
        shutil.rmtree(old)
    else:
        tmp.rename(final)
    print(table(man))
    lo, hi = DUR_RANGE_MIN[which]
    mins = man["duration_s"] / 60
    print(f"{final}: {mins:.2f} min" + ("" if lo <= mins <= hi else f" (outside the expected {lo}-{hi} min)"))
    for f in ("programme.flac", "programme-48k.wav", "manifest.json"):
        print(f"  {f}: {(final / f).stat().st_size / 1e6:.1f} MB")
    print(f"  beats/: {len(b.beats)} files")
    return final


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    bp = sub.add_parser("build", help="build a programme")
    bp.add_argument("which", choices=("pass1", "pass2"))
    bp.add_argument("--date", required=True, help="YYYYMMDD, the programme's name: <pass>-<date>")
    bp.add_argument("--out", type=pathlib.Path, default=OUT_ROOT, help=f"the folder that gets <pass>-<date>/ "
                    f"(default {OUT_ROOT})")
    sp = sub.add_parser("show", help="print a built programme's segment table")
    sp.add_argument("programme", type=pathlib.Path)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        build(a.which, a.date, a.out.expanduser())
    else:
        print(table(read_json(a.programme.expanduser() / "manifest.json")))


if __name__ == "__main__":
    main()
