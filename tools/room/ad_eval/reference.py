#!/usr/bin/env python3
"""The answer key for one karaoke song, from hindsight over its whole file (never the show's own code paths).

    ~/.venvs/sm-show/bin/python tools/room/ad_eval/reference.py <song id> [...] [--force]

Writes ~/.cache/sm-ad-eval/ref/<id>.json:
  beats, downbeats   madmom's offline RNN downbeat model + DBN (3 or 4 beats a bar) over the whole mix. A different
                     model from the show's (Beat This!, causal madmom), so a disagreement is real evidence.
  energy             the mix's level every 0.1 s (RMS over 0.4 s, in dB, the song's p05..p98 -> 0..1).
  sections           boundaries from a self-similarity matrix of beat-synchronous timbre + chroma (a checkerboard
                     novelty over the whole track, peaks snapped to downbeats); letters by clustering the sections'
                     sound; kinds from the lyrics (lines sung again elsewhere: chorus; other lines: verse; none: intro,
                     instrumental or outro by place).
  climax             the 20 s window with the highest mean energy (smoothed over 4 s).
  last_hit_s, end_s  the last strong onset, and the last moment above -45 dBFS.
Every number here is a starting point. CPU, nice 19.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

import common  # noqa: E402

VERSION = 1
FS = 44100
HOP_E = 0.1            # energy hop (s)
WIN_E = 0.4            # energy window (s): momentary, like EBU's 400 ms
KERNEL_BEATS = 16      # checkerboard half-width (beats): 4 bars of 4
MIN_SECTION_BARS = 4
PEAK_Z = 0.35          # novelty peaks above mean + this x std
CLUSTER_SIM = 0.85     # two sections share a letter above this cosine similarity of their mean features
CLIMAX_S = 20.0


def ref_path(sid: str) -> pathlib.Path:
    return common.CACHE / "ref" / f"{sid}.json"


# -- beats ----------------------------------------------------------------------------------------------------------
def beats_madmom(x: np.ndarray) -> tuple[list[float], list[float]]:
    from madmom.audio.signal import Signal
    from madmom.features.downbeats import DBNDownBeatTrackingProcessor, RNNDownBeatProcessor
    act = RNNDownBeatProcessor()(Signal(x, sample_rate=FS, num_channels=1))
    res = DBNDownBeatTrackingProcessor(beats_per_bar=[3, 4], fps=100)(act)
    beats = [float(t) for t, _ in res]
    downs = [float(t) for t, p in res if int(p) == 1]
    return beats, downs


# -- spectra --------------------------------------------------------------------------------------------------------
def stft_mag(x: np.ndarray, n_fft: int = 2048, hop: int = 512) -> np.ndarray:
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    n = 1 + (len(x) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n)[:, None]
    w = np.hanning(n_fft).astype(np.float32)
    out = np.empty((n, n_fft // 2 + 1), np.float32)
    for i in range(0, n, 2048):                     # in chunks: memory
        out[i:i + 2048] = np.abs(np.fft.rfft(x[idx[i:i + 2048]] * w, axis=1))
    return out


def mel_fb(n_fft: int, n_mels: int = 40, fmin: float = 30.0, fmax: float = 16000.0) -> np.ndarray:
    def hz2mel(f):
        return 2595.0 * np.log10(1.0 + f / 700.0)

    def mel2hz(m):
        return 700.0 * (10 ** (m / 2595.0) - 1.0)
    freqs = np.linspace(0, FS / 2, n_fft // 2 + 1)
    pts = mel2hz(np.linspace(hz2mel(fmin), hz2mel(fmax), n_mels + 2))
    fb = np.zeros((n_mels, len(freqs)), np.float32)
    for m in range(n_mels):
        lo, c, hi = pts[m], pts[m + 1], pts[m + 2]
        fb[m] = np.clip(np.minimum((freqs - lo) / (c - lo + 1e-9), (hi - freqs) / (hi - c + 1e-9)), 0, None)
    return fb


def chroma_fb(n_fft: int) -> np.ndarray:
    freqs = np.linspace(0, FS / 2, n_fft // 2 + 1)
    fb = np.zeros((12, len(freqs)), np.float32)
    ok = (freqs > 55) & (freqs < 5000)
    pc = np.mod(np.round(12 * np.log2(freqs[ok] / 440.0)), 12).astype(int)
    fb[pc, np.where(ok)[0]] = 1.0
    return fb


def beat_features(x: np.ndarray, beats: list[float]) -> np.ndarray:
    """Per beat interval: 13 MFCCs (timbre) + 12 chroma (harmony), each dimension z-scored over the song."""
    n_fft, hop = 2048, 512
    S = stft_mag(x, n_fft, hop)
    mel = np.log(S @ mel_fb(n_fft).T + 1e-6)
    from scipy.fft import dct
    mfcc = dct(mel, type=2, norm="ortho", axis=1)[:, :13]
    ch = S ** 2 @ chroma_fb(n_fft).T
    ch = ch / (ch.sum(axis=1, keepdims=True) + 1e-9)
    F = np.concatenate([mfcc, ch * 10.0], axis=1)
    ft = (np.arange(len(F)) * hop + n_fft / 2) / FS
    rows = []
    for a, b in zip(beats[:-1], beats[1:]):
        m = (ft >= a) & (ft < b)
        rows.append(F[m].mean(axis=0) if m.any() else np.zeros(F.shape[1], np.float32))
    B = np.asarray(rows, np.float32)
    B = (B - B.mean(axis=0)) / (B.std(axis=0) + 1e-6)
    return B


def novelty(B: np.ndarray, L: int = KERNEL_BEATS) -> np.ndarray:
    """Foote's checkerboard novelty on a lag-embedded (2-beat stack) cosine self-similarity matrix."""
    E = np.concatenate([B[:-1], B[1:]], axis=1) if len(B) > 1 else B
    E = E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)
    S = E @ E.T
    g = np.exp(-0.5 * (np.linspace(-2, 2, 2 * L)) ** 2)
    G = np.outer(g, g)
    sign = np.ones((2 * L, 2 * L))
    sign[:L, L:] = sign[L:, :L] = -1
    K = G * sign
    n = len(S)
    P = np.pad(S, L, mode="edge")
    nov = np.array([float((P[i:i + 2 * L, i:i + 2 * L] * K).sum()) for i in range(n)])
    nov = np.clip(nov, 0, None)
    return nov / (nov.max() + 1e-9)


# -- energy ---------------------------------------------------------------------------------------------------------
def energy_curve(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(0..1 per HOP_E, the raw dB)."""
    hop, win = int(HOP_E * FS), int(WIN_E * FS)
    n = max(1, 1 + (len(x) - win) // hop)
    c = np.concatenate([[0.0], np.cumsum(x.astype(np.float64) ** 2)])
    idx = np.arange(n) * hop
    ms = (c[np.minimum(idx + win, len(x))] - c[idx]) / win
    db = 10 * np.log10(ms + 1e-12)
    lo, hi = np.percentile(db, 5), np.percentile(db, 98)
    return np.clip((db - lo) / max(1e-6, hi - lo), 0, 1), db


def smooth(v: np.ndarray, s: float, hop: float = HOP_E) -> np.ndarray:
    k = max(1, int(round(s / hop)))
    return np.convolve(v, np.ones(k) / k, mode="same")


# -- lyrics ---------------------------------------------------------------------------------------------------------
def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (t or "").lower()).strip()


def lyric_lines(sid: str, meta: dict) -> list[tuple[float, str]]:
    p = common.song_dir(sid) / "lyrics.json"
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    off = (float(meta.get("offset_ms") or 0) + float(meta.get("user_offset_ms") or 0)) / 1000.0
    return [(float(ln["start"]) + off, _norm(ln.get("text", ""))) for ln in d.get("lines") or []
            if isinstance(ln, dict) and ln.get("start") is not None and _norm(ln.get("text", ""))]


# -- the whole key --------------------------------------------------------------------------------------------------
def analyse(sid: str) -> dict:
    lib = common.library()
    meta = lib.get(sid) or {}
    x = common.room_mix(sid, FS)
    dur = len(x) / FS
    t0 = time.time()
    beats, downs = beats_madmom(x)
    t_beats = time.time() - t0
    e, db = energy_curve(x)
    es = smooth(e, 4.0)
    # sections
    secs: list[dict] = []
    if len(beats) > 4 * KERNEL_BEATS:
        B = beat_features(x, beats)
        nov = novelty(B)
        thr = nov.mean() + PEAK_Z * nov.std()
        bar = int(round(np.median(np.diff(downs)) / np.median(np.diff(beats)))) if len(downs) > 2 else 4
        gap = MIN_SECTION_BARS * max(3, bar)
        peaks = []
        for i in range(1, len(nov) - 1):
            if nov[i] >= thr and nov[i] == nov[max(0, i - gap // 2):i + gap // 2 + 1].max():
                if not peaks or i - peaks[-1] >= gap:
                    peaks.append(i)
        bounds = sorted({0.0, *[min(downs, key=lambda d: abs(d - beats[i])) if downs else beats[i] for i in peaks]})
        first = beats[0] if beats else 0.0
        if bounds and bounds[0] < first:
            bounds[0] = 0.0
        ends = bounds[1:] + [dur]
        # letters: cluster by the mean feature of each section's beats
        bt = np.asarray(beats[:-1])
        means = []
        for a, b in zip(bounds, ends):
            m = (bt >= a) & (bt < b)
            v = B[m].mean(axis=0) if m.any() else np.zeros(B.shape[1])
            means.append(v / (np.linalg.norm(v) + 1e-9))
        letters: list[str] = []
        protos: list[np.ndarray] = []
        for v in means:
            sims = [float(v @ p) for p in protos]
            if sims and max(sims) >= CLUSTER_SIM:
                letters.append(chr(65 + int(np.argmax(sims))))
            else:
                protos.append(v)
                letters.append(chr(65 + min(25, len(protos) - 1)))
        lines = lyric_lines(sid, meta)
        texts = [t for _, t in lines]
        first_l = lines[0][0] if lines else None
        last_l = lines[-1][0] if lines else None
        for a, b, L in zip(bounds, ends, letters):
            mine = [t for s, t in lines if a <= s < b]
            others = [t for s, t in lines if not (a <= s < b)]
            if mine:
                rep = sum(1 for t in mine if t in others) / len(mine)
                kind = "chorus" if rep >= 0.5 else "verse"
            elif first_l is None or b <= first_l + 0.5:
                kind = "intro"
            elif last_l is not None and a >= last_l:
                kind = "outro"
            else:
                kind = "instrumental"
            secs.append({"start_s": round(a, 3), "end_s": round(b, 3), "label": L, "kind": kind,
                         "energy": round(float(e[int(a / HOP_E):max(int(a / HOP_E) + 1, int(b / HOP_E))].mean()), 3)})
        del texts
    # climax
    k = int(CLIMAX_S / HOP_E)
    c = np.convolve(es, np.ones(k) / k, mode="valid") if len(es) > k else es
    ci = int(np.argmax(c))
    climax = [round(ci * HOP_E, 2), round(ci * HOP_E + CLIMAX_S, 2)]
    # last hit and end
    loud = db > -45.0
    end_s = float((np.where(loud)[0][-1] + 1) * HOP_E) if loud.any() else dur
    d = np.diff(db, prepend=db[0])
    strong = (d > 6.0) & (db > np.percentile(db, 50))
    last_hit = float(np.where(strong)[0][-1] * HOP_E) if strong.any() else end_s
    return {"version": VERSION, "song": common.song_meta(sid, lib), "made_at": time.time(),
            "beats": [round(b, 4) for b in beats], "downbeats": [round(b, 4) for b in downs],
            "sections": secs, "energy": {"hop_s": HOP_E, "v": [round(float(v), 4) for v in e]},
            "climax": climax, "last_hit_s": round(last_hit, 2), "end_s": round(end_s, 2),
            "cost_s": {"beats": round(t_beats, 1), "all": round(time.time() - t0, 1)}}


def load(sid: str, force: bool = False) -> dict:
    p = ref_path(sid)
    if p.is_file() and not force:
        r = json.loads(p.read_text())
        if r.get("version") == VERSION:
            return r
    r = analyse(sid)
    common.write_json(p, r)
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="*", help="song ids (default: the song set)")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    try:
        os.nice(19)
    except OSError:
        pass
    ids = a.ids or [s["id"] for s in common.song_set()]
    for sid in ids:
        t = time.time()
        r = load(sid, a.force)
        print(f"{sid} {str(r['song'].get('title'))[:30]:30s} beats {len(r['beats'])} sections "
              f"{''.join(s['label'] for s in r['sections'])} "
              f"{' '.join(s['kind'][:3] for s in r['sections'])} climax {r['climax']} ({time.time() - t:.0f} s)",
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
