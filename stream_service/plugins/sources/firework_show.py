"""Firework show: beat-launched 3D fireworks over a dark city skyline and a rippling lake.

Source: Original (inspired by https://en.wikipedia.org/wiki/Fireworks)
Author: smfx (original)
License: MIT (original)
license_class: permissive

Look: a night harbour.  Rockets rise from barges on a black lake and burst as peonies, chrysanthemums (long trails
and a mid-life colour change), willows (heavy drag, long drooping gold trails), palms (a few thick fronds) and tilted
rings.  Behind them a two-layer city skyline with lit windows and blinking tower beacons; smoke haze drifts on the
wind and is lit by every burst; the lake mirrors the sky, the skyline and the bursts through moving ripples.

Model:
  * Every spark is CLOSED FORM (no simulation state): launch time, burst centre, velocity, linear drag k and gravity
    g give p(t) = C + V0 A + Vt (t - A), A = (1 - e^-kt) / k, Vt = (wind, -g/k) the terminal velocity.  A host ring
    buffer of 64 shells (launch time, type, origin, hue, size, seed) is uploaded each frame as a small param array;
    one fused generator kernel evaluates active shells x sparks x 4 trail segments (sampled back along the
    ballistic path) plus each head point, and writes a second, rippled y-mirrored copy for the lake reflection.
  * splat3d lines (trails) + points (heads, burst flashes, barge lights) at a 0.5-scale accumulator, resolved with
    bloom straight over the background.
  * Background kernel (internal res, upsampled): sky gradient + city glow, near/far skyline planes (anti-aliased
    silhouettes, hashed lit windows with distance LOD, beacons), fbm smoke on the wind, and up to 16 shell lights
    lighting the haze; below the horizon a Schlick-Fresnel water surface reflects the same sky through analytic
    ripple normals (band-limited by pixel footprint).

Macros: energy -> auto-launch rate (integrated counter), shell size and spark count, burst-type mix (more
chrysanthemums / palms / rings), haze glow, bloom and camera sway.  pulse -> a rising edge bursts a shell right on
the beat (spawning, never a jump) plus a soft haze flash.  speed scales shell time and every rate; drift_speed moves
the shell hues through the palette (palette_period = hue spread between shells); intensity is exposure.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTES
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.splat3d import Splat3D, look_at
from stream_service.plugins._lib.upsample import internal_size, present

N_SLOTS = 64                 # shell ring buffer
MAX_SPARKS = 2048            # per shell
ASC_N = 40                   # sparks drawn for a rising rocket (comet + shed glitter)
NF = 32                      # floats per shell record
NSPK = N_SLOTS * MAX_SPARKS
NSEG = 4 * NSPK
N_LIGHTS = 16
N_FLASH = 128
TWO_PI = 2.0 * math.pi

# types along the shell_mix axis: peony .. chrysanthemum .. willow .. palm .. ring
TYPE_POS = np.array([0.0, 0.28, 0.52, 0.76, 1.0])
TYPE_EBONUS = np.array([0.0, 0.7, 0.0, 0.9, 0.6])      # energy favours chrysanthemums, palms and rings
#                      n     k     g    life  trail  R    gain
TYPE_DEF = np.array([[1300, 1.30, 1.60, 1.7, 0.07, 3.0, 1.00],    # peony: crisp sphere, short streaks
                     [1400, 1.30, 1.50, 2.1, 0.32, 3.1, 0.80],    # chrysanthemum: long trails, colour change
                     [1000, 2.00, 2.30, 3.6, 0.85, 2.8, 0.80],    # willow: heavy drag, long drooping gold
                     [520, 1.00, 1.40, 2.4, 0.45, 3.2, 1.10],     # palm: a few thick fronds
                     [420, 1.30, 1.40, 1.8, 0.10, 3.1, 1.30]])    # ring: a tilted circle
BARGES = np.array([[-7.5, 0.0, 2.5], [-3.5, 0.0, 0.5], [0.5, 0.0, 3.5], [4.5, 0.0, 1.0], [8.0, 0.0, 2.8]])
MIRROR_RATES = (1.9, 0.5, 2.9, 1.4, 2.2)
WAVE_RATES = (1.1, 1.6, 2.2, 2.9)

_SRC = HELPERS + r"""
#define FW_NF 32
#define FW_ASC 40

__device__ __forceinline__ float3 fw3(float x, float y, float z) { return make_float3(x, y, z); }
__device__ __forceinline__ float3 fw_add(float3 a, float3 b) { return make_float3(a.x + b.x, a.y + b.y, a.z + b.z); }
__device__ __forceinline__ float3 fw_mul(float3 a, float s) { return make_float3(a.x * s, a.y * s, a.z * s); }
__device__ __forceinline__ float3 fw_mix(float3 a, float3 b, float t) {
    return make_float3(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t);
}
__device__ __forceinline__ float fw_dot(float3 a, float3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
__device__ __forceinline__ float3 fw_norm(float3 a) { return fw_mul(a, rsqrtf(fmaxf(fw_dot(a, a), 1e-12f))); }
__device__ __forceinline__ float fw_lin(float c) {
    c = clamp01(c);
    return c <= 0.04045f ? c * (1.f / 12.92f) : __powf((c + 0.055f) * (1.f / 1.055f), 2.4f);
}
__device__ __forceinline__ float fw_srgb(float c) {
    c = clamp01(c);
    return c <= 0.0031308f ? c * 12.92f : 1.055f * __powf(c, 1.f / 2.4f) - 0.055f;
}
__device__ __forceinline__ float3 fw_pal(const float* P, float t) {   // cosine palette -> linear rgb
    const float k = 6.2831853f;
    return make_float3(fw_lin(P[0] + P[3] * cosf(k * (P[6] * t + P[9]))),
                       fw_lin(P[1] + P[4] * cosf(k * (P[7] * t + P[10]))),
                       fw_lin(P[2] + P[5] * cosf(k * (P[8] * t + P[11]))));
}
__device__ __forceinline__ float3 fw_sphere(float u, float v) {
    const float z = 2.f * u - 1.f, r = sqrtf(fmaxf(1.f - z * z, 0.f)), ph = 6.2831853f * v;
    return make_float3(r * __cosf(ph), z, r * __sinf(ph));
}
// smooth twinkle 0..1 (value noise in shell time, cubed for sparkly peaks; no per-frame white noise)
__device__ __forceinline__ float fw_tw(unsigned int sd, float x) {
    const float fl = floorf(x);
    const int i = (int)fl;
    float f = x - fl;
    f = f * f * (3.f - 2.f * f);
    const float a = hash1(sd, (unsigned int)i, 77u), b = hash1(sd, (unsigned int)(i + 1), 77u);
    const float v = a + (b - a) * f;
    return v * v * v;
}
// mirror a world point in the lake (y = 0) and wobble it by the ripples where its reflection meets the water
__device__ float3 fw_mirror(float3 p, const float* __restrict__ G) {
    float3 q = make_float3(p.x, -p.y, p.z);
    const float ex = G[0], ey = fmaxf(G[1], 0.05f), ez = G[2];
    const float hy = fmaxf(p.y, 0.f);
    const float t = ey / (ey + hy + 1e-3f);
    const float wx = ex + t * (q.x - ex), wz = ez + t * (q.z - ez);
    const float r1 = sinf(wz * 2.3f + G[25] + 1.7f * sinf(wx * 0.7f + G[26])) + 0.6f * sinf(wz * 5.3f - G[27] + wx * 1.1f);
    const float r2 = sinf(wz * 3.1f - G[28] + wx * 0.45f) + 0.5f * sinf(wz * 7.7f + G[29]);
    q.x += G[8] * 0.035f * hy * r1;
    q.y *= 1.f + G[8] * 0.07f * r2;
    return q;
}

// G: 0-2 eye | 3 gravity x | 4 drag x | 5 trail x | 6 glitter | 8 ripple amp | 9,10 wind x,z | 11 period
//    12 drift | 13-24 palette | 25-29 mirror ripple phases
// S (per shell, FW_NF floats): 0 tau (since burst) | 1 age (since launch) | 2-4 C | 5-7 Vc | 8 type | 9 hue
//    10 v0 | 11 k | 12 g | 13 life | 14 trail | 15 gain | 16 seed | 17 n | 18-20 U | 21-23 V | 24-26 O
//    27-29 Vl | 30 gl | 31 fronds
extern "C" __global__ void fw_gen(const float* __restrict__ S, const int* __restrict__ off,
                                  const float* __restrict__ G, const int nsh, const int ntot, const int nseg_max,
                                  const int nspk_max, float4* __restrict__ P0, float4* __restrict__ P1,
                                  float* __restrict__ RGB, float4* __restrict__ HP, float* __restrict__ HC)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= ntot) return;
    int lo = 0, hi = nsh - 1;                                     // shell s: off[s] <= i < off[s + 1]
    while (lo < hi) { const int mid = (lo + hi + 1) >> 1; if (off[mid] <= i) lo = mid; else hi = mid - 1; }
    const int s = lo, j = i - off[s];
    const float* sh = S + s * FW_NF;
    const float tau = sh[0], age = sh[1];
    const float gain = sh[15];
    const unsigned int seed = (unsigned int)sh[16];
    const unsigned int sd = hash_u(seed * 0x9E3779B1u + (unsigned int)j * 0x85EBCA77u + 1u);
    const float h0 = hash1(sd, 1u), h1 = hash1(sd, 2u), h2 = hash1(sd, 3u);
    const float h3 = hash1(sd, 4u), h4 = hash1(sd, 5u), h5 = hash1(sd, 6u);
    float3 pos[5];
    float w[5];
    float3 col;
    float hb;
    if (tau < 0.f) {
        // ---- ascent: the rocket comet (j == 0) and a tail of glittering shed sparks
        const float3 O = fw3(sh[24], sh[25], sh[26]), Vl = fw3(sh[27], sh[28], sh[29]);
        const float gl = sh[30];
        if (j == 0) {
            const float tw[5] = {1.f, 0.65f, 0.354f, 0.125f, 0.f};
            #pragma unroll
            for (int m = 0; m < 5; ++m) {
                const float ta = fmaxf(age - 0.04f * m, 0.f);
                pos[m] = fw3(O.x + Vl.x * ta, O.y + Vl.y * ta - 0.5f * gl * ta * ta, O.z + Vl.z * ta);
                w[m] = age - 0.04f * m > 0.f ? tw[m] : 0.f;
            }
            col = fw_mul(fw3(1.f, 0.62f, 0.3f), 1.1f * gain);
            hb = 1.6f;
        } else {
            const float lag = 0.03f + 0.6f * __powf(h0, 1.4f);
            const float3 dj = fw_sphere(h1, h2);
            #pragma unroll
            for (int m = 0; m < 2; ++m) {
                const float lg = lag + 0.03f * m, ta = age - lg, tc = fmaxf(ta, 0.f);
                pos[m] = fw3(O.x + Vl.x * tc + dj.x * 0.35f * lg,
                             O.y + Vl.y * tc - 0.5f * gl * tc * tc + dj.y * 0.35f * lg - 1.2f * lg * lg,
                             O.z + Vl.z * tc + dj.z * 0.35f * lg);
                w[m] = ta > 0.f ? __expf(-lg * 4.5f) * (0.25f + 1.5f * fw_tw(sd, age * 15.f)) : 0.f;
            }
            pos[2] = pos[1]; pos[3] = pos[1]; pos[4] = pos[1];
            w[2] = 0.f; w[3] = 0.f; w[4] = 0.f;
            col = fw_mul(fw3(1.f, 0.5f, 0.16f), 0.8f * gain);
            hb = 0.35f;
        }
    } else {
        // ---- burst: closed-form ballistic sparks with linear drag, gravity and wind
        const int type = (int)(sh[8] + 0.5f);
        const float3 C = fw3(sh[2], sh[3], sh[4]), Vc = fw3(sh[5], sh[6], sh[7]);
        const float v0 = sh[10], life = sh[13], L = sh[14] * G[5];
        float3 dir;
        float spd;
        if (type == 3) {                                          // palm: fronds of sparks at spread speeds
            const int nf = max((int)sh[31], 3);
            const int f = j % nf;
            float3 fd = fw_sphere(hash1(seed, (unsigned int)f, 101u), hash1(seed, (unsigned int)f, 102u));
            fd.y = fabsf(fd.y) * 0.8f + 0.3f;
            dir = fw_norm(fw_add(fw_norm(fd), fw_mul(fw_sphere(h0, h1), 0.06f)));
            spd = 0.3f + 0.7f * h2;
        } else if (type == 4) {                                   // ring in the shell's tilted (U, V) plane
            const float n = fmaxf(sh[17], 1.f);
            const float ph = 6.2831853f * ((float)j + 0.35f * h0) / n;
            const float cph = __cosf(ph), sph = __sinf(ph);
            dir = fw3(cph * sh[18] + sph * sh[21], cph * sh[19] + sph * sh[22], cph * sh[20] + sph * sh[23]);
            dir = fw_add(dir, fw_mul(fw_sphere(h1, h2), 0.025f));
            spd = 1.f - 0.04f * h3;
        } else {
            dir = fw_sphere(h0, h1);
            spd = type == 2 ? 0.7f + 0.3f * h2 : 1.f - 0.07f * h2;
        }
        const float3 V0 = fw_add(Vc, fw_mul(dir, v0 * spd));
        const float k = fmaxf(sh[11] * G[4] * (0.9f + 0.2f * h4), 0.05f), ik = 1.f / k;
        const float3 Vt = fw3(G[9], -sh[12] * G[3] * ik, G[10]);
        const float lj = life * (0.75f + 0.5f * h3);
        const float tw[5] = {1.f, 0.688f, 0.406f, 0.165f, 0.f};
        #pragma unroll
        for (int m = 0; m < 5; ++m) {
            const float tm = fmaxf(tau - L * 0.25f * m, 0.f);
            const float A = (1.f - __expf(-k * tm)) * ik, B = tm - A;
            pos[m] = fw3(C.x + V0.x * A + Vt.x * B, C.y + V0.y * A + Vt.y * B, C.z + V0.z * A + Vt.z * B);
            float b = smooth(0.f, 0.035f, tm) * (1.f - smooth(0.6f * lj, lj, tm));
            const float gl = G[6] * smooth(0.35f * lj, 0.75f * lj, tm);
            b *= 1.f + gl * (1.6f * fw_tw(sd, tm * 13.f + 40.f * h5) - 0.75f);
            w[m] = b * tw[m];
        }
        float hue = sh[9] + 0.05f * (h5 - 0.5f);
        if (type == 1) hue += 0.33f * smooth(0.3f * lj, 0.55f * lj, tau);
        col = fw_pal(G + 13, hue * G[11] + G[12]);
        if (type == 2) col = fw_mix(col, fw3(1.f, 0.42f, 0.12f), 0.55f + 0.35f * smooth(0.f, lj, tau));
        col = fw_mix(col, fw3(1.f, 0.95f, 0.85f), 0.85f * __expf(-tau * 8.f));
        col = fw_mul(col, gain);
        hb = 1.f;
    }
    // ---- write 4 trail segments (older -> newer), their lake reflections, the head and its reflection
    float3 mp[5];
    #pragma unroll
    for (int m = 0; m < 5; ++m) mp[m] = fw_mirror(pos[m], G);
    #pragma unroll
    for (int m = 0; m < 4; ++m) {
        const int q = 4 * i + m;
        const bool live = w[m] + w[m + 1] > 1e-5f;               // dead segments collapse to a point (culled)
        const float3 a = live ? pos[m + 1] : pos[m], ma = live ? mp[m + 1] : mp[m];
        P0[q] = make_float4(a.x, a.y, a.z, w[m + 1]);
        P1[q] = make_float4(pos[m].x, pos[m].y, pos[m].z, w[m]);
        P0[nseg_max + q] = make_float4(ma.x, ma.y, ma.z, w[m + 1]);
        P1[nseg_max + q] = make_float4(mp[m].x, mp[m].y, mp[m].z, w[m]);
        RGB[3 * q] = col.x; RGB[3 * q + 1] = col.y; RGB[3 * q + 2] = col.z;
    }
    const float wh = w[0] * hb;
    HP[i] = make_float4(pos[0].x, pos[0].y, pos[0].z, wh);
    HP[nspk_max + i] = make_float4(mp[0].x, mp[0].y, mp[0].z, wh);
    HC[3 * i] = 0.65f * col.x + 0.35f * gain; HC[3 * i + 1] = 0.65f * col.y + 0.33f * gain;
    HC[3 * i + 2] = 0.65f * col.z + 0.3f * gain;
}

// ------------------------------------------------------------------------------------------- background
__device__ float3 fw_lights(float3 ro, float3 d, const float* __restrict__ Lt, int nl) {
    float3 acc = fw3(0.f, 0.f, 0.f);
    for (int i = 0; i < nl; ++i) {
        const float* l = Lt + 8 * i;
        const float3 v = fw3(l[0] - ro.x, l[1] - ro.y, l[2] - ro.z);
        const float c = fw_dot(d, v) * rsqrtf(fmaxf(fw_dot(v, v), 1e-6f));
        const float a2 = fmaxf(2.f * (1.f - c), 0.f);              // ~ angle^2
        const float is2 = 1.f / fmaxf(l[7] * l[7], 1e-5f);
        const float k = l[6] * (__expf(-a2 * is2) + 0.22f * __expf(-a2 * is2 * 0.1f));
        acc = fw_add(acc, fw_mul(fw3(l[3], l[4], l[5]), k));
    }
    return acc;
}
__device__ __forceinline__ float fw_bh(int ci, int layer, unsigned int seed) {     // building height
    const float a = hash1((unsigned int)ci, 11u + 7u * layer, seed), b = hash1((unsigned int)ci, 23u + 7u * layer, seed);
    float h = layer == 0 ? 2.f + 8.5f * a * a : 4.f + 10.f * a * a;
    if (b > 0.88f) h += (layer == 0 ? 9.f : 11.f) * (b - 0.88f) * 8.f;                // towers
    return h;
}
// B: 0-2 zenith | 3-5 horizon | 6-8 city glow | 9 smoke | 10,11 smoke offset | 12 haze gain | 13 lake | 14-17 wave
//    phases | 18 window gain | 19 beacon phase | 20 pixel angle | 21-23 flash | 24 seed | 25 ripple | 26-28 water
__device__ float3 fw_scene(float3 ro, float3 d, float dist0, const float* __restrict__ B,
                           const float* __restrict__ Lt, int nl) {
    const unsigned int seed = (unsigned int)B[24];
    const float3 zen = fw3(B[0], B[1], B[2]), hor = fw3(B[3], B[4], B[5]), city = fw3(B[6], B[7], B[8]);
    float3 c = fw_mix(hor, zen, smooth(-0.02f, 0.55f, d.y));
    c = fw_add(c, fw_mul(city, __expf(-fmaxf(d.y, 0.f) * 14.f)));
    const float3 Ls = fw_lights(ro, d, Lt, nl);
    // skyline: near plane over far plane
    float cov = 0.f;
    float3 bc = fw3(0.f, 0.f, 0.f), em = fw3(0.f, 0.f, 0.f);
    if (d.z > 1e-3f) {
        for (int layer = 0; layer < 2; ++layer) {
            if (cov > 0.999f) break;
            const float Z = layer == 0 ? 75.f : 115.f;
            const float t = (Z - ro.z) / d.z;
            if (t <= 0.f) continue;
            const float x = ro.x + t * d.x, y = ro.y + t * d.y;
            if (y > 34.f || y < -2.f) continue;
            const float ps = fmaxf((t + dist0) * B[20], 1e-3f);
            const float bw = layer == 0 ? 3.f : 4.4f, sh = layer == 0 ? 0.f : 0.37f;
            const float fx = x / bw + sh;
            const float fl = floorf(fx);
            const int ci = (int)fl;
            const float f = fx - fl;
            const float hc = fw_bh(ci, layer, seed), hn = fw_bh(f < 0.5f ? ci - 1 : ci + 1, layer, seed);
            const float de = fminf(f, 1.f - f) * bw;
            const float cc = smooth(hc + 0.5f * ps, hc - 0.5f * ps, y), cn = smooth(hn + 0.5f * ps, hn - 0.5f * ps, y);
            const float eg = smooth(0.f, ps, de);
            const float lc = 0.5f * (cc + cn) * (1.f - eg) + cc * eg;
            if (hc > (layer == 0 ? 11.f : 15.f)) {                  // tower beacon
                const float xc = ((float)ci + 0.5f - sh) * bw;
                const float dx = (x - xc) / ps, dy = (y - hc - 0.4f) / ps;
                const float bl = 0.5f + 0.5f * __sinf(B[19] + 1.7f * (float)(ci & 63));
                const float a = __expf(-(dx * dx + dy * dy) * 0.3f) * bl * bl * bl * (1.f - cov);
                em = fw_add(em, fw_mul(fw3(1.f, 0.06f, 0.03f), 1.4f * a));
            }
            if (lc > 1e-3f) {
                float3 col = fw_add(fw_mul(hor, layer == 0 ? 0.35f : 0.7f), fw_mul(Ls, 0.04f));
                const float lx = f * bw;
                if (lx > 0.35f && lx < bw - 0.35f && y > 0.5f && y < hc - 0.5f) {
                    const float wsx = layer == 0 ? 0.5f : 0.7f, wsy = layer == 0 ? 0.72f : 0.95f;
                    const float gx = (lx - 0.35f) / wsx, gy = y / wsy;
                    const float ix = floorf(gx), iy = floorf(gy);
                    const float ux = gx - ix, uy = gy - iy;
                    const float px = ps / wsx, py = ps / wsy;
                    float mk = (smooth(0.18f - px, 0.18f + px, ux) - smooth(0.82f - px, 0.82f + px, ux)) *
                               (smooth(0.25f - py, 0.25f + py, uy) - smooth(0.75f - py, 0.75f + py, uy));
                    const unsigned int cu = (unsigned int)ci, iu = (unsigned int)(int)ix, ju = (unsigned int)(int)iy;
                    const float frac = 0.12f + 0.45f * hash1(cu, 41u + layer, seed);
                    const float fo = hash1(cu * 977u + ju, 43u, seed);
                    const float lit = hash1(cu * 131u + iu, ju, seed + 5u) < frac * (0.4f + 1.2f * fo) ? 1.f : 0.f;
                    const float lod = smooth(0.25f, 0.6f, fmaxf(px, py));
                    mk = mk * lit * (1.f - lod) + 0.33f * frac * lod;
                    const float wt = hash1(cu, 47u + layer, seed);
                    const float3 wc = wt < 0.55f ? fw3(1.f, 0.62f, 0.3f) : (wt < 0.85f ? fw3(0.9f, 0.85f, 0.75f) : fw3(0.5f, 0.7f, 1.f));
                    const float wb = B[18] * (layer == 0 ? 1.f : 0.6f) * (0.5f + 0.5f * hash1(cu * 131u + iu, ju, seed + 9u));
                    col = fw_add(col, fw_mul(wc, mk * wb));
                }
                bc = fw_add(bc, fw_mul(col, lc * (1.f - cov)));
                cov += lc * (1.f - cov);
            }
        }
    }
    c = fw_add(fw_add(fw_mul(c, 1.f - cov), bc), em);
    // smoke on the wind, lit by the shells
    float den = 0.f;
    if (d.z > 1e-3f) {
        const float t = (40.f - ro.z) / d.z;
        if (t > 0.f) {
            const float qx = ro.x + t * d.x, qy = ro.y + t * d.y;
            const float n = fbm(qx * 0.06f + B[10], qy * 0.09f + B[11], seed + 3u);
            den = smooth(0.4f, 0.78f, n) * smooth(1.f, 8.f, qy) * (1.f - smooth(30.f, 45.f, qy)) * B[9];
        }
    }
    c = fw_mix(c, fw_add(fw_mul(hor, 1.8f), fw_mul(city, 0.5f)), 0.3f * den);
    c = fw_add(c, fw_mul(Ls, B[12] * (0.18f + 2.4f * den)));
    c = fw_add(c, fw_mul(fw3(B[21], B[22], B[23]), 0.3f + den));
    return c;
}

extern "C" __global__ void fw_bg(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ cam,
                                 const float* __restrict__ B, const float* __restrict__ Lt, const int nl)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float th = cam[12];
    const float sx = (2.f * (x + 0.5f) / W - 1.f) * th * ((float)W / (float)H);
    const float sy = (1.f - 2.f * (y + 0.5f) / H) * th;
    const float3 ro = fw3(cam[0], cam[1], cam[2]);
    const float3 d = fw_norm(fw3(cam[3] + sx * cam[6] + sy * cam[9], cam[4] + sx * cam[7] + sy * cam[10],
                                 cam[5] + sx * cam[8] + sy * cam[11]));
    float3 c;
    if (d.y < -1e-4f) {
        // lake: analytic ripple normals, band-limited by the pixel footprint, Schlick Fresnel reflection
        const float t = -ro.y / d.y;
        const float px = ro.x + t * d.x, pz = ro.z + t * d.z;
        const float fp = t * B[20] / fmaxf(-d.y, 0.02f);           // footprint along the view on the water
        const float r = B[25];
        float gx = 0.f, gz = 0.f, a, cs;
        a = 0.020f * __expf(-0.5f * (2.1f * fp) * (2.1f * fp)); cs = cosf(0.35f * px + 2.1f * pz + B[14]);
        gx += a * 0.35f * cs; gz += a * 2.1f * cs;
        a = 0.012f * __expf(-0.5f * (3.8f * fp) * (3.8f * fp)); cs = cosf(-0.8f * px + 3.7f * pz + B[15]);
        gx -= a * 0.8f * cs; gz += a * 3.7f * cs;
        a = 0.007f * __expf(-0.5f * (6.0f * fp) * (6.0f * fp)); cs = cosf(1.3f * px + 5.9f * pz + B[16]);
        gx += a * 1.3f * cs; gz += a * 5.9f * cs;
        a = 0.004f * __expf(-0.5f * (9.3f * fp) * (9.3f * fp)); cs = cosf(-0.4f * px + 9.3f * pz + B[17]);
        gx -= a * 0.4f * cs; gz += a * 9.3f * cs;
        const float3 n = fw_norm(fw3(-gx * r, 1.f, -gz * r));
        const float dn = fw_dot(d, n);
        float3 rr = fw_add(d, fw_mul(n, -2.f * dn));
        rr.y = fmaxf(rr.y, 0.004f);
        rr = fw_norm(rr);
        const float3 rc = fw_scene(fw3(px, 0.f, pz), rr, t, B, Lt, nl);
        const float om = 1.f - clamp01(-dn);
        const float fr = 0.02f + 0.98f * om * om * om * om * om;
        c = fw_add(fw3(B[26], B[27], B[28]), fw_mul(rc, B[13] * (0.12f + 0.88f * fr)));
    } else {
        c = fw_scene(ro, d, 0.f, B, Lt, nl);
    }
    const float dz = hash1((unsigned int)x, (unsigned int)y, 91u) - 0.5f;
    out[y * W + x] = make_uchar4((unsigned char)fminf(fmaxf(fw_srgb(c.x) * 255.f + dz + 0.5f, 0.f), 255.f),
                                 (unsigned char)fminf(fmaxf(fw_srgb(c.y) * 255.f + dz + 0.5f, 0.f), 255.f),
                                 (unsigned char)fminf(fmaxf(fw_srgb(c.z) * 255.f + dz + 0.5f, 0.f), 255.f), 255);
}
"""


def _srgb_to_lin(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _pal_lin(coeffs: np.ndarray, t) -> np.ndarray:
    """Cosine palette on the host for an array of indices -> (n, 3) linear rgb."""
    t = np.atleast_1d(np.asarray(t, np.float64))[:, None]
    a, b, c, d = coeffs[0:3], coeffs[3:6], coeffs[6:9], coeffs[9:12]
    return _srgb_to_lin(a + b * np.cos(TWO_PI * (c * t + d)))


def _sstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


class FireworkShow(Source):
    name = "firework_show"
    label = "Firework Show"
    description = ("Beat-launched 3D fireworks (peonies, chrysanthemums, willows, palms, rings) over a city skyline, "
                   "lighting the smoke and mirrored in a rippling lake.")
    tags = {"family": "particles", "dims": "3d", "toolkit": "splat3d", "technique": ["particles", "points"],
            "moods": ["euphoric", "playful", "bright"], "style": "wild", "energy": [0.4, 1.0]}
    params = {
        "shell_mix": num(0.0, 1.0, 0.3, "Shell mix (peony → willow → ring)", tier="regional", basic=True),
        "burst_size": num(0.5, 1.6, 1.0, "Burst size", unit="×", tier="regional", basic=True),
        "droop": num(0.0, 1.0, 0.45, "Droop (gravity + drag)", tier="regional"),
        "smoke": num(0.0, 1.0, 0.5, "Smoke haze", tier="regional"),
        "lake_reflection": num(0.0, 1.0, 0.75, "Lake reflection", tier="regional"),
        "launch_rate": num(0.2, 3.0, 1.0, "Auto-launch rate", unit="×", tier="regional"),
        "trails": num(0.2, 2.5, 1.0, "Trail length", unit="×", tier="regional"),
        "glitter": num(0.0, 1.0, 0.5, "Glitter", tier="regional"),
        **standard_scene_params(palette="rainbow", render_scale=0.5, period=1.0, drift=0.02),
    }

    # ------------------------------------------------------------------ setup
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        f32 = cp.float32
        seed = int(params["seed"])
        self.rng = np.random.default_rng(seed)
        self.kseed = float((seed * 2654435761) % (1 << 22))
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.sp = Splat3D(ctx, self.w, self.h, max_points=2 * NSPK, max_lines=1 << 14, pool=3 << 20)
        mod = raw_module(_SRC, ("--std=c++14",))
        self.k_gen = mod.get_function("fw_gen")
        self.k_bg = mod.get_function("fw_bg")
        # device buffers (allocated once)
        self.P0 = cp.zeros((2 * NSEG, 4), f32)
        self.P1 = cp.zeros((2 * NSEG, 4), f32)
        self.P0m, self.P0r = self.P0[:NSEG], self.P0[NSEG:]
        self.P1m, self.P1r = self.P1[:NSEG], self.P1[NSEG:]
        self.RGB = cp.zeros((NSEG, 3), f32)
        self.HP = cp.zeros((2 * NSPK, 4), f32)
        self.HPm, self.HPr = self.HP[:NSPK], self.HP[NSPK:]
        self.HC = cp.zeros((NSPK, 3), f32)
        self.Sd = cp.zeros((N_SLOTS, NF), f32)
        self.offd = cp.zeros(N_SLOTS + 8, cp.int32)
        self.Gd = cp.zeros(40, f32)
        self.camd = cp.zeros(16, f32)
        self.Bd = cp.zeros(32, f32)
        self.Ltd = cp.zeros((N_LIGHTS, 8), f32)
        self.FP = cp.zeros((N_FLASH, 4), f32)
        self.FC = cp.zeros((N_FLASH, 3), f32)
        self.bg = cp.zeros((self.h, self.w, 4), cp.uint8)
        # host staging
        self.S_h = np.zeros((N_SLOTS, NF), np.float32)
        self.off_h = np.zeros(N_SLOTS + 8, np.int32)
        self.G_h = np.zeros(40, np.float32)
        self.cam_h = np.zeros(16, np.float32)
        self.B_h = np.zeros(32, np.float32)
        self.Lt_h = np.zeros((N_LIGHTS, 8), np.float32)
        self.FP_h = np.zeros((N_FLASH, 4), np.float32)
        self.FC_h = np.zeros((N_FLASH, 3), np.float32)
        # barge deck + mast lights and their reflections (static)
        bl = []
        for b in BARGES:
            for dx in (-0.7, -0.25, 0.25, 0.7):
                bl.append((b[0] + dx, 0.12, b[2], 1.0))
            bl.append((b[0], 0.75, b[2], 0.8))
        bl = np.array(bl, np.float32)
        br = bl.copy()
        br[:, 1] *= -1.0
        br[:, 3] *= 0.35
        self.BAR = cp.asarray(np.concatenate([bl, br]))
        # shell ring buffer (host, float64): column 0 = launch clock, 1 = ascent duration, then the S layout
        self.S = np.zeros((N_SLOTS, NF))
        self.valid = np.zeros(N_SLOTS, bool)
        self.next = 0
        self.clock = 0.0
        self.rt = 0.0
        self.last_trig = -1.0
        self.prev_pulse = 0.0
        self.flash = 0.0
        self.flash_col = 0.0
        self.auto = 0.99
        self.smoke_acc = 4.0
        self.drift = 0.0
        self.e_s = None
        self.sway = float(self.rng.uniform(0.0, TWO_PI))
        self.bob = 0.0
        self.wind_ph = float(self.rng.uniform(0.0, TWO_PI))
        self.smoke_off = np.array([self.rng.uniform(0, 50), self.rng.uniform(0, 50)])
        self.mph = np.zeros(len(MIRROR_RATES))
        self.wph = np.zeros(len(WAVE_RATES))
        self.beacon = 0.0
        # prewarm: run the launch schedule for a few seconds so the show is already going
        e0 = float(params["energy"])
        spd0 = max(float(params["speed"]), 0.2)
        for _ in range(100):
            self.clock += 0.05 * spd0
            self._auto_launch(params, e0, 0.05 * spd0)
        self._launch(params, e0, immediate=True, age_after_burst=0.5)

    # ------------------------------------------------------------------ launches
    def _auto_launch(self, p, e: float, dts: float) -> None:
        self.auto += float(p["launch_rate"]) * (0.25 + 1.75 * e ** 1.3) * dts
        self.auto = min(self.auto, 4.0)
        while self.auto >= 1.0:
            self.auto -= 1.0
            self._launch(p, e, immediate=False)

    def _launch(self, p, e: float, immediate: bool, scale: float = 1.0, age_after_burst: float = 0.0,
                mirror_of: int = -1) -> int:
        rng = self.rng
        m = float(p["shell_mix"])
        wts = (np.exp(-((m - TYPE_POS) / 0.2) ** 2) + 0.06) * (1.0 + TYPE_EBONUS * e)
        typ = int(rng.choice(5, p=wts / wts.sum()))
        if mirror_of >= 0:
            typ = int(self.S[mirror_of, 8])
        n0, k, g, life, trail, R0, gain = TYPE_DEF[typ]
        size = float(p["burst_size"]) * (0.8 + 0.35 * e) * float(rng.uniform(0.85, 1.15)) * scale
        R = R0 * size
        n = int(np.clip(n0 * (0.6 + 0.6 * e) * (0.7 + 0.3 * size), 48, MAX_SPARKS))
        gain = gain * math.sqrt(1300.0 / n) * float(rng.uniform(0.85, 1.15))
        C = np.array([rng.uniform(-9.0, 9.0), rng.uniform(4.5, 8.0), rng.uniform(0.0, 6.0)])
        if mirror_of >= 0:                                 # salvo: a matching shell mirrored across the lake
            C = self.S[mirror_of, 2:5].copy()
            C[0] = -C[0] if abs(C[0]) > 2.0 else C[0] + (6.0 if C[0] <= 0 else -6.0)
        O = BARGES[int(rng.integers(len(BARGES)))] + np.array([rng.uniform(-0.4, 0.4), 0.05, rng.uniform(-0.2, 0.2)])
        tb = float(rng.uniform(1.1, 1.6)) * math.sqrt(C[1] / 6.0)
        gl = 2.0 * (C[1] - O[1]) / (tb * tb)
        Vl = np.array([(C[0] - O[0]) / tb, gl * tb, (C[2] - O[2]) / tb])
        Vc = np.array([0.35 * Vl[0], 0.0, 0.35 * Vl[2]])
        nrm = np.array([rng.normal() * 0.8, rng.normal() * 0.8, -1.0])
        nrm /= np.linalg.norm(nrm)
        U = np.cross(nrm, (0.0, 1.0, 0.0))
        U /= max(np.linalg.norm(U), 1e-6)
        V = np.cross(nrm, U)
        hue = float(rng.uniform(0.0, 0.6))
        if mirror_of >= 0:
            hue = float(self.S[mirror_of, 9])
        slot = self.next % N_SLOTS
        self.next += 1
        r = self.S[slot]
        r[:] = 0.0
        r[0] = self.clock - ((tb + age_after_burst) if immediate else 0.0)
        r[1] = tb
        r[2:5] = C
        r[5:8] = Vc
        r[8] = typ
        r[9] = hue
        r[10] = R * k
        r[11] = k
        r[12] = g
        r[13] = life
        r[14] = trail
        r[15] = gain
        r[16] = float(rng.integers(1, 1 << 22))
        r[17] = n
        r[18:21] = U
        r[21:24] = V
        r[24:27] = O
        r[27:30] = Vl
        r[30] = gl
        r[31] = float(rng.integers(6, 11))
        self.valid[slot] = True
        self.smoke_acc += 1.0
        return slot

    # ------------------------------------------------------------------ frame
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        a = 1.0 - math.exp(-dt / 0.4) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s
        sdt = spd * dt
        lake = float(p["lake_reflection"])

        # ---- clocks (host integrated; phases wrapped before upload)
        self.clock += sdt
        self.rt += dt
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * sdt, 20.0)
        self.sway += sdt * (0.05 + 0.07 * es)
        self.bob += sdt * (0.11 + 0.05 * es)
        self.wind_ph += sdt * 0.05
        wind_x = 0.12 + 0.25 * math.sin(self.wind_ph)
        wind_z = 0.06 * math.sin(0.7 * self.wind_ph + 1.0)
        self.smoke_off += np.array([wind_x * 0.06, 0.004]) * sdt
        self.mph = np.fmod(self.mph + np.array(MIRROR_RATES) * sdt * (0.7 + 0.3 * es), TWO_PI)
        self.wph = np.fmod(self.wph + np.array(WAVE_RATES) * sdt * (0.7 + 0.3 * es), TWO_PI)
        self.beacon = math.fmod(self.beacon + 2.4 * sdt, TWO_PI)
        self.smoke_acc *= math.exp(-dt / 25.0)
        self.flash *= math.exp(-dt / 0.3) if dt > 0 else 1.0

        # ---- launches: integrated auto counter + a burst right on each beat (rising edge, 120 ms refractory)
        self._auto_launch(p, es, sdt)
        if pulse > 0.2 and pulse - self.prev_pulse > 0.15 and self.rt - self.last_trig > 0.12:
            self.last_trig = self.rt
            slot = self._launch(p, es, immediate=True, scale=0.75 + 0.35 * pulse)
            if es > 0.65 and self.rng.random() < (es - 0.65) * 1.6:
                self._launch(p, es, immediate=True, scale=0.75 + 0.35 * pulse, mirror_of=slot)
            self.flash = max(self.flash, pulse)
            self.flash_col = float(self.S[slot, 9])
        self.prev_pulse = pulse

        # ---- palette
        sess = self.session_palette()
        coeffs = np.asarray(PALETTES[p["palette"]] if sess is None else sess, np.float64).reshape(-1)
        period = float(p["palette_period"])

        # ---- camera: slow sway and bob across the harbour (amplitude from energy)
        amp = 0.8 + 1.6 * es
        eye = (amp * math.sin(self.sway), 1.0 + 0.12 * math.sin(self.bob) + 0.15 * es,
               -20.0 + 0.8 * math.sin(0.7 * self.sway))
        cam = look_at(eye, (0.3 * eye[0], 2.3, 0.0), fov_deg=55.0)

        # ---- live shells -> packed param array + offsets
        dg = 0.45 + 1.3 * float(p["droop"])
        dk = 0.8 + 0.45 * float(p["droop"])
        trails = float(p["trails"])
        S = self.S
        age = self.clock - S[:, 0]
        tau = age - S[:, 1]
        alive = self.valid & (age >= 0.0) & (tau < S[:, 13] * 1.25 + S[:, 14] * trails + 0.3)
        self.valid[(age >= 0.0) & (tau > 0.0) & ~alive] = False
        idx = np.flatnonzero(alive)
        na = len(idx)
        ntot = 0
        nl = 0
        nf = 0
        if na:
            Sa = S[idx]
            ta, aa = tau[idx], age[idx]
            self.S_h[:na] = Sa
            self.S_h[:na, 0] = ta
            self.S_h[:na, 1] = aa
            counts = np.where(ta < 0.0, ASC_N, Sa[:, 17]).astype(np.int64)
            self.off_h[0] = 0
            self.off_h[1:na + 1] = np.cumsum(counts)
            ntot = int(self.off_h[na])

            # haze lights: burst centre of mass (same closed form) or the rising rocket
            asc = ta < 0.0
            k = Sa[:, 11] * dk
            g = Sa[:, 12] * dg
            tt = np.maximum(ta, 0.0)
            A = (1.0 - np.exp(-k * tt)) / k
            Bt = tt - A
            Vt = np.stack([np.full(na, wind_x), -g / k, np.full(na, wind_z)], axis=1)
            pb = Sa[:, 2:5] + Sa[:, 5:8] * A[:, None] + Vt * Bt[:, None]
            pa = Sa[:, 24:27] + Sa[:, 27:30] * aa[:, None]
            pa[:, 1] -= 0.5 * Sa[:, 30] * aa * aa
            pos = np.where(asc[:, None], pa, pb)
            life = Sa[:, 13]
            haze = (0.6 + 0.4 * es) * (1.0 + 1.2 * self.flash)
            Ib = Sa[:, 15] * (0.55 * np.exp(-tt / 0.22) + 0.14 * (1.0 - _sstep(0.3 * life, 1.1 * life, tt)))
            I = np.where(asc, 0.03, Ib) * haze
            colr = _pal_lin(coeffs, Sa[:, 9] * period + self.drift)
            colr = np.where(asc[:, None], np.array([1.0, 0.55, 0.25]), colr)
            rad = np.where(asc, 0.6, Sa[:, 10] * A + 0.4)
            dist = np.linalg.norm(pos - np.asarray(eye), axis=1)
            sig = np.clip(1.4 * rad / np.maximum(dist, 1.0) + 0.02, 0.03, 0.5)
            top = np.argsort(-I)[:N_LIGHTS]
            nl = len(top)
            self.Lt_h[:nl, 0:3] = pos[top]
            self.Lt_h[:nl, 3:6] = colr[top]
            self.Lt_h[:nl, 6] = I[top]
            self.Lt_h[:nl, 7] = sig[top]

            # burst flashes (white-hot cores) and mortar flashes, plus their reflections
            fb = np.flatnonzero((ta >= 0.0) & (ta < 0.35))
            fm = np.flatnonzero(asc & (aa < 0.3))
            fl_pos = np.concatenate([Sa[fb, 2:5], Sa[fm, 24:27] + np.array([0.0, 0.15, 0.0])])
            fl_w = np.concatenate([3.0 * Sa[fb, 15] * np.exp(-ta[fb] / 0.07), 1.2 * np.exp(-aa[fm] / 0.07)])
            fl_c = np.concatenate([0.3 * colr[fb] + 0.7 * np.array([1.0, 0.95, 0.85]),
                                   np.tile(np.array([1.0, 0.6, 0.3]), (len(fm), 1))])
            nf1 = min(len(fl_w), N_FLASH // 2)
            if nf1:
                self.FP_h[:nf1, 0:3] = fl_pos[:nf1]
                self.FP_h[:nf1, 3] = fl_w[:nf1]
                self.FC_h[:nf1] = fl_c[:nf1]
                self.FP_h[nf1:2 * nf1, 0:3] = fl_pos[:nf1] * np.array([1.0, -1.0, 1.0])
                self.FP_h[nf1:2 * nf1, 3] = fl_w[:nf1] * 0.35 * lake
                self.FC_h[nf1:2 * nf1] = fl_c[:nf1]
                nf = 2 * nf1

        # ---- generator globals
        G = self.G_h
        G[0:3] = eye
        G[3], G[4], G[5], G[6] = dg, dk, trails, float(p["glitter"])
        G[8] = 0.4 + 0.8 * lake
        G[9], G[10] = wind_x, wind_z
        G[11], G[12] = period, self.drift
        G[13:25] = coeffs
        G[25:30] = self.mph
        if ntot:
            self.Sd.set(self.S_h)
            self.offd.set(self.off_h)
            self.Gd.set(G)
            self.k_gen(((ntot + 255) // 256,), (256,),
                       (self.Sd, self.offd, self.Gd, np.int32(na), np.int32(ntot), np.int32(NSEG), np.int32(NSPK),
                        self.P0, self.P1, self.RGB, self.HP, self.HC))

        # ---- background: sky, skyline, smoke, lake (internal res -> out)
        hor_tint = _pal_lin(coeffs, self.drift + 0.5)[0]
        Bh = self.B_h
        Bh[0:3] = (0.0018, 0.0025, 0.007)
        Bh[3:6] = np.array([0.010, 0.011, 0.020]) + 0.006 * hor_tint
        Bh[6:9] = (0.028, 0.014, 0.006)
        smoke_amt = float(p["smoke"]) * (0.35 + 0.65 * min(1.0, self.smoke_acc / 10.0))
        Bh[9] = smoke_amt
        Bh[10:12] = np.fmod(self.smoke_off, 1000.0)
        Bh[12] = 1.0
        Bh[13] = lake
        Bh[14:18] = self.wph
        Bh[18] = 0.32
        Bh[19] = self.beacon
        Bh[20] = 2.0 * cam.tan_half / self.h
        fc = _pal_lin(coeffs, self.flash_col * period + self.drift)[0] if self.flash > 0.01 else np.zeros(3)
        Bh[21:24] = 0.02 * self.flash * fc
        Bh[24] = self.kseed
        Bh[25] = 1.0
        Bh[26:29] = (0.0008, 0.0012, 0.0025)
        self.cam_h[:13] = cam.pack()
        self.camd.set(self.cam_h)
        self.Bd.set(Bh)
        self.Ltd.set(self.Lt_h)
        launch_2d(self.k_bg, self.w, self.h, (self.bg, np.int32(self.w), np.int32(self.h), self.camd, self.Bd,
                                              self.Ltd, np.int32(nl)))
        present(self.ctx, self.bg, out)

        # ---- splats: trails, heads, reflections, flashes, barge lights
        sp = self.sp
        sp.clear()
        Il = 0.95 + 0.3 * e
        refl = 0.4 * lake
        if ntot:
            nseg = 4 * ntot
            sp.lines(self.P0m, self.P1m, n=nseg, cam=cam, rgb=self.RGB, weight_in_w=True, intensity=Il,
                     width_px=1.3, size_range=(0.6, 3.0))
            sp.points(self.HPm, n=ntot, cam=cam, rgb=self.HC, weight_in_w=True, intensity=0.45, size_px=1.8,
                      size_range=(0.6, 3.5))
            if refl > 1e-3:
                sp.lines(self.P0r, self.P1r, n=nseg, cam=cam, rgb=self.RGB, weight_in_w=True, intensity=Il * refl,
                         width_px=1.8, size_range=(0.6, 3.5))
                sp.points(self.HPr, n=ntot, cam=cam, rgb=self.HC, weight_in_w=True, intensity=0.45 * refl,
                          size_px=2.0, size_range=(0.6, 3.5))
        if nf:
            self.FP.set(self.FP_h)
            self.FC.set(self.FC_h)
            sp.points(self.FP, n=nf, cam=cam, rgb=self.FC, weight_in_w=True, intensity=1.0, size_px=7.0,
                      size_range=(1.0, 14.0))
        sp.points(self.BAR, cam=cam, weight_in_w=True, colour=(1.0, 0.82, 0.6), intensity=0.25 * (0.5 + 0.5 * lake),
                  size_px=1.6, size_range=(0.6, 3.0))
        sp.resolve(out, exposure=1.05 * float(p["intensity"]) * (1.0 + 0.08 * pulse), tonemap="aces",
                   saturation=1.15, glow=0.45 + 0.35 * e, glow_radius_px=16.0, glow_wide=0.6, background=out)

    def close(self) -> None:
        if getattr(self, "sp", None) is not None:
            self.sp.close()
        self.sp = None
        self.P0 = self.P1 = self.P0m = self.P0r = self.P1m = self.P1r = self.RGB = None
        self.HP = self.HPm = self.HPr = self.HC = self.Sd = self.offd = self.Gd = None
        self.camd = self.Bd = self.Ltd = self.FP = self.FC = self.bg = self.BAR = None
