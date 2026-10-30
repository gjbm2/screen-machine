"""Key loom: the piano's key analysis woven, row by row, into a cloth on a loom.

What the picture shows
  A loom in a dark studio, softly lit from the upper left.  A length of woven cloth hangs between a wooden cloth
  beam at the top and the fell near the bottom: the line where new cloth is being woven.  Below the fell the bare
  warp threads run down through the beater.  A wooden shuttle glides back and forth just under the fell, laying the
  newest weft thread, and its bobbin glows in that thread's colour.

  The cloth is the music.  Each row of weft is one moment.  The newest row is woven at the fell as you play (the
  half-second still sounding is woven provisionally and settles as it closes), and the cloth rises off the loom as
  the piece goes on: about a minute of music is in view, older rows roll up onto the beam.  When the piano rests,
  the loom rests and the cloth stops rising.  Across the width, each warp thread listens at its own timescale: at
  the left edge one second (the colours of the moment, changing from row to row), further right ever longer
  stretches, and at the right edge the whole piece up to that row (the slow key, in long steady bands).  ``mirror``
  folds this into a symmetric rug: the quick colours in both borders and the slow key down the middle.

  Colour is the key, as in the keyscape: the hue goes round the circle of fifths, major keys are light, minor keys
  deeper.  Where the music is clearly in a key, the wool is dyed, and diamond (lozenge) motifs in that key's colour
  grow out of the weave: the clearer the key, the bigger the diamond.  Each has a dark outline, an inner diamond in
  the colour of the key's dominant, and a small eye at the centre: cream when the key is the piece's home key, dark
  ink when it is far from home.  Where the key is ambiguous, the wool stays plain undyed grey-beige.  Where there
  was little sound, the weave is loose and open and shows the dark behind it.  The warp threads are ikat-dyed with
  the Wavescapes colour (the phase of the fifth Fourier coefficient): where the two analyses agree the cloth is one
  colour, where they differ it is shot with two.  The selvedges carry the key of everything heard up to that row.
  When a piece ends (``piece_gap`` seconds of silence) and the next one begins, the loom advances a hand's width of
  bare warp and starts a new cloth; the old one keeps rising above it.  Keyscape's example piece is the cloth already
  on the loom: it fills the loom at rest, and the first piece played is woven straight on from it, row after row, with
  no gap (also when the scene comes on with a piece already under way: the example hangs above its first row).

How the analysis maps onto it
  The shared key field (_lib/keyfield.py: keyscape's maths) in TRAILING mode over the whole piece.  Field u is the
  row's moment: each window ends there, so it is what had been heard by then, and a row barely changes once woven.
  Field v is the thread's timescale (1 s up to the whole piece; ``log_rows`` mixes arithmetic and logarithmic
  spacing).  Pass 1 works at thread resolution: for each crossing of warp i and weft j, the key, clarity, fog and
  F5 of that cell give the weft's dye, the warp's ikat colour and how open the weave is.  For each lozenge (a
  diamond lattice 16 threads across, quantised to whole threads so the edges step like a kilim), the key, clarity
  and home distance at its centre set its colours and size.  Pass 2 shades every pixel as yarn.  The weave (plain,
  2/2 twill, 5-end sateen, morphed by a seeded dissolve) decides which yarn is on top at each crossing.  Each yarn
  is a cylinder with ply twist, slubs, fuzz, a dip where it dives under, and an anisotropic (Kajiya-Kay) sheen that
  follows the drape.  Then come the loom's wood, the shuttle and the light.

Macros: energy -> the size and pace of the sway, the depth of the drape's folds, the sheen, the shuttle's pace and
the glow of the newest rows; pulse -> a kick (eased in, never a flash) to the shuttle's glow, the newest rows'
brightness and the sway's pace; speed -> the sway, the shuttle and the light's breathing; intensity -> gain.  No
palette: the key colours are the content; ``hue_shift`` turns them (0..1 = 0..180 degrees).  Alive with no notes:
the cloth sways and its sheen moves, the shuttle glides and the light breathes; until the first note, the loom
shows keyscape's example piece, woven over the whole length of cloth in view.

Source: Original (inspired by C. S. Sapp, "Visual hierarchical key analysis", ACM Computers in Entertainment 3(4),
        2005, and C. Viaccoz, D. Harasim, F. C. Moss, M. Rohrmeier, "Wavescapes", Musicae Scientiae 2022, through
        the shared key field; weaving drafts (plain, twill, sateen) and kilim lozenges are traditional; yarn sheen
        after Kajiya & Kay, "Rendering fur with three dimensional textures", SIGGRAPH 1989.  No code borrowed.)
Author: smfx (original)
License: MIT (original)
license_class: permissive
"""
from __future__ import annotations

import logging
import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib import keyfield as KF
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

log = logging.getLogger("stream_service.plugins.key_loom")

FW, FH = 2048, 64               # the key field's planes: time (u) x timescale (v)
VIS = 120.0                     # bins of music in view between the fell and the beam (60 s)
GAP_BINS = 8.0                  # bare warp between two pieces (bins of cloth)
GAP_TAU = 0.6                   # s: the loom advances that gap with this time constant
NC_MAX, NR_MAX = 576, 320       # the crossing buffer: warp threads x picks (thread_size is clamped to fit)
MS = 16.0                       # the lozenge lattice's diagonal (threads)
Q_WRAP = 80000.0                # the pick counter wraps here (a multiple of 2, 4, 5 and 16: the drafts' periods)
THREAD_PX = 6.0                 # a thread's width at thread_size 1, in 1080p pixels

# layout (fractions of the internal frame)
CX0, CX1 = 0.10, 0.90           # the cloth's selvedges (at rest)
Y_TOP = 0.06                    # the cloth beam's underside
Y_FELL = 0.80                   # the fell: where the newest row is woven
SH_Y, SH_HL, SH_HH = 0.030, 0.055, 0.0125     # the shuttle: centre below the fell (h), half length (w), half height (h)
BEAT0, BEAT1 = 0.858, 0.886     # the beater bar (h)
POST0, POST1, POST_HW = 0.045, 0.955, 0.011   # the loom's posts (w)

# light (unit vectors): the key light from the upper left, and its half vector with a frontal view
_L = np.array([-0.42, -0.55, 0.72])
_L /= np.linalg.norm(_L)
_H = _L + np.array([0.0, 0.0, 1.0])
_H /= np.linalg.norm(_H)
WOOL = (0.745, 0.006, 0.030)    # undyed wool, OKLab

# the arena (floats): host -> both kernels
_AR_NAMES = ("W H FW FH CX NHALF TP YFELL DPX QF J0 NR PPB "
             "FT NOW LO ORG SPAN CV PV PFT PNOW PLO PORG PSPAN "
             "MIRROR WEAVE MOTIF HUE GLOW EN KICK GAIN "
             "PH1 PH2 PH3 PH4 PH5 SWAY FOLD SHEEN SATIN "
             "SHX SHX0 SHLINE SHGLOW SHY SHHL SHHH "
             "BREATH SC TR TG TB BEAT0 BEAT1 POST0 POST1 POSTHW BEAMH CL CR").split()
AR = {n: i for i, n in enumerate(_AR_NAMES)}
N_AR = 64
assert len(_AR_NAMES) <= N_AR

_DEFS = "".join(f"#define KA_{n} {i}\n" for n, i in AR.items()) + "".join(
    f"#define {k} {v!r}f\n" for k, v in (("KL_LX", float(_L[0])), ("KL_LY", float(_L[1])), ("KL_LZ", float(_L[2])),
                                         ("KL_HX", float(_H[0])), ("KL_HY", float(_H[1])), ("KL_HZ", float(_H[2])),
                                         ("KL_WOOL_L", WOOL[0]), ("KL_WOOL_A", WOOL[1]), ("KL_WOOL_B", WOOL[2]),
                                         ("KL_MS", MS))) + f"#define KL_NAR {N_AR}\n#define KL_NC {NC_MAX}\n" \
    f"#define KL_NR {NR_MAX}\n"

_SRC = HELPERS + KF.KEYFIELD_CUDA + _DEFS + r"""
#define KL_PI 3.14159265f
#define KL_TAU 6.2831853f

__device__ __forceinline__ float3 kl_mix3(float3 a, float3 b, float t) {
    return make_float3(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t);
}
__device__ __forceinline__ float3 kl_scale(float3 a, float k) { return make_float3(a.x * k, a.y * k, a.z * k); }
// OKLab -> linear sRGB, clamped at 0
__device__ __forceinline__ float3 kl_lin(float3 lab) {
    const float3 c = kf_oklab_lin(lab.x, lab.y, lab.z);
    return make_float3(fmaxf(kf_fin(c.x), 0.f), fmaxf(kf_fin(c.y), 0.f), fmaxf(kf_fin(c.z), 0.f));
}
__device__ __forceinline__ int kl_imod(int a, int m) { const int r = a % m; return r < 0 ? r + m : r; }
// a thread's own random number 0..1
__device__ __forceinline__ float kl_hf(int i, unsigned int s) {
    return (hash_u((unsigned int)i * 0x9E3779B1u ^ s) >> 8) * (1.f / 16777216.f);
}
// 1 when the warp is on top at crossing (i, j): plain -> 2/2 twill -> 5-end sateen (weft-faced), a seeded dissolve
__device__ __forceinline__ int kl_up(int i, int j, float wv, unsigned int seed) {
    const int pl = (i + j) & 1;
    const int tw = kl_imod(i - j, 4) < 2 ? 1 : 0;
    const int sa = kl_imod(2 * i + j, 5) == 0 ? 1 : 0;
    const float h = (hash_u((unsigned int)i * 73856093u ^ (unsigned int)j * 19349663u ^ seed) >> 8) * (1.f / 16777216.f);
    return wv < 0.5f ? (h < 2.f * wv ? tw : pl) : (h < 2.f * wv - 1.f ? sa : tw);
}
// the field's timescale v at a place X (0..1 across the cloth): quick left -> slow right, or (mirror) the rug's fold
__device__ __forceinline__ float kl_v(float X, float mirror) {
    X = clamp01(X);
    const float sym = 1.f - fabsf(2.f * X - 1.f);
    return clamp01(X + (sym - X) * clamp01(mirror));
}

struct KLs { int key; float clar, fog, home; float4 c; };
// one look at the key field: the key (nearest), clarity, fog and home (bilinear) and plane C (F5, density)
__device__ __forceinline__ KLs kl_field(const float4* __restrict__ A, const float4* __restrict__ B,
                                        const float4* __restrict__ C, int fw, int fh, float u, float v) {
    KLs s;
    const float4 an = kf_tex_nearest(A, fw, fh, u, v);
    const float4 ab = kf_tex(A, fw, fh, u, v);
    s.key = kf_key_id(an.x);
    s.clar = clamp01(kf_fin(ab.y));
    s.fog = clamp01(kf_fin(ab.z));
    s.home = clamp01(kf_fin(kf_tex(B, fw, fh, u, v).w));
    const float4 c = kf_tex(C, fw, fh, u, v);
    s.c = make_float4(kf_fin(c.x), kf_fin(c.y), kf_fin(c.z), kf_fin(c.w));
    return s;
}

// pass 1, one thread per crossing (warp i, weft j) in view: R0 = (weft linear rgb, weft fill), R1 = (warp rgb, warp
// fill), R2 = (glow, woven 0..1, -, -).  Rows: the current piece (field F*), the previous one (Q*), or bare warp.
extern "C" __global__
void kl_cells(float4* __restrict__ R, const float* __restrict__ Ar,
              const float4* __restrict__ FA, const float4* __restrict__ FB, const float4* __restrict__ FC,
              const float4* __restrict__ QA, const float4* __restrict__ QB, const float4* __restrict__ QC)
{
    __shared__ float P[KL_NAR];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    for (int k = tid; k < KL_NAR; k += blockDim.x * blockDim.y) P[k] = Ar[k];
    __syncthreads();
    const int ci = blockIdx.x * blockDim.x + threadIdx.x, r = blockIdx.y * blockDim.y + threadIdx.y;
    const int NR = min((int)P[KA_NR], KL_NR);
    if (ci >= KL_NC || r >= NR) return;
    const int fw = max((int)P[KA_FW], 2), fh = max((int)P[KA_FH], 2);
    const float tp = fmaxf(P[KA_TP], 0.5f), nh = fmaxf(P[KA_NHALF], 1.f), ppb = fmaxf(P[KA_PPB], 1e-3f);
    const float qf = P[KA_QF], hs = P[KA_HUE], mir = P[KA_MIRROR], motif = P[KA_MOTIF];
    const int i = ci - KL_NC / 2;                     // the warp thread, counted from the cloth's centre line
    const int j = (int)P[KA_J0] + r;                  // the weft pick
    const float ac = (float)i + 0.5f, bc = (float)j + 0.5f;
    const float woven = clamp01(qf - (float)j);       // the newest pick weaves in as the fell crosses it
    const float dbin = (qf - fminf(bc, qf)) * tp / ppb;   // bins above the fell
    int kind = 0;
    float t = 0.f;
    if (woven > 0.f) {
        const float tc = P[KA_FT] - dbin;
        if (P[KA_CV] > 0.5f && tc >= P[KA_LO] && tc <= P[KA_NOW]) { kind = 1; t = tc; }
        else if (P[KA_PV] > 0.5f) {
            const float to = P[KA_PFT] - dbin;
            if (to >= P[KA_PLO] && to <= P[KA_PNOW]) { kind = 2; t = to; }
        }
    }
    const float3 wool = make_float3(KL_WOOL_L, KL_WOOL_A, KL_WOOL_B);
    float3 weft = wool, warp = make_float3(KL_WOOL_L * 0.86f, KL_WOOL_A, KL_WOOL_B);
    float fillf = 0.6f, fillw = 0.6f, glow = 0.f;
    if (kind > 0) {
        const float4* __restrict__ A = kind == 1 ? FA : QA;
        const float4* __restrict__ B = kind == 1 ? FB : QB;
        const float4* __restrict__ C = kind == 1 ? FC : QC;
        const float org = kind == 1 ? P[KA_ORG] : P[KA_PORG];
        const float span = fmaxf(kind == 1 ? P[KA_SPAN] : P[KA_PSPAN], 1e-3f);
        const float lo = kind == 1 ? P[KA_LO] : P[KA_PLO];
        const float nowp = kind == 1 ? P[KA_NOW] : P[KA_PNOW];
        const float ft = kind == 1 ? P[KA_FT] : P[KA_PFT];
        const float X = 0.5f + ac / (2.f * nh);
        const KLs s = kl_field(A, B, C, fw, fh, (t - org) / span, kl_v(X, mir));
        // the weft: undyed wool, dyed toward the key as far as the key is clear; fog darkens and loosens it
        const float dye = s.key >= 0 ? smooth(0.08f, 0.7f, s.clar) * (1.f - s.fog) : 0.f;
        weft = kl_mix3(wool, kf_key_lab((float)s.key, 1.f, hs), 0.55f * dye);
        weft.x *= 1.f - 0.22f * s.fog;
        // the warp: ikat-dyed with the wavescape colour (F5's phase), muted toward the wool
        warp = kl_mix3(wool, kf_wave_lab(s.c, hs), 0.62f * (1.f - s.fog));
        warp.x *= 0.88f;
        fillf = 0.97f - 0.45f * s.fog;
        fillw = 0.95f - 0.32f * s.fog;
        // the lozenges: a diamond lattice in thread units, its size by the clarity at the diamond's centre
        if (motif > 0.001f) {
            const float p = (ac + bc) * (1.f / KL_MS), q = (ac - bc) * (1.f / KL_MS);
            const float p0 = floorf(p) + 0.5f, q0 = floorf(q) + 0.5f;
            const float dn = 2.f * fmaxf(fabsf(p - p0), fabsf(q - q0));        // 0 at the centre, 1 at the cell's edge
            const float a0 = 0.5f * KL_MS * (p0 + q0), b0 = 0.5f * KL_MS * (p0 - q0);
            const float db0 = (qf - fminf(b0, qf)) * tp / ppb;                 // its centre's bins above the fell
            // a diamond across the hand-off between two cloths is woven whole, in the cloth at its centre
            const float4 *MA = A, *MB = B, *MC = C;
            float mft = ft, mlo = lo, mnow = nowp, morg = org, mspan = span;
            if (kind == 2 && P[KA_CV] > 0.5f) {
                const float tq = P[KA_FT] - db0;
                if (tq >= P[KA_LO] && tq <= P[KA_NOW]) {
                    MA = FA; MB = FB; MC = FC;
                    mft = P[KA_FT]; mlo = P[KA_LO]; mnow = P[KA_NOW]; morg = P[KA_ORG]; mspan = fmaxf(P[KA_SPAN], 1e-3f);
                }
            } else if (kind == 1 && P[KA_PV] > 0.5f) {
                const float tq = P[KA_PFT] - db0;
                if (P[KA_FT] - db0 < P[KA_LO] && tq >= P[KA_PLO] && tq <= P[KA_PNOW]) {
                    MA = QA; MB = QB; MC = QC;
                    mft = P[KA_PFT]; mlo = P[KA_PLO]; mnow = P[KA_PNOW]; morg = P[KA_PORG];
                    mspan = fmaxf(P[KA_PSPAN], 1e-3f);
                }
            }
            const float t0 = fminf(fmaxf(mft - db0, mlo), mnow);
            const KLs m = kl_field(MA, MB, MC, fw, fh, (t0 - morg) / mspan, kl_v(0.5f + a0 / (2.f * nh), mir));
            float rr = m.key >= 0 ? motif * smooth(0.3f, 0.85f, m.clar) * (1.f - m.fog) : 0.f;
            rr = fminf(rr, 0.97f);
            const float hm = 0.5f * KL_MS;
            const float tin = (rr - dn) * hm;                                  // threads in from its edge
            if (tin > 0.f && m.key >= 0) {
                float3 body = kf_key_lab((float)m.key, 1.f, hs);
                body.y *= 1.12f; body.z *= 1.12f;
                float3 c = body;
                if (tin <= 1.f) {
                    c = make_float3(body.x * 0.52f, body.y * 0.85f, body.z * 0.85f);       // the outline
                } else {
                    const float tin2 = (0.5f * rr - dn) * hm;
                    if (rr > 0.42f && tin2 > 0.f) {                                // the inner diamond: the dominant
                        const int kd = (m.key >= 12 ? 12 : 0) + (m.key % 12 + 7) % 12;
                        const float3 dom = kf_key_lab((float)kd, 1.f, hs);
                        c = tin2 <= 1.f ? make_float3(dom.x * 0.55f, dom.y * 0.85f, dom.z * 0.85f) : dom;
                        if (rr > 0.6f && dn * hm < 1.01f)                        // the eye: cream home, ink abroad
                            c = kl_mix3(make_float3(0.93f, 0.004f, 0.03f), make_float3(0.20f, 0.f, -0.01f),
                                        smooth(0.2f, 0.6f, m.home));
                    }
                }
                weft = c;
                warp = kl_mix3(warp, c, 0.72f);
                fillf = fmaxf(fillf, 0.95f);
                fillw = fmaxf(fillw, 0.92f);
            }
        }
        // the selvedges: the key of everything heard by this row (the field's top row), a deep shade
        if (fabsf(ac) > nh - 2.5f) {
            const KLs e = kl_field(A, B, C, fw, fh, (t - org) / span, 1.f - 0.5f / (float)fh);
            float3 sel = e.key >= 0 ? kf_key_lab((float)e.key, 0.8f, hs) : wool;
            sel.x *= 0.62f;
            weft = sel;
            warp = make_float3(sel.x * 0.9f, sel.y, sel.z);
            fillf = fillw = 0.97f;
        }
        if (kind == 1) glow = P[KA_GLOW] * __expf(-dbin * 0.8f);
    }
    const float3 wf = kl_lin(weft), wp = kl_lin(warp);
    const size_t o = (size_t)r * KL_NC + ci, PL = (size_t)KL_NR * KL_NC;
    R[o] = make_float4(wf.x, wf.y, wf.z, fillf);
    R[PL + o] = make_float4(wp.x, wp.y, wp.z, fillw);
    R[2 * PL + o] = make_float4(glow, kind > 0 ? woven : 0.f, 0.f, 0.f);
}

// a yarn's light.  xn: -1..1 across it; e: 1 riding on top .. 0 where it dives under (dipdir: which way it dives);
// weft: runs across (else down); hx, hy: the drape's slopes; tw: ply twist; lk: the light here
__device__ __forceinline__ float3 kl_yarn(float3 c, float xn, float e, float dipdir, int weft, float hx, float hy,
                                          float sheen, float pw, float tw, float lk)
{
    xn = fminf(fmaxf(xn, -1.f), 1.f);
    const float z = sqrtf(fmaxf(1.f - xn * xn, 0.04f));
    const float dip = (1.f - e) * dipdir * 0.75f;
    float nx = (weft ? dip : xn) - hx, ny = (weft ? xn : dip) - hy, nz = z;
    const float inv = rsqrtf(nx * nx + ny * ny + nz * nz + 1e-6f);
    nx *= inv; ny *= inv; nz *= inv;
    const float dif = fmaxf(nx * KL_LX + ny * KL_LY + nz * KL_LZ, 0.f);
    const float ao = (0.55f + 0.45f * z) * (0.55f + 0.45f * e);
    // Kajiya-Kay: the highlight where the yarn's tangent (tilted by the drape) is square to the half vector
    const float tx = weft ? 1.f : 0.f, ty = weft ? 0.f : 1.f, tz = weft ? hx : hy;
    const float th = (tx * KL_HX + ty * KL_HY + tz * KL_HZ) * rsqrtf(tx * tx + ty * ty + tz * tz);
    const float sp = sheen * __powf(fmaxf(1.f - th * th, 0.f), pw) * z * z * e * lk;
    const float k = lk * tw * ao * (0.26f + 0.95f * dif);
    return make_float3(c.x * k + sp * 0.9f, c.y * k + sp * 0.88f, c.z * k + sp * 0.82f);
}
// bare warp (no weft): a straight yarn every thread, the room between
__device__ __forceinline__ float3 kl_bare(float3 bg, float3 wc, float fill, float a, float b, int i, float hx,
                                          float hy, float sheen, float pw, float lk, float aa, unsigned int seed)
{
    const float hi = kl_hf(i, seed);
    const float sw = 1.f + 0.07f * (2.f * hi - 1.f) + 0.05f * __sinf(b * 0.83f + 6.28f * hi) * __sinf(b * 0.29f + 3.1f * hi);
    const float rw = fmaxf(0.5f * fill * sw, 0.05f);
    const float dw = a - floorf(a) - 0.5f;
    const float cov = clamp01((rw - fabsf(dw)) / aa + 0.5f);
    const float tw = 0.9f + 0.1f * __sinf(KL_TAU * (b * 2.3f + dw / rw * 0.45f) + 6.28f * hi);
    return kl_mix3(bg, kl_yarn(wc, dw / rw, 1.f, 0.f, 0, hx, hy, sheen, pw, tw, lk), cov);
}
// lit wood: a cylinder across yn (-1..1; vertical: a post, else a bar), grain along g
__device__ __forceinline__ float3 kl_wood(float yn, int vertical, float g, float lk) {
    yn = fminf(fmaxf(yn, -1.f), 1.f);
    const float z = sqrtf(fmaxf(1.f - yn * yn, 0.f));
    const float nx = vertical ? yn : 0.f, ny = vertical ? 0.f : yn;
    const float dif = fmaxf(nx * KL_LX + ny * KL_LY + z * KL_LZ, 0.f);
    const float gr = 0.82f + 0.18f * __sinf(g * 0.11f + 2.4f * __sinf(g * 0.013f + yn * 1.7f));
    const float sp = __powf(fmaxf(nx * KL_HX + ny * KL_HY + z * KL_HZ, 0.f), 24.f) * 0.30f * lk;
    const float k = (0.12f + 0.85f * dif) * lk * gr;
    return make_float3(0.20f * k + sp, 0.095f * k + sp * 0.9f, 0.04f * k + sp * 0.75f);
}

// pass 2, one thread per pixel: the room, the cloth (drape, weave, yarn), the bare warp, the pick being laid, the
// shuttle, the loom's wood, then gain and sRGB
extern "C" __global__
void kl_draw(uchar4* __restrict__ out, const float4* __restrict__ R, const float* __restrict__ Ar,
             const unsigned int seed)
{
    __shared__ float P[KL_NAR];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    for (int k = tid; k < KL_NAR; k += blockDim.x * blockDim.y) P[k] = Ar[k];
    __syncthreads();
    const int W = (int)P[KA_W], Hh = (int)P[KA_H];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= Hh) return;
    const float Wf = (float)W, Hf = (float)Hh;
    const float px = x + 0.5f, py = y + 0.5f;
    const float sc = fmaxf(P[KA_SC], 0.1f);
    const float tp = fmaxf(P[KA_TP], 0.5f), cx = P[KA_CX], nh = fmaxf(P[KA_NHALF], 1.f);
    const float yf = P[KA_YFELL], dpx = fmaxf(P[KA_DPX], 1.f);
    const float qf = P[KA_QF];
    const int j0 = (int)P[KA_J0], NR = max(min((int)P[KA_NR], KL_NR), 1);
    const float wv = P[KA_WEAVE], sheen = P[KA_SHEEN], pw = fmaxf(P[KA_SATIN], 1.f);
    const float aa = 0.8f / tp;                       // a pixel, in thread units
    const size_t PL = (size_t)KL_NR * KL_NC;
    const int rf = min(max((int)floorf(qf - 0.01f) - j0, 0), NR - 1);   // the newest pick's row
    const float ux = px / Wf, vy = py / Hf;
    // the studio: a soft key light from the upper left on a dark warm wall, faintly lit by the piece's home key
    const float pool = P[KA_BREATH] * (0.38f + 0.78f * __expf(-((ux - 0.42f) * (ux - 0.42f) * 3.0f
                                                               + (vy - 0.32f) * (vy - 0.32f) * 2.4f)));
    const float wall = (0.55f + 0.45f * (1.f - vy)) * pool;
    const float3 room = make_float3((0.014f + 0.045f * P[KA_TR]) * wall, (0.012f + 0.045f * P[KA_TG]) * wall,
                                    (0.011f + 0.045f * P[KA_TB]) * wall);
    float3 col = room;
    const float3 woolc = kl_lin(make_float3(KL_WOOL_L * 0.86f, KL_WOOL_A, KL_WOOL_B));

    if (py < yf) {
        // the drape: anchored at the fell and at the beam; a lateral sway travelling up and slow folds (slopes)
        const float s = clamp01((yf - py) / dpx);
        const float env = __sinf(KL_PI * s);
        const float k1 = KL_TAU / (0.95f * Hf), k2 = KL_TAU / (0.41f * Wf);
        const float xc = px - env * P[KA_SWAY] * (0.7f * __sinf(k1 * py + P[KA_PH1])
                                                  + 0.3f * __sinf(1.73f * k1 * py - P[KA_PH2]));
        const float yc = py - env * P[KA_SWAY] * 0.22f * __sinf(k2 * px + P[KA_PH3]);
        const float kx1 = KL_TAU / (0.23f * Wf), ky1 = KL_TAU / (0.6f * Hf);
        const float kx2 = KL_TAU / (0.13f * Wf), ky2 = KL_TAU / (0.5f * Hf);
        const float cy1 = __cosf(ky1 * py + P[KA_PH4]);
        const float g1 = __cosf(kx1 * px + 0.9f * __sinf(ky1 * py + P[KA_PH4]) + 0.6f * P[KA_PH3]);
        const float g2 = __cosf(kx2 * px - ky2 * py + P[KA_PH5]);
        const float S = P[KA_FOLD] * env;
        const float hx = S * (0.65f * g1 + 0.21f * g2);
        const float hy = S * (0.65f * g1 * 0.9f * cy1 * (ky1 / kx1) - 0.21f * g2 * (ky2 / kx2));
        const float a = (xc - cx) / tp;
        const float d = fmaxf(yf - yc, 0.f);
        if (d <= dpx + 0.03f * Hf && fabsf(a) < nh + 1.f) {
            const float ce = clamp01((nh - fabsf(a)) * tp + 0.5f);             // the selvedge's edge
            const float b = qf - d / tp;
            const float fa0 = floorf(a), fb0 = floorf(b);
            const int i = (int)fa0, j = (int)fb0;
            const float fa = a - fa0, fb = b - fb0;
            const int ci = min(max(i + KL_NC / 2, 0), KL_NC - 1);
            const int rr = min(max(j - j0, 0), NR - 1);
            const size_t o = (size_t)rr * KL_NC + ci;
            const float4 c0 = R[o], c1 = R[PL + o], c2 = R[2 * PL + o];
            const float3 cf = make_float3(c0.x, c0.y, c0.z), cw = make_float3(c1.x, c1.y, c1.z);
            const float fz = vnoise(a * 3.3f + 11.f, b * 3.3f + 5.f, seed + 3u);      // fuzz, fixed to the cloth
            const float lk = pool * (0.9f + 0.2f * fz);
            const float wa = clamp01(c2.y);
            float3 bare = room, wov = room;
            if (wa < 1.f) bare = kl_bare(room, cw, c1.w, a, b, i, hx, hy, sheen * 0.6f, pw, lk, aa, seed);
            if (wa > 0.f) {
                const int up = kl_up(i, j, wv, seed);
                const float hi = kl_hf(i, seed), hj = kl_hf(j, seed ^ 0x51ED27u);
                // the yarns' half widths (fog opens the weave), with slubs and per-thread irregularity
                const float sw = 1.f + 0.07f * (2.f * hi - 1.f) + 0.05f * __sinf(b * 0.83f + 6.28f * hi) * __sinf(b * 0.29f + 3.1f * hi);
                const float sf = 1.f + 0.08f * (2.f * hj - 1.f) + 0.06f * __sinf(a * 0.71f + 6.28f * hj) * __sinf(a * 0.23f + 2.3f * hj);
                const float rw = fmaxf(0.5f * c1.w * sw, 0.05f), rfh = fmaxf(0.5f * c0.w * sf, 0.05f);
                const float dw = fa - 0.5f, df = fb - 0.5f;
                const float covW = clamp01((rw - fabsf(dw)) / aa + 0.5f);
                const float covF = clamp01((rfh - fabsf(df)) / aa + 0.5f);
                const float tww = 0.9f + 0.1f * __sinf(KL_TAU * (b * 2.3f + dw / rw * 0.45f) + 6.28f * hi);
                const float twf = 0.9f + 0.1f * __sinf(KL_TAU * (a * 2.3f + df / rfh * 0.45f) + 6.28f * hj);
                // the top yarn rides over this crossing; it dives where the next crossing puts the other on top
                float ew = 0.35f, ef = 0.35f;
                if (up) {
                    const int n0 = kl_up(i, j - 1, wv, seed), n1 = kl_up(i, j + 1, wv, seed);
                    ew = fminf(n0 ? 1.f : smooth(0.f, 0.42f, fb), n1 ? 1.f : smooth(1.f, 0.58f, fb));
                } else {
                    const int n0 = kl_up(i - 1, j, wv, seed), n1 = kl_up(i + 1, j, wv, seed);
                    ef = fminf(n0 ? smooth(0.f, 0.42f, fa) : 1.f, n1 ? smooth(1.f, 0.58f, fa) : 1.f);
                }
                const float3 yw = kl_yarn(cw, dw / rw, ew, fb < 0.5f ? -1.f : 1.f, 0, hx, hy, up ? sheen : 0.f,
                                          pw, tww, lk);
                const float3 yt = kl_yarn(cf, df / rfh, ef, fa < 0.5f ? -1.f : 1.f, 1, hx, hy, up ? 0.f : sheen,
                                          pw, twf, lk);
                // behind the cloth: the dark, with stray fibres across the gaps
                const float fib = 0.16f * smooth(0.6f, 0.92f, fz) * lk;
                const float3 behind = make_float3(room.x * 0.4f + (cf.x + cw.x) * fib, room.y * 0.4f + (cf.y + cw.y) * fib,
                                                  room.z * 0.4f + (cf.z + cw.z) * fib);
                wov = up ? kl_mix3(kl_mix3(behind, kl_scale(yt, 0.62f), covF), yw, covW)
                         : kl_mix3(kl_mix3(behind, kl_scale(yw, 0.62f), covW), yt, covF);
                const float gl = c2.x;                    // the newest rows glow in their own colour
                wov.x += cf.x * gl; wov.y += cf.y * gl; wov.z += cf.z * gl;
            }
            const float3 cl = wa >= 1.f ? wov : (wa <= 0.f ? bare : kl_mix3(bare, wov, wa));
            col = kl_mix3(col, cl, ce);
        }
    } else {
        // below the fell: the bare warp, feeding up into the cloth
        const float a = (px - cx) / tp;
        if (fabsf(a) < nh + 1.f) {
            const float ce = clamp01((nh - fabsf(a)) * tp + 0.5f);
            const float b = qf + (py - yf) / tp;
            col = kl_mix3(col, kl_bare(room, woolc, 0.55f, a, b, (int)floorf(a), 0.f, 0.f, sheen * 0.5f, pw,
                                       pool * 0.85f, aa, seed), ce);
        }
    }

    // the pick being laid: from the selvedge the shuttle entered at, to the shuttle, in the newest row's colours
    if (P[KA_SHLINE] > 0.004f) {
        const float yw = yf + 0.5f * tp, r_ = 0.36f * tp;
        const float dy = fabsf(py - yw);
        const float xa = fmaxf(fminf(P[KA_SHX0], P[KA_SHX]), P[KA_CL]), xb = fminf(fmaxf(P[KA_SHX0], P[KA_SHX]), P[KA_CR]);
        if (dy < r_ + 1.f && px > xa - 1.f && px < xb + 1.f) {
            const int ci = min(max((int)floorf((px - cx) / tp) + KL_NC / 2, 0), KL_NC - 1);
            const float4 c4 = R[(size_t)rf * KL_NC + ci];
            const float3 c = make_float3(c4.x, c4.y, c4.z);
            const float tw = 0.9f + 0.1f * __sinf(KL_TAU * (px / tp * 2.3f + (py - yw) / r_ * 0.45f));
            float3 yc = kl_yarn(c, (py - yw) / r_, 1.f, 0.f, 1, 0.f, 0.f, sheen, pw, tw, pool);
            const float gl = P[KA_SHGLOW] * 0.7f;
            yc.x += c.x * gl; yc.y += c.y * gl; yc.z += c.z * gl;
            const float cov = P[KA_SHLINE] * clamp01(r_ - dy + 0.5f) * clamp01(px - xa + 0.5f) * clamp01(xb - px + 0.5f);
            col = kl_mix3(col, yc, cov);
        }
    }

    // the shuttle: its light on the cloth and warp, then the boat itself with the yarn in its bobbin window
    {
        const float shx = P[KA_SHX], shy = yf + P[KA_SHY], hl = fmaxf(P[KA_SHHL], 1.f), hh = fmaxf(P[KA_SHHH], 1.f);
        const float dxs = px - shx, dys = py - shy;
        if (fabsf(dxs) < 4.f * hl && fabsf(dys) < 16.f * hh) {
            const int ci = min(max((int)floorf((shx - cx) / tp) + KL_NC / 2, 0), KL_NC - 1);
            const float4 y4 = R[(size_t)rf * KL_NC + ci];
            const float3 yarn = make_float3(y4.x, y4.y, y4.z);
            const float gx = dxs / (1.5f * hl), gy = dys / (5.f * hh);
            const float g = P[KA_SHGLOW] * __expf(-(gx * gx + gy * gy));
            col.x += yarn.x * g; col.y += yarn.y * g; col.z += yarn.z * g;
            const float dxn = dxs / hl;
            if (fabsf(dxn) < 1.f) {
                const float hy_ = hh * __powf(fmaxf(1.f - dxn * dxn, 1e-6f), 0.6f);
                const float cov = clamp01(hy_ - fabsf(dys) + 0.5f) * clamp01((1.f - fabsf(dxn)) * hl + 0.5f);
                if (cov > 0.f) {
                    const float yn = fminf(fmaxf(dys / fmaxf(hy_, 0.5f), -1.f), 1.f);
                    const float z = sqrtf(fmaxf(1.f - yn * yn, 0.f));
                    float nx = -dxn * 0.45f * z, ny = yn, nz = z + 0.05f;
                    const float in_ = rsqrtf(nx * nx + ny * ny + nz * nz + 1e-6f);
                    nx *= in_; ny *= in_; nz *= in_;
                    const float dif = fmaxf(nx * KL_LX + ny * KL_LY + nz * KL_LZ, 0.f);
                    const float hv = fmaxf(nx * KL_HX + ny * KL_HY + nz * KL_HZ, 0.f);
                    const bool tip = fabsf(dxn) > 0.8f;
                    float3 base;
                    if (tip) {
                        base = make_float3(0.42f, 0.42f, 0.45f);                    // the steel tips
                    } else {
                        const float gr = 0.8f + 0.2f * __sinf(px / sc * 0.09f + 3.f * __sinf(py / sc * 0.05f + dxn * 2.f));
                        base = make_float3(0.24f * gr, 0.115f * gr, 0.045f * gr);
                    }
                    const float sp = (tip ? 1.1f : 0.45f) * __powf(hv, tip ? 60.f : 28.f) * pool;
                    const float k = (0.14f + 0.9f * dif) * pool;
                    float3 body = make_float3(base.x * k + sp, base.y * k + sp * 0.95f, base.z * k + sp * 0.85f);
                    if (fabsf(dxn) < 0.36f && fabsf(yn) < 0.52f) {                 // the bobbin window
                        const float st = 0.78f + 0.22f * __sinf(dxs / (0.12f * hh));
                        const float yq = yn * (1.f / 0.52f);
                        const float wz = sqrtf(fmaxf(1.f - yq * yq, 0.f));
                        const float kk = (0.25f + 0.85f * wz) * st * pool * (1.f + 1.6f * P[KA_SHGLOW]);
                        const float wa = clamp01((0.36f - fabsf(dxn)) * hl + 0.5f) * clamp01((0.52f - fabsf(yn)) * hy_ + 0.5f);
                        body = kl_mix3(body, kl_scale(yarn, kk), wa);
                    }
                    col = kl_mix3(col, body, cov);
                }
            }
        }
    }

    // the loom's wood: the posts, the beater, the cloth beam (with the woven cloth wound on it)
    {
        const float hw = fmaxf(P[KA_POSTHW], 1.f);
        const float dp0 = px - P[KA_POST0], dp1 = px - P[KA_POST1];
        const float dpp = fabsf(dp0) < fabsf(dp1) ? dp0 : dp1;
        if (fabsf(dpp) < hw + 1.f)
            col = kl_mix3(col, kl_wood(dpp / hw, 1, py / sc, pool * 0.9f), clamp01(hw - fabsf(dpp) + 0.5f));
        const float b0 = P[KA_BEAT0], b1 = P[KA_BEAT1];
        const float bl = P[KA_CL] - 0.03f * Wf, br = P[KA_CR] + 0.03f * Wf;
        if (py > b0 - 1.f && py < b1 + 1.f && px > bl - 1.f && px < br + 1.f) {
            const float bm = 0.5f * (b0 + b1), bh = fmaxf(0.5f * (b1 - b0), 1.f);
            const float cov = clamp01(bh - fabsf(py - bm) + 0.5f) * clamp01(px - bl + 0.5f) * clamp01(br - px + 0.5f);
            col = kl_mix3(col, kl_wood((py - bm) / bh, 0, px / sc, pool), cov);
        }
        const float bmh = fmaxf(P[KA_BEAMH], 2.f);
        const float el = P[KA_POST0] - hw, er = P[KA_POST1] + hw;
        if (py < bmh + 1.f && px > el - 1.f && px < er + 1.f) {
            const float bm = 0.5f * bmh, bh = 0.5f * bmh;
            const float yn = (py - bm) / bh;
            float3 c = kl_wood(yn, 0, px / sc, pool);
            if (px > P[KA_CL] && px < P[KA_CR]) {         // the cloth wound on the beam: wool, tinted, in layers
                const float z = sqrtf(fmaxf(1.f - fminf(yn * yn, 1.f), 0.f));
                const float dif = fmaxf(yn * KL_LY + z * KL_LZ, 0.f);
                const float ridge = 0.8f + 0.2f * __sinf(py * KL_PI / (0.5f * tp));
                const float k = (0.18f + 0.8f * dif) * ridge * pool;
                const float3 cl = make_float3((woolc.x * 0.6f + P[KA_TR] * 0.4f) * k, (woolc.y * 0.6f + P[KA_TG] * 0.4f) * k,
                                              (woolc.z * 0.6f + P[KA_TB] * 0.4f) * k);
                c = kl_mix3(c, cl, clamp01(px - P[KA_CL] + 0.5f) * clamp01(P[KA_CR] - px + 0.5f) * clamp01((0.92f - fabsf(yn)) * bh + 0.5f));
            }
            const float cov = clamp01(bmh - py + 0.5f) * clamp01(px - el + 0.5f) * clamp01(er - px + 0.5f);
            col = kl_mix3(col, c, cov);
        }
    }

    const float gn = P[KA_GAIN];
    const float dth = (hash_u((unsigned int)(y * W + x) ^ (seed * 0x9E3779B1u)) >> 8) * (1.f / 16777216.f) - 0.5f;
    out[y * W + x] = pack(make_float4(255.f * kf_enc(kf_fin(col.x) * gn) + dth, 255.f * kf_enc(kf_fin(col.y) * gn) + dth,
                                      255.f * kf_enc(kf_fin(col.z) * gn) + dth, 255.f));
}
"""

_OPTS = ("--std=c++14", "--use_fast_math")


def _c01(v) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return min(max(v, 0.0), 1.0) if v == v else 0.0


def _fin(v, default: float = 0.0) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return v if v == v and abs(v) < 1e30 else default


class KeyLoom(Source):
    """Per frame: the shared timeline's update, the key field's bake (one launch, skipped when nothing changed), one
    arena upload and three launches (the crossings, the pixels, the upsample).  A piece change copies the field's
    planes once (device to device) so the old cloth keeps rising above the new one."""
    name = "key_loom"
    label = "Key loom"
    description = ("The piano's key analysis woven into cloth on a loom: each row is a moment woven as you play, each "
                   "thread a timescale, and clear keys grow kilim lozenges in their colour.")
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette=None, render_scale=0.75),
            "weave": num(0.0, 1.0, 0.5, "Weave (plain, twill, satin)", basic=True),
            "mirror": num(0.0, 1.0, 0.0, "Rug symmetry", basic=True),
            "thread_size": num(0.5, 2.0, 1.0, "Thread size"),
            "motif": num(0.0, 1.0, 0.7, "Lozenges"),
            "log_rows": num(0.0, 1.0, 0.85, "Timescales spread (log)"),
            "hue_shift": num(0.0, 1.0, 0.0, "Hue shift"),
            "piece_gap": num(30.0, 300.0, 90.0, "New cloth after", unit="s", tier="structural"),
        }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.sc = self.h / 1080.0
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        mod = raw_module(_SRC, _OPTS)
        self.k_cells = mod.get_function("kl_cells")
        self.k_draw = mod.get_function("kl_draw")
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.kf = KF.KeyField(ctx, FW, FH)
        self.pA = cp.zeros((FH, FW, 4), cp.float32)   # the previous piece's planes (its cloth rises above the new)
        self.pB = cp.zeros((FH, FW, 4), cp.float32)
        self.pC = cp.zeros((FH, FW, 4), cp.float32)
        self.R = cp.zeros((3, NR_MAX, NC_MAX, 4), cp.float32)
        self.arena = cp.zeros(N_AR, cp.float32)
        self.A = np.zeros(N_AR, np.float32)
        self._own_tl = None           # the private timeline when there is no sound object
        self._key = None              # (timeline, piece) on the loom
        self._baked = False
        self._first = False           # the first frame: the loom has just seen its first piece
        self.past = None              # (base, now, lo, org, span) of the previous piece's cloth
        self.base = 0.0               # cloth position (bins) of the current piece's t = 0
        self.adv = 0.0                # the displayed fell's cloth position
        self.g = 0.0                  # the gap still to advance (bins)
        self._now = 0.0
        self.qf = 0.0                 # the pick counter at the fell
        self.kick = self.breath = self.shp = 0.0
        self.ph = np.zeros(5)
        self.tint = np.array([0.12, 0.10, 0.08])
        self._tint_t = self.tint.copy()

    # -- the timeline ------------------------------------------------------------------------------------------------
    def _timeline(self):
        snd = getattr(self, "sound", None)
        if snd is None:
            if self._own_tl is None:
                self._own_tl = KF.KeyTimeline(self.seed)
            return self._own_tl
        return KF.KeyTimeline.shared(snd, self.seed)

    def _track(self, tl, dt: float) -> float:
        """Follow the piece on the loom; returns how far (bins of cloth) the displayed fell advanced this frame."""
        key = (id(tl), int(tl.pieces), bool(tl.example))
        now = _fin(tl.now())
        if self._key is None:
            self._key, self.base, self.g, self.adv, self._now = key, 0.0, 0.0, now, now
            self._first = True
        elif key != self._key:
            if self._baked:                           # the old cloth keeps rising above the new one
                cp = self.ctx.cp
                cp.copyto(self.pA, self.kf.A)
                cp.copyto(self.pB, self.kf.B)
                cp.copyto(self.pC, self.kf.C)
                self.past = (self.base, float(self.kf.now), float(self.kf.lo), float(self.kf.org),
                             float(self.kf.span))
            # a hand's width of bare warp between two pieces; none after the example (one continuous cloth)
            gap = 0.0 if (self._key[2] and self._baked) else GAP_BINS
            self.base = self.base + self._now + gap
            self.g = max(self.base + now - self.adv, 0.0)
            self._key = key
        elif now < self._now - 0.5 * KF.DROP:         # the timeline dropped its oldest DROP bins (a re-base)
            self.base += KF.DROP
        self._now = now
        if dt > 0.0:
            self.g *= math.exp(-dt / GAP_TAU)
            if self.g < 1e-4:
                self.g = 0.0
        adv = self.base + now - self.g
        step = adv - self.adv
        self.adv = adv
        return step if step == step else 0.0

    def _weave_example(self, logr: float, hue: float) -> None:
        """The cloth already on the loom when it first sees a live (or frozen) piece: keyscape's example, baked once
        from a private timeline into the previous-piece planes and ending exactly at the piece's first row, so the
        loom is fully woven from the start and the hand-off has no seam.  Skipped when the piece already fills the
        view."""
        dmax_bins = VIS * (Y_FELL - Y_TOP + 0.03) / (Y_FELL - Y_TOP)
        if self._now > dmax_bins + 1.0:
            return
        ex = KF.KeyTimeline(self.seed)
        kf = self.kf
        kf.bake(ex, KF.TRAILING, None, logr, hue)
        cp = self.ctx.cp
        cp.copyto(self.pA, kf.A)
        cp.copyto(self.pB, kf.B)
        cp.copyto(self.pC, kf.C)
        self.past = (self.base - float(kf.now), float(kf.now), float(kf.lo), float(kf.org), float(kf.span))

    # -- render ------------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = float(dt) if dt == dt and 0.0 < dt < math.inf else 0.0
        e, pulse = _c01(p["energy"]), _c01(p["pulse"])
        spd = max(_fin(p["speed"], 1.0), 0.0)
        wv, mir, motif = _c01(p["weave"]), _c01(p["mirror"]), _c01(p["motif"])
        logr = _c01(p["log_rows"])
        hue = 180.0 * _c01(p["hue_shift"])
        ts = min(max(_fin(p["thread_size"], 1.0), 0.5), 2.0)
        gap = min(max(_fin(p["piece_gap"], 90.0), 1.0), 3600.0)

        tau = 0.08 if pulse > self.kick else 0.35     # kicks ease in, then fall away
        self.kick += (pulse - self.kick) * (1.0 - math.exp(-dt / tau) if dt > 0 else 0.0)
        k = self.kick
        rates = (0.25 + 0.55 * e + 0.4 * k, 0.17 + 0.30 * e + 0.2 * k, 0.11 + 0.20 * e,
                 0.07 + 0.20 * e + 0.1 * k, 0.13 + 0.25 * e)
        for n, r in enumerate(rates):
            self.ph[n] = (self.ph[n] + r * spd * dt) % (2000.0 * math.pi)
        self.breath = (self.breath + 0.05 * spd * dt) % 1000.0

        tl = self._timeline()
        tl.update(getattr(self, "sound", None), dt, gap)
        step = self._track(tl, dt)
        if self._first:
            self._first = False
            if not tl.example and self.past is None:
                self._weave_example(logr, hue)
        self.kf.bake(tl, KF.TRAILING, None, logr, hue)
        self._baked = True
        live = bool(tl.started and not tl.frozen)
        quiet_a = 1.0 - _smoothstep(0.5, 3.0, _fin(tl.quiet, 1e9)) if live else 0.0
        self.shp = (self.shp + (0.05 + 0.08 * quiet_a + 0.20 * e) * spd * dt) % 2.0

        # geometry (internal pixels)
        w, h, sc = self.w, self.h, self.sc
        cx, half_w = 0.5 * w, 0.5 * (CX1 - CX0) * w
        yf, dpx = Y_FELL * h, (Y_FELL - Y_TOP) * h
        dmax = dpx + 0.03 * h
        tp = max(THREAD_PX * sc * ts, half_w / (NC_MAX / 2 - 4), dmax / (NR_MAX - 8), 1.0)
        nh = half_w / tp
        ppb = dpx / VIS
        self.qf = (self.qf + step * ppb / tp) % Q_WRAP
        j0 = math.floor(self.qf - dmax / tp) - 1
        nr = min(max(math.floor(self.qf) + 2 - j0, 1), NR_MAX)

        # the field's views: the current piece and (while it shows) the previous one
        kf = self.kf
        ft = self.adv - self.base
        cur_ok = 1.0 if (tl.n + (1 if tl.started else 0)) >= 1 and kf.now > kf.lo else 0.0
        pv = 0.0
        pft = pnow = plo = porg = 0.0
        pspan = 1.0
        if self.past is not None:
            pbase, pnow, plo, porg, pspan = self.past
            pft = self.adv - pbase
            if pft - dmax / ppb > pnow + 1.0:         # risen off the top: forget it
                self.past = None
            else:
                pv = 1.0

        # the home key's faint light in the room and on the beam
        sk, sclar = tl.summit()
        if sk is not None and sk >= 0:
            c = KF.key_rgb(int(sk), hue)
            self._tint_t[:] = [min(max(v, 0.0), 1.0) ** 2.2 * (0.35 + 0.65 * _c01(sclar)) for v in c]
        else:
            self._tint_t[:] = (0.12, 0.10, 0.08)
        a_t = 1.0 - math.exp(-dt / 2.0) if dt > 0 else 0.0
        self.tint += (self._tint_t - self.tint) * a_t

        # the shuttle: back and forth, slowing at each side; the pick it lays fades as it is beaten in
        s = self.shp
        if s < 1.0:
            f, entered, travel = 0.5 - 0.5 * math.cos(math.pi * s), cx - half_w, s
        else:
            f, entered, travel = 0.5 + 0.5 * math.cos(math.pi * (s - 1.0)), cx + half_w, s - 1.0
        xl, xr = cx - half_w - 0.04 * w, cx + half_w + 0.04 * w
        shx = xl + (xr - xl) * f

        A = self.A
        A.fill(0.0)
        A[AR["W"]], A[AR["H"]], A[AR["FW"]], A[AR["FH"]] = w, h, FW, FH
        A[AR["CX"]], A[AR["NHALF"]], A[AR["TP"]], A[AR["YFELL"]], A[AR["DPX"]] = cx, nh, tp, yf, dpx
        A[AR["QF"]], A[AR["J0"]], A[AR["NR"]], A[AR["PPB"]] = self.qf, j0, nr, ppb
        A[AR["FT"]], A[AR["NOW"]], A[AR["LO"]] = ft, kf.now, kf.lo
        A[AR["ORG"]], A[AR["SPAN"]], A[AR["CV"]] = kf.org, max(kf.span, 1e-3), cur_ok
        A[AR["PV"]], A[AR["PFT"]], A[AR["PNOW"]], A[AR["PLO"]] = pv, pft, pnow, plo
        A[AR["PORG"]], A[AR["PSPAN"]] = porg, max(pspan, 1e-3)
        A[AR["MIRROR"]], A[AR["WEAVE"]], A[AR["MOTIF"]], A[AR["HUE"]] = mir, wv, motif, hue
        A[AR["GLOW"]] = (0.08 + 0.22 * e + 0.55 * k) * quiet_a
        A[AR["EN"]], A[AR["KICK"]], A[AR["GAIN"]] = e, k, min(max(_fin(p["intensity"], 1.0), 0.0), 2.0)
        A[AR["PH1"]:AR["PH5"] + 1] = self.ph
        A[AR["SWAY"]] = (0.003 + 0.008 * e + 0.004 * k) * h
        A[AR["FOLD"]] = 0.10 + 0.20 * e + 0.08 * k
        satin = _smoothstep(0.5, 1.0, wv)
        A[AR["SHEEN"]] = (0.10 + 0.22 * e + 0.15 * k) * (1.0 + 1.2 * satin)
        A[AR["SATIN"]] = 6.0 + 18.0 * satin
        A[AR["SHX"]], A[AR["SHX0"]] = shx, entered
        A[AR["SHLINE"]] = 1.0 - _smoothstep(0.8, 1.0, travel)
        A[AR["SHGLOW"]] = (0.05 + 0.12 * e + 0.6 * k) * (0.4 + 0.6 * quiet_a)
        A[AR["SHY"]], A[AR["SHHL"]], A[AR["SHHH"]] = SH_Y * h, SH_HL * w, SH_HH * h
        A[AR["BREATH"]] = 1.0 + 0.03 * math.sin(2.0 * math.pi * self.breath)
        A[AR["SC"]] = sc
        A[AR["TR"]], A[AR["TG"]], A[AR["TB"]] = self.tint
        A[AR["BEAT0"]], A[AR["BEAT1"]] = BEAT0 * h, BEAT1 * h
        A[AR["POST0"]], A[AR["POST1"]], A[AR["POSTHW"]] = POST0 * w, POST1 * w, POST_HW * w
        A[AR["BEAMH"]] = Y_TOP * h
        A[AR["CL"]], A[AR["CR"]] = cx - half_w, cx + half_w
        np.nan_to_num(A, copy=False, nan=0.0, posinf=0.0, neginf=0.0)

        self.arena.set(A)
        launch_2d(self.k_cells, NC_MAX, nr, (self.R, self.arena, kf.A, kf.B, kf.C, self.pA, self.pB, self.pC))
        launch_2d(self.k_draw, self.w, self.h, (self.buf, self.R, self.arena, np.uint32(self.seed)))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        if getattr(self, "kf", None) is not None:
            self.kf.close()
        self.kf = None
        self.buf = self.R = self.arena = self.pA = self.pB = self.pC = None


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)
