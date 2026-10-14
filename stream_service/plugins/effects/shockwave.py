"""Shockwave: on every kick a refracting ring ripples out across the picture, with a chromatic edge and a glow.

Source: https://github.com/pmndrs/postprocessing/blob/main/src/effects/ShockWaveEffect.js
        https://github.com/pmndrs/postprocessing/blob/main/src/effects/glsl/shock-wave.frag
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/Shockwave%20Pulse.fs
Author: Raoul van Rueschen (pmndrs ShockWaveEffect); VIDVOX (ISF Shockwave Pulse)
License: Zlib (pmndrs/postprocessing, per its repository LICENSE) / MIT (Vidvox ISF-Files)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: a pool of 6 rings spawned by a host beat edge detector instead of one
explicit "explode()" ring; aspect-correct radii in half-diagonals; each ring keeps its own spawn centre, widens as it
travels, fades in from zero amplitude and out to zero at max_radius so nothing pops; the profile is an odd
(lens-like) refraction plus a forward push instead of pmndrs' pure bump; optional RGB split and a palette-tinted
glow on the wavefront; idle rings keep it alive when no beat arrives).

amount 0 is an exact copy of the input.  amount scales every ring's refraction and glow.  pulse is the beat: the
host detects its rising edge (above 0.6 after dropping below 0.3, >= 150 ms apart) and spawns a ring whose strength
is the pulse peak; ring radii integrate on the host at ring_speed.  With no beat for 2 s, idle rings are emitted at
idle_rate so the effect still breathes in quiet passages.  The glow colour comes from pal() indexed by ring age and
position across the band plus an integrated drift.  One 4K pass, <= 6 rings, 1 bilinear tap (3 with chroma);
pixels outside every band are a plain copy, and the whole frame is a plain copy when no ring is live.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

POOL = 6
RF = 8  # floats per ring: cx, cy, R, w (px), amp (px), hue, glow, unused

_SRC = HELPERS + PALETTE_CUDA + r"""
#define RF 8
extern "C" __global__
void shockwave(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
               const float* __restrict__ rings, const int n, const float* __restrict__ P,
               const float chroma, const float band_hue, const float max_disp)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = x + 0.5f, py = y + 0.5f;
    float dx = 0.f, dy = 0.f, sx = 0.f, sy = 0.f, gmax = 0.f, gsum = 0.f, shade = 0.f;
    float3 gc = make_float3(0.f, 0.f, 0.f);
    bool hit = false;
    for (int i = 0; i < n; ++i) {
        const float* r = rings + i * RF;
        const float ex = px - r[0], ey = py - r[1];
        const float d = sqrtf(ex * ex + ey * ey);
        const float w = r[3];
        const float xx = (d - r[2]) / w;                 // -1 (trailing) .. +1 (leading edge)
        if (xx <= -1.f || xx >= 1.f) continue;
        hit = true;
        const float inv = 1.f / fmaxf(d, 1e-3f);
        const float ux = ex * inv, uy = ey * inv;
        const float s = __sinf(3.14159265f * xx), c = __cosf(3.14159265f * xx);
        const float h = 0.5f + 0.5f * c;                 // raised-cosine bump, C1 at the band edges
        const float a = r[4] * smooth(0.f, w, d);        // no singularity at the spawn point
        const float m = a * (s * h + 0.35f * h * h);     // odd lens term + forward push (pmndrs)
        dx += ux * m; dy += uy * m;
        const float sp = chroma * a * (0.55f * s * h + 0.45f * h);
        sx += ux * sp; sy += uy * sp;
        // glow rides the leading half of the wave, with a darker trough behind it for depth
        const float lead = clamp01(1.f - fabsf(xx - 0.3f) * 1.45f);
        const float gl = r[6] * lead * lead * (3.f - 2.f * lead);
        if (gl > 1e-4f) {
            const float3 col = pal(r[5] + band_hue * xx, P);
            const float hot = h * h * h * h * 0.35f;     // white-hot crest
            gc.x += gl * (col.x + (1.f - col.x) * hot);
            gc.y += gl * (col.y + (1.f - col.y) * hot);
            gc.z += gl * (col.z + (1.f - col.z) * hot);
            gsum += gl; gmax = fmaxf(gmax, gl);
        }
        const float trough = clamp01(-xx * 1.6f) * h;
        shade = fmaxf(shade, r[6] * 0.55f * trough * trough);
    }
    if (!hit) { out[y * W + x] = src[y * W + x]; return; }
    // keep the summed displacement bounded (overlapping rings)
    const float dm = sqrtf(dx * dx + dy * dy);
    if (dm > max_disp) { const float k = max_disp / dm; dx *= k; dy *= k; sx *= k; sy *= k; }
    const float qx = px - dx, qy = py - dy;
    float4 o;
    if (chroma > 1e-4f) {
        o.x = bilin(src, qx - sx, qy - sy, W, H).x;
        o.y = bilin(src, qx, qy, W, H).y;
        o.z = bilin(src, qx + sx, qy + sy, W, H).z;
    } else {
        o = bilin(src, qx, qy, W, H);
    }
    o.w = 255.f;
    const float dk = 1.f - fminf(shade, 0.6f);
    o.x *= dk; o.y *= dk; o.z *= dk;
    if (gsum > 1e-4f) {
        const float g = fminf(gmax, 1.f) / gsum;          // tint = weighted mean, strength = max (no stacking)
        const float tr = clamp01(gc.x * g), tg = clamp01(gc.y * g), tb = clamp01(gc.z * g);
        o.x += (255.f - o.x) * tr; o.y += (255.f - o.y) * tg; o.z += (255.f - o.z) * tb;   // screen blend
    }
    out[y * W + x] = pack(o);
}
"""


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class Shockwave(Effect):
    name = "shockwave"
    stage = "stylise"
    label = "Shockwave"
    description = "On every kick a refracting ring ripples out across the picture, with a chromatic edge and glow."
    params = {
        **standard_effect_params(),
        # the beat envelope is edge-detected on the host: keep it raw (easing would flatten the peaks)
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "wave_width": num(0.02, 0.3, 0.12, "Wave width", step=0.005, tier="regional", basic=True),
        "ring_speed": num(0.3, 3.0, 1.0, "Ring speed", step=0.05, unit="screens/s", tier="regional"),
        "max_radius": num(0.3, 1.5, 1.0, "Max radius", step=0.05, tier="regional"),
        "center_x": num(0.0, 1.0, 0.5, "Centre X", tier="regional"),
        "center_y": num(0.0, 1.0, 0.5, "Centre Y", tier="regional"),
        "chroma": num(0.0, 1.0, 0.35, "Chromatic edge", tier="regional"),
        "glow": num(0.0, 1.0, 0.45, "Wavefront glow", tier="regional"),
        "idle_rate": num(0.0, 2.0, 0.4, "Idle rings (no beat)", unit="/s", tier="regional"),
        **palette_params("neon", 1.0, 0.05),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("shockwave")
        self._rings_d = cp.zeros(POOL * RF, dtype=cp.float32)
        self._rings_h = np.zeros(POOL * RF, dtype=np.float32)
        self._P_d = cp.zeros(12, dtype=cp.float32)
        p0 = self.easer.values()
        self._pal = np.array(palette_coeffs(p0.get("palette", "neon")), dtype=np.float64)
        self._P_d.set(self._pal.astype(np.float32))
        rng = np.random.default_rng(int(p0.get("seed", 7) or 7))
        self._hue_seq = rng.random(64)
        self._hue_i = 0
        # ring pool: live, radius (half-diagonals), strength, centre (0..1), hue seed
        self._live = np.zeros(POOL, dtype=bool)
        self._R = np.zeros(POOL)
        self._S = np.zeros(POOL)
        self._C = np.zeros((POOL, 2))
        self._H = np.zeros(POOL)
        self._t = 0.0
        self._armed = True
        self._last_spawn = -10.0
        self._last_beat = -10.0
        self._track = -1          # ring whose strength follows the rising pulse
        self._prev_pulse = 0.0
        self._idle = 0.0
        self._drift = 0.0
        # prime one ring in flight so the effect reads the moment it is faded in
        self._spawn(0.8, p0)
        self._R[0] = 0.3 * float(p0.get("max_radius", 1.0))

    def _spawn(self, strength: float, p: dict) -> int:
        free = np.flatnonzero(~self._live)
        if free.size == 0:
            return -1             # pool full: skip rather than pop a live ring
        i = int(free[0])
        self._live[i] = True
        self._R[i] = 0.0
        self._S[i] = strength
        self._C[i] = (float(p["center_x"]), float(p["center_y"]))
        self._H[i] = self._hue_seq[self._hue_i % len(self._hue_seq)]
        self._hue_i += 1
        self._last_spawn = self._t
        return i

    def _rings(self, p: dict, dt: float) -> None:
        dt = min(max(dt, 0.0), 0.25)
        self._t += dt
        maxr = max(float(p["max_radius"]), 1e-3)
        # advance and retire
        self._R[self._live] += float(p["ring_speed"]) * dt
        self._live &= self._R < maxr
        # beat: rising edge with hysteresis, >= 150 ms apart; strength = pulse peak
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        if self._track >= 0:
            if self._live[self._track] and pulse > self._S[self._track]:
                self._S[self._track] = pulse
            elif pulse < self._prev_pulse:
                self._track = -1
        if self._armed and pulse > 0.6 and self._t - self._last_spawn >= 0.15:
            self._track = self._spawn(pulse, p)
            self._armed = False
            self._last_beat = self._t
            self._idle = 0.0
        elif pulse < 0.3:
            self._armed = True
        self._prev_pulse = pulse
        # idle rings when no beat has arrived for a while
        rate = float(p["idle_rate"])
        if rate > 1e-3 and self._t - self._last_beat > 2.0:
            self._idle += rate * dt
            if self._idle >= 1.0:
                self._idle -= 1.0
                self._spawn(0.7, p)
        else:
            self._idle = 0.0

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        W, H = self.ctx.width, self.ctx.height
        self._rings(p, dt)
        self._drift += float(p["drift_speed"]) * max(dt, 0.0)
        self._drift -= math.floor(self._drift)
        # palette coefficients ease (the enum switches at once)
        tgt = np.array(palette_coeffs(p["palette"]), dtype=np.float64)
        if dt > 0 and not np.array_equal(tgt, self._pal):
            a = 1.0 - math.exp(-dt / 1.0)
            self._pal += (tgt - self._pal) * a
            if np.max(np.abs(tgt - self._pal)) < 1e-4:
                self._pal = tgt
            self._P_d.set(self._pal.astype(np.float32))
        amount = min(max(float(p["amount"]), 0.0), 1.0)
        if amount <= 1e-4 or not self._live.any():
            cp.copyto(out, src)
            return
        D = 0.5 * math.hypot(W, H)                  # one "screen" = half the diagonal
        maxr = max(float(p["max_radius"]), 1e-3)
        ww = float(p["wave_width"])
        glow = float(p["glow"])
        period = float(p["palette_period"])
        h = self._rings_h
        n = 0
        for i in np.flatnonzero(self._live):
            u = self._R[i] / maxr
            env = _smooth(0.0, 0.12, u) * (1.0 - _smooth(0.3, 1.0, u))
            if env <= 1e-4:
                continue
            w_px = ww * D * (0.6 + 0.8 * u)          # the front broadens as it travels
            s = self._S[i] * env * amount
            o = n * RF
            h[o + 0] = self._C[i, 0] * W
            h[o + 1] = self._C[i, 1] * H
            h[o + 2] = self._R[i] * D
            h[o + 3] = max(w_px, 1.0)
            h[o + 4] = 0.42 * w_px * s
            h[o + 5] = (self._H[i] + 0.5 * period * u + self._drift) % 1.0
            h[o + 6] = glow * min(s * 1.25, 1.0)
            h[o + 7] = 0.0
            n += 1
        if n == 0:
            cp.copyto(out, src)
            return
        self._rings_d.set(h)
        launch_2d(self._k, W, H, (src, out, np.int32(W), np.int32(H), self._rings_d, np.int32(n), self._P_d,
                                  np.float32(p["chroma"]), np.float32(0.18 * period), np.float32(0.12 * H)))

    def close(self) -> None:
        self._rings_d = None
        self._P_d = None
