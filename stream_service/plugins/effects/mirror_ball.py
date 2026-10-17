"""Mirror ball: a faceted disco ball grows into the frame, its tiles reflecting the picture, while the light specks
it throws sweep across the (slightly dimmed) frame as it spins.

Source: Original (inspired by https://skyesage.gumroad.com/l/DiscoShader)
Author: smfx (original; inspired by Resolume 'Stingy Sphere' and the disco ball; the CC-BY-NC-SA Gumroad disco shader was NOT used)
License: MIT (original)
license_class: permissive

amount 0 is the untouched frame (copied).  As amount rises a mirror ball lowers on its chain and grows from size 0,
the room dims a little (background_dim) and the specks fade in.  The ball is analytic (ray/sphere, flat frame3d
camera): a lat-long grid of square mirror tiles (rows = facet_count, tiles per row ~ 2 rows sin(latitude), staggered
rows, slightly jittered tile normals, dark grout) each reflecting its own piece of the frame through a mirror-ball
environment lookup (the frame "surrounds" the ball), plus coloured spill, a soft highlight and hard per-tile glints
from two gelled spotlights.  Glinting tiles are found per frame by a tiny per-facet kernel and drawn as 4-point star
flares.  The specks are the same tile grid evaluated analytically per background pixel: for the beam direction b
from the ball to the wall point, the facet that would send light l there has normal normalize(b + l); its tile
decides whether a speck is lit and where its centre is (no scatter pass).  Colour: each spotlight's gel is pal() at
the integrated drift (+0.5 for the second light), per-speck palette offsets by palette_period.  Pulse: an
integrated spin-rate kick, a sparkle flash on a beat-seeded subset of tiles, brighter specks and stars.
facet_count eases by cross-blending the two neighbouring integer grids.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module

from stream_service.plugins._lib.beat import paced
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.frame3d import FRAME3D, FRAME3D_OPTIONS, FrameMips, camera_flat, flat_distance, smoothstep01
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params
from stream_service.plugins._lib.sdf3d import SDF3D

FOV = 40.0
NP = 96
MAXG = 48            # star flares (glinting tiles) per frame
GF = 6               # floats per glint: px py strength r g b

_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
#define P_R 13
#define P_C 22
#define P_RAD 25
#define P_M 26
#define P_BGK 27
#define P_SPK 28
#define P_SCOUNT 29
#define P_SSIZE 30
#define P_SFEATH 31
#define P_CS 32
#define P_L1 35
#define P_L2 38
#define P_LC1 41
#define P_LC2 44
#define P_GLINT 47
#define P_FLASH 48
#define P_N0 50
#define P_N1 51
#define P_W1 52
#define P_DRIFT 54
#define P_PERIOD 55
#define P_PAL 56
#define P_STAR 68
#define P_STROT 69
#define P_STARG 70
#define P_CHAIN 71
#define P_GLOW 72
#define P_LOD 73
#define P_GROUT 74
#define P_GCA 75
#define P_GCB 76
#define P_CPX 77
#define P_TINT 80
#define P_JIT 81
#define MB_MAXG 48
#define MB_GF 6

__device__ __forceinline__ float3 mb_screen(float3 a, float3 b) {
    return f3(1.f) - (f3(1.f) - a) * (f3(1.f) - saturate(b));
}

// Tiles per latitude row (square-ish tiles: about 2 nl sin(latitude) round the ball).
__device__ __forceinline__ int mb_nlon(int nl, float sl) { return max(3, (int)(2.f * (float)nl * sl + 0.5f)); }

// Object-space centre normal of tile (row, fl) incl. its small fixed tilt (real tiles are never perfectly set).
__device__ __forceinline__ float3 mb_tile_normal(int row, float fl, int nl, int nlon, float off, unsigned int id,
                                                 unsigned int seed, float jit) {
    const float latc = ((float)row + 0.5f) * (F3D_PI / (float)nl);
    float sl, cl; sincosf(latc, &sl, &cl);
    const float lonc = ((fl + 0.5f - off) / (float)nlon - 0.5f) * F3D_TAU;
    float so, co; sincosf(lonc, &so, &co);
    const float3 nc = f3(sl * so, cl, sl * co);
    if (jit <= 0.f) return nc;
    const float3 eu = f3(co, 0.f, -so), ev = f3(cl * so, -sl, cl * co);
    return normalize(nc + eu * (jit * (hash1(id, seed, 11u) - 0.5f)) + ev * (jit * (hash1(id, seed, 13u) - 0.5f)));
}

struct MbCell { float fu, fv, wu, wv; unsigned int id; float3 nc; };
// The tile containing object-space unit direction n: centred tile coords fu, fv in [-.5, .5], tile size in radians
// (wu along the row at this latitude, wv across rows), id, centre normal.
__device__ MbCell mb_cell(float3 n, int nl, unsigned int seed, float jit) {
    MbCell c;
    const float ny = clamp(n.y, -1.f, 1.f);
    const float fr = acosf(ny) * ((float)nl / F3D_PI);
    const int row = min(max((int)fr, 0), nl - 1);
    const float sl = sinf(((float)row + 0.5f) * (F3D_PI / (float)nl));
    const int nlon = mb_nlon(nl, sl);
    const float off = hash1((unsigned int)row, seed, 7u);
    const float fc = (atan2f(n.x, n.z) * (1.f / F3D_TAU) + 0.5f) * (float)nlon + off;
    const float fl = floorf(fc);
    int col = ((int)fl) % nlon;
    if (col < 0) col += nlon;
    c.fu = fc - fl - 0.5f;
    c.fv = fr - (float)row - 0.5f;
    c.wu = F3D_TAU * sqrtf(fmaxf(1.f - ny * ny, 0.f)) / (float)nlon;
    c.wv = F3D_PI / (float)nl;
    c.id = (unsigned int)(row * 4096 + col);
    c.nc = mb_tile_normal(row, fl, nl, nlon, off, c.id, seed, jit);
    return c;
}

// Light the two spotlights throw off the ball onto the wall in beam direction b (world, from the ball).
__device__ float3 mb_specks(const float* __restrict__ P, const Mat3& R, float3 b, int nl, unsigned int seed) {
    float3 acc = f3(0.f);
    #pragma unroll
    for (int k = 0; k < 2; ++k) {
        const float3 l = load3(P + (k ? P_L2 : P_L1));
        const float3 hv = b + l;
        const float hl = length(hv);
        if (hl < 1e-4f) continue;
        const float3 nw = hv * (1.f / hl);
        const float att = smoothstep(0.2f, 0.5f, dot(nw, l));      // grazing facets (the ball's shadow): none
        if (att <= 0.f) continue;
        const MbCell c = mb_cell(mul_t(R, nw), nl, seed, 0.f);
        if (hash1(c.id, seed, 31u + (unsigned int)k) >= P[P_SCOUNT]) continue;
        const float asp = c.wu / fmaxf(c.wv, 1e-6f);
        const float du = c.fu * asp, dv = c.fv;
        const float sc = fminf(1.f, asp);
        const float r = P[P_SSIZE] * sc, fe = P[P_SFEATH] * sc;
        const float s = 1.f - smoothstep(r - fe, r, sqrtf(du * du + dv * dv));
        if (s <= 0.f) continue;
        const float hb = hash1(c.id, seed, 41u + (unsigned int)k);
        const float3 gel = pal(P[P_DRIFT] + 0.5f * (float)k + P[P_PERIOD] * 0.25f * (hb - 0.5f), P + P_PAL);
        const float3 lc = mix(f3(1.f), gel, P[P_TINT]);
        acc += lc * (s * att * (0.55f + 0.45f * hash1(c.id, seed, 51u + (unsigned int)k)));
    }
    return acc;
}

// One mirror tile's colour at the ball pixel with true normal nt (world) for an nl-row ball.
__device__ float3 mb_ball(const Tex& T, const float* __restrict__ P, const Mat3& R, float3 rd, float3 nt, int nl,
                          unsigned int seed, unsigned int fseed, float fp, float rad, float lod) {
    const MbCell c = mb_cell(mul_t(R, nt), nl, seed, P[P_JIT]);
    const float3 nf = mul(R, c.nc);
    const float3 ns = normalize(mix(nf, nt, 0.18f));               // a little curvature: each tile shows a piece
    const float3 rr = reflect(rd, ns);
    const float mm = 2.f * sqrtf(rr.x * rr.x + rr.y * rr.y + (rr.z + 1.f) * (rr.z + 1.f)) + 1e-6f;
    float3 col = tex_sample(T, 0.5f + rr.x / mm, 0.5f - rr.y / mm, lod);
    const float hv = hash1(c.id, seed, 61u);
    col = col * (0.72f + 0.33f * hv) + f3(0.035f + 0.04f * hv);
    const float3 l1 = load3(P + P_L1), l2 = load3(P + P_L2);
    const float3 lc1 = load3(P + P_LC1), lc2 = load3(P + P_LC2);
    const float3 rf = reflect(rd, nf);
    const float d1 = dot(rf, l1), d2 = dot(rf, l2);
    // hard per-tile glints, a softer highlight pool, coloured spill
    const float g1 = smoothstep(P[P_GCA], P[P_GCB], d1), g2 = smoothstep(P[P_GCA], P[P_GCB], d2);
    const float s1 = __powf(saturate(d1), 22.f), s2 = __powf(saturate(d2), 22.f);
    col += (lc1 * g1 + lc2 * g2) * P[P_GLINT];
    col += (lc1 * s1 + lc2 * s2) * 0.32f;
    col += (lc1 * saturate(dot(nf, l1)) + lc2 * saturate(dot(nf, l2))) * 0.07f;
    // beat sparkle: a beat-seeded subset of tiles flashes
    const float hf = hash1(c.id, fseed, 71u);
    if (hf < 0.14f) col += mix(f3(1.f), hf < 0.07f ? lc1 : lc2, 0.35f) * (P[P_FLASH] * (0.6f + 2.8f * hf));
    col = col * f3(0.96f, 0.98f, 1.f);
    // grout between the tiles (antialiased in screen pixels)
    const float e = fminf((0.5f - fabsf(c.fu)) * c.wu, (0.5f - fabsf(c.fv)) * c.wv) * rad;
    const float gw = fmaxf(P[P_GROUT] * c.wv * rad, 0.6f * fp);
    const float g = 1.f - smoothstep(gw - 0.5f * fp, gw + 0.5f * fp, e);
    return col * (1.f - 0.85f * g);
}

// Per-facet pass: tiles whose mirror sends a spotlight straight into the camera -> star flare list.
SDF_KERNEL
void mirror_ball_glints(const float* __restrict__ P, const int nl, const float wgt, const int w, const int h,
                        const unsigned int seed, int* __restrict__ cnt, float* __restrict__ glist)
{
    const int col = blockIdx.x * blockDim.x + threadIdx.x, row = blockIdx.y * blockDim.y + threadIdx.y;
    if (row >= nl) return;
    const float sl = sinf(((float)row + 0.5f) * (F3D_PI / (float)nl));
    const int nlon = mb_nlon(nl, sl);
    if (col >= nlon) return;
    const float off = hash1((unsigned int)row, seed, 7u);
    const unsigned int id = (unsigned int)(row * 4096 + col);
    const Mat3 R = mat3_load(P + P_R);
    const float3 nw = mul(R, mb_tile_normal(row, (float)col, nl, nlon, off, id, seed, P[P_JIT]));
    const Cam cam = cam_load(P);
    const float3 p = load3(P + P_C) + nw * P[P_RAD];
    const float3 d = p - cam.eye;
    const float dl = length(d);
    if (dot(nw, d) > -0.08f * dl) return;                           // facing away / grazing
    const float3 v = d * (1.f / fmaxf(dl, 1e-6f));
    const float3 rf = reflect(v, nw);
    const float g1 = smoothstep(P[P_GCA], P[P_GCB], dot(rf, load3(P + P_L1)));
    const float g2 = smoothstep(P[P_GCA], P[P_GCB], dot(rf, load3(P + P_L2)));
    const float gm = fmaxf(g1, g2) * wgt;
    if (gm < 0.02f) return;
    const float z = dot(d, cam.fwd);
    if (z <= 1e-4f) return;
    const float s = cam.tan_half / (0.5f * (float)h);
    const float spx = 0.5f * (float)w + dot(d, cam.right) / z / s;
    const float spy = 0.5f * (float)h - dot(d, cam.up) / z / s;
    const int i = atomicAdd(cnt, 1);
    if (i >= MB_MAXG) return;
    const float3 lc = (load3(P + P_LC1) * g1 + load3(P + P_LC2) * g2) * (1.f / fmaxf(g1 + g2, 1e-4f));
    const float3 sc = mix(f3(1.f), lc, 0.45f);
    float* o = glist + i * MB_GF;
    o[0] = spx; o[1] = spy; o[2] = gm; o[3] = sc.x; o[4] = sc.y; o[5] = sc.z;
}

SDF_KERNEL
void mirror_ball(uchar4* __restrict__ out, const int w, const int h, FRAME_ARGS, const float* __restrict__ P,
                 const unsigned int seed, const unsigned int fseed, const int* __restrict__ gcnt,
                 const float* __restrict__ glist)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    TEX_FROM_ARGS;
    const float px = x + 0.5f, py = y + 0.5f;
    float3 col = tex_fetch(T, x, y) * P[P_BGK];
    const Mat3 R = mat3_load(P + P_R);
    const int n0 = (int)P[P_N0], n1 = (int)P[P_N1];
    const float w1 = P[P_W1];

    // specks on the wall (the frame plane z = 0; beams leave from P_CS in front of it)
    if (P[P_SPK] > 0.f) {
        const float A = (float)w / (float)h;
        const float3 pw = f3((2.f * px / (float)w - 1.f) * A, 1.f - 2.f * py / (float)h, 0.f);
        const float3 b = normalize(pw - load3(P + P_CS));
        float3 s = mb_specks(P, R, b, n0, seed);
        if (w1 > 0.f) s = mix(s, mb_specks(P, R, b, n1, seed), w1);
        col = mb_screen(col, s * (P[P_SPK] * (0.4f + 0.6f * saturate(-b.z))));
    }

    // haze glow round the ball
    const float cx = P[P_CPX], cy = P[P_CPX + 1], rp = fmaxf(P[P_CPX + 2], 1e-3f);
    const float dx = px - cx, dy = py - cy, dist = sqrtf(dx * dx + dy * dy);
    const float3 lc1 = load3(P + P_LC1), lc2 = load3(P + P_LC2);
    if (P[P_GLOW] > 0.f) {
        const float g = P[P_GLOW] * __expf(-fmaxf(dist - rp, 0.f) / (0.7f * rp + 1.f));
        col = mb_screen(col, (lc1 + lc2) * (0.5f * g));
    }

    // the chain it hangs from
    if (P[P_CHAIN] > 0.f && py < cy) {
        const float hw = 0.0016f * (float)h + 0.5f, ad = fabsf(dx);
        const float cov = saturate(hw + 0.5f - ad) * P[P_CHAIN];
        if (cov > 0.f) {
            const float lk = 0.55f + 0.45f * fabsf(sinf(py * (F3D_PI / (3.f * hw))));
            col = mix(col, f3(0.6f, 0.62f, 0.66f) * (lk * (1.f - 0.45f * ad / hw)), cov);
        }
    }

    // the ball
    const Cam cam = cam_load(P);
    const float3 C = load3(P + P_C);
    const float rad = P[P_RAD];
    const float3 ro = cam.eye - C;
    const float3 rd = cam_dir(cam, px, py, w, h);
    const float fp = pixel_angle(cam, h) * length(ro);
    const float cover = sphere_cover(ro, rd, rad, fp) * saturate(rad / fmaxf(fp, 1e-9f));
    if (cover > 0.f) {
        const FHit hit = ihit_sphere(ro, rd, rad + fp);
        if (fhit_ok(hit)) {
            const float3 nt = hit.n;
            const float ndv = saturate(-dot(rd, nt));
            const float lod = P[P_LOD] + 2.5f * (1.f - ndv);
            float3 bc = mb_ball(T, P, R, rd, nt, n0, seed, fseed, fp, rad, lod);
            if (w1 > 0.f) bc = mix(bc, mb_ball(T, P, R, rd, nt, n1, seed, fseed, fp, rad, lod), w1);
            bc = bc * (0.68f + 0.32f * sqrtf(ndv));
            col = mix(col, bc, cover);
        }
    }

    // star flares on the glinting tiles
    const int ns = min(gcnt[0], MB_MAXG);
    const float sl = P[P_STAR];
    if (ns > 0 && sl > 0.5f && fabsf(dx) < rp + sl && fabsf(dy) < rp + sl) {
        float sn, cs; sincosf(P[P_STROT], &sn, &cs);
        const float th = 0.0011f * (float)h + 0.6f;
        float3 acc = f3(0.f);
        for (int i = 0; i < ns; ++i) {
            const float* g = glist + i * MB_GF;
            const float ex = px - g[0], ey = py - g[1], a = g[2];
            const float L = sl * (0.45f + 0.55f * a);
            const float qx = fabsf(cs * ex + sn * ey), qy = fabsf(cs * ey - sn * ex);
            const float r2 = ex * ex + ey * ey, cr = 0.1f * L + 1.5f;
            if (qx > L && qy > L && r2 > 9.f * cr * cr) continue;
            const float tx = saturate(1.f - qx / L), ty = saturate(1.f - qy / L);
            const float wx = th * (0.35f + 0.65f * tx), wy = th * (0.35f + 0.65f * ty);
            const float ray = tx * tx * __expf(-qy * qy / (wx * wx)) + ty * ty * __expf(-qx * qx / (wy * wy));
            const float core = __expf(-r2 / (cr * cr));
            acc += f3(g[3], g[4], g[5]) * (a * (1.1f * core + 0.9f * ray));
        }
        col = mb_screen(col, acc * P[P_STARG]);
    }
    store_rgb(out, y * w + x, col);
}
"""


def _pal(t: float, c) -> np.ndarray:
    a, b, k, d = np.asarray(c[0:3]), np.asarray(c[3:6]), np.asarray(c[6:9]), np.asarray(c[9:12])
    return np.clip(a + b * np.cos(2.0 * math.pi * (k * t + d)), 0.0, 1.0)


def _rx(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], np.float64)


def _ry(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], np.float64)


def _rz(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], np.float64)


def _unit(v) -> np.ndarray:
    v = np.asarray(v, np.float64)
    return v / np.linalg.norm(v)


class MirrorBall(Effect):
    name = "mirror_ball"
    stage = "projection"
    label = "Mirror ball"
    description = "A faceted disco ball reflects the picture while the light specks it throws sweep across the frame."
    params = {
        **standard_effect_params(),
        "ball_size": num(0.08, 0.6, 0.3, "Ball size", tier="regional", basic=True),
        "ball_x": num(-0.8, 0.8, 0.0, "Ball position", tier="regional"),
        "ball_y": num(-0.5, 0.7, 0.3, "Ball height", tier="regional"),
        "facet_count": num(12, 64, 28, "Facet rows", step=1, tier="regional"),
        "spin_speed": paced(num(-0.4, 0.4, 0.06, "Spin", unit="turns/s", step=0.005, tier="realtime", bind="rate")),
        "kick": num(0.0, 1.0, 0.3, "Beat spin kick", unit="turns/s", tier="regional"),
        "speck_intensity": num(0.0, 1.5, 0.85, "Speck brightness", tier="regional"),
        "speck_count": num(0.05, 1.0, 0.7, "Speck count", tier="regional"),
        "speck_size": num(0.08, 0.45, 0.22, "Speck size", tier="regional"),
        "speck_spread": num(0.3, 1.6, 0.75, "Speck spread", tier="regional"),
        "speck_colour": num(0.0, 1.0, 0.6, "Coloured lights", tier="regional"),
        "sparkle": num(0.0, 1.5, 0.8, "Sparkle", tier="regional"),
        "background_dim": num(0.0, 0.9, 0.35, "Background dim", tier="regional"),
        **palette_params("rainbow", 1.0, 0.04),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC, FRAME3D_OPTIONS)
        self._k = mod.get_function("mirror_ball")
        self._kg = mod.get_function("mirror_ball_glints")
        self.mips = FrameMips(ctx, levels=4, blur_level=0)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.cnt = cp.zeros(1, cp.int32)
        self.glist = cp.zeros(MAXG * GF, cp.float32)
        self.cam = camera_flat(FOV)
        self.D = flat_distance(FOV)
        self.l1 = _unit((-0.5, 0.55, 0.68))
        self.l2 = _unit((0.55, 0.38, 0.75))
        self.theta = 0.0
        self.drift = 0.0
        self.sway = 0.0
        self.star_ph = 0.0
        self.clock = 0.0
        self.prev_pulse = 0.0
        self.last_beat = -1.0
        self.fseed = 1

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        a = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        dtc = min(max(float(dt), 0.0), 0.1)
        # integrate every phase (continuous whatever the amount), beat edge for the sparkle reseed
        spin = float(p["spin_speed"])
        sgn = 1.0 if spin >= 0 else -1.0
        self.theta = math.fmod(self.theta + 2.0 * math.pi * (spin + sgn * p["kick"] * pulse) * dtc, 2.0 * math.pi)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dtc, 100.0)
        self.sway = math.fmod(self.sway + 0.45 * dtc, 2.0 * math.pi)
        self.star_ph = math.fmod(self.star_ph + 0.31 * dtc, 2.0 * math.pi)
        self.clock += dtc
        if pulse - self.prev_pulse > 0.15 and self.clock - self.last_beat > 0.12:
            self.fseed = (self.fseed + 1) & 0x7FFFFFFF
            self.last_beat = self.clock
        self.prev_pulse = pulse
        if a <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        m = smoothstep01(a)
        mb = smoothstep01(min(1.0, a / 0.6))
        W, H = self.ctx.width, self.ctx.height
        A = W / H
        rad = float(p["ball_size"]) * mb
        cxw = float(p["ball_x"]) * A
        cyw = float(p["ball_y"]) + 0.35 * (1.0 - mb)              # lowers on its chain as it appears
        R = _rx(0.22) @ _rz(0.035 * math.sin(self.sway)) @ _ry(self.theta)
        coeffs = palette_coeffs(p["palette"])
        tint = float(p["speck_colour"])
        lc1 = 1.0 + (_pal(self.drift, coeffs) - 1.0) * tint
        lc2 = 1.0 + (_pal(self.drift + 0.5, coeffs) - 1.0) * tint
        nf = min(max(float(p["facet_count"]), 12.0), 64.0)
        n0 = math.floor(nf)
        fr = nf - n0
        n1, w1 = n0 + 1, 0.0
        if fr > 0.98:
            n0, n1 = n0 + 1, n0 + 2
        elif fr >= 0.02:
            w1 = fr
        throw = float(p["speck_spread"])
        cell_px = throw * (2.0 * math.pi / nf) * 0.5 * H
        sparkle = float(p["sparkle"])
        dist = self.D
        r_px = rad * dist / math.sqrt(max(dist * dist - rad * rad, 1e-6)) * 0.5 * H

        h = self.host
        h[:] = 0.0
        h[0:13] = self.cam
        h[13:22] = R.reshape(-1)
        h[22:25] = (cxw, cyw, 0.0)
        h[25] = rad
        h[26] = m
        h[27] = 1.0 - float(p["background_dim"]) * m
        h[28] = float(p["speck_intensity"]) * m * (1.0 + 0.4 * pulse)
        h[29] = float(p["speck_count"])
        h[30] = float(p["speck_size"])
        h[31] = max(0.04, 2.0 / max(cell_px, 1.0))
        h[32:35] = (cxw, cyw, throw)
        h[35:38] = self.l1
        h[38:41] = self.l2
        h[41:44] = lc1
        h[44:47] = lc2
        h[47] = 1.5 * sparkle * (1.0 + 0.6 * pulse)
        h[48] = 0.9 * pulse * m * (0.4 + 0.6 * min(sparkle, 1.0))
        h[50] = n0
        h[51] = n1
        h[52] = w1
        h[54] = self.drift
        h[55] = float(p["palette_period"])
        h[56:68] = coeffs
        h[68] = H * (0.035 + 0.11 * rad) * mb * (1.0 + 0.35 * pulse) * min(1.0, 0.3 + sparkle)
        h[69] = 0.25 + 0.15 * math.sin(self.star_ph)
        h[70] = 0.9 * sparkle * m * (1.0 + 0.6 * pulse)
        h[71] = mb if cyw + rad < 1.0 else 0.0
        h[72] = 0.1 * m * (1.0 + 0.5 * pulse)
        h[73] = 0.5
        h[74] = 0.055
        h[75] = math.cos(math.radians(5.5))
        h[76] = math.cos(math.radians(1.2))
        h[77] = 0.5 * W + cxw * 0.5 * H
        h[78] = 0.5 * H - cyw * 0.5 * H
        h[79] = r_px
        h[80] = tint
        h[81] = 0.06
        self.P.set(h)

        seed = np.uint32(int(p["seed"]) & 0xFFFFFFFF)
        self.mips.build(src)
        self.cnt.fill(0)
        iw, ih = np.int32(W), np.int32(H)
        grids = [(n0, 1.0 - w1)] + ([(n1, w1)] if w1 > 0.0 else [])
        for n, wg in grids:
            launch_2d(self._kg, 2 * n + 2, n, (self.P, np.int32(n), np.float32(wg), iw, ih, seed, self.cnt, self.glist))
        launch_2d(self._k, W, H, (out, iw, ih, *self.mips.args(src), self.P, seed, np.uint32(self.fseed),
                                  self.cnt, self.glist))
