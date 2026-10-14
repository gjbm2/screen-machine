"""Sakura Storm: a cherry-blossom storm.  Up to 180k petals ride a curl-noise wind through 3D space with depth
of field, in front of blossoming tree silhouettes, a pagoda (or torii) on a hill, a snow-capped volcano and a moon.
Beats send gusts sweeping across the frame that throw petals past the camera.

Source: Original (inspired by https://en.wikipedia.org/wiki/Cherry_blossom)
Author: smfx (original)
License: MIT (original)
license_class: permissive

Everything here is written from scratch for this renderer.

Petals (splat3d).  Each petal is a world position, a fade weight, a flutter phase, a per-petal spin rate, tone and
fall rate, and an orthonormal pair of axes (spin axis s, long axis a).  One fused kernel per frame:
  * advects every petal by RK2 through  wind (screen left -> right) + turbulence x curl-noise (sp_curl, offset
    drifting with the wind so the eddies travel) + gravity + up to two gust fronts (a gaussian bump in the
    normalised screen coordinate u = lateral / frustum half-width that sweeps left -> right, pushing petals along,
    up and TOWARDS the camera);
  * wraps a frustum-shaped domain (lateral and vertical wraps happen outside the view; a petal that drifts out of
    the far end comes back at a random depth and fades in);
  * spins the long axis about s (flutter phase integrated in-kernel, faster inside a gust) and writes TWO splats
    per petal (base and tip, offset along the projected long axis, so near petals read as ovals that foreshorten
    as they turn), each with brightness = |face normal . view| (+ a glint when face-on) and a two-tone colour:
    deeper pink at the base and on the front face, paler on the tip and the back face;
  * depth of field: per-point size = sqrt(geometric radius^2 + circle of confusion^2) about the focus distance,
    energy proportional to the geometric area so every petal has the same surface brightness at any depth.
Splat3D (alpha + occlusion so petals stay solid over a daylight sky) accumulates them at render_scale and resolves
straight onto the 4K output over the backdrop.

Backdrop (analytic 2D, passed as the resolve background).  Silhouette masks are rasterised once on the host
(cached per size / seed / landmark) into two RGBA8 textures (sharp and blurred, mixed by ``dof``): R = far range
with a snow-capped volcano, G = near hill with tree line + pagoda / torii, B = cherry-tree wood (procedural
branching), A = blossom canopy (thousands of soft discs on the twigs, a gap around the moon) + hill blossoms.
Per frame a kernel paints sky gradient, moon (disc, maria, halo), stars, drifting mist, the layers and the canopy
(rim-lit from the moon side, fine blossom speckle), sways the trees with the wind, and shifts everything with the
camera's small yaw / pitch.  ``backdrop`` morphs dusk .. night .. day.

Macros: energy -> wind strength, active petal count, flutter rate, turbulence, petal brightness, bloom, camera
sway and tree sway.  pulse -> rising edge launches a gust front (2 slots, decays); the envelope also kicks the
wind and the brightness a little.  speed scales advection and flutter.  drift moves the petal and sky palette
(``petal_tone`` 0 = natural pink, 0.5 = white, 1 = neon palette colour).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.splat3d import SPLAT3D_OPTIONS, SPLAT3D_SRC, Splat3D, look_at
from stream_service.plugins._lib.upsample import internal_size, present

N_MAX = 180_000
FOV = 50.0
TAN_HALF = math.tan(math.radians(FOV) * 0.5)
D0, D1 = 0.55, 20.0          # petal depth range (camera at the origin looking +z)
MARGIN = 0.35                # lateral margin beyond the frustum (world units)
PITCH0 = 0.06                # rest pitch (the backdrop is composed for it)
FADE_RATE = 1.1              # fade-in per second after a depth respawn
F_REF = 0.5 * 1080.0 / TAN_HALF

_SRC = HELPERS + PALETTE_CUDA + SPLAT3D_SRC + r"""
#define SK_PI2 6.2831853f
__device__ __forceinline__ float sk_lum(float3 c) { return 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z; }
// petal / canopy colour: k 0 natural (palette-tinged) .. 0.5 white .. 1 neon palette colour (sRGB in/out)
__device__ float3 sk_tone(float k, float3 nat, float3 pc) {
    const float3 c0 = sp_mix3(nat, pc, 0.3f);
    const float m = fmaxf(nat.x, fmaxf(nat.y, nat.z));
    const float3 c1 = sp_mix3(make_float3(m, m * 0.97f, m * 0.985f), sp_mul3(pc, m), 0.12f);
    const float L = sk_lum(pc);
    float3 s = make_float3(fmaxf(L + (pc.x - L) * 1.7f, 0.f), fmaxf(L + (pc.y - L) * 1.7f, 0.f),
                           fmaxf(L + (pc.z - L) * 1.7f, 0.f));
    const float ms = fmaxf(s.x, fmaxf(s.y, s.z));
    s = sp_mul3(s, m / fmaxf(ms, 0.05f));
    s = make_float3(fminf(s.x, 1.f), fminf(s.y, 1.f), fminf(s.z, 1.f));
    return k < 0.5f ? sp_mix3(c0, c1, k * 2.f) : sp_mix3(c1, s, k * 2.f - 1.f);
}

// wind velocity at p (world; screen-right is -x for a camera looking +z); g = local gust strength
__device__ __forceinline__ float3 sk_vel(float3 p, const float* __restrict__ P, unsigned int seed, float fallm,
                                         float& g) {
    const float sc = P[3];
    float3 v = sp_mul3(sp_curl(make_float3(p.x * sc + P[4], p.y * sc + P[5], p.z * sc + P[6]), seed, 2), P[2]);
    const float d = fmaxf(p.z, 0.f);
    const float u = -p.x / (d * P[25] + P[27]);
    const float iw = 1.f / P[46];
    const float a = (u - P[42]) * iw, b = (u - P[44]) * iw;
    g = P[43] * __expf(-a * a) + P[45] * __expf(-b * b);
    const float gs = g * P[47];
    v.x -= P[1] + gs;
    v.y += gs * P[32] - P[7] * fallm;
    v.z -= gs * P[33];
    return v;
}

extern "C" __global__ void __launch_bounds__(256)
sk_petals(float4* __restrict__ st, float4* __restrict__ ph, const float4* __restrict__ axs,
          const float4* __restrict__ axa, float4* __restrict__ pos2, float* __restrict__ rgb2,
          float* __restrict__ sz2, const float* __restrict__ P, const int n, const unsigned int seed,
          const unsigned int frame)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const float4 q = st[i];
    float3 p = make_float3(q.x, q.y, q.z);
    float fade = q.w;
    float4 m = ph[i];                                   // flutter phase, spin rate, tone, fall multiplier
    const float dt = P[0];
    const float TX = P[25], TY = P[26], M = P[27], Dn = P[28], Df = P[29], slope = P[48];
    float g = 0.f;
    if (dt > 0.f) {
        float g1;
        const float3 k1 = sk_vel(p, P, seed, m.w, g1);
        const float3 k2 = sk_vel(sp_add3(p, sp_mul3(k1, 0.5f * dt)), P, seed, m.w, g);
        p = sp_add3(p, sp_mul3(k2, dt));
        if (!(fabsf(p.x) + fabsf(p.y) + fabsf(p.z) < 1e4f)) {          // NaN / runaway: respawn mid-depth
            const float3 h = sp_hash3((unsigned int)i, seed * 7919u + frame);
            p = make_float3((2.f * h.x - 1.f) * 3.f, (2.f * h.y - 1.f) * 2.f, Dn + (Df - Dn) * h.z);
            fade = 0.f;
        }
        if (p.z < Dn || p.z > Df) {                                     // depth wrap
            const float h = sp_hash3((unsigned int)i, seed * 131u + frame).x;
            const float od = fmaxf(p.z, 0.f);
            const float nd = p.z < Dn ? Df - 0.5f : 1.5f + (Df - 2.5f) * h;
            const float sx = (nd * TX + M) / (od * TX + M), sy = (nd * TY + M) / (od * TY + M);
            p.x *= sx;
            p.y = nd * slope + (p.y - od * slope) * sy;
            p.z = nd;
            if (nd < Df - 2.f) fade = 0.f;                              // far respawns are hidden by far_fade
        }
        const float bx = p.z * TX + M, by = p.z * TY + M, cy = p.z * slope;   // lateral wraps: outside the view
        p.x -= 2.f * bx * floorf((p.x + bx) / (2.f * bx));
        p.y -= 2.f * by * floorf((p.y - cy + by) / (2.f * by));
        fade = fminf(fade + dt * P[30], 1.f);
        m.x += P[11] * m.y * (1.f + 2.2f * g);
        m.x -= SK_PI2 * floorf(m.x * (1.f / SK_PI2));
        st[i] = make_float4(p.x, p.y, p.z, fade);
        ph[i] = m;
    }
    // ---- shading: spinning petal, face brightness + glint, two tones
    const float4 S = axs[i], A = axa[i];
    float sn, cs;
    __sincosf(m.x, &sn, &cs);
    const float3 s = make_float3(S.x, S.y, S.z), a = make_float3(A.x, A.y, A.z);
    const float3 b = sp_cross3(s, a);
    const float3 L = sp_add3(sp_mul3(a, cs), sp_mul3(b, sn));          // long axis
    const float3 N = sp_cross3(L, s);                                  // face normal
    const float3 eye = make_float3(P[22], P[23], P[24]);
    float3 V = sp_sub3(eye, p);
    V = sp_mul3(V, rsqrtf(sp_dot3(V, V) + 1e-12f));
    const float ndv = sp_dot3(N, V);
    const float face = fabsf(ndv);
    const float bright = 0.14f + 0.86f * __powf(face, 0.7f) + P[21] * __powf(face, 28.f);
    const float side = sp_smooth(-0.3f, 0.3f, ndv);                    // 1 front face, 0 back face
    const float act = sp_clamp01((P[16] - A.w) * 30.f);                // active-count rank fade
    // depth of field about the focus distance; energy ~ geometric area (same surface brightness at any depth)
    const float3 fw = make_float3(P[34], P[35], P[36]);
    const float z = fmaxf(sp_dot3(sp_sub3(p, eye), fw), 0.05f);
    const float focus = P[12], ps = P[14];
    const float rref = ps * focus / z;
    const float rg = fminf(rref * S.w, P[37]);
    float coc = z > focus ? fminf(P[38] * (1.f - focus / z), P[39]) : fminf(P[40] * (focus / z - 1.f), P[41]);
    coc *= P[13];
    const float rho = sqrtf(rg * rg + coc * coc);
    const float smul = rho / fmaxf(rref, 1e-4f);
    const float ea = __powf(fmaxf(rg, 0.25f) / ps, 1.7f);
    const float w = fade * act * bright * ea * (1.f + 0.35f * g);
    const float hl = P[15] * S.w;
    pos2[2 * i] = make_float4(p.x - L.x * hl, p.y - L.y * hl, p.z - L.z * hl, 0.56f * w);
    pos2[2 * i + 1] = make_float4(p.x + L.x * hl, p.y + L.y * hl, p.z + L.z * hl, 0.44f * w);
    sz2[2 * i] = smul;
    sz2[2 * i + 1] = smul;
    const float tw = m.z;
    float3 nb = sp_mix3(make_float3(0.97f, 0.70f, 0.80f), make_float3(1.f, 0.46f, 0.64f), side);
    float3 nt = sp_mix3(make_float3(0.99f, 0.92f, 0.95f), make_float3(1.f, 0.78f, 0.86f), side);
    const float3 wh = make_float3(1.f, 0.96f, 0.97f);
    nb = sp_mix3(nb, wh, 0.5f * tw);
    nt = sp_mix3(nt, wh, 0.5f * tw);
    const float ti = P[20] + tw * 0.35f * P[19] + (1.f - side) * 0.08f;
    const float3 cb = sk_tone(P[18], nb, pal(ti, P + 52)), ct = sk_tone(P[18], nt, pal(ti + 0.07f, P + 52));
    float* rb = rgb2 + 6 * (size_t)i;
    rb[0] = sp_lin(cb.x); rb[1] = sp_lin(cb.y); rb[2] = sp_lin(cb.z);
    rb[3] = sp_lin(ct.x); rb[4] = sp_lin(ct.y); rb[5] = sp_lin(ct.z);
}

// ---- backdrop
__device__ __forceinline__ float4 sk_tex(const uchar4* __restrict__ t, float fx, float fy, int W, int H) {
    fx -= 0.5f; fy -= 0.5f;
    const float flx = floorf(fx), fly = floorf(fy);
    const float tx = fx - flx, ty = fy - fly;
    const int x0 = min(max((int)flx, 0), W - 1), x1 = min(max((int)flx + 1, 0), W - 1);
    const int y0 = min(max((int)fly, 0), H - 1), y1 = min(max((int)fly + 1, 0), H - 1);
    const uchar4 a = t[y0 * W + x0], b = t[y0 * W + x1], c = t[y1 * W + x0], d = t[y1 * W + x1];
    const float u = 1.f - tx, v = 1.f - ty, k = 1.f / 255.f;
    return make_float4((v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x)) * k,
                       (v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y)) * k,
                       (v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z)) * k,
                       (v * (u * a.w + tx * b.w) + ty * (u * c.w + tx * d.w)) * k);
}
__device__ __forceinline__ float4 sk_mask(const uchar4* __restrict__ A, const uchar4* __restrict__ B, float fx,
                                          float fy, int W, int H, float k) {
    if (k <= 0.002f) return sk_tex(A, fx, fy, W, H);
    if (k >= 0.998f) return sk_tex(B, fx, fy, W, H);
    const float4 a = sk_tex(A, fx, fy, W, H), b = sk_tex(B, fx, fy, W, H);
    return make_float4(a.x + (b.x - a.x) * k, a.y + (b.y - a.y) * k, a.z + (b.z - a.z) * k, a.w + (b.w - a.w) * k);
}
__device__ __forceinline__ float3 sk_c3(const float* P, int o) { return make_float3(P[o], P[o + 1], P[o + 2]); }

extern "C" __global__ void sk_backdrop(uchar4* __restrict__ out, const int W, const int H,
                                       const uchar4* __restrict__ mA, const uchar4* __restrict__ mB, const int MW,
                                       const int MH, const float* __restrict__ P, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float fx = x + 0.5f + P[0], fy = y + 0.5f + P[1];         // mask coords (margins + camera shift)
    const float v = (fy - P[2]) / P[55];                             // 0 top .. 1 bottom of the composed frame
    const float k = P[3];
    const float4 s0 = sk_mask(mA, mB, fx, fy, MW, MH, k);
    const float sw = P[4] * fmaxf(1.15f - v, 0.f);                   // tree sway, more towards the top
    const float dx = sw * __sinf(P[6] + fy * P[7] + fx * 0.37f * P[7]);
    const float dy = 0.35f * sw * __cosf(1.3f * P[6] + fx * P[7]);
    const float4 s1 = sk_mask(mA, mB, fx + dx, fy + dy, MW, MH, k);
    const float4 s2 = sk_mask(mA, mB, fx + dx + P[8], fy + dy + P[9], MW, MH, k);
    // sky
    float3 c = sp_mix3(sk_c3(P, 14), sk_c3(P, 17), __powf(sp_clamp01(v / 0.72f), 1.4f));
    // stars (smooth sub-pixel points in 3 px cells)
    if (P[39] > 0.f && v < 0.72f) {
        const float cell = P[56];
        const int ix = (int)floorf(fx / cell), iy = (int)floorf(fy / cell);
        const float h = hash1((unsigned int)ix, (unsigned int)iy, seed);
        if (h > 0.986f) {
            const float sx = (ix + 0.25f + 0.5f * hash1((unsigned int)ix, (unsigned int)iy, seed + 1u)) * cell;
            const float sy = (iy + 0.25f + 0.5f * hash1((unsigned int)ix, (unsigned int)iy, seed + 2u)) * cell;
            const float d2 = ((fx - sx) * (fx - sx) + (fy - sy) * (fy - sy)) / (cell * cell * 0.04f);
            const float hb = hash1((unsigned int)ix, (unsigned int)iy, seed + 3u);
            const float tw = 0.65f + 0.35f * __sinf(P[40] * (0.6f + hb) + 40.f * hb);
            const float st = P[39] * __expf(-d2) * (0.25f + 0.75f * hb * hb) * tw * (1.f - sp_smooth(0.45f, 0.72f, v));
            c = sp_add3(c, make_float3(st * 0.9f, st * 0.92f, st));
        }
    }
    // moon: disc with soft edge, maria, halo
    {
        const float mx = fx - P[10], my = fy - P[11];
        const float md = sqrtf(mx * mx + my * my) / P[12];
        const float e = fmaxf(md - 1.f, 0.f);
        const float halo = P[13] * (0.5f * __expf(-e * 2.6f) + 0.5f * __expf(-e * 0.42f));
        c = sp_add3(c, sp_mul3(sk_c3(P, 35), halo));
        const float disc = sp_smooth(1.f, 1.f - 1.5f / P[12], md);
        if (disc > 0.f) {
            const float mar = vnoise(mx * 3.2f / P[12] + 7.f, my * 3.2f / P[12] + 3.f, seed + 9u);
            const float tex = 0.78f + 0.22f * sp_smooth(0.35f, 0.7f, mar) + 0.06f * (1.f - md);
            c = sp_mix3(c, sp_mul3(sk_c3(P, 35), tex), disc * P[38]);
        }
    }
    // far range + snow cap
    float3 far = sp_mix3(sk_c3(P, 20), sk_c3(P, 41), 0.45f * sp_smooth(0.52f, 0.74f, v));
    const float snow_y = P[46] + P[47] * __sinf(fx * P[48]) + 0.6f * P[47] * __sinf(fx * P[48] * 2.7f + 1.f);
    const float snow = sp_smooth(snow_y + 2.f, snow_y - 2.f, fy);
    far = sp_mix3(far, sk_c3(P, 49), snow * 0.9f);
    c = sp_mix3(c, far, s0.x);
    // mist band between the far range and the hill
    const float mist = P[44] * sp_smooth(0.58f, 0.76f, v) * (1.f - sp_smooth(0.84f, 0.98f, v))
                       * (0.6f + 0.4f * vnoise(fx * P[52] * 0.15f + P[45], fy * P[52] * 0.6f, seed + 21u));
    c = sp_mix3(c, sk_c3(P, 41), mist);
    // near hill + landmark, then wood, then canopy
    c = sp_mix3(c, sp_mix3(sk_c3(P, 23), sk_c3(P, 41), 0.25f * mist), s0.y);
    c = sp_mix3(c, sk_c3(P, 26), s1.z);
    const float rim = sp_clamp01((s1.w - s2.w) * 2.2f);
    const float spk = vnoise((fx + dx) * P[52], (fy + dy) * P[52], seed + 5u);
    const float lit = sp_clamp01(0.32f + 1.1f * rim + 0.45f * (spk - 0.5f) + 0.18f * (0.6f - v));
    const float3 can = sp_mul3(sp_mix3(sk_c3(P, 32), sk_c3(P, 29), lit), P[57]);
    c = sp_mix3(c, can, s1.w);
    // vignette, gain
    const float vx = (x + 0.5f) / W - 0.5f, vy = (y + 0.5f) / H - 0.5f;
    c = sp_mul3(c, P[53] * (1.f - P[54] * (vx * vx + vy * vy)));
    const float dth = (hash1((unsigned int)x, (unsigned int)y, 77u) - 0.5f);
    out[y * W + x] = make_uchar4((unsigned char)fminf(fmaxf(c.x * 255.f + 0.5f + dth, 0.f), 255.f),
                                 (unsigned char)fminf(fmaxf(c.y * 255.f + 0.5f + dth, 0.f), 255.f),
                                 (unsigned char)fminf(fmaxf(c.z * 255.f + 0.5f + dth, 0.f), 255.f), 255);
}
"""


def _mod():
    return raw_module(_SRC, SPLAT3D_OPTIONS)


# ------------------------------------------------------------------------------------------- host colour helpers
def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _lerp3(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def _lum(c) -> float:
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _pal_host(coeffs, t: float):
    a, b, c, d = coeffs[0:3], coeffs[3:6], coeffs[6:9], coeffs[9:12]
    return tuple(min(max(a[i] + b[i] * math.cos(2 * math.pi * (c[i] * t + d[i])), 0.0), 1.0) for i in range(3))


def _tint(c, pc, k: float):
    """Pull colour c towards the palette colour pc at c's luminance."""
    s = _lum(c) / max(_lum(pc), 0.04)
    t = tuple(min(max(pc[i] * s, 0.0), 1.0) for i in range(3))
    return _lerp3(c, t, k)


def _tone(k: float, nat, pc):
    """Host twin of sk_tone: 0 natural .. 0.5 white .. 1 neon palette colour."""
    c0 = _lerp3(nat, pc, 0.3)
    m = max(nat)
    c1 = _lerp3((m, m * 0.97, m * 0.985), tuple(v * m for v in pc), 0.12)
    L = _lum(pc)
    s = [max(L + (v - L) * 1.7, 0.0) for v in pc]
    ms = max(max(s), 0.05)
    s = tuple(min(v * m / ms, 1.0) for v in s)
    return _lerp3(c0, c1, k * 2.0) if k < 0.5 else _lerp3(c1, s, k * 2.0 - 1.0)


def _tod(t: float, dusk, night, day):
    if t <= 0.5:
        return _lerp3(dusk, night, _ss(0.0, 1.0, t * 2.0))
    return _lerp3(night, day, _ss(0.0, 1.0, t * 2.0 - 1.0))


def _tod1(t: float, v3) -> float:
    """Scalar dusk / night / day value at time-of-day t."""
    if t <= 0.5:
        return v3[0] + (v3[1] - v3[0]) * _ss(0.0, 1.0, t * 2.0)
    return v3[1] + (v3[2] - v3[1]) * _ss(0.0, 1.0, t * 2.0 - 1.0)


#            dusk                  night                  day
ZEN = ((0.17, 0.10, 0.32), (0.015, 0.02, 0.06), (0.26, 0.48, 0.82))
HOR = ((1.00, 0.56, 0.42), (0.09, 0.11, 0.22), (0.80, 0.87, 0.95))
FAR = ((0.46, 0.27, 0.42), (0.05, 0.06, 0.13), (0.52, 0.60, 0.76))
MID = ((0.13, 0.06, 0.14), (0.012, 0.012, 0.03), (0.24, 0.30, 0.38))
WOOD = ((0.05, 0.025, 0.045), (0.004, 0.004, 0.01), (0.15, 0.10, 0.10))
CAN_LIT = ((1.00, 0.62, 0.66), (0.44, 0.31, 0.48), (1.00, 0.80, 0.88))
CAN_SH = ((0.36, 0.14, 0.26), (0.06, 0.045, 0.09), (0.78, 0.50, 0.64))
MOON = ((1.00, 0.90, 0.78), (0.96, 0.96, 1.00), (0.95, 0.97, 1.00))
MIST = ((0.95, 0.62, 0.58), (0.14, 0.16, 0.28), (0.88, 0.92, 0.97))
SNOW = ((1.00, 0.80, 0.78), (0.55, 0.58, 0.70), (0.97, 0.98, 1.00))
MOON_A = (0.9, 1.0, 0.5)
HALO = (0.22, 0.5, 0.06)
STARS = (0.3, 1.0, 0.0)
MIST_S = (0.45, 0.4, 0.38)


# ------------------------------------------------------------------------------------------- backdrop masks (host)
_MASK_CACHE: dict = {}


def _n1(x, vals):
    i = np.floor(x).astype(np.int64)
    f = x - i
    f = f * f * (3.0 - 2.0 * f)
    n = len(vals)
    return vals[i % n] * (1.0 - f) + vals[(i + 1) % n] * f


def _bbox(m, cx0, cy0, cx1, cy1):
    H, W = m.shape
    x0, x1 = max(int(math.floor(cx0)), 0), min(int(math.ceil(cx1)) + 1, W)
    y0, y1 = max(int(math.floor(cy0)), 0), min(int(math.ceil(cy1)) + 1, H)
    return x0, x1, y0, y1


def _disc(m, cx, cy, r, val=1.0):
    x0, x1, y0, y1 = _bbox(m, cx - r - 1.5, cy - r - 1.5, cx + r + 1.5, cy + r + 1.5)
    if x0 >= x1 or y0 >= y1:
        return
    yy = np.arange(y0, y1, dtype=np.float32)[:, None] + 0.5 - cy
    xx = np.arange(x0, x1, dtype=np.float32)[None, :] + 0.5 - cx
    cov = np.clip(r - np.sqrt(xx * xx + yy * yy) + 0.5, 0.0, 1.0) * val
    sub = m[y0:y1, x0:x1]
    np.maximum(sub, cov, out=sub)


def _capsule(m, ax, ay, bx, by, ra, rb):
    r = max(ra, rb)
    x0, x1, y0, y1 = _bbox(m, min(ax, bx) - r - 1.5, min(ay, by) - r - 1.5, max(ax, bx) + r + 1.5,
                           max(ay, by) + r + 1.5)
    if x0 >= x1 or y0 >= y1:
        return
    py = np.arange(y0, y1, dtype=np.float32)[:, None] + 0.5 - ay
    px = np.arange(x0, x1, dtype=np.float32)[None, :] + 0.5 - ax
    dx, dy = bx - ax, by - ay
    t = np.clip((px * dx + py * dy) / (dx * dx + dy * dy + 1e-9), 0.0, 1.0)
    ex, ey = px - t * dx, py - t * dy
    cov = np.clip(ra + (rb - ra) * t - np.sqrt(ex * ex + ey * ey) + 0.5, 0.0, 1.0)
    sub = m[y0:y1, x0:x1]
    np.maximum(sub, cov, out=sub)


def _poly(m, pts, ss=3):
    pts = [(float(a), float(b)) for a, b in pts]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1, y0, y1 = _bbox(m, min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1)
    if x0 >= x1 or y0 >= y1:
        return
    acc = np.zeros((y1 - y0, x1 - x0), np.float32)
    n = len(pts)
    for oy in range(ss):
        Y = np.arange(y0, y1, dtype=np.float64)[:, None] + (oy + 0.5) / ss
        for ox in range(ss):
            X = np.arange(x0, x1, dtype=np.float64)[None, :] + (ox + 0.5) / ss
            inside = np.zeros((y1 - y0, x1 - x0), bool)
            for k in range(n):
                xa, ya = pts[k]
                xb, yb = pts[(k + 1) % n]
                if ya == yb:
                    continue
                cond = ((ya > Y) != (yb > Y)) & (X < (xb - xa) * (Y - ya) / (yb - ya) + xa)
                inside ^= cond
            acc += inside
    sub = m[y0:y1, x0:x1]
    np.maximum(sub, acc / (ss * ss), out=sub)


def _rect(m, x0, y0, x1, y1):
    _poly(m, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def _pagoda(m, P, cx, base, s):
    """Five-tier pagoda silhouette (normalised units -> pixels via P)."""
    _rect(m, *P(cx - 0.050 * s, base - 0.010 * s), *P(cx + 0.050 * s, base + 0.02))
    yb = base - 0.010 * s
    for k in range(5):
        hb = 0.026 * s * (1.0 - 0.06 * k)
        wb = 0.031 * s * (1.0 - 0.11 * k)
        wr = wb + 0.024 * s * (1.0 - 0.07 * k)
        tr, ut = 0.011 * s, 0.008 * s
        yt = yb - hb
        _rect(m, *P(cx - wb, yt), *P(cx + wb, yb + 0.002 * s))
        _poly(m, [P(cx - wr, yt - ut), P(cx - 0.72 * wr, yt + 0.004 * s), P(cx + 0.72 * wr, yt + 0.004 * s),
                  P(cx + wr, yt - ut), P(cx + 0.5 * wr, yt - tr), P(cx - 0.5 * wr, yt - tr)])
        yb = yt - 0.6 * tr
    top = yb - 0.004 * s
    _rect(m, *P(cx - 0.0030 * s, top - 0.055 * s), *P(cx + 0.0030 * s, top + 0.002 * s))
    for j in range(5):
        yy = top - 0.010 * s - 0.008 * s * j
        _rect(m, *P(cx - 0.0075 * s, yy - 0.0025 * s), *P(cx + 0.0075 * s, yy))
    x, y = P(cx, top - 0.058 * s)
    _disc(m, x, y, max(0.005 * s * (P(1, 0)[0] - P(0, 0)[0]), 1.0))


def _torii(m, P, cx, base, s):
    """Torii gate silhouette."""
    for sgn in (-1.0, 1.0):
        _poly(m, [P(cx + sgn * 0.066 * s - 0.0075 * s, base + 0.02), P(cx + sgn * 0.066 * s + 0.0075 * s, base + 0.02),
                  P(cx + sgn * 0.060 * s + 0.0062 * s, base - 0.150 * s),
                  P(cx + sgn * 0.060 * s - 0.0062 * s, base - 0.150 * s)])
    _rect(m, *P(cx - 0.092 * s, base - 0.124 * s), *P(cx + 0.092 * s, base - 0.115 * s))
    _rect(m, *P(cx - 0.0055 * s, base - 0.150 * s), *P(cx + 0.0055 * s, base - 0.120 * s))
    _rect(m, *P(cx - 0.100 * s, base - 0.152 * s), *P(cx + 0.100 * s, base - 0.145 * s))
    top, bot = [], []
    for j in range(25):
        u = -1.0 + 2.0 * j / 24
        lift = 0.016 * s * abs(u) ** 2.6
        top.append(P(cx + u * 0.122 * s, base - 0.168 * s - lift))
        bot.append(P(cx + u * 0.112 * s, base - 0.155 * s - 0.8 * lift))
    _poly(m, top + bot[::-1])


def _tree(rng, x, y, ang, length, radius, depth, segs, blooms):
    stack = [(x, y, ang, length, radius, depth, 0)]
    while stack:
        x, y, a, L, r, dp, lvl = stack.pop()
        nsub = 4
        r_end = r * 0.72
        for k in range(nsub):
            a += rng.normal(0.0, 0.10)
            if lvl >= 2:                                   # spreading, drooping cherry limbs
                a += 0.06 * (1.0 if a >= 0.0 else -1.0)
            a = float(np.clip(a, -2.1, 2.1))
            nx, ny = x + math.sin(a) * L / nsub, y - math.cos(a) * L / nsub
            segs.append((x, y, nx, ny, r + (r_end - r) * k / nsub, r + (r_end - r) * (k + 1) / nsub))
            if dp <= 2 and k >= 1:
                blooms.append((nx, ny, dp))
            x, y = nx, ny
        if dp == 0:
            blooms.append((x, y, -1))
            continue
        nch = 2 if (lvl < 2 or rng.random() < 0.5) else 3
        spread = rng.uniform(0.36, 0.62)
        offs = (-spread, spread) if nch == 2 else (-spread, rng.normal(0.0, 0.12), spread)
        for j, off in enumerate(offs):
            mid = nch == 3 and j == 1
            stack.append((x, y, a + off + rng.normal(0.0, 0.07), L * rng.uniform(0.70, 0.84) * (0.8 if mid else 1.0),
                          r_end * rng.uniform(0.62, 0.74), dp - 1, lvl + 1))


def _box_blur(a, r):
    """3 passes of a separable box filter (~gaussian) on (H, W, C) float32, edge-clamped."""
    if r < 1:
        return a
    for _ in range(3):
        for ax in (0, 1):
            pad = [(0, 0)] * a.ndim
            pad[ax] = (r + 1, r)
            c = np.cumsum(np.pad(a, pad, mode="edge"), axis=ax, dtype=np.float32)
            n = a.shape[ax]
            hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=ax)
            lo = np.take(c, np.arange(0, n), axis=ax)
            a = (hi - lo) / (2 * r + 1)
    return a


def _build_masks(bw: int, bh: int, seed: int, landmark: str):
    key = (bw, bh, seed, landmark)
    if key in _MASK_CACHE:
        return _MASK_CACHE[key]
    rng = np.random.default_rng(seed + 7)
    mx, my = int(round(0.06 * bw)), int(round(0.06 * bh))
    MW, MH = bw + 2 * mx, bh + 2 * my
    A = bw / bh
    U = float(bh)

    def P(xn, yn):
        return mx + xn * U, my + yn * U

    R = np.zeros((MH, MW), np.float32)
    G = np.zeros_like(R)
    B = np.zeros_like(R)
    C = np.zeros_like(R)
    xs = (np.arange(MW, dtype=np.float64) + 0.5 - mx) / U
    Y = (np.arange(MH, dtype=np.float64)[:, None] + 0.5)
    v1, v2, v3, v4 = (rng.uniform(-1.0, 1.0, 256) for _ in range(4))

    # far range with a volcano (flat-topped cone) right of centre
    xf = 0.60 * A
    sh = np.clip(1.0 - np.abs(xs - xf) / 0.64, 0.0, 1.0)
    cone = np.minimum(0.31 * sh ** 1.7, 0.27)
    ridge = np.minimum(0.69 - 0.030 * _n1(xs * 3.0 + 11.0, v1) - 0.014 * _n1(xs * 9.0, v2), 0.705 - cone)
    R[:] = np.clip(Y - (my + ridge * U)[None, :] + 0.5, 0.0, 1.0)
    peak = 0.705 - 0.27

    # near hill (tree line) with a rise under the landmark
    xl = 0.27 * A
    hill = (0.835 - 0.035 * _n1(xs * 2.2 + 3.0, v3) - 0.075 * np.exp(-((xs - xl) / 0.20) ** 2)
            - 0.010 * np.abs(_n1(xs * 30.0, v4)))
    G[:] = np.clip(Y - (my + hill * U)[None, :] + 0.5, 0.0, 1.0)
    hill_at = lambda x: float(np.interp(x, xs, hill))                     # noqa: E731
    x = -0.05
    while x < A + 0.05:                                                   # distant blossom crowns / dark crowns
        if abs(x - xl) > 0.09:
            r = rng.uniform(0.008, 0.019)
            cx, cy = P(x, hill_at(x) - 0.3 * r)
            if rng.random() < 0.5:
                _disc(C, cx, cy, r * U, 0.6)
            else:
                _disc(G, cx, cy, r * U * 0.8, 1.0)
        x += rng.uniform(0.012, 0.024)
    base = hill_at(xl) + 0.012
    if landmark == "pagoda":
        _pagoda(G, P, xl, base, 1.0)
    elif landmark == "torii":
        _torii(G, P, xl, base + 0.01, 1.25)

    # cherry trees framing the top (procedural branching)
    moon = (0.80 * A, 0.19)
    moon_r = 0.062
    segs, blooms = [], []
    _tree(rng, 0.08 * A, 1.06, 0.42, 0.30, 0.030, 6, segs, blooms)
    _tree(rng, A - 0.04, 1.06, -0.50, 0.25, 0.024, 6, segs, blooms)
    for ax_, ay_, bx_, by_, ra, rb in segs:
        a0, b0 = P(ax_, ay_)
        a1, b1 = P(bx_, by_)
        _capsule(B, a0, b0, a1, b1, max(ra * U, 0.5), max(rb * U, 0.5))
    for bx_, by_, dp in blooms:
        for _ in range(3 if dp == -1 else 2):
            r = rng.uniform(0.011, 0.027)
            cx, cy = bx_ + rng.normal(0.0, 0.018), by_ + rng.normal(-0.004, 0.014)
            if math.hypot(cx - moon[0], cy - moon[1]) < 2.3 * moon_r + r:
                continue
            _disc(C, *P(cx, cy), r * U, rng.uniform(0.8, 1.0))

    sharp = np.stack([R, G, B, C], axis=-1)
    blur = _box_blur(sharp, max(1, int(round(0.004 * bh))))
    to8 = lambda a: np.ascontiguousarray((np.clip(a, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8))   # noqa: E731
    mx_m, my_m = P(*moon)
    res = dict(sharp=to8(sharp), blur=to8(blur), mx=mx, my=my, MW=MW, MH=MH, moon=(mx_m, my_m), moon_r=moon_r * U,
               snow_y=my + (peak + 0.07) * U)
    if len(_MASK_CACHE) > 6:
        _MASK_CACHE.clear()
    _MASK_CACHE[key] = res
    return res


class SakuraStorm(Source):
    name = "sakura_storm"
    label = "Sakura storm"
    description = ("A cherry-blossom storm: petals ride a curl-noise wind in 3D with depth of field before blossoming "
                   "trees, a pagoda, a volcano and the moon; beats send gusts sweeping petals past the camera.")
    tags = {"family": "organic", "dims": "3d", "toolkit": "splat3d", "technique": ["particles", "noise"],
            "moods": ["elegant", "dreamy", "euphoric"], "style": "lively", "energy": [0.1, 0.9]}
    params = {
        **standard_scene_params(palette="candy", render_scale=0.5, period=0.6, drift=0.02),
        "petal_density": num(0.1, 1.0, 0.6, "Petal density", tier="regional", basic=True),
        "wind_turbulence": num(0.0, 1.0, 0.5, "Wind turbulence", tier="regional"),
        "dof": num(0.0, 1.0, 0.55, "Depth of field", tier="regional"),
        "backdrop": num(0.0, 1.0, 0.3, "Backdrop: dusk .. night .. day", tier="regional"),
        "petal_size": num(0.5, 2.5, 1.0, "Petal size", unit="×", tier="regional"),
        "petal_tone": num(0.0, 1.0, 0.2, "Petal colour: pink .. white .. neon", tier="regional"),
        "focus": num(2.0, 10.0, 4.5, "Focus distance", tier="regional"),
        "wind": num(0.0, 3.0, 1.0, "Wind", unit="×", tier="realtime", bind="rate"),
        "gust": num(0.0, 2.0, 1.0, "Beat gust strength", tier="realtime", bind="level"),
        "landmark": enum(["pagoda", "torii", "none"], "pagoda", "Landmark", tier="structural"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        seed = int(params["seed"])
        self.seed = seed
        rng = np.random.default_rng(seed)
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.sp = Splat3D(ctx, self.w, self.h, max_points=2 * N_MAX, max_lines=16, alpha=True)
        self.aspect = ctx.width / ctx.height
        self.tx = TAN_HALF * self.aspect * 1.15
        self.ty = TAN_HALF * 1.25
        self.slope = math.tan(PITCH0)

        # petals: depth pdf ~ d (between uniform depth and uniform volume), uniform across the frustum
        n = N_MAX
        d = np.sqrt(rng.random(n) * (D1 * D1 - D0 * D0) + D0 * D0)
        st = np.empty((n, 4), np.float32)
        st[:, 0] = rng.uniform(-1.0, 1.0, n) * (d * self.tx + MARGIN)
        st[:, 1] = d * self.slope + rng.uniform(-1.0, 1.0, n) * (d * self.ty + MARGIN)
        st[:, 2] = d
        st[:, 3] = 1.0
        ph = np.empty((n, 4), np.float32)
        ph[:, 0] = rng.uniform(0.0, 2.0 * math.pi, n)
        ph[:, 1] = rng.uniform(0.55, 1.7, n) * np.where(rng.random(n) < 0.5, -1.0, 1.0)
        ph[:, 2] = rng.random(n) ** 1.5
        ph[:, 3] = rng.uniform(0.6, 1.5, n)
        s = rng.normal(size=(n, 3))
        s /= np.linalg.norm(s, axis=1, keepdims=True) + 1e-9
        a = np.cross(s, rng.normal(size=(n, 3)))
        a /= np.linalg.norm(a, axis=1, keepdims=True) + 1e-9
        axs = np.empty((n, 4), np.float32)
        axs[:, :3], axs[:, 3] = s, rng.uniform(0.75, 1.3, n)
        axa = np.empty((n, 4), np.float32)
        axa[:, :3], axa[:, 3] = a, (np.arange(n) + 0.5) / n
        self.st, self.ph = cp.asarray(st), cp.asarray(ph)
        self.axs, self.axa = cp.asarray(axs), cp.asarray(axa)
        self.pos2 = cp.zeros((2 * n, 4), cp.float32)
        self.rgb2 = cp.zeros((2 * n, 3), cp.float32)
        self.sz2 = cp.ones(2 * n, cp.float32)
        self.pp = cp.zeros(64, cp.float32)
        self.pp_h = np.zeros(64, np.float32)

        # backdrop
        mk = _build_masks(self.w, self.h, seed, str(params["landmark"]))
        self.mk = mk
        self.mA, self.mB = cp.asarray(mk["sharp"]), cp.asarray(mk["blur"])
        self.bg = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.pb = cp.zeros(64, cp.float32)
        self.pb_h = np.zeros(64, np.float32)

        mod = _mod()
        self.k_petals = mod.get_function("sk_petals")
        self.k_bg = mod.get_function("sk_backdrop")

        self.e_s = None
        self.drift = 0.0
        self.off = [0.0, 0.0, 0.0]
        self.cam1 = rng.uniform(0.0, 6.28)
        self.cam2 = rng.uniform(0.0, 6.28)
        self.sway = 0.0
        self.twinkle = 0.0
        self.mist = 0.0
        self.prev_pulse = 0.0
        self.clock = 0.0
        self.last_gust = -1.0
        self.gusts = [[-9.0, 0.0], [-9.0, 0.0]]          # [front u, amplitude]
        self.frame = 0

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        dt = min(max(float(dt), 0.0), 0.1)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        a = 1.0 - math.exp(-dt / 0.4) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s
        sdt = dt * spd
        self.clock += dt
        self.frame += 1

        # ---- gusts: launch on the pulse's rising edge into the weaker slot; fronts sweep left -> right and decay
        if pulse > 0.3 and pulse - self.prev_pulse > 0.15 and self.clock - self.last_gust > 0.12:
            k = 0 if self.gusts[0][1] < self.gusts[1][1] else 1
            self.gusts[k] = [-1.6, pulse * float(p["gust"])]
            self.last_gust = self.clock
        self.prev_pulse = pulse
        gsum = 0.0
        for g in self.gusts:
            if g[1] > 1e-3:
                g[0] += sdt * (1.4 + 0.8 * es)
                g[1] *= math.exp(-dt / 1.6)
                if g[0] > 2.2:
                    g[1] *= math.exp(-dt / 0.2)
                gsum += g[1] * _ss(-1.8, -1.0, g[0]) * (1.0 - _ss(1.2, 2.0, g[0]))
            else:
                g[1] = 0.0

        # ---- rates
        wind = float(p["wind"]) * (0.45 + 2.3 * e) * (1.0 + 0.5 * pulse)
        turb = float(p["wind_turbulence"]) * (0.9 + 1.4 * es) + 0.08
        scale = 0.32
        self.off[0] += wind * scale * sdt * 0.85
        self.off[1] += 0.05 * sdt
        self.off[2] += 0.03 * sdt
        self.off = [math.fmod(o, 4096.0) for o in self.off]
        flutter = 3.6 * (0.55 + 1.5 * e) * (1.0 + 0.6 * pulse)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)
        self.cam1 += sdt * (0.11 + 0.05 * es)
        self.cam2 += sdt * (0.07 + 0.03 * es)
        self.sway += sdt * (0.7 + 1.3 * e + 1.2 * gsum)
        self.twinkle += sdt * 2.0
        self.mist += sdt * (0.05 + 0.1 * e)

        # ---- camera (rotation only; the backdrop shifts by the same angles)
        amp = 0.020 * (0.6 + 0.9 * es)
        yaw = amp * (math.sin(self.cam1) + 0.35 * math.sin(2.3 * self.cam1 + 1.0))
        pitch = PITCH0 + 0.5 * amp * math.sin(self.cam2)
        fwd = (math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch))
        cam = look_at((0.0, 0.0, 0.0), fwd, fov_deg=FOV)

        sess = self.session_palette()
        coeffs = [float(v) for v in np.asarray(PALETTES[p["palette"]] if sess is None else sess,
                                               np.float64).reshape(-1)]
        period = float(p["palette_period"])
        tone = float(p["petal_tone"])
        focus = float(p["focus"])
        s_acc = self.sp.s
        ps = 2.4 * float(p["petal_size"])

        # ---- petals
        P = self.pp_h
        P[0] = sdt
        P[1] = wind
        P[2] = turb
        P[3] = scale
        P[4:7] = self.off
        P[7] = 0.22 + 0.1 * e
        P[11] = sdt * flutter
        P[12] = focus
        P[13] = float(p["dof"])
        P[14] = ps
        P[15] = 0.55 * ps * focus / F_REF
        n_active = float(p["petal_density"]) * (0.4 + 0.6 * es)
        P[16] = n_active
        P[18] = tone
        P[19] = period
        P[20] = self.drift
        P[21] = 0.7
        P[22:25] = 0.0
        P[25], P[26], P[27], P[28], P[29], P[30] = self.tx, self.ty, MARGIN, D0, D1, FADE_RATE
        P[32], P[33] = 0.45, 0.9
        P[34:37] = fwd
        P[37] = 8.0 / max(s_acc, 1e-3)
        P[38], P[39], P[40], P[41] = 4.0, 3.4, 3.0, 8.0 / max(s_acc, 1e-3)
        P[42], P[43] = self.gusts[0]
        P[44], P[45] = self.gusts[1]
        P[46], P[47] = 0.45, 3.2
        P[48] = self.slope
        P[52:64] = coeffs
        self.pp.set(P)
        n = N_MAX
        self.k_petals(((n + 255) // 256,), (256,),
                      (self.st, self.ph, self.axs, self.axa, self.pos2, self.rgb2, self.sz2, self.pp, np.int32(n),
                       np.uint32(self.seed & 0x7FFFFFFF), np.uint32(self.frame & 0x7FFFFFFF)))

        # ---- backdrop into the internal buffer, upsampled into out
        mk = self.mk
        tod = float(p["backdrop"])
        f_px = 0.5 * self.h / TAN_HALF
        pc_z = _pal_host(coeffs, self.drift + 0.55)
        pc_h = _pal_host(coeffs, self.drift + 0.25)
        pc_f = _pal_host(coeffs, self.drift + 0.40)
        pc_c = _pal_host(coeffs, self.drift + 0.10)
        Q = self.pb_h
        Q[0] = mk["mx"] - f_px * math.tan(yaw)
        Q[1] = mk["my"] - f_px * math.tan(pitch - PITCH0)
        Q[2] = mk["my"]
        Q[3] = float(p["dof"])
        Q[4] = (self.h / 540.0) * (0.5 + 1.4 * e + 2.5 * gsum)
        Q[6] = self.sway
        Q[7] = 3.0 / self.h
        Q[8], Q[9] = 2.2 * self.h / 540.0, -2.2 * self.h / 540.0
        Q[10], Q[11] = mk["moon"]
        Q[12] = mk["moon_r"]
        Q[13] = _tod1(tod, HALO)
        Q[14:17] = _tint(_tod(tod, *ZEN), pc_z, 0.35)
        Q[17:20] = _tint(_tod(tod, *HOR), pc_h, 0.3)
        Q[20:23] = _tint(_tod(tod, *FAR), pc_f, 0.2)
        Q[23:26] = _tint(_tod(tod, *MID), pc_f, 0.15)
        Q[26:29] = _tod(tod, *WOOD)
        Q[29:32] = _tone(tone, _tod(tod, *CAN_LIT), pc_c)
        Q[32:35] = _tone(tone, _tod(tod, *CAN_SH), pc_c)
        Q[35:38] = _tod(tod, *MOON)
        Q[38] = _tod1(tod, MOON_A)
        Q[39] = _tod1(tod, STARS)
        Q[40] = self.twinkle
        Q[41:44] = _tint(_tod(tod, *MIST), pc_h, 0.25)
        Q[44] = _tod1(tod, MIST_S)
        Q[45] = self.mist
        Q[46] = mk["snow_y"]
        Q[47] = 0.012 * self.h
        Q[48] = 45.0 / self.h
        Q[49:52] = _tod(tod, *SNOW)
        Q[52] = 0.35 * 540.0 / self.h
        Q[53] = min(float(p["intensity"]), 1.6)
        Q[54] = 0.55
        Q[55] = float(self.h)
        Q[56] = 3.0 * self.h / 540.0
        Q[57] = (0.88 + 0.22 * es) * (1.0 + 0.12 * gsum)
        self.pb.set(Q)
        launch_2d(self.k_bg, self.w, self.h, (self.bg, np.int32(self.w), np.int32(self.h), self.mA, self.mB,
                                              np.int32(mk["MW"]), np.int32(mk["MH"]), self.pb,
                                              np.uint32(self.seed & 0x7FFFFFFF)))
        present(self.ctx, self.bg, out)

        # ---- splat the petals and resolve over the backdrop
        sp = self.sp
        sp.clear()
        n_draw = min(N_MAX, int(math.ceil((n_active + 0.04) * N_MAX)))
        inten = 4.6 * (0.8 + 0.35 * e) * (1.0 + 0.25 * pulse)
        sp.points(self.pos2, cam=cam, n=2 * n_draw, intensity=inten, size_px=ps, depth_ref=focus, rgb=self.rgb2,
                  sizes=self.sz2, weight_in_w=True, size_range=(0.5, 64.0), near_fade=(D0, D0 + 0.35),
                  far_fade=(0.72 * D1, 0.98 * D1))
        day = _ss(0.5, 1.0, tod)
        sp.resolve(out, exposure=float(p["intensity"]), tonemap="aces", saturation=1.05,
                   glow=0.45 * (0.7 + 0.5 * e) * (1.0 + 0.3 * pulse) * (1.0 - 0.4 * day), glow_radius_px=14.0,
                   glow_wide=0.35, background=out, occlusion=1.2 + 1.8 * day, blend="screen")

    def close(self) -> None:
        if getattr(self, "sp", None) is not None:
            self.sp.close()
        self.sp = None
        self.st = self.ph = self.axs = self.axa = self.pos2 = self.rgb2 = self.sz2 = self.pp = None
        self.mA = self.mB = self.bg = self.pb = None
