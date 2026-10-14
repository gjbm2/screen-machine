"""Fluid ink: a stable-fluids dye simulation pushed by orbiting emitters, with relief shading and bloom glow.

Source: https://github.com/PavelDoGreat/WebGL-Fluid-Simulation (script.js @ master, LICENSE @ master)
        https://developer.nvidia.com/gpugems/gpugems/part-vi-beyond-triangles/chapter-38-fast-fluid-dynamics-simulation-gpu
Author: Pavel Dobryakov (WebGL-Fluid-Simulation); stable-fluids method Jos Stam, GPU formulation Mark J. Harris
        (GPU Gems ch. 38)
License: MIT
license_class: permissive
Ported from GLSL by smfx (ideas/changes: CUDA on fixed float buffers in y-down pixel space; an aspect-matched
    256x144 velocity/pressure grid independent of output size, dye at render_scale; the whole velocity step
    (all substeps, 20 Jacobi iterations) runs in ONE single-block kernel because per-launch host overhead
    dominates on this grid; splats fused into the velocity and dye advection passes (<= 16 per frame); GPU Gems'
    0.5 factor in the gradient subtraction; pointer input replaced by 1-8 orbiting emitters (continuous symmetry:
    emitter k fades in by its weight) and beat-spawned splat bursts with their own lifetimes; dye coloured by the
    shared cosine palette with drift; exp() dissipation with energy-scaled persistence plus a GPU-side coverage
    meter that adds dissipation only when the frame over-fills; directional relief light + gloss instead of the
    edge-darkening SHADING; Pavel's soft-knee bloom prefilter at 1/8 res + 4-tap down/up mip chain; soft toe and
    hue-preserving soft-knee tonemap; sunrays and dithering dropped).

Licence text of the ported work, quoted from the repository's LICENSE file and script.js header:

    MIT License

    Copyright (c) 2017 Pavel Dobryakov

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

Per substep (Pavel's order, rotated so the splat rides on the advection), in fl_sim:
    advect velocity (+ splat forces) -> curl -> vorticity confinement -> divergence -> 20 Jacobi pressure
    iterations (warm start x0.8) -> subtract pressure gradient.
Per frame: advect dye (+ splat dye, coverage meter) with the projected field -> bloom prefilter + mip chain ->
composite (relief light, gloss, bloom, toe, tonemap) at internal res -> present.  ~2.3 ms at 4K on the P4000.

Macros: energy (e' = smoothstep(0.1, 0.85, e)) -> emitter force (0.55 + 0.9e'), orbit / spin / wobble rates
(0.4 + 1.2e'), curl (0.6 + 0.8e'), velocity damping (calm = more viscous), dye persistence and coverage target
(slewed over 1.5 s: short sparse trails when calm, long full ones when busy), dye injection, bloom (smoothstep).
pulse (k = pulse (0.3 + 0.7e)) -> a rate kick on the sim clock, dt x (1 + surge_kick k), so the whole fluid lurches
on the beat and eases out; an orbit-rate lurch (1 + 1.2k); a force swell (1 + 0.5 k splat_kick); a <= +30% bloom
lift (bright areas only); and, on the rising edge (> 0.6 after < 0.3, >= 150 ms apart), a splat burst from every
emitter whose force and dye scale with k x splat_kick.  speed scales the sim clock (<= 2 clamped substeps) and
every phase.  palette: emitter i dye = pal(i / N x period + drift), so drift rotates colour through the fluid.
pan / tilt bias the emitter jets.  intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present

SIM_H = 144                  # velocity / pressure grid height (width follows the aspect: 256 at 16:9)
JACOBI = 20                  # pressure iterations (even, so the result lands back in p_a)
MAX_EMIT = 8
MAX_BURST = 8
MAX_SPLAT = MAX_EMIT + MAX_BURST
BLOOM_LEVELS = 5
VMAX = 900.0                 # velocity clamp, sim cells / s
ORBIT_BASE = 0.07            # emitter orbit, rev / s at e' ~ 0.5
F_CONT = 900.0               # continuous emitter force, cells / s^2
D_CONT = 6.0                # continuous emitter dye, per s
F_BURST = 7500.0             # beat burst force at k = 1
D_BURST = 26.0               # beat burst dye at k = 1
BURST_LIFE = 0.6
BURST_WHITE = 0.25
FILL_TARGET = 0.30           # mean coverage above which extra dissipation kicks in
FILL_K = 6.0                 # extra dissipation (/s) per unit of relative over-fill
BLOOM_THR = 1.0
RELIEF = 12.0
GLOSS = 0.9
TAU = 2.0 * math.pi

_SRC = HELPERS + r"""
#define NT 32                                        // single-block kernels: 32 x 32 threads
#define TOE %(toe)ff
#define SAT %(sat)ff
__device__ __forceinline__ float2 ld2(const float2* v, int x, int y, int W, int H) {
    x = min(max(x, 0), W - 1); y = min(max(y, 0), H - 1); return v[y * W + x];
}
__device__ __forceinline__ float ld1(const float* v, int x, int y, int W, int H) {
    x = min(max(x, 0), W - 1); y = min(max(y, 0), H - 1); return v[y * W + x];
}
__device__ __forceinline__ float4 ldf4(const float4* v, int x, int y, int W, int H) {
    x = min(max(x, 0), W - 1); y = min(max(y, 0), H - 1); return v[y * W + x];
}
// bilinear at continuous coords (cell centres at i + 0.5), clamped at the walls
__device__ __forceinline__ float2 bil2(const float2* v, float fx, float fy, int W, int H) {
    fx -= 0.5f; fy -= 0.5f;
    const float x0f = floorf(fx), y0f = floorf(fy);
    const int x0 = (int)x0f, y0 = (int)y0f;
    const float tx = fx - x0f, ty = fy - y0f;
    const float2 a = ld2(v, x0, y0, W, H), b = ld2(v, x0 + 1, y0, W, H);
    const float2 c = ld2(v, x0, y0 + 1, W, H), d = ld2(v, x0 + 1, y0 + 1, W, H);
    const float u = 1.f - tx, w = 1.f - ty;
    return make_float2(w * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       w * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y));
}
__device__ __forceinline__ float4 bil4(const float4* v, float fx, float fy, int W, int H) {
    fx -= 0.5f; fy -= 0.5f;
    const float x0f = floorf(fx), y0f = floorf(fy);
    const int x0 = (int)x0f, y0 = (int)y0f;
    const float tx = fx - x0f, ty = fy - y0f;
    const float4 a = ldf4(v, x0, y0, W, H), b = ldf4(v, x0 + 1, y0, W, H);
    const float4 c = ldf4(v, x0, y0 + 1, W, H), d = ldf4(v, x0 + 1, y0 + 1, W, H);
    const float u = 1.f - tx, w = 1.f - ty;
    return make_float4(w * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       w * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                       w * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z), 0.f);
}
__device__ __forceinline__ float2 vclamp(float2 v, float vmax) {
    const float m2 = v.x * v.x + v.y * v.y;
    if (!(m2 <= vmax * vmax)) {                      // also catches NaN
        if (m2 == m2) { const float k = vmax * rsqrtf(m2); v.x *= k; v.y *= k; }
        else v = make_float2(0.f, 0.f);
    }
    return v;
}
#define FOR_CELLS for (int y = threadIdx.y; y < H; y += NT) for (int x = threadIdx.x; x < W; x += NT)

// S: n splats x 8 floats {x, y (sim cells), 1/rr, fx, fy (cells/s added per substep), r, g, b (dye added per frame)}

// The whole velocity step on the small sim grid in ONE block (launch overhead dominates otherwise; the grid
// lives in L2).  Per substep, Pavel's order rotated so the splat rides on the advection:
//   advect velocity + splat forces -> curl -> vorticity confinement -> divergence -> JACOBI pressure iterations
//   (warm start x0.8) -> subtract the pressure gradient.  The result ends in va after an even nsub, else in vb.
extern "C" __global__ void __launch_bounds__(1024)
fl_sim(float2* va, float2* vb, float* cu, float* dv, float* pa, float* pb, const int W, const int H,
       const float dt, const float vdecay, const float eps, const float vmax, const float* __restrict__ S,
       const int n, const int nsub, const int jacobi, float* mass)
{
    if (threadIdx.x == 0 && threadIdx.y == 0) { mass[1] = mass[0]; mass[0] = 0.f; }   // fill meter for the dye pass
    for (int sub = 0; sub < nsub; ++sub) {
        FOR_CELLS {                                  // advectionShader (velocity) + splatShader
            const int i = y * W + x;
            const float2 v = va[i];
            const float cx = x + 0.5f, cy = y + 0.5f;
            float2 r = bil2(va, cx - dt * v.x, cy - dt * v.y, W, H);
            r.x *= vdecay; r.y *= vdecay;
            for (int k = 0; k < n; ++k) {
                const float* s = S + 8 * k;
                const float dx = cx - s[0], dy = cy - s[1];
                const float q = (dx * dx + dy * dy) * s[2];
                if (q < 16.f) { const float g = __expf(-q); r.x += g * s[3]; r.y += g * s[4]; }
            }
            vb[i] = vclamp(r, vmax);
        }
        __syncthreads();
        FOR_CELLS {                                  // curlShader: w = dv/dx - du/dy (y-down, same algebra)
            const float L = ld2(vb, x - 1, y, W, H).y, R = ld2(vb, x + 1, y, W, H).y;
            const float U = ld2(vb, x, y - 1, W, H).x, D = ld2(vb, x, y + 1, W, H).x;
            cu[y * W + x] = 0.5f * ((R - L) - (D - U));
        }
        __syncthreads();
        FOR_CELLS {                                  // vorticityShader: f = eps (N x w), N = grad|w| / |grad|w||
            const int i = y * W + x;
            const float L = fabsf(ld1(cu, x - 1, y, W, H)), R = fabsf(ld1(cu, x + 1, y, W, H));
            const float U = fabsf(ld1(cu, x, y - 1, W, H)), D = fabsf(ld1(cu, x, y + 1, W, H));
            float fx = 0.5f * (R - L), fy = 0.5f * (D - U);
            const float k = eps * cu[i] / (sqrtf(fx * fx + fy * fy) + 1e-4f);
            fx *= k; fy *= k;
            float2 o = vb[i];
            o.x += fy * dt; o.y -= fx * dt;
            va[i] = vclamp(o, vmax);
        }
        __syncthreads();
        FOR_CELLS {                                  // divergenceShader, reflecting walls
            const int i = y * W + x;
            const float2 C = va[i];
            const float L = x > 0 ? va[i - 1].x : -C.x, R = x < W - 1 ? va[i + 1].x : -C.x;
            const float U = y > 0 ? va[i - W].y : -C.y, D = y < H - 1 ? va[i + W].y : -C.y;
            dv[i] = 0.5f * (R - L + D - U);
        }
        __syncthreads();
        float* p0 = pa; float* p1 = pb;
        for (int it = 0; it < jacobi; ++it) {        // clearShader (x0.8) folded into the first pressureShader pass
            const float s = it == 0 ? 0.8f : 1.f;
            FOR_CELLS {
                const float L = ld1(p0, x - 1, y, W, H), R = ld1(p0, x + 1, y, W, H);
                const float U = ld1(p0, x, y - 1, W, H), D = ld1(p0, x, y + 1, W, H);
                p1[y * W + x] = (s * (L + R + U + D) - dv[y * W + x]) * 0.25f;
            }
            __syncthreads();
            float* t = p0; p0 = p1; p1 = t;
        }
        FOR_CELLS {                                  // gradientSubtractShader (GPU Gems' halfrdx = 0.5)
            const int i = y * W + x;
            const float L = ld1(p0, x - 1, y, W, H), R = ld1(p0, x + 1, y, W, H);
            const float U = ld1(p0, x, y - 1, W, H), D = ld1(p0, x, y + 1, W, H);
            float2 o = va[i];
            o.x -= 0.5f * (R - L); o.y -= 0.5f * (D - U);
            vb[i] = o;
        }
        __syncthreads();
        float2* t = va; va = vb; vb = t;
    }
}

// dye advection at dye resolution through the bilinear sim velocity, dissipation, splat dye.  Also measures the
// frame's dye coverage (sum of min(max channel, 1.5)) into mass[0]; mass[1] (last frame's, rotated by fl_sim) adds
// extra dissipation only while coverage is above `target`, so busy settings never wash out to a pastel whiteout.
extern "C" __global__
void fl_advect_dye(const float4* __restrict__ din, float4* __restrict__ dout, const float2* __restrict__ vel,
                   const int W, const int H, const int SW, const int SH, const float sx, const float sy,
                   const float dt, const float decay, const float* __restrict__ S, const int n,
                   float* __restrict__ mass, const float inv_n, const float target, const float kfill)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    float v = 0.f;
    if (x < W && y < H) {
        const float gx = (x + 0.5f) * sx, gy = (y + 0.5f) * sy;        // sim-cell coords
        const float2 u = bil2(vel, gx, gy, SW, SH);
        float4 c = bil4(din, (gx - dt * u.x) / sx, (gy - dt * u.y) / sy, W, H);
        const float over = fmaxf(mass[1] * inv_n / target - 1.f, 0.f);
        const float dk = decay * __expf(-kfill * fminf(over, 4.f) * dt);
        c.x *= dk; c.y *= dk; c.z *= dk;
        for (int k = 0; k < n; ++k) {
            const float* s = S + 8 * k;
            const float dx = gx - s[0], dy = gy - s[1];
            const float q = (dx * dx + dy * dy) * s[2];
            if (q < 16.f) { const float g = __expf(-q); c.x += g * s[5]; c.y += g * s[6]; c.z += g * s[7]; }
        }
        c.x = fminf(fmaxf(c.x, 0.f), 64.f); c.y = fminf(fmaxf(c.y, 0.f), 64.f); c.z = fminf(fmaxf(c.z, 0.f), 64.f);
        c.w = 0.f;
        dout[y * W + x] = c;
        v = fminf(fmaxf(c.x, fmaxf(c.y, c.z)), 1.5f);
    }
    // block reduction (blockDim = 32 x 8: threadIdx.x is the lane), one atomic per block
    for (int o = 16; o > 0; o >>= 1) v += __shfl_down_sync(0xffffffffu, v, o);
    __shared__ float ws[8];
    if (threadIdx.x == 0) ws[threadIdx.y] = v;
    __syncthreads();
    if (threadIdx.y == 0) {
        v = threadIdx.x < 8 ? ws[threadIdx.x] : 0.f;
        for (int o = 4; o > 0; o >>= 1) v += __shfl_down_sync(0xffffffffu, v, o);
        if (threadIdx.x == 0) atomicAdd(mass, v);
    }
}

// bloom prefilter (Pavel's soft-knee curve) fused with an 8x downsample: 16 bilinear taps = an exact 8x8 box
extern "C" __global__
void fl_bloom_pre(const float4* __restrict__ dye, float4* __restrict__ b, const int dw, const int dh,
                  const int W, const int H, const float thr, const float c0, const float c1, const float c2)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float sx = (float)dw / W, sy = (float)dh / H;
    const float fx = (x + 0.5f) * sx, fy = (y + 0.5f) * sy;
    float3 m = make_float3(0.f, 0.f, 0.f);
    #pragma unroll
    for (int j = 0; j < 4; ++j) {
        #pragma unroll
        for (int i = 0; i < 4; ++i) {
            const float4 a = bil4(dye, fx + (i - 1.5f) * 0.25f * sx, fy + (j - 1.5f) * 0.25f * sy, dw, dh);
            m.x += a.x; m.y += a.y; m.z += a.z;
        }
    }
    m.x *= 0.0625f; m.y *= 0.0625f; m.z *= 0.0625f;
    const float br = fmaxf(m.x, fmaxf(m.y, m.z));
    float rq = fminf(fmaxf(br - c0, 0.f), c1);
    rq = c2 * rq * rq;
    const float k = fmaxf(rq, br - thr) / fmaxf(br, 1e-4f);
    b[y * W + x] = make_float4(m.x * k, m.y * k, m.z * k, 0.f);
}

// Pavel's bloomBlurShader: 4 taps at +-1 source texel around the destination centre; add accumulates (upsample)
__device__ __forceinline__ void blur_level(const float4* src, int sw, int sh, float4* dst, int W, int H, bool add) {
    FOR_CELLS {
        const float fx = (x + 0.5f) * sw / W, fy = (y + 0.5f) * sh / H;
        const float4 a = bil4(src, fx - 1.f, fy, sw, sh), b = bil4(src, fx + 1.f, fy, sw, sh);
        const float4 c = bil4(src, fx, fy - 1.f, sw, sh), d = bil4(src, fx, fy + 1.f, sw, sh);
        float4 o = make_float4(0.25f * (a.x + b.x + c.x + d.x), 0.25f * (a.y + b.y + c.y + d.y),
                               0.25f * (a.z + b.z + c.z + d.z), 0.f);
        if (add) { const float4 p = dst[y * W + x]; o.x += p.x; o.y += p.y; o.z += p.z; }
        dst[y * W + x] = o;
    }
}
// the whole down / up mip chain after the prefilter in ONE block (the mips are tiny)
extern "C" __global__ void __launch_bounds__(1024)
fl_bloom_chain(float4* b0, float4* b1, float4* b2, float4* b3, float4* b4, const int w0, const int h0,
               const int w1, const int h1, const int w2, const int h2, const int w3, const int h3,
               const int w4, const int h4)
{
    blur_level(b0, w0, h0, b1, w1, h1, false); __syncthreads();
    blur_level(b1, w1, h1, b2, w2, h2, false); __syncthreads();
    blur_level(b2, w2, h2, b3, w3, h3, false); __syncthreads();
    blur_level(b3, w3, h3, b4, w4, h4, false); __syncthreads();
    blur_level(b4, w4, h4, b3, w3, h3, true); __syncthreads();
    blur_level(b3, w3, h3, b2, w2, h2, true); __syncthreads();
    blur_level(b2, w2, h2, b1, w1, h1, true);      // the last up step (b1 -> b0) is folded into fl_composite
}

// composite: relief light + gloss from the dye-density gradient, bloom, gain, hue-preserving soft-knee tonemap
extern "C" __global__
void fl_composite(const float4* __restrict__ dye, const float4* __restrict__ bl, const float4* __restrict__ bl1,
                  uchar4* __restrict__ out, const int W, const int H, const int bw, const int bh, const int bw1,
                  const int bh1, const float shading, const float relief,
                  const int s, const float bloom, const float gain, const float gloss)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float4 c4 = dye[y * W + x];
    float3 col = make_float3(c4.x, c4.y, c4.z);
    const float lc = sqrtf(col.x * col.x + col.y * col.y + col.z * col.z);
    if (shading > 0.f) {
        const float4 l = ldf4(dye, x - s, y, W, H), r = ldf4(dye, x + s, y, W, H);
        const float4 u = ldf4(dye, x, y - s, W, H), d = ldf4(dye, x, y + s, W, H);
        const float gx = (sqrtf(r.x * r.x + r.y * r.y + r.z * r.z) - sqrtf(l.x * l.x + l.y * l.y + l.z * l.z)) * relief;
        const float gy = (sqrtf(d.x * d.x + d.y * d.y + d.z * d.z) - sqrtf(u.x * u.x + u.y * u.y + u.z * u.z)) * relief;
        const float inv = rsqrtf(gx * gx + gy * gy + 1.f);
        const float nx = -gx * inv, ny = -gy * inv, nz = inv;
        const float Lx = -0.48f, Ly = -0.60f, Lz = 0.64f;            // key light from the upper left
        const float dif = nx * Lx + ny * Ly + nz * Lz;
        const float sh = shading * smooth(0.02f, 0.35f, lc);       // faint wisps stay flat (no crinkle noise)
        const float shade = fminf(fmaxf(1.f + sh * 1.3f * (dif - Lz), 0.2f), 1.9f);
        col.x *= shade; col.y *= shade; col.z *= shade;
        const float Hx = -0.29f, Hy = -0.36f, Hz = 0.886f;           // half vector of L and the view (0, 0, 1)
        const float nh = fmaxf(nx * Hx + ny * Hy + nz * Hz, 0.f);
        float sp = __powf(nh, 40.f) - __powf(Hz, 40.f);
        sp = fmaxf(sp, 0.f) * sh * gloss * smooth(0.05f, 0.7f, lc);
        col.x += sp; col.y += sp; col.z += sp;
    }
    if (bloom > 0.f) {
        const float4 b = bil4(bl, (x + 0.5f) * bw / W, (y + 0.5f) * bh / H, bw, bh);
        const float4 c1 = bil4(bl1, (x + 0.5f) * bw1 / W, (y + 0.5f) * bh1 / H, bw1, bh1);   // + the mip chain
        col.x += (b.x + c1.x) * bloom; col.y += (b.y + c1.y) * bloom; col.z += (b.z + c1.z) * bloom;
    }
    {                                                                // lift saturation lost to additive mixing
        const float lm = 0.2126f * col.x + 0.7152f * col.y + 0.0722f * col.z;
        col.x = fmaxf(lm + SAT * (col.x - lm), 0.f); col.y = fmaxf(lm + SAT * (col.y - lm), 0.f);
        col.z = fmaxf(lm + SAT * (col.z - lm), 0.f);
    }
    float m = fmaxf(col.x, fmaxf(col.y, col.z));
    const float toe = m * m / (m * m + TOE);                         // faint dye sinks to black: negative space
    col.x *= gain * toe; col.y *= gain * toe; col.z *= gain * toe;
    m *= gain * toe;
    if (m > 0.6f) {
        const float nm = 0.6f + 0.4f * (1.f - __expf(-(m - 0.6f) / 0.4f));
        const float k = nm / m;
        const float hot = 0.4f * smooth(1.6f, 6.f, m);               // very dense dye burns towards white
        col.x = col.x * k + (nm - col.x * k) * hot;
        col.y = col.y * k + (nm - col.y * k) * hot;
        col.z = col.z * k + (nm - col.z * k) * hot;
    }
    out[y * W + x] = pack(make_float4(255.f * col.x, 255.f * col.y, 255.f * col.z, 255.f));
}
""" % {"toe": 0.012, "sat": 1.2}


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _clamp01(x: float) -> float:
    return min(max(float(x), 0.0), 1.0)


class FluidInk(Source):
    name = "fluid_ink"
    label = "Fluid ink"
    description = "Liquid colour swirling with vortices, pushed by orbiting emitters, with relief shading and bloom."
    params = {
        "emitter_symmetry": num(1.0, 8.0, 3.0, "Emitters (symmetry)", step=0.01, tier="regional", basic=True),
        "trail_length": num(0.0, 1.0, 0.55, "Trail length", tier="regional", basic=True),
        "vorticity": num(0.0, 50.0, 18.0, "Swirl (vorticity)", step=0.5, tier="regional"),
        "splat_radius": num(0.1, 1.0, 0.3, "Splat radius", tier="regional"),
        "emitter_orbit": num(0.0, 0.8, 0.45, "Emitter orbit radius", tier="regional"),
        "shading": num(0.0, 1.0, 0.6, "Relief shading", tier="regional"),
        "bloom": num(0.0, 1.5, 0.8, "Bloom", tier="regional"),
        "splat_kick": num(0.0, 2.0, 1.0, "Beat splat kick", tier="regional"),
        "surge_kick": num(0.0, 3.0, 1.5, "Beat flow surge", tier="regional"),
        "emitter_spin": num(-1.0, 1.0, 0.12, "Emitter spin", unit="rev/s", tier="realtime", bind="rate"),
        "pan": num(-1.0, 1.0, 0.0, "Jet bias x (pan)", tier="realtime", bind="level"),
        "tilt": num(-1.0, 1.0, 0.0, "Jet bias y (tilt)", tier="realtime", bind="level"),
        **standard_scene_params(palette="rainbow", render_scale=0.5, period=0.5, drift=0.04),
    }

    # ------------------------------------------------------------------ setup
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.SH = SIM_H
        self.SW = max(16, int(round(SIM_H * ctx.width / ctx.height)))
        SW, SH, w, h = self.SW, self.SH, self.w, self.h
        f32 = cp.float32
        self.va = cp.zeros((SH, SW, 2), f32)
        self.vb = cp.zeros((SH, SW, 2), f32)
        self.curl = cp.zeros((SH, SW), f32)
        self.div = cp.zeros((SH, SW), f32)
        self.pa = cp.zeros((SH, SW), f32)
        self.pb = cp.zeros((SH, SW), f32)
        self.da = cp.zeros((h, w, 4), f32)
        self.db = cp.zeros((h, w, 4), f32)
        self.bloom_sz = []
        self.bloom = []
        bw, bh = max(2, w // 8), max(2, h // 8)
        for _ in range(BLOOM_LEVELS):
            self.bloom_sz.append((bw, bh))
            self.bloom.append(cp.zeros((bh, bw, 4), f32))
            bw, bh = max(2, bw // 2), max(2, bh // 2)
        self.buf = cp.zeros((h, w, 4), cp.uint8)
        self.S = cp.zeros(MAX_SPLAT * 8, f32)
        self.Sh = np.zeros(MAX_SPLAT * 8, np.float32)
        self.mass = cp.zeros(2, f32)           # dye coverage meter: [0] accumulating, [1] last frame
        mod = raw_module(_SRC, ("--std=c++14", "--use_fast_math"))
        self.k = {n: mod.get_function(n) for n in ("fl_sim", "fl_advect_dye", "fl_bloom_pre", "fl_bloom_chain",
                                                    "fl_composite")}
        self.iW, self.iH = np.int32(SW), np.int32(SH)
        self.bloom_dims = tuple(np.int32(v) for wh in self.bloom_sz for v in wh)

        rng = np.random.default_rng(int(params["seed"]))
        self.emit_rand = rng.uniform(0.0, 1.0, (MAX_EMIT, 3))
        # wobble frequency multipliers in 1/100 steps, so wrapping the wobble phase at 2 pi x 1000 is invisible
        self.wob_mul = np.round((0.9 + 0.2 * self.emit_rand[:, 0]) * 100.0) / 100.0
        self.orbit = float(rng.uniform(0, TAU))
        self.spin = float(rng.uniform(0, TAU))
        self.wob = float(rng.uniform(0, TAU))
        self.drift = 0.0
        self.clock = 0.0
        self.armed = True
        self.last_fire = -1.0
        self.e_slow = _smoothstep(0.1, 0.85, _clamp01(params["energy"]))
        self.kick = 0.0
        # bursts: [x, y, dir_x, dir_y, amp, r, g, b, age, radius]
        self.bursts: list[list[float]] = []
        # numpy-seeded opening splats (Pavel starts with a random multipleSplats), then a 1.2 s pre-roll so the
        # first frame is already a living fluid
        coef = palette_coeffs(params["palette"])
        for j in range(5):
            a = rng.uniform(0, TAU)
            c = self._pal(coef, rng.uniform(0, 1))
            self.bursts.append([rng.uniform(0.2, 0.8) * SW, rng.uniform(0.2, 0.8) * SH, math.cos(a), math.sin(a),
                                rng.uniform(0.5, 0.9), *c, 0.0, float(params["splat_radius"]) * 1.5])
        pre = dict(params)
        pre["pulse"] = 0.0
        pre["speed"] = 1.0                     # the pre-roll always runs, so even speed 0 starts on a living fluid
        for _ in range(72):
            self._advance(pre, 1.0 / 60.0, live=False)

    def close(self) -> None:
        self.va = self.vb = self.da = self.db = self.bloom = self.buf = None

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _pal(coef, t: float) -> list[float]:
        out = []
        for ch in range(3):
            v = coef[ch] + coef[3 + ch] * math.cos(TAU * (coef[6 + ch] * t + coef[9 + ch]))
            out.append(min(max(v, 0.0), 1.0))
        return out

    def _inv_rr(self, radius: float) -> float:
        # Pavel: exp(-|p|^2 / r), p in height units (x scaled by aspect), r = SPLAT_RADIUS / 100 * aspect
        aspect = self.SW / self.SH
        rr = radius / 100.0 * max(aspect, 1.0) * self.SH * self.SH
        return 1.0 / max(rr, 1e-3)

    def _emitters(self, p: dict, ep: float, k: float):
        """Current emitters: list of (x, y, dir_x, dir_y, weight, palette index)."""
        SW, SH = self.SW, self.SH
        N = min(max(float(p["emitter_symmetry"]), 1.0), float(MAX_EMIT))
        R = float(p["emitter_orbit"])
        half = 0.5 * SH
        off = 7.0 * (1.0 - _smoothstep(0.0, 0.35, R))      # keeps co-located jets from cancelling at orbit 0
        bx, by = float(p["pan"]), -float(p["tilt"])
        em = []
        for i in range(MAX_EMIT):
            wgt = min(max(N - i, 0.0), 1.0)
            wgt = wgt * wgt * (3.0 - 2.0 * wgt)
            if wgt <= 0.002:
                break
            r2 = self.emit_rand[i, 1]
            ang = self.orbit + TAU * i / N
            rr = R * half * (1.0 + 0.16 * math.sin(self.wob * self.wob_mul[i] + 2.1 * i))
            x = 0.5 * SW + 1.3 * rr * math.cos(ang)
            y = 0.5 * SH + rr * math.sin(ang)
            noz = ang + 0.5 * math.pi + self.spin + 0.55 * math.sin(1.3 * self.wob + TAU * r2)
            dx, dy = math.cos(noz) + 0.9 * bx, math.sin(noz) + 0.9 * by
            n = math.hypot(dx, dy) + 1e-6
            dx, dy = dx / n, dy / n
            em.append((x + dx * off, y + dy * off, dx, dy, wgt, i / N))
        return em

    def _fire(self, p: dict, k: float, ep: float) -> None:
        """Beat: every emitter spawns a splat burst (own lifetime) along its jet, rotated by a per-beat hash."""
        coef = palette_coeffs(p["palette"])
        h = (math.sin(self.clock * 12.9898 + 78.233) * 43758.5453) % 1.0
        rot = (h - 0.5) * 1.8
        cr, sr = math.cos(rot), math.sin(rot)
        amp = k * float(p["splat_kick"]) * (3.0 / max(float(p["emitter_symmetry"]), 3.0)) ** 0.6
        if amp <= 1e-3:
            return
        for (x, y, dx, dy, wgt, idx) in self._emitters(p, ep, k):
            c = self._pal(coef, idx * p["palette_period"] + self.drift + 0.08)
            c = [v + (1.0 - v) * BURST_WHITE for v in c]                    # hotter core than the stream
            self.bursts.append([x, y, cr * dx - sr * dy, sr * dx + cr * dy, amp * wgt, *c, 0.0,
                                float(p["splat_radius"]) * 1.45])
        self.bursts = self.bursts[-MAX_BURST:]

    # ------------------------------------------------------------------ simulation
    def _advance(self, p: dict, dt: float, live: bool) -> None:
        e = _clamp01(p["energy"])
        ep = _smoothstep(0.1, 0.85, e)
        if dt > 0:
            self.e_slow += (ep - self.e_slow) * (1.0 - math.exp(-dt / 1.5))
        es = self.e_slow
        pulse = _clamp01(p["pulse"])
        k = pulse * (0.3 + 0.7 * e)
        spd = max(float(p["speed"]), 0.0)
        self.clock += dt

        if live:                                    # rising edge with hysteresis, >= 150 ms apart
            if self.armed and pulse > 0.6 and self.clock - self.last_fire >= 0.15:
                self._fire(p, k, ep)
                self.armed = False
                self.last_fire = self.clock
            elif pulse < 0.3:
                self.armed = True

        rs = 0.4 + 1.2 * ep
        self.orbit = (self.orbit + TAU * ORBIT_BASE * rs * (1.0 + 1.2 * k) * spd * dt) % (TAU * 1000.0)
        self.spin = (self.spin + TAU * float(p["emitter_spin"]) * rs * spd * dt) % (TAU * 1000.0)
        self.wob = (self.wob + 0.45 * rs * spd * dt) % (TAU * 1000.0)
        self.drift = (self.drift + float(p["drift_speed"]) * spd * dt) % 1000.0

        # the beat's main response: a rate kick on the sim clock -- the whole fluid lurches, then eases out
        self.kick = k
        dts = dt * spd * (1.0 + float(p["surge_kick"]) * k)
        if dts <= 0.0:
            return
        nsub = min(2, max(1, int(math.ceil(dts / (1.0 / 40.0) - 1e-6))))
        hs = min(dts / nsub, 1.0 / 30.0)

        # energy fan-out
        force = F_CONT * (0.55 + 0.9 * ep) * (1.0 + 0.5 * k * float(p["splat_kick"]))
        eps = float(p["vorticity"]) * (0.6 + 0.8 * ep)
        vdiss = 0.12 + 0.5 * (1.0 - ep)
        tl = float(p["trail_length"])
        ddiss = math.exp(math.log(3.0) + (math.log(0.05) - math.log(3.0)) * tl) * (1.5 - 0.85 * es)
        inj = D_CONT * (0.6 + 0.6 * ep) * min(max(math.sqrt(ddiss / 0.45), 0.45), 2.0)

        # splat list: forces per substep (x hs), dye per frame (x hs x nsub)
        coef = palette_coeffs(p["palette"])
        period = float(p["palette_period"])
        S = self.Sh
        n = 0
        hd = hs * nsub
        inv_c = self._inv_rr(float(p["splat_radius"]))
        nrm = 3.0 / max(float(p["emitter_symmetry"]), 3.0)          # more emitters share the same budget
        for (x, y, dx, dy, wgt, idx) in self._emitters(p, ep, k):
            c = self._pal(coef, idx * period + self.drift)
            f = force * wgt * hs * nrm ** 0.4
            d = inj * wgt * hd * nrm ** 0.75
            S[8 * n: 8 * n + 8] = (x, y, inv_c, dx * f, dy * f, c[0] * d, c[1] * d, c[2] * d)
            n += 1
        alive = []
        for b in self.bursts:
            a = b[8]
            if a > BURST_LIFE:
                continue
            alive.append(b)
            ev = math.exp(-a / 0.07)
            ed = (1.0 - math.exp(-(a + 0.5 * dts) / 0.025)) * math.exp(-a / 0.16)
            f = F_BURST * b[4] * ev * hs
            d = D_BURST * b[4] * ed * hd
            S[8 * n: 8 * n + 8] = (b[0], b[1], self._inv_rr(b[9]), b[2] * f, b[3] * f, b[5] * d, b[6] * d, b[7] * d)
            n += 1
            b[8] = a + dts
        self.bursts = alive
        self.S[: 8 * n].set(S[: 8 * n])
        nS = np.int32(n)

        kk = self.k
        SW, SH, iW, iH = self.SW, self.SH, self.iW, self.iH
        # one block runs every substep of the velocity solve (see fl_sim)
        kk["fl_sim"]((1, 1, 1), (32, 32, 1), (self.va, self.vb, self.curl, self.div, self.pa, self.pb, iW, iH,
                                               np.float32(hs), np.float32(math.exp(-vdiss * hs)), np.float32(eps),
                                               np.float32(VMAX), self.S, nS, np.int32(nsub), np.int32(JACOBI),
                                               self.mass))
        if nsub % 2:
            self.va, self.vb = self.vb, self.va
        sx, sy = np.float32(SW / self.w), np.float32(SH / self.h)
        launch_2d(kk["fl_advect_dye"], self.w, self.h, (self.da, self.db, self.va, np.int32(self.w), np.int32(self.h),
                                                       iW, iH, sx, sy, np.float32(hd),
                                                       np.float32(math.exp(-ddiss * hd)), self.S, nS, self.mass,
                                                       np.float32(1.0 / (self.w * self.h)),
                                                       np.float32(FILL_TARGET * (0.85 + 0.35 * es)), np.float32(FILL_K)))
        self.da, self.db = self.db, self.da

    # ------------------------------------------------------------------ render
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        self._advance(p, dt, live=True)
        kk = self.k
        w, h = self.w, self.h
        es = self.e_slow
        bloom = float(p["bloom"]) * (0.6 + 0.6 * _smoothstep(0.1, 0.8, es)) * (1.0 + 0.3 * self.kick)
        if bloom > 1e-3:
            thr = BLOOM_THR
            knee = thr * 0.7 + 1e-4
            bw, bh = self.bloom_sz[0]
            launch_2d(kk["fl_bloom_pre"], bw, bh, (self.da, self.bloom[0], np.int32(w), np.int32(h), np.int32(bw),
                                                  np.int32(bh), np.float32(thr), np.float32(thr - knee),
                                                  np.float32(2 * knee), np.float32(0.25 / knee)))
            kk["fl_bloom_chain"]((1, 1, 1), (32, 32, 1), (*self.bloom, *self.bloom_dims))
        bw, bh = self.bloom_sz[0]
        s = max(1, int(round(h / 270.0)))             # relief stencil: a fixed fraction of the height
        relief = RELIEF * h / (540.0 * s)                  # gradient per 540-line height, independent of render size
        bw1, bh1 = self.bloom_sz[1]
        launch_2d(kk["fl_composite"], w, h, (self.da, self.bloom[0], self.bloom[1], self.buf, np.int32(w), np.int32(h),
                                            np.int32(bw), np.int32(bh), np.int32(bw1), np.int32(bh1),
                                            np.float32(p["shading"]), np.float32(relief), np.int32(s),
                                            np.float32(bloom), np.float32(p["intensity"]), np.float32(GLOSS)))
        present(self.ctx, self.buf, out)
