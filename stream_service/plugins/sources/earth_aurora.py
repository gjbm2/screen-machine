"""Earth & Aurora: the night side of Earth from low orbit, under green and magenta aurora curtains.

Source: Original (inspired by https://github.com/wwwtyro/glsl-atmosphere and https://www.shadertoy.com/view/XtGGRt)
Author: smfx (original aurora and planet); atmosphere after Rye Terrell (glsl-atmosphere, Unlicense); nimitz 'Auroras' idea only (NC)
License: Unlicense + MIT
license_class: permissive

Written from scratch for this renderer.  The atmosphere follows the single-scattering Rayleigh + Mie scheme
popularised by glsl-atmosphere (Unlicense), with the primary samples bunched around the ray's densest point, and
a closed-form Chapman-function approximation (after C. Schueler's published grazing-incidence formula) for the
optical depth towards the sun instead of a secondary loop.  Only the general *idea* of nimitz's Auroras (NC) is
used: curtains of emissive light; no code was copied.

The look: an analytic unit sphere (no SDF march) seen from orbit at 200-1600 km.  Procedural continents (fbm3 on
the sphere), city-light networks (three LOD-filtered layers of hashed lights + value-noise ridge "roads",
clustered along coasts), a drifting cloud layer that hides the lights and catches their glow, a thin Rayleigh/Mie
limb, a green airglow line and a star field with a faint Milky Way.  Aurora curtains are ribbon volumes along an
auroral oval around a tilted magnetic pole: a per-frame 1D GPU table holds each curtain's centre colatitude
(folds), ray striations, height and colour shift around the oval; the main kernel marches ~24 jittered samples
through the aurora shell and prefilters the thin sheets by the per-step change in distance (no banding).

Macros: energy -> curtain undulation and striation speed, brightness, curtain height, fold depth, the second
curtain, orbit drift and camera sway.  pulse -> on each beat a flare runs along the curtain (travelling
brightness + height + colour kick, spawned on the host-detected rising edge) plus gains on the aurora and the city
glow.  speed scales every rate (orbit, curtains, clouds, sweep); drift rotates the aurora palette; intensity is
output gain.  The dawn terminator sweeps slowly across the limb.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, rot_matrix, wrap
from stream_service.plugins._lib.upsample import internal_size, present

N_TAB = 4096                 # aurora table entries per curtain (around the oval)
MAX_FLARES = 6
EARTH_KM = 6371.0
LAT_CAM = math.radians(52.0)
MAG_TILT = math.radians(12.0)
AUR_BOTTOM = 0.017           # curtain bottom altitude (planet radii, ~108 km)
THETA0 = 0.34                # auroral oval colatitude about the magnetic pole (rad)

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28..30 sun dir | 31..33 moon dir
#  34..36 magnetic axis | 37..39 mag basis a | 40..42 mag basis b | 43..51 earth rot | 52..60 cloud rot
#  61 cloud threshold | 62 city gain | 63 aurora gain | 64 aurora bottom | 65 aurora shell radius | 66 oval theta0
#  67 cloud evolution | 68 sun intensity | 69 airglow | 70 stars | 71 seed | 72 colour base | 73 colour span
#  74 pulse | 75 ground aurora light | 76 curtain width
# Q (table kernel) layout
#  0 N | 1 theta0 | 2 fold amp | 3 fold freq | 4 fold time | 5 ray time | 6 activity time | 7 top time | 8 height
#  9 second curtain strength | 10 seed | 11 flare count | 12..29 flares (phi rad, amp, width) | 30 second offset
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define ATM_TOP 1.025f
#define HR 0.0020f
#define HM 0.0006f
#define BR_R 21.9f
#define BR_G 51.8f
#define BR_B 89.2f
#define BM_S 60.0f

// ------------------------------------------------------------------ aurora table (one thread per oval entry)
extern "C" __global__
void aurora_table(float4* __restrict__ T, const float* __restrict__ Q)
{
    const int N = (int)Q[0];
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= 2 * N) return;
    const int c = i / N, k = i - c * N;
    const float phi = SDF_TAU * (float)k / (float)N;
    const float cs = cosf(phi), sn = sinf(phi);
    const unsigned int sd = (unsigned int)Q[10] + 977u * (unsigned int)c;
    const float co = 3.7f * (float)c;
    // folds: noise sampled on a circle, so the oval closes seamlessly
    const float F = Q[3], Tf = Q[4] + co;
    const float n1 = noise3(f3(cs * F + co, sn * F, Tf), sd);
    const float n2 = noise3(f3(cs * F * 2.3f, sn * F * 2.3f + co, Tf * 1.6f + 11.f), sd + 1u);
    const float n0 = noise3(f3(cs * 1.4f, sn * 1.4f + co, Tf * 0.35f + 3.f), sd + 2u);
    const float theta = Q[1] - (float)c * Q[30] + Q[2] * ((n1 + 0.45f * n2) * (2.f / 1.45f) - 1.f)
                      + 0.035f * (2.f * n0 - 1.f);
    // ray striations scrolling along the curtain
    const float Fr = 85.f, Tr = Q[5] + co;
    const float r1 = noise3(f3(cs * Fr, sn * Fr, Tr), sd + 3u);
    const float r2 = noise3(f3(cs * Fr * 2.6f, sn * Fr * 2.6f, Tr * 1.4f), sd + 4u);
    const float rays = 0.3f + 0.7f * smoothstep(0.2f, 0.85f, 0.62f * r1 + 0.38f * r2);
    // slow activity patches and curtain height
    const float nb = noise3(f3(cs * 4.5f, sn * 4.5f, Q[6] + co), sd + 5u);
    const float act = 0.22f + 0.78f * smoothstep(0.25f, 0.75f, nb);
    const float nt = noise3(f3(cs * 6.f, sn * 6.f, Q[7] + co), sd + 6u);
    // beat flares running along the oval
    float fl = 0.f;
    const int nf = (int)Q[11];
    for (int j = 0; j < nf && j < 6; ++j) {
        float dp = phi - Q[12 + 3 * j];
        dp -= SDF_TAU * floorf(dp * (1.f / SDF_TAU) + 0.5f);
        const float w = Q[14 + 3 * j];
        fl += Q[13 + 3 * j] * expf(-dp * dp / (w * w));
    }
    if (c == 1) fl *= 0.5f;
    const float strength = c == 0 ? 1.f : Q[9];
    T[i] = make_float4(theta, rays * act * strength * (1.f + 2.6f * fl),
                       Q[8] * (0.62f + 0.76f * nt) * (1.f + 0.55f * fl),
                       0.14f * (nb - 0.5f) + 0.12f * fl);
}

__device__ __forceinline__ float4 tab_lookup(const float4* __restrict__ T, int base, int N, float ph01) {
    const float fi = ph01 * (float)N;
    int k0 = (int)floorf(fi);
    const float fr = fi - (float)k0;
    k0 = k0 % N; if (k0 < 0) k0 += N;
    const int k1 = k0 + 1 >= N ? 0 : k0 + 1;
    const float4 a = T[base + k0], b = T[base + k1];
    return make_float4(a.x + (b.x - a.x) * fr, a.y + (b.y - a.y) * fr, a.z + (b.z - a.z) * fr, a.w + (b.w - a.w) * fr);
}
__device__ __forceinline__ float mag_phi01(float3 u, float3 ma, float3 mb) {
    float ph = atan2f(dot(u, mb), dot(u, ma)) * (1.f / SDF_TAU);
    return ph < 0.f ? ph + 1.f : ph;
}
__device__ __forceinline__ float3 aur_colour(float idx, const float* __restrict__ P) {
    float3 c = srgb_to_linear(pal(idx, P + 13));
    const float l = 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z;
    return max(mix(f3(l), c, 1.3f), 0.f);                       // a touch more saturated than the palette
}

// ------------------------------------------------------------------ atmosphere (single scattering)
// Chapman grazing-incidence approximation: optical depth (in units of H * density at h) along a ray from
// radius X+h (in scale heights) with zenith cosine mu.  Huge values mean the ray goes through the planet.
__device__ __forceinline__ float chapman(float X, float hh, float mu) {
    const float c = sqrtf(X + hh);
    if (mu >= 0.f) return c / (c * mu + 1.f) * expf(-hh);
    const float x0 = sqrtf(fmaxf(1.f - mu * mu, 0.f)) * (X + hh);
    const float c0 = sqrtf(x0);
    return 2.f * c0 * expf(fminf(X - x0, 80.f)) - c / (1.f - c * mu) * expf(-hh);
}
__device__ float3 atmosphere(float3 ro, float3 rd, float tmax, float3 S, float Isun, float jit, float3& trans) {
    trans = f3(1.f);
    const float2 ia = iSphere(ro, rd, ATM_TOP);
    if (!(ia.x < ia.y) || ia.y <= 0.f) return f3(0.f);
    const float t0 = fmaxf(ia.x, 0.f), t1 = fminf(ia.y, tmax);
    if (t1 <= t0) return f3(0.f);
    const float3 BR = f3(BR_R, BR_G, BR_B);
    const float mu = dot(rd, S);
    const float pR = 0.0596831f * (1.f + mu * mu);
    const float g = 0.76f, gg = g * g;
    const float pM = 0.1193662f * ((1.f - gg) * (1.f + mu * mu)) /
                     ((2.f + gg) * powf(fmaxf(1.f + gg - 2.f * mu * g, 1e-4f), 1.5f));
    // samples bunched (quadratically) towards the densest point: the closest approach, or the ground
    const float tf = clamp(-dot(ro, rd), t0, t1);
    float odR = 0.f, odM = 0.f;
    float3 sR = f3(0.f), sM = f3(0.f);
    const int NH = 6;
    for (int side = 0; side < 2; ++side) {
        const float L = side == 0 ? tf - t0 : t1 - tf;
        if (L <= 1e-6f) continue;
        for (int i = 0; i < NH; ++i) {
            const float u = ((float)i + jit) * (1.f / NH);
            const float t = side == 0 ? tf - L * u * u : tf + L * u * u;
            const float ds = L * 2.f * fmaxf(u, 0.5f / NH) * (1.f / NH);
            const float3 p = ro + rd * t;
            const float r = length(p), h = r - 1.f;
            const float dR = expf(-h / HR) * ds, dM = expf(-h / HM) * ds;
            odR += dR; odM += dM;
            const float b = dot(p, S);
            float lit = 1.f;
            if (b < 0.f) lit = smoothstep(0.997f, 1.004f, sqrtf(fmaxf(r * r - b * b, 0.f)));
            if (lit <= 0.f) continue;
            const float ms = b / r;
            const float lR = HR * chapman(1.f / HR, h / HR, ms);
            const float lM = HM * chapman(1.f / HM, h / HM, ms);
            const float3 tau = BR * (odR + lR) + f3(BM_S * 1.1f * (odM + lM));
            const float3 att = exp(-tau) * lit;
            sR += att * dR; sM += att * dM;
        }
    }
    trans = exp(-(BR * odR + f3(BM_S * 1.1f * odM)));
    return (sR * BR * pR + sM * (BM_S * pM)) * Isun;
}

// length of [0, tmax] of the ray inside the shell rin <= r <= rout
__device__ __forceinline__ float shell_len(float3 ro, float3 rd, float rin, float rout, float tmax) {
    const float2 a = iSphere(ro, rd, rout);
    if (!(a.x < a.y)) return 0.f;
    const float a0 = fmaxf(a.x, 0.f), a1 = fminf(a.y, tmax);
    if (a1 <= a0) return 0.f;
    float L = a1 - a0;
    const float2 b = iSphere(ro, rd, rin);
    if (b.x < b.y) {
        const float b0 = fmaxf(b.x, a0), b1 = fminf(b.y, a1);
        if (b1 > b0) L -= b1 - b0;
    }
    return L;
}

// ------------------------------------------------------------------ planet surface
// one layer of hashed point lights (cell frequency F), filtered by the pixel footprint fp
__device__ __forceinline__ float city_layer(float3 q, float F, float pop, float fp, unsigned int sd, float rad) {
    const float pr = saturate(pop * 0.85f);
    const float avg = pr * 0.65f * 3.14159f * rad * rad;
    const float pxc = fp * F;                                    // pixel size in cells
    const float wd = 1.f - smoothstep(0.35f, 1.1f, pxc);
    if (wd <= 0.f || pr <= 0.f) return avg;
    const float3 g = q * F;
    const float3 cell = floor(g);
    const unsigned int ix = (unsigned int)(int)cell.x, iy = (unsigned int)(int)cell.y, iz = (unsigned int)(int)cell.z;
    const float hp = hash1(ix, iy, iz + sd);
    float det = 0.f;
    if (hp < pr) {
        const float3 j = f3(hash1(ix, iy, iz + sd + 1u), hash1(ix, iy, iz + sd + 2u), hash1(ix, iy, iz + sd + 3u)) * 0.5f + f3(0.25f);
        const float d2 = dot2(g - cell - j);
        const float s2 = rad * rad + 0.25f * pxc * pxc;
        det = (0.3f + 0.7f * hash1(ix, iy, iz + sd + 4u)) * (rad * rad / s2) * expf(-d2 / s2) * 1.3f;
    }
    return mix(avg, det, wd);
}

__device__ float3 surface(float3 n, float3 rd, float t, float pixA, const float* __restrict__ P,
                          const float4* __restrict__ T, int N)
{
    const unsigned int sd = (unsigned int)P[71];
    const Mat3 ER = mat3_load(P + 43), CR = mat3_load(P + 52);
    const float3 q = mul(ER, n);                                 // earth-fixed point
    const float cont = fbm3(q * 2.2f, 5, sd);
    const float land = smoothstep(0.515f, 0.535f, cont);
    const float lat = fabsf(q.y);
    const float ice = smoothstep(0.88f, 0.94f, lat);
    const float fp = t * pixA / fmaxf(-dot(rd, n), 0.12f);       // pixel footprint on the ground

    // population: land, near coasts, away from the poles
    const float coast = 1.f - smoothstep(0.f, 0.035f, cont - 0.515f);
    float pop = land * smoothstep(0.40f, 0.66f, fbm3(q * 6.5f + f3(3.1f), 3, sd + 101u));
    pop *= (0.5f + 1.3f * coast) * (1.f - smoothstep(0.72f, 0.88f, lat));
    float lights = 0.f;
    if (pop > 0.002f) {
        lights += city_layer(q, 150.f, pop, fp, sd + 7u, 0.22f);
        lights += 0.6f * city_layer(q, 470.f, pop * 0.9f, fp, sd + 13u, 0.18f);
        lights += 0.4f * city_layer(q, 1400.f, pop * 0.8f, fp, sd + 29u, 0.18f);
        // road / settlement networks: thin ridges of value noise, widened (and dimmed) by the footprint
        const float Fn = 70.f;
        const float rn = noise3(q * Fn, sd + 51u);
        const float ridge = 1.f - fabsf(2.f * rn - 1.f);
        const float lw = fmaxf(0.05f, fp * Fn * 2.f);
        lights += smoothstep(1.f - lw, 1.f, ridge) * (0.05f / lw) * pop * 0.9f;
        lights += pop * 0.05f;
    }

    // clouds (own rotation, slowly evolving)
    const float3 cq = mul(CR, n);
    const float cl = fbm3(cq * 3.0f + f3(0.f, P[67], 0.f), 4, sd + 201u);
    const float cloud = smoothstep(P[61], P[61] + 0.13f, cl);

    // lighting
    const float3 S = load3(P + 28), Mo = load3(P + 31);
    const float ns = dot(n, S);
    const float am = 1.f / (fmaxf(ns, 0.f) + 0.035f);
    const float3 sunT = exp(-f3(BR_R, BR_G, BR_B) * (HR * am) - f3(BM_S * HM * am));
    const float diff = smoothstep(-0.015f, 0.03f, ns) * fmaxf(ns, 0.004f);
    const float night = 1.f - smoothstep(-0.06f, 0.06f, ns);
    const float3 ocean = f3(0.010f, 0.020f, 0.042f);
    const float3 soil = mix(f3(0.055f, 0.065f, 0.035f), f3(0.11f, 0.095f, 0.065f), smoothstep(0.55f, 0.62f, cont));
    float3 alb = mix(ocean, soil, land);
    alb = mix(alb, f3(0.55f, 0.6f, 0.66f), ice * land);
    alb = mix(alb, f3(0.72f, 0.74f, 0.78f), cloud);
    float3 irr = sunT * (diff * P[68] * 0.11f) + f3(0.020f, 0.026f, 0.040f) * saturate(dot(n, Mo) + 0.15f);

    // aurora light falling on the ground and the clouds beneath the oval
    const float3 m = load3(P + 34);
    const float th = acosf(clamp(dot(n, m), -1.f, 1.f));
    if (fabsf(th - P[66]) < 0.3f) {
        const float4 e = tab_lookup(T, 0, N, mag_phi01(n, load3(P + 37), load3(P + 40)));
        const float gl = expf(-square((th - e.x) * (1.f / 0.045f)));
        irr += aur_colour(P[26] + P[72] + e.w, P) * (gl * e.y * P[75]);
    }
    float3 col = alb * irr;
    // sun glint on open water
    const float3 rf = reflect(rd, n);
    col += sunT * ((1.f - land) * (1.f - cloud) * powf(saturate(dot(rf, S)), 180.f) * diff * P[68] * 0.35f);
    // city lights: sodium orange to warm white in the brightest cores; clouds hide them and glow above them
    const float3 lc = mix(f3(1.0f, 0.50f, 0.17f), f3(1.0f, 0.82f, 0.55f), saturate(lights * 0.7f));
    col += lc * (lights * (1.f - 0.85f * cloud) * night * P[62] * 0.9f);
    col += f3(1.0f, 0.62f, 0.3f) * (cloud * pop * 0.10f * night * P[62]);
    return col;
}

// ------------------------------------------------------------------ sky
__device__ float3 sky(float3 rd, float pixA, unsigned int sd, float gain) {
    float3 c = f3(0.f);
    #pragma unroll
    for (int L = 0; L < 2; ++L) {
        const float F = L == 0 ? 200.f : 520.f, thr = L == 0 ? 0.965f : 0.93f;
        const float3 g = rd * F, cell = floor(g);
        const unsigned int ix = (unsigned int)(int)cell.x, iy = (unsigned int)(int)cell.y, iz = (unsigned int)(int)cell.z;
        const unsigned int s = sd + 7919u * (unsigned int)L;
        const float hs = hash1(ix, iy, iz + s);
        if (hs > thr) {
            const float3 j = f3(hash1(ix, iy, iz + s + 1u), hash1(ix, iy, iz + s + 2u), hash1(ix, iy, iz + s + 3u)) * 0.6f + f3(0.2f);
            const float sz = fmaxf(0.55f * pixA * F, 0.02f);
            const float k = (hs - thr) / (1.f - thr);
            const float b = (L == 0 ? 1.4f : 0.55f) * (0.15f + 2.2f * k * k * k);
            const float3 tint = mix(f3(1.0f, 0.78f, 0.6f), f3(0.7f, 0.82f, 1.0f), hash1(ix, iy, iz + s + 4u));
            c += tint * (b * expf(-dot2(g - cell - j) / (sz * sz)));
        }
    }
    // faint Milky Way band
    const float3 gn = f3(0.23f, -0.766f, -0.6f);
    const float bd = dot(rd, gn);
    const float band = expf(-bd * bd * 28.f);
    if (band > 0.02f) c += f3(0.55f, 0.6f, 0.75f) * (band * (0.25f + 0.75f * fbm3(rd * 6.f, 3, sd + 333u)) * 0.035f);
    return c * gain;
}

// ------------------------------------------------------------------ aurora curtains (emissive ribbon volumes)
__device__ float3 aurora_march(float3 ro, float3 rd, float tlimit, float jit, const float* __restrict__ P,
                               const float4* __restrict__ T, int N)
{
    float3 acc = f3(0.f);
    const float2 io = iSphere(ro, rd, P[65]);
    if (!(io.x < io.y) || io.y <= 0.f) return acc;
    const float hb = P[64];
    const float t0 = fmaxf(io.x, 0.f);
    float t1 = fminf(io.y, tlimit);
    const float2 ii = iSphere(ro, rd, 1.f + hb);
    if (ii.x < ii.y && ii.x > 0.f) t1 = fminf(t1, ii.x);        // below the curtains: done
    t1 = fminf(t1, t0 + 1.6f);
    if (t1 <= t0) return acc;
    const int NS = 24;
    const float dt = (t1 - t0) * (1.f / NS);
    const float3 m = load3(P + 34), ma = load3(P + 37), mb = load3(P + 40);
    const float th0 = P[66], W0 = P[76];
    const float cbase = P[26] + P[72], cspan = P[73] * P[25];
    float dp[2] = {1e9f, 1e9f};
    for (int i = 0; i < NS; ++i) {
        const float3 p = ro + rd * (t0 + ((float)i + jit) * dt);
        const float r = length(p), h = r - 1.f;
        const float3 u = p * (1.f / r);
        const float th = acosf(clamp(dot(u, m), -1.f, 1.f));
        if (h < hb - 0.006f || fabsf(th - th0) > 0.22f) { dp[0] = dp[1] = 1e9f; continue; }
        const float ph = mag_phi01(u, ma, mb);
        #pragma unroll
        for (int c = 0; c < 2; ++c) {
            const float4 e = tab_lookup(T, c * N, N, ph);
            const float d = (th - e.x) * r;
            const float dd = dp[c] < 1e8f ? fabsf(d - dp[c]) : dt;
            dp[c] = d;
            const float we2 = W0 * W0 + 0.3f * dd * dd;
            const float g = W0 * rsqrtf(we2) * expf(-d * d / we2) + 0.10f * expf(-d * d / (20.f * W0 * W0 + 0.3f * dd * dd));
            if (g < 2e-3f) continue;
            const float hf = (h - hb) / fmaxf(e.z, 1e-3f);
            const float prof = smoothstep(-0.05f, 0.05f, hf) * (1.f - smoothstep(0.62f, 1.f, hf))
                             * (0.16f + 0.84f * expf(-2.2f * fmaxf(hf, 0.f)));
            const float dens = g * prof * e.y;
            if (dens < 1e-4f) continue;
            acc += aur_colour(cbase + cspan * saturate(hf) + e.w, P) * dens;
        }
    }
    return acc * (dt * P[63]);
}

SDF_KERNEL
void earth_aurora(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
                  const float4* __restrict__ T, const int N)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pixA = pixel_angle(cam, H);
    const float jit = fract(52.9829189f * fract(0.06711056f * (float)x + 0.00583715f * (float)y));  // static IGN

    const float2 ip = iSphere(ro, rd, 1.f);
    const bool hitp = ip.x < ip.y && ip.x > 0.f;
    const float tp = hitp ? ip.x : 1e9f;
    const float3 S = load3(P + 28);
    float3 Tr;
    const float3 ins = atmosphere(ro, rd, tp, S, P[68], jit, Tr);
    float3 col;
    if (hitp) col = surface(normalize(ro + rd * tp), rd, tp, pixA, P, T, N);
    else col = sky(rd, pixA, (unsigned int)P[71], P[70]);
    col = col * Tr + ins;
    // green airglow line hugging the night limb
    const float ag = shell_len(ro, rd, 1.0158f, 1.0180f, tp) + 0.35f * shell_len(ro, rd, 1.0142f, 1.0196f, tp);
    col += mix(f3(0.30f, 0.95f, 0.45f), aur_colour(P[26] + P[72], P), 0.35f) * (ag * P[69]);
    col += aurora_march(ro, rd, tp, jit, P, T, N);
    col = vignette(col, x, y, W, H, 0.28f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
"""


class EarthAurora(Source):
    name = "earth_aurora"
    label = "Earth & Aurora"
    description = ("The night side of Earth from low orbit: city-light networks, clouds and a thin atmospheric limb "
                   "under rippling green and magenta aurora curtains, with the dawn terminator sweeping by.")
    params = {
        **standard_scene_params(palette="neon", render_scale=0.5, period=1.0, drift=0.012),
        "aurora_intensity": num(0.0, 2.0, 1.0, "Aurora intensity", tier="regional", basic=True),
        "curtain_folds": num(0.0, 1.0, 0.5, "Curtain folds", tier="regional"),
        "orbit_altitude": num(200.0, 1600.0, 420.0, "Orbit altitude", unit="km", step=10.0, tier="regional"),
        "terminator_angle": num(0.0, 1.0, 0.5, "Dawn terminator", tier="regional"),
        "cloud_cover": num(0.0, 1.0, 0.45, "Cloud cover", tier="regional"),
        "city_lights": num(0.0, 2.0, 1.0, "City lights", tier="regional"),
        "orbit_speed": num(0.0, 4.0, 1.0, "Orbit speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        mod = raw_module(_SRC, SDF3D_OPTIONS)
        self.k = mod.get_function("earth_aurora")
        self.kt = mod.get_function("aurora_table")
        self.P = cp.zeros(80, cp.float32)
        self.Ph = np.zeros(80, np.float32)
        self.Q = cp.zeros(32, cp.float32)
        self.Qh = np.zeros(32, np.float32)
        self.T = cp.zeros((2 * N_TAB, 4), cp.float32)
        seed = int(params["seed"])
        self.rng = np.random.default_rng(seed)
        self.seed_f = float(seed % 65521)
        self.ground = float(self.rng.uniform(0, 2 * math.pi))     # earth rotation under the camera
        self.oval = float(self.rng.uniform(0, 2 * math.pi))       # auroral oval rotation
        self.cloud = 0.0
        self.cloud_evo = float(self.rng.uniform(0, 50))
        self.t_fold = float(self.rng.uniform(0, 100))
        self.t_rays = float(self.rng.uniform(0, 100))
        self.t_act = float(self.rng.uniform(0, 100))
        self.t_top = float(self.rng.uniform(0, 100))
        self.sweep = 0.0
        self.cam_ph = 0.0
        self.drift = 0.0
        self.clock = 0.0
        self.e_s = None
        self.prev_pulse = 0.0
        self.armed = True
        self.last_spawn = -1.0
        self.flares: list[list[float]] = []                        # [phi01, vel, amp, width, age]

    # ------------------------------------------------------------------ helpers
    def _spawn_flare(self, phi_cam: float, pulse: float) -> None:
        r = self.rng
        self.flares.append([phi_cam + float(r.uniform(-0.07, 0.07)),
                            float(r.choice([-1.0, 1.0]) * r.uniform(0.05, 0.11)),
                            0.75 + 0.6 * pulse, float(r.uniform(0.012, 0.02)), 0.0])
        if len(self.flares) > MAX_FLARES:
            self.flares.pop(0)

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        dt = min(max(float(dt), 0.0), 0.25)
        a = 1.0 - math.exp(-dt / 0.3) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s
        self.clock += dt

        # ---- integrate rates (pulse kicks rates and gains only)
        osp = float(p["orbit_speed"]) * spd
        self.ground = wrap(self.ground + osp * (0.006 + 0.02 * e) * dt, 2 * math.pi)
        self.oval = wrap(self.oval + osp * (0.002 + 0.007 * e) * dt, 2 * math.pi)
        self.cloud = wrap(self.cloud + osp * (0.002 + 0.004 * e) * dt, 2 * math.pi)
        self.cloud_evo += spd * (0.004 + 0.01 * e) * dt
        self.t_fold += spd * (0.04 + 0.22 * e ** 1.2 + 0.3 * pulse) * dt
        self.t_rays += spd * (0.12 + 0.55 * e + 0.8 * pulse) * dt
        self.t_act += spd * (0.03 + 0.10 * e) * dt
        self.t_top += spd * (0.04 + 0.12 * e) * dt
        self.sweep = wrap(self.sweep + spd * 0.03 * dt, 2 * math.pi)
        self.cam_ph = wrap(self.cam_ph + spd * (0.05 + 0.07 * e) * dt, 2 * math.pi)
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # ---- camera: low orbit at 52N looking north towards the oval, horizon in the upper third
        alt = float(p["orbit_altitude"]) / EARTH_KM
        up = (0.0, math.sin(LAT_CAM), math.cos(LAT_CAM))
        north = (0.0, math.cos(LAT_CAM), -math.sin(LAT_CAM))
        east = (1.0, 0.0, 0.0)
        eye = tuple((1.0 + alt) * c for c in up)
        dip = math.acos(1.0 / (1.0 + alt))
        s_alt = min(max((alt - 0.0314) / 0.22, 0.0), 1.0)
        pitch = -(dip + 0.16 + 0.10 * s_alt)
        yaw = (0.08 + 0.10 * es) * math.sin(self.cam_ph)
        fh = tuple(n * math.cos(yaw) + ea * math.sin(yaw) for n, ea in zip(north, east))
        fwd = tuple(f * math.cos(pitch) + u * math.sin(pitch) for f, u in zip(fh, up))
        roll = 0.012 * math.sin(0.7 * self.cam_ph + 1.3)
        cam = camera_pack(eye, tuple(a_ + b_ for a_, b_ in zip(eye, fwd)), up=up, fov_deg=55.0, roll=roll)

        # ---- magnetic frame (tilted towards the camera's side), rotating oval basis
        m = (0.0, math.cos(MAG_TILT), math.sin(MAG_TILT))
        ma0 = (1.0, 0.0, 0.0)
        mb0 = (0.0, m[2], -m[1])
        cps, sps = math.cos(self.oval), math.sin(self.oval)
        ma = tuple(cps * x0 + sps * y0 for x0, y0 in zip(ma0, mb0))
        mb = tuple(-sps * x0 + cps * y0 for x0, y0 in zip(ma0, mb0))
        uc = up
        phi_cam = math.atan2(sum(x_ * y_ for x_, y_ in zip(uc, mb)), sum(x_ * y_ for x_, y_ in zip(uc, ma)))
        phi_cam = (phi_cam / (2 * math.pi)) % 1.0

        # ---- beat flares: spawn on the rising edge (120 ms refractory), then travel along the curtain
        rising = (pulse - self.prev_pulse > 0.15) or (self.armed and pulse > 0.55)
        if rising and self.clock - self.last_spawn > 0.12 and dt > 0:
            self._spawn_flare(phi_cam, pulse)
            self.last_spawn = self.clock
            self.armed = False
        if pulse < 0.25:
            self.armed = True
        self.prev_pulse = pulse
        live = []
        for f in self.flares:
            f[4] += dt
            f[0] = (f[0] + f[1] * spd * dt) % 1.0
            if f[4] < 4.0:
                live.append(f)
        self.flares = live

        # ---- sun: below the night horizon; the dawn terminator sweeps across the limb
        beta = -0.12 + 0.62 * float(p["terminator_angle"]) + 0.05 * math.sin(0.5 * self.sweep + 0.7)
        gam = 0.75 * math.sin(self.sweep)
        S = (math.sin(gam) * math.cos(beta), math.sin(beta), -math.cos(gam) * math.cos(beta))
        moon = (-0.45, 0.62, 0.64)
        mn = math.sqrt(sum(c * c for c in moon))
        moon = tuple(c / mn for c in moon)

        # ---- aurora table parameters
        folds = float(p["curtain_folds"])
        H = 0.028 + 0.045 * es
        Qh = self.Qh
        Qh[:] = 0.0
        Qh[0] = N_TAB
        Qh[1] = THETA0
        Qh[2] = (0.008 + 0.055 * folds) * (0.8 + 0.4 * es)
        Qh[3] = 2.5 + 8.5 * folds
        Qh[4] = self.t_fold
        Qh[5] = self.t_rays
        Qh[6] = self.t_act
        Qh[7] = self.t_top
        Qh[8] = H
        Qh[9] = 0.25 + 0.6 * es
        Qh[10] = self.seed_f
        Qh[11] = len(self.flares)
        for j, f in enumerate(self.flares[:MAX_FLARES]):
            env = (1.0 - math.exp(-f[4] / 0.05)) * math.exp(-f[4] / 0.8)
            Qh[12 + 3 * j] = f[0] * 2 * math.pi
            Qh[13 + 3 * j] = f[2] * env
            Qh[14 + 3 * j] = f[3] * 2 * math.pi * (1.0 + 0.8 * f[4])
        Qh[30] = 0.065
        self.Q.set(Qh)
        nthreads = 2 * N_TAB
        self.kt(((nthreads + 255) // 256,), (256,), (self.T, self.Q))

        # ---- main params
        aur_gain = float(p["aurora_intensity"]) * (0.45 + 0.85 * e) * (1.0 + 0.5 * pulse) * 120.0
        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"]) * 1.1
        Ph[28:31] = S
        Ph[31:34] = moon
        Ph[34:37] = m
        Ph[37:40] = ma
        Ph[40:43] = mb
        Ph[43:52] = rot_matrix((0.0, 1.0, 0.0), -self.ground)
        Ph[52:61] = rot_matrix((0.0, 1.0, 0.0), -(self.ground * 1.15 + self.cloud))
        Ph[61] = 0.70 - 0.32 * float(p["cloud_cover"])
        Ph[62] = float(p["city_lights"]) * (0.9 + 0.2 * e) * (1.0 + 0.6 * pulse)
        Ph[63] = aur_gain
        Ph[64] = AUR_BOTTOM
        Ph[65] = 1.0 + AUR_BOTTOM + H * 1.38 * 1.6
        Ph[66] = THETA0
        Ph[67] = self.cloud_evo
        Ph[68] = 12.0
        Ph[69] = 1.6
        Ph[70] = 1.0
        Ph[71] = self.seed_f
        Ph[72] = -0.05
        Ph[73] = 0.30
        Ph[74] = pulse
        Ph[75] = aur_gain * 0.0004
        Ph[76] = 0.0045
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P, self.T,
                                           np.int32(N_TAB)))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
        self.Q = None
        self.T = None
