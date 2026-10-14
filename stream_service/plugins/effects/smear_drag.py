"""Drip Smear: a luminance-ordered drip / wind smear that lives in a feedback buffer -- a flowing pixel sort.

Source: https://github.com/Vidvox/ISF-Files/blob/master/ISF/Sorting%20Smear.fs
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/Echo%20Trace.fs
Author: VIDVOX (ISF Sorting Smear, Echo Trace)
License: MIT  (repository Vidvox/ISF-Files LICENSE: MIT)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: Sorting Smear's per-frame "adapt toward the live source, then swap a
fraction toward a brighter/darker neighbour" is kept, but the fixed H/V neighbour becomes a neighbour along an
arbitrary direction angle (down = melting drip, sideways = wind, up = rising smoke) at a 4K-scaled step, run as two
compare-swap passes per frame with incommensurate steps so the front never ladders; hard if-tests become smooth
gates (no flicker); Echo Trace's luminance threshold becomes a soft key on which pixels may drip; a polarity axis
morphs "bright sinks" (light drips) -> symmetric sort -> "dark sinks" (ink drips); the direction field meanders
with smooth noise of integrated time and drip strength varies per column so it reads as real drips; rates and the
adapt are frame-rate independent with an 8-bit recovery bias so trails really return to the source; new: the
smeared pixels (and only those) take a palette tint indexed by luma + drift).

Buffers: two uchar4 history frames ping-pong plus one scratch frame for the first pass (a gather can't read and
write one buffer).  Pass 0: adapt + sort (prev -> tmp).  Pass 1: sort (tmp -> next) and the tinted output.

Realtime: amount -> m = smoothstep(amount); adapt = 1 at m = 0 (pure source, identity), the sort rate and tint
scale with m.  pulse kicks the sort rate (a burst of dripping) and adds a direction wobble amplitude.  History is
reset to src when amount leaves 0 or dt > 0.5 s; dt = 0 means no step.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num, palette_params, standard_effect_params

TAU = 2.0 * math.pi
NP = 32
# P layout (floats)
#  0 keep (history weight vs src, pass 0) | 1 threshold | 2 polarity (1 = bright sinks) | 3 back-swap weight
#  4 tint * m | 5 direction angle (rad, incl. wobble) | 6 wander amplitude (rad) | 7 wander freq (cells per H)
#  8 wander phase | 9 patch phase | 10,11 cos/sin of direction | 12 columns per H | 13 patch depth
#  14 8-bit recovery bias | 15 seed | 16 palette period | 17 drift | 20..31 palette coeffs
_SRC = HELPERS + PALETTE_CUDA + r"""
extern "C" __global__
void smear_drag(const uchar4* __restrict__ src, const uchar4* __restrict__ hin, uchar4* __restrict__ hout,
                uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
                const int final_pass, const float step, const float rate)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = x + 0.5f, py = y + 0.5f;
    const float invH = 1.f / (float)H;
    const unsigned int seed = (unsigned int)P[15];

    // direction field: base angle (0 = down) + a smooth meandering wander
    const float nf = invH * P[7];
    const float wander = 2.f * vnoise(px * nf + P[8], py * nf - 0.7f * P[8], seed) - 1.f;
    float sn, cs;
    __sincosf(P[5] + P[6] * wander, &sn, &cs);
    const float sx = sn * step, sy = cs * step;
    // per-column drip strength (coordinate across the base direction), slowly evolving
    const float q = (px * P[10] - py * P[11]) * invH * P[12];
    const float patch = 1.f - P[13] * smooth(0.3f, 0.7f, vnoise(q, P[9], seed + 7u));

    // adapt: pull the history back toward the live source (with an 8-bit bias so it really converges)
    float4 base = ld(hin, x, y, W, H);
    if (!final_pass) {
        const float4 s = ld(src, x, y, W, H);
        const float k = P[0], b = P[14];
        float d;
        d = (base.x - s.x) * k; base.x = s.x + copysignf(fmaxf(fabsf(d) - b, 0.f), d);
        d = (base.y - s.y) * k; base.y = s.y + copysignf(fmaxf(fabsf(d) - b, 0.f), d);
        d = (base.z - s.z) * k; base.z = s.z + copysignf(fmaxf(fabsf(d) - b, 0.f), d);
    }

    // compare-swap toward the up-stream / down-stream neighbours, by luminance order
    const float4 U = bilin(hin, px - sx, py - sy, W, H);
    const float4 D = bilin(hin, px + sx, py + sy, W, H);
    const float lb = luma(base), lU = luma(U), lD = luma(D);
    const float thr = P[1], pol = P[2], kg = 0.035f;
    const float wA = pol * smooth(0.f, kg, lU - lb) * smooth(thr - 0.06f, thr + 0.06f, lU)
                   + (1.f - pol) * smooth(0.f, kg, lb - lU) * smooth(thr - 0.06f, thr + 0.06f, 1.f - lU);
    const float wB = pol * smooth(0.f, kg, lb - lD) * smooth(thr - 0.06f, thr + 0.06f, lb)
                   + (1.f - pol) * smooth(0.f, kg, lD - lb) * smooth(thr - 0.06f, thr + 0.06f, 1.f - lb);
    const float r = rate * patch;
    float4 res = mix4(base, U, r * wA);
    res = mix4(res, D, r * wB * P[3]);

    const int i = y * W + x;
    hout[i] = pack(res);
    if (final_pass) {
        // palette tint on what has been smeared away from the source (the live picture stays true)
        const float4 s = ld(src, x, y, W, H);
        const float diff = (fabsf(res.x - s.x) + fabsf(res.y - s.y) + fabsf(res.z - s.z)) * (1.f / 765.f);
        const float mask = smooth(0.015f, 0.2f, diff) * P[4];
        if (mask > 0.f) {
            const float V = fmaxf(fmaxf(res.x, res.y), res.z);
            const float3 pc = pal(luma(res) * P[16] + P[17] + 0.15f * wander, P + 20);
            const float pm = V / fmaxf(fmaxf(fmaxf(pc.x, pc.y), pc.z), 0.05f);
            res = mix4(res, make_float4(pc.x * pm, pc.y * pm, pc.z * pm, 255.f), mask);
        }
        out[i] = pack(res);
    }
}
"""


def _smoothstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


class SmearDrag(Effect):
    name = "smear_drag"
    stage = "feedback"
    label = "Drip Smear"
    description = ("A living pixel sort: bright (or dark) pixels drip along a direction -- down is a melting drip, "
                   "sideways is wind -- while an adapt rate pulls the smear back toward the live picture.")
    _pal = palette_params("acid", 1.2, 0.05)
    params = {
        **standard_effect_params(),
        "direction": num(-180.0, 180.0, 0.0, "Direction (0 = drip down, 90 = wind right)", step=1.0, unit="°",
                         tier="regional", basic=True),
        "sort_rate": num(0.0, 1.0, 0.7, "Sort rate", tier="regional"),
        "threshold": num(0.0, 1.0, 0.22, "Threshold (which pixels drip)", tier="regional"),
        "recovery": num(0.0, 1.0, 0.3, "Recovery (adapt to source)", tier="regional"),
        "streak_length": num(0.0, 1.0, 0.5, "Streak length", tier="regional"),
        "polarity": num(0.0, 1.0, 0.85, "Polarity (ink drips -> light drips)", tier="regional"),
        "meander": num(0.0, 1.0, 0.4, "Meander (liquid irregularity)", tier="regional"),
        "tint": num(0.0, 1.0, 0.4, "Drip palette tint", tier="regional"),
        "palette": enum(list(PALETTES), "acid", "Palette", tier="regional"),
        "palette_period": _pal["palette_period"],
        "drift_speed": _pal["drift_speed"],
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("smear_drag")
        W, H = ctx.width, ctx.height
        self.hist = [cp.zeros((H, W, 4), cp.uint8), cp.zeros((H, W, 4), cp.uint8)]
        self.tmp = cp.zeros((H, W, 4), cp.uint8)
        self.cur = 0
        self.need_reset = True
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.wob_phase = 0.0
        self.wander_phase = 0.0
        self.patch_phase = 0.0
        self.drift = 0.0
        self.pal_name = None
        self.coeffs = palette_coeffs("acid")

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        a = min(max(float(p["amount"]), 0.0), 1.0)
        kp = min(max(float(p["pulse"]), 0.0), 1.0)
        dt = max(float(dt), 0.0)
        if dt > 0.5:
            self.need_reset = True
        dtc = min(dt, 0.1)
        meander = float(p["meander"])

        # host-integrated phases (always advance, so re-entry is seamless); wrapped before upload
        self.wob_phase = math.fmod(self.wob_phase + 3.3 * dtc, TAU)
        self.wander_phase = math.fmod(self.wander_phase + (0.10 + 0.25 * meander) * (1.0 + 1.5 * kp) * dtc, 4096.0)
        self.patch_phase = math.fmod(self.patch_phase + 0.09 * dtc, 4096.0)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dtc, 100.0)

        if a <= 1e-4:
            cp.copyto(out, src)
            self.need_reset = True
            return
        prev, nxt = self.hist[self.cur], self.hist[1 - self.cur]
        if self.need_reset:
            cp.copyto(prev, src)
            self.need_reset = False

        W, H = self.ctx.width, self.ctx.height
        m = _smoothstep(a)
        mp = m * kp
        dt60 = min(dt * 60.0, 4.0)
        rec = float(p["recovery"])

        # adapt per 1/60 s: 0.003 (long smears) .. 0.3 (snaps back); ramped in log space by the amount so mid
        # amounts already smear boldly; at m = 0 adapt = 1 (pure source)
        mr = min(1.0, 1.6 * m)
        adapt60 = (0.003 * 100.0 ** rec) ** mr
        keep = (1.0 - adapt60) ** dt60 if dt60 > 0 else 1.0
        # sort fraction per pass per 1/60 s; pulse bursts it
        r60 = 0.92 * float(p["sort_rate"]) ** 1.2
        r60 = min(0.97, r60 * (1.0 + 1.6 * kp) + 0.3 * kp) * mr
        rate = 1.0 - (1.0 - r60) ** dt60 if dt60 > 0 else 0.0
        step = (2.0 + 24.0 * float(p["streak_length"]) ** 1.3) * H / 2160.0

        base_ang = math.radians(float(p["direction"]))
        ang = base_ang + (0.06 * meander + math.radians(16.0) * mp) * math.sin(self.wob_phase)

        if p["palette"] != self.pal_name:
            self.pal_name = p["palette"]
            self.coeffs = palette_coeffs(self.pal_name)
        h = self.host
        h[0] = keep
        h[1] = float(p["threshold"])
        h[2] = float(p["polarity"])
        h[3] = 0.5
        h[4] = float(p["tint"]) * m
        h[5] = ang
        h[6] = 0.55 * meander
        h[7] = 2.2
        h[8] = self.wander_phase
        h[9] = self.patch_phase
        h[10], h[11] = math.cos(ang), math.sin(ang)
        h[12] = 7.0
        h[13] = 0.85 * meander
        h[14] = 0.55 + 0.4 * rec
        h[15] = float(int(p["seed"]) % 65521)
        h[16] = float(p["palette_period"])
        h[17] = self.drift
        h[20:32] = self.coeffs
        self.P.set(h)

        iW, iH = np.int32(W), np.int32(H)
        if dt60 <= 0.0:
            # dt = 0: no step, just present the current history (tinted against the live source)
            launch_2d(self._k, W, H, (src, prev, nxt, out, iW, iH, self.P, np.int32(1), np.float32(step),
                                      np.float32(0.0)))
        else:
            launch_2d(self._k, W, H, (src, prev, self.tmp, out, iW, iH, self.P, np.int32(0), np.float32(step),
                                      np.float32(rate)))
            launch_2d(self._k, W, H, (src, self.tmp, nxt, out, iW, iH, self.P, np.int32(1),
                                      np.float32(step * 0.618), np.float32(rate)))
        self.cur = 1 - self.cur

    def close(self) -> None:
        self.hist = []
        self.tmp = None
