"""Oscilloscope: a glowing phosphor scope of the room's live sound, drawn over the picture.

Source: Original
Author: smfx (original)
License: MIT (original)
license_class: permissive

Listens to the fast feed (``listens = ("scope",)``, CONVENTIONS 2b): ``self.sound.wave_gpu`` (float32 [1024], auto-gained
to about +-1) from ``self.sound.wave_trigger`` (a rising zero crossing), so a steady tone draws a steady trace.

How it works.  Three small kernels a frame, no host round trips:
  1. ``osc_fold`` (internal res, render_scale 0.5 = 1080p at 4K): the phosphor.  Two float planes per pixel, the
     afterglow P and the last frame's fresh beam N: P <- P * k + N * (1 - k), N <- 0, with k = exp(-dt / persistence).
     So a still trace settles at P = N whatever the frame rate, dt = 0 changes nothing, and the same fold runs on a
     quarter-res halo pair.  The afterglow is shown with a gain that grows as sqrt(persistence), like a real phosphor
     building up: short persistence is just the crisp beam, long a luminous cloud of the last traces.
  2. ``osc_splat`` (one thread per trace segment x sub-splat, 511 x 64): the beam.  Each of the 512 trace samples
     gets a position from the shape morphs (below); a segment between samples i and i+1 is a Catmull-Rom curve
     through i-1 .. i+2, split into n = len / (1.5 sigma) sub-splats (<= 64) so the line stays continuous, each an
     energy-normalised gaussian (atomicAdd, <= 14 x 14 taps) into N.  CRT touch: a segment's energy goes as
     len^(1 - 0.8) past the flat-trace spacing (x (1 + 7 xy): in XY every stroke is fast), so the beam is dimmer
     where it moves fast.  A few sub-splats per
     segment (spacing 1.5 sigma of the wide halo) also land in the quarter-res halo plane.  The splat width has a
     floor (0.6 px) but the energy follows the true width, so a sub-pixel beam at a low render scale gets dimmer
     instead of wider (correct coverage).
  3. ``osc_comp`` (output res): bilinear reads of both pairs, colour from the shared cosine palette (a gentle
     gradient across the frame + hue + drift; the afterglow is shifted along the palette like a P7 phosphor; each
     colour lifted to full value, near-black palette regions easing to a pale white, so drift never dims it), a hot
     core that whitens where it is brightest, soft saturation (1 - exp), a faint dark underlay where the light falls
     (so the beam reads on bright art), then a screen blend in sRGB at opacity x amount.  Unlit pixels copy src.

Shape morphs (regional, continuous): ``shape`` 0 = a horizontal trace across the middle third, 1 = the same trace
bent into a closed ring (arc curvature shape x 2 pi / L, recentred on its centroid; the signal displaces along the
arc normal, so it modulates the radius; the seam eases to the base ring).  ``xy`` 0 -> 1 morphs any of those to an
XY figure: x = s(t), y = s(t - delay) (fractional samples), turned 45 degrees like a goniometer, so a pure tone is
an ellipse and a chord a Lissajous knot.  The XY window starts at max(trigger, delay + 1), so the delayed sample
never runs off the start of the buffer.

The XY figure's size follows the trace's eased RMS (x 0.8 .. 2.6, fast up / slow down), so a quiet passage while the
feed's slow AGC is still rising draws a legible knot rather than a scribble in the middle; capped to the frame
width in portrait.

amount 0: out = src exactly (and the phosphor is cleared).  The phosphor is also cleared when the effect was not
rendered for a while (the slot skips render at amount ~0) or its clock restarts, so a fade-in never shows a stale
trace; a shorter gap decays it by the real elapsed time.  No sound object, no feed or silence: a barely visible
idle trace (a flat line, a ring, or nothing at all in XY: a still beam deposits no energy).  pulse: an amplitude
kick (beam brightness, glow, a little height), never a position.  Fast-attack beam, so the ~0.25 s feed lag hides.
Buffers follow a render_scale change (reallocated once, when the target changes).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params
from stream_service.plugins._lib.upsample import internal_size
from stream_service.scope_feed import WAVE_N

M_TR = 512                    # trace samples: wave[trig : trig + 512] (trig <= 511, so always inside the buffer)
S_MAX = 64                    # sub-splats per segment, at most
NP = 48
SQRT_2PI = math.sqrt(2.0 * math.pi)
CRT_GAMMA = 0.8               # 1 = a real CRT (energy per segment constant); < 1 keeps fast strokes visible
SIG_MIN, SIG_G_MIN = 0.6, 0.8  # splat width floors (internal / halo px)
XY_SPEED = 7.0                # the CRT dimming's reference stroke grows (1 + 7 xy)x in XY, where every stroke is fast
# P layout (floats)
#  0 A | 1 L trace length | 2 kappa | 3 centroid shift | 4 shape | 5 xy | 6 trace amplitude | 7 xy radius
#  8 vertical offset | 9 xy delay (samples) | 10 sigma (internal px) | 11 sigma halo (halo px)
#  12 core energy scale | 13 halo energy scale | 14 L0 flat spacing (internal px) | 15 crt gamma | 16 end fade weight
#  17 xy window start (sample index)
#  20..31 palette | 32 hue + drift | 33 period | 34 glow | 35 alpha | 36 underlay | 37 afterglow hue | 38 afterglow

_SRC = HELPERS + PALETTE_CUDA + r"""
#define M_TR %(m)d
#define S_MAX %(s)d
#define WN %(wn)d

__device__ __forceinline__ float osc_sinc(float x) {
    return fabsf(x) < 0.05f ? 1.f - x * x * (1.f / 6.f) * (1.f - x * x * 0.05f) : sinf(x) / x;
}

// palette hue at full value: light takes its hue from the palette, never its darkness (near-black palette regions
// ease continuously to a pale white, so drift never dims the beam)
__device__ __forceinline__ float3 osc_col(float t, const float* __restrict__ P) {
    const float3 c = pal(t, P);
    const float m = fmaxf(fmaxf(c.x, c.y), fmaxf(c.z, 0.f));
    const float s = 1.f / fmaxf(m, 0.2f);
    const float wv = (1.f - fminf(m * s, 1.f)) * 0.6f;
    return make_float3(fminf(c.x * s + wv, 1.f), fminf(c.y * s + wv, 1.f), fminf(c.z * s + wv, 1.f));
}

__device__ __forceinline__ float osc_wave(const float* __restrict__ wave, float i) {
    i = fminf(fmaxf(i, 0.f), (float)(WN - 1));
    const int i0 = (int)i, i1 = min(i0 + 1, WN - 1);
    const float f = i - i0;
    return wave[i0] + (wave[i1] - wave[i0]) * f;
}

// trace sample i (0..M_TR-1) -> world position (y up, half-height units, x in [-A, A])
__device__ float2 osc_pos(const float* __restrict__ wave, const int trig, int i, const float* __restrict__ P) {
    i = min(max(i, 0), M_TR - 1);
    const float sx = wave[min(trig + i, WN - 1)];
    const int bxy = min(max((int)P[17], 0), WN - M_TR);
    const float xx = wave[min(bxy + i, WN - 1)];
    const float yy = osc_wave(wave, (float)(bxy + i) - P[9]);
    const float t = i * (1.f / (M_TR - 1));
    const float shape = P[4];
    const float tp = smooth(0.f, 0.07f, t) * smooth(0.f, 0.07f, 1.f - t);   // the ring's seam eases to the base ring
    const float st = sx * (1.f + (tp - 1.f) * shape);
    const float u = (t - 0.5f) * P[1];
    const float ph = u * P[2];
    float sn, cs;
    sincosf(ph, &sn, &cs);
    const float hs = osc_sinc(0.5f * ph);
    float bx = u * osc_sinc(ph);                       // the arc, curling down; a full turn closes at the bottom
    float by = -u * 0.5f * ph * hs * hs + P[3];
    bx += sn * st * P[6];                              // the signal along the arc normal
    by += cs * st * P[6];
    const float X = (xx - yy) * P[7], Y = (xx + yy) * P[7];   // XY, turned 45 degrees (goniometer)
    const float xy = P[5];
    return make_float2(bx + (X - bx) * xy, by + (Y - by) * xy + P[8]);
}

__device__ __forceinline__ float2 osc_cr(const float2* q, float t) {   // uniform Catmull-Rom through q1..q2
    const float t2 = t * t, t3 = t2 * t;
    const float a = -0.5f * t3 + t2 - 0.5f * t, b = 1.5f * t3 - 2.5f * t2 + 1.f;
    const float c = -1.5f * t3 + 2.f * t2 + 0.5f * t, d = 0.5f * t3 - 0.5f * t2;
    return make_float2(a * q[0].x + b * q[1].x + c * q[2].x + d * q[3].x,
                       a * q[0].y + b * q[1].y + c * q[2].y + d * q[3].y);
}

// energy-normalised gaussian splat into channel 1 of an interleaved float2 plane (<= 14 x 14 taps)
__device__ void osc_put(float* __restrict__ acc, const int w, const int h, float x, float y, const float sig,
                        const float e)
{
    if (!(e > 0.f) || !(fabsf(x) < 1e7f) || !(fabsf(y) < 1e7f)) return;   // also drops NaN / inf positions
    const float fx = x - 0.5f, fy = y - 0.5f;
    const float rr = fminf(2.5f * sig, 6.5f);
    const int ux0 = (int)ceilf(fx - rr), ux1 = (int)floorf(fx + rr);
    const int uy0 = (int)ceilf(fy - rr), uy1 = (int)floorf(fy + rr);
    if (ux1 < 0 || uy1 < 0 || ux0 >= w || uy0 >= h) return;
    const float k = -0.5f / (sig * sig);
    float wx[14], wy[14], sx = 0.f, sy = 0.f;
    #pragma unroll
    for (int i = 0; i < 14; ++i) {
        const float dx = ux0 + i - fx, dy = uy0 + i - fy;
        const float a = (ux0 + i <= ux1) ? __expf(k * dx * dx) : 0.f;
        const float b = (uy0 + i <= uy1) ? __expf(k * dy * dy) : 0.f;
        wx[i] = a; wy[i] = b; sx += a; sy += b;
    }
    const float s = e / fmaxf(sx * sy, 1e-12f);
    const int nx = min(ux1 - ux0 + 1, 14), ny = min(uy1 - uy0 + 1, 14);
    for (int j = 0; j < ny; ++j) {
        const int yy = uy0 + j;
        if (yy < 0 || yy >= h) continue;
        const float wyj = wy[j] * s;
        for (int i = 0; i < nx; ++i) {
            const int xx = ux0 + i;
            if (xx < 0 || xx >= w) continue;
            atomicAdd(&acc[2 * (yy * w + xx) + 1], wx[i] * wyj);
        }
    }
}

extern "C" __global__ void osc_fold(float2* __restrict__ acc, const int w, const int h,
                                    float2* __restrict__ gacc, const int gw, const int gh, const float k, const float wn)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const int i = y * w + x;
    const float2 a = acc[i];
    float v = a.x * k + a.y * wn;
    acc[i] = make_float2(v > 1e-5f ? v : 0.f, 0.f);
    if (x < gw && y < gh) {
        const int j = y * gw + x;
        const float2 g = gacc[j];
        v = g.x * k + g.y * wn;
        gacc[j] = make_float2(v > 1e-5f ? v : 0.f, 0.f);
    }
}

extern "C" __global__ void osc_splat(const float* __restrict__ wave, const int trig, const float* __restrict__ P,
                                     float* __restrict__ acc, const int w, const int h,
                                     float* __restrict__ gacc, const int gw, const int gh)
{
    const int gid = blockIdx.x * blockDim.x + threadIdx.x;
    const int seg = gid / S_MAX, j = gid - seg * S_MAX;
    if (seg >= M_TR - 1) return;
    const float sxp = w / (2.f * P[0]), syp = 0.5f * h;
    float2 q[4];
    #pragma unroll
    for (int k = 0; k < 4; ++k) {
        const float2 p = osc_pos(wave, trig, seg - 1 + k, P);
        q[k] = make_float2(0.5f * w + p.x * sxp, 0.5f * h - p.y * syp);
    }
    const float dx = q[2].x - q[1].x, dy = q[2].y - q[1].y;
    const float len = sqrtf(dx * dx + dy * dy);
    const float sig = P[10];
    const int n = min(max((int)ceilf(len / (1.5f * sig)), 1), S_MAX);
    if (j >= n) return;
    const float L0 = P[14];
    const float I = __powf(L0 / fmaxf(len, L0), P[15]);           // dimmer where the beam moves fast
    const float tt = (seg + (j + 0.5f) / n) * (1.f / (M_TR - 1));
    const float fe = smooth(0.f, 0.045f, tt) * smooth(0.f, 0.045f, 1.f - tt);
    const float wend = 1.f + (fe - 1.f) * P[16];                  // the trace's ends fade in (not on the ring)
    const float base = I * wend * len;
    const float2 c = osc_cr(q, (j + 0.5f) / n);
    osc_put(acc, w, h, c.x, c.y, sig, P[12] * base / n);
    const float gsx = (float)gw / w, gsy = (float)gh / h;
    const float sigG = P[11];
    const int nG = min(max((int)ceilf(len * gsx / (1.5f * sigG)), 1), n);
    if (j < nG) {
        const float2 cg = osc_cr(q, (j + 0.5f) / nG);
        osc_put(gacc, gw, gh, cg.x * gsx, cg.y * gsy, sigG, P[13] * base * gsx / nG);
    }
}

__device__ __forceinline__ float2 osc_bil(const float2* __restrict__ b, const int w, const int h, float fx, float fy) {
    fx -= 0.5f; fy -= 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0;
    const int xa = min(max(x0, 0), w - 1), xb = min(max(x0 + 1, 0), w - 1);
    const int ya = min(max(y0, 0), h - 1), yb = min(max(y0 + 1, 0), h - 1);
    const float2 a = b[ya * w + xa], c = b[ya * w + xb], d = b[yb * w + xa], e = b[yb * w + xb];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float2(v * (u * a.x + tx * c.x) + ty * (u * d.x + tx * e.x),
                       v * (u * a.y + tx * c.y) + ty * (u * d.y + tx * e.y));
}

extern "C" __global__ void osc_comp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
                                    const float2* __restrict__ acc, const int w, const int h,
                                    const float2* __restrict__ gacc, const int gw, const int gh,
                                    const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const uchar4 s4 = src[y * W + x];
    const float px = x + 0.5f, py = y + 0.5f;
    const float2 a = osc_bil(acc, w, h, px * w / W, py * h / H);
    const float2 g = osc_bil(gacc, gw, gh, px * gw / W, py * gh / H);
    const float fresh = a.y, after = a.x * P[38], halo = (g.x * P[38] + g.y) * P[34];
    const float tot = fresh + after + halo;
    if (!(tot >= 2e-4f && tot < 1e30f)) { out[y * W + x] = s4; return; }       // unlit (or non-finite): src
    const float idx = P[32] + P[33] * (px / W * 0.55f + (1.f - py / H) * 0.2f);
    const float3 cb = osc_col(idx, P + 20), ca = osc_col(idx + P[37], P + 20);
    const float3 lb = make_float3(cb.x * cb.x, cb.y * cb.y, cb.z * cb.z);       // ~linear (gamma 2)
    const float3 la = make_float3(ca.x * ca.x, ca.y * ca.y, ca.z * ca.z);
    const float hot = fminf(fresh * 0.45f, 0.7f);                               // the core whitens where brightest
    const float Lr = (lb.x + (1.f - lb.x) * hot) * fresh + la.x * after + lb.x * halo;
    const float Lg = (lb.y + (1.f - lb.y) * hot) * fresh + la.y * after + lb.y * halo;
    const float Lb = (lb.z + (1.f - lb.z) * hot) * fresh + la.z * after + lb.z * halo;
    const float al = P[35];
    const float sh = 1.f - P[36] * al * (1.f - __expf(-2.5f * tot));            // faint dark underlay (bright art)
    const float lr = al * sqrtf(1.f - __expf(-Lr)), lg = al * sqrtf(1.f - __expf(-Lg)), lbb = al * sqrtf(1.f - __expf(-Lb));
    const float r = s4.x * sh, gg = s4.y * sh, bb = s4.z * sh;
    out[y * W + x] = pack(make_float4(r + lr * (255.f - r), gg + lg * (255.f - gg), bb + lbb * (255.f - bb), 255.f));
}
""" % {"m": M_TR, "s": S_MAX, "wn": WAVE_N}


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _ease(cur: float, target: float, dt: float, up_tau: float, down_tau: float) -> float:
    if dt <= 0.0:
        return cur
    tau = up_tau if target > cur else down_tau
    return cur + (target - cur) * (1.0 - math.exp(-dt / tau))


def _finite(v, default: float = 0.0) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


class Oscilloscope(Effect):
    name = "oscilloscope"
    stage = "overlay"
    label = "Oscilloscope"
    description = ("A glowing phosphor oscilloscope of the room's live sound over the picture: a thin bright beam "
                   "with soft bloom and fading persistence, as a flat trace, a ring or a Lissajous figure.")
    listens = ("scope",)
    params = {
        **standard_effect_params(),
        "shape": num(0.0, 1.0, 0.0, "Shape (trace · ring)", tier="regional", basic=True),
        "xy": num(0.0, 1.0, 0.0, "Lissajous (XY figure)", tier="regional"),
        "persistence": num(0.03, 1.2, 0.35, "Persistence", unit="s", tier="regional", basic=True),
        "height": num(0.1, 2.5, 1.0, "Height", unit="×", tier="regional"),
        "thickness": num(1.0, 8.0, 2.6, "Beam width", unit="px", step=0.1, tier="regional"),
        "glow": num(0.0, 2.0, 1.0, "Glow", tier="regional"),
        "position": num(-1.0, 1.0, 0.0, "Vertical position", tier="regional"),
        "delay": num(2.0, 240.0, 48.0, "XY delay", unit="samples", step=1.0, tier="regional"),
        "opacity": num(0.0, 1.0, 0.85, "Opacity", tier="regional"),
        "hue": num(0.0, 1.0, 0.0, "Hue shift", tier="regional"),
        **palette_params("ice", period=0.6, drift=0.02),
        "render_scale": num(0.25, 1.0, 0.5, "Render scale", step=0.05, tier="quality"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC, ("--std=c++14", "--use_fast_math"))
        self._kf = mod.get_function("osc_fold")
        self._ks = mod.get_function("osc_splat")
        self._kc = mod.get_function("osc_comp")
        W, H = ctx.width, ctx.height
        self.A = W / H
        self.zeros = cp.zeros(WAVE_N, cp.float32)                 # the idle wave when there is no sound at all
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.drift = 0.0
        self.pres = None
        self.lev = 0.0
        self.rms_e = 0.0
        self.t_last = None
        self.clean = True
        self.blocks = ((M_TR - 1) * S_MAX + 255) // 256
        self._rs = None
        self._alloc(_finite(params.get("render_scale", 0.5), 0.5))

    def _alloc(self, rs: float) -> None:
        """(Re)allocate the phosphor planes for render_scale ``rs``: at setup, then only when the target changes."""
        rs = min(max(rs, 0.25), 1.0)
        if self._rs is not None and abs(rs - self._rs) < 1e-6:
            return
        cp = self.ctx.cp
        self.w, self.h = internal_size(self.ctx, rs)
        self.gw, self.gh = max(8, self.w // 4), max(8, self.h // 4)
        self.acc = cp.zeros((self.h, self.w, 2), cp.float32)     # afterglow, fresh beam
        self.gacc = cp.zeros((self.gh, self.gw, 2), cp.float32)  # the same at quarter res: the halo
        self.clean = True
        self._rs = rs

    def _palette(self, name: str) -> np.ndarray:
        mod = getattr(self, "modulation", None)
        rows = None
        if mod is not None:
            try:
                rows = mod.palette_rows()
            except Exception:
                rows = None
        return np.asarray(PALETTES.get(name, PALETTES["ice"]) if rows is None else rows, np.float32).reshape(-1)[:12]

    def _clear(self) -> None:
        if not self.clean:
            self.acc.fill(0)
            self.gacc.fill(0)
            self.clean = True

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        a = min(max(_finite(p["amount"]), 0.0), 1.0)
        pulse = min(max(_finite(p["pulse"]), 0.0), 1.0)
        dtc = min(max(_finite(dt), 0.0), 0.5)
        # wrap at 10: every palette's frequencies x 10 are whole numbers, so the wrap is seamless
        self.drift = math.fmod(self.drift + _finite(p["drift_speed"]) * dtc, 10.0)
        # the time since this effect last drew: the slot skips render at amount ~0, so a fade-in can follow a gap
        tl = _finite(t_local)
        el = dtc
        if self.t_last is not None:
            gap = tl - self.t_last
            if gap < -1e-3 or gap > 0.5:
                self._clear()                              # stale (or the clock restarted): start clean
            elif gap > dtc:
                el = gap                                   # decay the afterglow by the real elapsed time
        self.t_last = tl
        if a <= 1e-4:
            self._clear()
            cp.copyto(out, src)
            return
        self._alloc(_finite(self.easer.target.get("render_scale", self._rs), self._rs))

        # how much there is to see (host copies only): idle is a faint trace, playing a full beam
        snd = getattr(self, "sound", None)
        wave = getattr(snd, "wave_gpu", None) if snd is not None else None
        rms, lev, trig = 0.0, 0.0, 0
        if wave is None:
            wave = self.zeros
        else:
            try:
                hw = snd.wave
                rms = _finite(math.sqrt(max(float(np.dot(hw, hw)), 0.0) / max(1, hw.size)))
                lev = min(max(_finite(snd.level), 0.0), 1.0)
                trig = int(snd.wave_trigger)
            except Exception:
                rms, lev, trig = 0.0, 0.0, 0
        trig = min(max(trig, 0), WAVE_N - M_TR)
        pres_t = _smooth(0.012, 0.09, rms)
        if self.pres is None:
            self.pres, self.lev, self.rms_e = pres_t, lev, rms
        else:
            self.pres = _ease(self.pres, pres_t, dtc, 0.06, 0.6)
            self.lev = _ease(self.lev, lev, dtc, 0.04, 0.3)
            self.rms_e = _ease(self.rms_e, rms, dtc, 0.08, 0.8)

        f = lambda k, d: _finite(p.get(k, d), d)           # noqa: E731
        shape = min(max(f("shape", 0.0), 0.0), 1.0)
        xy = min(max(f("xy", 0.0), 0.0), 1.0)
        height = min(max(f("height", 1.0), 0.0), 4.0) * (1.0 + 0.18 * pulse)
        delay = min(max(f("delay", 48.0), 0.0), float(WAVE_N - M_TR - 2))
        A = self.A
        # the XY figure's size follows the trace's RMS (a quiet passage still draws a legible knot), and fits the width
        g_xy = min(max(0.26 / max(self.rms_e, 1e-4), 0.8), 2.6) * min(1.0, A)
        L = 2.0 * A * 0.94
        theta = shape * 2.0 * math.pi
        kappa = theta / L
        if theta < 1e-4:
            cshift = kappa * L * L / 24.0
        else:
            cshift = (1.0 - math.sin(0.5 * theta) / (0.5 * theta)) / kappa
        th = min(max(f("thickness", 2.6), 0.5), 12.0)
        sig_t = th / 2.355 * self.h / 1080.0                        # the true beam sigma (internal px)
        sig_gt = (7.0 + 1.5 * th) * self.gh / 1080.0                # the halo's (quarter-res px)
        sig, sig_g = max(SIG_MIN, sig_t), max(SIG_G_MIN, sig_gt)
        beam = (0.14 + 0.86 * self.pres) * (0.6 + 0.5 * self.lev) * (1.0 + 0.5 * pulse)
        tau = min(max(f("persistence", 0.35), 0.01), 5.0)

        hst = self.host
        hst[0] = A
        hst[1] = L
        hst[2] = kappa
        hst[3] = cshift
        hst[4] = shape
        hst[5] = xy
        hst[6] = height / 3.0 * (1.0 - 0.4 * shape)
        hst[7] = height * 0.8 / math.sqrt(2.0) * g_xy
        hst[8] = min(max(f("position", 0.0), -1.0), 1.0) * 0.62
        hst[9] = delay
        hst[10] = sig
        hst[11] = sig_g
        hst[12] = beam * SQRT_2PI * sig_t                           # energy follows the true width (coverage)
        hst[13] = beam * SQRT_2PI * sig_gt * (1.0 - 0.55 * xy)     # an XY figure piles its loops up: less halo
        hst[14] = L / (M_TR - 1) * 0.5 * self.h * (1.0 + XY_SPEED * xy * g_xy)   # XY strokes are fast: dim the fastest
        hst[15] = CRT_GAMMA
        hst[16] = 1.0 - shape * (1.0 - xy)
        hst[17] = float(min(max(trig, int(math.ceil(delay)) + 1), WAVE_N - M_TR))
        hst[20:32] = self._palette(p["palette"])
        hst[32] = f("hue", 0.0) + self.drift
        hst[33] = f("palette_period", 0.6)
        hst[34] = min(max(f("glow", 1.0), 0.0), 4.0) * 0.7 * (1.0 + 0.8 * pulse)
        hst[35] = min(max(f("opacity", 0.85), 0.0), 1.0) * a
        hst[36] = 0.3
        hst[37] = 0.14
        hst[38] = 0.85 * min(max(math.sqrt(tau / 0.3), 0.35), 2.0)   # the afterglow builds up with persistence
        self.P.set(hst)

        k = math.exp(-min(el, 0.5) / tau)
        launch_2d(self._kf, self.w, self.h, (self.acc, np.int32(self.w), np.int32(self.h), self.gacc,
                                             np.int32(self.gw), np.int32(self.gh), np.float32(k), np.float32(1.0 - k)))
        self._ks((self.blocks,), (256,), (wave, np.int32(trig), self.P, self.acc, np.int32(self.w), np.int32(self.h),
                                         self.gacc, np.int32(self.gw), np.int32(self.gh)))
        self.clean = False
        W, H = self.ctx.width, self.ctx.height
        launch_2d(self._kc, W, H, (src, out, np.int32(W), np.int32(H), self.acc, np.int32(self.w), np.int32(self.h),
                                   self.gacc, np.int32(self.gw), np.int32(self.gh), self.P))

    def close(self) -> None:
        self.acc = self.gacc = self.zeros = self.P = None
