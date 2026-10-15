"""Live score: the piano's playing written out as an engraved score while it is played.

Source: Original.  Engraving by Verovio (https://www.verovio.org, LGPL-3.0-or-later, used as an unmodified library
        in a separate worker process, nothing ported) rasterised by resvg (resvg-py, MPL-2.0 / Apache-2.0); the
        Leipzig music font (SIL OFL 1.1).  Prior art for the score side: PM2S (MIT, cheriell/PM2S) is the planned
        upgrade for beats, metre, key and hands.
Author: smfx
License: MIT (this file)
license_class: permissive

Listens to the piano (``listens = ("notes",)``, CONVENTIONS §2b).  stream_service/score does the music: a beat clock
that follows the playing, light quantisation (16ths, swing read straight, hands split at middle C), 4/4 bars
committed one beat after they end, MEI per grand-staff system, and an engraving subprocess (Verovio -> SVG -> resvg
-> ink coverage + staff geometry), so the render thread never waits on it.

On screen, like a page: the system being written at the bottom, the one before above it.  Each finished bar inks
in (the new render crossfades over the old, the fresh ink glowing in the palette's colour for ``ink_glow``); notes
being played now, not yet in a finished bar, glow as noteheads at their pitch on the staff, in the next empty bar,
where the bar will be written.  When a system fills it slides up and a fresh one, empty staves waiting, opens below;
a pause of four seconds ends a phrase (the next system restarts the bar lines).  Looks: paper (warm paper, dark
ink) and night (deep blue-black, gold ink).

Macros: energy -> note glow size and the fresh ink's glow; pulse (bind note_on) -> a glow kick; speed -> the paper's
slow drift; intensity -> output gain; palette / period / drift colour the glowing notes (around the circle of
fifths).  Without the engraving libraries it still shows the live notes on drawn staves.  Alive without notes:
the paper breathes and the last systems stay.
"""
from __future__ import annotations

import logging
import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import palette_coeffs
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

log = logging.getLogger("stream_service.plugins.live_score")

LOOKS = ("paper", "night")
NSYS = 3                       # systems kept: previous, current, and one sliding away
MAX_LIVE = 48
_SRC = HELPERS + r"""
#define NSYS """ + str(NSYS) + r"""

__device__ __forceinline__ float3 pal3(float t, const float* c) {
    return make_float3(clamp01(c[0] + c[3] * cosf(6.2831853f * (c[6] * t + c[9]))),
                       clamp01(c[1] + c[4] * cosf(6.2831853f * (c[7] * t + c[10]))),
                       clamp01(c[2] + c[5] * cosf(6.2831853f * (c[8] * t + c[11]))));
}
__device__ __forceinline__ float ink_at(const unsigned char* tex, int w, int h, float u, float v) {
    if (u < 0.f || v < 0.f || u > w - 1.001f || v > h - 1.001f || w < 2 || h < 2) return 0.f;
    const int x0 = (int)u, y0 = (int)v;
    const float fx = u - x0, fy = v - y0;
    const unsigned char* r0 = tex + y0 * w + x0;
    const unsigned char* r1 = r0 + w;
    return ((r0[0] * (1.f - fx) + r0[1] * fx) * (1.f - fy) + (r1[0] * (1.f - fx) + r1[1] * fx) * fy) / 255.f;
}

// the page's grain, fibre and vignette, baked once in setup (5 value-noise lookups a pixel are too dear per frame)
extern "C" __global__
void bake_page(unsigned char* __restrict__ page, const int W, const int H, const int night, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float X = x + 0.5f, Y = y + 0.5f, u = X / W, v = Y / H;
    const float grain = fbm(u * 9.f, v * 5.f, seed + 3u) - 0.5f;
    const float fibre = vnoise(X * 0.35f, Y * 0.02f + 3.f, seed + 9u) - 0.5f;
    const float vig = 1.f - 0.35f * ((u - 0.5f) * (u - 0.5f) * 1.6f + (v - 0.5f) * (v - 0.5f) * 2.f);
    const float k = night ? (0.9f + 0.25f * grain) * vig : (0.93f + 0.05f * grain + 0.015f * fibre) * vig;
    page[y * W + x] = (unsigned char)(clamp01(k / 1.2f) * 255.f + 0.5f);
}

// P: [0..11] palette [12]W [13]H [14]look [15]time [16]energy [17]kick [18]gain [19]note glow [20]drift
//    [21]period [22..25] live bbox x0 y0 x1 y1 [26] synthetic staves [27..30] synthetic top/bottom (treble, bass)
//    [31] synthetic x0 [32] synthetic x1 [33] paper drift phase
// S (floats, per system): x0 y0 scale fade alpha fresh   SI (ints, per system): cur_off prev_off w h wp hp
// L (per live note): x y rx ry intensity colour-index
extern "C" __global__
void score(uchar4* __restrict__ out, const float* __restrict__ P, const unsigned char* __restrict__ ink,
           const float* __restrict__ S, const int* __restrict__ SI, const float* __restrict__ L, const int nl,
           const unsigned char* __restrict__ page)
{
    const int W = (int)P[12], H = (int)P[13];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float X = x + 0.5f, Y = y + 0.5f;
    const int night = (int)P[14];
    const float t = P[15], e = P[16], kick = P[17];
    // the page (baked), breathing very slowly
    const float v = Y / H;
    const float k = page[y * W + x] * (1.2f / 255.f) * (1.f + 0.012f * sinf(P[33]));
    float3 c, inkc;
    if (!night) {
        c = make_float3(0.95f * k, 0.905f * k, 0.80f * k);
        inkc = make_float3(0.11f, 0.085f, 0.07f);
    } else {
        c = make_float3(0.028f * k, 0.034f * k, 0.055f * k + 0.01f * (1.f - v));
        inkc = make_float3(0.92f, 0.82f, 0.58f);
    }
    const float3 fresh_c = pal3(P[20] + 0.1f, P);
    float glow_acc = 0.f;
    // engraved systems
    for (int s = 0; s < NSYS; ++s) {
        const float* Q = S + 6 * s;
        const int* QI = SI + 6 * s;
        if (Q[4] <= 0.001f || QI[2] < 2) continue;
        const float uu = (X - Q[0]) / Q[2], vv = (Y - Q[1]) / Q[2];
        if (uu < -2.f || vv < -2.f || uu > QI[2] + 2.f || vv > QI[3] + 2.f) continue;
        const float a = ink_at(ink + QI[0], QI[2], QI[3], uu, vv);
        const float b = Q[3] < 0.999f ? ink_at(ink + QI[1], QI[4], QI[5], uu, vv) : 0.f;
        const float cov = clamp01(b + (a - b) * Q[3]) * Q[4];
        const float newink = clamp01(a - b) * Q[5] * Q[4];          // what this render added, glowing a while
        c.x += (inkc.x - c.x) * cov; c.y += (inkc.y - c.y) * cov; c.z += (inkc.z - c.z) * cov;
        glow_acc += newink;
    }
    if (glow_acc > 0.f) {
        const float g = glow_acc * (0.55f + 0.5f * e);
        if (!night) { c.x += (fresh_c.x * 0.8f - c.x) * g * 0.6f; c.y += (fresh_c.y * 0.8f - c.y) * g * 0.6f;
                      c.z += (fresh_c.z * 0.8f - c.z) * g * 0.6f; }
        else { c.x += fresh_c.x * g; c.y += fresh_c.y * g; c.z += fresh_c.z * g; }
    }
    // drawn staves when there is no engraving
    if (P[26] > 0.5f && X > P[31] && X < P[32]) {
        for (int st = 0; st < 2; ++st) {
            const float top = P[27 + 2 * st], bot = P[28 + 2 * st], sp = (bot - top) * 0.25f;
            for (int l = 0; l < 5; ++l) {
                const float d = fabsf(Y - (top + l * sp));
                const float k = clamp01(1.2f - d) * 0.85f;
                c.x += (inkc.x - c.x) * k; c.y += (inkc.y - c.y) * k; c.z += (inkc.z - c.z) * k;
            }
        }
    }
    // the notes being played now
    if (nl > 0 && X > P[22] && X < P[24] && Y > P[23] && Y < P[25]) {
        const float cs = cosf(-0.35f), sn = sinf(-0.35f);
        for (int i = 0; i < nl; ++i) {
            const float* N = L + 6 * i;
            const float dx = X - N[0], dy = Y - N[1];
            const float ex = (dx * cs + dy * sn) / N[2], ey = (-dx * sn + dy * cs) / N[3];
            const float d = sqrtf(ex * ex + ey * ey);
            const float halo = P[19] * (0.8f + 0.6f * e) * (1.f + 0.5f * kick);
            const float core = 1.f - smooth(0.82f, 1.05f, d);
            const float glow = expf(-fmaxf(d - 1.f, 0.f) * (2.2f / fmaxf(halo, 0.05f))) * 0.7f * halo;
            const float k = N[4] * fminf(1.f, core + glow);
            const float3 nc = pal3(N[5] * P[21] + P[20], P);
            if (!night) { c.x += (nc.x * 0.85f - c.x) * k; c.y += (nc.y * 0.85f - c.y) * k; c.z += (nc.z * 0.85f - c.z) * k; }
            else { c.x += nc.x * k; c.y += nc.y * k; c.z += nc.z * k; }
        }
    }
    const float g = 255.f * P[18];
    out[y * W + x] = pack(make_float4(clamp01(c.x) * g, clamp01(c.y) * g, clamp01(c.z) * g, 255.f));
}
"""


class _System:
    def __init__(self, slot: int, first_clock_bar: int, fifths: int, meter: bool) -> None:
        self.slot = slot
        self.bars: list = []
        self.first_clock_bar = first_clock_bar
        self.fifths = fifths
        self.meter = meter
        self.closed = False
        self.meta = None              # geometry of the newest render
        self.dims = (0, 0)            # (w, h) of the cur texture
        self.pdims = (0, 0)
        self.fade = 1.0               # cur over prev
        self.fresh = 0.0
        self.y = None                 # draw position (screen px), eased to target
        self.alpha = 0.0
        self.y_target = 0.0
        self.alpha_target = 1.0
        self.version = 0


class LiveScore(Source):
    name = "live_score"
    label = "Live score"
    description = "The piano's playing written out as an engraved score while it is played, notes glowing as they sound."
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette="ember", render_scale=1.0, period=1.0, drift=0.01),
            "look": enum(LOOKS, "paper", "Look", tier="structural", basic=True),
            "bars_per_line": num(3, 6, 4, "Bars per line", tier="structural", integer=True),
            "note_glow": num(0.0, 1.5, 0.8, "Note glow", basic=True),
            "ink_glow": num(0.0, 3.0, 1.2, "Fresh ink glow", unit="s"),
        }

    # -- setup ----------------------------------------------------------------------------------------------------
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC).get_function("score")
        self.slots = int(params["bars_per_line"])
        self.ew = max(64, int(self.w * 0.94))                 # engraved width (texture px)
        self.ehmax = max(64, int(self.h * 0.62))
        self.tex_bytes = self.ew * self.ehmax
        self.ink = cp.zeros(NSYS * 2 * self.tex_bytes, cp.uint8)
        self.P = cp.zeros(40, cp.float32)
        self.Ph = np.zeros(40, np.float32)
        self.S = cp.zeros(NSYS * 6, cp.float32)
        self.Sh = np.zeros(NSYS * 6, np.float32)
        self.SI = cp.zeros(NSYS * 6, cp.int32)
        self.SIh = np.zeros(NSYS * 6, np.int32)
        self.L = cp.zeros(MAX_LIVE * 6, cp.float32)
        self.Lh = np.zeros(MAX_LIVE * 6, np.float32)
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        self.page = cp.zeros((self.h, self.w), cp.uint8)
        launch_2d(raw_module(_SRC).get_function("bake_page"), self.w, self.h,
                  (self.page, np.int32(self.w), np.int32(self.h), np.int32(LOOKS.index(params["look"])),
                   np.uint32(self.seed)))
        # ink arrives every bar or so: staged through pinned memory, the upload is an async copy on the stream
        self._pinned = cp.cuda.alloc_pinned_memory(self.tex_bytes)
        self.stage = np.frombuffer(self._pinned, np.uint8, self.tex_bytes)
        from stream_service.score.quantize import ScoreBuilder
        self.builder = ScoreBuilder()
        self.clock_bar = 0                                    # clock bars committed in this phrase
        self.systems: list[_System] = []
        self.free = list(range(NSYS))
        self.engraver = None
        try:
            import verovio  # noqa: F401  (only to know it's there; the worker imports it)
            import resvg_py  # noqa: F401
            from stream_service.score.client import Engraver
            self.engraver = Engraver(self.ew, page_width=1080 * self.slots, scale=50)
        except Exception as e:
            log.warning("live_score: no engraving (%s); drawing the staves instead", e)
        self.t_sound = 0.0
        self.clock = 0.0
        self.paper = 0.0
        self.drift = 0.0
        self.kick = 0.0
        self._new_system(meter=True)

    # -- systems -------------------------------------------------------------------------------------------------
    def _new_system(self, meter: bool) -> _System:
        if not self.free:
            old = self.systems.pop(0)
            self.free.append(old.slot)
        _tonic, _minor, fifths = self.builder.key()
        s = _System(self.free.pop(0), self.clock_bar, fifths, meter)
        self.systems.append(s)
        self._layout()
        self._engrave(s)
        return s

    def _layout(self) -> None:
        """Targets, by the engravings' real heights: the current system sits above the bottom margin, the one
        before right above it, older ones slide off the top (and fade)."""
        n = len(self.systems)
        est = 0.42 * self.h                                     # until a system's first render arrives
        y = 0.97 * self.h
        for i in range(n - 1, -1, -1):
            s = self.systems[i]
            back = n - 1 - i
            hs = s.dims[1] or est
            y -= hs
            s.y_target = y
            s.alpha_target = 1.0 if back == 0 else 0.92 if back == 1 and y > -0.15 * hs else 0.0
            y -= 0.02 * self.h
            if s.y is None:
                s.y = s.y_target + 0.08 * self.h

    def _engrave(self, s: _System) -> None:
        if self.engraver is None:
            return
        from stream_service.score import mei
        s.version += 1
        self.engraver.submit(f"{s.slot}:{id(s)}:{s.version}", mei.system(s.bars, self.slots, s.fifths, meter=s.meter))

    def _take_renders(self) -> None:
        if self.engraver is None:
            return
        for key, meta, ink in self.engraver.results():
            if not key or not meta.get("ok"):
                continue
            slot, sid, ver = key.split(":")
            s = next((x for x in self.systems if x.slot == int(slot) and str(id(x)) == sid), None)
            if s is None or int(ver) != s.version:
                continue                                      # superseded
            w, h = int(meta["w"]), int(meta["h"])
            h = min(h, self.ehmax)
            if w != self.ew or len(ink) < w * h:
                continue
            base = s.slot * 2 * self.tex_bytes
            cur, prev = base, base + self.tex_bytes
            cw, ch = s.dims
            if cw and ch:
                self.ink[prev:prev + cw * ch] = self.ink[cur:cur + cw * ch]     # the old render, to fade from
                s.pdims = (cw, ch)
            n = w * h
            self.stage[:n] = np.frombuffer(ink, np.uint8, n)        # bars arrive ~2 s apart: the last copy is done
            self.ink[cur:cur + n].set(self.stage[:n], stream=self.ctx.cp.cuda.get_current_stream())
            s.dims, s.meta = (w, h), meta
            s.fade = 0.0 if cw else 1.0
            s.fresh = 1.0 if s.bars else 0.0

    # -- the music ---------------------------------------------------------------------------------------------
    def _listen(self) -> object:
        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        if f is None:
            return None
        t = float(f.t_ms) / 1000.0
        self.t_sound = t
        b = self.builder
        for p, vel, age in f.onsets:
            b.note_on(p, float(vel), t - age)
        for p, _vo, age in f.releases:
            b.note_off(p, t - age)
        b.pedal(float(f.pedals[0]), t)
        for bar in b.poll(t):
            cur = self.systems[-1]
            if bar.marker:                                    # the phrase ended: the next one starts a system
                self.clock_bar = 0
                if cur.bars:
                    cur.closed = True
                    self._new_system(meter=True)
                else:
                    cur.meter, cur.first_clock_bar = True, 0
                continue
            if cur.closed or len(cur.bars) >= self.slots:
                cur = self._new_system(meter=cur.closed)
            cur.bars.append(bar)
            self.clock_bar += 1
            self._engrave(cur)
            if len(cur.bars) >= self.slots:
                self._new_system(meter=False)                 # a fresh system for the live edge to write into
        return f

    def _live_notes(self, f) -> int:
        """Glowing noteheads for notes not yet in a finished bar, at their pitch in the bar they'll be written in."""
        from stream_service.score.mei import signature, spell
        cur = self.systems[-1]
        geo = (cur.meta or {}).get("measures") or []
        b = self.builder
        notes = [n for n in b.notes]
        if not notes or f is None:
            return 0
        clk = b.clock
        committed_beat = self.clock_bar * 4
        first = cur.first_clock_bar
        sig = signature(cur.fifths)
        y0, sc = cur.y, (self.ew / max(1, cur.dims[0]) if cur.dims[0] else 1.0)
        x_off = 0.5 * (self.w - self.ew)
        lv = f.notes[:, 0]
        k = 0
        Lh = self.Lh
        bx0 = by0 = 1e9
        bx1 = by1 = -1e9
        t0 = notes[0].t_on
        for n in notes[-MAX_LIVE:]:
            if clk.running:
                beat = clk.beat(n.t_on)
                if beat < committed_beat - 0.01:
                    continue
                slot = int(beat // 4) - first
                frac = (beat % 4) / 4.0
            else:
                slot, frac = 0, min(0.95, (n.t_on - t0) / 2.4)
            level = float(lv[n.pitch]) if 0 <= n.pitch < 128 else 0.0
            inten = max(0.22, min(1.0, 1.4 * level)) * min(1.0, (self.t_sound - n.t_on) / 0.12 + 0.2)
            staff = 0 if n.pitch >= 60 else 1
            if geo and 0 <= slot < len(geo):
                m = geo[slot]
                top, bot = m["staves"][min(staff, len(m["staves"]) - 1)]
                x = m["x0"] + (m["x1"] - m["x0"]) * (0.08 + 0.84 * frac)
            elif geo:
                m = geo[-1]
                top, bot = m["staves"][min(staff, len(m["staves"]) - 1)]
                x = m["x1"] - 10
            else:
                top, bot = self._synthetic_staff(staff)
                x = (0.08 + 0.84 * frac) * self.ew
                sc = 1.0
            sp = (bot - top) / 4.0
            letter, _alt, octave = spell(n.pitch, cur.fifths, sig)
            d = "cdefgab".index(letter) + 7 * octave
            y = bot - (d - (30 if staff == 0 else 18)) * sp * 0.5
            X, Y = x_off + x * sc, y0 + y * sc if geo else y
            Lh[6 * k:6 * k + 6] = (X, Y, 0.62 * sp * sc, 0.48 * sp * sc, inten, ((n.pitch * 7) % 12) / 12.0)
            r = 3.0 * sp * sc
            bx0, by0, bx1, by1 = min(bx0, X - r), min(by0, Y - r), max(bx1, X + r), max(by1, Y + r)
            k += 1
            if k >= MAX_LIVE:
                break
        self.bbox = (bx0, by0, bx1, by1)
        return k

    def _synthetic_staff(self, staff: int) -> tuple[float, float]:
        top = self.h * (0.62 if staff == 0 else 0.76)
        return top, top + self.h * 0.07

    # -- render -------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        self.clock += dt
        self.paper += 0.25 * spd * dt
        self.drift += float(p["drift_speed"]) * spd * dt
        a = 1.0 - math.exp(-dt / 0.15) if dt > 0 else 1.0
        self.kick += (pulse - self.kick) * a
        f = self._listen()
        self._take_renders()
        # ease the systems: position, opacity, the crossfade and the fresh ink's glow
        ease = 1.0 - math.exp(-dt / 0.45) if dt > 0 else 1.0
        fade_k = dt / 0.25 if dt > 0 else 1.0
        glow_s = max(0.05, float(p["ink_glow"]))
        self._layout()
        for s in list(self.systems):
            s.y += (s.y_target - s.y) * ease
            s.alpha += (s.alpha_target - s.alpha) * ease
            s.fade = min(1.0, s.fade + fade_k)
            s.fresh = max(0.0, s.fresh - (dt / glow_s if dt > 0 else 0.0))
        Sh, SIh = self.Sh, self.SIh
        Sh[:] = 0.0
        SIh[:] = 0
        x_off = 0.5 * (self.w - self.ew)
        for i, s in enumerate(self.systems[-NSYS:]):
            if not s.dims[0]:
                continue
            base = s.slot * 2 * self.tex_bytes
            Sh[6 * i:6 * i + 6] = (x_off, s.y, 1.0, s.fade, s.alpha, s.fresh)
            SIh[6 * i:6 * i + 6] = (base, base + self.tex_bytes, s.dims[0], s.dims[1], s.pdims[0], s.pdims[1])
        self.bbox = (0.0, 0.0, 0.0, 0.0)
        nl = self._live_notes(f)
        Ph = self.Ph
        Ph[0:12] = palette_coeffs(p["palette"]) if p.get("palette") else palette_coeffs("ember")
        Ph[12], Ph[13] = self.w, self.h
        Ph[14] = LOOKS.index(p["look"])
        Ph[15] = self.clock % 1000.0
        Ph[16] = e
        Ph[17] = self.kick
        Ph[18] = float(p["intensity"])
        Ph[19] = float(p["note_glow"])
        Ph[20] = self.drift % 10.0
        Ph[21] = float(p["palette_period"])
        Ph[22:26] = self.bbox
        synthetic = self.engraver is None or not self.engraver.ok or not self.systems[-1].dims[0]
        Ph[26] = 1.0 if synthetic and self.engraver is None else 0.0
        Ph[27:31] = (*self._synthetic_staff(0), *self._synthetic_staff(1))
        Ph[31], Ph[32] = x_off, x_off + self.ew
        Ph[33] = self.paper % (2.0 * math.pi)
        self.P.set(Ph)
        self.S.set(Sh)
        self.SI.set(SIh)
        if nl:
            self.L.set(self.Lh)
        launch_2d(self.k, self.w, self.h, (self.buf, self.P, self.ink, self.S, self.SI, self.L, np.int32(nl),
                                           self.page))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        if self.engraver is not None:
            self.engraver.close()
            self.engraver = None
        self.buf = self.ink = self.P = self.S = self.SI = self.L = self.page = None
        self.stage = self._pinned = None
