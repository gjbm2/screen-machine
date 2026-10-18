#!/usr/bin/env python3
"""Fetch beat-annotated music datasets into one uniform layout for the beat-stack evaluation (sm-roundtrip venv).

Why: the room audio calibration (tools/room/audio_cal/) fits a model of how this room, the PA and the mic degrade
music; beat_eval replays beat-annotated music through that model into our live beat stack and scores it. This script
gets the annotated music, from wherever each set lives, into one layout so the harness never cares about the source.

  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/fetch_datasets.py              # everything not yet fetched
  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/fetch_datasets.py --list       # what exists, what is fetched
  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/fetch_datasets.py --verify     # every item readable, beats inside
  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/fetch_datasets.py --only harmonix --harmonix-n 120

Idempotent: an item whose audio and beats exist is kept; a dropped item is remembered in failures.json and not
retried (--retry-failed retries; YouTube 403s, timeouts and network errors are retried on every run). The process
renices itself to 19 and takes ~/.cache/screen-machine/cpu.lock itself around each heavy step (alignment, decoding,
transcoding; a long round of alignments lets it go every 5 minutes), so it does not hold the lock through hours of
downloads; do not wrap it in flock(1) (or pass --no-cpu-lock if you do). After fetching a set it verifies it.

Layout (ROOT = ~/.cache/screen-machine/beat-datasets):
  ROOT/<name>/audio/<id>.<ext>   .wav, .flac or .opus (soundfile and ffmpeg read all three)
  ROOT/<name>/beats/<id>.beats   one line per beat: "<time_s>\\t<beat_position_in_bar>" (1 = downbeat; 0 = unknown)
  ROOT/<name>/index.json         {id: {id, dataset, genre, duration_s, audio, beats, has_downbeats, notes, ...}},
                                 paths relative to ROOT/<name>/ (run_batch.py's {id: entry} layout)
  ROOT/<name>/failures.json      {id: reason} for items tried and dropped
  ROOT/<name>/verify.json        the last --verify (or post-fetch) check: {verified_at, checked, ok, problems}
  ROOT/index.json                every dataset merged, keyed "<name>/<id>", paths relative to ROOT
  ROOT/summary.json              counts, hours, genres, failures and disk use per dataset
  ROOT/_downloads/               archives and raw downloads (big archives deleted after extraction)

Datasets (README.md "Datasets" has the detail):
  ballroom    698 x 30 s ballroom-dance excerpts (MTG ISMIR 2004 audio, CPJKU annotations), beats + downbeats
  gtzan       1000 x 30 s, 10 genres (GTZAN via the marsyas Hugging Face mirror; Marchand & Peeters annotations)
  smc         217 x 40 s hard, expressive excerpts; only if its host is back (offline all of 18 Oct 2026, no mirror)
  harmonix    a stratified subset of the Harmonix Set's 912 full pop tracks: audio from YouTube, aligned onto the
              annotated originals' timeline against the published mel spectrograms of those originals
  tapcorrect  101 full tracks annotated directly on their YouTube audio (Chordify TapCorrect, ISMIR 2019)
  asap        rubato solo piano (ASAP annotations on MAESTRO audio): a should-not-falsely-lock set

Every item is checked against its audio (``onset_check``): the median lag of the audio's onsets behind the annotated
beats and how much they stand out. Harmonix and TapCorrect drop items whose beats do not line up; the other sets only
report it (``onset_lag_ms``, ``onset_salience`` in the index).
"""
import argparse
import collections
import contextlib
import csv
import datetime as dt
import fcntl
import glob
import io
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):   # one core: BLAS threads spin under
    os.environ.setdefault(_v, "1")                                         # nice 19 beside the renderer

import numpy as np
import soundfile as sf
import soxr

CACHE = os.path.expanduser("~/.cache/screen-machine")
ROOT = os.path.join(CACHE, "beat-datasets")
CPU_LOCK = os.path.join(CACHE, "cpu.lock")
FFMPEG = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
YTDLP = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
OUT_FS = 48000                      # rate of the audio this script writes itself (show_service's stream rate)

BALLROOM_AUDIO = "http://mtg.upf.edu/ismir2004/contest/tempoContest/data1.tar.gz"   # 1.45 GB; HEAD is refused, GET works
BALLROOM_ANN = ("CPJKU/BallroomAnnotations", "master")
BOCK_TREE = "https://api.github.com/repos/superbock/ISMIR2019/git/trees/HEAD?recursive=1"   # his de-duplicated sets
GTZAN_AUDIO = "https://huggingface.co/datasets/marsyas/gtzan/resolve/main/data/genres.tar.gz"   # 1.23 GB, wav
GTZAN_ANN = ("TempoBeatDownbeat/gtzan_tempo_beat", "main")
SMC_URLS = ["https://smc.inesctec.pt/data/SMC_MIREX.zip", "http://smc.inescporto.pt/data/SMC_MIREX.zip",
            "http://smc.inesctec.pt/data/SMC_MIREX.zip"]
HARMONIX_RAW = "https://raw.githubusercontent.com/urinieto/harmonixset/master/dataset/"
HARMONIX_MELS = "https://www.dropbox.com/s/zxnqlx0hxz0lsyc/Harmonix_melspecs.tgz?dl=1"   # 1.28 GB, the originals' mels
TAPCORRECT = ("chordify/tapcorrect", "master")
ASAP_RAW = "https://raw.githubusercontent.com/fosfrancesco/asap-dataset/master/"
MAESTRO_HF = "https://huggingface.co/datasets/ddPn08/maestro-v3.0.0/resolve/main/"     # MAESTRO v3, one file each

# Harmonix subset: shares of the subset per genre, weighted towards what plays at a party, plus a few tracks not in
# 4/4 (taken first, counted in their genre's share). Genres not listed share "other".
HARMONIX_SHARES = {"Pop": 0.23, "Dance/Electronic": 0.20, "Hip-Hop": 0.15, "R&B": 0.07, "Funk/Disco": 0.07,
                   "Reggaeton": 0.05, "Rock": 0.05, "Alternative": 0.04, "Metal": 0.04, "Country": 0.05,
                   "Classic Rock": 0.02, "other": 0.03}
HARMONIX_ODD_METER = 6
HARMONIX_MIN_SCORE = 0.95           # Harmonix's own YouTube-vs-original DTW score; below it the video is another cut

# The mel spectrograms Harmonix published (librosa 0.7.0, their info.json): power mel of the original, mono 22.05 kHz.
HX_SR, HX_NFFT, HX_HOP, HX_NMELS = 22050, 2048, 1024, 80
# Those spectrograms were made from the originals' MP3s, decoded without gapless trimming, so their content sits one
# MP3 encoder + decoder delay (576 + 529 samples at 44.1 kHz) later than the annotations' timeline. Measured on the
# first 29 aligned tracks (18 Oct): the onsets came a median 11.8 ms after the beats, against -13.7 ms on TapCorrect
# and -12.0 ms on Ballroom (same check, audio and annotations from the same file): 25 ms apart. The cut is moved by it.
HARMONIX_MP3_DELAY_S = 1105 / 44100

# Alignment acceptance (Harmonix): the YouTube audio must match the original's mel (mean per-band correlation), the
# local lag of nearly every 20 s window must agree with the global one, and the first and last parts of the track
# must agree to the millisecond-scale (no speed change, no edit).
ALIGN_MIN_R = 0.80
ALIGN_MIN_WINDOWS_OK = 0.85
ALIGN_MAX_DRIFT_MS = 15.0

# onset_check acceptance (Harmonix, TapCorrect): see onset_check.
ONSET_MIN_SALIENCE = 2.0
ONSET_MAX_LAG_MS = 60.0
ONSET_MIN_SEG_OK = 0.8


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


class Skip(Exception):
    """This item cannot be fetched or does not line up; the message goes to failures.json."""


# ---------------------------------------------------------------- plumbing

USE_CPU_LOCK = True


@contextlib.contextmanager
def cpu_lock():
    """The host-wide heavy-work lock (the same lock flock(1) takes; one heavy process at a time beside the renderer)."""
    if not USE_CPU_LOCK:
        yield
        return
    os.makedirs(CACHE, exist_ok=True)
    with open(CPU_LOCK, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def locked_slices(items, hold_s=300.0):
    """Yield items one at a time with the CPU lock held, letting it go after every hold_s so the replays queued
    behind it (run_batch.py takes the lock per track) get a turn during a long round of alignments."""
    it = iter(items)
    while True:
        with cpu_lock():
            t0 = time.time()
            for item in it:
                yield item
                if time.time() - t0 > hold_s:
                    break
            else:
                return


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def download(url, dest, rate=None, timeout=None, retries=4):
    """curl ``url`` to ``dest`` (resumable through dest.part); returns dest. Raises Skip on failure."""
    if os.path.exists(dest):
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    cmd = ["curl", "-fsSL", "-A", UA, "--retry", str(retries), "--retry-delay", "5", "--connect-timeout", "20",
           "-C", "-", "-o", part]
    if rate:
        cmd += ["--limit-rate", rate]
    if timeout:
        cmd += ["--max-time", str(timeout)]
    p = subprocess.run(cmd + [url], capture_output=True, text=True)
    if p.returncode == 33 or (p.returncode == 22 and "416" in p.stderr):    # .part already complete
        p = subprocess.CompletedProcess(cmd, 0)
    if p.returncode != 0:
        raise Skip(f"download failed ({url}): curl exit {p.returncode} {p.stderr.strip()[-200:]}")
    os.replace(part, dest)
    return dest


def fetch_bytes(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def cached_text(url, dest):
    """A small text file, fetched once into dest."""
    if not os.path.exists(dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        data = fetch_bytes(url)
        with open(dest + ".tmp", "wb") as f:
            f.write(data)
        os.replace(dest + ".tmp", dest)
    with open(dest, encoding="utf-8", errors="replace") as f:
        return f.read()


def github_zip(repo, branch, dl_dir, rate=None):
    """A repo snapshot as a ZipFile (members are read in memory, never extracted)."""
    dest = os.path.join(dl_dir, repo.replace("/", "__") + f"-{branch}.zip")
    download(f"https://codeload.github.com/{repo}/zip/refs/heads/{branch}", dest, rate)
    return zipfile.ZipFile(dest)


def decode(path, fs=None, channels=None):
    """Any audio file to float32 (n, ch) via ffmpeg (applies Opus pre-skip and AAC priming like a player)."""
    cmd = [FFMPEG, "-v", "error", "-nostdin", "-i", path, "-vn", "-f", "f32le", "-acodec", "pcm_f32le"]
    if fs:
        cmd += ["-ar", str(fs)]
    if not channels:
        channels = probe_channels(path)
    cmd += ["-ac", str(channels), "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0 or not p.stdout:
        raise Skip(f"ffmpeg could not decode {os.path.basename(path)}: {p.stderr.decode(errors='replace')[-200:]}")
    return np.frombuffer(p.stdout, np.float32).reshape(-1, channels), fs


def probe_channels(path):
    try:
        return min(2, sf.info(path).channels)
    except Exception:                                        # webm, m4a: ask ffprobe
        p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels",
                            "-of", "csv=p=0", path], capture_output=True, text=True)
        try:
            return min(2, int(p.stdout.strip().splitlines()[0]))
        except (ValueError, IndexError):
            return 2


def take(y, start, n):
    """y[start:start+n] along axis 0, zero-padded where it falls outside y."""
    out = np.zeros((n,) + y.shape[1:], y.dtype)
    a, b = max(0, start), min(len(y), start + n)
    if b > a:
        out[a - start:b - start] = y[a:b]
    return out


def write_beats(path, rows):
    rows = sorted((float(t), int(p)) for t, p in rows if math.isfinite(float(t)) and float(t) >= 0)
    with open(path + ".tmp", "w") as f:
        f.writelines(f"{t:.6f}\t{p}\n" for t, p in rows)
    os.replace(path + ".tmp", path)
    return rows


def read_beats(path):
    rows = []
    with open(path) as f:
        for line in f:
            parts = line.split()
            if parts:
                rows.append((float(parts[0]), int(float(parts[1])) if len(parts) > 1 else 0))
    return rows


def ytdlp_youtube(url, out_base, rate=None):
    """Best audio (Opus preferred) of one video to out_base.<ext>; returns (path, info). Raises Skip."""
    have = [p for p in glob.glob(glob.escape(out_base) + ".*")
            if not p.endswith((".part", ".ytdl", ".json", ".tmp")) and ".part-" not in p]
    meta = out_base + ".json"
    if have and os.path.exists(meta):
        return have[0], read_json(meta, {})
    os.makedirs(os.path.dirname(out_base), exist_ok=True)
    cmd = [YTDLP, "-f", "bestaudio[acodec=opus]/bestaudio", "--no-playlist", "--no-progress", "--no-warnings",
           "--sleep-interval", "2", "--max-sleep-interval", "5", "--retries", "3",
           "-o", out_base + ".%(ext)s", "-j", "--no-simulate"]
    node = shutil.which("node")
    if node:
        cmd += ["--js-runtimes", f"node:{node}"]
    if rate:
        cmd += ["--limit-rate", rate]
    try:
        p = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        raise Skip(f"yt-dlp timed out on {url}")
    if p.returncode != 0:
        err = [l for l in p.stderr.splitlines() if "ERROR" in l] or p.stderr.splitlines() or ["?"]
        raise Skip(f"yt-dlp: {err[-1].strip()[:240]}")
    info = {}
    for line in p.stdout.splitlines():
        if line.startswith("{"):
            try:
                info = json.loads(line)
            except ValueError:
                pass
    have = [p for p in glob.glob(glob.escape(out_base) + ".*")
            if not p.endswith((".part", ".ytdl", ".json", ".tmp")) and ".part-" not in p]
    if not have:
        raise Skip(f"yt-dlp reported success but wrote no file for {url}")
    keep = {k: info.get(k) for k in ("id", "title", "duration", "uploader", "channel", "acodec", "abr", "asr", "ext",
                                     "upload_date", "webpage_url")}
    write_json(meta, keep)
    return have[0], keep


# ---------------------------------------------------------------- checks

def onset_envelope(x, fs):
    """Spectral-flux onset envelope of x at 5 ms frames (22.05 kHz, 1024-point frames, up to 10 kHz, local mean
    removed and half-wave rectified). Returns (env, frame times in s, duration in s)."""
    mono = x.mean(axis=1) if x.ndim == 2 else x
    y = soxr.resample(mono.astype(np.float32), fs, 22050) if fs != 22050 else mono.astype(np.float32)
    hop, n_fft = 110, 1024
    yp = np.pad(y, n_fft // 2)
    n_frames = 1 + (len(yp) - n_fft) // hop
    win = np.hanning(n_fft).astype(np.float32)
    env = np.zeros(n_frames, np.float32)
    prev = None
    for a in range(0, n_frames, 4096):                       # chunked: a 5-minute track is 60k frames
        idx = np.arange(a, min(n_frames, a + 4096))
        mag = np.log1p(100.0 * np.abs(np.fft.rfft(yp[idx[:, None] * hop + np.arange(n_fft)] * win, axis=1))[:, :465])
        d = np.maximum(0.0, np.diff(mag if prev is None else np.vstack([prev, mag]), axis=0)).sum(axis=1)
        env[idx] = d if prev is not None else np.concatenate([[0.0], d])
        prev = mag[-1:]
    k = int(0.4 * 22050 / hop)
    env = np.maximum(0.0, env - np.convolve(env, np.ones(k) / k, mode="same"))
    return env, np.arange(n_frames) * hop / 22050, len(y) / 22050


def onset_check(x, fs, beats, seg_s=30.0):
    """Do the annotated beats sit on the audio's onsets? Returns {lag_ms, salience, seg_ok, segments} or None.

    The onset envelope is sampled at every annotated beat shifted by tau, over tau in +-half the median beat period.
    lag_ms is the best tau within +-150 ms (positive: the onsets come after the annotated beats). salience is the
    envelope's mean at that tau over its mean across the whole tau range: beats that line up give a clear peak (well
    above 2), beats that do not, about 1. The same per 30 s segment: seg_ok is the share of segments whose own lag
    is within 25 ms of the track's, with salience above ONSET_MIN_SALIENCE (an edit or a drift breaks it).
    """
    env, times, dur = onset_envelope(x, fs)
    t = np.asarray([b for b, _ in beats], float)
    t = t[(t > 1.0) & (t < dur - 1.0)]
    if len(t) < 8:
        return None
    period = float(np.median(np.diff(t)))
    taus = np.arange(-0.5 * period, 0.5 * period, 0.002)
    near = np.abs(taus) <= 0.150

    def peak(tt):
        v = np.interp(tt[None, :] + taus[:, None], times, env).mean(axis=1)
        if not near.any() or v.mean() <= 0:
            return None, None
        best = int(np.argmax(np.where(near, v, -np.inf)))
        return float(taus[best]) * 1000.0, float(v[best] / v.mean())

    lag, sal = peak(t)
    if lag is None:
        return None
    segs = []
    for a in np.arange(0.0, dur, seg_s):
        tt = t[(t >= a) & (t < a + seg_s)]
        if len(tt) >= 8:
            segs.append(peak(tt))
    ok = [s for s in segs if s[0] is not None and abs(s[0] - lag) <= 25.0 and s[1] >= ONSET_MIN_SALIENCE]
    return {"lag_ms": round(lag, 1), "salience": round(sal, 2), "seg_ok": round(len(ok) / len(segs), 2) if segs else None,
            "segments": len(segs)}


def onset_ok(oc, need_segments):
    """The acceptance rule for sets checked only against their own audio (TapCorrect) or after an alignment
    (Harmonix): a clear peak, a small lag and, without a reference, the same lag through the track."""
    if not oc or oc["salience"] < ONSET_MIN_SALIENCE or abs(oc["lag_ms"]) > ONSET_MAX_LAG_MS:
        return False
    return not need_segments or (oc["seg_ok"] or 0) >= ONSET_MIN_SEG_OK


def onset_fields(oc):
    if not oc:
        return {"onset_lag_ms": None, "onset_salience": None, "onset_seg_ok": None}
    return {"onset_lag_ms": oc["lag_ms"], "onset_salience": oc["salience"], "onset_seg_ok": oc["seg_ok"]}


# ---------------------------------------------------------------- Harmonix alignment

def _hz_to_mel(f):
    f = np.asarray(f, float)
    m = f / (200.0 / 3)
    lg = f >= 1000.0
    m[lg] = 15.0 + np.log(f[lg] / 1000.0) / (np.log(6.4) / 27.0)
    return m


def _mel_to_hz(m):
    m = np.asarray(m, float)
    f = m * (200.0 / 3)
    lg = m >= 15.0
    f[lg] = 1000.0 * np.exp((np.log(6.4) / 27.0) * (m[lg] - 15.0))
    return f


def _mel_fb():
    """librosa 0.7's filters.mel(22050, 2048, 80) (Slaney scale and area norm), which made Harmonix's spectrograms."""
    fft_f = np.linspace(0, HX_SR / 2, 1 + HX_NFFT // 2)
    mel_f = _mel_to_hz(np.linspace(_hz_to_mel([0.0])[0], _hz_to_mel([HX_SR / 2])[0], HX_NMELS + 2))
    fdiff = np.diff(mel_f)
    ramps = np.subtract.outer(mel_f, fft_f)
    w = np.zeros((HX_NMELS, len(fft_f)))
    for i in range(HX_NMELS):
        w[i] = np.maximum(0, np.minimum(-ramps[i] / fdiff[i], ramps[i + 2] / fdiff[i + 1]))
    w *= (2.0 / (mel_f[2:HX_NMELS + 2] - mel_f[:HX_NMELS]))[:, None]
    return w.astype(np.float32)


_FB = None
_WIN = None


def hx_mel_db(y):
    """librosa 0.7 melspectrogram (center, reflect pad, periodic Hann, power 2) of mono 22.05 kHz y, in dB."""
    global _FB, _WIN
    if _FB is None:
        _FB = _mel_fb()
        n = np.arange(HX_NFFT)
        _WIN = (0.5 - 0.5 * np.cos(2 * np.pi * n / HX_NFFT)).astype(np.float32)
    yp = np.pad(y.astype(np.float32), HX_NFFT // 2, mode="reflect")
    n_frames = 1 + (len(yp) - HX_NFFT) // HX_HOP
    out = np.empty((HX_NMELS, n_frames), np.float32)
    for a in range(0, n_frames, 2048):
        idx = np.arange(a, min(n_frames, a + 2048))
        fr = yp[idx[:, None] * HX_HOP + np.arange(HX_NFFT)[None, :]] * _WIN
        out[:, idx] = _FB @ (np.abs(np.fft.rfft(fr, axis=1)) ** 2).T
    return to_db(out)


def to_db(m):
    d = 10.0 * np.log10(np.maximum(m, 1e-10))
    return np.maximum(d, d.max() - 80.0)


def _band_r(a, b):
    """Mean over bands of the Pearson correlation in time between two (bands, frames) arrays."""
    za = a - a.mean(axis=1, keepdims=True)
    zb = b - b.mean(axis=1, keepdims=True)
    na, nb = np.sqrt((za ** 2).sum(axis=1)), np.sqrt((zb ** 2).sum(axis=1))
    ok = (na > 1e-3) & (nb > 1e-3)
    if not ok.any():
        return 0.0
    return float(((za * zb).sum(axis=1)[ok] / (na[ok] * nb[ok])).mean())


def hx_mel_db_at(frames_view, pad, centers):
    """The same mel (dB) for frames centred at the given sample positions; frames_view is
    sliding_window_view(y zero-padded by ``pad`` on both sides, HX_NFFT)."""
    hx_mel_db(np.zeros(HX_NFFT, np.float32))                # builds _FB and _WIN once
    idx = np.clip(np.asarray(centers) - HX_NFFT // 2 + pad, 0, len(frames_view) - 1)
    return to_db(_FB @ (np.abs(np.fft.rfft(frames_view[idx] * _WIN, axis=1)) ** 2).T)


def align_to_original(y22, ref_db):
    """Where the annotated original starts in the YouTube audio y22 (mono 22.05 kHz), and how well it matches.

    1. Global lag (frames) from the FFT cross-correlation of the per-band z-scored log-mels.
    2. Per 20 s window (10 s steps) of the original: the best local lag within +-3 s; nearly all must equal the global
       one (+-1 frame) at a correlation above 0.5, or the video is another edit (a music video's inserted break, a
       radio edit) or speed.
    3. Sub-frame offset: the YouTube mel is recomputed, on 600 loud frames, at sample shifts around the global lag
       (256- then 32-sample grid, then a parabola) and correlated with the original's; the same on frames from the
       first and the last 40 % of the track gives the drift.
    Returns a dict: offset_s (original t = 0 is YouTube t = offset_s), r (mean per-band correlation at offset_s),
    windows_ok, windows, drift_ms, yt_s.
    """
    yt_db = hx_mel_db(y22)
    ko, ky = ref_db.shape[1], yt_db.shape[1]

    def z(a, axis=1):
        return (a - a.mean(axis=axis, keepdims=True)) / (a.std(axis=axis, keepdims=True) + 1e-6)

    zo, zy = z(ref_db), z(yt_db)
    nfft = 1 << int(math.ceil(math.log2(ko + ky)))
    c = np.fft.irfft((np.conj(np.fft.rfft(zo, nfft, axis=1)) * np.fft.rfft(zy, nfft, axis=1)).sum(axis=0), nfft)
    lags = np.arange(-(ko // 2), ky - ko // 2)               # at least half of the original overlaps
    lag = int(lags[np.argmax(c[lags % nfft])])

    w, step, search = int(20 * HX_SR / HX_HOP), int(10 * HX_SR / HX_HOP), int(3 * HX_SR / HX_HOP)
    level = ref_db.mean(axis=0)
    loud = level > level.max() - 40.0
    n_win = n_ok = 0
    for a in range(0, max(1, ko - w + 1), step):
        lo, hi = a + lag - search, a + lag + search + w
        if loud[a:a + w].mean() < 0.5 or lo < 0 or hi > ky:
            continue
        zs = z(ref_db[:, a:a + w])                                              # (bands, w)
        win = z(np.lib.stride_tricks.sliding_window_view(yt_db[:, lo:hi], w, axis=1), axis=2)   # (bands, lags, w)
        rs = np.einsum("bw,blw->bl", zs, win).mean(axis=0) / w
        best = int(np.argmax(rs))
        n_win += 1
        n_ok += abs(best - search) <= 1 and rs[best] > 0.5
    windows_ok = n_ok / n_win if n_win else 0.0

    frames = np.flatnonzero(loud[2:ko - 2]) + 2
    pad = (ko + 4) * HX_HOP + HX_NFFT                       # every shift the refinement can try stays inside
    view = np.lib.stride_tricks.sliding_window_view(np.pad(y22.astype(np.float32), pad), HX_NFFT)

    def pick(k0, k1, n=600):
        f = frames[(frames >= k0) & (frames < k1)]
        return f[np.linspace(0, len(f) - 1, min(n, len(f))).astype(int)] if len(f) else f

    def r_at(shift, ks):
        return _band_r(ref_db[:, ks], hx_mel_db_at(view, pad, shift + ks * HX_HOP))

    def refine(ks, center, coarse=True):
        best = center
        for g in ((range(-1024, 1025, 256),) if coarse else ()) + (range(-256, 257, 32),):
            rs = {best + d: r_at(best + d, ks) for d in g}
            best = max(rs, key=rs.get)
        r0, rm, rp = r_at(best, ks), r_at(best - 32, ks), r_at(best + 32, ks)
        den = rm - 2 * r0 + rp
        frac = 0.5 * (rm - rp) / den if den < 0 else 0.0
        return best + 32 * max(-1.0, min(1.0, frac)), r0

    ks_all = pick(0, ko)
    if len(ks_all) < 50:
        return {"offset_s": lag * HX_HOP / HX_SR, "r": 0.0, "windows_ok": windows_ok, "windows": n_win,
                "drift_ms": None, "yt_s": round(len(y22) / HX_SR, 2)}
    s0, _ = refine(ks_all, lag * HX_HOP)
    k40 = int(0.4 * ko)
    s_first, _ = refine(pick(0, k40, 300), int(round(s0)), coarse=False)
    s_last, _ = refine(pick(ko - k40, ko, 300), int(round(s0)), coarse=False)
    seg = take(y22, int(round(s0)), (ko - 1) * HX_HOP + 1)                     # r over every frame at the offset
    r_all = _band_r(ref_db[:, 2:ko - 2], hx_mel_db(seg)[:, 2:ko - 2])
    return {"offset_s": round(s0 / HX_SR, 5), "r": round(r_all, 4), "windows_ok": round(float(windows_ok), 3),
            "windows": n_win, "drift_ms": round((s_last - s_first) / HX_SR * 1000.0, 2),
            "yt_s": round(len(y22) / HX_SR, 2)}


# ---------------------------------------------------------------- per-dataset fetchers

class Dataset:
    """One dataset's folder: audio/, beats/, index.json, failures.json."""

    def __init__(self, name, args):
        self.name, self.args = name, args
        self.dir = os.path.join(args.root, name)
        self.audio_dir, self.beats_dir = os.path.join(self.dir, "audio"), os.path.join(self.dir, "beats")
        self.dl = os.path.join(args.root, "_downloads", name)
        for d in (self.audio_dir, self.beats_dir, self.dl):
            os.makedirs(d, exist_ok=True)
        self.index_path, self.fail_path = os.path.join(self.dir, "index.json"), os.path.join(self.dir, "failures.json")
        self.index = read_json(self.index_path, {})
        self.failures = {} if args.retry_failed else read_json(self.fail_path, {})
        # transient failures (YouTube's sporadic 403s, timeouts, network) are retried on every run
        self.failures = {k: v for k, v in self.failures.items()
                         if not re.search(r"HTTP Error 403|timed out|curl exit (6|7|28|35|56)\b", v)}
        # drop index entries whose files went missing
        self.index = {k: e for k, e in self.index.items()
                      if os.path.exists(os.path.join(self.dir, e["audio"]))
                      and os.path.exists(os.path.join(self.dir, e["beats"]))}

    def audio_path(self, iid, ext):
        return os.path.join(self.audio_dir, f"{iid}.{ext}")

    def beats_path(self, iid):
        return os.path.join(self.beats_dir, f"{iid}.beats")

    def done(self, iid):
        return iid in self.index

    def fail(self, iid, reason):
        self.failures[iid] = str(reason)[:400]
        log(f"  {self.name}/{iid}: dropped: {reason}")

    def add(self, iid, audio_file, rows, genre, has_downbeats, notes, **extra):
        """Write the beats file, measure the audio and add the index entry (check_pending adds the onset check)."""
        rows = write_beats(self.beats_path(iid), rows)
        info = sf.info(audio_file)
        dur = info.frames / info.samplerate
        times = np.array([t for t, _ in rows])
        ibi = np.diff(times)
        pos = [p for _, p in rows]
        bpb = None
        if has_downbeats and any(p > 0 for p in pos):
            bars, cur = [], 0
            for p in pos:
                if p == 1 and cur:
                    bars.append(cur)
                cur = p if p > 0 else cur
            bpb = collections.Counter(bars).most_common(1)[0][0] if bars else max(pos)
        e = {"id": iid, "dataset": self.name, "genre": genre, "duration_s": round(dur, 3),
             "audio": os.path.relpath(audio_file, self.dir), "beats": os.path.relpath(self.beats_path(iid), self.dir),
             "has_downbeats": bool(has_downbeats), "notes": notes, "n_beats": len(rows),
             "bpm": round(60.0 / float(np.median(ibi)), 2) if len(ibi) else None, "beats_per_bar": bpb,
             "fs": info.samplerate, "channels": info.channels,
             "beats_after_audio": int((times > dur + 0.05).sum())}
        e.update(extra)
        self.index[iid] = e
        self.failures.pop(iid, None)
        return e

    def check_pending(self):
        """The report-only onset check for every entry without one, in one hold of the CPU lock (taking the lock
        per item would wait behind every replay run_batch.py queues)."""
        todo = [i for i, e in self.index.items() if "onset_lag_ms" not in e]
        if not todo or self.args.no_onset_check:
            return
        log(f"{self.name}: onset check of {len(todo)} items (waiting for the CPU lock)")
        with cpu_lock():
            t0 = time.time()
            for n, iid in enumerate(todo):
                e = self.index[iid]
                try:
                    x, fs = sf.read(os.path.join(self.dir, e["audio"]), dtype="float32", always_2d=True)
                    e.update(onset_fields(onset_check(x, fs, read_beats(os.path.join(self.dir, e["beats"])))))
                except Exception as ex:                      # report-only: never lose the item over it
                    log(f"  {self.name}/{iid}: onset check failed ({ex})")
                    e.update(onset_fields(None))
                if n % 100 == 99:
                    self.save()
            log(f"{self.name}: onset checks done in {time.time() - t0:.0f} s")
        self.save()

    def save(self):
        write_json(self.index_path, self.index)
        write_json(self.fail_path, self.failures)


def fetch_ballroom(args):
    ds = Dataset("ballroom", args)
    z = github_zip(*BALLROOM_ANN, ds.dl, args.rate)
    ann = {}
    for n in z.namelist():
        m = re.match(r"^[^/]+/([^/]+)\.beats$", n)
        if m:
            rows = []
            for line in z.read(n).decode().splitlines():
                parts = line.split()
                if parts:
                    rows.append((float(parts[0]), int(float(parts[1])) if len(parts) > 1 else 0))
            ann[m.group(1)] = rows
    log(f"ballroom: {len(ann)} annotation files")
    # Böck's ISMIR 2019 ballroom set leaves out the 13 exact duplicates Sturm found; flag them, keep them
    try:
        tree = json.loads(fetch_bytes(BOCK_TREE))["tree"]
        bock = {re.sub(r"^ballroom_", "", os.path.basename(t["path"])[:-6]) for t in tree
                if t["path"].startswith("ballroom/annotations/beats/") and t["path"].endswith(".beats")}
    except Exception as ex:                                  # GitHub API rate limit etc.: no flags then
        log(f"ballroom: no duplicate list ({ex})")
        bock = set()
    todo = [i for i in ann if not ds.done(i) and i not in ds.failures]
    if not todo:
        log("ballroom: all fetched")
        return ds
    styles = {}
    missing = [i for i in todo if not os.path.exists(ds.audio_path(i, "wav"))]
    if missing:
        tgz = download(BALLROOM_AUDIO, os.path.join(ds.dl, "data1.tar.gz"), args.rate)
        log("ballroom: extracting")
        with tarfile.open(tgz, "r|gz") as tf:
            for m in tf:
                mm = re.match(r"^BallroomData/([^/]+)/([^/]+)\.wav$", m.name)
                if not (m.isfile() and mm) or mm.group(2).startswith("._"):
                    continue
                styles[mm.group(2)] = mm.group(1)
                out = ds.audio_path(mm.group(2), "wav")
                if not os.path.exists(out):
                    with open(out + ".tmp", "wb") as f:
                        shutil.copyfileobj(tf.extractfile(m), f)
                    os.replace(out + ".tmp", out)
        with open(os.path.join(ds.dir, "styles.json"), "w") as f:
            json.dump(styles, f, indent=1, sort_keys=True)
    styles = styles or read_json(os.path.join(ds.dir, "styles.json"), {})
    for i in todo:
        a = ds.audio_path(i, "wav")
        if not os.path.exists(a):
            ds.fail(i, "annotation without audio in the MTG archive")
            continue
        dup = bool(bock) and i not in bock
        notes = ("30 s excerpt from ballroomdancers.com (ISMIR 2004 rhythm set); beats and downbeats by Krebs, "
                 "Böck & Widmer 2013")
        if dup:
            notes += "; an exact duplicate of another excerpt (Sturm 2014), left out of Böck's ISMIR 2019 set"
        ds.add(i, a, ann[i], styles.get(i, "unknown"), True, notes, duplicate=dup, kind="excerpt",
               tags=["excerpt", "dance"] + (["duplicate"] if dup else []))
    ds.save()
    if not args.keep_downloads and len(ds.index) >= len(ann) - len(ds.failures):
        for p in glob.glob(os.path.join(ds.dl, "data1.tar.gz*")):
            os.remove(p)
    return ds


def fetch_gtzan(args):
    ds = Dataset("gtzan", args)
    z = github_zip(*GTZAN_ANN, ds.dl, args.rate)
    ann = {}
    for n in z.namelist():
        m = re.match(r"^[^/]+/beats/gtzan_([a-z]+)_(\d{5})\.beats$", n)
        if m:
            rows = []
            for line in z.read(n).decode().splitlines():
                parts = line.split()
                if parts:
                    rows.append((float(parts[0]), int(float(parts[1])) if len(parts) > 1 else 0))
            ann[f"{m.group(1)}.{m.group(2)}"] = rows
    log(f"gtzan: {len(ann)} annotation files")
    todo = [i for i in ann if not ds.done(i) and i not in ds.failures]
    if not todo:
        log("gtzan: all fetched")
        return ds
    if any(not os.path.exists(ds.audio_path(i, "wav")) for i in todo):
        tgz = download(GTZAN_AUDIO, os.path.join(ds.dl, "genres.tar.gz"), args.rate)
        log("gtzan: extracting")
        with tarfile.open(tgz, "r|gz") as tf:
            for m in tf:
                mm = re.match(r"^genres/([a-z]+)/([a-z]+\.\d{5})\.(wav|au)$", m.name)
                if not (m.isfile() and mm):
                    continue
                out = ds.audio_path(mm.group(2), "wav")
                if os.path.exists(out):
                    continue
                data = tf.extractfile(m).read()
                try:
                    if mm.group(3) == "wav":
                        sf.info(io.BytesIO(data))
                    else:                                    # the original .au release: rewrite as wav
                        x, fs = sf.read(io.BytesIO(data), dtype="int16")
                        buf = io.BytesIO()
                        sf.write(buf, x, fs, format="WAV", subtype="PCM_16")
                        data = buf.getvalue()
                except Exception as ex:
                    log(f"gtzan: {mm.group(2)} unreadable ({ex})")
                    continue
                with open(out + ".tmp", "wb") as f:
                    f.write(data)
                os.replace(out + ".tmp", out)
    for i in todo:
        a = ds.audio_path(i, "wav")
        if not os.path.exists(a):
            ds.fail(i, "no readable audio in the GTZAN archive")
            continue
        genre = i.split(".")[0]
        notes = ("30 s excerpt, GTZAN genre collection (Tzanetakis & Cook 2002; 22.05 kHz mono, some clips "
                 "distorted, repeated or mislabelled, Sturm 2013); beats and downbeats by Marchand & Peeters 2015")
        tags = ["excerpt"] + (["expressive"] if genre in ("classical", "jazz", "blues") else [])
        ds.add(i, a, ann[i], genre, True, notes, kind="excerpt", tags=tags)
    ds.save()
    if not args.keep_downloads and len(ds.index) >= len(ann) - len(ds.failures):
        for p in glob.glob(os.path.join(ds.dl, "genres.tar.gz*")):
            os.remove(p)
    return ds


def fetch_smc(args):
    ds = Dataset("smc", args)
    if ds.index:
        log(f"smc: {len(ds.index)} fetched")
        return ds
    zpath = os.path.join(ds.dl, "SMC_MIREX.zip")
    errors = []
    for url in SMC_URLS:
        try:
            download(url, zpath, args.rate, timeout=1800, retries=0)    # a dead host: fail fast
            zipfile.ZipFile(zpath).testzip()
            break
        except (Skip, zipfile.BadZipFile, OSError) as ex:
            code = re.search(r"curl exit (\d+)", str(ex))
            errors.append(f"{url} ({'curl exit ' + code.group(1) if code else ex})")
            for p in glob.glob(zpath + "*"):
                os.remove(p)
    else:
        reason = ("SMC host offline: " + ", ".join(errors) + ". The Wayback Machine's copy of "
                  "SMC_MIREX.zip is cut at 10 MB (annotations and 4 of the 217 excerpts), so no audio source exists")
        ds.failures = {"_dataset": reason}
        ds.save()
        log(f"smc: unavailable ({len(errors)} URLs tried)")
        return ds
    z = zipfile.ZipFile(zpath)
    names = [n for n in z.namelist() if "__MACOSX" not in n]
    ann_dir = "SMC_MIREX_Annotations_05_08_2014" if any("05_08_2014" in n for n in names) else "SMC_MIREX_Annotations"
    for n in names:
        m = re.match(rf"^.*{ann_dir}/(SMC_\d+)_?([^/]*)\.txt$", n)
        if not m:
            continue
        iid, tags = m.group(1), m.group(2)
        wav = next((w for w in names if w.endswith(f"/{iid}.wav")), None)
        if not wav:
            ds.fail(iid, "annotation without audio")
            continue
        a = ds.audio_path(iid, "wav")
        with open(a, "wb") as f:
            f.write(z.read(wav))
        rows = [(float(l.split()[0]), 0) for l in z.read(n).decode().splitlines() if l.strip()]
        ds.add(iid, a, rows, "hard (SMC)", False,
               f"40 s hard excerpt (Holzapfel et al. 2012, selective sampling), annotation tags {tags}; beats only",
               kind="excerpt", tags=["excerpt", "hard", "no-false-lock"])
    ds.save()
    return ds


def _spread(items, key):
    """items sorted by key, reordered by bit reversal so that any prefix spreads over the key's range."""
    items = sorted(items, key=key)
    n = len(items)
    if n < 2:
        return items
    bits = max(1, (n - 1).bit_length())
    order = sorted(range(1 << bits), key=lambda i: int(f"{i:0{bits}b}"[::-1], 2))
    return [items[i] for i in order if i < n]


def fetch_harmonix(args):
    ds = Dataset("harmonix", args)
    meta = list(csv.DictReader(io.StringIO(cached_text(HARMONIX_RAW + "metadata.csv", os.path.join(ds.dl, "metadata.csv")))))
    urls = {r["File"]: r["URL"] for r in csv.DictReader(io.StringIO(
        cached_text(HARMONIX_RAW + "youtube_urls.csv", os.path.join(ds.dl, "youtube_urls.csv"))))}
    scores = {r["File"]: float(r["score"]) for r in csv.DictReader(io.StringIO(
        cached_text(HARMONIX_RAW + "youtube_alignment_scores.csv", os.path.join(ds.dl, "youtube_alignment_scores.csv"))))}
    mel_dir = os.path.join(ds.dl, "melspecs")
    if not os.path.exists(os.path.join(mel_dir, ".complete")):
        tgz = download(HARMONIX_MELS, os.path.join(args.root, "_downloads", "Harmonix_melspecs.tgz"), args.rate)
        log("harmonix: extracting the originals' mel spectrograms")
        os.makedirs(mel_dir, exist_ok=True)
        with tarfile.open(tgz, "r|gz") as tf:
            for m in tf:
                mm = re.match(r"^(?:\./)?melspecs/(\d{4}_[^/]+)-mel\.npy$", m.name)
                if m.isfile() and mm:
                    with open(os.path.join(mel_dir, mm.group(1) + ".npy"), "wb") as f:
                        shutil.copyfileobj(tf.extractfile(m), f)
        open(os.path.join(mel_dir, ".complete"), "w").close()
        if not args.keep_downloads:
            os.remove(tgz)

    cands = [r for r in meta if urls.get(r["File"]) and scores.get(r["File"], 0) >= HARMONIX_MIN_SCORE
             and os.path.exists(os.path.join(mel_dir, r["File"] + ".npy"))]
    n_total = args.harmonix_n
    quota = {g: max(1, round(s * n_total)) for g, s in HARMONIX_SHARES.items()}
    genre_of = {r["File"]: (r["Genre"] if r["Genre"] in HARMONIX_SHARES else "other") for r in meta}
    by_genre = collections.defaultdict(list)
    for r in cands:
        by_genre[genre_of[r["File"]]].append(r)
    bpm = lambda r: float(r["BPM"] or 0)
    odd = _spread([r for r in cands if r["Time Signature"] != "4|4"], bpm)
    queue = {g: _spread(rs, bpm) for g, rs in by_genre.items()}
    stale = [i for i, e in ds.index.items() if "mp3_delay_s" not in e.get("align", {})]
    for i in stale:                                          # cut before the MP3-delay correction: redo
        del ds.index[i]
    if stale:
        log(f"harmonix: {len(stale)} items cut before the MP3-delay correction will be redone")
    count = collections.Counter(genre_of[i] for i in ds.index)
    is_odd = lambda r: r["Time Signature"] != "4|4"
    odd_count = sum(1 for e in ds.index.values() if e.get("time_signature", "4|4") != "4|4")
    tried = set(ds.index) | set(ds.failures)
    log(f"harmonix: {len(cands)} candidates; {len(ds.index)} fetched; target {n_total} ({quota})")

    def untried(g):
        return [r for r in queue.get(g, []) if r["File"] not in tried]

    # Rounds: download the next candidates for every unmet quota (half again as many as missing: a music video is
    # often another edit), then align them under the CPU lock (let go every few minutes for queued replays).
    # Downloads that end up spare (the quota filled first) stay in _downloads and are first in line on a later,
    # bigger run.
    while True:
        room = n_total - sum(count.values())
        # a genre whose candidates ran out hands its unmet share to the biggest shares that still have some
        for g in [g for g in quota if quota[g] > count[g] and not untried(g)]:
            short = quota[g] - count[g]
            open_g = [h for h in sorted(HARMONIX_SHARES, key=HARMONIX_SHARES.get, reverse=True) if untried(h)]
            if not open_g:
                break
            quota[g] = count[g]
            for k in range(short):
                quota[open_g[k % min(3, len(open_g))]] += 1
            log(f"harmonix: {g} has no candidates left, {short} of its share moved to {open_g[:3]}")
        need = {g: quota[g] - count[g] for g in quota if quota[g] > count[g]}
        if room <= 0 or not need:
            break
        batch = []
        for r in odd:
            if odd_count + sum(1 for b in batch if is_odd(b)) >= HARMONIX_ODD_METER:
                break
            if r["File"] not in tried and genre_of[r["File"]] in need:
                batch.append(r)
        for g, k in need.items():
            fresh = [r for r in queue.get(g, []) if r["File"] not in tried and r not in batch]
            batch += fresh[:int(math.ceil(k * 1.5))]
        if not batch:
            break
        tried |= {r["File"] for r in batch}
        got = []
        for r in batch:
            try:
                got.append((r,) + ytdlp_youtube(urls[r["File"]], os.path.join(ds.dl, "yt", r["File"]), args.rate))
            except Skip as ex:
                ds.fail(r["File"], ex)
        ds.save()
        log(f"harmonix: {len(got)} of {len(batch)} downloaded; aligning (waiting for the CPU lock)")
        for r, path, info in locked_slices(sorted(got, key=lambda b: not is_odd(b[0]))):
            g = genre_of[r["File"]]
            if count[g] >= quota[g] or sum(count.values()) >= n_total:
                continue
            try:
                harmonix_process(ds, r, path, info, scores[r["File"]], mel_dir)
                count[g] += 1
                odd_count += is_odd(r)
            except Skip as ex:
                ds.fail(r["File"], ex)
            ds.save()
    # cuts left from items that are no longer in the index (redone or dropped since)
    keep = {os.path.basename(e[k]) for e in ds.index.values() for k in ("audio", "beats")}
    for d in (ds.audio_dir, ds.beats_dir):
        for f in os.listdir(d):
            if f not in keep:
                os.remove(os.path.join(d, f))
    log(f"harmonix: {len(ds.index)} fetched ({dict(collections.Counter(genre_of[i] for i in ds.index))})")
    ds.save()
    return ds


def harmonix_process(ds, r, path, info, hx_score, mel_dir):
    """Align one downloaded video onto its original, cut it, check the beats, write it (caller holds the CPU lock)."""
    iid = r["File"]
    ref = np.load(os.path.join(mel_dir, iid + ".npy"), allow_pickle=False)
    ref_db = to_db(ref.astype(np.float32))
    orig_s = float(r["Duration"]) if r.get("Duration") else ref.shape[1] * HX_HOP / HX_SR
    x, _ = decode(path, OUT_FS, 2)
    al = align_to_original(soxr.resample(x.mean(axis=1), OUT_FS, HX_SR), ref_db)
    if al["r"] < ALIGN_MIN_R or al["windows_ok"] < ALIGN_MIN_WINDOWS_OK or abs(al["drift_ms"] or 1e9) > ALIGN_MAX_DRIFT_MS:
        raise Skip(f"YouTube audio does not match the original: {al}")
    al["mp3_delay_s"] = round(HARMONIX_MP3_DELAY_S, 6)
    cut_s = al["offset_s"] + HARMONIX_MP3_DELAY_S            # original (annotation) t = 0 in the YouTube audio
    y = np.clip(take(x, int(round(cut_s * OUT_FS)), int(round(orig_s * OUT_FS))), -1.0, 1.0)
    del x
    # where the video is shorter than the original the cut is padded with silence: no beats there
    valid = (max(0.0, -cut_s) + 0.05, min(orig_s, al["yt_s"] - cut_s) - 0.05)
    rows, n_out = [], 0
    for line in cached_text(HARMONIX_RAW + f"beats_and_downbeats/{iid}.txt",
                            os.path.join(ds.dl, "beats", iid + ".txt")).splitlines():
        parts = line.split()
        if parts:
            t = float(parts[0])
            if valid[0] <= t <= valid[1]:
                rows.append((t, int(float(parts[1])) if len(parts) > 1 else 0))
            else:
                n_out += 1
    oc = onset_check(y, OUT_FS, rows)
    if not onset_ok(oc, need_segments=False):
        raise Skip(f"beats do not sit on the aligned audio's onsets ({oc}); {al}")
    a = ds.audio_path(iid, "flac")
    sf.write(a + ".tmp", y, OUT_FS, format="FLAC", subtype="PCM_16")
    os.replace(a + ".tmp", a)
    pad_s = max(0.0, -cut_s) + max(0.0, cut_s + orig_s - al["yt_s"])
    notes = (f"full track, {r['Artist']} - {r['Title']} ({r['Release']}); audio from YouTube {info.get('id')} "
             f"cut at {cut_s:+.3f} s onto the annotated original's timeline (mel r {al['r']}, drift "
             f"{al['drift_ms']} ms, MP3 delay removed); beats and downbeats by Harmonix (Nieto et al. 2019)")
    if pad_s > 0.05:
        notes += (f"; {pad_s:.2f} s of silence padded where the video is shorter than the original, "
                  f"{n_out} beats there left out")
    e = ds.add(iid, a, rows, r["Genre"] or "unknown", True, notes, kind="full",
               tags=["full", "pop"], artist=r["Artist"], title=r["Title"], time_signature=r["Time Signature"],
               youtube=info.get("id"), youtube_title=info.get("title"), harmonix_yt_score=round(hx_score, 4),
               align=al, padded_s=round(pad_s, 3))
    e.update(onset_fields(oc))
    log(f"  harmonix/{iid}: ok ({r['Genre']}, {r['BPM']} bpm, offset {al['offset_s']:+.3f} s, r {al['r']}, "
        f"windows {al['windows_ok']}, drift {al['drift_ms']} ms, onsets {oc})")


def fetch_tapcorrect(args):
    ds = Dataset("tapcorrect", args)
    z = github_zip(*TAPCORRECT, ds.dl, args.rate)
    items = {}
    for n in z.namelist():
        m = re.match(r"^[^/]+/dataset/csv/(\d{3}_youtube_[^/]+)/00-meta\.csv$", n)
        if m:
            meta = {}
            for row in csv.reader(io.StringIO(z.read(n).decode())):
                if len(row) >= 2:
                    meta[row[0].strip()] = ",".join(row[1:]).strip()
            taps = z.read(n.replace("00-meta.csv", "03-fully_corrected_taps.csv")).decode()
            rows = []
            for row in csv.reader(io.StringIO(taps)):
                if row and row[0].strip():
                    rows.append((float(row[0]), int(float(row[1].strip().strip('"'))) if len(row) > 1 else 0))
            items[m.group(1)] = (meta, rows)
    todo = [i for i in sorted(items)[:args.tapcorrect_n] if not ds.done(i) and i not in ds.failures]
    log(f"tapcorrect: {len(items)} items, {len(ds.index)} fetched, {len(todo)} to do")
    got = []
    for iid in todo:                                         # downloads first, without the CPU lock
        try:
            got.append((iid,) + ytdlp_youtube(items[iid][0]["youtube_url"], os.path.join(ds.dl, "yt", iid), args.rate))
        except Skip as ex:
            ds.fail(iid, ex)
            ds.save()
    if got:
        log(f"tapcorrect: {len(got)} downloaded; checking (waiting for the CPU lock)")
        with cpu_lock():
            for iid, path, info in got:
                try:
                    tapcorrect_process(ds, iid, items[iid][0], items[iid][1], path, info)
                except Skip as ex:
                    ds.fail(iid, ex)
                ds.save()
    ds.save()
    return ds


def tapcorrect_process(ds, iid, meta, rows, path, info):
    """Check one downloaded video against its annotation and store it (caller holds the CPU lock)."""
    want = float(meta.get("track_duration") or 0)
    opus = path.endswith((".webm", ".opus")) and (info.get("acodec") or "opus").startswith("opus")
    x, fs = decode(path, None if opus else OUT_FS, 2)
    fs = fs or OUT_FS                                        # Opus decodes at 48 kHz
    got = len(x) / fs
    if want and abs(got - want) > 1.0:
        raise Skip(f"the video's audio is {got:.2f} s, the annotated recording {want:.2f} s: another version")
    oc = onset_check(x, fs, rows)
    if not onset_ok(oc, need_segments=True):
        raise Skip(f"beats do not sit on the audio's onsets ({oc})")
    if opus:
        a = ds.audio_path(iid, "opus")                       # remux only: the exact samples that were annotated
        p = subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-y", "-i", path, "-vn", "-c:a", "copy",
                            "-f", "ogg", a + ".tmp"], capture_output=True, text=True)
        if p.returncode != 0:
            raise Skip(f"remux failed: {p.stderr[-200:]}")
        os.replace(a + ".tmp", a)
    else:
        a = ds.audio_path(iid, "flac")
        sf.write(a + ".tmp", np.clip(x, -1, 1), fs, format="FLAC", subtype="PCM_16")
        os.replace(a + ".tmp", a)
    artist, title = meta.get("song_artist", ""), meta.get("song_title", "")
    e = ds.add(iid, a, rows, "pop/various", True,
               f"full track, {artist} - {title}; beats and downbeats tapped and corrected on this YouTube audio "
               f"(Driedger et al. 2019, Chordify TapCorrect)", kind="full", tags=["full", "pop"],
               artist=artist, title=title, youtube=info.get("id"), youtube_title=info.get("title"))
    e.update(onset_fields(oc))
    log(f"  tapcorrect/{iid}: ok ({artist} - {title}, onsets {oc})")


def fetch_asap(args):
    ds = Dataset("asap", args)
    meta = list(csv.DictReader(io.StringIO(cached_text(ASAP_RAW + "metadata.csv", os.path.join(ds.dl, "metadata.csv")))))
    ann = json.loads(cached_text(ASAP_RAW + "asap_annotations.json", os.path.join(ds.dl, "asap_annotations.json")))
    pieces = collections.defaultdict(list)
    for r in meta:
        a = ann.get(r["midi_performance"])
        if not r["maestro_audio_performance"] or not a or len(a.get("performance_beats", [])) < 16:
            continue
        span = a["performance_beats"][-1] - a["performance_beats"][0]
        if 60 <= span <= 420:
            pieces[(r["composer"], r["title"])].append((r, span))
    by_comp = collections.defaultdict(list)
    for (comp, title), perfs in sorted(pieces.items()):
        by_comp[comp].append(sorted(perfs, key=lambda p: p[0]["midi_performance"])[0])
    queues = {c: _spread(v, key=lambda p: p[0]["title"]) for c, v in by_comp.items()}
    chosen, per = [], collections.Counter()
    while len(chosen) < args.asap_n and any(queues.values()):     # round-robin over composers, at most 3 each
        for c in sorted(queues):
            if queues[c] and len(chosen) < args.asap_n and per[c] < 3:
                chosen.append(queues[c].pop(0))
                per[c] += 1
            elif per[c] >= 3:
                queues[c] = []
    iid_of = lambda r: re.sub(r"[^A-Za-z0-9_.-]+", "_", r["midi_performance"][:-4].replace("/", "_"))
    todo = [r for r, _ in chosen if not ds.done(iid_of(r)) and iid_of(r) not in ds.failures]
    log(f"asap: {sum(len(v) for v in pieces.values())} candidate performances, {len(chosen)} chosen, "
        f"{len(todo)} to do")
    got = []
    for r in todo:                                           # downloads first, without the CPU lock
        rel = r["maestro_audio_performance"].replace("{maestro}/", "")
        try:
            got.append((r, download(MAESTRO_HF + rel, os.path.join(ds.dl, "tmp", iid_of(r) + ".wav"), args.rate)))
        except Skip as ex:
            ds.fail(iid_of(r), ex)
    if got:
        log(f"asap: {len(got)} downloaded; cutting (waiting for the CPU lock)")
        with cpu_lock():
            for r, tmp in got:
                try:
                    asap_process(ds, iid_of(r), r, ann[r["midi_performance"]], tmp)
                except (Skip, RuntimeError, ValueError) as ex:
                    ds.fail(iid_of(r), ex)
                finally:
                    os.remove(tmp)
                ds.save()
    ds.save()
    return ds


def asap_process(ds, iid, r, a, tmp):
    """Cut one MAESTRO recording to ASAP's span, write it and its beats (caller holds the CPU lock)."""
    x, fs = sf.read(tmp, dtype="float32", always_2d=True)
    s0 = float(r["start"]) if r.get("start") else 0.0
    s1 = float(r["end"]) if r.get("end") else len(x) / fs
    x = x[int(round(s0 * fs)):int(round(s1 * fs))]
    out = ds.audio_path(iid, "flac")
    sf.write(out + ".tmp", x, fs, format="FLAC", subtype="PCM_16")
    os.replace(out + ".tmp", out)
    beats = sorted(a["performance_beats"])
    downs = np.array(sorted(a.get("performance_downbeats", [])))
    types = a.get("performance_beats_type", {})
    is_db = [bool(len(downs)) and float(np.min(np.abs(downs - t))) < 0.002 for t in beats]
    rows, pos = [], 0
    first = is_db.index(True) if True in is_db else None
    nb = None
    if first is not None:
        nxt = is_db.index(True, first + 1) if True in is_db[first + 1:] else None
        nb = (nxt - first) if nxt else None
    for k, t in enumerate(beats):
        if is_db[k]:
            pos = 1
        elif first is not None and k > first:
            pos += 1
        elif first is not None and nb and first - k < nb:   # a pickup: count back from the first downbeat
            pos = nb - (first - k) + 1
        else:
            pos = 0
        rows.append((t, pos))
    n_rubato = sum(1 for v in types.values() if v == "bR")
    ts = sorted(a.get("perf_time_signatures", {}).items(), key=lambda kv: float(kv[0]))
    rel = r["maestro_audio_performance"].replace("{maestro}/", "")
    e = ds.add(iid, out, rows, "classical piano", True,
               f"{r['composer']}: {r['title'].replace('_', ' ')}, solo piano competition performance (MAESTRO, "
               f"Disklavier), rubato; beats and downbeats from the score alignment (ASAP, Foscarin et al. 2020); "
               f"{n_rubato} beats marked bR (position not fixed by the score)",
               kind="full", tags=["full", "rubato", "piano", "no-false-lock"], composer=r["composer"],
               title=r["title"], maestro=rel, time_signatures=[v[0] for _, v in ts][:8], rubato_beats=n_rubato)
    if not ds.args.no_onset_check:
        e.update(onset_fields(onset_check(x, fs, rows)))
    log(f"  asap/{iid}: ok ({len(rows)} beats, {e['duration_s']:.0f} s, onset lag {e.get('onset_lag_ms')} ms)")


FETCHERS = collections.OrderedDict([
    ("ballroom", (fetch_ballroom, "698 x 30 s ballroom-dance excerpts, beats + downbeats (MTG audio, CPJKU annotations)")),
    ("gtzan", (fetch_gtzan, "1000 x 30 s, 10 genres, beats + downbeats (HF marsyas mirror, Marchand & Peeters)")),
    ("smc", (fetch_smc, "217 x 40 s hard expressive excerpts, beats only (needs its host back)")),
    ("harmonix", (fetch_harmonix, "stratified subset of 912 full pop tracks, YouTube audio aligned to the originals")),
    ("tapcorrect", (fetch_tapcorrect, "101 full tracks annotated on their YouTube audio (Chordify TapCorrect)")),
    ("asap", (fetch_asap, "rubato solo piano, ASAP annotations on MAESTRO audio (no-false-lock set)")),
])


# ---------------------------------------------------------------- index, summary, --list

def du(path):
    total = 0
    for dp, _, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


def verify_dataset(root, name):
    """Is every indexed item usable? Reads each audio file's header and its last second (a truncated file fails
    there) and its beats file: strictly increasing, as many as the index says, inside the audio, a median tempo
    within 30-320 bpm, and a length that fits its kind. Writes ROOT/<name>/verify.json
    {verified_at, checked, ok, problems: {id: [reason, ...]}} and returns it. Light (headers and one second each)."""
    d = os.path.join(root, name)
    if not os.path.isdir(d):
        return None
    idx = read_json(os.path.join(d, "index.json"), {})
    problems = {}
    for iid, e in sorted(idx.items()):
        bad, dur = [], float(e.get("duration_s") or 0)
        path = os.path.join(d, e["audio"])
        try:
            info = sf.info(path)
            dur = info.frames / info.samplerate
            if abs(dur - e["duration_s"]) > 0.05:
                bad.append(f"audio is {dur:.3f} s, the index says {e['duration_s']} s")
            n = min(info.frames, info.samplerate)
            tail = sf.read(path, start=info.frames - n, dtype="float32")[0]
            if len(tail) != n:
                bad.append(f"truncated: the last second gave {len(tail)} of {n} frames")
            elif not np.isfinite(tail).all():
                bad.append("non-finite samples at the end")
        except Exception as ex:
            bad.append(f"audio unreadable ({ex})"[:200])
        try:
            t = np.array([b for b, _ in read_beats(os.path.join(d, e["beats"]))], float)
            if len(t) != e.get("n_beats", len(t)):
                bad.append(f"{len(t)} beats, the index says {e['n_beats']}")
            if len(t) < 8:
                bad.append(f"only {len(t)} beats")
            else:
                if (np.diff(t) <= 0).any():
                    bad.append(f"{int((np.diff(t) <= 0).sum())} beats not after the one before")
                if t[0] < 0 or t[-1] > dur + 0.05:
                    bad.append(f"beats outside the audio ({t[0]:.2f} .. {t[-1]:.2f} s of {dur:.2f} s)")
                bpm = 60.0 / float(np.median(np.diff(t)))
                if not 30 <= bpm <= 320:
                    bad.append(f"median tempo {bpm:.0f} bpm")
        except Exception as ex:
            bad.append(f"beats unreadable ({ex})"[:200])
        lo, hi = (5, 60) if e.get("kind") == "excerpt" else (30, 900)
        if not lo <= dur <= hi:
            bad.append(f"{dur:.1f} s is out of range for a {e.get('kind')} item")
        if bad:
            problems[iid] = bad
    out = {"verified_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "checked": len(idx),
           "ok": len(idx) - len(problems), "problems": problems}
    write_json(os.path.join(d, "verify.json"), out)
    return out


def write_combined(root):
    merged, summary = {}, {"written_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                           "datasets": {}}
    for name in FETCHERS:
        d = os.path.join(root, name)
        idx = read_json(os.path.join(d, "index.json"), {})
        fails = read_json(os.path.join(d, "failures.json"), {})
        for iid, e in idx.items():
            m = dict(e)
            m["id"] = f"{name}/{iid}"
            m["audio"], m["beats"] = f"{name}/{e['audio']}", f"{name}/{e['beats']}"
            merged[m["id"]] = m
        lags = [e["onset_lag_ms"] for e in idx.values() if e.get("onset_lag_ms") is not None]
        sal = [e["onset_salience"] for e in idx.values() if e.get("onset_salience") is not None]
        fail_kinds = collections.Counter(re.sub(r"[:(].*", "", str(v)).strip()[:60] for v in fails.values())
        summary["datasets"][name] = {
            "items": len(idx), "hours": round(sum(e["duration_s"] for e in idx.values()) / 3600, 2),
            "median_duration_s": round(statistics.median([e["duration_s"] for e in idx.values()]), 1) if idx else None,
            "genres": dict(collections.Counter(e["genre"] for e in idx.values()).most_common()),
            "with_downbeats": sum(1 for e in idx.values() if e["has_downbeats"]),
            "onset_lag_ms_median": round(statistics.median(lags), 1) if lags else None,
            "onset_salience_median": round(statistics.median(sal), 2) if sal else None,
            "failures": len(fails), "failure_kinds": dict(fail_kinds.most_common()),
            "disk_mb": round(du(d) / 1e6, 1),
        }
        ver = read_json(os.path.join(d, "verify.json"), None)
        if ver is not None:
            ip = os.path.join(d, "index.json")
            fresh = not os.path.exists(ip) or os.path.getmtime(os.path.join(d, "verify.json")) >= os.path.getmtime(ip)
            summary["datasets"][name]["verify"] = {"at": ver["verified_at"], "checked": ver["checked"], "ok": ver["ok"],
                                                   "problems": len(ver["problems"]), "current": fresh}
    summary["downloads_mb"] = round(du(os.path.join(root, "_downloads")) / 1e6, 1)
    summary["total_mb"] = round(du(root) / 1e6, 1)
    write_json(os.path.join(root, "index.json"), merged)
    write_json(os.path.join(root, "summary.json"), summary)
    return summary


def print_summary(summary):
    print(f"{'dataset':<11} {'items':>6} {'hours':>6} {'med s':>6} {'downb':>6} {'lag ms':>7} {'sal':>5} "
          f"{'fails':>6} {'MB':>8}  genres")
    for name, s in summary["datasets"].items():
        g = ", ".join(f"{k} {v}" for k, v in list(s["genres"].items())[:8])
        print(f"{name:<11} {s['items']:>6} {s['hours']:>6} {s['median_duration_s'] or '-':>6} "
              f"{s['with_downbeats']:>6} {s['onset_lag_ms_median'] if s['onset_lag_ms_median'] is not None else '-':>7} "
              f"{s['onset_salience_median'] or '-':>5} {s['failures']:>6} {s['disk_mb']:>8}  {g}")
    for name, s in summary["datasets"].items():
        v = s.get("verify")
        if v and (v["problems"] or not v["current"]):
            print(f"{name}: verify {v['ok']}/{v['checked']} ok at {v['at']}"
                  f"{'' if v['current'] else ' (index changed since: run --verify)'}; see {name}/verify.json")
    print(f"downloads {summary['downloads_mb']} MB, total {summary['total_mb']} MB")


def main():
    global USE_CPU_LOCK
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list the datasets and what is fetched, then exit")
    ap.add_argument("--verify", action="store_true",
                    help="check every fetched item (audio readable to its end, beats inside it), then exit")
    ap.add_argument("--only", default="", help="comma-separated dataset names (default: all)")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--harmonix-n", type=int, default=90, help="size of the Harmonix subset (default 90)")
    ap.add_argument("--tapcorrect-n", type=int, default=101, help="first N TapCorrect items (default all 101)")
    ap.add_argument("--asap-n", type=int, default=24, help="ASAP performances, one per piece (default 24)")
    ap.add_argument("--rate", default="25M", help="download rate cap for curl and yt-dlp (default 25M bytes/s)")
    ap.add_argument("--keep-downloads", action="store_true", help="keep the big archives after extraction")
    ap.add_argument("--retry-failed", action="store_true", help="retry items listed in failures.json")
    ap.add_argument("--no-onset-check", action="store_true", help="skip the onset check where it only reports")
    ap.add_argument("--no-cpu-lock", action="store_true", help="when already run under flock(1) on the CPU lock")
    args = ap.parse_args()
    USE_CPU_LOCK = not args.no_cpu_lock
    os.makedirs(args.root, exist_ok=True)
    if args.list:
        for name, (_, desc) in FETCHERS.items():
            print(f"{name:<11} {desc}")
        print()
        print_summary(write_combined(args.root))
        return 0
    try:
        os.nice(19 - os.nice(0))
    except OSError:
        pass
    names = [n.strip() for n in args.only.split(",") if n.strip()] or list(FETCHERS)
    bad = [n for n in names if n not in FETCHERS]
    if bad:
        ap.error(f"unknown dataset(s) {bad}; known: {', '.join(FETCHERS)}")
    if args.verify:
        for name in names:
            v = verify_dataset(args.root, name)
            if v is not None:
                log(f"{name}: {v['ok']} of {v['checked']} ok")
                for iid, why in list(v["problems"].items())[:20]:
                    log(f"  {name}/{iid}: {'; '.join(why)}")
        print_summary(write_combined(args.root))
        return 0
    for name in names:
        log(f"== {name}")
        try:
            ds = FETCHERS[name][0](args)
            if ds is not None:
                ds.check_pending()
                v = verify_dataset(args.root, name)
                if v and v["problems"]:
                    log(f"{name}: verify: {len(v['problems'])} of {v['checked']} items have problems "
                        f"({name}/verify.json)")
        except Skip as ex:
            log(f"{name}: failed: {ex}")
        except Exception as ex:                              # one set's surprise must not stop the others
            import traceback
            traceback.print_exc()
            log(f"{name}: crashed: {ex!r}")
    summary = write_combined(args.root)
    print()
    print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
