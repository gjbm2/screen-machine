"""Wire terrain: a wireframe terrain flyover morphing from Unknown Pleasures ridgelines to an 80s vector grid.

Source: Original (inspired by https://en.wikipedia.org/wiki/Unknown_Pleasures and https://github.com/mrdoob/three.js/blob/dev/examples/webgl_points_waves.html)
Author: smfx (original; the CP 1919 pulsar plot is copyright-free; three.js points_waves, MIT, idea only)
License: MIT (original)
license_class: permissive

Written from scratch on the splat3d toolkit:
  * Terrain rows: up to 204 rows x 512 vertices, regenerated every frame by one kernel.  Rows are anchored to the
    world (uniform spacing dz) and roll towards the camera as the flight integrates; the row phase wraps with a fade
    at both ends, so nothing ever pops.  Each row spans the view frustum at its depth.  Heights are a periodic
    value-noise fbm (lattice period 4096 along the flight, so the host-wrapped travel never jumps) shaped two ways:
      - ridgelines (style 0): a screen-centred band of sharp positive peaks over a faintly jittered baseline, seen
        through a long lens from above, so the rows stack like the CP 1919 pulsar plot;
      - grid (style 1): a flat valley along the flight path between ridged mountains, seen from a low wide camera,
        with world-anchored longitudinal lines (the cross-line opacity morph) and a striped retro sun.
    Plus ripple rows born at the far end that roll towards the camera, and beat bump waves.
  * Hidden lines (floating horizon): every row segment is rasterised per screen column into a per-row min-y
    buffer, a per-column scan (near -> far) turns it into the horizon in front of each row, and every vertex gets a
    soft visibility weight against that horizon.  The final horizon is the terrain silhouette: the background
    kernel paints sky, horizon haze and the sun above it and a dark fill below it, so mountains occlude the sun.
  * splat3d polylines (weight in w, palette index by depth + height), bloom + wide halo, ACES, resolved straight
    over the painted background at 4K.

Macros: energy -> flight speed, height amplitude, ripple amplitude, line brightness, glow and camera sway.
pulse -> spawns a bump wave at the far end that travels towards the camera and decays (rising edge), plus a
small brightness / glow / amplitude kick.  speed scales flight, ripple and bump travel; drift moves the line
palette along depth; intensity is exposure.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.splat3d import Splat3D, look_at
from stream_service.plugins._lib.upsample import internal_size

R_MAX = 204          # rows (line_density 1 -> 200 rows + wrap row)
C = 512              # vertices per row
NL = 320             # longitudinal (grid) lines
NB = 4               # bump slots
NG = 128             # param floats
BIGK = 0x7F000000    # "no horizon" threshold in the int horizon buffer

# G layout
(G_HC, G_Z0, G_DZ, G_FRAC, G_REFF, G_TANHW, G_COSP, G_SINP, G_XMARG, G_FR, G_TL, G_XOFF, G_S, G_AR, G_AG, G_GAIN,
 G_SHARP, G_VALLEY, G_RIPA, G_RIPPH, G_RIPLAM, G_BUMPW, G_ZF, G_GX, G_K0, G_FOG, G_BUMPGLOW, G_AMPREF) = range(28)
G_VIEW, G_FOCAL, G_CX, G_CY, G_NEAR = 32, 44, 45, 46, 47
G_BUMPS = 48
G_PAL, G_DRIFT, G_RIGHT, G_UP, G_FWD = 64, 76, 77, 80, 83
G_SUNEL, G_SUNR, G_SUN, G_SKY, G_STRIPE, G_BGGAIN = 86, 87, 88, 89, 90, 91

_SRC = HELPERS + PALETTE_CUDA + r"""
#define G_HC %(G_HC)d
#define G_Z0 %(G_Z0)d
#define G_DZ %(G_DZ)d
#define G_FRAC %(G_FRAC)d
#define G_REFF %(G_REFF)d
#define G_TANHW %(G_TANHW)d
#define G_COSP %(G_COSP)d
#define G_SINP %(G_SINP)d
#define G_XMARG %(G_XMARG)d
#define G_FR %(G_FR)d
#define G_TL %(G_TL)d
#define G_XOFF %(G_XOFF)d
#define G_S %(G_S)d
#define G_AR %(G_AR)d
#define G_AG %(G_AG)d
#define G_GAIN %(G_GAIN)d
#define G_SHARP %(G_SHARP)d
#define G_VALLEY %(G_VALLEY)d
#define G_RIPA %(G_RIPA)d
#define G_RIPPH %(G_RIPPH)d
#define G_RIPLAM %(G_RIPLAM)d
#define G_BUMPW %(G_BUMPW)d
#define G_ZF %(G_ZF)d
#define G_GX %(G_GX)d
#define G_K0 %(G_K0)d
#define G_FOG %(G_FOG)d
#define G_BUMPGLOW %(G_BUMPGLOW)d
#define G_AMPREF %(G_AMPREF)d
#define G_VIEW %(G_VIEW)d
#define G_FOCAL %(G_FOCAL)d
#define G_CX %(G_CX)d
#define G_CY %(G_CY)d
#define G_NEAR %(G_NEAR)d
#define G_BUMPS %(G_BUMPS)d
#define G_PAL %(G_PAL)d
#define G_DRIFT %(G_DRIFT)d
#define G_RIGHT %(G_RIGHT)d
#define G_UP %(G_UP)d
#define G_FWD %(G_FWD)d
#define G_SUNEL %(G_SUNEL)d
#define G_SUNR %(G_SUNR)d
#define G_SUN %(G_SUN)d
#define G_SKY %(G_SKY)d
#define G_STRIPE %(G_STRIPE)d
#define G_BGGAIN %(G_BGGAIN)d
#define NB %(NB)d
#define BIGK %(BIGK)d

// value noise, lattice periodic (4096) along z so the wrapped flight travel is seamless
__device__ __forceinline__ float wt_lat(int ix, int iz, unsigned int seed) {
    return hash1((unsigned int)ix, (unsigned int)iz & 4095u, seed);
}
__device__ float wt_noise(float x, float z, unsigned int seed) {
    const float fx = floorf(x), fz = floorf(z);
    const int ix = (int)fx, iz = (int)fz;
    float tx = x - fx, tz = z - fz;
    tx = tx * tx * tx * (tx * (tx * 6.f - 15.f) + 10.f);
    tz = tz * tz * tz * (tz * (tz * 6.f - 15.f) + 10.f);
    const float a = wt_lat(ix, iz, seed), b = wt_lat(ix + 1, iz, seed);
    const float c = wt_lat(ix, iz + 1, seed), d = wt_lat(ix + 1, iz + 1, seed);
    return (1.f - tz) * ((1.f - tx) * a + tx * b) + tz * ((1.f - tx) * c + tx * d);
}
// 5 octaves, lacunarity exactly 2 (keeps the period), contrast-stretched to ~0..1
__device__ float wt_fbm(float x, float z, unsigned int seed, float gain) {
    float s = 0.f, amp = 1.f, norm = 0.f;
    #pragma unroll
    for (int o = 0; o < 5; ++o) {
        s += amp * wt_noise(x, z, seed + 101u * o);
        norm += amp;
        x = x * 2.f + 17.31f; z = z * 2.f + 5.27f;
        amp *= gain;
    }
    return 0.5f + (s / norm - 0.5f) * 2.4f;          // NOT clamped (peaks keep rising)
}

// terrain height at terrain-frame (x, z); u = x / view half width; z01 = depth fraction.  b = bump sum.
__device__ float wt_height(float x, float z, float u, float z01, const float* __restrict__ g, unsigned int seed,
                           float& band_out, float& b_out) {
    const float fr = g[G_FR];
    const float xw = x + g[G_XOFF];
    const float xl = xw * fr, zl = g[G_TL] + z * fr;
    const float s = g[G_S], gain = g[G_GAIN];
    const float nu = wt_fbm(xl, zl, seed, gain);
    const float n = clamp01(nu);
    // ridgelines: screen-centred band of sharp peaks over a jittered baseline
    const float band = smooth(0.72f, 0.1f, fabsf(u));
    float pk = fmaxf(nu - 0.48f, 0.f) * 1.9f;
    pk = pk > 0.f ? __powf(pk, g[G_SHARP]) : 0.f;
    const float fine = wt_noise(xl * 3.f + 1.7f, zl * 3.f + 0.61f, seed + 313u);
    pk *= 0.55f + 0.9f * fine;                                       // spiky, uneven crests
    const float jit = wt_noise(xl * 9.f + 3.1f, zl * 4.f + 0.37f, seed + 909u) - 0.5f;
    const float hR = band * pk * 1.35f + 0.11f * jit * (0.3f + band);
    // grid: valley along the flight path between ridged mountains
    const float side = smooth(g[G_VALLEY], 3.f * g[G_VALLEY], fabsf(xw));
    const float m = clamp01(wt_fbm(xl + 41.7f, zl, seed + 7u, gain));
    const float ridge = 1.f - fabsf(2.f * n - 1.f);
    const float hG = side * (0.8f * smooth(0.25f, 0.95f, m) + 0.35f * ridge * ridge * (0.4f + m))
                     + 0.06f * (n - 0.5f);
    float h = g[G_AR] * (1.f - s) * hR + g[G_AG] * s * hG;
    // ripple rows: born at the far end, rolling towards the camera
    const float shape = (1.f - s) * (0.3f + 0.7f * band) + s;
    const float born = smooth(1.02f, 0.7f, z01) * smooth(-0.15f, 0.05f, z01);
    float ph = z01 / g[G_RIPLAM] + g[G_RIPPH];
    ph -= floorf(ph);
    h += g[G_RIPA] * born * shape * __sinf(6.2831853f * ph) * (0.7f + 0.3f * sinf(xl * 0.7f + 1.3f));
    // beat bump waves (travel far -> near, decaying)
    float b = 0.f;
    #pragma unroll
    for (int k = 0; k < NB; ++k) {
        const float amp = g[G_BUMPS + 2 * k + 1];
        if (amp > 1e-5f) {
            const float d = (z01 - g[G_BUMPS + 2 * k]) / g[G_BUMPW];
            b += amp * __expf(-d * d);
        }
    }
    h += b * shape;
    band_out = band;
    b_out = b * shape;
    return h;
}

// mode 0: rows (thread x = column j, y = row i; layout i * ncol + j)
// mode 1: longitudinal lines (thread x = line k, y = row i; layout k * nrow + i)
extern "C" __global__ void wt_gen(float4* __restrict__ V, float2* __restrict__ S, float* __restrict__ IDX,
                                  const float* __restrict__ g, const int ncol, const int nrow, const int mode,
                                  const unsigned int seed) {
    const int j = blockIdx.x * blockDim.x + threadIdx.x;
    const int i = blockIdx.y * blockDim.y + threadIdx.y;
    if (j >= ncol || i >= nrow) return;
    const float p = (float)i - g[G_FRAC];
    const float z = g[G_Z0] + p * g[G_DZ];
    const float dcam = fmaxf(z * g[G_COSP] + g[G_HC] * g[G_SINP], 0.05f);
    const float xh = dcam * g[G_TANHW];
    float x, wfade;
    if (mode == 0) {
        const float uu = ((float)j / (float)(ncol - 1)) * 2.f - 1.f;
        x = uu * (xh * g[G_XMARG] + 0.2f);
        wfade = 1.f;
    } else {
        x = (g[G_K0] + (float)j) * g[G_GX] - g[G_XOFF];
        wfade = clamp01(fminf((float)j, (float)(ncol - 1 - j)) * (1.f / 8.f));
    }
    const float u = x / xh;
    const float z01 = (z - g[G_Z0]) / fmaxf(g[G_ZF] - g[G_Z0], 1e-3f);
    float band, b;
    const float h = wt_height(x, z, u, z01, g, seed, band, b);
    const float zc = fmaxf(z01, 0.f);
    float w = clamp01(p + 1.f) * clamp01(g[G_REFF] - 1.f - p) * wfade;
    w *= __expf(-g[G_FOG] * zc * sqrtf(zc));
    w *= 1.f + g[G_BUMPGLOW] * b;
    const int o = mode == 0 ? i * ncol + j : j * nrow + i;
    V[o] = make_float4(x, h, z, w);
    IDX[o] = 0.8f * z01 + 0.3f * clamp01(h / fmaxf(g[G_AMPREF], 1e-4f));
    // projection (identical to splat3d's)
    const float* v = g + G_VIEW;
    const float vx = v[0] * x + v[1] * h + v[2] * z + v[3];
    const float vy = v[4] * x + v[5] * h + v[6] * z + v[7];
    const float vz = v[8] * x + v[9] * h + v[10] * z + v[11];
    if (vz > g[G_NEAR]) {
        const float iz = 1.f / vz;
        S[o] = make_float2(g[G_CX] + g[G_FOCAL] * vx * iz, g[G_CY] - g[G_FOCAL] * vy * iz);
    } else {
        S[o] = make_float2(-1e9f, 0.f);
    }
}

// rasterise each row segment into its per-row min-y buffer (thread x = segment j, y = row i)
extern "C" __global__ void wt_raster(const float2* __restrict__ S, int* __restrict__ HB, const int ncol,
                                     const int nrow, const int W) {
    const int j = blockIdx.x * blockDim.x + threadIdx.x;
    const int i = blockIdx.y * blockDim.y + threadIdx.y;
    if (j >= ncol - 1 || i >= nrow) return;
    float2 a = S[i * ncol + j], b = S[i * ncol + j + 1];
    if (a.x < -1e8f || b.x < -1e8f) return;
    if (a.x > b.x) { const float2 t = a; a = b; b = t; }
    const int x0 = max((int)ceilf(a.x - 0.5f), 0);
    int x1 = min((int)floorf(b.x - 0.5f), W - 1);
    if (x1 > x0 + 1024) x1 = x0 + 1024;
    const float inv = 1.f / fmaxf(b.x - a.x, 1e-6f);
    int* row = HB + (size_t)i * W;
    for (int X = x0; X <= x1; ++X) {
        const float t = ((float)X + 0.5f - a.x) * inv;
        const float y = a.y + t * (b.y - a.y);
        atomicMin(row + X, __float2int_rn(fminf(fmaxf(y, -60000.f), 60000.f) * 16.f));
    }
}

// per column, near -> far: row i's slot becomes the horizon of rows 0..i-1; slot nrow = the full silhouette
extern "C" __global__ void wt_scan(int* __restrict__ HB, const int nrow, const int W) {
    const int X = blockIdx.x * blockDim.x + threadIdx.x;
    if (X >= W) return;
    int H = 0x7f7f7f7f;
    for (int i = 0; i < nrow; ++i) {
        const int v = HB[(size_t)i * W + X];
        HB[(size_t)i * W + X] = H;
        H = min(H, v);
    }
    HB[(size_t)nrow * W + X] = H;
}

// soft visibility of every vertex against the horizon in front of its row
extern "C" __global__ void wt_vis(float4* __restrict__ V, const float2* __restrict__ S, const int* __restrict__ HB,
                                  const int n, const int nrow, const int ncol, const int mode, const int W) {
    const int t = blockIdx.x * blockDim.x + threadIdx.x;
    if (t >= n) return;
    const int i = mode == 0 ? t / ncol : t %% nrow;
    const float2 s = S[t];
    if (s.x < -1e8f) { V[t].w = 0.f; return; }
    const int X = min(max((int)floorf(s.x), 0), W - 1);
    const int hb = HB[(size_t)i * W + X];
    if (hb >= BIGK) return;
    const float H = (float)hb * (1.f / 16.f);
    V[t].w *= smooth(-0.6f, 0.9f, H - s.y);
}

// background at output resolution: sky, horizon haze and striped sun above the terrain silhouette, dark fill below
extern "C" __global__ void wt_bg(uchar4* __restrict__ out, const int OW, const int OH, const int* __restrict__ HB,
                                 const int hoff, const int AW, const int AH, const float* __restrict__ g) {
    const int x = blockIdx.x * blockDim.x + threadIdx.x;
    const int y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= OW || y >= OH) return;
    const float ax = ((float)x + 0.5f) * (float)AW / (float)OW, ay = ((float)y + 0.5f) * (float)AH / (float)OH;
    const float fx = ax - 0.5f;
    const int x0 = min(max((int)floorf(fx), 0), AW - 1), x1 = min(x0 + 1, AW - 1);
    const float tx = clamp01(fx - (float)x0);
    const int h0 = HB[hoff + x0], h1 = HB[hoff + x1];
    float Hs;
    if (h0 >= BIGK && h1 >= BIGK) Hs = 1e9f;
    else if (h0 >= BIGK) Hs = h1 * (1.f / 16.f);
    else if (h1 >= BIGK) Hs = h0 * (1.f / 16.f);
    else Hs = (h0 + (h1 - h0) * tx) * (1.f / 16.f);
    const float gm = smooth(Hs - 0.9f, Hs + 0.6f, ay);           // 0 sky .. 1 terrain fill
    // view ray -> elevation / azimuth
    const float px = (ax - g[G_CX]) / g[G_FOCAL], py = -(ay - g[G_CY]) / g[G_FOCAL];
    float rx = g[G_RIGHT] * px + g[G_UP] * py + g[G_FWD];
    float ry = g[G_RIGHT + 1] * px + g[G_UP + 1] * py + g[G_FWD + 1];
    float rz = g[G_RIGHT + 2] * px + g[G_UP + 2] * py + g[G_FWD + 2];
    const float il = rsqrtf(rx * rx + ry * ry + rz * rz);
    rx *= il; ry *= il; rz *= il;
    const float elev = asinf(fminf(fmaxf(ry, -1.f), 1.f));
    const float az = atan2f(rx, rz);
    const float* P = g + G_PAL;
    const float dr = g[G_DRIFT];
    const float3 top = pal(dr + 0.62f, P);
    const float3 hz = pal(dr + 0.08f, P);
    const float sky = g[G_SKY];
    const float hg = __expf(-fabsf(elev) * 9.f) * sky;
    float r = top.x * 0.03f + hz.x * hg * 0.5f;
    float gg = top.y * 0.03f + hz.y * hg * 0.5f;
    float bb = top.z * 0.04f + hz.z * hg * 0.5f;
    // sun: disc with the classic widening slats in its lower half, soft halo
    const float sr = g[G_SUNR], sun = g[G_SUN];
    if (sun > 0.f) {
        const float dy = elev - g[G_SUNEL];
        const float d = sqrtf(az * az + dy * dy);
        float disc = smooth(sr + 0.004f, sr - 0.004f, d);
        const float v = clamp01((dy + sr) / (2.f * sr));          // 0 bottom .. 1 top
        if (v < 0.6f) {
            float q = v * 10.f + g[G_STRIPE];
            q -= floorf(q);
            const float gw = 0.55f * (1.f - v / 0.6f);
            disc *= smooth(gw - 0.04f, gw + 0.04f, q);
        }
        const float3 sc = pal(dr + 0.1f + 0.32f * (1.f - v), P);
        const float sb = (1.0f + 0.45f * v) * sun;
        const float halo = __expf(-fmaxf(d - sr, 0.f) * 9.f) * 0.22f * sun;
        r += sc.x * disc * sb + hz.x * halo;
        gg += sc.y * disc * sb + hz.y * halo;
        bb += sc.z * disc * sb + hz.z * halo;
    }
    // terrain fill: near black, with a little haze close to the horizon
    const float3 fc = pal(dr + 0.55f, P);
    const float haze = __expf(-fabsf(elev) * 30.f) * sky * 0.3f;
    const float fr_ = fc.x * 0.012f + hz.x * haze, fg_ = fc.y * 0.012f + hz.y * haze, fb_ = fc.z * 0.016f + hz.z * haze;
    const float k = g[G_BGGAIN] * 255.f;
    r = (r + (fr_ - r) * gm) * k;
    gg = (gg + (fg_ - gg) * gm) * k;
    bb = (bb + (fb_ - bb) * gm) * k;
    out[(size_t)y * OW + x] = make_uchar4((unsigned char)fminf(fmaxf(r + 0.5f, 0.f), 255.f),
                                          (unsigned char)fminf(fmaxf(gg + 0.5f, 0.f), 255.f),
                                          (unsigned char)fminf(fmaxf(bb + 0.5f, 0.f), 255.f), 255);
}
""" % {k: v for k, v in globals().items() if k.startswith("G_") or k in ("NB", "BIGK")}


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _glerp(a: float, b: float, t: float) -> float:
    return math.exp(math.log(a) + (math.log(b) - math.log(a)) * t)


class WireTerrain(Source):
    name = "wire_terrain"
    label = "Wire terrain"
    description = ("A wireframe terrain flyover morphing from Unknown Pleasures ridgelines to an 80s vector grid "
                   "under a striped sun; beats roll bump waves in from the horizon.")
    params = {
        **standard_scene_params(palette="neon", render_scale=0.5, period=1.0, drift=0.02),
        "style": num(0.0, 1.0, 1.0, "Style (ridgelines .. grid)", tier="regional", basic=True),
        "line_density": num(0.0, 1.0, 0.45, "Line density", tier="regional", basic=True),
        "terrain_roughness": num(0.0, 1.0, 0.5, "Terrain roughness", tier="regional"),
        "camera_height": num(0.5, 2.0, 1.0, "Camera height", unit="×", tier="regional"),
        "glow": num(0.0, 2.0, 0.8, "Glow", tier="regional"),
        "sun": num(0.0, 1.0, 0.75, "Sun and sky", tier="regional"),
        "ripple": num(0.0, 1.0, 0.5, "Ripple rows", tier="regional"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.sp = Splat3D(ctx, self.w, self.h, max_points=1024, max_lines=R_MAX * C + NL * R_MAX,
                          pool=3 << 19)
        mod = raw_module(_SRC, ("--std=c++14", "--use_fast_math"))
        self.k_gen = mod.get_function("wt_gen")
        self.k_raster = mod.get_function("wt_raster")
        self.k_scan = mod.get_function("wt_scan")
        self.k_vis = mod.get_function("wt_vis")
        self.k_bg = mod.get_function("wt_bg")
        self.V = cp.zeros((R_MAX * C, 4), cp.float32)
        self.S = cp.zeros((R_MAX * C, 2), cp.float32)
        self.IDX = cp.zeros(R_MAX * C, cp.float32)
        self.LV = cp.zeros((NL * R_MAX, 4), cp.float32)
        self.LS = cp.zeros((NL * R_MAX, 2), cp.float32)
        self.LIDX = cp.zeros(NL * R_MAX, cp.float32)
        self.HB = cp.zeros((R_MAX + 1) * self.w, cp.int32)
        self.g = cp.zeros(NG, cp.float32)
        self.gh = np.zeros(NG, np.float32)
        self.phi = 0.0          # row phase (rows, wrapped to [0, 1))
        self.tl = 0.0           # flight travel in noise-lattice units (wrapped to [0, 4096))
        self.rip = 0.0          # ripple phase (cycles, wrapped)
        self.sway = 0.0         # camera sway phase
        self.bob = 0.0
        self.stripe = 0.0
        self.drift = 0.0
        self.e_s = None
        self.prev_pulse = 0.0
        self.since_bump = 1.0
        self.bumps = [[0.0, 0.0] for _ in range(NB)]   # [depth fraction, amplitude]

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.1)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        a = 1.0 - math.exp(-dt / 0.3) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s
        s = min(max(float(p["style"]), 0.0), 1.0)
        sm = s * s * (3.0 - 2.0 * s)
        rough = min(max(float(p["terrain_roughness"]), 0.0), 1.0)

        # ---- style geometry: long lens looking down (ridgelines) .. low wide camera (grid)
        S = _glerp(9.0, 1.4, sm)                       # scene scale (world units)
        fov = _lerp(16.0, 60.0, sm)
        th = math.tan(math.radians(fov) * 0.5)
        pitch = math.radians(_lerp(30.0, 6.6, sm))
        Hc = S * float(p["camera_height"])
        zb = Hc / math.tan(pitch + math.radians(fov) * 0.5)              # ground at the bottom edge
        tht = pitch - math.atan((0.5 - 0.16) * 2.0 * th)                 # ground 16% below the top edge
        zmax = 45.0 * Hc
        Zf = min(Hc / math.tan(tht), zmax) if tht > 0.02 else zmax
        r_eff = 30.0 + 170.0 * float(p["line_density"])
        Z0 = max(zb - 0.05 * (Zf - zb), 0.85 * zb)     # first rows just below the bottom edge
        dz = (Zf - Z0) / r_eff
        Z0 -= dz
        r_draw = min(R_MAX, int(math.ceil(r_eff)) + 1)
        cosp, sinp = math.cos(pitch), math.sin(pitch)
        tanhw = th * self.w / self.h
        fr = _glerp(0.6, 0.17, sm)                     # noise lattice per world unit

        # ---- integrate (pulse kicks rates only)
        rows_rate = (0.45 + 3.2 * es ** 1.3 + 0.6 * pulse) * spd
        self.phi = math.fmod(self.phi + rows_rate * dt, 1.0)
        self.tl = math.fmod(self.tl + rows_rate * dz * fr * dt, 4096.0)
        self.rip = math.fmod(self.rip + (0.35 + 1.2 * es) * spd * dt, 1.0)
        self.sway += (0.06 + 0.1 * es) * spd * dt
        self.bob += (0.2 + 0.3 * es) * spd * dt
        self.stripe = math.fmod(self.stripe + (0.1 + 0.25 * es) * spd * dt, 1.0)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # ---- beat bumps: spawn at the far end on a rising edge, travel to the camera, decay
        self.since_bump += dt
        if pulse > 0.2 and pulse - self.prev_pulse > 0.15 and self.since_bump > 0.12:
            k = min(range(NB), key=lambda i: self.bumps[i][1])
            self.bumps[k] = [1.02, _lerp(0.022, 0.25, sm) * S * (0.6 + 0.5 * pulse)]
            self.since_bump = 0.0
        self.prev_pulse = pulse
        bump_v = (0.42 + 0.35 * es) * spd
        for b in self.bumps:
            if b[1] > 0.0:
                b[0] -= bump_v * dt
                b[1] *= math.exp(-dt / 1.8)
                if b[0] < -0.2 or b[1] < 1e-4:
                    b[0], b[1] = 0.0, 0.0

        # ---- camera (terrain frame: camera above x = 0, flying +z)
        eye_y = Hc * (1.0 + (0.015 + 0.03 * es) * math.sin(self.bob))
        pe = pitch + 0.006 * math.sin(0.7 * self.bob + 0.4)
        roll = (0.012 + 0.03 * es) * math.sin(1.37 * self.sway + 0.5)
        cam = look_at((0.0, eye_y, 0.0), (0.0, eye_y - math.sin(pe), math.cos(pe)), fov_deg=fov, roll=roll)
        xoff = _glerp(0.08, 0.25, sm) * S * (0.4 + 0.6 * es) * math.sin(self.sway)

        amp_e = (0.55 + 0.9 * es ** 1.2) * (1.0 + 0.2 * pulse)
        AR = 0.034 * S * amp_e
        AG = 1.75 * S * amp_e
        xh_far = (Zf * cosp + Hc * sinp) * tanhw * 1.18
        gx = max(dz, 2.0 * xh_far / (NL - 16))
        k0 = math.floor(xoff / gx) - NL // 2
        bump_ref = _lerp(0.022, 0.25, sm) * S

        sess = self.session_palette()
        coeffs = np.asarray(PALETTES[p["palette"]] if sess is None else sess, np.float64).reshape(-1)
        sun_el, sun_r = 0.10, 0.2

        g = self.gh
        g[G_HC], g[G_Z0], g[G_DZ], g[G_FRAC], g[G_REFF] = Hc, Z0, dz, self.phi, r_eff
        g[G_TANHW], g[G_COSP], g[G_SINP], g[G_XMARG] = tanhw, cosp, sinp, 1.18
        g[G_FR], g[G_TL], g[G_XOFF], g[G_S] = fr, self.tl, xoff, sm
        g[G_AR], g[G_AG], g[G_GAIN], g[G_SHARP] = AR, AG, _lerp(0.32, 0.66, rough), 1.0 + 1.3 * rough
        g[G_VALLEY] = 0.95 * S
        g[G_RIPA] = _lerp(0.008, 0.08, sm) * S * (0.3 + 0.9 * es) * 2.0 * float(p["ripple"])
        g[G_RIPPH], g[G_RIPLAM], g[G_BUMPW] = self.rip, 0.18, 0.045
        g[G_ZF], g[G_GX], g[G_K0], g[G_FOG] = Zf, gx, k0, _lerp(0.5, 3.2, sm)
        g[G_BUMPGLOW] = 1.4 / max(bump_ref, 1e-4)
        g[G_AMPREF] = AR * (1.0 - sm) * 1.25 + AG * sm
        g[G_VIEW:G_VIEW + 12] = cam._view
        g[G_FOCAL], g[G_CX], g[G_CY], g[G_NEAR] = 0.5 * self.h / th, 0.5 * self.w, 0.5 * self.h, cam.near
        for k, (bp, ba) in enumerate(self.bumps):
            g[G_BUMPS + 2 * k], g[G_BUMPS + 2 * k + 1] = bp, ba
        g[G_PAL:G_PAL + 12] = coeffs
        g[G_DRIFT] = self.drift
        g[G_RIGHT:G_RIGHT + 3] = cam.right
        g[G_UP:G_UP + 3] = cam.up
        g[G_FWD:G_FWD + 3] = cam.fwd
        g[G_SUNEL], g[G_SUNR] = sun_el, sun_r
        g[G_SUN] = float(p["sun"]) * (1.0 + 0.15 * pulse)
        g[G_SKY] = float(p["sun"]) * (0.55 + 0.45 * es) * (1.0 + 0.2 * pulse)
        g[G_STRIPE] = self.stripe
        g[G_BGGAIN] = min(float(p["intensity"]), 1.5)
        self.g.set(g)

        # ---- terrain vertices, hidden-line horizon, visibility
        seed = np.uint32(self.seed)
        cw = min(max((sm - 0.15) / 0.7, 0.0), 1.0)
        cw = cw * cw * (3.0 - 2.0 * cw)
        W = np.int32(self.w)
        nr = np.int32(r_draw)
        self.HB.data.memset_async(0x7F, (r_draw + 1) * self.w * 4)
        launch_2d(self.k_gen, C, r_draw, (self.V, self.S, self.IDX, self.g, np.int32(C), nr, np.int32(0), seed))
        if cw > 0.01:
            launch_2d(self.k_gen, NL, r_draw, (self.LV, self.LS, self.LIDX, self.g, np.int32(NL), nr, np.int32(1),
                                               seed))
        launch_2d(self.k_raster, C - 1, r_draw, (self.S, self.HB, np.int32(C), nr, W))
        self.k_scan(((self.w + 127) // 128,), (128,), (self.HB, nr, W))
        n_rows = r_draw * C
        self.k_vis(((n_rows + 255) // 256,), (256,), (self.V, self.S, self.HB, np.int32(n_rows), nr, np.int32(C),
                                                       np.int32(0), W))
        if cw > 0.01:
            n_l = NL * r_draw
            self.k_vis(((n_l + 255) // 256,), (256,), (self.LV, self.LS, self.HB, np.int32(n_l), nr, np.int32(NL),
                                                        np.int32(1), W))

        # ---- background (sky, sun, silhouette fill) straight into out
        Ho, Wo = int(out.shape[0]), int(out.shape[1])
        launch_2d(self.k_bg, Wo, Ho, (out, np.int32(Wo), np.int32(Ho), self.HB, np.int32(r_draw * self.w), W,
                                      np.int32(self.h), self.g))

        # ---- lines
        sp = self.sp
        sp.clear()
        pal = tuple(coeffs.tolist())
        period = float(p["palette_period"])
        zmid = 0.5 * (Z0 + Zf)
        dref = zmid * cosp + Hc * sinp
        line_i = 1.05 * (0.6 + 0.6 * es) * (1.0 + 0.3 * pulse)
        width = _lerp(1.5, 1.9, sm)
        sp.polylines(self.V, k=C, n_strips=r_draw, cam=cam, intensity=line_i, width_px=width, depth_ref=dref,
                     size_range=(0.8, 2.5), weight_in_w=True, palette=pal, index=self.IDX, period=period,
                     drift=self.drift)
        if cw > 0.01:
            sp.polylines(self.LV, k=r_draw, n_strips=NL, cam=cam, intensity=0.85 * line_i * cw, width_px=width,
                         depth_ref=dref, size_range=(0.8, 2.5), weight_in_w=True, palette=pal, index=self.LIDX,
                         period=period, drift=self.drift + 0.04)
        sp.resolve(out, exposure=float(p["intensity"]), tonemap="aces", saturation=1.1,
                   glow=float(p["glow"]) * (0.45 + 0.75 * es) * (1.0 + 0.4 * pulse), glow_radius_px=18.0,
                   glow_wide=0.5, background=out)

    def close(self) -> None:
        if getattr(self, "sp", None) is not None:
            self.sp.close()
        self.sp = None
        self.V = self.S = self.IDX = self.LV = self.LS = self.LIDX = self.HB = self.g = None
