"""Hyperspace jump: a warp-speed 3D starfield of motion streaks that morphs into a spiralling hyperspace tunnel.

Source: Original (inspired by https://en.wikipedia.org/wiki/Starfield_(screensaver))
Author: smfx (original)
License: MIT (original)
license_class: permissive

Built on smfx/_lib/splat3d.py.  Up to 100k stars live in a hollow cylinder around the flight axis (inner radius =
tunnel_radius).  A custom kernel regenerates every star each frame from (hash, integrated travel phase): the head
depth wraps modulo the depth range (the wrap happens behind the camera, deeper as the streaks grow, so the field
is infinite with no state jumps), and each star becomes a 3-segment motion streak from its current to its past
position (the past follows the roll and the depth twist, so streaks curve into a spiral during the jump).  Per-end
weights fade each streak from a bright head to a dark tail; per-segment colour is the palette (index from star id
and depth, + drift) times a chromatic Doppler ramp: blue-white ahead, red towards the rim.  Splat3D draws the
streaks (lines) and the star heads (points) at 0.5 scale.  Before that a second kernel writes the background
straight into the accumulator: two cylinders of nebula wisps (ridged value noise, periodic along the flight axis
so the travel phase wraps seamlessly, stretched along z by the jump, co-rotating with the stars), a vanishing-point
core glow, and the odd planet (lit sphere with palette bands, atmosphere rim and sometimes a ring) drifting past.
resolve() adds bloom + ACES and writes the 4K output directly.

Warp factor W = warp_factor x energy curve; the jump morph j = smoothstep(0.62, 1.12, W) stretches the streaks into
long tunnel lines, adds spiral twist and roll, stretches the nebula and lights the core.

Macros: energy -> forward velocity (integrated), streak length and jump morph (via W), star and streak brightness,
roll rate, bloom, camera steering.  pulse -> a forward surge (velocity kick, decays with the envelope), a streak
and core flash and a small exposure punch.  speed scales velocity, roll and steering; drift tints stars, nebula
and planets (session palette honoured); intensity is exposure.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import launch_2d
from stream_service.plugins._lib.palettes import PALETTES
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.splat3d import SPLAT3D_OPTIONS, SPLAT3D_SRC, Splat3D, look_at
from stream_service.plugins._lib.upsample import internal_size

N_MAX = 100_000          # star capacity
N_BASE = 64_000          # stars at density 1 with the default wrap range
SEGS = 3                 # streak sub-segments (must match HJ_S)
DEPTH = 60.0             # visible depth range (world units)
R_OUT = 7.0              # cylinder outer radius
FOV = 72.0
PLANET_SPAWN = 125.0     # planet starts here (fades in from 125 -> 95)
PLANET_SLOT = 140.0      # planet travel per slot
PLANET_RATE = 0.3        # planets travel at 0.3 x the star flow (bigger, further: parallax)
TAU = 2.0 * math.pi

_SRC = SPLAT3D_SRC + r"""
#define HJ_S 3
__device__ __forceinline__ float hj_fract(float x) { return x - floorf(x); }
__device__ __forceinline__ float hj_wrap(float a) { return a - 6.2831853f * floorf(a * 0.15915494f); }
__device__ __forceinline__ float3 hj_pal(const float* __restrict__ P, float t) {   // cosine palette -> linear
    const float k = 6.2831853f;
    return make_float3(sp_lin(sp_clamp01(P[0] + P[3] * __cosf(k * (P[6] * t + P[9])))),
                       sp_lin(sp_clamp01(P[1] + P[4] * __cosf(k * (P[7] * t + P[10])))),
                       sp_lin(sp_clamp01(P[2] + P[5] * __cosf(k * (P[8] * t + P[11])))));
}
__device__ __forceinline__ float3 hj_mulc(float3 a, float3 b) { return make_float3(a.x * b.x, a.y * b.y, a.z * b.z); }
// chromatic Doppler: a = tan(angle off the flight axis); blue-white ahead -> neutral -> red at the rim
__device__ __forceinline__ float3 hj_doppler(float a, float dop) {
    const float s = sp_smooth(0.03f, 0.8f, a);
    float3 t;
    if (s < 0.5f) t = sp_mix3(make_float3(0.55f, 0.82f, 1.6f), make_float3(1.f, 1.f, 1.f), 2.f * s);
    else          t = sp_mix3(make_float3(1.f, 1.f, 1.f), make_float3(1.75f, 0.58f, 0.34f), 2.f * s - 1.f);
    return sp_mix3(make_float3(1.f, 1.f, 1.f), t, dop);
}

// ------------------------------------------------------------------ stars -> streak segments + head points
// U: 0 zph | 1 range Dr | 2 zlo | 3 r_in | 4 r_out | 5 L | 6 roll | 7 twist k (rad/unit) | 8 roll per unit length
//    9 n_f (fade-in count) | 10 drift | 11 period | 12 doppler | 13 jump | 14 head weight | 15 colour spread
//    16..27 palette | 28 far depth D | 29 wrap fade band
extern "C" __global__ void hj_stars(const float* __restrict__ U, float4* __restrict__ P0, float4* __restrict__ P1,
                                   float* __restrict__ RGB, float4* __restrict__ HD, float* __restrict__ HRGB,
                                   const int n, const int nmax, const unsigned int seed)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const float zph = U[0], Dr = U[1], zlo = U[2], rin = U[3], rout = U[4], L = U[5];
    const float roll = U[6], twk = U[7], rpl = U[8], nf = U[9], drift = U[10], period = U[11];
    const float dop = U[12], jump = U[13], hw = U[14], cspread = U[15], D = U[28], band = U[29];
    const float* pal = U + 16;
    const float3 h1 = sp_hash3((unsigned int)i, seed), h2 = sp_hash3((unsigned int)i, seed ^ 0x5bd1e995u);
    const float th0 = 6.2831853f * h1.x;
    const float r = sqrtf(rin * rin + h1.y * (rout * rout - rin * rin));
    const float zh = zlo + Dr * hj_fract(h1.z - zph);
    float b = (0.08f + 0.92f * h2.x * h2.x * h2.x);
    b *= sp_clamp01((nf - (float)i) / fmaxf(0.04f * (float)nmax, 1.f));     // density eases star by star
    b *= sp_smooth(zlo, zlo + band, zh);                                      // wrap fade (behind the camera)
    float4 P[HJ_S + 1];
    #pragma unroll
    for (int k = 0; k <= HJ_S; ++k) {
        const float s = L * (float)k * (1.f / HJ_S);
        const float z = zh + s;
        float sn, cs;
        __sincosf(hj_wrap(th0 + roll + twk * z - rpl * s), &sn, &cs);
        const float u = 1.f - (float)k * (1.f / HJ_S);
        P[k] = make_float4(r * cs, r * sn, z, b * u * sqrtf(u));
    }
    float3 c0 = make_float3(0.f, 0.f, 0.f);
    #pragma unroll
    for (int k = 0; k < HJ_S; ++k) {
        const int q = i * HJ_S + k;
        P0[q] = P[k];
        P1[q] = P[k + 1];
        const float zm = zh + L * ((float)k + 0.5f) * (1.f / HJ_S);
        const float a = r / fmaxf(zm, 0.05f);
        const float idx = h2.y * cspread + 0.55f * sp_clamp01(zm / D);
        float3 c = hj_mulc(hj_pal(pal, idx * period + drift), hj_doppler(a, dop));
        const float lc = sp_lum(c);
        c = sp_mix3(c, make_float3(lc, lc, lc), 0.2f * jump);
        RGB[3 * q] = c.x; RGB[3 * q + 1] = c.y; RGB[3 * q + 2] = c.z;
        if (k == 0) c0 = c;
    }
    HD[i] = make_float4(P[0].x, P[0].y, P[0].z, b * hw);
    const float l0 = sp_lum(c0);
    HRGB[3 * i] = 0.65f * c0.x + 0.35f * l0; HRGB[3 * i + 1] = 0.65f * c0.y + 0.35f * l0;
    HRGB[3 * i + 2] = 0.65f * c0.z + 0.35f * l0;
}

// ------------------------------------------------------------------ background: nebula wisps, core glow, planet
__device__ __forceinline__ float hj_lat(int x, int y, int z, int pz, unsigned int seed) {
    z = ((z % pz) + pz) % pz;                                    // periodic along the flight axis
    return sp_lat(x, y, z, seed);
}
__device__ float hj_vn(float x, float y, float z, int pz, unsigned int seed) {
    const float fx = floorf(x), fy = floorf(y), fz = floorf(z);
    const int ix = (int)fx, iy = (int)fy, iz = (int)fz;
    float tx = x - fx, ty = y - fy, tz = z - fz;
    tx = tx * tx * (3.f - 2.f * tx); ty = ty * ty * (3.f - 2.f * ty); tz = tz * tz * (3.f - 2.f * tz);
    const float a = hj_lat(ix, iy, iz, pz, seed), b = hj_lat(ix + 1, iy, iz, pz, seed);
    const float c = hj_lat(ix, iy + 1, iz, pz, seed), d = hj_lat(ix + 1, iy + 1, iz, pz, seed);
    const float e = hj_lat(ix, iy, iz + 1, pz, seed), f = hj_lat(ix + 1, iy, iz + 1, pz, seed);
    const float g = hj_lat(ix, iy + 1, iz + 1, pz, seed), h = hj_lat(ix + 1, iy + 1, iz + 1, pz, seed);
    const float x0 = a + (b - a) * tx, x1 = c + (d - c) * tx, x2 = e + (f - e) * tx, x3 = g + (h - g) * tx;
    const float y0 = x0 + (x1 - x0) * ty, y1 = x2 + (x3 - x2) * ty;
    return y0 + (y1 - y0) * tz;
}
__device__ float hj_fbm(float x, float y, float z, int oct, unsigned int seed) {
    float s = 0.f, amp = 0.5f, nrm = 0.f;
    int pz = 64;
    for (int o = 0; o < oct && o < 4; ++o) {
        s += amp * hj_vn(x, y, z, pz, seed + 31u * (unsigned int)o);
        nrm += amp; x *= 2.f; y *= 2.f; z *= 2.f; pz *= 2; amp *= 0.5f;
    }
    return s / nrm;
}

// B: 0 f | 1 cx | 2 cy | 3..5 right | 6..8 up | 9..11 fwd | 12 nebula z phase | 13 roll | 14 twist k
//    15 noise freq around | 16 noise freq along | 17 nebula amount | 18 fog length | 19 core | 20 drift | 21 period
//    22..33 palette | 34 jump | 35 flash | 36 doppler | 37 Rn1 | 38 Rn2
//    40 planet weight | 41..43 planet centre | 44 planet radius | 45..47 planet axis | 48 planet index
//    49 band freq | 50 ring | 51 planet noise seed
extern "C" __global__ void hj_bg(float* __restrict__ acc, const int W, const int H, const float* __restrict__ B,
                                const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float f = B[0];
    const float u = (x + 0.5f - B[1]) / f, v = -(y + 0.5f - B[2]) / f;
    const float3 d = make_float3(B[9] + u * B[3] + v * B[6], B[10] + u * B[4] + v * B[7], B[11] + u * B[5] + v * B[8]);
    const float phz = B[12], roll = B[13], twk = B[14], fxy = B[15], fz = B[16], neb = B[17], fogl = B[18];
    const float core = B[19], drift = B[20], period = B[21], jump = B[34], flash = B[35], dop = B[36];
    const float* pal = B + 22;
    const float rho = sqrtf(d.x * d.x + d.y * d.y) + 1e-6f;
    const float a = rho / fmaxf(d.z, 1e-3f);                     // tan(angle off the flight axis)
    float3 col = sp_mul3(hj_pal(pal, drift + 0.62f), 0.0025f);   // deep-space floor

    // nebula wisps on two co-rotating cylinders (the ray meets radius Rn at depth z = Rn / a)
    if (neb > 0.f) {
        #pragma unroll
        for (int l = 0; l < 2; ++l) {
            const float Rn = B[37 + l];
            const float z = Rn / fmaxf(a, 1e-4f);
            if (z < 0.05f || z > 30.f * fogl) continue;
            float sn, cs;
            __sincosf(hj_wrap(-(roll + twk * z)), &sn, &cs);
            const float ux = d.x / rho, uy = d.y / rho;
            const float px = (ux * cs - uy * sn) * Rn, py = (ux * sn + uy * cs) * Rn;
            const float zz = z * fz + phz + 17.f * l;
            const float n1 = hj_fbm(px * fxy, py * fxy, zz, 3, seed + 101u * l);
            const float n2 = hj_vn(px * fxy * 0.37f + 5.3f, py * fxy * 0.37f, zz * 0.5f, 32, seed + 977u + 13u * l);
            float rid = 1.f - fabsf(2.f * n1 - 1.f);
            rid = rid * rid; rid = rid * rid;
            const float dens = rid * sp_smooth(0.3f, 0.75f, n2) + 0.15f * sp_smooth(0.45f, 0.9f, n1);
            const float fade = __expf(-z / fogl) * sp_smooth(0.15f, 2.2f, z);
            const float3 c = hj_mulc(hj_pal(pal, (n1 * 0.7f + 0.33f * l) * period + drift + 0.5f),
                                     hj_doppler(Rn / z, 0.6f * dop));
            col = sp_add3(col, sp_mul3(c, dens * fade * neb * (l == 0 ? 0.2f : 0.14f)));
        }
    }
    // vanishing-point core glow (lights up with the jump)
    if (core > 0.f) {
        const float sg = 0.022f + 0.05f * jump;
        const float g = __expf(-(a * a) / (sg * sg)) + 0.18f * __expf(-(a * a) / (16.f * sg * sg));
        const float3 cc = sp_mix3(hj_pal(pal, drift + 0.12f), make_float3(1.f, 1.f, 1.f), 0.55f);
        col = sp_add3(col, sp_mul3(cc, core * g));
    }
    // the odd planet: lit sphere with palette bands, atmosphere rim and an optional ring
    const float pw = B[40];
    if (pw > 1e-4f) {
        const float3 C = make_float3(B[41], B[42], B[43]);
        const float Rp = B[44];
        const float3 Ax = make_float3(B[45], B[46], B[47]);
        const float il = rsqrtf(sp_dot3(d, d));
        const float3 dn = sp_mul3(d, il);
        const float tc = sp_dot3(C, dn);
        const float h2 = fmaxf(sp_dot3(C, C) - tc * tc, 0.f);
        const float pxw = fmaxf(tc, 0.05f) * il / f;            // world size of a pixel at the planet
        float cov = 0.f, thit = 1e30f;
        float3 pc = make_float3(0.f, 0.f, 0.f);
        if (tc > 0.f) {
            const float hh = sqrtf(h2);
            cov = sp_clamp01((Rp - hh) / pxw + 0.5f);
            if (cov > 0.f) {
                const float hc = fminf(hh, Rp * 0.9999f);
                thit = tc - sqrtf(Rp * Rp - hc * hc);
                const float3 N = sp_mul3(sp_sub3(sp_mul3(dn, thit), C), 1.f / Rp);
                const float lat = sp_dot3(N, Ax);
                const float nb = hj_vn(N.x * 2.6f + B[51], N.y * 2.6f, N.z * 2.6f + 3.1f, 1 << 20, seed + 4242u);
                const float bi = B[48] + 0.22f * __sinf(lat * B[49] + 2.6f * nb) + 0.18f * nb;
                const float3 alb = hj_pal(pal, bi * period + drift + 0.3f);
                const float3 Ld = make_float3(0.7462f, 0.4477f, -0.4925f);
                const float ndl = sp_dot3(N, Ld);
                const float diff = fmaxf(ndl, 0.f);
                const float mu = fmaxf(-sp_dot3(N, dn), 0.f);
                float rim = 1.f - mu; rim = rim * rim * rim;
                rim *= 0.2f + 0.8f * sp_smooth(-0.35f, 0.5f, ndl);
                const float3 atm = sp_mix3(hj_pal(pal, drift + 0.68f), make_float3(1.f, 1.f, 1.f), 0.3f);
                pc = sp_add3(sp_mul3(alb, 0.012f + 1.1f * diff), sp_mul3(atm, 0.9f * rim));
            }
        }
        float ra = 0.f;
        bool rfront = true;
        float3 rc = make_float3(0.f, 0.f, 0.f);
        if (B[50] > 0.f) {
            const float den = sp_dot3(dn, Ax);
            if (fabsf(den) > 1e-4f) {
                const float tr = sp_dot3(C, Ax) / den;
                if (tr > 0.f) {
                    const float3 Q = sp_sub3(sp_mul3(dn, tr), C);
                    const float rr = sqrtf(sp_dot3(Q, Q)) / Rp;
                    if (rr > 1.3f && rr < 2.4f) {
                        const float nr = hj_vn(rr * 11.f, 0.5f, 0.5f, 1 << 20, seed + 777u);
                        const float bandv = 0.45f + 0.55f * __sinf(rr * 27.f + 4.f * nr);
                        ra = B[50] * fmaxf(bandv, 0.f) * sp_smooth(1.3f, 1.45f, rr) * (1.f - sp_smooth(2.15f, 2.4f, rr));
                        rc = sp_mul3(hj_pal(pal, (0.2f * rr + B[48]) * period + drift + 0.15f), 0.55f);
                        rfront = !(cov > 0.f && tr > thit);
                    }
                }
            }
        }
        if (!rfront) col = sp_mix3(col, rc, ra * pw);
        col = sp_mix3(col, pc, cov * pw);
        if (rfront) col = sp_mix3(col, rc, ra * pw);
    }
    const size_t plane = (size_t)W * H, o = (size_t)y * W + x;
    acc[o] = col.x; acc[plane + o] = col.y; acc[2 * plane + o] = col.z;
}
"""


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _hu(x: int) -> int:
    x &= 0xFFFFFFFF
    x ^= x >> 16
    x = (x * 0x7FEB352D) & 0xFFFFFFFF
    x ^= x >> 15
    x = (x * 0x846CA68B) & 0xFFFFFFFF
    x ^= x >> 16
    return x


def _hf(k: int, seed: int, j: int) -> float:
    """Deterministic host hash -> [0, 1) (planet slot k, attribute j)."""
    return _hu((k * 0x9E3779B1) ^ _hu(seed * 0x85EBCA77 + j * 0x27D4EB2D)) / 4294967296.0


class HyperspaceJump(Source):
    name = "hyperspace_jump"
    label = "Hyperspace jump"
    description = ("Warp-speed starfield: 3D motion streaks with chromatic Doppler, nebula wisps and the odd planet; "
                   "high warp morphs it into a spiralling hyperspace tunnel.")
    tags = {"family": "cosmic", "dims": "3d", "toolkit": "splat3d", "technique": ["points", "particles"],
            "moods": ["euphoric", "cosmic", "aggressive"], "style": "wild", "energy": [0.5, 1.0]}
    params = {
        **standard_scene_params(palette="ice", render_scale=0.5, period=1.0, drift=0.02),
        "warp_factor": num(0.0, 1.0, 0.55, "Warp factor", tier="regional", basic=True),
        "star_density": num(0.1, 1.0, 0.6, "Star density", tier="regional"),
        "tunnel_radius": num(0.15, 3.0, 0.6, "Tunnel radius", tier="regional"),
        "twist": num(0.0, 1.0, 0.4, "Spiral twist (roll)", tier="regional", basic=True),
        "doppler_strength": num(0.0, 1.0, 0.65, "Doppler colour", tier="regional"),
        "nebula": num(0.0, 1.5, 0.6, "Nebula wisps", tier="regional"),
        "planets": num(0.0, 1.0, 0.5, "Planets", tier="regional"),
        "glow": num(0.0, 2.0, 0.8, "Bloom", tier="regional"),
        "roll_speed": num(-3.0, 3.0, 1.0, "Roll speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        ns = N_MAX * SEGS
        self.sp = Splat3D(ctx, self.w, self.h, max_points=N_MAX, max_lines=ns, pool=3_000_000)
        self.p0 = cp.zeros((ns, 4), cp.float32)
        self.p1 = cp.zeros((ns, 4), cp.float32)
        self.rgb = cp.zeros((ns, 3), cp.float32)
        self.hd = cp.zeros((N_MAX, 4), cp.float32)
        self.hrgb = cp.zeros((N_MAX, 3), cp.float32)
        self.U = cp.zeros(32, cp.float32)
        self.B = cp.zeros(64, cp.float32)
        self.Uh = np.zeros(32, np.float32)
        self.Bh = np.zeros(64, np.float32)
        mod = raw_module(_SRC, SPLAT3D_OPTIONS)
        self.k_stars = mod.get_function("hj_stars")
        self.k_bg = mod.get_function("hj_bg")

        self.zph = 0.0           # travel phase (fraction of the wrap range)
        self.roll = 0.0          # field roll (rad)
        self.phz = 0.0           # nebula travel phase (noise units, periodic 64)
        self.zp = PLANET_SPAWN - 42.0   # planet travel: the first planet starts in view at depth ~42
        self.steer = 0.0
        self.drift = 0.0
        self.e_s = None
        self.l_slow = None

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.1)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        a = 1.0 - math.exp(-dt / 0.35) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s
        D = DEPTH

        # ---- warp: W from the knob and energy; jump morph j
        W = min(1.45, float(p["warp_factor"]) * (0.55 + 0.9 * es))
        j = _smooth(0.62, 1.12, W)
        twist = float(p["twist"])
        L = D * min(0.93, 0.005 + 0.4 * W * W + 0.45 * j)          # streak length (world)
        al = 1.0 - math.exp(-dt / 1.0) if dt > 0 else 1.0
        self.l_slow = L if self.l_slow is None else self.l_slow + (L - self.l_slow) * al
        zlo = -0.6 - 0.92 * self.l_slow                              # wrap behind the camera, deeper for long streaks
        Dr = D - zlo

        # ---- rates (pulse kicks rates only): forward surge, roll, steering, colour drift
        v = D * spd * (0.03 + 0.55 * W * W + 0.35 * j) + D * spd * 0.4 * pulse * (0.5 + 0.5 * e)
        self.zph = (self.zph + v * dt / Dr) % 1.0
        omega = float(p["roll_speed"]) * spd * (0.03 + 0.2 * e + twist * (0.12 + 1.1 * j))
        self.roll = (self.roll + omega * dt) % TAU
        self.steer = (self.steer + spd * (0.07 + 0.1 * e) * dt) % (TAU * 100.0)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)
        twk = twist * (0.1 + 0.9 * j) * TAU * 1.2 / D
        rpl = omega / max(v, 0.5)
        if rpl * L > 0.6:
            rpl = 0.6 / L
        fxy = 0.55
        fz = fxy / (1.0 + 5.0 * j + 1.2 * W)
        self.phz = (self.phz + v * fz * dt) % 64.0
        self.zp += PLANET_RATE * v * dt

        # ---- camera: gentle steering of the view axis (the vanishing point wanders)
        ys = (0.045 + 0.05 * es) * math.sin(self.steer)
        ps = (0.03 + 0.03 * es) * math.sin(0.71 * self.steer + 1.1)
        cam = look_at((0.0, 0.0, 0.0), (math.tan(ys), math.tan(ps), 1.0), fov_deg=FOV, near=0.05)

        sess = self.session_palette()
        coeffs = np.asarray(PALETTES[p["palette"]] if sess is None else sess, np.float64).reshape(-1)[:12]
        period = float(p["palette_period"])
        dop = float(p["doppler_strength"]) * (0.45 + 0.55 * min(W, 1.0))
        r_in = float(p["tunnel_radius"])
        dens = float(p["star_density"])
        n_f = min(float(N_MAX), dens * N_BASE * Dr / (D + 0.6))
        n = min(N_MAX, int(math.ceil(n_f + 0.04 * N_MAX)))

        # ---- star uniforms
        U = self.Uh
        U[0:16] = (self.zph, Dr, zlo, r_in, max(R_OUT, r_in + 2.0), L, self.roll, twk, rpl, n_f,
                   self.drift, period, dop, j, 1.0 - 0.55 * j, 0.35)
        U[16:28] = coeffs
        U[28] = D
        U[29] = 0.4 + 0.1 * L
        self.U.set(U)

        # ---- background uniforms
        B = self.Bh
        B[:] = 0.0
        f = 0.5 * self.h / cam.tan_half
        B[0:3] = (f, 0.5 * self.w, 0.5 * self.h)
        B[3:6] = cam.right
        B[6:9] = cam.up
        B[9:12] = cam.fwd
        B[12:22] = (self.phz, self.roll, twk, fxy, fz, float(p["nebula"]) * (0.75 + 0.5 * e), 9.0 + 4.0 * j,
                    (0.02 + 0.05 * es + 0.4 * j) * (1.0 + 0.5 * pulse), self.drift, period)
        B[22:34] = coeffs
        B[34:39] = (j, pulse, dop, 1.4 + r_in, 3.8 + r_in)
        self._planet(B, float(p["planets"]), r_in, twk)
        self.B.set(B)

        sp = self.sp
        sp.clear()
        launch_2d(self.k_bg, self.w, self.h, (sp.acc, np.int32(self.w), np.int32(self.h), self.B,
                                              np.uint32(self.seed)))
        self.k_stars(((n + 255) // 256,), (256,), (self.U, self.p0, self.p1, self.rgb, self.hd, self.hrgb,
                                                    np.int32(n), np.int32(N_MAX), np.uint32(self.seed)))
        far = (0.62 * D, 0.97 * D)
        gain = (0.5 + 0.5 * e) * (1.0 + 0.6 * pulse)
        sp.lines(self.p0, self.p1, n=n * SEGS, cam=cam, rgb=self.rgb, weight_in_w=True,
                 intensity=0.3 * gain / (1.0 + 1.2 * j), width_px=1.5, size_range=(0.6, 3.0), depth_ref=5.0,
                 far_fade=far, fog=0.012)
        sp.points(self.hd, n=n, cam=cam, rgb=self.hrgb, weight_in_w=True, intensity=0.45 * gain, size_px=1.5,
                  size_range=(0.7, 4.0), depth_ref=6.0, far_fade=far, fog=0.012)
        sp.resolve(out, exposure=1.05 * float(p["intensity"]) * (1.0 + 0.25 * pulse), tonemap="aces",
                   saturation=1.12, glow=float(p["glow"]) * (0.45 + 0.3 * e + 0.35 * j) * (1.0 + 0.4 * pulse),
                   glow_radius_px=18.0, glow_wide=0.5)

    def _planet(self, B: np.ndarray, amount: float, r_in: float, twk: float) -> None:
        """Planet slot from the integrated planet travel: deterministic per slot, fades in far away."""
        if amount <= 0.0:
            return
        k = int(self.zp // PLANET_SLOT)
        z = PLANET_SPAWN - (self.zp - k * PLANET_SLOT)
        if z < -12.0:
            return
        if k > 0 and _hf(k, self.seed, 0) >= amount:
            return
        pw = _smooth(0.0, 0.25, amount) * (1.0 - _smooth(0.76 * PLANET_SPAWN, PLANET_SPAWN, z))
        if pw <= 1e-4:
            return
        Rp = 2.4 + 3.6 * _hf(k, self.seed, 1)
        dpl = Rp + max(2.0, 1.5 * r_in) + 5.0 * _hf(k, self.seed, 2)
        ang = TAU * _hf(k, self.seed, 3) + self.roll + twk * z
        t1 = 0.25 + 0.6 * _hf(k, self.seed, 4)
        t2 = TAU * _hf(k, self.seed, 5)
        B[40] = pw
        B[41:44] = (dpl * math.cos(ang), dpl * math.sin(ang), z)
        B[44] = Rp
        B[45:48] = (math.sin(t1) * math.cos(t2), math.cos(t1), math.sin(t1) * math.sin(t2))
        B[48] = _hf(k, self.seed, 6)
        B[49] = 6.0 + 8.0 * _hf(k, self.seed, 7)
        B[50] = 0.85 if _hf(k, self.seed, 8) < 0.5 or k == 0 else 0.0
        B[51] = 37.0 * _hf(k, self.seed, 9)

    def close(self) -> None:
        if getattr(self, "sp", None) is not None:
            self.sp.close()
        self.sp = self.p0 = self.p1 = self.rgb = self.hd = self.hrgb = self.U = self.B = None
