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
committed one beat after they end.  Each bar is engraved on its own (no clefs or signatures, mei.bar_doc) and each
line's start once (brace, clefs, key, and the time signature when a phrase starts: mei.header_doc), all at one fixed
staff size, by a worker process (Verovio -> SVG -> resvg), so the render thread never waits.

On screen, like a page being written: lines of grand staff, the one being written at the bottom and the one before
above it.  Each engraved bar is composited once into its line's canvas on the GPU (aligned on the treble staff's top
line, the engraver's system-start line cropped off) and never moves; it inks in with the palette's glow for
``ink_glow`` seconds.  Ahead of the last bar the staff is open (drawn here), and the notes being played now glow as
noteheads at their pitch where the next bar will be written.  A bar that doesn't fit starts a new line, and a new
line also starts early when there's no room left for another bar; a pause of four seconds ends a phrase (the next
line shows the time signature again).  Looks: paper (warm paper, dark ink, baked once) and night (gold on blue-black).

Macros: energy -> note glow size and the fresh ink's glow; pulse (bind note_on) -> a glow kick; speed -> the page's
slow breathing; intensity -> output gain; palette / period / drift colour the glowing notes (around the circle of
fifths).  Without the engraving libraries it still shows the live notes on drawn staves.
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
NROW = 3                       # lines kept: the one being written, the one before, one sliding away
MAX_LIVE = 48
ROW_SP = 24                    # a line's canvas height in staff spaces
TOP_SP = 6                     # the treble staff's top line, in staff spaces from the canvas top
RESULTS_PER_FRAME = 3

_SRC = HELPERS + r"""
#define NROW """ + str(NROW) + r"""

__device__ __forceinline__ float3 pal3(float t, const float* c) {
    return make_float3(clamp01(c[0] + c[3] * cosf(6.2831853f * (c[6] * t + c[9]))),
                       clamp01(c[1] + c[4] * cosf(6.2831853f * (c[7] * t + c[10]))),
                       clamp01(c[2] + c[5] * cosf(6.2831853f * (c[8] * t + c[11]))));
}

// the page's grain, fibre and vignette, baked once in setup
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

// P: [0..11] palette [12]W [13]H [14]look [15]breath [16]energy [17]kick [18]gain [19]note glow [20]drift
//    [21]period [22..25] live bbox [26] canvas w [27] canvas h [28] staff space [29] line thickness
// R (per line): x0 y0 alpha fresh_x0 fresh_x1 fresh staff_from t1 t2 _      RI: canvas offset
// L (per live note): x y rx ry intensity colour-index
extern "C" __global__
void score(uchar4* __restrict__ out, const float* __restrict__ P, const unsigned char* __restrict__ canvas,
           const float* __restrict__ R, const int* __restrict__ RI, const float* __restrict__ L, const int nl,
           const unsigned char* __restrict__ page)
{
    const int W = (int)P[12], H = (int)P[13];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float X = x + 0.5f, Y = y + 0.5f;
    const int night = (int)P[14];
    const float e = P[16], kick = P[17];
    const float v = Y / H;
    const float k = page[y * W + x] * (1.2f / 255.f) * (1.f + 0.012f * sinf(P[15]));
    float3 c, inkc;
    if (!night) {
        c = make_float3(0.95f * k, 0.905f * k, 0.80f * k);
        inkc = make_float3(0.11f, 0.085f, 0.07f);
    } else {
        c = make_float3(0.028f * k, 0.034f * k, 0.055f * k + 0.01f * (1.f - v));
        inkc = make_float3(0.92f, 0.82f, 0.58f);
    }
    const int cw = (int)P[26], ch = (int)P[27];
    const float sp = P[28], th = P[29];
    const float3 fresh_c = pal3(P[20] + 0.1f, P);
    for (int r = 0; r < NROW; ++r) {
        const float* Q = R + 10 * r;
        if (Q[2] <= 0.001f) continue;
        const float cx = X - Q[0], cy = Y - Q[1];
        if (cx < 0.f || cy < 0.f || cx >= cw || cy >= ch) continue;
        const int ix = (int)cx, iy = (int)cy;
        float cov = canvas[RI[r] + iy * cw + ix] / 255.f;
        // the open staff ahead of the last bar
        if (Q[6] >= 0.f && cx >= Q[6]) {
            for (int st = 0; st < 2; ++st) {
                const float top = Q[7 + st];
                const float d0 = cy - top;
                if (d0 < -th || d0 > 4.f * sp + th) continue;
                const float m = fmodf(d0 + 0.5f * sp, sp) - 0.5f * sp;
                cov = fmaxf(cov, clamp01(0.5f * th + 0.5f - fabsf(m)) * 0.85f);
            }
        }
        cov *= Q[2];
        c.x += (inkc.x - c.x) * cov; c.y += (inkc.y - c.y) * cov; c.z += (inkc.z - c.z) * cov;
        if (Q[5] > 0.f && cx >= Q[3] && cx < Q[4]) {                   // the newest bar's ink, glowing a while
            const float g = cov * Q[5] * (0.55f + 0.5f * e);
            if (!night) { c.x += (fresh_c.x * 0.8f - c.x) * g * 0.6f; c.y += (fresh_c.y * 0.8f - c.y) * g * 0.6f;
                          c.z += (fresh_c.z * 0.8f - c.z) * g * 0.6f; }
            else { c.x += fresh_c.x * g; c.y += fresh_c.y * g; c.z += fresh_c.z * g; }
        }
    }
    // the notes being played now
    if (nl > 0 && X > P[22] && X < P[24] && Y > P[23] && Y < P[25]) {
        const float cs = cosf(-0.35f), sn = sinf(-0.35f);
        const float halo = P[19] * (0.8f + 0.6f * e) * (1.f + 0.5f * kick);
        for (int i = 0; i < nl; ++i) {
            const float* N = L + 6 * i;
            const float dx = X - N[0], dy = Y - N[1];
            const float ex = (dx * cs + dy * sn) / N[2], ey = (-dx * sn + dy * cs) / N[3];
            const float d = sqrtf(ex * ex + ey * ey);
            const float core = 1.f - smooth(0.82f, 1.05f, d);
            const float glow = expf(-fmaxf(d - 1.f, 0.f) * (2.2f / fmaxf(halo, 0.05f))) * 0.7f * halo;
            const float kk = N[4] * fminf(1.f, core + glow);
            const float3 nc = pal3(N[5] * P[21] + P[20], P);
            if (!night) { c.x += (nc.x * 0.85f - c.x) * kk; c.y += (nc.y * 0.85f - c.y) * kk; c.z += (nc.z * 0.85f - c.z) * kk; }
            else { c.x += nc.x * kk; c.y += nc.y * kk; c.z += nc.z * kk; }
        }
    }
    const float g = 255.f * P[18];
    out[y * W + x] = pack(make_float4(clamp01(c.x) * g, clamp01(c.y) * g, clamp01(c.z) * g, 255.f));
}
"""


class _Line:
    _ids = 0

    def __init__(self, slot: int, fifths: int, meter: bool, sp: float) -> None:
        _Line._ids += 1
        self.id = _Line._ids
        self.slot = slot
        self.fifths = fifths
        self.meter = meter
        self.bars = 0                               # bars assigned here (requested)
        self.cursor = 0.0                           # canvas x where the next bar goes
        self.header = False
        self.pending: list = []                     # bar renders waiting for the header
        self.widths: list[float] = []
        self.full = False
        self.t1, self.t2 = TOP_SP * sp, (TOP_SP + 10) * sp     # staff tops (refined by the header's geometry)
        self.fresh = (0.0, 0.0, 0.0)
        self.y = None
        self.alpha = 0.0
        self.y_target = 0.0
        self.alpha_target = 1.0


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
            "staff_size": num(0.6, 1.5, 1.0, "Staff size", tier="structural"),
            "note_glow": num(0.0, 1.5, 0.8, "Note glow", basic=True),
            "ink_glow": num(0.0, 3.0, 1.2, "Fresh ink glow", unit="s"),
        }

    # -- setup ----------------------------------------------------------------------------------------------------
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        mod = raw_module(_SRC)
        self.k = mod.get_function("score")
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        self.sp = max(4.0, round(0.013 * self.h * float(params["staff_size"]), 1))     # staff space (px)
        self.cw = max(64, int(self.w * 0.94))                                          # a line's canvas
        self.ch = int(ROW_SP * self.sp)
        self.canvas = cp.zeros(NROW * self.ch * self.cw, cp.uint8)
        self.page = cp.zeros((self.h, self.w), cp.uint8)
        launch_2d(mod.get_function("bake_page"), self.w, self.h,
                  (self.page, np.int32(self.w), np.int32(self.h), np.int32(LOOKS.index(params["look"])),
                   np.uint32(self.seed)))
        # engraved parts come through pinned memory to a GPU staging area, then into their line's canvas
        self.stage_bytes = RESULTS_PER_FRAME * self.ch * self.cw
        self._pinned = cp.cuda.alloc_pinned_memory(self.stage_bytes)
        self.stage = np.frombuffer(self._pinned, np.uint8, self.stage_bytes)
        self.gstage = cp.zeros(self.stage_bytes, cp.uint8)
        self.P = cp.zeros(32, cp.float32)
        self.Ph = np.zeros(32, np.float32)
        self.R = cp.zeros(NROW * 10, cp.float32)
        self.Rh = np.zeros(NROW * 10, np.float32)
        self.RI = cp.zeros(NROW, cp.int32)
        self.RIh = np.zeros(NROW, np.int32)
        self.L = cp.zeros(MAX_LIVE * 6, cp.float32)
        self.Lh = np.zeros(MAX_LIVE * 6, np.float32)
        from stream_service.score.quantize import ScoreBuilder
        self.builder = ScoreBuilder()
        self.clock_bar = 0
        self.lines: list[_Line] = []
        self.free = list(range(NROW))
        self.engraver = None
        try:
            import verovio  # noqa: F401  (the worker imports them; this only checks they're installed)
            import resvg_py  # noqa: F401
            from stream_service.score.client import Engraver
            self.engraver = Engraver(self.cw, space_px=self.sp)
        except Exception as e:
            log.warning("live_score: no engraving (%s); drawing the staves instead", e)
        self.t_sound = 0.0
        self.clock = 0.0
        self.breath = 0.0
        self.drift = 0.0
        self.kick = 0.0
        self.bbox = (0.0, 0.0, 0.0, 0.0)
        self._new_line(meter=True)

    # -- lines ---------------------------------------------------------------------------------------------------
    def _new_line(self, meter: bool) -> _Line:
        if self.lines:
            self.lines[-1].full = True
        if not self.free:
            old = self.lines.pop(0)
            self.free.append(old.slot)
        _tonic, _minor, fifths = self.builder.key()
        ln = _Line(self.free.pop(0), fifths, meter, self.sp)
        off = ln.slot * self.ch * self.cw
        self.canvas[off:off + self.ch * self.cw].fill(0)
        self.lines.append(ln)
        if self.engraver is not None:
            from stream_service.score import mei
            self.engraver.submit(f"h:{ln.id}", mei.header_doc(fifths, meter=meter), margin_left=int(3 * self.sp))
        else:
            ln.header = True
        return ln

    def _request_bar(self, ln: _Line, bar) -> None:
        ln.bars += 1
        if self.engraver is None:
            return
        from stream_service.score import mei
        self.engraver.submit(f"b:{ln.id}:{bar.index}", mei.bar_doc(bar, ln.fifths))

    def _layout(self) -> None:
        """Targets: the line being written sits above the bottom margin, the one before right above it, older ones
        slide off the top."""
        y = 0.97 * self.h
        n = len(self.lines)
        for i in range(n - 1, -1, -1):
            ln = self.lines[i]
            back = n - 1 - i
            y -= self.ch
            ln.y_target = y
            ln.alpha_target = 1.0 if back == 0 else 0.92 if back == 1 and y > -0.2 * self.ch else 0.0
            y -= 0.01 * self.h
            if ln.y is None:
                ln.y = ln.y_target + 0.06 * self.h

    # -- engraved ink into the canvases --------------------------------------------------------------------------------
    def _take_renders(self) -> None:
        if self.engraver is None:
            return
        stage_off = 0
        for key, meta, ink in self.engraver.results(RESULTS_PER_FRAME):
            if not key or not meta.get("ok") or not meta.get("measures"):
                continue
            parts = key.split(":")
            ln = next((x for x in self.lines if x.id == int(parts[1])), None)
            if ln is None:
                continue
            w, h = int(meta["w"]), int(meta["h"])
            if w * h > self.ch * self.cw or len(ink) < w * h:
                continue
            self.stage[stage_off:stage_off + w * h] = np.frombuffer(ink, np.uint8, w * h)
            g = self.gstage[stage_off:stage_off + w * h]
            g.set(self.stage[stage_off:stage_off + w * h], stream=self.ctx.cp.cuda.get_current_stream())
            render = (g.reshape(h, w), meta["measures"][0])
            stage_off += w * h
            if parts[0] == "h":
                self._place(ln, render, 0.0, render[1]["x1"] + 1, header=True)
                ln.header = True
                pending, ln.pending = ln.pending, []
                for r in pending:
                    self._place_bar(ln, r)
            elif ln.header:
                self._place_bar(ln, render)
            else:
                ln.pending.append(render)

    def _place_bar(self, ln: _Line, render) -> None:
        m = render[1]
        crop = m["x0"] + 0.35 * self.sp                               # the engraver's system-start line
        width = m["x1"] + 1 - crop
        if ln.cursor + width > self.cw - 2 and ln.widths:
            nxt = self._next_line_after(ln)                           # doesn't fit: it opens the next line
            if nxt.header:
                self._place_bar(nxt, render)
            else:
                nxt.pending.append(render)
            return
        self._place(ln, render, crop, m["x1"] + 1)
        ln.widths.append(width)
        ln.fresh = (ln.cursor - width, ln.cursor, 1.0)
        typical = sorted(ln.widths)[len(ln.widths) // 2]
        if self.lines[-1] is ln and self.cw - ln.cursor < 0.9 * typical:
            self._new_line(meter=False)                               # no room for another bar: open the next line

    def _next_line_after(self, ln: _Line) -> _Line:
        i = self.lines.index(ln)
        if i + 1 < len(self.lines):
            return self.lines[i + 1]
        return self._new_line(meter=False)

    def _place(self, ln: _Line, render, x0: float, x1: float, header: bool = False) -> None:
        """Composite columns [x0, x1) of an engraved part at the line's cursor, its treble top line on ln.t1."""
        img, m = render
        h, w = img.shape
        top = m["staves"][0][0]
        dy = int(round(ln.t1 - top))
        c0, c1 = int(x0), min(int(math.ceil(x1)), w)
        dst_x = int(round(ln.cursor))
        cols = min(c1 - c0, self.cw - dst_x)
        r0, r1 = max(0, dy), min(self.ch, dy + h)
        if cols <= 0 or r1 <= r0:
            return
        off = ln.slot * self.ch * self.cw
        cv = self.canvas[off:off + self.ch * self.cw].reshape(self.ch, self.cw)
        dst = cv[r0:r1, dst_x:dst_x + cols]
        self.ctx.cp.maximum(dst, img[r0 - dy:r1 - dy, c0:c0 + cols], out=dst)
        ln.cursor = dst_x + cols
        if header and len(m["staves"]) > 1:
            ln.t2 = ln.t1 + (m["staves"][1][0] - top)

    # -- the music ---------------------------------------------------------------------------------------------
    def _listen(self):
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
            cur = self.lines[-1]
            if bar.marker:                                    # the phrase ended: the next one starts a line
                self.clock_bar = 0
                if cur.bars:
                    self._new_line(meter=True)
                continue
            self.clock_bar += 1
            self._request_bar(cur, bar)
        return f

    def _live_notes(self, f) -> int:
        """Glowing noteheads for notes not yet in a finished bar, at their pitch where the bar will be written."""
        from stream_service.score.mei import signature, spell
        b = self.builder
        if f is None or not b.notes:
            return 0
        ln = self.lines[-1]
        sig = signature(ln.fifths)
        widths = ln.widths or next((x.widths for x in reversed(self.lines) if x.widths), [])
        typical = sorted(widths)[len(widths) // 2] if widths else 0.22 * self.cw
        start = ln.cursor if ln.header else 8 * self.sp
        x_off = 0.5 * (self.w - self.cw)
        committed_beat = self.clock_bar * 4
        clk = b.clock
        lv = f.notes[:, 0]
        sp = self.sp
        t0 = b.notes[0].t_on
        k = 0
        bx0 = by0 = 1e9
        bx1 = by1 = -1e9
        for n in b.notes[-MAX_LIVE:]:
            if clk.running:
                beat = clk.beat(n.t_on)
                if beat < committed_beat - 0.01:
                    continue
                pos = (beat - committed_beat) / 4.0            # bars ahead of the last written one
            else:
                pos = min(0.95, (n.t_on - t0) / 2.4)
            x = min(self.cw - 2 * sp, start + typical * (0.08 + 0.84 * pos))
            staff = 0 if n.pitch >= 60 else 1
            letter, _alt, octave = spell(n.pitch, ln.fifths, sig)
            d = "cdefgab".index(letter) + 7 * octave
            bottom = (ln.t1 if staff == 0 else ln.t2) + 4 * sp
            y = bottom - (d - (30 if staff == 0 else 18)) * sp * 0.5
            level = float(lv[n.pitch]) if 0 <= n.pitch < 128 else 0.0
            inten = max(0.22, min(1.0, 1.4 * level)) * min(1.0, (self.t_sound - n.t_on) / 0.12 + 0.2)
            X, Y = x_off + x, ln.y + y
            self.Lh[6 * k:6 * k + 6] = (X, Y, 0.62 * sp, 0.48 * sp, inten, ((n.pitch * 7) % 12) / 12.0)
            r = 3.0 * sp
            bx0, by0, bx1, by1 = min(bx0, X - r), min(by0, Y - r), max(bx1, X + r), max(by1, Y + r)
            k += 1
            if k >= MAX_LIVE:
                break
        self.bbox = (bx0, by0, bx1, by1) if k else (0.0, 0.0, 0.0, 0.0)
        return k

    # -- render -------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        self.clock += dt
        self.breath += 0.25 * spd * dt
        self.drift += float(p["drift_speed"]) * spd * dt
        a = 1.0 - math.exp(-dt / 0.15) if dt > 0 else 1.0
        self.kick += (pulse - self.kick) * a
        f = self._listen()
        self._take_renders()
        self._layout()
        ease = 1.0 - math.exp(-dt / 0.45) if dt > 0 else 1.0
        glow_s = max(0.05, float(p["ink_glow"]))
        Rh, RIh = self.Rh, self.RIh
        Rh[:] = 0.0
        RIh[:] = 0
        x_off = 0.5 * (self.w - self.cw)
        last = self.lines[-1]
        for i, ln in enumerate(self.lines[-NROW:]):
            ln.y += (ln.y_target - ln.y) * ease
            ln.alpha += (ln.alpha_target - ln.alpha) * ease
            fx0, fx1, fr = ln.fresh
            ln.fresh = (fx0, fx1, max(0.0, fr - (dt / glow_s if dt > 0 else 0.0)))
            open_from = (ln.cursor if ln.header else 0.0) if ln is last else -1.0
            Rh[10 * i:10 * i + 10] = (x_off, ln.y, ln.alpha, fx0, fx1, ln.fresh[2], open_from, ln.t1, ln.t2, 0.0)
            RIh[i] = ln.slot * self.ch * self.cw
        nl = self._live_notes(f)
        Ph = self.Ph
        Ph[0:12] = palette_coeffs(p["palette"]) if p.get("palette") else palette_coeffs("ember")
        Ph[12], Ph[13] = self.w, self.h
        Ph[14] = LOOKS.index(p["look"])
        Ph[15] = self.breath % (2.0 * math.pi)
        Ph[16] = e
        Ph[17] = self.kick
        Ph[18] = float(p["intensity"])
        Ph[19] = float(p["note_glow"])
        Ph[20] = self.drift % 10.0
        Ph[21] = float(p["palette_period"])
        Ph[22:26] = self.bbox
        Ph[26], Ph[27] = self.cw, self.ch
        Ph[28] = self.sp
        Ph[29] = max(1.0, 0.08 * self.sp)
        self.P.set(Ph)
        self.R.set(Rh)
        self.RI.set(RIh)
        if nl:
            self.L.set(self.Lh)
        launch_2d(self.k, self.w, self.h, (self.buf, self.P, self.canvas, self.R, self.RI, self.L, np.int32(nl),
                                           self.page))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        if self.engraver is not None:
            self.engraver.close()
            self.engraver = None
        self.buf = self.canvas = self.P = self.R = self.RI = self.L = self.page = self.gstage = None
        self.stage = self._pinned = None
