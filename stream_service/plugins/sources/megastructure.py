"""Alien megastructure: a low flight over an endless brutalist alien necropolis.

Source: https://github.com/mrange/windows-terminal-shader-gallery/tree/main/gallery/necropolis
        (metadata: https://github.com/mrange/windows-terminal-shader-gallery/blob/main/gallery/necropolis/metadata.json ;
        also https://www.shadertoy.com/view/DsdGDf)
Author: Marten Range (mrange, 'Necropolis', CC0); helpers Inigo Quilez (MIT), mercury hg_sdf ('MIT OR CC-BY-NC-4.0',
        taken as MIT), sam hocevar (WTFPL); helpers marked 'License: Unknown' are replaced with smfx HELPERS
License: CC0-1.0 + MIT + WTFPL
license_class: permissive
Ported from GLSL by smfx (ideas/changes: mrange's 6-rep KIFS terrace fold (box columns with a carved bowl and an
        orb, folded abs(xz) and doubled per level, smooth-min'd) and his trench glow lines are kept; the fold depth
        eases continuously (the last level rises out of the ground), level-0 boxes become per-cell towers, a
        neighbour-cell guard keeps the domain repetition safe, the floor is analytic, and new: lit window grids on
        every box face (per-window hashed, slowly re-seeded), slab seams, floating searchlight beacons whose beams
        sweep the slabs (analytic volumetric cones + surface pools), hard 12-step sun shadows, height haze,
        palette-driven colour.  sam hocevar's hsv2rgb, nmz's sRGB, the ACES approx and tanh_approx are replaced by
        the sdf3d toolkit and pal().)

Layout: everything in the kernel is in "structure units" (the city's cell is 4.5 units).  The camera flies +x along
the trench lane z = 2.25 between two rows of cells, so it can never hit a tower (their footprints stay >= 0.6 away
from the lane even at full lateral sway); altitude is in world units, so structure_scale changes how big the city is
relative to the flight.  The host integrates the flight in structure units and splits it into an integer cell
offset ix0 (hashed in-kernel) and a small fp32 local x, so long runs never lose precision; every x-pattern
(trench pulses, slab seams) is periodic in the 4.5-unit cell.

Macros: energy -> flight speed (0.3 + 1.5e), lit-window density (e^1.3), searchlight sweep rate (0.1 + 0.5e),
altitude swoop amplitude, window/trench/beam brightness.  pulse -> a window-grid flash (unlit panes flash too,
~35%) plus a searchlight intensity kick and small flight/sweep rate kicks (rates and gains only).  speed scales
flight, sweep, window re-seed and trench-flow rates; drift rotates the window, beam and trench palette;
intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, wrap
from stream_service.plugins._lib.sdf3d import smoothstep as hsmooth
from stream_service.plugins._lib.upsample import internal_size, present

CS = 4.5            # cell size (structure units)
NB = 4              # searchlight slots
BEAM_EVERY = 3      # a beacon every 3rd cell along the flight, alternating sides
MAX_STEPS = 64
LIFT_K = 2.8        # tallest tower lift (structure units) at tower_height 1

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 fog density | 29 fog height falloff
#  30 fold reps (float) | 31 tower height | 32 top bound | 33 window density | 34..36 sun dir | 37..39 sun col
#  40..42 horizon haze | 43 window epoch phase | 44 window glow | 45 window flash | 46..48 zenith
#  49..51 trench colour | 52 trench flow phase | 53, 54 trench width phases | 55..57 orb colour | 58 pulse
#  59 haze knob | 64..111 beams: 4 x [ox oy oz dx dy dz w L r g b pad]
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define CS (%(cs)ff)
#define HCS (%(hcs)ff)
#define OFF 1.125f
#define MAXREP 6
#define NB %(nb)d
#define WU 0.075f
#define WV 0.1f
#define MAXSTEPS %(steps)d
#define LIFTK (%(liftk)ff)

// mrange's ubox: a box column open downwards (top at b.y), |x|,|z| <= b.x, b.z
__device__ __forceinline__ float ubox(float3 p, float3 b) {
    const float3 q = f3(fabsf(p.x) - b.x, p.y - b.y, fabsf(p.z) - b.z);
    return length(max(q, 0.f)) + fminf(vmax(q), 0.f);
}
__device__ __forceinline__ float cell_lift(int ix, int iz, unsigned int seed, float th) {
    const float h = hash1((unsigned int)ix, (unsigned int)iz, seed);
    const float s = saturate((h - 0.45f) * (1.f / 0.55f));
    return th * LIFTK * s * s;
}
__device__ __forceinline__ float3 hue(float t, const float* __restrict__ PAL) {
    const float3 c = srgb_to_linear(pal(t, PAL));
    const float l = 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z;
    return min((c + f3(0.03f)) / (l + 0.03f), 4.f);
}
__device__ __forceinline__ float boxpulse(float f, float a, float b, float w) {
    return saturate((f - a) / w + 0.5f) - saturate((f - b) / w + 0.5f);
}

// The necropolis distance (structure units; p.x is local to cell column ix0).
__device__ float city(float3 p, const float* __restrict__ P, int ix0, unsigned int seed) {
    float3 q = p; q.y -= 0.5f;
    const float2 c = pMod2(q.x, q.z, CS, CS);
    const float db = HCS - fmaxf(fabsf(q.x), fabsf(q.z));
    const float lift = cell_lift((int)c.x + ix0, (int)c.y, seed, P[31]);
    float d = length(f3(q.x, q.y - 0.5f - lift, q.z)) - 0.5f;          // the orb sitting in the top bowl
    const float reps = P[30];
    float dc = 1e6f, sc = 1.f;
    #pragma unroll
    for (int i = 0; i < MAXREP; ++i) {
        const float w = saturate(reps - (float)i);
        if (w <= 0.f) break;
        const float top = 1.f - (1.f - w) * 2.2f + (i == 0 ? lift : 0.f);  // a fading level sinks into the ground
        const float d0 = ubox(q, f3(1.f, top, 1.f)) - 0.033f;
        const float d4 = length(f3(q.x, q.y - top, q.z)) - 1.1f;
        dc = smin(dc, smax(d0, -d4, 0.2f) * sc, 0.25f * sc);
        q.x = fabsf(q.x) - OFF; q.z = fabsf(q.z) - OFF; q.y += 0.25f;
        q = q * 2.f; sc *= 0.5f;
    }
    d = fminf(d, dc);
    // neighbour-cell guard: a taller tower next door sits >= 1.2 inside its own cell (only matters above y 1.45)
    return fminf(d, db + 1.15f + fmaxf(1.45f - p.y, 0.f) * 4.f);
}

struct MatInfo { float3 q; float sc, top, lift; int level, cx, cz; unsigned int branch; bool orb; };
__device__ MatInfo city_mat(float3 p, const float* __restrict__ P, int ix0, unsigned int seed) {
    MatInfo m;
    float3 q = p; q.y -= 0.5f;
    const float2 c = pMod2(q.x, q.z, CS, CS);
    m.cx = (int)c.x + ix0; m.cz = (int)c.y;
    m.lift = cell_lift(m.cx, m.cz, seed, P[31]);
    const float dorb = length(f3(q.x, q.y - 0.5f - m.lift, q.z)) - 0.5f;
    m.q = q; m.sc = 1.f; m.top = 1.f; m.level = 0; m.branch = 1u;
    const float reps = P[30];
    float best = 1e9f, sc = 1.f;
    unsigned int br = 1u;
    #pragma unroll 1
    for (int i = 0; i < MAXREP; ++i) {
        const float w = saturate(reps - (float)i);
        if (w <= 0.f) break;
        const float top = 1.f - (1.f - w) * 2.2f + (i == 0 ? m.lift : 0.f);
        const float d0 = ubox(q, f3(1.f, top, 1.f)) - 0.033f;
        const float d4 = length(f3(q.x, q.y - top, q.z)) - 1.1f;
        const float dd = smax(d0, -d4, 0.2f) * sc;
        if (dd < best) { best = dd; m.q = q; m.sc = sc; m.top = top; m.level = i; m.branch = br; }
        br = br * 4u + (q.x < 0.f ? 1u : 0u) + (q.z < 0.f ? 2u : 0u);
        q.x = fabsf(q.x) - OFF; q.z = fabsf(q.z) - OFF; q.y += 0.25f;
        q = q * 2.f; sc *= 0.5f;
    }
    m.orb = dorb < best;
    return m;
}

// Lit window grid on a vertical box face.  Returns emission (linear); 0 off the faces.
__device__ float3 windows(const MatInfo& m, float3 pos, float3 n, float foot, const float* __restrict__ P,
                          unsigned int seed, float& glass) {
    glass = 0.f;
    const float3 q = m.q;
    const bool xface = fabsf(q.x) > fabsf(q.z);
    const float facing = xface ? fabsf(q.x) : fabsf(q.z);
    if (fabsf(n.y) > 0.4f || facing < 0.93f) return f3(0.f);
    const float ul = xface ? q.z : q.x;
    const float below_top = (m.top - q.y) * m.sc;                     // structure units under this box's roof
    if (below_top < 0.07f || pos.y < 0.06f) return f3(0.f);
    const unsigned int face = xface ? (q.x > 0.f ? 0u : 1u) : (q.z > 0.f ? 2u : 3u);
    const unsigned int key = hash_u(m.branch * 977u + face * 131u) ^
                             hash_u((unsigned int)m.cx * 7919u + (unsigned int)m.cz * 104729u + seed);
    if (hash1(key, 5u) < 0.16f) return f3(0.f);                       // dark monoliths
    const float edge = 1.f - smoothstep(0.86f, 0.92f, fabsf(ul));     // no panes on the corners
    const float gu = ul * m.sc * (1.f / WU) + 0.5f, gv = pos.y * (1.f / WV);
    const float iu = floorf(gu), iv = floorf(gv);
    const float fu = gu - iu, fv = gv - iv;
    const float fwu = fmaxf(foot * (1.f / WU), 1e-3f), fwv = fmaxf(foot * (1.f / WV), 1e-3f);
    const float pane = boxpulse(fu, 0.17f, 0.83f, fwu) * boxpulse(fv, 0.22f, 0.78f, fwv);
    const float far = smoothstep(0.3f, 0.8f, fmaxf(fwu, fwv));
    const float avg = 0.66f * 0.56f;
    const unsigned int wid = (unsigned int)((int)iu + 4096) * 8192u + (unsigned int)((int)iv + 256);
    const float h1 = hash1(wid, key, 1u), h2 = hash1(wid, key, 2u), tone = hash1(wid, key, 4u);
    const float ep = P[43] * (0.5f + h1) + h2 * 17.f;
    const float k = floorf(ep), fr = ep - k;
    const float dens = P[33];
    const float la = smoothstep(dens + 0.03f, dens - 0.03f, hash1(wid, key ^ (unsigned int)(int)k, 3u));
    const float lb = smoothstep(dens + 0.03f, dens - 0.03f, hash1(wid, key ^ (unsigned int)(int)(k + 1.f), 3u));
    const float lit = mix(la, lb, smoothstep(0.82f, 1.f, fr));
    const float bh = hash1(key, 6u);
    const float3 wc = hue(P[26] + 0.08f + P[25] * (0.22f * bh + 0.08f * tone), P + 13);
    const float bright = (0.45f + 0.95f * tone * tone) * P[44];
    const float near_e = pane * (lit * bright) + pane * P[45];
    const float far_e = avg * (dens * 0.75f * P[44] + P[45]);
    glass = mix(pane, avg, far) * edge;
    return wc * (mix(near_e, far_e, far) * edge);
}

__device__ __forceinline__ float3 fog_col(float3 rd, const float* __restrict__ P) {
    const float3 sun = load3(P + 34), sunc = load3(P + 37);
    const float sd = saturate(dot(rd, sun));
    const float s2 = sd * sd, s6 = s2 * s2 * s2;
    return load3(P + 40) + sunc * (0.025f * s6 + 0.05f * powf(sd, 48.f));
}
__device__ __forceinline__ float3 sky(float3 rd, const float* __restrict__ P) {
    const float3 sun = load3(P + 34), sunc = load3(P + 37);
    const float sd = saturate(dot(rd, sun));
    float3 c = mix(fog_col(rd, P), load3(P + 46), sqrtf(saturate(rd.y * 1.6f)));
    return c + sunc * (1.5f * smoothstep(0.9994f, 0.9997f, sd));
}
// height haze: density(y) = dens * (0.25 + 2 exp(-hf y)), integrated analytically along the ray
__device__ __forceinline__ float fog_amount(float3 ro, float3 rd, float t, float dens, float hf) {
    const float k = hf * rd.y;
    const float e0 = expf(-hf * ro.y);
    const float integ = fabsf(k) > 1e-4f ? e0 * (1.f - expf(-k * t)) / k : e0 * t;
    return 1.f - expf(-dens * (0.25f * t + 2.f * integ));
}

SDF_KERNEL
void megastructure(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
                   const int ix0, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);
    auto map = [&](float3 p) { return city(p, P, ix0, seed); };

    const float TFAR = 34.f;
    const float topb = P[32];
    const float tfloor = rd.y < -1e-4f ? -ro.y / rd.y : 1e9f;
    float t0 = 0.02f, t1 = fminf(TFAR, tfloor);
    if (ro.y > topb) {
        if (rd.y >= -1e-4f) t1 = -1.f;                                // looking up from above the city: sky
        else t0 = fmaxf(t0, (ro.y - topb) / -rd.y);
    } else if (rd.y > 1e-4f) {
        t1 = fminf(t1, (topb - ro.y) / rd.y);
    }
    float t = t0, bd = 1e9f, bt = t0;
    int it = 0;
    bool hit = false, ran_out = false;
    if (t0 < t1) {
        for (it = 0; it < MAXSTEPS; ++it) {
            const float d = map(ro + rd * t);
            if (d < bd) { bd = d; bt = t; }
            if (d < 0.6f * pix * t) { hit = true; break; }
            t += d;
            if (t > t1) break;
        }
        if (!hit && it >= MAXSTEPS) { ran_out = true; t = bt; hit = true; }   // as the original: closest approach
    }
    const bool hit_floor = !hit && t0 < t1 && tfloor <= TFAR && t >= tfloor - 1e-3f;
    if (hit_floor) t = tfloor;

    const float* PAL = P + 13;
    const float3 sun = load3(P + 34), sunc = load3(P + 37);
    const float3 hz = load3(P + 40);
    const float3 trc = load3(P + 49);
    const float pulse = P[58];
    float3 col;
    const bool surf = hit || hit_floor;
    if (surf) {
        const float3 pos = ro + rd * t;
        float3 n, alb, emis = f3(0.f);
        float ao = 1.f, speck = 0.12f, shin = 14.f;
        if (hit) {
            n = calc_normal(map, pos, fmaxf(0.5f * pix * t, 1e-4f));
            const MatInfo m = city_mat(pos, P, ix0, seed);
            const float foot = t * pix / fmaxf(fabsf(dot(n, rd)), 0.2f);
            if (m.orb) {                                               // obsidian orb with a palette rim glow
                const float fre = 1.f - saturate(dot(n, -rd));
                alb = f3(0.02f);
                speck = 1.2f; shin = 60.f;
                emis = load3(P + 55) * (0.06f + 0.9f * fre * fre * fre);
            } else {
                const unsigned int bk = hash_u(m.branch * 977u) ^ hash_u((unsigned int)m.cx * 7919u + (unsigned int)m.cz * 104729u + seed);
                const float var = 0.72f + 0.5f * hash1(bk, 9u);
                alb = f3(0.115f, 0.118f, 0.13f) * var * (m.level == 0 && m.lift > 0.3f ? 0.75f : 1.f);
                if (n.y > 0.7f) {                                      // poured-slab seams on the terraces
                    const float sx = fabsf(fract(pos.x * 2.f + 0.5f) - 0.5f), sz = fabsf(fract(pos.z * 2.f + 0.5f) - 0.5f);
                    const float fw = fmaxf(foot * 2.f, 1e-3f);
                    const float line = 1.f - smoothstep(0.02f, 0.02f + fw, fminf(sx, sz));
                    alb = alb * (1.f - 0.5f * line * (1.f - smoothstep(0.1f, 0.3f, fw)));
                }
                float glass;
                emis = windows(m, pos, n, foot, P, seed, glass);
                alb = alb * (1.f - 0.6f * glass);                      // dark glass panes
                speck = 0.12f + 0.5f * glass;
            }
            ao = calc_ao(map, pos, n, 0.3f, 3);
        } else {
            n = f3(0.f, 1.f, 0.f);
            alb = f3(0.05f, 0.052f, 0.06f);
        }
        const float ndl = dot(n, sun);
        const float sh = (ndl > 0.f && t < 26.f) ? soft_shadow(map, pos + n * 0.012f, sun, 0.02f, 9.f, 18.f, 12) : 0.f;
        const float dif = saturate(ndl) * sh;
        const float spe = powf(saturate(dot(reflect(rd, n), sun)), shin) * sh;
        float3 light = sunc * dif + hz * (1.6f * (0.55f + 0.45f * n.y) * ao) + f3(0.004f, 0.0045f, 0.006f) * ao;
        // searchlight pools on the slabs (no occlusion: cheap, and the beacons sit above the roofs)
        #pragma unroll 1
        for (int b = 0; b < NB; ++b) {
            const float* B = P + 64 + 12 * b;
            const float w = B[6];
            if (w < 1e-3f) continue;
            const float3 o = load3(B), bdir = load3(B + 3);
            const float3 v = pos - o;
            const float s = dot(v, bdir);
            if (s <= 0.f || s > B[7] + 0.5f) continue;
            const float rad = 0.06f + 0.07f * s;
            const float perp = length(v - bdir * s);
            const float cone = smoothstep(rad * 1.15f, rad * 0.6f, perp);
            const float lam = saturate(dot(n, -bdir)) * 0.85f + 0.15f;
            light += load3(B + 8) * (w * cone * lam * fminf(0.3f / (rad * rad), 8.f));
        }
        col = alb * light + sunc * (spe * speck) + emis;
        // mrange's step-count glow: a faint palette sheen where the march crawled along an edge
        const float ii = (float)it / (float)MAXSTEPS;
        col += trc * (6.f * ii * ii) * (ran_out ? 0.5f : 1.f);
    } else {
        col = sky(rd, P);
    }

    // trench glow lines (mrange): distance at the floor to the trench lines, leaking over nearby occluders
    if (rd.y < -1e-4f && tfloor < 1e8f) {
        const float3 bp = ro + rd * tfloor;
        const float tv = surf ? t : TFAR;
        const float occl = fmaxf(tfloor - tv, 0.001f) * 0.5f;
        const float ph = P[52] * SDF_TAU;
        const float kx = SDF_TAU / CS;
        const float wvz = 0.5f + 0.5f * sinf(9.f * kx * bp.x + P[53]) * sinf(4.f * kx * bp.x + P[54]);
        const float wvx = 0.5f + 0.5f * sinf(9.f * kx * bp.z + P[53]) * sinf(4.f * kx * bp.z + P[54]);
        const float gdz = fabsf(mod(bp.z, CS) - HCS) - mix(0.0025f, 0.012f, wvz);
        const float gdx = fabsf(mod(bp.x, CS) - HCS) - mix(0.0025f, 0.012f, wvx);
        const float pk = 0.55f + 0.9f * powf(0.5f + 0.5f * sinf(2.f * kx * bp.x + ph), 6.f);
        const float cap = 0.0002f * tfloor * tfloor + 0.0004f;
        col += trc * (pk / fmaxf(gdz + occl, cap) + 0.45f / fmaxf(gdx + occl, cap));
    }

    // haze
    const float tfog = surf ? t : TFAR;
    if (surf) {
        float f = fog_amount(ro, rd, t, P[28], P[29]);
        f = fmaxf(f, smoothstep(TFAR * 0.55f, TFAR, t));
        col = mix(col, fog_col(rd, P), f);
    }

    // searchlight shafts (analytic closest approach of the view ray to each beam) and the beacon flares
    float3 vol = f3(0.f);
    const float ext = P[28] * 0.6f;
    #pragma unroll 1
    for (int b = 0; b < NB; ++b) {
        const float* B = P + 64 + 12 * b;
        const float w = B[6];
        if (w < 1e-3f) continue;
        const float3 o = load3(B), bdir = load3(B + 3), bc = load3(B + 8);
        const float L = B[7];
        const float3 w0 = ro - o;
        const float bb = dot(rd, bdir), dd = dot(rd, w0), ee = dot(bdir, w0);
        const float den = fmaxf(1.f - bb * bb, 1e-4f);
        float s = clamp((ee - bb * dd) / den, 0.f, L);
        float tc = clamp(dot(o + bdir * s - ro, rd), 0.f, tfog);
        s = clamp(dot(ro + rd * tc - o, bdir), 0.f, L);
        const float dist2 = dot2(ro + rd * tc - (o + bdir * s));
        const float rad = 0.06f + 0.07f * s;
        float g = expf(-dist2 / (rad * rad)) * 0.05f / (fmaxf(rad, 0.1f) * fmaxf(sqrtf(den), 0.15f));
        g = fminf(g, 0.6f) * expf(-0.045f * s) * expf(-ext * tc);
        // flare at the beacon itself
        const float to = dot(o - ro, rd);
        if (to > 0.f && to < tfog + 0.2f) {
            const float d2 = dot2(ro + rd * to - o);
            g += 0.0009f / (d2 + 0.0006f) * expf(-ext * to);
        }
        vol += bc * (g * w);
    }
    col += vol * (0.35f + 0.9f * P[59]);

    col = vignette(col, x, y, W, H, 0.4f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
""" % {"cs": CS, "hcs": CS * 0.5, "nb": NB, "steps": MAX_STEPS, "liftk": LIFT_K}


# ----------------------------------------------------------------------------- host helpers (mirror the kernel)
_M = 0xFFFFFFFF


def _hash_u(x: int) -> int:
    x &= _M
    x ^= x >> 16
    x = (x * 0x7FEB352D) & _M
    x ^= x >> 15
    x = (x * 0x846CA68B) & _M
    x ^= x >> 16
    return x


def _hash1(a: int, b: int = 0, c: int = 0) -> float:
    """Bit-exact copy of HELPERS hash1 (unsigned 32-bit arithmetic)."""
    inner = _hash_u((c + 0x27D4EB2D) & _M)
    mid = _hash_u(((b & _M) * 0x85EBCA77 & _M) ^ inner)
    return (_hash_u(((a & _M) * 0x9E3779B1 & _M) ^ mid) >> 8) * (1.0 / 16777216.0)


def _lift(ix: int, iz: int, seed: int, th: float) -> float:
    s = min(max((_hash1(ix, iz, seed) - 0.45) / 0.55, 0.0), 1.0)
    return th * LIFT_K * s * s


def _srgb_dec(c: float) -> float:
    c = min(max(c, 0.0), 1.0)
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _pal_lin(name: str, t: float) -> np.ndarray:
    a, b, c, d = PALETTES[name]
    return np.array([_srgb_dec(a[i] + b[i] * math.cos(2.0 * math.pi * (c[i] * t + d[i]))) for i in range(3)])


def _hue(name: str, t: float) -> np.ndarray:
    c = _pal_lin(name, t)
    lum = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    return np.minimum((c + 0.03) / (lum + 0.03), 4.0)


class Megastructure(Source):
    name = "megastructure"
    label = "Alien megastructure"
    description = ("A low flight over an endless brutalist alien necropolis of KIFS terraces, trenches and towers, "
                   "with hard sun shadows, haze, glowing window grids and sweeping searchlights (3D raymarched).")
    params = {
        **standard_scene_params(palette="ember", render_scale=0.5, period=1.0, drift=0.02),
        "window_density": num(0.0, 1.0, 0.55, "Lit windows", tier="regional", basic=True),
        "fold_complexity": num(2.0, 6.0, 6.0, "Fold complexity (terrace levels)", step=0.05, tier="regional"),
        "structure_scale": num(0.6, 1.5, 1.0, "Structure scale", tier="regional"),
        "tower_height": num(0.0, 1.0, 0.45, "Tower height", tier="regional"),
        "haze": num(0.0, 1.0, 0.45, "Haze", tier="regional"),
        "sun_angle": num(0.0, 1.0, 0.25, "Sun height (low -> high)", tier="regional"),
        "beam_glow": num(0.0, 2.0, 1.0, "Searchlights", tier="regional"),
        "trench_glow": num(0.0, 2.0, 1.0, "Trench glow", tier="regional"),
        "flight_speed": num(0.0, 3.0, 1.0, "Flight speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("megastructure")
        self.P = cp.zeros(112, cp.float32)
        self.Ph = np.zeros(112, np.float32)
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        rng = np.random.default_rng(self.seed)
        self.u = float(rng.uniform(0.0, 4000.0))   # flight position (structure units, float64 on the host)
        self.sway_ph = float(rng.uniform(0, 6.28))
        self.swoop_ph = float(rng.uniform(0, 6.28))
        self.yaw_ph = float(rng.uniform(0, 6.28))
        self.beam_ph = float(rng.uniform(0, 50.0))
        self.win_ph = 0.0
        self.flow = 0.0
        self.wph_a = 0.0
        self.wph_b = 0.0
        self.drift = 0.0
        self.e_s = None

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        a = 1.0 - math.exp(-dt / 0.35) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s
        S = float(p["structure_scale"])
        pal_name = p["palette"]
        period = float(p["palette_period"])

        # ---- integrate rates (pulse only kicks rates)
        v_world = float(p["flight_speed"]) * spd * (0.3 + 1.5 * e + 0.7 * pulse)
        self.u += v_world / S * dt
        self.sway_ph += spd * (0.08 + 0.14 * e) * dt
        self.swoop_ph += spd * (0.12 + 0.30 * e) * dt
        self.yaw_ph += spd * (0.05 + 0.08 * e) * dt
        self.beam_ph += spd * (0.10 + 0.50 * e + 0.4 * pulse) * dt
        self.win_ph += spd * (0.05 + 0.25 * e) * dt
        self.flow = wrap(self.flow + spd * (0.25 + 0.9 * e + 0.8 * pulse) * dt, 1.0)
        self.wph_a = wrap(self.wph_a + spd * 2.0 * dt, 2.0 * math.pi)
        self.wph_b = wrap(self.wph_b + spd * 3.0 * dt, 2.0 * math.pi)
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 10.0)
        drift = self.drift

        # ---- camera: down the trench lane, weaving, swooping with energy
        ix0 = int(math.floor(self.u / CS))
        u_loc = self.u - ix0 * CS
        A = 0.3 + 0.8 * es
        alt = 2.75 + A * (0.75 * math.sin(self.swoop_ph) + 0.25 * math.sin(2.3 * self.swoop_ph + 1.0))
        ey = alt / S
        ez = 0.5 * CS + 0.45 * math.sin(self.sway_ph)
        pitch = 0.36 + 0.12 * (alt - 2.75)
        yaw = (0.15 * math.sin(self.yaw_ph) + 0.05 * math.sin(2.7 * self.yaw_ph + 0.6)) * (0.6 + 0.4 * es)
        eye = (u_loc, ey, ez)
        fwd = (math.cos(pitch) * math.cos(yaw), -math.sin(pitch), math.cos(pitch) * math.sin(yaw))
        roll = -(0.03 + 0.05 * es) * math.cos(self.sway_ph)
        cam = camera_pack(eye, (eye[0] + fwd[0], eye[1] + fwd[1], eye[2] + fwd[2]), fov_deg=62.0, roll=roll)

        # ---- lighting and colours (palette rotated by drift)
        sa = float(p["sun_angle"])
        el = math.radians(5.0 + 55.0 * sa)
        sun = (0.45 * math.cos(el), math.sin(el), -0.89 * math.cos(el))
        warm = np.array([2.3, 1.45, 0.75]) * (1 - sa) + np.array([2.0, 1.9, 1.7]) * sa
        sunc = warm * (0.85 + 0.15 * _hue(pal_name, drift + 0.05)) * 0.9
        haze = float(p["haze"])
        hz_hue = _hue(pal_name, drift + 0.55)
        grey = float(np.mean(hz_hue))
        hz = (0.4 * hz_hue + 0.6 * grey) * (0.035 + 0.03 * haze) + np.array([0.004, 0.005, 0.008])
        zen = hz * 0.22 + np.array([0.001, 0.0012, 0.002])
        trc = _hue(pal_name, drift + 0.3 * period + 0.25) * 0.035 * float(p["trench_glow"]) * (0.6 + 0.7 * e) \
            * (1.0 + 0.5 * pulse)
        orbc = _hue(pal_name, drift + 0.6) * 0.5 * (1.0 + 0.6 * pulse)

        dens = min(1.0, float(p["window_density"]) * (0.2 + 0.8 * es ** 1.3) * 1.15)
        win_glow = (0.9 + 0.7 * e) * (1.0 + 0.5 * pulse) * 2.2
        win_flash = 0.55 * pulse
        th = float(p["tower_height"])
        reps = float(p["fold_complexity"])

        Ph = self.Ph
        Ph[:] = 0.0
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(pal_name)
        Ph[25] = period
        Ph[26] = drift
        Ph[27] = float(p["intensity"]) * 1.1
        Ph[28] = (0.03 + 0.2 * haze) * (1.1 - 0.2 * e)
        Ph[29] = 1.4
        Ph[30] = reps
        Ph[31] = th
        Ph[32] = 1.5 + LIFT_K * th + 0.12
        Ph[33] = dens
        Ph[34:37] = sun
        Ph[37:40] = sunc
        Ph[40:43] = hz
        Ph[43] = self.win_ph
        Ph[44] = win_glow
        Ph[45] = win_flash
        Ph[46:49] = zen
        Ph[49:52] = trc
        Ph[52] = self.flow
        Ph[53] = self.wph_a
        Ph[54] = self.wph_b
        Ph[55:58] = orbc
        Ph[58] = pulse
        Ph[59] = haze

        # ---- searchlight beacons: every 3rd cell along the lane, alternating sides, faded in far ahead / behind
        beam_lvl = float(p["beam_glow"]) * (0.5 + 0.6 * e) * (1.0 + 1.2 * pulse)
        span = BEAM_EVERY * CS
        k0 = int(math.floor((self.u - 6.0) / span))
        for m in range(NB):
            kk = k0 + m
            i = BEAM_EVERY * kk
            j = kk & 1
            cxg = i * CS
            dx = cxg - self.u
            w = hsmooth(-6.0, -2.0, dx) * (1.0 - hsmooth(17.0, 21.0, dx)) * beam_lvl
            if w <= 1e-3:
                continue
            r = [_hash1(i, j, (self.seed ^ 0xBEA0) + q) for q in range(6)]
            oy = 1.5 + _lift(i, j, self.seed, th) + 0.9
            byaw = self.beam_ph * (0.7 + 0.6 * r[0]) * (1.0 if r[1] > 0.5 else -1.0) + 6.283 * r[2]
            bel = -0.22 + 0.42 * math.sin(self.beam_ph * (0.45 + 0.3 * r[3]) + 6.283 * r[4])
            bdir = (math.cos(bel) * math.cos(byaw), math.sin(bel), math.cos(bel) * math.sin(byaw))
            L = min(oy / -bdir[1], 40.0) if bdir[1] < -1e-3 else 40.0
            bc = 0.6 * _hue(pal_name, drift + 0.4 + 0.3 * r[5] * period) + 0.4
            base = 64 + 12 * m
            Ph[base:base + 3] = (cxg - ix0 * CS, oy, j * CS)
            Ph[base + 3:base + 6] = bdir
            Ph[base + 6] = w
            Ph[base + 7] = L
            Ph[base + 8:base + 11] = bc

        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P,
                                           np.int32(ix0), np.uint32(self.seed)))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
