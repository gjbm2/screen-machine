#!/usr/bin/env python3
"""How faithfully a piano performance plays the written score of a piece (owner, 23 Oct: "I'm a purist").

    score(performance_midi, reference_midi) -> dict

The reference is a score-faithful MIDI with the written repeats played out (a step-sequenced or engraved
score: Colin MacDonald's ragtimemusic.com sequences, PDMX / MuseScore, Mutopia).  Its marker meta events
("A Strain", "A Strain - Repeat", "Introduction", ...) name the sections; without markers the sections are
8-bar phrases.

How it works:
  1. Both files become frame features: onset chroma, bass pitch-class and decaying sustain chroma; the
     reference in beats (1/8 of a quarter per frame), the performance in seconds (one frame = the performance's
     own time for 1/8 beat, measured in a first pass).
  2. A tempo-flexible DTW aligns them.  The path may also jump from the end of any section to the start of
     any section (a fixed penalty), so a performance that skips a written repeat, adds one, or plays the
     strains in another order still aligns, and the jumps say what it did with the structure.
  3. Notes are matched by pitch near the aligned position (a residual correction from the first matches
     tightens the window to 0.2 beat), each note at most once.

Returned fields:
  recall            share of all reference notes (written repeats included) that the performance plays
  recall_played     the same, counting only the sections it plays (note fidelity without the cuts)
  precision         share of the performance's notes (velocity >= MIN_VEL) that are reference notes
  precision_harmonic  share that are reference notes, octave doublings of them, or tones of the written chord
  extra_per_bar / missing_per_bar   unmatched performance notes per played bar / unmatched reference notes per
                    covered bar
  extra_kinds       {"octave", "chord", "foreign"} counts of the unmatched performance notes
  swing             median long/short ratio of written even subdivisions (1.0 straight, 2.0 triplet swing)
  structure         {"written", "played", "in_order", "skipped", "extra", "skipped_bars", "extra_bars"}
  verdict           "faithful" | "embellished" | "liberal", and "reasons" for anything short of faithful

Thresholds (THRESHOLDS below) were set and checked on 23 Oct 2026 against the reference against itself,
two independent score editions against each other, straight classical-style takes, Sue Keller's and Alessandro
Simonetto's arrangements and Marcus Roberts' jazz Maple Leaf; see the module's git log for the numbers.

CLI:  faithful.py PERF.mid REF.mid [--json]        one score
      faithful.py --pairs pairs.tsv [--out out.json]   many (tab-separated: perf, ref[, label])
Needs numpy and mido.  CPU: a 4-minute rag scores in about a second.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field

for _v in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):  # one core: the host plays live video
    os.environ.setdefault(_v, '1')

import mido  # noqa: E402
import numpy as np  # noqa: E402

FRAME_BEAT = 0.125        # reference frame = 1/8 quarter note
MIN_VEL = 10              # quieter performance notes are ghosts / transcription noise; not counted
DRUM_CH = 9
STEP_PEN = 0.12           # extra cost of a non-diagonal DTW step
JUMP_PEN = 6.0            # cost of jumping between sections (about 8 mismatched frames)
TOL0, TOL = 0.45, 0.2     # note-match windows in beats: first pass, after the residual correction

THRESHOLDS = {
    # faithful: plays what is written, all of it, straight
    'faithful': {'recall_played': 0.90, 'precision': 0.85, 'swing_max': 1.20, 'skipped_bars': 2,
                 'extra_bars': 2},
    # embellished: the written notes are all still there; extras are few and fit the harmony
    'embellished': {'recall_played': 0.80, 'precision': 0.62, 'precision_harmonic': 0.80, 'swing_max': 1.28,
                    'foreign_per_bar': 1.5, 'skipped_bars': 2, 'extra_bars': 4},
}


# ------------------------------------------------------------------------------------------ reading
@dataclass
class Midi:
    on: np.ndarray            # onset seconds
    off: np.ndarray           # offset seconds
    pitch: np.ndarray
    vel: np.ndarray
    beat: np.ndarray          # onset in quarter-note beats (ticks / ppq)
    end_beat: float
    bar_starts: np.ndarray    # beats where bars start (time signatures honoured)
    markers: list = field(default_factory=list)   # [(beat, text)]
    texts: list = field(default_factory=list)


def read_midi(path_or_bytes) -> Midi:
    if isinstance(path_or_bytes, (bytes, bytearray)):
        import io
        mf = mido.MidiFile(file=io.BytesIO(path_or_bytes), clip=True)
    else:
        mf = mido.MidiFile(path_or_bytes, clip=True)
    ppq = mf.ticks_per_beat or 480
    events = []  # (tick, order, msg)
    for tr in mf.tracks:
        t = 0
        for k, msg in enumerate(tr):
            t += msg.time
            events.append((t, 0 if msg.is_meta else 1, msg))
    events.sort(key=lambda e: (e[0], e[1]))
    # tempo map -> seconds
    tempo, last_tick, sec = 500000, 0, 0.0
    tsig = []
    markers, texts = [], []
    active = {}
    notes = []  # on_s, off_s, pitch, vel, on_tick, channel
    programs = {}
    for tick, _, msg in events:
        sec += (tick - last_tick) * tempo / 1e6 / ppq
        last_tick = tick
        if msg.is_meta:
            if msg.type == 'set_tempo':
                tempo = msg.tempo
            elif msg.type == 'time_signature':
                tsig.append((tick / ppq, msg.numerator * 4.0 / msg.denominator))
            elif msg.type == 'marker':
                markers.append((tick / ppq, msg.text.strip()))
            elif msg.type in ('text', 'track_name') and len(texts) < 40:
                texts.append(str(getattr(msg, 'text', None) or getattr(msg, 'name', '')).strip())
            continue
        if getattr(msg, 'channel', None) == DRUM_CH:
            continue
        if msg.type == 'program_change':
            programs.setdefault(msg.channel, msg.program)
        elif msg.type == 'note_on' and msg.velocity > 0:
            key = (msg.channel, msg.note)
            if key in active:  # re-struck without a note-off: close the old one
                s, v, tk = active.pop(key)
                notes.append((s, sec, msg.note, v, tk, msg.channel))
            active[key] = (sec, msg.velocity, tick)
        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
            key = (msg.channel, msg.note)
            if key in active:
                s, v, tk = active.pop(key)
                notes.append((s, sec, msg.note, v, tk, msg.channel))
    for (ch, p), (s, v, tk) in active.items():
        notes.append((s, sec, p, v, tk, ch))
    # a channel set to a non-piano sound (e.g. MacDonald's woodblock "stomps" in Stoptime Rag) is not piano
    if any(p < 8 for p in programs.values()):
        notes = [n for n in notes if programs.get(n[5], 0) < 8]
    notes.sort(key=lambda n: (n[0], n[2]))
    arr = np.array(notes, dtype=float).reshape(-1, 6)
    end_beat = last_tick / ppq
    if not tsig or tsig[0][0] > 0:
        tsig.insert(0, (0.0, 4.0))
    bars = []
    for i, (b0, blen) in enumerate(tsig):
        b1 = tsig[i + 1][0] if i + 1 < len(tsig) else max(end_beat, b0) + blen
        x = b0
        while x < b1 - 1e-6:
            bars.append(x)
            x += max(blen, 0.25)
    return Midi(on=arr[:, 0], off=arr[:, 1], pitch=arr[:, 2].astype(int), vel=arr[:, 3].astype(int),
                beat=arr[:, 4] / ppq, end_beat=end_beat, bar_starts=np.array(sorted(set(bars))),
                markers=markers, texts=texts)


def trim_reference(ref: Midi) -> Midi:
    """Drop what follows the music in a reference: Colin MacDonald's sequences end with a few bars of his own
    'Copyright Logo' jingle."""
    cut = [b for b, t in ref.markers if re.search(r'copyright\s*logo', t, re.I)]
    if not cut:
        return ref
    keep = ref.beat < min(cut) - 1e-6
    ref.on, ref.off, ref.pitch, ref.vel, ref.beat = (a[keep] for a in (ref.on, ref.off, ref.pitch, ref.vel, ref.beat))
    ref.end_beat = min(ref.end_beat, min(cut))
    ref.bar_starts = ref.bar_starts[ref.bar_starts < min(cut) - 1e-6]
    return ref


def _label(text):
    t = text.strip()
    m = re.match(r'^([A-H])\s*(strain|section)\b', t, re.I)
    if m:
        return m.group(1).upper()
    m = re.match(r'^(?:strain|section)\s+([A-H])\b', t, re.I)
    if m:
        return m.group(1).upper()
    low = t.lower()
    for k, v in (('intro', 'Intro'), ('instruction', 'Intro'), ('intoduction', 'Intro'), ('interlude', 'Interlude'),
                 ('transition', 'Transition'), ('coda', 'Coda'), ('vamp', 'Vamp'), ('trio', 'Trio'),
                 ('break', 'Break')):
        if low.startswith(k):
            return v + ('2' if re.search(r'(-2|second|2nd)\b', low) else '')
    if 'introduction' in low or 'intro' in low:
        return 'Intro'
    return t[:12] or '?'


def sections_of(ref: Midi):
    """[(start_beat, end_beat, label)] covering the reference, from markers or 8-bar phrases."""
    first = float(ref.beat.min()) if len(ref.beat) else 0.0
    last = float(ref.beat.max() if len(ref.beat) else 0) + 0.5
    marks = sorted((b, _label(t)) for b, t in ref.markers
                   if not re.search(r'end of|fine$|copyright', t, re.I) and b < last - 0.5)
    dedup = []
    for b, lab in marks:
        if dedup and abs(dedup[-1][0] - b) < 0.5:
            dedup[-1] = (dedup[-1][0], lab)
            continue
        dedup.append((b, lab))
    if len(dedup) >= 2:
        starts = [b for b, _ in dedup]
        if starts[0] > first + 0.5:  # notes before the first marker (a pickup or an unlabelled intro)
            dedup.insert(0, (first, 'Pickup' if starts[0] - first <= 2 else 'Intro'))
        else:
            dedup[0] = (min(first, dedup[0][0]), dedup[0][1])
        out = []
        for i, (b, lab) in enumerate(dedup):
            e = dedup[i + 1][0] if i + 1 < len(dedup) else last
            if e - b > 0.25:
                out.append((b, e, lab))
        return out
    bars = ref.bar_starts
    out = []
    for i in range(0, len(bars), 8):
        b = bars[i]
        e = bars[i + 8] if i + 8 < len(bars) else last
        if b >= last:
            break
        out.append((max(b, first) if i == 0 else b, e, f'bars {i + 1}-{min(i + 8, len(bars))}'))
    return out or [(first, last, 'all')]


# ----------------------------------------------------------------------------------------- features
def _features(times, offs, pitch, vel, n_frames, hop, t0=0.0):
    """36-d frame features: onset chroma (smeared over two frames), bass pitch-class onset, decaying sustain
    chroma.  times/offs in the same unit as hop."""
    on = np.zeros((n_frames + 2, 12))
    bass = np.zeros((n_frames + 2, 12))
    sus = np.zeros((n_frames + 2, 12))
    if len(times):
        f = np.clip(((times - t0) / hop).astype(int), 0, n_frames - 1)
        w = np.sqrt(np.clip(vel, 1, 127) / 64.0)
        pc = pitch % 12
        np.add.at(on, (f, pc), w)
        np.add.at(on, (f + 1, pc), 0.5 * w)
        # bass = lowest onset per frame
        order = np.lexsort((pitch, f))
        fs = f[order]
        firsts = np.r_[True, fs[1:] != fs[:-1]]
        idx = order[firsts]
        bass[f[idx], pc[idx]] += 1.0
        bass[f[idx] + 1, pc[idx]] += 0.5
        fe = np.clip(((offs - t0) / hop).astype(int), 0, n_frames - 1)
        fe = np.maximum(fe, f + 1)
        for a, b, p, ww in zip(f, fe, pc, w):  # sustained notes, decaying
            L = min(b - a, 24)
            if L > 0:
                sus[a:a + L, p] += ww * np.exp(-np.arange(L) / 8.0)
    X = np.hstack([on[:n_frames], 0.7 * bass[:n_frames], 0.4 * sus[:n_frames]])
    norm = np.linalg.norm(X, axis=1)
    sil = norm < 1e-6
    X[~sil] /= norm[~sil, None]
    X = np.hstack([X, sil[:, None].astype(float)])  # silence matches silence
    return X


# ---------------------------------------------------------------------------------------------- DTW
def _jump_dtw(P, R, sec_starts, sec_ends):
    """DTW of performance frames P (N x d) against reference frames R (M x d) with steps (1,1), (1,0)+pen,
    (0,1)+pen and jumps from any section end column to any section start column (+JUMP_PEN).  Returns the
    path as a list of (i, j, step) with step 0 diag, 1 perf-advances, 2 ref-advances, 3 jump, 4 start."""
    N, M = len(P), len(R)
    INF = 1e18
    choice = np.zeros((N, M), dtype=np.int8)
    jsrc = np.full(N, -1, dtype=np.int64)
    starts = np.array(sorted(set(sec_starts)), dtype=np.int64)
    ends = np.array(sorted(set(sec_ends)), dtype=np.int64)
    cols = np.arange(M, dtype=float)
    pen_cols = STEP_PEN * cols
    C = 1.0 - P @ R.T
    np.maximum(C, 0.0, out=C)  # (np.clip is ~40x slower here)
    np.minimum(C, 1.0, out=C)
    Dp = None
    diag = np.empty(M)
    diag[0] = INF
    for i in range(N):
        c = C[i]
        if i == 0:
            A = np.full(M, INF)
            ch = np.zeros(M, dtype=np.int8)
            A[0] = 0.0
            ch[0] = 4
            A[starts] = np.minimum(A[starts], JUMP_PEN)
            ch[starts[starts > 0]] = 4
        else:
            diag[1:] = Dp[:-1]
            vert = Dp + STEP_PEN
            ch = (diag > vert).astype(np.int8)
            A = np.minimum(diag, vert)
            e = ends[np.argmin(Dp[ends])]
            jv = Dp[e] + JUMP_PEN
            better = jv < A[starts]
            better &= starts != e + 1  # that one is just the diagonal
            if better.any():
                A[starts[better]] = jv
                ch[starts[better]] = 3
                jsrc[i] = e
        S = np.cumsum(c)
        val = A - (S - c) - pen_cols
        cm = np.minimum.accumulate(val)
        ch[cm < val - 1e-9] = 2
        choice[i] = ch
        Dp = S + pen_cols + cm
    # end at the last column or at any section end (+jump)
    j = M - 1
    best = Dp[M - 1]
    e = ends[np.argmin(Dp[ends])]
    if Dp[e] + JUMP_PEN < best:
        j = int(e)
    i = N - 1
    path = []
    while True:
        st = int(choice[i, j])
        path.append((i, j, st))
        if st == 4:
            break
        if st == 0:
            i, j = i - 1, j - 1
        elif st == 1:
            i -= 1
        elif st == 2:
            j -= 1
        else:
            i, j = i - 1, int(jsrc[i])
        if i < 0 or j < 0:
            break
    path.reverse()
    return path


# ------------------------------------------------------------------------------------------ helpers
def _cluster_onsets(t, gap):
    if not len(t):
        return t
    t = np.sort(t)
    keep = np.r_[True, np.diff(t) > gap]
    return t[keep]


def _ref_grid(ref: Midi):
    """The written even subdivision (beats): the most common inter-onset interval of the reference."""
    b = _cluster_onsets(ref.beat, 1e-3)
    d = np.round(np.diff(b), 4)
    d = d[(d > 0.05) & (d <= 1.0)]
    if not len(d):
        return 0.25
    vals, cnt = np.unique(d, return_counts=True)
    return float(vals[np.argmax(cnt)])


def _warp_from_path(path, n_perf, hop_s, perf_t0):
    """Perf frame -> ref frame (float), and the run id of each perf frame (runs split at jumps)."""
    jf = np.full(n_perf, np.nan)
    run = np.zeros(n_perf, dtype=int)
    acc = {}
    r = 0
    for i, j, st in path:
        if st == 3:
            r += 1
        acc.setdefault(i, []).append(j)
        run[i] = r
    for i, js in acc.items():
        jf[i] = float(np.mean(js))
    ok = ~np.isnan(jf)
    if ok.sum() >= 2:
        jf = np.interp(np.arange(n_perf), np.nonzero(ok)[0], jf[ok])
    return jf, run


def _match(perf_beat_est, perf_pitch, ref_beat, ref_pitch, tol, perf_mask):
    """Greedy one-to-one matching by pitch within tol beats (closest pairs first).
    Returns arrays: perf->ref index (-1) and ref->perf index (-1)."""
    nP, nR = len(perf_beat_est), len(ref_beat)
    p2r = np.full(nP, -1)
    r2p = np.full(nR, -1)
    by_pitch = {}
    for k in range(nR):
        by_pitch.setdefault(int(ref_pitch[k]), []).append(k)
    cand = []
    for p, ks in by_pitch.items():
        ks = np.array(ks)
        rb = ref_beat[ks]
        order = np.argsort(rb)
        ks, rb = ks[order], rb[order]
        pidx = np.nonzero((perf_pitch == p) & perf_mask)[0]
        for n in pidx:
            x = perf_beat_est[n]
            lo = np.searchsorted(rb, x - tol)
            hi = np.searchsorted(rb, x + tol, side='right')
            for q in range(lo, hi):
                cand.append((abs(rb[q] - x), n, ks[q]))
    cand.sort()
    for d, n, k in cand:
        if p2r[n] < 0 and r2p[k] < 0:
            p2r[n] = k
            r2p[k] = n
    return p2r, r2p


def _running_median(x, y, xq, half_window):
    """Median of y over x within +-half_window, evaluated on a grid of half_window/2 and interpolated to xq
    (x sorted)."""
    if not len(x):
        return np.zeros(len(xq))
    grid = np.arange(x[0], x[-1] + half_window / 2, half_window / 2)
    lo = np.searchsorted(x, grid - half_window)
    hi = np.searchsorted(x, grid + half_window, side='right')
    med = np.array([np.median(y[a:b]) if b > a else np.nan for a, b in zip(lo, hi)])
    ok = ~np.isnan(med)
    if not ok.any():
        return np.zeros(len(xq))
    return np.interp(xq, grid[ok], med[ok])


# ----------------------------------------------------------------------------------------- scoring
def score(performance_midi, reference_midi, *, detail=False) -> dict:
    perf = read_midi(performance_midi)
    ref = trim_reference(read_midi(reference_midi))
    if len(perf.on) < 16 or len(ref.on) < 16:
        return {'error': 'too few notes', 'verdict': 'liberal', 'reasons': ['unreadable']}
    secs = sections_of(ref)
    t0 = float(perf.on.min())
    perf_dur = float(perf.off.max() - t0)
    ref_b0 = 0.0
    # sustain in beats for the reference: its offsets in seconds go through its own tempo map
    ref_end_beat = _sec_to_beat(ref, ref.off)
    M = int(math.ceil((secs[-1][1] - ref_b0) / FRAME_BEAT)) + 2
    R = _features(ref.beat, ref_end_beat, ref.pitch, ref.vel, M, FRAME_BEAT, ref_b0)
    sec_starts = [int(round((s - ref_b0) / FRAME_BEAT)) for s, e, _ in secs]
    sec_ends = [max(0, min(M - 1, int(round((e - ref_b0) / FRAME_BEAT)) - 1)) for s, e, _ in secs]

    # pass 1: assume the whole reference is played once; pass 2: the tempo pass 1 measured
    hop = max(0.02, min(0.25, perf_dur / max(1.0, (ref.beat.max() - ref.beat.min())) * FRAME_BEAT))
    for _ in range(2):
        N = int(math.ceil(perf_dur / hop)) + 2
        P = _features(perf.on, perf.off, perf.pitch, perf.vel, N, hop, t0)
        path = _jump_dtw(P, R, sec_starts, sec_ends)
        diag = [(i, j) for i, j, st in path if st == 0]
        if len(diag) < 20:
            break
        # seconds per ref frame from the path's slope over diagonal-ish stretches
        ii = np.array([d[0] for d in diag], float)
        jj = np.array([d[1] for d in diag], float)
        seg = max(8, len(ii) // 40)
        slopes = []
        for a in range(0, len(ii) - seg, seg):
            dj = jj[a + seg] - jj[a]
            di = ii[a + seg] - ii[a]
            if dj > 0 and di > 0 and 0.2 < di / dj < 5:
                slopes.append(di / dj)
        if not slopes:
            break
        new_hop = hop * float(np.median(slopes))
        if abs(new_hop / hop - 1) < 0.08:
            break
        hop = max(0.015, min(0.3, new_hop))
    N = len(P)
    jf, run = _warp_from_path(path, N, hop, t0)
    # perf note -> estimated ref beat
    fpos = (perf.on - t0) / hop
    fi = np.clip(fpos.astype(int), 0, N - 1)
    fi2 = np.clip(fi + 1, 0, N - 1)
    frac = np.clip(fpos - fi, 0, 1)
    same = run[fi] == run[fi2]
    jest = np.where(same, jf[fi] * (1 - frac) + jf[fi2] * frac, jf[fi])
    est = ref_b0 + jest * FRAME_BEAT
    counted = perf.vel >= MIN_VEL

    p2r, r2p = _match(est, perf.pitch, ref.beat, ref.pitch, TOL0, counted)
    m = p2r >= 0
    if m.sum() >= 10:
        resid = ref.beat[p2r[m]] - est[m]
        order = np.argsort(perf.on[m])
        corr = _running_median(perf.on[m][order], resid[order], perf.on, half_window=max(2.0, 6 * hop * 8))
        est2 = est + corr
        p2r, r2p = _match(est2, perf.pitch, ref.beat, ref.pitch, TOL, counted)
        est = est2

    # ------------------------------------------------------------------ structure from the path
    occ = []  # (section index, perf frame from, perf frame to, ref frames covered)
    sec_of_col = np.zeros(M, dtype=int)
    for k, (s, e, _) in enumerate(secs):
        a = int(round((s - ref_b0) / FRAME_BEAT))
        b = int(round((e - ref_b0) / FRAME_BEAT))
        sec_of_col[a:b] = k
    sec_of_col[int(round((secs[-1][1] - ref_b0) / FRAME_BEAT)):] = len(secs) - 1
    cur = None
    for i, j, st in path:
        k = int(sec_of_col[min(j, M - 1)])
        if cur is None or k != cur[0] or st == 3:
            if cur is not None:
                occ.append(cur)
            cur = [k, i, i, set()]
        cur[2] = i
        if st in (0, 1, 3, 4):
            cur[3].add(j)
    if cur is not None:
        occ.append(cur)
    sec_len = [max(1, int(round((e - s) / FRAME_BEAT))) for s, e, _ in secs]
    played = []  # (section index, coverage)
    for k, a, b, cols_ in occ:
        cov = len(cols_) / sec_len[k]
        if cov >= 0.5:
            if played and played[-1][0] == k:
                played[-1] = (k, played[-1][1] + cov)
            else:
                played.append((k, cov))
    played_idx = [k for k, _ in played]
    def bars_in(k):
        s, e, _ = secs[k]
        return int(np.sum((ref.bar_starts >= s - 1e-6) & (ref.bar_starts < e - 1e-6))) or max(1, round((e - s) / 2))
    covered = set(played_idx)
    skipped = [k for k in range(len(secs)) if k not in covered]
    seen, extra = set(), []
    for k in played_idx:
        if k in seen:
            extra.append(k)
        seen.add(k)
    in_order = played_idx == sorted(played_idx) and not extra
    skipped_bars = sum(bars_in(k) for k in skipped)
    extra_bars = sum(bars_in(k) for k in extra)
    total_bars = sum(bars_in(k) for k in range(len(secs)))
    played_bars = sum(bars_in(k) for k in played_idx)

    # ------------------------------------------------------------------ note statistics
    ref_sec = np.searchsorted([s for s, e, _ in secs], ref.beat, side='right') - 1
    ref_sec = np.clip(ref_sec, 0, len(secs) - 1)
    in_cov = np.isin(ref_sec, list(covered))
    n_ref = len(ref.beat)
    matched_ref = r2p >= 0
    recall = float(matched_ref.sum() / n_ref)
    recall_played = float(matched_ref[in_cov].sum() / max(1, in_cov.sum()))
    n_perf = int(counted.sum())
    matched_perf = (p2r >= 0) & counted
    precision = float(matched_perf.sum() / max(1, n_perf))

    # unmatched performance notes: octave doubling / chord tone / foreign
    kinds = {'octave': 0, 'chord': 0, 'foreign': 0}
    um = np.nonzero(counted & (p2r < 0))[0]
    rb_sorted = np.argsort(ref.beat)
    rbs = ref.beat[rb_sorted]
    ref_end_b = ref_end_beat
    for n in um:
        x = est[n]
        lo = np.searchsorted(rbs, x - 0.3)
        hi = np.searchsorted(rbs, x + 0.3, side='right')
        near = ref.pitch[rb_sorted[lo:hi]]
        p = perf.pitch[n]
        if len(near) and np.any(((near - p) % 12 == 0) & (near != p)):
            kinds['octave'] += 1
            continue
        # chord: pitch classes sounding in the reference within the beat around x
        lo2 = np.searchsorted(rbs, x - 1.0)
        hi2 = np.searchsorted(rbs, x + 0.25, side='right')
        idx = rb_sorted[lo2:hi2]
        sounding = idx[(ref.beat[idx] <= x + 0.25) & (ref_end_b[idx] >= x - 0.25)]
        if len(sounding) and (p % 12) in set((ref.pitch[sounding] % 12).tolist()):
            kinds['chord'] += 1
        else:
            kinds['foreign'] += 1
    precision_harmonic = float((matched_perf.sum() + kinds['octave'] + kinds['chord']) / max(1, n_perf))
    pb = max(1, played_bars)
    extra_per_bar = float(len(um) / pb)
    foreign_per_bar = float(kinds['foreign'] / pb)
    cov_bars = max(1, sum(bars_in(k) for k in covered))
    missing_per_bar = float((in_cov & ~matched_ref).sum() / cov_bars)

    # ------------------------------------------------------------------ swing
    g = _ref_grid(ref)
    ratios = []
    if g > 0:
        ob = _cluster_onsets(ref.beat, 1e-3)
        # performed time of each reference onset cluster: earliest matched note in the cluster
        perf_t = {}
        for k in np.nonzero(r2p >= 0)[0]:
            key = round(float(ref.beat[k]), 4)
            t = perf.on[r2p[k]]
            perf_t[key] = min(t, perf_t.get(key, t))
        obs = set(round(float(b), 4) for b in ob)
        for b in ob:
            b = round(float(b), 4)
            if abs((b / (2 * g)) - round(b / (2 * g))) > 1e-3:
                continue  # pairs start on the strong half
            b1, b2 = round(b + g, 4), round(b + 2 * g, 4)
            if b1 in obs and b2 in obs and b in perf_t and b1 in perf_t and b2 in perf_t:
                lng, sht = perf_t[b1] - perf_t[b], perf_t[b2] - perf_t[b1]
                if lng > 0.02 and sht > 0.02:
                    ratios.append(lng / sht)
    swing = float(np.median(ratios)) if len(ratios) >= 20 else None

    labels = [lab for _, _, lab in secs]
    res = {
        'recall': round(recall, 4), 'recall_played': round(recall_played, 4), 'precision': round(precision, 4),
        'precision_harmonic': round(precision_harmonic, 4),
        'extra_per_bar': round(extra_per_bar, 3), 'missing_per_bar': round(missing_per_bar, 3),
        'foreign_per_bar': round(foreign_per_bar, 3), 'extra_kinds': kinds,
        'swing': None if swing is None else round(swing, 3), 'swing_pairs': len(ratios), 'grid_beats': g,
        'structure': {'written': ' '.join(labels), 'played': ' '.join(labels[k] for k in played_idx),
                      'in_order': bool(in_order), 'skipped': [labels[k] for k in skipped],
                      'extra': [labels[k] for k in extra], 'skipped_bars': int(skipped_bars),
                      'extra_bars': int(extra_bars), 'bars': int(total_bars)},
        'tempo_bpm': round(60.0 / (hop / FRAME_BEAT), 1),
        'notes': {'perf': int(len(perf.on)), 'perf_counted': n_perf, 'ref': n_ref,
                  'matched': int(matched_perf.sum())},
    }
    res['verdict'], res['reasons'] = verdict(res)
    if detail:
        res['_path'] = path
    return res


def _sec_to_beat(mid: Midi, secs):
    """Map seconds to beats through the file's own (on second, on beat) pairs."""
    o = np.argsort(mid.on)
    x, y = mid.on[o], mid.beat[o]
    x, idx = np.unique(x, return_index=True)
    y = y[idx]
    if len(x) < 2:
        return mid.beat + 0.5
    return np.interp(secs, x, y)


def verdict(r):
    """('faithful' | 'embellished' | 'liberal', [reasons])."""
    st = r['structure']
    sw = r['swing']
    f, e = THRESHOLDS['faithful'], THRESHOLDS['embellished']
    why = []
    if r['recall_played'] < f['recall_played']:
        why.append(f"misses {100 * (1 - r['recall_played']):.0f}% of the written notes")
    if r['precision'] < f['precision']:
        why.append(f"{100 * (1 - r['precision']):.0f}% of its notes are not in the score "
                   f"({r['extra_kinds']['octave']} octave, {r['extra_kinds']['chord']} chord, "
                   f"{r['extra_kinds']['foreign']} foreign)")
    if sw is not None and sw > f['swing_max']:
        why.append(f'swung (long/short {sw:.2f})')
    if st['skipped_bars'] > f['skipped_bars']:
        why.append(f"cuts {st['skipped_bars']} bars ({', '.join(st['skipped'])})")
    if st['extra_bars'] > f['extra_bars']:
        why.append(f"adds {st['extra_bars']} bars ({', '.join(st['extra'])})")
    if not why:
        return 'faithful', []
    ok = (r['recall_played'] >= e['recall_played'] and r['precision'] >= e['precision']
          and r['precision_harmonic'] >= e['precision_harmonic'] and r['foreign_per_bar'] <= e['foreign_per_bar']
          and (sw is None or sw <= e['swing_max']) and st['skipped_bars'] <= e['skipped_bars']
          and st['extra_bars'] <= e['extra_bars'])
    return ('embellished' if ok else 'liberal'), why


# ------------------------------------------------------------------------------------------------ CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('perf', nargs='?')
    ap.add_argument('ref', nargs='?')
    ap.add_argument('--pairs', help='TSV: perf<TAB>ref[<TAB>label]')
    ap.add_argument('--out')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    if a.pairs:
        rows = []
        for line in open(a.pairs, encoding='utf-8'):
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 2 or line.startswith('#'):
                continue
            try:
                r = score(parts[0], parts[1])
            except Exception as ex:  # noqa: BLE001 - one bad file must not stop a batch
                r = {'error': f'{type(ex).__name__}: {ex}', 'verdict': None}
            r.update({'perf': parts[0], 'ref': parts[1], 'label': parts[2] if len(parts) > 2 else ''})
            rows.append(r)
            print(f"{r.get('verdict')!s:12s} rec={r.get('recall_played')} prec={r.get('precision')} "
                  f"harm={r.get('precision_harmonic')} swing={r.get('swing')} "
                  f"struct={r.get('structure', {}).get('played', '')!r}  {r['label'] or os.path.basename(parts[0])}",
                  flush=True)
        if a.out:
            json.dump(rows, open(a.out, 'w'), indent=1)
        return
    if not (a.perf and a.ref):
        ap.error('PERF and REF, or --pairs')
    r = score(a.perf, a.ref)
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        st = r['structure']
        print(f"verdict      {r['verdict']}  {'; '.join(r['reasons'])}")
        print(f"recall       {r['recall']}  (played sections {r['recall_played']})")
        print(f"precision    {r['precision']}  (harmonic {r['precision_harmonic']}; extra {r['extra_kinds']})")
        print(f"per bar      extra {r['extra_per_bar']}  missing {r['missing_per_bar']}  foreign {r['foreign_per_bar']}")
        print(f"swing        {r['swing']}  ({r['swing_pairs']} pairs on a {r['grid_beats']}-beat grid)")
        print(f"written      {st['written']}")
        print(f"played       {st['played']}  in order={st['in_order']} skipped={st['skipped_bars']} bars, "
              f"added={st['extra_bars']} bars")
        print(f"tempo        {r['tempo_bpm']} bpm;  notes {r['notes']}")


if __name__ == '__main__':
    main()
