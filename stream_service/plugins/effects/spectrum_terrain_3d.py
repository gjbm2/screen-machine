"""Spectrum terrain 3D: the room's FFT history as glowing ridge lines receding in perspective over the scene.

Source: Original
Author: smfx (original)
License: MIT
license_class: permissive

'Unknown Pleasures' style.  Listens to the fast feed (``listens = ("fft",)``, CONVENTIONS §2b):
``self.sound.fft_history_gpu`` (float32 [128, 48] ring, one row of the 48-band FFT per 1/30 s) with ``fft_row`` (the
newest row) and ``level``.  Each history row is one ridge line: newest at the front, older rows further back, its
height across the width that moment's spectrum (bass left, treble right).

Geometry (host, once per frame, preallocated numpy):
  * a pinhole camera at height c looking level along the terrain (a shifted principal point, so ``tilt`` = how far
    above the terrain it sits: grazing -> overhead), focal 1, the front row's data spanning 86 % of the width, its
    baseline near the bottom.  Row k sits at depth Z0 + (k + phi) dz, where phi (0..1) is the time since the newest
    row arrived, in rows: the rows glide back continuously instead of stepping at 30 Hz.  ``depth`` = row spacing,
    ``rows`` (24..96, eased, the last row fading) = how much history is drawn, ``height`` = the front row's peak
    height on screen (older rows also flatten by 30 %).  ``drift``: a slow bounded camera sway (lateral, parallax)
    and bob.
  * the hidden-line walk bound: per row, its highest possible crest on screen (from the row's own maximum: the
    B-spline below never exceeds it) plus its glow reach; a running minimum gives, for each internal pixel row,
    the first ridge that can reach it (``ks``, uploaded with the scalars in one small ``.set()``).
  * the walk budget, per row: how far a ridge may reach above its baseline (height + glow) so that at most ~46
    ridges behind it share that span (exact for the perspective spacing).  A low, deep camera thins the glow of far,
    crowded rows first and then flattens them; near rows keep their full height (degrade smoothly, never cut).

Device, two passes:
  1. ``st_draw`` at ``render_scale`` (default 0.5): each pixel walks the ridges front to back from ``ks``, at most
     48 of them.  Per ridge: the pixel column's world x at that depth -> a band position; a cubic B-spline through the
     floor-cut bands (smooth, never above the row's max, edge-tapered) -> the crest's screen y and slope.  The ridge
     emits a slope-corrected anti-aliased core line + soft halo (palette colour by band, brightness by its height,
     fading in over the first 1.5 rows and out at the back, dimming with depth and where rows crowd); then, if the
     pixel lies under the crest, the ridge's filled silhouette covers it (AA coverage): a dark translucent ``fill``
     with a faint sheen under the crest, and with ``mesh`` > 0 faint cross lines (the wireframe heightfield's columns,
     interpolated in depth across the strip down to the ridge in front).  Front-to-back compositing (transmittance
     T), stopping once T ~ 0: a ridge hides everything behind it.  Output: tonemapped glow (rgb), fill absorption (a).
  2. ``st_comp`` at full resolution: bilinear-samples that layer, darkens the scene under the fill and screen-blends
     the glow on top in sRGB, both scaled by amount x opacity.  Pixels the layer does not touch are copied untouched.

amount 0, no sound (None, no arrays, all zeros below the floor) -> the output is exactly ``src``; the terrain eases
in / out over ~0.3 s / ~1.2 s when sound comes and goes.  pulse lifts brightness (up to +45 %), never geometry.
dt = 0: nothing advances.  No history of the picture, so a content change is harmless.  Non-finite params read as
their defaults.  The internal layer is sized once at setup from ``render_scale`` (the render thread never allocates,
R20); both kernels take their sizes from it and from ``out``, so any output size or aspect is safe.
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
from stream_service.scope_feed import FFT_BANDS, FFT_HIST

NB = FFT_BANDS                 # 48 log bands
NH = FFT_HIST                  # 128 history rows
MAXROWS = 96
MAXIT = 48                     # ridges one pixel may walk
WALK_CAP = 46.0                # rows one ridge's reach may span (under MAXIT)
OAMP = 40                      # per-row height factor
OHS = OAMP + MAXROWS           # per-row glow scale
NS = OHS + MAXROWS             # then the walk start per internal pixel row
FRONT_SPAN = 0.86              # the front row's data spans this fraction of the width (line ends inside the frame)
FRONT_Y = 0.955                # its baseline (frame heights from the top)
ADECAY = 0.3                   # older rows flatten by this much at the back
HIST_HZ = 30.0

# P layout (floats)
#  0 rows (float) | 1 R (rows drawn, int) | 2 phi | 3 Z0 | 4 dz | 5 focal | 6 camera height c | 7 horizon y
#  8 ridge height A (world) | 9 (unused) | 10 camera x | 11 band scale | 12 floor | 13 1/(1-floor) | 14 px
#  15 line width | 16 glow radius | 17 glow | 18 fill | 19 mesh | 20 palette period | 21 colour phase | 22 gain
#  23 mesh spacing (bands) | 24..35 palette | OAMP.. per-row height factor | OHS.. per-row glow scale
#  NS.. walk start per internal pixel row
_SRC = HELPERS + PALETTE_CUDA + r"""
#define NB %(nb)d
#define NH %(nh)d
#define MAXIT %(maxit)d
#define OKS %(ns)d
#define OAMP %(oamp)d
#define OHS %(ohs)d

// the palette folded to its bright side: a dark phase keeps its hue but still glows
__device__ __forceinline__ float3 st_pal(float t, const float* __restrict__ PAL) {
    const float3 c = pal(t, PAL);
    const float m = fmaxf(c.x, fmaxf(c.y, c.z));
    const float k = 0.95f / fmaxf(m, 0.35f);
    return make_float3(fminf(c.x * k, 1.f), fminf(c.y * k, 1.f), fminf(c.z * k, 1.f));
}

// taper the data to flat at both ends (band units)
__device__ __forceinline__ float st_win(float b) {
    return smooth(-0.5f, 3.f, b) * smooth(-0.5f, 3.f, (float)(NB - 1) - b);
}

// Uniform cubic B-spline through one history row's floor-cut band values at band position b (centres at integers).
// A convex combination of 4 samples: never above the row's largest value (the host's walk bound relies on it).
// NaN samples read as 0 (fmaxf).  dv = d value / d band.
__device__ __forceinline__ float st_ridge(const float* __restrict__ Hr, float b, float fl, float inv, float& dv) {
    const float bc = fminf(fmaxf(b, 0.f), (float)(NB - 1) - 1e-3f);
    const int i1 = (int)bc;
    const float t = bc - (float)i1;
    const float p0 = fminf(fmaxf(__ldg(Hr + max(i1 - 1, 0)) - fl, 0.f) * inv, 1.f);
    const float p1 = fminf(fmaxf(__ldg(Hr + i1) - fl, 0.f) * inv, 1.f);
    const float p2 = fminf(fmaxf(__ldg(Hr + min(i1 + 1, NB - 1)) - fl, 0.f) * inv, 1.f);
    const float p3 = fminf(fmaxf(__ldg(Hr + min(i1 + 2, NB - 1)) - fl, 0.f) * inv, 1.f);
    const float u = 1.f - t, t2 = t * t, t3 = t2 * t;
    dv = (-u * u * p0 + (3.f * t2 - 4.f * t) * p1 + (-3.f * t2 + 2.f * t + 1.f) * p2 + t2 * p3) * 0.5f;
    return (u * u * u * p0 + (3.f * t3 - 6.f * t2 + 4.f) * p1 + (-3.f * t3 + 3.f * t2 + 3.f * t + 1.f) * p2
            + t3 * p3) * (1.f / 6.f);
}

__device__ __forceinline__ float st_tone(float c) {      // linear to 0.6, then a soft shoulder to 1
    return c < 0.6f ? c : 0.6f + 0.4f * (1.f - __expf(-(c - 0.6f) / 0.4f));
}

extern "C" __global__
void st_draw(uchar4* __restrict__ ov, const int w, const int h, const float* __restrict__ hist, const int row0,
             const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const int R = (int)P[1];
    const int ks = (int)P[OKS + y];
    if (ks >= R) { ov[y * w + x] = make_uchar4(0, 0, 0, 0); return; }
    const float X = (x + 0.5f - 0.5f * w) / (float)h, Y = (y + 0.5f) / (float)h;    // frame heights, y down
    const float rows = P[0], phi = P[2], Z0 = P[3], dz = P[4], f = P[5], c = P[6], Yh = P[7], A = P[8];
    const float ex = P[10], bs = P[11], fl = P[12], inv = P[13], px = P[14], lw0 = P[15];
    const float gr0 = P[16], glow = P[17], fill = P[18], mesh = P[19], period = P[20], phase = P[21];
    const float gain = P[22], mg = P[23];
    const float* PAL = P + 24;
    const float fc = f * c, invf = 1.f / f, bmid = 0.5f * (float)(NB - 1), ib = 1.f / (float)(NB - 1);
    const bool meshon = mesh > 0.001f;
    float T = 1.f, er = 0.f, eg = 0.f, eb = 0.f, ab = 0.f;
    // the ridge in front of the walk's first (the mesh strip under a ridge runs down to it)
    float pZ = Z0 + ((float)(ks - 1) + phi) * dz;
    float pYc = Yh + fc / pZ;
    if (meshon && ks >= 1) {
        const float b = (X * pZ * invf + ex) * bs + bmid;
        float dv;
        const float v = st_ridge(hist + ((row0 - (ks - 1)) & (NH - 1)) * NB, b, fl, inv, dv) * st_win(b);
        pYc -= f * A * P[OAMP + ks - 1] * v / pZ;
    }
    for (int it = 0; it < MAXIT; ++it) {
        const int k = ks + it;
        if (k >= R) break;
        const float pk = (float)k + phi, q = pk / rows;
        if (q >= 1.f) break;
        const float Z = Z0 + pk * dz, iz = 1.f / Z;
        const float Yb = Yh + fc * iz;
        const float b = (X * Z * invf + ex) * bs + bmid;
        const float le = smooth(-3.5f, -0.5f, b) * smooth(-3.5f, -0.5f, (float)(NB - 1) - b);  // flat line ends
        if (le <= 0.f) { pYc = Yb; pZ = Z; continue; }
        float dv;
        const float wv = st_win(b);
        const float v = st_ridge(hist + ((row0 - k) & (NH - 1)) * NB, b, fl, inv, dv) * wv;
        dv *= wv;
        const float Ak = A * P[OAMP + k];
        const float Yc = Yb - f * Ak * v * iz;                // crest
        const float sl = Ak * dv * bs;                        // dYc/dX (depth cancels)
        const float dy = Y - Yc;                              // + below the crest (inside the ridge)
        const float d = dy * rsqrtf(1.f + sl * sl);
        // fades: in over the first 1.5 rows, out over the back 28 %%, dimmer with depth, and where rows crowd
        const float ff = smooth(0.f, 1.5f, pk);
        const float fb = 1.f - smooth(0.72f, 1.f, q);
        const float sp = fc * dz * iz * iz;                   // screen spacing of the rows here
        const float dens = smooth(1.2f * px, 4.5f * px, sp);
        const float fd = ff * fb * (1.f - 0.55f * q) * le;
        const float sh = 1.f - 0.4f * q;
        const float lw = lw0 * sh, gr = fmaxf(gr0 * sh * P[OHS + k], 1e-5f);
        const float hr = 3.2f * gr + 3.f * lw;                // reach above the crest (= the host's walk bound)
        const float hb0 = 0.22f + 0.23f * mesh;                // a wireframe lights its flat wires too
        const float hb = hb0 + (1.f - hb0) * smooth(0.f, 0.45f, v);
        const float core = __expf(-(d * d) / (lw * lw));
        const float halo = __expf(-fabsf(d) / gr) * glow * 0.32f * (1.f - smooth(0.45f * hr, hr, -dy));
        const float3 col = st_pal(b * ib * period + phase + 0.2f * q, PAL);
        const float lv = fd * hb * dens;
        const float kc = (core + halo) * lv, kw = 0.3f * core * lv;
        er += T * (kc * col.x + kw);
        eg += T * (kc * col.y + kw);
        eb += T * (kc * col.z + kw);
        const float cov = clamp01(0.5f + d / px) * ff * le;
        if (cov > 0.f) {
            // the filled silhouette: dark translucent glass over the terrain's surface (down to the ridge in front;
            // under the front ridge it fades out over a short skirt)
            const float body = 1.f - smooth(sp, sp + 0.03f, Y - Yb);
            const float aa = cov * body * fill * (1.f - smooth(0.05f, 0.95f, q));   // thins out toward the back
            ab += T * aa;
            const float slen = 0.3f * f * Ak * iz + 2.f * px;
            const float shn = aa * 0.35f * __expf(-fmaxf(dy, 0.f) / slen) * hb * dens;
            er += T * shn * col.x; eg += T * shn * col.y; eb += T * shn * col.z;
            if (meshon) {
                // the wireframe's columns across the strip from this crest down to the ridge in front
                const float tt = clamp01((pYc - Y) / fmaxf(pYc - Yc, 1e-6f));
                const float Zs = pZ + (Z - pZ) * tt;
                const float bm = (X * Zs * invf + ex) * bs + bmid;
                const float g = bm / mg;
                const float colsp = mg / bs * f / Zs;         // screen spacing of the columns
                const float dX = fabsf(g - rintf(g)) * colsp;
                const float lem = smooth(-0.5f, 0.5f, bm) * smooth(-0.5f, 0.5f, (float)(NB - 1) - bm);
                const float strip = 1.f - smooth(pYc - px, pYc + px, Y);
                // as bright as the ridge wires at mesh 1 (a wireframe), with a little of their halo
                const float wm = __expf(-(dX * dX) / (1.2f * lw * lw));
                const float mk = mesh * cov * fd * dens * lem * strip * smooth(1.5f * px, 5.f * px, colsp)
                                 * (0.45f + 0.55f * hb);
                const float mc = (wm + __expf(-dX / gr) * glow * 0.2f) * mk, mw = 0.3f * wm * mk;
                er += T * (mc * col.x + mw); eg += T * (mc * col.y + mw); eb += T * (mc * col.z + mw);
            }
            T *= 1.f - cov;
            if (T < 0.002f) break;
        }
        pYc = Yc; pZ = Z;
    }
    const float a = clamp01(ab);
    ov[y * w + x] = make_uchar4((unsigned char)(st_tone(er * gain) * 255.f + 0.5f),
                                (unsigned char)(st_tone(eg * gain) * 255.f + 0.5f),
                                (unsigned char)(st_tone(eb * gain) * 255.f + 0.5f),
                                (unsigned char)(a * 255.f + 0.5f));
}

// full resolution: fill absorption, then a screen blend of the glow (sRGB), both scaled by k = amount x opacity
extern "C" __global__
void st_comp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const uchar4* __restrict__ ov,
             const int W, const int H, const int w, const int h, const float k)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const size_t i = (size_t)y * W + x;
    const float fx = (x + 0.5f) * (float)w / (float)W - 0.5f, fy = (y + 0.5f) * (float)h / (float)H - 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0;
    const int xa = min(max(x0, 0), w - 1), xb = min(max(x0 + 1, 0), w - 1);
    const int ya = min(max(y0, 0), h - 1), yb = min(max(y0 + 1, 0), h - 1);
    const unsigned int* ou = reinterpret_cast<const unsigned int*>(ov);
    const unsigned int ra = ou[ya * w + xa], rb = ou[ya * w + xb], rc = ou[yb * w + xa], rd = ou[yb * w + xb];
    if ((ra | rb | rc | rd) == 0u) { out[i] = src[i]; return; }
    const uchar4 A = ov[ya * w + xa], B = ov[ya * w + xb], C = ov[yb * w + xa], D = ov[yb * w + xb];
    const float u0 = (1.f - tx) * (1.f - ty), u1 = tx * (1.f - ty), u2 = (1.f - tx) * ty, u3 = tx * ty;
    const float kr = k * (1.f / 255.f);
    const float gr_ = (u0 * A.x + u1 * B.x + u2 * C.x + u3 * D.x) * kr;
    const float gg_ = (u0 * A.y + u1 * B.y + u2 * C.y + u3 * D.y) * kr;
    const float gb_ = (u0 * A.z + u1 * B.z + u2 * C.z + u3 * D.z) * kr;
    const float ka = (u0 * A.w + u1 * B.w + u2 * C.w + u3 * D.w) * kr;
    const uchar4 sp = src[i];
    float r = sp.x * (1.f - ka), g = sp.y * (1.f - ka), b = sp.z * (1.f - ka);
    r += gr_ * (255.f - r); g += gg_ * (255.f - g); b += gb_ * (255.f - b);
    out[i] = pack(make_float4(r, g, b, 255.f));
}
""" % {"nb": NB, "nh": NH, "maxit": MAXIT, "ns": NS, "oamp": OAMP, "ohs": OHS}
_OPTIONS = ("--std=c++14", "--use_fast_math")


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if not (v >= lo) else hi if v > hi else v          # NaN -> lo


def _num(v, lo: float, hi: float, default: float) -> float:
    """A param as a finite float in [lo, hi]; NaN / inf / junk -> default (a bound signal can't poison state)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return _clamp(v, lo, hi) if math.isfinite(v) else default


class SpectrumTerrain3D(Effect):
    name = "spectrum_terrain_3d"
    stage = "overlay"
    label = "Spectrum terrain 3D"
    description = ("The room's FFT history as glowing ridge lines receding in perspective over the scene, "
                   "'Unknown Pleasures' style, each ridge hiding the ones behind it; morphs into a wireframe terrain.")
    listens = ("fft",)
    params = {
        **standard_effect_params(),
        "rows": num(24.0, 96.0, 60.0, "Rows (history drawn)", step=1.0, tier="regional"),
        "depth": num(0.3, 1.6, 1.0, "Depth (row spacing)", tier="regional"),
        "height": num(0.04, 0.4, 0.17, "Ridge height", tier="regional"),
        "tilt": num(0.0, 1.0, 0.45, "Camera tilt (grazing -> overhead)", tier="regional", basic=True),
        "mesh": num(0.0, 1.0, 0.0, "Mesh (ridges -> wireframe)", tier="regional", basic=True),
        "fill": num(0.0, 1.0, 0.5, "Fill (dark glass over the terrain)", tier="regional"),
        "opacity": num(0.1, 1.0, 0.85, "Opacity", tier="regional"),
        "glow": num(0.0, 1.5, 0.7, "Glow", tier="regional"),
        "drift": num(0.0, 1.0, 0.35, "Camera drift", tier="regional"),
        "floor": num(0.0, 0.6, 0.15, "Noise floor", tier="regional"),
        "hue": num(0.0, 0.5, 0.0, "Hue shift (up to half a turn)", tier="regional"),
        **palette_params("ice", period=0.8, drift=0.015),
        "render_scale": num(0.25, 1.0, 0.5, "Render scale", step=0.05, tier="quality"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC, _OPTIONS)
        self._kd = mod.get_function("st_draw")
        self._kc = mod.get_function("st_comp")
        self.w, self.h = internal_size(ctx, float(params.get("render_scale", 0.5)))
        self.layer = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.P = cp.zeros(NS + self.h, cp.float32)
        self.host = np.zeros(NS + self.h, np.float32)
        self._negY = -((np.arange(self.h, dtype=np.float64) + 0.5) / self.h)      # -Y per internal pixel row
        self._kr = np.arange(MAXROWS, dtype=np.float64)
        self._ki = np.arange(MAXROWS, dtype=np.int64)
        self._idx = np.zeros(MAXROWS, np.int64)
        self._rm = np.zeros(NH, np.float32)             # each history row's max
        self._vm = np.zeros(MAXROWS, np.float32)        # ... of the rows drawn, floor-cut
        self._z = np.zeros(MAXROWS)
        self._q = np.zeros(MAXROWS)
        self._a = np.zeros(MAXROWS)
        self._tmp = np.zeros(MAXROWS)
        self._top = np.zeros(MAXROWS)
        self._hs = np.zeros(MAXROWS)
        self._row = None
        self.phi = 0.0
        self.pres = None
        self.level = 0.0
        self.drift = 0.0
        self.cam = 0.0

    # -- host side ----------------------------------------------------------------------------------------------
    def _palette(self, name: str) -> np.ndarray:
        mod = getattr(self, "modulation", None)
        rows = None
        if mod is not None:
            try:
                rows = mod.palette_rows()
            except Exception:
                rows = None
        return np.asarray(PALETTES.get(name, PALETTES["ice"]) if rows is None else rows, np.float32).reshape(-1)[:12]

    def _listen(self, dt: float, floor: float):
        """Read the sound; step phi (sub-row scroll), presence and level.  Returns (hist_gpu, host hist, row)."""
        s = getattr(self, "sound", None)
        hist_gpu = hist = None
        row = 0
        lvl = 0.0
        if s is not None:
            try:
                hist_gpu, hist, row, lvl = s.fft_history_gpu, s.fft_history, int(s.fft_row), float(s.level)
            except Exception:
                hist_gpu = hist = None
        if hist is None or hist_gpu is None or getattr(hist, "shape", None) != (NH, NB):
            hist_gpu = hist = None
        target = 0.0
        if hist is not None:
            np.fmax.reduce(hist, axis=1, out=self._rm)
            top = float(np.fmax.reduce(self._rm))
            target = 1.0 if top > floor + 0.03 else 0.0
            row %= NH
            if self._row is None:
                self._row, self.phi = row, 0.0
            else:
                n = (row - self._row) % NH
                self.phi = _clamp(self.phi + dt * HIST_HZ - n, 0.0, 1.0)
                self._row = row
        if self.pres is None:               # the first frame: the engine's amount fade does the bringing in
            self.pres = target
        else:
            tau = 0.3 if target > self.pres else 1.2
            self.pres += (target - self.pres) * (1.0 - math.exp(-dt / tau))
        if not math.isfinite(lvl):
            lvl = 0.0
        lvl = _clamp(lvl, 0.0, 1.0)
        self.level += (lvl - self.level) * (1.0 - math.exp(-dt / (0.06 if lvl > self.level else 0.5)))
        return hist_gpu, hist, row

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        dtc = _clamp(float(dt), 0.0, 1.0)
        floor = _num(p["floor"], 0.0, 0.95, 0.15)
        self.drift = math.fmod(self.drift + _num(p["drift_speed"], -2.0, 2.0, 0.0) * dtc, 1000.0)
        self.cam = math.fmod(self.cam + dtc, 41.0 * 59.0)
        hist_gpu, hist, row = self._listen(dtc, floor)
        k = _num(p["amount"], 0.0, 1.0, 0.0) * _num(p["opacity"], 0.0, 1.0, 0.85)
        if k <= 1e-4 or hist_gpu is None or self.pres < 1e-3:          # nothing to show: the scene, untouched
            cp.copyto(out, src)
            return

        W, H = self.ctx.width, self.ctx.height
        aspect = W / H
        pulse = _num(p["pulse"], 0.0, 1.0, 0.0)
        rows = _num(p["rows"], 24.0, float(MAXROWS), 60.0)
        R = min(MAXROWS, max(1, int(math.ceil(rows - 1e-3))))
        depth = _num(p["depth"], 0.3, 1.6, 1.0)
        tilt = _num(p["tilt"], 0.0, 1.0, 0.45)
        drift = _num(p["drift"], 0.0, 1.0, 0.35)
        glow = _num(p["glow"], 0.0, 1.5, 0.7)
        inv = 1.0 / max(1.0 - floor, 1e-3)
        px = 1.0 / self.h
        f = 1.0
        Z0 = f / (0.5 * aspect * FRONT_SPAN)               # the front row (world half width 1) spans FRONT_SPAN
        dz = Z0 * 0.025 * depth
        zfar = Z0 + rows * dz
        spread = 0.18 + 0.42 * tilt                         # baselines' spread on screen, front to back
        c0 = spread / (f * (1.0 / Z0 - 1.0 / zfar))
        yh = FRONT_Y - f * c0 / Z0                          # horizon (the drift bobs the camera around it)
        c = c0 * (1.0 + 0.08 * drift * math.sin(2.0 * math.pi * self.cam / 59.0 + 0.4))
        ex = 0.2 * drift * math.sin(2.0 * math.pi * self.cam / 41.0 + 1.1)
        lw0 = 0.75 * px + 0.0005
        gr0 = 0.0022 + 0.0055 * glow

        # rows drawn: depth, age, flattening, and each one's highest possible crest (+ glow reach)
        kr = self._kr[:R]
        z, q, a, tmp, top = self._z[:R], self._q[:R], self._a[:R], self._tmp[:R], self._top[:R]
        np.add(kr, self.phi, out=q)
        np.multiply(q, dz, out=z)
        z += Z0
        q /= rows
        np.multiply(q, -ADECAY, out=a)
        a += 1.0                                            # per-row height factor (1 - decay q)
        sh = 1.0 - 0.4 * q
        A = _num(p["height"], 0.04, 0.4, 0.17) * Z0 / f
        # the walk budget: a reach r above row k's baseline spans the rows out to depth 1 / (1/Z - r/(f c)); keep
        # that under WALK_CAP rows -> r <= f c (1/Z - 1/(Z + WALK_CAP dz)).  The glow takes at most 40 % of it
        # (far, crowded rows lose their halo first), the height the rest (then they flatten).
        np.add(z, WALK_CAP * dz, out=tmp)
        np.reciprocal(tmp, out=tmp)
        budget = f * c * (1.0 / z - tmp)                    # screen reach allowed above each baseline
        lws = 3.0 * lw0 * sh
        hs = self._hs[:R]
        np.clip((0.4 * budget - lws) / (3.2 * gr0 * sh), 0.0, 1.0, out=hs)
        hr = 3.2 * gr0 * sh * hs + lws                      # glow reach, as the kernel
        amax = (budget - hr) * z / (f * max(A, 1e-6))
        np.minimum(a, amax, out=a)
        np.clip(a, 0.0, 1.0, out=a)
        idx = self._idx[:R]
        np.subtract(row, self._ki[:R], out=idx)
        np.bitwise_and(idx, NH - 1, out=idx)
        vm = self._vm[:R]
        np.take(self._rm, idx, out=vm)
        vm -= floor
        vm *= inv
        np.nan_to_num(vm, copy=False, nan=0.0, posinf=1.0, neginf=0.0)
        np.clip(vm, 0.0, 1.0, out=vm)
        np.multiply(a, vm, out=top)
        top *= -A * f
        top += f * c
        top /= z
        top += yh
        top -= hr
        top[q >= 1.0] = np.inf
        np.fmin.accumulate(top, out=top)
        ks = np.searchsorted(-top, self._negY, side="left")     # first ridge that can reach each pixel row

        hst = self.host
        gain = self.pres * (0.7 + 0.5 * self.level) * (1.0 + 0.45 * pulse)
        hst[0] = rows
        hst[1] = R
        hst[2] = self.phi
        hst[3] = Z0
        hst[4] = dz
        hst[5] = f
        hst[6] = c
        hst[7] = yh
        hst[8] = A
        hst[9] = 0.0
        hst[10] = ex
        hst[11] = 0.5 * (NB - 1)                            # band units per world unit (half width 1)
        hst[12] = floor
        hst[13] = inv
        hst[14] = px
        hst[15] = lw0
        hst[16] = gr0
        hst[17] = glow
        hst[18] = _num(p["fill"], 0.0, 1.0, 0.5) * 0.85 * self.pres
        hst[19] = _num(p["mesh"], 0.0, 1.0, 0.0)
        hst[20] = _num(p["palette_period"], 0.05, 10.0, 0.8)
        hst[21] = self.drift + _num(p["hue"], 0.0, 0.5, 0.0)
        hst[22] = gain
        hst[23] = 2.0
        hst[24:36] = self._palette(p["palette"])
        hst[OAMP:OAMP + R] = a
        hst[OHS:OHS + R] = hs
        hst[NS:NS + self.h] = ks
        self.P.set(hst)

        launch_2d(self._kd, self.w, self.h, (self.layer, np.int32(self.w), np.int32(self.h), hist_gpu,
                                             np.int32(row), self.P))
        launch_2d(self._kc, W, H, (src, out, self.layer, np.int32(W), np.int32(H), np.int32(self.w),
                                   np.int32(self.h), np.float32(k)))

    def close(self) -> None:
        self.layer = None
        self.P = None
