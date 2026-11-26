"""Lyric float: each word appears as it is sung, somewhere in the room's air at its own depth, and drifts slowly up,
swaying and turning a little, then fades; the screen holds a slow cloud of the song's recent words (karaoke;
listens "lyrics").

Source: Original
Author: screen-machine
License: MIT
license_class: permissive

Owner, 25 Nov ("others where words float").  Near words are larger, sharper and rise faster than far ones (the far
ones are softened through the text field), so the cloud has depth.  A word lands where the cloud is thinnest (the best
of a few random spots), so they don't pile up.  The word being sung glows warm.  A seek backwards clears the cloud.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.lyric_fx import ENV_MIN, LyricEffect, font_param, under_lyrics_param
from stream_service.plugins._lib.lyric_fx import LYRICS_BAND_TOP
from stream_service.plugins._lib.lyric_text import GI_MAX, GLYPH_CUDA
from stream_service.plugins._lib.params import num, standard_effect_params

_SRC = HELPERS + GLYPH_CUDA + r"""
extern "C" __global__
void floatk(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
            const float* __restrict__ G, const int n, const unsigned char* __restrict__ A,
            const float halo, const float shade, const float hr, const float hg, const float hb, const float warm, const float btop, const float bfe, const float bk)
{
    GI_LOAD(G, n, sh)
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    float c = 0.f, gl = 0.f, sum = 0.f, us = 0.f;
    const float px = x + 0.5f, py = y + 0.5f;
    for (int k = 0; k < n; ++k) {             // per instance: union cover, halo, and the sung glow by its own cover
        const float* g = sh + k * GI_N;
        const float f = gi_field(g, A, px, py);
        if (f <= 0.f) continue;
        const float a = gi_edge(g, f) * g[8];
        c = 1.f - (1.f - c) * (1.f - a);
        gl = fmaxf(gl, f * g[8] * (0.35f + 0.65f * g[11]));
        us = fmaxf(us, g[11] * a);
        sum += a;
    }
    float4 s = ld(src, x, y, W, H);
    const float d = shade * clamp01(gl * 1.5f) * (1.f - c);
    s.x *= 1.f - d; s.y *= 1.f - d; s.z *= 1.f - d;
    const float u = c > 1e-3f ? clamp01(us / c) * warm : 0.f;
    const float cr = 250.f + (hr - 250.f) * u, cg = 246.f + (hg - 246.f) * u, cb = 238.f + (hb - 238.f) * u;
    const float h = halo * gl * gl * (0.6f + 0.8f * u);
    s.x = s.x + (cr - s.x) * c + h * hr * 0.35f;
    s.y = s.y + (cg - s.y) * c + h * hg * 0.35f;
    s.z = s.z + (cb - s.z) * c + h * hb * 0.35f;
    s.w = 255.f;
    { const float kp = band_keep(y + 0.5f, btop, bfe, bk);    // the screen's own lyrics band
      s = mix4(ld(src, x, y, W, H), s, kp); }
    out[y * W + x] = pack(s);
}
"""

MAX_LIVE = GI_MAX - 4


class LyricFloat(LyricEffect):
    name = "lyric_float"
    label = "Lyric float"
    description = ("Each word appears as it is sung at its own depth and drifts slowly up, swaying, then fades: "
                   "a slow cloud of the song's recent words.")
    n_slots = 14
    params = {
        **standard_effect_params(),
        "size": num(0.05, 0.3, 0.14, "Word size (of the screen's height)", tier="regional", basic=True),
        "life": num(2.0, 14.0, 7.0, "Seconds a word stays", step=0.1, tier="regional", basic=True),
        "rise": num(0.0, 0.2, 0.05, "Rise (screens a second)", step=0.005, tier="regional"),
        "sway": num(0.0, 1.0, 0.4, "Sway and turn", tier="regional"),
        "depth": num(0.0, 1.0, 0.6, "Depth (far words smaller and softer)", tier="regional"),
        "opacity": num(0.2, 1.0, 0.8, "Opacity", tier="regional"),
        "halo": num(0.0, 1.0, 0.45, "Halo", tier="regional"),
        "shade": num(0.0, 0.8, 0.2, "Shadow behind", tier="regional"),
        "warm": num(0.0, 1.0, 0.7, "Sung-word glow", tier="regional"),
        "hue": num(0.0, 1.0, 0.1, "Glow hue", tier="regional"),
        "under_lyrics": under_lyrics_param(),
        "font": font_param("bold"),
        "seed": num(0, 9999, 7, "Seed", step=1, tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        self._k = raw_module(_SRC).get_function("floatk")
        self._rng = np.random.default_rng(int(params.get("seed", 7)))
        self._live: list[dict] = []
        self._next = 0            # the next word (index in song.words) to spawn
        self._key = None
        self._last_t = None

    def _reset(self, song, t: float) -> None:
        self._live.clear()
        self._key = song.key
        self._next = max(0, song.word_at(t) - 3)       # a jump: the last few words only
        self._last_t = t

    def _spot(self, p) -> tuple[float, float]:
        best, bd = (0.5, 0.5), -1.0
        for _ in range(7):
            x, y = self._rng.uniform(0.12, 0.88), self._rng.uniform(0.16, LYRICS_BAND_TOP - 0.1)
            d = min((math.hypot((x - w["x"]) * 1.6, y - w["y"]) for w in self._live), default=9.0)
            if d > bd:
                best, bd = (x, y), d
        return best

    def render(self, t_local: float, dt: float, src, out) -> None:
        p, lf, env = self.begin(dt)
        if lf is None:
            self._live.clear()
            self._key = None
            return self.passthrough(src, out)
        song, t = lf.song, lf.t
        if song.key != self._key or self._last_t is None or t < self._last_t - 0.5 or t > self._last_t + 5.0:
            self._reset(song, t)
        self._last_t = t
        self.want_lines(lf, behind=2, ahead=2)
        lead = 0.12
        while self._next < len(song.words) and song.words[self._next].t0 - lead <= t:
            w = song.words[self._next]
            self._next += 1
            if t - w.t0 > p["life"]:
                continue
            x, y = self._spot(p)
            z = float(self._rng.uniform(0.0, 1.0))
            self._live.append({"w": w, "x": x, "y": y, "z": z, "ph": float(self._rng.uniform(0, 6.283)),
                               "rot": float(self._rng.uniform(-1, 1))})
            if len(self._live) > MAX_LIVE:
                self._live.pop(0)
        life = p["life"]
        self._live = [o for o in self._live if t - o["w"].t0 < life]
        if env < ENV_MIN or not self._live:
            return self.passthrough(src, out)
        op = p["opacity"] * env * (1.0 + 0.25 * p["pulse"])
        for o in sorted(self._live, key=lambda o: -o["z"]):          # far first (the union doesn't care, but tidy)
            w = o["w"]
            slot = self.slot(song.lines[w.line].text)
            if slot is None:
                continue
            age = t - w.t0
            a = min(1.0, (age + 0.12) / 0.3) * min(1.0, (life - age) / (0.4 * life))
            if a <= 0.0:
                continue
            zd = o["z"] * p["depth"]
            near = 1.0 - 0.6 * zd
            em = p["size"] * self.H * near
            sc = em / slot.em
            rise = p["rise"] * (0.5 + near) * age
            sw = p["sway"]
            cx = (o["x"] + 0.025 * sw * math.sin(o["ph"] + age * 0.7)) * self.W
            cy = (o["y"] - rise) * self.H
            rot = sw * (0.12 * o["rot"] + 0.05 * math.sin(o["ph"] * 1.7 + age * 0.5))
            sung = 1.0 if w.t0 - 0.05 <= t <= w.t1 + 0.35 else max(0.0, 1.0 - (t - w.t1 - 0.35) / 0.8) if t > w.t1 else 0.0
            a0, a1 = self.word_px(slot, w.i)
            self.gl.add(slot, a0, a1, cx, cy, sc, rot=rot, alpha=op * a * (1.0 - 0.45 * zd),
                        soft=zd * 22.0 * self.u, user=sung, height=slot.em * 0.62)
        n = self.gl.upload()
        if n == 0:
            return self.passthrough(src, out)
        import colorsys
        hr, hg, hb = (255.0 * v for v in colorsys.hsv_to_rgb(p["hue"] % 1.0, 0.6, 1.0))
        launch_2d(self._k, self.W, self.H,
                  (src, out, np.int32(self.W), np.int32(self.H), self.gl.gpu, np.int32(n), self.atlas.gpu,
                   np.float32(p["halo"] * env), np.float32(p["shade"] * env), np.float32(hr), np.float32(hg),
                   np.float32(hb), np.float32(p["warm"]), *self.band(p)))
