"""Live score v2: the piano's playing written out as notation from the first frame, one object per note for its life.

Source: Original.  Engraving by Verovio (https://www.verovio.org, LGPL-3.0-or-later, an unmodified library in a separate
        worker process: score/engrave.py, with its "notes" and proportional "spacing" options), rasterised by resvg
        (resvg-py, MPL-2.0 / Apache-2.0); the Leipzig music font (SIL OFL 1.1), whose outlines the GPU also draws
        (score/glyphs.py).  The transcription is the room's (score/room.py, quantize.py: PM2S beats, metre, key and
        hands), shared with live_score.
Author: smfx
License: MIT (this file)
license_class: permissive

Listens to the piano (``listens = ("notes",)``) through the room's one transcription.

**One process, not two.**  A key press makes a note: a Leipzig notehead (filled; opening to a half note once nothing
else in its staff has started for two beats, a whole note after four), its stem (one per chord, from the head farthest
from the middle line), its ledger lines and its accidental (by the bar's rule), drawn by the GPU from the font's own
outlines at the engraving's size, readable on the first frame after it arrives.  It is placed from the start where
the engraving will put it: in its bar's slot by Verovio's proportional spacing (time, plus room for accidentals and
seconds: K16 / ACC_X / SEC_X, fitted to the engraver), on the best beat grid the builder has (the analysed BeatGrid,
else its beat clock, else free time), snapped to the 16th.  Each estimate (an analysis reply, a clock re-anchor)
re-places the unwritten notes, but a shown note moves only when two estimates in a row agree (no flicker), and not at
all for less than MOVE_MIN_SP; once its bar is committed the bar's own 16th and staff place it.  Before the metre a
phrase writes on open lines laid out in time (a line holds about ``line_s``); when the metre lands, whole bars are
fitted to the same spans (no note changes line), the bar lines drop in and the time signature flips in.  When a bar's
engraving lands it is composited, still hidden, and its notes hop (a low arc if they moved), morph their heads, twist
or trim their stems to the engraved ones and pop their accidentals; then the line's reveal front sweeps the bar in
under them (a short sweep, no backlog pen) and each glyph hands over to its identical ink, which adds only what a note
doesn't own: beams, flags, rests, ties, dynamics.  When the metre turns (a rewrite) while the phrase is all in view,
its ink lifts back into glyphs on their heads, the notes shuffle into the new bars and the bars settle again; once its
start has gone, what's written stays and the phrase carries on after it with the new time signature.  Nothing blanks.

``page``: a stationary page of ``lines`` grand staves (3-5) that writes in place and wraps: line j sits in slot
j mod lines, and a line about to be written erases the old one in its slot left to right.  Bars per line follow the
tempo (a line holds about ``line_s`` of music: more, narrower bars when they're short, never under FIT_MIN of their
natural width), and a line closes early when the bars landing show the rest won't fit.  Older lines dim.
``scroll``: one grand staff on a tape moving at the music's pace (its tempo fed forward, a gentle pull keeping the
newest notes at the playhead, never backwards), new notes appearing at the playhead and settling into ink behind it;
key changes are written inline and a sticky header shows the clefs and key.

Degrade, never refuse: without the engraver the glyph notes are the score; an overloaded host drops frames, not motion
(motion is time-based); over the record cap the rest go undrawn that frame.  Looks: paper and night.  Macros: energy ->
halo size and the fresh ink's glow; pulse (bind note_on) -> a kick on the newest heads; speed -> the page's breathing;
intensity -> gain; palette / period / drift colour the sounding notes (around the circle of fifths), cooling to ink.
"""
from __future__ import annotations

import logging
import math
import queue
import threading
import time

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import palette_coeffs
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source
from stream_service.score import glyphs as GL
from stream_service.score.motion import LinePlan, PlanLine, smoothstep

log = logging.getLogger("stream_service.plugins.score_v2")

LOOKS = ("paper", "night")
LAYOUTS = ("page", "scroll")
SPACINGS = ("proportional", "classic")
SPANS = ("screen", "room")                                 # scroll: each screen its own tape, or one across the room
SPAN_HEADERS = ("every", "first")                          # span: room: the sticky header on every screen, or the first
SPAN_SP, SPAN_NMAX = 1.25, 3                               # span: the staff this much larger (one row); the ring holds n
SPAN_DT, SPAN_NS = 1.5, 9                                  # span: the paper is where the music was, averaged over this
NAT_SEED = {"proportional": 8.1, "classic": 7.2}         # a beat's natural width (sp) until the engraver says
PROP_AC = {"proportional": (1.17, 0.33), "classic": (1.39, 0.82)}   # a head's origin x = x0 + a + f (w - a - c)
# f: the onset's place in the bar's natural spacing: 16ths at K16 each, plus room for an accidental before an onset and
# for a chord with a second after one (fitted to Verovio's proportional spacing over 988 engraved heads in 120 justified
# bars: p50 0.38 sp, against 0.67 for time alone; s.t. spacing_fit in the stage A notes)
K16, ACC_X, SEC_X = 1.5, 0.6, 0.7
ROOM_KEY = "room"
# geometry (staff spaces)
ROW_SP, TOP_SP, STAVE_GAP_SP, PITCH_SP = 24.0, 6.0, 11.5, 21.0
SP_PAGE, SP_SCROLL = 0.0118, 0.0102                       # staff space as a fraction of the height (staff_size 1)
MARGIN_SP = 3.0                                           # brace room left of a line's header
HDR_SP, HDR_ACC_SP = 15.4, 1.2                            # a header's width (brace, clefs; measured) + a key's sign
TS_SP = 2.9                                               # the time signature's room after a phrase's first header
PAD_R_SP = 1.5
# layout
LINE_S = 7.0                                              # a line should hold about this much music
WANT, SQUEEZE, FIT_MIN = 1.15, 0.85, 0.92
N_MIN, N_MAX = 2, 12
FEATHER_SP = 1.0
FRONT_V_SPS, FRONT_A_SPS = 140.0, 1400.0                  # the reveal front's speed and acceleration caps
SHOW_AT = 0.7                                             # the next line shows once the newest note is this far along
ERASE_S, ERASE_FEATHER_SP, SHOW_S, HDR_FADE_S = 0.6, 3.0, 0.4, 0.3
AGE_ALPHA = (1.0, 1.0, 0.86, 0.7, 0.62)
HDR_WAIT_S, PART_TIMEOUT_S, PARK_MAX = 1.5, 5.0, 8
RESULTS_PER_FRAME = 3
INK_SLOTS = 6                                             # (pinned slots the ink thread fills: results in flight)
SETTLE_PER_FRAME = 16                                     # (a landed bar's notes set off a frame; the rest next)
PLACE_PER_FRAME = 1                                       # (bars landed a frame: a burst lands over frames)
PUMP_S = 0.004
LIFT_S = 0.3
JOIN_SCROLL_S = 16.0                                      # joining mid-phrase: the music kept, behind the playhead
JOIN_PAGE_S = 18.0                                        # ... on the page (at most: lines - 1 of them)
JOIN_ROWS_S = 1.5                                         # bars the room hands over this soon after: drawn at once
JOIN_PER_FRAME = 16                                       # (that many of their notes a frame, newest first)
# notes
NCAP = 512
POP, POP_S, BIRTH_S = 0.22, 0.14, 0.03
GLIDE_S, STAFF_HOP_S, STAFF_ARC_SP = 0.25, 0.4, 0.8
SETTLE_S0, SETTLE_SPS, SETTLE_MAX, SETTLE_ARC = 0.22, 0.03, 0.5, 1.0
MORPH_S, STEM_S, TWIST_S, ACC_S, HAND_S, PUFF_S = 0.15, 0.08, 0.25, 0.2, 0.15, 0.35
WAVE_SPREAD_S, WAVE_S = 0.15, 0.12                       # a bar's hops start left to right over this; bars in a burst
                                                          # this far apart
HALF_BEATS, WHOLE_BEATS = 2.0, 4.0
STEM_SP = 3.5
FRESH_S, REL_S = 0.7, 0.6
EPOCH_MIN_S = 0.1
RELAYOUT_PER_FRAME = 24                                   # (notes laid out a frame, newest first; the rest next frame)
BIRTH_PER_FRAME = 8                                       # (a join's unwritten notes: born over a few frames)
MOVE_MIN_SP = 0.3                                         # a shown note doesn't glide for less (the settle takes it)
HALO_SP = 0.4
# gates (bar lines and beat ticks)
NGATE = 192
GATE_TICK_A, GATE_DROP_S = 0.16, 0.3
TICK, BARL, FINAL = 0, 1, 2
# scroll
X_HDR, PLAY_X, HW_MAX_SP = 0.02, 0.86, 26               # the sticky header's x; the playhead (of the tape)
S_PULL, S_TAU, S_AMAX_SP = 0.35, 0.8, 8.0                # the paper: its pull to the playhead (1/s), its speed's
                                                          # easing (s), its acceleration cap (sp/s^2)
LAG_S = 0.6                                               # notes arrive about this late: 'now' on the paper is earlier
RS = 24                                                   # floats per canvas in the frame's parameters

_SRC = HELPERS + GL.GLYPH_SRC + r"""
#define RS """ + str(RS) + r"""

extern "C" __global__
void bake_page(unsigned char* __restrict__ page, const int W, const int H, const int night, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float X = x + 0.5f, Y = y + 0.5f, u = X / W, v = Y / H;
    const float grain = fbm(u * 9.f, v * 5.f, seed + 3u) - 0.5f;
    const float fibre = vnoise(X * 0.35f, Y * 0.02f + 3.f, seed + 9u) - 0.5f;
    const float vig = 1.f - 0.35f * ((u - 0.5f) * (u - 0.5f) * 1.6f + (v - 0.5f) * (v - 0.5f) * 2.f);
    const float k = night ? (0.9f + 0.25f * grain) * vig : (0.93f + 0.05f * grain + 0.015f * fibre) * vig;
    page[y * W + x] = (unsigned char)(clamp01(k / 1.2f) * 255.f + 0.5f);
}

__device__ __forceinline__ void blend_ink(float3& c, const float3 inkc, const float cov) {
    c.x += (inkc.x - c.x) * cov; c.y += (inkc.y - c.y) * cov; c.z += (inkc.z - c.z) * cov;
}
__device__ __forceinline__ void blend_glow(float3& c, const float3 g3, const float g, const int night) {
    if (!night) { c.x += (g3.x * 0.8f - c.x) * g * 0.6f; c.y += (g3.y * 0.8f - c.y) * g * 0.6f;
                  c.z += (g3.z * 0.8f - c.z) * g * 0.6f; }
    else { c.x += g3.x * g; c.y += g3.y * g; c.z += g3.z * g; }
}
__device__ __forceinline__ float staff_lines(const float cy, const float t1, const float t2, const float sp,
                                             const float th) {
    float s = 0.f;
    for (int st = 0; st < 2; ++st) {
        const float d0 = cy - (st ? t2 : t1);
        if (d0 < -th || d0 > 4.f * sp + th) continue;
        const float m = fmodf(d0 + 0.5f * sp, sp) - 0.5f * sp;
        s = fmaxf(s, clamp01(0.5f * th + 0.5f - fabsf(m)) * 0.85f);
    }
    return s;
}
__device__ __forceinline__ float3 background(const unsigned char* __restrict__ page, const float* __restrict__ P,
                                             const int x, const int y, const int W, const float v, const int night,
                                             float3& inkc)
{
    const float k = page[y * W + x] * (1.2f / 255.f) * (1.f + 0.012f * sinf(P[15]));
    if (!night) { inkc = make_float3(0.11f, 0.085f, 0.07f); return make_float3(0.95f * k, 0.905f * k, 0.80f * k); }
    inkc = make_float3(0.92f, 0.82f, 0.58f);
    return make_float3(0.028f * k, 0.034f * k, 0.055f * k + 0.01f * (1.f - v));
}

// P: [0..11] palette [12] W [13] H [14] look [15] breath [18] gain [27] canvas h [28] staff space [29] line
//    thickness [30..32] fresh ink colour [33] canvases
// R (RS per canvas): [0] x0 [1] y0 (screen px of canvas x 0 / row 0) [2] alpha [3] reveal front (canvas x)
//    [4] feather [5] glow amp [6] glow length [7] header x1 [8] header alpha [9] staff x0 [10] staff x1
//    [11] staff weight [12] t1 [13] t2 [14] erased up to (canvas x) [15] erase feather [16] ink alpha
//    [17] ring width (0: a flat canvas) [18] visible from screen x [19] its feather [20] canvas width
//    [21] (ring) visible to screen x
// RI: each canvas's byte offset.  GN / GU: the glyph records and uniforms (glyphs.py)
extern "C" __global__
void score(uchar4* __restrict__ out, const float* __restrict__ P, const unsigned char* __restrict__ canvas,
           const float* __restrict__ R, const int* __restrict__ RI, const unsigned char* __restrict__ page,
           const float* __restrict__ GN, const float* __restrict__ GU, const float* __restrict__ GA, const int GAW,
           const float* __restrict__ GT)
{
    const int W = (int)P[12], H = (int)P[13];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float X = x + 0.5f, Y = y + 0.5f;
    const int night = (int)P[14];
    float3 inkc;
    float3 c = background(page, P, x, y, W, Y / H, night, inkc);
    const int ch = (int)P[27], nc = (int)P[33];
    const float sp = P[28], th = P[29];
    const float3 fresh_c = make_float3(P[30], P[31], P[32]);
    for (int r = 0; r < nc; ++r) {
        const float* Q = R + RS * r;
        const float a = Q[2];
        if (a <= 0.001f) continue;
        const float cx = X - Q[0], cy = Y - Q[1];
        const int cw = (int)Q[20];
        const float ring = Q[17];
        if (cy < 0.f || cy >= ch) continue;
        if (ring <= 0.f && (cx < 0.f || cx >= cw)) continue;
        float vis = a;
        if (ring > 0.f) {
            if (X < Q[18] - 0.5f * Q[19] || X > Q[21] + 0.5f * Q[19]) continue;
            vis *= clamp01((X - Q[18]) / Q[19] + 0.5f) * clamp01((Q[21] - X) / Q[19] + 0.5f);
        }
        const float ev = clamp01((cx - Q[14]) / Q[15] + 0.5f);           // erased left of Q[14]
        if (ev <= 0.f) continue;
        vis *= ev;
        const float fy = cy - 0.5f;
        const int iy = (int)floorf(fy);
        const float ty = fy - iy;
        const int ya = max(iy, 0), yb = min(iy + 1, ch - 1);
        const unsigned char* C = canvas + RI[r];
        float ink;
        if (ring > 0.f) {
            const int TW = (int)ring;
            const float u = cx - 0.5f;
            const float fu = floorf(u);
            const float fr = u - fu;
            int c0 = (int)fmodf(fu, ring);
            if (c0 < 0) c0 += TW;
            const int c1 = (c0 + 1) % TW;
            const float i0 = C[ya * TW + c0] * (1.f - ty) + C[yb * TW + c0] * ty;
            const float i1 = C[ya * TW + c1] * (1.f - ty) + C[yb * TW + c1] * ty;
            ink = (i0 + (i1 - i0) * fr) * (1.f / 255.f);
        } else {
            const int ix = (int)cx;
            ink = (C[ya * cw + ix] * (1.f - ty) + C[yb * cw + ix] * ty) * (1.f / 255.f);
        }
        const float rev = cx < Q[7] ? Q[8] : clamp01((Q[3] - cx) / Q[4] + 0.5f);
        const float inkv = ink * rev * Q[16];
        const float sw = (cx >= Q[9] && cx < Q[10]) ? Q[11] : 0.f;
        const float staff = sw > 0.f ? fminf(1.f, sw * staff_lines(cy, Q[12], Q[13], sp, th)) : 0.f;
        blend_ink(c, inkc, fmaxf(inkv, staff) * vis);
        if (cx < Q[3] && cx >= Q[7] && inkv > 0.004f && Q[5] > 0.f)       // the fresh ink glowing behind the front
            blend_glow(c, fresh_c, inkv * vis * Q[5] * __expf(-(Q[3] - cx) / Q[6]), night);
    }
    glyph_pass(c, X, Y, GN, GU, GA, GAW, GT, inkc, night);
    const float g = 255.f * P[18];
    out[y * W + x] = pack(make_float4(clamp01(c.x) * g, clamp01(c.y) * g, clamp01(c.z) * g, 255.f));
}
"""


def _pal3(c, t):
    """The palette at t (numpy, like the kernels' pal): (..., 3) in 0..1."""
    t = np.asarray(t, np.float64)[..., None]
    c = np.asarray(c, np.float64)
    return np.clip(c[0:3] + c[3:6] * np.cos(2.0 * math.pi * (c[6:9] * t + c[9:12])), 0.0, 1.0)


def _cl(x, lo, hi):
    """np.clip without its call overhead (a few us a call on small arrays)."""
    return np.minimum(np.maximum(x, lo), hi)


def _ease(u):
    """Smoothstep on 0..1 (arrays)."""
    u = np.minimum(np.maximum(u, 0.0), 1.0)
    return u * u * (3.0 - 2.0 * u)


EMPTY, SKIP = "empty", "skip"


class _Line:
    """A line of grand staff on one canvas: (page) in a slot of the page, (scroll) the tape."""
    _uids = 0

    def __init__(self, ci: int, slot: int, g: int, sp: float, cw: float) -> None:
        _Line._uids += 1
        self.uid = _Line._uids
        self.ci, self.slot, self.g = ci, slot, g
        self.phrase = None
        self.m = 0
        self.t1 = TOP_SP * sp
        self.t2 = self.t1 + STAVE_GAP_SP * sp
        self.fifths = 0
        self.hreq = 0
        self.header = False
        self.hdr_t = None
        self.hdr_w = 0.0
        self.staff_x0 = (MARGIN_SP + 0.4) * sp
        self.hdr_a = 0.0
        self.ts_w = 0.0
        self.front = 0.0
        self.front_v = 0.0
        self.bars: dict = {}                    # phrase bar -> [x0, width, t_land]
        self.sent = 0
        self.shown = False
        self.t_shown = None
        self.show_a = 0.0
        self.erase_t = None
        self.erase_x = -1e9
        self.alpha = 1.0
        self.weight = 1.0
        self.ink_a = 1.0
        self.lift_t = None
        self.open_to = float(cw)
        self.open_a = 1.0
        self.end_x = None                       # the phrase ended on this line: its last bar's end
        self.glow = 0.0
        self.glow_len = 4.0 * sp
        self.placeholder = False

    @property
    def music_x0(self) -> float:
        return self.hdr_w + self.ts_w


class _Phrase:
    _ids = 0

    def __init__(self, g0: int) -> None:
        _Phrase._ids += 1
        self.id = _Phrase._ids
        self.epoch = 0
        self.g0 = g0
        self.t0 = None
        self.lines: dict[int, _Line] = {}
        self.plan = LinePlan()
        self.metre = None
        self.L, self.unit, self.sig = 4, 4, (4, 4)
        self.v = None                          # before its metre: px a second along its (open) lines
        self.committed = 0
        self.wait: list = []
        self.sub: dict = {}
        self.t_sub: dict = {}
        self.items: dict = {}
        self.next_place = 0
        self.ended = False
        self.lift_t = None
        self.queued: list = []
        self.nat = None
        self.bar_s = None
        self.rows: set = set()
        self.last_wave = -1e9
        self.heads: dict = {}                  # note id -> engraved (line, x, y, glyph, stem, acc), canvas coords
        self.ts_t = None                       # the time signature flipped in at
        self.ts_old = None
        self.final = False
        self.U0 = self.Um0 = None              # (scroll) its place on the tape, its music's start
        self.v_bar = None                      # (scroll) its bars' width a second
        self.newest = None                     # its newest note's row
        self.k_base = 0                        # the builder's bar number of its bar 0 (None: not known yet)
        self.cut_t = None                      # (a rewrite carried on) bars ending by then are written already
        self.bar_t: dict = {}                  # bar -> (t_start, t_end)
        self.ruler = None
        self.bar_fifths: dict = {}             # (scroll) the key each bar is written in
        self.ins_sub: dict = {}                # (scroll) engraver key -> the bar an inline key change precedes
        self.fifths_tape = 0
        self.open_m: set = set()               # its lines laid out in open metre (their beats became bars)

    @property
    def done(self) -> bool:
        return self.ended and not self.wait and not self.items and self.next_place >= self.committed


class _Notes:
    """The notes as structure-of-arrays: motion, glyph, stem and accidental state per row (canvas coordinates of
    the row's line; the line's screen offset added when drawn)."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        z = lambda v=0.0: np.full(cap, v, np.float64)        # noqa: E731
        self.used = np.zeros(cap, bool)
        self.state = np.zeros(cap, np.int8)                  # 0 provisional 1 engraved 2 handing over 3 inked 4 puff
        self.nid = np.zeros(cap, np.int64)
        self.pitch, self.t_on, self.t_off, self.t_born = z(), z(), z(np.inf), z()   # t_off inf: held
        self.lid = np.zeros(cap, np.int64)
        self.tq = z()                                        # its time on the grid it's placed by (open lines)
        self.t_next = z(np.inf)                              # the next onset in its staff (its written end)
        self.C = np.zeros((2, 6, cap))                       # quintic coefficients, x and y
        self.h_t0, self.h_T, self.arc = z(), z(1e-6), z()
        self.tx, self.ty = z(), z()
        self.ga, self.gb, self.g_t0 = np.zeros(cap, np.int64), np.zeros(cap, np.int64), z(-1e9)
        self.sa, self.sb = np.zeros(cap, bool), np.zeros(cap, bool)       # stem on (from, to)
        self.S = np.zeros((2, 3, cap))                       # stem (x, y0, y1) rel. to the head origin (sp): from, to
        self.s_t0, self.s_T, self.s_flip = z(-1e9), z(STEM_S), np.zeros(cap, bool)
        self.aa, self.ab = np.full(cap, -1, np.int64), np.full(cap, -1, np.int64)
        self.adxa, self.adxb, self.a_t0 = z(), z(), z(-1e9)
        self.st_top = z()
        self.hand_t0, self.die_t0 = z(np.inf), z(np.inf)                        # inf: not (yet)
        self.readable = np.zeros(cap, bool)
        self.note = [None] * cap
        self.line = [None] * cap
        self.ph = [None] * cap
        self.key = [None] * cap
        self.cand = [None] * cap
        self.k = np.full(cap, -1, np.int64)
        self.by_id: dict = {}
        self.free_rows = list(range(cap - 1, -1, -1))         # (a stack: alloc / free without a scan)
        self.grp = [None] * cap                              # (line, bar) it was last laid out in (_relayout)
        self.moves = np.zeros(cap, np.int64)
        self.pend = np.zeros(cap, bool)                      # engraved, its settle still queued (_settle_step)
        self.fixed = np.zeros(cap, bool)                     # its bar is committed: its 16th and staff are known
        self.bar_end = z(np.inf)                             # (engraved) its bar's right edge on its line

    def alloc(self, nid: int):
        if not self.free_rows:
            return None
        i = self.free_rows.pop()
        self.used[i] = True
        self.state[i] = 0
        self.nid[i] = nid
        self.by_id[nid] = i
        self.t_off[i] = np.inf
        self.t_next[i] = np.inf
        self.hand_t0[i] = self.die_t0[i] = np.inf
        self.ga[i] = self.gb[i] = GL.BLACK
        self.g_t0[i] = -1e9
        self.sa[i] = self.sb[i] = False
        self.s_t0[i] = -1e9
        self.s_flip[i] = False
        self.aa[i] = self.ab[i] = -1
        self.a_t0[i] = -1e9
        self.readable[i] = False
        self.key[i] = self.cand[i] = None
        self.grp[i] = None
        self.k[i] = -1
        self.moves[i] = 0
        self.fixed[i] = False
        self.pend[i] = False
        self.bar_end[i] = np.inf
        self.arc[i] = 0.0
        return i

    def free(self, i: int) -> None:
        if not self.used[i]:
            return
        self.used[i] = False
        self.free_rows.append(i)
        if self.by_id.get(int(self.nid[i])) == i:
            del self.by_id[int(self.nid[i])]
        ph = self.ph[i]
        if ph is not None:
            ph.rows.discard(i)
        self.note[i] = self.ph[i] = self.line[i] = None

    # -- motion: quintic (minimum-jerk) hops, re-planned from the drawn state ------------------------------------
    def state_at(self, i: int, t: float):
        """(x, vx, ax, y, vy, ay) as drawn at t (the arc included)."""
        T = float(self.h_T[i])
        tau = min(max(t - float(self.h_t0[i]), 0.0), T)
        if tau >= T:
            return float(self.tx[i]), 0.0, 0.0, float(self.ty[i]), 0.0, 0.0
        out = []
        for a in (0, 1):
            c = self.C[a, :, i].tolist()
            x = c[0] + tau * (c[1] + tau * (c[2] + tau * (c[3] + tau * (c[4] + tau * c[5]))))
            v = c[1] + tau * (2 * c[2] + tau * (3 * c[3] + tau * (4 * c[4] + tau * 5 * c[5])))
            acc = 2 * c[2] + tau * (6 * c[3] + tau * (12 * c[4] + tau * 20 * c[5]))
            out += [x, v, acc]
        arc = float(self.arc[i])
        if arc:
            w = math.pi / T
            u = tau / T
            out[3] -= arc * math.sin(math.pi * u)
            out[4] -= arc * w * math.cos(math.pi * u)
            out[5] += arc * w * w * math.sin(math.pi * u)
        return tuple(out)

    def place(self, i: int, ln, x: float, y: float) -> None:
        self.lid[i] = ln.ci
        self.line[i] = ln
        self.C[:, :, i] = 0.0
        self.C[0, 0, i], self.C[1, 0, i] = x, y
        self.tx[i], self.ty[i] = x, y
        self.h_t0[i], self.h_T[i], self.arc[i] = -1e9, 1e-6, 0.0

    def hop(self, i: int, ln, x1: float, y1: float, T: float, arc: float, now: float, offx, offy,
            start: float | None = None) -> None:
        x, vx, ax, y, vy, ay = self.state_at(i, now)
        ci = ln.ci
        self.line[i] = ln
        oc = int(self.lid[i])
        if oc != ci:
            x += float(offx[oc] - offx[ci])
            y += float(offy[oc] - offy[ci])
            self.lid[i] = ci
        t0 = now
        if start is not None and start > now and abs(vx) + abs(vy) < 1.0:
            t0 = start
            vx = vy = ax = ay = 0.0
        T = max(T, 1e-3)
        for a, (p0, v, acc, p1) in enumerate(((x, vx, ax, x1), (y, vy, ay, y1))):
            D = p1 - p0
            self.C[a, :, i] = (p0, v, 0.5 * acc, (20 * D - 12 * v * T - 3 * acc * T * T) / (2 * T ** 3),
                               (-30 * D + 16 * v * T + 3 * acc * T * T) / (2 * T ** 4),
                               (12 * D - 6 * v * T - acc * T * T) / (2 * T ** 5))
        self.tx[i], self.ty[i] = x1, y1
        self.h_t0[i], self.h_T[i], self.arc[i] = t0, T, arc

    def shift(self, i: int, dx: float, dy: float) -> None:
        """Move the row's whole path (a line's coordinates changed under it, e.g. scroll's re-anchor)."""
        self.C[0, 0, i] += dx
        self.C[1, 0, i] += dy
        self.tx[i] += dx
        self.ty[i] += dy

    # -- glyph, stem, accidental targets ---------------------------------------------------------------------
    def set_glyph(self, i: int, g: int, t0: float) -> None:
        if g == self.gb[i]:
            return
        cur = self.gb[i] if t0 - self.g_t0[i] >= 0.5 * MORPH_S else self.ga[i]
        self.ga[i], self.gb[i], self.g_t0[i] = cur, g, t0

    def stem_now(self, i: int, t: float):
        """(on, x, y0, y1) of the stem as drawn at t (rel. sp)."""
        T = self.s_T[i]
        u = min(max((t - self.s_t0[i]) / T, 0.0), 1.0)
        e = u * u * (3 - 2 * u)
        A, B = self.S[0, :, i], self.S[1, :, i]
        sa, sb = self.sa[i], self.sb[i]
        if sa and sb:
            if self.s_flip[i]:
                if u < 0.5:
                    return True, A[0], A[1], A[1] + (A[2] - A[1]) * math.cos(math.pi * u)
                return True, B[0], B[1], B[1] - (B[2] - B[1]) * math.cos(math.pi * u)
            return True, A[0] + (B[0] - A[0]) * e, A[1] + (B[1] - A[1]) * e, A[2] + (B[2] - A[2]) * e
        if sa:
            return e < 1.0, A[0], A[1], A[1] + (A[2] - A[1]) * (1.0 - e)
        if sb:
            return e > 0.0, B[0], B[1], B[1] + (B[2] - B[1]) * e
        return False, 0.0, 0.0, 0.0

    def set_stem(self, i: int, on: bool, x: float, y0: float, y1: float, t0: float, T: float = STEM_S,
                 instant: bool = False) -> None:
        if (on == self.sb[i] and (not on or (abs(self.S[1, 0, i] - x) < 1e-3 and abs(self.S[1, 1, i] - y0) < 1e-3
                                                and abs(self.S[1, 2, i] - y1) < 1e-3))):
            return
        if instant:
            self.sa[i] = self.sb[i] = on
            self.S[0, :, i] = self.S[1, :, i] = (x, y0, y1)
            self.s_t0[i] = -1e9
            self.s_flip[i] = False
            return
        con, cx, cy0, cy1 = self.stem_now(i, t0)
        self.sa[i] = con
        self.S[0, :, i] = (cx, cy0, cy1)
        self.sb[i] = on
        self.S[1, :, i] = (x, y0, y1)
        self.s_flip[i] = bool(con and on and (cy1 - cy0) * (y1 - y0) < 0)
        self.s_T[i] = TWIST_S if self.s_flip[i] else T
        self.s_t0[i] = t0

    def set_acc(self, i: int, g: int, dx: float, t0: float, instant: bool = False) -> None:
        if g == self.ab[i] and (g < 0 or abs(self.adxb[i] - dx) < 1e-3):
            return
        if instant:
            self.aa[i] = self.ab[i] = g
            self.adxa[i] = self.adxb[i] = dx
            self.a_t0[i] = -1e9
            return
        u = (t0 - self.a_t0[i]) / ACC_S
        if u < 0.5:                                  # mid-swap: the one showing goes on from where it is
            g0, dx0 = self.aa[i], self.adxa[i]
        else:
            g0, dx0 = self.ab[i], self.adxb[i]
        self.aa[i], self.adxa[i] = g0, dx0
        self.ab[i], self.adxb[i] = g, dx
        self.a_t0[i] = t0



# -- the glyph records on the GPU (perf: no per-frame numpy over the notes) ---------------------------------------
NFLD = 44                                                  # a row's fields in the upload (rows x NCAP, see _pack)
_REC_SRC = r"""
#define NF %(NF)d
#define MAXN %(MAXN)d
#define NCAP %(NCAP)d
#define NFLD %(NFLD)d
__device__ __forceinline__ float cl01(float x) { return fminf(fmaxf(x, 0.f), 1.f); }
__device__ __forceinline__ float ease(float u) { u = cl01(u); return u * u * (3.f - 2.f * u); }

extern "C" __global__ void recs(const float* __restrict__ F, const float* __restrict__ IX, const int n,
                                const float* __restrict__ XR, const int m, const float* __restrict__ L, const int NC,
                                const float* __restrict__ P, const float* __restrict__ ADV, float* __restrict__ REC,
                                float* __restrict__ GN, float* __restrict__ GU)
{
    __shared__ float key[512];
    __shared__ int ord[512];
    __shared__ float r0[512], r1[512], r2[512], r3[512], r4[512];
    const int t = threadIdx.x;
    const int tot = n + m;
    const float sp = P[0];
    float q[NF];
    for (int f = 0; f < NF; ++f) q[f] = 0.f;
    if (t < n) {
        const int i = (int)IX[t];
#define FV(f) F[(f) * NCAP + i]
        const int ci = (int)FV(42);
        const float T = FV(1);
        const float tau = fminf(fmaxf(FV(0), 0.f), T);
        const bool fin = tau >= T;
        float x, y;
        if (fin) { x = FV(3); y = FV(4); }
        else {
            x = FV(5) + tau * (FV(6) + tau * (FV(7) + tau * (FV(8) + tau * (FV(9) + tau * FV(10)))));
            y = FV(11) + tau * (FV(12) + tau * (FV(13) + tau * (FV(14) + tau * (FV(15) + tau * FV(16)))));
            y -= FV(2) * sinf(3.14159265358979f * tau / fmaxf(T, 1e-6f));
        }
        float X = x + L[ci], Y = y + L[NC + ci];
        float wy = 0.f, wj = 0.f;
        const int scroll = (int)P[2];
        const float SR = P[3], XL = P[4], TW = P[5];
        if (scroll && SR > 1.f) {
            wj = fminf(fmaxf(ceilf((XL - X) / TW), 0.f), SR - 1.f);
            wy = -wj * P[6];
            X += wj * TW; Y += wy;
        }
        const float age = FV(17);
        float s = 1.f + P[1] * (1.f - ease(age * (1.f / %(POP_S)r)));
        const float ht = cl01(FV(18) * (1.f / %(HAND_S)r)), pu = cl01(FV(19) * (1.f / %(PUFF_S)r));
        float alpha = fminf(1.f, 0.85f + age * (0.15f / %(BIRTH_S)r)) * (1.f - ht) * (1.f - pu) * L[2 * NC + ci]
                      * cl01((x - L[3 * NC + ci]) * (1.f / (%(ERASE)r * sp)) + 0.5f);
        s *= 1.f + 0.4f * pu;
        if (scroll) {
            alpha *= cl01((X - XL) * (0.5f / sp));
            if (SR > 1.f && wj > 0.f) alpha *= cl01((XL + TW - X) * (0.5f / sp));
        }
        const float ga = FV(21), gb = FV(22);
        const float mt = cl01(FV(20) * (1.f / %(MORPH_S)r));
        const float OX = X + (0.5f * sp) * ADV[(int)gb] * (1.f - s);
        const float su = cl01(FV(23) / FV(24));
        const float se = su * su * (3.f - 2.f * su);
        const bool sa = FV(25) > 0.5f, sb = FV(26) > 0.5f, flip = FV(27) > 0.5f;
        const bool useB = sb && (!sa || (flip && su >= 0.5f));
        const bool lerp = sa && sb && !flip;
        const float A0 = FV(28), A1 = FV(29), A2 = FV(30), B0 = FV(31), B1 = FV(32), B2 = FV(33);
        const float sx_ = lerp ? A0 + (B0 - A0) * se : (useB ? B0 : A0);
        const float sy0 = lerp ? A1 + (B1 - A1) * se : (useB ? B1 : A1);
        const float cosu = cosf(3.14159265358979f * su);
        const float kk = lerp ? 1.f : ((sa && sb) ? fabsf(cosu) : (sa ? 1.f - se : se));
        const float ty1 = lerp ? A2 + (B2 - A2) * se : (useB ? B2 : A2);
        const float sy1 = lerp ? ty1 : sy0 + (ty1 - sy0) * kk;
        const bool son = (sa || sb) && fabsf(sy1 - sy0) > 0.05f;
        const float au = cl01(FV(34) * (1.f / %(ACC_S)r));
        const float aa = FV(35), ab = FV(36);
        const bool same = aa == ab, first = !same && au < 0.5f;
        const float ag = first ? aa : ab;
        const float adx = same ? FV(37) + (FV(38) - FV(37)) * (au * au * (3.f - 2.f * au)) : (first ? FV(37) : FV(38));
        float asc = same ? 1.f : fabsf(1.f - 2.f * au);
        asc = ag >= 0.f ? asc : 0.f;
        const float tyt = FV(4) + wy;
        float top = FV(39) + wy;
        float bot = top + 4.f * sp;
        const float nab = fminf(floorf((top - tyt) * (1.f / sp) + 0.01f), 6.f);
        const float nbe = fminf(floorf((tyt - bot) * (1.f / sp) + 0.01f), 6.f);
        const bool above = nab >= 1.f;
        top += Y - tyt;
        bot += Y - tyt;
        const float gadv = ADV[(int)(mt >= 0.5f ? gb : ga)];
        const float tint = fmodf(FV(40) * 7.f, 12.f) * P[7] + P[8];
        const float hold = __expf(-fmaxf(0.f, FV(41)) * (1.f / %(REL_S)r));
        const float fresh = fmaxf(__expf(-age * (1.f / %(FRESH_S)r)), 0.75f * hold);
        const float halo = P[9] * (age < 0.25f ? P[10] : 1.f);
        q[0] = OX; q[1] = Y; q[2] = sp * s; q[3] = sp * s;
        q[4] = ga; q[5] = gb; q[6] = mt; q[7] = alpha;
        q[8] = X + sx_ * sp; q[9] = Y + sy0 * sp; q[10] = Y + sy1 * sp;
        q[11] = son ? %(STEM_HW)r * sp : 0.f;
        q[12] = asc > 0.01f ? ag : -1.f;
        q[13] = X + adx * sp; q[14] = asc;
        q[15] = above ? top - sp : bot + sp;
        q[16] = above ? -sp : sp;
        q[17] = above ? nab : fmaxf(nbe, 0.f);
        q[18] = 0.5f * fresh; q[19] = halo * fresh;
        for (int c = 0; c < 3; ++c)
            q[20 + c] = cl01(P[12 + c] + P[15 + c] * cosf(6.28318530717959f * (P[18 + c] * tint + P[21 + c])));
        q[27] = X - %(LEDGER_EXT)r * sp; q[28] = X + (gadv + %(LEDGER_EXT)r) * sp; q[29] = Y;
#undef FV
    } else if (t < tot) {
        for (int f = 0; f < NF; ++f) q[f] = XR[(t - n) * NF + f];
    }
    float kx = 3.0e38f;
    if (t < tot) {                                       // its box (glyphs.boxes_sort)
        const float BIG = 1e9f;
        const float pad = (%(PAD)r + 2.5f * P[11]) * sp + 2.f;
        const bool hg = q[4] >= 0.f;
        const float sx = fabsf(q[2]), sy = fabsf(q[3]);
        const float gadv = ADV[(int)fmaxf(q[4], 0.f)];
        float x0 = hg ? q[0] - 0.1f * sx : BIG, x1 = hg ? q[0] + (gadv + 0.2f) * sx : -BIG;
        float y0 = hg ? q[1] - 2.2f * sy : BIG, y1 = hg ? q[1] + 2.2f * sy : -BIG;
        if (q[11] > 0.f) {
            x0 = fminf(x0, q[8] - q[11]); x1 = fmaxf(x1, q[8] + q[11]);
            y0 = fminf(y0, fminf(q[9], q[10])); y1 = fmaxf(y1, fmaxf(q[9], q[10]));
        }
        if (q[12] >= 0.f && q[14] > 0.01f) {
            const float r = 1.2f * sp * q[14];
            x0 = fminf(x0, q[13] - r); x1 = fmaxf(x1, q[13] + r);
            y0 = fminf(y0, q[29] - 2.f * sp); y1 = fmaxf(y1, q[29] + 2.f * sp);
        }
        if (q[17] > 0.f) {
            const float lend = q[15] + fmaxf(q[17] - 1.f, 0.f) * q[16];
            x0 = fminf(x0, q[27]); x1 = fmaxf(x1, q[28]);
            y0 = fminf(y0, fminf(q[15], lend)); y1 = fmaxf(y1, fmaxf(q[15], lend));
        }
        if (x0 > x1) { q[7] = 0.f; x0 = x1 = y0 = y1 = 0.f; }
        q[23] = x0 - pad; q[24] = y0 - pad; q[25] = x1 + pad; q[26] = y1 + pad;
        for (int f = 0; f < NF; ++f) REC[t * NF + f] = q[f];
        kx = q[23];
        r0[t] = q[23]; r1[t] = q[24]; r2[t] = q[25]; r3[t] = q[26]; r4[t] = q[25] - q[23];
    } else {
        r0[t] = 3.0e38f; r1[t] = 3.0e38f; r2[t] = -3.0e38f; r3[t] = -3.0e38f; r4[t] = -3.0e38f;
    }
    key[t] = kx;
    ord[t] = t;
    __syncthreads();
    for (int k = 2; k <= 512; k <<= 1) {                 // bitonic, ascending by (x0, order): numpy's stable sort
        for (int j = k >> 1; j > 0; j >>= 1) {
            const int o = t ^ j;
            if (o > t) {
                const float a = key[t], b = key[o];
                const int oa = ord[t], ob = ord[o];
                const bool gt = (a > b) || (a == b && oa > ob);
                if (gt == ((t & k) == 0)) { key[t] = b; key[o] = a; ord[t] = ob; ord[o] = oa; }
            }
            __syncthreads();
        }
    }
    for (int w = 256; w > 0; w >>= 1) {
        if (t < w) {
            r0[t] = fminf(r0[t], r0[t + w]); r1[t] = fminf(r1[t], r1[t + w]);
            r2[t] = fmaxf(r2[t], r2[t + w]); r3[t] = fmaxf(r3[t], r3[t + w]); r4[t] = fmaxf(r4[t], r4[t + w]);
        }
        __syncthreads();
    }
    if (t < tot) {
        const int sidx = ord[t];
        for (int f = 0; f < NF; ++f) GN[t * NF + f] = REC[sidx * NF + f];
    }
    if (t < 16) {
        float u = 0.f;
        if (t == 0) u = (float)tot;
        else if (tot > 0 && t == 2) u = r0[0];
        else if (tot > 0 && t == 3) u = r1[0];
        else if (tot > 0 && t == 4) u = r2[0];
        else if (tot > 0 && t == 5) u = r3[0];
        else if (tot > 0 && t == 6) u = r4[0];
        else if (t == 7) u = sp;
        else if (t == 8) u = P[11];
        GU[t] = u;
    }
}
""" % dict(NF=GL.NF, MAXN=GL.MAXN, NCAP=NCAP, NFLD=NFLD, POP_S=POP_S, HAND_S=HAND_S, PUFF_S=PUFF_S, BIRTH_S=BIRTH_S,
           ERASE=ERASE_FEATHER_SP, MORPH_S=MORPH_S, ACC_S=ACC_S, REL_S=REL_S, FRESH_S=FRESH_S, STEM_HW=GL.STEM_HW,
           LEDGER_EXT=GL.LEDGER_EXT, PAD=GL.PAD)


class ScoreV2(Source):
    name = "score_v2"
    label = "Live score v2"
    description = ("The piano's playing written out as notation from the first frame: each note one object, placed "
                   "where it will be engraved and settling into the ink.")
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette="ember", render_scale=1.0, period=1.0, drift=0.01),
            "layout": enum(LAYOUTS, "page", "Layout", tier="structural", basic=True),
            "look": enum(LOOKS, "paper", "Look", tier="structural", basic=True),
            "lines": num(3, 5, 4, "Lines on the page", tier="structural", integer=True),
            "rows": num(1, 3, 2, "Scroll rows", tier="structural", integer=True),
            "span": enum(SPANS, "screen", "Span (scroll)", tier="structural"),
            "span_header": enum(SPAN_HEADERS, "every", "Header when spanning", tier="structural"),
            "staff_size": num(0.6, 1.5, 1.0, "Staff size", tier="structural"),
            "spacing": enum(SPACINGS, "proportional", "Spacing", tier="structural"),
            "line_s": num(4.0, 14.0, LINE_S, "Music a line holds", unit="s", tier="structural"),
            "flourish": num(0.0, 1.0, 0.7, "Flourish"),
            "note_glow": num(0.0, 1.5, 0.8, "Note glow", basic=True),
            "ink_glow": num(0.0, 3.0, 1.2, "Fresh ink glow", unit="s"),
        }

    # -- setup ----------------------------------------------------------------------------------------------------
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.layout = params.get("layout") if params.get("layout") in LAYOUTS else "page"
        self.scroll = self.layout == "scroll"
        self._ctx0, self._params0 = ctx, dict(params)
        self.span_room = self.scroll and params.get("span") == "room"
        self.span_first = params.get("span_header") == "first"
        seg = getattr(self, "_seg_cfg", None)
        if seg is None:
            seg = self._segment()
        self.span = seg if (self.span_room and seg is not None and seg[1] > 1) else None     # (k, n): spanning
        self.spacing = params.get("spacing") if params.get("spacing") in SPACINGS else "proportional"
        self.line_s = float(params.get("line_s", LINE_S))
        self.flourish = float(params.get("flourish", 0.7))
        self.NL = int(min(5, max(3, int(params.get("lines", 4))))) if not self.scroll else 1
        size = float(params["staff_size"])
        if self.scroll:
            sp = SP_SCROLL * self.h * size * (SPAN_SP if self.span is not None else 1.0)
        else:
            sp = min(SP_PAGE * self.h * size, 0.975 * self.h / ((self.NL - 1) * PITCH_SP + ROW_SP))
        self.sp = sp = round(max(4.0, sp), 2)
        self.th = max(1.0, 0.075 * sp)
        self.FE = FEATHER_SP * sp
        self.ch = int(ROW_SP * sp)
        self.cw = max(64, int(self.w * 0.95))
        self.x_off = int(0.5 * (self.w - self.cw))
        self.pitch = PITCH_SP * sp
        self.y_top = int(round(0.5 * (self.h - ((self.NL - 1) * self.pitch + self.ch))))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        mod = raw_module(_SRC)
        self.k = mod.get_function("score")
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        self.page = cp.zeros((self.h, self.w), cp.uint8)
        launch_2d(mod.get_function("bake_page"), self.w, self.h,
                  (self.page, np.int32(self.w), np.int32(self.h), np.int32(LOOKS.index(params["look"])),
                   np.uint32(self.seed)))
        self.SR = int(min(3, max(1, int(params.get("rows", 2))))) if self.scroll else 1
        if self.span is not None:
            self.SR = 1                                      # (one row a screen: the screens are the rows, side by side)
        if self.scroll:
            self.NC = 2                                      # the tape (a ring) and the sticky header
            nw = min(self.span[1], SPAN_NMAX) if self.span is not None else max(2, self.SR)
            self.TW = int(math.ceil((nw * self.w + 64 * sp) / 64.0) * 64)
            self.HW = int(HW_MAX_SP * sp)
            self.cwid = [self.TW, self.HW]
            sizes = [self.ch * self.TW, self.ch * self.HW]
        else:
            self.NC = self.NL + 2
            self.cwid = [self.cw] * self.NC
            sizes = [self.ch * self.cw] * self.NC
        self.coff = [int(sum(sizes[:i])) for i in range(self.NC)]
        self.NR = 2 * self.SR if self.scroll else self.NC   # canvas entries drawn (scroll: tape and header a row)
        self.canvas = cp.zeros(int(sum(sizes)), cp.uint8)
        self.offx = np.zeros(self.NC)
        self.offy = np.zeros(self.NC)
        # staging for engraved parts
        self.slot_bytes = self.ch * max(self.cw, self.HW if self.scroll else 0)
        self._pinned = [cp.cuda.alloc_pinned_memory(self.slot_bytes) for _ in range(INK_SLOTS)]
        self.slots = [np.frombuffer(m, np.uint8, self.slot_bytes) for m in self._pinned]
        self.slot_free: "queue.Queue" = queue.Queue()
        for j in range(INK_SLOTS):
            self.slot_free.put(j)
        self.slot_fly: list = []                    # (slot, event): copies queued, the slot reused once done
        self.ink_ready: "queue.Queue" = queue.Queue()
        self.ink_thread = None
        self.ink_stop = False
        self.side = cp.cuda.Stream(non_blocking=True)
        self.settle_q: list = []                    # landed bars' notes still to set off (_settle_step)
        # the frame's parameters: one async upload a frame from pinned memory, double-buffered
        self.UP_R = 64
        self.UP_RI = (self.UP_R + self.NR * RS + 15) // 16 * 16
        self.UP_GN = (self.UP_RI + self.NR + 15) // 16 * 16
        self.UP_GU = self.UP_GN + GL.MAXN * GL.NF
        self.UP_F = (self.UP_GU + GL.NU + 15) // 16 * 16              # (GPU records) the note rows' fields
        self.UP_IX = self.UP_F + NFLD * NCAP                         # the drawn rows
        self.UP_X = self.UP_IX + GL.MAXN                             # bar line / time signature records
        self.UP_L = self.UP_X + GL.MAXN * GL.NF                      # per canvas: offx, offy, alpha, erase
        self.UP_Q = self.UP_L + 4 * self.NC                          # the frame's scalars
        self.UP_N = self.UP_Q + 32
        self.up_dev = cp.zeros(self.UP_N, cp.float32)
        self._up_pin = [cp.cuda.alloc_pinned_memory(4 * self.UP_N) for _ in range(2)]
        self.up_host = [np.frombuffer(m, np.float32, self.UP_N) for m in self._up_pin]
        self.up_ev = [None, None]
        self.up_k = 0
        self.P = self.up_dev[0:64]
        self.R = self.up_dev[self.UP_R:self.UP_R + self.NR * RS]
        self.RI = self.up_dev[self.UP_RI:self.UP_RI + self.NR].view(cp.int32)
        self.GN = self.up_dev[self.UP_GN:self.UP_GU]
        self.GU = self.up_dev[self.UP_GU:self.UP_GU + GL.NU]
        self.krec = raw_module(_REC_SRC).get_function("recs")
        self.rec_scratch = cp.zeros(GL.MAXN * GL.NF, cp.float32)
        self.gpu_recs = None                         # (n notes, m other records) when this frame's are the GPU's
        d = self.up_dev
        self._rv = (d[self.UP_F:self.UP_IX], d[self.UP_IX:self.UP_X], d[self.UP_X:self.UP_L], d[self.UP_L:self.UP_Q],
                    d[self.UP_Q:self.UP_N])
        self.Ph = np.zeros(64, np.float32)
        self.Rh = np.zeros(self.NR * RS, np.float32)
        self.RIh = np.array(self.coff * self.SR if self.scroll else self.coff, np.int32)
        self.recs = np.zeros((GL.MAXN, GL.NF), np.float32)
        self.GNh = np.zeros((GL.MAXN, GL.NF), np.float32)
        self.GUh = np.zeros(GL.NU, np.float32)
        self.nrec = 0
        self.gatlas, self.gtable, gt = GL.device_atlas(cp)
        self.adv = gt[:, 10].astype(np.float64)
        self.adv_dev = cp.asarray(self.adv.astype(np.float32))
        self.gcent = GL.centres()
        self.pump_until = 0.0
        from stream_service.score import mei  # noqa: F401  (first use loads it: here, not on a frame)
        w8 = cp.zeros((2, 16), cp.uint8)
        cp.maximum(w8[:, :8], w8[:, 8:], out=w8[:, :8])
        w8.fill(0)
        from stream_service.score.room import RemoteRoom
        self.room = RemoteRoom.join(ROOM_KEY, self)
        self.builder = self.room.builder
        self.seen_rev = 0
        self.engraver = None
        try:
            import verovio  # noqa: F401  (the worker imports them; this only checks they're installed)
            import resvg_py  # noqa: F401
            from stream_service.score.client import Engraver
            self.engraver = Engraver(self.cw, space_px=sp)
        except Exception as e:
            log.warning("score_v2: no engraving (%s); the glyph notes are the score", e)
        self.t_sound = 0.0
        self.clock = 0.0
        self.breath = 0.0
        self.drift = 0.0
        self.kick = 0.0
        self.A, self.C = (v * sp for v in PROP_AC[self.spacing])
        self.hdr_cache: dict = {}
        self.P_free = 0.6
        self.spell_c = None
        self.spells: dict = {}
        self.N = _Notes(NCAP)
        self.held: set = set()
        self.last_nid = 0
        self.epoch_sig = None
        self.epoch_t = -1e9
        self.epoch_force = False
        self.epoch_carry = False                     # (rows carried to the next frame)
        self._placed_n = 0
        self.relayout_due = False                    # every unwritten note re-laid (a layout change) ...
        self.dirty_rows: set = set()                 # ... or just these notes (and their bar and chord mates)
        self.dirty_lines: set = set()                # ... or the notes on these lines
        self.join_queue: list = []                   # (joining) written notes still to draw, a few a frame
        self._tcache: dict = {}                      # one layout pass's bar geometry
        self._spell_pass = None
        self.play_g = 0
        self.spills = 0
        self.bars_empty = 0
        self.dbg = None                              # the harness's event log (a list), or None
        self.joined = False                          # the room's first music taken (a scene may join mid-phrase)
        self.join_t = None
        self.join_rows = False
        # gates
        G = NGATE
        self.g_used = np.zeros(G, bool)
        self.g_kind = np.zeros(G, np.int64)
        self.g_lid = np.zeros(G, np.int64)
        self.g_x = np.zeros(G)
        self.g_tx = np.zeros(G)
        self.g_v = np.zeros(G)
        self.g_a = np.zeros(G)
        self.g_at = np.zeros(G)
        self.g_t0 = np.zeros(G)
        self.g_line = [None] * G
        self.g_key: dict = {}
        self.lines: list[_Line] = []
        self.old: list[_Phrase] = []
        self.gnext = 0
        if self.scroll:
            self._setup_scroll()
        else:
            for s in range(1, self.NL):              # an empty page: staves waiting
                ln = _Line(self._alloc_canvas(), s, s - self.NL, sp, self.cw)
                ln.placeholder, ln.shown, ln.show_a = True, True, 1.0
                ln.fifths = 0
                ln.hdr_w = self._hdr_w(0)
                self._line_geom(ln)
                self.lines.append(ln)
                self._request_header(ln)
            self.ph = _Phrase(0)
            ln = self._line_of(self.ph, 0)
            self._show(ln)
            ln.show_a = 1.0

    # -- lines ----------------------------------------------------------------------------------------------------
    def _line_geom(self, ln: _Line) -> None:
        if self.scroll:
            return
        self.offx[ln.ci] = self.x_off
        self.offy[ln.ci] = self.y_top + ln.slot * self.pitch

    def _alloc_canvas(self) -> int:
        used = {ln.ci for ln in self.lines}
        free = [c for c in range(self.NC) if c not in used]
        if not free:
            # degrade: a line not shown yet of an old phrase, else the oldest line that isn't the current phrase's
            cands = [ln for ln in self.lines if not ln.shown and ln.phrase is not self.ph]
            if not cands:
                cands = sorted((ln for ln in self.lines if ln.phrase is not self.ph), key=lambda ln: ln.g)
            if not cands:
                cands = sorted(self.lines, key=lambda ln: ln.g)
            victim = cands[0]
            self._release(victim)
            free = [victim.ci]
        ci = free[0]
        o = self.coff[ci]
        self.canvas[o:o + self.ch * self.cwid[ci]].fill(0)
        return ci

    def _release(self, ln: _Line) -> None:
        if ln in self.lines:
            self.lines.remove(ln)
        ph = ln.phrase
        if ph is not None and ph.lines.get(ln.m) is ln:
            del ph.lines[ln.m]
        N = self.N
        for i in np.flatnonzero(N.used & (N.lid == ln.ci)):
            if N.line[i] is ln:
                N.free(int(i))
        for gi in np.flatnonzero(self.g_used & (self.g_lid == ln.ci)):
            if self.g_line[gi] is ln:
                self._gate_free(int(gi))

    def _row_line(self, i: int):
        return self.N.line[i]

    def _line_of(self, ph: _Phrase, m: int, create: bool = True):
        ln = ph.lines.get(m)
        if ln is not None or not create:
            return ln
        g = ph.g0 + m
        ci = self._alloc_canvas()
        ln = _Line(ci, g % self.NL, g, self.sp, self.cw)
        ln.phrase, ln.m = ph, m
        ln.ts_w = TS_SP * self.sp if m == 0 else 0.0
        ln.fifths = self._key_now()
        ln.hdr_w = self._hdr_w(ln.fifths)
        ln.front = ln.music_x0 - 0.5 * self.FE
        self._line_geom(ln)
        self.lines.append(ln)
        ph.lines[m] = ln
        self.gnext = max(self.gnext, g + 1)
        self._request_header(ln)
        return ln

    def _show(self, ln: _Line) -> None:
        if ln.shown:
            return
        ln.shown = True
        ln.t_shown = self.t_sound
        for o in self.lines:
            if o is not ln and o.slot == ln.slot and o.shown and o.erase_t is None and o.g < ln.g:
                o.erase_t = self.t_sound
        if not ln.sent and not ln.bars:
            f = self._key_now()
            if f != ln.fifths:                              # the key moved since it was planned
                ln.fifths = f
                self._request_header(ln)

    def _hdr_est(self, fifths: int) -> float:
        return (MARGIN_SP + HDR_SP - MARGIN_SP + (HDR_ACC_SP * abs(fifths) + 0.4 if fifths else 0.0)) * self.sp

    def _hdr_w(self, fifths: int) -> float:
        w = self.hdr_cache.get(fifths)
        return w if w is not None else self._hdr_est(fifths)

    def _request_header(self, ln: _Line) -> None:
        ln.hreq += 1
        ln.hdr_t = self.t_sound
        if self.engraver is None:
            return
        from stream_service.score import mei
        self.engraver.submit(f"h:{ln.uid}:{ln.hreq}", mei.header_doc(ln.fifths, meter=False),
                             margin_left=int(MARGIN_SP * self.sp))

    def _key_now(self) -> int:
        try:
            return int(self.builder.key()[2])
        except Exception:
            return 0

    # -- the music ---------------------------------------------------------------------------------------------
    def _listen(self):
        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        if f is None:
            return None
        t = float(f.t_ms) / 1000.0
        self.t_sound = t
        b = self.builder
        bars, rev = self.room.tick(self, f, t)
        if not self.joined:
            self.joined = self._join(bars)
            if self.joined:
                self.join_t = self.t_sound
                last = max((j for j, bar in enumerate(bars) if bar.marker), default=-1)
                bars = bars[last + 1:]                      # (bars before a rewrite in the hand-over: superseded)
        self._metre_check()
        self._births()
        self._metre_check()
        if rev != self.seen_rev:
            self.seen_rev = rev                             # (a new estimate: _epoch's signature changes)
        for bar in bars:
            if bar.rewrite:
                self._rewrite()
                continue
            if bar.marker:
                self._phrase_end()
                continue
            ph = self.ph
            if ph.lift_t is not None:
                if len(ph.queued) < 64:
                    ph.queued.append(bar)
                continue
            self.join_rows = self.join_t is not None and self.t_sound - self.join_t < JOIN_ROWS_S
            self._commit(ph, bar)
            self.join_rows = False
        if self.join_queue:
            self._join_step()
        self._sync_held()
        self._epoch(b)
        return f

    def _join(self, bars) -> bool:
        """The room's first music for this scene (it may start mid-phrase: cued while the piano plays, or switched
        from another layout; the room hands it the phrase's bars and unwritten notes so far).  Only the recent part
        is kept (what the page or the tape shows) and starts the phrase: its written notes are drawn at once as
        glyphs (_commit_keys) while their ink is engraved, and the empty tape is placed with its playhead at the
        newest notes (_scroll_step).  False: no music yet (it joins when some comes)."""
        cur = []
        for bar in bars:
            if bar.marker:
                cur = []
                continue
            cur.append(bar)
        notes = self.builder.notes
        times = [bar.t_start for bar in cur] + [n.t_on for n in notes]
        if not times:
            return False
        ph = self.ph
        if ph.t0 is not None:
            return True
        keep = JOIN_SCROLL_S if self.scroll else min(JOIN_PAGE_S, (self.NL - 1) * self.line_s)
        cut = self.t_sound - keep
        earliest = min(times)
        if earliest < cut:
            ph.cut_t = cut
            ph.k_base = next((bar.pos for bar in cur if bar.t_end > cut + 0.05), None)
            if ph.k_base is None and cur:
                ph.k_base = cur[-1].pos + 1
            if ph.k_base is None:
                pos = self.builder.position(cut)
                ph.k_base = int(math.floor(pos)) if pos is not None else None
        ph.t0 = max(earliest, cut)
        self._phrase_started(ph)
        if self.dbg is not None:
            self.dbg.append(("join", ph.id, self.t_sound, len(cur), ph.cut_t, ph.k_base))
        return True

    def _metre_check(self) -> None:
        ph = self.ph
        if (ph.metre is None and ph.lift_t is None and ph.t0 is not None
                and self.builder.position(self.t_sound) is not None):
            self._metre(ph, self.builder.beats, self.builder.unit)

    def _sync_held(self) -> None:
        """Releases into the rows.  A note can leave the room's view already released and written (its release and
        its bar in one tick of the room): it's released then."""
        N = self.N
        live = getattr(self.builder, "_by_id", None)
        for i in list(self.held):
            n = N.note[i]
            if not N.used[i] or n is None:
                self.held.discard(i)
                continue
            if n.t_off is not None:
                N.t_off[i] = n.t_off
                self.held.discard(i)
            elif live is not None and live.get(n.id) is not n:
                N.t_off[i] = min(N.t_off[i], self.t_sound)
                self.held.discard(i)

    def _beat_period(self) -> float:
        b = self.builder
        if b.grid is not None and len(b.grid.t) >= 3:
            return max(0.2, (b.grid.t[-1] - b.grid.t[-3]) / 2.0)
        if b.clock.P:
            return b.clock.P
        return self.P_free

    def _births(self) -> None:
        notes = self.builder.notes
        if not notes or notes[-1].id <= self.last_nid:
            return
        i0 = len(notes)
        while i0 > 0 and notes[i0 - 1].id > self.last_nid:
            i0 -= 1
        ph = self.ph
        N = self.N
        now = self.t_sound
        new = []
        last = notes[-1].id
        for n in notes[i0:]:
            if n.id in N.by_id or (ph.cut_t is not None and n.t_on < ph.cut_t - 0.05):
                continue
            if len(new) >= BIRTH_PER_FRAME:
                last = n.id - 1                             # (the rest next frame)
                break
            if ph.t0 is None:
                ph.t0 = n.t_on
                self._phrase_started(ph)
            i = N.alloc(n.id)
            if i is None:
                i = self._evict()
                if i is None:
                    continue
                i = N.alloc(n.id)
            N.note[i], N.ph[i] = n, ph
            N.pitch[i], N.t_on[i], N.t_born[i] = n.pitch, n.t_on, now
            ph.t_last = max(getattr(ph, "t_last", -1e18), float(n.t_on))
            if n.t_off is not None:
                N.t_off[i] = n.t_off
            else:
                self.held.add(i)
            ph.rows.add(i)
            if ph.newest is None or not N.used[ph.newest] or N.t_on[ph.newest] <= n.t_on:
                ph.newest = i
            N.key[i] = self._estimate(ph, i)
            N.tq[i] = self._tq(ph, N.key[i], i)
            new.append(i)
            if self.dbg is not None:
                self.dbg.append(("birth", int(n.id), now, float(n.t_on), time.perf_counter()))
        self.last_nid = last
        if new:
            self._relayout(ph, fresh=set(new), only=set(new))

    def _evict(self):
        """No free row: the oldest inked row goes (its ink stays), else the oldest puffing, handing over, ... one."""
        N = self.N
        for st in (3, 4, 2, 1, 0):                     # (0: no engraver, the glyphs are the score: the oldest goes)
            c = np.flatnonzero(N.used & (N.state == st))
            if len(c):
                i = int(c[np.argmin(N.t_on[c])])
                N.free(i)
                return i
        return None

    def _staff_of(self, i: int) -> int:
        n = self.N.note[i]
        if n is None:                                       # (written before the scene joined)
            k = self.N.key[i]
            return k[2] if k is not None else (0 if self.N.pitch[i] >= 60 else 1)
        try:
            return int(self.builder._staff(n))
        except Exception:
            return 0 if n.pitch >= 60 else 1

    def _estimate(self, ph: _Phrase, i: int):
        """(kind, 16th, staff): the note's place on the best beat grid the builder has now."""
        N = self.N
        t = N.t_on[i]
        st = self._staff_of(i)
        b = self.builder
        w = b.where(t) if ph is self.ph else None
        if ph is not self.ph:
            return N.key[i]
        if w is None:
            return ("free", int(round(1000.0 * (t - (ph.t0 if ph.t0 is not None else t)))), st)
        k, frac, prov = w
        if prov:
            return ("prov", k * 16 + int(round(frac * 16)), st)
        if ph.k_base is None or k < ph.k_base:          # (a rewrite carried on) its bars' numbering not known yet
            return ("free", int(round(1000.0 * (t - (ph.t0 if ph.t0 is not None else t)))), st)
        k -= ph.k_base
        bu = b.beats * b.unit
        return ("bars", k * bu + int(round(frac * bu)), st)

    def _tq(self, ph: _Phrase, key, i: int) -> float:
        """The time a key stands for (its 16th on the beat clock), fixed when the key is taken: a note on an open
        line doesn't drift as the clock re-anchors, it moves only when its 16th does."""
        t = float(self.N.t_on[i])
        if key is not None and key[0] == "prov":
            c = self.builder.clock
            if c.running:
                return float(c.time(key[1] / 4.0))
        return t

    def _epoch(self, b) -> None:
        """A new estimate (an analysis reply, the clock re-anchored, the grid grew): unwritten notes re-placed, a
        shown note moving only when two estimates in a row agree on its new place."""
        now = self.t_sound
        g = b.grid
        sig = (self.seen_rev, (b.clock.P, b.clock.t0, b.clock.b0) if g is None else None,
               (len(g.t), g.t[-1], g.idx0) if g is not None else None, b.beats, b.unit)
        if (sig == self.epoch_sig and not self.epoch_force and not self.relayout_due and not self.dirty_rows
                and not self.dirty_lines):
            return
        if now - self.epoch_t < EPOCH_MIN_S and not self.epoch_force and not self.epoch_carry:
            return
        self.epoch_carry = False
        new_est = sig != self.epoch_sig or self.epoch_force
        full = self.relayout_due or self.epoch_force
        self.epoch_sig = sig
        self.epoch_t = now
        self.epoch_force = False
        self.relayout_due = False
        ph = self.ph
        N = self.N
        changed = self.dirty_rows
        self.dirty_rows = set()
        lines, self.dirty_lines = self.dirty_lines, set()
        if new_est and ph.lift_t is None:
            for i in list(ph.rows):
                if not N.used[i] or N.state[i] != 0 or N.fixed[i]:
                    continue
                new = self._estimate(ph, i)
                cur = N.key[i]
                if new == cur:
                    N.cand[i] = None
                elif cur is None or new[0] != cur[0] or new == N.cand[i]:
                    if cur is not None and new[0] != cur[0]:
                        full = True                          # (a better kind of grid: everything re-placed)
                    N.key[i], N.cand[i] = new, None          # a better kind of grid took over, or agreed twice
                    N.tq[i] = self._tq(ph, new, i)
                    changed.add(i)
                else:
                    N.cand[i] = new
        if full:                                            # (every unwritten note: spread newest first below)
            for q in [ph] + self.old:
                changed.update(q.rows)
        elif lines:
            for q in [ph] + self.old:
                changed.update(i for i in q.rows if N.used[i] and N.line[i] in lines)
        todo = [i for i in changed if N.used[i] and N.state[i] == 0 and N.ph[i] is not None]
        if len(todo) > RELAYOUT_PER_FRAME:                  # a frame's share, the newest (by the playhead) first,
            todo.sort(key=N.t_on.__getitem__, reverse=True)  # counted in whole bars (mates are laid out together)
            grp = N.grp
            cnt: dict = {}
            for q in {id(N.ph[i]): N.ph[i] for i in todo}.values():
                for i in q.rows:
                    g = grp[i]
                    if g is not None:
                        cnt[g] = cnt.get(g, 0) + 1
            take, rest, groups, cost = [], [], set(), 0
            for i in todo:
                g = grp[i]
                if g is not None and g in groups:
                    take.append(i)
                elif cost < RELAYOUT_PER_FRAME:
                    take.append(i)
                    cost += cnt.get(g, 1) if g is not None else 1
                    if g is not None:
                        groups.add(g)
                else:
                    rest.append(i)
            if rest:
                self.dirty_rows.update(rest)
                self.epoch_carry = True
            todo = take
        by_ph: dict = {}
        for i in todo:
            by_ph.setdefault(id(N.ph[i]), (N.ph[i], set()))[1].add(i)
        for q, rows in by_ph.values():
            self._relayout(q, only=rows)

    def _pitch_info(self, pitch: int, fifths: int, staff: int):
        """(staff step offset from the staff's top line in sp*0.5 units, letter, alteration, octave)."""
        key = (pitch, fifths, staff)
        r = self.spells.get(key)
        if r is None:
            from stream_service.score.mei import ottava, signature, spell
            shift = ottava([pitch], staff)
            letter, alt, octave = spell(pitch - 12 * shift, fifths, signature(fifths))
            d = "cdefgab".index(letter) + 7 * octave
            off = 4.0 - (d - (30 if staff == 0 else 18)) * 0.5
            r = (off, letter, alt, octave)
            if len(self.spells) > 4096:
                self.spells.clear()
            self.spells[key] = r
        return r

    def _spelling(self) -> int:
        b = self.builder
        c = self.spell_c
        now = self.t_sound
        if c is None or (c[0] != b.ver and now - c[2] >= 0.5):
            f = b.key_fifths
            if f is None:
                f = self._key_now()
            c = self.spell_c = (b.ver, f, now)
        return c[1]

    def _bar_x(self, ph: _Phrase, k: int, frac: float):
        g = self._tcache.get((ph.id, k))
        if g is None:
            self._ensure_plan(ph, k)
            if not ph.plan.lines:
                return None
            j = ph.plan.line_of(k)
            PL = ph.plan.line(j)
            ln = self._line_of(ph, j)
            pl = ln.bars.get(k)
            if pl is not None:
                x0, w = pl[0], pl[1]
            else:
                x0, w = ph.plan.slot_x0(j, k), PL.bw
            g = self._tcache[(ph.id, k)] = (ln, x0, w)
        ln, x0, w = g
        return ln, x0 + self.A + frac * max(self.sp, w - self.A - self.C), x0, w

    def _target(self, ph: _Phrase, key, i: int):
        """(line, x, y, staff top, spelling, bar id, bar geometry (x0, w, 16th in bar, 16ths) or None) in the line's
        canvas, or None."""
        typ, q, st = key
        if self.scroll:
            return self._s_target(ph, key, i)
        if ph.metre is not None:
            bu = ph.L * ph.unit
            q = max(0, q) if typ == "bars" else self._q_bars(ph, i)
            k = q // bu
            r = self._bar_x(ph, k, (q - k * bu) / bu)
            if r is None:
                return None
            ln, x, x0, w = r
            barid = k
            geo = (x0, w, q - k * bu, bu)
        else:
            if ph.v is None:
                self._open_setup(ph)
            t = float(self.N.tq[i])
            ln, t_s = self._open_line_at(ph, t)
            x = ln.music_x0 + self.A + max(0.0, t - t_s) * ph.v
            barid = q // 16 if typ == "prov" else q // 2000
            geo = None
        off, letter, alt, octave = self._pitch_info(int(self.N.pitch[i]),
                                                    self._spell_pass if self._spell_pass is not None
                                                    else self._spelling(), st)
        top = ln.t1 if st == 0 else ln.t2
        return ln, x, top + off * self.sp, top, (letter, alt, octave), barid, geo

    def _s_target(self, ph: _Phrase, key, i: int):
        typ, q, st = key
        ln = self.tape_line
        if getattr(ph, "U0", None) is None:
            self._s_start(ph)
        if ph.metre is not None and ph.ruler is not None:
            bu = ph.L * ph.unit
            q = max(0, q) if typ == "bars" else self._q_bars(ph, i)
            k = q // bu
            x0, w = self._s_slot(ph, k)
            x = x0 + self.A + (q - k * bu) / bu * max(self.sp, w - self.A - self.C)
            barid, geo = k, (x0, w, q - k * bu, bu)
        else:
            x = ph.Um0 + self.A + max(0.0, float(self.N.tq[i]) - ph.t0) * ph.v
            barid, geo = (q // 16 if typ == "prov" else q // 2000), None
        off, letter, alt, octave = self._pitch_info(int(self.N.pitch[i]),
                                                    self._spell_pass if self._spell_pass is not None
                                                    else self._spelling(), st)
        top = ln.t1 if st == 0 else ln.t2
        return ln, x, top + off * self.sp, top, (letter, alt, octave), barid, geo

    def _q_bars(self, ph: _Phrase, i: int) -> int:
        """A note still placed by time (its estimate not yet on the bars) under a phrase with bars: its 16th by the
        builder's beats (the next estimate takes over)."""
        try:
            q = int(round(self.builder.timeline.beat(float(self.N.tq[i])) * ph.unit))
        except Exception:
            return 0
        return max(0, q - (ph.k_base or 0) * ph.L * ph.unit)

    def _open_line_at(self, ph: _Phrase, t: float):
        """(line, its start time): before the metre a phrase's lines are laid out in time, ph.v px a second, each
        holding what fits (about LINE_S); the metre then fits whole bars to the same spans."""
        t_s = ph.t0 if ph.t0 is not None else t
        m = 0
        while True:
            ln = self._line_of(ph, m)
            T = max(0.5, (self.cw - PAD_R_SP * self.sp - ln.music_x0 - self.A) / ph.v)
            if t < t_s + T or m >= 24:
                return ln, t_s
            t_s += T
            m += 1

    def _relayout(self, ph: _Phrase, fresh=(), only=None) -> None:
        """The phrase's unwritten notes to their places (accidentals by the bar's rule, chords sharing a stem):
        all of them, or ``only`` these and their bar mates (the groups their places depend on)."""
        N = self.N
        sp = self.sp
        now = self.t_sound
        if not ph.rows:
            return
        ra = np.fromiter(ph.rows, np.intp, len(ph.rows))
        ra = ra[N.used[ra] & (N.state[ra] == 0)]
        key = N.key
        rows_all = [i for i in ra.tolist() if key[i] is not None]
        if not rows_all:
            return
        self._tcache = {}
        self._spell_pass = self._spelling()
        info = {}
        keys: set = set()
        if only is None:
            rows = rows_all
        else:
            keys = {N.grp[i] for i in only if N.grp[i] is not None}
            rows = [i for i in rows_all if i in only or N.grp[i] in keys]
        for i in rows:
            r = self._target(ph, key[i], i)
            if r is not None:
                info[i] = r
        if only is not None:                                # rows that moved to another bar: its mates too
            new = {(r[0].uid, r[5]) for r in info.values()} - keys
            if new:
                for i in rows_all:
                    if i not in info and N.grp[i] in new:
                        r = self._target(ph, N.key[i], i)
                        if r is not None:
                            info[i] = r
        self._spell_pass = None
        if not info:
            return
        chords: dict = {}
        accs: dict = {}
        for i, r in info.items():
            ln = r[0]
            N.grp[i] = (ln.uid, r[5])
            chords.setdefault((ln.uid, N.key[i][2], N.key[i][1]), []).append(i)
            accs.setdefault((ln.uid, N.key[i][2], r[5]), []).append(i)
        ra = np.array(rows_all, np.intp)                    # a note is written to its staff's next onset
        stf = np.array([key[i][2] for i in rows_all], np.int64)
        for v in set(stf.tolist()):                      # (not np.unique: its first call imports numpy.ma, 50 ms)
            g = ra[stf == v]
            g = g[np.argsort(N.t_on[g], kind="stable")]
            ons = N.t_on[g]
            j = np.searchsorted(ons, ons + 0.045, side="right")
            N.t_next[g] = np.where(j < len(g), ons[np.minimum(j, len(g) - 1)], np.inf)
        from stream_service.score.mei import signature
        acc = {}
        for grp in accs.values():
            grp.sort(key=lambda i: N.t_on[i])
            sig = signature(info[grp[0]][0].fifths)
            shown: dict = {}
            for i in grp:
                letter, alt, octave = info[i][4]
                cur = shown.get((letter, octave), sig[letter])
                shown[(letter, octave)] = alt
                acc[i] = GL.ACC_BY_ALT.get(alt, GL.NATURAL) if alt != cur else -1
        # within a bar: the natural spacing (accidentals and seconds take room), as the engraver will lay it out
        bars: dict = {}
        for i, r in info.items():
            if r[6] is not None:
                bars.setdefault((r[0].uid, r[5]), []).append(i)
        nat_x: dict = {}
        for grp in bars.values():
            x0, w, _s, units = info[grp[0]][6]
            ons: dict = {}
            for i in grp:
                s_i = info[i][6][2]
                o = ons.setdefault(s_i, [0, 0, []])
                if acc.get(i, -1) >= 0:
                    o[0] = 1
                o[2].append((N.key[i][2], int(N.pitch[i])))
            for o in ons.values():
                for stf in (0, 1):
                    ps = sorted(p for st_, p in o[2] if st_ == stf)
                    if any(b - a <= 2 for a, b in zip(ps, ps[1:])):
                        o[1] = 1
            order = sorted(ons)
            P, extra = {}, 0.0
            sec_prev = 0
            for s_i in order:
                extra += ACC_X * ons[s_i][0] + SEC_X * sec_prev
                P[s_i] = K16 * s_i + extra
                sec_prev = ons[s_i][1]
            tot = K16 * units + extra + SEC_X * sec_prev
            span = max(sp, w - self.A - self.C)
            for i in grp:
                nat_x[i] = x0 + self.A + P[info[i][6][2]] / tot * span
        dxs: dict = {}
        stems: dict = {}
        for grp in chords.values():
            ys = [info[i][2] for i in grp]
            top = info[grp[0]][3]
            mid = top + 2.0 * sp
            lo, hi = min(ys), max(ys)
            up = (hi - mid) > (mid - lo) + 1e-3
            stemmed = [i for i in grp if N.gb[i] != GL.WHOLE]
            if len(grp) > 1:                             # seconds: one head the other side of the stem
                order = sorted(grp, key=lambda i: -info[i][2])           # lowest first
                prev = None
                for i in order:
                    if prev is not None and abs(info[prev][2] - info[i][2]) < 0.6 * sp and dxs.get(prev, 0.0) == 0.0:
                        dxs[i if up else prev] = (1.2 if up else -1.2) * sp
                        prev = None
                        continue
                    prev = i
            if not stemmed:
                continue
            ys2 = [info[i][2] for i in stemmed]
            lo2, hi2 = min(ys2), max(ys2)
            if up:
                base = max(stemmed, key=lambda i: info[i][2])
                tip = min(lo2 - STEM_SP * sp, mid)
                rel = (1.206, -0.16, (tip - info[base][2]) / sp)
            else:
                base = min(stemmed, key=lambda i: info[i][2])
                tip = max(hi2 + STEM_SP * sp, mid)
                rel = (0.05, 0.16, (tip - info[base][2]) / sp)
            stems[base] = rel
        for i, (ln, x, y, top, _sp, _b, _g) in info.items():
            x = nat_x.get(i, x) + dxs.get(i, 0.0)
            N.st_top[i] = top
            g_acc = acc.get(i, -1)
            adx = -(0.247 + 0.5 * self.adv[g_acc]) if g_acc >= 0 else 0.0
            if i in fresh:
                N.place(i, ln, x, y)
                N.set_acc(i, g_acc, adx, now, instant=True)
                s = stems.get(i)
                N.set_stem(i, s is not None, *(s or (0.0, 0.0, 0.0)), now, instant=True)
                self._show(ln)
                continue
            ci = int(N.lid[i])
            moved = N.line[i] is not ln or abs(N.tx[i] - x) > MOVE_MIN_SP * sp or abs(N.ty[i] - y) > 0.05
            if moved:
                cross = N.line[i] is not ln or abs(N.ty[i] - y) > 3.0 * sp
                d = math.hypot((N.tx[i] + self.offx[ci] - self.offx[ln.ci]) - x,
                               (N.ty[i] + self.offy[ci] - self.offy[ln.ci]) - y) / sp
                if cross:
                    N.hop(i, ln, x, y, STAFF_HOP_S, STAFF_ARC_SP * sp * self.flourish, now, self.offx, self.offy)
                else:
                    N.hop(i, ln, x, y, GLIDE_S, 0.0, now, self.offx, self.offy)
                N.moves[i] += 1
                if self.dbg is not None:
                    self.dbg.append(("move", int(N.nid[i]), now, d))
                self._show(ln)
            N.set_acc(i, g_acc, adx, now)
            s = stems.get(i)
            N.set_stem(i, s is not None, *(s or (0.0, 0.0, 0.0)), now)

    # -- phrases, metre, bars -------------------------------------------------------------------------------
    def _phrase_started(self, ph: _Phrase) -> None:
        if ph.v is None:
            self._open_setup(ph)
        if self.scroll:
            self._s_start(ph)
            return
        ln = self._line_of(ph, 0)
        self._show(ln)

    def _open_setup(self, ph: _Phrase) -> None:
        """The phrase's open lines (before its metre) in time: a line holds about LINE_S of music."""
        if self.scroll:
            ph.v = self._s_v()
            return
        ln0 = ph.lines.get(0)
        mx0 = ln0.music_x0 if ln0 is not None else self._hdr_w(self._key_now()) + TS_SP * self.sp
        ph.v = (self.cw - PAD_R_SP * self.sp - mx0 - self.A) / self.line_s

    def _metre(self, ph: _Phrase, beats: int, unit: int) -> None:
        """The metre is decided: whole bars fitted to the open lines' time spans (equal slots, notes keep about their
        x), every bar line drops in and the time signature flips in."""
        ph.metre = (beats, unit)
        ph.L, ph.unit = beats, unit
        ph.sig = (beats * 3, 8) if unit == 6 else (beats, 4)
        if ph.v is None:
            self._open_setup(ph)
        if ph.ts_t is None:
            ph.ts_t = self.t_sound
        if self.scroll:
            self._s_metre(ph)
            self.epoch_force = True
            return
        if ph.cut_t is not None:                            # joined mid-phrase, or carried on after a rewrite: its
            ph.plan = LinePlan()                            # bars are known, so its lines are planned by the tempo
            ph.open_m = set()                               # (_ensure_plan) rather than fitted to open lines
            self.epoch_force = True
            return
        b = self.builder
        tl = b.timeline
        bar_s = b.bar_seconds() or beats * self._beat_period()
        sp = self.sp
        nat_px = (ph.nat or NAT_SEED[self.spacing]) * (1.5 if unit == 6 else 1.0) * beats * sp
        ph.plan = LinePlan()
        ph.open_m = set()
        k0 = 0
        t_s = ph.t0 if ph.t0 is not None else self.t_sound
        for m in sorted(ph.lines):
            if ph.plan.lines and m != ph.plan.last + 1:
                break
            ln = ph.lines[m]
            avail = self.cw - PAD_R_SP * sp - ln.music_x0
            t_e = t_s + max(0.5, (avail - self.A) / ph.v)
            try:
                t_k = float(tl.time(k0 * beats))
            except Exception:
                t_k = t_s
            n = max(1, int(round((t_e - t_k) / max(0.2, bar_s))))
            # no note shown on this line or the next moves line: the line ends after its last note's bar and before
            # the next line's first (when it can)
            k_last, k_next = self._open_line_bars(ph, ln, beats, unit)
            if k_last is not None:
                n = max(n, k_last - k0 + 1)
            if k_next is not None and k_next > k0:
                n = min(n, k_next - k0)
            n = min(n, max(1, int(avail // (FIT_MIN * nat_px))), N_MAX)
            n = max(1, n)
            ph.plan.lines.append(PlanLine(k0, n, avail / n, ln.music_x0))
            ph.open_m.add(m)
            self._bar_gates(ph, ln)
            k0 += n
            t_s = t_e
        self.epoch_force = True

    def _open_line_bars(self, ph: _Phrase, ln: _Line, beats: int, unit: int):
        """(the last bar any note now on open line ``ln`` falls in, the first bar of a note on the next line), by the
        builder's new beats."""
        N = self.N
        tl = self.builder.timeline
        nxt = ph.lines.get(ln.m + 1)
        k_last = k_next = None
        for i in ph.rows:
            if not N.used[i] or N.state[i] != 0:
                continue
            on = N.line[i]
            if on is not ln and on is not nxt:
                continue
            try:
                k = int(math.floor(round(tl.beat(float(N.t_on[i])) * unit) / (beats * unit)))
            except Exception:
                continue
            if on is ln:
                k_last = k if k_last is None else max(k_last, k)
            else:
                k_next = k if k_next is None else min(k_next, k)
        return k_last, k_next

    def _plan_line(self, ph: _Phrase) -> None:
        """The phrase's next line after its open ones: as many bars as about LINE_S of music, never squeezed past
        FIT_MIN of their natural width, more if they fit at WANT."""
        sp = self.sp
        m = ph.plan.last + 1 if ph.plan.lines else 0
        last = ph.plan.lines[-1] if ph.plan.lines else None
        k0 = last.k0 + last.n if last is not None else 0
        ln = self._line_of(ph, m)
        avail = self.cw - PAD_R_SP * sp - ln.music_x0
        nat = (ph.nat or NAT_SEED[self.spacing]) * (1.5 if ph.unit == 6 else 1.0)
        nat_px = nat * ph.L * sp
        bar_s = ph.bar_s or self.builder.bar_seconds() or 2.0
        n_time = int(math.ceil(self.line_s / max(0.2, bar_s)))
        n_nat = int(avail // (WANT * nat_px))
        n_fit = int(avail // (FIT_MIN * nat_px))
        n = max(n_nat, min(n_time, n_fit))
        n = min(N_MAX, max(N_MIN, n))
        ph.plan.lines.append(PlanLine(k0, n, avail / n, ln.music_x0))
        if len(ph.plan.lines) > 8:
            ph.plan.lines.pop(0)
            ph.plan.base += 1
        self._bar_gates(ph, ln)

    def _ensure_plan(self, ph: _Phrase, k: int) -> None:
        if ph.plan.lines:                                   # never more than a few lines ahead (a stray estimate)
            last = ph.plan.lines[-1]
            k = min(k, last.k0 + last.n + 3 * N_MAX)
        guard = 0
        while ph.metre is not None and not self.scroll and not ph.plan.covers(k) and guard < 12:
            self._plan_line(ph)
            guard += 1

    def _commit(self, ph: _Phrase, bar) -> None:
        if ph.cut_t is not None:
            if bar.t_end <= ph.cut_t + 0.05:
                return                                      # written already, in the metre before the rewrite
            if ph.k_base is None:
                ph.k_base = bar.pos
                self.epoch_force = True
        if ph.metre is None:
            if ph.t0 is None:
                ph.t0 = bar.t_start
                self._phrase_started(ph)
            self._metre(ph, bar.beats, bar.unit)
        k = bar.pos - (ph.k_base or 0)
        ph.bar_t[k] = (float(bar.t_start), float(bar.t_end))
        ph.committed = max(ph.committed, k + 1)
        if self.dbg is not None:
            self.dbg.append(("commit", ph.id, self.t_sound, k, float(bar.t_start), float(bar.t_end), bar.beats,
                             bar.unit))
        d = min(6.0, max(0.3, bar.t_end - bar.t_start))
        ph.bar_s = d if ph.bar_s is None else ph.bar_s + 0.3 * (d - ph.bar_s)
        self.P_free = min(1.2, max(0.25, ph.bar_s / max(1, ph.L)))
        self._ensure_plan(ph, k)
        if self.scroll and ph.ruler is not None:
            f = self._key_now()
            if f != ph.fifths_tape:
                if ph.next_place == 0 and not ph.sub and not ph.items:
                    ph.fifths_tape = f                       # nothing engraved yet: the phrase starts in it
                    self._s_header(f)
                elif k > 0:
                    self._s_key_change(ph, k, f)
            ph.bar_fifths[k] = ph.fifths_tape
        ph.wait.append((k, bar))
        self._commit_keys(ph, bar, k)

    def _commit_keys(self, ph: _Phrase, bar, k: int) -> None:
        """A committed bar says exactly where each of its notes is (its 16th, its staff): the notes go there now,
        a second or more before the ink, and estimates no longer move them."""
        N = self.N
        bu = bar.beats * bar.unit
        moved = False
        span = (bar.t_end - bar.t_start) / bu
        fresh = set()
        for st, events in enumerate(bar.staves):
            for ev in events:
                for nid, tin, tout, pitch in zip(ev.ids, ev.tie_in, ev.tie_out, ev.pitches):
                    i = N.by_id.get(int(nid))
                    if i is None and self.join_rows and not tin:
                        self.join_queue.append((bar.t_start + ev.start * span, ph, int(nid), int(pitch), st,
                                                k * bu + ev.start, bar.t_start + (ev.start + ev.dur) * span))
                        continue
                    if i is not None and not tout and np.isinf(N.t_off[i]):
                        N.t_off[i] = bar.t_start + (ev.start + ev.dur) * span        # written to end here
                    if tin:
                        continue
                    if i is None or N.ph[i] is not ph or N.state[i] != 0:
                        continue
                    key = ("bars", k * bu + ev.start, st)
                    N.fixed[i] = True
                    N.cand[i] = None
                    if N.key[i] != key:
                        N.key[i] = key
                        self.dirty_rows.add(i)
        del fresh, moved

    def _join_step(self) -> None:
        """Written notes handed over at a join, drawn newest first, JOIN_PER_FRAME a frame (a join's hundreds of
        notes would otherwise cost one frame tens of ms)."""
        q = self.join_queue
        q.sort(key=lambda e: e[0])
        take, self.join_queue = q[-JOIN_PER_FRAME:], q[:-JOIN_PER_FRAME]
        fresh: dict = {}
        for t_on, ph, nid, pitch, st, qq, t_off in take:
            if nid in self.N.by_id or ph is not self.ph:
                continue
            i = self._written_row(ph, nid, pitch, st, qq, t_on, t_off)
            if i is not None:
                fresh.setdefault(id(ph), (ph, set()))[1].add(i)
        for ph, rows in fresh.values():
            self._relayout(ph, fresh=rows, only=rows)

    def _written_row(self, ph: _Phrase, nid: int, pitch: int, staff: int, q: int, t_on: float, t_off: float):
        """A note written before this scene joined: a settled glyph at its place (no pop, no glow) until its ink."""
        N = self.N
        ph.t_last = max(getattr(ph, "t_last", -1e18), float(t_on))
        i = N.alloc(nid)
        if i is None:
            i = self._evict()
            if i is None:
                return None
            i = N.alloc(nid)
        N.ph[i] = ph
        N.pitch[i], N.t_on[i], N.t_off[i] = pitch, t_on, t_off
        N.t_born[i] = self.t_sound - 10.0
        N.key[i] = ("bars", q, staff)
        N.tq[i] = t_on
        N.fixed[i] = True
        N.readable[i] = True
        ph.rows.add(i)
        return i

    def _pump(self, ph: _Phrase) -> None:
        from stream_service.score import mei
        while ph.wait:
            k, bar = ph.wait[0]
            if self.scroll:
                u0, fit = self._s_slot(ph, k)
                if (self.frontier is not None
                        and u0 + fit < self.S_win - 4.0 * self.sp):
                    ph.items[k] = SKIP                      # already scrolled away: never engraved
                    ph.wait.pop(0)
                    continue
                ln = self.tape_line
                fifths = ph.bar_fifths.get(k, ph.fifths_tape)
            else:
                if not ph.plan.covers(k) or ph.plan.line(ph.plan.line_of(k)) is None:
                    ph.items[k] = SKIP
                    ph.wait.pop(0)
                    continue
                j = ph.plan.line_of(k)
                ln = self._line_of(ph, j)
                fit = ph.plan.line(j).bw
                if not ln.sent and not ln.bars and ln.hdr_t is not None and self.t_sound - ln.hdr_t > 0.05:
                    f = self._key_now()                       # the key heard since the line was planned
                    if f != ln.fifths:
                        ln.fifths = f
                        ln.header = False
                        self._request_header(ln)
                        w = self.hdr_cache.get(f)
                        if w is not None and abs(w - ln.hdr_w) > 0.5:
                            ln.hdr_w = w
                            self._line_moved(ln)
                        fit = ph.plan.line(j).bw
                        if f not in self.hdr_cache:
                            break
                fifths = ln.fifths
                if (not ln.header and ln.fifths not in self.hdr_cache and ln.hdr_t is not None
                        and self.t_sound - ln.hdr_t < HDR_WAIT_S and self.engraver is not None):
                    break
            if self.engraver is None:
                ph.items[k] = EMPTY
                ph.wait.pop(0)
                continue
            ln.sent += 1
            key = f"b:{ph.id}:{ph.epoch}:{bar.index}:{k}"
            doc = mei.bar_doc(bar, fifths).replace('place="below">', 'place="above">')   # dynamics over the
            self.engraver.submit(key, doc, fit_px=round(fit - 0.3 * self.sp, 1), squeeze=SQUEEZE,  # treble: between staves
                                 spacing=self.spacing, notes=True)                       # they'd be cut at the split
            ph.sub[key] = k
            ph.t_sub[k] = self.t_sound
            ph.wait.pop(0)
            if time.perf_counter() > self.pump_until:
                break

    def _ink_loop(self) -> None:
        """(a thread) The engraver's results made ready to land: the ink copied into a free pinned slot, a bar's
        staff split found, its heads read; numpy and Python only (no CUDA calls off the render thread)."""
        eng = self.engraver
        while not self.ink_stop and eng is not None:
            try:
                key, meta, ink = eng.out.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                slot = None
                if key:
                    kind = key.split(":", 1)[0]
                    ok = bool(meta.get("ok")) and bool(meta.get("measures"))
                    w, h = (int(meta.get("w", 0)), int(meta.get("h", 0))) if ok else (0, 0)
                    if ok and (w * h > self.ch * max(self.cwid) or len(ink) < w * h or w * h > self.slot_bytes
                               or w * h <= 0):
                        ok = False
                    if ok:
                        while not self.ink_stop:
                            try:
                                j = self.slot_free.get(timeout=0.2)
                                break
                            except queue.Empty:
                                continue
                        else:
                            return
                        arr = np.frombuffer(ink, np.uint8, w * h)
                        self.slots[j][:w * h] = arr
                        ms = meta["measures"][0]
                        if kind == "b" and len(ms.get("staves") or ()) > 1:
                            self._split_row(ms, arr.reshape(h, w))
                        if kind in ("b", "i") and meta.get("notes"):
                            meta["_heads"] = self._heads(meta)
                        slot = (j, w, h)
                self.ink_ready.put((key, meta, slot))
            except Exception as e:                          # (never the thread: that result is dropped)
                log.warning("score_v2 ink thread: %s", e)

    def _heads(self, meta: dict) -> list:
        """The engraver's head centres -> glyph origins, left to right."""
        cen = self.gcent
        sp = self.sp
        notes = []
        for e in meta.get("notes") or []:
            g = GL.GI.get(e.get("glyph"), GL.BLACK)
            notes.append((int(e["id"]), g, e["x"] - float(cen[g, 0]) * sp, e["y"] - float(cen[g, 1]) * sp,
                          e.get("stem"), e.get("dir"), e.get("accid")))
        notes.sort(key=lambda r: r[2])
        return notes

    def _take_renders(self) -> None:
        if self.engraver is None:
            return
        if self.ink_thread is None:
            self.ink_thread = threading.Thread(target=self._ink_loop, daemon=True, name="score_v2-ink")
            self.ink_thread.start()
        if self.slot_fly:                                   # slots whose copies are done go back to the thread
            keep = []
            for j, ev in self.slot_fly:
                if ev.done:
                    self.slot_free.put(j)
                else:
                    keep.append((j, ev))
            self.slot_fly = keep
        results = []
        while len(results) < RESULTS_PER_FRAME:
            try:
                results.append(self.ink_ready.get_nowait())
            except queue.Empty:
                break
        if not results:
            return
        cp = self.ctx.cp
        main = cp.cuda.get_current_stream()
        touched = []
        for key, meta, slot in results:
            if not key:
                if slot is not None:
                    self.slot_free.put(slot[0])
                continue
            kind = key.split(":", 1)[0]
            render = None
            if slot is not None:
                j, w, h = slot
                g = cp.empty((h, w), cp.uint8)
                g.set(self.slots[j][:w * h].reshape(h, w), stream=self.side)
                ev = cp.cuda.Event(block=False, disable_timing=True)
                ev.record(self.side)
                main.wait_event(ev)                         # (the GPU orders it; nothing waits here)
                self.slot_fly.append((j, ev))
                render = (g, meta)
            if kind == "h":
                _h, uid, req = key.split(":")[:3]
                ln = next((x for x in self.lines if x.uid == int(uid) and x.hreq == int(req)), None)
                if ln is not None and render is not None:
                    self._header_landed(ln, render)
                continue
            if kind == "H":
                if render is not None:
                    self._s_header_landed(key, render)
                continue
            if kind == "i":
                ph = next((q for q in [self.ph] + self.old if key in q.ins_sub), None)
                if ph is not None and render is not None:
                    self._s_key_landed(ph, ph.ins_sub.pop(key), render)
                continue
            ph = next((q for q in [self.ph] + self.old if key in q.sub), None)
            if ph is None:
                continue
            k = ph.sub.pop(key)
            ph.items[k] = render if render is not None else EMPTY
            touched.append(ph)
        for ph in touched:
            self._drain(ph)

    def _drain(self, ph: _Phrase) -> None:
        dead = self.engraver is not None and not self.engraver.ok
        placed = False
        for _ in range(64):
            k = ph.next_place
            it = ph.items.pop(k, None)
            if it is None:
                t = ph.t_sub.get(k)
                if t is None or not (self.t_sound - t > PART_TIMEOUT_S or len(ph.items) > PARK_MAX or dead):
                    break
                it = EMPTY
                for key, kk in list(ph.sub.items()):
                    if kk == k:
                        del ph.sub[key]
            if it is not SKIP:
                if self._placed_n >= PLACE_PER_FRAME:
                    ph.items[k] = it                        # (next frame)
                    break
                self._placed_n += 1
                self._place_bar(ph, k, it)
                placed = True
            ph.next_place += 1
        if placed:
            self._orphans(ph)

    def _orphans(self, ph: _Phrase) -> None:
        """Unwritten notes whose bar (and the next) are placed without them: they puff."""
        N = self.N
        now = self.t_sound
        for i in list(ph.rows):
            if not N.used[i] or N.state[i] != 0 or N.key[i] is None:
                continue
            kind, q, _st = N.key[i]
            if ph.metre is None:
                continue
            k = q // (ph.L * ph.unit)
            if k <= ph.next_place - 2 or (ph.done and now - N.t_born[i] > 0.5):
                self._puff(i)

    def _puff(self, i: int) -> None:
        N = self.N
        if N.state[i] == 4:
            return
        N.state[i] = 4
        N.die_t0[i] = self.t_sound
        if self.dbg is not None:
            self.dbg.append(("puff", int(N.nid[i]), self.t_sound))

    def _split_row(self, m: dict, img: np.ndarray) -> None:
        """Where an engraved bar is cut into its treble and bass parts (each goes to its fixed staff place): the
        emptiest ink row between the staves (nearest the middle), so no beam, stem or mark is cut in two."""
        tb, bt = m["staves"][0][1], m["staves"][1][0]
        r0, r1 = int(math.ceil(tb + 0.6 * self.sp)), int(math.floor(bt - 0.6 * self.sp))
        if r1 - r0 < 2:
            return
        prof = img[r0:r1].sum(axis=1, dtype=np.int64)
        mid = 0.5 * (r1 - r0 - 1)
        lo = prof.min()
        cand = np.nonzero(prof <= lo)[0]
        m["split"] = float(r0 + cand[np.argmin(np.abs(cand - mid))]) + 0.5

    def _parts(self, m: dict, t1: float, h: int, gap: float):
        top = m["staves"][0][0]
        dy1 = int(round(t1 - top))
        if len(m["staves"]) < 2:
            split, dy2 = math.inf, dy1
        else:
            tb, bt = m["staves"][0][1], m["staves"][1][0]
            split, dy2 = m.get("split", (tb + bt) / 2.0), int(round(t1 + gap - bt))
        cut = h if split == math.inf else max(0, min(h, int(round(split))))
        out = []
        for a, b, dy in ((0, cut, dy1), (cut, h, dy2)):
            r0, r1 = max(0, a + dy), min(self.ch, b + dy)
            if r1 > r0:
                out.append((r0, r1, dy))
        return out, split, dy1, dy2

    def _composite(self, ln: _Line, img, m: dict, x0: float, x1: float, dst: float) -> tuple:
        """Columns [x0, x1) of an engraved part onto the line's canvas at x ``dst`` (treble top on t1, bass a fixed
        gap below); returns (split, dy1, dy2) for placing its heads."""
        h, w = img.shape
        c0, c1 = int(x0), min(int(math.ceil(x1)), w)
        parts, split, dy1, dy2 = self._parts(m, ln.t1, h, STAVE_GAP_SP * self.sp)
        ci = ln.ci
        CW = self.cwid[ci]
        cv = self.canvas[self.coff[ci]:self.coff[ci] + self.ch * CW].reshape(self.ch, CW)
        if self.scroll and ci == 0:
            from stream_service.score.motion import ring_slices
            u = int(round(dst))
            for d, o, n in ring_slices(u, c1 - c0, CW):
                cv[:, d:d + n] = 0                          # a lap ago's ink
                for r0, r1, dy in parts:
                    seg = cv[r0:r1, d:d + n]
                    self.ctx.cp.maximum(seg, img[r0 - dy:r1 - dy, c0 + o:c0 + o + n], out=seg)
            return split, dy1, dy2
        dx = int(round(dst))
        cols = min(c1 - c0, CW - dx)
        if cols > 0 and dx >= 0:
            for r0, r1, dy in parts:
                d = cv[r0:r1, dx:dx + cols]
                self.ctx.cp.maximum(d, img[r0 - dy:r1 - dy, c0:c0 + cols], out=d)
        return split, dy1, dy2

    def _header_landed(self, ln: _Line, render) -> None:
        img, meta = render
        m = meta["measures"][0]
        w = float(m["x1"] + 1)
        ci = ln.ci
        CW = self.cwid[ci]
        cv = self.canvas[self.coff[ci]:self.coff[ci] + self.ch * CW].reshape(self.ch, CW)
        if not self.scroll and not ln.bars:
            cv[:, :int(min(CW, max(w, ln.hdr_w)))] = 0
        self._composite(ln, img, m, 0, w, 0)
        ln.header = True
        ln.staff_x0 = float(m["x0"])
        self.hdr_cache[ln.fifths] = w
        if abs(w - ln.hdr_w) > 0.5 and not ln.sent and not ln.bars:
            ln.hdr_w = w
            self._line_moved(ln)
        else:
            ln.hdr_w = max(ln.hdr_w, w)

    def _line_moved(self, ln: _Line) -> None:
        """A line's music starts elsewhere (its header's real width, a key heard since it was planned): its plan,
        bar lines and notes follow (only while none of its bars has gone to the engraver)."""
        ph = ln.phrase
        if ph is None:
            return
        if not ln.bars:
            ln.front = ln.music_x0 - 0.5 * self.FE
        if self.scroll:
            self.relayout_due = True
            return
        if ph.metre is None:
            if ln.m == 0 and ph.v is not None:
                self._open_setup(ph)
        else:
            PL = ph.plan.line(ln.m)
            if PL is not None and not ln.sent and not ln.bars:
                PL.hdr_w = ln.music_x0
                PL.bw = (self.cw - PAD_R_SP * self.sp - ln.music_x0) / max(1, PL.n)
                self._bar_gates(ph, ln)
            self.dirty_lines.add(ln)
            return
        self.relayout_due = True

    def _staff_fill(self, ln: _Line, x0: float, x1: float, barline: bool) -> None:
        a, b = max(0, int(round(x0))), min(self.cwid[ln.ci], int(round(x1)))
        if b <= a:
            return
        CW = self.cwid[ln.ci]
        cv = self.canvas[self.coff[ln.ci]:self.coff[ln.ci] + self.ch * CW].reshape(self.ch, CW)
        t = max(1, int(round(self.th)))
        for top in (ln.t1, ln.t2):
            for i in range(5):
                y = int(round(top + i * self.sp - 0.5 * t))
                if 0 <= y and y + t <= self.ch:
                    cv[y:y + t, a:b] = 220
        if barline:
            y0, y1 = max(0, int(round(ln.t1))), min(self.ch, int(round(ln.t2 + 4 * self.sp)) + 1)
            cv[y0:y1, max(a, b - 3):b] = 230

    def _place_bar(self, ph: _Phrase, k: int, it) -> None:
        if self.scroll:
            self._s_place_bar(ph, k, it)
            return
        plan = ph.plan
        if not plan.covers(k):
            self._ensure_plan(ph, k)
        j = plan.line_of(k)
        PL, ln = plan.line(j), self._line_of(ph, j)
        if PL is None or ln is None:
            return
        render = it if isinstance(it, tuple) else None
        if render is not None:
            img, meta = render
            m = meta["measures"][0]
            crop = m["x0"] + 0.35 * self.sp
            width = m["x1"] + 1 - crop
        else:
            width = PL.bw
        dst = plan.slot_x0(j, k)
        if dst + width > self.cw - 2 and k > PL.k0 and plan.spill(k):
            self.spills += 1
            self._ensure_plan(ph, k)
            j = plan.line_of(k)
            PL, ln = plan.line(j), self._line_of(ph, j)
            dst = plan.slot_x0(j, k)
            self._bar_gates(ph, ph.lines.get(j - 1))
            self._bar_gates(ph, ln)
            self.relayout_due = True
        self._show(ln)
        now = self.t_sound
        t_land = now
        if render is not None:
            split, dy1, dy2 = self._composite(ln, img, m, crop, m["x1"] + 1, dst)
            t_land = self._settle(ph, ln, k, meta, crop, dst, split, dy1, dy2, width)
            nat = meta.get("natural")
            if nat:
                v = min(30.0, max(1.5, nat / (ph.L * self.sp * (1.5 if ph.unit == 6 else 1.0))))
                ph.nat = v if ph.nat is None else ph.nat + 0.25 * (v - ph.nat)
        else:
            self._staff_fill(ln, dst, dst + width, barline=True)
            self.bars_empty += 1
        if self.dbg is not None:
            self.dbg.append(("place", ph.id, now, k, ln.m, round(dst / self.sp, 2), round(width / self.sp, 2),
                             round(PL.bw / self.sp, 2), round((render[1].get("natural") or 0) / self.sp, 2)
                             if render is not None else None, PL.k0, PL.n))
        PL.E += width - PL.bw
        ln.bars[k] = [dst, width, t_land]
        self._bar_gates(ph, ln)
        self._fit_check(ph, k, PL, dst + width)
        if PL.E != 0.0:
            self.dirty_lines.add(ln)                        # (the line's later slots moved)

    def _fit_check(self, ph: _Phrase, k: int, PL, x_end: float) -> None:
        """The bars landing are wider than their slots (dense music): if the line's later bars, not yet sent to the
        engraver, can't fit what's left, the line closes after bar k now (their notes move on while still being
        written, not after their ink)."""
        rest = PL.k0 + PL.n - 1 - k
        if rest <= 0 or ph.ended:
            return
        nxt = k + 1
        if nxt in ph.items or any(kk == nxt for kk in ph.sub.values()):
            return
        nat = (ph.nat or NAT_SEED[self.spacing]) * (1.5 if ph.unit == 6 else 1.0) * ph.L * self.sp
        per = max(PL.bw, SQUEEZE * nat)
        room = self.cw - PAD_R_SP * self.sp - x_end
        if rest * per > room + 0.25 * PL.bw and ph.plan.spill(nxt):
            self.spills += 1
            j = ph.plan.line_of(nxt)
            self._ensure_plan(ph, nxt)
            for m in (j - 1, j, j + 1):
                self._bar_gates(ph, ph.lines.get(m))
            self.relayout_due = True
            if self.dbg is not None:
                self.dbg.append(("respill", ph.id, self.t_sound, k))

    def _settle(self, ph: _Phrase, ln: _Line, k: int, meta: dict, crop: float, dst: float, split, dy1, dy2,
                width: float, bkey=None) -> float:
        """A bar's engraving landed: each of its notes hops to its engraved head, morphs its head, sets its stem and
        accidental as engraved.  The notes are marked engraved now and set off by _settle_step (SETTLE_PER_FRAME a
        frame, left to right); returns inf while some wait (_settle_step sets the bar's landing time: the front
        waits for it)."""
        N = self.N
        sp = self.sp
        now = self.t_sound
        notes = meta.get("_heads")
        if notes is None:
            notes = self._heads(meta)
        t_start = max(now, ph.last_wave + WAVE_S)
        ph.last_wave = t_start
        stems: dict = {}
        for nid, g, hx, hy, stem, d, acc in notes:            # a chord's stem goes to the head at its base
            if stem is not None:
                base = stem[2] if d == "up" else stem[1]
                key = tuple(stem)
                b = stems.get(key)
                if b is None or abs(hy - base) < abs(b[1] - base):
                    stems[key] = (nid, hy)
        jobs = []
        for nid, g, hx, hy, stem, sdir, acc in notes:
            dy = dy1 if hy < split else dy2
            X = dst + hx - crop
            Y = hy + dy
            srel = None
            if stem is not None and stems.get(tuple(stem), (None,))[0] == nid:
                base, tip = (stem[2], stem[1]) if sdir == "up" else (stem[1], stem[2])
                srel = ((stem[0] - hx) / sp, (base - hy) / sp, (tip - hy) / sp)
            arel = None
            if acc is not None:
                ga = GL.GI.get(acc.get("glyph"), -1)
                if ga >= 0:
                    arel = (ga, (acc["x"] - hx) / sp)
            ph.heads[int(nid)] = (ln, X, Y, g, srel, arel)
            i = N.by_id.get(int(nid))
            if i is None or N.ph[i] is not ph or N.state[i] >= 2:
                continue
            N.state[i] = 1
            N.pend[i] = True
            N.k[i] = k
            N.bar_end[i] = dst + width
            N.st_top[i] = ln.t1 if Y < split + dy1 else ln.t2
            start = t_start + WAVE_SPREAD_S * min(1.0, max(0.0, (X - dst) / max(1.0, width)))
            jobs.append((i, int(nid), g, X, Y, srel, arel, start))
        if not jobs:
            return now
        self.settle_q.append([ph, ln, k if bkey is None else bkey, k, jobs, 0, now])
        return math.inf

    def _settle_step(self) -> None:
        """Landed bars' notes set off, SETTLE_PER_FRAME a frame (oldest bar first, left to right): each hops to its
        engraved head (from where it is drawn now), morphs, sets its stem and accidental."""
        q = self.settle_q
        if not q:
            return
        N = self.N
        sp = self.sp
        now = self.t_sound
        fl = self.flourish
        budget = SETTLE_PER_FRAME
        while q and budget > 0:
            grp = q[0]
            ph, ln, bkey, k, jobs, j, t_land = grp
            bu = ph.L * ph.unit if ph.L else 0
            while j < len(jobs) and budget > 0:
                i, nid, g, X, Y, srel, arel, start = jobs[j]
                j += 1
                budget -= 1
                if not (N.used[i] and int(N.nid[i]) == nid):
                    continue
                N.pend[i] = False
                if N.state[i] != 1:
                    continue
                ci = int(N.lid[i])
                cx = float(N.tx[i] + self.offx[ci] - self.offx[ln.ci])
                cy = float(N.ty[i] + self.offy[ci] - self.offy[ln.ci])
                d = math.hypot(X - cx, Y - cy) / sp
                T = min(SETTLE_MAX, SETTLE_S0 + SETTLE_SPS * d)
                dxs = abs(X - cx) / sp
                arc = min(SETTLE_ARC, 0.3 * dxs) * sp * fl if dxs > 0.3 else 0.0
                N.hop(i, ln, X, Y, T, arc, now, self.offx, self.offy, start=start)
                t0 = max(now, float(N.h_t0[i]))
                N.set_glyph(i, g, t0)
                if srel is not None:
                    N.set_stem(i, True, *srel, t0)
                else:
                    N.set_stem(i, False, 0.0, 0.0, 0.0, t0)
                if arel is not None:
                    N.set_acc(i, arel[0], arel[1], t0)
                else:
                    N.set_acc(i, -1, 0.0, t0)
                end = max(float(N.h_t0[i]) + T, t0 + max(MORPH_S, float(N.s_T[i]), ACC_S))
                t_land = max(t_land, end)
                if self.dbg is not None:
                    key = N.key[i]
                    frac_k = ((key[1] - k * bu) / bu) if (key is not None and key[0] == "bars" and bu) else -1.0
                    self.dbg.append(("settle", nid, now, d, T, int(N.moves[i]), (X - cx) / sp, (Y - cy) / sp,
                                     frac_k, k, float(X + self.offx[ln.ci]), float(Y + self.offy[ln.ci])))
            grp[5], grp[6] = j, t_land
            if j < len(jobs):
                break
            q.pop(0)
            e = ln.bars.get(bkey)
            if e is not None and e[2] == math.inf:
                e[2] = t_land

    def _phrase_end(self) -> None:
        ph = self.ph
        N = self.N
        if not ph.committed:                                  # nothing written: its notes puff, its lines go on
            for i in list(ph.rows):
                if N.used[i] and N.state[i] < 3:
                    self._puff(i)
            new = _Phrase(ph.g0)
            for m, ln in sorted(ph.lines.items()):
                for gi in np.flatnonzero(self.g_used & (self.g_lid == ln.ci)):
                    if self.g_line[gi] is ln:
                        self._gate_free(int(gi))
                ln.phrase, ln.m = new, m
                new.lines[m] = ln
                ln.bars, ln.sent = {}, 0
                ln.front = ln.music_x0 - 0.5 * self.FE
            for i in list(ph.rows):
                N.ph[i] = None
            self.ph = new
            if self.scroll:
                self._s_phrase_end(ph, new)
            return
        ph.ended = True
        if ph.metre is not None and not self.scroll:
            ph.plan.close(ph.committed)
            j = ph.plan.line_of(max(0, ph.committed - 1)) if ph.plan.lines else None
            if j is not None:
                self._bar_gates(ph, ph.lines.get(j))
        last_m = max((m for m, ln in ph.lines.items() if ln.bars or ln.sent), default=0)
        if not self.scroll:
            last_m = max(last_m, ph.plan.line_of(max(0, ph.committed - 1)) if ph.plan.lines else 0)
        spare = []
        for m in sorted(ph.lines):
            if m > last_m:
                spare.append(ph.lines.pop(m))
        if ph.lines.get(last_m) is not None:
            ph.lines[last_m].end_x = -1.0                     # closes once its last bar is in
        self.old.append(ph)
        new = _Phrase(ph.g0 + last_m + 1)
        for ln in spare:
            for gi in np.flatnonzero(self.g_used & (self.g_lid == ln.ci)):
                if self.g_line[gi] is ln:
                    self._gate_free(int(gi))
            if ln.g == new.g0 and not self.scroll:
                ln.phrase, ln.m, ln.ts_w = new, 0, TS_SP * self.sp
                ln.bars, ln.sent = {}, 0
                ln.front = ln.music_x0 - 0.5 * self.FE
                new.lines[0] = ln
            elif not self.scroll:
                ln.phrase = None
                self._release(ln)
        self.ph = new
        if self.scroll:
            self._s_phrase_end(ph, new)
        if len(self.old) > 6:
            self.old = self.old[-6:]

    def _rewrite(self) -> None:
        """The metre turned: the phrase is written again.  While all of it is in view it's lifted and re-laid in place
        (_lift); once its start has gone (scrolled off, or its first line erased) what's written stays and the
        phrase carries on after its ink with the new time signature (_carry_on)."""
        ph = self.ph
        if self.scroll:
            in_view = ph.U0 is not None and ph.U0 > self.S_lo - 2.0 * self.sp     # (span: the room's view)
        else:
            ln0 = ph.lines.get(0)
            in_view = ln0 is not None and ln0.erase_t is None
        if (in_view and ph.cut_t is None) or ph.next_place == 0:
            self._lift()
        else:
            self._carry_on(ph)

    def _carry_on(self, ph: _Phrase) -> None:
        N = self.N
        t_cut = max((ph.bar_t[k][1] for k in range(ph.next_place) if k in ph.bar_t), default=ph.t0 or self.t_sound)
        moving = [i for i in ph.rows if N.used[i] and N.state[i] in (0, 1)]
        ph.wait, ph.sub, ph.items = [], {}, {}
        ph.committed = ph.next_place
        for i in moving:
            ph.rows.discard(i)
        self._phrase_end()
        new = self.ph
        new.cut_t = t_cut
        new.k_base = None
        new.t0 = min([float(N.t_on[i]) for i in moving] + [t_cut])
        self._phrase_started(new)
        for i in moving:
            N.ph[i] = new
            new.rows.add(i)
            N.state[i] = 0
            N.fixed[i] = False
            N.key[i] = N.cand[i] = None
            if new.newest is None or N.t_on[i] >= N.t_on[new.newest]:
                new.newest = i
        for i in moving:
            N.key[i] = self._estimate(new, i)
            N.tq[i] = self._tq(new, N.key[i], i)
        self.epoch_force = True
        if self.dbg is not None:
            self.dbg.append(("carry_on", ph.id, self.t_sound, len(moving)))

    def _lift(self) -> None:
        """The metre turned: the phrase's ink lifts back into glyphs on its heads (they never leave), then the notes
        shuffle into the new bars and the bars settle again."""
        ph = self.ph
        N = self.N
        now = self.t_sound
        ph.epoch += 1
        ph.lift_t = now
        ph.queued = []
        for ln in ph.lines.values():
            ln.lift_t = now
        for i in list(ph.rows):
            if not N.used[i]:
                continue
            N.fixed[i] = False
            if N.state[i] in (1, 2, 3):
                N.state[i] = 0
                N.hand_t0[i] = np.inf
                N.key[i] = N.cand[i] = None
        for gi in np.flatnonzero(self.g_used):
            ln = self.g_line[gi]
            if ln is not None and ln.phrase is ph and self.g_kind[gi] != TICK:
                self.g_at[gi] = 0.0
        ph.committed, ph.wait, ph.sub, ph.t_sub, ph.items, ph.next_place = 0, [], {}, {}, {}, 0
        ph.heads = {}
        if self.scroll:
            self._s_lift(ph)
        if self.dbg is not None:
            self.dbg.append(("lift", ph.id, now))

    def _lift_done(self, ph: _Phrase) -> None:
        N = self.N
        ph.lift_t = None
        for ln in ph.lines.values():
            CW = self.cwid[ln.ci]
            cv = self.canvas[self.coff[ln.ci]:self.coff[ln.ci] + self.ch * CW].reshape(self.ch, CW)
            if not self.scroll:
                cv[:, int(ln.music_x0):] = 0
            ln.bars, ln.sent = {}, 0
            ln.front, ln.front_v = ln.music_x0 - 0.5 * self.FE, 0.0
            ln.ink_a, ln.lift_t = 1.0, None
        for gi in np.flatnonzero(self.g_used):
            ln = self.g_line[gi]
            if ln is not None and ln.phrase is ph and self.g_kind[gi] != TICK:
                self._gate_free(int(gi))
        old_sig = ph.sig
        ph.metre = None
        ph.plan = LinePlan()
        queued, ph.queued = ph.queued, []
        b = self.builder
        if queued:
            self._metre(ph, queued[0].beats, queued[0].unit)
        elif b.position(self.t_sound) is not None:
            self._metre(ph, b.beats, b.unit)
        if ph.sig != old_sig:
            ph.ts_old, ph.ts_t = old_sig, self.t_sound
        for i in list(ph.rows):
            if N.used[i] and N.state[i] == 0:
                N.key[i] = self._estimate(ph, i)
                N.tq[i] = self._tq(ph, N.key[i], i)
        for bar in queued:
            self._commit(ph, bar)
        self.epoch_force = True

    # -- gates: beat ticks and bar lines --------------------------------------------------------------------------
    def _gate(self, ln: _Line, key, kind: int, x: float, alpha: float, t0: float):
        gi = self.g_key.get(key)
        if gi is None:
            free = np.flatnonzero(~self.g_used)
            if not len(free):
                return None
            gi = int(free[0])
            self.g_used[gi] = True
            self.g_key[key] = gi
            self.g_x[gi] = x
            self.g_v[gi] = 0.0
            self.g_a[gi] = 0.0
            self.g_t0[gi] = t0
        self.g_line[gi] = ln
        self.g_lid[gi] = ln.ci
        self.g_kind[gi] = kind
        self.g_tx[gi] = x
        self.g_at[gi] = alpha
        return gi

    def _gate_free(self, gi: int) -> None:
        self.g_used[gi] = False
        self.g_line[gi] = None
        for k, v in list(self.g_key.items()):
            if v == gi:
                del self.g_key[k]
                break

    def _bar_gates(self, ph: _Phrase, ln) -> None:
        """Bar lines at the line's bar ends (placed widths where placed, slots ahead): dropped in when new."""
        if ln is None or self.scroll or ph.metre is None:
            return
        PL = ph.plan.line(ln.m)
        if PL is None:
            return
        now = self.t_sound
        keep = set()
        for k in range(PL.k0, PL.k0 + PL.n):
            pl = ln.bars.get(k)
            x = (pl[0] + pl[1]) if pl is not None else ph.plan.slot_x0(ln.m, k) + PL.bw
            key = (ln.uid, "b", k)
            new = key not in self.g_key
            gi = self._gate(ln, key, BARL, x, 1.0, now + (0.04 * (k - PL.k0) * self.flourish if new else 0.0))
            if gi is not None and new and not ln.shown:
                self.g_t0[gi] = 1e18                          # drops in when its line shows
            keep.add(key)
        for key, gi in list(self.g_key.items()):
            if key[0] == ln.uid and key[1] == "b" and key not in keep:
                self.g_at[gi] = 0.0

    def _gates_step(self, dt: float) -> None:
        u = np.nonzero(self.g_used)[0]
        if not len(u):
            return
        if dt > 0:
            w = 30.0
            e = self.g_x[u] - self.g_tx[u]
            k = math.exp(-w * dt)
            c = (self.g_v[u] + w * e) * dt
            self.g_x[u] = self.g_tx[u] + (e + c) * k
            self.g_v[u] = (self.g_v[u] - w * c) * k
        # bar lines the front has passed: the ink's own bar line is there now
        fr = self._c_front[self.g_lid[u]]
        passed = (self.g_kind[u] == BARL) & (fr >= np.minimum(self.g_x[u], self.g_tx[u]) + 0.25 * self.FE)
        rate = dt / 0.15 if dt > 0 else 1.0
        a = self.g_a[u] + np.clip(self.g_at[u] - self.g_a[u], -rate, rate)
        a[passed] = 0.0
        self.g_a[u] = a
        dead = u[(self.g_at[u] <= 0.0) & (a <= 0.0)]
        for gi in dead:
            self._gate_free(int(gi))
        wake = u[self.g_t0[u] > 1e17]
        for gi in wake:
            ln = self.g_line[gi]
            if ln is not None and ln.shown:
                self.g_t0[gi] = self.t_sound

    # -- per frame: lines ---------------------------------------------------------------------------------------
    def _front_target(self, ln: _Line) -> float:
        x = ln.music_x0 - 0.5 * self.FE
        now = self.t_sound
        for k in sorted(ln.bars):
            x0, w, t_land = ln.bars[k]
            if now < t_land:
                break
            x = x0 + w + 0.5 * self.FE
        return x

    def _lines_step(self, dt: float, glow_s: float, e: float) -> None:
        now = self.t_sound
        sp = self.sp
        for ph in [self.ph] + self.old:
            if ph.lift_t is not None and now - ph.lift_t >= LIFT_S:
                self._lift_done(ph)
        # the next line shows (its slot's old line erasing) before the playing reaches it
        ph = self.ph
        N = self.N
        if ph.newest is not None and not self.scroll and N.used[ph.newest] and N.ph[ph.newest] is ph:
            if True:
                inew = ph.newest
                ln = self._row_line(inew)
                if ln is not None:
                    self.play_g = ln.g
                    if N.tx[inew] > SHOW_AT * self.cw and ln.phrase is ph and ph.lines.get(ln.m + 1) is None:
                        if ph.metre is not None:
                            guard = 0
                            while ph.plan.last < ln.m + 1 and guard < 4:
                                self._plan_line(ph)
                                guard += 1
                        nxt = self._line_of(ph, ln.m + 1)
                        self._show(nxt)
                    elif N.tx[inew] > SHOW_AT * self.cw and ph.lines.get(ln.m + 1) is not None:
                        self._show(ph.lines[ln.m + 1])
        a_up = 1.0 - math.exp(-dt / 0.15) if dt > 0 else 1.0
        a_dn = 1.0 - math.exp(-dt / max(0.1, 0.5 * glow_s)) if dt > 0 else 1.0
        for ln in list(self.lines):
            if ln.erase_t is not None:
                span = self.cw + ERASE_FEATHER_SP * sp
                ln.erase_x = (now - ln.erase_t) / ERASE_S * span - 0.5 * ERASE_FEATHER_SP * sp
                if ln.erase_x > self.cw + ERASE_FEATHER_SP * sp:
                    self._release(ln)
                    continue
            if ln.shown and not ln.placeholder:
                ln.show_a = min(1.0, ln.show_a + dt / SHOW_S) if dt > 0 else ln.show_a
            if ln.header and ln.shown:
                ln.hdr_a = min(1.0, ln.hdr_a + dt / HDR_FADE_S) if dt > 0 else 1.0
            if ln.lift_t is not None:
                ln.ink_a = 1.0 - smoothstep(0.0, LIFT_S, now - ln.lift_t)
            tgt = self._s_front_target(ln) if self.scroll else self._front_target(ln)
            d = tgt - ln.front
            if d > 0.0 and dt > 0:
                vmax, amax = FRONT_V_SPS * sp, FRONT_A_SPS * sp
                ln.front_v = min(vmax, math.sqrt(2.0 * amax * d), ln.front_v + amax * dt)
                ln.front = min(tgt, ln.front + ln.front_v * dt)
                amp = 0.55 + 0.5 * e
                ln.glow += (amp - ln.glow) * a_up
                ln.glow_len = max(2.0 * sp, 0.5 * max((w for _x, w, _t in ln.bars.values()), default=10 * sp))
            else:
                ln.front_v = 0.0
                ln.glow -= ln.glow * a_dn
            if ln.end_x is not None and ln.phrase is not None and ln.phrase.done:
                last = max(ln.bars) if ln.bars else None
                if last is not None:
                    x_end = ln.bars[last][0] + ln.bars[last][1]
                    if ln.end_x < 0:
                        ln.end_x = x_end
                        self._gate(ln, (ln.uid, "f"), FINAL, x_end + 0.6 * sp, 1.0, now)
                    if ln.front >= x_end and dt > 0:
                        ln.open_to = max(x_end + 0.9 * sp, ln.open_to - 60.0 * sp * dt)
        # lines dim by age from the playing line; the playing line's staff a little heavier
        for ln in self.lines:
            age = self.play_g - ln.g
            ln.alpha = AGE_ALPHA[min(len(AGE_ALPHA) - 1, max(0, age))] if not ln.placeholder else 0.62
            want = 1.15 if (ln.g == self.play_g and not ln.placeholder) else 1.0
            ln.weight += (want - ln.weight) * (1.0 - math.exp(-dt / 0.4) if dt > 0 else 1.0)

    # -- per frame: notes -> glyph records -------------------------------------------------------------------
    def _canvas_arrays(self) -> None:
        """Per canvas, once a frame: its line's front, visibility, erase front and staff tops (vector lookups)."""
        NC = self.NC
        fr, la, er, t1, t2 = (np.full(NC, -1e9), np.zeros(NC), np.full(NC, -1e9), np.zeros(NC), np.zeros(NC))
        for ln in self.lines:
            c = ln.ci
            fr[c] = ln.front
            la[c] = ln.alpha if ln.shown else 0.0             # (a note never waits for its line's fade-in)
            er[c] = ln.erase_x
            t1[c], t2[c] = ln.t1, ln.t2
        self._c_front, self._c_alpha, self._c_erase, self._c_t1, self._c_t2 = fr, la, er, t1, t2

    def _notes_step(self, p: dict, e: float) -> int:
        N = self.N
        sp = self.sp
        now = self.t_sound
        live = np.nonzero(N.used & (N.state != 3))[0]
        if len(live):
            st = N.state[live]
            # a held note's head opens as it's held (only ever grows): half after 2 beats, whole after 4
            prov = live[st == 0]
            if len(prov):
                end = np.where(np.isinf(N.t_next[prov]), np.minimum(N.t_off[prov], now), N.t_next[prov])
                held = (np.minimum(end, now) - N.t_on[prov]) * (1.0 / self._beat_period())
                want = np.where(held >= WHOLE_BEATS, GL.WHOLE, np.where(held >= HALF_BEATS, GL.HALF, GL.BLACK))
                chg = want > N.gb[prov]
                if chg.any():
                    idx = prov[chg]
                    N.ga[idx] = N.gb[idx]
                    N.gb[idx] = want[chg]
                    N.g_t0[idx] = now
                    if (want[chg] == GL.WHOLE).any():
                        self.dirty_rows.update(int(j) for j in idx[want[chg] == GL.WHOLE])
            # hand over: an engraved note that has landed, its line's front past it
            eng = live[st == 1]
            if len(eng):
                ok = (~N.pend[eng] & (now >= N.h_t0[eng] + N.h_T[eng]) & (now >= N.g_t0[eng] + MORPH_S)
                      & (now >= N.s_t0[eng] + N.s_T[eng]) & (now >= N.a_t0[eng] + ACC_S)
                      & (self._c_front[N.lid[eng]] >= np.minimum(N.tx[eng] + (self.adv[N.gb[eng]] * sp + 0.5 * self.FE),
                                                                    N.bar_end[eng])))
                if ok.any():
                    idx = eng[ok]
                    N.state[idx] = 2
                    N.hand_t0[idx] = now
                    if self.dbg is not None:
                        for i in idx:
                            self.dbg.append(("hand", int(N.nid[i]), now))
            hd = live[st == 2]
            if len(hd):
                N.state[hd[now - N.hand_t0[hd] >= HAND_S]] = 3
            pf = live[st == 4]
            for i in pf[now - N.die_t0[pf] >= PUFF_S]:
                N.free(int(i))
        idx = np.nonzero(N.used & (N.state != 3))[0]
        cap = GL.MAXN - 64                                  # (room for the bar lines and time signatures)
        if len(idx) > cap:                                  # degrade: the newest notes drawn, the oldest wait for ink
            idx = np.sort(idx[np.argsort(-N.t_on[idx], kind="stable")[:cap]])
        n = len(idx)
        R = self.recs
        nrec = 0
        if self.dbg is None:                                # the GPU builds the notes' records (_pack, recs)
            nrec = self._emit_gates(R, n)
            nrec = self._emit_ts(R, nrec)
            nrec = min(nrec, GL.MAXN)
            self.gpu_recs = (idx, n, nrec - n, p, e)
            return nrec
        self.gpu_recs = None
        if n:
            ci = N.lid[idx]
            T = N.h_T[idx]
            tau = _cl(now - N.h_t0[idx], 0.0, T)
            fin = tau >= T
            Cx, Cy = N.C[0][:, idx], N.C[1][:, idx]
            x = Cx[0] + tau * (Cx[1] + tau * (Cx[2] + tau * (Cx[3] + tau * (Cx[4] + tau * Cx[5]))))
            y = Cy[0] + tau * (Cy[1] + tau * (Cy[2] + tau * (Cy[3] + tau * (Cy[4] + tau * Cy[5]))))
            x = np.where(fin, N.tx[idx], x)
            y = np.where(fin, N.ty[idx], y - N.arc[idx] * np.sin(np.pi * tau / np.maximum(T, 1e-6)))
            X = x + self.offx[ci]
            Y = y + self.offy[ci]
            wy = 0.0
            if self.scroll and self.SR > 1:
                wj, wx, wy = self._s_wrap(X)
                X, Y = X + wx, Y + wy
            age = now - N.t_born[idx]
            s = 1.0 + (POP * self.flourish) * (1.0 - _ease(age * (1.0 / POP_S)))
            ht = _cl((now - N.hand_t0[idx]) * (1.0 / HAND_S), 0.0, 1.0)
            pu = _cl((now - N.die_t0[idx]) * (1.0 / PUFF_S), 0.0, 1.0)
            alpha = (np.minimum(1.0, 0.85 + age * (0.15 / BIRTH_S)) * (1.0 - ht) * (1.0 - pu) * self._c_alpha[ci]
                     * _cl((x - self._c_erase[ci]) * (1.0 / (ERASE_FEATHER_SP * sp)) + 0.5, 0.0, 1.0))
            s = s * (1.0 + 0.4 * pu)
            if self.scroll:
                alpha = alpha * _cl((X - self.X_fade) * (0.5 / sp), 0.0, 1.0)
                if self.SR > 1:
                    alpha = alpha * np.where(wj > 0, _cl((self.X_left + self.tape_w - X) * (0.5 / sp), 0.0, 1.0), 1.0)
            if self.dbg is not None:
                rd = (alpha >= 0.8) & ~N.readable[idx]
                for j in np.nonzero(rd)[0]:
                    i = idx[j]
                    N.readable[i] = True
                    self.dbg.append(("readable", int(N.nid[i]), now, time.perf_counter()))
            ga, gb = N.ga[idx], N.gb[idx]
            mt = _cl((now - N.g_t0[idx]) * (1.0 / MORPH_S), 0.0, 1.0)
            OX = X + (0.5 * sp) * self.adv[gb] * (1.0 - s)
            # stems (rel. to the head's origin, sp): a glide, a twist through the head, a grow or a retract
            su = _cl((now - N.s_t0[idx]) / N.s_T[idx], 0.0, 1.0)
            se = su * su * (3.0 - 2.0 * su)
            A0, B0 = N.S[0][:, idx], N.S[1][:, idx]
            sa, sb, flip = N.sa[idx], N.sb[idx], N.s_flip[idx]
            useB = sb & (~sa | flip & (su >= 0.5))
            lerp = sa & sb & ~flip
            sx_ = np.where(lerp, A0[0] + (B0[0] - A0[0]) * se, np.where(useB, B0[0], A0[0]))
            sy0 = np.where(lerp, A0[1] + (B0[1] - A0[1]) * se, np.where(useB, B0[1], A0[1]))
            cosu = np.cos(np.pi * su)
            k = np.where(lerp, 1.0, np.where(sa & sb, np.abs(cosu), np.where(sa, 1.0 - se, se)))
            ty1 = np.where(lerp, A0[2] + (B0[2] - A0[2]) * se, np.where(useB, B0[2], A0[2]))
            sy1 = np.where(lerp, ty1, sy0 + (ty1 - sy0) * k)
            son = (sa | sb) & (np.abs(sy1 - sy0) > 0.05)
            # accidentals: a move, or a pop out and in
            au = _cl((now - N.a_t0[idx]) * (1.0 / ACC_S), 0.0, 1.0)
            aa, ab = N.aa[idx], N.ab[idx]
            same = aa == ab
            first = ~same & (au < 0.5)
            ag = np.where(first, aa, ab)
            adx = np.where(same, N.adxa[idx] + (N.adxb[idx] - N.adxa[idx]) * (au * au * (3.0 - 2.0 * au)),
                           np.where(first, N.adxa[idx], N.adxb[idx]))
            asc = np.where(same, 1.0, np.abs(1.0 - 2.0 * au))
            asc = np.where(ag >= 0, asc, 0.0)
            # ledger lines: those of where the note is going (its staff, its target), carried with the head, so a
            # note hopping between staves takes its own few along instead of drawing a ladder to the staff
            ty = N.ty[idx] + wy
            top = N.st_top[idx] + wy
            bot = top + 4.0 * sp
            nab = np.minimum(np.floor((top - ty) * (1.0 / sp) + 0.01), 6.0)
            nbe = np.minimum(np.floor((ty - bot) * (1.0 / sp) + 0.01), 6.0)
            above = nab >= 1.0
            top = top + (Y - ty)
            bot = bot + (Y - ty)
            gadv = self.adv[np.where(mt >= 0.5, gb, ga)]
            # colour: the palette around the circle of fifths while it sounds, cooling to ink
            tint = ((N.pitch[idx] * 7.0) % 12.0) * (float(p["palette_period"]) / 12.0) + self.drift % 10.0
            pc = self._pal
            rgb = _cl(pc[0] + pc[1] * np.cos(2.0 * math.pi * (pc[2] * tint[:, None] + pc[3])), 0.0, 1.0)
            hold = np.exp(-np.maximum(0.0, now - N.t_off[idx]) * (1.0 / REL_S))
            fresh = np.maximum(np.exp(-age * (1.0 / FRESH_S)), 0.75 * hold)
            halo = float(p["note_glow"]) * (0.8 + 0.6 * e) * np.where(age < 0.25, 1.0 + 0.6 * self.kick, 1.0)
            Q = R[:n]
            Q[:, 0], Q[:, 1], Q[:, 2], Q[:, 3] = OX, Y, sp * s, sp * s
            Q[:, 4], Q[:, 5], Q[:, 6], Q[:, 7] = ga, gb, mt, alpha
            Q[:, 8] = X + sx_ * sp
            Q[:, 9] = Y + sy0 * sp
            Q[:, 10] = Y + sy1 * sp
            Q[:, 11] = np.where(son, GL.STEM_HW * sp, 0.0)
            Q[:, 12] = np.where(asc > 0.01, ag, -1)
            Q[:, 13] = X + adx * sp
            Q[:, 14] = asc
            Q[:, 15] = np.where(above, top - sp, bot + sp)
            Q[:, 16] = np.where(above, -sp, sp)
            Q[:, 17] = np.where(above, nab, np.maximum(nbe, 0.0))
            Q[:, 18] = 0.5 * fresh
            Q[:, 19] = halo * fresh
            Q[:, 20:23] = rgb
            Q[:, 27] = X - GL.LEDGER_EXT * sp
            Q[:, 28] = X + (gadv + GL.LEDGER_EXT) * sp
            Q[:, 29] = Y
            nrec = n
        nrec = self._emit_gates(R, nrec)
        nrec = self._emit_ts(R, nrec)
        if nrec > GL.MAXN:
            nrec = GL.MAXN
        return GL.boxes_sort(R, nrec, sp, HALO_SP, self.GNh, self.GUh, self.adv)

    def _emit_gates(self, R, n: int) -> int:
        u = np.nonzero(self.g_used & (self.g_a > 0.003))[0]
        m = min(len(u), GL.MAXN - n)
        if m <= 0:
            return n
        idx = u[:m]
        sp = self.sp
        ci = self.g_lid[idx]
        x = self.g_x[idx]
        kind = self.g_kind[idx]
        now = self.t_sound
        d = _cl((now - self.g_t0[idx]) * (1.0 / GATE_DROP_S), 0.0, 1.0)
        bar = kind == BARL
        grow = np.where(bar, _ease(d) * (1.0 + 0.1 * self.flourish * np.sin(np.pi * d)), 1.0)
        top = self.offy[ci] + self._c_t1[ci]
        bot = self.offy[ci] + self._c_t2[ci] + 4.0 * sp
        a = (self.g_a[idx] * self._c_alpha[ci]
             * _cl((x - self._c_erase[ci]) * (1.0 / (ERASE_FEATHER_SP * sp)) + 0.5, 0.0, 1.0))
        Xs = x + self.offx[ci]
        if self.scroll:
            wj, wx, wy = self._s_wrap(Xs)
            Xs, top, bot = Xs + wx, top + wy, bot + wy
            a = a * _cl((Xs - self.X_fade) * (0.5 / sp), 0.0, 1.0)
            if self.SR > 1:
                a = a * np.where(wj > 0, _cl((self.X_left + self.tape_w - Xs) * (0.5 / sp), 0.0, 1.0), 1.0)
        a = np.where(bar & (self.g_t0[idx] > now), 0.0, a)
        Q = R[n:n + m]
        Q[:] = 0.0
        Q[:, 4] = Q[:, 5] = Q[:, 12] = -1
        Q[:, 7] = a
        Q[:, 8] = Xs
        Q[:, 9] = top
        Q[:, 10] = top + (bot - top) * np.minimum(grow, 1.1)
        Q[:, 11] = np.where(kind == TICK, 0.04 * sp, np.where(kind == FINAL, 0.25 * sp, GL.BAR_HW * sp))
        return n + m

    def _emit_ts(self, R, n: int) -> int:
        """The time signature on each phrase's first line: flipped in (and over, after a rewrite) like a split-flap."""
        sp = self.sp
        for ph in [self.ph] + self.old:
            if ph.metre is None or ph.ts_t is None:
                continue
            ln = self.tape_line if self.scroll else ph.lines.get(0)
            if ln is None or not ln.shown:
                continue
            if self.scroll and getattr(ph, "U0", None) is None:
                continue
            u = min(1.0, max(0.0, (self.t_sound - ph.ts_t) / 0.3))
            sig = ph.sig if (ph.ts_old is None or u >= 0.5) else ph.ts_old
            k = abs(math.cos(math.pi * u)) if ph.ts_old is not None else _ease(u)
            k = max(0.02, k)
            tsy = 0.0
            if self.scroll:
                cx = self.offx[0] + ph.U0 + 0.5 * TS_SP * sp
                wj, wx, wy = self._s_wrap(np.array([cx]))
                cx, tsy = cx + float(wx[0]), float(wy[0])
                a = min(1.0, max(0.0, (cx - self.X_fade) / (2.0 * sp)))
                if wj[0] > 0:
                    a *= min(1.0, max(0.0, (self.X_left + self.tape_w - cx) / (2.0 * sp)))
                if cx < self.X_left - 3.0 * sp or cx > self.w + 3.0 * sp:
                    continue
            else:
                cx = self.offx[ln.ci] + ln.hdr_w + 0.5 * TS_SP * sp - 0.2 * sp
                a = ln.alpha * ln.show_a * min(1.0, max(0.0, (ln.hdr_w + 1.0 * sp - ln.erase_x)
                                                        / (ERASE_FEATHER_SP * sp) + 0.5))
            for top in (ln.t1, ln.t2):
                for digits, dy in ((str(sig[0]), 1.0), (str(sig[1]), 3.0)):
                    gs = [GL.TS0 + int(c) for c in digits]
                    wsum = sum(self.adv[g] for g in gs)
                    x = cx - 0.5 * wsum * sp
                    for g in gs:
                        if n >= GL.MAXN:
                            return n
                        Q = R[n]
                        Q[:] = 0.0
                        Q[0], Q[1], Q[2], Q[3] = x, self.offy[ln.ci] + tsy + top + dy * sp, sp, sp * k
                        Q[4] = Q[5] = g
                        Q[7] = a
                        Q[12] = -1
                        n += 1
                        x += self.adv[g] * sp
        return n

    # -- scroll ----------------------------------------------------------------------------------------------------
    def _setup_scroll(self) -> None:
        """One grand staff on a ring tape under a fixed playhead (new notes appear there), the paper moving at the
        music's pace (a critically damped follow of where the music is now, never backwards), a sticky header on
        the left (brace, clefs, key)."""
        sp = self.sp
        span = (self.SR - 1) * self.pitch + (TOP_SP + STAVE_GAP_SP + 4.0 + TOP_SP) * sp
        y_top = 0.5 * (self.h - span)
        self.row_y = [int(round(y_top + (self.SR - 1 - j) * self.pitch)) for j in range(self.SR)]   # newest first
        self.y0 = self.row_y[0]
        self.x_h0 = int(round(X_HDR * self.w))
        ln = _Line(0, 0, 0, sp, self.TW)
        ln.shown, ln.show_a, ln.alpha = True, 1.0, 1.0
        ln.hdr_w, ln.ts_w, ln.front = 0.0, 0.0, 0.0
        ln.open_to = 1e12
        self.tape_line = ln
        self.lines = [ln]
        self.hdr = {"w": self._hdr_est(self._key_now()), "a": 0.0, "fifths": None, "req": 0, "x0": (MARGIN_SP + 0.4) * sp}
        self.hdr_wd = self.hdr["w"]
        self.S, self.V, self.S_ff, self.S_tp = 0.0, 0.0, 0.0, None
        self.frontier = None                         # the tape's last ink (None: nothing written yet)
        self.v_last = None
        self._s_geom()
        self.offy[0] = self.offy[1] = self.y0
        self.offx[1] = self.x_h0
        self.ph = _Phrase(0)
        self._s_header(self._key_now())

    def _s_geom(self) -> None:
        sp = self.sp
        if self.span is not None:
            self._span_geom()
            return
        self.X_left = self.x_h0 + self.hdr_wd + 1.5 * sp
        self.X_R = self.w - 2.0 * sp
        self.tape_w = max(sp, self.X_R - self.X_left)
        self.offx[0] = self.X_left - self.S
        self.S_win = self.S - (self.SR - 1) * self.tape_w      # (the tape's left end on screen)
        self.S_lo = self.S_win
        self.X_fade = self.X_left

    def _span_geom(self) -> None:
        """span: room.  Screen j's tape area runs from after its header (every screen's, or the first's only) to
        its right edge (the last screen's: a margin); screen j+1's starts where screen j's ends.  S is the tape at
        the left of the last screen's area (its playhead at PLAY_X of it); this screen shows its own part."""
        sp = self.sp
        k, n = self.span
        hdr = self.x_h0 + self.hdr_wd + 1.5 * sp
        left = [hdr if (j == 0 or not self.span_first) else 0.0 for j in range(n)]
        W = [(self.w - 2.0 * sp if j == n - 1 else float(self.w)) - left[j] for j in range(n)]
        self.X_left = left[k]
        self.X_R = left[k] + W[k]
        self.tape_w = max(sp, W[n - 1])
        self.S_win = self.S - sum(W[k:n - 1])
        self.S_lo = self.S - sum(W[0:n - 1])                 # (the room's left end: the first screen's)
        self.offx[0] = self.X_left - self.S_win
        self.X_fade = self.X_left if left[k] > 0 else -1e6     # (no fade in at a seam)

    def _s_header(self, fifths: int) -> None:
        h = self.hdr
        if h["fifths"] == fifths:
            return
        h["fifths"] = fifths
        h["req"] += 1
        self.tape_line.fifths = fifths
        if self.engraver is None:
            return
        from stream_service.score import mei
        self.engraver.submit(f"H:{h['req']}", mei.header_doc(fifths, meter=False), margin_left=int(MARGIN_SP * self.sp))

    def _s_header_landed(self, key, render) -> None:
        h = self.hdr
        if key != f"H:{h['req']}":
            return
        img, meta = render
        m = meta["measures"][0]
        w = float(min(self.HW, m["x1"] + 1))
        cv = self.canvas[self.coff[1]:self.coff[1] + self.ch * self.HW].reshape(self.ch, self.HW)
        cv.fill(0)
        parts, _s, _d1, _d2 = self._parts(m, self.tape_line.t1, img.shape[0], STAVE_GAP_SP * self.sp)
        c = int(min(w, img.shape[1]))
        for r0, r1, dy in parts:
            cv[r0:r1, :c] = img[r0 - dy:r1 - dy, :c]
        h["w"], h["x0"] = w, float(m["x0"])
        h["a"] = min(h["a"], 0.35)                          # a new key: the header flashes in again

    def _s_v(self) -> float:
        """px a second on the tape before the metre: the last phrase's, else bars at SQUEEZE of their natural width
        at 100 bpm (the tape then holds about 15 s)."""
        if self.v_last and self.span is None:
            return self.v_last
        return SQUEEZE * NAT_SEED[self.spacing] * self.sp / 0.6

    def _s_start(self, ph: _Phrase) -> None:
        """A phrase's first note: its place on the tape, at the playhead (after the last ink, a gap on)."""
        sp = self.sp
        if ph.v is None:
            ph.v = self._s_v()
        lag = max(0.0, self.t_sound - (ph.t0 or self.t_sound))
        u_play = self.S + PLAY_X * self.tape_w - lag * ph.v - self.A
        if self.span is not None:
            ph.U_pre = self.S + PLAY_X * self.tape_w if self.frontier is not None else None
        ph.U0 = u_play - TS_SP * sp if self.frontier is None else max(self.frontier + 2.0 * sp, u_play - TS_SP * sp)
        ph.Um0 = ph.U0 + TS_SP * sp
        ph.ruler = None
        ph.fifths_tape = self._key_now()
        self._s_header(ph.fifths_tape)

    def _s_metre(self, ph: _Phrase) -> None:
        from stream_service.score.motion import Ruler
        if getattr(ph, "U0", None) is None:
            self._s_start(ph)
        b = self.builder
        bar_s = b.bar_seconds() or ph.L * self._beat_period()
        nat_px = (ph.nat or NAT_SEED[self.spacing]) * (1.5 if ph.unit == 6 else 1.0) * ph.L * self.sp
        w = max(SQUEEZE * nat_px, ph.v * bar_s)
        if self.span is not None:
            w = self._span_w(ph, bar_s)
        ph.ruler = Ruler(ph.U0, ph.Um0 - ph.U0, w)
        ph.span_fz = 0
        ph.v_bar = w / max(0.2, bar_s)
        self.v_last = ph.v_bar
        self._s_gates(ph)

    def _s_slot(self, ph: _Phrase, k: int):
        """Bar k's place on the tape (its slot frozen when first asked for: written bars never move)."""
        r = ph.ruler
        if self.span is not None:                           # a committed bar's slot from its own length, frozen;
            j = getattr(ph, "span_fz", 0)                   # later ones at the tempo now (both from the room)
            while j in ph.bar_t:
                t0, t1 = ph.bar_t[j]
                r.freeze(j, self._span_w(ph, t1 - t0))
                j += 1
            ph.span_fz = j
            r.set_prov(self._span_w(ph, self.builder.bar_seconds() or ph.L * self._beat_period()))
            return r.u0(k), r.width(k)
        for j in range(len(r.w), k + 1):
            r.freeze(j, r.w_prov)
        return r.u0(k), r.width(k)

    def _s_fit(self, ph: _Phrase, k: int) -> float:
        return self._s_slot(ph, k)[1]

    def _s_place_bar(self, ph: _Phrase, k: int, it) -> None:
        r = ph.ruler
        if r is None:
            return
        sp = self.sp
        u, wslot = self._s_slot(ph, k)
        ln = self.tape_line
        now = self.t_sound
        t_land = now
        render = it if isinstance(it, tuple) else None
        width = wslot
        if render is not None:
            img, meta = render
            m = meta["measures"][0]
            crop = m["x0"] + 0.35 * sp
            width = m["x1"] + 1 - crop
            split, dy1, dy2 = self._composite(ln, img, m, crop, m["x1"] + 1, u)
            t_land = self._settle(ph, ln, k, meta, crop, u, split, dy1, dy2, width, bkey=(ph.id, k))
            nat = meta.get("natural")
            if nat and self.span is None:
                v = min(30.0, max(1.5, nat / (ph.L * sp * (1.5 if ph.unit == 6 else 1.0))))
                ph.nat = v if ph.nat is None else ph.nat + 0.25 * (v - ph.nat)
        else:
            self._s_staff_fill(u, u + width)
            self.bars_empty += 1
        r.place(k, width)
        ln.bars[(ph.id, k)] = [u, width, t_land]
        self.frontier = max(self.frontier if self.frontier is not None else -1e18, u + width)
        if abs(width - wslot) > 0.5:
            self.relayout_due = True
        self._s_gates(ph)
        if ph.ended and k >= ph.committed - 1:
            self._gate(ln, (ln.uid, "f", ph.id), FINAL, u + width + 0.6 * sp, 1.0, now)
            self.frontier = max(self.frontier, u + width + sp)                      # (set just above)

    def _s_staff_fill(self, u0: float, u1: float) -> None:
        from stream_service.score.motion import ring_slices
        ln = self.tape_line
        TW = self.TW
        cv = self.canvas[self.coff[0]:self.coff[0] + self.ch * TW].reshape(self.ch, TW)
        t = max(1, int(round(self.th)))
        for d, _o, n in ring_slices(int(round(u0)), int(round(u1 - u0)), TW):
            cv[:, d:d + n] = 0
            for top in (ln.t1, ln.t2):
                for i in range(5):
                    y = int(round(top + i * self.sp - 0.5 * t))
                    if 0 <= y and y + t <= self.ch:
                        cv[y:y + t, d:d + n] = 220

    def _s_gates(self, ph: _Phrase) -> None:
        """Bar lines at the bar ends ahead on the tape (placed widths where placed), dropped in when new."""
        r = getattr(ph, "ruler", None)
        if r is None:
            return
        ln = self.tape_line
        now = self.t_sound
        lo = self.S_win - 4.0 * self.sp
        hi = self.S + self.tape_w + 4.0 * self.sp
        k = max(0, len(r.w) - 12)
        while k < len(r.w) + 4:
            x = r.u0(k) + r.width(k)
            if x > hi:
                break
            if x >= lo and x > ln.front:
                key = (ln.uid, ph.id, k)
                new = key not in self.g_key
                self._gate(ln, key, BARL, x, 1.0, now if new else 0.0)
            k += 1

    def _s_u(self, ph: _Phrase, t: float):
        """The tape position of time t in the phrase's layout (bars once the metre is known)."""
        if ph.t0 is None or getattr(ph, "U0", None) is None:
            return None
        if ph.ruler is not None:
            pos = self.builder.position(t) if (ph is self.ph and ph.k_base is not None) else None
            if pos is not None:
                pos -= ph.k_base
                k = max(0, int(math.floor(pos)))
                f = pos - k
                w = ph.ruler.width(k)
                return ph.ruler.u0(k) + self.A + f * max(self.sp, w - self.A - self.C)
        return ph.Um0 + self.A + max(0.0, t - ph.t0) * ph.v

    def _scroll_step(self, dt: float) -> None:
        """The paper moves at the music's pace: the phrase's tempo (its bars' width a second, before the metre its
        time scale) fed forward, eased, plus a gentle pull that keeps the newest notes near the playhead; never
        backwards, its acceleration capped; after a phrase it eases to a stop."""
        sp = self.sp
        ph = self.ph
        N = self.N
        inew = ph.newest if (ph.t0 is not None and not ph.ended) else None
        if self.span is not None:
            inew = None
            S0 = self.S
            u = self._span_u()
            if u is not None:
                self.S = u - PLAY_X * self.tape_w
            self.V = (self.S - S0) / dt if dt > 0 else self.V
            dt_ = 0.0
        else:
            dt_ = dt
        v_t = 0.0
        if inew is not None and N.used[inew] and N.ph[inew] is ph:
            v_ff = ph.v_bar if (ph.metre is not None and ph.v_bar) else ph.v
            u_new = N.tx[inew] + (self.t_sound - LAG_S - N.t_on[inew]) * v_ff      # where 'now' is on the tape
            err = u_new - (self.S + PLAY_X * self.tape_w)
            if self.frontier is None:                       # nothing written on the tape yet (a start, or a join
                self.S += err                               # mid-phrase): the paper is simply placed, the playhead
                err = 0.0                                   # at the newest notes, at the music's pace
                self.V = self.S_ff = v_ff
                self.tape_line.front = min(self.tape_line.front,
                                           self.S_win - self.FE)
            v_t = max(0.0, v_ff + S_PULL * err)
            if self.t_sound - N.t_on[inew] > 2.5:                                    # a pause in the playing
                v_t *= max(0.0, 1.0 - (self.t_sound - N.t_on[inew] - 2.5) / 2.0)
        if dt_ > 0:
            self.S_ff += (v_t - self.S_ff) * (1.0 - math.exp(-dt / S_TAU))
            amax = S_AMAX_SP * sp
            dv = min(amax * dt, max(-amax * dt, self.S_ff - self.V))
            self.V = max(0.0, self.V + dv)
            self.S += self.V * dt
        # the sticky header
        h = self.hdr
        h["a"] = min(1.0, h["a"] + dt / HDR_FADE_S) if dt > 0 else h["a"]
        self.hdr_wd += (h["w"] - self.hdr_wd) * (1.0 - math.exp(-dt / 0.3)) if dt > 0 else 0.0
        self._s_geom()
        for q in [ph] + self.old:
            if getattr(q, "ruler", None) is not None and not q.done:
                self._s_gates(q)
        # rows long off the left edge go
        N = self.N
        back = (self.SR - 1) * self.tape_w
        off = N.used & (N.state >= 2) & (N.tx + self.offx[0] + back < self.X_left - 6.0 * sp)
        back = self.S - self.S_win
        for i in np.nonzero(off)[0]:
            N.free(int(i))
        ln = self.tape_line
        for key in [k for k, b in ln.bars.items() if b[0] + b[1] < self.S - back - 8.0 * sp and b[0] + b[1] < ln.front]:
            del ln.bars[key]

    def _span_u(self):
        """(span) Where the music was LAG_S ago on the tape, averaged over SPAN_DT (the paper's speed eased across
        bars of different widths): from the phrase's layout and the music's time only, so every screen's instance,
        started whenever, puts the paper in the same place."""
        ph = self.ph
        if ph.t0 is None or getattr(ph, "U0", None) is None:
            return None
        t = self.t_sound - LAG_S
        t_stop = max(ph.t0, getattr(ph, "t_last", ph.t0)) + 2.5          # a pause: the paper stops (eased)
        acc = 0.0
        for j in range(SPAN_NS):
            tau = t - SPAN_DT * j / (SPAN_NS - 1)
            if tau > t_stop:
                x = min(2.0, tau - t_stop)
                tau = t_stop + x - 0.25 * x * x
            if tau < ph.t0 and getattr(ph, "U_pre", None) is not None:
                acc += ph.U_pre
                continue
            acc += self._span_u_at(ph, max(tau, ph.t0))
        return acc / SPAN_NS

    def _span_u_at(self, ph: _Phrase, tau: float) -> float:
        r = ph.ruler
        if r is not None and ph.k_base is not None:
            pos = self.builder.position(tau)
            if pos is not None and pos >= ph.k_base:
                pos -= ph.k_base
                k = int(math.floor(pos))
                return r.u0(k) + (pos - k) * r.width(k)
        return ph.Um0 + self.A + max(0.0, tau - ph.t0) * ph.v

    def _span_w(self, ph: _Phrase, d: float) -> float:
        """(span) A bar's slot from its length alone (no history: the screens' instances agree)."""
        nat_px = NAT_SEED[self.spacing] * (1.5 if ph.unit == 6 else 1.0) * ph.L * self.sp
        return max(SQUEEZE * nat_px, ph.v * min(6.0, max(0.3, d)))

    def _s_front_target(self, ln: _Line) -> float:
        x = ln.front
        now = self.t_sound
        for x0, w, t_land in sorted(ln.bars.values()):
            if x0 + w + 0.5 * self.FE <= x:
                continue
            if now < t_land:
                break
            x = x0 + w + 0.5 * self.FE
        return x

    def _s_phrase_end(self, old: _Phrase, new: _Phrase) -> None:
        if old.ruler is not None and old.committed and old.next_place >= old.committed:
            k = old.committed - 1
            u, w = old.ruler.u0(k), old.ruler.width(k)
            ln = self.tape_line
            self._gate(ln, (ln.uid, "f", old.id), FINAL, u + w + 0.6 * self.sp, 1.0, self.t_sound)
            self.frontier = max(self.frontier if self.frontier is not None else -1e18, u + w + self.sp)
        for key, gi in list(self.g_key.items()):                 # bar lines planned past its end
            if len(key) == 3 and key[1] == old.id and isinstance(key[2], int) and key[2] >= old.committed:
                self.g_at[gi] = 0.0
        new.lines = {}

    def _s_key_change(self, ph: _Phrase, k: int, f: int) -> None:
        """(scroll) The key heard changed: the new key signature is written inline before bar k (its room made on the
        tape at once, the engraving placed when it lands) and the sticky header takes it."""
        sp = self.sp
        ph.fifths_tape = f
        ph.ruler.insert(k, (1.0 + 1.15 * abs(f)) * sp + sp)
        self._s_header(f)
        self.relayout_due = True
        if self.engraver is not None:
            from stream_service.score import mei
            key = f"i:{ph.id}:{ph.epoch}:{k}"
            self.engraver.submit(key, mei.change_doc(f))
            ph.ins_sub[key] = k

    def _s_key_landed(self, ph: _Phrase, k: int, render) -> None:
        img, meta = render
        m = meta["measures"][0]
        r = ph.ruler
        if r is None:
            return
        crop = m["x0"] + 0.35 * self.sp
        w = m["x1"] + 1 - crop
        if not (k < len(r.act) and r.act[k] is not None):        # its bar not written yet: the room fits it
            r.set_insert(k, w + 0.6 * self.sp)
            self.relayout_due = True
        u = r.u0(k) - r.ins[k]
        self._composite(self.tape_line, img, m, crop, m["x1"] + 1, u)
        ln = self.tape_line
        ln.bars[(ph.id, k - 0.5)] = [u, w, self.t_sound]

    def _s_lift(self, ph: _Phrase) -> None:
        """(scroll) A rewrite: the phrase's ink comes off the tape (its notes stay, as glyphs on their heads), its
        bar lines rise out, and it's laid out again from where it starts."""
        from stream_service.score.motion import ring_slices
        ln = self.tape_line
        mine = [k for k in ln.bars if k[0] == ph.id]
        if mine:
            u0 = min(ln.bars[k][0] for k in mine)
            u1 = max(ln.bars[k][0] + ln.bars[k][1] for k in mine)
            cv = self.canvas[self.coff[0]:self.coff[0] + self.ch * self.TW].reshape(self.ch, self.TW)
            for d, _o, n in ring_slices(int(u0), int(math.ceil(u1 - u0)) + 2, self.TW):
                cv[:, d:d + n] = 0
            for k in mine:
                del ln.bars[k]
            ln.front = min(ln.front, u0 - 0.5 * self.FE)
            self.frontier = max(ph.U0 or 0.0, min(self.frontier if self.frontier is not None else u0, u0))
        for key, gi in list(self.g_key.items()):
            if len(key) == 3 and key[1] == ph.id:
                self.g_at[gi] = 0.0
        ph.ruler = None

    def _s_canvas_params(self, Rh, sp: float) -> None:
        """Row j (0 the newest, at the bottom) shows the tape from S - j W: the music leaving a row's left edge
        carries on at the right end of the row above, so the rows read top to bottom like a page."""
        ln = self.tape_line
        h = self.hdr
        W = self.tape_w
        for j in range(self.SR):
            o = 2 * j * RS
            Rh[o:o + 2 * RS] = 0.0
            Rh[o + 0] = self.offx[0] + j * W
            Rh[o + 1] = self.row_y[j]
            Rh[o + 2] = 1.0
            Rh[o + 3] = ln.front if self.engraver is not None else -1e9
            Rh[o + 4] = self.FE
            Rh[o + 5] = ln.glow
            Rh[o + 6] = ln.glow_len
            Rh[o + 7] = -1e9
            Rh[o + 9], Rh[o + 10] = -1e12, 1e12
            Rh[o + 11] = 1.0
            Rh[o + 12], Rh[o + 13] = ln.t1, ln.t2
            Rh[o + 14], Rh[o + 15] = -1e12, 1.0
            Rh[o + 16] = ln.ink_a
            Rh[o + 17] = self.TW
            Rh[o + 18], Rh[o + 19] = self.X_fade + sp, 2.0 * sp
            Rh[o + 20] = self.TW
            Rh[o + 21] = 1e9 if j == 0 else self.X_left + W - sp
            o += RS
            Rh[o + 0], Rh[o + 1] = self.x_h0, self.row_y[j]
            Rh[o + 2] = 0.0 if (self.span is not None and self.span_first and self.span[0] > 0) else 1.0
            Rh[o + 3], Rh[o + 4] = 1e9, self.FE
            Rh[o + 7], Rh[o + 8] = float(self.HW), h["a"]
            Rh[o + 9], Rh[o + 10] = h["x0"], self.X_left - self.x_h0 + 3.0 * sp
            Rh[o + 11] = 1.0
            Rh[o + 12], Rh[o + 13] = ln.t1, ln.t2
            Rh[o + 14], Rh[o + 15] = -1e9, 1.0
            Rh[o + 16] = 1.0
            Rh[o + 20] = self.HW

    def _s_wrap(self, X):
        """(rows back, x shift, y shift) for screen x on the newest row: left of it, the rows above."""
        if self.SR <= 1:
            z = np.zeros_like(X)
            return z, z, z
        W = self.tape_w
        j = np.clip(np.ceil((self.X_left - X) / W), 0, self.SR - 1)
        return j, j * W, -j * self.pitch

    # -- render ---------------------------------------------------------------------------------------------------
    def _segment(self):
        seg = getattr(getattr(self, "sound", None), "segment", None)
        try:
            k, n = int(seg[0]), int(seg[1])
        except (TypeError, ValueError, IndexError):
            return None
        return (min(max(k, 0), n - 1), n) if n > 1 else None

    def render(self, t_local: float, dt: float, out) -> None:
        if self.span_room:
            want = self._segment()
            if (want is None) != (self.span is None) or (want is not None and want[1] != self.span[1]):
                self._seg_cfg = want                        # (spanning starts or stops: the layout set up again)
                self.close()
                self.setup(self._ctx0, self._params0)
            elif want is not None and want != self.span:
                self.span = want
        p = self.step_params(dt)
        self._params = p
        dt = min(max(dt, 0.0), 0.1)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        self.clock += dt
        self.breath += 0.25 * spd * dt
        self.drift += float(p["drift_speed"]) * spd * dt
        a = 1.0 - math.exp(-dt / 0.15) if dt > 0 else 1.0
        self.kick += (pulse - self.kick) * a
        Ph = self.Ph
        Ph[0:12] = palette_coeffs(p["palette"]) if p.get("palette") else palette_coeffs("ember")
        c = Ph[0:12].astype(np.float64)
        self._pal = (c[0:3], c[3:6], c[6:9], c[9:12])
        self.pump_until = time.perf_counter() + PUMP_S
        self._placed_n = 0
        self._listen()
        self._take_renders()
        for ph in [self.ph] + self.old:
            if ph.lift_t is None:
                self._pump(ph)
                self._drain(ph)
        self._settle_step()
        glow_s = max(0.05, float(p["ink_glow"]))
        if self.scroll:
            self._scroll_step(dt)
        self._lines_step(dt, glow_s, e)
        self._canvas_arrays()
        self._gates_step(dt)
        if self.relayout_due:
            self._epoch(self.builder)
        if self.scroll:                                     # (a phrase's time signature shows while on the tape)
            back = (self.SR - 1) * self.tape_w
            self.old = [q for q in self.old if not (q.done and (q.U0 is None or q.U0 + self.offx[0] + back
                                                                < self.X_left - 10.0 * self.sp))]
        else:
            self.old = [q for q in self.old if not (q.done and not any(ln in self.lines for ln in q.lines.values()))]
        nrec = self._notes_step(p, e)
        self.nrec = nrec
        Ph[12], Ph[13] = self.w, self.h
        Ph[14] = LOOKS.index(p["look"])
        Ph[15] = self.breath % (2.0 * math.pi)
        Ph[18] = float(p["intensity"])
        Ph[27] = self.ch
        Ph[28] = self.sp
        Ph[29] = self.th
        Ph[30:33] = _pal3(Ph[0:12], self.drift % 10.0 + 0.1)
        Ph[33] = self.NR
        self._canvas_params(e)
        self._upload()
        launch_2d(self.k, self.w, self.h, (self.buf, self.P, self.canvas, self.R, self.RI, self.page, self.GN,
                                           self.GU, self.gatlas, np.int32(self.gatlas.shape[1]), self.gtable))
        present(self.ctx, self.buf, out)

    def _canvas_params(self, e: float) -> None:
        Rh = self.Rh
        Rh[:] = 0.0
        sp = self.sp
        for ln in self.lines:
            o = RS * ln.ci
            vis = ln.alpha * (ln.show_a if ln.shown else 0.0)
            Rh[o + 0] = self.offx[ln.ci]
            Rh[o + 1] = self.offy[ln.ci]
            Rh[o + 2] = vis
            Rh[o + 3] = ln.front if self.engraver is not None else -1e9
            Rh[o + 4] = self.FE
            Rh[o + 5] = ln.glow
            Rh[o + 6] = ln.glow_len
            Rh[o + 7] = ln.hdr_w if ln.header else 0.0
            Rh[o + 8] = ln.hdr_a
            Rh[o + 9] = ln.staff_x0 if not ln.placeholder else ln.staff_x0
            Rh[o + 10] = ln.open_to
            Rh[o + 11] = ln.open_a * ln.weight
            Rh[o + 12], Rh[o + 13] = ln.t1, ln.t2
            Rh[o + 14] = ln.erase_x
            Rh[o + 15] = ERASE_FEATHER_SP * sp
            Rh[o + 16] = ln.ink_a
            Rh[o + 20] = self.cwid[ln.ci]
        if self.scroll:
            self._s_canvas_params(Rh, sp)

    def _upload(self) -> None:
        cp = self.ctx.cp
        k = self.up_k = 1 - self.up_k
        ev = self.up_ev[k]
        if ev is not None and not ev.done:
            ev.synchronize()
        h = self.up_host[k]
        h[0:64] = self.Ph
        h[self.UP_R:self.UP_R + self.NR * RS] = self.Rh
        h[self.UP_RI:self.UP_RI + self.NR].view(np.int32)[:] = self.RIh
        if self.gpu_recs is not None:
            self._pack(h)
        else:
            n = int(self.GUh[0])
            if n:
                h[self.UP_GN:self.UP_GN + GL.NF * n] = self.GNh[:n].ravel()
            h[self.UP_GU:self.UP_GU + GL.NU] = self.GUh
        st = cp.cuda.get_current_stream()
        self.up_dev.set(h, stream=st)
        if ev is None:
            ev = self.up_ev[k] = cp.cuda.Event(block=False, disable_timing=True)
        ev.record(st)
        if self.gpu_recs is not None:
            n, m = self.gpu_recs[1], self.gpu_recs[2]
            F, IX, XR, L, Q = self._rv
            self.krec((1,), (512,), (F, IX, np.int32(n), XR, np.int32(m), L, np.int32(self.NC), Q, self.adv_dev,
                                     self.rec_scratch, self.GN, self.GU))

    def _pack(self, h) -> None:
        """The note rows' fields for recs (times relative to now: float32 can't hold the clock), the drawn rows,
        the other records, the canvases' offsets / alpha / erase fronts and the frame's scalars."""
        idx, n, m, p, e = self.gpu_recs
        N = self.N
        now = self.t_sound
        F = h[self.UP_F:self.UP_IX].reshape(NFLD, NCAP)
        sub = np.subtract
        sub(now, N.h_t0, out=F[0])
        F[1] = N.h_T
        F[2] = N.arc
        F[3] = N.tx
        F[4] = N.ty
        F[5:17] = N.C.reshape(12, NCAP)
        sub(now, N.t_born, out=F[17])
        sub(now, N.hand_t0, out=F[18])
        sub(now, N.die_t0, out=F[19])
        sub(now, N.g_t0, out=F[20])
        F[21] = N.ga
        F[22] = N.gb
        sub(now, N.s_t0, out=F[23])
        F[24] = N.s_T
        F[25] = N.sa
        F[26] = N.sb
        F[27] = N.s_flip
        F[28:34] = N.S.reshape(6, NCAP)
        sub(now, N.a_t0, out=F[34])
        F[35] = N.aa
        F[36] = N.ab
        F[37] = N.adxa
        F[38] = N.adxb
        F[39] = N.st_top
        F[40] = N.pitch
        sub(now, N.t_off, out=F[41])
        F[42] = N.lid
        if n:
            h[self.UP_IX:self.UP_IX + n] = idx
        if m:
            h[self.UP_X:self.UP_X + GL.NF * m] = self.recs[n:n + m].ravel()
        NC = self.NC
        Lh = h[self.UP_L:self.UP_Q]
        Lh[0:NC] = self.offx
        Lh[NC:2 * NC] = self.offy
        Lh[2 * NC:3 * NC] = self._c_alpha
        np.maximum(self._c_erase, -1e30, out=Lh[3 * NC:4 * NC])
        Q = h[self.UP_Q:self.UP_N]
        Q[:] = 0.0
        Q[0] = self.sp
        Q[1] = POP * self.flourish
        if self.scroll:
            Q[2], Q[3], Q[4], Q[5], Q[6] = 1.0, self.SR, self.X_fade, self.tape_w, self.pitch
        Q[7] = float(p["palette_period"]) / 12.0
        Q[8] = self.drift % 10.0
        Q[9] = float(p["note_glow"]) * (0.8 + 0.6 * e)
        Q[10] = 1.0 + 0.6 * self.kick
        Q[11] = HALO_SP
        pc = self._pal
        Q[12:15], Q[15:18], Q[18:21], Q[21:24] = pc[0], pc[1], pc[2], pc[3]

    def close(self) -> None:
        self.ink_stop = True
        t = getattr(self, "ink_thread", None)
        if t is not None:
            t.join(timeout=1.0)
        if self.engraver is not None:
            self.engraver.close()
            self.engraver = None
        room, self.room = getattr(self, "room", None), None
        if room is not None:
            room.leave(self)
        self.buf = self.canvas = self.P = self.R = self.RI = self.page = None
        self.up_dev = self.up_host = self._up_pin = self.up_ev = None
        self.GN = self.GU = self.gatlas = self.gtable = None
        self.slots = self._pinned = self.slot_fly = None
