"""Portal ring: a ring of sparks spins open at a point and expands past the frame edges, revealing the new picture
inside it.  Hashed tangential spark streaks with short trails whirl around the ring, embers eject outward ahead of
it, and through the ring the new scene appears with depth parallax (magnified and receding into place, lensed
towards the centre, shadowed under the ring's lip) while the old picture is shoved outward by the ring's front.

Source: Original
Author: smfx (original)
License: MIT (original)
license_class: permissive

Everything is analytic in polar coordinates around the origin, with no state:
  * ring radius R = 1.1 x progress x (distance from the origin to the farthest corner), so it clears the frame
    before progress 1 (progress is already eased by the engine);
  * sparks: three layers of angular bins (``spark_density`` sets the bin count), each layer rotating at its own
    multiple of ``spin_speed`` (phases computed on the host from progress x duration_s and wrapped).  Each bin
    holds one spark whose radial offset, streak length, thickness, brightness and hue are hashed from
    (bin, lifetime epoch, seed); its life envelope sin(pi f) crossfades it out before it reseeds, so nothing pops.
    The streak is a tangential arc whose trail points back against the spin, with a hot round head.  Each pixel
    evaluates 4 neighbouring bins per layer, and only inside the ring band;
  * embers: angular bins with two slots, each ejecting an ember from the ring at a hashed time and speed; the
    ember flies outward as a short radial streak, cooling along the palette as it fades;
  * light is tone-mapped (1 - exp(-L)) and screen-blended over the picture, so bright stills never clip hard;
    all light is weighted by sin(pi p)^0.45, the lens/shadow/push by sin(pi p).  Endpoints are early-out copies.

Binder: style wild, 2-4 s, ideal on a drop or a section change.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num

_SRC = HELPERS + PALETTE_CUDA + r"""
#define TWO_PI 6.28318531f

__device__ __forceinline__ float3 vivid(float t, const float* __restrict__ P) {
    const float3 g = pal(t, P);
    const float m = 1.f / fmaxf(fmaxf(g.x, fmaxf(g.y, g.z)), 0.15f);   // light is always saturated + bright
    return make_float3(fminf(g.x * m, 1.f), fminf(g.y * m, 1.f), fminf(g.z * m, 1.f));
}

__device__ __forceinline__ float fractf(float v) { return v - floorf(v); }

extern "C" __global__
void portal_ring(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
                 const int W, const int H, const float ox, const float oy,
                 const float R, const float w, const float envL, const float envG,
                 const float Z, const float lens, const float shadow, const float push,
                 const float T, const float s0, const float s1, const float s2,
                 const int n0, const int n1, const int n2, const float trail, const float thick,
                 const float se, const int ne, const float ev, const float emax,
                 const float hue, const unsigned int seed, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float invH = 1.f / (float)H;
    const float dx = (x + 0.5f - ox) * invH, dy = (y + 0.5f - oy) * invH;
    const float r = sqrtf(dx * dx + dy * dy);
    const float ir = r > 1e-6f ? 1.f / r : 0.f;
    const float ux = dx * ir, uy = dy * ir;
    const float dr = r - R;

    // ---------------------------------------------------------------- base picture
    const float aa = 1.2f * invH;
    const float cov = clamp01((R - r) / aa + 0.5f);
    float4 col = make_float4(0.f, 0.f, 0.f, 255.f);
    if (cov < 1.f) {                                    // old picture outside, shoved outward by the ring front
        const float dout = fmaxf(dr, 0.f);
        const float rs = fmaxf(r - push * __expf(-dout / (2.5f * w + 0.01f)), 0.f);
        col = bilin(a, ox + ux * rs * H, oy + uy * rs * H, W, H);
    }
    if (cov > 0.f) {                                    // new picture inside: depth parallax + lens + rim shadow
        const float rr = fminf(r / fmaxf(R, 1e-4f), 1.f);
        const float Ze = Z * (1.f + lens * (1.f - rr * rr));
        const float rs = r / Ze;
        float4 c = bilin(b, ox + ux * rs * H, oy + uy * rs * H, W, H);
        const float k = 1.f - shadow * __expf(-fmaxf(R - r, 0.f) / (1.6f * w + 0.006f));
        c.x *= k; c.y *= k; c.z *= k;
        col = mix4(col, c, cov);
    }

    // ---------------------------------------------------------------- light
    float Lx = 0.f, Ly = 0.f, Lz = 0.f;
    const float th_turns = atan2f(dy, dx) * (1.f / TWO_PI) + 0.5f;     // [0, 1]

    // halo around the ring (both sides) + a soft wide bloom
    {
        const float sg = 2.2f * w + 0.004f;
        const float hg = 0.55f * __expf(-(dr * dr) / (sg * sg)) + 0.22f * __expf(-fabsf(dr) / (5.f * w + 0.015f));
        const float3 hc = vivid(hue + 0.08f, P);
        Lx += hc.x * hg; Ly += hc.y * hg; Lz += hc.z * hg;
    }

    // sparks: three rotating layers of hashed tangential streaks
    if (fabsf(dr) < w + 14.f * thick + 0.004f) {
        #pragma unroll
        for (int l = 0; l < 3; ++l) {
            const int N = l == 0 ? n0 : (l == 1 ? n1 : n2);
            const float sp = l == 0 ? s0 : (l == 1 ? s1 : s2);
            const float u = fractf(th_turns - sp) * (float)N;
            const int kb = min((int)floorf(u), N - 1);
            const float binArc = TWO_PI / (float)N * fmaxf(r, 1e-4f);   // one bin, in H units at this radius
            #pragma unroll
            for (int j = -1; j <= 2; ++j) {
                int idx = kb + j;
                idx += idx < 0 ? N : 0;
                idx -= idx >= N ? N : 0;
                const unsigned int ui = (unsigned int)idx, sl = seed + 7919u * (unsigned int)l;
                const float h0 = hash1(ui, 911u, sl);
                const float lf = T * (0.9f + 0.9f * h0) + h0 * 7.f;           // staggered lifetimes
                const float epf = floorf(lf);
                const float f = lf - epf;
                const unsigned int ep = (unsigned int)(int)epf;
                const float life = __sinf(3.14159265f * f);
                const float h1 = hash1(ui, ep, sl + 1u), h2 = hash1(ui, ep, sl + 2u);
                const float h3 = hash1(ui, ep, sl + 3u), h4 = hash1(ui, ep, sl + 4u);
                const float h5 = hash1(ui, ep, sl + 5u), h6 = hash1(ui, ep, sl + 6u);
                const float rsp = fabsf(R + (h2 + h3 - 1.f) * w * 1.15f);       // radial home of the spark
                const float th = thick * (0.6f + 1.2f * h5);
                const float rd = r - rsp;
                if (fabsf(rd) > 7.f * th) continue;
                const float head = (float)(kb + j) + 0.1f + 0.8f * h1;
                const float db = head - u;                                       // > 0: behind the head
                const float arc = db * binArc;
                const float Lb = trail * (0.35f + 1.25f * h4);                   // trail length in bins
                float prof;
                float thE = th;
                if (db >= 0.f) {
                    const float s = db / Lb;
                    prof = s < 1.f ? (1.f - s) * (1.f - s) : 0.f;
                    thE = th * (1.f - 0.55f * fminf(s, 1.f));
                } else {
                    const float q = arc / (1.5f * th);
                    prof = __expf(-q * q);
                }
                const float rq = rd / thE;
                const float streak = prof * __expf(-rq * rq);
                const float gq = (arc * arc + rd * rd) / (6.f * th * th);
                const float glow = 0.9f * __expf(-gq);
                const float I = life * (0.45f + 1.15f * h6 * h6) * (streak + glow);
                if (I <= 1e-4f) continue;
                const float3 c = vivid(hue + 0.2f * h0 + 0.1f * (float)l, P);
                const float wh = 0.35f * I * I;                                  // white-hot cores
                Lx += c.x * I + wh; Ly += c.y * I + wh; Lz += c.z * I + wh;
            }
        }
    }

    // embers ejecting outward from the ring
    if (ne > 0 && dr > -w - 0.01f && dr < emax) {
        const float u = fractf(th_turns - se) * (float)ne;
        const int kb = min((int)floorf(u), ne - 1);
        const float binArc = TWO_PI / (float)ne * fmaxf(r, 1e-4f);
        #pragma unroll
        for (int j = -1; j <= 1; ++j) {
            int idx = kb + j;
            idx += idx < 0 ? ne : 0;
            idx -= idx >= ne ? ne : 0;
            #pragma unroll
            for (int slot = 0; slot < 2; ++slot) {
                const unsigned int ui = (unsigned int)idx, sl = seed + 104729u + 31u * (unsigned int)slot;
                const float h0 = hash1(ui, 577u, sl);
                const float rate = 0.6f + 0.6f * h0;                              // births per second
                const float lf = T * rate + h0 * 5.f + 0.5f * (float)slot;
                const float epf = floorf(lf);
                const float f = lf - epf;
                const unsigned int ep = (unsigned int)(int)epf;
                const float h1 = hash1(ui, ep, sl + 1u), h2 = hash1(ui, ep, sl + 2u);
                const float h3 = hash1(ui, ep, sl + 3u), h4 = hash1(ui, ep, sl + 4u);
                const float h5 = hash1(ui, ep, sl + 5u);
                const float v = ev * (0.45f + 1.1f * h2);                          // H units per second
                const float age = f / rate;
                const float re = R + w * (h3 - 0.5f) + v * age;
                const float rd = r - re;
                const float size = (0.0016f + 0.0032f * h4) * (0.6f + 0.4f * fminf(R * 4.f, 1.f));
                const float tail = size + v * 0.06f;                              // radial motion streak
                const float sr = rd > 0.f ? size : tail;
                if (fabsf(rd) > 4.f * sr) continue;
                const float ta = (u - ((float)(kb + j) + 0.15f + 0.7f * h1)) * binArc;
                const float q = (ta * ta) / (size * size) + (rd * rd) / (sr * sr);
                if (q > 16.f) continue;
                const float fade = (1.f - f) * sqrtf(1.f - f) * smooth(0.f, 0.05f, f);
                const float I = 1.6f * (0.5f + h5) * fade * __expf(-q);
                const float3 c = vivid(hue + 0.06f + 0.3f * f, P);              // cools along the palette
                const float wh = 0.5f * I * (1.f - f) * (1.f - f);
                Lx += c.x * I + wh; Ly += c.y * I + wh; Lz += c.z * I + wh;
            }
        }
    }

    // ---------------------------------------------------------------- screen-blend the tone-mapped light
    Lx = 1.f - __expf(-Lx * envL); Ly = 1.f - __expf(-Ly * envL); Lz = 1.f - __expf(-Lz * envL);
    col.x += (255.f - col.x) * Lx;
    col.y += (255.f - col.y) * Ly;
    col.z += (255.f - col.z) * Lz;
    out[y * W + x] = pack(col);
}
"""


def _frac(v: float) -> float:
    return v - math.floor(v)


class PortalRing(Transition):
    name = "portal_ring"
    label = "Portal ring"
    description = ("A ring of spinning sparks opens at a point and expands past the frame edges, embers flying "
                   "outward, revealing the new picture through the portal with depth parallax.")
    tags = {"family": "light", "dims": "2d", "toolkit": "none", "technique": ["light", "particles"],
            "moods": ["fiery", "euphoric", "cosmic"], "style": "wild", "energy": [0.4, 1.0]}
    default_duration_s = 3.0
    params = {
        "spark_density": num(0.0, 1.0, 0.55, "Spark density", tier="regional", basic=True),
        "ring_width": num(0.0, 1.0, 0.4, "Ring width", tier="regional"),
        "spin_speed": num(0.0, 1.5, 0.45, "Spin speed", unit="turns/s", tier="regional"),
        "ember_count": num(0, 400, 160, "Embers", tier="regional", integer=True),
        "depth": num(0.0, 1.0, 0.5, "Portal depth (parallax)", tier="regional"),
        "hue": num(0.0, 1.0, 0.0, "Hue (palette index)", tier="regional"),
        "palette": enum(list(PALETTES), "ember", "Palette", tier="regional"),
        "origin_x": num(0.0, 1.0, 0.5, "Origin x", tier="regional"),
        "origin_y": num(0.0, 1.0, 0.5, "Origin y", tier="regional"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("portal_ring")
        self._P = cp.zeros(12, cp.float32)
        self._pal = None
        self._sync_palette()
        s = int(self.params["seed"])
        self._seed = int(np.random.default_rng().integers(0, 2**31 - 1)) if s < 0 else s

    def _sync_palette(self) -> None:
        name = self.params["palette"]
        if name != self._pal:
            self._P.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal = name

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 1e-4:
            cp.copyto(out, a)
            return
        if progress >= 0.9999:
            cp.copyto(out, b)
            return
        p = self.params
        self._sync_palette()
        W, H = self.ctx.width, self.ctx.height
        t = float(progress)
        T = t * self.duration_s

        ox, oy = float(p["origin_x"]) * W, float(p["origin_y"]) * H
        rmax = max(math.hypot(cx - ox, cy - oy) for cx in (0.0, W) for cy in (0.0, H)) / H
        R = 1.1 * t * rmax
        rw = float(p["ring_width"])
        w = (0.012 + 0.05 * rw) * (0.3 + 0.7 * min(R / 0.25, 1.0))      # band half-width (H units)
        thick = (0.0011 + 0.0012 * rw)

        sn = math.sin(math.pi * t)
        envL = 1.25 * sn ** 0.45                                          # light: up fast, gone at the ends
        envG = sn                                                         # geometry: lens, shadow, push
        dep = float(p["depth"])
        Z = 1.0 + 1.2 * dep * (1.0 - t) ** 1.5                            # new scene recedes into place
        lens = 0.6 * dep * envG
        shadow = 0.6 * envG
        push = (0.02 + 0.05 * rw) * envG

        sd = float(p["spark_density"])
        n0 = max(8, int(round(40 + 260 * sd)))
        n1, n2 = max(6, int(round(n0 * 0.6))), max(10, int(round(n0 * 1.5)))
        spin = float(p["spin_speed"])
        s0 = _frac(spin * 1.0 * T)
        s1 = _frac(spin * 1.45 * T + 0.31)
        s2 = _frac(spin * 0.72 * T + 0.67)
        se = _frac(spin * 0.3 * T + 0.17)
        trail = min(0.6 + 0.9 * spin, 2.2)
        ne = int(p["ember_count"]) // 2
        ev = 0.35
        emax = ev * 1.55 / 0.6 + w + 0.03
        hue = float(p["hue"]) + 0.3 * t                                   # colour drifts through the move

        launch_2d(self._k, W, H,
                  (a, b, out, np.int32(W), np.int32(H), np.float32(ox), np.float32(oy),
                   np.float32(R), np.float32(w), np.float32(envL), np.float32(envG),
                   np.float32(Z), np.float32(lens), np.float32(shadow), np.float32(push),
                   np.float32(T), np.float32(s0), np.float32(s1), np.float32(s2),
                   np.int32(n0), np.int32(n1), np.int32(n2), np.float32(trail), np.float32(thick),
                   np.float32(se), np.int32(ne), np.float32(ev), np.float32(emax),
                   np.float32(hue), np.uint32(self._seed & 0xFFFFFFFF), self._P))
