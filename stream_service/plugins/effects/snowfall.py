"""Snowfall: multi-layer parallax snow over the picture.  Up to 8 depth layers of hashed-cell flakes with depth of
field (near flakes big and soft like bokeh, far ones small and sharp), wind sway from an integrated phase, flakes lit
by the underlying image's luma, optional frost creeping in at the frame edges and a slight cool grade.

Source: Original (inspired by https://www.shadertoy.com/view/ldsGDn)
Author: smfx (original; Shadertoy 'Just snow' and godotshaders 'let it snow' are ideas only; the latter's CC0 label is untrusted)
License: MIT (original)
license_class: permissive

Technique: one kernel; per layer a single jittered-cell lookup (one flake per cell, column-staggered rows, presence
by hash), so the cost is ~3 integer hashes per layer per pixel.  Fall and wind offsets are integrated on the host
per layer and split into an integer cell offset plus a fraction, so positions stay exact for days of runtime.
Layer slots have fixed depths in an interleaved order (near, far, mid, ...) and the continuous `layers` knob fades
the extra slots in and out, so it never re-grids.  Flakes are lighten-blended (they never darken the picture), the
cool grade and frost are applied underneath.

Realtime: amount -> flake density, flake alpha, frost reach and cool grade all scale with m = smoothstep(amount)
(amount 0 copies src exactly); pulse -> a gust (a kick on the integrated wind / fall / sway velocities, never on
positions) plus a sparkle on a random subset of flakes; speed -> every motion rate; drift_speed -> palette drift.
Regional: density (basic), flake_size, wind, depth_of_field, layers (3-8, continuous fade), frost, cool, tint,
gust, sparkle, palette, palette_period.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

NSL = 8                       # layer slots
SLOT_DEPTH = (0.0, 1.0, 0.5, 0.25, 0.75, 0.125, 0.625, 0.375)   # fade-in order: near, far, mid, ...
NG = 32                       # global floats
LS = 16                       # floats per layer
NPRM = NG + NSL * LS
WRAP = 1 << 22                # integer cell offsets wrap here (one reshuffle every few weeks)
TAU = 2.0 * math.pi
# Globals: 0..11 palette | 12 drift | 13 period | 14 tint | 15 cool | 16 frost reach | 17 frost strength
#          18 sparkle gain | 19 seed | 20 lit lo | 21 lit gain | 22 frost creep wobble
# Layer:   0 weight | 1 cells/height | 2 fall frac | 3 fall int | 4 wind frac | 5 wind int | 6 sway phase
#          7 sway amp | 8 radius | 9 blur | 10 alpha | 11 presence | 12 depth | 13 bokeh | 14 - | 15 slot id

_SRC = HELPERS + PALETTE_CUDA + r"""
#define NSL 8
#define NG 32
#define LS 16

extern "C" __global__
void snowfall(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
              const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const uchar4 s = src[y * W + x];
    float3 c = make_float3(s.x * (1.f / 255.f), s.y * (1.f / 255.f), s.z * (1.f / 255.f));
    const float invH = 1.f / (float)H;
    const float u = (x + 0.5f) * invH, v = (y + 0.5f) * invH;
    const unsigned int seed = (unsigned int)P[19];

    // --- slight cool grade (scaled by m on the host) ---
    const float cool = P[15];
    if (cool > 0.f) {
        const float g = 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z;
        const float3 t = make_float3((0.82f * c.x + 0.18f * g) * 0.92f,
                                     (0.82f * c.y + 0.18f * g) * 0.99f,
                                     fminf((0.82f * c.z + 0.18f * g) * 1.08f + 0.02f, 1.f));
        c.x += (t.x - c.x) * cool; c.y += (t.y - c.y) * cool; c.z += (t.z - c.z) * cool;
    }

    // --- frost creeping in from the frame edges ---
    const float reach = P[16];
    if (reach > 1e-4f) {
        const float A = (float)W * invH;
        const float e = fminf(fminf(u, A - u), fminf(v, 1.f - v));
        if (e < reach * 1.7f + 0.08f) {
            const float wob = (fbm(u * 4.5f, v * 4.5f, seed + 101u) - 0.5f) * (0.10f + 0.6f * reach) + P[22];
            const float ee = e + wob;
            const float n = vnoise(u * 46.f, v * 46.f, seed + 202u);
            float ridge = 1.f - fabsf(2.f * n - 1.f);
            ridge = ridge * ridge; ridge = ridge * ridge * ridge;                 // thin crystalline veins
            const float n2 = vnoise(u * 160.f, v * 160.f, seed + 303u);          // fine ice grain
            const float body = smooth(reach, reach * 0.2f, ee);
            const float veins = smooth(reach * 1.7f, reach * 0.4f, ee) * ridge;
            const float k = clamp01((body * body * (0.62f + 0.25f * ridge) + 0.55f * veins) * P[17]);
            const float b = 0.74f + 0.28f * ridge + 0.12f * n2;
            const float3 f3 = make_float3(0.84f * b, 0.92f * b, fminf(1.f * b, 1.f));
            const float g = 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z;
            const float3 dsat = make_float3(0.5f * (c.x + g), 0.5f * (c.y + g), 0.5f * (c.z + g));
            c.x += (fmaxf(dsat.x, f3.x) - c.x) * k;
            c.y += (fmaxf(dsat.y, f3.y) - c.y) * k;
            c.z += (fmaxf(dsat.z, f3.z) - c.z) * k;
        }
    }

    // --- flakes, far layers first ---
    const float L = 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z;
    const float lit = P[20] + P[21] * L;
    const float tint = P[14], spkG = P[18];
    #pragma unroll 1
    for (int k = 0; k < NSL; ++k) {
        const float* __restrict__ Q = P + NG + LS * k;
        const float w = Q[0];
        if (w <= 0.f) continue;
        const float N = Q[1];
        const unsigned int slot = (unsigned int)Q[15];
        const float gx = u * N - Q[4];
        const float flx = floorf(gx);
        const int colI = (int)flx - (int)Q[5];
        const float fx = gx - flx;
        const unsigned int hc = hash_u((unsigned int)colI * 0x9E3779B1u ^ hash_u(slot * 0x632BE5ABu + seed));
        const float stag = (hc & 0xFFFFu) * (1.f / 65536.f);
        const float gy = v * N - Q[2] + stag;
        const float fly = floorf(gy);
        const int rowI = (int)fly - (int)Q[3];
        const float fy = gy - fly;
        const unsigned int h = hash_u((unsigned int)rowI * 0x85EBCA77u ^ hc);
        const float h0 = (h & 0xFFFFu) * (1.f / 65536.f);
        if (h0 >= Q[11]) continue;                                            // empty cell
        const float h1 = (h >> 16) * (1.f / 65536.f);
        const unsigned int hb = hash_u(h ^ 0x27d4eb2du);
        const float h2 = (hb & 0xFFFFu) * (1.f / 65536.f), h3 = (hb >> 16) * (1.f / 65536.f);
        const unsigned int hd = hash_u(hb + 0x165667B1u);
        const float h4 = (hd & 0xFFFFu) * (1.f / 65536.f), h5 = (hd >> 16) * (1.f / 65536.f);

        const float rr = Q[8] * (0.62f + 0.38f * h3);
        const float aa = fmaxf(Q[9] * rr, 1.6f * N * invH);
        const float amp = Q[7];
        const float exy = fminf(rr + 0.5f * aa, 0.5f);
        const float jx = fmaxf(1.f - 2.f * fminf(exy + amp, 0.5f), 0.f);
        const float jy = fmaxf(1.f - 2.f * exy, 0.f);
        const float ph = Q[6] + 6.2831853f * h4;
        const float px = 0.5f + (h1 - 0.5f) * jx + amp * __sinf(ph);
        const float py = 0.5f + (h2 - 0.5f) * jy;
        const float dx = fx - px, dy = fy - py;
        const float d = sqrtf(dx * dx + dy * dy);
        float a = smooth(rr + 0.5f * aa, rr - 0.5f * aa, d);
        if (a <= 0.f) continue;
        const float bok = Q[13];
        a *= 1.f - 0.3f * bok * (1.f - smooth(rr * 0.45f, rr, d));          // bokeh: brighter rim, softer centre
        const float twk = 0.86f + 0.14f * __sinf(2.f * Q[6] + 6.2831853f * h5);
        const float spk = (h5 > 0.5f) ? spkG * (h5 - 0.5f) * 2.f : 0.f;
        const float3 pc = pal(Q[12] * P[13] + P[12] + 0.3f * h3, P);
        const float br = lit * twk * (1.f + spk);
        const float3 fc = make_float3((1.f + (pc.x - 1.f) * tint) * br, (1.f + (pc.y - 1.f) * tint) * br,
                                      (1.f + (pc.z - 1.f) * tint) * br);
        a = clamp01(a * Q[10] * w * (1.f + 0.6f * spk));
        c.x += (fmaxf(fc.x, c.x) - c.x) * a;
        c.y += (fmaxf(fc.y, c.y) - c.y) * a;
        c.z += (fmaxf(fc.z, c.z) - c.z) * a;
    }
    out[y * W + x] = make_uchar4((unsigned char)(clamp01(c.x) * 255.f + 0.5f), (unsigned char)(clamp01(c.y) * 255.f + 0.5f),
                                 (unsigned char)(clamp01(c.z) * 255.f + 0.5f), 255);
}
"""


def _smoothstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


class Snowfall(Effect):
    name = "snowfall"
    label = "Snowfall"
    description = ("Multi-layer parallax snow with bokeh depth of field, wind sway, luma-lit flakes, optional edge "
                   "frost and a cool grade; gusts and sparkles on the beat.")
    stage = "overlay"
    params = {
        **standard_effect_params(),
        **palette_params("ice", 0.6, 0.02),
        "speed": num(0.0, 4.0, 1.0, "Speed", unit="×", tier="realtime", bind="rate"),
        "density": num(0.0, 1.0, 0.55, "Density", tier="regional", basic=True),
        "flake_size": num(0.5, 1.6, 1.0, "Flake size", tier="regional"),
        "wind": num(-1.0, 1.0, 0.15, "Wind", tier="regional"),
        "depth_of_field": num(0.0, 1.0, 0.65, "Depth of field (bokeh)", tier="regional"),
        "layers": num(3.0, 8.0, 6.0, "Layers", step=0.05, tier="regional"),
        "frost": num(0.0, 1.0, 0.25, "Edge frost", tier="regional"),
        "cool": num(0.0, 1.0, 0.3, "Cool grade", tier="regional"),
        "tint": num(0.0, 1.0, 0.22, "Palette tint on flakes", tier="regional"),
        "gust": num(0.0, 2.0, 1.0, "Beat gust", tier="regional"),
        "sparkle": num(0.0, 2.0, 1.0, "Beat sparkle", tier="regional"),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("snowfall")
        self._prm = cp.zeros(NPRM, dtype=cp.float32)
        self._h = np.zeros(NPRM, dtype=np.float32)
        rng = np.random.default_rng(int(params.get("seed", 1) or 1))
        self._seed = float(rng.integers(0, 1 << 20))
        self._fall_i = [int(v) for v in rng.integers(0, 1 << 16, NSL)]
        self._fall_f = [0.0] * NSL
        self._wind_i = [int(v) for v in rng.integers(0, 1 << 16, NSL)]
        self._wind_f = [0.0] * NSL
        self._sway = [float(v) for v in rng.uniform(0.0, TAU, NSL)]
        self._drift = 0.0
        self._creep = 0.0
        self._pal_name = None
        # far layers drawn first: pack slots by descending depth
        self._order = sorted(range(NSL), key=lambda s: -SLOT_DEPTH[s])

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.1)
        amount = float(p["amount"])
        m = _smoothstep(amount)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        gk = float(p["gust"]) * pulse * m
        wind = float(p["wind"])
        sgn = 1.0 if wind >= 0.0 else -1.0

        # --- host integration (always, so motion carries on through amount fades) ---
        self._drift = (self._drift + float(p["drift_speed"]) * spd * dt) % 1.0
        self._creep = (self._creep + 0.12 * spd * dt) % TAU
        for s in range(NSL):
            d = SLOT_DEPTH[s]
            N = 2.6 + 23.4 * d ** 1.3
            par = 1.0 - 0.65 * d                                         # parallax: near moves faster on screen
            vfall = (0.06 + 0.24 * (1.0 - d)) * (1.0 + 0.5 * gk)         # screen heights / s
            vwind = (0.30 * wind + 0.45 * sgn * gk) * par
            f = self._fall_f[s] + vfall * N * spd * dt
            fi = math.floor(f)
            self._fall_f[s] = f - fi
            self._fall_i[s] = (self._fall_i[s] + int(fi)) % WRAP
            g = self._wind_f[s] + vwind * N * spd * dt
            gi = math.floor(g)
            self._wind_f[s] = g - gi
            self._wind_i[s] = (self._wind_i[s] + int(gi)) % WRAP
            self._sway[s] = (self._sway[s] + (0.8 + 0.8 * (1.0 - d)) * spd * (1.0 + 1.2 * gk) * dt) % TAU

        if amount <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return

        ma = _smoothstep(min(1.0, amount * 2.0))      # alpha: full by amount 0.5
        md = m ** 0.6                                  # density
        h = self._h
        if self._pal_name != p["palette"]:
            self._pal_name = p["palette"]
            h[:12] = palette_coeffs(p["palette"])
        h[12] = self._drift
        h[13] = p["palette_period"]
        h[14] = p["tint"]
        h[15] = 0.85 * float(p["cool"]) * m
        fr = float(p["frost"]) * m
        h[16] = 0.20 * fr
        h[17] = min(1.0, 0.4 + 1.2 * fr)
        h[18] = 1.6 * float(p["sparkle"]) * pulse * m
        h[19] = self._seed
        h[20] = 0.55
        h[21] = 0.65
        h[22] = 0.012 * fr * math.sin(self._creep)

        dof = float(p["depth_of_field"])
        dens = 0.08 + 0.82 * float(p["density"])
        size = float(p["flake_size"])
        layers = float(p["layers"])
        for j, s in enumerate(self._order):
            d = SLOT_DEPTH[s]
            o = NG + LS * j
            bok = dof * (1.0 - d) ** 2
            N = 2.6 + 23.4 * d ** 1.3
            amp = 0.04 + 0.08 * (1.0 - d)
            rr = (0.055 + 0.035 * (1.0 - d)) * size * (1.0 + 0.9 * bok)
            blur = 0.12 + 0.8 * bok
            ext = rr * (1.0 + 0.5 * blur) + amp
            if ext > 0.49:                                 # keep the flake inside its cell
                k = 0.49 / ext
                rr *= k
                amp *= k
            h[o + 0] = min(max(layers - s, 0.0), 1.0)
            h[o + 1] = N
            h[o + 2] = self._fall_f[s]
            h[o + 3] = self._fall_i[s]
            h[o + 4] = self._wind_f[s]
            h[o + 5] = self._wind_i[s]
            h[o + 6] = self._sway[s]
            h[o + 7] = amp
            h[o + 8] = rr
            h[o + 9] = blur
            h[o + 10] = ma * (1.0 - 0.5 * bok) * (1.0 - 0.3 * d)
            h[o + 11] = dens * (0.4 + 0.6 * d) * md
            h[o + 12] = d
            h[o + 13] = bok
            h[o + 15] = s
        self._prm.set(h)
        W, H = self.ctx.width, self.ctx.height
        launch_2d(self._k, W, H, (src, out, np.int32(W), np.int32(H), self._prm))
