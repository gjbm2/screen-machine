"""A karaoke song's moments: the places where the music itself does something a lighting designer would hit.

Owner, 27 Nov: "there are circumstances in a song where e.g. there's very strong rhythmic section, strong drumming,
orchestral hit, etc. ... we'd prob. expect to see a stronger situational lighting response". The beat and the
sections are found elsewhere; these are the exceptions, each judged against THIS song's own norm (a ballad's one
big stab stands out where a disco track's every kick does not):

  stab    a hit far above the song's other onsets, across the spectrum (an orchestral hit, a band stab, a tutti
          chord), not just a kick: the top of the song's onsets, against the ones around it
  stop    the band cuts out (the backing falls far under where it was) for a short gap, then comes back on a hit:
          stop-time, the break before the last chorus. ``end`` is the return.
  drums   two bars or more where the percussion carries the music far more than it usually does in this song
          (a drum break, a stomp section, the drums alone): from ``t`` to ``end``
  end     how the song ends, from the whole mix (a song may end on the voice alone), always one:
          cold  the music stops dead on a last hit (``t``) and is gone within END_COLD_S: a blackout's place
          ring  a last hit left to ring out (``t``) until ``end``
          fade  the record fades out, from ``t`` (where it starts to fall) to ``end``
          (``style``; ``end`` is where the sound is gone)

Read from the backing stem (instrumental.m4a): the lead vocal is not the band, and in a stop it often goes on
alone. Each moment has a ``salience`` 0..1: how far it stands out, for choosing the few a song gets (the art director
spends them; show_service/director.py). numpy + scipy + ffmpeg only.

    python -m utils.song_moments <song dir> [...]     # writes moments.json beside the stems, prints them
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from typing import Any, Optional

import numpy as np

FS = 22050
N_FFT, HOP = 2048, 512                  # ~23 ms frames
BANDS_HZ = (0, 150, 800, 4000, 11025)   # low | low-mid | mid | high
VERSION = 2
# Every number below is a starting point (27 Nov 2026), chosen by reasoning and checked by ear on a few songs.
STAB_PCT = 99.0          # a stab's onset is in the song's top 1% ...
STAB_LOCAL = 2.5         # ... and this many times the median onset peak in the 8 s around it
STAB_BANDS = 3           # ... and a jump in at least this many of the four bands at once
STAB_GAP_S = 4.0         # stabs closer than this: the stronger one
STAB_LOUD_PCT = 50.0     # a stab is at least as loud as this percentile of the song (not a piano chord in a hush)
STOP_DROP_DB = 18.0      # a stop: the backing this far under its level in the 2 s before ...
STOP_MIN_BEATS, STOP_MAX_BEATS = 0.75, 4.0   # ... for this many beats at least, at most ...
STOP_HELD = 0.8          # ... out of a passage held this much of its 2 s (not a staccato groove's own gaps)
STOP_LOUD_PCT = 40.0     # ... that was at least this loud for the song (not a quiet intro's)
STOP_BACK_DB = 8.0       # ... then back within this of where it was
DRUMS_BARS = 2.0         # a drum section: at least this many bars ...
DRUMS_Z = 1.4            # ... with the percussion's share this many deviations over the song's median
EDGE_S = 3.0             # nothing in the first or last seconds (the count-in, the fade)
END_GONE_DB = 35.0       # the end: the mix this far under the song's loud level (its 80th percentile) ...
END_COLD_S = 1.0         # ... within this of the last hit (a room's reverb tail): cold; within END_RING_S: a ring
END_RING_S = 6.0
END_FADE_DB = 10.0       # a fade: the last FADE_LOOK_S before the end fall this much, steadily
END_FADE_LOOK_S = 12.0
END_FULL_DB = 4.0        # a fade starts where the song was last within this of its loud level (2 s average)
END_FADE_MAX_S = 40.0


def decode(path: pathlib.Path, fs: int = FS) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-ac", "1", "-ar", str(fs), "-f", "f32le",
                        "-"], capture_output=True, check=True, timeout=300)
    return np.frombuffer(p.stdout, np.float32).copy()


def _stft(y: np.ndarray) -> np.ndarray:
    win = np.hanning(N_FFT).astype(np.float32)
    n = 1 + max(0, (len(y) - N_FFT) // HOP)
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    return np.abs(np.fft.rfft(y[idx] * win, axis=1)).astype(np.float32)      # frames x bins


def _beat_s(beats: Optional[list]) -> float:
    if beats and len(beats) > 8:
        d = np.diff(np.asarray(beats, float))
        d = d[(d > 0.2) & (d < 2.0)]
        if len(d):
            return float(np.median(d))
    return 0.5


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def ending(mix: np.ndarray, fs: int = FS) -> Optional[dict]:
    """How the song ends (the ``end`` moment), from the whole mix; None for a mix too short to tell."""
    from scipy.ndimage import maximum_filter1d, uniform_filter1d
    S = _stft(mix)
    fps = fs / HOP
    if len(S) < int(10 * fps):
        return None
    db = 20.0 * np.log10(np.sqrt(np.mean(S ** 2, axis=1)) + 1e-6)
    db = uniform_filter1d(db, size=max(1, int(0.05 * fps)))
    loud = float(np.percentile(db, 80))
    on = np.flatnonzero(db > loud - END_GONE_DB)
    if not len(on):
        return None
    gone = int(on[-1]) + 1                                              # the last frame with sound
    L = np.log1p(100.0 * S)
    onset = np.maximum(np.diff(L, axis=0, prepend=L[:1]), 0.0).mean(axis=1)
    look = max(0, gone - int(END_RING_S * fps))
    seg = onset[look:gone]
    peaks = np.flatnonzero((seg == maximum_filter1d(seg, size=max(3, int(0.1 * fps))))
                           & (seg >= np.percentile(onset[max(0, gone - int(60 * fps)):gone], 95)))
    hit = look + int(peaks[-1]) if len(peaks) else None
    t_gone = gone / fps
    w0 = max(0, gone - int(END_FADE_LOOK_S * fps))
    early = float(np.median(db[w0:w0 + int(2 * fps)])) if gone - w0 > int(4 * fps) else None
    late = float(np.median(db[max(w0, gone - int(1.5 * fps)):gone]))
    hit_db = float(db[hit:hit + int(0.15 * fps)].max()) if hit is not None else -120.0
    if hit is not None and (gone - hit) / fps <= END_COLD_S and hit_db >= loud - END_FADE_DB:
        return {"t": round(hit / fps, 3), "end": round(t_gone, 3), "kind": "end", "style": "cold", "salience": 1.0,
                "why": f"the last hit, then gone in {(gone - hit) / fps:.2f} s"}
    if early is not None and early - late >= END_FADE_DB and (hit is None or hit_db < loud - END_FADE_DB):
        db2 = uniform_filter1d(db, size=max(1, int(2.0 * fps)))
        a0 = max(0, gone - int(END_FADE_MAX_S * fps))
        full = np.flatnonzero(db2[a0:gone - int(1.0 * fps)] >= loud - END_FULL_DB)
        t0 = (a0 + int(full[-1])) / fps if len(full) else w0 / fps
        return {"t": round(t0, 3), "end": round(t_gone, 3), "kind": "end", "style": "fade", "salience": 1.0,
                "why": f"a fade of {early - late:.0f} dB over {t_gone - t0:.1f} s"}
    if hit is not None:
        return {"t": round(hit / fps, 3), "end": round(t_gone, 3), "kind": "end", "style": "ring", "salience": 1.0,
                "why": f"the last hit rings {(gone - hit) / fps:.1f} s"}
    return {"t": round(max(0.0, t_gone - 2.0), 3), "end": round(t_gone, 3), "kind": "end", "style": "fade",
            "salience": 1.0, "why": "no last hit: it falls away"}


def find(y: np.ndarray, beats: Optional[list] = None, fs: int = FS, mix: Optional[np.ndarray] = None) -> dict[str, Any]:
    """The backing's moments: {"version", "beat_s", "moments": [{t, kind, salience, end?, why}]}, by time."""
    from scipy.ndimage import maximum_filter1d, median_filter, uniform_filter1d
    S = _stft(y)
    fps = fs / HOP
    t_of = lambda i: float(i) / fps                                           # noqa: E731
    beat = _beat_s(beats)
    dur = len(y) / fs
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / fs)
    L = np.log1p(100.0 * S)
    flux_b = []
    for lo, hi in zip(BANDS_HZ[:-1], BANDS_HZ[1:]):
        sel = (freqs >= lo) & (freqs < hi)
        d = np.diff(L[:, sel], axis=0, prepend=L[:1, sel])
        flux_b.append(np.maximum(d, 0.0).mean(axis=1))
    F = np.stack(flux_b, axis=1)                                              # frames x 4
    onset = F.sum(axis=1)
    rms_db = 20.0 * np.log10(np.sqrt(np.mean(S ** 2, axis=1)) + 1e-6)
    rms_db = uniform_filter1d(rms_db, size=max(1, int(0.05 * fps)))
    ok = (np.arange(len(onset)) / fps >= EDGE_S) & (np.arange(len(onset)) / fps <= dur - EDGE_S)
    song_loud = float(np.percentile(rms_db[ok], STAB_LOUD_PCT)) if ok.any() else -120.0
    stop_loud = float(np.percentile(rms_db[ok], STOP_LOUD_PCT)) if ok.any() else -120.0
    out: list[dict] = []

    # stabs: the song's top onsets that stand out locally and across the bands
    peaks = (onset == maximum_filter1d(onset, size=max(3, int(0.1 * fps)))) & ok
    pk_idx = np.flatnonzero(peaks)
    if len(pk_idx) > 20:
        thr = np.percentile(onset[pk_idx], STAB_PCT)
        band_thr = np.percentile(F[pk_idx], 90, axis=0)
        win = int(4.0 * fps)
        stabs = []
        for i in pk_idx[onset[pk_idx] >= thr]:
            near = pk_idx[(pk_idx >= i - win) & (pk_idx <= i + win)]
            local = float(np.median(onset[near])) if len(near) else float(onset[i])
            ratio = float(onset[i]) / max(local, 1e-9)
            bands = int(np.sum(F[i] >= band_thr))
            lvl = float(rms_db[i:i + int(0.15 * fps)].max()) if i < len(rms_db) else -120.0
            if ratio >= STAB_LOCAL and bands >= STAB_BANDS and lvl >= song_loud:
                stabs.append((i, ratio, bands))
        stabs.sort(key=lambda s: -s[1])
        kept: list[tuple[int, float, int]] = []
        for s in stabs:
            if all(abs(s[0] - k[0]) / fps >= STAB_GAP_S for k in kept):
                kept.append(s)
        for i, ratio, bands in kept:
            out.append({"t": round(t_of(i), 3), "kind": "stab",
                        "salience": round(0.4 + 0.6 * min(1.0, float(np.log(ratio / STAB_LOCAL) / np.log(8.0))), 3),
                        "why": f"{ratio:.1f}x the onsets around it, in {bands} of 4 bands"})

    # stops: the backing falls far under its level just before, briefly, and comes back
    pre = int(2.0 * fps)
    ref_db = np.full_like(rms_db, -120.0)
    if len(rms_db) > pre:
        med = median_filter(rms_db, size=pre, mode="nearest")
        ref_db[pre // 2:] = med[:len(med) - pre // 2]                         # the median of the 2 s before
    low = (rms_db < ref_db - STOP_DROP_DB) & ok
    for a, b in _runs(low):
        gap = (b - a) / fps
        if gap < STOP_MIN_BEATS * beat or gap > STOP_MAX_BEATS * beat or ref_db[a] < stop_loud:
            continue
        before = rms_db[max(0, a - pre):a]
        if len(before) == 0 or float(np.mean(before > ref_db[a] - 12.0)) < STOP_HELD:
            continue
        back = b + int(0.25 * fps)
        if back >= len(rms_db) or rms_db[b:back].max() < ref_db[a] - STOP_BACK_DB:
            continue
        r = int(b + np.argmax(onset[b:back]))
        depth = float(ref_db[a] - rms_db[a:b].mean())
        out.append({"t": round(t_of(a), 3), "end": round(t_of(r), 3), "kind": "stop",
                    "salience": round(min(1.0, 0.5 + (depth - STOP_DROP_DB) / 40.0 + 0.1 * min(gap / beat, 2.0)), 3),
                    "why": f"the band {depth:.0f} dB down for {gap / beat:.1f} beats, back on a hit"})

    # drum sections: the percussive share of the backing far over the song's own, for bars on end
    P = median_filter(S, size=(1, 17))                                        # across frequency: the hits
    H = median_filter(S, size=(17, 1))                                        # across time: the held notes
    share = (P ** 2).sum(axis=1) / ((P ** 2).sum(axis=1) + (H ** 2).sum(axis=1) + 1e-12)
    share = uniform_filter1d(share, size=max(1, int(4 * beat * fps)))         # over a bar
    loud = rms_db > np.percentile(rms_db[ok], 30) if ok.any() else np.zeros_like(ok)
    base, dev = float(np.median(share[ok])), float(np.std(share[ok])) or 1e-6
    hi = (share > base + DRUMS_Z * dev) & ok & loud
    for a, b in _runs(hi):
        if (b - a) / fps < DRUMS_BARS * 4 * beat:
            continue
        z = float((share[a:b].mean() - base) / dev)
        out.append({"t": round(t_of(a), 3), "end": round(t_of(b), 3), "kind": "drums",
                    "salience": round(min(1.0, 0.4 + (z - DRUMS_Z) / 3.0), 3),
                    "why": f"the percussion {z:.1f} deviations over this song's usual, {(b - a) / fps / (4 * beat):.0f} bars"})
    end = ending(mix if mix is not None else y, fs)
    if end is not None:
        out = [m for m in out if m["t"] < end["t"] - 0.5] + [end]          # nothing of the band's after its end
    out.sort(key=lambda m: m["t"])
    return {"version": VERSION, "beat_s": round(beat, 3), "moments": out}


def for_song(song_dir: pathlib.Path, write: bool = True) -> Optional[dict]:
    inst = song_dir / "instrumental.m4a"
    if not inst.is_file():
        return None
    beats = None
    try:
        beats = json.loads((song_dir / "beats.json").read_text()).get("beats")
    except (OSError, ValueError):
        pass
    y = decode(inst)
    mix = None
    if (song_dir / "vocals.m4a").is_file():
        v = decode(song_dir / "vocals.m4a")
        n = min(len(y), len(v))
        mix = y[:n] + v[:n]
    m = find(y, beats, mix=mix)
    if write:
        tmp = song_dir / "moments.json.tmp"
        tmp.write_text(json.dumps(m, indent=1))
        tmp.replace(song_dir / "moments.json")
    return m


def main(argv: Optional[list] = None) -> int:
    for d in (argv if argv is not None else sys.argv[1:]):
        m = for_song(pathlib.Path(d))
        if m is None:
            print(f"{d}: no backing stem")
            continue
        print(f"{d}: {len(m['moments'])} moments (beat {m['beat_s']} s)")
        for x in m["moments"]:
            end = f"-{x['end']:.1f}" if "end" in x else ""
            print(f"  {x['t']:7.2f}{end:8s} {x['kind']:6s} {x['salience']:.2f}  {x['why']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
