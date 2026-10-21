"""mllink: show_service's side of the ML worker (README "Stage 4", S4.3 and S4.5; SHOW_ENGINE §5.3, §6).

Two pieces, built on ``show_service/ml/supervisor.py`` (B7's FrameFeeder and WorkerSupervisor, which follow S4.3
exactly; c8's integration ruling, 16 Oct: mllink wraps them rather than repeating them):

``MLFeed`` (made by __main__, written on show-rx: no locks, no waits)
  ``write`` = contract.OnAudio, called after Rings.write under the guard "ml.feed". While no worker is attached it
  returns at once (nothing is resampled). Otherwise: a mono mix -> ``soxr.ResampleStream(fs, 16000, MQ)`` -> the
  FrameFeeder's 800-sample frames, each one non-blocking ``os.write`` (a full pipe drops the frame and counts it; the
  next written frame carries ML_FLAG_AFTER_DROP and the jump in n0). The resampler is rebuilt (and the feeder starts a
  segment) on a new rate, on AUDIO_SEGMENT, on a jump in the receiver's index, or after a time with no worker.
  **Time.** soxr's stream output is delay-compensated, so output sample m of a resampler run sounds at input position
  ``n_start + m x fs / 16000``; a chunk's t0 comes from the newest block: ``t0_blk + (p - n0_blk) / fs``, the
  receiver's skew-tracked times, never a free count. AUDIO_GAP blocks go through as zeros (the index stays continuous)
  and flag the frames they reach ML_FLAG_GAP.

``MLLink`` (component "ml", 1 Hz, on show-brain)
  - **The worker.** A WorkerSupervisor spawns ``[ml.python, -m, show_service.ml.worker, --models, ml.models_dir,
    --parent, <pid>, --nice, ml.nice]`` (asyncio, its own session, stdin our 1 MB pipe, stdout lines, stderr
    ``<state_dir>/ml-worker.log``, appended per run), sends the config frame (the ``ml`` keys the worker reads:
    supervisor.WORKER_KEYS), attaches the feed after ``hello``, restarts it on exit or stall with S4.3's backoff, and
    never gives up. ``ml.enabled: false`` stops it; a change to python, models_dir or nice restarts it; any other
    change goes as a config frame (hot reload). A value it can't use takes its starting point and adds a
    ``problems`` line; a worker section that can't make a frame (too big, a NaN, a date) keeps the last good one.
  - **The view** (``ctx.ml``, MLView, swapped by reference): the latest ced/tags/key/beats for the stream measured,
    ``ced_recent`` and ``emb_recent`` (the last ``RECENT_S`` for that stream; another stream resets them),
    ``vote``, ``ages``, ``hello`` and ``stale_s``: per task, max(ml.results_stale_s, ml.stale_periods x its interval
    in the last stats line), so every reader's freshness follows the worker's own stretching and none flaps while it
    degrades. Each tags line's embedding is decoded once (float32).
  - **Beat votes** (§5.3: integer corrections only, never a phase or a tempo). ``beat_vote`` (pure) reads a beats line
    against the clock's published line where that line holds (the window's last ``local_s``, the line carried back
    from its anchor at Beat This!'s own local period; an ungated line counts while locked or holding, and at 0.5
    either half of its beats may be the one kept); ``_apply`` then sends ``ctx.clock_correct`` only the parts that
    differ, once the same vote came ``agree_runs`` times in the run (turns that are no vote don't break it; another
    vote does; ``run_max_s`` and a new epoch end it) with ``conf >= min_conf`` and ``cooldown_s`` after the last one;
    an octave also needs one of the run's votes judged while the clock's gate was beat or dmx.
    An octave goes alone (bar and one wait for a run under the new numbering; a halving onto the other half carries
    the one that picks it), with the ``octave_*``
    thresholds: the clock judges it on the room's audio at its switch beat, since conf can't pick an octave (one
    grid's beats are a subset of the other's; README S4.15 "Octave evidence"). A new bar length needs a one
    and ``bar_agree_runs`` runs in a row. A vote that agrees with the clock in full is sent too (at most every
    ``cooldown_s``): the clock raises its bar_conf on an agreeing one, which it otherwise never hears (B9's open point).
  - **CPU** every ``CPU_EVERY_S`` from ``/proc``: ``cpu`` = {worker, service, total, children} in cores. ``service``
    is show_service's own process plus its other children (``child_pids``: __main__ passes the clock's beat process,
    ``Clock.beat_pid()``, None while it is down); a respawned child's CPU counts from its first reading. The owner's
    0.8 is enforced by degrading: while the 60 s total is over ``TOTAL_WARN``, the worker's cpu_max (a config frame) is
    0.8 less show_service's own 60 s share, within [ml.cpu_floor, ml.cpu_max]; the worker's guard then stretches its
    intervals. It goes back once show_service leaves room again (``cpu_limit`` in the snapshot).
  - **The log** (``ml-worker.log``) is appended per run (a header line each) and rotated (to ``.1``) past
    ``LOG_ROTATE_BYTES``.

Imports: contract, ``ml.supervisor`` / ``ml.protocol`` (stdlib + numpy), numpy and soxr (both in ~/.venvs/sm-show).
Nothing here names a mode, vibe, device, stream or genre.
"""
from __future__ import annotations

import collections
import logging
import math
import os
import pathlib
import shutil
import time
from typing import Any, Callable, Optional

import numpy as np

from . import contract
from .ml import protocol as P
from .ml.supervisor import FrameFeeder, WorkerSupervisor, check_frame, resolve_checked, worker_section

log = logging.getLogger("show_service.mllink")

SOXR_QUALITY = "MQ"              # S4.5: the worker's ears, not a master (ring.py's choice too)
RECENT_S = 30.0                  # S4.5: ced_recent / emb_recent keep 30 s for the view's stream
VOTES_KEEP = 10
CPU_EVERY_S = 10.0               # S4.5: /proc every 10 s
TOTAL_WARN = 0.8                 # owner: show_service + the worker under 0.8 of one core
TOTAL_WARN_FOR_S = 60.0          # ...measured over this long before the worker's share is cut (and logged)
TOTAL_WARN_LOG_S = 600.0
CPU_STEP = 0.02                  # the worker's effective cpu_max is sent again only when it moves this much
LOG_ROTATE_BYTES = 1 << 20       # contract.ML_LOG: rotated at 1 MB
LOG_CHECK_S = 60.0
HEAVY_EVERY_S = 30.0             # the snapshot's log tail and cost percentiles (the counters go at 1 Hz)
STAT_N = 2000
#: S4.9's starting points for what mllink itself reads (show.yaml ml.beat_votes; the same numbers, marked there)
BEAT_VOTES_FALLBACK: dict = {"match_ms": 70, "octave_tol": 0.06, "min_conf": 0.6, "one_agree": 0.6, "agree_runs": 2,
                             "bar_agree_runs": 4, "bar_lens": [3, 4], "cooldown_s": 30, "local_s": 10,
                             "octave_add_min": 0.2, "octave_drop_max": 0.5, "octave_prior_oct": 0.8,
                             "run_max_s": 90}
# (the octave_* keys go with each octave to the clock, which judges it on the room's audio: clock.OCT_ADD_MIN etc.)
LOCAL_MIN_BEATS = 6              # beat_vote: Beat This!'s beats in the local span before its tempo counts
VOTE_UNGATED_STATES = ("locked", "holding")   # beat_vote: an ungated line in these is read too (not searching, free)
RESULTS_STALE_FALLBACK = 3.0     # starting point (show.yaml ml.results_stale_s)
STALE_PERIODS_FALLBACK = 1.5     # starting point (show.yaml ml.stale_periods)
CPU_FLOOR_FALLBACK = 0.1         # starting point (show.yaml ml.cpu_floor)
CPU_MAX_FALLBACK = 0.35          # starting point (show.yaml ml.cpu_max; the worker's own DEFAULTS)
_CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def decode_emb(b64: str, dim: int) -> np.ndarray:
    """MlTags.emb -> float32 (dim,) (S4.5's pure helper; protocol.decode_emb)."""
    return P.decode_emb(b64, dim)


# =====================================================================================================================
# MLFeed (show-rx)
# =====================================================================================================================
class MLFeed:
    """The measured stream, 16 kHz mono, into the worker's stdin (module docstring). ``write`` is show-rx's; ``attach``
    and ``stats`` are any thread's."""

    def __init__(self, *, fs: int = contract.ML_FS, frame_samples: int = contract.ML_FRAME_SAMPLES) -> None:
        self.fs = int(fs)
        self.feeder = FrameFeeder(frame_samples)
        self._rs: Any = None                 # soxr.ResampleStream | False (no resampling) | None (rebuild)
        self._rs_fs = 0
        self._n_start = 0                    # the receiver's index of the resampler's first input sample
        self._m = 0                          # output samples it has given
        self._n_expect: Optional[int] = None
        self._mono = np.zeros(4096, np.float32)
        self._blk_us: collections.deque = collections.deque(maxlen=STAT_N)
        self.blocks = 0
        self.rebuilds = 0
        self._soxr: Any = None
        try:
            import soxr                      # in ~/.venvs/sm-show (ring.py and the clock use it)
            self._soxr = soxr
        except Exception as e:               # degrade: without soxr the feed stays off
            log.error("soxr not importable (%s): the ML feed is off", e)

    def attach(self, fd: Optional[int]) -> None:
        """The worker's stdin write end (O_NONBLOCK), or None; taken at the next write (a new fd: a new segment)."""
        self.feeder.attach(fd)

    def write(self, stream: str, n0: int, t0_host_s: float, pcm: np.ndarray, fs: int, flags: int) -> None:
        """contract.OnAudio (show-rx). Returns at once while no worker is attached."""
        if not self.feeder.attached or self._soxr is None:
            self._rs = None                  # the next attached write starts a fresh resampler run
            return
        t = time.perf_counter_ns()
        frames = int(pcm.shape[0])
        if frames <= 0 or fs <= 0:
            return
        if (self._rs is None or fs != self._rs_fs or (flags & contract.AUDIO_SEGMENT)
                or n0 != self._n_expect):
            self._rebuild(fs, n0)
        self._n_expect = n0 + frames
        if pcm.ndim == 1:
            x = pcm
        elif pcm.shape[1] == 1:
            x = pcm[:, 0]
        else:
            if frames > self._mono.shape[0]:
                self._mono = np.zeros(frames, np.float32)
            x = self._mono[:frames]
            np.mean(pcm, axis=1, out=x)
        if x.dtype != np.float32 or not x.flags.c_contiguous:
            if frames > self._mono.shape[0]:
                self._mono = np.zeros(frames, np.float32)
            np.copyto(self._mono[:frames], x, casting="unsafe")
            x = self._mono[:frames]
        y = self._rs.resample_chunk(x) if self._rs else x
        k = int(y.shape[0])
        if k:
            p = self._n_start + self._m * fs / self.fs             # input position of y[0]
            self._m += k
            self.feeder.write(stream, t0_host_s + (p - n0) / fs, y, gap=bool(flags & contract.AUDIO_GAP))
        self.blocks += 1
        self._blk_us.append((time.perf_counter_ns() - t) / 1000.0)

    def _rebuild(self, fs: int, n0: int) -> None:
        self._rs_fs = fs
        self._rs = (self._soxr.ResampleStream(fs, self.fs, 1, dtype="float32", quality=SOXR_QUALITY)
                    if fs != self.fs else False)
        self._n_start, self._m = n0, 0
        self.feeder.segment()
        self.rebuilds += 1

    def stats(self, costs: bool = True) -> dict:
        """Any thread: the feeder's counters, plus (``costs``) show-rx's cost per block (µs, p50/p95)."""
        out = {**self.feeder.stats(), "blocks": self.blocks, "rebuilds": self.rebuilds}
        if costs:
            b = np.fromiter(list(self._blk_us), float) if self._blk_us else None
            out["block_us_p50"] = round(float(np.percentile(b, 50)), 1) if b is not None else None
            out["block_us_p95"] = round(float(np.percentile(b, 95)), 1) if b is not None else None
        return out


# =====================================================================================================================
# beat votes (pure)
# =====================================================================================================================
def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _local_period(beats: list[float]) -> Optional[float]:
    """Beat This!'s beat period over ``beats`` (ascending, the local span): each beat numbered on from the last by its
    interval in median intervals (a skipped beat counts 2; one closer than half a median is an extra and left out),
    then the Theil-Sen slope of time on number (the median of every pair's): finer than the median interval (Beat
    This!'s 20 ms frames make that 1.5 % coarse at 200 bpm), and a phase step (a jump cut, a re-entry) can't tilt it
    the way it tilts an LS fit. None with fewer than 4 numbered beats."""
    t = np.asarray(beats, float)
    iv = np.diff(t)
    T0 = float(np.median(iv)) if iv.size else 0.0
    if not T0 > 0.0:
        return None
    n, keep = [0.0], [float(t[0])]
    for b in t[1:]:
        step = int(round((float(b) - keep[-1]) / T0))
        if step >= 1:
            n.append(n[-1] + step)
            keep.append(float(b))
    if len(keep) < 4:
        return None
    nn, tt = np.asarray(n), np.asarray(keep)
    i, j = np.triu_indices(nn.size, 1)
    T = float(np.median((tt[j] - tt[i]) / (nn[j] - nn[i])))
    return T if T > 0.0 and math.isfinite(T) else None


def beat_vote(msg: contract.MlBeats, clock: Optional[contract.ClockData], cfg: dict) -> contract.BeatVote:
    """One Beat This! window read against the clock's published line (S4.5 "Beat votes"), where that line holds. Pure:
    ``applied`` is False and ``why`` says why there is no vote (None: a vote); MLLink's apply rule decides the rest.

    The clock's line holds near its anchor (an integer beat near the moment it was published, so near now): its bpm
    is the tracker's local fit plus any slew in force, and the DBN re-anchors it on every beat, so extrapolated back
    over a 30 s window it walks hundreds of ms off the beats (26, 21 Oct: 1.8 % on the quickstep; a 3 % slew while
    gated dmx). So only the window's last ``local_s`` (of the interior, where the beats are) is read, and the clock's
    line is carried back from its anchor at Beat This!'s own local period:
    - **octave**: Beat This!'s period over the local span (``_local_period``: a Theil-Sen fit; LOCAL_MIN_BEATS beats or
      "too few beats") against the clock's: their ratio within ``octave_tol`` of 2, 0.5 or 1;
    - **conf**: the share of the local beats within ``match_ms`` of the octave-mapped clock line: the clock's beats at
      its period / octave (within ``octave_tol``: Beat This!'s fitted period), phase from the clock's line at its
      anchor (for 0.5, its bar anchor's beats: the ones a halving keeps). A consistent x2 window reaches ~1 however
      the clock's slope is off; Beat This! on another phase, or split between tempi, stays low;
    - **at 0.5 either half**: the other half of the clock's beats is read too, and the better counts; on the other
      half ``beat_is_one`` is the beat one after the bar anchor (mod 2 bars) within a bar of the anchor, the one with
      which the clock's halving keeps that half (26's 031: 0.0 on the bar anchor's half, 1.0 on the other);
    - **the one** (octave 1): each local downbeat within ``match_ms`` of a beat of that line votes its number.
    "no grid": no line (bpm 0, no anchor), or a line with its gate none that is searching or free; an ungated line
    in VOTE_UNGATED_STATES is read (the clock ran 031's locked line at 181 ungated on 19 of 26 turns)."""
    out: contract.BeatVote = {"window_t0": float(msg.get("window_t0") or 0.0),
                              "window_t1": float(msg.get("window_t1") or 0.0),
                              "beat_is_one": None, "bar_len": None, "octave": None, "conf": 0.0, "applied": False,
                              "why": None}
    c = dict(BEAT_VOTES_FALLBACK)
    c.update(cfg or {})
    bpm = _num((clock or {}).get("bpm"))
    if (not isinstance(clock, dict) or not bpm or bpm <= 0
            or (clock.get("gate") in (None, "none") and clock.get("state") not in VOTE_UNGATED_STATES)
            or _num(clock.get("anchor_ms")) is None or _num(clock.get("beat_at_anchor")) is None):
        out["why"] = "no grid"
        return out
    match_s = float(c["match_ms"]) / 1000.0
    beats = sorted(b for b in (_num(x) for x in (msg.get("beats") or [])) if b is not None)
    inter = msg.get("interior")
    t_end = _num(inter[1]) if isinstance(inter, (list, tuple)) and len(inter) == 2 else None
    if t_end is None:
        t_end = _num(msg.get("window_t1")) or (beats[-1] if beats else 0.0)
    lo = t_end - float(c["local_s"])
    loc = [b for b in beats if lo <= b <= t_end]
    T_bt = _local_period(loc) if len(loc) >= LOCAL_MIN_BEATS else None
    if T_bt is None:
        out["why"] = "too few beats"
        return out
    r = (60.0 / bpm) / T_bt                         # the clock's period / Beat This!'s
    tol = float(c["octave_tol"])
    octave = next((o for o in (2.0, 0.5, 1.0) if abs(r / o - 1.0) <= tol), None)
    if octave is None:
        out["why"] = "the tempi don't relate"
        return out
    out["octave"] = octave
    t_ref = float(clock["anchor_ms"]) / 1000.0
    k_ref = float(clock["beat_at_anchor"])
    origin = float(int(clock.get("bar_anchor_beat") or 0))

    def line(t: float) -> float:                    # the octave-mapped clock line's beat (float) at t
        return (k_ref - origin) * octave + (t - t_ref) / T_bt

    def hits(half: float) -> int:                   # local beats on the mapped line (half = 0.5: the other half)
        return sum(1 for b in loc if abs((line(b) - half) - round(line(b) - half)) * T_bt <= match_s)

    n_hit = hits(0.0)
    if octave == 0.5:
        n_other = hits(0.5)                         # Beat This!'s beats on the other half of the clock's beats
        if n_other > n_hit:
            n_hit = n_other
            L = int(clock.get("beats_per_bar") or 4)
            res = (int(origin) + 1) % (2 * L)       # that half's bar starts one beat after the bar anchor; the beat
            k0 = int(k_ref) + L                     # of that residue (mod 2 bars) within a bar of the anchor
            out["beat_is_one"] = k0 - (k0 - res) % (2 * L)
    out["conf"] = round(n_hit / len(loc), 3)
    if octave != 1.0:
        return out
    bar_lens = [int(v) for v in (c.get("bar_lens") or []) if isinstance(v, (int, float))]
    w_len = msg.get("bar_len")
    bl = int(w_len) if isinstance(w_len, (int, float)) and int(w_len) in bar_lens else None
    use = bl or int(clock.get("beats_per_bar") or 4)
    votes: list[int] = []
    for d in (_num(x) for x in (msg.get("downbeats") or [])):
        if d is None or not lo <= d <= t_end:
            continue
        x = line(d) + origin                         # (octave 1: the published count)
        k = round(x)
        if abs(x - k) * T_bt <= match_s:
            votes.append(int(k))
    if votes:
        counts = collections.Counter(k % use for k in votes)
        res, n = counts.most_common(1)[0]
        if n / len(votes) >= float(c["one_agree"]):
            out["beat_is_one"] = max(k for k in votes if k % use == res)
    out["bar_len"] = bl
    return out


# =====================================================================================================================
# MLLink (show-brain)
# =====================================================================================================================
def _proc_cpu_s(pid: int) -> Optional[float]:
    """utime + stime of ``pid`` (every thread), in seconds; None when it can't be read."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            raw = f.read()
        fields = raw[raw.rindex(b")") + 2:].split()
        return (int(fields[11]) + int(fields[12])) / float(_CLK_TCK)
    except (OSError, ValueError, IndexError):
        return None


def _t_of(r: Optional[dict]) -> Optional[float]:
    """A result's acoustic time: ``t``, or a beats window's end."""
    if not isinstance(r, dict):
        return None
    return _num(r.get("t")) if "t" in r else _num(r.get("window_t1"))


def _neutral_view(state: str, why: Optional[str], stale_s: Optional[dict] = None) -> contract.MLView:
    return {"state": state, "why": why, "stream": None, "ced": None, "ced_recent": [], "tags": None,
            "emb_recent": [], "key": None, "beats": None, "vote": None,
            "ages": {t: None for t in contract.ML_TASKS}, "hello": None,
            "stale_s": dict(stale_s or {t: RESULTS_STALE_FALLBACK for t in contract.ML_TASKS})}


class MLLink:
    """Component ``ml`` (module docstring). Everything runs on show-brain but ``view`` and ``snapshot``."""

    name = "ml"
    tick_hz = contract.SLOW_TICK_HZ

    def __init__(self, ctx: contract.BrainContext, *, log_path: Optional[pathlib.Path] = None,
                 python: Optional[str] = None, extra_args: tuple = (), mono: Callable[[], float] = time.monotonic,
                 cpu_every_s: float = CPU_EVERY_S,
                 child_pids: Optional[Callable[[], Any]] = None) -> None:
        self.ctx = ctx
        self._child_pids = child_pids                # show_service's other children (the beat process), counted as it
        self._mono = mono
        self.log_path = pathlib.Path(log_path) if log_path else contract.ML_LOG
        self._python = python                        # tests: the interpreter to run (over ml.python)
        self.feed: Optional[MLFeed] = ctx.ml_feed if isinstance(ctx.ml_feed, MLFeed) else None
        feeder = self.feed.feeder if self.feed is not None else FrameFeeder()
        self.sup = WorkerSupervisor({}, log_path=self.log_path, on_line=self._on_line, on_state=self._on_state,
                                    extra_args=tuple(extra_args), clock=mono, feeder=feeder)
        self.ml: dict = {}
        self.problems: list[str] = []
        self.votes_cfg: dict = dict(BEAT_VOTES_FALLBACK)
        self.stale_s = RESULTS_STALE_FALLBACK
        self.stale_periods = STALE_PERIODS_FALLBACK
        self._every: dict[str, float] = {}           # task -> its configured every_s (until a stats line says more)
        self._stale: dict[str, float] = {t: RESULTS_STALE_FALLBACK for t in contract.ML_TASKS}
        self._worker_good: dict = {}                 # the last worker section that made a config frame
        self._sent_worker: Optional[dict] = None
        self.cpu_max_cfg = CPU_MAX_FALLBACK
        self.cpu_floor = CPU_FLOOR_FALLBACK
        self.cpu_limit: Optional[float] = None       # the worker's cpu_max while the total is over 0.8, else None
        self._cpu_hist: collections.deque = collections.deque(maxlen=64)   # (now, service s, worker pid, worker s)
        self._view: contract.MLView = _neutral_view("starting", "not started yet")
        self._ced: collections.deque = collections.deque()
        self._emb: collections.deque = collections.deque()
        self._latest: dict[str, Optional[dict]] = {t: None for t in contract.ML_TASKS}
        self._stream: Optional[str] = None
        self._votes: collections.deque = collections.deque(maxlen=VOTES_KEEP)
        self._run: list[tuple] = []                  # the vote run: (key, window_t1, clock epoch, gated), one key
        self._last_apply: Optional[float] = None
        self.applied = 0
        self.confirmed = 0
        self._started = False
        self._cpu_every = float(cpu_every_s)
        self._cpu_last: Optional[tuple] = None       # (wall, service cpu s, worker pid, worker cpu s)
        self.cpu: dict = {"worker": None, "service": None, "total": None, "children": None}
        self._kids: dict[int, float] = {}            # child pid -> its last cpu s
        self._kids_s = 0.0                           # the children's cpu s summed from their readings (monotonic)
        self._kids_share: Optional[float] = None
        self._kids_last: Optional[tuple] = None      # (now, the children's cpu s) at the last measure
        self._over_since: Optional[float] = None
        self._over_logged = -math.inf
        self._log_checked = -math.inf
        self._tick_ms: collections.deque = collections.deque(maxlen=STAT_N)
        self._line_ms: collections.deque = collections.deque(maxlen=STAT_N)
        self._snap: dict = {}
        self._heavy: dict = {}
        self._heavy_due = -math.inf
        self._build_snapshot()

    # -- Component ---------------------------------------------------------------------------------------------------
    def provides(self) -> dict:
        return {"ml": self.view}

    def view(self) -> contract.MLView:
        """Any thread: the latest MLView (swapped by reference)."""
        return self._view

    def configure(self, settings: dict) -> None:
        """show.yaml ``ml``: the worker's keys (supervisor.WORKER_KEYS) go to it as its config frame; the process keys
        (enabled, python, models_dir, nice, restart) to the supervisor; ``beat_votes``, ``results_stale_s``,
        ``stale_periods`` and ``cpu_floor`` are read here. A value that can't be used takes its starting point and
        adds a ``problems`` line; a worker section that can't make a frame keeps the last good one. Never raises."""
        s = settings if isinstance(settings, dict) else {}
        raw = s.get("ml")
        probs: list[str] = []
        if raw is not None and not isinstance(raw, dict):
            probs.append(f"ml: {raw!r} is not a section: the starting points are used")
            raw = {}
        ml = dict(raw or {})
        if self._python:
            ml["python"] = self._python
        bv = dict(BEAT_VOTES_FALLBACK)
        given = ml.get("beat_votes")
        if isinstance(given, dict):
            for k, v in given.items():
                if k not in bv:
                    continue
                if k == "bar_lens":
                    ok = isinstance(v, list) and v and all(isinstance(x, int) and 2 <= x <= 16 for x in v)
                elif k in ("agree_runs", "bar_agree_runs"):
                    ok = isinstance(v, int) and not isinstance(v, bool) and v >= 1
                elif k in ("local_s", "run_max_s"):
                    ok = _num(v) is not None and float(v) > 0
                else:
                    ok = _num(v) is not None and float(v) >= 0
                if ok:
                    bv[k] = v
                else:
                    probs.append(f"ml.beat_votes.{k}: {v!r} is not usable: {BEAT_VOTES_FALLBACK[k]!r} used")
        elif given is not None:
            probs.append(f"ml.beat_votes: {given!r} is not a section: the starting points are used")
        st = self._positive(ml, "results_stale_s", RESULTS_STALE_FALLBACK, probs)
        sp = self._positive(ml, "stale_periods", STALE_PERIODS_FALLBACK, probs)
        floor = _num(ml.get("cpu_floor"))
        if ml.get("cpu_floor") is not None and (floor is None or not 0.0 < floor <= 1.0):
            probs.append(f"ml.cpu_floor: {ml.get('cpu_floor')!r} is not 0..1: {CPU_FLOOR_FALLBACK:g} used")
            floor = None
        _proc, pprobs = resolve_checked(ml)
        probs += pprobs
        worker = worker_section(ml)
        bad = check_frame(worker)
        if bad is not None:
            probs.append(f"ml: the worker's settings can't be sent ({bad}): its last good ones stay")
            worker = dict(self._worker_good)
        else:
            self._worker_good = dict(worker)
        cm = _num(worker.get("cpu_max"))
        self.cpu_max_cfg = cm if cm is not None and cm > 0 else CPU_MAX_FALLBACK
        self.cpu_floor = floor if floor is not None else CPU_FLOOR_FALLBACK
        tasks = worker.get("tasks") if isinstance(worker.get("tasks"), dict) else {}
        self._every = {}
        for t in contract.ML_TASKS:
            e = _num(tasks[t].get("every_s")) if isinstance(tasks.get(t), dict) else None
            if e is not None and e > 0:
                self._every[t] = e
        self.votes_cfg, self.stale_s, self.stale_periods, self.problems = bv, st, sp, probs
        self._set_stale(None)
        frame = self._frame_worker(worker)
        if ml != self.ml or frame != self._sent_worker:
            self.ml, self._sent_worker = ml, frame
            self.sup.configure(ml, worker=frame)
        self._build_snapshot()

    @staticmethod
    def _positive(ml: dict, key: str, fallback: float, probs: list[str]) -> float:
        raw = ml.get(key)
        x = _num(raw)
        if raw is not None and (x is None or x <= 0):
            probs.append(f"ml.{key}: {raw!r} is not a positive number: {fallback:g} used")
            x = None
        return x if x is not None else fallback

    def _frame_worker(self, worker: dict) -> dict:
        """The worker section as sent: with the effective cpu_max while show_service's share pushes the total over
        the owner's 0.8 (``_cpu_limit``)."""
        if self.cpu_limit is None:
            return dict(worker)
        return {**worker, "cpu_max": round(self.cpu_limit, 3)}

    def _set_stale(self, stats: Optional[dict]) -> None:
        """MLView.stale_s: per task, max(results_stale_s, stale_periods x its interval now): the last stats line's
        every_s (the worker's CPU stretch and over-budget doubling are in it), else the configured one."""
        tasks = stats.get("tasks") if isinstance(stats, dict) and isinstance(stats.get("tasks"), dict) else {}
        out = {}
        for t in contract.ML_TASKS:
            e = _num((tasks.get(t) or {}).get("every_s")) if isinstance(tasks.get(t), dict) else None
            e = e if e is not None and e > 0 else self._every.get(t)
            out[t] = round(max(self.stale_s, self.stale_periods * e) if e else self.stale_s, 2)
        if out != self._stale:
            self._stale = out
            self._view = {**self._view, "stale_s": dict(out)}                     # type: ignore[typeddict-item]

    def restore(self, saved: dict) -> None:
        """Nothing: the worker starts cold."""

    def start(self) -> None:
        if not self._started:
            self._started = True
            self.sup.start()

    def persist(self) -> Optional[dict]:
        return None

    def stop(self) -> Any:
        """The supervisor's stop sequence (a coroutine: brain.py awaits it), then the feed is detached."""
        self._started = False
        return self.sup.stop()

    def snapshot(self) -> dict:
        return self._snap

    def tick(self, now_s: float) -> None:
        t0 = time.perf_counter()
        v = self._view
        ages = self._ages(now_s)
        if ages != v.get("ages"):
            self._view = {**v, "ages": ages}                                     # type: ignore[typeddict-item]
        if now_s - (self._cpu_last[0] if self._cpu_last else -math.inf) >= self._cpu_every:
            self._measure_cpu(now_s)
        if now_s - self._log_checked >= LOG_CHECK_S:
            self._log_checked = now_s
            self._rotate_log()
        self._build_snapshot()
        self._tick_ms.append((time.perf_counter() - t0) * 1000.0)

    # -- the supervisor's callbacks (show-brain) ---------------------------------------------------------------------
    def _on_state(self, state: str, why: Optional[str]) -> None:
        if state != "running":
            for t in contract.ML_TASKS:
                self._latest[t] = None
            self._ced.clear()
            self._emb.clear()
            self._view = _neutral_view(state if state in contract.ML_STATES else "failed", why, self._stale)
        else:
            self._view = {**self._view, "state": "running", "why": None,               # type: ignore[typeddict-item]
                          "hello": self.sup.hello}
        log.info("ML worker: %s%s", state, f" ({why})" if why else "")
        self._build_snapshot(heavy=True)

    def _on_line(self, obj: dict) -> None:
        t0 = time.perf_counter()
        kind = obj.get("type")
        v = dict(self._view)
        if kind == contract.ML_HELLO:
            v["hello"] = obj
            log.info("ML worker up: pid %s, models %s, tasks %s, nice %s", obj.get("pid"),
                     ", ".join(f"{k} {s}" for k, s in sorted((obj.get("models") or {}).items())),
                     ", ".join(k for k, on in (obj.get("tasks") or {}).items() if on) or "none", obj.get("nice"))
        elif kind in (contract.ML_CED, contract.ML_TAGS, contract.ML_KEY, contract.ML_BEATS):
            stream = obj.get("stream")
            if stream != self._stream:
                self._stream = stream
                self._ced.clear()
                self._emb.clear()
                for t in contract.ML_TASKS:
                    self._latest[t] = None
                v["ced"] = v["tags"] = v["key"] = v["beats"] = v["vote"] = None
                v["ced_recent"], v["emb_recent"] = [], []
            v["stream"] = stream
            self._latest[kind] = obj
            t = _t_of(obj)
            if kind == contract.ML_CED:
                v["ced"] = obj
                self._ced.append(obj)
                self._trim(self._ced, t, lambda r: r["t"])
                v["ced_recent"] = list(self._ced)
            elif kind == contract.ML_TAGS:
                v["tags"] = obj
                try:
                    emb = decode_emb(obj["emb"], int(obj.get("emb_dim") or contract.EMB_DIM))
                except (ValueError, TypeError, KeyError):
                    emb = None
                if emb is not None:
                    self._emb.append((int(obj.get("emb_id") or 0), float(obj["t"]), emb))
                    self._trim(self._emb, t, lambda r: r[1])
                    v["emb_recent"] = list(self._emb)
            elif kind == contract.ML_KEY:
                v["key"] = obj
            else:
                v["beats"] = obj
                v["vote"] = self._vote(obj)
            v["ages"] = self._ages(self._mono())
        elif kind == contract.ML_ERROR:
            log.debug("ML worker error: %s: %s", obj.get("task"), obj.get("error"))
            return
        else:                                                   # stats: the supervisor keeps the last one; the
            self._set_stale(obj)                                # readers' freshness follows its intervals
            return
        self._view = v                                          # type: ignore[assignment]
        self._line_ms.append((time.perf_counter() - t0) * 1000.0)

    def _ages(self, now_s: float) -> dict:
        out: dict = {}
        for k, r in self._latest.items():
            t = _t_of(r)
            out[k] = None if t is None else round(now_s - t, 2)
        return out

    @staticmethod
    def _trim(dq: collections.deque, t_new: Optional[float], key: Callable) -> None:
        if t_new is None:
            return
        while dq and t_new - float(key(dq[0])) > RECENT_S:
            dq.popleft()

    # -- beat votes ----------------------------------------------------------------------------------------------------
    def _vote(self, msg: dict) -> contract.BeatVote:
        clock = None
        if self.ctx.clock is not None:
            try:
                clock = self.ctx.clock()
            except Exception:
                clock = None
        vote = beat_vote(msg, clock, self.votes_cfg)            # type: ignore[arg-type]
        try:
            self._apply(vote, clock)
        except Exception:
            log.exception("ML beat vote: apply failed")
        self._votes.append(dict(vote))
        return vote

    def _apply(self, vote: contract.BeatVote, clock: Optional[dict]) -> None:
        """S4.5 "Apply": conf, agree_runs votes of one key, differs from the clock, cooldown. Only the parts that
        differ; an octave alone. A new bar length (the meter: it moves every bar line, phrase and drop line) needs a
        one and bar_agree_runs votes (§5.3: stronger evidence). An agreeing vote is sent as a confirmation
        (bar_conf), at most every cooldown_s.

        **The run** (21 Oct, 3a): the votes of one key, each kept with its window's end and the clock's epoch. A turn
        that is no vote ("no grid", "too few beats", "the tempi don't relate"; a silent window writes no line at all)
        neither adds to it nor breaks it: through the room's mic Beat This! is sparse, and on 26's full-song
        lookatmenow four x0.5 votes at conf 0.64-1.0 never came two turns in a row. A vote that breaks it: another
        key, or one under min_conf ("low agreement": Beat This!'s beats off the line it would set). Stale evidence
        can't combine: a vote whose window ended more than ``run_max_s`` before the new one's falls out, and a new
        epoch (a new grid: the clock's numbering and line are another's) clears it.

        **An octave needs a gated vote** (21 Oct, 3a): each entry keeps whether the clock's gate was beat or dmx when
        it was judged, and an octave applies only when one of the run's is (else "waiting for a gated vote"; the run
        is kept). On 26's full-song set a wrong halving (044, 148 -> 74) reached the clock through two ungated turns
        in a row, its tempo plausible to the clock's prior; the right ones (lookatmenow, 031) each had a gated vote in
        their run. Octave 1 (the one, the bar length, a confirmation) is unchanged."""
        c = self.votes_cfg
        if vote["why"] is not None or vote["octave"] is None:
            return                                            # no vote: the run is kept as it is
        bpb = int((clock or {}).get("beats_per_bar") or 4)
        anchor = int((clock or {}).get("bar_anchor_beat") or 0)
        L = vote["bar_len"] or bpb
        one = vote["beat_is_one"]
        if vote["octave"] == 0.5:                             # which half of the clock's beats a halving keeps
            key = (0.5, None, (anchor if one is None else one) % 2)
        else:
            key = (vote["octave"], vote["bar_len"], None if one is None else one % L)
        t_w, epoch = float(vote["window_t1"]), (clock or {}).get("epoch")
        gated = (clock or {}).get("gate") in ("beat", "dmx")
        run_max = float(c["run_max_s"])
        self._run = [e for e in self._run if e[2] == epoch and 0.0 <= t_w - e[1] <= run_max]
        if vote["conf"] < float(c["min_conf"]):
            vote["why"] = "low agreement"
            self._run = []
            return
        agree, bar_agree = int(c["agree_runs"]), max(int(c["agree_runs"]), int(c["bar_agree_runs"]))
        if self._run and self._run[-1][0] != key:
            self._run = []                                    # a contrary vote
        self._run = (self._run + [(key, t_w, epoch, gated)])[-bar_agree:]
        octave = vote["octave"]
        meter = octave == 1.0 and vote["bar_len"] is not None and vote["bar_len"] != bpb
        need = bar_agree if (meter and one is not None) else agree
        if len(self._run) < need:
            vote["why"] = "waiting for a second run" if need <= 2 else f"waiting for {need} runs (a new bar length)"
            return
        if octave != 1.0 and not any(e[3] for e in self._run):
            vote["why"] = "waiting for a gated vote"          # (the run is kept: a gated one of this key completes it)
            return
        now = self._mono()
        cooling = self._last_apply is not None and now - self._last_apply < float(c["cooldown_s"])
        no_one = False
        if octave != 1.0:                                     # (the clock judges it on the room's audio at its
            args = {"octave": octave, "add_min": float(c["octave_add_min"]),       # switch beat, these thresholds:
                    "drop_max": float(c["octave_drop_max"]),                       # README S4.15 "Octave evidence")
                    "prior_oct": float(c["octave_prior_oct"])}
            if one is not None:                               # a halving onto the other half of the beats: the
                args["beat_is_one"] = one                     # one that picks it (beat_vote)
        else:
            args = {}
            if meter:
                if one is None:
                    no_one = True                             # no downbeat evidence: the meter stays
                else:
                    args["bar_len"] = vote["bar_len"]
                    args["beat_is_one"] = one                 # a new bar length keeps the one it was voted with
            if one is not None and not no_one and (one - anchor) % L != 0:
                args["beat_is_one"] = one
        fn = self.ctx.clock_correct
        if not args:
            vote["why"] = "a new bar length needs a one" if no_one else "the clock agrees"
            if one is not None and not cooling and fn is not None and not meter:
                fn(beat_is_one=one, bar_len=L, octave=1.0)    # a confirmation: the clock's bar_conf rises
                self._last_apply = now
                self.confirmed += 1
                vote["applied"] = True
            return
        if cooling:
            vote["why"] = "cooldown"
            return
        if fn is None:
            vote["why"] = "no clock here"
            return
        fn(**args)
        self._last_apply = now
        self._run = []
        self.applied += 1
        vote["applied"] = True
        log.info("ML beat vote applied: %s (conf %.2f)", args, vote["conf"])

    # -- CPU and the log ---------------------------------------------------------------------------------------------
    def _children_cpu_s(self) -> float:
        """The other children's cpu seconds since we first read each (a new or respawned pid starts at its first
        reading), summed so it only grows; a pid that's gone keeps what it had."""
        try:
            pids = [p for p in (self._child_pids() or ()) if isinstance(p, int) and p > 0]
        except Exception:                            # the clock mid-restart: this reading counts none
            pids = []
        seen: dict[int, float] = {}
        for p in pids:
            c = _proc_cpu_s(p)
            if c is None:
                continue
            last = self._kids.get(p)
            if last is not None and c >= last:
                self._kids_s += c - last
            seen[p] = c
        self._kids = seen
        return self._kids_s

    def _measure_cpu(self, now_s: float) -> None:
        svc = _proc_cpu_s(os.getpid())
        kids_s = self._children_cpu_s() if self._child_pids is not None else 0.0
        kids_last, self._kids_last = self._kids_last, (now_s, kids_s)
        if svc is not None:
            svc += kids_s
        pid = self.sup.pid
        wk = _proc_cpu_s(pid) if pid else None
        last = self._cpu_last
        self._cpu_last = (now_s, svc, pid, wk)
        if svc is not None:
            self._cpu_hist.append((now_s, svc, pid, wk))
        if last is None or svc is None or last[1] is None:
            return
        dt = now_s - last[0]
        if dt <= 0:
            return
        service = max(0.0, (svc - last[1]) / dt)
        worker = (max(0.0, (wk - last[3]) / dt) if wk is not None and last[3] is not None and pid == last[2]
                  else (0.0 if pid is None else None))
        total = service + (worker or 0.0)
        if self._child_pids is not None and kids_last is not None and now_s > kids_last[0]:
            self._kids_share = max(0.0, (kids_s - kids_last[1]) / (now_s - kids_last[0]))
        self.cpu = {"worker": None if worker is None else round(worker, 3), "service": round(service, 3),
                    "total": round(total, 3),
                    "children": None if self._kids_share is None else round(self._kids_share, 3)}
        if total > TOTAL_WARN:
            self._over_since = self._over_since if self._over_since is not None else now_s
            if now_s - self._over_since >= TOTAL_WARN_FOR_S and now_s - self._over_logged >= TOTAL_WARN_LOG_S:
                self._over_logged = now_s
                log.warning("show_service + the ML worker use %.2f of a core (over %.1f for %.0f s): service %.2f, "
                            "worker %s; the worker's cpu_max is cut to what show_service leaves", total, TOTAL_WARN,
                            now_s - self._over_since, service, self.cpu["worker"])
        else:
            self._over_since = None
        self._cpu_limit(now_s)

    def _shares_60s(self, now_s: float) -> Optional[tuple[float, float]]:
        """(show_service's share, the total) over the last TOTAL_WARN_FOR_S or more, the worker's from one process."""
        if not self._cpu_hist:
            return None
        t1, s1, pid, w1 = self._cpu_hist[-1]
        old = [h for h in self._cpu_hist if h[0] <= t1 - TOTAL_WARN_FOR_S + 1e-6]
        if not old:
            return None
        t0, s0, pid0, w0 = old[-1]
        if t1 <= t0:
            return None
        service = max(0.0, (s1 - s0) / (t1 - t0))
        if pid is None:
            worker = 0.0
        elif pid0 == pid and w0 is not None and w1 is not None:
            worker = max(0.0, (w1 - w0) / (t1 - t0))
        else:
            return None                                  # a new worker: wait for 60 s of it
        return service, service + worker

    def _cpu_limit(self, now_s: float) -> None:
        """The owner's 0.8 enforced, by degrading: while show_service and the worker together are over TOTAL_WARN
        for TOTAL_WARN_FOR_S, the worker's cpu_max becomes TOTAL_WARN less show_service's 60 s share, within
        [ml.cpu_floor, ml.cpu_max] (its own guard then stretches its intervals); it goes back once show_service
        leaves the configured cpu_max room again."""
        sh = self._shares_60s(now_s)
        if sh is None:
            return
        service, total = sh
        want = min(self.cpu_max_cfg, max(self.cpu_floor, TOTAL_WARN - service))
        cur = self.cpu_limit
        if cur is None:
            if total <= TOTAL_WARN or want >= self.cpu_max_cfg - 1e-9:
                return
            new: Optional[float] = want
        elif want >= self.cpu_max_cfg - 1e-9:
            new = None
        elif abs(want - cur) >= CPU_STEP:
            new = want
        else:
            return
        self.cpu_limit = new
        log.info("ML worker: cpu_max %s (show_service %.2f of a core over 60 s, total %.2f)",
                 f"{new:.2f} while the total is over {TOTAL_WARN}" if new is not None
                 else f"back to {self.cpu_max_cfg:g}", service, total)
        frame = self._frame_worker(self._worker_good)
        self._sent_worker = frame
        self.sup.send_config(frame)

    def _rotate_log(self) -> None:
        try:
            if self.log_path.stat().st_size > LOG_ROTATE_BYTES:
                shutil.copyfile(self.log_path, str(self.log_path) + ".1")
                os.truncate(self.log_path, 0)            # the worker's stderr is O_APPEND: it carries on at 0
        except OSError:
            pass

    # -- snapshot ------------------------------------------------------------------------------------------------------
    def _build_snapshot(self, heavy: bool = False) -> None:
        """S4.5's snapshot keys. The log tail and the cost percentiles are refreshed every HEAVY_EVERY_S (or on a
        state change); the counters every call (1 Hz and on each state change)."""
        sup, now = self.sup, self._mono()
        if heavy or now >= self._heavy_due:
            self._heavy_due = now + HEAVY_EVERY_S

            def p95(dq: collections.deque) -> Optional[float]:
                return round(float(np.percentile(np.fromiter(list(dq), float), 95)), 3) if dq else None
            self._heavy = {"log_tail": sup.log_tail(), "stats_ms": {"tick_ms_p95": p95(self._tick_ms),
                                                                   "line_ms_p95": p95(self._line_ms)},
                           "feed_costs": ({k: v for k, v in self.feed.stats().items() if k.startswith("block_us")}
                                          if self.feed is not None else {})}
        s = {"state": sup.state, "why": sup.why, "pid": sup.pid, "since_s": round(now - sup.since, 1),
             "restarts": sup.restarts, "failures_in_row": sup.failures_in_row,
             "next_start_in_s": (round(max(0.0, sup.next_start_at - now), 1)
                                 if sup.next_start_at is not None and sup.state in ("backoff", "failed") else None),
             "last_exit": sup.last_exit, "log_tail": self._heavy["log_tail"], "hello": sup.hello,
             "stats": sup.last_stats, "lines": dict(sup.lines), "bad_lines": sup.bad_lines}
        s["feed"] = ({**self.feed.stats(costs=False), **self._heavy["feed_costs"]} if self.feed is not None
                     else {**sup.feeder.stats(), "attached": False})
        s["votes"] = list(self._votes)
        s["applied"], s["confirmed"] = self.applied, self.confirmed
        s["cpu"] = dict(self.cpu)
        s["cpu_limit"] = {"cpu_max": self.cpu_max_cfg, "now": self.cpu_limit if self.cpu_limit is not None
                          else self.cpu_max_cfg, "cut": self.cpu_limit is not None, "floor": self.cpu_floor}
        s["over_0_8_for_s"] = None
        if self._over_since is not None and self._cpu_last is not None:
            s["over_0_8_for_s"] = round(self._cpu_last[0] - self._over_since, 1)
        s["problems"] = list(self.problems)
        s["stream"] = self._view.get("stream")
        s["ages"] = self._view.get("ages")
        s["stats_ms"] = self._heavy["stats_ms"]
        s["stale_s"] = dict(self._stale)
        s["config_errors"], s["run_errors"] = sup.config_errors, sup.run_errors
        s["rss_mb"] = (sup.last_stats or {}).get("rss_mb")
        self._snap = s


def attach(ctx: contract.BrainContext, **kw: Any) -> MLLink:
    """The brain's ``ml``. __main__ passes ``log_path`` (<state_dir>/ml-worker.log) and ``child_pids`` (the beat
    process's pid, counted in show_service's share); tests pass ``python``,
    ``extra_args`` (``("--fake",)``), ``mono`` and ``cpu_every_s``."""
    return MLLink(ctx, **kw)
