"""Jellyfish abyss: a bloom of bioluminescent jellyfish pulsing through black water, splatted as light.

Source: Original (inspired by https://en.wikipedia.org/wiki/Bioluminescence)
Author: smfx (original)
License: MIT (original)
license_class: permissive

Deep-sea bioluminescence built on the splat3d toolkit.  One fused set of generator kernels writes every frame's
geometry into device buffers allocated in setup, then Splat3D draws it additively:
  * bells: ~7k points per jelly sampled on a parametric dome (outer exumbrella + inner subumbrella layer) whose
    rim pulls in and whose dome stretches on a per-jelly contraction cycle (fast squeeze, slow release).  Weights
    light the scalloped rim band, 8 radial canals and 4 horseshoe gonads; additive density gives the translucent
    fresnel silhouette for free.  Meridian canals and rim / velum / gonad rings are drawn as polylines.
  * tentacles: analytic strands hanging from the rim whose radius follows the rim at a LAGGED contraction phase
    (the squeeze travels down them), with a helical sway wave and a current shear; oral arms are frilly twisted
    ribbons (splat3d ribbons) under the bell.
  * comb jellies (every 5th slot): ellipsoid bodies with 8 comb rows carrying travelling rainbow ripples, plus two
    long trailing tentacles.
  * siphonophores: long sinuous chains drifting through the bloom, beaded with glowing cormidia and a
    bioluminescent wave running along them.
  * marine snow: a wrapped volume of sinking motes around the camera; every point gets a depth-of-field size
    (energy conserving, so out-of-focus motes become soft dim bokeh) and depth fog.
  * a dim surface-lit water gradient behind it all, then bloom + ACES in resolve() straight to the 4K output.

Macros: energy -> swim (contraction) rate, contraction amplitude, glow (bell, lines, bloom), visible jelly count
(continuous per-slot fade), current drift, sway rate and camera drift.  pulse -> contraction-amplitude kick
(smoothed, adds to amplitude) and, on a host-detected rising edge, a rim-light flash that rings each bell then
travels down its tentacles (and along the siphonophore chains), rippling across the bloom left to right.
speed scales every rate (swim, sway, drift, current); drift_speed rotates the bioluminescent palette.
Every contraction / sway / ripple phase is integrated on the host and wrapped before upload.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS
from stream_service.plugins._lib.palettes import PALETTES
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.splat3d import Splat3D, look_at, orbit, srgb_to_linear
from stream_service.plugins._lib.upsample import internal_size

OPTIONS = ("--std=c++14", "--use_fast_math")
NJ = 24                    # jelly slots
NB = 7000                  # bell points per jelly
NM, KM = 16, 14            # meridians per jelly x vertices
NR, KR = 3, 49             # rings per jelly x vertices (closed)
NT, KT = 20, 40            # tentacles per jelly x vertices
NA, KA = 4, 28             # oral arms per jelly x vertices
NC, KC, NBD = 3, 128, 160  # siphonophore chains x stem vertices, beads per chain
NS = 40_000                # marine snow motes
JS, CS = 24, 20            # floats per jelly / chain record
BOX = np.array([5.4, 3.0, 3.8])          # jelly volume half extents (world units), centred on the origin
FADE = np.array([1.1, 1.1, 0.9])         # edge fade widths (wrapping is invisible)
CBOX = np.array([9.0, 2.6, 4.2])         # siphonophore volume
SNOW_H = (10.0, 6.0, 10.0)               # snow volume half extents (surrounds the camera)
LAG = 0.55                               # contraction phase lag over a tentacle's length (cycles)
CAM_D = 9.0

_SRC = HELPERS + r"""
#define NB %(nb)d
#define NM %(nm)d
#define KM %(km)d
#define NR %(nr)d
#define KR %(kr)d
#define NT %(nt)d
#define KT %(kt)d
#define NA %(na)d
#define KA %(ka)d
#define KC %(kc)d
#define NBD %(nbd)d
#define JS %(js)d
#define CS %(cs)d
#define LAG %(lag)ff
#define TWO_PI 6.2831853f

__device__ __forceinline__ float jsq(float x) { return x * x; }
__device__ __forceinline__ float jsm(float x) { x = clamp01(x); return x * x * (3.f - 2.f * x); }
// contraction cycle 0..1: fast squeeze (28%% of the cycle) then a slow release
__device__ __forceinline__ float jcon(float ph) {
    const float f = ph - floorf(ph);
    return f < 0.28f ? jsm(f * (1.f / 0.28f)) : 1.f - jsm((f - 0.28f) * (1.f / 0.72f));
}
// bell surface in the jelly frame (x, y = swim axis, z), in bell radii.  v: 0 apex .. 1 rim (jelly) or
// 0 top .. 1 bottom (comb jelly); th azimuth; c contraction (amplitude applied); flat 0 tall .. 1 flat dome
__device__ float3 bell_local(float v, float th, float c, float flat, float sp) {
    float s, co;
    if (sp > 0.5f) {
        __sincosf(v * 3.14159265f, &s, &co);
        const float r = 0.52f * s * (1.f + 0.05f * c) * (1.f + 0.05f * __cosf(8.f * th));
        return make_float3(r * __cosf(th), 0.92f * co * (1.f - 0.04f * c), r * __sinf(th));
    }
    __sincosf(v * 1.78f, &s, &co);
    const float v2 = v * v;
    const float R = 1.f - 0.36f * c * v * sqrtf(v);
    const float H = (0.95f - 0.45f * flat) * (1.f + 0.32f * c);
    const float r = R * s * (1.f + 0.035f * v2 * v2 * v2 * __cosf(16.f * th));
    return make_float3(r * __cosf(th), H * (co - 0.25f), r * __sinf(th));
}
__device__ __forceinline__ float3 jworld(const float* __restrict__ q, float3 L) {
    const float sz = q[12];
    return make_float3(q[0] + sz * (L.x * q[3] + L.y * q[6] + L.z * q[9]),
                       q[1] + sz * (L.x * q[4] + L.y * q[7] + L.z * q[10]),
                       q[2] + sz * (L.x * q[5] + L.y * q[8] + L.z * q[11]));
}
// depth of field: (size multiplier, weight multiplier) from camera depth
__device__ __forceinline__ float2 jdof(const float* __restrict__ G, float3 P) {
    const float z = G[0] * P.x + G[1] * P.y + G[2] * P.z + G[3];
    const float blur = fminf(fabsf(z - G[4]) / (0.3f * G[4]), 3.f);
    return make_float2(1.f + G[5] * blur * 2.2f, 1.f / (1.f + 0.4f * G[5] * blur));
}
// travelling flash along a strand: s 0..1 from the attachment; two global slots (time since, amplitude)
__device__ __forceinline__ float jflash(const float* __restrict__ G, float delay, float s) {
    float f = 0.f;
    #pragma unroll
    for (int k = 0; k < 2; ++k) {
        const float tf = G[6 + 2 * k] - delay, a = G[7 + 2 * k];
        if (tf > 0.f && a > 0.f) f += a * __expf(-tf * 1.1f) * __expf(-jsq((s - tf * G[10]) * 11.f));
    }
    return f;
}

// J record: 0-2 centre | 3-5 e1 | 6-8 axis | 9-11 e2 | 12 size | 13 phase | 14 amplitude | 15 visibility
//           16 hue | 17 species | 18 length mul | 19 sway phase | 20 spin | 21 ripple phase | 22 rim flash
//           23 flash delay
// G: 0-3 view z row | 4 focus | 5 dof | 6-9 flash slots | 10 flash speed | 11 tentacle length | 12-14 shear
//    16 contraction glow | 17-19 snow offset | 20-22 snow half | 23-25 snow centre | 26 snow amount
//    27 twinkle phase | 32 arm length
extern "C" __global__ void jf_bell(float4* __restrict__ BP, float* __restrict__ BI, float* __restrict__ BS,
                                   const float* __restrict__ J, const float* __restrict__ G, const int n,
                                   const unsigned int seed)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const int j = i / NB, k = i - j * NB;
    const float* q = J + j * JS;
    const float vis = q[15];
    if (!(vis > 0.f)) { BP[i] = make_float4(q[0], q[1], q[2], 0.f); BI[i] = 0.f; BS[i] = 1.f; return; }
    const float sp = q[17], spin = q[20];
    const float h1 = hash1(k, 11u, seed), h2 = hash1(k, 12u, seed), h3 = hash1(k, 13u, seed);
    const int sel = k & 7;
    float v, th, layer = 0.f;
    if (sp < 0.5f) {
        th = TWO_PI * h1;
        if (sel < 2) v = 0.88f + 0.12f * h2;                                   // rim band
        else if (sel == 2) v = 0.22f + 0.3f * h2;                              // gonad band
        else if (sel == 3) { th = floorf(h1 * 8.f) * 0.78539816f + (h3 - 0.5f) * 0.035f; v = 0.08f + 0.92f * h2; }
        else if (sel == 4) { layer = 1.f; v = sqrtf(h2); }                     // subumbrella
        else v = sqrtf(h2);
    } else {
        if (sel < 5) { th = floorf(h1 * 8.f) * 0.78539816f + (h3 - 0.5f) * 0.05f; v = 0.1f + 0.8f * h2; }
        else { th = TWO_PI * h1; v = acosf(1.f - 2.f * h2) * 0.31830989f; }
    }
    const float thl = th;                                                       // body-fixed azimuth
    th += spin;
    const float c = q[14] * jcon(q[13]);
    float3 L = bell_local(v, th, c, hash1(j, 77u, seed), sp);          // flatness: stable per slot
    if (layer > 0.5f) { L.x *= 0.9f; L.z *= 0.9f; L.y -= 0.07f; }
    const float3 P = jworld(q, L);
    float w, idx;
    const float hue = q[16];
    if (sp < 0.5f) {
        const float rimb = __expf(-jsq((1.f - v) * (1.f / 0.045f)));
        float cq = thl * (8.f / TWO_PI); cq = fabsf(cq - rintf(cq));
        const float canal = __expf(-jsq(cq * (1.f / 0.06f))) * jsm((v - 0.05f) * 5.f);
        const float lob = 0.5f + 0.5f * __cosf(4.f * thl + 0.78539816f);
        const float gon = __expf(-jsq((v - 0.36f) * (1.f / 0.1f))) * lob * lob * lob;
        w = 0.16f + 1.5f * rimb + 0.55f * canal + 0.9f * gon;
        if (layer > 0.5f) w *= 0.5f;
        w *= 0.65f + G[16] * c;
        w += q[22] * (2.5f * rimb + 0.25f);
        idx = hue + 0.28f * v + 0.3f * gon + 0.1f * rimb;
    } else {
        float cq = thl * (8.f / TWO_PI); cq = fabsf(cq - rintf(cq));
        const float comb = __expf(-jsq(cq * (1.f / 0.05f))) * jsm((v - 0.08f) * 8.f) * jsm((0.92f - v) * 8.f);
        const float rip = 0.5f + 0.5f * __sinf(TWO_PI * (v * 6.f - q[21]) + floorf(h1 * 8.f) * 0.7f);
        w = 0.07f + comb * (0.3f + 2.6f * rip * rip * rip) + q[22] * 0.6f * comb;
        idx = hue + 1.2f * v + 0.45f * rip + 0.125f * floorf(h1 * 8.f);
    }
    const float2 d = jdof(G, P);
    BP[i] = make_float4(P.x, P.y, P.z, w * vis * d.y);
    BI[i] = idx;
    BS[i] = d.x;
}

// meridian canals / comb rows (polylines, NM per jelly x KM vertices)
extern "C" __global__ void jf_mer(float4* __restrict__ V, float* __restrict__ I, const float* __restrict__ J,
                                  const float* __restrict__ G, const int n, const unsigned int seed)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const int per = NM * KM, j = i / per, r = i - j * per, m = r / KM, kk = r - m * KM;
    const float* q = J + j * JS;
    const float vis = q[15], sp = q[17];
    const float th = m * (TWO_PI / NM) + q[20];
    const float c = q[14] * jcon(q[13]);
    float v, w, idx;
    const float t = (float)kk / (float)(KM - 1);
    if (sp < 0.5f) {
        v = 0.04f + 0.96f * t;
        w = ((m & 1) == 0 ? 0.5f : 0.14f) * (0.65f + G[16] * c) + q[22] * 1.2f * v * v * v * v;
        idx = q[16] + 0.28f * v + 0.05f;
    } else {
        v = 0.1f + 0.8f * t;
        const float rip = 0.5f + 0.5f * __sinf(TWO_PI * (v * 6.f - q[21]) + (float)(m >> 1) * 0.7f);
        w = (m & 1) == 0 ? (0.2f + 1.3f * rip * rip * rip + 0.5f * q[22]) : 0.f;
        idx = q[16] + 1.2f * v + 0.45f * rip + 0.125f * (float)(m >> 1);
    }
    const float3 P = jworld(q, bell_local(v, th, c, hash1(j, 77u, seed), sp));
    V[i] = make_float4(P.x, P.y, P.z, w * vis);
    I[i] = idx;
}

// rim, velum and gonad-level rings (closed polylines, NR per jelly x KR vertices)
extern "C" __global__ void jf_ring(float4* __restrict__ V, float* __restrict__ I, const float* __restrict__ J,
                                   const float* __restrict__ G, const int n, const unsigned int seed)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const int per = NR * KR, j = i / per, r = i - j * per, ring = r / KR, kk = r - ring * KR;
    const float* q = J + j * JS;
    const float vis = q[15], sp = q[17];
    const float th = kk * (TWO_PI / (KR - 1)) + q[20];
    const float c = q[14] * jcon(q[13]);
    float v, w, sc = 1.f, dy = 0.f;
    if (sp < 0.5f) {
        if (ring == 0) { v = 1.f; w = 1.f + 3.f * q[22]; }
        else if (ring == 1) { v = 0.97f; w = 0.45f + q[22]; sc = 0.9f; dy = -0.06f; }
        else { v = 0.4f; w = 0.16f + 0.3f * q[22]; }
        w *= 0.65f + G[16] * c;
    } else {
        v = 0.5f; w = ring == 0 ? 0.12f + 0.4f * q[22] : 0.f;
    }
    float3 L = bell_local(v, th, c, hash1(j, 77u, seed), sp);
    L.x *= sc; L.z *= sc; L.y += dy;
    const float3 P = jworld(q, L);
    V[i] = make_float4(P.x, P.y, P.z, w * vis);
    I[i] = q[16] + 0.1f + 0.28f * v + 0.06f * ring;
}

// tentacles (polylines, NT per jelly x KT vertices): the rim radius at a lagged contraction phase travels down
extern "C" __global__ void jf_tent(float4* __restrict__ V, float* __restrict__ I, const float* __restrict__ J,
                                   const float* __restrict__ G, const int n, const unsigned int seed)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const int per = NT * KT, j = i / per, r = i - j * per, t = r / KT, kk = r - t * KT;
    const float* q = J + j * JS;
    const float vis = q[15], sp = q[17], sz = q[12];
    const float s = (float)kk / (float)(KT - 1);
    const float ht = hash1(t, (unsigned)(j * 31 + 5), seed), ht2 = hash1(t, (unsigned)(j * 31 + 6), seed);
    const float flat = hash1(j, 77u, seed), wp = q[19];
    float3 L;
    float w, idx;
    if (sp < 0.5f) {
        const float th = ((float)t + 0.5f + 0.35f * (ht - 0.5f)) * (TWO_PI / NT) + q[20];
        const float len = G[11] * q[18] * (0.55f + 0.45f * ht);
        const float cl = q[14] * jcon(q[13] - s * LAG);
        const float3 r0 = bell_local(1.f, th, cl, flat, 0.f);
        const float pull = 1.f - 0.35f * s;
        const float sw = 0.22f * len * s * sqrtf(s);
        L = make_float3(r0.x * pull + sw * __sinf(TWO_PI * (1.3f * s - wp) + th * 1.7f + ht2 * 6.f),
                        r0.y - s * len,
                        r0.z * pull + sw * __cosf(TWO_PI * (1.1f * s - wp) + th * 1.3f + ht2 * 5.f));
        const float fl = jflash(G, q[23], s);
        w = (0.5f * (1.f - 0.75f * s)) * (0.75f + 0.5f * G[16] * cl) + 3.5f * fl;
        idx = q[16] + 0.12f + 0.4f * s + 0.25f * fl;
    } else if (t < 2) {
        const float th = (float)t * 3.14159265f + q[20];
        const float len = G[11] * q[18] * 1.5f;
        const float3 r0 = bell_local(0.5f, th, 0.f, flat, 1.f);
        const float sw = 0.3f * len * s;
        const float out = 1.f + 1.4f * s;
        L = make_float3(r0.x * out + sw * __sinf(TWO_PI * (0.9f * s - wp) + ht2 * 6.f),
                        r0.y - s * len * 0.75f,
                        r0.z * out + sw * __cosf(TWO_PI * (0.7f * s - wp) + ht2 * 5.f));
        const float fl = jflash(G, q[23], s);
        w = 0.35f * (1.f - 0.6f * s) + 2.5f * fl;
        idx = q[16] + 0.6f + 0.5f * s;
    } else {
        L = make_float3(0.f, 0.f, 0.f); w = 0.f; idx = 0.f;
    }
    float3 P = jworld(q, L);
    const float k2 = jsq(s * G[11] * q[18] * sz) * 0.12f;
    P.x += G[12] * k2; P.y += G[13] * k2; P.z += G[14] * k2;
    V[i] = make_float4(P.x, P.y, P.z, w * vis);
    I[i] = idx;
}

// oral arms (ribbon rails A, B: NA per jelly x KA vertices), frilly and twisted
extern "C" __global__ void jf_arm(float4* __restrict__ A, float4* __restrict__ B, float* __restrict__ I,
                                  const float* __restrict__ J, const float* __restrict__ G, const int n,
                                  const unsigned int seed)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const int per = NA * KA, j = i / per, r = i - j * per, a = r / KA, kk = r - a * KA;
    const float* q = J + j * JS;
    const float vis = q[15], sp = q[17];
    if (sp > 0.5f) {
        A[i] = make_float4(q[0], q[1], q[2], 0.f); B[i] = A[i]; I[i] = 0.f; return;
    }
    const float s = (float)kk / (float)(KA - 1);
    const float wp = q[19];
    const float len = G[11] * q[18] * 0.6f * G[32];
    const float cl = q[14] * jcon(q[13] - s * 0.4f);
    const float th = (float)a * 1.5707963f + 0.78539816f + q[20] + 0.9f * s;
    const float ra = 0.08f + 0.2f * s * (1.f + 0.3f * cl);
    float cx = ra * __cosf(th), cz = ra * __sinf(th);
    const float cy = 0.18f - s * len;
    const float sw = 0.12f * len * s * s;
    cx += sw * __sinf(TWO_PI * (0.8f * s - wp) + (float)a * 1.7f);
    cz += sw * __cosf(TWO_PI * (0.8f * s - wp) + (float)a * 2.3f);
    const float wd = 0.2f * (1.f - 0.5f * s) * (0.75f + 0.25f * __sinf(TWO_PI * (5.f * s - wp) + (float)a));
    const float psi = th + 1.5707963f + 3.5f * s;
    const float dx = 0.5f * wd * __cosf(psi), dz = 0.5f * wd * __sinf(psi), dy = 0.15f * wd * __sinf(2.f * psi);
    const float fl = jflash(G, q[23] + 0.05f, s);
    const float w = vis * (0.45f * (1.f - 0.5f * s) * (0.8f + 0.4f * cl) + 1.5f * fl);
    const float3 Pa = jworld(q, make_float3(cx + dx, cy + dy, cz + dz));
    const float3 Pb = jworld(q, make_float3(cx - dx, cy - dy, cz - dz));
    A[i] = make_float4(Pa.x, Pa.y, Pa.z, w);
    B[i] = make_float4(Pb.x, Pb.y, Pb.z, w);
    I[i] = q[16] + 0.42f + 0.25f * s;
}

// siphonophore chains: stem polylines (KC per chain) and glowing cormidia beads (NBD per chain)
// C record: 0-2 base | 3-5 dir | 6-8 n1 | 9-11 n2 | 12 phase | 13 vis | 14 length | 15 hue | 16 amp | 17 glow
//           phase | 18 flash delay
__device__ __forceinline__ float3 jstem(const float* __restrict__ c, float s) {
    const float a1 = c[16] * (0.25f + s) * __sinf(TWO_PI * (1.4f * s - c[12]));
    const float a2 = 0.6f * c[16] * (0.25f + s) * __cosf(TWO_PI * (0.9f * s - c[12]) + 1.3f);
    const float u = (s - 0.5f) * c[14];
    return make_float3(c[0] + c[3] * u + c[6] * a1 + c[9] * a2, c[1] + c[4] * u + c[7] * a1 + c[10] * a2,
                       c[2] + c[5] * u + c[8] * a1 + c[11] * a2);
}
extern "C" __global__ void jf_siph(float4* __restrict__ SV, float* __restrict__ SI, float4* __restrict__ BD,
                                   float* __restrict__ BI, float* __restrict__ BS, const float* __restrict__ C,
                                   const float* __restrict__ G, const int nchains, const unsigned int seed)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    const int ns = nchains * KC, nb = nchains * NBD;
    if (i >= ns + nb) return;
    if (i < ns) {
        const int ch = i / KC, kk = i - ch * KC;
        const float* c = C + ch * CS;
        const float s = (float)kk / (float)(KC - 1);
        const float3 P = jstem(c, s);
        const float fl = jflash(G, c[18], s);
        SV[i] = make_float4(P.x, P.y, P.z, c[13] * (0.3f + 2.f * fl) * (0.4f + 0.6f * jsm(s * 6.f)));
        SI[i] = c[15] + 0.5f * s;
        return;
    }
    const int b = i - ns, ch = b / NBD, r = b - ch * NBD;
    const float* c = C + ch * CS;
    const int ci = r / 5, sub = r - ci * 5;
    const float sc = 0.04f + 0.94f * ((float)ci + 0.5f) / (float)(NBD / 5);
    float3 P = jstem(c, sc);
    const float ang = (float)sub * (TWO_PI / 5.f) + (float)ci * 0.7f;
    const float rad = sub == 0 ? 0.f : (ci < 3 ? 0.16f : 0.06f + 0.03f * hash1(r, ch, seed));
    const float ca = __cosf(ang) * rad, sa = __sinf(ang) * rad;
    P.x += c[6] * ca + c[9] * sa; P.y += c[7] * ca + c[10] * sa; P.z += c[8] * ca + c[11] * sa;
    const float wv = 0.5f + 0.5f * __sinf(TWO_PI * (3.f * sc - c[17]));
    const float wv2 = wv * wv * wv * wv;
    const float fl = jflash(G, c[18], sc);
    const float w = c[13] * ((sub == 0 ? 0.9f : 0.45f) + 2.4f * wv2 * wv2 + 3.f * fl);
    const float2 d = jdof(G, P);
    BD[b] = make_float4(P.x, P.y, P.z, w * d.y);
    BI[b] = c[15] + 0.3f + 0.6f * sc + 0.2f * (float)(sub == 0);
    BS[b] = d.x * (ci < 3 ? 1.6f : 1.f);
}

// marine snow: a wrapped box of motes (offset integrated on the host), faded at the box faces, with DOF sizes
extern "C" __global__ void jf_snow(float4* __restrict__ S, float* __restrict__ SZ, float* __restrict__ SI,
                                   const float* __restrict__ G, const int n, const unsigned int seed)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const float hx = G[20], hy = G[21], hz = G[22];
    float x = (hash1(i, 1u, seed) * 2.f - 1.f) * hx + G[17];
    float y = (hash1(i, 2u, seed) * 2.f - 1.f) * hy + G[18];
    float z = (hash1(i, 3u, seed) * 2.f - 1.f) * hz + G[19];
    x -= 2.f * hx * floorf((x + hx) / (2.f * hx));
    y -= 2.f * hy * floorf((y + hy) / (2.f * hy));
    z -= 2.f * hz * floorf((z + hz) / (2.f * hz));
    const float edge = jsm((hx - fabsf(x)) * 1.2f) * jsm((hy - fabsf(y)) * 1.2f) * jsm((hz - fabsf(z)) * 1.2f);
    const float h4 = hash1(i, 4u, seed), h5 = hash1(i, 5u, seed);
    const float3 P = make_float3(x + G[23], y + G[24], z + G[25]);
    const float tw = 0.7f + 0.3f * __sinf(TWO_PI * (h5 + G[27]));
    const float2 d = jdof(G, P);
    S[i] = make_float4(P.x, P.y, P.z, G[26] * (0.15f + 0.85f * h4 * h4 * h4 * h4) * edge * tw * d.y);
    SZ[i] = d.x * (0.8f + 0.6f * h5);
    SI[i] = 0.55f + 0.2f * h4;
}

// deep-water backdrop: dark gradient lit faintly from the surface (sRGB uint8, dithered)
extern "C" __global__ void jf_bg(uchar4* __restrict__ out, const int W, const int H, const float tr, const float tg,
                                 const float tb, const float gr, const float gg, const float gb, const float gx,
                                 const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float xn = (x + 0.5f) / W, yn = (y + 0.5f) / H;
    const float k = __powf(1.f - yn, 1.8f);
    const float g = __expf(-(jsq((xn - gx) * 1.6f) + jsq(yn * 2.4f)));
    float c[3] = {tr * k + gr * g, tg * k + gg * g, tb * k + gb * g};
    const float dz = hash1(x, y, seed) - 0.5f;
    unsigned char o[3];
    #pragma unroll
    for (int ch = 0; ch < 3; ++ch) {
        float v = fminf(fmaxf(c[ch], 0.f), 1.f);
        v = v <= 0.0031308f ? v * 12.92f : 1.055f * __powf(v, 1.f / 2.4f) - 0.055f;
        o[ch] = (unsigned char)fminf(fmaxf(v * 255.f + 0.5f + dz, 0.f), 255.f);
    }
    out[y * W + x] = make_uchar4(o[0], o[1], o[2], 255);
}
""" % dict(nb=NB, nm=NM, km=KM, nr=NR, kr=KR, nt=NT, kt=KT, na=NA, ka=KA, kc=KC, nbd=NBD, js=JS, cs=CS, lag=LAG)


def _pal_host(coeffs, t: float) -> tuple[float, float, float]:
    """Cosine palette on the host (sRGB 0..1)."""
    a, b, c, d = coeffs[0:3], coeffs[3:6], coeffs[6:9], coeffs[9:12]
    return tuple(min(max(a[i] + b[i] * math.cos(2 * math.pi * (c[i] * t + d[i])), 0.0), 1.0) for i in range(3))


def _sstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _launch(k, n, args):
    k(((n + 255) // 256,), (256,), args)


class JellyfishAbyss(Source):
    name = "jellyfish_abyss"
    label = "Jellyfish abyss"
    description = ("A bloom of bioluminescent jellyfish, comb jellies and siphonophore chains pulsing through "
                   "black water with marine snow; beats flash the rims and send light down the tentacles.")
    tags = {"family": "water", "dims": "3d", "toolkit": "splat3d", "technique": ["particles", "points", "wireframe"],
            "moods": ["dreamy", "organic", "hypnotic"], "style": "gentle", "energy": [0.0, 0.7]}
    params = {
        **standard_scene_params(palette="neon", render_scale=0.5, period=1.0, drift=0.02),
        "jelly_count": num(6.0, 24.0, 12.0, "Jellyfish", tier="regional", basic=True),
        "tentacle_length": num(0.3, 2.0, 1.0, "Tentacle length", tier="regional", basic=True),
        "hue_spread": num(0.0, 1.0, 0.35, "Hue spread", tier="regional"),
        "depth_of_field": num(0.0, 1.0, 0.5, "Depth of field", tier="regional"),
        "marine_snow": num(0.0, 2.0, 0.8, "Marine snow", tier="regional"),
        "siphonophores": num(0.0, 1.0, 0.6, "Siphonophore chains", tier="regional"),
        "fog": num(0.0, 1.0, 0.5, "Depth fog", tier="regional"),
        "glow": num(0.0, 2.0, 0.8, "Bloom", tier="regional"),
        "swim_speed": num(0.0, 3.0, 1.0, "Swim rate", unit="×", tier="realtime", bind="rate"),
        "current": num(0.0, 3.0, 1.0, "Current drift", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        rng = np.random.default_rng(self.seed)
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        n_pts = NJ * NB + NC * NBD + NS
        n_seg = NJ * (NM * (KM - 1) + NR * (KR - 1) + NT * (KT - 1) + NA * 4 * (KA - 1)) + NC * (KC - 1)
        self.sp = Splat3D(ctx, self.w, self.h, max_points=n_pts, max_lines=n_seg)

        f4 = lambda *s: cp.zeros((*s, 4), cp.float32)   # noqa: E731
        f1 = lambda n: cp.zeros(n, cp.float32)          # noqa: E731
        self.BP, self.BI, self.BS = f4(NJ * NB), f1(NJ * NB), f1(NJ * NB)
        self.MV, self.MI = f4(NJ * NM * KM), f1(NJ * NM * KM)
        self.RV, self.RI = f4(NJ * NR * KR), f1(NJ * NR * KR)
        self.TV, self.TI = f4(NJ * NT * KT), f1(NJ * NT * KT)
        self.AA, self.AB, self.AI = f4(NJ * NA * KA), f4(NJ * NA * KA), f1(NJ * NA * KA)
        self.SV, self.SI = f4(NC * KC), f1(NC * KC)
        self.BD, self.BDI, self.BDS = f4(NC * NBD), f1(NC * NBD), f1(NC * NBD)
        self.SN, self.SNZ, self.SNI = f4(NS), f1(NS), f1(NS)
        self.J, self.C, self.G = f1(NJ * JS), f1(NC * CS), f1(64)
        self.Jh = np.zeros((NJ, JS), np.float32)
        self.Ch = np.zeros((NC, CS), np.float32)
        self.Gh = np.zeros(64, np.float32)

        mod = raw_module(_SRC, OPTIONS)
        self.k_bell = mod.get_function("jf_bell")
        self.k_mer = mod.get_function("jf_mer")
        self.k_ring = mod.get_function("jf_ring")
        self.k_tent = mod.get_function("jf_tent")
        self.k_arm = mod.get_function("jf_arm")
        self.k_siph = mod.get_function("jf_siph")
        self.k_snow = mod.get_function("jf_snow")
        self.k_bg = mod.get_function("jf_bg")

        # jelly slots: best-candidate placement so any prefix (the visible count) is spread through the volume
        pos = []
        for j in range(NJ):
            cand = rng.uniform(-1.0, 1.0, (16, 3)) * (BOX - 0.4 * FADE)
            if pos:
                d = np.min(np.linalg.norm(cand[:, None, :] - np.asarray(pos)[None], axis=2), axis=1)
                pos.append(cand[int(np.argmax(d))])
            else:
                pos.append(cand[0] * 0.3)
        self.jpos = np.asarray(pos, np.float64)
        self.species = np.array([1.0 if j % 5 == 3 else 0.0 for j in range(NJ)])
        self.jsize = np.where(self.species > 0.5, rng.uniform(0.45, 0.6, NJ), rng.uniform(0.42, 0.95, NJ))
        self.lenmul = rng.uniform(0.7, 1.3, NJ)
        self.hue_r = rng.uniform(-0.5, 0.5, NJ)
        self.rate_mul = rng.uniform(0.75, 1.25, NJ)
        self.phase = rng.uniform(0.0, 1.0, NJ)
        self.spin = rng.uniform(0.0, 2 * math.pi, NJ)
        self.spin_rate = rng.uniform(0.04, 0.14, NJ) * rng.choice([-1.0, 1.0], NJ)
        self.tph = rng.uniform(0.0, 100.0, NJ)
        self.tilt_p = rng.uniform(0.0, 2 * math.pi, (NJ, 2))
        self.wph = rng.uniform(0.0, 1.0, NJ)
        self.ripple = rng.uniform(0.0, 1.0, NJ)
        self.delay_r = rng.uniform(0.0, 0.08, NJ)

        # siphonophore chains
        self.cpos = rng.uniform(-1.0, 1.0, (NC, 3)) * (CBOX - FADE) * np.array([1.0, 0.8, 0.8])
        self.cyaw = rng.uniform(-0.5, 0.5, NC) + np.where(rng.uniform(size=NC) < 0.5, 0.0, math.pi)
        self.cyaw_rate = rng.uniform(0.01, 0.03, NC) * rng.choice([-1.0, 1.0], NC)
        self.clen = rng.uniform(5.0, 7.0, NC)
        self.chue = rng.uniform(-0.5, 0.5, NC)
        self.cph = rng.uniform(0.0, 1.0, NC)
        self.cgph = rng.uniform(0.0, 1.0, NC)

        self.snow_off = np.zeros(3)
        self.twinkle = 0.0
        self.cur_yaw = float(rng.uniform(0.0, 2 * math.pi))
        self.yaw = float(rng.uniform(-0.4, 0.4))
        self.bob = 0.0
        self.drift = 0.0
        self.e_s = float(params["energy"])
        self.kick = 0.0
        self.prev_pulse = 0.0
        self.refr = 0.0
        self.flash = [[9.0, 0.0], [9.0, 0.0]]     # [seconds since, amplitude]

    # -------------------------------------------------------------------------------------------- per frame
    def _advance(self, p, dt: float):
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        a = 1.0 - math.exp(-dt / 0.4) if dt > 0 else 0.0
        self.e_s += (e - self.e_s) * a
        es = self.e_s
        ka = 1.0 - math.exp(-dt / (0.06 if pulse > self.kick else 0.3)) if dt > 0 else 0.0
        self.kick += (pulse - self.kick) * ka

        # beat flash: host rising edge with a refractory period
        self.refr = max(0.0, self.refr - dt)
        if pulse > 0.2 and pulse - self.prev_pulse > 0.15 and self.refr <= 0.0:
            slot = 0 if self.flash[0][1] * math.exp(-self.flash[0][0]) < self.flash[1][1] * math.exp(-self.flash[1][0]) else 1
            self.flash[slot] = [0.0, pulse]
            self.refr = 0.12
        self.prev_pulse = pulse
        for f in self.flash:
            f[0] = min(f[0] + dt, 9.0)
            if f[0] > 6.0:
                f[1] = 0.0

        swim = float(p["swim_speed"]) * spd
        rate = (0.3 + 0.5 * e + 0.25 * self.kick) * swim * self.rate_mul             # contraction cycles / s
        self.amp = min(1.25, (0.45 + 0.35 * e) * (1.0 + 0.8 * self.kick))
        self.phase = np.mod(self.phase + rate * dt, 1.0)
        self.ripple = np.mod(self.ripple + (1.0 + 2.5 * e) * swim * dt, 1.0)
        self.wph = np.mod(self.wph + (0.12 + 0.25 * e) * spd * dt, 1.0)
        self.spin = np.mod(self.spin + self.spin_rate * spd * dt, 2 * math.pi)
        self.tph += (0.12 + 0.2 * e) * spd * dt

        # orientation: gentle tilt wobble about the vertical (comb jellies tilt more)
        tamp = np.where(self.species > 0.5, 0.75, 0.32)
        tx = tamp * np.sin(0.7 * self.tph + self.tilt_p[:, 0])
        tz = tamp * np.sin(0.53 * self.tph + self.tilt_p[:, 1])
        ax = np.stack([np.sin(tz), np.cos(tx) * np.cos(tz), np.sin(tx) * np.cos(tz)], 1)
        ax /= np.linalg.norm(ax, axis=1, keepdims=True)
        e1 = np.stack([ax[:, 1], -ax[:, 0], np.zeros(NJ)], 1)
        e1 /= np.linalg.norm(e1, axis=1, keepdims=True) + 1e-9
        self.axis, self.e1, self.e2 = ax, e1, np.cross(e1, ax)

        # swimming: thrust along the axis during the squeeze, glide otherwise; current + slow sink
        f = self.phase
        tp = np.where(f < 0.35, np.sin(np.pi * np.minimum(f, 0.35) / 0.35), 0.0)
        vj = np.where(self.species > 0.5, 0.25 * (0.5 + e) * swim,
                      (0.06 + 0.55 * tp * self.amp) * (0.4 + rate))
        self.cur_yaw += 0.02 * spd * dt
        cs = (0.05 + 0.25 * e) * float(p["current"]) * spd
        self.cur = np.array([math.cos(self.cur_yaw), 0.0, math.sin(self.cur_yaw) * 0.4]) * cs
        vel = ax * (vj * self.jsize)[:, None] + self.cur[None] + np.array([0.0, -0.03 * spd, 0.0])
        self.jpos = np.mod(self.jpos + vel * dt + BOX, 2 * BOX) - BOX

        # chains drift along their stems, slowly turning
        self.cyaw += self.cyaw_rate * spd * dt
        cdir = np.stack([np.cos(self.cyaw), 0.12 * np.sin(0.7 * self.cyaw), np.sin(self.cyaw) * 0.6], 1)
        cdir /= np.linalg.norm(cdir, axis=1, keepdims=True)
        self.cdir = cdir
        self.cpos = np.mod(self.cpos + (cdir * (0.12 + 0.2 * e) * swim + self.cur[None]) * dt + CBOX, 2 * CBOX) - CBOX
        self.cph = np.mod(self.cph + (0.18 + 0.25 * e) * spd * dt, 1.0)
        self.cgph = np.mod(self.cgph + (0.3 + 0.6 * e) * spd * dt, 1.0)

        # snow sinks and rides the current
        h = np.asarray(SNOW_H)
        self.snow_off = np.mod(self.snow_off + (self.cur + np.array([0.0, -(0.05 + 0.05 * e) * spd, 0.0])) * dt
                               + h, 2 * h) - h
        self.twinkle = math.fmod(self.twinkle + 0.3 * spd * dt, 1.0)
        self.yaw += (0.015 + 0.05 * es) * spd * dt
        self.bob += (0.1 + 0.15 * es) * spd * dt
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)
        return e, pulse, es

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.1)
        e, pulse, es = self._advance(p, dt)
        seed = np.uint32(self.seed)

        # camera: slow orbit, looking slightly up into the bloom
        pitch = -0.1 + (0.03 + 0.04 * es) * math.sin(self.bob)
        cam = look_at(orbit((0.0, 0.2, 0.0), CAM_D, self.yaw, pitch), (0.0, 0.2, 0.0), fov_deg=50.0)
        view = cam.view()

        # visibility: continuous count (energy fades jellies in/out per slot) x edge fade of the wrapped volume
        cnt = min(max(float(p["jelly_count"]) * (0.55 + 0.45 * es), 2.0), float(NJ))
        nj = min(NJ, int(math.ceil(cnt)))
        vis = _sstep(0.0, 1.0, np.clip(cnt - np.arange(NJ), 0.0, 1.0))
        edge = np.prod(_sstep(BOX, BOX - FADE, np.abs(self.jpos)), axis=1)
        vis = vis * edge
        delay = 0.25 * (self.jpos[:, 0] + BOX[0]) / (2 * BOX[0]) + self.delay_r
        rimf = np.zeros(NJ)
        for tf, amp in self.flash:
            if amp > 0.0:
                d = tf - delay
                rimf += np.where(d > 0.0, amp * np.minimum(d / 0.04, 1.0) * np.exp(-np.maximum(d, 0.0) / 0.3), 0.0)

        hs = float(p["hue_spread"])
        Jh = self.Jh
        Jh[:, 0:3] = self.jpos
        Jh[:, 3:6] = self.e1
        Jh[:, 6:9] = self.axis
        Jh[:, 9:12] = self.e2
        Jh[:, 12] = self.jsize
        Jh[:, 13] = self.phase
        Jh[:, 14] = np.where(self.species > 0.5, 0.3, self.amp)
        Jh[:, 15] = vis
        Jh[:, 16] = hs * self.hue_r
        Jh[:, 17] = self.species
        Jh[:, 18] = self.lenmul
        Jh[:, 19] = self.wph
        Jh[:, 20] = self.spin
        Jh[:, 21] = self.ripple
        Jh[:, 22] = rimf
        Jh[:, 23] = delay
        self.J.set(Jh.reshape(-1))

        # chains
        sv = float(p["siphonophores"])
        cvis = np.clip(NC * sv - np.arange(NC), 0.0, 1.0) * np.prod(_sstep(CBOX, CBOX - FADE, np.abs(self.cpos)), 1)
        Ch = self.Ch
        up = np.array([0.0, 1.0, 0.0])
        for c in range(NC):
            d = self.cdir[c]
            n1 = up - d * float(np.dot(up, d))
            n1 /= np.linalg.norm(n1) + 1e-9
            n2 = np.cross(d, n1)
            Ch[c, 0:3], Ch[c, 3:6], Ch[c, 6:9], Ch[c, 9:12] = self.cpos[c], d, n1, n2
            Ch[c, 12], Ch[c, 13], Ch[c, 14] = self.cph[c], cvis[c], self.clen[c]
            Ch[c, 15], Ch[c, 16], Ch[c, 17] = 0.5 + hs * self.chue[c], 0.28, self.cgph[c]
            Ch[c, 18] = 0.25 * (self.cpos[c, 0] + CBOX[0]) / (2 * CBOX[0])
        self.C.set(Ch.reshape(-1))

        Gh = self.Gh
        Gh[0:4] = view[2]
        Gh[4] = CAM_D
        Gh[5] = float(p["depth_of_field"])
        Gh[6], Gh[7], Gh[8], Gh[9] = self.flash[0][0], self.flash[0][1], self.flash[1][0], self.flash[1][1]
        Gh[10] = 0.9
        Gh[11] = 2.6 * float(p["tentacle_length"])
        Gh[12:15] = self.cur * 0.6
        Gh[16] = 0.5 + 0.6 * e
        Gh[17:20] = self.snow_off
        Gh[20:23] = SNOW_H
        Gh[23:26] = (0.0, 0.0, 0.0)
        Gh[26] = float(p["marine_snow"])
        Gh[27] = self.twinkle
        Gh[32] = 1.0
        self.G.set(Gh)

        # ---- generate geometry
        _launch(self.k_bell, nj * NB, (self.BP, self.BI, self.BS, self.J, self.G, np.int32(nj * NB), seed))
        _launch(self.k_mer, nj * NM * KM, (self.MV, self.MI, self.J, self.G, np.int32(nj * NM * KM), seed))
        _launch(self.k_ring, nj * NR * KR, (self.RV, self.RI, self.J, self.G, np.int32(nj * NR * KR), seed))
        _launch(self.k_tent, nj * NT * KT, (self.TV, self.TI, self.J, self.G, np.int32(nj * NT * KT), seed))
        _launch(self.k_arm, nj * NA * KA, (self.AA, self.AB, self.AI, self.J, self.G, np.int32(nj * NA * KA), seed))
        _launch(self.k_siph, NC * (KC + NBD), (self.SV, self.SI, self.BD, self.BDI, self.BDS, self.C, self.G,
                                               np.int32(NC), seed))
        if p["marine_snow"] > 0.0:
            _launch(self.k_snow, NS, (self.SN, self.SNZ, self.SNI, self.G, np.int32(NS), seed))

        # ---- palette
        sess = self.session_palette()
        coeffs = np.asarray(PALETTES[p["palette"]] if sess is None else sess, np.float64).reshape(-1)
        pal = tuple(coeffs.tolist())
        period, drift = float(p["palette_period"]), self.drift
        fog = 0.03 + 0.07 * float(p["fog"])
        glow_e = (0.55 + 0.6 * e) * (1.0 + 0.35 * pulse)

        sp = self.sp
        sp.clear()
        common = dict(cam=cam, palette=pal, period=period, drift=drift, fog=fog, weight_in_w=True)
        if p["marine_snow"] > 0.0:
            sp.points(self.SN, intensity=0.09, size_px=1.6, size_range=(0.6, 9.0), sizes=self.SNZ,
                      index=self.SNI, near_fade=(0.4, 1.6), colour=(0.85, 0.9, 1.0), **common)
        if sv > 0.0:
            sp.polylines(self.SV, k=KC, n_strips=NC, intensity=0.5 * glow_e, width_px=1.0, index=self.SI, **common)
            sp.points(self.BD, intensity=0.9 * glow_e, size_px=2.6, size_range=(0.6, 8.0), sizes=self.BDS,
                      index=self.BDI, **common)
        sp.points(self.BP, n=nj * NB, intensity=1.7 * glow_e, size_px=1.8, size_range=(0.6, 4.0), sizes=self.BS,
                  index=self.BI, **common)
        sp.polylines(self.MV, k=KM, n_strips=nj * NM, intensity=0.5 * glow_e, width_px=1.0, index=self.MI, **common)
        sp.polylines(self.RV, k=KR, n_strips=nj * NR, intensity=0.9 * glow_e, width_px=1.4, index=self.RI, **common)
        sp.polylines(self.TV, k=KT, n_strips=nj * NT, intensity=0.55 * glow_e, width_px=0.9, index=self.TI, **common)
        sp.ribbons(self.AA, self.AB, strands=4, k=KA, n_ribbons=nj * NA, edge=2.0, intensity=0.3 * glow_e,
                   width_px=0.8, index=self.AI, **common)

        # ---- backdrop (deep water, faint surface light) then resolve over it
        top = _pal_host(coeffs, drift + 0.55)
        top = [0.6 * srgb_to_linear(v) + 0.4 * w for v, w in zip(top, (0.004, 0.02, 0.05))]
        bk = 0.05 * (0.7 + 0.5 * es)
        gk = 0.03 * (0.6 + 0.6 * es)
        H, W = int(out.shape[0]), int(out.shape[1])
        self.k_bg(((W + 31) // 32, (H + 7) // 8, 1), (32, 8, 1),
                  (out, np.int32(W), np.int32(H), np.float32(top[0] * bk), np.float32(top[1] * bk),
                   np.float32(top[2] * bk), np.float32(top[0] * gk), np.float32(top[1] * gk),
                   np.float32(top[2] * gk), np.float32(0.5 + 0.15 * math.sin(0.3 * self.yaw)), seed))
        sp.resolve(out, exposure=1.05 * float(p["intensity"]) * (1.0 + 0.2 * pulse), tonemap="aces",
                   saturation=1.15, glow=float(p["glow"]) * (0.6 + 0.6 * e) * (1.0 + 0.5 * pulse),
                   glow_radius_px=20.0, glow_wide=0.6, background=out)

    def close(self) -> None:
        if getattr(self, "sp", None) is not None:
            self.sp.close()
        self.sp = None
        for k in ("BP", "BI", "BS", "MV", "MI", "RV", "RI", "TV", "TI", "AA", "AB", "AI", "SV", "SI", "BD", "BDI",
                  "BDS", "SN", "SNZ", "SNI", "J", "C", "G"):
            setattr(self, k, None)
