"""Hex grid reveal: the frame tiles into hexagons (morphable to circles, diamonds or drifting Voronoi cells) and
each cell switches from the old picture to the new one: the old patch shrinks to a point while the new one blooms
out around it, cell by cell in a diagonal, radial, random or luminance order, with palette-glowing edges while a
cell is switching and a per-ring zoom offset on the incoming patch.  Content stays full resolution inside each
shaped cell (unlike mosaic / pixelate).

Source: https://godotshaders.com/shader/hex-grid-transition-shader/
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/ZoomInCircles.glsl
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/PolkaDotsCurtain.glsl
        https://godotshaders.com/shader/moving-voronoi/
Author: muzi1983 (godotshaders hex grid transition); dycm8009 (gl-transitions ZoomInCircles); bobylito (PolkaDotsCurtain); HalconVengador (godotshaders moving Voronoi)
License: CC0-1.0 + MIT   (godotshaders pages: CC0; gl-transitions: MIT)
license_class: permissive
Ported by smfx (ideas/changes: the hex grid transition's per-cell local progress (clamp((p - delay) / window))
  over a hex tiling, ZoomInCircles' per-ring zoom offsets and PolkaDotsCurtain's growing dots are rebuilt as one
  generalised-Voronoi kernel.  Sites sit on a staggered lattice whose row pitch morphs from sqrt(3)/2 (hexagons)
  to 1/2 (diamonds), and gain hashed jitter plus a slow drift (moving Voronoi) at the far end of the shape axis;
  a site pass computes each cell's position, order value and ring once per frame (luminance order captures a
  coarse luma of the old picture at the first frame so cells never switch back).  Per pixel: nearest site, then
  the normalised polygon distance max_n 2 (p-c).(n-c) / |n-c|^2 (its level sets are scaled copies of the cell),
  mixed with a radial distance for the circle shape.  Each cell's old patch shrinks (optionally squeezing its
  content into a miniature) to a point while the new patch grows behind it from the centre at a per-ring zoom
  that relaxes to identity; the gap between them is a dimmed blend; old edge, new edge and cell border glow in
  two palette tones (screen blend), weighted by sin(pi t) per cell and sin(pi p) overall.  Colour drifts with
  progress * duration_s; p <= 1e-4 copies a, p >= 0.9999 copies b, and idle / finished cells are exact copies.)

MIT notice (gl-transitions): Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following
conditions: The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num

_CELL_MIN = 0.03          # smallest cell pitch (fraction of frame height): sizes the site grid
_SQ3_2 = math.sqrt(3.0) / 2.0

_SRC = HELPERS + PALETTE_CUDA + r"""
// Per-frame site pass: position (with Voronoi jitter + drift), order value, ring.
extern "C" __global__
void hgr_sites(const uchar4* __restrict__ a, float4* __restrict__ sites, float* __restrict__ lum,
               const int W, const int H, const int NC, const int NR, const int ci, const int cj,
               const float s, const float ky, const float vw, const float tau,
               const float dx, const float dy, const float w0, const float w1, const float w2, const float w3,
               const float scatter, const unsigned int seed, const int capture)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= NC || j >= NR) return;
    const int id = j * NC + i;
    const float sh = ((j - cj) & 1) ? 0.5f : 0.f;
    const float bx = 0.5f * W + ((float)(i - ci) + sh) * s;
    const float by = 0.5f * H + (float)(j - cj) * ky * s;
    const float r1 = hash1(id, seed, 1u), r2 = hash1(id, seed, 2u), r3 = hash1(id, seed, 3u);
    const float r4 = hash1(id, seed, 4u), r5 = hash1(id, seed, 5u), r6 = hash1(id, seed, 6u);
    const float r7 = hash1(id, seed, 7u);
    // moving Voronoi: hashed jitter plus a slow drift, both scaled by the Voronoi weight
    const float jx = (r1 - 0.5f) * 0.5f + 0.15f * sinf(tau * (0.7f + 0.6f * r3) + 6.2831853f * r4);
    const float jy = (r2 - 0.5f) * 0.5f + 0.15f * cosf(tau * (0.6f + 0.7f * r5) + 6.2831853f * r6);
    const float x = bx + vw * s * jx, y = by + vw * s * jy;

    if (capture) {                                   // coarse luma of the old picture around the cell
        const float rr = 0.28f * s;
        const int X0 = (int)bx, Y0 = (int)by, R = (int)rr;
        float L = luma(ld(a, X0, Y0, W, H)) * 2.f;
        L += luma(ld(a, X0 + R, Y0, W, H)) + luma(ld(a, X0 - R, Y0, W, H));
        L += luma(ld(a, X0, Y0 + R, W, H)) + luma(ld(a, X0, Y0 - R, W, H));
        L += luma(ld(a, X0 + R, Y0 + R, W, H)) + luma(ld(a, X0 - R, Y0 - R, W, H));
        lum[id] = L * (1.f / 8.f);
    }
    const float cx = bx - 0.5f * W, cy = by - 0.5f * H;
    const float o_diag = clamp01(0.5f + (cx * dx + cy * dy) / (fabsf(W * dx) + fabsf(H * dy) + 1e-3f));
    const float o_rad = clamp01(sqrtf(cx * cx + cy * cy) / (0.5f * sqrtf((float)W * W + (float)H * H)));
    const float o_lum = clamp01(1.f - lum[id]);                     // bright cells first
    float o = w0 * o_diag + w1 * o_rad + w2 * r7 + w3 * o_lum;
    o = clamp01(o + scatter * (r7 - o) * (1.f - w2));
    const float ring = floorf(sqrtf(cx * cx + cy * cy) / s + 0.5f);
    sites[id] = make_float4(x, y, o, ring);
}

extern "C" __global__
void hgr_main(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
              const float4* __restrict__ sites, const float* __restrict__ P,
              const int W, const int H, const int NC, const int NR, const int ci, const int cj,
              const float s, const float ky, const float p, const float win,
              const float circ, const float R0, const float aa, const float inr,
              const float squeeze, const float zoomk, const float glow, const float env,
              const float drift, const float period, const float wl, const float wh)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int idx = y * W + x;
    const float px = x + 0.5f, py = y + 0.5f;
    const int jr = (int)floorf((py - 0.5f * H) / (ky * s) + (float)cj + 0.5f);
    float best = 1e30f;
    float4 C = make_float4(px, py, 0.f, 0.f);
    int bi = -1;
    for (int dj = -2; dj <= 2; ++dj) {
        const int j = jr + dj;
        if (j < 0 || j >= NR) continue;
        const float sh = ((j - cj) & 1) ? 0.5f : 0.f;
        const int ir = (int)floorf((px - 0.5f * W) / s + (float)ci - sh + 0.5f);
        for (int di = -1; di <= 1; ++di) {
            const int i = ir + di;
            if (i < 0 || i >= NC) continue;
            const float4 S = sites[j * NC + i];
            const float ex = px - S.x, ey = py - S.y;
            const float d2 = ex * ex + ey * ey;
            if (d2 < best) { best = d2; C = S; bi = j * NC + i; }
        }
    }
    const float t = clamp01((p - C.z * (1.f - win)) / win);
    if (t <= 0.f) { out[idx] = a[idx]; return; }
    if (t >= 1.f) { out[idx] = b[idx]; return; }

    // normalised polygon distance: 0 at the site, 1 on the cell border
    const float ox = px - C.x, oy = py - C.y;
    float f = 0.f;
    for (int dj = -2; dj <= 2; ++dj) {
        const int j = jr + dj;
        if (j < 0 || j >= NR) continue;
        const float sh = ((j - cj) & 1) ? 0.5f : 0.f;
        const int ir = (int)floorf((px - 0.5f * W) / s + (float)ci - sh + 0.5f);
        for (int di = -1; di <= 1; ++di) {
            const int i = ir + di;
            if (i < 0 || i >= NC || j * NC + i == bi) continue;
            const float4 S = sites[j * NC + i];
            const float nx = S.x - C.x, ny = S.y - C.y;
            const float L2 = nx * nx + ny * ny;
            if (L2 < 1e-4f) continue;
            f = fmaxf(f, 2.f * (ox * nx + oy * ny) / L2);
        }
    }
    const float dc = sqrtf(ox * ox + oy * oy) / inr;
    const float d = f + (dc - f) * circ;

    const float eo = smooth(0.f, 0.62f, t), en = smooth(0.30f, 1.f, t);
    const float ro = R0 * (1.f - eo), rn = R0 * en;
    const float mo = (1.f - smooth(ro - aa, ro + aa, d)) * clamp01(ro / aa);
    const float mn = (1.f - smooth(rn - aa, rn + aa, d)) * clamp01(rn / aa);

    const float4 ca = ld(a, x, y, W, H), cb = ld(b, x, y, W, H);
    float4 col = mix4(ca, cb, t);
    col = make_float4(col.x * 0.26f, col.y * 0.26f, col.z * 0.26f, 255.f);       // the gap: a dimmed blend
    if (mn > 0.f) {
        const bool odd = ((int)C.w) & 1;
        const float zf = fmaxf(1.f + zoomk * (odd ? 1.f : -0.6f) * (1.f - en) * (1.f - en), 0.2f);
        col = mix4(col, bilin(b, C.x + ox * zf, C.y + oy * zf, W, H), mn);
    }
    if (mo > 0.f) {
        const float sq = 1.f + (fmaxf(1.f - eo, 0.03f) - 1.f) * squeeze;
        col = mix4(col, bilin(a, C.x + ox / sq, C.y + oy / sq, W, H), mo);
    }

    // palette glow on the old edge, the new edge (only where it shows) and the cell border
    const float g = glow * __sinf(3.14159265f * t) * (0.55f + 0.45f * env);
    if (g > 1e-4f) {
        const float dpo = fabsf(d - ro) * inr, dpn = fabsf(d - rn) * inr, dpb = fmaxf(1.f - f, 0.f) * inr;
        const float lo = (__expf(-dpo / wl) + 0.45f * __expf(-dpo / wh)) * clamp01(ro / aa);
        const float ln = (__expf(-dpn / wl) + 0.45f * __expf(-dpn / wh)) * clamp01(rn / aa) * (1.f - mo);
        const float lb = 0.8f * __expf(-dpb / wl) + 0.35f * __expf(-dpb / wh);
        const float gap = (1.f - mo) * (1.f - mn);
        const float k = C.z * period + drift;
        const float3 c1 = pal(k, P), c2 = pal(k + 0.33f, P);
        const float u1 = g * (lo + lb + 0.22f * gap), u2 = g * (ln + 0.12f * gap);
        const float gx = fminf(c1.x * u1 + c2.x * u2, 1.f);
        const float gy = fminf(c1.y * u1 + c2.y * u2, 1.f);
        const float gz = fminf(c1.z * u1 + c2.z * u2, 1.f);
        col = make_float4(255.f - (255.f - col.x) * (1.f - gx), 255.f - (255.f - col.y) * (1.f - gy),
                          255.f - (255.f - col.z) * (1.f - gz), 255.f);
    }
    out[idx] = pack(col);
}
"""


def _seg(v: float, lo: float) -> float:
    return min(max(v - lo, 0.0), 1.0)


class HexGridReveal(Transition):
    name = "hex_grid_reveal"
    label = "Hex grid reveal"
    description = ("The frame tiles into hexagons (or circles, diamonds, drifting Voronoi cells); each cell's old "
                   "patch shrinks to a point as the new one blooms out, in a diagonal, radial, random or luminance "
                   "order, with palette-glowing edges.")
    default_duration_s = 2.0
    params = {
        "cell_size": num(_CELL_MIN, 0.3, 0.11, "Cell size", unit="of height", tier="regional", basic=True),
        "shape_morph": num(0.0, 3.0, 0.0, "Shape (hex / circle / diamond / Voronoi)", tier="regional", basic=True),
        "order": num(0.0, 3.0, 0.0, "Order (diagonal / radial / random / luma)", tier="regional"),
        "direction": num(0.0, 360.0, 35.0, "Sweep direction", step=1.0, unit="deg", tier="regional"),
        "scatter": num(0.0, 1.0, 0.15, "Order scatter", tier="regional"),
        "window": num(0.1, 1.0, 0.42, "Switch length per cell", tier="regional"),
        "squeeze": num(0.0, 1.0, 0.75, "Old patch squeeze", tier="regional"),
        "ring_zoom": num(0.0, 1.0, 0.35, "Per-ring zoom", tier="regional"),
        "edge_glow": num(0.0, 1.0, 0.6, "Edge glow", tier="regional"),
        "softness": num(0.0, 1.0, 0.06, "Softness", tier="regional"),
        "palette": enum(list(PALETTES), "neon", "Glow palette", tier="regional"),
        "palette_period": num(0.1, 3.0, 0.8, "Palette period", tier="regional"),
        "colour_drift": num(0.0, 1.0, 0.12, "Colour drift", unit="/s", tier="regional"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        w, h = ctx.width, ctx.height
        mod = raw_module(_SRC)
        self._k_sites = mod.get_function("hgr_sites")
        self._k_main = mod.get_function("hgr_main")
        s_min = max(_CELL_MIN * h, 4.0)
        self._nc = int(math.ceil(w / s_min)) + 6
        nr = int(math.ceil(h / (0.5 * s_min))) + 8
        self._nr = nr + (nr % 2)
        self._ci, self._cj = self._nc // 2, (self._nr // 2) & ~1
        self._sites = cp.zeros(self._nc * self._nr * 4, dtype=cp.float32)
        self._lum = cp.full(self._nc * self._nr, 0.5, dtype=cp.float32)
        self._P = cp.zeros(12, dtype=cp.float32)
        self._pal = None
        s = int(self.params.get("seed", -1))
        self._seed = int(np.random.default_rng().integers(0, 2**31)) if s < 0 else s
        self._seed_u = np.uint32((self._seed * 2654435761) & 0xFFFFFFFF)
        self._drift0 = (self._seed % 997) / 997.0
        self._captured = False
        self._last_p = 0.0

    def _sync_palette(self) -> None:
        name = self.params.get("palette", "neon")
        if name not in PALETTES:
            name = "neon"
        if name != self._pal:
            self._P.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal = name

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        q = float(progress)
        if q <= 1e-4:
            cp.copyto(out, a)
            self._last_p = q
            return
        if q >= 0.9999:
            cp.copyto(out, b)
            self._last_p = q
            return
        p = self.params
        self._sync_palette()
        w, h = self.ctx.width, self.ctx.height
        hs = h / 2160.0

        # shape axis: 0 hex -> 1 circle -> 2 diamond -> 3 Voronoi
        sm = min(max(float(p["shape_morph"]), 0.0), 3.0)
        u01, u12, u23 = _seg(sm, 0.0), _seg(sm, 1.0), _seg(sm, 2.0)
        circ = u01 * (1.0 - u12)
        ky = _SQ3_2 + (0.5 - _SQ3_2) * u12
        vw = u23
        s = max(float(p["cell_size"]), _CELL_MIN) * h
        nn = min(1.0, math.sqrt(0.25 + ky * ky)) * s           # nearest-neighbour pitch
        inr = 0.5 * nn
        cr = 2.0 / math.sqrt(3.0) + (math.sqrt(2.0) - 2.0 / math.sqrt(3.0)) * u12
        aa_px = 0.9 * max(hs, 0.5) + float(p["softness"]) * 0.35 * inr
        aa = aa_px / inr
        r0 = 1.0 + (cr - 1.0) * circ + aa + 0.01

        # order weights: 0 diagonal -> 1 radial -> 2 random -> 3 luma
        o = min(max(float(p["order"]), 0.0), 3.0)
        ws = [max(0.0, 1.0 - abs(o - k)) for k in range(4)]
        ang = math.radians(float(p["direction"]))

        capture = (not self._captured) or q < self._last_p - 0.3
        self._captured = True
        self._last_p = q
        tau = q * self.duration_s
        launch_2d(self._k_sites, self._nc, self._nr,
                  (a, self._sites, self._lum, np.int32(w), np.int32(h), np.int32(self._nc), np.int32(self._nr),
                   np.int32(self._ci), np.int32(self._cj), np.float32(s), np.float32(ky), np.float32(vw),
                   np.float32(tau), np.float32(math.cos(ang)), np.float32(math.sin(ang)),
                   np.float32(ws[0]), np.float32(ws[1]), np.float32(ws[2]), np.float32(ws[3]),
                   np.float32(p["scatter"]), self._seed_u, np.int32(1 if capture else 0)))

        glow = float(p["edge_glow"])
        drift = (self._drift0 + float(p["colour_drift"]) * tau) % 1.0
        wl = max(1.2 * hs, 0.6) * (0.7 + 0.8 * glow)
        wh = (0.04 + 0.08 * glow) * inr
        launch_2d(self._k_main, w, h,
                  (a, b, out, self._sites, self._P, np.int32(w), np.int32(h), np.int32(self._nc),
                   np.int32(self._nr), np.int32(self._ci), np.int32(self._cj), np.float32(s), np.float32(ky),
                   np.float32(q), np.float32(p["window"]), np.float32(circ), np.float32(r0), np.float32(aa),
                   np.float32(inr), np.float32(p["squeeze"]), np.float32(0.8 * float(p["ring_zoom"])),
                   np.float32(1.6 * glow), np.float32(math.sin(math.pi * q)), np.float32(drift),
                   np.float32(p["palette_period"]), np.float32(wl), np.float32(wh)))
