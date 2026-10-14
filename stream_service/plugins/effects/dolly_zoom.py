"""Dolly Zoom: the Vertigo effect on a flat frame.  A pseudo-depth map (radial about a central subject, optionally
mixed with blurred luma: bright = near, dark = far) holds the subject at the same size while the background
stretches away or looms in with a true perspective (FOV) remap, so space seems to warp around the centre.  A slow
push-pull oscillation drives it; the beat lurches the dolly rate and snaps the camera like a rubber band.

Source: Original (inspired by https://en.wikipedia.org/wiki/Dolly_zoom)
Author: smfx (original)
License: MIT (original)
license_class: permissive

How it works: a pixel at depth z (subject at z = 1, far background at 1 + DEPTH) seen by a camera that dollies by
dz while its focal length compensates to keep the subject framed lands at x' = x * z (1 - dz) / (z - dz).  The
kernel inverts that per output pixel with a damped 3-step fixed point on depth (depth lives in source space), adds
an optional depth-weighted "vertigo twist" (the background rotates about the subject in proportion to dz), a zoom
motion blur of up to 6 taps spanning the last ~2 frames of dolly travel, a palette-tinted depth fog that thickens
as the background stretches, and mirror-folded edges that blend into an automatic overscan (edge_fill).  Luma depth
is a 1/8-res luma map, Gaussian-blurred and temporally smoothed (two tiny passes, skipped when depth_source = 0).

Realtime: m = smoothstep(amount) scales every term; amount 0 is an exact copy.  The oscillation phase is integrated
on the host (rate x (1 + 3 m pulse)), and an under-damped spring driven by the beat envelope adds a loom-side snap
that overshoots and settles, so nothing ever jumps.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

DEPTH = 3.0      # far background sits at z = 1 + DEPTH (subject at z = 1)
NF = 16          # floats in the per-frame parameter block
LOOM_GAIN = 1.8  # the loom side (dolly out + zoom in) needs more travel to read as strongly as the recede side

_SRC = HELPERS + PALETTE_CUDA + r"""
// ---- 1/8-res luma for the luma depth source ---------------------------------------------------------------
extern "C" __global__
void dz_lum_down(const uchar4* __restrict__ src, float* __restrict__ lum, const int W, const int H,
                 const int W8, const int H8)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W8 || y >= H8) return;
    float acc = 0.f;
    for (int j = 0; j < 4; ++j) {
        const int sy = min(y * 8 + 1 + 2 * j, H - 1);
        for (int i = 0; i < 4; ++i) {
            const int sx = min(x * 8 + 1 + 2 * i, W - 1);
            const uchar4 p = src[sy * W + sx];
            acc += 0.2126f * p.x + 0.7152f * p.y + 0.0722f * p.z;
        }
    }
    lum[y * W8 + x] = acc * (1.f / (16.f * 255.f));
}

// separable 13-tap Gaussian (sigma ~3 texels); the vertical pass folds in a temporal EMA (state = out)
extern "C" __global__
void dz_lum_blur(const float* __restrict__ in, float* __restrict__ out, const int W8, const int H8,
                 const int ax, const float k)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W8 || y >= H8) return;
    float acc = 0.f, wsum = 0.f;
    for (int t = -6; t <= 6; ++t) {
        const int sx = ax ? min(max(x + t, 0), W8 - 1) : x;
        const int sy = ax ? y : min(max(y + t, 0), H8 - 1);
        const float w = __expf(-(float)(t * t) * (1.f / 18.f));
        acc += w * in[sy * W8 + sx];
        wsum += w;
    }
    const float v = acc / wsum;
    const int idx = y * W8 + x;
    out[idx] = (k >= 1.f) ? v : out[idx] + (v - out[idx]) * k;
}

__device__ __forceinline__ float dz_fold(float v, float n) {        // mirror at the frame edges (any distance)
    v = fmodf(fabsf(v), 2.f * n);
    v = n - fabsf(n - v);
    return fminf(fmaxf(v, 0.f), n);
}

__device__ __forceinline__ float dz_lum(const float* __restrict__ lum, float sx, float sy, int W8, int H8) {
    float fx = sx * 0.125f - 0.5f, fy = sy * 0.125f - 0.5f;
    fx = fminf(fmaxf(fx, 0.f), (float)(W8 - 1));
    fy = fminf(fmaxf(fy, 0.f), (float)(H8 - 1));
    const int x0 = (int)fx, y0 = (int)fy;
    const int x1 = min(x0 + 1, W8 - 1), y1 = min(y0 + 1, H8 - 1);
    const float tx = fx - x0, ty = fy - y0;
    const float a = lum[y0 * W8 + x0], b = lum[y0 * W8 + x1], c = lum[y1 * W8 + x0], d = lum[y1 * W8 + x1];
    return (1.f - ty) * ((1.f - tx) * a + tx * b) + ty * ((1.f - tx) * c + tx * d);
}

// pseudo-depth 0 (subject plane) .. 1 (far background) at a source-space point
__device__ __forceinline__ float dz_depth(float sx, float sy, const float* __restrict__ F,
                                          const float* __restrict__ lum, int W8, int H8) {
    const float r = hypotf(sx - F[0], sy - F[1]) * F[2];
    const float R = F[3];
    const float t = smooth(0.8f * R, F[4], r);
    float d = __powf(fmaxf(t, 1e-6f), 0.75f);
    const float ds = F[6];
    if (ds > 1e-3f) {
        const float L = dz_lum(lum, sx, sy, W8, H8);
        const float dl = (1.f - smooth(0.06f, 0.78f, L)) * smooth(0.6f * R, 1.1f * R, r);
        d += (dl - d) * ds;
    }
    return d;
}

__device__ __forceinline__ float dz_scale(float d, float dz, float depth) {   // screen scale of depth d
    const float z = 1.f + depth * d;
    return z * (1.f - dz) / fmaxf(z - dz, 1e-3f);
}

extern "C" __global__
void dolly_zoom(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
                const float* __restrict__ lum, const int W8, const int H8,
                const float* __restrict__ F, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float cx = F[0], cy = F[1];
    const float depth = F[5], g = F[7], dz = F[8], ddz = F[9], twist = F[10], fog = F[11];
    const int n = min(max((int)(F[14] + 0.5f), 1), 6);
    const float qx = x + 0.5f - cx, qy = y + 0.5f - cy;

    // invert the perspective remap: depth is defined where the pixel came from (damped fixed point)
    float s = 1.f, d = 0.f;
    for (int it = 0; it < 3; ++it) {
        const float inv = 1.f / fmaxf(s * g, 1e-3f);
        d = dz_depth(cx + qx * inv, cy + qy * inv, F, lum, W8, H8);
        const float sn = dz_scale(d, dz, depth);
        s = (it == 0) ? sn : 0.5f * (s + sn);
    }

    const float fW = (float)W, fH = (float)H;
    float ar = 0.f, ag = 0.f, ab = 0.f;
    const float tstep = (n > 1) ? 1.f / (float)(n - 1) : 0.f;
    for (int k = 0; k < n; ++k) {
        const float dzk = dz - ddz * (k * tstep);
        const float sk = dz_scale(d, dzk, depth);
        const float th = twist * dzk * d;
        float sn_, cs_;
        __sincosf(th, &sn_, &cs_);
        const float inv = 1.f / fmaxf(sk * g, 1e-3f);
        const float rx = (cs_ * qx - sn_ * qy) * inv, ry = (sn_ * qx + cs_ * qy) * inv;
        const float4 c = bilin(src, dz_fold(cx + rx, fW), dz_fold(cy + ry, fH), W, H);
        ar += c.x; ag += c.y; ab += c.z;
    }
    const float invn = 1.f / (float)n;
    float4 c = make_float4(ar * invn, ag * invn, ab * invn, 255.f);

    if (fog > 1e-4f) {
        const float stretch = clamp01(fabsf(1.f - s) * 1.5f);
        const float fk = clamp01(fog * d * sqrtf(d) * (0.45f + 0.55f * stretch)) * 0.8f;
        const float ro = hypotf(qx, qy) * F[2];
        const float3 fc = pal(F[13] + F[12] * (0.4f * d + 0.12f * ro), P);
        const float lift = 0.55f * 255.f;
        c.x += (fc.x * lift - c.x) * fk;
        c.y += (fc.y * lift - c.y) * fk;
        c.z += (fc.z * lift - c.z) * fk;
    }
    out[y * W + x] = pack(c);
}
"""


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(max(float(v), lo), hi)


def _smooth01(t: float) -> float:
    t = _clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _scale(d: float, dz: float) -> float:
    z = 1.0 + DEPTH * d
    return z * (1.0 - dz) / max(z - dz, 1e-3)


class DollyZoom(Effect):
    name = "dolly_zoom"
    stage = "motion"
    label = "Dolly Zoom"
    description = ("The Vertigo effect on a flat frame: the central subject holds its size while the background "
                   "stretches away or looms in with a perspective (FOV) warp. The beat lurches the dolly.")
    tags = {"family": ["motion"], "dims": ["2d"], "technique": ["optical", "warp"]}
    params = {
        **standard_effect_params(),
        # the beat drives a spring and a rate kick on the host: keep the envelope raw
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "fov_range": num(0.05, 0.8, 0.45, "FOV swing", step=0.01, tier="regional", basic=True),
        "rate": num(0.01, 1.0, 0.12, "Dolly rate", unit="Hz", step=0.01, tier="realtime", bind="rate"),
        "bias": num(-1.0, 1.0, 0.0, "Loom ↔ recede", step=0.01, tier="regional"),
        "depth_source": num(0.0, 1.0, 0.25, "Depth: radial ↔ luma", tier="regional"),
        "subject_radius": num(0.05, 0.9, 0.35, "Subject radius", unit="× half-height", tier="regional"),
        "edge_fill": num(0.0, 1.0, 0.6, "Edges: mirror ↔ overscan", tier="regional"),
        "twist": num(0.0, 1.0, 0.2, "Vertigo twist", tier="regional"),
        "snap": num(0.0, 1.5, 0.6, "Beat snap", step=0.01, tier="regional"),
        "blur": num(0.0, 1.0, 0.5, "Zoom blur", tier="regional"),
        "fog": num(0.0, 1.0, 0.35, "Depth fog", tier="regional"),
        **palette_params("dusk", 1.0, 0.03),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._k = mod.get_function("dolly_zoom")
        self._k_down = mod.get_function("dz_lum_down")
        self._k_blur = mod.get_function("dz_lum_blur")
        W, H = ctx.width, ctx.height
        self._W8, self._H8 = (W + 7) // 8, (H + 7) // 8
        self._lum_raw = cp.zeros(self._W8 * self._H8, dtype=cp.float32)
        self._lum_tmp = cp.zeros(self._W8 * self._H8, dtype=cp.float32)
        self._lum = cp.zeros(self._W8 * self._H8, dtype=cp.float32)
        self._lum_ok = False
        self._F_d = cp.zeros(NF, dtype=cp.float32)
        self._F_h = np.zeros(NF, dtype=np.float32)
        self._P_d = cp.zeros(12, dtype=cp.float32)
        p0 = self.easer.values()
        self._pal = np.array(palette_coeffs(p0.get("palette", "dusk")), dtype=np.float64)
        self._P_d.set(self._pal.astype(np.float32))
        seed = int(params.get("seed", 3) or 3) if isinstance(params, dict) else 3
        rng = np.random.default_rng(seed)
        self._phase = float(rng.uniform(0.0, 2.0 * math.pi))
        self._drift = 0.0
        self._x = 0.0          # snap spring
        self._v = 0.0
        self._dz_prev = None

    def _spring(self, pulse: float, dt: float) -> None:
        if dt > 0.5:
            self._x, self._v = 0.0, 0.0
            return
        dt = min(max(dt, 0.0), 0.1)
        if dt <= 0.0:
            return
        w = 2.0 * math.pi * 2.6
        z = 0.42
        nsub = max(1, int(math.ceil(dt * 120.0)))
        h = dt / nsub
        for _ in range(nsub):
            self._v += (w * w * (pulse - self._x) - 2.0 * z * w * self._v) * h
            self._x += self._v * h
        self._x = _clamp(self._x, -0.6, 1.6)

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        W, H = self.ctx.width, self.ctx.height
        pulse = _clamp(p["pulse"], 0.0, 1.0)
        amount = _clamp(p["amount"], 0.0, 1.0)
        m = _smooth01(amount)
        dts = min(max(dt, 0.0), 0.1)
        self._spring(pulse, dt)
        rate = max(float(p["rate"]), 0.0) * (1.0 + 3.0 * m * pulse)
        self._phase = (self._phase + 2.0 * math.pi * rate * dts) % (2.0 * math.pi)
        self._drift = (self._drift + float(p["drift_speed"]) * dts) % 1.0
        tgt = np.array(palette_coeffs(p["palette"]), dtype=np.float64)
        if dt > 0 and not np.array_equal(tgt, self._pal):
            k = 1.0 - math.exp(-dt / 1.0)
            self._pal += (tgt - self._pal) * k
            if np.max(np.abs(tgt - self._pal)) < 1e-4:
                self._pal = tgt
            self._P_d.set(self._pal.astype(np.float32))

        if amount <= 1e-4:
            cp.copyto(out, src)
            self._lum_ok = False
            self._dz_prev = None
            return

        # dolly travel: push-pull oscillation + beat snap (loom side), all continuous
        bias = _clamp(p["bias"], -1.0, 1.0)
        wave = bias + (1.0 - abs(bias)) * math.sin(self._phase)
        A = _clamp(p["fov_range"], 0.0, 0.8)
        dz = m * A * wave - m * float(p["snap"]) * 0.3 * self._x
        dz = dz * LOOM_GAIN if dz < 0.0 else dz
        dz = _clamp(dz, -1.6, 0.85)
        if self._dz_prev is None or dt <= 0.0 or dt > 0.5:
            ddz = 0.0
        else:
            ddz = _clamp((dz - self._dz_prev) * 2.0 * _clamp(p["blur"], 0.0, 1.0), -0.25, 0.25)
        self._dz_prev = dz

        # overscan that holds the corners (far depth) inside the frame, blended against mirror folding
        s_min = min(_scale(1.0, dz), _scale(1.0, dz - ddz))
        g_full = max(1.0, 1.0 / max(s_min, 1e-3))
        g = 1.0 + (g_full - 1.0) * _clamp(p["edge_fill"], 0.0, 1.0)
        half_diag = 0.5 * math.hypot(W, H)
        disp = half_diag * abs(1.0 / max(_scale(1.0, dz), 1e-3) - 1.0 / max(_scale(1.0, dz - ddz), 1e-3)) / g
        taps = 1 if abs(ddz) < 1e-5 else int(_clamp(math.ceil(disp / 5.0) + 1, 1, 6))

        ds = _clamp(p["depth_source"], 0.0, 1.0)
        W8, H8 = self._W8, self._H8
        if ds > 1e-3:
            if not self._lum_ok or dt > 0.5:
                kk = 1.0
            elif dt <= 0.0:
                kk = 0.0
            else:
                kk = 1.0 - math.exp(-dt / 0.2)
            launch_2d(self._k_down, W8, H8, (src, self._lum_raw, np.int32(W), np.int32(H), np.int32(W8),
                                             np.int32(H8)))
            launch_2d(self._k_blur, W8, H8, (self._lum_raw, self._lum_tmp, np.int32(W8), np.int32(H8),
                                             np.int32(1), np.float32(1.0)))
            launch_2d(self._k_blur, W8, H8, (self._lum_tmp, self._lum, np.int32(W8), np.int32(H8),
                                             np.int32(0), np.float32(kk)))
            self._lum_ok = True
        else:
            self._lum_ok = False

        hh = 0.5 * H
        F = self._F_h
        F[0] = 0.5 * W
        F[1] = 0.5 * H
        F[2] = 1.0 / hh
        F[3] = _clamp(p["subject_radius"], 0.02, 1.0)
        F[4] = half_diag / hh
        F[5] = DEPTH
        F[6] = ds
        F[7] = g
        F[8] = dz
        F[9] = ddz
        F[10] = 1.1 * _clamp(p["twist"], 0.0, 1.0)
        F[11] = _clamp(p["fog"], 0.0, 1.0) * m * (1.0 + 0.3 * m * pulse)
        F[12] = float(p["palette_period"])
        F[13] = self._drift
        F[14] = float(taps)
        F[15] = 0.0
        self._F_d.set(F)
        launch_2d(self._k, W, H, (src, out, np.int32(W), np.int32(H), self._lum, np.int32(W8), np.int32(H8),
                                  self._F_d, self._P_d))
