"""Keyscape live: Craig Sapp's keyscape grown as the piano plays, a stained-glass mountain of keys at every timescale.

Source: Original (inspired by C. S. Sapp, "Visual hierarchical key analysis", ACM Computers in Entertainment 3(4),
        2005, https://esf.ccarh.org/254-old/254_LiteraturePack1/Harmony/Sapp_VisualHierKeyAnal05.pdf, and
        "Harmonic visualizations of tonal music", ICMC 2001; C. Viaccoz, D. Harasim, F. C. Moss, M. Rohrmeier,
        "Wavescapes: a visual hierarchical analysis of tonal music using discrete Fourier transform", Musicae
        Scientiae 2022, https://infoscience.epfl.ch/record/291958.  Their wavescapes code is GPL-3.0: nothing of it
        is used here, only the published formulas.  Key profiles: Krumhansl & Kessler 1982, as show_service.)
Author: smfx (original)
License: MIT (original)
license_class: permissive

What the picture shows
  A wide mountain of soft coloured glass over a dark night sky.  Left to right is time through the piece (the
  session): the left foot is where the playing began, the right flank is now.  Height is how much music each
  piece of glass listens to.  Along the base every piece hears one second; halfway up, a passage of a few bars;
  the summit hears the whole session at once.  So every point of the mountain answers the question "what key is
  this stretch of music in?" for the stretch centred under it, and the answers for every timescale are shown
  together.  A passage in G inside a piece in C appears as a G-coloured foothill under a C-coloured summit; where
  the base's colours differ from the summit's, the moment-to-moment harmony is wandering from the home key.

  Colour is the key.  The hue is the key's tonic placed around the circle of fifths, so closely related keys (C, G,
  F) are neighbouring hues and distant ones (C and F#) opposite.  Major keys are light glass, minor keys deeper and
  darker shades of the same hue.  The saturation is how clear the answer is: a stretch that fits one key much better
  than any other glows in full colour, an ambiguous one fades to grey glass, and a stretch with almost no sound is
  pale mist.  Thin dark lead lines run where the key changes between neighbouring pieces (only where both sides
  are clearly keyed, so the murky base stays soft).  Up to four large key names sit in the biggest regions, in
  Sapp's convention: capitals for major (E♭ is E-flat major), lower case for minor (c♯ is C-sharp minor).

  The right flank is "now": a bright edge where new glass blooms in as the music arrives.  A faint strip of twelve
  rows under the base shows what was played in each half-second (one row per pitch class, ordered around the circle
  of fifths, C at the top).  A thin ribbon above the summit shows the room's own key detector's current answer
  (show_service's key, or this scene's own tracker when the room has none) for comparison with the summit.  When
  the piano rests for ``piece_gap`` seconds the mountain freezes; the next note starts a new one and the old one
  shrinks into a small framed thumbnail at the top left.

  The ``wave_mix`` morph recolours the same mountain the Wavescapes way, with no key templates at all: the hue is
  the phase of the fifth coefficient of the pitch-class Fourier transform, F5 = sum_n H_n exp(-2 pi i 5 n / 12),
  which turns 30 degrees per step around the circle of fifths (so a C major scale lands on the hue of the key C),
  and the saturation is |F5| / sum H, the "diatonicity" (a major scale 0.53, a triad 0.64, a diminished seventh 0).

The maths (per pixel, on the GPU)
  1. Time is cut into 0.5 s bins.  Into each bin goes, per pitch class, the sum of the sounding LEVEL of its notes
     times dt (from the notes table, so pedal-held notes count), plus 0.3 x the velocity of every strike.
  2. Prefix sums S_j = sum over b < j of h_b (one 12-float row per bin, uploaded once per bin) give the pitch-class
     content of any window [s, e) as H = S(e) - S(s), interpolating linearly between rows for fractional ends.
  3. Pixel (x, y'): the window centre c is x mapped onto the session (bins), the window length is
     L(y') = mix(L_min + (L_V - L_min) y', L_min (L_V / L_min)^y', log_rows), L_min = 2 bins (1 s), L_V the view's
     span: log_rows 0 is Sapp's arithmetic triangle, 1 his logarithmic, curved-flank form.  The pixel is on the
     mountain when c - L/2 >= 0 and c + L/2 <= now.
  4. Key: d = H - mean(H), var = d.d / 12, r_k = (Z_k . d) / sqrt(var) for the 24 z-scored Krumhansl-Kessler
     profiles Z (exactly show_service/harmony.py's rows), i.e. Pearson's r with each key.  Best k1 (r1), runner-up
     r2; clarity = clamp((r1 - r2) / 0.1) x clamp(r1 / 0.5).
  5. Colour in OKLCh: hue = h0 + 30 deg x (7 tonic mod 12) (+ hue_shift), L 0.71 major / 0.50 minor, C = 0.15 x
     clarity; the mist: fog = 1 - smoothstep(0.02, 0.15, sum H / L) (an empty window is mist, never black).

Host diet: per frame one upload of a small arena (``.set``, ~0.8 KB: the views, the open bin's provisional row,
the label quads, the ribbon), one 48-byte row per closed bin, and three launches (the evaluation pass, the
compositing pass, the upsample).  The key-name labels come from a 48 x 24 grid of windows evaluated in numpy on the
host every second, spread over 12 frames (96 windows a frame), then flood-filled into regions; the four biggest
clear regions get a label that eases (a critically damped spring) to the region's deepest cell.  The cold start is a
synthetic example (8 bars I-IV-V-I in C, ii-V to G, 6 bars in G, back to C), shown dimmed until the first note, when
it cross-fades away and the live mountain grows from the left.  Over 30 minutes the view scrolls (the last 30 min).

Macros: energy -> the glass's backlight, the sheen's speed and brightness, the growing edge's glow and the sky's
drift; pulse -> a kick (eased in, never a flash) to the growing edge and the backlight; speed -> the sheen, the sky
and the breathing; intensity -> gain.  No palette: the key colours are the content; ``hue_shift`` turns them
(0..1 = 0..180 degrees).  Alive with no notes: the mountain (or the example) breathes, the sheen sweeps across the
glass and faint clouds drift in the sky.
"""
from __future__ import annotations

import logging
import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib import tonal as T
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

log = logging.getLogger("stream_service.plugins.keyscape_live")

BIN_S = 0.5                     # one bin of the timeline (s)
CAP = 3600                      # bins shown at most (30 min); the view scrolls beyond
DROP = 600                      # rows dropped (re-based) when the buffer is full, 5 min at a time, seamlessly
ROWS = CAP + DROP + 2           # prefix-sum rows per slot
L_MIN = 2.0                     # the base's window (bins): 1 s
V_MIN = 40.0                    # the live view spans at least 20 s: a new mountain grows in from the left
EX_SPAN = 120.0                 # the example's 60 s
QUIET_HOLD = 2.0                # silence longer than this stops the clock (a rest is music, a pause is not)
MIN_KEEP = 20                   # bins (10 s) a session needs before it is kept as the thumbnail
ONSET_W = 0.3                   # a strike adds this x its velocity to its pitch class's bin
GRID_W, GRID_H = 48, 24         # the label grid (windows)
LABEL_PERIOD = 1.0              # s between label refreshes
LABEL_FRAMES = 12               # frames a refresh is spread over
LABEL_MIN_AREA = 8              # cells
LABEL_MIN_CLAR = 0.4
N_LAB = 8                       # label slots drawn (4 live + ones fading out)
EX_FADE_S = 3.0                 # the example cross-fades out over this after the first note
THUMB_S = 2.0                   # the old mountain shrinks into the thumbnail over this
THUMB_SCALE = 0.22

# layout (fractions of the internal frame)
MX0, MX1 = 0.04, 0.96           # the mountain's time axis
MY_TOP, MY_BASE = 0.13, 0.80    # y' = 1 and y' = 0
STRIP_Y, STRIP_H = 0.818, 0.072 # the chroma strip under the base
TH_X, TH_Y = 0.025, 0.035       # the thumbnail's top-left

# colours (OKLCh)
H0_DEG = 32.0                   # the hue of C (a warm rose-red); G is +30, D +60 ...
L_MAJ, L_MIN_ = 0.71, 0.50
C_KEY = 0.15
FIFTH_ARG_C = 60.0              # arg F5 (deg) of a C major scale: maps the wave colour onto the key colour of C
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
LABELS24 = [T.key_label(k, short=True, unicode=True) for k in range(24)]

# arena layout (floats)
VW = 16                         # floats per view
O_V = 24                        # views: 0 main (live), 1 example, 2 thumbnail
O_OPEN = O_V + 3 * VW           # the live session's provisional row (12)
O_SKY = O_OPEN + 12             # the sky tint (3)
O_STRIP = O_SKY + 4             # strip: view index, y0, h, alpha
O_LAB = O_STRIP + 4             # labels: N_LAB x 8 (cx cy scale alpha glyph - - -)
O_RIB = O_LAB + N_LAB * 8       # ribbon: x y w h r g b alpha glyph glyph_scale
N_AR = O_RIB + 12

_KZ = ",".join(f"{v:.9e}f" for v in T.KK_Z.astype(np.float32).ravel())
_C5 = ",".join(f"{math.cos(2 * math.pi * 5 * n / 12):.9e}f" for n in range(12))
_S5 = ",".join(f"{-math.sin(2 * math.pi * 5 * n / 12):.9e}f" for n in range(12))

_SRC = HELPERS + r"""
#define ROWS """ + str(ROWS) + r"""
#define VW """ + str(VW) + r"""
#define O_V """ + str(O_V) + r"""
#define O_OPEN """ + str(O_OPEN) + r"""
#define O_SKY """ + str(O_SKY) + r"""
#define O_STRIP """ + str(O_STRIP) + r"""
#define O_LAB """ + str(O_LAB) + r"""
#define O_RIB """ + str(O_RIB) + r"""
#define N_AR """ + str(N_AR) + r"""
#define N_LAB """ + str(N_LAB) + r"""
#define TWO_PI 6.2831853f
#define DEG 0.017453293f
#define NOISE 256
#define H0 """ + repr(H0_DEG) + r"""f
#define L_MAJ """ + repr(L_MAJ) + r"""f
#define L_MIN_ """ + repr(L_MIN_) + r"""f
#define C_KEY """ + repr(C_KEY) + r"""f
#define ARG_C """ + repr(FIFTH_ARG_C) + r"""f

__constant__ float KZ[288] = {""" + _KZ + r"""};
__constant__ float C5[12] = {""" + _C5 + r"""};
__constant__ float S5[12] = {""" + _S5 + r"""};

__device__ __forceinline__ float3 oklab_lin(float L, float a, float b) {
    const float l_ = L + 0.3963377774f * a + 0.2158037573f * b;
    const float m_ = L - 0.1055613458f * a - 0.0638541728f * b;
    const float s_ = L - 0.0894841775f * a - 1.2914855480f * b;
    const float l = l_ * l_ * l_, m = m_ * m_ * m_, s = s_ * s_ * s_;
    return make_float3(4.0767416621f * l - 3.3077115913f * m + 0.2309699292f * s,
                       -1.2684380046f * l + 2.6097574011f * m - 0.3413193965f * s,
                       -0.0041960863f * l - 0.7034186147f * m + 1.7076147010f * s);
}
__device__ __forceinline__ float enc(float x) {
    x = clamp01(x);
    return x <= 0.0031308f ? 12.92f * x : 1.055f * __powf(x, 1.f / 2.4f) - 0.055f;
}
__device__ __forceinline__ float noise_tex(const float* __restrict__ Tx, float u, float v) {
    u -= 0.5f; v -= 0.5f;
    const float fu = floorf(u), fv = floorf(v);
    const int x0 = ((int)fu) & (NOISE - 1), y0 = ((int)fv) & (NOISE - 1);
    const int x1 = (x0 + 1) & (NOISE - 1), y1 = (y0 + 1) & (NOISE - 1);
    const float tx = u - fu, ty = v - fv;
    const float a = Tx[y0 * NOISE + x0], b = Tx[y0 * NOISE + x1], c = Tx[y1 * NOISE + x0], d = Tx[y1 * NOISE + x1];
    return (1.f - ty) * ((1.f - tx) * a + tx * b) + ty * ((1.f - tx) * c + tx * d);
}

// one row (12 floats) of a slot's prefix sums; row n + 1 of the live view is the open bin's provisional row
__device__ __forceinline__ void row12(const float4* __restrict__ S, int slot, int j, int n, const float* OPEN, float* o) {
    if (j > n) {
        #pragma unroll
        for (int k = 0; k < 12; ++k) o[k] = OPEN[k];
        return;
    }
    const float4* R = S + ((size_t)slot * ROWS + j) * 3;
    const float4 a = R[0], b = R[1], c = R[2];
    o[0] = a.x; o[1] = a.y; o[2] = a.z; o[3] = a.w; o[4] = b.x; o[5] = b.y;
    o[6] = b.z; o[7] = b.w; o[8] = c.x; o[9] = c.y; o[10] = c.z; o[11] = c.w;
}
// S(x) at a fractional bin position (0 <= x <= the last valid row), linear between rows
__device__ __forceinline__ void prefix(const float4* __restrict__ S, int slot, int n, int open, const float* OPEN,
                                       float x, float* o) {
    const int nmax = n + open;                      // the last valid row
    const float fi = floorf(x);
    int i = (int)fi;
    float f = x - fi;
    if (i >= nmax) { i = nmax - 1; f = 1.f; }
    if (i < 0) { i = 0; f = 0.f; }
    float a[12], b[12];
    row12(S, slot, i, n, OPEN, a);
    row12(S, slot, i + 1, n, OPEN, b);
    #pragma unroll
    for (int k = 0; k < 12; ++k) o[k] = a[k] + f * (b[k] - a[k]);
}

struct Res { float3 c; float cov; float halo; int id; float r1; };

// one view of a session: the window under (px, py), its key and colour (linear RGB), coverage and glow
// V: [0]weight [1]slot [2]n [3]now (bins) [4]open [5]span [6]x0 [7]y0 (top) [8]x1 [9]y1 (base) [10]dim [11]glow
//    [12]panel alpha [13]L_min [14]origin (bins) [15]px scale
__device__ Res eval_view(const float* P, const float* V, const float4* __restrict__ S, float px, float py) {
    Res r; r.c = make_float3(0.f, 0.f, 0.f); r.cov = 0.f; r.halo = 0.f; r.id = 255; r.r1 = 0.f;
    if (!(V[0] > 0.f)) return r;
    const int slot = (int)V[1], n = (int)V[2], open = (int)V[4];
    const float now = V[3], span = V[5], x0 = V[6], y0 = V[7], x1 = V[8], y1 = V[9], Lmin = V[13], org = V[14];
    if (n + open < 1 || now - org <= Lmin) return r;
    if (py > y1 + 1.f || py < y0 - 2.f || px < x0 - 2.f || px > x1 + 2.f) return r;
    const float yp = clamp01((y1 - py) / (y1 - y0));
    const float logr = P[2];
    const float Llin = Lmin + (span - Lmin) * yp;
    const float Llog = Lmin * __powf(span / Lmin, yp);
    const float Lw = Llin + (Llog - Llin) * logr;
    const float c = org + (px - x0) / (x1 - x0) * span;
    const float s = c - 0.5f * Lw, e = c + 0.5f * Lw;
    const float ppb = (x1 - x0) / span;
    const float dl = (s - org) * ppb, dr = (now - e) * ppb;
    const float cb = clamp01(y1 - py + 0.5f);
    const float cov = clamp01(fminf(dl, dr) + 0.5f) * cb;
    if (V[11] > 0.f && dl >= 0.f && dr < 0.5f && cb > 0.f)     // the growing edge's halo, over the sky
        r.halo = V[11] * 0.45f * __expf(fminf(dr, 0.f) / (12.f * V[15])) * cb;
    if (cov <= 0.f) return r;
    float A[12], B[12];
    prefix(S, slot, n, open, P + O_OPEN, fmaxf(s, org), A);
    prefix(S, slot, n, open, P + O_OPEN, fminf(e, now), B);
    float H[12], sum = 0.f;
    #pragma unroll
    for (int k = 0; k < 12; ++k) { H[k] = fmaxf(B[k] - A[k], 0.f); sum += H[k]; }
    const float mean = sum * (1.f / 12.f);
    float var = 0.f;
    #pragma unroll
    for (int k = 0; k < 12; ++k) { H[k] -= mean; var += H[k] * H[k]; }
    var *= (1.f / 12.f);
    float fog = 1.f - smooth(0.02f, 0.15f, sum / fmaxf(Lw, 1e-3f));
    float Lk = 0.6f, ak = 0.f, bk = 0.f, aw = 0.f, bw = 0.f, Lwv = 0.6f;
    if (var > 1e-9f + 1e-6f * mean * mean && sum > 1e-5f) {
        const float inv = rsqrtf(var);
        float r1 = -2.f, r2 = -2.f;
        int k1 = 0, k2 = 0;
        #pragma unroll 4
        for (int k = 0; k < 24; ++k) {
            float acc = 0.f;
            #pragma unroll
            for (int i = 0; i < 12; ++i) acc += KZ[k * 12 + i] * H[i];
            acc *= inv;
            if (acc > r1) { r2 = r1; k2 = k1; r1 = acc; k1 = k; }
            else if (acc > r2) { r2 = acc; k2 = k; }
        }
        const float clar = clamp01((r1 - r2) * 10.f) * clamp01(r1 * 2.f);
        const int t1 = k1 % 12;
        const float hue = (H0 + P[4] + 30.f * (float)((7 * t1) % 12)) * DEG;
        const float l1 = k1 >= 12 ? L_MIN_ : L_MAJ, l2 = k2 >= 12 ? L_MIN_ : L_MAJ;
        Lk = l1 + (l2 - l1) * 0.5f * (1.f - smooth(0.f, 0.05f, r1 - r2));   // continuous across a boundary
        const float ch = C_KEY * clar;
        ak = ch * __cosf(hue); bk = ch * __sinf(hue);
        // the wavescape: the phase and size of the fifth DFT coefficient (mean removed: F5 is unchanged by it)
        float fr = 0.f, fi = 0.f;
        #pragma unroll
        for (int i = 0; i < 12; ++i) { fr += H[i] * C5[i]; fi += H[i] * S5[i]; }
        const float dia = sqrtf(fr * fr + fi * fi) / sum;
        const float hw = (H0 + P[4] - ARG_C) * DEG + atan2f(fi, fr);
        const float cw = fminf(C_KEY * dia / 0.53f, 0.19f);
        aw = cw * __cosf(hw); bw = cw * __sinf(hw);
        Lwv = 0.58f + 0.14f * clamp01(dia / 0.53f);
        r.id = fog < 0.6f ? k1 : 255;
        r.r1 = r1 * (1.f - fog);
    } else {
        fog = 1.f;
    }
    const float wm = P[3];
    float L = Lk + (Lwv - Lk) * wm, a = ak + (aw - ak) * wm, b = bk + (bw - bk) * wm;
    L += (0.40f - L) * fog; a += (-0.004f - a) * fog; b += (-0.018f - b) * fog;            // the mist
    float3 lin = oklab_lin(L, a, b);
    if (V[11] > 0.f) {                               // the growing edge: windows that end in the last 1.5 s
        const float g = V[11] * smooth(now - 3.f, now, e);
        lin.x += g * 0.75f; lin.y += g * 0.68f; lin.z += g * 0.55f;
    }
    const float dim = V[10];
    r.c = make_float3(fmaxf(lin.x, 0.f) * dim, fmaxf(lin.y, 0.f) * dim, fmaxf(lin.z, 0.f) * dim);
    r.cov = cov;
    return r;
}

// pass 1: the views -> premultiplied sRGB colour + coverage, and the key id / clarity for the lead lines
extern "C" __global__
void ks_eval(uchar4* __restrict__ col, uchar4* __restrict__ info, const float* __restrict__ Ar,
             const float4* __restrict__ S)
{
    __shared__ float P[N_AR];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    for (int i = tid; i < N_AR; i += blockDim.x * blockDim.y) P[i] = Ar[i];
    __syncthreads();
    const int W = (int)P[0], Hh = (int)P[1];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= Hh) return;
    const float px = x + 0.5f, py = y + 0.5f;
    const float* V0 = P + O_V;
    const float* V1 = P + O_V + VW;
    const float* V2 = P + O_V + 2 * VW;
    const Res m = eval_view(P, V0, S, px, py);
    float3 C = make_float3(m.c.x * m.cov, m.c.y * m.cov, m.c.z * m.cov);
    float Aa = m.cov, halo = m.halo;
    int id = m.id, lay = 0;
    float r1 = m.r1;
    const float w1 = V1[0];
    if (w1 > 0.f) {                                   // the example, cross-fading away
        const Res e = eval_view(P, V1, S, px, py);
        C.x += (e.c.x * e.cov - C.x) * w1; C.y += (e.c.y * e.cov - C.y) * w1; C.z += (e.c.z * e.cov - C.z) * w1;
        Aa += (e.cov - Aa) * w1;
        halo += (e.halo - halo) * w1;
        if (w1 > 0.5f && e.cov > 0.5f) { id = e.id; r1 = e.r1; lay = 1; }
    }
    if (V2[0] > 0.f) {                                // the previous session, shrinking into its frame
        const float pad = 10.f * V2[15];
        const float qx = fmaxf(fmaxf(V2[6] - pad - px, px - V2[8] - pad), 0.f);
        const float qy = fmaxf(fmaxf(V2[7] - 2.f * pad - py, py - V2[9] - pad), 0.f);
        const float rad = 8.f * V2[15];
        const float bx = fmaxf(V2[6] - pad + rad - px, px - (V2[8] + pad - rad));
        const float by = fmaxf(V2[7] - 2.f * pad + rad - py, py - (V2[9] + pad - rad));
        const float d = sqrtf(fmaxf(bx, 0.f) * fmaxf(bx, 0.f) + fmaxf(by, 0.f) * fmaxf(by, 0.f)) - rad
                        + fminf(fmaxf(bx, by), 0.f);
        (void)qx; (void)qy;
        const float pa = V2[12] * clamp01(0.5f - d);
        if (pa > 0.f) {                               // a dark panel with a faint rim
            const float rim = V2[12] * clamp01(1.f - fabsf(d + 1.f)) * 0.5f;
            C.x = C.x * (1.f - pa) + rim * 0.10f; C.y = C.y * (1.f - pa) + rim * 0.10f; C.z = C.z * (1.f - pa) + rim * 0.12f;
            Aa = Aa + (1.f - Aa) * pa;
            halo *= 1.f - pa;
            C.x += pa * 0.006f; C.y += pa * 0.007f; C.z += pa * 0.014f;
        }
        const Res t = eval_view(P, V2, S, px, py);
        const float tw = V2[0] * t.cov;
        if (tw > 0.f) {
            C.x = C.x * (1.f - tw) + t.c.x * tw; C.y = C.y * (1.f - tw) + t.c.y * tw; C.z = C.z * (1.f - tw) + t.c.z * tw;
            Aa = Aa + (1.f - Aa) * tw;
            if (tw > 0.5f) { id = t.id; r1 = t.r1; lay = 2; }
        }
    }
    // premultiplied linear -> sRGB of the unpremultiplied colour x coverage (+ the halo as light over the sky)
    const float ia = Aa > 1e-4f ? 1.f / Aa : 0.f;
    const float hr = halo * 0.95f, hg = halo * 0.82f, hb = halo * 0.62f;
    col[y * W + x] = make_uchar4((unsigned char)fminf(255.f, 255.f * (enc(C.x * ia) * Aa + hr) + 0.5f),
                                 (unsigned char)fminf(255.f, 255.f * (enc(C.y * ia) * Aa + hg) + 0.5f),
                                 (unsigned char)fminf(255.f, 255.f * (enc(C.z * ia) * Aa + hb) + 0.5f),
                                 (unsigned char)(255.f * clamp01(Aa) + 0.5f));
    info[y * W + x] = make_uchar4((unsigned char)id, (unsigned char)(255.f * clamp01(r1) + 0.5f),
                                  (unsigned char)lay, 0);
}

// a label glyph from the atlas (fill, shadow), bilinear; quad centred at (cx, cy), scale s
__device__ __forceinline__ float2 glyph(const uchar2* __restrict__ atlas, int g, float u, float v, int GW, int GH) {
    u -= 0.5f; v -= 0.5f;
    const float fu = floorf(u), fv = floorf(v);
    const int x0 = (int)fu, y0 = (int)fv;
    const float tx = u - fu, ty = v - fv;
    float2 r = make_float2(0.f, 0.f);
    #pragma unroll
    for (int k = 0; k < 4; ++k) {
        const int xx = x0 + (k & 1), yy = y0 + (k >> 1);
        if (xx < 0 || yy < 0 || xx >= GW || yy >= GH) continue;
        const float w = ((k & 1) ? tx : 1.f - tx) * ((k >> 1) ? ty : 1.f - ty);
        const uchar2 q = atlas[((size_t)g * GH + yy) * GW + xx];
        r.x += w * q.x; r.y += w * q.y;
    }
    return make_float2(r.x * (1.f / 255.f), r.y * (1.f / 255.f));
}

// pass 2: sky, glass (backlight, mottle, breathing), lead lines, sheen, the chroma strip, labels, ribbon
// P: [0]W [1]H [2]log_rows [3]wave_mix [4]hue shift deg [5]lead lines [6]labels [7]energy [8]kick [9]gain
//    [10]sheen phase (cycles) [11]sky phase [12]breath factor [13]sheen amp [14]backlight [15]px scale
//    [16]sheen dir x [17]dir y [18]sheen freq (rad/px) [19]glyph w [20]glyph h [21]clock
extern "C" __global__
void ks_compose(uchar4* __restrict__ out, const uchar4* __restrict__ col, const uchar4* __restrict__ info,
                const float* __restrict__ Ar, const float4* __restrict__ S, const float* __restrict__ tex,
                const uchar2* __restrict__ atlas, const unsigned int seed)
{
    __shared__ float P[N_AR];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    for (int i = tid; i < N_AR; i += blockDim.x * blockDim.y) P[i] = Ar[i];
    __syncthreads();
    const int W = (int)P[0], Hh = (int)P[1];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= Hh) return;
    const float px = x + 0.5f, py = y + 0.5f, sc = P[15];
    const float v = py / (float)Hh;
    const float en = P[7];

    // the sky: deep blue-black, a faint glow of the summit's key low down, slow clouds
    float3 o;
    {
        const float g = v * v;
        o = make_float3(0.010f + 0.030f * g, 0.012f + 0.032f * g, 0.030f + 0.042f * g);
        const float tint = 0.10f * smooth(0.15f, 0.85f, v);
        o.x += tint * P[O_SKY]; o.y += tint * P[O_SKY + 1]; o.z += tint * P[O_SKY + 2];
        const float s1 = noise_tex(tex, px / (3.1f * sc) + P[11] * 160.f, py / (5.3f * sc) + 17.f);
        const float s2 = noise_tex(tex, px / (1.7f * sc) - P[11] * 230.f + 90.f, py / (2.9f * sc) + 60.f);
        const float cl = smooth(0.45f, 0.9f, 0.6f * s1 + 0.4f * s2) * (0.045f + 0.03f * en) * (1.f - 0.6f * v);
        o.x += cl * 0.55f; o.y += cl * 0.62f; o.z += cl * 0.85f;
    }
    // the glass
    const uchar4 c4 = col[y * W + x];
    const uchar4 i4 = info[y * W + x];
    const float A = c4.w * (1.f / 255.f);
    float3 g = make_float3(c4.x * (1.f / 255.f), c4.y * (1.f / 255.f), c4.z * (1.f / 255.f));
    const float mot = 1.f + 0.10f * (noise_tex(tex, px / (0.45f * sc) + 33.f, py / (0.6f * sc) + 71.f) - 0.5f)
                    + 0.05f * (noise_tex(tex, px / (0.11f * sc), py / (0.16f * sc)) - 0.5f);
    const float pass = noise_tex(tex, px / (2.4f * sc) - P[11] * 420.f, py / (2.0f * sc) + P[11] * 90.f + 140.f);
    const float bl = 1.f + (P[14] * P[12] * mot * (0.9f + 0.2f * pass) - 1.f) * A;            // the backlight (the halo over the sky as is)
    g.x *= bl; g.y *= bl; g.z *= bl;
    // the sheen: a narrow bright band and a broad soft one sweeping across the glass
    {
        const float d = (px * P[16] + py * P[17]) * P[18];
        const float s1 = __powf(fmaxf(0.f, __sinf(d - TWO_PI * P[10])), 40.f);
        const float s2 = __powf(fmaxf(0.f, __sinf(0.5f * d - TWO_PI * 0.61f * P[10] + 1.3f)), 6.f);
        const float sh = P[13] * (s1 + 0.3f * s2) * A;
        g.x += sh * 0.95f; g.y += sh * 0.97f; g.z += sh;
    }
    o.x = o.x * (1.f - A) + g.x; o.y = o.y * (1.f - A) + g.y; o.z = o.z * (1.f - A) + g.z;

    // lead lines: where the key differs from a neighbour (both clearly keyed), and around the mountain
    if (P[5] > 0.f) {
        float line = 0.f;
        const int id = i4.x;
        const float r1 = i4.y * (1.f / 255.f);
        #pragma unroll
        for (int k = 0; k < 8; ++k) {
            const int dx = k == 0 ? 1 : k == 1 ? -1 : k == 4 ? 1 : k == 5 ? -1 : k == 6 ? 1 : k == 7 ? -1 : 0;
            const int dy = k == 2 ? 1 : k == 3 ? -1 : k == 4 ? 1 : k == 5 ? 1 : k == 6 ? -1 : k == 7 ? -1 : 0;
            const int xx = min(max(x + dx, 0), W - 1), yy = min(max(y + dy, 0), Hh - 1);
            const uchar4 n4 = info[yy * W + xx];
            const float wk = k < 4 ? 1.f : 0.45f;
            if (id != 255 && n4.x != 255 && n4.x != id && n4.z == i4.z) {
                const float rr = fminf(r1, n4.y * (1.f / 255.f));
                line = fmaxf(line, wk * smooth(0.3f, 0.5f, rr));
            }
            const float an = col[yy * W + xx].w * (1.f / 255.f);
            line = fmaxf(line, wk * 0.9f * smooth(0.25f, 0.75f, fabsf(an - A)) * fmaxf(an, A));
        }
        const float lw = P[5] * line * 0.92f;
        o.x += (0.018f - o.x) * lw; o.y += (0.017f - o.y) * lw; o.z += (0.022f - o.z) * lw;
    }

    // the chroma strip: what was played in each half-second, one row per pitch class (fifths order, C on top)
    if (P[O_STRIP + 3] > 0.f && py >= P[O_STRIP + 1] && py < P[O_STRIP + 1] + P[O_STRIP + 2]) {
        const float* V = P + O_V + VW * (int)P[O_STRIP];
        const float span = V[5], x0 = V[6], x1 = V[8], org = V[14], now = V[3];
        const int slot = (int)V[1], n = (int)V[2], open = (int)V[4];
        const float c = org + (px - x0) / (x1 - x0) * span;
        const float fr = (py - P[O_STRIP + 1]) / P[O_STRIP + 2] * 12.f;
        const int row = min((int)fr, 11);
        const float inrow = fr - (float)row;
        if (c >= org && c < now && n + open >= 1 && V[0] > 0.f) {
            const int b = (int)floorf(c);
            float a[12], bb[12];
            row12(S, slot, min(b, n + open - 1), n, P + O_OPEN, a);
            row12(S, slot, min(b + 1, n + open), n, P + O_OPEN, bb);
            float mx = 0.f;
            #pragma unroll
            for (int k = 0; k < 12; ++k) mx = fmaxf(mx, bb[k] - a[k]);
            const int pc = (7 * row) % 12;
            float hv = 0.f;
            #pragma unroll
            for (int k = 0; k < 12; ++k) if (k == pc) hv = bb[k] - a[k];
            const float lev = clamp01(hv / (mx + 0.06f));
            const float hue = (H0 + P[4] + 30.f * (float)row) * DEG;
            const float3 lc = oklab_lin(0.74f, 0.11f * __cosf(hue), 0.11f * __sinf(hue));
            const float k = P[O_STRIP + 3] * lev * smooth(0.f, 0.18f, inrow) * smooth(1.f, 0.82f, inrow);
            o.x += k * enc(lc.x) * 0.55f; o.y += k * enc(lc.y) * 0.55f; o.z += k * enc(lc.z) * 0.55f;
        }
    }

    // the key names (light letters on a soft dark shadow) and the detector's ribbon
    const int GW = (int)P[19], GH = (int)P[20];
    if (GW > 0) {
        for (int i = 0; i < N_LAB; ++i) {
            const float* L = P + O_LAB + 8 * i;
            const float la = L[3] * P[6];
            if (!(la > 0.004f)) continue;
            const float s = L[2];
            const float u = (px - L[0]) / s + 0.5f * GW, vv = (py - L[1]) / s + 0.5f * GH;
            if (u < -1.f || vv < -1.f || u > GW + 1.f || vv > GH + 1.f) continue;
            const float2 q = glyph(atlas, (int)L[4], u, vv, GW, GH);
            const float sh = la * 0.65f * q.y, fi = la * 0.94f * q.x;
            o.x *= 1.f - sh; o.y *= 1.f - sh; o.z *= 1.f - sh;
            o.x += (0.97f - o.x) * fi; o.y += (0.955f - o.y) * fi; o.z += (0.93f - o.z) * fi;
        }
        const float* R = P + O_RIB;
        if (R[7] > 0.004f) {
            const float dx = fabsf(px - R[0]) - (0.5f * R[2] - 0.5f * R[3]);
            const float dy = fabsf(py - R[1]);
            const float d = sqrtf(fmaxf(dx, 0.f) * fmaxf(dx, 0.f) + dy * dy) - 0.5f * R[3];
            const float cov = R[7] * clamp01(0.5f - d);
            const float gl = R[7] * 0.25f * __expf(-fmaxf(d, 0.f) / (6.f * sc));
            o.x += (R[4] - o.x) * cov + gl * R[4]; o.y += (R[5] - o.y) * cov + gl * R[5];
            o.z += (R[6] - o.z) * cov + gl * R[6];
            const float s = R[9];
            const float u = (px - R[0]) / s + 0.5f * GW, vv = (py - (R[1] - 0.45f * GH * s)) / s + 0.5f * GH;
            if (u >= -1.f && vv >= -1.f && u <= GW + 1.f && vv <= GH + 1.f) {
                const float2 q = glyph(atlas, (int)R[8], u, vv, GW, GH);
                const float fi = R[7] * 0.8f * q.x;
                o.x += (R[4] - o.x) * fi; o.y += (R[5] - o.y) * fi; o.z += (R[6] - o.z) * fi;
            }
        }
    }

    const float gn = 255.f * P[9];
    const float dth = (hash_u((unsigned int)(y * W + x) ^ (seed * 0x9E3779B1u)) >> 8) * (1.f / 16777216.f) - 0.5f;
    out[y * W + x] = pack(make_float4(gn * o.x + dth, gn * o.y + dth, gn * o.z + dth, 255.f));
}
"""

_OPTS = ("--std=c++14", "--use_fast_math")
_logged: set = set()


def _log_once(key: str, msg: str, *args) -> None:
    if key not in _logged:
        _logged.add(key)
        log.warning(msg, *args)


# ------------------------------------------------------------------------------------------------- host helpers
def _enc(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055


def key_rgb(k: int, hue_shift_deg: float = 0.0, chroma: float = C_KEY) -> tuple[float, float, float]:
    """A key's glass colour (sRGB 0..1) at full clarity: the kernel's formula."""
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


def window_len(yp, span: float, logr: float):
    """L(y') in bins (scalar or array): the arithmetic and logarithmic rows mixed by ``logr``."""
    lin = L_MIN + (span - L_MIN) * yp
    lg = L_MIN * (span / L_MIN) ** yp
    return lin + (lg - lin) * logr


def apex_y(length: float, span: float, logr: float) -> float:
    """The y' whose window length is ``length`` (bisection; L(y') is increasing)."""
    lo, hi = 0.0, 1.0
    for _ in range(22):
        mid = 0.5 * (lo + hi)
        if window_len(mid, span, logr) < length:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def windows_keys(S: np.ndarray, n: int, s: np.ndarray, e: np.ndarray, length: np.ndarray):
    """Key (or -1), clarity and fog of the windows [s, e) (bins) over the prefix sums S[0..n]: the kernel's maths in
    numpy (float64)."""
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


def regions(ids: np.ndarray, clar: np.ndarray, top: int = 4):
    """Flood-fill the label grid (GRID_H x GRID_W, -1 = none) into regions of one key; the ``top`` biggest clear ones
    (mean clarity > LABEL_MIN_CLAR, area >= LABEL_MIN_AREA), one per key: [(key, area, row, col, depth)] with the
    deepest cell (most 4-erosions survived; nearest the centroid on a tie)."""
    h, w = ids.shape
    lab = -np.ones((h, w), np.int64)
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
            if len(cells) >= LABEL_MIN_AREA:
                idx = np.array(cells)
                mc = float(clar[idx[:, 0], idx[:, 1]].mean())
                if mc > LABEL_MIN_CLAR:
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
        out.append((k, area, float(best[0]), float(best[1]), dmax))
        if len(out) >= top:
            break
    return out


def bake_labels(height: int) -> tuple[np.ndarray, int, int]:
    """The 24 key names (Sapp's case) as a uint8 atlas [24, GH, GW, 2]: fill, soft shadow."""
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
        font = ImageFont.truetype(FONT, max(8, int(round(height * 0.78))))
        gh = max(12, int(round(height * 1.45)))      # room for the soft shadow all round
        gw = max(12, int(math.ceil(max(font.getlength(s) for s in LABELS24) + 0.9 * height)))
        out = np.zeros((24, gh, gw, 2), np.uint8)
        for i, s in enumerate(LABELS24):
            img = Image.new("L", (gw, gh), 0)
            ImageDraw.Draw(img).text((gw / 2, gh / 2), s, fill=255, font=font, anchor="mm")
            sh = img.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(max(1.5, gh * 0.13)))
            out[i, :, :, 0] = np.asarray(img, np.uint8)
            out[i, :, :, 1] = np.minimum(np.asarray(sh, np.float32) * 2.2, 255).astype(np.uint8)
        return out, gw, gh
    except Exception as exc:                          # degrade: no names
        _log_once("font", "keyscape_live: cannot bake the key names (%s); drawing none", exc)
        return np.zeros((24, 1, 1, 2), np.uint8), 0, 0


def _noise_texture(seed: int, n: int = 256) -> np.ndarray:
    """A tiling smooth noise in 0..1 (a sum of random low-frequency waves)."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:n, 0:n] / n
    f = np.zeros((n, n))
    for octave, amp in ((1, 1.0), (2, 0.6), (3, 0.45), (5, 0.3), (8, 0.18), (13, 0.1)):
        for _ in range(3):
            kx, ky = rng.integers(-octave, octave + 1, 2)
            if kx == 0 and ky == 0:
                kx = octave
            f += amp * np.cos(2 * np.pi * (kx * x + ky * y) + rng.uniform(0, 2 * np.pi))
    f -= f.min()
    f /= max(f.max(), 1e-9)
    return f.astype(np.float32)


def example_bins(seed: int) -> np.ndarray:
    """The cold-start example's bins [120, 12]: 8 bars I-IV-V-I in C, ii-V to G, 6 bars in G, then back to C
    (IV, V7, then I-IV-V7-I-IV-I), a bar = 2.5 s = 5 bins: chord tones, a stronger bass root, a scale melody, strikes."""
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


# ------------------------------------------------------------------------------------------------- the scene
class KeyscapeLive(Source):
    """Per frame: one arena upload and three launches (evaluate, compose, upsample); one 48-byte row per closed
    0.5 s bin; the label grid in numpy, 96 windows a frame."""
    name = "keyscape_live"
    label = "Keyscape live"
    description = ("Craig Sapp's keyscape grown live: a stained-glass mountain whose base is the key of each moment "
                   "and whose summit is the key of the whole session, so the harmony is shown at every timescale.")
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette=None, render_scale=0.5),
            "wave_mix": num(0.0, 1.0, 0.0, "Wavescape colours", basic=True),
            "log_rows": num(0.0, 1.0, 0.85, "Curved flanks (log rows)"),
            "lead_lines": num(0.0, 1.0, 1.0, "Lead lines", basic=True),
            "labels": num(0.0, 1.0, 1.0, "Key names"),
            "hue_shift": num(0.0, 1.0, 0.0, "Hue shift"),
            "piece_gap": num(30.0, 300.0, 90.0, "New mountain after", unit="s", tier="structural"),
        }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.sc = self.h / 1080.0
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        mod = raw_module(_SRC, _OPTS)
        self.k_eval = mod.get_function("ks_eval")
        self.k_comp = mod.get_function("ks_compose")
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.col = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.info = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.tex = cp.asarray(_noise_texture(self.seed))
        atlas, self.gw, self.gh = bake_labels(round(0.14 * self.h))
        self.atlas = cp.asarray(np.ascontiguousarray(atlas))
        # prefix sums: slots 0 and 1 ping-pong the live sessions, slot 2 holds the example
        self.S = np.zeros((3, ROWS, 12))
        self.Sg = cp.zeros((3, ROWS, 12), cp.float32)
        self._row32 = np.zeros(12, np.float32)
        self.n = [0, 0, 0]
        ex = example_bins(self.seed)
        self.S[2, 1:len(ex) + 1] = np.cumsum(ex, axis=0)
        self.n[2] = len(ex)
        self.Sg[2].set(self.S[2].astype(np.float32))
        self.arena = cp.zeros(N_AR, cp.float32)
        self.A = np.zeros(N_AR, np.float32)
        self.e = np.zeros(12, np.float32)
        self.acc = np.zeros(12)
        self.open_row = np.zeros(12)
        self.keyr = T.KeyResolver()
        self._seen = None
        # the timeline
        self.cur = 0                  # the live session's slot
        self.started = False          # a live session exists
        self.frozen = False
        self.first = False            # the first note has come (the example fades)
        self.tbin = 0.0               # seconds into the open bin
        self.quiet = 1e9              # seconds since anything sounded
        self.ex_w = 1.0
        self.th_slot, self.th_n, self.th_now, self.th_p = -1, 0, 0.0, 0.0
        # motion
        self.clock = self.sheen = self.sky = self.breath = self.kick = 0.0
        # labels: slots {(layer, key): state}; the refresh job
        self.labs: dict = {}
        self.layer = 10               # the live session's label layer (1: the example's)
        self.job = None
        self.job_t = LABEL_PERIOD
        self.sky_rgb = np.array(key_rgb(0)) * 0.5
        self.sky_tgt = self.sky_rgb.copy()
        self.rib = {"a": 0.0, "rgb": np.array(key_rgb(0)), "g": 0, "x": 0.5 * self.w, "y": MY_TOP * self.h}
        self._example_labels(params)

    # -- geometry ------------------------------------------------------------------------------------------------
    def _rect(self) -> tuple[float, float, float, float]:
        return MX0 * self.w, MY_TOP * self.h, MX1 * self.w, MY_BASE * self.h

    def _live_view(self) -> tuple[int, int, float, int, float, float]:
        """(slot, n, now, open, span, origin) of the live session."""
        n = self.n[self.cur]
        now = n + (min(self.tbin / BIN_S, 1.0) if self.started else 0.0)
        org = max(0.0, now - CAP)
        span = min(max(now - org, V_MIN), float(CAP))
        return self.cur, n, now, 1 if self.started else 0, span, org

    # -- the timeline ---------------------------------------------------------------------------------------------
    def _upload_row(self, slot: int, j: int) -> None:
        self._row32[:] = self.S[slot, j]
        self.Sg[slot, j].set(self._row32)

    def _close_bin(self, provisional: bool = False) -> None:
        """Close the open bin into the live slot (``provisional``: a partial bin, stored as the kernel extrapolates
        it, so the picture does not move)."""
        s, n = self.cur, self.n[self.cur]
        if provisional:
            f = max(self.tbin / BIN_S, 0.25)
            self.S[s, n + 1] = self.S[s, n] + self.acc / f
        else:
            self.S[s, n + 1] = self.S[s, n] + self.acc
        self._upload_row(s, n + 1)
        self.n[s] = n + 1
        self.acc[:] = 0.0
        if self.n[s] >= CAP + DROP:                   # re-base: drop the oldest DROP rows (the view shows < CAP)
            self.S[s, :ROWS - DROP] = self.S[s, DROP:] - self.S[s, DROP]
            self.S[s, ROWS - DROP:] = 0.0
            self.n[s] -= DROP
            self.Sg[s].set(self.S[s].astype(np.float32))

    def _new_session(self) -> None:
        if self.started:
            old, n_old = self.cur, self.n[self.cur]
            now_old = n_old + min(self.tbin / BIN_S, 1.0)
            if n_old >= MIN_KEEP:                     # keep it as the thumbnail; the new one takes the other slot
                if self.tbin > 0.0:
                    self._close_bin(provisional=True)
                self.th_slot, self.th_n, self.th_now, self.th_p = old, self.n[old], now_old, 0.0
                self.cur = 1 - old
            for key, st in self.labs.items():        # the old mountain's names fade as it shrinks
                if key[0] == self.layer:
                    st["ta"] = 0.0
            self.layer += 1
        self.n[self.cur] = 0
        self.S[self.cur, 0] = 0.0
        self._upload_row(self.cur, 0)
        self.acc[:] = 0.0
        self.tbin = 0.0
        self.started, self.frozen = True, False
        self.job = None
        self.job_t = 0.0

    def _timeline(self, dt: float, gap: float) -> None:
        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        notes = getattr(f, "notes", None) if f is not None else None
        if notes is not None and getattr(notes, "shape", None) == (128, 4):
            T.pc_energy(notes, self.e)
            np.nan_to_num(self.e, copy=False, nan=0.0, posinf=0.0)
        else:
            self.e.fill(0.0)
        ons = getattr(f, "onsets", None) if f is not None else None
        new = []
        if ons and ons is not self._seen:
            self._seen = ons
            new = ons[:64]
        sounding = float(self.e.sum()) > 0.02 or bool(new)
        self.quiet = 0.0 if sounding else self.quiet + dt
        if new and (not self.started or self.frozen):
            self._new_session()
            self.first = True
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
                for _ in range(8):                    # at most 8 bins per frame (a long skip settles)
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

    # -- labels ---------------------------------------------------------------------------------------------------
    def _grid(self, span: float, org: float, now: float, logr: float):
        u = (np.arange(GRID_W) + 0.5) / GRID_W
        yp = (np.arange(GRID_H) + 0.5) / GRID_H
        L = window_len(yp, span, logr)[:, None] * np.ones((1, GRID_W))
        c = org + u[None, :] * span * np.ones((GRID_H, 1))
        s, e = c - 0.5 * L, c + 0.5 * L
        inside = (s >= org) & (e <= now)
        return s.ravel(), e.ravel(), L.ravel(), inside.ravel()

    def _example_labels(self, params: dict) -> None:
        s, e, L, inside = self._grid(EX_SPAN, 0.0, float(self.n[2]), float(params["log_rows"]))
        ids, clar, _fog = windows_keys(self.S[2], self.n[2], s, e, L)
        ids = np.where(inside, ids, -1)
        self.ex_logr = float(params["log_rows"])
        self._targets(1, regions(ids.reshape(GRID_H, GRID_W), clar.reshape(GRID_H, GRID_W)), snap=True)

    def _targets(self, layer: int, regs: list, snap: bool = False) -> None:
        cell_h = (MY_BASE - MY_TOP) * self.h / GRID_H
        keep = set()
        for k, area, row, col, depth in regs:
            key = (layer, int(k))
            keep.add(key)
            u, yp = (col + 0.5) / GRID_W, (row + 0.5) / GRID_H
            s = min(max((depth + 0.5) * cell_h * 1.9 / max(self.gh, 1), 0.6), 1.25) if self.gh else 0.0
            st = self.labs.get(key)
            if st is None or snap:
                st = {"u": u, "y": yp, "vu": 0.0, "vy": 0.0, "s": s, "a": 1.0 if snap else 0.0}
                self.labs[key] = st
            st.update(tu=u, ty=yp, ts=s, ta=1.0)
        for key, st in self.labs.items():
            if key[0] == layer and key not in keep:
                st["ta"] = 0.0

    def _label_job(self, dt: float, logr: float) -> None:
        """Every LABEL_PERIOD: the live mountain's 48 x 24 windows, 96 per frame, then the regions."""
        if not self.started:
            return
        if self.job is None:
            self.job_t -= dt
            if self.job_t > 0.0:
                return
            self.job_t = LABEL_PERIOD
            slot, n, _now, _o, span, org = self._live_view()
            if n < 3:
                return
            s, e, L, inside = self._grid(span, org, float(n), logr)
            self.job = {"slot": slot, "n": n, "s": s, "e": e, "L": L, "in": inside, "i": 0,
                        "ids": -np.ones(s.size, np.int64), "clar": np.zeros(s.size), "layer": self.layer}
            return
        j = self.job
        step = (GRID_W * GRID_H) // LABEL_FRAMES
        i = j["i"]
        if i < j["s"].size:
            sl = slice(i, i + step)
            ids, clar, _fog = windows_keys(self.S[j["slot"]], j["n"], j["s"][sl], j["e"][sl], j["L"][sl])
            j["ids"][sl] = np.where(j["in"][sl], ids, -1)
            j["clar"][sl] = clar
            j["i"] = i + step
            return
        self.job = None
        if j["slot"] != self.cur or j["layer"] != self.layer:
            return
        self._targets(self.layer, regions(j["ids"].reshape(GRID_H, GRID_W), j["clar"].reshape(GRID_H, GRID_W)))
        # the summit's key tints the sky
        tot = self.S[j["slot"], j["n"]] - self.S[j["slot"], 0]
        k, conf = T.key_estimate(tot)
        if k >= 0:
            self.sky_tgt = np.array(key_rgb(k, self._hue_deg)) * (0.35 + 0.65 * conf)

    def _ease_labels(self, dt: float) -> None:
        a = 1.0 - math.exp(-dt / 0.5) if dt > 0 else 0.0
        w = 2.6                                       # a critically damped spring (rad/s)
        h = min(dt, 0.1)
        dead = []
        for key, st in self.labs.items():
            for p, v in (("u", "vu"), ("y", "vy")):
                x, vel = st[p], st[v]
                acc = w * w * (st["t" + p] - x) - 2.0 * w * vel
                vel += acc * h
                st[v], st[p] = vel, x + vel * h
            st["s"] += (st["ts"] - st["s"]) * a
            st["a"] += (st["ta"] - st["a"]) * (1.0 - math.exp(-dt / 0.6) if dt > 0 else 0.0)
            if st["ta"] == 0.0 and st["a"] < 0.01:
                dead.append(key)
        for key in dead:
            del self.labs[key]

    # -- render ---------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = float(dt) if dt == dt and 0.0 < dt < math.inf else 0.0
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        logr = min(max(float(p["log_rows"]), 0.0), 1.0)
        self._hue_deg = 180.0 * min(max(float(p["hue_shift"]), 0.0), 1.0)
        self.clock += dt
        tau = 0.08 if pulse > self.kick else 0.35     # kicks ease in, then fall away
        self.kick += (pulse - self.kick) * (1.0 - math.exp(-dt / tau) if dt > 0 else 0.0)
        self.sheen += (0.06 + 0.12 * e + 0.12 * self.kick) * spd * dt
        self.sky += (0.006 + 0.012 * e) * spd * dt
        self.breath += 0.065 * spd * dt

        self._timeline(dt, float(p["piece_gap"]))
        if self.first and dt > 0:
            self.ex_w = max(0.0, self.ex_w - dt / EX_FADE_S)
        if self.th_slot >= 0 and self.th_p < 1.0:
            self.th_p = min(1.0, self.th_p + dt / THUMB_S)
        self._label_job(dt, logr)
        if not self.started and abs(logr - self.ex_logr) > 0.04:
            self._example_labels(p)                   # the example's names follow its shape
        if self.ex_w <= 0.0 and any(k[0] == 1 for k in self.labs):
            self.labs = {k: v for k, v in self.labs.items() if k[0] != 1}
        self._ease_labels(dt)
        a_sky = 1.0 - math.exp(-dt / 2.0) if dt > 0 else 0.0
        self.sky_rgb += (self.sky_tgt - self.sky_rgb) * a_sky

        A = self.A
        A.fill(0.0)
        A[0], A[1] = self.w, self.h
        A[2], A[3], A[4] = logr, min(max(float(p["wave_mix"]), 0.0), 1.0), self._hue_deg
        A[5], A[6] = min(max(float(p["lead_lines"]), 0.0), 1.0), min(max(float(p["labels"]), 0.0), 1.0)
        A[7], A[8], A[9] = e, self.kick, float(p["intensity"])
        A[10], A[11] = self.sheen % 1000.0, self.sky % 1000.0
        A[12] = 1.0 + 0.04 * math.sin(2 * math.pi * self.breath)
        A[13] = 0.14 + 0.14 * e + 0.08 * self.kick
        A[14] = 0.80 + 0.28 * e + 0.12 * self.kick
        A[15] = self.sc
        ang = math.radians(-58.0)
        A[16], A[17] = math.cos(ang), math.sin(ang)
        A[18] = 2 * math.pi / (1.15 * self.w)
        A[19], A[20] = self.gw, self.gh
        A[21] = self.clock % 1000.0

        x0, y0, x1, y1 = self._rect()
        # view 0: the live session
        slot, n, now, opn, span, org = self._live_view()
        fresh = self.started and not self.frozen
        glow = (0.10 + 0.22 * e + 0.30 * self.kick) * (1.0 - T.smoothstep(0.5, 3.0, self.quiet)) if fresh else 0.0
        V = A[O_V:O_V + VW]
        if self.started:
            V[:] = (1.0, slot, n, now, opn, span, x0, y0, x1, y1, 1.0, glow, 0.0, L_MIN, org, self.sc)
            if opn:
                f = max(self.tbin / BIN_S, 0.25)
                np.add(self.S[slot, n], self.acc / f, out=self.open_row)
                A[O_OPEN:O_OPEN + 12] = self.open_row
        # view 1: the example (dimmed), until the first note has faded it
        if self.ex_w > 0.0:
            V = A[O_V + VW:O_V + 2 * VW]
            V[:] = (self.ex_w, 2, self.n[2], float(self.n[2]), 0, EX_SPAN, x0, y0, x1, y1, 0.74, 0.0, 0.0, L_MIN, 0.0,
                    self.sc)
        # view 2: the previous session, shrinking into its frame at the top left
        thumb = None
        if self.th_slot >= 0 and self.th_slot != self.cur:
            q = self.th_p * self.th_p * (3.0 - 2.0 * self.th_p)
            tx0, ty0 = TH_X * self.w, TH_Y * self.h + 0.03 * self.h
            tx1, ty1 = tx0 + THUMB_SCALE * (x1 - x0), ty0 + THUMB_SCALE * (y1 - y0)
            r = [x0 + (tx0 - x0) * q, y0 + (ty0 - y0) * q, x1 + (tx1 - x1) * q, y1 + (ty1 - y1) * q]
            tnow = self.th_now
            torg = max(0.0, tnow - CAP)
            tspan = min(max(tnow - torg, V_MIN), float(CAP))
            thumb = (r, q)
            V = A[O_V + 2 * VW:O_V + 3 * VW]
            V[:] = (1.0, self.th_slot, self.th_n, tnow, 0, tspan, r[0], r[1], r[2], r[3], 0.9, 0.0, q * 0.85, L_MIN,
                    torg, self.sc * (0.35 + 0.65 * (1.0 - q)))
        A[O_SKY:O_SKY + 3] = self.sky_rgb
        # the strip under whichever mountain is in front
        A[O_STRIP] = 0.0 if self.started else 1.0
        A[O_STRIP + 1], A[O_STRIP + 2] = STRIP_Y * self.h, STRIP_H * self.h
        A[O_STRIP + 3] = 0.55 if self.started else 0.4 * self.ex_w
        # labels (the example's in layer 1, which fade with it; each live session its own layer)
        i = 0
        cell = (x1 - x0, y1 - y0)
        for (layer, k), st in sorted(self.labs.items(), key=lambda kv: -kv[1]["a"]):
            if i >= N_LAB:
                break
            a = st["a"] * (self.ex_w if layer == 1 else 1.0)
            lx0, ly1, lw, lh, ls = x0, y1, cell[0], cell[1], st["s"]
            if layer not in (1, self.layer):          # the old mountain's names shrink with it, fading
                if thumb is None:
                    continue
                r, q = thumb
                lx0, ly1, lw, lh = r[0], r[3], r[2] - r[0], r[3] - r[1]
                ls *= lw / cell[0]
                a *= (1.0 - q) ** 2
            if a < 0.004:
                continue
            A[O_LAB + 8 * i:O_LAB + 8 * i + 5] = (lx0 + st["u"] * lw, ly1 - st["y"] * lh, max(ls, 0.05), a, k)
            i += 1
        # the ribbon: the room's key (or our own tracker's) above the live summit
        kr = self.keyr
        rib = self.rib
        want = 1.0 if (self.started and kr.known and kr.source != "default") else 0.0
        ar = 1.0 - math.exp(-dt / 0.6) if dt > 0 else 0.0
        rib["a"] += (want - rib["a"]) * ar
        if kr.known:
            rib["rgb"] += (np.array(key_rgb(kr.key, self._hue_deg, 0.16)) - rib["rgb"]) * ar
            rib["g"] = kr.key
        if self.started:
            L_top = now - org
            yp = apex_y(L_top, span, logr) if L_top > L_MIN else 0.0
            tx = x0 + (org + 0.5 * L_top - org) / span * (x1 - x0)
            ty = y1 - yp * (y1 - y0) - 0.03 * self.h
            if rib["a"] < 0.02:
                rib["x"], rib["y"] = tx, ty
            rib["x"] += (tx - rib["x"]) * ar
            rib["y"] += (ty - rib["y"]) * ar
        R = A[O_RIB:O_RIB + 12]
        R[0], R[1], R[2], R[3] = rib["x"], max(rib["y"], 0.075 * self.h), 0.085 * self.w, 0.009 * self.h
        R[4:7] = rib["rgb"]
        R[7], R[8], R[9] = rib["a"], rib["g"], 0.42

        self.arena.set(A)
        launch_2d(self.k_eval, self.w, self.h, (self.col, self.info, self.arena, self.Sg))
        launch_2d(self.k_comp, self.w, self.h, (self.buf, self.col, self.info, self.arena, self.Sg, self.tex,
                                                self.atlas, np.uint32(self.seed)))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = self.col = self.info = self.arena = self.Sg = self.tex = self.atlas = None
