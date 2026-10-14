"""Murmuration: up to 32k GPU boids wheel around moving attractor curves, drawn as wing-flapping V silhouettes
against a dusk sky, morphing into a glowing fish shoal underwater; density waves and beat startles ripple through.

Source: https://github.com/mrdoob/three.js/blob/dev/examples/webgl_gpgpu_birds.html ;
        https://www.red3d.com/cwr/boids/
Author: three.js authors (webgl_gpgpu_birds); Craig Reynolds (boids model); CUDA port smfx
License: MIT
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the three.js GPGPU birds' all-pairs texture loop is replaced by a uniform
grid (hashed cells, atomic counting sort into preallocated buffers, one 27-cell neighbour pass with a candidate cap);
Reynolds' separation / alignment / cohesion are averaged with a smooth cosine zone falloff in the spirit of the
three.js zones; the mouse "predator" becomes beat-spawned startle shockwaves; birds steer around a host-animated
attractor curve (cloud / ring / figure-eight / knot, continuously morphed) with flow + swirl; density waves are a
travelling speed modulation; rendering is splat3d line splats with an analytic sky, not WebGL meshes.)

Pipeline per frame (all on the GPU, no host round trip):
  * sim (substepped, <= 3 x 1/45 s): hash -> scan -> scatter (sorted copies) -> boids (neighbours + attractor
    + waves + startle; speed and turn-rate clamped; NaN/escape respawn on the curve).
  * geometry: two segments per boid -- a flapping V (bird) lerped to a wiggling head/tail streak (fish) by
    ``environment``; per-segment linear colour (palette by local density, wave phase and a per-bird hash).
  * sky: analytic, straight into the 4K output: dusk (palette gradient, sun glow, sky-plane stratus, hill line)
    lerped to underwater (palette-tinted depth gradient, god-ray shafts, surface window).
  * splat3d (alpha): lines + (underwater) drifting motes; resolve over the sky with occlusion = dark silhouettes
    at dusk, additive bloom for the glowing shoal.

Macros: energy -> max speed, turning rate, flock spread (tube + spacing), attractor speed, flap rate, wave speed,
camera sway, glow.  pulse -> a rising edge spawns a moving startle point whose shockwave pushes birds outward as
it expands (flash expansion rippling through the flock); the envelope also boosts separation, a small sky flash
and the fish brightness.  speed scales sim time (dt clamped 0.1, substepped; dt = 0 -> no step).  palette /
period / drift colour the sky and the birds (session palette honoured).  intensity = output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.splat3d import Splat3D, fibonacci_sphere, look_at, mat_rot, mat_trs, orbit
from stream_service.plugins._lib.upsample import internal_size

N_MAX = 32768          # boids allocated
NB = 1 << 17           # grid hash buckets
NC = 64                # attractor curve samples
MAXC = 192             # neighbour candidates checked per boid (bounds the cost in dense clumps)
N_MOTES = 4096
FADE_BAND = 1500.0     # boids fading in/out at the flock_size edge

_FIELDS = ["H", "VMAX", "VMIN", "AMAX", "R", "ICELL", "RSEP", "KSEP", "KALI", "KCOH", "KATT", "TUBE", "KFLOW",
           "KSWIRL", "WAMP", "WK", "WPH", "WDX", "WDY", "WDZ", "FLAP", "NACT", "KSTART", "DRIFT", "PERIOD", "ENV",
           "BSIZE", "FLEN", "BRIGHT", "PULSE", "SEPB", "INTENS", "SUNAZ", "SUNEL", "SUNG", "RAYPH", "CLOUDPH",
           "CLOUDS"]
PR = {k: i for i, k in enumerate(_FIELDS)}
PR["PRED"] = 48        # 2 x (cx, cy, cz, radius, strength, width)
PR["CAM"] = 64         # 13 floats (sdf3d camera_pack layout)
PR["PAL"] = 80         # 12 palette coefficients
PR["CURVE"] = 96       # NC x (px, py, pz, tx, ty, tz)
PRM_SIZE = PR["CURVE"] + NC * 6
assert len(_FIELDS) <= PR["PRED"]

_DEFS = "\n".join(f"#define PR_{k} {v}" for k, v in PR.items())

_SRC = HELPERS + PALETTE_CUDA + f"""
#define NB_MASK {NB - 1}
#define NBUCK {NB}
#define NC {NC}
#define MAXC {MAXC}
#define FADE_BAND {FADE_BAND}f
{_DEFS}
""" + r"""
__device__ __forceinline__ float3 operator+(float3 a, float3 b) { return make_float3(a.x + b.x, a.y + b.y, a.z + b.z); }
__device__ __forceinline__ float3 operator-(float3 a, float3 b) { return make_float3(a.x - b.x, a.y - b.y, a.z - b.z); }
__device__ __forceinline__ float3 operator*(float3 a, float s) { return make_float3(a.x * s, a.y * s, a.z * s); }
__device__ __forceinline__ float3 mm_mix(float3 a, float3 b, float t) { return a + (b - a) * t; }
__device__ __forceinline__ float mm_dot(float3 a, float3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
__device__ __forceinline__ float3 mm_cross(float3 a, float3 b) {
    return make_float3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x);
}
__device__ __forceinline__ float3 mm_norm(float3 a) { return a * rsqrtf(mm_dot(a, a) + 1e-12f); }
__device__ __forceinline__ unsigned int mm_cell(int x, int y, int z) {
    return (((unsigned int)x * 73856093u) ^ ((unsigned int)y * 19349663u) ^ ((unsigned int)z * 83492791u)) & NB_MASK;
}
__device__ __forceinline__ float3 mm_lin(float3 c) {
    return make_float3(__powf(c.x, 2.2f), __powf(c.y, 2.2f), __powf(c.z, 2.2f));
}

// Startle shockwaves: outward push (sum of unit directions x gain) and the total gain.
__device__ __forceinline__ float mm_startle(const float* __restrict__ P, float3 p, float3& push) {
    float g = 0.f;
    push = make_float3(0.f, 0.f, 0.f);
    #pragma unroll
    for (int j = 0; j < 2; ++j) {
        const float* q = P + PR_PRED + 6 * j;
        const float s = q[4];
        if (s > 1e-4f) {
            const float3 d = make_float3(p.x - q[0], p.y - q[1], p.z - q[2]);
            const float r = sqrtf(mm_dot(d, d)) + 1e-4f;
            const float x = (r - q[3]) / q[5];
            const float gj = s * (__expf(-x * x) + 0.8f * __expf(-r * r * 10.f));
            push = push + d * (gj / r);
            g += gj;
        }
    }
    return g;
}

// ---- grid: hash -> exclusive scan -> scatter sorted copies
extern "C" __global__ void mm_hash(const float4* __restrict__ pos, const float* __restrict__ P,
                                   int* __restrict__ cell, int* __restrict__ rank, int* __restrict__ count,
                                   const int n) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const float ic = P[PR_ICELL];
    const float4 q = pos[i];
    const float x = fminf(fmaxf(q.x * ic, -1e6f), 1e6f), y = fminf(fmaxf(q.y * ic, -1e6f), 1e6f);
    const float z = fminf(fmaxf(q.z * ic, -1e6f), 1e6f);
    const unsigned int h = mm_cell((int)floorf(x), (int)floorf(y), (int)floorf(z));
    cell[i] = (int)h;
    rank[i] = atomicAdd(&count[h], 1);
}

extern "C" __global__ void __launch_bounds__(1024) mm_scan(const int* __restrict__ count, int* __restrict__ start) {
    __shared__ int s[1024];
    const int t = threadIdx.x, per = NBUCK / 1024, b = t * per;
    int sum = 0;
    for (int k = 0; k < per; ++k) sum += count[b + k];
    s[t] = sum;
    __syncthreads();
    for (int off = 1; off < 1024; off <<= 1) {
        const int v = t >= off ? s[t - off] : 0;
        __syncthreads();
        s[t] += v;
        __syncthreads();
    }
    int run = s[t] - sum;
    for (int k = 0; k < per; ++k) { start[b + k] = run; run += count[b + k]; }
}

extern "C" __global__ void mm_scatter(const float4* __restrict__ pos, const float4* __restrict__ vel,
                                      const int* __restrict__ cell, const int* __restrict__ rank,
                                      const int* __restrict__ start, float4* __restrict__ spos,
                                      float4* __restrict__ svel, const int n) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const int j = start[cell[i]] + rank[i];
    spos[j] = pos[i];
    svel[j] = vel[i];
}

// closest point on segment a-b (tangents ta-tb lerped)
__device__ __forceinline__ float mm_seg(float3 p, const float* a, const float* b, float3& c, float3& t) {
    const float3 A = make_float3(a[0], a[1], a[2]), B = make_float3(b[0], b[1], b[2]);
    const float3 ab = B - A;
    const float u = fminf(fmaxf(mm_dot(p - A, ab) / (mm_dot(ab, ab) + 1e-9f), 0.f), 1.f);
    c = A + ab * u;
    t = mm_norm(mm_mix(make_float3(a[3], a[4], a[5]), make_float3(b[3], b[4], b[5]), u));
    const float3 d = p - c;
    return mm_dot(d, d);
}

// ---- one boids step (reads neighbours from the sorted copies, writes pos/vel in place)
extern "C" __global__ void __launch_bounds__(128)
mm_boids(const float* __restrict__ P, float4* __restrict__ pos, float4* __restrict__ vel,
         const float4* __restrict__ spos, const float4* __restrict__ svel, const int* __restrict__ start,
         const int* __restrict__ count, const int n)
{
    __shared__ float cv[NC * 6];
    for (int k = threadIdx.x; k < NC * 6; k += blockDim.x) cv[k] = P[PR_CURVE + k];
    __syncthreads();
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const float h = P[PR_H];
    float4 q = pos[i], qv = vel[i];
    float3 p = make_float3(q.x, q.y, q.z), v = make_float3(qv.x, qv.y, qv.z);
    if (!(fabsf(p.x) + fabsf(p.y) + fabsf(p.z) < 300.f) || !(fabsf(v.x) + fabsf(v.y) + fabsf(v.z) < 300.f)
        || !(fabsf(q.w) < 100.f) || !(fabsf(qv.w) < 100.f)) {
        const int k = (int)(hash_u((unsigned int)i * 7919u) % NC);
        const float jx = hash1(i, 1u) - 0.5f, jy = hash1(i, 2u) - 0.5f, jz = hash1(i, 3u) - 0.5f;
        p = make_float3(cv[6 * k] + 0.3f * jx, cv[6 * k + 1] + 0.3f * jy, cv[6 * k + 2] + 0.3f * jz);
        v = make_float3(cv[6 * k + 3], cv[6 * k + 4], cv[6 * k + 5]) * P[PR_VMIN];
        pos[i] = make_float4(p.x, p.y, p.z, 0.f);
        vel[i] = make_float4(v.x, v.y, v.z, 6.2831853f * hash1(i, 4u));
        return;
    }
    // neighbours (27 cells, capped)
    const float R = P[PR_R], R2 = R * R, rs = P[PR_RSEP], ic = P[PR_ICELL];
    const int cx = (int)floorf(p.x * ic), cy = (int)floorf(p.y * ic), cz = (int)floorf(p.z * ic);
    float3 sep = make_float3(0.f, 0.f, 0.f), sv = sep, sc = sep;
    float wsum = 0.f, nn = 0.f;
    int checked = 0;
    for (int dz = -1; dz <= 1; ++dz)
        for (int dy = -1; dy <= 1; ++dy)
            for (int dx = -1; dx <= 1; ++dx) {
                const unsigned int hh = mm_cell(cx + dx, cy + dy, cz + dz);
                const int s0 = start[hh], c = min(count[hh], MAXC - checked);
                for (int k = 0; k < c; ++k) {
                    const float4 o = spos[s0 + k];
                    const float3 d = make_float3(o.x - p.x, o.y - p.y, o.z - p.z);
                    const float r2 = mm_dot(d, d);
                    if (r2 < R2 && r2 > 1e-12f) {
                        const float r = sqrtf(r2);
                        if (r < rs) sep = sep - d * ((rs - r) / (rs * r));
                        const float w = 0.5f + 0.5f * __cosf(3.14159265f * r / R);
                        const float4 ov = svel[s0 + k];
                        sv = sv + make_float3(ov.x, ov.y, ov.z) * w;
                        sc = sc + d * w;
                        wsum += w;
                        nn += 1.f;
                    }
                }
                checked += max(c, 0);
            }
    float3 a = make_float3(0.f, 0.f, 0.f);
    const float sl = sqrtf(mm_dot(sep, sep));
    a = a + sep * (P[PR_KSEP] * P[PR_SEPB] * (sl > 4.f ? 4.f / sl : 1.f));
    if (wsum > 1e-4f) {
        const float iw = 1.f / wsum;
        a = a + (sv * iw - v) * P[PR_KALI];
        a = a + sc * (iw * P[PR_KCOH]);
    }
    // attractor curve: nearest sample, then the closer adjacent segment
    float best = 1e30f;
    int kb = 0;
    for (int k = 0; k < NC; ++k) {
        const float3 d = make_float3(p.x - cv[6 * k], p.y - cv[6 * k + 1], p.z - cv[6 * k + 2]);
        const float d2 = mm_dot(d, d);
        if (d2 < best) { best = d2; kb = k; }
    }
    float3 c1, t1, c2, t2;
    const int kn = (kb + 1) % NC, kp = (kb + NC - 1) % NC;
    const float e1 = mm_seg(p, cv + 6 * kb, cv + 6 * kn, c1, t1);
    const float e2 = mm_seg(p, cv + 6 * kp, cv + 6 * kb, c2, t2);
    const float3 cc = e1 <= e2 ? c1 : c2, tt = e1 <= e2 ? t1 : t2;
    const float3 o = p - cc;
    const float tube = P[PR_TUBE];
    a = a - o * (P[PR_KATT] / tube);
    a = a + tt * P[PR_KFLOW];
    a = a + mm_cross(tt, o) * (P[PR_KSWIRL] / tube);
    // density waves: travelling speed modulation along the heading
    const float spd0 = sqrtf(mm_dot(v, v)) + 1e-5f;
    const float3 f = v * (1.f / spd0);
    const float wph = mm_dot(p, make_float3(P[PR_WDX], P[PR_WDY], P[PR_WDZ])) * P[PR_WK] - P[PR_WPH];
    a = a + f * (P[PR_WAMP] * __sinf(wph));
    // startle shockwaves
    float3 push;
    const float g = mm_startle(P, p, push);
    a = a + push * P[PR_KSTART];
    a.y -= 0.3f * v.y;                                            // murmurations stay sheet-like
    const float amax = P[PR_AMAX] * (1.f + 3.f * g);
    const float al = sqrtf(mm_dot(a, a));
    if (al > amax) a = a * (amax / al);
    v = v + a * h;
    const float vmax = P[PR_VMAX] * (1.f + 0.7f * fminf(g, 1.5f)), vmin = P[PR_VMIN];
    const float sp = sqrtf(mm_dot(v, v));
    if (sp > 1e-6f) v = v * (fminf(fmaxf(sp, vmin), vmax) / sp);
    else v = tt * vmin;
    p = p + v * h;
    const float fr = P[PR_FLAP] * (0.8f + 0.4f * hash1(i, 9u)) * (0.85f + 0.5f * fminf(al / P[PR_AMAX], 1.f));
    qv.w = fmodf(qv.w + h * fr, 6.2831853f);
    q.w += (fminf(nn * (1.f / 24.f), 2.f) - q.w) * (1.f - __expf(-h * 4.f));
    pos[i] = make_float4(p.x, p.y, p.z, q.w);
    vel[i] = make_float4(v.x, v.y, v.z, qv.w);
}

// ---- per-boid geometry: two segments (bird V <-> fish head/tail), linear colour per segment
extern "C" __global__ void mm_geom(const float* __restrict__ P, const float4* __restrict__ pos,
                                   const float4* __restrict__ vel, float4* __restrict__ p0, float4* __restrict__ p1,
                                   float* __restrict__ rgb, const int n)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const float4 q = pos[i], qv = vel[i];
    const float3 p = make_float3(q.x, q.y, q.z), v = make_float3(qv.x, qv.y, qv.z);
    const float sp = sqrtf(mm_dot(v, v));
    const float3 fw = sp > 1e-5f ? v * (1.f / sp) : make_float3(0.f, 0.f, 1.f);
    float3 side = make_float3(-fw.z, 0.f, fw.x);
    const float sl = mm_dot(side, side);
    side = sl > 1e-6f ? side * rsqrtf(sl) : make_float3(1.f, 0.f, 0.f);
    const float3 up = mm_cross(side, fw);
    const float hs = hash1(i, 5u);
    const float env = P[PR_ENV];
    const float ph = qv.w;
    // bird: V of two wings, flapping, swept back
    const float span = P[PR_BSIZE] * (0.75f + 0.5f * hs);
    const float th = 0.55f * __sinf(ph);
    const float cs = __cosf(th), sn = __sinf(th);
    const float3 head = p + fw * (0.3f * span);
    const float3 tipL = p + (side * (-cs) + up * sn) * span - fw * (0.4f * span);
    const float3 tipR = p + (side * cs + up * sn) * span - fw * (0.4f * span);
    // fish: head -> mid -> wagging tail
    const float L = P[PR_FLEN] * (0.75f + 0.5f * hs);
    const float3 fh = p + fw * (0.5f * L);
    const float3 fm = p + side * (0.05f * L * __sinf(ph));
    const float3 ft = p - fw * (0.5f * L) + side * (0.22f * L * __sinf(ph - 1.2f));
    const float3 a0 = mm_mix(tipL, fh, env), a1 = mm_mix(head, fm, env);
    const float3 b1 = mm_mix(tipR, ft, env);
    const float w = q.w > -1.f ? fminf(fmaxf(1.f - ((float)i - P[PR_NACT]) * (1.f / FADE_BAND), 0.f), 1.f) : 0.f;
    p0[2 * i] = make_float4(a0.x, a0.y, a0.z, 1.f);
    p1[2 * i] = make_float4(a1.x, a1.y, a1.z, 1.f);
    p0[2 * i + 1] = make_float4(a1.x, a1.y, a1.z, 1.f);
    p1[2 * i + 1] = make_float4(b1.x, b1.y, b1.z, 1.f);
    // colour: palette by local density, wave phase and a per-bird hash; startle flash
    float3 push;
    const float g = mm_startle(P, p, push);
    const float dens = fminf(fmaxf(q.w, 0.f), 2.f);
    const float wph = mm_dot(p, make_float3(P[PR_WDX], P[PR_WDY], P[PR_WDZ])) * P[PR_WK] - P[PR_WPH];
    const float wv = __sinf(wph);
    const float idx = 0.4f * dens + 0.25f * hs + 0.12f * wv + 0.1f * fw.y;
    const float3 pc = mm_lin(pal(idx * P[PR_PERIOD] + P[PR_DRIFT] + 0.08f, P + PR_PAL));
    const float fb = (0.45f + 0.55f * fminf(dens, 1.5f) + 0.15f * wv + 1.6f * fminf(g, 1.5f)) * P[PR_BRIGHT];
    const float3 bird = mm_mix(make_float3(1.f, 1.f, 1.f), pc, 0.25f);
    const float3 col = mm_mix(bird, pc * fb, env) * w;
    const float tail = 1.f - 0.3f * env;
    rgb[6 * i] = col.x; rgb[6 * i + 1] = col.y; rgb[6 * i + 2] = col.z;
    rgb[6 * i + 3] = col.x * tail; rgb[6 * i + 4] = col.y * tail; rgb[6 * i + 5] = col.z * tail;
}

// ---- analytic sky straight into the output (sRGB-encoded): dusk <-> underwater
extern "C" __global__ void mm_sky(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float* C = P + PR_CAM;
    const float th = C[12];
    const float u = (2.f * (x + 0.5f) / W - 1.f) * th * ((float)W / H), vv = (1.f - 2.f * (y + 0.5f) / H) * th;
    const float3 d = mm_norm(make_float3(C[3] + C[6] * u + C[9] * vv, C[4] + C[7] * u + C[10] * vv,
                                         C[5] + C[8] * u + C[11] * vv));
    const float el = d.y;
    const float hl = rsqrtf(d.x * d.x + d.z * d.z + 1e-8f);
    const float hx = d.x * hl, hz = d.z * hl;                      // horizontal unit direction (seamless azimuth)
    const float env = P[PR_ENV], drift = P[PR_DRIFT], per = P[PR_PERIOD];
    const float* PP = P + PR_PAL;
    float3 c = make_float3(0.f, 0.f, 0.f);
    if (env < 0.999f) {
        // ---- dusk
        const float e01 = clamp01(el * 1.8f);
        float3 s = pal(drift + per * 0.45f * sqrtf(e01), PP) * (1.f - 0.45f * e01);
        const float3 hz_col = pal(drift + 0.02f, PP);
        s = mm_mix(s, hz_col * 1.08f, 0.35f * __expf(-fmaxf(el, 0.f) * 14.f));
        const float se = P[PR_SUNEL], sa = P[PR_SUNAZ];
        const float3 sd = make_float3(__cosf(se) * __sinf(sa), __sinf(se), __cosf(se) * __cosf(sa));
        const float cs = mm_dot(d, sd);
        const float sg = (0.55f * __expf((cs - 1.f) * 7.f) + 0.7f * __expf((cs - 1.f) * 60.f))
                         * __expf(-fmaxf(el, 0.f) * 3.f) * P[PR_SUNG];
        const float3 gc = mm_mix(hz_col, make_float3(1.f, 0.93f, 0.82f), 0.45f);
        s = s + gc * sg;
        if (el > 0.f && P[PR_CLOUDS] > 0.f) {                        // sky-plane stratus
            const float k = 1.f / (el + 0.06f);
            const float cx = d.x * k, cz = d.z * k, ph = P[PR_CLOUDPH];
            const float nz = 0.65f * vnoise(cx * 0.9f + ph, cz * 2.6f, 11u)
                             + 0.35f * vnoise(cx * 2.1f - ph * 1.3f, cz * 5.3f + 3.1f, 12u);
            const float cl = smooth(0.5f, 0.78f, nz) * smooth(0.f, 0.06f, el) * (1.f - 0.6f * e01) * P[PR_CLOUDS];
            const float3 ccol = mm_mix(s * 0.55f, gc * 1.15f, clamp01(0.2f + 1.4f * sg));
            s = mm_mix(s, ccol, 0.85f * cl);
        }
        const float gh = -0.012f + 0.024f * vnoise(hx * 6.f + 3.f, hz * 6.f + 9.f, 21u)
                         + 0.008f * vnoise(hx * 23.f, hz * 23.f, 22u);
        const float pxe = 2.5f * th / H;
        const float gnd = smooth(gh + pxe, gh - pxe, el);
        s = mm_mix(s, pal(drift + per * 0.45f, PP) * 0.06f, gnd);
        c = s;
    }
    if (env > 0.001f) {
        // ---- underwater
        const float dep = clamp01(0.5f - el * 0.9f);
        const float3 tint = make_float3(0.32f, 0.72f, 1.f);
        const float3 wc = pal(drift + 0.5f + per * 0.25f * dep, PP);
        const float b = 0.05f + 0.6f * __powf(clamp01(0.5f + el * 0.8f), 2.2f);
        float3 w = make_float3(wc.x * tint.x, wc.y * tint.y, wc.z * tint.z) * b;
        const float rp = P[PR_RAYPH];
        const float rn = vnoise(hx * 7.f + rp, hz * 7.f - 0.7f * rp, 31u) * vnoise(hx * 13.f - 1.3f * rp, hz * 13.f + 5.f, 32u);
        const float shaft = rn * sqrtf(rn) * smooth(-0.7f, 0.3f, el) * (1.f - smooth(0.55f, 0.95f, el));
        const float3 rc = pal(drift + 0.12f, PP);
        w = w + make_float3(rc.x * 0.6f, rc.y * 0.9f, rc.z) * (0.9f * shaft);
        w = w + make_float3(0.7f, 0.9f, 1.f) * (0.6f * smooth(0.75f, 0.98f, el));
        c = mm_mix(c, w, env);
    }
    c = c * (P[PR_INTENS] * (1.f + 0.12f * P[PR_PULSE]));
    const unsigned int hh = hash_u((unsigned int)x * 1973u + (unsigned int)y * 9277u + 0x2545F491u);
    const float dth = ((hh & 0xffffu) + ((hh >> 16) & 0xffffu)) * (1.f / 65536.f) - 1.f;
    out[y * W + x] = make_uchar4((unsigned char)fminf(fmaxf(c.x * 255.f + dth + 0.5f, 0.f), 255.f),
                                 (unsigned char)fminf(fmaxf(c.y * 255.f + dth + 0.5f, 0.f), 255.f),
                                 (unsigned char)fminf(fmaxf(c.z * 255.f + dth + 0.5f, 0.f), 255.f), 255);
}
"""
_OPTIONS = ("--std=c++14", "--use_fast_math")


def _smooth(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


def _shapes(u: np.ndarray) -> list[np.ndarray]:
    """The four attractor curves (closed, NC samples): cloud, ring, figure-eight, trefoil-ish knot."""
    s, c = np.sin(u), np.cos(u)
    cloud = np.stack([0.6 * c, 0.3 * np.sin(2 * u), 0.6 * s], 1)
    ring = np.stack([2.1 * c, 0.35 * np.sin(2 * u), 2.1 * s], 1)
    eight = np.stack([2.6 * s, 0.5 * c, 2.2 * s * c], 1)
    r = 1.7 + 0.55 * np.cos(3 * u)
    knot = np.stack([r * np.cos(2 * u), 0.6 * np.sin(3 * u), r * np.sin(2 * u)], 1)
    return [cloud, ring, eight, knot]


TUBE_MUL = (2.3, 1.0, 1.0, 0.85)


def _rot(axis: int, a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    if axis == 0:
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == 1:
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


class Murmuration(Source):
    name = "murmuration"
    label = "Murmuration"
    description = "Tens of thousands of GPU boids wheel around moving attractors: starlings at dusk or a glowing shoal underwater."
    tags = {"family": "particles", "dims": "3d", "toolkit": "splat3d", "technique": ["agents", "simulation", "particles"],
            "moods": ["organic", "elegant", "calm"], "style": "gentle", "energy": [0.0, 0.8]}
    params = {
        **standard_scene_params(palette="dusk", render_scale=0.5, period=1.0, drift=0.01),
        "flock_size": num(0.0, 1.0, 0.6, "Flock size", tier="regional", basic=True),
        "cohesion": num(0.0, 1.0, 0.5, "Cohesion vs separation", tier="regional"),
        "environment": num(0.0, 1.0, 0.0, "Dusk sky - underwater", tier="regional", basic=True),
        "attractor_shape": num(0.0, 1.0, 0.4, "Attractor (cloud, ring, eight, knot)", tier="regional"),
        "bird_size": num(0.4, 2.5, 1.0, "Bird size", tier="regional"),
        "camera_distance": num(5.0, 16.0, 9.0, "Camera distance", tier="regional"),
        "waves": num(0.0, 2.0, 1.0, "Density waves", tier="regional"),
        "glow": num(0.0, 2.0, 0.8, "Glow", tier="regional"),
        "orbit_speed": num(0.0, 4.0, 1.0, "Camera sway", unit="×", tier="realtime", bind="rate"),
    }

    # ------------------------------------------------------------------ setup
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.cp = cp
        seed = int(params["seed"])
        self.rng = np.random.default_rng(seed)
        mod = raw_module(_SRC, _OPTIONS)
        self.k_hash = mod.get_function("mm_hash")
        self.k_scan = mod.get_function("mm_scan")
        self.k_scatter = mod.get_function("mm_scatter")
        self.k_boids = mod.get_function("mm_boids")
        self.k_geom = mod.get_function("mm_geom")
        self.k_sky = mod.get_function("mm_sky")

        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.sp = Splat3D(ctx, self.w, self.h, max_points=N_MOTES, max_lines=2 * N_MAX, alpha=True, pool=1 << 20)

        self.pos = cp.zeros((N_MAX, 4), cp.float32)
        self.vel = cp.zeros((N_MAX, 4), cp.float32)
        self.spos = cp.zeros_like(self.pos)
        self.svel = cp.zeros_like(self.vel)
        self.cell = cp.zeros(N_MAX, cp.int32)
        self.rank = cp.zeros(N_MAX, cp.int32)
        self.count = cp.zeros(NB, cp.int32)
        self.start = cp.zeros(NB, cp.int32)
        self.p0 = cp.zeros((2 * N_MAX, 4), cp.float32)
        self.p1 = cp.zeros((2 * N_MAX, 4), cp.float32)
        self.rgb = cp.zeros((2 * N_MAX, 3), cp.float32)
        self.hp = np.zeros(PRM_SIZE, np.float32)
        self.prm = cp.zeros(PRM_SIZE, cp.float32)

        # motes (underwater): faint specks in a big ball, weight skewed
        m = np.ones((N_MOTES, 4), np.float32)
        m[:, :3] = fibonacci_sphere(N_MOTES) * (self.rng.uniform(0.0, 1.0, (N_MOTES, 1)) ** 0.5 * 9.0)
        m[:, 3] = self.rng.uniform(0.0, 1.0, N_MOTES) ** 3
        self.motes = cp.asarray(m)
        self.mote_idx = cp.asarray(self.rng.uniform(0.0, 1.0, N_MOTES).astype(np.float32))

        # host state
        self.ph = float(self.rng.uniform(0, 50))   # attractor wander phase
        self.spin = 0.0
        self.sway = 0.0
        self.wph = 0.0
        self.wang = float(self.rng.uniform(0, 2 * math.pi))
        self.ray = 0.0
        self.cloud = float(self.rng.uniform(0, 100))
        self.mote_rot = 0.0
        self.drift = 0.0
        self.e_s = None
        self.target = None
        self.prev_pulse = 0.0
        self.since = 1.0
        self.preds = [[np.zeros(3), 0.0, 0.0, np.zeros(3)] for _ in range(2)]   # centre, radius, strength, vel

        # seed boids along the curve, then pre-warm so the first frame is a formed flock
        p = self.step_params(0.0)
        self._frame_params(p, 0.0, 0.0, 0.5, 0.0, 1.0)
        pts, tans = self.curve_pts, self.curve_tan
        k = self.rng.integers(0, NC, N_MAX)
        tube = float(self.hp[PR["TUBE"]])
        pos = np.zeros((N_MAX, 4), np.float32)
        vel = np.zeros((N_MAX, 4), np.float32)
        pos[:, :3] = pts[k] + self.rng.normal(0.0, 0.6 * tube, (N_MAX, 3))
        pos[:, 3] = 0.5
        vel[:, :3] = tans[k] * float(self.hp[PR["VMAX"]]) * 0.8 + self.rng.normal(0.0, 0.3, (N_MAX, 3))
        vel[:, 3] = self.rng.uniform(0, 2 * math.pi, N_MAX)
        self.pos.set(pos)
        self.vel.set(vel)
        h = 1.0 / 40.0
        self.hp[PR["H"]] = h
        self.prm.set(self.hp)
        n = self.n_sim
        for _ in range(100):
            self._sim_step(n)

    # ------------------------------------------------------------------ helpers
    def _curve(self, shape: float, scale: float, centre) -> None:
        u = np.linspace(0.0, 2 * math.pi, NC, endpoint=False)
        S = _shapes(u)
        x = min(max(shape, 0.0), 1.0) * 3.0
        k = min(int(x), 2)
        f = _smooth(x - k)
        C = S[k] * (1.0 - f) + S[k + 1] * f
        self.tube_mul = TUBE_MUL[k] * (1.0 - f) + TUBE_MUL[k + 1] * f
        R = _rot(1, self.spin) @ _rot(0, 0.32 * math.sin(0.37 * self.ph)) @ _rot(2, 0.22 * math.sin(0.29 * self.ph + 1.0))
        pts = (C @ R.T) * scale + np.asarray(centre)[None, :]
        tan = np.roll(pts, -1, 0) - np.roll(pts, 1, 0)
        tan /= np.linalg.norm(tan, axis=1, keepdims=True) + 1e-9
        self.curve_pts, self.curve_tan = pts, tan

    def _frame_params(self, p: dict, e: float, es: float, pulse: float, sdt_h: float, spd: float) -> None:
        hp = self.hp
        coh = float(p["cohesion"])
        env = _smooth(float(p["environment"]))
        self.env = env
        nf = 8192.0 + 24576.0 * float(p["flock_size"])
        self.nact = nf
        self.n_sim = int(min(N_MAX, math.ceil(nf + FADE_BAND)))
        centre = (1.0 * math.sin(0.31 * self.ph), 0.45 * math.sin(0.53 * self.ph + 1.0),
                  0.8 * math.sin(0.23 * self.ph + 2.0))
        self.centre = centre
        self._curve(float(p["attractor_shape"]), 1.0 + 0.2 * es, centre)
        vmax = 1.7 + 1.8 * e
        R = 0.16
        hp[PR["H"]] = sdt_h
        hp[PR["VMAX"]] = vmax
        hp[PR["VMIN"]] = 0.55 * vmax
        hp[PR["AMAX"]] = 4.0 + 8.0 * e
        hp[PR["R"]] = R
        hp[PR["ICELL"]] = 1.0 / R
        hp[PR["RSEP"]] = 0.075 * (0.85 + 0.4 * es) * (1.25 - 0.45 * coh)
        hp[PR["KSEP"]] = 14.0 - 8.0 * coh
        hp[PR["KALI"]] = 1.2 + 1.8 * coh
        hp[PR["KCOH"]] = 1.0 + 7.0 * coh
        hp[PR["KATT"]] = 2.5
        hp[PR["TUBE"]] = (0.3 + 0.35 * es) * (1.35 - 0.6 * coh) * self.tube_mul
        hp[PR["KFLOW"]] = 1.8
        hp[PR["KSWIRL"]] = 1.2 + 1.5 * e
        hp[PR["WAMP"]] = float(p["waves"]) * (1.5 + 2.0 * e)
        hp[PR["WK"]] = 2.2
        hp[PR["WPH"]] = math.fmod(self.wph, 2 * math.pi)
        hp[PR["WDX"]], hp[PR["WDY"]], hp[PR["WDZ"]] = math.cos(self.wang), 0.25, math.sin(self.wang)
        hp[PR["FLAP"]] = 2 * math.pi * (2.0 + 1.5 * e)
        hp[PR["NACT"]] = nf
        hp[PR["KSTART"]] = 30.0
        hp[PR["DRIFT"]] = self.drift
        hp[PR["PERIOD"]] = float(p["palette_period"])
        hp[PR["ENV"]] = env
        bs = float(p["bird_size"])
        hp[PR["BSIZE"]] = 0.03 * bs
        hp[PR["FLEN"]] = 0.085 * bs
        hp[PR["BRIGHT"]] = (0.7 + 0.5 * e) * (1.0 + 0.6 * pulse)
        hp[PR["PULSE"]] = pulse
        hp[PR["SEPB"]] = 1.0 + 1.5 * pulse
        hp[PR["INTENS"]] = float(p["intensity"])
        hp[PR["SUNAZ"]] = 0.25
        hp[PR["SUNEL"]] = -0.03
        hp[PR["SUNG"]] = 1.0 + 0.3 * pulse
        hp[PR["RAYPH"]] = self.ray
        hp[PR["CLOUDPH"]] = self.cloud
        hp[PR["CLOUDS"]] = 1.0
        for j, (c, r, s, _v) in enumerate(self.preds):
            b = PR["PRED"] + 6 * j
            hp[b:b + 3] = c
            hp[b + 3] = r
            hp[b + 4] = s
            hp[b + 5] = 0.35
        sess = self.session_palette()
        coeffs = np.asarray(PALETTES[p["palette"]] if sess is None else sess, np.float64).reshape(-1)
        self.pal = tuple(float(v) for v in coeffs)
        hp[PR["PAL"]:PR["PAL"] + 12] = coeffs
        hp[PR["CURVE"]:PR["CURVE"] + NC * 6] = np.concatenate([self.curve_pts, self.curve_tan], 1).reshape(-1)

    def _sim_step(self, n: int) -> None:
        if n <= 0:
            return
        self.count.data.memset_async(0, self.count.nbytes)
        g = ((n + 127) // 128,)
        ni = np.int32(n)
        self.k_hash(g, (128,), (self.pos, self.prm, self.cell, self.rank, self.count, ni))
        self.k_scan((1,), (1024,), (self.count, self.start))
        self.k_scatter(g, (128,), (self.pos, self.vel, self.cell, self.rank, self.start, self.spos, self.svel, ni))
        self.k_boids(g, (128,), (self.prm, self.pos, self.vel, self.spos, self.svel, self.start, self.count, ni))

    # ------------------------------------------------------------------ render
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        dtc = min(max(dt, 0.0), 0.1)
        a = 1.0 - math.exp(-dtc / 0.5) if dtc > 0 else 0.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s
        sdt = dtc * spd

        # ---- host phases (pulse only adds to rates / gains)
        self.ph += (0.25 + 0.8 * e) * sdt
        self.spin += (0.04 + 0.12 * e) * sdt
        self.sway += float(p["orbit_speed"]) * (0.05 + 0.08 * e) * sdt
        self.wph += (1.5 + 2.5 * e) * sdt
        self.wang += 0.05 * sdt
        self.ray += 0.25 * sdt
        self.cloud += 0.02 * sdt
        self.mote_rot += 0.03 * sdt
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * spd * dtc, 1000.0)

        # ---- startle on the pulse's rising edge (refractory ~120 ms), placed on the attractor curve
        self.since += dtc
        if pulse > 0.2 and pulse - self.prev_pulse > 0.15 and self.since > 0.12 and hasattr(self, "curve_pts"):
            slot = 0 if self.preds[0][2] < self.preds[1][2] else 1
            k = int(self.rng.integers(0, NC))
            c = self.curve_pts[k] + self.rng.normal(0.0, 0.15, 3)
            vdir = self.rng.normal(0.0, 1.0, 3)
            vdir /= np.linalg.norm(vdir) + 1e-9
            self.preds[slot] = [c, 0.0, min(1.0, 1.2 * pulse), vdir * 1.2]
            self.since = 0.0
        self.prev_pulse = pulse
        for pr in self.preds:
            if pr[2] > 1e-3:
                pr[1] += (2.2 + 1.5 * es) * sdt
                pr[2] *= math.exp(-sdt / 0.8)
                pr[0] = pr[0] + pr[3] * sdt
            else:
                pr[2] = 0.0

        # ---- sim (substepped; dt = 0 or speed 0 -> no step)
        nsub = 0
        h = 0.0
        if sdt > 1e-6:
            nsub = min(3, max(1, math.ceil(sdt / (1.0 / 45.0))))
            h = sdt / nsub
        self._frame_params(p, e, es, pulse, h, spd)

        # ---- camera: sway in front of the sunset, follow the flock centre gently
        D = float(p["camera_distance"])
        c = np.asarray(self.centre) * 0.7
        if self.target is None:
            self.target = c
        else:
            self.target = self.target + (c - self.target) * (1.0 - math.exp(-dtc / 1.5))
        env = self.env
        yaw = 0.85 * math.sin(self.sway) * (0.6 + 0.4 * es)
        pitch = -(0.27 - 0.07 * env) + 0.04 * math.sin(0.7 * self.sway + 1.0)
        tgt = tuple(float(v) for v in self.target)
        cam = look_at(orbit(tgt, D, yaw, pitch), tgt, fov_deg=50.0)
        self.hp[PR["CAM"]:PR["CAM"] + 13] = cam.pack()
        self.prm.set(self.hp)

        n = self.n_sim
        for _ in range(nsub):
            self._sim_step(n)
        g = ((n + 127) // 128,)
        self.k_geom(g, (128,), (self.prm, self.pos, self.vel, self.p0, self.p1, self.rgb, np.int32(n)))

        # ---- sky into the output, birds splatted over it
        H, W = int(out.shape[0]), int(out.shape[1])
        launch_2d(self.k_sky, W, H, (out, np.int32(W), np.int32(H), self.prm))

        sp = self.sp
        sp.clear()
        fog = (0.05 + 0.02 * env) * 9.0 / D
        sp.lines(self.p0, self.p1, n=2 * n, cam=cam, rgb=self.rgb, intensity=1.7 - 0.8 * env,
                 width_px=1.3 + 0.3 * env, near_fade=(0.3, 1.0), fog=fog)
        if env > 0.02:
            sp.points(self.motes, cam=cam, model=mat_trs(t=tgt, r=mat_rot((0.2, 1.0, 0.1), self.mote_rot)),
                      intensity=0.25 * env, size_px=1.6, weight_in_w=True, palette=self.pal,
                      index=self.mote_idx, period=0.4, drift=self.drift + 0.2, fog=fog * 0.7)
        # dusk birds are pure occlusion (any light term turns dense cores white); light fades in with env
        glow = float(p["glow"]) * (0.5 + 0.6 * e) * (1.0 + 0.6 * pulse)
        sp.resolve(out, exposure=0.75 * env * float(p["intensity"]), tonemap="aces",
                   saturation=1.1, glow=glow, glow_radius_px=16.0, glow_wide=0.5, background=out,
                   occlusion=7.0 * (1.0 - env) ** 1.5)

    def close(self) -> None:
        if getattr(self, "sp", None) is not None:
            self.sp.close()
        self.sp = None
        self.pos = self.vel = self.spos = self.svel = self.cell = self.rank = self.count = self.start = None
        self.p0 = self.p1 = self.rgb = self.prm = self.motes = self.mote_idx = None
