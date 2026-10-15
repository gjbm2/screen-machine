"""Piano waterfall: the notes being played rise as glowing bars from a keyboard, with sparks where keys are struck.

Source: Original (inspired by https://github.com/kosua20/MIDIVisualizer, whose falling-notes-and-particles look
        the owner picked; nothing copied: MIDIVisualizer plays a known file and drops notes onto the keys, a live
        piano can't be seen ahead, so here the notes are born at the keyboard and rise)
Author: smfx
License: MIT (MIDIVisualizer, the inspiration: "MIT License Copyright (c) 2020 Simon Rodriguez")
license_class: permissive

Listens to the piano (``listens = ("notes",)``, CONVENTIONS §2b).  Each strike starts a bar at the keyboard's top
edge; while the key is held (or the pedal holds it, bars follow the key, not the sound) its bottom stays on the
keyboard, and after key-up the whole bar floats up at ``rise`` screen heights per second.  A bar's height is
therefore the note's duration.  Ages come from the sound view at photon time, so a bar leaves the keyboard as
the ear hears the key go up.  Host side, a ring of the last K strikes per pitch (absolute times on the sound
clock) is rebuilt into relative ages each frame and uploaded (128 x K x 3 floats); every pixel tests the bars of
the seven nearest keys (three white, four black) so a halo never stops at a key boundary.

Look: bars are rounded, with a bright tip at the keyboard while held and an additive halo whose strength follows
the note's sounding level (pedal-held notes keep glowing, damped ones dim).  The keyboard lights struck keys in the
note's colour; above it a plume of rising smoke and twinkling sparks is emitted by every sounding key (stronger
right after a strike, then following the level).  colours: hands (blue below middle C, amber above, as in the
reference), pitch (the palette around the circle of fifths: harmony becomes colour) or register (the palette
from A0 to C8).

Macros: energy -> haze, spark density, plume height and halo size; pulse (bind note_on) -> halo and spark kick;
speed -> the haze and spark drift (never the bars: rise is a regional knob, eased); intensity -> output gain;
palette / period / drift colour the pitch and register modes and tint the haze.  Alive without notes: the haze
drifts up, a sparse dust twinkles and the keyboard's edge line breathes.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import palette_coeffs
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

K = 6                                  # strikes remembered per pitch
LOW, HIGH = 21, 108                    # A0 .. C8
N_WHITE = 52
COLOURS = ("hands", "pitch", "register")
_BLACK_PC = {1, 3, 6, 8, 10}


def _keyboard() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per pitch (x centre 0-1, half width 0-1, black, white index); per white key its pitch and the black key
    to its right (-1 if none)."""
    info = np.zeros((128, 4), np.float32)
    info[:, 3] = -1
    white_pitch = np.full(N_WHITE, -1, np.int32)
    black_right = np.full(N_WHITE, -1, np.int32)
    ww = 1.0 / N_WHITE
    wi = -1
    for p in range(LOW, HIGH + 1):
        if p % 12 in _BLACK_PC:
            info[p] = (ww * (wi + 1), 0.30 * ww, 1.0, -1)
            black_right[wi] = p
        else:
            wi += 1
            info[p] = (ww * (wi + 0.5), 0.5 * ww, 0.0, wi)
            white_pitch[wi] = p
    return info, white_pitch, black_right


_SRC = HELPERS + r"""
#define K """ + str(K) + r"""
#define NW """ + str(N_WHITE) + r"""

__device__ __forceinline__ float3 pal3(float t, const float* c) {
    return make_float3(c[0] + c[3] * cosf(6.2831853f * (c[6] * t + c[9])),
                       c[1] + c[4] * cosf(6.2831853f * (c[7] * t + c[10])),
                       c[2] + c[5] * cosf(6.2831853f * (c[8] * t + c[11])));
}
__device__ float3 note_colour(int p, const float* P) {
    const int mode = (int)P[20];
    if (mode == 0) {                                   // hands: blue below middle C, amber from it
        return p < 60 ? make_float3(0.30f, 0.72f, 1.00f) : make_float3(1.00f, 0.74f, 0.26f);
    }
    float t;
    if (mode == 1) t = (float)((p * 7) % 12) / 12.f;   // circle of fifths
    else t = (float)(p - 21) / 87.f;                  // register
    const float3 c = pal3(t * P[21] + P[22], P);
    return make_float3(clamp01(c.x), clamp01(c.y), clamp01(c.z));
}
__device__ __forceinline__ float sd_box(float px, float py, float hx, float hy, float r) {
    const float qx = fabsf(px) - hx + r, qy = fabsf(py) - hy + r;
    const float ox = fmaxf(qx, 0.f), oy = fmaxf(qy, 0.f);
    return sqrtf(ox * ox + oy * oy) + fminf(fmaxf(qx, qy), 0.f) - r;
}

// P: [0..11] palette  [12]W [13]H [14]kh (keyboard height, screen fraction)  [15]rise (screen heights/s)
//    [16]bar width factor  [17]glow px  [18]glow gain  [19]sparks  [20]colour mode  [21]period  [22]drift
//    [23]time (drift clock, s)  [24]energy  [25]kick  [26]gain  [27]haze phase  [28]spark phase  [29]edge breath
extern "C" __global__
void waterfall(uchar4* __restrict__ out, const float* __restrict__ P, const float* __restrict__ seg,
               const float* __restrict__ notes, const float* __restrict__ info,
               const int* __restrict__ wpitch, const int* __restrict__ bright, const unsigned int seed)
{
    const int W = (int)P[12], H = (int)P[13];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float u = (x + 0.5f) / W;
    const float yb = 1.f - (y + 0.5f) / H;               // 0 at the bottom
    const float kh = P[14], rise = fmaxf(P[15], 1e-3f), e = P[24], kick = P[25];
    const float wwpx = (float)W / NW;
    const float h = yb - kh;                             // screen heights above the keyboard's top edge
    float3 acc = make_float3(0.f, 0.f, 0.f);

    // background: a faint palette-tinted haze drifting up, more with energy
    {
        const float3 tint = pal3(P[22], P);
        const float hz = fbm(u * 3.1f, yb * 1.7f - P[27], seed + 7u);
        const float k = (0.025f + 0.06f * e) * hz * hz * (0.4f + 0.6f * clamp01(yb));
        acc.x += k * clamp01(tint.x); acc.y += k * clamp01(tint.y); acc.z += k * clamp01(tint.z);
    }

    const int wi = min(NW - 1, max(0, (int)floorf(u * NW)));
    int cand[7];
    cand[0] = wpitch[wi];
    cand[1] = wi > 0 ? wpitch[wi - 1] : -1;
    cand[2] = wi < NW - 1 ? wpitch[wi + 1] : -1;
    cand[3] = bright[wi];
    cand[4] = wi > 0 ? bright[wi - 1] : -1;
    cand[5] = wi > 1 ? bright[wi - 2] : -1;
    cand[6] = wi < NW - 1 ? bright[wi + 1] : -1;

    if (h >= 0.f) {
        // bars: every remembered strike of the nearby keys
        const float gpx = P[17] * (0.8f + 0.5f * e) * (1.f + 0.6f * kick);
        for (int c = 0; c < 7; ++c) {
            const int p = cand[c];
            if (p < 0) continue;
            const float* I = info + 4 * p;
            const float hw = I[1] * P[16] * (I[2] > 0.5f ? 0.92f : 0.84f);
            const float3 col = note_colour(p, P);
            const float lvl = notes[4 * p];
            for (int k = 0; k < K; ++k) {
                const float* S = seg + (p * K + k) * 3;
                const float aon = S[0];
                if (!(aon >= 0.f)) continue;                     // empty slot (NaN / -1)
                const float held = S[1] < 0.f ? 1.f : 0.f;
                const float a0 = held > 0.f ? 0.f : S[1];
                const float top = aon * rise, bot = a0 * rise;   // screen heights above the keyboard
                if (bot > 1.2f) continue;
                const float cy = 0.5f * (top + bot), hy = 0.5f * (top - bot);
                const float dxp = (u - I[0]) * W, dyp = (h - cy) * H;
                const float r = fminf(0.35f * hw * W, 0.5f * hy * H + 0.5f);
                const float d = sd_box(dxp, dyp, hw * W, fmaxf(hy * H, 0.5f), fmaxf(r, 0.5f));
                const float core = 1.f - smooth(-0.75f, 0.75f, d);
                const float vel = S[2];
                const float latest = (k == 0) ? 1.f : 0.f;
                float b = (0.55f + 0.45f * vel) * core;
                if (held > 0.f) {                                // a bright tip at the keyboard while held
                    const float tip = expf(-fmaxf(h - bot, 0.f) * H / (2.5f * wwpx));
                    b *= 1.f + 0.8f * tip;
                }
                const float halo = (d > 0.f ? expf(-d / gpx) : 1.f) * P[18]
                                   * (0.5f + 0.5f * vel) * (0.55f + latest * (0.9f * lvl + 0.4f * kick));
                // a whitish core inside the bar, the note's colour around it (the reference's neon look)
                const float wcore = b * (0.3f + 0.3f * latest * lvl);
                const float k2 = b + 0.8f * halo;
                acc.x += k2 * col.x + wcore; acc.y += k2 * col.y + wcore; acc.z += k2 * col.z + wcore;
            }
        }
        // plume: smoke and sparks rising from every sounding key
        if (P[19] > 0.f) {
            const float ph = 0.18f + 0.22f * e;                  // plume height (screen heights)
            if (h < 2.5f * ph) {
                float em = 0.f;
                float3 pc = make_float3(0.f, 0.f, 0.f);
                for (int c = 0; c < 7; ++c) {
                    const int p = cand[c];
                    if (p < 0) continue;
                    const float* N = notes + 4 * p;
                    const float bl = smooth(0.f, 0.12f, N[2]);       // strikes bloom in (they land late)
                    const float s = fminf(1.f, bl * (1.3f * N[0] + N[1] * expf(-fmaxf(N[2], 0.f) / 0.6f)));
                    if (s <= 1e-3f) continue;
                    const float* I = info + 4 * p;
                    const float sx = I[1] * (1.4f + 7.f * h / ph);
                    const float dx = (u - I[0]) / fmaxf(sx, 1e-4f);
                    const float w = s * expf(-dx * dx) * expf(-h / ph);
                    em += w;
                    const float3 col = note_colour(p, P);
                    pc.x += w * col.x; pc.y += w * col.y; pc.z += w * col.z;
                }
                if (em > 1e-4f) {
                    pc.x /= em; pc.y /= em; pc.z /= em;
                    const float sm = fbm(u * 18.f, h * 9.f - P[28] * 0.9f, seed + 3u);
                    const float smoke = em * sm * sm * 0.9f;
                    // sparks: hashed cells drifting up, twinkling
                    const float cs = 3.f;
                    const float cyc = (y - P[28] * 0.55f * H) / cs;
                    const int ix = (int)floorf(x / cs), iy = (int)floorf(cyc);
                    const float hs = hash1(ix, iy, seed + 11u);
                    const float dens = 0.94f - 0.05f * P[19] - 0.03f * e - 0.03f * kick;
                    float spark = 0.f;
                    if (hs > dens) {
                        const float fx = x / cs - ix - 0.5f, fy = cyc - iy - 0.5f;
                        const float tw = 0.5f + 0.5f * sinf(6.2831853f * (hash1(ix, iy, seed + 12u) + 2.f * P[23]));
                        spark = expf(-(fx * fx + fy * fy) * 8.f) * tw * 2.2f;
                    }
                    const float k2 = P[19] * (smoke + em * spark);
                    acc.x += k2 * pc.x; acc.y += k2 * pc.y; acc.z += k2 * pc.z;
                }
            }
            // impact glow on the keyboard's edge where keys are struck
            for (int c = 0; c < 7; ++c) {
                const int p = cand[c];
                if (p < 0) continue;
                const float* N = notes + 4 * p;
                const float s = smooth(0.f, 0.12f, N[2]) * (N[1] * expf(-fmaxf(N[2], 0.f) / 0.35f) + 0.4f * N[0]);
                if (s <= 1e-3f) continue;
                const float* I = info + 4 * p;
                const float dx = (u - I[0]) * W / (1.2f * wwpx), dy = h * H / (1.6f * wwpx);
                const float g = s * expf(-(dx * dx + dy * dy)) * 0.9f * P[19];
                const float3 col = note_colour(p, P);
                acc.x += g * (0.5f + col.x); acc.y += g * (0.5f + col.y); acc.z += g * (0.5f + col.z);
            }
        }
        // a sparse dust everywhere, so the scene lives without notes
        {
            const float cs = 4.f;
            const float cyc = (y - P[28] * 0.25f * H) / cs;
            const int ix = (int)floorf(x / cs), iy = (int)floorf(cyc);
            if (hash1(ix, iy, seed + 21u) > 0.9975f - 0.0015f * e) {
                const float fx = x / cs - ix - 0.5f, fy = cyc - iy - 0.5f;
                const float tw = 0.5f + 0.5f * sinf(6.2831853f * (hash1(ix, iy, seed + 22u) + 0.5f * P[23]));
                const float k2 = 0.35f * expf(-(fx * fx + fy * fy) * 10.f) * tw;
                const float3 tint = pal3(P[22] + 0.3f, P);
                acc.x += k2 * (0.6f + 0.4f * clamp01(tint.x)); acc.y += k2 * (0.6f + 0.4f * clamp01(tint.y));
                acc.z += k2 * (0.6f + 0.4f * clamp01(tint.z));
            }
        }
        // the keyboard's top edge: a thin warm line that breathes
        if (kh > 0.f) {
            const float d = h * H;
            const float line = expf(-d * d / 2.f) * (0.45f + 0.25f * P[29]) + expf(-d / 6.f) * 0.08f;
            acc.x += line * 0.9f; acc.y += line * 0.12f; acc.z += line * 0.1f;
        }
    } else {
        // the keyboard
        const float ky = yb / kh;                            // 0 bottom .. 1 top of the keys
        const float wx = u * NW - wi;                        // 0..1 across the white key
        const int pw = wpitch[wi];
        float3 c;
        // black key under this pixel?
        int pb = -1;
        if (ky > 0.36f) {
            const int r = bright[wi], l = wi > 0 ? bright[wi - 1] : -1;
            if (r >= 0 && fabsf(u - info[4 * r]) < info[4 * r + 1]) pb = r;
            else if (l >= 0 && fabsf(u - info[4 * l]) < info[4 * l + 1]) pb = l;
        }
        if (pb >= 0) {
            const float* N = notes + 4 * pb;
            const float lit = clamp01(1.6f * N[0] + (N[3] < 0.f ? 0.35f : 0.f));
            const float3 col = note_colour(pb, P);
            const float shade = 0.05f + 0.06f * smooth(0.36f, 1.f, ky);
            c = make_float3(shade + lit * (col.x - shade), shade + lit * (col.y - shade), shade + lit * (col.z - shade));
        } else {
            const float* N = notes + 4 * pw;
            const float lit = clamp01(1.6f * N[0] + (N[3] < 0.f ? 0.35f : 0.f));
            const float3 col = note_colour(pw, P);
            const float gap = smooth(0.f, 1.5f / wwpx, wx) * smooth(1.f, 1.f - 1.5f / wwpx, wx);
            const float face = (0.72f + 0.2f * smooth(0.f, 0.6f, ky)) * gap;
            const float m = 0.8f * lit;
            c = make_float3(face + m * (col.x - face), face + m * (col.y - face), face + m * (col.z - face));
        }
        const float sh = 0.75f + 0.25f * smooth(0.f, 0.08f, ky);     // the keybed's shadow at the bottom
        acc.x += c.x * sh; acc.y += c.y * sh; acc.z += c.z * sh;
    }

    const float g = 255.f * P[26];
    out[y * W + x] = pack(make_float4(g * (1.f - expf(-1.25f * acc.x)), g * (1.f - expf(-1.25f * acc.y)),
                                      g * (1.f - expf(-1.25f * acc.z)), 255.f));
}
"""


class PianoWaterfall(Source):
    name = "piano_waterfall"
    label = "Piano waterfall"
    description = "The notes being played rise from a keyboard as glowing bars, with sparks where keys are struck."
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette="ice", render_scale=0.5, period=1.0, drift=0.02),
            "rise": num(0.08, 0.8, 0.28, "Rise speed", unit="screens/s", basic=True),
            "colours": enum(COLOURS, "hands", "Colours", basic=True),
            "keyboard": num(0.0, 0.25, 0.15, "Keyboard height"),
            "bar_width": num(0.4, 1.0, 0.82, "Bar width"),
            "glow": num(0.0, 1.5, 0.6, "Glow"),
            "sparks": num(0.0, 1.5, 0.6, "Sparks and smoke"),
        }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC).get_function("waterfall")
        info, wp, br = _keyboard()
        self.info = cp.asarray(info)
        self.wpitch = cp.asarray(wp)
        self.bright = cp.asarray(br)
        self.P = cp.zeros(32, cp.float32)
        self.Ph = np.zeros(32, np.float32)
        self.seg = cp.zeros(128 * K * 3, cp.float32)
        self.Sh = np.full((128, K, 3), -1.0, np.float32)
        self.quiet = cp.zeros((128, 4), cp.float32)       # the notes table when nothing is heard
        # strike history on the sound clock (ms): slot 0 is the latest
        self.on_ms = np.full((128, K), np.nan)
        self.off_ms = np.full((128, K), np.nan)
        self.vel = np.zeros((128, K))
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        self.clock = 0.0
        self.haze = 0.0
        self.spark = 0.0
        self.drift = 0.0
        self.kick = 0.0

    # -- the strike history -------------------------------------------------------------------------------------
    def _strike(self, p: int, t_on: float, vel: float) -> None:
        self.on_ms[p, 1:] = self.on_ms[p, :-1]
        self.off_ms[p, 1:] = self.off_ms[p, :-1]
        self.vel[p, 1:] = self.vel[p, :-1]
        self.on_ms[p, 0], self.off_ms[p, 0], self.vel[p, 0] = t_on, np.nan, vel
        if not math.isnan(self.off_ms[p, 1]) or math.isnan(self.on_ms[p, 1]):
            return
        self.off_ms[p, 1] = t_on                      # re-struck while held: the old bar ends here

    def _listen(self) -> tuple[object, float]:
        """Fold this frame's strikes and releases into the history; returns (notes on the GPU, sound time ms)."""
        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        if f is None or getattr(snd, "notes_gpu", None) is None:
            return self.quiet, self.clock * 1000.0
        t = float(f.t_ms)
        for p, vel, age in f.onsets:
            if LOW <= p <= HIGH:
                self._strike(p, t - age * 1000.0, float(vel))
        for p, _vo, age in f.releases:
            if LOW <= p <= HIGH and not math.isnan(self.on_ms[p, 0]) and math.isnan(self.off_ms[p, 0]):
                self.off_ms[p, 0] = max(self.on_ms[p, 0], t - age * 1000.0)
        # heal against the table: a key held that we never saw struck (we came in mid-note), or one that went up
        n = f.notes
        held = n[:, 3] < 0.0
        open_ = np.isnan(self.off_ms[:, 0]) & ~np.isnan(self.on_ms[:, 0])
        for p in np.flatnonzero(held & ~open_):
            if LOW <= p <= HIGH and np.isfinite(n[p, 2]):
                self._strike(int(p), t - float(n[p, 2]) * 1000.0, float(n[p, 1]))
        for p in np.flatnonzero(open_ & ~held):
            a = float(n[p, 3])
            self.off_ms[p, 0] = t - (a * 1000.0 if np.isfinite(a) and a >= 0.0 else 0.0)
        return snd.notes_gpu, t

    # -- render -------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        self.clock += dt
        notes, t = self._listen()
        a = 1.0 - math.exp(-dt / 0.12) if dt > 0 else 1.0     # kicks ease in (they land late anyway)
        self.kick += (pulse - self.kick) * a
        self.haze += (0.05 + 0.08 * e) * spd * dt
        self.spark += (0.35 + 0.25 * e) * spd * dt
        self.drift += float(p["drift_speed"]) * spd * dt

        # relative ages of every remembered strike (NaN slots stay -1: empty)
        S = self.Sh
        with np.errstate(invalid="ignore"):
            S[:, :, 0] = np.where(np.isnan(self.on_ms), -1.0, (t - self.on_ms) / 1000.0)
            S[:, :, 1] = np.where(np.isnan(self.off_ms), -1.0, np.maximum(0.0, (t - self.off_ms) / 1000.0))
        S[:, :, 2] = self.vel
        self.seg.set(S.reshape(-1))

        Ph = self.Ph
        Ph[0:12] = palette_coeffs(p["palette"])
        Ph[12], Ph[13] = self.w, self.h
        Ph[14] = float(p["keyboard"])
        Ph[15] = float(p["rise"])
        Ph[16] = float(p["bar_width"])
        Ph[17] = 0.6 * (self.w / N_WHITE)
        Ph[18] = float(p["glow"])
        Ph[19] = float(p["sparks"])
        Ph[20] = COLOURS.index(p["colours"])
        Ph[21] = float(p["palette_period"])
        Ph[22] = self.drift % 10.0
        Ph[23] = self.clock % 1000.0
        Ph[24] = e
        Ph[25] = self.kick
        Ph[26] = float(p["intensity"])
        Ph[27] = self.haze % 1000.0
        Ph[28] = self.spark % 1000.0
        Ph[29] = 0.5 + 0.5 * math.sin(0.9 * self.clock)
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, self.P, self.seg, notes, self.info, self.wpitch, self.bright,
                                           np.uint32(self.seed)))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = self.P = self.seg = self.info = self.wpitch = self.bright = self.quiet = None
