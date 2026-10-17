"""Live score: the piano's playing written out as an engraved score while it is played.

Source: Original.  Engraving by Verovio (https://www.verovio.org, LGPL-3.0-or-later, used as an unmodified library
        in a separate worker process, nothing ported) rasterised by resvg (resvg-py, MPL-2.0 / Apache-2.0); the
        Leipzig music font (SIL OFL 1.1).  Prior art for the score side: PM2S (MIT, cheriell/PM2S) is the planned
        upgrade for beats, metre, key and hands: its networks (MIT, ported in stream_service/score/pm2s_net.py with
        the authors' weights) run in the analysis worker, with our metre model (score/metre.py) on top.
Author: smfx
License: MIT (this file)
license_class: permissive

Listens to the piano (``listens = ("notes",)``, CONVENTIONS §2b).  stream_service/score does the music: the analysis
worker (score/analyse.py, its own venv and process) finds the beats, the metre (2/4 3/4 4/4 6/8 9/8 12/8 and where
the bar lines fall), the key signature and each note's hand from the last 24 s of playing; the quantiser writes
bars on those beats (16ths, swing read straight) once they're a little behind the newest notes.  A phrase's first
bar waits for the metre (6-12 s); without the worker it writes 4/4 on a beat clock of its own, hands split at
middle C.  Each bar is engraved on its own (no clefs or signatures, mei.bar_doc) and each
line's start once (brace, clefs, key, and the time signature when a phrase starts: mei.header_doc), all at one
fixed staff size, by a worker process (Verovio -> SVG -> resvg), so the render thread never waits.

On screen there are two layouts.

``page`` (the default) is like a page being written: lines of grand staff, the one being played at the bottom, the
one being inked above it, and the one before that dimmed at the top.  A line's bars are laid out before they're
played: as many equal slots as the music's recent bar widths allow, each bar engraved justified to its slot
(``fit_px``; never squeezed below 0.85 of its natural width, and a bar that still doesn't fit moves to the next
line).  So a note glows where it will be written from the moment it sounds, and the page turns (the whole stack in
one minimum-jerk glide) a second before the playing reaches the next line.  Engraved ink is composited once and
never moves: a pen uncovers it left to right at the music's pace (faster while bars arrive in a burst), the fresh
ink glowing behind it for ``ink_glow`` seconds, and each glowing note sinks into its ink as the pen reaches it.
Ahead of the pen the staff is open, with faint bar lines where the next bars will go.  A line's header (brace,
clefs, key, and the time signature when a phrase starts) fades in once it's engraved.  Before a phrase's metre is
known its notes drift right to left on its first line, then settle into their bars.  A pause of four seconds ends
a phrase (the next one starts a line); a phrase whose metre turns is un-written (the pen runs back over it) and
written again in place.

``scroll`` is one grand staff moving right to left, the paper following the pen.  A sticky header on the left shows
the clefs, key and time in force there; key and time changes are written inline and a phrase ends on a final bar
line.  Between phrases the staff drifts slowly on.  The notes being played glow ahead of the pen, drawn closer
together when the writing lags (they meet their ink at the pen's speed).

Motion follows musical time, never the pipeline: quintic glides re-planned from where things are, critically damped
springs for notes (a move over 8 staff spaces, or to another line, crossfades instead), slewed opacities, and a
rate-limited scroll that never runs backwards.  Degrade, never refuse: bars too short to write in time (a fast
reading of the metre) keep the line being inked on the page and the pen passes lines that have left; a part that
doesn't come is written as an empty bar.  Bounds: NROW lines, MAX_LIVE notes (x-sorted, a 7-step search), NGHOST
ghost bar lines, one launch a frame.  Looks: paper (warm paper, dark ink, baked once) and night (gold on
blue-black).

Macros: energy -> note glow size and the fresh ink's glow; pulse (bind note_on) -> a glow kick; speed -> the page's
slow breathing; intensity -> output gain; palette / period / drift colour the glowing notes (around the circle of
fifths).  Without the engraving libraries it still shows the live notes on drawn staves.
"""
from __future__ import annotations

import collections
import logging
import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import palette_coeffs
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source
from stream_service.score.motion import Glide, LinePlan, Pen, Ruler, lane_map, ring_slices, smoothstep, spring_step

log = logging.getLogger("stream_service.plugins.live_score")

LOOKS = ("paper", "night")
LAYOUTS = ("page", "scroll")
ROW_SP = 24                    # a line's canvas height in staff spaces
TOP_SP = 6                     # the treble staff's top line, in staff spaces from the canvas top
RESULTS_PER_FRAME = 3
# page
NROW = 5                       # lines kept: entering, playing, inking, older, leaving
TURN_S, TURN_EXTRA_S, TURN_LEAD_S = 1.6, 0.5, 1.0       # a turn's glide (+ per extra row), started this early
WANT, SQUEEZE, N_MIN, N_MAX, PAD_R_SP = 1.15, 0.85, 2, 8, 1.0
NAT_BEAT_SP, NAT_DOTTED_SP = 5.0, 6.5                   # a beat's natural width until the engraver says
HDR_WAIT_S, PART_TIMEOUT_S, PARK_MAX = 1.5, 5.0, 8
FEATHER_SP, OPEN_FADE_S, HDR_FADE_S, KILL_S, PHRASE_HOLD_S = 1.0, 0.8, 0.3, 0.4, 0.6
OPEN_FADE_SPS = 20.0           # a long open staff fades no faster than this many staff spaces a second
PEN_VMAX_SPS = 42.0            # the pen's fastest (staff spaces a second: under 2 a frame) ...
PEN_AMAX_SPS = 80.0            # ... and its hardest acceleration or braking (staff spaces a second squared)
PEN_W = 12.0                   # the pen's x follows its bars through a stiff spring (a wider bar's pace eases in)
GHOST_A, GHOST_TH = 0.22, 0.6
# notes
MAX_LIVE, MAX_GLOWS, CAP_HEADROOM = 64, 96, 8
NOTE_W, NOTE_PRE_S, TELEPORT_SP, GHOST_OUT_S, FADE_IN_S = 6.0, 0.1, 8.0, 0.2, 0.25
RETIRE_S, RETIRE_AHEAD_SP, ORPHAN_S = 0.3, 0.5, 8.0
SLEW_UP, SLEW_DN, REL_FLOOR, REL_TAU = 6.0, 3.0, 0.32, 0.6
BLOOM, RC_SP, LPAD_SP, RPAD_SP = 1.3, 3.0, 1.6, 1.2
CONVEYOR_W, CONVEYOR_AT = 2.5, 0.88
# scroll
SP_SCROLL, X_HDR, HW_MAX_SP, SHIELD_SP = 0.0135, 0.03, 22, 2.0
PEN_X, LANE_X, ENTRY_X = 0.55, 0.90, 0.50        # the pen's place, the lane's end, a phrase's entry (of the tape)
V_CAP_SP, W_RATE = 16.0, (0.8, 1.25)              # a bar's slot: no wider than this speed allows, near the last
S_OMEGA, S_AMAX_SP, V_IDLE_SP, VFF_TAU, D_TAU = 0.9, 10.0, 0.6, 0.5, 0.3
V_PRE = 0.7                    # before the metre the paper moves at this much of the provisional pace ...
V_FLOOR = 0.35                 # ... and while a phrase plays at least this much of its recent pace
A_DT = 0.04                    # velocities change by at most a frame's worth of acceleration (a late frame no more)
PHRASE_GAP_SP, HDR_XFADE_S, NGHOST = 3.0, 0.5, 8
LANE_W = 4.0                   # the lane's start follows the current phrase's pen through a spring (a new phrase or a
                               # rewrite moves it on smoothly)

_SRC = HELPERS + r"""
#define NROW """ + str(NROW) + r"""
#define MAX_LIVE """ + str(MAX_LIVE) + r"""
#define NGHOST """ + str(NGHOST) + r"""
#define GHOST_TH """ + f"{GHOST_TH}f" + r"""

// the page's grain, fibre and vignette, baked once in setup
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
// five staff lines from top (and the next five from top2), weight 0.85 on a line
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

// the notes being played now: L is x-sorted, 8 floats each (x y rx ry intensity r g b); P[33] the reach
__device__ __forceinline__ void live_notes(float3& c, const float X, const float Y, const float* __restrict__ L,
                                           const int nl, const float* __restrict__ P, const int night)
{
    const int n = min(nl, MAX_LIVE);
    if (n <= 0 || X <= P[22] || X >= P[24] || Y <= P[23] || Y >= P[25]) return;
    const float rc = P[33];
    int lo = 0, hi = n;
    for (int s = 0; s < 7 && lo < hi; ++s) {                // the first note with x >= X - rc
        const int mid = (lo + hi) >> 1;
        if (L[8 * mid] < X - rc) lo = mid + 1; else hi = mid;
    }
    const float e = P[16], kick = P[17];
    const float cs = 0.93937271f, sn = -0.34289781f;        // cos, sin of -0.35
    const float halo = P[19] * (0.8f + 0.6f * e) * (1.f + 0.5f * kick);
    const float hk = 2.2f / fmaxf(halo, 0.05f);
    for (int i = lo; i < n && L[8 * i] <= X + rc; ++i) {
        const float* N = L + 8 * i;
        const float dx = X - N[0], dy = Y - N[1];
        const float rr = sqrtf(dx * dx + dy * dy);
        if (rr >= rc) continue;
        const float ex = (dx * cs + dy * sn) / N[2], ey = (-dx * sn + dy * cs) / N[3];
        const float d = sqrtf(ex * ex + ey * ey);
        const float core = 1.f - smooth(0.82f, 1.05f, d);
        const float glow = expf(-fmaxf(d - 1.f, 0.f) * hk) * 0.7f * halo;
        const float kk = N[4] * fminf(1.f, core + glow) * (1.f - smooth(0.7f * rc, rc, rr));
        if (!night) { c.x += (N[5] * 0.85f - c.x) * kk; c.y += (N[6] * 0.85f - c.y) * kk;
                      c.z += (N[7] * 0.85f - c.z) * kk; }
        else { c.x += N[5] * kk; c.y += N[6] * kk; c.z += N[7] * kk; }
    }
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

// P: [0..11] palette [12]W [13]H [14]look [15]breath [16]energy [17]kick [18]gain [19]note glow [20]drift
//    [21]period [22..25] live bbox [26] canvas w [27] canvas h [28] staff space [29] line thickness
//    [30..32] fresh ink colour [33] the notes' reach
// R (16 per line): [0] x0 [1] y0 [2] alpha [3] pen x [4] feather [5] glow amp [6] glow length [7] header x1
//    [8] header alpha [9] open staff to [10] its alpha [11] t1 [12] t2 [13] ghost phase [14] ghost step [15] ghost a
// RI: canvas offset.  L: the live notes (see live_notes)
extern "C" __global__
void score(uchar4* __restrict__ out, const float* __restrict__ P, const unsigned char* __restrict__ canvas,
           const float* __restrict__ R, const int* __restrict__ RI, const float* __restrict__ L, const int nl,
           const unsigned char* __restrict__ page)
{
    const int W = (int)P[12], H = (int)P[13];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float X = x + 0.5f, Y = y + 0.5f;
    const int night = (int)P[14];
    float3 inkc;
    float3 c = background(page, P, x, y, W, Y / H, night, inkc);
    const int cw = (int)P[26], ch = (int)P[27];
    const float sp = P[28], th = P[29];
    const float3 fresh_c = make_float3(P[30], P[31], P[32]);
    for (int r = 0; r < NROW; ++r) {
        const float* Q = R + 16 * r;
        const float a = Q[2];
        if (a <= 0.001f) continue;
        const float cx = X - Q[0], cy = Y - Q[1];
        if (cx < 0.f || cy < 0.f || cx >= cw || cy >= ch) continue;
        const int ix = (int)cx;
        const float fy = cy - 0.5f;                          // 2-tap vertical: crisp at rest (integer y0)
        const int iy = (int)floorf(fy);
        const float ty = fy - iy;
        const unsigned char* C = canvas + RI[r];
        const int ya = max(iy, 0), yb = min(iy + 1, ch - 1);
        const float ink = (C[ya * cw + ix] * (1.f - ty) + C[yb * cw + ix] * ty) * (1.f / 255.f);
        const float pen = Q[3];
        const float rev = cx < Q[7] ? Q[8] : clamp01((pen - cx) / Q[4] + 0.5f);
        const float inkv = ink * rev;
        const float sw = cx < Q[7] ? 1.f - Q[8] : (cx < Q[9] ? Q[10] * (1.f - rev) : 0.f);
        const float staff = sw > 0.f ? sw * staff_lines(cy, Q[11], Q[12], sp, th) : 0.f;
        float ghost = 0.f;
        if (Q[14] > 0.f && cx >= pen && cx < Q[9] && cx >= Q[13] + 0.5f * Q[14] && cy >= Q[11] - th
            && cy <= Q[12] + 4.f * sp + th) {
            float u = fmodf(cx - Q[13], Q[14]);
            if (u < 0.f) u += Q[14];
            const float d = fminf(u, Q[14] - u);
            ghost = Q[15] * (1.f - rev) * clamp01(0.5f * GHOST_TH * th + 0.5f - d);
        }
        const float cov = fmaxf(inkv, fmaxf(staff, ghost)) * a;
        blend_ink(c, inkc, cov);
        if (cx < pen && inkv > 0.004f && Q[5] > 0.f)             // the fresh ink glowing behind the pen
            blend_glow(c, fresh_c, inkv * a * Q[5] * expf(-(pen - cx) / Q[6]), night);
    }
    live_notes(c, X, Y, L, nl, P, night);
    const float g = 255.f * P[18];
    out[y * W + x] = pack(make_float4(clamp01(c.x) * g, clamp01(c.y) * g, clamp01(c.z) * g, 255.f));
}

// scroll: one grand staff on a ring of TW columns moving right to left, a sticky header on the left.
// P as for score, plus [36] TW [37] the ring column at screen x 0 [38] its fraction [39] y0 [40] X_left
//    [41] shield [42] pen x [43] feather [44] glow amp [45] glow length [46] t1 [47] t2 [48] header x0
//    [49] A alpha [50] A width [51] B alpha [52] B width [53] header slot stride [54..56] dissolve x0 x1 alpha
//    [57] ghost bar lines.  G: NGHOST x (screen x, alpha).  colok: a ring column holds this lap's ink
extern "C" __global__
void score_scroll(uchar4* __restrict__ out, const float* __restrict__ P, const unsigned char* __restrict__ tape,
                  const unsigned char* __restrict__ colok, const unsigned char* __restrict__ hdr,
                  const float* __restrict__ G, const float* __restrict__ L, const int nl,
                  const unsigned char* __restrict__ page)
{
    const int W = (int)P[12], H = (int)P[13];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float X = x + 0.5f, Y = y + 0.5f;
    const int night = (int)P[14];
    float3 inkc;
    float3 c = background(page, P, x, y, W, Y / H, night, inkc);
    const int ch = (int)P[27], y0 = (int)P[39];
    if (y >= y0 && y < y0 + ch) {
        const int iy = y - y0;
        const float cy = iy + 0.5f;
        const float sp = P[28], th = P[29], t1 = P[46], t2 = P[47];
        const float XL = P[40];
        float cov, inkv = 0.f;
        if (X < XL) {                                       // the sticky header
            const float hx = X - P[48];
            cov = 0.f;
            if (hx >= 0.f) {
                const int HW = (int)P[53], ix = (int)hx;
                const float inA = hx < P[50] ? 1.f : 0.f, inB = hx < P[52] ? 1.f : 0.f;
                float hc = 0.f;
                if (ix < HW)
                    hc = fmaxf(P[49] * inA * hdr[iy * HW + ix], P[51] * inB * hdr[(ch + iy) * HW + ix]) * (1.f / 255.f);
                const float sw = clamp01(1.f - P[49] * inA - P[51] * inB);
                cov = fmaxf(hc, sw * staff_lines(cy, t1, t2, sp, th));
            }
        } else {
            const int TW = (int)P[36];
            const int c0 = ((int)P[37] + x) % TW, c1 = (c0 + 1) % TW;
            const float fr = P[38];
            const float ok0 = colok[c0], ok1 = colok[c1];
            const float i0 = tape[iy * TW + c0] * ok0, i1 = tape[iy * TW + c1] * ok1;
            const float ink = (i0 + (i1 - i0) * fr) * (1.f / 255.f);
            const float okf = ok0 + (ok1 - ok0) * fr;
            const float pen = P[42];
            const float rev = clamp01((pen - X) / P[43] + 0.5f);
            const float m = smooth(XL, XL + P[41], X);
            const float dis = (X >= P[54] && X < P[55]) ? P[56] : 1.f;
            const float vis = rev * m * dis;
            inkv = ink * vis;
            const float staff = (1.f - okf * vis) * staff_lines(cy, t1, t2, sp, th);
            float ghost = 0.f;
            if (cy >= t1 - th && cy <= t2 + 4.f * sp + th) {
                const int ng = min((int)P[57], NGHOST);
                for (int k = 0; k < ng; ++k)
                    ghost = fmaxf(ghost, G[2 * k + 1] * clamp01(0.5f * GHOST_TH * th + 0.5f - fabsf(X - G[2 * k])));
            }
            cov = fmaxf(inkv, fmaxf(staff, ghost));
            blend_ink(c, inkc, cov);
            if (X < pen && inkv > 0.004f && P[44] > 0.f)
                blend_glow(c, make_float3(P[30], P[31], P[32]), inkv * P[44] * expf(-(pen - X) / P[45]), night);
            cov = 0.f;
        }
        blend_ink(c, inkc, cov);
    }
    live_notes(c, X, Y, L, nl, P, night);
    const float g = 255.f * P[18];
    out[y * W + x] = pack(make_float4(clamp01(c.x) * g, clamp01(c.y) * g, clamp01(c.z) * g, 255.f));
}
"""


def _own(render):
    """A render kept for later (it waits for an earlier bar): its own copy, as the staging area is reused."""
    img, m = render
    return img.copy(), m


def _pal3(c, t):
    """The palette at t (numpy, like the kernels' pal): (..., 3) in 0..1."""
    t = np.asarray(t, np.float64)[..., None]
    c = np.asarray(c, np.float64)
    return np.clip(c[0:3] + c[3:6] * np.cos(2.0 * math.pi * (c[6:9] * t + c[9:12])), 0.0, 1.0)


def _row_alpha(ro: float) -> float:
    """A line's opacity by its row offset from the page's position (0 the bottom line, -1 above it ...)."""
    if ro >= 0.0:
        return 1.0 - smoothstep(0.3, 0.95, ro)
    if ro >= -1.0:
        return 1.0
    if ro >= -2.0:
        return 1.0 - 0.4 * (-1.0 - ro)
    return 0.6 * (1.0 - smoothstep(2.0, 2.6, -ro))


EMPTY, SKIP, VIRTUAL = "empty", "skip", "virtual"


class _Line:
    """A line of grand staff on its canvas slot (page), or the tape (scroll)."""
    _ids = 0

    def __init__(self, slot: int, sp: float, t2_off: float | None = None) -> None:
        self.slot = slot
        self.reset(sp, t2_off)

    def reset(self, sp: float, t2_off: float | None = None, keep: bool = False) -> None:
        """A fresh line (``keep``: the same id and place on the page: a rewritten line)."""
        if not keep:
            _Line._ids += 1
            self.id = _Line._ids
            self.row = 0
            self.y = None
            self.alpha = 0.0
            self.y_target = 0.0
            self.wpen = 0.0
            self.hreq = 0
            self.glow = 0.0
            self.fresh = (0.0, 0.0, 0.0)
        self.phrase = None
        self.j = 0
        self.fifths = 0
        self.meter = False
        self.sig = (4, 4)
        self.hdr_key = None
        self.header = False                         # the engraved header is on the canvas
        self.hdr_t = None                           # asked for at
        self.hdr_x1 = 0.0                           # the header's width (estimated until it lands) ...
        self.hdr_xd = None                          # ... as drawn (eased: a wider header doesn't jump the staff)
        self.hdr_a = 0.0
        self.hdr_fade = False
        self.sent = 0                               # bars asked for
        self.cursor = 0.0
        self.widths: list[float] = []
        self.pending: list = []
        self.full = False
        self.placed: dict = {}                      # phrase bar -> (x, width)
        self.heads: dict = {}                       # note id -> engraved head (canvas x, y)
        self.t1 = TOP_SP * sp
        self.t2 = self.t1 + (t2_off if t2_off is not None else 10 * sp)
        self.x = None
        self.pen_x = -1e9
        self.pen_v = 0.0
        self.pen_entry = False
        self.open_to = 0.0
        self.open_a = 1.0
        self.open_fade_s = None                     # closed short: its open staff fading over this long
        self.kill = 1.0
        self.dying = None                           # being un-written since (a rewrite)


class _Phrase:
    """A phrase: its pen and bars in flight, and its place: (page) its line plan and the canvas lines it has,
    (scroll) its ruler along the tape."""

    def __init__(self) -> None:
        self.plan = LinePlan()
        self.pen = Pen()
        self.lines: dict[int, _Line] = {}           # plan line -> its canvas line
        self.metre = None                           # (beats, unit) once decided
        self.sig = (4, 4)
        self.fifths = 0
        self.nat = NAT_BEAT_SP                      # a beat's natural width (sp, EMA of the engraver's)
        self.bar_s = None
        self.t0 = None                              # first note (sound clock)
        self.conv, self.conv_v = 0.0, 0.0           # before the metre: the notes' drift on the first line
        self.hdr_est = 0.0
        self.R_prov = 0.0
        self.committed = 0                          # bars committed (phrase bars 0 ..)
        self.t_commit: dict[int, float] = {}
        self.wait: list = []                        # (k, bar) to ask for, in order
        self.sub: dict[str, int] = {}               # engraver key -> phrase bar
        self.t_sub: dict[int, float] = {}
        self.items: dict = {}                       # phrase bar -> render / EMPTY / SKIP / VIRTUAL, to place in order
        self.next_place = 0                         # == F: bars resolved from 0
        self.turned = 0
        self.pen_j = 0
        self.turn_wait = False                      # (a new phrase) its first line waits to turn in
        self.ended = False
        self.t_done = None                          # the pen reached the end at
        self.queued: list = []                      # a rewrite's bars, until the old lines have dissolved
        self.killing: list = []
        # scroll
        self.ruler = None                           # tape positions (from the phrase's first note)
        self.U_p = 0.0
        self.k_base = 0                             # (after a rewrite) the phrase bar written first
        self.seq = 0
        self.heads: dict = {}                       # note id -> engraved head (tape u, y)
        self.placed: dict = {}                      # bar -> (tape u, width)
        self.bar_fifths: dict = {}
        self.bar_t: dict = {}                       # bar -> (t_start, t_end)
        self.tape_fifths = 0
        self.nfrozen = 0
        self.ins_sub: dict = {}                     # a key change before bar k: engraver key -> k
        self.ins_wait: dict = {}                    # k -> asked at
        self.ins_items: dict = {}
        self.ins_info: dict = {}                    # k -> its fifths
        self.pen_u = 0.0
        self.pen_u_prev = None
        self.pen_ur, self.pen_uv = None, 0.0        # the pen's raw tape u, and pen_u's spring speed
        self.v_music = None                         # the pen's recent pace (px/s)
        self.final = False

    @property
    def beats(self) -> int:
        return self.metre[0] if self.metre else 4

    @property
    def done(self) -> bool:
        """Ended and written out (the pen's last stretch is an exponential approach: within 0.02 bar is done)."""
        return self.ended and self.pen.p >= self.committed - 0.02 and not self.wait and not self.items


class _Glow:
    __slots__ = ("gid", "note", "phrase", "staff", "home", "k", "frac", "pre", "x", "vx", "y", "vy", "gx", "gy",
                 "inten", "tgt", "presence", "bloom", "state", "t_state", "dirty", "born", "fade", "shrink", "edge")

    def __init__(self, gid: int, note, phrase, staff: int, born: float) -> None:
        self.gid, self.note, self.phrase, self.staff, self.born = gid, note, phrase, staff, born
        self.home = None
        self.k, self.frac, self.pre = None, 0.0, True
        self.x = self.vx = self.y = self.vy = self.gx = self.gy = 0.0
        self.inten = 0.0
        self.tgt = 0.0
        self.presence = 1.0
        self.bloom = BLOOM
        self.state = "live"
        self.t_state = born
        self.dirty = True
        self.fade = 0.0                             # > 0: fading out over that many seconds (cap, orphan, no line)
        self.shrink = 1.0
        self.edge = 1.0                             # before the metre: faded at the line's left edge (slewed)


class LiveScore(Source):
    name = "live_score"
    label = "Live score"
    description = "The piano's playing written out as an engraved score while it is played, notes glowing as they sound."
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette="ember", render_scale=1.0, period=1.0, drift=0.01),
            "layout": enum(LAYOUTS, "page", "Layout", tier="structural", basic=True),
            "look": enum(LOOKS, "paper", "Look", tier="structural", basic=True),
            "staff_size": num(0.6, 1.5, 1.0, "Staff size", tier="structural"),
            "note_glow": num(0.0, 1.5, 0.8, "Note glow", basic=True),
            "ink_glow": num(0.0, 3.0, 1.2, "Fresh ink glow", unit="s"),
        }

    # -- setup ----------------------------------------------------------------------------------------------------
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.layout = params.get("layout", "page") if params.get("layout") in LAYOUTS else "page"
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        mod = raw_module(_SRC)
        scroll = self.layout == "scroll"
        self.k = mod.get_function("score_scroll" if scroll else "score")
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        size = float(params["staff_size"])
        self.sp = max(4.0, round((SP_SCROLL if scroll else 0.013) * self.h * size, 1))     # staff space (px)
        sp = self.sp
        self.cw = max(64, int(self.w * 0.94))                                          # a line's canvas
        self.ch = int(ROW_SP * sp)
        self.x_off = 0 if scroll else int(0.5 * (self.w - self.cw))
        self.y_bot = int(round(0.97 * self.h - self.ch))
        self.pitch = int(round(self.ch + 0.01 * self.h))
        self.th = max(1.0, 0.08 * sp)
        self.canvas = None if scroll else cp.zeros(NROW * self.ch * self.cw, cp.uint8)
        self.page = cp.zeros((self.h, self.w), cp.uint8)
        launch_2d(mod.get_function("bake_page"), self.w, self.h,
                  (self.page, np.int32(self.w), np.int32(self.h), np.int32(LOOKS.index(params["look"])),
                   np.uint32(self.seed)))
        # engraved parts come through pinned memory to a GPU staging area, then into their line's canvas
        self.stage_bytes = RESULTS_PER_FRAME * self.ch * self.cw
        self._pinned = cp.cuda.alloc_pinned_memory(self.stage_bytes)
        self.stage = np.frombuffer(self._pinned, np.uint8, self.stage_bytes)
        self.gstage = cp.zeros(self.stage_bytes, cp.uint8)
        self._stage_done = None                 # the last uploads' event: pinned memory is reused only after it
        self.P = cp.zeros(64, cp.float32)
        self.Ph = np.zeros(64, np.float32)
        self.R = cp.zeros(NROW * 16, cp.float32)
        self.Rh = np.zeros(NROW * 16, np.float32)
        self.RI = cp.zeros(NROW, cp.int32)
        self.RIh = np.zeros(NROW, np.int32)
        self.L = cp.zeros(MAX_LIVE * 8, cp.float32)
        self.Lh = np.zeros(MAX_LIVE * 8, np.float32)
        from stream_service.score.quantize import ScoreBuilder
        self.builder = ScoreBuilder()
        self.analyser = None
        try:
            from stream_service.score.client import Analyser
            if Analyser.available():
                self.analyser = Analyser()
                self.builder.use_analysis(self.analyser.ok)
            else:
                log.warning("live_score: no analysis venv or PM2S weights; writing 4/4 on the beat clock")
        except Exception as e:
            log.warning("live_score: no analysis (%s); writing 4/4 on the beat clock", e)
        self.engraver = None
        try:
            import verovio  # noqa: F401  (the worker imports them; this only checks they're installed)
            import resvg_py  # noqa: F401
            from stream_service.score.client import Engraver
            self.engraver = Engraver(self.cw, space_px=sp)
        except Exception as e:
            log.warning("live_score: no engraving (%s); drawing the staves instead", e)
        self.t_sound = 0.0
        self.clock = 0.0
        self.breath = 0.0
        self.drift = 0.0
        self.kick = 0.0
        self.bbox = (0.0, 0.0, 0.0, 0.0)
        self.hdr_cache: dict = {}               # (fifths, meter, sig) -> the header's measured width (px)
        self.t2_off = None                      # the bass staff's top below the treble's (from a header)
        self.lpads = collections.deque([LPAD_SP * sp], maxlen=8)
        self.rpads = collections.deque([RPAD_SP * sp], maxlen=8)
        self.lpad, self.rpad = LPAD_SP * sp, RPAD_SP * sp
        self.nat_seed: dict = {}                # unit -> the last phrase's natural beat width (sp)
        self.R_last = None                      # the last phrase's px/s
        self.spells: dict = {}
        self.glows: list[_Glow] = []
        self.gid = 0
        self.last_nid = 0
        self.spills = 0
        self.bars_empty = 0
        self.live_debug: list = []
        self.lines: list[_Line] = []
        self.turn = Glide(-1.0)                 # the page's position, in rows (the first line glides in)
        self.page_row = 0.0
        self.old: list[_Phrase] = []            # phrases whose pen is still finishing
        self.ph = _Phrase()
        self.pen = 0.0
        self.pen_backlog = 0.0
        self.pen_line_id = None
        if scroll:
            self._setup_scroll(cp)
        else:
            ln = self._alloc(0)
            self._attach_first(self.ph, ln)
            self.turn.to(0.0, TURN_S)

    # -- page: canvas lines ----------------------------------------------------------------------------------------
    def _hdr_est(self, fifths: int, meter: bool) -> float:
        """A header's width until one like it is engraved (measured: 3 sp of brace room, clefs and the space after
        them 10.8, a key signature 0.8 + 1 a sharp or flat, a time signature 2.6)."""
        return (13.8 + (0.8 + abs(fifths) if fifths else 0.0) + (2.6 if meter else 0.0)) * self.sp

    def _hdr_w(self, key) -> float:
        w = self.hdr_cache.get(key)
        return w if w is not None else self._hdr_est(key[0], key[1])

    def _alloc(self, row: int) -> _Line:
        """A canvas line at ``row`` (a free slot; else the least visible line gives up its slot)."""
        used = {ln.slot for ln in self.lines}
        free = [s for s in range(NROW) if s not in used]
        if not free:
            pens = self._pen_lines()
            cands = [ln for ln in self.lines if ln.id not in pens] or self.lines
            victim = min(cands, key=lambda ln: (ln.alpha, ln.row))
            self._drop_line(victim)
            free = [victim.slot]
        ln = _Line(free[0], self.sp, self.t2_off)
        off = ln.slot * self.ch * self.cw
        self.canvas[off:off + self.ch * self.cw].fill(0)
        ln.row = row
        ln.y = self.y_bot + (row - self.turn.x) * self.pitch
        self.lines.append(ln)
        self.lines.sort(key=lambda x: x.row)
        return ln

    def _drop_line(self, ln: _Line) -> None:
        if ln in self.lines:
            self.lines.remove(ln)
        ph = ln.phrase
        if ph is not None and ph.lines.get(ln.j) is ln:
            del ph.lines[ln.j]

    def _attach_first(self, ph: _Phrase, ln: _Line) -> None:
        """A phrase's first line before its metre: open staff all along, the header's room estimated."""
        fifths = self.builder.key()[2]
        ln.phrase, ln.j, ln.fifths, ln.meter = ph, 0, fifths, True
        ln.hdr_x1 = ph.hdr_est = self._hdr_est(fifths, True)
        ln.open_to, ln.open_a = float(self.cw), 1.0
        ph.lines[0] = ln
        ph.fifths = fifths
        ph.R_prov = self.R_last or 10.0 * self.sp

    def _pen_lines(self) -> set:
        """The lines the pens are on (they stay while inked)."""
        out = set()
        for ph in [self.ph] + self.old:
            if ph.metre is None or not ph.plan.lines or ph.done:
                continue
            ln = ph.lines.get(ph.plan.line_of(math.floor(ph.pen.p + 1e-9)))
            if ln is not None:
                out.add(ln.id)
        return out

    def _request_header(self, ln: _Line) -> None:
        ln.hdr_t = self.t_sound
        if self.engraver is None:
            return
        from stream_service.score import mei
        ln.hreq += 1                                    # a rewritten line ignores its old header's reply
        self.engraver.submit(f"h:{ln.id}:{ln.hreq}", mei.header_doc(ln.fifths, meter=ln.meter, sig=ln.sig),
                             margin_left=int(3 * self.sp))

    def _plan_line(self, ph: _Phrase) -> _Line | None:
        """Plan the phrase's next line (slots from its natural bar width) on a canvas line in the next row."""
        j_next = ph.plan.last + 1
        ln = ph.lines.get(j_next)
        fifths = self.builder.key()[2]
        meter = j_next == 0
        if meter:
            ph.fifths = fifths
        key = (fifths, meter, ph.sig if meter else None)
        hdr_w = self._hdr_w(key)
        nat_px = ph.nat * ph.beats * self.sp
        j = ph.plan.plan_next(self.cw, hdr_w, nat_px, PAD_R_SP * self.sp, WANT, N_MIN, N_MAX)
        if ln is None:
            ln = self._alloc((self.lines[-1].row + 1) if self.lines else int(round(self.turn.target)))
        ln.phrase, ln.j, ln.fifths, ln.meter, ln.sig, ln.hdr_key = ph, j, fifths, meter, ph.sig, key
        ln.hdr_x1 = hdr_w
        L = ph.plan.line(j)
        ln.open_to, ln.open_a = max(L.end, ln.open_to), 1.0
        ph.lines[j] = ln
        self._request_header(ln)
        return ln

    def _ensure_plan(self, ph: _Phrase, k: int) -> None:
        """Plan lines until phrase bar k has one."""
        guard = 0
        while ph.metre is not None and not ph.plan.covers(k) and guard < 8:
            self._plan_line(ph)
            guard += 1

    def _turn_to(self, row: float) -> None:
        if row <= self.turn.target + 1e-6:
            return
        rows = max(1, math.ceil(row - self.turn.x - 1e-6))
        self.turn.to(row, TURN_S + TURN_EXTRA_S * (rows - 1))

    # -- the music ---------------------------------------------------------------------------------------------
    def _listen(self):
        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        if f is None:
            return None
        t = float(f.t_ms) / 1000.0
        self.t_sound = t
        b = self.builder
        for p, vel, age in f.onsets:
            b.note_on(p, float(vel), t - age)
        for p, _vo, age in f.releases:
            b.note_off(p, t - age)
        b.pedal(float(f.pedals[0]), t)
        self._births()
        an = self.analyser
        if an is not None:
            if an.ok:
                req = b.analysis_request(t)
                if req is not None:
                    an.submit("a", req)
                for _key, meta, _blob in an.results():
                    b.on_analysis(meta)
                    self._dirty()
            elif b.analysed:
                b.use_analysis(False)                         # the worker died: the beat clock from the next phrase
        ph = self.ph
        if ph.metre is None and not ph.killing and b.position(t) is not None:    # the metre is known: lay it out
            self._metre(ph, b.beats, b.unit)
        for bar in b.poll(t):
            if bar.rewrite:                                   # the metre turned: the phrase is written again
                self._rewrite()
                continue
            if bar.marker:                                    # the phrase ended: the next one starts a line
                self._phrase_end()
                continue
            ph = self.ph
            if ph.killing:
                if len(ph.queued) < 64:
                    ph.queued.append(bar)
                continue
            self._commit(ph, bar)
        return f

    def _dirty(self) -> None:
        for g in self.glows:
            if g.phrase is self.ph:
                g.dirty = True

    def _metre(self, ph: _Phrase, beats: int, unit: int) -> None:
        """The phrase's metre is decided: its first line gets a header with the time signature and slots."""
        ph.metre = (beats, unit)
        ph.sig = (beats * 3, 8) if unit == 6 else (beats, 4)
        ph.nat = self.nat_seed.get(unit) or (NAT_DOTTED_SP if unit == 6 else NAT_BEAT_SP)
        if 0 not in ph.lines:
            ln = self._alloc((self.lines[-1].row + 1) if self.lines else 0)
            self._attach_first(ph, ln)
        self._plan_line(ph)
        self._dirty()

    def _commit(self, ph: _Phrase, bar) -> None:
        if ph.metre is None:
            self._metre(ph, bar.beats, bar.unit)
        k = bar.pos
        ph.committed = max(ph.committed, k + 1)
        ph.t_commit[k] = self.t_sound
        d = min(6.0, max(0.3, bar.t_end - bar.t_start))
        ph.bar_s = d if ph.bar_s is None else ph.bar_s + 0.3 * (d - ph.bar_s)
        self._ensure_plan(ph, k)
        ph.wait.append((k, bar))
        self._dirty()

    def _pump(self, ph: _Phrase) -> None:
        """Ask for the waiting bars in order, each justified to its slot (once its line's header width is known)."""
        from stream_service.score import mei
        while ph.wait:
            k, bar = ph.wait[0]
            j = ph.plan.line_of(k)
            L, ln = ph.plan.line(j), ph.lines.get(j)
            if L is None or ln is None or not ph.plan.covers(k) or ln.row - self.turn.target < -2.6:
                ph.items[k] = SKIP
                ph.wait.pop(0)
                continue
            if self.engraver is None:
                ph.items[k] = VIRTUAL
                ph.wait.pop(0)
                continue
            if (not ln.header and ln.hdr_key not in self.hdr_cache and ln.hdr_t is not None
                    and self.t_sound - ln.hdr_t < HDR_WAIT_S):
                break
            ln.sent += 1
            key = f"b:{ln.id}:{bar.index}"
            self.engraver.submit(key, mei.bar_doc(bar, ln.fifths), fit_px=round(L.bw, 1), squeeze=SQUEEZE)
            ph.sub[key] = k
            ph.t_sub[k] = self.t_sound
            ph.wait.pop(0)

    def _phrase_end(self) -> None:
        """The phrase ended: its last line closes where it stopped, a line planned past it is cleared for the next
        phrase, which turns in once the old pen is done (or its first note sounds)."""
        ph = self.ph
        b = self.builder
        if ph.metre is None and not ph.committed:
            for g in self.glows:                              # nothing written: its drifting notes fade
                if g.phrase is ph:
                    g.fade = g.fade or 0.6
            ph.t0, ph.conv, ph.conv_v = None, 0.0, 0.0
            ln0 = ph.lines.get(0)
            if ln0 is not None:
                ln0.fifths = b.key()[2]
            return
        ph.ended = True
        ph.plan.close(ph.committed)
        reuse = None
        for j in sorted(ph.lines):
            ln = ph.lines[j]
            if j > ph.plan.last or (ph.plan.line(j) is not None and ph.plan.line(j).n == 0):
                del ph.lines[j]
                if reuse is None and ln in self.lines:
                    reuse = ln
                else:
                    self._drop_line(ln)
        for j, ln in ph.lines.items():
            L = ph.plan.line(j)
            if L is not None and L.closed:
                ln.open_to = max(ln.open_to, L.end)
        if ph.bar_s and ph.plan.lines:
            self.R_last = ph.plan.lines[-1].bw / ph.bar_s
        if ph.metre:
            self.nat_seed[ph.metre[1]] = ph.nat
        self.old.append(ph)
        new = _Phrase()
        if reuse is not None:
            off = reuse.slot * self.ch * self.cw
            self.canvas[off:off + self.ch * self.cw].fill(0)
            row = reuse.row
            reuse.reset(self.sp, self.t2_off)
            reuse.row = row
            ln = reuse
        else:
            ln = self._alloc(self.lines[-1].row + 1 if self.lines else 0)
        self._attach_first(new, ln)
        new.turn_wait = True
        self.ph = new

    def _rewrite(self) -> None:
        """The metre turned: the phrase's lines are un-written (the pen runs back over them, KILL_S), then the
        phrase is written again on them from its start."""
        old = self.ph
        new = _Phrase()
        new.killing = [old.lines[j] for j in sorted(old.lines)]
        for ln in new.killing:
            ln.dying = self.t_sound
        new.t0 = old.t0
        new.turned = 0
        self.ph = new
        for g in self.glows:
            if g.phrase is old:
                g.phrase = new
                g.dirty = True
        if not new.killing:
            self._rewrite_lines(new)

    def _rewrite_lines(self, ph: _Phrase) -> None:
        """The un-written lines are cleared and start again where they are; the queued bars follow."""
        lines, ph.killing = ph.killing, []
        for ln in lines:
            off = ln.slot * self.ch * self.cw
            self.canvas[off:off + self.ch * self.cw].fill(0)
            ln.reset(self.sp, self.t2_off, keep=True)
            ln.open_to = float(self.cw)                  # open staff all along until it's planned again
            ln.hdr_x1 = self._hdr_est(self.builder.key()[2], False)
        if lines:
            self._attach_first(ph, lines[0])
            for j, ln in enumerate(lines[1:], 1):
                ph.lines[j] = ln
        queued, ph.queued = ph.queued, []
        if queued:
            self._metre(ph, queued[0].beats, queued[0].unit)
        for bar in queued:
            self._commit(ph, bar)

    # -- engraved ink into the canvases --------------------------------------------------------------------------------
    def _take_renders(self) -> None:
        if self.engraver is None:
            return
        results = self.engraver.results(RESULTS_PER_FRAME)
        if not results:
            return
        cp = self.ctx.cp
        if self._stage_done is not None:
            self._stage_done.synchronize()      # the last frame's async copies may still be reading the pinned area
            self._stage_done = None
        stage_off = 0
        touched = set()
        for key, meta, ink in results:
            if not key:
                continue
            kind = key.split(":", 1)[0]
            target = None
            if kind == "h":
                _h, lid, req = (key.split(":") + ["0"])[:3]
                target = next((x for x in self.lines if x.id == int(lid) and x.hreq == int(req)), None)
            elif kind == "H":
                target = next((sl for sl in getattr(self, "slots", ()) if sl["key"] == key), None)
            else:
                target = next((ph for ph in [self.ph] + self.old if key in ph.sub or key in ph.ins_sub), None)
            if target is None:
                continue
            ok = bool(meta.get("ok")) and bool(meta.get("measures"))
            w, h = (int(meta.get("w", 0)), int(meta.get("h", 0))) if ok else (0, 0)
            if ok and (w * h > self.ch * self.cw or len(ink) < w * h):
                ok = False
            render = None
            if ok:
                self.stage[stage_off:stage_off + w * h] = np.frombuffer(ink, np.uint8, w * h)
                g = self.gstage[stage_off:stage_off + w * h]
                g.set(self.stage[stage_off:stage_off + w * h], stream=cp.cuda.get_current_stream())
                render = (g.reshape(h, w), meta)
                stage_off += w * h
                if self._stage_done is None:
                    self._stage_done = cp.cuda.Event(block=False, disable_timing=True)
            if kind == "h":
                if render is not None:
                    self._header_landed(target, render)
            elif kind == "H":
                if render is not None:
                    self._s_header_landed(key, render)
            elif key in target.sub:
                k = target.sub.pop(key)
                target.items[k] = render if render is not None else EMPTY
                touched.add(id(target))
            else:
                k = target.ins_sub.pop(key)
                target.ins_items[k] = render if render is not None else EMPTY
                target.ins_wait.pop(k, None)
                touched.add(id(target))
        for ph in [self.ph] + self.old:
            if id(ph) in touched:
                self._drain(ph)
                for d in (ph.items, ph.ins_items):              # parked: own copies (the staging is reused)
                    for k, it in list(d.items()):
                        if isinstance(it, tuple) and not (len(it) > 2 and it[2]):
                            img, meta = _own(it[:2])
                            d[k] = (img, meta, True)
        if self._stage_done is not None:
            self._stage_done.record(cp.cuda.get_current_stream())

    def _header_landed(self, ln: _Line, render) -> None:
        img, meta = render[0], render[1]
        m = meta["measures"][0]
        w = m["x1"] + 1
        self._place(ln, (img, m), 0.0, w, header=True, dst=0)
        ln.header = True
        if ln.hdr_key is not None:
            self.hdr_cache[ln.hdr_key] = w
        ph = ln.phrase
        L = ph.plan.line(ln.j) if ph is not None and ph.lines.get(ln.j) is ln else None
        if L is not None and ln.sent == 0 and abs(w - L.hdr_w) > 0.5:
            ph.plan.resize(ln.j, self.cw, w, PAD_R_SP * self.sp)        # no bar asked for yet: slots re-sized
            ln.open_to = L.end
        elif L is not None and w < L.hdr_w - 1.0:
            self._staff_fill(ln, w, L.hdr_w, barline=False)
        ln.hdr_x1 = w if L is None else max(w, L.hdr_w)
        if ln.alpha > 0.05:
            ln.hdr_fade = True
        else:
            ln.hdr_a = 1.0

    def _drain(self, ph: _Phrase) -> None:
        """Place the phrase's bars in order; a bar blocking the queue too long (or past PARK_MAX parked, or the
        engraver gone) becomes an empty bar."""
        dead = self.engraver is not None and not self.engraver.ok
        for _ in range(64):
            k = ph.next_place
            if k in ph.ins_wait:                                # (scroll) the key change before it is due first
                if self.t_sound - ph.ins_wait[k] > PART_TIMEOUT_S or dead:
                    del ph.ins_wait[k]
                    for key, kk in list(ph.ins_sub.items()):
                        if kk == k:
                            del ph.ins_sub[key]
                else:
                    break
            it = ph.items.pop(k, None)
            if it is None:
                t = ph.t_sub.get(k)
                if t is None or not (self.t_sound - t > PART_TIMEOUT_S or len(ph.items) > PARK_MAX or dead):
                    break
                it = EMPTY
                for key, kk in list(ph.sub.items()):
                    if kk == k:
                        del ph.sub[key]
            if self._place_bar(ph, k, it) is False:             # (scroll) no room in the ring yet
                ph.items[k] = it
                break
            ph.next_place += 1

    def _place_bar(self, ph: _Phrase, k: int, it) -> None:
        plan = ph.plan
        j = plan.line_of(k)
        L, ln = plan.line(j), ph.lines.get(j)
        if it is SKIP or L is None or ln is None:
            return
        render = it if isinstance(it, tuple) else None
        if render is not None:
            img, meta = render[0], render[1]
            m = meta["measures"][0]
            crop = m["x0"] + 0.35 * self.sp                       # the engraver's system-start line
            width = m["x1"] + 1 - crop
        else:
            width = L.bw
        dst = plan.slot_x0(j, k)
        if dst + width > self.cw - 2 and k > L.k0 and plan.spill(k):
            self.spills += 1                                      # doesn't fit: the rest of the line moves on
            ln.open_fade_s = None
            self._ensure_plan(ph, k)
            j = plan.line_of(k)
            L, ln = plan.line(j), ph.lines.get(j)
            for g in self.glows:
                if g.phrase is ph:
                    g.dirty = True
            if L is None or ln is None:
                return
            dst = plan.slot_x0(j, k)
        if render is not None:
            self._place(ln, (img, m), crop, m["x1"] + 1, dst=dst)
            dy = int(round(ln.t1 - m["staves"][0][0]))
            hs = meta.get("heads") or []
            for nid, cx, cy in hs:
                ln.heads[int(nid)] = (dst + cx - crop, cy + dy)
            if hs:
                xs = sorted(cx - crop for _n, cx, _c in hs)
                self.lpads.append(min(4 * self.sp, max(0.3 * self.sp, xs[0])))
                self.rpads.append(min(4 * self.sp, max(0.3 * self.sp, width - xs[-1])))
                self.lpad = sorted(self.lpads)[len(self.lpads) // 2]
                self.rpad = sorted(self.rpads)[len(self.rpads) // 2]
            nat = meta.get("natural")
            if nat:
                ph.nat += 0.25 * (min(30.0, max(1.5, nat / (ph.beats * self.sp))) - ph.nat)
        elif it is EMPTY:
            self._staff_fill(ln, dst, dst + width, barline=True)
            self.bars_empty += 1
        L.E += width - L.bw
        ln.placed[k] = (dst, width)
        ln.widths.append(width)
        ln.cursor = max(ln.cursor, dst + width)
        ln.full = k >= L.k0 + L.n - 1
        if ln.open_fade_s is None and not L.closed:
            ln.open_to = min(float(self.cw), max(ln.open_to, L.end))

    def _place(self, ln: _Line, render, x0: float, x1: float, header: bool = False, dst=None) -> None:
        """Composite columns [x0, x1) of an engraved part at canvas x ``dst`` (default the line's cursor), its
        treble top line on ln.t1."""
        img, m = render
        h, w = img.shape
        top = m["staves"][0][0]
        dy = int(round(ln.t1 - top))
        c0, c1 = int(x0), min(int(math.ceil(x1)), w)
        if self.layout == "scroll":                         # onto the ring at tape u = dst
            u = int(round(ln.cursor if dst is None else dst))
            cols = min(c1 - c0, self.TW - 64)
            r0, r1 = max(0, dy), min(self.ch, dy + h)
            if cols <= 0 or r1 <= r0:
                return
            for d, o, n in ring_slices(u, cols, self.TW):
                t = self.tape[:, d:d + n]
                t *= self.colok[d:d + n][None, :]           # a column from an earlier lap starts clean
                seg = t[r0:r1]
                self.ctx.cp.maximum(seg, img[r0 - dy:r1 - dy, c0 + o:c0 + o + n], out=seg)
                self.colok[d:d + n] = 1
            ln.cursor = max(ln.cursor, u + cols)
            return
        dst_x = int(round(ln.cursor if dst is None else dst))
        cols = min(c1 - c0, self.cw - dst_x)
        r0, r1 = max(0, dy), min(self.ch, dy + h)
        if cols <= 0 or r1 <= r0 or dst_x < 0:
            return
        off = ln.slot * self.ch * self.cw
        cv = self.canvas[off:off + self.ch * self.cw].reshape(self.ch, self.cw)
        d = cv[r0:r1, dst_x:dst_x + cols]
        self.ctx.cp.maximum(d, img[r0 - dy:r1 - dy, c0:c0 + cols], out=d)
        ln.cursor = max(ln.cursor, dst_x + cols)
        if header and len(m["staves"]) > 1:
            ln.t2 = ln.t1 + (m["staves"][1][0] - top)
            self.t2_off = ln.t2 - ln.t1

    def _staff_fill(self, ln: _Line, x0: float, x1: float, barline: bool) -> None:
        """Ink the ten staff lines over [x0, x1) (an empty bar, or a gap), and its bar line at the right."""
        a, b = max(0, int(round(x0))), min(self.cw, int(round(x1)))
        if b <= a:
            return
        off = ln.slot * self.ch * self.cw
        cv = self.canvas[off:off + self.ch * self.cw].reshape(self.ch, self.cw)
        t = max(1, int(round(self.th)))
        for top in (ln.t1, ln.t2):
            for i in range(5):
                y = int(round(top + i * self.sp - 0.5 * t))
                if 0 <= y and y + t <= self.ch:
                    cv[y:y + t, a:b] = 220
        if barline:
            y0, y1 = max(0, int(round(ln.t1))), min(self.ch, int(round(ln.t2 + 4 * self.sp)) + 1)
            cv[y0:y1, max(a, b - 2):b] = 230

    # -- page: the pen, turns and lines ----------------------------------------------------------------------------
    def _line_pen(self, ph: _Phrase, ln: _Line) -> float:
        """The pen's x on one of its phrase's lines: half a feather before the header's end until it starts the
        line, half a feather past the last bar line once it's done, the feather's offset ramping between."""
        fe = FEATHER_SP * self.sp
        hx = ln.hdr_x1 if ln.hdr_xd is None else ln.hdr_xd
        L = ph.plan.line(ln.j) if ph.metre is not None else None
        if L is None:                                   # no plan yet; or (a long phrase) an old line let go: written
            return hx - 0.5 * fe if ph.metre is None or ln.j > ph.plan.last else self.cw + fe
        p = ph.pen.p
        if p <= L.k0:
            return hx - 0.5 * fe
        if p >= L.k0 + L.n:
            last = ln.placed.get(L.k0 + L.n - 1)
            return (last[0] + last[1] if last is not None else L.end) + 0.5 * fe
        k = int(math.floor(p))
        pl = ln.placed.get(k)
        x0, w = pl if pl is not None else (ph.plan.slot_x0(ln.j, k), L.bw)
        return x0 + (p - k) * w + fe * ((p - L.k0) / max(1, L.n) - 0.5)

    def _page_step(self, dt: float, glow_s: float, e: float) -> None:
        b = self.builder
        ph = self.ph
        now = self.t_sound
        # rewrite: the old lines dissolve, then come back for the new phrase
        if ph.killing and all(ln.kill <= 0.0 for ln in ph.killing):
            self._rewrite_lines(ph)
        # pens
        sp = self.sp
        for q in [ph] + self.old:
            self._pump(q)
            self._drain(q)
            if q.metre is None or not q.plan.lines:
                continue
            bar_s = q.bar_s or b.bar_seconds() or 2.0
            R = 1.0 / bar_s
            L = q.plan.line(q.plan.line_of(math.floor(q.pen.p + 1e-9)))
            bw = L.bw if L is not None else 20 * sp
            q.pen.step(float(q.next_place), R, dt, vcap=PEN_VMAX_SPS * sp / max(bw, 1.0), fade_s=0.5 * glow_s,
                       acap=PEN_AMAX_SPS * sp / max(bw, 1.0) * (min(dt, A_DT) / dt if dt > 0 else 1.0))
            # degrade: bars with no line (skipped, or their line gone) are passed at once, and a line leaving the
            # page is left (the pen can't keep up: very short bars)
            for _ in range(64):
                k = int(math.floor(q.pen.p + 1e-9))
                if k >= q.next_place:
                    break
                jl = q.plan.line_of(k)
                ln, L = q.lines.get(jl), q.plan.line(jl)
                if L is None:
                    break
                if ln is None or ln.row - self.turn.x < -2.3:
                    q.pen.p = float(min(q.next_place, max(k + 1, L.k0 + L.n)))
                else:
                    break
            j = q.plan.line_of(math.floor(q.pen.p + 1e-9))
            if j != q.pen_j:                              # the pen starts a line: its x there starts at its pace
                q.pen_j = j
                ln = q.lines.get(j)
                if ln is not None:
                    ln.pen_entry = True
            if q.done and q.t_done is None:
                q.t_done = now
        # turns
        if ph.metre is None:
            if ph.turn_wait:
                last_old = self.old[-1] if self.old else None
                started = any(g.phrase is ph for g in self.glows)
                if started or last_old is None or (last_old.t_done is not None
                                                   and now - last_old.t_done >= PHRASE_HOLD_S):
                    ph.turn_wait = False
                    ln0 = ph.lines.get(0)
                    if ln0 is not None:
                        self._turn_to(ln0.row)
        elif not ph.killing:
            U = b.position(now)
            if U is None:
                U = float(ph.committed)
            bar_s = ph.bar_s or b.bar_seconds() or 2.0
            k = int(math.floor(U + TURN_LEAD_S / bar_s))
            self._ensure_plan(ph, k)
            j = ph.plan.line_of(k)
            Lp = ph.plan.line(j)
            if Lp is not None and not ph.ended:
                self._ensure_plan(ph, Lp.k0 + Lp.n)               # the line after it is planned too
            if j > ph.turned and j in ph.lines:
                ph.turned = j
            if ph.turned in ph.lines:
                ph.turn_wait = False
                row = ph.lines[ph.turned].row
                pl = ph.lines.get(ph.plan.line_of(math.floor(ph.pen.p + 1e-9)))
                if pl is not None:                       # the line being inked stays on the page (very short
                    row = min(row, pl.row + 2)           # bars: the playing may run two lines ahead)
                self._turn_to(row)
            elif ph.turn_wait and 0 in ph.lines:
                ph.turn_wait = False
                self._turn_to(ph.lines[0].row)
        self.turn.step(dt)
        self.page_row = self.turn.x
        # old phrases are let go once done and off the page
        self.old = [q for q in self.old if not (q.done and not any(ln in self.lines for ln in q.lines.values()))]
        # lines
        pens = self._pen_lines()
        for ln in list(self.lines):
            ro = ln.row - self.turn.x
            if ro <= -2.6 and ln.id not in pens and ln.dying is None:
                self._drop_line(ln)
        fe = FEATHER_SP * sp
        a_pen = 1.0 - math.exp(-dt / 0.5) if dt > 0 else 0.0
        a_up = 1.0 - math.exp(-dt / 0.15) if dt > 0 else 0.0
        a_dn = 1.0 - math.exp(-dt / max(0.1, 0.5 * glow_s)) if dt > 0 else 0.0
        for ln in self.lines:
            ro = ln.row - self.turn.x
            if ln.hdr_xd is None or ln.alpha <= 0.02:
                ln.hdr_xd = ln.hdr_x1
            else:
                step = OPEN_FADE_SPS * sp * dt
                ln.hdr_xd += min(step, max(-step, ln.hdr_x1 - ln.hdr_xd))
            if ln.dying is not None:
                ln.kill = 1.0 - smoothstep(0.0, KILL_S, now - ln.dying)
            base = _row_alpha(ro)
            on_pen = ln.id in pens
            ln.wpen += ((1.0 if on_pen else 0.0) - ln.wpen) * a_pen
            boost = max(base, smoothstep(-2.6, -2.0, ro))
            ln.alpha = base + (boost - base) * ln.wpen
            ln.y = self.y_bot + ro * self.pitch
            ln.y_target = self.y_bot + (ln.row - self.turn.target) * self.pitch
            q = ln.phrase
            L = q.plan.line(ln.j) if q is not None and q.metre is not None else None
            raw = self._line_pen(q, ln) if q is not None else -1e9
            if ln.dying is not None:                     # un-writing: the pen runs back to the line's start
                hx = (ln.hdr_xd if ln.hdr_xd is not None else ln.hdr_x1) - 0.5 * FEATHER_SP * sp
                raw = hx + (max(raw, hx) - hx) * ln.kill
                ln.hdr_a = min(ln.hdr_a, ln.kill)
            if ln.pen_entry and L is not None:
                pl = ln.placed.get(int(math.floor(q.pen.p)))
                w = pl[1] if pl is not None else L.bw
                v_px = q.pen.v * (w + FEATHER_SP * sp / max(1, L.n))
                ln.pen_x, ln.pen_v, ln.pen_entry = raw - 2.0 * v_px / PEN_W, v_px, False   # its steady lag
            elif ln.pen_x <= -1e8 or raw <= -1e8 or abs(raw - ln.pen_x) > 0.5 * self.cw:
                ln.pen_x, ln.pen_v = raw, 0.0
            else:
                ln.pen_x, ln.pen_v = spring_step(ln.pen_x, ln.pen_v, raw, PEN_W, dt)
            # the fresh ink's glow follows the pen on its line and fades behind it
            amp = (0.55 + 0.5 * e) * q.pen.active if (q is not None and on_pen and ln.dying is None) else 0.0
            ln.glow += (amp - ln.glow) * (a_up if amp > ln.glow else a_dn)
            ln.fresh = (0.0, 0.0, ln.glow)
            if ln.hdr_fade:
                ln.hdr_a = min(1.0, ln.hdr_a + dt / HDR_FADE_S)
            # open staff: to the line's end; a line closed short fades it once the pen has passed its last bar line
            if L is not None:
                if L.closed:
                    last = ln.placed.get(L.k0 + L.n - 1)
                    x_last = last[0] + last[1] if last is not None else L.end
                    if ln.open_fade_s is None and ln.pen_x >= x_last and (q.next_place >= L.k0 + L.n):
                        span = max(0.0, ln.open_to - x_last)
                        ln.open_fade_s = max(OPEN_FADE_S, span / (OPEN_FADE_SPS * sp))
                    if ln.open_fade_s is not None:
                        ln.open_a = max(0.0, ln.open_a - dt / ln.open_fade_s)
                else:
                    ln.open_to = min(float(self.cw), max(L.end, ln.open_to)) if ln.open_fade_s is None else ln.open_to
        ph_now = self.ph
        pl = ph_now.lines.get(ph_now.plan.line_of(math.floor(ph_now.pen.p + 1e-9))) if ph_now.plan.lines else None
        self.pen_line_id = pl.id if pl is not None else None
        self.pen = ph_now.pen.p
        self.pen_backlog = max(0.0, ph_now.next_place - ph_now.pen.p)
        # the kernel's per-line parameters
        Rh, RIh = self.Rh, self.RIh
        Rh[:] = 0.0
        RIh[:] = 0
        for i, ln in enumerate(self.lines[-NROW:]):
            q = ln.phrase
            L = q.plan.line(ln.j) if q is not None and q.metre is not None else None
            bar_s = (q.bar_s if q is not None else None) or b.bar_seconds() or 2.0
            bw = L.bw if L is not None else 20 * sp
            v = q.pen.v if q is not None else 0.0
            glow_len = max(sp, glow_s * max(v * bw, 0.5 * bw / bar_s))
            pen_k = ln.pen_x if self.engraver is not None else -1e9
            ghost_bw = L.bw if L is not None else 0.0
            ghost_phase = L.hdr_w + L.E if L is not None else 0.0
            open_to = ln.open_to if self.engraver is not None else float(self.cw)
            Rh[16 * i:16 * i + 16] = (self.x_off, ln.y, ln.alpha, pen_k, fe, ln.glow, glow_len,
                                      ln.hdr_xd if self.engraver is not None else 0.0, ln.hdr_a, open_to,
                                      ln.open_a, ln.t1, ln.t2, ghost_phase, ghost_bw, GHOST_A if L else 0.0)
            RIh[i] = ln.slot * self.ch * self.cw
        for ln in self.lines:
            ln.pending = []
        for q in [ph] + self.old:
            for k, it in q.items.items():
                ln = q.lines.get(q.plan.line_of(k)) if q.plan.lines else None
                if ln is not None:
                    ln.pending.append(k)

    # -- scroll: the tape ----------------------------------------------------------------------------------------------
    def _setup_scroll(self, cp) -> None:
        sp = self.sp
        self.y0 = int(round(0.5 * self.h - 13 * sp))
        self.x_h0 = int(round(X_HDR * self.w))
        self.HW = int(HW_MAX_SP * sp)
        self.TW = int(math.ceil((2 * self.w + 64 * sp) / 64.0) * 64)
        self.tape = cp.zeros((self.ch, self.TW), cp.uint8)
        self.colok = cp.zeros(self.TW, cp.uint8)
        self.hdr = cp.zeros((2, self.ch, self.HW), cp.uint8)
        self.G = cp.zeros(NGHOST * 2, cp.float32)
        self.Gh = np.zeros(NGHOST * 2, np.float32)
        ln = _Line(0, sp, None)
        ln.id = 0                                   # the tape (the glows' home 0)
        ln.y = ln.y_target = float(self.y0)
        ln.alpha = 1.0
        ln.header = True
        self.tape_line = ln
        self.lines = [ln]
        self.S, self.V = 0.0, V_IDLE_SP * sp        # the paper: tape u at screen x 0, and its speed (px/s)
        self.vff = 0.0
        self.Ds, self.Ds_v, self.Lw, self.lane_c = 0.0, 0.0, 1.0, 1.0
        self.lane_u, self.lane_v = None, 0.0        # where the lane starts (the current phrase's pen, eased)
        self.frontier = None                        # the tape's last ink
        self.cleared = 0                            # ring columns below this u have scrolled past (free)
        self.seq = 0
        self.pseq = 0
        fifths = self.builder.key()[2]
        self.marks = collections.deque(maxlen=8)   # (u, fifths, sig | None): where a key / time takes over
        first = (-1e18, fifths, None)
        self.marks.append(first)
        self.slots = [{"mark": None, "w": 0.0, "ready": False, "key": None} for _ in range(2)]
        self.slot_a = [0.0, 0.0]
        self.active = 0
        self.xfade = None
        self._s_request_header(0, first)
        self.hw = Glide(self._hdr_est(fifths, False))   # the sticky header's width, eased
        self._s_geom()
        self.scroll_x, self.scroll_v, self.pen_sx = self.S, self.V, self.X_p
        self._metre, self._commit, self._phrase_end = self._s_metre, self._s_commit, self._s_phrase_end
        self._rewrite, self._target, self._retired = self._s_rewrite, self._s_target, self._s_retired
        self._place_bar, self._edge = self._s_place_bar, (lambda g: 1.0)

    def _s_geom(self) -> None:
        sp = self.sp
        self.X_left = self.x_h0 + self.hw.x + 1.5 * sp
        self.X_R = self.w - 2.0 * sp
        self.tape_w = max(sp, self.X_R - self.X_left)
        self.X_p = self.X_left + PEN_X * self.tape_w
        self.X_h = self.X_left + LANE_X * self.tape_w

    def _inl_est(self, fifths: int, meter: bool) -> float:
        """An inline key / time change's width until it's engraved (measured)."""
        return (7.4 + (0.8 + abs(fifths) if fifths else 0.0) + (2.5 if meter else 0.0)) * self.sp

    def _room(self, u_end: float) -> bool:
        """The ring has room up to here (it holds the screen and the stretch ahead)."""
        return u_end <= self.S + self.TW - 64

    def _s_request_header(self, slot: int, mark) -> None:
        self.seq += 1
        key = f"H:{self.seq}"
        self.slots[slot] = {"mark": mark, "w": 0.0, "ready": False, "key": key}
        if self.engraver is None:
            return
        from stream_service.score import mei
        _u, fifths, sig = mark
        self.engraver.submit(key, mei.header_doc(fifths, meter=sig is not None, sig=sig or (4, 4)),
                             margin_left=int(3 * self.sp))

    def _s_header_landed(self, key: str, render) -> None:
        slot = next((i for i, sl in enumerate(self.slots) if sl["key"] == key), None)
        if slot is None:
            return
        img, meta = render[0], render[1]
        m = meta["measures"][0]
        w = min(self.HW, int(m["x1"] + 1))
        top = m["staves"][0][0]
        dy = int(round(self.tape_line.t1 - top))
        h = img.shape[0]
        r0, r1 = max(0, dy), min(self.ch, dy + h)
        hs = self.hdr[slot]
        hs.fill(0)
        if r1 > r0 and w > 0:
            hs[r0:r1, :min(w, img.shape[1])] = img[r0 - dy:r1 - dy, :min(w, img.shape[1])]
        self.slots[slot]["w"] = float(w)
        self.slots[slot]["ready"] = True
        if len(m["staves"]) > 1:
            self.t2_off = m["staves"][1][0] - top
            self.tape_line.t2 = self.tape_line.t1 + self.t2_off
        if slot == self.active:
            self.hw.to(float(w), HDR_XFADE_S)

    def _s_headers(self, dt: float) -> None:
        """The sticky header: the newest mark that has reached the header's edge takes over (its header loaded into
        the other slot beforehand, then a crossfade; a slot is never overwritten mid-crossfade)."""
        edge = self.S + self.X_left
        target = None
        for m in self.marks:
            if m[0] <= edge:
                target = m
        act = self.slots[self.active]
        o = 1 - self.active
        if self.xfade is None:
            if act["ready"]:
                self.slot_a[self.active] = min(1.0, self.slot_a[self.active] + dt / HDR_FADE_S)
            cur_u = act["mark"][0] if act["mark"] is not None else -1e18
            nxt = next((m for m in self.marks if m[0] > cur_u), None)
            want = target if (target is not None and target is not act["mark"]) else nxt
            if want is not None and want is not act["mark"] and self.slots[o]["mark"] is not want:
                self._s_request_header(o, want)
            if (target is not None and target is not act["mark"] and self.slots[o]["mark"] is target
                    and self.slots[o]["ready"]):
                self.xfade = (self.t_sound, self.slot_a[self.active])
                self.hw.to(self.slots[o]["w"], HDR_XFADE_S)
        if self.xfade is not None:
            t0, a0 = self.xfade
            k = smoothstep(0.0, HDR_XFADE_S, self.t_sound - t0)
            self.slot_a[o] = k
            self.slot_a[self.active] = a0 * (1.0 - k)
            if k >= 1.0:
                self.slot_a[self.active] = 0.0
                self.active, self.xfade = o, None

    def _s_free_columns(self) -> None:
        upto = int(math.floor(self.S)) - 2
        if upto > self.cleared:
            n = min(upto - self.cleared, self.TW)
            for d, _o, w in ring_slices(self.cleared, n, self.TW):
                self.colok[d:d + w] = 0
            self.cleared = upto

    def _s_clear(self, u0: float, u1: float) -> None:
        a = int(math.ceil(u0))
        n = min(int(math.ceil(u1)) - a, self.TW)
        for d, _o, w in ring_slices(a, n, self.TW):
            self.colok[d:d + w] = 0

    def _s_start(self, ph: _Phrase) -> None:
        """A phrase's first note: its place on the tape (after the last ink, a staff gap on), the pen parked there."""
        sp = self.sp
        base = self.S + self.X_left + ENTRY_X * self.tape_w
        ph.U_p = base if self.frontier is None else max(self.frontier + PHRASE_GAP_SP * sp, base)
        fifths = self.builder.key()[2]
        ph.fifths = ph.tape_fifths = fifths
        ph.ruler = Ruler(ph.U_p, self._inl_est(fifths, True), 20.0 * sp)
        ph.pen = Pen(-1.0)
        ph.pen_u = ph.U_p
        ph.next_place = -1
        ph.R_prov = self.R_last or 10.0 * sp
        self.pseq += 1
        ph.seq = self.pseq

    def _s_wslot(self, ph: _Phrase) -> float:
        nat_px = ph.nat * ph.beats * self.sp
        bar_s = ph.bar_s or self.builder.bar_seconds() or 2.0
        return max(SQUEEZE * nat_px, min(WANT * nat_px, V_CAP_SP * self.sp * bar_s))

    def _s_freeze(self, ph: _Phrase, upto: int) -> None:
        """Slots are fixed half a beat before their bar is played (no wider than V_CAP_SP allows, and near the
        last); the ones further on follow the same rule provisionally."""
        r = ph.ruler
        w0 = self._s_wslot(ph)
        for _ in range(64):
            if ph.nfrozen > upto:
                break
            w = w0
            if ph.nfrozen > 0 and r.w[ph.nfrozen - 1]:
                prev = r.w[ph.nfrozen - 1]
                w = min(W_RATE[1] * prev, max(W_RATE[0] * prev, w))
            r.freeze(ph.nfrozen, w)
            ph.nfrozen += 1
        if ph.nfrozen > 0 and r.w[ph.nfrozen - 1]:
            prev = r.w[ph.nfrozen - 1]
            w0 = min(W_RATE[1] * prev, max(W_RATE[0] * prev, w0))
        r.set_prov(w0)

    def _s_metre(self, ph: _Phrase, beats: int, unit: int) -> None:
        """The metre is decided: an inline time signature (and the key) opens the phrase, its bars follow."""
        if ph.ruler is None:
            self._s_start(ph)
        ph.metre = (beats, unit)
        ph.sig = (beats * 3, 8) if unit == 6 else (beats, 4)
        ph.nat = self.nat_seed.get(unit) or (NAT_DOTTED_SP if unit == 6 else NAT_BEAT_SP)
        fifths = self.builder.key()[2]
        ph.fifths = ph.tape_fifths = fifths
        ph.ruler.set_inline(self._inl_est(fifths, True))
        ph.ruler.set_prov(self._s_wslot(ph))
        if self.engraver is None:
            ph.items[-1] = VIRTUAL
        else:
            from stream_service.score import mei
            self.seq += 1
            key = f"i:{self.seq}"
            self.engraver.submit(key, mei.change_doc(fifths, ph.sig))
            ph.sub[key] = -1
            ph.t_sub[-1] = self.t_sound
        self._dirty()

    def _s_commit(self, ph: _Phrase, bar) -> None:
        if ph.metre is None:
            self._s_metre(ph, bar.beats, bar.unit)
        k = bar.pos - ph.k_base
        if k < 0:                                         # written before the rewrite's anchor: stays as it was
            return
        ph.committed = max(ph.committed, k + 1)
        ph.t_commit[k] = self.t_sound
        ph.bar_t[k] = (bar.t_start, bar.t_end)
        d = min(6.0, max(0.3, bar.t_end - bar.t_start))
        ph.bar_s = d if ph.bar_s is None else ph.bar_s + 0.3 * (d - ph.bar_s)
        fifths = self.builder.key()[2]
        if k > 0 and fifths != ph.tape_fifths and self.engraver is not None:
            from stream_service.score import mei
            self.seq += 1                                 # a key change: written inline before this bar
            key = f"i:{self.seq}"
            ph.ruler.insert(k, self._inl_est(fifths, False))
            self.engraver.submit(key, mei.change_doc(fifths))
            ph.ins_sub[key] = k
            ph.ins_wait[k] = self.t_sound
            ph.ins_info[k] = fifths
            ph.tape_fifths = fifths
        ph.bar_fifths[k] = ph.tape_fifths
        self._s_freeze(ph, k)
        if self.engraver is None:
            ph.items[k] = VIRTUAL
        else:
            from stream_service.score import mei
            key = f"b:{ph.seq}:{bar.index}"
            self.engraver.submit(key, mei.bar_doc(bar, ph.tape_fifths), fit_px=round(ph.ruler.w[k], 1),
                                 squeeze=SQUEEZE)
            ph.sub[key] = k
            ph.t_sub[k] = self.t_sound
        self._dirty()

    def _s_place_bar(self, ph: _Phrase, k: int, it) -> bool:
        """Composite a part onto the tape at its ruler position (False: no room in the ring yet)."""
        r = ph.ruler
        sp = self.sp
        if r is None or it is SKIP:
            return True
        render = it if isinstance(it, tuple) else None
        if render is not None:
            img, meta = render[0], render[1]
            m = meta["measures"][0]
            crop = m["x0"] + 0.35 * sp
            width = m["x1"] + 1 - crop
        if k == -1:                                       # the phrase's opening time signature and key
            if render is not None:
                r.set_inline(width)
            u = r.U_p
            if not self._room(u + r.w_inl):
                return False
            if render is not None:
                self._place(self.tape_line, (img, m), crop, m["x1"] + 1, dst=u)
            self.marks.append((u, ph.fifths, ph.sig))
            self.frontier = max(self.frontier or u, u + r.w_inl)
            return True
        ins = ph.ins_items.get(k)
        if isinstance(ins, tuple):
            im = ins[1]["measures"][0]
            icrop = im["x0"] + 0.35 * sp
            r.set_insert(k, im["x1"] + 1 - icrop)
        u = r.u0(k)
        if render is None:
            width = r.width(k)
        if not self._room(u + width):
            return False
        ins = ph.ins_items.pop(k, None)
        if ins is not None and k in ph.ins_info:
            ui = u - r.ins[k]
            if isinstance(ins, tuple):
                im = ins[1]["measures"][0]
                icrop = im["x0"] + 0.35 * sp
                self._place(self.tape_line, (ins[0], im), icrop, im["x1"] + 1, dst=ui)
            self.marks.append((ui, ph.ins_info[k], None))
        if render is not None:
            self._place(self.tape_line, (img, m), crop, m["x1"] + 1, dst=u)
            dy = int(round(self.tape_line.t1 - m["staves"][0][0]))
            hs = meta.get("heads") or []
            for nid, cx, cy in hs:
                ph.heads[int(nid)] = (u + cx - crop, cy + dy)
            if hs:
                xs = sorted(cx - crop for _n, cx, _c in hs)
                self.lpads.append(min(4 * sp, max(0.3 * sp, xs[0])))
                self.rpads.append(min(4 * sp, max(0.3 * sp, width - xs[-1])))
                self.lpad = sorted(self.lpads)[len(self.lpads) // 2]
                self.rpad = sorted(self.rpads)[len(self.rpads) // 2]
            nat = meta.get("natural")
            if nat:
                ph.nat += 0.25 * (min(30.0, max(1.5, nat / (ph.beats * sp))) - ph.nat)
        elif it is EMPTY:
            self.bars_empty += 1                          # colok stays 0 there: the staff is drawn
        r.place(k, width)
        ph.placed[k] = (u, width)
        self.tape_line.widths.append(width)
        self.frontier = max(self.frontier or u, u + width)
        if ph.ended and k >= ph.committed - 1:
            self._s_final_bar(ph)
        return True

    def _s_final_bar(self, ph: _Phrase) -> None:
        """A phrase's end: a final double bar line (thin, gap, thick) after its last bar."""
        if ph.final or ph.ruler is None:
            return
        ph.final = True
        sp = self.sp
        u = int(round(ph.ruler.u0(ph.committed)))
        thin, gap, thick = max(1, int(round(0.16 * sp))), int(round(0.4 * sp)), max(2, int(round(0.5 * sp)))
        wtot = thin + gap + thick
        ln = self.tape_line
        y0, y1 = max(0, int(round(ln.t1))), min(self.ch, int(round(ln.t2 + 4 * sp)) + 1)
        t = max(1, int(round(self.th)))
        u0 = u - thin                                     # the last bar's own thin line is at its end
        for d, o, n in ring_slices(u0, wtot, self.TW):
            seg = self.tape[:, d:d + n]
            seg *= self.colok[d:d + n][None, :]
            for top in (ln.t1, ln.t2):
                for i in range(5):
                    yy = int(round(top + i * sp - 0.5 * t))
                    if 0 <= yy and yy + t <= self.ch:
                        seg[yy:yy + t, :] = 220
            for a, b in ((0, thin), (thin + gap, wtot)):
                a2, b2 = max(a - o, 0), min(b - o, n)
                if b2 > a2:
                    seg[y0:y1, a2:b2] = 235
            self.colok[d:d + n] = 1
        self.frontier = max(self.frontier or u, float(u0 + wtot))

    def _s_phrase_end(self) -> None:
        ph = self.ph
        if ph.metre is None and not ph.committed:
            for g in self.glows:                              # nothing written: its notes fade
                if g.phrase is ph:
                    g.fade = g.fade or 0.6
            self.ph = _Phrase()
            return
        ph.ended = True
        if ph.next_place >= ph.committed:
            self._s_final_bar(ph)
        if ph.bar_s and ph.nfrozen:
            self.R_last = float(np.mean([w for w in ph.ruler.w[:ph.nfrozen] if w])) / ph.bar_s
        if ph.metre:
            self.nat_seed[ph.metre[1]] = ph.nat
        self.old.append(ph)
        self.ph = _Phrase()

    def _s_rewrite(self) -> None:
        """The metre turned: no going back on the tape.  What was written past the pen is taken back, and the
        phrase goes on after a staff gap with the new time signature, from its next bar line in the new metre."""
        old = self.ph
        new = _Phrase()
        b = self.builder
        new.t0 = old.t0
        if old.ruler is not None and old.metre is not None:
            kp = int(math.floor(max(0.0, old.pen.p)))
            frac = max(0.0, old.pen.p) - kp
            ts = old.bar_t.get(kp)
            t_pen = ts[0] + frac * (ts[1] - ts[0]) if ts else self.t_sound
            pos = b.position(t_pen)
            new.k_base = int(math.ceil(pos - 1e-6)) if pos is not None else 0
            u_pen = old.ruler.u(old.pen.p)
            if self.frontier is not None and self.frontier > u_pen:
                self._s_clear(u_pen + FEATHER_SP * self.sp, self.frontier)
            self.frontier = u_pen
            old.items.clear()
            old.sub.clear()
            old.t_sub.clear()
            old.ins_sub.clear()
            old.ins_wait.clear()
            old.committed = max(0, int(math.ceil(old.pen.p - 1e-6)))
            old.next_place = max(old.next_place, old.committed)
            old.ended = old.final = True
            self.old.append(old)
            self._s_start(new)
        self.ph = new
        for g in self.glows:
            if g.phrase is old:
                g.phrase = new
                g.dirty = True

    def _s_target(self, g: _Glow):
        """(the tape line, u, y): a note's place on the tape."""
        ph = g.phrase
        n = g.note
        r = ph.ruler
        if r is None or ph.t0 is None:
            return None
        if g.dirty:
            self._where(g)
        ln = self.tape_line
        if g.pre or g.k is None:
            if g.fade:
                return None
            u = r.U_p + r.w_inl + self.lpad + (n.t_on - ph.t0) * ph.R_prov
            return ln, u, self._pitch_y(ln, n.pitch, ph.tape_fifths, g.staff)
        k = g.k
        pl = ph.placed.get(k)
        if pl is not None:
            hd = ph.heads.get(n.id)
            if hd is not None:
                return ln, hd[0], hd[1]
            u0, wk = pl
        else:
            u0, wk = r.u0(k), r.width(k)
        u = u0 + self.lpad + g.frac * max(self.sp, wk - self.lpad - self.rpad)
        return ln, u, self._pitch_y(ln, n.pitch, ph.bar_fifths.get(k, ph.tape_fifths), g.staff)

    def _s_retired(self, g: _Glow) -> bool:
        ph = g.phrase
        if g.pre or g.k is None or ph.ruler is None or ph.metre is None:
            return False
        return ph.pen_u >= g.x - RETIRE_AHEAD_SP * self.sp

    def _lane(self, d):
        """lane_map with a soft onset: the squeeze starts with zero slope as the lag outgrows the lane (the
        newest note may then sit up to a quarter lane past its end)."""
        D, Lw = self.Ds, self.Lw
        m = 0.5 * Lw
        if D > Lw + m:
            D -= 0.5 * m
        elif D > Lw:
            D = Lw + (D - Lw) ** 2 / (2.0 * m)
        vf = self.V / 30.0                              # px a frame: a note leaving the lane speeds up to the
        return lane_map(d, D, Lw, knee=vf * vf / 1.2)   # paper's pace over the knee, at most ~1.2 px/frame^2

    def _s_head(self, ph: _Phrase):
        """The tape position being played now (before the metre: the newest note's provisional place)."""
        if ph.ruler is None or ph.t0 is None:
            return None
        r = ph.ruler
        U = self.builder.position(self.t_sound) if ph.metre is not None else None
        if U is not None:
            return r.u(U - ph.k_base)
        return r.U_p + r.w_inl + self.lpad + (self.t_sound - ph.t0) * ph.R_prov

    def _scroll_step(self, dt: float, glow_s: float, e: float, big: bool) -> None:
        b = self.builder
        sp = self.sp
        now = self.t_sound
        self._s_geom()
        ph = self.ph
        if ph.metre is not None and ph.ruler is not None:
            U = b.position(now)
            if U is not None:
                self._s_freeze(ph, int(math.floor(U - ph.k_base + 0.5 / ph.beats)))
        elif ph.ruler is not None:                       # nothing written yet: the phrase's place moves with the paper
            base = self.S + self.X_left + ENTRY_X * self.tape_w
            ph.U_p = ph.ruler.U_p = base if self.frontier is None else max(self.frontier + PHRASE_GAP_SP * sp, base)
            ph.ruler._dirty = True
        for q in [ph] + self.old:
            self._drain(q)
        # pens: each phrase's, held back by the paper (it may not run past the lane's end)
        for q in self.old + [ph]:
            r = q.ruler
            if r is None:
                continue
            bar_s = q.bar_s or b.bar_seconds() or 2.0
            k = int(math.floor(q.pen.p))
            wcur = max(1.0, r.width(k) if k >= 0 else r.w_inl)
            vcap = (self.V + max(0.0, self.X_h - (q.pen_u - self.S)) / 0.5) / wcur
            q.pen.step(float(q.next_place), 1.0 / bar_s, dt, vcap=vcap, fade_s=0.5 * glow_s,
                       acap=PEN_AMAX_SPS * sp / wcur * (min(dt, A_DT) / dt if dt > 0 else 1.0))
            raw = r.u(q.pen.p)                           # through a stiff spring: a wider bar's pace eases in
            if q.pen_ur is None or big or abs(raw - q.pen_u) > 0.5 * self.w or q.metre is None:
                q.pen_u, q.pen_uv = raw, 0.0
            else:
                q.pen_u, q.pen_uv = spring_step(q.pen_u, q.pen_uv, raw, PEN_W, dt)
            q.pen_ur = raw
            if q.done and q.t_done is None:
                q.t_done = now
        self.old = [q for q in self.old
                    if not (q.done and q.t_done is not None and now - q.t_done > ORPHAN_S)]
        # the paper follows the leading unfinished pen, rate-limited, never backwards; idle it drifts on
        lead = next((q for q in self.old + [ph] if q.ruler is not None and not q.done), None)
        a_ff = 1.0 - math.exp(-dt / VFF_TAU) if dt > 0 else 1.0
        v_idle = V_IDLE_SP * sp
        if lead is not None and lead.metre is None:     # before the metre: at the provisional pace
            target, vff = self.S, max(v_idle, V_PRE * lead.R_prov)
            self.vff += (vff - self.vff) * a_ff
            lead.pen_u_prev = lead.pen_u
        elif lead is not None:
            target = lead.pen_u - self.X_p
            v_pen = (lead.pen_u - lead.pen_u_prev) / dt if (dt > 0 and lead.pen_u_prev is not None) else 0.0
            lead.pen_u_prev = lead.pen_u
            self.vff += (v_pen - self.vff) * a_ff
            vff = self.vff
            if lead.v_music is None:
                lead.v_music = lead.R_prov
            lead.v_music += (v_pen - lead.v_music) * (1.0 - math.exp(-dt / 4.0) if dt > 0 else 0.0)
        else:
            target, vff = self.S, v_idle
            self.vff += (v_idle - self.vff) * a_ff
        if big:
            self.S, self.V = max(self.S, target), max(v_idle, vff)
        elif dt > 0:
            amax = S_AMAX_SP * sp
            err, v_rel = target - self.S, self.V - vff
            a = S_OMEGA * S_OMEGA * err + 2.0 * S_OMEGA * (vff - self.V)
            if err * v_rel > 0.0 and v_rel * v_rel >= 1.4 * amax * abs(err):
                a = -math.copysign(amax, v_rel)              # brake now or it overshoots (acceleration-limited)
            if lead is not None and lead.metre is not None and lead.v_music:
                # while a phrase plays the paper keeps some pace (the writing may stall on a late analysis),
                # unless the pen is already far left
                floor = V_FLOOR * lead.v_music * smoothstep(0.25, 0.35, (lead.pen_u - self.S - self.X_left)
                                                            / self.tape_w)
                a = max(a, 2.0 * S_OMEGA * (floor - self.V))
            a = min(amax, max(-amax, a))
            self.V = max(v_idle, self.V + a * min(dt, A_DT))
            self.S += self.V * dt
        self._s_free_columns()
        self._s_headers(dt)
        self.hw.step(dt)
        self._s_geom()
        # the lane: the notes ahead of the current phrase's pen, drawn closer when the writing lags
        pen_u = (lead.pen_u if lead is not None else (ph.pen_u if ph.ruler is not None else self.S + self.X_p))
        self.pen_sx = pen_u - self.S
        if ph.ruler is not None:
            if self.lane_u is None or big:
                self.lane_u, self.lane_v = ph.pen_u, 0.0
            else:
                self.lane_u, self.lane_v = spring_step(self.lane_u, self.lane_v, ph.pen_u, LANE_W, dt)
            head = self._s_head(ph)
            D = max(0.0, head - self.lane_u) if head is not None else 0.0
        else:
            D = 0.0
        if big:
            self.Ds, self.Ds_v = D, 0.0
        else:                                            # critically damped: the lane never jolts
            self.Ds, self.Ds_v = spring_step(self.Ds, self.Ds_v, D, 1.0 / D_TAU, dt)
        lane_sx = (self.lane_u - self.S) if self.lane_u is not None else self.pen_sx
        self.Lw = max(sp, self.X_h - lane_sx)
        self.lane_c = self._lane(0.0)[1]
        # ghost bar lines: the next bars' places, through the lane
        ng = 0
        if ph.metre is not None and ph.ruler is not None and self.lane_u is not None:
            k0 = max(0, int(math.floor(ph.pen.p)) + 1)
            for k in range(k0, k0 + NGHOST):
                d = ph.ruler.u0(k) - self.lane_u
                if d <= 0.0:
                    continue
                X = lane_sx + self._lane(d)[0]
                a = GHOST_A * (1.0 - smoothstep(self.X_h, self.X_R, X))
                if X >= self.X_R or a <= 0.003:
                    break
                self.Gh[2 * ng], self.Gh[2 * ng + 1] = X, a
                ng += 1
        # the tape line (the harness reads it like a page line)
        ln = self.tape_line
        amp = (0.55 + 0.5 * e) * (lead.pen.active if lead is not None else 0.0)
        a_up = 1.0 - math.exp(-dt / 0.15) if dt > 0 else 0.0
        a_dn = 1.0 - math.exp(-dt / max(0.1, 0.5 * glow_s)) if dt > 0 else 0.0
        ln.glow += (amp - ln.glow) * (a_up if amp > ln.glow else a_dn)
        ln.fresh = (0.0, 0.0, ln.glow)
        ln.pen_x, ln.open_to, ln.open_a = self.pen_sx, self.X_R, 1.0
        ln.cursor = (self.frontier - self.S) if self.frontier is not None else 0.0
        self.scroll_x, self.scroll_v = self.S, self.V
        self.pen = ph.pen.p
        self.pen_backlog = max(0.0, ph.next_place - ph.pen.p) if ph.ruler is not None else 0.0
        self.pen_line_id = 0
        # the kernel's parameters
        Ph = self.Ph
        c0 = math.floor(self.S)
        Ph[36] = self.TW
        Ph[37] = c0 % self.TW
        Ph[38] = (self.S - c0) if self.V >= 2.0 else 0.0
        Ph[39] = self.y0
        Ph[40] = self.X_left
        Ph[41] = SHIELD_SP * sp
        Ph[42] = self.pen_sx if self.engraver is not None else -1e9
        Ph[43] = FEATHER_SP * sp
        Ph[44] = ln.glow
        wcur = 20.0 * sp
        bar_s = 2.0
        if lead is not None:
            k = int(math.floor(lead.pen.p))
            wcur = lead.ruler.width(k) if k >= 0 else lead.ruler.w_inl
            bar_s = lead.bar_s or b.bar_seconds() or 2.0
        Ph[45] = max(sp, glow_s * max((lead.pen.v if lead is not None else 0.0) * wcur, 0.5 * wcur / bar_s))
        Ph[46], Ph[47] = ln.t1, ln.t2
        Ph[48] = self.x_h0
        Ph[49], Ph[50] = self.slot_a[0], self.slots[0]["w"] if self.slots[0]["ready"] else 0.0
        Ph[51], Ph[52] = self.slot_a[1], self.slots[1]["w"] if self.slots[1]["ready"] else 0.0
        Ph[53] = self.HW
        Ph[54], Ph[55], Ph[56] = 0.0, 0.0, 1.0
        Ph[57] = ng

    # -- glows: the notes being played ------------------------------------------------------------------------
    def _births(self) -> None:
        notes = self.builder.notes
        if not notes or notes[-1].id <= self.last_nid:
            return
        i = len(notes)
        while i > 0 and notes[i - 1].id > self.last_nid:
            i -= 1
        ph = self.ph
        for n in notes[i:]:
            if ph.t0 is None:
                ph.t0 = n.t_on
                if self.layout == "scroll":
                    self._s_start(ph)
            g = self._new_glow(n, ph)
            tgt = self._target(g)
            if tgt is None:
                self.glows.pop()
                continue
            g.home, g.x, g.y = tgt
            g.gx, g.gy = g.x, g.y
            g.edge = self._edge(g)
        self.last_nid = notes[-1].id

    def _edge(self, g: _Glow) -> float:
        if g.pre and g.phrase.metre is None:
            h = g.phrase.hdr_est
            return smoothstep(h + self.sp, h + 4 * self.sp, g.x)
        return 1.0

    def _new_glow(self, n, ph) -> _Glow:
        self.gid += 1
        g = _Glow(self.gid, n, ph, self.builder._staff(n), self.t_sound)
        if len(self.glows) >= MAX_GLOWS:                          # the hard bound: the faintest goes
            i = min(range(len(self.glows)), key=lambda i: self.glows[i].presence * self.glows[i].inten)
            self.glows.pop(i)
        self.glows.append(g)
        return g

    def _pitch_y(self, ln: _Line, pitch: int, fifths: int, staff: int) -> float:
        key = (pitch, fifths, staff)
        off = self.spells.get(key)
        if off is None:
            from stream_service.score.mei import signature, spell
            letter, _alt, octave = spell(pitch, fifths, signature(fifths))
            d = "cdefgab".index(letter) + 7 * octave
            off = 4 * self.sp - (d - (30 if staff == 0 else 18)) * self.sp * 0.5
            if len(self.spells) > 4096:
                self.spells.clear()
            self.spells[key] = off
        return (ln.t1 if staff == 0 else ln.t2) + off

    def _where(self, g: _Glow) -> None:
        """The glow's bar and place in it, from the builder (only while its phrase is the builder's)."""
        g.dirty = False
        if g.phrase is not self.ph:
            return
        w = self.builder.where(g.note.t_on)
        if w is None or w[2] or self.ph.metre is None:
            g.k, g.pre = None, True
            return
        k = w[0] - g.phrase.k_base
        if k < 0:                                           # (scroll, after a rewrite) written before the anchor
            g.k, g.pre = None, False
            g.fade = g.fade or 0.6
            return
        g.k, g.frac, g.pre = k, w[1], False

    def _target(self, g: _Glow):
        """(home line, x, y) in the line's canvas, or None (no line for it)."""
        ph = g.phrase
        n = g.note
        if ph.killing:                                  # a rewrite is un-writing: the notes hold where they are
            return (g.home, g.gx, g.gy) if g.home is not None else None
        if g.dirty:
            self._where(g)
        if g.pre or g.k is None:
            ln = ph.lines.get(0)
            if ln is None or ph.t0 is None:
                return None
            x = ph.hdr_est + self.lpad + (n.t_on - ph.t0) * ph.R_prov - ph.conv
            return ln, x, self._pitch_y(ln, n.pitch, ln.fifths, g.staff)
        if ph is self.ph and not ph.plan.covers(g.k):
            self._ensure_plan(ph, g.k)
        if not ph.plan.lines:
            return None
        j = ph.plan.line_of(g.k)
        ln = ph.lines.get(j)
        if ln is None:
            return None
        pl = ln.placed.get(g.k)
        if pl is not None:
            hd = ln.heads.get(n.id)
            if hd is not None:
                return ln, hd[0], hd[1]
            x0, wk = pl
        else:
            L = ph.plan.line(j)
            x0, wk = ph.plan.slot_x0(j, g.k), L.bw
        x = x0 + self.lpad + g.frac * max(self.sp, wk - self.lpad - self.rpad)
        return ln, x, self._pitch_y(ln, n.pitch, ln.fifths, g.staff)

    def _retired(self, g: _Glow) -> bool:
        """The pen has reached the glow's note (its ink is showing)."""
        ph = g.phrase
        if g.pre or g.k is None or ph.metre is None or not ph.plan.lines or g.home is None:
            return False
        pj = ph.plan.line_of(math.floor(ph.pen.p + 1e-9))
        if pj > g.home.j:
            return True
        return pj == g.home.j and g.home.pen_x >= g.x - RETIRE_AHEAD_SP * self.sp

    def _glows_step(self, f, dt: float, big: bool) -> int:
        sp = self.sp
        now = self.t_sound
        lv = f.notes[:, 0] if f is not None else None
        ph = self.ph
        # before the metre: the notes drift right to left on the phrase's first line
        for q in (ph,):
            if q.t0 is not None and q.metre is None:
                x_new = q.hdr_est + self.lpad + (now - q.t0) * q.R_prov
                goal = max(0.0, x_new - CONVEYOR_AT * self.cw)
                if big:
                    q.conv, q.conv_v = goal, 0.0
                else:
                    q.conv, q.conv_v = spring_step(q.conv, q.conv_v, goal, CONVEYOR_W, dt)
        self.glows = [g for g in self.glows
                      if not ((g.state != "live" or g.fade) and g.presence * max(g.inten, 0.05) < 0.05)]
        newest: dict = {}
        for g in self.glows:
            if g.state == "live":
                p = g.note.pitch
                if g.note.id > newest.get(p, -1):
                    newest[p] = g.note.id
        a_pre = 1.0 - math.exp(-dt / NOTE_PRE_S) if dt > 0 else 1.0
        rel = math.exp(-dt / REL_TAU) if dt > 0 else 1.0
        add = []
        lines = set(id(ln) for ln in self.lines)
        tele = TELEPORT_SP * sp
        for g in self.glows:
            n = g.note
            if g.phrase is ph and g.state == "live":
                g.staff = self.builder._staff(n)
            # envelope: held and the newest of its pitch reads the live level; else it falls to the floor
            if n.t_off is None and newest.get(n.pitch) == n.id and lv is not None and 0 <= n.pitch < 128:
                g.tgt = 0.55 + 0.45 * float(lv[n.pitch])
            else:
                g.tgt = REL_FLOOR + (g.tgt - REL_FLOOR) * rel
            g.inten += min(SLEW_UP * dt, 0.2, max(-SLEW_DN * dt, -0.1, g.tgt - g.inten))
            g.bloom = 1.0 + (BLOOM - 1.0) * (1.0 - smoothstep(0.0, 0.25, now - g.born))
            if g.state == "live":
                tgt = self._target(g)
                if tgt is not None and id(tgt[0]) in lines:
                    home, tx, ty = tgt
                    jump = g.home is not home or abs(tx - g.x) > tele or abs(ty - g.y) > tele
                    vis = g.home is not None and id(g.home) in lines and g.presence * g.inten * g.edge > 0.02
                    hold = jump and vis and len(self.glows) + len(add) >= MAX_LIVE - CAP_HEADROOM
                    if hold:
                        jump = False                                   # no room for a ghost yet: it waits
                    elif jump:
                        if vis:                                        # crossfade: this one stays as a ghost ...
                            g.state, g.t_state = "ghost", now
                            self.gid += 1
                            new = _Glow(self.gid, n, g.phrase, g.staff, g.born)
                            new.k, new.frac, new.pre, new.dirty = g.k, g.frac, g.pre, False
                            new.inten, new.tgt, new.bloom = g.inten, g.tgt, g.bloom
                            add.append(new)
                        else:
                            new = g
                        new.presence = 0.0                             # ... and a new one rises at the target
                        new.home, new.x, new.y, new.vx, new.vy, new.gx, new.gy = home, tx, ty, 0.0, 0.0, tx, ty
                        new.edge = self._edge(new)
                    if not jump:
                        if big:
                            g.x, g.y, g.vx, g.vy, g.gx, g.gy = tx, ty, 0.0, 0.0, tx, ty
                        elif not hold:
                            g.gx += (tx - g.gx) * a_pre
                            g.gy += (ty - g.gy) * a_pre
                else:
                    g.fade = g.fade or GHOST_OUT_S                     # no line for it: it fades where it is
            # one motion step a frame for every instance (a ghost's or a retiring note's target stays put)
            if not big:
                g.x, g.vx = spring_step(g.x, g.vx, g.gx, NOTE_W, dt)
                g.y, g.vy = spring_step(g.y, g.vy, g.gy, NOTE_W, dt)
            rate = 0.0
            if g.state == "live":
                if self._retired(g):
                    g.state, g.t_state = "retiring", now
                elif (g.k is not None and not g.pre and g.k < g.phrase.committed and g.k >= g.phrase.next_place
                      and now - g.phrase.t_commit.get(g.k, now) > ORPHAN_S):
                    g.fade = g.fade or 0.6                             # its bar never came
                g.edge += min(dt / FADE_IN_S, 0.13, max(-dt / FADE_IN_S, -0.13, self._edge(g) - g.edge))
            if g.state == "ghost":
                rate = 1.0 / GHOST_OUT_S
            elif g.state == "retiring":
                rate = 1.0 / RETIRE_S
                g.shrink = 0.85 + 0.15 * g.presence
            if g.fade:
                rate = max(rate, 1.0 / g.fade)
            if rate:                                                   # the fastest fade that applies, a frame's worth
                g.presence = max(0.0, g.presence - min(dt, 1 / 30) * rate)
            elif g.state == "live":
                g.presence = min(1.0, g.presence + min(dt, 1 / 30) / FADE_IN_S)
        self.glows.extend(add)
        # too many: ghosts fade first, then the retiring, then the oldest written (fading ones gone below 0.05 were
        # removed at the top of the step: each is drawn faint at least once)
        excess = len(self.glows) - (MAX_LIVE - CAP_HEADROOM)
        if excess > 0:
            order = sorted(self.glows, key=lambda g: (0 if g.state == "ghost" else 1 if g.state == "retiring"
                                                      else 2 if (g.k is not None and g.k < g.phrase.committed)
                                                      else 3, g.gid))
            for g in order:
                if excess <= 0:
                    break
                if not g.fade or g.fade > 0.15:
                    g.fade = 0.15
                excess -= 1
        return self._emit()

    def _emit(self) -> int:
        """The visible glows (the brightest MAX_LIVE), x-sorted into L, their bounding box into P."""
        sp = self.sp
        rows = []
        dbg = []
        b = self.builder
        scroll = self.layout == "scroll"
        for g in self.glows:
            ln = g.home
            if ln is None:
                continue
            if scroll:                                      # ahead of the pen through the lane, else on the paper
                d = g.x - self.lane_u if self.lane_u is not None else -1.0
                X = (self.lane_u - self.S + self._lane(d)[0]) if d > 0.0 else g.x - self.S
                Y = self.y0 + g.y
                a = smoothstep(self.X_left, self.X_left + SHIELD_SP * sp, X) * (
                    1.0 - smoothstep(0.5 * (self.X_h + self.X_R), self.X_R + 2.0 * sp, X))
            else:
                a = ln.alpha if ln in self.lines else 0.0
                X = self.x_off + g.x
                Y = ln.y + g.y
            I = g.inten * g.presence * a * g.edge
            if I <= 0.004:
                continue
            s = g.bloom * g.shrink
            rows.append((X, Y, 0.62 * sp * s, 0.48 * sp * s, I, ((g.note.pitch * 7) % 12) / 12.0, g))
        if len(rows) > MAX_LIVE:
            rows.sort(key=lambda r: -r[4])
            rows = rows[:MAX_LIVE]
        rows.sort(key=lambda r: r[0])
        n = len(rows)
        if n:
            p = self._params
            cols = _pal3(self.Ph[0:12], np.array([r[5] for r in rows]) * float(p["palette_period"]) + self.drift % 10.0)
            arr = np.array([r[:5] for r in rows], np.float32)
            Lh = self.Lh
            Lh[:8 * n].reshape(n, 8)[:, :5] = arr
            Lh[:8 * n].reshape(n, 8)[:, 5:8] = cols
            rc = RC_SP * sp
            self.bbox = (float(arr[:, 0].min() - rc), float(arr[:, 1].min() - rc),
                         float(arr[:, 0].max() + rc), float(arr[:, 1].max() + rc))
            for r in rows:
                g = r[6]
                pos = None
                if g.phrase is self.ph and not g.pre and g.k is not None:
                    pos = g.k + g.phrase.k_base + g.frac - b.next_bar
                dbg.append((g.gid, round(r[0], 2), round(r[1], 2), round(r[4], 4), g.home.id, pos))
        else:
            self.bbox = (0.0, 0.0, 0.0, 0.0)
        self.live_debug = dbg
        return n

    # -- render -------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        self._params = p
        big = dt > 0.5
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
        f = self._listen()
        self._take_renders()
        glow_s = max(0.05, float(p["ink_glow"]))
        if self.layout == "scroll":
            self._scroll_step(dt, glow_s, e, big)
        else:
            if big:
                self.turn.snap()
            self._page_step(dt, glow_s, e)
        nl = self._glows_step(f, dt, big)
        Ph[12], Ph[13] = self.w, self.h
        Ph[14] = LOOKS.index(p["look"])
        Ph[15] = self.breath % (2.0 * math.pi)
        Ph[16] = e
        Ph[17] = self.kick
        Ph[18] = float(p["intensity"])
        Ph[19] = float(p["note_glow"])
        Ph[20] = self.drift % 10.0
        Ph[21] = float(p["palette_period"])
        Ph[22:26] = self.bbox
        Ph[26], Ph[27] = self.cw, self.ch
        Ph[28] = self.sp
        Ph[29] = self.th
        Ph[30:33] = _pal3(Ph[0:12], self.drift % 10.0 + 0.1)
        Ph[33] = RC_SP * self.sp
        self.P.set(Ph)
        if nl:
            self.L.set(self.Lh)
        if self.layout == "scroll":
            self.G.set(self.Gh)
            launch_2d(self.k, self.w, self.h, (self.buf, self.P, self.tape, self.colok, self.hdr, self.G, self.L,
                                               np.int32(nl), self.page))
        else:
            self.R.set(self.Rh)
            self.RI.set(self.RIh)
            launch_2d(self.k, self.w, self.h, (self.buf, self.P, self.canvas, self.R, self.RI, self.L,
                                               np.int32(nl), self.page))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        if self.engraver is not None:
            self.engraver.close()
            self.engraver = None
        if self.analyser is not None:
            self.analyser.close()
            self.analyser = None
        self.buf = self.canvas = self.P = self.R = self.RI = self.L = self.page = self.gstage = None
        self.tape = self.colok = self.hdr = self.G = None
        self.stage = self._pinned = None
