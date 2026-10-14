"""Laser fans: concert lasers -- fans of thin beams from 1-4 projectors sweep, chase and scan through haze.

Source: Original (inspired by https://en.wikipedia.org/wiki/Laser_lighting_display)
Author: smfx (original concert-laser look)
License: MIT
license_class: permissive

Projector slots (faded in by the continuous ``projectors`` knob, symmetric at every integer):
    1 = centre floor projector, 2 = left + right floor projectors (scissor pair, centre fades out),
    3 = left + centre + right, 4 = plus a truss projector at the top pointing down.
Each projector throws a fan of up to 24 beams (``beam_density`` is continuous: the last beam fades in and the spacing
slides).  A beam is drawn analytically: perpendicular distance -> a white-hot core plus a compact haze glow, both
widening with distance (divergence), attenuated through the haze, ending in a hot dot where it hits the ceiling
(floor projectors) or the floor (truss projector).  Every projector also lights a soft cone of haze, and
``sheet_mix`` morphs the fan from discrete beams into a "liquid sky" sheet: a continuous plane of light with
waves rolling outward, their bending crests brightening and fading along their length (drawn in the fan's own plane,
so tilt foreshortens them in perspective) while the discrete beams fade to ~3 %; mid values give beams over a
translucent sheet.  Hot spots flare at the apertures.  A quarter-res
fbm haze field (rising, drifting smoke) modulates the scatter.  Renders at render_scale 0.75 (thin beams), additive
in linear light, soft-shoulder tonemap, compiled with --use_fast_math; ~3-4 ms at 4K on the P4000.  Colours are the
shared cosine palette (same formula as PALETTE_CUDA's pal) evaluated per beam on the host, then laser-saturated.

Per-pixel cost is bounded: each projector computes the pixel's plane angle once, converts it to a fractional beam
index and only visits the beams whose glow can reach the pixel (an angle window).

Macros:
  energy (e' = smoothstep(0.1, 0.85, e)) -> sweep / chase / scan / breath / ripple rates x(0.4 + 1.2 e'),
      sweep and breath amplitudes (smoothstep, floor 50 %), active beam fraction (e'^1.5, slewed 1.5 s, faded beam
      by beam), chase depth, haze glow brightness, flicker depth (only above mid energy), exposure.
  pulse  (k = pulse * (0.3 + 0.7 e)) -> a chase step: on each beat (rising edge with hysteresis) a bright wave runs
      across every fan (a blooming front with a short trail, lighting even the beams that are off), alternating
      direction; plus a sweep-rate kick into the host-integrated sweep phase (lurch then ease, never the angle
      itself).  ``beat_kick`` scales both.
  speed  -> every phase (sweep, chase, scan, breath, flicker, ripples, haze, colour drift).
  pan / tilt (levels, one-pole smoothed 150 ms on the host) aim the fans: pan rotates every fan sideways (the
      truss fan follows on screen), tilt foreshortens each fan plane (low = flattened towards the crowd, high =
      narrowed upwards).  The show bus's pan/tilt are signed, so presets bind them as offsets around the centred
      defaults ({kick: pan, amount: a}); the plug-in clamps to 0..1.
  colour = pal(beam_index * palette_period + drift), laser-saturated; palette_period 0 = mono (all beams one colour,
      e.g. Maestro's rgb through the palette), higher = rainbow across the beams.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTES
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present

NPROJ = 4
MAXB = 24
PRS = 32          # floats per projector record
BMS = 12          # floats per beam record
NG = 32           # global floats
LMAX = 4.5        # beam length when it hits nothing (hh units)
HAZE_DIV = 4      # haze field resolution divisor

# slot: x, y (hh units, y up), aim (deg), mirror (+1 / -1: beam order + sweep sign), cut (+1 ceiling, -1 floor),
#       cut y, sweep phase offset (cycles), pan sign, colour offset (x palette_period)
SLOTS = (
    (0.00, -0.94, 90.0, 1.0, 1.0, 0.90, 0.00, -1.0, 0.0),     # centre, floor
    (-1.30, -0.94, 72.0, 1.0, 1.0, 0.90, 0.21, -1.0, 0.33),   # left, floor, leaning in
    (1.30, -0.94, 108.0, -1.0, 1.0, 0.90, 0.21, -1.0, 0.33),  # right, floor (mirror of left)
    (0.00, 0.97, -90.0, -1.0, -1.0, -0.90, 0.47, 1.0, 0.66),  # truss, pointing down
)

_SRC = HELPERS + r"""
#define NPROJ 4
#define MAXB 24
#define PRS 32
#define BMS 12

__device__ __forceinline__ float bilinf(const float* __restrict__ img, float fx, float fy, int w, int h) {
    fx -= 0.5f; fy -= 0.5f;
    int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0;
    const int x1 = min(max(x0 + 1, 0), w - 1), y1 = min(max(y0 + 1, 0), h - 1);
    x0 = min(max(x0, 0), w - 1); y0 = min(max(y0, 0), h - 1);
    const float a = img[y0 * w + x0], b = img[y0 * w + x1], c = img[y1 * w + x0], d = img[y1 * w + x1];
    return (1.f - ty) * ((1.f - tx) * a + tx * b) + ty * ((1.f - tx) * c + tx * d);
}

// G: [0..3] layer-1 offset/scale, [4..7] layer-2
extern "C" __global__
void haze_field(float* __restrict__ hz, const int w, const int h, const float* __restrict__ G, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float hh = 0.5f * h;
    const float px = (x + 0.5f - 0.5f * w) / hh, py = -(y + 0.5f - hh) / hh;
    // two layers of smoke, stretched sideways, drifting and rising at different rates
    const float n1 = fbm(px * 1.3f + G[12], py * 2.2f - G[13], seed + 11u);
    const float n2 = fbm(px * 2.6f - G[14], py * 3.4f - G[15], seed + 977u);
    hz[y * w + x] = 0.6f * n1 + 0.4f * n2;
}

__device__ __forceinline__ float line_aa(float q, float lw, float fw) {
    const float d = fabsf(q - floorf(q + 0.5f));
    return (1.f - smooth(0.f, lw + fw, d)) * (lw / (lw + fw));
}

// G (globals): 0 core width wc | 1 glow radius Rg | 2 core amp | 3 glow amp | 4 haze | 5 atten k | 6 ambient amp
//   7 sheet mix | 8 exposure | 9 pixel size (hh units) | 10 divergence | 11 sheet F1 | 12-15 haze phases
//   16 sheet F2 | 17 end-dot amp | 18 flare amp | 19 sheet gain
// PR (per projector, PRS floats): 0 weight | 1,2 origin | 3,4 aim cos/sin | 5 k | 6 1/k | 7 nb | 8 theta0 | 9 s
//   10,11 sheet window lo/hi (plane angle) | 12 extra index window (scan wobble) | 13 cut kind | 14 cut y
//   15 flare amp | 16..18 flare colour | 19 fan centre angle | 20..23 ripple phases | 24 max(k, 1/k)
// BM (per beam, BMS floats): 0,1 dir | 2 brightness | 3 sheet brightness | 4..6 linear colour | 7 length | 8 end amp
//   9 glow gain (beat bloom)
extern "C" __global__
void laser_fans(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ hz, const int hw,
                const int hhz, const float* __restrict__ G, const float* __restrict__ PR, const float* __restrict__ BM)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float hh = 0.5f * H;
    const float px = (x + 0.5f - 0.5f * W) / hh, py = -(y + 0.5f - hh) / hh;
    const float haze = G[4];
    const float n = bilinf(hz, (x + 0.5f) * hw / W, (y + 0.5f) * hhz / H, hw, hhz);
    const float hzm = fmaxf(0.1f, 1.f + haze * 3.4f * (n - 0.5f));         // local haze density
    const float hzc = 0.7f + 0.3f * hzm;                                    // cores barely care
    const float wc0 = G[0], Rg0 = G[1], camp = G[2], gamp = G[3] * hzm, attk = G[5];
    const float amb = G[6] * hzm, smix = G[7], pix = G[9], dv = G[10];
    float ar = 0.f, ag = 0.f, ab = 0.f;

    for (int j = 0; j < NPROJ; ++j) {
        const float* pr = PR + PRS * j;
        const float wP = pr[0];
        if (wP <= 0.f) continue;
        const float rx = px - pr[1], ry = py - pr[2];
        const float r = sqrtf(rx * rx + ry * ry) + 1e-6f;
        const float ca = pr[3], sa = pr[4];
        const float along = rx * ca + ry * sa, perp = -rx * sa + ry * ca;
        const float th = atan2f(perp, along * pr[6]);                   // angle in the fan's own plane
        const int nb = (int)pr[7];
        const float th0 = pr[8], s = pr[9], as = fabsf(s);
        const float fi = (th - th0) / s;                                // fractional beam index
        // ceiling / floor cut
        float cutm = 1.f, cuts = 1.f, cuty = pr[14];                   // hard cut (sheet), soft cut (haze)
        if (pr[13] > 0.5f) { cutm = 1.f - smooth(cuty - 0.012f, cuty + 0.004f, py); cuts = 1.f - smooth(cuty - 0.12f, cuty + 0.03f, py); }
        else if (pr[13] < -0.5f) { cutm = smooth(cuty - 0.004f, cuty + 0.012f, py); cuts = smooth(cuty - 0.03f, cuty + 0.12f, py); }

        // ---- fan-level haze cone + liquid-sky sheet (interpolated beam brightness / colour)
        const float soft = 0.6f * as + 0.05f;
        const float win = smooth(pr[10] - soft, pr[10] + 0.5f * soft, th) * (1.f - smooth(pr[11] - 0.5f * soft, pr[11] + soft, th));
        if (win > 0.f) {
            const float fc = fminf(fmaxf(fi, 0.f), (float)(nb - 1));
            const int i0 = min((int)floorf(fc), nb - 1), i1 = min(i0 + 1, nb - 1);
            const float f = fc - i0;
            const float* b0 = BM + (j * MAXB + i0) * BMS;
            const float* b1 = BM + (j * MAXB + i1) * BMS;
            const float sb = b0[3] + (b1[3] - b0[3]) * f;
            const float cr = b0[4] + (b1[4] - b0[4]) * f, cg = b0[5] + (b1[5] - b0[5]) * f,
                        cb = b0[6] + (b1[6] - b0[6]) * f;
            float v = amb * expf(-1.1f * r) * (0.35f + 0.65f * smooth(0.f, 0.25f, r)) * cuts;
            float vs = 0.f;
            if (smix > 0.f) {
                // plane coordinates: u across, w along (foreshortened axis stretched back by 1/k)
                const float u = perp, wv = along * pr[6];
                const float rp = sqrtf(u * u + wv * wv);
                const float thc = th - pr[19];
                const float F1 = G[11], F2 = G[16];
                const float fwp = pix * pr[24] * 1.3f;                  // pixel footprint in plane units
                // waves roll outward through the sheet; their crests bend (wq) and brighten / dim along their
                // length (mq), so the surface reads as liquid rather than a mesh
                const float wq = 0.6f * __sinf(thc * 5.f + 6.2831853f * pr[21])
                               + 0.4f * __sinf(rp * 2.3f - thc * 3.f + 6.2831853f * pr[22]);
                const float q1 = rp * F1 + wq + 0.45f * __sinf(rp * 1.3f - 6.2831853f * pr[22]) - pr[20];
                const float q2 = rp * F2 * (1.f + 0.15f * __sinf(thc * 3.f - 6.2831853f * pr[22])) + thc * 2.4f
                               - 0.6f * wq - pr[23];
                const float c1 = 0.5f + 0.5f * __cosf(6.2831853f * q1);
                const float c2 = 0.5f + 0.5f * __cosf(6.2831853f * q2);
                const float b1 = c1 * c1 * c1, b2 = c2 * c2;
                const float l1 = line_aa(q1, 0.035f, F1 * fwp);
                const float mq = 0.5f + 0.5f * __sinf(thc * 7.f - rp * 2.6f + 6.2831853f * pr[23] + 1.3f * wq);
                const float rip = (0.15f + 0.85f * mq * mq) * (0.55f * b1 * b1 + 0.8f * l1) * (0.7f + 0.6f * b2)
                                + 0.1f * b2;
                const float sw = 0.17f + 0.95f * rip;
                vs = smix * G[19] * sw * expf(-0.32f * r) * (0.55f + 0.45f * hzm) * smooth(0.f, 0.06f, r);
            }
            v += vs * cutm;
            // the sheet's bright edge where it meets the ceiling / floor
            if (smix > 0.f && pr[13] != 0.f) {
                const float de = (py - cuty) / 0.010f;
                v += smix * G[19] * 0.8f * __expf(-de * de) * __expf(-0.25f * r);
            }
            v *= win * sb;
            ar += v * cr; ag += v * cg; ab += v * cb;
        }

        // ---- beams whose glow can reach this pixel
        const float Rmax = Rg0 * (1.f + dv * LMAX_K);
        const float angR = fminf(1.5708f * Rmax / r, 3.1415927f) * pr[24];   // >= asin(Rmax / r)
        const float wwin = pr[12] + angR / as + 0.5f;
        const int ia = max(0, (int)floorf(fi - wwin)), ib = min(nb - 1, (int)ceilf(fi + wwin));
        for (int i = ia; i <= ib; ++i) {
            const float* bm = BM + (j * MAXB + i) * BMS;
            const float br = bm[2];
            if (br <= 0.f) continue;
            const float dx = bm[0], dy = bm[1];
            const float t = rx * dx + ry * dy;
            if (t <= 0.f) continue;
            const float d = fabsf(rx * dy - ry * dx);
            const float L = bm[7];
            const float tt = fminf(t, L);
            const float sp = 1.f + dv * tt;
            const float Rg = Rg0 * sp;
            if (d >= Rg) continue;
            const float wc = wc0 * sp;
            const float endm = 1.f - smooth(L - 2.f * wc, L + 2.f * wc, t);
            const float att = br * __expf(-attk * tt) * (1.f + 0.5f * __expf(-10.f * t));
            const float xc = d / wc;
            const float core = camp * hzc * __expf(-xc * xc) * endm;
            const float xg = d / Rg;
            const float g1 = 1.f - xg * xg;
            const float glow = gamp * bm[9] * g1 * g1 * g1 * endm;
            float v = att * (core + glow) * G[20];
            float wh = att * core * G[20];
            // hot dot where the beam hits the ceiling / floor
            if (bm[8] > 0.f) {
                const float ex = rx - dx * L, ey = ry - dy * L;
                const float de2 = (ex * ex + ey * ey) / (0.36f * Rg * Rg);
                if (de2 < 1.f) {
                    const float wd = 2.4f * wc / (0.6f * Rg);
                    const float g2 = 1.f - de2;
                    const float dotv = bm[8] * br * __expf(-attk * L) * (1.6f * __expf(-de2 / (wd * wd)) + 0.5f * g2 * g2);
                    v += dotv; wh += 0.6f * dotv;
                }
            }
            const float whc = fminf(wh, 1.2f);
            const float w2 = 0.22f * whc * whc;                         // white-hot where the light piles up
            ar += v * bm[4] + w2; ag += v * bm[5] + w2; ab += v * bm[6] + w2;
        }

        // ---- aperture hot spot: core, haze halo and a thin horizontal streak
        if (pr[15] > 0.f) {
            const float d2 = rx * rx + ry * ry;
            const float fl = pr[15] * (1.f / (1.f + d2 * 6000.f) + (0.06f + 0.18f * haze) / (1.f + d2 * 90.f)
                                       + 0.16f / (1.f + rx * rx * 9.f + ry * ry * 30000.f));
            const float wf = 0.5f * pr[15] / (1.f + d2 * 12000.f);
            ar += fl * pr[16] + wf; ag += fl * pr[17] + wf; ab += fl * pr[18] + wf;
        }
    }
    // tonemap (soft shoulder), then sRGB-ish encode
    const float ex = G[8];
    const float tr = 1.f - __expf(-ar * ex), tg = 1.f - __expf(-ag * ex), tb = 1.f - __expf(-ab * ex);
    out[y * W + x] = pack(make_float4(255.f * __powf(fmaxf(tr, 0.f), 0.4545f), 255.f * __powf(fmaxf(tg, 0.f), 0.4545f),
                                      255.f * __powf(fmaxf(tb, 0.f), 0.4545f), 255.f));
}
""".replace("LMAX_K", f"{LMAX:.3f}f")


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _pal_np(t: np.ndarray, name: str) -> np.ndarray:
    """The shared cosine palette (same formula as PALETTE_CUDA's pal) on the host, sRGB 0..1, shape (..., 3)."""
    a, b, c, d = (np.asarray(v, np.float64) for v in PALETTES[name])
    tt = np.asarray(t, np.float64)[..., None]
    return np.clip(a + b * np.cos(2.0 * math.pi * (c * tt + d)), 0.0, 1.0)


def _laserise(c: np.ndarray) -> np.ndarray:
    """Push a palette colour towards a pure laser hue (more saturated) and lift its brightness (lasers are never
    dim: max channel >= 0.55, so a dark palette stretch or a dark Maestro colour still reads), then to linear."""
    m = np.max(c, axis=-1, keepdims=True)
    hue = np.where(m > 1e-3, c / np.maximum(m, 1e-3), 1.0)
    return (hue ** 1.8 * (0.55 + 0.45 * m)) ** 2.2


_STD = standard_scene_params(palette="neon", render_scale=0.75, period=0.35, drift=0.04)


class LaserFans(Source):
    name = "laser_fans"
    label = "Laser fans"
    description = "Concert lasers: fans of thin beams from 1-4 projectors sweep, chase and scan through haze, " \
                  "morphing into a rippling liquid-sky sheet."
    params = {
        "sheet_mix": num(0.0, 1.0, 0.0, "Beams -> liquid sky sheet", tier="regional", basic=True),
        "fan_spread": num(10.0, 160.0, 70.0, "Fan spread", step=1.0, unit="°", tier="regional", basic=True),
        "projectors": num(1.0, 4.0, 3.0, "Projectors", step=0.05, tier="regional"),
        "beam_density": num(4.0, 24.0, 12.0, "Beams per fan", step=0.1, tier="regional"),
        "haze": num(0.0, 1.0, 0.5, "Haze", tier="regional"),
        "beam_width": num(0.0, 1.0, 0.3, "Beam width", tier="regional"),
        "scan_mix": num(0.0, 1.0, 0.25, "Rigid sweep -> independent beam scan", tier="regional"),
        "palette_period": num(0.0, 3.0, 0.35, "Colour spread across beams (0 = mono)", tier="regional"),
        "pan": num(0.0, 1.0, 0.5, "Pan", tier="realtime", bind="level"),
        "tilt": num(0.0, 1.0, 0.55, "Tilt", tier="realtime", bind="level"),
        "beat_kick": num(0.0, 2.0, 1.0, "Beat response (chase step + sweep kick)", tier="regional"),
        **{k: v for k, v in _STD.items() if k != "palette_period"},
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.hw, self.hh_ = max(8, self.w // HAZE_DIV), max(8, self.h // HAZE_DIV)
        self.hz = cp.zeros((self.hh_, self.hw), cp.float32)
        mod = raw_module(_SRC, ("--std=c++14", "--use_fast_math"))
        self.k_haze = mod.get_function("haze_field")
        self.k_main = mod.get_function("laser_fans")
        # one packed parameter upload per frame: [G | PR | BM]
        n_pr, n_bm = NPROJ * PRS, NPROJ * MAXB * BMS
        self.ALL = cp.zeros(NG + n_pr + n_bm, cp.float32)
        self.G, self.PR, self.BM = self.ALL[:NG], self.ALL[NG:NG + n_pr], self.ALL[NG + n_pr:]
        self.ALLh = np.zeros(NG + n_pr + n_bm, np.float32)
        self.Gh = self.ALLh[:NG]
        self.PRh = self.ALLh[NG:NG + n_pr].reshape(NPROJ, PRS)
        self.BMh = self.ALLh[NG + n_pr:].reshape(NPROJ, MAXB, BMS)
        rng = np.random.default_rng(int(params["seed"]))
        rank = rng.uniform(0.0, 1.0, (NPROJ, MAXB))                 # order in which beams light up with energy
        self.rank = np.where(np.arange(MAXB)[None, :] % 2 == 0, rank * 0.8, 0.2 + rank * 0.8)
        self.scan_f = rng.uniform(0.7, 1.3, (NPROJ, MAXB))
        self.scan_o = rng.uniform(0.0, 1.0, (NPROJ, MAXB))
        self.flick = rng.uniform(0.0, 1.0, (64, NPROJ, MAXB))
        # mirror-pair symmetry: the right projector reuses the left projector's randoms
        self.rank[2], self.scan_f[2], self.scan_o[2], self.flick[:, 2] = (
            self.rank[1], self.scan_f[1], self.scan_o[1], self.flick[:, 1])
        sl = np.asarray(SLOTS, np.float64)
        (self.OX, self.OY, aim, self.MIR, self.CUT, self.CUTY, self.SOFF, self.PANS, self.COFF) = sl.T
        self.AIM = np.radians(aim)
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        self.ph = {"sweep": 0.0, "chase": 0.0, "scan": 0.0, "breath": 0.0, "flick": 0.0,
                   "r1": 0.0, "r2": 0.0, "r3": 0.0, "r4": 0.0}
        self.hzp = np.zeros(4)
        self.drift = 0.0
        self.e_dens = None
        self.pan_s = None
        self.tilt_s = None
        self.armed = True
        self.t_last = -1.0
        self.t = 0.0
        self.waves: list[list[float]] = []     # [age 0..1, direction, amplitude]
        self.wdir = 1.0

    # ------------------------------------------------------------------ host-side show logic
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        e1 = _ss(0.1, 0.85, e)                 # remapped energy
        ea = 0.5 + 0.5 * _ss(0.0, 1.0, e1)     # amplitude curve, floor 50 %
        rate = 0.4 + 1.2 * e1                  # rate curve, floor 40 %
        k = pulse * (0.3 + 0.7 * e)
        kick = float(p["beat_kick"])
        self.t += dt

        # slewed density target, one-pole smoothed pan / tilt (150 ms)
        a_d = 1.0 - math.exp(-dt / 1.5) if dt > 0 else 1.0
        a_pt = 1.0 - math.exp(-dt / 0.15) if dt > 0 else 1.0
        tgt = e1 ** 1.5
        self.e_dens = tgt if self.e_dens is None else self.e_dens + (tgt - self.e_dens) * a_d
        pan = min(max(float(p["pan"]), 0.0), 1.0)     # bindings may offset these past the ends
        tilt = min(max(float(p["tilt"]), 0.0), 1.0)
        self.pan_s = pan if self.pan_s is None else self.pan_s + (pan - self.pan_s) * a_pt
        self.tilt_s = tilt if self.tilt_s is None else self.tilt_s + (tilt - self.tilt_s) * a_pt

        # beat: rising-edge detector with hysteresis spawns a chase step that owns its lifetime
        if pulse < 0.3:
            self.armed = True
        if self.armed and pulse > 0.6 and self.t - self.t_last >= 0.15:
            self.armed = False
            self.t_last = self.t
            amp = min(1.6, kick * math.sqrt(k))
            if amp > 0.02:
                self.waves.append([0.0, self.wdir, amp])
                self.wdir = -self.wdir
                self.waves = self.waves[-4:]
        for w in self.waves:
            w[0] += dt * max(0.5, min(spd, 2.0)) / 0.45
        self.waves = [w for w in self.waves if w[0] < 1.0]

        # integrate phases (cycles): every rate x speed; the sweep gets the beat kick
        ph = self.ph
        ph["sweep"] += 0.28 * rate * (1.0 + 1.6 * kick * k) * spd * dt
        ph["chase"] += 0.42 * rate * spd * dt
        ph["scan"] += 0.55 * rate * spd * dt
        ph["breath"] += 0.09 * rate * spd * dt
        ph["flick"] += 11.0 * spd * dt
        for key, base in (("r1", 0.55), ("r2", 0.13), ("r3", 0.09), ("r4", 0.31)):
            ph[key] = (ph[key] + base * rate * spd * dt) % 1.0
        self.hzp += np.array([0.045, 0.030, 0.028, 0.050]) * spd * dt
        self.drift += float(p["drift_speed"]) * spd * dt

        # regional knobs
        nproj = float(p["projectors"])
        wC = (2.0 - nproj) if nproj < 2.0 else min(1.0, nproj - 2.0)
        wLR = min(max(nproj - 1.0, 0.0), 1.0)
        W = np.array([min(max(wC, 0.0), 1.0), wLR, wLR, min(max(nproj - 3.0, 0.0), 1.0)])
        spread = math.radians(float(p["fan_spread"]))
        dens = float(p["beam_density"])
        nb = min(MAXB, int(math.ceil(dens - 1e-4)))
        haze = float(p["haze"])
        bw = float(p["beam_width"])
        scan = float(p["scan_mix"])
        period = float(p["palette_period"])
        kt = 0.3 + 1.0 * float(self.tilt_s)            # tilt -> fan plane foreshortening
        pan_a = math.radians(70.0) * (float(self.pan_s) - 0.5)

        # energy-driven beam activity
        frac = 0.42 + 0.58 * self.e_dens                # fraction of beams lit
        chase_depth = 0.22 + 0.45 * _ss(0.0, 1.0, e1)
        flick_depth = 0.45 * _ss(0.45, 1.0, e1)
        hb = 0.6 + 0.7 * e1                             # haze brightness

        # ---- per projector (4,) and per beam (4, nb), vectorised
        two_pi = 2.0 * math.pi
        I = np.arange(nb, dtype=np.float64)
        MIR = self.MIR[:, None]
        breath = 1.0 + 0.16 * ea * np.sin(two_pi * (ph["breath"] + 0.5 * self.SOFF))
        s = spread * breath / max(dens - 1.0, 1e-3)                                   # (4,) beam spacing
        sw = ph["sweep"] + self.SOFF
        sweep = 0.72 * np.sin(two_pi * sw) + 0.28 * np.sin(two_pi * (0.43 * sw + 0.3))
        centre = self.MIR * math.radians(26.0) * ea * (1.0 - 0.55 * scan) * sweep + self.PANS * pan_a
        th_grid = centre[:, None] + MIR * (I - 0.5 * (dens - 1.0))[None, :] * s[:, None]
        a_scan = scan * (0.55 * s + math.radians(5.0) * ea)                           # (4,)
        wob = a_scan[:, None] * np.sin(two_pi * (ph["scan"] * self.scan_f[:, :nb] + self.scan_o[:, :nb]))
        th_b = np.clip(th_grid + MIR * wob, -3.0, 3.0)
        ang = self.AIM[:, None] + np.arctan2(np.sin(th_b), kt * np.cos(th_b))         # foreshortened, world
        dxs, dys = np.cos(ang), np.sin(ang)
        toward = dys * self.CUT[:, None]                                               # heading for ceiling / floor
        L = np.where(toward > 0.03, np.abs(self.CUTY - self.OY)[:, None] / np.maximum(toward, 0.03), LMAX)
        L = np.minimum(L, LMAX)
        end_amp = (L < LMAX - 0.05) * (0.35 + 0.4 * e1)
        fade = np.clip(dens - I, 0.0, 1.0)                                             # fractional last beam
        active = np.clip((frac - self.rank[:, :nb]) / 0.12 + 0.5, 0.0, 1.0)
        xb = I / max(dens - 1.0, 1.0)
        u = (xb - ph["chase"]) % 1.0
        chase = 1.0 - chase_depth + chase_depth * (0.25 + (0.5 + 0.5 * np.cos(two_pi * u)) ** 2)
        fp = ph["flick"]
        fn = int(math.floor(fp))
        ff = fp - fn
        ff = ff * ff * (3.0 - 2.0 * ff)
        flk = self.flick[fn % 64, :, :nb] * (1.0 - ff) + self.flick[(fn + 1) % 64, :, :nb] * ff
        boost = np.zeros(nb)
        for age, wdir, wamp in self.waves:             # chase step: a bright front with a short trail
            xw = xb if wdir > 0 else 1.0 - xb
            pos = -0.12 + 1.24 * age
            env = wamp * (1.0 - age) ** 0.6
            dxw = xw - pos
            front = np.exp(-(dxw / 0.11) ** 2)
            trail = np.where(dxw < 0.0, 0.45 * np.exp(dxw / 0.22), 0.0)
            boost = np.maximum(boost, env * np.maximum(front, trail))
        base = active * chase * (1.0 - flick_depth * flk)
        wf = W[:, None] * fade[None, :]
        bright = wf * np.maximum(base * (1.0 + 1.2 * boost), 1.1 * boost)
        sheet_b = wf * hb * chase * (1.0 + 1.0 * boost)
        col = _laserise(_pal_np(xb[None, :] * period + self.COFF[:, None] * period + self.drift, p["palette"]))

        B = self.BMh
        B[:, nb:, 2:4] = 0.0
        B[:, :nb, 0], B[:, :nb, 1] = dxs, dys
        B[:, :nb, 2], B[:, :nb, 3] = bright, sheet_b
        B[:, :nb, 4:7] = col
        B[:, :nb, 7], B[:, :nb, 8] = L, end_amp
        B[:, :nb, 9] = 1.0 + 1.6 * boost[None, :]
        Pr = self.PRh
        Pr[:, 0] = W
        Pr[:, 1], Pr[:, 2] = self.OX, self.OY
        Pr[:, 3], Pr[:, 4] = np.cos(self.AIM), np.sin(self.AIM)
        Pr[:, 5], Pr[:, 6] = kt, 1.0 / kt
        Pr[:, 7] = nb
        Pr[:, 8] = th_grid[:, 0]
        Pr[:, 9] = self.MIR * s
        Pr[:, 10] = th_grid.min(axis=1) - 0.2 * s
        Pr[:, 11] = th_grid.max(axis=1) + 0.2 * s
        Pr[:, 12] = (a_scan + 1e-6) / np.maximum(s, 1e-4)
        Pr[:, 13], Pr[:, 14] = self.CUT, self.CUTY
        mean_b = bright.sum(axis=1) / (np.maximum(W, 1e-4) * max(dens, 1.0))
        Pr[:, 15] = W * (0.25 + 0.5 * e1) * (0.4 + 0.6 * mean_b) * (1.0 + 0.3 * k)
        fc = col.mean(axis=1)
        Pr[:, 16:19] = 0.8 * fc / np.maximum(fc.max(axis=1, keepdims=True), 1e-3)
        Pr[:, 19] = centre
        Pr[:, 20:24] = (ph["r1"], ph["r2"], ph["r3"], ph["r4"])
        Pr[:, 24] = max(kt, 1.0 / kt)

        pix = 1.0 / (0.5 * self.h)
        wc = max(0.0008 + 0.0055 * bw * bw + 0.0012 * bw, 0.75 * pix)
        G = self.Gh
        G[0] = wc
        G[1] = wc * (4.0 + 9.0 * haze) + 0.028 * haze + 2.0 * pix                     # glow radius
        G[2] = 1.5
        G[3] = (0.16 + 0.45 * haze) * hb * math.sqrt(12.0 / max(dens, 12.0))
        G[4] = haze
        G[5] = 0.08 + 0.45 * haze
        G[6] = (0.02 + 0.09 * haze) * min(1.0, 10.0 / max(dens, 4.0)) * hb * (0.35 + 0.65 * min(1.0, spread / 1.2))
        G[7] = float(p["sheet_mix"])
        G[8] = float(p["intensity"]) * (0.85 + 0.3 * e1)
        G[9] = pix
        G[10] = 0.22
        G[11] = 7.0
        G[12:16] = self.hzp
        G[16] = 4.3
        G[19] = 0.55
        G[20] = 1.0 - 0.97 * _ss(0.0, 1.0, float(p["sheet_mix"])) ** 0.8  # beams give way to the sheet
        self.ALL.set(self.ALLh)
        if haze > 0.0:
            launch_2d(self.k_haze, self.hw, self.hh_, (self.hz, np.int32(self.hw), np.int32(self.hh_), self.G,
                                                       np.uint32(self.seed)))
        launch_2d(self.k_main, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.hz,
                                                np.int32(self.hw), np.int32(self.hh_), self.G, self.PR, self.BM))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.hz = None
        self.ALL = self.G = self.PR = self.BM = None
