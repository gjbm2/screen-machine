"""Infinity Mirror Room: a mirrored room hung with glowing LED spheres, reflected into an endless lattice.

Source: Original (inspired by https://en.wikipedia.org/wiki/Infinity_Mirror_Room)
Author: smfx (original; inspired by Yayoi Kusama's Infinity Mirror Rooms)
License: MIT (original)
license_class: permissive

Written from scratch (no shader code consulted).  How it works:
  * The room is a box (4 x 3 x 4 units) whose walls and ceiling are mirrors and whose floor is a mirror or, with
    `water`, black water.  A ray bouncing between mirrors is the same as a straight ray through an infinite lattice
    of reflected copies of the room ("unfolding"), so each pixel walks that lattice with a bounded 3D DDA
    (MAXC = 24 cells).  In cell c the room's contents are mirrored on every odd axis, so the ray is moved into the
    canonical room frame (flip by parity) and tested against the same light list held in shared memory.
  * Every crossing is one bounce: the throughput is multiplied by the wall tint (or the floor's water tint) and the
    lights in that cell get the pulse gain for that bounce count; a fade over the last cells hides the DDA bound.
  * room_shape skews the horizontal lattice from square (cube room) to 60 degrees (a rhombic/hex arrangement of
    reflections).  Distances use the skewed metric <a, b> = a.b + g (a_x b_z + a_z b_x), so lights stay spheres.
  * Each light: analytic closest approach of the ray segment in that cell -> anti-aliased solid core with a
    white-hot centre + 1/(1 + k d^2) halo; sub-pixel images are widened to ~1 px with energy conserved, so the
    far field is a stable starfield instead of sparkling aliasing.  Mirror-panel seams are drawn at each entry
    face (constant pixel width, fading with distance); a faint coloured haze adds depth.
  * Water: floor crossings use a darker, cooler reflectance and bend the ray by an analytic ripple gradient.

Realtime: energy -> twinkle and chase rates, brightness, lit-light count (fades lights in/out by weight), twinkle and
chase depth, halo, camera amplitude/rate.  pulse -> a flash wave radiating outward through the reflections: the
pulse envelope is kept in a short history against an integrated wave clock and bounce n shows the pulse from
n * delay ago, so each beat is a shell of light travelling out into the infinity (gains only; also a chase-rate
kick).  speed scales chase, twinkle, ripple, wave travel and camera drift; drift_speed rotates the LED palette.
Perf: render_scale 0.5, <= 24 cells x (lit lights) segment tests, no raymarch.  The spec suggested <= 4 glows per
cell via a sub-grid; whole-room cells with a cheap early cut are used instead (lights default 12).
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present

MAXL = 24               # lights per room (max)
MAXC = 24               # DDA cells per ray
NGAIN = 32              # per-bounce pulse gains
ROOM = (4.0, 3.0, 4.0)  # Lx, H, Lz (scaled lattice units == world units along each lattice axis)
WAVE_DELAY = 0.055      # wave-clock seconds per bounce

_SRC = HELPERS + r"""
#define MAXL %(maxl)d
#define MAXC %(maxc)d
#define NGAIN %(ngain)d

extern "C" __global__
void infinity_room(uchar4* __restrict__ out, const int w, const int h,
                   const float* __restrict__ P, const float* __restrict__ LG, const int nl)
{
    __shared__ float sL[MAXL * 8];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    for (int i = tid; i < nl * 8; i += blockDim.x * blockDim.y) sL[i] = LG[i];
    __syncthreads();
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;

    // camera ray (world), then into scaled-lattice coords (world x = u + w cos, z = w sin)
    const float tanH = P[12];
    const float asp = (float)w / (float)h;
    const float sx = (2.f * (x + 0.5f) / w - 1.f) * asp * tanH;
    const float sy = (1.f - 2.f * (y + 0.5f) / h) * tanH;
    float dwx = P[9] + sx * P[3] + sy * P[6];
    float dwy = P[10] + sx * P[4] + sy * P[7];
    float dwz = P[11] + sx * P[5] + sy * P[8];
    const float il = rsqrtf(dwx * dwx + dwy * dwy + dwz * dwz);
    dwx *= il; dwy *= il; dwz *= il;
    const float cs = P[13], sn = P[14];
    float dz = dwz / sn;
    float dx = dwx - dz * cs;
    float dy = dwy;
    float qx = P[0], qy = P[1], qz = P[2];
    const float Sx = P[15], Sy = P[16], Sz = P[17];
    const float pixA = P[19];
    int cx = 0, cy = 0, cz = 0;

    float Tr = 1.f, Tg = 1.f, Tb = 1.f;
    float ar = 0.f, ag = 0.f, ab = 0.f;
    float dist = 0.f;
    const float hk = P[36], fade0 = P[38];
    const float halo_base = 1.f / (1.f + 2.2f * 49.f);

    for (int n = 0; n < MAXC; ++n) {
        // exit time from the current cell
        float tx = 1e9f, ty = 1e9f, tz = 1e9f;
        if (dx > 1e-7f) tx = ((cx + 1) * Sx - qx) / dx; else if (dx < -1e-7f) tx = (cx * Sx - qx) / dx;
        if (dy > 1e-7f) ty = ((cy + 1) * Sy - qy) / dy; else if (dy < -1e-7f) ty = (cy * Sy - qy) / dy;
        if (dz > 1e-7f) tz = ((cz + 1) * Sz - qz) / dz; else if (dz < -1e-7f) tz = (cz * Sz - qz) / dz;
        tx = fmaxf(tx, 0.f); ty = fmaxf(ty, 0.f); tz = fmaxf(tz, 0.f);
        int ax; float te;
        if (tx <= ty && tx <= tz) { ax = 0; te = tx; } else if (ty <= tz) { ax = 1; te = ty; } else { ax = 2; te = tz; }
        te = fminf(te, 50.f);

        // canonical room frame for this cell (mirror on odd axes)
        const bool fx = (cx & 1) != 0, fy = (cy & 1) != 0, fz = (cz & 1) != 0;
        float lx = qx - cx * Sx, ly = qy - cy * Sy, lz = qz - cz * Sz;
        if (fx) lx = Sx - lx;
        if (fy) ly = Sy - ly;
        if (fz) lz = Sz - lz;
        const float ux = fx ? -dx : dx, uy = fy ? -dy : dy, uz = fz ? -dz : dz;
        const float g = (fx != fz) ? -cs : cs;
        const float dd = fmaxf(ux * ux + uy * uy + uz * uz + 2.f * g * ux * uz, 1e-6f);
        const float idd = 1.f / dd;
        const float sl = sqrtf(dd);

        const float fade = smooth((float)MAXC, fade0, (float)n);
        const float gn = P[40 + min(n, NGAIN - 1)];
        float cr = 0.f, cg = 0.f, cb = 0.f, occ = 1.f;
        for (int i = 0; i < nl; ++i) {
            const float* L = sL + i * 8;
            const float ex = lx - L[0], ey = ly - L[1], ez = lz - L[2];
            const float ed = ex * ux + ey * uy + ez * uz + g * (ex * uz + ez * ux);
            const float tc = fminf(fmaxf(-ed * idd, 0.f), te);
            const float hx = ex + ux * tc, hy = ey + uy * tc, hz = ez + uz * tc;
            const float d2 = fmaxf(hx * hx + hy * hy + hz * hz + 2.f * g * hx * hz, 0.f);
            const float pf = fmaxf((dist + tc * sl) * pixA, 1e-5f);
            const float reff = fmaxf(L[3], 0.75f * pf);
            const float r2 = reff * reff;
            if (d2 > 49.f * r2) continue;
            const float xx = d2 / r2;
            const float ke = L[3] * L[3] / r2;
            const float s = sqrtf(d2);
            const float cov = clamp01((reff - s) / pf + 0.5f);
            const float halo = hk * fmaxf(1.f / (1.f + 2.2f * xx) - halo_base, 0.f);
            float inner = fmaxf(1.f - xx, 0.f); inner *= inner;
            const float a = ke * (cov * 1.45f + halo * (1.f - cov));
            const float wht = fmaxf(L[4], fmaxf(L[5], L[6])) * 0.8f * inner * cov * ke;
            cr += L[4] * a + wht; cg += L[5] * a + wht; cb += L[6] * a + wht;
            occ *= 1.f - 0.92f * cov;
        }
        const float wgt = fade * gn;
        const float hz_ = fade * te * sl;
        ar += Tr * (cr * wgt + P[32] * hz_);
        ag += Tg * (cg * wgt + P[33] * hz_);
        ab += Tb * (cb * wgt + P[34] * hz_);
        Tr *= occ; Tg *= occ; Tb *= occ;

        // advance to the boundary and cross it (one bounce)
        qx += dx * te; qy += dy * te; qz += dz * te;
        dist += te * sl;
        if (fmaxf(Tr, fmaxf(Tg, Tb)) < 0.004f || n == MAXC - 1) break;
        bool floor_hit = false;
        if (ax == 0) { if (dx > 0.f) { cx += 1; qx = cx * Sx; } else { qx = cx * Sx; cx -= 1; } }
        else if (ax == 1) {
            int j;
            if (dy > 0.f) { cy += 1; j = cy; qy = cy * Sy; } else { j = cy; qy = cy * Sy; cy -= 1; }
            floor_hit = (j & 1) == 0;
        }
        else { if (dz > 0.f) { cz += 1; qz = cz * Sz; } else { qz = cz * Sz; cz -= 1; } }

        // mirror-panel seams at the entry face (constant pixel width, energy-conserving when sub-pixel)
        if (P[28] > 0.f) {
            const float ex_ = qx - cx * Sx, ey_ = qy - cy * Sy, ez_ = qz - cz * Sz;
            const float mx = fminf(ex_, Sx - ex_), my = fminf(ey_, Sy - ey_), mz = fminf(ez_, Sz - ez_);
            const float e = ax == 0 ? fminf(my, mz) : (ax == 1 ? fminf(mx, mz) : fminf(mx, my));
            const float pf = fmaxf(dist * pixA, 1e-5f);
            const float wl = fmaxf(0.006f, pf);
            const float k = (e / wl);
            const float line = __expf(-k * k) * fminf(1.f, 0.006f / pf) * fade * P[28] * (floor_hit ? 0.6f : 1.f);
            ar += Tr * P[29] * line; ag += Tg * P[30] * line; ab += Tb * P[31] * line;
        }

        if (floor_hit) {
            Tr *= P[23]; Tg *= P[24]; Tb *= P[25];
            const float amp = P[26];
            if (amp > 0.f) {
                // ripple gradient in the canonical floor tile (mirrored per tile, so continuous at seams)
                float px = qx - cx * Sx, pz = qz - cz * Sz;
                const bool mx_ = (cx & 1) != 0, mz_ = (cz & 1) != 0;
                if (mx_) px = Sx - px;
                if (mz_) pz = Sz - pz;
                const float ph = P[27];
                const float a1 = 5.1f * px + 1.7f * pz + ph;
                const float a2 = -2.2f * px + 6.3f * pz + 2.f * ph;
                const float a3 = 8.3f * px + 7.1f * pz - 3.f * ph;
                const float c1 = __cosf(a1), c2 = __cosf(a2), c3 = __cosf(a3);
                float gx = 5.1f * c1 - 0.6f * 2.2f * c2 + 0.3f * 8.3f * c3;
                float gz = 1.7f * c1 + 0.6f * 6.3f * c2 + 0.3f * 7.1f * c3;
                if (mx_) gx = -gx;
                if (mz_) gz = -gz;
                dx += amp * gx; dz += amp * gz;
                const float len2 = dx * dx + dy * dy + dz * dz + 2.f * cs * dx * dz;
                const float r = rsqrtf(fmaxf(len2, 1e-8f));
                dx *= r; dy *= r; dz *= r;
            }
        } else {
            Tr *= P[20]; Tg *= P[21]; Tb *= P[22];
        }
        if (fmaxf(Tr, fmaxf(Tg, Tb)) < 0.004f) break;
    }

    const float gain = P[35];
    const float dth = (hash1((unsigned)x, (unsigned)y, 911u) - 0.5f) * (1.f / 255.f);
    const float orr = 1.f - __expf(-ar * gain) + dth;
    const float ogg = 1.f - __expf(-ag * gain) + dth;
    const float obb = 1.f - __expf(-ab * gain) + dth;
    out[y * w + x] = pack(make_float4(orr * 255.f, ogg * 255.f, obb * 255.f, 255.f));
}
""" % {"maxl": MAXL, "maxc": MAXC, "ngain": NGAIN}
_OPTIONS = ("--std=c++14", "--use_fast_math")


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _pal(t, P: np.ndarray) -> np.ndarray:
    """Host copy of PALETTE_CUDA's pal(): t (N,) -> (N, 3) in 0..1."""
    t = np.asarray(t, np.float64)[:, None]
    a, b, c, d = P[0:3], P[3:6], P[6:9], P[9:12]
    return np.clip(a + b * np.cos(2 * np.pi * (c * t + d)), 0.0, 1.0)


def _order(d: dict, first: list[str]) -> dict:
    return {**{k: d[k] for k in first if k in d}, **{k: v for k, v in d.items() if k not in first}}


class InfinityRoom(Source):
    name = "infinity_room"
    label = "Infinity Mirror Room"
    description = ("A Kusama-style mirrored room hung with glowing LED spheres, reflected into an endless lattice of "
                   "lights; beats send shells of light racing out through the reflections.")
    params = _order({
        "lights_per_room": num(4.0, 24.0, 12.0, "Lights per room", step=1.0, tier="regional", basic=True),
        "mirror_tint": num(0.0, 1.0, 0.3, "Mirror tint (clear -> deep coloured)", tier="regional"),
        "room_shape": num(0.0, 1.0, 0.0, "Room shape (cube -> hex)", tier="regional"),
        "light_size": num(0.4, 2.0, 1.0, "Light size", tier="regional"),
        "camera_drift": num(0.0, 1.0, 0.4, "Camera drift", tier="regional"),
        "water": num(0.0, 1.0, 0.0, "Black-water floor", tier="regional"),
        "seams": num(0.0, 1.0, 0.35, "Mirror seams", tier="regional"),
        "beat_wave": num(0.0, 2.0, 1.0, "Beat wave", tier="regional"),
        **standard_scene_params(palette="rainbow", render_scale=0.5, period=1.0, drift=0.03),
    }, ["lights_per_room", "mirror_tint", "room_shape", "light_size", "camera_drift", "water", "seams",
        "beat_wave", "energy", "pulse", "palette_period", "intensity", "speed", "drift_speed"])

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, _OPTIONS).get_function("infinity_room")
        self.Ph = np.zeros(40 + NGAIN, np.float32)
        self.P = cp.zeros(40 + NGAIN, cp.float32)
        self.Lh = np.zeros(MAXL * 8, np.float32)
        self.L = cp.zeros(MAXL * 8, cp.float32)

        # lights (normalised room coords), kept off the walls and out of the camera's wander column
        rng = np.random.default_rng(int(params["seed"]))
        pos = []
        for _ in range(MAXL):
            best = None
            for _try in range(300):
                c = np.array([rng.uniform(0.13, 0.87), rng.uniform(0.28, 0.86), rng.uniform(0.13, 0.87)])
                if math.hypot(c[0] - 0.5, c[2] - 0.5) < 0.25:
                    continue
                sep = min((np.linalg.norm((c - o) * np.array(ROOM)) for o in pos), default=9.0)
                if sep > 0.55:
                    best = c
                    break
                if best is None or sep > best[1]:
                    best = (c, sep)
            if isinstance(best, tuple):
                best = best[0]
            if best is None:
                best = np.array([0.15, 0.5, 0.15])
            pos.append(best)
        self.lpos = np.array(pos) * np.array(ROOM)
        self.lrad = rng.uniform(0.75, 1.25, MAXL)
        off = rng.uniform(0, 1)
        self.lhue = (np.arange(MAXL) * 0.6180339887 + off) % 1.0
        self.lfreq = rng.uniform(0.55, 1.45, MAXL)
        self.lph = rng.uniform(0, 2 * math.pi, MAXL)
        ang = np.arctan2(self.lpos[:, 2] / ROOM[2] - 0.5, self.lpos[:, 0] / ROOM[0] - 0.5)
        self.lorder = (ang / (2 * math.pi)) % 1.0 + 0.15 * (self.lpos[:, 1] / ROOM[1])

        self.cam_ph = rng.uniform(0, 2 * math.pi, 4)
        self.yaw = float(rng.uniform(0, 2 * math.pi))
        self.chase = 0.0
        self.tw = 0.0
        self.ripple = 0.0
        self.drift = 0.0
        self.wave_clock = 0.0
        self.hist_c: deque = deque(maxlen=320)
        self.hist_p: deque = deque(maxlen=320)
        self.es = None

    def _frame(self, p: dict, dt: float) -> int:
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        dt = min(max(dt, 0.0), 0.25)
        a = 1.0 - math.exp(-dt / 0.3) if dt > 0 else 1.0
        self.es = e if self.es is None else self.es + (e - self.es) * a
        es = self.es
        bw = float(p["beat_wave"])
        kick = pulse * bw
        cd = min(max(float(p["camera_drift"]), 0.0), 1.0)

        # host-integrated phases (kicks add to rates only)
        self.chase = _wrap(self.chase + (0.06 + 0.22 * es) * (1.0 + 0.8 * kick) * spd * dt, 1.0)
        self.tw = _wrap(self.tw + (0.7 + 1.8 * es) * spd * dt, 2 * math.pi * 1000.0)
        self.ripple = _wrap(self.ripple + (0.5 + 0.7 * es) * spd * dt, 2 * math.pi)
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * spd * dt, 10.0)
        crate = (0.5 + 0.5 * es) * spd * dt
        self.cam_ph = (self.cam_ph + crate * (0.15 + 0.35 * cd) * np.array([0.53, 0.31, 0.41, 0.37])) % (2 * math.pi)
        self.yaw = _wrap(self.yaw + 2 * math.pi * 0.011 * (0.35 + 1.6 * cd) * crate, 2 * math.pi)
        self.wave_clock += (0.75 + 0.55 * es) * spd * dt

        # pulse history against the wave clock -> per-bounce gains (a shell of light travelling outward)
        if not self.hist_c or self.wave_clock - self.hist_c[-1] >= 0.006:
            self.hist_c.append(self.wave_clock)
            self.hist_p.append(pulse)
        else:
            self.hist_p[-1] = max(self.hist_p[-1], pulse)
        xs = np.fromiter(self.hist_c, np.float64)
        ys = np.fromiter(self.hist_p, np.float64)
        qn = self.wave_clock - np.arange(NGAIN) * WAVE_DELAY
        pw = np.interp(qn, xs, ys, left=0.0, right=pulse)
        pw[0] = max(pw[0], pulse)
        gains = 1.0 + 0.55 * bw * pw

        # palette
        rows = self.session_palette()
        PC = (np.asarray(rows, np.float64).reshape(12) if rows is not None
              else np.asarray(palette_coeffs(p["palette"]), np.float64))
        period = float(p["palette_period"])

        # lights: lit count fades with energy, brightness/twinkle/chase per light
        nmax = min(max(float(p["lights_per_room"]), 4.0), float(MAXL))
        nlit = min(max(nmax * (0.45 + 0.55 * es ** 0.8), 3.0), float(MAXL))
        wts = np.clip(nlit - np.arange(MAXL), 0.0, 1.0)
        td = 0.12 + 0.3 * es
        tw = 1.0 - td * (0.5 + 0.5 * np.sin(self.lfreq * self.tw + self.lph))
        chd = 0.25 + 0.35 * es
        ch = 0.5 + 0.5 * np.cos(2 * np.pi * (self.chase - self.lorder))
        ch = (1.0 - chd) + chd * ch ** 3
        bright = (0.6 + 0.55 * es) * wts * tw * ch
        cols = _pal(self.lhue * period + self.drift, PC) * bright[:, None]
        rad = 0.12 * float(p["light_size"]) * self.lrad
        idx = np.nonzero(wts > 1e-3)[0]
        nl = int(idx.size)
        Lh = self.Lh
        Lh[:] = 0.0
        Lv = Lh.reshape(MAXL, 8)
        Lv[:nl, 0:3] = self.lpos[idx]
        Lv[:nl, 3] = rad[idx]
        Lv[:nl, 4:7] = cols[idx]
        self.L.set(Lh)

        # camera (camera position in scaled-lattice coords; orientation in world)
        ca = (0.07 + 0.11 * cd) * (0.8 + 0.4 * es)
        ph = self.cam_ph
        cam = np.array([ROOM[0] * (0.5 + ca * math.sin(ph[0])),
                        ROOM[1] * (0.43 + 0.05 * cd * math.sin(ph[1])),
                        ROOM[2] * (0.5 + ca * math.sin(ph[2] + 1.0))])
        pitch = (0.03 + 0.12 * cd) * math.sin(ph[3])
        roll = 0.03 * cd * math.sin(ph[1] * 1.0 + 0.7)
        yaw = self.yaw
        fwd = np.array([math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch)])
        right = np.array([math.cos(yaw), 0.0, -math.sin(yaw)])
        up = np.cross(fwd, right)
        up /= np.linalg.norm(up)
        if up[1] < 0:
            up = -up
        cr, sr = math.cos(roll), math.sin(roll)
        right, up = right * cr + up * sr, up * cr - right * sr

        shape = min(max(float(p["room_shape"]), 0.0), 1.0)
        theta = math.radians(90.0 - 30.0 * shape)
        tanH = 0.62

        # mirrors
        mt = min(max(float(p["mirror_tint"]), 0.0), 1.0)
        tint = _pal(np.array([self.drift + 0.5]), PC)[0]
        tint = tint / max(tint.max(), 1e-3)
        neutral = np.array([0.97, 0.985, 1.0])
        R = 0.93 - 0.2 * mt
        wall = R * (neutral + (tint - neutral) * 0.55 * mt)
        water = min(max(float(p["water"]), 0.0), 1.0)
        floor = wall * (1.0 - water) + water * 0.52 * np.array([0.8, 0.9, 1.0])

        lit_cols = cols[idx]
        avg = lit_cols.mean(axis=0) if nl else np.zeros(3)
        mxa = max(float(avg.max()), 1e-3)
        haze = avg * (0.010 + 0.010 * es)
        seam_col = 0.5 * (avg / mxa) + 0.5 * np.array([0.9, 0.92, 1.0])

        P = self.Ph
        P[0:3] = cam
        P[3:6] = right
        P[6:9] = up
        P[9:12] = fwd
        P[12] = tanH
        P[13] = math.cos(theta)
        P[14] = math.sin(theta)
        P[15:18] = ROOM
        P[18] = nl
        P[19] = 2.0 * tanH / self.h
        P[20:23] = wall
        P[23:26] = floor
        P[26] = water * 0.0065 * (0.7 + 0.6 * es)
        P[27] = self.ripple
        P[28] = float(p["seams"]) * (0.3 + 0.1 * es) * (1.0 + 0.25 * kick)
        P[29:32] = seam_col
        P[32:35] = haze
        P[35] = float(p["intensity"]) * 1.15
        P[36] = (0.32 + 0.2 * es) * (1.0 + 0.25 * kick)
        P[38] = MAXC * 0.5
        P[40:40 + NGAIN] = gains
        self.P.set(P)
        return nl

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        nl = self._frame(p, dt)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P, self.L, np.int32(nl)))
        present(self.ctx, self.buf, out)
