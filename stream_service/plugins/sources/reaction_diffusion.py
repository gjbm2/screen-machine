"""Reaction-diffusion: Gray-Scott chemistry growing coral, fingerprints, spots and dividing cells, kept from ever
settling by a slow swirl and zoom of the medium, rendered as glossy embossed relief through the palette.

Source: Original (inspired by https://www.karlsims.com/rd.html and https://github.com/pmneila/jsexp)
Author: smfx (algorithm: P. Gray & S. K. Scott, the Gray-Scott model; parameter map after K. Sims' tutorial and
        P. M. Neila's jsexp)
License: MIT
license_class: permissive

Written from scratch from the published Gray-Scott equations; no code was copied.  References consulted only:
  * Karl Sims, "Reaction-Diffusion Tutorial", https://www.karlsims.com/rd.html ("(c) 2013-2016, Karl Sims, All
    rights reserved"): D_A = 1.0, D_B = 0.5, dt = 1, the 3x3 Laplacian (centre -1, sides 0.2, diagonals 0.05) and
    the named (feed, kill) pairs coral (0.0545, 0.062) and mitosis (0.0367, 0.0649).  Equations and constants
    are scientific facts, not code.
  * P. M. Neila, jsexp, https://github.com/pmneila/jsexp ("BSD-3-Clause license" per the repository sidebar):
    its named (feed, kill) presets (solitons, mazes, fingerprints) were consulted for the pattern path.

How it works
  * State: a float2 (A, B) grid, 960 cells wide at the output aspect, periodic, ping-ponged.  Each frame:
      1. advect + inject: the grid is resampled along a slow vortex (swirl) plus radial (zoom) flow that fades to
         zero at the grid edges, and seeds of B (beat rings/blobs, a faint random trickle) are relaxed in;
      2. a fractional number of explicit Gray-Scott substeps (9-point isotropic Laplacian):
             A += dt (D_A lap A - A B^2 + f (1 - A))
             B += dt (D_B lap B + A B^2 - (k + f) B)
      3. shade at render_scale: bilinear B -> height, 4-tap normal, rotating key light (diffuse + gloss),
         palette index = mix(height, gradient magnitude, colour_by) * period + drift; then present().
  * pattern walks continuously through curated (f, k) pairs: spots -> mitosis -> fingerprints -> maze -> coral.
  * pattern_scale sets the diffusion length (grid dx); above 1 half of it is a gentle view magnification so the
    explicit scheme stays stable and the chemistry does not crawl.

Macros
  * energy (e' = smoothstep(0.1, 0.85, e)): reaction rate x(0.5 + 1.0 e'); swirl/zoom amplitude x(0.4 + 1.2 e');
    emboss depth and light-azimuth rate; trickle density and a +k nudge toward livelier regimes (both e'^1.5,
    slewed over ~2 s).
  * pulse (k = pulse (0.3 + 0.7 e)): the rising edge drops seeds of B (rings or blobs, radius ~k) that bloom over
    seconds, plus a reaction-rate surge (bloom_kick); a relief-light flash (flash_kick): gloss x(1 + 0.8 k) and the
    crests brighten up to +35% (crests only, so the HEVC stream never sees a full-frame luma swing).
  * speed scales the reaction rate, the flows and the light; palette index = B-height * period + drift.
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

GRID_W = 640            # simulation cells across (height follows the output aspect); 6 px per cell at 4K
MAX_SEEDS = 24
SEED_STRIDE = 6         # x, y, radius, width, amount (per-frame relax 0..1), ring flag
DA, DB = 1.0, 0.5       # Sims' diffusion rates (with the 0.2/0.05/-1 kernel)
BASE_RATE = 540.0       # substeps per second at e' = 0.5, speed 1 (9 per frame at 60 fps)
CAP_PER_60 = 16         # substep cap per 1/60 s of frame
FLOW_KAPPA = 0.7        # how tightly the swirl/zoom is centred (periodic bump sharpness)
STEPS_PER_LAUNCH = 4    # = RH, the shared-memory halo of rd_multi
WARMUP = 600            # substeps run in setup so the first frame is already organised

# pattern path, ordered by connectivity (dots -> dividing dots -> growing worms -> stripes -> labyrinth):
# (position, name, feed, kill, energy k-nudge, regime-map k depth, regime-map f depth).  Kill values were scanned
# so base +- map depth (+ nudge) stays patterned: higher k dies out (spots/mitosis), lower k turns to a uniform
# reacted haze (coral side).
ANCHORS = (
    (0.00, "spots", 0.0300, 0.0612, 0.0005, 0.0010, 0.0),
    (0.25, "mitosis", 0.0367, 0.0640, 0.0003, 0.0008, 0.0),
    (0.50, "coral", 0.0545, 0.0635, 0.0008, 0.0018, 0.0),
    (0.75, "fingerprints", 0.0370, 0.0612, 0.0008, 0.0015, 0.0),
    (1.00, "maze", 0.0260, 0.0555, 0.0008, 0.0012, 0.0),
)

# P layout (floats)
#  advect  0 cx 1 cy 2 swirl angle/frame 3 - 4 zoom/frame 5 flow kappa 6 n seeds 7 memory rate
#          8 map scale 9..11 map phases
#  shade  16..27 palette 28 period 29 drift 30 mag 31 relief(normal k) 32 colour_by 33 light az 34 spec gain
#         35 gain 36 tap offset (cells) 37 relief mix 38 vignette 39 growth palette shift 40 growth glow
#         41 regime palette shift 42 beat crest flash
#  seeds  64 + 6 i
_SRC = HELPERS + PALETTE_CUDA + r"""
// RH explicit Gray-Scott steps per launch on a shared-memory tile (TX x TY interior + RH halo), periodic, 9-point
// isotropic Laplacian (sides 0.2, diagonals 0.05, centre -1):
//     A += dt (D_A lap A - A B^2 + f (1 - A)),   B += dt (D_B lap B + A B^2 - (k + f) B)
// with (f, k) offset per cell by the drifting regime map km (-1..1) times (df, dk).
#define TX 32
#define TY 16
#define RH 4
#define LX (TX + 2 * RH)
#define LY (TY + 2 * RH)
extern "C" __global__ void __launch_bounds__(256)
rd_multi(const float2* __restrict__ src, float2* __restrict__ dst, const float* __restrict__ km, const int W,
         const int H, const float da, const float db, const float f0, const float k0, const float df,
         const float dk, const float dt, const int nsteps)
{
    __shared__ float2 s0[LY * LX];
    __shared__ float2 s1[LY * LX];
    __shared__ float sf[LY * LX];
    __shared__ float sk[LY * LX];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x, nth = blockDim.x * blockDim.y;
    const int ox = blockIdx.x * TX - RH, oy = blockIdx.y * TY - RH;
    for (int i = tid; i < LX * LY; i += nth) {
        int gx = ox + i % LX, gy = oy + i / LX;
        gx = gx < 0 ? gx + W : (gx >= W ? gx - W : gx);
        gy = gy < 0 ? gy + H : (gy >= H ? gy - H : gy);
        s0[i] = src[gy * W + gx];
        const float m = km[gy * W + gx];
        sf[i] = f0 + m * df;
        sk[i] = k0 + m * dk;
    }
    __syncthreads();
    float2* a = s0;
    float2* b = s1;
    const int n = min(nsteps, RH);
    for (int st = 0; st < n; ++st) {
        const int lo = st + 1;
        for (int i = tid; i < LX * LY; i += nth) {
            const int lx = i % LX, ly = i / LX;
            const float2 c = a[i];
            if (lx < lo || ly < lo || lx >= LX - lo || ly >= LY - lo) { b[i] = c; continue; }
            const float2 nn = a[i - LX], ss = a[i + LX], ee = a[i + 1], ww = a[i - 1];
            const float2 ne = a[i - LX + 1], nw = a[i - LX - 1], se = a[i + LX + 1], sw = a[i + LX - 1];
            const float la = 0.2f * (nn.x + ss.x + ee.x + ww.x) + 0.05f * (ne.x + nw.x + se.x + sw.x) - c.x;
            const float lb = 0.2f * (nn.y + ss.y + ee.y + ww.y) + 0.05f * (ne.y + nw.y + se.y + sw.y) - c.y;
            const float f = sf[i], k = sk[i];
            const float abb = c.x * c.y * c.y;
            b[i] = make_float2(clamp01(c.x + dt * (da * la - abb + f * (1.f - c.x))),
                               clamp01(c.y + dt * (db * lb + abb - (k + f) * c.y)));
        }
        __syncthreads();
        float2* t = a; a = b; b = t;
    }
    for (int i = tid; i < TX * TY; i += nth) {
        const int ix = i % TX, iy = i / TX;
        const int gx = blockIdx.x * TX + ix, gy = blockIdx.y * TY + iy;
        if (gx < W && gy < H) dst[gy * W + gx] = a[(iy + RH) * LX + ix + RH];
    }
}

// periodic flow on the torus: a vortex (swirl) + source (zoom) centred on screen, weighted by a periodic bump
// G = exp(kappa (cos tx + cos ty - 2)); near the centre it is a rigid rotation by P[2] and a zoom by P[4] per
// frame, and it stays smooth across the wrap so the grid has no seams.
__device__ __forceinline__ float2 flow_disp(float px, float py, int W, int H, const float* __restrict__ P) {
    const float tx = 6.2831853f * (px - P[0]) / W, ty = 6.2831853f * (py - P[1]) / H;
    float sx, cx, sy, cy;
    __sincosf(tx, &sx, &cx); __sincosf(ty, &sy, &cy);
    const float G = __expf(P[5] * (cx + cy - 2.f));
    const float ux = sx * W * 0.15915494f, uy = sy * H * 0.15915494f;
    return make_float2(G * (-P[2] * uy + P[4] * ux), G * (P[2] * ux + P[4] * uy));
}
__device__ __forceinline__ int wrapi(int i, int n) { i %= n; return i < 0 ? i + n : i; }
__device__ float2 gbilw(const float2* __restrict__ g, float fx, float fy, int W, int H) {
    fx -= 0.5f; fy -= 0.5f;
    const float flx = floorf(fx), fly = floorf(fy);
    const float tx = fx - flx, ty = fy - fly;
    const int x0 = wrapi((int)flx, W), y0 = wrapi((int)fly, H);
    const int x1 = x0 + 1 == W ? 0 : x0 + 1, y1 = (y0 + 1 == H ? 0 : y0 + 1) * W, ya = y0 * W;
    const float2 a = g[ya + x0], b = g[ya + x1], c = g[y1 + x0], d = g[y1 + x1];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float2(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y));
}
__device__ float mbilw(const float* __restrict__ g, float fx, float fy, int W, int H) {
    fx -= 0.5f; fy -= 0.5f;
    const float flx = floorf(fx), fly = floorf(fy);
    const float tx = fx - flx, ty = fy - fly;
    const int x0 = wrapi((int)flx, W), y0 = wrapi((int)fly, H);
    const int x1 = x0 + 1 == W ? 0 : x0 + 1, y1 = (y0 + 1 == H ? 0 : y0 + 1) * W, ya = y0 * W;
    return (1.f - ty) * ((1.f - tx) * g[ya + x0] + tx * g[ya + x1]) + ty * ((1.f - tx) * g[y1 + x0] + tx * g[y1 + x1]);
}

// semi-Lagrangian advection (midpoint backtrace, periodic), seed injection, slow memory of B, regime map
extern "C" __global__
void rd_advect(const float2* __restrict__ src, float2* __restrict__ dst, const float* __restrict__ msrc,
               float* __restrict__ mdst, float* __restrict__ km, const int W, const int H,
               const float* __restrict__ P, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = x + 0.5f, py = y + 0.5f;
    const bool still = P[2] == 0.f && P[4] == 0.f;
    float2 g; float m0;
    if (still) { g = src[y * W + x]; m0 = msrc[y * W + x]; }
    else {
        const float2 d1 = flow_disp(px, py, W, H, P);
        const float2 d2 = flow_disp(px - 0.5f * d1.x, py - 0.5f * d1.y, W, H, P);
        g = gbilw(src, px - d2.x, py - d2.y, W, H);
        m0 = mbilw(msrc, px - d2.x, py - d2.y, W, H);
    }
    const int ns = (int)P[6];
    for (int i = 0; i < ns; ++i) {
        const float* s = P + 64 + 6 * i;
        const float dx = px - s[0], dy = py - s[1], reach = s[2] + 3.f * s[3];
        if (fabsf(dx) > reach || fabsf(dy) > reach) continue;
        const float d = sqrtf(dx * dx + dy * dy);
        float m;
        if (s[5] > 0.5f) {                                     // ring; clears its inside so it blooms inward too
            const float q = (d - s[2]) / s[3];
            m = __expf(-q * q);
            const float cl = 0.5f * s[4] * (1.f - smooth(s[2] - 3.f * s[3], s[2] - 1.2f * s[3], d)) * (1.f - m);
            g.y -= cl * g.y;
            g.x += cl * (1.f - g.x);
        } else m = 1.f - smooth(s[2] * 0.45f, s[2], d);
        m *= s[4];
        g.y += m * (0.5f - g.y);
        g.x += m * (0.25f - g.x);
    }
    dst[y * W + x] = g;
    mdst[y * W + x] = m0 + (g.y - m0) * P[7];                  // slow memory of B: growth reads as B - M > 0
    // regime map: two drifting value-noise layers, a few blobs across the screen
    const float u = px * P[8], v = py * P[8];
    const float n = 0.65f * vnoise(u + P[9], v + P[10], seed) + 0.35f * vnoise(2.03f * u - P[11], 2.03f * v + 0.6f * P[11], seed + 7u);
    km[y * W + x] = fminf(fmaxf((n - 0.5f) * 3.f, -1.f), 1.f);
}

__device__ __forceinline__ float hfield(float b) { return smooth(0.03f, 0.34f, b); }

extern "C" __global__
void rd_shade(const float2* __restrict__ G, const float* __restrict__ M, const float* __restrict__ KM,
              uchar4* __restrict__ out, const int w, const int h, const int GW, const int GH,
              const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float sc = (float)GW / ((float)w * P[30]);
    const float gx = 0.5f * GW + (x + 0.5f - 0.5f * w) * sc, gy = 0.5f * GH + (y + 0.5f - 0.5f * h) * sc;
    const float o = P[36];
    const float bc = gbilw(G, gx, gy, GW, GH).y;
    const float hc = hfield(bc);
    const float fresh = clamp01((bc - mbilw(M, gx, gy, GW, GH)) * 5.f);     // new growth (B above its memory)
    const float regime = mbilw(KM, gx, gy, GW, GH);                         // drifting local chemistry, -1..1
    const float hx1 = hfield(gbilw(G, gx + o, gy, GW, GH).y), hx0 = hfield(gbilw(G, gx - o, gy, GW, GH).y);
    const float hy1 = hfield(gbilw(G, gx, gy + o, GW, GH).y), hy0 = hfield(gbilw(G, gx, gy - o, GW, GH).y);
    const float dhx = (hx1 - hx0) / (2.f * o), dhy = (hy1 - hy0) / (2.f * o);   // per cell
    const float gm = sqrtf(dhx * dhx + dhy * dhy);
    // palette index from height and/or gradient magnitude
    const float gn = clamp01(gm * 3.2f);
    const float idx = (hc + (gn - hc) * P[32]) * P[28] + P[29] + P[39] * fresh + P[41] * regime;
    float3 c = pal(idx, P + 16);
    // embossed relief: normal from the height gradient, rotating key light, gloss
    const float K = P[31];
    float nx = -dhx * K, ny = -dhy * K, nz = 1.f;
    const float il = rsqrtf(nx * nx + ny * ny + nz * nz + 1e-8f);
    nx *= il; ny *= il; nz *= il;
    const float az = P[33];
    const float lx = 0.72f * cosf(az), ly = 0.72f * sinf(az), lz = 0.694f;
    const float diff = fmaxf(nx * lx + ny * ly + nz * lz, 0.f) * (1.f / 0.694f);
    float hx = lx, hy = ly, hz = lz + 1.f;
    const float ih = rsqrtf(hx * hx + hy * hy + hz * hz);
    hx *= ih; hy *= ih; hz *= ih;
    const float nh = fmaxf(nx * hx + ny * hy + nz * hz, 0.f);
    float sp = nh * nh; sp *= sp; sp *= sp; sp *= sp; sp *= sp;              // ^32
    sp *= 1.f - nz;                                                        // only on slopes (no flat glare)
    sp = fminf(sp * 6.f, 1.f);
    const float rm = P[37];
    const float depth = 1.f - rm * 0.45f * (1.f - hc);                     // valleys sit lower and darker
    const float shade = (1.f - rm + rm * (0.28f + 0.8f * diff)) * depth * (1.f + P[40] * fresh)
                        * (1.f + P[42] * hc * hc);                  // beat flash on the crests only
    const float spec = sp * P[34] * rm;
    float3 o3;
    o3.x = c.x * shade + spec * (0.75f + 0.25f * c.x);
    o3.y = c.y * shade + spec * (0.75f + 0.25f * c.y);
    o3.z = c.z * shade + spec * (0.75f + 0.25f * c.z);
    if (P[38] > 0.f) {
        const float ex = (x + 0.5f) / w - 0.5f, ey = (y + 0.5f) / h - 0.5f;
        const float v = 1.f - P[38] * (ex * ex + ey * ey) * 1.6f;
        o3.x *= v; o3.y *= v; o3.z *= v;
    }
    const float g = 255.f * P[35];
    out[y * w + x] = pack(make_float4(o3.x * g, o3.y * g, o3.z * g, 255.f));
}
"""


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _wrap(x: float, period: float = 2.0 * math.pi) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def pattern_fk(pos: float) -> tuple[float, ...]:
    """(feed, kill, energy k-nudge, map k depth, map f depth) along the curated path; piecewise linear."""
    pos = min(max(pos, 0.0), 1.0)
    for a, b in zip(ANCHORS, ANCHORS[1:]):
        if pos <= b[0]:
            t = (pos - a[0]) / (b[0] - a[0])
            return tuple(x + (y - x) * t for x, y in zip(a[2:], b[2:]))
    return tuple(ANCHORS[-1][2:])


def scale_terms(s: float) -> tuple[float, float]:
    """pattern_scale -> (diffusion multiplier, view magnification).  Pattern size on screen ~ sqrt(dmul) * mag = s."""
    if s <= 1.0:
        return s * s, 1.0
    return s, math.sqrt(s)


class ReactionDiffusion(Source):
    name = "reaction_diffusion"
    label = "Reaction-diffusion"
    description = ("Gray-Scott chemistry grows coral, fingerprints, spots and dividing cells under a slow swirl, "
                   "shown as glossy embossed relief.")
    params = {
        "pattern": num(0.0, 1.0, 0.5, "Pattern (spots > mitosis > coral > fingerprints > maze)", basic=True),
        "pattern_scale": num(0.5, 2.0, 1.0, "Pattern scale", step=0.05, basic=True),
        "flow_swirl": num(-0.5, 0.5, 0.2, "Swirl", unit="rad/s"),
        "flow_zoom": num(-0.1, 0.1, 0.04, "Zoom flow (+ in, - out)", step=0.005, unit="/s"),
        "relief": num(0.0, 1.0, 0.75, "Relief (emboss)"),
        "colour_by": num(0.0, 1.0, 0.2, "Colour by (concentration > edges)"),
        "bloom_kick": num(0.0, 2.0, 1.0, "Beat bloom (seeds + growth surge)"),
        "flash_kick": num(0.0, 2.0, 1.0, "Beat light flash"),
        **standard_scene_params(palette="dusk", render_scale=0.5, period=0.9, drift=0.02),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.GW = GRID_W
        self.GH = max(16, int(round(GRID_W * ctx.height / ctx.width / 2)) * 2)
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        mod = raw_module(_SRC)
        self.k_step = mod.get_function("rd_multi")
        self.k_adv = mod.get_function("rd_advect")
        self.k_shade = mod.get_function("rd_shade")
        seed = int(params["seed"])
        self.rng = np.random.default_rng(seed * 7919 + 13)
        self.g0 = cp.asarray(self._initial_state(params))
        self.g1 = cp.empty_like(self.g0)
        self.km = cp.zeros((self.GH, self.GW), cp.float32)          # regime map (-1..1), written by rd_advect
        self.seed = np.uint32(seed % (2**32))
        self.map_ph = [float(v) for v in self.rng.uniform(0, 50, 3)]
        self.P = cp.zeros(64 + SEED_STRIDE * MAX_SEEDS, cp.float32)
        self.Ph = np.zeros(self.P.size, np.float32)
        self.acc = 0.0
        self.clock = 0.0
        self.az = float(self.rng.uniform(0, 2 * math.pi))
        self.ph_swirl = float(self.rng.uniform(0, 2 * math.pi))
        self.ph_zoom = float(self.rng.uniform(0, 2 * math.pi))
        self.drift = 0.0
        self.e_slow = None
        self.armed = True
        self.last_fire = -1.0
        self.seeds: list[dict] = []
        # warm-up so the first frame already shows organised chemistry (~15 ms of GPU work)
        f, k = pattern_fk(float(params["pattern"]))[:2]
        dmul, _ = scale_terms(float(params["pattern_scale"]))
        dts = min(1.0, 1.1 / (DA * dmul))
        self._substep(dmul, dts, f, k, 0.0, 0.0, WARMUP)
        self.m0 = cp.ascontiguousarray(self.g0[..., 1])          # slow memory of B (growth tint)
        self.m1 = cp.empty_like(self.m0)

    def _initial_state(self, params) -> np.ndarray:
        """A = 1, B = 0 with rounded random B blobs at roughly the pattern's own spacing (numpy, setup only)."""
        GW, GH = self.GW, self.GH
        dmul, _ = scale_terms(float(params["pattern_scale"]))
        cell = max(4.0, 9.0 * math.sqrt(dmul))
        cw, ch = int(GW / cell) + 3, int(GH / cell) + 3
        coarse = self.rng.random((ch, cw)).astype(np.float32)
        # bilinear upsample of the coarse noise, then threshold -> rounded irregular blobs
        xs = (np.arange(GW, dtype=np.float32) + 0.5) / cell + 0.5
        ys = (np.arange(GH, dtype=np.float32) + 0.5) / cell + 0.5
        x0, y0 = xs.astype(np.int32), ys.astype(np.int32)
        tx, ty = (xs - x0)[None, :], (ys - y0)[:, None]
        tx, ty = tx * tx * (3 - 2 * tx), ty * ty * (3 - 2 * ty)
        c00, c10 = coarse[y0][:, x0], coarse[y0][:, x0 + 1]
        c01, c11 = coarse[y0 + 1][:, x0], coarse[y0 + 1][:, x0 + 1]
        n = (1 - ty) * ((1 - tx) * c00 + tx * c10) + ty * ((1 - tx) * c01 + tx * c11)
        mask = np.clip((n - 0.62) * 12.0, 0.0, 1.0)
        g = np.zeros((GH, GW, 2), np.float32)
        g[..., 0] = 1.0 - 0.5 * mask
        g[..., 1] = 0.25 * mask
        return g

    def _substep(self, dmul, dts, f, k, df, dk, n=1) -> None:
        """Run n Gray-Scott steps, at most STEPS_PER_LAUNCH per (shared-memory tiled) launch."""
        grid = ((self.GW + 31) // 32, (self.GH + 15) // 16, 1)
        args = (np.int32(self.GW), np.int32(self.GH), np.float32(DA * dmul), np.float32(DB * dmul),
                np.float32(f), np.float32(k), np.float32(df), np.float32(dk), np.float32(dts))
        while n > 0:
            m = min(n, STEPS_PER_LAUNCH)
            self.k_step(grid, (32, 8, 1), (self.g0, self.g1, self.km, *args, np.int32(m)))
            self.g0, self.g1 = self.g1, self.g0
            n -= m

    def _spawn(self, x, y, radius, width, amount, life, ring) -> None:
        if len(self.seeds) >= MAX_SEEDS:
            self.seeds.pop(0)
        self.seeds.append({"x": x, "y": y, "r": radius, "w": width, "a": amount, "life": life, "age": 0.0,
                           "ring": 1.0 if ring else 0.0})

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        e = min(max(p["energy"], 0.0), 1.0)
        ep = _smoothstep(0.1, 0.85, e)
        pulse = min(max(p["pulse"], 0.0), 1.0)
        kp = pulse * (0.3 + 0.7 * e)
        speed = max(p["speed"], 0.0)
        self.clock += dt
        # slewed density/complexity target (~2 s)
        tgt = ep ** 1.5
        if self.e_slow is None:
            self.e_slow = tgt
        elif dt > 0:
            self.e_slow += (tgt - self.e_slow) * (1.0 - math.exp(-dt / 2.0))

        # chemistry
        f, k, dk, map_dk, map_df = pattern_fk(p["pattern"])
        k += dk * self.e_slow
        mdepth = 0.75 + 0.25 * self.e_slow              # regime-map depth (mostly constant: k limits)
        s = p["pattern_scale"]
        dmul, mag = scale_terms(s)
        lam = math.sqrt(dmul)                       # pattern length factor in cells
        dts = min(1.0, 1.1 / (DA * dmul))
        rate = BASE_RATE * (0.5 + 1.0 * ep) * (1.0 + 0.8 * p["bloom_kick"] * kp) * speed / math.sqrt(dts)
        cap = int(CAP_PER_60 * max(1.0, 60.0 / max(self.ctx.fps, 1)))
        self.acc += rate * dt
        nsub = min(int(self.acc), cap)
        self.acc = min(self.acc - nsub, 1.0)

        # beat seeds: rising edge with hysteresis (fire > 0.6 after < 0.3, >= 150 ms apart)
        if pulse < 0.3:
            self.armed = True
        if self.armed and pulse > 0.6 and self.clock - self.last_fire >= 0.15:
            self.armed = False
            self.last_fire = self.clock
            if p["bloom_kick"] > 0.0:
                n = 1 + int(self.rng.random() < 0.35 * ep)
                for _ in range(n):
                    ring = self.rng.random() < 0.55
                    rad = (8.0 + 22.0 * kp) * lam * (1.0 if ring else 0.6)
                    self._spawn(self.rng.uniform(0.1, 0.9) * self.GW, self.rng.uniform(0.12, 0.88) * self.GH,
                                rad, 1.6 * lam, min(0.35 * p["bloom_kick"], 0.6), 0.3, ring)
        # faint random trickle keeps dead regions re-nucleating
        if dt > 0 and speed > 0:
            lam_rate = (0.6 + 2.4 * self.e_slow) * speed
            for _ in range(int(self.rng.poisson(lam_rate * dt))):
                self._spawn(self.rng.uniform(0.04, 0.96) * self.GW, self.rng.uniform(0.04, 0.96) * self.GH,
                            3.0 * lam, 1.0, 0.25, 0.15, False)

        Ph = self.Ph
        Ph[:] = 0.0
        live = []
        fr = dt * 60.0
        for sd in self.seeds:
            if sd["age"] >= sd["life"]:
                continue
            u = sd["age"] / sd["life"]
            env = math.sin(math.pi * min(u, 1.0)) ** 2 if dt > 0 else 0.0
            amt = 1.0 - (1.0 - sd["a"] * env) ** fr if fr > 0 else 0.0
            j = 64 + SEED_STRIDE * len(live)
            Ph[j:j + 6] = (sd["x"], sd["y"], sd["r"], sd["w"], amt, sd["ring"])
            sd["age"] += dt
            live.append(sd)
        self.seeds = live

        # flow: swirl angle and zoom per frame (rates x energy, integrated by the advection itself)
        amp = 0.4 + 1.2 * ep
        self.ph_swirl = _wrap(self.ph_swirl + 2 * math.pi / 37.0 * speed * dt)
        self.ph_zoom = _wrap(self.ph_zoom + 2 * math.pi / 23.0 * speed * dt)
        swirl = p["flow_swirl"] * amp * (0.75 + 0.25 * math.sin(self.ph_swirl)) * speed * dt
        zoom = p["flow_zoom"] * amp * (0.35 + 0.65 * math.sin(self.ph_zoom)) * speed * dt
        Ph[0:8] = (0.5 * self.GW, 0.5 * self.GH, swirl, 0.0, zoom, FLOW_KAPPA, len(live),
                   1.0 - math.exp(-dt / 1.2) if dt > 0 else 0.0)
        # regime map drifts (two layers, rates x energy); wrapped far beyond any visible repeat
        mrate = (0.4 + 1.2 * ep) * speed * dt
        mp = self.map_ph
        mp[0] = _wrap(mp[0] + 0.045 * mrate, 4096.0)
        mp[1] = _wrap(mp[1] + 0.028 * mrate, 4096.0)
        mp[2] = _wrap(mp[2] + 0.07 * mrate, 4096.0)
        Ph[8:12] = (2.6 / self.GH, mp[0], mp[1], mp[2])

        # shading
        self.drift = _wrap(self.drift + p["drift_speed"] * speed * dt, 1.0)
        self.az = _wrap(self.az + 0.22 * (0.4 + 1.2 * ep) * speed * dt)
        rel = p["relief"]
        fk = p["flash_kick"] * kp
        Ph[16:28] = palette_coeffs(p["palette"])
        Ph[28] = p["palette_period"]
        Ph[29] = self.drift
        Ph[30] = mag
        Ph[31] = 2.2 * (0.75 + 0.5 * _smoothstep(0.0, 1.0, ep)) * (1.0 + 0.25 * fk) / lam
        Ph[32] = p["colour_by"]
        Ph[33] = self.az
        Ph[34] = 0.55 * (1.0 + 0.8 * fk) * (0.8 + 0.4 * ep)
        Ph[35] = p["intensity"]
        Ph[36] = max(1.0, self.GW / (self.w * mag))
        Ph[37] = rel
        Ph[38] = 0.3
        Ph[39] = 0.22                                   # palette shift on fresh growth
        Ph[40] = 0.25                                   # growth glow
        Ph[41] = 0.12                                   # palette shift with the regime map (large-scale colour)
        Ph[42] = 0.22 * min(fk, 1.6)                    # crest flash (<= +35%, crests only: HEVC-safe)
        self.P.set(Ph)

        launch_2d(self.k_adv, self.GW, self.GH, (self.g0, self.g1, self.m0, self.m1, self.km, np.int32(self.GW),
                                                 np.int32(self.GH), self.P, self.seed))
        self.g0, self.g1 = self.g1, self.g0
        self.m0, self.m1 = self.m1, self.m0
        self._substep(dmul, dts, f, k, map_df * mdepth, map_dk * mdepth, nsub)
        launch_2d(self.k_shade, self.w, self.h,
                  (self.g0, self.m0, self.km, self.buf, np.int32(self.w), np.int32(self.h), np.int32(self.GW), np.int32(self.GH),
                   self.P))
        present(self.ctx, self.buf, out)
