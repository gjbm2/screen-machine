"""Feedback echo: MilkDrop-style video feedback over any input -- light trails, tunnelling echoes, RGB afterimages.

Source: https://github.com/jberg/butterchurn/blob/master/src/rendering/shaders/warp.js
        https://github.com/jberg/butterchurn/blob/master/src/rendering/shaders/comp.js
        https://github.com/mrdoob/three.js/blob/dev/examples/jsm/shaders/AfterimageShader.js
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/RGB%20Trails%203.0.fs  (idea only: per-channel decay)
Author: Jordan Berg (butterchurn, MilkDrop 2 engine port); three.js authors (AfterimageShader); zoidberg (ISF RGB Trails 3.0)
License: MIT  (butterchurn LICENSE: https://github.com/jberg/butterchurn/blob/master/LICENSE "The MIT License";
               three.js: MIT.  Engine shaders only -- no MilkDrop community presets were used.)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the MilkDrop per-frame warp -- zoom about the centre, rotate, then the
four-term sine warp field with its slowly breathing frequencies -- is applied to the previous output as a gather;
decay is the AfterimageShader damp, made frame-rate independent (pow(d, dt*60)) and clamped below 0.98 per 1/60 s;
the comp is one continuous blend axis max (light-painting) -> lerp (ghosts) -> screen-add (glow); RGB Trails' idea
of per-channel persistence becomes a per-channel zoom/decay split; new: hue rotation of the feedback and a palette
tint on what enters the trail buffer, so echoes carry the palette colour of the moment they were laid down).

Two uchar4 buffers ping-pong (a gather can't read and write one buffer).  The trail buffer holds the *tinted*
composite; the output is the same composite with the untinted source on top, so the live picture stays true and
only its echoes take palette colour.  8-bit decay uses a small per-channel bias so dim trails really reach black.

Realtime: amount = feedback mix + persistence (0 -> exactly src, buffer reset to src); pulse kicks the zoom and warp
rates and briefly boosts persistence, so on the beat the echoes fly outward faster and nothing jumps.
zoom_speed / rotate_speed / drift_speed are bindable rates.
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

TAU = 2.0 * math.pi
NP = 52
# P layout (floats)
#  0,1 centre px | 2,3 cos/sin of per-frame rotation | 4,5,6 inverse zoom R,G,B | 7 warp displacement (px)
#  8 2/H (pixel -> normalised) | 9..12 warp freqs f0..f3 | 13..16 warp phases | 17..19 gain R,G,B
#  20..22 8-bit decay bias R,G,B | 23..31 hue rotation 3x3 | 32 blend | 33 tint | 34 palette period | 35 drift
#  36..47 palette coeffs | 48 per-channel sampling flag
_SRC = HELPERS + PALETTE_CUDA + r"""
// one channel of the comp (0..255): max (light painting) -> lerp (ghosts, k = 0.9 * gain) -> screen-add (glow)
__device__ __forceinline__ float comp(float S, float F, float k, float tML, float tLA) {
    const float M = fmaxf(S, F);
    const float L = S + 0.9f * F - k * S;
    const float A = 255.f - (255.f - S) * (255.f - 0.85f * F) * (1.f / 255.f);
    const float ML = M + (L - M) * tML;
    return ML + (A - ML) * tLA;
}

extern "C" __global__
void feedback_echo(const uchar4* __restrict__ src, const uchar4* __restrict__ prev, uchar4* __restrict__ out,
                   uchar4* __restrict__ next, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float cx = P[0], cy = P[1];
    const float dx = x + 0.5f - cx, dy = y + 0.5f - cy;
    // MilkDrop warp: rotate about the centre, zoom, then the sine warp field (all evaluated at the destination).
    const float rx = P[2] * dx + P[3] * dy, ry = -P[3] * dx + P[2] * dy;
    const float ox = dx * P[8], oy = dy * P[8];
    const float wu = P[7] * (__sinf(P[13] + ox * P[9] - oy * P[12]) + __cosf(P[15] - (ox * P[10] - oy * P[11])));
    const float wv = P[7] * (__cosf(P[14] - (ox * P[11] + oy * P[10])) + __sinf(P[16] + (ox * P[9] + oy * P[12])));
    const float gx = cx + rx * P[5] + wu, gy = cy + ry * P[5] + wv;
    float4 w;
    if (P[48] > 0.5f) {
        w.x = bilin(prev, cx + rx * P[4] + wu, cy + ry * P[4] + wv, W, H).x;
        w.y = bilin(prev, gx, gy, W, H).y;
        w.z = bilin(prev, cx + rx * P[6] + wu, cy + ry * P[6] + wv, W, H).z;
    } else {
        w = bilin(prev, gx, gy, W, H);
    }
    // what falls outside the frame (zooming out / rotated corners) brings no history
    const float edge = clamp01(fminf(fminf(gx, (float)W - gx), fminf(gy, (float)H - gy)) * 0.5f + 0.5f);
    // hue rotation of the feedback, then per-channel decay with an 8-bit bias so dim trails reach black
    float3 f;
    f.x = P[23] * w.x + P[24] * w.y + P[25] * w.z;
    f.y = P[26] * w.x + P[27] * w.y + P[28] * w.z;
    f.z = P[29] * w.x + P[30] * w.y + P[31] * w.z;
    f.x = fmaxf(f.x * P[17] * edge - P[20], 0.f);
    f.y = fmaxf(f.y * P[18] * edge - P[21], 0.f);
    f.z = fmaxf(f.z * P[19] * edge - P[22], 0.f);

    const float4 s = ld(src, x, y, W, H);
    // palette-tinted copy of the source (same max-channel value) for what enters the trail buffer
    const float V = fmaxf(fmaxf(s.x, s.y), s.z);
    const float3 pc = pal(luma(s) * P[34] + P[35], P + 36);
    const float pm = V / fmaxf(fmaxf(fmaxf(pc.x, pc.y), pc.z), 0.05f);
    const float tn = P[33];
    const float3 st = make_float3(s.x + (pc.x * pm - s.x) * tn, s.y + (pc.y * pm - s.y) * tn,
                                  s.z + (pc.z * pm - s.z) * tn);

    // comp: blend 0 = max (light painting), 0.5 = lerp (ghosts), 1 = screen-add (glow); continuous
    const float b = P[32];
    const float tML = clamp01(2.f * b), tLA = clamp01(2.f * b - 1.f);
    const float kx = 0.9f * P[17], ky = 0.9f * P[18], kz = 0.9f * P[19];
    const float4 o = make_float4(comp(s.x, f.x, kx, tML, tLA), comp(s.y, f.y, ky, tML, tLA),
                                 comp(s.z, f.z, kz, tML, tLA), 255.f);
    const float4 n = make_float4(comp(st.x, f.x, kx, tML, tLA), comp(st.y, f.y, ky, tML, tLA),
                                 comp(st.z, f.z, kz, tML, tLA), 255.f);
    const int i = y * W + x;
    out[i] = pack(o);
    next[i] = pack(n);
}
"""

# MilkDrop's warp-field constants (butterchurn warp.js): f_i = base + amp * cos(t * rate + offset)
_F_BASE = (11.68, 8.77, 10.54, 11.49)
_F_AMP = (4.0, 3.0, 3.0, 4.0)
_F_RATE = (1.413, 1.113, 1.233, 0.933)
_F_OFF = (10.0, 7.0, 3.0, 5.0)
_W_RATE = (0.333, 0.375, 0.753, 0.825)
_WARP_SCALE_INV = 0.62      # broader swirls than MilkDrop's 1.0: bold forms at 4K
_WARP_TIME = 0.9            # warp animation speed (MilkDrop warpAnimSpeed)


def _hue_matrix(angle: float) -> list[float]:
    c, s = math.cos(angle), math.sin(angle)
    t, r = (1.0 - c) / 3.0, math.sqrt(1.0 / 3.0) * s
    return [c + t, t - r, t + r,
            t + r, c + t, t - r,
            t - r, t + r, c + t]


class FeedbackEcho(Effect):
    name = "feedback_echo"
    stage = "feedback"
    label = "Feedback echo"
    description = ("MilkDrop-style feedback: the last frame is zoomed, rotated and sine-warped under the new one, "
                   "leaving light trails, tunnelling echoes and RGB-fringed afterimages.")
    _pal = palette_params("neon", 0.8, 0.06)
    params = {
        **standard_effect_params(),
        "trail_length": num(0.0, 1.0, 0.62, "Trail length", tier="regional", basic=True),
        "zoom_speed": num(-1.5, 1.5, 0.45, "Zoom", unit="/s", tier="realtime", bind="rate", basic=True),
        "rotate_speed": paced(num(-90.0, 90.0, 7.0, "Rotate", unit="°/s", step=0.5, tier="realtime", bind="rate"), 360),
        "warp_amount": num(0.0, 0.05, 0.006, "Warp", step=0.001, tier="regional"),
        "blend": num(0.0, 1.0, 0.2, "Blend (max / lerp / add)", tier="regional"),
        "rgb_spread": num(0.0, 1.0, 0.4, "RGB spread", tier="regional"),
        "hue_drift": paced(num(-0.5, 0.5, 0.05, "Echo hue drift", unit="turns/s", step=0.005, tier="regional")),
        "echo_tint": num(0.0, 1.0, 0.45, "Echo palette tint", tier="regional"),
        "zoom_kick": num(0.0, 2.0, 1.0, "Beat zoom/warp kick", tier="regional"),
        "palette": enum(list(PALETTES), "neon", "Palette", tier="regional"),
        "palette_period": _pal["palette_period"],
        "drift_speed": _pal["drift_speed"],
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("feedback_echo")
        W, H = ctx.width, ctx.height
        self.fb = [cp.zeros((H, W, 4), cp.uint8), cp.zeros((H, W, 4), cp.uint8)]
        self.cur = 0
        self.need_reset = True
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.fph = [0.0, 0.0, 0.0, 0.0]          # phases of the breathing warp frequencies
        self.wph = [0.0, 1.1, 2.3, 4.0]          # phases of the four warp terms
        self.drift = 0.0
        self.pal_name = None

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        a = min(max(float(p["amount"]), 0.0), 1.0)
        kp = min(max(float(p["pulse"]), 0.0), 1.0)
        dt = min(max(float(dt), 0.0), 4.0 / 60.0)
        kick = float(p["zoom_kick"]) * kp

        # host-integrated phases (always advance, so re-entry is seamless)
        wrate = _WARP_TIME * (1.0 + 1.5 * kick)
        for i in range(4):
            self.fph[i] = math.fmod(self.fph[i] + _F_RATE[i] * _WARP_TIME * dt, TAU)
            self.wph[i] = math.fmod(self.wph[i] + _W_RATE[i] * wrate * dt, TAU)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dt, 100.0)

        if a <= 1e-4:
            cp.copyto(out, src)
            self.need_reset = True
            return
        prev, nxt = self.fb[self.cur], self.fb[1 - self.cur]
        if self.need_reset:
            cp.copyto(prev, src)
            self.need_reset = False

        W, H = self.ctx.width, self.ctx.height
        dt60 = dt * 60.0
        sp = float(p["rgb_spread"])
        # persistence: half-life 3..34 frames (decay 0.79..0.98 per 1/60 s); amount scales it, the beat boosts it
        half = 3.0 * (34.0 / 3.0) ** float(p["trail_length"])
        half *= (0.35 + 0.65 * a) * (1.0 + 0.5 * kp)
        mix_gain = a * (2.0 - a)
        gains, bias = [], []
        for hc in (half * (1.0 + 0.45 * sp), half, half * (1.0 - 0.3 * sp)):
            d = min(0.5 ** (1.0 / max(hc, 0.5)), 0.98)
            k = (d ** max(dt60, 1.0) if dt60 > 0 else d) * mix_gain
            gains.append(k)
            bias.append(max(0.0, k - 0.45))
        # zoom (per-frame scale), RGB split in zoom, rotation, warp
        zs = float(p["zoom_speed"])
        z = zs + (1.0 if zs >= 0 else -1.0) * 0.9 * kick
        lz = z * dt
        split = sp * (0.6 * abs(lz) + 0.003 * dt60)
        inv = [math.exp(-(lz + split)), math.exp(-lz), math.exp(-(lz - split))]
        ang = math.radians(float(p["rotate_speed"])) * dt
        warp_px = float(p["warp_amount"]) * dt60 * H * 0.5 * (1.0 + 0.6 * kick)

        if p["palette"] != self.pal_name:
            self.pal_name = p["palette"]
            self.coeffs = palette_coeffs(self.pal_name)
        h = self.host
        h[0], h[1] = 0.5 * W, 0.5 * H
        h[2], h[3] = math.cos(ang), math.sin(ang)
        h[4:7] = inv
        h[7] = warp_px
        h[8] = 2.0 / H
        for i in range(4):
            h[9 + i] = _WARP_SCALE_INV * (_F_BASE[i] + _F_AMP[i] * math.cos(self.fph[i] + _F_OFF[i]))
            h[13 + i] = self.wph[i]
        h[17:20] = gains
        h[20:23] = bias
        h[23:32] = _hue_matrix(TAU * float(p["hue_drift"]) * dt)
        h[32] = float(p["blend"])
        h[33] = float(p["echo_tint"])
        h[34] = float(p["palette_period"])
        h[35] = self.drift
        h[36:48] = self.coeffs
        h[48] = 1.0 if sp > 1e-3 else 0.0
        self.P.set(h)
        launch_2d(self._k, W, H, (src, prev, out, nxt, np.int32(W), np.int32(H), self.P))
        self.cur = 1 - self.cur

    def close(self) -> None:
        self.fb = []
