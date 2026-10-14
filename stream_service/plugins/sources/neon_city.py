"""Neon city: a night flight down an avenue between procedural setback towers (Blade Runner without the rain).

Source: https://www.shadertoy.com/view/XtsSWs ("Skyline"), https://www.shadertoy.com/view/WlKXzm ("Orbital
        Megastructure"), https://github.com/Rust-GPU/rust-gpu-shadertoys/blob/HEAD/shaders/src/skyline.rs
        (reference copy read: xscreensaver-6.17 hacks/glx/glsl/skyline.glsl)
Author: Otavio Good (Skyline, Orbital Megastructure); simplified by smfx
License: CC0-1.0 ("License CC0 - http://creativecommons.org/publicdomain/zero/1.0/" in the Skyline header)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: Skyline's block grid of setback towers with procedural window grids from
        the face uv and a per-window hash, street lights and distance haze are kept; the SDF march is replaced by an
        analytic grid DDA through the block cells (one building = up to three nested boxes + a blade sign per cell,
        <= 72 cells per ray), analytic box normals, no cars/trees/reflections; neon crowns, corner strips, blade
        signs with glyphs, traffic light trails, beat light waves and rooftop strobes are smfx additions.)

The city is a periodic grid of 1x1 blocks (4096 blocks long in z, so the flight distance wraps invisibly).  Each block
holds one tower made of 1-3 nested boxes (setbacks), sized by hashes, a "downtown" cluster field and the regional
knobs.  The camera flies down the avenue at x = 0, which is kept wider than the side streets and is never entered by
a building or sign, so the eye can never end up inside a solid.  The ground is intersected analytically: asphalt,
lane light trails (red tail lights going away, warm head lights coming), sodium lamp pools.  Fog is exp + exp^2,
tinted by the sky's light-polluted horizon glow plus a warm street-level in-scatter term.  Window, crown, lane and
lamp detail is box-filtered by the pixel footprint and fades to its mean with distance (no shimmer at 0.45 scale).

Macros: energy (e' = smoothstep(0.1, 0.85, e)) -> fly speed x(0.4+1.2e'), lit-window fraction (continuous threshold
with per-window fade, e'^1.5, slewed ~1.8 s), window flicker rate and depth, neon/shop/traffic brightness, sign
chase and traffic rates, spring-damped camera sway amplitude.  pulse -> host rising-edge detector (fire > 0.6 after
< 0.3, >= 150 ms apart) spawns a light wave that ripples out through the windows from the camera (radius grows at
its own rate, wave_kick gain) plus a rooftop strobe on a fresh hashed ~30 % of towers (strobe_kick gain); the eased
k = pulse*(0.3+0.7e) also kicks the fly rate (+50 %) and neon gain (+30 %).  speed scales every rate; palette:
neon = pal(hash(building)*period + drift), windows mix warm and cool with a touch of the building's neon.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, smoothstep, wrap
from stream_service.plugins._lib.upsample import internal_size, present

ZPERIOD = 4096          # the city repeats every 4096 blocks along z (camera z wraps there invisibly)
NWAVES = 4
NP = 72

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 fog density | 29 lit threshold
#  30 window columns per unit | 31 floors per unit | 32 street width | 33 building density | 34 height variance
#  35 max building height | 36 neon level | 37 flicker phase (2pi) | 38 flicker depth | 39 sign chase phase (1)
#  40 strobe level | 41 beat index | 42 shop level | 43 traffic phase (1) | 44 avenue half width | 45 seed key
#  46 street glow | 47 wave gain | 48..63 waves (x, z, radius, amp) | 64 sky level | 65,66 cloud offset
#  67,68 cluster phases | 69 window brightness | 70 mean lit fraction | 71 traffic level
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define MAXSTEPS 72
#define TFAR 80.f
#define ZMASK 4095u

__device__ __forceinline__ float u01(unsigned int h) { return (h >> 8) * (1.f / 16777216.f); }
// fraction of a pixel footprint [d - fw/2, d + fw/2] that lies inside [-w, w] (box-filtered band, energy preserving)
__device__ __forceinline__ float boxcov(float d, float w, float fw) {
    fw = fmaxf(fw, 1e-4f);
    return saturate((fminf(d + 0.5f * fw, w) - fmaxf(d - 0.5f * fw, -w)) / fw);
}

struct Bld { float xc, zc, hx, hz, h, s1, s2, hb, hs, hs2, hs3; int n; unsigned int key; };

// The tower in block (cx, cz).  Returns false for an empty lot.
__device__ __forceinline__ bool building(int cx, int cz, const float* __restrict__ P, Bld& b) {
    const unsigned int czm = (unsigned int)cz & ZMASK;
    unsigned int k = hash_u((unsigned int)cx * 0x9E3779B1u ^ hash_u(czm * 0x85EBCA77u ^ (unsigned int)P[45]));
    b.key = k;
    const float r0 = u01(k); k = hash_u(k ^ 0x68bc21ebu);
    const float r1 = u01(k); k = hash_u(k ^ 0x02e5be93u);
    const float r2 = u01(k); k = hash_u(k ^ 0x967a889bu);
    const float r3 = u01(k); k = hash_u(k ^ 0x0b5e8f2du);
    const float r4 = u01(k); k = hash_u(k ^ 0x5a1c3b7fu);
    const float r5 = u01(k); k = hash_u(k ^ 0x3c6ef372u);
    const float r6 = u01(k); k = hash_u(k ^ 0x1b873593u);
    b.hb = u01(k); k = hash_u(k ^ 0xcc9e2d51u);
    b.hs = u01(k); k = hash_u(k ^ 0xe6546b64u);
    b.hs2 = u01(k); k = hash_u(k ^ 0x85ebca6bu);
    b.hs3 = u01(k);
    const float dens = P[33];
    const float grow = smoothstep(r0, r0 + 0.15f, 0.22f + 0.88f * dens);      // towers rise out of the ground
    if (grow < 0.02f) return false;
    const float hv = P[34];
    const float cl = 0.5f + 0.5f * sinf((float)cx * 0.29f + P[67]) *
                     sinf(SDF_TAU * fract((float)czm * 0.0439453125f) + P[68]);   // 180 periods per 4096 blocks
    const float h = mix(1.3f + 0.3f * r2, 0.45f + 5.5f * r2 * r2, hv) * mix(1.f, 0.5f + cl, hv);
    b.h = h * grow;
    const float lot = 0.5f * (1.f - P[32]);
    const float fill = 0.8f + 0.2f * dens;
    b.hx = lot * mix(0.62f, 1.f, r1) * fill;
    b.hz = lot * mix(0.62f, 1.f, r3) * fill;
    b.xc = (float)cx + 0.5f + (lot - b.hx) * (2.f * r4 - 1.f);
    b.zc = (float)cz + 0.5f + (lot - b.hz) * (2.f * r6 - 1.f);
    b.n = 1 + (r5 > 0.3f ? 1 : 0) + (r5 > 0.68f ? 1 : 0);
    b.s1 = 0.68f + 0.14f * r6;
    b.s2 = 0.42f + 0.14f * r4;
    return true;
}
__device__ __forceinline__ float tier_top(const Bld& b, int i) {
    if (i >= b.n - 1) return b.h;
    if (b.n == 2) return 0.58f * b.h;
    return (i == 0 ? 0.4f : 0.7f) * b.h;
}
// Nested setback boxes; the two blocks beside the avenue (x = 0) are clipped back to keep it wide and clear.
__device__ __forceinline__ bool tier_box(const Bld& b, int i, int cx, float avh, float3& mn, float3& mx) {
    const float s = i == 0 ? 1.f : (i == 1 ? b.s1 : b.s2);
    mn = f3(b.xc - b.hx * s, 0.f, b.zc - b.hz * s);
    mx = f3(b.xc + b.hx * s, tier_top(b, i), b.zc + b.hz * s);
    if (cx == 0) mn.x = fmaxf(mn.x, avh);
    else if (cx == -1) mx.x = fminf(mx.x, -avh);
    return mx.x - mn.x > 0.03f;
}
// A blade sign sticking out of the tower's wall towards the avenue side, broad face along x (seen down the street).
__device__ __forceinline__ bool sign_box(const Bld& b, int cx, float avh, float3& mn, float3& mx) {
    if (b.hs2 > 0.45f) return false;
    float3 tm, tM;
    if (!tier_box(b, 0, cx, avh, tm, tM)) return false;
    const bool neg = cx >= 0;
    const float wall = neg ? tm.x : tM.x;
    const float cellb = neg ? (float)cx : (float)(cx + 1);
    const float ext = fminf(0.06f, fabsf(wall - cellb) - 0.004f);
    if (ext < 0.02f) return false;
    const float zs = tm.z + (tM.z - tm.z) * (0.18f + 0.64f * b.hs3);
    const float v0 = 0.2f + 0.15f * b.hs3;
    const float v1 = fminf(v0 + 0.35f + 1.6f * b.hs2, tM.y - 0.1f);
    if (v1 - v0 < 0.2f) return false;
    mn = f3(neg ? wall - ext : wall, v0, zs - 0.006f);
    mx = f3(neg ? wall : wall + ext, v1, zs + 0.006f);
    return true;
}
__device__ __forceinline__ float wave_at(float x, float z, const float* __restrict__ P) {
    float w = 0.f;
    #pragma unroll
    for (int i = 0; i < 4; ++i) {
        const float* q = P + 48 + 4 * i;
        if (q[3] > 0.003f) {
            const float d = sqrtf(square(x - q[0]) + square(z - q[1])) - q[2];
            const float wd = 0.35f + 0.05f * q[2];
            w = fmaxf(w, q[3] * expf(-d * d / (wd * wd)));      // overlapping waves combine with max
        }
    }
    return w;
}
__device__ __forceinline__ float3 sky_base(float y, float3 hor) {
    return mix(hor, f3(0.0025f, 0.0032f, 0.0065f), powf(saturate(y * 2.2f + 0.03f), 0.55f));
}
__device__ float3 sky_col(float3 rd, const float* __restrict__ P, float3 hor) {
    float3 c = sky_base(rd.y, hor);
    if (rd.y > 0.015f) {                                          // low cloud deck lit from below by the city
        const float s = 0.9f / (rd.y + 0.06f);
        const float nz = fbm(rd.x * s + P[65], rd.z * s + P[66], 0x2545u);
        const float cl = smoothstep(0.47f, 0.78f, nz) * smoothstep(0.015f, 0.12f, rd.y);
        c = mix(c, hor * 0.75f + f3(0.003f, 0.002f, 0.002f), cl * 0.8f);
    }
    return c;
}

__device__ float3 shade_ground(float3 pos, float t, float3 rd, const float* __restrict__ P, float pix, float3 sodium) {
    const float fw = t * pix / fmaxf(-rd.y, 0.03f);
    const float rx = roundf(pos.x), rz = roundf(pos.z);
    const float qx = pos.x - rx, qz = pos.z - rz;
    const float sw = 0.5f * P[32];
    const float hsx = (rx == 0.f) ? P[44] : sw;                   // the avenue is wider
    const float ax = fabsf(qx), az = fabsf(qz);
    const float inz = 1.f - smoothstep(hsx - 0.01f, hsx + 0.01f + fw, ax);
    const float inx = 1.f - smoothstep(sw - 0.01f, sw + 0.01f + fw, az);
    const float road = fmaxf(inz, inx);
    const float glow = P[46];
    float3 col = mix(f3(0.02f), f3(0.010f), road) * (f3(0.5f, 0.55f, 0.7f) + sodium * (glow * 0.8f));
    const float det = 1.f - smoothstep(0.08f, 0.3f, fw);          // fine detail -> its mean with distance
    float3 em = f3(0.f);
    {   // light trails along the z streets
        const float dir = qx > 0.f ? 1.f : -1.f;
        const float lane = boxcov(ax - hsx * 0.45f, 0.010f, fw);
        const float hl = hash1((unsigned int)(int)(rx * 2.f + (qx > 0.f ? 1.f : 0.f)) + 1000u, 0x77u);
        const float s = fract(dir * pos.z * 0.5f - P[43] * (1.f + floorf(hl * 2.f)) + hl);
        const float s2 = s * s, s4 = s2 * s2, s12 = s4 * s4 * s4;
        const float tr = mix(1.f / 13.f, s12, det);
        em += (dir > 0.f ? f3(1.f, 0.07f, 0.03f) : f3(1.f, 0.82f, 0.55f)) * (lane * tr * 2.2f * P[71]);
    }
    {   // light trails along the x streets
        const float dir = qz > 0.f ? -1.f : 1.f;
        const float lane = boxcov(az - sw * 0.45f, 0.010f, fw);
        const float hl = hash1((unsigned int)(int)(rz * 2.f + (qz > 0.f ? 1.f : 0.f)) + 7000u, 0x99u);
        const float s = fract(dir * pos.x * 0.5f - P[43] * (1.f + floorf(hl * 2.f)) + hl);
        const float s2 = s * s, s4 = s2 * s2, s12 = s4 * s4 * s4;
        const float tr = mix(1.f / 13.f, s12, det);
        em += (dir > 0.f ? f3(1.f, 0.07f, 0.03f) : f3(1.f, 0.82f, 0.55f)) * (lane * tr * 2.0f * P[71]);
    }
    {   // sodium lamp pools along the kerbs, one every half block
        const float ex = square(ax - (hsx - 0.025f)), ez = square(az - (sw - 0.025f));
        const float lz = (fract(pos.z * 2.f) - 0.5f) * 0.5f, lx = (fract(pos.x * 2.f) - 0.5f) * 0.5f;
        const float pool = expf(-(ex + lz * lz) * 160.f) + expf(-(ez + lx * lx) * 160.f);
        const float pmean = 0.28f * (expf(-ex * 160.f) + expf(-ez * 160.f));
        em += sodium * (glow * 0.9f * mix(pmean, pool, det));
    }
    return col + em;
}

__device__ float3 shade_building(float3 ro, float3 rd, float t, int cx, int cz, int tier, int axis,
                                 const float* __restrict__ P, float pix, float3 hor, float3 sodium) {
    Bld b;
    building(cx, cz, P, b);
    float3 mn, mx;
    if (tier == 3) sign_box(b, cx, P[44], mn, mx); else tier_box(b, tier, cx, P[44], mn, mx);
    const float3 pos = ro + rd * t;
    const float* PAL = P + 13;
    const float drift = P[26], period = P[25], neon = P[36];
    const float3 nc = srgb_to_linear(pal(b.hb * period + drift, PAL));
    const float3 n = axis == 0 ? f3(rd.x > 0.f ? -1.f : 1.f, 0.f, 0.f)
                   : (axis == 1 ? f3(0.f, 1.f, 0.f) : f3(0.f, 0.f, rd.z > 0.f ? -1.f : 1.f));
    const float facing = fmaxf(fabsf(dot(n, rd)), 0.12f);
    const float fw = t * pix / facing;                            // pixel footprint on the surface (world units)
    const float wv = wave_at(pos.x, pos.z, P) * P[47];
    const float3 wavec = srgb_to_linear(pal(drift + 0.5f, PAL)) * 1.6f + f3(0.35f);

    if (tier == 3) {                                              // blade sign: glyph rows, frame, chase light
        const float3 sc = srgb_to_linear(pal(b.hb * period + 0.37f + drift, PAL));
        const float3 fc = srgb_to_linear(pal(b.hb * period + 0.12f + drift, PAL));
        if (axis != 2) return sc * (0.5f * neon) + wavec * (wv * 0.3f);
        const float ext = mx.x - mn.x, L = mx.y - mn.y;
        const float u = (pos.x - mn.x) / ext, v = pos.y - mn.y;
        const float db = fminf(fminf(u * ext, (1.f - u) * ext), fminf(v, L - v));
        const float border = boxcov(db - 0.007f, 0.0045f, fw);
        const float gv = v * 16.f;
        const float gi = floorf(gv);
        const float gx = floorf(u * 2.f);
        const float on = hash1(b.key, (unsigned int)(int)gi * 2u + (unsigned int)(int)gx, 0x5u) > 0.4f ? 1.f : 0.f;
        const float cell = boxcov(fract(gv) - 0.5f, 0.32f, fw * 16.f) * boxcov(fract(u * 2.f) - 0.5f, 0.3f, fw * 2.f / ext);
        const float gdet = 1.f - smoothstep(0.3f, 0.8f, fw * 16.f);
        const float glyph = mix(0.25f, on * cell, gdet) * step(0.012f, db);
        const float ch = fract(gv * 0.0625f - P[39]);
        const float chase = 0.45f + 0.75f * ch * ch * ch * ch;
        return sc * (neon * 2.6f * glyph * chase) + fc * (neon * 3.0f * border) + sc * 0.03f + wavec * (wv * 0.5f);
    }

    const bool has_neon = b.hs > 0.18f;
    const float sel = hash1(b.key, (unsigned int)P[41], 0x51u) < 0.3f ? P[40] : 0.f;
    const float3 strobe_c = mix(f3(1.f), nc, 0.35f) * (3.f * sel);
    const float alb = 0.022f + 0.02f * b.hs;
    float3 em = f3(0.f);
    float3 col;
    if (axis == 1) {                                              // roof: neon outline, strobe wash
        const float de = fminf(fminf(pos.x - mn.x, mx.x - pos.x), fminf(pos.z - mn.z, mx.z - pos.z));
        const float edge = boxcov(de - 0.02f, 0.012f, fw);
        em = nc * (edge * neon * (has_neon ? 1.6f : 0.f)) + strobe_c * (0.3f + 0.7f * edge) + wavec * (wv * 0.25f);
        col = f3(0.04f, 0.045f, 0.06f) * alb;
        return col + em;
    }
    // vertical face
    const float u = axis == 0 ? pos.z - mn.z : pos.x - mn.x;
    const float Wf = fmaxf(axis == 0 ? mx.z - mn.z : mx.x - mn.x, 1e-3f);
    const unsigned int fid = (unsigned int)(axis * 2 + (n.x + n.z > 0.f ? 1 : 0));
    const float top = mx.y;
    // windows: columns fitted to the face width, floors on a common pitch from the ground
    const float ncol = fmaxf(1.f, floorf(Wf * P[30] + 0.5f));
    const float uu = u * ncol / Wf, vv = pos.y * P[31];
    const float iu = floorf(uu), iv = floorf(vv);
    const float fwu = fw * ncol / Wf, fwv = fw * P[31];
    const float cov = boxcov(uu - iu - 0.5f, 0.31f, fwu) * boxcov(vv - iv - 0.56f, 0.29f, fwv);
    const float zone = smoothstep(0.13f, 0.17f, pos.y) * (1.f - smoothstep(top - 0.075f, top - 0.055f, pos.y));
    const unsigned int wk = b.key ^ (fid * 0x27d4eb2du);
    const float hfl = hash1(wk, (unsigned int)(int)iv, 0x11u);   // whole floors tend to be lit together
    const float hw = hash1(wk, (unsigned int)(int)iu, (unsigned int)(int)iv);
    const float lit = saturate((P[29] - 0.5f * (hfl + hw)) * 12.f + 0.5f);
    const float hc = hash1(wk, (unsigned int)(int)iu * 7u + 3u, (unsigned int)(int)iv * 13u + 5u);
    const float3 cool = f3(0.50f, 0.70f, 1.0f), warm = f3(1.0f, 0.60f, 0.28f);
    const float bw = saturate(b.hs * 1.5f - 0.25f);
    const float3 wc = mix(mix(cool, warm, saturate(bw + (hc - 0.5f) * 0.7f)), nc, 0.18f);
    float bri = 0.55f + 0.9f * fract(hc * 7.31f);
    if (hc > 0.82f) {                                             // flickering tubes / TVs
        const float kf = 1.f + floorf(fract(hc * 13.7f) * 3.f);
        bri *= 1.f - P[38] * (0.5f + 0.5f * sinf(kf * P[37] + SDF_TAU * hw));
    }
    const float3 wexact = (wc * (bri * lit * P[69]) + hor * (0.25f * (1.f - lit)) + wavec * wv) * cov;
    const float3 wcm = mix(mix(cool, warm, bw), nc, 0.18f);
    const float3 wmean = (wcm * (P[70] * P[69]) + hor * (0.25f * (1.f - P[70])) + wavec * wv) * (0.62f * 0.58f);
    const float detail = 1.f - smoothstep(0.3f, 0.85f, fmaxf(fwu, fwv));
    em = mix(wmean, wexact, detail) * zone;
    // ground-floor shop fronts
    if (tier == 0) {
        const float shop = boxcov(pos.y - 0.065f, 0.045f, fw);
        const float3 shc = mix(f3(1.f, 0.7f, 0.4f), srgb_to_linear(pal(b.hb * period + 0.5f + drift, PAL)), 0.6f);
        em += shc * (shop * P[42] * (0.35f + 0.65f * b.hs3));
    }
    // neon crown at the top of every tier, corner strips on some towers, rooftop strobe on the crown
    const float crd = top - 0.035f - pos.y;
    if (has_neon) {
        em += nc * (boxcov(crd, 0.012f, fw) * neon * 2.2f);
        if (b.hs > 0.62f) {
            const float du = fminf(u, Wf - u);
            em += nc * (boxcov(du, 0.012f, fw) * neon * 1.4f * smoothstep(0.1f, 0.3f, pos.y));
        }
    }
    em += (strobe_c + wavec * (wv * 0.8f)) * boxcov(crd, 0.022f, fw);
    const float3 lightc = f3(0.035f, 0.04f, 0.06f) + sodium * (P[46] * 0.35f * expf(-pos.y * 1.6f))
                        + nc * (0.05f * neon * (has_neon ? 1.f : 0.3f));
    col = lightc * alb;
    return col + em;
}

SDF_KERNEL
void neon_city(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);
    const float* PAL = P + 13;
    const float drift = P[26];
    const float maxh = P[35], avh = P[44];

    const float tground = rd.y < -1e-5f ? -ro.y / rd.y : 1e9f;
    float tend = fminf(TFAR, tground);
    if (rd.y > 1e-5f) tend = fminf(tend, (maxh - ro.y) / rd.y);
    float tstart = 0.f;
    if (ro.y > maxh) tstart = rd.y < -1e-5f ? (ro.y - maxh) / -rd.y : 1e9f;

    // grid DDA through the blocks: one tower (<= 3 boxes) + an optional sign per block
    float thit = 1e9f;
    int hcx = 0, hcz = 0, htier = 0, haxis = 0;
    bool reached = tstart >= tend;
    if (!reached) {
        const float3 inv = sdf_safe_rcp(rd);
        const float3 o = ro + rd * tstart;
        int cx = (int)floorf(o.x), cz = (int)floorf(o.z);
        const int sx = rd.x > 0.f ? 1 : -1, sz = rd.z > 0.f ? 1 : -1;
        float tmx = ((float)(cx + (sx > 0 ? 1 : 0)) - ro.x) * inv.x;
        float tmz = ((float)(cz + (sz > 0 ? 1 : 0)) - ro.z) * inv.z;
        const float tdx = fabsf(inv.x), tdz = fabsf(inv.z);
        float t0 = tstart;
        for (int i = 0; i < MAXSTEPS; ++i) {
            const float t1 = fminf(fminf(tmx, tmz), tend);
            const float ylo = ro.y + rd.y * (rd.y < 0.f ? t1 : t0);
            if (ylo < maxh) {
                Bld b;
                if (building(cx, cz, P, b) && ylo < b.h) {
                    for (int k = 0; k < 4; ++k) {
                        float3 mn, mx;
                        if (k < 3) { if (k >= b.n || !tier_box(b, k, cx, avh, mn, mx)) continue; }
                        else if (!sign_box(b, cx, avh, mn, mx)) continue;
                        const float3 a = (mn - ro) * inv, c = (mx - ro) * inv;
                        const float3 lo = min(a, c), hi = max(a, c);
                        const float tn = vmax(lo), tf = vmin(hi);
                        if (tn <= tf && tf > 0.f && tn < thit) {
                            thit = fmaxf(tn, 1e-3f); hcx = cx; hcz = cz; htier = k;
                            haxis = (lo.x >= lo.y && lo.x >= lo.z) ? 0 : (lo.y >= lo.z ? 1 : 2);
                        }
                    }
                    if (thit < 1e8f) break;
                }
            }
            if (t1 >= tend) { reached = true; break; }
            if (tmx < tmz) { t0 = tmx; tmx += tdx; cx += sx; } else { t0 = tmz; tmz += tdz; cz += sz; }
        }
    }

    const float3 hor = mix(f3(0.30f, 0.12f, 0.045f), srgb_to_linear(pal(drift + 0.62f, PAL)), 0.45f) * P[64];
    const float3 sodium = f3(1.0f, 0.52f, 0.20f);
    float3 col;
    float t = -1.f;
    float yend = ro.y;
    if (thit < 1e8f) {
        t = thit;
        col = shade_building(ro, rd, t, hcx, hcz, htier, haxis, P, pix, hor, sodium);
        yend = ro.y + rd.y * t;
    } else if (reached && rd.y < -1e-5f && tground <= TFAR) {
        t = tground;
        col = shade_ground(ro + rd * t, t, rd, P, pix, sodium);
        yend = 0.f;
    } else if (reached && rd.y > 1e-5f) {
        col = sky_col(rd, P, hor);
    } else {
        t = TFAR;
        col = f3(0.f);
    }
    if (t > 0.f) {                                                // haze: horizon glow + warm street in-scatter
        float3 fogc = sky_base(fminf(rd.y, 0.12f), hor)
                    + sodium * (0.06f * P[46] * expf(-0.35f * (ro.y + yend)))
                    + srgb_to_linear(pal(drift + 0.2f, PAL)) * 0.012f;
        const float a = P[28] * t;
        col = mix(col, fogc, 1.f - expf(-(0.45f * a + a * a)));
    }
    col = vignette(col, x, y, W, H, 0.3f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
"""


def _tri_cdf(x: float) -> float:
    """CDF of the mean of two uniforms (the kernel's per-window lit hash)."""
    x = min(max(x, 0.0), 1.0)
    return 2.0 * x * x if x < 0.5 else 1.0 - 2.0 * (1.0 - x) ** 2


class NeonCity(Source):
    name = "neon_city"
    label = "Neon city"
    description = ("Night flight down an avenue of procedural setback towers: window grids, neon crowns and blade "
                   "signs, light trails and sodium lamps in haze; beats ripple a light wave through the windows (3D).")
    params = {
        **standard_scene_params(palette="neon", render_scale=0.45, period=1.0, drift=0.02),
        "building_density": num(0.0, 1.0, 0.8, "Building density", tier="regional"),
        "height_variance": num(0.0, 1.0, 0.65, "Height variance (flat -> skyscrapers)", tier="regional"),
        "street_width": num(0.12, 0.5, 0.26, "Street width", unit="blocks", tier="regional"),
        "camera_altitude": num(0.0, 1.0, 0.3, "Camera altitude (street -> rooftops)", tier="regional", basic=True),
        "haze": num(0.0, 1.0, 0.45, "Haze", tier="regional", basic=True),
        "window_density": num(0.0, 1.0, 0.5, "Window density", tier="regional"),
        "wave_kick": num(0.0, 2.0, 1.0, "Beat light wave", tier="regional"),
        "strobe_kick": num(0.0, 2.0, 1.0, "Rooftop strobe", tier="regional"),
        "fly_speed": num(0.0, 4.0, 1.0, "Fly speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("neon_city")
        self.P = cp.zeros(NP, cp.float32)
        self.Ph = np.zeros(NP, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.seed_key = float(rng.integers(0, 65536))
        self.cl_ph = rng.uniform(0.0, 2.0 * math.pi, 2)
        self.z = float(rng.uniform(0.0, ZPERIOD))           # flight distance (wraps at ZPERIOD with the city)
        self.cam_ph = float(rng.uniform(0.0, 2.0 * math.pi))
        self.flick = 0.0
        self.sign = 0.0
        self.traffic = 0.0
        self.cloud = 0.0
        self.drift = 0.0
        self.clock = 0.0
        self.lit = None                                     # slewed lit-window threshold
        self.sway = 0.75                                    # spring-damped sway amplitude
        self.sway_v = 0.0
        self.armed = True
        self.last_fire = -1.0
        self.beat = 0
        self.strobe = 0.0
        self.waves = np.zeros((NWAVES, 4), np.float64)      # x, z, radius, amp

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        ep = smoothstep(0.1, 0.85, e)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        kk = pulse * (0.3 + 0.7 * e)
        spd = float(p["speed"])
        dts = min(max(dt, 0.0), 0.25)

        # ---- slewed / sprung energy responses
        lit_t = 0.26 + 0.52 * ep ** 1.5
        a = 1.0 - math.exp(-dts / 1.8) if dts > 0 else 0.0
        self.lit = lit_t if self.lit is None else self.lit + (lit_t - self.lit) * a
        if dts > 0:
            tgt = 0.5 + 0.5 * ep
            nsub = min(3, max(1, int(math.ceil(dts * 60.0 - 1e-6))))
            hs = min(dts, 3.0 / 60.0) / nsub
            w0 = 1.8
            for _ in range(nsub):
                acc = w0 * w0 * (tgt - self.sway) - 2.0 * w0 * self.sway_v
                self.sway_v += acc * hs
                self.sway += self.sway_v * hs

        # ---- integrate rates (kicks add to rates only)
        rate = 0.4 + 1.2 * ep
        self.z += 1.3 * float(p["fly_speed"]) * spd * rate * (1.0 + 0.5 * kk) * dts
        if self.z >= ZPERIOD:
            self.z -= ZPERIOD
            self.waves[:, 1] -= ZPERIOD
        self.cam_ph += spd * (0.09 + 0.14 * ep) * dts
        self.flick = wrap(self.flick + spd * 2.4 * rate * dts, 2.0 * math.pi)
        self.sign = wrap(self.sign + spd * 0.3 * rate * dts, 1.0)
        self.traffic = wrap(self.traffic + spd * 0.45 * rate * dts, 1.0)
        self.cloud = math.fmod(self.cloud + spd * 0.012 * dts, 1.0e4)
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dts, 1000.0)
        self.clock += dts

        # ---- camera: down the avenue at x = 0 (never inside a block: lateral sway stays inside the avenue)
        alt = float(p["camera_altitude"])
        street = float(p["street_width"])
        avh = 0.5 * street + 0.12
        sw = self.sway
        lat = (avh - 0.07) * 0.85 * min(sw, 1.0) * math.sin(self.cam_ph)
        y = max(0.08, 0.14 + 3.3 * alt ** 1.3 + (0.04 + 0.14 * alt) * sw * math.sin(0.73 * self.cam_ph + 1.1))
        yaw = 0.07 * sw * math.cos(self.cam_ph) + 0.05 * sw * math.sin(0.37 * self.cam_ph + 0.6)
        pitch = 0.07 - 0.36 * alt ** 1.2 + 0.035 * sw * math.sin(0.53 * self.cam_ph + 2.0)
        eye = (lat, y, self.z)
        fwd = (math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch))
        cam = camera_pack(eye, (eye[0] + fwd[0], eye[1] + fwd[1], eye[2] + fwd[2]), fov_deg=64.0,
                          roll=-0.05 * sw * math.cos(self.cam_ph))

        # ---- beat spawns: rising edge with hysteresis -> light wave from the camera + rooftop strobe
        if pulse < 0.3:
            self.armed = True
        if self.armed and pulse > 0.6 and self.clock - self.last_fire >= 0.15:
            self.armed = False
            self.last_fire = self.clock
            self.beat = (self.beat + 1) % 65536
            amp = 0.45 + 0.55 * e
            i = int(np.argmin(self.waves[:, 3]))
            self.waves[i] = (lat, self.z, 0.0, amp)
            self.strobe = max(self.strobe, amp)
        if dts > 0:
            self.waves[:, 2] += 8.0 * spd * dts
            self.waves[:, 3] *= math.exp(-dts / 1.4)
            self.waves[self.waves[:, 3] < 0.003, 3] = 0.0
            self.strobe *= math.exp(-dts / 0.17)

        hv = float(p["height_variance"])
        maxh = (1.6 * (1.0 - hv) + 5.95 * hv) * (1.0 + 0.5 * hv) + 0.05
        wd = float(p["window_density"])
        haze = float(p["haze"])
        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"]) * 1.1
        Ph[28] = 0.022 + 0.063 * haze
        Ph[29] = self.lit
        Ph[30] = 5.0 + 9.0 * wd
        Ph[31] = 7.0 + 9.0 * wd
        Ph[32] = street
        Ph[33] = p["building_density"]
        Ph[34] = hv
        Ph[35] = maxh
        Ph[36] = (0.55 + 0.65 * ep) * (1.0 + 0.3 * kk)
        Ph[37] = self.flick
        Ph[38] = 0.3 + 0.5 * ep
        Ph[39] = self.sign
        Ph[40] = self.strobe * float(p["strobe_kick"])
        Ph[41] = self.beat
        Ph[42] = 0.55 + 0.45 * ep
        Ph[43] = self.traffic
        Ph[44] = avh
        Ph[45] = self.seed_key
        Ph[46] = 0.85 + 0.25 * ep
        Ph[47] = p["wave_kick"]
        Ph[48:64] = self.waves.reshape(-1)
        Ph[64] = 0.12 + 0.08 * haze
        Ph[65] = self.cloud
        Ph[66] = 0.37 * self.cloud
        Ph[67:69] = self.cl_ph
        Ph[69] = 0.95 + 0.35 * ep
        Ph[70] = _tri_cdf(self.lit + 0.5 / 12.0)
        Ph[71] = 0.7 + 0.6 * ep
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
