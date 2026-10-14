"""Black hole: a Schwarzschild black hole with a thin accretion disc, lensed stars, a galaxy band and a photon ring.

Source: https://github.com/ebruneton/black_hole_shader ; https://ebruneton.github.io/black_hole_shader/
Author: Eric Bruneton (black_hole_shader); smfx (CUDA reimplementation)
License: BSD-3-Clause
license_class: permissive
Ported from GLSL by smfx (ideas/changes: Bruneton's precomputed "deflection" and "inverse radius" lookup tables
        for light rays around a Schwarzschild black hole, his Doppler/gravitational shift of a blackbody accretion
        disc and his lensed-background approach, re-derived and written for CUDA: the tables are integrated on the
        GPU (RK4 on the Binet equation u'' + u = 3Mu^2, one thread per ray angle, quadratic sampling densest at the
        shadow edge) and rebuilt only when the camera distance changes; procedural star layers, nebula and galaxy
        band instead of textures; a two-layer flow-noise turbulent disc with Keplerian shear; palette morph; a
        stylised photon-ring glow; realtime energy/pulse/speed macros.)

Copyright (c) 2020 Eric Bruneton
All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the
following conditions are met:
1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following
   disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following
   disclaimer in the documentation and/or other materials provided with the distribution.
3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote
   products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

Units: G = c = M = 1 (horizon r = 2, photon sphere r = 3, ISCO r = 6, critical impact parameter 3*sqrt(3)).
Every camera ray lies in the plane through the hole, the camera and the ray, so its path depends only on the angle
delta between the ray and the direction to the hole.  Two tables over delta: (1) where the ray ends (escape angle,
or captured), (2) the inverse radius u(phi) along the path.  Per pixel: two table lookups for the escape direction
(lensed background), and up to four disc-plane crossings (phi = phi0 + k*pi) whose radius comes from table (2).

Macros: energy -> disc rotation rate, disc turbulence and temperature, camera orbit rate and sway, exposure.
pulse -> disc flare (hot spots brighten), photon-ring glow kick, a kick to the rotation rate.  speed scales the
disc and orbit rates; drift moves the nebula, galaxy and star palette (and the disc when disc_palette > 0).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, wrap
from stream_service.plugins._lib.upsample import internal_size, present

ND = 2048                    # ray-angle samples (quadratic, densest at the shadow edge)
NP = 512                     # phi samples along each ray
PHIMAX = 4.0 * math.pi       # rays still orbiting after two turns count as captured
BC = 3.0 * math.sqrt(3.0)    # critical impact parameter
R_IN = 6.0                   # ISCO
RC_QUANT = 0.05              # camera distance quantum for table rebuilds
FOV = 42.0

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 exposure | 28 rc | 29 delta_c | 30 r_in | 31 r_out
#  32 T_max | 33 beaming | 34 disc palette mix | 35 turbulence | 36 flow phase A | 37 flow phase B
#  38 seed A | 39 seed B | 40 shear per cycle | 41 flare gain | 42 ring gain | 43 star gain | 44..46 galaxy normal
#  47 noise seed | 48 sqrt(1 - 2/rc) | 49 opacity | 50 energy | 51 dphi | 52 disc gain
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define BH_ND %(nd)d
#define BH_NP %(np)d
#define BH_BC %(bc).7ff

// ---------------------------------------------------------------- table build: one thread per ray angle
extern "C" __global__
void bh_lut(float* __restrict__ invr, float2* __restrict__ endl, const float rc, const float dc, const float phimax)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= BH_ND) return;
    const float x = -1.f + 2.f * (float)i / (float)(BH_ND - 1);
    float del = x < 0.f ? dc - dc * x * x : dc + (SDF_PI - dc) * x * x;
    del = fminf(fmaxf(del, 1e-5f), SDF_PI - 1e-5f);
    const float sq = sqrtf(1.f - 2.f / rc);
    const float b = fmaxf(rc * sinf(del) / sq, 1e-4f);
    float u = 1.f / rc;
    float w = sqrtf(fmaxf(1.f / (b * b) - u * u * (1.f - 2.f * u), 0.f));
    if (cosf(del) < 0.f) w = -w;                                  // pointing away from the hole: u decreasing
    const float dphi = phimax / (float)(BH_NP - 1);
    float phi = 0.f, phend = phimax, cap = 1.f;
    int state = 0;                                                // 0 alive, 1 escaped, 2 captured
    float* row = invr + (size_t)i * BH_NP;
    for (int j = 0; j < BH_NP; ++j) {
        row[j] = state == 0 ? u : (state == 1 ? -1.f : 1.f);
        if (state != 0 || j == BH_NP - 1) continue;
        const int n = 6 + min((int)(dphi * fabsf(w) / 0.003f), 600);
        const float h = dphi / (float)n;
        for (int k = 0; k < n; ++k) {
            // RK4 on (u, w): u' = w, w' = 3u^2 - u
            const float k1u = w,                   k1w = 3.f * u * u - u;
            const float u2 = u + 0.5f * h * k1u,   w2 = w + 0.5f * h * k1w;
            const float k2u = w2,                  k2w = 3.f * u2 * u2 - u2;
            const float u3 = u + 0.5f * h * k2u,   w3 = w + 0.5f * h * k2w;
            const float k3u = w3,                  k3w = 3.f * u3 * u3 - u3;
            const float u4 = u + h * k3u,          w4 = w + h * k3w;
            const float k4u = w4,                  k4w = 3.f * u4 * u4 - u4;
            const float un = u + h * (k1u + 2.f * k2u + 2.f * k3u + k4u) * (1.f / 6.f);
            const float wn = w + h * (k1w + 2.f * k2w + 2.f * k3w + k4w) * (1.f / 6.f);
            if (un <= 0.f) { phend = phi + h * u / fmaxf(u - un, 1e-12f); cap = 0.f; state = 1; break; }
            if (un >= 0.5f) { phend = phi + h; cap = 1.f; state = 2; break; }
            u = un; w = wn; phi += h;
        }
    }
    endl[i] = make_float2(phend, cap);
}

// ---------------------------------------------------------------- colour helpers
// Planckian locus (Kim et al. cubic fit, 1667..25000 K) -> linear sRGB, max component 1.
__device__ float3 bb_rgb(float T) {
    const float Tc = fminf(fmaxf(T, 1667.f), 25000.f);
    const float it = 1000.f / Tc, it2 = it * it, it3 = it2 * it;
    const float x = Tc <= 4000.f ? (-0.2661239f * it3 - 0.2343589f * it2 + 0.8776956f * it + 0.179910f)
                                 : (-3.0258469f * it3 + 2.1070379f * it2 + 0.2226347f * it + 0.240390f);
    const float x2 = x * x, x3 = x2 * x;
    const float y = Tc <= 2222.f ? (-1.1063814f * x3 - 1.34811020f * x2 + 2.18555832f * x - 0.20219683f)
                  : Tc <= 4000.f ? (-0.9549476f * x3 - 1.37418593f * x2 + 2.09137015f * x - 0.16748867f)
                                 : (3.0817580f * x3 - 5.87338670f * x2 + 3.75112997f * x - 0.37001483f);
    const float X = x / y, Z = (1.f - x - y) / y;
    float3 c = f3(3.2406f * X - 1.5372f - 0.4986f * Z, -0.9689f * X + 1.8758f + 0.0415f * Z,
                  0.0557f * X - 0.2040f + 1.0570f * Z);
    c = max(c, 0.f);
    c = c / fmaxf(vmax(c), 1e-4f);
    if (T < 1667.f) c = mix(f3(1.f, 0.16f, 0.f), c, smoothstep(900.f, 1667.f, T));
    return c;
}

// ---------------------------------------------------------------- background (direction at infinity)
__device__ float3 star_layer(float3 v, float S, float seedo, float fpix, float dens, const float* __restrict__ P) {
    const float3 q = v * S;
    const float3 c = floor(q);
    if (hash13(c + f3(seedo + 7.f)) > dens) return f3(0.f);
    const float3 h = hash33(c + f3(seedo));
    const float3 sp = c + f3(0.25f) + h * 0.5f;                  // the 0.25-ball around sp stays inside the cell
    const float d2 = dot2(q - sp);
    const float r0 = 0.045f + 0.05f * h.x;
    const float rr = fminf(fmaxf(r0, fpix), 0.1f);
    const float b = fract(h.y * 7.31f + h.z * 3.17f);
    const float br = b * b * b * b * b * b * b * 7.f + 0.12f;
    const float I = br * (r0 * r0) / (rr * rr) * expf(-d2 / (rr * rr));
    if (I < 1e-4f) return f3(0.f);
    const float3 tint = srgb_to_linear(pal(h.z * P[25] + P[26] + 0.3f, P + 13));
    return mix(bb_rgb(2600.f + 10000.f * h.z), tint, 0.3f) * I;
}

__device__ float3 background(float3 v, const float* __restrict__ P, float pix) {
    const unsigned int sd = (unsigned int)P[47];
    const float drift = P[26], period = P[25];
    const float* PAL = P + 13;
    // nebula: soft palette clouds
    const float n1 = fbm3(v * 1.8f + f3(11.3f, 4.1f, -7.7f), 4, sd);
    const float neb = smoothstep(0.42f, 0.85f, n1);
    float3 col = srgb_to_linear(pal(n1 * 1.4f * period + drift, PAL)) * (neb * neb * 0.07f);
    // galaxy band along a great circle, with dust lanes
    const float gd = dot(v, load3(P + 44));
    const float band = expf(-gd * gd * 16.f);
    if (band > 0.01f) {
        const float n2 = fbm3(v * 4.5f + f3(-3.1f, 9.2f, 2.6f), 4, sd + 5u);
        const float dust = smoothstep(0.42f, 0.7f, fbm3(v * 9.f + f3(5.5f, -1.3f, 8.8f), 3, sd + 9u));
        const float3 gc = mix(f3(1.f, 0.86f, 0.72f), srgb_to_linear(pal(0.2f + n2 * 0.6f * period + drift + gd, PAL)), 0.55f);
        const float core = expf(-gd * gd * 90.f);
        col += gc * (band * (0.08f + 0.22f * n2 * n2) + core * 0.12f * n2) * (1.f - 0.85f * dust * band);
    }
    // stars: two layers
    col += star_layer(v, 70.f, 1000.f + (float)(sd & 1023u), pix * 70.f * 0.8f, 0.55f, P);
    col += star_layer(v, 170.f, 3000.f + (float)(sd & 1023u), pix * 170.f * 0.8f, 0.7f, P) * 0.45f;
    return col * P[43];
}

// ---------------------------------------------------------------- tables
__device__ __forceinline__ float lut_u(const float* __restrict__ invr, int i0, float fs, float ph, float dphi) {
    const float fp = ph / dphi;
    const int j0 = min(max((int)fp, 0), BH_NP - 2);
    const float fj = fminf(fmaxf(fp - (float)j0, 0.f), 1.f);
    const float* r0 = invr + (size_t)i0 * BH_NP + j0;
    const float* r1 = r0 + BH_NP;
    const float v[4] = {r0[0], r0[1], r1[0], r1[1]};
    const float wt[4] = {(1.f - fs) * (1.f - fj), (1.f - fs) * fj, fs * (1.f - fj), fs * fj};
    float s = 0.f, ws = 0.f;
    #pragma unroll
    for (int k = 0; k < 4; ++k) if (v[k] > 0.f && v[k] < 0.5f) { s += wt[k] * v[k]; ws += wt[k]; }
    return ws > 0.5f ? s / ws : -1.f;
}

// one flow-noise layer of the disc, in comoving coordinates (rotated back by the Keplerian angle)
__device__ __forceinline__ float disc_layer(float2 dxz, float lr, float ang, unsigned int seed) {
    float s, c;
    __sincosf(ang, &s, &c);
    const float rx = dxz.x * c - dxz.y * s, rz = dxz.x * s + dxz.y * c;
    return fbm3(f3(rx * 2.4f, lr * 7.5f, rz * 2.4f), 4, seed);
}

SDF_KERNEL
void black_hole(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
                const float* __restrict__ invr, const float2* __restrict__ endl)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);
    const float rc = P[28], dc = P[29], sq = P[48];
    const float* PAL = P + 13;

    // ray plane basis: e1 radial (hole -> camera), e2 the in-plane direction the ray turns towards
    const float3 e1 = normalize(ro);
    const float cd = -dot(rd, e1);
    float3 e2 = rd + e1 * cd;
    const float sd = length(e2);
    e2 = sd > 1e-6f ? e2 / sd : normalize(cross(e1, fabsf(e1.y) < 0.9f ? f3(0.f, 1.f, 0.f) : f3(1.f, 0.f, 0.f)));
    const float del = atan2f(sd, cd);
    const float b = rc * sd / sq;                                // impact parameter
    const float3 no = cross(e1, e2);                             // orbital-plane normal (backward ray)
    const float Ly = -b * no.y;                                  // photon angular momentum along the disc axis

    // table coordinate (quadratic in delta around delta_c)
    const float xx = del < dc ? -sqrtf(fmaxf(dc - del, 0.f) / dc) : sqrtf(fmaxf(del - dc, 0.f) / (SDF_PI - dc));
    const float s = (xx + 1.f) * 0.5f * (float)(BH_ND - 1);
    const int i0 = min(max((int)floorf(s), 0), BH_ND - 2);
    const float fs = fminf(fmaxf(s - (float)i0, 0.f), 1.f);
    const float2 E0 = endl[i0], E1 = endl[i0 + 1];
    const float cap = mix(E0.y, E1.y, fs);
    float phend;
    if (E0.y < 0.5f && E1.y < 0.5f) phend = mix(E0.x, E1.x, fs);
    else if (E0.y < 0.5f) phend = E0.x;
    else if (E1.y < 0.5f) phend = E1.x;
    else phend = mix(E0.x, E1.x, fs);
    const float phlim = fmaxf(E0.x, E1.x);

    const float rin = P[30], rout = P[31], Tmax = P[32], beam = P[33], dmix = P[34], turb = P[35];
    const float drift = P[26], period = P[25];
    const float wa = 1.f - fabsf(2.f * P[36] - 1.f), wb = 1.f - wa;
    const float wnorm = rsqrtf(fmaxf(wa * wa + wb * wb, 1e-4f));

    // ---- disc crossings, front to back
    float3 col = f3(0.f);
    float trans = 1.f;
    float ph0 = atan2f(-e1.y, e2.y);
    ph0 -= SDF_PI * floorf(ph0 / SDF_PI);
    if (ph0 < 1e-4f) ph0 += SDF_PI;
    for (int k = 0; k < 4; ++k) {
        const float ph = ph0 + SDF_PI * (float)k;
        if (ph >= phlim || trans < 0.02f) break;
        const float uu = lut_u(invr, i0, fs, ph, P[51]);
        if (uu <= 0.f) continue;
        const float r = 1.f / uu;
        if (r < rin - 0.8f || r > rout) continue;
        float sp, cpp;
        __sincosf(ph, &sp, &cpp);
        const float3 pos = (e1 * cpp + e2 * sp) * r;
        const float2 dxz = normalize(f2(pos.x, pos.z));
        const float lr = logf(r);
        const float om = powf(rin / r, 1.5f);
        const float na = disc_layer(dxz, lr, P[36] * P[40] * om, (unsigned int)P[38]);
        const float nb = disc_layer(dxz, lr, P[37] * P[40] * om, (unsigned int)P[39]);
        const float n = fminf(fmaxf(0.5f + ((wa * na + wb * nb) - 0.5f) * wnorm, 0.f), 1.f);
        // density: radial profile x turbulence
        const float prof = smoothstep(rin - 0.8f, rin + 0.6f, r) * (1.f - smoothstep(rin + (rout - rin) * 0.55f, rout, r));
        const float streak = smoothstep(0.18f, 0.82f, n);
        const float dens = prof * mix(1.f, 0.08f + 1.7f * streak, turb);
        const float alpha = 1.f - expf(-dens * P[49]);
        if (alpha < 1e-3f) continue;
        // Doppler + gravitational shift (circular Keplerian emitter, static observer at rc)
        const float Om = sqrtf(1.f / (r * r * r));
        const float g = sqrtf(fmaxf(1.f - 3.f / r, 0.05f)) / fmaxf(1.f - Om * Ly, 0.05f) / sq;
        const float ge = expf(beam * logf(fmaxf(g, 1e-3f)));
        const float f = powf(rin / fmaxf(r, rin), 0.75f);
        const float hot = smoothstep(0.55f, 0.85f, n);
        const float T = Tmax * f * ge * (1.f + 0.35f * turb * (n - 0.5f));
        const float3 bbc = bb_rgb(T);
        const float3 pc = srgb_to_linear(pal((1.f - f * ge) * 0.8f * period + 0.3f * n + drift, PAL));
        const float lum = ge * ge * ge * f * f * (0.55f + 0.9f * n) * (1.f + P[41] * hot) * P[52];
        const float3 em = mix(bbc, pc, dmix) * lum;
        col += em * (trans * alpha);
        trans *= 1.f - alpha;
    }

    // ---- photon ring glow (stylised): bright at the shadow edge, brighter on the approaching side
    {
        const float db = b - BH_BC;
        const float ring = db > 0.f ? (expf(-db * 9.f) + 0.45f * expf(-db * 1.6f) + 0.08f * expf(-db * 0.35f))
                                    : expf(db * 30.f);
        const float side = fminf(fmaxf(Ly / fmaxf(b, 1e-3f), -1.f), 1.f);
        const float gs = expf(beam * 1.2f * side);
        const float3 rcol = mix(bb_rgb(Tmax * 0.8f * gs), srgb_to_linear(pal(0.1f + drift, PAL)), dmix);
        col += rcol * (ring * gs * P[42] * trans);
    }

    // ---- lensed background
    if (cap < 0.999f && trans > 0.01f) {
        float se, ce;
        sincosf(phend, &se, &ce);
        const float3 v = normalize(e1 * ce + e2 * se);
        col += background(v, P, pix) * (trans * (1.f - cap));
    }

    col = vignette(col * P[27], x, y, W, H, 0.3f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col)));
}
""" % {"nd": ND, "np": NP, "bc": BC}


class BlackHole(Source):
    name = "black_hole"
    label = "Black hole"
    description = ("A Schwarzschild black hole: a Doppler-beamed blackbody accretion disc lensed over its own shadow, "
                   "with a photon ring and lensed stars and galaxy behind.")
    params = {
        **standard_scene_params(palette="ocean", render_scale=0.5, period=1.0, drift=0.02),
        "camera_distance": num(14.0, 45.0, 26.0, "Camera distance", unit="M", tier="regional", basic=True),
        "inclination": num(0.0, 70.0, 9.0, "View elevation above disc", unit="°", tier="regional"),
        "disc_width": num(3.0, 20.0, 11.0, "Disc width", unit="M", tier="regional"),
        "disc_temperature": num(2500.0, 15000.0, 6800.0, "Disc temperature", step=50.0, unit="K", tier="regional"),
        "beaming_strength": num(0.0, 1.5, 0.7, "Doppler beaming", tier="regional"),
        "disc_palette": num(0.0, 1.0, 0.0, "Disc colour (blackbody -> palette)", tier="regional"),
        "turbulence": num(0.0, 1.0, 0.65, "Disc turbulence", tier="regional"),
        "star_field": num(0.0, 2.0, 1.0, "Stars and galaxy", tier="regional"),
        "rotation_speed": num(0.0, 4.0, 1.0, "Disc rotation", unit="×", tier="realtime", bind="rate"),
        "orbit_speed": num(0.0, 4.0, 1.0, "Camera orbit", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        mod = raw_module(_SRC, SDF3D_OPTIONS)
        self.k = mod.get_function("black_hole")
        self.k_lut = mod.get_function("bh_lut")
        self.invr = cp.zeros(ND * NP, cp.float32)
        self.endl = cp.zeros(ND * 2, cp.float32)
        self.P = cp.zeros(64, cp.float32)
        self.Ph = np.zeros(64, np.float32)
        seed = int(params["seed"])
        rng = np.random.default_rng(seed)
        tilt = rng.uniform(0.55, 1.1)
        az = rng.uniform(0.0, 2.0 * math.pi)
        self.gal_n = (math.sin(tilt) * math.cos(az), math.cos(tilt), math.sin(tilt) * math.sin(az))
        self.noise_seed = float(seed % 4093)
        self.rc_lut = None
        self.dc = 0.0
        self.sq = 1.0
        self.flow = rng.uniform(0.0, 1.0)      # disc flow cycles (two crossfaded layers)
        self.az = rng.uniform(0.0, 2.0 * math.pi)
        self.cam_ph = 0.0
        self.drift = 0.0
        self.e_s = None

    def _build_lut(self, rc: float) -> None:
        self.dc = math.asin(min(BC * math.sqrt(1.0 - 2.0 / rc) / rc, 0.999))
        self.sq = math.sqrt(1.0 - 2.0 / rc)
        self.k_lut(((ND + 127) // 128, 1, 1), (128, 1, 1),
                   (self.invr, self.endl, np.float32(rc), np.float32(self.dc), np.float32(PHIMAX)))
        self.rc_lut = rc

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        a = 1.0 - math.exp(-dt / 0.3) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s

        # ---- tables: rebuilt only when the (quantised) camera distance changes
        rc = round(float(p["camera_distance"]) / RC_QUANT) * RC_QUANT
        if self.rc_lut is None or abs(rc - self.rc_lut) > 1e-6:
            self._build_lut(rc)

        # ---- integrate rates (pulse adds to rates only)
        self.flow += float(p["rotation_speed"]) * spd * (0.07 + 0.2 * e + 0.12 * pulse) * dt
        self.flow = math.fmod(self.flow, 65536.0)
        self.az = wrap(self.az + float(p["orbit_speed"]) * spd * (0.012 + 0.05 * e + 0.02 * pulse) * dt,
                       2.0 * math.pi)
        self.cam_ph = wrap(self.cam_ph + spd * (0.05 + 0.12 * e) * dt, 2.0 * math.pi * 64.0)
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # ---- camera: orbit the hole's axis, gentle elevation sway and roll
        el = math.radians(float(p["inclination"]) + (0.8 + 2.4 * es) * math.sin(0.7 * self.cam_ph))
        eye = (rc * math.cos(el) * math.cos(self.az), rc * math.sin(el), rc * math.cos(el) * math.sin(self.az))
        roll = 0.05 + (0.01 + 0.03 * es) * math.sin(0.43 * self.cam_ph + 0.6)
        cam = camera_pack(eye, (0.0, 0.0, 0.0), fov_deg=FOV, roll=roll)

        r_out = min(R_IN + float(p["disc_width"]), 0.85 * rc * max(math.cos(el), 0.2))
        fa = self.flow % 1.0
        fb = (self.flow + 0.5) % 1.0
        sa = float((2 * int(math.floor(self.flow))) % 60000 + 1)
        sb = float((2 * int(math.floor(self.flow + 0.5)) + 1) % 60000 + 1)

        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"]) * (0.9 + 0.25 * e)
        Ph[28] = rc
        Ph[29] = self.dc
        Ph[30] = R_IN
        Ph[31] = r_out
        Ph[32] = float(p["disc_temperature"]) * (0.85 + 0.3 * e)
        Ph[33] = p["beaming_strength"]
        Ph[34] = p["disc_palette"]
        Ph[35] = min(float(p["turbulence"]) * (0.55 + 0.65 * e ** 1.3), 1.0)
        Ph[36] = fa
        Ph[37] = fb
        Ph[38] = sa
        Ph[39] = sb
        Ph[40] = 3.0
        Ph[41] = 3.5 * pulse
        Ph[42] = (0.35 + 0.25 * e) * (1.0 + 2.2 * pulse)
        Ph[43] = float(p["star_field"]) * (0.9 + 0.2 * e)
        Ph[44:47] = self.gal_n
        Ph[47] = self.noise_seed
        Ph[48] = self.sq
        Ph[49] = 2.6
        Ph[50] = e
        Ph[51] = PHIMAX / (NP - 1)
        Ph[52] = 1.9 * (1.0 + 0.25 * pulse)
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P, self.invr, self.endl))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
        self.invr = None
        self.endl = None
