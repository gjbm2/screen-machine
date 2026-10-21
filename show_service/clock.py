"""The beat clock (SHOW_ENGINE §5.3): tempo, beat phase, bar and phrase, shared Ableton Link-style as ``music.clock``.

We share a clock, not triggers. A consumer computes ``beat(t) = beat_at_anchor + (t_ms - anchor_ms) / 1000 * bpm / 60``
at its own ``now + lead`` (``beat_at``, ``next_beat`` and ``position_at`` below do exactly that from a published body).

Thread and wiring
-----------------
Clock state lives on ``show-rx``. ``process`` is a ``contract.OnAudio``. ``__main__`` fans the active stream's blocks
out to ``features.process`` first, then ``clock.process`` (the same block, untouched), and calls
``clock.tick(now_s)`` from ``on_tick``. ``observe_hit`` (optional) takes dsp's low-band hits for phase refinement:
``__main__`` wraps the publish it gives Features, so that ``music.hit`` bodies also reach
``clock.observe_hit(t_ms / 1000, band, strength)``.
Methods marked *any thread* only append to a deque, which show-rx drains at its next ``process`` or ``tick``.

**show-beat** (README S4.6). The activation (madmom's 2 LSTMs, 1-6 ms a frame) can run off show-rx, two ways, behind
one transport face (``send_hop``, ``send_reset``, ``poll``, ``out``), so the hop and drain paths are one:
- ``Clock(beat_proc=True)`` (``SHOW_BEAT_PROC=on``; beatproc.py): in a child process, so a madmom frame takes neither
  show-rx's time nor the GIL. It wins over ``beat_thread``; it is for madmom (and beatproc's test activation) only.
- ``Clock(beat_thread=True)`` (``SHOW_BEAT_THREAD=on``): on the show-beat daemon thread. The integration measured it at
  0.17-0.19 of a core in the real process (S4.12: every BLAS call hands the GIL over and back).
- Neither (the default, as config.Options) runs it inline on show-rx, exactly as in Stage 3.
How the hand-off works:
- show-rx keeps the AGC, soxr and hop assembly. For each full hop it computes the frame's time (the sound's, from the
  block's ``t0_host_s``/``n0``, as inline) and sends ``(epoch, seq, hop, time, send time)``: threaded a
  ``queue.SimpleQueue`` put (never blocks), to the process one non-blocking 1796 B pipe write. Then it returns.
- The other side runs ``act.process_hop`` and hands back ``(epoch, seq, a, time, send time)`` (an activation error
  comes back as ``a = None`` and is counted).
- show-rx drains them (never waiting) at the start of every ``process`` and ``tick`` and runs peaks, tempogram and
  estimator on each ``a``, in ``seq`` order. So clock state never leaves show-rx (it only reads the activation's
  constant ``name``, ``lag_s``, ``floor``). Silent frames (a quiet room, a hole) wait behind the hops still in
  flight, so every frame reaches the peaks and the tempogram in time order.
- **Resets.** A new segment (``_lane_reset``) bumps the epoch and sends a reset: the other side resets the activation
  when it gets there (to the process the reset rides the next hop), and results of an older epoch are dropped
  (``beat_stale``; at most the hops in flight, about one).
- **Back-pressure.** At most BEAT_Q_MAX hops (0.5 s) may be in flight (sent by show-rx and not yet drained by it; both
  counters are show-rx's). Beyond that, or when the process's pipe is full (``beat_pipe_full``), a hop is dropped
  (``beat_drops``) and a reset goes with the next one that is sent, so madmom never sees a splice; the reset comes
  back in order and resets the peak picker there. The tempogram's gap fill covers the missing frames by their times.
- **The process's lifecycle** (degrade, never refuse). The parent builds its own activation either way: it is the
  fallback. Until the child's HELLO (about 1.5 s after the spawn) hops run inline (``beat_inline_hops``); at hello
  the next hop switches over (epoch + 1, a reset rides it, the peaks start over: ``beat_proc_switches``). A child
  lost (EPIPE, EOF: ``beat_proc_lost``) or stalled (hops in flight and no record for BEAT_STALL_S: ``beat_stalls``;
  it is killed) puts show-rx back inline at once, from a clean start; the supervisor respawns it with a backoff and
  the next hello switches back. Each switch is one LSTM restart (1-2 s of weaker activation); the grid holds.
- **Degrade.** If show-beat ever dies, or after ``stop()`` once it (or the process) has exited, show-rx runs the
  activation inline for good. The frames, and so every count and the published line, are the same either way; off
  show-rx, a frame's results are published at the next ``process`` or ``tick`` (a few ms later).
- ``stop()`` (any thread, idempotent; after show-rx stops, as Show.stop) ends show-beat or the process (EOF, then
  SIGTERM, SIGKILL; reaped). ``wait_beat()`` blocks until the other side has caught up and ``wait_beat_ready()``
  until the process has said hello: offline runs and tests only (never on show-rx live). ``beat_pid()`` is the
  child's pid (for CPU accounting).

Blocks and holes (contract.OnAudio: a block may be up to COALESCE_MAX_FRAMES contiguous frames):
- Any block length gives the frames (within float noise) that the same audio gives in 128-frame packets: the AGC
  steps per AGC_CHUNK frames aligned on ``n``, soxr is chunk-invariant, and each hop takes its time from its block's
  ``t0``/``n0``. A RESUME block's peak suppression spans at most RESUME_MAX_FRAMES (one VBAN packet), so a longer
  block doesn't stretch it. ``cost_ms_per_10ms`` is per 10 ms of real audio (holes left out); ``block_ms`` is per call.
- An AUDIO_GAP block (zero-fill; its pcm may be a read-only view, never written here) of a hop or less goes through
  the lane as zeros, as before (madmom's state runs on). A longer one computes nothing: soxr is cleared, the lane
  moves on by the hole (a fractional carry in its origin keeps every later time exact), each hop it completes is a
  silent frame (no activation: ``gap_hops_skipped``), and the next hop that reaches the activation resets it first
  (a splice; to a thread or the process the reset rides that hop). Never a segment, a lane reset or an n0 jump.
- Rollbacks (both on by default): ``Clock(gap_skip=False)`` zero-fills every hole through soxr and the activation
  (as before), ``Clock(agc_chunks=False)`` steps the AGC once per block and gains that block with the new gain (as
  before; then a coalesced block no longer gives the 128-frame packets' gains exactly).

Estimators (``Clock(estimator=...)``; config ``SHOW_CLOCK_ESTIMATOR``; README S4.15)
------------------------------------------------------------------------------------
- ``"dbn"`` (the default since 19 Oct): beatdbn.DbnTracker (madmom's online DBN rebuilt in numpy, our line, a fitted
  conf: 26's measured candidate best2) takes every frame after the activation, in place of steps 3-6 below. The front
  end is untouched: steps 1-2, the quiet gate, the activation inline, on show-beat or in the beat process, holes and
  segments. Each frame goes to the tracker with two flags from the path it came by: ``silent`` (no activation: a
  quiet room, a skipped hole, an activation error) and ``fresh`` (the first activation after the LSTM was reset:
  show-rx's own reset inline, or the reset marker that came back ahead of it from show-beat or the child, whose
  REC_RESET_DONE record precedes the hop's). conf, state, gate and stability are the tracker's. **The published line**
  (bpm, anchor, beat count) and ``epoch`` are Clock's, on the tracker's line with step 6's discipline
  (``_dbn_publish``; README S4.15 "Publishing"): the tracker's own line re-anchors on every DBN beat, the published
  one slews to it (gated beat: half the phase error per beat, at most 10 % tempo; dmx, none: all of it per beat;
  none also snaps forward; the slew is judged again at each of the line's beats that passes with no DBN beat, as
  legacy's after every judged beat), its count never goes back at a consumer's now and never jumps a whole beat
  within an epoch, and ``epoch`` + 1 only for a new grid (the first line, an octave or a tap, a tempo jump over 8 %
  that holds 3 line updates on a grid that was gated, a whole-beat step, or the first beat or dmx gate after a
  quiet room: a new song); the tracker's beat k is published beat ``k + _off``. ``beats_per_bar``, ``bar_anchor_beat``, ``phrase_bars``, ``bar_conf`` and ``phrase_conf`` come from
  the bar and phrase code below (confirmation, ``_bar_phrase``, ``_apply_pending``), fed the tracker's judged beats
  (``DbnTracker.events``) as the legacy estimator feeds it its own; once started the bars keep their anchor (a
  tempo jump drops bar_conf to 0.1). **conf there is P(the next beat lands within 70 ms)**, the gate beat
  0.80/0.60, dmx 0.60/0.35 (2-bar dwell). Corrections: a tap
  re-phases the line (a run of 4+ even taps also sets its tempo), the DBN re-seeded on it and its disagreement held
  off for beatdbn.HOLD_S; one, the bar length and the slow lane's one go to the bar code as before.
  **The octave map** (21 Oct): half/double and the slow lane's octave never touch the tracker. They set the
  publisher's factor (x2 or x0.5 of what it was: 1/4..4) at the upcoming beat (the bar line once bar_conf >= 0.5, and
  a beat of both grids), which keeps its time and number; from there the published line is the tracker's line at
  factor x its tempo, while the DBN tracks on at its own octave underneath (conf and gate stay its own). Tracker beat k
  is mapped beat ``_om_m + (k - _om_k) x factor``, published ``+ _off``: at 2 each tracker beat is two published
  beats (the one between interpolated on the line, ``interp`` to the bar code), at 0.5 every other one. The slew, the
  forward-only count, the epochs and the bars (anchor moved once at the switch, then kept) all run on the mapped line.
  The map holds until a new grid (``_om_clear``): the first gate after a quiet room, a confirmed jump of the mapped
  tempo over 8 % (when the tracker's own tempo is then the one followed, the tracker came to the mapped octave
  itself: the line slews on as its own, no new grid), a run of taps (their tempo is the human's octave), a mapped
  tempo out of DBN_OCT_MIN_BPM..DBN_OCT_MAX_BPM (45-200 bpm with 8 % slack, also the switch's range), or a contrary
  correction (factor back to 1). It replaced (21 Oct) the tracker's octave hold, after which the DBN took its own
  octave back ~6 s later. No audio for
  NO_AUDIO_S: ``free``, gate ``none``, the line's beats pass as misses on tick time. ``stats()["estimator"]``,
  ``stats()["source"]`` (``madmom-dbn2``), ``stats()["dbn"]`` (the tracker's counts; its ``epoch`` counts its own
  re-phases) and the publisher's ``pub_grids``, ``pub_slews``, ``pub_snaps``, ``pub_far_waits``, ``pub_resyncs``,
  ``pub_cold_grids``, ``oct_factor``, ``oct_age_s`` (since the factor was set; None at 1), ``oct_maps_applied``,
  ``oct_maps_cleared`` and ``oct_clear_<why>`` (contrary, joined, jump, range, cold, tap); ``stats()["epoch"]`` is the
  published one.
- ``"legacy"``: the estimator below (steps 3-6), exactly as before: the rollback.

Pipeline (steps 3-6 and their conf, gate and corrections: the legacy estimator)
--------------------------------------------------------------------------------
1. **Rhythm lane:** mono mix, then a slow, bounded AGC (aim -12 dBFS RMS, up tau 10 s, down 3 s, -20..+36 dB, frozen
   below -65 dBFS; madmom needs mastered-music level, see AGC_TARGET_DB), then soxr to 44.1 kHz. soxr's output is
   delay-compensated: 44.1 k sample m is input time m/44100. Time comes from each block's ``t0_host_s`` and ``n0``,
   so every frame carries the acoustic time of its sound.
2. **Activation** (the ``Activation`` protocol): ``process_hop`` takes 441 samples and returns one value >= 0, at
   100 fps.
   - ``MadmomActivation`` (madmom's online beat LSTM, git main; 2 of its 8 nets by default) is preferred.
   - ``AubioActivation`` (aubio-ledfx energy-rise + specflux novelty) is the fallback.
   - ``lag_s`` is each source's peak lag, calibrated on kicks and clicks at four tempos. Live, it is refined from
     ``observe_hit``.
3. **Peaks:** local maxima over +-30 ms, above max(source floor, 30 % of the recent peak level, 1.5 x the local
   mean), with parabolic sub-frame timing.
4. **Tempogram:** a real-time PLP-style Fourier tempogram (Meier, Chiu & Mueller 2024) over the last 6 s.
   - Its input is the activation above the peak threshold (noise and pads carry no rhythm).
   - It is a sliding DFT with PLP's causal half-Hann window, 45-200 bpm at 1 bpm (computed to 240 so the 8th-note
     rate never peaks at the edge), O(bins) per frame.
   - It gives tempo candidates and a clarity (stability) in 0..1.
   - A cold seed takes its best candidate (x the tempo prior: 120 bpm +-1 octave cold, the last 30-60 s +-40 %
     once locked), then sharpens it with a comb search and an LS fit on the peaks of the last 4 s.
   - **The octave from the kicks** (until the slow lane, Beat This!, exists; dsp's sub/bass hits via
     ``observe_hit``, a sub and a bass hit within 60 ms counting once). A fitted seed above FAST_BPM (160) whose
     kicks of the last 6 s land on alternate beats only (on < 70 % of its beats, between the first and the last
     that has one) is seeded at the half, through the kicks (2T, no comb: madmom can peak as high on off-beat
     hats). With no kick evidence (< 4 on-beat kicks) a seed is never above SEED_CAP_BPM (180): the half, its
     phase by the comb. Hip-hop at 90-100 bpm with 8th-note hats no longer locks at 180-200.
5. **Estimator:** an alpha-beta PLL (alpha 0.2, beta = alpha^2/(2-alpha); 0.5 for the first hits after a seed) on
   the peaks. Each beat is judged once its whole cycle (+-T/2) is heard.
   - Only peaks within +-20 % of a period are observations. Three outliers in a row that agree re-lock the phase.
   - The period is a least-squares fit over the last 32/16/8/5/3 hits: the longest window that still fits. Three
     same-signed innovations over 4 % mark a tempo change and restart the fit.
   - Octave switches need 8 beats of tempogram evidence (x2 or /2 scoring 1.3x better under a 120 bpm metrical
     weight). A clean half-time pattern (kick on 1 and 3 only) counts as a full hit record, so it neither flaps nor
     re-seeds. Above 160 bpm the kicks vote too: alternate beats only -> halve (8 beats running, at a bar line,
     onto the kicks' parity: the count goes on); a doubling to above 160 needs the kicks not to say "alternate",
     and to above 180 needs them on (nearly) every beat.
   - Breakdowns hold the grid indefinitely, and only the confidence decays. Peaks under 40 % of the level heard while
     locked can't move a held grid. Two tight hits re-lock it and restore its confidence, keeping the beat count and
     phrase. From 8 s after the last locked hit (LEVEL_GRACE_S: past madmom's ghost beats once the drums stop),
     that level and the peak picker's floor fade (tau 20 s) while not locked; the faded level applies to a held
     grid only while the tempogram finds our own tempo clear (>= 0.3), so the same pulse back quieter is taken and
     pads (no pulse) are not.
   - A cold search (re-seed; ``cold_searches``) starts when the music moved on, either way:
     - hits stop (<= 1 in 8 beats) *and* >= 4 strong peaks (half the current peak level) in 4 s pulse at a tempo
       the tempogram finds clearer than ours (``cold_hits``);
     - another tempo (not within 4 % of ours, its double or its half) has clarity >= 0.30 and >= 2x ours for 4 s,
       the same candidate (within 4 %) all along, with rhythm present (>= 4 peaks in 4 s) and conf below 0.45,
       firing below 0.35 (``cold_alt``). A new song near 3:4 or 4:3 of the old tempo keeps catching hits on the
       old grid by coincidence and never gives the tempo-change rule its three same-signed innovations: this
       is what catches it (re-lock in 7-9 s, synthetic). The old song's level is forgotten at a cold search.
     Silence never starts one: the tempogram's weighted sum under X0_MIN (1e-6) reads as no rhythm.
6. **Published clock:** separate from the estimator.
   - While the beat gate is on, phase differences are slewed as a temporary tempo change (at most 4 %, half the
     error per beat); below it, the line jumps to the estimator.
   - The anchor is always an integer beat near now, and the published beat count never jumps by more than half a
     beat (``epoch`` counts jumps).
   - Integer corrections (octave, bar) re-anchor at an upcoming beat.

Published body (``music.clock``, ``contract.ClockData``): on every re-anchor, and at 10 Hz from ``tick``
--------------------------------------------------------------------------------------------------------------
``bpm`` (0.0 before the first grid), ``anchor_ms`` (CLOCK_MONOTONIC ms, acoustic, of integer beat
``beat_at_anchor``), ``beats_per_bar``, ``bar_anchor_beat`` (a beat that starts both a bar and a phrase, so
``bar = floor((beat - bar_anchor_beat) / beats_per_bar)`` and ``bar % phrase_bars`` is the bar in the phrase),
``phrase_bars`` (4/8/16), ``conf``, ``bar_conf``, ``phrase_conf`` (0..1), and ``state``:
- ``searching``: no confirmed grid;
- ``locked``: tracking (>= 2 hits in the last 4 beats);
- ``holding``: coasting through a breakdown on the held grid;
- ``free``: coasting with no audio arriving, or on a tap tempo that audio hasn't confirmed.

Extras:
- ``gate``: ``beat``/``dmx``/``none``, with hysteresis (beat on 0.70, off 0.60; dmx on 0.45, off 0.35) and a
  2-bar minimum dwell;
- ``epoch``: +1 per published jump;
- ``stability``: tempogram clarity at the clock's tempo;
- ``source``: the activation's name;
- ``t_ms``: publish time.

``conf`` is "calibrated-ish": P(beat present) x P(|error| < 25 ms) x clarity.
- P(beat present) is the hit rate over 8 beats.
- P(|error| < 25 ms) blends the empirical and Gaussian precision of the last 8 innovations, shrunk toward 0.35 while
  there are few of them. A seed brings leave-one-out residuals, never in-sample ones.
- The clarity term is 0.3 + 0.7 x smoothstep(0.08, 0.35, clarity).
- It is capped at 0.3 until the grid is confirmed (3 hits in 4).
It is a hand-set stand-in until the logistic fit on recordings (§13) exists. Synthetic checks: clean loops 0.86-0.94,
half-time 0.9, random onsets and rubato <= 0.3.

Corrections (*any thread*)
--------------------------
- ``apply_integer_correction(beat_is_one, bar_len, octave)`` is for the slow lane (Beat This!, through mllink's
  ``beat_vote``, which reads only the window's interior and gates on its agreement with this clock). ``beat_is_one``
  is a beat index in this clock's published numbering (carry window times over with ``beat_at``), and ``octave`` is
  1, 2 (the beat is twice as fast) or 0.5. Integer corrections only: never a phase observation, never a tempo.
  On show-rx:
  - **Checked.** ``beat_is_one`` must be a whole number, ``bar_len`` a whole number in BAR_LEN_MIN..BAR_LEN_MAX and
    ``octave`` exactly 1, 2 or 0.5, or the call is ignored (``corr_bad``).
  - **Gated.** It needs a confirmed grid (``corr_no_grid`` otherwise). Its one must not precede the last numbering
    break (a new grid, an octave change, a re-lock: the count before it was another grid's) nor lie more than a bar
    past the upcoming beat (``corr_stale``). A break while it waits drops it (``corr_stale``); so does a human "one"
    (``corr_superseded``). A later call replaces a waiting one.
  - **At a bar line.** It waits for the upcoming beat K that starts a bar under the new hypothesis:
    ``((K - one) x octave) mod bar_len == 0`` (the one defaults to the bar anchor, the length to the current one). The
    octave switches there (that beat keeps its time and number), the bar length and one take effect there, and the
    bar's place in the phrase is kept. An octave out of 45-200 bpm is refused there and nothing moves
    (``corr_refused``). One that has not met its bar line after twice the longest wait (2 x bar_len beats, x2 for
    octave 0.5: the count moved under it) is dropped (``corr_expired``), never applied off a bar line.
  - The dbn estimator's octave is the publisher's octave map ("Estimators"): an octave also waits for a beat of both
    grids (past a bar line that is an in-between beat, the first one after it, which then starts the bar), and its
    range is DBN_OCT_MIN_BPM..DBN_OCT_MAX_BPM.
  - Every accepted call (it agrees on the octave, or sets one) holds the clock's own octave check off for
    SLOW_OCT_HOLD_S. ``stats()["last_correction"]`` = ``{one, bar_len, octave, result, at_beat}`` for the latest
    (result ``accepted``, ``applied``, ``bad``, ``no_grid``, ``stale``, ``superseded``, ``refused`` or
    ``expired``, each also counted as ``corr_<result>``).
- ``tap(t_s)``, ``half()``, ``double()`` and ``one(t_s)`` are the show console's human actions: high-confidence
  observations, not overrides.

Costs and hot-thread rules: numpy buffers are preallocated where it is cheap (soxr's output is its own), there is no
I/O but the process's non-blocking pipe ends, and an exception inside ``process``/``tick`` is counted and never
propagates. ``stats()["cost_ms_per_10ms"]`` reports the measured cost of show-rx's part per 10 ms of audio,
``stats()["block_ms"]`` show-rx's cost per ``process`` call, and ``stats()["beat_thread"]`` show-beat's or the
process's: ``act_ms`` a frame, ``lag_ms`` from send to drain, ``mode`` (inline, thread or process), ``via`` (hops
leave show-rx now), and for the process ``pid``, ``state``, ``restarts``, ``failures_in_row``, ``last_exit``,
``cpu`` (the child's share of a core), ``inline_hops``, ``switches``, ``stalls``, ``pipe_full``, ``proc_lost``.
"""

from __future__ import annotations

import collections
import logging
import math
import os
import queue
import threading
import time
import warnings
import weakref
from typing import Callable, Literal, Optional, Protocol

import numpy as np

from . import beatdbn
from .contract import AUDIO_GAP, AUDIO_RESUME, AUDIO_SEGMENT, MUSIC_CLOCK, ClockData, Publish

log = logging.getLogger("show_service.clock")

# -- constants ---------------------------------------------------------------------------------------------------------
FS = 44100                  # rhythm lane rate (madmom was trained at 44.1 kHz; at 48 kHz its frames are 9 % off)
HOP = 441
FPS = FS / HOP              # 100 activation frames a second
MIN_BPM = 45.0              # madmom's default 55 causes octave errors on slow music
MAX_BPM = 200.0
GUARD_BPM = 240.0           # the tempogram runs to 240 so the 8th-note rate of 120 bpm peaks there, not at the edge
TEMPO_WIN_S = 6.0
ALPHA = 0.2
BETA = ALPHA * ALPHA / (2.0 - ALPHA)     # critically damped alpha-beta
GATE_FRAC = 0.20            # observations within +-20 % of a period; beyond that, outliers
RELOCK_N = 3                # outliers in a row that agree -> re-lock
RELOCK_SPREAD = 0.10        # ... agree = spread within 10 % of a period
OCTAVE_BEATS = 8
OCTAVE_MARGIN = 1.3
CONF_X_S = 0.025            # conf = P(next beat lands within +-25 ms)
BEAT_ON, BEAT_OFF = 0.70, 0.60
# the room is quiet (set_level, features.gate; artnet_out's numbers): under QUIET_GATE_OFF for QUIET_AFTER_S, until
# back over QUIET_GATE_ON. While quiet the activation reads silence (madmom isn't run: no beat from talk or room
# noise, and no CPU), the gate is "none" at once and a lock reads "holding" (the grid is kept for a breakdown).
QUIET_GATE_OFF, QUIET_GATE_ON = 0.02, 0.05
QUIET_AFTER_S = 3.0
DMX_ON, DMX_OFF = 0.45, 0.35
DWELL_BARS = 2
SLEW_BEATS = 2.0            # close half the published phase error per beat ...
SLEW_MAX = 0.04             # ... as a tempo change of at most 4 %
PUBLISH_HZ = 10.0
NO_AUDIO_S = 1.5            # no frames for this long -> "free", coasting on tick time
SUPPRESS_S = 0.06           # no peaks within 60 ms after a zero-filled gap or a resume (madmom's window is 46 ms)
AGC_TARGET_DB = -12.0       # tracker-lane RMS aim. madmom was trained on mastered audio: at -20 dBFS RMS one LSTM takes
                            # off-beat hats for the beat, at -10 every ensemble takes the kicks (measured 16 Oct)
PRIOR_BPM = 120.0
PRIOR_BROAD_OCT = 1.0       # cold prior: log-normal, 1 octave sigma around 120 bpm (also the octave "metrical" weight)
PRIOR_NARROW_OCT = math.log2(1.4) / 1.5    # running prior: +-40 % is 1.5 sigma
PRIOR_TAU_S = 45.0          # running prior = the locked tempo over the last 30-60 s
PRIOR_KEEP_S = 60.0
SEED_MIN_FRAMES = 250       # 2.5 s of activation before a cold seed
SEED_MIN_CLARITY = 0.15
FAIL_CLARITY = 0.30         # a pulse this clear that isn't ours (and no hits) -> cold search
X0_MIN = 1e-6               # the tempogram's weighted sum below this is silence (its residue is ~1e-13, either sign):
                            # clarity ratios there are noise
ALT_HOLD_S = 4.0            # ... or another tempo >= FAIL_CLARITY and >= ALT_RATIO x ours, held this long, conf low
ALT_RATIO = 2.0
ALT_SAME = 0.04             # candidates within 4 % are the same tempo (and within 4 % of x1, x2, x0.5 ours: ours)
LEVEL_TAU_S = 20.0          # while not locked, the locked level (and the peak floor) fades: a quieter song follows,
LEVEL_GRACE_S = 8.0         # ... from this long after the last locked hit (past the tempogram's 6 s and madmom's
                            # ghost beats after the drums stop, which must still face the full level)
# the octave from the kicks (until the slow lane, Beat This!, exists): dsp's sub/bass hits (observe_hit)
FAST_BPM = 160.0            # a grid above this must show low hits on most of its beats ...
FAST_COVER = 0.7            # ... this share of them over the last LOW_WIN_S; less (kicks on alternate beats): the half
SEED_CAP_BPM = 180.0        # with no low-hit evidence either way, never seed or double above this (seed the half)
LOW_WIN_S = 6.0
LOW_MIN_HITS = 4            # on-beat low hits in the window before they count as evidence
LOW_MERGE_S = 0.06          # a sub and a bass hit this close are one kick
LOW_TOL = 0.15              # a hit within 15 % of a period of a beat lands on it
# show-beat (README S4.6)
BEAT_Q_MAX = 50             # hops in flight to show-beat or the process (0.5 s of frames); beyond this show-rx drops hops
BEAT_STALL_S = 2.0          # the process: hops in flight and no record for this long -> killed, inline at once
BEAT_HELLO_S = 30.0         # ... no hello this long after a spawn -> killed and respawned (beatproc.HELLO_S)
# blocks and holes (contract.OnAudio: up to COALESCE_MAX_FRAMES contiguous frames a call)
AGC_CHUNK = 128             # the AGC steps once per this many frames (aligned on n), so any block cut gives one gain
RESUME_MAX_FRAMES = 256     # a RESUME block is one packet (VBAN: at most 256 frames); its suppression spans no more
# integer corrections (the slow lane, Beat This!)
BAR_LEN_MIN, BAR_LEN_MAX = 2, 16   # a bar length outside this is not a bar: the correction is ignored
SLOW_OCT_HOLD_S = 30.0      # after a slow-lane or human octave word, the clock's own octave check waits this long
HUMAN_OCT_HOLD_S = 600.0    # a person's half/double outranks the slow lane's octave votes (dropped whole: their one
                            # and bar length assumed their octave) this long, or until a new song (a cold grid)
# the estimator after the activation (module docstring, "Estimators")
ESTIMATORS = ("dbn", "legacy")
DBN_CONFIRM_N = 3           # dbn: the bars start (bar_anchor at that beat, bar_conf 0.1) at the 3rd DBN beat on a line
# dbn, the published line (module docstring, "Estimators"; README S4.15 "Publishing"): step 6's slew on the tracker's
# line, by the gate: (close 1/x of the phase error per beat, as a tempo change of at most this)
DBN_SLEW = {"beat": (SLEW_BEATS, 0.10), "dmx": (1.0, 0.25), "none": (1.0, 0.5)}
DBN_NEW_TEMPO = 0.08        # the tracker's tempo this far from the last one published from: a new grid (a jump) ...
DBN_NEW_N = 3               # ... on a grid that was gated, once this many line updates in a row say so (a re-phase's
                            # first beats can read 10 % off for a beat or two)
DBN_PIVOT_S = 0.15          # a slower line keeps the count at this far past "now as heard" (the consumers' now is
                            # later): so the count a consumer reads at its now never goes back
DBN_NEW_BAR_CONF = 0.1      # a new grid (a tempo jump) keeps the bar anchor; its bar_conf drops to this
# dbn, the octave map (module docstring, "Estimators"; README S4.15 "The octave map"): an octave correction maps the
# published line onto the tracker's at x2 or x0.5 (composable) and keeps it there while the DBN tracks at its own
# octave underneath. The mapped tempo must lie within the clock's 45-200 bpm with this slack (a quickstep at 50-52
# bars a minute is 200-208 bpm, its tracker at the half reads 101-104), or the correction is refused; a map whose
# tempo drifts out of that range ends (a new grid on the tracker's own).
DBN_OCT_SLACK = 0.08
DBN_OCT_MIN_BPM, DBN_OCT_MAX_BPM = MIN_BPM / (1.0 + DBN_OCT_SLACK), MAX_BPM * (1.0 + DBN_OCT_SLACK)
DBN_OCT_FACTORS = (0.25, 0.5, 1.0, 2.0, 4.0)

ClockState = Literal["searching", "locked", "holding", "free"]
Gate = Literal["beat", "dmx", "none"]
_GATE_RANK = {"none": 0, "dmx": 1, "beat": 2}


# -- consumer helpers (pure functions of a published body) -------------------------------------------------------------
def beat_at(clock: ClockData, t_s: float) -> float:
    """The published beat (float) at host time ``t_s``; NaN before the first grid."""
    bpm = clock["bpm"]
    if bpm <= 0.0:
        return math.nan
    return clock["beat_at_anchor"] + (t_s * 1000.0 - clock["anchor_ms"]) * bpm / 60000.0


def next_beat(clock: ClockData, after_s: float, lead_s: float = 0.0) -> Optional[tuple[int, float]]:
    """The first integer beat ``k`` whose fire time ``t_k - lead_s`` is at or after ``after_s``: ``(k, t_k)``,
    ``t_k`` being the acoustic time of the beat. An output with latency ``lead_s`` fires it at ``t_k - lead_s``.
    None without a grid."""
    b = beat_at(clock, after_s + lead_s)
    if math.isnan(b):
        return None
    k = math.ceil(b - 1e-9)
    return k, clock["anchor_ms"] / 1000.0 + (k - clock["beat_at_anchor"]) * 60.0 / clock["bpm"]


def position_at(clock: ClockData, t_s: float) -> Optional[dict]:
    """``{beat, bar, beat_in_bar (float), bar_in_phrase (int), phrase}`` at host time ``t_s``; None without a grid."""
    b = beat_at(clock, t_s)
    if math.isnan(b):
        return None
    rel = b - clock["bar_anchor_beat"]
    bpb = max(1, clock["beats_per_bar"])
    bar = math.floor(rel / bpb)
    pb = max(1, clock["phrase_bars"])
    return {"beat": b, "bar": bar, "beat_in_bar": rel - bar * bpb, "bar_in_phrase": bar % pb,
            "phrase": bar // pb}


# -- activation sources ------------------------------------------------------------------------------------------------
class Activation(Protocol):
    """A beat (or onset) activation at 100 fps from 44.1 kHz mono. One instance per lane, used on show-rx only."""
    name: str
    lag_s: float        # its peak sits this long after the sound it marks (measured on clicks)
    floor: float        # smallest raw value a beat peak may have (after the lane's AGC); 0 = relative only

    def process_hop(self, hop: np.ndarray) -> float:
        """``hop``: exactly HOP new float32 samples. Returns the activation (>= 0) of the frame ending at the newest
        sample."""
        ...

    def reset(self) -> None:
        """Forget all state (a new segment or stream)."""
        ...


class AubioActivation:
    """aubio-ledfx onset novelty, 1024-point window: 0.6 x the rise in frame energy (carried by kick and bass) + 0.4 x
    log-compressed spectral flux (every onset), each over its own max with a 30 s decay (floored at 5 % of the
    loudest seen). Whitened specflux alone
    scores off-beat hats as high as the kicks, and locks to the off-beat. It costs about 0.06 ms a frame. It is weaker
    than madmom (an onset, not a beat, function), but runs anywhere."""
    name = "aubio"
    lag_s = 0.012
    floor = 0.05

    def __init__(self) -> None:
        import aubio                                    # lazy: the module imports without it
        self._aubio = aubio
        self._dec = math.exp(-1.0 / (30.0 * FPS))
        self.reset()

    def _onset(self, method: str, compression: float):
        o = self._aubio.onset(method, 1024, HOP, FS)
        o.set_awhitening(0)
        o.set_compression(compression)
        return o

    def process_hop(self, hop: np.ndarray) -> float:
        self._en(hop)
        self._sf(hop)
        e = max(0.0, float(self._en.get_descriptor()))
        de = max(0.0, e - self._e_prev)
        self._e_prev = e
        s = max(0.0, float(self._sf.get_descriptor()))
        self._top_e, self._top_s = max(self._top_e, de), max(self._top_s, s)
        self._me = max(de, self._me * self._dec, 0.05 * self._top_e, 1e-9)    # never amplify silence to onsets
        self._ms = max(s, self._ms * self._dec, 0.05 * self._top_s, 1e-9)
        return 0.6 * de / self._me + 0.4 * s / self._ms

    def reset(self) -> None:
        self._en = self._onset("energy", 0.0)
        self._sf = self._onset("specflux", 1.0)
        self._me = self._ms = 1e-9
        self._top_e = self._top_s = 0.0
        self._e_prev = 0.0


class MadmomActivation:
    """madmom's online beat LSTM (RNNBeatProcessor(online=True), 2048-point frames, 100 fps), fed frame by frame.
    Measured here: 1 net 0.45 ms p50 / 1.0 ms p99 a frame; the 8-net ensemble 1.7 / 5.7 ms (niced, under render
    load). ``nets`` picks how many of the 8 LSTMs to average. The default is 2: on a synthetic kick + off-beat hat
    loop, one LSTM sometimes takes the hats for the beat, while two or more never did."""
    name = "madmom"
    lag_s = 0.020
    floor = 0.03
    FRAME = 2048

    def __init__(self, nets: int = 2) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from madmom.audio.signal import Signal
            from madmom.features.beats import RNNBeatProcessor
            from madmom.models import BEATS_LSTM
        self.nets = max(1, min(int(nets), len(BEATS_LSTM)))
        self._Signal = Signal
        self._proc = RNNBeatProcessor(online=True, nn_files=list(BEATS_LSTM[:self.nets]), origin="stream",
                                      num_frames=1, fps=FPS)
        self._buf = np.zeros(self.FRAME, np.float32)
        self._reset_next = True
        self.process_hop(np.zeros(HOP, np.float32))      # warm-up (first call builds caches)
        self.reset()

    def process_hop(self, hop: np.ndarray) -> float:
        b = self._buf
        b[:-HOP] = b[HOP:]
        b[-HOP:] = hop
        a = self._proc(self._Signal(b, sample_rate=FS), reset=self._reset_next)
        self._reset_next = False
        return max(0.0, float(np.asarray(a).reshape(-1)[-1]))

    def reset(self) -> None:
        self._buf[:] = 0.0
        self._reset_next = True


def make_activation(prefer: str = "auto", nets: int = 2) -> Activation:
    """``"madmom"``, ``"aubio"``, or ``"auto"`` (madmom when it imports, else aubio)."""
    if prefer in ("auto", "madmom"):
        try:
            return MadmomActivation(nets=nets)
        except Exception as exc:                        # not installed, or broken on this numpy
            if prefer == "madmom":
                raise
            log.warning("madmom activation unavailable (%s); using aubio", exc)
    return AubioActivation()


class _BeatThread:
    """show-beat: the activation on its own daemon thread (see the module docstring). It holds no reference to the
    Clock (a dropped Clock's finalizer stops it). Items in ``q`` (put by show-rx):
    ``("hop", epoch, seq, hop, t_raw, t_put)``, ``("reset", epoch)``, or None (stop). Items in ``out`` (popped by
    show-rx): ``("a", epoch, seq, a | None, t_raw, t_put)``, and each reset marker as it was reached. Its transport
    face (``send_hop`` .. ``stop``) is beatproc.BeatProc's, so the Clock drives both the same way."""
    respawns = False                    # a dead show-beat stays dead: the Clock runs inline for good
    pid = None
    gen = 0

    def __init__(self, act: Activation) -> None:
        self.act = act
        self.q: queue.SimpleQueue = queue.SimpleQueue()
        self.out: collections.deque = collections.deque()
        self.done = 0                                   # hops computed (written here only)
        self.sent = 0                                   # hops put (show-rx only)
        self.errors = 0
        self.cost: collections.deque = collections.deque(maxlen=2000)     # ms per process_hop (written here only)
        self.idle = threading.Event()                   # set when the queue runs dry (wait_beat only)
        self.last_progress = 0.0                        # (BeatProc's stall clock; a thread is never killed)
        self._err_logged = -1e9
        self._stopped = False
        self.thread = threading.Thread(target=self._run, name="show-beat", daemon=True)
        self.thread.start()

    # -- the transport (show-rx) ---------------------------------------------------------------------------------------
    @property
    def alive(self) -> bool:
        return self.thread.is_alive()

    ready = alive

    @property
    def inflight(self) -> int:
        return self.sent - self.done

    def send_hop(self, epoch: int, seq: int, hop: np.ndarray, t_raw: float, t_put: float) -> int:
        self.q.put(("hop", epoch, seq, hop.copy(), t_raw, t_put))
        self.sent += 1
        return 0

    def send_reset(self, epoch: int) -> None:
        self.q.put(("reset", epoch))

    def poll(self) -> None:
        """Nothing to read: show-beat appends to ``out`` itself."""

    def wait(self, dt: float) -> None:
        self.idle.wait(dt)
        self.idle.clear()

    def snapshot(self) -> dict:
        return {"pid": None, "state": "running" if self.thread.is_alive() else "stopped", "why": None, "restarts": 0,
                "failures_in_row": 0, "last_exit": None, "cpu": None}

    def stop(self, timeout: float = 1.0) -> None:
        if not self._stopped:
            self._stopped = True
            self.q.put(None)
        if self.thread is not threading.current_thread():
            self.thread.join(max(0.0, float(timeout)))

    def _run(self) -> None:
        q, out, act = self.q, self.out, self.act
        perf = time.perf_counter
        while True:
            item = q.get()
            if item is None:
                self.idle.set()
                return
            try:
                if item[0] == "hop":
                    _, ep, seq, hop, t_raw, t_put = item
                    c0 = perf()
                    try:
                        a: Optional[float] = float(act.process_hop(hop))
                    except Exception:
                        a = None
                        self._error()
                    self.cost.append((perf() - c0) * 1000.0)
                    out.append(("a", ep, seq, a, t_raw, t_put))
                    self.done += 1
                else:
                    try:
                        act.reset()
                    except Exception:
                        self._error()
                    out.append(item)
            except Exception:                           # never let show-beat die
                self._error()
            if q.empty():
                self.idle.set()

    def _error(self) -> None:
        self.errors += 1
        now = time.monotonic()
        if now - self._err_logged > 10.0:
            self._err_logged = now
            log.exception("show-beat: activation error (%d so far)", self.errors)


# -- tempogram ---------------------------------------------------------------------------------------------------------
class Tempogram:
    """A sliding-DFT Fourier tempogram over the last ``win_s`` of activation, one bin per ``step_bpm``.

    The window is causal, PLP's "half window": the rising half of a Hann of length 2N over the last N frames, so the
    newest frame weighs most and a window that is still filling reads its newest data. w(m) = sin^2(pi m / 2N)
    = 0.5 - 0.25 e^{i beta m} - 0.25 e^{-i beta m}, with beta = pi / N and m = 0 the oldest frame. The windowed DFT
    at omega is therefore three plain sliding sums at omega and omega +- beta, each kept in O(1) per frame with
    absolute-time phasors. Bin 0 is DC, giving the window-weighted sum, so ``clarity = |X| / X0`` is in 0..1 for a
    non-negative input (1 = all of the activation is a pulse train at that tempo or a divisor of it).
    """

    def __init__(self, fps: float = FPS, win_s: float = TEMPO_WIN_S, bpm_lo: float = MIN_BPM,
                 bpm_hi: float = MAX_BPM, step_bpm: float = 1.0) -> None:
        self.fps = fps
        self.N = N = int(round(win_s * fps))
        self.bpm = np.arange(bpm_lo, bpm_hi + 0.5 * step_bpm, step_bpm)
        self.bpm_lo, self.step = float(bpm_lo), float(step_bpm)
        self.K = len(self.bpm)
        f0 = np.concatenate(([0.0], self.bpm / 60.0 / fps * 2.0 * np.pi))
        self._beta = np.pi / N
        self._w = np.concatenate([f0, f0 - self._beta, f0 + self._beta])
        self._K1 = len(f0)
        self._step = np.exp(-1j * self._w)
        self._back = np.exp(1j * self._w * N)
        self._tmp = np.zeros(len(self._w), complex)
        self.reset()

    def reset(self) -> None:
        self._S = np.zeros(len(self._w), complex)
        self._P = np.ones(len(self._w), complex)
        self._n = 0
        self._ring = np.zeros(self.N)
        self.filled = 0
        self.t_last = math.nan

    def push(self, x: float, t: float) -> None:
        """One frame: value ``x`` (>= 0) whose sound is at host time ``t``."""
        i = self._n % self.N
        old = self._ring[i]
        tmp = self._tmp
        np.multiply(self._back, -old, out=tmp)
        tmp += x
        tmp *= self._P
        self._S += tmp
        self._ring[i] = x
        self._n += 1
        self._P *= self._step
        if (self._n & 4095) == 0:                      # re-derive the phasors exactly now and then
            self._P = np.exp(-1j * self._w * self._n)
        if self.filled < self.N:
            self.filled += 1
        self.t_last = t

    def spectrum(self) -> np.ndarray:
        """Windowed DFT of the current window: [X0 (real: weighted sum), X(bpm_0), X(bpm_1), ...]."""
        K1, S = self._K1, self._S
        c = np.exp(-1j * self._beta * ((self._n - self.N) % (2 * self.N)))
        return 0.5 * S[:K1] - 0.25 * c * S[K1:2 * K1] - 0.25 * np.conj(c) * S[2 * K1:]

    def clarity(self, X: np.ndarray, bpm: float) -> float:
        """|X(bpm)| / X0, linearly interpolated between bins; 0 outside the range or on silence."""
        x0 = X[0].real
        if not (self.bpm_lo <= bpm <= self.bpm[-1]) or x0 <= X0_MIN:
            return 0.0
        pos = (bpm - self.bpm_lo) / self.step
        i = min(int(pos), self.K - 1)
        f = pos - i
        j = min(i + 1, self.K - 1)
        return float((abs(X[1 + i]) * (1.0 - f) + abs(X[1 + j]) * f) / x0)

    def beat_time(self, X: np.ndarray, i: int) -> float:
        """Host time of the beat nearest the newest frame implied by bin ``i``'s phase (the PLP kernel's peak)."""
        w = self._w[1 + i]
        theta = -float(np.angle(X[1 + i]))
        n_last = self._n - 1
        m = round((w * n_last - theta) / (2.0 * np.pi))
        j_b = (theta + 2.0 * np.pi * m) / w
        return self.t_last + (j_b - n_last) / self.fps


# -- peak picking ------------------------------------------------------------------------------------------------------
class _Peaks:
    """Causal peak picker with a 3-frame look-ahead. A peak must be a local maximum over +-3 frames and above
    max(floor, 0.3 x L, 1.5 x the 0.5 s local mean). L is the recent peak height, max-held with a 20 s decay, and
    floored at half the level seen while locked, so pads in a breakdown don't become beats."""
    W = 3

    def __init__(self) -> None:
        self.L = 0.0
        self.L_floor = 0.0
        self._dec = math.exp(-1.0 / (20.0 * FPS))
        self._mu_a = 1.0 - math.exp(-1.0 / (0.5 * FPS))
        self.reset()

    def reset(self) -> None:
        self.a = [0.0] * (2 * self.W + 1)
        self.t = [0.0] * (2 * self.W + 1)
        self.n = 0
        self.mu = 0.0

    def push(self, a: float, t: float, floor: float) -> tuple[float, Optional[tuple[float, float, float]]]:
        """Returns (the tempogram's input: the mean-removed value where the activation reaches the peak threshold,
        else 0, so noise and pads carry no rhythm; the peak (t, strength 0..1.5, height) of the frame W back, or
        None)."""
        self.mu += (a - self.mu) * self._mu_a
        av, tv = self.a, self.t
        av.pop(0)
        av.append(a)
        tv.pop(0)
        tv.append(t)
        self.n += 1
        self.L = max(self.L * self._dec, self.L_floor)
        thr = max(floor, 0.3 * self.L, 1.5 * self.mu)
        out = max(0.0, a - self.mu) if a >= thr else 0.0
        if self.n < len(av):
            return out, None
        c = self.W
        h = av[c]
        if h <= av[c - 1] or h < max(av):
            return out, None
        if h < thr or h <= 0.0:
            return out, None
        den = av[c - 1] - 2.0 * h + av[c + 1]
        d = 0.5 * (av[c - 1] - av[c + 1]) / den if den < 0.0 else 0.0
        d = min(0.5, max(-0.5, d))
        hp = h - 0.25 * (av[c - 1] - av[c + 1]) * d
        if hp > self.L:
            self.L = hp
        return out, (tv[c] + d / FPS, min(1.5, hp / max(self.L, 1e-12)), hp)


def _smoothstep(e0: float, e1: float, x: float) -> float:
    u = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return u * u * (3.0 - 2.0 * u)


def _arr(dq: collections.deque) -> np.ndarray:
    """A deque another thread may append to, as an array (``list`` copies it in one C call, under the GIL)."""
    for _ in range(3):
        try:
            return np.asarray(list(dq), float)
        except RuntimeError:                            # mutated during the copy: try again
            continue
    return np.zeros(0)


def _pct(a: np.ndarray, q: float, nd: int = 4) -> float:
    return round(float(np.percentile(a, q)), nd) if a.size else 0.0


def _whole(v: object) -> Optional[int]:
    """``v`` as an int if it is a whole number (int, numpy integer, or an integral float); else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)) and math.isfinite(v) and float(v).is_integer():
        return int(v)
    return None


# -- the clock ---------------------------------------------------------------------------------------------------------
class Clock:
    """One room's beat clock. ``process`` = contract.OnAudio, ``tick`` from on_tick, both on show-rx. With
    ``beat_proc`` the activation runs in a child process (beatproc.py; ``beat_nice`` its nice, ``beat_log`` its
    stderr log, appended), with ``beat_thread`` on show-beat; neither (the default, as show_service runs) runs it
    inline on show-rx (Stage 3). ``beat_proc`` wins over ``beat_thread``, and needs madmom (or beatproc's fake): with
    another activation it is ignored. ``gap_skip`` and ``agc_chunks`` (on) are the block paths' rollbacks (see the
    module docstring). ``estimator``: ``"dbn"`` (the default: beatdbn's tracker after the activation) or
    ``"legacy"`` (the PLL estimator, exactly as before: the rollback); anything else is logged and runs ``"dbn"``,
    and a tracker that can't be built runs ``"legacy"`` (degrade, never refuse)."""

    def __init__(self, publish: Optional[Publish] = None, *, activation: "Activation | str" = "auto", nets: int = 2,
                 beats_per_bar: int = 4, phrase_bars: int = 8, prior_bpm: float = PRIOR_BPM,
                 publish_hz: float = PUBLISH_HZ, resample_quality: str = "HQ", beat_thread: bool = False,
                 beat_proc: bool = False, beat_nice: int = 0,
                 beat_log: "Optional[os.PathLike | str]" = None, gap_skip: bool = True,
                 agc_chunks: bool = True, estimator: str = "dbn") -> None:
        self._publish = publish
        est = str(estimator).strip().lower() if isinstance(estimator, str) else ""
        if est not in ESTIMATORS:
            log.warning("clock estimator %r unknown (known: %s): dbn", estimator, ", ".join(ESTIMATORS))
            est = "dbn"
        self._estimator = est
        self._trk: Optional[beatdbn.DbnTracker] = None   # the dbn estimator's tracker (reset() makes it), or None
        self._act_fresh = True                          # inline: the activation was reset since its last hop
        self._bt_fresh = False                          # show-beat / the process: a reset came back, its hop next
        self._f_silent = self._f_fresh = False          # the next _frame's flags (the dbn tracker reads them)
        self._gap_skip = bool(gap_skip)                 # False: every hole is zero-filled through the lane (as before)
        self._agc_chunks = bool(agc_chunks)             # False: one AGC step per block, its new gain on it (as before)
        self._counts = collections.Counter()
        self.act: Activation = make_activation(activation, nets) if isinstance(activation, str) else activation
        # show-beat or the beat process (all of these but the transport's own fields are show-rx's)
        self._bt = None                                 # _BeatThread | beatproc.BeatProc | None
        self._bt_epoch = 0
        self._bt_put = 0                                # hops sent (their seq), show-rx
        self._bt_got = 0                                # hop results drained (any epoch), show-rx
        self._bt_seq_next = 1
        self._bt_splice = False                         # hops were dropped: reset the activation before the next
        self._bt_stopped = False
        self._bt_via = False                            # the process: hops go to it now (else inline, on show-rx)
        self._bt_gen = 0                                # ... to this generation of it
        self._act_splice = False                        # inline: reset the activation before the next hop (a hole)
        self._zq: collections.deque = collections.deque()   # silent frames waiting behind hops in flight: (seq, t)
        self._bt_lag: collections.deque = collections.deque(maxlen=2000)    # ms from send to drain
        self._blk: collections.deque = collections.deque(maxlen=2000)       # ms per process() call
        self._corr_last: Optional[dict] = None
        if beat_proc and getattr(self.act, "name", None) in ("madmom", "fake"):
            try:
                from .beatproc import BeatProc
                self._bt = BeatProc(self.act.name, int(getattr(self.act, "nets", 2) or 0), nice=int(beat_nice),
                                    log_path=beat_log, hello_s=BEAT_HELLO_S)
                weakref.finalize(self, self._bt.close)
            except Exception:                           # degrade: the thread (if asked for) or inline
                self._counts["beat_proc_failed"] += 1
                log.exception("the beat process could not be set up: %s",
                              "show-beat instead" if beat_thread else "inline on show-rx")
                self._bt = None
        elif beat_proc:
            log.info("beat process: not for the %s activation (madmom only)", getattr(self.act, "name", "?"))
        if self._bt is None and beat_thread:
            self._bt = _BeatThread(self.act)
            weakref.finalize(self, self._bt.q.put, None)
        self._prior_bpm = float(prior_bpm)
        self._pub_period = 1.0 / publish_hz
        self._rs_quality = resample_quality
        self._def_bpb, self._def_phrase = int(beats_per_bar), int(phrase_bars)
        self.tg = Tempogram(bpm_hi=GUARD_BPM)
        self._lb = np.log2(self.tg.bpm)
        self._pk = _Peaks()
        self._hopbuf = np.zeros(HOP, np.float32)
        self._inbox: collections.deque = collections.deque()
        self._hitq: collections.deque = collections.deque(maxlen=128)
        self._cost: collections.deque = collections.deque(maxlen=2000)
        self._err_n = 0
        self._err_logged = -1e9
        self._snapshot: Optional[ClockData] = None
        self._last_pub_s = -1e9
        self._next_hb = -1e9
        self._lane_fs = 0
        self._rs = None
        self._agc_db = 0.0
        self._agc_pow = 0.0
        self._agc_init = False
        self._agc_acc_p = self._agc_acc_t = 0.0
        self._agc_k: tuple = (None, 0.0, 0.0, 0.0)     # (dt, the three smoothing steps at that dt): exp() once a size
        self._agc_g = np.float32(1.0)                  # the gain now (from the chunks before)
        self._agc_sp, self._agc_sn = 0.0, 0            # the chunk in progress: sum of squares, frames
        self._gbuf = np.zeros(4096, np.float32)        # the lane's gained mono block (grows once if a block is longer)
        self._mbuf = np.zeros(4096, np.float32)        # ... its mono mix of a multichannel block
        self._zbuf = np.zeros(4096, np.float32)        # zeros for a short hole (never written)
        self._lvl_dec = math.exp(-1.0 / (LEVEL_TAU_S * FPS))
        self.reset()

    # ---- public: show-rx ---------------------------------------------------------------------------------------------
    def reset(self) -> None:
        """Cold start: forget the grid, the tempogram and the published line (show-rx)."""
        self._grid = False
        self._confirmed = False
        self._T = 0.5
        self._T_ab = 0.5
        self._k_next = 0
        self._t_next = 0.0
        self._obs: collections.deque = collections.deque(maxlen=32)
        self._res: collections.deque = collections.deque(maxlen=8)
        self._hits: collections.deque = collections.deque(maxlen=8)
        self._out: list[tuple[int, float]] = []
        self._acq = 0
        self._since_seed = 0
        self._h_good = 0.0
        self._conf_good = 0.0
        self._n_innov = 0
        self._tight = 0
        self._was_holding = False
        self._oct_streak = 0
        self._oct_alt = 1.0
        self._oct_lock_k = -10**9
        self._oct_lock_t = -1e9
        self._pending: Optional[dict] = None
        self._break_k = -(1 << 62)                  # published beat of the last numbering break (corrections)
        self._pending_oct: Optional[float] = None
        self._human_oct_t: Optional[float] = None       # when a person last said half/double (HUMAN_OCT_HOLD_S)
        self._pending_kick: Optional[int] = None   # with a kick-voted halving: an estimator beat that carries kicks
        self._seed_prev: Optional[float] = None
        self._R_s: Optional[np.ndarray] = None
        self._prior_c: Optional[float] = None
        self._last_locked_t = -1e9
        self._lag_adj = 0.0
        self._peaks: collections.deque = collections.deque()
        self._recent: collections.deque = collections.deque()
        self._L_lock = 0.0                          # the peak level heard while locked ...
        self._L_fade = 0.0                          # ... fading (LEVEL_TAU_S) while not locked
        self._alt: Optional[tuple[float, float]] = None     # (bpm, since): a clearer tempo than ours (new song?)
        self._taps: collections.deque = collections.deque(maxlen=8)
        self._tap_mode = False
        self._clar = 0.0
        self._conf = 0.0
        self._gate: str = "none"
        self._gate_t = -1e9
        self._gate_up = True
        self._quiet = False
        self._quiet_below: Optional[float] = None
        self._state: str = "searching"
        self._t_known = -1e9
        self._t_known_seen = -1e9
        self._w_known = None
        self._no_audio = False
        self._pub_ok = False
        self._pub_k = 0
        self._pub_t = 0.0
        self._pub_bpm = 0.0
        self._off = 0
        self._epoch = 0
        self._bpb = self._def_bpb
        self._bar_anchor = 0
        self._bar_conf = 0.0
        self._bar_votes = 0
        self._phrase_bars = self._def_phrase
        self._phrase_conf = 0.0
        self._ph_hist = np.zeros(16)
        self._ph_bars = 0
        self._ph_pick: Optional[tuple[int, int]] = None
        self._miss_run = 0
        self._beat_strengths: collections.deque = collections.deque(maxlen=16)
        self._dbn_k = -(1 << 62)                    # dbn: the last of the tracker's beats the bar code has had
        self._dbn_m = -(1 << 62)                    # ... the last beat of the mapped line it has had (the octave map)
        self._dbn_K = -(1 << 62)                    # ... and its number in the published count (m + _off)
        # dbn: the octave map. The tracker's beat k is mapped beat _om_m + (k - _om_k) x _om_f (whole for every k at
        # factor >= 1, the in-between ones interpolated on the line; at 0.5 every other k); mapped beat m is published
        # beat m + _off. Factor 1 is no map (identity until the first correction).
        self._om_f = 1.0
        self._om_k = 0
        self._om_m = 0
        self._om_t: Optional[float] = None          # the tracker's time when the factor became what it is (not 1)
        self._pub_base = 0.0                        # dbn: the tracker's bpm the published line last followed
        self._pub_real = False                      # dbn: the published grid was gated (beat|dmx) since its epoch
        self._pub_far = 0                           # dbn: line updates in a row DBN_NEW_TEMPO off the tempo followed
        self._pub_cold = False                      # dbn: the room went quiet: the next grid gated is a new one
        self._trk = self._make_tracker() if self._estimator == "dbn" else None
        self.tg.reset()
        self._pk.reset()
        self._pk.L = 0.0
        self._pk.L_floor = 0.0
        self._lane_reset(self._lane_fs or 48000, 0, None)

    def _make_tracker(self) -> Optional[beatdbn.DbnTracker]:
        """The dbn estimator's tracker for this activation: best2's settings, the activation's net count (it picks
        the conf weights), its lag (madmom's tuned 15 ms; another activation's own) and floor. None, and the legacy
        estimator from here on, if it can't be built."""
        act = self.act
        name = getattr(act, "name", "?")
        nets = int(getattr(act, "nets", 0) or 0)
        over = {"nets": nets or beatdbn.SETTINGS["nets"]}
        if name != "madmom":
            over["lag_ms"] = float(getattr(act, "lag_s", 0.0)) * 1000.0
        try:
            trk = beatdbn.DbnTracker(beatdbn.settings(**over), act_floor=float(getattr(act, "floor", 0.0)))
        except Exception:                               # degrade: the legacy estimator
            log.exception("the dbn beat estimator could not be built: the legacy estimator instead")
            self._estimator = "legacy"
            self._counts["dbn_failed"] += 1
            return None
        self._dbn_source = f"{name}-dbn{nets}" if name == "madmom" else f"{name}-dbn"
        return trk

    def process(self, stream: str, n0: int, t0_host_s: float, pcm: np.ndarray, fs: int, flags: int) -> None:
        """contract.OnAudio: one delivered block of the active stream."""
        c0 = time.perf_counter()
        try:
            self._process(n0, t0_host_s, pcm, int(fs), int(flags))
        except Exception:                               # never take show-rx down
            self._error()
        frames = pcm.shape[0] if pcm.ndim else 0
        if frames and fs:
            ms = (time.perf_counter() - c0) * 1000.0
            self._blk.append(ms)
            if not int(flags) & AUDIO_GAP:              # per 10 ms of real audio: a skipped hole would read as free
                self._cost.append(ms / (frames / fs / 0.01))

    def tick(self, now_s: float) -> None:
        """on_tick: show-beat's results, coasting without audio, corrections, and the 10 Hz music.clock."""
        try:
            self._drain()
            if self._drain_beats():
                self._emit(now_s)
            if self._t_known != self._t_known_seen:
                self._t_known_seen = self._t_known
                self._w_known = now_s
                self._no_audio = False
            elif self._w_known is not None and now_s - self._w_known > NO_AUDIO_S:
                self._no_audio = True
                if self._trk is not None:               # dbn: "free", gate none, the line's beats pass as misses
                    trk = self._trk
                    k0, b0 = trk.key(), self._bar_key()
                    trk.coast(self._t_known + (now_s - self._w_known) - 0.5)
                    self._dbn_missed(now_s)             # (pivots at the consumers' now: the coast's beats lag it)
                    if trk.events:
                        self._dbn_beats()
                    if trk.key() != k0 or self._bar_key() != b0:
                        self._emit(now_s)
                elif self._grid:                        # keep counting beats (as misses) on tick time
                    t_eff = self._t_known + (now_s - self._w_known) - 0.5
                    if self._run_beats(t_eff):
                        self._emit(now_s)
                    self._update_state()
            if now_s >= self._next_hb - 1e-9:            # the 10 Hz heartbeat keeps its own schedule
                self._next_hb = max(self._next_hb + self._pub_period, now_s - self._pub_period / 2)
                self._emit(now_s)
        except Exception:
            self._error()

    def stop(self, timeout: float = 1.0) -> None:
        """Ends show-beat (a sentinel, then a join of at most ``timeout``) or the beat process (EOF, ``timeout``,
        SIGTERM, ``timeout``, SIGKILL; reaped, every fd closed); any thread, idempotent, after show-rx has stopped
        (Show.stop's order). Audio that still arrives runs the activation inline (degrade, never refuse)."""
        bt = self._bt
        if bt is None:
            return
        self._bt_stopped = True
        bt.stop(timeout)

    def wait_beat(self, timeout: float = 5.0) -> bool:
        """Blocks until show-beat or the process has computed every hop sent so far (their results then wait for the
        next ``process`` or ``tick``). For offline runs and tests only: it waits, so never call it on show-rx live.
        False on timeout or with the other side gone; True at once when the activation runs inline."""
        bt = self._bt
        if bt is None:
            return True
        end = time.monotonic() + timeout
        while bt.inflight > 0:
            if not bt.alive or (bt.respawns and not bt.ready) or time.monotonic() > end:
                return False
            bt.wait(0.002)
        return True

    def wait_beat_ready(self, timeout: float = 30.0) -> bool:
        """Blocks until the beat process has said hello, so the next hop goes to it (show-rx's side adopts it here).
        For offline runs and tests only. True at once inline or threaded; False on timeout."""
        bt = self._bt
        if bt is None or not bt.respawns:
            return True
        return bt.wait_ready(timeout)

    def beat_pid(self) -> Optional[int]:
        """The beat process's pid while one runs (from its spawn, before its hello), else None (mllink adds its CPU
        to show_service's); any thread."""
        bt = self._bt
        return bt.pid if bt is not None else None

    # ---- public: any thread (reads the last published body, swapped by reference; never a lock) ---------------------
    def snapshot(self) -> Optional[ClockData]:
        """The last published body."""
        return self._snapshot

    def beat_at(self, t_s: float) -> float:
        """The published beat (float) at host time ``t_s``; NaN before the first grid."""
        d = self._snapshot
        return beat_at(d, t_s) if d is not None else math.nan

    def next_beat(self, t_s: float, lead_s: float = 0.0, *, gate: str = "beat") -> Optional[float]:
        """The acoustic time of the first beat whose fire time (``t_k - lead_s``) is at or after ``t_s``. Returns None
        while the clock is below ``gate``: ``"beat"`` (the default, everything beat-locked), ``"dmx"`` (beat-locked
        on DMX only) or ``"none"`` (any grid). This is artnet_out's ``BeatClock``."""
        k = self.next_beat_k(t_s, lead_s, gate=gate)
        return None if k is None else k[1]

    def next_beat_k(self, t_s: float, lead_s: float = 0.0, *, gate: str = "none") -> Optional[tuple[int, float]]:
        """``(k, t_k)``: as ``next_beat``, with the beat's number (for bar and phrase position)."""
        d = self._snapshot
        if d is None or _GATE_RANK.get(d["gate"], 0) < _GATE_RANK.get(gate, 0):
            return None
        return next_beat(d, t_s, lead_s)

    def set_level(self, gate: Optional[float], t_s: float) -> None:
        """features.gate for the block about to be processed (show-rx, before ``process``; None: unknown, no change).
        Quiet under QUIET_GATE_OFF for QUIET_AFTER_S, until back over QUIET_GATE_ON (the constants' comment)."""
        if gate is None or not isinstance(gate, (int, float)) or math.isnan(gate):
            return
        if self._quiet:
            if gate >= QUIET_GATE_ON:
                self._quiet, self._quiet_below = False, None
                self._counts["quiet_ends"] += 1
                self._act_reset()                       # madmom starts clean on the music, not mid-state
                if self._trk is not None:
                    self._trk.set_quiet(False, t_s)     # (its forward state starts over too)
                else:
                    self._update_state()
                self._emit(t_s)
            return
        if gate >= QUIET_GATE_OFF:
            self._quiet_below = None
            return
        if self._quiet_below is None:
            self._quiet_below = t_s
        elif t_s - self._quiet_below >= QUIET_AFTER_S:
            self._quiet = True
            self._counts["quiet_starts"] += 1
            if self._trk is not None:
                self._trk.set_quiet(True, t_s)          # gate none at once, a line reads "holding"
            else:
                if self._state == "locked":
                    self._state = "holding"
                self._gate, self._gate_t, self._gate_up = "none", t_s, False
            self._emit(t_s)

    def _act_reset(self) -> None:
        if self._off_rx():
            self._bt_epoch += 1
            self._bt_splice = False
            self._bt.send_reset(self._bt_epoch)
        else:
            self._reset_act()
            self._act_splice = False
        self._pk.reset()

    def _reset_act(self) -> None:
        """show-rx resets its own activation (inline): the next hop's frame is ``fresh`` for the dbn tracker."""
        self.act.reset()
        self._act_fresh = True

    def _off_rx(self) -> bool:
        """Do hops leave show-rx now (show-beat, or the process after its hello)?"""
        bt = self._bt
        return bt is not None and (not bt.respawns or self._bt_via)

    @property
    def beats_per_bar(self) -> int:
        d = self._snapshot
        return d["beats_per_bar"] if d is not None else self._def_bpb

    def state(self) -> dict:
        """For /api/v1/state (__main__ snapshots it every 0.5 s on show-rx): the last body plus ``stats()``."""
        return {"clock": self._snapshot, "stats": self.stats()}

    def apply_integer_correction(self, beat_is_one: Optional[int] = None, bar_len: Optional[int] = None,
                                 octave: float = 1.0) -> None:
        """The slow lane's vote (Beat This!): which published beat is a one, the bar length, the tempo octave
        (1, 2 = the beat is twice as fast, 0.5). Integers only; checked, gated and applied at the next bar line under
        the new hypothesis on show-rx (module docstring, "Corrections"); any thread."""
        self._inbox.append(("corr", beat_is_one, bar_len, octave))

    def observe_hit(self, t_s: float, band: str, strength: float = 1.0) -> None:
        """A dsp hit (``music.hit``): low-band hits near an activation peak refine its time and the lag (legacy; the
        dbn tracker keeps them for its ``hits_w``, off in best2); any thread."""
        if band in ("sub", "bass"):
            self._hitq.append((float(t_s), float(strength)))
            trk = self._trk
            if trk is not None:
                trk.hit(t_s, strength)

    def tap(self, t_s: float) -> None:
        """Show console Tap at host time ``t_s``; any thread."""
        self._inbox.append(("tap", float(t_s)))

    def half(self) -> None:
        self._inbox.append(("oct", 0.5))

    def double(self) -> None:
        self._inbox.append(("oct", 2.0))

    def one(self, t_s: float) -> None:
        """"Now is the one": the beat nearest ``t_s`` starts a bar; any thread."""
        self._inbox.append(("one", float(t_s)))

    def stats(self) -> dict:
        c = _arr(self._cost)
        b = _arr(self._blk)
        bt = self._bt
        k = self._counts
        beat = {"on": bt is not None and bt.alive, "queued": self._bt_put - self._bt_got,
                "drops": k.get("beat_drops", 0), "lag_ms_p95": _pct(_arr(self._bt_lag), 95, 3),
                "act_ms_p50": _pct(_arr(bt.cost), 50, 3) if bt is not None else 0.0,
                "act_ms_p95": _pct(_arr(bt.cost), 95, 3) if bt is not None else 0.0,
                "act_errors": bt.errors if bt is not None else 0,
                "mode": "inline" if bt is None else ("process" if bt.respawns else "thread"),
                "via": self._off_rx(),
                "pid": None, "state": "inline", "restarts": 0, "failures_in_row": 0, "last_exit": None, "cpu": None,
                "inline_hops": k.get("beat_inline_hops", 0), "switches": k.get("beat_proc_switches", 0),
                "stalls": k.get("beat_stalls", 0), "pipe_full": k.get("beat_pipe_full", 0),
                "proc_lost": k.get("beat_proc_lost", 0)}
        if bt is not None:
            s = bt.snapshot()
            beat.update({key: s.get(key) for key in ("pid", "state", "restarts", "failures_in_row", "last_exit",
                                                      "cpu")})
        out = {"source": self.act.name, "state": self._state, "gate": self._gate, "conf": round(self._conf, 3),
               "bpm": round(60.0 / self._T, 3) if self._grid else 0.0, "epoch": self._epoch,
               "lag_adj_ms": round(self._lag_adj * 1000.0, 2), "agc_db": round(self._agc_db, 1),
               "cost_ms_per_10ms": {"p50": _pct(c, 50), "p95": _pct(c, 95), "max": _pct(c, 100)},
               "block_ms": {"p50": _pct(b, 50), "p95": _pct(b, 95), "max": _pct(b, 100)},
               "beat_thread": beat, "last_correction": self._corr_last,
               "errors": self._err_n, **dict(self._counts), "estimator": self._estimator}
        trk = self._trk
        if trk is not None:                             # the line's labels are the tracker's; its counts nest
            ts = trk.stats()                            # (ts["epoch"]: the tracker's own re-phases; epoch: published)
            out.update(source=self._dbn_source, state=ts["state"], gate=ts["gate"], conf=ts["conf"], bpm=ts["bpm"],
                       dbn=ts, oct_factor=self._om_f,
                       oct_age_s=(round(trk.t_last - self._om_t, 1) if self._om_t is not None else None))
        return out

    # ---- rhythm lane -------------------------------------------------------------------------------------------------
    def _lane_reset(self, fs: int, n0: int, t0: Optional[float]) -> None:
        if fs != self._lane_fs or self._rs is None:
            self._lane_fs = fs
            if fs != FS:
                import soxr
                self._rs = soxr.ResampleStream(fs, FS, 1, dtype="float32", quality=self._rs_quality)
            else:
                self._rs = False
        elif self._rs:
            self._rs.clear()
        self._n_origin = n0                             # input index of lane sample 0 (a float after a skipped hole)
        self._n_expect = n0
        self._m_done = 0
        self._hopfill = 0
        self._last_n0 = n0
        self._last_t0 = t0 if t0 is not None else 0.0
        self._suppress_until = (t0 + SUPPRESS_S) if t0 is not None else -1e9
        self._zq.clear()                                # the old segment's silent frames go with its results
        self._agc_sp, self._agc_sn = 0.0, 0             # n restarts: so do the AGC's chunks (its level carries on)
        if self._off_rx():                              # the other side resets the activation when it gets there;
            self._bt_epoch += 1                         # the results still in flight belong to the old segment
            self._bt_splice = False
            self._bt.send_reset(self._bt_epoch)
        else:
            self._reset_act()
            self._act_splice = False
        self._pk.reset()

    def _process(self, n0: int, t0: float, pcm: np.ndarray, fs: int, flags: int) -> None:
        self._drain()
        changed = self._drain_beats()                   # before a segment reset: those frames are the old segment's
        frames = pcm.shape[0]
        if frames == 0 or fs <= 0:
            if changed:
                self._emit(t0)
            return
        if (flags & AUDIO_SEGMENT) or fs != self._lane_fs or n0 != self._n_expect:
            self._counts["segments"] += 1
            self._lane_reset(fs, n0, t0)
        self._n_expect = n0 + frames
        self._last_n0, self._last_t0 = n0, t0
        if flags & AUDIO_GAP:                           # zero-fill, alone in its block (contract): never read or written
            self._counts["gap_blocks"] += 1
            self._suppress_until = max(self._suppress_until, t0 + frames / fs + SUPPRESS_S)
            if self._gap_skip and frames * FS > HOP * fs:   # longer than a hop: nothing to compute (see _gap)
                changed |= self._gap(n0 + frames, fs)
                if changed:
                    self._emit(t0 + frames / fs)
                return
            if self._zbuf.shape[0] < frames:
                self._zbuf = np.zeros(frames, np.float32)
            x = self._zbuf[:frames]                     # a hop or less: zeros through the lane (madmom's state runs on)
        else:
            if flags & AUDIO_RESUME:                    # one packet's span, however long the block
                self._suppress_until = max(self._suppress_until,
                                           t0 + min(frames, RESUME_MAX_FRAMES) / fs + SUPPRESS_S)
            x = self._lane_in(pcm, n0, fs)
        y = self._rs.resample_chunk(x) if self._rs else x
        pos, ny = 0, y.shape[0]
        hb = self._hopbuf
        while pos < ny:
            take = min(HOP - self._hopfill, ny - pos)
            hb[self._hopfill:self._hopfill + take] = y[pos:pos + take]
            self._hopfill += take
            pos += take
            if self._hopfill == HOP:
                self._hopfill = 0
                changed |= self._hop(hb)
        if changed:
            self._emit(t0 + frames / fs)

    def _lane_in(self, pcm: np.ndarray, n0: int, fs: int) -> np.ndarray:
        """The block's mono mix, through the AGC, gained, in a preallocated buffer (``pcm`` is only read). The AGC
        steps once per AGC_CHUNK frames, aligned on ``n``, and a sample takes the gain of the chunks before its own
        (a chunk's power adds up across calls): so however the audio is cut into blocks, every sample gets the same
        gain (to float noise), and one 4096-frame block gets the gains its 32 packets would."""
        frames = pcm.shape[0]
        if self._gbuf.shape[0] < frames:                # a longer block than any yet: grow once
            self._gbuf = np.zeros(frames, np.float32)
            self._mbuf = np.zeros(frames, np.float32)
        if pcm.ndim == 1:
            x = pcm
        elif pcm.shape[1] == 1:
            x = pcm[:, 0]
        else:
            x = np.mean(pcm, axis=1, out=self._mbuf[:frames])
        if x.dtype != np.float32:
            x = np.asarray(x, np.float32)
        out = self._gbuf[:frames]
        if not self._agc_chunks:                        # the rollback: one step per block, its new gain on all of it
            self._agc(float(np.dot(x, x)) / frames, frames / fs)
            np.multiply(x, np.float32(10.0 ** (self._agc_db / 20.0)), out=out)
            return out
        pos, n = 0, n0
        while pos < frames:
            take = min(frames - pos, AGC_CHUNK - n % AGC_CHUNK)    # to the next chunk boundary
            seg = x[pos:pos + take]
            np.multiply(seg, self._agc_g, out=out[pos:pos + take])
            self._agc_sp += float(np.dot(seg, seg))
            self._agc_sn += take
            pos += take
            n += take
            if n % AGC_CHUNK == 0:                      # a chunk is complete: one AGC step, a new gain from here
                self._agc(self._agc_sp / self._agc_sn, self._agc_sn / fs)
                self._agc_sp, self._agc_sn = 0.0, 0
                self._agc_g = np.float32(10.0 ** (self._agc_db / 20.0))
        return out

    def _agc(self, p: float, dt: float) -> None:
        """Slow, bounded pre-gain for the tracker lane only (§4): aim AGC_TARGET_DB RMS; up tau 10 s, down 3 s;
        -20..+36 dB; frozen below -65 dBFS. The first fix comes from the first 0.5 s of signal, not from 0 dB. One
        step: ``p`` the mean power of the last ``dt`` s (a chunk)."""
        if not self._agc_init:
            if 10.0 * math.log10(p + 1e-20) < -65.0:
                return
            self._agc_acc_p += p * dt
            self._agc_acc_t += dt
            if self._agc_acc_t < 0.5:
                return
            self._agc_pow = self._agc_acc_p / self._agc_acc_t
            self._agc_db = min(36.0, max(-20.0, AGC_TARGET_DB - 10.0 * math.log10(self._agc_pow + 1e-20)))
            self._agc_init = True
            return
        k = self._agc_k
        if k[0] != dt:                                  # the three smoothing steps, once per chunk length
            k = self._agc_k = (dt, 1.0 - math.exp(-dt / 0.4), 1.0 - math.exp(-dt / 10.0), 1.0 - math.exp(-dt / 3.0))
        self._agc_pow += (p - self._agc_pow) * k[1]
        lvl = 10.0 * math.log10(self._agc_pow + 1e-20)
        if lvl < -65.0:
            return                                       # frozen in silence
        want = min(36.0, max(-20.0, AGC_TARGET_DB - lvl))
        err = want - self._agc_db
        self._agc_db += err * (k[2] if err > 0 else k[3])

    def _gap(self, n_next: int, fs: int) -> bool:
        """A zero-filled hole longer than one hop (AUDIO_GAP), computing nothing. soxr is cleared (the ~14 ms of the
        sound before the hole that it still held goes too: all of it inside the hole's peak suppression). The lane
        moves on to ``n_next`` (the next real sample): its 44.1 kHz position takes a whole number of zero samples
        and the origin keeps the fraction, so every later time is exact. Each hop it completes is a silent frame
        (as a quiet room's: no activation; the partial hop's sound goes with it), and the next hop that reaches the
        activation resets it first (a splice: madmom never sees the jump). Never a lane reset: that would also
        reset the peaks and the epoch."""
        if self._rs:
            self._rs.clear()
        m_next = (n_next - self._n_origin) * FS / fs
        m = max(self._m_done + self._hopfill, math.ceil(m_next - 1e-6))
        hops, rem = divmod(m - self._m_done, HOP)
        changed = False
        for _ in range(hops):
            changed |= self._silent(self._hop_t())
        if hops:
            self._hopbuf[:rem] = 0.0
        else:
            self._hopbuf[self._hopfill:rem] = 0.0
        self._hopfill = rem
        self._n_origin = n_next - m * fs / FS
        self._counts["gap_hops_skipped"] += hops
        if self._off_rx():
            self._bt_splice = True
        else:
            self._act_splice = True
        return changed

    def _hop_t(self) -> float:
        """The next hop's time, from the newest block's times: the input position of its last sample, as host
        seconds (before the activation's lag)."""
        self._m_done += HOP
        fs = self._lane_fs
        n_in = self._n_origin + self._m_done * fs / FS
        return self._last_t0 + (n_in - self._last_n0) / fs

    def _hop(self, hop: np.ndarray) -> bool:
        """One full hop (``hop`` is the reused buffer). Its time is fixed here (``_hop_t``). Inline, the frame runs at
        once; off show-rx, the hop is sent (or dropped under back-pressure) and its frame runs when it comes back."""
        t_raw = self._hop_t()
        bt = self._bt
        changed = False
        if bt is not None and self._bt_stopped and not bt.alive:
            changed = self._inline_again()
            bt = None
        if self._quiet:                                 # a quiet room: silence, and madmom isn't run
            return self._silent(t_raw) or changed
        if bt is None:
            return self._inline(hop, t_raw) or changed
        if bt.respawns:                                 # the process: inline until its hello, and whenever it's lost
            if self._bt_via and (not bt.ready or bt.gen != self._bt_gen):     # gone (or replaced unseen)
                changed |= self._to_inline("beat_proc_lost")
            if not bt.ready:
                self._counts["beat_inline_hops"] += 1
                return self._inline(hop, t_raw) or changed
            if not self._bt_via:
                changed |= self._to_proc()
            elif self._bt_put > self._bt_got and time.perf_counter() - bt.last_progress > BEAT_STALL_S:
                bt.request_kill()                       # wedged: the supervisor kills it and respawns
                changed |= self._to_inline("beat_stalls")
                self._counts["beat_inline_hops"] += 1
                return self._inline(hop, t_raw) or changed
        if self._bt_put - self._bt_got >= BEAT_Q_MAX:
            changed |= self._drain_beats()              # whatever came back since this process() began
            if self._bt_put - self._bt_got >= BEAT_Q_MAX:
                if not bt.respawns and not bt.alive:    # show-beat is gone: run inline from here (degrade)
                    changed |= self._inline_again()
                    return self._inline(hop, t_raw) or changed
                self._counts["beat_drops"] += 1
                self._bt_splice = True
                return changed
        if self._bt_splice:                             # dropped hops before this one: madmom never sees a splice
            self._bt_splice = False
            bt.send_reset(self._bt_epoch)
        r = bt.send_hop(self._bt_epoch, self._bt_put + 1, hop, t_raw, time.perf_counter())
        if r == 0:
            self._bt_put += 1
            return changed
        if r == 1:                                      # the process's pipe is full: dropped like the cap's
            self._counts["beat_drops"] += 1
            self._counts["beat_pipe_full"] += 1
            self._bt_splice = True
            return changed
        changed |= self._to_inline("beat_proc_lost")    # 2: the process has gone
        self._counts["beat_inline_hops"] += 1
        return self._inline(hop, t_raw) or changed

    def _inline(self, hop: np.ndarray, t_raw: float) -> bool:
        if self._act_splice:                            # after a skipped hole: the activation starts clean
            self._act_splice = False
            self._reset_act()
            self._pk.reset()
        a = self.act.process_hop(hop)
        self._f_fresh, self._act_fresh = self._act_fresh, False
        return self._frame(a, t_raw)

    def _silent(self, t_raw: float) -> bool:
        """A frame with no activation (a quiet room, a skipped hole): at once, or behind the hops still in flight,
        so frames reach the peaks and the tempogram in time order."""
        if self._bt_put > self._bt_got:
            self._zq.append((self._bt_put, t_raw))
            return False
        self._f_silent = True
        return self._frame(0.0, t_raw)

    def _flush_silent(self, upto: Optional[int] = None) -> bool:
        """Run the waiting silent frames whose hops before them have come back (``upto``: that seq), or all."""
        zq = self._zq
        changed = False
        while zq and (upto is None or zq[0][0] <= upto):
            t = zq.popleft()[1]
            try:
                self._f_silent = True
                changed |= self._frame(0.0, t)
            except Exception:                           # never take show-rx down; the next frame goes on
                self._error()
        return changed

    def _to_proc(self) -> bool:
        """The process has said hello: hops go to it from this one (a clean start there, and for the peaks)."""
        changed = self._flush_silent()
        self._bt_via = True
        self._bt_gen = self._bt.gen                     # this child: another one means this one was lost
        self._bt_epoch += 1
        self._bt_put = self._bt_got = 0
        self._bt_seq_next = 1
        self._bt_splice = self._act_splice = False
        self._bt_fresh = False                          # (the child's reset comes back as a marker: fresh then)
        self._bt.send_reset(self._bt_epoch)
        self._pk.reset()
        self._counts["beat_proc_switches"] += 1
        return changed

    def _to_inline(self, why: str) -> bool:
        """The process is lost (``beat_proc_lost``) or stalled (``beat_stalls``): what came back runs, the hops still
        out there are given up, and the activation runs on show-rx from this hop, from a clean start, until the
        respawned process says hello."""
        changed = self._drain_beats()
        changed |= self._flush_silent()
        self._counts[why] += 1
        self._bt_via = False
        self._bt_epoch += 1                             # anything that still came from it is stale
        self._bt_put = self._bt_got = 0
        self._bt_seq_next = 1
        self._bt_splice = self._act_splice = False
        self._bt_fresh = False
        self._reset_act()
        self._pk.reset()
        if why == "beat_stalls":
            log.warning("the beat process stalled (%.0f s with hops in flight): inline on show-rx until it is back",
                        BEAT_STALL_S)
        else:
            log.warning("the beat process has gone: inline on show-rx until it is back")
        return changed

    def _inline_again(self) -> bool:
        """show-beat has exited (``stop()``, or it died), or the process was stopped: drain what it left, then run
        the activation on show-rx for good."""
        changed = self._drain_beats()
        changed |= self._flush_silent()
        self._bt = None
        self._bt_via = False
        self._bt_put = self._bt_got = 0
        self._bt_seq_next = 1
        self._bt_splice = self._act_splice = False
        self._bt_fresh = False
        self._reset_act()                               # a splice either way
        self._pk.reset()
        if self._bt_stopped:
            self._counts["beat_inline_after_stop"] += 1
        else:
            self._counts["beat_thread_died"] += 1
            log.warning("show-beat has died: the beat activation runs on show-rx from now on")
        return changed

    def _drain_beats(self) -> bool:
        """show-rx: run the rest of each frame show-beat or the process has finished, in order (never waits), and the
        silent frames that waited behind them. True if the published line was re-anchored."""
        bt = self._bt
        if bt is None:
            return False
        bt.poll()
        out = bt.out
        zq = self._zq
        changed = False
        perf = time.perf_counter
        while out:
            item = out.popleft()
            if item[0] != "a":                          # a reset marker, reached: the peak picker starts over too
                if item[1] == self._bt_epoch:
                    self._pk.reset()
                    self._bt_fresh = True               # ... and the next activation is the LSTM's first (dbn)
                continue
            _, ep, seq, a, t_raw, t_put = item
            self._bt_got += 1
            self._bt_lag.append((perf() - t_put) * 1000.0)
            if seq != self._bt_seq_next:                # never expected: the queue, the pipe and the deque are FIFO
                self._counts["beat_order"] += 1
            self._bt_seq_next = seq + 1
            if ep != self._bt_epoch:
                self._counts["beat_stale"] += 1
            elif a is None:                             # the activation failed (the other side logged it)
                self._err_n += 1
                if self._trk is not None:               # dbn: a silent frame, so the line still judges its beats
                    try:
                        self._f_silent = True
                        changed |= self._frame(0.0, t_raw)
                    except Exception:
                        self._error()
            else:
                try:
                    self._f_fresh, self._bt_fresh = self._bt_fresh, False
                    changed |= self._frame(a, t_raw)
                except Exception:                       # never take show-rx down; the next frame goes on
                    self._error()
            if zq and zq[0][0] <= seq:
                changed |= self._flush_silent(seq)
        if zq and self._bt_got >= self._bt_put:
            changed |= self._flush_silent()
        return changed

    def _frame(self, a: float, t_raw: float) -> bool:
        """The rest of a frame, on show-rx: activation ``a`` of the hop whose last sample sounds at ``t_raw``. Its
        ``silent``/``fresh`` flags (dbn) are set by the caller just before (``_f_silent``, ``_f_fresh``), so this
        keeps its signature for the legacy path, the tests' spies and subclasses."""
        if self._trk is not None:
            return self._frame_dbn(a, t_raw)
        t = t_raw - self.act.lag_s - self._lag_adj
        tg = self.tg
        if tg.filled and not math.isnan(tg.t_last):     # keep the tempogram on one time axis across segments
            gap = t - tg.t_last
            if abs(gap - 1.0 / FPS) > 0.5 / FPS:
                miss = int(round(gap * FPS)) - 1
                if gap < 0 or miss >= tg.N:
                    tg.reset()
                else:
                    for i in range(miss):
                        tg.push(0.0, tg.t_last + 1.0 / FPS)
        if (self._state != "locked" and t - self._last_locked_t > LEVEL_GRACE_S
                and (self._L_fade > 0.0 or self._pk.L_floor > 0.0)):
            self._L_fade *= self._lvl_dec               # the last song's level stops holding a quieter one down
            self._pk.L_floor *= self._lvl_dec
        v, pk = self._pk.push(a, t, self.act.floor)
        tg.push(v, t)
        self._counts["frames"] += 1
        if pk is not None and pk[0] > self._suppress_until:
            self._peaks.append(pk)
            self._recent.append(pk)                     # the last 8 s, unpruned: seed fits and the cold-search test
            while self._recent[0][0] < pk[0] - 8.0:
                self._recent.popleft()
            self._counts["peaks"] += 1
            if not self._grid:
                while self._peaks and self._peaks[0][0] < pk[0] - 8.0:
                    self._peaks.popleft()
        self._t_known = t - _Peaks.W / FPS
        changed = False
        if not self._grid and self._counts["frames"] % 25 == 0:
            changed |= self._try_seed()
        if self._grid:
            changed |= self._run_beats(self._t_known)
        if self._no_audio:
            self._no_audio = False
            self._update_state()
        return changed

    # ---- the dbn estimator (module docstring, "Estimators") ----------------------------------------------------------
    def _bar_key(self) -> tuple:
        """dbn: what the body carries besides the tracker's key: the bars, and the published line and epoch."""
        return (self._bpb, self._bar_anchor, self._phrase_bars, round(self._bar_conf, 4), round(self._phrase_conf, 4),
                self._confirmed, self._pub_k, self._pub_t, self._pub_bpm, self._epoch)

    def _frame_dbn(self, a: float, t_raw: float) -> bool:
        """A frame for the tracker, with the flags its caller set (``silent``: no activation; ``fresh``: the LSTM's
        first after a reset); when the tracker's line moved, the published line follows it (``_dbn_publish``); then
        its judged beats to the bar and phrase code. True if the published body moved."""
        silent, fresh = self._f_silent, self._f_fresh and not self._f_silent
        self._f_silent = self._f_fresh = False
        trk = self._trk
        k0, b0 = trk.key(), self._bar_key()
        if self._no_audio:                              # audio again: no longer coasting
            self._no_audio = False
            trk.set_no_audio(False, t_raw - trk.lag_s)
        ln = trk.line
        trk.frame(a, t_raw, silent=silent, fresh=fresh)
        self._t_known = t_raw - self.act.lag_s
        self._counts["frames"] += 1
        if trk.line != ln:
            self._dbn_publish(trk.t_last)
        else:
            self._dbn_missed(trk.t_last)
        if trk.quiet:                                   # a quiet room: the grid gated after it is a new one
            if self._pub_ok:
                self._pub_cold, self._pub_real = True, False
        elif trk.gate != "none":
            if self._pub_cold:
                self._dbn_cold_grid()
            self._pub_real = True
        if trk.events:
            self._dbn_beats()
        return trk.key() != k0 or self._bar_key() != b0

    def _dbn_missed(self, u: float) -> None:
        """A beat of the tracker's line passed with no DBN beat (a miss among its events): the published line's slew
        is judged again at ``u``, as legacy judges it after every beat, hit or miss; otherwise a slew set to close
        the error in one beat (or half of it per beat) would run on, overshooting, until the next DBN beat."""
        if any(e[1] == "miss" for e in self._trk.events):
            self._dbn_publish(u, resync=True)

    def _dbn_cold_grid(self) -> None:
        """The first beat or dmx gate after a quiet room (a new song, or the same one after a pause): a new grid,
        epoch + 1 once, unless a new grid on the way had it already (``_dbn_publish``). The line itself followed the
        tracker ungated meanwhile; the bars keep their anchor, bar_conf drops to DBN_NEW_BAR_CONF; a waiting
        correction is stale. An octave map ends here: the line is the tracker's own again (a new grid on it)."""
        self._pub_cold = False
        self._human_oct_t = None                        # a new song: the slow lane may set its octave again
        if self._om_f != 1.0:
            self._om_clear("cold")
            self._dbn_publish(self._trk.t_last, explicit=True)     # (epoch + 1, the numbering break)
        else:
            self._epoch += 1
            self._numbering_break(self._upcoming())
        if self._confirmed:
            self._bar_conf = min(self._bar_conf, DBN_NEW_BAR_CONF)
            self._bar_votes = 0
        self._counts["pub_cold_grids"] += 1

    def _dbn_beats(self) -> None:
        """The tracker's judged beats, in order and each once, numbered in the published count (its beat k is
        published beat ``k + _off``), through what ``_process_beat``/``_after_beat`` do for the legacy estimator's:
        the miss run and entries, the bars' start (DBN_CONFIRM_N beats on a line: that beat is a one, bar_conf 0.1;
        once started the bars are kept), the phrase evidence (``_bar_phrase``, the beat's activation peak as its
        strength) and the waiting corrections (``_apply_pending``). The tracker's re-phases and new lines are no break
        in the published count; the published line's new grids are (``_dbn_publish``)."""
        trk = self._trk
        while trk.events:
            k, kind, strength = trk.events.popleft()
            self._dbn_k = max(self._dbn_k, k)
            for m, knd in self._om_beats(k, kind):
                K = m + self._off
                self._dbn_m = max(self._dbn_m, m)
                if K <= self._dbn_K:
                    continue
                self._dbn_K = K
                entry = knd == "hit" and self._miss_run >= self._bpb
                self._miss_run = 0 if knd == "hit" else self._miss_run + 1
                if (not self._confirmed and knd not in ("miss", "interp") and trk.n_line >= DBN_CONFIRM_N
                        and trk.state == "locked"):
                    self._confirmed = True
                    self._bar_anchor = K
                    self._bar_conf = 0.1
                    self._counts["confirmed"] += 1
                if self._confirmed:
                    self._bar_phrase(m, knd, strength, entry)
                self._apply_pending(m)

    # -- the octave map (module docstring, "Estimators"; README S4.15 "The octave map")
    def _om_line(self) -> Optional[tuple[int, float, float]]:
        """The tracker's line through the octave map: (m, t_m, T / factor), m a whole mapped beat; the tracker's own
        line (the same tuple) at factor 1 with no offset; None without a line."""
        ln = self._trk.line
        if ln is None or (self._om_f == 1.0 and self._om_k == self._om_m):
            return ln
        k0, t0, T = ln
        x = self._om_m + (k0 - self._om_k) * self._om_f
        m = math.floor(x + 1e-9)
        Tm = T / self._om_f
        return (m, t0 - (x - m) * Tm, Tm)

    def _om_floor(self, k: int) -> int:
        """The mapped beat at or before the tracker's beat ``k`` (exact integers)."""
        f = self._om_f
        if f >= 1.0:
            return self._om_m + (k - self._om_k) * int(f)
        return self._om_m + (k - self._om_k) // int(round(1.0 / f))

    def _om_beats(self, k: int, kind: str) -> tuple:
        """The mapped beats the tracker's judged beat ``k`` gives the bar code, in order, with their kind: itself at
        factor 1; at 2 (4) the in-between beat(s) before it first (``interp``, or ``miss`` before a miss); at 0.5
        (0.25) itself every other (fourth) beat, else none."""
        f = self._om_f
        if f == 1.0:
            return ((self._om_m + k - self._om_k, kind),)
        m = self._om_floor(k)
        if f > 1.0:
            n = int(f)
            between = "miss" if kind == "miss" else "interp"
            return tuple((m - n + j, between) for j in range(1, n)) + ((m, kind),)
        return ((m, kind),) if (k - self._om_k) % int(round(1.0 / f)) == 0 else ()

    def _om_ref(self, m: int, f2: float) -> Optional[tuple[int, int]]:
        """(tracker beat, mapped beat) to refer a map of factor ``f2`` to so that mapped beat ``m`` keeps its time and
        number (``m`` must be a beat of both grids: at factor 1 a tracker beat, at 0.5 the one the halving keeps);
        None when it is not."""
        u = self._om_k + (m - self._om_m) / self._om_f        # m's place on the tracker's line (a beat, or between)
        ki = math.floor(u + 1e-9)
        d = (u - ki) * f2
        if abs(d - round(d)) > 1e-6:
            return None
        return ki, m - int(round(d))

    def _om_switchable(self, K: int, f: float) -> bool:
        """Can an octave ``f`` switch at published beat ``K`` (dbn: a beat of both grids)? Legacy: always."""
        return self._trk is None or self._om_ref(K - self._off, self._om_f * f) is not None

    def _om_bar_wait(self, K: int, f: float, at_bar: Callable[[int], bool]) -> Optional[int]:
        """An octave ``f`` that switches at a bar line (``at_bar``): 0 when the upcoming beat ``K`` is one and a beat
        of both grids; under a map of factor 2 (4) the bar line can be an in-between beat (the tracker re-phased by
        half its beat under the map, or a one landed there), and then the first beat of both grids after it, 1 (to 3)
        beats on; None: not here. Legacy and factor <= 1: 0 at a bar line, as before."""
        if not self._om_switchable(K, f):
            return None
        n = int(self._om_f) if self._trk is not None and self._om_f > 1.0 else 1
        for j in range(n):
            if at_bar(K - j):
                return j if j == 0 or not self._om_switchable(K - j, f) else None
        return None

    def _om_clear(self, why: str) -> None:
        """The map ends (factor 1, referred to the last tracker beat the bar code has had); counted by ``why``:
        contrary (a correction back), joined (the tracker came to the mapped octave itself), jump (a confirmed tempo
        jump of the mapped line), range (it drifted out of the octave range), cold (a new grid after a quiet room),
        tap (a run of taps set the tempo)."""
        if self._om_f == 1.0:
            return
        k = self._dbn_k if self._dbn_k > -(1 << 61) else (self._trk.line[0] if self._trk.line is not None else 0)
        m = self._om_floor(k)
        self._om_k, self._om_m, self._om_f, self._om_t = k, m, 1.0, None
        self._dbn_m = min(self._dbn_m, m) if self._dbn_m > -(1 << 61) else self._dbn_m
        self._counts["oct_maps_cleared"] += 1
        self._counts[f"oct_clear_{why}"] += 1

    def _dbn_publish(self, u: float, explicit: bool = False, resync: bool = False) -> None:
        """The tracker's line moved, at ``u`` (now, as heard): the published line follows it with step 6's discipline
        (README S4.15 "Publishing"). The tracker's beat k is published beat ``k + _off``. A consumer's now is at or
        after ``u``: from ``u`` to ``u + DBN_PIVOT_S`` the new line's count is never behind the old one's.
        - **a slew** (the tracker's tempo within DBN_NEW_TEMPO of the one last followed): the phase error (at most
          half a beat: ``_off`` is the nearest whole beat) closes as a temporary tempo change, by the gate
          (DBN_SLEW): gated beat half of it per beat, at most 10 %; dmx and none all of it per beat (at most 25 %,
          50 %). A faster line keeps the count at ``u``, a slower one at ``u + DBN_PIVOT_S``. The anchor is an integer
          beat near now. No epoch.
        - **a new grid** (``explicit``: an octave or a tap; a tempo jump, which on a grid that was gated must hold
          for DBN_NEW_N line updates in a row, the published line holding meanwhile; the first line): the
          line becomes the tracker's, numbered on so that the count steps forward by less than a beat over that
          window where it can. ``epoch`` + 1 for the first line, a correction, a jump away from a grid that was gated
          since its epoch, or a step of a whole beat; a line that was never gated (the room's noise before the music)
          wanders without one. A waiting slow-lane correction is stale; after a tempo jump the bars keep their anchor
          and bar_conf drops to DBN_NEW_BAR_CONF.
        ``resync`` (``_dbn_missed``): the tracker's line has not moved, a beat of it passed with no DBN beat: only the
        slew is judged again (or the ungated line snaps on); a tempo jump is judged on the line's own updates.
        **The octave map**: "the tracker's line" is it through the map (``_om_line``), its tempo the mapped one. A
        confirmed jump of that tempo ends the map: when the tracker's own tempo is the one followed (it came to the
        mapped octave itself) the line slews on as the tracker's, with no new grid; otherwise a new grid on the
        tracker's own. A mapped tempo out of DBN_OCT_MIN_BPM..DBN_OCT_MAX_BPM ends it too (a new grid)."""
        trk = self._trk
        ln = self._om_line()
        if ln is None or (resync and not self._pub_ok):
            return
        k0, t0, T = ln
        bpm_e = 60.0 / T
        # (a mapped tempo out of the range that is also a jump, such as the tracker at the mapped octave by itself, is
        # judged as a jump below: confirmed, then joined or jump; here the map's own drift out of the range)
        if (self._om_f != 1.0 and not resync and not DBN_OCT_MIN_BPM <= bpm_e <= DBN_OCT_MAX_BPM
                and (explicit or not self._pub_ok or abs(bpm_e / self._pub_base - 1.0) <= DBN_NEW_TEMPO)):
            self._om_clear("range")
            k0, t0, T = ln = self._om_line()
            bpm_e = 60.0 / T
            explicit = True
        gate = "none" if trk.quiet else trk.gate
        if not self._pub_ok:                            # the first line: a cold start
            self._off = 0
            self._set_pub(k0, t0, bpm_e)
            self._pub_base = bpm_e
            self._pub_real = gate != "none"
            self._pub_far = 0
            self._pub_cold = False
            self._epoch += 1
            self._numbering_break(k0)
            self._counts["pub_grids"] += 1
            return
        if gate != "none":
            self._pub_real = True
        far = abs(bpm_e / self._pub_base - 1.0) > DBN_NEW_TEMPO
        if resync and far:                              # (a jump waits for the tracker's own line updates)
            return
        self._pub_far = self._pub_far + 1 if far else 0
        if far and not explicit and self._pub_real and self._pub_far < DBN_NEW_N:
            self._counts["pub_far_waits"] += 1          # not yet: the published line holds (its count goes on)
            return
        if far and not explicit and self._om_f != 1.0:  # the mapped line jumped: the map ends
            joined = abs(60.0 / trk.line[2] / self._pub_base - 1.0) <= DBN_NEW_TEMPO
            self._om_clear("joined" if joined else "jump")
            k0, t0, T = ln = self._om_line()
            bpm_e = 60.0 / T
            if joined:                                  # the tracker's own octave is the one followed: a slew
                far, self._pub_far = False, 0
        if explicit or far:
            w = (u, u + DBN_PIVOT_S)
            off = max(math.ceil(self._pub_beat(t) - (k0 + (t - t0) / T) - 1e-9) for t in w)
            step = max(k0 + (t - t0) / T + off - self._pub_beat(t) for t in w)
            self._off = off
            self._set_pub(k0 + off, t0, bpm_e)
            self._pub_base = bpm_e
            self._pub_far = 0
            if explicit or self._pub_real or step >= 1.0:
                self._epoch += 1
                self._pub_cold = False
                if not explicit and self._pub_real and self._confirmed:
                    self._bar_conf = min(self._bar_conf, DBN_NEW_BAR_CONF)
                    self._bar_votes = 0
            self._pub_real = gate != "none"
            self._numbering_break(k0 + off)
            self._counts["pub_grids"] += 1
            return
        if gate == "none":                              # nothing beat-locked reads it: ahead, it snaps on
            off = int(round(self._pub_beat(u) - (k0 + (u - t0) / T)))
            if all(k0 + (t - t0) / T + off >= self._pub_beat(t) for t in (u, u + DBN_PIVOT_S)):
                if resync and (k0 + off, t0, bpm_e) == (self._pub_k, self._pub_t, self._pub_bpm):
                    return                              # (snapped on already)
                self._off = off
                self._set_pub(k0 + off, t0, bpm_e)
                self._pub_base = bpm_e
                self._counts["pub_snaps"] += 1
                return
        sb, smax = DBN_SLEW.get(gate, DBN_SLEW["none"])
        p = u
        for _ in range(2):                              # the error at the pivot: u, or ahead of it for a slower line
            pp = self._pub_beat(p)
            off = int(round(pp - (k0 + (p - t0) / T)))
            d = k0 + (p - t0) / T + off - pp
            bpm = min(MAX_BPM * 1.1, max(MIN_BPM * 0.9, bpm_e * (1.0 + min(smax, max(-smax, d / sb)))))
            if bpm >= self._pub_bpm or p > u:
                break
            p = u + DBN_PIVOT_S
        if p > u:
            bpm = min(bpm, self._pub_bpm)               # ahead of now only ever slower: the count never goes back
        if resync:
            if abs(bpm / self._pub_bpm - 1.0) < 2e-4:   # the line already goes there: nothing to publish
                return
            self._counts["pub_resyncs"] += 1
        self._off = off
        ka = math.floor(pp)
        self._set_pub(ka, p - (pp - ka) * 60.0 / bpm, bpm)
        self._pub_base = bpm_e
        self._counts["pub_slews"] += 1

    def _dbn_octave(self, f: float) -> bool:
        """``_apply_octave`` for the dbn estimator: the octave map's factor x f from the upcoming beat (it keeps its
        time and number there: ``_apply_pending`` waits for a beat of both grids), and the published line a new grid
        on the mapped line (``_dbn_publish``; the bar anchor moves with the count, so the bar line stays on its beat).
        The tracker is not touched: it tracks on at its own octave, its conf and gate its own, and the map holds until
        a new grid or a contrary correction (factor back to 1). A mapped tempo out of DBN_OCT_MIN_BPM..DBN_OCT_MAX_BPM
        (or a factor past 4 or 1/4) is refused (counted)."""
        trk = self._trk
        ln = trk.line
        f2 = self._om_f * f
        m = self._dbn_m + 1                              # the upcoming beat, mapped
        ref = self._om_ref(m, f2) if ln is not None else None
        if (ref is None or f2 not in DBN_OCT_FACTORS
                or not DBN_OCT_MIN_BPM <= 60.0 / ln[2] * f2 <= DBN_OCT_MAX_BPM):
            self._counts["octave_refused"] += 1
            return False
        off0 = self._off
        if f2 == 1.0:
            self._om_clear("contrary")
        else:
            self._counts["oct_maps_applied"] += 1
        self._om_k, self._om_m = ref
        self._om_f, self._om_t = f2, (None if f2 == 1.0 else trk.t_last)
        self._dbn_publish(trk.t_last, explicit=True)    # beats from m on are the new octave's (a numbering break)
        self._dbn_m = m - 1                              # m is the upcoming beat (an in-between one before it is not)
        self._dbn_K = max(self._dbn_K, m - 1 + self._off)
        self._bar_anchor += self._off - off0
        return True

    def _on_tap_dbn(self, t: float) -> None:
        """A tap for the dbn estimator (beatdbn.DbnTracker.tap): the run of taps it ends counts when 4 or more are
        evenly spaced (as the legacy rule), and a tap that moved the line makes the published line a new grid on it
        (``_dbn_publish``); a run's line starts the bars at its first tap (as a legacy tap seed)."""
        taps = self._taps
        if taps and t - taps[-1] > 2.0:
            taps.clear()
        taps.append(t)
        self._counts["taps"] += 1
        run = None
        if len(taps) >= 4:
            tt = np.fromiter(taps, float, len(taps))
            iv = np.diff(tt)
            T = float(np.median(iv))
            if 60.0 / MAX_BPM <= T <= 60.0 / MIN_BPM and float(np.std(iv)) <= 0.1 * T:
                run = [float(v) for v in tt]
        kind, k = self._trk.tap(t, run)
        if kind in ("phase", "tempo"):
            if kind == "tempo":                         # the taps' tempo is the human's octave: an octave map ends
                self._om_clear("tap")
            elif self._om_f < 1.0 and (k - self._om_k) % int(round(1.0 / self._om_f)):
                self._om_m, self._om_k = self._om_floor(k), k        # at 0.5: the tapped beat is a published one
            self._dbn_publish(max(self._trk.t_last, t), explicit=True)
            m = self._om_floor(k)
            self._dbn_k = max(self._dbn_k, k)
            self._dbn_m = max(self._dbn_m, m)
            self._dbn_K = max(self._dbn_K, m + self._off)
            if kind == "tempo":
                self._confirmed = True
                self._bar_anchor = m + self._off - (len(run) - 1)     # the first tap of the run is a one
            self._emit(t)

    def _line_beat(self, t: float) -> float:
        """The published line's beat (float) at host time ``t``, either estimator (NaN without a line)."""
        return self._pub_beat(t) if self._pub_ok else math.nan

    def _has_grid(self) -> bool:
        """A confirmed grid (corrections need one): legacy's, or the dbn line once its bars have started."""
        if self._trk is not None:
            return self._trk.line is not None and self._confirmed
        return self._grid and self._confirmed

    def _upcoming(self) -> int:
        """The next beat to be judged, in the published numbering."""
        return self._dbn_m + 1 + self._off if self._trk is not None else self._k_next + self._off

    def _data_dbn(self, now_s: float) -> ClockData:
        trk = self._trk
        trk.set_quiet(self._quiet, now_s)                # (as the candidate: every body re-reads the quiet flag)
        d = trk.body(now_s, self._dbn_source)
        ok = self._pub_ok
        d.update(bpm=self._pub_bpm if ok else 0.0, anchor_ms=self._pub_t * 1000.0 if ok else 0.0,
                 beat_at_anchor=self._pub_k if ok else 0, epoch=self._epoch,
                 beats_per_bar=self._bpb, bar_anchor_beat=int(self._bar_anchor), phrase_bars=self._phrase_bars,
                 bar_conf=round(self._bar_conf if self._confirmed else 0.0, 4),
                 phrase_conf=round(self._phrase_conf if self._confirmed else 0.0, 4))
        return d

    # ---- estimator ---------------------------------------------------------------------------------------------------
    def _prior(self, t: float) -> np.ndarray:
        if self._prior_c is not None and t - self._last_locked_t < PRIOR_KEEP_S:
            return np.exp(-0.5 * ((self._lb - self._prior_c) / PRIOR_NARROW_OCT) ** 2)
        return np.exp(-0.5 * ((self._lb - math.log2(self._prior_bpm)) / PRIOR_BROAD_OCT) ** 2)

    def _try_seed(self) -> bool:
        """Every 0.25 s while there is no grid: the tempogram's best candidate (clarity averaged over ~1 s of
        evaluations, x the tempo prior, a local maximum within 45-200 bpm), seeded once two evaluations agree."""
        tg = self.tg
        if tg.filled < SEED_MIN_FRAMES:
            return False
        X = tg.spectrum()
        x0 = X[0].real
        if x0 <= X0_MIN:
            return False
        R = np.abs(X[1:]) / x0
        self._R_s = R if self._R_s is None else self._R_s + 0.4 * (R - self._R_s)
        Rs = self._R_s
        score = Rs * self._prior(self._t_known)
        score[tg.bpm > MAX_BPM] = 0.0
        loc = np.ones(tg.K, bool)
        loc[1:] &= Rs[1:] >= Rs[:-1]
        loc[:-1] &= Rs[:-1] >= Rs[1:]
        score[~loc] = 0.0
        i = int(np.argmax(score))
        if score[i] <= 0.0 or Rs[i] < SEED_MIN_CLARITY:
            self._seed_prev = None
            return False
        d = 0.0
        if 0 < i < tg.K - 1:
            a, b, c = Rs[i - 1], Rs[i], Rs[i + 1]
            den = a - 2.0 * b + c
            if den < 0.0:
                d = min(0.5, max(-0.5, 0.5 * (a - c) / den))
        bpm = float(tg.bpm[i] + d * tg.step)
        if self._seed_prev is None or abs(bpm / self._seed_prev - 1.0) > 0.02:
            self._seed_prev = bpm
            return False
        T, t_b, fit = self._fit_peaks(60.0 / bpm, tg.beat_time(X, i))
        if 60.0 / T > FAST_BPM and 30.0 / T >= MIN_BPM:
            # the octave from the kicks, on the fitted fast grid: alternate beats only -> the half, through the kicks
            # (no comb: the activation can be as strong on off-beat hats); no evidence -> the cap, phase by the comb
            vote, par = self._kick_vote(T, t_b, self._t_known)
            if vote < 0 or (vote == 0 and 60.0 / T > SEED_CAP_BPM):
                self._counts["seed_halved"] += 1
                T, t_b, fit = self._fit_peaks(2.0 * T, t_b + par * T, comb=vote == 0)
        self._seed(60.0 / T, t_b, clarity=float(R[i]), fit=fit)
        return True

    def _low_hits(self, t_end: float) -> list[float]:
        """dsp's sub/bass hits (observe_hit) in the LOW_WIN_S before ``t_end``, a sub and a bass hit within
        LOW_MERGE_S counting once: the kicks."""
        hs = sorted(t for t, _s in list(self._hitq) if t_end - LOW_WIN_S <= t <= t_end)
        out: list[float] = []
        for t in hs:
            if not out or t - out[-1] >= LOW_MERGE_S:
                out.append(t)
        return out

    def _kick_vote(self, T: float, t_beat: float, t_end: float) -> tuple[int, int]:
        """Do the kicks support a grid of period ``T`` through ``t_beat``? ``(+1, 0)``: low hits on >= FAST_COVER of
        its beats (between the first and the last that has one); ``(-1, parity)``: fewer (on alternate beats: the
        half is the beat), and the kicks are mostly on beats ``k % 2 == parity``, k counted from ``t_beat``;
        ``(0, 0)``: fewer than LOW_MIN_HITS on-beat hits, no evidence."""
        ks: set[int] = set()
        for th in self._low_hits(t_end):
            q = (th - t_beat) / T
            k = round(q)
            if abs(q - k) <= LOW_TOL:
                ks.add(k)
        if len(ks) < LOW_MIN_HITS:
            return 0, 0
        cover = len(ks) / (max(ks) - min(ks) + 1)
        if cover >= FAST_COVER:
            return 1, 0
        even = sum(1 for k in ks if k % 2 == 0)
        return -1, (0 if even >= len(ks) - even else 1)

    def _fast_ok(self, bpm: float, T: float, t_beat: float) -> bool:
        """May the clock run at ``bpm`` (period T, a beat at t_beat)? Above FAST_BPM only if the kicks don't say
        "alternate beats", and above SEED_CAP_BPM only if they say "every beat"."""
        if bpm <= FAST_BPM:
            return True
        vote, _ = self._kick_vote(T, t_beat, self._t_known)
        return vote > 0 or (vote == 0 and bpm <= SEED_CAP_BPM)

    def _fit_peaks(self, T: float, t_b: float, comb: bool = True) -> tuple[float, float, list]:
        """Sharpen a tempogram seed on the peaks heard in the last 4 s.
        - A comb search over T +-6 % (0.5 % steps) picks the period and phase that align the most peak strength
          (the tempogram's 1 bpm bins and early windows are coarse). ``comb=False`` keeps T and the phase (a
          halving the kicks chose: T is already fitted, and the kicks set the phase).
        - Then the strongest peak within +-20 % of each beat feeds a strength-weighted LS line.
        Returns (T, a beat time, the fit [(beat offset from that beat, t, leave-one-out residual)], [] if too
        little)."""
        tk = self._t_known
        pts = [(tp, s) for tp, s, _h in self._recent if tp >= tk - 4.0]
        if len(pts) < 4:
            return T, t_b, []
        tp = np.array([p[0] for p in pts]) - tk
        sp = np.array([p[1] for p in pts])
        if comb:
            Ts = T * (1.0 + np.arange(-0.06, 0.0601, 0.005))
            v = np.exp(2j * np.pi * tp[None, :] / Ts[:, None]) @ sp
            j = int(np.argmax(np.abs(v)))
            T = float(Ts[j])
            t_b = tk + float(np.angle(v[j])) / (2.0 * np.pi) * T
        per: dict[int, tuple[float, float]] = {}
        for tt, ss in zip(tp + tk, sp):
            q = (tt - t_b) / T
            k = round(q)
            if abs(q - k) <= GATE_FRAC and (k not in per or ss > per[k][1]):
                per[k] = (float(tt), float(ss))
        if len(per) < 4:
            return T, t_b, []
        k = np.array(sorted(per), float)
        t = np.array([per[int(i)][0] for i in k])
        w = np.array([per[int(i)][1] for i in k])
        sw = float(w.sum())
        km, tm = float(np.dot(w, k)) / sw, float(np.dot(w, t)) / sw
        dk = k - km
        vk = float(np.dot(w, dk * dk))
        if vk > 0.0:
            b = float(np.dot(w, dk * (t - tm))) / vk
            if abs(b / T - 1.0) <= 0.03:
                T = b
        a = tm - T * km                                  # the line's beat 0
        r = t - (a + T * k)
        dk = k - km
        lev = w * (1.0 / sw + dk * dk / max(vk, 1e-12))  # leave-one-out residuals: honest, not in-sample
        r = r / np.maximum(1.0 - lev, 0.2)
        return T, a, [(int(ki), float(ti), float(ri)) for ki, ti, ri in zip(k, t, r)]

    def _seed(self, bpm: float, t_beat: float, clarity: float, *, confirmed: bool = False,
              hit_fill: float = 0.5, t_ref: Optional[float] = None, fit: Optional[list] = None) -> None:
        """A new grid through ``t_beat``; its first beat to judge is the first not yet heard in full at ``t_ref``.
        ``fit`` (from ``_fit_peaks``) pre-fills the LS history, residuals and hit record with what was heard."""
        T = 60.0 / bpm
        tk = self._t_known if t_ref is None else t_ref
        m = math.ceil((tk - 0.5 * T - t_beat) / T + 1e-9)
        self._T = self._T_ab = T
        self._t_next = t_beat + m * T
        self._k_next = 0
        self._obs.clear()
        self._res.clear()
        self._n_innov = 0
        self._hits.clear()
        self._hits.extend([hit_fill] * 8)
        if fit:
            heard = {k for k, _, _ in fit}
            for k, t, r in fit:
                if k < m:
                    self._obs.append((k - m, t))
                    self._res.append(r)
                    self._n_innov += 1
            first = min(heard)
            for j in range(m - 8, m):                    # the 8 beats before the first one to judge
                if j in heard:
                    self._hits.append(1.0)
                elif j >= first and t_beat + j * T >= tk - 4.0:
                    self._hits.append(0.0)               # heard, and nothing there
                else:
                    self._hits.append(hit_fill)
        self._out = []
        self._acq = 4
        self._alt = None
        self._grid = True
        self._confirmed = confirmed
        self._since_seed = 0
        self._oct_streak = 0
        self._clar = clarity
        self._seed_prev = None
        self._counts["seeds"] += 1
        self._jump_published()
        self._numbering_break(self._pub_k)               # a new grid: the count before it was another grid's

    def _drop_grid(self) -> None:
        self._grid = False
        self._confirmed = False
        self._seed_prev = None
        self._R_s = None
        self._alt = None
        # a new song: forget the old one's level (peaks are judged against what is heard now)
        self._L_lock = self._L_fade = 0.0
        self._pk.L_floor = 0.0
        tk = self._t_known
        hs = [h for tp, _s, h in self._recent if tp >= tk - 4.0]
        if hs:
            self._pk.L = min(self._pk.L, max(hs))
        self._counts["cold_searches"] += 1
        self._numbering_break(None)

    def _run_beats(self, t_known: float) -> bool:
        changed = False
        while self._grid and self._t_next + 0.5 * self._T <= t_known:
            behind = (t_known - self._t_next) / self._T
            if behind > 64:                              # hours without audio: skip ahead in one step
                n = int(behind) - 4
                self._k_next += n
                self._t_next += n * self._T
                if not (self._no_audio and self._tap_mode):
                    self._hits.extend([0.0] * 8)
            changed |= self._process_beat()
        return changed

    def _process_beat(self) -> bool:
        T, tb = self._T, self._t_next
        gate = GATE_FRAC * T
        lo, hi = tb - 0.5 * T, tb + 0.5 * T
        best_in = best_out = None
        pk = self._peaks
        while pk and pk[0][0] < lo:
            pk.popleft()
        # a peak must reach a share of the level heard while locked: 25 %, or 40 % on a held grid, so pads and
        # tremolo in a breakdown can't drag it. That level fades (tau 20 s) while not locked, but the faded one
        # applies only while the tempogram finds our own tempo clear: the same pulse back, quieter, is taken; pads
        # (no pulse) still face the full level
        held = self._state == "holding" or self._was_holding
        ref = self._L_fade if self._clar >= FAIL_CLARITY else self._L_lock
        hmin = (0.4 if held else 0.25) * ref
        for tp, s, h in pk:
            if tp >= hi:
                break
            if h < hmin:
                continue
            e = tp - tb
            if abs(e) <= gate:
                sc = s * (1.0 - 0.25 * (e / gate) ** 2)  # mostly the strongest: closest-first flatters noise
                if best_in is None or sc > best_in[2]:
                    best_in = (e, s, sc)
            elif best_out is None or s > best_out[1]:
                best_out = (e, s)
        while pk and pk[0][0] < hi - gate:               # consumed (the next window starts at ~hi)
            pk.popleft()
        k = self._k_next
        x = tb
        kind = "miss"
        strength = 0.0
        if best_in is not None and (best_out is None or best_in[1] >= 0.4 * best_out[1]):
            kind = "hit"
            e, strength = best_in[0], best_in[1]
            e = self._refine(tb + e) - tb
            a = 0.5 if self._acq > 0 else ALPHA          # acquisition: the first hits after a seed or re-lock
            self._acq = max(0, self._acq - 1)
            r3 = list(self._res)[-2:] + [e]
            if self._state == "locked" and len(r3) == 3 and min(abs(v) for v in r3) >= 0.04 * T and (
                    all(v > 0 for v in r3) or all(v < 0 for v in r3)):
                # three innovations over 4 % of a period, all one way: the tempo changed. Catch up fast, and fit
                # the period on the beats since the change only.
                a = 0.5
                keep = list(self._obs)[-2:]
                self._obs.clear()
                self._obs.extend(keep)
                self._counts["tempo_changes"] += 1
            x = tb + a * e
            self._T_ab = min(60.0 / MIN_BPM * 1.1, max(60.0 / MAX_BPM / 1.1, self._T_ab + a * a / (2.0 - a) * e))
            self._obs.append((k, tb + e))
            self._res.append(e)
            self._n_innov += 1
            self._hits.append(1.0)
            self._out = []
            self._tight = self._tight + 1 if abs(e) <= 0.08 * T else 0     # misses don't reset it (half-time)
            self._counts["hits"] += 1
        elif best_out is not None and best_out[1] >= 0.5:
            kind = "out"
            self._hits.append(0.0)
            self._out = [(kk, ee) for kk, ee in self._out if k - kk <= 6] + [(k, best_out[0])]
            self._tight = 0
            self._counts["outliers"] += 1
            if len(self._out) >= RELOCK_N:
                es = [ee for _, ee in self._out[-RELOCK_N:]]
                if max(es) - min(es) <= RELOCK_SPREAD * T:
                    x = tb + sum(es) / len(es)               # re-lock: the estimator jumps
                    self._obs.clear()
                    self._res.clear()
                    self._hits.append(1.0)
                    self._out = []
                    self._acq = 4
                    self._counts["relocks"] += 1
                    kind = "relock"
                    self._numbering_break(k + self._off)
        else:
            if not (self._no_audio and self._tap_mode):  # a tap tempo with no audio coasts without decaying
                self._hits.append(0.0)
            self._counts["misses"] += 1
        entry = kind == "hit" and self._miss_run >= self._bpb
        self._miss_run = 0 if kind == "hit" else self._miss_run + 1
        if kind == "hit":
            Tls = self._ls_period()
            self._T = Tls if Tls is not None else self._T_ab
            self._T_ab = self._T
        self._k_next = k + 1
        self._t_next = x + self._T
        self._since_seed += 1
        return self._after_beat(k, tb, kind, strength, entry)

    def _refine(self, t_a: float) -> float:
        """An activation peak at ``t_a``: the low-band hit within -40..+30 ms of it is the observation (and teaches
        the activation's lag); otherwise the peak itself."""
        best = None
        for th, _s in self._hitq:
            if t_a - 0.04 <= th <= t_a + 0.03 and (best is None or abs(th - t_a) < abs(best - t_a)):
                best = th
        if best is None:
            return t_a
        self._lag_adj = min(0.04, max(-0.04, self._lag_adj + 0.1 * (t_a - best)))
        self._counts["hit_refined"] += 1
        return best

    def _ls_period(self) -> Optional[float]:
        n = len(self._obs)
        if n < 3:
            return None
        k = np.fromiter((o[0] for o in self._obs), float, n)
        t = np.fromiter((o[1] for o in self._obs), float, n)
        tol = max(0.006, 0.015 * self._T)
        for N in (32, 16, 8, 5, 3):
            if N > n:
                continue
            kk, tt = k[-N:], t[-N:]
            km, tm = kk.mean(), tt.mean()
            dk = kk - km
            vk = float(np.dot(dk, dk))
            if vk <= 0.0:
                continue
            b = float(np.dot(dk, tt - tm)) / vk
            r = tt - (tm + b * dk)
            if math.sqrt(float(np.dot(r, r)) / N) <= tol and 0.5 * self._T < b < 2.0 * self._T:
                return min(60.0 / MIN_BPM * 1.1, max(60.0 / MAX_BPM / 1.1, b))
        return None

    def _after_beat(self, k: int, tb: float, kind: str, strength: float, entry: bool) -> bool:
        """Bookkeeping after beat ``k`` (at ``tb``) was judged: confidence, state, octave, bar/phrase, published."""
        X = self.tg.spectrum()
        bpm = 60.0 / self._T
        self._clar = max(self.tg.clarity(X, bpm), self.tg.clarity(X, 2.0 * bpm))
        # confirmation of a seeded (tentative) grid
        if not self._confirmed:
            if self._since_seed >= 3 and (sum(list(self._hits)[-4:]) >= 3 or
                                          (self._since_seed >= 6 and self._hit_rate() >= 0.7)):
                self._confirmed = True
                self._bar_anchor = k + self._off
                self._bar_conf = 0.1
                self._counts["confirmed"] += 1
            elif self._since_seed >= 8 and not self._tap_mode:
                self._drop_grid()
        self._conf_update()
        prev_state = self._state
        self._update_state()
        if self._state in ("holding", "free") and prev_state == "locked":
            self._was_holding = True
            self._tight = 0
        if self._state == "locked" and kind == "hit":
            self._prior_update(bpm)
            self._last_locked_t = tb
            self._h_good += (self._hit_rate() - self._h_good) * 0.125
            self._conf_good += (self._conf - self._conf_good) * 0.125
            self._pk.L_floor = 0.5 * self._pk.L
            self._L_lock = self._L_fade = self._pk.L
            self._tap_mode = False
        # a held grid confirmed by two tight hits keeps its confidence (and its count and phrase)
        if self._was_holding and self._tight >= 2 and self._conf_good >= BEAT_OFF:
            self._was_holding = False
            fill = max(self._hit_rate(), 0.9 * self._h_good)
            last = list(self._hits)[-2:]
            self._hits.clear()
            self._hits.extend([fill] * 6 + last)
            self._counts["held_relocks"] += 1
            self._conf_update()
            self._update_state()
        if self._was_holding and self._state == "locked" and self._hit_rate() >= 0.75:
            self._was_holding = False                   # back on its feet without needing the restore
        # the music moved on (a new song): cold search
        if self._confirmed and not self._tap_mode and self._moved_on(X, tb, bpm):
            self._drop_grid()
            self._conf_update()
            self._update_state()
        if self._grid and self._confirmed:
            self._octave_check(X, bpm, k)
            self._bar_phrase(k, kind, strength, entry)
        self._update_gate(tb)
        changed = self._apply_pending(k)
        changed |= self._update_published(tb + 0.5 * self._T)
        return changed

    def _moved_on(self, X: np.ndarray, tb: float, bpm: float) -> bool:
        """Two ways to see that the grid no longer fits the music (both start a cold search):
        - hits stopped (<= 1 in 8 beats) while >= 4 strong peaks (half the *current* peak level) in 4 s pulse at a
          tempo the tempogram finds clearer than ours (``cold_hits``);
        - another tempo has clarity >= FAIL_CLARITY and >= ALT_RATIO x ours for ALT_HOLD_S, whatever the hit count
          (``cold_alt``): a new song near 3:4 or 4:3 of the old one keeps catching hits on the old grid by
          coincidence, and a polyrhythm never gives the three same-signed innovations a tempo change needs. The hold
          runs while conf < DMX_ON and there is rhythm (>= 4 peaks in the last 4 s: silence makes no candidates),
          and fires only with conf < DMX_OFF. Candidates within 4 % of our tempo, its double or its half are ours
          (the octave check's business), and the same candidate (within 4 %) must hold the whole time."""
        x0 = X[0].real
        if x0 <= X0_MIN:                                 # no rhythm in the window (silence): ratios are noise
            self._alt = None
            return False
        R = np.abs(X[1:]) / x0
        if sum(self._hits) <= 1.0:
            ref = self._pk.L
            strong = sum(1 for tp, _s, h in self._recent if tp >= tb - 4.0 and h >= 0.5 * ref)
            if strong >= 4 and R.max() >= FAIL_CLARITY and self._clar < 0.5 * R.max():
                self._counts["cold_hits"] += 1
                return True
        if self._conf >= DMX_ON or sum(1 for tp, _s, _h in self._recent if tp >= tb - 4.0) < 4:
            self._alt = None
            return False
        b = self.tg.bpm
        ok = b <= MAX_BPM
        for f in (1.0, 2.0, 0.5):
            ok &= np.abs(b / (bpm * f) - 1.0) > ALT_SAME
        ok[1:] &= R[1:] >= R[:-1]                       # local maxima only
        ok[:-1] &= R[:-1] >= R[1:]
        if not ok.any():
            self._alt = None
            return False
        i = int(np.argmax(np.where(ok, R, -1.0)))
        if R[i] < FAIL_CLARITY or R[i] < ALT_RATIO * self._clar:
            self._alt = None
            return False
        cand = float(b[i])
        if self._alt is None or abs(cand / self._alt[0] - 1.0) > ALT_SAME:
            self._alt = (cand, tb)
            return False
        if tb - self._alt[1] < ALT_HOLD_S or self._conf >= DMX_OFF:
            return False
        self._counts["cold_alt"] += 1
        return True

    def _hit_rate(self) -> float:
        """Hits over the last 8 beats. A clean alternation (all 4 of one parity, none of the other: kick on 1 and 3
        only) counts as 0.95: a half-time pattern is a confident grid, not half a one."""
        h = list(self._hits)
        n = len(h)
        if not n:
            return 0.0
        rate = sum(h) / n
        if n == 8 and self._since_seed >= 8:              # judged beats only, never a seed's prefill
            a, b = sum(h[-1::-2]), sum(h[-2::-2])
            if max(a, b) >= 4.0 and min(a, b) <= 0.0:
                rate = max(rate, 0.95)
        return rate

    def _conf_update(self) -> None:
        if not self._grid:
            self._conf = 0.0
            return
        h = self._hit_rate()
        if self._res:
            r = np.fromiter(self._res, float, len(self._res))
            sig = max(0.003, math.sqrt(float(np.mean(r * r))))
            frac = float(np.mean(np.abs(r) < CONF_X_S))
            prec = 0.5 * frac + 0.5 * math.erf(CONF_X_S / (math.sqrt(2.0) * sig))
            n = min(32, self._n_innov)
            prec = (n * prec + 3.0 * 0.35) / (n + 3.0)  # few innovations: shrink toward a sceptical prior
        else:
            prec = 0.35
        c = h * prec * (0.3 + 0.7 * _smoothstep(0.08, 0.35, self._clar))
        if not self._confirmed:
            c = min(c, 0.3)                              # no gate until the grid is confirmed
        self._conf = min(0.99, max(0.0, c))

    def _update_state(self) -> None:
        if not self._grid or not self._confirmed:
            self._state = "searching"
        elif self._quiet:
            self._state = "holding"
        elif self._no_audio or self._tap_mode:          # a tap tempo is free until audio confirms it
            self._state = "free"
        elif sum(list(self._hits)[-4:]) >= 2.0:
            self._state = "locked"
        else:
            self._state = "holding"

    def _prior_update(self, bpm: float) -> None:
        lb = math.log2(bpm)
        if self._prior_c is None:
            self._prior_c = lb
        else:
            self._prior_c += (lb - self._prior_c) * (1.0 - math.exp(-self._T / PRIOR_TAU_S))

    def _octave_check(self, X: np.ndarray, bpm: float, k: int) -> None:
        if k < self._oct_lock_k or self._t_next < self._oct_lock_t or self._pending_oct is not None:
            self._oct_streak = 0
            return
        tg = self.tg

        def score(b: float) -> float:
            return tg.clarity(X, b) * math.exp(-0.5 * (math.log2(b / PRIOR_BPM) / PRIOR_BROAD_OCT) ** 2)

        cur = score(bpm)
        best, fac = 0.0, 1.0
        kick: Optional[int] = None
        if bpm > FAST_BPM and bpm / 2.0 >= MIN_BPM:
            vote, par = self._kick_vote(self._T, self._t_next, self._t_known)
            if vote < 0:                                 # kicks on alternate beats: the half is the beat
                fac, kick = 0.5, self._k_next + par
        if kick is None:
            for f in (2.0, 0.5):
                b = bpm * f
                if MIN_BPM <= b <= MAX_BPM and tg.clarity(X, b) >= 0.2:
                    s = score(b)
                    if s > OCTAVE_MARGIN * cur and s > best:
                        if f == 2.0 and not self._fast_ok(b, self._T / 2.0, self._t_next):
                            self._counts["octave_kick_refused"] += 1
                            continue
                        best, fac = s, f
        if fac != 1.0 and fac == self._oct_alt:
            self._oct_streak += 1
        else:
            self._oct_alt, self._oct_streak = fac, (1 if fac != 1.0 else 0)
        if self._oct_streak >= OCTAVE_BEATS:
            self._pending_oct = fac
            self._pending_kick = kick
            self._oct_streak = 0

    def _bar_phrase(self, k: int, kind: str, strength: float, entry: bool) -> None:
        """Phrase evidence: accents at bar starts (x3 for an entry, a hit after a bar of silence), by bar mod 16,
        leaky over ~32 bars. The phrase (4/8/16 bars, and which bar starts it) moves only when the same pick wins
        two bars running; ``phrase_conf`` stays below ``bar_conf``."""
        K = k + self._off
        if kind == "hit":
            self._beat_strengths.append(strength)
        if (K - self._bar_anchor) % self._bpb:
            return
        bar = (K - self._bar_anchor) // self._bpb
        mean_s = (sum(self._beat_strengths) / len(self._beat_strengths)) if self._beat_strengths else 1.0
        acc = (strength / max(mean_s, 1e-6)) * (3.0 if entry else 1.0) if kind == "hit" else 0.0
        h = self._ph_hist
        h *= 0.97
        h[bar % 16] += acc
        self._ph_bars += 1
        mu, sd = float(h.mean()), float(h.std())
        if sd <= 1e-9:
            return
        scores = {L: max(((float(h[o::L].mean()) - mu) / sd, o) for o in range(L)) for L in (4, 8, 16)}
        top = max(s for s, _ in scores.values())
        L = next(L for L in (4, 8, 16) if scores[L][0] >= 0.85 * top)
        o = scores[L][1]
        if top >= 1.5 and (L, o) == self._ph_pick and (L != self._phrase_bars or o % L):
            self._phrase_bars = L                       # whole bars: the bar phase is untouched
            self._bar_anchor += o * self._bpb
            self._ph_hist = np.roll(h, -o)
            self._ph_pick = (L, 0)
        else:
            self._ph_pick = (L, o)
        self._phrase_conf = self._bar_conf * min(1.0, max(0.0, top / 3.0)) * min(1.0, self._ph_bars / (2.0 * L))

    def _update_gate(self, t: float) -> None:
        c, g = self._conf, self._gate
        if self._state == "searching" or self._quiet:
            new = "none"
        elif g == "none":
            new = "beat" if c >= BEAT_ON else ("dmx" if c >= DMX_ON else "none")
        elif g == "dmx":
            new = "beat" if c >= BEAT_ON else ("none" if c < DMX_OFF else "dmx")
        else:
            new = "beat" if c >= BEAT_OFF else ("dmx" if c >= DMX_OFF else "none")
        if new == g:
            return
        up = _GATE_RANK[new] > _GATE_RANK[g]
        recent = t - self._gate_t < DWELL_BARS * self._bpb * self._T
        # 2-bar dwell against flapping: nothing goes down within 2 bars of a change, and nothing goes back up within
        # 2 bars of a drop; a climb (none -> dmx -> beat) is not held back, and losing the grid drops at once
        if recent and not (new == "none" and (self._state == "searching" or self._quiet)) and (not up or not self._gate_up):
            return
        self._gate, self._gate_t, self._gate_up = new, t, up

    # ---- corrections -------------------------------------------------------------------------------------------------
    def _drain(self) -> None:
        q = self._inbox
        while q:
            item = q.popleft()
            kind = item[0]
            if kind == "corr":
                self._on_correction(*item[1:])
            elif kind == "oct":
                self._pending_oct, self._pending_kick = item[1], None
                self._oct_lock_t = self._t_known + SLOW_OCT_HOLD_S
                self._human_oct_t = self._t_known
                self._counts["human_octave"] += 1
            elif kind == "one":
                b = self._line_beat(item[1])
                if not math.isnan(b):
                    K = round(b)
                    self._bar_anchor = K
                    self._bar_conf = max(self._bar_conf, 0.8)
                    self._counts["human_one"] += 1
                    if self._pending is not None and self._pending["one"] is not None:
                        self._pending = None                # the human said where the one is
                        self._corr_done("superseded")
            elif kind == "tap":
                if self._trk is not None:
                    self._on_tap_dbn(item[1])
                else:
                    self._on_tap(item[1])

    def _on_correction(self, one_in: object, L_in: object, oct_in: object) -> None:
        """A slow-lane vote, on show-rx: checked, gated, then waiting for its bar line (``_apply_pending``)."""
        one = None if one_in is None else _whole(one_in)
        L = None if L_in is None else _whole(L_in)
        try:
            octv = float(oct_in) if oct_in is not None else 1.0
        except (TypeError, ValueError):
            octv = math.nan
        if ((one_in is not None and one is None) or (L_in is not None and (L is None or
                                                                           not BAR_LEN_MIN <= L <= BAR_LEN_MAX))
                or octv not in (0.5, 1.0, 2.0)):
            self._corr_done("bad", {"one": None, "L": None, "oct": None})
            return
        if octv != 1.0 and self._human_oct_t is not None and self._t_known - self._human_oct_t < HUMAN_OCT_HOLD_S:
            self._corr_done("human", {"one": one, "L": L, "oct": octv})   # a person set the octave: theirs stands
            return                                      # (the vote's one and bar length assumed its own octave)
        p = {"one": one, "L": L, "oct": octv, "age": 0}
        if not self._has_grid():
            self._corr_done("no_grid", p)
            return
        K = self._upcoming()                            # the upcoming beat, published numbering
        if one is not None and not self._break_k <= one <= K + (L or self._bpb):
            self._corr_done("stale", p)                 # another grid's count, or not from the window (the past)
            return
        if self._pending is not None:
            self._counts["corr_replaced"] += 1
        self._pending = p
        self._corr_done("accepted", p)
        # the slow lane agrees with this octave (or sets it): the clock's own octave check holds off
        self._oct_lock_t = max(self._oct_lock_t, self._t_known + SLOW_OCT_HOLD_S)

    def _corr_done(self, result: str, p: Optional[dict] = None, at: Optional[int] = None) -> None:
        """Count a correction's fate and keep it for ``stats()["last_correction"]`` (swapped by reference):
        ``{one, bar_len, octave, result, at_beat}``, ``at_beat`` being the published beat it switched at."""
        self._counts[f"corr_{result}"] += 1
        last = self._corr_last
        if p is not None:
            last = {"one": p["one"], "bar_len": p["L"], "octave": p["oct"]}
        elif last is None:
            last = {"one": None, "bar_len": None, "octave": None}
        self._corr_last = {**last, "result": result, "at_beat": at}

    def _numbering_break(self, K: Optional[int]) -> None:
        """The count before published beat ``K`` is not this grid's (a new grid, an octave change, a re-lock; None:
        the grid is gone): a waiting slow-lane correction, and any that refers to an earlier beat, is stale."""
        if K is not None:
            self._break_k = int(K)
        if self._pending is not None:
            self._pending = None
            self._corr_done("stale")

    def _on_tap(self, t: float) -> None:
        taps = self._taps
        if taps and t - taps[-1] > 2.0:
            taps.clear()
        taps.append(t)
        self._counts["taps"] += 1
        if len(taps) < 4:
            return
        tt = np.fromiter(taps, float, len(taps))
        iv = np.diff(tt)
        T = float(np.median(iv))
        if not (60.0 / MAX_BPM <= T <= 60.0 / MIN_BPM) or float(np.std(iv)) > 0.1 * T:
            return
        if self._grid:
            ph = ((t - self._t_next) / self._T + 0.5) % 1.0 - 0.5
            if abs(self._T / T - 1.0) <= 0.04 and abs(ph) <= 0.15:
                self._hits.append(1.0)                  # agrees: one more observation
                return
        n = len(tt)                                     # LS line through the taps, leave-one-out residuals
        k = np.arange(n, dtype=float)
        km, tm = k.mean(), tt.mean()
        dk = k - km
        T = float(np.dot(dk, tt - tm) / np.dot(dk, dk))
        a = tm - T * km
        lev = 1.0 / n + dk * dk / float(np.dot(dk, dk))
        r = (tt - (a + T * k)) / np.maximum(1.0 - lev, 0.2)
        fit = [(int(i), float(ti), float(ri)) for i, ti, ri in zip(k, tt, r)]
        self._seed(60.0 / T, a, clarity=0.5, confirmed=True, hit_fill=0.75, t_ref=t, fit=fit)
        self._tap_mode = True
        self._bar_anchor = self._k_next + self._off - (n - 1)     # the first tap of the run is a one
        self._conf_update()
        self._update_state()
        self._emit(t)

    def _apply_pending(self, k: int) -> bool:
        """Integer corrections switch at the upcoming beat (``k + 1``) when it is a bar line (either estimator: the
        dbn one calls this after each of the line's judged beats, ``_dbn_beats``)."""
        if self._trk is not None:
            if self._trk.line is None:
                return False
        elif not self._grid:
            return False
        K = self._upcoming()                            # the upcoming beat, published numbering
        changed = False
        if self._pending_oct is not None and self._pending is None:
            j = self._om_bar_wait(K, self._pending_oct,
                                  lambda x: self._bar_conf < 0.5 or (x - self._bar_anchor) % self._bpb == 0)
            if j is not None:
                f, kick = self._pending_oct, self._pending_kick
                self._pending_oct = self._pending_kick = None
                if self._apply_octave(f, kick=kick):
                    self._bar_anchor += j                   # (past an in-between bar line: the switch beat starts it)
                    changed = True
                    self._counts["octave_changes"] += 1
                    if kick is not None:
                        self._counts["octave_kicks"] += 1
        p = self._pending
        if p is not None:
            p["age"] += 1
            L = p["L"] or self._bpb
            ref = p["one"] if p["one"] is not None else self._bar_anchor
            # does the upcoming beat K start a bar under the new hypothesis? Old beat K is new beat (K - ref) x octave
            # counted from the one; it starts a bar when that is a whole multiple of L. With octave = num / den:
            # (K - ref) x num = 0 mod (L x den)
            num, den = (2, 1) if p["oct"] == 2.0 else ((1, 2) if p["oct"] == 0.5 else (1, 1))

            def at_bar(x: int) -> bool:
                return ((x - ref) * num) % (L * den) == 0

            # (dbn, under an octave map: an octave also needs K to be a beat of both grids, _om_bar_wait)
            if at_bar(K) if p["oct"] == 1.0 else (self._om_bar_wait(K, p["oct"], at_bar) is not None):
                self._pending = None
                into = ((K - self._bar_anchor) // self._bpb) % self._phrase_bars
                if p["oct"] != 1.0:
                    if not self._apply_octave(p["oct"]):    # out of 45-200 bpm (counted): nothing else moves
                        self._corr_done("refused", p)
                        return changed
                    changed = True
                    self._counts["octave_votes"] += 1
                    if self._trk is not None:           # dbn: the new grid may number that beat one on
                        K = self._upcoming()
                if p["one"] is not None:
                    agree = L == self._bpb and (p["one"] - self._bar_anchor) % self._bpb == 0 and p["oct"] == 1.0
                    self._bar_conf = (self._bar_conf + 0.35 * (0.95 - self._bar_conf)) if (
                        agree and self._bar_votes) else 0.6
                    self._bar_votes += 1
                if p["one"] is not None or p["oct"] != 1.0 or L != self._bpb:
                    self._bar_anchor = K - into * L             # K starts a bar; its place in the phrase is kept
                self._bpb = L
                self._counts["bar_votes"] += 1
                self._corr_done("applied", p, K)
            elif p["age"] > 2 * L * den:                # the count moved under it (a published jump): never off a bar
                self._pending = None
                self._corr_done("expired", p)
        return changed

    def _apply_octave(self, f: float, kick: Optional[int] = None) -> bool:
        """The upcoming beat keeps its time; the period becomes T / f; the published line re-anchors there. An octave
        that would leave 45-200 bpm is refused (counted). ``kick`` (a kick-voted halving): an estimator beat that
        carries kicks; when the upcoming beat is of the other parity, the new grid starts one beat later, on the
        kicks (the count goes on: that beat keeps its number). The dbn estimator: ``_dbn_octave``."""
        if self._trk is not None:
            return self._dbn_octave(f)
        if not MIN_BPM <= 60.0 / self._T * f <= MAX_BPM:
            self._counts["octave_refused"] += 1
            return False
        if f == 0.5 and kick is not None and (self._k_next - kick) % 2:
            self._k_next += 1
            self._t_next += self._T
        K = self._k_next + self._off
        self._T = self._T_ab = self._T / f
        self._obs.clear()
        self._res.clear()
        self._oct_lock_k = self._k_next + 16
        self._oct_streak = 0
        self._set_pub(K, self._t_next, 60.0 / self._T)
        self._epoch += 1
        self._numbering_break(K)                        # beats from K on are the new octave's
        return True

    # ---- the published line ------------------------------------------------------------------------------------------
    def _pub_beat(self, t: float) -> float:
        return self._pub_k + (t - self._pub_t) * self._pub_bpm / 60.0

    def _est_beat(self, t: float) -> float:
        return self._k_next - (self._t_next - t) / self._T

    def _set_pub(self, k: int, t: float, bpm: float) -> None:
        self._pub_k, self._pub_t, self._pub_bpm = int(k), float(t), float(bpm)
        self._pub_ok = True

    def _jump_published(self) -> None:
        """Below the gate (or a new grid): the published line becomes the estimator's, keeping the count within
        half a beat of where it was."""
        t = self._t_known
        if self._pub_ok:
            self._off = int(round(self._pub_beat(t) - self._est_beat(t)))
        else:
            self._off = 0
        self._set_pub(self._k_next + self._off, self._t_next, 60.0 / self._T)
        self._epoch += 1

    def _update_published(self, t: float) -> bool:
        """After a judged beat, at estimator time ``t`` (now, as heard). True if the line was re-anchored."""
        if not self._grid:
            return False
        bpm_e = 60.0 / self._T
        if not self._pub_ok:
            self._jump_published()
            return True
        pb = self._pub_beat(t)
        eb = self._est_beat(t)
        off = int(round(pb - eb))
        d = eb + off - pb
        old = (self._pub_k, self._pub_t, self._pub_bpm)
        if self._gate == "beat":
            self._off = off
            if abs(d) < 4e-4 and abs(self._pub_bpm / bpm_e - 1.0) < 2e-4:
                self._set_pub(self._k_next + off, self._t_next, bpm_e)     # converged: snap (< 0.2 ms)
            else:
                bpm = bpm_e * (1.0 + min(SLEW_MAX, max(-SLEW_MAX, d / SLEW_BEATS)))
                ka = math.floor(pb)
                self._set_pub(ka, t - (pb - ka) * 60.0 / bpm, bpm)
        else:
            self._off = off
            self._set_pub(self._k_next + off, self._t_next, bpm_e)
            if abs(d) > 0.01:
                self._epoch += 1
        new = (self._pub_k, self._pub_t, self._pub_bpm)
        if new == old:
            return False
        # negligible re-anchors (same line within 0.1 ms over the next second) are not news
        t1 = t + 1.0
        b_old = old[0] + (t1 - old[1]) * old[2] / 60.0
        b_new = new[0] + (t1 - new[1]) * new[2] / 60.0
        b0_old = old[0] + (t - old[1]) * old[2] / 60.0
        b0_new = new[0] + (t - new[1]) * new[2] / 60.0
        return abs(b_new - b_old) * 60.0 / bpm_e > 1e-4 or abs(b0_new - b0_old) * 60.0 / bpm_e > 1e-4

    def _data(self, now_s: float) -> ClockData:
        if self._trk is not None:
            return self._data_dbn(now_s)
        return {"bpm": self._pub_bpm if self._pub_ok else 0.0,
                "anchor_ms": self._pub_t * 1000.0 if self._pub_ok else 0.0,
                "beat_at_anchor": self._pub_k if self._pub_ok else 0,
                "beats_per_bar": self._bpb, "bar_anchor_beat": int(self._bar_anchor),
                "phrase_bars": self._phrase_bars, "conf": round(self._conf, 4),
                "bar_conf": round(self._bar_conf if self._confirmed else 0.0, 4),
                "phrase_conf": round(self._phrase_conf if self._confirmed else 0.0, 4),
                "state": self._state, "gate": self._gate, "epoch": self._epoch,
                "stability": round(self._clar, 4), "source": self.act.name, "t_ms": now_s * 1000.0}

    def _emit(self, now_s: float) -> None:
        d = self._data(now_s)
        self._snapshot = d
        self._last_pub_s = now_s
        self._counts["published"] += 1
        if self._publish is not None:
            self._publish({"type": MUSIC_CLOCK, "data": d})

    def _error(self) -> None:
        self._err_n += 1
        now = time.monotonic()
        if now - self._err_logged > 10.0:
            self._err_logged = now
            log.exception("clock error (%d so far)", self._err_n)
