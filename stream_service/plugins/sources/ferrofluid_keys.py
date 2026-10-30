"""Ferrofluid keys: a pool of glossy black magnetic fluid in a dark studio that bristles into spikes under the music.

What the picture shows
  A still pool of jet-black, mirror-glossy ferrofluid in a dark studio, seen from low down and close, like a
  product shot on black acrylic: the pool fills the lower two thirds of the frame and the spikes stand tall in it,
  their tips rising over the horizon.  Two softboxes and two tall strips stand ahead of the pool where the camera
  cannot see them, so they show only as crisp reflections in the mirror (the spikes' reflections stand in silhouette
  against them); two strips behind the camera run a highlight line down the face of every spike; a small overhead
  box catches the tips.  A coloured glow low behind the pool (its colour follows the music: the key, or where on
  the keyboard the playing is) catches the spikes' edges; the flat pool barely mirrors it, so the colour sits on
  the spikes and in a soft glow over the horizon.  Every key of the piano owns a place on the pool.  When a key
  sounds, the fluid there heaves up into a little crown of sharp black spikes (a central spike with rings of
  shorter ones around it, as real ferrofluid does over a magnet), mirror-bright on their flanks; the spikes and the
  pool reflect each other.  When the note ends the spikes slump back into the pool and the fluid they release runs
  out as rings of ripples.  With nothing playing the pool is still alive: a hidden magnet wanders slowly under it
  dragging a small knot of spikes, the surface breathes with slow swells and stray drops, and the lights drift.

How the music drives it
  * Each sounding key: spike height = the note's sounding level (velocity at the strike, decaying as the piano
    does, held while the damper pedal is down).  The spikes bristle up in ~70 ms on a slightly springy curve
    (a small overshoot, never a flash) and slump back over ~0.3 s.  Loud notes make tall, wide clusters with more
    rings of spikes; soft notes a low tender knot.  Bass clusters are broad and coarse, treble ones small and fine.
  * Where: ``layout`` morphs between an arc round the camera by pitch (A0 left, C8 right; every key the same
    distance away, so each stands as large as the middle one) and a spiral (one turn per octave, pitch classes on
    spokes like a clock, bass outside, treble near the centre), so in the spiral an octave doubling stacks on one
    spoke and a chord stands as a ring.
  * The camera (low view) stands at the arc's centre and frames the keys being played: it pans to them and zooms
    in down to ~3.5 octaves (sinking as it zooms, so the horizon stays a third down the frame), opens at once to
    take in a note outside the frame, closes in slowly, and opens to the whole keyboard after a rest.  The studio
    turns and scales with it.  With the screens in one picture (``sound.segment``, span: room) it holds still.
  * Ripples: the volume a cluster draws up or lets go of is the source of a damped wave equation on the pool, so a
    strike sends a faint dip outward and a release a ring of ripples; dense playing fills the pool with interfering
    rings, rests let it settle to glass.
  * Chords (3+ notes within 80 ms) raise a crown: a slowly turning ring of spikes through or around the chord's
    notes, held as long as its notes sound.
  * Runs (3+ stepwise notes in one direction, each within 0.16 s) send a travelling ridge, a bristling wave crest
    that races along the layout ahead of the notes and leaves a wake.
  * Damper pedal: resonance; ripples ring longer, clusters spread wider and shimmer faintly.  Soft pedal rounds the
    spikes.  The room ensemble's register window dims notes outside it (``sound.register``), and with the screens
    in one picture (``sound.segment``, span: room) the line layout spans the TVs (this screen draws its slice of
    the keyboard).  The default, span: screen, gives each TV the whole pool.

Macros
  energy -> ripple agitation (swells, stray drops, ripple strength), the camera's slow drift, the lights'
  brightness, the crowns' turning and the hidden magnet's wander; pulse (bind note_on) -> a flare of the rim glow
  and the softboxes (eased in, decays); speed -> every drift and the ripple simulation's clock (notes stay locked to the music);
  intensity -> exposure; palette / palette_period / drift_speed -> the rim light's colour (the key is placed around
  the circle of fifths; ``light_hue`` turns it, ``hue_source`` moves it from the key to the register).  Regional:
  layout, spike_sharpness (rounded bumps .. needles), spike_height, ripple, camera (low close view that follows
  the playing .. top-down over the spiral).

GPU: per frame one arena upload (~6 KB), the wave equation on a 400 x 280 grid (1-4 substeps), the spike field on
an 800 x 560 height grid (+ gradient, + coarse (4 cm) and block (20 cm) max grids for empty-space skipping), then
one raymarch pass at render_scale (heightfield march with two-level skipping and bisection, a second bounce for
the fluid's self-reflections, analytic softbox environment, Schlick Fresnel) and the upsample.  All buffers
allocated in setup.  ~4.6 ms at 4K on the P4000 (fxtest --bench).

Source: Original (inspired by Sachiko Kodama's ferrofluid sculptures "Protrude, Flow" (with Minako Takeno, 2001),
        https://transmaterial.net/protrude-flow, and Nigel Stanford's "Cymatics" music video,
        https://nigelstanford.com/cymatics; no code from either: the spike clusters, the ripple solver, the
        heightfield raymarcher and the studio lighting are written from scratch)
Author: smfx (original)
License: MIT (original)
license_class: permissive
"""
from __future__ import annotations

import logging
import math

import numpy as np

from stream_service import ensemble
from stream_service.gpu import raw_module
from stream_service.plugins._lib import tonal as T
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import palette_coeffs
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

log = logging.getLogger("stream_service.plugins.ferrofluid_keys")

# ---- the pool (world units; y up, the camera looks towards -z) ----------------------------------------------------
XW, ZW = 2.0, 1.4                 # the height grid's half extent; beyond it the pool is a flat mirror
GX, GZ = 800, 560                 # fine height grid (5 mm cells)
DXF = 2.0 * XW / GX
CSZ = 8                           # fine cells per coarse (max) cell
NCX, NCZ = GX // CSZ, GZ // CSZ
CSW = CSZ * DXF
BSZ = 5                           # coarse cells per block (a second, coarser max level: 20 cm)
NBX, NBZ = NCX // BSZ, NCZ // BSZ
BSW = BSZ * CSW
GXW, GZW = 400, 280               # wave grid (1 cm cells)
DXW = 2.0 * XW / GXW
C_WAVE = 0.55                     # ripple speed (world / s)
H_MAX_SUB = 0.0085                # wave substep (s): CFL c h / dx = 0.47
MAX_SUB = 4
RCAP = 0.05                       # ripple height clamp
HCAP_ABS = 0.45                   # the field never rises above this (the camera stays above it)
MSAT = 0.07                       # the mounds' saturation height

# ---- layout ---------------------------------------------------------------------------------------------------------
ZA, RA = 1.25, 1.30               # line: an arc round the low camera's foot (0, ZA), so every key stands as close
PHI = math.radians(33.0)          #   and as large as the middle one: A0 at -PHI (left) .. C8 at +PHI (right)
ZS = -0.15                        # spiral: centred at (0, ZS)
R_OUT, R_IN = 0.95, 0.16          #   A0 outside .. C8 near the centre, one turn per octave
NL = 8                            # studio lights

# ---- tables -----------------------------------------------------------------------------------------------------
MAXC = 96                         # spike clusters (88 keys + probes)
NPROBE = 2                        # the wandering magnets (alive with no notes)
MAXK = 4                          # crowns (chords)
MAXR = 2                          # ridges (runs)
MAXS = 128                        # wave sources
MAXDROP = 10                      # stray drops (energy)
MAX_ONSETS = 48                   # onsets read per frame (a burst of 30 is fine)
CHORD_WIN = 0.08                  # s: strikes this close make a chord
RUN_GAP = 0.16                    # s: the longest step of a run
KAPPA = 0.55                      # ripple coupling

# arena layout (floats)
O_L = 64                          # NL lights x 16
O_C = O_L + NL * 16               # clusters MAXC x 8: cx cz H R spacing cos sin mound
O_K = O_C + MAXC * 8              # crowns MAXK x 8: cx cz Rc Hc N rot wr mound
O_R = O_K + MAXK * 8              # ridges MAXR x 8: hx hz tx tz H W comb phase
O_S = O_R + MAXR * 8              # sources MAXS x 4: x z 1/r^2 F
N_AR = O_S + MAXS * 4

_SRC = HELPERS + r"""
#define XW """ + f"{XW:.6f}f" + r"""
#define ZW """ + f"{ZW:.6f}f" + r"""
#define GX """ + str(GX) + r"""
#define GZ """ + str(GZ) + r"""
#define DXF """ + f"{DXF:.8f}f" + r"""
#define IDXF """ + f"{1.0 / DXF:.6f}f" + r"""
#define CSZ """ + str(CSZ) + r"""
#define NCX """ + str(NCX) + r"""
#define NCZ """ + str(NCZ) + r"""
#define CSW """ + f"{CSW:.8f}f" + r"""
#define ICSW """ + f"{1.0 / CSW:.6f}f" + r"""
#define BSZ """ + str(BSZ) + r"""
#define NBX """ + str(NBX) + r"""
#define NBZ """ + str(NBZ) + r"""
#define BSW """ + f"{BSW:.8f}f" + r"""
#define IBSW """ + f"{1.0 / BSW:.6f}f" + r"""
#define GXW """ + str(GXW) + r"""
#define GZW """ + str(GZW) + r"""
#define DXW """ + f"{DXW:.8f}f" + r"""
#define IDXW """ + f"{1.0 / DXW:.6f}f" + r"""
#define RCAP """ + f"{RCAP:.6f}f" + r"""
#define MSAT """ + f"{MSAT:.6f}f" + r"""
#define MAXC """ + str(MAXC) + r"""
#define MAXK """ + str(MAXK) + r"""
#define MAXR """ + str(MAXR) + r"""
#define MAXS """ + str(MAXS) + r"""
#define O_L """ + str(O_L) + r"""
#define NL """ + str(NL) + r"""
#define PN """ + str(O_L + NL * 16) + r"""
#define O_C """ + str(O_C) + r"""
#define O_K """ + str(O_K) + r"""
#define O_R """ + str(O_R) + r"""
#define O_S """ + str(O_S) + r"""
#define TWO_PI 6.2831853f
#define FR0 0.045f

// ---- small vector maths ------------------------------------------------------------------------------------------
__device__ __forceinline__ float3 f3(float x, float y, float z) { return make_float3(x, y, z); }
__device__ __forceinline__ float3 operator+(float3 a, float3 b) { return f3(a.x + b.x, a.y + b.y, a.z + b.z); }
__device__ __forceinline__ float3 operator-(float3 a, float3 b) { return f3(a.x - b.x, a.y - b.y, a.z - b.z); }
__device__ __forceinline__ float3 operator*(float3 a, float s) { return f3(a.x * s, a.y * s, a.z * s); }
__device__ __forceinline__ float dot3(float3 a, float3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
__device__ __forceinline__ float3 nrm(float3 a) { return a * rsqrtf(fmaxf(dot3(a, a), 1e-20f)); }
__device__ __forceinline__ float enc_srgb(float x) {
    x = clamp01(x);
    return x <= 0.0031308f ? 12.92f * x : 1.055f * __powf(x, 1.f / 2.4f) - 0.055f;
}
// a spike's profile, u = distance from its axis / its base radius (0..1): rounded bump .. needle with concave flanks
__device__ __forceinline__ float prof(float u, float sharp) {
    const float v = 1.f - u;
    const float r = 1.f - u * u;
    const float rounded = r * r;
    const float needle = v * v * sqrtf(v);
    return rounded + (needle - rounded) * sharp;
}

// ---- 1. the ripples: a damped wave equation (leapfrog), sources from the clusters, crowns, ridges and drops -------
// Hd: [27] courant^2  [28] damping (1/s)  [29] substep h (s)  [30] noise forcing  [43] edge sponge (1/s)
extern "C" __global__
void ff_wave(float* __restrict__ un, const float* __restrict__ u, const float* __restrict__ up,
             const float* __restrict__ Ar, const float ratio, const float tn)
{
    __shared__ float Hd[64];
    __shared__ float S[MAXS * 4];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x, nth = blockDim.x * blockDim.y;
    const int ns = min(max((int)Ar[26], 0), MAXS);
    for (int i = tid; i < 64; i += nth) Hd[i] = Ar[i];
    for (int i = tid; i < ns * 4; i += nth) S[i] = Ar[O_S + i];
    __syncthreads();
    const int x = blockIdx.x * blockDim.x + threadIdx.x, z = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= GXW || z >= GZW) return;
    const int i = z * GXW + x;
    if (x == 0 || z == 0 || x == GXW - 1 || z == GZW - 1) { un[i] = 0.f; return; }
    const float wx = -XW + (x + 0.5f) * DXW, wz = -ZW + (z + 0.5f) * DXW;
    const float c = u[i], p = up[i];
    const float lap = u[i - 1] + u[i + 1] + u[i - GXW] + u[i + GXW] - 4.f * c;
    const float edge = fminf(XW - fabsf(wx), ZW - fabsf(wz));
    const float sp = 1.f - smooth(0.f, 0.32f, edge);
    const float h = Hd[29];
    const float gam = Hd[28] + Hd[43] * sp * sp;
    float f = 0.f;
    for (int k = 0; k < ns; ++k) {
        const float dx = wx - S[4 * k], dz = wz - S[4 * k + 1];
        const float q = (dx * dx + dz * dz) * S[4 * k + 2];
        if (q < 9.f) f += S[4 * k + 3] * __expf(-q);
    }
    const float na = Hd[30];
    if (na > 0.f) {                                    // slow swells: a drifting smooth pressure field
        const float n1 = vnoise(wx * 3.1f + tn * 0.7f, wz * 3.1f - tn * 0.4f, 11u) - 0.5f;
        const float n2 = vnoise(wx * 5.3f - tn * 0.9f, wz * 5.3f + tn * 0.6f, 29u) - 0.5f;
        f += na * (n1 + 0.6f * n2) * (1.f - sp);
    }
    const float v = (c - p) * ratio * (1.f - fminf(gam * h, 0.5f));
    float nu = c + v + Hd[27] * lap + h * h * f;
    if (!(fabsf(nu) < 1.f)) nu = 0.f;                  // NaN / runaway guard
    un[i] = fminf(fmaxf(nu, -RCAP), RCAP);
}

// ---- 2. the height field: spike clusters (max: sharp valleys between them), mounds (saturating sum), crowns,
//         ridges, + the ripples
// Hd: [17] floor  [19] sharpness  [41] ceiling  [23..25] counts
extern "C" __global__
void ff_field(float* __restrict__ hb, const float* __restrict__ wv, const float* __restrict__ Ar)
{
    __shared__ float Hd[64];
    __shared__ float C[MAXC * 8];
    __shared__ float K[MAXK * 8];
    __shared__ float R[MAXR * 8];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x, nth = blockDim.x * blockDim.y;
    const int nc = min(max((int)Ar[23], 0), MAXC);
    const int nk = min(max((int)Ar[24], 0), MAXK);
    const int nr = min(max((int)Ar[25], 0), MAXR);
    for (int i = tid; i < 64; i += nth) Hd[i] = Ar[i];
    for (int i = tid; i < nc * 8; i += nth) C[i] = Ar[O_C + i];
    for (int i = tid; i < nk * 8; i += nth) K[i] = Ar[O_K + i];
    for (int i = tid; i < nr * 8; i += nth) R[i] = Ar[O_R + i];
    __syncthreads();
    const int x = blockIdx.x * blockDim.x + threadIdx.x, z = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= GX || z >= GZ) return;
    const float wx = -XW + (x + 0.5f) * DXF, wz = -ZW + (z + 0.5f) * DXF;
    const float sharp = Hd[19];
    float sp = 0.f, mound = 0.f;
    // clusters: a hex lattice of spikes centred on the key's place, tallest in the middle
    for (int c = 0; c < nc; ++c) {
        const float* Q = C + 8 * c;
        const float dx = wx - Q[0], dz = wz - Q[1];
        const float d2 = dx * dx + dz * dz, Rr = Q[3];
        const float lim = 1.44f * Rr * Rr;
        if (!(d2 < lim)) continue;
        const float mw = 1.f - d2 / lim;
        mound += Q[7] * mw * mw * mw;
        const float s = Q[4], is = 1.f / s;
        const float lx = (dx * Q[5] + dz * Q[6]) * is, lz = (dz * Q[5] - dx * Q[6]) * is;
        const float bb = lz * 1.1547005f, aa = lx - 0.5f * bb;
        const float fa = floorf(aa), fb = floorf(bb);
        float best = 1e9f, bx = 0.f, bz = 0.f, ba = 0.f, bc = 0.f;
        #pragma unroll
        for (int k = 0; k < 4; ++k) {
            const float ca = fa + (float)(k & 1), cb = fb + (float)(k >> 1);
            const float px = ca + 0.5f * cb, pz = 0.8660254f * cb;
            const float dd = (lx - px) * (lx - px) + (lz - pz) * (lz - pz);
            if (dd < best) { best = dd; bx = px; bz = pz; ba = ca; bc = cb; }
        }
        const float uu = 2.f * sqrtf(best);
        if (uu >= 1.f) continue;
        float en = 1.f - (bx * bx + bz * bz) * s * s / (Rr * Rr);
        if (en <= 0.f) continue;
        en = en * sqrtf(en);
        const float jit = 0.86f + 0.28f * hash1((unsigned int)((int)ba + 4096), (unsigned int)((int)bc + 4096),
                                                 (unsigned int)(Q[5] * 8191.f + 9000.f));
        sp = fmaxf(sp, Q[2] * en * jit * prof(uu, sharp));
    }
    // crowns: a turning ring of spikes on a raised annulus
    for (int k = 0; k < nk; ++k) {
        const float* Q = K + 8 * k;
        if (!(Q[3] > 0.f) && !(Q[7] > 0.f)) continue;
        const float dx = wx - Q[0], dz = wz - Q[1];
        const float rho = sqrtf(dx * dx + dz * dz);
        const float Rc = Q[2], wr = Q[6];
        const float dr = rho - Rc;
        const float band = fmaxf(wr, 0.06f);
        if (fabsf(dr) >= band) continue;
        const float m = 1.f - (dr * dr) / (band * band);
        mound += Q[7] * m * m * m;
        if (fabsf(dr) >= wr || !(Q[3] > 0.f)) continue;
        const int N = max((int)Q[4], 3);
        float fr = (atan2f(dz, dx) - Q[5]) * ((float)N / TWO_PI);
        const float fi = floorf(fr + 0.5f);
        fr -= fi;
        const float arc = fr * TWO_PI * Rc / (float)N;
        const float uu = sqrtf(dr * dr + arc * arc) / wr;
        if (uu >= 1.f) continue;
        const int si = (((int)fi % N) + N) % N;
        const float jit = 0.82f + 0.36f * hash1((unsigned int)si + 101u, (unsigned int)k + 77u, 5u);
        sp = fmaxf(sp, Q[3] * jit * prof(uu, sharp));
    }
    // ridges: a bristling crest (and two smaller ones behind it) travelling along the layout
    for (int k = 0; k < nr; ++k) {
        const float* Q = R + 8 * k;
        if (!(Q[4] > 0.f)) continue;
        const float dx = wx - Q[0], dz = wz - Q[1];
        const float a = dx * Q[2] + dz * Q[3];
        const float b = dz * Q[2] - dx * Q[3];
        const float Wd = Q[5];
        if (fabsf(b) >= Wd || a > 0.09f || a < -0.32f) continue;
        float acr = 1.f - (b * b) / (Wd * Wd);
        acr = acr * acr;
        const float c0 = a * (1.f / 0.026f), c1 = (a + 0.10f) * (1.f / 0.03f), c2 = (a + 0.20f) * (1.f / 0.035f);
        const float crest = __expf(-c0 * c0) + 0.5f * __expf(-c1 * c1) + 0.25f * __expf(-c2 * c2);
        const float hh = Q[4] * acr * crest;
        mound += 0.4f * hh;
        float cb = b / Q[6] + Q[7];
        cb -= floorf(cb + 0.5f);
        const float uu = fminf(2.f * fabsf(cb) / 0.9f, 1.f);
        sp = fmaxf(sp, 0.8f * hh * prof(uu, sharp));
    }
    // the ripples (bilinear from the wave grid)
    float fx = (wx + XW) * IDXW - 0.5f, fz = (wz + ZW) * IDXW - 0.5f;
    fx = fminf(fmaxf(fx, 0.f), (float)(GXW - 1) - 0.001f);
    fz = fminf(fmaxf(fz, 0.f), (float)(GZW - 1) - 0.001f);
    const int ix = (int)fx, iz = (int)fz;
    const float tx = fx - (float)ix, tz = fz - (float)iz;
    const float* w0 = wv + iz * GXW + ix;
    const float rip = (w0[0] + (w0[1] - w0[0]) * tx) * (1.f - tz) + (w0[GXW] + (w0[GXW + 1] - w0[GXW]) * tx) * tz;
    const float mnd = MSAT * (1.f - __expf(-fmaxf(mound, 0.f) * (1.f / MSAT)));
    const float fade = smooth(0.f, 0.2f, XW - fabsf(wx)) * smooth(0.f, 0.2f, ZW - fabsf(wz));
    float h = (sp + mnd) * fade + rip;
    if (!(fabsf(h) < 10.f)) h = 0.f;
    hb[z * GX + x] = fminf(fmaxf(h, Hd[17] + 0.001f), Hd[41]);
}

// ---- 3. the height field's gradient (for smooth interpolated normals) ---------------------------------------------
extern "C" __global__
void ff_grad(float2* __restrict__ g, const float* __restrict__ hb)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, z = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= GX || z >= GZ) return;
    const int xl = max(x - 1, 0), xr = min(x + 1, GX - 1), zl = max(z - 1, 0), zr = min(z + 1, GZ - 1);
    const float gx = (hb[z * GX + xr] - hb[z * GX + xl]) / ((float)(xr - xl) * DXF);
    const float gz = (hb[zr * GX + x] - hb[zl * GX + x]) / ((float)(zr - zl) * DXF);
    g[z * GX + x] = make_float2(gx, gz);
}

// ---- 4. the coarse max grid (empty-space skipping): max over the cell + one fine cell around it ------------------
extern "C" __global__
void ff_cmax(float* __restrict__ cm, const float* __restrict__ hb)
{
    const int cx = blockIdx.x * blockDim.x + threadIdx.x, cz = blockIdx.y * blockDim.y + threadIdx.y;
    if (cx >= NCX || cz >= NCZ) return;
    const int x0 = max(cx * CSZ - 1, 0), x1 = min(cx * CSZ + CSZ, GX - 1);
    const int z0 = max(cz * CSZ - 1, 0), z1 = min(cz * CSZ + CSZ, GZ - 1);
    float m = -1e9f;
    for (int z = z0; z <= z1; ++z)
        for (int x = x0; x <= x1; ++x) m = fmaxf(m, hb[z * GX + x]);
    cm[cz * NCX + cx] = m + 0.0005f;
}
// ---- 4b. the block max grid (BSZ x BSZ coarse cells), stored after the coarse grid in the same buffer
extern "C" __global__
void ff_bmax(float* __restrict__ cm)
{
    const int bx = blockIdx.x * blockDim.x + threadIdx.x, bz = blockIdx.y * blockDim.y + threadIdx.y;
    if (bx >= NBX || bz >= NBZ) return;
    float m = -1e9f;
    for (int z = bz * BSZ; z < bz * BSZ + BSZ; ++z)
        for (int x = bx * BSZ; x < bx * BSZ + BSZ; ++x) m = fmaxf(m, cm[z * NCX + x]);
    cm[NCX * NCZ + bz * NBX + bx] = m;
}

// ---- 5. the raymarch -----------------------------------------------------------------------------------------------
__device__ __forceinline__ float hsample(const float* __restrict__ HB, float x, float z) {
    float fx = (x + XW) * IDXF - 0.5f, fz = (z + ZW) * IDXF - 0.5f;
    fx = fminf(fmaxf(fx, 0.f), (float)(GX - 1) - 0.001f);
    fz = fminf(fmaxf(fz, 0.f), (float)(GZ - 1) - 0.001f);
    const int ix = (int)fx, iz = (int)fz;
    const float tx = fx - (float)ix, tz = fz - (float)iz;
    const float* r0 = HB + iz * GX + ix;
    const float a = __ldg(r0), b = __ldg(r0 + 1), c = __ldg(r0 + GX), d = __ldg(r0 + GX + 1);
    return (a + (b - a) * tx) * (1.f - tz) + (c + (d - c) * tx) * tz;
}
__device__ __forceinline__ float2 gsample(const float2* __restrict__ G, float x, float z) {
    float fx = (x + XW) * IDXF - 0.5f, fz = (z + ZW) * IDXF - 0.5f;
    fx = fminf(fmaxf(fx, 0.f), (float)(GX - 1) - 0.001f);
    fz = fminf(fmaxf(fz, 0.f), (float)(GZ - 1) - 0.001f);
    const int ix = (int)fx, iz = (int)fz;
    const float tx = fx - (float)ix, tz = fz - (float)iz;
    const float2* r0 = G + iz * GX + ix;
    const float2 a = r0[0], b = r0[1], c = r0[GX], d = r0[GX + 1];
    return make_float2((a.x + (b.x - a.x) * tx) * (1.f - tz) + (c.x + (d.x - c.x) * tx) * tz,
                       (a.y + (b.y - a.y) * tx) * (1.f - tz) + (c.y + (d.y - c.y) * tx) * tz);
}
__device__ __forceinline__ float safe_inv(float v) { return 1.f / (fabsf(v) > 1e-8f ? v : copysignf(1e-8f, v)); }

__device__ __forceinline__ bool box_hit(float3 ro, float3 rd, float yb, float yt, float& tn, float& tf) {
    const float ix = safe_inv(rd.x), iy = safe_inv(rd.y), iz = safe_inv(rd.z);
    float t1 = (-XW - ro.x) * ix, t2 = (XW - ro.x) * ix;
    tn = fminf(t1, t2); tf = fmaxf(t1, t2);
    t1 = (yb - ro.y) * iy; t2 = (yt - ro.y) * iy;
    tn = fmaxf(tn, fminf(t1, t2)); tf = fminf(tf, fmaxf(t1, t2));
    t1 = (-ZW - ro.z) * iz; t2 = (ZW - ro.z) * iz;
    tn = fmaxf(tn, fminf(t1, t2)); tf = fminf(tf, fmaxf(t1, t2));
    return tf > fmaxf(tn, 0.f);
}

// march [t0, t1] through the slab: skip coarse cells the ray stays above, fine-step (<= one cell) inside the rest,
// bisect the crossing.  Every loop is bounded (maxit; 6 bisection steps).
__device__ float march(const float* __restrict__ HB, const float* __restrict__ CM, float3 ro, float3 rd,
                       float t0, float t1, int maxit, bool exhaust_hit, bool& hit)
{
    hit = false;
    float t = t0, tprev = t0, tcell = -1.f, tblk = -1.f;
    const float* __restrict__ BM = CM + NCX * NCZ;
    const float rxz = sqrtf(rd.x * rd.x + rd.z * rd.z);
    const float dtcap = 0.9f * DXF / fmaxf(rxz, 1e-4f);
    for (int it = 0; it < maxit; ++it) {
        if (t > t1) return t1;
        const float px = ro.x + rd.x * t, py = ro.y + rd.y * t, pz = ro.z + rd.z * t;
        if (t >= tblk) {                               // a whole block the ray stays above: one step
            const int ix = min(max((int)floorf((px + XW) * IBSW), 0), NBX - 1);
            const int iz = min(max((int)floorf((pz + ZW) * IBSW), 0), NBZ - 1);
            const float bx = (rd.x > 0.f ? (float)(ix + 1) : (float)ix) * BSW - XW;
            const float bz = (rd.z > 0.f ? (float)(iz + 1) : (float)iz) * BSW - ZW;
            const float tx = fabsf(rd.x) > 1e-7f ? (bx - px) / rd.x : 1e30f;
            const float tz = fabsf(rd.z) > 1e-7f ? (bz - pz) / rd.z : 1e30f;
            const float tb = fminf(fmaxf(fminf(tx, tz), 0.f), 1e3f) + 2e-4f;
            const float ylow = rd.y < 0.f ? py + rd.y * tb : py;
            if (ylow > __ldg(BM + iz * NBX + ix)) { t += tb; tprev = t; continue; }
            tblk = t + tb;
        }
        if (t >= tcell) {
            const int ix = min(max((int)floorf((px + XW) * ICSW), 0), NCX - 1);
            const int iz = min(max((int)floorf((pz + ZW) * ICSW), 0), NCZ - 1);
            const float bx = (rd.x > 0.f ? (float)(ix + 1) : (float)ix) * CSW - XW;
            const float bz = (rd.z > 0.f ? (float)(iz + 1) : (float)iz) * CSW - ZW;
            const float tx = fabsf(rd.x) > 1e-7f ? (bx - px) / rd.x : 1e30f;
            const float tz = fabsf(rd.z) > 1e-7f ? (bz - pz) / rd.z : 1e30f;
            const float tc = fminf(fmaxf(fminf(tx, tz), 0.f), 1e3f) + 2e-4f;
            const float ylow = rd.y < 0.f ? py + rd.y * tc : py;
            if (ylow > __ldg(CM + iz * NCX + ix)) { t += tc; tprev = t; continue; }
            tcell = t + tc;
        }
        const float g = py - hsample(HB, px, pz);
        if (g < 0.f) {
            float a = tprev, b = t;
            #pragma unroll
            for (int k = 0; k < 6; ++k) {
                const float m = 0.5f * (a + b);
                if (ro.y + rd.y * m - hsample(HB, ro.x + rd.x * m, ro.z + rd.z * m) < 0.f) b = m; else a = m;
            }
            hit = true;
            return 0.5f * (a + b);
        }
        tprev = t;
        t += fminf(fmaxf(0.4f * g, 4e-4f), dtcap);
    }
    hit = exhaust_hit;
    return fminf(t, t1);
}

struct Hit { float t; int kind; };      // kind 0 miss, 1 the fluid (grid), 2 the flat pool beyond the grid

__device__ Hit trace(const float* P, const float* __restrict__ HB, const float* __restrict__ CM, float3 ro, float3 rd,
                     int maxit, bool exhaust_hit)
{
    Hit h; h.t = 0.f; h.kind = 0;
    const float tpl = rd.y < -1e-6f ? -ro.y / rd.y : -1.f;
    float tn, tf;
    if (box_hit(ro, rd, P[17], P[16], tn, tf)) {
        const float t0 = fmaxf(tn, 0.f);
        if (tpl >= 0.f && tpl < t0) { h.t = tpl; h.kind = 2; return h; }
        bool hit;
        const float t = march(HB, CM, ro, rd, t0, tf, maxit, exhaust_hit, hit);
        if (hit) { h.t = t; h.kind = 1; return h; }
    }
    if (tpl >= 0.f) { h.t = tpl; h.kind = 2; }
    return h;
}

// ---- 6. the studio: a dark cyclorama (a soft glow low behind the pool, in the rim light's colour) and NL gradient
//         softboxes and strips
// P: [32..34] rim colour  [35..37] backdrop tint  [38] haze  [39] far fade  [40] light gain  [42] zoom (tan of
//    half the vertical fov / its widest)  [44, 46] back dir
// hw: how much of the coloured haze this lookup sees (1 seen directly; reflections: as the rim light, see rimw)
__device__ float3 backdrop(const float* P, float3 d, float hw) {
    const float zs = P[42];
    const float ey = fmaxf(d.y, 0.f);
    const float base = 0.0012f + 0.0055f * __expf(-ey * (1.f / 0.22f));     // darkest overhead, a dim glow low down
    float3 c = f3(P[35], P[36], P[37]) * base;
    const float back = smooth(-0.2f, 0.9f, d.x * P[44] + d.z * P[46]);
    const float hz = (d.y - 0.03f * zs) / (0.07f * zs);
    const float hn = (d.y - 0.02f * zs) / (0.022f * zs);
    const float hb = d.y < 0.f ? d.y / (0.012f * zs) : 0.f;                // the glow stops at the horizon
    return c + f3(P[32], P[33], P[34]) * (P[38] * hw * back * (__expf(-hz * hz) + 0.8f * __expf(-hn * hn))
                                          * __expf(-hb * hb));
}
// rimw: how much of the rim light (0) this surface sees.  The steep flanks of the spikes take all of it (their edges
// catch the colour); the flat pool only a trace, so the colour sits on the spikes, not as a band across the pool.
__device__ __forceinline__ float rimw(float3 n) { return 0.012f + 0.988f * smooth(0.04f, 0.35f, 1.f - n.y); }
__device__ float3 env(const float* P, float3 d, float rough, float rw) {
    float3 c = backdrop(P, d, rw);
    #pragma unroll
    for (int i = 0; i < NL; ++i) {
        const float* L = P + O_L + 16 * i;
        const float k = d.x * L[0] + d.y * L[1] + d.z * L[2];
        if (k < 0.08f) continue;
        const float ik = 1.f / k;
        const float qx = (d.x * L[3] + d.y * L[4] + d.z * L[5]) * ik;
        const float qy = (d.x * L[6] + d.y * L[7] + d.z * L[8]) * ik;
        const float hh = fabsf(L[10]);                  // < 0: a pool box, brightest at its foot (by the spikes)
        const float ex = fabsf(qx) - L[9], ey = fabsf(qy) - hh;
        const float ox = fmaxf(ex, 0.f), oy = fmaxf(ey, 0.f);
        const float sd = sqrtf(ox * ox + oy * oy) + fminf(fmaxf(ex, ey), 0.f);
        const float w = L[11] + rough;
        const float m = 1.f - smooth(-w, w, sd);
        if (m <= 0.f) continue;
        const float gy = clamp01(0.5f + 0.5f * qy / L[10]);
        const float gx = clamp01(fabsf(qx) / L[9]);
        const float gv = L[10] < 0.f ? 0.10f + 0.90f * gy * gy : 0.5f + 0.5f * gy;
        const float I = L[12] * m * gv * (1.f - (i == 0 ? 0.92f : 0.55f) * gx * gx)  // 0: the rim glow fades out
                        * (i == 0 ? rw : 1.f);                                     //    to its ends
        c = c + f3(L[13], L[14], L[15]) * I;
    }
    return c;
}
__device__ __forceinline__ float fres(float c) {
    const float m = clamp01(1.f - c);
    const float m2 = m * m;
    return FR0 + (1.f - FR0) * m2 * m2 * m;
}
__device__ __forceinline__ float3 fluid_base(const float* P, float3 n) {
    const float l = (0.35f + 1.0f * fmaxf(n.y, 0.f)) * P[40];
    return f3(0.0026f, 0.0023f, 0.0021f) * l;
}
__device__ __forceinline__ float farfade(const float* P, float3 p) {
    return 1.f - smooth(P[39], P[39] + 2.6f, sqrtf(p.x * p.x + p.z * p.z));
}
__device__ __forceinline__ float3 face(float3 n, float3 rd) {
    const float dn = dot3(rd, n);              // a normal facing away (interpolation at a silhouette): graze instead
    return dn > -0.02f ? nrm(n - rd * (dn + 0.02f)) : n;
}
// the fluid seen in a reflection: its dark base and its own mirror of the studio (no further bounce)
__device__ float3 shade_once(const float* P, float3 rd, float3 n, float gm, float dist) {
    n = face(n, rd);
    const float dn = dot3(rd, n);
    float3 r = rd - n * (2.f * dn);
    if (r.y < 0.f) r = nrm(f3(r.x, -0.35f * r.y, r.z));
    const float rough = 0.012f + 0.005f * fminf(gm, 12.f) + P[47] * dist;
    return fluid_base(P, n) + env(P, r, rough, rimw(n)) * fres(clamp01(-dn));
}

// P: [0] W [1] H [2..4] eye [5..7] forward [8..10] right [11..13] up [14] tan(fov/2) [15] aspect [16] slab top
//    [17] slab floor [18] gain [47] pixel footprint (rad)
extern "C" __global__
void ff_render(uchar4* __restrict__ out, const float* __restrict__ HB, const float2* __restrict__ G,
               const float* __restrict__ CM, const float* __restrict__ Ar, const unsigned int seed)
{
    __shared__ float P[PN];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x, nth = blockDim.x * blockDim.y;
    for (int i = tid; i < PN; i += nth) P[i] = Ar[i];
    __syncthreads();
    const int W = (int)P[0], Hh = (int)P[1];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= Hh) return;
    const float th = P[14], asp = P[15];
    const float vx = (x + 0.5f) / (float)W * 2.f - 1.f, vy = 1.f - (y + 0.5f) / (float)Hh * 2.f;
    const float3 ro = f3(P[2], P[3], P[4]);
    const float3 rd = nrm(f3(P[5], P[6], P[7]) + f3(P[8], P[9], P[10]) * (vx * asp * th)
                          + f3(P[11], P[12], P[13]) * (vy * th));
    float3 col;
    const Hit h = trace(P, HB, CM, ro, rd, 256, true);
    if (h.kind == 0) {
        col = backdrop(P, rd, 1.f);
    } else {
        const float3 p = ro + rd * h.t;
        float3 n = f3(0.f, 1.f, 0.f);
        float gm = 0.f;
        if (h.kind == 1) {
            const float2 g = gsample(G, p.x, p.z);
            gm = sqrtf(g.x * g.x + g.y * g.y);
            n = face(nrm(f3(-g.x, 1.f, -g.y)), rd);
        }
        const float dn = dot3(rd, n);
        float3 r = rd - n * (2.f * dn);
        if (r.y < 0.f) r = nrm(f3(r.x, -0.35f * r.y, r.z));
        const float rough = 0.012f + 0.005f * fminf(gm, 12.f) + P[47] * h.t;
        const float3 o2 = p + n * 0.004f;
        float3 refl;
        const Hit h2 = trace(P, HB, CM, o2, r, 64, false);     // the spikes and the pool in each other
        if (h2.kind == 1) {
            const float3 p2 = o2 + r * h2.t;
            const float2 g2 = gsample(G, p2.x, p2.z);
            refl = shade_once(P, r, nrm(f3(-g2.x, 1.f, -g2.y)), sqrtf(g2.x * g2.x + g2.y * g2.y), h.t + h2.t);
        } else if (h2.kind == 2) {
            const float3 p2 = o2 + r * h2.t;
            refl = shade_once(P, r, f3(0.f, 1.f, 0.f), 0.f, h.t + h2.t) * farfade(P, p2);
        } else {
            refl = env(P, r, rough, rimw(n));
        }
        col = fluid_base(P, n) + refl * fres(clamp01(-dn));
        if (h.kind == 2) {
            const float ff = farfade(P, p);
            col = col * ff + backdrop(P, rd, 1.f) * (1.f - ff);
        }
    }
    const float vig = 1.f - 0.18f * (0.55f * vx * vx + 0.8f * vy * vy);
    const float gn = P[18] * vig;
    float c3[3] = {col.x * gn, col.y * gn, col.z * gn};
    const float dth = (hash_u((unsigned int)(y * W + x) ^ (seed * 0x9E3779B1u)) >> 8) * (1.f / 16777216.f) - 0.5f;
    unsigned char o[3];
    #pragma unroll
    for (int k = 0; k < 3; ++k) {
        float c = fmaxf(c3[k], 0.f);
        if (!(c < 1e4f)) c = 0.f;
        c = c * (1.f + c * (1.f / 25.f)) / (1.f + c);          // soft shoulder (white at 5)
        o[k] = (unsigned char)fminf(fmaxf(255.f * enc_srgb(c) + dth, 0.f), 255.f);
    }
    out[y * W + x] = make_uchar4(o[0], o[1], o[2], 255);
}
"""

_OPTS = ("--std=c++14", "--use_fast_math")


# ------------------------------------------------------------------------------------------------- host helpers
def _alpha(dt: float, tau: float) -> float:
    return 1.0 - math.exp(-dt / tau) if dt > 0.0 and tau > 0.0 else 0.0


def _clamp01(v) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return min(max(v, 0.0), 1.0) if v == v else 0.0


def _smooth01(v: float) -> float:
    v = min(max(v, 0.0), 1.0)
    return v * v * (3.0 - 2.0 * v)


def _norm(v) -> np.ndarray:
    v = np.asarray(v, np.float64)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


def _rot_y(v: np.ndarray, a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([v[0] * c + v[2] * s, v[1], -v[0] * s + v[2] * c])


def _light_basis(c, u_hint) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A softbox's centre direction and two tangents (U across, V up the box: its gradient brightens towards +V)."""
    c = _norm(c)
    u = np.asarray(u_hint, np.float64)
    u = _norm(u - np.dot(u, c) * c)
    v = np.cross(u, c)
    if v[1] < 0.0:
        v = -v
    return c, u, v


def _azel(az: float, el: float) -> tuple[tuple, tuple]:
    """A direction at azimuth ``az`` (deg, 0 = straight ahead of the camera, + right, 180 behind it) and elevation
    ``el`` (deg), and its horizontal tangent (the U hint)."""
    a, e = math.radians(az), math.radians(el)
    return (math.sin(a) * math.cos(e), math.sin(e), -math.cos(a) * math.cos(e)), (math.cos(a), 0.0, math.sin(a))


# The studio, relative to the camera (it pans and zooms with it, as a photographer's lights would).  The low camera
# sees the pool reflect what lies ahead of it between the frame top's elevation and pitch + half the field of view,
# and sees nothing above the frame top directly: the boxes ahead are placed in that band ("band", from .. to as
# fractions of it), sideways as fractions of the frame's half width, so they show only in the pool and on the
# spikes; the rim strip sits just over the horizon; two strips behind the camera run a highlight line down the face
# of every spike; a big softbox overhead catches the tips and the mounds.
# (x: frame fraction | azimuth deg, elevation: ("band", lo, hi) | (deg, half height tan), half width (frame fraction
#  for the band boxes, else tan), edge softness, intensity, colour)
_LIGHTS = (
    (0.0, ("rim",), 0.95, 0.0, 1.7, "rim"),                                # 0 the rim glow, low behind (colour)
    (-0.21, ("band", 0.05, 0.78), 0.15, 0.006, 1.15, (1.0, 0.97, 0.93)),   # 1 two softboxes ahead, a gap between
    (0.25, ("band", 0.03, 0.70), 0.17, 0.006, 1.05, (1.0, 0.95, 0.90)),   #   them: panels in the pool
    (-0.68, ("band", 0.0, 0.98), 0.032, 0.004, 1.8, "side"),               # 3 left strip ahead (cool)
    (0.72, ("band", 0.06, 0.94), 0.028, 0.004, 1.5, "side"),               # 4 right strip ahead (cool)
    (163.0, (26.0, 0.45), 0.05, 0.02, 10.0, (1.0, 0.86, 0.70)),            # 5 warm strip behind the camera
    (202.0, (32.0, 0.40), 0.04, 0.02, 7.0, (0.82, 0.90, 1.0)),             # 6 cool strip behind the camera
    (-25.0, (70.0, 0.14), 0.22, 0.03, 2.0, (1.0, 0.97, 0.92)),             # 7 overhead softbox
)
_FIXED = tuple(None if isinstance(ev[0], str) else _light_basis(*_azel(x, ev[0])) for x, ev, *_ in _LIGHTS)
HV_WIDE, HV_MIN = math.radians(23.0), math.radians(10.5)   # half the vertical field of view: whole keyboard .. ~3.5 oct
FOLLOW_MIN = 40.0                                          # the narrowest key window the camera frames


def _pal_rgb(rows: np.ndarray, t: float) -> np.ndarray:
    a, b, c, d = rows[0:3], rows[3:6], rows[6:9], rows[9:12]
    col = np.clip(a + b * np.cos(2.0 * math.pi * (c * t + d)), 0.0, 1.0)
    return col / max(float(col.max()), 0.25)


SPANS = ("screen", "room")    # screen: each TV the whole picture (default; owner, 30 Oct: the TVs aren't adjacent);
                              # room: one picture across the room's screens (sound.segment)


class FerrofluidKeys(Source):
    """Per frame: host numpy over the 128 keys (springs, layout, chords, runs), one arena upload, 1-4 wave substeps,
    field + gradient + coarse max, one raymarch at render_scale, the upsample."""
    name = "ferrofluid_keys"
    label = "Ferrofluid keys"
    description = ("A pool of glossy black ferrofluid seen low and close in a dark studio: every key raises a "
                   "cluster of tall mirror-black spikes at its place, chords stand as rings, runs send a ridge "
                   "racing across, and released notes slump back in ripples.")
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette="rainbow", render_scale=0.5, period=1.0, drift=0.01),
            "layout": num(0.0, 1.0, 0.2, "Layout (line .. spiral)", basic=True),
            "spike_sharpness": num(0.0, 1.0, 0.7, "Spike sharpness"),
            "spike_height": num(0.4, 1.6, 1.0, "Spike height"),
            "ripple": num(0.0, 1.0, 0.6, "Ripples"),
            "camera": num(0.0, 1.0, 0.05, "Camera (low, follows the playing .. overhead)", basic=True),
            "light_hue": num(0.0, 1.0, 0.0, "Rim light hue"),
            "hue_source": num(0.0, 1.0, 0.0, "Rim hue: key .. register"),
            "span": enum(SPANS, "screen", "Span (this screen, or one picture across the room)"),
        }

    # -- setup --------------------------------------------------------------------------------------------------
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.span_room = params.get("span") == "room"
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        mod = raw_module(_SRC, _OPTS)
        self.k_wave = mod.get_function("ff_wave")
        self.k_field = mod.get_function("ff_field")
        self.k_grad = mod.get_function("ff_grad")
        self.k_cmax = mod.get_function("ff_cmax")
        self.k_bmax = mod.get_function("ff_bmax")
        self.k_render = mod.get_function("ff_render")
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.hb = cp.zeros((GZ, GX), cp.float32)
        self.gb = cp.zeros((GZ, GX, 2), cp.float32)
        self.cm = cp.zeros(NCZ * NCX + NBZ * NBX, cp.float32)       # coarse max grid, then the block max grid
        self.wv = [cp.zeros((GZW, GXW), cp.float32) for _ in range(3)]
        self.wi = [0, 1, 2]                               # prev, cur, next
        self.arena = cp.zeros(N_AR, cp.float32)
        self.A = np.zeros(N_AR, np.float32)
        rng = np.random.default_rng(self.seed)
        self.rng = rng

        # per key (static): place on the keyboard, cluster size, spike spacing and height, lattice turn, shimmer
        keys = np.arange(128, dtype=np.float64)
        self._u = np.clip((keys - 21.0) / 87.0, 0.0, 1.0)
        u = self._u
        self._R0 = 0.115 + (0.062 - 0.115) * u
        self._sp = 0.050 + (0.031 - 0.050) * u
        self._hmax = 0.34 + (0.22 - 0.34) * u
        th = rng.uniform(0.0, math.pi / 3.0, 128)
        self._cs, self._sn = np.cos(th), np.sin(th)
        self._shf = rng.uniform(1.2, 2.4, 128)
        self._shp = rng.uniform(0.0, 2.0 * math.pi, 128)
        ang = 0.5 * math.pi - 2.0 * math.pi * (keys - 24.0) / 12.0          # C up the pool, ascending clockwise
        rad = R_OUT - (R_OUT - R_IN) * u
        self._spx, self._spz = rad * np.cos(ang), ZS - rad * np.sin(ang)
        # per key (dynamic)
        self.amp = np.zeros(128)
        self.vel = np.zeros(128)
        self.Rv = self._R0.copy()
        self._silent = np.zeros((128, 4), np.float32)
        self._silent[:, 2:] = np.inf
        self._lv = np.zeros(128)
        self._tgt = np.zeros(128)
        self._vis = np.zeros(128)
        self._pos = np.zeros((128, 2))
        self._wl = np.ones(128)
        self._pce = np.zeros(12, np.float32)
        self._reg_key, self._reg_w = None, None
        self._seg = None
        # motion and colour
        self.clock = 0.0
        self.e_s = 0.5
        self.flare = 0.0
        self.damper = self.soft = 0.0
        self.cam_ph = float(rng.uniform(0.0, 1.0))
        self.env_ph = float(rng.uniform(0.0, 1.0))
        self.probe_t = float(rng.uniform(0.0, 200.0))
        self.noise_t = 0.0
        self.drift = 0.0
        self.quiet = 1e9
        self.idle = 0.24
        self._probe_ph = rng.uniform(0.0, 1.0, (NPROBE, 3))
        self._probe_amp = np.zeros(NPROBE)
        sp = self.session_palette()
        self.pal = np.array(palette_coeffs(params["palette"]) if sp is None else np.asarray(sp).ravel(), np.float64)
        self.keyr = T.KeyResolver()
        self.hue_k = 0.0                                  # the key's place on the fifths (unwrapped, eased)
        self.hue_r = 0.3                                  # the register's centroid (eased)
        # chords -> crowns
        self.pend_t0, self.pend = -1e9, []
        self.cr_on = np.zeros(MAXK, bool)
        self.cr_p = np.zeros((MAXK, 12), np.int64)
        self.cr_n = np.zeros(MAXK, np.int64)
        self.cr_age = np.zeros(MAXK)
        self.cr_amp = np.zeros(MAXK)
        self.cr_ampv = np.zeros(MAXK)
        self.cr_rot = np.zeros(MAXK)
        self.cr_dir = np.ones(MAXK)
        self.cr_N = np.full(MAXK, 12.0)
        # runs -> ridges
        self.rd_last_p = np.full(MAXR, -100.0)
        self.rd_last_t = np.full(MAXR, -1e9)
        self.rd_dir = np.zeros(MAXR)
        self.rd_cnt = np.zeros(MAXR, np.int64)
        self.rd_pos = np.full(MAXR, 60.0)
        self.rd_vel = np.zeros(MAXR)
        self.rd_amp = np.zeros(MAXR)
        self.rd_ampt = np.zeros(MAXR)
        self.rd_ph = rng.uniform(0.0, 1.0, MAXR)
        # stray drops: x z age dur strength
        self.drops = np.zeros((MAXDROP, 5))
        self.drops[:, 2] = 1e9
        # the camera's key window (follows the playing): centre and width in keys, eased
        self.last_on = np.full(128, -1e9)
        self.fc_c, self.fc_w = 64.5, 95.0
        self.drop_t = 0.5
        self.h_prev = H_MAX_SUB

    # -- helpers ----------------------------------------------------------------------------------------------------
    def _reg_weights(self, snd):
        reg = getattr(snd, "register", None) if snd is not None else None
        if not reg:
            return None
        if self._reg_key != reg:
            try:
                w = ensemble.note_weights(reg, np.arange(128))
            except Exception:                              # a malformed window: draw everything at full
                w = None
            self._reg_key = reg
            self._reg_w = None if w is None else np.nan_to_num(np.asarray(w, np.float64), nan=1.0)
        return self._reg_w

    def _layout_xy(self, pf: float, s: float) -> tuple[float, float]:
        """World (x, z) of a continuous pitch under the current layout morph ``s`` (0 line .. 1 spiral)."""
        u = min(max((pf - 21.0) / 87.0, 0.0), 1.0)
        ul = u
        if self._seg is not None:
            k, n = self._seg
            ul = min(max(u * n - k, -0.15), 1.15)
        phi = PHI * (2.0 * ul - 1.0)
        xl, zl = RA * math.sin(phi), ZA - RA * math.cos(phi)
        ang = 0.5 * math.pi - 2.0 * math.pi * (pf - 24.0) / 12.0
        r = R_OUT - (R_OUT - R_IN) * u
        xs, zs = r * math.cos(ang), ZS - r * math.sin(ang)
        return xl + (xs - xl) * s, zl + (zs - zl) * s

    def _layout(self, s: float) -> None:
        u = self._u
        if self._seg is not None:
            k, n = self._seg
            ul = u * n - k
            inside = ((ul >= -0.02) & (ul <= 1.02)).astype(np.float64)
            ul = np.clip(ul, -0.15, 1.15)
        else:
            ul, inside = u, None
        phi = PHI * (2.0 * ul - 1.0)
        xl = RA * np.sin(phi)
        zl = ZA - RA * np.cos(phi)
        self._pos[:, 0] = xl + (self._spx - xl) * s
        self._pos[:, 1] = zl + (self._spz - zl) * s
        if inside is None:
            self._wl.fill(1.0)
        else:                                             # off this screen's slice of the line: gone in line layout
            np.multiply(1.0 - inside, 1.0 - s, out=self._wl)
            np.subtract(1.0, self._wl, out=self._wl)

    def _springs(self, dt: float) -> None:
        """Each key's spike amount follows its target: up fast and a little springy, down as a slump."""
        if dt <= 0.0:
            return
        amp, vel, tgt = self.amp, self.vel, self._tgt
        up = tgt > amp
        om = np.where(up, 26.0, 7.5)
        ze = np.where(up, 0.55, 1.0)
        n = min(8, max(1, int(math.ceil(dt / 0.01))))
        h = dt / n
        k1, k2 = om * om, 2.0 * ze * om
        for _ in range(n):
            vel += (k1 * (tgt - amp) - k2 * vel) * h
            amp += vel * h
        neg = amp < 0.0
        amp[neg] = 0.0
        vel[neg & (vel < 0.0)] = 0.0
        np.clip(amp, 0.0, 1.6, out=amp)
        np.clip(vel, -60.0, 60.0, out=vel)

    def _onsets(self, f, dt: float, s_lay: float, e: float) -> None:
        """Chords (crowns) and runs (ridges) from this frame's strikes, in time order."""
        ons = getattr(f, "onsets", None) if f is not None else None
        evs = []
        if ons:
            for o in list(ons)[:MAX_ONSETS]:
                try:
                    pch, vv, age = float(o[0]), float(o[1]), float(o[2])
                except (TypeError, ValueError, IndexError):
                    continue
                if not (pch == pch and vv == vv and 0.0 <= pch < 128.0):
                    continue
                age = min(max(age if age == age else 0.0, 0.0), 2.0)
                evs.append((self.clock - age, pch, min(max(vv, 0.0), 1.0)))
            evs.sort()
        for t_on, pch, vv in evs:
            ki = int(pch)
            self.last_on[ki] = max(self.last_on[ki], t_on)
            # chords: strikes within CHORD_WIN of the first
            if self.pend and t_on - self.pend_t0 <= CHORD_WIN:
                if len(self.pend) < 12:
                    self.pend.append(pch)
            else:
                self._close_chord(s_lay)
                self.pend, self.pend_t0 = [pch], t_on
            # runs: a step of <= 5 semitones within RUN_GAP of a tracker's last note
            best, bd = -1, 99.0
            for j in range(MAXR):
                gap = t_on - self.rd_last_t[j]
                d = abs(pch - self.rd_last_p[j])
                if 0.008 < gap <= RUN_GAP and 0.0 < d <= 5.0 and d < bd:
                    best, bd = j, d
            if best >= 0:
                j = best
                d = math.copysign(1.0, pch - self.rd_last_p[j])
                gap = max(t_on - self.rd_last_t[j], 0.02)
                rate = min(max((pch - self.rd_last_p[j]) / gap, -60.0), 60.0)
                if d == self.rd_dir[j]:
                    self.rd_cnt[j] += 1
                    self.rd_vel[j] = 0.5 * self.rd_vel[j] + 0.5 * rate
                else:
                    self.rd_dir[j], self.rd_cnt[j], self.rd_vel[j] = d, 2, rate
                self.rd_last_p[j], self.rd_last_t[j] = pch, t_on
                if self.rd_cnt[j] >= 3:
                    if self.rd_amp[j] < 0.02:
                        self.rd_pos[j] = pch
                    self.rd_ampt[j] = min(1.0, self.rd_ampt[j] + 0.3 * (0.4 + 0.6 * vv))
            else:
                # no run continues: restart the tracker idle longest (one mid-run only if both are); a chord tone
                # struck at the same instant as that tracker's last note leaves it alone
                score = [t_on - self.rd_last_t[i] - (1.0 if self.rd_ampt[i] >= 0.2 else 0.0) for i in range(MAXR)]
                j = int(np.argmax(score))
                if t_on - self.rd_last_t[j] > 0.008:
                    self.rd_last_p[j], self.rd_last_t[j], self.rd_cnt[j], self.rd_dir[j] = pch, t_on, 1, 0.0
        if self.pend and self.clock - self.pend_t0 > CHORD_WIN:
            self._close_chord(s_lay)

    def _close_chord(self, s_lay: float) -> None:
        notes = sorted(set(int(round(p)) for p in self.pend))
        self.pend = []
        if len(notes) < 3:
            return
        free = np.flatnonzero(~self.cr_on)
        j = int(free[0]) if free.size else int(np.argmin(self.cr_amp))
        k = min(len(notes), 12)
        self.cr_on[j] = True
        self.cr_p[j, :k] = notes[:k]
        self.cr_n[j] = k
        self.cr_age[j] = 0.0
        self.cr_amp[j] = 0.0
        self.cr_ampv[j] = 0.0
        self.cr_rot[j] = float(self.rng.uniform(0.0, 2.0 * math.pi))
        self.cr_dir[j] = 1.0 if self.rng.uniform() < 0.5 else -1.0
        pts = self._pos[self.cr_p[j, :k]]
        rc = self._crown_radius(pts)
        self.cr_N[j] = float(min(max(round(2.0 * math.pi * rc / 0.055), 8), 40))

    @staticmethod
    def _crown_radius(pts: np.ndarray) -> float:
        c = pts.mean(axis=0)
        md = float(np.sqrt(((pts - c) ** 2).sum(axis=1)).mean())
        return min(max(md * 1.1 + 0.07, 0.12), 0.95)

    # -- render ---------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        try:
            dt = float(dt)
        except (TypeError, ValueError):
            dt = 0.0
        dt = min(dt, 0.25) if dt == dt and dt > 0.0 else 0.0
        e = _clamp01(p["energy"])
        pulse = _clamp01(p["pulse"])
        spd = max(float(p["speed"]), 0.0) if p["speed"] == p["speed"] else 1.0
        lay = _smooth01(_clamp01(p["layout"]))
        sharp0 = _clamp01(p["spike_sharpness"])
        sh = min(max(float(p["spike_height"]), 0.2), 2.0)
        ripple = _clamp01(p["ripple"])
        camv = _smooth01(_clamp01(p["camera"]))
        self.clock += dt
        sdt = dt * spd
        self.e_s += (e - self.e_s) * _alpha(dt, 2.5)
        self.flare += (pulse - self.flare) * _alpha(dt, 0.06 if pulse > self.flare else 0.35)
        self.cam_ph = (self.cam_ph + (0.010 + 0.016 * e) * sdt) % 1000.0
        self.env_ph = (self.env_ph + (0.006 + 0.010 * e) * sdt) % 1000.0
        self.probe_t = (self.probe_t + (0.6 + 0.6 * e) * sdt) % 10000.0
        self.noise_t = (self.noise_t + (0.5 + 0.8 * e) * sdt) % 1000.0
        self.drift = (self.drift + float(p["drift_speed"]) * sdt) % 1000.0

        # ---- the piano
        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        notes = getattr(f, "notes", None) if f is not None else None
        if not isinstance(notes, np.ndarray) or notes.shape != (128, 4):
            notes = self._silent
        ped = getattr(f, "pedals", None) if f is not None else None
        dmp = _clamp01(ped[0]) if ped is not None and len(ped) > 1 else 0.0
        sft = _clamp01(ped[1]) if ped is not None and len(ped) > 1 else 0.0
        self.damper += (dmp - self.damper) * _alpha(dt, 0.15)
        self.soft += (sft - self.soft) * _alpha(dt, 0.15)
        self._seg = ensemble.clean_segment(getattr(snd, "segment", None)) \
            if snd is not None and self.span_room else None

        lv = self._lv
        lv[:] = np.nan_to_num(notes[:, 0], nan=0.0, posinf=1.0, neginf=0.0)
        np.clip(lv, 0.0, 1.0, out=lv)
        wr = self._reg_weights(snd)
        if wr is not None:
            lv *= wr
        np.power(lv, 0.65, out=self._tgt)
        self._springs(dt)
        velst = np.clip(np.nan_to_num(notes[:, 1], nan=0.0), 0.0, 1.0)
        self.Rv += (self._R0 * (0.75 + 0.45 * velst) - self.Rv) * _alpha(dt, 0.08)
        self._layout(lay)
        self._onsets(f, dt, lay, e)

        loud = float(self.amp.max())
        self.quiet = 0.0 if loud > 0.03 else self.quiet + dt
        self.idle += ((0.24 if self.quiet > 2.5 else 0.05) - self.idle) * _alpha(dt, 2.0)

        # the key (room's, else our own tracker) and the register centroid, for the rim light
        T.pc_energy(notes, self._pce)
        try:
            self.keyr.update(self._pce, dt, getattr(f, "key", None), getattr(f, "mode", None))
        except Exception:                                  # degrade: keep the last key
            pass
        tk = ((7 * self.keyr.tonic) % 12) / 12.0
        tk += round(self.hue_k - tk)                       # the nearest turn: ease the short way round
        self.hue_k += (tk - self.hue_k) * _alpha(dt, 1.5)
        tot = float(lv.sum())
        if tot > 0.02:
            cen = float((lv * np.arange(128)).sum()) / tot
            self.hue_r += (min(max((cen - 21.0) / 87.0, 0.0), 1.0) * 0.75 - self.hue_r) * _alpha(dt, 1.2)
        hs = _clamp01(p["hue_source"])
        hue = self.hue_k + (self.hue_r - self.hue_k) * hs + _clamp01(p["light_hue"])

        sp = self.session_palette()
        tp = np.asarray(palette_coeffs(p["palette"]) if sp is None else np.asarray(sp).ravel(), np.float64)
        if tp.shape == self.pal.shape:
            self.pal += (tp - self.pal) * (_alpha(dt, 0.8) if dt > 0 else 0.0)
        rim = (0.82 * _pal_rgb(self.pal, hue * float(p["palette_period"]) + self.drift) + 0.18) ** 2.2
        comp = _pal_rgb(self.pal, (hue + 0.5) * float(p["palette_period"]) + self.drift) ** 2.2

        A = self.A
        A.fill(0.0)
        # ---- clusters
        vis = self._vis
        np.multiply(self.amp, 1.0 + 0.05 * self.damper * np.sin(2.0 * math.pi * self._shf * self.clock + self._shp),
                    out=vis)
        vis *= self._wl
        act = (vis > 0.003) | (np.abs(self.vel) > 0.02)
        idx = np.flatnonzero(act)
        if idx.size > MAXC - NPROBE:
            idx = idx[np.argsort(-vis[idx])[:MAXC - NPROBE]]
        k = int(idx.size)
        C = A[O_C:O_C + MAXC * 8].reshape(MAXC, 8)
        hmax = self._hmax[idx] * sh
        H = vis[idx] * hmax
        R = self.Rv[idx] * (1.0 + 0.2 * self.damper)
        if k:
            C[:k, 0] = self._pos[idx, 0]
            C[:k, 1] = self._pos[idx, 1]
            C[:k, 2] = H
            C[:k, 3] = R
            C[:k, 4] = self._sp[idx]
            C[:k, 5] = self._cs[idx]
            C[:k, 6] = self._sn[idx]
            C[:k, 7] = 0.30 * H
        spike_top = float(H.max()) * 1.15 if k else 0.0
        mound_sum = float(C[:k, 7].sum()) if k else 0.0
        # the wandering magnets
        tpb = self.probe_t
        for j in range(NPROBE):
            ph = self._probe_ph[j]
            a_t = self.idle * (0.7 + 0.3 * math.sin(2.0 * math.pi * (0.05 * tpb + ph[2])))
            self._probe_amp[j] = a_t
            w1 = math.sin(2.0 * math.pi * (0.013 * tpb + ph[0])) * (1.0 - 0.25 * j)
            w2 = math.sin(2.0 * math.pi * (0.0097 * tpb + ph[1])) * (1.0 - 0.2 * j)
            pa, pr = 0.85 * PHI * w1, RA * (1.25 + 0.35 * w2)          # line: along the arc and behind it
            ax, az = pr * math.sin(pa), ZA - pr * math.cos(pa)
            sx, sz = 0.75 * w1, ZS + 0.6 * w2                            # spiral: over the pool's middle
            px, pz = ax + (sx - ax) * lay, az + (sz - az) * lay
            row = C[k]
            hp = a_t * 0.6 * sh * (1.0 - 0.3 * j)
            rot = 0.03 * tpb + j
            row[:] = (px, pz, hp, 0.13 - 0.03 * j, 0.044, math.cos(rot), math.sin(rot), 0.3 * hp)
            spike_top = max(spike_top, hp * 1.15)
            mound_sum += 0.3 * hp
            k += 1
        A[23] = k

        # ---- sources for the ripples
        S = A[O_S:O_S + MAXS * 4].reshape(MAXS, 4)
        ns = 0
        kap = KAPPA * (0.25 + 0.75 * ripple) * (0.8 + 0.4 * e)
        if idx.size:
            F = -kap * hmax * self.vel[idx] * self._wl[idx]
            m = np.abs(F) > 1e-3
            n_src = min(int(m.sum()), MAXS - MAXDROP - MAXK - MAXR)
            if n_src:
                sel = np.flatnonzero(m)[:n_src]
                rs = 0.6 * R[sel]
                S[:n_src, 0] = C[sel, 0]
                S[:n_src, 1] = C[sel, 1]
                S[:n_src, 2] = 1.0 / np.maximum(rs * rs, 1e-4)
                S[:n_src, 3] = F[sel]
                ns = n_src

        # ---- crowns
        K = A[O_K:O_K + MAXK * 8].reshape(MAXK, 8)
        crown_top = 0.0
        for j in range(MAXK):
            if not self.cr_on[j]:
                continue
            self.cr_age[j] += dt
            nj = int(self.cr_n[j])
            pts = self._pos[self.cr_p[j, :nj]]
            cen = pts.mean(axis=0)
            rc = self._crown_radius(pts)
            bloom = _smooth01(self.cr_age[j] / 0.18)
            tgt = float(self.amp[self.cr_p[j, :nj]].mean()) * bloom * math.exp(-self.cr_age[j] / 6.0)
            prev = self.cr_amp[j]
            self.cr_amp[j] += (tgt - prev) * _alpha(dt, 0.08)
            self.cr_ampv[j] = (self.cr_amp[j] - prev) / dt if dt > 0 else 0.0
            if self.cr_age[j] > 0.5 and tgt < 0.01 and self.cr_amp[j] < 0.01:
                self.cr_on[j] = False
                continue
            self.cr_rot[j] = (self.cr_rot[j] + self.cr_dir[j] * (0.15 + 0.35 * e) * sdt) % (2.0 * math.pi)
            N = self.cr_N[j]
            hc = self.cr_amp[j] * 0.17 * sh
            K[j] = (cen[0], cen[1], rc, hc, N, self.cr_rot[j], 0.45 * 2.0 * math.pi * rc / N, 0.22 * hc)
            crown_top = max(crown_top, hc * 1.2)
            mound_sum += 0.22 * hc
            if ns < MAXS and abs(self.cr_ampv[j]) > 1e-3:
                r2 = 0.8 * rc
                S[ns] = (cen[0], cen[1], 1.0 / (r2 * r2), -0.6 * kap * 0.13 * sh * self.cr_ampv[j])
                ns += 1
        A[24] = MAXK

        # ---- ridges
        Rr = A[O_R:O_R + MAXR * 8].reshape(MAXR, 8)
        ridge_top = 0.0
        for j in range(MAXR):
            self.rd_ampt[j] *= math.exp(-dt / 0.35) if dt > 0 else 1.0
            self.rd_amp[j] += (self.rd_ampt[j] - self.rd_amp[j]) * _alpha(dt, 0.07)
            since = self.clock - self.rd_last_t[j]
            if since > 0.2:
                self.rd_vel[j] *= math.exp(-dt / 0.4) if dt > 0 else 1.0
            target = self.rd_last_p[j] + self.rd_vel[j] * min(max(since, 0.0), 0.2)
            if self.rd_amp[j] < 0.003:
                continue
            self.rd_pos[j] += (target - self.rd_pos[j]) * _alpha(dt, 0.06)
            self.rd_pos[j] = min(max(self.rd_pos[j], 20.0), 109.0)
            sgn = self.rd_dir[j] if self.rd_dir[j] != 0.0 else 1.0
            pf = self.rd_pos[j]
            hx, hz = self._layout_xy(pf, lay)
            ax, az = self._layout_xy(pf - 0.5 * sgn, lay)
            bx, bz = self._layout_xy(pf + 0.5 * sgn, lay)
            tx, tz = bx - ax, bz - az
            tl = math.hypot(tx, tz)
            if tl < 1e-6:
                continue
            tx, tz = tx / tl, tz / tl
            edge = min(max((pf - 20.0) / 3.0, 0.0), 1.0) * min(max((109.0 - pf) / 3.0, 0.0), 1.0)
            hr = self.rd_amp[j] * 0.16 * sh * edge
            if self._seg is not None:
                u = (pf - 21.0) / 87.0 * self._seg[1] - self._seg[0]
                hr *= 1.0 - (1.0 - lay) * (0.0 if -0.02 <= u <= 1.02 else 1.0)
            Rr[j] = (hx, hz, tx, tz, hr, 0.11 + 0.08 * self.rd_amp[j], 0.034, self.rd_ph[j])
            ridge_top = max(ridge_top, hr * 1.6)
            mound_sum += 0.4 * hr * 1.75
            if ns < MAXS and hr > 1e-3:
                S[ns] = (hx, hz, 1.0 / (0.05 * 0.05), -kap * 2.5 * hr * min(abs(self.rd_vel[j]) / 20.0, 1.5))
                ns += 1
        A[25] = MAXR

        # ---- stray drops (energy)
        dr = self.drops
        dr[:, 2] += sdt
        self.drop_t -= sdt
        if self.drop_t <= 0.0:
            free = np.flatnonzero(dr[:, 2] > dr[:, 3])
            if free.size:
                i = int(free[0])
                dr[i] = (self.rng.uniform(-1.7, 1.7), self.rng.uniform(-1.2, 0.8), 0.0, 0.12,
                         self.rng.uniform(0.6, 1.2) * (0.5 + e))
            self.drop_t = float(self.rng.exponential(1.0 / (0.25 + 2.2 * e)))
        for i in range(MAXDROP):
            if dr[i, 2] < dr[i, 3] and ns < MAXS:
                x = dr[i, 2] / dr[i, 3]
                S[ns] = (dr[i, 0], dr[i, 1], 1.0 / (0.03 * 0.03), dr[i, 4] * (0.4 + ripple) * 4.0 * x * (1.0 - x))
                ns += 1
        A[26] = ns

        # ---- the wave step's constants
        sim_dt = min(sdt, 1.0 / 30.0)
        nsub = 0 if sim_dt <= 0.0 else min(MAX_SUB, max(1, int(math.ceil(sim_dt / H_MAX_SUB))))
        hsub = sim_dt / nsub if nsub else 0.0
        A[27] = (C_WAVE * hsub / DXW) ** 2
        A[28] = (3.2 - 2.6 * ripple) * (1.0 - 0.55 * self.damper) + 0.2
        A[29] = hsub
        A[30] = (0.03 + 0.22 * e) * (0.4 + ripple)            # slow swells: gentle, so the mirror stays crisp
        A[43] = 25.0

        # ---- the slab (a hard bound: the field kernel clamps to it)
        rip_b = RCAP
        top = max(spike_top, crown_top, ridge_top) + 0.002 + min(MSAT, mound_sum) + rip_b + 0.004
        yt = min(HCAP_ABS, top)
        A[16] = yt
        A[17] = -rip_b - 0.004
        A[41] = yt - 0.001
        A[19] = sharp0 * (1.0 - 0.45 * self.soft)

        # ---- the camera's key window: the keys struck in the last 4 s (and any still sounding), widened at once to take
        #      in a new note, narrowed slowly; after a rest it opens out to the whole keyboard
        rec = (self.clock - self.last_on < 4.0) | (self.amp > 0.08)
        rec[:21] = False
        rec[109:] = False
        if rec.any():
            ks = np.flatnonzero(rec)
            lo, hi = float(ks[0]) - 4.0, float(ks[-1]) + 4.0
            tw, tc = min(max(hi - lo, FOLLOW_MIN), 95.0), 0.5 * (lo + hi)
            wide = tw > self.fc_w
            tau_w, tau_c = (0.6, 0.8) if wide else (4.0, 2.2)
        else:
            tw, tc, tau_w, tau_c = 95.0, 64.5, 6.0, 6.0
        tc = min(max(tc, 17.0 + 0.5 * tw), 112.0 - 0.5 * tw) if tw < 95.0 else 64.5
        self.fc_w += (tw - self.fc_w) * _alpha(dt, tau_w)
        self.fc_c += (tc - self.fc_c) * _alpha(dt, tau_c)

        # ---- the camera.  Low: the eye stands at the arc's centre a hand's height over the pool and pans and zooms to
        #      frame the keys being played (the horizon a third down the frame, the keys ~62% down: as it zooms in it
        #      also sinks, so the spikes rise over the horizon).  High: top-down over the spiral.  Drifting (energy).
        es = self.e_s
        asp = self.w / max(self.h, 1)
        wf = (1.0 - camv) * (0.0 if self._seg is not None else 1.0)
        hv_f = math.atan(math.tan(min(PHI * self.fc_w / 87.0, 1.3)) / asp)
        hv = HV_WIDE + (min(max(hv_f, HV_MIN), HV_WIDE) - HV_WIDE) * wf
        th = math.tan(hv)
        psi = PHI * (2.0 * (self.fc_c - 21.0) / 87.0 - 1.0) * wf
        cph = 2.0 * math.pi * self.cam_ph
        drift = (0.4 + 0.5 * es) * th * math.sin(cph) * 0.12 + 0.01 * th * math.sin(0.37 * cph + 1.0)
        p_lo = math.atan(th / 3.0)
        ye = RA * math.tan(p_lo + math.atan(0.24 * th))
        p_lo += math.radians(0.4 + 0.6 * es) * (th / 0.42) * math.sin(0.71 * cph + 0.5)
        t_lo = np.array([RA * math.sin(psi), ye - RA * math.tan(p_lo), ZA - RA * math.cos(psi)])
        d_lo, y_lo = RA / math.cos(p_lo), -psi + drift
        t_hi, d_hi, p_hi, y_hi = np.array([0.0, 0.0, ZS]), 2.75, math.radians(88.0), 3.0 * drift
        tgt_c = t_lo + (t_hi - t_lo) * camv
        dist = d_lo + (d_hi - d_lo) * camv
        pitch = p_lo + (p_hi - p_lo) * camv
        yaw = y_lo + (y_hi - y_lo) * camv
        ro = tgt_c + dist * np.array([math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch)])
        fwd = _norm(tgt_c - ro)
        right = np.array([math.cos(yaw), 0.0, -math.sin(yaw)])
        up = np.cross(right, fwd)
        A[0], A[1] = self.w, self.h
        A[2:5], A[5:8], A[8:11], A[11:14] = ro, fwd, right, up
        A[14], A[15] = th, self.w / max(self.h, 1)
        A[18] = float(p["intensity"])
        A[42] = th / math.tan(HV_WIDE)
        A[47] = 0.7 * 2.0 * th / max(self.h, 1)

        # ---- the studio
        lg = 0.85 + 0.3 * es
        eyaw = yaw + 0.10 * th * math.sin(2.0 * math.pi * self.env_ph)     # the studio turns with the camera
        e_top = hv - pitch                                               # the frame top's elevation
        e0 = max(abs(e_top), math.radians(1.5))
        e1 = max(min(hv + pitch, math.radians(80.0)), e0 + math.radians(4.0))
        el_rim = math.radians(3.2)
        side = 0.75 * np.array([0.84, 0.92, 1.0]) + 0.25 * comp
        for i, (x, ev, a, soft, inten, col) in enumerate(_LIGHTS):
            if ev[0] == "rim":                         # a soft glow 0..~6 deg up: the spikes' edges reflect it
                az, el, b = 0.0, el_rim, 0.045
                soft = 0.03
            elif ev[0] == "band":
                az = math.atan(x * asp * th)
                lo_, hi_ = e0 + ev[1] * (e1 - e0), e0 + ev[2] * (e1 - e0)
                el, b = 0.5 * (lo_ + hi_), -math.tan(0.5 * (hi_ - lo_))
                a = a * asp * th * math.cos(az)
                soft = soft * th / 0.4245
            else:
                b = ev[1]
            c, u, v = _FIXED[i] if _FIXED[i] is not None else _light_basis(*_azel(math.degrees(az),
                                                                                    math.degrees(el)))
            L = A[O_L + 16 * i:O_L + 16 * (i + 1)]
            L[0:3], L[3:6], L[6:9] = _rot_y(c, eyaw), _rot_y(u, eyaw), _rot_y(v, eyaw)
            L[9], L[10], L[11] = a, b, soft
            if col == "rim":
                L[12] = inten * lg * (1.0 + 1.8 * self.flare)
                L[13:16] = rim
            elif col == "side":
                L[12] = inten * lg
                L[13:16] = side
            else:
                L[12] = inten * lg * (1.0 + (0.4 * self.flare if i in (1, 2) else 0.0))
                L[13:16] = col
        A[32:35] = rim
        A[35:38] = 0.8 * np.array([0.75, 0.82, 1.0]) + 0.2 * rim
        A[38] = 0.015 * (1.0 + 1.2 * self.flare) * lg
        A[39] = 2.2
        A[40] = lg
        bd = _rot_y(np.array([0.0, 0.0, -1.0]), eyaw)
        A[44], A[45], A[46] = bd[0], 0.0, bd[2]
        np.nan_to_num(A, copy=False, nan=0.0, posinf=0.0, neginf=0.0)

        # ---- the GPU
        self.arena.set(A)
        wv = self.wv
        for sub in range(nsub):
            ip, ic, inx = self.wi
            ratio = min(max(hsub / self.h_prev, 0.25), 4.0) if (sub == 0 and self.h_prev > 0) else 1.0
            launch_2d(self.k_wave, GXW, GZW, (wv[inx], wv[ic], wv[ip], self.arena, np.float32(ratio),
                                              np.float32((self.noise_t + sub * hsub) % 1000.0)))
            self.wi = [ic, inx, ip]
        if nsub:
            self.h_prev = hsub
        launch_2d(self.k_field, GX, GZ, (self.hb, wv[self.wi[1]], self.arena))
        launch_2d(self.k_grad, GX, GZ, (self.gb, self.hb))
        launch_2d(self.k_cmax, NCX, NCZ, (self.cm, self.hb))
        launch_2d(self.k_bmax, NBX, NBZ, (self.cm,))
        launch_2d(self.k_render, self.w, self.h, (self.buf, self.hb, self.gb, self.cm, self.arena,
                                                  np.uint32(self.seed)))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = self.hb = self.gb = self.cm = self.arena = None
        self.wv = []
