"""Dune sea: a low glide over an endless golden desert of analytic, domain-warped, asymmetric dune ridges.

Source: Original (inspired by https://iquilezles.org/articles/terrainmarching/ and
        https://iquilezles.org/articles/fog/)
Author: smfx (original; iq's terrainmarching and fog article snippets, MIT)
License: MIT (original)
license_class: permissive

Look: dune ridges with a gentle stoss side and a steep slip face, sinuous through a periodic sine domain warp,
morphing continuously from transverse ridges through barchan crescents to star dunes (dune_shape).  Wind ripples
are normal-only detail (faded by pixel footprint, absent on slip faces), a low sun throws long 12-tap soft
shadows, spindrift streams off the crests (accumulated along the march where the ray skims the sand), heat
shimmer bends rays near the horizon, height-free exponential haze takes iq's sun-direction fog colour, and a
sun below the horizon turns it to a moonlit dusk with a starfield.

World layout: the camera sits at xz = 0; everything is evaluated in "dune space" q = xz / lambda + Q, where Q is
the camera's host-integrated position in dune units wrapped mod 64.  Every q-pattern (warp/modulation sine waves
with integer wave vectors, hashed barchan/star cells, the noise texture) is periodic with 64, so the wrap is
invisible and fp32 arguments stay small.  Ripples use a camera-relative coordinate plus a wrapped phase.

Macros: energy -> glide speed (0.25 + 0.75e), spindrift density (e^1.3, floor 0.15), heat shimmer
(0.3 + 0.7e), camera bank/sway amplitude and dune morph rate.  pulse -> a wind gust (ripple scroll-rate kick,
spindrift gain, haze brightening) and a sun-flare punch (disc and glow gain); rates and gains only.  speed scales
glide, ripple, spindrift and shimmer rates; drift slides the palette hue of the sand and sky (luminance matched,
mixed in by palette_tint); intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, smoothstep, wrap
from stream_service.plugins._lib.upsample import internal_size, present

NPER = 64.0        # dune-space period (dune units)
TEXN = 512         # noise texture size; covers NPER dune units (8 texels per unit)
NWAVE = 9          # 3 warp-x, 3 warp-z, 3 modulation waves

# P layout (floats)
#   0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 lambda | 29 1/lambda | 30,31 Q
#   32 H (world dune height scale) | 33 w_trans 34 w_barchan 35 w_star | 36 march 1/L slope term
#   37..39 key dir | 40..42 key colour | 43..45 sun dir | 46 sun visible gain (disc/glow incl. flare)
#   47..49 zenith | 50..52 horizon | 53..55 glow colour | 56 star amount | 57 twinkle phase
#   58 haze density | 59 haze gain | 60 ripple amount | 61 ripple phase | 62 1/ripple wavelength
#   63 spindrift density | 64 spindrift phase (dune units) | 65 shimmer strength | 66 shimmer phase (texels)
#   67 palette tint | 68 crest softness | 69 warp amp | 70..72 moon dir | 73 moon disc gain
#   80..115 waves (kx, kz, phase, amp) x 9
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define TEXN 512
#define TEXM 511
#define TMAX 260.f

__device__ __forceinline__ float lum(float3 c) { return 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z; }
__device__ __forceinline__ int pmod(int a, int n) { const int r = a % n; return r < 0 ? r + n : r; }

// Hermite-interpolated, wrapping sample of the tileable noise texture (u, v in texels).  Shading-only uses.
__device__ __forceinline__ float texs(const float* __restrict__ T, float u, float v) {
    const float fu = floorf(u), fv = floorf(v);
    float tx = u - fu, ty = v - fv;
    tx = tx * tx * (3.f - 2.f * tx); ty = ty * ty * (3.f - 2.f * ty);
    const int iu = ((int)fu) & TEXM, iv = ((int)fv) & TEXM;
    const int iu1 = (iu + 1) & TEXM, iv1 = (iv + 1) & TEXM;
    const float a = T[iv * TEXN + iu], b = T[iv * TEXN + iu1], c = T[iv1 * TEXN + iu], d = T[iv1 * TEXN + iu1];
    return mix(mix(a, b, tx), mix(c, d, tx), ty);
}

__device__ __forceinline__ float wave(float2 q, const float* __restrict__ W) {
    return W[3] * __sinf(W[0] * q.x + W[1] * q.y + W[2]);
}

// Asymmetric transverse profile: gentle (rounded-foot) stoss side over fraction a, steep linear slip face.
__device__ __forceinline__ float prof(float u, float kc) {
    const float a = 0.72f;
    u = u - floorf(u);
    const float s1 = u * (1.f / a);
    const float r1 = s1 * s1 * (1.5f - 0.5f * s1);
    const float r2 = (1.f - u) * (1.f / (1.f - a));
    return fmaxf(smin(r1, r2, kc), 0.f);
}

// Barchan crescents on a 1.6-unit jittered grid (40 cells per period), horns pointing downwind (+x).
__device__ float barchans(float2 qw, float kc) {
    const float2 g = qw * (1.f / 1.6f);
    const float bx = floorf(g.x - 0.5f), bz = floorf(g.y - 0.5f);
    float best = 0.f;
    #pragma unroll
    for (int j = 0; j < 4; ++j) {
        const int cx = (int)bx + (j & 1), cz = (int)bz + (j >> 1);
        const unsigned int hx = (unsigned int)pmod(cx, 40), hz = (unsigned int)pmod(cz, 40);
        const float r1 = hash1(hx, hz, 11u), r2 = hash1(hx, hz, 23u), r3 = hash1(hx, hz, 37u);
        const float sz = 0.44f + 0.22f * r3;
        float2 d = (g - f2(cx + 0.5f + (r1 - 0.5f) * 0.3f, cz + 0.5f + (r2 - 0.5f) * 0.3f)) * (1.f / sz);
        if (dot(d, d) > 1.6f) continue;
        float2 e = d;
        if (e.x < 0.f) e.x *= 0.8f;                                  // long gentle stoss upwind
        const float s = saturate(1.f - length(e));
        const float dome = s * s * (3.f - 2.f * s);
        const float di = length(d - f2(0.42f, 0.f)) - 0.6f;           // slip-face hollow, opening downwind
        const float slip = smin(1.f, fmaxf(di * 4.f, 0.f), kc * 2.f);
        best = fmaxf(best, dome * slip * (0.75f + 0.55f * r3));
    }
    return best;
}

// Star dunes on a 2-unit jittered grid (32 cells per period): 3-5 curling sharp arms around a pyramid peak.
__device__ float stars(float2 qw) {
    const float2 g = qw * 0.5f;
    const float bx = floorf(g.x - 0.5f), bz = floorf(g.y - 0.5f);
    float best = 0.f;
    #pragma unroll
    for (int j = 0; j < 4; ++j) {
        const int cx = (int)bx + (j & 1), cz = (int)bz + (j >> 1);
        const unsigned int hx = (unsigned int)pmod(cx, 32), hz = (unsigned int)pmod(cz, 32);
        const float r1 = hash1(hx, hz, 51u), r2 = hash1(hx, hz, 67u), r3 = hash1(hx, hz, 79u);
        const float r4 = hash1(hx, hz, 97u);
        const float2 d = g - f2(cx + 0.5f + (r1 - 0.5f) * 0.3f, cz + 0.5f + (r2 - 0.5f) * 0.3f);
        const float r = length(d);
        if (r > 0.82f) continue;
        const float n = 3.f + floorf(r3 * 2.99f);
        const float sector = SDF_TAU / n;
        const float th = atan2f(d.y, d.x) - r4 * SDF_TAU - 1.6f * r;
        const float a = th - sector * floorf(th / sector + 0.5f);
        const float perp = r * fabsf(__sinf(a)) * (a > 0.f ? 1.f : 1.75f);
        const float w = 0.04f + 0.36f * r;
        float across = saturate(1.f - perp / w);
        across = across * (0.45f + 0.55f * across);
        const float along = saturate(1.f - r * (1.f / 0.8f));
        const float pk = saturate(1.f - r * (1.f / 0.3f));
        float h = fmaxf(along * (0.35f + 0.65f * along) * across, pk * pk * (3.f - 2.f * pk) * 0.95f);
        const float sk = saturate(1.f - r * (1.f / 0.82f));
        h = fmaxf(h, sk * sk * 0.28f);
        best = fmaxf(best, h * (0.75f + 0.45f * r4));
    }
    return best;
}

// Dimensionless dune height at local world xz; hr returns the dune part (crestness proxy) and m the modulation.
__device__ float dune_h(float px, float pz, const float* __restrict__ P, float& m) {
    const float2 q = f2(px * P[29] + P[30], pz * P[29] + P[31]);
    const float* Wv = P + 80;
    const float wx = wave(q, Wv) + wave(q, Wv + 4) + wave(q, Wv + 8);
    const float wz = wave(q, Wv + 12) + wave(q, Wv + 16) + wave(q, Wv + 20);
    m = wave(q, Wv + 24) + wave(q, Wv + 28) + wave(q, Wv + 32);         // ~[-1, 1]
    const float2 qw = q + f2(wx, wz) * P[69];
    const float kc = P[68];
    float h = 0.f;
    if (P[33] > 1e-3f) h += P[33] * prof(qw.x + 0.22f * m, kc) * (0.78f + 0.32f * m);
    if (P[34] > 1e-3f) h += P[34] * barchans(qw, kc);
    if (P[35] > 1e-3f) h += P[35] * stars(qw);
    return h + 0.07f * (m + 1.f);
}
__device__ __forceinline__ float dune_y(float px, float pz, const float* __restrict__ P) {
    float m; return P[32] * dune_h(px, pz, P, m);
}

__device__ float3 sky(float3 rd, const float* __restrict__ P, float3 tint_h) {
    const float e = fmaxf(rd.y, 0.f);
    const float3 zen = load3(P + 47);
    float3 c = mix(tint_h, zen, powf(e, 0.45f));
    const float3 sd = load3(P + 43);
    const float mu = dot(rd, sd);
    const float2 hf = normalize(f2(rd.x, rd.z)), sf = normalize(f2(sd.x, sd.z));
    const float mh = saturate(dot(hf, sf));
    const float3 gc = load3(P + 53);
    c += gc * (0.55f * mh * mh * mh * mh * __expf(-e * 5.f) + 0.4f * powf(saturate(mu), 12.f)) * P[46];
    return c;
}

extern "C" __global__ __launch_bounds__(256, 3)
void dune_sea(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
              const float* __restrict__ T)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye;
    float3 rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);

    // heat shimmer: a smooth, rising refraction field near the horizon (integrated phase, no per-frame noise)
    if (P[65] > 0.f) {
        const float msk = __expf(-fabsf(rd.y) * 22.f);
        const float u = (float)x / (float)W * 1100.f, yv = (float)y / (float)H;
        const float n1 = texs(T, u, yv * 380.f + P[66]) - 0.5f;
        const float n2 = texs(T, u * 0.5f + 211.f, yv * 270.f + 2.f * P[66] + 97.f) - 0.5f;
        rd.y += P[65] * msk * (n1 + 0.6f * n2);
        rd.x += 0.3f * P[65] * msk * n2;
        rd = normalize(rd);
    }

    const float3 kd = load3(P + 37), kcol = load3(P + 40), sd = load3(P + 43);
    const float tint = P[67];
    // palette tint (luminance matched) for the horizon / haze colour and the sand
    const float3 hor0 = load3(P + 50);
    const float3 phc = srgb_to_linear(pal(P[26] + 0.08f + 0.15f * P[25], P + 13));
    const float3 phm = min(phc * (lum(hor0) / fmaxf(lum(phc), 0.02f)), f3(4.f * fmaxf(lum(hor0), 0.05f)));
    const float3 hor = mix(hor0, phm, 0.55f * tint);

    const float Hs = P[32];
    const float top = 1.42f * Hs, bot = -0.05f * Hs;
    float3 col;
    float t = TMAX, acc = 0.f;
    bool hit = false;
    float mres = 0.f;
    if (rd.y < -1e-4f || ro.y < top) {
        float t0 = 0.f;
        if (ro.y > top) t0 = (top - ro.y) / rd.y;
        const float t1 = rd.y < -1e-4f ? fminf((bot - ro.y) / rd.y, TMAX) : TMAX;
        const float invL = 1.f / (fabsf(rd.y) + P[36] * sqrtf(fmaxf(1.f - rd.y * rd.y, 0.f)));
        const float thick = 0.3f * Hs;
        const float sdens = P[63];
        float tp = t0, gp = 1e9f;
        t = t0;
        for (int i = 0; i < 72; ++i) {
            const float3 p = ro + rd * t;
            float m;
            const float hd = dune_h(p.x, p.z, P, m);
            const float g = p.y - Hs * hd;
            if (g < 0.f) {                                            // crossed: secant back to the surface
                t = tp + (t - tp) * gp / fmaxf(gp - g, 1e-6f);
                hit = true; break;
            }
            if (g < 0.6f * pix * t) { hit = true; break; }
            const float st = fmaxf(g * invL, 0.0015f * t + 0.004f);
            // spindrift: wind-streaked sand density near the crests, thickest just above the sand
            if (sdens > 0.f && g < 4.f * thick) {
                const float qx = p.x * P[29] + P[30], qz = p.z * P[29] + P[31];
                float s = texs(T, (qx - P[64]) * 8.f, qz * 64.f);
                s = smoothstep(0.42f, 0.78f, s) * (0.5f + texs(T, (qx - 2.f * P[64]) * 16.f + 77.f, qz * 128.f));
                const float crest = smoothstep(0.35f, 0.9f, hd);
                acc += sdens * crest * s * __expf(-g / thick) * fminf(st, 2.f * thick);
            }
            tp = t; gp = g;
            t += st;
            if (t >= t1) { t = t1; hit = t1 < TMAX; break; }
        }
        if (!hit && t < TMAX) hit = true;                             // step budget ran out: shade where we are
    }

    if (hit) {
        const float3 pos = ro + rd * t;
        // normal: footprint-scaled central-ish differences of the analytic height (LOD for free)
        const float ep = fmaxf(0.012f, 0.9f * pix * t);
        const float h0 = dune_y(pos.x, pos.z, P);
        const float hx = dune_y(pos.x + ep, pos.z, P);
        const float hz = dune_y(pos.x, pos.z + ep, P);
        float3 n = normalize(f3(h0 - hx, ep, h0 - hz));
        const float slope = sqrtf(fmaxf(1.f - n.y * n.y, 0.f));

        // wind ripples: normal-only, perpendicular to the wind (x), sinuous, fade with footprint and on slip faces
        const float qx = pos.x * P[29] + P[30], qz = pos.z * P[29] + P[31];
        if (P[60] > 0.f) {
            const float ir = P[62];
            const float fpw = pix * t * ir * rsqrtf(fmaxf(-rd.y, 0.03f));
            const float fade = saturate(1.f - fpw * 2.2f) * (1.f - smoothstep(0.32f, 0.6f, slope));
            if (fade > 0.f) {
                const float wig = 0.35f * __sinf(qz * 9.4247780f + 1.3f * __sinf(qx * 3.1415926f));
                float rc = pos.x * ir + P[61] + wig + 0.5f * texs(T, qx * 32.f, qz * 32.f);
                rc = rc - floorf(rc);
                // asymmetric ripple: derivative of a skewed sawtooth (steep lee)
                const float ph = SDF_TAU * rc;
                const float dr = __cosf(ph) + 0.45f * __cosf(2.f * ph) + 0.18f * __cosf(3.f * ph);
                n = normalize(n + f3(-dr * 0.22f * P[60] * fade, 0.f, -0.25f * dr * 0.22f * P[60] * fade * wig));
            }
        }

        // albedo: golden sand, palette tinted (luminance matched), mottled, slip faces a touch darker
        const float mot = texs(T, qx * 32.f + 13.f, qz * 32.f + 51.f);
        const float3 sb = f3(0.62f, 0.38f, 0.19f);
        const float3 pc = srgb_to_linear(pal(P[26] + (h0 / fmaxf(Hs, 1e-3f)) * 0.12f * P[25] + 0.06f * mot, P + 13));
        const float3 pcm = min(pc * (lum(sb) / fmaxf(lum(pc), 0.02f)), f3(1.2f));
        float3 alb = mix(sb, pcm, tint) * (0.86f + 0.28f * mot) * (1.f - 0.12f * smoothstep(0.35f, 0.7f, slope));

        // 12-tap heightfield soft shadow towards the key light (skipped when deep in the haze)
        float sh = 1.f;
        const float fogt = 1.f - __expf(-P[58] * t);
        if (kd.y > 0.f && fogt < 0.97f) {
            float ts = 0.06f + 0.02f * t * pix * 50.f;
            const float3 o = pos + n * 0.03f;
            for (int j = 0; j < 12; ++j) {
                const float3 sp = o + kd * ts;
                if (sp.y > top) break;
                const float gg = sp.y - dune_y(sp.x, sp.z, P);
                sh = fminf(sh, 7.f * gg / ts);
                if (sh < 0.f) break;
                ts *= 1.62f;
            }
            sh = saturate(sh);
            sh = sh * sh * (3.f - 2.f * sh);
        }
        const float dif = saturate(dot(n, kd));
        const float3 zen = load3(P + 47);
        float3 lit = kcol * (dif * sh);
        lit += zen * (0.75f + 0.5f * n.y) + hor * (0.25f * (1.f - n.y));        // sky fill
        lit += kcol * sb * (0.06f * (1.f - n.y) * saturate(kd.y * 3.f));        // warm bounce off the sand
        col = alb * lit;
        // backlit crest sheen (grazing forward scatter)
        const float vn = saturate(dot(n, -rd));
        const float fr = (1.f - vn) * (1.f - vn);
        col += kcol * alb * (0.5f * powf(saturate(dot(rd, kd)), 3.f) * fr * sh);
        mres = slope;
    } else {
        col = sky(rd, P, hor);
        // stars and moon (dusk-to-night)
        if (P[56] > 0.f && rd.y > 0.f) {
            const float2 su = f2(rd.x, rd.z) * (1.f / (1.f + rd.y)) * 420.f;
            const float fx = floorf(su.x), fz = floorf(su.y);
            const unsigned int cx = (unsigned int)((int)fx + 4096), cz = (unsigned int)((int)fz + 4096);
            const float hs = hash1(cx, cz, 5u);
            if (hs > 0.965f) {
                const float2 jp = f2(fx + 0.2f + 0.6f * hash1(cx, cz, 9u), fz + 0.2f + 0.6f * hash1(cx, cz, 13u));
                const float dd = length(su - jp);
                const float b = (hs - 0.965f) * 28.6f;
                const float tw = 0.65f + 0.35f * __sinf(P[57] * (1.f + floorf(2.99f * hash1(cx, cz, 17u))) + 40.f * hs);
                const float3 sc = mix(f3(1.f, 0.85f, 0.7f), f3(0.75f, 0.85f, 1.f), hash1(cx, cz, 21u));
                col += sc * (P[56] * b * b * tw * 2.5f * __expf(-dd * dd * 9.f) * smoothstep(0.f, 0.12f, rd.y));
            }
            const float3 md = load3(P + 70);
            const float mm = dot(rd, md);
            col += f3(0.85f, 0.9f, 1.f) * (P[73] * (smoothstep(0.99955f, 0.99975f, mm) * 6.f
                                                  + 0.06f * powf(saturate(mm), 60.f)));
        }
        // the sun disc (flare-punched)
        const float mu = dot(rd, sd);
        col += load3(P + 53) * (smoothstep(0.99935f, 0.99965f, mu) * 30.f * P[46]) * smoothstep(-0.02f, 0.01f, rd.y);
    }

    // haze (iq: sun-direction fog colour); the sky gets the horizon band of it
    const float2 hf = normalize(f2(rd.x, rd.z)), sf = normalize(f2(sd.x, sd.z));
    const float sa = saturate(dot(hf, sf));
    const float sa2 = sa * sa, sa4 = sa2 * sa2;
    float3 fogc = (hor + load3(P + 53) * (0.6f * sa4 * sa4 * P[46] + 0.12f * sa2)) * P[59];
    if (hit) {
        col = mix(col, fogc, 1.f - __expf(-P[58] * t));
    } else {
        col = mix(col, fogc, saturate(P[58] * 60.f) * __expf(-fmaxf(rd.y, 0.f) * 14.f));
    }
    // spindrift on top (lit by the key light, forward scattering towards it)
    if (acc > 0.f) {
        const float a = 1.f - __expf(-acc);
        const float fs = powf(saturate(dot(rd, kd)), 4.f);
        const float3 sc = kcol * (0.18f + 0.9f * fs) * f3(0.95f, 0.82f, 0.62f) + hor * 0.55f;
        col = mix(col, sc, a * 0.85f);
    }
    col = vignette(col, x, y, W, H, 0.22f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
"""


def _rand_waves(rng, specs):
    """Integer wave vectors (periodic in NPER) near a target index |k|, random direction and phase."""
    out = []
    for target, amp, aniso in specs:
        while True:
            ang = rng.uniform(0.0, 2.0 * math.pi)
            kx, kz = round(target * math.cos(ang) * aniso), round(target * math.sin(ang))
            if kx != 0 or kz != 0:
                break
        s = 2.0 * math.pi / NPER
        out.append([kx * s, kz * s, rng.uniform(0.0, 2.0 * math.pi), amp])
    return np.asarray(out, np.float64)


def _noise_texture(rng) -> np.ndarray:
    n = rng.standard_normal((TEXN, TEXN))
    f = np.fft.fftfreq(TEXN) * TEXN
    r = np.sqrt(f[:, None] ** 2 + f[None, :] ** 2)
    spec = np.exp(-(r / 24.0) ** 2) * (r > 0.5)
    v = np.real(np.fft.ifft2(np.fft.fft2(n) * spec))
    v = (v - v.mean()) / max(v.std(), 1e-9)
    return np.clip(0.5 + 0.18 * v, 0.0, 1.0).astype(np.float32)


def _mix3(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


class DuneSea(Source):
    name = "dune_sea"
    label = "Dune sea"
    description = ("A low glide over an endless desert of sharp-crested dunes, transverse to barchan to star, with "
                   "long low-sun shadows, spindrift, heat shimmer and a moonlit starfield option.")
    params = {
        **standard_scene_params(palette="ember", render_scale=0.5, period=1.0, drift=0.006),
        "dune_shape": num(0.0, 1.0, 0.3, "Dune shape (transverse -> barchan -> star)", tier="regional",
                          basic=True),
        "dune_scale": num(0.4, 2.5, 1.0, "Dune scale", tier="regional"),
        "relief": num(0.3, 1.6, 1.0, "Dune height", tier="regional"),
        "sun_elevation": num(-12.0, 40.0, 7.0, "Sun elevation (night <- dusk -> day)", step=0.5, unit="deg",
                             tier="regional", basic=True),
        "haze": num(0.0, 1.0, 0.35, "Haze", tier="regional"),
        "ripple_detail": num(0.0, 1.0, 0.6, "Wind ripples", tier="regional"),
        "spindrift": num(0.0, 1.0, 0.5, "Spindrift", tier="regional"),
        "camera_height": num(0.0, 1.0, 0.25, "Camera height (skim -> aerial)", tier="regional"),
        "palette_tint": num(0.0, 1.0, 0.45, "Palette tint", tier="regional"),
        "glide_speed": num(0.0, 4.0, 1.0, "Glide speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("dune_sea")
        self.P = cp.zeros(128, cp.float32)
        self.Ph = np.zeros(128, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.tex = cp.asarray(_noise_texture(rng))
        # warp-x (strong, long meanders + crest wiggle), warp-z, crest-height modulation; x stretched for warp-x
        self.waves = _rand_waves(rng, [(4.0, 0.95, 1.0), (9.0, 0.32, 1.0), (22.0, 0.11, 1.0),
                                       (5.0, 0.6, 1.0), (11.0, 0.24, 1.0), (19.0, 0.09, 1.0),
                                       (5.0, 0.5, 1.0), (11.0, 0.3, 1.0), (17.0, 0.2, 1.0)])
        self.wave_rate = rng.uniform(0.012, 0.035, NWAVE) * rng.choice([-1.0, 1.0], NWAVE)
        self.Q = np.array([rng.uniform(0.0, NPER), rng.uniform(0.0, NPER)])
        self.cam_ph = float(rng.uniform(0.0, 100.0))
        self.rph = 0.0
        self.sp = 0.0
        self.shim = 0.0
        self.tw = 0.0
        self.drift = 0.0
        self.e_s = None
        self.roll = 0.0

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.25)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        k = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        a = 1.0 - math.exp(-dt / 1.0) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s

        # ---- shape / scale
        lam = 8.0 * float(p["dune_scale"])
        relief = float(p["relief"])
        H = 0.14 * relief * lam
        s = min(max(float(p["dune_shape"]), 0.0), 1.0)
        w_t = 1.0 - smoothstep(0.0, 0.5, s)
        w_s = smoothstep(0.5, 1.0, s)
        w_b = max(0.0, 1.0 - w_t - w_s)
        top = 1.42 * H

        # ---- camera: glide with a wandering heading, banking into turns; altitude above the highest crests
        wr = spd * (0.35 + 0.65 * es)
        self.cam_ph += wr * dt
        c = self.cam_ph
        heading = 0.5 + 0.45 * math.sin(0.061 * c) + 0.2 * math.sin(0.023 * c + 1.7)
        h_rate = (0.45 * 0.061 * math.cos(0.061 * c) + 0.2 * 0.023 * math.cos(0.023 * c + 1.7)) * wr
        bank = 0.01 + 0.05 * es
        roll_t = max(-0.1, min(0.1, -2.0 * h_rate * (0.5 + es))) + bank * math.sin(0.27 * c)
        self.roll += (roll_t - self.roll) * (1.0 - math.exp(-dt / 0.7) if dt > 0 else 1.0)
        ch = float(p["camera_height"])
        eye_y = top + 0.45 + 9.0 * ch * ch + 0.15 * H * math.sin(0.19 * c)
        pitch = -math.atan2(eye_y + 0.4 * H, 2.4 * lam + 6.0) - 0.035 - 0.01 * math.sin(0.33 * c)
        fwd = (math.sin(heading) * math.cos(pitch), math.sin(pitch), math.cos(heading) * math.cos(pitch))
        cam = camera_pack((0.0, eye_y, 0.0), (fwd[0], eye_y + fwd[1], fwd[2]), fov_deg=50.0, roll=self.roll)

        # ---- integrate travel (dune units), dune morph, ripples, spindrift, shimmer, twinkle, drift
        gs = float(p["glide_speed"])
        v = (0.7 + 2.1 * es) * gs * spd                                       # world units / s
        vx, vz = v * math.sin(heading), v * math.cos(heading)
        self.Q[0] = wrap(self.Q[0] + vx / lam * dt, NPER)
        self.Q[1] = wrap(self.Q[1] + vz / lam * dt, NPER)
        self.waves[:, 2] = np.mod(self.waves[:, 2] + self.wave_rate * (0.25 + 0.75 * es) * spd * dt, 2.0 * math.pi)
        rd_ = float(p["ripple_detail"])
        lam_r = 0.2 - 0.08 * rd_
        scroll = 0.05 * (0.4 + 0.6 * e) * (1.0 + 5.0 * k)                    # ripple migration (world u/s)
        self.rph = wrap(self.rph + (vx - scroll * spd) / lam_r * dt, 1.0)
        self.sp = wrap(self.sp + (0.25 + 0.9 * e) * spd * (1.0 + 1.2 * k) * dt, NPER)
        self.shim = wrap(self.shim + (14.0 + 30.0 * e) * spd * dt, float(TEXN))
        self.tw = wrap(self.tw + 1.3 * dt, 2.0 * math.pi * 16.0)
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # ---- light: sun / moon, sky colours by elevation (linear)
        el_deg = float(p["sun_elevation"])
        el = math.radians(el_deg)
        az = -0.65                                                            # sun fixed in the world (upwind side)
        sun = (math.cos(el) * math.sin(az), math.sin(el), math.cos(el) * math.cos(az))
        mel, maz = math.radians(32.0), az + 2.4
        moon = (math.cos(mel) * math.sin(maz), math.sin(mel), math.cos(mel) * math.cos(maz))
        dayf = smoothstep(-6.0, 3.0, el_deg)
        low = 1.0 - smoothstep(2.0, 28.0, el_deg)
        vis = smoothstep(-2.5, 4.0, el_deg)
        sun_col = tuple(x * 3.4 * vis for x in _mix3((1.0, 0.9, 0.76), (1.0, 0.52, 0.2), low))
        moon_col = tuple(x * 0.5 * (1.0 - dayf) for x in (0.32, 0.42, 0.68))
        key_col = tuple(a_ + b_ for a_, b_ in zip(sun_col, moon_col))
        kd = _mix3(moon, sun, dayf)
        kd = (kd[0], max(kd[1], 0.04), kd[2])
        kn = math.sqrt(sum(x * x for x in kd))
        kd = tuple(x / kn for x in kd)
        zen = _mix3((0.003, 0.006, 0.016), _mix3((0.07, 0.17, 0.45), (0.06, 0.1, 0.27), low), dayf)
        hor = _mix3((0.012, 0.018, 0.04), _mix3((0.55, 0.53, 0.5), (0.85, 0.47, 0.24), low), dayf)
        glowf = smoothstep(-9.0, 0.0, el_deg) * (0.35 + 0.65 * low)
        glow = tuple(x * glowf for x in (1.0, 0.5, 0.18))
        flare = 1.0 + 1.8 * k
        haze = float(p["haze"])

        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = 1.25 * float(p["intensity"]) * (1.0 + 0.07 * k)
        Ph[28] = lam
        Ph[29] = 1.0 / lam
        Ph[30:32] = self.Q
        Ph[32] = H
        Ph[33], Ph[34], Ph[35] = w_t, w_b, w_s
        Ph[36] = 0.55 + 0.45 * relief
        Ph[37:40] = kd
        Ph[40:43] = key_col
        Ph[43:46] = sun
        Ph[46] = flare
        Ph[47:50] = zen
        Ph[50:53] = hor
        Ph[53:56] = glow
        Ph[56] = 1.0 - smoothstep(-7.0, 0.5, el_deg)
        Ph[57] = self.tw
        Ph[58] = (0.0025 + 0.035 * haze ** 1.5) * (8.0 / max(lam, 1.0)) ** 0.3
        Ph[59] = 1.0 + 0.4 * k
        Ph[60] = rd_
        Ph[61] = self.rph
        Ph[62] = 1.0 / lam_r
        Ph[63] = float(p["spindrift"]) * (0.15 + 0.85 * e ** 1.3) * (1.0 + 1.2 * k) * 1.5 / max(H, 0.2)
        Ph[64] = self.sp
        Ph[65] = (0.3 + 0.7 * e) * (0.4 + 0.6 * dayf) * (0.0012 + 0.002 * haze)
        Ph[66] = self.shim
        Ph[67] = p["palette_tint"]
        Ph[68] = 0.12
        Ph[69] = 1.0
        Ph[70:73] = moon
        Ph[73] = 1.0 - dayf
        Ph[80:80 + 4 * NWAVE] = self.waves.reshape(-1)
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P, self.tex))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
        self.tex = None
