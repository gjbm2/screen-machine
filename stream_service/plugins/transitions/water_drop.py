"""Water drop: a drop hits the surface and refracting rings spread out with a specular glint, carrying the new
picture in with them; several drops can land in a cascade.

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/WaterDrop.glsl
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/ripple.glsl
Author: Paweł Płóciennik (WaterDrop); gre (ripple)
License: MIT   (both file headers say "// License: MIT"; repo LICENSE: "MIT License, Copyright (c)
                2017-present gl-transitions contributors", https://github.com/gl-transitions/gl-transitions/blob/master/LICENSE)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: WaterDrop's rings, offset = dir * sin(dist * amplitude - progress * speed)
  inside an expanding disk of radius progress, with ripple's progress-scaled offset envelope, are rebuilt as a
  small water-surface model in aspect-correct (height-normalised) space on y-down pixel frames.  Each drop's ring
  train is a radial wave h = A sin(k r - w q) behind its own front R = v q (q = progress since that drop landed);
  the displacement is the wave's slope along the unit radius (not the originals' unnormalised dir), so depth is
  the true peak refraction in screen heights; the leading ring tapers in over half a wavelength so the front has
  no tear, the impact point is tapered, and damping decays the train behind the front (0 = the whole disk keeps
  rippling, like WaterDrop; 1 = one short packet, like a ring).  Instead of WaterDrop's global crossfade the new
  picture is revealed by the fronts themselves (the union of the disks, sampled at the refracted point so the
  boundary bends under the rings).  Added: a specular glint and a relief shade from the slope normal (a key light
  from the upper left), 1-5 host-seeded drops staggered in time that land where the old picture still shows and
  superpose (interference), per-drop wavelength/amplitude variation, a global calm-down over the last ~30 % so
  the endpoints are pure; ring_frequency is k in rad per screen height and wave_speed is w in rad per unit
  progress, so the defaults 30 / 30 keep WaterDrop's ring/front speed ratio.)

Geometry: the primary drop lands at (center_x, center_y) at progress 0 and its front reaches the farthest corner
at progress ~0.85, so b covers the frame before the end; drop i > 0 lands at i * stagger (capped so the last one
lands by 0.6) at a host-seeded point outside every disk open at that moment.  Endpoints: p <= 0 copies a,
p >= 1 copies b, and the wave amplitude fades to 0 before p = 1, so the end is continuous.

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
from stream_service.plugins._lib.params import num

MAX_DROPS = 5
_STRIDE = 8            # floats per drop: cx, cy (height units), start, amp, k_mul, pad...
_FRONT_END = 0.85      # progress at which the primary front clears the farthest corner
_FADE0 = 0.70          # the surface calms over [_FADE0, 1]

_SRC = HELPERS + r"""
#define TWO_PI 6.2831853f
#define INV_TWO_PI 0.15915494f

extern "C" __global__
void water_drop(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
                const float* __restrict__ drops, const int nd, const int W, const int H, const float p,
                const float k, const float w, const float v, const float soft, const float dampk,
                const float depth_px, const float tilt, const float glint, const float gend,
                const float hx, const float hy, const float hz, const float spec_base, const float spec_norm,
                const float lx, const float ly, const float lz)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int i = y * W + x;
    const float invH = 1.f / H;
    const float px = (x + 0.5f) * invH, py = (y + 0.5f) * invH;     // height-normalised: rings stay round

    // 1) surface slope (gradient of the summed ring trains), in height units per height unit
    float gx = 0.f, gy = 0.f;
    bool inside = false;
    for (int d = 0; d < nd; ++d) {
        const float* D = drops + d * 8;
        const float q = p - D[2];
        if (q <= 0.f) continue;                                       // not landed yet
        const float R = v * q;
        const float dx = px - D[0], dy = py - D[1];
        const float r2 = dx * dx + dy * dy;
        if (r2 >= R * R) continue;                                    // ahead of the front: calm water
        inside = true;
        const float r = sqrtf(r2) + 1e-6f;
        const float kd = k * D[4];
        const float lam = TWO_PI / kd;
        const float behind = R - r;
        float env = D[3] * smooth(0.f, 0.5f * lam, behind)            // leading ring tapers in
                  * __expf(-dampk * behind)                           // older rings decay behind the front
                  * smooth(0.f, 0.35f * lam, r)                       // no singularity at the impact point
                  * smooth(0.f, 0.04f, q);                            // the splash builds as it lands
        float ph = kd * r - w * q;
        ph -= TWO_PI * rintf(ph * INV_TWO_PI);                        // range-reduce for the fast cosine
        const float s = env * __cosf(ph) / r;
        gx += s * dx; gy += s * dy;
    }
    if (!inside) { out[i] = a[i]; return; }                          // untouched old picture
    gx *= gend; gy *= gend;
    const float gm = sqrtf(gx * gx + gy * gy);
    if (gm > 1.6f) { const float f = 1.6f / gm; gx *= f; gy *= f; } // overlapping drops: keep it sane

    // 2) refract: sample at the displaced point; the reveal uses the displaced point too, so the edge bends
    const float sx = x + 0.5f + gx * depth_px, sy = y + 0.5f + gy * depth_px;
    const float spx = sx * invH, spy = sy * invH;
    float m = 0.f;
    for (int d = 0; d < nd; ++d) {
        const float* D = drops + d * 8;
        const float q = p - D[2];
        if (q <= 0.f) continue;
        const float R = v * q;
        const float dx = spx - D[0], dy = spy - D[1];
        m = fmaxf(m, smooth(R, R - soft, sqrtf(dx * dx + dy * dy)));
    }
    float4 c;
    if (m <= 0.f) c = bilin(a, sx, sy, W, H);
    else if (m >= 1.f) c = bilin(b, sx, sy, W, H);
    else c = mix4(bilin(a, sx, sy, W, H), bilin(b, sx, sy, W, H), m);

    // 3) light: relief shade and a specular glint from the slope normal (only where the surface moves)
    if (glint > 0.f && gm > 1e-5f) {
        const float nx = -gx * tilt, ny = -gy * tilt;
        const float inv = rsqrtf(nx * nx + ny * ny + 1.f);
        const float ndl = (nx * lx + ny * ly + lz) * inv - lz;        // 0 on flat water
        const float ndh = fmaxf((nx * hx + ny * hy + hz) * inv, 0.f);
        const float sp = fmaxf(__powf(ndh, 56.f) - spec_base, 0.f) * spec_norm;
        const float shade = 1.f + 0.55f * glint * ndl;
        const float add = 300.f * glint * sp;
        c = make_float4(c.x * shade + add, c.y * shade + add * 0.98f, c.z * shade + add * 0.93f, 255.f);
    }
    out[i] = pack(c);
}
"""


def _norm(v):
    n = math.sqrt(sum(x * x for x in v))
    return tuple(x / n for x in v)


_L = _norm((-0.45, -0.65, 0.62))                     # key light from the upper left (y-down screen)
_HV = _norm((_L[0], _L[1], _L[2] + 1.0))             # half vector with the viewer straight above
_SPEC_BASE = _HV[2] ** 56                             # flat water gives (almost) nothing
_SPEC_NORM = 1.0 / (1.0 - _SPEC_BASE)


class WaterDrop(Transition):
    name = "water_drop"
    label = "Water drop"
    description = ("A drop hits the surface and refracting rings spread out with a specular glint, carrying the "
                   "new picture in with them; several drops can splash in a cascade.")
    default_duration_s = 2.0
    params = {
        "depth": num(0.0, 0.1, 0.03, "Refraction depth", step=0.001, unit="× height", tier="regional", basic=True),
        "ring_frequency": num(10.0, 80.0, 30.0, "Ring frequency", step=0.5, unit="rad/height", tier="regional"),
        "wave_speed": num(10.0, 80.0, 30.0, "Wave speed", step=0.5, tier="regional"),
        "damping": num(0.0, 1.0, 0.35, "Damping (whole disk -> one ring)", tier="regional"),
        "glint": num(0.0, 1.0, 0.6, "Glint", tier="regional"),
        "stagger": num(0.0, 0.3, 0.2, "Drop stagger (fraction of the transition)", tier="regional"),
        "center_x": num(0.0, 1.0, 0.5, "Centre x", tier="regional"),
        "center_y": num(0.0, 1.0, 0.5, "Centre y", tier="regional"),
        "drops": num(1, MAX_DROPS, 1, "Drops", tier="structural", integer=True),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        self._k = raw_module(_SRC).get_function("water_drop")
        self._drops = ctx.cp.zeros(MAX_DROPS * _STRIDE, dtype=ctx.cp.float32)
        s = int(self.params.get("seed", -1))
        self._seed = int(np.random.default_rng().integers(0, 2**31)) if s < 0 else s
        self._key = None
        self._geo = None

    # ------------------------------------------------------------------ host-side drop layout (cached)
    def _layout(self, w: int, h: int):
        p = self.params
        n = int(min(max(int(p["drops"]), 1), MAX_DROPS))
        k = float(p["ring_frequency"])
        cx, cy = float(p["center_x"]), float(p["center_y"])
        stagger = float(p["stagger"])
        key = (w, h, n, k, cx, cy, stagger, self._seed)
        if key == self._key:
            return self._geo
        aspect = w / h
        lam = 2.0 * math.pi / k
        soft = min(max(0.5 * lam, 0.03), 0.2)
        c0 = (cx * aspect, cy)
        corners = [(x, y) for x in (0.0, aspect) for y in (0.0, 1.0)]
        rmax = max(math.hypot(x - c0[0], y - c0[1]) for x, y in corners)
        v = (rmax + soft + 0.02) / _FRONT_END                      # primary front clears the frame by _FRONT_END
        gap = min(stagger, 0.6 / (n - 1)) if n > 1 else 0.0
        rng = np.random.default_rng(self._seed)
        data = np.zeros((MAX_DROPS, _STRIDE), np.float32)
        data[0, :5] = (c0[0], c0[1], 0.0, 1.0, 1.0)
        placed = [(c0[0], c0[1], 0.0)]
        for j in range(1, n):
            s = j * gap
            cand = np.stack([rng.uniform(0.08 * aspect, 0.92 * aspect, 96), rng.uniform(0.08, 0.92, 96)], 1)
            # margin outside every disk already open when this drop lands (land where the old picture shows)
            marg = np.min(np.stack([np.hypot(cand[:, 0] - qx, cand[:, 1] - qy) - v * max(s - qs, 0.0)
                                    for qx, qy, qs in placed]), 0)
            ok = np.flatnonzero(marg > 0.15)
            pick = int(rng.choice(ok)) if ok.size else int(np.argmax(marg))
            px, py = float(cand[pick, 0]), float(cand[pick, 1])
            data[j, :5] = (px, py, s, rng.uniform(0.6, 0.9), rng.uniform(0.85, 1.2))
            placed.append((px, py, s))
        self._drops.set(data.ravel())
        self._geo = (n, v, soft)
        self._key = key
        return self._geo

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 0.0:
            cp.copyto(out, a)
            return
        if progress >= 1.0:
            cp.copyto(out, b)
            return
        p = self.params
        w, h = self.ctx.width, self.ctx.height
        n, v, soft = self._layout(w, h)
        depth = float(p["depth"])
        damping = float(p["damping"])
        dampk = 16.0 * damping ** 1.5                                # e-folding 1/16 height at damping 1
        t = min(max((progress - _FADE0) / (1.0 - _FADE0), 0.0), 1.0)
        gend = 1.0 - t * t * (3.0 - 2.0 * t)                          # the surface calms before the end
        tilt = 0.55 + 6.0 * depth                                     # deeper water, steeper crests
        launch_2d(self._k, w, h,
                  (a, b, out, self._drops, np.int32(n), np.int32(w), np.int32(h), np.float32(progress),
                   np.float32(p["ring_frequency"]), np.float32(p["wave_speed"]), np.float32(v), np.float32(soft),
                   np.float32(dampk), np.float32(depth * h), np.float32(tilt), np.float32(p["glint"]),
                   np.float32(gend), np.float32(_HV[0]), np.float32(_HV[1]), np.float32(_HV[2]),
                   np.float32(_SPEC_BASE), np.float32(_SPEC_NORM),
                   np.float32(_L[0]), np.float32(_L[1]), np.float32(_L[2])))
