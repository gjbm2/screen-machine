"""Synthwave canyon: a retrowave flight down a neon canyon under a striped sun.

Source: https://www.shadertoy.com/view/slcXW8 ('Synthwave canyon');
        mirror https://github.com/ArthurTent/ShaderAmp/blob/main/dist/shaders/SynthwaveCanyon.frag
Author: Mårten Rånge (mrange); CUDA port and new parts by smfx
License: CC0-1.0 (the shader's header reads '// CC0 - Synthwave canyon').  Its helpers: hg_sdf mod1 ('MIT OR
         CC-BY-NC-4.0', not used here), Inigo Quilez value noise / fbm / pmin ('MIT'; reimplemented here, not
         copied), WTFPL hsv2rgb (not used: colour comes from the shared cosine palettes).  The helpers marked
         'License: Unknown' in the original (tanh_approx, alphaBlend, hash) are NOT used: blending, hashing and
         shaping below are written from scratch.
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the plane-march idea is kept, i.e. no SDF march: the camera flies through
        a stack of NPL depth planes, each a z-slice of an fbm canyon heightfield, composited front to back over a
        gradient sky.  New: an analytic floor grid on the canyon bottom (true converging lines) plus wall grid lines
        on the slices; pixel-footprint line widths and octave fading (no shimmer at render_scale 0.5); a striped
        sun whose scanlines scroll, coloured from the palette's warm end; stars; z-periodic noise (period 256)
        and a periodic flight path so the host can wrap the flight distance forever; the plane lattice advances
        by a host fraction, so plane_spacing eases without the landscape reshuffling; ACES + sRGB dither store.)

Pipeline: one kernel at render_scale (default 0.5), then present().  Per pixel: the sky (gradient + sun + corona +
stars) is computed once; then for each of NPL planes (camera-relative z = (i - u) * spacing) the ray hits the plane,
the canyon height h(x, z) = floor + wall(|x|) * (amp * fbm + outward rise) is evaluated (skipped when the hit point
is clearly above any possible ridge), the face below the ridge is an opaque dark fill, the ridge line is an
emissive core + halo, and the face carries grid lines.  Far planes fade into the sky colour behind them; the
nearest plane fades out as it passes the camera.  Colour: plane depth -> pal(depth * period + drift); the sun takes
the palette's warm end (found on the host per palette); sky_mix rotates the sky and plane hues (sunset -> cyber).

Macros: energy (e' = smoothstep(0.1, 0.85, e)) -> flight speed x(0.4 + 1.2 e'), canyon wall amplitude (smoothstep,
slewed 0.8 s, floor 55%), grid line glow, ridge halo, sun-stripe scroll rate, far-plane weight (e'^1.5, slewed:
how deep the canyon reads before it fades into the sky).  pulse (k = pulse * (0.3 + 0.7 e)) -> a brightness wave
on the ridge and grid lines that rushes from the horizon toward the camera (spawned on beat rising edges; gain
``flash_kick``, <= +35% gain plus a halo swell), a rate kick into the sun-scanline scroll (``sun_kick``) and a
spring-damped FOV breath of up to 3 degrees.  speed scales every rate; palette/period/drift colour everything;
intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, wrap
from stream_service.plugins._lib.upsample import internal_size, present

NPL = 20                     # depth planes (fixed: structural/quality)
ZPER = 256                   # noise and flight-path period in z (world units); the host wraps the flight there
# flight path: integer multiples of 2 pi / ZPER so the wrap is seamless (~1, ~sqrt(2), ~sqrt(0.75), ~sqrt(0.5))
PM1, PM2, PM3, PM4 = 41, 58, 35, 29
PCW = 20                     # canyon-width wobble (~0.5 rad / unit)
PATH_AX, PATH_AY = 0.10, 0.06
FLOOR = -0.35

# P layout (floats)
#  0..11 palette | 12 period | 13 drift | 14 warm t | 15 sky offset | 16,17 eye xy | 18 eye world z (wrapped)
#  19..21 ww | 22..24 uu | 25..27 vv | 28 focal | 29 pixel angle | 30 u (lattice fraction) | 31 spacing
#  32 lattice base z (wrapped) | 33 canyon width | 34 fbm gain | 35 wall amp | 36 grid spacing | 37 grid gain
#  38 edge gain | 39 far-fade start | 40,41 wave0 (front, amp) | 42,43 wave1 | 44 1/wave width | 45 halo amount
#  46 sun radius | 47 sun stripe phase | 48 stripes per radius | 49 sun centre (tangent y) | 50,51 path amp x, y
#  52 fill level | 53 output gain | 54 seed | 55..57 sun top (linear) | 58..60 sun bottom (linear)
#  61 plane hue base | 62 sky hue base
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define NPL %(npl)d
#define ZPER %(zper)d
#define FLOORY (%(floor)ff)

__device__ __forceinline__ float sq(float x) { return x * x; }
// integer lattice hash -> [0, 1)
__device__ __forceinline__ float sc_hash(int ix, int iz, unsigned int s) {
    unsigned int h = ((unsigned int)ix * 0x8da6b343u) ^ ((unsigned int)iz * 0xd8163841u + s * 0x9E3779B1u);
    h ^= h >> 15; h *= 0x2c1b3c6du; h ^= h >> 12; h *= 0x297a2d39u; h ^= h >> 15;
    return (float)(h >> 8) * (1.f / 16777216.f);
}
// value noise, periodic in z with lattice period zper (a power of two)
__device__ __forceinline__ float sc_vnoise(float x, float z, int zper, unsigned int s) {
    const float fx = floorf(x), fz = floorf(z);
    const int ix = (int)fx;
    const int iz = ((int)fz) & (zper - 1), iz1 = (iz + 1) & (zper - 1);
    float tx = x - fx, tz = z - fz;
    tx = tx * tx * (3.f - 2.f * tx); tz = tz * tz * (3.f - 2.f * tz);
    const float a = sc_hash(ix, iz, s), b = sc_hash(ix + 1, iz, s);
    const float c = sc_hash(ix, iz1, s), d = sc_hash(ix + 1, iz1, s);
    return mix(mix(a, b, tx), mix(c, d, tx), tz);
}
// sin / cos of 2 pi m z / ZPER with an exact argument reduction (z can be ~300)
__device__ __forceinline__ float psin(float m, float z) { return __sinf(6.2831853f * fract(m * z * (1.f / ZPER))); }
__device__ __forceinline__ float pcos(float m, float z) { return __cosf(6.2831853f * fract(m * z * (1.f / ZPER))); }
__device__ __forceinline__ float2 sc_path(float z, const float* __restrict__ P) {
    return f2(-P[50] * (pcos(%(pm1)d.f, z) + pcos(%(pm3)d.f, z)), -P[51] * (psin(%(pm2)d.f, z) + psin(%(pm4)d.f, z)));
}
// palette colour kept vivid for emissive lines: a dark phase folds toward its bright opposite, then max channel -> 1
__device__ __forceinline__ float3 bright_pal(float t, const float* __restrict__ PAL) {
    float3 c = pal(t, PAL);
    const float m0 = vmax(c);
    const float w = 1.f - smoothstep(0.12f, 0.45f, m0);
    c = mix(c, saturate(f3(2.f * PAL[0], 2.f * PAL[1], 2.f * PAL[2]) - c), w);
    return c * (1.f / fmaxf(vmax(c), 0.3f));
}
// canyon height at plane-local x and world z (aa = world size of a pixel there: fades octaves under ~3 px)
__device__ __forceinline__ float sc_height(float x, float zw, float aa, const float* __restrict__ P, unsigned int seed) {
    const float cwb = P[33];
    const float cw = cwb * (1.f + 0.22f * psin(%(pcw)d.f, zw));
    const float xf = 0.3f + 0.2f * cwb;
    const float ax = fabsf(x);
    const float wall = smoothstep(xf, xf + cw, ax);
    if (wall <= 0.f) return FLOORY;
    float sum = 0.f, amp = 1.f, norm = 0.f, nx = x * 1.1f, nz = zw, cell = 1.f / 1.1f;
    int zp = ZPER;
    #pragma unroll
    for (int o = 0; o < 4; ++o) {
        const float w = smoothstep(1.5f * aa, 4.f * aa, cell);
        const float n = w > 0.f ? sc_vnoise(nx, nz, zp, seed + 101u * (unsigned int)o) : 0.5f;
        sum += amp * (0.5f + w * (n - 0.5f));
        norm += amp;
        nx *= 2.f; nz *= 2.f; zp <<= 1; cell *= 0.5f; amp *= P[34];
    }
    const float n = sum / norm;
    return FLOORY + wall * (P[35] * (0.15f + 1.2f * n) + 0.28f * fminf(ax - xf, 3.f));
}

__device__ float3 sc_sky(float3 rd, float pixa, const float* __restrict__ P, unsigned int seed) {
    const float iz = 1.f / fmaxf(rd.z, 1e-3f);
    const float tx = rd.x * iz, ty = rd.y * iz;
    const float tsk = P[62];
    const float3 top = srgb_to_linear(pal(tsk + 0.5f, P)) * 0.035f;
    const float3 hor = srgb_to_linear(pal(tsk + 0.06f, P)) * 0.42f;
    float3 col = mix(top, hor, __expf(-4.5f * fmaxf(ty, 0.f)));
    // sun (fixed in the world: tangent coords of the world direction)
    const float R = P[46];
    const float sx = tx, sy = ty - P[49];
    const float r = sqrtf(sx * sx + sy * sy);
    const float ds = r - R;
    const float v = sy / R;                                           // -1 bottom .. +1 top
    const float3 stop = load3(P + 55), sbot = load3(P + 58);
    const float3 glow = mix(sbot, stop, 0.4f);
    col += glow * (0.55f * __expf(-2.4f * fmaxf(ds, 0.f) / R) + 0.12f * __expf(-0.5f * fmaxf(ds, 0.f) / R));
    float disk = smoothstep(pixa, -pixa, ds);
    if (disk > 0.f) {
        const float nst = P[48];
        const float sb = v * nst + P[47];                             // phase grows -> scanlines slide down
        const float gw = 0.46f * smoothstep(0.25f, -0.95f, v);       // gap half-width (band units), thicker lower
        const float aab = pixa * nst / R;
        const float fb = fabsf(fract(sb) - 0.5f);
        const float gap = saturate((gw - fb) / aab + 0.5f) * saturate(gw / aab);
        disk *= 1.f - gap;
        const float3 sc = mix(sbot, stop, smoothstep(-1.f, 0.85f, v)) * 2.6f;
        col = mix(col, sc, disk);
    }
    // stars, high in the sky
    if (ty > 0.03f) {
        const float SC = 60.f;
        const float gx = tx * SC, gy = ty * SC;
        const float cx = floorf(gx), cy = floorf(gy);
        const float h = sc_hash((int)cx, (int)cy, seed + 777u);
        if (h > 0.84f) {
            const float jx = sc_hash((int)cx, (int)cy, seed + 778u), jy = sc_hash((int)cx, (int)cy, seed + 779u);
            const float dx = (gx - cx - 0.2f - 0.6f * jx) / SC, dy = (gy - cy - 0.2f - 0.6f * jy) / SC;
            const float rs = 1.1f * pixa;
            const float st = (h - 0.84f) * 6.25f * __expf(-(dx * dx + dy * dy) / (rs * rs))
                           * smoothstep(0.03f, 0.25f, ty) * (1.f - disk);
            col += f3(0.85f, 0.85f, 1.f) * (0.9f * st);
        }
    }
    return col;
}

SDF_KERNEL
void synthwave_canyon(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float asp = (float)W / (float)H;
    const float px = (2.f * (x + 0.5f) / W - 1.f) * asp, py = 1.f - 2.f * (y + 0.5f) / H;
    const float3 ro = f3(P[16], P[17], 0.f);
    const float3 rd = normalize(load3(P + 22) * px + load3(P + 25) * py + load3(P + 19) * P[28]);
    const float pixa = P[29];
    const unsigned int seed = (unsigned int)P[54];
    const float* PAL = P;

    const float3 sky = sc_sky(rd, pixa, P, seed);
    float3 C = f3(0.f);
    float T = 1.f;
    const float u = P[30], s = P[31], zb = P[32];
    const float far = NPL * s;
    const float xf = 0.3f + 0.2f * P[33];
    const float hb = FLOORY + P[35] * 1.36f;
    if (rd.z > 1e-3f) {
        for (int i = 1; i <= NPL; ++i) {
            const float zr = (i - u) * s;
            const float pd = zr / rd.z;
            const float3 pp = ro + rd * pd;
            const float zw = zb + i * s;
            const float2 off = sc_path(zw, P);
            const float lx = pp.x - 2.f * off.x, ly = pp.y - 2.f * off.y;
            const float aa = pixa * pd;
            // travelling beat wave (max of two)
            const float fl = fmaxf(P[41] * __expf(-sq((zr - P[40]) * P[44])), P[43] * __expf(-sq((zr - P[42]) * P[44])));
            const float hw = (0.02f + 3.f * aa) * (1.f + 0.5f * fl);
            const float bound = hb + 0.28f * fminf(fmaxf(fabsf(lx) - xf, 0.f), 3.f);
            if (ly > bound + 4.f * hw + 2.f * aa) continue;           // clearly above any ridge: nothing here
            const float he = sc_height(lx, zw, aa, P, seed);
            const float d = ly - he;
            const float fill = smoothstep(aa, -aa, d);
            const float fadeIn = 1.f - smoothstep(P[39], far, zr);
            const float fadeNear = smoothstep(0.f, 0.4f * s, zr);
            const float idx = P[12] * (0.55f * zr / far) + P[13] + P[61];
            const float3 ec = srgb_to_linear(bright_pal(idx, PAL));
            // ridge line: core (>= ~1 px, thicker up close) + halo
            const float lw = fmaxf(0.9f * aa, 0.0035f);
            const float ad = fabsf(d);
            const float core = __expf(-sq(ad / lw));
            const float halo = __expf(-ad / hw);
            const float eg = P[38] * (1.f + 0.35f * fl);
            float3 em = ec * (eg * (1.4f * core + P[45] * halo)) + f3(0.45f * eg * core * core);
            if (fill > 0.002f) {
                const float gs = P[36];
                const float floorw = 1.f - smoothstep(0.03f, 0.3f, he - FLOORY);
                float g = 0.f;
                if (floorw > 0.f && rd.y < -1e-4f) {
                    // analytic floor grid: where the ray meets this plane's floor level (true converging lines)
                    const float yfl = FLOORY + 2.f * off.y;
                    const float tf = fminf(fmaxf((yfl - ro.y) / rd.y, 0.f), pd);
                    const float2 offh = sc_path(P[18] + tf * rd.z, P);
                    const float xh = ro.x + rd.x * tf - 2.f * offh.x;
                    const float aaf = pixa * fmaxf(tf, 1e-3f);
                    const float dl = fabsf(fract(xh / gs + 0.5f) - 0.5f) * gs;
                    const float lwf = fmaxf(0.8f * aaf, 0.004f);
                    g += floorw * __expf(-sq(dl / lwf)) * smoothstep(2.5f, 6.f, gs / aaf);
                }
                if (floorw < 1.f) {
                    // wall grid: vertical lines on the slice face
                    const float dl = fabsf(fract(lx / gs + 0.5f) - 0.5f) * gs;
                    const float lww = fmaxf(0.8f * aa, 0.004f);
                    g += (1.f - floorw) * 0.55f * __expf(-sq(dl / lww)) * smoothstep(2.5f, 6.f, gs / aa);
                }
                const float3 gc = srgb_to_linear(bright_pal(idx + 0.18f, PAL));
                em += gc * (g * P[37] * (1.f + 0.35f * fl) * fill);
            }
            float3 fc = srgb_to_linear(pal(idx + 0.1f, PAL)) * (P[52] * (0.4f + 1.1f * saturate(he - ly)));
            fc = mix(sky, fc, fadeIn);
            const float a = fill * fadeNear;
            C += (fc * a + em * (fadeIn * fadeNear)) * T;
            T *= 1.f - a;
            if (T < 0.02f) break;
        }
    }
    float3 col = (C + sky * T) * P[53];
    col = vignette(col, x, y, W, H, 0.35f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col)));
}
""" % {"npl": NPL, "zper": ZPER, "floor": FLOOR, "pm1": PM1, "pm2": PM2, "pm3": PM3, "pm4": PM4, "pcw": PCW}

_K = [2.0 * math.pi * m / ZPER for m in (PM1, PM2, PM3, PM4)]


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _norm(v):
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / n for c in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _path(z: float):
    k1, k2, k3, k4 = _K
    off = (-PATH_AX * (math.cos(k1 * z) + math.cos(k3 * z)), -PATH_AY * (math.sin(k2 * z) + math.sin(k4 * z)))
    d1 = (PATH_AX * (k1 * math.sin(k1 * z) + k3 * math.sin(k3 * z)),
          -PATH_AY * (k2 * math.cos(k2 * z) + k4 * math.cos(k4 * z)))
    d2 = (PATH_AX * (k1 * k1 * math.cos(k1 * z) + k3 * k3 * math.cos(k3 * z)),
          PATH_AY * (k2 * k2 * math.sin(k2 * z) + k4 * k4 * math.sin(k4 * z)))
    return off, d1, d2


def _pal_np(c, t):
    c = np.asarray(c, np.float64)
    t = np.asarray(t, np.float64)[..., None]
    return np.clip(c[0:3] + c[3:6] * np.cos(2 * np.pi * (c[6:9] * t + c[9:12])), 0.0, 1.0)


def _lin(c):
    c = np.clip(np.asarray(c, np.float64), 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


_SUN_CACHE: dict = {}


def _sun_for(name: str):
    """The palette's warm end (t), and the sun's top / bottom colours (linear): top = warm end lifted toward a
    hot yellow-white, bottom = the neighbour (+-0.15) that leans most magenta."""
    if name in _SUN_CACHE:
        return _SUN_CACHE[name]
    c = palette_coeffs(name)
    ts = np.linspace(0.0, 1.0, 256, endpoint=False)
    cols = _pal_np(c, ts)
    score = cols[:, 0] + 0.35 * cols[:, 1] - 0.9 * cols[:, 2] + 0.25 * cols.mean(axis=1)
    tw = float(ts[int(np.argmax(score))])
    top = _pal_np(c, tw)
    cands = [_pal_np(c, tw + 0.15), _pal_np(c, tw - 0.15)]
    bot = max(cands, key=lambda q: q[0] + q[2] - 1.2 * q[1])
    top = 0.65 * top + 0.35 * np.array([1.0, 0.92, 0.55])
    top = top / max(top.max(), 0.6)
    bot = bot / max(bot.max(), 0.5)
    res = (tw, _lin(top), _lin(bot))
    _SUN_CACHE[name] = res
    return res


class SynthwaveCanyon(Source):
    name = "synthwave_canyon"
    label = "Synthwave canyon"
    description = ("A retrowave flight down a neon canyon under a striped sun: stacked depth planes of glowing "
                   "fbm ridges and a laser grid floor over a sunset sky.")
    params = {
        "canyon_width": num(0.5, 2.5, 1.25, "Canyon width", tier="regional", basic=True),
        "grid_density": num(0.0, 1.0, 0.45, "Grid density", tier="regional", basic=True),
        "ridge_roughness": num(0.3, 0.7, 0.45, "Ridge roughness", tier="regional"),
        "sun_size": num(0.5, 1.5, 1.0, "Sun size", tier="regional"),
        "plane_spacing": num(0.3, 0.8, 0.5, "Plane spacing (depth detail)", tier="regional"),
        "sky_mix": num(0.0, 1.0, 0.0, "Sky (sunset -> cyber)", tier="regional"),
        "flash_kick": num(0.0, 2.0, 1.0, "Beat wave", tier="regional"),
        "sun_kick": num(0.0, 2.0, 1.0, "Beat sun scroll", tier="regional"),
        **standard_scene_params(palette="dusk", render_scale=0.5, period=0.6, drift=0.02),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("synthwave_canyon")
        self.Ph = np.zeros(64, np.float32)
        self.P = cp.zeros(64, cp.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.zbase = float(rng.uniform(0.0, ZPER))
        self.u = 0.0
        self.sun_ph = 0.0
        self.drift = 0.0
        self.es = None
        self.fov_x = 0.0
        self.fov_v = 0.0
        self.waves = [[0.0, 0.0], [0.0, 0.0]]       # (front distance, amplitude)
        self.armed = True
        self.since = 1.0
        self.slot = 0

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        ep = _ss(0.1, 0.85, e)
        a = 1.0 - math.exp(-dt / 0.8) if dt > 0 else 1.0
        self.es = ep if self.es is None else self.es + (ep - self.es) * a
        es = self.es
        k = pulse * (0.3 + 0.7 * e)
        rate = 0.4 + 1.2 * ep
        fk = float(p["flash_kick"])

        # ---- flight: advance the plane lattice by a fraction (spacing can ease without reshuffling the slices)
        s = float(p["plane_spacing"])
        self.u += 0.85 * rate * spd * dt / s
        if self.u >= 1.0:
            n = math.floor(self.u)
            self.u -= n
            self.zbase = wrap(self.zbase + n * s, float(ZPER))
        zc = self.zbase + self.u * s
        far = NPL * s

        # ---- phases (host-integrated, wrapped; kicks add to rates only)
        self.sun_ph = wrap(self.sun_ph + 0.35 * rate * (1.0 + 2.5 * float(p["sun_kick"]) * k) * spd * dt, 1.0)
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 20.0)

        # ---- beat wave: spawn on a rising edge (hysteresis 0.5 / 0.25, >= 150 ms apart), rush to the camera
        self.since += dt
        if pulse < 0.25:
            self.armed = True
        if self.armed and pulse > 0.5 and self.since >= 0.15:
            wd = 0.12 * far + s
            self.waves[self.slot] = [far + wd, (0.3 + 0.7 * e) * fk]
            self.slot ^= 1
            self.armed = False
            self.since = 0.0
        wd = 0.12 * far + s
        for wv in self.waves:
            if wv[1] > 0.0:
                wv[0] -= (far + wd) / 0.6 * dt
                if wv[0] < -2.0 * wd:
                    wv[1] = 0.0

        # ---- FOV breath: spring-damped, up to 3 degrees narrower on the beat
        target = min(k * min(fk, 1.0) * 1.2, 1.0)
        if dt > 0:
            nsub = max(1, min(8, int(math.ceil(dt * 240.0))))
            h = dt / nsub
            w0 = 2.0 * math.pi * 2.2
            for _ in range(nsub):
                acc = w0 * w0 * (target - self.fov_x) - 2.0 * 0.6 * w0 * self.fov_v
                self.fov_v += acc * h
                self.fov_x += self.fov_v * h
        fov = 56.0 - 3.0 * min(max(self.fov_x, -0.4), 1.0)
        f = 1.0 / math.tan(math.radians(fov) * 0.5)

        # ---- camera: follow the winding path (the canyon itself winds twice as far)
        off, d1, d2 = _path(zc)
        ww = _norm((d1[0], d1[1], 1.0))
        up = (0.6 * d2[0], 1.0 + 0.6 * d2[1], 0.0)
        uu = _norm(_cross(up, ww))
        vv = _cross(ww, uu)

        tw, sun_top, sun_bot = _sun_for(p["palette"])
        ofs = 0.5 * float(p["sky_mix"])
        gd = float(p["grid_density"])
        sun_size = float(p["sun_size"])
        farw = 0.5 + 0.5 * es ** 1.5

        P = self.Ph
        P[0:12] = palette_coeffs(p["palette"])
        P[12] = float(p["palette_period"])
        P[13] = self.drift
        P[14] = tw
        P[15] = ofs
        P[16], P[17] = off
        P[18] = zc
        P[19:22] = ww
        P[22:25] = uu
        P[25:28] = vv
        P[28] = f
        P[29] = 2.0 / (self.h * f)
        P[30] = self.u
        P[31] = s
        P[32] = self.zbase
        P[33] = float(p["canyon_width"])
        P[34] = float(p["ridge_roughness"])
        P[35] = 0.55 + 0.45 * _ss(0.0, 1.0, es)
        P[36] = 1.0 / (1.5 + 8.5 * gd)
        P[37] = 0.45 + 0.75 * ep
        P[38] = 0.9 + 0.25 * ep
        P[39] = far * (0.3 + 0.45 * farw)
        P[40], P[41] = self.waves[0]
        P[42], P[43] = self.waves[1]
        P[44] = 1.0 / wd
        P[45] = 0.3 + 0.3 * ep
        P[46] = 0.28 * sun_size
        P[47] = self.sun_ph
        P[48] = 3.0 + 4.0 * sun_size
        P[49] = 0.1
        P[50], P[51] = PATH_AX, PATH_AY
        P[52] = 0.05
        P[53] = float(p["intensity"]) * 1.1
        P[54] = float(int(p["seed"]) % 65536)
        P[55:58] = sun_top
        P[58:61] = sun_bot
        P[61] = wrap(tw + 0.6 + 0.6 * ofs, 20.0)
        P[62] = wrap(tw + ofs + 0.5 * self.drift, 10.0)
        self.P.set(P)

        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)
