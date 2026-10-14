"""Sacred mandala: log-polar rings of neon line work zooming endlessly in (or out), with seed-of-life,
flower-of-life and Metatron's-cube rosettes set into every ring.

Source: https://www.shadertoy.com/view/mtjyz1 (as shipped in xscreensaver 6.17, hacks/glx/glsl/trainmandala.glsl)
Author: Marten Range (mrange, 'Quick hack on the train', mtjyz1, CC0, as in xscreensaver trainmandala.glsl); helpers Inigo Quilez (MIT), mercury mod1 ('MIT OR CC-BY-NC-4.0', taken as MIT); overlays by smfx
License: CC0-1.0 + MIT
license_class: permissive
Ported from GLSL by smfx (ideas/changes: host-integrated zoom/rotation phases instead of iTime; the ring colour is
keyed to a global ring id via a host accumulator so the self-similar wrap never shows; neon-on-dark by default
with a continuous 'invert' back towards the original's ink-on-paper look; chords of the neighbouring band are
also evaluated so halos do not seam at the circles; symmetry order eased by cross-blending neighbouring
integers; up to three zoom layers faded in by energy; seed-of-life / flower-of-life / Metatron's-cube rosettes
inscribed in each kite of the chord lattice (D6-folded, primitives pruned on the host); an outward-running
radius-delayed beat glow; palette via the shared cosine pal()).

Geometry (per layer j, zoom factor zf, symmetry N): the screen point is rotated, divided by z_j = zf^frac(phase_j)
and mapped to its ring band [r0, r0 zf] with r0 = zf^floor(log_zf r).  In r0 units the band is the annulus
[1, zf]: the two circles bound it, and in each angular sector (half-angle h = pi/N, folded with mod + abs)
two chords run 1/_h -> zf/_0 and 1/_0 -> zf/_h (the original's two passes, i = 0 and i = 1).  They cross
into a kite around the sector's centre line; the motif sits on the kite's incircle (computed on the host per
N and zf).  Distances are in band units, scaled by r0 z_j to screen units, so line widths are constant on screen;
bands narrower than a few pixels fade out towards the centre, where a soft core glow takes over.

Macros: energy (smoothed) -> zoom rate (0.1 + 0.9 e^1.2 rings/s x zoom_speed), rotation rate (0.04 + 0.2 e,
x spin), glow (0.45 + 0.55 e, plus halo width and gain) and layer count (1 + 2 e^1.3, extra layers fade in).
pulse -> a glow kick that runs outward ring by ring (the kernel reads a 64-sample pulse history at a delay
proportional to log radius, i.e. a constant time per ring) plus a small zoom-rate kick.  speed scales zoom and
rotation; colour = pal(ring id x step + angle terms + drift).  intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present

HN = 64                      # pulse history samples
HDT = 1.0 / 60.0             # history spacing (s)
PH = 96                      # offset of the pulse history in P
NP = PH + HN + 4


# ------------------------------------------------------------------ motif primitives (motif units: outer radius 1)
def _ang(r: float, deg: float) -> tuple[float, float]:
    return (r * math.cos(math.radians(deg)), r * math.sin(math.radians(deg)))


def _motifs():
    a = 1.0 / 3.0
    seed = [(0.0, 0.0, a)] + [(*_ang(a, 60 * k), a) for k in range(6)] + [(0.0, 0.0, 2 * a)]
    flower = ([(0.0, 0.0, a)] + [(*_ang(a, 60 * k), a) for k in range(6)] + [(*_ang(2 * a, 60 * k), a) for k in range(6)]
              + [(*_ang(math.sqrt(3) * a, 30 + 60 * k), a) for k in range(6)] + [(0.0, 0.0, 3 * a)])
    b = 0.2
    pts = [(0.0, 0.0)] + [_ang(2 * b, 60 * k) for k in range(6)] + [_ang(4 * b, 60 * k) for k in range(6)]
    fruit = [(x, y, b) for x, y in pts] + [(0.0, 0.0, 1.0)]
    # Metatron's cube: all 78 lines between the 13 fruit-of-life centres, merged where colinear and overlapping
    lines: dict = {}
    for i in range(13):
        for j in range(i + 1, 13):
            p, q = pts[i], pts[j]
            dx, dy = q[0] - p[0], q[1] - p[1]
            ln = math.hypot(dx, dy)
            ux, uy = dx / ln, dy / ln
            if ux < -1e-9 or (abs(ux) < 1e-9 and uy < 0):
                ux, uy = -ux, -uy
            nx, ny = -uy, ux
            c = nx * p[0] + ny * p[1]
            key = (round(ux, 5) + 0.0, round(uy, 5) + 0.0, round(c, 5) + 0.0)
            t0, t1 = sorted([ux * p[0] + uy * p[1], ux * q[0] + uy * q[1]])
            lines.setdefault(key, []).append((t0, t1))
    segs = []
    for (ux, uy, c), iv in lines.items():
        iv.sort()
        cur = list(iv[0])
        for s0, s1 in iv[1:]:
            if s0 <= cur[1] + 1e-6:
                cur[1] = max(cur[1], s1)
            else:
                segs.append((ux, uy, c, cur[0], cur[1]))
                cur = [s0, s1]
        segs.append((ux, uy, c, cur[0], cur[1]))
    segs = [((-uy * c + ux * t0, ux * c + uy * t0), (-uy * c + ux * t1, ux * c + uy * t1)) for ux, uy, c, t0, t1 in segs]
    return seed, flower, fruit, segs


def _prune(circles, segs, tol=0.002):
    """Keep the primitives that are (near-)nearest somewhere in the D6 fundamental wedge (0..30 deg, r <= 1.02):
    the kernel folds every point into that wedge first, so the rest can never be the closest."""
    rr = np.linspace(0.0, 1.02, 360)
    tt = np.radians(np.linspace(0.0, 30.0, 240))
    R, T = np.meshgrid(rr, tt)
    X, Y = (R * np.cos(T)).ravel(), (R * np.sin(T)).ravel()
    ds = [np.abs(np.hypot(X - cx, Y - cy) - r) for cx, cy, r in circles]
    for (ax, ay), (bx, by) in segs:
        px, py, ex, ey = X - ax, Y - ay, bx - ax, by - ay
        h = np.clip((px * ex + py * ey) / (ex * ex + ey * ey), 0.0, 1.0)
        ds.append(np.hypot(px - ex * h, py - ey * h))
    D = np.stack(ds)
    keep = (D <= D.min(0) + tol + 1e-7).any(1)
    nc = len(circles)
    return [c for i, c in enumerate(circles) if keep[i]], [s for i, s in enumerate(segs) if keep[nc + i]]


def _dist_fn(name: str, circles, segs) -> str:
    body = ["    float d = 1e4f;"]
    for cx, cy, r in circles:
        body.append(f"    d = fminf(d, fabsf(sqrtf((x - ({cx:.7f}f)) * (x - ({cx:.7f}f)) + (y - ({cy:.7f}f)) * "
                    f"(y - ({cy:.7f}f))) - {r:.7f}f));")
    for (ax, ay), (bx, by) in segs:
        body.append(f"    d = fminf(d, sdseg(x, y, {ax:.7f}f, {ay:.7f}f, {bx:.7f}f, {by:.7f}f));")
    return f"__device__ __forceinline__ float {name}(float x, float y) {{\n" + "\n".join(body) + "\n    return d;\n}\n"


def _motif_src() -> str:
    seed, flower, fruit, segs = _motifs()
    s_c, _ = _prune(seed, [])
    f_c, _ = _prune(flower, [])
    m_c, m_s = _prune(fruit, segs)
    return _dist_fn("d_seed", s_c, []) + _dist_fn("d_flower", f_c, []) + _dist_fn("d_metatron", m_c, m_s)


# P layout (floats)
#  0..11 palette | 12 ring colour step | 13 drift | 14 cos rot | 15 sin rot | 16 ln zf | 17 1/ln zf | 18 zf
#  19 weight of symmetry variant 1 | 20..24 variant 0 (h, cos h, sin h, motif cx, motif R) | 25..29 variant 1
#  30 palette period | 32 + 4j: layer j (z, colour offset, weight, -) for j < 3
#  44 core width | 45 halo width | 46 halo gain | 47 circle gain | 48 chord gain | 49 motif gain
#  50..52 motif weights (seed, flower, metatron) | 53 invert | 54 vignette | 55 intensity
#  56 pulse glow kick | 57 ring delay (s / ring) | 58 1 / HDT | 59 px (screen units) | 60 centre glow | 61 aspect
#  62 white-core gain | PH.. pulse history (newest first)
_SRC = HELPERS + PALETTE_CUDA + r"""
#define HN %(hn)d
#define PH %(ph)d
__device__ __forceinline__ float sdseg(float px, float py, float ax, float ay, float bx, float by) {
    const float pax = px - ax, pay = py - ay, bax = bx - ax, bay = by - ay;
    const float h = clamp01((pax * bax + pay * bay) / (bax * bax + bay * bay + 1e-12f));
    const float dx = pax - bax * h, dy = pay - bay * h;
    return sqrtf(dx * dx + dy * dy);
}
// palette colour kept bright for emissive lines: a dark phase folds towards 2a - pal(t), then max channel >= ~0.6
__device__ __forceinline__ float3 bright_pal(float t, const float* __restrict__ PAL) {
    float3 c = pal(t, PAL);
    const float m0 = fmaxf(c.x, fmaxf(c.y, c.z));
    const float w = 1.f - smooth(0.12f, 0.45f, m0);
    c.x += w * (clamp01(2.f * PAL[0] - c.x) - c.x);
    c.y += w * (clamp01(2.f * PAL[1] - c.y) - c.y);
    c.z += w * (clamp01(2.f * PAL[2] - c.z) - c.z);
    const float k = 1.f / fmaxf(fmaxf(c.x, fmaxf(c.y, c.z)), 0.6f);
    return make_float3(c.x * k, c.y * k, c.z * k);
}
%(motifs)s
struct Acc { float r, g, b, e, w; };
// add a line at screen distance d: gaussian core + lorentzian halo, tinted by col; core also feeds a white-hot term
__device__ __forceinline__ void add_line(Acc& A, float d, float cw, float hw, float hg, float gain, float3 col, float wg) {
    const float a = d / cw, b = d / hw;
    const float core = __expf(-a * a);
    const float g = gain * (core + hg / (1.f + b * b));
    A.r += g * col.x; A.g += g * col.y; A.b += g * col.z; A.e += g;
    A.w += wg * gain * core * core;
}

extern "C" __global__
void mandala_draw(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float hh = 0.5f * H;
    const float u = (x + 0.5f - 0.5f * W) / hh, v = (hh - y - 0.5f) / hh;
    const float rs = sqrtf(u * u + v * v) + 1e-6f;
    const float* PAL = P;
    const float kp = P[12], drift = P[13], lnzf = P[16], ilz = P[17], zf = P[18], period = P[30];
    const float px = P[59];
    const float pu = P[14] * u - P[15] * v, pv = P[15] * u + P[14] * v;
    const float th = atan2f(pv, pu);

    // beat glow running outward: delay = ring_delay x rings from a small core radius (constant time per ring)
    float tau = fmaxf(__logf(rs * 25.f) * ilz, 0.f) * P[57] * P[58];
    tau = fminf(tau, HN - 1.001f);
    const int i0 = (int)tau;
    const float pk = P[PH + i0] + (tau - i0) * (P[PH + i0 + 1] - P[PH + i0]);
    const float gain = 1.f + P[56] * pk;

    const float cw = P[44], hw = P[45], hg = P[46];
    const float wsum = P[50] + P[51] + P[52];
    Acc A = {0.f, 0.f, 0.f, 0.f, 0.f};
    float bgk = 0.f, bgt = 0.f;

    for (int j = 0; j < 3; ++j) {
        const float lw = P[34 + 4 * j];
        if (lw <= 0.001f) continue;
        const float z = P[32 + 4 * j], coff = P[33 + 4 * j];
        const float r = rs / z;
        const float lr = __logf(r) * ilz;
        const float nf = floorf(lr);
        const float r0 = __expf(nf * lnzf);
        const float sc = r0 * z;                       // band unit -> screen units
        const float qr = fminf(fmaxf(r / r0, 1.f), zf);
        const float bwpx = (zf - 1.f) * sc / px;
        const float fade = lw * smooth(2.5f, 9.f, bwpx);
        if (j == 0) {                                  // faint band shading behind everything
            bgt = (nf + 0.5f) * kp + coff;
            bgk = 0.5f + 0.5f * __cosf(3.14159265f * (nf + 0.5f));
        }
        if (fade <= 0.002f) continue;
        const float g0 = gain * fade;
        // circles (shared by both symmetry variants), coloured by their own ring id
        const float dIn = qr - 1.f, dOut = zf - qr;
        const bool nearIn = dIn < dOut;
        const float nc = nf + (nearIn ? 0.f : 1.f);
        const float3 cc = bright_pal(nc * kp + coff + drift + 0.08f * period * __cosf(th), PAL);
        add_line(A, fminf(dIn, dOut) * sc, cw, hw, hg, g0 * P[47], cc, 0.35f);

        for (int k = 0; k < 2; ++k) {
            const float wk = k ? P[19] : 1.f - P[19];
            if (wk <= 0.002f) continue;
            const float* V = P + 20 + 5 * k;
            const float h = V[0], ch = V[1], sh = V[2];
            const float tf = th - 2.f * h * rintf(th / (2.f * h));
            float sn, cs;
            __sincosf(tf, &sn, &cs);
            const float qx = qr * cs, qy = qr * fabsf(sn);
            const float af = fminf(fabsf(tf) / h, 1.f);
            const float cf = g0 * wk * smooth(2.5f, 9.f, 2.f * sh * sc / px);
            if (cf > 0.002f) {
                // chords of this band, then of the nearer neighbouring band (so halos do not seam at the circles)
                const float d0 = fminf(sdseg(qx, qy, ch, sh, zf, 0.f), sdseg(qx, qy, 1.f, 0.f, zf * ch, zf * sh));
                const float sb = nearIn ? 1.f / zf : zf;
                const float d1 = fminf(sdseg(qx, qy, sb * ch, sb * sh, sb * zf, 0.f),
                                       sdseg(qx, qy, sb, 0.f, sb * zf * ch, sb * zf * sh));
                const float tb = (nf + 0.5f) * kp + coff + drift + 0.12f * period * af + 0.17f;
                add_line(A, d0 * sc, cw, hw, hg, cf * P[48], bright_pal(tb, PAL), 0.3f);
                add_line(A, d1 * sc, cw, hw, hg, cf * P[48], bright_pal(tb + (nearIn ? -kp : kp), PAL), 0.3f);
            }
            if (wsum > 0.002f) {
                const float R = V[4];
                const float Rs = R * sc;
                const float mf = g0 * wk * smooth(7.f, 22.f, Rs / px);
                if (mf > 0.002f) {
                    const float mx = (qx - V[3]) / R, my = qy / R;
                    const float mr = sqrtf(mx * mx + my * my);
                    float ds = mr - 0.6666667f, dfl = mr - 1.f, dmt = mr - 1.f;
                    if (mr < 1.02f) {
                        float a = atan2f(my, mx);
                        a = fabsf(a - 1.04719755f * rintf(a * 0.95492966f));
                        float sa, ca;
                        __sincosf(a, &sa, &ca);
                        const float fx = mr * ca, fy = mr * sa;
                        if (P[50] > 0.002f) ds = d_seed(fx, fy);
                        if (P[51] > 0.002f) dfl = d_flower(fx, fy);
                        if (P[52] > 0.002f) dmt = d_metatron(fx, fy);
                    }
                    const float3 mc = bright_pal((nf + 0.5f) * kp + coff + drift + 0.33f + 0.1f * period * mr, PAL);
                    const float mcw = 0.75f * cw, mhw = 0.6f * hw, mhg = 0.8f * hg, mg = mf * P[49];
                    if (P[50] > 0.002f) add_line(A, fmaxf(ds, 0.f) * Rs, mcw, mhw, mhg, mg * P[50], mc, 0.25f);
                    if (P[51] > 0.002f) add_line(A, fmaxf(dfl, 0.f) * Rs, mcw, mhw, mhg, mg * P[51], mc, 0.25f);
                    if (P[52] > 0.002f) add_line(A, fmaxf(dmt, 0.f) * Rs, mcw, mhw, mhg, mg * P[52], mc, 0.25f);
                }
            }
        }
    }
    // core glow where the rings become too fine to draw (kicked by the beat first: the wave starts here)
    const float cg = P[60] * (1.f + P[56] * P[PH]) * 0.05f / (rs * rs + 0.06f);
    const float3 gc = bright_pal(drift + 0.6f, PAL);
    A.r += cg * gc.x; A.g += cg * gc.y; A.b += cg * gc.z; A.e += cg;
    // faint band-tinted ground
    const float3 bc = pal(bgt + drift + 0.5f, PAL);
    const float bl = 0.035f * (0.6f + 0.4f * bgk) * (1.f - 0.5f * smooth(0.f, 1.8f, rs));
    const float wc = P[62] * A.w;
    float3 c = make_float3(1.f - __expf(-(A.r + wc + bl * bc.x)), 1.f - __expf(-(A.g + wc + bl * bc.y)),
                           1.f - __expf(-(A.b + wc + bl * bc.z)));
    // invert -> ink on paper (dark, tinted lines on a warm light ground), as the original's 1 - col
    const float iv = P[53];
    if (iv > 0.002f) {
        const float e = 1.f - __expf(-1.3f * A.e);
        const float ie = 1.f / fmaxf(A.e, 1e-4f);
        const float3 pp = pal(drift + 0.5f, PAL);
        const float pr = 0.9f * (0.85f + 0.15f * pp.x), pg = 0.9f * (0.85f + 0.15f * pp.y), pb = 0.88f * (0.85f + 0.15f * pp.z);
        const float ir = 0.32f * clamp01(A.r * ie), ig = 0.32f * clamp01(A.g * ie), ib = 0.32f * clamp01(A.b * ie);
        c.x += iv * (pr + e * (ir - pr) - c.x);
        c.y += iv * (pg + e * (ig - pg) - c.y);
        c.z += iv * (pb + e * (ib - pb) - c.z);
    }
    const float qx = u / P[61], ql = sqrtf(qx * qx + v * v);
    const float vg = 1.f - P[54] * (1.f - smooth(1.55f, 0.45f, ql));
    const float g = 255.f * P[55] * vg;
    out[y * W + x] = pack(make_float4(c.x * g, c.y * g, c.z * g, 255.f));
}
""" % {"hn": HN, "ph": PH, "motifs": _motif_src()}
_OPTIONS = ("--std=c++14", "--use_fast_math")

LAYER_OFFSETS = (0.0, 0.5, 0.25)


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _kite(n: float, zf: float) -> tuple[float, float]:
    """Incircle of the chord kite in band units: centre x on the sector axis, radius."""
    h = math.pi / n
    s1 = math.sin(math.atan2(zf * math.sin(h), zf * math.cos(h) - 1.0))     # chord 1/_0 -> zf/_h, angle at (1, 0)
    s0 = math.sin(math.atan2(math.sin(h), zf - math.cos(h)))                # chord 1/_h -> zf/_0, angle at (zf, 0)
    xc = (zf * s0 + s1) / (s0 + s1)
    return xc, (xc - 1.0) * s1


def _order(d: dict, first: list[str]) -> dict:
    return {**{k: d[k] for k in first if k in d}, **{k: v for k, v in d.items() if k not in first}}


class SacredMandala(Source):
    name = "sacred_mandala"
    label = "Sacred mandala"
    description = ("Endless log-polar zoom through rings of neon line work, with seed-of-life, flower-of-life "
                   "and Metatron's-cube rosettes set into every ring; the beat sends a glow running outward.")
    tags = {"family": "geometric", "dims": "2d", "toolkit": "none", "technique": ["analytic", "kaleido"],
            "moods": ["hypnotic", "geometric", "cosmic"], "style": "lively", "energy": [0.2, 1.0]}
    params = _order({
        "symmetry_order": num(6.0, 16.0, 10.0, "Symmetry", step=0.1, tier="regional", basic=True),
        "overlay": num(0.0, 1.0, 0.67, "Overlay (plain -> seed -> flower -> Metatron)", tier="regional", basic=True),
        "zoom_factor": num(1.4, 2.6, 1.8, "Ring spacing", tier="regional"),
        "line_weight": num(0.0, 1.0, 0.45, "Line weight", tier="regional"),
        "zoom_speed": num(-1.0, 1.0, 0.8, "Zoom (out <-> in)", tier="regional"),
        "spin": num(-1.0, 1.0, 0.5, "Spin", tier="regional"),
        "invert": num(0.0, 1.0, 0.0, "Neon -> ink on paper", tier="regional"),
        "vignette": num(0.0, 1.0, 0.55, "Vignette", tier="regional"),
        **standard_scene_params(palette="neon", render_scale=0.75, period=1.0, drift=0.03),
    }, ["symmetry_order", "overlay", "zoom_factor", "line_weight", "zoom_speed", "spin", "invert", "vignette",
        "energy", "pulse", "palette", "palette_period", "intensity", "speed", "drift_speed"])

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k_draw = raw_module(_SRC, _OPTIONS).get_function("mandala_draw")
        self.Ph = np.zeros(NP, np.float32)
        self.P = cp.zeros(NP, cp.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.zoom = float(rng.uniform(0.0, 1.0))
        self.rot = float(rng.uniform(0.0, 2 * math.pi))
        self.drift = 0.0
        self.es = None
        self.hist = np.zeros(HN, np.float32)
        self.hacc = 0.0
        self.ctm = [math.floor(self.zoom + o) for o in LAYER_OFFSETS]
        self.coff = [0.31 * j for j in range(3)]

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        a = 1.0 - math.exp(-dt / 0.35) if dt > 0 else 1.0
        self.es = e if self.es is None else self.es + (e - self.es) * a
        es = self.es

        # host-integrated phases (pulse adds to the zoom rate only)
        zrate = float(p["zoom_speed"]) * (0.1 + 0.9 * e ** 1.2) * (1.0 + 0.6 * pulse)
        self.zoom += zrate * spd * dt
        self.rot = _wrap(self.rot + float(p["spin"]) * (0.04 + 0.2 * e) * spd * dt, 2 * math.pi)
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * spd * dt, 10.0)

        # pulse history on a fixed time grid, newest first (dt = 0: no step)
        h = self.hist
        if dt > 0:
            self.hacc += min(dt, 2.0)
            n = int(self.hacc / HDT)
            if n > 0:
                self.hacc -= n * HDT
                n = min(n, HN)
                prev = float(h[0])
                if n < HN:
                    h[n:] = h[:-n].copy()
                h[:n] = np.linspace(pulse, prev, n, endpoint=False, dtype=np.float32)
        h[0] = pulse

        P = self.Ph
        pal_rows = self.session_palette()
        P[0:12] = (np.asarray(pal_rows, np.float32).reshape(12) if pal_rows is not None
                   else palette_coeffs(p["palette"]))
        period = float(p["palette_period"])
        kp = 0.15 * period
        P[12] = kp
        P[13] = self.drift
        P[14], P[15] = math.cos(self.rot), math.sin(self.rot)
        zf = min(max(float(p["zoom_factor"]), 1.2), 3.0)
        lz = math.log(zf)
        P[16], P[17], P[18] = lz, 1.0 / lz, zf

        # symmetry order: cross-blend the neighbouring integers
        nfl = min(max(float(p["symmetry_order"]), 6.0), 16.0)
        n0 = min(int(math.floor(nfl)), 15)
        P[19] = _ss(0.15, 0.85, nfl - n0)
        for k, n in enumerate((n0, n0 + 1)):
            hk = math.pi / n
            xc, rk = _kite(n, zf)
            P[20 + 5 * k: 25 + 5 * k] = (hk, math.cos(hk), math.sin(hk), xc, rk)
        P[30] = period

        # zoom layers: fixed offsets, each with a ring-id colour accumulator (exactly cancels the wrap of n)
        nl = 1.0 + 2.0 * es ** 1.3
        lw_gain = (1.0, 0.8, 0.65)
        for j, off in enumerate(LAYER_OFFSETS):
            ph = self.zoom + off
            ctm = math.floor(ph)
            if ctm != self.ctm[j]:
                self.coff[j] = _wrap(self.coff[j] - kp * (ctm - self.ctm[j]), 10.0)
                self.ctm[j] = ctm
            P[32 + 4 * j] = zf ** (ph - ctm)
            P[33 + 4 * j] = self.coff[j]
            P[34 + 4 * j] = _ss(0.0, 1.0, nl - j) * lw_gain[j]

        px = 2.0 / self.h
        s1080 = self.h / 1080.0
        lwp = float(p["line_weight"])
        glow = (0.45 + 0.55 * es) / (1.0 + 0.45 * (nl - 1.0))      # more layers -> each a little dimmer
        P[44] = (0.7 + 3.0 * lwp) * s1080 * px
        P[45] = (6.0 + 10.0 * es + 6.0 * lwp) * s1080 * px
        P[46] = 0.2 + 0.16 * es
        P[47] = 1.05 * glow
        P[48] = 0.9 * glow
        P[49] = 0.85 * glow
        o = min(max(float(p["overlay"]), 0.0), 1.0)
        for i, c in enumerate((1.0 / 3.0, 2.0 / 3.0, 1.0)):
            P[50 + i] = _ss(0.0, 1.0, 1.0 - 3.0 * abs(o - c))
        P[53] = min(max(float(p["invert"]), 0.0), 1.0)
        P[54] = min(max(float(p["vignette"]), 0.0), 1.0)
        P[55] = float(p["intensity"])
        P[56] = 1.4 + 0.6 * e
        P[57] = 0.085
        P[58] = 1.0 / HDT
        P[59] = px
        P[60] = 0.5 + 0.3 * es
        P[61] = self.w / self.h
        P[62] = 0.35
        P[PH:PH + HN] = h
        P[PH + HN] = h[-1]
        self.P.set(P)

        launch_2d(self.k_draw, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)
