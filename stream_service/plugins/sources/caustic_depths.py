"""Caustic depths: underwater glide over a sandy seabed under a rippling surface, sun shafts and a dancing caustic net.

Source: Original (inspired by https://github.com/evanw/webgl-water/blob/master/renderer.js and
        http://madebyevan.com/webgl-water/ )
Author: smfx (original); caustics method after Evan Wallace (WebGL Water, MIT)
License: MIT
license_class: permissive

Idea only (no code copied): WebGL Water measures caustic brightness as the ratio of a surface patch's area before
and after refraction onto the floor.  Here that ratio is computed per pixel and analytically: the surface is a sum
of 8 sines eta(q), the refracted-light map is F(q) = q + D * (g0 - 0.25 * grad eta(q)) (D = depth below the surface,
g0 = the refracted sun's horizontal drift per unit depth), its Jacobian is J = I - 0.25 D * Hessian(eta), and the
light on the seabed is 1 / |det J| (softened by eps, so focal lines are bright but finite).  The volumetric shafts
use the same field (its 3 long waves only) at every depth along the view ray, 16 samples with a static
interleaved-gradient start jitter, Henyey-Greenstein forward scattering and per-channel absorption.

Seabed: a tileable heightfield (dunes + boulder outcrops) precomputed with numpy FFT noise in setup and sampled at
two scales, marched analytically (<= 44 Lipschitz-ish steps + 5 bisections); analytic sand ripples; rock/sand
material from a precomputed mask.  Kelp: swaying ribbon silhouettes found by a 2D DDA over a hashed cell grid
(backlit when looking towards the sun).  Marine snow: a 3D DDA over a drifting hashed cell grid (smooth, no
per-frame noise).  Everything is evaluated in a camera-relative frame; every world pattern gets a host-integrated,
wrapped offset (fp64 on the host), so long runs never lose fp32 precision or jump.

Macros: energy -> surface chop (wave clock rate and amplitude, so the caustic net sharpens and dances faster),
shaft intensity, marine-snow drift speed and amount, kelp sway, camera glide speed and sway.  pulse -> a surge
brightening the shafts, a caustic sharpness kick (eps shrinks) and a lift of caustic gain, plus a small kick to the
wave clock rate (rates only, nothing jumps).  speed scales every rate; drift tints the water and light through
the palette (mixed in by water_tint); intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, wrap
from stream_service.plugins._lib.upsample import internal_size, present

TEXN = 512           # heightfield texture size (power of two, wraps)
TA = 40.0            # world size of the fine heightfield tile
TB = 120.0           # world size of the coarse (transposed) tile
KC = 3.0             # kelp cell size (hash period 64 cells)
SC = 0.9             # marine snow cell size (hash period 32 cells)
TFAR = 46.0
NWAVE = 8
TWO_PI = 2.0 * math.pi

# base surface waves: wavelength (world units), curvature amplitude (kappa = a k^2), spread from the wind direction
WAVE_LAM = (8.0, 6.2, 4.8, 2.9, 2.3, 1.8, 1.45, 1.15)
WAVE_KAP = (0.17, 0.15, 0.13, 0.13, 0.12, 0.11, 0.10, 0.09)
WAVE_SPREAD = (0.0, 0.55, -0.6, 0.95, -1.05, 0.35, -0.3, 1.4)

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 surface y | 29 scatter sigma
#  30..32 absorption rgb | 33..35 extinction rgb | 36..38 refracted sun travel dir | 39..41 water colour
#  42..44 sun colour | 45..47 snow colour | 48 shaft gain | 49 shaft contrast | 50 caustic c (bed) | 51 caustic eps
#  52 caustic gain | 53 shaft c | 54 shaft eps | 55 relief | 56 hmin | 57 hmax | 58 kelp density | 59 kelp sway phase
#  60 kelp sway amp | 61,62 offA | 63,64 offB | 65,66 offK | 67..69 offS | 70 ripple phase | 71,72 ripple dir
#  73 ripple freq | 74 snow threshold | 75 pulse | 76 energy | 77 seed | 78 surface slope gain | 79..81 sun dir (air)
#  82 kmax | 84..115 waves (kx, kz, a, phase)
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define TEXN %(texn)d
#define TA_W (%(ta)ff)
#define TB_W (%(tb)ff)
#define KC (%(kc)ff)
#define SCELL (%(sc)ff)
#define TFAR (%(tfar)ff)
#define NWAVE %(nwave)d
#define NLONG 3
#define PW 84

// fast-math sin/cos with explicit range reduction (phases here can reach a few hundred radians)
__device__ __forceinline__ float wsin(float a) { a -= 6.2831853f * rintf(a * 0.15915494f); return __sinf(a); }
__device__ __forceinline__ float wcos(float a) { a -= 6.2831853f * rintf(a * 0.15915494f); return __cosf(a); }

// bilinear, wrapping, on a TEXN x TEXN float texture; u, v in texels
__device__ __forceinline__ float texw(const float* __restrict__ T, float u, float v) {
    u -= 0.5f; v -= 0.5f;
    const float fu = floorf(u), fv = floorf(v);
    const float tx = u - fu, ty = v - fv;
    const int x0 = ((int)fu) & (TEXN - 1), y0 = ((int)fv) & (TEXN - 1);
    const int x1 = (x0 + 1) & (TEXN - 1), y1 = (y0 + 1) & (TEXN - 1);
    const float a = __ldg(T + y0 * TEXN + x0), b = __ldg(T + y0 * TEXN + x1);
    const float c = __ldg(T + y1 * TEXN + x0), d = __ldg(T + y1 * TEXN + x1);
    return (1.f - ty) * ((1.f - tx) * a + tx * b) + ty * ((1.f - tx) * c + tx * d);
}
__device__ __forceinline__ float bed_h(float x, float z, const float* __restrict__ HT, const float* __restrict__ P) {
    const float sa = (float)TEXN / TA_W, sb = (float)TEXN / TB_W;
    return P[55] * (texw(HT, (x + P[61]) * sa, (z + P[62]) * sa) + 0.8f * texw(HT, (z + P[64]) * sb, (x + P[63]) * sb));
}
__device__ __forceinline__ float rock_m(float x, float z, const float* __restrict__ RT, const float* __restrict__ P) {
    const float sa = (float)TEXN / TA_W, sb = (float)TEXN / TB_W;
    return fmaxf(texw(RT, (x + P[61]) * sa, (z + P[62]) * sa), texw(RT, (z + P[64]) * sb, (x + P[63]) * sb));
}

// Caustic focus 1/|det J| of the refracted-light map q -> q + D (g0 - 0.25 grad eta(q)),
// eta = sum a_i sin(k_i.q + ph_i); J = I + c * sum a_i k_i k_i^T sin(.), c = 0.25 D (folded into c).
template <int N>
__device__ __forceinline__ float caustic_I(float qx, float qz, float c, float eps, const float* __restrict__ P) {
    float sxx = 0.f, szz = 0.f, sxz = 0.f;
    #pragma unroll
    for (int i = 0; i < N; ++i) {
        const float* w = P + PW + 4 * i;
        const float s = w[2] * wsin(w[0] * qx + w[1] * qz + w[3]);
        sxx += s * w[0] * w[0]; szz += s * w[1] * w[1]; sxz += s * w[0] * w[1];
    }
    const float det = (1.f + c * sxx) * (1.f + c * szz) - c * c * sxz * sxz;
    return rsqrtf(det * det + eps * eps);
}
__device__ __forceinline__ float2 surf_grad(float qx, float qz, const float* __restrict__ P) {
    float gx = 0.f, gz = 0.f;
    #pragma unroll
    for (int i = 0; i < NWAVE; ++i) {
        const float* w = P + PW + 4 * i;
        const float c = w[2] * wcos(w[0] * qx + w[1] * qz + w[3]);
        gx += c * w[0]; gz += c * w[1];
    }
    return f2(gx, gz);
}

// Kelp: vertical swaying ribbons, one per occupied cell of a KC grid, found by a 2D DDA in xz.
__device__ void kelp_trace(float3 ro, float3 rd, float tlim, float pix, const float* __restrict__ P,
                           float& tk, float& ak, float& side, float& ku, float& kh)
{
    const float k2 = sqrtf(rd.x * rd.x + rd.z * rd.z);
    if (k2 < 2e-3f) return;
    const float dx = rd.x / k2, dz = rd.z / k2;
    const float ox = ro.x + P[65], oz = ro.z + P[66];
    const float smax = fminf(tlim, 36.f) * k2;
    int cx = (int)floorf(ox / KC), cz = (int)floorf(oz / KC);
    const int sx = dx > 0.f ? 1 : -1, sz = dz > 0.f ? 1 : -1;
    const float tdx = KC / fmaxf(fabsf(dx), 1e-6f), tdz = KC / fmaxf(fabsf(dz), 1e-6f);
    float tmx = fabsf(dx) > 1e-6f ? ((float)(cx + (sx > 0 ? 1 : 0)) * KC - ox) / dx : 1e9f;
    float tmz = fabsf(dz) > 1e-6f ? ((float)(cz + (sz > 0 ? 1 : 0)) * KC - oz) / dz : 1e9f;
    const float dens = P[58], SURF = P[28], HMIN = P[56];
    const unsigned int seed = (unsigned int)P[77];
    float sbest = 1e9f;
    for (int i = 0; i < 26; ++i) {
        const unsigned int hx = (unsigned int)(cx & 63), hz = (unsigned int)(cz & 63);
        const float h0 = hash1(hx, hz, seed + 101u);
        if (h0 < dens) {
            const float h1 = hash1(hx, hz, seed + 202u), h2 = hash1(hx, hz, seed + 303u), h3 = hash1(hx, hz, seed + 404u);
            const float px = ((float)cx + 0.22f + 0.56f * h1) * KC, pz = ((float)cz + 0.22f + 0.56f * h2) * KC;
            const float s0 = (px - ox) * dx + (pz - oz) * dz;
            if (s0 > -1.f && s0 < smax) {
                const float t0 = fmaxf(s0, 0.f) / k2;
                const float yc = ro.y + rd.y * t0;
                const float top = 0.3f + (0.38f + 0.45f * h3) * (SURF - 0.3f);
                const float u = (yc - HMIN) / (top - HMIN);
                if (u > 0.f && u < 1.f) {
                    const float ph = P[59] + h1 * 6.2831853f;
                    const float amp = P[60] * powf(u, 1.5f) * (0.6f + 0.6f * h3);
                    const float swx = amp * wsin(ph + 0.35f * yc);
                    const float swz = amp * 0.6f * wcos(0.8f * ph + 0.27f * yc + h2 * 3.f);
                    const float qx = px + swx - ox, qz = pz + swz - oz;
                    const float s1 = qx * dx + qz * dz;
                    const float dper = qx * dz - qz * dx;
                    const float t1 = s1 / k2;
                    const float leaf = fabsf(wsin(yc * 1.9f + h1 * 6.283f + 0.5f * ph));
                    const float w = (0.16f * (0.3f + 0.9f * leaf) * (0.7f + 0.6f * h2)) * (1.f - smoothstep(0.84f, 1.f, u)) + 0.02f;
                    const float fw = fmaxf(t1 * pix, 1e-4f);
                    float a = 1.f - smoothstep(w - fw, w + fw, fabsf(dper));
                    a *= smoothstep(0.8f, 2.6f, t1);
                    if (a > 0.01f && s1 > 0.f && s1 < sbest && t1 < tlim) {
                        sbest = s1; tk = t1; ak = a; side = dper / fmaxf(w, 1e-3f); ku = u; kh = h1;
                    }
                }
            }
        }
        float sb;
        if (tmx < tmz) { cx += sx; sb = tmx; tmx += tdx; } else { cz += sz; sb = tmz; tmz += tdz; }
        if (sb > smax || sb > sbest + 1.2f) break;
    }
}

// Marine snow: one soft mote per occupied cell of a drifting SCELL grid, 3D DDA along the ray.
__device__ float snow_trace(float3 ro, float3 rd, float tlim, float pix, const float* __restrict__ P)
{
    const float3 o = f3(ro.x + P[67], ro.y + P[68], ro.z + P[69]);
    int cx = (int)floorf(o.x / SCELL), cy = (int)floorf(o.y / SCELL), cz = (int)floorf(o.z / SCELL);
    const int sx = rd.x > 0.f ? 1 : -1, sy = rd.y > 0.f ? 1 : -1, sz = rd.z > 0.f ? 1 : -1;
    const float tdx = SCELL / fmaxf(fabsf(rd.x), 1e-6f), tdy = SCELL / fmaxf(fabsf(rd.y), 1e-6f);
    const float tdz = SCELL / fmaxf(fabsf(rd.z), 1e-6f);
    float tmx = fabsf(rd.x) > 1e-6f ? ((float)(cx + (sx > 0 ? 1 : 0)) * SCELL - o.x) / rd.x : 1e9f;
    float tmy = fabsf(rd.y) > 1e-6f ? ((float)(cy + (sy > 0 ? 1 : 0)) * SCELL - o.y) / rd.y : 1e9f;
    float tmz = fabsf(rd.z) > 1e-6f ? ((float)(cz + (sz > 0 ? 1 : 0)) * SCELL - o.z) / rd.z : 1e9f;
    const unsigned int seed = (unsigned int)P[77];
    const float thr = P[74];
    float acc = 0.f;
    for (int i = 0; i < 24; ++i) {
        const unsigned int hc = (unsigned int)(cx & 31) | ((unsigned int)(cy & 31) << 5) | ((unsigned int)(cz & 31) << 10);
        const float h0 = hash1(hc, seed, 7u);
        const float pres = saturate((thr - h0) * 12.f);
        if (pres > 0.f) {
            const float h1 = hash1(hc, seed, 11u), h2 = hash1(hc, seed, 13u), h3 = hash1(hc, seed, 17u);
            const float3 c = f3(((float)cx + 0.1f + 0.8f * h1) * SCELL, ((float)cy + 0.1f + 0.8f * h2) * SCELL,
                                ((float)cz + 0.1f + 0.8f * h3) * SCELL);
            const float tc = dot(c - o, rd);
            if (tc > 0.15f && tc < tlim) {
                const float d2 = dot2(o + rd * tc - c);
                const float r = 0.010f * (0.5f + 1.3f * h3 * h3);
                const float fw = tc * pix * 0.9f;
                const float rr = r * r + fw * fw;
                acc += pres * (r * r / rr) * __expf(-d2 / rr) * __expf(-0.09f * tc) * smoothstep(0.15f, 0.9f, tc)
                       * (0.45f + h2);
            }
        }
        float tn;
        if (tmx < tmy && tmx < tmz) { cx += sx; tn = tmx; tmx += tdx; }
        else if (tmy < tmz) { cy += sy; tn = tmy; tmy += tdy; }
        else { cz += sz; tn = tmz; tmz += tdz; }
        if (tn > tlim) break;
    }
    return acc;
}

SDF_KERNEL
void caustic_depths(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
                    const float* __restrict__ HT, const float* __restrict__ RT)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);
    const float SURF = P[28];
    const float3 sabs = load3(P + 30), sext = load3(P + 33), Td = load3(P + 36);
    const float3 wcol = load3(P + 39), scol = load3(P + 42);
    const float invTy = 1.f / fmaxf(-Td.y, 0.2f);
    const float2 g0 = f2(Td.x * invTy, Td.z * invTy);
    const float3 fogc = wcol * (0.5f + 0.85f * smoothstep(-0.75f, 0.9f, rd.y));
    const float pulse = P[75];

    // ---------------------------------------------------------------- seabed (heightfield march)
    const float HMIN = P[56], HMAX = P[57];
    float tb = 1e9f; bool hitb = false;
    {
        float t0 = 0.02f, t1 = TFAR;
        if (rd.y < -1e-5f) t1 = fminf(t1, (HMIN - ro.y) / rd.y);
        bool ok = true;
        if (ro.y > HMAX) { if (rd.y < -1e-5f) t0 = (HMAX - ro.y) / rd.y; else ok = false; }
        if (ok && t0 < t1) {
            float t = t0, tp = t0;
            for (int i = 0; i < 44; ++i) {
                const float3 p = ro + rd * t;
                const float dy = p.y - bed_h(p.x, p.z, HT, P);
                if (dy < 0.f) {
                    float a = tp, b = t;
                    for (int j = 0; j < 5; ++j) {
                        const float m = 0.5f * (a + b);
                        const float3 pm = ro + rd * m;
                        if (pm.y - bed_h(pm.x, pm.z, HT, P) < 0.f) b = m; else a = m;
                    }
                    tb = 0.5f * (a + b); hitb = true; break;
                }
                if (t >= t1 || (rd.y >= 0.f && p.y > HMAX)) break;
                tp = t;
                t = fminf(t + fmaxf(dy * 0.42f, 0.015f + 0.014f * t), t1);
            }
        }
    }
    const float ts = rd.y > 1e-4f ? (SURF - ro.y) / rd.y : 1e9f;
    const float tscene = hitb ? tb : fminf(ts, TFAR);

    // ---------------------------------------------------------------- kelp
    float tk = 1e9f, ak = 0.f, kside = 0.f, ku = 0.f, kh = 0.f;
    if (P[58] > 0.002f) kelp_trace(ro, rd, tscene, pix, P, tk, ak, kside, ku, kh);

    // ---------------------------------------------------------------- surface hit shading
    float3 col = fogc;
    if (hitb) {
        const float3 pos = ro + rd * tb;
        const float e = 0.03f + 0.002f * tb;
        const float h0 = bed_h(pos.x, pos.z, HT, P);
        const float hx = bed_h(pos.x + e, pos.z, HT, P), hz = bed_h(pos.x, pos.z + e, HT, P);
        float3 n = normalize(f3(-(hx - h0) / e, 1.f, -(hz - h0) / e));
        const float rk = smoothstep(0.15f, 0.7f, rock_m(pos.x, pos.z, RT, P));
        const float fw = tb * pix / fmaxf(-rd.y, 0.06f);               // ground footprint of a pixel
        // sand ripples (normal only), meandering with the dune height
        const float rf = P[73];
        const float ramp = 0.22f * (1.f - rk) * (1.f - smoothstep(0.08f, 0.35f, fw * rf));
        const float rarg = rf * (pos.x * P[71] + pos.z * P[72]) + P[70] + 2.2f * h0;
        const float rc = ramp * wcos(rarg);
        n = normalize(n + f3(-rc * P[71], 0.f, -rc * P[72]));
        // caustic net
        const float D = fmaxf(SURF - pos.y, 0.f);
        float I = caustic_I<NWAVE>(pos.x - D * g0.x, pos.z - D * g0.y, P[50] * D / SURF, P[51], P);
        I = mix(I, 1.2f, smoothstep(0.35f, 1.4f, fw * P[82]));        // far / grazing: fade to the mean
        const float3 sunb = scol * exp(sabs * (-D * invTy));
        const float dif = saturate(dot(n, -Td));
        const float sandv = 0.85f + 0.3f * saturate(h0 * 0.6f + 0.5f);
        const float3 alb = mix(f3(0.30f, 0.255f, 0.17f) * sandv, f3(0.06f, 0.075f, 0.065f), rk);
        const float amb = 0.45f + 0.55f * n.y;
        col = alb * (sunb * (dif * (0.2f + P[52] * I)) + wcol * (1.4f * amb));
    } else if (ts < TFAR) {
        // underside of the surface: Snell's window inside the critical angle, total internal reflection outside
        const float3 pos = ro + rd * ts;
        const float2 gr = surf_grad(pos.x, pos.z, P) * P[78];
        const float3 nu = normalize(f3(-gr.x, 1.f, -gr.y));
        const float ci = dot(rd, nu);
        const float k = 1.f - 1.7689f * (1.f - ci * ci);
        const float2 sh = normalize(f2(-Td.x, -Td.z) + f2(1e-4f));
        const float rip = saturate(0.5f + 2.5f * (gr.x * sh.x + gr.y * sh.y));
        const float3 tir = wcol * (0.55f + 1.1f * rip) + scol * (0.05f * rip * rip);
        if (k > 0.f) {
            const float3 a = normalize(rd * 1.33f + nu * (sqrtf(k) - 1.33f * ci));
            const float ct = saturate(a.y);
            const float F = 0.02f + 0.98f * powf(1.f - ct, 5.f);
            const float3 La = load3(P + 79);
            const float sd = saturate(dot(a, La));
            const float3 sky = mix(f3(0.35f, 0.6f, 0.85f), scol * 0.5f, 0.3f) * 1.4f;
            const float3 tr = sky + scol * (powf(sd, 300.f) * 30.f + powf(sd, 12.f) * 0.8f);
            col = mix(tr, tir, F);
        } else {
            col = tir;
        }
    }
    {
        const float3 Tv = exp(sext * (-tscene));
        col = col * Tv + fogc * (f3(1.f) - Tv);
    }

    // ---------------------------------------------------------------- volumetric shafts (16 samples)
    float3 ins = f3(0.f), insk = f3(0.f);
    {
        const float tv = fminf(tscene, 34.f);
        const float jit = fract(52.9829189f * fract(0.06711056f * (float)x + 0.00583715f * (float)y));
        const float c_s = P[53] / SURF, eps_s = P[54], cexp = P[49];
        for (int i = 0; i < 16; ++i) {
            const float s = ((float)i + jit) * (1.f / 16.f);
            const float tsx = tv * s * s;
            const float dts = tv * 2.f * s * (1.f / 16.f);
            const float3 p = ro + rd * tsx;
            const float d = SURF - p.y;
            if (d <= 0.f) continue;
            const float I = caustic_I<NLONG>(p.x - d * g0.x, p.z - d * g0.y, c_s * d, eps_s, P);
            const float shf = fmaxf(powf(fminf(I, 5.f), cexp) - 0.6f, 0.f) + 0.12f;   // beams, little uniform haze
            const float3 e3 = exp(sabs * (-d * invTy) + sext * (-tsx)) * (shf * dts);
            ins += e3;
            if (tsx < tk) insk += e3;
        }
        const float cth = dot(rd, -Td);
        const float g = 0.55f;
        const float hg = (1.f - g * g) / powf(1.f + g * g - 2.f * g * cth, 1.5f);
        const float3 k3 = scol * (P[29] * P[48] * (0.35f + hg));
        ins = ins * k3; insk = insk * k3;
    }
    col += ins;

    // ---------------------------------------------------------------- kelp composite
    if (ak > 0.f) {
        const float3 pk = ro + rd * tk;
        const float D = fmaxf(SURF - pk.y, 0.f);
        const float3 sunk = scol * exp(sabs * (-D * invTy));
        const float2 d2 = normalize(f2(rd.x, rd.z));
        const float sd = clamp(kside, -1.f, 1.f), sc = sqrtf(fmaxf(1.f - sd * sd, 0.f));
        const float3 nk = normalize(f3(d2.y * sd - d2.x * sc, 0.2f, -d2.x * sd - d2.y * sc));
        const float dif = 0.45f + 0.55f * dot(nk, -Td);
        const float back = powf(saturate(dot(rd, -Td)), 3.f);
        const float3 kalb = mix(f3(0.045f, 0.06f, 0.012f), f3(0.09f, 0.07f, 0.018f), kh);
        float3 kc = kalb * (sunk * fmaxf(dif, 0.1f) + wcol * 0.9f)
                  + sunk * f3(0.16f, 0.24f, 0.03f) * (back * (0.35f + 0.65f * ku));
        const float3 Tk = exp(sext * (-tk));
        kc = kc * Tk + fogc * (f3(1.f) - Tk) + insk;
        col = mix(col, kc, ak);
    }

    // ---------------------------------------------------------------- marine snow
    {
        float tl = fminf(tscene, 14.f);
        if (ak > 0.5f) tl = fminf(tl, tk);
        col += load3(P + 45) * snow_trace(ro, rd, tl, pix, P);
    }

    col = vignette(col, x, y, W, H, 0.4f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
""" % {"texn": TEXN, "ta": TA, "tb": TB, "kc": KC, "sc": SC, "tfar": TFAR, "nwave": NWAVE}


def _lowpass(rng, n: int, fc: float) -> np.ndarray:
    """Tileable gaussian-lowpassed noise (cutoff fc in cycles/texel), zero mean, unit std."""
    f = np.fft.fftfreq(n)
    kk = np.sqrt(f[:, None] ** 2 + f[None, :] ** 2)
    spec = np.fft.fft2(rng.standard_normal((n, n))) * np.exp(-(kk / fc) ** 2)
    spec[0, 0] = 0.0
    out = np.real(np.fft.ifft2(spec))
    return (out - out.mean()) / max(out.std(), 1e-9)


def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _gen_seabed(seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed * 7919 + 13)
    dunes = _lowpass(rng, TEXN, 0.010)
    rock_base = _lowpass(rng, TEXN, 0.028)
    detail = _lowpass(rng, TEXN, 0.085)
    fine = _lowpass(rng, TEXN, 0.2)
    rock = _smoothstep(0.85, 1.7, rock_base + 0.25 * detail)
    rock_h = rock ** 0.8 * (1.25 + 0.35 * detail + 0.12 * fine)
    height = 0.5 * dunes + 1.15 * rock_h + 0.05 * detail * (1.0 - rock)
    height -= np.median(height)
    return height.astype(np.float32), rock.astype(np.float32)


def _srgb_to_lin(c):
    return np.power(np.clip(np.asarray(c, np.float64), 0.0, 1.0), 2.2)


def _pal_lin(t: float, coeffs) -> np.ndarray:
    a, b, c, d = (np.asarray(coeffs[i:i + 3], np.float64) for i in (0, 3, 6, 9))
    return _srgb_to_lin(np.clip(a + b * np.cos(TWO_PI * (c * t + d)), 0.0, 1.0))


def _lum(c) -> float:
    return float(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])


def _refract(i, n, eta):
    i = np.asarray(i, np.float64); n = np.asarray(n, np.float64)
    ci = float(np.dot(n, i))
    k = 1.0 - eta * eta * (1.0 - ci * ci)
    return eta * i - (eta * ci + math.sqrt(max(k, 0.0))) * n


class CausticDepths(Source):
    name = "caustic_depths"
    label = "Caustic depths"
    description = "Underwater: sun shafts slant through a rippling surface while a caustic net dances on a sandy seabed with rocks and kelp."
    tags = {"family": "water", "dims": "3d", "toolkit": "sdf3d", "technique": ["analytic", "optical", "volumetric"],
            "moods": ["liquid", "calm", "dreamy"], "style": "gentle", "energy": [0.0, 0.7]}
    params = {
        **standard_scene_params(palette="ocean", render_scale=0.5, period=0.6, drift=0.02),
        "depth": num(0.0, 1.0, 0.3, "Depth (turquoise shallows -> deep blue)", tier="regional", basic=True),
        "shaft_density": num(0.0, 1.5, 0.8, "Sun shafts", tier="regional"),
        "caustic_scale": num(0.4, 2.5, 1.0, "Caustic scale", tier="regional"),
        "seabed_relief": num(0.0, 1.5, 0.7, "Seabed relief", tier="regional"),
        "turbidity": num(0.0, 1.0, 0.35, "Turbidity", tier="regional"),
        "kelp": num(0.0, 1.0, 0.45, "Kelp", tier="regional"),
        "water_tint": num(0.0, 1.0, 0.4, "Palette tint", tier="regional"),
        "glide_speed": num(0.0, 3.0, 1.0, "Glide speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("caustic_depths")
        seed = int(params["seed"])
        self.hnp, rock = _gen_seabed(seed)
        self.hmin_t, self.hmax_t = float(self.hnp.min()), float(self.hnp.max())
        self.HT = cp.asarray(self.hnp)
        self.RT = cp.asarray(rock)
        self.P = cp.zeros(128, cp.float32)
        self.Ph = np.zeros(128, np.float32)
        rng = np.random.default_rng(seed)
        wind = rng.uniform(0.0, TWO_PI)
        self.w_ang = np.array([wind + s + rng.uniform(-0.15, 0.15) for s in WAVE_SPREAD])
        self.w_k0 = np.array([TWO_PI / lam for lam in WAVE_LAM])
        self.w_om = 0.32 * np.sqrt(9.81 * self.w_k0)
        self.w_ph0 = rng.uniform(0.0, TWO_PI, NWAVE)
        self.w_clock = np.zeros(NWAVE)          # sum omega * rate * dt
        self.w_cam = np.zeros(NWAVE)            # sum k . dcam (integrated so caustic_scale eases never swim)
        self.ripple_dir = (math.cos(wind + 1.2), math.sin(wind + 1.2))
        self.ripple_f = TWO_PI / 0.38
        self.ripple_ph = 0.0
        self.seed_h = float(seed % 65521)
        self.cam = [0.0, 0.0]                   # world xz (fp64)
        self.snow = [0.0, 0.0, 0.0]             # integrated snow drift
        self.y_cam = None
        self.head_ph = rng.uniform(0.0, 10.0)
        self.cam_ph = rng.uniform(0.0, 10.0)
        self.sway = 0.0
        self.drift = 0.0
        self.e_s = None
        # sun in air: high, ahead and to the right of the glide direction (+z)
        el, az = math.radians(58.0), math.radians(24.0)
        self.L_air = np.array([math.sin(az) * math.cos(el), math.sin(el), math.cos(az) * math.cos(el)])
        T = _refract(-self.L_air, (0.0, 1.0, 0.0), 1.0 / 1.33)
        self.T = T / np.linalg.norm(T)

    # bilinear wrapped sample of the host copy of the heightfield (texel coords)
    def _tex(self, u: float, v: float) -> float:
        u -= 0.5; v -= 0.5
        fu, fv = math.floor(u), math.floor(v)
        tx, ty = u - fu, v - fv
        x0, y0 = int(fu) & (TEXN - 1), int(fv) & (TEXN - 1)
        x1, y1 = (x0 + 1) & (TEXN - 1), (y0 + 1) & (TEXN - 1)
        H = self.hnp
        return float((1 - ty) * ((1 - tx) * H[y0, x0] + tx * H[y0, x1]) + ty * ((1 - tx) * H[y1, x0] + tx * H[y1, x1]))

    def _bed(self, X: float, Z: float, relief: float) -> float:
        sa, sb = TEXN / TA, TEXN / TB
        return relief * (self._tex(X * sa, Z * sa) + 0.8 * self._tex(Z * sb, X * sb))

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        a = 1.0 - math.exp(-dt / 0.3) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s

        depth = float(p["depth"])
        relief = float(p["seabed_relief"])
        turb = float(p["turbidity"])
        tint = float(p["water_tint"])
        cscale = float(p["caustic_scale"])
        surf = 5.0 + 9.0 * depth

        # ---- glide (heading wanders slowly), integrated in fp64
        self.head_ph += spd * (0.025 + 0.04 * e) * dt
        self.cam_ph += spd * (0.10 + 0.20 * e) * dt
        yaw = 0.32 * math.sin(self.head_ph) + 0.12 * math.sin(2.3 * self.head_ph + 1.0)
        v = float(p["glide_speed"]) * spd * (0.25 + 0.9 * e)
        dx, dz = math.sin(yaw) * v * dt, math.cos(yaw) * v * dt
        self.cam[0] += dx
        self.cam[1] += dz
        cx, cz = self.cam

        # ---- surface waves: chop (amplitude) and clock rate from energy, pulse kicks the rate
        chop = 0.7 + 0.6 * es ** 1.2
        rate = spd * (0.35 + 0.9 * e + 0.6 * pulse)
        ks = self.w_k0 * cscale
        kx, kz = ks * np.cos(self.w_ang), ks * np.sin(self.w_ang)
        self.w_clock += self.w_om * rate * dt
        self.w_cam += kx * dx + kz * dz
        amp = np.array(WAVE_KAP) * chop / (ks * ks)
        phase = np.mod(self.w_cam - self.w_clock + self.w_ph0, TWO_PI)
        self.ripple_ph = wrap(self.ripple_ph + self.ripple_f * (self.ripple_dir[0] * dx + self.ripple_dir[1] * dz), TWO_PI)

        # ---- marine snow drift (sinking + a slow current), kelp sway, colour drift
        sv = spd * (0.25 + 1.0 * e)
        self.snow[0] += 0.12 * sv * dt
        self.snow[1] += -0.10 * sv * dt
        self.snow[2] += -0.06 * sv * dt
        self.sway = wrap(self.sway + spd * (0.45 + 0.8 * e + 0.3 * pulse) * dt, TWO_PI * 5.0)
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # ---- camera: hover over the seabed (looking ahead), gentle sway
        fx, fz = math.sin(yaw), math.cos(yaw)
        bed_now = self._bed(cx, cz, relief)
        bed_ahead = max(bed_now, self._bed(cx + 2.5 * fx, cz + 2.5 * fz, relief),
                        self._bed(cx + 5.0 * fx, cz + 5.0 * fz, relief))
        sway = 0.3 + 0.7 * es
        y_t = bed_ahead + 1.7 + 0.35 * math.sin(0.6 * self.cam_ph)
        if self.y_cam is None:
            self.y_cam = y_t
        else:
            self.y_cam += (y_t - self.y_cam) * (1.0 - math.exp(-dt / 1.1) if dt > 0 else 0.0)
        self.y_cam = min(max(self.y_cam, bed_now + 0.6), surf - 1.0)
        pitch = -0.17 + 0.07 * sway * math.sin(0.37 * self.cam_ph + 0.5)
        yv = yaw + 0.10 * sway * math.sin(self.cam_ph)
        cpch = math.cos(pitch)
        eye = (0.0, self.y_cam, 0.0)
        tgt = (math.sin(yv) * cpch, self.y_cam + math.sin(pitch), math.cos(yv) * cpch)
        roll = 0.025 * sway * math.sin(0.53 * self.cam_ph + 0.4)
        cam = camera_pack(eye, tgt, fov_deg=62.0, roll=roll)

        # ---- water optics and colours (palette-tinted, luminance matched)
        coeffs = palette_coeffs(p["palette"])
        per = float(p["palette_period"])
        dd = depth ** 0.8
        base_w = (1.0 - dd) * np.array([0.010, 0.20, 0.20]) + dd * np.array([0.002, 0.024, 0.08])
        pw = _pal_lin(self.drift + 0.15 * depth * per, coeffs)
        pw = pw * (_lum(base_w) / max(_lum(pw), 1e-4))
        wcol = (1.0 - tint) * base_w + tint * pw
        sun_strength = 2.6 * (1.0 - 0.35 * depth)
        ps = _pal_lin(self.drift + 0.45 * per, coeffs)
        ps = ps / max(_lum(ps), 1e-4)
        scol = sun_strength * ((1.0 - 0.35 * tint) * np.array([1.0, 0.96, 0.86]) + 0.35 * tint * ps)
        absorb = (0.6 + 0.9 * depth) * np.array([0.22, 0.045, 0.03])
        scat = 0.03 + 0.09 * turb
        ext = absorb * 0.6 + scat
        d_cam = max(surf - self.y_cam, 0.0)
        invTy = 1.0 / max(-self.T[1], 0.2)
        snow_col = (scol * np.exp(-absorb * d_cam * invTy) * 0.45 + wcol * 2.5) * (0.6 + 0.6 * e)

        sd = float(p["shaft_density"])
        H0 = relief * 1.8
        Ph = self.Ph
        Ph[:] = 0.0
        Ph[0:13] = cam
        Ph[13:25] = coeffs
        Ph[25] = per
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"]) * 1.2
        Ph[28] = surf
        Ph[29] = scat
        Ph[30:33] = absorb
        Ph[33:36] = ext
        Ph[36:39] = self.T
        Ph[39:42] = wcol
        Ph[42:45] = scol
        Ph[45:48] = snow_col
        Ph[48] = 0.2 * sd * (0.45 + 0.75 * e) * (1.0 + 0.9 * pulse)
        Ph[49] = 1.4 + 1.1 * min(sd, 1.0)
        Ph[50] = 1.5
        Ph[51] = 0.30 / (1.0 + 2.2 * pulse)
        Ph[52] = 0.9 * (1.0 + 0.3 * pulse)
        Ph[53] = 1.6
        Ph[54] = 0.25
        Ph[55] = relief
        Ph[56] = H0 * min(self.hmin_t, 0.0) - 0.05
        Ph[57] = H0 * max(self.hmax_t, 0.0) + 0.05
        Ph[58] = 0.42 * float(p["kelp"])
        Ph[59] = self.sway
        Ph[60] = 0.3 + 0.5 * es
        Ph[61] = cx % TA
        Ph[62] = cz % TA
        Ph[63] = cx % TB
        Ph[64] = cz % TB
        Ph[65] = cx % (KC * 64)
        Ph[66] = cz % (KC * 64)
        Ph[67] = (cx - self.snow[0]) % (SC * 32)
        Ph[68] = (-self.snow[1]) % (SC * 32)
        Ph[69] = (cz - self.snow[2]) % (SC * 32)
        Ph[70] = self.ripple_ph
        Ph[71:73] = self.ripple_dir
        Ph[73] = self.ripple_f
        Ph[74] = 0.35 + 0.3 * e
        Ph[75] = pulse
        Ph[76] = e
        Ph[77] = self.seed_h
        Ph[78] = 0.6
        Ph[79:82] = self.L_air
        Ph[82] = float(ks.max())
        w4 = np.stack([kx, kz, amp, phase], axis=1).reshape(-1)
        Ph[84:84 + 4 * NWAVE] = w4
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P, self.HT, self.RT))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
        self.HT = None
        self.RT = None
