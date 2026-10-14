"""Black hole swallow: a black hole forms at a point, lenses the old picture into an Einstein ring, spins a
palette-coloured accretion disc with Doppler brightening and drags the old picture into a horizon that grows to
fill the screen; then it reverses into a white hole that ejects the new picture with the opposite twist and a
flash.

Source: https://github.com/nanfengw0w/varyloom/blob/main/src/transitions/vortex-portal.ts
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/Swirl.glsl
        https://github.com/rmarchet/blackhole-ts   (lensing idea only)
Author: Varyloom contributors (vortex-portal.ts); Sergey Kosarevsky (gl-transitions Swirl); Roberto Marchetti
        (blackhole-ts, lensing idea)
License: MIT   (varyloom README: "Licensed under MIT."; Swirl.glsl header: "// License: MIT"; gl-transitions
                repo LICENSE: "MIT License, Copyright (c) 2017-present gl-transitions contributors")
license_class: permissive
Ported from GLSL by smfx (ideas/changes: vortex-portal's radial front with a twist envelope around the origin and
  Swirl's (1 - r)^2 swirl peaking mid-transition are rebuilt as a two-act black-hole model in aspect-correct
  (height-normalised) space.  Act 1 (progress 0 -> 0.5): a horizon of radius rh grows to cover the farthest corner;
  outside it the old picture is lensed with r' = r - rE^2 / r (rE = Einstein radius, a multiple of rh set by
  lensing and mass; r' < 0 gives the inverted secondary image inside the Einstein ring), frame-dragged by a twist
  that grows towards the horizon, swirled globally (Swirl), sucked inwards and redshift-dimmed at the horizon; a
  thin photon ring rims the shadow.  A tilted accretion disc (Keplerian-sheared periodic value-noise streaks,
  palette-coloured by radius with drift, white-hot inner edge, relativistic Doppler beaming g^3 and a colour shift
  on the approaching side) is drawn directly for its front half (crossing in front of the shadow) and through the
  same lens for its back half, which gives the over-the-top arc and the thin under-arc.  Act 2 (0.5 -> 1): from
  black, a white hole at the same origin flashes and ejects the new picture as an expanding disc (shrunken whole
  picture growing to full frame), twisted the opposite way and unwinding, with a bright rim at its front and the
  disc spinning the other way around it.  Light is composited with an exponential screen so bright stills do not
  clip; mirror-repeat sampling avoids edge smears; disc spin uses progress * duration_s.  Endpoints are forced
  copies, and every term is zero at both ends and at the black turnover.)

MIT notice (gl-transitions, varyloom): Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions: The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY
KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES
OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
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
#define TAU 6.2831853f
#define INV_TAU 0.15915494f

// Mirror-repeat a continuous pixel coordinate into [0, L] (no clamped edge smears when the lens reaches out).
__device__ __forceinline__ float fold(float x, float L) {
    const float L2 = 2.f * L;
    x = x - L2 * floorf(x / L2);
    return x > L ? L2 - x : x;
}
__device__ __forceinline__ float4 samp(const uchar4* __restrict__ img, float x, float y, int W, int H) {
    return bilin(img, fold(x, (float)W), fold(y, (float)H), W, H);
}

// Value noise periodic in its first coordinate (N cells around the disc), smooth in both.
__device__ float pnoise(float a, float rr, int N, unsigned int seed) {
    const float fa = floorf(a), fr = floorf(rr);
    int ia = ((int)fa) % N; if (ia < 0) ia += N;
    const int ia1 = (ia + 1) % N, ir = (int)fr;
    float ta = a - fa, tr = rr - fr;
    ta = ta * ta * (3.f - 2.f * ta); tr = tr * tr * (3.f - 2.f * tr);
    const float v00 = hash1(ia, ir, seed), v10 = hash1(ia1, ir, seed);
    const float v01 = hash1(ia, ir + 1, seed), v11 = hash1(ia1, ir + 1, seed);
    return (1.f - tr) * ((1.f - ta) * v00 + ta * v10) + tr * ((1.f - ta) * v01 + ta * v11);
}

// Accretion-disc emission at screen offset (dx, dy) (height units, y down) around inner radius rin.
// The disc lies in a plane tilted about the horizontal axis: v = dy / cos(tilt).  *vout gets v (front half v > 0).
__device__ float3 disc(float dx, float dy, float rin, float ct, float st, float dir, float spin, float tpal,
                       float period, float size, const float* __restrict__ P, unsigned int seed, float* vout)
{
    const float u = dx, v = dy / ct;
    *vout = v;
    const float rd = sqrtf(u * u + v * v) + 1e-6f;
    const float x = rd / rin;
    if (x < 0.9f || x > size) return make_float3(0.f, 0.f, 0.f);
    const float prof = smooth(0.93f, 1.05f, x) * (1.f - smooth(0.45f * size, size, x)) * __powf(x, -1.6f);
    const float phi = atan2f(v, u);
    const float om = dir * spin * rsqrtf(x * x * x);                       // Keplerian shear: inner edge fastest
    const float a = (phi + om) * INV_TAU;
    const float lr = __logf(x);
    const float n = 0.62f * pnoise(a * 24.f, lr * 7.f, 24, seed) + 0.38f * pnoise(a * 57.f, lr * 18.f, 57, seed + 7u);
    const float streak = 0.2f + 1.7f * n * n;
    const float los = dir * st * (u / rd);                                 // + on the approaching side
    const float g = fmaxf(1.f + 0.55f * los, 0.2f);
    const float e = prof * streak * g * g * g;                             // relativistic beaming ~ g^3
    const float3 c = pal(tpal + period * 0.4f * lr + 0.12f * los + 0.1f * n, P);
    const float hot = 0.65f * __expf(-4.f * (x - 1.f));                    // white-hot inner edge
    return make_float3(e * (c.x + (1.f - c.x) * hot), e * (c.y + (1.f - c.y) * hot), e * (c.z + (1.f - c.z) * hot));
}

extern "C" __global__
void black_hole_swallow(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
                        const float* __restrict__ P, const int W, const int H, const int act,
                        const float ox, const float oy, const float rmax,
                        const float rh, const float rE2, const float tw, const float gsw, const float infall,
                        const float scale, const float bulge, const float flash, const float rim,
                        const float dir, const float ct, const float st, const float spin, const float tpal,
                        const float period, const float disc_w, const float dsize, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float invH = 1.f / H;
    const float fe = 1.5f * invH;
    const float dx = (x + 0.5f) * invH - ox, dy = (y + 0.5f) * invH - oy;
    const float r = sqrtf(dx * dx + dy * dy) + 1e-6f;
    float cr = 0.f, cg = 0.f, cb = 0.f;          // base picture 0..1
    float er = 0.f, eg = 0.f, eb = 0.f;          // emitted light (screen-composited)
    float vd;

    if (act == 0) {
        // ---------------- act 1: the black hole swallows the old picture
        const float hz = smooth(rh - fe, rh + fe, r);                      // 0 inside the horizon
        const float rr = fmaxf(r, rh);
        const float k = 1.f - rE2 / (rr * rr);                             // r' = r - rE^2 / r  (k < 0: secondary image)
        if (hz > 0.f) {
            const float t = fmaxf(1.f - r / rmax, 0.f);
            const float ang = dir * (tw * __powf(rh / rr, 1.5f) + gsw * t * t);
            float sn, cs; __sincosf(ang, &sn, &cs);
            const float s = k * infall;
            const float sx = (cs * dx - sn * dy) * s, sy = (sn * dx + cs * dy) * s;
            const float4 c = samp(a, (ox + sx) * H, (oy + sy) * H, W, H);
            const float dim = hz * (0.12f + 0.88f * smooth(rh, 1.4f * rh, r)); // gravitational redshift at the edge
            cr = c.x * (dim / 255.f); cg = c.y * (dim / 255.f); cb = c.z * (dim / 255.f);
        }
        if (disc_w > 0.f) {
            // The front half is seen directly (crossing in front of the shadow); the back half is seen through the
            // lens (the arc over the top and the thin secondary arc beneath).  The sample point slides smoothly
            // from direct to lensed across the disc's mid-line, so there is no seam.
            const float rin = 1.35f * rh;
            const float vn = dy / (ct * rin);
            const float wb = st * smooth(-0.3f, 0.3f, -vn) * hz;
            const float ks = 1.f + (k - 1.f) * wb;
            const float3 d0 = disc(dx * ks, dy * ks, rin, ct, st, dir, spin, tpal, period, dsize, P, seed, &vd);
            const float wf = smooth(-0.3f, 0.3f, vn);
            const float w0 = disc_w * (wf + (1.f - wf) * hz);           // the back half hides behind the shadow
            er += d0.x * w0; eg += d0.y * w0; eb += d0.z * w0;
        }
        if (rim > 0.f) {                                                   // photon ring hugging the shadow
            const float w = 0.03f * rh + fe;
            const float z = (r - 1.03f * rh) / w;
            const float pr = rim * __expf(-z * z);
            const float3 c = pal(tpal + 0.5f, P);
            er += pr * (0.5f + 0.5f * c.x); eg += pr * (0.5f + 0.5f * c.y); eb += pr * (0.5f + 0.5f * c.z);
        }
    } else {
        // ---------------- act 2: the white hole ejects the new picture
        const float R2 = rh;
        const float fe2 = fmaxf(fe, 0.006f);
        const float inside = 1.f - smooth(R2 - fe2, R2 + fe2, r);
        const float t = fminf(r / fmaxf(R2, 1e-4f), 1.f);
        if (inside > 0.f) {
            const float ang = -dir * tw * (1.f - t) * (1.f - t);            // the opposite twist, unwinding
            float sn, cs; __sincosf(ang, &sn, &cs);
            const float z = (t - 1.f) * 4.f;
            const float s = scale * (1.f - bulge * __expf(-z * z));          // ejecta pile up behind the front
            const float sx = (cs * dx - sn * dy) * s, sy = (sn * dx + cs * dy) * s;
            const float4 c = samp(b, (ox + sx) * H, (oy + sy) * H, W, H);
            cr = c.x * (inside / 255.f); cg = c.y * (inside / 255.f); cb = c.z * (inside / 255.f);
        }
        if (disc_w > 0.f && R2 > 1e-4f) {
            const float3 d0 = disc(dx, dy, 1.05f * R2, ct, st, dir, spin, tpal, period, dsize, P, seed, &vd);
            const float wf = smooth(-0.01f, 0.01f, vd);
            const float w0 = disc_w * (wf + (1.f - wf) * (1.f - inside));
            er += d0.x * w0; eg += d0.y * w0; eb += d0.z * w0;
        }
        if (rim > 0.f) {                                                   // the bright lip of the ejecta
            const float w = 0.01f + 0.03f * R2 + fe;
            const float z = (r - R2) / w;
            const float pr = rim * __expf(-z * z);
            const float3 c = pal(tpal + 0.25f, P);
            er += pr * (0.35f + 0.65f * c.x); eg += pr * (0.35f + 0.65f * c.y); eb += pr * (0.35f + 0.65f * c.z);
        }
        if (flash > 0.f) {                                                 // the white-hole flash
            const float f = flash * (0.35f + 1.6f * __expf(-r * r * 14.f));
            const float3 c = pal(tpal + 0.1f, P);
            er += f * (0.6f + 0.4f * c.x); eg += f * (0.6f + 0.4f * c.y); eb += f * (0.6f + 0.4f * c.z);
        }
    }
    // exponential screen: 1 - (1 - base) e^-light  (bright stills never clip hard)
    cr = 1.f - (1.f - cr) * __expf(-er);
    cg = 1.f - (1.f - cg) * __expf(-eg);
    cb = 1.f - (1.f - cb) * __expf(-eb);
    out[y * W + x] = pack(make_float4(cr * 255.f, cg * 255.f, cb * 255.f, 255.f));
}
"""


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class BlackHoleSwallow(Transition):
    name = "black_hole_swallow"
    label = "Black hole swallow"
    description = ("A black hole lenses the old picture into an Einstein ring, spins a glowing accretion disc and "
                   "swallows the frame; then a white hole flashes and ejects the new picture with the opposite twist.")
    default_duration_s = 3.0
    params = {
        "mass": num(0.0, 1.0, 0.5, "Mass (how fast and hard it pulls)", tier="regional", basic=True),
        "twist": num(0.0, 1.0, 0.6, "Twist (frame dragging)", tier="regional", basic=True),
        "lensing": num(0.0, 1.0, 0.7, "Lensing", tier="regional"),
        "disc_brightness": num(0.0, 2.0, 1.0, "Disc brightness", tier="regional"),
        "disc_tilt": num(0.0, 1.0, 0.8, "Disc tilt (face-on -> edge-on)", tier="regional"),
        "disc_size": num(1.5, 5.0, 3.0, "Disc size (× inner radius)", step=0.05, tier="regional"),
        "flash": num(0.0, 1.5, 0.8, "White-hole flash", tier="regional"),
        "origin_x": num(0.05, 0.95, 0.5, "Origin x", tier="regional"),
        "origin_y": num(0.05, 0.95, 0.5, "Origin y", tier="regional"),
        "palette": enum(list(PALETTES), "ember", "Disc palette", tier="regional"),
        "palette_period": num(0.1, 3.0, 1.0, "Palette period", tier="regional"),
        "spin": enum(["clockwise", "anticlockwise"], "clockwise", "Spin direction", tier="structural"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("black_hole_swallow")
        self._P = cp.zeros(12, dtype=cp.float32)
        self._pal_name = None
        s = int(self.params.get("seed", -1))
        seed = int(np.random.default_rng().integers(0, 2**31)) if s < 0 else s
        rng = np.random.default_rng(seed)
        self._nseed = int(rng.integers(1, 2**31 - 1))
        self._drift0 = float(rng.uniform(0.0, 1.0))

    def _sync_palette(self) -> None:
        name = self.params.get("palette", "ember")
        if name not in PALETTES:
            name = "ember"
        if name != self._pal_name:
            self._P.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal_name = name

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 1e-4:
            cp.copyto(out, a)
            return
        if progress >= 0.9999:
            cp.copyto(out, b)
            return
        self._sync_palette()
        p = self.params
        w, h = self.ctx.width, self.ctx.height
        aspect = w / h
        mass = float(p["mass"])
        twist = float(p["twist"])
        lensing = float(p["lensing"])
        ox, oy = float(p["origin_x"]) * aspect, float(p["origin_y"])
        rmax = max(math.hypot(x - ox, y - oy) for x in (0.0, aspect) for y in (0.0, 1.0))
        tilt = float(p["disc_tilt"]) * 1.45                          # up to ~83 degrees
        ct, st = max(math.cos(tilt), 0.12), math.sin(tilt)
        dirc = 1.0 if p.get("spin", "clockwise") == "clockwise" else -1.0
        secs = progress * self.duration_s
        spin = 2.4 * secs                                            # disc angle at the inner edge (rad)
        tpal = self._drift0 + 0.07 * secs                            # palette drift
        disc_b = float(p["disc_brightness"])
        dsize = float(p["disc_size"])

        if progress < 0.5:
            q = progress / 0.5
            gamma = 2.4 - 1.4 * mass                                 # heavy holes grow early
            rcov = rmax + 0.02
            rh = rcov * q ** gamma
            kE = 1.0 + lensing * (0.3 + 0.7 * mass)                  # Einstein radius / horizon radius
            rE2 = (kE * rh) ** 2
            tw = twist * 2.2 * math.pi * q ** 1.2                    # frame dragging at the horizon
            gsw = twist * 1.3 * math.pi * q * q                      # Swirl's global (1 - r)^2 swirl
            infall = 1.0 + (0.15 + 0.3 * mass) * q * q               # the picture is sucked in
            fade = _smooth(0.0, 0.2, q) * (1.0 - _smooth(0.78, 1.0, q))
            disc_w = 1.6 * disc_b * fade
            rim = 1.4 * _smooth(0.0, 0.12, q) * (1.0 - _smooth(0.85, 1.0, q))
            act, scale, bulge, flash = 0, 1.0, 0.0, 0.0
        else:
            q = (progress - 0.5) / 0.5
            rh = (rmax + 0.04) * 1.04 * (1.0 - (1.0 - q) ** 2.2)    # the ejecta front, fast out
            rE2, gsw, infall = 0.0, 0.0, 1.0
            tw = twist * 2.2 * math.pi * (1.0 - q) ** 1.6
            scale = min(max(rmax / max(rh, 1e-4), 1.0), 6.0)         # the whole picture, growing from the point
            bulge = 0.14 * lensing * (1.0 - q)
            flash = float(p["flash"]) * _smooth(0.0, 0.04, q) * math.exp(-q / 0.14)
            disc_w = 1.6 * disc_b * _smooth(0.0, 0.08, q) * (1.0 - q) ** 1.5
            rim = 1.4 * _smooth(0.0, 0.05, q) * (1.0 - q) ** 0.8
            dirc = -dirc                                             # the white hole spins the other way
            act = 1

        launch_2d(self._k, w, h,
                  (a, b, out, self._P, np.int32(w), np.int32(h), np.int32(act),
                   np.float32(ox), np.float32(oy), np.float32(rmax),
                   np.float32(rh), np.float32(rE2), np.float32(tw), np.float32(gsw), np.float32(infall),
                   np.float32(scale), np.float32(bulge), np.float32(flash), np.float32(rim),
                   np.float32(dirc), np.float32(ct), np.float32(st), np.float32(spin), np.float32(tpal),
                   np.float32(p["palette_period"]), np.float32(disc_w), np.float32(dsize),
                   np.uint32(self._nseed)))
