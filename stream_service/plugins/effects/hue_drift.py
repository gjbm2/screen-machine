"""Hue drift: continuous hue rotation with a luminance-dependent spread (rainbow banding across tones), a
channel-cycle mode that slides R->G->B as a continuous matrix, saturation + vibrance, and a pull toward a
drifting palette tint.  The simplest way to make any still feel alive with colour.

Source: https://github.com/pmndrs/postprocessing/blob/main/src/effects/HueSaturationEffect.js
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/Multi%20Hue%20Shift.fs
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/RGBA%20Swap.fs
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/Channel%20Slide.fs  (idea only)
Author: Raoul van Ruschen (pmndrs HueSaturationEffect); VIDVOX (ISF Multi Hue Shift, RGBA Swap); zoidberg (ISF Channel Slide)
License: Zlib + MIT
license_class: permissive
Ported from GLSL by smfx (ideas/changes: Multi Hue Shift's three-band brightness->hue-shift ramp becomes one
continuous spread, phi = phase + spread * (luma - 0.5), so tones fan out into a rainbow; its rgb2hsv/hsv2rgb hue
shift is kept for the vivid "hue" end; RGBA Swap's discrete channel permutations become a continuous circulant
matrix (pmndrs' hue-vector form) that dwells on each permutation and slides between them, with partial chroma
compensation so the slide does not wash out; pmndrs' saturation is applied about the grey axis, plus a vibrance
term that lifts dull pixels more than vivid ones; new: a luminance-keeping "colour" blend toward the shared
cosine palette, indexed by tone + an integrated drift; the amount mix restores chroma magnitude so fades never
pass through grey).

Realtime: amount -> m = smoothstep(amount); the output is the src -> processed mix by m with the chroma magnitude
interpolated (not the vector), so the hue fades in along the shortest arc without a grey dip, and 0 is exactly
src.  pulse -> a hue-rate kick (fast attack, slow release: a whoosh through the spectrum, then a glide) and a
saturation punch.  rate and drift_speed are host-integrated rates; phases are wrapped before upload.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.beat import paced
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num, palette_params, standard_effect_params

NP = 32
# P layout (floats)
#  0 base phase (turns, wrapped) | 1 luma spread (turns across black->white) | 2 channel cycle 0..1
#  3 saturation (incl. pulse punch) | 4 vibrance | 5 tint strength | 6 palette period | 7 palette drift (wrapped)
#  8 m (smoothstep(amount)) | 9..20 palette coeffs
_SRC = HELPERS + PALETTE_CUDA + r"""
__device__ __forceinline__ float fractf(float v) { return v - floorf(v); }

// Multi Hue Shift's rgb2hsv / hsv2rgb (branch form), 0..1
__device__ __forceinline__ float3 rgb2hsv(float r, float g, float b) {
    float px, py, pz, pw;
    if (g < b) { px = b; py = g; pz = -1.f; pw = 2.f / 3.f; } else { px = g; py = b; pz = 0.f; pw = -1.f / 3.f; }
    float qx, qy, qz, qw;
    if (r < px) { qx = px; qy = py; qz = pw; qw = r; } else { qx = r; qy = py; qz = pz; qw = px; }
    const float d = qx - fminf(qw, qy);
    const float e = 1.0e-6f;
    return make_float3(fabsf(qz + (qw - qy) / (6.f * d + e)), d / (qx + e), qx);
}
__device__ __forceinline__ float3 hsv2rgb(float h, float s, float v) {
    const float px = fabsf(fractf(h + 1.f) * 6.f - 3.f);
    const float py = fabsf(fractf(h + 2.f / 3.f) * 6.f - 3.f);
    const float pz = fabsf(fractf(h + 1.f / 3.f) * 6.f - 3.f);
    return make_float3(v * (1.f + s * (clamp01(px - 1.f) - 1.f)),
                       v * (1.f + s * (clamp01(py - 1.f) - 1.f)),
                       v * (1.f + s * (clamp01(pz - 1.f) - 1.f)));
}

extern "C" __global__
void hue_drift(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
               const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int i = y * W + x;
    const uchar4 q = src[i];
    const float sr = q.x * (1.f / 255.f), sg = q.y * (1.f / 255.f), sb = q.z * (1.f / 255.f);
    const float L0 = 0.2126f * sr + 0.7152f * sg + 0.0722f * sb;

    // per-pixel hue angle: the drifting phase fanned out across tones
    const float phi = fractf(P[0] + P[1] * (L0 - 0.5f));

    // (a) vivid HSV hue shift (keeps value and saturation)
    const float3 hsv = rgb2hsv(sr, sg, sb);
    const float3 a = hsv2rgb(fractf(hsv.x + phi), hsv.y, hsv.z);
    float r = a.x, g = a.y, b = a.z;

    // (b) channel cycle: a circulant matrix sliding identity -> R->G -> R->B, dwelling on each permutation
    const float cyc = P[2];
    if (cyc > 1e-4f) {
        const float u = phi * 3.f;
        const float k = floorf(u);
        float f = u - k;
        f = f * f * f * (f * (f * 6.f - 15.f) + 10.f);          // dwell on the pure permutations
        float wp[3] = {0.f, 0.f, 0.f};                            // weight on P0 (id), P1 (R->G), P2 (R->B)
        const int k0 = ((int)k) % 3;
        wp[k0] = 1.f - f;
        wp[(k0 + 1) % 3] += f;
        const float w0 = wp[0], w1 = wp[2], w2 = wp[1];          // circulant slots (pmndrs hue.xyz order)
        float cr = w0 * sr + w1 * sg + w2 * sb;
        float cg = w2 * sr + w0 * sg + w1 * sb;
        float cb = w1 * sr + w2 * sg + w0 * sb;
        // chroma eigenvalue |w0 + w1 w + w2 w^2| dips to 0.5 mid-slide: compensate most of it
        const float lam = sqrtf(fmaxf(w0 * w0 + w1 * w1 + w2 * w2 - w0 * w1 - w1 * w2 - w2 * w0, 1e-4f));
        const float comp = __powf(lam, -0.7f);
        const float av = (cr + cg + cb) * (1.f / 3.f);
        cr = av + (cr - av) * comp; cg = av + (cg - av) * comp; cb = av + (cb - av) * comp;
        r += (cr - r) * cyc; g += (cg - g) * cyc; b += (cb - b) * cyc;
    }

    // saturation (about the grey axis) + vibrance (lifts dull pixels more than vivid ones)
    {
        const float av = (r + g + b) * (1.f / 3.f);
        const float mx = fmaxf(r, fmaxf(g, b)), mn = fminf(r, fminf(g, b));
        const float sp = clamp01((mx - mn) / (mx + 1e-3f));
        const float dull = (1.f - sp) * (1.f - sp);
        const float gs = fmaxf(P[3] * (1.f + P[4] * dull), 0.f);
        r = av + (r - av) * gs; g = av + (g - av) * gs; b = av + (b - av) * gs;
    }

    // pull toward the palette: a luminance-keeping "colour" blend, chroma faded out at black and white
    if (P[5] > 1e-4f) {
        const float Lc = clamp01(0.2126f * r + 0.7152f * g + 0.0722f * b);
        const float3 pc = pal(L0 * P[6] + P[7], P + 9);
        const float Lp = 0.2126f * pc.x + 0.7152f * pc.y + 0.0722f * pc.z;
        const float guard = clamp01(4.f * Lc) * clamp01(4.f * (1.f - Lc));
        const float tr = Lc + (pc.x - Lp) * guard, tg = Lc + (pc.y - Lp) * guard, tb = Lc + (pc.z - Lp) * guard;
        const float t = P[5];
        r += (tr - r) * t; g += (tg - g) * t; b += (tb - b) * t;
    }
    r = clamp01(r); g = clamp01(g); b = clamp01(b);

    // amount: mix src -> processed, interpolating the chroma MAGNITUDE so the fade never washes through grey
    const float m = P[8];
    const float as = (sr + sg + sb) * (1.f / 3.f), ac = (r + g + b) * (1.f / 3.f);
    const float csr = sr - as, csg = sg - as, csb = sb - as;
    const float ccr = r - ac, ccg = g - ac, ccb = b - ac;
    const float am = as + (ac - as) * m;
    const float cmr = csr + (ccr - csr) * m, cmg = csg + (ccg - csg) * m, cmb = csb + (ccb - csb) * m;
    const float ns = sqrtf(csr * csr + csg * csg + csb * csb);
    const float nc = sqrtf(ccr * ccr + ccg * ccg + ccb * ccb);
    const float nm = sqrtf(cmr * cmr + cmg * cmg + cmb * cmb);
    const float target = ns + (nc - ns) * m;
    const float gk = nm > 1e-4f ? fminf(target / nm, 4.f) : 1.f;
    out[i] = pack(make_float4(255.f * (am + cmr * gk), 255.f * (am + cmg * gk), 255.f * (am + cmb * gk), 255.f));
}
"""


def _smoothstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


class HueDrift(Effect):
    name = "hue_drift"
    label = "Hue drift"
    description = ("Continuous hue rotation fanned out across tones into rainbow bands, with a channel-cycle "
                   "mode, saturation/vibrance and a palette tint; the beat whooshes it through the spectrum.")
    stage = "colour"
    tags = {"family": ["colour"], "dims": ["2d"], "technique": ["colour"]}
    _pal = palette_params("neon", 1.0, 0.03)
    params = {
        **standard_effect_params(),
        "rate": paced(num(-1.0, 1.0, 0.05, "Drift rate", unit="turns/s", step=0.005, tier="realtime", bind="rate",
                          basic=True)),
        "offset": num(0.0, 0.5, 0.2, "Hue offset", unit="turn", tier="regional"),
        "luma_spread": num(0.0, 2.0, 0.6, "Rainbow spread across tones", unit="turns", tier="regional",
                           basic=True),
        "channel_cycle": num(0.0, 1.0, 0.0, "Hue -> channel cycle", tier="regional"),
        "saturation": num(0.0, 2.0, 1.15, "Saturation", tier="regional"),
        "vibrance": num(-1.0, 1.0, 0.35, "Vibrance", tier="regional"),
        "tint_strength": num(0.0, 1.0, 0.2, "Palette tint", tier="regional"),
        "punch": num(0.0, 2.0, 0.6, "Beat hue whoosh", unit="turns/s", step=0.05, tier="regional"),
        "sat_punch": num(0.0, 1.0, 0.35, "Beat saturation punch", tier="regional"),
        "palette": enum(list(PALETTES), "neon", "Palette", tier="regional"),
        "palette_period": _pal["palette_period"],
        "drift_speed": _pal["drift_speed"],
    }
    params["amount"] = {**params["amount"], "default": 0.8}

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("hue_drift")
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.phase = 0.0          # hue drift (turns)
        self.drift = 0.0          # palette drift
        self.kick = 0.0           # beat hue-rate kick (turns/s): fast attack, slow glide
        self.pal_name = None
        self.coeffs = palette_coeffs("neon")

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.1)
        kp = min(max(float(p["pulse"]), 0.0), 1.0)

        # host-integrated phases (always advance so re-entry is seamless)
        target = float(p["punch"]) * kp
        tau = 0.04 if target > self.kick else 0.55
        if dt > 0.0:
            self.kick += (target - self.kick) * (1.0 - math.exp(-dt / tau))
        self.phase = math.fmod(self.phase + (float(p["rate"]) + self.kick) * dt, 1.0)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dt, 64.0)

        amount = float(p["amount"])
        if amount <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        m = _smoothstep(amount)

        if p["palette"] != self.pal_name:
            self.pal_name = p["palette"]
            self.coeffs = palette_coeffs(self.pal_name)
        h = self.host
        h[0] = math.fmod(self.phase + float(p["offset"]) + 1.0, 1.0)
        h[1] = float(p["luma_spread"])
        h[2] = min(max(float(p["channel_cycle"]), 0.0), 1.0)
        h[3] = float(p["saturation"]) * (1.0 + float(p["sat_punch"]) * kp)
        h[4] = float(p["vibrance"])
        h[5] = min(max(float(p["tint_strength"]), 0.0), 1.0)
        h[6] = float(p["palette_period"])
        h[7] = self.drift
        h[8] = m
        h[9:21] = self.coeffs
        self.P.set(h)
        W, H = self.ctx.width, self.ctx.height
        launch_2d(self._k, W, H, (src, out, np.int32(W), np.int32(H), self.P))
