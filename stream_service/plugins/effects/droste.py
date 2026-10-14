"""Droste spiral: Escher's 'Print Gallery'.  The frame holds a scaled (optionally rotated) copy of itself, which holds
another, forever; the twist bends that nest of rectangles into a logarithmic spiral, and the whole picture zooms
continuously into itself.  Thin palette-coloured bezels (with a soft glow) frame each copy.

Source: Original (inspired by https://pub.math.leidenuniv.nl/~smitbde/escherdroste/ and
        https://pub.math.leidenuniv.nl/~smitbde/papers/2003-de_smit-lenstra-escher.pdf)
Author: smfx (original; maths from B. de Smit and H. W. Lenstra, Notices AMS 2003)
License: MIT (original)
license_class: permissive

Maths (written from scratch from the paper's idea, no shader code consulted).  Work in log coordinates
w = log(z - c0) about the nesting centre c0.  The Droste picture D is self-similar under w -> w + g with
g = log(1/ratio) - i*turn (the inner copy is the frame scaled by `ratio` and turned by `turn`), and trivially under
w -> w + 2 pi i, so it is periodic on the lattice {g, 2 pi i}.  Levels use the RECTANGLE norm of the (off-centre)
frame, so the copies are nested rectangles: a point is reduced to the fundamental annulus "inside the frame, outside
the inner copy" by k = floor(-log N / L) steps in log space plus <= 3 exact fix-ups (rotated copies).  The Escher map
samples D at c * w with c = 1 + t g / (2 pi i): for integer t (= twist x strands) c * 2 pi i = 2 pi i + t g lies on
the lattice, so the picture is single-valued and every turn round the centre moves t levels (t strands).
Non-integer t (the twist easing in) would leave a seam on the atan2 branch cut, so two branches (cut at angle pi and
at angle 0) are blended with weight sin^2(angle / 2): continuous everywhere, and exactly one image again at every
integer t.  The zoom shifts the source by -q * g, q the integrated phase in levels, wrapped mod 1 (a lattice period,
so seamless); bezel colours are keyed to (level + completed cycles) so they ride with their copy through the wrap.
Deep copies are sampled from a FrameMips chain with the analytic (conformal) footprint |c| |P| / |z| so they stay
crisp without shimmer.

Realtime: m = smoothstep(amount).  amount grows the inner copy from scale 0 (identity) to `ratio` over 0..0.4, the
zoom engages over 0.15..0.5 and the twist ramps in over 0.45..0.85, so the nested frame appears, starts to fall
inwards, then winds into the spiral.  As the zoom gate closes the displayed phase is squeezed onto the nearest
whole level (both identity-equivalent), and a final mix to the input below amount 0.08 makes amount 0 exact.
pulse: kicks the zoom rate (1 + punch * pulse) and flashes the bezels/glow; it never moves geometry.
One full-res pass: log/atan2 + one (two while the twist eases) reduce-and-sample taps, ~1 ms + 0.25 ms mip build.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.frame3d import FRAME3D, FRAME3D_OPTIONS, FrameMips
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params
from stream_service.plugins._lib.sdf3d import SDF3D

TWO_PI = 2.0 * math.pi
NK = 32
# K layout (floats)
K_C0X, K_C0Y = 0, 1                      # nesting centre (normalised: half-height = 1, y down)
K_EXP, K_EXN, K_EYP, K_EYN = 2, 3, 4, 5  # frame extents from the centre (+x, -x, +y, -y)
K_IEXP, K_IEXN, K_IEYP, K_IEYN = 6, 7, 8, 9
K_CRE, K_CIM = 10, 11                    # Escher multiplier c
K_SHR, K_SHI = 12, 13                    # zoom shift (-q * g)
K_GRE, K_GIM = 14, 15                    # g = L - i turn
K_G2R, K_G2I = 16, 17                    # e^g (one step inwards)
K_GIR, K_GII = 18, 19                    # e^-g (one step outwards)
K_INVL = 20
K_T = 21                                 # t = twist * strands (level offset across the branch cut)
K_TWO = 22                               # 1 = blend two branches (non-integer t)
K_BW = 23                                # bezel width (copy units, full copy height = 2)
K_BEZ = 24                               # bezel opacity
K_BRI = 25                               # bezel brightness (beat flash)
K_GLOW, K_GW = 26, 27                    # glow gain, glow width (copy units)
K_CBASE, K_CSTEP = 28, 29                # palette index base / per-level step
K_CABS = 30                              # |c|
K_MIX = 31                               # mix weight towards the effect (identity guard)

_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
#define D_TWO_PI 6.28318531f
#define K_C0X 0
#define K_C0Y 1
#define K_EXP 2
#define K_EXN 3
#define K_EYP 4
#define K_EYN 5
#define K_IEXP 6
#define K_IEXN 7
#define K_IEYP 8
#define K_IEYN 9
#define K_CRE 10
#define K_CIM 11
#define K_SHR 12
#define K_SHI 13
#define K_GRE 14
#define K_GIM 15
#define K_G2R 16
#define K_G2I 17
#define K_GIR 18
#define K_GII 19
#define K_INVL 20
#define K_T 21
#define K_TWO 22
#define K_BW 23
#define K_BEZ 24
#define K_BRI 25
#define K_GLOW 26
#define K_GW 27
#define K_CBASE 28
#define K_CSTEP 29
#define K_CABS 30
#define K_MIX 31

__device__ __forceinline__ float dr_wrap(float a) { return a - D_TWO_PI * rintf(a * 0.15915494f); }
__device__ __forceinline__ float2 dr_cmul(float2 a, float ar, float ai) {
    return make_float2(a.x * ar - a.y * ai, a.x * ai + a.y * ar);
}
// rectangle norm of the off-centre frame (1 on the frame edge)
__device__ __forceinline__ float dr_norm(float2 z, const float* __restrict__ K) {
    const float nx = z.x > 0.f ? z.x * K[K_IEXP] : -z.x * K[K_IEXN];
    const float ny = z.y > 0.f ? z.y * K[K_IEYP] : -z.y * K[K_IEYN];
    return fmaxf(nx, ny);
}
// Chebyshev distance outside the frame rectangle (< 0 inside), in normalised units: square-cornered bezels
__device__ __forceinline__ float dr_dist(float2 z, const float* __restrict__ K) {
    const float dx = z.x > 0.f ? z.x - K[K_EXP] : -z.x - K[K_EXN];
    const float dy = z.y > 0.f ? z.y - K[K_EYP] : -z.y - K[K_EYN];
    return fmaxf(dx, dy);
}
// box-filtered coverage of the band [0, bw] by a pixel window of width fp centred at d
__device__ __forceinline__ float dr_cov(float d, float fp, float bw) {
    return clamp01((fminf(d + 0.5f * fp, bw) - fmaxf(d - 0.5f * fp, 0.f)) / fmaxf(fp, 1e-9f));
}

// One Droste lookup at output log coordinate (wr, wi); corr = level offset of this branch (colour continuity).
__device__ float3 dr_tap(const Tex& T, float wr, float wi, float corr, float rz, float A, float invh2,
                         const float* __restrict__ K, const float* __restrict__ PAL) {
    // source log coordinate: c * w - q * g
    const float sr = K[K_CRE] * wr - K[K_CIM] * wi + K[K_SHR];
    const float si = K[K_CRE] * wi + K[K_CIM] * wr + K[K_SHI];
    float s, c;
    __sincosf(dr_wrap(si), &s, &c);
    const float e1 = __expf(sr);
    const float N1 = dr_norm(make_float2(e1 * c, e1 * s), K);
    float k = floorf(-__logf(fmaxf(N1, 1e-30f)) * K[K_INVL]);
    k = fminf(fmaxf(k, -12.f), 64.f);
    __sincosf(dr_wrap(si + k * K[K_GIM]), &s, &c);
    const float e2 = __expf(sr + k * K[K_GRE]);
    float2 P = make_float2(e2 * c, e2 * s);
    #pragma unroll
    for (int it = 0; it < 3; ++it) {                       // exact fix-ups (only needed for turned copies)
        if (dr_norm(P, K) >= 1.f) { P = dr_cmul(P, K[K_GIR], K[K_GII]); k -= 1.f; continue; }
        const float2 Qi = dr_cmul(P, K[K_G2R], K[K_G2I]);
        if (dr_norm(Qi, K) < 1.f) { P = Qi; k += 1.f; continue; }
        break;
    }
    const float2 Q = dr_cmul(P, K[K_G2R], K[K_G2I]);       // the same point in its inner copy's coordinates
    // conformal footprint of one output pixel at P (normalised units) and in source pixels
    const float rP = sqrtf(P.x * P.x + P.y * P.y);
    const float fpP = invh2 * K[K_CABS] * rP / rz;
    const float lod = __log2f(fmaxf(K[K_CABS] * rP / rz, 1e-12f)) - 0.35f;
    const float u = 0.5f + (K[K_C0X] + P.x) / (2.f * A), v = 0.5f + 0.5f * (K[K_C0Y] + P.y);
    float3 col = tex_sample(T, u, v, lod);

    const float rq = sqrtf(K[K_G2R] * K[K_G2R] + K[K_G2I] * K[K_G2I]);
    const float fpQ = fpP * rq;
    const float dQ = fmaxf(dr_dist(Q, K), 0.f);             // outside the inner copy (copy units)
    const float dP = fminf(dr_dist(P, K), 0.f);             // inside this level's own frame edge
    const float bw = K[K_BW];
    const float lev = k + corr;
    // glow: light from the inner copy's bezel spilling over this level's picture (screen blend)
    if (K[K_GLOW] > 1e-4f) {
        const float gl = K[K_GLOW] * __expf(-fmaxf(dQ - bw, 0.f) / K[K_GW]);
        const float3 gc = pal(K[K_CBASE] + K[K_CSTEP] * (lev + 1.f) + 0.08f, PAL) * gl;
        col = make_float3(1.f - (1.f - col.x) * (1.f - fminf(gc.x, 1.f)),
                          1.f - (1.f - col.y) * (1.f - fminf(gc.y, 1.f)),
                          1.f - (1.f - col.z) * (1.f - fminf(gc.z, 1.f)));
    }
    if (bw > 0.f && K[K_BEZ] > 0.f) {
        const float co = dr_cov(dQ, fpQ, bw) * K[K_BEZ];   // the inner copy's bezel, on this level
        const float ci = dr_cov(dP, fpP, bw) * K[K_BEZ];   // this level's own bezel (AA of its inside edge)
        if (co > 1e-4f) {
            const float sb = clamp01(dQ / bw);
            const float3 bc = pal(K[K_CBASE] + K[K_CSTEP] * (lev + 1.f) + 0.12f * sb, PAL)
                              * (K[K_BRI] * (0.62f + 0.45f * __sinf(3.14159265f * sb)));
            col = col + (bc - col) * co;
        }
        if (ci > 1e-4f) {
            const float3 bc = pal(K[K_CBASE] + K[K_CSTEP] * lev, PAL) * (K[K_BRI] * 0.62f);
            col = col + (bc - col) * ci;
        }
    }
    return col;
}

extern "C" __global__
void droste(uchar4* __restrict__ out, const int w, const int h, FRAME_ARGS,
            const float* __restrict__ K, const float* __restrict__ PAL)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    TEX_FROM_ARGS;
    const float A = (float)w / (float)h, invh2 = 2.f / (float)h;
    const float zx = ((float)x + 0.5f - 0.5f * w) * invh2 - K[K_C0X];
    const float zy = ((float)y + 0.5f - 0.5f * h) * invh2 - K[K_C0Y];
    const float rz = fmaxf(sqrtf(zx * zx + zy * zy), 1e-6f);
    const float wr = __logf(rz), wi = atan2f(zy, zx);
    float3 col = dr_tap(T, wr, wi, 0.f, rz, A, invh2, K, PAL);
    if (K[K_TWO] > 0.5f) {                                  // twist easing: hide the branch cut (see docstring)
        const bool lo = wi < 0.f;
        const float3 c2 = dr_tap(T, wr, lo ? wi + D_TWO_PI : wi, lo ? K[K_T] : 0.f, rz, A, invh2, K, PAL);
        const float beta = 0.5f - 0.5f * __cosf(wi);
        col = col + (c2 - col) * beta;
    }
    const float mw = K[K_MIX];
    if (mw < 1.f) {
        const float3 s0 = tex_fetch(T, x, y);
        col = s0 + (col - s0) * mw;
    }
    store_rgb(out, y * w + x, col);
}
"""


def _clamp01(v: float) -> float:
    return min(max(float(v), 0.0), 1.0)


def _smooth01(x: float) -> float:
    x = _clamp01(x)
    return x * x * (3.0 - 2.0 * x)


def _squeeze(f: float, gate: float) -> float:
    """Displayed zoom phase: f itself at gate 1, squeezed onto 0 or 1 (both identity-equivalent) as gate -> 0.
    h(0) = 0 and h(1) = 1 for every gate, so the wrap stays seamless."""
    if f <= 0.0:
        return 0.0
    if f >= 1.0:
        return 1.0
    p = 1.0 / max(gate, 0.02)
    a, b = f ** p, (1.0 - f) ** p
    return a / max(a + b, 1e-300)


class Droste(Effect):
    name = "droste"
    stage = "warp"
    label = "Droste spiral"
    description = ("Escher's Print Gallery: the picture contains a smaller copy of itself forever, twisted into a "
                   "logarithmic spiral and zooming endlessly inwards.")
    params = {
        "amount": {**standard_effect_params()["amount"], "default": 1.0},
        # the beat envelope flashes the bezels directly: keep it raw (easing would flatten the punch)
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "ratio": num(0.1, 0.6, 0.22, "Inner copy scale", step=0.01, tier="regional", basic=True),
        "twist": num(0.0, 1.0, 1.0, "Nested ↔ Escher spiral", tier="regional", basic=True),
        "strands": num(1.0, 3.0, 1.0, "Spiral strands", step=0.05, tier="regional"),
        "copy_turn": num(-0.6, 0.6, 0.0, "Copy rotation per level", unit="rad", step=0.01, tier="regional"),
        "frame_border": num(0.0, 0.02, 0.006, "Bezel width", unit="× copy height", step=0.0005, tier="regional"),
        "glow": num(0.0, 1.0, 0.35, "Bezel glow", tier="regional"),
        "centre_x": num(-0.5, 0.5, 0.0, "Nest centre x", unit="× half width", tier="regional"),
        "centre_y": num(-0.5, 0.5, 0.0, "Nest centre y", unit="× half height", tier="regional"),
        "wander": num(0.0, 0.25, 0.05, "Centre wander", tier="regional"),
        "punch": num(0.0, 4.0, 1.5, "Beat zoom kick", step=0.05, tier="regional"),
        "zoom_speed": num(-1.0, 2.0, 0.3, "Zoom speed", unit="e-fold/s", tier="realtime", bind="rate"),
        **palette_params("neon", 1.0, 0.03),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC, FRAME3D_OPTIONS).get_function("droste")
        self.mips = FrameMips(ctx, levels=6, blur_level=0)
        self._K_h = np.zeros(NK, dtype=np.float32)
        self._K_d = cp.zeros(NK, dtype=cp.float32)
        self._P_d = cp.zeros(12, dtype=cp.float32)
        p0 = self.easer.values()
        self._pal = np.array(palette_coeffs(p0.get("palette", "neon")), dtype=np.float64)
        self._P_d.set(self._pal.astype(np.float32))
        seed = int(params.get("seed", 7) or 7) if isinstance(params, dict) else 7
        rng = np.random.default_rng(seed)
        self._wx, self._wy = (float(v) for v in rng.uniform(0.0, TWO_PI, size=2))
        self._drift = float(rng.uniform(0.0, 1.0))
        self._phi = 0.0          # zoom phase in levels, wrapped to [0, 1)
        self._cyc = 0.0          # palette offset accumulated over completed zoom cycles (mod 10)
        self._pk = 0.0           # attack-smoothed pulse

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dts = min(max(float(dt), 0.0), 0.1)
        # pulse: ~25 ms attack, then follows the envelope down
        pulse = _clamp01(p["pulse"])
        if pulse > self._pk and dts > 0:
            self._pk += (pulse - self._pk) * (1.0 - math.exp(-dts / 0.025))
        elif pulse <= self._pk:
            self._pk = pulse
        # palette coefficients ease (the enum switches at once)
        tgt = np.array(palette_coeffs(p["palette"]), dtype=np.float64)
        if dts > 0 and not np.array_equal(tgt, self._pal):
            self._pal += (tgt - self._pal) * (1.0 - math.exp(-dts / 1.0))
            if np.max(np.abs(tgt - self._pal)) < 1e-4:
                self._pal = tgt
            self._P_d.set(self._pal.astype(np.float32))
        self._drift = (self._drift + float(p["drift_speed"]) * dts) % 10.0
        self._wx = (self._wx + TWO_PI * 0.043 * dts) % TWO_PI
        self._wy = (self._wy + TWO_PI * 0.031 * dts) % TWO_PI
        step = 0.13 * float(p["palette_period"])

        a = _clamp01(p["amount"])
        if a <= 1e-4:
            if self._phi > 0.5:
                self._cyc = (self._cyc + step) % 10.0
            self._phi = 0.0
            self.ctx.cp.copyto(out, src)
            return
        m = _smooth01(a)
        s_copy = _smooth01(a / 0.4)
        gate = _smooth01((a - 0.15) / 0.35)
        s_tw = _smooth01((a - 0.45) / 0.4)

        ratio = min(max(float(p["ratio"]), 0.05), 0.9)
        r_eff = max(ratio * s_copy, 2e-4)
        L = -math.log(r_eff)
        turn = float(p["copy_turn"]) * s_copy
        t = float(p["twist"]) * min(max(float(p["strands"]), 1.0), 3.0) * s_tw

        # zoom phase (levels): integrated, attracted to a whole level as the gate closes, wrapped mod 1
        rate = float(p["zoom_speed"]) * (1.0 + float(p["punch"]) * self._pk * m) / L
        self._phi += rate * gate * dts
        if gate < 1.0 and dts > 0:
            self._phi += (round(self._phi) - self._phi) * (1.0 - math.exp(-dts * 4.0 * (1.0 - gate)))
        while self._phi >= 1.0:
            self._phi -= 1.0
            self._cyc = (self._cyc + step) % 10.0
        while self._phi < 0.0:
            self._phi += 1.0
            self._cyc = (self._cyc - step) % 10.0
        q = _squeeze(self._phi, gate)

        W, H = self.ctx.width, self.ctx.height
        A = W / H
        wander = float(p["wander"])
        cx = A * min(max(float(p["centre_x"]) + wander * math.sin(self._wx), -0.6), 0.6)
        cy = min(max(float(p["centre_y"]) + 0.8 * wander * math.sin(self._wy), -0.6), 0.6)
        exp_, exn, eyp, eyn = A - cx, A + cx, 1.0 - cy, 1.0 + cy

        gre, gim = L, -turn
        cre, cim = 1.0 - t * turn / TWO_PI, -t * L / TWO_PI
        e = 1.0 / r_eff
        frac = t - round(t)

        K = self._K_h
        K[K_C0X], K[K_C0Y] = cx, cy
        K[K_EXP], K[K_EXN], K[K_EYP], K[K_EYN] = exp_, exn, eyp, eyn
        K[K_IEXP], K[K_IEXN], K[K_IEYP], K[K_IEYN] = 1.0 / exp_, 1.0 / exn, 1.0 / eyp, 1.0 / eyn
        K[K_CRE], K[K_CIM] = cre, cim
        K[K_SHR], K[K_SHI] = -q * gre, -q * gim
        K[K_GRE], K[K_GIM] = gre, gim
        K[K_G2R], K[K_G2I] = e * math.cos(gim), e * math.sin(gim)
        K[K_GIR], K[K_GII] = r_eff * math.cos(-gim), r_eff * math.sin(-gim)
        K[K_INVL] = 1.0 / L
        K[K_T] = t
        K[K_TWO] = 1.0 if abs(frac) > 1e-3 else 0.0
        border = float(p["frame_border"])
        K[K_BW] = 2.0 * border
        K[K_BEZ] = _smooth01(a / 0.25) if border > 1e-5 else 0.0
        kick = float(p["punch"]) * self._pk * m
        K[K_BRI] = min(1.0 + 0.45 * kick, 2.5)
        K[K_GLOW] = _clamp01(p["glow"]) * 0.7 * m * (1.0 + 0.6 * kick)
        K[K_GW] = 0.025 + 0.05 * _clamp01(p["glow"])
        K[K_CBASE] = (self._drift + self._cyc) % 10.0
        K[K_CSTEP] = step
        K[K_CABS] = math.hypot(cre, cim)
        K[K_MIX] = _smooth01(a / 0.08)
        self._K_d.set(K)

        self.mips.build(src)
        launch_2d(self._k, W, H, (out, np.int32(W), np.int32(H), *self.mips.args(src), self._K_d, self._P_d))

    def close(self) -> None:
        self._K_d = None
        self._P_d = None
        self.mips = None
