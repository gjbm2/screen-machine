"""ring.py: the last RING_SECONDS of the delivered audio, per stream, as 16 kHz mono int16 (show_service/README.md,
"Stage 3 > S3.12"; §3.3).

Written on show-rx (``Rings.write`` = contract.OnAudio, after Features and the Clock, under __main__'s "ring.write"
guard); read from any thread (``clip``, ``latest``, ``info``) without a lock.

* **Write path** (one block, no buffer allocated: the ring and the scratch buffers are preallocated and reused; soxr's
  small output array per chunk is the only temporary): mono mix into a scratch buffer (``np.mean(..., out=)``) ->
  ``soxr.ResampleStream`` to RING_FS (rebuilt on a new rate, cleared on AUDIO_SEGMENT or a jump in n0) -> scaled,
  rounded and stored as int16 at ``w % size``. AUDIO_GAP blocks go through the same path (they are zeros, so the
  resampler stays continuous) and record a gap span.
* **Time.** soxr's stream output is delay-compensated: output sample j of a segment sounds at the segment's input time
  plus j / RING_FS. Each segment keeps one anchor ``(abs index, acoustic host s)``, refreshed every block from that
  block's ``(n0, t0_host_s)``, so sender skew never accumulates. A clip is time-linear: ``pcm[i]`` sounds at
  ``t0_s + i / fs``; spans with nothing held (between segments) are zeros and listed in ``gaps`` with the AUDIO_GAP
  spans.
* **Seqlock.** The writer publishes ``w_pending`` (the index it is about to reach) before it copies and ``w`` after.
  A reader copies, then reads ``w_pending`` again: any sample older than ``w_pending - size`` may have been
  overwritten while it copied, so that part is cut off the clip's start (counted in ``lapped_reads``).
* **Armed.** ``armed`` (the listener sets it each tick) False: ``write`` returns at once, so nothing is resampled on
  show-rx while no clip could go. When it comes back, everything held before is forgotten (``floor``) and a first clip
  waits for the ring to fill again.
* **Streams.** Only the active stream is delivered, so normally one ring fills. Over ``max_streams`` the ring written
  longest ago is dropped and its buffer reused (a reader still holding it sees it as lapped).
"""

from __future__ import annotations

import collections
import logging
import math
from typing import Any, NamedTuple, Optional

import numpy as np

from . import contract

log = logging.getLogger("show_service.ring")

SOXR_QUALITY = "MQ"          # a listener clip, not a master: medium quality, less work per block than HQ
SCRATCH_FRAMES = 4096        # grown (once) for a bigger block
MAX_SEGMENTS = 64            # per ring; the oldest drop off (they are long overwritten by then)
MAX_GAPS = 256


class Clip(NamedTuple):
    stream: str
    fs: int
    t0_s: float                              # acoustic host s of pcm[0]
    pcm: np.ndarray                          # int16 mono, time-linear (zeros where nothing was held)
    gaps: list                               # [(t_a, t_b)] spans inside the clip with no real sound


class _Seg:
    __slots__ = ("abs0", "n0", "fs", "n_expect", "anchor")

    def __init__(self, abs0: int, n0: int, fs: int, t0: float) -> None:
        self.abs0 = abs0                     # ring index of the segment's first output sample
        self.n0 = n0                         # its input sample index
        self.fs = fs                         # input rate
        self.n_expect = n0
        self.anchor = (float(abs0), float(t0))   # (ring index, acoustic s): swapped whole, never torn


class _Ring:
    __slots__ = ("stream", "fs", "size", "buf", "w", "w_pending", "writes", "gap_count", "lapped_reads", "segs",
                 "seg", "rs", "rs_fs", "gaps", "t_last", "mono", "out", "floor")

    def __init__(self, stream: str, fs: int, size: int, buf: Optional[np.ndarray] = None) -> None:
        self.stream, self.fs, self.size = stream, fs, size
        self.buf = buf if buf is not None else np.zeros(size, np.int16)
        self.w = 0                           # samples written, ever (the next sample's index)
        self.w_pending = 0
        self.writes = 0
        self.gap_count = 0
        self.lapped_reads = 0
        self.segs: collections.deque = collections.deque(maxlen=MAX_SEGMENTS)
        self.seg: Optional[_Seg] = None
        self.rs: Any = None                  # soxr.ResampleStream | False (no resampling) | None (not built)
        self.rs_fs = 0
        self.gaps: collections.deque = collections.deque(maxlen=MAX_GAPS)   # [abs_a, abs_b) float spans
        self.t_last = -math.inf
        self.mono = np.zeros(SCRATCH_FRAMES, np.float32)
        self.out = np.zeros(SCRATCH_FRAMES, np.float32)
        self.floor = 0                       # nothing below this index is held (set when recording resumes)


class Rings:
    def __init__(self, *, seconds: float = contract.RING_SECONDS, fs: int = contract.RING_FS,
                 max_streams: int = contract.RING_MAX_STREAMS) -> None:
        self.seconds = float(seconds)
        self.fs = int(fs)
        self.size = max(1, int(round(self.seconds * self.fs)))
        self.max_streams = max(1, int(max_streams))
        self._rings: dict[str, _Ring] = {}
        self._last: Optional[str] = None
        self.errors = 0
        #: Set by the listener (any thread; one attribute): False while no clip could go (the AI listening switch,
        #: the mode or the vibe says no), so show-rx skips the resampling. Recording resumes from nothing: what was
        #: held before the pause is forgotten, so a clip never spans it.
        self.armed = True
        self._paused = False

    # -- show-rx ---------------------------------------------------------------------------------------------------
    def write(self, stream: str, n0: int, t0_host_s: float, pcm: np.ndarray, fs: int, flags: int) -> None:
        """contract.OnAudio (show-rx)."""
        if not self.armed:
            self._paused = True
            return
        if self._paused:                     # recording again: forget what was held before the pause
            self._paused = False
            for old in list(self._rings.values()):
                old.floor, old.seg = old.w, None
        frames = int(pcm.shape[0])
        if frames <= 0 or fs <= 0:
            return
        r = self._rings.get(stream)
        if r is None:
            r = self._new(stream)
        self._last = stream
        seg = r.seg
        if seg is None or (flags & contract.AUDIO_SEGMENT) or fs != seg.fs or n0 != seg.n_expect:
            seg = self._segment(r, n0, fs, t0_host_s)
        # mono mix, into the scratch buffer when it needs one
        if pcm.ndim == 1:
            x = pcm
        elif pcm.shape[1] == 1:
            x = pcm[:, 0]
        else:
            if frames > r.mono.shape[0]:
                r.mono = np.zeros(frames, np.float32)
            x = r.mono[:frames]
            np.mean(pcm, axis=1, out=x)
        if x.dtype != np.float32 or not x.flags.c_contiguous:
            if frames > r.mono.shape[0]:
                r.mono = np.zeros(frames, np.float32)
            np.copyto(r.mono[:frames], x, casting="unsafe")
            x = r.mono[:frames]
        y = r.rs.resample_chunk(x) if r.rs else x
        ratio = self.fs / fs
        seg.n_expect = n0 + frames
        seg.anchor = (seg.abs0 + (n0 - seg.n0) * ratio, float(t0_host_s))
        if flags & contract.AUDIO_GAP:
            a = seg.abs0 + (n0 - seg.n0) * ratio
            b = a + frames * ratio
            g = r.gaps[-1] if r.gaps else None
            if g is not None and abs(g[1] - a) < 2.0:
                r.gaps[-1] = (g[0], b)
            else:
                r.gaps.append((a, b))
                r.gap_count += 1
        k = int(y.shape[0])
        if k:
            if k > r.out.shape[0]:
                r.out = np.zeros(k, np.float32)
            tmp = r.out[:k]
            np.multiply(y, 32767.0, out=tmp)
            np.rint(tmp, out=tmp)
            np.clip(tmp, -32768.0, 32767.0, out=tmp)
            if k > r.size:                   # a block longer than the ring: keep its end
                skip = k - r.size
                r.w += skip
                tmp, k = tmp[skip:], r.size
            w = r.w
            r.w_pending = w + k
            i = w % r.size
            first = min(k, r.size - i)
            r.buf[i:i + first] = tmp[:first]
            if first < k:
                r.buf[:k - first] = tmp[first:]
            r.w = w + k
        r.writes += 1
        r.t_last = t0_host_s + frames / fs

    def _new(self, stream: str) -> _Ring:
        buf = None
        if len(self._rings) >= self.max_streams:
            old_name = min(self._rings, key=lambda s: self._rings[s].t_last)
            old = self._rings.pop(old_name)
            old.w_pending = 1 << 62          # a reader still copying from it sees everything as lapped
            buf = old.buf
            log.info("ring: dropped %s (written longest ago) for %s", old_name, stream)
        r = _Ring(stream, self.fs, self.size, buf)
        self._rings[stream] = r
        return r

    def _segment(self, r: _Ring, n0: int, fs: int, t0: float) -> _Seg:
        if fs != r.rs_fs or r.rs is None:
            r.rs_fs = fs
            if fs != self.fs:
                import soxr
                r.rs = soxr.ResampleStream(fs, self.fs, 1, dtype="float32", quality=SOXR_QUALITY)
            else:
                r.rs = False
        elif r.rs:
            r.rs.clear()
        seg = _Seg(r.w, n0, fs, t0)
        r.segs.append(seg)
        r.seg = seg
        return seg

    # -- any thread ------------------------------------------------------------------------------------------------
    def _ring(self, stream: Optional[str]) -> Optional[_Ring]:
        name = stream if stream is not None else self._last
        return self._rings.get(name) if name is not None else None

    def _pieces(self, r: _Ring, w: int) -> list[tuple[int, int, float]]:
        """The held content in order: [(abs_lo, abs_hi, t_lo)], each piece time-linear."""
        oldest = max(0, w - r.size, r.floor)
        segs = list(r.segs)
        out = []
        for i, s in enumerate(segs):
            lo = max(s.abs0, oldest)
            hi = segs[i + 1].abs0 if i + 1 < len(segs) else w
            hi = min(hi, w)
            if hi <= lo:
                continue
            a_idx, a_t = s.anchor
            out.append((lo, hi, a_t + (lo - a_idx) / self.fs))
        return out

    def clip(self, stream: Optional[str], t0_s: float, t1_s: float) -> Optional[Clip]:
        """The audio that sounded in [t0_s, t1_s) (trimmed to what is held), or None if nothing is."""
        r = self._ring(stream)
        if r is None or not (t1_s > t0_s):
            return None
        fs = self.fs
        w = r.w
        pieces = self._pieces(r, w)
        if not pieces:
            return None
        held0 = pieces[0][2]
        held1 = max(p[2] + (p[1] - p[0]) / fs for p in pieces)
        T0 = max(t0_s, held0)
        T1 = min(t1_s, held1, T0 + self.seconds)
        n = int(round((T1 - T0) * fs))
        if n <= 0:
            return None
        out = np.zeros(n, np.int16)
        covered: list[tuple[int, int]] = []
        copied_lo = None
        for lo, hi, tlo in pieces:
            # this piece's part inside [T0, T1)
            s_off = int(round((tlo - T0) * fs))          # where abs ``lo`` lands in out
            a = max(0, -s_off)                           # skip into the piece
            o = max(0, s_off)
            m = min(hi - lo - a, n - o)
            if m <= 0:
                continue
            src = lo + a
            i = src % r.size
            first = min(m, r.size - i)
            out[o:o + first] = r.buf[i:i + first]
            if first < m:
                out[o + first:o + m] = r.buf[:m - first]
            covered.append((o, o + m))
            copied_lo = src if copied_lo is None else min(copied_lo, src)
        # seqlock: anything older than w_pending - size may have been overwritten while we copied
        limit = r.w_pending - r.size
        cut = 0
        if copied_lo is not None and copied_lo < limit:
            r.lapped_reads += 1
            for lo, hi, tlo in pieces:
                if lo <= limit < hi:
                    cut = max(cut, int(math.ceil((tlo + (limit - lo) / fs - T0) * fs)))
                elif hi <= limit:
                    cut = max(cut, int(math.ceil((tlo + (hi - lo) / fs - T0) * fs)))
            cut = min(max(cut, 0), n)
            if cut >= n:
                return None
        # gaps: unheld spans between pieces, then the AUDIO_GAP spans
        gaps: list[tuple[float, float]] = []
        covered.sort()
        pos = cut
        for a, b in covered:
            if a > pos:
                gaps.append((T0 + pos / fs, T0 + a / fs))
            pos = max(pos, b)
        if pos < n:
            gaps.append((T0 + pos / fs, T1))
        for ga, gb in list(r.gaps):
            ta, tb = self._abs_time(pieces, ga), self._abs_time(pieces, gb)
            if ta is None or tb is None:
                continue
            ta, tb = max(ta, T0 + cut / fs), min(tb, T1)
            if tb > ta:
                gaps.append((ta, tb))
        gaps.sort()
        pcm = out[cut:] if cut else out
        return Clip(r.stream, fs, T0 + cut / fs, pcm, gaps)

    def _abs_time(self, pieces: list, idx: float) -> Optional[float]:
        for lo, hi, tlo in pieces:
            if lo <= idx <= hi:
                return tlo + (idx - lo) / self.fs
        return None

    def latest(self, stream: Optional[str] = None, seconds: float = 24.0) -> Optional[Clip]:
        """The last ``seconds`` held (ending at the newest sample); ``stream`` None = the stream written last."""
        r = self._ring(stream)
        if r is None:
            return None
        pieces = self._pieces(r, r.w)
        if not pieces:
            return None
        lo, hi, tlo = pieces[-1]
        t_end = tlo + (hi - lo) / self.fs
        return self.clip(r.stream, t_end - float(seconds), t_end)

    def info(self) -> dict:
        out = {}
        for name, r in list(self._rings.items()):
            pieces = self._pieces(r, r.w)
            held = sum(hi - lo for lo, hi, _ in pieces) / self.fs
            out[name] = {"fs": self.fs, "held_s": round(held, 3),
                         "t_first_s": round(pieces[0][2], 3) if pieces else None,
                         "t_last_s": round(pieces[-1][2] + (pieces[-1][1] - pieces[-1][0]) / self.fs, 3)
                         if pieces else None,
                         "writes": r.writes, "gaps": r.gap_count, "lapped_reads": r.lapped_reads}
        return out

    def streams(self) -> list[str]:
        return list(self._rings)

    @property
    def last_stream(self) -> Optional[str]:
        return self._last
