"""Physarum: a slime-mould transport network grown by a million trail-following agents.

Source: Original (inspired by https://cargocollective.com/sagejenson/physarum and https://github.com/fogleman/physarum)
Author: smfx.  Algorithm: Jeff Jones, "Characteristics of pattern formation and evolution in approximations of
        Physarum transport networks", Artificial Life 16(2), 2010; popularised by Sage Jenson; reference
        implementation Michael Fogleman (fogleman/physarum, MIT) was read for ideas only, no code copied
License: MIT
license_class: permissive

Model (Jones 2010): each agent senses a trail map at three points (left / ahead / right, ``sensor_angle`` apart, at
``sensor_distance``), turns by ``turn_angle`` towards the strongest, steps forward and deposits; the trail map is
3x3-diffused and decays every tick.  The agents self-organise into veins, rivers and membranes that keep
re-routing.  Up to three species share the map: ``species_mix`` blends continuously from one species (everyone
follows the total trail) to three species that follow their own trail and are repelled by the others'.

Implementation: 1M agents as SoA float buffers (x, y, heading; species = index % 3), launched 2D (1024 x 1024);
a fixed 960x540 float4 trail map (one channel per species) so the look is identical at every output size; the sim
ticks at a fixed 60 Hz of stream time (<= 3 ticks per frame).  Agents are numpy-seeded in setup along ridged-noise
"veins" (headings along the veins) and the trail map is pre-seeded with the same veins, so the first frame already
reads as a network.  Kept alive (Jones networks otherwise coarsen into a few static loops within a minute):
  * exploration: a few agents per tick hop to a random spot and stay only if it is a void, sprouting new filaments;
  * a slowly drifting, torus-periodic noise field varies the sensor distance across the frame (+-35%), so fine and
    coarse regions keep sliding and the boundaries re-route; a slow LFO breathes sensor distance / angle.
The colour pass reads the trail bilinearly at ``render_scale``; exposure tracks the trail's mean on the host (the
diffusion conserves mass, so it is computed exactly without a read-back); the palette index is the log density
(filaments and rivers take different colours), with an emboss sheen, a hot core and a quarter-res gaussian bloom;
then ``present()`` upsamples to the output.  ~2.4 ms at 4K on the P4000.

Regional knobs: sensor_angle / sensor_distance (network scale, basic) / turn_angle (the Jones parameters);
trail_decay (persistence per 60 Hz tick); species_mix (basic; one species -> three rivals: cross-species weight
1 - 2m, plus per-species sensor variety); density (active agents 8-100%, exploration rate and exposure: sparse
filaments -> filled membranes); glow (tonemap gamma = glow^-0.8 plus bloom).  Beat gains (regional):
surge_kick (flow lurch + fresh-trail deposit + vein flash) and scatter_kick (turn shimmer + scatter tears).

Macros:
  energy (e' = smoothstep(0.1, 0.85, e)) -> sim rate (step, deposit, decay and diffusion all scale with it)
      x(0.4 + 1.2e'); fresh-trail deposit strength (smoothstep); sensor distance up to +30% (slewed 2 s, the
      network coarsens); wander jitter (e'^1.5); exploration rate; brightness and hot-core.
  pulse  (k = pulse * (0.3 + 0.7e)), two targets:
      surge   (k * surge_kick): a rate lurch into the sim (it is integrated by the agents, so it lurches and eases
              out), a deposit-gain kick (fresh paths win, so the network re-routes) and a flash on the bright veins
              only (<= +35%, localised, so no full-frame luma swing);
      scatter (k * scatter_kick): a turn-jitter kick (k^2, the veins shimmer and re-form) and, on each rising edge
              of the eased pulse (hysteresis 0.6 / 0.3, >= 150 ms), a scatter burst: agents in a disc at a random
              spot turn outward (headings only, never positions), tearing a hole that heals over the next second.
  speed  -> multiplies the sim rate (the network evolves faster / slower; 0 freezes it) and every phase.
  palette -> pal(log density * 0.3 * period + species * 0.33 * species_mix + drift).
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

SIM_W, SIM_H = 960, 540          # trail map (fixed, independent of output size)
N_ROW = 1024                     # agents per launch row
N_AGENTS = N_ROW * 1024          # 1,048,576
TICK_HZ = 60.0
MAX_TICKS = 3
OPTIONS = ("--std=c++14", "--use_fast_math")

_SRC = HELPERS + PALETTE_CUDA + r"""
#define PI_F 3.14159265f
#define TAU_F 6.2831853f

__device__ __forceinline__ int wrapi(int v, int n) { v %= n; return v < 0 ? v + n : v; }
__device__ __forceinline__ float wrapf(float v, float n) { return v - n * floorf(v / n); }
// Value noise periodic over a gw x gh lattice (so it tiles on the sim torus).
__device__ float pnoise(float x, float y, int gw, int gh, unsigned int seed) {
    const float fx = floorf(x), fy = floorf(y);
    const int ix = (int)fx, iy = (int)fy;
    float tx = x - fx, ty = y - fy;
    tx = tx * tx * (3.f - 2.f * tx); ty = ty * ty * (3.f - 2.f * ty);
    const int x0 = wrapi(ix, gw), x1 = wrapi(ix + 1, gw), y0 = wrapi(iy, gh), y1 = wrapi(iy + 1, gh);
    const float a = hash1(x0, y0, seed), b = hash1(x1, y0, seed), c = hash1(x0, y1, seed), d = hash1(x1, y1, seed);
    return (1.f - ty) * ((1.f - tx) * a + tx * b) + ty * ((1.f - tx) * c + tx * d);
}

// Sensed value for species s: own trail + w * (others' trail).  w = 1: one species (follow the total);
// w = -1: three species repelling each other.
__device__ __forceinline__ float sense(const float4* __restrict__ T, float x, float y, int W, int H, int s, float w) {
    const float4 t = T[wrapi((int)floorf(y), H) * W + wrapi((int)floorf(x), W)];
    const float own = s == 0 ? t.x : (s == 1 ? t.y : t.z);
    return own + w * (t.x + t.y + t.z - own);
}

// AP: [0] active count [1] sensor dist [2] sensor angle (rad) [3] turn (rad/tick) [4] step (px/tick)
//     [5] deposit [6] jitter (rad/tick) [7] cross-species weight w [8] species_mix (variety) [9] burst on
//     [10] burst x [11] burst y [12] burst radius [13] burst strength [14] scale-field amount
//     [15] [16] scale-field phase x, y (lattice units) [17] [18] scale-field lattice cells across W, H
//     [19] explore probability per tick [20] void threshold (trail units)
extern "C" __global__
void phys_agents(float* __restrict__ X, float* __restrict__ Y, float* __restrict__ A, float4* __restrict__ T,
                 const int W, const int H, const float* __restrict__ AP, const unsigned int tick,
                 const unsigned int seed)
{
    const int i = (blockIdx.y * blockDim.y + threadIdx.y) * 1024 + blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= (int)AP[0]) return;
    const int s = i % 3;
    float x = X[i], y = Y[i], a = A[i];
    const float m = AP[8];
    // a slowly drifting noise field varies the network scale across the frame (fine here, coarse there), so the
    // boundaries keep re-routing; species variety (only with species_mix): species 0 senses nearer and wider
    const float nz = pnoise(x * AP[17] / W + AP[15], y * AP[18] / H + AP[16], (int)AP[17], (int)AP[18], seed + 99u);
    const float sd = AP[1] * (1.f + AP[14] * (2.f * nz - 1.f)) * (1.f + 0.25f * m * (float)(s - 1));
    const float sa = AP[2] * (1.f - 0.2f * m * (float)(s - 1));
    const float w = AP[7];
    const float F = sense(T, x + sd * cosf(a), y + sd * sinf(a), W, H, s, w);
    const float L = sense(T, x + sd * cosf(a - sa), y + sd * sinf(a - sa), W, H, s, w);
    const float R = sense(T, x + sd * cosf(a + sa), y + sd * sinf(a + sa), W, H, s, w);
    const float r0 = hash1((unsigned int)i, tick, seed);
    const float r1 = hash1((unsigned int)i, tick, seed + 7u);
    const float turn = AP[3];
    if (F > L && F > R) {
    } else if (F < L && F < R) {
        a += (r0 < 0.5f ? -turn : turn);
    } else if (L > R) {
        a -= turn;
    } else if (R > L) {
        a += turn;
    }
    a += (r1 - 0.5f) * 2.f * AP[6];
    if (AP[9] > 0.f) {                                   // scatter burst: turn outward from the burst centre
        float dx = x - AP[10], dy = y - AP[11];
        dx -= W * rintf(dx / W); dy -= H * rintf(dy / H);
        const float d2 = dx * dx + dy * dy, R2 = AP[12] * AP[12];
        if (d2 < R2 && hash1((unsigned int)i, tick, seed + 13u) < AP[13] * (1.f - 0.5f * d2 / R2))
            a = atan2f(dy, dx) + (r0 - 0.5f) * 0.6f;
    }
    // exploration: a few agents per tick hop to a random spot; if it is a void (trail below a fraction of the
    // mean) they stay there and sprout new filaments, so the network keeps subdividing instead of coarsening
    if (hash1((unsigned int)i, tick, seed + 21u) < AP[19]) {
        const float nx = hash1((unsigned int)i, tick, seed + 22u) * W, ny = hash1((unsigned int)i, tick, seed + 23u) * H;
        const float4 tv = T[min((int)ny, H - 1) * W + min((int)nx, W - 1)];
        if (tv.x + tv.y + tv.z < AP[20]) { x = nx; y = ny; a = (r1 - 0.5f) * TAU_F; }
    }
    a = a - TAU_F * floorf((a + PI_F) / TAU_F);          // keep the heading in [-pi, pi) for fast-math trig
    x = wrapf(x + AP[4] * cosf(a), (float)W);
    y = wrapf(y + AP[4] * sinf(a), (float)H);
    X[i] = x; Y[i] = y; A[i] = a;
    const int c = wrapi((int)floorf(y), H) * W + wrapi((int)floorf(x), W);
    atomicAdd(((float*)T) + 4 * c + s, AP[5]);
}

// 3x3 box diffusion (mixed in by dw) and decay; wraps (torus), so it conserves the mass before decay.
extern "C" __global__
void phys_diffuse(const float4* __restrict__ T, float4* __restrict__ O, const int W, const int H,
                  const float dw, const float decay)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    float4 s = make_float4(0.f, 0.f, 0.f, 0.f);
    #pragma unroll
    for (int j = -1; j <= 1; ++j) {
        const int yy = wrapi(y + j, H) * W;
        #pragma unroll
        for (int k = -1; k <= 1; ++k) {
            const float4 t = T[yy + wrapi(x + k, W)];
            s.x += t.x; s.y += t.y; s.z += t.z;
        }
    }
    const float4 c = T[y * W + x];
    const float a = dw * (1.f / 9.f), b = 1.f - dw;
    O[y * W + x] = make_float4((b * c.x + a * s.x) * decay, (b * c.y + a * s.y) * decay, (b * c.z + a * s.z) * decay, 0.f);
}

__device__ __forceinline__ float4 tbil(const float4* __restrict__ T, float fx, float fy, int W, int H) {
    fx -= 0.5f; fy -= 0.5f;
    const float x0f = floorf(fx), y0f = floorf(fy);
    const float tx = fx - x0f, ty = fy - y0f;
    const int x0 = wrapi((int)x0f, W), x1 = wrapi((int)x0f + 1, W);
    const int y0 = wrapi((int)y0f, H) * W, y1 = wrapi((int)y0f + 1, H) * W;
    const float4 a = T[y0 + x0], b = T[y0 + x1], c = T[y1 + x0], d = T[y1 + x1];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float4(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z), 0.f);
}
__device__ __forceinline__ float tsum(float4 t) { return t.x + t.y + t.z; }

// Bloom source: 4x4 box-downsample of the trail map (W/4 x H/4).
extern "C" __global__
void phys_down(const float4* __restrict__ T, float4* __restrict__ B, const int W, const int H)
{
    const int bw = W / 4, bh = H / 4;
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= bw || y >= bh) return;
    float4 s = make_float4(0.f, 0.f, 0.f, 0.f);
    for (int j = 0; j < 4; ++j)
        for (int k = 0; k < 4; ++k) {
            const float4 t = T[(4 * y + j) * W + 4 * x + k];
            s.x += t.x; s.y += t.y; s.z += t.z;
        }
    B[y * bw + x] = make_float4(s.x * 0.0625f, s.y * 0.0625f, s.z * 0.0625f, 0.f);
}

// 9-tap gaussian (sigma ~2 texels), horizontal (dx=1) or vertical (dx=0), wrapping.
extern "C" __global__
void phys_blur(const float4* __restrict__ S, float4* __restrict__ D, const int w, const int h, const int dx)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float k[5] = {0.2042f, 0.1802f, 0.1238f, 0.0663f, 0.0276f};
    float4 s = make_float4(0.f, 0.f, 0.f, 0.f);
    #pragma unroll
    for (int i = -4; i <= 4; ++i) {
        const int xx = dx ? wrapi(x + i, w) : x, yy = dx ? y : wrapi(y + i, h);
        const float4 t = S[yy * w + xx];
        const float kk = k[i < 0 ? -i : i];
        s.x += kk * t.x; s.y += kk * t.y; s.z += kk * t.z;
    }
    D[y * w + x] = s;
}

// Species colours blended by their share of the trail (identical when species_mix = 0).
__device__ __forceinline__ float3 species_col(float4 t, float idx, float m3, const float* __restrict__ P) {
    const float inv = 1.f / fmaxf(tsum(t), 1e-9f);
    const float3 c0 = pal(idx, P), c1 = pal(idx + m3, P), c2 = pal(idx + 2.f * m3, P);
    const float w0 = t.x * inv, w1 = t.y * inv, w2 = t.z * inv;
    return make_float3(w0 * c0.x + w1 * c1.x + w2 * c2.x, w0 * c0.y + w1 * c1.y + w2 * c2.y,
                       w0 * c0.z + w1 * c1.z + w2 * c2.z);
}

// CP: [0..11] palette [12] period [13] drift [14] exposure [15] gamma [16] species_mix [17] gain
//     [18] hot core [19] emboss [20] footprint (sim px per pixel) [21] 1/mean trail [22] bloom
//     [23] beat flash (applied to the bright veins only, so it stays localised)
extern "C" __global__
void phys_colour(const float4* __restrict__ T, const float4* __restrict__ B, uchar4* __restrict__ out,
                 const int W, const int H, const int w, const int h, const float* __restrict__ CP)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float sx = (x + 0.5f) * W / w, sy = (y + 0.5f) * H / h;
    const float fp = CP[20];
    float4 t;
    if (fp > 1.2f) {                                     // minifying: 2x2 box of bilinear taps
        const float o = 0.25f * fp;
        const float4 a = tbil(T, sx - o, sy - o, W, H), b = tbil(T, sx + o, sy - o, W, H);
        const float4 c = tbil(T, sx - o, sy + o, W, H), d = tbil(T, sx + o, sy + o, W, H);
        t = make_float4(0.25f * (a.x + b.x + c.x + d.x), 0.25f * (a.y + b.y + c.y + d.y),
                        0.25f * (a.z + b.z + c.z + d.z), 0.f);
    } else {
        t = tbil(T, sx, sy, W, H);
    }
    const float D = fmaxf(tsum(t), 0.f);
    const float expo = CP[14], im = CP[21];
    float v = powf(fmaxf(1.f - expf(-D * expo), 1e-6f), CP[15]);
    // emboss: light from the upper left over the trail "height" (a soft sheen on the veins)
    const float st = fmaxf(fp, 1.f) * 1.5f;
    const float gx = expf(-tsum(tbil(T, sx - st, sy, W, H)) * expo) - expf(-tsum(tbil(T, sx + st, sy, W, H)) * expo);
    const float gy = expf(-tsum(tbil(T, sx, sy - st, W, H)) * expo) - expf(-tsum(tbil(T, sx, sy + st, W, H)) * expo);
    const float lit = clamp01(1.f + CP[19] * (-0.7f * gx - 0.7f * gy));
    // colour index from the log density: thin filaments and thick rivers take different palette colours
    const float m3 = 0.3333333f * CP[16];
    const float idx = log2f(1.f + D * im) * 0.3f * CP[12] + CP[13];
    const float3 col = species_col(t, idx, m3, CP);
    const float hot = smooth(0.45f, 1.f, v);
    const float core = (CP[18] + 0.6f * CP[23]) * smooth(0.6f, 1.f, v);
    const float g = 255.f * CP[17] * (1.f + CP[23] * hot);
    // bloom: the blurred, downsampled trail, coloured the same way
    const float4 bt = tbil(B, sx * 0.25f, sy * 0.25f, W / 4, H / 4);
    const float BD = fmaxf(tsum(bt), 0.f);
    const float hb = CP[22] * (1.f - expf(-BD * expo * 0.6f));
    const float3 hc = species_col(bt, log2f(1.f + BD * im) * 0.3f * CP[12] + CP[13] - 0.08f, m3, CP);
    const float vl = v * lit;
    float3 o;
    o.x = (col.x + core * (1.f - col.x)) * vl; o.y = (col.y + core * (1.f - col.y)) * vl;
    o.z = (col.z + core * (1.f - col.z)) * vl;
    o.x = 1.f - (1.f - o.x) * (1.f - hb * hc.x); o.y = 1.f - (1.f - o.y) * (1.f - hb * hc.y);
    o.z = 1.f - (1.f - o.z) * (1.f - hb * hc.z);
    out[y * w + x] = pack(make_float4(o.x * g, o.y * g, o.z * g, 255.f));
}
"""


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _value_noise(rng, W: int, H: int, cells: int) -> np.ndarray:
    """Smooth periodic value noise (W x H) with ``cells`` lattice cells across the height; tiles on the torus."""
    gh = max(2, cells)
    gw = max(2, int(round(cells * W / H)))
    g = rng.random((gh, gw)).astype(np.float32)
    ys = (np.arange(H, dtype=np.float32) + 0.5) * gh / H
    xs = (np.arange(W, dtype=np.float32) + 0.5) * gw / W
    y0 = np.floor(ys).astype(np.int64)
    x0 = np.floor(xs).astype(np.int64)
    ty = ys - y0
    tx = xs - x0
    ty = ty * ty * (3 - 2 * ty)
    tx = tx * tx * (3 - 2 * tx)
    y1, x1 = (y0 + 1) % gh, (x0 + 1) % gw
    y0, x0 = y0 % gh, x0 % gw
    a = g[np.ix_(y0, x0)]
    b = g[np.ix_(y0, x1)]
    c = g[np.ix_(y1, x0)]
    d = g[np.ix_(y1, x1)]
    return ((1 - ty)[:, None] * ((1 - tx)[None, :] * a + tx[None, :] * b)
            + ty[:, None] * ((1 - tx)[None, :] * c + tx[None, :] * d))


def _ridges(rng, W: int, H: int) -> np.ndarray:
    """Ridged fbm in [0, 1]: thin bright vein-like lines."""
    n = 0.62 * _value_noise(rng, W, H, 4) + 0.28 * _value_noise(rng, W, H, 9) + 0.10 * _value_noise(rng, W, H, 19)
    return (1.0 - np.abs(2.0 * n - 1.0)).astype(np.float32)


class Physarum(Source):
    name = "physarum"
    label = "Physarum"
    description = ("A million slime-mould agents follow their own trails and self-organise into glowing veins, "
                   "rivers and membranes that pulse and re-route.")
    params = {
        "sensor_angle": num(10.0, 90.0, 25.0, "Sensor angle", step=1, unit="°", tier="regional"),
        "sensor_distance": num(2.0, 40.0, 20.0, "Sensor distance (network scale)", step=0.5, unit="px",
                               tier="regional", basic=True),
        "turn_angle": num(5.0, 60.0, 45.0, "Turn angle", step=1, unit="°", tier="regional"),
        "trail_decay": num(0.8, 0.99, 0.9, "Trail persistence", step=0.005, tier="regional"),
        "species_mix": num(0.0, 1.0, 0.5, "Species (one -> three rivals)", tier="regional", basic=True),
        "density": num(0.0, 1.0, 0.55, "Density (filaments -> membranes)", tier="regional"),
        "glow": num(0.4, 1.5, 0.9, "Glow", step=0.05, tier="regional"),
        "surge_kick": num(0.0, 2.0, 1.0, "Beat surge (flow lurch, fresh trails, vein flash)", step=0.05,
                          tier="regional"),
        "scatter_kick": num(0.0, 2.0, 1.0, "Beat scatter (shimmer + tears)", step=0.05, tier="regional"),
        **standard_scene_params(palette="rainbow", render_scale=0.5, period=1.0, drift=0.03),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        mod = raw_module(_SRC, OPTIONS)
        self.k_agents = mod.get_function("phys_agents")
        self.k_diffuse = mod.get_function("phys_diffuse")
        self.k_down = mod.get_function("phys_down")
        self.k_blur = mod.get_function("phys_blur")
        self.k_colour = mod.get_function("phys_colour")
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        rng = np.random.default_rng(self.seed)
        # ---- seed: three ridged-noise vein fields (one per species); agents sampled on the veins, heading along them
        W, H = SIM_W, SIM_H
        trail = np.zeros((H, W, 4), np.float32)
        X = np.empty(N_AGENTS, np.float32)
        Y = np.empty(N_AGENTS, np.float32)
        A = np.empty(N_AGENTS, np.float32)
        idx = np.arange(N_AGENTS)
        for s in range(3):
            r = _ridges(rng, W, H)
            pdf = r ** 10
            trail[:, :, s] = 0.35 * pdf
            sel = idx[idx % 3 == s]
            cells = rng.choice(W * H, size=sel.size, p=(pdf / pdf.sum()).ravel())
            cy, cx = np.divmod(cells, W)
            X[sel] = cx + rng.random(sel.size)
            Y[sel] = cy + rng.random(sel.size)
            gy, gx = np.gradient(r)
            ang = np.arctan2(gy, gx)[cy, cx] + np.pi / 2 + np.where(rng.random(sel.size) < 0.5, 0.0, np.pi)
            A[sel] = (ang + rng.normal(0.0, 0.25, sel.size)).astype(np.float32)
        A[:] = (A + np.pi) % (2 * np.pi) - np.pi
        self.X, self.Y, self.A = cp.asarray(X), cp.asarray(Y), cp.asarray(A)
        self.T = cp.asarray(trail)
        self.T2 = cp.zeros_like(self.T)
        self.B = cp.zeros((H // 4, W // 4, 4), cp.float32)
        self.B2 = cp.zeros_like(self.B)
        self.mass = float(trail.sum(dtype=np.float64))        # host mirror of the trail's total mass
        self.mass_s = None
        self.AP = cp.zeros(24, cp.float32)
        self.APh = np.zeros(24, np.float32)
        self.CP = cp.zeros(32, cp.float32)
        self.CPh = np.zeros(32, np.float32)
        self.rng = rng
        self.acc = 0.0                    # fractional sim ticks
        self.tick = 0
        self.drift = 0.0
        self.lfo = float(rng.uniform(0, 2 * math.pi))
        self.fx, self.fy = float(rng.uniform(0, 5)), float(rng.uniform(0, 3))   # scale-field phases (lattice units)
        self.e_slow = None
        self.pulse_armed = True
        self.last_burst_t = -1.0
        self.t = 0.0
        self.burst = None                 # (x, y, radius, strength) to apply on the next tick
        self.explore = 0.003              # base explore probability per agent per tick

    def close(self) -> None:
        self.X = self.Y = self.A = self.T = self.T2 = self.B = self.B2 = self.buf = None

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        ep = _smoothstep(0.1, 0.85, e)
        a_slow = 1.0 - math.exp(-dt / 2.0) if dt > 0 else 1.0
        self.e_slow = ep if self.e_slow is None else self.e_slow + (ep - self.e_slow) * a_slow
        k0 = pulse * (0.3 + 0.7 * e)
        g = float(p["scatter_kick"])
        k = k0 * float(p["surge_kick"])          # surge: rate lurch, deposit kick, vein flash
        kt = k0 * g                              # scatter: turn-jitter shimmer (+ bursts on rising edges)
        self.t += dt

        # ---- spawn: scatter burst on a rising edge of the beat envelope (hysteresis, >= 150 ms apart)
        if pulse < 0.3:
            self.pulse_armed = True
        if self.pulse_armed and pulse > 0.6 and g > 0.02 and self.t - self.last_burst_t >= 0.15 and dt > 0:
            self.pulse_armed = False
            self.last_burst_t = self.t
            rad = (18.0 + 30.0 * ep) * (0.6 + 0.4 * min(g, 1.5))
            self.burst = (float(self.rng.uniform(0, SIM_W)), float(self.rng.uniform(0, SIM_H)), rad,
                          min(1.0, 0.55 * g) * (0.5 + 0.5 * e))

        # ---- sim rate: step, deposit, decay and diffusion all scale with it; the beat lurches it
        rate = spd * (0.4 + 1.2 * ep) * (1.0 + 0.9 * k)
        self.lfo = math.fmod(self.lfo + 0.07 * spd * (0.6 + 0.8 * ep) * dt, 2 * math.pi)
        fs = 0.035 * spd * (0.5 + ep) * dt
        self.fx += fs
        self.fy += 0.6 * fs
        dens = float(p["density"])
        frac = 0.08 + 0.92 * dens ** 1.5
        nact = int(N_AGENTS * frac) // 3 * 3
        m = float(p["species_mix"])
        sd = float(p["sensor_distance"]) * (1.0 + 0.3 * self.e_slow) * (1.0 + 0.06 * math.sin(self.lfo))
        sa = math.radians(float(p["sensor_angle"])) * (1.0 + 0.12 * math.sin(2.0 * self.lfo + 1.0))
        turn = math.radians(float(p["turn_angle"]))
        step = 1.0 * rate
        # deposit per agent per tick: normalised so the trail mass is independent of the agent count;
        # energy strengthens fresh deposits, the beat kicks them (fresh paths win: the network re-routes)
        dep = 0.06 / frac * rate * (0.6 + 0.6 * _smoothstep(0.0, 1.0, e)) * (1.0 + 1.5 * min(k, 1.5))
        jit = (0.04 + 0.22 * ep ** 1.5 + 0.28 * min(kt, 1.5) ** 2) * math.sqrt(max(rate, 0.0))
        decay = float(p["trail_decay"]) ** rate
        dw = min(1.0, 0.5 * rate)

        if dt <= 0:
            n = 1 if self.tick == 0 else 0
        else:
            self.acc += dt * TICK_HZ
            n = int(self.acc + 1e-4)                  # tolerate float error so 60 fps gives exactly 1 tick/frame
            self.acc = max(self.acc - n, 0.0)
            n = min(n, MAX_TICKS)

        AP = self.APh
        AP[0:9] = (nact, sd, sa, turn, step, dep, jit, 1.0 - 2.0 * m, m)
        AP[14:21] = (0.35, self.fx % 5.0, self.fy % 3.0, 5, 3, self.explore * (0.3 + 1.4 * dens) * (0.6 + 0.8 * ep) * min(rate, 2.0),
                     0.25 * self.mass / (SIM_W * SIM_H))
        for i in range(n):
            if self.burst is not None:
                bx, by, br, bs = self.burst
                AP[9:14] = (1.0, bx, by, br, bs)
                self.burst = None
            else:
                AP[9] = 0.0
            self.AP.set(AP)
            launch_2d(self.k_agents, N_ROW, N_AGENTS // N_ROW,
                      (self.X, self.Y, self.A, self.T, np.int32(SIM_W), np.int32(SIM_H), self.AP,
                       np.uint32(self.tick & 0xFFFFFFFF), np.uint32(self.seed)))
            launch_2d(self.k_diffuse, SIM_W, SIM_H, (self.T, self.T2, np.int32(SIM_W), np.int32(SIM_H),
                                                      np.float32(dw), np.float32(decay)))
            self.T, self.T2 = self.T2, self.T
            self.mass = (self.mass + dep * nact) * decay
            self.tick += 1

        # ---- bloom map (quarter-res trail, blurred)
        bw, bh = SIM_W // 4, SIM_H // 4
        launch_2d(self.k_down, bw, bh, (self.T, self.B, np.int32(SIM_W), np.int32(SIM_H)))
        launch_2d(self.k_blur, bw, bh, (self.B, self.B2, np.int32(bw), np.int32(bh), np.int32(1)))
        launch_2d(self.k_blur, bw, bh, (self.B2, self.B, np.int32(bw), np.int32(bh), np.int32(0)))

        # ---- colour: exposure follows the mean trail (lightly smoothed) so every density / decay setting reads
        # well and deposit kicks only redistribute brightness to the fresh paths; density lifts the exposure
        mean = max(self.mass / (SIM_W * SIM_H), 1e-9)
        a_m = 1.0 - math.exp(-dt / 0.1) if dt > 0 else 1.0
        self.mass_s = mean if self.mass_s is None else self.mass_s + (mean - self.mass_s) * a_m
        expo = (0.22 + 0.2 * dens) / self.mass_s
        # wrapped at 10 (a whole number of periods of every palette's frequencies), so fast-math cos stays exact
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * spd * dt, 10.0)
        flash = min(0.35, 0.35 * k)
        gain = float(p["intensity"]) * (0.8 + 0.25 * ep)
        glow = float(p["glow"])
        CP = self.CPh
        CP[0:12] = palette_coeffs(p["palette"])
        CP[12:24] = (float(p["palette_period"]), self.drift, expo, glow ** -0.8, m, gain, 0.12 + 0.35 * ep, 0.6,
                     SIM_W / self.w, 1.0 / self.mass_s, 0.15 + 0.45 * (glow - 0.4) / 1.1, flash)
        self.CP.set(CP)
        launch_2d(self.k_colour, self.w, self.h, (self.T, self.B, self.buf, np.int32(SIM_W), np.int32(SIM_H),
                                                  np.int32(self.w), np.int32(self.h), self.CP))
        present(self.ctx, self.buf, out)
