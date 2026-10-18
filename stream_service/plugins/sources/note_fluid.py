"""Note fluid: every piano strike blooms a plume of coloured dye that rises from its key through a living fluid.

Source: https://github.com/PavelDoGreat/WebGL-Fluid-Simulation (script.js @ master, LICENSE @ master)
Author: Pavel Dobryakov (WebGL-Fluid-Simulation); stable-fluids method Jos Stam, GPU formulation Mark J. Harris
        (GPU Gems ch. 38)
License: MIT (script.js header comment and LICENSE: "MIT License  Copyright (c) 2017 Pavel Dobryakov")
license_class: permissive
Ported from GLSL by smfx (ideas/changes: Pavel's splat / curl / vorticity / divergence / pressure (Jacobi, x0.8
    warm start) / gradientSubtract / advection (with dissipation) / display (SHADING) shaders re-implemented as CUDA
    kernels on fixed float32 buffers in y-down pixel space.  Velocity and pressure live on a coarse grid of 1/8 the
    output (480x270 at 4K), dye at render_scale.  GPU Gems' 0.5 factor in the gradient subtraction.  exp()
    dissipation instead of 1/(1 + d dt).  The pointer is replaced by the piano: each onset is a splat blooming in over
    0.15 s while its injection point rises; sounding notes trickle dye and lift from their keys; the damper pedal
    pools colour; dye buoyancy lifts plumes like smoke; a slow solenoidal stir and ambient splats keep it alive.
    Dye splats are culled per block; a GPU coverage meter adds dissipation only when the frame over-fills.  Pavel's
    edge shading gets an amount and a soft directional relief; hue-preserving soft-knee tonemap with a toe.  Bloom
    and sunrays dropped.)

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

Per frame (Pavel's step order): velocity splats + ambient stir + dye buoyancy -> curl -> vorticity confinement ->
divergence -> 20 Jacobi pressure iterations -> subtract the pressure gradient -> advect velocity -> advect dye (+ dye
splats, dissipation, top vent, coverage meter) -> display (shading, gain, tonemap) at render_scale -> present.
29 launches, ~1.8-3.1 ms at 4K on the P4000 (fxtest --bench).

Host diet (4K60 plan change 4): every per-frame value (the splat list, its count, the step's scalars, shading and
gain) lives in one small "uniform arena" U, so a frame is ONE H2D copy from a pinned host slot plus fixed launches
whose arguments never change.  That fixed sequence (copy, the 28 kernels and present) is captured once per
(arena slot, output buffer) as a CUDA graph, so a frame costs 2 host CUDA calls (graph launch + the slot's event
record) instead of ~30.  Velocity / dye ping-pong by frame parity (fixed buffers, no pointer swaps), pressure stays
in pa (JACOBI even).  Four pinned slots, each guarded by an event, so the host never overwrites a slot the GPU has
not copied yet.  A graph is captured only once an output buffer has been seen two frames running (a pool buffer
that alternates stays on direct launches).  Capture or launch failing, or no pinned memory: direct launches, the
same kernels and arena (degrade, never refuse; logged once).  Buoyancy (dye lifts like warm smoke) fades out
under the ceiling and a vent dissipates dye in the top fifth, so risen colour thins away instead of packing into a
flat band.

The piano (self.sound, CONVENTIONS §2b):
  * onset (pitch, vel, age): one splat blooming in over 0.15 s (smoothstep-shaped injection, so no hard flash; an
    onset that arrives late starts part-way in but still injects its full amount).  x = the key across the width
    (A0 left, C8 right) + seeded jitter; y in the lower third, the injection point rising 6 % of the height during
    the bloom; force mostly up with a seeded lateral part, both x vel; radius larger for low notes; dye x vel.
  * sounding notes (LEVEL > 0.05): a gentle continuous trickle of dye and lift at the key, x LEVEL x emit, eased in
    over the first 0.3 s of the note, swaying slowly: pedal-held harmony keeps smoking upwards.
  * the damper pedal (slewed) lowers the dye dissipation (up to -75 %), so colours pool while it is down.
  * no sound (None or not present): ambient splats of 1-3 related colours every 2-5 s (more often with energy),
    and the stir keeps everything drifting.
Colour: circle of fifths.  index = ((pitch class x 7) mod 12) / 12 x palette_period + drift + a small octave lift,
so a diatonic chord lands on one contiguous arc of the palette (consonance reads as harmony) and modulation rotates
it.  The palette colour is squared to linear and normalised to a unit max channel before mixing.

Macros: energy (e' = smoothstep(0.1, 0.85, e)) -> splat force (0.55 + 0.9e'), curl (0.5 + 1.0e'), stir
(0.4 + 0.9e'), buoyancy (0.7 + 0.6e'), less velocity damping, ambient rate and a slight gain lift.
pulse (k = pulse (0.3 + 0.7e)) -> force x (1 + 0.8k), curl x (1 + k), stir x (1 + 0.6k): rates and amplitudes
only, never positions.  speed scales the sim
time step (and the stir / sway phases).  intensity is output gain.  palette / palette_period / drift_speed colour.
"""
from __future__ import annotations

import logging
import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present

log = logging.getLogger(__name__)

TAU = 2.0 * math.pi
SIM_DIV = 8                  # sim grid = output / 8 (480x270 at 4K)
JACOBI = 20                  # pressure iterations (even: the result lands back in pa, so graphs see fixed buffers)
assert JACOBI % 2 == 0
MAX_SPLAT = 64               # splats per frame (onset blooms first, then trickles by level)
MAX_BLOOM = 40               # blooming splats in flight
MAX_ONSETS = 24              # onsets taken per frame
HDR = 24                     # U header floats before the splat list
# U header slots for the per-frame scalars (0..11 the stir modes, 12 vmax, 13 buoyancy)
U_N, U_EDT, U_SDT, U_VDEC, U_DDEC, U_KFILL, U_VENT, U_SHADE, U_GAIN = 14, 15, 16, 17, 18, 19, 20, 21, 22
SLOTS = 4                    # pinned arena slots (frames in flight); even, so a slot fixes the frame parity
GRAPH_MAX = 16               # cached graphs (SLOTS x a few output buffers)
USE_GRAPHS = True            # False: direct launches only (the A/B check sets it per instance via .graphs)
DYE_SHARP = 1.6              # dye splat 1/r^2 relative to its velocity splat (dye radius ~0.79x)
LO_KEY, HI_KEY = 21.0, 108.0
ONSET_BLOOM_S = 0.15
AMB_BLOOM_S = 0.5
RISE = 0.06                  # the onset injection point rises this much (x height) during its bloom
ONSET_V = 2.0                # onset impulse, heights / s at vel 1
ONSET_D = 1.8                # onset dye at vel 1
TRICKLE_MIN = 0.05
TRICKLE_F = 2.0              # trickle lift, heights / s^2 at LEVEL 1
TRICKLE_D = 0.9              # trickle dye / s at LEVEL 1
AMB_V = 0.9
AMB_D = 1.3
VMAX = 4.0                   # velocity clamp, heights / s
STIR_F = 0.08                # ambient stir force, heights / s^2
BUOY = 1.2                   # dye buoyancy, heights / s^2 per unit dye
FILL_TARGET = 0.30           # mean coverage above which extra dissipation kicks in
FILL_K = 4.0
VENT = 1.2                   # extra dye dissipation (/s) at the top edge, fading out over the top fifth
OCT_SHIFT = 0.03             # palette index per octave above middle C (times 1, not period)
SIM_DT_MAX = 1.0 / 30.0

_SRC = HELPERS + r"""
#define NF_HDR %(hdr)d
#define NF_MAXS %(maxs)d
#define NF_SHARP %(sharp)ff
#define NF_TOE %(toe)ff
#define NF_N %(u_n)d
#define NF_EDT %(u_edt)d
#define NF_SDT %(u_sdt)d
#define NF_VDEC %(u_vdec)d
#define NF_DDEC %(u_ddec)d
#define NF_KFILL %(u_kfill)d
#define NF_VENT %(u_vent)d
#define NF_SHADE %(u_shade)d
#define NF_GAIN %(u_gain)d
__device__ __forceinline__ float2 ld2(const float2* v, int x, int y, int W, int H) {
    x = min(max(x, 0), W - 1); y = min(max(y, 0), H - 1); return v[y * W + x];
}
__device__ __forceinline__ float ld1(const float* v, int x, int y, int W, int H) {
    x = min(max(x, 0), W - 1); y = min(max(y, 0), H - 1); return v[y * W + x];
}
__device__ __forceinline__ float4 ldf4(const float4* v, int x, int y, int W, int H) {
    x = min(max(x, 0), W - 1); y = min(max(y, 0), H - 1); return v[y * W + x];
}
// bilinear at continuous coords (cell centres at i + 0.5), clamped at the walls (Pavel's linear sampler)
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
__device__ __forceinline__ float len3(float4 c) { return sqrtf(c.x * c.x + c.y * c.y + c.z * c.z); }
#define XY const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y; \
           if (x >= W || y >= H) return; const int i = y * W + x;

// U: header (NF_HDR floats) then n splats x 8 floats {x, y (sim cells), 1/r^2 (sim cells^-2), fx, fy (cells/s added
// this frame), r, g, b (dye added this frame)}.  Header: [0..5] stir mode 1 {ax, ay, kx, ky, phx, phy},
// [6..11] stir mode 2, [12] vmax (cells/s), [13] buoyancy (cells/s added per unit dye this frame), then this frame's
// scalars: [NF_N] splat count, [NF_EDT] vorticity eps x dt, [NF_SDT] sim dt, [NF_VDEC] / [NF_DDEC] velocity / dye
// decay, [NF_KFILL] coverage dissipation x dt, [NF_VENT] vent x dt, [NF_SHADE] shading, [NF_GAIN] gain.  Every
// per-frame value is read here, so the launches' own arguments never change (one CUDA graph replays them).

// splatShader (velocity) + the ambient stir: two drifting stream-function modes psi = sin(kx x + px) sin(ky y + py),
// force = (dpsi/dy, -dpsi/dx), divergence-free so the projection keeps it; dye buoyancy.  Rotates the coverage meter.
extern "C" __global__
void nf_vsplat(float2* __restrict__ v, const int W, const int H, const float* __restrict__ U,
               float* __restrict__ mass, const float4* __restrict__ dye, const int DW, const int DH)
{
    if (blockIdx.x == 0 && blockIdx.y == 0 && threadIdx.x == 0 && threadIdx.y == 0) { mass[1] = mass[0]; mass[0] = 0.f; }
    XY
    const int n = (int)U[NF_N];
    float2 o = v[i];
    const float cx = x + 0.5f, cy = y + 0.5f;
    for (int k = 0; k < n && k < NF_MAXS; ++k) {
        const float* s = U + NF_HDR + 8 * k;
        const float dx = cx - s[0], dy = cy - s[1];
        const float q = (dx * dx + dy * dy) * s[2];
        if (q < 9.f) { const float g = __expf(-q); o.x += g * s[3]; o.y += g * s[4]; }
    }
    #pragma unroll
    for (int m = 0; m < 2; ++m) {
        const float* S = U + 6 * m;
        float s1, c1, s2, c2;
        __sincosf(S[2] * cx + S[4], &s1, &c1);
        __sincosf(S[3] * cy + S[5], &s2, &c2);
        o.x += S[0] * s1 * c2;
        o.y -= S[1] * c1 * s2;
    }
    // buoyancy: dye lifts like warm smoke (last frame's dye at this cell), fading out under the ceiling so it
    // spreads there instead of packing into a flat band
    const float4 d = bil4(dye, cx * DW / W, cy * DH / H, DW, DH);
    o.y -= U[13] * fminf(fmaxf(d.x, fmaxf(d.y, d.z)), 2.f) * smooth(0.f, 0.3f, cy / H);
    v[i] = vclamp(o, U[12]);
}

// curlShader: w = dv/dx - du/dy (y-down)
extern "C" __global__
void nf_curl(const float2* __restrict__ v, float* __restrict__ cu, const int W, const int H)
{
    XY
    const float L = ld2(v, x - 1, y, W, H).y, R = ld2(v, x + 1, y, W, H).y;
    const float T = ld2(v, x, y - 1, W, H).x, B = ld2(v, x, y + 1, W, H).x;
    cu[i] = 0.5f * ((R - L) - (B - T));
}

// vorticityShader: f = eps w (N.y, -N.x), N = grad|w| / |grad|w||  (in place: reads only its own velocity)
extern "C" __global__
void nf_vort(float2* __restrict__ v, const float* __restrict__ cu, const int W, const int H,
             const float* __restrict__ U, const float vmax)
{
    XY
    const float edt = U[NF_EDT];
    const float L = fabsf(ld1(cu, x - 1, y, W, H)), R = fabsf(ld1(cu, x + 1, y, W, H));
    const float T = fabsf(ld1(cu, x, y - 1, W, H)), B = fabsf(ld1(cu, x, y + 1, W, H));
    const float nx = 0.5f * (R - L), ny = 0.5f * (B - T);
    const float k = edt * cu[i] / (sqrtf(nx * nx + ny * ny) + 1e-4f);
    float2 o = v[i];
    o.x += ny * k; o.y -= nx * k;
    v[i] = vclamp(o, vmax);
}

// divergenceShader, reflecting walls
extern "C" __global__
void nf_div(const float2* __restrict__ v, float* __restrict__ dv, const int W, const int H)
{
    XY
    const float2 C = v[i];
    const float L = x > 0 ? v[i - 1].x : -C.x, R = x < W - 1 ? v[i + 1].x : -C.x;
    const float T = y > 0 ? v[i - W].y : -C.y, B = y < H - 1 ? v[i + W].y : -C.y;
    dv[i] = 0.5f * (R - L + B - T);
}

// pressureShader (one Jacobi iteration); s = 0.8 on the first folds in clearShader's warm start.  NaN guard.
extern "C" __global__
void nf_jacobi(const float* __restrict__ p0, float* __restrict__ p1, const float* __restrict__ dv, const int W,
               const int H, const float s)
{
    XY
    const float L = ld1(p0, x - 1, y, W, H), R = ld1(p0, x + 1, y, W, H);
    const float T = ld1(p0, x, y - 1, W, H), B = ld1(p0, x, y + 1, W, H);
    float p = (s * (L + R + T + B) - dv[i]) * 0.25f;
    if (!(fabsf(p) < 1e7f)) p = 0.f;
    p1[i] = p;
}

// gradientSubtractShader (GPU Gems' halfrdx = 0.5), in place
extern "C" __global__
void nf_gradsub(float2* __restrict__ v, const float* __restrict__ p, const int W, const int H, const float vmax)
{
    XY
    const float L = ld1(p, x - 1, y, W, H), R = ld1(p, x + 1, y, W, H);
    const float T = ld1(p, x, y - 1, W, H), B = ld1(p, x, y + 1, W, H);
    float2 o = v[i];
    o.x -= 0.5f * (R - L); o.y -= 0.5f * (B - T);
    v[i] = vclamp(o, vmax);
}

// advectionShader (velocity through itself), exp dissipation
extern "C" __global__
void nf_advect_v(const float2* __restrict__ vin, float2* __restrict__ vout, const int W, const int H,
                 const float* __restrict__ U, const float vmax)
{
    XY
    const float dt = U[NF_SDT], decay = U[NF_VDEC];
    const float2 u = vin[i];
    float2 r = bil2(vin, x + 0.5f - dt * u.x, y + 0.5f - dt * u.y, W, H);
    r.x *= decay; r.y *= decay;
    vout[i] = vclamp(r, vmax);
}

// advectionShader (dye through the bilinear sim velocity) + splatShader (dye), dissipation and the coverage meter:
// each block culls the splat list to the splats that reach it; the frame's coverage (sum of min(max channel, 1.5))
// goes into mass[0]; last frame's (mass[1]) adds dissipation only while coverage is above `target`.  `vent` adds
// dissipation in the top fifth, so risen dye fades out under the ceiling.
extern "C" __global__
void nf_advect_dye(const float4* __restrict__ din, float4* __restrict__ dout, const float2* __restrict__ vel,
                   const int W, const int H, const int SW, const int SH, const float sx, const float sy,
                   const float* __restrict__ U, float* __restrict__ mass, const float inv_n,
                   const float target)
{
    const int n = (int)U[NF_N];
    const float dt = U[NF_SDT], decay = U[NF_DDEC], kfill = U[NF_KFILL], vent = U[NF_VENT];
    __shared__ int ids[NF_MAXS];
    __shared__ int cnt;
    __shared__ float ws[8];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    if (tid == 0) cnt = 0;
    __syncthreads();
    if (tid < n && tid < NF_MAXS) {
        const float* s = U + NF_HDR + 8 * tid;
        const float bx0 = blockIdx.x * blockDim.x * sx, bx1 = (blockIdx.x + 1) * blockDim.x * sx;
        const float by0 = blockIdx.y * blockDim.y * sy, by1 = (blockIdx.y + 1) * blockDim.y * sy;
        const float ddx = fmaxf(fmaxf(bx0 - s[0], s[0] - bx1), 0.f);
        const float ddy = fmaxf(fmaxf(by0 - s[1], s[1] - by1), 0.f);
        if ((ddx * ddx + ddy * ddy) * s[2] * NF_SHARP < 9.f) ids[atomicAdd(&cnt, 1)] = tid;
    }
    __syncthreads();
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    float m = 0.f;
    if (x < W && y < H) {
        const float gx = (x + 0.5f) * sx, gy = (y + 0.5f) * sy;        // sim-cell coords
        const float2 u = bil2(vel, gx, gy, SW, SH);
        float4 c = bil4(din, (gx - dt * u.x) / sx, (gy - dt * u.y) / sy, W, H);
        const float over = fmaxf(mass[1] * inv_n / target - 1.f, 0.f);
        const float dk = decay * __expf(-kfill * fminf(over, 4.f) - vent * (1.f - smooth(0.f, 0.2f, gy / SH)));
        c.x *= dk; c.y *= dk; c.z *= dk;
        const int nc = cnt;
        for (int j = 0; j < nc; ++j) {
            const float* s = U + NF_HDR + 8 * ids[j];
            const float dx = gx - s[0], dy = gy - s[1];
            const float q = (dx * dx + dy * dy) * s[2] * NF_SHARP;
            if (q < 9.f) { const float g = __expf(-q); c.x += g * s[5]; c.y += g * s[6]; c.z += g * s[7]; }
        }
        c.x = fminf(fmaxf(c.x, 0.f), 32.f); c.y = fminf(fmaxf(c.y, 0.f), 32.f); c.z = fminf(fmaxf(c.z, 0.f), 32.f);
        c.w = 0.f;
        dout[y * W + x] = c;
        m = fminf(fmaxf(c.x, fmaxf(c.y, c.z)), 1.5f);
    }
    // block reduction (blockDim = 32 x 8: threadIdx.x is the lane), one atomic per block
    for (int o = 16; o > 0; o >>= 1) m += __shfl_down_sync(0xffffffffu, m, o);
    if (threadIdx.x == 0) ws[threadIdx.y] = m;
    __syncthreads();
    if (threadIdx.y == 0) {
        m = threadIdx.x < 8 ? ws[threadIdx.x] : 0.f;
        for (int o = 4; o > 0; o >>= 1) m += __shfl_down_sync(0xffffffffu, m, o);
        if (threadIdx.x == 0) atomicAdd(mass, m);
    }
}

// displayShader: Pavel's SHADING (n = normalize(dx, dy, |texel|), diffuse = clamp(n.z + 0.7, 0.7, 1)) with an
// amount and a soft relief light from the upper left; gain; toe (faint dye sinks to black); hue-preserving soft knee;
// very dense dye glows towards white; linear -> ~sRGB (sqrt).
extern "C" __global__
void nf_display(const float4* __restrict__ dye, uchar4* __restrict__ out, const int W, const int H,
                const float* __restrict__ U, const float gk)
{
    XY
    const float shading = U[NF_SHADE], gain = U[NF_GAIN];
    const float4 c4 = dye[i];
    float3 col = make_float3(c4.x, c4.y, c4.z);
    if (shading > 0.f) {
        const float gx = (len3(ldf4(dye, x + 1, y, W, H)) - len3(ldf4(dye, x - 1, y, W, H))) * gk;
        const float gy = (len3(ldf4(dye, x, y + 1, W, H)) - len3(ldf4(dye, x, y - 1, W, H))) * gk;
        const float inv = rsqrtf(gx * gx + gy * gy + 1.f);              // n.z
        const float diffuse = fminf(fmaxf(inv + 0.7f, 0.7f), 1.f);
        const float rel = (gx * 0.6f + gy * 0.6f) * inv;                 // -n . (-0.6, -0.6): lit from upper left
        const float sh = shading * smooth(0.01f, 0.2f, len3(c4));        // faint wisps stay flat
        const float k = fminf(fmaxf(1.f + sh * (2.2f * (diffuse - 1.f) - 0.9f * rel), 0.25f), 1.9f);
        col.x *= k; col.y *= k; col.z *= k;
    }
    col.x *= gain; col.y *= gain; col.z *= gain;
    float m = fmaxf(col.x, fmaxf(col.y, col.z));
    const float toe = m * m / (m * m + NF_TOE);
    col.x *= toe; col.y *= toe; col.z *= toe;
    m *= toe;
    if (m > 0.5f) {
        const float nm = 0.5f + 0.5f * (1.f - __expf(-(m - 0.5f) * 2.f));
        const float k = nm / m;
        const float hot = 0.45f * smooth(1.2f, 5.f, m);
        col.x = col.x * k + (nm - col.x * k) * hot;
        col.y = col.y * k + (nm - col.y * k) * hot;
        col.z = col.z * k + (nm - col.z * k) * hot;
    }
    out[i] = pack(make_float4(255.f * sqrtf(clamp01(col.x)), 255.f * sqrtf(clamp01(col.y)),
                              255.f * sqrtf(clamp01(col.z)), 255.f));
}
""" % {"hdr": HDR, "maxs": MAX_SPLAT, "sharp": DYE_SHARP, "toe": 0.003, "u_n": U_N, "u_edt": U_EDT,
       "u_sdt": U_SDT, "u_vdec": U_VDEC, "u_ddec": U_DDEC, "u_kfill": U_KFILL, "u_vent": U_VENT,
       "u_shade": U_SHADE, "u_gain": U_GAIN}


def _smoothstep(e0: float, e1: float, x):
    t = np.clip((np.asarray(x, dtype=np.float64) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _clamp01(x: float) -> float:
    return min(max(float(x), 0.0), 1.0)


def _key_x(pitch):
    """Key position across the width: A0 (21) near the left edge, C8 (108) near the right."""
    return 0.05 + 0.90 * np.clip((np.asarray(pitch, dtype=np.float64) - LO_KEY) / (HI_KEY - LO_KEY), 0.0, 1.0)


def _radius_mul(pitch):
    """Splat radius factor: 1.5 at A0 down to 0.6 at C8."""
    return 1.5 - 0.9 * np.clip((np.asarray(pitch, dtype=np.float64) - LO_KEY) / (HI_KEY - LO_KEY), 0.0, 1.0)


class NoteFluid(Source):
    name = "note_fluid"
    label = "Note fluid"
    description = "Piano notes bloom coloured dye and lift into a swirling real-time fluid, rising from their keys."
    listens = ("notes",)
    params = {
        "curl": num(0.0, 60.0, 24.0, "Swirl (vorticity)", step=0.5, tier="regional", basic=True),
        "dissipation": num(0.05, 1.5, 0.35, "Fade (dissipation)", unit="/s", tier="regional", basic=True),
        "splat_radius": num(0.015, 0.12, 0.045, "Splat radius", step=0.005, tier="regional"),
        "force": num(0.0, 3.0, 1.0, "Note force", tier="regional"),
        "emit": num(0.0, 2.0, 1.0, "Sustained-note emission", tier="regional"),
        "shading": num(0.0, 1.0, 0.6, "Shading", tier="regional"),
        **standard_scene_params(palette="rainbow", render_scale=0.5, period=1.0, drift=0.02),
    }

    # ------------------------------------------------------------------ setup
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.sw = max(32, int(round(ctx.width / SIM_DIV)))
        self.sh = max(18, int(round(ctx.height / SIM_DIV)))
        self.dw, self.dh = internal_size(ctx, float(params["render_scale"]))
        f32 = cp.float32
        # velocity and dye ping-pong by frame parity: frame f reads [f & 1] and writes [1 - (f & 1)]
        self.v = [cp.zeros((self.sh, self.sw, 2), f32), cp.zeros((self.sh, self.sw, 2), f32)]
        self.cu = cp.zeros((self.sh, self.sw), f32)
        self.dv = cp.zeros((self.sh, self.sw), f32)
        self.pa = cp.zeros((self.sh, self.sw), f32)
        self.pb = cp.zeros((self.sh, self.sw), f32)
        self.d = [cp.zeros((self.dh, self.dw, 4), f32), cp.zeros((self.dh, self.dw, 4), f32)]
        self.buf = cp.zeros((self.dh, self.dw, 4), cp.uint8)
        self.mass = cp.zeros(2, f32)
        self.U = cp.zeros(HDR + 8 * MAX_SPLAT, f32)
        self.Uh = np.zeros(HDR + 8 * MAX_SPLAT, np.float32)
        self.u_bytes = int(self.Uh.nbytes)
        # the uniform arena's host side: SLOTS pinned copies of Uh, each reused only after its event (the GPU has
        # copied it).  No pinned memory: U.set from Uh (pageable, synchronous), direct launches.
        self.fi = 0                         # frames rendered (parity and arena slot)
        self.pin, self.ev, self.ev_used = [], [], [False] * SLOTS
        try:
            for _ in range(SLOTS):
                mem = cp.cuda.alloc_pinned_memory(self.u_bytes)
                self.pin.append((mem, np.frombuffer(mem, np.float32, self.Uh.size)))
                self.ev.append(cp.cuda.Event(block=False, disable_timing=True))
        except Exception as e:
            self.pin, self.ev = [], []
            log.warning("note_fluid: no pinned arena (%s); U.set per frame, direct launches", e)
        self.graphs = USE_GRAPHS and bool(self.pin)
        self.graph_cache: dict = {}         # (slot, out ptr, out shape) -> cupy Graph
        self.cap_stream = None
        self.last_out = None
        self.captures = 0
        self.cuda_calls = 0                 # host CUDA calls the last render issued (launches, copies, records)
        mod = raw_module(_SRC)
        self.k_vsplat = mod.get_function("nf_vsplat")
        self.k_curl = mod.get_function("nf_curl")
        self.k_vort = mod.get_function("nf_vort")
        self.k_div = mod.get_function("nf_div")
        self.k_jac = mod.get_function("nf_jacobi")
        self.k_grad = mod.get_function("nf_gradsub")
        self.k_adv_v = mod.get_function("nf_advect_v")
        self.k_adv_d = mod.get_function("nf_advect_dye")
        self.k_disp = mod.get_function("nf_display")

        self.rng = np.random.default_rng(int(params["seed"]))
        rng = self.rng
        # blooming splats (onsets and ambient), fixed slots
        z = lambda: np.zeros(MAX_BLOOM, np.float64)
        self.b_on = np.zeros(MAX_BLOOM, bool)
        self.b_age, self.b_dur, self.b_norm = z(), np.ones(MAX_BLOOM), np.ones(MAX_BLOOM)
        self.b_pitch, self.b_x, self.b_y, self.b_lat = z(), z(), z(), z()
        self.b_r, self.b_v, self.b_d = z(), z(), z()
        # per-pitch trickle sway phases, stir modes (wavelengths ~0.8 / 0.5 of the width), phases
        self.wob = rng.uniform(0.0, TAU, 128)
        self.wob_t = 0.0
        self.stir_n = np.array([[1.25, 0.85], [2.1, 1.6]])            # cycles across (width, height)
        self.stir_ph = rng.uniform(0.0, TAU, 4)
        self.stir_rate = rng.uniform(0.05, 0.11, 4) * rng.choice([-1.0, 1.0], 4)
        self.drift = 0.0
        self.ped = 0.0                      # slewed damper pedal
        self.pres = 0.0                     # slewed "piano present"
        self.amb_t = 0.0                    # time to the next ambient splat (no sound)
        self.first = True
        self.gk = 0.435 * self.dh           # dye-magnitude difference over +-1 px -> Pavel's dx / |texel| at 1080p

    # ------------------------------------------------------------------ host-side splat bookkeeping
    def _spawn(self, pitch, age0, dur, xw, yh, lat, rmul, vimp, dye) -> None:
        free = np.flatnonzero(~self.b_on)
        i = int(free[0]) if free.size else int(np.argmax(self.b_age / self.b_dur))
        a0 = max(float(age0), 0.0) / dur
        f0 = float(_smoothstep(0.0, 1.0, a0))
        self.b_on[i] = True
        self.b_age[i], self.b_dur[i], self.b_norm[i] = float(age0), dur, max(1.0 - f0, 0.05)
        self.b_pitch[i], self.b_x[i], self.b_y[i], self.b_lat[i] = pitch, xw, yh, lat
        self.b_r[i], self.b_v[i], self.b_d[i] = rmul, vimp, dye

    def _ambient(self, delay: float = 0.0) -> None:
        rng = self.rng
        n = int(rng.choice([1, 2, 3], p=[0.5, 0.35, 0.15]))
        p0 = float(rng.integers(33, 97))
        x0 = float(_key_x(p0)) + rng.uniform(-0.06, 0.06)
        for j in range(n):
            pitch = p0 + (0.0, 7.0, 4.0)[j]                          # a fifth / a third: neighbouring colours
            xw = min(max(x0 + 0.07 * (j - 0.5 * (n - 1)) + rng.uniform(-0.02, 0.02), 0.06), 0.94)
            vel = rng.uniform(0.5, 0.9)
            self._spawn(pitch, -delay - 0.12 * j, AMB_BLOOM_S, xw, rng.uniform(0.62, 0.9), rng.uniform(-0.6, 0.6),
                        1.25 * float(_radius_mul(pitch)), AMB_V * vel, AMB_D * vel)

    def _colours(self, pitch: np.ndarray, period: float, coeffs: np.ndarray) -> np.ndarray:
        """Circle-of-fifths palette colour per pitch, squared to linear, unit max channel.  (n, 3)"""
        pc = np.mod(np.rint(pitch).astype(np.int64), 12)
        t = ((pc * 7) % 12) / 12.0 * period + self.drift + OCT_SHIFT * (pitch - 60.0) / 12.0
        a, b, c, d = coeffs[0:3], coeffs[3:6], coeffs[6:9], coeffs[9:12]
        col = np.clip(a + b * np.cos(TAU * (c * t[:, None] + d)), 0.0, 1.0)
        col = col * col
        return col / np.maximum(col.max(axis=1, keepdims=True), 0.2)

    # ------------------------------------------------------------------ render
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = max(float(dt), 0.0)
        e = _clamp01(p["energy"])
        pulse = _clamp01(p["pulse"])
        spd = max(float(p["speed"]), 0.0)
        ep = float(_smoothstep(0.1, 0.85, e))
        kp = pulse * (0.3 + 0.7 * e)
        fmul = float(p["force"]) * (0.55 + 0.9 * ep) * (1.0 + 0.8 * kp)
        cmul = (0.5 + 1.0 * ep) * (1.0 + 1.0 * kp)
        sdt = min(dt * spd, SIM_DT_MAX)
        SW, SH = self.sw, self.sh
        rad = float(p["splat_radius"])
        period = float(p["palette_period"])
        coeffs = np.asarray(palette_coeffs(p["palette"]), np.float64)
        self.drift = (self.drift + float(p["drift_speed"]) * spd * dt) % 10.0
        self.wob_t = (self.wob_t + spd * dt) % 1000.0

        # ---- the piano
        snd = self.sound
        fr = getattr(snd, "frame", None) if snd is not None else None
        heard = bool(snd is not None and fr is not None and getattr(snd, "present", False))
        a_s = 1.0 - math.exp(-dt / 0.25) if dt > 0 else 0.0
        a_p = 1.0 - math.exp(-dt / 1.0) if dt > 0 else 0.0
        ped_t = float(fr.pedals[0]) if heard else 0.0
        self.ped += (min(max(ped_t, 0.0), 1.0) - self.ped) * a_s
        self.pres += ((1.0 if heard else 0.0) - self.pres) * a_p
        if heard:
            for (pitch, vel, age) in list(fr.onsets)[:MAX_ONSETS]:
                pitch, vel = float(pitch), min(max(float(vel), 0.0), 1.0)
                rng = self.rng
                self._spawn(pitch, min(max(float(age), 0.0), 0.6 * ONSET_BLOOM_S), ONSET_BLOOM_S,
                            float(_key_x(pitch)) + rng.uniform(-0.012, 0.012), 0.87 + rng.uniform(-0.03, 0.04),
                            rng.uniform(-0.35, 0.35), float(_radius_mul(pitch)) * (0.8 + 0.4 * vel),
                            ONSET_V * vel, ONSET_D * vel)
        else:
            if self.first:
                self._ambient(0.0)
                self._ambient(0.7)
                self.amb_t = 2.0
            self.amb_t -= dt
            if self.amb_t <= 0.0:
                self._ambient(0.0)
                self.amb_t = self.rng.uniform(2.0, 5.0) * (1.3 - 0.8 * ep)
        self.first = False

        # ---- splat list: blooms first, then trickles by level
        Uh = self.Uh
        S = Uh[HDR:].reshape(MAX_SPLAT, 8)
        n = 0
        on = self.b_on
        if on.any():
            idx = np.flatnonzero(on)
            dur = self.b_dur[idx]
            a0 = self.b_age[idx]
            a1 = a0 + dt
            f0 = _smoothstep(0.0, 1.0, a0 / dur)
            f1 = _smoothstep(0.0, 1.0, a1 / dur)
            frac = (f1 - f0) / self.b_norm[idx]
            self.b_age[idx] = a1
            self.b_on[idx[a1 >= dur]] = False
            live = frac > 1e-5
            idx, frac, f1 = idx[live][:MAX_SPLAT], frac[live][:MAX_SPLAT], f1[live][:MAX_SPLAT]
            k = idx.size
            if k:
                r = rad * self.b_r[idx] * SH
                imp = self.b_v[idx] * fmul * frac * SH
                col = self._colours(self.b_pitch[idx], period, coeffs) * (self.b_d[idx] * frac)[:, None]
                S[:k, 0] = self.b_x[idx] * SW
                S[:k, 1] = (self.b_y[idx] - RISE * f1) * SH
                S[:k, 2] = 1.0 / np.maximum(r * r, 0.25)
                S[:k, 3] = self.b_lat[idx] * imp
                S[:k, 4] = -imp
                S[:k, 5:8] = col
                n = k
        room = MAX_SPLAT - n
        emit = float(p["emit"])
        if heard and room > 0 and emit > 0.0 and dt > 0.0:
            lev = fr.notes[:, 0]
            cand = np.flatnonzero(lev > TRICKLE_MIN)
            if cand.size:
                if cand.size > room:
                    cand = cand[np.argsort(-lev[cand])[:room]]
                lv = lev[cand].astype(np.float64)
                age_on = np.nan_to_num(fr.notes[cand, 2].astype(np.float64), posinf=10.0)
                w = emit * lv * _smoothstep(TRICKLE_MIN, 0.12, lv) * _smoothstep(0.0, 0.3, age_on)
                pf = cand.astype(np.float64)
                ph = self.wob[cand] + 1.3 * self.wob_t
                r = rad * 0.55 * _radius_mul(pf) * SH
                lift = TRICKLE_F * w * fmul * dt * SH
                k = cand.size
                S[n:n + k, 0] = (_key_x(pf) + 0.006 * np.sin(ph)) * SW
                S[n:n + k, 1] = 0.9 * SH
                S[n:n + k, 2] = 1.0 / np.maximum(r * r, 0.25)
                S[n:n + k, 3] = 0.3 * lift * np.sin(0.7 * ph + 1.0)
                S[n:n + k, 4] = -lift
                S[n:n + k, 5:8] = self._colours(pf, period, coeffs) * (TRICKLE_D * w * dt)[:, None]
                n += k

        # ---- header: stir modes, velocity clamp, this frame's scalars (the uniform arena)
        stir = STIR_F * (0.4 + 0.9 * ep) * (1.0 + 0.6 * kp) * SH * sdt
        self.stir_ph = (self.stir_ph + self.stir_rate * (0.5 + ep) * spd * dt) % TAU
        for m in range(2):
            kx = TAU * self.stir_n[m, 0] / SW
            ky = TAU * self.stir_n[m, 1] / SH
            amp = stir / max(kx, ky) * (1.0 if m == 0 else 0.6)
            Uh[6 * m:6 * m + 6] = (amp * ky, amp * kx, kx, ky, self.stir_ph[2 * m], self.stir_ph[2 * m + 1])
        Uh[12] = VMAX * SH
        Uh[13] = BUOY * (0.7 + 0.6 * ep) * SH * sdt
        Uh[U_N] = n
        Uh[U_EDT] = float(p["curl"]) * cmul * SH / 128.0 * sdt
        Uh[U_SDT] = sdt
        Uh[U_VDEC] = math.exp(-(0.3 - 0.15 * ep) * sdt)
        # dye: pedal pools colour; with no piano the ambient splats linger a little longer
        ddiss = float(p["dissipation"]) * (1.0 - 0.75 * self.ped) * (0.7 + 0.3 * self.pres)
        Uh[U_DDEC] = math.exp(-ddiss * sdt)
        Uh[U_KFILL] = FILL_K * dt
        Uh[U_VENT] = VENT * sdt
        Uh[U_SHADE] = float(p["shading"])
        Uh[U_GAIN] = float(p["intensity"]) * (1.0 + 0.25 * ep)
        self._submit(out)

    # ------------------------------------------------------------------ the GPU step
    def _launch(self, slot: int, out) -> int:
        """The frame's fixed launch sequence on the current stream (Pavel's order): the arena copy from pinned
        slot ``slot`` (or none: the caller set U), the step, display and present.  Every argument is fixed for a
        given (slot, out), so this is also what a graph capture records.  Returns the CUDA calls issued."""
        cp = self.ctx.cp
        calls = 0
        if slot >= 0:
            cp.cuda.runtime.memcpyAsync(self.U.data.ptr, self.pin[slot][0].ptr, self.u_bytes,
                                        cp.cuda.runtime.memcpyHostToDevice, cp.cuda.get_current_stream().ptr)
            calls += 1
        ph = self.fi & 1 if slot < 0 else slot & 1
        va, vb = self.v[ph], self.v[1 - ph]
        da, db = self.d[ph], self.d[1 - ph]
        i32, f32 = np.int32, np.float32
        SW, SH, DW, DH = self.sw, self.sh, self.dw, self.dh
        W, H = i32(SW), i32(SH)
        vmax = f32(VMAX * SH)
        U = self.U
        launch_2d(self.k_vsplat, SW, SH, (va, W, H, U, self.mass, da, i32(DW), i32(DH)))
        launch_2d(self.k_curl, SW, SH, (va, self.cu, W, H))
        launch_2d(self.k_vort, SW, SH, (va, self.cu, W, H, U, vmax))
        launch_2d(self.k_div, SW, SH, (va, self.dv, W, H))
        p0, p1 = self.pa, self.pb
        for it in range(JACOBI):
            launch_2d(self.k_jac, SW, SH, (p0, p1, self.dv, W, H, f32(0.8 if it == 0 else 1.0)))
            p0, p1 = p1, p0
        launch_2d(self.k_grad, SW, SH, (va, self.pa, W, H, vmax))       # JACOBI even: the result is in pa
        launch_2d(self.k_adv_v, SW, SH, (va, vb, W, H, U, vmax))
        launch_2d(self.k_adv_d, DW, DH, (da, db, vb, i32(DW), i32(DH), W, H, f32(SW / DW), f32(SH / DH), U,
                                         self.mass, f32(1.0 / (DW * DH)), f32(FILL_TARGET)))
        launch_2d(self.k_disp, DW, DH, (db, self.buf, i32(DW), i32(DH), U, f32(self.gk)))
        present(self.ctx, self.buf, out)
        return calls + 9 + JACOBI

    def _graph_off(self, what: str, e: BaseException) -> None:
        log.warning("note_fluid: CUDA graph %s failed (%s: %s); direct launches from here on", what,
                    type(e).__name__, e)
        self.graphs = False
        self.graph_cache.clear()

    def _capture(self, slot: int, out):
        cp = self.ctx.cp
        if self.cap_stream is None:
            self.cap_stream = cp.cuda.Stream(non_blocking=True)
        cs = self.cap_stream
        with cs:
            cs.begin_capture(cp.cuda.runtime.streamCaptureModeRelaxed)    # other threads' calls stay legal
            try:
                self._launch(slot, out)
            except BaseException:
                try:
                    cs.end_capture()
                except Exception:
                    pass
                raise
            g = cs.end_capture()
        self.captures += 1
        return g

    def _submit(self, out) -> None:
        """One frame on the GPU: fill an arena slot, then one graph launch (or the direct sequence)."""
        cp = self.ctx.cp
        if not self.pin:                                     # no arena: today's pageable upload, direct launches
            self.U.set(self.Uh)
            self.cuda_calls = 1 + self._launch(-1, out)
            self.fi += 1
            return
        slot = self.fi % SLOTS
        ev = self.ev[slot]
        if self.ev_used[slot] and not ev.done:               # the GPU has not copied this slot yet (rare)
            ev.synchronize()
        self.pin[slot][1][:] = self.Uh
        calls = 0
        ran = False
        optr = out.data.ptr
        if self.graphs and self.fi >= 1:                     # frame 0 runs direct: every kernel loaded first
            key = (slot, optr, out.shape)
            g = self.graph_cache.get(key)
            if g is None and optr == self.last_out:          # this output seen two frames running: capture it
                try:
                    g = self._capture(slot, out)
                except Exception as e:
                    self._graph_off("capture", e)
                    g = None
                if g is not None:
                    if len(self.graph_cache) >= GRAPH_MAX:
                        self.graph_cache.pop(next(iter(self.graph_cache)))
                    self.graph_cache[key] = g
            if g is not None:
                try:
                    g.launch(cp.cuda.get_current_stream())
                    calls, ran = 1, True
                except Exception as e:
                    self._graph_off("launch", e)
        if not ran:
            calls = self._launch(slot, out)
        ev.record()
        self.ev_used[slot] = True
        self.cuda_calls = calls + 1
        self.last_out = optr
        self.fi += 1

    def close(self) -> None:
        self.graph_cache = {}
        self.cap_stream = None
        self.v = self.d = None
        self.cu = self.dv = self.pa = self.pb = None
        self.buf = self.mass = self.U = None
        self.pin, self.ev = [], []
