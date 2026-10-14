"""Time slice: slit-scan time displacement; every pixel shows the picture from a different moment.

Source: https://github.com/Vidvox/ISF-Files/blob/master/ISF/Slit%20Scan.fs
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/Time%20Glitch%20RGB.fs
Author: VIDVOX (ISF Slit Scan, Time Glitch RGB); CUDA rewrite by smfx
License: MIT (ISF-Files repository licence, https://github.com/Vidvox/ISF-Files/blob/master/LICENSE)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the ISF originals use a persistent update-on-slit buffer and 8 fixed
frame buffers; this keeps a 32-slot ring of quarter-res history frames (one slot per 1/30 s, the newest slot is
rewritten every frame so delayed regions still move at full frame rate), and every output pixel reads the moment
chosen by a continuous delay map (rows -> columns -> elliptical radial -> fbm noise), with a two-slot lerp for
fractional delay, an extra per-channel RGB lag, a depth curve, a slowly flowing map and an optional palette tint
of the older moments.  Where the delay is ~0 the full-res input is used, so static regions stay sharp.)

amount 0 is the untouched input (copied, history reset).  amount scales the deepest delay (up to ~1 s); the beat
kicks the delay depth (the picture lurches back in time and rushes forward again) and widens the RGB time split.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

N_SLOTS = 32          # history ring length
RING_W = 960          # history width target (quarter of 4K)
SLOT_RATE = 30.0      # history slots per second

# P layout (floats): 0..11 palette | 12 delay_map | 13 map_scale | 14 cos a | 15 sin a | 16 depth_curve
# 17 max delay (frames) | 18 rgb spread (frames) | 19 flow phase | 20 tint | 21 palette period | 22 drift
# 23 m (frames since last slot advance) | 24 stride (frames per slot) | 25 avail age (frames) | 26 aspect
# 27 noise flow phase
NP = 32
_SRC = HELPERS + PALETTE_CUDA + r"""
// Box-downsample the input into one history slot.
extern "C" __global__
void ts_down(const uchar4* __restrict__ src, uchar4* __restrict__ ring, const int slot, const int W, const int H,
             const int rw, const int rh, const int f)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= rw || y >= rh) return;
    float r = 0.f, g = 0.f, b = 0.f;
    for (int j = 0; j < f; ++j)
        for (int i = 0; i < f; ++i) {
            const float4 c = ld(src, x * f + i, y * f + j, W, H);
            r += c.x; g += c.y; b += c.z;
        }
    const float s = 1.f / (f * f);
    ring[(size_t)slot * rw * rh + y * rw + x] = pack(make_float4(r * s, g * s, b * s, 255.f));
}

__device__ __forceinline__ float tri2(float v) {       // 0 at v=0, 1 at v=1, 0 at v=2 ... (seamless repeat)
    const float m = v - 2.f * floorf(v * 0.5f);
    return 1.f - fabsf(1.f - m);
}

// One channel of the ring at age F frames (continuous): slot ages are 0 (head), m+1, m+1+stride, ...
__device__ __forceinline__ void ring_slots(float F, int head, float m, float stride, int& s0, int& s1, float& t) {
    int k0; float tt;
    if (F < m + 1.f) { k0 = 0; tt = F / (m + 1.f); }
    else {
        const float u = (F - m - 1.f) / stride;
        const float fu = floorf(u);
        k0 = 1 + (int)fu; tt = u - fu;
    }
    k0 = min(k0, NSLOT - 2);
    s0 = (head - k0 + NSLOT) % NSLOT;
    s1 = (head - k0 - 1 + NSLOT) % NSLOT;
    t = tt;
}

__device__ __forceinline__ float4 ring_at(const uchar4* __restrict__ ring, float F, int head, float m, float stride,
                                          float rx, float ry, int rw, int rh) {
    int s0, s1; float t;
    ring_slots(F, head, m, stride, s0, s1, t);
    const size_t sz = (size_t)rw * rh;
    const float4 a = bilin(ring + s0 * sz, rx, ry, rw, rh);
    if (t < 1e-3f) return a;
    const float4 b = bilin(ring + s1 * sz, rx, ry, rw, rh);
    return mix4(a, b, t);
}

extern "C" __global__
void ts_comp(const uchar4* __restrict__ src, const uchar4* __restrict__ ring, uchar4* __restrict__ out,
             const float* __restrict__ P, const int W, const int H, const int rw, const int rh, const int head,
             const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float asp = P[26];
    // centred coords in picture-height units, rotated by map angle
    const float px = ((x + 0.5f) / W - 0.5f) * asp, py = (y + 0.5f) / H - 0.5f;
    const float ca = P[14], sa = P[15];
    const float qx = ca * px + sa * py, qy = -sa * px + ca * py;
    const float scale = P[13], ph = P[19];

    // continuous blend through the four maps: rows -> columns -> radial -> noise
    const float dm = fminf(fmaxf(P[12], 0.f), 1.f) * 3.f;
    const int i0 = min((int)floorf(dm), 2);
    float fr = dm - i0; fr = fr * fr * (3.f - 2.f * fr);
    float d = 0.f;
    #pragma unroll
    for (int k = 0; k < 2; ++k) {
        const int mi = i0 + k;
        const float w = k == 0 ? 1.f - fr : fr;
        if (w < 1e-4f) continue;
        float v;
        if (mi == 0) {
            v = tri2((qy / 1.f + 0.5f) * scale + ph);
        } else if (mi == 1) {
            v = tri2((qx / asp + 0.5f) * scale + ph);
        } else if (mi == 2) {
            const float rr = sqrtf(qx * qx + 2.0f * qy * qy) / (0.5f * sqrtf(asp * asp + 2.0f));
            v = tri2(rr * scale - ph);
        } else {
            const float nph = P[27];
            const float n = fbm(qx * scale * 2.2f + nph * 0.7f + 17.3f, qy * scale * 2.2f - nph * 0.45f + 5.1f, seed);
            v = clamp01((n - 0.5f) * 2.4f + 0.5f);
            v = v * v * (3.f - 2.f * v);
        }
        d += w * v;
    }
    d = powf(fmaxf(d, 1e-5f), P[16]);

    const float avail = P[25], m = P[23], stride = P[24];
    const float F = fminf(d * P[17], avail);
    const float sp = P[18] * (0.3f + 0.7f * d);
    const float Fr = F, Fg = fminf(F + 0.5f * sp, avail), Fb = fminf(F + sp, avail);

    const float rx = (x + 0.5f) * rw / W, ry = (y + 0.5f) * rh / H;
    const float4 s = ld(src, x, y, W, H);
    float4 o = s;
    const float4 cr = ring_at(ring, Fr, head, m, stride, rx, ry, rw, rh);
    const float4 cg = fabsf(Fg - Fr) < 0.02f ? cr : ring_at(ring, Fg, head, m, stride, rx, ry, rw, rh);
    const float4 cb = fabsf(Fb - Fg) < 0.02f ? cg : ring_at(ring, Fb, head, m, stride, rx, ry, rw, rh);
    // near-zero delay fades back to the sharp full-res input
    o.x = s.x + (cr.x - s.x) * smooth(0.f, 1.5f, Fr);
    o.y = s.y + (cg.y - s.y) * smooth(0.f, 1.5f, Fg);
    o.z = s.z + (cb.z - s.z) * smooth(0.f, 1.5f, Fb);

    // palette tint of the older moments (time-coded colour)
    const float tw = P[20] * smooth(0.5f, 6.f, F);
    if (tw > 1e-3f) {
        const float3 c = pal(d * P[21] + P[22], P);
        const float cl = fmaxf(0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z, 0.2f);
        const float l = luma(o) * 255.f / cl;
        o.x += (fminf(l * c.x, 255.f) - o.x) * tw;
        o.y += (fminf(l * c.y, 255.f) - o.y) * tw;
        o.z += (fminf(l * c.z, 255.f) - o.z) * tw;
    }
    out[y * W + x] = pack(o);
}
""".replace("NSLOT", str(N_SLOTS))


class TimeSlice(Effect):
    name = "time_slice"
    stage = "stylise"
    label = "Time slice"
    description = ("Slit-scan time displacement: each pixel shows the picture from a different moment (rows, "
                   "columns, rings or noise), so motion stretches like rubber; RGB can lag by different amounts.")
    params = {
        **standard_effect_params(),
        "delay_map": num(0.0, 1.0, 0.66, "Delay map (rows / columns / rings / noise)", tier="regional", basic=True),
        "map_scale": num(0.25, 4.0, 1.2, "Map scale (repeats)", tier="regional"),
        "map_angle_deg": num(-180.0, 180.0, 0.0, "Map angle", step=1.0, unit="°", tier="regional"),
        "channel_offset": num(0.0, 1.0, 0.3, "RGB time split", tier="regional"),
        "depth_curve": num(0.5, 2.0, 1.0, "Depth curve", tier="regional"),
        "flow": num(-0.5, 0.5, 0.05, "Map flow", unit="/s", tier="regional"),
        "depth_kick": num(0.0, 2.0, 0.7, "Beat depth kick", tier="regional"),
        "spread_kick": num(0.0, 3.0, 1.2, "Beat RGB-split kick", tier="regional"),
        "tint_mix": num(0.0, 1.0, 0.2, "Palette tint of the past", tier="regional"),
        **palette_params("dusk", 1.0, 0.03),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        W, H = ctx.width, ctx.height
        self._f = max(1, int(round(W / RING_W)))
        self._rw, self._rh = max(16, W // self._f), max(16, H // self._f)
        self._ring = cp.zeros((N_SLOTS, self._rh, self._rw, 4), dtype=cp.uint8)
        self._P = cp.zeros(NP, dtype=cp.float32)
        self._Ph = np.zeros(NP, dtype=np.float32)
        mod = raw_module(_SRC)
        self._kd = mod.get_function("ts_down")
        self._kc = mod.get_function("ts_comp")
        fps = float(getattr(ctx, "fps", 30) or 30)
        self._stride = max(1, int(round(fps / SLOT_RATE)))
        self._seed = 1
        self._phase = 0.0
        self._nphase = 0.0
        self._drift = 0.0
        self._reset()

    def _reset(self) -> None:
        self._head = 0
        self._m = 0
        self._filled = 0

    def _push(self, src, dt: float) -> None:
        if self._filled == 0:
            self._head, self._m, self._filled = 0, 0, 1
        elif dt > 0.0:
            self._m += 1
            if self._m >= self._stride:
                self._m = 0
                self._head = (self._head + 1) % N_SLOTS
                self._filled = min(self._filled + 1, N_SLOTS)
        launch_2d(self._kd, self._rw, self._rh,
                  (src, self._ring, np.int32(self._head), np.int32(self.ctx.width), np.int32(self.ctx.height),
                   np.int32(self._rw), np.int32(self._rh), np.int32(self._f)))

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        amt = float(p["amount"])
        if amt <= 1e-4:
            self.ctx.cp.copyto(out, src)
            self._reset()
            return
        self._push(src, dt)
        k = float(p["pulse"])
        self._phase = (self._phase + p["flow"] * dt) % 64.0        # map period is 2, so this wrap is seamless
        self._nphase = (self._nphase + p["flow"] * dt) % 1000.0
        self._drift = (self._drift + p["drift_speed"] * dt) % 1.0

        s, m = self._stride, self._m
        max_age = m + 1 + (N_SLOTS - 2) * s                      # oldest reachable age in frames
        avail = 0.0 if self._filled <= 1 else float(min(max_age, m + 1 + (self._filled - 2) * s))
        eff = min(max(amt * (1.0 + p["depth_kick"] * k), 0.0), 1.0)
        depth = eff * (1 + (N_SLOTS - 2) * s) * 0.98
        spread = p["channel_offset"] * 0.3 * (1 + (N_SLOTS - 2) * s) * amt * (1.0 + p["spread_kick"] * k)

        P = self._Ph
        P[0:12] = palette_coeffs(p["palette"]) if isinstance(p.get("palette"), str) else palette_coeffs("dusk")
        a = math.radians(p["map_angle_deg"])
        P[12] = p["delay_map"]
        P[13] = p["map_scale"]
        P[14], P[15] = math.cos(a), math.sin(a)
        P[16] = p["depth_curve"]
        P[17] = depth
        P[18] = spread
        P[19] = self._phase
        P[20] = p["tint_mix"] * min(amt * 1.5, 1.0)
        P[21] = p["palette_period"]
        P[22] = self._drift
        P[23] = m
        P[24] = s
        P[25] = avail
        P[26] = self.ctx.width / self.ctx.height
        P[27] = self._nphase
        self._P.set(P)
        launch_2d(self._kc, self.ctx.width, self.ctx.height,
                  (src, self._ring, out, self._P, np.int32(self.ctx.width), np.int32(self.ctx.height),
                   np.int32(self._rw), np.int32(self._rh), np.int32(self._head), np.uint32(self._seed & 0xFFFFFFFF)))

    def close(self) -> None:
        self._ring = None
