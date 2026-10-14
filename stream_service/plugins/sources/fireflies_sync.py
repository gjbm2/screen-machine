"""Firefly Sync: a summer night where thousands of fireflies drift on slow curl noise over grass or forest
silhouettes.  Each one is a phase oscillator coupled to its neighbours (Kuramoto), so the swarm gradually locks
into travelling waves of flashes: the crowd literally finding the beat.

Source: Original (inspired by https://en.wikipedia.org/wiki/Kuramoto_model)
Author: smfx (original; Kuramoto model)
License: MIT
license_class: permissive

No code was copied or ported; everything below is written from scratch from the Kuramoto model's maths
(d theta_i / dt = omega_i + K R sin(psi - theta_i), with R e^{i psi} the order parameter of i's neighbourhood).

Model.  49 152 fireflies (float SoA, launched 2D as 256 x 192 so every warp is full) carry a world position, a
phase theta and fixed per-firefly attributes (depth hash, natural frequency, colour, brightness, activity rank,
home height, wander angle / rate).  Each frame:
  1. ff_deposit: every active firefly adds (cos theta, sin theta, 1) into a coarse 64 x 36 screen grid with
     float atomics (and, warp-reduced, into one global sum), i.e. the order parameter per cell, not O(N^2).
  2. ff_field: each cell's mean field = a Gaussian-weighted neighbourhood average of the grid (radius from
     ``coupling_range``), mixed towards the global mean as the range approaches 1 (local waves .. global sync).
  3. ff_update: phases advance by their natural frequency (spread +-13 %, plus a smooth slowly moving
     "pacemaker" frequency field that seeds travelling waves) + K * Im(Z e^{-i theta}) (Z bilinear from the
     field grid) + the beat forcing; positions ride a divergence-free curl flow (curl of a 4-wave sinusoidal
     potential with integrated phases) plus a per-firefly wander and a weak spring to a home height, scaled by
     depth (near fireflies cross the screen faster).  Each firefly then splats a Gaussian core (white-hot at
     the flash peak) into a float RGB accumulator and a bilinear deposit into a quarter-res glow buffer.
     Fireflies behind the silhouettes are occluded analytically (the silhouette function is evaluated at the
     firefly), so one accumulator serves every depth.
  4. ff_blur (x2): separable Gaussian halo on the glow buffer (``glow_size``).
  5. ff_composite: night sky gradient + faint twinkling stars, back silhouette row, ground mist (lit by the
     glow), front silhouette row, then the fireflies' core + halo light; 1 - exp tonemap, sRGB, static dither.
The flash is a fast-rise / slower-decay pulse around theta = 0, with a dim ember between flashes.

Silhouettes (``silhouette`` 0 = meadow grass, 1 = forest, 2 = none; a continuous morph): grass is two layers of
hash-placed tapering blades (front tall and swaying with an integrated wind phase, back short), forest is two
rows of tiered conifers; both rows sit on gently rolling ground and parallax with a slow, bounded side-to-side
camera dolly (pan = sum of sines of one integrated phase, so it never drifts or wraps).  ``depth_layers``
(1..4, cross-blends neighbouring integer counts) spreads the fireflies over parallax planes: 1 puts them all in
one near plane, more layers put some behind the front row and behind the back row (dimmer, smaller, occluded).

Macros (e = energy, es = energy slewed over ~1.5 s):
  energy -> coupling strength K = 0.25 + 3.0 e^1.3 rad/s (incoherent glitter at 0, partial sync / waves around
            0.5, near-total lock at 0.8+), visible count x(0.35 + 0.65 es^1.2), drift speed 0.022 + 0.05 e^1.1,
            natural flash rate 0.42 + 0.26 e Hz, halo strength x(0.6 + 0.4 e), core brightness x(0.8 + 0.3 e),
            grass sway, camera dolly rate.
  pulse  -> adds to every phase velocity (never to positions): kp = 9 * beat_sync * pulse rad/s, applied as
            kp (0.25 + 0.375 (1 - sin theta)), so fireflies late in their cycle are rushed through their flash
            and the swarm flashes together on the beat; plus a <= +35 % flash gain.
  speed  -> scales drift, wander, natural frequency, coupling, wind, mist and camera (all host-integrated).
  palette, palette_period, drift_speed -> firefly colour = pal(hash * period + drift) normalised to full
            brightness (default acid at period 0.2: warm yellow-green to gold), a slight warm shift at the flash
            peak; a faint complementary tint in the sky.
  intensity -> output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present

NW, NH = 256, 192            # firefly launch grid (N = 49 152); NW % 32 == 0 and NH % 8 == 0: full warps
N_FF = NW * NH
GX, GY = 64, 36              # coupling grid (order parameter cells)
GLOW_SCALE = 0.25            # glow buffer px per internal px
OPTIONS = ("--std=c++14", "--use_fast_math")
TAU = 2.0 * math.pi

# P layout (floats); mirrored by the #defines in the CUDA source
I_ASPECT, I_MARGIN, I_DW, I_PAN, I_DTS, I_DTR, I_W0, I_SPREAD = 0, 1, 2, 3, 4, 5, 6, 7
I_K, I_KP, I_PMAMP, I_PMT, I_FLOW, I_WANDER, I_WPH, I_SPRING = 8, 9, 10, 11, 12, 13, 14, 15
I_FLOWK = 16                 # 16..31: (kx, ky, amp, phase) x 4
I_HTOP, I_HBOT, I_AFRAC, I_LAYERS, I_CORE, I_FLASHG, I_HALO, I_SIZES = 32, 33, 34, 35, 36, 37, 38, 39
I_WG, I_WF, I_WIND, I_SWAY, I_HINT, I_WINT, I_GSCALE, I_PERIOD = 40, 41, 42, 43, 44, 45, 46, 47
I_DRIFT, I_PAL = 48, 49      # 49..60 palette
I_EMBER, I_CRAD, I_GMIX, I_EXPO, I_TWINK, I_MISTPH, I_MIST, I_GLOWC = 61, 62, 63, 64, 65, 66, 67, 68
I_WHITE, I_GSIG = 69, 70
P_LEN = 80

_SRC = HELPERS + PALETTE_CUDA + r"""
#define TAU 6.2831853f
#define I_ASPECT 0
#define I_MARGIN 1
#define I_DW 2
#define I_PAN 3
#define I_DTS 4
#define I_DTR 5
#define I_W0 6
#define I_SPREAD 7
#define I_K 8
#define I_KP 9
#define I_PMAMP 10
#define I_PMT 11
#define I_FLOW 12
#define I_WANDER 13
#define I_WPH 14
#define I_SPRING 15
#define I_FLOWK 16
#define I_HTOP 32
#define I_HBOT 33
#define I_AFRAC 34
#define I_LAYERS 35
#define I_CORE 36
#define I_FLASHG 37
#define I_HALO 38
#define I_SIZES 39
#define I_WG 40
#define I_WF 41
#define I_WIND 42
#define I_SWAY 43
#define I_HINT 44
#define I_WINT 45
#define I_GSCALE 46
#define I_PERIOD 47
#define I_DRIFT 48
#define I_PAL 49
#define I_EMBER 61
#define I_CRAD 62
#define I_GMIX 63
#define I_EXPO 64
#define I_TWINK 65
#define I_MISTPH 66
#define I_MIST 67
#define I_GLOWC 68
#define I_WHITE 69
#define I_GSIG 70

#define PAR_FRONT 0.6f
#define PAR_BACK 0.4f

__device__ __forceinline__ float ground_front(float u) {
    return 0.905f + 0.028f * __sinf(1.7f * u + 0.6f) + 0.014f * __sinf(4.3f * u + 2.0f);
}
__device__ __forceinline__ float ground_back(float u) {
    return 0.835f + 0.03f * __sinf(1.1f * u + 2.2f) + 0.012f * __sinf(3.7f * u + 0.3f);
}
// one layer of tapering grass blades (freq blades per unit), opacity with ~1 px anti-aliasing
__device__ float grass_layer(float u, float v, float freq, float hmin, float hmax, float ground, float wph,
                             float sway, float pxu, unsigned int seed)
{
    if (v >= ground) return 1.f;
    if (v < ground - hmax) return 0.f;
    const float c0 = floorf(u * freq);
    float a = 0.f;
    #pragma unroll
    for (int k = -2; k <= 2; ++k) {
        const float c = c0 + (float)k;
        const unsigned int ci = (unsigned int)(int)c;
        const float h1 = hash1(ci, 11u, seed), h2 = hash1(ci, 12u, seed), h3 = hash1(ci, 13u, seed);
        const float H = hmin + (hmax - hmin) * h2 * h2;           // mostly short, a few tall stalks
        const float t = (ground - v) / H;                          // 0 at the root, 1 at the tip
        if (t >= 1.f) continue;
        const float base = (c + 0.15f + 0.7f * h1) / freq;
        const float lean = ((h3 - 0.5f) * 2.2f + sway * __sinf(wph + base * 3.1f + h1 * 2.f)) / freq;
        const float xc = base + lean * t * t;
        const float hw = (0.44f / freq) * (1.f - t) + 0.25f * pxu;
        a = fmaxf(a, clamp01((hw - fabsf(u - xc)) / pxu + 0.5f));
    }
    return a;
}
// a row of tiered conifers standing on the ground line
__device__ float conifer_row(float u, float v, float cw, float hmin, float hmax, float ground, float gap,
                             float pxu, unsigned int seed)
{
    if (v >= ground) return 1.f;
    if (v < ground - hmax) return 0.f;
    const float c0 = floorf(u / cw);
    float a = 0.f;
    #pragma unroll
    for (int k = -1; k <= 1; ++k) {
        const float c = c0 + (float)k;
        const unsigned int ci = (unsigned int)(int)c;
        const float h1 = hash1(ci, 21u, seed), h2 = hash1(ci, 22u, seed), h3 = hash1(ci, 23u, seed);
        if (h3 < gap) continue;                                     // clearings
        const float H = hmin + (hmax - hmin) * h2;
        const float t = (ground - v) / H;
        if (t >= 1.f) continue;
        const float xc = (c + 0.25f + 0.5f * h1) * cw;
        const float W = H * 0.22f;
        float hw;
        if (t < 0.07f) {
            hw = fmaxf(W * 0.09f, 1.2f * pxu);                      // trunk
        } else {
            const float tt = (t - 0.07f) * (1.f / 0.93f);
            const float tier = tt * (5.f + 3.f * h3);
            const float fr = tier - floorf(tier);
            hw = W * (1.f - tt) * (0.6f + 0.4f * (1.f - fr)) + 0.3f * pxu;   // whorls: wide at each tier's foot
        }
        a = fmaxf(a, clamp01((hw - fabsf(u - xc)) / pxu + 0.5f));
    }
    return a;
}
// (front, back) silhouette opacity at screen (u, v) in screen-height units
__device__ float2 silhouette(float u, float v, const float* __restrict__ P, float pxu, unsigned int seed)
{
    const float wg = P[I_WG], wf = P[I_WF];
    if (wg + wf <= 0.f) return make_float2(0.f, 0.f);
    const float uf = u + P[I_PAN] * PAR_FRONT, ub = u + P[I_PAN] * PAR_BACK;
    const float gf = ground_front(uf), gb = ground_back(ub);
    float fr = 0.f, bk = 0.f;
    if (wg > 0.f) {
        fr += wg * grass_layer(uf, v, 62.f, 0.05f, 0.30f, gf, P[I_WIND], P[I_SWAY], pxu, seed);
        bk += wg * grass_layer(ub, v, 120.f, 0.025f, 0.12f, gb, P[I_WIND] + 1.3f, 0.7f * P[I_SWAY], pxu, seed + 31u);
    }
    if (wf > 0.f) {
        fr += wf * conifer_row(uf, v, 0.34f, 0.30f, 0.76f, gf, 0.35f, pxu, seed + 7u);
        bk += wf * conifer_row(ub, v, 0.19f, 0.16f, 0.42f, gb, 0.12f, pxu, seed + 13u);
    }
    return make_float2(fminf(fr, 1.f), fminf(bk, 1.f));
}
__device__ __forceinline__ float layer_z(float h, float n) {
    if (n <= 1.f) return 0.22f;
    const float k = fminf(floorf(h * n), n - 1.f);
    return k / (n - 1.f);
}
// depth 0 (near) .. 1 (far); continuous in the layer count (cross-blend of the neighbouring integers)
__device__ __forceinline__ float depth_of(float hz, float L) {
    const float Lf = floorf(L), fr = L - Lf;
    const float za = layer_z(hz, Lf), zb = layer_z(hz, Lf + 1.f);
    float h2 = hz * 7.31f; h2 -= floorf(h2);
    return clamp01(za + (zb - za) * fr + 0.08f * (h2 - 0.5f));
}
__device__ __forceinline__ float screen_u(float x, float z, const float* __restrict__ P) {
    const float par = 1.f - 0.7f * z;
    const float Dw = P[I_DW];
    float s = x - P[I_PAN] * par;
    s -= Dw * floorf(s / Dw);
    return s - P[I_MARGIN];
}
__device__ __forceinline__ float activity(float ha, float af) {
    return clamp01((af - ha) / (0.06f * af + 0.002f));
}

// 1. order parameter per coarse cell (+ global), from the current phases
extern "C" __global__
void ff_deposit(const float* __restrict__ PX, const float* __restrict__ PY, const float* __restrict__ TH,
                const float4* __restrict__ AT, float* __restrict__ grid, const int NWi, const int GXi,
                const int GYi, const float* __restrict__ P)
{
    const int gx = blockIdx.x * blockDim.x + threadIdx.x, gy = blockIdx.y * blockDim.y + threadIdx.y;
    const int i = gy * NWi + gx;
    const float4 a = AT[2 * i], b = AT[2 * i + 1];
    const float z = depth_of(a.x, P[I_LAYERS]);
    const float u = screen_u(PX[i], z, P), v = PY[i];
    const float act = activity(b.x, P[I_AFRAC]);
    float c = 0.f, s = 0.f, w = 0.f;
    if (act > 0.f) {
        __sincosf(TH[i], &s, &c);
        s *= act; c *= act; w = act;
        const int cx = min(max((int)floorf(u / P[I_ASPECT] * GXi), 0), GXi - 1);
        const int cy = min(max((int)floorf(v * GYi), 0), GYi - 1);
        float* g = grid + 3 * (cy * GXi + cx);
        atomicAdd(g, c); atomicAdd(g + 1, s); atomicAdd(g + 2, w);
    }
    #pragma unroll
    for (int o = 16; o > 0; o >>= 1) {
        c += __shfl_down_sync(0xffffffffu, c, o);
        s += __shfl_down_sync(0xffffffffu, s, o);
        w += __shfl_down_sync(0xffffffffu, w, o);
    }
    if ((threadIdx.x & 31) == 0 && w > 0.f) {
        float* G = grid + 3 * GXi * GYi;
        atomicAdd(G, c); atomicAdd(G + 1, s); atomicAdd(G + 2, w);
    }
}

// 2. neighbourhood mean field (Gaussian radius in cells), mixed towards the global mean
extern "C" __global__
void ff_field(const float* __restrict__ grid, float2* __restrict__ Z, const int GXi, const int GYi,
              const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= GXi || y >= GYi) return;
    const float r = fmaxf(P[I_CRAD], 0.5f);
    const int R = min((int)ceilf(2.f * r), 12);
    const float inv = -0.5f / (r * r);
    float sc = 0.f, ss = 0.f, sw = 0.f;
    for (int dy = -R; dy <= R; ++dy) {
        const int yy = y + dy;
        if (yy < 0 || yy >= GYi) continue;
        for (int dx = -R; dx <= R; ++dx) {
            const int xx = x + dx;
            if (xx < 0 || xx >= GXi) continue;
            const float wt = __expf((float)(dx * dx + dy * dy) * inv);
            const float* g = grid + 3 * (yy * GXi + xx);
            sc += wt * g[0]; ss += wt * g[1]; sw += wt * g[2];
        }
    }
    float zx = sc / fmaxf(sw, 0.5f), zy = ss / fmaxf(sw, 0.5f);
    const float* G = grid + 3 * GXi * GYi;
    const float gw = fmaxf(G[2], 0.5f), gm = P[I_GMIX];
    zx += (G[0] / gw - zx) * gm;
    zy += (G[1] / gw - zy) * gm;
    Z[y * GXi + x] = make_float2(zx, zy);
}

// 3. Kuramoto step + curl drift, then splat the core and the halo deposit
extern "C" __global__
void ff_update(float* __restrict__ PX, float* __restrict__ PY, float* __restrict__ TH,
               const float4* __restrict__ AT, const float2* __restrict__ Z, float* __restrict__ core,
               float* __restrict__ glow, const int W, const int H, const int GW, const int GH, const int GXi,
               const int GYi, const int NWi, const float* __restrict__ P, const unsigned int seed)
{
    const int gx = blockIdx.x * blockDim.x + threadIdx.x, gy = blockIdx.y * blockDim.y + threadIdx.y;
    const int i = gy * NWi + gx;
    const float4 a = AT[2 * i], b = AT[2 * i + 1];
    // a: depth hash, frequency, colour, brightness; b: activity rank, home height, wander angle, wander rate
    float x = PX[i], y = PY[i], th = TH[i];
    const float z = depth_of(a.x, P[I_LAYERS]);
    const float dts = P[I_DTS], dtr = P[I_DTR];
    if (dts > 0.f || dtr > 0.f) {
        const float u0 = screen_u(x, z, P);
        const float gxf = fminf(fmaxf(u0 / P[I_ASPECT] * GXi - 0.5f, 0.f), GXi - 1.001f);
        const float gyf = fminf(fmaxf(y * GYi - 0.5f, 0.f), GYi - 1.001f);
        const int ix = (int)gxf, iy = (int)gyf;
        const float tx = gxf - ix, ty = gyf - iy;
        const float2 z00 = Z[iy * GXi + ix], z10 = Z[iy * GXi + ix + 1];
        const float2 z01 = Z[(iy + 1) * GXi + ix], z11 = Z[(iy + 1) * GXi + ix + 1];
        const float zx = (1.f - ty) * ((1.f - tx) * z00.x + tx * z10.x) + ty * ((1.f - tx) * z01.x + tx * z11.x);
        const float zy = (1.f - ty) * ((1.f - tx) * z00.y + tx * z10.y) + ty * ((1.f - tx) * z01.y + tx * z11.y);
        float s, c;
        __sincosf(th, &s, &c);
        const float pm = vnoise(u0 * 1.1f + P[I_PMT], y * 1.1f + 0.37f * P[I_PMT], seed + 99u) - 0.5f;
        const float w = P[I_W0] * (1.f + P[I_SPREAD] * (2.f * a.y - 1.f)) * (1.f + P[I_PMAMP] * pm);
        const float coup = P[I_K] * (zy * c - zx * s);               // K R sin(psi - theta)
        const float force = P[I_KP] * (0.25f + 0.375f * (1.f - s));  // beat: rush late-cycle phases to flash
        th += (w + coup) * dts + force * dtr;
        th -= TAU * floorf(th * (1.f / TAU));
        // divergence-free drift: v = (d psi / dy, -d psi / dx), psi = sum a_k sin(k . p + phi_k)
        float vx = 0.f, vy = 0.f;
        #pragma unroll
        for (int k = 0; k < 4; ++k) {
            const float* F = P + I_FLOWK + 4 * k;
            const float cs = __cosf(F[0] * x + F[1] * y + F[3]) * F[2];
            vx += F[1] * cs;
            vy -= F[0] * cs;
        }
        vx *= P[I_FLOW]; vy *= P[I_FLOW];
        float sa, ca;
        __sincosf(TAU * b.z + P[I_WPH] * b.w, &sa, &ca);            // personal wander (integer rate: seamless)
        vx += ca * P[I_WANDER];
        vy += sa * P[I_WANDER] * 0.7f;
        const float home = P[I_HTOP] + (P[I_HBOT] - P[I_HTOP]) * __powf(fmaxf(b.y, 1e-4f), 0.55f);
        vy += (home - y) * P[I_SPRING];
        const float near = 0.45f + 0.8f * (1.f - z);
        x += vx * near * dts;
        y += vy * near * dts;
        x -= P[I_DW] * floorf(x / P[I_DW]);
        y = fminf(fmaxf(y, -0.3f), 1.3f);
        PX[i] = x; PY[i] = y; TH[i] = th;
    }
    // ---- draw ----
    const float u = screen_u(x, z, P), v = y;
    if (u < -0.06f || u > P[I_ASPECT] + 0.06f || v < -0.06f || v > 1.06f) return;
    const float act = activity(b.x, P[I_AFRAC]);
    if (act <= 0.f) return;
    const float ph = th * (1.f / TAU);
    const float fl = ph < 0.5f ? __expf(-ph * ph * (1.f / (0.075f * 0.075f)))
                               : __expf(-(1.f - ph) * (1.f - ph) * (1.f / (0.022f * 0.022f)));
    const float hi = P[I_HINT];
    float vis = 1.f;
    if (z > 0.38f && P[I_WG] + P[I_WF] > 0.f) {                       // behind the silhouettes: occluded
        const float2 sl = silhouette(u, v, P, 1.f / hi, seed);
        vis *= 1.f - sl.x * smooth(0.38f, 0.5f, z);
        vis *= 1.f - 0.9f * sl.y * smooth(0.68f, 0.8f, z);
    }
    const float bright = (0.55f + 0.9f * a.w * a.w) * (1.f - 0.55f * z) * vis * act * P[I_CORE];
    const float A = bright * (P[I_EMBER] + P[I_FLASHG] * fl);
    if (A < 1e-3f) return;
    float3 col = pal(a.z * P[I_PERIOD] + P[I_DRIFT] + 0.05f * fl, P + I_PAL);
    const float m = fmaxf(fmaxf(col.x, col.y), fmaxf(col.z, 0.05f));
    col.x /= m; col.y /= m; col.z /= m;                              // full-brightness hue
    col.x = col.x * col.x * (0.25f + 0.75f * col.x);                 // ~sRGB -> linear
    col.y = col.y * col.y * (0.25f + 0.75f * col.y);
    col.z = col.z * col.z * (0.25f + 0.75f * col.z);
    const float wm = P[I_WHITE] * fl;
    const float cr = col.x + (1.f - col.x) * wm, cg = col.y + (1.f - col.y) * wm, cb = col.z + (1.f - col.z) * wm;
    // core: Gaussian footprint, peak = A
    const float zn = 1.f - z;
    const float sig = fmaxf((0.9f + 2.1f * zn * zn) * P[I_SIZES], 0.55f);
    const float px = u * hi, py = v * hi;
    const int R = min((int)ceilf(2.3f * sig), 5);
    const int cx = (int)floorf(px), cy = (int)floorf(py);
    const float inv = -0.5f / (sig * sig);
    for (int dy = -R; dy <= R; ++dy) {
        const int yy = cy + dy;
        if ((unsigned int)yy >= (unsigned int)H) continue;
        const float ddy = yy + 0.5f - py;
        for (int dx = -R; dx <= R; ++dx) {
            const int xx = cx + dx;
            if ((unsigned int)xx >= (unsigned int)W) continue;
            const float ddx = xx + 0.5f - px;
            const float wt = A * __expf((ddx * ddx + ddy * ddy) * inv);
            if (wt < 2e-4f) continue;
            float* o = core + 4 * (yy * W + xx);
            atomicAdd(o, wt * cr); atomicAdd(o + 1, wt * cg); atomicAdd(o + 2, wt * cb);
        }
    }
    // halo: bilinear deposit into the quarter-res glow buffer (blurred later; peak ~ A * halo)
    const float sg = P[I_GSIG];
    const float Ag = bright * (P[I_FLASHG] * fl + 0.3f * P[I_EMBER]) * P[I_HALO] * TAU * sg * sg;
    if (Ag < 1e-4f) return;
    const float gs = P[I_GSCALE];
    const float fx = px * gs - 0.5f, fy = py * gs - 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0;
    #pragma unroll
    for (int k = 0; k < 4; ++k) {
        const int xx = x0 + (k & 1), yy = y0 + (k >> 1);
        if ((unsigned int)xx >= (unsigned int)GW || (unsigned int)yy >= (unsigned int)GH) continue;
        const float wt = Ag * ((k & 1) ? tx : 1.f - tx) * ((k >> 1) ? ty : 1.f - ty);
        float* o = glow + 4 * (yy * GW + xx);
        atomicAdd(o, wt * col.x); atomicAdd(o + 1, wt * col.y); atomicAdd(o + 2, wt * col.z);
    }
}

// 4. separable Gaussian (energy preserving: weights normalised over the full kernel, zero outside)
extern "C" __global__
void ff_blur(const float4* __restrict__ src, float4* __restrict__ dst, const int W, const int H, const int horiz,
             const float sig)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float sg = fmaxf(sig, 0.3f);
    const int R = min((int)ceilf(3.f * sg), 24);
    const float inv = -0.5f / (sg * sg);
    float4 acc = make_float4(0.f, 0.f, 0.f, 0.f);
    float ws = 0.f;
    for (int k = -R; k <= R; ++k) {
        const float w = __expf((float)(k * k) * inv);
        ws += w;
        const int xx = horiz ? x + k : x, yy = horiz ? y : y + k;
        if ((unsigned int)xx >= (unsigned int)W || (unsigned int)yy >= (unsigned int)H) continue;
        const float4 s = src[yy * W + xx];
        acc.x += w * s.x; acc.y += w * s.y; acc.z += w * s.z;
    }
    const float n = 1.f / ws;
    dst[y * W + x] = make_float4(acc.x * n, acc.y * n, acc.z * n, 0.f);
}

__device__ __forceinline__ float3 glow_at(const float4* __restrict__ g, float fx, float fy, int GW, int GH) {
    fx = fminf(fmaxf(fx, 0.f), GW - 1.001f);
    fy = fminf(fmaxf(fy, 0.f), GH - 1.001f);
    const int x0 = (int)fx, y0 = (int)fy;
    const float tx = fx - x0, ty = fy - y0;
    const float4 a = g[y0 * GW + x0], b = g[y0 * GW + x0 + 1], c = g[(y0 + 1) * GW + x0], d = g[(y0 + 1) * GW + x0 + 1];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float3(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z));
}

// 5. sky, stars, silhouettes, mist, firefly light -> tonemapped sRGB; clears the core accumulator
extern "C" __global__
void ff_composite(float4* __restrict__ core, const float4* __restrict__ glow, uchar4* __restrict__ img,
                  const int W, const int H, const int GW, const int GH, const float* __restrict__ P,
                  const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float pxu = 1.f / P[I_HINT];
    const float u = (x + 0.5f) * pxu, v = (y + 0.5f) * pxu;
    const float pan = P[I_PAN];
    // night sky: deep blue overhead, lifting towards the horizon, a faint complementary palette tint
    const float sv = clamp01(v / 0.92f);
    const float sh = sv * sv;
    float3 tint = pal(P[I_DRIFT] + 0.5f, P + I_PAL);
    float r = 0.0028f + 0.010f * sh + 0.004f * sh * tint.x;
    float g = 0.0045f + 0.016f * sh + 0.004f * sh * tint.y;
    float bl = 0.0130f + 0.024f * sh + 0.004f * sh * tint.z;
    // stars (static field, integer-rate twinkle so the wrapped phase never jumps)
    {
        const float su = u + pan * 0.08f;
        const float gsz = 260.f;
        const float fx = floorf(su * gsz), fy = floorf(v * gsz);
        const unsigned int cxi = (unsigned int)(int)fx, cyi = (unsigned int)(int)fy;
        const float hs = hash1(cxi, cyi, seed + 501u);
        if (hs < 0.03f) {
            const float ox = hash1(cxi, cyi, seed + 502u), oy = hash1(cxi, cyi, seed + 503u);
            const float dx = su * gsz - fx - (0.25f + 0.5f * ox), dy = v * gsz - fy - (0.25f + 0.5f * oy);
            const float tw = 0.6f + 0.4f * __sinf(P[I_TWINK] * (1.f + floorf(ox * 3.f)) + oy * TAU);
            const float st = __expf(-(dx * dx + dy * dy) * 6.f) * tw * (0.012f + 0.05f * oy * oy)
                             * (1.f - smooth(0.35f, 0.8f, v));
            r += st * 0.9f; g += st * 0.95f; bl += st;
        }
    }
    const float2 sl = silhouette(u, v, P, pxu, seed);
    // back row: dark, a little lifted by the haze
    r += (0.35f * r + 0.0016f - r) * sl.y;
    g += (0.35f * g + 0.0022f - g) * sl.y;
    bl += (0.35f * bl + 0.0034f - bl) * sl.y;
    const int k = y * W + x;
    const float4 c = core[k];
    core[k] = make_float4(0.f, 0.f, 0.f, 0.f);
    const float gsc = P[I_GSCALE];
    const float3 gl = glow_at(glow, (x + 0.5f) * gsc - 0.5f, (y + 0.5f) * gsc - 0.5f, GW, GH);
    // ground mist between the rows, lit by the fireflies' glow
    float mist = 0.f;
    if (P[I_MIST] > 0.f) {
        const float band = smooth(0.38f, 0.74f, v) * (1.f - smooth(0.93f, 1.05f, v));
        if (band > 0.f) {
            const float mu = u + pan * 0.5f + P[I_MISTPH];
            const float n = fbm(mu * 1.7f, v * 5.5f, seed + 77u);
            mist = band * smooth(0.3f, 0.72f, n) * P[I_MIST];
        }
    }
    r += mist * (0.010f + 0.004f * tint.x + 0.9f * gl.x);
    g += mist * (0.014f + 0.004f * tint.y + 0.9f * gl.y);
    bl += mist * (0.022f + 0.004f * tint.z + 0.9f * gl.z);
    // front row: near-black
    r += (0.0012f - r) * sl.x;
    g += (0.0016f - g) * sl.x;
    bl += (0.0022f - bl) * sl.x;
    // firefly light (core + halo), then tonemap
    const float gc = P[I_GLOWC], ex = P[I_EXPO];
    r = (r + c.x + gc * gl.x) * ex;
    g = (g + c.y + gc * gl.y) * ex;
    bl = (bl + c.z + gc * gl.z) * ex;
    const float dith = (hash1((unsigned int)x, (unsigned int)y, 911u) - 0.5f) * (1.f / 255.f);
    const float orr = __powf(clamp01(1.f - __expf(-r)), 0.4545f) + dith;
    const float og = __powf(clamp01(1.f - __expf(-g)), 0.4545f) + dith;
    const float ob = __powf(clamp01(1.f - __expf(-bl)), 0.4545f) + dith;
    img[k] = make_uchar4((unsigned char)(255.f * clamp01(orr) + 0.5f), (unsigned char)(255.f * clamp01(og) + 0.5f),
                         (unsigned char)(255.f * clamp01(ob) + 0.5f), 255);
}
"""


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _home_band(sil: float) -> tuple[float, float, float, float]:
    wg = min(max(1.0 - sil, 0.0), 1.0)
    wf = min(max(1.0 - abs(sil - 1.0), 0.0), 1.0)
    top = 0.05 + 0.13 * wg + 0.08 * wf
    bot = 0.96 - 0.03 * (wg + wf)
    return wg, wf, top, bot


class FirefliesSync(Source):
    name = "fireflies_sync"
    label = "Firefly sync"
    description = ("A summer night: thousands of fireflies drift over grass or forest silhouettes and, coupled to "
                   "their neighbours, lock into travelling waves of flashes.")
    params = {
        "density": num(0.0, 1.0, 0.5, "Density (fireflies)", tier="regional", basic=True),
        "coupling_range": num(0.0, 1.0, 0.3, "Coupling range (local waves to global sync)", tier="regional"),
        "silhouette": num(0.0, 2.0, 0.0, "Silhouette (grass, forest, none)", step=0.05, tier="regional",
                          basic=True),
        "glow_size": num(0.0, 1.0, 0.5, "Glow size", tier="regional"),
        "depth_layers": num(1.0, 4.0, 3.0, "Depth layers (parallax)", step=0.05, tier="regional"),
        "mist": num(0.0, 1.0, 0.45, "Ground mist", tier="regional"),
        "beat_sync": num(0.0, 2.0, 1.0, "Beat sync (pulse pulls flashes together)", step=0.05, tier="regional"),
        **standard_scene_params(palette="acid", render_scale=0.5, period=0.2, drift=0.01),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.gw = max(8, int(math.ceil(self.w * GLOW_SCALE)) + 1)
        self.gh = max(8, int(math.ceil(self.h * GLOW_SCALE)) + 1)
        self.aspect = self.w / self.h
        self.margin = 0.08
        self.Dw = self.aspect + 2.0 * self.margin
        seed = int(params["seed"])
        self.seed = seed & 0xFFFFFFFF
        rng = np.random.default_rng(seed)
        # per-firefly attributes: depth, frequency, colour, brightness | activity rank, home, wander angle, rate
        at = np.empty((N_FF, 8), np.float32)
        at[:, 0:6] = rng.uniform(0.0, 1.0, (N_FF, 6))
        at[:, 6] = rng.uniform(0.0, 1.0, N_FF)
        at[:, 7] = rng.integers(1, 4, N_FF).astype(np.float32)
        # attribute order in the kernel: a = (hz, hf, hc, hb), b = (ha, hh, hw, hw2)
        _, _, top, bot = _home_band(float(params["silhouette"]))
        x0 = rng.uniform(0.0, self.Dw, N_FF)
        home = top + (bot - top) * np.power(np.maximum(at[:, 5], 1e-4), 0.55)
        y0 = np.clip(home + rng.normal(0.0, 0.04, N_FF), -0.2, 1.2)
        # a loose travelling wave to start from (coupling sharpens it within seconds)
        th0 = TAU * (0.55 * x0 + 0.35 * y0 + 0.15 * np.sin(3.0 * x0)) + rng.normal(0.0, 1.1, N_FF)
        th0 = np.mod(th0, TAU)
        self.at = cp.asarray(at.reshape(-1))
        self.px = cp.asarray(x0.astype(np.float32))
        self.py = cp.asarray(y0.astype(np.float32))
        self.th = cp.asarray(th0.astype(np.float32))
        self.core = cp.zeros(self.w * self.h * 4, cp.float32)
        self.glowA = cp.zeros(self.gw * self.gh * 4, cp.float32)
        self.glowB = cp.zeros(self.gw * self.gh * 4, cp.float32)
        self.glowC = cp.zeros(self.gw * self.gh * 4, cp.float32)
        self.grid = cp.zeros(GX * GY * 3 + 4, cp.float32)
        self.Z = cp.zeros(GX * GY * 2, cp.float32)
        self.img = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.P = cp.zeros(P_LEN, cp.float32)
        self.Ph = np.zeros(P_LEN, np.float32)
        mod = raw_module(_SRC, OPTIONS)
        self.k_dep = mod.get_function("ff_deposit")
        self.k_field = mod.get_function("ff_field")
        self.k_upd = mod.get_function("ff_update")
        self.k_blur = mod.get_function("ff_blur")
        self.k_comp = mod.get_function("ff_composite")
        # curl flow: 4 waves, |k| in [1.6, 3.8] per unit, amplitude ~ 1 / |k| so each adds ~0.7 to |v|
        ang = rng.uniform(0.0, TAU, 4)
        mag = rng.uniform(1.6, 3.8, 4)
        self.flow_k = np.stack([mag * np.cos(ang), mag * np.sin(ang), 1.0 / (1.4 * mag)], axis=1)
        self.flow_w = rng.uniform(0.08, 0.22, 4) * rng.choice([-1.0, 1.0], 4)
        self.flow_ph = rng.uniform(0.0, TAU, 4)
        self.wind = 0.0
        self.wph = 0.0
        self.pmt = float(rng.uniform(0.0, 64.0))
        self.mistph = float(rng.uniform(0.0, 64.0))
        self.twink = 0.0
        self.cam = float(rng.uniform(0.0, TAU))
        self.drift = 0.0
        self.e_slow = None

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        dtc = min(max(float(dt), 0.0), 0.1)
        dts = dtc * spd
        a = 1.0 - math.exp(-dtc / 1.5) if dtc > 0 else 1.0
        self.e_slow = e if self.e_slow is None else self.e_slow + (e - self.e_slow) * a
        es = self.e_slow
        # host-integrated phases (all wrapped seamlessly)
        self.flow_ph = np.mod(self.flow_ph + self.flow_w * (0.5 + 0.8 * e) * dts, TAU)
        self.wind = _wrap(self.wind + (0.6 + 0.9 * e) * dts, TAU)
        self.wph = _wrap(self.wph + 0.35 * (0.6 + 0.8 * e) * dts, TAU)
        self.pmt = _wrap(self.pmt + 0.04 * dts, 2048.0)
        self.mistph = _wrap(self.mistph + 0.012 * (0.5 + e) * dts, 2048.0)
        self.twink = _wrap(self.twink + 1.3 * dts, TAU)
        self.cam = _wrap(self.cam + 0.035 * (0.5 + 0.8 * e) * dts, TAU)
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * dts, 1000.0)
        pan = 0.55 * math.sin(self.cam) + 0.18 * math.sin(2.0 * self.cam + 1.1)     # bounded dolly
        wg, wf, top, bot = _home_band(float(p["silhouette"]))
        cr = min(max(float(p["coupling_range"]), 0.0), 1.0)
        bs = max(float(p["beat_sync"]), 0.0)
        dens = 0.06 + 0.94 * min(max(float(p["density"]), 0.0), 1.0) ** 1.4
        sizes = self.h / 1080.0
        gsig = min((8.0 + 24.0 * float(p["glow_size"])) * sizes * GLOW_SCALE, 8.0)
        P = self.Ph
        P[:] = 0.0
        P[I_ASPECT] = self.aspect
        P[I_MARGIN] = self.margin
        P[I_DW] = self.Dw
        P[I_PAN] = pan
        P[I_DTS] = dts
        P[I_DTR] = dtc
        P[I_W0] = TAU * (0.42 + 0.26 * e)
        P[I_SPREAD] = 0.13
        P[I_K] = 0.25 + 3.0 * e ** 1.3
        P[I_KP] = 9.0 * bs * pulse * (0.6 + 0.4 * e)
        P[I_PMAMP] = 0.08 * (1.0 - _smoothstep(0.55, 1.0, cr))
        P[I_PMT] = self.pmt
        P[I_FLOW] = 0.022 + 0.05 * e ** 1.1
        P[I_WANDER] = 0.012 * (0.6 + 0.6 * e)
        P[I_WPH] = self.wph
        P[I_SPRING] = 0.12
        for k in range(4):
            P[I_FLOWK + 4 * k: I_FLOWK + 4 * k + 4] = (*self.flow_k[k], self.flow_ph[k])
        P[I_HTOP], P[I_HBOT] = top, bot
        P[I_AFRAC] = dens * (0.35 + 0.65 * es ** 1.2)
        P[I_LAYERS] = min(max(float(p["depth_layers"]), 1.0), 4.0)
        P[I_CORE] = 2.2 * (0.8 + 0.3 * e)
        P[I_FLASHG] = 1.0 + 0.35 * pulse * min(bs, 1.0)
        P[I_HALO] = 0.13 * (0.6 + 0.4 * e)
        P[I_SIZES] = sizes
        P[I_WG], P[I_WF] = wg, wf
        P[I_WIND] = self.wind
        P[I_SWAY] = 0.5 + 0.6 * e
        P[I_HINT] = float(self.h)
        P[I_WINT] = float(self.w)
        P[I_GSCALE] = GLOW_SCALE
        P[I_PERIOD] = float(p["palette_period"])
        P[I_DRIFT] = self.drift
        P[I_PAL:I_PAL + 12] = palette_coeffs(p["palette"])
        P[I_EMBER] = 0.02
        P[I_CRAD] = 1.2 + 9.0 * cr ** 1.3
        P[I_GMIX] = _smoothstep(0.55, 1.0, cr)
        P[I_EXPO] = float(p["intensity"])
        P[I_TWINK] = self.twink
        P[I_MISTPH] = self.mistph
        P[I_MIST] = float(p["mist"])
        P[I_GLOWC] = 1.0
        P[I_WHITE] = 0.45
        P[I_GSIG] = gsig
        self.P.set(P)
        self.grid.fill(0)
        self.glowA.fill(0)
        launch_2d(self.k_dep, NW, NH, (self.px, self.py, self.th, self.at, self.grid, np.int32(NW), np.int32(GX),
                                       np.int32(GY), self.P))
        launch_2d(self.k_field, GX, GY, (self.grid, self.Z, np.int32(GX), np.int32(GY), self.P))
        launch_2d(self.k_upd, NW, NH, (self.px, self.py, self.th, self.at, self.Z, self.core, self.glowA,
                                       np.int32(self.w), np.int32(self.h), np.int32(self.gw), np.int32(self.gh),
                                       np.int32(GX), np.int32(GY), np.int32(NW), self.P, np.uint32(self.seed)))
        launch_2d(self.k_blur, self.gw, self.gh, (self.glowA, self.glowB, np.int32(self.gw), np.int32(self.gh),
                                                  np.int32(1), np.float32(gsig)))
        launch_2d(self.k_blur, self.gw, self.gh, (self.glowB, self.glowC, np.int32(self.gw), np.int32(self.gh),
                                                  np.int32(0), np.float32(gsig)))
        launch_2d(self.k_comp, self.w, self.h, (self.core, self.glowC, self.img, np.int32(self.w), np.int32(self.h),
                                                np.int32(self.gw), np.int32(self.gh), self.P, np.uint32(self.seed)))
        present(self.ctx, self.img, out)
