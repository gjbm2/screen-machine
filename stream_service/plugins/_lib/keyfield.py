"""Key field: keyscape_live's analysis as a shared object, so other scenes can draw with it.  For every moment and every
listening span it holds the key of the music, how clear that key is, how much sound there was, and the Wavescapes fifth
coefficient.  The analysis is baked into three small GPU planes.

Source: Original (inspired by C. S. Sapp, "Visual hierarchical key analysis", ACM Computers in Entertainment 3(4),
        2005, https://esf.ccarh.org/254-old/254_LiteraturePack1/Harmony/Sapp_VisualHierKeyAnal05.pdf; C. Viaccoz,
        D. Harasim, F. C. Moss, M. Rohrmeier, "Wavescapes: a visual hierarchical analysis of tonal music using
        discrete Fourier transform", Musicae Scientiae 2022, https://infoscience.epfl.ch/record/291958.  Their
        wavescapes code is GPL-3.0: nothing of it is used here, only the published formulas.  Key profiles:
        Krumhansl & Kessler 1982, as show_service.  The maths, constants and colour rule are keyscape_live's.)
Author: smfx (original)
License: MIT (original)
license_class: permissive

What it is for
  keyscape_live draws its analysis one way: Sapp's triangle as a stained-glass mountain.  The analysis itself is a
  2-D field over (moment, listening span), and other scenes can use it as input: a landscape to fly over, a
  deformation field over another picture, a colour field, a terrain of distances from home.  This module keeps that
  field once per screen and bakes it into GPU planes that any plug-in samples.
  * :class:`KeyTimeline` (host, numpy): the piece's pitch-class content in 0.5 s bins, as prefix sums.  It is shared:
    every key-field scene on one screen reads the same timeline (``KeyTimeline.shared(self.sound)``), so a scene that
    follows another one continues the same piece instead of starting again.
  * :class:`KeyField` (GPU, one per plug-in instance): one kernel launch per frame turns the timeline into planes A,
    B and C ([fh, fw, 4] float32, 384 x 192 by default) for a chosen view.  ``KEYFIELD_CUDA`` holds the device
    helpers that plug-in kernels paste in to sample the planes and colour keys.

The maths (keyscape_live's, unchanged)
  1. Time is cut into 0.5 s bins.  Each bin gets, per pitch class, the sum of the sounding LEVEL of its notes x dt
     (from the notes table, so pedal-held notes count), plus ONSET_W (0.3) x the velocity of every strike.  Silence
     longer than QUIET_HOLD (2 s) stops the clock (a rest is music, a pause is not).
  2. The prefix sums S_j = sum over b < j of h_b give the content of any window [s, e) (in bins) as H = S(e) - S(s),
     linear between rows for fractional ends.  The open bin counts as in keyscape: row n + 1 is the provisional row
     S_n + acc / max(tbin / BIN_S, 0.25).
  3. Key: d = H - mean(H), var = d.d / 12, r_k = (Z_k . d) / sqrt(var) for the 24 z-scored Krumhansl-Kessler
     profiles Z (tonal.KK_Z, i.e. show_service's rows): Pearson's r with each key.  Best k1 (r1), runner-up r2;
     clarity = clamp((r1 - r2) / 0.1) x clamp(r1 / 0.5).  fog = 1 - smoothstep(0.02, 0.15, sum H / length): an
     empty window is mist.  A window is keyed when var > 1e-9 + 1e-6 mean^2, sum H > 1e-5 and fog < 0.6.
  4. Colour in OKLCh: hue = 32 deg + 30 deg x (7 tonic mod 12) (+ hue shift), so the hues follow the circle of
     fifths; L 0.71 major / 0.50 minor (eased toward the runner-up's L at a near tie, so a boundary does not step);
     C = 0.15 x clarity; then mixed toward the mist (L 0.40, a -0.004, b -0.018) by fog.
  5. Wavescapes: F5 = sum_n H_n exp(-2 pi i 5 n / 12).  Its phase turns 30 deg per step around the circle of fifths;
     |F5| / sum H is the "diatonicity" (a major scale 0.53, a triad 0.64, a diminished seventh 0).

The view: u, v and the window
  ``bake`` looks at the bins [org, org + span].  With ``span`` None that is the whole piece so far: at least V_MIN
  bins (20 s, so a new piece grows in from the left) and at most CAP (the last 30 min).  Otherwise it is ``span``
  bins ending at now.  u in 0..1 runs left to right across the view.  v in 0..1 is the window length L(v) =
  mix(L_MIN + (span - L_MIN) v, L_MIN (span / L_MIN)^v, log_rows): row 0 holds the shortest windows (1 s), the top
  row windows as long as the view.  Texel (x, y) sits at u = (x + 0.5) / fw, v = (y + 0.5) / fh, so row 0 is v ~ 0.
  * CENTRED (0): the window is centred on u.  This is Sapp's triangle: a cell is inside where the whole window lies
    within [lo, now].
  * TRAILING (1): the window ends at u.  It is what had been heard up to that moment, at most L back, cut at the
    start of the piece.  This field is a rectangle: every cell from lo to now is inside.
  ``lo`` = max(0, now - CAP): the start of the piece, or 30 min back in a longer one.  So zoomed views (a short
  ``span``) still see the history to their left, and nothing jumps when the timeline drops its oldest 5 min.
  Cells outside are still evaluated, over the part of their window that lies in [lo, now] (density divides by that
  part's length).  Plug-ins can extend a surface past the edge and fade it by ``inside``.

Planes (cupy float32 [fh, fw, 4]; pass them to kernels as ``const float4*``)
  A = (key 0..23 or -1, clarity 0..1, fog 0..1, inside 0..1 with a soft one-texel edge)
  B = (r, g, b, home): the key's colour in sRGB 0..1 (point 4), and home = the circle-of-fifths distance of the
      cell's key from the summit key, / 6, 0..1.  A minor key sits at its relative major's place (a minor is home in
      C).  Home is 1 for an unkeyed cell, and 1 everywhere while the piece has no summit key.
  C = (cos F5 phase, sin F5 phase, |F5| / sum H, density): the phase as a unit vector ((0, 0) when F5 vanishes),
      the diatonicity, and density = clamp((sum H / length - 0.02) / 0.13): the linear input of the fog smoothstep,
      so 1 - fog = density^2 (3 - 2 density).
  The summit key is ``KeyTimeline.summit()``: the key of the whole piece, i.e. the top cell of the default view.

Timeline life
  Cold start: until the first note ever, the timeline holds keyscape's synthetic example (8 bars I-IV-V-I in C, a
  modulation to G, back to C; 120 bins, no open bin), with ``example`` True.  The first note clears it and starts the
  live piece.  After ``piece_gap`` seconds of silence the piece freezes, and the next note starts a new one (n back
  to 0, ``pieces`` + 1).  If the frames' clock (``frame.t_ms``) jumps by more than ``piece_gap`` between two updates
  (no key-field scene was on screen meanwhile), the piece freezes too, because what was played in between was never
  heard.  At CAP + DROP closed bins the oldest DROP rows are dropped (re-based); every window inside the last CAP keeps
  its content.  Each change to S bumps ``version``.  A KeyField syncs only the rows changed since its own last sync
  (``dirty_since``), and everything after a reset or re-base.

Cost
  update: ~20 us of numpy per frame, once per screen however many plug-ins call it.  sync: one 48-byte row per closed
  bin.  bake: one 128-byte arena upload and one launch over fw x fh texels (73,728 by default: two prefix lookups and
  288 multiply-adds each, well under 0.1 ms on the P4000).  A bake whose inputs are unchanged (the example, a frozen
  piece) launches nothing.
"""
from __future__ import annotations

import logging
import math
import threading
import weakref
from collections import deque
from typing import Any, Optional

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib import tonal as T
from stream_service.plugins._lib.cuda import HELPERS, launch_2d

log = logging.getLogger("stream_service.plugins._lib.keyfield")

# ---------------------------------------------------------------------------------------------------- constants
BIN_S = 0.5                     # one bin of the timeline (s)
CAP = 3600                      # bins a view spans at most (30 min)
DROP = 600                      # rows dropped (re-based) when the buffer is full, 5 min at a time
ROWS = CAP + DROP + 2           # prefix-sum rows
L_MIN = 2.0                     # the shortest window (bins): 1 s
V_MIN = 40.0                    # the default view spans at least 20 s
CENTRED = 0                     # the window centred on the u position (Sapp's triangle)
TRAILING = 1                    # the window ending at the u position (what had been heard by then; a rectangle)
QUIET_HOLD = 2.0                # silence longer than this stops the clock
ONSET_W = 0.3                   # a strike adds this x its velocity to its pitch class's bin
MAX_ONSETS = 64                 # strikes read per frame
MAX_BINS = 8                    # bins closed per update at most (a long skip settles)
LOG_MAX = 512                   # versions remembered for dirty_since
STRONG_MAX = 16                 # shared timelines held for sound objects that take no weak reference

# colours (OKLCh), keyscape_live's
H0_DEG = 32.0                   # the hue of C; G is +30, D +60 ...
L_MAJ, L_MIN_ = 0.71, 0.50      # lightness of major / minor keys
C_KEY = 0.15                    # chroma at full clarity
FIFTH_ARG_C = 60.0              # arg F5 (deg) of a C major scale: maps the wave colour onto the key colour of C
MIST = (0.40, -0.004, -0.018)   # the fog's OKLab colour
LABEL_MIN_AREA = 8              # regions(): cells
LABEL_MIN_CLAR = 0.4            # regions(): mean clarity

# the bake's arena (floats)
N_AR = 32
O_OPEN = 16                     # the open bin's provisional row (12)

_KZ = ",".join(f"{v:.9e}f" for v in T.KK_Z.astype(np.float32).ravel())
_C5_NP = np.array([math.cos(2 * math.pi * 5 * n / 12) for n in range(12)])
_S5_NP = np.array([-math.sin(2 * math.pi * 5 * n / 12) for n in range(12)])
_C5 = ",".join(f"{v:.9e}f" for v in _C5_NP)
_S5 = ",".join(f"{v:.9e}f" for v in _S5_NP)

# ------------------------------------------------------------------------------------------------- device helpers
KEYFIELD_CUDA = r"""
// ---- keyfield device helpers (stream_service/plugins/_lib/keyfield.py); paste after _lib/cuda.py HELPERS ----
#define KF_H0 """ + repr(H0_DEG) + r"""f
#define KF_L_MAJ """ + repr(L_MAJ) + r"""f
#define KF_L_MINOR """ + repr(L_MIN_) + r"""f
#define KF_C_KEY """ + repr(C_KEY) + r"""f
#define KF_ARG_C """ + repr(FIFTH_ARG_C) + r"""f
#define KF_DEG 0.017453293f
#define KF_STEP 0.52359878f

// linear sRGB -> encoded, clamped to 0..1
__device__ __forceinline__ float kf_enc(float x) {
    x = clamp01(x);
    return x <= 0.0031308f ? 12.92f * x : 1.055f * __powf(x, 1.f / 2.4f) - 0.055f;
}
__device__ __forceinline__ float3 kf_oklab_lin(float L, float a, float b) {
    const float l_ = L + 0.3963377774f * a + 0.2158037573f * b;
    const float m_ = L - 0.1055613458f * a - 0.0638541728f * b;
    const float s_ = L - 0.0894841775f * a - 1.2914855480f * b;
    const float l = l_ * l_ * l_, m = m_ * m_ * m_, s = s_ * s_ * s_;
    return make_float3(4.0767416621f * l - 3.3077115913f * m + 0.2309699292f * s,
                       -1.2684380046f * l + 2.6097574011f * m - 0.3413193965f * s,
                       -0.0041960863f * l - 0.7034186147f * m + 1.7076147010f * s);
}
// OKLab (L, a, b) -> sRGB 0..1
__device__ __forceinline__ float3 kf_oklab_srgb(float3 lab) {
    const float3 c = kf_oklab_lin(lab.x, lab.y, lab.z);
    return make_float3(kf_enc(c.x), kf_enc(c.y), kf_enc(c.z));
}
__device__ __forceinline__ float kf_fin(float x) { return (x == x && fabsf(x) < 1e30f) ? x : 0.f; }
// a plane's key value -> 0..23, or -1 (also for NaN and anything out of range)
__device__ __forceinline__ int kf_key_id(float key) {
    return (key > -0.5f && key < 23.5f) ? (int)(key + 0.5f) : -1;
}
// the key's colour in OKLab: hue by the circle of fifths, major light / minor deep, chroma x clarity; -1 -> the mist
__device__ __forceinline__ float3 kf_key_lab(float key, float clarity, float hue_shift_deg) {
    const int k = kf_key_id(key);
    if (k < 0) return make_float3(0.40f, -0.004f, -0.018f);
    const float hue = (KF_H0 + kf_fin(hue_shift_deg) + 30.f * (float)((7 * (k % 12)) % 12)) * KF_DEG;
    const float ch = KF_C_KEY * clamp01(clarity);
    return make_float3(k >= 12 ? KF_L_MINOR : KF_L_MAJ, ch * __cosf(hue), ch * __sinf(hue));
}
// mix an OKLab colour toward the mist by fog 0..1 (as plane B)
__device__ __forceinline__ float3 kf_mist(float3 lab, float fog) {
    fog = clamp01(fog);
    return make_float3(lab.x + (0.40f - lab.x) * fog, lab.y + (-0.004f - lab.y) * fog, lab.z + (-0.018f - lab.z) * fog);
}
// the key's colour in sRGB 0..1 (plane B's rule without the fog or the near-tie easing)
__device__ __forceinline__ float3 kf_key_rgb(float key, float clarity, float hue_shift_deg) {
    return kf_oklab_srgb(kf_key_lab(key, clarity, hue_shift_deg));
}
// keyscape's wavescape colour from a plane C texel (F5 phase -> hue, diatonicity -> chroma and lightness), OKLab
__device__ __forceinline__ float3 kf_wave_lab(float4 c, float hue_shift_deg) {
    const float m = c.x * c.x + c.y * c.y;
    const bool ph = m > 1e-12f;
    const float dia = clamp01(c.z);
    const float hw = (KF_H0 + kf_fin(hue_shift_deg) - KF_ARG_C) * KF_DEG + (ph ? atan2f(c.y, c.x) : 0.f);
    const float cw = ph ? fminf(KF_C_KEY * dia / 0.53f, 0.19f) : 0.f;
    return make_float3(0.58f + 0.14f * clamp01(dia / 0.53f), cw * __cosf(hw), cw * __sinf(hw));
}
__device__ __forceinline__ float3 kf_wave_rgb(float4 c, float hue_shift_deg) {
    return kf_oklab_srgb(kf_wave_lab(c, hue_shift_deg));
}
// a key's place 0..11 around the circle of fifths (C 0, G 1, D 2 ... F 11); a minor key at its relative major's
__device__ __forceinline__ int kf_fifths_pos(int k) {
    const int t = k % 12;
    const int maj = k >= 12 ? (t + 3) % 12 : t;
    return (7 * maj) % 12;
}
// the key's angle (radians, 0..2 pi) on the circle of fifths: C 0, G pi/6 ...; a minor at its relative major's; -1 -> 0
__device__ __forceinline__ float kf_key_angle(float key) {
    const int k = kf_key_id(key);
    return k < 0 ? 0.f : KF_STEP * (float)kf_fifths_pos(k);
}
// the circle-of-fifths distance of key from summit, / 6 (0 home .. 1 the tritone away); 1 when either is -1
__device__ __forceinline__ float kf_home(float key, float summit) {
    const int a = kf_key_id(key), b = kf_key_id(summit);
    if (a < 0 || b < 0) return 1.f;
    int d = abs(kf_fifths_pos(a) - kf_fifths_pos(b));
    d = min(d, 12 - d);
    return (float)d * (1.f / 6.f);
}
// bilinear sample of a plane at (u, v) in 0..1 (texel centres at (i + 0.5) / size), clamped to the edge
__device__ __forceinline__ float4 kf_tex(const float4* __restrict__ P, int fw, int fh, float u, float v) {
    const float fx = fminf(fmaxf(u * (float)fw - 0.5f, 0.f), (float)(fw - 1));
    const float fy = fminf(fmaxf(v * (float)fh - 0.5f, 0.f), (float)(fh - 1));
    const int x0 = (int)fx, y0 = (int)fy;
    const int x1 = min(x0 + 1, fw - 1), y1 = min(y0 + 1, fh - 1);
    const float tx = fx - (float)x0, ty = fy - (float)y0;
    const float4 a = P[y0 * fw + x0], b = P[y0 * fw + x1], c = P[y1 * fw + x0], d = P[y1 * fw + x1];
    const float sx = 1.f - tx, sy = 1.f - ty;
    return make_float4(sy * (sx * a.x + tx * b.x) + ty * (sx * c.x + tx * d.x),
                       sy * (sx * a.y + tx * b.y) + ty * (sx * c.y + tx * d.y),
                       sy * (sx * a.z + tx * b.z) + ty * (sx * c.z + tx * d.z),
                       sy * (sx * a.w + tx * b.w) + ty * (sx * c.w + tx * d.w));
}
// nearest texel (for key ids), clamped to the edge
__device__ __forceinline__ float4 kf_tex_nearest(const float4* __restrict__ P, int fw, int fh, float u, float v) {
    const int x = min((int)fminf(fmaxf(u * (float)fw, 0.f), (float)fw), fw - 1);
    const int y = min((int)fminf(fmaxf(v * (float)fh, 0.f), (float)fh), fh - 1);
    return P[y * fw + x];
}
// ---- end keyfield helpers ----
"""

_SRC = HELPERS + KEYFIELD_CUDA + r"""
#define KF_ROWS """ + str(ROWS) + r"""
#define KF_NAR """ + str(N_AR) + r"""
#define KF_O_OPEN """ + str(O_OPEN) + r"""

__constant__ float KF_KZ[288] = {""" + _KZ + r"""};
__constant__ float KF_C5[12] = {""" + _C5 + r"""};
__constant__ float KF_S5[12] = {""" + _S5 + r"""};

// one prefix-sum row (12 floats); rows after the last closed one read the open bin's provisional row
__device__ __forceinline__ void kf_row12(const float4* __restrict__ S, int j, int n, const float* OPEN, float* o) {
    if (j > n) {
        #pragma unroll
        for (int k = 0; k < 12; ++k) o[k] = OPEN[k];
        return;
    }
    const float4* R = S + (size_t)j * 3;
    const float4 a = R[0], b = R[1], c = R[2];
    o[0] = a.x; o[1] = a.y; o[2] = a.z; o[3] = a.w; o[4] = b.x; o[5] = b.y;
    o[6] = b.z; o[7] = b.w; o[8] = c.x; o[9] = c.y; o[10] = c.z; o[11] = c.w;
}
// S(x) at a fractional bin position, linear between rows (keyscape's prefix)
__device__ __forceinline__ void kf_prefix(const float4* __restrict__ S, int n, int open, const float* OPEN,
                                          float x, float* o) {
    const int nmax = n + open;
    const float fx = floorf(x);
    int i = (int)fx;
    float f = x - fx;
    if (i >= nmax) { i = nmax - 1; f = 1.f; }
    if (i < 0) { i = 0; f = 0.f; }
    float a[12], b[12];
    kf_row12(S, i, n, OPEN, a);
    kf_row12(S, i + 1, n, OPEN, b);
    #pragma unroll
    for (int k = 0; k < 12; ++k) o[k] = a[k] + f * (b[k] - a[k]);
}

// the bake: every texel's window -> A (key, clarity, fog, inside), B (sRGB, home), C (F5 cos, sin, |F5|/sum, density)
// arena: [0]fw [1]fh [2]mode [3]span [4]org [5]now [6]n [7]open [8]log_rows [9]hue shift deg [10]lo [11]summit key
//        [12]L_min  [16..27] the open row
extern "C" __global__
void kf_bake(float4* __restrict__ A, float4* __restrict__ B, float4* __restrict__ C,
             const float* __restrict__ Ar, const float4* __restrict__ S)
{
    __shared__ float P[KF_NAR];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    for (int i = tid; i < KF_NAR; i += blockDim.x * blockDim.y) P[i] = Ar[i];
    __syncthreads();
    const int fw = (int)P[0], fh = (int)P[1];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= fw || y >= fh) return;
    const int idx = y * fw + x;
    const int mode = (int)P[2];
    const float Lmin = fmaxf(P[12], 1e-3f);
    const float span = fmaxf(P[3], 2.f * Lmin), org = P[4], now = P[5], lo = P[10];
    const int n = min(max((int)P[6], 0), KF_ROWS - 2), open = P[7] > 0.5f ? 1 : 0;
    const float logr = clamp01(P[8]), hs = kf_fin(P[9]), summit = P[11];
    const float u = ((float)x + 0.5f) / (float)fw, v = ((float)y + 0.5f) / (float)fh;
    const float Llin = Lmin + (span - Lmin) * v;
    const float Llog = Lmin * __powf(span / Lmin, v);
    const float Lw = Llin + (Llog - Llin) * logr;
    const float pos = org + u * span;
    const float ppb = (float)fw / span;
    float s, e, inside;
    if (mode == 0) {                                  // CENTRED: the whole window within [lo, now]
        s = pos - 0.5f * Lw; e = pos + 0.5f * Lw;
        inside = clamp01(fminf((s - lo) * ppb, (now - e) * ppb) + 0.5f);
    } else {                                          // TRAILING: ends at pos, cut at lo: a rectangle
        e = pos; s = pos - Lw;
        inside = clamp01(fminf((pos - lo) * ppb, (now - pos) * ppb) + 0.5f);
    }
    const float ec = fminf(fmaxf(e, lo), now);
    const float sc = fminf(fmaxf(s, lo), ec);
    const float len = ec - sc;
    float4 oa = make_float4(-1.f, 0.f, 1.f, inside);
    float4 oc = make_float4(0.f, 0.f, 0.f, 0.f);
    float3 lab = make_float3(0.40f, -0.004f, -0.018f);
    float home = 1.f;
    if (n + open >= 1 && now > lo) {
        float Pa[12], Pb[12];
        kf_prefix(S, n, open, P + KF_O_OPEN, sc, Pa);
        kf_prefix(S, n, open, P + KF_O_OPEN, ec, Pb);
        float H[12], sum = 0.f;
        #pragma unroll
        for (int k = 0; k < 12; ++k) { H[k] = fmaxf(Pb[k] - Pa[k], 0.f); sum += H[k]; }
        const float mean = sum * (1.f / 12.f);
        float var = 0.f;
        #pragma unroll
        for (int k = 0; k < 12; ++k) { H[k] -= mean; var += H[k] * H[k]; }
        var *= (1.f / 12.f);
        const float dn = clamp01((sum / fmaxf(len, 1e-3f) - 0.02f) * (1.f / 0.13f));
        float fog = 1.f - dn * dn * (3.f - 2.f * dn);
        // the wavescape: the fifth DFT coefficient (unchanged by the mean removal)
        float fr = 0.f, fi = 0.f;
        #pragma unroll
        for (int i = 0; i < 12; ++i) { fr += H[i] * KF_C5[i]; fi += H[i] * KF_S5[i]; }
        const float mag = sqrtf(fr * fr + fi * fi);
        const bool ph = mag > 1e-6f * sum + 1e-9f;
        oc = make_float4(ph ? fr / mag : 0.f, ph ? fi / mag : 0.f, sum > 1e-5f ? clamp01(mag / sum) : 0.f, dn);
        float Lk = 0.6f, ak = 0.f, bk = 0.f;
        if (var > 1e-9f + 1e-6f * mean * mean && sum > 1e-5f) {
            const float inv = rsqrtf(var);
            float r1 = -2.f, r2 = -2.f;
            int k1 = 0, k2 = 0;
            #pragma unroll 4
            for (int k = 0; k < 24; ++k) {
                float acc = 0.f;
                #pragma unroll
                for (int i = 0; i < 12; ++i) acc += KF_KZ[k * 12 + i] * H[i];
                acc *= inv;
                if (acc > r1) { r2 = r1; k2 = k1; r1 = acc; k1 = k; }
                else if (acc > r2) { r2 = acc; k2 = k; }
            }
            const float clar = clamp01((r1 - r2) * 10.f) * clamp01(r1 * 2.f);
            const int t1 = k1 % 12;
            const float hue = (KF_H0 + hs + 30.f * (float)((7 * t1) % 12)) * KF_DEG;
            const float l1 = k1 >= 12 ? KF_L_MINOR : KF_L_MAJ, l2 = k2 >= 12 ? KF_L_MINOR : KF_L_MAJ;
            Lk = l1 + (l2 - l1) * 0.5f * (1.f - smooth(0.f, 0.05f, r1 - r2));     // continuous across a boundary
            const float ch = KF_C_KEY * clar;
            ak = ch * __cosf(hue); bk = ch * __sinf(hue);
            oa.x = fog < 0.6f ? (float)k1 : -1.f;
            oa.y = clar;
        } else {
            fog = 1.f;
        }
        oa.z = fog;
        lab = kf_mist(make_float3(Lk, ak, bk), fog);
        home = kf_home(oa.x, summit);
    } else {
        oa.w = 0.f;                                   // no rows at all: nothing to show
    }
    const float3 rgb = kf_oklab_srgb(lab);
    A[idx] = oa;
    B[idx] = make_float4(rgb.x, rgb.y, rgb.z, home);
    C[idx] = oc;
}
"""

_OPTS = ("--std=c++14", "--use_fast_math")


# ---------------------------------------------------------------------------------------------------- host maths
def _enc(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055


def _oklab_srgb_np(L: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """OKLab arrays -> sRGB 0..1, [..., 3] (the kernel's kf_oklab_srgb)."""
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    lin = np.stack([4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
                    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
                    -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s], axis=-1)
    lin = np.clip(lin, 0.0, 1.0)
    return np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * lin ** (1 / 2.4) - 0.055)


def key_rgb(k: int, hue_shift_deg: float = 0.0, chroma: float = C_KEY) -> tuple[float, float, float]:
    """A key's colour (sRGB 0..1) at the given chroma (C_KEY = full clarity): keyscape's rule, the kernels'
    kf_key_rgb.  -1 -> the mist."""
    if k is None or k < 0:
        L, a, b = MIST
    else:
        hue = math.radians(H0_DEG + hue_shift_deg + 30.0 * ((7 * (k % 12)) % 12))
        L = L_MIN_ if k >= 12 else L_MAJ
        a, b = chroma * math.cos(hue), chroma * math.sin(hue)
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return (_enc(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
            _enc(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
            _enc(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s))


def label(k: int, unicode: bool = True) -> str:
    """Sapp's short key name: 'E♭' (major), 'c♯' (minor); '' for -1."""
    return T.key_label(int(k), short=True, unicode=unicode) if k is not None and 0 <= k < 24 else ""


def fifths_pos(k: int) -> int:
    """A key's place 0..11 around the circle of fifths (C 0, G 1 ... F 11); a minor key at its relative major's."""
    t = int(k) % 12
    maj = (t + 3) % 12 if k >= 12 else t
    return (7 * maj) % 12


def key_angle(k: int) -> float:
    """The key's angle (radians, 0..2 pi) on the circle of fifths, the kernels' kf_key_angle; -1 -> 0."""
    return 0.0 if k is None or not 0 <= k < 24 else 2.0 * math.pi * fifths_pos(k) / 12.0


def home(k: int, summit: int) -> float:
    """The circle-of-fifths distance of key ``k`` from ``summit``, / 6 (0..1): plane B's w, the kernels' kf_home."""
    if k is None or summit is None or not 0 <= k < 24 or not 0 <= summit < 24:
        return 1.0
    d = abs(fifths_pos(k) - fifths_pos(summit))
    return min(d, 12 - d) / 6.0


def window_len(v, span: float, log_rows: float):
    """L(v) in bins (scalar or array): the arithmetic and logarithmic rows mixed by ``log_rows``."""
    lin = L_MIN + (span - L_MIN) * v
    lg = L_MIN * (span / L_MIN) ** v
    return lin + (lg - lin) * log_rows


def view_geometry(tl: "KeyTimeline", span: Optional[float] = None) -> tuple[float, float, float, float]:
    """(org, span, now, lo) in bins of the view ``bake`` uses: span None -> the whole piece (V_MIN..CAP bins from lo),
    else ``span`` bins (2 L_MIN..CAP) ending at now.  lo = max(0, now - CAP): where windows may start."""
    now = float(tl.now())
    lo = max(0.0, now - CAP)
    if span is None:
        org = lo
        sp = min(max(now - org, V_MIN), float(CAP))
    else:
        sp = float(span)
        sp = min(max(sp, 2.0 * L_MIN), float(CAP)) if sp == sp else V_MIN
        org = now - sp
    return org, sp, now, lo


def _rows_np(S: np.ndarray, n: int, open_row: Optional[np.ndarray], j: np.ndarray) -> np.ndarray:
    out = S[np.minimum(j, n)]
    if open_row is not None:
        m = j > n
        if m.any():
            out[m] = open_row
    return out


def _prefix_np(S: np.ndarray, n: int, opn: int, open_row: Optional[np.ndarray], x) -> np.ndarray:
    """S(x) at fractional bin positions (any shape -> shape + (12,)), the kernel's kf_prefix."""
    x = np.nan_to_num(np.asarray(x, np.float64))
    nmax = n + opn
    fl = np.floor(x)
    f = x - fl
    i = fl.astype(np.int64)
    hi = i >= nmax
    i = np.where(hi, nmax - 1, i)
    f = np.where(hi, 1.0, f)
    neg = i < 0
    i = np.where(neg, 0, i)
    f = np.where(neg, 0.0, f)
    orow = open_row if opn else None
    a = _rows_np(S, n, orow, i)
    b = _rows_np(S, n, orow, i + 1)
    return a + f[..., None] * (b - a)


def _evaluate(H: np.ndarray, length: np.ndarray) -> dict:
    """The kernel's per-window maths over windows' content H [..., 12] and lengths [...] (bins), float64."""
    H = np.maximum(H, 0.0)
    tot = H.sum(axis=-1)
    mean = tot / 12.0
    d = H - mean[..., None]
    var = (d * d).sum(axis=-1) / 12.0
    ok = (var > 1e-9 + 1e-6 * mean * mean) & (tot > 1e-5)
    r = (d @ T.KK_Z.T) / np.sqrt(np.where(ok, var, 1.0))[..., None]
    k1 = np.argmax(r, axis=-1)
    r1 = np.take_along_axis(r, k1[..., None], axis=-1)[..., 0]
    r_ = r.copy()
    np.put_along_axis(r_, k1[..., None], -np.inf, axis=-1)
    k2 = np.argmax(r_, axis=-1)
    r2 = np.take_along_axis(r_, k2[..., None], axis=-1)[..., 0]
    clar = np.clip((r1 - r2) * 10.0, 0, 1) * np.clip(r1 * 2.0, 0, 1)
    dn = np.clip((tot / np.maximum(length, 1e-3) - 0.02) / 0.13, 0, 1)
    fog = np.where(ok, 1.0 - dn * dn * (3 - 2 * dn), 1.0)
    key = np.where(ok & (fog < 0.6), k1, -1)
    fr, fi = d @ _C5_NP, d @ _S5_NP
    mag = np.sqrt(fr * fr + fi * fi)
    ph = mag > 1e-6 * tot + 1e-9
    safe = np.where(ph, mag, 1.0)
    return {"key": key, "clar": np.where(ok, clar, 0.0), "fog": fog, "ok": ok, "k1": k1, "k2": k2, "r1": r1,
            "r2": r2, "cos": np.where(ph, fr / safe, 0.0), "sin": np.where(ph, fi / safe, 0.0),
            "dia": np.where(tot > 1e-5, np.clip(mag / np.maximum(tot, 1e-30), 0, 1), 0.0), "dens": dn}


def windows_keys(S: np.ndarray, n: int, s: np.ndarray, e: np.ndarray, length: np.ndarray):
    """Key (or -1), clarity and fog of the windows [s, e) (bins) over the prefix sums S[0..n] (closed bins only):
    keyscape_live's windows_keys, float64."""
    def at(x):
        i = np.clip(np.floor(x).astype(np.int64), 0, max(n - 1, 0))
        f = (x - i)[:, None]
        return S[i] + f * (S[np.minimum(i + 1, n)] - S[i])
    H = np.maximum(at(e) - at(s), 0.0)
    tot = H.sum(axis=1)
    d = H - tot[:, None] / 12.0
    var = (d * d).sum(axis=1) / 12.0
    ok = (var > 1e-9 + 1e-6 * (tot / 12.0) ** 2) & (tot > 1e-5)
    r = d @ T.KK_Z.T / np.sqrt(np.where(ok, var, 1.0))[:, None]
    rows = np.arange(len(r))
    k1 = np.argmax(r, axis=1)
    r1 = r[rows, k1]
    r[rows, k1] = -np.inf
    r2 = r.max(axis=1)
    clar = np.clip((r1 - r2) * 10.0, 0, 1) * np.clip(r1 * 2.0, 0, 1)
    dens = tot / np.maximum(length, 1e-3)
    t = np.clip((dens - 0.02) / 0.13, 0, 1)
    fog = 1.0 - t * t * (3 - 2 * t)
    fog = np.where(ok, fog, 1.0)
    ids = np.where(ok & (fog < 0.6), k1, -1)
    return ids, np.where(ok, clar, 0.0), fog


def regions(ids: np.ndarray, clar: np.ndarray, top: int = 4, min_area: int = LABEL_MIN_AREA,
            min_clar: float = LABEL_MIN_CLAR):
    """Flood-fill a grid of key ids (-1 = none) into regions of one key; the ``top`` biggest clear ones (mean clarity
    > min_clar, area >= min_area), one per key: [(key, area, row, col, depth)] with the deepest cell (most
    4-erosions survived; nearest the centroid on a tie).  keyscape_live's, for placing key names."""
    h, w = ids.shape
    flat = ids.tolist()
    seen = [[False] * w for _ in range(h)]
    found = []
    for r0 in range(h):
        for c0 in range(w):
            k = flat[r0][c0]
            if k < 0 or seen[r0][c0]:
                continue
            stack = [(r0, c0)]
            seen[r0][c0] = True
            cells = []
            while stack:
                r, c = stack.pop()
                cells.append((r, c))
                for rr, cc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                    if 0 <= rr < h and 0 <= cc < w and not seen[rr][cc] and flat[rr][cc] == k:
                        seen[rr][cc] = True
                        stack.append((rr, cc))
            if len(cells) >= min_area:
                idx = np.array(cells)
                mc = float(clar[idx[:, 0], idx[:, 1]].mean())
                if mc > min_clar:
                    found.append((k, len(cells), idx))
    found.sort(key=lambda f: -f[1])
    out, keys = [], set()
    for k, area, idx in found:
        if k in keys:
            continue
        keys.add(k)
        m = np.zeros((h + 2, w + 2), bool)
        m[idx[:, 0] + 1, idx[:, 1] + 1] = True
        depth = np.zeros((h + 2, w + 2), np.int32)
        cur = m
        for _ in range(16):
            if not cur.any():
                break
            depth += cur
            nxt = cur.copy()
            nxt[1:, :] &= cur[:-1, :]
            nxt[:-1, :] &= cur[1:, :]
            nxt[:, 1:] &= cur[:, :-1]
            nxt[:, :-1] &= cur[:, 1:]
            cur = nxt
        dmax = int(depth.max())
        cand = np.argwhere(depth == dmax) - 1
        cy, cx = idx[:, 0].mean(), idx[:, 1].mean()
        best = cand[np.argmin((cand[:, 0] - cy) ** 2 + (cand[:, 1] - cx) ** 2)]
        out.append((int(k), area, float(best[0]), float(best[1]), dmax))
        if len(out) >= top:
            break
    return out


def example_bins(seed: int) -> np.ndarray:
    """The cold-start example's bins [120, 12] (keyscape_live's): 8 bars I-IV-V-I in C, ii-V to G, 6 bars in G, then
    back to C (IV, V7, then I-IV-V7-I-IV-I), a bar = 2.5 s = 5 bins: chord tones, a stronger bass root, a scale
    melody, strikes."""
    rng = np.random.default_rng(seed + 1234)
    C, F, G, Am7, D7, Gg, Cg, D, G7 = ((0, 4, 7), (5, 9, 0), (7, 11, 2), (9, 0, 4, 7), (2, 6, 9, 0), (7, 11, 2),
                                       (0, 4, 7), (2, 6, 9), (7, 11, 2, 5))
    bars = [C, F, G, C, C, F, G, C, Am7, D7, Gg, Cg, D, Gg, Cg, D, F, G7, C, F, G7, C, F, C]
    scales = [T.MAJOR_SCALE] * 24
    tonic = [0] * 8 + [7] * 8 + [0] * 8
    out = np.zeros((len(bars) * 5, 12))
    for b, ch in enumerate(bars):
        sc = [(tonic[b] + i) % 12 for i in scales[b]]
        for j in range(5):
            h = out[b * 5 + j]
            decay = 0.85 ** j
            for i, pc in enumerate(ch):
                h[pc] += (0.30 if i else 0.45) * decay * (0.85 + 0.3 * rng.random())
            h[sc[int(rng.integers(0, 7))]] += 0.16 + 0.1 * rng.random()        # the melody
            if j == 0:
                for pc in ch:
                    h[pc] += ONSET_W * (0.5 + 0.3 * rng.random())
            if j == 3:
                h[ch[0]] += ONSET_W * 0.4
    return out


# ------------------------------------------------------------------------------------------------- the timeline
_SHARED: dict = {}              # id(sound) -> (weakref or the object itself, KeyTimeline)
_STRONG: deque = deque()        # ids of entries holding their sound strongly (no weak reference possible)
_LOCK = threading.Lock()


class KeyTimeline:
    """The piece's pitch-class content in 0.5 s bins, as prefix sums (host, numpy).  One per screen:
    ``KeyTimeline.shared(self.sound)``; every key-field plug-in calls ``update(self.sound, dt, piece_gap)`` each frame
    (only the first call per sound frame advances it).

    Read: ``S`` (float64 [ROWS, 12] prefix sums; rows 0..n valid), ``n`` (closed bins), ``tbin`` (s into the open
    bin), ``acc`` (the open bin's 12 values), ``e`` (this frame's pitch-class energy, float32 [12]), ``started`` (a
    live piece exists), ``frozen``, ``example`` (the cold-start example is showing), ``quiet`` (s since sound),
    ``pieces`` (live pieces begun), ``piece_t`` (s since the current piece, or the example, began), ``version`` and
    ``dirty_from`` (the lowest row changed by the latest change), ``keyr`` (a tonal.KeyResolver: the room's key, or
    its own tracker's)."""

    def __init__(self, seed: int = 0) -> None:
        self.seed = int(seed) & 0x7FFFFFFF
        self.S = np.zeros((ROWS, 12))
        self.n = 0
        self.tbin = 0.0
        self.acc = np.zeros(12)
        self.e = np.zeros(12, np.float32)
        self.started = False
        self.frozen = False
        self.example = False
        self.quiet = 1e9
        self.pieces = 0
        self.piece_t = 0.0
        self.version = 0
        self.dirty_from = 0
        self.keyr = T.KeyResolver()
        self._log: deque = deque(maxlen=LOG_MAX)
        self._open = np.zeros(12)
        self._seen_ons: Any = None
        self._last_f: Any = None
        self._last_t: Optional[float] = None
        self._last_dt = 0.0
        self._tick = 0
        self._summit_at: tuple = (-1, -1)
        self._summit: tuple[int, float] = (-1, 0.0)
        ex = example_bins(self.seed)
        self.S[1:len(ex) + 1] = np.cumsum(ex, axis=0)
        self.n = len(ex)
        self.example = True
        self._changed(0)

    # -- sharing ----------------------------------------------------------------------------------------------------
    @classmethod
    def shared(cls, sound: Any, seed: int = 0) -> "KeyTimeline":
        """The one timeline for this sound object (one screen session); the first caller's seed sets its example.
        ``sound`` None -> a new private timeline per call."""
        if sound is None:
            return cls(seed)
        key = id(sound)
        with _LOCK:
            ent = _SHARED.get(key)
            if ent is not None:
                ref, tl = ent
                obj = ref() if isinstance(ref, weakref.ref) else ref
                if obj is sound:
                    return tl
            for k in [k for k, (r, _t) in _SHARED.items() if isinstance(r, weakref.ref) and r() is None]:
                del _SHARED[k]
            tl = cls(seed)
            try:
                ref = weakref.ref(sound)
            except TypeError:                         # no weak reference (a test's SimpleNamespace): hold a few
                ref = sound
                _STRONG.append(key)
                while len(_STRONG) > STRONG_MAX:
                    old = _STRONG.popleft()
                    e = _SHARED.get(old)
                    if e is not None and not isinstance(e[0], weakref.ref):
                        del _SHARED[old]
            _SHARED[key] = (ref, tl)
            return tl

    # -- versions ---------------------------------------------------------------------------------------------------
    def _changed(self, low: int) -> None:
        self.version += 1
        self.dirty_from = int(low)
        self._log.append((self.version, int(low)))

    def dirty_since(self, version: int) -> int:
        """The lowest row of S changed after ``version`` (ROWS: nothing; 0: everything, also when ``version`` is
        older than the remembered history)."""
        if version >= self.version:
            return ROWS
        if not self._log or self._log[0][0] > version + 1:
            return 0
        low = ROWS
        for v, lw in reversed(self._log):
            if v <= version:
                break
            low = min(low, lw)
        return low

    # -- reading ----------------------------------------------------------------------------------------------------
    def now(self) -> float:
        """Bins so far, including the open bin's fraction (the example has no open bin)."""
        return self.n + (min(self.tbin / BIN_S, 1.0) if self.started else 0.0)

    def open_row(self) -> np.ndarray:
        """The open bin's provisional prefix row, S[n] + acc / max(tbin / BIN_S, 0.25) (keyscape's; S[n] while there
        is no live piece).  A reused array: copy it to keep it."""
        if self.started:
            np.multiply(self.acc, 1.0 / max(self.tbin / BIN_S, 0.25), out=self._open)
            self._open += self.S[self.n]
        else:
            self._open[:] = self.S[self.n]
        return self._open

    def summit(self) -> tuple[int, float]:
        """(key 0..23 or -1, clarity) of the whole piece: the window [lo, now] (the top of the default view)."""
        at = (self._tick, self.version)
        if at == self._summit_at:
            return self._summit
        _org, _sp, now, lo = view_geometry(self)
        opn = 1 if self.started else 0
        res: tuple[int, float] = (-1, 0.0)
        if self.n + opn >= 1 and now > lo:
            P = _prefix_np(self.S, self.n, opn, self.open_row(), np.array([lo, now]))
            ev = _evaluate((P[1] - P[0])[None], np.array([now - lo]))
            res = (int(ev["key"][0]), float(ev["clar"][0]))
        self._summit_at, self._summit = at, res
        return res

    # -- writing ----------------------------------------------------------------------------------------------------
    def update(self, sound: Any, dt: float, piece_gap: float = 12.0) -> None:
        """Advance by this frame.  Idempotent per sound frame: a frame already applied (the same frame object with the
        same ``t_ms``) is skipped, except that a larger ``dt`` than before adds the difference, so the frame counts
        the longest dt any caller passed for it.  A frame refreshed in place must carry an advancing ``t_ms``
        (SoundFrame does).  ``sound`` None: every call advances (use a private timeline)."""
        try:
            dt = float(dt)
        except (TypeError, ValueError):
            dt = 0.0
        dt = dt if 0.0 < dt < math.inf else 0.0
        try:
            gap = float(piece_gap)
        except (TypeError, ValueError):
            gap = 12.0
        gap = gap if 0.0 < gap < math.inf else 12.0
        f = getattr(sound, "frame", None) if sound is not None else None
        if f is not None:
            t = getattr(f, "t_ms", None)
            try:
                t = None if t is None else float(t)
            except (TypeError, ValueError):
                t = None
            same = f is self._last_f and (t == self._last_t if t is not None else self._last_t is None)
            if same:
                extra = dt - self._last_dt
                if extra <= 1e-9:
                    return
                self._last_dt = dt
                self._step(f, extra, gap, fresh=False)
                return
            if t is not None and self._last_t is not None and t == t and self._last_t == self._last_t:
                away = (t - self._last_t) / 1000.0 - dt     # frames nobody here heard (no key-field scene on)
                if away > gap and self.started and not self.frozen:
                    self.frozen = True
                    self.quiet = max(self.quiet, away)
            self._last_f, self._last_t, self._last_dt = f, t, dt
        self._step(f, dt, gap, fresh=True)

    def _step(self, f: Any, dt: float, gap: float, fresh: bool) -> None:
        """keyscape_live._timeline: bin the notes' energy and the strikes, freeze after ``gap`` s of silence."""
        self._tick += 1
        notes = getattr(f, "notes", None) if f is not None else None
        if notes is not None and getattr(notes, "shape", None) == (128, 4):
            T.pc_energy(notes, self.e)
            np.nan_to_num(self.e, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        else:
            self.e.fill(0.0)
        new: Any = ()
        if fresh and f is not None:
            ons = getattr(f, "onsets", None)
            try:
                has = ons is not None and len(ons) > 0
            except TypeError:
                has = False
            if has and ons is not self._seen_ons:
                self._seen_ons = ons
                new = ons[:MAX_ONSETS]
        sounding = float(self.e.sum()) > 0.02 or len(new) > 0
        self.quiet = 0.0 if sounding else self.quiet + dt
        self.piece_t += dt
        if len(new) > 0 and (not self.started or self.frozen):
            self._new_piece()
        if self.started and not self.frozen:
            if self.quiet < QUIET_HOLD:
                for o in new:
                    try:
                        p, v = int(o[0]), float(o[1])
                    except (TypeError, ValueError, IndexError):
                        continue
                    if 0 <= p < 128 and v == v:
                        self.acc[p % 12] += ONSET_W * min(max(v, 0.0), 1.0)
                step = dt
                for _ in range(MAX_BINS):
                    room = BIN_S - self.tbin
                    if step < room:
                        self.acc += self.e * step
                        self.tbin += step
                        break
                    self.acc += self.e * room
                    step -= room
                    self.tbin = 0.0
                    self._close_bin()
            if self.quiet > gap:
                self.frozen = True
        if f is not None:
            self.keyr.update(self.e, dt, getattr(f, "key", None), getattr(f, "mode", None))

    def _new_piece(self) -> None:
        self.S.fill(0.0)
        self.n = 0
        self.acc[:] = 0.0
        self.tbin = 0.0
        self.started, self.frozen, self.example = True, False, False
        self.pieces += 1
        self.piece_t = 0.0
        self._changed(0)

    def _close_bin(self) -> None:
        n = self.n
        self.S[n + 1] = self.S[n] + self.acc
        self.n = n + 1
        self.acc[:] = 0.0
        self._changed(n + 1)
        if self.n >= CAP + DROP:                      # re-base: drop the oldest DROP rows (a view shows <= CAP)
            self.S[:ROWS - DROP] = self.S[DROP:] - self.S[DROP]
            self.S[ROWS - DROP:] = 0.0
            self.n -= DROP
            self._changed(0)


# ------------------------------------------------------------------------------------------------- the host bake
def bake_numpy(tl: KeyTimeline, mode: int = TRAILING, span: Optional[float] = None, log_rows: float = 1.0,
               fw: int = 384, fh: int = 192, hue_shift_deg: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The GPU bake in numpy (float64 maths, float32 out): planes A, B, C [fh, fw, 4] as KeyField.bake fills them.
    For tests, checks of the GPU, and low-resolution host grids (e.g. 48 x 24 for key names with regions())."""
    fw, fh = max(int(fw), 2), max(int(fh), 2)
    org, sp, now, lo = view_geometry(tl, span)
    mode = CENTRED if mode == CENTRED else TRAILING
    lr = min(max(float(log_rows), 0.0), 1.0) if log_rows == log_rows else 1.0
    hs = float(hue_shift_deg) if hue_shift_deg == hue_shift_deg and abs(hue_shift_deg) < 1e30 else 0.0
    u = (np.arange(fw) + 0.5) / fw
    v = (np.arange(fh) + 0.5) / fh
    V, U = np.meshgrid(v, u, indexing="ij")
    L = window_len(V, sp, lr)
    pos = org + U * sp
    ppb = fw / sp
    if mode == CENTRED:
        s, e = pos - 0.5 * L, pos + 0.5 * L
        inside = np.clip(np.minimum((s - lo) * ppb, (now - e) * ppb) + 0.5, 0.0, 1.0)
    else:
        e, s = pos, pos - L
        inside = np.clip(np.minimum((pos - lo) * ppb, (now - pos) * ppb) + 0.5, 0.0, 1.0)
    ec = np.minimum(np.maximum(e, lo), now)
    sc = np.minimum(np.maximum(s, lo), ec)
    A = np.zeros((fh, fw, 4), np.float32)
    B = np.zeros((fh, fw, 4), np.float32)
    C = np.zeros((fh, fw, 4), np.float32)
    opn = 1 if tl.started else 0
    A[..., 0], A[..., 2], A[..., 3] = -1.0, 1.0, inside
    B[..., :3] = _oklab_srgb_np(np.full((fh, fw), MIST[0]), np.full((fh, fw), MIST[1]), np.full((fh, fw), MIST[2]))
    B[..., 3] = 1.0
    if tl.n + opn < 1 or not now > lo:
        A[..., 3] = 0.0
        return A, B, C
    orow = tl.open_row().copy()
    H = _prefix_np(tl.S, tl.n, opn, orow, ec) - _prefix_np(tl.S, tl.n, opn, orow, sc)
    ev = _evaluate(H, ec - sc)
    A[..., 0], A[..., 1], A[..., 2] = ev["key"], ev["clar"], ev["fog"]
    # colour: the key in OKLCh, L eased toward the runner-up's at a near tie, then the mist by fog
    k1, k2, ok = ev["k1"], ev["k2"], ev["ok"]
    hue = np.radians(H0_DEG + hs + 30.0 * ((7 * (k1 % 12)) % 12))
    l1 = np.where(k1 >= 12, L_MIN_, L_MAJ)
    l2 = np.where(k2 >= 12, L_MIN_, L_MAJ)
    t = np.clip((ev["r1"] - ev["r2"]) / 0.05, 0, 1)
    Lk = np.where(ok, l1 + (l2 - l1) * 0.5 * (1.0 - t * t * (3 - 2 * t)), 0.6)
    ch = np.where(ok, C_KEY * ev["clar"], 0.0)
    ak, bk = ch * np.cos(hue), ch * np.sin(hue)
    fog = ev["fog"]
    Lc = Lk + (MIST[0] - Lk) * fog
    ac = ak + (MIST[1] - ak) * fog
    bc = bk + (MIST[2] - bk) * fog
    B[..., :3] = _oklab_srgb_np(Lc, ac, bc)
    sk, _sc = tl.summit()
    key = ev["key"]
    if sk >= 0:
        t1 = key % 12
        maj = np.where(key >= 12, (t1 + 3) % 12, t1)
        pa = (7 * maj) % 12
        d = np.abs(pa - fifths_pos(sk))
        B[..., 3] = np.where(key >= 0, np.minimum(d, 12 - d) / 6.0, 1.0)
    C[..., 0], C[..., 1], C[..., 2], C[..., 3] = ev["cos"], ev["sin"], ev["dia"], ev["dens"]
    return A, B, C


# ------------------------------------------------------------------------------------------------- the GPU bake
class KeyField:
    """GPU planes of a KeyTimeline (one per plug-in instance; everything allocated here, in setup).  Per frame:
    ``bake(tl, mode, span, log_rows, hue_shift_deg)``, then pass ``A``, ``B``, ``C`` (cupy float32 [fh, fw, 4]),
    ``fw`` and ``fh`` to your kernel and sample them with KEYFIELD_CUDA's kf_tex / kf_tex_nearest.

    After a bake: ``org``, ``span``, ``now``, ``lo`` (bins: u 0..1 covers [org, org + span]), ``mode``,
    ``summit_key``, ``summit_clarity``, ``example`` and ``version`` (the timeline's, as baked)."""

    def __init__(self, ctx: Any, fw: int = 384, fh: int = 192) -> None:
        cp = ctx.cp
        self.fw, self.fh = max(int(fw), 2), max(int(fh), 2)
        self.Sg = cp.zeros((ROWS, 12), cp.float32)
        self.A = cp.zeros((self.fh, self.fw, 4), cp.float32)
        self.B = cp.zeros((self.fh, self.fw, 4), cp.float32)
        self.C = cp.zeros((self.fh, self.fw, 4), cp.float32)
        self.arena = cp.zeros(N_AR, cp.float32)
        self._ar = np.zeros(N_AR, np.float32)
        self._last_ar = np.full(N_AR, np.nan, np.float32)
        self._S32 = np.zeros((ROWS, 12), np.float32)
        self._k = raw_module(_SRC, _OPTS).get_function("kf_bake")
        self._tl: Optional[KeyTimeline] = None
        self._ver = -1
        self._baked: tuple = (None, -1)
        self.org, self.span, self.now, self.lo = 0.0, V_MIN, 0.0, 0.0
        self.mode = TRAILING
        self.summit_key, self.summit_clarity = -1, 0.0
        self.example = False
        self.version = -1

    def sync(self, tl: KeyTimeline) -> None:
        """Upload the rows of S changed since this field's last sync (all of them after a reset or re-base, or for
        another timeline)."""
        if tl is not self._tl:
            self._tl, low = tl, 0
        else:
            low = tl.dirty_since(self._ver)
        n = min(int(tl.n), ROWS - 2)
        if low <= n:
            j0, j1 = max(int(low), 0), n + 1
            self._S32[j0:j1] = tl.S[j0:j1]
            self.Sg[j0:j1].set(self._S32[j0:j1])
        self._ver = tl.version

    def bake(self, tl: KeyTimeline, mode: int = TRAILING, span: Optional[float] = None, log_rows: float = 1.0,
             hue_shift_deg: float = 0.0) -> None:
        """Fill A, B, C for the view (module docstring) with one launch; nothing is launched when nothing changed."""
        self.sync(tl)
        org, sp, now, lo = view_geometry(tl, span)
        mode = CENTRED if mode == CENTRED else TRAILING
        lr = min(max(float(log_rows), 0.0), 1.0) if log_rows == log_rows else 1.0
        hs = float(hue_shift_deg) if hue_shift_deg == hue_shift_deg and abs(hue_shift_deg) < 1e30 else 0.0
        sk, sc = tl.summit()
        opn = 1 if tl.started else 0
        a = self._ar
        a.fill(0.0)
        a[0], a[1], a[2], a[3], a[4], a[5] = self.fw, self.fh, mode, sp, org, now
        a[6], a[7], a[8], a[9], a[10], a[11], a[12] = tl.n, opn, lr, hs, lo, sk, L_MIN
        if opn:
            a[O_OPEN:O_OPEN + 12] = tl.open_row()
        if not (self._baked == (tl, tl.version) and np.array_equal(a, self._last_ar)):
            self.arena.set(a)
            launch_2d(self._k, self.fw, self.fh, (self.A, self.B, self.C, self.arena, self.Sg))
            self._last_ar[:] = a
            self._baked = (tl, tl.version)
        self.org, self.span, self.now, self.lo, self.mode = org, sp, now, lo, mode
        self.summit_key, self.summit_clarity = sk, sc
        self.example, self.version = tl.example, tl.version

    def close(self) -> None:
        self.Sg = self.A = self.B = self.C = self.arena = None
        self._tl = None
        self._baked = (None, -1)
