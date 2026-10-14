"""CRT power cycle: a retro TV channel change in three phases.  (1) The old picture loses its signal: tracking
lines tear and roll, the vertical hold slips (with its black blanking bar), RGB ghosting and a red/cyan wash
flicker over it.  (2) The set switches off: the picture squashes to a white-hot horizontal line, the line
collapses to a flaring white dot, and the phosphor afterglow fades on a curved, vignetted tube.  (3) It powers on
into the new picture: dot, then line, then the full frame springing open with overshoot, a degauss colour wobble
and a bright beam sweeping down the scanlines.

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/old_tv_lost_signal.glsl
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/Drop_Zone_Flicker.glsl
Author: mernking (gl-transitions old_tv_lost_signal); bread (Drop_Zone_Flicker); power-off and power-on sequence by smfx
License: MIT   (file headers "// License: MIT"; repo LICENSE: "MIT License, Copyright (c) 2017-present
                gl-transitions contributors", https://github.com/gl-transitions/gl-transitions/blob/master/LICENSE)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: old_tv_lost_signal's 120 hashed tracking lines, sine drift and scanline
  darkening and Drop_Zone_Flicker's RGB offset, red/cyan ghost wash and exposure flicker are kept as phase 1, with
  integer hashes re-picked from progress * duration_s (30 Hz), a rolling tracking bar, a vertical-hold roll with
  a blanking bar, and a multipath echo added.  Phases 2 and 3 (squash to line, collapse to dot, afterglow, dot ->
  line -> frame with spring overshoot, degauss wobble with palette-tinted purity blotches, beam sweep, barrel
  curvature and tube vignette) are original.  signal_loss weights phase 1 (0 = a clean switch-off).  Glows use a
  screen blend; endpoints are early-out copies.)

MIT notice (gl-transitions): Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following
conditions: The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num

_TAU = 2.0 * math.pi

_SRC = HELPERS + PALETTE_CUDA + r"""
extern "C" __global__
void crt_power(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
               const float* __restrict__ P, const int W, const int H, const unsigned int fi,
               const unsigned int seed, const int use_b,
               const float sx, const float sy, const float kc, const float bezw, const float vig,
               const float gain, const float whiten, const float lk, const float agk,
               const float dcore, const float dhalo, const float flare,
               const float gs, const float roll, const float tear_p, const float tear_amp,
               const float wob_amp, const float wob_ph, const float split_px, const float tb_pos,
               const float tb_k, const float flick, const float wash,
               const float dg, const float dg_ph,
               const float scan_k, const float scan_per, const float bs, const float yb,
               const float period, const float drift, const float ptint)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float X = (x + 0.5f) / W * 2.f - 1.f, Y = (y + 0.5f) / H * 2.f - 1.f;
    const float asp = (float)W / (float)H;

    // ---- the tube: barrel curvature, glass edge, vignette
    const float r2 = (X * X * asp * asp + Y * Y) / (asp * asp + 1.f);
    const float bd = 1.f + kc * r2;
    const float Xd = X * bd, Yd = Y * bd;
    float tube = 1.f;
    if (bezw > 0.f) {
        const float ex = 1.f - smooth(1.f - 8.f / W, 1.f, fabsf(Xd));
        const float ey = 1.f - smooth(1.f - 8.f / H, 1.f, fabsf(Yd));
        tube = 1.f - bezw * (1.f - ex * ey);
    }
    tube *= 1.f - vig * r2;

    // phosphor colour: cool white, pulled towards the palette by ptint
    const float3 pc = pal(drift, P);
    const float phr = 0.84f + (pc.x - 0.84f) * ptint;
    const float phg = 0.93f + (pc.y - 0.93f) * ptint;
    const float phb = 1.00f + (pc.z - 1.00f) * ptint;

    float r = 0.f, g = 0.f, bl = 0.f;
    const float Xs = Xd / sx, Ys = Yd / sy;
    if (fabsf(Xs) < 1.f && fabsf(Ys) < 1.f) {
        float px = (Xs + 1.f) * 0.5f * W, py = (Ys + 1.f) * 0.5f * H;
        if (!use_b) {
            // ---------------- phase 1/2: the old picture losing its signal
            float bar = 0.f, tb = 0.f, split = 0.f;
            if (gs > 0.f) {
                const float vv = py / H;
                const int line = (int)floorf((vv - 0.37f * roll) * 120.f);
                const unsigned int fl = fi >> 1;
                const float torn = hash1((unsigned)line, fl, seed + 3u) > 1.f - tear_p ? 1.f : 0.f;
                float dx = wob_amp * __sinf(vv * 30.f + wob_ph);
                dx += torn * (hash1((unsigned)line, fl, seed + 5u) - 0.5f) * 2.f * tear_amp;
                tb = tb_k * (1.f - smooth(0.012f, 0.05f, fabsf(vv - tb_pos)));
                dx += tb * (hash1((unsigned)(y / 3), fi, seed + 9u) - 0.5f) * 0.07f * W;
                px += dx;
                px -= floorf(px / W) * W;
                float vs = vv + roll;
                vs -= floorf(vs);
                const float sd = fminf(vs, 1.f - vs);
                bar = smooth(0.f, 0.03f, fabsf(roll)) * (1.f - smooth(0.02f, 0.032f, sd));
                py = vs * H;
                split = split_px * (0.35f + 0.65f * fminf(torn + tb, 1.f));
            }
            const float4 c = bilin(a, px, py, W, H);
            r = c.x; g = c.y; bl = c.z;
            if (split >= 0.5f) {                                   // RGB ghosting
                r = bilin(a, px + split, py, W, H).x;
                bl = bilin(a, px - split, py, W, H).z;
            }
            if (gs > 0.f) {
                const float4 e = ld(a, (int)(px - 0.02f * W), (int)py, W, H);   // multipath echo
                const float ek = 0.28f * gs;
                r += (e.x - r) * ek; g += (e.y - g) * ek; bl += (e.z - bl) * ek;
                // Drop_Zone_Flicker's red / cyan ghost wash
                const float ux = px / W;
                const float lw = (1.f - smooth(0.10f, 0.78f, ux)) * wash * 0.25f;
                const float cw = smooth(0.08f, 0.62f, ux) * (1.f - smooth(0.86f, 1.f, ux)) * wash * 0.18f;
                r += (r * 1.34f - r) * lw; g += (g * 0.42f - g) * lw; bl += (bl * 0.38f - bl) * lw;
                r += (r * 0.48f - r) * cw; g += (g * 1.12f - g) * cw; bl += (bl * 1.24f - bl) * cw;
                // snow in the tracking bar and a faint off-air grain
                const float n = hash1((unsigned)(x >> 1), (unsigned)(y >> 1), fi * 977u + seed);
                const float sk = fminf(0.7f * tb + 0.1f * gs, 1.f);
                r += (n * 255.f - r) * sk; g += (n * 255.f - g) * sk; bl += (n * 255.f - bl) * sk;
                const float kb = (1.f - 0.95f * bar) * flick;
                r *= kb; g *= kb; bl *= kb;
            }
        } else {
            // ---------------- phase 3: the new picture, degauss wobble
            if (dg > 0.f) {
                const float dx = dg * 0.014f * W * __sinf(Ys * 5.f + dg_ph);
                const float dy = dg * 0.006f * H * __sinf(Xs * 4.f + 1.3f * dg_ph);
                const float spl = dg * 0.006f * W * __sinf(0.7f * dg_ph + 1.f);
                px += dx; py += dy;
                r = bilin(b, px + spl, py, W, H).x;
                g = bilin(b, px, py, W, H).y;
                bl = bilin(b, px - spl, py, W, H).z;
                // colour-purity blotches swimming over the tube
                const float n = vnoise(Xs * 1.6f + 4.f + 0.35f * __sinf(dg_ph * 0.5f),
                                       Ys * 1.4f + 4.f + 0.35f * __cosf(dg_ph * 0.4f), seed);
                const float3 t = pal(n * period * 2.f + drift + 0.3f, P);
                const float k = 0.6f * dg;
                r *= 1.f + k * (1.7f * t.x - 1.f);
                g *= 1.f + k * (1.7f * t.y - 1.f);
                bl *= 1.f + k * (1.7f * t.z - 1.f);
            } else {
                const float4 c = bilin(b, px, py, W, H);
                r = c.x; g = c.y; bl = c.z;
            }
        }
        // concentrated beam: brighter, then white-hot as the picture shrinks
        r *= gain; g *= gain; bl *= gain;
        if (whiten > 0.f) {
            const float hr = 255.f * (0.8f + 0.2f * phr), hg = 255.f * (0.8f + 0.2f * phg), hb = 255.f * (0.8f + 0.2f * phb);
            r += (hr - r) * whiten; g += (hg - g) * whiten; bl += (hb - bl) * whiten;
        }
    }

    // ---- beam sweep: rows below the beam are not refreshed yet
    const float vy = (Yd + 1.f) * 0.5f;
    float G = 0.f;
    if (bs > 0.f) {
        const float d = (vy - yb) / 0.012f;
        G += bs * 0.6f * __expf(-d * d);
        const float dim = 1.f - 0.45f * bs * smooth(0.f, 0.035f, vy - yb);
        r *= dim; g *= dim; bl *= dim;
    }

    // ---- line glow, residual phosphor line, flaring dot (pixel units, screen blend)
    const float hx = fabsf(Xd) * W * 0.5f, hy = fabsf(Yd) * H * 0.5f;
    if (lk > 0.f) {
        const float dxp = fmaxf(hx - sx * W * 0.5f, 0.f), dyp = fmaxf(hy - sy * H * 0.5f, 0.f);
        G += lk * (0.9f * __expf(-dyp / (H * 0.004f)) + 0.35f * __expf(-dyp / (H * 0.035f)))
                * __expf(-dxp / (W * 0.012f));
    }
    if (agk > 0.f)
        G += agk * __expf(-hy / (H * 0.003f)) * (1.f - smooth(0.6f, 1.f, fabsf(Xd)));
    if (dcore + dhalo > 0.f) {
        const float rp2 = hx * hx + hy * hy, rp = sqrtf(rp2);
        const float rc = H * 0.009f;
        G += dcore * (1.8f * __expf(-rp2 / (rc * rc))
                      + flare * 0.55f * __expf(-hy / (H * 0.0025f)) * __expf(-hx / (W * 0.07f))
                      + flare * 0.3f * __expf(-hx / (H * 0.0025f)) * __expf(-hy / (H * 0.05f)));
        G += dhalo * (0.6f * __expf(-rp / (H * 0.035f)) + 0.22f * __expf(-rp / (H * 0.15f)));
    }
    if (G > 0.f) {
        const float gk = 1.f - __expf(-1.3f * G);
        const float cw = smooth(0.8f, 2.2f, G);                    // a hot core goes white
        const float kr = phr + (1.f - phr) * cw, kg = phg + (1.f - phg) * cw, kb = phb + (1.f - phb) * cw;
        r += (255.f - r) * clamp01(gk * kr);
        g += (255.f - g) * clamp01(gk * kg);
        bl += (255.f - bl) * clamp01(gk * kb);
    }

    // ---- scanlines over everything, then the tube mask
    const float sc = (1.f - scan_k * (0.5f + 0.5f * __cosf(6.2831853f * (y + 0.5f) / scan_per))) * tube;
    out[y * W + x] = pack(make_float4(r * sc, g * sc, bl * sc, 255.f));
}
"""


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _clamp01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def _hash01(a: int, b: int) -> float:
    h = (a * 2654435761 ^ (b + 0x9E3779B9) * 2246822519) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 2654435761) & 0xFFFFFFFF
    h ^= h >> 13
    return (h & 0xFFFFFF) / 16777216.0


def _wrap(ph: float) -> float:
    return (ph + math.pi) % _TAU - math.pi


def _spring(s: float, ov: float) -> float:
    """0 -> 1 with a damped-spring overshoot (weight ov) that lands exactly on 1 at s = 1."""
    s = _clamp01(s)
    base = s * s * (3.0 - 2.0 * s)
    spr = 1.0 - math.exp(-4.5 * s) * math.cos(10.0 * s)
    return base + ov * (spr - base) * (1.0 - _smooth(0.75, 1.0, s))


class CrtPower(Transition):
    name = "crt_power"
    label = "CRT power cycle"
    description = ("A retro TV channel change: the old picture loses signal, the tube switches off to a line and a "
                   "flaring dot, then powers on into the new picture with overshoot, degauss wobble and a beam sweep.")
    default_duration_s = 1.8
    tags = {"family": ["glitch"], "dims": ["2d"], "technique": ["retro", "glitch"]}
    params = {
        "signal_loss": num(0.0, 1.0, 0.6, "Signal loss (0 = clean switch-off)", tier="regional", basic=True),
        "curvature": num(0.0, 1.0, 0.55, "Tube curvature", tier="regional"),
        "line_glow": num(0.0, 1.0, 0.7, "Line and dot glow", tier="regional"),
        "overshoot": num(0.0, 1.0, 0.6, "Power-on overshoot", tier="regional"),
        "scanlines": num(0.0, 1.0, 0.5, "Scanlines and beam sweep", tier="regional"),
        "phosphor_tint": num(0.0, 1.0, 0.35, "Phosphor tint", tier="regional"),
        "palette": enum(list(PALETTES), "ice", "Phosphor palette", tier="regional"),
        "palette_period": num(0.1, 3.0, 1.0, "Palette period", tier="regional"),
        "colour_drift": num(0.0, 1.0, 0.2, "Colour drift", unit="/s", tier="regional"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("crt_power")
        self._P = cp.zeros(12, dtype=cp.float32)
        self._pal_name = None
        s = int(self.params.get("seed", -1))
        self._seed = int(np.random.default_rng().integers(0, 2**31)) if s < 0 else s
        self._drift0 = _hash01(self._seed, 7)

    def _sync_palette(self) -> None:
        name = self.params.get("palette", "ice")
        if name not in PALETTES:
            name = "ice"
        if name != self._pal_name:
            self._P.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal_name = name

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        q = float(progress)
        if q <= 1e-4:
            cp.copyto(out, a)
            return
        if q >= 0.9999:
            cp.copyto(out, b)
            return
        p = self.params
        w, h = self.ctx.width, self.ctx.height
        dur = max(self.duration_s, 0.05)
        t = q * dur
        fi = int(math.floor(t * 30.0))
        self._sync_palette()

        sl = _clamp01(float(p.get("signal_loss", 0.6)))
        curv = _clamp01(float(p.get("curvature", 0.55)))
        lg = _clamp01(float(p.get("line_glow", 0.7)))
        ov = _clamp01(float(p.get("overshoot", 0.6)))
        scan = _clamp01(float(p.get("scanlines", 0.5)))

        # ---- phase boundaries: signal loss [0, q1], switch-off [q1, q2], power-on [q2, 1]
        q1 = 0.36 * sl
        q2 = q1 + 0.46 * (1.0 - q1)
        s1 = q / q1 if q1 > 1e-4 else 1.0
        u = _clamp01((q - q1) / max(q2 - q1, 1e-4)) if q > q1 else 0.0
        v = _clamp01((q - q2) / max(1.0 - q2, 1e-4)) if q > q2 else 0.0
        on = q > q2

        lh = 5.0 / h                 # line half-height (normalised y)
        dw = 6.0 / w                 # dot half-width (normalised x)
        sx = sy = 1.0
        gain, whiten, lk, agk, dcore, dhalo = 1.0, 0.0, 0.0, 0.0, 0.0, 0.0
        gs, dg, bs, yb = 0.0, 0.0, 0.0, -1.0

        if not on:
            if q < q1:
                gs = sl * _smooth(0.0, 0.25, s1) * (0.45 + 0.55 * s1)
            else:
                gs = sl * (1.0 - _smooth(0.2, 0.6, u))
                # switch-off: squash to a line, collapse to a dot, afterglow
                sy = 1.0 + (lh - 1.0) * _smooth(0.0, 0.42, u)
                sx = 1.0 + (dw - 1.0) * _smooth(0.42, 0.72, u)
                gain = 1.0 + 1.6 * _smooth(0.05, 0.4, u)
                whiten = _smooth(0.12, 0.4, u)
                lk = lg * (1.0 - _smooth(0.04, 0.45, sy)) * (1.0 - _smooth(0.78, 0.95, u))
                agk = lg * 0.7 * _smooth(0.42, 0.55, u) * (1.0 - _smooth(0.55, 0.95, u))
                ign = _smooth(0.58, 0.7, u)
                dcore = (0.5 + 0.5 * lg) * ign * (1.0 - _smooth(0.74, 0.92, u))
                dhalo = (0.3 + 0.7 * lg) * ign * (1.0 - _smooth(0.75, 1.0, u))
        else:
            # power-on: dot, line, frame (spring overshoot), degauss, beam sweep
            ign = _smooth(0.02, 0.07, v)
            dcore = (0.5 + 0.5 * lg) * ign * (1.0 - _smooth(0.16, 0.32, v))
            dhalo = (0.3 + 0.7 * lg) * ign * (1.0 - _smooth(0.2, 0.4, v))
            sx = dw + (1.0 - dw) * _spring((v - 0.08) / 0.22, ov) if v > 0.08 else dw
            sy = lh + (1.0 - lh) * _spring((v - 0.3) / 0.3, ov) if v > 0.3 else lh
            sx, sy = max(sx, dw), max(sy, lh)
            gain = 1.0 + 0.8 * (1.0 - _smooth(0.3, 0.85, v))
            whiten = (1.0 - _smooth(0.25, 0.45, v)) * _smooth(0.0, 0.05, v)
            lk = lg * (1.0 - _smooth(0.04, 0.45, sy)) * _smooth(0.06, 0.14, v)
            dg = _smooth(0.4, 0.5, v) * (1.0 - _smooth(0.6, 0.98, v))
            bs = (0.35 + 0.65 * scan) * _smooth(0.5, 0.56, v) * (1.0 - _smooth(0.88, 0.99, v))
            yb = -0.05 + 1.15 * _clamp01((v - 0.5) / 0.42)

        # ---- tube curvature and scanlines: in from 0, out to 0 (pure endpoints)
        cv = _smooth(0.0, 0.15, q) * (1.0 - _smooth(0.5, 1.0, v))
        kc = curv * 0.24 * cv
        bezw = min(1.0, cv * 6.0) if curv > 0.0 else 0.0
        vig = 0.32 * curv * cv
        scan_k = scan * 0.45 * _smooth(0.0, 0.1, q) * (1.0 - _smooth(0.6, 1.0, v))
        scan_per = max(3.0, 6.0 * h / 2160.0)

        # ---- signal-loss detail (old_tv_lost_signal + Drop_Zone_Flicker)
        roll = sl * (0.5 * min(s1, 1.0) ** 2 + 0.7 * u) if not on else 0.0
        roll += (_hash01(fi, self._seed) - 0.5) * 0.02 * gs
        tear_p = 0.06 + 0.25 * gs
        tear_amp = 0.05 * w * gs
        wob_amp = 0.012 * w * gs
        wob_ph = _wrap(q * 10.0 + t * 2.0)
        split_px = 0.008 * w * gs
        tb_pos = 1.15 - ((q * 3.0) % 1.0) * 1.3
        flick = 1.0 + (_hash01(fi, self._seed + 1) - 0.4) * 0.35 * gs
        wash = gs * (0.4 + 0.6 * _hash01(fi, self._seed + 3))
        dg_ph = _wrap(_TAU * 9.0 * t)

        drift = (self._drift0 + float(p.get("colour_drift", 0.2)) * t) % 1.0

        f32 = np.float32
        launch_2d(self._k, w, h,
                  (a, b, out, self._P, np.int32(w), np.int32(h), np.uint32(fi & 0xFFFFFFFF),
                   np.uint32(self._seed & 0xFFFFFFFF), np.int32(1 if on else 0),
                   f32(sx), f32(sy), f32(kc), f32(bezw), f32(vig),
                   f32(gain), f32(whiten), f32(lk), f32(agk),
                   f32(dcore), f32(dhalo), f32(1.0),
                   f32(gs), f32(roll), f32(tear_p), f32(tear_amp),
                   f32(wob_amp), f32(wob_ph), f32(split_px), f32(tb_pos),
                   f32(gs), f32(flick), f32(wash),
                   f32(dg), f32(dg_ph),
                   f32(scan_k), f32(scan_per), f32(bs), f32(yb),
                   f32(p.get("palette_period", 1.0)), f32(drift), f32(p.get("phosphor_tint", 0.35))))
