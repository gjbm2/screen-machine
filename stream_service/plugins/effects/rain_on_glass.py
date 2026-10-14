"""Rain on glass: a rainy window in front of the show.  Beads and runners zig-zag down the pane, each drop a tiny
lens that refracts the sharp picture upside down; the dry glass between them is fogged and the trails wipe it clear.

Source: https://www.shadertoy.com/view/ltffzl  ("Heartfelt")
        https://github.com/sanxincao/shadertoy/blob/master/heartfelt.glsl  (mirror)
        https://cables.gl/p/YfIeXD
Author: Martijn Steinrucken aka BigWings ('Heartfelt', 2017)
License: CC-BY-NC-SA-3.0
license_class: noncommercial
Ported from GLSL by smfx (ideas/changes: Heartfelt's structure is kept: a static-bead layer (40 cells per unit)
plus two "DropLayer2" runner layers (12 x 2 grid, uv and uv * 1.85) whose drops stick and slip down a saw-wave
path, wiggle on sin(y + sin(y)), leave a narrowing trail and a string of beads, and merge metaball-style through
smoothstep(.3, 1, sum); the refraction offset is the drop field's gradient, and the drop/trail field picks the
fog's blur level.  Changes: the field is rendered once at half resolution into a buffer (gradient by central
differences in a second tiny pass) instead of three Drops() calls per pixel; each runner also evaluates the
nearer neighbour column so drops are never clipped at cell edges; the scroll is split on the host into an
integer row offset and a fraction (integer hashes per cell, so hours of running never lose precision); every
cell gets a hashed density threshold, so the amount/rain knobs add or remove whole drops (each one growing in)
instead of shrinking them all; the trail length is a knob; fog comes from a FrameMips pyramid (trilinear, then the
Gaussian backdrop) and is lifted towards a palette-tinted mist; drop rims darken and catch a glint; the
Heartfelt cool grade is a palette tint; lightning is beat-gated (plus rare ambient strikes) with a deterministic
double-flicker; the heart/story sequence and mouse control are dropped.)

amount 0 is an exact copy.  amount drives drop density, refraction, fog, mist, tint and vignette through
m = smoothstep(amount), so the fade-in grows drops from nothing and slowly mists the pane.  energy drives the
slide speed, rain density (static beads most), glints and the fog's mist.  pulse is the beat: it kicks the slide
rate (drops shake loose; added to the integrated rate, never to positions), bursts rain density (a spatter of new
beads that settles as the envelope decays) and, gated by the lightning weight, fires a lightning flash on its
rising edge.  dt = 0 holds the scene; there is no image history, so content cuts are safe.
Cost at 4K: half-res drop field (2 runner layers x 2 columns + beads), FrameMips(4) and one full-res composite
with four texture taps.
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

NQ = 16   # field params
NC = 20   # composite params

_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
__device__ __forceinline__ float rog_fract(float x) { return x - floorf(x); }
// Heartfelt's Saw: rises over [0, b], falls over [b, 1]
__device__ __forceinline__ float rog_saw(float b, float t) { return smooth(0.f, b, t) * smooth(1.f, b, t); }

// One column (ix) of a runner layer (Heartfelt DropLayer2, y up).  Returns (drop mask, trail).
//   f: fractional scroll (layer units), K: integer row offset, tph: fract(t), tlen: trail length 0..1,
//   dens: share of cells that carry a drop (hashed threshold per cell, drops grow in)
__device__ float2 rog_cell(int ix, float ux, float uy, float f, int K, float tph, float tlen, float dens,
                           unsigned int seed)
{
    const float colShift = hash1((unsigned)ix, 0x51u, seed);
    const float fy = (uy + f + colShift) * 2.f;              // grid = a * 2, a = (6, 1)
    const float idy = floorf(fy);
    const unsigned int iy = (unsigned)((int)idy + K);
    const float hg = hash1((unsigned)ix, iy, seed + 3u);
    const float w = smooth(hg, hg + 0.15f, dens * 1.15f);
    if (w <= 0.f) return make_float2(0.f, 0.f);
    const float nx = hash1((unsigned)ix, iy, seed + 1u), nz = hash1((unsigned)ix, iy, seed + 2u);
    const float stx = ux * 12.f - ((float)ix + 0.5f);       // relative to this column's centre
    const float sty = fy - idy;
    float x = nx - 0.5f;
    const float wy = uy * 20.f;                              // screen-fixed zig-zag
    const float wiggle = __sinf(wy + __sinf(wy));
    x += wiggle * (0.5f - fabsf(x)) * (nz - 0.5f);
    x *= 0.7f;
    const float ti = rog_fract(tph + nz);
    const float y = (rog_saw(0.85f, ti) - 0.5f) * 0.9f + 0.5f;
    const float dx = stx - x, dy = (sty - y) * 6.f;
    const float mainDrop = smooth(0.4f, 0.f, sqrtf(dx * dx + dy * dy));
    const float top = y + fmaxf(tlen, 0.05f) * (1.f - y);
    const float r = sqrtf(smooth(top, y, sty));
    const float cd = fabsf(dx);
    const float trailFront = smooth(-0.02f, 0.02f, sty - y);
    float trail = 0.f;
    if (r > 1e-3f) trail = smooth(0.23f * r, 0.15f * r * r, cd) * trailFront * r * r;
    const float ddy = 0.5f - rog_fract(uy * 10.f);           // beads left behind stay put on the glass
    const float droplets = smooth(0.3f, 0.f, sqrtf(dx * dx + ddy * ddy));
    return make_float2((mainDrop + droplets * r * trailFront) * w, trail * w);
}

__device__ float2 rog_layer(float ux, float uy, float f, int K, float tph, float tlen, float dens, unsigned int seed)
{
    const float cx = ux * 12.f;
    const int ix = (int)floorf(cx);
    const int nb = (cx - floorf(cx) < 0.5f) ? ix - 1 : ix + 1;   // only the nearer neighbour can reach this pixel
    const float2 a = rog_cell(ix, ux, uy, f, K, tph, tlen, dens, seed);
    const float2 b = rog_cell(nb, ux, uy, f, K, tph, tlen, dens, seed);
    return make_float2(fmaxf(a.x, b.x), fmaxf(a.y, b.y));
}

// Heartfelt StaticDrops: beads that splash on and slowly evaporate.
__device__ float rog_static(float ux, float uy, float tph, float dens, unsigned int seed)
{
    ux *= 40.f; uy *= 40.f;
    const float fx = floorf(ux), fy = floorf(uy);
    const unsigned int ix = (unsigned)(int)fx, iy = (unsigned)(int)fy;
    const float hg = hash1(ix, iy, seed + 14u);
    const float w = smooth(hg, hg + 0.15f, dens * 1.15f);
    if (w <= 0.f) return 0.f;
    const float n0 = hash1(ix, iy, seed + 11u), n1 = hash1(ix, iy, seed + 12u), n2 = hash1(ix, iy, seed + 13u);
    const float sx = ux - fx - 0.5f - (n0 - 0.5f) * 0.4f, sy = uy - fy - 0.5f - (n1 - 0.5f) * 0.4f;
    const float fade = rog_saw(0.025f, rog_fract(tph + n2));
    return smooth(0.3f, 0.f, sqrtf(sx * sx + sy * sy)) * rog_fract(n2 * 10.f) * fade * w;
}

// Q: 0 inv drop scale, 1 scroll frac, 2 fract(t), 3 trail length, 4 bead density, 5 big-runner density,
//    6 small-runner density
extern "C" __global__
void rog_field(float2* __restrict__ F, const int FW, const int FH, const float* __restrict__ Q, const int K,
               const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= FW || y >= FH) return;
    const float inv = Q[0], f = Q[1], tph = Q[2], tlen = Q[3], d0 = Q[4], d1 = Q[5], d2 = Q[6];
    const float ux = ((x + 0.5f) - 0.5f * FW) / FH * inv, uy = (0.5f * FH - (y + 0.5f)) / FH * inv;
    float s = 0.f, trail = 0.f;
    if (d0 > 1e-4f) s += 2.f * rog_static(ux, uy, tph, d0, seed);
    if (d1 > 1e-4f) {
        const float2 a = rog_layer(ux, uy, f, K, tph, tlen, d1, seed + 101u);
        s += a.x; trail = fmaxf(trail, a.y);
    }
    if (d2 > 1e-4f) {
        const float2 b = rog_layer(ux * 1.85f, uy * 1.85f, f, K, tph, tlen, d2, seed + 202u);
        s += b.x; trail = fmaxf(trail, b.y);
    }
    F[y * FW + x] = make_float2(smooth(0.3f, 1.f, s), trail);
}

// Gradient of the drop mask (field texels, y down) + mask + trail.
extern "C" __global__
void rog_grad(const float2* __restrict__ F, float4* __restrict__ G, const int FW, const int FH)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= FW || y >= FH) return;
    const int xm = max(x - 1, 0), xp = min(x + 1, FW - 1), ym = max(y - 1, 0), yp = min(y + 1, FH - 1);
    const float2 c = F[y * FW + x];
    G[y * FW + x] = make_float4(0.5f * (F[y * FW + xp].x - F[y * FW + xm].x),
                                0.5f * (F[yp * FW + x].x - F[ym * FW + x].x), c.x, c.y);
}

__device__ __forceinline__ float4 rog_ldg(const float4* __restrict__ g, int x, int y, int FW, int FH) {
    x = min(max(x, 0), FW - 1); y = min(max(y, 0), FH - 1);
    return g[y * FW + x];
}

// C: 0 refraction px per texel-gradient, 1 fog, 2 wipe, 3 mist, 4 tint, 5 vignette, 6 flash, 7 drift,
//    8 period, 9 rim, 10 glint
extern "C" __global__
void rog_comp(uchar4* __restrict__ out, FRAME_ARGS, const float4* __restrict__ G, const int FW, const int FH,
              const float* __restrict__ P, const float* __restrict__ C)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    TEX_FROM_ARGS;
    const float refr = C[0], fog = C[1], wipe = C[2], mist = C[3], tint = C[4], vig = C[5], flash = C[6];
    const float drift = C[7], period = C[8], rimk = C[9], glintk = C[10];
    const float u = (x + 0.5f) / W, v = (y + 0.5f) / H;
    // bilinear drop field
    const float fx = u * FW - 0.5f, fy = v * FH - 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0, ix = 1.f - tx, iy = 1.f - ty;
    const float4 a = rog_ldg(G, x0, y0, FW, FH), b = rog_ldg(G, x0 + 1, y0, FW, FH);
    const float4 c = rog_ldg(G, x0, y0 + 1, FW, FH), d = rog_ldg(G, x0 + 1, y0 + 1, FW, FH);
    const float gx = iy * (ix * a.x + tx * b.x) + ty * (ix * c.x + tx * d.x);
    const float gy = iy * (ix * a.y + tx * b.y) + ty * (ix * c.y + tx * d.y);
    const float cm = iy * (ix * a.z + tx * b.z) + ty * (ix * c.z + tx * d.z);
    const float tr = iy * (ix * a.w + tx * b.w) + ty * (ix * c.w + tx * d.w);
    // each drop is a lens: sample towards its centre (the picture flips inside it)
    const float su = u + gx * refr / W, sv = v + gy * refr / H;
    const float inDrop = smooth(0.1f, 0.2f, cm);
    const float fogF = fog * (1.f - wipe * clamp01(tr * 1.3f)) * (1.f - inDrop);
    float3 col;
    if (fogF < 0.002f) {
        col = tex_sample0(T, su, sv);
    } else {
        col = tex_sample(T, su, sv, fogF * 3.2f);
        const float bw = smooth(0.4f, 1.f, fogF);
        if (bw > 0.f) col = col + (tex_blur(T, su, sv) - col) * bw;
    }
    const float3 pc = pal(drift + period * (0.3f * u + 0.18f * v + 0.25f * cm), P);
    // condensation scatters light: lift towards a soft palette-tinted mist
    const float mk = mist * fogF;
    if (mk > 0.f) col = col + ((0.30f + 0.26f * pc) - col) * mk;
    // drop rims darken (total internal reflection) and catch a glint from the upper left
    const float gl = sqrtf(gx * gx + gy * gy);
    if (gl > 1e-5f) {
        const float rim = smooth(0.015f, 0.07f, gl) * rimk;
        col = col * (1.f - rim);
        const float sp = clamp01(-(gx * -0.6f + gy * -0.8f) / gl);
        const float gk = sp * sp * sp * sp * sp * sp * smooth(0.02f, 0.09f, gl) * glintk;
        col = col + (1.f - col) * (gk * (0.75f + 0.25f * pc));
    }
    // cinematic grade towards the palette
    if (tint > 0.f) col = col * (1.f + tint * ((0.6f + 0.8f * pc) - 1.f));
    // lightning (screen blend, the fogged pane glows more)
    if (flash > 0.f) {
        const float fk = flash * (0.6f + 0.4f * fogF);
        const float3 fc = 0.8f + 0.2f * pc;
        col = col + (1.f - col) * (fc * fk);
    }
    if (vig > 0.f) {
        const float du = u - 0.5f, dv = v - 0.5f;
        col = col * (1.f - vig * 1.4f * (du * du + dv * dv));
    }
    store_rgb(out, y * W + x, col);
}
"""


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class RainOnGlass(Effect):
    name = "rain_on_glass"
    stage = "overlay"
    label = "Rain on glass"
    description = ("Rain on a window in front of the show: drops zig-zag down leaving clear trails, each one a lens; "
                   "the dry glass is fogged; optional lightning on the beat.")
    params = {
        **standard_effect_params(),
        # the beat envelope is edge-detected on the host for lightning: keep it raw
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "energy": num(0.0, 1.0, 0.4, "Energy", tier="realtime", bind="level"),
        "slide_speed": num(0.0, 3.0, 1.0, "Slide speed", unit="×", tier="realtime", bind="rate"),
        "rain_amount": num(0.0, 1.0, 0.65, "Rain", tier="regional", basic=True),
        "drop_size": num(0.0, 1.0, 0.55, "Drop size", tier="regional"),
        "fog": num(0.0, 1.0, 0.6, "Fog on the glass", tier="regional", basic=True),
        "trail_length": num(0.0, 1.0, 0.8, "Trail length", tier="regional"),
        "refraction": num(0.0, 2.0, 1.0, "Lens strength", step=0.05, tier="regional"),
        "lightning": num(0.0, 1.0, 0.0, "Lightning", tier="regional"),
        "tint": num(0.0, 1.0, 0.35, "Palette grade", tier="regional"),
        **palette_params("ice", 1.0, 0.02),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC, FRAME3D_OPTIONS)
        self._k_field = mod.get_function("rog_field")
        self._k_grad = mod.get_function("rog_grad")
        self._k_comp = mod.get_function("rog_comp")
        W, H = ctx.width, ctx.height
        self._FW, self._FH = max(8, W // 2), max(8, H // 2)
        self._F = cp.zeros((self._FH, self._FW, 2), dtype=cp.float32)
        self._G = cp.zeros((self._FH, self._FW, 4), dtype=cp.float32)
        self.mips = FrameMips(ctx, levels=4, blur_sigma=2.5)
        self._Q_h = np.zeros(NQ, dtype=np.float32)
        self._Q_d = cp.zeros(NQ, dtype=cp.float32)
        self._C_h = np.zeros(NC, dtype=np.float32)
        self._C_d = cp.zeros(NC, dtype=cp.float32)
        self._P_d = cp.zeros(12, dtype=cp.float32)
        p0 = self.easer.values()
        self._pal = np.array(palette_coeffs(p0.get("palette", "ice")), dtype=np.float64)
        self._P_d.set(self._pal.astype(np.float32))
        seed = int(p0.get("seed", 1) or 1)
        self._seed = np.uint32(seed & 0xFFFFFFFF)
        self._rng = np.random.default_rng(seed)
        self._t = 3.0 + 10.0 * float(self._rng.random())   # drop clock (host double)
        self._drift = float(self._rng.random())
        self._clock = 0.0
        self._prev_pulse = 0.0
        self._last_beat = -10.0
        self._flash = 0.0
        self._flash_age = 10.0
        self._amb = 4.0 + 6.0 * float(self._rng.random())

    def _strike(self, level: float) -> None:
        self._flash = max(self._flash, level)
        self._flash_age = 0.0

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        dt = min(max(float(dt), 0.0), 0.25)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        light = min(max(float(p["lightning"]), 0.0), 1.0)
        if dt > 0.0:
            self._clock += dt
            # drops stick and slip; the beat shakes them loose (added to the rate, never to positions)
            rate = 0.2 * float(p["slide_speed"]) * (0.55 + 0.9 * e) * (1.0 + 2.2 * pulse)
            self._t += rate * dt
            self._drift += float(p["drift_speed"]) * dt
            self._drift -= math.floor(self._drift)
            # lightning: beat rising edge (gated by the weight) plus rare ambient strikes
            if pulse - self._prev_pulse > 0.15 and self._clock - self._last_beat >= 0.12:
                self._last_beat = self._clock
                if light > 0.01 and float(self._rng.random()) < 0.2 + 0.8 * light:
                    self._strike(0.55 + 0.45 * pulse)
            if light > 0.01:
                self._amb -= dt * (0.3 + light)
                if self._amb <= 0.0:
                    self._strike(0.7 + 0.3 * float(self._rng.random()))
                    self._amb = 5.0 + 10.0 * float(self._rng.random())
            self._flash *= math.exp(-dt / 0.28)
            self._flash_age += dt
            # palette coefficients ease (the enum switches at once)
            tgt = np.array(palette_coeffs(p["palette"]), dtype=np.float64)
            if not np.array_equal(tgt, self._pal):
                a = 1.0 - math.exp(-dt / 1.0)
                self._pal += (tgt - self._pal) * a
                if np.max(np.abs(tgt - self._pal)) < 1e-4:
                    self._pal = tgt
                self._P_d.set(self._pal.astype(np.float32))
        self._prev_pulse = pulse
        amount = min(max(float(p["amount"]), 0.0), 1.0)
        if amount <= 1e-4:
            cp.copyto(out, src)
            return
        m = _smooth(0.0, 1.0, amount)
        W, H, FW, FH = self.ctx.width, self.ctx.height, self._FW, self._FH
        # ---- drop field (half res)
        ra = min(max(float(p["rain_amount"]), 0.0), 1.0) * (0.8 + 0.4 * e)
        d0 = m * min(1.0, 0.3 + 0.9 * ra + 0.45 * pulse)          # beads (the beat spatters new ones on)
        d1 = m * min(1.0, _smooth(0.15, 0.75, ra) + 0.12 * pulse)  # big runners
        d2 = m * min(1.0, _smooth(0.0, 0.5, ra) + 0.12 * pulse)    # small runners
        scale = 0.55 + 1.1 * min(max(float(p["drop_size"]), 0.0), 1.0)
        S = 0.75 * self._t                         # Heartfelt: uv.y += t * .75 (both runner layers)
        K = math.floor(2.0 * S)
        f = S - 0.5 * K
        q = self._Q_h
        q[0] = 1.0 / scale
        q[1] = f
        q[2] = self._t - math.floor(self._t)
        q[3] = min(max(float(p["trail_length"]), 0.0), 1.0)
        q[4], q[5], q[6] = d0, d1, d2
        self._Q_d.set(q)
        launch_2d(self._k_field, FW, FH, (self._F, np.int32(FW), np.int32(FH), self._Q_d,
                                          np.int32(K % (1 << 28)), self._seed))
        launch_2d(self._k_grad, FW, FH, (self._F, self._G, np.int32(FW), np.int32(FH)))
        # ---- composite (full res)
        fl = 0.0
        if self._flash > 1e-3 and light > 0.0:
            flick = 0.62 + 0.38 * math.cos(2.0 * math.pi * 9.0 * self._flash_age)
            fl = m * self._flash * flick * (0.3 + 0.7 * light) * min(1.0, light * 10.0)
        c = self._C_h
        c[0] = FH * 0.001 * H * 1.4 * float(p["refraction"]) * m
        c[1] = min(max(float(p["fog"]), 0.0), 1.0) * m * (0.8 + 0.2 * ra)
        c[2] = 0.9
        c[3] = 0.16 + 0.1 * e
        c[4] = min(max(float(p["tint"]), 0.0), 1.0) * m
        c[5] = 0.5 * m
        c[6] = fl
        c[7] = self._drift
        c[8] = float(p["palette_period"])
        c[9] = 0.3 * m
        c[10] = m * (0.25 + 0.25 * e) * (1.0 + 0.5 * pulse)
        self._C_d.set(c)
        self.mips.build(src)
        launch_2d(self._k_comp, W, H, (out, *self.mips.args(src), self._G, np.int32(FW), np.int32(FH),
                                       self._P_d, self._C_d))

    def close(self) -> None:
        self._F = self._G = None
        self._Q_d = self._C_d = self._P_d = None
        self.mips = None
