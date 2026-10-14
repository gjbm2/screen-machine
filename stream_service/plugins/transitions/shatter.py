"""Shatter: the old picture cracks from an impact point into Voronoi glass shards that spin, tumble, recede and fly
off with thin palette-tinted edge glints, revealing the new scene behind them.

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/fragment.glsl
Author: lbl
License: MIT   (file header says "// License: MIT"; repo LICENSE: "MIT License, Copyright (c)
                2017-present gl-transitions contributors", https://github.com/gl-transitions/gl-transitions/blob/master/LICENSE)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: fragment.glsl's per-pixel O(N^2) nearest-point test over 10 fract(sin())
  points, each Voronoi fragment sliding off along a random direction, is rebuilt so it costs O(N) per pixel:
  6-32 host-seeded sites (Mitchell best-candidate, denser near the impact so shards are small at the hit and
  large at the rim) are clipped into exact Voronoi polygons on the host in setup; every frame the host computes
  each shard's affine motion (translation along a direction that blends radial-from-impact -> one global
  direction with `spread`, an in-plane spin, a fake 3D tumble that foreshortens the shard across a random axis,
  and a depth recession that shrinks it) and transforms the shard's edge half-planes into screen space, so the
  kernel tests each shard's few edges exactly (signed pixel distances: exact anti-aliasing and glint widths),
  composites shards front-to-back by depth with coverage, and inverse-maps the accepted pixel into the old
  picture.  Added: a crack phase (crack lines race out from the impact over the first ~12 % and each shard
  jolts a few pixels as the crack passes it), launch delays staggered by distance from the impact (radial) or
  by position along the blow direction, palette-tinted (shared cosine palette, drifting with progress) screen-
  blend edge glints that flare with each shard's tumble specular, a facing/recession shade, and a dimmed new
  scene that lifts to full as the glass clears.  Every shard's travel is computed to clear the frame before its
  flight ends (<= p 0.975), and endpoints are forced on the host: p <= 0 copies a, p >= 1 copies b.)

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

Binder: style wild, 1-2 beats on a drop, starting on a downbeat.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num

MAX_S = 32          # shards
MAX_E = 24          # edges per shard polygon (Voronoi cell clipped by the frame)
_DYN = 16           # floats per shard per frame
_CRACK_END = 0.12   # the crack network has spread over the whole frame by this progress
_END = 0.975        # every shard has left the frame by this progress

_SRC = HELPERS + PALETTE_CUDA + r"""
#define MAX_S 32
#define MAX_E 24
#define DYN 16

extern "C" __global__
void shatter(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
             const float* __restrict__ buf, const int n, const int W, const int H,
             const float ix, const float iy, const float crackR, const float front_w, const float crack_in,
             const float invH, const float glint, const float gw, const float kb, const float drift,
             const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float qx = x + 0.5f, qy = y + 0.5f;
    const float* PL = buf + MAX_S * DYN;
    float cr = 0.f, cg = 0.f, cb = 0.f, acc = 0.f;
    for (int k = 0; k < n; ++k) {                                    // shards, front to back
        const float* D = buf + k * DYN;
        const float dx = qx - D[1], dy = qy - D[2];
        if (dx * dx + dy * dy > D[3]) continue;                      // outside the shard's bounding circle
        const int ne = (int)D[14];
        const float* E = PL + k * (MAX_E * 4);
        const float lam = D[12];
        const float gb = (1.f - lam) * 1.0e4f;                       // frame-border edges glint only in flight
        float sdm = 1.0e9f, sdg = 1.0e9f;
        bool in = true;
        for (int e = 0; e < ne; ++e, E += 4) {                       // screen-space half-planes, signed px
            const float sd = E[2] - (E[0] * qx + E[1] * qy);
            if (sd < -1.f) { in = false; break; }
            sdm = fminf(sdm, sd);
            sdg = fminf(sdg, sd + E[3] * gb);
        }
        if (!in) continue;
        const float cov = clamp01(sdm + 1.f);                        // 1 px fattened: no seams between shards
        const float ux = D[4] * qx + D[5] * qy + D[8], uy = D[6] * qx + D[7] * qy + D[9];
        float4 c = bilin(a, ux, uy, W, H);
        const float sh = D[10], ff = D[11];
        c.x *= sh; c.y *= sh; c.z *= sh;
        if (ff > 0.f) { c.x += (255.f - c.x) * ff; c.y += (255.f - c.y) * ff; c.z += (255.f - c.z) * ff; }
        if (glint > 0.f) {
            const float line = 1.f - smooth(0.f, gw, fmaxf(sdg, 0.f));
            if (line > 0.f) {
                const float rx = ux - ix, ry = uy - iy;
                const float r = sqrtf(rx * rx + ry * ry);
                const float cv = 1.f - smooth(crackR - 0.5f * front_w, crackR + 0.5f * front_w, r);
                const float fr = (crackR - r) / front_w;
                const float crack = crack_in * cv * (0.55f + 1.2f * __expf(-fr * fr));
                const float gi = clamp01(glint * line * ((1.f - lam) * crack + lam * D[13]));
                if (gi > 0.f) {
                    const float3 pc = pal(r * invH * 1.3f + drift + 0.17f * D[0], P);
                    const float gr = gi * (0.45f + 0.55f * pc.x), gg = gi * (0.45f + 0.55f * pc.y),
                                gbl = gi * (0.45f + 0.55f * pc.z);
                    c.x += (255.f - c.x) * gr; c.y += (255.f - c.y) * gg; c.z += (255.f - c.z) * gbl;
                }
            }
        }
        const float wgt = (1.f - acc) * cov;
        cr += wgt * c.x; cg += wgt * c.y; cb += wgt * c.z;
        acc += wgt;
        if (acc >= 0.999f) break;
    }
    const float rest = 1.f - acc;
    if (rest > 1.0e-4f) {                                            // the new scene behind the glass
        const float4 nb = ld(b, x, y, W, H);
        const float w = rest * kb;
        cr += w * nb.x; cg += w * nb.y; cb += w * nb.z;
    }
    out[y * W + x] = pack(make_float4(cr, cg, cb, 255.f));
}
"""


def _sm(e0, e1, x):
    t = np.clip((np.asarray(x, np.float64) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _norm3(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


_L = _norm3((-0.4, -0.6, 0.7))                        # key light from the upper left (y-down screen)
_HV = np.array(_norm3((_L[0], _L[1], _L[2] + 1.0)))   # half vector, viewer straight ahead
_SPEC_POW = 24.0
_SPEC_BASE = _HV[2] ** _SPEC_POW                       # flat glass gives nothing


def _clip(poly, ax, ay, c):
    """Sutherland-Hodgman: keep ax*x + ay*y <= c."""
    out = []
    n = len(poly)
    for k in range(n):
        px, py = poly[k]
        qx, qy = poly[(k + 1) % n]
        fp = ax * px + ay * py - c
        fq = ax * qx + ay * qy - c
        if fp <= 0.0:
            out.append((px, py))
        if (fp <= 0.0) != (fq <= 0.0):
            t = fp / (fp - fq)
            out.append((px + t * (qx - px), py + t * (qy - py)))
    return out


def _centroid(poly):
    a = cx = cy = 0.0
    n = len(poly)
    for k in range(n):
        x0, y0 = poly[k]
        x1, y1 = poly[(k + 1) % n]
        cr = x0 * y1 - x1 * y0
        a += cr
        cx += (x0 + x1) * cr
        cy += (y0 + y1) * cr
    if abs(a) < 1e-12:
        return float(np.mean([p[0] for p in poly])), float(np.mean([p[1] for p in poly]))
    return cx / (3.0 * a), cy / (3.0 * a)


class Shatter(Transition):
    name = "shatter"
    label = "Shatter"
    description = ("The old picture smashes into glass shards that spin, recede and fly off with thin edge glints, "
                   "revealing the new scene behind them.")
    default_duration_s = 1.2
    params = {
        "spread": num(0.0, 1.0, 0.25, "Spread (radial -> one direction)", tier="regional", basic=True),
        "fly_speed": num(0.5, 2.0, 1.0, "Fly speed", unit="×", tier="regional"),
        "spin": num(0.0, 2.0, 1.0, "Spin", unit="rad", tier="regional"),
        "depth_shrink": num(0.0, 1.0, 0.6, "Depth (shards recede)", tier="regional"),
        "edge_glint": num(0.0, 1.0, 0.7, "Edge glint", tier="regional"),
        "center_x": num(0.0, 1.0, 0.5, "Impact x", tier="regional"),
        "center_y": num(0.0, 1.0, 0.45, "Impact y", tier="regional"),
        "palette": enum(list(PALETTES), "ice", "Glint palette", tier="regional"),
        "shard_density": num(6, MAX_S, 16, "Shard density (shards)", tier="structural", integer=True),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("shatter")
        self._buf = cp.zeros(MAX_S * _DYN + MAX_S * MAX_E * 4, dtype=cp.float32)
        self._host = np.zeros(MAX_S * _DYN + MAX_S * MAX_E * 4, np.float32)
        self._P = cp.zeros(12, cp.float32)
        self._pal = None
        s = int(self.params.get("seed", -1))
        self._seed = int(np.random.default_rng().integers(0, 2**31)) if s < 0 else s
        self._geo_key = None
        self._build(ctx.width, ctx.height)

    # ------------------------------------------------------------------ host: shard geometry (structural)
    def _build(self, w: int, h: int) -> None:
        p = self.params
        n = int(min(max(int(p.get("shard_density", 16)), 6), MAX_S))
        key = (w, h, n, self._seed)
        if key == self._geo_key:
            return
        A = w / h
        rng = np.random.default_rng(self._seed)
        c0 = np.array([float(p.get("center_x", 0.5)) * A, float(p.get("center_y", 0.45))])
        corners = np.array([[0, 0], [A, 0], [0, 1], [A, 1]], np.float64)
        rfull = float(np.max(np.hypot(*(corners - c0).T)))
        # Mitchell best-candidate sites, denser near the impact (small shards at the hit, big ones at the rim)
        sites = [c0 + rng.normal(0.0, 0.03, 2)]
        for _ in range(n - 1):
            m = 14
            radial = rng.random(m) < 0.45
            ang = rng.uniform(0, 2 * math.pi, m)
            rr = rfull * rng.random(m) ** 1.6
            cand = np.where(radial[:, None], c0 + np.stack([np.cos(ang), np.sin(ang)], 1) * rr[:, None],
                            np.stack([rng.uniform(0, A, m), rng.uniform(0, 1, m)], 1))
            cand[:, 0] = np.clip(cand[:, 0], 0.01, A - 0.01)
            cand[:, 1] = np.clip(cand[:, 1], 0.01, 0.99)
            S = np.array(sites)
            md = np.min(np.hypot(cand[:, None, 0] - S[None, :, 0], cand[:, None, 1] - S[None, :, 1]), 1)
            local = 0.4 + 0.6 * np.minimum(np.hypot(*(cand - c0).T) / (0.5 * rfull), 1.0)
            sites.append(cand[int(np.argmax(md / local))])
        S = np.array(sites)
        S[:, 0] = np.clip(S[:, 0], 0.005, A - 0.005)
        S[:, 1] = np.clip(S[:, 1], 0.005, 0.995)

        cen = np.zeros((n, 2))
        rad = np.zeros(n)
        planes = np.zeros((n, MAX_E, 4))     # nx, ny, d (inside: n.u <= d, height units), border flag
        ne = np.zeros(n, np.int32)
        eps = 1e-6
        for i in range(n):
            poly = [(0.0, 0.0), (A, 0.0), (A, 1.0), (0.0, 1.0)]
            si = S[i]
            for j in range(n):
                if j == i:
                    continue
                dv = S[j] - si
                if dv @ dv < 1e-14:
                    continue
                mid = 0.5 * (S[j] + si)
                poly = _clip(poly, dv[0], dv[1], float(mid @ dv))
                if len(poly) < 3:
                    break
            # drop near-duplicate vertices
            clean = []
            for v in poly:
                if not clean or math.hypot(v[0] - clean[-1][0], v[1] - clean[-1][1]) > 1e-7:
                    clean.append(v)
            if len(clean) > 1 and math.hypot(clean[0][0] - clean[-1][0], clean[0][1] - clean[-1][1]) <= 1e-7:
                clean.pop()
            if len(clean) < 3:                                   # degenerate (coincident sites): a tiny dot
                clean = [(si[0] - 1e-4, si[1] - 1e-4), (si[0] + 1e-4, si[1] - 1e-4), (si[0], si[1] + 1e-4)]
            cx, cy = _centroid(clean)
            cen[i] = (cx, cy)
            rad[i] = max(math.hypot(v[0] - cx, v[1] - cy) for v in clean)
            k = 0
            m = len(clean)
            for e in range(m):
                if k >= MAX_E:
                    break
                x0, y0 = clean[e]
                x1, y1 = clean[(e + 1) % m]
                ex, ey = x1 - x0, y1 - y0
                le = math.hypot(ex, ey)
                if le < 1e-9:
                    continue
                nx, ny = ey / le, -ex / le
                if nx * (cx - x0) + ny * (cy - y0) > 0.0:         # point the normal outward
                    nx, ny = -nx, -ny
                border = ((x0 < eps and x1 < eps) or (x0 > A - eps and x1 > A - eps)
                          or (y0 < eps and y1 < eps) or (y0 > 1 - eps and y1 > 1 - eps))
                planes[i, k] = (nx, ny, nx * x0 + ny * y0, 1.0 if border else 0.0)
                k += 1
            ne[i] = k

        self._n = n
        self._cen, self._rad, self._planes, self._ne = cen, rad, planes, ne
        # per-shard randomness (structural, from the seed)
        self._jdir = rng.normal(0, 1, (n, 2))
        self._jdir /= np.maximum(np.hypot(*self._jdir.T), 1e-9)[:, None]
        self._jsg = rng.choice((-1.0, 1.0), n) * rng.uniform(0.5, 1.0, n)
        self._rang = rng.uniform(-math.pi, math.pi, n)           # radial angle for a shard right at the hit
        self._jang = rng.uniform(-0.35, 0.35, n)                 # direction scatter
        self._rd = rng.random(n)                                  # launch-delay scatter
        self._vel = rng.uniform(1.0, 1.5, n)                      # travel beyond the exit distance
        self._z = rng.uniform(1.5, 3.5, n)                        # recession rate
        self._rot = rng.choice((-1.0, 1.0), n) * rng.uniform(0.5, 1.0, n)
        self._tmb = rng.choice((-1.0, 1.0), n) * rng.uniform(0.3, 1.0, n)
        self._axis = rng.uniform(0, math.pi, n)
        ga = rng.uniform(0, 2 * math.pi)
        self._gdir = np.array([math.cos(ga), math.sin(ga)])
        self._geo_key = key

    def _sync_palette(self) -> None:
        name = self.params.get("palette", "ice")
        if name not in PALETTES:
            name = "ice"
        if name != self._pal:
            self._P.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal = name

    # ------------------------------------------------------------------ host: per-frame shard motion
    def _motion(self, P: float, w: int, h: int):
        p = self.params
        A = w / h
        n = self._n
        cen, rad = self._cen, self._rad
        impact = np.array([min(max(float(p["center_x"]), 0.0), 1.0) * A, min(max(float(p["center_y"]), 0.0), 1.0)])
        corners = np.array([[0, 0], [A, 0], [0, 1], [A, 1]], np.float64)
        rfull = float(np.max(np.hypot(*(corners - impact).T))) + 0.05
        spread = min(max(float(p["spread"]), 0.0), 1.0)
        fly = min(max(float(p["fly_speed"]), 0.5), 2.0)
        spin = min(max(float(p["spin"]), 0.0), 2.0)
        depth = min(max(float(p["depth_shrink"]), 0.0), 1.0)
        glint = min(max(float(p["edge_glint"]), 0.0), 1.0)

        rel = cen - impact
        dist = np.hypot(rel[:, 0], rel[:, 1])
        xc = min(P / _CRACK_END, 1.0)
        crackR = rfull * (1.0 - (1.0 - xc) ** 2)                   # ease-out: the crack races then slows
        pc = _CRACK_END * (1.0 - np.sqrt(1.0 - np.clip(dist / rfull, 0.0, 1.0)))   # crack reaches the centroid
        jolt = _sm(pc, pc + 0.04, P)

        gv = (np.array([0.5 * A, 0.5]) - impact) + 0.25 * self._gdir
        gdir = gv / max(float(np.hypot(*gv)), 1e-9)
        ag = math.atan2(gdir[1], gdir[0])
        ar = np.where(dist > 0.02, np.arctan2(rel[:, 1], rel[:, 0]), self._rang)
        delta = (ag - ar + math.pi) % (2 * math.pi) - math.pi
        ang = ar + spread * delta + self._jang * (1.0 - 0.6 * spread)
        dirv = np.stack([np.cos(ang), np.sin(ang)], 1)

        rn = dist / max(float(dist.max()), 1e-6)
        proj = cen @ gdir
        dn = (proj.max() - proj) / max(float(np.ptp(proj)), 1e-6)  # leading shards go first
        dl = 0.05 + 0.30 * (rn + spread * (dn - rn)) + 0.05 * self._rd
        dl = np.maximum(dl, pc + 0.03)
        fsc = min(max((0.5 / fly) ** 0.7, 0.3), 1.0)
        F = np.maximum((_END - dl) * fsc, 1e-3)
        tau = np.clip((P - dl) / F, 0.0, 1.0)
        g = 0.3 * tau + 0.7 * tau * tau                            # a kick, then accelerating away
        lam = _sm(0.0, 0.1, tau)

        # travel: enough to carry the shard's bounding circle out of the frame along its direction
        dx, dy = dirv[:, 0], dirv[:, 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            tx = np.where(dx > 1e-6, (A + rad - cen[:, 0]) / dx, np.where(dx < -1e-6, (cen[:, 0] + rad) / -dx, np.inf))
            ty = np.where(dy > 1e-6, (1.0 + rad - cen[:, 1]) / dy, np.where(dy < -1e-6, (cen[:, 1] + rad) / -dy, np.inf))
        texit = np.minimum(tx, ty)
        L = (np.maximum(texit, 0.0) + 0.03) * self._vel
        T = dirv * (L * g)[:, None] + self._jdir * (0.004 * jolt)[:, None]
        s = 1.0 / (1.0 + depth * self._z * g)
        th = 0.012 * self._jsg * jolt + spin * self._rot * tau
        ph = spin * 0.85 * self._tmb * tau
        cph = np.cos(ph)
        ce = np.where(cph >= 0.0, 1.0, -1.0) * np.maximum(np.abs(cph), 0.04)

        # inverse affine (screen -> source): Minv = Ra diag(1, 1/ce) Ra^T R(th)^T / s
        ca, sa = np.cos(self._axis), np.sin(self._axis)
        Ra = np.stack([np.stack([ca, -sa], 1), np.stack([sa, ca], 1)], 1)
        Dg = np.zeros((n, 2, 2))
        Dg[:, 0, 0] = 1.0
        Dg[:, 1, 1] = 1.0 / ce
        Sinv = Ra @ Dg @ np.transpose(Ra, (0, 2, 1))
        ct, st = np.cos(th), np.sin(th)
        Rt = np.stack([np.stack([ct, st], 1), np.stack([-st, ct], 1)], 1)
        Minv = (Sinv @ Rt) / s[:, None, None]
        o = cen + T                                                 # screen centroid (height units)

        # shading: facing + recession, and a tumble specular that flares the faces and edges
        a2 = self._axis + th
        sph = np.sin(ph)
        nrm = np.stack([-np.sin(a2) * sph, np.cos(a2) * sph, cph], 1)
        spec = np.clip((np.abs(nrm @ _HV) ** _SPEC_POW - _SPEC_BASE) / (1.0 - _SPEC_BASE), 0.0, 1.0)
        shade = (0.65 + 0.35 * s) * (0.7 + 0.3 * np.abs(cph))
        face = 0.3 * glint * spec * lam
        fglint = 0.35 + 0.9 * spec

        # screen-space (pixel) half-planes
        nv = self._planes[:, :, :2]
        dd = self._planes[:, :, 2]
        m = np.einsum("nij,nei->nej", Minv, nv)                    # Minv^T n
        ds = dd - np.einsum("nei,ni->ne", nv, cen) + np.einsum("nei,ni->ne", m, o)
        ms = m / h
        ln = np.maximum(np.hypot(ms[..., 0], ms[..., 1]), 1e-12)
        pl = np.empty((n, MAX_E, 4))
        pl[..., 0] = ms[..., 0] / ln
        pl[..., 1] = ms[..., 1] / ln
        pl[..., 2] = ds / ln
        pl[..., 3] = self._planes[:, :, 3]

        opx = o * h
        rpx = rad * h * s + 2.0
        vis = (opx[:, 0] + rpx > 0) & (opx[:, 0] - rpx < w) & (opx[:, 1] + rpx > 0) & (opx[:, 1] - rpx < h)
        idx = np.flatnonzero(vis)
        idx = idx[np.argsort(-s[idx], kind="stable")]               # front (least receded) first

        tb = h * (cen - np.einsum("nij,nj->ni", Minv, o))
        hb = self._host
        hb[:] = 0.0
        D = hb[:MAX_S * _DYN].reshape(MAX_S, _DYN)
        PLh = hb[MAX_S * _DYN:].reshape(MAX_S, MAX_E, 4)
        k = len(idx)
        if k:
            D[:k, 0] = idx
            D[:k, 1] = opx[idx, 0]
            D[:k, 2] = opx[idx, 1]
            D[:k, 3] = rpx[idx] ** 2
            D[:k, 4] = Minv[idx, 0, 0]
            D[:k, 5] = Minv[idx, 0, 1]
            D[:k, 6] = Minv[idx, 1, 0]
            D[:k, 7] = Minv[idx, 1, 1]
            D[:k, 8] = tb[idx, 0]
            D[:k, 9] = tb[idx, 1]
            D[:k, 10] = shade[idx]
            D[:k, 11] = face[idx]
            D[:k, 12] = lam[idx]
            D[:k, 13] = fglint[idx]
            D[:k, 14] = self._ne[idx]
            PLh[:k] = pl[idx]
        self._buf.set(hb)
        return k, impact * h, crackR * h, glint

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 0.0:
            cp.copyto(out, a)
            return
        if progress >= 1.0:
            cp.copyto(out, b)
            return
        w, h = self.ctx.width, self.ctx.height
        self._build(w, h)
        self._sync_palette()
        P = float(progress)
        k, imp, crackR, glint = self._motion(P, w, h)
        front_w = 0.035 * h
        crack_in = float(_sm(0.0, 0.02, P))
        gw = max(1.5, 2.6 * h / 2160.0)
        kb = 1.0 - 0.3 * (1.0 - float(_sm(0.3, 0.92, P)))
        drift = (0.6 * P) % 1.0
        launch_2d(self._k, w, h,
                  (a, b, out, self._buf, np.int32(k), np.int32(w), np.int32(h),
                   np.float32(imp[0]), np.float32(imp[1]), np.float32(crackR), np.float32(front_w),
                   np.float32(crack_in), np.float32(1.0 / h), np.float32(glint), np.float32(gw), np.float32(kb),
                   np.float32(drift), self._P))
