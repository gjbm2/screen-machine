#!/usr/bin/env python3
"""The answer key for one karaoke song, from hindsight over its whole file (never the show's own code paths).

    ~/.venvs/sm-show/bin/python tools/room/ad_eval/reference.py <song id> [...] [--force]

Writes ~/.cache/sm-ad-eval/ref/<id>.v4.json:
  beats, downbeats   madmom's offline RNN downbeat model + DBN (3 or 4 beats a bar) over the whole mix. A different
                     model from the show's (Beat This!, causal madmom), so a disagreement is real evidence.
  energy             the mix's level every 0.1 s (RMS over 0.4 s, in dB, the song's p05..p98 -> 0..1).
  sections           for a song in keys.yaml (the eval's set): its sections as annotated by reading its timed lyrics
                     against its known form (kind, size within the song, how the music arrives at each, the peak),
                     each start moved to the nearest downbeat; ``sections_from`` says "key". The first key cut songs
                     by audio novelty alone and was wrong about most of them (Dancing Queen cut mid-chorus), which
                     made every measure that reads sections a measure of the key. For any other song ("novelty"):
                     boundaries from a self-similarity matrix of beat-synchronous timbre + chroma (a checkerboard
                     novelty over the whole track, peaks snapped to downbeats); letters by clustering the sections'
                     sound; kinds from the lyrics (lines sung again elsewhere: chorus; other lines: verse; none: intro,
                     instrumental or outro by place).
  sim                how alike each pair of sections sounds, without the letters' threshold: ``audio`` (the mean
                     similarity of their beats at the best alignment of the shorter over the longer: a part that
                     comes back keeps its order, a stripe in the self-similarity matrix), ``lyrics`` (the share of the
                     shorter one's lines that the other sings too; null unless both are sung) and ``agree`` (the rank
                     correlation of the two over the sung pairs: how far this key's idea of the form can be trusted).
  character          what kind of recording it is, from the sound alone (never the catalogue's genre, which the
                     show's own reading uses): bpm, punch (how hard its onsets hit: the mean rise of the log-mel
                     spectrum), range_db (its loud parts over its quiet ones), bright (the octave of its spectral
                     centroid) and low (the share of its power under 150 Hz). A ballad and a disco track differ in
                     these whatever their loudness; form.character asks whether their shows differ too.
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
from typing import Optional

os.environ.setdefault("OMP_NUM_THREADS", "1")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

import common  # noqa: E402

VERSION = 4            # 2: sim. 3: character. 4: annotated sections (keys.yaml). (An older key is completed: its
                       # beats kept.)
FS = 44100
HOP_E = 0.1            # energy hop (s)
WIN_E = 0.4            # energy window (s): momentary, like EBU's 400 ms
KERNEL_BEATS = 16      # checkerboard half-width (beats): 4 bars of 4
MIN_SECTION_BARS = 4
PEAK_Z = 0.35          # novelty peaks above mean + this x std
CLUSTER_SIM = 0.85     # two sections share a letter above this cosine similarity of their mean features
CLIMAX_S = 20.0


def ref_path(sid: str) -> pathlib.Path:
    """This version's key (version 1's is <id>.json: a run of older code beside this one keeps its own)."""
    return common.CACHE / "ref" / f"{sid}.v{VERSION}.json"


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


def section_sim(B: np.ndarray, beats: list[float], bounds: list[float], ends: list[float]) -> list[list[float]]:
    """How alike each pair of sections sounds, -1..1 (1 with itself): the mean cosine similarity of their beats (the
    lag-embedded timbre + chroma of ``novelty``) along the best diagonal of one over the other."""
    E = np.concatenate([B[:-1], B[1:]], axis=1) if len(B) > 1 else B
    E = E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)
    bt = np.asarray(beats[:len(E)])
    rng = []
    for a, b in zip(bounds, ends):
        ix = np.where((bt >= a) & (bt < b))[0]
        rng.append((int(ix[0]), int(ix[-1]) + 1) if len(ix) else None)
    n = len(rng)
    out = [[0.0] * n for _ in range(n)]
    for i in range(n):
        out[i][i] = 1.0
        for j in range(i + 1, n):
            v = 0.0
            if rng[i] and rng[j]:
                (a0, a1), (b0, b1) = sorted((rng[i], rng[j]), key=lambda r: r[1] - r[0])
                S = E[a0:a1] @ E[b0:b1].T
                k = np.arange(a1 - a0)
                v = max(float(S[k, o + k].mean()) for o in range(0, (b1 - b0) - (a1 - a0) + 1))
            out[i][j] = out[j][i] = round(v, 4)
    return out


def lyric_sim(lines: list[tuple[float, str]], bounds: list[float], ends: list[float]) -> list[list]:
    """For each pair of sections that both have two lines or more: the share of the shorter one's lines that the
    other sings too (a chorus coming back: 1). None for the rest."""
    per = [[t for s, t in lines if a <= s < b] for a, b in zip(bounds, ends)]
    n = len(per)
    out: list[list] = [[None] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j and len(per[i]) >= 2 and len(per[j]) >= 2:
                a, b = sorted((per[i], per[j]), key=len)
                pool, hit = list(b), 0
                for t in a:
                    if t in pool:
                        pool.remove(t)
                        hit += 1
                out[i][j] = round(hit / len(a), 3)
    return out


def sim_of(B: np.ndarray, beats: list[float], secs: list[dict], lines: list[tuple[float, str]]) -> dict:
    bounds, ends = [float(s["start_s"]) for s in secs], [float(s["end_s"]) for s in secs]
    A, L = section_sim(B, beats, bounds, ends), lyric_sim(lines, bounds, ends)
    xa = [A[i][j] for i in range(len(secs)) for j in range(i + 1, len(secs)) if L[i][j] is not None]
    xl = [L[i][j] for i in range(len(secs)) for j in range(i + 1, len(secs)) if L[i][j] is not None]
    agree = None
    if len(xa) >= 6 and np.std(xa) > 1e-9 and np.std(xl) > 1e-9:
        ra, rl = np.argsort(np.argsort(xa)), np.argsort(np.argsort(xl))
        agree = round(float(np.corrcoef(ra, rl)[0, 1]), 3)
    return {"audio": A, "lyrics": L, "agree": agree}


def sound_character(x: np.ndarray, beats: list[float]) -> dict:
    """What kind of recording this is, from its sound: {bpm, punch, range_db, bright, low}."""
    n_fft, hop = 2048, 512
    S = stft_mag(x, n_fft, hop)
    P = S.astype(np.float64) ** 2
    tot = P.sum(axis=1) + 1e-12
    on = tot > np.percentile(tot, 20)                    # the quietest fifth (gaps, the fade) says nothing
    mel = np.log(S @ mel_fb(n_fft).T + 1e-6)
    flux = np.concatenate([[0.0], np.clip(np.diff(mel, axis=0), 0, None).mean(axis=1)])
    freqs = np.linspace(0, FS / 2, n_fft // 2 + 1)
    _, db = energy_curve(x)
    loud = db[db > -45.0]
    ibi = float(np.median(np.diff(beats))) if len(beats) > 2 else 0.0
    return {"bpm": round(60.0 / ibi, 1) if ibi > 0 else None,
            "punch": round(float(flux[on].mean()), 4),
            "range_db": round(float(np.percentile(loud, 95) - np.percentile(loud, 10)), 2) if len(loud) > 10 else None,
            "bright": round(float(np.log2(((P @ freqs) / tot)[on].mean())), 3),
            "low": round(float((P[:, freqs < 150.0].sum(axis=1) / tot)[on].mean()), 4)}


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
    sim: dict = {}
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
        sim = sim_of(B, beats, secs, lines)
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
    out = {"version": VERSION, "song": common.song_meta(sid, lib), "made_at": time.time(),
            "beats": [round(b, 4) for b in beats], "downbeats": [round(b, 4) for b in downs],
            "sections": secs, "sim": sim, "character": sound_character(x, beats), "energy": {"hop_s": HOP_E, "v": [round(float(v), 4) for v in e]},
            "climax": climax, "last_hit_s": round(last_hit, 2), "end_s": round(end_s, 2),
            "cost_s": {"beats": round(t_beats, 1), "all": round(time.time() - t0, 1)}}
    return complete(out, sid) if annotated(sid) else {**out, "sections_from": "novelty", "key_rev": ""}


KEYS_YAML = common.HERE / "keys.yaml"
KEY_SNAP_S = 1.5       # an annotated start moves to a downbeat within this


def annotated(sid: str) -> list[dict]:
    """A song's sections as annotated in keys.yaml ([] for a song not in it): [{start_s (the lyrics' own time), kind,
    size, arrive, final?, peak?}]."""
    try:
        import yaml
        rows = ((yaml.safe_load(KEYS_YAML.read_text()) or {}).get("keys") or {}).get(sid) or []
    except (OSError, ValueError):
        return []
    return [x for x in rows if isinstance(x, dict) and isinstance(x.get("start_s"), (int, float))]


def liveliness(sid: str) -> Optional[float]:
    """keys.yaml's word on what kind of song this is (0 hushed .. 4 club), or None."""
    try:
        import yaml
        v = ((yaml.safe_load(KEYS_YAML.read_text()) or {}).get("liveliness") or {}).get(sid)
    except (OSError, ValueError):
        return None
    return float(v) if isinstance(v, (int, float)) else None


def key_rev(rows: list[dict]) -> str:
    """What a reference's annotated sections were made from ("" for none): a change to a song's lines in keys.yaml
    makes its reference again."""
    import hashlib
    return hashlib.sha1(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()[:10] if rows else ""


def key_sections(rows: list[dict], off: float, downs: list[float], dur: float, e: np.ndarray) -> list[dict]:
    """Annotated sections as the reference's: starts in the audio's time (the lyrics' own + the song's offsets),
    moved to the nearest downbeat within KEY_SNAP_S; a letter per kind; the recording's loudness over each."""
    starts = []
    for i, x in enumerate(rows):
        t = 0.0 if i == 0 else float(x["start_s"]) + off
        if i and downs:
            near = min(downs, key=lambda d: abs(d - t))
            t = near if abs(near - t) <= KEY_SNAP_S else t
        starts.append(max(0.0, t))
    letters: dict[str, str] = {}
    secs = []
    for i, x in enumerate(rows):
        a, b = starts[i], (starts[i + 1] if i + 1 < len(rows) else dur)
        if b - a < 1.0:
            continue
        k = str(x.get("kind") or "verse")
        secs.append({"start_s": round(a, 3), "end_s": round(b, 3), "label": letters.setdefault(k, chr(65 + len(letters))),
                     "kind": k, "energy": round(float(e[int(a / HOP_E):max(int(a / HOP_E) + 1, int(b / HOP_E))].mean()), 3),
                     "size": float(x.get("size") or 0.5), "arrive": str(x.get("arrive") or "none"),
                     "final": bool(x.get("final")), "peak": bool(x.get("peak"))})
    return secs


def complete(r: dict, sid: str) -> dict:
    """An older key made whole: its ``sim`` and ``character``, from its own beats and sections (no beat tracking
    again)."""
    lib = common.library()
    meta = lib.get(sid) or {}
    x = common.room_mix(sid, FS)
    beats = r.get("beats") or []
    rows = annotated(sid)
    r["sections_from"] = r.get("sections_from") or "novelty"
    r["key_rev"] = key_rev(rows)
    if rows:
        off = (float(meta.get("offset_ms") or 0) + float(meta.get("user_offset_ms") or 0)) / 1000.0
        e, _db = energy_curve(x)
        r["sections"] = key_sections(rows, off, r.get("downbeats") or [], len(x) / FS, e)
        r["sections_from"] = "key"
        pk = next((s_ for s_ in r["sections"] if s_.get("peak")), None)
        if pk is not None:                               # the annotated high point (20 s of it at most)
            r["climax"] = [pk["start_s"], round(min(pk["end_s"], pk["start_s"] + CLIMAX_S), 2)]
    secs = r.get("sections") or []
    r["sim"] = {}
    if secs and len(beats) > 4 * KERNEL_BEATS:
        r["sim"] = sim_of(beat_features(x, beats), beats, secs, lyric_lines(sid, lib.get(sid) or {}))
    r["character"] = {**sound_character(x, beats), "liveliness": liveliness(sid)}
    r["version"] = VERSION
    return r


def moments(sid: str) -> Optional[list]:
    """The song's moments (utils/song_moments.py, from its backing stem and mix), cached beside the references. The
    show finds them with the same detector: the ``moments`` goal checks that the lights answer them, and the blind
    pairs check, by eye, that they are worth answering."""
    if str(common.REPO) not in sys.path:
        sys.path.insert(0, str(common.REPO))
    from utils import song_moments
    p = common.CACHE / "ref" / f"{sid}.moments.v{song_moments.VERSION}.json"
    if p.is_file():
        return json.loads(p.read_text()).get("moments")
    m = song_moments.for_song(common.song_dir(sid), write=False)
    if m is None:
        return None
    common.write_json(p, m)
    return m.get("moments")


def load(sid: str, force: bool = False) -> dict:
    return {**_load(sid, force), "moments": moments(sid)}


def _load(sid: str, force: bool = False) -> dict:
    p = ref_path(sid)
    if p.is_file() and not force:
        r = json.loads(p.read_text())
        if r.get("version") == VERSION:
            if r.get("key_rev", "") != key_rev(annotated(sid)) or \
                    (r.get("character") or {}).get("liveliness") != liveliness(sid):   # keys.yaml changed: again
                r = complete(r, sid)
                common.write_json(p, r)
            return r
    for old in (common.CACHE / "ref" / f"{sid}.v3.json", common.CACHE / "ref" / f"{sid}.v2.json",
                common.CACHE / "ref" / f"{sid}.json"):
        if old.is_file() and not force:
            r = json.loads(old.read_text())
            if r.get("version") in (1, 2, 3):
                r = complete(r, sid)
                common.write_json(p, r)
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
