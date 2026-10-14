"""Stargate slit-scan: the 2001 Star Gate corridor as a transition.  The old picture collapses into a glowing
centre slit; two perspective planes then rush toward the viewer, each textured slit-scan style -- one slit line of
the old picture (later the new one) smeared along depth into streaming, palette-tinted planes of light; finally the
corridor opens out from the slit onto the new picture.

Source: Original (inspired by https://en.wikipedia.org/wiki/Slit-scan_photography and
        https://derivative.ca/community-post/tutorial/glsl-slit-scan-effect-touchdesigner/74007 -- idea only)
Author: smfx (original; inspired by Douglas Trumbull's slit-scan 'Star Gate'; the TouchDesigner tutorial is an idea only)
License: MIT (original)
license_class: permissive

Geometry (height-normalised screen coords, centre origin, focal length 1): s is the coordinate across the slit
(x for axis=vertical: walls left/right; y for axis=horizontal: floor/ceiling), r the one along it.  The walls sit at
|s3d| = S (the screen's half extent in s) tilted by plane_angle, so a ray hits a wall at depth
z = S / (|s| - tan(angle)): z = 1 at the screen edge (the wall texture there lines up with the picture's rows),
-> infinity at the vanishing slit.  Wall texture: u = r * z (along the slit), v = z + travel (depth, streaming
toward the viewer; travel = speed * progress * duration_s).  Colour at (u, v) = the input's slit column
s_img = 0.5 +- (0.03 + 0.47 * slit_width) * wander(v) at row mirror(0.5 + u / 2R), read from a 1/4 (near) or
1/16 (far) box-downsampled copy, saturated, tinted toward pal(v * period + drift) by colour_smear, and modulated by
streaming light bands along v and fine streaks along u; depth fogs into the glowing far light.  A front of the new
picture's slit streams in from the far end (1/z front) mid-way.  Collapse (progress < 0.2) and open (> 0.74) are
pure squashes of a and b about the slit, so the endpoints are exact (and early-out copies); every extra light
(rims, front ring) is weighted by sin(pi p).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num

_SRC = HELPERS + PALETTE_CUDA + r"""
// 4x4 box downsample (clamped), uchar4 -> uchar4.
extern "C" __global__
void sg_down4(const uchar4* __restrict__ src, uchar4* __restrict__ dst, const int Wi, const int Hi,
              const int Wo, const int Ho)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= Wo || y >= Ho) return;
    float r = 0.f, g = 0.f, b = 0.f;
    #pragma unroll
    for (int j = 0; j < 4; ++j) {
        const int yy = min(y * 4 + j, Hi - 1);
        #pragma unroll
        for (int i = 0; i < 4; ++i) {
            const int xx = min(x * 4 + i, Wi - 1);
            const uchar4 p = src[yy * Wi + xx];
            r += p.x; g += p.y; b += p.z;
        }
    }
    dst[y * Wo + x] = make_uchar4((unsigned char)(r * 0.0625f + 0.5f), (unsigned char)(g * 0.0625f + 0.5f),
                                  (unsigned char)(b * 0.0625f + 0.5f), 255);
}

__device__ __forceinline__ float mirror01(float t) {
    t = t - 2.f * floorf(t * 0.5f);          // [0, 2)
    return t > 1.f ? 2.f - t : t;
}

// Sample a picture at normalised (sx, sy) in [0, 1] from a level (bilinear).
__device__ __forceinline__ float4 samp(const uchar4* __restrict__ img, float sx, float sy, int w, int h) {
    return bilin(img, sx * w, sy * h, w, h);
}

// The squashed picture at screen-normalised position (ix, iy) in [0,1]: full res -> 1/4 -> 1/16 as k shrinks.
__device__ float4 squashed(const uchar4* __restrict__ f, const uchar4* __restrict__ f4, const uchar4* __restrict__ f16,
                           float ix, float iy, float k, int W, int H, int W4, int H4, int W16, int H16)
{
    const float w0 = smooth(0.25f, 0.5f, k);
    const float w16 = 1.f - smooth(0.04f, 0.1f, k);
    float4 c;
    if (w0 >= 1.f) return samp(f, ix, iy, W, H);
    c = samp(f4, ix, iy, W4, H4);
    if (w0 > 0.f) c = mix4(c, samp(f, ix, iy, W, H), w0);
    if (w16 > 0.f) c = mix4(c, samp(f16, ix, iy, W16, H16), w16);
    return c;
}

// One wall sample of a source: slit column s_img (across), row r_img (along), near/far level blend.
__device__ float4 wall(const uchar4* __restrict__ f4, const uchar4* __restrict__ f16, float s_img, float r_img,
                       int vert, float farw, int W4, int H4, int W16, int H16)
{
    const float ix = vert ? s_img : r_img, iy = vert ? r_img : s_img;
    float4 c = make_float4(0.f, 0.f, 0.f, 255.f);
    if (farw < 1.f) c = samp(f4, ix, iy, W4, H4);
    if (farw > 0.f) c = mix4(c, samp(f16, ix, iy, W16, H16), farw);
    return c;
}

extern "C" __global__
void stargate(const uchar4* __restrict__ a, const uchar4* __restrict__ b,
              const uchar4* __restrict__ a4, const uchar4* __restrict__ b4,
              const uchar4* __restrict__ a16, const uchar4* __restrict__ b16,
              uchar4* __restrict__ out, const float* __restrict__ P,
              const int W, const int H, const int W4, const int H4, const int W16, const int H16,
              const int vert, const float ka, const float kb, const float travel, const float m,
              const float slitw, const float smear, const float glow, const float env, const float frontq,
              const float drift, const float period, const float ph1, const float ph2, const float ph3,
              const unsigned int seed, const float ga, const float gb)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float sc = 2.f / H;
    const float px = (x + 0.5f - 0.5f * W) * sc, py = (y + 0.5f - 0.5f * H) * sc;
    const float A = (float)W / (float)H;
    const float s = vert ? px : py, r = vert ? py : px;
    const float S = vert ? A : 1.f, R = vert ? 1.f : A;
    const float as = fabsf(s);
    const float sgn = s >= 0.f ? 1.f : -1.f;

    // glow colour: the palette at the drift, pushed toward white
    const float3 gp = pal(drift + 0.12f, P);
    const float3 gcol = make_float3(0.55f * gp.x + 0.45f, 0.55f * gp.y + 0.45f, 0.55f * gp.z + 0.45f);

    // ---- squashed picture (collapse of a / opening onto b), soft edge
    float wimg = 0.f, edge = -1.f;
    float3 cimg = make_float3(0.f, 0.f, 0.f);
    const float ew = 0.006f * S;
    if (ka > 0.f || kb > 0.f) {
        const float k = ka > 0.f ? ka : kb;
        edge = k * S;
        wimg = 1.f - smooth(edge - fminf(ew, edge), edge + 0.25f * ew, as);
        if (wimg > 0.f) {
            const float sp = s / fmaxf(k, 1e-4f);                      // un-squashed screen coord
            const float sx = vert ? sp : px, sy = vert ? py : sp;
            const float ix = 0.5f + 0.5f * sx / A, iy = 0.5f + 0.5f * sy;
            const float4 c = ka > 0.f ? squashed(a, a4, a16, ix, iy, k, W, H, W4, H4, W16, H16)
                                      : squashed(b, b4, b16, ix, iy, k, W, H, W4, H4, W16, H16);
            const float g = ka > 0.f ? ga : gb;                        // light concentrates as it squeezes
            cimg = make_float3(fminf(c.x * g / 255.f, 1.f), fminf(c.y * g / 255.f, 1.f), fminf(c.z * g / 255.f, 1.f));
        }
    }

    float3 col = cimg;
    if (wimg < 1.f) {
        // ---- the corridor: ray -> tilted wall plane
        const float den = as - m;
        const float q = fmaxf(den, 0.f) / S;                           // 1 / depth
        const float z = q > 1e-4f ? 1.f / q : 1e4f;
        const float farw = smooth(3.f, 12.f, z);                       // 1/4 -> 1/16 level
        const float fog = smooth(9.f, 45.f, z);                        // -> the far light
        const float detail = 1.f - smooth(7.f, 18.f, z);               // band / streak modulation fades with depth
        const float u = r * fminf(z, 200.f);
        const float v = fminf(z, 200.f) + travel;

        // slit column: a small wander, widened by slit_width into a scan across the picture (mirrored walls)
        const float wander = 0.62f * sinf(v * 0.23f + ph1) + 0.38f * sinf(v * 0.57f + ph2);
        const float s_img = clamp01(0.5f + sgn * (0.03f + 0.47f * slitw) * wander);
        const float r_img = mirror01(0.5f + u / (2.f * R) + 0.02f * sinf(v * 0.31f + ph3));

        // which source: the new picture's slit streams in from the far end (1/z front)
        const float mb = 1.f - smooth(frontq - 0.06f, frontq + 0.06f, q);
        float4 c;
        if (mb <= 0.f) c = wall(a4, a16, s_img, r_img, vert, farw, W4, H4, W16, H16);
        else if (mb >= 1.f) c = wall(b4, b16, s_img, r_img, vert, farw, W4, H4, W16, H16);
        else c = mix4(wall(a4, a16, s_img, r_img, vert, farw, W4, H4, W16, H16),
                      wall(b4, b16, s_img, r_img, vert, farw, W4, H4, W16, H16), mb);
        float cr = c.x / 255.f, cg = c.y / 255.f, cb = c.z / 255.f;
        const float L = 0.2126f * cr + 0.7152f * cg + 0.0722f * cb;
        cr = clamp01(L + (cr - L) * 1.45f); cg = clamp01(L + (cg - L) * 1.45f); cb = clamp01(L + (cb - L) * 1.45f);

        // palette gel: colour bands streaming along depth, lit by the slit's own brightness
        const float3 tp = pal(v * 0.06f * period + 0.18f * r_img + drift, P);
        const float lit = 0.3f + 1.15f * L;
        cr += (tp.x * lit - cr) * smear; cg += (tp.y * lit - cg) * smear; cb += (tp.z * lit - cb) * smear;

        // streaming light planes along v (smooth band values, bright seams) and fine streaks along u
        const float bv = v * 0.85f;
        const float fb = floorf(bv), tb = bv - fb;
        const unsigned int ib = (unsigned int)(int)fb;
        const float tt = tb * tb * (3.f - 2.f * tb);
        const float h0 = hash1(ib, seed, 7u), h1 = hash1(ib + 1u, seed, 7u);
        const float band = h0 + (h1 - h0) * tt;
        const float seam = __expf(-tb * 18.f) * (h0 > 0.55f ? 1.f : 0.25f);
        const float streak = 0.45f + 1.1f * vnoise(u * 7.f, 0.5f * sgn, seed + 3u);
        float gain = (0.22f + 1.25f * band * band) * (1.f + 0.75f * seam);
        gain = 0.8f + (gain * (0.55f + 0.45f * streak) - 0.8f) * detail;
        cr *= gain; cg *= gain; cb *= gain;

        // depth fog into the far light (dimmer than the core so the slit line reads)
        const float3 fc = pal(drift + 0.33f, P);
        const float fk = 0.55f;
        cr += (fk * (0.5f * fc.x + 0.5f * gcol.x) - cr) * fog;
        cg += (fk * (0.5f * fc.y + 0.5f * gcol.y) - cg) * fog;
        cb += (fk * (0.5f * fc.z + 0.5f * gcol.z) - cb) * fog;

        // the vanishing slit itself: a hot line of light
        const float core = glow * (__expf(-fmaxf(den, 0.f) / (0.012f * S)) + 0.35f * __expf(-fmaxf(den, 0.f) / (0.06f * S)));
        // the front of the new picture rushing toward the viewer: a ring of light
        const float dq = (q - frontq) / 0.03f;
        const float ring = (frontq > 0.f && frontq < 1.05f) ? glow * env * 0.9f * __expf(-dq * dq) : 0.f;
        const float gl = fminf(core + ring, 1.f);
        cr = 1.f - (1.f - clamp01(cr)) * (1.f - gcol.x * gl);
        cg = 1.f - (1.f - clamp01(cg)) * (1.f - gcol.y * gl);
        cb = 1.f - (1.f - clamp01(cb)) * (1.f - gcol.z * gl);

        col = make_float3(cr + (cimg.x - cr) * wimg, cg + (cimg.y - cg) * wimg, cb + (cimg.z - cb) * wimg);
    }

    // rim light on the squashed picture's edge (weighted by sin(pi p))
    if (edge >= 0.f) {
        const float rim = glow * env * 1.1f * __expf(-fabsf(as - edge) / (0.012f * S));
        const float g = fminf(rim, 1.f);
        col = make_float3(1.f - (1.f - clamp01(col.x)) * (1.f - gcol.x * g),
                          1.f - (1.f - clamp01(col.y)) * (1.f - gcol.y * g),
                          1.f - (1.f - clamp01(col.z)) * (1.f - gcol.z * g));
    }
    out[y * W + x] = pack(make_float4(col.x * 255.f, col.y * 255.f, col.z * 255.f, 255.f));
}
"""

_PC = 0.20      # collapse ends
_PO = 0.74      # open begins


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class StargateSlitscan(Transition):
    name = "stargate_slitscan"
    label = "Stargate slit-scan"
    description = ("The old picture collapses into a glowing slit, two planes of streaming slit-scan light rush "
                   "toward you (2001's Star Gate), then the corridor opens onto the new picture.")
    default_duration_s = 6.0
    tags = {"family": "warp", "dims": "3d", "technique": ["warp", "light", "retro"],
            "moods": ["hypnotic", "cosmic", "retro"], "style": "wild", "energy": [0.4, 1.0]}
    params = {
        "speed": num(0.2, 3.0, 1.0, "Travel speed", unit="×", tier="regional", basic=True),
        "axis": enum(["vertical", "horizontal"], "vertical", "Slit axis (walls left/right or floor/ceiling)",
                     tier="structural"),
        "plane_angle": num(-25.0, 10.0, 0.0, "Wall angle (V crease <- parallel -> fanned)", step=0.5, unit="°",
                           tier="regional"),
        "slit_width": num(0.0, 1.0, 0.3, "Slit width (one line -> whole picture scanned)", tier="regional"),
        "colour_smear": num(0.0, 1.0, 0.55, "Colour smear (picture -> palette light)", tier="regional"),
        "glow": num(0.0, 1.5, 0.75, "Glow", tier="regional"),
        "palette": enum(list(PALETTES), "neon", "Palette", tier="regional"),
        "palette_period": num(0.1, 4.0, 1.0, "Palette period", tier="regional"),
        "colour_drift": num(0.0, 2.0, 0.5, "Colour drift (cycles per transition)", tier="regional"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        w, h = ctx.width, ctx.height
        self._w4, self._h4 = (w + 3) // 4, (h + 3) // 4
        self._w16, self._h16 = (self._w4 + 3) // 4, (self._h4 + 3) // 4
        self._a4 = cp.zeros((self._h4, self._w4, 4), cp.uint8)
        self._b4 = cp.zeros((self._h4, self._w4, 4), cp.uint8)
        self._a16 = cp.zeros((self._h16, self._w16, 4), cp.uint8)
        self._b16 = cp.zeros((self._h16, self._w16, 4), cp.uint8)
        self._P = cp.zeros(12, cp.float32)
        self._pal_name = None
        mod = raw_module(_SRC)
        self._k_down = mod.get_function("sg_down4")
        self._k = mod.get_function("stargate")
        s = int(self.params.get("seed", -1))
        seed = int(np.random.default_rng().integers(0, 2**31)) if s < 0 else s
        rng = np.random.default_rng(seed)
        self._ph = rng.uniform(0.0, 2.0 * math.pi, 3)
        self._travel0 = float(rng.uniform(0.0, 40.0))
        self._drift0 = float(rng.uniform(0.0, 1.0))
        self._hseed = np.uint32(rng.integers(0, 2**31))

    def _sync_palette(self) -> None:
        name = self.params.get("palette", "neon")
        if name not in PALETTES:
            name = "neon"
        if name != self._pal_name:
            self._P.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal_name = name

    def _down(self, src, d4, d16) -> None:
        w, h = self.ctx.width, self.ctx.height
        launch_2d(self._k_down, self._w4, self._h4,
                  (src, d4, np.int32(w), np.int32(h), np.int32(self._w4), np.int32(self._h4)))
        launch_2d(self._k_down, self._w16, self._h16,
                  (d4, d16, np.int32(self._w4), np.int32(self._h4), np.int32(self._w16), np.int32(self._h16)))

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 1e-4:
            cp.copyto(out, a)
            return
        if progress >= 0.9999:
            cp.copyto(out, b)
            return
        p = self.params
        w, h = self.ctx.width, self.ctx.height
        q = float(progress)
        self._sync_palette()

        # phases: collapse of a into the slit, the b front streaming in from far away, the opening onto b
        ka = 1.0 - _smooth(0.0, _PC, q) if q < _PC else 0.0
        kb = _smooth(_PO, 1.0, q) if q > _PO else 0.0
        frontq = -0.15 + 1.35 * _smooth(0.28, 0.70, q)
        need_a = ka > 0.0 or frontq < 1.08
        need_b = kb > 0.0 or frontq > -0.08
        if need_a:
            self._down(a, self._a4, self._a16)
        if need_b:
            self._down(b, self._b4, self._b16)

        env = math.sin(math.pi * q)
        glow = float(p["glow"])
        ga = 1.0 + 0.9 * glow * (1.0 - ka) ** 2 if ka > 0.0 else 1.0
        gb = 1.0 + 0.9 * glow * (1.0 - kb) ** 2 if kb > 0.0 else 1.0
        seconds = q * self.duration_s
        travel = (self._travel0 + 4.5 * float(p["speed"]) * seconds) % 2000.0
        m = math.tan(math.radians(float(p["plane_angle"])))
        drift = (self._drift0 + float(p["colour_drift"]) * q) % 1.0
        vert = 1 if p.get("axis", "vertical") == "vertical" else 0
        launch_2d(self._k, w, h,
                  (a, b, self._a4, self._b4, self._a16, self._b16, out, self._P,
                   np.int32(w), np.int32(h), np.int32(self._w4), np.int32(self._h4),
                   np.int32(self._w16), np.int32(self._h16), np.int32(vert),
                   np.float32(ka), np.float32(kb), np.float32(travel), np.float32(m),
                   np.float32(p["slit_width"]), np.float32(p["colour_smear"]), np.float32(glow),
                   np.float32(env), np.float32(frontq), np.float32(drift), np.float32(p["palette_period"]),
                   np.float32(self._ph[0]), np.float32(self._ph[1]), np.float32(self._ph[2]),
                   self._hseed, np.float32(ga), np.float32(gb)))
